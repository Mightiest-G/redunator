"""POST /subscriptions/verify — Google Play subscription check
(added 2026-11-18).

The endpoint requires a device JWT (see test_auth.py). Verification
itself is covered at two levels: the HTTP contract with
``_verify_with_google`` mocked, and the Google-response parsing
with the real google client libraries pointed at a fake service.
No live Google credentials exist in this environment, so the real
API call is never made here.
"""

from __future__ import annotations

import httplib2
import pytest

from backend.routers.subscriptions import PlayVerificationError

ACTIVE_SUB = {
    "subscriptionState": "SUBSCRIPTION_STATE_ACTIVE",
    "latestOrderId": "GPA.1234",
    "lineItems": [{
        "productId": "pro_monthly",
        "expiryTime": "2026-12-18T06:44:14Z",
        "autoRenewingPlan": {"autoRenewEnabled": True},
    }],
}


def _body(user_id, **overrides):
    body = {"product_id": "pro_monthly",
            "purchase_token": "purchase-token-123",
            "device_id": user_id}
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# HTTP contract
# ---------------------------------------------------------------------------

def test_verify_requires_jwt(client, user_id):
    r = client.post("/subscriptions/verify", json=_body(user_id))
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


def test_verify_not_configured(client, auth_headers, user_id, monkeypatch):
    monkeypatch.delenv("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON", raising=False)
    r = client.post("/subscriptions/verify", headers=auth_headers,
                    json=_body(user_id))
    assert r.status_code == 200
    assert r.json() == {"valid": None,
                        "reason": "play_verification_not_configured"}


def test_verify_device_mismatch_401(client, auth_headers, user_id):
    r = client.post("/subscriptions/verify", headers=auth_headers,
                    json=_body(user_id,
                               device_id="00000000-0000-4000-8000-"
                                         "000000000002"))
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


def test_verify_missing_fields_400(client, auth_headers, user_id):
    for bad in ({"product_id": "pro_monthly"},
                {"product_id": "pro_monthly",
                 "purchase_token": "tok"},
                {"purchase_token": "tok", "device_id": user_id}):
        r = client.post("/subscriptions/verify", headers=auth_headers,
                        json=bad)
        assert r.status_code == 400, bad
        assert r.json()["error"] == "invalid_parameters"


def _configure(monkeypatch, fake_result=None, fake_error=None):
    """Point the endpoint at a fake Google: set the env var (so the
    not-configured path is skipped) and patch _verify_with_google."""
    import backend.routers.subscriptions as subs

    monkeypatch.setenv("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON",
                       "/tmp/fake-service-account.json")
    if fake_error is not None:
        def _raise(product_id, purchase_token):
            raise fake_error
        monkeypatch.setattr(subs, "_verify_with_google", _raise)
    else:
        monkeypatch.setattr(subs, "_verify_with_google",
                            lambda pid, tok: fake_result)


def test_verify_active_subscription(client, auth_headers, user_id,
                                    monkeypatch):
    _configure(monkeypatch, fake_result={
        "valid": True, "expiry": "2026-12-18T06:44:14Z",
        "auto_renewing": True, "reason": None})
    r = client.post("/subscriptions/verify", headers=auth_headers,
                    json=_body(user_id))
    assert r.status_code == 200
    assert r.json() == {"valid": True, "product_id": "pro_monthly",
                        "expiry": "2026-12-18T06:44:14Z",
                        "auto_renewing": True, "reason": None}


def test_verify_expired_subscription(client, auth_headers, user_id,
                                     monkeypatch):
    _configure(monkeypatch, fake_result={
        "valid": False, "expiry": "2026-11-01T00:00:00Z",
        "auto_renewing": False, "reason": None})
    r = client.post("/subscriptions/verify", headers=auth_headers,
                    json=_body(user_id))
    assert r.status_code == 200
    assert r.json()["valid"] is False
    assert r.json()["expiry"] == "2026-11-01T00:00:00Z"


