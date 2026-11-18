"""Device JWT auth: POST /auth/device + Bearer protection of the
user endpoints (added 2026-11-18).

The suite secret is the fixed test-only value set in conftest.py
(TEST_JWT_SECRET) — never a real secret.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from backend.auth import TOKEN_TTL_SECONDS
from backend.tests.conftest import TEST_JWT_SECRET


def _mint(device_id, secret=TEST_JWT_SECRET, **claims):
    now = datetime.now(timezone.utc)
    payload = {"sub": device_id, "iss": "redunator", "iat": now,
               "exp": now + timedelta(seconds=TOKEN_TTL_SECONDS)}
    payload.update(claims)
    return jwt.encode(payload, secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# POST /auth/device — issuance
# ---------------------------------------------------------------------------

def test_auth_device_issues_token(client, user_id):
    r = client.post("/auth/device", json={"device_id": user_id})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == TOKEN_TTL_SECONDS == 30 * 24 * 3600
    assert body["access_token"]
    # The token decodes with the expected claims.
    payload = jwt.decode(body["access_token"], TEST_JWT_SECRET,
                         algorithms=["HS256"], issuer="redunator")
    assert payload["sub"] == user_id
    assert payload["iss"] == "redunator"
    assert payload["exp"] - payload["iat"] == TOKEN_TTL_SECONDS


def test_auth_device_normalises_device_id(client):
    raw = str(uuid.uuid4()).upper()  # same device, non-canonical spelling
    r = client.post("/auth/device", json={"device_id": raw})
    assert r.status_code == 200
    payload = jwt.decode(r.json()["access_token"], TEST_JWT_SECRET,
                         algorithms=["HS256"], issuer="redunator")
    assert payload["sub"] == raw.lower()


def test_auth_device_missing_device_id_400(client):
    r = client.post("/auth/device", json={})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_parameters"


def test_auth_device_bad_device_id_400(client):
    r = client.post("/auth/device", json={"device_id": "not-a-uuid"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_parameters"


def test_auth_device_503_when_secret_unset(client, user_id, monkeypatch):
    monkeypatch.delenv("REDUNATOR_JWT_SECRET", raising=False)
    r = client.post("/auth/device", json={"device_id": user_id})
    assert r.status_code == 503
    body = r.json()
    assert body["reason"] == "jwt_secret_not_configured"
    assert "REDUNATOR_JWT_SECRET" in body["message"]


# ---------------------------------------------------------------------------
# Happy path: a minted token works, and its device_id IS the user id
# ---------------------------------------------------------------------------

def test_valid_token_grants_access_and_sets_identity(client, user_id):
    r = client.post("/auth/device", json={"device_id": user_id})
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    r = client.post("/user_report", headers=headers,
                    json={"camera_id": "1413"})
    assert r.status_code == 201
    assert r.json()["user_id"] == user_id
    r = client.get("/user_settings", headers=headers)
    assert r.status_code == 200
    assert r.json()["user_id"] == user_id


# ---------------------------------------------------------------------------
# 401s
# ---------------------------------------------------------------------------

def test_no_token_401(client):
    for path, method in (("/user_report", "post"),
                         ("/user_alerts", "get"),
                         ("/user_feedback", "post"),
                         ("/user_settings", "get")):
        call = getattr(client, method)
        r = call(path, json={}) if method == "post" else call(path)
        assert r.status_code == 401, path
        assert r.json()["error"] == "unauthorized"


def test_bad_token_401(client, user_id):
    bad = _mint(user_id, secret="a-completely-different-secret")
    r = client.get("/user_settings",
                   headers={"Authorization": f"Bearer {bad}"})
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


def test_expired_token_401(client, user_id):
    expired = _mint(user_id, exp=datetime.now(timezone.utc)
                    - timedelta(seconds=1))
    r = client.get("/user_settings",
                   headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401
    assert "expired" in r.json()["message"]


def test_malformed_authorization_401(client):
    for bad_header in ("not-a-token", "Token abc.def.ghi",
                       "Bearer", "Bearer "):
        r = client.get("/user_settings",
                       headers={"Authorization": bad_header})
        assert r.status_code == 401, bad_header


def test_wrong_issuer_401(client, user_id):
    token = _mint(user_id, iss="someone-else")
    r = client.get("/user_settings",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_token_without_sub_401(client):
    now = datetime.now(timezone.utc)
    payload = {"iss": "redunator", "iat": now,
               "exp": now + timedelta(seconds=3600)}
    token = jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")
    r = client.get("/user_settings",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_unset_secret_verifies_nothing(client, user_id, monkeypatch):
    """With the secret unset, even a token minted under the test
    secret must fail closed (401), not pass."""
    token = _mint(user_id)
    monkeypatch.delenv("REDUNATOR_JWT_SECRET", raising=False)
    r = client.get("/user_settings",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Spoofing is dead: X-User-Id no longer grants or steers identity
# ---------------------------------------------------------------------------

def test_spoofed_x_user_id_alone_401(client):
    r = client.post("/user_report", headers={"X-User-Id": "victim"},
                    json={"camera_id": "1413"})
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


def test_x_user_id_cannot_steal_identity(client, user_id):
    """A valid token for device A plus X-User-Id: victim still
    records everything under device A — the header is ignored."""
    r = client.post("/auth/device", json={"device_id": user_id})
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}",
               "X-User-Id": "victim-device"}
    r = client.post("/user_report", headers=headers,
                    json={"camera_id": "1413", "user_id": "victim-device"})
    assert r.status_code == 201
    assert r.json()["user_id"] == user_id
    assert r.json()["user_id"] != "victim-device"


def test_body_user_id_ignored_for_identity(client, user_id):
    r = client.post("/auth/device", json={"device_id": user_id})
    token = r.json()["access_token"]
    r = client.post("/user_feedback",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"text": "hi", "user_id": "someone-else"})
    assert r.status_code == 201
    assert r.json()["user_id"] == user_id
