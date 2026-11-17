#!/usr/bin/env python3
"""Redunator Phase 2c — validation of the trip-demand layer (worker C).

Same holdout protocol as Phase 2b (`validate_hourly.py`), extended:

  * Holdout 2026-08-01 -> 2026-10-29 (90 days x 361 cameras =
    779,760 camera-hours, 8,517 observed attributed crashes).
  * Profiles rebuilt on pre-holdout crashes only (2025-01-01 ->
    2026-07-31); level = each camera's pre-holdout daily crash rate.
  * XGBoost scored with feature construction mirroring
    `hourly_model.model_weights` exactly (same as validate_hourly.py).

Variants (primary four, as briefed):
  (a) profile_only_preholdout          — Phase 2b baseline (0.0945)
  (b) profile_only + demand (shipped)  — (a) x demand multiplier,
      renormalised per camera-day. No weather, so the (b)-(a) delta
      is pure demand attribution.
  (c) xgboost_raw_counts               — Phase 2b baseline (0.0933)
  (d) xgboost_shape + demand (shipped) — XGB per-day weights x demand,
      renormalised per camera-day, scaled by the pre-holdout rate
      (the shipped model+demand logic, with the validation level).

Supplementary rows (attribution / leakage checks):
  * profile + weather + demand — the shipped profile path.
  * profile + demand_preholdout / xgb_shape + demand_preholdout —
    demand profiles REBUILT from trip counts truncated at
    2026-07-31 (window 2023-01-01 -> 2026-07-31), so no holdout-period
    trip enters the shape. The shipped demand profiles run
    2023-01-01 -> present (TNP through 2026-09-30, taxi through
    2026-11-01) and therefore DO include trips from inside the
    holdout window. That is not leakage of the TARGET (trips are not
    crashes), but it is shape information from the holdout period,
    so the pre-holdout rebuild measures how much it matters.

Also reports:
  * demand-vs-crash correlation: Pearson r between the demand weight
    and the observed crash-hour share — citywide (holdout day-type
    mix), per day type, and per community area (59 areas with
    cameras; distribution + worst areas).
  * low-volume-area split: deviance of (a) vs (b) and (c) vs (d)
    restricted to cameras in the bottom-quartile areas by weekday
    trips/day (Worker B flagged these as noisy) vs the rest.
  * per-hour calibration for (b) and (d), worst hours flagged.

Writes hourly_validation_demand.json next to this script.
Reads only; modifies no Phase 2 / worker A / worker B artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

import holidays
import joblib
import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr

import hourly_model as hm
from validate_hourly import (
    HOLDOUT_END,
    HOLDOUT_START,
    N_PRE_DAYS,
    N_TRAIN_DAYS,
    PRE_END,
    TRAIN_END,
    _metrics,
    _share_corr,
)

BASE = Path(__file__).resolve().parent
OUT_PATH = BASE / "hourly_validation_demand.json"
TRIP_PATH = BASE / "data" / "hourly" / "processed" / "trip_hourly_by_area.parquet"

# Phase 2b recorded numbers (hourly_validation.json) — reproduction check.
RECORDED = {
    "profile_only_preholdout": 0.09454932634961201,
    "xgboost_raw_counts": 0.09334237333814034,
    "xgboost_shape_x_preholdout_rate": 0.09333512833917688,
}


def _day_type_series(dates: pd.DatetimeIndex) -> np.ndarray:
    us = holidays.US(years=range(2019, 2027))
    out = np.empty(len(dates), dtype=object)
    for i, d in enumerate(dates):
        if d.date() in us:
            out[i] = "holiday"
        elif d.dayofweek >= 5:
            out[i] = "weekend"
        else:
            out[i] = "weekday"
    return out


def rebuild_demand_preholdout() -> dict:
    """Demand profiles from trips 2023-01-01 -> 2026-07-31 only.

    Same recipe as build_demand.py: summed taxi+TNP counts, split by
    day type, weights sum to 1 per (area, day_type). Returns
    {(area, day_type): 24-vector of weights}, area 0 = citywide.
    """
    df = pd.read_parquet(TRIP_PATH)
    df = df[df["area"].notna()].copy()
    df["day"] = pd.to_datetime(df["day"])
    df = df[(df["day"] >= "2023-01-01") & (df["day"] <= "2026-07-31")]
    df["area"] = df["area"].astype(int)
    df["day_type"] = _day_type_series(df["day"])
    agg = df.groupby(["area", "day_type", "hour"])["trips"].sum().reset_index()
    out: dict = {}
    for (area, dt), sub in agg.groupby(["area", "day_type"]):
        w = np.zeros(24)
        w[sub["hour"].to_numpy(dtype=int)] = sub["trips"].to_numpy(dtype=float)
        out[(int(area), str(dt))] = w / w.sum()
    # Citywide = sum over areas.
    cw = df.groupby(["day_type", "hour"])["trips"].sum().reset_index()
    for dt, sub in cw.groupby("day_type"):
        w = np.zeros(24)
        w[sub["hour"].to_numpy(dtype=int)] = sub["trips"].to_numpy(dtype=float)
        out[(0, str(dt))] = w / w.sum()
    return out


def _demand_mult_matrix(cams, cam_area, day_types_holdout, profiles):
    """(n_c, 90, 24) demand multipliers from a {(area, day_type): w} map."""
    n_c = len(cams)
    dm = np.ones((n_c, 90, 24))
    for dt in ("weekday", "weekend", "holiday"):
        days_idx = np.where(day_types_holdout == dt)[0]
        if len(days_idx) == 0:
            continue
        for i, cam in enumerate(cams):
            area = cam_area.get(cam, 0)
            w = profiles.get((area, dt))
            if w is None:
                w = profiles[(0, dt)]
            dm[i, days_idx, :] = w / w.mean()
    return dm


def main():
    print("loading matrices + rebuilding pre-holdout profiles ...")
    mats = hm._poisson_matrices()
    times, days = mats["times"], mats["days"]
    cams, cam_index = mats["cams"], mats["cam_index"]
    n_c = len(cams)

    prof_pre = hm.build_profiles(crash_end=PRE_END, cong_end="2026-04-30")
    w_pre = np.zeros((n_c, 24))
    for i, cam in enumerate(cams):
        sub = prof_pre[prof_pre["camera_id"] == cam].sort_values("hour")
        assert len(sub) == 24, cam
        w_pre[i] = sub["weight"].to_numpy(dtype=float)

    cw = hm._load_crashes_citywide()
    cw_pre = cw[cw["timestamp"] < pd.Timestamp("2026-08-01")]
    city_counts = cw_pre.groupby("hour")["crash_count"].sum().reindex(
        range(24), fill_value=0)
    p_city_pre = (city_counts / city_counts.sum()).to_numpy(dtype=float)

    daily = mats["daily"]
    pre_day_idx = int((pd.Timestamp(PRE_END) - days[0]).days) + 1
    train_day_idx = int((pd.Timestamp(TRAIN_END) - days[0]).days) + 1
    rate_pre = daily[:, :pre_day_idx].sum(axis=1) / N_PRE_DAYS
    rate_train = np.maximum(
        daily[:, :train_day_idx].sum(axis=1) / N_TRAIN_DAYS, 0.01)

    t0 = int((HOLDOUT_START - times[0]) / pd.Timedelta(hours=1))
    t1 = int((HOLDOUT_END + pd.Timedelta(hours=23) - times[0])
             / pd.Timedelta(hours=1)) + 1
    hold_times = times[t0:t1]
    n_t_hold = len(hold_times)
    assert n_t_hold == 90 * 24, n_t_hold
    y = mats["crash_mat"][:, t0:t1].astype(float)
    print(f"holdout camera-hours: {n_c * n_t_hold:,}  "
          f"observed crashes: {y.sum():,.0f}")

    hour_of_t = mats["hour_of_t"][t0:t1]
    dow_of_t = mats["dow_of_t"][t0:t1]
    day_idx_of_t = (hold_times.normalize() - days[0]).days.to_numpy()
    day_of_hold = ((hold_times.normalize() - HOLDOUT_START).days
                   .to_numpy())
    hold_dates = pd.date_range(HOLDOUT_START, HOLDOUT_END, freq="D")
    day_types_holdout = _day_type_series(hold_dates)
    print("holdout day types:",
          {dt: int((day_types_holdout == dt).sum())
           for dt in ("weekday", "weekend", "holiday")})

    wx = np.ones((90, 24))
    for d in range(90):
        wx[d] = hm.weather_multipliers_for_day(HOLDOUT_START + pd.Timedelta(days=d))

    # --- demand multipliers --------------------------------------------------
    # Shipped profiles (2023 -> present, includes holdout-period trips).
    shipped_prof: dict = {}
    ddf = hm.load_demand_profiles()
    for (area, dt), sub in ddf.groupby(["area", "day_type"]):
        w = np.zeros(24)
        sub = sub.sort_values("hour")
        w[sub["hour"].to_numpy(dtype=int)] = sub["weight"].to_numpy(dtype=float)
        shipped_prof[(int(area), str(dt))] = w / w.sum()
    # Pre-holdout rebuild (2023-01-01 -> 2026-07-31).
    pre_prof = rebuild_demand_preholdout()
    drift = []
    for k, w in shipped_prof.items():
        if k in pre_prof:
            drift.append(np.abs(w - pre_prof[k]))
    drift = np.array(drift)
    print(f"shipped-vs-preholdout demand drift: mean |dw| {drift.mean():.4f}, "
          f"max |dw| {drift.max():.4f} over {len(drift)} (area, day_type) cells")

    cam_area = {}
    area_map = hm.load_camera_area_map()
    for _, r in area_map.iterrows():
        cam_area[str(r["camera_id"])] = (
            int(r["community_area"]) if pd.notna(r["community_area"]) else 0)
    dm_ship = _demand_mult_matrix(cams, cam_area, day_types_holdout, shipped_prof)
    dm_pre = _demand_mult_matrix(cams, cam_area, day_types_holdout, pre_prof)
    print(f"demand multipliers (shipped): min {dm_ship.min():.3f} "
          f"max {dm_ship.max():.3f} mean {dm_ship.mean():.4f}")

    def _renorm(raw_day):
        denom = raw_day.sum(axis=2, keepdims=True)
        return raw_day / np.where(denom > 0, denom, 1.0)

    dm_t_ship = dm_ship[:, day_of_hold, hour_of_t]  # (n_c, n_t_hold)
    dm_t_pre = dm_pre[:, day_of_hold, hour_of_t]

    # --- base variants (verbatim Phase 2b construction) ----------------------
    pred_flat = np.repeat((rate_pre / 24)[:, None], n_t_hold, axis=1)
    pred_city = rate_pre[:, None] * p_city_pre[hour_of_t][None, :]
    pred_prof = rate_pre[:, None] * w_pre[:, hour_of_t]
    raw = rate_pre[:, None] * w_pre[:, hour_of_t] * wx[day_of_hold, hour_of_t]
    pred_profwx = (_renorm(raw.reshape(n_c, 90, 24))
                   * rate_pre[:, None, None]).reshape(n_c, n_t_hold)

    # --- demand variants (profile path) --------------------------------------
    raw_pd = rate_pre[:, None] * w_pre[:, hour_of_t] * dm_t_ship
    pred_prof_demand = (_renorm(raw_pd.reshape(n_c, 90, 24))
                        * rate_pre[:, None, None]).reshape(n_c, n_t_hold)
    raw_pd_pre = rate_pre[:, None] * w_pre[:, hour_of_t] * dm_t_pre
    pred_prof_demand_pre = (_renorm(raw_pd_pre.reshape(n_c, 90, 24))
                            * rate_pre[:, None, None]).reshape(n_c, n_t_hold)
    raw_pwd = (rate_pre[:, None] * w_pre[:, hour_of_t]
               * wx[day_of_hold, hour_of_t] * dm_t_ship)
    pred_profwx_demand = (_renorm(raw_pwd.reshape(n_c, 90, 24))
                          * rate_pre[:, None, None]).reshape(n_c, n_t_hold)

    # --- XGBoost (verbatim Phase 2b construction) ----------------------------
    reg = joblib.load(hm.POISSON_MODEL_PATH)
    seg_hour_mean, ff, cam_seg = mats["seg_hour_mean"], mats["ff"], mats["cam_seg"]
    weather = mats["weather"]
    w_precip = weather["precip"][t0:t1]
    w_snow = weather["snow"][t0:t1]
    w_temp = weather["temp"][t0:t1]
    w_wind = weather["wind"][t0:t1]
    trail7 = mats["trail7"]
    typ = np.full((n_c, n_t_hold), np.nan)
    ok = cam_seg >= 0
    typ[ok] = seg_hour_mean[cam_seg[ok]][:, hour_of_t]
    ff_cam = np.where(ok, ff[np.maximum(cam_seg, 0)], np.nan)
    speed_ratio = np.where(np.isnan(typ) | (ff_cam[:, None] <= 0),
                           1.0, typ / ff_cam[:, None])
    speed_ratio = np.clip(speed_ratio, 0.2, 1.5)
    trail_day = trail7[:, day_idx_of_t[::24]]
    trail_day = np.where(trail_day > 0, trail_day, rate_train[:, None])
    log_trail_h = np.log(np.maximum(
        np.repeat(trail_day, 24, axis=1), 0.01)).astype(np.float32)
    log_cam = np.log(rate_train).astype(np.float32)
    N = n_c * n_t_hold
    X = np.empty((N, 12), dtype=np.float32)
    X[:, 0] = np.tile(np.sin(2 * np.pi * hour_of_t / 24).astype(np.float32), n_c)
    X[:, 1] = np.tile(np.cos(2 * np.pi * hour_of_t / 24).astype(np.float32), n_c)
    X[:, 2] = np.tile(np.sin(2 * np.pi * dow_of_t / 7).astype(np.float32), n_c)
    X[:, 3] = np.tile(np.cos(2 * np.pi * dow_of_t / 7).astype(np.float32), n_c)
    X[:, 4] = np.tile((dow_of_t >= 5).astype(np.float32), n_c)
    X[:, 5] = speed_ratio.reshape(-1)
    X[:, 6] = np.tile(w_precip.astype(np.float32), n_c)
    X[:, 7] = np.tile(w_snow.astype(np.float32), n_c)
    X[:, 8] = np.tile((w_temp / 10.0).astype(np.float32), n_c)
    X[:, 9] = np.tile((w_wind / 10.0).astype(np.float32), n_c)
    X[:, 10] = np.repeat(log_cam, n_t_hold)
    X[:, 11] = log_trail_h.reshape(-1)
    print("scoring XGBoost on holdout ...")
    pred_xgb_raw = np.clip(reg.predict(X), 0, None).reshape(n_c, n_t_hold)
    xgb_day = pred_xgb_raw.reshape(n_c, 90, 24)
    pred_xgb_shape = (_renorm(xgb_day)
                      * rate_pre[:, None, None]).reshape(n_c, n_t_hold)
    # XGB + demand (shipped): model weights x demand, renormalised, x rate.
    xgb_dm = _renorm(xgb_day * dm_ship)
    pred_xgb_demand = (xgb_dm * rate_pre[:, None, None]).reshape(n_c, n_t_hold)
    xgb_dm_pre = _renorm(xgb_day * dm_pre)
    pred_xgb_demand_pre = (xgb_dm_pre * rate_pre[:, None, None]).reshape(
        n_c, n_t_hold)

    variants = {
        "flat_uniform": pred_flat,
        "citywide_shape": pred_city,
        "profile_only_preholdout": pred_prof,
        "profile_plus_weather": pred_profwx,
        "profile_plus_demand_shipped": pred_prof_demand,
        "profile_plus_demand_preholdout": pred_prof_demand_pre,
        "profile_plus_weather_plus_demand_shipped": pred_profwx_demand,
        "xgboost_raw_counts": pred_xgb_raw,
        "xgboost_shape_x_preholdout_rate": pred_xgb_shape,
        "xgboost_shape_plus_demand_shipped": pred_xgb_demand,
        "xgboost_shape_plus_demand_preholdout": pred_xgb_demand_pre,
    }
    y_flat = y.reshape(-1)
    obs_hour_totals = y.reshape(n_c, 90, 24).sum(axis=(0, 1))
    obs_peak = int(np.argmax(obs_hour_totals))
    results = {
        "holdout": {"start": "2026-08-01", "end": "2026-10-29", "days": 90,
                    "cameras": int(n_c), "camera_hours": int(N),
                    "observed_crashes_attributed": float(y.sum()),
                    "nonzero_camera_hours": int((y > 0).sum()),
                    "day_types": {dt: int((day_types_holdout == dt).sum())
                                  for dt in ("weekday", "weekend", "holiday")},
                    "note": ("Same protocol as validate_hourly.py; demand "
                             "multipliers are post-hoc shape layers, "
                             "renormalised per camera-day. Shipped demand "
                             "profiles include holdout-period trips "
                             "(2023->present); *_preholdout demand profiles "
                             "are rebuilt from trips through 2026-07-31.")},
        "demand_profile_drift_shipped_vs_preholdout": {
            "mean_abs_weight_diff": float(drift.mean()),
            "max_abs_weight_diff": float(drift.max()),
            "n_cells": int(len(drift)),
        },
        "demand_multipliers_holdout_shipped": {
            "min": float(dm_ship.min()), "max": float(dm_ship.max()),
            "mean": float(dm_ship.mean()),
        },
        "variants": {},
        "reproduction_check": {},
    }
    print(f"\nobserved citywide peak hour: {obs_peak}:00")
    for name, pred in variants.items():
        m = _metrics(y_flat, pred.reshape(-1))
        ph_tot = pred.reshape(n_c, 90, 24).sum(axis=(0, 1))
        p_share, o_share, rp, rs = _share_corr(ph_tot, obs_hour_totals)
        m["share_pearson_r"] = rp
        m["share_spearman_rho"] = rs
        m["pred_peak_hour"] = int(np.argmax(ph_tot))
        m["pred_peak_share"] = float(p_share.max())
        m["obs_peak_share"] = float(o_share[obs_peak])
        results["variants"][name] = m
        rec = RECORDED.get(name)
        if rec is not None:
            results["reproduction_check"][name] = {
                "recorded_deviance": rec,
                "reproduced_deviance": m["mean_poisson_deviance"],
                "abs_diff": float(abs(m["mean_poisson_deviance"] - rec)),
            }
        print(f"{name:42s} dev {m['mean_poisson_deviance']:.4f}  "
              f"RMSE {m['rmse']:.4f}  MAE {m['mae']:.4f}  "
              f"pred {m['pred_total']:,.0f} vs actual {m['actual_total']:,.0f}  "
              f"share r={rp:.3f} rho={rs:.3f}  peak {m['pred_peak_hour']}:00")

    # --- per-hour calibration for the two demand variants --------------------
    calib = {}
    for name in ("profile_plus_demand_shipped",
                 "xgboost_shape_plus_demand_shipped",
                 "profile_only_preholdout", "xgboost_raw_counts"):
        pred = variants[name]
        ph_tot = pred.reshape(n_c, 90, 24).sum(axis=(0, 1))
        p_share, o_share, _, _ = _share_corr(ph_tot, obs_hour_totals)
        rows = []
        for h in range(24):
            rows.append({
                "hour": h,
                "observed_crashes": float(obs_hour_totals[h]),
                "observed_share": float(o_share[h]),
                "predicted_share": float(p_share[h]),
                "pred_over_obs_ratio": float(p_share[h] / o_share[h])
                if o_share[h] > 0 else None,
            })
        calib[name] = rows
        worst_over = max(rows, key=lambda r: r["pred_over_obs_ratio"])
        worst_under = min(rows, key=lambda r: r["pred_over_obs_ratio"])
        print(f"{name}: worst over {worst_over['hour']}:00 "
              f"(ratio {worst_over['pred_over_obs_ratio']:.2f}); "
              f"worst under {worst_under['hour']}:00 "
              f"(ratio {worst_under['pred_over_obs_ratio']:.2f})")
    results["per_hour_calibration"] = calib
    results["observed_peak_hour"] = obs_peak
    results["observed_hour_totals"] = [float(v) for v in obs_hour_totals]

    # --- demand-vs-crash correlation -----------------------------------------
    # Citywide: demand share for the holdout's day-type mix (area 0).
    n_days_by_type = {dt: int((day_types_holdout == dt).sum())
                      for dt in ("weekday", "weekend", "holiday")}
    dem_cw = np.zeros(24)
    for dt, nd in n_days_by_type.items():
        dem_cw += nd * shipped_prof[(0, dt)]
    dem_cw /= dem_cw.sum()
    o_share = obs_hour_totals / obs_hour_totals.sum()
    r_cw = float(pearsonr(dem_cw, o_share).statistic)
    rho_cw = float(spearmanr(dem_cw, o_share).statistic)
    per_type = {}
    for dt in ("weekday", "weekend", "holiday"):
        d_idx = np.where(day_types_holdout == dt)[0]
        if len(d_idx) == 0:
            continue
        obs_dt = y.reshape(n_c, 90, 24)[:, d_idx, :].sum(axis=(0, 1, 2)) \
            if False else y.reshape(n_c, 90, 24)[:, d_idx, :].sum(axis=(0, 1))
        obs_dt_share = obs_dt / obs_dt.sum()
        per_type[dt] = {
            "n_days": int(len(d_idx)),
            "demand_share_peak_hour": int(np.argmax(shipped_prof[(0, dt)])),
            "observed_peak_hour": int(np.argmax(obs_dt_share)),
            "pearson_r": float(pearsonr(shipped_prof[(0, dt)],
                                         obs_dt_share).statistic),
            "spearman_rho": float(spearmanr(shipped_prof[(0, dt)],
                                             obs_dt_share).statistic),
        }
    print(f"\ncitywide demand-vs-crash share: Pearson r={r_cw:.3f} rho={rho_cw:.3f} "
          f"(demand peak {int(np.argmax(dem_cw))}:00 vs observed {obs_peak}:00)")
    for dt, v in per_type.items():
        print(f"  {dt}: r={v['pearson_r']:.3f} rho={v['spearman_rho']:.3f} "
              f"demand peak {v['demand_share_peak_hour']}:00 "
              f"observed peak {v['observed_peak_hour']}:00")

    # Per-area: demand share (holdout day-type mix) vs observed crash
    # share aggregated over that area's cameras.
    y_day = y.reshape(n_c, 90, 24)
    dem_by_area = {}
    for area in sorted(set(cam_area.values())):
        if area == 0:
            continue
        w_mix = np.zeros(24)
        for dt, nd in n_days_by_type.items():
            w_mix += nd * shipped_prof.get((area, dt), shipped_prof[(0, dt)])
        dem_by_area[area] = w_mix / w_mix.sum()
    area_rows = []
    for area, dw in dem_by_area.items():
        idx = [i for i, c in enumerate(cams) if cam_area.get(c, 0) == area]
        if not idx:
            continue
        obs_a = y_day[idx].sum(axis=(0, 1))
        tot = obs_a.sum()
        if tot < 20:  # too few crashes for a 24-share correlation
            continue
        os_a = obs_a / tot
        area_rows.append({
            "area": int(area),
            "n_cameras": len(idx),
            "observed_crashes": float(tot),
            "pearson_r": float(pearsonr(dw, os_a).statistic),
            "spearman_rho": float(spearmanr(dw, os_a).statistic),
            "demand_peak_hour": int(np.argmax(dw)),
            "observed_peak_hour": int(np.argmax(os_a)),
        })
    rs = np.array([r["pearson_r"] for r in area_rows])
    # Pooled (area, hour) cells, unweighted.
    pooled_dem = np.concatenate([dem_by_area[r["area"]] for r in area_rows])
    pooled_obs = np.concatenate([
        y_day[[i for i, c in enumerate(cams)
               if cam_area.get(c, 0) == r["area"]]].sum(axis=(0, 1))
        / y_day[[i for i, c in enumerate(cams)
                 if cam_area.get(c, 0) == r["area"]]].sum()
        for r in area_rows])
    r_pooled = float(pearsonr(pooled_dem, pooled_obs).statistic)
    print(f"per-area demand-vs-crash: n_areas={len(area_rows)} "
          f"median r={np.median(rs):.3f} mean r={rs.mean():.3f} "
          f"min r={rs.min():.3f} (area "
          f"{area_rows[int(np.argmin(rs))]['area']}) "
          f"max r={rs.max():.3f}; pooled-cell r={r_pooled:.3f}")
    results["demand_crash_correlation"] = {
        "citywide_holdout_mix": {"pearson_r": r_cw, "spearman_rho": rho_cw,
                                 "demand_peak_hour": int(np.argmax(dem_cw)),
                                 "observed_peak_hour": obs_peak,
                                 "day_counts": n_days_by_type},
        "per_day_type": per_type,
        "per_area": {
            "n_areas": len(area_rows),
            "median_pearson_r": float(np.median(rs)),
            "mean_pearson_r": float(rs.mean()),
            "min_pearson_r": float(rs.min()),
            "max_pearson_r": float(rs.max()),
            "n_areas_r_above_0.8": int((rs > 0.8).sum()),
            "n_areas_r_below_0.5": int((rs < 0.5).sum()),
            "pooled_cell_pearson_r": r_pooled,
            "areas": area_rows,
        },
    }

    # --- low-volume-area split -----------------------------------------------
    # Weekday trips/day per area from the shipped demand profiles.
    trips_wd = {}
    for (area, dt), sub in ddf.groupby(["area", "day_type"]):
        if dt == "weekday" and area != 0:
            trips_wd[int(area)] = float(sub["trips"].sum()
                                        / sub["n_days"].iloc[0])
    vols = np.array(sorted(trips_wd.values()))
    q25 = float(np.quantile(vols, 0.25))
    low_areas = {a for a, v in trips_wd.items() if v <= q25}
    print(f"\nlow-volume areas (bottom quartile, <= {q25:,.0f} weekday "
          f"trips/day): {len(low_areas)} areas")
    low_mask = np.array([cam_area.get(c, 0) in low_areas for c in cams])
    print(f"cameras in low-volume areas: {int(low_mask.sum())} of {n_c}; "
          f"their holdout crashes: {float(y[low_mask].sum()):,.0f} of "
          f"{float(y.sum()):,.0f}")
    group_results = {}
    for gname, mask in (("low_volume_areas", low_mask),
                        ("other_areas", ~low_mask)):
        sub_y = y[mask].reshape(-1)
        grow = {}
        for name in ("profile_only_preholdout",
                     "profile_plus_demand_shipped",
                     "xgboost_raw_counts",
                     "xgboost_shape_plus_demand_shipped"):
            grow[name] = _metrics(sub_y, variants[name][mask].reshape(-1))
        group_results[gname] = {
            "n_cameras": int(mask.sum()),
            "observed_crashes": float(y[mask].sum()),
            "weekday_trips_per_day_q25_cutoff": q25,
            "variants": grow,
        }
        print(f"{gname}: profile dev "
              f"{grow['profile_only_preholdout']['mean_poisson_deviance']:.4f} "
              f"-> +demand "
              f"{grow['profile_plus_demand_shipped']['mean_poisson_deviance']:.4f}; "
              f"xgb raw "
              f"{grow['xgboost_raw_counts']['mean_poisson_deviance']:.4f} "
              f"-> +demand "
              f"{grow['xgboost_shape_plus_demand_shipped']['mean_poisson_deviance']:.4f}")
    results["low_volume_split"] = group_results

    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    main()
