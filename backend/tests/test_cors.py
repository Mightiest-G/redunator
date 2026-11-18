"""CORS: deny-by-default (added 2026-11-18).

The API serves a native Flutter app only — there is no web client —
so the default allow-list is empty. The REDUNATOR_CORS_ORIGINS env
var still overrides it for explicitly configured origins.

Config is read at import time, so these tests reload
``backend.config`` under a patched env and restore it afterwards.
The restore is explicit (not order-dependent on pytest's
teardown): the old env value is captured up front and re-applied
in the finalizer before reloading.
"""

from __future__ import annotations

import importlib
import os

from fastapi.testclient import TestClient


def _reload_config_under(monkeypatch, request, env_value):
    """Set REDUNATOR_CORS_ORIGINS to env_value (None = unset),
    reload backend.config, and arrange an explicit restore+reload
    at teardown. Returns the reloaded config module."""
    from backend import config

    old = os.environ.get("REDUNATOR_CORS_ORIGINS")
    if env_value is None:
        monkeypatch.delenv("REDUNATOR_CORS_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("REDUNATOR_CORS_ORIGINS", env_value)
    importlib.reload(config)

    def _restore():
        if old is None:
            os.environ.pop("REDUNATOR_CORS_ORIGINS", None)
        else:
            os.environ["REDUNATOR_CORS_ORIGINS"] = old
        importlib.reload(config)

    request.addfinalizer(_restore)
    return config


def test_cors_default_is_empty(monkeypatch, request):
    config = _reload_config_under(monkeypatch, request, None)
    assert config.CORS_ORIGINS == []


def test_cors_env_override_is_honoured(monkeypatch, request):
    config = _reload_config_under(
        monkeypatch, request,
        "https://admin.example.com, http://localhost:9000")
    assert config.CORS_ORIGINS == ["https://admin.example.com",
                                   "http://localhost:9000"]


def test_cors_env_override_empty_string_means_deny(monkeypatch, request):
    config = _reload_config_under(monkeypatch, request, "")
    assert config.CORS_ORIGINS == []


def test_cors_override_allows_listed_origin_on_fresh_app(monkeypatch,
                                                         request):
    """End to end: an origin named in the env var gets the CORS
    header back from a freshly built app; any other origin does
    not."""
    from backend.app import create_app

    _reload_config_under(monkeypatch, request,
                         "https://admin.example.com")
    with TestClient(create_app()) as c:
        r = c.get("/health",
                  headers={"Origin": "https://admin.example.com"})
        assert (r.headers.get("access-control-allow-origin")
                == "https://admin.example.com")
        r = c.get("/health", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in r.headers
