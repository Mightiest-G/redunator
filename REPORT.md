# Redunator — Phase 2 Model Report (v1)

Built 2026-11-15. Chicago red-light-camera ticket-risk model: predicts
**daily violations per camera**, mapped to a 0–1 risk score and a
LOW / MEDIUM / HIGH band.

## 1. Holdout metrics (the honest numbers)

**Split — time-based only, never random** (violations are strongly
autocorrelated per camera, so a random split would leak):

| Split | Dates | Rows | Cameras |
|---|---|---|---|
| Train | 2014-07-01 → 2026-07-31 | 1,331,343 | 365 |
| Test (holdout) | 2026-08-01 → 2026-10-29 | 26,752 | 300 |

Target mean: 5.34 violations/camera-day (train), 5.26 (test). Max in
test: 142.

| Model | Holdout RMSE | Holdout MAE |
|---|---|---|
| **Naive baseline** (each camera's pre-test historical daily mean, `cam_mean_pre_test`) | 4.7997 | 2.9444 |
| **Random Forest — SHIPPED v1** (150 trees, max_depth=12) | **3.3068** | **2.1452** |
| Random Forest, unbounded depth (first run) | 3.3116 | 2.1641 |
| XGBoost (600 rounds, depth 8, lr 0.05) | 3.3613 | 2.1477 |

**Winner: Random Forest (max_depth=12).** It beats the naive baseline
by **31.1% on RMSE** (4.7997 → 3.3068) and **27.1% on MAE**
(2.9444 → 2.1452), and beats XGBoost by 1.6% on RMSE (3.3613 →
3.3068); on MAE the two are effectively tied (2.1477 vs 2.1452).

Why max_depth=12 and not the unbounded forest: the first run used
unbounded trees and scored RMSE 3.3116 — but the file was **2.9 GB**
(150 trees × ~266k nodes each). A depth-12 retrain scored *slightly
better* on the same holdout (3.3068 vs 3.3116) at **56 MB**, so it is
the shipped v1. The unbounded model is kept as
`redunator_brain_v1_rf_unbounded.joblib` for reference. The XGBoost
model is kept as the compact alternative
(`redunator_brain_v1_xgboost.joblib`, 8.3 MB).

Features (17): `dow, month, year, season, day_of_year, is_weekend,
is_holiday, days_since_first, lag_1d, roll_mean_7d, roll_mean_30d,
cam_mean_pre_test, cam_std_pre_test, cam_n_days_pre_test, latitude,
longitude, cam_code`. Excluded: `is_observed`, `intersection`,
`holiday_name`, `segment_id`, `date`. `cam_code` is an integer code
for `camera_id` (fit on sorted train cameras) — legitimate because all
300 test cameras have pre-test history. Lag/rolling NaNs (segment
starts, 407 rows) filled with 0; lat/lon NaN (4 cameras that never
have coordinates) imputed with train medians. Predictions clipped at 0.

**What the model actually leans on** (Random Forest feature
importances): `roll_mean_7d` 0.480, `roll_mean_30d` 0.308, `lag_1d`
0.109, `cam_mean_pre_test` 0.051, `cam_std_pre_test` 0.023,
`dow` 0.010, everything else ≤ 0.004. In plain terms: the model is
~90% "what this camera did recently". It tracks levels well but will
be slow to catch a sudden regime change (new enforcement, a camera
moved, roadworks).

## 2. Risk bands — how they were chosen

Bands are **derived from the distribution of the shipped model's
predictions on the holdout** (26,752 camera-days), not hand-picked.
Full numbers in `data/processed/risk_thresholds.json`.

| Predicted-count quantile | Value |
|---|---|
| p50 | 3.58 |
| p75 | 6.29 |
| p90 | 9.91 |
| p95 | 16.05 |
| p99 | 30.59 |
| max | 127.24 |

- **LOW**: predicted < **4** violations/day (≈ below the median day)
- **MEDIUM**: **4 ≤** predicted < **10** violations/day (median → 90th percentile)
- **HIGH**: predicted ≥ **10** violations/day (top decile of camera-days)
- **Risk score** = `clip(predicted / 35, 0, 1)` — 35 is the p99
  rounded up to the nearest 5, so a score of 1.0 means "as bad as the
  worst 1% of camera-days". A score of 0.5 ≈ 17–18 predicted violations.

Share of holdout camera-days in each band: LOW 54.2%, MEDIUM 36.0%,
HIGH 9.9% — as designed (HIGH ≈ the top decile).

Per-camera typical-day profiles (median predicted weekday/weekend
violations + bands + scores for all 300 cameras) are in
`data/processed/camera_profiles.csv`, built by `risk.py`.

## 3. Top 10 riskiest intersections

Ranked by **mean predicted daily violations over the holdout, summed
across all cameras at the intersection** (an intersection's risk is
its total ticket volume, not one camera's). "Actual" is the true mean
over the same 90 days.

| # | Intersection | Predicted / day | Actual / day | Cameras |
|---|---|---|---|---|
| 1 | CICERO AND I55 | 71.22 | 69.61 | 2 |
| 2 | LAKE SHORE DR AND BELMONT | 70.51 | 68.70 | 2 |
| 3 | WENTWORTH AND GARFIELD | 50.46 | 49.78 | 2 |
| 4 | 99TH AND HALSTED | 36.70 | 37.08 | 2 |
| 5 | STONEY ISLAND AND 76TH | 35.64 | 35.60 | 2 |
| 6 | HOLLYWOOD AND SHERIDAN | 35.36 | 37.11 | 2 |
| 7 | STONY ISLAND/CORNELL AND 67TH | 34.56 | 33.22 | 2 |
| 8 | CALIFORNIA AND DIVERSEY | 30.44 | 30.03 | 3 |
| 9 | 75TH AND STATE | 28.17 | 28.60 | 2 |
| 10 | CICERO AND WASHINGTON | 26.73 | 25.77 | 2 |

Per-camera, the single hottest camera is 1413 (Lake Shore Dr &
Belmont, typical weekday 60.5 predicted), then 2251 (Cicero & I-55,
56.6). See `camera_profiles.csv` for the full per-camera ranking.

### Sanity check — does the model agree with reality? YES.

The raw data's top intersections by all-time mean violations per
camera-day are **CICERO AND I55 (31.31)** and **LAKE SHORE DR AND
BELMONT (31.14)** — the model ranks those same two #1 and #2, in the
same order, and its predicted totals track the actual holdout totals
within ~2% for the top 3. The model is not inventing hotspots.

**Correction to the brief:** the brief said "California & Diversey
is the top raw intersection". It is not. In the raw data it ranks
**17th of 183 intersections** by all-time mean (9.65 violations per
camera-day; its busiest single camera, 1514, averages 19.80). The
model agrees with the data, ranking it **#8 by intersection total**
(30.44/day across its 3 cameras) and its camera 1514 #7 per-camera
(typical weekday 20.67 predicted). California & Diversey is genuinely
hot — just not the hottest.

## 4. Worked examples — Thursday 2026-10-15

One specific recent date inside the test period. Predicted vs actual
violations that day, per camera, with risk band (score = pred / 35).

**LAKE SHORE DR AND BELMONT**
| Camera | Predicted | Actual | Band | Score |
|---|---|---|---|---|
| 1412 | 4.61 | 7 | MEDIUM | 0.13 |
| 1413 | 53.01 | 50 | HIGH | 1.00 |
| **Intersection total** | **57.62** | **57** | — | — |

**CICERO AND I55**
| Camera | Predicted | Actual | Band | Score |
|---|---|---|---|---|
| 2251 | 46.82 | 39 | HIGH | 1.00 |
| 2252 | 8.15 | 12 | MEDIUM | 0.23 |
| **Intersection total** | **54.97** | **51** | — | — |

**CALIFORNIA AND DIVERSEY**
| Camera | Predicted | Actual | Band | Score |
|---|---|---|---|---|
| 1511 | 5.67 | 3 | MEDIUM | 0.16 |
| 1513 | 2.03 | 1 | LOW | 0.06 |
| 1514 | 22.57 | 7 | HIGH | 0.64 |
| **Intersection total** | **30.27** | **11** | — | — |

Two of the three intersections land within a few tickets of reality.
California & Diversey is the miss of the day: the model expected ~30
and 11 arrived, driven by camera 1514 (predicted 22.57, actual 7).
That is the recent-history behaviour from §1 — 1514's trailing 7-day
mean was high going into the 15th, so the model extrapolated it. A
single quiet day does not move the rolling means much; the model
corrects over the following days, not instantly.

## 5. Honest limits — read this before using the predictions

- **Daily granularity only.** The source data has no hour-of-day, so
  there are **no 30-minute forecasts** and no intraday pattern. A
  "HIGH" day says nothing about which hour is worst.
- **~14-day data lag.** The Chicago portal publishes violations about
  two weeks late (latest data here ends 2026-10-29). A prediction for
  "today" is really built on data up to ~2 weeks ago; `lag_1d` /
  rolling means are stale by that lag in live use.
- **Speed-camera data was NOT joined.** The speed file
  (`speed_violations.csv`, 504,853 rows) uses different camera IDs
  (`CHI041` style) and ends 2026-09-24, ~5 weeks before the red-light
  data. Joining needs an intersection/address match and was out of
  scope for v1.
- **Predictions are risk estimates, not guarantees.** Holdout MAE is
  ~2.15 violations/camera-day on a mean of ~5.26 — typical days are
  close, but single-camera single-day misses of 10–15 happen (see
  camera 1514 above). The bands are the right unit to communicate,
  not the exact count.
- **Recent-history driven.** ~90% of the model's signal is lag/rolling
  means. It will miss sudden changes (camera installed/moved,
  enforcement blitz, roadworks) until the rolling windows catch up.
  A brand-new camera with no history has no `cam_code` and no
  pre-test mean — it falls back to the citywide mean (~5.3/day).
- **Zero-fill assumption.** Days missing from the portal inside a
  camera's active segment are treated as 0 violations. If the portal
  ever omits a day that did have violations, those days are wrongly
  zero (see `FEATURES_SUMMARY.md`).
- **April 2026 re-issue cluster.** 94 of 114 conflicting duplicate
  pairs fall on 2026-04-16/17/18; max-dedup kept the higher count, so
  those days may be slightly overstated.
- **4 cameras have no coordinates** (1623, 2552, 2592, 2662); their
  lat/lon were imputed with train medians, so map placement for those
  four is meaningless.

## 6. Files

| File | What it is |
|---|---|
| `train.py` | Trains RF + XGBoost on the time-based holdout, prints metrics, saves the winner |
| `risk.py` | Derives risk bands from holdout predictions; builds `camera_profiles.csv` |
| `export_models.py` | Retrains/saves the XGBoost alternative, exports both models to ONNX, runs the depth-12 RF experiment |
| `ingest.py`, `features.py`, `validate.py` | Steps 1–2 (see README) |
| `redunator_brain_v1.joblib` (56 MB) | **Shipped model — Random Forest, max_depth=12** |
| `redunator_brain_v1.json` | Sidecar: feature list, split dates, metrics, notes |
| `redunator_brain_v1.onnx` (30.6 MB) | Same model as ONNX (verified: max |ONNX − native| = 2.4e-06 on 2,000 holdout rows) |
| `redunator_brain_v1_xgboost.joblib` (8.3 MB) | XGBoost alternative (RMSE 3.3613, MAE 2.1477) |
| `redunator_brain_v1_xgboost.onnx` (7.9 MB) | XGBoost as ONNX (verified: max diff 2.1e-05) |
| `redunator_brain_v1_rf_unbounded.joblib` (2.9 GB) | First-run unbounded RF, kept for reference |
| `data/processed/red_light_features.parquet` | Feature table (1,358,095 rows) |
| `data/processed/model_metrics.json` | Holdout metrics for all models |
| `data/processed/risk_thresholds.json` | Band thresholds + prediction quantiles |
| `data/processed/camera_profiles.csv` | Typical weekday/weekend predictions for all 300 cameras |
| `data/processed/FEATURES_SUMMARY.md` | Feature-table documentation |
| `data/raw/INGEST_SUMMARY.md` | Ingest validation notes |

## 7. How it was verified

- `train.py` re-run end to end from the saved script after the
  depth-12 change; metrics reproduced (RF RMSE 3.3068, MAE 2.1452).
- Winning model loads fresh in a new Python process and predicts a
  real camera/date sanely (non-negative, in line with that camera's
  history) — see README "Quick check".
- ONNX exports verified against native predictions (diffs ~1e-05).
- Top-10 sanity-checked against raw all-time means (§3).

## 8. Addendum — Phase 2b hourly risk index (2026-11-15)

The daily model above is unchanged. A new hourly layer splits
each daily prediction across 24 hours from proxy signals (crashes
+ congestion near each camera, hourly weather). **Every hourly
number is an ESTIMATE** — the violation data has no hour-of-day.
Full detail: [HOURLY_REPORT.md](HOURLY_REPORT.md).

Headline validation (independent, worker C — leakage-free profiles
rebuilt on pre-holdout crashes only, evaluated on the Phase 2
holdout **2026-08-01 → 2026-10-29**, 779,760 camera-hours, 8,517
observed crashes near cameras): the shipped XGBoost Poisson model
beats the profile-only baseline on mean Poisson deviance
(**0.0933 vs 0.0945**; citywide shape 0.0940), hourly-share
Pearson r = 0.988, and its predicted total (8,504) lands within
0.2% of the observed 8,517. The profile path gets the peak hour
right (**15:00**, matching observed); the model is off by one hour
(16:00). Weak spots, stated plainly: overnight is over-predicted
(02:00 at 1.55× profile / 1.27× model) and the afternoon peak is
under-predicted (18:00 at 0.87× / 0.88×); the assumed weather
multipliers showed no measurable gain on this holdout
(0.0946 vs 0.0945). The congestion feed is **dead** (last row
2026-04-30) — congestion multipliers are 1.0 for every date
after that, never forward-filled.

Worked example (Thursday 2026-10-15, `predict_hourly`): the daily
totals reconcile **exactly** (reconciliation error 0.0) — 1413:
53.01 (peak 15:00, 4.74 MEDIUM), 1412: 4.61, 2251: 46.82,
2252: 8.15. Phase 2 artifacts verified byte-identical
(`redunator_brain_v1.joblib` md5
`9162dcbe76bf842584bd6f208c8bcb99`, `.onnx`
`90b9160389dd76d945bc8428a9ec3bf0`, `.json`
`28c83534e67267e272108c582ed0c56b`) and the daily holdout metrics
(RF RMSE 3.3068 / MAE 2.1452) and worked-example predictions
(53.01 / 46.82 / 22.57) still reproduce.

## 9. Addendum — Phase 1 model improvements (2026-11-16)

Gabriel approved the full program on 2026-11-16 (model improvements →
real-time layer → backend → Flutter app). Phase 1 is the model
improvements below. The daily model v1 above is unchanged; the hourly
items (bands, peak bias) are in [HOURLY_REPORT.md](HOURLY_REPORT.md) §10.

### 9.1 Speed-camera join — FAILED EXPERIMENT, nothing shipped

The speed-camera data (`hhkd-xvj4`, 504,853 rows, 2014-07-01 →
2026-09-24, 236 cameras) was never joined in Phase 2 because camera
IDs differ. Worker A joined it spatially (cKDTree, local-metre
projection): median nearest-speed-camera distance **692 m**.
At the 200 m radius the hourly layer uses for crash attribution only
**68/361 (18.8%)** red-light cameras match a speed camera; at 500 m,
132/361 (36.6%); at 1,000 m, 248/361 (68.7%). Address/intersection
matching was tested and rejected — **0/361** match after
normalisation (red-light addresses are intersections, speed addresses
are mid-block points). Pre-train diagnostic: per-camera lag-1
correlation between a red-light camera's daily violations and its
nearest speed camera's is **~0.02** (vs the camera's own lag-1
autocorrelation of 0.37) — the signal was never there.

Holdout (same time-based split; RF 150 trees, depth 12; bar
**RMSE 3.3068 / MAE 2.1452**; same-run baseline reproduces it
exactly). Primary window with fresh speed data (2026-08-01 →
2026-09-24): spd500 **3.3450 / 2.1841** (−0.16% / −0.05% vs the
3.3504 / 2.1853 baseline on that window) — noise, not a win.
Full holdout (to 2026-10-29): spd500 3.3046/2.1447, spd_all
3.3041/2.1448 — all within 0.08% of the bar. **Decisive control:**
v1 features + a `speed_age_days` staleness flag only (no speed data
at all) scores **3.3057 / 2.1441**, reproducing the speed variants'
October gains — the apparent gain is a date effect of the
staleness flag, not speed information. XGBoost agrees (best full
RMSE 3.3280, still above the RF bar). Forest importances for spd500:
all seven speed columns combined = **0.0027**.

October handling: speed data ends 2026-09-24, so 10,307/26,752 test
rows (38.5%) are past the end of the speed series. Speed series were
carried forward (last observed count held constant) with an explicit
`speed_age_days` flag (max 35); the primary comparison uses only the
fresh window. No v2 files exist — the ship rule required beating the
baseline on the fresh window AND the bar on the full holdout AND the
staleness control, and spd500 fails the control leg on MAE
(2.1447 vs control 2.1441). v1 verified byte-identical.
Files: `speed_features.py`, `train_v2.py`,
`data/processed/speed_camera_join.csv` (365-row mapping),
`data/processed/red_light_features_speed.parquet`,
`data/processed/model_metrics_v2_speed.json`,
[SPEED_JOIN_FINDINGS.md](SPEED_JOIN_FINDINGS.md). The join and
feature parquet are reusable if a later item wants speed proximity
as an explanatory layer — it carries no predictive signal for
tomorrow's red-light count.

### 9.2 Monthly retraining automation — LIVE (cron `redunator-monthly-retrain`)

`retrain_monthly.py` re-runs ingest → features → train → export
end-to-end into a staging directory (`retrain_runs/<date>/staging/`),
compares the candidate against the shipped model on the same holdout
protocol, and promotes ONLY if the gate passes — with a written
promotion report (`promotion_report.md` + `.json` + `run.log`) every
run. Any step failure writes a FAILED report, exits non-zero, and
promotes nothing (fail closed).

Two design findings: `train.py`/`features.py`/`export_models.py`
hardcode live paths AND frozen split dates (train ≤ 2026-07-31), so
running them in place would overwrite v1 and a "retrain" on fresh
data would be a no-op. The script therefore runs **copies** in
staging with a **rolling split** preserving the v1 protocol
(time-based, ~90-day holdout on the most recent data,
`train_end = latest_date − 90d`), with `features.py`'s `TEST_START`
patched too so camera aggregates still exclude the test window
(no leakage). Live scripts were never edited. Promotion never
overwrites v1: it writes versioned `redunator_brain_v2.joblib/.json/.onnx`
and moves the `live_model.json` pointer (currently v1, md5s recorded
in the file).

**Promotion rule (baked in):** PROMOTE iff candidate RMSE < shipped
RMSE **and** candidate MAE < shipped MAE **and** candidate RMSE <
naive RMSE — strict, a tie refuses. "Shipped" is the v1 model
**re-scored on the candidate's own test set**, never a stale number.

Gate demonstration (dry run 2026-11-16, no full retrain — pulls
~270 MB and retrains on 1.5 CPUs): the scorer re-produced the
recorded v1 metrics **exactly** on the live table (RMSE 3.3068 /
MAE 2.1452, 26,752 rows). Gate tests: naive baseline (4.7997/2.9444)
→ REFUSE; exact tie → REFUSE; XGBoost (3.3613/2.1477) → REFUSE;
synthetic better (3.20/2.05) → PROMOTE; mixed (RMSE better, MAE
worse) → REFUSE. Evidence:
`retrain_runs/2026-11-16-dryrun/promotion_report.md`.

**Schedule:** cron `redunator-monthly-retrain`, monthly, day 1,
~03:45 America/Chicago, first run **Tue 2026-12-01 03:45 CST**,
effective timeout 86400s, verified present via `cron.list`. It runs
the full script (TMPDIR on home disk) and reports
PROMOTE / DO NOT PROMOTE / FAILED with numbers. No goal owns
Redunator, so no owner is set.

Open points (from [RETRAINING_FINDINGS.md](RETRAINING_FINDINGS.md)
§6): (1) a promotion does not yet change what the product serves —
`risk.py`/`hourly_risk.py` load `redunator_brain_v1.joblib` by name;
nothing reads `live_model.json` yet — wiring the pointer in is a
separate step (Phase 3 backend work). (2) A full run has never
executed end-to-end; first live proof is the Dec 1 run. Known risk:
a brand-new camera with no pre-`train_end` history trips
`train.py`'s own assertion → fail-closed, needs a handling decision
then. (3) Data lag (~14 days) means a 1st-of-month run trains on
data ending mid-previous-month. (4) Hourly model is out of scope for
the retrainer; no pruning of old run dirs (~440 MB/run) — needs a
retention decision eventually.

### 9.3 FOIA draft — ready for Gabriel to send (draft only, nothing filed)

[FOIA_REQUEST_DRAFT.md](FOIA_REQUEST_DRAFT.md) (433 lines) is a
complete send-ready letter asking for **hourly violation counts per
camera** (`camera_id`, `intersection`, `date`, `hour` 0–23 CT,
`violation_count`, complete grid including zeros) for **January 1,
2023 → present**, covering **both** programs (red-light `spqx-js37`
and speed `hhkd-xvj4`) in one request, in machine-readable CSV.
The date range gives 3+ years of seasonal coverage in the current
post-2020 traffic regime while staying proportionate. The definition
is locked to the published daily data so hourly sums should
reconcile to the published daily totals.

**Recipient (verified, not guessed): CDOT** (Chicago Department of
Transportation), FOIA Officer — GovQA portal at
chicago.gov/publicrecords, backup email **cdotfoia@cityofchicago.org**,
mail 2 North LaSalle St., Suite 1110, Chicago, IL 60602. Chicago FOIA
is department-specific; CDOT runs the Automated Enforcement Program
and publishes its annual report. The mailbox is corroborated by two
Illinois AG documents (Public Access Opinion 23-012, Sept 2023; PAC
letter 2022 PAC 72761, Oct 2023) and the Headline Club FOIA contacts
list; the portal route is confirmed by a Jan 2025 CDOT response
letter. Caveats: the named FOIA officer changed (Erica Schroeder
2022–23, G. Rubenstein by Jan 2025), so the letter is addressed to
the office, not a person; a direct fetch of CDOT's own FOIA page
returned HTTP 403 on 2026-11-16, so the draft tells Gabriel to
re-check chicago.gov/foia if the portal/email fails. **Department of
Finance is the one genuine alternate candidate** (it administers
citations/fines) — send to CDOT first, re-file to Finance only if
CDOT redirects; no Finance FOIA email was verified, so none is given.

**Vendors (from CDOT's own 2024 Automated Enforcement Program
Annual Report): red-light cameras — Modaxo, d/b/a Elovate** (lineage:
Xerox State & Local Solutions → Conduent → Modaxo; public-safety
business sold May 1, 2024); **speed cameras — Verra Mobility,
Inc.** FOIA cannot go to a vendor directly (private companies are
not "public bodies" under Illinois FOIA), but under 5 ILCS 140/2(c)
records "prepared by or for" a public body are public records, so
the letter asks CDOT to obtain the records from its vendor(s). The
red-light contract's last verified extension ran to **June 30,
2026** — post-expiry status was not verified, so the letter says
"vendor(s)" without naming one. Ranked fallbacks are embedded in
the letter: (a) subset of cameras / shorter range (2025+);
(b) citywide hourly totals; (c) weekly/monthly hourly profiles;
(d) PII-stripped event-level timestamps; (e) name of the system /
query producing the daily totals. Fee waiver requested under
5 ILCS 140/6(c) (non-commercial, public health/safety/welfare),
"notify me before incurring any fee". The draft warns Gabriel his
name and request text will be posted publicly on the City's FOIA
log, and gives the 5-business-day clock (+5-day extension) and the
Public Access Counselor appeal route. **Nothing has been filed or
sent — Gabriel sends it under his own name.**

## 10. Addendum — Phase 2 real-time layer (2026-11-16)

Live signals + a live adjustment for the current hour, built on the
shipped hourly layer. The daily model v1 above is unchanged; the
hourly model is unchanged. Full detail:
[REALTIME_REPORT.md](REALTIME_REPORT.md); Phase 3 data contract:
[LIVE_CONTRACT.md](LIVE_CONTRACT.md).

**Providers** (`live_signals.py`, Worker B, verified live): Open-Meteo
current + forecast and Divvy GBFS need no key; TomTom, HERE, and CTA
Bus are key-ready plug-ins (no keys exist, no accounts created).
**Adjustment** (`live_adjust.py`): the current hour is multiplied by
`weather_multiplier(live) / weather_multiplier(expected)` — the
*delta* between live conditions and the conditions the baseline
already assumed — and the day is renormalised, so the daily total
never changes. Stale signals are dropped and listed; with all signals
stale the output is exactly the unadjusted baseline. Divvy's
`bike_occupancy` is collected but never consumed (weak contextual
signal, no validated relationship, no historical archive).
**Backtest verdict — DO NOT SHIP.** Same holdout protocol
(2026-08-01 → 2026-10-29, 779,760 camera-hours, 8,517 crashes;
baselines reproduced to <1e-6: profile+slowness 0.094369, B12 shape
0.093260): LIVE-A (profile+slowness + current-hour live delta)
**0.094370 vs 0.094369** and LIVE-B (B12 + current-hour top-up)
**0.093262 vs 0.093260** mean Poisson deviance — both lose by
~1–2×10⁻⁶. The adjustment stays experimental, default OFF; the
`GET /live/risk` contract serves the unadjusted prediction with a
full live-signal audit block until a future backtest passes it.
No forecast archive exists, so the backtest used archive actuals
as the live stand-in and averaged over all 24 choices of current
hour; no forecast data was invented. Daily + hourly model files
verified byte-identical at the end of the work.

## 11. Addendum — Phase 3 FastAPI backend (2026-11-16)

The v1 daily model + hourly layer are now served over HTTP (FastAPI,
`backend/`). Full endpoint docs and curl examples:
[`backend/README.md`](backend/README.md); deploy guide:
[`backend/DEPLOY.md`](backend/DEPLOY.md). **Nothing has been deployed.**

**What was built.** `create_app` factory (CORS, JSON-lines logging,
in-memory rate limiting 100 req/min, `/health` + `/docs` exempt,
`{"error","message"}` error shape); ONNX serving through the
`live_model.json` pointer with per-request mtime reload;
`POST /predict_risk` (+ `/batch`), `GET /intersections`,
`GET /violation_trends` (daily only); `GET /live/risk` per
[`LIVE_CONTRACT.md`](LIVE_CONTRACT.md); user endpoints (spike
reports, proximity alerts, feedback, settings) on SQLite by default,
Postgres via `DATABASE_URL`; API-key gate for the user endpoints in
local dev (JWT auth still required before any public deployment).

**Reconciliation (verified live 2026-11-16, real HTTP).** Cam 1413 on
2026-10-15: offline `hourly_risk.predict_hourly` (joblib) → daily
**53.0096**, peak **15:00 / 4.8194**, 21 HIGH hours, method `model`.
Live API (ONNX) → daily **53.0095**, hourly_sum **53.0095**,
reconciliation_error **0.0**, peak **15:00 / 4.8194**, 21 HIGH hours,
`risk_score` 1.0, `risk_level` HIGH, `daily_source` model. The 1e-04
daily gap is ONNX float32 rounding; the hourly values are rescaled to
the ONNX daily so the API is internally exact. `/live/risk` worked
example (real network run): camera **3041**, MICHIGAN AND JACKSON,
distance_m **443.0**, daily **10.3** (`pre_test_mean`), hour 05:00
predicted **0.038 LOW**, `risk_score` **0.0011**, `confidence` **0.4**,
`adjustment_applied` **false**. Error cases verified: missing lat →
400 `invalid_parameters`; lat 91 → 400; naive `at` → 400;
`max_distance_m=50` → 400; far coords (0,0) max 100 → 404
`no_camera_nearby` with `nearest_distance_m` 9806335.7. Rate limiting
demonstrated with `REDUNATOR_RATE_LIMIT_PER_MIN=5`: 5×200 then 429
`rate_limited`; `/health` exempt.

**ONNX parity.** `backend/verify_onnx.py` measured max abs diff
**3.411e-06** on the first 2,000 holdout rows (threshold 2e-05).
**Honest caveat:** REPORT.md's 2.4e-06 figure (from §7 / the
original export) was measured in the original export environment and
is **NOT** reproduced exactly here — other 2,000-row slices measure up
to ~1.7e-05. That is float32 rounding noise (ONNX takes float32,
sklearn predicts in float64; predictions reach ~97/day, float32
epsilon there is ~7.6e-06), not a model difference. The 2e-05
threshold bounds every slice measured so far, and is the threshold
the backend docs and tests use. The two stale `2.4e-06` references in
code comments (`backend/routers/predict.py`,
`backend/model_service.py`) were corrected to `2e-05` in Phase 3.

