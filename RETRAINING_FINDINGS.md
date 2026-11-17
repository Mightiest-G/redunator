# Redunator — Monthly Retraining Automation (Phase 1, item 4a)

Built 2026-11-16 (Worker D). Script, promotion gate, demonstration
evidence, and the monthly schedule. This doc is the standalone record;
`REPORT.md`, `HOURLY_REPORT.md`, and `README.md` were NOT edited.

## 1. What was built

| Artifact | Path |
|---|---|
| Retrain script | `~/workspace/redunator/retrain_monthly.py` |
| Live-model pointer (new, points at v1) | `~/workspace/redunator/live_model.json` |
| Dry-run evidence | `~/workspace/redunator/retrain_runs/2026-11-16-dryrun/{promotion_report.md,promotion_report.json,run.log}` |
| Ad-hoc gate evidence | `~/workspace/redunator/retrain_runs/2026-11-16-dryrun-adhoc/{promotion_report.md,promotion_report.json,run.log}` |
| Monthly cron | job id `redunator-monthly-retrain` (see §5) |

`retrain_monthly.py` re-runs the pipeline end to end — ingest →
features → train → export — into a staging directory
(`retrain_runs/<YYYY-MM-DD>/staging/`), compares the candidate against
the shipped model on the SAME holdout, and promotes ONLY if the gate
passes. Every run writes `promotion_report.md` + `promotion_report.json`
+ `run.log` into its run directory. Any step failure writes a FAILED
report, exits non-zero, and promotes nothing (fail closed).

Usage:

```bash
# Full monthly run (downloads ~270 MB, retrains RF + XGBoost):
~/workspace/redunator-venv/bin/python retrain_monthly.py

# Dry run: no downloads, no training — re-scores the shipped model and
# exercises the promotion gate (see §4):
~/workspace/redunator-venv/bin/python retrain_monthly.py --dry-run

# Ad-hoc gate check (dry-run only):
~/workspace/redunator-venv/bin/python retrain_monthly.py --dry-run \
    --candidate-rmse 3.20 --candidate-mae 2.05
```

Flags: `--holdout-days N` (default 90), `--skip-export` (skip the
staging `export_models.py` step), `--run-dir NAME` (override the run
directory name).

## 2. Why staging copies with patched dates

Two facts about the existing pipeline forced the design:

1. **Hardcoded live paths.** `train.py`, `features.py`, and
   `export_models.py` all compute `BASE` from their own file location
   and write the model, sidecar, and metrics next to themselves.
   Running them in place would overwrite the shipped v1 files.
   The script therefore copies all four pipeline scripts into the
   run's staging directory and runs the copies there. The live
   scripts are never edited.
2. **Frozen split dates.** All three scripts hardcode train
   ≤ 2026-07-31 / test ≥ 2026-08-01. With fresh data, a fixed split
   would retrain on byte-identical train rows — `features.py`
   computes `cam_mean_pre_test` over pre-2026-08-01 data only — so
   the "retrain" would be a no-op producing a bit-identical model.
   Each run therefore uses a **rolling split that preserves the v1
   protocol** (time-based, never random; ~90-day holdout on the most
   recent data): after the staging ingest, the script reads the max
   violation date from the fresh CSV and sets
   `train_end = latest_date − 90 days`,
   `test_start = train_end + 1 day`. The staging copies are patched
   by regex on the date constants only (the patch raises if a pattern
   is not found, so a silent no-op patch is impossible). `features.py`
   gets its `TEST_START` patched too, so the camera aggregates still
   exclude the test window (no leakage), and its `holidays.US` year
   range is extended to cover the new data years.

The export step runs the staging copy of `export_models.py` (XGBoost
alternative + XGB ONNX + depth-12 RF file) and additionally exports
the staging winner to ONNX via `skl2onnx`, verified against native
predictions on 2,000 test rows. If the winner ONNX export fails, the
run still completes but promotes joblib + JSON only, flagged in the
report.

## 3. Promotion rule (baked into the script)

```
PROMOTE iff  candidate RMSE < shipped RMSE
         AND candidate MAE  < shipped MAE
         AND candidate RMSE < naive-baseline RMSE   (sanity guard)
```

- **Strict comparison — a tie refuses.** Implemented in
  `decide_promotion()` in `retrain_monthly.py`.
- **"Shipped" is re-scored, not recalled.** The shipped v1 model is
  re-scored on the candidate's own test set (same rows, same feature
  prep as `train.py`: `cam_code` fit on train cameras only, lag/rolling
  NaNs → 0, lat/lon NaN → train medians, predictions clipped at 0),
  so the comparison is apples-to-apples on the new, longer holdout —
  never a stale number from v1's shorter window. The scorer is
  `score_model_on_table()`; §4 shows it reproduces the recorded v1
  metrics exactly.
- **Promotion never overwrites v1.** A promotion copies the staging
  winner to versioned files — `redunator_brain_v2.joblib/.json/.onnx`
  (next version = max existing `vN` + 1; the script asserts the
  targets do not exist and refuses version ≤ 1) — and moves the
  `live_model.json` pointer to the new version. The v1 files keep
  their md5s (verified before and after every run) and remain the
  fallback.

## 4. Gate demonstration (evidence)

A full ingest + retrain was NOT run for this demonstration: it pulls
~270 MB and retrains RF (150 trees) + XGBoost on 1.5 shared CPUs.
Instead: a dry-run plus a real staging comparison against existing
metrics — the dry-run re-scores the shipped model on the live feature
table (real model, real data) and exercises the gate against real and
synthetic candidates. The full-run path's staging machinery was
additionally verified separately: staging copies with patched dates
(train_end 2026-08-15 / test_start 2026-08-16) all compile, the date
constants land in the right places, `ingest.py` stays an unpatched
copy, and `next_version()` returns 2.

