# Redunator Phase 2b — Hourly Risk Index: model summary (worker B)

Built 2026-11-15. **Every hourly number this layer produces is an
ESTIMATE.** The daily red-light violation data has no hour-of-day, so
no hourly violation count can ever be *measured* here. The hourly
shape comes from proxy signals — crashes near each camera and
congestion speeds near each camera — and the daily total always comes
from the shipped Phase 2 daily model (`redunator_brain_v1`, Random
Forest). The hourly layer only splits that total across 24 hours; it
never changes it.

## 1. What ships

**The XGBoost Poisson model ships** (`hourly_model.joblib`, 1.4 MB),
used through `predict_hourly(..., method="model")` in
`hourly_risk.py`. The profile version is the fallback: it is used
whenever the model file is absent or the date lacks model inputs
(weather row / crash history — i.e. any date after 2026-10-29), and
it is available explicitly as `method="profile"`. The win is narrow
(§5) — expect the two paths to agree closely most days, with the
model's shape flatter overnight and sharper at the afternoon peak
than a single camera's raw crash profile.

## 2. Hourly profiles (the fallback and the baseline)

File: `data/hourly/processed/hourly_profiles.parquet` — 8,760 rows
(365 cameras × 24 hours), columns `camera_id, hour, weight, source,
n_crashes, thin_local_data, congestion_weight, congestion_coverage,
distance_to_segment_m, remapped_to_live_segment, segment_id`.

**Blending rule.** For camera with crash counts `c[h]` by hour
(`n = Σc`) and citywide crash share `p_city[h]`:

- Local crash share, Dirichlet-smoothed toward citywide:
  `crash_local[h] = (c[h] + 30·p_city[h]) / (n + 30)`.
  The prior strength K = 30 crashes: the median camera (n = 158)
  puts ~84% weight on its own counts; the thinnest camera (n = 27)
  puts ~47%. **Thin-data threshold: n < 50** → `thin_local_data =
  true`. 8 cameras are thin: the 4 unmappable cameras below (n = 0)
  plus 1243, 1244, 2181, 2182 (n = 27–49).
- Congestion share: segment hourly mean speeds `s[h]`; free-flow
  `ff` = 90th percentile of `s[h]`; slowness `g[h] = max(0,
  (ff − s[h]) / ff)`; `cong_share[h] = (g[h] + 0.02) / Σ(g + 0.02)`.
  The 0.02 floor keeps every hour non-zero. Speed varies only
  modestly by hour (citywide mean 23.0 mph at 16:00 vs 26.0 at
  02:00; median (ff − slowest)/ff = 0.133), so congestion is a weak
  shape signal and gets a small weight on purpose.
- Blend: `profile = (1 − w_cong)·crash_local + w_cong·cong_share`,
  renormalised to sum to exactly 1 (residual placed on the largest
  weight):
  - `w_cong = 0.25` if the segment covers ≥ 50% of possible hours
    AND camera–segment distance ≤ 500 m AND not remapped — 243 cameras.
  - `w_cong = 0.10` if remapped to a live segment OR distance >
    500 m OR coverage 25–50% — 113 cameras.
  - `w_cong = 0.00` if coverage < 25% or no segment — 9 cameras
    (the 4 unmappable + 5 with near-empty segments).
- **Fallback:** cameras with no usable local data (`n = 0`) get the
  citywide profile exactly (`source = "citywide"`): **1623, 2552,
  2592, 2662** — the 4 cameras with no coordinates. They still get
  daily predictions (lat/lon imputed, as in Phase 2).

**Citywide shape (sanity):** peak **15:00** (share 0.0803), trough
**04:00** (0.0113). Per-camera peaks: 99 cameras peak at 15:00, 93
at 16:00, 78 at 17:00, 13 at 08:00 (morning-peak commuter routes,
e.g. camera 1413 peaks 08:00). Mean weight by hour tracks the
citywide crash shape throughout.

## 3. Disaggregation

`hourly_risk.py::predict_hourly(camera_id, date)`:

```
raw[h] = daily_prediction × profile_weight[h] × weather_mult[h]
         × congestion_mult[h]          (profile path)
hourly[h] = raw[h] / Σraw × daily_prediction
```

The renormalisation is deliberate: the multipliers **shape the day
only**; the 24 hourly values always sum to the daily prediction
(§6). On the model path, `hourly = daily × model_weights` (weights
already sum to 1) and **no** extra weather/congestion multipliers
are applied — the model already includes weather and speed as
features.

**Daily predictions** come from the shipped RF model via
`daily_prediction()`, which reuses `train.py`'s feature list and
its fill rules (lag/rolling NaN → 0, lat/lon NaN → train medians,
`cam_code` from the sorted train camera list). The daily model file
is never modified. Dates with no feature-table row (camera inactive
that day, or after 2026-10-29) fall back to the camera's pre-test
mean, or the citywide pre-test mean (5.34/day) for an unknown
camera — flagged as `daily_source` in the result. Spot check:
2026-10-15 reproduces REPORT.md's worked examples exactly —
1413: 53.01, 2251: 46.82, 1514: 22.57.

