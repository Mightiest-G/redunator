# Redunator — Phase 2: Data Pipeline + Model

Chicago red-light-camera ticket-risk predictor. Phase 2 builds the data
pipeline and a risk model from the city's public camera-violation datasets.

**Status (2026-11-15): v1 model trained and verified.** Random Forest
(max_depth=12) predicts daily violations per camera: holdout RMSE
**3.3068**, MAE **2.1452** (test 2026-08-01 → 2026-10-29), vs a naive
per-camera historical-mean baseline of 4.7997 / 2.9444. Full numbers,
top-10 intersections, worked examples, and honest limits: see
[REPORT.md](REPORT.md).

## Setup

```bash
python3 -m venv ~/workspace/redunator-venv
# /tmp is a 512 MB tmpfs on this VM — point pip's temp dir at the workspace
TMPDIR=~/workspace/.tmp ~/workspace/redunator-venv/bin/pip install -r requirements.txt
```

`requirements.txt` is fully pinned (pandas 3.1.1, scikit-learn 1.9.1,
xgboost 3.4.1, holidays 0.108, pyarrow 26.0.1, plus ONNX export deps).
All commands below use the project venv:
`~/workspace/redunator-venv/bin/python`.

## Re-run end to end

```bash
cd ~/workspace/redunator
~/workspace/redunator-venv/bin/python ingest.py     # Step 1: download both CSVs
~/workspace/redunator-venv/bin/python validate.py   # writes data/raw/ingest_stats.json
~/workspace/redunator-venv/bin/python features.py   # Step 2: build the feature table
~/workspace/redunator-venv/bin/python train.py      # Step 3: train RF + XGBoost, save winner
~/workspace/redunator-venv/bin/python risk.py       # Step 3b: risk bands + camera profiles
~/workspace/redunator-venv/bin/python export_models.py  # XGBoost alt + ONNX exports
```

Timings on this VM (~1.5 shared CPUs, 6 GB RAM): ingest ~1 min,
features ~18 s, train ~12 min (Random Forest dominates; XGBoost ~2 min),
risk ~5 s. `train.py` accepts `--skip-rf` / `--skip-xgb` to train one
model only. Re-running `train.py` is deterministic (fixed seeds) and
reproduces the shipped metrics.

### Quick check — load the model fresh and predict

```python
import joblib, json, pandas as pd
m = joblib.load("redunator_brain_v1.joblib")
meta = json.load(open("redunator_brain_v1.json"))  # feature list, split, metrics
# ...build one feature row in meta["features"] order (see risk.py for the
# exact fills: lags 0 on segment starts, lat/lon train medians if missing)...
print(m.predict([row]))  # e.g. [53.0] for camera 1413 on a hot day; never negative
```

## Phase 2b — hourly risk index

Splits each daily prediction across 24 hours from proxy signals
(crashes + congestion near each camera, hourly weather). **Every
hourly number is an ESTIMATE** — the violation data has no
hour-of-day, so no hourly violation count is ever measured. Full
method, independent validation, worked example, and honest limits:
see [HOURLY_REPORT.md](HOURLY_REPORT.md).

**Status (2026-11-15): shipped.** XGBoost Poisson model
(`hourly_model.joblib`) beats the profile-only baseline on the
Mar–Apr test (mean Poisson deviance 0.0879 vs 0.0892) and again on
an independent leakage-free check on the Phase 2 holdout
2026-08-01 → 2026-10-29 (0.0933 vs 0.0945 profile-only,
0.0940 citywide shape; 779,760 camera-hours, 8,517 observed
crashes; hourly-share Pearson r = 0.988). Peak hour: predicted
15:00 (profile) / 16:00 (model) vs observed 15:00. Weak spots,
stated plainly: overnight is over-predicted (02:00 at 1.55×
profile / 1.27× model) and the afternoon peak under-predicted
(18:00 at 0.87× / 0.88×). **Update 2026-11-16 (Phase 2d):** the
shipped model is now the B12 retrain of the same 12 features
(trained through 2026-07-31; holdout shape 0.093260 vs 0.093335),
and the profile path now blends trip slowness by default
(0.094390 vs 0.094561) — see Phase 2d below and
[HOURLY_REPORT.md §9](HOURLY_REPORT.md). The congestion feed is **dead**
(last row 2026-04-30) — congestion multipliers are 1.0 for every
date after that, never forward-filled. Daily totals reconcile
exactly: 1413 on 2026-10-15 splits 53.01 across 24 hours with
reconciliation error 0.0.

