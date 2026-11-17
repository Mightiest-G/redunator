"""Redunator Phase 3 — model service (Worker A).

Serves the DAILY model through ONNX Runtime. The model served is the
one `live_model.json` names: filenames are resolved through that
pointer, never hardcoded. The pointer file is re-read on every call
(its mtime is checked); if it changed, the ONNX session is reloaded,
so a monthly retrain promotion takes effect without a restart and
without ever caching a version forever.

Feature vectors are built from the serving table
(`data/processed/red_light_features_serving.parquet` — see
`build_serving_parquet.py`) exactly as `hourly_risk.daily_prediction`
does, reusing `train.FEATURES`
and train.py's fill rules (lag_1d / roll_mean_7d / roll_mean_30d NaN -> 0.0;
lat/lon NaN -> train medians; cam_code from the sorted train camera list,
median code for an unknown camera). When there is no feature row for the
camera-day, the service falls back to the camera's pre-test mean, or the
citywide mean (~5.34/day) for an unknown camera — exactly as
`daily_prediction` does — and reports the source so callers can label it.

ONNX vs joblib parity is demonstrated by `backend/verify_onnx.py`
(measured max abs diff must be <= 2e-05, the documented threshold —
REPORT.md's 2.4e-06 was measured in the original export environment
and is NOT reproduced exactly here; other 2,000-row slices measure up
to ~1.7e-05, float32 rounding noise, not a model difference).
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import numpy as np
import pandas as pd

from . import config

PROJECT_ROOT = config.PROJECT_ROOT
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from train import FEATURES  # noqa: E402  (reuse the shipped feature list)

CITYWIDE_DAILY_MEAN = 5.34  # Phase 2 train target mean (REPORT.md §1)
TRAIN_END = "2026-07-31"


class ModelService:
    """ONNX-backed daily prediction service with live-pointer reload."""

    def __init__(self, live_model_path: Path | str | None = None):
        self.live_model_path = Path(live_model_path or config.LIVE_MODEL_PATH)
        self._lock = threading.Lock()
        self._pointer_mtime: float | None = None
        self._pointer: dict | None = None
        self._session = None          # onnxruntime InferenceSession
        self._input_name: str | None = None
        self._features: pd.DataFrame | None = None
        self._code_map: dict | None = None
        self._med_lat: float | None = None
        self._med_lon: float | None = None

    # -- pointer / session ----------------------------------------------------
    def pointer(self) -> dict:
        """Read live_model.json, reloading the ONNX session if it changed.

        Called on every prediction so a retrain promotion (pointer moved
        to versioned vN files) is picked up without a restart.
        """
        mtime = self.live_model_path.stat().st_mtime
        if self._pointer is None or mtime != self._pointer_mtime:
            with self._lock:
                mtime = self.live_model_path.stat().st_mtime
                if self._pointer is None or mtime != self._pointer_mtime:
                    self._pointer = json.loads(
                        self.live_model_path.read_text())
                    self._pointer_mtime = mtime
                    self._load_session_locked()
        return self._pointer

    def _load_session_locked(self) -> None:
        import onnxruntime as ort

        ptr = self._pointer
        onnx_path = PROJECT_ROOT / ptr["onnx"]
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        opts.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(onnx_path), opts, providers=["CPUExecutionProvider"])
        self._input_name = self._session.get_inputs()[0].name

    @property
    def live_version(self) -> int | None:
        try:
            return self.pointer().get("live_version")
        except Exception:
            return None

    @property
    def metrics(self) -> dict:
        try:
            return dict(self.pointer().get("metrics", {}))
        except Exception:
            return {}

    # -- feature table --------------------------------------------------------
    def _load_features(self) -> pd.DataFrame:
        if self._features is None:
            with self._lock:
                if self._features is None:
                    df = pd.read_parquet(config.FEATURES_PATH)
                    df["date"] = pd.to_datetime(df["date"])
                    train_mask = df["date"] <= TRAIN_END
                    train_cams = sorted(
                        df.loc[train_mask, "camera_id"].unique())
                    self._code_map = {c: i for i, c in enumerate(train_cams)}
                    self._med_lat = float(
                        df.loc[train_mask, "latitude"].median())
                    self._med_lon = float(
                        df.loc[train_mask, "longitude"].median())
                    self._features = df
        return self._features

    @property
    def features(self) -> pd.DataFrame:
        return self._load_features()

    @property
    def latest_violation_date(self) -> str | None:
        try:
            return str(self._load_features()["date"].max().date())
        except Exception:
            return None

    def feature_row(self, camera_id: str, date) -> pd.Series | None:
        feats = self._load_features()
        row = feats[(feats["camera_id"] == str(camera_id))
                    & (feats["date"] == pd.Timestamp(date))]
        return row.iloc[0] if len(row) else None

    def build_vector(self, row: pd.Series, camera_id: str) -> np.ndarray:
        """Feature vector for one parquet row — train.py / hourly_risk rules."""
        x = {}
        for f in FEATURES:
            if f == "cam_code":
                continue  # not a parquet column; train.py creates it
            x[f] = row[f]
        for col in ("lag_1d", "roll_mean_7d", "roll_mean_30d"):
            if pd.isna(x[col]):
                x[col] = 0.0
        if pd.isna(x["latitude"]):
            x["latitude"] = self._med_lat
        if pd.isna(x["longitude"]):
            x["longitude"] = self._med_lon
        code_map = self._code_map
        x["cam_code"] = code_map.get(
            str(camera_id), int(np.median(list(code_map.values()))))
        return np.array([[float(x[f]) for f in FEATURES]], dtype=np.float32)

    def predict_onnx(self, vec: np.ndarray) -> float:
        self.pointer()  # ensure session matches the live pointer
        out = self._session.run(None, {self._input_name: vec})
        return float(np.clip(out[0][0][0], 0, None))

    def predict_rows(self, vecs: np.ndarray) -> np.ndarray:
        """Batch ONNX predictions (used by /violation_trends)."""
        self.pointer()
        out = self._session.run(None, {self._input_name: vecs})
        return np.clip(np.asarray(out[0]).reshape(-1), 0, None)

    # -- public API ------------------------------------------------------------
    def daily_prediction(self, camera_id: str, date) -> tuple[float, str]:
        """(prediction, source) with source in model|pre_test_mean|citywide_mean."""
        cam = str(camera_id)
        date = pd.Timestamp(date)
        row = self.feature_row(cam, date)
        if row is None:
            feats = self._load_features()
            pre = feats[feats["camera_id"] == cam]["cam_mean_pre_test"]
            if len(pre) and pd.notna(pre.iloc[0]):
                return float(pre.iloc[0]), "pre_test_mean"
            return CITYWIDE_DAILY_MEAN, "citywide_mean"
        vec = self.build_vector(row, cam)
        return self.predict_onnx(vec), "model"

    def reasons_for(self, camera_id: str, date, daily_pred: float,
                    source: str) -> list[str]:
        """Human-readable top contributing factors (from the feature row)."""
        cam = str(camera_id)
        row = self.feature_row(cam, date)
        reasons: list[str] = []
        if row is None:
            if source == "pre_test_mean":
                reasons.append(
                    "No daily record for this camera-day, so the prediction "
                    "falls back to this camera's pre-test historical daily mean.")
            else:
                reasons.append(
                    "Unknown camera with no history, so the prediction falls "
                    "back to the citywide mean of about 5.3 violations/day.")
            return reasons
        pre_mean = float(row["cam_mean_pre_test"]) if pd.notna(
            row["cam_mean_pre_test"]) else None
        if pre_mean is not None:
            reasons.append(
                f"This camera averaged {pre_mean:.1f} violations/day before "
                f"the test window (baseline level).")
        lag = row["lag_1d"]
        if pd.notna(lag):
            if pre_mean is not None and float(lag) > pre_mean * 1.2:
                reasons.append(
                    f"Yesterday was busy here ({float(lag):.0f} violations, "
                    f"above the camera's usual {pre_mean:.1f}/day).")
            elif pre_mean is not None and float(lag) < pre_mean * 0.8:
                reasons.append(
                    f"Yesterday was quiet here ({float(lag):.0f} violations, "
                    f"below the camera's usual {pre_mean:.1f}/day).")
            else:
                reasons.append(
                    f"Yesterday saw {float(lag):.0f} violations at this camera.")
        roll7 = row["roll_mean_7d"]
        if pd.notna(roll7):
            reasons.append(
                f"7-day rolling average is {float(roll7):.1f} violations/day.")
        if bool(row["is_weekend"]):
            reasons.append("Weekend day — this camera's weekend pattern applies.")
        if bool(row["is_holiday"]):
            reasons.append("Public holiday — holiday pattern applies.")
        return reasons[:4]


# Process-wide singleton; the pointer reload inside keeps it current.
service = ModelService()
