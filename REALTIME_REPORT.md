# Redunator — Phase 2 Real-Time Layer Report

Built 2026-11-16 (Worker C). The real-time layer adds **live
signals** (current weather, forecast, bike-share, and key-ready
traffic/transit feeds) and a **live adjustment** that reshapes the
current hour of a camera-day — without ever changing the daily
total.

> **Headline verdict: the live adjustment does NOT ship.** On the
> same holdout protocol as Phase 2b/2c/2d, the live-adjusted
> variants lose to their baselines on mean Poisson deviance
> (LIVE-A 0.094370 vs 0.094369 profile+slowness; LIVE-B 0.093262
> vs 0.093260 B12 shape). The deltas are ~1–2×10⁻⁶ — the
> adjustment is, within noise, a no-op — but the ship rule is
> "beats the baseline", and it does not. `live_adjust.py` is kept
> as a documented experiment, default OFF: `predict_hourly` is
> untouched and no other module calls it. The provider module
> (`live_signals.py`) and the Phase 3 data contract
> ([LIVE_CONTRACT.md](LIVE_CONTRACT.md)) **do** stand — the
> endpoint serves the unadjusted prediction with a full live-signal
> audit block until a future backtest passes the adjustment.

Phase 2 daily report: [REPORT.md](REPORT.md). Hourly layer:
[HOURLY_REPORT.md](HOURLY_REPORT.md). Phase 3 contract:
[LIVE_CONTRACT.md](LIVE_CONTRACT.md).

## 1. Sources

### 1.1 Live-signal providers — `live_signals.py` (Worker B, verified live 2026-11-16)

| Provider | Key? | What it returns | Max age | Verified live |
|---|---|---|---|---|
| `open-meteo-current` | No | Chicago current temp / precipitation / wind / WMO code | 1 h | Yes, 2026-11-16 ~05:20 CST |
| `open-meteo-forecast` | No | Next-24h hourly temp / precip probability / precip | 6 h | Yes |
| `divvy-gbfs` | No | Citywide bike-availability level (`bike_occupancy`), stations fresh, median report age | 15 min (+ <50%-fresh guard) | Yes; median report age ~2–3 min |
| `tomtom-flow` | Yes (`TOMTOM_API_KEY`) | Live segment speeds | 10 min | Key-gated (HTTP 401, no key) — no account created |
| `here-flow` | Yes (`HERE_API_KEY`) | Live flow speed / jam factor | 10 min | Key-gated (HTTP 401, no key) — no account created |
| `cta-bus` | Yes (`CTA_BUS_API_KEY`) | Live bus positions / speeds | 5 min | Key-gated (API "Invalid API access key") — no account created |

No signups were made and no API keys exist for this project. The
key-ready providers are handled gracefully everywhere: a missing
key produces an error result that is listed as dropped, never a
crash.

### 1.2 Survey file

The brief said a survey worker was separately verifying live
sources into `data/hourly/LIVE_SOURCES_VERIFIED.md`. **That file
did not exist when this work ran** (checked 2026-11-16 ~05:30
CST), so this report does not cite it. Everything in §1.1 is from
`live_signals.py`'s own verified claims plus this worker's live
runs (smoke test §5.2, which re-fetched all three no-key providers
successfully and confirmed the three key-ready providers still
fail cleanly with "API key required"). If the survey file lands
later, its findings should be cross-checked against this table;
nothing here depends on it.

### 1.3 Historical data for the backtest

`data/hourly/processed/weather_hourly.parquet` (Open-Meteo
archive, 2025-01-01→2026-10-29, 16,008 hourly rows, 0 gaps),
`crashes_hourly_per_camera.parquet`, `hourly_profiles.parquet`,
`speed_profiles.parquet` — the same files as the Phase 2b/2c/2d
validations. No new data was ingested and no file under
`data/hourly/` was modified.

## 2. Method — the live adjustment (`live_adjust.py`)

### 2.1 Formula

The shipped hourly layer already contains weather: the profile
path applies `hourly_model.weather_multiplier` from the Open-Meteo
archive actuals, and the B12 Poisson model takes precipitation /
snowfall / temperature / wind as features. A live adjustment can
only add information as a **delta** between live conditions and
the conditions the baseline already assumed for that hour:

```
live_mult[h]  = weather_multiplier(live precip, live snow, live wind at h)
expected[h]   = weather multiplier the baseline already embedded for h
factor[h]     = live_mult[h] / expected[h], clipped to [0.8, 1.25]
adjusted[h]   = baseline[h] × factor[h]
hourly        = adjusted / Σadjusted × daily_prediction
```

`weather_multiplier` is the shipped assumed formula
(HOURLY_REPORT.md §2.3): `1 + 0.10 if precip > 0 +
min(0.05, 0.01·precip_mm) + min(0.10, 0.06·snowfall_cm) +
min(0.03, 0.002·max(0, wind−30))`, clipped to [1.0, 1.25].
The factor clip [0.8, 1.25] is that range inverted — the
adjustment can never move the current hour by more than the
weather multiplier itself could.

What "expected" is:

* **Live dates (after 2026-10-29, the archive end):** the baseline
  has no weather row, so its multiplier is 1.0 for every hour (and
  the model path is unavailable, so `predict_hourly` falls back to
  the profile path). Expected = 1.0; the live weather is genuinely
  new information. This is the production case the adjustment
  exists for.
* **Historical dates (inside the archive):** the profile baseline
  already applied the archive multiplier for that hour, so
  expected = that multiplier and the factor is 1.0 by construction
  — the adjustment is a no-op and says so. Applying the live
  multiplier a second time would double-count the same rain.
  `adjust_current_hour` auto-detects this from the archive row;
  pass `expected_weather_multiplier` explicitly to override it
  (the backtest and the worked example do this to simulate the
  live-date situation on a historical date).
* **Model path:** the adjustment is **not applied**. The B12 model
  ingests weather as features; there is no separable "expected
  multiplier" to take a delta against, and layering the assumed
  multiplier on top would double-count. The function returns the
  unadjusted values with the reason recorded. (Backtest variant
  LIVE-B measures the cost of topping up anyway: it loses.)

Only the **current hour** is ever touched. The other 23 hours move
only through the renormalisation, which shrinks them slightly when
the current hour rises. The daily total is never changed:
`hourly_sum == daily_prediction` is asserted in the smoke test and
in every backtest variant (reconciliation error 0.0 to float
precision).

### 2.2 Live weather lookup

For the current hour, precipitation prefers the
`open-meteo-forecast` hourly sum for that hour — the same
definition as the archive's `precipitation_mm` — falling back to
`open-meteo-current` precipitation. Wind comes from the
current-weather values. **Snowfall is not carried by either live
provider**, so the snow term is 0 in the live multiplier: a
snowfall hour gets only its precipitation part (see §6).

### 2.3 Staleness rules

A signal is used only if `live_signals.is_stale` says it is fresh
at call time (re-checked at serve time, not just at fetch time).
Stale, errored, or missing signals are dropped and listed in
`signals_dropped_stale` with fetched timestamps and reasons; the
key-ready providers without keys land there with "API key
required". If every weather signal is stale, the factor is exactly
1.0 and the output is byte-identical to the unadjusted baseline
(backtest variant LIVE-C; smoke test check 4). The adjustment
never invents a value and never crashes because a provider failed.

Every result carries three explicit lists:

* `signals_used` — sources whose values entered the response
  (`open-meteo-current` / `open-meteo-forecast`), with
  `fetched_at` and `as_of`.
* `signals_dropped_stale` — dropped sources with `fetched_at`,
  `as_of`, and `reason`.
* `signals_ignored` — fresh-but-unconsumed sources (Divvy, and
  any fresh key-ready provider with no validated use), with the
  reason they were not consumed.

### 2.4 Divvy — excluded, and why

`divvy-gbfs` is collected but **never consumed** by the adjustment.
Its `bike_occupancy` is a citywide bike-availability level — a
weak contextual activity signal, explicitly not a congestion proxy
(`live_signals.py`). No validated relationship with hourly
crash/violation risk could be shown, for two independent reasons:

1. **No historical archive exists to validate against.** The GBFS
   feed is a live-only snapshot; there is no archived occupancy
   series, and reconstructing one would need station-level Divvy
   trip data simulated into dock levels — a separate ingest +
   simulation job, not attempted here.