**Tests.** 81 pytest tests pass
(`~/workspace/redunator-venv/bin/python -m pytest backend/tests/ -q`,
~26 s, 2026-11-16): every endpoint (happy path + main error cases),
the mandatory 1413 reconciliation test, the LIVE_CONTRACT worked
example (live signals mocked to fresh dry weather so the deterministic
fields hold offline), a pointer test (a COPY of `live_model.json`
with `live_version` 99 is reported as 99; the real pointer untouched),
a rate-limit test (subprocess, env var), CORS, validation-error
shapes, SQLite round-trips, and Postgres wiring up to the network.
One real bug was found and fixed by the tests: `POST /user_alerts`
returned `"active": 1` (SQLite integer) while GET/DELETE returned
`true`; the create handler now coerces to boolean. Full detail:
`backend/README.md` (Worker C section).

**Honest limits (Phase 3).** Hourly values are proxy ESTIMATES; the
live adjustment does NOT ship (`adjustment_applied` always false);
`confidence` is a heuristic, not calibrated; rate limiting is
in-memory per process; the feature parquet is cached in-process (a
retrain rewriting it needs a restart; a pointer move does not);
`weather_override` / `congestion_override` are accepted but ignored.
**Deployment limits, measured:** warm RSS is **~1.2 GB** for one
uvicorn worker (joblib RF ~295 MB + feature parquet ~390 MB + ONNX
session ~330 MB), so Render's free tier (512 MB) will OOM — the
deploy guide (`backend/DEPLOY.md`, `backend/render.yaml`) targets the
Standard plan ($25/mo) and documents the slimming path back to free
(drop the joblib load, slim the parquet). The repo is 3.3 GB (2.9 GB
reference model + raw data); the Docker image ships only the ~200 MB
the backend reads (repo-root `.dockerignore`). SQLite user data does
not survive redeploys on Render (no persistent disk); Postgres is
wired in (`psycopg[binary]` 3.3.9) but **a live Postgres round-trip
is untested** (no server in this environment). A retrain promotion
needs a redeploy to reach Render (the image is a snapshot).
**Model files verified byte-identical at the end of Phase 3:**
joblib `9162dcbe76bf842584bd6f208c8bcb99`, onnx
`90b9160389dd76d945bc8428a9ec3bf0`, json
`28c83534e67267e272108c582ed0c56b`, hourly
`d530a9a10b6e7c782bf8f9e8c5f1ca17`.

