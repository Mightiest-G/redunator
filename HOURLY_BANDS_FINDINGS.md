# Redunator Phase 1 (item 2) — Per-Hour Risk Bands: Findings

Built 2026-11-16 (Worker B). Standalone findings doc — the coordinator
merges docs at the end of Phase 1.

> **Every hourly number here is an ESTIMATE.** The daily red-light
> violation data has no hour-of-day, so no hourly violation count can
> ever be *measured*. The hourly shape comes from proxy signals
> (crashes near each camera, congestion, trip speed/demand) and the
> daily total always comes from the shipped Phase 2 daily model.
> The bands below label those estimates; they do not make them
> observations.

## 1. The new bands

| Band | Per-hour threshold | Meaning |
|---|---|---|
| **LOW** | predicted < **0.1** violations/hour | quieter than the median camera-hour citywide |
| **MEDIUM** | **0.1 ≤** predicted < **0.5** violations/hour | median hour → 90th percentile of camera-hours |
| **HIGH** | predicted ≥ **0.5** violations/hour | top decile of camera-hours |

The old banding — the Phase 2 **daily** thresholds (LOW < 4,
MEDIUM 4–<10, HIGH ≥ 10) applied to an hourly count — is kept
available as `banding="daily"` in `predict_hourly` for comparison.
The **daily** bands in `risk.py` are untouched and remain the right
unit for "is this a bad day".

## 2. Derivation — same rule as the daily bands, on the hourly population

`risk.py` derived the daily bands from the distribution of the
shipped daily model's predictions on the Phase 2 holdout
(2026-08-01 → 2026-10-29, 26,752 camera-days): LOW/MEDIUM =
round(p50), MEDIUM/HIGH = round(p90), so HIGH ≈ the top decile of
camera-days. The hourly bands use **exactly the same rule on the
hourly population**: the shipped *default* hourly output
(`predict_hourly` with default settings — model path, demand OFF)
for the same 26,752 holdout camera-days × 24 hours = **642,048
predicted camera-hours**.

Quantiles of predicted hourly counts over that population:

| Quantile | Value (violations/hour, ESTIMATE) |
|---|---|
| p50 | 0.1262 |
| p75 | 0.2664 |
| p90 | 0.4822 |
| p95 | 0.7103 |
| p99 | 1.5705 |
| mean | 0.2249 |
| max | 7.5992 |

- LOW/MEDIUM = round(p50) = **0.1**; MEDIUM/HIGH = round(p90) = **0.5**.
- Rounding is to **0.1 violations/hour (1 decimal)**, not whole
  violations as in `risk.py`, because hourly counts are ~24× smaller
  than daily counts — whole-number rounding would put the LOW/MEDIUM
  line at 0 and make every non-zero hour MEDIUM or higher.
- Score scale (recorded, not wired — see §6): p99 rounded up to the
  nearest 0.5 = **2.0**, i.e. an hourly risk score of
  `clip(predicted / 2.0, 0, 1)` would saturate at the 99th percentile
  of camera-hours, mirroring the daily score's `/ 35`.

Holdout shares under the new bands: **LOW 42.0%, MEDIUM 48.6%,
HIGH 9.4%** — HIGH is again about the top decile, as designed for
the daily bands (which were LOW 54.2% / MEDIUM 36.0% / HIGH 9.9%).

### Why pooled quantiles, not per-camera-relative bands

