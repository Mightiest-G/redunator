#!/usr/bin/env python3
"""Redunator Phase 1 (item 2) — derive per-hour risk bands.

Mirrors risk.py's derivation exactly, but on the HOURLY population:
risk.py derived the daily bands from the distribution of the shipped
daily model's predictions on the holdout (26,752 camera-days);
here the bands come from the distribution of the shipped DEFAULT
hourly output (`predict_hourly` with default settings: model path,
demand OFF) on the SAME holdout, i.e. 26,752 camera-days x 24 hours
= 642,048 predicted camera-hours.

Why not just call predict_hourly() 26,752 times: each call rebuilds
the Poisson matrices and reloads both models (~10 s/call, ~3 days
total). Instead this script loads the daily model, the hourly model,
and the Poisson matrices ONCE, then builds the full design matrix
vectorised, mirroring hourly_model.model_weights() feature-for-feature
(speed ratio = segment typical speed for holdout dates, since the
congestion feed is dead after 2026-04-30; log_cam_rate through
2026-07-31, the B12 training-window end). Equivalence is asserted
below against live predict_hourly() calls on a sample of camera-days.

The 4 coordinate-less cameras (1623, 2552, 2592, 2662) are not in
the Poisson matrices, so predict_hourly falls back to the profile
path for them (profile + weather + slowness, the shipped profile
default). They are included here through the same profile path,
replicated from hourly_model helpers, so the population is exactly
what the default API returns.

Outputs:
  data/processed/hourly_risk_thresholds.json — quantiles + thresholds
  hourly_bands_derivation.json — full derivation numbers (this run)

Usage:
  ~/workspace/redunator-venv/bin/python derive_hourly_bands.py
"""

import json
import math
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

import hourly_model as hm

BASE = Path(__file__).resolve().parent
FEATURES_PATH = BASE / "data" / "processed" / "red_light_features.parquet"
DAILY_MODEL_PATH = BASE / "redunator_brain_v1.joblib"
SIDECAR_PATH = BASE / "redunator_brain_v1.json"
HOURLY_MODEL_PATH = BASE / "hourly_model.joblib"
THRESHOLDS_PATH = BASE / "data" / "processed" / "hourly_risk_thresholds.json"
OUT_PATH = BASE / "hourly_bands_derivation.json"

TEST_START = "2026-08-01"
TEST_END = "2026-10-29"
RATE_END = pd.Timestamp(hm.SHIPPED_MODEL_RATE_END)  # "2026-07-31"


def daily_predictions_holdout():
    """Daily predictions for every holdout camera-day (risk.py method)."""
    from train import FEATURES  # reuse the shipped feature list

    sidecar = json.loads(SIDECAR_PATH.read_text())
    assert sidecar["features"] == FEATURES
    model = joblib.load(DAILY_MODEL_PATH)

    df = pd.read_parquet(FEATURES_PATH)
    df["date"] = pd.to_datetime(df["date"])
    train_cams = sorted(df.loc[df["date"] <= "2026-07-31", "camera_id"].unique())
    code_map = {c: i for i, c in enumerate(train_cams)}
    df["cam_code"] = df["camera_id"].map(code_map).astype("int32")
    df["lag_1d"] = df["lag_1d"].fillna(0)
    df["roll_mean_7d"] = df["roll_mean_7d"].fillna(0)
    df["roll_mean_30d"] = df["roll_mean_30d"].fillna(0)
    df["latitude"] = df["latitude"].fillna(df["latitude"].median())
    df["longitude"] = df["longitude"].fillna(df["longitude"].median())

    test = df[(df["date"] >= TEST_START) & (df["date"] <= TEST_END)].copy()
    X_test = test[FEATURES].to_numpy(dtype=np.float32)
    pred = np.clip(model.predict(X_test), 0, None)
    test["predicted"] = pred
    return test[["camera_id", "date", "predicted"]].reset_index(drop=True)


