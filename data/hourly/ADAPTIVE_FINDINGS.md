# Redunator Phase 2d — Adaptive-Trust / Daily-Deviation / Lag Findings

**Worker 2, 2026-11-15.** Same holdout protocol as Phase 2b/2c
(`validate_hourly.py`, `validate_hourly_demand.py`): holdout
2026-08-01 → 2026-10-29, 90 days × 361 cameras = 779,760
camera-hours, 8,517 observed attributed crashes near cameras.
Target = hourly crash counts within 200 m (PROXY). Primary metric =
mean Poisson deviance. Baselines to beat: profile-only **0.0945**,
XGBoost **0.0933**.

**Headline: none of the three Phase 2d demand refinements beats its
baseline. All three are DROPPED as ship candidates.** The demand
shape's problem is not where it is applied, how strongly, or at what
lag — the shape itself (trips peak 18:00, crashes peak 15:00; demand
collapses overnight to 0.31–0.77×) is the wrong shape for crashes, and
no gating, nudging, or shifting of it fixes that.

## Baselines (reproduced exactly, abs diff 0.0)

| Variant | Deviance | RMSE | MAE | Pred total | Peak |
|---|---|---|---|---|---|
| profile-only (pre-holdout) | 0.094549 | 0.105764 | 0.020982 | 8,110 | 15:00 |
| profile + demand, pre-holdout | 0.094939 | 0.105771 | 0.020952 | 8,110 | 15:00 |
| XGBoost raw counts | 0.093342 | 0.105711 | 0.021455 | 8,504 | 16:00 |
| XGBoost shape × pre-holdout rate | 0.093335 | 0.105709 | 0.020969 | 8,110 | 16:00 |

Observed total 8,517; observed peak 15:00.

## Task 1 — Per-area adaptive trust: DROPPED

**Method.** Per-area demand–crash Pearson r of the 24 hourly shares,
computed on PRE-holdout data only: demand share = pre-holdout demand
profiles (trips 2023-01-01 → 2026-07-31) blended with the pre-holdout
day-type mix (396 weekday / 163 weekend / 18 holiday days); crash
share = attributed crashes 2025-01-01 → 2026-07-31 summed over the
area's cameras. Areas with <20 pre-holdout crashes never qualify
(none hit the floor — all 59 areas with cameras had ≥20). Demand
multiplier applied only in qualifying areas, 1.0 elsewhere.

**Key diagnostic.** Pre-holdout per-area correlations are far higher
than the holdout ones Phase 2c reported: **median r = 0.748, mean
0.702, min 0.356 (area 18), max 0.924 (area 13)**, vs holdout median
0.535 / min 0.130. The 577-day pre-holdout window gives a much less
noisy crash share than 90 days, so most of Phase 2c's "low-correlation
areas" were noise, not signal — there is almost nothing to gate on.
Worst pre-holdout areas: 18 (0.356), 9 (0.371), 6 (0.411), 35 (0.422),
20 (0.469). Best: 13 (0.924), 23 (0.923), 51 (0.902).

**Threshold grid (holdout deviance):**

| Threshold | Qualifying areas | Qualifying cameras | Holdout crashes in them | Profile deviance | XGB-shape deviance |
|---|---|---|---|---|---|
| r > 0.5 | 50 / 59 | 318 / 361 | 7,658 / 8,517 | 0.094870 | 0.094281 |
| r > 0.6 | 46 / 59 | 307 / 361 | 7,479 / 8,517 | 0.094850 | 0.094245 |
| r > 0.7 | 34 / 59 | 239 / 361 | 5,893 / 8,517 | 0.094826 | 0.094056 |

Profile-only is 0.094549; blanket demand is 0.094939. Adaptive trust
is strictly between the two at every threshold — it recovers only
~0.0001 of blanket demand's 0.0004 damage, and never beats
profile-only. XGB path: best adaptive (r>0.7) 0.094056 vs XGB shape
0.093335 and blanket XGB+demand 0.094361 — same story. **Chosen
threshold: none; the grid's best (r>0.7) still loses to both
baselines, so there is nothing to ship.**