```bash
cd ~/workspace/redunator
~/workspace/redunator-venv/bin/python hourly_ingest.py --steps segments,map,congestion,region,crashes,aggregate,weather,process-congestion,verify  # Step 1b: hourly sources
~/workspace/redunator-venv/bin/python hourly_model.py --build-profiles        # profiles (365 x 24 weights)
~/workspace/redunator-venv/bin/python hourly_model.py --train-poisson --alphas 0.1,1.0,10.0 --xgb-poisson  # Poisson comparison; saves winner
~/workspace/redunator-venv/bin/python validate_hourly.py                      # independent holdout validation (worker C)
~/workspace/redunator-venv/bin/python worked_example_hourly.py                # 2026-10-15 worked example, asserted reconciliation
~/workspace/redunator-venv/bin/python hourly_risk.py 1413 2026-10-15          # smoke test: 24 hourly values, peak 15:00
```

```python
from hourly_risk import predict_hourly
r = predict_hourly("1413", "2026-10-15")  # default method="auto" -> shipped XGBoost model
r["hourly"]          # list of 24 predicted violations (ESTIMATES), sums to r["daily_prediction"]
r["bands"]           # list of 24 band labels (Phase 2 thresholds applied per hour)
r["method"]          # "model" or "profile" (profile = fallback, or method="profile")
```

| File | What it is |
|---|---|
| `hourly_model.py` | Profiles, multipliers, Poisson/XGB training + evaluation |
| `hourly_risk.py` | Clean API: `daily_prediction()`, `predict_hourly()`, `risk_band()` |
| `validate_hourly.py` / `hourly_validation.json` | Independent leakage-free holdout validation |
| `worked_example_hourly.py` / `worked_example_hourly.json` | 2026-10-15 worked example |
| `hourly_model.joblib` (1.4 MB) | **Shipped model — XGBoost count:poisson** |
| `data/hourly/processed/hourly_profiles.parquet` | 365 × 24 profile weights |
| `HOURLY_REPORT.md` | Sources, method, validation, worked example, limits |

## Phase 2c — trip-demand layer

Blends taxi + rideshare (TNP) trip volume by community area into the
hourly shape: **674,559,395 trips, 2019 → 2026** (successor datasets
`ajtu-isnz` taxi 2024–, `n26f-ihde` TNP 2023–2024, `6dvr-xwnh` TNP
2025–; boundaries `igwz-8jzy`), profiles 2023–present by day type
(weekday/weekend/holiday), 361/365 cameras mapped to an area.
`predict_hourly(..., demand=True)` is the default; `demand=False`
reproduces Phase 2b exactly. Full method, validation, worked example,
and limits: see [HOURLY_REPORT.md §4](HOURLY_REPORT.md).

**Status (2026-11-15): validated — and it does NOT improve the
holdout.** Same 2026-08-01 → 2026-10-29 protocol as Phase 2b, with
the baselines reproduced exactly (profile 0.0945, XGBoost 0.0933):
profile + demand is **0.0949** (vs 0.0945) and XGBoost + demand is
**0.0944** (vs 0.0933) mean Poisson deviance. Trip volume peaks
18:00 while crashes peak 15:00 (citywide demand-vs-crash share
Pearson r = 0.783; per-area median r = 0.535), so demand fixes the
15:00 under-prediction (ratio 0.89 → 1.02) but collapses overnight
to 0.31× at 03:00. Worked example Thu 2026-10-15: 1413's peak moves
15:00/4.74 → **18:00/6.08** with demand; 2251 stays 15:00, 3.94 →
4.23; daily totals still reconcile exactly (53.01 / 46.82). **Ship
as a documented experiment, not a claimed win** — whether the
`demand=True` default should be flipped is an open decision.
Demand data ends Sep/Oct 2026 (TNP 2026-09-30, taxi 2026-11-01).

