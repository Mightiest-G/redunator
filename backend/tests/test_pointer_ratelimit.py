"""Model-pointer + rate-limit tests (Worker C).

Both behaviours depend on environment variables that
``backend.config`` reads at import time, so they are exercised in
fresh subprocesses (see ``_subprocess_checks.py``) with the env
var set before import — the real ``live_model.json`` pointer is
never touched.

Also included: an in-process pointer test using a fresh
``ModelService`` instance pointed at a COPY of the pointer file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHECKS = PROJECT_ROOT / "backend" / "tests" / "_subprocess_checks.py"


def _run(mode: str, env_extra: dict, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("REDUNATOR_DB_PATH", None)  # subprocess uses the real default path
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(CHECKS), mode, *args],
        cwd=str(PROJECT_ROOT), env=env,
        capture_output=True, text=True, timeout=300)


def test_pointer_copy_reports_new_version(tmp_path):
    """Point REDUNATOR_LIVE_MODEL at a COPY of live_model.json with
    live_version 99: the backend must report 99 and still serve the
    same model files. The real pointer is untouched (asserted)."""
    real = json.loads((PROJECT_ROOT / "live_model.json").read_text())
    assert real["live_version"] == 1
    copy = dict(real)
    copy["live_version"] = 99
    copy_path = tmp_path / "live_model_copy.json"
    copy_path.write_text(json.dumps(copy))

    proc = _run("pointer", {"REDUNATOR_LIVE_MODEL": str(copy_path)},
                str(copy_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert '"result": "pass"' in proc.stdout

    # The real pointer is byte-identical afterwards.
    after = json.loads((PROJECT_ROOT / "live_model.json").read_text())
    assert after["live_version"] == 1
    assert after == real


def test_pointer_fresh_model_service(tmp_path):
    """Same check in-process, via a fresh ModelService instance
    (no app import involved): version 99, same daily prediction."""
    from backend.model_service import ModelService

    real = json.loads((PROJECT_ROOT / "live_model.json").read_text())
    copy = dict(real)
    copy["live_version"] = 99
    copy_path = tmp_path / "live_model_service.json"
    copy_path.write_text(json.dumps(copy))

    svc = ModelService(live_model_path=copy_path)
    assert svc.live_version == 99
    assert svc.pointer()["onnx"] == "redunator_brain_v1.onnx"
    daily, source = svc.daily_prediction("1413", "2026-10-15")
    assert source == "model"
    assert abs(daily - 53.0095) < 0.002


def test_rate_limit_env_var_429_and_health_exempt():
    """REDUNATOR_RATE_LIMIT_PER_MIN=5 in a fresh process: the first
    5 requests are 200, the 6th+ are 429 rate_limited, and /health
    stays 200 (exempt)."""
    proc = _run("ratelimit", {"REDUNATOR_RATE_LIMIT_PER_MIN": "5"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert '"result": "pass"' in proc.stdout
