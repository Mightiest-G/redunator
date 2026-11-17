#!/usr/bin/env python3
"""Redunator Phase 2 — LIVE ADJUSTMENT layer (Worker C).

Adjusts the CURRENT hour of a `predict_hourly` camera-day using the
live signals from `live_signals.py` (Open-Meteo current + forecast,
Divvy GBFS, and the key-ready TomTom / HERE / CTA providers).

The adjustment
---------------
The shipped hourly layer already contains weather: the profile path
applies `hourly_model.weather_multiplier` from the Open-Meteo
*archive* actuals, and the B12 Poisson model takes precipitation /
snowfall / temperature / wind as features. A live adjustment can
therefore only add information as a DELTA between live conditions
and the conditions the baseline already assumed for that hour::

    factor[h] = weather_multiplier(live precip/snow/wind at h)
                / weather_multiplier(expected at h)

where "expected" is the weather multiplier already embedded in the
baseline for hour h. The current hour's value is multiplied by
``factor`` and the 24 hours are renormalised to the same daily total
— the daily total is NEVER changed, exactly like the hourly layer.

What "expected" is depends on the date:

* **Live dates (after 2026-10-29, the end of the weather archive):**
  the baseline has no weather row at all, so its weather multiplier
  is 1.0 for every hour (and the model path is unavailable, so
  `predict_hourly` falls back to the profile path). Expected = 1.0
  and the live weather is genuinely new information. This is the
  production case the adjustment exists for.
* **Historical dates (inside the archive window):** the profile
  baseline already applied the archive weather multiplier for that
  hour, so expected = that multiplier and the factor is 1.0 by
  construction — the adjustment is a no-op, and says so. Applying
  the live multiplier a second time would double-count the same
  rain. On the **model path** there is no separable "expected
  multiplier" at all (the model's weather response is learned, not
  a multiplier), so the adjustment is not applied there either:
  layering the assumed multiplier on top of the model output is
  double-counting, and the backtest (`validate_live_adjust.py`,
  variant LIVE-B) measures that it loses. Pass
  ``expected_weather_multiplier`` explicitly to override the
  auto-detection (the backtest and the worked example do this to
  simulate the live-date situation on a historical date).

Divvy
-----
`divvy-gbfs` is collected but NEVER consumed by the adjustment.
Its `bike_occupancy` is a citywide bike-availability level — a weak
contextual activity signal, explicitly not a congestion proxy
(`live_signals.py`). No validated relationship between it and
hourly crash/violation risk could be shown: the GBFS feed is a
live-only snapshot with no historical archive, so a historical
occupancy series cannot be reconstructed from it, and the trip
data that does exist (taxi + TNP) already lost as a shape layer
on this same holdout (demand 0.0949 vs 0.0945 profile-only;
HOURLY_REPORT.md §4). It is reported under ``signals_ignored``
with the reason, never under ``signals_used``.

Staleness
---------
A signal is used only if `live_signals.is_stale` says it is fresh
at call time. Stale signals, errored signals (including the
key-ready providers failing with "API key required" when no key
is set) and missing signals are dropped, listed in
``signals_dropped_stale`` with fetched timestamps and reasons, and
the adjustment degrades to the unadjusted prediction — it never
crashes and never invents a value. If every weather signal is
stale, the factor is exactly 1.0 and the returned hourly values
are byte-identical to the unadjusted baseline (asserted in the
backtest, variant LIVE-C, and in `smoke_test_live_adjust.py`).

Live weather lookup
--------------------
For the current hour the precipitation comes from the
``open-meteo-forecast`` hourly list for that hour when available
(an hourly sum, the same definition as the archive's
``precipitation_mm``), falling back to ``open-meteo-current``
``precipitation_mm``. Wind comes from the current-weather values.
The live providers carry no snowfall amount, so the snow term is
0 in the live multiplier — a stated gap (snowfall hours get only
their precipitation part; see REALTIME_REPORT.md §6).

Status
------
EXPERIMENTAL, default OFF in the sense that no other module calls
it: `hourly_risk.predict_hourly` is untouched and `adjust_current_hour`
must be called explicitly. The backtest verdict (see
REALTIME_REPORT.md §4) is that the weather-delta adjustment does
NOT beat its baseline on the holdout — it is kept as a documented
experiment with exact numbers, not wired in as a default path.

Usage:
    from live_adjust import adjust_current_hour
    r = adjust_current_hour("1413", "2026-11-16")   # live signals fetched
    r["hourly"]           # 24 values, sum == r["daily_prediction"]
    r["signals_used"]     # [{"source", "fetched_at", "as_of"}, ...]
    r["signals_dropped_stale"]  # same shape + "reason"
    r["adjustment_factor"]      # 1.0 when nothing fresh / no delta

    from live_adjust import nearest_camera
    nearest_camera(41.8781, -87.6298)  # (camera_id, distance_m)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import hourly_model as hm
from hourly_risk import HOURLY_SCORE_SCALE, predict_hourly, risk_band
from live_signals import (
    CHICAGO_TZ,
    UTC,
    LiveSignal,
    collect_live_signals,
    is_stale,
)

BASE = Path(__file__).resolve().parent
SEGMENT_MAP_PATH = BASE / "data" / "hourly" / "processed" / "camera_segment_map.csv"

# The live adjustment can move the current hour by at most the
# weather multiplier's own clip range: weather_multiplier is clipped
# to [1.0, 1.25], so a delta factor lives in [1/1.25, 1.25].
FACTOR_MIN = 1.0 / hm.WX_MULT_MAX
FACTOR_MAX = hm.WX_MULT_MAX

DIVVY_EXCLUSION_REASON = (
    "bike_occupancy is a weak contextual activity signal, not a "
    "congestion proxy (live_signals.py); no validated relationship "
    "with hourly crash/violation risk could be shown — the GBFS feed "
    "is a live-only snapshot with no historical archive to validate "
    "against — so it is never consumed by the adjustment."
)

MODEL_PATH_REASON = (
    "base method is the Poisson model, which already ingests "
    "precipitation/snowfall/temperature/wind as features; there is "
    "no separable 'expected' weather multiplier to take a delta "
    "against, and layering the assumed weather multiplier on top "
    "would double-count the same weather (backtest variant LIVE-B: "
    "it loses). Adjustment not applied."
)

_cache: dict = {}


def weather_delta_factor(live_precip_mm: float | None,
                         live_snowfall_cm: float | None,
                         live_wind_kmh: float | None,
                         expected_multiplier: float = 1.0) -> float:
    """Delta factor for one hour: live weather multiplier / expected.

    ``expected_multiplier`` is the weather multiplier the baseline
    already embedded for that hour (1.0 when the baseline had no
    weather input — the live-date case). The factor is clipped to
    [1/1.25, 1.25], the weather multiplier's own clip range.
    Missing live values are treated as dry/calm (0.0).
    """
    live_mult = hm.weather_multiplier(
        float(live_precip_mm or 0.0),
        float(live_snowfall_cm or 0.0),
        float(live_wind_kmh or 0.0))
    expected = float(expected_multiplier) if expected_multiplier else 1.0
    if expected <= 0:
        expected = 1.0
    return float(min(max(live_mult / expected, FACTOR_MIN), FACTOR_MAX))


def apply_hour_adjustment(hourly, hour: int, factor: float):
    """Multiply one hour by ``factor`` and renormalise to the same total.

    The 24 values sum to exactly the input sum afterwards (float
    residual placed on the peak hour, mirroring `predict_hourly`).
    A factor of 1.0 returns the input unchanged.
    """
    hourly = np.asarray(hourly, dtype=float).copy()
    if len(hourly) != 24:
        raise ValueError(f"hourly must have 24 values, got {len(hourly)}")
    if not 0 <= int(hour) <= 23:
        raise ValueError(f"hour must be 0-23, got {hour}")
    total = float(hourly.sum())
    if total <= 0 or factor == 1.0:
        return hourly
    hourly[int(hour)] *= float(factor)
    hourly = np.clip(hourly, 0, None)
    new_total = float(hourly.sum())
    if new_total > 0:
        hourly = hourly / new_total * total
        resid = total - float(hourly.sum())
        if resid != 0.0:
            hourly[int(np.argmax(hourly))] += resid
    return hourly


def _live_weather_for_hour(signals: dict[str, LiveSignal],
                           hour_chicago: datetime) -> dict:
    """Live (precip_mm, snowfall_cm, wind_kmh) for one Chicago hour.

    Precipitation prefers the ``open-meteo-forecast`` hourly sum for
    that hour (same definition as the archive's precipitation_mm);
    falls back to ``open-meteo-current`` precipitation. Wind comes
    from the current-weather values. Snowfall is not carried by
    either live provider, so it is None (treated as 0 by the
    multiplier) — see the module docstring.
    Returns {"precipitation_mm", "snowfall_cm", "wind_speed_kmh",
    "precip_source"} with None values when no fresh signal has it.
    """
    precip = None
    precip_source = None
    wind = None
    fc = signals.get("open-meteo-forecast")
    if fc is not None and not fc.stale and fc.error is None:
        hours = fc.values.get("hours") or []
        precips = fc.values.get("precipitation_mm") or []
        for i, t in enumerate(hours):
            try:
                tt = datetime.fromisoformat(t).replace(tzinfo=CHICAGO_TZ)
            except ValueError:
                continue
            if tt.replace(minute=0, second=0, microsecond=0) == \
                    hour_chicago.replace(minute=0, second=0, microsecond=0):
                if i < len(precips) and precips[i] is not None:
                    precip = float(precips[i])
                    precip_source = "open-meteo-forecast"
                break
    cur = signals.get("open-meteo-current")
    if cur is not None and not cur.stale and cur.error is None:
        if precip is None and cur.values.get("precipitation_mm") is not None:
            precip = float(cur.values["precipitation_mm"])
            precip_source = "open-meteo-current"
        if cur.values.get("wind_speed_kmh") is not None:
            wind = float(cur.values["wind_speed_kmh"])
    return {
        "precipitation_mm": precip,
        "snowfall_cm": None,  # not carried by either live provider
        "wind_speed_kmh": wind,
        "precip_source": precip_source,
    }


def _expected_weather_multiplier(camera_id: str, date,
                                 hour: int) -> tuple[float, str]:
    """Weather multiplier the baseline already embedded for (date, hour).

    From the Open-Meteo archive row when one exists for that hour
    (the profile path's multiplier); 1.0 when no archive row exists
    (live dates after 2026-10-29 — the baseline had no weather
    input). Returns (multiplier, provenance string).
    """
    try:
        w = hm._load_weather()
    except Exception:
        return 1.0, "no weather archive available"
    ts = pd.Timestamp(date) + pd.Timedelta(hours=int(hour))
    if ts in w.index:
        r = w.loc[ts]
        return float(hm.weather_multiplier(
            float(r["precipitation_mm"]), float(r["snowfall_cm"]),
            float(r["wind_speed_10m_kmh"]))), "archive weather row"
    return 1.0, "no archive weather row for this date (live date)"


def _partition_signals(signals: dict[str, LiveSignal],
                       now: datetime) -> tuple[list, list, list]:
    """Split collected signals into used / dropped-stale / ignored.

    "Used" here means the weather providers whose values the
    adjustment can consume (open-meteo-current, open-meteo-forecast);
    freshness is re-checked at ``now`` with `is_stale`. Divvy is
    fresh-or-not but never consumed (see module docstring) and is
    reported under "ignored". Key-ready providers without keys
    surface as error results and land in "dropped" with their
    error as the reason.
    Returns (used, dropped, ignored), each a list of dicts with
    source + fetched_at (+ as_of / reason).
    """
    used, dropped, ignored = [], [], []
    for name, sig in signals.items():
        entry = {
            "source": sig.source,
            "fetched_at": sig.fetched_at.isoformat(),
            "as_of": sig.as_of.isoformat(),
        }
        stale_now = sig.stale or is_stale(sig, now)
        if name == "divvy-gbfs":
            entry["reason"] = DIVVY_EXCLUSION_REASON
            entry["stale"] = bool(stale_now)
            ignored.append(entry)
        elif name in ("open-meteo-current", "open-meteo-forecast"):
            if stale_now:
                entry["reason"] = (sig.error or
                                   f"stale: older than max age "
                                   f"{sig.max_age_seconds}s")
                dropped.append(entry)
            else:
                used.append(entry)
        else:
            # Key-ready providers (tomtom-flow, here-flow, cta-bus)
            # and anything unknown: not consumed by this adjustment.
            entry["reason"] = (
                sig.error or
                "not consumed by the weather-delta adjustment")
            if stale_now or sig.error is not None:
                dropped.append(entry)
            else:
                entry["reason"] = (
                    "fresh but not consumed by the weather-delta "
                    "adjustment (no validated use)")
                ignored.append(entry)
    return used, dropped, ignored


def adjust_current_hour(camera_id: str, date, live_signals=None,
                        current_hour: int | None = None,
                        now: datetime | None = None,
                        expected_weather_multiplier: float | None = None,
                        method: str = "auto", demand: bool = False,
                        slowness: bool = True,
                        banding: str = "hourly") -> dict:
    """Adjust the current hour of one camera-day with live weather.

    ``live_signals``: a dict from `live_signals.collect_live_signals`
    (fetched live when None). ``current_hour``: Chicago hour 0-23 to
    adjust; defaults to the current Chicago hour when ``date`` is
    today, and is required for any other date. ``now``: reference
    time (UTC-aware; defaults to now) used for staleness checks and
    for resolving "today". ``expected_weather_multiplier``: override
    the auto-detected expected multiplier (see module docstring).
    ``method`` / ``demand`` / ``slowness`` / ``banding`` are passed
    through to `hourly_risk.predict_hourly`.

    Never changes the daily total; never uses a stale signal; never
    raises because a provider failed — with no fresh weather signal
    the output is exactly the unadjusted baseline.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    date_ts = pd.Timestamp(date)
    today_chicago = now.astimezone(CHICAGO_TZ).date()
    if current_hour is None:
        if date_ts.date() == today_chicago:
            current_hour = now.astimezone(CHICAGO_TZ).hour
        else:
            raise ValueError(
                "current_hour is required when date is not today "
                f"(date={date_ts.date()}, today={today_chicago}); pass "
                "the Chicago hour 0-23 to adjust.")
    current_hour = int(current_hour)
    if not 0 <= current_hour <= 23:
        raise ValueError(f"current_hour must be 0-23, got {current_hour}")

    base = predict_hourly(camera_id, date, method=method, demand=demand,
                          slowness=slowness, banding=banding)
    hourly_unadjusted = list(base["hourly"])

    if live_signals is None:
        live_signals = collect_live_signals()
    used, dropped, ignored = _partition_signals(live_signals, now)

    # The hour being adjusted is the requested current_hour on the
    # requested date, not necessarily "now" — for a historical
    # worked example the live-weather lookup keys off that hour.
    target_hour = (pd.Timestamp(date) + pd.Timedelta(hours=current_hour))
    target_hour_chicago = target_hour.tz_localize(CHICAGO_TZ) \
        if target_hour.tzinfo is None else target_hour.tz_convert(CHICAGO_TZ)
    target_hour_chicago = target_hour_chicago.to_pydatetime().replace(
        minute=0, second=0, microsecond=0)
    # When the caller did not pin an hour via a historical date, the
    # forecast/current lookup is for "now"'s hour; the two agree
    # whenever date is today.
    lookup_hour = target_hour_chicago

    notes: list[str] = []
    adjustment_applied = False
    factor = 1.0
    live_wx = {"precipitation_mm": None, "snowfall_cm": None,
               "wind_speed_kmh": None, "precip_source": None}
    expected_mult = 1.0
    expected_prov = "not evaluated"

    is_model_path = str(base["method"]).startswith("model")
    if is_model_path:
        notes.append(MODEL_PATH_REASON)
        expected_prov = ("model path: no separable expected multiplier")
    else:
        if expected_weather_multiplier is not None:
            expected_mult = float(expected_weather_multiplier)
            expected_prov = "caller-supplied override"
        else:
            expected_mult, expected_prov = _expected_weather_multiplier(
                str(camera_id), date_ts, current_hour)
        live_wx = _live_weather_for_hour(live_signals, lookup_hour)
        if live_wx["precipitation_mm"] is None \
                and live_wx["wind_speed_kmh"] is None:
            notes.append(
                "no fresh live weather value for the current hour "
                "(both Open-Meteo signals stale, errored, or missing "
                "the hour) — adjustment degrades to the unadjusted "
                "baseline.")
        else:
            factor = weather_delta_factor(
                live_wx["precipitation_mm"], live_wx["snowfall_cm"],
                live_wx["wind_speed_kmh"], expected_mult)
            if factor == 1.0:
                notes.append(
                    "live weather multiplier equals the expected "
                    "multiplier for this hour (factor 1.0) — no new "
                    "information; baseline unchanged.")
            else:
                adjustment_applied = True

    hourly = apply_hour_adjustment(hourly_unadjusted, current_hour, factor) \
        if adjustment_applied else list(hourly_unadjusted)
    hourly = [float(v) for v in hourly]
    live_mult = hm.weather_multiplier(
        float(live_wx["precipitation_mm"] or 0.0),
        float(live_wx["snowfall_cm"] or 0.0),
        float(live_wx["wind_speed_kmh"] or 0.0))

    return {
        "camera_id": str(camera_id),
        "date": date_ts.strftime("%Y-%m-%d"),
        "current_hour": current_hour,
        "daily_prediction": float(base["daily_prediction"]),
        "daily_source": base["daily_source"],
        "hourly": hourly,
        "hourly_unadjusted": [float(v) for v in hourly_unadjusted],
        "bands": [risk_band(float(v), banding) for v in hourly],
        "banding": banding,
        "risk_scores": [round(min(float(v) / HOURLY_SCORE_SCALE, 1.0), 4)
                        for v in hourly],
        "method": base["method"] + ("+live" if adjustment_applied
                                    else ""),
        "base_method": base["method"],
        "fallback_note": base.get("fallback_note"),
        "demand": bool(base["demand"]),
        "slowness_applied": bool(base["slowness_applied"]),
        "adjustment_applied": bool(adjustment_applied),
        "adjustment_factor": float(factor),
        "live_weather_multiplier": float(live_mult),
        "expected_weather_multiplier": float(expected_mult),
        "expected_weather_provenance": expected_prov,
        "live_weather": live_wx,
        "signals_used": used,
        "signals_dropped_stale": dropped,
        "signals_ignored": ignored,
        "notes": notes,
        "estimate": True,
        "hourly_sum": float(sum(hourly)),
        "reconciliation_error": float(sum(hourly)
                                      - float(base["daily_prediction"])),
    }


def nearest_camera(lat: float, lon: float):
    """Nearest red-light camera to (lat, lon), by haversine distance.

    Coordinates from `camera_segment_map.csv` (361 cameras; the 4
    coordinate-less cameras 1623, 2552, 2592, 2662 have no row
    position and can never be nearest). Returns
    (camera_id, distance_m). Raises ValueError on no usable rows.
    Phase 3 contract helper — see LIVE_CONTRACT.md.
    """
    if "segmap" not in _cache:
        m = pd.read_csv(SEGMENT_MAP_PATH)
        m["camera_id"] = m["camera_id"].astype(str)
        _cache["segmap"] = m
    m = _cache["segmap"].dropna(subset=["latitude", "longitude"])
    if len(m) == 0:
        raise ValueError("no cameras with coordinates in segment map")
    lat_r = np.radians(float(lat))
    lon_r = np.radians(float(lon))
    lat2 = np.radians(m["latitude"].to_numpy(dtype=float))
    lon2 = np.radians(m["longitude"].to_numpy(dtype=float))
    dlat = lat2 - lat_r
    dlon = lon2 - lon_r
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat_r) * np.cos(lat2) \
        * np.sin(dlon / 2.0) ** 2
    dist_m = 2.0 * 6371000.0 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    i = int(np.argmin(dist_m))
    return str(m.iloc[i]["camera_id"]), float(dist_m[i])


if __name__ == "__main__":
    import json
    import sys

    cam = sys.argv[1] if len(sys.argv) > 1 else "1413"
    date = sys.argv[2] if len(sys.argv) > 2 else "2026-11-16"
    hour = int(sys.argv[3]) if len(sys.argv) > 3 else None
    r = adjust_current_hour(cam, date, current_hour=hour)
    print(json.dumps(r, indent=2))
    print(f"current hour {r['current_hour']}:00: "
          f"{r['hourly_unadjusted'][r['current_hour']]:.3f} -> "
          f"{r['hourly'][r['current_hour']]:.3f} "
          f"(factor {r['adjustment_factor']:.3f}, "
          f"band {r['bands'][r['current_hour']]})")
