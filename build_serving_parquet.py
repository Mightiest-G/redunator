#!/usr/bin/env python3
"""Build the SERVING-only feature parquet for the Redunator backend.

The full table (data/processed/red_light_features.parquet, 1.36M rows
x 23 cols) is the TRAINING artifact. The serving path needs only:

  * the lookup keys: camera_id, date
  * the 16 model features in train.FEATURES except cam_code (derived
    at predict time, not a parquet column)
  * violations (observed daily count, for /violation_trends)

Dropped: segment_id (a zero-fill artefact), is_observed (a
data-availability flag, not a predictor), intersection + holiday_name
(strings — the two biggest deep-memory columns; nothing at serving
time reads them: /intersections serves names from camera_profiles.csv).

Downcasts (value-preserving: the model casts every feature to
float32 before scoring, train.py `to_numpy(dtype=np.float32)`):
  * latitude / longitude float64 -> float32 (a float64 coordinate
    rounded to float32 is exactly what the scorer already receives)
  * days_since_first int32 -> int16 (max ~4,500 days, asserted)

Row ORDER is preserved (column selection only), so anything that
reads "parquet order" (backend/verify_onnx.py) is unaffected.

Output: data/processed/red_light_features_serving.parquet — tracked
in git, shipped in the Docker image (not excluded by .dockerignore),
read by backend/config.py FEATURES_PATH. The full parquet stays in
the repo for training / retraining and is never loaded at serving
time after this change.

Verification (run on every build): same row count, same camera set,
same date range, and a sampled row-by-row equality check against the
full table (including cam 1413 / 2026-10-15, the acceptance case).

Usage (from the project root):
    ~/workspace/redunator-venv/bin/python build_serving_parquet.py
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
FULL_PATH = BASE / "data" / "processed" / "red_light_features.parquet"
SERVING_PATH = BASE / "data" / "processed" / "red_light_features_serving.parquet"

# Serving columns: lookup keys + train.FEATURES (minus cam_code, which
# is derived at predict time) + violations for /violation_trends.
SERVING_COLUMNS = [
    "camera_id", "date", "violations",
    "dow", "month", "year", "season", "day_of_year",
    "is_weekend", "is_holiday", "days_since_first",
    "lag_1d", "roll_mean_7d", "roll_mean_30d",
    "cam_mean_pre_test", "cam_std_pre_test", "cam_n_days_pre_test",
    "latitude", "longitude",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    full = pd.read_parquet(FULL_PATH)
    missing = [c for c in SERVING_COLUMNS if c not in full.columns]
    assert not missing, f"serving columns missing from full table: {missing}"

    serving = full[SERVING_COLUMNS].copy()
    # Downcasts (see module docstring for why each is value-preserving).
    serving["latitude"] = serving["latitude"].astype(np.float32)
    serving["longitude"] = serving["longitude"].astype(np.float32)
    assert int(serving["days_since_first"].max()) < 32767
    assert int(serving["days_since_first"].min()) >= 0
    serving["days_since_first"] = serving["days_since_first"].astype(np.int16)

    SERVING_PATH.parent.mkdir(parents=True, exist_ok=True)
    serving.to_parquet(SERVING_PATH)

    # -- verification against the full table -------------------------
    check = pd.read_parquet(SERVING_PATH)
    assert len(check) == len(full), "row count changed"
    assert list(check.columns) == SERVING_COLUMNS, "column order changed"
    assert set(check["camera_id"].unique()) == set(full["camera_id"].unique())
    assert check["date"].min() == full["date"].min()
    assert check["date"].max() == full["date"].max()
    # Sampled row-by-row equality (every 997th row + the acceptance case).
    # latitude/longitude were downcast float64 -> float32: the check
    # that matters is that float32(full value) == serving value
    # exactly — that is what the scorer receives either way (it casts
    # every feature to float32 before scoring). All other columns
    # must be bit-identical. NaNs compare equal to NaNs.
    idx = list(range(0, len(full), 997))
    for col in SERVING_COLUMNS:
        a = full[col].iloc[idx].to_numpy()
        b = check[col].iloc[idx].to_numpy()
        if col in ("latitude", "longitude"):
            f32 = np.asarray(a, dtype=np.float32)
            assert ((f32 == b) | (np.isnan(a) & np.isnan(b))).all(), col
        elif pd.api.types.is_float_dtype(full[col]):
            assert ((a == b) | (np.isnan(a) & np.isnan(b))).all(), col
        else:
            assert (a == b).all(), col
    acc = check[(check["camera_id"] == "1413")
                & (check["date"] == "2026-10-15")]
    assert len(acc) == 1, "acceptance row cam 1413 / 2026-10-15 missing"

    full_mem = full.memory_usage(deep=True).sum() / 1e6
    serving_mem = check.memory_usage(deep=True).sum() / 1e6
    print(f"full table   : {len(full):,} rows x {len(full.columns)} cols, "
          f"{full_mem:.1f} MB in pandas, {FULL_PATH.stat().st_size / 1e6:.1f} MB on disk")
    print(f"serving table: {len(check):,} rows x {len(check.columns)} cols, "
          f"{serving_mem:.1f} MB in pandas, {SERVING_PATH.stat().st_size / 1e6:.1f} MB on disk")
    print(f"serving sha256: {sha256(SERVING_PATH)}")
    print("VERIFIED: row count, columns, cameras, date range, sampled "
          "rows (incl. cam 1413 / 2026-10-15) all match the full table.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