def model_path_hourly(daily_df, mats, reg):
    """Vectorised mirror of hourly_model.model_weights() for holdout days.

    Returns dict (camera_id, date) -> 24 weights summing to 1, for
    cameras present in mats["cam_index"]. Feature construction mirrors
    model_weights() line for line (see its docstring/source).
    """
    days = mats["days"]
    day0 = days[0]
    train_days = int((RATE_END - day0).days) + 1
    cam_index = mats["cam_index"]
    daily_mat = mats["daily"]
    trail7 = mats["trail7"]
    weather = mats["weather"]
    seg_hour_mean = mats["seg_hour_mean"]
    ff = mats["ff"]
    cam_seg = mats["cam_seg"]
    hour_of_t = mats["hour_of_t"]
    dow_of_t = mats["dow_of_t"]

    # Camera daily crash rate over the shipped model's training window.
    cam_rate = np.maximum(
        daily_mat[:, :train_days].sum(axis=1) / train_days, hm._RATE_FLOOR)
    log_cam = np.log(cam_rate)

    holdout_days = pd.date_range(TEST_START, TEST_END, freq="D")
    day_idx = np.array([(d - day0).days for d in holdout_days])
    # All (camera, day) pairs present in the daily holdout table.
    cams = sorted(daily_df["camera_id"].unique())
    model_cams = [c for c in cams if c in cam_index]
    n_c, n_d = len(model_cams), len(holdout_days)
    ci = np.array([cam_index[c] for c in model_cams])

    # Speed ratio per (camera, hour): holdout dates are all past the
    # dead congestion feed, so observed speed is never available and
    # the segment typical speed is used — exactly as model_weights().
    ratio = np.ones((n_c, 24), dtype=np.float32)
    ok = cam_seg[ci] >= 0
    for j in np.where(ok)[0]:
        seg = cam_seg[ci[j]]
        f = ff[seg]
        if np.isnan(f) or f <= 0:
            continue
        typ = seg_hour_mean[seg]  # (24,) typical speed by hour
        r = np.where(np.isnan(typ), 1.0, typ / f)
        ratio[j] = np.clip(r, 0.2, 1.5).astype(np.float32)

    # Weather per (day, hour): rows of the mats weather arrays.
    # mats["times"] is hourly 2025-01-01 .. 2026-10-29 23:00.
    t_idx = np.array([int((d - pd.Timestamp("2025-01-01")).total_seconds() // 3600)
                      for d in holdout_days])  # start hour of each day
    precip = np.stack([weather["precip"][t:t + 24] for t in t_idx]).astype(np.float32)
    snow = np.stack([weather["snow"][t:t + 24] for t in t_idx]).astype(np.float32)
    temp = (np.stack([weather["temp"][t:t + 24] for t in t_idx]) / 10.0).astype(np.float32)
    wind = (np.stack([weather["wind"][t:t + 24] for t in t_idx]) / 10.0).astype(np.float32)

    hour_sin = np.sin(2 * np.pi * np.arange(24) / 24).astype(np.float32)
    hour_cos = np.cos(2 * np.pi * np.arange(24) / 24).astype(np.float32)
    dow = holdout_days.day_of_week.to_numpy()
    dow_sin = np.sin(2 * np.pi * dow / 7).astype(np.float32)
    dow_cos = np.cos(2 * np.pi * dow / 7).astype(np.float32)
    is_wknd = (dow >= 5).astype(np.float32)

    # Trailing-7d rate per (camera, day), same rule as model_weights():
    # trail7 if > 0 else the camera's training-window rate.
    trail = trail7[np.ix_(ci, day_idx)]  # (n_c, n_d)
    trail = np.where(trail > 0, np.maximum(trail, hm._RATE_FLOOR),
                     cam_rate[ci][:, None])
    log_trail = np.log(trail).astype(np.float32)

    # Design matrix: (n_c * n_d * 24, 12), camera-major, then day, hour.
    N = n_c * n_d * 24
    X = np.empty((N, 12), dtype=np.float32)
    X[:, 0] = np.tile(hour_sin, n_c * n_d)
    X[:, 1] = np.tile(hour_cos, n_c * n_d)
    X[:, 2] = np.tile(np.repeat(dow_sin, 24), n_c)
    X[:, 3] = np.tile(np.repeat(dow_cos, 24), n_c)
    X[:, 4] = np.tile(np.repeat(is_wknd, 24), n_c)
    X[:, 5] = np.tile(ratio, (1, n_d)).reshape(-1)
    X[:, 6] = np.tile(precip.reshape(-1), n_c)
    X[:, 7] = np.tile(snow.reshape(-1), n_c)
    X[:, 8] = np.tile(temp.reshape(-1), n_c)
    X[:, 9] = np.tile(wind.reshape(-1), n_c)
    X[:, 10] = np.repeat(np.repeat(log_cam[ci], n_d), 24)
    X[:, 11] = np.repeat(log_trail.reshape(-1), 24)

    pred = np.clip(reg.predict(X), 0, None).astype(float)
    pred = pred.reshape(n_c, n_d, 24)
    totals = pred.sum(axis=2, keepdims=True)
    assert (totals > 0).all(), "a camera-day predicted zero total weight"
    weights = pred / totals
    wdict = {(model_cams[i], holdout_days[j]): weights[i, j]
             for i in range(n_c) for j in range(n_d)}
    return wdict, X, model_cams, holdout_days


def profile_path_hourly(daily_df, cams):
    """Shipped profile path (profile + weather + slowness), per camera-day.

    Replicates predict_hourly()'s profile branch with cached helpers.
    Used here for the 4 coordinate-less cameras that have no model
    weights, and for the profile-path sensitivity quantiles.
    """
    out = {}
    wx_cache, sm_cache = {}, {}
    for cam in cams:
        w = hm.profile_weights(cam)
        for date in pd.date_range(TEST_START, TEST_END, freq="D"):
            if date not in wx_cache:
                wx_cache[date] = hm.weather_multipliers_for_day(date)
            if (cam, date.day_of_week >= 5) not in sm_cache:
                sm_cache[(cam, date.day_of_week >= 5)] = \
                    hm.slowness_multipliers_for_day(cam, date)
            raw = w * wx_cache[date] * sm_cache[(cam, date.day_of_week >= 5)]
            # Congestion multiplier is 1.0 for every holdout date
            # (dead feed after 2026-04-30) — verified, not assumed:
            # hm.congestion_multipliers_for_day returns all-ones here.
            out[(cam, date)] = raw / raw.sum()
    return out


def main():
    t0 = time.time()
    daily_df = daily_predictions_holdout()
    print(f"holdout camera-days: {len(daily_df):,}  "
          f"cameras: {daily_df['camera_id'].nunique()}")
    daily_map = {(r.camera_id, r.date): float(r.predicted)
                 for r in daily_df.itertuples()}

    mats = hm._poisson_matrices()
    reg = joblib.load(HOURLY_MODEL_PATH)
    mw, _X, _cams, _days = model_path_hourly(daily_df, mats, reg)
    print(f"model-path camera-days: {len(mw):,} "
          f"({time.time() - t0:.0f}s)")

    cams = sorted(daily_df["camera_id"].unique())
    fallback_cams = [c for c in cams if c not in mats["cam_index"]]
    print(f"fallback (profile-path) cameras: {fallback_cams}")
    pw = {}
    if fallback_cams:
        pw = profile_path_hourly(daily_df, fallback_cams)

    # --- Population: exactly what default predict_hourly() returns -----
    hourly = np.empty(len(daily_df) * 24, dtype=float)
    for k, (cam, date) in enumerate(daily_map):
        w = mw.get((cam, date), pw.get((cam, date)))
        assert w is not None, f"no weights for {cam} {date}"
        hourly[k * 24:(k + 1) * 24] = daily_map[(cam, date)] * w
    assert len(hourly) == 26752 * 24

    qs = np.quantile(hourly, [0.5, 0.75, 0.9, 0.95, 0.99])
    print("hourly quantiles p50/p75/p90/p95/p99/max:",
          [round(float(q), 4) for q in qs], round(float(hourly.max()), 4))

    # --- Sensitivity: model-path-only and profile-path-only quantiles --
    hourly_model_only = np.concatenate([
        daily_map[(c, d)] * mw[(c, d)]
        for (c, d) in daily_map if (c, d) in mw]) if mw else np.array([])
    prof_all = profile_path_hourly(daily_df, cams)
    hourly_prof_only = np.concatenate([
        daily_map[(c, d)] * prof_all[(c, d)]
        for (c, d) in daily_map])
    qs_m = np.quantile(hourly_model_only, [0.5, 0.9, 0.99])
    qs_p = np.quantile(hourly_prof_only, [0.5, 0.9, 0.99])
    print("model-only p50/p90/p99:", [round(float(q), 4) for q in qs_m])
    print("profile-only p50/p90/p99:", [round(float(q), 4) for q in qs_p])

    # --- Thresholds: mirror risk.py (round p50 / p90), at hourly scale -
    # risk.py rounds to whole violations/day; hourly counts are ~24x
    # smaller, so rounding is to 0.1 violations/hour (1 decimal).
    med_cut = round(float(qs[0]), 1)
    high_cut = round(float(qs[2]), 1)
    scale = round(float(np.ceil(qs[4] / 0.5) * 0.5), 1)  # p99 up to 0.5
    shares = {
        "LOW": float((hourly < med_cut).mean()),
        "MEDIUM": float(((hourly >= med_cut) & (hourly < high_cut)).mean()),
        "HIGH": float((hourly >= high_cut).mean()),
    }
    print(f"thresholds: LOW < {med_cut}, MEDIUM {med_cut}-<{high_cut}, "
          f"HIGH >= {high_cut}; score scale {scale}")
    print("band shares:", {k: round(v, 4) for k, v in shares.items()})

    thresholds = {
        "derived_from": "shipped DEFAULT hourly output (predict_hourly, "
                        "model path, demand OFF) on the Phase 2 holdout "
                        "2026-08-01 -> 2026-10-29 (26,752 camera-days x "
                        "24 h = 642,048 camera-hours); the 4 coordinate-less "
                        "cameras use the shipped profile path (profile + "
                        "weather + slowness) exactly as predict_hourly does",
        "method": "same rule as risk.py (LOW/MEDIUM = round(p50), "
                  "MEDIUM/HIGH = round(p90)), rounded to 0.1 "
                  "violations/hour because hourly counts are ~24x smaller "
                  "than daily counts; score scale = p99 rounded up to "
                  "nearest 0.5",
        "hourly_count_quantiles": {
            "p50": round(float(qs[0]), 4),
            "p75": round(float(qs[1]), 4),
            "p90": round(float(qs[2]), 4),
            "p95": round(float(qs[3]), 4),
            "p99": round(float(qs[4]), 4),
            "max": round(float(hourly.max()), 4),
            "mean": round(float(hourly.mean()), 4),
        },
        "med_cut": med_cut,
        "high_cut": high_cut,
        "score_scale": scale,
        "bands": {
            "LOW": f"predicted < {med_cut} violations/hour (ESTIMATE)",
            "MEDIUM": f"{med_cut} <= predicted < {high_cut} violations/hour (ESTIMATE)",
            "HIGH": f"predicted >= {high_cut} violations/hour (ESTIMATE)",
        },
        "score": f"hourly risk_score = clip(predicted / {scale}, 0, 1) "
                 f"(NOT wired into hourly_risk.py yet — see findings doc)",
        "band_shares_holdout": {k: round(v, 4) for k, v in shares.items()},
        "sensitivity_quantiles": {
            "model_path_only_p50_p90_p99": [round(float(q), 4) for q in qs_m],
            "profile_path_only_p50_p90_p99": [round(float(q), 4) for q in qs_p],
        },
        "fallback_cameras_profile_path": fallback_cams,
        "script": "derive_hourly_bands.py",
    }
    THRESHOLDS_PATH.write_text(json.dumps(thresholds, indent=2))
    OUT_PATH.write_text(json.dumps(thresholds, indent=2))
    print(f"wrote {THRESHOLDS_PATH}")
    print(f"total time: {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
