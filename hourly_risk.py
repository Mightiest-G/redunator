#!/usr/bin/env python3
"""Redunator Phase 2b — hourly risk prediction API (ESTIMATES).

`predict_hourly(camera_id, date)` splits the shipped Phase 2 daily
prediction for one camera-day across the 24 hours:

    hourly[h] = daily_prediction(camera, date)
                x profile_weight(camera, h)
                x weather_multiplier(h) [x congestion_multiplier(h)]
                [x demand_multiplier(h)]
    renormalised so sum(hourly) == daily_prediction exactly.

The daily total is the anchor and is never changed by the hourly
layer; the profile and multipliers only shape the distribution within
the day. Every hourly number is an ESTIMATE: the daily violation data
has no hour-of-day, so the hourly shape is built from crash and
congestion proxies (see `hourly_model.py`) plus, in Phase 2c, trip
demand (see `build_demand.py` / DEMAND_BUILD.md).

Methods (base "profile" / "model" as below; "+demand" / "+slowness"
suffixes when those layers are on — demand OFF by default (opt-in,
see `demand` parameter; it loses on every validation variant, Phases
2c/2d); slowness on by default on the profile path only,
see `slowness` parameter):
  * "profile" (default base, always available): the profile formula above.
    For dates after 2026-04-30 the congestion multiplier is 1.0 for
    every hour — the congestion feed is dead and is never
    forward-filled; the shape then comes from the profile + weather.
  * "model": if `hourly_model.joblib` exists (Poisson model that beat
    the profile baseline — see `hourly_model.py`), the 24 hourly
    weights come from the model's predicted crash counts for that
    camera-day, normalised to sum to 1. No extra weather/congestion
    multipliers are applied on this path (the model already includes
    weather and speed as features). Falls back to "profile" with a
    flag when the model file is absent or the date lacks model inputs
    (weather row / crash history — i.e. after 2026-10-29).
  * Demand layer ("profile+demand" / "model+demand"): multiplies by
    the camera's community-area trip-demand multiplier for the date's
    day type (weekday / weekend / holiday), then renormalises. The
    demand multiplier has mean exactly 1.0, so it only reshapes the
    day. Unmapped cameras (1623, 2552, 2592, 2662 — no coordinates)
    and unknown cameras use the citywide demand profile. The demand
    layer is a post-hoc shape adjustment on the model path too: the
    Poisson model has no demand feature (that would need a retrain
    and re-validation). Pass demand=False (or method="profile" /
    "model" with demand=False) to reproduce the exact Phase 2b
    output.
  * Slowness layer (Phase 2d, "profile+slowness" /
    "profile+demand+slowness"): multiplies the PROFILE path only by
    the camera's community-area trip-slowness multiplier for the
    date's day type (1 / mean trip speed, from speed_profiles.parquet,
    pre-holdout trips 2023-01-01 -> 2026-07-31), then renormalises.
    It is NOT applied on the model path: post-hoc slowness hurts the
    model on the holdout (XGBoost shape + slowness 0.093480 vs
    0.093335; B12 shape + slowness 0.093392 vs 0.093260), and the
    retrained models that took slowness as a feature did not beat
    their no-slowness controls — so `slowness=True` is silently
    ignored on the model path (the result reports
    `slowness_applied: False` there). On the profile path it wins:
    profile + weather + slowness 0.094390 vs profile + weather
    0.094561 on the holdout (HOURLY_REPORT.md §9). Pass
    slowness=False to reproduce the exact Phase 2b/2c profile output.

Risk bands — PER-HOUR thresholds (Phase 1, item 2), NOT the daily ones.
The Phase 2 daily bands (LOW < 4, MEDIUM 4-<10, HIGH >= 10 violations/day)
were applied per hour in Phase 2b, which made almost every hour LOW by
construction — even the hottest camera's peak hour (1413, 4.82) only
reached MEDIUM, and hourly HIGH was nearly unreachable. The hourly
bands below are derived the SAME WAY risk.py derived the daily bands
(LOW/MEDIUM = round(p50), MEDIUM/HIGH = round(p90) of the shipped
model's predictions on the Phase 2 holdout), but on the HOURLY
population: the default `predict_hourly` output for all 26,752
holdout camera-days x 24 h = 642,048 camera-hours (script:
`derive_hourly_bands.py`, numbers:
`data/processed/hourly_risk_thresholds.json`). Hourly counts are ~24x
smaller than daily counts, so the rounding is to 0.1 violations/hour
instead of whole violations/day. Result: LOW < 0.1, MEDIUM 0.1-<0.5,
HIGH >= 0.5 predicted violations/hour (ESTIMATE). Holdout shares:
LOW 42.0%, MEDIUM 48.6%, HIGH 9.4% — HIGH is again about the top
decile, as designed for the daily bands. The old daily-threshold
hourly banding is still available via `banding="daily"` for
comparison. The DAILY bands in risk.py are untouched and remain the
right unit for "is this a bad day".

Daily predictions come from the SHIPPED Phase 2 model
(`redunator_brain_v1`, Random Forest) via `daily_prediction`, which
delegates to the backend's ONNX service (`backend.model_service` —
the same session that serves /predict_risk). The ONNX graph was
exported from the same forest and reproduces its predictions to
<= 2e-05 (backend/verify_onnx.py), so the hourly anchor and the
served daily total are now the same number instead of two
near-equal ones. If the backend package cannot be imported
(standalone use outside the project), it falls back to scoring
the joblib file directly with the same feature list and fill rules.
Either way the rows come from the SERVING table
(data/processed/red_light_features_serving.parquet — see
build_serving_parquet.py) and the daily model files are never
modified. Dates with no row in the feature table (camera
inactive that day, or a date after 2026-10-29) fall back to the
camera's pre-test mean, or the citywide pre-test mean (~5.3/day) for
an unknown camera — flagged in the result.

Usage:
    from hourly_risk import predict_hourly
    r = predict_hourly("1413", "2026-10-15")
    r["hourly"]          # list of 24 predicted violations (ESTIMATES)
    r["daily_prediction"]
    r["bands"]           # list of 24 band labels
    r["method"]          # "profile" or "model"
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

import hourly_model as hm

BASE = Path(__file__).resolve().parent
# Serving table (build_serving_parquet.py): the 19 columns the serving
# path reads. The full 23-column table stays the training artifact
# and is never loaded at serving time.
FEATURES_PATH = BASE / "data" / "processed" / "red_light_features_serving.parquet"
DAILY_MODEL_PATH = BASE / "redunator_brain_v1.joblib"

# Lever 1 (Render free-tier slimming, 2026-11-17): the daily anchor
# comes from the backend's ONNX service instead of loading the joblib
# forest here. In the backend process that removes the second parquet
# copy AND the ~295 MB joblib load; the ONNX session is shared with
# /predict_risk. Fallback (joblib) only if the backend package cannot
# be imported at all.
try:
    from backend.model_service import service as _onnx_service
except Exception:  # noqa: BLE001 - standalone use without the backend
    _onnx_service = None

# Phase 2 daily bands (REPORT.md §2), kept for the "daily" banding
# option only. NEVER applied to hourly output by default any more.
BAND_LOW_MAX = 4.0
BAND_HIGH_MIN = 10.0
# Per-hour bands (Phase 1, item 2) — derived in derive_hourly_bands.py;
# the JSON is the auditable record, these are the fallback if it is
# ever missing. LOW < 0.1, MEDIUM 0.1-<0.5, HIGH >= 0.5 /hour.
HOURLY_BAND_LOW_MAX = 0.1
HOURLY_BAND_HIGH_MIN = 0.5
# Hourly risk-score scale (wired 2026-11-17, decision: yes): the p99
# of predicted HOURLY counts is 2.0 (derive_hourly_bands.py;
# hourly_risk_thresholds.json), so risk_scores = clip(hourly / 2.0, 0, 1).
# The old daily / 35 scale made the hottest hour score ~0.14 and a
# typical hour ~0.01 — uninterpretable at hourly resolution.
HOURLY_SCORE_SCALE = 2.0
HOURLY_BAND_THRESHOLDS_PATH = (BASE / "data" / "processed"
                               / "hourly_risk_thresholds.json")


def _load_hourly_thresholds():
    try:
        t = json.loads(HOURLY_BAND_THRESHOLDS_PATH.read_text())
        return float(t["med_cut"]), float(t["high_cut"])
    except Exception:
        return HOURLY_BAND_LOW_MAX, HOURLY_BAND_HIGH_MIN


HOURLY_BAND_LOW_MAX, HOURLY_BAND_HIGH_MIN = _load_hourly_thresholds()
CITYWIDE_DAILY_MEAN = 5.34  # Phase 2 train target mean (REPORT.md §1)

_cache: dict = {}


def risk_band(hourly_pred: float, banding: str = "hourly") -> str:
    """Band for one hourly prediction (ESTIMATE).

    `banding="hourly"` (default): the per-hour bands derived from the
    holdout distribution of hourly predictions — LOW < 0.1,
    MEDIUM 0.1-<0.5, HIGH >= 0.5 predicted violations/hour.
    `banding="daily"`: the Phase 2 daily thresholds applied per hour
    (LOW < 4, MEDIUM 4-<10, HIGH >= 10) — kept only for comparison;
    it makes almost every hour LOW by construction.
    """
    if banding not in ("hourly", "daily"):
        raise ValueError(f"unknown banding {banding!r}: use 'hourly' or 'daily'")
    low_max, high_min = ((HOURLY_BAND_LOW_MAX, HOURLY_BAND_HIGH_MIN)
                         if banding == "hourly"
                         else (BAND_LOW_MAX, BAND_HIGH_MIN))
    if hourly_pred < low_max:
        return "LOW"
    if hourly_pred < high_min:
        return "MEDIUM"
    return "HIGH"


def _load_daily_model():
    if "daily_model" not in _cache:
        _cache["daily_model"] = joblib.load(DAILY_MODEL_PATH)
    return _cache["daily_model"]


def _load_features() -> pd.DataFrame:
    if "features" not in _cache:
        df = pd.read_parquet(FEATURES_PATH)
        df["date"] = pd.to_datetime(df["date"])
        _cache["features"] = df
        train_mask = df["date"] <= "2026-07-31"
        train_cams = sorted(df.loc[train_mask, "camera_id"].unique())
        _cache["code_map"] = {c: i for i, c in enumerate(train_cams)}
        _cache["med_lat"] = float(df.loc[train_mask, "latitude"].median())
        _cache["med_lon"] = float(df.loc[train_mask, "longitude"].median())
    return _cache["features"]


def daily_prediction(camera_id: str, date) -> tuple[float, str]:
    """Daily predicted violations for one camera-day (shipped RF model).

    Served by the backend's ONNX service (the same session that
    serves /predict_risk) when it is importable; falls back to the
    joblib file only if the backend package cannot be imported.
    Returns (prediction, source) where source is "model" (scored from
    the feature-table row), "pre_test_mean" (no row for that
    camera-day: camera inactive or date outside the table), or
    "citywide_mean" (unknown camera). Predictions are clipped at 0,
    matching train.py.
    """
    if _onnx_service is not None:
        return _onnx_service.daily_prediction(str(camera_id), date)
    return _daily_prediction_joblib(camera_id, date)


def _daily_prediction_joblib(camera_id: str, date) -> tuple[float, str]:
    """Joblib fallback for daily_prediction (same contract).

    Only used when the backend ONNX service cannot be imported.
    Same feature list and fill rules as the ONNX path; the forest
    is loaded lazily and cached.
    """
    from train import FEATURES  # reuse the shipped feature list, don't copy it

    cam = str(camera_id)
    date = pd.Timestamp(date)
    feats = _load_features()
    row = feats[(feats["camera_id"] == cam) & (feats["date"] == date)]
    if len(row) == 0:
        pre = feats[feats["camera_id"] == cam]["cam_mean_pre_test"]
        if len(pre) and pd.notna(pre.iloc[0]):
            return float(pre.iloc[0]), "pre_test_mean"
        return CITYWIDE_DAILY_MEAN, "citywide_mean"
    r = row.iloc[0]
    x = {}
    for f in FEATURES:
        if f == "cam_code":
            continue  # not a parquet column; train.py creates it at load
        x[f] = r[f]
    # train.py fill rules, verbatim.
    for col in ("lag_1d", "roll_mean_7d", "roll_mean_30d"):
        if pd.isna(x[col]):
            x[col] = 0.0
    if pd.isna(x["latitude"]):
        x["latitude"] = _cache["med_lat"]
    if pd.isna(x["longitude"]):
        x["longitude"] = _cache["med_lon"]
    code_map = _cache["code_map"]
    x["cam_code"] = code_map.get(cam, int(np.median(list(code_map.values()))))
    vec = np.array([[float(x[f]) for f in FEATURES]], dtype=np.float32)
    pred = float(np.clip(_load_daily_model().predict(vec)[0], 0, None))
    return pred, "model"


def predict_hourly(camera_id: str, date, method: str = "auto",
                   demand: bool = False, slowness: bool = True,
                   banding: str = "hourly") -> dict:
    """Predict 24 hourly violations (ESTIMATES) for one camera-day.

    `banding`: "hourly" (default) — the per-hour bands (LOW < 0.1,
    MEDIUM 0.1-<0.5, HIGH >= 0.5 predicted violations/hour, derived
    in derive_hourly_bands.py); "daily" — the old Phase 2 daily
    thresholds applied per hour (LOW < 4, MEDIUM 4-<10, HIGH >= 10),
    kept for comparison. Banding changes ONLY the band labels, never
    the predictions.

    `method`: "auto" (model if available, else profile), "profile",
    "model", "profile+demand", "profile+slowness",
    "profile+demand+slowness", or "model+demand". An explicit
    "+demand" / "+slowness" suffix forces that layer on for that
    call; otherwise the `demand` (default False) and `slowness`
    (default True) flags control them. `slowness` applies ONLY on
    the profile path — on the model path it is ignored (Phase 2d:
    post-hoc slowness hurts the model on the holdout), and the
    result reports `slowness_applied: False` there. With
    demand=False and slowness=False the output reproduces Phase 2b
    exactly (profile path) / the Phase 2b model output (model path,
    modulo the Phase 2d model retrain — see hourly_model.py).

    Returns a dict with: camera_id, date, daily_prediction,
    daily_source, hourly (list of 24 floats), bands (list of 24),
    banding ("hourly" or "daily"), risk_scores (hourly / HOURLY_SCORE_SCALE
    = 2.0, the hourly p99 scale — wired 2026-11-17, was hourly / 35),
    method ("profile", "model", "profile+demand", "profile+slowness",
    "profile+demand+slowness", or "model+demand"),
    fallback_note (why the model path was not used, or None),
    demand (bool), slowness_applied (bool), demand_day_type,
    community_area, and estimate=True on every hourly value.
    The 24 hourly values sum to daily_prediction (within float
    rounding) by construction.
    """
    cam = str(camera_id)
    date = pd.Timestamp(date)
    daily, daily_source = daily_prediction(cam, date)

    # Resolve method + demand/slowness flags. Explicit "+demand" /
    # "+slowness" suffixes force those layers on; otherwise the
    # `demand` (default False) / `slowness` (default True) flags decide.
    m = str(method).lower()
    force_demand = False
    force_slowness = False
    while True:
        if m.endswith("+demand"):
            m = m[: -len("+demand")]
            force_demand = True
        elif m.endswith("+slowness"):
            m = m[: -len("+slowness")]
            force_slowness = True
        else:
            break
    use_demand = True if force_demand else bool(demand)
    use_slowness = True if force_slowness else bool(slowness)
    if m not in ("auto", "profile", "model"):
        raise ValueError(f"unknown method {method!r}: use auto/profile/model "
                         f"(optionally +demand / +slowness)")

    use_model = m in ("auto", "model")
    fallback_note = None
    weights = None
    used = "profile"
    if use_model:
        weights = hm.model_weights(cam, date)
        if weights is not None:
            used = "model"
        else:
            fallback_note = ("Poisson model unavailable for this "
                             "camera/date (no saved model, or date "
                             "outside the weather/crash window); "
                             "profile method used.")
    if weights is None:
        weights = hm.profile_weights(cam)
        if m == "model":
            fallback_note = ("Poisson model unavailable for this "
                             "camera/date; profile method used.")

    dm = None
    day_type = None
    area = None
    if use_demand:
        dm = hm.demand_multipliers_for_day(cam, date)
        day_type = hm.day_type_for_date(date)
        area = hm.camera_community_area(cam)
        used = used + "+demand"

    # Slowness (Phase 2d): profile path only. On the model path it is
    # ignored — post-hoc slowness hurts the model on the holdout
    # (§9) — so slowness_applied stays False there.
    sm = None
    slowness_applied = False
    if use_slowness and not used.startswith("model"):
        sm = hm.slowness_multipliers_for_day(cam, date)
        if day_type is None:
            day_type = hm.day_type_for_date(date)
        if area is None:
            area = hm.camera_community_area(cam)
        used = used + "+slowness"
        slowness_applied = True

    if daily <= 0:
        hourly = np.zeros(24)
    elif used.startswith("model"):
        # Model weights already sum to 1; no extra weather/congestion
        # multipliers (the model includes weather + speed as features).
        # Demand, when on, is a post-hoc shape layer: multiply then
        # renormalise so the day still sums to the daily prediction.
        raw = daily * weights if dm is None else daily * weights * dm
        total = raw.sum()
        hourly = raw / total * daily if total > 0 else np.zeros(24)
    else:
        wx = hm.weather_multipliers_for_day(date)
        cg = hm.congestion_multipliers_for_day(cam, date)
        raw = daily * weights * wx * cg
        if dm is not None:
            raw = raw * dm
        if sm is not None:
            raw = raw * sm
        total = raw.sum()
        hourly = raw / total * daily if total > 0 else np.zeros(24)
    hourly = np.clip(np.asarray(hourly, dtype=float), 0, None)
    # Final exact reconciliation: put float residual on the peak hour.
    resid = daily - float(hourly.sum())
    if abs(resid) > 0 and daily > 0:
        hourly[int(np.argmax(hourly))] += resid

    if banding not in ("hourly", "daily"):
        raise ValueError(f"unknown banding {banding!r}: use 'hourly' or 'daily'")

    return {
        "camera_id": cam,
        "date": date.strftime("%Y-%m-%d"),
        "daily_prediction": float(daily),
        "daily_source": daily_source,
        "hourly": [float(v) for v in hourly],
        "bands": [risk_band(float(v), banding) for v in hourly],
        "banding": banding,
        "risk_scores": [round(min(float(v) / HOURLY_SCORE_SCALE, 1.0), 4)
                        for v in hourly],
        "method": used,
        "fallback_note": fallback_note,
        "demand": bool(use_demand),
        "slowness_applied": bool(slowness_applied),
        "demand_day_type": day_type,
        "community_area": area,
        "estimate": True,
        "hourly_sum": float(hourly.sum()),
        "reconciliation_error": float(hourly.sum() - daily),
    }


if __name__ == "__main__":
    import json
    import sys

    cam = sys.argv[1] if len(sys.argv) > 1 else "1413"
    date = sys.argv[2] if len(sys.argv) > 2 else "2026-10-15"
    use_demand = not (len(sys.argv) > 3 and sys.argv[3] == "nodemand")
    banding = sys.argv[4] if len(sys.argv) > 4 else "hourly"
    r = predict_hourly(cam, date, demand=use_demand, banding=banding)
    print(json.dumps(r, indent=2))
    print(f"peak hour: {int(np.argmax(r['hourly']))}:00 "
          f"({max(r['hourly']):.2f} predicted, {r['bands'][int(np.argmax(r['hourly']))]})")