2. **The trip signal it would proxy for already lost.** Trip
   volume (taxi + TNP, the closest available analogue of "how busy
   the streets are") loses as a shape layer on this same holdout
   on both paths (demand 0.0949 vs 0.0945 profile-only;
   HOURLY_REPORT.md §4), because trip volume peaks 18:00 while
   crashes peak 15:00. A weaker, bike-only version of the same
   signal has no prior reason to do better.

Divvy stays in the response under `signals_ignored` with this
reason, so a future validator can see it was considered, not
overlooked. If a keyed traffic-flow provider (TomTom/HERE) ever
becomes available, *that* is the signal to validate next — it
measures the thing the adjustment actually wants (live speeds),
whereas Divvy never did.

### 2.5 API

```python
from live_adjust import adjust_current_hour, nearest_camera

r = adjust_current_hour("1413", "2026-11-16")  # fetches live signals
r["hourly"]            # 24 values, sum == r["daily_prediction"]
r["current_hour"]      # Chicago hour adjusted
r["adjustment_factor"] # 1.0 when nothing fresh / no delta
r["adjustment_applied"]
r["signals_used"]      # [{"source", "fetched_at", "as_of"}, ...]
r["signals_dropped_stale"]  # same shape + "reason"
r["signals_ignored"]   # Divvy etc., with reasons
nearest_camera(41.8781, -87.6298)  # ("3041", 442.6) — haversine metres
```

`current_hour` defaults to the current Chicago hour when `date`
is today and is required for any other date. `method`, `demand`,
`slowness`, `banding` pass through to `predict_hourly`.

## 3. Backtest — the deciding test (`validate_live_adjust.py`)

### 3.1 The honest-backtest problem

A live adjustment is a delta between live conditions and the
conditions the model expected. The honest version of that test
needs archived **forecasts** (what was expected at the time) next
to archived **actuals** — and **no forecast archive exists**.
Open-Meteo keeps no historical forecast record, and no forecast
was stored when the hourly layer was built. **Forecast data was
therefore NOT invented.** The backtest runs the weaker but honest
version the brief names, in its production-faithful form:

* The live adjustment exists for dates the model has **no weather
  for** (after 2026-10-29). For those dates the "expected"
  multiplier is 1.0 and live weather is genuinely new.
* The backtest simulates exactly that on holdout dates: the
  baseline is the **no-weather shape** (profile+slowness / B12
  shape), and the "live" signal is the archive **actual** weather
  for that hour — the best available stand-in for what a live feed
  would report at that hour.
* Because the adjustment touches one hour, the test averages over
  **all 24 choices of "current hour"**: for each holdout
  camera-day, 24 adjusted variants are scored (one per hour) and
  their deviances averaged. That is the exact expectation of the
  adjustment's effect for a random current hour.

Protocol is otherwise identical to Phase 2b/2c/2d: holdout
2026-08-01→2026-10-29, 779,760 camera-hours, 8,517 observed
attributed crashes; profiles rebuilt on pre-holdout crashes only;
level = pre-holdout daily crash rate; target = hourly crash counts
within 200 m (PROXY — hourly violations are unobservable).

### 3.2 Baselines reproduce first

| Variant | Mean Poisson deviance | Recorded | Abs diff |
|---|---|---|---|
| profile+slowness | 0.094369 | 0.094369 | 5.4e-08 |
| profile+weather+slowness | 0.094390 | 0.094390 | 3.3e-07 |
| B12 shape | 0.093260 | 0.093260 | 2.8e-07 |

All three baselines reproduce to <1e-6, so the protocol is
identical and the adjusted deltas below are real, not protocol
artifacts. Weather context for the holdout: 438/2,160 hours
(20.3%) have a multiplier above 1.0; mean multiplier 1.0223, max
1.150, mean among wet hours 1.1099.

### 3.3 Results — full table

| Variant | Mean Poisson deviance | RMSE | MAE | Pred. total | Actual total | Pred. peak hour | Hourly-share Pearson r |
|---|---|---|---|---|---|---|---|
| profile+slowness (baseline) | 0.094369 | 0.105753 | 0.020961 | 8,110 | 8,517 | 16:00 | 0.991 |
| profile+weather+slowness (full-day weather, reference) | 0.094390 | 0.105755 | 0.020960 | 8,110 | 8,517 | 16:00 | 0.990 |
| **LIVE-A: profile+slowness + current-hour live delta** | **0.094370** | 0.105753 | 0.020961 | 8,110 | 8,517 | 16:00 | 0.991 |
| B12 shape (baseline) | 0.093260 | 0.105705 | 0.020969 | 8,110 | 8,517 | 16:00 | 0.988 |
| **LIVE-B: B12 shape + current-hour weather top-up** | **0.093262** | 0.105706 | 0.020969 | 8,110 | 8,517 | 16:00 | 0.988 |

LIVE-A / LIVE-B numbers are means over all 24 choices of current
hour. Per-choice deviance ranges: LIVE-A 0.094364–0.094383,
LIVE-B 0.093253–0.093267 — a few individual hour choices beat
their baseline by up to 0.000005, but no hour wins reliably and
the mean loses in both cases. Predicted totals are unchanged by
construction (renormalisation preserves the daily total exactly).

### 3.4 Verdict — DO NOT SHIP

* **LIVE-A: DO NOT SHIP.** 0.094370 vs 0.094369 baseline
  (delta **+0.000001**, worse). RMSE and MAE unchanged to 6 dp.
* **LIVE-B: DO NOT SHIP.** 0.093262 vs 0.093260 baseline
  (delta **+0.000002**, worse). RMSE/MAE unchanged to 6 dp.
* **LIVE-C (all signals stale):** degrades to the unadjusted
  baseline exactly — asserted in the backtest (factor 1.0 is
  identity) and through the public function in the smoke test.

Reading: the adjustment is, within noise, a no-op — but the ship
rule is "beats the baseline", and it does not. This is consistent
with the earlier finding that even the **full-day** weather layer
does not help on this holdout (profile+weather+slowness 0.094390
vs profile+slowness 0.094369 — the full-day layer is *worse* by
0.000021; the current-hour-only version is ~1/24th of that effect,
hence ~+0.000001). The assumed weather multipliers were never
validated as an improvement (§3 of HOURLY_REPORT.md: profile +
weather 0.0946 vs profile-only 0.0945 — a wash); a one-hour slice
of an unvalidated layer cannot become validated by being live.
The adjustment stays experimental in `live_adjust.py`, default
OFF, with these numbers. What would change the verdict: a live
signal that measures something the model does **not** already
have — live speeds from TomTom/HERE (key-gated, unavailable here)
— validated the same way, on the same protocol.

## 4. Data contract for Phase 3

[LIVE_CONTRACT.md](LIVE_CONTRACT.md) defines `GET
/live/risk?lat=&lon=` — request params, response fields with types,
hourly bands (LOW < 0.1, MEDIUM 0.1–<0.5, HIGH ≥ 0.5/hour) and
daily bands (LOW < 4, MEDIUM 4–<10, HIGH ≥ 10/day), nearest-camera
resolution (haversine over the 361 cameras with coordinates in
`camera_segment_map.csv`; 404 beyond `max_distance_m`, default
2000 m), the `live_adjustment` audit block (`signals_used` /
`signals_dropped_stale` / `signals_ignored`, each with fetched
timestamps, plus an `all_signals_stale` flag), error cases (400 /
404 / 503; all-stale and provider failures degrade to 200 with the
unadjusted prediction), and a worked example from a real live run.
The response shape follows the SDD's `/predict_risk` conventions
(`risk_score` 0.0–1.0 on the /35 scale, `risk_level`, `confidence`,
`reasons`). Until the adjustment ships, the contract requires the
backend to return `adjustment_applied: false` and the unadjusted
values.

## 5. Worked examples

### 5.1 Simulated live rain — camera 1413, Thu 2026-10-15, current hour 18:00

The rainiest hour of the worked-example day (4.7 mm at 18:00;
HOURLY_REPORT.md §5). Live signals are **simulated** from the
archive weather for that hour (forecast precip 4.7 mm, current
precip 4.7 mm, wind 20 km/h), with `expected_weather_multiplier`
overridden to 1.0 to simulate a live date — labelled as simulated
because the real live feed cannot be re-fetched for a past date.
Base: profile path (`method="profile"`), daily 53.01, factor
**1.147**. The 18:00 hour rises 4.678 → **5.297 (+0.619)**; every
other hour falls slightly through renormalisation; the daily total
is unchanged (53.01, reconciliation error 0.0).

| Hour | Before | After | Δ | Band | Hour | Before | After | Δ | Band |
|---|---|---|---|---|---|---|---|---|---|
| 00:00 | 0.874 | 0.863 | −0.011 | HIGH | 12:00 | 2.909 | 2.872 | −0.037 | HIGH |
| 01:00 | 0.721 | 0.712 | −0.009 | HIGH | 13:00 | 1.841 | 1.817 | −0.024 | HIGH |
| 02:00 | 0.471 | 0.465 | −0.006 | MEDIUM | 14:00 | 2.865 | 2.828 | −0.037 | HIGH |
| 03:00 | 0.230 | 0.227 | −0.003 | MEDIUM | 15:00 | 3.475 | 3.431 | −0.045 | HIGH |
| 04:00 | 0.101 | 0.100 | −0.001 | LOW | 16:00 | 5.198 | 5.132 | −0.067 | HIGH |
| 05:00 | 0.265 | 0.261 | −0.003 | MEDIUM | 17:00 | 5.738 | 5.664 | −0.073 | HIGH |
| 06:00 | 0.437 | 0.431 | −0.006 | MEDIUM | **18:00** | **4.678** | **5.297** | **+0.619** | HIGH |
| 07:00 | 2.164 | 2.136 | −0.028 | HIGH | 19:00 | 3.147 | 3.107 | −0.040 | HIGH |
| 08:00 | 4.106 | 4.053 | −0.053 | HIGH | 20:00 | 1.831 | 1.807 | −0.023 | HIGH |
| 09:00 | 2.687 | 2.653 | −0.034 | HIGH | 21:00 | 1.759 | 1.737 | −0.023 | HIGH |
| 10:00 | 2.345 | 2.314 | −0.030 | HIGH | 22:00 | 1.380 | 1.363 | −0.018 | HIGH |
| 11:00 | 2.419 | 2.388 | −0.031 | HIGH | 23:00 | 1.368 | 1.351 | −0.018 | HIGH |

Note the unadjusted profile values match the Phase 2d worked
example (1413 profile+slowness peaks 17:00 at 5.74). The
adjustment does not change any band here — 18:00 was already HIGH
— it only moves the point value. Divvy (fabricated fresh,
occupancy 0.42) was ignored, as designed.

### 5.2 Real live run — Mon 2026-11-16 ~05:28 CST

`adjust_current_hour("1413", "2026-11-16")` with genuinely fetched
signals: Open-Meteo current (fetched 11:28:05 UTC, as_of 11:15
UTC) and forecast (fetched 11:28:06 UTC, as_of 11:00 UTC) both
fresh and used; live weather dry (0.0 mm precipitation, wind 6.9
km/h, precip from the forecast); factor **1.000**, adjustment not
applied. Daily 53.63 is the camera's `pre_test_mean` (no feature
row after 2026-10-29); current hour 05:00 predicted 0.285
(MEDIUM). TomTom/HERE/CTA all dropped with "API key required";
Divvy fresh but ignored (occupancy reason, §2.4). Nearest camera
to the Loop (41.8781, −87.6298) is **3041** (MICHIGAN AND
JACKSON) at **443 m** — daily 10.30, 05:00 predicted 0.038 (LOW),
full day peaking 17:00 at 1.305 (HIGH); see the contract for the
full response.

