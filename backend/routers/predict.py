"""Prediction endpoints (Worker A): /predict_risk, /predict_risk/batch,
/intersections, /violation_trends.

Honest-labelling rules enforced here:
- Hourly values are ESTIMATES from proxies, never observed hourly
  violations (the daily data has no hour-of-day). Every response that
  carries hourly values says so (`estimate: true` + a note).
- /violation_trends is DAILY granularity only; it makes no hourly claims.
- No live adjustment is applied on the default path (experimental,
  default off, not part of this API).
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, date as date_cls
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import config
from ..model_service import service

PROJECT_ROOT = config.PROJECT_ROOT
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import hourly_risk  # noqa: E402  (already built; do not re-implement)

router = APIRouter(tags=["predictions"])

HOURLY_ESTIMATE_NOTE = (
    "Hourly values are ESTIMATES: the daily violation data has no "
    "hour-of-day, so the hourly shape is built from crash/congestion "
    "proxies and renormalised to the daily total. They are not observed "
    "hourly violations."
)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PredictRequest(BaseModel):
    camera_id: str | None = Field(
        default=None, description="Red-light camera ID, e.g. '1413'.")
    latitude: float | None = Field(
        default=None, ge=-90, le=90,
        description="Latitude for nearest-camera lookup (used when no camera_id).")
    longitude: float | None = Field(
        default=None, ge=-180, le=180,
        description="Longitude for nearest-camera lookup (used when no camera_id).")
    timestamp: datetime | None = Field(
        default=None,
        description="ISO-8601 timestamp; the date part selects the camera-day. "
                    "Defaults to now (UTC). Dates after the data window fall back "
                    "to the camera's pre-test mean (see daily_source).")
    weather_override: dict | None = Field(
        default=None,
        description="Accepted and stored in the response echo, but IGNORED by "
                    "the validated prediction path: the shipped model does not "
                    "take live weather as an input. No live adjustment is applied.")
    congestion_override: dict | None = Field(
        default=None,
        description="Accepted and echoed, but IGNORED by the validated prediction "
                    "path (same reason as weather_override).")
    method: str | None = Field(
        default=None,
        description="Hourly shaping method passed to hourly_risk.predict_hourly: "
                    "'auto' (default), 'profile' or 'model' (optionally +demand / "
                    "+slowness). Only shapes the hourly split; the daily total "
                    "is unchanged.")
    demand: bool = Field(
        default=False,
        description="Opt-in trip-demand shaping of the hourly split (default off; "
                    "demand lost on every validation variant).")
    slowness: bool = Field(
        default=True,
        description="Trip-slowness shaping of the hourly split on the profile "
                    "path (default on; ignored on the model path).")
    banding: str = Field(
        default="hourly",
        description="'hourly' (default: LOW <0.1, MEDIUM 0.1-<0.5, HIGH >=0.5/hr) "
                    "or 'daily' (daily thresholds applied per hour, comparison only).")


class HourlyEntry(BaseModel):
    hour: int
    predicted_violations: float
    band: str
    risk_score: float
    estimate: bool = True


class PredictResponse(BaseModel):
    camera_id: str
    intersection: str | None
    latitude: float | None
    longitude: float | None
    date: str
    timestamp: str | None
    daily_prediction: float
    daily_source: str
    risk_score: float
    risk_level: str
    confidence: float
    reasons: list[str]
    hourly: list[HourlyEntry]
    hourly_estimate_note: str = HOURLY_ESTIMATE_NOTE
    hourly_method: str
    hourly_sum: float
    reconciliation_error: float
    model_version: int | None
    nearest_distance_m: float | None = None
    weather_override_applied: bool = False
    congestion_override_applied: bool = False


class BatchRequest(BaseModel):
    items: list[PredictRequest] = Field(..., min_length=1, max_length=100)


class IntersectionOut(BaseModel):
    camera_id: str
    intersection: str
    latitude: float | None
    longitude: float | None
    typical_weekday_pred: float | None
    typical_weekend_pred: float | None
    typical_weekday_band: str | None
    typical_weekend_band: str | None
    typical_weekday_score: float | None
    typical_weekend_score: float | None
    actual_test_mean: float | None
    cam_mean_pre_test: float | None


# ---------------------------------------------------------------------------
# Cached lookups
# ---------------------------------------------------------------------------
_profiles: pd.DataFrame | None = None
_segment_map: pd.DataFrame | None = None


def _profiles_df() -> pd.DataFrame:
    global _profiles
    if _profiles is None:
        df = pd.read_csv(config.PROFILES_PATH, dtype={"camera_id": str})
        _profiles = df
    return _profiles


def _segment_map_df() -> pd.DataFrame:
    global _segment_map
    if _segment_map is None:
        df = pd.read_csv(config.SEGMENT_MAP_PATH, dtype={"camera_id": str})
        _segment_map = df.dropna(subset=["latitude", "longitude"])
    return _segment_map


def _profile_for(cam: str) -> dict | None:
    df = _profiles_df()
    row = df[df["camera_id"] == str(cam)]
    if len(row) == 0:
        return None
    r = row.iloc[0]
    return {
        "intersection": (None if pd.isna(r["intersection"]) else str(r["intersection"])),
        "latitude": (None if pd.isna(r["latitude"]) else float(r["latitude"])),
        "longitude": (None if pd.isna(r["longitude"]) else float(r["longitude"])),
    }


def _haversine_m(lat1, lon1, lat2, lon2) -> np.ndarray:
    r = 6_371_000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = np.radians(lat2 - lat1)
    dl = np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def nearest_camera(lat: float, lon: float) -> tuple[str, float]:
    """(camera_id, distance_m) of the nearest camera in the segment map."""
    df = _segment_map_df()
    d = _haversine_m(lat, lon, df["latitude"].to_numpy(float),
                     df["longitude"].to_numpy(float))
    i = int(np.argmin(d))
    return str(df.iloc[i]["camera_id"]), float(d[i])


def daily_band(pred: float) -> str:
    if pred < config.DAILY_BAND_LOW_MAX:
        return "LOW"
    if pred < config.DAILY_BAND_HIGH_MIN:
        return "MEDIUM"
    return "HIGH"


def risk_score_daily(pred: float) -> float:
    return float(min(max(pred / config.DAILY_SCORE_SCALE, 0.0), 1.0))


def _confidence(source: str, nearest_distance_m: float | None) -> float:
    c = {
        "model": config.CONFIDENCE_MODEL,
        "pre_test_mean": config.CONFIDENCE_PRE_TEST_MEAN,
        "citywide_mean": config.CONFIDENCE_CITYWIDE_MEAN,
    }.get(source, 0.5)
    if nearest_distance_m is not None:
        # Nearest-camera lookup adds location uncertainty; scale down
        # linearly to -0.1 at the max distance. Heuristic, not calibrated.
        c -= 0.1 * min(nearest_distance_m / config.MAX_NEAREST_M, 1.0)
    return round(float(c), 2)


# ---------------------------------------------------------------------------
# Core prediction (shared by single + batch)
# ---------------------------------------------------------------------------

def _predict_one(req: PredictRequest) -> PredictResponse:
    if req.camera_id is None and (req.latitude is None or req.longitude is None):
        raise HTTPException(status_code=400, detail={
            "error": "bad_request",
            "message": "Provide camera_id, or both latitude and longitude."})
    if req.camera_id is not None and (
            req.latitude is not None or req.longitude is not None):
        # camera_id wins; lat/lon are ignored (documented, not an error).
        pass

    nearest_distance_m: float | None = None
    cam = req.camera_id
    if cam is None:
        cam, nearest_distance_m = nearest_camera(req.latitude, req.longitude)
        if nearest_distance_m > config.MAX_NEAREST_M:
            raise HTTPException(status_code=404, detail={
                "error": "no_camera_nearby",
                "message": (
                    f"No camera within {config.MAX_NEAREST_M:.0f} m of "
                    f"({req.latitude}, {req.longitude}); nearest is "
                    f"{nearest_distance_m:.0f} m away (camera {cam}).")})
    cam = str(cam)

    ts = req.timestamp or datetime.utcnow()
    day = pd.Timestamp(ts.date())

    # Daily total from the served ONNX model (via the live pointer).
    daily, source = service.daily_prediction(cam, day)

    # Hourly split from the shipped hourly API (ONNX daily anchor —
    # hourly_risk delegates to this same ONNX service, so the anchor
    # and the daily total below are the same number).
    try:
        h = hourly_risk.predict_hourly(
            cam, day, method=req.method or "auto",
            demand=req.demand, slowness=req.slowness, banding=req.banding)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={
            "error": "bad_request", "message": str(e)})

    # Reconcile the hourly split to the ONNX daily total. The ONNX and
    # joblib models agree to <= 2e-05 (verified in backend/verify_onnx.py;
    # REPORT.md's 2.4e-06 was measured in the original export environment
    # and is not reproduced exactly here — float32 rounding noise), so
    # this rescale moves hourly values by a negligible amount; it keeps
    # hourly_sum == daily exactly.
    hourly = np.array(h["hourly"], dtype=float)
    anchor = float(h["daily_prediction"])
    if anchor > 0 and daily > 0:
        hourly = hourly * (daily / anchor)
        resid = daily - float(hourly.sum())
        if resid != 0:
            hourly[int(np.argmax(hourly))] += resid
    elif daily == 0:
        hourly = np.zeros(24)
    hourly = np.clip(hourly, 0, None)

    bands = [hourly_risk.risk_band(float(v), req.banding) for v in hourly]
    hourly_entries = [
        HourlyEntry(hour=i, predicted_violations=round(float(v), 4),
                    band=bands[i],
                    risk_score=round(float(v) / 35.0, 4), estimate=True)
        for i, v in enumerate(hourly)
    ]

    prof = _profile_for(cam)
    return PredictResponse(
        camera_id=cam,
        intersection=prof["intersection"] if prof else None,
        latitude=prof["latitude"] if prof else None,
        longitude=prof["longitude"] if prof else None,
        date=day.strftime("%Y-%m-%d"),
        timestamp=ts.isoformat(),
        daily_prediction=round(float(daily), 4),
        daily_source=source,
        risk_score=round(risk_score_daily(daily), 4),
        risk_level=daily_band(daily),
        confidence=_confidence(source, nearest_distance_m),
        reasons=service.reasons_for(cam, day, daily, source),
        hourly=hourly_entries,
        hourly_method=h["method"],
        hourly_sum=round(float(hourly.sum()), 4),
        reconciliation_error=round(float(hourly.sum() - daily), 6),
        model_version=service.live_version,
        nearest_distance_m=(round(nearest_distance_m, 1)
                            if nearest_distance_m is not None else None),
        weather_override_applied=False,
        congestion_override_applied=False,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/predict_risk", response_model=PredictResponse,
             summary="Daily + hourly risk for one camera or location")
def predict_risk(req: PredictRequest):
    """Daily prediction (ONNX model named by live_model.json) plus the 24
    hourly values as ESTIMATES. Give `camera_id`, or `latitude` +
    `longitude` for nearest-camera lookup (404 beyond 2000 m by default).
    `timestamp` selects the camera-day (date part only). Weather /
    congestion overrides are accepted but ignored by the validated path.
    """
    return _predict_one(req)


@router.post("/predict_risk/batch",
             summary="Batch daily + hourly risk (per-item errors don't fail the batch)")
def predict_risk_batch(req: BatchRequest):
    """Same as /predict_risk for a list (max 100 items). Each result is
    either a full prediction object or {"camera_id"/"index", "error",
    "message"} — one bad item never fails the whole batch."""
    out = []
    for i, item in enumerate(req.items):
        try:
            out.append(_predict_one(item).model_dump())
        except HTTPException as e:
            detail = e.detail if isinstance(e.detail, dict) else {
                "error": "error", "message": str(e.detail)}
            out.append({"index": i,
                        "camera_id": item.camera_id,
                        "error": detail.get("error", "error"),
                        "message": detail.get("message", str(e.detail))})
        except Exception as e:  # never fail the batch on one item
            out.append({"index": i, "camera_id": item.camera_id,
                        "error": "internal_error", "message": str(e)})
    return {"results": out, "count": len(out),
            "errors": sum(1 for r in out if "error" in r)}


@router.get("/intersections", response_model=list[IntersectionOut],
            summary="Camera intersections with typical-day risk profiles")
def list_intersections(
    q: str | None = Query(
        default=None, description="Case-insensitive substring match on the "
        "intersection name, e.g. 'BELMONT'."),
    min_typical_weekday_pred: float | None = Query(
        default=None, description="Only cameras whose typical weekday "
        "prediction is >= this value."),
    min_typical_weekend_pred: float | None = Query(
        default=None, description="Only cameras whose typical weekend "
        "prediction is >= this value."),
    band: str | None = Query(
        default=None, description="Typical weekday band filter: LOW, MEDIUM or HIGH."),
    limit: int = Query(default=300, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """All 300 profiled cameras from data/processed/camera_profiles.csv,
    with their typical weekday/weekend predictions, bands and scores.
    Typical values are medians of the shipped model's holdout predictions.
    """
    df = _profiles_df()
    if q:
        df = df[df["intersection"].str.contains(q, case=False, na=False)]
    if min_typical_weekday_pred is not None:
        df = df[df["typical_weekday_pred"] >= min_typical_weekday_pred]
    if min_typical_weekend_pred is not None:
        df = df[df["typical_weekend_pred"] >= min_typical_weekend_pred]
    if band:
        b = band.upper()
        if b not in ("LOW", "MEDIUM", "HIGH"):
            raise HTTPException(status_code=400, detail={
                "error": "bad_request",
                "message": "band must be LOW, MEDIUM or HIGH."})
        df = df[df["typical_weekday_band"] == b]
    df = df.iloc[offset:offset + limit]
    out = []
    for _, r in df.iterrows():
        out.append(IntersectionOut(
            camera_id=str(r["camera_id"]), intersection=str(r["intersection"]),
            latitude=None if pd.isna(r["latitude"]) else float(r["latitude"]),
            longitude=None if pd.isna(r["longitude"]) else float(r["longitude"]),
            typical_weekday_pred=_f(r["typical_weekday_pred"]),
            typical_weekend_pred=_f(r["typical_weekend_pred"]),
            typical_weekday_band=_s(r["typical_weekday_band"]),
            typical_weekend_band=_s(r["typical_weekend_band"]),
            typical_weekday_score=_f(r["typical_weekday_score"]),
            typical_weekend_score=_f(r["typical_weekend_score"]),
            actual_test_mean=_f(r["actual_test_mean"]),
            cam_mean_pre_test=_f(r["cam_mean_pre_test"])))
    return out


def _f(v):
    return None if pd.isna(v) else float(v)


def _s(v):
    return None if pd.isna(v) else str(v)


@router.get("/violation_trends",
            summary="Daily historical violation trend for one camera")
def violation_trends(
    camera_id: str | None = Query(default=None, description="Camera ID."),
    intersection: str | None = Query(
        default=None, description="Intersection name (exact, case-insensitive); "
        "use when you don't know the camera ID. Fails if it matches more "
        "than one camera — pass camera_id then."),
    start_date: date_cls | None = Query(default=None),
    end_date: date_cls | None = Query(default=None),
):
    """DAILY granularity only: observed violations per day from the
    processed feature table, plus the served model's daily prediction
    for the same days. No hourly values are returned or implied.
    Defaults to the last 90 days of data (data ends 2026-10-29).
    """
    if camera_id is None and intersection is None:
        raise HTTPException(status_code=400, detail={
            "error": "bad_request",
            "message": "Provide camera_id or intersection."})
    if camera_id is None:
        df = _profiles_df()
        m = df[df["intersection"].str.lower() == intersection.lower()]
        if len(m) == 0:
            raise HTTPException(status_code=404, detail={
                "error": "not_found",
                "message": f"No camera with intersection {intersection!r}."})
        if len(m) > 1:
            raise HTTPException(status_code=400, detail={
                "error": "ambiguous_intersection",
                "message": f"Intersection {intersection!r} matches "
                           f"{len(m)} cameras: "
                           + ", ".join(m["camera_id"].astype(str)) +
                           ". Pass camera_id."})
        camera_id = str(m.iloc[0]["camera_id"])
    cam = str(camera_id)

    feats = service.features
    latest = feats["date"].max()
    end = pd.Timestamp(end_date) if end_date else latest
    start = pd.Timestamp(start_date) if start_date else end - pd.Timedelta(days=89)
    if end < start:
        raise HTTPException(status_code=400, detail={
            "error": "bad_request",
            "message": "end_date must be on or after start_date."})
    if (end - start).days > config.MAX_TREND_DAYS:
        raise HTTPException(status_code=400, detail={
            "error": "bad_request",
            "message": f"Date range too long (max {config.MAX_TREND_DAYS} days)."})

    sub = feats[(feats["camera_id"] == cam)
                & (feats["date"] >= start) & (feats["date"] <= end)].copy()
    if len(sub) == 0:
        raise HTTPException(status_code=404, detail={
            "error": "not_found",
            "message": f"No daily records for camera {cam} in "
                       f"{start.date()}..{end.date()}."})
    sub = sub.sort_values("date")
    vecs = np.stack([service.build_vector(r, cam)
                     for _, r in sub.iterrows()]).reshape(len(sub), -1).astype(np.float32)
    preds = service.predict_rows(vecs)
    days = [{
        "date": d.strftime("%Y-%m-%d"),
        "observed_violations": int(v),
        "predicted_violations": round(float(p), 4),
    } for d, v, p in zip(sub["date"], sub["violations"], preds)]
    prof = _profile_for(cam)
    return {
        "camera_id": cam,
        "intersection": prof["intersection"] if prof else None,
        "granularity": "daily",
        "note": "Daily observed violations from the processed feature table; "
                "predictions are the served ONNX daily model. No hourly data.",
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "days": days,
        "summary": {
            "n_days": len(days),
            "mean_observed": round(float(np.mean([d["observed_violations"] for d in days])), 2),
            "mean_predicted": round(float(np.mean([d["predicted_violations"] for d in days])), 2),
            "total_observed": int(sum(d["observed_violations"] for d in days)),
        },
    }
