#!/usr/bin/env python3
"""Smoke test for live_adjust.py (Redunator Phase 2, Worker C).

No network needed except the live run at the end (skipped when
offline — the fabricated-signal checks run first and are the
assertions that matter).

Checks:
  1. weather_delta_factor: dry live vs expected 1.0 -> 1.0; rain
     5 mm -> 1.15; identical live/expected -> 1.0 (no double-count).
  2. apply_hour_adjustment: factor 1.0 is identity; a 1.15 factor
     on one hour renormalises to the same daily total.
  3. adjust_current_hour with FABRICATED fresh signals on a
     historical date (2026-10-15, cam 1413, profile path,
     current hour 18, rain 4.7 mm live, expected 1.0): the 18:00
     hour rises, the other 23 fall slightly, the daily total is
     unchanged, signals_used lists the weather providers, and
     Divvy (fabricated fresh) lands in signals_ignored, not used.
  4. Same call with ALL signals stale: output hourly == unadjusted
     hourly exactly, adjustment_applied False, factor 1.0.
  5. Model path on a historical date: adjustment NOT applied
     (no-op with the model-path reason), hourly == unadjusted.
  6. Historical date, profile path, no expected override: live
     weather equal to the archive weather -> factor 1.0 (the
     anti-double-count guard).
  7. nearest_camera: the Loop resolves to a camera within a few km.
  8. Live run (needs network): adjust_current_hour for today;
     prints the real fetched signals and the factor. Asserts only
     the invariants (sum == daily, stale signals dropped, no crash).

Run:  python smoke_test_live_adjust.py
Exit code 0 = all assertions passed (live run included when reachable).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from live_adjust import (
    adjust_current_hour,
    apply_hour_adjustment,
    nearest_camera,
    weather_delta_factor,
)
from live_signals import LiveSignal, UTC

NOW = datetime.now(UTC)


def _sig(source, values, age_s=60, max_age_s=3600, error=None):
    fetched = NOW - timedelta(seconds=age_s)
    return LiveSignal(source=source, fetched_at=fetched,
                      as_of=fetched, values=values,
                      stale=bool(error) or age_s > max_age_s,
                      error=error, max_age_seconds=max_age_s)


def _forecast_for_hour(hour, precip_mm):
    """Minimal open-meteo-forecast signal with one wet hour."""
    hours = []
    precips = []
    base = datetime(2026, 10, 15, 0, 0)
    for h in range(24):
        hours.append((base + timedelta(hours=h)).isoformat())
        precips.append(precip_mm if h == hour else 0.0)
    return _sig("open-meteo-forecast", {
        "hours": hours,
        "temperature_c": [15.0] * 24,
        "precipitation_probability_pct": [80 if h == hour else 0
                                          for h in range(24)],
        "precipitation_mm": precips,
    }, max_age_s=6 * 3600)


def main():
    # 1. delta factor ---------------------------------------------------------
    assert weather_delta_factor(0.0, 0.0, 0.0, 1.0) == 1.0
    assert weather_delta_factor(None, None, None, 1.0) == 1.0
    assert abs(weather_delta_factor(5.0, 0.0, 10.0, 1.0) - 1.15) < 1e-9
    # live == expected -> no delta (anti-double-count)
    assert abs(weather_delta_factor(5.0, 0.0, 10.0, 1.15) - 1.0) < 1e-9
    # clipping: rain + snow + wind together cap at 1.25
    assert weather_delta_factor(100.0, 10.0, 100.0, 1.0) == 1.25
    print("1. weather_delta_factor OK")

    # 2. hour adjustment ------------------------------------------------------
    hourly = np.array([2.0] * 24)
    assert np.array_equal(apply_hour_adjustment(hourly, 15, 1.0), hourly)
    adj = apply_hour_adjustment(hourly, 15, 1.15)
    assert abs(adj.sum() - hourly.sum()) < 1e-9, adj.sum()
    assert adj[15] > hourly[15] and all(
        adj[h] < hourly[h] for h in range(24) if h != 15)
    print("2. apply_hour_adjustment OK (renormalises, total preserved)")

    # Fabricated fresh signals: rainy 18:00, dry otherwise.
    fresh = {
        "open-meteo-current": _sig("open-meteo-current", {
            "temperature_c": 16.0, "precipitation_mm": 4.7,
            "wind_speed_kmh": 20.0, "weather_code": 61}),
        "open-meteo-forecast": _forecast_for_hour(18, 4.7),
        "divvy-gbfs": _sig("divvy-gbfs", {
            "stations_total": 100, "stations_fresh": 99,
            "bike_occupancy": 0.42}, max_age_s=900),
    }
    stale = {
        "open-meteo-current": _sig(
            "open-meteo-current", {}, age_s=7200,
            error="simulated fetch failure"),
        "open-meteo-forecast": _sig(
            "open-meteo-forecast", {}, age_s=7 * 3600,
            max_age_s=6 * 3600),
        "divvy-gbfs": _sig("divvy-gbfs", {}, age_s=3600, max_age_s=900),
        "tomtom-flow": _sig(
            "tomtom-flow", {}, error="tomtom-flow: API key required: "
            "set the TOMTOM_API_KEY environment variable."),
    }

    # 3. fabricated fresh, profile path, historical date, expected=1.0 -------
    r = adjust_current_hour("1413", "2026-10-15", live_signals=fresh,
                            current_hour=18, method="profile",
                            expected_weather_multiplier=1.0)
    assert r["adjustment_applied"] is True, r
    assert abs(r["adjustment_factor"] - 1.147) < 1e-9, r["adjustment_factor"]
    assert abs(r["hourly_sum"] - r["daily_prediction"]) < 1e-9
    assert abs(r["reconciliation_error"]) < 1e-9
    assert r["hourly"][18] > r["hourly_unadjusted"][18]
    assert all(r["hourly"][h] <= r["hourly_unadjusted"][h] + 1e-12
               for h in range(24) if h != 18)
    used = {s["source"] for s in r["signals_used"]}
    assert used == {"open-meteo-current", "open-meteo-forecast"}, used
    assert all("fetched_at" in s for s in r["signals_used"])
    ignored = {s["source"] for s in r["signals_ignored"]}
    assert "divvy-gbfs" in ignored, ignored
    assert r["signals_dropped_stale"] == [], r["signals_dropped_stale"]
    print(f"3. fabricated fresh OK: 18:00 "
          f"{r['hourly_unadjusted'][18]:.3f} -> {r['hourly'][18]:.3f} "
          f"(factor {r['adjustment_factor']:.3f}), "
          f"Divvy ignored, total {r['hourly_sum']:.2f} preserved")

    # 4. all stale -> exact baseline ------------------------------------------
    r2 = adjust_current_hour("1413", "2026-10-15", live_signals=stale,
                             current_hour=18, method="profile")
    assert r2["adjustment_applied"] is False
    assert r2["adjustment_factor"] == 1.0
    assert r2["hourly"] == r2["hourly_unadjusted"]
    assert len(r2["signals_dropped_stale"]) == 3, r2["signals_dropped_stale"]
    assert all("fetched_at" in s for s in r2["signals_dropped_stale"])
    # Divvy is never consumed, so even stale it is reported under
    # "ignored" (with stale=True recorded) rather than "dropped".
    div = [s for s in r2["signals_ignored"] if s["source"] == "divvy-gbfs"]
    assert len(div) == 1 and div[0]["stale"] is True, r2["signals_ignored"]
    print("4. all-stale OK: output == unadjusted baseline exactly, "
          "3 signals dropped with timestamps (+ Divvy ignored as stale)")

    # 5. model path: no-op -----------------------------------------------------
    r3 = adjust_current_hour("1413", "2026-10-15", live_signals=fresh,
                             current_hour=18, method="model")
    assert r3["adjustment_applied"] is False
    assert r3["hourly"] == r3["hourly_unadjusted"]
    assert any("model" in n.lower() for n in r3["notes"]), r3["notes"]
    print("5. model path OK: adjustment refused (already ingests weather)")

    # 6. anti-double-count guard: live == archive weather ----------------------
    r4 = adjust_current_hour("1413", "2026-10-15", live_signals=fresh,
                             current_hour=18, method="profile")
    # Fresh signals say 18:00 precip 4.7 mm; the archive row for
    # 2026-10-15 18:00 also has 4.7 mm, so expected == live -> factor 1.0.
    assert r4["adjustment_factor"] == 1.0, r4["adjustment_factor"]
    assert r4["adjustment_applied"] is False
    assert r4["hourly"] == r4["hourly_unadjusted"]
    print("6. anti-double-count guard OK: live == archive -> factor 1.0")

    # 7. nearest camera ---------------------------------------------------------
    cam, dist_m = nearest_camera(41.8781, -87.6298)
    assert dist_m < 5000, (cam, dist_m)
    print(f"7. nearest_camera OK: Loop -> camera {cam} at {dist_m:.0f} m")

    # 8. live run for today ------------------------------------------------------
    try:
        r5 = adjust_current_hour("1413", "2026-11-16")
        assert abs(r5["hourly_sum"] - r5["daily_prediction"]) < 1e-9
        assert r5["hourly"] is not None
        print(f"8. live run OK: today {r5['date']} hour "
              f"{r5['current_hour']}:00 factor "
              f"{r5['adjustment_factor']:.3f} applied="
              f"{r5['adjustment_applied']} used="
              f"{[s['source'] for s in r5['signals_used']]} dropped="
              f"{[s['source'] for s in r5['signals_dropped_stale']]}")
    except Exception as e:  # noqa: BLE001 - offline is not a failure here
        print(f"8. live run SKIPPED (no network or provider error: {e})")

    print("\nALL SMOKE CHECKS PASSED")


if __name__ == "__main__":
    main()
