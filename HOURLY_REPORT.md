# Redunator — Phase 2b Hourly Risk Index Report

Built 2026-11-15. The hourly layer splits the shipped Phase 2 **daily**
prediction for one camera-day across 24 hours. It never changes the
daily total.

> **Every hourly number here is an ESTIMATE.** The daily red-light
> violation data has no hour-of-day, so no hourly violation count can
> ever be *measured*. The hourly shape comes from proxy signals —
> crashes near each camera and congestion speeds near each camera —
> and the daily total always comes from the shipped Phase 2 daily model
> (`redunator_brain_v1`, Random Forest). The hourly layer only shapes
> the distribution within the day.

Phase 2 daily report: [REPORT.md](REPORT.md). Method detail from the
build step: [data/hourly/HOURLY_MODEL_SUMMARY.md](data/hourly/HOURLY_MODEL_SUMMARY.md).
Source verification: [data/hourly/SOURCES_VERIFIED.md](data/hourly/SOURCES_VERIFIED.md).

## 1. Sources verified (worker A, live 2026-11-15)

Every claim below was observed in an API response or in the pulled
data — nothing assumed.

| Source | Verified ID | Window ingested | Rows |
|---|---|---|---|
| Traffic Tracker — Congestion by Segments, historical 2024–2026 | `4g9f-3jbs` | 2025-01-01 → **2026-04-30 (feed end)** | 101,139,655 in source; 1,947,880 hourly aggregates pulled |
| Traffic Tracker — Congestion by Regions, historical 2018–2026 | `kf7e-cur8` | 2025-01-01 → **2026-04-30 (feed end)** | 323,594 hourly aggregates pulled |
| Traffic Crashes — Crashes | `85ca-t3if` | 2025-01-01 → 2026-10-29 | 202,151 in window (199,936 geocoded) |
| Open-Meteo archive, Chicago 41.8781,-87.6298 (no key) | `archive-api.open-meteo.com/v1/archive` | 2025-01-01 → 2026-10-29 | 16,008 hourly rows, **0 gaps** |

**All five dataset IDs in the original Phase 2b brief were wrong.**
`8v94-mt3p`, `3v54-hgmi`, `n4j6-ws8y`, `ft7m-7xmn`, `85fq-67pd`
all return HTTP 404 from the Socrata metadata API. The real IDs
above were found through the Socrata catalog API.

**The congestion feed is DEAD. Last row 2026-04-30 01:10:21.** Both
"current" datasets are labelled *Deprecated* / "no longer updated".
Congestion therefore **cannot cover the Phase 2 holdout window
(2026-08-01 → 2026-10-29) at all** — it is usable only as a
training-period feature (2025-01-01 → 2026-04-30). It is never
forward-filled across the 3-month gap: for any date after
2026-04-30 the congestion multiplier is exactly 1.0 for every hour
(verified in §5). No newer congestion dataset exists in the catalog
(searched 2026-11-15). A live congestion signal after April 2026
would need TomTom/HERE (keys required; none created — see
`traffic_flow.py`, which defines the plug-in interface).

Crashes: hourly counts within **200 m** of each camera (haversine).
59,158 camera-hours with ≥1 crash; 60,513 crashes attributed to ≥1
camera (30.3% of geocoded crashes — the rest are >200 m from every
red-light camera). Citywide hour distribution peaks **15:00**
(16,051 crashes in-window) and troughs **04:00** (2,255) — plausible.

**4 cameras have no coordinates and cannot be mapped or
crash-counted: 1623, 2552, 2592, 2662.** They get the citywide
profile exactly (same caveat as Phase 2: map placement meaningless).
41 cameras were remapped from dead 2010–2012 snapshot segments to
their nearest *live* segment (median distance 802 m — low-trust
congestion for those cameras, flagged per camera).

Timestamps are naive hourly labels in a flat 24-rows-per-day grid.
The 02:00 row on spring-forward days (2025-03-09, 2026-03-08) is
not a real local hour; all three sources share the grid, so joins
are consistent.

## 2. Method

### 2.1 Hourly profiles (the fallback and the baseline)

File: `data/hourly/processed/hourly_profiles.parquet` — 8,760 rows
(365 cameras × 24 hours). Profile weights sum to exactly 1 per
camera (max |Σweight − 1| = 4.44e-16).

- Local crash share, Dirichlet-smoothed toward the citywide share:
  `crash_local[h] = (c[h] + 30·p_city[h]) / (n + 30)`, where `c[h]` is
  the camera's attributed crashes in hour `h` and `n = Σc`. Prior
  strength K = 30: the median camera (n = 158) puts ~84% weight on
  its own counts; the thinnest camera (n = 27) ~47%. Thin-data
  threshold n < 50: 8 cameras are thin (the 4 unmappable, n = 0, plus
  1243, 1244, 2181, 2182, n = 27–49).
- Congestion share: segment hourly mean speeds `s[h]`; free-flow
  `ff` = 90th percentile; slowness `g[h] = max(0, (ff − s[h]) / ff)`;
  `cong_share[h] = (g[h] + 0.02) / Σ(g + 0.02)`. Speed varies only
  modestly by hour (citywide mean 23.0 mph at 16:00 vs 26.0 at
  02:00), so congestion is a weak shape signal and gets a small
  weight on purpose.
- Blend: `profile = (1 − w_cong)·crash_local + w_cong·cong_share`,
  renormalised. `w_cong = 0.25` for good coverage (243 cameras),
  `0.10` for remapped / distant / partial coverage (113 cameras),
  `0.00` for no usable segment (9 cameras).

Citywide shape: peak **15:00** (share 0.0803), trough **04:00**
(0.0113). Per-camera peaks: 99 cameras peak at 15:00, 93 at 16:00,
78 at 17:00, 13 at 08:00 (morning-peak commuter routes — e.g.
camera 1413's raw profile peaks 08:00).

### 2.2 Disaggregation

`hourly_risk.py::predict_hourly(camera_id, date)`:

```
raw[h] = daily_prediction × profile_weight[h] × weather_mult[h]
         × congestion_mult[h]          (profile path)
hourly[h] = raw[h] / Σraw × daily_prediction
```

The renormalisation is deliberate: the multipliers **shape the day
only**; the 24 hourly values always sum to the daily prediction.
On the model path, `hourly = daily × model_weights` (weights
already sum to 1) and **no** extra weather/congestion multipliers
are applied — the model already includes weather and speed as
features. Daily predictions come from the shipped RF model via
`daily_prediction()` (same feature list and fill rules as
`train.py`); dates with no feature-table row fall back to the
camera's pre-test mean, or the citywide pre-test mean (5.34/day)
for an unknown camera — flagged as `daily_source`.

**Risk bands** use the Phase 2 daily thresholds applied to the
hourly prediction, as specified: LOW < 4, MEDIUM 4–<10, HIGH ≥ 10.
Consequence, stated plainly: a typical camera-day predicts ~5
violations over 24 hours, so a typical hour is ~0.2 and almost
every hour is LOW. Even the hottest camera (1413, 53/day) only
reaches MEDIUM at its peak hour. Hourly HIGH is rare by
construction; the daily band remains the right unit for "is this
a bad day".

### 2.3 Multipliers (ESTIMATES — assumed planning values)

**Weather multiplier** (one hour):
`1 + 0.10 if precip > 0 + min(0.05, 0.01·precip_mm) +
min(0.10, 0.06·snowfall_cm) + min(0.03, 0.002·max(0, wind−30))`,
clipped to [1.0, 1.25]. Dry/calm hours return exactly 1.0. These
coefficients are **ASSUMED planning values, NOT measured effects
on violations** — violations are daily-only, so no hourly weather
effect on violations can be estimated from the data. They are
calibrated to the observed within-hour wet-vs-dry **crash** ratio:
wet hours average ~1.15× the crashes of dry hours in the same
hour-of-day (range 1.04–1.49 across hours; light rain 0.1–2.5 mm
≈ 1.13×, ≥ 2.5 mm ≈ 1.15× on the raw means). A rainy hour gets
~1.10–1.15, a snowy hour up to ~1.21. **The SDD's 1.3× / 1.5×
figures were NOT used and are not treated as measured fact.**

**Congestion multiplier** (one camera-hour):
`1 + (typical_speed[h] − observed_speed) / typical_speed[h]`,
clipped to [0.80, 1.25]; coefficient 1.0 is an ASSUMED planning
value. **It returns 1.0 for every timestamp after 2026-04-30** —
the congestion feed is dead and is never forward-filled.

### 2.4 Model choice (worker B's comparison)

Target = **hourly crash counts within 200 m of each camera
(PROXY)**. Split (time-based, inside the congestion window):
train 2025-01-01 → 2026-02-28 (3,673,536 camera-hours), test
2026-03-01 → 2026-04-30 (528,504 camera-hours). Baseline =
profile-only (train-window profiles, rebuilt with no leakage from
test crashes) × camera train daily crash rate. Metric: mean
Poisson deviance (primary), RMSE / MAE secondary. Test actual
total: 5,364 crashes.

| Model | Mean Poisson deviance | RMSE | MAE | Pred. total |
|---|---|---|---|---|
| **Profile-only baseline** | 0.0892 | 0.1019 | 0.0202 | 5,461 |
| PoissonRegressor α=0.1 | 0.0932 | 0.1021 | 0.0203 | 5,463 |
| PoissonRegressor α=1.0 | 0.0937 | 0.1022 | 0.0202 | 5,426 |
| PoissonRegressor α=10.0 | 0.0937 | 0.1022 | 0.0203 | 5,457 |
| **XGBoost Poisson — SHIPS** | **0.0879** | **0.1018** | **0.0200** | **5,399** |

**Verdict: the XGBoost Poisson model ships** (`hourly_model.joblib`,
1.4 MB, `count:poisson`, 300 rounds, depth 6, lr 0.05, seed 42),
used through `predict_hourly(..., method="model")` (the default
`method="auto"` uses it when available). The linear
PoissonRegressor **loses** on every alpha and is **not** shipped.
The win is narrow (−1.5% deviance) and is reported as narrow.
Feature importances (gain): `hour_cos` 0.415, `log_cam_rate`
0.126, `hour_sin` 0.114, `is_weekend` 0.082, `dow_sin` 0.055, the
rest ≤ 0.04 — hour-of-day dominates, as expected.

## 3. Independent validation (worker C)

This is **not** a re-run of worker B's table. It evaluates the
hourly shape on ground worker B never touched: the **Phase 2
holdout window 2026-08-01 → 2026-10-29**, where congestion does
not exist at all. Script: `validate_hourly.py`, results:
`hourly_validation.json`.

**Design (leakage-free).** Profiles were rebuilt using only
pre-holdout crashes (2025-01-01 → 2026-07-31) via
`hourly_model.build_profiles(crash_end="2026-07-31")`, so no
holdout crash leaks into the shape being tested. (The shipped
profiles were built on the full window through 2026-10-29 and
therefore partly reflect holdout crashes — the drift between the
two profile sets is small: mean |Δweight| 0.0032, max 0.0489 —
but it is real, and this validation measures the honest
out-of-sample shape quality.) Level for the profile variants is
each camera's **pre-holdout daily crash rate** (attributed crashes
÷ 577 days), fixed and leakage-free, so the comparison tests
shape at a constant level. The shipped XGBoost model is scored
with feature construction mirroring `model_weights` exactly
(same matrices, same train-window `log_cam_rate`, same
trailing-7-day rate, same typical-speed fill for the dead
congestion feed). Target = hourly crash counts within 200 m
(PROXY — hourly violations are unobservable).

**Holdout:** 2026-08-01 → 2026-10-29, 90 days × 361 cameras =
**779,760 camera-hours**; **8,517 observed attributed crashes**;
8,345 non-zero camera-hours. Observed citywide peak hour:
**15:00** (share 0.0867).

| Variant | Mean Poisson deviance | RMSE | MAE | Pred. total | Hourly-share Pearson r | Spearman ρ | Pred. peak hour |
|---|---|---|---|---|---|---|---|
| Flat (uniform 1/24) | 0.0974 | 0.1059 | 0.0211 | 8,110 | n/a (constant) | n/a | — |
| Citywide shape × rate | 0.0940 | 0.1057 | 0.0210 | 8,110 | 0.986 | 0.983 | 15:00 |
| Profile-only (pre-holdout) | 0.0945 | 0.1058 | 0.0210 | 8,110 | **0.991** | **0.986** | 15:00 |
| Profile + weather (shipped profile path) | 0.0946 | 0.1058 | 0.0210 | 8,110 | 0.991 | 0.986 | 15:00 |
| **XGBoost, raw counts** | **0.0933** | **0.1057** | 0.0215 | **8,504** | 0.988 | 0.984 | 16:00 |
| XGBoost shape × pre-holdout rate | 0.0933 | 0.1057 | 0.0210 | 8,110 | 0.988 | 0.984 | 16:00 |

