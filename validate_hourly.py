#!/usr/bin/env python3
"""Redunator Phase 2b — INDEPENDENT validation of the hourly shape (worker C).

This is NOT a re-run of worker B's in-window comparison. Differences:

  * Holdout = the Phase 2 holdout window 2026-08-01 -> 2026-10-29, which
    worker B never evaluated (their split was 2026-03-01 -> 2026-04-30,
    inside the congestion window).
  * Profiles are REBUILT using only pre-holdout crashes
    (crash_end="2026-07-31", via hourly_model.build_profiles) so no
    holdout crash leaks into the shape being tested. The shipped
    profiles were built on the full window (through 2026-10-29) and
    therefore partly reflect holdout crashes; the drift between the two
    profile sets is measured and reported below.
  * Congestion does NOT exist in this holdout (feed dead 2026-04-30):
    the validation measures the shape the product actually has for
    holdout dates — profiles + weather only, congestion multiplier 1.0.
  * The shipped XGBoost Poisson model is scored on this holdout with
    feature construction mirroring hourly_model.model_weights exactly
    (same matrices, same train-window log_cam_rate, same trailing-7d
    rate, same typical-speed fill for the dead congestion feed).

Target = hourly crash counts within 200 m of each camera (PROXY — the
same proxy worker B used; hourly VIOLATIONS are unobservable).
Level for the profile variants = each camera's PRE-HOLDOUT daily crash
rate (attributed crashes 2025-01-01 -> 2026-07-31 / 577 days), so the
comparison tests shape (+ weather shaping) at a fixed, leakage-free
level. The XGB model is scored two ways: raw predicted counts (as
worker B scored it) and shape-only (its per-day weights x the same
pre-holdout rate).

Metrics: mean Poisson deviance (primary), RMSE, MAE, pred vs actual
total, plus Pearson/Spearman correlation of the 24 citywide hourly
shares, per-hour calibration (predicted vs observed share), and the
predicted vs observed peak hour.

Writes hourly_validation.json next to this script and prints a summary.
Reads only; modifies no Phase 2 or worker A/B artifact.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr
from sklearn.metrics import mean_poisson_deviance

import hourly_model as hm

BASE = Path(__file__).resolve().parent
OUT_PATH = BASE / "hourly_validation.json"

HOLDOUT_START = pd.Timestamp("2026-08-01")
HOLDOUT_END = pd.Timestamp("2026-10-29")
PRE_END = "2026-07-31"          # last day of pre-holdout crash data
N_PRE_DAYS = 577                # 2025-01-01 -> 2026-07-31
TRAIN_END = "2026-02-28"        # worker B's train window (log_cam_rate)
N_TRAIN_DAYS = 424              # 2025-01-01 -> 2026-02-28


def _metrics(y_true, y_pred):
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


def _share_corr(pred_hourly_totals, obs_hourly_totals):
    p = np.asarray(pred_hourly_totals, dtype=float)
    o = np.asarray(obs_hourly_totals, dtype=float)
    p_share = p / p.sum()
    o_share = o / o.sum()
    r_pearson = float(pearsonr(p_share, o_share).statistic)
    r_spearman = float(spearmanr(p_share, o_share).statistic)
    return p_share, o_share, r_pearson, r_spearman


def main():
    print("loading matrices + rebuilding pre-holdout profiles ...")
    mats = hm._poisson_matrices()
    times, days = mats["times"], mats["days"]
    cams, cam_index = mats["cams"], mats["cam_index"]
    n_c = len(cams)
    print(f"cameras in crash matrices: {n_c}")

    # --- pre-holdout profiles (no holdout crash leakage) -------------------
    prof_pre = hm.build_profiles(crash_end=PRE_END, cong_end="2026-04-30")
    w_pre = np.zeros((n_c, 24))
    n_pre = np.zeros(n_c, dtype=int)
    for i, cam in enumerate(cams):
        sub = prof_pre[prof_pre["camera_id"] == cam].sort_values("hour")
        assert len(sub) == 24, cam
        w_pre[i] = sub["weight"].to_numpy(dtype=float)
        n_pre[i] = int(sub["n_crashes"].iloc[0])
    print(f"pre-holdout profile crash counts per camera: min {n_pre.min()}, "
          f"median {int(np.median(n_pre))}, max {n_pre.max()}, "
          f"thin (n<50): {int((n_pre < 50).sum())}")

    # Drift: shipped (full-window) profiles vs pre-holdout profiles.
    shipped = hm.load_profiles()
    drift = []
    for i, cam in enumerate(cams):
        sub = shipped[shipped["camera_id"] == cam].sort_values("hour")
        drift.append(np.abs(sub["weight"].to_numpy(dtype=float) - w_pre[i]))
    drift = np.array(drift)
    print(f"shipped-vs-preholdout profile drift: mean |dw| {drift.mean():.4f}, "
          f"max |dw| {drift.max():.4f}")

    # --- citywide pre-holdout share ----------------------------------------
    cw = hm._load_crashes_citywide()
    cw_pre = cw[cw["timestamp"] < pd.Timestamp("2026-08-01")]
    city_counts = cw_pre.groupby("hour")["crash_count"].sum().reindex(
        range(24), fill_value=0)
    p_city_pre = (city_counts / city_counts.sum()).to_numpy(dtype=float)

    # --- rates --------------------------------------------------------------
    # daily crash totals per camera (mats["daily"]: days x 24 summed)
    daily = mats["daily"]
    pre_day_idx = int((pd.Timestamp(PRE_END) - days[0]).days) + 1  # 577 days
    train_day_idx = int((pd.Timestamp(TRAIN_END) - days[0]).days) + 1  # 424
    rate_pre = daily[:, :pre_day_idx].sum(axis=1) / N_PRE_DAYS
    rate_train = np.maximum(
        daily[:, :train_day_idx].sum(axis=1) / N_TRAIN_DAYS, 0.01)

    # --- holdout slices ------------------------------------------------------
    t0 = int((HOLDOUT_START - times[0]) / pd.Timedelta(hours=1))
    t1 = int((HOLDOUT_END + pd.Timedelta(hours=23) - times[0])
             / pd.Timedelta(hours=1)) + 1
    hold_times = times[t0:t1]
    n_t_hold = len(hold_times)
    assert n_t_hold == 90 * 24, n_t_hold
    y = mats["crash_mat"][:, t0:t1].astype(float)  # (n_c, n_t_hold)
    print(f"holdout camera-hours: {n_c * n_t_hold:,}  "
          f"observed crashes (attributed): {y.sum():,.0f}  "
          f"non-zero camera-hours: {int((y > 0).sum()):,}")

    hour_of_t = mats["hour_of_t"][t0:t1]
    dow_of_t = mats["dow_of_t"][t0:t1]
    day_idx_of_t = (hold_times.normalize() - days[0]).days.to_numpy()
    # day index within holdout (0..89) for the weather-multiplier lookup
    hold_day_numbers = np.arange(90)
    day_of_hold = ((hold_times.normalize() - HOLDOUT_START).days
                   .to_numpy())

    # --- weather multipliers per holdout day (90 x 24) ----------------------
    wx = np.ones((90, 24))
    for d in range(90):
        wx[d] = hm.weather_multipliers_for_day(HOLDOUT_START + pd.Timedelta(days=d))
    print(f"weather multipliers in holdout: min {wx.min():.3f}, "
          f"max {wx.max():.3f}, mean {wx.mean():.4f}, "
          f"hours > 1.0: {int((wx > 1.0).sum())} of {wx.size}")

    # Congestion multiplier spot check (must be 1.0: dead feed).
    for dstr in ("2026-08-15", "2026-10-15"):
        cg = hm.congestion_multipliers_for_day("1413", dstr)
        assert np.all(cg == 1.0), dstr
    print("congestion multipliers: all 1.0 on 2026-08-15 and 2026-10-15 (dead feed, as designed)")

    # --- predicted counts per variant (n_c, n_t_hold) ------------------------
    # (a) flat shape
    pred_flat = np.repeat((rate_pre / 24)[:, None], n_t_hold, axis=1)
    # (b) citywide pre-holdout shape
    pred_city = rate_pre[:, None] * p_city_pre[hour_of_t][None, :]
    # (c) per-camera pre-holdout profile
    pred_prof = rate_pre[:, None] * w_pre[:, hour_of_t]
    # (d) profile + weather multipliers (the shipped profile path), renormalised per camera-day
    raw = rate_pre[:, None] * w_pre[:, hour_of_t] * wx[day_of_hold, hour_of_t]
    raw_day = raw.reshape(n_c, 90, 24)
    denom = raw_day.sum(axis=2, keepdims=True)
    pred_profwx = (raw_day / np.where(denom > 0, denom, 1.0)
                   * rate_pre[:, None, None]).reshape(n_c, n_t_hold)

    # (e/f) XGBoost Poisson, feature construction mirroring model_weights.
    import joblib
    reg = joblib.load(hm.POISSON_MODEL_PATH)
    seg_hour_mean, ff, cam_seg = mats["seg_hour_mean"], mats["ff"], mats["cam_seg"]
    weather = mats["weather"]
    w_precip = weather["precip"][t0:t1]
    w_snow = weather["snow"][t0:t1]
    w_temp = weather["temp"][t0:t1]
    w_wind = weather["wind"][t0:t1]
    trail7 = mats["trail7"]  # (n_c, n_d)
    # speed ratio per camera-hour: typical-speed fill (no observed speeds in holdout)
    typ = np.full((n_c, n_t_hold), np.nan)
    ok = cam_seg >= 0
    typ[ok] = seg_hour_mean[cam_seg[ok]][:, hour_of_t]
    ff_cam = np.where(ok, ff[np.maximum(cam_seg, 0)], np.nan)
    speed_ratio = np.where(np.isnan(typ) | (ff_cam[:, None] <= 0),
                           1.0, typ / ff_cam[:, None])
    speed_ratio = np.clip(speed_ratio, 0.2, 1.5)
    # trailing-7d rate per camera-day (mirrors model_weights: 0 -> train rate)
    trail_day = trail7[:, day_idx_of_t[::24]]  # (n_c, 90)
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
    # shape-only: normalise per camera-day, scale by pre-holdout rate
    xgb_day = pred_xgb_raw.reshape(n_c, 90, 24)
    xgb_denom = xgb_day.sum(axis=2, keepdims=True)
    pred_xgb_shape = (xgb_day / np.where(xgb_denom > 0, xgb_denom, 1.0)
                      * rate_pre[:, None, None]).reshape(n_c, n_t_hold)

    variants = {
        "flat_uniform": pred_flat,
        "citywide_shape": pred_city,
        "profile_only_preholdout": pred_prof,
        "profile_plus_weather": pred_profwx,
        "xgboost_raw_counts": pred_xgb_raw,
        "xgboost_shape_x_preholdout_rate": pred_xgb_shape,
    }
    y_flat = y.reshape(-1)
    results = {
        "holdout": {"start": "2026-08-01", "end": "2026-10-29", "days": 90,
                    "cameras": int(n_c),
                    "camera_hours": int(N),
                    "observed_crashes_attributed": float(y.sum()),
                    "nonzero_camera_hours": int((y > 0).sum()),
                    "note": ("Profiles rebuilt on pre-holdout crashes only "
                             "(2025-01-01->2026-07-31); congestion absent "
                             "(feed dead 2026-04-30); target = hourly crash "
                             "counts within 200 m (PROXY).")},
        "preholdout_profiles": {
            "n_crashes_per_camera_min": int(n_pre.min()),
            "n_crashes_per_camera_median": int(np.median(n_pre)),
            "n_crashes_per_camera_max": int(n_pre.max()),
            "thin_n_lt_50": int((n_pre < 50).sum()),
            "shipped_vs_preholdout_mean_abs_weight_diff": float(drift.mean()),
            "shipped_vs_preholdout_max_abs_weight_diff": float(drift.max()),
        },
        "weather_multipliers_holdout": {
            "min": float(wx.min()), "max": float(wx.max()),
            "mean": float(wx.mean()),
            "hours_above_1": int((wx > 1.0).sum()), "hours_total": int(wx.size),
        },
        "variants": {},
    }

    obs_hour_totals = y.reshape(n_c, 90, 24).sum(axis=(0, 1))
    obs_peak = int(np.argmax(obs_hour_totals))
    print(f"\nobserved citywide peak hour (holdout): {obs_peak}:00 "
          f"(share {obs_hour_totals[obs_peak] / obs_hour_totals.sum():.4f})")
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
        print(f"{name:38s} dev {m['mean_poisson_deviance']:.4f}  "
              f"RMSE {m['rmse']:.4f}  MAE {m['mae']:.4f}  "
              f"pred {m['pred_total']:,.0f} vs actual {m['actual_total']:,.0f}  "
              f"share r={rp:.3f} rho={rs:.3f}  peak {m['pred_peak_hour']}:00")

    # --- per-hour calibration (profile+weather and xgb raw) ------------------
    calib = {}
    for name in ("profile_plus_weather", "xgboost_raw_counts", "profile_only_preholdout"):
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
        print(f"{name}: worst over-predicted hour {worst_over['hour']}:00 "
              f"(ratio {worst_over['pred_over_obs_ratio']:.2f}); "
              f"worst under-predicted hour {worst_under['hour']}:00 "
              f"(ratio {worst_under['pred_over_obs_ratio']:.2f})")
    results["per_hour_calibration"] = calib
    results["observed_peak_hour"] = obs_peak
    results["observed_hour_totals"] = [float(v) for v in obs_hour_totals]

    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    main()