**Risk bands** use the Phase 2 daily thresholds applied to the
hourly prediction, as specified: LOW < 4, MEDIUM 4–<10, HIGH ≥ 10.
Consequence, stated plainly: a typical camera-day predicts ~5
violations over 24 hours, so a typical hour is ~0.2 and almost
every hour is LOW. Even the hottest camera (1413, 53/day) only
reaches MEDIUM at its peak hour (4.34 at 17:00 on 2026-10-15).
Hourly HIGH is rare by construction. The daily band remains the
right unit for "is this a bad day".

## 4. Multipliers

**Weather multiplier** (one hour, ESTIMATE):
`1 + 0.10 if precip > 0 + min(0.05, 0.01·precip_mm) +
min(0.10, 0.06·snowfall_cm) + min(0.03, 0.002·max(0, wind−30))`,
clipped to [1.0, 1.25]. Dry/calm hours return exactly 1.0.
These coefficients are **ASSUMED planning values, NOT measured
effects on violations** — violations are daily-only, so no hourly
weather effect on violations can be estimated from the data. They
are calibrated to the observed within-hour wet-vs-dry **crash**
ratio: wet hours average ~1.15× the crashes of dry hours in the
same hour-of-day (range 1.04–1.49 across hours; light rain
0.1–2.5 mm ≈ 1.13×, ≥ 2.5 mm ≈ 1.15× on the raw means). A rainy
hour gets ~1.10–1.15, a snowy hour up to ~1.21. The SDD's 1.3× /
1.5× figures were **not** used and are not treated as measured
fact. Observed weather ranges in-window: precip max 11.0 mm,
snowfall max 1.68 cm, wind max 43.6 km/h.

**Congestion multiplier** (one camera-hour, ESTIMATE):
`1 + (typical_speed[h] − observed_speed) / typical_speed[h]`,
clipped to [0.80, 1.25]; `typical_speed[h]` is the segment's mean
speed at that hour-of-day over the congestion window. Coefficient
1.0 is an ASSUMED planning value. **It returns 1.0 for every
timestamp after 2026-04-30** — the congestion feed is dead and is
never forward-filled across the 3-month gap. Verified: all 1.0 for
2026-10-15, varying 0.80–1.14 for 2026-03-15 (camera 1413).

## 5. Second model — honest comparison

**Target = hourly crash counts within 200 m of each camera
(PROXY — labelled as such everywhere).** The model predicts a
relative-risk multiplier, not violations.

- Split (time-based, inside the congestion window):
  train 2025-01-01 → 2026-02-28 (3,673,536 camera-hours),
  test 2026-03-01 → 2026-04-30 (528,504 camera-hours).
- Features (12): `hour_sin, hour_cos, dow_sin, dow_cos,
  is_weekend, speed_ratio` (observed speed / segment free-flow,
  typical-speed fill when missing), `precip_mm, snowfall_cm,
  temp_c/10, wind_kmh/10, log_cam_rate` (camera mean crashes/day,
  train window), `log_trail7_rate` (trailing-7-day daily crash
  rate, no leakage — shifted).
- Baseline = **profile-only**: train-window profiles (rebuilt on
  train crashes + congestion only, so no leakage from test
  crashes) × camera train daily crash rate. Metric: mean Poisson
  deviance (primary), RMSE / MAE secondary.
- Challengers: sklearn `PoissonRegressor` (alphas 0.1, 1.0, 10.0)
  and XGBoost `count:poisson` (300 rounds, depth 6, lr 0.05,
  subsample/colsample 0.8, hist, seed 42). Test actual total:
  5,364 crashes.

| Model | Mean Poisson deviance | RMSE | MAE | Pred. total |
|---|---|---|---|---|
| **Profile-only baseline** | 0.0892 | 0.1019 | 0.0202 | 5,461 |
| PoissonRegressor α=0.1 | 0.0932 | 0.1021 | 0.0203 | 5,463 |
| PoissonRegressor α=1.0 | 0.0937 | 0.1022 | 0.0202 | 5,426 |
| PoissonRegressor α=10.0 | 0.0937 | 0.1022 | 0.0203 | 5,457 |
| **XGBoost Poisson — SHIPS** | **0.0879** | **0.1018** | **0.0200** | **5,399** |

**Verdict: the XGBoost Poisson model ships.** It beats the
profile-only baseline on all three metrics: deviance 0.0879 vs
0.0892 (−1.5%), RMSE 0.1018 vs 0.1019, MAE 0.0200 vs 0.0202, and
its predicted total (5,399) is closer to the actual 5,364 than
the baseline's 5,461. The linear PoissonRegressor **loses** on
every alpha (deviance 0.0932–0.0937, +4.5–5.0% vs baseline) —
the linear form cannot express the hour × speed / hour × weather
interactions, and it is **not** shipped. The win is narrow and
is reported as narrow: the model is a modest refinement of the
profile, not a different answer.

