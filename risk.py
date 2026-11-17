#!/usr/bin/env python3
"""Redunator Phase 2 — Step 3b: risk scoring + per-camera profiles.

Maps a predicted daily violation count to a risk score in [0, 1] and a
LOW / MEDIUM / HIGH band, and builds per-camera typical-day profiles.

Thresholds are NOT hand-picked: they are derived from the distribution
of the winning model's predictions on the holdout (2026-08-01 ->
2026-10-29, 26,752 camera-days) and written to
data/processed/risk_thresholds.json, so they are reproducible and
auditable. See that file (and REPORT.md) for the exact numbers.

Definitions (computed here from the holdout predictions):
  med_cut  = round(p50 of predicted daily counts)   -> LOW / MEDIUM line
  high_cut = round(p90 of predicted daily counts)   -> MEDIUM / HIGH line
  scale    = round(p99 of predicted daily counts, up to nearest 5)
             -> risk score saturates at 1.0 here

  risk_score(pred) = clip(pred / scale, 0, 1)
  band(pred)       = LOW    if pred <  med_cut
                     MEDIUM if med_cut <= pred < high_cut
                     HIGH   if pred >= high_cut

Why quantiles: a "high risk" day should mean "worse than ~90% of
camera-days", not an arbitrary round number. The score is linear in the
predicted count up to the 99th percentile, so it stays interpretable:
score 0.5 ~ half the p99 count.

Per-camera profiles (data/processed/camera_profiles.csv): for each of
the 300 test cameras, the median predicted violations across its test
weekdays and across its test weekend days — i.e. what a "typical"
weekday / weekend looks like at that intersection — plus the matching
risk bands and scores, and the camera's actual test-window mean for
reference.

Unseen cameras: a camera with no pre-test history has no cam_code and
no cam_mean_pre_test. predict_camera() falls back to that camera's
historical mean if known, else the citywide pre-test mean
(~5.3 violations/day). The shipped model itself only ever scores the
300 cameras it was trained on.

Usage:
  ~/workspace/redunator-venv/bin/python risk.py
"""

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
FEATURES_PATH = BASE / "data" / "processed" / "red_light_features.parquet"
MODEL_PATH = BASE / "redunator_brain_v1.joblib"
SIDECAR_PATH = BASE / "redunator_brain_v1.json"
THRESHOLDS_PATH = BASE / "data" / "processed" / "risk_thresholds.json"
PROFILES_PATH = BASE / "data" / "processed" / "camera_profiles.csv"

TEST_START = "2026-08-01"


def risk_score(pred, scale):
    """Linear 0-1 score, saturating at the p99 predicted count."""
    return np.clip(np.asarray(pred, dtype=float) / scale, 0, 1)


def band(pred, med_cut, high_cut):
    pred = np.asarray(pred, dtype=float)
    out = np.full(pred.shape, "LOW", dtype=object)
    out[(pred >= med_cut) & (pred < high_cut)] = "MEDIUM"
    out[pred >= high_cut] = "HIGH"
    return out


def main():
    sidecar = json.loads(SIDECAR_PATH.read_text())
    features = sidecar["features"]
    model = joblib.load(MODEL_PATH)

    df = pd.read_parquet(FEATURES_PATH)
    df["date"] = pd.to_datetime(df["date"])

    # cam_code is not stored in the parquet; rebuild it exactly as
    # train.py does (sorted train-camera list, 2014-07-01 -> 2026-07-31).
    train_cams = sorted(df.loc[df["date"] <= "2026-07-31", "camera_id"].unique())
    code_map = {c: i for i, c in enumerate(train_cams)}
    df["cam_code"] = df["camera_id"].map(code_map).astype("int32")
    df["lag_1d"] = df["lag_1d"].fillna(0)
    df["roll_mean_7d"] = df["roll_mean_7d"].fillna(0)
    df["roll_mean_30d"] = df["roll_mean_30d"].fillna(0)
    df["latitude"] = df["latitude"].fillna(df["latitude"].median())
    df["longitude"] = df["longitude"].fillna(df["longitude"].median())

    test = df[df["date"] >= TEST_START].copy()
    print(f"holdout rows: {len(test):,}  cameras: {test['camera_id'].nunique()}")

    X_test = test[features].to_numpy(dtype=np.float32)
    pred = np.clip(model.predict(X_test), 0, None)
    test["predicted"] = pred

    qs = np.quantile(pred, [0.5, 0.75, 0.9, 0.95, 0.99])
    med_cut = int(round(qs[0]))
    high_cut = int(round(qs[2]))
    scale = int(np.ceil(qs[4] / 5.0) * 5)
    thresholds = {
        "derived_from": "winning-model predictions on holdout "
                        "2026-08-01 -> 2026-10-29 (26,752 camera-days)",
        "predicted_count_quantiles": {
            "p50": round(float(qs[0]), 2),
            "p75": round(float(qs[1]), 2),
            "p90": round(float(qs[2]), 2),
            "p95": round(float(qs[3]), 2),
            "p99": round(float(qs[4]), 2),
            "max": round(float(pred.max()), 2),
        },
        "med_cut": med_cut,
        "high_cut": high_cut,
        "score_scale": scale,
        "bands": {
            "LOW": f"predicted < {med_cut} violations/day",
            "MEDIUM": f"{med_cut} <= predicted < {high_cut} violations/day",
            "HIGH": f"predicted >= {high_cut} violations/day",
        },
        "score": f"risk_score = clip(predicted / {scale}, 0, 1)",
    }
    THRESHOLDS_PATH.write_text(json.dumps(thresholds, indent=2))
    print("thresholds:", json.dumps(thresholds, indent=2))

    test["risk_score"] = risk_score(pred, scale)
    test["band"] = band(pred, med_cut, high_cut)

    # --- Per-camera typical-day profiles ----------------------------------
    rows = []
    for (cam, inter), g in test.groupby(["camera_id", "intersection"]):
        wd = g[g["is_weekend"] == 0]["predicted"]
        we = g[g["is_weekend"] == 1]["predicted"]
        rows.append({
            "camera_id": cam,
            "intersection": inter,
            "latitude": g["latitude"].median(),
            "longitude": g["longitude"].median(),
            "n_test_days": len(g),
            "typical_weekday_pred": round(float(wd.median()), 2),
            "typical_weekend_pred": round(float(we.median()), 2),
            "typical_weekday_band": band([wd.median()], med_cut, high_cut)[0],
            "typical_weekend_band": band([we.median()], med_cut, high_cut)[0],
            "typical_weekday_score": round(float(risk_score([wd.median()], scale)[0]), 3),
            "typical_weekend_score": round(float(risk_score([we.median()], scale)[0]), 3),
            "actual_test_mean": round(float(g["violations"].mean()), 2),
            "cam_mean_pre_test": round(float(g["cam_mean_pre_test"].iloc[0]), 2),
        })
    profiles = pd.DataFrame(rows).sort_values(
        "typical_weekday_pred", ascending=False)
    profiles.to_csv(PROFILES_PATH, index=False)
    print(f"wrote {PROFILES_PATH} ({len(profiles)} cameras)")
    print(profiles.head(10)[["intersection", "typical_weekday_pred",
                             "typical_weekday_band", "actual_test_mean"]]
          .to_string(index=False))


if __name__ == "__main__":
    main()
