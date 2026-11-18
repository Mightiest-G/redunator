"""GET /app/latest — latest published APK release (update check)."""

from __future__ import annotations

import json

import pytest

from backend.app import _release_build


def test_release_build_parses_apk_tags():
    assert _release_build("apk-4") == 4
    assert _release_build("apk-12") == 12
    assert _release_build("apk-1") == 1


def test_release_build_rejects_other_tags():
    assert _release_build("v1.0.0") is None
    assert _release_build("apk-") is None
    assert _release_build("apk-x") is None
    assert _release_build("") is None
    assert _release_build(None) is None


def test_app_latest_without_token(client, monkeypatch):
    # No GITHUB_TOKEN in the test env: the endpoint still answers 200
    # with nulls + a reason, so the app simply skips the update check.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    r = client.get("/app/latest")
    assert r.status_code == 200
    body = r.json()
    assert body["tag"] is None
    assert body["build"] is None
    assert body["download_url"] is None
    assert body["reason"] == "github_token_not_configured"


def test_app_latest_exempt_from_rate_limit(client, monkeypatch):
    # Like /health, the update check must stay 200 under repeated hits.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    for _ in range(5):
        assert client.get("/app/latest").status_code == 200


def test_app_latest_with_token_parses_release(client, monkeypatch):
    # Fake the GitHub releases/latest response; the endpoint must pick
    # the app-release.apk asset URL and the build number from the tag.
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")

    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "tag_name": "apk-7",
                "name": "Redunator APK (build 7)",
                "published_at": "2026-11-18T00:00:00Z",
                "html_url": "https://github.com/Mightiest-G/redunator/releases/tag/apk-7",
                "body": "Dark mode + update check.",
                "assets": [
                    {
                        "name": "app-release.apk",
                        "browser_download_url": (
                            "https://github.com/Mightiest-G/redunator"
                            "/releases/download/apk-7/app-release.apk"
                        ),
                    },
                    {"name": "notes.txt",
                     "browser_download_url": "https://example.invalid/x"},
                ],
            }

    def fake_get(url, headers=None, timeout=None):
        assert url == ("https://api.github.com/repos/Mightiest-G/redunator"
                       "/releases/latest")
        assert headers["Authorization"] == "Bearer fake-token-for-tests"
        return FakeResp()

    monkeypatch.setattr("requests.get", fake_get)
    r = client.get("/app/latest")
    assert r.status_code == 200
    body = r.json()
    assert body["tag"] == "apk-7"
    assert body["build"] == 7
    assert body["download_url"] == (
        "https://github.com/Mightiest-G/redunator"
        "/releases/download/apk-7/app-release.apk"
    )
    assert body["name"] == "Redunator APK (build 7)"
    assert body["reason"] is None


def test_app_latest_github_failure_is_soft(client, monkeypatch):
    # GitHub down / bad token: still 200 with nulls, never a 500 —
    # the app must keep working when the check cannot run.
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")

    def failing_get(url, headers=None, timeout=None):
        raise ConnectionError("github unreachable")

    monkeypatch.setattr("requests.get", failing_get)
    r = client.get("/app/latest")
    assert r.status_code == 200
    body = r.json()
    assert body["tag"] is None
    assert body["build"] is None
    assert body["reason"] == "github_lookup_failed"


def test_openapi_lists_app_latest(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert "/app/latest" in r.json()["paths"]
