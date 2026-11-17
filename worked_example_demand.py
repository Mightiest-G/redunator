#!/usr/bin/env python3
"""Redunator Phase 2c — worked example with the trip-demand layer (worker C).

Re-runs the Phase 2b worked example (Thursday 2026-10-15, cams 1413
LSD & Belmont and 2251 Cicero & I-55) with demand blended in, side by
side with the no-demand numbers, on BOTH the model path (default) and
the profile path.

Checks performed here (asserted, not assumed):
  * daily_prediction for each camera equals the Phase 2 REPORT.md value
    (1413: 53.01, 2251: 46.82) to 2 dp, with and without demand —
    demand only reshapes the day, it never changes the daily total;
  * the 24 hourly values sum to the daily prediction with
    reconciliation error exactly 0.0 in all four (camera, demand)
    combinations on both methods;
  * demand actually changes the shape (not just a rescale).

Writes worked_example_demand.json next to this script.
Reads only; modifies no Phase 2 / worker A / worker B artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import hourly_model as hm
from hourly_risk import daily_prediction, predict_hourly

BASE = Path(__file__).resolve().parent
OUT_PATH = BASE / "worked_example_demand.json"

DATE = "2026-10-15"  # Thursday -> weekday demand profile
EXPECTED_DAILY = {"1413": 53.01, "2251": 46.82}
CAM_INFO = {"1413": "Lake Shore Dr & Belmont, area 6 LAKE VIEW",
            "2251": "Cicero & I-55, area 56 GARFIELD RIDGE"}


def main():
    out = {"date": DATE, "day_type": "weekday", "cameras": {}}
    for cam, exp in EXPECTED_DAILY.items():
        print(f"\ncam {cam} — {CAM_INFO[cam]} (daily {exp:.2f}):")
        dm = hm.demand_multipliers_for_day(cam, DATE)
        print(f"  demand multipliers: min {dm.min():.2f} max {dm.max():.2f} "
              f"mean {dm.mean():.4f} (peak {int(np.argmax(dm)):02d}:00, "
              f"trough {int(np.argmin(dm)):02d}:00)")
        out["cameras"][cam] = {
            "info": CAM_INFO[cam],
            "community_area": hm.camera_community_area(cam),
            "expected_daily": exp,
            "demand_multipliers": [float(v) for v in dm],
        }
        for method in ("auto", "profile"):
            r_off = predict_hourly(cam, DATE, method=method, demand=False)
            r_on = predict_hourly(cam, DATE, method=method, demand=True)
            for r, label in ((r_off, "no demand "), (r_on, "with demand")):
                assert round(r["daily_prediction"], 2) == exp, (cam, method, label)
                assert abs(r["reconciliation_error"]) < 1e-9, (cam, method, label)
            h_off = np.array(r_off["hourly"])
            h_on = np.array(r_on["hourly"])
            assert not np.allclose(h_off, h_on), "demand changed nothing"
            print(f"  method={r_on['method']:14s} "
                  f"no-demand peak {int(np.argmax(h_off)):02d}:00 "
                  f"{h_off.max():.2f} ({r_off['bands'][int(np.argmax(h_off))]}) | "
                  f"with-demand peak {int(np.argmax(h_on)):02d}:00 "
                  f"{h_on.max():.2f} ({r_on['bands'][int(np.argmax(h_on))]}) | "
                  f"max |dh| {np.abs(h_on - h_off).max():.2f} | "
                  f"recon err {r_on['reconciliation_error']:.2e}")
            out["cameras"][cam][method] = {
                "method_no_demand": r_off["method"],
                "method_with_demand": r_on["method"],
                "daily_prediction": r_on["daily_prediction"],
                "daily_source": r_on["daily_source"],
                "hourly_no_demand": r_off["hourly"],
                "hourly_with_demand": r_on["hourly"],
                "bands_no_demand": r_off["bands"],
                "bands_with_demand": r_on["bands"],
                "reconciliation_error_no_demand": r_off["reconciliation_error"],
                "reconciliation_error_with_demand": r_on["reconciliation_error"],
            }

    # Printable full 24h table for 1413 (model path) + top hours for 2251.
    c = out["cameras"]["1413"]["auto"]
    print("\ncam 1413 — full 24h, model path (daily 53.01):")
    print(" hour | no demand | with demand |  delta | band off -> on")
    for h in range(24):
        print(f" {h:02d}:00 | {c['hourly_no_demand'][h]:9.2f} | "
              f"{c['hourly_with_demand'][h]:11.2f} | "
              f"{c['hourly_with_demand'][h] - c['hourly_no_demand'][h]:+6.2f} | "
              f"{c['bands_no_demand'][h]:6s} -> {c['bands_with_demand'][h]}")
    for cam in ("1413", "2251"):
        for method in ("auto", "profile"):
            cc = out["cameras"][cam][method]
            order = np.argsort(cc["hourly_with_demand"])[::-1][:6]
            print(f"\ncam {cam} ({method}) — top 6 hours WITH demand "
                  f"(daily {cc['daily_prediction']:.2f}):")
            for h in order:
                h = int(h)
                print(f" {h:02d}:00 | with {cc['hourly_with_demand'][h]:.2f} "
                      f"({cc['bands_with_demand'][h]}) | "
                      f"without {cc['hourly_no_demand'][h]:.2f} "
                      f"({cc['bands_no_demand'][h]}) | "
                      f"delta {cc['hourly_with_demand'][h] - cc['hourly_no_demand'][h]:+.2f}")

    OUT_PATH.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    main()
