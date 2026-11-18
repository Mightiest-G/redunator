"""Health, OpenAPI/docs, and CORS (Worker C)."""

from __future__ import annotations


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["model_version"] == 1
    assert body["model_files"]["joblib"] == "redunator_brain_v1.joblib"
    assert body["model_files"]["onnx"] == "redunator_brain_v1.onnx"
    assert body["model_files"]["json"] == "redunator_brain_v1.json"
    # Data freshness: the violation feature table ends 2026-10-29.
    assert body["data_freshness"]["latest_violation_date"] == "2026-10-29"
    assert body["data_freshness"]["latest_violation_source"] == (
        "data/processed/red_light_features_serving.parquet")


def test_health_exempt_from_rate_limit(client):
    # /health is exempt by design (app.RATE_LIMIT_EXEMPT); it must
    # stay 200 even after many hits in a row.
    for _ in range(5):
        assert client.get("/health").status_code == 200


def test_openapi_lists_all_routes(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    expected = {
        "/health",
        "/app/latest",
        "/auth/device",
        "/predict_risk",
        "/predict_risk/batch",
        "/intersections",
        "/violation_trends",
        "/live/risk",
        "/user_report",
        "/user_report/{report_id}",
        "/user_alerts",
        "/user_alerts/{alert_id}",
        "/user_feedback",
        "/user_settings",
        "/subscriptions/verify",
    }
    assert expected <= set(paths), f"missing routes: {expected - set(paths)}"


def test_docs_loads(client):
    r = client.get("/docs")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_cors_preflight(client):
    """CORS default is deny (2026-11-18): the API serves a native
    app only, so a browser preflight from any origin is refused —
    no access-control-allow-origin header comes back."""
    r = client.options(
        "/predict_risk",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert "access-control-allow-origin" not in r.headers


def test_cors_response_header(client):
    """A plain request still succeeds, but a browser Origin gets no
    CORS allowance back under the deny-by-default config."""
    r = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers


def test_unknown_route_is_404_json(client):
    r = client.get("/no_such_route")
    assert r.status_code == 404
    assert r.json()["error"] == "http_error"
