"""Prediction endpoints: /predict_risk, /predict_risk/batch,
/intersections, /violation_trends (Worker C).

Grounded numbers (do NOT re-derive; verify against them):
- cam 1413 / 2026-10-15 — offline joblib: daily 53.0096, peak
  15:00 / 4.8194, 21 HIGH hours, method "model". Live API (ONNX):
  daily 53.0095, hourly_sum 53.0095, reconciliation_error 0.0.
  The 1e-04 gap is ONNX float32 rounding; hourly values are
  rescaled to the ONNX daily.
"""

from __future__ import annotations

import pytest

CAM_1413 = "1413"
DAY_1413 = "2026-10-15T12:00:00"

# Segment-map coordinates of cam 1413 (Cicero & I-55 area).
LAT_1413, LON_1413 = 41.940241, -87.639639


# ---------------------------------------------------------------------------
# /predict_risk — happy paths
# ---------------------------------------------------------------------------

def test_predict_by_camera_id(client):
    r = client.post("/predict_risk",
                    json={"camera_id": CAM_1413, "timestamp": DAY_1413})
    assert r.status_code == 200
    body = r.json()
    assert body["camera_id"] == CAM_1413
    assert body["date"] == "2026-10-15"
    assert body["daily_source"] == "model"
    assert body["risk_level"] == "HIGH"
    assert body["risk_score"] == pytest.approx(1.0)
    assert body["model_version"] == 1
    assert len(body["hourly"]) == 24
    assert all(h["estimate"] is True for h in body["hourly"])
    assert body["hourly_estimate_note"].startswith("Hourly values are ESTIMATES")
    assert body["hourly_method"] == "model"
    assert body["weather_override_applied"] is False
    assert body["congestion_override_applied"] is False


def test_reconciliation_cam_1413_2026_10_15(client):
    """MANDATORY reconciliation test.

    Backend /predict_risk for cam 1413 / 2026-10-15 must return the
    exact offline numbers (within float32 rounding): daily ~53.01,
    peak 15:00 / ~4.82, 24 hours summing to the daily total.
    """
    r = client.post("/predict_risk",
                    json={"camera_id": CAM_1413, "timestamp": DAY_1413})
    assert r.status_code == 200
    body = r.json()
    # Offline joblib daily 53.0096; live ONNX daily 53.0095 (1e-04 gap).
    assert body["daily_prediction"] == pytest.approx(53.0095, abs=0.002)
    assert body["daily_prediction"] == pytest.approx(53.0096, abs=0.002)
    assert body["hourly_sum"] == pytest.approx(
        body["daily_prediction"], abs=0.01)
    assert body["reconciliation_error"] == pytest.approx(0.0, abs=1e-4)
    assert sum(h["predicted_violations"] for h in body["hourly"]) == \
        pytest.approx(body["daily_prediction"], abs=0.01)
    peak = max(body["hourly"], key=lambda h: h["predicted_violations"])
    assert peak["hour"] == 15
    assert peak["predicted_violations"] == pytest.approx(4.8194, abs=0.002)
    assert peak["band"] == "HIGH"
    assert sum(1 for h in body["hourly"] if h["band"] == "HIGH") == 21
    assert sum(1 for h in body["hourly"] if h["band"] == "MEDIUM") == 3
    assert sum(1 for h in body["hourly"] if h["band"] == "LOW") == 0


def test_predict_by_lat_lon(client):
    r = client.post("/predict_risk",
                    json={"latitude": LAT_1413, "longitude": LON_1413,
                          "timestamp": DAY_1413})
    assert r.status_code == 200
    body = r.json()
    assert body["camera_id"] == CAM_1413
    assert body["nearest_distance_m"] == pytest.approx(0.0, abs=1.0)
    assert body["daily_prediction"] == pytest.approx(53.0095, abs=0.002)


def test_predict_camera_id_wins_over_lat_lon(client):
    # camera_id wins; lat/lon are ignored (documented, not an error) —
    # even coordinates with no camera anywhere near them.
    r = client.post("/predict_risk",
                    json={"camera_id": CAM_1413, "latitude": 0.0,
                          "longitude": 0.0, "timestamp": DAY_1413})
    assert r.status_code == 200
    assert r.json()["camera_id"] == CAM_1413