Per-hour calibration of the best adaptive variant (r>0.7): worst
over-predicted 08:00 (1.20×), worst under-predicted 03:00 (0.54×) —
the overnight collapse survives gating, because it is in the demand
shape itself, even in high-correlation areas.

## Task 2 — Daily-deviation adjustment: DROPPED

**Method.** For each camera-day, deviation(h) = that source day's
taxi share in the camera's area / typical taxi share for the source
day's day type (typical = taxi-only pre-holdout profiles,
2023-01-01 → 2026-07-31, so the deviation is apples-to-apples).
Nudge multiplier = 1 + alpha·(deviation − 1), clipped to [0.5, 2.0],
applied on top of the crash profile, renormalised per camera-day so
the 24 hours still sum to the daily prediction.

**Source choice.** Taxi-only, not taxi+TNP: TNP ends 2026-09-30, so a
combined series would change composition mid-holdout; taxi covers
the full holdout through 2026-11-01 (mean 19,467 taxi trips/day
citywide on pre-holdout weekdays). 155–159 camera-days (of 32,490)
had zero source-day taxi trips in their area and got a 1.0 multiplier.

**Holdout deviance (profile path):**

| Source | alpha | Deviance | RMSE | MAE |
|---|---|---|---|---|
| same-day (post-hoc) | 0.25 | 0.095130 | 0.105810 | 0.020983 |
| same-day (post-hoc) | 0.50 | 0.096416 | 0.105902 | 0.020980 |
| same-day (post-hoc) | 1.00 | 0.097192 | 0.105960 | 0.020980 |
| previous day | 0.50 | 0.095849 | 0.105862 | 0.020972 |
| previous day | 1.00 | 0.096563 | 0.105914 | 0.020970 |
| same weekday last week | 0.50 | 0.096347 | 0.105899 | 0.020979 |
| same weekday last week | 1.00 | 0.097150 | 0.105956 | 0.020979 |

Profile-only is 0.094549. Deviance gets **monotonically worse as
alpha grows** in every source — the day-to-day wiggle in the taxi
shape is noise relative to crashes, not signal. Even the smallest
nudge (alpha 0.25, same-day) loses by 0.0006.

**Availability (honest).** The same-day version is POST-HOC: the
day's trips are not available at prediction time in production
(taxi/TNP feeds lag ~2 weeks, like the violation feed), so it could
never ship as a forecast anyway. The prev-day and last-week versions
are the forecastable ones (they use only trips from days before the
prediction day) — and they are worse still than same-day, so nothing
in this family is shippable.

## Task 3 — Demand lag test: DROPPED (but the correlation finding is real)

**Method.** Crash hour h uses the demand weight of hour (h + lag)
mod 24, from the pre-holdout demand profiles, per area and day type,
renormalised per camera-day.

**Citywide correlation, crash share(h) vs demand share(h + lag):**

| Lag | Pre-holdout r | Holdout r (diagnostic) |
|---|---|---|
| −1 | 0.617 | 0.619 |
| 0 | 0.783 | 0.782 |
| +1 | **0.867** | **0.874** |

Lag +1 is the clear winner on correlation in both windows: the
demand shape one hour *later* best matches the crash shape — i.e.
crashes run about an hour ahead of the trip peak (consistent with
crash peak 15:00 vs demand peak 18:00, though the correlation peak
at +1 rather than +3 says the shapes differ in more than a pure
shift). Lag was chosen on the pre-holdout correlation
(leakage-free); holdout deviance is reported for all three lags.

**Holdout deviance (profile path):** lag −1: 0.095077; lag 0:
0.094939 (= Phase 2c pre-holdout demand, reproduced); lag +1:
0.095128. XGB shape + lag +1: 0.094507 vs XGB shape 0.093335.
**The correlation win does not translate to a deviance win** — the
shifted demand shape is still the wrong shape (it still collapses
overnight and over-weights the evening), just a differently-wrong
one. Peak hour moves to 16:00 for lag ±1 (observed 15:00).

