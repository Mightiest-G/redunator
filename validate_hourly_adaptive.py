#!/usr/bin/env python3
"""Redunator Phase 2d — validation of the improved trip-demand layer.

Same holdout protocol as Phase 2b/2c (`validate_hourly.py`,
`validate_hourly_demand.py`): holdout 2026-08-01 -> 2026-10-29
(90 days x 361 cameras = 779,760 camera-hours, 8,517 observed
attributed crashes). Profiles rebuilt on pre-holdout crashes only;
levels = each camera's pre-holdout daily crash rate. XGBoost scored
with feature construction mirroring `hourly_model.model_weights`
exactly (block copied verbatim from validate_hourly_demand.py).

Variants:
  Baselines (reproduction): profile_only_preholdout (0.0945),
    profile_plus_demand_preholdout (0.0949), xgboost_raw_counts
    (0.0933), xgboost_shape_x_preholdout_rate (0.0933).
  Task 1 — per-area adaptive trust: demand blend only in areas whose
    PRE-holdout demand-crash Pearson r clears a threshold; grid
    r > 0.5 / 0.6 / 0.7, profile path + XGB-shape path.
  Task 2 — daily-deviation nudge: 1 + alpha*(deviation - 1) on top of
    the crash profile, renormalised per camera-day. Same-day (post-hoc
    diagnostic), previous-day, and same-weekday-last-week sources;
    alpha grid 0.25 / 0.5 / 1.0 (same-day), 0.5 / 1.0 (forecastable).
  Task 3 — demand lag: crash hour h uses demand hour h+lag,
    lag in {-1, 0, +1}, profile path (+ XGB path if a lag wins).

Writes hourly_validation_adaptive.json next to this script.
Reads only; modifies no Phase 2 / 2b / 2c artifact and does not flip
any default in hourly_risk.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.stats import pearsonr

import hourly_model as hm
import hourly_adaptive as ha
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
    _day_type_series,
    _demand_mult_matrix,
    rebuild_demand_preholdout,
)

BASE = Path(__file__).resolve().parent
OUT_PATH = BASE / "hourly_validation_adaptive.json"

RECORDED = {
    "profile_only_preholdout": 0.09454932634961201,
    "profile_plus_demand_preholdout": 0.0949394217923261,
    "xgboost_raw_counts": 0.09334237333814034,
    "xgboost_shape_x_preholdout_rate": 0.09333512833917688,
}


def _renorm(raw_day):
    denom = raw_day.sum(axis=2, keepdims=True)
    return raw_day / np.where(denom > 0, denom, 1.0)


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
    day_of_hold = ((hold_times.normalize() - HOLDOUT_START).days
                   .to_numpy())
    hold_dates = pd.date_range(HOLDOUT_START, HOLDOUT_END, freq="D")
    day_types_holdout = _day_type_series(hold_dates)

    cam_area = {}
    area_map = hm.load_camera_area_map()
    for _, r in area_map.iterrows():
        cam_area[str(r["camera_id"])] = (
            int(r["community_area"]) if pd.notna(r["community_area"]) else 0)

    pre_prof = rebuild_demand_preholdout()  # trips 2023-01-01 -> 2026-07-31
    dm_pre = _demand_mult_matrix(cams, cam_area, day_types_holdout, pre_prof)

    # --- base predictions ----------------------------------------------------
    pred_prof = rate_pre[:, None] * w_pre[:, hour_of_t]
    raw_pd = rate_pre[:, None] * w_pre[:, hour_of_t] * dm_pre[
        :, day_of_hold, hour_of_t]
    pred_prof_demand_pre = (_renorm(raw_pd.reshape(n_c, 90, 24))
                            * rate_pre[:, None, None]).reshape(n_c, n_t_hold)

    # --- XGBoost (verbatim Phase 2b/2c construction) -------------------------
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

    variants = {
        "profile_only_preholdout": pred_prof,
        "profile_plus_demand_preholdout": pred_prof_demand_pre,
        "xgboost_raw_counts": pred_xgb_raw,
        "xgboost_shape_x_preholdout_rate": pred_xgb_shape,
    }

    y_flat = y.reshape(-1)
    obs_hour_totals = y.reshape(n_c, 90, 24).sum(axis=(0, 1))
    obs_peak = int(np.argmax(obs_hour_totals))
    results = {
        "holdout": {"start": "2026-08-01", "end": "2026-10-29", "days": 90,
                    "cameras": int(n_c), "camera_hours": int(N),
                    "observed_crashes_attributed": float(y.sum()),
                    "observed_peak_hour": obs_peak},
        "variants": {},
        "reproduction_check": {},
    }

    def score(name, pred):
        variants[name] = pred  # retain for per-hour calibration below
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
        print(f"{name:44s} dev {m['mean_poisson_deviance']:.4f}  "
              f"RMSE {m['rmse']:.4f}  MAE {m['mae']:.4f}  "
              f"pred {m['pred_total']:,.0f} vs actual {m['actual_total']:,.0f}  "
              f"share r={rp:.3f}  peak {m['pred_peak_hour']}:00")
        return m

    # =====================================================================
    # Task 1 — per-area adaptive trust (PRE-holdout correlations)
    # =====================================================================
    print("\n--- Task 1: per-area adaptive trust ---")
    pre_dates = pd.date_range("2025-01-01", "2026-07-31", freq="D")
    day_types_pre = _day_type_series(pre_dates)
    area_rows, n_days_pre = ha.preholdout_area_correlations(
        mats, cam_area, pre_prof, day_types_pre, min_crashes=20)
    rs = np.array([r["pearson_r"] for r in area_rows
                   if r["pearson_r"] is not None])
    print(f"pre-holdout per-area correlations: n_areas={len(area_rows)} "
          f"(r defined for {len(rs)}), median r={np.median(rs):.3f} "
          f"mean r={rs.mean():.3f} min r={rs.min():.3f} "
          f"(area {area_rows[int(np.argmin([r['pearson_r'] if r['pearson_r'] is not None else 9 for r in area_rows]))]['area']}) "
          f"max r={rs.max():.3f}")
    print(f"pre-holdout day-type mix: {n_days_pre}")

    adaptive_results = {}
    for thr in (0.5, 0.6, 0.7):
        qual = ha.qualifying_areas(area_rows, thr)
        n_cam_qual = sum(1 for c in cams if cam_area.get(c, 0) in qual)
        n_crash_qual = float(y[[i for i, c in enumerate(cams)
                                if cam_area.get(c, 0) in qual]].sum()) \
            if n_cam_qual else 0.0
        dm_ad = ha.adaptive_demand_matrix(
            cams, cam_area, day_types_holdout, pre_prof, qual)
        raw = rate_pre[:, None] * w_pre[:, hour_of_t] * dm_ad[
            :, day_of_hold, hour_of_t]
        pred = (_renorm(raw.reshape(n_c, 90, 24))
                * rate_pre[:, None, None]).reshape(n_c, n_t_hold)
        score(f"profile_adaptive_r{int(thr * 100):02d}", pred)
        xgb_dm = _renorm(xgb_day * dm_ad)
        pred_x = (xgb_dm * rate_pre[:, None, None]).reshape(n_c, n_t_hold)
        score(f"xgb_shape_adaptive_r{int(thr * 100):02d}", pred_x)
        adaptive_results[f"r>{thr}"] = {
            "n_qualifying_areas": len(qual),
            "qualifying_areas": sorted(qual),
            "n_qualifying_cameras": int(n_cam_qual),
            "holdout_crashes_in_qualifying_areas": n_crash_qual,
        }
        print(f"  threshold r>{thr}: {len(qual)} areas, "
              f"{n_cam_qual} cameras, {n_crash_qual:,.0f} holdout crashes")
    results["task1_adaptive_trust"] = {
        "method": ("Per-area demand-crash Pearson r computed on PRE-holdout "
                   "data only (crashes 2025-01-01->2026-07-31, demand "
                   "profiles rebuilt from trips 2023-01-01->2026-07-31, "
                   "blended with the pre-holdout day-type mix). Demand "
                   "multiplier applied only in qualifying areas; 1.0 "
                   "elsewhere. Areas with <20 pre-holdout crashes never "
                   "qualify."),
        "preholdout_correlation_summary": {
            "n_areas_with_cameras": len(area_rows),
            "n_areas_r_defined": int(len(rs)),
            "median_r": float(np.median(rs)),
            "mean_r": float(rs.mean()),
            "min_r": float(rs.min()),
            "max_r": float(rs.max()),
            "n_areas_r_above_0.5": int((rs > 0.5).sum()),
            "n_areas_r_above_0.6": int((rs > 0.6).sum()),
            "n_areas_r_above_0.7": int((rs > 0.7).sum()),
            "n_areas_r_below_0.5": int((rs < 0.5).sum()),
            "preholdout_day_type_counts": n_days_pre,
        },
        "per_area_preholdout_correlations": area_rows,
        "thresholds": adaptive_results,
        "note": ("Threshold grid was selected ON the holdout (deviance "
                 "comparison), so the best-threshold number is mildly "
                 "optimistic; the per-area correlations themselves are "
                 "leakage-free."),
    }

    # =====================================================================
    # Task 2 — daily-deviation adjustment (taxi-only)
    # =====================================================================
    print("\n--- Task 2: daily-deviation adjustment (taxi-only) ---")
    trip_days, trip_arr = ha.load_taxi_daily_trips()
    day_types_all = _day_type_series(trip_days)
    typical = ha.taxi_typical_shares(trip_days, trip_arr, day_types_all)
    print(f"taxi daily trips loaded: {len(trip_days)} days "
          f"({trip_days[0].date()} -> {trip_days[-1].date()}), "
          f"mean weekday taxi trips/day citywide: "
          f"{float(trip_arr[(trip_days <= PRE_END) & (day_types_all == 'weekday'), 0, :].sum(axis=1).mean()):,.0f}")

    dev_results = {}
    for offset, label in ((0, "sameday"), (-1, "prevday"), (-7, "lastweek")):
        alphas = (0.25, 0.5, 1.0) if offset == 0 else (0.5, 1.0)
        for alpha in alphas:
            dm_dev, n_zero = ha.daily_deviation_multipliers(
                cams, cam_area, hold_dates, trip_days, trip_arr, typical,
                day_types_holdout, source_offset=offset, alpha=alpha)
            raw = rate_pre[:, None] * w_pre[:, hour_of_t] * dm_dev[
                :, day_of_hold, hour_of_t]
            pred = (_renorm(raw.reshape(n_c, 90, 24))
                    * rate_pre[:, None, None]).reshape(n_c, n_t_hold)
            name = f"profile_dev_{label}_a{int(alpha * 100):02d}"
            score(name, pred)
            dev_results[name] = {
                "source_offset_days": offset,
                "alpha": alpha,
                "camera_days_with_zero_source_trips": int(n_zero),
            }
            print(f"  {label} alpha={alpha}: {n_zero} camera-days with "
                  f"zero source-day trips in their area (multiplier 1.0)")
    results["task2_daily_deviation"] = {
        "method": ("For each camera-day, deviation(h) = that source day's "
                   "taxi share in the camera's area / typical taxi share "
                   "for the source day's day type (typical = taxi-only "
                   "pre-holdout profiles, 2023-01-01->2026-07-31). Nudge "
                   "multiplier = 1 + alpha*(deviation-1), clipped to "
                   "[0.5, 2.0], applied on top of the crash profile and "
                   "renormalised per camera-day."),
        "source": "taxi-only (TNP ends 2026-09-30, so combined taxi+TNP "
                  "would change composition mid-holdout; taxi covers the "
                  "full holdout through 2026-11-01).",
        "availability_note": ("SAME-DAY is POST-HOC: the day's trips are "
                              "not available at prediction time in "
                              "production (taxi/TNP feeds lag ~2 weeks, "
                              "like the violation feed). Prev-day and "
                              "same-weekday-last-week are the forecastable "
                              "versions (they use only trips from days "
                              "before the prediction day).",
        ),
        "variants": dev_results,
    }

    # =====================================================================
    # Task 3 — demand lag test
    # =====================================================================
    print("\n--- Task 3: demand lag test ---")
    cw_pre = hm._load_crashes_citywide()
    cw_pre = cw_pre[cw_pre["timestamp"] < pd.Timestamp("2026-08-01")]
    city_counts = cw_pre.groupby("hour")["crash_count"].sum().reindex(
        range(24), fill_value=0)
    obs_share_pre = (city_counts / city_counts.sum()).to_numpy(dtype=float)
    lag_r_pre = ha.lag_correlations(pre_prof, obs_share_pre, n_days_pre)
    # Holdout correlations for the same lags (diagnostic only).
    obs_share_hold = obs_hour_totals / obs_hour_totals.sum()
    n_days_hold = {dt: int((day_types_holdout == dt).sum())
                   for dt in ("weekday", "weekend", "holiday")}
    lag_r_hold = ha.lag_correlations(pre_prof, obs_share_hold, n_days_hold)
    print(f"citywide lag correlations (crash share h vs demand share h+lag):")
    for lag in (-1, 0, 1):
        print(f"  lag {lag:+d}: pre-holdout r={lag_r_pre[lag]:.3f}, "
              f"holdout r={lag_r_hold[lag]:.3f}")
    best_lag = max(lag_r_pre, key=lag_r_pre.get)
    print(f"best pre-holdout lag: {best_lag:+d} (r={lag_r_pre[best_lag]:.3f})")
    lag_results = {}
    for lag in (-1, 0, 1):
        dm_lag = ha.lagged_demand_matrix(
            cams, cam_area, day_types_holdout, pre_prof, lag)
        raw = rate_pre[:, None] * w_pre[:, hour_of_t] * dm_lag[
            :, day_of_hold, hour_of_t]
        pred = (_renorm(raw.reshape(n_c, 90, 24))
                * rate_pre[:, None, None]).reshape(n_c, n_t_hold)
        score(f"profile_lag_{lag:+d}".replace("+", "p").replace("-", "m"), pred)
        lag_results[lag] = results["variants"][
            f"profile_lag_{lag:+d}".replace("+", "p").replace("-", "m")][
            "mean_poisson_deviance"]
    # XGB path for the best lag, if it is not lag 0.
    if best_lag != 0:
        dm_lag = ha.lagged_demand_matrix(
            cams, cam_area, day_types_holdout, pre_prof, best_lag)
        xgb_dm = _renorm(xgb_day * dm_lag)
        pred_x = (xgb_dm * rate_pre[:, None, None]).reshape(n_c, n_t_hold)
        score(f"xgb_shape_lag_{best_lag:+d}".replace("+", "p").replace("-", "m"),
              pred_x)
    results["task3_lag"] = {
        "method": ("Crash hour h uses the demand weight of hour "
                   "(h+lag) mod 24, from the pre-holdout demand profiles, "
                   "per area and day type, renormalised per camera-day."),
        "citywide_correlation_preholdout": {str(k): v
                                            for k, v in lag_r_pre.items()},
        "citywide_correlation_holdout_diagnostic": {
            str(k): v for k, v in lag_r_hold.items()},
        "best_preholdout_lag": best_lag,
        "holdout_deviance_by_lag": {str(k): v for k, v in lag_results.items()},
        "note": ("Lag chosen on PRE-holdout correlation (leakage-free); "
                 "holdout deviance per lag is reported for all three lags, "
                 "so the lag-0 row is the Phase 2c pre-holdout demand "
                 "variant reproduced."),
    }

    # --- baselines (score last so the Task sections print first) ------------
    print("\n--- baselines (reproduction) ---")
    for name in ("profile_only_preholdout", "profile_plus_demand_preholdout",
                 "xgboost_raw_counts", "xgboost_shape_x_preholdout_rate"):
        score(name, variants[name])

    # --- per-hour calibration for the best adaptive + best deviation -------
    calib = {}
    best_ad = min(
        (f"profile_adaptive_r{int(t * 100):02d}" for t in (0.5, 0.6, 0.7)),
        key=lambda n: results["variants"][n]["mean_poisson_deviance"])
    best_dev = min(
        (n for n in results["variants"] if n.startswith("profile_dev_")),
        key=lambda n: results["variants"][n]["mean_poisson_deviance"])
    for name in (best_ad, best_dev, "profile_only_preholdout"):
        pred = variants[name]
        ph_tot = pred.reshape(n_c, 90, 24).sum(axis=(0, 1))
        p_share, o_share, _, _ = _share_corr(ph_tot, obs_hour_totals)
        calib[name] = [{
            "hour": h,
            "observed_crashes": float(obs_hour_totals[h]),
            "observed_share": float(o_share[h]),
            "predicted_share": float(p_share[h]),
            "pred_over_obs_ratio": float(p_share[h] / o_share[h])
            if o_share[h] > 0 else None,
        } for h in range(24)]
    results["per_hour_calibration"] = calib
    results["best_adaptive_variant"] = best_ad
    results["best_deviation_variant"] = best_dev

    OUT_PATH.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    main()
