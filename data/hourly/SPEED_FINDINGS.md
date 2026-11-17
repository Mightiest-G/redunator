# Phase 2d — trip-speed congestion proxy: findings (Worker 1)

Date: 2026-11-16. Author: Worker 1 (speed). Status: pulls complete,
profiles built, correlations computed. **No model was modified** — outputs
are new data files + this note only (`hourly_risk.py`, `hourly_model.py`,
`redunator_brain_v1.*` untouched).

## 1. What was pulled

SoQL aggregation only; no raw rows downloaded. Every row is a server-side
aggregate over real trips. Full query log:
[`TRIP_SPEED_QUERIES.md`](TRIP_SPEED_QUERIES.md); per-chunk records in
`data/hourly/raw/trip_speed_chunks/chunk_summary_speed.json` (82 entries).

**Speed definition.** For a (day/hour/area) cell, mean speed (mph) =
`sum(trip_miles) / (sum(trip_seconds) / 3600)` over **valid** trips only:

- **valid**: `trip_seconds > 0 AND trip_miles > 0 AND trip_miles * 3600 / trip_seconds <= 80`
- **zero**: `trip_seconds = 0` — excluded from speed, counted separately
- **missing**: `trip_seconds IS NULL OR trip_miles IS NULL` — excluded, counted separately
- **absurd**: `trip_seconds > 0 AND trip_miles IS NOT NULL AND (trip_miles <= 0 OR implied speed > 80 mph)` — excluded, counted separately

The four classes partition ALL trips (exhaustive, mutually exclusive).
Attribution is by **pickup community area + trip start hour** — the trip is
credited to where it starts, not to where it drives. That is the only
geography the source offers; a trip starting downtown and driving to O'Hare
counts as downtown speed for its start hour. Limits: (a) long trips smear
across areas; (b) a camera's area speed is a pickup-weighted average, not
the speed on the camera's own road; (c) TNP/taxi fleets are not a random
sample of all traffic (they skew to dense / downtown / airport trips).

### Datasets

| Dataset | Source | Range pulled | 2c total trips | Valid trips | Valid share |
|---|---|---|---|---|---|
| wrvz-psew | Taxi 2013–2023 | 2023-01-01 → 2024-01-01 | 6,495,570 | 5,803,639 | 89.35% |
| ajtu-isnz | Taxi 2024– | 2024-01-01 → 2026-08-01 | 17,462,873 | 15,954,052 | 91.36% |
| n26f-ihde | TNP 2023–2024 | 2023-01-01 → 2025-01-01 | 174,369,516 | 174,339,152 | 99.983% |
| 6dvr-xwnh | TNP 2025– | 2025-01-01 → 2026-08-01 | 146,640,660 | 146,622,912 | 99.988% |
| m6dm-c72p | TNP 2018–2022 | **NOT pulled — deferred, see §6** | 282,141,543 (2019–2022) | — | — |

Distinct valid trips in the profile window: **342,719,755**. (The
`chunk_summary_speed.json` total, 541,892,227, double-counts: the
daily-hourly series and holiday chunks re-aggregate the same trips as the
(dow, hour, area) chunks. Do not quote it as a trip count.)

### Edge-case counts (reconciliation)

| Dataset | zero-sec | missing | absurd | Residual (total − valid − zero − missing − absurd) |
|---|---|---|---|---|
| wrvz-psew | 128,203 (1.97%) | 1,304 (0.02%) | 562,426 (8.66%, pulled directly) | 0 (derived absurd 562,424, differs by 2 trips — see note) |
| ajtu-isnz | 232,083 (1.33%) | 3,481 (0.02%) | 1,273,278 (7.29%, pulled directly) | 0 (derived absurd 1,273,257, differs by 21 trips) |
| n26f-ihde | 164 (~0%) | 5,046 (0.003%) | 25,154 (0.014%, derived) | 0 |
| 6dvr-xwnh | 513 (~0%) | 5,808 (0.004%) | 11,427 (0.008%, derived) | 0 |

Reconciliation vs the Phase 2c `trip_hourly_by_area.parquet` totals is
**exact (residual 0) for all four pulled datasets**. Two facts fall out:

1. **Taxi data is dirty, TNP data is clean.** 8.7% / 7.3% of taxi trips are
   absurd-speed (or zero-distance) vs 0.014% / 0.008% of TNP trips; 1–2% of
   taxi trips have `trip_seconds = 0` vs ~0 in TNP. The >80 mph cap is doing
   real work for taxi and almost none for TNP. The task brief's "~10.7M
   zero-second taxi trips" is a full-history figure; in our 2023+ window it
   is 360,286 taxi trips (128,203 + 232,083).
