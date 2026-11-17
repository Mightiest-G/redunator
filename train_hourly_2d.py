#!/usr/bin/env python3
"""Redunator Phase 2d — XGBoost retrain with demand + trip-speed features (worker 3).

Extends the shipped Poisson XGBoost (`hourly_model.py`, 12 features) with
two new per-(camera, hour) features, both built from PRE-HOLDOUT
profiles only:

  * demand_share — trip-demand weight for the camera's community area,
    the date's day type and the hour, from pre-holdout demand profiles
    (trips 2023-01-01 -> 2026-07-31, rebuilt via
    validate_hourly_demand.rebuild_demand_preholdout). Range ~0.007-0.07.
  * trip_slowness — 1 / mean trip speed (mph) for the same
    (area, day type, hour), from speed_profiles.parquet (trips
    2023-01-01 -> 2026-07-31, pre-holdout by construction).
    Range ~0.023-0.085 (1/mph). Raw trip speed is NOT added as a
    third feature: it is a monotone transform of slowness, so a tree
    gains no new split from it.

Everything else mirrors the shipped model exactly: same target
(hourly crash counts within 200 m, PROXY), same other 12 features
(same construction as hourly_model.build_poisson_design), same
hyperparameters (count:poisson, 300 rounds, depth 6, lr 0.05,
subsample/colsample 0.8, hist, seed 42), same train/test split for
variant A. Unmapped cameras (1623, 2552, 2592, 2662) fall back to the
citywide (area 0) demand/speed profiles — no camera-hour is dropped
for a missing feature; every feature is defined for every row.

Variants trained here:
  A — same split as the shipped model (train 2025-01-01 -> 2026-02-28;
      log_cam_rate through 2026-02-28), so the ONLY difference from
      the shipped model is the two new features. Evaluated on the
      worker-B in-window test (2026-03-01 -> 2026-04-30) AND on the
      Phase 2 holdout (2026-08-01 -> 2026-10-29).
  B — train on the full pre-holdout window (2025-01-01 -> 2026-07-31;
      log_cam_rate through 2026-07-31), the model that would actually
      ship if it wins. Evaluated on the holdout only (no in-window
      test exists outside its training window).
  A-demand-only / A-slowness-only — single-feature ablations on the
      A split, for attribution (which new feature helps or hurts).

Candidate models are saved as hourly_model_2d_<variant>.joblib and
NEVER overwrite hourly_model.joblib. Metrics go to
hourly_model_2d_metrics.json. Nothing is wired into hourly_risk.py
here — wiring happens only for a variant that beats the bar
(XGB shape 0.093335 / raw 0.093342 on the holdout).

Usage:
    python train_hourly_2d.py --variants A B
    python train_hourly_2d.py --variants A-demand-only A-slowness-only
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import holidays
import numpy as np
import pandas as pd

import hourly_model as hm
from validate_hourly import _metrics
from validate_hourly_demand import rebuild_demand_preholdout

BASE = Path(__file__).resolve().parent
OUT_METRICS = BASE / "hourly_model_2d_metrics.json"

HOLDOUT_START = pd.Timestamp("2026-08-01")
HOLDOUT_END = pd.Timestamp("2026-10-29")
N_PRE_DAYS = 577  # 2025-01-01 -> 2026-07-31

FEATURES_2D = hm.POISSON_FEATURES + ["demand_share", "trip_slowness"]
_RATE_FLOOR = 0.01

_cache: dict = {}


def _day_type_idx(dates: pd.DatetimeIndex) -> np.ndarray:
    """0=weekday, 1=weekend, 2=holiday (holiday takes precedence)."""
    if "us_holidays" not in _cache:
        _cache["us_holidays"] = holidays.US(years=range(2019, 2027))
    us = _cache["us_holidays"]
    out = np.zeros(len(dates), dtype=np.int64)
    for i, d in enumerate(dates):
        if d.date() in us:
            out[i] = 2
        elif d.dayofweek >= 5:
            out[i] = 1
    return out


def _profile_tables():
    """(demand_table, slowness_table): each [78 areas, 3 day types, 24 h].
    Demand: pre-holdout rebuild (trips through 2026-07-31). Slowness:
    1/speed from speed_profiles.parquet (pre-holdout by construction).
    Area index = area code (0 = citywide)."""
    if "tables" in _cache:
        return _cache["tables"]
    dem = rebuild_demand_preholdout()
    dem_table = np.zeros((78, 3, 24))
    for (area, dt), w in dem.items():
        di = {"weekday": 0, "weekend": 1, "holiday": 2}[dt]
        dem_table[area, di, :] = w
    # Any missing (area, day_type) cell falls back to citywide.
    for di in range(3):
        for a in range(78):
            if dem_table[a, di, :].sum() == 0:
                dem_table[a, di, :] = dem_table[0, di, :]
    sp = pd.read_parquet(BASE / "data" / "hourly" / "processed"
                         / "speed_profiles.parquet")
    slow_table = np.zeros((78, 3, 24))
    for (area, dt), sub in sp.groupby(["area", "day_type"]):
        di = {"weekday": 0, "weekend": 1, "holiday": 2}[str(dt)]
        s = np.zeros(24)
        sub = sub.sort_values("hour")
        s[sub["hour"].to_numpy(dtype=int)] = sub["mean_speed_mph"].to_numpy(dtype=float)
        slow_table[int(area), di, :] = 1.0 / s
    for di in range(3):
        for a in range(78):
            if slow_table[a, di, :].sum() == 0:
                slow_table[a, di, :] = slow_table[0, di, :]
    _cache["tables"] = (dem_table, slow_table)
    return _cache["tables"]


def _cam_area_idx(mats):
    """(n_c,) area code per camera (0 = citywide fallback)."""
    if "cam_area" in _cache:
        return _cache["cam_area"]
    area_map = hm.load_camera_area_map()
    m = {str(r["camera_id"]): (int(r["community_area"])
                               if pd.notna(r["community_area"]) else 0)
         for _, r in area_map.iterrows()}
    arr = np.array([m.get(c, 0) for c in mats["cams"]], dtype=np.int64)
    _cache["cam_area"] = arr
    return arr


def build_design_2d(mats, t_start, t_end, rate_end, extra=("demand", "slowness")):
    """Design matrix for camera-hours t_start..t_end (inclusive hours).

    Columns: the 12 shipped POISSON_FEATURES, then demand_share and/or
    trip_slowness as requested (order: demand, slowness). log_cam_rate
    uses crashes through `rate_end` (train-window rate, past only).
    speed_ratio / weather / trail7 exactly as in
    hourly_model.build_poisson_design. Returns (X, y, times_slice).
    """
    times = mats["times"]
    days = mats["days"]
    i0 = int((pd.Timestamp(t_start) - times[0]) / pd.Timedelta(hours=1))
    i1 = int((pd.Timestamp(t_end) - times[0]) / pd.Timedelta(hours=1))
    n_t = i1 - i0 + 1
    n_c = len(mats["cams"])
    d0 = int((pd.Timestamp(t_start).normalize() - days[0]).days)
    d1 = int((pd.Timestamp(t_end).normalize() - days[0]).days)
    hour_of_t = mats["hour_of_t"][i0:i1 + 1]
    dow_of_t = mats["dow_of_t"][i0:i1 + 1]
    crash_mat = mats["crash_mat"][:, i0:i1 + 1]
    speed_mat = mats["speed_mat"][:, i0:i1 + 1]
    trail7 = mats["trail7"][:, d0:d1 + 1]
    weather = {k: v[i0:i1 + 1] for k, v in mats["weather"].items()}

    rate_days = int((pd.Timestamp(rate_end) - days[0]).days) + 1
    cam_rate = np.maximum(
        mats["daily"][:, :rate_days].sum(axis=1) / rate_days, _RATE_FLOOR)

    # Speed ratio per camera-hour (observed or typical-speed fill).
    cam_seg = mats["cam_seg"]
    ff = mats["ff"]
    seg_hour_mean = mats["seg_hour_mean"]
    typ = np.full((n_c, n_t), np.nan, dtype=np.float32)
    ok_cam = cam_seg >= 0
    typ[ok_cam] = seg_hour_mean[cam_seg[ok_cam]][:, hour_of_t]
    cam_speed = np.full((n_c, n_t), np.nan, dtype=np.float32)
    cam_speed[ok_cam] = speed_mat[cam_seg[ok_cam]]
    cam_speed = np.where(np.isnan(cam_speed), typ, cam_speed)
    ff_cam = np.where(ok_cam, ff[np.maximum(cam_seg, 0)], np.nan)
    speed_ratio = np.where(np.isnan(cam_speed) | (ff_cam[:, None] <= 0),
                           1.0, cam_speed / ff_cam[:, None]).astype(np.float32)
    speed_ratio = np.clip(speed_ratio, 0.2, 1.5)

    trail7_h = np.repeat(trail7, 24, axis=1)
    log_trail7 = np.log(np.maximum(trail7_h, _RATE_FLOOR)).astype(np.float32)
    log_cam = np.log(cam_rate).astype(np.float32)

    n_feat = 12 + len(extra)
    N = n_c * n_t
    X = np.empty((N, n_feat), dtype=np.float32)
    X[:, 0] = np.tile(np.sin(2 * np.pi * hour_of_t / 24).astype(np.float32), n_c)
    X[:, 1] = np.tile(np.cos(2 * np.pi * hour_of_t / 24).astype(np.float32), n_c)
    X[:, 2] = np.tile(np.sin(2 * np.pi * dow_of_t / 7).astype(np.float32), n_c)
    X[:, 3] = np.tile(np.cos(2 * np.pi * dow_of_t / 7).astype(np.float32), n_c)
    X[:, 4] = np.tile((dow_of_t >= 5).astype(np.float32), n_c)
    X[:, 5] = speed_ratio.reshape(-1)
    X[:, 6] = np.tile(weather["precip"].astype(np.float32), n_c)
    X[:, 7] = np.tile(weather["snow"].astype(np.float32), n_c)
    X[:, 8] = np.tile((weather["temp"] / 10.0).astype(np.float32), n_c)
    X[:, 9] = np.tile((weather["wind"] / 10.0).astype(np.float32), n_c)
    X[:, 10] = np.repeat(log_cam, n_t)
    X[:, 11] = log_trail7.reshape(-1)
    col = 12
    if extra:
        dem_table, slow_table = _profile_tables()
        cam_area = _cam_area_idx(mats)
        dates = pd.date_range(pd.Timestamp(t_start).normalize(),
                              pd.Timestamp(t_end).normalize(), freq="D")
        dt_idx = _day_type_idx(dates)
        dt_per_t = np.repeat(dt_idx, 24)  # per hour in window
        assert len(dt_per_t) == n_t
        if "demand" in extra:
            dem_mat = dem_table[cam_area[:, None], dt_per_t[None, :],
                                hour_of_t[None, :]]
            X[:, col] = dem_mat.reshape(-1).astype(np.float32)
            col += 1
        if "slowness" in extra:
            slow_mat = slow_table[cam_area[:, None], dt_per_t[None, :],
                                  hour_of_t[None, :]]
            X[:, col] = slow_mat.reshape(-1).astype(np.float32)
            col += 1
    y = crash_mat.reshape(-1).astype(np.float32)
    return X, y, times[i0:i1 + 1]


def _fit_xgb(X_train, y_train):
    import xgboost as xgb

    reg = xgb.XGBRegressor(
        objective="count:poisson", n_estimators=300, max_depth=6,
        learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
        tree_method="hist", n_jobs=3, random_state=42)
    reg.fit(X_train, y_train)
    return reg


def _holdout_scores(reg, mats, rate_end, extra, feature_names):
    """Score a fitted model on the Phase 2 holdout: raw counts and
    shape x pre-holdout rate (same level as validate_hourly.py)."""
    Xh, yh = build_design_2d(mats, HOLDOUT_START, HOLDOUT_END + pd.Timedelta(hours=23),
                             rate_end, extra=extra)[:2]
    pred_raw = np.clip(reg.predict(Xh), 0, None)
    m_raw = _metrics(yh, pred_raw)
    # Shape-only: normalise per camera-day, scale by pre-holdout rate.
    n_c = len(mats["cams"])
    daily = mats["daily"]
    pre_day_idx = int((pd.Timestamp("2026-07-31") - mats["days"][0]).days) + 1
    rate_pre = daily[:, :pre_day_idx].sum(axis=1) / N_PRE_DAYS
    day = pred_raw.reshape(n_c, 90, 24)
    denom = day.sum(axis=2, keepdims=True)
    shape = (day / np.where(denom > 0, denom, 1.0)
             * rate_pre[:, None, None]).reshape(-1)
    m_shape = _metrics(yh, shape)
    # Peak hour (shape totals).
    ph_tot = shape.reshape(n_c, 90, 24).sum(axis=(0, 1))
    m_shape["pred_peak_hour"] = int(np.argmax(ph_tot))
    m_raw["pred_peak_hour"] = int(np.argmax(
        pred_raw.reshape(n_c, 90, 24).sum(axis=(0, 1))))
    return {"raw_counts": m_raw, "shape_x_preholdout_rate": m_shape}


def run_variant(name, mats, train_end, rate_end, extra, in_window_test=True):
    t0 = time.time()
    print(f"\n=== variant {name}: train 2025-01-01 -> {train_end}, "
          f"features {['demand_share', 'trip_slowness'] if len(extra) == 2 else extra} ===")
    X_train, y_train, _ = build_design_2d(
        mats, "2025-01-01", pd.Timestamp(train_end) + pd.Timedelta(hours=23),
        rate_end, extra=extra)
    print(f"train rows {len(X_train):,}  features {X_train.shape[1]}")
    reg = _fit_xgb(X_train, y_train)
    print(f"fit done in {time.time() - t0:.0f}s")
    out = {"train": f"2025-01-01 -> {train_end}", "rate_end": rate_end,
           "features": hm.POISSON_FEATURES + [
               f for f in ("demand_share", "trip_slowness")
               if f.split('_')[0] in [e for e in extra] or
               (f == "demand_share" and "demand" in extra) or
               (f == "trip_slowness" and "slowness" in extra)],
           "train_rows": int(len(X_train)),
           "feature_importances_gain": {
               f: float(v) for f, v in zip(
                   hm.POISSON_FEATURES + [
                       "demand_share" if "demand" in extra else None,
                       "trip_slowness" if "slowness" in extra else None],
                   reg.feature_importances_) if f is not None},
           }
    # Fix feature list ordering to match X columns.
    feats = hm.POISSON_FEATURES.copy()
    if "demand" in extra:
        feats.append("demand_share")
    if "slowness" in extra:
        feats.append("trip_slowness")
    out["features"] = feats
    out["feature_importances_gain"] = {
        f: float(v) for f, v in zip(feats, reg.feature_importances_)}
    if in_window_test:
        Xt, yt, _ = build_design_2d(
            mats, "2026-03-01", pd.Timestamp("2026-04-30") + pd.Timedelta(hours=23),
            rate_end, extra=extra)
        m_test = _metrics(yt, np.clip(reg.predict(Xt), 0, None))
        out["in_window_test_mar_apr"] = m_test
        print(f"in-window test Mar-Apr: dev {m_test['mean_poisson_deviance']:.4f} "
              f"RMSE {m_test['rmse']:.4f} MAE {m_test['mae']:.4f} "
              f"pred {m_test['pred_total']:.0f} vs actual {m_test['actual_total']:.0f} "
              f"(shipped XGB 0.0879 / profile 0.0892)")
    hold = _holdout_scores(reg, mats, rate_end, extra, feats)
    out["holdout"] = hold
    print(f"holdout raw:   dev {hold['raw_counts']['mean_poisson_deviance']:.4f} "
          f"RMSE {hold['raw_counts']['rmse']:.4f} MAE {hold['raw_counts']['mae']:.4f} "
          f"pred {hold['raw_counts']['pred_total']:.0f} peak "
          f"{hold['raw_counts']['pred_peak_hour']}:00 (bar raw 0.093342)")
    print(f"holdout shape: dev {hold['shape_x_preholdout_rate']['mean_poisson_deviance']:.4f} "
          f"RMSE {hold['shape_x_preholdout_rate']['rmse']:.4f} "
          f"MAE {hold['shape_x_preholdout_rate']['mae']:.4f} "
          f"pred {hold['shape_x_preholdout_rate']['pred_total']:.0f} peak "
          f"{hold['shape_x_preholdout_rate']['pred_peak_hour']}:00 (bar shape 0.093335)")
    import joblib

    path = BASE / f"hourly_model_2d_{name}.joblib"
    joblib.dump(reg, path)
    print(f"saved candidate {path} (does NOT replace hourly_model.joblib)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="A",
                    help="comma-separated: A, B, B12, A-demand-only, A-slowness-only")
    args = ap.parse_args()
    wanted = [v.strip() for v in args.variants.split(",") if v.strip()]
    print("loading matrices ...")
    mats = hm._poisson_matrices()
    results = {}
    if OUT_METRICS.exists():
        results = json.loads(OUT_METRICS.read_text())
    for name in wanted:
        if name == "A":
            results["A"] = run_variant(
                "A", mats, "2026-02-28", "2026-02-28",
                extra=("demand", "slowness"), in_window_test=True)
        elif name == "B":
            results["B"] = run_variant(
                "B", mats, "2026-07-31", "2026-07-31",
                extra=("demand", "slowness"), in_window_test=False)
        elif name == "B12":
            # Attribution control: the shipped 12 features, trained on
            # the FULL pre-holdout window. If B12 wins by as much as B,
            # B's win is the longer training window, not the new features.
            results["B12"] = run_variant(
                "B12", mats, "2026-07-31", "2026-07-31",
                extra=(), in_window_test=False)
        elif name == "A-demand-only":
            results["A-demand-only"] = run_variant(
                "A-demand-only", mats, "2026-02-28", "2026-02-28",
                extra=("demand",), in_window_test=True)
        elif name == "A-slowness-only":
            results["A-slowness-only"] = run_variant(
                "A-slowness-only", mats, "2026-02-28", "2026-02-28",
                extra=("slowness",), in_window_test=True)
        else:
            raise ValueError(f"unknown variant {name!r}")
        OUT_METRICS.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {OUT_METRICS}")


if __name__ == "__main__":
    main()
