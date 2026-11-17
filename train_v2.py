#!/usr/bin/env python3
"""
Redunator Phase 1 (model improvements) — ITEM 1: does the speed join help?

Trains Random Forest (v1 hyper-parameters: 150 trees, max_depth=12)
and XGBoost (v1 hyper-parameters) WITH speed-history features from
speed_features.py, on the SAME time-based holdout as v1, and compares
against the v1 bar. Does NOT touch train.py, features.py, or any v1
model file.

Splits / windows
----------------
  train: 2014-07-01 -> 2026-07-31 (same as v1)
  test PRIMARY (fresh speed):  2026-08-01 -> 2026-09-24  (16,445 rows)
      Every speed lag/roll window here is fully inside observed speed
      data (speed file ends 2026-09-24). Baseline and speed variants
      are compared on THESE SAME ROWS — the apples-to-apples test.
  test FULL (carry-forward):   2026-08-01 -> 2026-10-29  (26,752 rows)
      October speed features are carry-forward values (last observed
      daily count held constant) with speed_age_days marking staleness
      (0 fresh, up to 35). Reported as a secondary run. The v1 bar
      (RF RMSE 3.3068 / MAE 2.1452) is a FULL-holdout number, so a
      ship decision must also clear it here — a win that exists only
      while speed data is fresh cannot ship, because live speed data
      arrives ~5 weeks late and October-style staleness is the normal
      case, not the exception.

Variants (all = v1's 17 features + the listed speed set)
--------------------------------------------------------
  baseline  : v1 features only (reproduces the bar; sanity check)
  spd500    : nearest speed camera within 500 m
  spd200    : nearest speed camera within 200 m
  spd_city  : citywide daily speed total (no neighbour needed)
  spd_all   : spd500 + spd_city

Speed feature columns per set: lag_1d, roll_mean_7d, roll_mean_30d,
mean_pre_test, has_<set> (neighbour sets only), <set>_dist_m
(neighbour sets only), plus speed_age_days in every speed variant.

Ship rule (enforced in code, not just in prose)
-----------------------------------------------
Ship v2 ONLY if a speed variant beats the v1 bar on BOTH the fresh
window (vs the baseline on the same rows) AND the full holdout
(RMSE < 3.3068 and MAE < 2.1452) AND beats the staleness control
(v1 features + speed_age_days only) on the full holdout. The third
leg is the artifact guard: speed_age_days is a "days since Sep 24"
date proxy in October, so a full-window gain the control reproduces
is a date effect, not speed information. Otherwise ship nothing and
this script writes only the metrics JSON documenting the failed
experiment.
Shipping writes redunator_brain_v2.joblib / .onnx / .json + a v2
metrics file, via the same export path as v1 (joblib + skl2onnx,
ONNX verified against native predictions on 2,000 holdout rows).

Usage:
    python train_v2.py [--skip-xgb] [--skip-rf]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
FEATURES_PATH = BASE / "data" / "processed" / "red_light_features_speed.parquet"
METRICS_PATH = BASE / "data" / "processed" / "model_metrics_v2_speed.json"
V2_MODEL_PATH = BASE / "redunator_brain_v2.joblib"
V2_ONNX_PATH = BASE / "redunator_brain_v2.onnx"
V2_SIDECAR_PATH = BASE / "redunator_brain_v2.json"

TRAIN_END = "2026-07-31"
TEST_START = "2026-08-01"
SPEED_END = "2026-09-24"  # last observed speed date; primary window end
TEST_END = "2026-10-29"

# v1 bar (full holdout, from data/processed/model_metrics.json)
BAR_RMSE = 3.3068
BAR_MAE = 2.1452

V1_FEATURES = [
    "dow", "month", "year", "season", "day_of_year",
    "is_weekend", "is_holiday",
    "days_since_first",
    "lag_1d", "roll_mean_7d", "roll_mean_30d",
    "cam_mean_pre_test", "cam_std_pre_test", "cam_n_days_pre_test",
    "latitude", "longitude",
    "cam_code",
]
TARGET = "violations"

SPEED_SETS = {
    "spd500": ["spd500_lag_1d", "spd500_roll_mean_7d", "spd500_roll_mean_30d",
               "spd500_mean_pre_test", "has_spd500", "spd500_dist_m",
               "speed_age_days"],
    "spd200": ["spd200_lag_1d", "spd200_roll_mean_7d", "spd200_roll_mean_30d",
               "spd200_mean_pre_test", "has_spd200", "spd200_dist_m",
               "speed_age_days"],
    "spd_city": ["spd_city_lag_1d", "spd_city_roll_mean_7d",
                 "spd_city_roll_mean_30d", "spd_city_mean_pre_test",
                 "speed_age_days"],
    "spd_all": ["spd500_lag_1d", "spd500_roll_mean_7d", "spd500_roll_mean_30d",
                "spd500_mean_pre_test", "has_spd500", "spd500_dist_m",
                "spd_city_lag_1d", "spd_city_roll_mean_7d",
                "spd_city_roll_mean_30d", "spd_city_mean_pre_test",
                "speed_age_days"],
}

# Control (NOT shippable): v1 features + speed_age_days only. It isolates
# the staleness-flag artifact — speed_age_days is 0 through 2026-09-24
# and ramps 1..35 after that, so it is effectively a "days since Sep 24"
# date proxy in the October window. If a speed variant's FULL-window
# gain is no bigger than this control's, the gain comes from the date
# proxy, not from speed information, and it is not a win.
CONTROL_SETS = {
    "spd_age_only": ["speed_age_days"],
}


def load_and_split():
    df = pd.read_parquet(FEATURES_PATH)
    df["date"] = pd.to_datetime(df["date"])
    train_mask = df["date"] <= TRAIN_END
    train_cams = sorted(df.loc[train_mask, "camera_id"].unique())
    code_map = {c: i for i, c in enumerate(train_cams)}
    df["cam_code"] = df["camera_id"].map(code_map).astype("int32")
    # Same fills as train.py: lag/roll NaN (segment starts, 407 rows) -> 0;
    # lat/lon NaN (4 cameras) -> train medians.
    lag_cols = [c for c in df.columns
                if c.endswith("lag_1d") or "roll_mean" in c]
    for col in lag_cols:
        df[col] = df[col].fillna(0)
    med_lat = df.loc[train_mask, "latitude"].median()
    med_lon = df.loc[train_mask, "longitude"].median()
    df["latitude"] = df["latitude"].fillna(med_lat)
    df["longitude"] = df["longitude"].fillna(med_lon)
    train = df[train_mask]
    test_full = df[df["date"] >= TEST_START]
    test_fresh = test_full[test_full["date"] <= SPEED_END]
    assert set(test_full["camera_id"]) <= set(train["camera_id"])
    return train, test_full, test_fresh, med_lat, med_lon


def metrics(y_true, y_pred):
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    err = y_pred - y_true
    return {"rmse": float(np.sqrt(np.mean(err ** 2))),
            "mae": float(np.mean(np.abs(err)))}


def fit_rf(X_train, y_train):
    from sklearn.ensemble import RandomForestRegressor
    rf = RandomForestRegressor(
        n_estimators=150, max_features=0.5, min_samples_leaf=5,
        max_depth=12, n_jobs=3, random_state=42)
    rf.fit(X_train, y_train)
    return rf


def fit_xgb(X_train, y_train):
    import xgboost as xgb
    xg = xgb.XGBRegressor(
        n_estimators=600, max_depth=8, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8, tree_method="hist",
        n_jobs=3, random_state=42)
    xg.fit(X_train, y_train)
    return xg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-rf", action="store_true")
    ap.add_argument("--skip-xgb", action="store_true")
    ap.add_argument("--only", default=None, choices=[
        "baseline", "spd500", "spd200", "spd_city", "spd_all",
        "spd_age_only"],
        help="run a single variant (checkpointed; resume-safe)")
    args = ap.parse_args()

    t0 = time.time()
    train, test_full, test_fresh, med_lat, med_lon = load_and_split()
    y_train = train[TARGET].to_numpy(dtype=np.float32)
    y_full = test_full[TARGET].to_numpy(dtype=np.float32)
    y_fresh = test_fresh[TARGET].to_numpy(dtype=np.float32)
    print(f"train rows: {len(train):,}  test full: {len(test_full):,}  "
          f"test fresh (<= {SPEED_END}): {len(test_fresh):,}", flush=True)
    print(f"test target mean: full {y_full.mean():.3f}  fresh {y_fresh.mean():.3f}",
          flush=True)

    # --- Resume support: partial results checkpoint after every variant,
    # so a killed run resumes instead of restarting from scratch. ------
    if METRICS_PATH.exists():
        results = json.loads(METRICS_PATH.read_text())
        print(f"resuming from existing {METRICS_PATH} "
              f"(variants done: "
              f"{sorted(results.get('variants', {}))})", flush=True)
    else:
        results = {
            "bar_v1_full_holdout": {"rmse": BAR_RMSE, "mae": BAR_MAE},
            "windows": {"fresh": f"{TEST_START} -> {SPEED_END}",
                        "full": f"{TEST_START} -> {TEST_END}"},
            "variants": {},
        }
    fitted: dict = {}  # (variant, model) -> fitted model, for shipping

    variants = {"baseline": V1_FEATURES}
    variants.update({k: V1_FEATURES + v for k, v in SPEED_SETS.items()})
    variants.update({k: V1_FEATURES + v for k, v in CONTROL_SETS.items()})
    if args.only:
        variants = {args.only: variants[args.only]}

    for name, feats in variants.items():
        done = results["variants"].get(name, {})
        if (not args.skip_rf and "random_forest" in done
                and (args.skip_xgb or "xgboost" in done)):
            print(f"{name:9s} already in {METRICS_PATH} — skipping",
                  flush=True)
            continue
        X_train = train[feats].to_numpy(dtype=np.float32)
        X_full = test_full[feats].to_numpy(dtype=np.float32)
        X_fresh = test_fresh[feats].to_numpy(dtype=np.float32)
        row: dict = done if isinstance(done, dict) else {}
        row["features"] = feats
        if not args.skip_rf and "random_forest" not in row:
            t = time.time()
            rf = fit_rf(X_train, y_train)
            row["random_forest"] = {
                "fresh": metrics(y_fresh, np.clip(rf.predict(X_fresh), 0, None)),
                "full": metrics(y_full, np.clip(rf.predict(X_full), 0, None)),
            }
            fitted[(name, "random_forest")] = rf
            print(f"{name:9s} RF   fresh RMSE "
                  f"{row['random_forest']['fresh']['rmse']:.4f}  MAE "
                  f"{row['random_forest']['fresh']['mae']:.4f}  |  full RMSE "
                  f"{row['random_forest']['full']['rmse']:.4f}  MAE "
                  f"{row['random_forest']['full']['mae']:.4f}  "
                  f"({time.time() - t:.0f}s)", flush=True)
        if not args.skip_xgb and "xgboost" not in row:
            t = time.time()
            xg = fit_xgb(X_train, y_train)
            row["xgboost"] = {
                "fresh": metrics(y_fresh, np.clip(xg.predict(X_fresh), 0, None)),
                "full": metrics(y_full, np.clip(xg.predict(X_full), 0, None)),
            }
            fitted[(name, "xgboost")] = xg
            print(f"{name:9s} XGB  fresh RMSE "
                  f"{row['xgboost']['fresh']['rmse']:.4f}  MAE "
                  f"{row['xgboost']['fresh']['mae']:.4f}  |  full RMSE "
                  f"{row['xgboost']['full']['rmse']:.4f}  MAE "
                  f"{row['xgboost']['full']['mae']:.4f}  "
                  f"({time.time() - t:.0f}s)", flush=True)
        results["variants"][name] = row
        results["complete"] = False
        METRICS_PATH.write_text(json.dumps(results, indent=2))
        print(f"checkpointed {name} -> {METRICS_PATH}", flush=True)

    if args.only:
        print("single-variant run done (no ship decision); "
              "run without --only to finalise.", flush=True)
        print(f"total elapsed: {time.time() - t0:.0f}s", flush=True)
        return

    # --- Ship decision -----------------------------------------------------
    # A speed variant ships only if it beats the v1 bar on the FULL
    # holdout (the bar's window) AND beats the same-run baseline on the
    # fresh window AND beats the staleness control (v1 + speed_age_days
    # only) on the full window. The third leg is the artifact guard:
    # speed_age_days is a "days since Sep 24" date proxy in October, so
    # a gain that the control reproduces is a date effect, not speed
    # information, and does not count as a win. RF is the bar's model;
    # XGB is reported, not shipped on its own (v1's winner was RF).
    ship = None
    control = results["variants"].get("spd_age_only", {}).get("random_forest")
    for name in SPEED_SETS:
        rf = results["variants"][name].get("random_forest")
        base = results["variants"]["baseline"].get("random_forest")
        if not rf or not base:
            continue
        fresh_win = (rf["fresh"]["rmse"] < base["fresh"]["rmse"]
                     and rf["fresh"]["mae"] < base["fresh"]["mae"])
        full_win = (rf["full"]["rmse"] < BAR_RMSE
                    and rf["full"]["mae"] < BAR_MAE)
        beats_control = True
        if control:
            beats_control = (rf["full"]["rmse"] < control["full"]["rmse"]
                             and rf["full"]["mae"] < control["full"]["mae"])
        if fresh_win and full_win and beats_control:
            ship = (name, "random_forest")
            break
    results["ship_decision"] = {
        "shipped": ship is not None,
        "variant": ship[0] if ship else None,
        "model": ship[1] if ship else None,
        "rule": "RF speed variant must beat same-run baseline on the fresh "
                "window (2026-08-01 -> 2026-09-24) AND beat the v1 bar "
                "(RMSE 3.3068 / MAE 2.1452) on the full holdout "
                "(2026-08-01 -> 2026-10-29, carry-forward October) AND "
                "beat the staleness control (v1 + speed_age_days only) "
                "on the full holdout — a gain the control reproduces is "
                "a date effect, not speed information.",
    }
    results["complete"] = True
    METRICS_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {METRICS_PATH}")

    if ship:
        name, model_kind = ship
        if (name, model_kind) not in fitted:
            # Resumed run: the winner was fitted in an earlier (killed)
            # process, so refit it once here for shipping.
            print(f"refitting {name} for shipping (cached result)...",
                  flush=True)
            feats_all = V1_FEATURES + SPEED_SETS[name]
            fitted[(name, model_kind)] = fit_rf(
                train[feats_all].to_numpy(dtype=np.float32), y_train)
        model = fitted[(name, model_kind)]
        feats = variants[name]
        joblib.dump(model, V2_MODEL_PATH)
        sidecar = {
            "model": model_kind,
            "model_file": V2_MODEL_PATH.name,
            "variant": name,
            "features": feats,
            "target": TARGET,
            "train_start": "2014-07-01",
            "train_end": TRAIN_END,
            "test_start": TEST_START,
            "test_end": TEST_END,
            "speed_data_end": SPEED_END,
            "train_rows": int(len(train)),
            "test_rows": int(len(test_full)),
            "test_fresh_rows": int(len(test_fresh)),
            "metrics": results["variants"][name][model_kind],
            "bar_v1": {"rmse": BAR_RMSE, "mae": BAR_MAE},
            "notes": {
                "speed_join": "nearest speed camera within 500 m "
                              "(spd500) or 200 m (spd200); see "
                              "SPEED_JOIN_FINDINGS.md",
                "october_handling": "speed features carry-forwarded past "
                                    "2026-09-24 with speed_age_days flag",
                "clip": "predictions clipped at 0",
            },
        }
        V2_SIDECAR_PATH.write_text(json.dumps(sidecar, indent=2))
        print(f"SHIPPED v2 ({name}, {model_kind}): saved {V2_MODEL_PATH} + "
              f"{V2_SIDECAR_PATH}")
        try:
            from skl2onnx import to_onnx
            import onnxruntime as ort
            X_sample = train[feats].to_numpy(dtype=np.float32)[:100]
            onx = to_onnx(model, X_sample, options={"zipmap": False})
            V2_ONNX_PATH.write_bytes(onx.SerializeToString())
            sess = ort.InferenceSession(str(V2_ONNX_PATH),
                                        providers=["CPUExecutionProvider"])
            sample = test_full[feats].to_numpy(dtype=np.float32)[:2000]
            onnx_pred = sess.run(None, {sess.get_inputs()[0].name: sample})[0]
            native_pred = model.predict(sample)
            diff = float(np.abs(onnx_pred.ravel() - native_pred).max())
            print(f"saved {V2_ONNX_PATH} "
                  f"({V2_ONNX_PATH.stat().st_size / 1e6:.1f} MB); "
                  f"onnx vs native max abs diff: {diff:.6f}")
            sidecar["onnx_max_abs_diff_2000_rows"] = diff
            V2_SIDECAR_PATH.write_text(json.dumps(sidecar, indent=2))
        except Exception as e:
            print(f"ONNX export skipped/failed: {type(e).__name__}: {e}")
    else:
        print("NO SHIP: no speed variant beat the bar on both windows. "
              "v1 files untouched; see SPEED_JOIN_FINDINGS.md.")

    print(f"total elapsed: {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