## 12. Addendum — Phase 4 Flutter app (2026-11-16, Worker 3 verify + release)

The app (built by Phase 4 Worker 2) lives in `app/`; Worker 3
re-verified it, fixed two small things, and produced the release APK.
Full app detail: `app/README.md`, `app/API_REFERENCE.md`.

### 12.1 Environment re-verified

- `flutter doctor`: Flutter 3.50.0 / Dart 3.14.0 ✓, Android toolchain
  (SDK 36.0.0) ✓, Chrome ✓, Proxy ✓, Connected device ✓ (Linux +
  Chrome), Network ✓. Only failure: Linux toolchain (GTK 3.0 dev libs
  missing — uninstallable, see `app/API_REFERENCE.md`); Linux desktop
  builds stay blocked, Android + web are fine.
- Mid-task the VM/cell migrated and `/usr/bin/google-chrome`
  vanished; `flutter doctor -v` reported Chrome ✗. Restored from the
  cached `~/workspace/.tmp/google-chrome-stable_current_amd64.deb`
  (Chrome 157.0.8103.78, deps already satisfied).
- `flutter analyze`: **No issues found!** `flutter test`: **38/38
  passed** — 27 original unit/widget tests + 11 new live-backend
  tests (`app/test/live_backend_test.dart`).