```bash
cd ~/workspace/redunator
~/workspace/redunator-venv/bin/python build_demand.py          # map + profiles + staleness
~/workspace/redunator-venv/bin/python smoke_test_demand.py     # demand on/off smoke test
~/workspace/redunator-venv/bin/python validate_hourly_demand.py  # holdout validation (worker C)
~/workspace/redunator-venv/bin/python worked_example_demand.py  # 2026-10-15 worked example, asserted reconciliation
~/workspace/redunator-venv/bin/python hourly_risk.py 1413 2026-10-15  # default: demand ON (model+demand)
```

```python
from hourly_risk import predict_hourly
r = predict_hourly("1413", "2026-10-15", demand=False)  # Phase 2b output, demand off
r = predict_hourly("1413", "2026-10-15", method="profile+demand")
```

| File | What it is |
|---|---|
| `build_demand.py` / `data/hourly/DEMAND_BUILD.md` | Camera→area mapping, demand profiles, staleness |
| `validate_hourly_demand.py` / `hourly_validation_demand.json` | Holdout validation of the demand layer |
| `worked_example_demand.py` / `worked_example_demand.json` | 2026-10-15 worked example with demand |
| `data/hourly/processed/demand_profiles.parquet` | 78 areas × 3 day types × 24 h demand weights |
| `data/hourly/processed/camera_community_area.csv` | 365 cameras → community area |
| `data/hourly/TRIP_SOURCES_VERIFIED.md` | Trip source verification (worker A) |

## Phase 2d — improved trip-demand layer

Trip speed as a congestion proxy, demand/speed as XGBoost features,
per-area adaptive trust, daily-deviation nudges, and demand lags —
all validated on the same 2026-08-01 → 2026-10-29 holdout protocol
as Phase 2b/2c (baselines reproduced exactly: profile 0.0945,
XGBoost 0.0933). Full method, validation table, worked example,
failed experiments, and the default recommendation: see
[HOURLY_REPORT.md §9](HOURLY_REPORT.md).

**Status (2026-11-16): two small wins ship; the demand layer loses
again.** Trip slowness (1/mean trip speed, 342.7M valid taxi+TNP
trips 2023–2026-07, citywide slowness-vs-crash share r = 0.949)
blended into the **profile path** wins its bar (profile + weather +
slowness 0.0944 vs 0.0946) and is now default-on there
(`predict_hourly(..., slowness=True)`; `slowness=False` reproduces
the old profile; it is never applied on the model path, where it
hurts). The hourly model is now the **B12 retrain** — the same 12
features, trained on the full pre-holdout window (2025-01-01 →
2026-07-31) instead of worker B's Feb-28 split (shape 0.093260 vs
0.093335; worker-B model preserved as `hourly_model_v2b.joblib`).
The win is the longer training window, **not** the new features:
the same-split retrain with demand+slowness features loses
(0.093345), and the full-window retrain without them beats the one
with them. Adaptive trust (best 0.0948), daily-deviation nudges
(best 0.0951), demand lag +1 (0.0951), and the combined
slowness+demand blend (0.0955 profile / 0.0951 XGB) are all dropped.
Worked example Thu 2026-10-15, winning config (B12, demand OFF):
1413 peaks 15:00/4.82 (model; 2c demand version was 18:00/6.08,
2b no-demand 15:00/4.74) and 17:00/5.74 (profile+slowness);
2251 peaks 16:00/3.97 (model) and 15:00/4.18 (profile+slowness);
daily totals still reconcile exactly (53.01 / 46.82).
**Default recommendation: slowness ON (as wired); demand OFF**
— demand loses on every variant in 2c and 2d. **Update 2026-11-16:
Gabriel flipped the default — `demand=False` is now the default in
`hourly_risk.py` (verified: default call peaks 15:00/4.82 for cam
1413); `demand=True` remains available as opt-in.** No new
dependencies were added.

