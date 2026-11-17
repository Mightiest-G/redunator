"""Redunator Phase 3 backend — configuration (Worker A: core app).

All settings come from environment variables with safe defaults so a
local run needs no Postgres/Redis and no keys.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = Path(__file__).resolve().parent

# --- Model pointer -----------------------------------------------------------
# The backend NEVER hardcodes a model version: it reads live_model.json
# (project root) and resolves the filenames it names. A monthly retrain
# promotes by writing versioned redunator_brain_vN.* files and moving
# this pointer; the service notices the pointer file changing (mtime)
# and reloads. For tests, point REDUNATOR_LIVE_MODEL at a COPY.
LIVE_MODEL_PATH = Path(os.environ.get(
    "REDUNATOR_LIVE_MODEL", str(PROJECT_ROOT / "live_model.json")))

# --- Data files --------------------------------------------------------------
FEATURES_PATH = PROJECT_ROOT / "data" / "processed" / "red_light_features.parquet"
PROFILES_PATH = PROJECT_ROOT / "data" / "processed" / "camera_profiles.csv"
SEGMENT_MAP_PATH = (PROJECT_ROOT / "data" / "hourly" / "processed"
                    / "camera_segment_map.csv")
RISK_THRESHOLDS_PATH = PROJECT_ROOT / "data" / "processed" / "risk_thresholds.json"
CRASHES_PATH = (PROJECT_ROOT / "data" / "hourly" / "raw"
                / "crashes_20250101_20261029.csv")

# --- Risk bands / scores (Phase 2, REPORT.md §2; hourly bands Phase 1 item 2)
DAILY_BAND_LOW_MAX = 4.0    # LOW < 4, MEDIUM 4-<10, HIGH >= 10 /day
DAILY_BAND_HIGH_MIN = 10.0
DAILY_SCORE_SCALE = 35.0    # risk_score = clip(daily_prediction / 35, 0, 1)
HOURLY_BAND_LOW_MAX = 0.1   # LOW < 0.1, MEDIUM 0.1-<0.5, HIGH >= 0.5 /hour
HOURLY_BAND_HIGH_MIN = 0.5

# --- Nearest-camera lookup ---------------------------------------------------
# Max distance (metres) for a lat/lon-only request to resolve to a camera.
# Beyond this the endpoint returns 404 (documented behaviour).
MAX_NEAREST_M = float(os.environ.get("REDUNATOR_MAX_NEAREST_M", "2000"))

# --- Rate limiting (SDD: 100 req/min per client, in-memory) ------------------
# /health, /docs, /openapi.json and /redoc are EXEMPT.
RATE_LIMIT_PER_MIN = int(os.environ.get("REDUNATOR_RATE_LIMIT_PER_MIN", "100"))

# --- CORS (Flutter app) ------------------------------------------------------
# Comma-separated list of allowed origins. Defaults cover Flutter web/dev
# and local dev servers; set REDUNATOR_CORS_ORIGINS in production.
_DEFAULT_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://localhost",
    "http://localhost:5000",
    "capacitor://localhost",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "REDUNATOR_CORS_ORIGINS", ",".join(_DEFAULT_ORIGINS)).split(",") if o.strip()]

# --- Misc --------------------------------------------------------------------
HOST = os.environ.get("REDUNATOR_HOST", "127.0.0.1")
PORT = int(os.environ.get("REDUNATOR_PORT", "8000"))
# Confidence is a heuristic, NOT a calibrated probability (see predict.py).
CONFIDENCE_MODEL = 0.90       # daily prediction scored from a feature row
CONFIDENCE_PRE_TEST_MEAN = 0.60
CONFIDENCE_CITYWIDE_MEAN = 0.40
MAX_BATCH_ITEMS = 100
MAX_TREND_DAYS = 366