### 12.2 Deprecation suppression — investigated, removed

Worker 2's project-wide `deprecated_member_use: ignore` in
`app/analysis_options.yaml` claimed "Flutter 3.50 deprecates
`package:flutter/material.dart` in favour of a material_ui fork".
Half true: this SDK really does mark `package:flutter/material.dart`
`@Deprecated` ("Use package:material_ui/material_ui.dart instead",
after v3.47.0-0.0.pre — `~/flutter/packages/flutter/lib/material.dart`),
but **no `material_ui` package exists anywhere in the SDK**, so the
standard import cannot be replaced. With the suppression removed,
analyze reported 11 info issues: 10× the material.dart import (8 lib
+ 2 test files) and 1× a REAL deprecation — `Color.withOpacity` in
`lib/screens/map_screen.dart:438`. The real one is fixed
(`.withValues(alpha: 0.2)`); each material.dart import now carries
its own `// ignore: deprecated_member_use`, so the analyzer stays
strict for everything else. Analyze is clean with no project-wide
suppression.

### 12.3 Release APK

`app/build/app/outputs/flutter-apk/app-release.apk` — **53,659,706
bytes** (53.7 MB), sha256
`3780a557ee9ff6857fd05d9505c94b8f21963ea76bf229b1c084c6590da1ab33`.
aapt: package `com.redunator.redunator`, versionCode 1, versionName
1.0.0, compileSdk 36, targetSdk 36, minSdk 24, launchable
`com.redunator.redunator.MainActivity`. Built 2026-11-16 ~15:04 CST
(Gradle 117.5 s warm; debug APK for comparison: 162,728,020 bytes).