```bash
cd ~/workspace/redunator
~/workspace/redunator-venv/bin/python validate_hourly_2d.py       # slowness blends + combined, holdout validation (§9.3)
~/workspace/redunator-venv/bin/python train_hourly_2d.py --variants A,B,B12,A-demand-only,A-slowness-only  # retrains (§9.3)
~/workspace/redunator-venv/bin/python score_B12_blends.py         # best-combination check (§9.3)
~/workspace/redunator-venv/bin/python worked_example_2d.py        # 2026-10-15 worked example, winning config (§9.4)
~/workspace/redunator-venv/bin/python smoke_test_demand.py        # smoke test (updated for 2d method strings)
~/workspace/redunator-venv/bin/python hourly_risk.py 1413 2026-10-15  # default: B12 model, demand OFF, slowness N/A on model path
```

```python
from hourly_risk import predict_hourly
r = predict_hourly("1413", "2026-10-15", demand=False)  # winning model config: B12, no demand
r = predict_hourly("1413", "2026-10-15", method="profile", demand=False)  # winning profile config: +slowness
r["method"]  # "model" / "profile+slowness"; r["slowness_applied"] True only on the profile path
```

| File | What it is |
|---|---|
| `validate_hourly_2d.py` / `hourly_validation_2d.json` | Holdout validation of the slowness blends |
| `train_hourly_2d.py` / `hourly_model_2d_metrics.json` | Retrains (A/B/B12/ablations) + numbers |
| `score_B12_blends.py` | Best-combination check (B12/B + slowness) |
| `worked_example_2d.py` / `worked_example_2d.json` | 2026-10-15 worked example, winning config |
| `hourly_model_v2b.joblib` | Worker-B model, preserved byte-identical |
| `data/hourly/processed/speed_profiles.parquet` | 78 areas × 3 day types × 24 h mean trip speeds |
| `data/hourly/SPEED_FINDINGS.md` | Trip-speed findings (Worker 1) |
| `data/hourly/ADAPTIVE_FINDINGS.md` | Adaptive-trust / deviation / lag findings (Worker 2) |

## Phase 2 — real-time layer

Live signals (Open-Meteo current + forecast, Divvy GBFS — no key;
TomTom / HERE / CTA Bus key-ready, no keys exist) and a live
adjustment that reshapes the **current hour** of a camera-day by the
delta between live weather and the weather the baseline already
assumed, renormalised so the daily total never changes. Full method,
backtest, worked example, and honest limits: see
[REALTIME_REPORT.md](REALTIME_REPORT.md). Phase 3 data contract for
`GET /live/risk`: see [LIVE_CONTRACT.md](LIVE_CONTRACT.md).

**Status (2026-11-16): providers verified live; adjustment does NOT
ship.** Same 2026-08-01 → 2026-10-29 holdout protocol as Phase
2b/2c/2d (baselines reproduced to <1e-6: profile+slowness 0.094369,
B12 shape 0.093260): LIVE-A (profile+slowness + current-hour live
delta) is **0.094370 vs 0.094369** and LIVE-B (B12 + current-hour
top-up) is **0.093262 vs 0.093260** mean Poisson deviance — both
lose by ~1–2×10⁻⁶. The adjustment stays experimental in
`live_adjust.py`, default OFF (`predict_hourly` untouched); the
contract serves the unadjusted prediction with a full live-signal
audit block (`signals_used` / `signals_dropped_stale` /
`signals_ignored`, each with fetched timestamps). Divvy's
`bike_occupancy` is collected but never consumed (weak contextual
signal, no validated relationship, no historical archive). No
forecast archive exists, so the backtest used archive actuals as the
live stand-in and averaged over all 24 choices of current hour — no
forecast data was invented. With all signals stale the output is
exactly the unadjusted baseline. Worked example Thu 2026-10-15
(simulated live rain 4.7 mm at 18:00): camera 1413's 18:00 hour
4.68 → 5.30 (factor 1.147), daily total unchanged at 53.01.