2. **Derived-vs-pulled absurd differs by 2 trips (wrvz) and 21 trips
   (ajtu)** out of millions. The two queries compute `miles*3600/seconds`
   independently; a trip sitting exactly on the 80 mph boundary flips with
   floating-point rounding. Negligible (≤0.0001%) and it does not affect any
   profile: the derived value is what the analysis uses, and the residual
   is 0 by construction.
3. **TNP 'absurd' was derived, not queried** for n26f/6dvr: its count(*)
   query carries the arithmetic `$where` over a half-year scan and
   proxy-dropped twice (~10 min each). The partition is exact, so
   absurd = total − valid − zero − missing. Taxi absurd counts WERE pulled
   directly and cross-check the derivation (see note 2).

## 2. Speed profiles

`data/hourly/processed/speed_profiles.parquet` — **5,616 rows**: 78 areas
(0 = citywide + 77 community areas) × 3 day types (weekday / weekend /
holiday, same `holidays.US` scheme as `demand_profiles.parquet`, holiday
takes precedence) × 24 h. Window **2023-01-01 → 2026-07-31 (pre-holdout)**:
896 weekday / 370 weekend / 42 holiday days. Columns: `area, day_type,
hour, mean_speed_mph, n_trips, sum_miles, sum_seconds, n_days, window`.
Speed is a **level, not a weight**: store mean speed per cell plus
`n_trips` (sample size) so consumers can weight by it. No NaN speeds, no
zero-trip cells. Speed range 11.82–42.76 mph.

Construction: valid trips aggregated by (dow, hour, area) per dataset-year
(or month for TNP); weekday = Mon–Fri dows minus holiday (hour, area)
sums; weekend = Sat–Sun dows minus holiday sums; holiday = the holiday
aggregate itself. Null pickup areas are excluded from per-area profiles
and included in the citywide (area 0) profile, mirroring the demand build.

Citywide mean speed by hour (mph), profile window, holdout day-type mix
(62 weekday / 26 weekend / 2 holiday days):

| Hour | 00 | 01 | 02 | 03 | 04 | 05 | 06 | 07 | 08 | 09 | 10 | 11 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Speed | 27.22 | 27.46 | 28.43 | 31.52 | 34.44 | 33.19 | 28.66 | 24.01 | 22.80 | 23.67 | 24.25 | 23.48 |
| Hour | 12 | 13 | 14 | **15** | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23 |
| Speed | 22.44 | 21.62 | 19.90 | **18.58** | 17.66 | 17.51 | 18.70 | 21.34 | 23.41 | 23.89 | 24.04 | 24.96 |

Citywide crashes (holdout, all Chicago, for reference) peak at **15:00
(2,323)**; speed is slowest at **17:00 (17.51 mph)**, second-slowest 16:00
(17.66). So the speed trough lags the crash peak by ~2 h — but speed is
already near-trough at 15:00 (18.58). By day type: weekday min 16.58 mph
@17:00, max 35.60 @04:00; weekend min 19.37 @16:00, max 34.08 @05:00;
holiday min 21.32 @16:00, max 33.81 @05:00. Per-area 15:00 weekday speed
ranges 13.3 mph (area 20) to 25.6 mph (area 54), median 15.9 — real
spatial variation, not a flat citywide curve. Taxi overall mean speed
19.42 mph vs TNP 22.28 mph (different trip mixes; the blend is
TNP-dominated by volume, ~11:1, as with demand).

## 3. Correlation — speed vs crashes (before any blending)

Crash series: `crashes_hourly_citywide.parquet` (all Chicago crashes, not
just near cameras). Speed series:
`data/hourly/processed/trip_speed_citywide_daily_hourly.parquet`
(16,006 rows, 2025-01-01 → 2026-10-29; 2 missing hours are the
spring-forward 02:00s). **October 2026 speed is taxi-only**: TNP dataset
6dvr-xwnh ends 2026-09-30, so the combined series has no TNP trips in
October; the holdout per-area file
(`trip_speed_hourly_by_area.parquet`, 207,661 rows, 16,630,109 trips,
Aug–Oct 2026) is likewise taxi-only in October.

### Citywide, 24-hour profile (holdout day-type mix) vs holdout crash share

