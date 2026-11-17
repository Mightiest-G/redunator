#!/usr/bin/env python3
"""Redunator Phase 2 — Step 3: train the ticket-risk models.

Trains two regressors predicting daily `violations` per red-light camera
from ~/workspace/redunator/data/processed/red_light_features.parquet.

Split (TIME-BASED ONLY — never random; violations are strongly
autocorrelated per camera, so a random split would leak):
  train: 2014-07-01 -> 2026-07-31  (1,331,343 rows)
  test:  2026-08-01 -> 2026-10-29  (26,752 rows, 300 cameras)

Models:
  * Random Forest (scikit-learn)
  * XGBoost (hist tree method)

Plus a NAIVE BASELINE: predict each camera's pre-test historical daily
mean (cam_mean_pre_test) for every test day. Any model that cannot beat
that is not worth shipping.

The winning model (lowest test RMSE) is saved as
redunator_brain_v1.joblib with a sidecar JSON carrying its feature list
and training metadata. Metrics are written to
data/processed/model_metrics.json and printed to stdout.

Usage:
  ~/workspace/redunator-venv/bin/python train.py [--skip-rf] [--skip-xgb]
"""

import argparse
import json
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
FEATURES_PATH = BASE / "data" / "processed" / "red_light_features.parquet"
METRICS_PATH = BASE / "data" / "processed" / "model_metrics.json"
MODEL_PATH = BASE / "redunator_brain_v1.joblib"
SIDECAR_PATH = BASE / "redunator_brain_v1.json"

TRAIN_END = "2026-07-31"   # last train date (inclusive)
TEST_START = "2026-08-01"  # first test date (inclusive)

# --- Features ---------------------------------------------------------------
# EXCLUDED on purpose: is_observed (a data-availability flag, not a
# predictor), intersection / holiday_name (strings), segment_id (an
# artefact of the zero-fill segmentation), date (the split key).
FEATURES = [
    "dow", "month", "year", "season", "day_of_year",
    "is_weekend", "is_holiday",
    "days_since_first",
    "lag_1d", "roll_mean_7d", "roll_mean_30d",
    "cam_mean_pre_test", "cam_std_pre_test", "cam_n_days_pre_test",
    "latitude", "longitude",
    "cam_code",  # integer code for camera_id (see note below)
]
TARGET = "violations"

# cam_code note: the model learns a per-camera level from this integer
# code. This is legitimate here because EVERY test camera (300/300) has
# pre-test history in the training window — no unseen camera is ever
# scored. A brand-new camera with no history would get the median code's
# behaviour; risk.py handles that case by falling back to the
# cam_mean_pre_test-only path (see risk.py). The code is assigned from
# the sorted train camera list so it is stable across re-runs.


def load_and_split():
    df = pd.read_parquet(FEATURES_PATH)
    df["date"] = pd.to_datetime(df["date"])

    # Integer camera code, fit on train cameras only (all test cameras
    # are in train, verified in FEATURES_SUMMARY.md).
    train_mask = df["date"] <= TRAIN_END
    train_cams = sorted(df.loc[train_mask, "camera_id"].unique())
    # A brand-new camera has no train history, so no cam_code exists
    # for it: drop its rows and say so instead of failing the whole
    # run (decision 2026-11-17: skip-and-log, not fail-closed). Its
    # rows are all in the test window by construction (test starts
    # the day after train ends), so the holdout stays comparable.
    new_cams = sorted(set(df["camera_id"]) - set(train_cams))
    if new_cams:
        print(f"WARNING: dropping {len(new_cams)} camera(s) with no "
              f"train history from this run: {new_cams}")
        df = df[~df["camera_id"].isin(new_cams)].copy()
        # Re-derive the mask: the drop leaves a non-contiguous index
        # and the old boolean Series would misalign against it.
        train_mask = df["date"] <= TRAIN_END
    code_map = {c: i for i, c in enumerate(train_cams)}
    df["cam_code"] = df["camera_id"].map(code_map).astype("int32")

    # Lag/rolling NaNs occur only on segment-start days (407 rows):
    # no prior day exists inside the segment, so 0 is the honest fill.
    for col in ("lag_1d", "roll_mean_7d", "roll_mean_30d"):
        df[col] = df[col].fillna(0)

    # Lat/lon NaN for the 4 cameras that never have coordinates
    # (1623, 2552, 2592, 2662). Impute with the train median so the
    # model can still score them; they are a tiny share of rows.
    med_lat = df.loc[train_mask, "latitude"].median()
    med_lon = df.loc[train_mask, "longitude"].median()
    df["latitude"] = df["latitude"].fillna(med_lat)
    df["longitude"] = df["longitude"].fillna(med_lon)

    train = df[train_mask]
    test = df[df["date"] >= TEST_START]
    assert len(train) + len(test) == len(df), "split does not cover all rows"
    assert set(test["camera_id"]) <= set(train["camera_id"]), \
        "test contains a camera with no train history"
    return train, test, med_lat, med_lon