```bash
cd ~/workspace/redunator
~/workspace/redunator-venv/bin/python smoke_test_live_signals.py  # providers (Worker B)
~/workspace/redunator-venv/bin/python smoke_test_live_adjust.py   # adjustment incl. live run
~/workspace/redunator-venv/bin/python validate_live_adjust.py     # backtest (§3)
~/workspace/redunator-venv/bin/python live_adjust.py 1413 2026-11-16  # one live adjustment
```

```python
from live_adjust import adjust_current_hour, nearest_camera
r = adjust_current_hour("1413", "2026-11-16")  # fetches live signals
r["hourly"]            # 24 values, sum == r["daily_prediction"]
r["adjustment_factor"] # 1.0 when nothing fresh / no delta
r["signals_used"]      # [{"source", "fetched_at", "as_of"}, ...]
r["signals_dropped_stale"]  # same shape + "reason"
nearest_camera(41.8781, -87.6298)  # ("3041", 442.6) — haversine metres
```

| File | What it is |
|---|---|
| `live_signals.py` / `smoke_test_live_signals.py` | Live-signal providers + smoke test (Worker B) |
| `live_adjust.py` | Live adjustment — EXPERIMENTAL, default OFF |
| `validate_live_adjust.py` / `hourly_validation_live.json` | Backtest + machine-readable numbers |
| `smoke_test_live_adjust.py` | Adjustment smoke test (fabricated + live signals) |
| `LIVE_CONTRACT.md` | Phase 3 data contract for `GET /live/risk` |
| `REALTIME_REPORT.md` | Sources, method, backtest, contract, worked example, limits |

## Steps 1–2 — Ingest + Features (done 2026-11-15)

Raw files land in `data/raw/`:

- `red_light_violations.csv` — 1,175,909 rows, 2014-07-01 → 2026-10-29
- `speed_violations.csv` — 504,853 rows, 2014-07-01 → 2026-09-24
  (validated only; **not** joined into v1 — different camera IDs)

Feature table: `data/processed/red_light_features.parquet` — 1,358,095
rows (one per camera-day), 2014-07-01 → 2026-10-29, 365 cameras,
23 columns. Full documentation: `data/processed/FEATURES_SUMMARY.md`.
Headline decisions: dedup keep-max per (camera, date); zero-filled
missing in-segment days (182,610 rows, flagged by `is_observed`);
lags/rolling means shifted (past only) within camera segments; camera
aggregates use pre-2026-08-01 data only (no leakage into the holdout).

## Step 3 — Model (done 2026-11-15)

Time-based holdout only: train 2014-07-01 → 2026-07-31, test
2026-08-01 → 2026-10-29. Two regressors predicting daily `violations`
per camera, plus a naive baseline (each camera's pre-test mean).

| File | What it is |
|---|---|
| `train.py` | Trains Random Forest + XGBoost, reports RMSE/MAE vs baseline, saves the winner + sidecar JSON + `data/processed/model_metrics.json` |
| `risk.py` | Maps predicted counts to a 0–1 risk score and LOW/MEDIUM/HIGH bands (thresholds derived from holdout prediction quantiles, saved to `data/processed/risk_thresholds.json`); builds per-camera typical-day profiles in `data/processed/camera_profiles.csv` |
| `export_models.py` | Retrains/saves the XGBoost alternative, exports both models to ONNX (verified against native predictions), runs the depth-12 RF experiment |
| `ingest.py` / `validate.py` | Step 1: download + validate the Chicago Data Portal CSVs |
| `features.py` | Step 2: build the feature table from the raw CSVs |
| `redunator_brain_v1.joblib` (56 MB) | **Shipped model — Random Forest, max_depth=12** |
| `redunator_brain_v1.json` | Sidecar: feature list, split dates, metrics, notes |
| `redunator_brain_v1.onnx` (30.6 MB) | Same model as ONNX |
| `redunator_brain_v1_xgboost.joblib` (8.3 MB) | XGBoost alternative (RMSE 3.3613) + `.onnx` (7.9 MB) |
| `redunator_brain_v1_rf_unbounded.joblib` (2.9 GB) | First-run unbounded RF, kept for reference (RMSE 3.3116) |
| `REPORT.md` | Metrics, top-10 intersections, worked examples, limits |
| `data/processed/` | Feature table, metrics, thresholds, camera profiles |

### Risk bands

Derived from the shipped model's holdout predictions (p50 3.58,
p90 9.91, p99 30.59): **LOW** < 4, **MEDIUM** 4–<10, **HIGH** ≥ 10
predicted violations/day; score = `clip(predicted / 35, 0, 1)`.
See `risk.py` and `REPORT.md` §2.