| Signal | Pearson r | p | Peak hour | Observed crash peak |
|---|---|---|---|---|
| **Slowness (1/speed)** | **+0.941** | 8.3e-12 | 17:00 | 15:00 |
| Speed | −0.912 | 5.8e-10 | 04:00 (fastest) | 15:00 |
| Congestion (free-flow − speed; free flow = 42.20 mph) | +0.912 | 5.8e-10 | 17:00 | 15:00 |
| **Demand (Phase 2c, same method)** | **+0.783** | — | 18:00 | 15:00 |

### Per day type (profile vs holdout crash share)

| Day type | Slowness r | Demand r |
|---|---|---|
| Weekday | 0.950 | 0.786 |
| Weekend | 0.958 | 0.785 |

Speed beats demand **within each day type**, not just in the mix.

### Per community area (56 areas with ≥20 holdout crashes near cameras)

| Signal | Median r | Mean r | Min | Max |
|---|---|---|---|---|
| **Slowness profile** | **+0.686** | 0.634 | 0.127 (area 57, 32 crashes) | 0.905 (area 28, 394 crashes) |
| Speed profile | −0.647 | −0.610 | −0.838 | −0.117 |
| **Demand profile (Phase 2c)** | **+0.535** | — | — | — |

Best slowness areas: 28 (r=0.905), 16 (0.887), 21 (0.878). Worst:
57 (0.127), 26 (0.230), 53 (0.234) — all low-crash areas (27–74
crashes), where a 24-share correlation is noisy.

### Daily-hourly series (16,006 hourly observations, 2025-01-01 → 2026-10-29)

| Signal | Pearson r (full) | Pearson r (holdout only, n=2,160) |
|---|---|---|
| Speed | −0.763 | −0.799 |
| **Slowness** | **+0.782** | **+0.816** |
| Congestion | +0.763 | — |
| Demand (count) | +0.558 | +0.369 |

Slowness and demand profiles correlate with each other at r=0.868
(weekday 0.844, weekend/holiday ~0.91) — related but not the same signal;
speed's trough (17:00) sits 1 h before demand's peak (18:00) and 2 h after
the crash peak (15:00).

## 4. Lag test (citywide series)

Speed at h−1 / h / h+1 vs crash count at hour h (Pearson r):

| Lag | Speed r (full) | Speed r (holdout) | Slowness r (full) | Slowness r (holdout) |
|---|---|---|---|---|
| h−1 | −0.677 | −0.727 | +0.677 | +0.718 |
| **h (contemporaneous)** | **−0.763** | **−0.799** | **+0.782** | **+0.816** |
| h+1 | −0.718 | −0.740 | +0.730 | +0.751 |

**Best lag is h itself.** No useful lead or lag: h−1 and h+1 are both
worse. Same test for the demand (count) signal, from the existing
`trip_hourly_by_area.parquet` citywide series:

| Lag | Demand r (full) | Demand r (holdout) |
|---|---|---|
| h−1 | +0.472 | +0.317 |
| h | +0.558 | +0.369 |
| **h+1 (lead)** | **+0.577** | **+0.378** |

Demand's best lag is actually the *lead* (trips at h+1 predict crashes at
h marginally better than trips at h) — consistent with demand's signal
being mostly the shared diurnal shape. Speed beats demand at **every**
lag, by 0.2–0.45 in r.

## 5. Assessment — is speed promising?

**Yes — the most promising Phase 2d signal so far, by a wide margin.**
On every comparison the task asked for, slowness beats the demand signal
that Phase 2c found useless:

- Citywide profile r: **0.941 vs 0.783** (slowness vs demand).
- Per-area median r: **0.686 vs 0.535**.
- Within-day-type r: **0.950 / 0.958 vs 0.786 / 0.785**.
- Series r: **0.782 vs 0.558** (0.816 vs 0.369 on the holdout alone).
- Best lag is contemporaneous (h), which is what a same-hour multiplier
  needs; demand's best lag is a lead, which is not usable in the same way.

Caveats, stated plainly:

1. **Correlation is not deviance.** A 0.941 profile correlation does not
   guarantee the blended model beats profile-only 0.0945 / XGBoost
   0.0933 mean Poisson deviance. The blend still has to be run (Worker 2
   / coordinator). The peak-hour offset is the main risk: slowness peaks
   17:00, crashes peak 15:00 — a pure slowness multiplier will overweight
   16:00–17:00 and underweight 15:00 relative to truth, exactly the
   failure mode demand had (peak 18:00). What saves it is that speed is
   already near-trough at 15:00, and the profile correlation is high
   *despite* the offset.