Two failures on the way, both fixed and documented in
`app/README.md` and `app/android/build.gradle.kts`:
1. **Build 1 (159 s): Gradle daemon OOM-killed.** The daemon from
   Worker 2's debug build disappeared mid-build with no
   OutOfMemoryError and no heap dump (kernel OOM-killer) while the
   backend (~1.1 GB RSS) + a stale Kotlin daemon (~1.2 GB) were
   resident and only ~1.7 GB was available. Fixed by killing the
   orphaned Kotlin daemon by PID, stopping the backend for the build,
   rebuilding with ~5.9 GB available.
2. **Build 2 (8 m 7 s): AGP lint crash.** `lintVitalAnalyzeRelease`
   on `:google_maps_flutter_android` dies with `NoSuchMethodError:
   java.util.List.removeLast()` (a Java 21 API) inside lint's
   JavaDocParser on that plugin's `Convert.java` — a crash in the
   lint tool under JDK 17, not a finding in our code. Only JDK 17
   exists on this VM. Fixed with `checkReleaseBuilds = false` for all
   Android modules in `android/build.gradle.kts` (the block MUST stay
   above the `evaluationDependsOn(":app")` block or AGP rejects it as
   too late); dry-run verified zero `lintVital*` tasks in the release
   graph. Re-enable release lint if the toolchain moves to JDK 21 or
   a fixed AGP. `flutter analyze` + unit tests remain the quality
   gate.

