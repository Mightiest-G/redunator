#!/usr/bin/env python3
"""One-off companion to train.py (not part of the core pipeline).

1. Retrains the XGBoost model with the exact hyper-parameters/seed from
   train.py and saves it as a COMPACT serving alternative
   (redunator_brain_v1_xgboost.joblib). The full Random Forest winner is
   2.9 GB (150 unbounded trees, ~266k nodes each) — accurate, but not
   shippable; XGBoost is within 1.5% on RMSE and has the better MAE.
2. Exports that XGBoost model to ONNX and verifies the ONNX predictions
   match the native model on a sample of holdout rows.
3. Trains a depth-limited Random Forest (max_depth=12) as a servable-RF
   experiment and reports its holdout metrics + file size.

Usage:
  ~/workspace/redunator-venv/bin/python export_models.py
"""

import json
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
FEATURES_PATH = BASE / "data" / "processed" / "red_light_features.parquet"
SIDECAR_PATH = BASE / "redunator_brain_v1.json"
XGB_PATH = BASE / "redunator_brain_v1_xgboost.joblib"
XGB_ONNX_PATH = BASE / "redunator_brain_v1_xgboost.onnx"
RF12_PATH = BASE / "redunator_brain_v1_rf_depth12.joblib"

TRAIN_END = "2026-07-31"
TEST_START = "2026-08-01"


def load_split():
    sidecar = json.loads(SIDECAR_PATH.read_text())
    features = sidecar["features"]
    df = pd.read_parquet(FEATURES_PATH)
    df["date"] = pd.to_datetime(df["date"])
    train_cams = sorted(df.loc[df["date"] <= TRAIN_END, "camera_id"].unique())
    code_map = {c: i for i, c in enumerate(train_cams)}
    df["cam_code"] = df["camera_id"].map(code_map).astype("int32")
    for col in ("lag_1d", "roll_mean_7d", "roll_mean_30d"):
        df[col] = df[col].fillna(0)
    df["latitude"] = df["latitude"].fillna(df["latitude"].median())
    df["longitude"] = df["longitude"].fillna(df["longitude"].median())
    train = df[df["date"] <= TRAIN_END]
    test = df[df["date"] >= TEST_START]
    X_train = train[features].to_numpy(dtype=np.float32)
    y_train = train["violations"].to_numpy(dtype=np.float32)
    X_test = test[features].to_numpy(dtype=np.float32)
    y_test = test["violations"].to_numpy(dtype=np.float32)
    return features, X_train, y_train, X_test, y_test


def rmse_mae(y, p):
    p = np.clip(p, 0, None)
    err = p - y
    return float(np.sqrt(np.mean(err ** 2))), float(np.mean(np.abs(err)))


def main():
    features, X_train, y_train, X_test, y_test = load_split()

    import xgboost as xgb

    t = time.time()
    xg = xgb.XGBRegressor(
        n_estimators=600, max_depth=8, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8, tree_method="hist",
        n_jobs=3, random_state=42,
    )
    xg.fit(X_train, y_train)
    print(f"xgb retrain {time.time() - t:.0f}s  "
          f"RMSE/MAE {rmse_mae(y_test, xg.predict(X_test))}")
    joblib.dump(xg, XGB_PATH)
    print(f"saved {XGB_PATH} ({XGB_PATH.stat().st_size / 1e6:.1f} MB)")

    # --- ONNX export + verification ---------------------------------------
    try:
        from skl2onnx import to_onnx
        import onnxruntime as ort

        onx = to_onnx(xg, X_train[:100].astype(np.float32),
                      options={"zipmap": False})
        XGB_ONNX_PATH.write_bytes(onx.SerializeToString())
        print(f"saved {XGB_ONNX_PATH} "
              f"({XGB_ONNX_PATH.stat().st_size / 1e6:.1f} MB)")
        sess = ort.InferenceSession(str(XGB_ONNX_PATH),
                                    providers=["CPUExecutionProvider"])
        sample = X_test[:2000]
        onnx_pred = sess.run(None, {sess.get_inputs()[0].name: sample})[0]
        native_pred = xg.predict(sample)
        print(f"onnx vs native max abs diff: "
              f"{np.abs(onnx_pred.ravel() - native_pred).max():.6f}")
    except Exception as e:  # ONNX is nice-to-have, not required
        print(f"ONNX export skipped/failed: {type(e).__name__}: {e}")

    # --- Depth-limited RF experiment ---------------------------------------
    from sklearn.ensemble import RandomForestRegressor

    t = time.time()
    rf12 = RandomForestRegressor(
        n_estimators=150, max_features=0.5, min_samples_leaf=5,
        max_depth=12, n_jobs=3, random_state=42,
    )
    rf12.fit(X_train, y_train)
    r, m = rmse_mae(y_test, rf12.predict(X_test))
    print(f"rf depth=12 fit {time.time() - t:.0f}s  RMSE {r:.4f}  MAE {m:.4f}")
    joblib.dump(rf12, RF12_PATH)
    print(f"saved {RF12_PATH} ({RF12_PATH.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