Actual total: 8,517. The fixed-rate variants predict 8,110
(−4.8%: the pre-holdout crash rate runs slightly below the
holdout rate); the XGBoost raw counts predict 8,504 (−0.2%,
because its trailing-7-day rate adapts the level to recent
crashes — past-only, no leakage).

**Reading the table honestly:**

- **The XGBoost model still wins on the unseen holdout** —
  deviance 0.0933 vs 0.0945 profile-only and 0.0940 citywide —
  the same direction and roughly the same size as worker B's
  in-window win (0.0879 vs 0.0892). Its shape correlation
  (r = 0.988) is a touch below the profile's (0.991), and its
  **MAE is slightly worse** than the profile's (0.0215 vs
  0.0210) — the win is on deviance, not on every metric.
- **Per-camera profiles do NOT beat the citywide shape on
  holdout deviance** (0.0945 vs 0.0940). At the camera-hour
  level, per-camera hour shares carry sampling noise (the
  thinnest pre-holdout camera has only 24 crashes; 6 cameras
  have n < 50) that outweighs the per-camera signal — even
  though the profile's aggregate 24-hour share correlates
  marginally better with the observed share (r = 0.991 vs
  0.986). The per-camera profile's value is in the aggregate
  shape and in per-camera peak hours, not in camera-hour
  deviance.
- **The weather multipliers do not measurably help on this
  holdout**: profile + weather deviance 0.0946 vs profile-only
  0.0945 — a wash. In the holdout only 438 of 2,160 hours have a
  multiplier above 1.0 (mean 1.0223, max 1.150), so the effect
  is small either way; the assumed values are not contradicted,
  but neither are they confirmed. They stay in the product as
  planning values, not as a validated improvement.
- **Peak hour: the profile path gets it right (15:00)**,
  matching the observed holdout peak; **the XGBoost model is
  off by one hour (16:00)** — its predicted 15:00 share is
  0.0791 vs the observed 0.0867, and its 16:00 share (0.0802)
  edges out its own 15:00. The evening rush (15:00–17:00) is
  right in both cases; the exact peak hour is not pinned down
  to better than ±1 hour by either path.

### 3.1 Per-hour calibration (holdout)

Predicted share vs observed crash share by hour-of-day
(citywide, holdout). Ratio > 1 = over-predicted, < 1 =
under-predicted.

| Hour | Observed share | Profile+weather pred. share | Ratio | XGBoost pred. share | Ratio |
|---|---|---|---|---|---|
| 00:00 | 0.0198 | 0.0213 | 1.07 | 0.0192 | 0.97 |
| 01:00 | 0.0151 | 0.0189 | 1.25 | 0.0158 | 1.04 |
| 02:00 | 0.0115 | 0.0179 | **1.55** | 0.0146 | **1.27** |
| 03:00 | 0.0130 | 0.0138 | 1.06 | 0.0117 | 0.90 |
| 04:00 | 0.0095 | 0.0129 | 1.35 | 0.0105 | 1.11 |
| 05:00 | 0.0155 | 0.0149 | 0.96 | 0.0124 | **0.80** |
| 06:00 | 0.0204 | 0.0219 | 1.07 | 0.0185 | 0.91 |
| 07:00 | 0.0453 | 0.0430 | 0.95 | 0.0416 | 0.92 |
| 08:00 | 0.0484 | 0.0524 | 1.08 | 0.0494 | 1.02 |
| 09:00 | 0.0424 | 0.0444 | 1.05 | 0.0463 | 1.09 |
| 10:00 | 0.0409 | 0.0445 | 1.09 | 0.0464 | 1.14 |
| 11:00 | 0.0527 | 0.0488 | 0.93 | 0.0505 | 0.96 |
| 12:00 | 0.0567 | 0.0559 | 0.99 | 0.0587 | 1.04 |
| 13:00 | 0.0576 | 0.0602 | 1.05 | 0.0649 | 1.13 |
| 14:00 | 0.0700 | 0.0664 | 0.95 | 0.0728 | 1.04 |
| 15:00 | 0.0867 | 0.0774 | 0.89 | 0.0791 | 0.91 |
| 16:00 | 0.0796 | 0.0770 | 0.97 | 0.0802 | 1.01 |
| 17:00 | 0.0808 | 0.0744 | 0.92 | 0.0777 | 0.96 |
| 18:00 | 0.0683 | 0.0592 | **0.87** | 0.0599 | **0.88** |
| 19:00 | 0.0436 | 0.0470 | 1.08 | 0.0470 | 1.07 |
| 20:00 | 0.0377 | 0.0380 | 1.01 | 0.0369 | 0.98 |
| 21:00 | 0.0344 | 0.0343 | 1.00 | 0.0341 | 0.99 |
| 22:00 | 0.0249 | 0.0301 | 1.21 | 0.0282 | 1.13 |
| 23:00 | 0.0251 | 0.0253 | 1.01 | 0.0240 | 0.95 |

**Where the index is weak — stated plainly:**

- **Overnight is the worst region.** The profile path
  over-predicts 02:00 by 1.55× and 04:00 by 1.35× (observed
  shares 0.0115 and 0.0095 — only 98 and 81 crashes in 90 days
  across all cameras, so the Dirichlet smoothing floor and the
  0.02 congestion floor keep weight in hours where almost
  nothing happens). The XGBoost model is better overnight
  (02:00 ratio 1.27) but still over-predicts. This matches
  worker B's in-window finding (baseline 1.9× at 02:00, model
  1.4×) — the overnight dip in the per-camera profile is
  partly small-sample noise, and the model pools hour
  information across cameras and regularises it.
- **The afternoon peak is under-predicted.** Both paths put
  too little weight at 15:00 (ratios 0.89 / 0.91) and 18:00
  (0.87 / 0.88): the predicted rush is flatter than the
  observed one. The shape is right in outline (rush 15:00–
  17:00, trough 04:00) but compressed toward the mean.
- **Daytime hours 07:00–21:00 are the strong region**: ratios
  mostly 0.87–1.14 for both paths, with 12:00, 20:00–21:00
  and 23:00 within ±0.05.

## 4. Phase 2c — trip-demand layer (validation + worked example)

Built 2026-11-15 (workers A/B build, worker C validation — this
section). The demand layer multiplies the hourly shape by a
community-area trip-demand multiplier (taxi + rideshare pickups in
the camera's area, by day type), then renormalises so the day still
sums to the daily prediction exactly. Full build note:
[data/hourly/DEMAND_BUILD.md](data/hourly/DEMAND_BUILD.md); source
verification: [data/hourly/TRIP_SOURCES_VERIFIED.md](data/hourly/TRIP_SOURCES_VERIFIED.md);
exact queries: [data/hourly/TRIP_QUERIES.md](data/hourly/TRIP_QUERIES.md).

### 4.1 Sources (worker A, verified live 2026-11-15)

The two trip datasets named in the original brief are frozen;
successors were found through the Socrata catalog API instead. Every
row pulled is a real server-side `count(*)` of trips starting in one
hour in one pickup community area — no averages in the ingest.

| Source | Verified ID | Pulled window | Pulled trips |
|---|---|---|---|
| Taxi Trips (2013–2023) | `wrvz-psew` | 2019-01-01 → 2023-12-31 | 37,192,437 |
| Taxi Trips (2024–) | `ajtu-isnz` | 2024-01-01 → 2026-11-01 | 19,277,823 |
| TNP Trips (2018–2022) | `m6dm-c72p` | 2019-01-01 → 2022-12-31 | 282,141,543 |
| TNP Trips (2023–2024) | `n26f-ihde` | 2023-01-01 → 2024-12-31 | 174,369,516 |
| TNP Trips (2025–) | `6dvr-xwnh` | 2025-01-01 → 2026-09-30 | 161,578,076 |
| Boundaries — Community Areas | `igwz-8jzy` | 77 areas | — |

**Total: 674,559,395 trips, 2019 → 2026** (8,279,951 aggregated
day × hour × area rows in
`data/hourly/processed/trip_hourly_by_area.parquet`). Reconciliation
vs the datasets' published row counts is exact (diff 0) for all five
files. The five files abut with no overlap and no gap; taxi covers
2,862/2,862 days, TNP 2,830/2,830 days, zero missing days. Null
pickup areas (taxi 5.5%, TNP 8.5%, ~55M trips) are excluded from the
profiles — they cannot be mapped to a camera.

**Staleness: the trip data ends before the present.** Taxi ends
2026-11-01 (effectively 2026-10-31); TNP ends **2026-09-30** — six
weeks stale as of 2026-11-15, and the TNP (2025–) file was last
updated 2026-10-27, so a newer pull will not extend it until the
city posts October 2026. Demand profiles therefore cannot see the
holdout window's last ~6 weeks for TNP.

### 4.2 Method (worker B)

- **Camera → area:** point-in-polygon on the `igwz-8jzy`
  boundaries (shapely 2.1.2, new pinned dep). **361/365 cameras
  mapped** (59 of 77 areas hold cameras); the 4 coordinate-less
  cameras (1623, 2552, 2592, 2662) fall back to the **citywide**
  demand profile (area 0). Output:
  `data/hourly/processed/camera_community_area.csv`. Examples:
  1413 → area 6 LAKE VIEW; 2251 → area 56 GARFIELD RIDGE.
- **Demand profiles:** `data/hourly/processed/demand_profiles.parquet`
  — 5,616 rows = 78 areas (77 + citywide 0) × 3 day types × 24 h.
  Weights sum to 1 per (area, day type) (max |Σweight − 1| =
  2.22e-16). **Blend: summed taxi + TNP counts** (TNP dominates
  ~11× by volume, so the blend is effectively the rideshare shape
  with a small taxi tilt). **Window: 2023-01-01 → present**
  (659,173,028 trips in-window) — the COVID regime break (TNP
  ~2.2M trips/week Feb 2020 → ~0.62M/week Jun 2020) rules out a
  2019–present average. **Day types: weekday / weekend / holiday**
  (holiday takes precedence; `holidays.US`, same convention as
  `features.py`); day counts in-window: weekday 959, weekend 398,
  holiday 44.
- **Demand multiplier:** `demand_weight[h] / mean(demand_weight)` —
  mean exactly 1.0, so it only reshapes the day. Citywide weekday
  peak **18:00** (0.0669), trough 03:00 (0.0070). Multiplier ranges
  on 2026-10-15: 1413 0.23–1.82, 2251 0.11–1.48 — demand can nearly
  zero out small hours in low-overnight-demand areas.
- **Blend into `predict_hourly(..., demand=True)` (default ON):**
  profile path `daily × profile × weather × congestion × demand`;
  model path `daily × model_weights × demand`, renormalised.
  **The XGBoost model did NOT take demand as a feature** — demand
  is a post-hoc shape layer on the model path (a feature would need
  a retrain + re-validation). `demand=False` reproduces Phase 2b
  exactly. Result dict adds `demand`, `demand_day_type`,
  `community_area`; methods report as `profile+demand` /
  `model+demand`.

### 4.3 Validation — same protocol as §3 (worker C)

Script: `validate_hourly_demand.py`, results:
`hourly_validation_demand.json`. Same holdout (2026-08-01 →
2026-10-29, 779,760 camera-hours, 8,517 observed crashes), same
leakage-free pre-holdout profiles and rates, same XGBoost feature
construction as §3. **The Phase 2b baselines reproduce exactly:**
profile-only deviance 0.094549 (recorded 0.094549, abs diff 0.0),
XGBoost raw 0.093342 (recorded 0.093342, diff 0.0), XGBoost shape
0.093335 (recorded 0.093335, diff 0.0) — the protocol is identical,
so the demand deltas below are real, not a protocol artifact.

| Variant | Mean Poisson deviance | RMSE | MAE | Pred. total | Hourly-share Pearson r | Spearman ρ | Pred. peak hour |
|---|---|---|---|---|---|---|---|
| Profile-only (Phase 2b baseline) | 0.0945 | 0.1058 | 0.0210 | 8,110 | 0.991 | 0.986 | 15:00 |
| **Profile + demand (shipped)** | **0.0949** | 0.1058 | 0.0210 | 8,110 | 0.989 | 0.984 | 15:00 |
| Profile + weather + demand (shipped profile path) | 0.0950 | 0.1058 | 0.0210 | 8,110 | 0.989 | 0.984 | 15:00 |
| **XGBoost raw (Phase 2b baseline)** | **0.0933** | 0.1057 | 0.0215 | 8,504 | 0.988 | 0.984 | 16:00 |
| **XGBoost shape + demand (shipped)** | **0.0944** | 0.1057 | 0.0209 | 8,110 | 0.988 | 0.980 | 16:00 |

Actual total: 8,517. Supplementary sensitivity rows (demand profiles
rebuilt from trips truncated at 2026-07-31, §4.4): profile + demand
(pre-holdout) 0.0949, XGBoost shape + demand (pre-holdout) 0.0944 —
identical to the shipped rows to 4 dp.

**VERDICT — demand does NOT improve the holdout numbers. Ship it as
a documented experiment, not a claimed win.**

- Profile path: deviance **0.0945 → 0.0949 (+0.0004, +0.4%)** with
  demand. XGBoost path: **0.0933 → 0.0944 (+0.0011, +1.2%)** —
  demand erases most of the XGBoost model's win over the profile
  (0.0933 vs 0.0945 without demand; 0.0944 vs 0.0949 with it).
  RMSE is unchanged to 4 dp on both paths; MAE improves trivially
  (profile 0.0210 → 0.0210, XGBoost 0.0215 → 0.0209 — the raw-count
  MAE gap was a level effect, not shape). Share correlation is flat
  or slightly worse (profile r 0.991 → 0.989; XGBoost ρ 0.984 →
  0.980). The shipped profile path with weather + demand (0.0950)
  is the worst of the profile family.
- **Why it hurts — the peaks disagree.** Trip volume peaks at
  **18:00**; crashes peak at **15:00**. Demand's overnight trough
  is far deeper than crashes' (citywide weekday demand trough
  03:00 = 0.0070 vs crash share 0.0130). Blending demand in
  therefore over-sharpens the day: it fixes one calibration error
  and creates a bigger one (§4.3.1).