## 6. Honest limits — read before using the live layer

* **The adjustment does not beat its baseline (§3.4).** It is a
  documented experiment, not a validated improvement. Do not
  present live-adjusted values as better than the unadjusted ones.
* **No forecast archive exists (§3.1).** The backtest substitutes
  archive actuals for live signals. A real forecast-vs-actual
  delta has never been tested; if forecasts are ever archived, the
  backtest should be re-run with them before any ship decision.
* **The model already has the weather.** For any date inside the
  archive the live weather adds nothing (the anti-double-count
  guard makes the adjustment a no-op); for live dates the
  adjustment is the *only* weather input, because the model falls
  back to the profile path with multipliers of 1.0. The live
  layer's value, if any, is confined to dates after 2026-10-29.
* **Live snowfall is missing (§2.2).** Neither live provider
  carries a snowfall amount, so snowy hours get only their
  precipitation part. The archive multiplier's snow term (up to
  +0.10) is unreachable live.
* **Live current precipitation is a point value, not an hourly
  sum.** The adjustment prefers the forecast hourly sum for the
  current hour for exactly this reason; the current-weather value
  is only a fallback, and the two can disagree near hour
  boundaries.
* **Divvy is not a traffic signal (§2.4).** It is in the response
  for audit only. Do not feed `bike_occupancy` to any model as a
  congestion proxy without its own validation.
