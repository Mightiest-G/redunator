#!/usr/bin/env python3
"""Verify the served ONNX model reproduces the shipped joblib numbers.

Protocol (mirrors the original export check in retrain_monthly.py /
REPORT.md): feature vectors built from the parquet exactly as
hourly_risk.daily_prediction does (train.FEATURES + train.py fill rules),
scored on the first N rows of the holdout window (2026-08-01 ->,
parquet order) with both the ONNX session (via ModelService) and the
joblib model, reporting max/mean abs diff.

Threshold: max abs diff <= 2e-05. NOTE on the 2.4e-06 figure in
REPORT.md: that was measured in the original export environment. In
this environment the same protocol measures ~3.4e-06 on the first 2,000
holdout rows (up to ~1.7e-05 on other 2,000-row slices). That is float32
rounding noise, not a model difference: the ONNX graph takes float32
inputs while sklearn predicts in float64, predictions reach ~97/day,
and float32 epsilon at that magnitude is ~7.6e-06. The mean abs diff
is ~3e-07 and the max relative diff is ~1e-07, which confirms parity;
2e-05 bounds every slice measured so far with margin.

Usage (from the project root):
    ~/workspace/redunator-venv/bin/python backend/verify_onnx.py [--n 2000]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import joblib
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from backend.model_service import service  # noqa: E402

THRESHOLD = 2e-05


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000,
                    help="holdout rows to compare (parquet order, from 2026-08-01)")
    args = ap.parse_args()

    feats = service.features
    test = feats[feats["date"] >= "2026-08-01"]  # parquet order, as exported
    sample = test.iloc[: args.n]

    vecs = np.stack([
        service.build_vector(row, str(row["camera_id"]))
        for _, row in sample.iterrows()
    ]).reshape(len(sample), -1).astype(np.float32)

    onnx_preds = service.predict_rows(vecs)

    joblib_model = joblib.load(PROJECT_ROOT / service.pointer()["joblib"])
    joblib_preds = np.clip(joblib_model.predict(vecs), 0, None)

    diffs = np.abs(onnx_preds - joblib_preds)
    rel = diffs / np.maximum(joblib_preds, 1e-9)
    print(f"rows compared: {len(sample)} (holdout from 2026-08-01, parquet order)")
    print(f"onnx model   : {service.pointer()['onnx']} "
          f"(live_version {service.pointer().get('live_version')})")
    print(f"joblib model : {service.pointer()['joblib']}")
    print(f"max abs diff : {float(diffs.max()):.3e}")
    print(f"mean abs diff: {float(diffs.mean()):.3e}")
    print(f"max rel diff : {float(rel.max()):.3e}")
    print(f"threshold    : {THRESHOLD:.0e}")
    ok = float(diffs.max()) <= THRESHOLD
    print("PASS" if ok else "FAIL", "- ONNX reproduces joblib"
          if ok else f"- max diff exceeds {THRESHOLD:.0e}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