- **Peak hour:** citywide, the blended peak stays 15:00 on the
  profile path and 16:00 on the model path — demand does not move
  the *aggregate* peak. Per camera it can move the peak a long way
  (§4.5: 1413 moves 15:00 → 18:00, away from the observed 15:00
  crash peak).
- **Recommendation (for the coordinator, not decided here):** the
  `demand=True` default in `predict_hourly` currently ships a
  shape that is worse on the holdout's primary metric on both
  paths. The honest options are (a) flip the default to
  `demand=False` and keep demand as an opt-in experiment
  (`method="model+demand"`), or (b) keep the default and accept a
  ~1% deviance cost for a shape that matches trip volume rather
  than crash exposure. No code was changed in this validation —
  the default is still ON as worker B left it.

#### 4.3.1 Per-hour calibration with demand

Predicted share vs observed crash share by hour (citywide,
holdout). Ratio > 1 = over-predicted, < 1 = under-predicted.
Compare §3.1 (no demand): profile 02:00 was 1.55×, 15:00 was
0.89×, 18:00 was 0.87×.

| Hour | Observed share | Profile+demand pred. share | Ratio | XGB+demand pred. share | Ratio |
|---|---|---|---|---|---|
| 00:00 | 0.0198 | 0.0125 | **0.63** | 0.0136 | **0.69** |
| 01:00 | 0.0151 | 0.0084 | **0.55** | 0.0091 | **0.60** |
| 02:00 | 0.0115 | 0.0059 | **0.51** | 0.0064 | **0.56** |
| 03:00 | 0.0130 | 0.0040 | **0.31** | 0.0040 | **0.31** |
| 04:00 | 0.0095 | 0.0042 | **0.44** | 0.0035 | **0.37** |
| 05:00 | 0.0155 | 0.0072 | **0.46** | 0.0056 | **0.36** |
| 06:00 | 0.0204 | 0.0158 | 0.77 | 0.0128 | **0.63** |
| 07:00 | 0.0453 | 0.0406 | 0.90 | 0.0399 | 0.88 |
| 08:00 | 0.0484 | 0.0580 | **1.20** | 0.0564 | **1.17** |
| 09:00 | 0.0424 | 0.0491 | 1.16 | 0.0506 | **1.19** |
| 10:00 | 0.0409 | 0.0464 | 1.14 | 0.0473 | 1.16 |
| 11:00 | 0.0527 | 0.0493 | 0.93 | 0.0497 | 0.94 |
| 12:00 | 0.0567 | 0.0578 | 1.02 | 0.0587 | 1.04 |
| 13:00 | 0.0576 | 0.0632 | 1.10 | 0.0668 | 1.16 |
| 14:00 | 0.0700 | 0.0732 | 1.05 | 0.0785 | 1.12 |
| 15:00 | 0.0867 | 0.0887 | 1.02 | 0.0882 | 1.02 |
| 16:00 | 0.0796 | 0.0882 | 1.11 | 0.0893 | 1.12 |
| 17:00 | 0.0808 | 0.0879 | 1.09 | 0.0883 | 1.09 |
| 18:00 | 0.0683 | 0.0710 | 1.04 | 0.0687 | 1.01 |
| 19:00 | 0.0436 | 0.0510 | 1.17 | 0.0489 | 1.12 |
| 20:00 | 0.0377 | 0.0365 | 0.97 | 0.0351 | 0.93 |
| 21:00 | 0.0344 | 0.0325 | 0.95 | 0.0321 | 0.93 |
| 22:00 | 0.0249 | 0.0282 | 1.13 | 0.0267 | 1.07 |
| 23:00 | 0.0251 | 0.0203 | 0.81 | 0.0196 | 0.78 |

Demand trades one miscalibration for another: the afternoon peak
is now right (15:00 ratio 0.89 → 1.02, 18:00 0.87 → 1.04 on the
profile path), but **overnight 00:00–06:00 collapses to 0.31–0.77×**
— far worse than the no-demand overnight over-prediction it
replaces (02:00 was 1.55×, now 0.51×). Trips nearly vanish
overnight; crashes do not (only 81–169 crashes per overnight hour
in 90 days, but not ~zero). The morning (08:00–10:00) is also
pushed up to 1.14–1.20×. Daytime 11:00–23:00 stays mostly within
±0.17.

### 4.4 Demand-vs-crash correlation — is demand a good proxy at all?

Direct evidence, independent of any model: Pearson r between the
demand weight (share of trips in that hour) and the observed
crash-hour share.

- **Citywide (holdout day-type mix: 62 weekday / 26 weekend /
  2 holiday days): r = 0.783, Spearman ρ = 0.760.** Demand peaks
  18:00; crashes peak 15:00. By day type: weekday r = 0.800,
  weekend r = 0.719, holiday r = 0.749 (2 days only). Compare the
  profile shape's own share correlation: r = 0.991 (§3) — the
  crash-derived shape tracks crash hours far better than the
  trip-derived shape does, which is expected (same signal family)
  but sets the bar demand fails to clear.
- **Per community area (56 areas with ≥20 holdout crashes):
  median r = 0.535, mean 0.515, range 0.130–0.816; only 1 area
  above 0.8, 23 of 56 below 0.5; pooled (area, hour) cells
  r = 0.468.** Demand is a weak-to-moderate proxy at area level,
  not a strong one. Best areas: 13 (r = 0.816, 6 cameras),
  51 (0.798), 16 (0.798, 12 cameras), 19 (0.777, 16 cameras),
  44 (0.773). Worst areas: 31 LOWER WEST SIDE (r = 0.130, 4
  cameras, 69 crashes), 57 (0.174), 22 (0.219, 13 cameras,
  292 crashes), 26 (0.243), 35 (0.268), 27 (0.275).
- **Low-volume areas do not explain the loss.** Split at the
  bottom quartile of weekday trips/day (≤ 609 trips/day, 20 areas,
  33 cameras, 777 holdout crashes): profile deviance 0.0921 →
  0.0919 with demand (tiny *improvement*), but XGBoost 0.0913 →
  0.0918 with demand (worse). In the other 328 cameras demand
  hurts on both paths (profile 0.0948 → 0.0952, XGBoost 0.0936 →
  0.0946). So the noisy low-volume profiles Worker B flagged are
  not the driver — demand hurts broadly, including in
  high-volume areas. (Shrinking low-volume profiles toward
  citywide, as Worker B suggested, is therefore not expected to
  flip the verdict; the 18:00-vs-15:00 peak mismatch is citywide.)

**Leakage check (demand profiles vs holdout trips).** The shipped
demand profiles (2023 → present) include trips from inside the
holdout window — shape information from the holdout period, though
*not* leakage of the target (trips are not crashes). Rebuilding
the profiles from trips truncated at 2026-07-31 changes the
weights almost not at all (mean |Δweight| 0.0001, max 0.0055 over
234 (area, day-type) cells) and the validation numbers not at all
(0.0949 / 0.0944 either way, to 4 dp). The verdict does not depend
on that overlap.

### 4.5 Worked example — Thursday 2026-10-15, with demand

Script: `worked_example_demand.py`, results:
`worked_example_demand.json`. Same date and cameras as §5
(Phase 2b worked example). Daily predictions reproduce Phase 2
exactly with and without demand (asserted): **1413: 53.01, 2251:
46.82** — demand never changes the daily total. Reconciliation
error is **exactly 0.00e+00** in all four (camera × demand)
combinations on both methods.

**LAKE SHORE DR AND BELMONT — camera 1413** (daily 53.01,
model path; area 6 demand multipliers 0.23–1.82, peak 18:00,
trough 03:00):

| Hour | No demand | With demand | Δ | Band off → on | Hour | No demand | With demand | Δ | Band off → on |
|---|---|---|---|---|---|---|---|---|---|
| 00:00 | 0.61 | 0.37 | −0.24 | LOW → LOW | 12:00 | 3.07 | 2.36 | −0.71 | LOW → LOW |
| 01:00 | 0.51 | 0.22 | −0.29 | LOW → LOW | 13:00 | 3.22 | 2.40 | −0.82 | LOW → LOW |
| 02:00 | 0.41 | 0.12 | −0.28 | LOW → LOW | 14:00 | 3.63 | 2.81 | −0.82 | LOW → LOW |
| 03:00 | 0.29 | 0.06 | −0.23 | LOW → LOW | **15:00** | **4.74** | **4.06** | **−0.68** | MEDIUM → MEDIUM |
| 04:00 | 0.30 | 0.07 | −0.23 | LOW → LOW | 16:00 | 4.04 | 4.07 | +0.03 | MEDIUM → MEDIUM |
| 05:00 | 0.55 | 0.19 | −0.36 | LOW → LOW | 17:00 | 4.28 | 5.36 | +1.08 | MEDIUM → MEDIUM |
| 06:00 | 0.90 | 0.51 | −0.39 | LOW → LOW | **18:00** | **3.82** | **6.08** | **+2.26** | LOW → **MEDIUM** |
| 07:00 | 2.58 | 2.08 | −0.49 | LOW → LOW | 19:00 | 2.56 | 3.94 | +1.38 | LOW → LOW |
| 08:00 | 3.22 | 2.87 | −0.35 | LOW → LOW | 20:00 | 2.33 | 2.94 | +0.61 | LOW → LOW |
| 09:00 | 2.66 | 2.38 | −0.27 | LOW → LOW | 21:00 | 1.93 | 2.77 | +0.84 | LOW → LOW |
| 10:00 | 2.51 | 2.05 | −0.45 | LOW → LOW | 22:00 | 1.23 | 1.83 | +0.60 | LOW → LOW |
| 11:00 | 2.55 | 2.03 | −0.53 | LOW → LOW | 23:00 | 1.08 | 1.44 | +0.35 | LOW → LOW |

Sum: 53.01 both ways. Demand moves 1413's peak from **15:00
(4.74) to 18:00 (6.08, +2.26)** — away from the observed
citywide crash peak (15:00) and toward the trip peak (18:00).
Overnight hours are cut to roughly a fifth of their no-demand
values (03:00: 0.29 → 0.06). On the profile path the same move
happens (peak 17:00, 4.34 → 18:00, 6.14; max |Δ| 2.24).

**CICERO AND I-55 — camera 2251** (daily 46.82, model path;
area 56 demand multipliers 0.11–1.48, peak 18:00, trough 04:00)
— top 6 hours with demand:

| Hour | With demand | Band | No demand | Band | Δ |
|---|---|---|---|---|---|
| **15:00** | **4.23** | **MEDIUM** | 3.94 | LOW | +0.29 |
| 18:00 | 4.04 | MEDIUM | 3.29 | LOW | +0.75 |
| 16:00 | 3.97 | LOW | 3.63 | LOW | +0.34 |
| 17:00 | 3.93 | LOW | 3.23 | LOW | +0.70 |
| 14:00 | 3.60 | LOW | 3.19 | LOW | +0.42 |
| 13:00 | 3.36 | LOW | 2.78 | LOW | +0.59 |

2251's peak stays at 15:00 but rises 3.94 → 4.23, crossing the
LOW/MEDIUM line (max |Δ| 0.88). On the profile path it also stays
at 15:00 (3.51 → 3.99, still LOW; max |Δ| 1.55).

### 4.6 Staleness / drift (worker B + worker C check)

- Year-on-year demand-profile drift (Worker B,
  `data/hourly/demand_staleness.json`): 2023 vs 2024 mean |Δw| =
  0.0016, max ~0.03; 2024 vs 2025 the same (mean 0.0016, max
  0.0320); across the COVID break (2019 vs 2023) mean 0.0037.
  The max drift (~0.03) persists between adjacent post-COVID
  years and is always in low-volume outlying areas (Oakland,
  Hegewisch, Burnside, Edison Park, Mount Greenwood) — small-sample
  noise at a few hundred trips/weekend day, not regime change.
  That supports the 2023–present window, and §4.4 shows the
  low-volume noise is not what loses the validation.
