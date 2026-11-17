"""Redunator Phase 3 backend — test-suite fixtures (Worker C).

Run from the project root:

    ~/workspace/redunator-venv/bin/python -m pytest backend/tests/ -q

Notes
-----
* Tests use FastAPI's TestClient against the real app (``create_app``)
  and the real served models — no mocks except the live-signal fetch
  in ``test_live.py`` (the Open-Meteo / Divvy providers are network
  calls; the mocked signals reproduce the LIVE_CONTRACT worked
  example exactly, dry weather, so the deterministic fields hold
  regardless of network or time of day).
* The user endpoints write to SQLite. ``REDUNATOR_DB_PATH`` is pointed
  at a throwaway file under the session tmp dir BEFORE the app is
  imported, so tests never touch the real ``data/redunator.db``.
  Each test uses a fresh ``X-User-Id`` so tests are independent.
* The in-process rate limiter is raised for the main suite (the
  suite makes >100 requests in total across tests; the 100/min
  default would 429 the later tests). The real limit is exercised
  for real in ``test_pointer_ratelimit.py`` via a subprocess that
  sets ``REDUNATOR_RATE_LIMIT_PER_MIN=5`` before import.
* The real ``live_model.json`` pointer is NEVER touched by any test.
  The pointer test points a COPY at a different version, in a
  subprocess and via a fresh ``ModelService`` instance.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402

# -- temp DB, set BEFORE the app (and storage) is imported -------------------
_TMP_DB = Path(os.environ.get(
    "REDUNATOR_TEST_DB",
    str(Path("/tmp/redunator-tests") / f"test-{os.getpid()}.db")))
_TMP_DB.parent.mkdir(parents=True, exist_ok=True)
os.environ["REDUNATOR_DB_PATH"] = str(_TMP_DB)

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import create_app  # noqa: E402
from backend.rate_limit import limiter  # noqa: E402

# Raise the in-process limit for the suite (see module docstring).
limiter.per_min = 10_000
limiter.reset()


@pytest.fixture(scope="session")
def client():
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def user_id():
    """A fresh user id per test, so user-endpoint tests are independent."""
    return "test-user-" + uuid.uuid4().hex[:12]


@pytest.fixture()
def headers(user_id):
    return {"X-User-Id": user_id}