### 12.4 Live-backend verification (real server, real client)

Backend started with `~/workspace/redunator-venv/bin/python -m
backend.app` from the project root; `test/live_backend_test.dart`
runs the real `RedunatorApiClient` (no mocks) against it — **11/11
passed** — plus an independent curl sweep with identical values:

| Flow | Result |
|---|---|
| `GET /health` | ok, model v1, MAE 2.145 / RMSE 3.307, data through 2026-10-29 |
| `POST /predict_risk` 1413 @ 2026-10-15 (detail-sheet data) | 53.0095, `model`, HIGH, risk 1.0, conf 0.9, hourly sum 53.0095, recon 0.0, peak 15:00 / 4.8194 HIGH, 24 hourly entries all `estimate: true` |
| `POST /predict_risk/batch` (2 items, map markers) | count 2, errors 0; 1413 → 53.0095, 3041 → 10.2965 |
| `GET /intersections?limit=500` | 300 cameras; first 1413 LAKE SHORE DR AND BELMONT, weekday 60.54 HIGH |
| `GET /violation_trends` 1413, 2026-10-13→15 | 3 days, mean observed 54.33, total observed 163 |
| `GET /live/risk` downtown (41.8781, −87.6298) | cam 3041 MICHIGAN AND JACKSON, 443 m, `adjustment_applied: false`, factor 1.0, `estimate: true` |
| Report Spike POST → GET | round-trip ok (id returned, note echoed) |
| User alerts create → list → deactivate | ok (deactivated `active: false`) |
| User settings round-trip | defaults metric / 2000 m / MEDIUM, merge ok |
| Feedback POST | 201, id returned |
| Error paths | 400 no-location and 404 mid-ocean surface as typed `ApiException`s |