- Shipped vs pre-holdout demand profiles (§4.4): mean |Δw| =
  0.0001, max 0.0055 — the holdout-period trips in the shipped
  profiles move almost nothing.

### 4.7 Honest limits — demand-specific (adds to §6)

- **Demand does not beat the baseline on the holdout** (§4.3):
  deviance worse on both paths. It is a documented experiment,
  not a validated improvement. The `demand=True` default should
  be reconsidered (§4.3).
- **Trip peak ≠ crash peak.** Demand peaks 18:00 citywide;
  crashes peak 15:00. Any demand-blended shape inherits the
  18:00 peak — per camera it can move the reported peak hour
  three hours late (1413: 15:00 → 18:00 on 2026-10-15).
- **Overnight is now under-predicted, badly.** Demand cuts
  00:00–06:00 to 0.31–0.77× of observed crash share (§4.3.1),
  the mirror image of the Phase 2b overnight over-prediction.
  Overnight point values with demand on are the least reliable
  hours in the product.
- **Demand is a weak-to-moderate proxy at area level**
  (median per-area r = 0.535, pooled r = 0.468; §4.4) — it tracks
  the *shape* of traffic volume, not of crash exposure, and
  crash exposure is what the hourly shape is validated against.
- **Demand data ends Sep/Oct 2026** (TNP 2026-09-30, taxi
  2026-11-01; §4.1). Profiles are static shapes, so they do not
  go stale overnight, but they cannot reflect any post-September
  change in trip patterns, and a refresh needs a new pull.
- **XGBoost never saw demand as a feature.** The model path
  applies demand post-hoc; a demand feature would need a retrain
  and a re-validation, and given §4.3 there is no evidence that
  would help — the peak-mismatch is in the signal, not the
  fitting.

## 5. Worked example — Thursday 2026-10-15

The Phase 2 worked-example date, broken into 24 hours with
`predict_hourly` (default method: the shipped XGBoost model;
profile-method peaks shown for comparison). Script:
`worked_example_hourly.py`, results:
`worked_example_hourly.json`.

Daily predictions reproduce Phase 2 exactly (asserted in the
script, not eyeballed): 1413: 53.01, 1412: 4.61, 2251: 46.82,
2252: 8.15 (and 1514: 22.57 via the daily predict path). The 24
hourly values sum to the daily prediction with reconciliation
error **exactly 0.00e+00** for all four cameras on both methods
(`predict_hourly` places the float residual on the peak hour).

**Weather on the day:** 9.2 mm of rain in total (heaviest
18:00, 4.7 mm; rain 11:00–19:00 and 22:00–23:00), no snow,
15.1–19.1 °C, wind max 23.4 km/h. Weather multipliers 1.000–
1.147; 10 of 24 hours above 1.0. **Congestion multipliers are
1.0 for all 24 hours × all 4 cameras** (dead feed) — weather is
the only hour-varying multiplier besides the profile/model
shape itself.

**LAKE SHORE DR AND BELMONT — camera 1413** (daily 53.01
predicted, 50 actual; model method, peak 15:00, 4.74 MEDIUM):

| Hour | Predicted | Band | Score | Hour | Predicted | Band | Score |
|---|---|---|---|---|---|---|---|
| 00:00 | 0.61 | LOW | 0.017 | 12:00 | 3.07 | LOW | 0.088 |
| 01:00 | 0.51 | LOW | 0.015 | 13:00 | 3.22 | LOW | 0.092 |
| 02:00 | 0.41 | LOW | 0.012 | 14:00 | 3.63 | LOW | 0.104 |
| 03:00 | 0.29 | LOW | 0.008 | **15:00** | **4.74** | **MEDIUM** | **0.136** |
| 04:00 | 0.30 | LOW | 0.009 | 16:00 | 4.04 | MEDIUM | 0.115 |
| 05:00 | 0.55 | LOW | 0.016 | 17:00 | 4.28 | MEDIUM | 0.122 |
| 06:00 | 0.90 | LOW | 0.026 | 18:00 | 3.82 | LOW | 0.109 |
| 07:00 | 2.58 | LOW | 0.074 | 19:00 | 2.56 | LOW | 0.073 |
| 08:00 | 3.22 | LOW | 0.092 | 20:00 | 2.33 | LOW | 0.067 |
| 09:00 | 2.66 | LOW | 0.076 | 21:00 | 1.93 | LOW | 0.055 |
| 10:00 | 2.51 | LOW | 0.072 | 22:00 | 1.23 | LOW | 0.035 |
| 11:00 | 2.55 | LOW | 0.073 | 23:00 | 1.08 | LOW | 0.031 |

Sum: 53.01. Only 15:00–17:00 reach MEDIUM; nothing reaches
HIGH — even the hottest camera in the city tops out at 4.74
predicted violations in its peak hour. The profile method peaks
later for this camera (17:00, 4.34): 1413's raw profile peaks
at **08:00** (weight 0.0841, a morning-peak commuter route), but
the afternoon rain (multipliers 1.10–1.15 from 11:00) shifts
the shaped profile peak to the evening.

**LAKE SHORE DR AND BELMONT — camera 1412** (daily 4.61
predicted, 7 actual; model method, peak 15:00, 0.42 LOW):

| Hour | Predicted | Band | Hour | Predicted | Band |
|---|---|---|---|---|---|
| 00:00 | 0.05 | LOW | 12:00 | 0.28 | LOW |
| 01:00 | 0.04 | LOW | 13:00 | 0.29 | LOW |
| 02:00 | 0.03 | LOW | 14:00 | 0.33 | LOW |
| 03:00 | 0.02 | LOW | **15:00** | **0.42** | **LOW** |
| 04:00 | 0.02 | LOW | 16:00 | 0.36 | LOW |
| 05:00 | 0.05 | LOW | 17:00 | 0.39 | LOW |
| 06:00 | 0.07 | LOW | 18:00 | 0.31 | LOW |
| 07:00 | 0.22 | LOW | 19:00 | 0.23 | LOW |
| 08:00 | 0.27 | LOW | 20:00 | 0.19 | LOW |
| 09:00 | 0.23 | LOW | 21:00 | 0.16 | LOW |
| 10:00 | 0.22 | LOW | 22:00 | 0.11 | LOW |
| 11:00 | 0.23 | LOW | 23:00 | 0.09 | LOW |

Sum: 4.61. Every hour LOW — a quiet camera stays quiet all
day. (Profile method peaks 17:00, 0.37.)

**CICERO AND I-55 — peak hours** (model method):

| Camera | Daily pred. (actual) | 15:00 | 16:00 | 17:00 | 18:00 | 14:00 | 13:00 |
|---|---|---|---|---|---|---|---|
| 2251 | 46.82 (39) | **3.94** | 3.63 | 3.23 | 3.29 | 3.19 | 2.78 |
| 2252 | 8.15 (12) | **0.68** | 0.63 | 0.56 | 0.57 | 0.55 | 0.48 |

All hours LOW for both cameras — note that 2251's peak hour
(3.94) is still LOW even though its day is HIGH (46.82): the
hourly band is not the daily band, and must not be read as one.
(Profile method: 2251 peaks 15:00 at 3.51, 2252 peaks 15:00 at
0.61.)

Intersection totals for the day: Lake Shore Dr & Belmont
57.62 predicted vs 57 actual; Cicero & I-55 54.97 vs 51 —
both land close, as in Phase 2. Hourly actuals do not exist
for any of these numbers; the hourly split is an estimate
built on the crash proxy.

## 6. Honest limits — read before using the hourly numbers

- **Hourly numbers are ESTIMATES from proxies, never observed
  hourly violations.** The daily violation data has no
  hour-of-day. The shape is validated against hourly *crash*
  counts near cameras (§3), and the transfer from crash shape
  to violation shape is an assumption, stated, not a
  measurement.
- **No congestion after 2026-04-30.** Profiles embed
  training-period congestion patterns (stable shapes), but no
  hour-specific congestion signal exists for any date after
  that — including the entire Phase 2 holdout, where the shape
  comes from profiles + weather only. A live feed (TomTom/HERE,
  keys required) would be needed to restore it.
- **The model predicts crashes, not violations.** Its win is on
  a proxy target (§2.4, §3). It is a better hourly *shape* for
  crash exposure, applied to violation totals.
- **Both wins are narrow.** XGBoost beats the profile baseline
  by 1.5% deviance in-window and by ~1.3% on the unseen
  holdout; the profile fallback is nearly as good, and the
  citywide shape is nearly as good again on deviance. If the
  model ever misbehaves, the profile path is one argument away
  (`method="profile"`).
- **Overnight is over-predicted, the peak is under-predicted**
  (§3.1): 02:00 at 1.55× (profile) / 1.27× (model), 18:00 at
  0.87× / 0.88×. Treat overnight and peak-hour point values as
  the least reliable hours.
- **Per-camera profiles do not beat the citywide shape on
  holdout deviance** (§3) — per-camera hour shares carry
  sampling noise. Their value is the aggregate shape and
  per-camera peak hours.
- **Trip demand does not beat the baseline either — and it is on
  by default.** Phase 2c (§4): profile + demand 0.0949 vs 0.0945
  profile-only; XGBoost + demand 0.0944 vs 0.0933. Demand peaks
  18:00 while crashes peak 15:00, and it under-predicts overnight
  to 0.31× at 03:00. See §4.3 for the verdict and §4.7 for the
  demand-specific limits; whether the `demand=True` default
  should be flipped is an open decision, not yet taken.
- **Weather multipliers are assumed planning values**,
  calibrated only to the wet-vs-dry crash ratio (~1.15×). They
  showed no measurable gain on the holdout (§3) and are not
  measured effects on violations. The SDD's 1.3× / 1.5× figures
  were not used.
- **The shipped profiles were built on the full crash window
  (through 2026-10-29)**, so they partly reflect holdout
  crashes (drift vs leakage-free profiles: mean |Δweight|
  0.0032, max 0.0489). The validation in §3 uses leakage-free
  profiles; the shipped numbers will differ from it slightly.
- **Phase 2d (2026-11-16) changed the shipped hourly model and
  the profile path — see §9.** The hourly model is now the B12
  retrain (same 12 features, trained through 2026-07-31; shape
  0.093260 vs 0.093335), and the profile path blends in trip
  slowness by default (profile + weather + slowness 0.094390 vs
  0.094561). Both wins are small; B12 was selected on the same
  holdout it is validated on; the demand layer is still on by
  default and still loses on every variant tested (§9.6 has the
  recommendation to flip it off).
- **4 cameras (1623, 2552, 2592, 2662) have no coordinates**
  and get the citywide profile; their map placement is
  meaningless (same caveat as Phase 2).
- **Timestamps are naive hourly labels** in a flat 24/day grid;
  the 02:00 row on spring-forward days (2025-03-09, 2026-03-08)
  is not a real local hour. All sources share the grid, so joins
  are consistent, but do not treat that label as true local time.
- **Daily lag:** the daily model still inherits Phase 2's
  ~14-day data lag; the hourly layer adds no new staleness,
  but it cannot remove it either.

## 7. Files

