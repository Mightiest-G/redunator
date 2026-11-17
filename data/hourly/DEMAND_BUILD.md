# Phase 2c — Trip-demand layer: build note (Worker B)

Date: 2026-11-15. Builder script: `build_demand.py` (mapping + profiles
+ staleness). Blend + API: `hourly_model.py` (`demand_weights`,
`demand_multipliers_for_day`, `day_type_for_date`,
`camera_community_area`) and `hourly_risk.py` (`predict_hourly(...,
demand=True)`). Smoke test: `smoke_test_demand.py`. Validation and the
HOURLY_REPORT.md Phase 2c section are Worker C's — not done here.

## 1. Camera → community-area mapping

Source: `data/hourly/raw/community_areas.geojson` (dataset `igwz-8jzy`,
77 areas, key `area_numbe`, CRS84 lon/lat). Camera lat/lons from
`data/processed/red_light_features.parquet` (first row per camera).
Point-in-polygon with shapely 2.1.2 (new pinned dep in
`requirements.txt`; `STRtree` bbox candidates + explicit `covers`
test — note: `STRtree.query(pt, predicate="contains")` tests
point-contains-polygon and returns nothing; that cost one debug round).

- **361 / 365 cameras mapped; 0 fall outside all polygons.**
- **4 unmapped — all have no coordinates:** 1623, 2552, 2592, 2662
  (same 4 as Phase 2b). Fallback: **citywide demand profile**
  (area 0), documented in `hourly_model.demand_weights`.
- Cameras cover **59 of 77** areas (max 16 cameras in one area);
  18 areas have no camera and therefore no local demand use, but all
  77 areas still have profiles in the file.
- Output: `data/hourly/processed/camera_community_area.csv`
  (camera_id, community_area, community_name, mapped, latitude,
  longitude, distance_to_nearest_area_m, nearest_area, note).
  `distance_to_nearest_area_m` is 0.0 for every mapped camera; it is
  only populated for a camera outside all polygons (none exist).
  Examples: cam 1413 → area 6 LAKE VIEW; cam 2251 → area 56 GARFIELD
  RIDGE.

## 2. Demand profiles

Source: `data/hourly/processed/trip_hourly_by_area.parquet` (Worker A:
8,279,951 rows, 674,559,395 trips). Null pickup areas (taxi 5.5%,
TNP 8.5%) are **excluded** — they cannot be mapped to a camera.

- **Blend: summed counts (taxi + TNP).** TNP dominates by volume
  (~11x taxi), so the blend is effectively the rideshare shape with a
  small taxi tilt. Summing is the natural default: both sources count
  the same thing (vehicle trips starting in the area) and there is no
  principled weight to favour taxi; a volume-weighted average of the
  two shapes would give the same result as summing.
- **Window: 2023-01-01 → present** (TNP through 2026-09-30, taxi
  through 2026-11-01; no forward-filling — the profile is a shape,
  so the differing end dates only change the day counts slightly).
  Justification: the COVID regime break (TNP ~2.2M trips/week Feb
  2020 → ~0.62M/week Jun 2020) makes a 2019–present average mix two
  regimes; 2023–present is the stable post-COVID regime (see §3).
  Trips in window: 659,173,028.
- **Day types: weekday / weekend / holiday** (holiday split done —
  it was cheap: `holidays.US`, same convention as `features.py`,
  federal holidays INCLUDING federally observed dates; a holiday
  takes precedence over weekday/weekend). Day counts in window:
  weekday 959, weekend 398, holiday 44.
- **Weights sum to 1 per (area, day_type): max |sum − 1| = 2.22e-16**
  (exact renormalisation, residual on the largest weight — same
  pattern as Phase 2b profiles).
- Citywide profiles (area 0, the fallback): weekday peak 18:00
  (0.0669), trough 03:00 (0.0070); weekend peak 18:00 (0.0608),
  trough 05:00 (0.0130); holiday peak 18:00 (0.0608), trough 04:00
  (0.0161). Note area 6 (Lake View) holidays peak at 00:00 (0.065) —
  late-night trip share, plausible for a nightlife area.
- Output: `data/hourly/processed/demand_profiles.parquet`
  (5,616 rows = 78 areas × 3 day types × 24 h; columns area,
  day_type, hour, weight, trips, n_days, window). Area 0 = citywide.

## 3. Staleness check

Per-area weekday/weekend profiles for full calendar years
(2019 / 2023 / 2024 / 2025), summed counts. Full numbers in
`data/hourly/demand_staleness.json`. Mean / max |Δweight| over all
(area, day_type, hour) cells:

| comparison | mean \|Δw\| | max \|Δw\| | worst areas |
|---|---|---|---|
| 2019 vs 2023 | 0.0037 | 0.0310 | 55 HEGEWISCH wknd, 55 wkday, 31 LOWER WEST SIDE wknd |
| 2023 vs 2024 | 0.0016 | 0.0303 | 36 OAKLAND wknd, 9 EDISON PARK wknd, 47 BURNSIDE wknd |
| 2024 vs 2025 | 0.0016 | 0.0320 | 36 OAKLAND wknd, 60 BRIDGEPORT wknd, 74 MOUNT GREENWOOD wknd |
| 2019 vs 2025 | 0.0044 | 0.0384 | 55 wkday, 31 wknd, 55 wknd |