def test_verify_upstream_failure_502(client, auth_headers, user_id,
                                     monkeypatch):
    _configure(monkeypatch, fake_error=PlayVerificationError(
        "Google Play verification could not run"))
    r = client.post("/subscriptions/verify", headers=auth_headers,
                    json=_body(user_id))
    assert r.status_code == 502
    assert r.json()["error"] == "play_verification_failed"


# ---------------------------------------------------------------------------
# Google-response parsing (real client libs, fake service)
# ---------------------------------------------------------------------------

class _FakeGet:
    def __init__(self, resp=None, exc=None):
        self._resp, self._exc = resp, exc

    def execute(self):
        if self._exc is not None:
            raise self._exc
        return self._resp


class _FakeService:
    """Stands in for the androidpublisher service: records the call
    kwargs and returns the canned subscriptionsv2 response."""

    def __init__(self, resp=None, exc=None):
        self.resp, self.exc = resp, exc
        self.calls = []

    def purchases(self):
        return self

    def subscriptionsv2(self):
        return self

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeGet(self.resp, self.exc)


def _patch_google(monkeypatch, service):
    """Point the lazy google imports at the fake service."""
    import google.oauth2.service_account as sa
    import googleapiclient.discovery as discovery

    monkeypatch.setattr(
        sa.Credentials, "from_service_account_file",
        classmethod(lambda cls, path, scopes=None: object()))
    monkeypatch.setattr(discovery, "build",
                        lambda *args, **kwargs: service)


def test_verify_with_google_parses_active(monkeypatch):
    from backend.routers.subscriptions import _verify_with_google

    service = _FakeService(resp=dict(ACTIVE_SUB))
    _patch_google(monkeypatch, service)
    result = _verify_with_google("pro_monthly", "purchase-token-123")
    assert result == {"valid": True, "expiry": "2026-12-18T06:44:14Z",
                      "auto_renewing": True, "reason": None}
    assert service.calls == [{"packageName": "com.redunator.redunator",
                              "productId": "pro_monthly",
                              "token": "purchase-token-123"}]


def test_verify_with_google_parses_cancelled(monkeypatch):
    from backend.routers.subscriptions import _verify_with_google

    resp = dict(ACTIVE_SUB)
    resp["subscriptionState"] = "SUBSCRIPTION_STATE_CANCELED"
    resp["lineItems"] = [{"productId": "pro_monthly",
                          "expiryTime": "2026-11-20T00:00:00Z"}]
    _patch_google(monkeypatch, _FakeService(resp=resp))
    result = _verify_with_google("pro_monthly", "tok")
    assert result["valid"] is False
    assert result["expiry"] == "2026-11-20T00:00:00Z"
    assert result["auto_renewing"] is False


def test_verify_with_google_grace_period_is_entitled(monkeypatch):
    from backend.routers.subscriptions import _verify_with_google

    resp = dict(ACTIVE_SUB)
    resp["subscriptionState"] = "SUBSCRIPTION_STATE_IN_GRACE_PERIOD"
    _patch_google(monkeypatch, _FakeService(resp=resp))
    assert _verify_with_google("pro_monthly", "tok")["valid"] is True


def test_verify_with_google_unknown_token_is_invalid(monkeypatch):
    from backend.routers.subscriptions import _verify_with_google
    from googleapiclient.errors import HttpError

    resp = httplib2.Response({"status": "404", "reason": "Not Found"})
    err = HttpError(resp, b'{"error": {"message": "not found"}}')
    _patch_google(monkeypatch, _FakeService(exc=err))
    result = _verify_with_google("pro_monthly", "no-such-token")
    assert result == {"valid": False, "expiry": None,
                      "auto_renewing": None,
                      "reason": "purchase_token_invalid"}


def test_verify_with_google_server_error_raises(monkeypatch):
    from backend.routers.subscriptions import _verify_with_google
    from googleapiclient.errors import HttpError

    resp = httplib2.Response({"status": "500",
                              "reason": "Internal Server Error"})
    err = HttpError(resp, b'{"error": {"message": "boom"}}')
    _patch_google(monkeypatch, _FakeService(exc=err))
    with pytest.raises(PlayVerificationError):
        _verify_with_google("pro_monthly", "tok")