| File | What it is |
|---|---|
| `hourly_model.py` | Profiles, multipliers, Poisson/XGB training + evaluation, `model_weights()` |
| `hourly_risk.py` | Clean API: `daily_prediction()`, `predict_hourly()`, `risk_band()` |
| `validate_hourly.py` | **Independent validation (worker C)** — leakage-free holdout check (§3) |
| `hourly_validation.json` | Validation numbers (§3) in machine-readable form |
| `worked_example_hourly.py` | Worked example (§5) with asserted reconciliation |
| `worked_example_hourly.json` | Worked-example numbers (§5) in machine-readable form |
| `validate_hourly_demand.py` | **Phase 2c validation (worker C)** — same holdout protocol as §3 + demand (§4.3) |
| `hourly_validation_demand.json` | Phase 2c validation numbers (§4.3–4.4) in machine-readable form |
| `worked_example_demand.py` | Phase 2c worked example (§4.5) with asserted reconciliation |
| `worked_example_demand.json` | Phase 2c worked-example numbers (§4.5) in machine-readable form |
| `build_demand.py` | Phase 2c: camera→area mapping, demand profiles, staleness (§4.2, §4.6) |
| `smoke_test_demand.py` | Phase 2c smoke test (demand on/off, unmapped camera, holiday) |
| `data/hourly/processed/demand_profiles.parquet` | 78 areas × 3 day types × 24 h demand weights (§4.2) |
| `data/hourly/processed/camera_community_area.csv` | 365 cameras → community area (§4.2) |
| `data/hourly/processed/trip_hourly_by_area.parquet` | 8,279,951 rows, 674.6M trips 2019→2026 (§4.1) |
| `data/hourly/TRIP_SOURCES_VERIFIED.md` | Trip source verification (worker A, §4.1) |
| `data/hourly/TRIP_QUERIES.md` | Exact SoQL queries for the trip pulls |
| `data/hourly/DEMAND_BUILD.md` | Phase 2c build note (worker B) |
| `data/hourly/demand_staleness.json` | Year-on-year demand-profile drift (§4.6) |
| `data/hourly/processed/hourly_profiles.parquet` | 365 × 24 profile weights + provenance flags |
| `hourly_model.joblib` (1.4 MB) | **Shipped model — XGBoost count:poisson** (Phase 2d B12 retrain, §9; worker-B model preserved as `hourly_model_v2b.joblib`) |
| `hourly_model_metrics.json` | Worker B's comparison numbers, winner, importances, params |
| `hourly_model_2d_B12.joblib` | B12 candidate file (byte-identical to the shipped `hourly_model.joblib`) |
| `hourly_model_2d_A.joblib` / `_B.joblib` / `_A-demand-only.joblib` / `_A-slowness-only.joblib` | Phase 2d retrain candidates that did NOT ship (§9.3, §9.5) |
| `hourly_model_2d_metrics.json` | Phase 2d retrain + blend numbers (§9.3) |
| `validate_hourly_2d.py` / `hourly_validation_2d.json` | **Phase 2d validation (worker 3)** — slowness blends + combined slowness+demand (§9.3) |
| `train_hourly_2d.py` | **Phase 2d retrain** — demand/slowness features, variants A/B/B12/ablations (§9.2–9.3) |
| `score_B12_blends.py` | Phase 2d best-combination check — B12/B + post-hoc slowness (§9.3) |
| `worked_example_2d.py` / `worked_example_2d.json` | Phase 2d worked example, winning configuration (§9.4) |
| `data/hourly/processed/speed_profiles.parquet` | 78 areas × 3 day types × 24 h mean trip speeds, 2023-01-01 → 2026-07-31 (§9.1) |
| `data/hourly/SPEED_FINDINGS.md` | Trip-speed findings (Worker 1, §9.1) |
| `data/hourly/TRIP_SPEED_QUERIES.md` | Exact SoQL queries for the speed pulls |
| `data/hourly/ADAPTIVE_FINDINGS.md` | Adaptive-trust / daily-deviation / lag findings (Worker 2, §9.2) |
| `hourly_adaptive.py` / `validate_hourly_adaptive.py` / `hourly_validation_adaptive.json` | Worker 2 demand-refinement code + numbers (§9.2) |
| `data/hourly/SOURCES_VERIFIED.md` | Source verification (worker A) |
| `data/hourly/HOURLY_MODEL_SUMMARY.md` | Build detail (worker B) |
| `HOURLY_REPORT.md` | This file |

Reproduce the hourly pipeline:

```bash
cd ~/workspace/redunator
~/workspace/redunator-venv/bin/python hourly_ingest.py --steps segments,map,congestion,region,crashes,aggregate,weather,process-congestion,verify
~/workspace/redunator-venv/bin/python hourly_model.py --build-profiles
~/workspace/redunator-venv/bin/python hourly_model.py --train-poisson --alphas 0.1,1.0,10.0 --xgb-poisson
~/workspace/redunator-venv/bin/python validate_hourly.py        # independent holdout check (§3)
~/workspace/redunator-venv/bin/python worked_example_hourly.py  # worked example (§5)
~/workspace/redunator-venv/bin/python validate_hourly_demand.py   # Phase 2c validation (§4.3)
~/workspace/redunator-venv/bin/python worked_example_demand.py    # Phase 2c worked example (§4.5)
~/workspace/redunator-venv/bin/python validate_hourly_2d.py       # Phase 2d validation: slowness blends (§9.3)
~/workspace/redunator-venv/bin/python train_hourly_2d.py --variants A,B,B12,A-demand-only,A-slowness-only  # Phase 2d retrains (§9.3)
~/workspace/redunator-venv/bin/python score_B12_blends.py         # Phase 2d best-combination check (§9.3)
~/workspace/redunator-venv/bin/python worked_example_2d.py        # Phase 2d worked example, winning config (§9.4)
~/workspace/redunator-venv/bin/python smoke_test_demand.py        # smoke test (updated for 2d method strings)
~/workspace/redunator-venv/bin/python hourly_risk.py 1413 2026-10-15
```

## 8. Integrity — Phase 2 artifacts untouched

The daily model files are byte-identical to the Phase 2 baseline
(verified 2026-11-15 by worker C, md5): `redunator_brain_v1.joblib`
`9162dcbe76bf842584bd6f208c8bcb99`, `redunator_brain_v1.onnx`
`90b9160389dd76d945bc8428a9ec3bf0`, `redunator_brain_v1.json`
`28c83534e67267e272108c582ed0c56b`. Daily holdout metrics still
read RF RMSE 3.3068 / MAE 2.1452
(`data/processed/model_metrics.json`), and the daily worked-example
predictions still reproduce via the predict path (1413: 53.01,
2251: 46.82, 1514: 22.57 on 2026-10-15; §5). `train.py`,
`features.py`, and everything under `data/hourly/raw/` and
`data/hourly/processed/` were not modified by the Phase 2b
validation. Phase 2c re-verified the same md5s at the end of its
work (2026-11-15, worker C): joblib
`9162dcbe76bf842584bd6f208c8bcb99`, onnx
`90b9160389dd76d945bc8428a9ec3bf0`, json
`28c83534e67267e272108c582ed0c56b` — unchanged. Daily metrics still
reproduce from `data/processed/model_metrics.json` (RF RMSE 3.3068 /
MAE 2.1452), and the 2026-10-15 daily predictions still reconcile
with demand on and off (1413: 53.01, 2251: 46.82; reconciliation
error exactly 0.00e+00; §4.5). Phase 2c added files only
(`validate_hourly_demand.py`, `hourly_validation_demand.json`,
`worked_example_demand.py`, `worked_example_demand.json`) and
edited `HOURLY_REPORT.md` / `README.md` docs; it modified no model
file and no worker A/B artifact.

Phase 2d re-verified the same daily md5s at the end of its work
(2026-11-16, worker 3): joblib
`9162dcbe76bf842584bd6f208c8bcb99`, onnx
`90b9160389dd76d945bc8428a9ec3bf0`, json
`28c83534e67267e272108c582ed0c56b` — unchanged. Daily metrics still
read RF RMSE 3.3068 / MAE 2.1452, and the 2026-10-15 daily
predictions still reproduce via the predict path (1413: 53.01,
2251: 46.82; §9.4). Phase 2d **did** change hourly artifacts, and
only these: `hourly_model.joblib` was replaced by the B12 retrain
(see §9.6; the worker-B model is preserved byte-identical as
`hourly_model_v2b.joblib`, md5
`b6aa33fdc97dafec12b58ecc089072a3`), `hourly_model.py` gained the
speed-profile helpers + `SHIPPED_MODEL_RATE_END`, `hourly_risk.py`
gained the profile-path slowness layer, and `smoke_test_demand.py`
had one method-string assertion updated
(`profile+demand` → `profile+demand+slowness`). Phase 2d added
files only otherwise (listed in §7). **Reproducibility note:**
`validate_hourly.py` and `validate_hourly_demand.py` load
`hourly_model.joblib` for their XGBoost rows, so re-running them
now scores the B12 model (raw 0.093203 / shape 0.093260), not the
worker-B model their recorded reproduction checks expect (raw
0.093342 / shape 0.093335). The §3 / §4.3 numbers are preserved
in `hourly_validation.json` / `hourly_validation_demand.json`;
to re-run those scripts against the worker-B model, point them
at `hourly_model_v2b.joblib` (temporarily restore it as
`hourly_model.joblib`, or edit the load path). `train.py`,
`features.py`, `redunator_brain_v1.*`, and everything under
`data/hourly/raw/` were not modified by Phase 2d.

## 9. Phase 2d — improved trip-demand layer (validation + worked example)

Built 2026-11-16 (Worker 1: trip speed; Worker 2: demand
refinements; Worker 3: build, retrain, validation, wiring). The
brief was to improve the trip-demand layer five ways — trip speed
as a congestion proxy, demand/speed as XGBoost features, per-area
adaptive trust, daily-deviation nudges, and demand lags — and ship
only what beats the bar (XGBoost shape 0.093335 / raw 0.093342,
profile-only 0.094549).

**Headline: two things ship, both small wins — and neither is the
demand layer.** The profile path gains a trip-slowness blend
(0.094369 vs 0.094549 profile-only), and the hourly model is
retrained on the full pre-holdout window (B12: shape 0.093260 vs
0.093335). The demand layer itself loses on every variant tried
in 2c and 2d, including all three of Worker 2's refinements and
the combined slowness+demand blend. The retrain win comes from
the longer training window, **not** from the new demand/speed
features — the same-split retrain with those features loses, and
the full-window retrain *without* them beats the full-window
retrain *with* them.

### 9.1 Speed source (Worker 1)

Full note: [data/hourly/SPEED_FINDINGS.md](data/hourly/SPEED_FINDINGS.md);
exact queries: [data/hourly/TRIP_SPEED_QUERIES.md](data/hourly/TRIP_SPEED_QUERIES.md).

Speed is a server-side SoQL aggregate over valid trips only:
`mean speed = sum(trip_miles) / (sum(trip_seconds) / 3600)`,
valid = `trip_seconds > 0 AND trip_miles > 0 AND implied speed ≤ 80 mph`.
Zero-second, missing, and absurd-speed trips are counted separately
and excluded; the four classes partition all trips (residual 0 for
all four pulled datasets). Attribution is by **pickup community
area + trip start hour**. **342,719,755 valid trips** in the profile
window.

| Dataset | Range pulled | Valid trips |
|---|---|---|
| Taxi Trips (2013–2023) `wrvz-psew` | 2023-01-01 → 2024-01-01 | 5,803,639 |
| Taxi Trips (2024–) `ajtu-isnz` | 2024-01-01 → 2026-08-01 | 15,954,052 |
| TNP Trips (2023–2024) `n26f-ihde` | 2023-01-01 → 2025-01-01 | 174,339,152 |
| TNP Trips (2025–) `6dvr-xwnh` | 2025-01-01 → 2026-08-01 | 146,622,912 |

`m6dm-c72p` (TNP 2019–2022) was **not** pulled — it is outside the
profile window, so it cannot change the profiles; it would only
serve a staleness check.

**Profiles:** `data/hourly/processed/speed_profiles.parquet` —
5,616 rows = 78 areas (0 = citywide + 77) × 3 day types × 24 h,
window **2023-01-01 → 2026-07-31 (pre-holdout)**, 0 NaNs, speed
range 11.82–42.76 mph. Citywide (holdout day-type mix): slowest
**17:00 (17.51 mph)**, fastest **04:00 (34.44 mph)**; crashes peak
15:00, so the speed trough lags the crash peak by ~2 h — but speed
is already near-trough at 15:00 (18.58 mph). Taxi mean speed
19.42 mph vs TNP 22.28 mph (different trip mixes; the blend is
TNP-dominated ~11:1 by volume, as with demand). **October 2026
speed is taxi-only** (TNP `6dvr-xwnh` ends 2026-09-30) — this
affects only the daily/hourly speed *series* files, not the
profiles (which end 2026-07-31).

**Correlation (Worker 1, all-Chicago crashes):** slowness
(1/speed) vs crash share r = **+0.941** citywide (weekday 0.950,
weekend 0.958), per-area median **0.686** — vs demand 0.783 /
0.535 on the same comparisons. Best lag is contemporaneous (h);
h−1 and h+1 are both worse. §9.3 recomputes the same
correlations on camera-attributed crash shares (citywide
r = 0.949, per-area median 0.693 — the two denominators differ
slightly; the conclusion is the same).

### 9.2 Method

**Slowness blend (post-hoc shape layer).** For a camera-date,
`slow[h] = 1 / mean_speed_mph` for the camera's community area
and the date's day type; multiplier = `slow[h] / mean(slow)`
(mean exactly 1.0, same convention as the demand multiplier).
Strength α: multiplier**α (α = 1 full blend, α = 0.5
half-strength, a geometric shrink toward 1.0). Combined
slowness+demand = product of the two mean-1 multipliers.
Renormalised per camera-day, so the daily total never changes.
Holdout multiplier range: min 0.505, max 1.704, mean 1.0000
(demand, for comparison: min 0.081, max 3.499 — slowness is a
far gentler reshape). Unmapped cameras fall back to the citywide
(area 0) profile. Functions: `hourly_model.slowness_weights` /
`slowness_multipliers_for_day`; wired into `predict_hourly`
on the **profile path only** (see §9.6 for why not the model path).

**XGBoost retrain.** The 12 shipped features plus two new
per-(camera, hour) features, both from pre-holdout profiles only:
`demand_share` (trip-demand weight for the camera's area, the
date's day type, the hour; pre-holdout demand rebuild, trips
through 2026-07-31) and `trip_slowness` (1 / mean trip speed
for the same cell). Raw trip speed is NOT a third feature: it is
a monotone transform of slowness, so a tree gains no new split
from it. Hyperparameters identical to the shipped model
(`count:poisson`, 300 rounds, depth 6, lr 0.05, subsample /
colsample 0.8, `hist`, seed 42). Script: `train_hourly_2d.py`;
results: `hourly_model_2d_metrics.json`. Variants:

| Variant | Train window | Features | log_cam_rate through |
|---|---|---|---|
| **A** | 2025-01-01 → 2026-02-28 (same split as shipped) | 12 + demand + slowness | 2026-02-28 |
| **B** | 2025-01-01 → 2026-07-31 (full pre-holdout) | 12 + demand + slowness | 2026-07-31 |
| **B12** (control) | 2025-01-01 → 2026-07-31 | 12 shipped only | 2026-07-31 |
| **A-demand-only** | 2025-01-01 → 2026-02-28 | 12 + demand | 2026-02-28 |
| **A-slowness-only** | 2025-01-01 → 2026-02-28 | 12 + slowness | 2026-02-28 |

A isolates the feature effect (only difference from the shipped
model is the two new features). B is the model that would ship
if the features helped. B12 is the attribution control: if B12
wins by as much as B, the win is the longer training window,
not the features.

**Worker 2 — demand refinements (all DROPPED).** Full note:
[data/hourly/ADAPTIVE_FINDINGS.md](data/hourly/ADAPTIVE_FINDINGS.md);
code: `hourly_adaptive.py`, `validate_hourly_adaptive.py`,
results: `hourly_validation_adaptive.json`. (a) **Per-area
adaptive trust:** demand applied only in areas whose pre-holdout
demand–crash correlation clears a threshold. Best (r > 0.7,
34/59 areas, 239/361 cameras): profile **0.094826**,
XGB-shape **0.094056** — still loses to profile-only 0.094549 /
XGB 0.093335. Pre-holdout correlations are high almost
everywhere (median 0.748), so gating barely discriminates.
(b) **Daily-deviation nudge:** multiplier
`1 + α·(deviation − 1)` from that day's taxi shape vs its typical
shape. Deviance worsens monotonically with α in every source;
best (same-day, α = 0.25) **0.095130** vs profile-only 0.094549.
Same-day is post-hoc anyway (the day's trips are not available
at prediction time); the forecastable prev-day / last-week
versions are worse still. (c) **Demand lag:** lag +1 correlates
best (r = 0.874 vs 0.783 at lag 0, holdout) but deviance still
loses: profile **0.095128**, XGB-shape **0.094507**. A shifted
wrong shape is still wrong. These three were **not re-tested**
in §9.3 — Worker 2's numbers are cited here as the record.

### 9.3 Validation — same protocol as §3/§4.3

Scripts: `validate_hourly_2d.py` (post-hoc blends),
`train_hourly_2d.py` (retrains), `score_B12_blends.py`
(best-combination check). Results: `hourly_validation_2d.json`,
`hourly_model_2d_metrics.json`. Same holdout (2026-08-01 →
2026-10-29, 779,760 camera-hours, 8,517 observed crashes),
same leakage-free pre-holdout profiles and rates, same XGBoost
feature construction as §3. **Baselines reproduce exactly:**
profile-only 0.094549 (abs diff 0.0), XGBoost raw 0.093342
(0.0), XGBoost shape 0.093335 (0.0) — the protocol is
identical, so the deltas below are real, not artifacts.
(One scoring-precision note: `score_B12_blends.py` rebuilds
the shipped model's holdout features in float32, as
`build_poisson_design` does, while `validate_hourly.py` used
float64 intermediates for the speed ratio — its shipped-model
row reads raw 0.093343 / pred 8,478 vs the recorded 0.093342 /
8,504. That 1e-6 wobble does not affect any verdict: every
win/loss margin below is ≥ 0.000065 except the half-strength
ties, which are reported as ties.)

| Variant | Mean Poisson deviance | RMSE | MAE | Pred. total | Hourly-share Pearson r | Spearman ρ | Pred. peak hour |
|---|---|---|---|---|---|---|---|
| Profile-only (Phase 2b baseline) | 0.0945 | 0.1058 | 0.0210 | 8,110 | 0.991 | 0.986 | 15:00 |
| Profile + weather (shipped profile path) | 0.0946 | 0.1058 | 0.0210 | 8,110 | 0.991 | 0.986 | 15:00 |
| **Profile + slowness** | **0.0944** | **0.1058** | **0.0210** | 8,110 | 0.991 | 0.986 | 16:00 |
| Profile + slowness (half, α=0.5) | 0.0944 | 0.1058 | 0.0210 | 8,110 | 0.993 | 0.986 | 16:00 |
| **Profile + weather + slowness (wired profile path)** | **0.0944** | **0.1058** | **0.0210** | 8,110 | 0.990 | 0.986 | 16:00 |
| Profile + slowness + demand | 0.0955 | 0.1058 | 0.0209 | 8,110 | 0.989 | 0.983 | 16:00 |
| Profile + weather + slowness + demand | 0.0956 | 0.1058 | 0.0209 | 8,110 | 0.989 | 0.984 | 16:00 |
| **XGBoost raw (Phase 2b baseline)** | **0.0933** | **0.1057** | **0.0215** | **8,504** | 0.988 | 0.984 | 16:00 |
| **XGBoost shape (Phase 2b baseline)** | **0.0933** | **0.1057** | **0.0210** | 8,110 | 0.988 | 0.984 | 16:00 |
| XGBoost shape + slowness | 0.0935 | 0.1057 | 0.0210 | 8,110 | 0.990 | 0.983 | 16:00 |
| XGBoost shape + slowness (half) | 0.0933 | 0.1057 | 0.0210 | 8,110 | 0.990 | 0.984 | 16:00 |
| XGBoost shape + slowness + demand | 0.0951 | 0.1058 | 0.0209 | 8,110 | 0.988 | 0.977 | 16:00 |
| **Retrain A** (same split, +demand +slowness) raw / shape | 0.0934 / 0.0933 | 0.1057 / 0.1057 | 0.0214 / 0.0210 | 8,472 / 8,110 | — | — | 16:00 |
| **Retrain B** (full pre-holdout, +demand +slowness) raw / shape | 0.0932 / 0.0933 | 0.1057 / 0.1057 | 0.0214 / 0.0210 | 8,429 / 8,110 | — | — | 16:00 |
| **Retrain B12** (full pre-holdout, 12 feats) raw / shape — **SHIPS** | **0.0932 / 0.0933** | **0.1057 / 0.1057** | **0.0214 / 0.0210** | **8,432 / 8,110** | — | — | 16:00 |
| Retrain A-demand-only raw / shape | 0.0933 / 0.0933 | 0.1057 / 0.1057 | 0.0214 / 0.0210 | 8,495 / 8,110 | — | — | 16:00 |
| Retrain A-slowness-only raw / shape | 0.0934 / 0.0934 | 0.1057 / 0.1057 | 0.0214 / 0.0210 | 8,483 / 8,110 | — | — | 16:00 |
| B12 shape + slowness | 0.0934 | 0.1057 | 0.0210 | 8,110 | — | — | 16:00 |
| B12 shape + slowness (half) | 0.0933 | 0.1057 | 0.0210 | 8,110 | — | — | 16:00 |
| B shape + slowness / + slowness (half) | 0.0935 / 0.0933 | 0.1057 / 0.1057 | 0.0210 / 0.0210 | 8,110 | — | — | 16:00 |

Exact 6-dp numbers (deviance): profile-only 0.094549;
profile+weather 0.094561; profile+slowness 0.094369;
profile+slowness-half 0.094382; profile+weather+slowness
0.094390; profile+slowness+demand 0.095531;
profile+weather+slowness+demand 0.095564; XGB raw 0.093342;
XGB shape 0.093335; XGB+slowness 0.093480; XGB+slowness-half
0.093340; XGB+slowness+demand 0.095106; A raw 0.093363 /
shape 0.093345; B raw 0.093216 / shape 0.093270; **B12 raw
0.093203 / shape 0.093260**; A-demand-only raw 0.093328 /
shape 0.093329; A-slowness-only raw 0.093394 / shape 0.093352;
B12+slowness 0.093392; B12+slowness-half 0.093258; B+slowness
0.093454; B+slowness-half 0.093297. Actual total: 8,517.

**Reading the table honestly:**

- **Profile + slowness wins its bar** — 0.094369 vs 0.094549
  (−0.000180, −0.19%), with RMSE and MAE also improving
  (0.105753 / 0.020961 vs 0.105764 / 0.020982). The wired
  profile path (with weather) is 0.094390 vs 0.094561. The
  win is small but consistent: full, half-strength, and
  with-weather variants all beat their no-slowness
  counterparts. Share correlation is unchanged (r = 0.991).
- **Slowness does NOT help the model path.** XGBoost shape +
  slowness is 0.093480 vs 0.093335 (worse); half-strength is
  0.093340, a 0.000005 tie. B12 + slowness is 0.093392 (worse);
  B12 + slowness-half is 0.093258, a 0.000002 tie with B12
  alone. The model already contains an hour-of-day shape
  (hour_cos is its top feature) and a congestion speed ratio;
  a second, static slowness shape adds nothing it does not
  already have.
- **The combined slowness+demand blend is the worst of both
  worlds** — 0.095531 profile / 0.095106 XGB. Demand's
  overnight collapse (0.31× at 03:00, §4.3.1) dominates the
  combination even though slowness alone is gentle. The
  fallback Worker 1 suggested is **dropped**.
- **The new XGB features do not help.** On the identical
  split, adding demand + slowness makes the model *worse*
  (A shape 0.093345 vs 0.093335; in-window Mar–Apr 0.087974
  vs shipped 0.087929). The ablations split the blame:
  demand-only is a tie (shape 0.093329; in-window 0.087926,
  better than shipped by 0.000003 — noise), slowness-only
  loses (shape 0.093352; in-window 0.087976). The model
  leans on the new features anyway — `demand_share` is the
  3rd-most-important feature by gain in A (0.141) and B
  (0.133), `trip_slowness` 5th (0.053 / 0.047) — but leaning
  on them does not improve holdout deviance: both are static
  per-(area, day type, hour) shapes the trees can already
  approximate from hour + day-type + camera-rate features.
- **B12 wins the model bar — and the win is the training
  window, not the features.** B12 (12 shipped features,
  full pre-holdout train) is raw 0.093203 / shape 0.093260
  vs the shipped bar 0.093342 / 0.093335 (−0.15% / −0.08%),
  with RMSE and raw MAE also better and shape MAE tied
  (0.020969). B (same window, plus the two features) is
  *worse* than B12 on both (raw 0.093216, shape 0.093270).
  The extra five months of training data (Mar–Jul 2026,
  +1.33M camera-hours) and the fresher `log_cam_rate`
  (through 2026-07-31 instead of 2026-02-28) are what move
  the number. That is a legitimate, leakage-free improvement
  — but it is a training-window improvement, not a Phase 2d
  signal improvement, and it is stated as such so nobody
  credits the demand/speed features for it.

#### 9.3.1 Per-hour calibration with slowness

Predicted share vs observed crash share (citywide, holdout).
Compare §3.1 (no slowness): profile 02:00 was 1.56×, 15:00
was 0.89×, 18:00 was 0.87×.

| Hour | Observed share | Profile+slowness pred. share | Ratio | XGB+slowness pred. share | Ratio |
|---|---|---|---|---|---|
| 00:00 | 0.0198 | 0.0172 | 0.87 | 0.0158 | 0.80 |
| 01:00 | 0.0151 | 0.0147 | 0.97 | 0.0129 | 0.85 |
| 02:00 | 0.0115 | 0.0133 | 1.16 | 0.0114 | 0.99 |
| 03:00 | 0.0130 | 0.0097 | 0.75 | 0.0085 | 0.66 |
| 04:00 | 0.0095 | 0.0086 | 0.90 | 0.0072 | 0.75 |
| 05:00 | 0.0155 | 0.0106 | **0.69** | 0.0084 | **0.54** |
| 06:00 | 0.0204 | 0.0174 | 0.85 | 0.0143 | 0.70 |
| 07:00 | 0.0453 | 0.0409 | 0.90 | 0.0407 | 0.90 |
| 08:00 | 0.0484 | 0.0522 | 1.08 | 0.0503 | 1.04 |
| 09:00 | 0.0424 | 0.0415 | 0.98 | 0.0428 | 1.01 |
| 10:00 | 0.0409 | 0.0409 | 1.00 | 0.0421 | 1.03 |
| 11:00 | 0.0527 | 0.0462 | 0.88 | 0.0472 | 0.90 |
| 12:00 | 0.0567 | 0.0559 | 0.99 | 0.0575 | 1.01 |
| 13:00 | 0.0576 | 0.0614 | 1.07 | 0.0657 | 1.14 |
| 14:00 | 0.0700 | 0.0723 | 1.03 | 0.0785 | 1.12 |
| 15:00 | 0.0867 | 0.0905 | 1.04 | 0.0916 | 1.06 |
| 16:00 | 0.0796 | 0.0920 | 1.16 | 0.0948 | 1.19 |
| 17:00 | 0.0808 | 0.0890 | 1.10 | 0.0910 | 1.13 |
| 18:00 | 0.0683 | 0.0657 | 0.96 | 0.0647 | 0.95 |
| 19:00 | 0.0436 | 0.0466 | 1.07 | 0.0456 | 1.05 |
| 20:00 | 0.0377 | 0.0351 | 0.93 | 0.0340 | 0.90 |
| 21:00 | 0.0344 | 0.0305 | 0.89 | 0.0302 | 0.88 |
| 22:00 | 0.0249 | 0.0261 | 1.05 | 0.0246 | 0.99 |
| 23:00 | 0.0251 | 0.0215 | 0.85 | 0.0203 | 0.81 |

