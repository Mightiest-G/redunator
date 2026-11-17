#!/usr/bin/env python3
"""Verify the Phase 1 (item 2) hourly bands work + worked example.

Checks (all asserted, not eyeballed):
  1. Daily model files byte-identical to the Phase 2 baseline md5s.
  2. predict_hourly('1413', '2026-10-15') default still sums to
     53.0096 with peak 15:00 at 4.82, method "model".
  3. banding="daily" returns the IDENTICAL hourly values, only the
     band labels differ (banding never changes predictions).
  4. The vectorised derivation in derive_hourly_bands.py reproduces
     live predict_hourly() hourly values on a sample of camera-days.
  5. Holdout-wide band-change counts (old vs new banding) over all
     642,048 holdout camera-hours.
  6. Worked example: cam 1413, 2026-10-15, all 24 hours re-banded
     under the new bands, with the old banding alongside.

Writes hourly_bands_worked_example.json.

Usage:
  ~/workspace/redunator-venv/bin/python verify_hourly_bands.py
"""

import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

import hourly_model as hm
import hourly_risk as hr
from derive_hourly_bands import (daily_predictions_holdout,
                                 model_path_hourly)

BASE = Path(__file__).resolve().parent
OUT_PATH = BASE / "hourly_bands_worked_example.json"

BASELINE_MD5 = {
    "redunator_brain_v1.joblib": "9162dcbe76bf842584bd6f208c8bcb99",
    "redunator_brain_v1.onnx": "90b9160389dd76d945bc8428a9ec3bf0",
    "redunator_brain_v1.json": "28c83534e67267e272108c582ed0c56b",
}


