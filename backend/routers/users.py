"""Redunator Phase 3 — user endpoints (Worker B).

Minimal but REAL endpoints per the SDD: spike reports, proximity
alerts, feedback, per-user settings. All persistence goes through
``backend.storage.Store`` — one interface, SQLite by default,
PostgreSQL when ``DATABASE_URL`` is set.

Identity & auth (device JWTs, added 2026-11-18 — see backend/auth.py)
----------------------------------------------------------------------
The app has no user accounts. Identity is a device-scoped JWT: the
app mints one at ``POST /auth/device`` (``{"device_id": "<uuid>"}``)
and sends ``Authorization: Bearer <token>`` on every endpoint here.
The token's ``sub`` claim IS the user id. The old ``X-User-Id``
header (and ``user_id`` body field) were spoofable by anyone and
are no longer read for identity at all; the old ``X-Api-Key``
local-dev gate is gone with them. Missing / invalid / expired
token -> 401 ``{"error": "unauthorized", ...}``.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from ..auth import require_device
from ..storage import get_store

router = APIRouter()

DEFAULT_SETTINGS = {
    "units": "metric",
    "alert_radius_m": 2000,
    "risk_threshold": "MEDIUM",
    "notifications": {"email": False, "push": False},
}

BANDS = ("LOW", "MEDIUM", "HIGH")


def _err(status: int, error: str, message: str):
    return JSONResponse(status_code=status,
                        content={"error": error, "message": message})


def _parse_lat_lon(body: dict):
    """Return (lat, lon) or an error string; (None, None, None) if absent."""
    lat, lon = body.get("lat"), body.get("lon")
    if lat is None and lon is None:
        return None, None, None
    if lat is None or lon is None:
        return None, None, "both lat and lon are required together."
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None, "lat and lon must be numbers."
    if not -90.0 <= lat_f <= 90.0:
        return None, None, f"lat out of range (-90..90): {lat_f}"
    if not -180.0 <= lon_f <= 180.0:
        return None, None, f"lon out of range (-180..180): {lon_f}"
    return lat_f, lon_f, None


def _parse_timestamp(raw):
    if raw is None:
        return None, None
    try:
        dt = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except ValueError:
        return None, f"timestamp is not a parseable ISO-8601 value: {raw!r}"
    return dt.isoformat(), None


# ------------------------------------------------------------------
# POST /user_report — "Report Spike"
# ------------------------------------------------------------------
@router.post("/user_report", status_code=201)
def create_user_report(payload: dict = Body(...),
                       device_id: str = Depends(require_device)):
    camera_id = payload.get("camera_id")
    if camera_id is not None:
        camera_id = str(camera_id).strip() or None
    lat, lon, err = _parse_lat_lon(payload)
    if err:
        return _err(400, "invalid_parameters", err)
    if camera_id is None and lat is None:
        return _err(400, "invalid_parameters",
                    "Provide camera_id and/or lat+lon to identify the "
                    "location of the spike.")
    reported_at, err = _parse_timestamp(payload.get("timestamp"))
    if err:
        return _err(400, "invalid_parameters", err)
    note = payload.get("note")
    if note is not None:
        note = str(note)
    rec = get_store().create_report(
        user_id=device_id, camera_id=camera_id,
        latitude=lat, longitude=lon, reported_at=reported_at, note=note)
    return JSONResponse(status_code=201, content=rec)


@router.get("/user_report/{report_id}")
def get_user_report(report_id: int,
                    device_id: str = Depends(require_device)):
    rec = get_store().get_report(report_id)
    if rec is None or str(rec.get("user_id")) != device_id:
        # 404 either way: do not reveal whether another device's
        # report exists.
        return _err(404, "not_found", f"No report with id {report_id}.")
    return rec


# ------------------------------------------------------------------
# /user_alerts — proximity-alert configs
# ------------------------------------------------------------------
@router.get("/user_alerts")
def list_user_alerts(
        active_only: bool = Query(True),
        device_id: str = Depends(require_device)):
    alerts = get_store().list_alerts(device_id, active_only=active_only)
    return {"user_id": device_id, "alerts": alerts}


@router.post("/user_alerts", status_code=201)
def create_user_alert(payload: dict = Body(...),
                      device_id: str = Depends(require_device)):
    lat, lon, err = _parse_lat_lon(payload)
    if err:
        return _err(400, "invalid_parameters", err)
    if lat is None:
        return _err(400, "invalid_parameters",
                    "lat and lon are required for a proximity alert.")
    radius_m = payload.get("radius_m", 2000)
    try:
        radius_m = float(radius_m)
    except (TypeError, ValueError):
        return _err(400, "invalid_parameters",
                    f"radius_m must be a number, got {radius_m!r}")
    if not 100 <= radius_m <= 50000:
        return _err(400, "invalid_parameters",
                    f"radius_m must be 100..50000, got {radius_m}")
    threshold = str(payload.get("threshold", "MEDIUM")).upper()
    if threshold not in BANDS:
        return _err(400, "invalid_parameters",
                    f"threshold must be one of {list(BANDS)}, got {threshold!r}")
    threshold_value = payload.get("threshold_value")
    if threshold_value is not None:
        try:
            threshold_value = float(threshold_value)
        except (TypeError, ValueError):
            return _err(400, "invalid_parameters",
                        "threshold_value must be a number.")
        if threshold_value < 0:
            return _err(400, "invalid_parameters",
                        "threshold_value must be >= 0.")
    name = payload.get("name")
    rec = get_store().create_alert(
        user_id=device_id, latitude=lat, longitude=lon,
        radius_m=radius_m, threshold=threshold,
        threshold_value=threshold_value,
        name=str(name) if name is not None else None)
    # SQLite stores active as INTEGER 1; coerce to a real JSON boolean,
    # as the list/delete handlers already do (Worker C fix, 2026-11-16:
    # POST used to return "active": 1 while GET returned true).
    rec["active"] = bool(rec["active"])
    return JSONResponse(status_code=201, content=rec)


@router.delete("/user_alerts/{alert_id}")
def delete_user_alert(alert_id: int,
                      device_id: str = Depends(require_device)):
    rec = get_store().deactivate_alert(alert_id, device_id)
    if rec is None:
        return _err(404, "not_found",
                    f"No alert with id {alert_id} for this user.")
    rec["active"] = bool(rec["active"])
    return rec


# ------------------------------------------------------------------
# POST /user_feedback
# ------------------------------------------------------------------
@router.post("/user_feedback", status_code=201)
def create_user_feedback(payload: dict = Body(...),
                         device_id: str = Depends(require_device)):
    text = payload.get("text", payload.get("message"))
    if text is None or not str(text).strip():
        return _err(400, "invalid_parameters",
                    "Feedback text is required (field 'text').")
    rating = payload.get("rating")
    if rating is not None:
        try:
            rating = int(rating)
        except (TypeError, ValueError):
            return _err(400, "invalid_parameters",
                        f"rating must be an integer 1..5, got {rating!r}")
        if not 1 <= rating <= 5:
            return _err(400, "invalid_parameters",
                        f"rating must be 1..5, got {rating}")
    category = payload.get("category")
    rec = get_store().create_feedback(
        user_id=device_id, text=str(text),
        rating=rating,
        category=str(category) if category is not None else None)
    return JSONResponse(status_code=201, content=rec)


# ------------------------------------------------------------------
# /user_settings
# ------------------------------------------------------------------
@router.get("/user_settings")
def get_user_settings(
        device_id: str = Depends(require_device)):
    stored = get_store().get_settings(device_id)
    merged = dict(DEFAULT_SETTINGS)
    merged.update(stored)
    if isinstance(merged.get("notifications"), dict) and \
            isinstance(DEFAULT_SETTINGS["notifications"], dict):
        notes = dict(DEFAULT_SETTINGS["notifications"])
        notes.update(merged["notifications"])
        merged["notifications"] = notes
    return {"user_id": device_id, "settings": merged}


@router.post("/user_settings")
def set_user_settings(payload: dict = Body(...),
                      device_id: str = Depends(require_device)):
    settings = payload.get("settings")
    if settings is None:
        settings = {k: v for k, v in payload.items() if k != "user_id"}
    if not isinstance(settings, dict):
        return _err(400, "invalid_parameters",
                    "settings must be a JSON object.")
    if "units" in settings and settings["units"] not in ("metric", "imperial"):
        return _err(400, "invalid_parameters",
                    "units must be 'metric' or 'imperial'.")
    if "risk_threshold" in settings and \
            str(settings["risk_threshold"]).upper() not in BANDS:
        return _err(400, "invalid_parameters",
                    f"risk_threshold must be one of {list(BANDS)}.")
    if "risk_threshold" in settings:
        settings["risk_threshold"] = str(settings["risk_threshold"]).upper()
    if "alert_radius_m" in settings:
        try:
            settings["alert_radius_m"] = float(settings["alert_radius_m"])
        except (TypeError, ValueError):
            return _err(400, "invalid_parameters",
                        "alert_radius_m must be a number.")
    uid = device_id
    stored = get_store().set_settings(uid, settings)
    merged = dict(DEFAULT_SETTINGS)
    merged.update(stored)
    return {"user_id": uid, "settings": merged}