### Known limits

Daily granularity only (no hour-of-day, no 30-minute forecasts);
~14-day portal lag; speed data tested as a join in Phase 1
(2026-11-16) and dropped — no predictive signal (see REPORT.md
§9.1); predictions are risk estimates, not guarantees (holdout MAE
~2.15 on a ~5.26 mean); the model is ~90% recent-history driven and
slow to catch regime changes. Full list: `REPORT.md` §5.

## Phase 1 — model improvements (done 2026-11-16)

Gabriel approved the full program on 2026-11-16 (model improvements →
real-time layer → backend → Flutter app). Phase 1, in order:

1. **Speed-camera join — FAILED EXPERIMENT, nothing shipped.**
   Spatial join of the 236 speed cameras to the 365 red-light cameras
   (median nearest distance 692 m; only 68/361 match within 200 m;
   address matching: 0/361). Speed-history features do not beat the
   v1 bar on the holdout (spd500 fresh window 3.3450/2.1841 vs
   baseline 3.3504/2.1853; a `speed_age_days`-only control reproduces
   the October gains, so the gain is a staleness-flag date effect,
   not speed signal). No v2 files exist; v1 byte-identical. Full
   detail: [REPORT.md §9.1](REPORT.md) and
   [SPEED_JOIN_FINDINGS.md](SPEED_JOIN_FINDINGS.md).
2. **Hourly risk bands — FIXED.** New per-hour bands derived the
   same way as the daily bands (p50/p90 of the hourly population):
   **LOW < 0.1, MEDIUM 0.1–<0.5, HIGH ≥ 0.5** predicted
   violations/hour. Daily bands unchanged. `predict_hourly(...,
   banding="hourly")` is the default; `banding="daily"` keeps the
   old banding for comparison. Cam 1413 on 2026-10-15 now reads
   21 HIGH / 3 MEDIUM / 0 LOW (was 0 HIGH / 3 MEDIUM / 21 LOW);
   predictions unchanged. Full detail:
   [HOURLY_REPORT.md §10.1](HOURLY_REPORT.md).
3. **3pm vs 4pm peak bias — INVESTIGATED, no systematic bias, no
   fix.** Timezone/bucketing, profile construction, model artifacts,
   and attribution radius all ruled out with numbers; the gap is a
   ~1.6σ argmax artifact on a flat 15:00–17:00 plateau, and every
   candidate fix (shape shift, dropping weather, dropping the
   congestion blend) loses on holdout deviance. Present the peak as
   a "15:00–16:00 plateau". Full detail:
   [HOURLY_REPORT.md §10.2](HOURLY_REPORT.md).