**Flag — drift is real but concentrated, not systemic.** The mean
drift post-COVID is small (0.0016 year-on-year, vs 0.0037 across the
COVID break), which supports the 2023–present window. But the max
drift (~0.03) persists even between adjacent post-COVID years, and
it is always the same kind of area: low-volume outlying areas
(Oakland ~36–45k weekend trips/year ≈ 350–430 trips/weekend day,
Hegewisch, Burnside, Edison Park, Mount Greenwood). At those volumes
a ±0.03 weight swing is small-sample noise (a few hundred trips
moving between hours), not a regime change — but it means
**per-area demand shapes for low-volume areas are noisy**, and
Worker C's validation should watch whether the demand layer helps or
hurts there. A possible refinement (not done): shrink low-volume
area profiles toward the citywide shape.

## 4. Blend into the hourly risk

`hourly_risk.predict_hourly(camera_id, date, method="auto",
demand=True)`:

- Demand multiplier = `demand_weight[h] / mean(demand_weight)` —
  mean exactly 1.0, so it only reshapes the day. Applied as an extra
  multiplier on **both** paths:
  profile: `daily × profile × weather × congestion × demand`;
  model: `daily × model_weights × demand` (renormalised).
- **The Poisson model did NOT take demand as a feature** — that
  would need a retrain + re-validation (Worker C's call). Demand is a
  post-hoc shape layer on the model path, documented in both module
  docstrings.
- Renormalisation: the 24 hours still sum **exactly** to the daily
  prediction (same float-residual-on-peak-hour step as Phase 2b;
  smoke-test reconciliation error 0.00e+00). Every hourly value is
  still labelled `estimate=True`.
- Methods: `"profile"`, `"model"`, `"profile+demand"`,
  `"model+demand"`; `demand=False` reproduces Phase 2b exactly
  (profile branch with `dm=None` is byte-identical logic; model
  branch `daily × weights` divided by its own sum is identical
  because model weights already sum to 1). Default is demand ON
  (`method` reports e.g. `"model+demand"`); result dict adds
  `demand`, `demand_day_type`, `community_area`.
- Demand multiplier ranges (2026-10-15, weekday): cam 1413
  0.23–1.82, cam 2251 0.11–1.48. The 0.11 overnight floor for
  Garfield Ridge is the deepest in the smoke test — demand can
  nearly zero-out small hours in low-overnight-demand areas.

## 5. Smoke tests (`smoke_test_demand.py`, all passed)

2026-10-15 (Thursday, weekday). Daily predictions reconcile exactly
with Phase 2 (53.01 / 46.82; unrounded sums 53.009553 / 46.822644,
recon error 0.00e+00). Both variants use the model path
(`hourly_model.joblib` present, weather covers the date).

| camera | no demand | with demand |
|---|---|---|
| 1413 LSD & Belmont (area 6) | peak **15:00, 4.74** MEDIUM | peak **18:00, 6.08** MEDIUM; max \|Δhourly\| 2.26 |
| 2251 Cicero & I-55 (area 56) | peak **15:00, 3.94** LOW | peak **15:00, 4.23** MEDIUM; max \|Δhourly\| 0.88 |

Demand moves 1413's peak from the crash-driven 15:00 to the
trip-driven 18:00 and lifts it by 1.34; for 2251 it only nudges the
peak (3.94 → 4.23, crossing the LOW/MEDIUM line). Extra checks
passed: unmapped cam 1623 → citywide demand, daily 3.45
(pre_test_mean), peak 17:00 0.34 LOW; holiday date 2026-07-03
(observed Independence Day) → `demand_day_type="holiday"`, peak
17:00 6.84 MEDIUM; explicit `method="profile+demand"` /
`method="model", demand=False` strings behave as documented.

## 6. Re-run

```bash
cd ~/workspace/redunator
~/workspace/redunator-venv/bin/python build_demand.py   # map + profiles + staleness
~/workspace/redunator-venv/bin/python smoke_test_demand.py
```

Re-run verified 2026-11-15: byte-identical outputs (same md5s).
`shapely==2.1.2` added to `requirements.txt`. Daily model files
untouched — md5s verified at end of build (see §7).

## 7. Files

- `data/hourly/processed/camera_community_area.csv` (365 rows)
- `data/hourly/processed/demand_profiles.parquet` (5,616 rows)
- `data/hourly/demand_staleness.json`
- `build_demand.py`, `smoke_test_demand.py` (new)
- `hourly_model.py`, `hourly_risk.py` (modified: demand layer only)
- `requirements.txt` (added `shapely==2.1.2`)
- Daily model md5s (unchanged): joblib
  `9162dcbe76bf842584bd6f208c8bcb99`, onnx
  `90b9160389dd76d945bc8428a9ec3bf0`, json
  `28c83534e67267e272108c582ed0c56b`