The bands are **citywide-relative**, like the daily bands: an hour
is HIGH if it is busier than ~90% of all camera-hours in the city,
not busier than that camera's own usual hours. The alternative
(per-camera quantiles) would give every camera HIGH hours by
construction and destroy cross-camera comparability — the same
reason the daily bands are pooled. Consequence, stated plainly:
at the hottest cameras even the overnight trough can read MEDIUM
(§4, cam 1413's 03:00 = 0.28 → MEDIUM); at a typical camera the
same hour reads LOW (cam 1412's 03:00 = 0.02 → LOW). That is the
intended semantics — "this hour at this camera is busier than most
hours at most cameras" — not "this hour is busy for this camera".

### Sensitivity — the thresholds are stable across paths

| Population | p50 | p90 | p99 | Rounded cuts |
|---|---|---|---|---|
| Default output (model path; the derivation population) | 0.1262 | 0.4822 | 1.5705 | 0.1 / 0.5 |
| Model path only | 0.1262 | 0.4822 | 1.5705 | 0.1 / 0.5 |
| Profile path only (profile + weather + slowness) | 0.1176 | 0.5115 | 1.6927 | 0.1 / 0.5 |

Both paths round to the same cuts, so the bands do not depend on
which hourly path produced the numbers. (The two rows are identical
for the default population because all 300 holdout cameras have
model weights — none of the 4 coordinate-less cameras, which would
fall back to the profile path, is in the holdout.)

### Reproducibility

Script: `derive_hourly_bands.py` (run: `~/workspace/redunator-venv/bin/python derive_hourly_bands.py`, ~30 s).
Numbers: `data/processed/hourly_risk_thresholds.json`.
Calling `predict_hourly()` 26,752 times was not feasible (~10 s/call,
~3 days — each call rebuilds the Poisson matrices and reloads both
models), so the script loads the daily model, the hourly (B12) model,
and the Poisson matrices once and builds the full design matrix
vectorised, mirroring `hourly_model.model_weights()` feature for
feature. Equivalence is asserted, not assumed: the vectorised output
reproduces live `predict_hourly()` hourly values on a 7-camera-day
sample to max |Δ| = **7.2e-16** (see §5). One subtlety found while
verifying: dividing temperature/wind by 10 in float32 instead of
float64 flips a few XGBoost split decisions and moves weights by up
to 0.0008 — the script divides in float64 first, then casts, exactly
as `model_weights()` does.

## 3. Implementation — `hourly_risk.py` only

- `risk_band(hourly_pred, banding="hourly")`: `"hourly"` (default)
  uses LOW < 0.1 / MEDIUM 0.1–<0.5 / HIGH ≥ 0.5;
  `"daily"` uses the old Phase 2 daily thresholds (LOW < 4 /
  MEDIUM 4–<10 / HIGH ≥ 10). Unknown `banding` raises `ValueError`.
- `predict_hourly(..., banding="hourly")`: new keyword argument;
  the result dict gains a `"banding"` field. **Banding changes only
  the band labels — never the predictions** (asserted in §5).
- Thresholds are loaded from
  `data/processed/hourly_risk_thresholds.json` at import, with the
  derived values (0.1 / 0.5) as hard-coded fallback if the file is
  ever missing. The JSON is the auditable record; the code and the
  file agree.
- CLI: `python hourly_risk.py 1413 2026-10-15 [nodemand] [daily]`
  — the optional 4th argument selects the old banding for display.
- **Nothing else changed.** `risk.py`, the daily model files, the
  hourly model, the profiles, and every other script are untouched.
  `risk_scores` in the result are still hourly / 35 (the Phase 2
  daily scale) — see the caveat in §6.

## 4. Worked example — camera 1413, Thursday 2026-10-15 (model path, demand OFF)

Daily prediction 53.01 (actual 50 that day). Hourly values sum to
53.0096; peak **15:00, 4.82**. All 24 hours, new band vs old band:

| Hour | Predicted | New band | Old band | Hour | Predicted | New band | Old band |
|---|---|---|---|---|---|---|---|
| 00:00 | 0.68 | HIGH | LOW | 12:00 | 3.34 | HIGH | LOW |
| 01:00 | 0.52 | HIGH | LOW | 13:00 | 3.18 | HIGH | LOW |
| 02:00 | 0.38 | MEDIUM | LOW | 14:00 | 3.60 | HIGH | LOW |
| 03:00 | 0.28 | MEDIUM | LOW | **15:00** | **4.82** | **HIGH** | MEDIUM |
| 04:00 | 0.31 | MEDIUM | LOW | 16:00 | 4.54 | HIGH | MEDIUM |
| 05:00 | 0.53 | HIGH | LOW | 17:00 | 4.53 | HIGH | MEDIUM |
| 06:00 | 0.83 | HIGH | LOW | 18:00 | 2.51 | HIGH | LOW |
| 07:00 | 2.69 | HIGH | LOW | 19:00 | 2.34 | HIGH | LOW |
| 08:00 | 3.09 | HIGH | LOW | 20:00 | 2.29 | HIGH | LOW |
| 09:00 | 2.76 | HIGH | LOW | 21:00 | 1.84 | HIGH | LOW |
| 10:00 | 2.65 | HIGH | LOW | 22:00 | 1.47 | HIGH | LOW |
| 11:00 | 2.69 | HIGH | LOW | 23:00 | 1.14 | HIGH | LOW |

New counts: **21 HIGH / 3 MEDIUM / 0 LOW**. Old counts: 0 HIGH /
3 MEDIUM / 21 LOW. **24 of 24 hours change band, all upward.**
The peak hour finally reads HIGH — under the old banding the
hottest camera in the city topped out at MEDIUM.

Two more cameras the same day, for contrast (model path, demand OFF):

- **1412** (same intersection, quiet camera; daily 4.61, actual 7):
  new **8 LOW / 16 MEDIUM / 0 HIGH** (old: 24 LOW). Overnight
  00:00–06:00 (0.02–0.07) plus 23:00 (0.10) all read LOW; daytime
  07:00–22:00 (0.13–0.44) reads MEDIUM; peak 15:00 = 0.44 MEDIUM.
  This is the "dead overnight hour reads LOW" behaviour — it holds
  at a typical camera.
- **2251** (Cicero & I-55; daily 46.82, actual 39): new **21 HIGH /
  3 MEDIUM / 0 LOW** (old: 24 LOW). Only 02:00–04:00 (0.37–0.43)
  read MEDIUM; every other hour, including 00:00 (0.63) and 23:00
  (1.07), reads HIGH. Peak 16:00 = 3.97 HIGH (15:00 = 3.93 HIGH).

Machine-readable: `hourly_bands_worked_example.json`
(1413, all 24 hours with both bandings + holdout transition counts).

## 5. Comparison vs old banding + verification

### Holdout-wide (all 642,048 camera-hours)

| Transition | Camera-hours | Share |
|---|---|---|
| LOW → LOW (unchanged) | 269,684 | 42.0% |
| LOW → MEDIUM | 312,207 | 48.6% |
| LOW → HIGH | 59,220 | 9.2% |
| MEDIUM → HIGH | 937 | 0.1% |
| Any other transition | 0 | 0% |
| **Changed band** | **372,364** | **58.0%** |

All changes are upward (the new thresholds are strictly lower than
the daily ones, so no hour can move down). Under the old banding,
**zero** of the 642,048 holdout camera-hours was HIGH; under the
new bands 60,157 (9.4%) are. Under the old banding only 937 hours
(0.1%) were even MEDIUM.

### Verification (script `verify_hourly_bands.py` — all asserted, all pass)

1. **Daily model files byte-identical** to the Phase 2 baseline:
   `redunator_brain_v1.joblib` md5 `9162dcbe76bf842584bd6f208c8bcb99`,
   `.onnx` `90b9160389dd76d945bc8428a9ec3bf0`, `.json`
   `28c83534e67267e272108c582ed0c56b`. `risk.py` untouched
   (mtime 2026-11-15, before this work).
2. **Anchor reproduces:** `predict_hourly('1413', '2026-10-15')`
   default still sums to **53.0096**, peak **15:00 at 4.82**,
   method `"model"` — exactly the coordinator-verified values.
3. **Banding changes labels only:** `banding="daily"` returns hourly
   values identical to the default call (max |Δ| < 1e-9; the residual
   is the known ~1-ulp RF daily-total wobble between runs, HOURLY_REPORT §9.4).
4. **Derivation equivalence:** vectorised derivation vs live
   `predict_hourly()` on 7 camera-days (1413/2251/1412/1514/1513/2252
   on 2026-10-15, 1413 on 2026-10-17): worst max |Δ| = **7.2e-16**.
5. **Holdout band-change counts** as tabulated above, computed from
   the derivation population.

## 6. Caveats / open items (for the coordinator)

- **Estimates, not observations** — as everywhere in the hourly
  layer. A HIGH hour means "the model estimates this hour at ≥ 0.5
  violations", where the estimate is a crash-proxy shape applied to
  a daily total. Do not present band labels as measured hourly
  violations.
- **Hot cameras never read LOW.** By design (pooled bands): 1413's
  quietest hour (03:00, 0.28) is MEDIUM, and 2251 has no LOW hour
  at all on 2026-10-15. If a use case needs "quiet for *this*
  camera", that is a different (per-camera-relative) banding, not
  this one.
- **Hourly risk scores now use the hourly / 2.0 scale (wired 2026-11-17).**
  `risk_scores` are `clip(hourly / 2.0, 0, 1)` in both `hourly_risk.py`
  (`HOURLY_SCORE_SCALE`) and `live_adjust.py`, so the hottest hour in
  the city scores 1.0 and a typical hour ~0.1. The old daily / 35 scale
  made the hottest hour ~0.14 and a typical hour ~0.01.
- **Re-derive if the hourly model changes.** The thresholds are tied
  to the shipped B12 model's output distribution on the holdout. A
  future hourly-model retrain or shape change should re-run
  `derive_hourly_bands.py` and check whether 0.1 / 0.5 still hold.
  (The profile path rounds to the same cuts today, so a path switch
  alone does not move them.)
- **Rounding-boundary note:** p90 = 0.4822 rounds to 0.5, and the
  profile-path p90 = 0.5115 also rounds to 0.5 — both paths agree,
  but the pooled p90 sits close enough to the 0.45 boundary that a
  materially different derivation population could in principle
  round it to 0.4. The sensitivity table above is the evidence
  against that on the populations that matter.
- **Historical scripts will now emit new bands if re-run.**
  `worked_example_hourly.py`, `worked_example_demand.py`, and
  `worked_example_2d.py` read `r["bands"]` from `predict_hourly`,
  so re-running them produces the new band labels in their printed
  tables and stored JSONs. Their stored files were **not** rewritten
  here — they remain the record of what was published at the time.
  If the coordinator wants them refreshed, that is a separate
  re-run, not part of this item.

## 7. Files

| File | What it is |
|---|---|
| `HOURLY_BANDS_FINDINGS.md` | This doc |
| `hourly_risk.py` (edited) | New per-hour bands by default; `banding="hourly"` / `"daily"`; thresholds loaded from JSON with hard-coded fallback |
| `data/processed/hourly_risk_thresholds.json` | Derived quantiles, thresholds (0.1 / 0.5), score scale (2.0), band shares, sensitivity |
| `derive_hourly_bands.py` (new) | Derivation script — vectorised holdout hourly predictions, quantiles, thresholds |
| `verify_hourly_bands.py` (new) | Verification script — md5s, anchor, banding-labels-only, derivation equivalence, holdout transitions, worked example |
| `hourly_bands_derivation.json` | Full derivation numbers (same content as the thresholds JSON) |
| `hourly_bands_worked_example.json` | Worked example: 1413, 2026-10-15, 24 hours under both bandings + holdout transitions |
| `risk.py` | **Untouched** — daily bands exactly as they were |
| `REPORT.md`, `HOURLY_REPORT.md`, `README.md` | **Untouched** — coordinator merges docs |
