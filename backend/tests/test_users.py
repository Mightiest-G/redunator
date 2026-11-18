"""User endpoints: /user_report, /user_alerts, /user_feedback,
/user_settings (Worker C).

Storage is the temp SQLite DB pointed at by REDUNATOR_DB_PATH in
conftest.py — never the real data/redunator.db. Each test uses a
fresh device id + JWT (``auth_headers`` fixture); X-User-Id is gone.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# /user_report
# ---------------------------------------------------------------------------

def test_report_create_and_retrieve(client, auth_headers):
    r = client.post("/user_report", headers=auth_headers, json={
        "camera_id": "1413", "note": "spike at lunch"})
    assert r.status_code == 201
    body = r.json()
    assert body["id"]
    assert body["camera_id"] == "1413"
    assert body["note"] == "spike at lunch"
    assert body["created_at"]

    r2 = client.get(f"/user_report/{body['id']}", headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["id"] == body["id"]
    assert r2.json()["camera_id"] == "1413"


def test_report_with_lat_lon(client, auth_headers):
    r = client.post("/user_report", headers=auth_headers, json={
        "lat": 41.8781, "lon": -87.6298,
        "timestamp": "2026-11-16T05:00:00-06:00"})
    assert r.status_code == 201
    assert r.json()["latitude"] == 41.8781
    assert r.json()["longitude"] == -87.6298


def test_report_missing_location_400(client, auth_headers):
    r = client.post("/user_report", headers=auth_headers, json={"note": "where?"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_parameters"


def test_report_bad_lat_400(client, auth_headers):
    r = client.post("/user_report", headers=auth_headers,
                    json={"lat": 91, "lon": 0})
    assert r.status_code == 400


def test_report_lat_without_lon_400(client, auth_headers):
    r = client.post("/user_report", headers=auth_headers, json={"lat": 41.9})
    assert r.status_code == 400


def test_report_bad_timestamp_400(client, auth_headers):
    r = client.post("/user_report", headers=auth_headers, json={
        "camera_id": "1413", "timestamp": "not-a-time"})
    assert r.status_code == 400


def test_report_unknown_id_404(client, auth_headers):
    r = client.get("/user_report/999999999", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_report_other_device_404(client, auth_headers):
    """A report created by one device is invisible to another: the
    owner check answers 404, not 401 (no existence leak)."""
    from backend.auth import issue_device_token

    other_token, _ = issue_device_token("00000000-0000-4000-8000-"
                                        "000000000001")
    r = client.post("/user_report", headers=auth_headers,
                    json={"camera_id": "1413"})
    assert r.status_code == 201
    rid = r.json()["id"]
    r2 = client.get(f"/user_report/{rid}",
                    headers={"Authorization": f"Bearer {other_token}"})
    assert r2.status_code == 404
    assert r2.json()["error"] == "not_found"


# ---------------------------------------------------------------------------
# Old gates are gone (device JWTs replaced them, 2026-11-18)
# ---------------------------------------------------------------------------

def test_user_endpoints_ignore_api_key_and_header_identity(
        client, auth_headers):
    """The removed X-Api-Key gate and X-User-Id header no longer do
    anything: a valid JWT is sufficient on its own, and no header
    can substitute for one."""
    # JWT alone is enough — no X-Api-Key needed.
    r = client.post("/user_report", headers=auth_headers,
                    json={"camera_id": "1413"})
    assert r.status_code == 201
    # An X-Api-Key (even a plausible one) does NOT grant access
    # without a JWT.
    r = client.post("/user_report",
                    headers={"X-Api-Key": "test-secret"},
                    json={"camera_id": "1413"})
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


def test_report_anonymous_user_when_no_header(client):
    """There is no anonymous access anymore: without a device JWT
    the user endpoints answer 401 (the pre-2026-11-18 test of the
    same name expected 201 with user_id "anonymous")."""
    r = client.post("/user_report", json={"camera_id": "1413"})
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


# ---------------------------------------------------------------------------
# /user_alerts
# ---------------------------------------------------------------------------

def test_alerts_create_list_delete(client, auth_headers):
    r = client.get("/user_alerts", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["alerts"] == []

    r = client.post("/user_alerts", headers=auth_headers, json={
        "lat": 41.8781, "lon": -87.6298, "radius_m": 1500,
        "threshold": "HIGH", "name": "Loop"})
    assert r.status_code == 201
    alert = r.json()
    assert alert["id"]
    assert alert["threshold"] == "HIGH"
    assert alert["radius_m"] == 1500
    assert alert["active"] is True

    r = client.get("/user_alerts", headers=auth_headers)
    assert len(r.json()["alerts"]) == 1

    r = client.delete(f"/user_alerts/{alert['id']}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["active"] is False

    r = client.get("/user_alerts", headers=auth_headers)
    assert r.json()["alerts"] == []
    r = client.get("/user_alerts", headers=auth_headers,
                   params={"active_only": False})
    assert len(r.json()["alerts"]) == 1


def test_alerts_defaults(client, auth_headers):
    r = client.post("/user_alerts", headers=auth_headers, json={
        "lat": 41.8781, "lon": -87.6298})
    assert r.status_code == 201
    assert r.json()["radius_m"] == 2000
    assert r.json()["threshold"] == "MEDIUM"


def test_alerts_missing_location_400(client, auth_headers):
    r = client.post("/user_alerts", headers=auth_headers, json={"radius_m": 500})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_parameters"


def test_alerts_radius_out_of_range_400(client, auth_headers):
    for bad in (50, 60000):
        r = client.post("/user_alerts", headers=auth_headers, json={
            "lat": 41.8781, "lon": -87.6298, "radius_m": bad})
        assert r.status_code == 400


def test_alerts_bad_threshold_400(client, auth_headers):
    r = client.post("/user_alerts", headers=auth_headers, json={
        "lat": 41.8781, "lon": -87.6298, "threshold": "EXTREME"})
    assert r.status_code == 400


def test_alerts_delete_unknown_404(client, auth_headers):
    r = client.delete("/user_alerts/999999999", headers=auth_headers)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /user_feedback
# ---------------------------------------------------------------------------

def test_feedback_create(client, auth_headers):
    r = client.post("/user_feedback", headers=auth_headers, json={
        "text": "Prediction felt right today", "rating": 5,
        "category": "accuracy"})
    assert r.status_code == 201
    body = r.json()
    assert body["text"] == "Prediction felt right today"
    assert body["rating"] == 5
    assert body["category"] == "accuracy"


def test_feedback_message_alias(client, auth_headers):
    r = client.post("/user_feedback", headers=auth_headers, json={
        "message": "via the message alias"})
    assert r.status_code == 201
    assert r.json()["text"] == "via the message alias"


def test_feedback_missing_text_400(client, auth_headers):
    r = client.post("/user_feedback", headers=auth_headers, json={"rating": 3})
    assert r.status_code == 400


def test_feedback_rating_out_of_range_400(client, auth_headers):
    for bad in (0, 6, "high"):
        r = client.post("/user_feedback", headers=auth_headers, json={
            "text": "x", "rating": bad})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# /user_settings
# ---------------------------------------------------------------------------

def test_settings_defaults_then_set_then_merge(client, auth_headers):
    r = client.get("/user_settings", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["settings"] == {
        "units": "metric", "alert_radius_m": 2000,
        "risk_threshold": "MEDIUM",
        "notifications": {"email": False, "push": False}}

    r = client.post("/user_settings", headers=auth_headers, json={
        "units": "imperial", "risk_threshold": "high"})
    assert r.status_code == 200
    assert r.json()["settings"]["units"] == "imperial"
    assert r.json()["settings"]["risk_threshold"] == "HIGH"
    # Untouched keys survive (merge, not replace).
    assert r.json()["settings"]["alert_radius_m"] == 2000

    r = client.post("/user_settings", headers=auth_headers, json={
        "settings": {"alert_radius_m": 500}})
    assert r.status_code == 200
    assert r.json()["settings"]["alert_radius_m"] == 500
    assert r.json()["settings"]["units"] == "imperial"

    r = client.get("/user_settings", headers=auth_headers)
    assert r.json()["settings"]["units"] == "imperial"
    assert r.json()["settings"]["alert_radius_m"] == 500


def test_settings_bad_units_400(client, auth_headers):
    r = client.post("/user_settings", headers=auth_headers,
                    json={"units": "furlongs"})
    assert r.status_code == 400


def test_settings_bad_threshold_400(client, auth_headers):
    r = client.post("/user_settings", headers=auth_headers,
                    json={"risk_threshold": "EXTREME"})
    assert r.status_code == 400