### 4.1 Real staging comparison — scorer reproduces the shipped metrics

```
shipped model re-scored on live table: RMSE 3.3068 / MAE 2.1452
(recorded: RMSE 3.3068 / MAE 2.1452; test 2026-08-01 -> 2026-10-29,
26,752 rows)
rescore_reproduces_recorded: true
```

The scorer mirrors `train.py` exactly — if it did not, the gate
would be comparing numbers computed two different ways.

### 4.2 Gate tests (all as expected, exit 0)

Run: `retrain_monthly.py --dry-run` (2026-11-16-dryrun). Shipped
reference on the same test set: RMSE 3.3068 / MAE 2.1452. Naive
baseline: RMSE 4.7997 / MAE 2.9444.

| # | Candidate | RMSE / MAE | Decision | Expected? |
|---|---|---|---|---|
| 1 | Naive baseline (deliberately worse) | 4.7997 / 2.9444 | DO NOT PROMOTE | yes |
| 2 | Shipped metrics as candidate (exact tie) | 3.3068 / 2.1452 | DO NOT PROMOTE | yes |
| 3 | XGBoost alternative (worse on both) | 3.3613 / 2.1477 | DO NOT PROMOTE | yes |
| 4 | Synthetic better candidate | 3.2000 / 2.0500 | **PROMOTE** | yes |
| 5 | Synthetic mixed (RMSE better, MAE worse) | 3.2000 / 2.2000 | DO NOT PROMOTE | yes |

Test 5 is the important edge: a candidate that wins on the primary
metric alone still refuses, because the rule requires BOTH metrics.

### 4.3 Ad-hoc gate check

`--dry-run --candidate-rmse 3.20 --candidate-mae 2.05` → PROMOTE
(simulated only; dry-run never writes live files). Report in
`retrain_runs/2026-11-16-dryrun-adhoc/`.

### 4.4 Integrity

Shipped v1 md5s verified before and after all work, unchanged:
`redunator_brain_v1.joblib` 9162dcbe76bf842584bd6f208c8bcb99,
`redunator_brain_v1.onnx` 90b9160389dd76d945bc8428a9ec3bf0,
`redunator_brain_v1.json` 28c83534e67267e272108c582ed0c56b.
`ingest.py`, `features.py`, `train.py`, `REPORT.md`,
`HOURLY_REPORT.md`, `README.md` were not edited. Disk: 73 GB free;
one full run costs ~440 MB of staging (raw ~268 MB + parquet ~11 MB
+ models + ONNX), so retention is not urgent but old run directories
should be pruned if they accumulate.

## 5. Monthly schedule

Cron job **`redunator-monthly-retrain`** ("Redunator — monthly model
retrain"), created 2026-11-16 after checking the existing job list
(no overlapping retrain job existed). No goal owns Redunator, so the
job has no owner; it reports to the coordinator in its run message,
not directly to Gabriel.

- Schedule: **monthly, day 1, ~03:45 America/Chicago**
  (requested ~03:00 with `flexible_time: true`; the runtime shifted
  the minute to 03:45, as documented for flexible times).
- First run: **Tuesday 2026-12-01 03:45:00 CST** (15 days out at
  creation).
- Effective timeout: 86400 s (requested 7200 s; the runtime extends
  recurring jobs to the schedule interval, capped at 86400 s).
- Definition file:
  `~/workspace/cron.d/monthly/redunator-monthly-retrain__monthly@1-03:45:00_user_current.md`
  (read-only copy; change via `cron.view` / `cron.update`).
- The job body runs the full script with `TMPDIR` on the home disk
  (`~/tmp`, avoiding the 512 MB `/tmp` tmpfs), reads the promotion
  report, and reports PROMOTE / DO NOT PROMOTE / FAILED with the
  numbers. It forbids manual promotion, manual overrides of the gate,
  and any overwrite of the v1 files.

## 6. Open points / honest limits (for the coordinator)

1. **A promotion does not change what the product serves — yet.**
   `risk.py` / `hourly_risk.py` load `redunator_brain_v1.joblib` by
   name. A promoted v2 would sit beside v1 with `live_model.json`
   pointing at it, but nothing reads the pointer yet. Wiring the
   pointer into the serving code (or renaming on promotion) is a
   separate step for a later item — deliberately NOT done here, since
   it would change live behaviour.
2. **Full run never executed end to end.** The dry-run + scorer
   reproduction + staging-patch test cover the gate and the
   machinery, but the first real run (Dec 1) is the first live
   proof of ingest → features → train → export in staging. Watch
   its report. Known risk: if a brand-new camera appears with no
   pre-`train_end` history, `train.py`'s own assertion (test cameras
   ⊆ train cameras) fails the run — fail-closed by design, but it
   would need a handling decision (e.g. exclude the camera or roll
   the split differently) at that point.
3. **Data lag.** The portal publishes ~14 days late, so a 1st-of-the-
   month run trains on data ending roughly mid-previous-month. The
   rolling 90-day holdout still uses the freshest available data;
   no change needed, just stated.
4. **Hourly model untouched.** This automation covers the daily model
   only (`redunator_brain_v1`). The hourly model (`hourly_model.joblib`)
   has its own retrain scripts and is out of scope here.
5. **No pruning.** The script never deletes old run directories.
   If monthly ~440 MB runs accumulate, add a retention step (keep
   reports + metrics, prune raw/model files of runs older than N
   months) — not implemented, to avoid silent deletion in an
   automated job.
