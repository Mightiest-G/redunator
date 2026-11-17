#!/usr/bin/env python3
"""Phase 2c smoke test: predict_hourly with vs without demand.

Checks for each camera-date: 24 hours, sum == daily prediction
(Phase 2 daily model), peak hour/value per variant. Also exercises
the unmapped-camera citywide fallback and a holiday date.
"""
from __future__ import annotations

import numpy as np

from hourly_risk import predict_hourly

CASES = [("1413", "2026-10-15"), ("2251", "2026-10-15")]
EXPECTED_DAILY = {"1413": 53.01, "2251": 46.82}


def show(r: dict, label: str):
    h = np.array(r["hourly"])
    pk = int(np.argmax(h))
    print(f"  {label}: method={r['method']} daily={r['daily_prediction']:.2f} "
          f"({r['daily_source']}) sum={r['hourly_sum']:.6f} "
          f"recon_err={r['reconciliation_error']:.2e} "
          f"peak={pk:02d}:00 val={h[pk]:.2f} band={r['bands'][pk]} "
          f"day_type={r['demand_day_type']} area={r['community_area']}")
    return h


def main():
    ok = True
    for cam, date in CASES:
        print(f"cam {cam} {date} (expected daily {EXPECTED_DAILY[cam]}):")
        r_off = predict_hourly(cam, date, demand=False)
        r_on = predict_hourly(cam, date, demand=True)
        h_off = show(r_off, "no demand ")
        h_on = show(r_on, "with demand")
        for r, h in ((r_off, h_off), (r_on, h_on)):
            assert len(h) == 24, "not 24 hours"
            assert abs(r["hourly_sum"] - r["daily_prediction"]) < 1e-9, "sum != daily"
            assert abs(r["daily_prediction"] - EXPECTED_DAILY[cam]) < 0.02, \
                f"daily {r['daily_prediction']} != expected {EXPECTED_DAILY[cam]}"
            assert all(v >= 0 for v in h), "negative hourly value"
        # Demand must actually change the shape (not just rescale).
        assert not np.allclose(h_off, h_on), "demand changed nothing"
        print(f"  shape change: max |d hourly| = {np.abs(h_on - h_off).max():.3f}, "
              f"peak moved {int(np.argmax(h_off)):02d}:00 -> {int(np.argmax(h_on)):02d}:00")

    # Unmapped camera (1623, no coordinates) -> citywide demand, no crash.
    print("cam 1623 (unmapped, no coordinates) 2026-10-15:")
    r = predict_hourly("1623", "2026-10-15", demand=True)
    show(r, "with demand")
    assert r["community_area"] is None and r["demand_day_type"] == "weekday"
    assert abs(r["hourly_sum"] - r["daily_prediction"]) < 1e-9

    # Holiday date: 2026-07-03 (observed Independence Day, Fri) — model
    # path unavailable that far out? No: weather covers to 2026-10-29,
    # so model path applies; day_type must be holiday.
    print("cam 1413 2026-07-03 (observed holiday):")
    r = predict_hourly("1413", "2026-07-03", demand=True)
    show(r, "with demand")
    assert r["demand_day_type"] == "holiday", r["demand_day_type"]
    assert abs(r["hourly_sum"] - r["daily_prediction"]) < 1e-9

    # Explicit method strings (Phase 2d: the profile path now also
    # applies the slowness layer by default, so it shows in the label).
    r = predict_hourly("1413", "2026-10-15", method="profile+demand")
    assert r["method"] == "profile+demand+slowness" and r["demand"] is True
    assert r["slowness_applied"] is True
    r = predict_hourly("1413", "2026-10-15", method="model", demand=False)
    assert r["method"] == "model" and r["demand"] is False
    assert r["slowness_applied"] is False  # slowness never shapes the model path
    r = predict_hourly("1413", "2026-10-15", method="profile",
                       demand=False, slowness=False)
    assert r["method"] == "profile"  # Phase 2b profile output, reproduced
    print("explicit method strings OK (profile+demand+slowness, model+demand=False, profile bare)")

    # Demand multiplier ranges (diagnostic).
    import hourly_model as hm
    for cam, dt in (("1413", "2026-10-15"), ("2251", "2026-10-15")):
        dm = hm.demand_multipliers_for_day(cam, dt)
        print(f"  demand multipliers cam {cam}: min {dm.min():.2f} "
              f"max {dm.max():.2f} mean {dm.mean():.4f}")
    print("SMOKE TEST PASSED" if ok else "SMOKE TEST FAILED")


if __name__ == "__main__":
    main()
