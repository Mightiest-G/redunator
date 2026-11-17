"""Redunator Phase 3 — user endpoints (Worker B).

Minimal but REAL endpoints per the SDD: spike reports, proximity
alerts, feedback, per-user settings. All persistence goes through
``backend.storage.Store`` — one interface, SQLite by default,
PostgreSQL when ``DATABASE_URL`` is set.

Identity & auth (Phase 3 choice — see backend/README.md)
---------------------------------------------------------
* The user is identified by the ``X-User-Id`` header, falling back
  to a ``user_id`` field in the JSON body, falling back to
  ``"anonymous"``. There is no account system in this phase.
* No auth in local dev by default. If the ``REDUNATOR_API_KEY``
  env var is set, every endpoint here requires the matching
  ``X-Api-Key`` header (401 otherwise). Production MUST replace
  this with JWT auth before any public deployment (per the SDD);
  this header-key check is a local-dev gate only, not a user auth
  system.
"""

from __future__ import annotations

import os
from datetime import datetime

from fastapi import APIRouter, Body, Header, Query
from fastapi.responses import JSONResponse

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


def _require_api_key(x_api_key: str | None):
    """Local-dev gate: enforced only when REDUNATOR_API_KEY is set."""
    expected = os.environ.get("REDUNATOR_API_KEY", "")
    if expected and x_api_key != expected:
        return _err(401, "unauthorized",
                    "Missing or incorrect X-Api-Key header.")
    return None


def _user_id(x_user_id: str | None, body: dict) -> str:
    if x_user_id:
        return str(x_user_id)
    if body.get("user_id"):
        return str(body["user_id"])
    return "anonymous"


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
                       x_user_id: str | None = Header(None, alias="X-User-Id"),
                       x_api_key: str | None = Header(None, alias="X-Api-Key")):
    gate = _require_api_key(x_api_key)
    if gate is not None:
        return gate
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
        user_id=_user_id(x_user_id, payload), camera_id=camera_id,
        latitude=lat, longitude=lon, reported_at=reported_at, note=note)
    return JSONResponse(status_code=201, content=rec)


@router.get("/user_report/{report_id}")
def get_user_report(report_id: int,
                    x_api_key: str | None = Header(None, alias="X-Api-Key")):
    gate = _require_api_key(x_api_key)
    if gate is not None:
        return gate
    rec = get_store().get_report(report_id)
    if rec is None:
        return _err(404, "not_found", f"No report with id {report_id}.")
    return rec


# ------------------------------------------------------------------
# /user_alerts — proximity-alert configs
# ------------------------------------------------------------------
@router.get("/user_alerts")
def list_user_alerts(
        user_id: str | None = Query(None),
        active_only: bool = Query(True),
        x_user_id: str | None = Header(None, alias="X-User-Id"),
        x_api_key: str | None = Header(None, alias="X-Api-Key")):
    gate = _require_api_key(x_api_key)
    if gate is not None:
        return gate
    uid = x_user_id or user_id or "anonymous"
    alerts = get_store().list_alerts(str(uid), active_only=active_only)
    return {"user_id": str(uid), "alerts": alerts}


@router.post("/user_alerts", status_code=201)
def create_user_alert(payload: dict = Body(...),
                      x_user_id: str | None = Header(None, alias="X-User-Id"),
                      x_api_key: str | None = Header(None, alias="X-Api-Key")):
    gate = _require_api_key(x_api_key)
    if gate is not None:
        return gate
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
        user_id=_user_id(x_user_id, payload), latitude=lat, longitude=lon,
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
                      x_user_id: str | None = Header(None, alias="X-User-Id"),
                      x_api_key: str | None = Header(None, alias="X-Api-Key")):
    gate = _require_api_key(x_api_key)
    if gate is not None:
        return gate
    rec = get_store().deactivate_alert(alert_id, x_user_id or "anonymous")
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
                         x_user_id: str | None = Header(None, alias="X-User-Id"),
                         x_api_key: str | None = Header(None, alias="X-Api-Key")):
    gate = _require_api_key(x_api_key)
    if gate is not None:
        return gate
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
        user_id=_user_id(x_user_id, payload), text=str(text),
        rating=rating,
        category=str(category) if category is not None else None)
    return JSONResponse(status_code=201, content=rec)


# ------------------------------------------------------------------
# /user_settings
# ------------------------------------------------------------------
@router.get("/user_settings")
def get_user_settings(
        user_id: str | None = Query(None),
        x_user_id: str | None = Header(None, alias="X-User-Id"),
        x_api_key: str | None = Header(None, alias="X-Api-Key")):
    gate = _require_api_key(x_api_key)
    if gate is not None:
        return gate
    uid = str(x_user_id or user_id or "anonymous")
    stored = get_store().get_settings(uid)
    merged = dict(DEFAULT_SETTINGS)
    merged.update(stored)
    if isinstance(merged.get("notifications"), dict) and \
            isinstance(DEFAULT_SETTINGS["notifications"], dict):
        notes = dict(DEFAULT_SETTINGS["notifications"])
        notes.update(merged["notifications"])
        merged["notifications"] = notes
    return {"user_id": uid, "settings": merged}


@router.post("/user_settings")
def set_user_settings(payload: dict = Body(...),
                      x_user_id: str | None = Header(None, alias="X-User-Id"),
                      x_api_key: str | None = Header(None, alias="X-Api-Key")):
    gate = _require_api_key(x_api_key)
    if gate is not None:
        return gate
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
    uid = _user_id(x_user_id, payload)
    stored = get_store().set_settings(uid, settings)
    merged = dict(DEFAULT_SETTINGS)
    merged.update(stored)
    return {"user_id": uid, "settings": merged}