* **Keyed providers are unavailable.** TomTom/HERE/CTA need API
  keys (and accounts) that do not exist. Live speeds — the signal
  most likely to actually help — cannot be tested or served until
  someone creates them. No account was created by this work.
* **Daily lag is unchanged.** The daily model still inherits
  Phase 2's ~14-day data lag; for live dates the daily prediction
  is a pre-test-mean fallback, not a model score. The live layer
  reshapes the current hour; it cannot fix the daily anchor.
* **Hourly numbers remain ESTIMATES.** Validated against hourly
  *crash* counts near cameras (§3); the transfer from crash shape
  to violation shape is an assumption, stated, not a measurement.
* **The survey file was absent (§1.2).** If
  `data/hourly/LIVE_SOURCES_VERIFIED.md` appears later with
  findings that contradict §1.1, this report's source table needs
  revisiting.

## 7. Files

| File | What it is |
|---|---|
| `live_adjust.py` | Live adjustment: `adjust_current_hour`, `weather_delta_factor`, `apply_hour_adjustment`, `nearest_camera` (§2) — EXPERIMENTAL, default OFF |
| `validate_live_adjust.py` / `hourly_validation_live.json` | Backtest (§3) + machine-readable numbers |
| `smoke_test_live_adjust.py` | Smoke test: delta math, renormalisation, fabricated fresh/stale signals, model-path refusal, anti-double-count guard, nearest camera, live run |
| `LIVE_CONTRACT.md` | Phase 3 data contract for `GET /live/risk` (§4) |
| `live_signals.py` | Live-signal providers (Worker B) — unchanged by this work |
| `smoke_test_live_signals.py` | Provider smoke test (Worker B) — re-run, still passes |
| `REALTIME_REPORT.md` | This file |

