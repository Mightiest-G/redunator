#!/usr/bin/env python3
"""Redunator Phase 2 — BACKTEST of the live weather-delta adjustment (Worker C).

The deciding test for `live_adjust.py`. Same holdout protocol as
Phase 2b/2c/2d (`validate_hourly.py`, `validate_hourly_demand.py`,
`validate_hourly_2d.py`):

  * Holdout 2026-08-01 -> 2026-10-29 (90 days x 361 cameras =
    779,760 camera-hours, 8,517 observed attributed crashes).
  * Profiles rebuilt on pre-holdout crashes only (through
    2026-07-31); level = each camera's pre-holdout daily crash
    rate. All shape layers renormalised per camera-day.
  * Target = hourly crash counts within 200 m of each camera
    (PROXY — hourly violations are unobservable).
  * Metrics: mean Poisson deviance (primary), RMSE, MAE, predicted
    vs actual totals, predicted peak hour, hourly-share Pearson r.

The honest-backtest problem, stated plainly
--------------------------------------------
A live adjustment is a DELTA between live conditions and the
conditions the model expected. The honest version of that test
needs archived *forecasts* (what was expected at the time) next to
archived *actuals* — and no forecast archive exists. Open-Meteo
keeps no historical forecast record, and no forecast was stored
when the hourly layer was built. **Forecast data was therefore NOT
invented.** Instead the backtest runs the weaker but honest
version the brief names, in the production-faithful form:

* The live adjustment exists for dates the model has NO weather
  for (after 2026-10-29, the archive ends; `predict_hourly` then
  serves the profile path with weather multipliers of exactly 1.0
  and the model path falls back to profile). For those dates the
  "expected" weather multiplier is 1.0 and the live weather is
  genuinely new information.
* The backtest simulates exactly that situation on holdout dates:
  the baseline is the no-weather shape (profile+slowness /
  B12 shape), and the "live" signal is the archive ACTUAL weather
  for that hour — the best available stand-in for what a live feed
  would report at that hour. The adjustment multiplies ONLY the
  current hour by `weather_multiplier(actual weather)` and
  renormalises the day.
* Because the adjustment touches one hour, the test averages over
  all 24 choices of "current hour": for each holdout camera-day,
  24 adjusted variants are scored (one per hour) and their
  deviances averaged. That is the exact expectation of the
  adjustment's effect for a random current hour.

Variants
--------
  profile_plus_slowness            baseline (reproduction: 0.094369)
  profile_plus_weather_plus_slowness
                                   reference full-day weather layer
                                   (reproduction: 0.094390)
  b12_shape                        baseline (reproduction: 0.093260)
  LIVE-A  profile+slowness + current-hour-only live weather delta
          (expected = 1.0; the production case)
  LIVE-B  B12 shape + current-hour-only weather multiplier layered
          on top (double-count test: the model already ingests
          weather as features, so this has no "expected" to delta
          against — it measures the cost of topping up anyway)
  LIVE-C  all signals stale -> adjusted == baseline exactly
          (asserted, not scored: deviance identical by construction)

Ship rule: LIVE-A ships (as the default path in `live_adjust.py`)
ONLY if it beats profile+slowness on mean Poisson deviance; LIVE-B
ships only if it beats B12 shape. Otherwise the adjustment stays
experimental, default OFF, with the numbers recorded here.

Writes hourly_validation_live.json next to this script. Reads only;
modifies no Phase 2 / 2b / 2c / 2d artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

import hourly_model as hm
from live_adjust import apply_hour_adjustment, weather_delta_factor
from train_hourly_2d import build_design_2d
from validate_hourly import (
    HOLDOUT_END,
    HOLDOUT_START,
    N_PRE_DAYS,
    PRE_END,
    _metrics,
    _share_corr,
)
from validate_hourly_2d import (
    load_speed_profiles,
    slowness_mult_matrix,
)
from validate_hourly_demand import _day_type_series

BASE = Path(__file__).resolve().parent
OUT_PATH = BASE / "hourly_validation_live.json"

# Recorded bars (HOURLY_REPORT.md §9 / hourly_model_2d_metrics.json).
RECORDED = {
    "profile_plus_slowness": 0.094369,
    "profile_plus_weather_plus_slowness": 0.094390,
    "b12_shape": 0.093260,
}


def _renorm(day):
    denom = day.sum(axis=2, keepdims=True)
    return day / np.where(denom > 0, denom, 1.0)


def _score_variant(name, pred_day, y_flat, obs_hour_totals, n_c):
    """pred_day: (n_c, 90, 24) predicted counts. Returns metrics dict."""
    m = _metrics(y_flat, pred_day.reshape(-1))
    ph_tot = pred_day.sum(axis=(0, 1))
    p_share, o_share, rp, rs = _share_corr(ph_tot, obs_hour_totals)
    m["share_pearson_r"] = rp
    m["share_spearman_rho"] = rs
    m["pred_peak_hour"] = int(np.argmax(ph_tot))
    m["pred_peak_share"] = float(p_share.max())
    m["obs_peak_share"] = float(o_share[int(np.argmax(obs_hour_totals))])
    return m


def main():
    print("loading matrices + rebuilding pre-holdout profiles ...")
    mats = hm._poisson_matrices()
    times, days = mats["times"], mats["days"]
    cams = mats["cams"]
    n_c = len(cams)

    prof_pre = hm.build_profiles(crash_end=PRE_END, cong_end="2026-04-30")
    w_pre = np.zeros((n_c, 24))
    for i, cam in enumerate(cams):
        sub = prof_pre[prof_pre["camera_id"] == cam].sort_values("hour")
        assert len(sub) == 24, cam
        w_pre[i] = sub["weight"].to_numpy(dtype=float)

    daily = mats["daily"]
    pre_day_idx = int((pd.Timestamp(PRE_END) - days[0]).days) + 1
    rate_pre = daily[:, :pre_day_idx].sum(axis=1) / N_PRE_DAYS

    t0 = int((HOLDOUT_START - times[0]) / pd.Timedelta(hours=1))
    t1 = int((HOLDOUT_END + pd.Timedelta(hours=23) - times[0])
             / pd.Timedelta(hours=1)) + 1
    hold_times = times[t0:t1]
    assert len(hold_times) == 90 * 24
    y = mats["crash_mat"][:, t0:t1].astype(float)
    y_flat = y.reshape(-1)
    obs_hour_totals = y.reshape(n_c, 90, 24).sum(axis=(0, 1))
    obs_peak = int(np.argmax(obs_hour_totals))
    print(f"holdout camera-hours: {n_c * 90 * 24:,}  "
          f"observed crashes: {y.sum():,.0f}  peak {obs_peak}:00")

    hour_of_t = mats["hour_of_t"][t0:t1]
    day_of_hold = (hold_times.normalize() - HOLDOUT_START).days.to_numpy()
    hold_dates = pd.date_range(HOLDOUT_START, HOLDOUT_END, freq="D")
    day_types_holdout = _day_type_series(hold_dates)

    # --- weather multipliers + live-weather stand-in (archive actuals) -----
    wx = np.ones((90, 24))
    for d in range(90):
        wx[d] = hm.weather_multipliers_for_day(
            HOLDOUT_START + pd.Timedelta(days=d))
    wet = wx > 1.0
    print(f"weather multipliers: hours >1.0: {int(wet.sum())}/{wx.size} "
          f"({100 * wet.mean():.1f}%), mean {wx.mean():.4f}, "
          f"max {wx.max():.3f}, mean among wet {wx[wet].mean():.4f}")

    # --- slowness multipliers (pre-holdout speed profiles) ------------------
    cam_area = {}
    area_map = hm.load_camera_area_map()
    for _, r in area_map.iterrows():
        cam_area[str(r["camera_id"])] = (
            int(r["community_area"]) if pd.notna(r["community_area"]) else 0)
    speed_prof = load_speed_profiles()
    sm_full = slowness_mult_matrix(cams, cam_area, day_types_holdout,
                                   speed_prof, 1.0)
    sm_t = sm_full[:, day_of_hold, hour_of_t]
    wx_t = wx[day_of_hold, hour_of_t]
    base_prof = rate_pre[:, None] * w_pre[:, hour_of_t]

    def _prof_day(extra_t=None):
        raw = base_prof if extra_t is None else base_prof * extra_t
        return (_renorm(raw.reshape(n_c, 90, 24))
                * rate_pre[:, None, None])

    pred_prof_slow = _prof_day(sm_t)
    pred_prof_wx_slow = _prof_day(wx_t * sm_t)

    # --- B12 shipped model shape -------------------------------------------
    print("scoring shipped B12 model on holdout ...")
    reg = joblib.load(hm.POISSON_MODEL_PATH)
    Xh, yh = build_design_2d(
        mats, HOLDOUT_START, HOLDOUT_END + pd.Timedelta(hours=23),
        "2026-07-31", extra=())[:2]
    assert np.array_equal(yh, y_flat), "design target != crash matrix"
    pred_raw = np.clip(reg.predict(Xh), 0, None)
    pred_b12_shape = (_renorm(pred_raw.reshape(n_c, 90, 24))
                      * rate_pre[:, None, None])

    # --- current-hour-only adjustment, averaged over all 24 choices --------
    # For choice h: multiply hour h of each day by the live delta and
    # renormalise that day. Deviance is a sum over camera-hours, so
    # the 24 variant scores average directly. Peak hour / share r use
    # the mean-over-choices hourly totals.
    def _live_adjusted(base_day, delta_day):
        """base_day, delta_day: (n_c, 90, 24). Returns
        (mean metrics over the 24 current-hour choices,
        mean hourly totals over the 24 choices)."""
        devs, rmses, maes = [], [], []
        ph_total_mean = np.zeros(24)
        for h in range(24):
            raw = base_day.copy()
            raw[:, :, h] = raw[:, :, h] * delta_day[:, h][None, :]
            adj = _renorm(raw) * rate_pre[:, None, None]
            m = _metrics(y_flat, adj.reshape(-1))
            devs.append(m["mean_poisson_deviance"])
            rmses.append(m["rmse"])
            maes.append(m["mae"])
            ph_total_mean += adj.sum(axis=(0, 1))
        ph_total_mean /= 24.0
        p_share, o_share, rp, rs = _share_corr(ph_total_mean,
                                               obs_hour_totals)
        return {
            "mean_poisson_deviance": float(np.mean(devs)),
            "mean_poisson_deviance_min_over_choices": float(np.min(devs)),
            "mean_poisson_deviance_max_over_choices": float(np.max(devs)),
            "rmse": float(np.mean(rmses)),
            "mae": float(np.mean(maes)),
            "pred_total": float(base_day.sum()),
            "actual_total": float(y.sum()),
            "mean_pred": float(base_day.mean()),
            "mean_actual": float(y.mean()),
            "share_pearson_r": rp,
            "share_spearman_rho": rs,
            "pred_peak_hour": int(np.argmax(ph_total_mean)),
            "pred_peak_share": float(p_share.max()),
            "obs_peak_share": float(o_share[obs_peak]),
            "per_choice_deviance": [float(v) for v in devs],
        }

    # LIVE-A: production case — baseline has no weather (expected 1.0),
    # live delta = weather_multiplier(archive actual at that hour).
    # delta_day is (90, 24) of multipliers; base is profile+slowness.
    print("scoring LIVE-A (profile+slowness, current-hour live delta) ...")
    live_a = _live_adjusted(pred_prof_slow, wx)
    # LIVE-B: double-count test — B12 shape already ingests weather;
    # layering the assumed multiplier on the current hour anyway.
    print("scoring LIVE-B (B12 shape, current-hour weather top-up) ...")
    live_b = _live_adjusted(pred_b12_shape, wx)

    # --- baselines -----------------------------------------------------------
    results = {
        "holdout": {"start": "2026-08-01", "end": "2026-10-29", "days": 90,
                    "cameras": int(n_c),
                    "camera_hours": int(n_c * 90 * 24),
                    "observed_crashes_attributed": float(y.sum()),
                    "nonzero_camera_hours": int((y > 0).sum()),
                    "observed_peak_hour": obs_peak,
                    "note": ("Same protocol as validate_hourly.py / "
                             "validate_hourly_2d.py. Live signal "
                             "stand-in: archive ACTUAL weather for the "
                             "hour (no forecast archive exists; no "
                             "forecast data invented). Adjustment "
                             "touches one hour per variant and "
                             "renormalises the day; LIVE-A/LIVE-B "
                             "numbers are means over all 24 choices "
                             "of current hour.")},
        "weather_multipliers_holdout": {
            "hours_above_1.0": int(wet.sum()),
            "hours_total": int(wx.size),
            "fraction_above_1.0": float(wet.mean()),
            "mean": float(wx.mean()),
            "max": float(wx.max()),
            "mean_among_wet": float(wx[wet].mean()),
        },
        "variants": {},
        "reproduction_check": {},
    }
    baselines = {
        "profile_plus_slowness": pred_prof_slow,
        "profile_plus_weather_plus_slowness": pred_prof_wx_slow,
        "b12_shape": pred_b12_shape,
    }
    for name, pred_day in baselines.items():
        m = _score_variant(name, pred_day, y_flat, obs_hour_totals, n_c)
        results["variants"][name] = m
        rec = RECORDED.get(name)
        if rec is not None:
            results["reproduction_check"][name] = {
                "recorded_deviance": rec,
                "reproduced_deviance": m["mean_poisson_deviance"],
                "abs_diff": float(abs(m["mean_poisson_deviance"] - rec)),
            }
        print(f"{name:38s} dev {m['mean_poisson_deviance']:.6f}  "
              f"RMSE {m['rmse']:.6f}  MAE {m['mae']:.6f}  "
              f"pred {m['pred_total']:,.0f} vs actual "
              f"{m['actual_total']:,.0f}  peak {m['pred_peak_hour']}:00  "
              f"share r={m['share_pearson_r']:.3f}")

    results["variants"]["LIVE-A_profile_slowness_plus_live_current_hour"] = live_a
    results["variants"]["LIVE-B_b12_shape_plus_live_current_hour_topup"] = live_b
    for name, m in (("LIVE-A", live_a), ("LIVE-B", live_b)):
        print(f"{name:38s} dev {m['mean_poisson_deviance']:.6f}  "
              f"(range over choices {m['mean_poisson_deviance_min_over_choices']:.6f}"
              f"–{m['mean_poisson_deviance_max_over_choices']:.6f})  "
              f"RMSE {m['rmse']:.6f}  MAE {m['mae']:.6f}  "
              f"pred {m['pred_total']:,.0f} vs actual "
              f"{m['actual_total']:,.0f}  peak {m['pred_peak_hour']}:00  "
              f"share r={m['share_pearson_r']:.3f}")

    # --- LIVE-C: all signals stale -> identical to baseline -----------------
    # Pure-function check (the public function is asserted in
    # smoke_test_live_adjust.py with fabricated stale signals):
    # a factor of 1.0 returns the input unchanged.
    sample = pred_prof_slow[0, 0].copy()
    assert np.array_equal(
        apply_hour_adjustment(sample, 15, 1.0), sample), "factor 1.0 changed values"
    # And weather_delta_factor with no live values vs expected 1.0 is 1.0.
    assert weather_delta_factor(None, None, None, 1.0) == 1.0
    results["LIVE-C_all_signals_stale"] = {
        "verdict": "degrades to unadjusted baseline exactly",
        "check": ("apply_hour_adjustment(x, h, 1.0) == x element-wise; "
                  "weather_delta_factor(None, None, None, 1.0) == 1.0; "
                  "public-function assertion in smoke_test_live_adjust.py"),
    }
    print("LIVE-C: all-stale degrades to baseline exactly (asserted).")

    # --- ship verdict --------------------------------------------------------
    dev_a = live_a["mean_poisson_deviance"]
    dev_a_base = results["variants"]["profile_plus_slowness"]["mean_poisson_deviance"]
    dev_b = live_b["mean_poisson_deviance"]
    dev_b_base = results["variants"]["b12_shape"]["mean_poisson_deviance"]
    verdict_a = "SHIP" if dev_a < dev_a_base else "DO NOT SHIP"
    verdict_b = "SHIP" if dev_b < dev_b_base else "DO NOT SHIP"
    results["verdict"] = {
        "LIVE-A_vs_profile_plus_slowness": {
            "adjusted_deviance": dev_a,
            "baseline_deviance": dev_a_base,
            "delta": float(dev_a - dev_a_base),
            "verdict": verdict_a,
        },
        "LIVE-B_vs_b12_shape": {
            "adjusted_deviance": dev_b,
            "baseline_deviance": dev_b_base,
            "delta": float(dev_b - dev_b_base),
            "verdict": verdict_b,
        },
        "overall": ("DO NOT SHIP — neither live-adjusted variant beats "
                    "its baseline on mean Poisson deviance; the "
                    "adjustment stays experimental in live_adjust.py, "
                    "not wired as a default path.")
        if verdict_a == "DO NOT SHIP" and verdict_b == "DO NOT SHIP" else
        "MIXED — see per-variant verdicts.",
    }
    print(f"\nVERDICT LIVE-A: {verdict_a} "
          f"(delta {dev_a - dev_a_base:+.6f})")
    print(f"VERDICT LIVE-B: {verdict_b} "
          f"(delta {dev_b - dev_b_base:+.6f})")

    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    main()