def md5(path):
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def main():
    # 1. Daily model files byte-identical.
    for name, want in BASELINE_MD5.items():
        got = md5(BASE / name)
        assert got == want, f"{name} md5 {got} != baseline {want}"
        print(f"md5 OK  {name}  {got}")

    # 2. Worked-example anchor, default settings.
    r = hr.predict_hourly("1413", "2026-10-15")
    assert r["method"] == "model", r["method"]
    assert abs(sum(r["hourly"]) - 53.0096) < 1e-3, sum(r["hourly"])
    peak_h = int(np.argmax(r["hourly"]))
    assert peak_h == 15, peak_h
    assert abs(max(r["hourly"]) - 4.82) < 0.01, max(r["hourly"])
    print(f"anchor OK: 1413 2026-10-15 sum={sum(r['hourly']):.4f} "
          f"peak {peak_h}:00 {max(r['hourly']):.2f} method={r['method']}")

    # 3. banding="daily" changes labels only, never values.
    r_old = hr.predict_hourly("1413", "2026-10-15", banding="daily")
    # Exact float equality is not expected: the RF daily total wobbles
    # by ~1 ulp between runs (HOURLY_REPORT §9.4) and the residual
    # lands on the peak hour. Banding must not move any value beyond
    # that float noise.
    max_diff = float(np.max(np.abs(np.array(r_old["hourly"])
                                  - np.array(r["hourly"]))))
    assert max_diff < 1e-9, f"hourly values differ by banding: {max_diff}"
    assert r_old["bands"] != r["bands"], "bands unexpectedly identical"
    print(f"banding check OK: values identical; new bands "
          f"{r['bands'].count('HIGH')} HIGH / "
          f"{r['bands'].count('MEDIUM')} MEDIUM / "
          f"{r['bands'].count('LOW')} LOW vs old "
          f"{r_old['bands'].count('HIGH')} HIGH / "
          f"{r_old['bands'].count('MEDIUM')} MEDIUM / "
          f"{r_old['bands'].count('LOW')} LOW")

    # 4. Vectorised derivation vs live predict_hourly, sample days.
    daily_df = daily_predictions_holdout()
    daily_map = {(row.camera_id, row.date): float(row.predicted)
                 for row in daily_df.itertuples()}
    mats = hm._poisson_matrices()
    reg = joblib.load(BASE / "hourly_model.joblib")
    mw, _X, _mcams, _mdays = model_path_hourly(daily_df, mats, reg)
    sample = [("1413", "2026-10-15"), ("2251", "2026-10-15"),
              ("1412", "2026-10-15"), ("1514", "2026-10-15"),
              ("1413", "2026-10-17"), ("1513", "2026-10-15"),
              ("2252", "2026-10-15")]
    worst = 0.0
    for cam, date in sample:
        live = hr.predict_hourly(cam, date)
        vec = daily_map[(cam, pd.Timestamp(date))] \
            * mw[(cam, pd.Timestamp(date))]
        diff = float(np.max(np.abs(np.array(live["hourly"]) - vec)))
        worst = max(worst, diff)
        print(f"  equiv {cam} {date}: max|live - vectorised| = {diff:.2e}")
    assert worst < 1e-4, f"vectorised derivation diverges: {worst}"
    print(f"derivation equivalence OK (worst {worst:.2e})")

    # 5. Holdout-wide band changes (old vs new) over all camera-hours.
    hourly_all = np.concatenate([
        daily_map[(c, d)] * mw[(c, d)] for (c, d) in daily_map])
    new_b = np.array([hr.risk_band(v, "hourly") for v in hourly_all])
    old_b = np.array([hr.risk_band(v, "daily") for v in hourly_all])
    changed = (new_b != old_b)
    up = ((old_b == "LOW") & (new_b == "MEDIUM")) | \
         ((old_b == "LOW") & (new_b == "HIGH")) | \
         ((old_b == "MEDIUM") & (new_b == "HIGH"))
    down = ((old_b == "MEDIUM") & (new_b == "LOW")) | \
           ((old_b == "HIGH") & (new_b == "MEDIUM")) | \
           ((old_b == "HIGH") & (new_b == "LOW"))
    trans = {}
    for o in ("LOW", "MEDIUM", "HIGH"):
        for n in ("LOW", "MEDIUM", "HIGH"):
            trans[f"{o}->{n}"] = int(((old_b == o) & (new_b == n)).sum())
    print(f"holdout band changes: {int(changed.sum()):,} / {len(hourly_all):,} "
          f"({changed.mean():.1%}) change; up {int(up.sum()):,}, "
          f"down {int(down.sum()):,}")
    print("transition counts:", trans)

    # 6. Worked example: 1413, 2026-10-15, both bandings.
    hours = []
    for h in range(24):
        hours.append({
            "hour": h,
            "predicted": round(r["hourly"][h], 2),
            "band_hourly": r["bands"][h],
            "band_daily": r_old["bands"][h],
            "changed": r["bands"][h] != r_old["bands"][h],
        })
    n_changed = sum(h["changed"] for h in hours)
    n_up = sum(1 for h in hours
               if (h["band_daily"], h["band_hourly"]) in
               [("LOW", "MEDIUM"), ("LOW", "HIGH"), ("MEDIUM", "HIGH")])
    out = {
        "camera_id": "1413",
        "date": "2026-10-15",
        "daily_prediction": r["daily_prediction"],
        "method": r["method"],
        "hourly_sum": round(sum(r["hourly"]), 4),
        "peak_hour": peak_h,
        "peak_value": round(max(r["hourly"]), 2),
        "bands_hourly": {"LOW": 0.1, "MEDIUM": "0.1-<0.5", "HIGH": ">=0.5"},
        "bands_daily": {"LOW": 4, "MEDIUM": "4-<10", "HIGH": ">=10"},
        "hours": hours,
        "n_changed": n_changed,
        "n_up": n_up,
        "holdout_band_changes": {
            "total_changed": int(changed.sum()),
            "total_hours": len(hourly_all),
            "up": int(up.sum()),
            "down": int(down.sum()),
            "transitions": trans,
        },
    }
    OUT_PATH.write_text(json.dumps(out, indent=2))
    print(f"worked example: {n_changed}/24 hours change band "
          f"({n_up} up, {n_changed - n_up} down)")
    print(f"wrote {OUT_PATH}")
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