2. **The series correlation is partly diurnal.** Both speed and crashes
   have strong hour-of-day patterns, so the 0.782 series r overstates the
   incremental signal; the 24-point profile r (0.941) is the cleaner
   comparison, and it is also where speed wins biggest.
3. **October 2026 is taxi-only** (TNP ends 2026-09-30). Any blend using
   October speed inherits taxi's faster/sparser mix; the profile build
   (2023–2026-07) is unaffected.
4. **Pickup-area attribution** (§1) is a real limitation for per-camera
   use; per-area median r 0.686 (vs citywide 0.941) partly reflects that.
5. **m6dm-c72p (2019–2022) was not pulled** (§6) — it cannot affect the
   profiles (out of window) but the "all five datasets" coverage is
   incomplete.

Recommendation: **blend slowness (1/speed), not raw speed, as the
shape signal** — it is the best-correlating form at every level — and
test it the same way demand was tested (profile path and XGB-shape path,
pre-holdout profiles, holdout 2026-08-01 → 10-29). If the deviance does
not beat 0.0945 / 0.0933, the fallback is a *combined* slowness+demand
shape, since the two profiles correlate only 0.868 and may complement
each other around the 15:00–18:00 peak.

## 6. What was NOT done / open items

- **m6dm-c72p (TNP 2018–2022, 282.1M trips in 2019–2022) not pulled.**
  It is outside the profile window (2023-01-01 → 2026-07-31), so it cannot
  change `speed_profiles.parquet`; it was deferred for time, not for
  principle. Pulling it monthly (the only survivable unit) is 48 chunks
  at ~2–3 min each — roughly 2–2.5 h of wall time — and would only serve
  a staleness/context check. Say the word and it can be run.
- **No blending, no model change.** `hourly_risk.py`, `hourly_model.py`,
  `redunator_brain_v1.*` untouched (verified: no writes outside the new
  speed files listed in §7).
- **No per-camera deviance test** — that is the blending step, not this
  worker's task. The per-area file
  (`trip_speed_hourly_by_area.parquet`) is built for it.

## 7. Files delivered

| File | What it is |
|---|---|
| `data/hourly/processed/speed_profiles.parquet` | 5,616 rows: area (0=citywide + 77) × day_type × hour, mean speed + n_trips + sums, window 2023-01-01 → 2026-07-31 |
| `data/hourly/processed/trip_speed_citywide_daily_hourly.parquet` | 16,006 rows: daily-hourly citywide speed, 2025-01-01 → 2026-10-29 |
| `data/hourly/processed/trip_speed_hourly_by_area.parquet` | 207,661 rows: daily-hourly speed by area, holdout Aug–Oct 2026 (taxi-only in Oct) |
| `data/hourly/SPEED_FINDINGS.md` | This file |
| `data/hourly/TRIP_SPEED_QUERIES.md` | Exact SoQL queries, definitions, quirks, chunk log |
| `data/hourly/raw/trip_speed_chunks/` | 102 chunk files (parquet + edge JSONs) + `chunk_summary_speed.json` |
| `data/hourly/speed_work/` | `speed_pull_one.py`, `run_jobs.py`, job lists, `analyze_speed.py`, `speed_analysis.json` (all numbers in machine-readable form) |

## 8. Pull reliability notes (for the next worker)

- **Yearly AND half-yearly TNP aggregates proxy-drop** after ~4–7 min of
  server compute (`Remote end closed connection`, 2 attempts each).
  Monthly is the survivable unit for TNP (2c proved it); taxi yearly
  aggregates (~6M rows, ~185 s) succeed. Two TNP jobs failed all 6
  attempts and were split into halves to finish (n26f valid 2024-03,
  6dvr series 2025-11). One `IncompleteRead` mid-download was also seen.
- Two runtime restarts killed the pull streams mid-flight; both resumed
  cleanly from the chunk files (skip-if-exists). Total failed attempts
  across the run: ~30, all recovered by retry/split except the two noted
  above.
- Per-chunk timings: monthly TNP valid 57–270 s; monthly TNP series
  75–270 s; taxi yearly valid 162–204 s; edge counts 1–71 s (taxi
  absurd 20–24 s); holiday aggregates 2–43 s.