def metrics(y_true, y_pred):
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    err = y_pred - y_true
    return {
        "rmse": float(np.sqrt(np.mean(err ** 2))),
        "mae": float(np.mean(np.abs(err))),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-rf", action="store_true")
    ap.add_argument("--skip-xgb", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    train, test, med_lat, med_lon = load_and_split()
    X_train = train[FEATURES].to_numpy(dtype=np.float32)
    y_train = train[TARGET].to_numpy(dtype=np.float32)
    X_test = test[FEATURES].to_numpy(dtype=np.float32)
    y_test = test[TARGET].to_numpy(dtype=np.float32)
    print(f"train rows: {len(train):,}  test rows: {len(test):,}")
    print(f"train target mean: {y_train.mean():.3f}  test target mean: {y_test.mean():.3f}")

    results = {}

    # --- Naive baseline: each camera's pre-test historical daily mean ---
    base_pred = test["cam_mean_pre_test"].to_numpy(dtype=np.float32)
    results["naive_baseline"] = metrics(y_test, base_pred)
    print(f"naive baseline  RMSE {results['naive_baseline']['rmse']:.4f}  "
          f"MAE {results['naive_baseline']['mae']:.4f}")

    models = {}

    if not args.skip_rf:
        from sklearn.ensemble import RandomForestRegressor

        # max_depth=12 is deliberate, not a guess: the first run used
        # unbounded trees and scored RMSE 3.3116 / MAE 2.1641 — but the
        # file was 2.9 GB (150 trees x ~266k nodes each). A max_depth=12
        # retrain scored slightly BETTER on the same holdout
        # (RMSE 3.3068, MAE 2.1452) at 56 MB, so depth 12 is what ships.
        rf = RandomForestRegressor(
            n_estimators=150,
            max_features=0.5,
            min_samples_leaf=5,
            max_depth=12,
            n_jobs=3,
            random_state=42,
        )
        t = time.time()
        rf.fit(X_train, y_train)
        pred = np.clip(rf.predict(X_test), 0, None)
        results["random_forest"] = metrics(y_test, pred)
        print(f"random forest   RMSE {results['random_forest']['rmse']:.4f}  "
              f"MAE {results['random_forest']['mae']:.4f}  "
              f"({time.time() - t:.0f}s)")
        models["random_forest"] = rf

    if not args.skip_xgb:
        import xgboost as xgb

        xg = xgb.XGBRegressor(
            n_estimators=600,
            max_depth=8,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            tree_method="hist",
            n_jobs=3,
            random_state=42,
        )
        t = time.time()
        xg.fit(X_train, y_train)
        pred = np.clip(xg.predict(X_test), 0, None)
        results["xgboost"] = metrics(y_test, pred)
        print(f"xgboost         RMSE {results['xgboost']['rmse']:.4f}  "
              f"MAE {results['xgboost']['mae']:.4f}  "
              f"({time.time() - t:.0f}s)")
        models["xgboost"] = xg

    # --- Pick the winner (lowest test RMSE) and save it -------------------
    scored = {k: v for k, v in results.items() if k != "naive_baseline"}
    winner_name = min(scored, key=lambda k: scored[k]["rmse"])
    winner = models[winner_name]
    joblib.dump(winner, MODEL_PATH)

    sidecar = {
        "model": winner_name,
        "model_file": MODEL_PATH.name,
        "features": FEATURES,
        "target": TARGET,
        "train_start": "2014-07-01",
        "train_end": TRAIN_END,
        "test_start": TEST_START,
        "test_end": "2026-10-29",
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
        "train_cameras": int(train["camera_id"].nunique()),
        "test_cameras": int(test["camera_id"].nunique()),
        "metrics": results,
        "notes": {
            "cam_code": "integer code for camera_id, fit on sorted train "
                        "cameras; all 300 test cameras have train history",
            "lag_fill": "lag_1d/roll_mean_7d/roll_mean_30d NaN (segment "
                        "starts) filled with 0",
            "latlon_fill": f"NaN lat/lon imputed with train medians "
                           f"({med_lat:.4f}, {med_lon:.4f})",
            "clip": "predictions clipped at 0 (no negative violations)",
        },
    }
    SIDECAR_PATH.write_text(json.dumps(sidecar, indent=2))
    METRICS_PATH.write_text(json.dumps(
        {"split": {"train_end": TRAIN_END, "test_start": TEST_START},
         "metrics": results}, indent=2))

    print(f"\nWINNER: {winner_name} "
          f"(RMSE {scored[winner_name]['rmse']:.4f} vs naive "
          f"{results['naive_baseline']['rmse']:.4f})")
    print(f"saved {MODEL_PATH} + {SIDECAR_PATH}")
    print(f"total elapsed: {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
