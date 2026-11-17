"""Redunator Phase 3 — GET /live/risk (Worker B).

Implements LIVE_CONTRACT.md exactly. Serves the VALIDATED models
plus live-signal freshness reporting — the live adjustment is
EXPERIMENTAL and does NOT ship (backtest: LIVE-A deviance 0.094370
vs baseline 0.094369; LIVE-B 0.093262 vs 0.093260), so this endpoint
MUST return ``adjustment_applied: false`` and the UNADJUSTED hourly
values. It calls ``live_adjust.adjust_current_hour`` once per
request (with a single ``collect_live_signals()`` fetch passed in,
so all ``fetched_at`` values match one fetch) for its audit block —
live weather, signal partitions, multipliers — and then discards
any applied adjustment, using ``hourly_unadjusted`` instead. It
never calls the adjustment with ``expected_weather_multiplier``
overridden to force a factor.

All hourly numbers are ESTIMATES from proxies, never observed
hourly violations (``estimate: true`` on every response).
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

# Top-level project modules (hourly_risk / live_adjust / live_signals)
# live in the project root, not in this package. Worker A's app.py
# puts the project root on sys.path; bootstrap it here too so this
# router also works standalone / under TestClient.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]  # backend/routers/ -> project root
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from hourly_risk import BAND_HIGH_MIN, BAND_LOW_MAX, risk_band  # noqa: E402
from live_adjust import _partition_signals, adjust_current_hour, nearest_camera  # noqa: E402
from live_signals import CHICAGO_TZ, UTC, collect_live_signals, is_stale  # noqa: E402

router = APIRouter()

SEGMENT_MAP_PATH = _PROJECT_ROOT / "data" / "hourly" / "processed" / "camera_segment_map.csv"
PROFILES_PATH = _PROJECT_ROOT / "data" / "processed" / "camera_profiles.csv"

HOURLY_LOW_MAX = 0.1
HOURLY_HIGH_MIN = 0.5

_cache: dict = {}


def _camera_coords() -> dict[str, tuple[float, float]]:
    if "coords" not in _cache:
        coords: dict[str, tuple[float, float]] = {}
        with open(SEGMENT_MAP_PATH, newline="") as f:
            for row in csv.DictReader(f):
                try:
                    lat = float(row["latitude"])
                    lon = float(row["longitude"])
                except (KeyError, TypeError, ValueError):
                    continue
                coords[str(row["camera_id"])] = (lat, lon)
        _cache["coords"] = coords
    return _cache["coords"]


def _intersections() -> dict[str, str]:
    if "intersections" not in _cache:
        names: dict[str, str] = {}
        try:
            with open(PROFILES_PATH, newline="") as f:
                for row in csv.DictReader(f):
                    names[str(row["camera_id"])] = row.get("intersection") or ""
        except FileNotFoundError:
            pass
        _cache["intersections"] = names
    return _cache["intersections"]


def _err(status: int, error: str, message: str, **extra):
    body = {"error": error, "message": message}
    body.update(extra)
    return JSONResponse(status_code=status, content=body)


def _parse_float(raw, name: str):
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {raw!r}")


def _daily_band(daily: float) -> str:
    if daily < BAND_LOW_MAX:
        return "LOW"
    if daily < BAND_HIGH_MIN:
        return "MEDIUM"
    return "HIGH"


def _build_response(*, camera_id: str, distance_m: float,
                    at_dt: datetime, signals, adj: dict,
                    serve_now: datetime) -> dict:
    """Build the contract response from one adjust_current_hour result.

    ``adj`` carries the audit block; the hourly values used here are
    ALWAYS ``adj["hourly_unadjusted"]`` and ``adjustment_applied``
    is ALWAYS False — the live adjustment does not ship.
    """
    at_chicago = at_dt.astimezone(CHICAGO_TZ)
    hour = at_chicago.hour
    date_str = at_chicago.strftime("%Y-%m-%d")
    coords = _camera_coords().get(camera_id, (None, None))
    intersection = _intersections().get(camera_id) or None

    hourly = [round(float(v), 3) for v in adj["hourly_unadjusted"]]
    daily = float(adj["daily_prediction"])
    daily_source = str(adj["daily_source"])
    # Renormalise the rounded hourly values so they sum exactly to the
    # rounded daily total. Independent rounding left a display gap of
    # up to ~0.012 (e.g. hourly summed to 10.296 while daily read 10.3
    # for cam 3041). The whole residual goes on the largest hour, which
    # keeps every value non-negative; if the largest hour is too small
    # to absorb a negative residual, the gap is left as-is rather than
    # inventing a negative hour.
    daily_rounded = round(daily, 2)
    residual = round(daily_rounded - sum(hourly), 3)
    if residual != 0.0:
        peak = max(range(24), key=lambda i: hourly[i])
        if hourly[peak] + residual >= 0:
            hourly[peak] = round(hourly[peak] + residual, 3)
    predicted = hourly[hour]
    risk_level = risk_band(predicted, "hourly")
    daily_band = _daily_band(daily)

    # Re-check staleness at serve time (contract: never serve a
    # response whose signals_used entries are stale at serve time).
    # Re-partition with the serve-time clock; if the weather sources
    # that fed live_weather have gone stale since the fetch, their
    # values are dropped, not served.
    used, dropped, ignored = _partition_signals(signals, serve_now)
    fresh_weather = {e["source"] for e in used
                     if e["source"] in ("open-meteo-current",
                                        "open-meteo-forecast")}
    all_signals_stale = len(fresh_weather) == 0

    live_weather = dict(adj["live_weather"])
    live_mult = float(adj["live_weather_multiplier"])
    if all_signals_stale:
        live_weather = {"precipitation_mm": None, "snowfall_cm": None,
                        "wind_speed_kmh": None, "precip_source": None}
        live_mult = 1.0
    else:
        if live_weather.get("precip_source") not in fresh_weather:
            live_weather["precipitation_mm"] = None
            live_weather["precip_source"] = None
        if "open-meteo-current" not in fresh_weather:
            live_weather["wind_speed_kmh"] = None

    confidence = 0.6 if daily_source == "model" else 0.4
    if all_signals_stale:
        confidence -= 0.1
    confidence = max(confidence, 0.3)

    reasons = [
        f"Current hour {hour:02d}:00 predicted {predicted:.2f} "
        f"violations ({risk_level} hourly band).",
        f"Daily total {daily:.2f} predicted ({daily_band} daily band); "
        f"daily total is the anchor and is never changed by the live layer.",
    ]
    if daily_source == "pre_test_mean":
        if date_str > "2026-10-29":
            reasons.append(
                "Daily prediction is the camera's pre-test mean: the "
                "shipped model has no feature row for this date (data "
                "ends 2026-10-29).")
        else:
            reasons.append(
                "Daily prediction is the camera's pre-test mean: no "
                "feature-table row exists for this camera on this date.")
    elif daily_source == "citywide_mean":
        reasons.append(
            "Daily prediction is the citywide pre-test mean: this "
            "camera has no pre-test history.")
    precip = live_weather.get("precipitation_mm")
    if all_signals_stale:
        reasons.append(
            "No fresh live weather signal was available; no adjustment "
            "applied.")
    elif precip is None:
        reasons.append(
            "Live weather signal fresh but no precipitation value for "
            "the current hour; no adjustment applied.")
    elif float(precip) == 0.0:
        reasons.append(
            "Live weather dry at the current hour (0.0 mm "
            "precipitation); no adjustment applied.")
    else:
        reasons.append(
            f"Live weather wet at the current hour ({float(precip):.1f} "
            f"mm precipitation); no adjustment applied.")

    return {
        "camera_id": str(camera_id),
        "intersection": intersection,
        "distance_m": float(round(distance_m)),
        "latitude": coords[0],
        "longitude": coords[1],
        "at": at_dt.isoformat(),
        "current_hour": hour,
        "date": date_str,
        "risk_score": round(min(max(predicted / 35.0, 0.0), 1.0), 4),
        "risk_level": risk_level,
        "predicted_violations_this_hour": predicted,
        "daily_prediction": round(daily, 2),
        "daily_band": daily_band,
        "daily_source": daily_source,
        "confidence": round(confidence, 2),
        "reasons": reasons,
        "hourly_band_thresholds": {"low_max": HOURLY_LOW_MAX,
                                   "high_min": HOURLY_HIGH_MIN},
        "daily_band_thresholds": {"low_max": BAND_LOW_MAX,
                                  "high_min": BAND_HIGH_MIN},
        "live_adjustment": {
            # The adjustment does NOT ship: always False / 1.0, and
            # the hourly values above are the unadjusted baseline.
            "adjustment_applied": False,
            "adjustment_factor": 1.0,
            "live_weather_multiplier": live_mult,
            "expected_weather_multiplier": float(
                adj["expected_weather_multiplier"]),
            "live_weather": live_weather,
            "all_signals_stale": all_signals_stale,
            "signals_used": used,
            "signals_dropped_stale": dropped,
            "signals_ignored": ignored,
        },
        "estimate": True,
        "hourly": hourly,
    }


@router.get("/live/risk")
def live_risk(lat: str | None = Query(None),
              lon: str | None = Query(None),
              at: str | None = Query(None),
              max_distance_m: str | None = Query(None)):
    # -- validate before any fetch (contract error table) ----------
    if lat is None or lon is None:
        return _err(400, "invalid_parameters",
                    "lat and lon query parameters are required.")
    try:
        lat_f = _parse_float(lat, "lat")
        lon_f = _parse_float(lon, "lon")
    except ValueError as e:
        return _err(400, "invalid_parameters", str(e))
    if not -90.0 <= lat_f <= 90.0:
        return _err(400, "invalid_parameters",
                    f"lat out of range (-90..90): {lat_f}")
    if not -180.0 <= lon_f <= 180.0:
        return _err(400, "invalid_parameters",
                    f"lon out of range (-180..180): {lon_f}")

    max_dist = 2000
    if max_distance_m is not None:
        try:
            max_dist = int(max_distance_m)
        except (TypeError, ValueError):
            return _err(400, "invalid_parameters",
                        f"max_distance_m must be an integer, "
                        f"got {max_distance_m!r}")
        if not 100 <= max_dist <= 20000:
            return _err(400, "invalid_parameters",
                        f"max_distance_m must be 100..20000, got {max_dist}")

    now_utc = datetime.now(UTC)
    if at is None:
        at_dt = now_utc.astimezone(CHICAGO_TZ)
    else:
        raw = at.strip()
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        try:
            at_dt = datetime.fromisoformat(raw)
        except ValueError:
            return _err(400, "invalid_parameters",
                        f"at is not a parseable ISO-8601 timestamp: {at!r}")
        if at_dt.tzinfo is None:
            return _err(400, "invalid_parameters",
                        "at must be timezone-aware (include an offset or "
                        "end in Z); naive timestamps are rejected.")
        if at_dt > now_utc + timedelta(hours=24):
            return _err(400, "invalid_parameters",
                        "at must not be more than 24 h in the future.")

    # -- nearest camera ---------------------------------------------
    try:
        camera_id, distance_m = nearest_camera(lat_f, lon_f)
    except Exception as e:  # noqa: BLE001 - no usable camera rows
        return _err(503, "model_unavailable",
                    f"nearest-camera lookup failed: {e}")
    if distance_m > max_dist:
        return _err(404, "no_camera_nearby",
                    f"No camera within {max_dist} m; nearest camera "
                    f"{camera_id} is {distance_m:.1f} m away.",
                    nearest_distance_m=round(float(distance_m), 1))

    # -- one live-signal fetch, passed into the adjustment ----------
    at_chicago = at_dt.astimezone(CHICAGO_TZ)
    date_str = at_chicago.strftime("%Y-%m-%d")
    hour = at_chicago.hour
    signals = collect_live_signals()
    fetch_now = datetime.now(UTC)
    try:
        adj = adjust_current_hour(
            camera_id, date_str, live_signals=signals,
            current_hour=hour, now=fetch_now)
    except Exception as e:  # noqa: BLE001 - total failure to predict
        return _err(503, "model_unavailable",
                    f"Could not produce a daily prediction for camera "
                    f"{camera_id} on {date_str}: {e}")

    body = _build_response(camera_id=camera_id, distance_m=distance_m,
                           at_dt=at_dt, signals=signals, adj=adj,
                           serve_now=datetime.now(UTC))
    return JSONResponse(status_code=200, content=body)
