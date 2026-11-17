# Peak-bias investigation — 3pm vs 4pm (Phase 1, Item 3)

Worker C, 2026-11-16. Standalone findings doc for the coordinator; no
code was changed and no file other than this one was written.

> **All hourly numbers here are ESTIMATES from proxies, never observed
> hourly violations.** The target in every test below is hourly *crash*
> counts within 200 m of a camera (the proxy), on the Phase 2 holdout
> 2026-08-01 → 2026-10-29 (779,760 camera-hours, 8,517 observed
> attributed crashes) unless stated otherwise.

## Verdict in one paragraph

**There is no systematic one-hour bias to fix, and no fix was
implemented.** The observed crash distribution is a nearly flat
plateau across 15:00–17:00, and which hour wins the argmax flips
month to month. The observed holdout gap itself (15:00: 738 crashes
vs 16:00: 678) is only ~1.6σ. The profile path already peaks at the
right hour (15:00); the XGBoost model (B12) peaks at 16:00 by a
0.0012 share margin, but shifting its shape one hour earlier makes
holdout deviance *worse* (0.093805 vs 0.093260), and removing the
features that tip it late also makes deviance worse. The peak-hour
label is a near-tie artifact, not a shape error. Recommendation:
describe the peak as a "15:00–16:00 plateau" rather than a single
hour (HOURLY_REPORT.md §3 already notes the exact peak hour "is not
pinned down to better than ±1 hour by either path").

## Baseline numbers (reproduced)

| Quantity | Value |
|---|---|
| Observed holdout peak (attributed crashes) | 15:00, share 0.0867 (738 crashes); 16:00 0.0796 (678); 17:00 0.0808 |
| Observed full-window peak (citywide crashes) | 15:00, share 0.0803; 16:00 0.0778; 17:00 0.0731 |
| Profile-only (pre-holdout) | deviance 0.094549, peak 15:00 (0.0770 vs 0.0767 at 16:00) |
| Profile + slowness (wired path) | deviance 0.094390, peak 16:00 (§9.3 — slowness peaks 17:00) |
| XGBoost B12 raw / shape | deviance 0.093203 / 0.093260, peak 16:00 (0.0791 / 0.0803 raw) |
| Worker-B model (v2b) on its Mar–Apr 2026 test | deviance 0.087929, peak **15:00** (0.0803 vs 0.0796) — correct |

## H1 — Timezone / timestamp bucketing: RULED OUT

Checked exactly how hours are extracted at each step. No conversion
is applied anywhere — and none is needed, because every source is
already Chicago-local.

- **Crashes.** `hourly_ingest.py::step_crashes` pulls `crash_date`
  and the city's own derived `crash_hour` column from Socrata
  `85ca-t3if`; `step_aggregate` floors `crash_date` to the hour.
  Test on the raw file (`data/hourly/raw/crashes_20250101_20261029.csv`,
  202,151 rows): `crash_date.hour − crash_hour ≡ 0` on **all
  202,151 rows**, in every month. If the API timestamps were UTC,
  `crash_hour` (computed by the city from local time) would disagree
  by 5–6 hours on every row. It never does.
- **Congestion.** Same check on
  `congestion_segments_hourly_raw.csv` (1,947,880 rows):
  `timestamp.hour − hour ≡ 0` on **all rows**.
- **Weather.** Open-Meteo was requested with
  `timezone=America/Chicago`; the file's diurnal temperature cycle
  (mean min 05:00, 9.2 °C; mean max 14:00, 14.7 °C) is a local-time
  cycle. (It is a naive 24-row/day grid — a fake 02:00 row exists on
  the two spring-forward days — but that affects 2 days and only the
  weather multipliers, not the crash target.)
- **Trips (demand / speed).** No derived hour column exists in the
  taxi/TNP tables to cross-check, but the shapes are unambiguous
  local-time patterns: demand peaks 08:00 (morning commute) and
  18:00 (evening); trip speed troughs 16:00–17:00 (afternoon rush).
  Under a UTC interpretation the trip peak would be noon local and
  the crash peak 09:00–10:00 local — implausible, and contradicted
  by the city's own hour columns above.

What the observed peak would be under each interpretation:
- As ingested (Chicago local): **15:00** (0.0867 holdout / 0.0803
  full window).
- If the timestamps had been UTC and converted to Chicago:
  15:00 UTC = 09:00 CST / 10:00 CDT — i.e. the peak would move to
  **09:00–10:00**, and the 02:00 trough would move to ~20:00–21:00.
  Nothing in any source supports that reading.

**H1 verdict: no timezone error. The 15:00 observed peak is a real
local-time peak.**

## H2 — Profile construction: NO BUG

`hourly_model.build_profiles` takes the hour straight from the
crash parquet (`df["timestamp"].dt.hour`); there is no index shift,
no rolling/smoothing kernel over hours (Dirichlet smoothing is
per-hour toward the citywide share, `K = 30`, and cannot move a
peak across hours), and no lag.

- Crash-only profile (K = 30, congestion blend removed, rebuilt
  from pre-holdout crashes): deviance **0.094839**, peak **15:00**
  with margin +0.0014 over 16:00 (shares 0.0811 vs 0.0797).
- Shipped pre-holdout profile (with congestion blend): deviance
  **0.094549**, peak **15:00** with margin +0.0003 (0.0770 vs
  0.0767).
- The congestion share itself peaks at **16:00** (citywide mean
  congestion share: 15:00 0.0724, 16:00 0.0767, 17:00 0.0757;
  segment mean speed 23.2 mph at 15:00 vs 23.0 at 16:00–17:00).
  The blend therefore *narrows* the profile's 15:00 margin but
  does not flip it — and it improves deviance (0.094549 vs
  0.094839), so the blend is a net win, not a defect.

**H2 verdict: no off-by-one, no smoothing artifact. The profile
path gets the peak right; the congestion blend mildly pulls it
late and still helps on the primary metric. No fix.**

## H3 — Model-path artifacts: NEAR-TIE, NOT A LEARNED BIAS

The XGBoost model's hour features are `hour_sin` / `hour_cos`
(cyclical, no off-by-one) plus `speed_ratio`, weather, and the two
log crash-rate features. There is no lagged feature that could
shift the peak.

Evidence the model did **not** learn a wrong peak from its target:

- The worker-B model predicted **15:00** on its own Mar–Apr 2026
  test window (deviance 0.087929; predicted 15:00 0.0803 vs 16:00
  0.0796) — matching the *attributed* observed peak there
  (15:00 0.0854 vs 16:00 0.0848). (Note: the *citywide*
  unattributed Mar–Apr peak was 16:00, 0.0832 vs 0.0827 — the
  model's target is the attributed distribution, which peaked at
  15:00.)
- The worker-B model also predicted **15:00** in-sample on its own
  train window (Jan 2025–Feb 2026; predicted 0.0790 vs 0.0787;
  observed 0.0806 vs 0.0784).
- The B12 model predicts 16:00 **in-sample on its own train
  window too** (Jan 2025–Jul 2026; predicted 0.0798 vs 0.0796 —
  a 0.0002 margin) while its observed train peak is 15:00
  (0.0813 vs 0.0799). The only difference between B12 and the
  worker-B model is the training window: the five extra months
  (Mar–Jul 2026) themselves peaked 16:00 in Mar and Jul and
  17:00 in Jun. The extra data tipped a coin-flip argmax.

Ablations on the holdout (B12 model, same design construction;
baseline raw deviance 0.093203, peak 16:00, 15:00 0.0791 vs
16:00 0.0803):

| Ablation | Deviance | Peak | 15:00 / 16:00 shares |
|---|---|---|---|
| Baseline (as shipped) | 0.093203 | 16:00 | 0.0791 / 0.0803 |
| `speed_ratio` := 1.0 | 0.093258 | 16:00 | 0.0807 / 0.0819 |
| Weather features := 0 | 0.093710 | 15:00 | 0.0788 / 0.0784 |
| `speed_ratio` = 1.0 + weather = 0 | 0.093843 | 15:00 | 0.0869 / 0.0861 |

- The congestion-speed feature is **not** the cause: removing it
  leaves the peak at 16:00.
- The weather features **do** contribute to the late tip (zeroing
  them moves the peak to 15:00), but they earn their keep: removing
  them costs +0.0005 deviance. They are not a defect to remove.
- **Naive peak fix, tested and rejected:** circularly shifting the
  B12 shape −1 h gives deviance **0.093805** vs 0.093260
  unshifted (+0.0005 worse); shifting +1 h gives 0.094019. The
  model's shape is deviance-optimal as-is; the peak-hour label is
  cosmetic.

**H3 verdict: the model has no systematic one-hour bias. Its
16:00 peak is a 0.0012-share argmax loss on a plateau, inherited
from training months that themselves peaked late. No fix —
every candidate fix loses on the primary metric.**

## H4 — Attribution-radius effect: RULED OUT

Recomputed the attributed crash-hour distribution from the raw
crash file at three radii (per camera-hour hit counts, same
haversine join as `step_aggregate`):

| Radius | Holdout events | Holdout peak | 15:00 / 16:00 / 17:00 shares |
|---|---|---|---|
| 100 m | 5,030 | **15:00** | 0.0847 / 0.0803 / 0.0823 |
| 200 m (shipped) | 8,517 | **15:00** | 0.0867 / 0.0796 / 0.0808 |
| 400 m | 16,961 | **15:00** | 0.0845 / 0.0818 / 0.0770 |

Full window: 100 m peaks 17:00 by a 0.0001 tie (0.0804 vs 0.0803
at 15:00); 200 m and 400 m peak 15:00. The peak does not move
with radius.

**H4 verdict: the radius does not cause the peak discrepancy.**

## Why the "bias" looks real: the plateau

The underlying hour distribution is flat-topped at 15:00–17:00,
so the argmax is noise-sensitive:

- Monthly argmax over 22 months (citywide crashes): 15:00 wins
  13, 16:00 wins 6, 17:00 wins 2, 08:00 wins 1 (Jan 2026, an
  outlier month). No seasonal pattern: Aug–Oct months average
  a 15:00 share of 0.0814 vs 0.0798 for other months.
- Holdout 15:00 vs 16:00: 738 vs 678 crashes, difference 60,
  SE ≈ 38, **z ≈ 1.6 — not significant at 95%**. The observed
  peak hour itself is a ~1.6σ statement on this holdout.
- Full window 15:00 vs 16:00: 16,055 vs 15,555, z ≈ 2.8 — real
  but small (a 3% relative gap over 22 months).
- Both paths under-predict the holdout 15:00 share (profile
  0.89×, model 0.91×) because the holdout's 15:00 share (0.0867)
  is sharper than the pre-holdout training share (0.0813) —
  a ~1.9σ fluctuation, not a modelling error either path could
  have foreseen.

## What was NOT done

- No code was changed. No candidate fix survived validation, so
  there was nothing to ship and nothing to revert. In particular
  `hourly_risk.py` was not touched (another worker owns it) and
  the daily model files were not touched.
- A retrain with a different hour encoding (e.g. one-hot hour) or
  a month/season feature was considered and **not** attempted: the
  month-to-month 15:00 share shows no seasonal structure to learn
  (range 0.070–0.087 with no pattern), and any variant selected
  on this same holdout would be selection on noise (the same
  caveat §9.7 already records for B12).

## Recommendation (for the coordinator)

1. **No fix.** Close Item 3 as investigated, cause: none — the
   discrepancy is an argmax artifact on a near-tie plateau.
2. **Presentation:** where the product reports a peak hour, say
   "peaks 15:00–16:00" (or "afternoon peak 15:00–17:00") rather
   than a single hour, on both paths. The model path's 16:00 is
   within the plateau and within noise of the observed 15:00.
3. **Do not** shift the model shape, drop the weather features,
   or drop the congestion blend to "fix" the peak — each was
   tested and each loses on holdout deviance (see tables above).
4. If a future holdout (e.g. Nov 2026 crashes, when published)
   shows the same 15:00-vs-16:00 split with a larger gap, revisit
   — a second independent holdout is the only evidence that could
   turn this from noise into a real bias.

## Probe scripts

- `/tmp/peak_bias_probe.py` — model ablations, profile
  crash-only vs blended, worker-B model on its own test window
  and in-sample (scratch; not a deliverable).
- Radius + timezone checks were inline one-off scripts; results
  are recorded in the tables above.
