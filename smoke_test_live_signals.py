#!/usr/bin/env python3
"""Smoke test for live_signals.py (Redunator Phase 2, Worker B).

Calls the no-key providers live, prints each result (source,
fetched_at, as_of, values, stale), asserts the no-key providers
succeed, and asserts the key-ready providers either fail cleanly
with the "API key required" error (no key set) or succeed live
with sane values (key set in the environment).

TomTom is the exception: when TOMTOM_API_KEY is not set, the
provider falls back to the custom.tomtom connector
(see live_signals.TomTomFlowProvider), so on this VM it succeeds
live instead of failing. The test accepts either outcome for
tomtom-flow: a live success with sane values, or a clean error
naming the env var (connector unavailable off this VM).

Run:  python smoke_test_live_signals.py
Exit code 0 = all assertions passed.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from live_signals import (  # noqa: E402
    LiveSignalError,
    collect_live_signals,
    get_provider,
    is_stale,
)

NO_KEY = ["open-meteo-current", "open-meteo-forecast", "divvy-gbfs"]
KEY_READY = ["tomtom-flow", "here-flow", "cta-bus"]


def main() -> int:
    failures = []

    # --- 1. No-key providers must succeed, fresh, with a fetched stamp.
    print("== no-key providers (live) ==")
    results = collect_live_signals(NO_KEY)
    for name in NO_KEY:
        r = results[name]
        print(f"--- {r.source} ---")
        print(f"  fetched_at: {r.fetched_at.isoformat()}")
        print(f"  as_of:      {r.as_of.isoformat()}")
        print(f"  stale:      {r.stale} (max_age {r.max_age_seconds}s)")
        print(f"  error:      {r.error}")
        print(f"  values:     {json.dumps(r.values)[:600]}")
        if r.error is not None:
            failures.append(f"{name}: unexpected error: {r.error}")
        if r.fetched_at.tzinfo is None or r.as_of.tzinfo is None:
            failures.append(f"{name}: timestamps must be timezone-aware")
        if r.stale:
            failures.append(f"{name}: unexpectedly stale on a live fetch")
        if is_stale(r):
            failures.append(f"{name}: is_stale() disagrees with stale=False")
        if not r.values:
            failures.append(f"{name}: empty values on success")

    # Forecast-specific: 24 hours starting at the current hour.
    fc = results["open-meteo-forecast"]
    if len(fc.values.get("hours", [])) != 24:
        failures.append(
            "open-meteo-forecast: expected 24 forecast hours, got "
            f"{len(fc.values.get('hours', []))}")

    # Divvy-specific: the feed is real — 2k stations, sane occupancy.
    dv = results["divvy-gbfs"]
    if dv.values.get("stations_total", 0) < 1000:
        failures.append(
            f"divvy-gbfs: suspiciously few stations: "
            f"{dv.values.get('stations_total')}")
    occ = dv.values.get("bike_occupancy")
    if occ is None or not (0.0 <= occ <= 1.0):
        failures.append(f"divvy-gbfs: bad bike_occupancy: {occ}")

    # --- 2. Key-ready providers: without a key they must fail cleanly
    #     with "API key required" naming the env var + signup URL;
    #     with a key set they must succeed live with sane values.
    #     TomTom is special in the no-key case: without TOMTOM_API_KEY
    #     it falls back to the custom.tomtom connector, so it may
    #     succeed live on this VM even with no env var.
    print("\n== key-ready providers (key set = live, unset = clean error) ==")
    for var in ("TOMTOM_API_KEY", "HERE_API_KEY", "CTA_BUS_API_KEY"):
        if var in os.environ:
            print(f"  NOTE: {var} is set in this environment — "
                  "key-ready assertions will attempt a real call")
    for name in KEY_READY:
        provider = get_provider(name)
        try:
            r = provider.fetch()
        except LiveSignalError as e:
            msg = str(e)
            print(f"--- {name} ---")
            print(f"  LiveSignalError: {msg}")
            if name == "tomtom-flow":
                # Connector unavailable off this VM: either the env-var
                # error or the connector-unavailable error is fine.
                if ("API key required" not in msg
                        and "connector unavailable" not in msg):
                    failures.append(
                        f"{name}: wrong error: {msg}")
                continue
            if "API key required" not in msg:
                failures.append(
                    f"{name}: wrong error (expected 'API key required'): {msg}")
            if provider.env_var not in msg:
                failures.append(
                    f"{name}: error does not name env var {provider.env_var}")
            if provider.signup_url not in msg:
                failures.append(
                    f"{name}: error does not name signup URL")
            continue
        # Live success (key set, or tomtom connector fallback):
        # validate values per provider.
        print(f"--- {name} ---")
        print(f"  live success: {json.dumps(r.values)}")
        if r.error is not None:
            failures.append(f"{name}: unexpected error: {r.error}")
        if r.stale:
            failures.append(f"{name}: unexpectedly stale on a live fetch")
        if not r.values:
            failures.append(f"{name}: empty values on live fetch")
        if name == "tomtom-flow":
            if r.values.get("current_speed_mph") is None:
                failures.append(f"{name}: no current_speed_mph on live fetch")
            if r.values.get("confidence") is None:
                failures.append(f"{name}: no confidence on live fetch")
        elif name == "here-flow":
            if not r.values.get("segments"):
                failures.append(f"{name}: no segments on live fetch")
            if r.values.get("avg_speed_mph") is None:
                failures.append(f"{name}: no avg_speed_mph on live fetch")
        elif name == "cta-bus":
            if r.values.get("vehicles_active") is None:
                failures.append(f"{name}: no vehicles_active on live fetch")
            if r.values.get("routes_sampled") != 10:
                failures.append(
                    f"{name}: expected 10 routes sampled, got "
                    f"{r.values.get('routes_sampled')}")

    # --- 3. collect_live_signals() over ALL providers never raises,
    #     and records the key errors instead of failing.
    print("\n== collect_live_signals() over all providers ==")
    all_results = collect_live_signals()
    for name, r in all_results.items():
        print(f"  {name}: stale={r.stale} error={r.error!r}")
    for name in KEY_READY:
        r = all_results[name]
        if r.error is None:
            # Live success (key set, or tomtom connector fallback):
            # must be fresh with values.
            if r.stale or not r.values:
                failures.append(
                    f"collect: {name} live result must be fresh "
                    "with values")
            continue
        # Clean error path: "API key required" (tomtom may instead
        # report "connector unavailable"), and the result must be stale.
        if ("API key required" not in r.error
                and not (name == "tomtom-flow"
                         and "connector unavailable" in r.error)):
            failures.append(
                f"collect: {name} wrong error, got {r.error!r}")
        if not r.stale:
            failures.append(f"collect: {name} error result must be stale")

    print()
    if failures:
        print(f"FAIL ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: all smoke-test assertions held.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
