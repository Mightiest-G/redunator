#!/usr/bin/env python3
"""Redunator Phase 2d — validation of the trip-speed (slowness) layer (worker 3).

Same holdout protocol as Phase 2b (`validate_hourly.py`) and Phase 2c
(`validate_hourly_demand.py`):
  * Holdout 2026-08-01 -> 2026-10-29 (90 days x 361 cameras =
    779,760 camera-hours, 8,517 observed attributed crashes).
  * Profiles rebuilt on pre-holdout crashes only (2025-01-01 ->
    2026-07-31); level = each camera's pre-holdout daily crash rate.
  * XGBoost scored with feature construction mirroring
    `hourly_model.model_weights` exactly (same as validate_hourly.py).

New in 2d — slowness (Worker 1, SPEED_FINDINGS.md):
  * Source: `data/hourly/processed/speed_profiles.parquet` — mean trip
    speed (mph) per (community area, day type, hour), built from valid
    taxi + TNP trips 2023-01-01 -> 2026-07-31 (PRE-HOLDOUT by
    construction; window string in the file reads
    "2023-01-01 -> 2026-08-01 (pre-holdout)", i.e. through 2026-07-31
    inclusive: 896 weekday / 370 weekend / 42 holiday days). 0 NaNs.
  * Slowness shape: slow[h] = 1 / speed[h]; multiplier =
    slow[h] / mean(slow) (mean exactly 1.0, same convention as the
    demand multiplier). Strength alpha: multiplier**alpha, so
    alpha=1 is the full blend and alpha=0.5 a half-strength blend
    (geometric shrink toward 1.0). Renormalised per camera-day.
  * Combined slowness+demand: product of the two mean-1 multipliers,
    renormalised per camera-day (the fallback Worker 1 suggested).
  * Demand here uses the PRE-HOLDOUT demand profiles rebuilt from
    trips through 2026-07-31 (validate_hourly_demand.
    rebuild_demand_preholdout), not the shipped profiles, so no
    holdout-period trip enters any shape tested here.

Variants (primary):
  profile_only_preholdout, profile_plus_weather (reproduction),
  profile_plus_slowness, profile_plus_slowness_half,
  profile_plus_weather_plus_slowness,
  profile_plus_slowness_plus_demand,
  profile_plus_weather_plus_slowness_plus_demand,
  xgboost_raw_counts, xgboost_shape_x_preholdout_rate (reproduction),
  xgb_shape_plus_slowness, xgb_shape_plus_slowness_half,
  xgb_shape_plus_slowness_plus_demand.

Also reports slowness-vs-crash share correlation (citywide + per
area) and per-hour calibration for the main slowness variants.

Writes hourly_validation_2d.json next to this script.
Reads only; modifies no Phase 2 / 2b / 2c artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

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
from validate_hourly_demand import (
    RECORDED,
    _day_type_series,
    _demand_mult_matrix,
    rebuild_demand_preholdout,
)

BASE = Path(__file__).resolve().parent
OUT_PATH = BASE / "hourly_validation_2d.json"
SPEED_PATH = BASE / "data" / "hourly" / "processed" / "speed_profiles.parquet"


def load_speed_profiles() -> dict:
    """{(area, day_type): 24-vector of mean speeds (mph)} from
    speed_profiles.parquet. Area 0 = citywide. Window is pre-holdout
    (2023-01-01 -> 2026-07-31); asserted on the stored window string
    and on the absence of NaNs."""
    df = pd.read_parquet(SPEED_PATH)
    windows = df["window"].unique()
    assert len(windows) == 1 and "pre-holdout" in str(windows[0]), windows
    assert df["mean_speed_mph"].isna().sum() == 0
    assert (df["mean_speed_mph"] > 0).all()
    out: dict = {}
    for (area, dt), sub in df.groupby(["area", "day_type"]):
        w = np.zeros(24)
        sub = sub.sort_values("hour")
        w[sub["hour"].to_numpy(dtype=int)] = sub["mean_speed_mph"].to_numpy(dtype=float)
        assert (w > 0).all(), (area, dt)
        out[(int(area), str(dt))] = w
    return out


def slowness_mult_matrix(cams, cam_area, day_types_holdout, speed_prof,
                         alpha: float = 1.0):
    """(n_c, 90, 24) slowness multipliers: (1/speed) / mean(1/speed),
    raised to `alpha`, per camera area + holdout day type. Unmapped
    cameras fall back to the citywide (area 0) profile."""
    n_c = len(cams)
    sm = np.ones((n_c, 90, 24))
    for dt in ("weekday", "weekend", "holiday"):
        days_idx = np.where(day_types_holdout == dt)[0]
        if len(days_idx) == 0:
            continue
        for i, cam in enumerate(cams):
            area = cam_area.get(cam, 0)
            speeds = speed_prof.get((area, dt))
            if speeds is None:
                speeds = speed_prof[(0, dt)]
            slow = 1.0 / speeds
            mult = slow / slow.mean()
            sm[i, days_idx, :] = mult ** alpha
    return sm


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
    day_of_hold = ((hold_times.normalize() - HOLDOUT_START).days.to_numpy())
    hold_dates = pd.date_range(HOLDOUT_START, HOLDOUT_END, freq="D")
    day_types_holdout = _day_type_series(hold_dates)

    wx = np.ones((90, 24))
    for d in range(90):
        wx[d] = hm.weather_multipliers_for_day(HOLDOUT_START + pd.Timedelta(days=d))

    # --- camera -> area ------------------------------------------------------
    cam_area = {}
    area_map = hm.load_camera_area_map()
    for _, r in area_map.iterrows():
        cam_area[str(r["camera_id"])] = (
            int(r["community_area"]) if pd.notna(r["community_area"]) else 0)

    # --- slowness + demand multipliers (both pre-holdout) --------------------
    speed_prof = load_speed_profiles()
    print(f"speed profiles: {len(speed_prof)} (area, day_type) cells, "
          f"window {pd.read_parquet(SPEED_PATH)['window'].iloc[0]}")
    sm_full = slowness_mult_matrix(cams, cam_area, day_types_holdout, speed_prof, 1.0)
    sm_half = slowness_mult_matrix(cams, cam_area, day_types_holdout, speed_prof, 0.5)
    print(f"slowness multipliers: min {sm_full.min():.3f} max {sm_full.max():.3f} "
          f"mean {sm_full.mean():.4f}")
    pre_prof = rebuild_demand_preholdout()
    dm_pre = _demand_mult_matrix(cams, cam_area, day_types_holdout, pre_prof)
    print(f"demand multipliers (pre-holdout): min {dm_pre.min():.3f} "
          f"max {dm_pre.max():.3f} mean {dm_pre.mean():.4f}")

    def _renorm(raw_day):
        denom = raw_day.sum(axis=2, keepdims=True)
        return raw_day / np.where(denom > 0, denom, 1.0)

    sm_t = sm_full[:, day_of_hold, hour_of_t]
    smh_t = sm_half[:, day_of_hold, hour_of_t]
    dm_t = dm_pre[:, day_of_hold, hour_of_t]
    wx_t = wx[day_of_hold, hour_of_t]
    base = rate_pre[:, None] * w_pre[:, hour_of_t]

    def _prof_variant(extra_t=None):
        raw = base if extra_t is None else base * extra_t
        return (_renorm(raw.reshape(n_c, 90, 24))
                * rate_pre[:, None, None]).reshape(n_c, n_t_hold)

    pred_prof = _prof_variant()
    pred_profwx = _prof_variant(wx_t)
    pred_prof_slow = _prof_variant(sm_t)
    pred_prof_slow_half = _prof_variant(smh_t)
    pred_profwx_slow = _prof_variant(wx_t * sm_t)
    pred_prof_slow_dem = _prof_variant(sm_t * dm_t)
    pred_profwx_slow_dem = _prof_variant(wx_t * sm_t * dm_t)

    # --- XGBoost shipped model (verbatim Phase 2b construction) --------------
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
    print("scoring shipped XGBoost on holdout ...")
    pred_xgb_raw = np.clip(reg.predict(X), 0, None).reshape(n_c, n_t_hold)
    xgb_day = pred_xgb_raw.reshape(n_c, 90, 24)
    pred_xgb_shape = (_renorm(xgb_day)
                      * rate_pre[:, None, None]).reshape(n_c, n_t_hold)
    pred_xgb_slow = (_renorm(xgb_day * sm_full)
                     * rate_pre[:, None, None]).reshape(n_c, n_t_hold)
    pred_xgb_slow_half = (_renorm(xgb_day * sm_half)
                          * rate_pre[:, None, None]).reshape(n_c, n_t_hold)
    pred_xgb_slow_dem = (_renorm(xgb_day * sm_full * dm_pre)
                         * rate_pre[:, None, None]).reshape(n_c, n_t_hold)

    variants = {
        "profile_only_preholdout": pred_prof,
        "profile_plus_weather": pred_profwx,
        "profile_plus_slowness": pred_prof_slow,
        "profile_plus_slowness_half": pred_prof_slow_half,
        "profile_plus_weather_plus_slowness": pred_profwx_slow,
        "profile_plus_slowness_plus_demand": pred_prof_slow_dem,
        "profile_plus_weather_plus_slowness_plus_demand": pred_profwx_slow_dem,
        "xgboost_raw_counts": pred_xgb_raw,
        "xgboost_shape_x_preholdout_rate": pred_xgb_shape,
        "xgboost_shape_plus_slowness": pred_xgb_slow,
        "xgboost_shape_plus_slowness_half": pred_xgb_slow_half,
        "xgboost_shape_plus_slowness_plus_demand": pred_xgb_slow_dem,
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
                    "note": ("Same protocol as validate_hourly.py / "
                             "validate_hourly_demand.py. Slowness from "
                             "speed_profiles.parquet (trips 2023-01-01 -> "
                             "2026-07-31, pre-holdout); demand from "
                             "pre-holdout rebuild (trips through "
                             "2026-07-31). All shape layers renormalised "
                             "per camera-day; level = pre-holdout daily "
                             "crash rate.")},
        "slowness_multipliers_holdout": {
            "min": float(sm_full.min()), "max": float(sm_full.max()),
            "mean": float(sm_full.mean()),
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
        print(f"{name:48s} dev {m['mean_poisson_deviance']:.4f}  "
              f"RMSE {m['rmse']:.4f}  MAE {m['mae']:.4f}  "
              f"pred {m['pred_total']:,.0f} vs actual {m['actual_total']:,.0f}  "
              f"share r={rp:.3f} rho={rs:.3f}  peak {m['pred_peak_hour']}:00")

    # --- per-hour calibration for the main slowness variants ------------------
    calib = {}
    for name in ("profile_only_preholdout", "profile_plus_slowness",
                 "profile_plus_slowness_plus_demand",
                 "xgboost_shape_x_preholdout_rate",
                 "xgboost_shape_plus_slowness",
                 "xgboost_shape_plus_slowness_plus_demand"):
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

    # --- slowness-vs-crash correlation ---------------------------------------
    n_days_by_type = {dt: int((day_types_holdout == dt).sum())
                      for dt in ("weekday", "weekend", "holiday")}
    slow_cw = np.zeros(24)
    for dt, nd in n_days_by_type.items():
        speeds = speed_prof[(0, dt)]
        slow = 1.0 / speeds
        slow_cw += nd * (slow / slow.sum())
    slow_cw /= slow_cw.sum()
    o_share = obs_hour_totals / obs_hour_totals.sum()
    r_cw = float(pearsonr(slow_cw, o_share).statistic)
    rho_cw = float(spearmanr(slow_cw, o_share).statistic)
    print(f"\ncitywide slowness-vs-crash share: Pearson r={r_cw:.3f} "
          f"rho={rho_cw:.3f} (slowness peak {int(np.argmax(slow_cw))}:00 "
          f"vs observed {obs_peak}:00)")
    y_day = y.reshape(n_c, 90, 24)
    area_rows = []
    for area in sorted(set(cam_area.values())):
        if area == 0:
            continue
        w_mix = np.zeros(24)
        for dt, nd in n_days_by_type.items():
            speeds = speed_prof.get((area, dt), speed_prof[(0, dt)])
            slow = 1.0 / speeds
            w_mix += nd * (slow / slow.sum())
        w_mix /= w_mix.sum()
        idx = [i for i, c in enumerate(cams) if cam_area.get(c, 0) == area]
        if not idx:
            continue
        obs_a = y_day[idx].sum(axis=(0, 1))
        tot = obs_a.sum()
        if tot < 20:
            continue
        os_a = obs_a / tot
        area_rows.append({
            "area": int(area),
            "n_cameras": len(idx),
            "observed_crashes": float(tot),
            "pearson_r": float(pearsonr(w_mix, os_a).statistic),
            "spearman_rho": float(spearmanr(w_mix, os_a).statistic),
            "slowness_peak_hour": int(np.argmax(w_mix)),
            "observed_peak_hour": int(np.argmax(os_a)),
        })
    rs = np.array([r["pearson_r"] for r in area_rows])
    print(f"per-area slowness-vs-crash: n_areas={len(area_rows)} "
          f"median r={np.median(rs):.3f} mean r={rs.mean():.3f} "
          f"min r={rs.min():.3f} (area "
          f"{area_rows[int(np.argmin(rs))]['area']}) "
          f"max r={rs.max():.3f}")
    results["slowness_crash_correlation"] = {
        "citywide_holdout_mix": {"pearson_r": r_cw, "spearman_rho": rho_cw,
                                 "slowness_peak_hour": int(np.argmax(slow_cw)),
                                 "observed_peak_hour": obs_peak,
                                 "day_counts": n_days_by_type},
        "per_area": {
            "n_areas": len(area_rows),
            "median_pearson_r": float(np.median(rs)),
            "mean_pearson_r": float(rs.mean()),
            "min_pearson_r": float(rs.min()),
            "max_pearson_r": float(rs.max()),
            "n_areas_r_above_0.8": int((rs > 0.8).sum()),
            "n_areas_r_below_0.5": int((rs < 0.5).sum()),
            "areas": area_rows,
        },
    }

    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    main()