Test rows are marked `[Worker 3 live verification 2026-11-16]` under
user `w3-live-test` in the dev SQLite DB (1 report + 1 alert + 1
feedback per run × 2 runs). **One backend finding, reported not
fixed:** `/live/risk` rounds daily to 2 dp and hourly to 3 dp without
renormalising, so its hourly array sums to 10.296 while
`daily_prediction` reads 10.3 for cam 3041 — a 0.004 (0.04%)
display-rounding gap (`backend/routers/live.py:118` vs `:204`);
`/predict_risk` reconciles exactly. Bands and display are unaffected;
the verified Phase 3 backend was deliberately not touched for it.

### 12.5 Web verification (browser, not just unit tests)

`web/` did not exist, so `flutter create . --platforms=web` added it
(nothing else changed — `pubspec.yaml` untouched).
`flutter build web --release` succeeds (`✓ Built build/web`). The
default CanvasKit build was served locally and loaded in headless
Chrome 157: it boots with **zero console errors** and the Map tab
renders the full missing-key explanation (screenshot-verified: title,
6 steps, "No Maps key" chip, Map/Query/Route/Settings nav). Two
sandbox-only hurdles, both environment, not app bugs: the bundled
`/opt/meta-chromium/chrome` refuses to start from a shell ("Hatch
WebGL domain policy is required"), and headless Chrome rejects
userinfo in `--proxy-server` (`ERR_NO_SUPPORTED_PROXIES`), so it
could not fetch CanvasKit/fonts from gstatic until a throwaway
localhost CONNECT relay injected the proxy auth (killed after). On a
normal network the web build just works. `--web-renderer` no longer
exists in Flutter 3.50, so the HTML renderer is not an option.

The missing-key path is verified three ways: widget test (Map tab
shows the explanation, never a blank map), Settings screen test, and
the real-browser screenshot. One copy fix by Worker 3: the screen
used to claim "The Query, Route and Settings tabs work without a
key" even though Route also shows the explanation — it now reads
"The Query and Settings tabs work without a key; only the map and
route views need it."

### 12.6 What was NOT verified (honest limits)

- **No Android device or emulator exists in this VM** (connected
  devices: Linux + Chrome only). The release APK was built and
  aapt-verified but **never installed or run on Android** — map-tile
  rendering, GPS proximity alerts, and audio alerts are untested on
  hardware. Do not claim on-device verification.
- **No map tiles were tested** — there is no Maps key. The key-missing
  path renders correctly (see §12.5); the real map renders only once
  a key is added.
- **No iOS build** — no IPA exists; the path (Apple Developer $99/yr
  + macOS + TestFlight) is documented in `app/README.md`, Gabriel
  submits.
- **Background alerts are NOT implemented** — alerts pause when the
  app is backgrounded or killed; adding a foreground service is the
  known follow-up.
- **Query/Settings tabs were not click-tested in the browser** —
  headless Chrome cannot interact; they are covered by widget tests
  plus the live API tests, which use the same client code.
- The web app's default backend URL is the Android emulator's
  `http://10.0.2.2:8000`, which means nothing on web/desktop — set
  the URL in Settings to a reachable backend before testing Query on
  web.

### 12.7 Model + backend integrity (end of Phase 4)

Model files byte-identical to Phase 3 (md5s re-checked 2026-11-16):
joblib `9162dcbe76bf842584bd6f208c8bcb99`, onnx
`90b9160389dd76d945bc8428a9ec3bf0`, json
`28c83534e67267e272108c582ed0c56b`. No backend `.py` file modified
after 14:00 on 2026-11-16; `live_model.json` untouched (2026-11-16
08:57). The only backend writes were the test rows in the dev SQLite
DB (§12.4). No accounts, keys, or paid services were created.