Why the model's shape is trustworthy despite looking flatter:
on the test set the model's overnight (00:00–05:00) share of
predicted crashes is 0.084 vs actual 0.083, while the baseline
over-predicts overnight at 0.100 (at 02:00 the baseline predicts
1.9× the actual crashes, the model 1.4×). At the 15:00 peak the
model's share is 0.080 vs actual 0.085 (baseline 0.076). The
per-camera profile's sharp overnight dip is partly small-sample
noise (a camera with ~2 crashes at 03:00 in 667 days gets a
near-zero weight); the model pools hour information across
cameras and regularises it. Feature importances (gain):
`hour_cos` 0.415, `log_cam_rate` 0.126, `hour_sin` 0.114,
`is_weekend` 0.082, `dow_sin` 0.055, the rest ≤ 0.04
(`speed_ratio` 0.029, `snowfall_cm` 0.039 — weather and speed
contribute, but hour-of-day dominates, as expected).

## 6. Verification (all run, numbers as observed)

- **Profile sums:** max |Σweight − 1| per camera = **4.44e-16**
  (8,760 rows, 365 cameras).
- **Reconciliation:** 120 camera-days (20 cameras × 6 dates
  spanning 2025-06-15 → 2026-10-29) on the profile path: max
  |Σhourly − daily| = **7.11e-15**, zero negative values. Model
  path after the float64 fix: **0.00e+00** on checked dates
  (a float32 rounding residue of ~8e-06 was found and fixed in
  `model_weights` / `predict_hourly`).
- **Citywide peak hour:** **15:00** (share 0.0803), trough 04:00
  (0.0113) — matches worker A's verified distribution.
- **Daily predictions** reproduce REPORT.md's 2026-10-15 worked
  examples exactly (1413: 53.01, 2251: 46.82, 1514: 22.57).
- **Model path end-to-end:** `predict_hourly("1413",
  "2026-10-15", method="model")` returns 24 values peaking
  15:00 (4.74, MEDIUM), sum = daily exactly; a date after
  2026-10-29 falls back to the profile path with a flag.
- **Protected files untouched:** `redunator_brain_v1.joblib /
  .onnx / .json`, `train.py`, `features.py`, `REPORT.md`,
  `README.md`, and everything under `data/hourly/raw/` and
  `data/hourly/processed/` except the new
  `hourly_profiles.parquet` were not modified.
- **`requirements.txt` unchanged:** nothing new was installed —
  the work uses only `scikit-learn 1.9.1`, `xgboost 3.4.1`,
  `pandas 3.1.1`, `pyarrow`, `joblib`, all already pinned there.

## 7. Files

| File | What it is |
|---|---|
| `hourly_model.py` | Profiles, multipliers, Poisson/XGB training + evaluation, `model_weights()` |
| `hourly_risk.py` | Clean API: `daily_prediction()`, `predict_hourly()`, `risk_band()` |
| `data/hourly/processed/hourly_profiles.parquet` | 365 × 24 profile weights + provenance flags |
| `hourly_model.joblib` (1.4 MB) | **Shipped model — XGBoost count:poisson** |
| `hourly_model_metrics.json` | Full comparison numbers, winner, importances, model params |
| `data/hourly/HOURLY_MODEL_SUMMARY.md` | This file |

Reproduce: `python hourly_model.py --build-profiles` then
`python hourly_model.py --train-poisson --alphas 0.1,1.0,10.0
--xgb-poisson` (~100 s; the XGB fit is ~90 s). Smoke test:
`python hourly_risk.py 1413 2026-10-15`.

## 8. Honest limits — read before using the hourly numbers

- **Congestion ends 2026-04-30.** Profiles embed training-period
  congestion patterns (stable shapes), but no hour-specific
  congestion signal exists for any date after that — including
  the entire Phase 2 holdout (2026-08-01 → 2026-10-29), where
  the shape comes from profiles + weather only. A live congestion
  feed (TomTom/HERE, keys required) would be needed to restore it.
- **The model predicts crashes, not violations.** Its win is on a
  proxy target. It is a better hourly *shape* for crash exposure,
  applied to violation totals — that transfer is an assumption,
  stated, not a measurement.
- **The win is narrow (1.5% deviance).** The profile fallback is
  nearly as good; if the model ever misbehaves, the profile path
  is one argument away.
- **Weather multipliers are assumed planning values**, calibrated
  only to the wet-vs-dry crash ratio (§4). They are not measured
  effects on red-light violations.
- **Timestamps are naive hourly labels** in a flat 24/day grid;
  the 02:00 row on spring-forward days (2025-03-09, 2026-03-08)
  is not a real local hour. All sources share the grid, so joins
  are consistent, but do not treat that label as true local time.
- **4 cameras (1623, 2552, 2592, 2662) have no coordinates** and
  get the citywide profile; their map placement is meaningless
  (same caveat as Phase 2).
- **Daily lag:** the daily model still inherits Phase 2's ~14-day
  data lag; the hourly layer adds no new staleness, but it cannot
  remove it either.