Reproduce:

```bash
cd ~/workspace/redunator
~/workspace/redunator-venv/bin/python smoke_test_live_signals.py  # providers (Worker B)
~/workspace/redunator-venv/bin/python smoke_test_live_adjust.py   # adjustment incl. live run
~/workspace/redunator-venv/bin/python validate_live_adjust.py     # backtest (§3)
~/workspace/redunator-venv/bin/python live_adjust.py 1413 2026-11-16  # one live adjustment
```

## 8. Integrity — Phase 2 artifacts untouched

The daily model files are byte-identical to the Phase 2 baseline
(verified 2026-11-16 at the end of this work, md5):
`redunator_brain_v1.joblib`
`9162dcbe76bf842584bd6f208c8bcb99`,
`redunator_brain_v1.json`
`28c83534e67267e272108c582ed0c56b`,
`redunator_brain_v1.onnx`
`90b9160389dd76d945bc8428a9ec3bf0` — matching `live_model.json`.
`hourly_model.joblib` is unchanged from the start of this work
(md5 `d530a9a10b6e7c782bf8f9e8c5f1ca17` before and after).
`live_signals.py` was not modified (no bug found). No file under
`data/hourly/` was modified. This work added files only
(`live_adjust.py`, `validate_live_adjust.py`,
`hourly_validation_live.json`, `smoke_test_live_adjust.py`,
`LIVE_CONTRACT.md`, `REALTIME_REPORT.md`) and added link sections
to `REPORT.md` / `README.md`.