4. **Monthly retraining + FOIA draft.**
   - `retrain_monthly.py` re-runs ingest → features → train →
     export into `retrain_runs/<date>/staging/`, compares on a
     rolling 90-day holdout, and promotes ONLY if the candidate
     beats the shipped model on RMSE **and** MAE **and** the naive
     baseline (strict; a tie refuses). Promotion writes versioned
     `redunator_brain_vN.*` files and moves the `live_model.json`
     pointer; v1 is never overwritten. Cron
     **`redunator-monthly-retrain`** runs it monthly, day 1,
     ~03:45 America/Chicago; first run **Tue 2026-12-01 03:45
     CST**. Gate demonstrated by dry run (naive/tie/XGB refuse,
     synthetic-better promotes). Full detail:
     [REPORT.md §9.2](REPORT.md) and
     [RETRAINING_FINDINGS.md](RETRAINING_FINDINGS.md). Open point:
     a promotion does not yet change what the product serves —
     `risk.py`/`hourly_risk.py` load v1 by name; wiring the
     `live_model.json` pointer in is Phase 3 work.
   - [FOIA_REQUEST_DRAFT.md](FOIA_REQUEST_DRAFT.md) — send-ready
     letter for **hourly violation counts per camera**, Jan 1 2023
     → present, both programs, CSV. Recipient verified: **CDOT**
     FOIA Officer via chicago.gov/publicrecords, backup
     cdotfoia@cityofchicago.org. Vendors: red-light — Modaxo d/b/a
     Elovate; speed — Verra Mobility. **Draft only — Gabriel sends
     it under his own name; nothing has been filed.** Full detail:
     [REPORT.md §9.3](REPORT.md).

```bash
cd ~/workspace/redunator
~/workspace/redunator-venv/bin/python train_v2.py            # speed-join evaluation (checkpoints; resumes from model_metrics_v2_speed.json)
~/workspace/redunator-venv/bin/python derive_hourly_bands.py # derive per-hour bands from the holdout population
~/workspace/redunator-venv/bin/python verify_hourly_bands.py # verify bands (ALL CHECKS PASSED)
~/workspace/redunator-venv/bin/python retrain_monthly.py --dry-run   # retrain pipeline, gate tests, no downloads/training/promotion
~/workspace/redunator-venv/bin/python retrain_monthly.py              # full monthly run (staging only; promotes only on a win)
```

```python
from hourly_risk import predict_hourly
r = predict_hourly("1413", "2026-10-15")                    # default: new hourly bands (21 HIGH / 3 MEDIUM)
r = predict_hourly("1413", "2026-10-15", banding="daily")   # old daily-threshold hourly banding, same values
r["banding"]  # "hourly" / "daily"
```

| File | What it is |
|---|---|
| `speed_features.py` / `train_v2.py` | Speed join + speed features; v2 evaluation (checkpointed) |
| `data/processed/speed_camera_join.csv` | 365-row red-light → nearest speed camera mapping |
| `data/processed/model_metrics_v2_speed.json` | All speed variants, both windows, ship decision (`shipped: null`) |
| `SPEED_JOIN_FINDINGS.md` | Speed-join findings (Worker A) |
| `derive_hourly_bands.py` / `verify_hourly_bands.py` | Band derivation + verification |
| `data/processed/hourly_risk_thresholds.json` | Hourly band quantiles + thresholds (0.1 / 0.5) |
| `HOURLY_BANDS_FINDINGS.md` | Hourly bands findings (Worker B) |
| `PEAK_BIAS_FINDINGS.md` | Peak-bias investigation (Worker C) |
| `retrain_monthly.py` / `live_model.json` | Monthly retrain + live-model pointer (v1) |
| `retrain_runs/2026-11-16-dryrun/` | Dry-run promotion report + log |
| `RETRAINING_FINDINGS.md` | Retraining findings (Worker D) |
| `FOIA_REQUEST_DRAFT.md` | Send-ready FOIA letter (Worker E) — draft only |

## Phase 3 — FastAPI backend (done 2026-11-16)

The model is now served over HTTP. Full docs, every endpoint with a
copy-paste curl example, env vars, and the deploy guide: see
[`backend/README.md`](backend/README.md) and
[`backend/DEPLOY.md`](backend/DEPLOY.md). **Nothing has been deployed.**

```bash
cd ~/workspace/redunator
~/workspace/redunator-venv/bin/python -m backend.app
# or: ~/workspace/redunator-venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port 8000
# then: curl http://127.0.0.1:8000/health
# docs: http://127.0.0.1:8000/docs
```

