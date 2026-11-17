#!/usr/bin/env python3
"""Phase 2d — best-combination check: does a post-hoc slowness blend help
the retrained B12 model (12 shipped features, full pre-holdout train)?

Loads hourly_model_2d_B12.joblib, scores it on the Phase 2 holdout
(2026-08-01 -> 2026-10-29) as raw counts and as shape x pre-holdout
rate, then applies the slowness multiplier (full and half strength)
post-hoc to the shape, same protocol as validate_hourly_2d.py.
Also re-scores the shipped model for reference (numbers must match
the recorded bars: raw 0.093342, shape 0.093335).

Writes nothing except stdout; the numbers go into
hourly_model_2d_metrics.json under "B12_blends" by hand (see run log)
— actually this script appends them itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

import hourly_model as hm
from train_hourly_2d import build_design_2d, _holdout_scores
from validate_hourly import _metrics
from validate_hourly_2d import slowness_mult_matrix
from validate_hourly_demand import _day_type_series

BASE = Path(__file__).resolve().parent
N_PRE_DAYS = 577


def main():
    print("loading matrices ...")
    mats = hm._poisson_matrices()
    hold_dates = pd.date_range("2026-08-01", "2026-10-29", freq="D")
    day_types = _day_type_series(hold_dates)
    area_map = hm.load_camera_area_map()
    cam_area = {str(r["camera_id"]): (int(r["community_area"])
                                      if pd.notna(r["community_area"]) else 0)
                for _, r in area_map.iterrows()}
    from validate_hourly_2d import load_speed_profiles
    speed_prof = load_speed_profiles()
    cams = mats["cams"]
    sm_full = slowness_mult_matrix(cams, cam_area, day_types, speed_prof, 1.0)
    sm_half = slowness_mult_matrix(cams, cam_area, day_types, speed_prof, 0.5)

    n_c = len(cams)
    n_t_hold = 90 * 24
    daily = mats["daily"]
    pre_day_idx = int((pd.Timestamp("2026-07-31") - mats["days"][0]).days) + 1
    rate_pre = daily[:, :pre_day_idx].sum(axis=1) / N_PRE_DAYS
    y = mats["crash_mat"][:, int((pd.Timestamp("2026-08-01") - mats["times"][0])
                                 / pd.Timedelta(hours=1)):
                            int((pd.Timestamp("2026-10-29 23:00") - mats["times"][0])
                                / pd.Timedelta(hours=1)) + 1].astype(float)
    y_flat = y.reshape(-1)

    def _renorm(day):
        denom = day.sum(axis=2, keepdims=True)
        return day / np.where(denom > 0, denom, 1.0)

    out = {}
    for label, path, rate_end in (
            ("B12", BASE / "hourly_model_2d_B12.joblib", "2026-07-31"),
            ("B", BASE / "hourly_model_2d_B.joblib", "2026-07-31"),
            ("shipped", hm.POISSON_MODEL_PATH, "2026-02-28")):
        reg = joblib.load(path)
        extra = ("demand", "slowness") if label == "B" else ()
        Xh, yh = build_design_2d(mats, "2026-08-01",
                                 pd.Timestamp("2026-10-29") + pd.Timedelta(hours=23),
                                 rate_end, extra=extra)[:2]
        pred_raw = np.clip(reg.predict(Xh), 0, None)
        day = pred_raw.reshape(n_c, 90, 24)
        shape = (_renorm(day) * rate_pre[:, None, None]).reshape(n_c, n_t_hold)
        rows = {}
        for name, pred in (
                ("raw_counts", pred_raw),
                ("shape", shape.reshape(-1)),
                ("shape_plus_slowness", (_renorm(day * sm_full)
                                         * rate_pre[:, None, None]).reshape(-1)),
                ("shape_plus_slowness_half", (_renorm(day * sm_half)
                                              * rate_pre[:, None, None]).reshape(-1))):
            m = _metrics(y_flat, pred)
            ph = pred.reshape(n_c, 90, 24).sum(axis=(0, 1))
            m["pred_peak_hour"] = int(np.argmax(ph))
            rows[name] = m
            print(f"{label:8s} {name:28s} dev {m['mean_poisson_deviance']:.6f} "
                  f"RMSE {m['rmse']:.6f} MAE {m['mae']:.6f} "
                  f"pred {m['pred_total']:.0f} peak {m['pred_peak_hour']}:00")
        out[label] = rows
    metrics = json.loads((BASE / "hourly_model_2d_metrics.json").read_text())
    metrics["B12_blends"] = out
    (BASE / "hourly_model_2d_metrics.json").write_text(json.dumps(metrics, indent=2))
    print("appended B12_blends to hourly_model_2d_metrics.json")


if __name__ == "__main__":
    main()