def test_predict_unknown_camera_falls_back_to_citywide_mean(client):
    # Unknown camera_id is NOT a 404: same contract as
    # hourly_risk.daily_prediction — citywide mean, source reported.
    r = client.post("/predict_risk",
                    json={"camera_id": "999999", "timestamp": DAY_1413})
    assert r.status_code == 200
    body = r.json()
    assert body["daily_source"] == "citywide_mean"
    assert body["daily_prediction"] == pytest.approx(5.34, abs=0.01)
    assert body["risk_level"] == "MEDIUM"
    assert body["confidence"] == pytest.approx(0.40)


def test_predict_future_date_falls_back_to_pre_test_mean(client):
    # After the data window (ends 2026-10-29) there is no feature row.
    r = client.post("/predict_risk",
                    json={"camera_id": CAM_1413,
                          "timestamp": "2026-11-16T12:00:00"})
    assert r.status_code == 200
    body = r.json()
    assert body["daily_source"] == "pre_test_mean"
    assert body["confidence"] == pytest.approx(0.60)


def test_predict_overrides_accepted_but_ignored(client):
    r = client.post("/predict_risk",
                    json={"camera_id": CAM_1413, "timestamp": DAY_1413,
                          "weather_override": {"precipitation_mm": 5.0},
                          "congestion_override": {"avg_speed_mph": 10}})
    assert r.status_code == 200
    body = r.json()
    assert body["weather_override_applied"] is False
    assert body["congestion_override_applied"] is False
    # Same daily as without overrides: the overrides change nothing.
    assert body["daily_prediction"] == pytest.approx(53.0095, abs=0.002)


def test_predict_banding_daily_changes_labels_only(client):
    r = client.post("/predict_risk",
                    json={"camera_id": CAM_1413, "timestamp": DAY_1413,
                          "banding": "daily"})
    assert r.status_code == 200
    body = r.json()
    assert body["daily_prediction"] == pytest.approx(53.0095, abs=0.002)
    assert all(h["band"] in ("LOW", "MEDIUM") for h in body["hourly"])


# ---------------------------------------------------------------------------
# /predict_risk — error cases
# ---------------------------------------------------------------------------

def test_predict_empty_body_400(client):
    r = client.post("/predict_risk", json={})
    assert r.status_code == 400
    assert r.json()["error"] == "bad_request"


def test_predict_far_lat_lon_404(client):
    r = client.post("/predict_risk",
                    json={"latitude": 0.0, "longitude": 0.0,
                          "timestamp": DAY_1413})
    assert r.status_code == 404
    assert r.json()["error"] == "no_camera_nearby"


def test_predict_bad_timestamp_422(client):
    r = client.post("/predict_risk",
                    json={"camera_id": CAM_1413, "timestamp": "not-a-date"})
    assert r.status_code == 422
    assert r.json()["error"] == "validation_error"


def test_predict_lat_out_of_range_422(client):
    r = client.post("/predict_risk",
                    json={"latitude": 91.0, "longitude": 0.0})
    assert r.status_code == 422
    assert r.json()["error"] == "validation_error"


def test_predict_lon_out_of_range_422(client):
    r = client.post("/predict_risk",
                    json={"latitude": 41.9, "longitude": 181.0})
    assert r.status_code == 422


def test_predict_lat_without_lon_400(client):
    r = client.post("/predict_risk", json={"latitude": 41.9})
    assert r.status_code == 400
    assert r.json()["error"] == "bad_request"


# ---------------------------------------------------------------------------
# /predict_risk/batch
# ---------------------------------------------------------------------------

def test_batch_happy_path(client):
    r = client.post("/predict_risk/batch", json={"items": [
        {"camera_id": CAM_1413, "timestamp": DAY_1413},
        {"camera_id": "3041", "timestamp": DAY_1413},
    ]})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert body["errors"] == 0
    assert body["results"][0]["camera_id"] == CAM_1413
    assert body["results"][1]["camera_id"] == "3041"


def test_batch_one_bad_item_does_not_fail_batch(client):
    r = client.post("/predict_risk/batch", json={"items": [
        {"camera_id": CAM_1413, "timestamp": DAY_1413},
        {},
        {"latitude": 0.0, "longitude": 0.0},
    ]})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3
    assert body["errors"] == 2
    assert body["results"][0]["camera_id"] == CAM_1413
    assert body["results"][1]["error"] == "bad_request"
    assert body["results"][1]["index"] == 1
    assert body["results"][2]["error"] == "no_camera_nearby"


