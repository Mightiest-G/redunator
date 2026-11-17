#!/usr/bin/env python3
"""Redunator Phase 2d — worked example with the WINNING configuration (worker 3).

Re-runs the Phase 2b worked example (Thursday 2026-10-15, cams 1413
LSD & Belmont and 2251 Cicero & I-55) with the Phase 2d winners:

  * Model path (default, method="auto"): the B12 retrained XGBoost
    (12 shipped features, trained 2025-01-01 -> 2026-07-31), with
    demand OFF — post-hoc demand hurts the model on the holdout, so
    the winning model configuration has no demand layer. Slowness is
    never applied on the model path (it hurts there too).
  * Profile path (method="profile"): profile + weather + slowness,
    demand OFF — post-hoc slowness wins on the profile path; demand
    loses there too. Slowness is on by default on this path.

Checks performed here (asserted, not assumed):
  * daily_prediction for each camera equals the Phase 2 REPORT.md value
    (1413: 53.01, 2251: 46.82) to 2 dp, on both methods — neither
    layer changes the daily total;
  * the 24 hourly values sum to the daily prediction with
    reconciliation error exactly 0.0 on both methods;
  * slowness actually changes the profile shape (not just a rescale);
  * slowness_applied is True on the profile path, False on the model path.

Comparison peaks (recorded, not recomputed — the 2b/2c runs used the
worker-B model, which Phase 2d replaced; see HOURLY_REPORT.md §9):
  * 2c demand version, 1413 (model+demand, v2b model): peak 18:00 / 6.08
    (WRONG hour — trips peak 18:00, crashes peak 15:00).
  * 2b no-demand version, 1413 (model, v2b model): peak 15:00 / 4.74
    (right hour).
These are loaded from worked_example_demand.json /
worked_example_hourly.json when present, so the comparison is against
the recorded numbers, not memory.

Writes worked_example_2d.json next to this script.
Reads only; modifies no Phase 2 / 2b / 2c artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import hourly_model as hm
from hourly_risk import daily_prediction, predict_hourly

BASE = Path(__file__).resolve().parent
OUT_PATH = BASE / "worked_example_2d.json"

DATE = "2026-10-15"  # Thursday -> weekday profiles
EXPECTED_DAILY = {"1413": 53.01, "2251": 46.82}
CAM_INFO = {"1413": "Lake Shore Dr & Belmont, area 6 LAKE VIEW",
            "2251": "Cicero & I-55, area 56 GARFIELD RIDGE"}


def _recorded_peak(path, cam, method_key, hourly_key):
    """Peak (hour, value) from a recorded worked-example JSON, or None."""
    try:
        data = json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    try:
        if "cameras" in data and cam in data["cameras"]:
            cc = data["cameras"][cam]
            # 2d/2c shape: cameras[cam][method][hourly_key]; 2b shape:
            # the auto-method hours sit directly under cameras[cam].
            if method_key in cc and isinstance(cc[method_key], dict):
                h = np.array(cc[method_key][hourly_key])
            elif hourly_key in cc:
                h = np.array(cc[hourly_key])
            else:
                return None
            return int(np.argmax(h)), float(h.max())
        return None
    except (KeyError, TypeError):
        return None


def main():
    out = {"date": DATE, "day_type": "weekday",
           "winning_configuration": {
               "model_path": "B12 retrained XGBoost (12 shipped features, "
                             "train 2025-01-01 -> 2026-07-31), demand OFF, "
                             "slowness N/A (never applied on model path)",
               "profile_path": "profile + weather + slowness, demand OFF",
           },
           "cameras": {}}
    # Recorded comparison peaks (2b v2b-model no-demand; 2c v2b-model +demand).
    rec_2b = {}
    rec_2c = {}
    for cam in EXPECTED_DAILY:
        rec_2b[cam] = _recorded_peak(BASE / "worked_example_hourly.json", cam,
                                     "auto", "hourly")
        rec_2c[cam] = _recorded_peak(BASE / "worked_example_demand.json", cam,
                                     "auto", "hourly_with_demand")
    print("recorded comparison peaks (v2b model):")
    print(f"  2b no-demand (model): 1413 {rec_2b.get('1413')}, "
          f"2251 {rec_2b.get('2251')}")
    print(f"  2c with-demand (model+demand): 1413 {rec_2c.get('1413')}, "
          f"2251 {rec_2c.get('2251')}")
    out["recorded_comparison_peaks"] = {
        "phase_2b_no_demand_v2b_model": {c: rec_2b[c] for c in EXPECTED_DAILY},
        "phase_2c_with_demand_v2b_model": {c: rec_2c[c] for c in EXPECTED_DAILY},
    }

    for cam, exp in EXPECTED_DAILY.items():
        print(f"\ncam {cam} — {CAM_INFO[cam]} (daily {exp:.2f}):")
        sm = hm.slowness_multipliers_for_day(cam, DATE)
        print(f"  slowness multipliers: min {sm.min():.2f} max {sm.max():.2f} "
              f"mean {sm.mean():.4f} (peak {int(np.argmax(sm)):02d}:00, "
              f"trough {int(np.argmin(sm)):02d}:00)")
        out["cameras"][cam] = {
            "info": CAM_INFO[cam],
            "community_area": hm.camera_community_area(cam),
            "expected_daily": exp,
            "slowness_multipliers": [float(v) for v in sm],
        }
        # Winning configuration, both methods, demand OFF.
        for method in ("auto", "profile"):
            r_win = predict_hourly(cam, DATE, method=method,
                                   demand=False, slowness=True)
            r_plain = predict_hourly(cam, DATE, method=method,
                                     demand=False, slowness=False)
            assert round(r_win["daily_prediction"], 2) == exp, (cam, method)
            assert abs(r_win["reconciliation_error"]) < 1e-9, (cam, method)
            h_win = np.array(r_win["hourly"])
            h_plain = np.array(r_plain["hourly"])
            if method == "profile":
                assert r_win["slowness_applied"] is True
                assert not np.allclose(h_win, h_plain), "slowness changed nothing"
                assert r_win["method"] == "profile+slowness", r_win["method"]
            else:
                assert r_win["slowness_applied"] is False
                assert np.allclose(h_win, h_plain), "model path must ignore slowness"
                assert r_win["method"] == "model", r_win["method"]
            pk = int(np.argmax(h_win))
            print(f"  method={r_win['method']:18s} winning peak {pk:02d}:00 "
                  f"{h_win[pk]:.2f} ({r_win['bands'][pk]}) | "
                  f"no-slowness peak {int(np.argmax(h_plain)):02d}:00 "
                  f"{h_plain.max():.2f} | max |dh| "
                  f"{np.abs(h_win - h_plain).max():.2f} | "
                  f"recon err {r_win['reconciliation_error']:.2e}")
            out["cameras"][cam][method] = {
                "method": r_win["method"],
                "daily_prediction": r_win["daily_prediction"],
                "daily_source": r_win["daily_source"],
                "hourly_winning": r_win["hourly"],
                "bands_winning": r_win["bands"],
                "hourly_no_slowness": r_plain["hourly"],
                "bands_no_slowness": r_plain["bands"],
                "slowness_applied": r_win["slowness_applied"],
                "reconciliation_error": r_win["reconciliation_error"],
            }

    # Printable full 24h table for 1413 (winning config, both methods).
    for method in ("auto", "profile"):
        c = out["cameras"]["1413"][method]
        print(f"\ncam 1413 — full 24h, {method} path, winning config "
              f"(daily {c['daily_prediction']:.2f}):")
        print(" hour | winning | no-slowness |  delta | band win")
        for h in range(24):
            print(f" {h:02d}:00 | {c['hourly_winning'][h]:7.2f} | "
                  f"{c['hourly_no_slowness'][h]:11.2f} | "
                  f"{c['hourly_winning'][h] - c['hourly_no_slowness'][h]:+6.2f} | "
                  f"{c['bands_winning'][h]}")
    for cam in ("1413", "2251"):
        for method in ("auto", "profile"):
            cc = out["cameras"][cam][method]
            order = np.argsort(cc["hourly_winning"])[::-1][:6]
            print(f"\ncam {cam} ({method}) — top 6 hours, WINNING config "
                  f"(daily {cc['daily_prediction']:.2f}):")
            for h in order:
                h = int(h)
                print(f" {h:02d}:00 | winning {cc['hourly_winning'][h]:.2f} "
                      f"({cc['bands_winning'][h]}) | "
                      f"no-slowness {cc['hourly_no_slowness'][h]:.2f} "
                      f"({cc['bands_no_slowness'][h]}) | "
                      f"delta {cc['hourly_winning'][h] - cc['hourly_no_slowness'][h]:+.2f}")

    OUT_PATH.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    main()
