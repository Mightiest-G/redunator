#!/usr/bin/env python3
"""Redunator Phase 2b — hourly profiles, multipliers, and the optional
Poisson hourly model.

Everything in this module is an ESTIMATE built on proxy signals. The daily
red-light violation data has no hour-of-day, so no hourly violation count
can ever be *measured* here — the hourly shape comes from *crashes* near
each camera (a proxy for traffic exposure) and *congestion speeds* near
each camera (a proxy for traffic volume). The daily total always comes
from the shipped Phase 2 daily model (`redunator_brain_v1`); the hourly
layer only splits that total across the 24 hours.

Headline constraints (from worker A's `data/hourly/SOURCES_VERIFIED.md`):
  * Congestion feed is DEAD: last row 2026-04-30. It covers
    2025-01-01 -> 2026-04-30 ONLY and CANNOT cover the Phase 2 holdout
    window (2026-08-01 -> 2026-10-29). Congestion shapes the *profiles*
    (stable patterns learned in the training period) and supplies an
    hour-specific multiplier ONLY for timestamps <= 2026-04-30. For later
    dates the congestion multiplier is 1.0 — it is never forward-filled
    across the 3-month gap.
  * Crashes and weather DO cover the full window (2025-01-01 ->
    2026-10-29).
  * Timestamps are naive hourly labels in a flat 24/day grid; the 02:00
    row on spring-forward days is not a real local hour. All three
    sources share the grid, so they join cleanly on `timestamp`.
  * 4 cameras have no coordinates and cannot be mapped or crash-counted:
    1623, 2552, 2592, 2662. They get the citywide profile.

Profile construction (see `build_profiles`):
  * Citywide hourly share p_city[h]: citywide crash counts by hour.
  * Local crash share, Dirichlet-smoothed toward the citywide share:
        crash_local[h] = (c[h] + K * p_city[h]) / (n + K),  K = 30
    where c[h] is the camera's attributed crashes in hour h and
    n = sum(c). Thin-data rule: n < 50 is flagged `thin_local_data`;
    with K = 30 a camera with n = 27 (the minimum observed) still gets
    ~47% weight on its own counts, a camera with n = 158 (the median)
    gets ~84%. Cameras with n = 0 fall back to the citywide profile
    exactly (source = "citywide").
  * Congestion share: for the camera's segment, hourly mean speeds
    s[h]; free-flow ff = 90th percentile of s[h]; slowness
    g[h] = max(0, (ff - s[h]) / ff); cong_share[h] =
    (g[h] + 0.02) / sum(g + 0.02). The 0.02 floor keeps every hour
    non-zero and stops a flat speed profile from producing a degenerate
    share. Speed varies only modestly by hour (citywide mean 23.0 mph
    at 16:00 vs 26.0 at 02:00), so congestion is a *weak* shape signal
    and gets a small blend weight on purpose.
  * Blend: profile = (1 - w_cong) * crash_local + w_cong * cong_share,
    renormalised to sum to exactly 1.
        w_cong = 0.25  if segment covers >= 50% of possible hours AND
                        distance <= 500 m AND not remapped to a live
                        segment;
               = 0.10  if remapped OR distance > 500 m OR coverage
                        25-50%;
               = 0.00  if coverage < 25% or no segment.
    (Distance is camera -> segment distance from `camera_segment_map.csv`.)

Multipliers (see `weather_multiplier`, `congestion_multiplier`):
  * Weather: 1 + 0.10 if any precipitation + min(0.05, 0.01 * precip_mm)
    + min(0.10, 0.06 * snowfall_cm) + min(0.03, 0.002 * max(0, wind-30)),
    clipped to [1.0, 1.25]. These coefficients are ASSUMED planning
    values, NOT measured effects on violations (violations have no
    hourly resolution, so the effect cannot be estimated). They are
    calibrated to the observed within-hour wet-vs-dry crash ratio
    (~1.15 on average across hours; see HOURLY_MODEL_SUMMARY.md):
    a rainy hour gets ~1.10-1.15, a snowy hour up to ~1.21.
  * Congestion (hour-specific, only when an observed speed exists for
    that camera-hour): 1 + (typical_speed[h] - observed_speed) /
    typical_speed[h], clipped to [0.80, 1.25]. Coefficient 1.0 is an
    ASSUMED planning value. Missing speed (including every timestamp
    after 2026-04-30) -> 1.0.
  * Multipliers SHAPE the day only: `hourly_risk.py` renormalises so
    the 24 hourly values always sum to the daily prediction. The daily
    total is the anchor; the hourly layer never changes it.

Poisson model (see `train_poisson_model`):
  * Target = HOURLY CRASH COUNTS near cameras (proxy target — labelled
    as such everywhere). Features: hour/dow cyclical, speed ratio
    (observed speed / segment free-flow), weather (precip/snow/temp/
    wind), log camera daily crash rate, log trailing-7-day crash rate.
  * Train 2025-01-01 -> 2026-02-28, test 2026-03-01 -> 2026-04-30
    (time-based, inside the congestion window). Baseline = train-period
    profile weights x camera daily crash rate (profiles rebuilt on the
    train window only, so the comparison has no leakage from test
    crashes). Metric: mean Poisson deviance (primary), RMSE/MAE
    secondary. Two challengers: sklearn PoissonRegressor (alphas 0.1 /
    1.0 / 10.0) and XGBoost count:poisson (300 rounds, depth 6 — with
    `--xgb-poisson`). The best model is saved ONLY if it beats the
    baseline on deviance without losing on RMSE/MAE; otherwise the
    profile version ships and the numbers say so.

Phase 2c — trip-demand layer (see `build_demand.py`, DEMAND_BUILD.md):
  * Demand weights: per community area, 24-hour trip-volume shares
    from summed taxi + rideshare (TNP) pickup counts, 2023-01-01 ->
    present, split by day type (weekday / weekend / holiday; holiday
    takes precedence, same holidays.US-observed convention as
    features.py). Cameras map to areas by point-in-polygon on the
    igwz-8jzy boundaries (361/365 mapped; the 4 coordinate-less
    cameras fall back to the citywide profile, area 0).
  * Demand multiplier: demand_weight[h] / mean(demand_weight), mean
    exactly 1.0 — it only reshapes the day. Applied as an extra
    multiplier in `hourly_risk.py` on BOTH the profile and model
    paths (the Poisson model has no demand feature — adding one would
    need a retrain and re-validation, which is Worker C's call — so
    demand is a post-hoc shape layer there, not a model feature).
    The daily total is still the anchor: hourly_risk.py renormalises
    so the 24 hours sum to the daily prediction exactly.

Phase 2d — trip-speed (slowness) layer + retrain (see HOURLY_REPORT.md
§9, `validate_hourly_2d.py`, `train_hourly_2d.py`):
  * `load_speed_profiles` / `slowness_weights` /
    `slowness_multipliers_for_day`: per-(area, day type, hour) mean
    trip speed from `speed_profiles.parquet` (valid taxi + TNP trips
    2023-01-01 -> 2026-07-31, pre-holdout); slowness = 1/speed,
    multiplier = slowness / mean(slowness). Validated as a post-hoc
    shape layer on the PROFILE path only (it beats profile-only on
    the holdout; it hurts the model path, so hourly_risk.py never
    applies it there).
  * Shipped hourly model (`hourly_model.joblib`) is the B12 retrain:
    the same 12 features as worker B, trained on the FULL pre-holdout
    window (2025-01-01 -> 2026-07-31) instead of worker B's
    2025-01-01 -> 2026-02-28 split. Holdout shape deviance 0.093260
    vs 0.093335 for the worker-B model. The demand_share /
    trip_slowness features tried in 2d did NOT help (same-split
    retrain loses; full-window retrain without them beats the
    full-window retrain with them) — the B12 win is the longer
    training window, not the new features. Worker-B model preserved
    as `hourly_model_v2b.joblib`. `model_weights()` uses
    SHIPPED_MODEL_RATE_END ("2026-07-31") for log_cam_rate.

Usage:
    python hourly_model.py --build-profiles   # writes hourly_profiles.parquet
    python hourly_model.py --train-poisson    # trains + evaluates, saves only if it wins
    python hourly_model.py --all             # both
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
HOURLY = BASE / "data" / "hourly"
PROCESSED = HOURLY / "processed"

CRASHES_PER_CAMERA = PROCESSED / "crashes_hourly_per_camera.parquet"
CRASHES_CITYWIDE = PROCESSED / "crashes_hourly_citywide.parquet"
CONGESTION = PROCESSED / "congestion_hourly.parquet"
WEATHER = PROCESSED / "weather_hourly.parquet"
SEGMENT_MAP = PROCESSED / "camera_segment_map.csv"
PROFILES_PATH = PROCESSED / "hourly_profiles.parquet"
POISSON_MODEL_PATH = BASE / "hourly_model.joblib"
POISSON_METRICS_PATH = BASE / "hourly_model_metrics.json"
DEMAND_PROFILES_PATH = PROCESSED / "demand_profiles.parquet"
SPEED_PROFILES_PATH = PROCESSED / "speed_profiles.parquet"
CAMERA_AREA_PATH = PROCESSED / "camera_community_area.csv"
CITYWIDE_AREA = 0  # area code for the citywide demand profile (fallback)

# --- profile constants ------------------------------------------------------
DIRICHLET_K = 30          # prior strength (in crashes) toward citywide share
THIN_N = 50               # n_crashes below this -> thin_local_data flag
CONG_FLOOR = 0.02         # floor on slowness when building the congestion share
W_CONG_FULL = 0.25        # blend weight, good congestion coverage
W_CONG_LOW = 0.10         # blend weight, weak/remapped congestion coverage
CONG_COVER_FULL = 0.50    # segment hourly coverage for the full weight
CONG_COVER_MIN = 0.25     # below this, congestion is unusable
DIST_TRUST_M = 500.0      # camera-segment distance above this -> low trust

# --- weather multiplier constants (ASSUMED planning values; see docstring) --
WX_RAIN_BASE = 0.10       # any precipitation at all
WX_RAIN_PER_MM = 0.01     # per mm, capped by WX_RAIN_CAP
WX_RAIN_CAP = 0.05
WX_SNOW_PER_CM = 0.06     # per cm, capped by WX_SNOW_CAP
WX_SNOW_CAP = 0.10
WX_WIND_PER_KMH = 0.002   # per km/h above 30, capped by WX_WIND_CAP
WX_WIND_THRESHOLD = 30.0
WX_WIND_CAP = 0.03
WX_MULT_MAX = 1.25

# --- congestion multiplier constants (ASSUMED planning value) ---------------
CONG_MULT_MIN = 0.80
CONG_MULT_MAX = 1.25

# --- Poisson model split (inside the congestion window) ---------------------
POISSON_TRAIN_END = "2026-02-28"
POISSON_TEST_START = "2026-03-01"
POISSON_TEST_END = "2026-04-30"
# Phase 2d: the SHIPPED hourly model (hourly_model.joblib) is the B12
# retrain — the same 12 features, trained 2025-01-01 -> 2026-07-31, so
# its log_cam_rate window ends 2026-07-31, not POISSON_TRAIN_END.
# model_weights() must use this window to match the shipped model.
# The worker-B model (train through 2026-02-28) is preserved as
# hourly_model_v2b.joblib. See HOURLY_REPORT.md §9 and
# hourly_model_2d_metrics.json.
SHIPPED_MODEL_RATE_END = "2026-07-31"
CONGESTION_END = pd.Timestamp("2026-04-30 01:00")

HOURS = np.arange(24)


# --- loading helpers (cached) ----------------------------------------------
_cache: dict = {}


def _load_crashes_per_camera() -> pd.DataFrame:
    if "crashes_pc" not in _cache:
        df = pd.read_parquet(CRASHES_PER_CAMERA)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["hour"] = df["timestamp"].dt.hour.astype("int8")
        _cache["crashes_pc"] = df
    return _cache["crashes_pc"]


def _load_crashes_citywide() -> pd.DataFrame:
    if "crashes_cw" not in _cache:
        df = pd.read_parquet(CRASHES_CITYWIDE)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["hour"] = df["timestamp"].dt.hour.astype("int8")
        _cache["crashes_cw"] = df
    return _cache["crashes_cw"]


def _load_congestion() -> pd.DataFrame:
    if "cong" not in _cache:
        df = pd.read_parquet(CONGESTION)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        _cache["cong"] = df
    return _cache["cong"]


def _load_weather() -> pd.DataFrame:
    if "weather" not in _cache:
        df = pd.read_parquet(WEATHER)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        _cache["weather"] = df.set_index("timestamp").sort_index()
    return _cache["weather"]


def _load_segment_map() -> pd.DataFrame:
    if "segmap" not in _cache:
        m = pd.read_csv(SEGMENT_MAP)
        m["camera_id"] = m["camera_id"].astype(str)
        _cache["segmap"] = m
    return _cache["segmap"]


def load_profiles() -> pd.DataFrame:
    """Load the shipped profiles parquet (camera_id str, hour, weight)."""
    if "profiles" not in _cache:
        df = pd.read_parquet(PROFILES_PATH)
        df["camera_id"] = df["camera_id"].astype(str)
        _cache["profiles"] = df
    return _cache["profiles"]


def load_demand_profiles() -> pd.DataFrame:
    """Load the Phase 2c demand profiles (area, day_type, hour, weight).

    Built by `build_demand.py` from summed taxi + rideshare (TNP)
    pickup counts, 2023-01-01 -> present, split by day type
    (weekday / weekend / holiday). Area 0 is the citywide profile.
    """
    if "demand_profiles" not in _cache:
        df = pd.read_parquet(DEMAND_PROFILES_PATH)
        _cache["demand_profiles"] = df
    return _cache["demand_profiles"]


def load_speed_profiles() -> pd.DataFrame:
    """Load the Phase 2d trip-speed profiles (area, day_type, hour,
    mean_speed_mph). Built by Worker 1 from valid taxi + TNP trips
    2023-01-01 -> 2026-07-31 (PRE-HOLDOUT); area 0 is citywide.
    5,616 rows, 0 NaNs. See data/hourly/SPEED_FINDINGS.md."""
    if "speed_profiles" not in _cache:
        df = pd.read_parquet(SPEED_PROFILES_PATH)
        _cache["speed_profiles"] = df
    return _cache["speed_profiles"]


def slowness_weights(camera_id: str, date) -> np.ndarray:
    """24-vector of trip-slowness shares for a camera-date (sums to 1).

    slow[h] = 1 / mean_speed_mph for the camera's community area and
    the date's day type; share = slow / sum(slow). Citywide profile
    for unmapped / unknown cameras. A slowness share is the share of
    the area's slowness in that hour — a proxy for congestion
    exposure, NOT a measured violation rate. Phase 2d validation:
    citywide slowness-vs-crash share r = 0.949 (holdout day-type
    mix), per-area median r = 0.693.
    """
    df = load_speed_profiles()
    area = camera_community_area(camera_id)
    if area is None:
        area = CITYWIDE_AREA
    dt = day_type_for_date(date)
    sub = df[(df["area"] == area) & (df["day_type"] == dt)].sort_values("hour")
    if len(sub) != 24:  # pragma: no cover - profiles cover all areas
        sub = df[(df["area"] == CITYWIDE_AREA)
                 & (df["day_type"] == dt)].sort_values("hour")
    slow = 1.0 / sub["mean_speed_mph"].to_numpy(dtype=float)
    return slow / slow.sum()


def slowness_multipliers_for_day(camera_id: str, date,
                                alpha: float = 1.0) -> np.ndarray:
    """24 slowness multipliers for a camera-date (ESTIMATE).

    (slow[h] / mean(slow)) ** alpha, mean ~1.0 at alpha=1, so the
    multiplier only reshapes the day — it never changes the daily
    total (hourly_risk.py renormalises on top of this). Range at
    alpha=1 is roughly 0.5x in the small hours to ~1.7x at the
    evening trough of speed; alpha=0.5 is the half-strength blend.
    """
    w = slowness_weights(camera_id, date)
    mult = w / w.mean()
    return mult ** alpha


def load_camera_area_map() -> pd.DataFrame:
    """Load the Phase 2c camera -> community-area map (see build_demand.py)."""
    if "camera_area" not in _cache:
        m = pd.read_csv(CAMERA_AREA_PATH)
        m["camera_id"] = m["camera_id"].astype(str)
        _cache["camera_area"] = m
    return _cache["camera_area"]


def camera_community_area(camera_id: str) -> int | None:
    """Community area (1-77) for a camera, or None if unmapped.

    Unmapped = the 4 cameras with no coordinates (1623, 2552, 2592,
    2662). Callers fall back to the citywide demand profile.
    """
    m = load_camera_area_map()
    row = m[m["camera_id"] == str(camera_id)]
    if len(row) == 0 or pd.isna(row.iloc[0]["community_area"]):
        return None
    return int(row.iloc[0]["community_area"])


def day_type_for_date(date) -> str:
    """weekday / weekend / holiday for a date (ESTIMATE input).

    Holiday takes precedence over weekday/weekend. Same holiday
    convention as features.py: holidays.US federal holidays
    INCLUDING federally observed dates.
    """
    import holidays

    d = pd.Timestamp(date)
    if "us_holidays" not in _cache:
        _cache["us_holidays"] = holidays.US(years=range(2019, 2027))
    if d.date() in _cache["us_holidays"]:
        return "holiday"
    return "weekend" if d.day_of_week >= 5 else "weekday"


def demand_weights(camera_id: str, date) -> np.ndarray:
    """24-vector of trip-demand weights for a camera-date (sums to 1).

    The camera's community-area profile for the date's day type;
    citywide profile for unmapped / unknown cameras. A demand weight
    is the share of that area's trips in that hour — a proxy for
    traffic exposure, NOT a measured violation rate.
    """
    df = load_demand_profiles()
    area = camera_community_area(camera_id)
    if area is None:
        area = CITYWIDE_AREA
    dt = day_type_for_date(date)
    sub = df[(df["area"] == area) & (df["day_type"] == dt)].sort_values("hour")
    if len(sub) != 24:  # pragma: no cover - profiles cover all areas
        sub = df[(df["area"] == CITYWIDE_AREA)
                 & (df["day_type"] == dt)].sort_values("hour")
    w = sub["weight"].to_numpy(dtype=float)
    return w / w.sum()


def demand_multipliers_for_day(camera_id: str, date) -> np.ndarray:
    """24 demand multipliers for a camera-date (ESTIMATE).

    demand_weight[h] / mean(demand_weight): mean exactly 1.0, so the
    multiplier only reshapes the day — it never changes the daily
    total (hourly_risk.py renormalises on top of this). Range is
    roughly 0.2x in the small hours to ~1.7x at the evening peak;
    exact range depends on the area and day type.
    """
    w = demand_weights(camera_id, date)
    return w / w.mean()


def profile_weights(camera_id: str) -> np.ndarray:
    """24-vector of profile weights for one camera (sums to 1).

    Falls back to the citywide share if the camera is not in the file.
    """
    df = load_profiles()
    sub = df[df["camera_id"] == str(camera_id)]
    if len(sub) == 0:
        # Unknown camera: citywide profile (the 4 unmappable cameras'
        # rows are exactly this, so this also covers them if the file
        # is ever rebuilt without them).
        cw = _load_crashes_citywide()
        city = cw.groupby("hour")["crash_count"].sum().reindex(HOURS, fill_value=0)
        return (city / city.sum()).to_numpy(dtype=float)
    sub = sub.sort_values("hour")
    w = sub["weight"].to_numpy(dtype=float)
    return w / w.sum()


# --- profiles ---------------------------------------------------------------
def build_profiles(crash_end: str | None = None,
                   cong_end: str | None = None) -> pd.DataFrame:
    """Build 24-hour profiles for every camera.

    `crash_end` / `cong_end` restrict the source windows (used to build
    leakage-free train-period profiles for the Poisson comparison).
    With no arguments the full windows are used (crashes through
    2026-10-29, congestion through 2026-04-30) — the shipped profiles.
    Returns a DataFrame with one row per (camera, hour).
    """
    pc = _load_crashes_per_camera()
    cw = _load_crashes_citywide()
    cg = _load_congestion()
    segmap = _load_segment_map()

    if crash_end is not None:
        end = pd.Timestamp(crash_end) + pd.Timedelta(days=1)
        pc = pc[pc["timestamp"] < end]
        cw = cw[cw["timestamp"] < end]
    if cong_end is not None:
        end = pd.Timestamp(cong_end) + pd.Timedelta(days=1)
        cg = cg[cg["timestamp"] < end]

    # Citywide hourly share from crash counts.
    city_counts = cw.groupby("hour")["crash_count"].sum().reindex(HOURS, fill_value=0)
    p_city = (city_counts / city_counts.sum()).to_numpy(dtype=float)

    # Local crash counts per camera-hour.
    local = (pc.groupby(["camera_id", "hour"])["crash_count"].sum()
             .unstack(fill_value=0).reindex(columns=HOURS, fill_value=0))
    # local: index camera_id (str), columns 0..23, values crash counts.
    n_per_cam = local.sum(axis=1)

    # Congestion: per-segment hourly mean speed + coverage.
    seg_hour_speed = (cg.groupby(["segment_id", "hour"])["mean_speed_mph"]
                      .mean().unstack(fill_value=np.nan))
    # Possible hourly slots in the congestion window actually used.
    cong_start = cg["timestamp"].min().floor("h")
    cong_last = cg["timestamp"].max().floor("h")
    n_possible = int((cong_last - cong_start) / pd.Timedelta(hours=1)) + 1
    seg_n_hours = cg.groupby("segment_id")["timestamp"].nunique()
    seg_coverage = (seg_n_hours / n_possible).to_dict()

    # Congestion share per segment: slowness vs free-flow (p90 of the
    # segment's hourly mean speeds), plus a floor, normalised.
    cong_share: dict = {}
    for seg_id, row in seg_hour_speed.iterrows():
        s = row.to_numpy(dtype=float)
        valid = ~np.isnan(s)
        if valid.sum() == 0:
            cong_share[seg_id] = None
            continue
        ff = np.nanpercentile(s[valid], 90)
        s_filled = np.where(valid, s, np.nanmean(s[valid]))
        g = np.maximum(0.0, (ff - s_filled) / ff)
        share = (g + CONG_FLOOR)
        cong_share[seg_id] = share / share.sum()

    rows = []
    for _, m in segmap.iterrows():
        cam = str(m["camera_id"])
        seg_id = m["segment_id"]
        dist = m["distance_to_segment_m"]
        remapped = bool(m["remapped_to_live_segment"])
        if cam in local.index:
            c = local.loc[cam].to_numpy(dtype=float)
            n = int(n_per_cam.loc[cam])
        else:
            c = np.zeros(24)
            n = 0
        crash_local = (c + DIRICHLET_K * p_city) / (n + DIRICHLET_K)

        # Congestion blend weight from coverage / distance / remap.
        if pd.isna(seg_id) or seg_id not in cong_share or cong_share[seg_id] is None:
            w_cong = 0.0
            coverage = 0.0
        else:
            coverage = float(seg_coverage.get(seg_id, 0.0))
            if coverage >= CONG_COVER_FULL and not remapped and (
                    pd.isna(dist) or dist <= DIST_TRUST_M):
                w_cong = W_CONG_FULL
            elif coverage >= CONG_COVER_MIN:
                w_cong = W_CONG_LOW
            else:
                w_cong = 0.0
        if w_cong > 0:
            cs = cong_share[seg_id]
            prof = (1.0 - w_cong) * crash_local + w_cong * cs
        else:
            prof = crash_local.copy()
        # Exact renormalisation: divide by the sum, then put the whole
        # floating-point residual on the largest weight so the stored
        # weights sum to 1.0 to within ~1e-16.
        prof = prof / prof.sum()
        prof[np.argmax(prof)] += 1.0 - prof.sum()

        source = "citywide" if n == 0 else "local"
        for h in range(24):
            rows.append({
                "camera_id": cam,
                "hour": h,
                "weight": float(prof[h]),
                "source": source,
                "n_crashes": n,
                "thin_local_data": bool(n < THIN_N),
                "congestion_weight": float(w_cong),
                "congestion_coverage": float(coverage),
                "distance_to_segment_m": (None if pd.isna(dist) else float(dist)),
                "remapped_to_live_segment": remapped,
                "segment_id": (None if pd.isna(seg_id) else int(seg_id)),
            })
    return pd.DataFrame(rows)


# --- multipliers ------------------------------------------------------------
def weather_multiplier(precip_mm: float, snowfall_cm: float,
                       wind_kmh: float) -> float:
    """Bounded weather multiplier for one hour (ESTIMATE).

    Coefficients are ASSUMED planning values, calibrated to the observed
    within-hour wet-vs-dry crash ratio (~1.15) — they describe crash
    risk, applied here as a proxy for violation risk. They are NOT
    measured effects on violations: violations are daily-only, so no
    hourly weather effect on violations can be estimated from the data.
    Dry/calm hours return exactly 1.0.
    """
    mult = 1.0
    if precip_mm and precip_mm > 0:
        mult += WX_RAIN_BASE + min(WX_RAIN_CAP, WX_RAIN_PER_MM * precip_mm)
    if snowfall_cm and snowfall_cm > 0:
        mult += min(WX_SNOW_CAP, WX_SNOW_PER_CM * snowfall_cm)
    if wind_kmh and wind_kmh > WX_WIND_THRESHOLD:
        mult += min(WX_WIND_CAP,
                    WX_WIND_PER_KMH * (wind_kmh - WX_WIND_THRESHOLD))
    return float(min(max(mult, 1.0), WX_MULT_MAX))


def weather_multipliers_for_day(date) -> np.ndarray:
    """24 weather multipliers for a date (ESTIMATE); 1.0 if no weather row."""
    date = pd.Timestamp(date)
    w = _load_weather()
    out = np.ones(24)
    for h in range(24):
        ts = date + pd.Timedelta(hours=h)
        if ts in w.index:
            r = w.loc[ts]
            out[h] = weather_multiplier(float(r["precipitation_mm"]),
                                        float(r["snowfall_cm"]),
                                        float(r["wind_speed_10m_kmh"]))
    return out


def congestion_multiplier(camera_id: str, timestamp) -> float:
    """Hour-specific congestion multiplier (ESTIMATE), 1.0 when unavailable.

    1 + (typical_speed[hour] - observed_speed) / typical_speed[hour],
    clipped to [0.80, 1.25]. `typical_speed` is the segment's mean speed
    at that hour-of-day over the congestion window. Returns 1.0 for any
    timestamp after the congestion feed ends (2026-04-30) or with no
    observed speed — never forward-filled.
    """
    ts = pd.Timestamp(timestamp)
    if ts > CONGESTION_END:
        return 1.0
    segmap = _load_segment_map()
    row = segmap[segmap["camera_id"] == str(camera_id)]
    if len(row) == 0 or pd.isna(row.iloc[0]["segment_id"]):
        return 1.0
    seg_id = row.iloc[0]["segment_id"]
    cg = _load_congestion()
    sub = cg[(cg["segment_id"] == seg_id) & (cg["timestamp"] == ts)]
    if len(sub) == 0:
        return 1.0
    obs = float(sub.iloc[0]["mean_speed_mph"])
    seg_hours = cg[cg["segment_id"] == seg_id]
    typ = seg_hours[seg_hours["hour"] == ts.hour]["mean_speed_mph"].mean()
    if pd.isna(typ) or typ <= 0:
        return 1.0
    return float(min(max(1.0 + (typ - obs) / typ, CONG_MULT_MIN), CONG_MULT_MAX))


def congestion_multipliers_for_day(camera_id: str, date) -> np.ndarray:
    """24 congestion multipliers for a camera-date (ESTIMATE).

    All 1.0 for dates after 2026-04-30 (dead feed — by design).
    """
    date = pd.Timestamp(date)
    out = np.ones(24)
    if date > pd.Timestamp("2026-04-30"):
        return out
    segmap = _load_segment_map()
    row = segmap[segmap["camera_id"] == str(camera_id)]
    if len(row) == 0 or pd.isna(row.iloc[0]["segment_id"]):
        return out
    seg_id = row.iloc[0]["segment_id"]
    cg = _load_congestion()
    seg = cg[cg["segment_id"] == seg_id]
    if len(seg) == 0:
        return out
    typ = seg.groupby("hour")["mean_speed_mph"].mean()
    day_rows = seg[(seg["timestamp"] >= date)
                   & (seg["timestamp"] < date + pd.Timedelta(days=1))]
    obs = day_rows.set_index(day_rows["timestamp"].dt.hour)["mean_speed_mph"]
    for h in range(24):
        if h in obs.index and h in typ.index and typ[h] > 0:
            out[h] = float(min(max(1.0 + (typ[h] - float(obs[h])) / typ[h],
                                   CONG_MULT_MIN), CONG_MULT_MAX))
    return out


# --- Poisson hourly model (proxy target: hourly crash counts) --------------
POISSON_FEATURES = [
    "hour_sin", "hour_cos", "dow_sin", "dow_cos", "is_weekend",
    "speed_ratio", "precip_mm", "snowfall_cm", "temp_c_10", "wind_kmh_10",
    "log_cam_rate", "log_trail7_rate",
]
_RATE_FLOOR = 0.01  # crashes/day floor before taking logs


def _poisson_matrices():
    """Build the camera-hour matrices for the Poisson fit.

    Grid: 2025-01-01 00:00 -> 2026-10-29 23:00 (the full crash/weather
    window), 361 cameras with coordinates. The congestion speed matrix
    only has values through 2026-04-30 (dead feed); later hours are
    NaN and fall back to the segment's typical speed. `build_poisson_
    design` slices the fit window (through 2026-04-30) out of these
    full-length matrices; the full crash history is kept so
    `model_weights` can score dates through 2026-10-29.
    """
    pc = _load_crashes_per_camera()
    cg = _load_congestion()
    w = _load_weather()
    segmap = _load_segment_map()

    times = pd.date_range("2025-01-01", "2026-10-29 23:00", freq="h")
    n_t = len(times)
    t_index = {t: i for i, t in enumerate(times)}
    days = pd.date_range("2025-01-01", "2026-10-29", freq="D")
    n_d = len(days)

    cams = sorted(pc["camera_id"].unique().tolist())  # 361, str
    cam_index = {c: i for i, c in enumerate(cams)}
    n_c = len(cams)

    # Crash counts per camera-hour (int16 matrix, mostly 0).
    crash_mat = np.zeros((n_c, n_t), dtype=np.int16)
    sub = pc[(pc["timestamp"] >= times[0]) & (pc["timestamp"] <= times[-1])]
    ci = sub["camera_id"].map(cam_index).to_numpy()
    ti = sub["timestamp"].map(t_index).to_numpy()
    vals = sub["crash_count"].to_numpy(dtype=np.int16)
    np.add.at(crash_mat, (ci, ti), vals)

    # Congestion speed per segment-hour (float32, NaN where unobserved).
    segs = sorted(cg["segment_id"].unique().tolist())
    seg_index = {s: i for i, s in enumerate(segs)}
    speed_mat = np.full((len(segs), n_t), np.nan, dtype=np.float32)
    cg_sub = cg[(cg["timestamp"] >= times[0]) & (cg["timestamp"] <= times[-1])]
    si = cg_sub["segment_id"].map(seg_index).to_numpy()
    ti2 = cg_sub["timestamp"].map(t_index).to_numpy()
    speed_mat[si, ti2] = cg_sub["mean_speed_mph"].to_numpy(dtype=np.float32)

    # Per-segment free-flow (p90 of hourly mean speeds) and typical
    # speed by hour-of-day (for filling missing hours).
    seg_hour_mean = (cg_sub.groupby(["segment_id", "hour"])["mean_speed_mph"]
                     .mean().unstack(fill_value=np.nan)
                     .reindex(index=segs, columns=HOURS).to_numpy(dtype=float))
    ff = np.nanpercentile(seg_hour_mean, 90, axis=1)  # (seg,)
    ff = np.where(np.isnan(ff) | (ff <= 0), np.nanmean(seg_hour_mean), ff)

    # Camera -> segment.
    cam_seg = np.full(n_c, -1, dtype=int)
    for c, i in cam_index.items():
        row = segmap[segmap["camera_id"] == c]
        if len(row) and pd.notna(row.iloc[0]["segment_id"]):
            cam_seg[i] = seg_index.get(int(row.iloc[0]["segment_id"]), -1)

    # Weather arrays aligned to `times`.
    w_aligned = w.reindex(times)
    weather = {
        "precip": w_aligned["precipitation_mm"].to_numpy(dtype=float),
        "snow": w_aligned["snowfall_cm"].to_numpy(dtype=float),
        "temp": w_aligned["temperature_2m_c"].to_numpy(dtype=float),
        "wind": w_aligned["wind_speed_10m_kmh"].to_numpy(dtype=float),
    }
    weather = {k: np.nan_to_num(v, nan=0.0) for k, v in weather.items()}

    # Daily crash totals per camera, and trailing-7-day daily rate.
    daily = crash_mat.reshape(n_c, n_d, 24).sum(axis=2).astype(float)
    trail7 = np.zeros_like(daily)
    for d in range(n_d):
        lo = max(0, d - 7)
        trail7[:, d] = daily[:, lo:d].mean(axis=1) if d > 0 else 0.0
    # trail7 for day d uses the 7 days BEFORE d (shifted: no leakage).
    trail7 = np.roll(trail7, 1, axis=1)
    trail7[:, 0] = 0.0

    hour_of_t = (times.hour).to_numpy()
    dow_of_t = (times.day_of_week).to_numpy()
    return {
        "times": times, "days": days, "cams": cams, "cam_index": cam_index,
        "crash_mat": crash_mat, "speed_mat": speed_mat, "segs": segs,
        "seg_hour_mean": seg_hour_mean, "ff": ff, "cam_seg": cam_seg,
        "weather": weather, "daily": daily, "trail7": trail7,
        "hour_of_t": hour_of_t, "dow_of_t": dow_of_t,
    }


def build_poisson_design(mats: dict):
    """Design matrix X (camera-hour rows, cam-major) + target y.

    Feature order is POISSON_FEATURES. speed_ratio uses the observed
    speed where present, else the segment's typical speed for that
    hour-of-day. log_cam_rate is the camera's mean crashes/day over
    the TRAIN window only; log_trail7_rate is the trailing-7-day
    daily crash rate (0 before any history -> floor).

    Only the fit window (through 2026-04-30, where congestion exists)
    is built into the design matrix; the input matrices run longer
    (through 2026-10-29) for prediction-time use.
    """
    # Slice to the fit window: 2025-01-01 00:00 -> 2026-04-30 23:00.
    n_t_fit = int((pd.Timestamp("2026-04-30 23:00") - mats["times"][0])
                  / pd.Timedelta(hours=1)) + 1
    n_d_fit = int((pd.Timestamp("2026-04-30") - mats["days"][0]).days) + 1
    n_c = len(mats["cams"])
    hour_of_t = mats["hour_of_t"][:n_t_fit]
    dow_of_t = mats["dow_of_t"][:n_t_fit]
    crash_mat = mats["crash_mat"][:, :n_t_fit]
    speed_mat = mats["speed_mat"][:, :n_t_fit]
    trail7 = mats["trail7"][:, :n_d_fit]
    daily = mats["daily"][:, :n_d_fit]
    weather = {k: v[:n_t_fit] for k, v in mats["weather"].items()}
    n_t = n_t_fit

    # Train-window camera daily rate (through POISSON_TRAIN_END).
    train_days = int((pd.Timestamp(POISSON_TRAIN_END) - mats["days"][0]).days) + 1
    cam_rate = mats["daily"][:, :train_days].sum(axis=1) / train_days
    cam_rate = np.maximum(cam_rate, _RATE_FLOOR)

    # Speed ratio per camera-hour, with typical-speed fill.
    cam_seg = mats["cam_seg"]
    ff = mats["ff"]
    speed = speed_mat
    seg_hour_mean = mats["seg_hour_mean"]
    # typical speed for (camera, t): segment's mean at that hour.
    typ = np.full((n_c, n_t), np.nan, dtype=np.float32)
    ok_cam = cam_seg >= 0
    # Build per-camera typical-speed matrix via segment lookup.
    typ_ok = seg_hour_mean[cam_seg[ok_cam]]  # (n_ok, 24)
    typ[ok_cam] = typ_ok[:, hour_of_t]
    cam_speed = np.full((n_c, n_t), np.nan, dtype=np.float32)
    cam_speed[ok_cam] = speed[cam_seg[ok_cam]]
    cam_speed = np.where(np.isnan(cam_speed), typ, cam_speed)
    ff_cam = np.where(ok_cam, ff[np.maximum(cam_seg, 0)], np.nan)
    # Cameras with no segment: ratio 1.0 (no congestion signal).
    speed_ratio = np.where(np.isnan(cam_speed) | (ff_cam[:, None] <= 0),
                           1.0, cam_speed / ff_cam[:, None]).astype(np.float32)
    speed_ratio = np.clip(speed_ratio, 0.2, 1.5)

    # Trailing-7d rate expanded to hours.
    trail7_h = np.repeat(trail7, 24, axis=1)
    log_trail7 = np.log(np.maximum(trail7_h, _RATE_FLOOR)).astype(np.float32)
    log_cam = np.log(cam_rate).astype(np.float32)

    N = n_c * n_t
    X = np.empty((N, len(POISSON_FEATURES)), dtype=np.float32)
    hour_sin = np.sin(2 * np.pi * hour_of_t / 24).astype(np.float32)
    hour_cos = np.cos(2 * np.pi * hour_of_t / 24).astype(np.float32)
    dow_sin = np.sin(2 * np.pi * dow_of_t / 7).astype(np.float32)
    dow_cos = np.cos(2 * np.pi * dow_of_t / 7).astype(np.float32)
    is_wknd = (dow_of_t >= 5).astype(np.float32)
    X[:, 0] = np.tile(hour_sin, n_c)
    X[:, 1] = np.tile(hour_cos, n_c)
    X[:, 2] = np.tile(dow_sin, n_c)
    X[:, 3] = np.tile(dow_cos, n_c)
    X[:, 4] = np.tile(is_wknd, n_c)
    X[:, 5] = speed_ratio.reshape(-1)
    X[:, 6] = np.tile(weather["precip"].astype(np.float32), n_c)
    X[:, 7] = np.tile(weather["snow"].astype(np.float32), n_c)
    X[:, 8] = np.tile((weather["temp"] / 10.0).astype(np.float32), n_c)
    X[:, 9] = np.tile((weather["wind"] / 10.0).astype(np.float32), n_c)
    X[:, 10] = np.repeat(log_cam, n_t)
    X[:, 11] = log_trail7.reshape(-1)
    y = crash_mat.reshape(-1).astype(np.float32)
    return X, y


def _poisson_metrics(y_true, y_pred):
    from sklearn.metrics import mean_poisson_deviance

    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.maximum(np.asarray(y_pred, dtype=float), 1e-9)
    err = y_pred - y_true
    return {
        "mean_poisson_deviance": float(mean_poisson_deviance(y_true, y_pred)),
        "rmse": float(np.sqrt(np.mean(err ** 2))),
        "mae": float(np.mean(np.abs(err))),
        "pred_total": float(y_pred.sum()),
        "actual_total": float(y_true.sum()),
        "mean_pred": float(y_pred.mean()),
        "mean_actual": float(y_true.mean()),
    }


def train_poisson_model(alphas=(1.0,), save_if_wins=True, xgb_poisson=False):
    """Train Poisson models vs the profile-only baseline.

    Split (time-based, inside the congestion window):
      train 2025-01-01 -> 2026-02-28, test 2026-03-01 -> 2026-04-30.
    Baseline predictions use profiles rebuilt on the TRAIN window only
    (no leakage from test-period crashes) x the camera's train-period
    daily crash rate. Returns (results dict, fitted model or None).
    The best model is saved to hourly_model.joblib ONLY if it beats
    the baseline on mean Poisson deviance without losing on RMSE.
    With `xgb_poisson`, an XGBoost count:poisson challenger (300
    rounds, depth 6, lr 0.05 — the same shape as the Phase 2 daily
    XGBoost) is also trained and compared on the same split.
    """
    from sklearn.linear_model import PoissonRegressor

    t0 = time.time()
    mats = _poisson_matrices()
    X, y = build_poisson_design(mats)
    # X covers the fit window only (through 2026-04-30); slice the
    # time axis to match before masking. X is cam-major (cam0 all
    # times, cam1 all times, ...), so the time mask tiles per camera.
    n_t_fit = int((pd.Timestamp("2026-04-30 23:00") - mats["times"][0])
                  / pd.Timedelta(hours=1)) + 1
    times = mats["times"][:n_t_fit]
    n_c = len(mats["cams"])
    train_mask_t = times <= pd.Timestamp(POISSON_TRAIN_END) + pd.Timedelta(hours=23)
    test_mask_t = (times >= pd.Timestamp(POISSON_TEST_START)) & (
        times <= pd.Timestamp(POISSON_TEST_END) + pd.Timedelta(hours=23))
    train_rows = np.tile(train_mask_t, n_c)
    test_rows = np.tile(test_mask_t, n_c)
    X_train, y_train = X[train_rows], y[train_rows]
    X_test, y_test = X[test_rows], y[test_rows]
    print(f"poisson design: train rows {len(X_train):,}  "
          f"test rows {len(X_test):,}  features {X.shape[1]}")

    # Baseline: train-window profiles x train daily crash rate.
    prof_train = build_profiles(crash_end=POISSON_TRAIN_END,
                                cong_end=POISSON_TRAIN_END)
    w_mat = np.zeros((n_c, 24))
    for i, cam in enumerate(mats["cams"]):
        sub = prof_train[prof_train["camera_id"] == cam].sort_values("hour")
        if len(sub) == 24:
            w_mat[i] = sub["weight"].to_numpy(dtype=float)
        else:  # pragma: no cover - every crash camera is in the map
            w_mat[i] = 1.0 / 24
    train_days = int((pd.Timestamp(POISSON_TRAIN_END) - mats["days"][0]).days) + 1
    cam_rate = np.maximum(
        mats["daily"][:, :train_days].sum(axis=1) / train_days, _RATE_FLOOR)
    hour_test = mats["hour_of_t"][:n_t_fit][test_mask_t]
    base_test = (cam_rate[:, None] * w_mat[:, hour_test]).reshape(-1)
    base_metrics = _poisson_metrics(y_test, base_test)
    print(f"baseline (profile-only): deviance "
          f"{base_metrics['mean_poisson_deviance']:.4f}  "
          f"RMSE {base_metrics['rmse']:.4f}  MAE {base_metrics['mae']:.4f}")

    results = {
        "split": {"train": f"2025-01-01 -> {POISSON_TRAIN_END}",
                  "test": f"{POISSON_TEST_START} -> {POISSON_TEST_END}"},
        "target": "hourly crash counts within 200 m of each camera (PROXY)",
        "features": POISSON_FEATURES,
        "train_rows": int(len(X_train)),
        "test_rows": int(len(X_test)),
        "baseline_profile_only": base_metrics,
        "models": {},
    }
    best = None  # (name, model, metrics)
    for alpha in alphas:
        t = time.time()
        reg = PoissonRegressor(alpha=float(alpha), max_iter=200, tol=1e-4)
        reg.fit(X_train, y_train)
        pred = np.clip(reg.predict(X_test), 0, None)
        m = _poisson_metrics(y_test, pred)
        results["models"][f"poisson_alpha_{alpha}"] = m
        print(f"poisson alpha={alpha}: deviance {m['mean_poisson_deviance']:.4f}  "
              f"RMSE {m['rmse']:.4f}  MAE {m['mae']:.4f}  ({time.time() - t:.0f}s)")
        if best is None or m["mean_poisson_deviance"] < best[2]["mean_poisson_deviance"]:
            best = (f"poisson_alpha_{alpha}", reg, m)

    if xgb_poisson:
        import xgboost as xgb

        t = time.time()
        xg = xgb.XGBRegressor(
            objective="count:poisson", n_estimators=300, max_depth=6,
            learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
            tree_method="hist", n_jobs=3, random_state=42)
        xg.fit(X_train, y_train)
        pred = np.clip(xg.predict(X_test), 0, None)
        m = _poisson_metrics(y_test, pred)
        results["models"]["xgboost_poisson"] = m
        print(f"xgboost poisson: deviance {m['mean_poisson_deviance']:.4f}  "
              f"RMSE {m['rmse']:.4f}  MAE {m['mae']:.4f}  ({time.time() - t:.0f}s)")
        if best is None or m["mean_poisson_deviance"] < best[2]["mean_poisson_deviance"]:
            best = ("xgboost_poisson", xg, m)

    name, reg, m = best
    wins = (m["mean_poisson_deviance"] < base_metrics["mean_poisson_deviance"]
            and m["rmse"] <= base_metrics["rmse"])
    results["winner"] = {
        "model": name if wins else "profile_only_baseline",
        "ships": "model" if wins else "profile",
        "reason": (f"{name} beats the profile-only baseline on deviance "
                   f"({m['mean_poisson_deviance']:.4f} vs "
                   f"{base_metrics['mean_poisson_deviance']:.4f}) with "
                   f"RMSE no worse ({m['rmse']:.4f} vs "
                   f"{base_metrics['rmse']:.4f}) and MAE "
                   f"{m['mae']:.4f} vs {base_metrics['mae']:.4f}."
                   if wins else
                   "No model beats the profile-only baseline, so the "
                   "profile version ships (see numbers above)."),
    }
    print(f"VERDICT: {results['winner']['model']} ships — "
          f"{results['winner']['reason']}")
    if wins and save_if_wins:
        import joblib

        joblib.dump(reg, POISSON_MODEL_PATH)
        payload = dict(results)
        if hasattr(reg, "coef_"):  # sklearn PoissonRegressor
            payload["coefficients"] = {
                f: float(c) for f, c in zip(POISSON_FEATURES, reg.coef_)}
            payload["intercept"] = float(reg.intercept_)
        else:  # XGBoost: feature importances (gain)
            payload["feature_importances_gain"] = {
                f: float(v) for f, v in
                zip(POISSON_FEATURES, reg.feature_importances_)}
        payload["model_params"] = reg.get_params()
        POISSON_METRICS_PATH.write_text(json.dumps(payload, indent=2))
        print(f"saved {POISSON_MODEL_PATH} + {POISSON_METRICS_PATH}")
    else:
        POISSON_METRICS_PATH.write_text(json.dumps(results, indent=2))
        print(f"model NOT saved; comparison written to {POISSON_METRICS_PATH}")
    print(f"total elapsed: {time.time() - t0:.0f}s")
    return results, (reg if wins else None)


def model_weights(camera_id: str, date) -> np.ndarray | None:
    """24 hourly weights from the fitted Poisson model (ESTIMATE).

    Returns None when the model file is absent or the date lacks the
    inputs the model needs (weather row, crash history). The caller
    falls back to the profile weights in that case.
    """
    import joblib

    if not POISSON_MODEL_PATH.exists():
        return None
    date = pd.Timestamp(date)
    w = _load_weather()
    day_rows = [date + pd.Timedelta(hours=h) for h in range(24)]
    if any(ts not in w.index for ts in day_rows):
        return None  # no weather for this date (e.g. after 2026-10-29)
    mats = _poisson_matrices()
    if str(camera_id) not in mats["cam_index"]:
        return None
    ci = mats["cam_index"][str(camera_id)]
    n_d = len(mats["days"])
    day_idx = int((date.normalize() - mats["days"][0]).days)
    if day_idx < 0 or day_idx >= n_d:
        return None
    # Trailing-7d rate: from crash data if the date is inside coverage,
    # else the camera's shipped-model daily rate.
    # log_cam_rate window = the SHIPPED model's training window end
    # (Phase 2d B12: 2026-07-31), NOT worker B's 2026-02-28 split.
    rate_end = pd.Timestamp(SHIPPED_MODEL_RATE_END)
    train_days = int((rate_end - mats["days"][0]).days) + 1
    cam_rate = max(float(mats["daily"][ci, :train_days].sum()) / train_days,
                   _RATE_FLOOR)
    if date <= pd.Timestamp("2026-10-29"):
        trail = float(mats["trail7"][ci, day_idx]) \
            if day_idx < mats["trail7"].shape[1] else 0.0
    else:
        trail = 0.0
    trail = max(trail, _RATE_FLOOR) if trail > 0 else cam_rate

    # Speed ratio: observed speed where the feed has it, else the
    # segment's typical speed for that hour (never forward-filled).
    seg = mats["cam_seg"][ci]
    ff = mats["ff"][seg] if seg >= 0 else np.nan
    rows = []
    for h in range(24):
        ts = date + pd.Timedelta(hours=h)
        ratio = 1.0
        if seg >= 0 and not np.isnan(ff) and ff > 0:
            obs = np.nan
            if ts <= CONGESTION_END:
                cg = _load_congestion()
                sub = cg[(cg["segment_id"] == mats["segs"][seg])
                         & (cg["timestamp"] == ts)]
                if len(sub):
                    obs = float(sub.iloc[0]["mean_speed_mph"])
            typ = mats["seg_hour_mean"][seg, h]
            speed = obs if not np.isnan(obs) else typ
            if not np.isnan(speed):
                ratio = float(np.clip(speed / ff, 0.2, 1.5))
        r = w.loc[ts]
        rows.append([
            math.sin(2 * math.pi * h / 24), math.cos(2 * math.pi * h / 24),
            math.sin(2 * math.pi * date.dayofweek / 7),
            math.cos(2 * math.pi * date.dayofweek / 7),
            1.0 if date.dayofweek >= 5 else 0.0,
            ratio,
            float(r["precipitation_mm"]), float(r["snowfall_cm"]),
            float(r["temperature_2m_c"]) / 10.0,
            float(r["wind_speed_10m_kmh"]) / 10.0,
            math.log(cam_rate), math.log(max(trail, _RATE_FLOOR)),
        ])
    reg = joblib.load(POISSON_MODEL_PATH)
    pred = np.clip(reg.predict(np.array(rows, dtype=np.float32)), 0, None)
    pred = np.asarray(pred, dtype=float)
    if pred.sum() <= 0:
        return None
    return pred / pred.sum()


# --- CLI --------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-profiles", action="store_true")
    ap.add_argument("--train-poisson", action="store_true")
    ap.add_argument("--alphas", default="1.0",
                    help="comma-separated PoissonRegressor alphas")
    ap.add_argument("--xgb-poisson", action="store_true",
                    help="also train the XGBoost count:poisson challenger")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    if args.all:
        args.build_profiles = True
        args.train_poisson = True
    if args.build_profiles:
        prof = build_profiles()
        prof.to_parquet(PROFILES_PATH, index=False)
        print(f"wrote {PROFILES_PATH}  rows={len(prof):,}  "
              f"cameras={prof['camera_id'].nunique()}")
        dev = prof.groupby("camera_id")["weight"].sum()
        print(f"profile sums: max |sum-1| = {np.abs(dev - 1).max():.2e}")
        print(f"citywide-profile cameras: "
              f"{sorted(prof.loc[prof['source'] == 'citywide', 'camera_id'].unique())}")
        print(f"thin local data (n<{THIN_N}): "
              f"{int(prof.drop_duplicates('camera_id')['thin_local_data'].sum())} cameras")
        cw = _load_crashes_citywide()
        city = cw.groupby("hour")["crash_count"].sum()
        print(f"citywide peak hour: {int(city.idxmax())}:00 "
              f"(share {(city / city.sum()).max():.4f}), "
              f"trough {int(city.idxmin())}:00")
    if args.train_poisson:
        alphas = tuple(float(a) for a in args.alphas.split(",") if a.strip())
        train_poisson_model(alphas=alphas, xgb_poisson=args.xgb_poisson)


if __name__ == "__main__":
    main()
