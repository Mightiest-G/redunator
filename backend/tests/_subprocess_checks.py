"""Subprocess checks for the backend test suite (Worker C).

NOT a pytest file (underscore prefix). It is invoked by
``test_pointer_ratelimit.py`` as a fresh process so that
environment variables read at import time
(``REDUNATOR_LIVE_MODEL``, ``REDUNATOR_RATE_LIMIT_PER_MIN``) can
be set before the app is imported — something an in-process test
cannot do, because ``backend.config`` reads them at import.

Usage:
    python backend/tests/_subprocess_checks.py pointer <pointer-path>
    python backend/tests/_subprocess_checks.py ratelimit

Exits 0 on pass, 1 on failure, printing a one-line verdict.
"""

from __future__ import annotations

import json
import sys

PROJECT_ROOT = __file__ and __import__("pathlib").Path(
    __file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def check_pointer(pointer_path: str) -> None:
    from fastapi.testclient import TestClient

    from backend.app import create_app

    with TestClient(create_app()) as client:
        r = client.get("/health")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["model_version"] == 99, body
        assert body["model_files"]["onnx"] == "redunator_brain_v1.onnx", body
        # The copied pointer still serves predictions from the same files.
        r = client.post("/predict_risk", json={
            "camera_id": "1413", "timestamp": "2026-10-15T12:00:00"})
        assert r.status_code == 200, r.text
        assert r.json()["model_version"] == 99, r.json()
        assert abs(r.json()["daily_prediction"] - 53.0095) < 0.002, r.json()
    print(json.dumps({"check": "pointer", "result": "pass",
                      "model_version": 99}))


def check_ratelimit() -> None:
    from fastapi.testclient import TestClient

    from backend.app import create_app

    with TestClient(create_app()) as client:
        statuses = []
        for _ in range(7):
            statuses.append(client.get("/intersections",
                                       params={"limit": 1}).status_code)
        assert statuses[:5] == [200] * 5, statuses
        assert statuses[5:] == [429, 429], statuses
        r = client.get("/intersections", params={"limit": 1})
        assert r.status_code == 429
        assert r.json()["error"] == "rate_limited"
        assert r.headers["x-ratelimit-limit"] == "5"
        assert r.headers["x-ratelimit-remaining"] == "0"
        assert "retry-after" in r.headers
        # /health is exempt: still 200 after the limit is hit.
        assert client.get("/health").status_code == 200
    print(json.dumps({"check": "ratelimit", "result": "pass",
                      "statuses": statuses}))


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "pointer":
        check_pointer(sys.argv[2])
    elif mode == "ratelimit":
        check_ratelimit()
    else:
        raise SystemExit(f"unknown mode {mode!r}")