## Leakage notes

- **Rebuilt pre-holdout (no holdout data):** crash profiles
  (`build_profiles(crash_end="2026-07-31")`), per-camera pre-holdout
  daily crash rates, per-area demand–crash correlations, demand
  profiles (trips through 2026-07-31, via
  `validate_hourly_demand.rebuild_demand_preholdout`), taxi typical
  shares (taxi trips through 2026-07-31), lag correlations.
- **Reused from shipped files (no target leakage, but holdout-period
  inputs by construction):** none in the variants above — every
  variant in this report uses pre-holdout demand profiles, not the
  shipped `demand_profiles.parquet` (which runs 2023 → present and
  includes holdout-period trips; Phase 2c measured that drift as
  mean |Δw| 0.00014, negligible). The shipped hourly profiles were
  NOT used; profiles were rebuilt pre-holdout as in Phase 2b/2c.
- **Same-day deviation uses holdout-day trips.** That is not leakage
  of the target (trips are not crashes), but it is information from
  the prediction day itself, which is why it is labelled post-hoc and
  why the forecastable prev-day / last-week versions were tested
  alongside it.
- **Threshold selection used the holdout.** The r > 0.5/0.6/0.7 grid
  was compared on holdout deviance, so the best-threshold numbers
  are mildly optimistic. This does not affect the verdict: even the
  optimistic best still loses to both baselines. The per-area
  correlations themselves are leakage-free.

## Verdicts

| Variant | Verdict | Reason |
|---|---|---|
| Per-area adaptive trust (any threshold) | **DROPPED** | Best (r>0.7) 0.094826 / XGB 0.094056 — still worse than profile-only 0.094549 / XGB 0.093335. Pre-holdout correlations are high almost everywhere (median 0.748), so gating barely discriminates; the overnight collapse survives in qualifying areas. |
| Daily-deviation nudge (same-day, prev-day, last-week) | **DROPPED** | All 7 variants worse than profile-only; deviance worsens monotonically with alpha. Day-to-day taxi-shape wiggle is noise vs crashes. Same-day is post-hoc anyway. |
| Lagged demand (lag +1) | **DROPPED** | Correlation win is real (r 0.867 vs 0.783 pre-holdout, 0.874 vs 0.782 holdout) but deviance still loses: profile 0.095128, XGB 0.094507. A shifted wrong shape is still wrong. |

**Overall Phase 2d verdict on the demand layer:** three independent
refinements (gate it, nudge with it, shift it) all fail to beat the
no-demand baselines. The evidence now points to the demand shape
itself being a poor proxy for the crash shape at hourly resolution —
trips measure exposure volume, crashes measure something else
(speed, signal timing, enforcement patterns) that volume alone does
not capture. The remaining Phase 2d work (trip speed as a congestion
proxy; demand/speed as XGBoost features) is the more promising
direction, because it changes the *signal*, not the *packaging* of
the same volume shape. No default was flipped; `hourly_risk.py`
behaviour is unchanged.

## Files

- `hourly_adaptive.py` — builders: `preholdout_area_correlations`,
  `qualifying_areas`, `adaptive_demand_matrix`,
  `load_taxi_daily_trips`, `taxi_typical_shares`,
  `daily_deviation_multipliers`, `lagged_demand_matrix`,
  `lag_correlations`.
- `validate_hourly_adaptive.py` — full validation run (imports the
  Phase 2b/2c protocol and metric code; XGB feature construction
  copied verbatim from `validate_hourly_demand.py`).
- `hourly_validation_adaptive.json` — all numbers, per-area
  correlations, threshold counts, per-hour calibration.
- `data/hourly/ADAPTIVE_FINDINGS.md` — this file.

Untouched: `redunator_brain_v1.*`, `hourly_risk.py` (default still
`demand=True` as shipped in 2c — the coordinator decides), all Phase
2 / 2b / 2c files.
