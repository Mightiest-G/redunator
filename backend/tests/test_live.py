"""GET /live/risk (LIVE_CONTRACT.md) (Worker C).

The live-signal fetch (Open-Meteo / Divvy) is a network call, so the
deterministic tests patch ``backend.routers.live.collect_live_signals``
with fake signals that reproduce the contract's worked example
exactly: dry weather (0.0 mm precipitation, wind 6.9 km/h), fresh at
fetch time. The deterministic response fields then hold regardless
of network or time of day. The error-case tests need no patch — the
endpoint validates before any fetch. A real-network run is done
separately in the manual verification (curl against uvicorn).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from live_signals import LiveSignal  # noqa: E402  (project-root module)

AT_WORKED = "2026-11-16T05:28:06-06:00"
LAT_LOOP, LON_LOOP = 41.8781, -87.6298


def _fresh_dry_signals():
    """Fake collect_live_signals() result: dry, fresh, as in the
    LIVE_CONTRACT worked example (precip 0.0 mm, wind 6.9 km/h)."""
    now = datetime.now(timezone.utc)
    hours = [f"2026-11-16T{h:02d}:00:00" for h in range(24)]
    return {
        "open-meteo-current": LiveSignal(
            source="open-meteo-current", fetched_at=now, as_of=now,
            values={"precipitation_mm": 0.0, "wind_speed_kmh": 6.9},
            stale=False, error=None, max_age_seconds=3600),
        "open-meteo-forecast": LiveSignal(
            source="open-meteo-forecast", fetched_at=now, as_of=now,
            values={"hours": hours,
                    "precipitation_mm": [0.0] * 24},
            stale=False, error=None, max_age_seconds=6 * 3600),
    }


@pytest.fixture()
def dry_signals(monkeypatch):
    monkeypatch.setattr("backend.routers.live.collect_live_signals",
                        _fresh_dry_signals)
    return _fresh_dry_signals


@pytest.fixture()
def no_signals(monkeypatch):
    monkeypatch.setattr("backend.routers.live.collect_live_signals",
                        lambda: {})
    return None


# ---------------------------------------------------------------------------
# Worked example (LIVE_CONTRACT.md) — deterministic fields
# ---------------------------------------------------------------------------

def test_live_worked_example(client, dry_signals):
    r = client.get("/live/risk", params={
        "lat": LAT_LOOP, "lon": LON_LOOP, "at": AT_WORKED})
    assert r.status_code == 200
    body = r.json()
    assert body["camera_id"] == "3041"
    assert body["intersection"] == "MICHIGAN AND JACKSON"
    assert body["distance_m"] == pytest.approx(443.0, abs=1.0)
    assert body["latitude"] == pytest.approx(41.878217, abs=1e-4)
    assert body["longitude"] == pytest.approx(-87.624456, abs=1e-4)
    assert body["at"] == AT_WORKED
    assert body["current_hour"] == 5
    assert body["date"] == "2026-11-16"
    # Daily is the camera's pre-test mean (no feature row after 2026-10-29).
    assert body["daily_prediction"] == pytest.approx(10.3, abs=0.01)
    assert body["daily_source"] == "pre_test_mean"
    assert body["daily_band"] == "HIGH"
    # Current hour 05:00.
    assert body["predicted_violations_this_hour"] == pytest.approx(
        0.038, abs=0.001)
    assert body["risk_level"] == "LOW"
    assert body["risk_score"] == pytest.approx(0.0011, abs=0.0001)
    assert body["confidence"] == pytest.approx(0.4)
    # The live adjustment does NOT ship.
    assert body["live_adjustment"]["adjustment_applied"] is False
    assert body["live_adjustment"]["adjustment_factor"] == pytest.approx(1.0)
    assert body["live_adjustment"]["live_weather_multiplier"] == pytest.approx(1.0)
    assert body["live_adjustment"]["all_signals_stale"] is False
    assert body["live_adjustment"]["live_weather"]["precipitation_mm"] == 0.0
    assert body["live_adjustment"]["live_weather"]["wind_speed_kmh"] == pytest.approx(6.9)
    assert body["estimate"] is True
    assert len(body["hourly"]) == 24
    assert body["hourly"][5] == pytest.approx(0.038, abs=0.001)
    # Rounded hourly values sum exactly to the rounded daily total
    # (renormalised in _build_response; was a 0.004 display gap before
    # the fix: hourly summed to 10.296 while daily read 10.3).
    assert sum(body["hourly"]) == pytest.approx(
        body["daily_prediction"], abs=1e-9)
    assert body["hourly_band_thresholds"] == {"low_max": 0.1, "high_min": 0.5}
    assert body["daily_band_thresholds"] == {"low_max": 4.0, "high_min": 10.0}
    assert any("05:00" in reason for reason in body["reasons"])
    assert any("no adjustment applied" in reason.lower()
               for reason in body["reasons"])


def test_live_default_at_is_now(client, dry_signals):
    # No `at`: the endpoint evaluates "now" and still returns 200.
    r = client.get("/live/risk", params={"lat": LAT_LOOP, "lon": LON_LOOP})
    assert r.status_code == 200
    body = r.json()
    assert body["camera_id"] == "3041"
    assert body["live_adjustment"]["adjustment_applied"] is False


def test_live_degraded_all_signals_stale_200(client, no_signals):
    """All signals stale is NOT an error: 200 degraded, unadjusted
    baseline, confidence reduced by 0.1 (0.4 -> 0.3)."""
    r = client.get("/live/risk", params={
        "lat": LAT_LOOP, "lon": LON_LOOP, "at": AT_WORKED})
    assert r.status_code == 200
    body = r.json()
    assert body["camera_id"] == "3041"
    assert body["live_adjustment"]["all_signals_stale"] is True
    assert body["live_adjustment"]["adjustment_applied"] is False
    assert body["live_adjustment"]["live_weather"]["precipitation_mm"] is None
    assert body["confidence"] == pytest.approx(0.3)
    assert body["daily_prediction"] == pytest.approx(10.3, abs=0.01)
    assert any("No fresh live weather signal" in reason
               for reason in body["reasons"])


# ---------------------------------------------------------------------------
# Error cases (LIVE_CONTRACT.md error table)
# ---------------------------------------------------------------------------

def test_live_missing_lat_400(client):
    r = client.get("/live/risk", params={"lon": LON_LOOP})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_parameters"


def test_live_missing_lon_400(client):
    r = client.get("/live/risk", params={"lat": LAT_LOOP})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_parameters"


def test_live_non_numeric_lat_400(client):
    r = client.get("/live/risk", params={"lat": "abc", "lon": LON_LOOP})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_parameters"


def test_live_lat_out_of_range_400(client):
    r = client.get("/live/risk", params={"lat": 91, "lon": LON_LOOP})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_parameters"


def test_live_lon_out_of_range_400(client):
    r = client.get("/live/risk", params={"lat": LAT_LOOP, "lon": -181})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_parameters"


def test_live_naive_at_400(client):
    r = client.get("/live/risk", params={
        "lat": LAT_LOOP, "lon": LON_LOOP, "at": "2026-11-16T05:28:06"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_parameters"


def test_live_unparseable_at_400(client):
    r = client.get("/live/risk", params={
        "lat": LAT_LOOP, "lon": LON_LOOP, "at": "yesterday-ish"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_parameters"


def test_live_at_more_than_24h_future_400(client):
    future = (datetime.now(timezone.utc) + timedelta(hours=25)).isoformat()
    r = client.get("/live/risk", params={
        "lat": LAT_LOOP, "lon": LON_LOOP, "at": future})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_parameters"


def test_live_max_distance_too_small_400(client):
    r = client.get("/live/risk", params={
        "lat": LAT_LOOP, "lon": LON_LOOP, "max_distance_m": 50})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_parameters"


def test_live_max_distance_too_large_400(client):
    r = client.get("/live/risk", params={
        "lat": LAT_LOOP, "lon": LON_LOOP, "max_distance_m": 20001})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_parameters"


def test_live_max_distance_non_numeric_400(client):
    r = client.get("/live/risk", params={
        "lat": LAT_LOOP, "lon": LON_LOOP, "max_distance_m": "far"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_parameters"


def test_live_far_coords_404(client):
    # (0, 0) with max 100 m: no camera nearby; nearest_distance_m is
    # still reported so the client can retry with a larger radius.
    r = client.get("/live/risk", params={
        "lat": 0, "lon": 0, "max_distance_m": 100})
    assert r.status_code == 404
    body = r.json()
    assert body["error"] == "no_camera_nearby"
    assert body["nearest_distance_m"] == pytest.approx(9806335.7, abs=5.0)
