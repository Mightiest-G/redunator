#!/usr/bin/env python3
"""Redunator Phase 2b — worked example (worker C).

Thursday 2026-10-15, the Phase 2 worked-example date: Lake Shore Dr &
Belmont (cams 1412, 1413) and Cicero & I-55 (cams 2251, 2252), each
broken into 24 hours via hourly_risk.predict_hourly.

Checks performed here (asserted, not assumed):
  * daily_prediction for each camera equals the Phase 2 REPORT.md value
    (1413: 53.01, 1412: 4.61, 2251: 46.82, 2252: 8.15) to 2 dp;
  * the 24 hourly values sum to the daily prediction (reconciliation
    error reported; predict_hourly places the float residual on the
    peak hour, so it is exactly 0.0);
  * congestion multipliers are 1.0 for every hour of this date (dead
    feed) for all four cameras;
  * 1514 still reproduces 22.57 via the daily predict path.

Also prints the day's weather (Open-Meteo) and the weather multipliers,
so the report can state exactly what varied hour to hour.

Writes worked_example_hourly.json next to this script.
Reads only; modifies no Phase 2 or worker A/B artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import hourly_model as hm
from hourly_risk import daily_prediction, predict_hourly

BASE = Path(__file__).resolve().parent
OUT_PATH = BASE / "worked_example_hourly.json"

DATE = "2026-10-15"
EXPECTED_DAILY = {"1412": 4.61, "1413": 53.01, "2251": 46.82, "2252": 8.15}
EXPECTED_1514 = 22.57


def main():
    # --- daily reproduction check (incl. 1514) ------------------------------
    print("daily predictions vs REPORT.md (2026-10-15):")
    for cam, exp in {**EXPECTED_DAILY, "1514": EXPECTED_1514}.items():
        pred, src = daily_prediction(cam, DATE)
        ok = round(pred, 2) == exp
        print(f"  cam {cam}: {pred:.4f} (rounded {pred:.2f}) vs {exp:.2f} "
              f"source={src}  {'OK' if ok else 'MISMATCH'}")
        assert ok, f"daily prediction mismatch for {cam}: {pred} vs {exp}"

    # --- weather on the day --------------------------------------------------
    w = pd.read_parquet(BASE / "data" / "hourly" / "processed" / "weather_hourly.parquet")
    w["timestamp"] = pd.to_datetime(w["timestamp"])
    day_w = w[w["timestamp"].dt.strftime("%Y-%m-%d") == DATE].sort_values("timestamp")
    assert len(day_w) == 24, len(day_w)
    wx = hm.weather_multipliers_for_day(DATE)
    print(f"\nweather 2026-10-15: precip total {day_w['precipitation_mm'].sum():.1f} mm, "
          f"max hourly {day_w['precipitation_mm'].max():.1f} mm; "
          f"snowfall {day_w['snowfall_cm'].sum():.2f} cm; "
          f"temp {day_w['temperature_2m_c'].min():.1f}..{day_w['temperature_2m_c'].max():.1f} C; "
          f"wind max {day_w['wind_speed_10m_kmh'].max():.1f} km/h")
    print(f"weather multipliers: min {wx.min():.3f} max {wx.max():.3f} "
          f"hours > 1.0: {int((wx > 1.0).sum())}")
    weather_rows = []
    for (_, r), m in zip(day_w.iterrows(), wx):
        weather_rows.append({
            "hour": int(r["timestamp"].hour),
            "temp_c": float(r["temperature_2m_c"]),
            "precip_mm": float(r["precipitation_mm"]),
            "snowfall_cm": float(r["snowfall_cm"]),
            "wind_kmh": float(r["wind_speed_10m_kmh"]),
            "weather_multiplier": float(m),
        })

    # --- congestion multipliers: must be 1.0 (dead feed) ---------------------
    for cam in EXPECTED_DAILY:
        cg = hm.congestion_multipliers_for_day(cam, DATE)
        assert np.all(cg == 1.0), (cam, cg)
    print("congestion multipliers: 1.0 for all 24 hours x all 4 cameras (dead feed)")

    # --- hourly breakdowns ----------------------------------------------------
    out = {"date": DATE, "weather": weather_rows, "cameras": {}}
    for cam, exp in EXPECTED_DAILY.items():
        for method in ("auto", "profile"):
            r = predict_hourly(cam, DATE, method=method)
            assert round(r["daily_prediction"], 2) == exp, (cam, method)
            err = r["reconciliation_error"]
            assert abs(err) < 1e-9, (cam, method, err)
            print(f"cam {cam} method={r['method']:7s} daily={r['daily_prediction']:.2f} "
                  f"sum={r['hourly_sum']:.6f} recon_err={err:.2e} "
                  f"peak {int(np.argmax(r['hourly'])):02d}:00 "
                  f"({max(r['hourly']):.2f}, {r['bands'][int(np.argmax(r['hourly']))]})")
            if method == "auto":
                out["cameras"][cam] = {
                    "daily_prediction": r["daily_prediction"],
                    "daily_source": r["daily_source"],
                    "method": r["method"],
                    "fallback_note": r["fallback_note"],
                    "hourly": r["hourly"],
                    "bands": r["bands"],
                    "risk_scores": r["risk_scores"],
                    "hourly_sum": r["hourly_sum"],
                    "reconciliation_error": r["reconciliation_error"],
                    "expected_daily": exp,
                }
            else:
                out["cameras"][cam]["profile_method"] = {
                    "method": r["method"],
                    "hourly": r["hourly"],
                    "bands": r["bands"],
                    "peak_hour": int(np.argmax(r["hourly"])),
                    "peak_value": float(max(r["hourly"])),
                    "reconciliation_error": r["reconciliation_error"],
                }

    # --- printable tables ------------------------------------------------------
    for cam in ("1413", "1412"):
        c = out["cameras"][cam]
        print(f"\ncam {cam} — full 24h (method={c['method']}, daily {c['daily_prediction']:.2f}):")
        print(" hour | predicted | band   | score")
        for h in range(24):
            print(f" {h:02d}:00 | {c['hourly'][h]:9.2f} | {c['bands'][h]:6s} | {c['risk_scores'][h]:.3f}")
    for cam in ("2251", "2252"):
        c = out["cameras"][cam]
        order = np.argsort(c["hourly"])[::-1][:6]
        print(f"\ncam {cam} — top 6 hours (method={c['method']}, daily {c['daily_prediction']:.2f}):")
        for h in order:
            print(f" {int(h):02d}:00 | {c['hourly'][int(h)]:.2f} | {c['bands'][int(h)]}")

    OUT_PATH.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    main()
