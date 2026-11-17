#!/usr/bin/env python3
"""Redunator Phase 2d — adaptive-trust / daily-deviation / lag builders.

Pure builders for the improved trip-demand layer (worker 2). None of
these functions touch the shipped daily model or `hourly_risk.py`;
they only build (n_cameras, 90, 24) multiplier matrices that the
validation script applies on top of the Phase 2b profile / XGBoost
shape, renormalised per camera-day exactly as in
`validate_hourly_demand.py`.

Leakage discipline (see ADAPTIVE_FINDINGS.md):
  * Per-area demand-crash correlations are computed on PRE-HOLDOUT
    data only (crashes 2025-01-01 -> 2026-07-31, demand profiles
    rebuilt from trips 2023-01-01 -> 2026-07-31).
  * Daily-deviation "typical" shares are taxi-only pre-holdout
    profiles (same source as the daily trips, so the deviation is
    apples-to-apples). The same-day deviation itself uses the
    holdout day's own trips: it is a POST-HOC diagnostic, not a
    forecast — trips for the day are not available at prediction
    time in production (taxi/TNP feeds lag ~2 weeks, like the
    violation feed). The prev-day and same-weekday-last-week
    versions are the forecastable ones.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr

BASE = Path(__file__).resolve().parent
TRIP_PATH = BASE / "data" / "hourly" / "processed" / "trip_hourly_by_area.parquet"

PRE_START = pd.Timestamp("2025-01-01")
PRE_END = pd.Timestamp("2026-07-31")
TRIP_START = pd.Timestamp("2023-01-01")
TRIP_END = pd.Timestamp("2026-11-01")


# ---------------------------------------------------------------------------
# Task 1 — per-area adaptive trust
# ---------------------------------------------------------------------------

def preholdout_area_correlations(mats, cam_area, pre_prof,
                                 day_types_pre, min_crashes=20):
    """Demand-vs-crash hourly-share correlation per area, PRE-holdout.

    Demand share: pre-holdout demand profiles blended with the
    PRE-holdout day-type mix. Crash share: attributed crashes
    2025-01-01 -> 2026-07-31 summed over the area's cameras.
    Areas with fewer than `min_crashes` pre-holdout crashes are
    reported with r=None (too thin to trust).

    Returns (rows, day_type_counts) where rows is a list of dicts.
    """
    crash_mat = mats["crash_mat"]  # (n_c, n_t_hours)
    times = mats["times"]
    n_pre_hours = int((pd.Timestamp("2026-08-01") - times[0])
                      / pd.Timedelta(hours=1))
    pre = crash_mat[:, :n_pre_hours].reshape(len(mats["cams"]), -1, 24)
    cam_hour_totals = pre.sum(axis=1)  # (n_c, 24)

    n_days_by_type = {dt: int((day_types_pre == dt).sum())
                      for dt in ("weekday", "weekend", "holiday")}
    total_pre_days = sum(n_days_by_type.values())

    rows = []
    for area in sorted(set(cam_area.values())):
        if area == 0:
            continue
        idx = [i for i, c in enumerate(mats["cams"])
               if cam_area.get(c, 0) == area]
        if not idx:
            continue
        w_mix = np.zeros(24)
        for dt, nd in n_days_by_type.items():
            w = pre_prof.get((area, dt), pre_prof.get((0, dt)))
            w_mix += nd * w
        w_mix = w_mix / w_mix.sum()
        obs = cam_hour_totals[idx].sum(axis=0).astype(float)
        tot = obs.sum()
        row = {"area": int(area), "n_cameras": len(idx),
               "preholdout_crashes": float(tot),
               "demand_peak_hour": int(np.argmax(w_mix))}
        if tot < min_crashes:
            row.update({"pearson_r": None, "spearman_rho": None,
                        "observed_peak_hour": int(np.argmax(obs))
                        if tot > 0 else None})
        else:
            os_a = obs / tot
            row.update({
                "pearson_r": float(pearsonr(w_mix, os_a).statistic),
                "spearman_rho": float(spearmanr(w_mix, os_a).statistic),
                "observed_peak_hour": int(np.argmax(os_a)),
            })
        rows.append(row)
    return rows, n_days_by_type


def qualifying_areas(area_rows, threshold):
    """Areas whose PRE-holdout demand-crash Pearson r clears `threshold`."""
    return {r["area"] for r in area_rows
            if r["pearson_r"] is not None and r["pearson_r"] > threshold}


def adaptive_demand_matrix(cams, cam_area, day_types_holdout, pre_prof,
                           qual_areas):
    """(n_c, 90, 24) demand multipliers, applied ONLY in `qual_areas`.

    Elsewhere the multiplier is exactly 1.0 (crash profile alone).
    Uses the pre-holdout demand profiles (no holdout-period trips).
    """
    n_c = len(cams)
    dm = np.ones((n_c, 90, 24))
    for dt in ("weekday", "weekend", "holiday"):
        days_idx = np.where(day_types_holdout == dt)[0]
        if len(days_idx) == 0:
            continue
        for i, cam in enumerate(cams):
            area = cam_area.get(cam, 0)
            if area not in qual_areas:
                continue
            w = pre_prof.get((area, dt))
            if w is None:
                w = pre_prof[(0, dt)]
            dm[i, days_idx, :] = w / w.mean()
    return dm


# ---------------------------------------------------------------------------
# Task 2 — daily-deviation adjustment (taxi-only, consistent source)
# ---------------------------------------------------------------------------

def load_taxi_daily_trips():
    """Taxi trips as (days, areas, hours) array.

    days: 2023-01-01 -> 2026-11-01 (taxi coverage in the shipped file).
    Index 0 of the area axis is the citywide total; areas 1..77 are
    the community areas. Missing (day, area, hour) cells are 0 trips.
    """
    df = pd.read_parquet(TRIP_PATH)
    df = df[(df["source"] == "taxi") & (df["area"].notna())].copy()
    df["day"] = pd.to_datetime(df["day"])
    df = df[(df["day"] >= TRIP_START) & (df["day"] <= TRIP_END)]
    df["area"] = df["area"].astype(int)
    days = pd.date_range(TRIP_START, TRIP_END, freq="D")
    day_idx = {d: i for i, d in enumerate(days)}
    arr = np.zeros((len(days), 78, 24), dtype=np.float32)
    di = df["day"].map(day_idx).to_numpy()
    ai = df["area"].to_numpy(dtype=int)
    hi = df["hour"].to_numpy(dtype=int)
    np.add.at(arr, (di, ai, hi), df["trips"].to_numpy(dtype=np.float32))
    arr[:, 0, :] = arr[:, 1:, :].sum(axis=1)  # citywide = sum over areas
    return days, arr


def taxi_typical_shares(days, arr, day_types):
    """Typical taxi share per (area, day_type) from PRE-holdout days.

    `day_types` is aligned with `days` (same _day_type_series recipe).
    Only days <= 2026-07-31 enter. Returns {(area, day_type): 24-vector}.
    """
    pre = days <= PRE_END
    out = {}
    for area in range(78):
        for dt in ("weekday", "weekend", "holiday"):
            sel = pre & (day_types == dt)
            tot = arr[sel, area, :].sum(axis=0).astype(float)
            out[(area, dt)] = tot / tot.sum() if tot.sum() > 0 else np.full(24, 1 / 24)
    return out


def daily_deviation_multipliers(cams, cam_area, holdout_days, days, arr,
                                typical, day_types_holdout,
                                source_offset=0, alpha=0.5,
                                clip=(0.5, 2.0)):
    """(n_c, 90, 24) nudge multipliers from a day's demand deviation.

    For camera-day (cam, d): source day = holdout day d shifted by
    `source_offset` (0 = same day, -1 = previous day, -7 = same
    weekday last week). deviation(h) = source day's taxi share in the
    camera's area / typical taxi share for the source day's day type.
    Nudge = 1 + alpha * (deviation - 1), clipped to `clip`. Areas/days
    with zero source-day trips get a 1.0 multiplier (no nudge).

    `holdout_days` is a DatetimeIndex of the 90 holdout dates;
    `days`/`arr` come from `load_taxi_daily_trips`; `typical` from
    `taxi_typical_shares`; `day_types_holdout` is the holdout day-type
    array. The source day's day type is computed from its own date.
    """
    import holidays as _holidays
    us = _holidays.US(years=range(2019, 2027))

    def _dt(d):
        if d.date() in us:
            return "holiday"
        return "weekend" if d.dayofweek >= 5 else "weekday"

    day_pos = {d: i for i, d in enumerate(days)}
    n_c = len(cams)
    dm = np.ones((n_c, 90, 24))
    zero_days = 0
    for d in range(90):
        src_date = holdout_days[d] + pd.Timedelta(days=source_offset)
        src_i = day_pos.get(src_date)
        if src_i is None:
            continue
        src_dt = _dt(src_date)
        for i, cam in enumerate(cams):
            area = cam_area.get(cam, 0)
            trips = arr[src_i, area, :].astype(float)
            tot = trips.sum()
            if tot <= 0:
                zero_days += 1
                continue
            share = trips / tot
            typ = typical[(area, src_dt)]
            dev = np.where(typ > 0, share / np.where(typ > 0, typ, 1.0), 1.0)
            dm[i, d, :] = np.clip(1.0 + alpha * (dev - 1.0),
                                  clip[0], clip[1])
    return dm, zero_days


# ---------------------------------------------------------------------------
# Task 3 — demand lag test
# ---------------------------------------------------------------------------

def lagged_demand_matrix(cams, cam_area, day_types_holdout, pre_prof, lag):
    """(n_c, 90, 24) demand multipliers with the shape shifted by `lag`.

    Crash hour h uses the demand weight of hour (h + lag) mod 24.
    lag = 0 reproduces the Phase 2c pre-holdout demand multiplier.
    """
    n_c = len(cams)
    dm = np.ones((n_c, 90, 24))
    for dt in ("weekday", "weekend", "holiday"):
        days_idx = np.where(day_types_holdout == dt)[0]
        if len(days_idx) == 0:
            continue
        for i, cam in enumerate(cams):
            area = cam_area.get(cam, 0)
            w = pre_prof.get((area, dt))
            if w is None:
                w = pre_prof[(0, dt)]
            w_roll = np.roll(w, -lag)  # w_roll[h] = w[(h + lag) % 24]
            dm[i, days_idx, :] = w_roll / w_roll.mean()
    return dm


def lag_correlations(pre_prof, obs_hour_share_pre, day_counts_pre):
    """Citywide Pearson r of crash share(h) vs demand share(h + lag).

    Demand share is the pre-holdout citywide (area 0) profile blended
    with the PRE-holdout day-type mix; crash share is the pre-holdout
    citywide hourly crash share. Returns {lag: r} for lag in -1, 0, +1.
    """
    dem = np.zeros(24)
    for dt, nd in day_counts_pre.items():
        dem += nd * pre_prof[(0, dt)]
    dem /= dem.sum()
    out = {}
    for lag in (-1, 0, 1):
        dem_shift = np.roll(dem, -lag)  # dem_shift[h] = dem[(h + lag) % 24]
        out[lag] = float(pearsonr(dem_shift, obs_hour_share_pre).statistic)
    return out