Slowness fixes the two §3.1 errors it was aimed at — the
overnight over-prediction (02:00 1.56× → 1.16× profile) and
the afternoon under-prediction (15:00 0.89× → 1.04×, 18:00
0.87× → 0.96×) — but it overshoots in the other direction
in the small hours: 05:00 falls to 0.69× (profile) / 0.54×
(XGB), 03:00 to 0.75× / 0.66×, 06:00 to 0.85× / 0.70×.
Trips are slow at 05:00–06:00 for reasons other than crash
exposure (early freight / airport runs at low speed), and a
pure slowness shape inherits that. The 16:00–17:00 hours are
now over-predicted (1.10–1.19×) because slowness peaks at
17:00 while crashes peak at 15:00. Net: calibration is better
at the hours that matter most (15:00, 18:00, 02:00) and
worse at 03:00–06:00, where absolute counts are small
(81–174 crashes in 90 days across all cameras). Peak hour:
**16:00 on every slowness variant** (share 0.0920 profile /
0.0948 XGB), vs observed 15:00 (0.0867) — slowness moves
the predicted peak one hour late, the same error the
shipped XGBoost already makes.

**Slowness-vs-crash correlation, recomputed on
camera-attributed shares (this validation):** citywide
r = 0.949, Spearman ρ = 0.951 (slowness peak 17:00 vs
observed 15:00); per area (56 areas with ≥20 holdout
crashes): median r = 0.693, mean 0.634, min 0.152 (area 57),
max 0.893 (area 28). Worker 1's all-Chicago figures were
0.941 / 0.686 — same conclusion on either denominator.

### 9.4 Worked example — Thursday 2026-10-15, winning configuration

Script: `worked_example_2d.py`, results:
`worked_example_2d.json`. Winning configuration: model path =
B12 retrained XGBoost, **demand OFF** (slowness never applies
on the model path); profile path = profile + weather +
slowness, **demand OFF**. Daily predictions reproduce Phase 2
exactly with the winning configuration (asserted): **1413:
53.01, 2251: 46.82** — neither layer changes the daily total.
Reconciliation error is **0.00e+00** in all four (camera ×
method) combinations in the recorded run. (One earlier run
showed −7.11e-15 — one float ulp — for 2251 on the profile
path; the daily total itself wobbles by 1 ulp between runs
from the Random Forest predict path, and the hourly split
always reconciles to that run's daily total exactly.)

**LAKE SHORE DR AND BELMONT — camera 1413** (daily 53.01;
slowness multipliers 0.60–1.47, peak 17:00, trough 04:00):

| Hour | Model (B12, winning) | Band | Profile+slowness (winning) | Band | Profile, no slowness | Band |
|---|---|---|---|---|---|---|
| 00:00 | 0.68 | LOW | 0.87 | LOW | 1.16 | LOW |
| 01:00 | 0.52 | LOW | 0.72 | LOW | 0.98 | LOW |
| 02:00 | 0.38 | LOW | 0.47 | LOW | 0.66 | LOW |
| 03:00 | 0.28 | LOW | 0.23 | LOW | 0.38 | LOW |
| 04:00 | 0.31 | LOW | 0.10 | LOW | 0.19 | LOW |
| 05:00 | 0.53 | LOW | 0.26 | LOW | 0.46 | LOW |
| 06:00 | 0.83 | LOW | 0.44 | LOW | 0.64 | LOW |
| 07:00 | 2.69 | LOW | 2.16 | LOW | 2.42 | LOW |
| 08:00 | 3.09 | LOW | 4.11 | MEDIUM | 4.21 | MEDIUM |
| 09:00 | 2.76 | LOW | 2.69 | LOW | 3.17 | LOW |
| 10:00 | 2.65 | LOW | 2.34 | LOW | 2.88 | LOW |
| 11:00 | 2.69 | LOW | 2.42 | LOW | 2.85 | LOW |
| 12:00 | 3.34 | LOW | 2.91 | LOW | 3.21 | LOW |
| 13:00 | 3.18 | LOW | 1.84 | LOW | 1.93 | LOW |
| 14:00 | 3.60 | LOW | 2.86 | LOW | 2.71 | LOW |
| **15:00** | **4.82** | **MEDIUM** | 3.48 | LOW | 2.96 | LOW |
| 16:00 | 4.54 | MEDIUM | 5.20 | MEDIUM | 4.03 | MEDIUM |
| **17:00** | 4.53 | MEDIUM | **5.74** | **MEDIUM** | 4.34 | MEDIUM |
| 18:00 | 2.51 | LOW | 4.68 | MEDIUM | 3.89 | LOW |
| 19:00 | 2.34 | LOW | 3.15 | LOW | 2.94 | LOW |
| 20:00 | 2.29 | LOW | 1.83 | LOW | 1.91 | LOW |
| 21:00 | 1.84 | LOW | 1.76 | LOW | 1.92 | LOW |
| 22:00 | 1.47 | LOW | 1.38 | LOW | 1.55 | LOW |
| 23:00 | 1.14 | LOW | 1.37 | LOW | 1.62 | LOW |

Sum: 53.01 on every column. **Peak comparison for 1413:**
2b no-demand (v2b model) 15:00 / 4.74 — right hour;
2c with-demand (v2b model) **18:00 / 6.08** — wrong hour,
+2.26 over no-demand; 2d winning model (B12, no demand)
**15:00 / 4.82** — right hour, +0.08 over the 2b number
(the retrain, not a shape layer); 2d winning profile
(+slowness) 17:00 / 5.74. Two honest notes: the profile
path's peak was already 17:00 without slowness (4.34) —
slowness raises the existing 17:00 peak rather than moving
it to 15:00 — and 5.74 is closer to demand's 6.08 than to
the model's 4.82, because the slowness shape also peaks
late (17:00). What slowness does *not* do is repeat
demand's error: it never reaches 18:00, and it cuts the
overnight hours far less (profile 03:00: 0.38 → 0.23 with
slowness, vs model 03:00: 0.29 → 0.06 with demand in §4.5).

**CICERO AND I-55 — camera 2251** (daily 46.82; slowness
multipliers 0.77–1.32, peak 07:00, trough 01:00) — top 6
hours, winning configuration:

| Hour | Model (B12, winning) | Band | Profile+slowness (winning) | Band | Profile, no slowness |
|---|---|---|---|---|---|
| 16:00 | **3.97** | LOW | 3.91 | LOW | 3.22 |
| **15:00** | 3.93 | LOW | **4.18** | **MEDIUM** | 3.51 |
| 17:00 | 3.55 | LOW | 3.33 | LOW | 2.90 |
| 18:00 | 3.09 | LOW | 2.65 | LOW | 2.39 |
| 14:00 | 2.96 | LOW | 2.41 | LOW | 2.15 |
| 08:00 | 2.79 | LOW | 3.44 | LOW | 2.92 |

2251's winning model peak is 16:00 / 3.97 (2b no-demand was
15:00 / 3.94 — the retrain moves this camera's peak one hour
late, as the aggregate does); its winning profile peak stays
at 15:00 but rises 3.51 → 4.18, crossing LOW/MEDIUM (max
|Δ| 0.69). Recorded comparisons: 2c with-demand (v2b model)
15:00 / 4.23; 2b no-demand (v2b model) 15:00 / 3.94.

### 9.5 Failed experiments — with numbers

| Experiment | Best number | Bar | Verdict |
|---|---|---|---|
| Demand, blanket (2c) | profile 0.0949 / XGB 0.0944 | 0.0945 / 0.0933 | DROPPED (§4.3) |
| Adaptive trust, r > 0.7 (W2) | profile 0.094826 / XGB-shape 0.094056 | 0.0945 / 0.0933 | DROPPED (§9.2) |
| Daily-deviation nudge, α = 0.25 same-day (W2) | profile 0.095130 | 0.0945 | DROPPED (§9.2) |
| Demand lag +1 (W2) | profile 0.095128 / XGB-shape 0.094507 | 0.0945 / 0.0933 | DROPPED (§9.2) |
| XGB + demand/slowness features, same split (A) | shape 0.093345 / raw 0.093363 | 0.0933 / 0.0933 | DROPPED |
| XGB + demand feature only (A-demand-only) | shape 0.093329 / raw 0.093328 | 0.0933 / 0.0933 | DROPPED (tie) |
| XGB + slowness feature only (A-slowness-only) | shape 0.093352 / raw 0.093394 | 0.0933 / 0.0933 | DROPPED |
| Post-hoc slowness on XGBoost shape | 0.093480 (B12: 0.093392) | 0.0933 | DROPPED |
| Post-hoc slowness (half) on XGBoost shape | 0.093340 (B12: 0.093258) | 0.0933 | DROPPED (tie) |
| Combined slowness+demand, profile / XGB | 0.095531 / 0.095106 | 0.0945 / 0.0933 | DROPPED |
| Full-window retrain WITH features (B) | shape 0.093270 / raw 0.093216 | 0.0933 / 0.0933 | SUPERSEDED by B12 (same window, no features, better on both) |

### 9.6 What shipped vs what was dropped — and the default recommendation

**SHIPPED:**

1. **Hourly model = B12 retrain.** `hourly_model.joblib` is now
   the B12 model (12 shipped features, train 2025-01-01 →
   2026-07-31; md5 `d530a9a10b6e7c782bf8f9e8c5f1ca17`).
   The worker-B model is preserved byte-identical as
   `hourly_model_v2b.joblib` (md5
   `b6aa33fdc97dafec12b58ecc089072a3`). `hourly_model.py`
   gained `SHIPPED_MODEL_RATE_END = "2026-07-31"`, which
   `model_weights()` uses for `log_cam_rate` so scoring
   matches training. Holdout: raw 0.093203, shape 0.093260.
   The win is small (−0.15% raw / −0.08% shape) and comes
   from the training window, not from any Phase 2d signal
   (§9.3) — it ships because it beats the bar on every
   reported metric, not because the 2d features earned it.
2. **Slowness layer on the profile path, default ON.**
   `predict_hourly(..., slowness=True)` (new parameter,
   default True) multiplies the profile path by the
   slowness multiplier; method labels gain `+slowness`
   (`profile+slowness`, `profile+demand+slowness`); the
   result reports `slowness_applied`. `slowness=False`
   reproduces the exact Phase 2b/2c profile output. The
   model path ignores the flag (`slowness_applied: False`).
   Wired profile path (with weather): 0.094390 vs 0.094561.

**DROPPED:** everything in §9.5. In particular the
`demand_share` / `trip_slowness` features are not in the
shipped model (B12 has the 12 shipped features only), the
combined slowness+demand blend is not wired anywhere, and
no adaptive-trust, deviation, or lag logic was added to
`hourly_risk.py`.

**DEFAULT RECOMMENDATION (for the coordinator / Gabriel —
no default was flipped in this work):**

- **Slowness: ON** (as wired). It wins its bar on deviance,
  RMSE, and MAE, and it improves calibration at the hours
  that matter most (15:00, 18:00, 02:00). It touches only
  the profile path; the default model path is unaffected.
- **Demand: recommend OFF — flip the `demand=True` default
  to `demand=False`.** The evidence is now two phases deep
  and unanimous: demand loses on the profile path (0.0949),
  the model path (0.0944), under adaptive trust (best
  0.0948 / 0.0941), under daily-deviation nudges (≥ 0.0951),
  at lag +1 (0.0951 / 0.0945), as an XGBoost feature (tie at
  best), and combined with slowness (0.0955 / 0.0951). It
  moves 1413's peak three hours late (15:00 → 18:00) and
  collapses overnight to 0.31× of observed crash share at
  03:00. The current default ships a shape that is worse on
  the primary metric on both paths. Demand stays available
  opt-in (`demand=True`, `method="model+demand"`). The flip
  itself is **not** made here — the task forbids it, and it
  is Gabriel's call.
- **Model: B12 ships as the model** (done, §9.6 item 1),
  with the caveats in §9.7: the margin is small, there is
  no in-window test for B12 (its training window includes
  Mar–Apr), and it was selected on the same holdout it is
  validated on. If Gabriel prefers zero selection risk, the
  fallback is the worker-B model (`hourly_model_v2b.joblib`,
  shape 0.093335) — one file copy away.

### 9.7 Honest limits — Phase 2d (adds to §6)