Endpoints (12 routes): `POST /predict_risk`, `POST /predict_risk/batch`,
`GET /intersections`, `GET /violation_trends`, `GET /live/risk`
(per [`LIVE_CONTRACT.md`](LIVE_CONTRACT.md)), `POST /user_report`,
`GET /user_report/{id}`, `GET/POST /user_alerts`,
`DELETE /user_alerts/{id}`, `POST /user_feedback`,
`GET/POST /user_settings`, plus `GET /health`.

What it serves: the daily model through ONNX Runtime, resolved
through the `live_model.json` pointer (a retrain promotion is picked
up without a restart; the feature parquet is cached in-process, so a
parquet rewrite still needs a restart). Hourly values are the same
proxy ESTIMATES as Phase 2b, rescaled to the ONNX daily so the API
is internally exact. **The live adjustment does NOT ship**
(`/live/risk` always returns `adjustment_applied: false`).

Verified 2026-11-16: **81 pytest tests pass**
(`~/workspace/redunator-venv/bin/python -m pytest backend/tests/ -q`);
reconciliation cam 1413 / 2026-10-15 — offline joblib daily 53.0096 vs
live API (ONNX) 53.0095, peak 15:00 / 4.8194, 21 HIGH hours,
reconciliation error 0.0 (the 1e-04 gap is ONNX float32 rounding);
ONNX-vs-joblib parity max abs diff 3.411e-06 on the first 2,000
holdout rows (threshold 2e-05 — see REPORT.md §11 for the 2.4e-06
caveat). Rate limiting demonstrated (5×200 then 429, `/health`
exempt). Model files byte-identical at the end of the work.

Honest limits that matter for deployment: warm RSS is **~1.2 GB**
for one worker, so Render's free tier (512 MB) will OOM — the deploy
guide targets the $25/mo Standard plan and documents the slimming
path back to free; SQLite user data does not survive redeploys on
Render (no persistent disk); Postgres is wired in but a live
Postgres round-trip is untested (no server here); JWT auth is still
required before any public launch. Full list:
[REPORT.md §11](REPORT.md) and [`backend/README.md`](backend/README.md).

## Phase 4 — Flutter app (2026-11-16, VERIFIED)

The Android app is built and verified in [`app/`](app/) (full detail:
[`app/README.md`](app/README.md), [`app/API_REFERENCE.md`](app/API_REFERENCE.md)).

- **What it is:** Map with color-coded camera markers
  (`GET /intersections` + `POST /predict_risk/batch`), tap for the
  detail sheet (daily prediction, 24-hour curve, reasons, trends,
  Report Spike), Query screen, OSRM-first routing with Google
  Directions fallback, Settings, offline cache (SDD FR12), foreground
  proximity alerts. Uses the backend's `risk_score` verbatim; hourly
  values labelled as estimates; no live adjustment applied.
- **Tests:** `flutter analyze` clean; `flutter test` **38/38 passed**
  (27 unit/widget + 11 live-backend tests in
  `app/test/live_backend_test.dart`, real client against the real
  server — see REPORT.md §12 for the numbers).
- **Release APK:** `app/build/app/outputs/flutter-apk/app-release.apk`
  — **53,659,706 bytes**, sha256
  `3780a557ee9ff6857fd05d9505c94b8f21963ea76bf229b1c084c6590da1ab33`,
  package `com.redunator.redunator`, versionCode 1, versionName 1.0.0,
  targetSdk 36. Built 2026-11-16 ~15:04 CST.
- **Honest limits:** no Google Maps key yet — Map and Route tabs show
  a full-screen explanation (verified in a real browser), Query +
  Settings work without it; **no Android device was available**, so
  the APK was never installed or run on a device (map tiles, GPS
  proximity alerts, and audio alerts are untested on hardware);
  background alerts are NOT implemented (alerts pause when the app is
  backgrounded/killed); no iOS build (needs macOS + $99/yr Apple
  Developer account — path documented in `app/README.md`, Gabriel
  submits); no key, no accounts, no paid services were created.
  Backend files and model files untouched (md5s in REPORT.md §12).