def test_batch_empty_items_422(client):
    r = client.post("/predict_risk/batch", json={"items": []})
    assert r.status_code == 422
    assert r.json()["error"] == "validation_error"


def test_batch_over_100_items_422(client):
    r = client.post("/predict_risk/batch", json={"items": [
        {"camera_id": CAM_1413}] * 101})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# /intersections
# ---------------------------------------------------------------------------

def test_intersections_default(client):
    r = client.get("/intersections")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 300
    assert body[0]["camera_id"]
    assert body[0]["intersection"]
    assert body[0]["typical_weekday_band"] in ("LOW", "MEDIUM", "HIGH")


def test_intersections_query_filter(client):
    r = client.get("/intersections", params={"q": "BELMONT"})
    assert r.status_code == 200
    assert len(r.json()) >= 1
    assert all("BELMONT" in row["intersection"]
               for row in r.json())


def test_intersections_band_filter_and_bad_band(client):
    r = client.get("/intersections", params={"band": "HIGH", "limit": 5})
    assert r.status_code == 200
    assert len(r.json()) == 5
    assert all(row["typical_weekday_band"] == "HIGH" for row in r.json())
    r = client.get("/intersections", params={"band": "NOPE"})
    assert r.status_code == 400
    assert r.json()["error"] == "bad_request"


def test_intersections_pagination_and_min_pred(client):
    r = client.get("/intersections",
                   params={"min_typical_weekday_pred": 20, "limit": 3,
                           "offset": 0})
    assert r.status_code == 200
    assert len(r.json()) == 3
    assert all(row["typical_weekday_pred"] >= 20 for row in r.json())
    r2 = client.get("/intersections", params={"limit": 2, "offset": 2})
    assert len(r2.json()) == 2


def test_intersections_limit_validation_422(client):
    r = client.get("/intersections", params={"limit": 0})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# /violation_trends
# ---------------------------------------------------------------------------

def test_trends_default_90_days(client):
    r = client.get("/violation_trends", params={"camera_id": CAM_1413})
    assert r.status_code == 200
    body = r.json()
    assert body["camera_id"] == CAM_1413
    assert body["granularity"] == "daily"
    assert body["start_date"] == "2026-08-01"
    assert body["end_date"] == "2026-10-29"
    assert body["summary"]["n_days"] == 90
    assert len(body["days"]) == 90
    assert body["days"][0]["date"] == "2026-08-01"
    assert body["days"][-1]["date"] == "2026-10-29"
    assert all("observed_violations" in d and "predicted_violations" in d
               for d in body["days"])
    assert body["summary"]["total_observed"] > 0


def test_trends_explicit_range(client):
    r = client.get("/violation_trends", params={
        "camera_id": CAM_1413,
        "start_date": "2026-10-13", "end_date": "2026-10-15"})
    assert r.status_code == 200
    assert r.json()["summary"]["n_days"] == 3


def test_trends_by_intersection(client):
    # NORTHWEST HIGHWAY AND FOSTER is unique in camera_profiles.csv
    # (cam 3002). Note MICHIGAN AND JACKSON is NOT unique — it matches
    # both 3041 and 3043, so it 400s (see the ambiguous test below).
    r = client.get("/violation_trends",
                   params={"intersection": "NORTHWEST HIGHWAY AND FOSTER"})
    assert r.status_code == 200
    assert r.json()["camera_id"] == "3002"


def test_trends_missing_params_400(client):
    r = client.get("/violation_trends")
    assert r.status_code == 400
    assert r.json()["error"] == "bad_request"


def test_trends_unknown_camera_404(client):
    r = client.get("/violation_trends", params={"camera_id": "999999"})
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_trends_ambiguous_intersection_400(client):
    # "111TH AND HALSTED" is served by two cameras (2422 and 2424).
    r = client.get("/violation_trends",
                   params={"intersection": "111TH AND HALSTED"})
    assert r.status_code == 400
    assert r.json()["error"] == "ambiguous_intersection"


def test_trends_reversed_range_400(client):
    r = client.get("/violation_trends", params={
        "camera_id": CAM_1413,
        "start_date": "2026-10-15", "end_date": "2026-10-13"})
    assert r.status_code == 400


def test_trends_range_too_long_400(client):
    r = client.get("/violation_trends", params={
        "camera_id": CAM_1413,
        "start_date": "2025-01-01", "end_date": "2026-10-29"})
    assert r.status_code == 400