- **Both wins are small.** Profile+slowness −0.19%, B12
  −0.08% shape / −0.15% raw on mean Poisson deviance. The
  profile fallback is nearly as good without slowness, and
  the worker-B model is nearly as good as B12. Neither win
  should be read as a breakthrough; both are incremental
  shape/data improvements on a proxy target.
- **B12 was selected on the same holdout it is validated
  on.** Five retrain candidates (plus two blend variants)
  were compared on the 2026-08-01 → 2026-10-29 holdout and
  the best was shipped, so the reported B12 numbers are
  mildly optimistic by selection. There is no independent
  in-window test for B12 — its training window includes
  Mar–Apr 2026, so worker B's test split is inside its
  training data. The honest check would be a future holdout
  (e.g. Nov 2026 crashes, when the portal publishes them).
- **Peak hour is still off by one.** Every slowness variant
  and both shipped models predict a 16:00 citywide peak vs
  the observed 15:00. Slowness peaks at 17:00; it cannot
  fix a 15:00 peak, only soften the error around it.
- **Early morning is now under-predicted.** Profile+slowness
  puts 03:00–06:00 at 0.69–0.90× of observed crash share
  (05:00 worst at 0.69×; XGB+slowness 0.54×). Overnight
  point values with slowness on are the least reliable
  hours in the product — the mirror image of the Phase 2b
  over-prediction they replace, smaller in size.
- **Per-camera peaks can move the wrong way.** For 1413 the
  winning profile peak is 17:00 / 5.74 (slowness raises an
  existing 17:00 peak); for 2251 the winning model peak is
  16:00 / 3.97 (the retrain moves it one hour late vs 2b's
  15:00 / 3.94). Aggregate deviance improves while
  individual peak hours do not all improve.
- **October 2026 speed is taxi-only** (TNP ends 2026-09-30).
  The *profiles* are unaffected (pre-holdout window), but
  any future use of the daily/hourly speed *series* files
  for October inherits taxi's faster/sparser mix.
- **Pickup-area attribution** (§9.1) is a real limitation
  for per-camera use: a trip counts toward the area it
  starts in, not the roads it drives. Per-area slowness
  correlation (median 0.693) is well below citywide (0.949)
  partly for this reason.
- **`m6dm-c72p` (TNP 2019–2022) was not pulled.** It cannot
  change the profiles (outside the window); the "all five
  datasets" coverage is incomplete only as a staleness
  check.
- **The demand layer is still on by default** (§9.6) and
  still loses on every variant tested. Until Gabriel flips
  it, the default `predict_hourly` output includes a shape
  layer the validation says not to use.

### 9.8 Leakage discipline (Phase 2d)

- **Rebuilt from pre-holdout data only:** crash profiles
  (`build_profiles(crash_end="2026-07-31")`), per-camera
  pre-holdout daily crash rates, demand profiles (trips
  through 2026-07-31, via
  `validate_hourly_demand.rebuild_demand_preholdout`),
  speed profiles (trips 2023-01-01 → 2026-07-31), all
  retrain training windows (A through 2026-02-28; B/B12
  through 2026-07-31), per-area slowness/demand
  correlations. The shipped hourly profiles (full window
  through 2026-10-29) were NOT used in any 2d validation
  variant; the shipped demand profiles (2023 → present)
  were NOT used in any 2d variant — the pre-holdout
  rebuild was used instead.
- **No holdout crash enters any shape, feature, or model
  in §9.3.** Holdout crashes appear only as the target the
  metrics are computed against.
- **`log_cam_rate` is a static training-window rate, not a
  trailing feature.** `model_weights()` uses the shipped
  model's training-window rate (B12: through 2026-07-31)
  for every date. For holdout dates (Aug–Oct) that rate is
  strictly past-only — no leakage into the validation. For
  dates *inside* the training window the rate uses crashes
  from after the prediction date; that is the same
  construction worker B shipped (Feb-28 rate), and it is a
  property of the static camera-level feature, not of the
  holdout numbers. The trailing-7-day rate is past-only for
  every date.
- **Threshold selection:** no threshold was selected on the
  holdout in §9.3. The full-strength blend (α = 1) was the
  pre-registered default; α = 0.5 was a sensitivity row,
  not a tuned pick (and it loses to α = 1 anyway). The one
  selection that *was* made on the holdout is the model
  choice itself (B12 over A/B/ablations) — flagged as
  mildly optimistic in §9.7. Worker 2's threshold grid
  (r > 0.5/0.6/0.7) was likewise compared on holdout
  deviance; its best still loses, so that verdict is
  unaffected.
- **Demand/speed series staleness is not leakage.** The
  speed profiles end 2026-07-31; the demand pre-holdout
  rebuild ends 2026-07-31; neither contains holdout-period
  trips.

## 10. Phase 1 model improvements (2026-11-16)

Gabriel approved the full program on 2026-11-16 (model improvements →
real-time layer → backend → Flutter app). Phase 1 items 2 (hourly
bands) and 3 (peak bias) live here; items 1 (speed-camera join) and
4 (retraining + FOIA) are in [REPORT.md](REPORT.md) §9. The daily
model is unchanged throughout.

### 10.1 Hourly risk bands — FIXED (new per-hour bands, default)

The daily thresholds (LOW <4, MEDIUM 4–<10, HIGH ≥10 predicted/day)
applied per hour made nearly every hour read LOW by construction —
under the old banding, **zero** of 642,048 holdout camera-hours were
HIGH. New per-hour bands, derived the same way as the daily bands
(LOW/MEDIUM = round(p50), MEDIUM/HIGH = round(p90)) but on the
**hourly** population — the shipped default `predict_hourly` output
(model path, demand OFF) for all 26,752 Phase 2 holdout camera-days
× 24 h = **642,048 camera-hours**:

| Band | Threshold (predicted violations/hour, ESTIMATE) |
|---|---|
| LOW | < **0.1** |
| MEDIUM | **0.1** – < **0.5** |
| HIGH | ≥ **0.5** |

Quantiles: p50 = 0.1262, p75 = 0.2664, **p90 = 0.4822**, p95 =
0.7103, p99 = 1.5705, max = 7.60, mean = 0.22. Rounding is to
0.1/hr (hourly counts are ~24× smaller than daily). Holdout shares:
LOW 42.0% / MEDIUM 48.6% / **HIGH 9.4%** — HIGH is again about the
top decile, as designed for the daily bands. Thresholds are stable
across paths (model-only p50/p90 = 0.1262/0.4822; profile-only =
0.1176/0.5115 — both round to 0.1/0.5). Bands are pooled /
citywide-relative, like the daily bands — a deliberate choice
(justified in [HOURLY_BANDS_FINDINGS.md](HOURLY_BANDS_FINDINGS.md)).
Calling `predict_hourly` 26,752 times was infeasible (~10 s/call),
so the derivation is vectorised and proven to reproduce live
`predict_hourly` exactly (max |Δ| = 7.2e-16 on a 7-camera-day
sample). **Daily bands are unchanged** (`risk.py` untouched); no
predictions changed — only band labels.

Implementation (`hourly_risk.py` only): `predict_hourly(...,
banding="hourly")` is the new default; `banding="daily"` keeps the
old daily-threshold hourly banding for comparison. The result dict
gains a `"banding"` field. Thresholds load from
`data/processed/hourly_risk_thresholds.json` with hard-coded 0.1/0.5
fallback. Re-derive if the hourly model changes.

Worked example — cam 1413, 2026-10-15 (predictions unchanged: sum
**53.0096**, peak **15:00 at 4.82**, method "model"): re-banded
**21 HIGH / 3 MEDIUM / 0 LOW** (old: 0 HIGH / 3 MEDIUM / 21 LOW) —
**24/24 hours change band, all upward**. The peak hour finally
reads HIGH. For contrast, quiet cam 1412 the same day: 8 LOW /
16 MEDIUM / 0 HIGH — overnight 00:00–06:00 all LOW, so the "dead
overnight hour reads LOW" behaviour holds at a typical camera.
Cam 2251: 21 HIGH / 3 MEDIUM / 0 LOW. Holdout-wide: **372,364 /
642,048 (58.0%)** camera-hours change band, all upward (LOW→MEDIUM
312,207; LOW→HIGH 59,220; MEDIUM→HIGH 937; zero move down); now
60,157 (9.4%) are HIGH.

Verification (all asserted, all pass): daily model files
byte-identical (joblib `9162dcbe…`, onnx `90b91603…`, json
`28c83534…`); anchor reproduces; `banding="daily"` returns
identical hourly values (max |Δ| < 1e-9); derivation equivalence
7.2e-16. Coordinator re-verified live: default call sums 53.0096,
peak 15:00/4.82, bands 21 HIGH / 3 MEDIUM; `banding="daily"` gives
21 LOW / 3 MEDIUM with identical values.

Caveats: hot cameras never read LOW (1413's 03:00 trough = 0.28 →
MEDIUM) — pooled bands by design. `risk_scores` use the hourly
/ 2.0 scale (p99 = 2.0, wired 2026-11-17; hottest hour scores 1.0).
Historical worked-example scripts will emit new bands if re-run
(their stored files were not rewritten). Files: `derive_hourly_bands.py`,
`verify_hourly_bands.py` (ALL CHECKS PASSED),
`hourly_bands_worked_example.json`.

### 10.2 The 3pm vs 4pm peak bias — INVESTIGATED, NO SYSTEMATIC BIAS FOUND, NO FIX

Both the profile+slowness path and the B12 model peak at 16:00 while
observed crashes peak at 15:00. Worker C investigated four
hypotheses; the verdict is that the "bias" is an **argmax artifact
on a nearly flat 15:00–17:00 plateau, not a one-hour shift** — and
every candidate fix makes holdout deviance worse. No code was
changed. Full evidence: [PEAK_BIAS_FINDINGS.md](PEAK_BIAS_FINDINGS.md).

1. **Timezone / bucketing — RULED OUT.** `crash_date.hour −
   crash_hour ≡ 0` on **all 202,151** raw crash rows; congestion
   `timestamp.hour − hour ≡ 0` on **all 1,947,880** rows. Weather
   temp cycle (min 05:00, max 14:00) and trip shapes (08:00 morning
   + 18:00 evening commute peaks, speed trough 16:00–17:00) are all
   consistent Chicago-local patterns. A UTC reading would put the
   crash peak at 09:00–10:00 local — contradicted by the city's own
   derived hour columns. The 15:00 observed peak is real local time.
2. **Profile construction — NO BUG.** No off-by-one, no
   hour-smoothing kernel. Crash-only profile (K=30, no congestion
   blend): deviance 0.094839, peak **15:00** (margin +0.0014).
   Shipped cong-blended profile: deviance 0.094549, peak **15:00**
   (margin +0.0003). The congestion share does peak at 16:00
   (0.0767 vs 0.0724 at 15:00) and narrows the margin, but the blend
   still improves deviance — a net win, not a defect. Note the
   profile+slowness wired path peaks 16:00 (deviance 0.094390) —
   slowness itself peaks 17:00.
3. **Model path — near-tie, not a learned bias.** The worker-B model
   predicted **15:00 correctly** on its own Mar–Apr 2026 test window
   (deviance 0.087929) and in-sample on its train window. B12
   predicts 16:00 in-sample on its own train window too, by a
   **0.0002** margin — the extra Mar–Jul 2026 training months
   (which peaked 16:00 in Mar/Jul, 17:00 in Jun) tipped a coin-flip
   argmax. Ablations (holdout, baseline raw deviance 0.093203):
   `speed_ratio`=1.0 → peak stays 16:00, deviance 0.093258;
   weather=0 → peak moves to 15:00 but deviance worsens to
   0.093710; both → 15:00 but deviance 0.093843. The weather
   features contribute to the late tip but earn their keep. Naive
   fix tested and rejected: circularly shifting the B12 shape −1h
   gives deviance **0.093805 vs 0.093260** unshifted; +1h gives
   0.094019. The shape is deviance-optimal as-is.
4. **Attribution radius — RULED OUT.** Holdout peak is 15:00 at
   100 m (0.0847), 200 m (0.0867), and 400 m (0.0845).

Why it looks real: the observed holdout gap itself (15:00: 738
crashes vs 16:00: 678) is only **~1.6σ**. Monthly argmax flips
constantly — 15:00 wins 13/22 months, 16:00 wins 6, 17:00 wins 2,
08:00 wins 1 (Jan 2026). No seasonal pattern (Aug–Oct 15:00 share
0.0814 vs 0.0798 other months). Both paths under-predict holdout
15:00 (0.89×/0.91×) only because the holdout's 15:00 share
(0.0867) is sharper than the pre-holdout share (0.0813) — a ~1.9σ
fluctuation.

**Recommendation (accepted): close Item 3 with no fix.** Present
the peak as a "15:00–16:00 plateau" rather than a single hour. Do
not shift the model shape, drop weather features, or drop the
congestion blend — each was tested and loses on deviance. A future
independent holdout (e.g. Nov 2026 crashes) is the only evidence
that could turn this from noise into a real bias.
