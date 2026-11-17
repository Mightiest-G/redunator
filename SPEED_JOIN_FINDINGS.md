# Speed-camera join — findings (Redunator Phase 1, Item 1)

Worker A, 2026-11-16. Standalone findings note for the Phase 1
coordinator. This doc does NOT edit REPORT.md, HOURLY_REPORT.md, or
README.md. Code: `speed_features.py` (join + speed features),
`train_v2.py` (evaluation). Artifacts:
`data/processed/speed_camera_join.csv`,
`data/processed/red_light_features_speed.parquet`,
`data/processed/model_metrics_v2_speed.json`.

## Verdict up front

**Speed features do NOT beat the v1 bar. Nothing shipped as v2.**
Best RF variant (spd500, nearest speed camera within 500 m):
full-holdout RMSE 3.3046 / MAE 2.1447 vs the bar 3.3068 / 2.1452 —
a −0.07% / −0.02% difference, i.e. noise. And that hair of gain is
not speed information at all: the staleness control (v1 features +
`speed_age_days` only, no speed data whatsoever) scores 3.3057 /
2.1441 on the same full holdout, reproducing — and on MAE beating —
the speed variants' October gains. The gain is a date effect of the
carry-forward flag, not a speed signal. On the clean fresh window
(2026-08-01 → 2026-09-24, no staleness anywhere) no variant wins
both metrics: spd500 −0.16% RMSE / −0.05% MAE, spd200 0.00% /
−0.05%, spd_city +0.05% / +0.17% (worse), spd_all −0.26% RMSE but
+0.09% MAE. XGBoost agrees: no speed variant beats the XGB baseline on the
fresh window on both metrics (spd_city and spd_all improve fresh
RMSE but worsen fresh MAE), and none comes near the RF bar — best
XGB full RMSE is spd_all's 3.3280 vs the bar's 3.3068. **Documented failed experiment; no v2 files
exist.**

## 1. The join

### 1.1 What was joined

| Source | Rows | Cameras | Window |
|---|---|---|---|
| Red-light violations (`data/raw/red_light_violations.csv`) | 1,175,909 | 365 (361 with coords) | 2014-07-01 → 2026-10-29 |
| Speed violations (`data/raw/speed_violations.csv`) | 504,853 (5 null-camera rows dropped, unattributable — same rule as `features.py`) | 236 (all with coords) | 2014-07-01 → **2026-09-24** |

Camera IDs differ between the files (`1233` style vs `CHI041`
style), so the join is spatial, not on ID.

### 1.2 Spatial nearest neighbour (the join that ships in the features)

Per-camera median lat/lon on both sides; nearest speed camera by
haversine-equivalent local-metre projection
(`x = lon·cos(41.8781°)·111320`, `y = lat·111320`), cKDTree.
4 red-light cameras (1623, 2552, 2592, 2662) have no coordinates
anywhere and get no neighbour — same caveat as v1.

Nearest-speed-camera distance for the 361 red-light cameras with
coordinates: **median 692 m**, p10 128 m, p25 279 m, p75 1,216 m,
p90 1,601 m, max 4,340 m.

| Radius | Red-light cameras with a neighbour | Share | Distinct speed cameras used |
|---|---|---|---|
| ≤ 100 m | 15 | 4.2% | 9 |
| ≤ 150 m | 53 | 14.7% | — |
| ≤ **200 m** | **68** | **18.8%** | **37 of 236** |
| ≤ 250 m | 80 | 22.2% | — |
| ≤ 300 m | 96 | 26.6% | — |
| ≤ **500 m** | **132** | **36.6%** | **67 of 236** |
| ≤ 750 m | 195 | 54.0% | — |
| ≤ 1,000 m | 248 | 68.7% | 98 of 236 |
| ≤ 1,500 m | 314 | 87.0% | — |
| ≤ 2,000 m | 353 | 97.8% | — |

Reverse check: median distance from a speed camera to its nearest
red-light camera is 447 m; 70/236 speed cameras (29.7%) have a
red-light camera within 200 m, 129/236 (54.7%) within 500 m.

**Radius justification.** 200 m is one Chicago block (1/8 mile =
201 m) — the distance within which two cameras plausibly watch the
same traffic on the same corridor — and it is the same radius the
hourly layer already uses for crash attribution (200 m), so the
project stays consistent with itself. 500 m (≈2.5 blocks) is the
wider net carried as a sensitivity variant: more coverage (36.6%
vs 18.8%) at the cost of a weaker "same traffic" claim. 1,000 m
was rejected as a join radius — at a kilometre the neighbour is
on a different corridor and the match is geography, not traffic —
but the correlation diagnostic below was still run at 1,000 m to
show the signal does not appear even there.

### 1.3 Address / intersection matching — tested and rejected

After normalising addresses (upper-case, strip street-type
suffixes: AVE/ST/ROAD/DR/…), **ZERO of 361 red-light cameras
exactly match a speed-camera address**. This is structural, not a
normalisation failure: red-light addresses are *intersection*
addresses ("2200 S WESTERN AVENUE", "6400 W IRVING PK"), speed
addresses are *mid-block point* addresses ("4124 W FOSTER AVE",
"5120 N PULASKI"). They name different things — a red-light
camera sits at a crossing, a speed camera sits mid-block, usually
near a school or park — so no address join exists to find. The
spatial join is the only defensible one, and its coverage is what
§1.2 reports. (The red-light ADDRESS field is also truncated at
25 chars in the source; only 3 of 236 speed addresses hit 25 chars,
so truncation is not the cause of the zero matches.)

### 1.4 Pre-train signal diagnostic (why expectations were low)

Per-camera Pearson correlation between a red-light camera's daily
violations and its nearest speed camera's daily violations
(full overlapping window 2014-07-01 → 2026-09-24, zero-filled
days, max-dedup):

| Radius | n cameras | Same-day corr (mean / median) | Lag-1d corr, speed yesterday vs red-light today (mean / median) | 7-day rolling corr (mean) |
|---|---|---|---|---|
| ≤ 200 m | 68 | 0.020 / 0.003 | **0.024 / 0.020** | 0.047 |
| ≤ 500 m | 132 | 0.022 / 0.008 | 0.023 / 0.018 | 0.042 |
| ≤ 1,000 m | 248 | 0.033 / 0.019 | 0.033 / 0.027 | 0.058 |

Scale: the same cameras' OWN lag-1 autocorrelation is **0.37**
(mean). Citywide daily speed total vs a camera's violations,
lag-1: 0.069. Citywide daily totals correlate 0.461 same-day /
0.443 lag-1 — the two series share calendar/seasonality, but at
the per-camera level the neighbour's speed history carries almost
no information about tomorrow's red-light count beyond what the
camera's own history already says. The train step (§3) is the
real test; this diagnostic only says not to expect much.

## 2. Speed features built

`speed_features.py` writes
`data/processed/red_light_features_speed.parquet` (1,358,095 rows,
40 cols = the v1 table + speed columns). `features.py` and
`train.py` were not edited; the v1 parquet is untouched.

Per red-light camera, the matched speed camera's daily series
(max-dedup per camera-day, missing day = 0 — the `features.py`
convention) is mapped onto the red-light camera's grid, then:

- `spd{R}_lag_1d`, `spd{R}_roll_mean_7d`, `spd{R}_roll_mean_30d`
  — shifted (past only), computed WITHIN each
  (`camera_id`, `segment_id`) exactly like the red-light lags, so
  a segment restart resets the speed lags too. NaN at segment
  starts (407 rows, same rows as the red-light lags) filled with 0
  at train time, same as v1.
- `spd{R}_mean_pre_test` — matched camera's mean daily speed
  before 2026-08-01 (0 if no neighbour).
- `has_spd{R}` — 0/1 neighbour flag; `spd{R}_dist_m` — neighbour
  distance in metres (9999 if none, so "no neighbour" is a distinct
  extreme the forest can split on).
- `spd_city_*` — the same four columns for the **citywide daily
  speed total** (sum over all 236 speed cameras; no neighbour
  needed, so every camera gets it).

Built for R = 200 and R = 500 separately, plus citywide.
`speed_age_days` — see §4.

## 3. Holdout evaluation

Same time-based split as v1 (never random): train 2014-07-01 →
2026-07-31 (1,331,343 rows), test 2026-08-01 → 2026-10-29
(26,752 rows). Same hyper-parameters as v1 (RF: 150 trees,
max_depth=12, max_features=0.5, min_samples_leaf=5; XGB: 600
rounds, depth 8, lr 0.05). Predictions clipped at 0, same as v1.

**Bar (v1, full holdout): RF RMSE 3.3068 / MAE 2.1452.**
The same-run baseline in `train_v2.py` reproduces it exactly
(full-window RF RMSE 3.3068 / MAE 2.1452), so the comparison
pipeline is verified against the shipped model.

### 3.1 Primary window — fresh speed (2026-08-01 → 2026-09-24, 16,445 rows)

Every speed lag/roll window here is fully inside observed speed
data. Baseline and speed variants are compared on these SAME rows.
Target mean on this window: 5.421 (vs 5.263 on the full holdout —
August/September run slightly hotter).

| Variant | RF fresh RMSE | RF fresh MAE | Δ RMSE vs baseline | XGB fresh RMSE | XGB fresh MAE |
|---|---|---|---|---|---|
| baseline (v1 features) | 3.3504 | 2.1853 | — | 3.4077 | 2.1833 |
| spd500 (≤ 500 m neighbour) | 3.3450 | 2.1841 | −0.16% / −0.05% | 3.4432 | 2.1957 |
| spd200 (≤ 200 m neighbour) | 3.3504 | 2.1843 | 0.00% / −0.05% | 3.4129 | 2.1844 |
| spd_city (citywide speed) | 3.3520 | 2.1891 | +0.05% / +0.17% | 3.3942 | 2.1902 |
| spd_all (500 m + citywide) | 3.3417 | 2.1873 | −0.26% / +0.09% | 3.3841 | 2.1861 |
| spd_age_only (control, §3.2.1) | 3.3505 | 2.1851 | +0.00% / −0.01% | 3.4207 | 2.1913 |

Reading: on the only window where speed data is actually fresh,
**no variant wins both metrics**, and the largest move in either
direction is a quarter of a percent. spd_all's RMSE gain comes
with a worse MAE; spd_city is worse on both. The control is
identical to baseline here by construction (`speed_age_days` is
constant 0 before 2026-09-25), which is the point — it isolates
what the flag does in October.

### 3.2 Full holdout — carry-forward October (2026-08-01 → 2026-10-29, 26,752 rows)

| Variant | RF full RMSE | RF full MAE | Δ vs bar (3.3068 / 2.1452) | XGB full RMSE | XGB full MAE |
|---|---|---|---|---|---|
| baseline (v1 features) | **3.3068** | **2.1452** | — (reproduces the bar exactly) | 3.3613 | 2.1477 |
| spd500 | 3.3046 | 2.1447 | −0.07% / −0.02% | 3.4026 | 2.1576 |
| spd200 | 3.3053 | 2.1434 | −0.05% / −0.08% | 3.3920 | 2.1494 |
| spd_city | 3.3076 | 2.1461 | +0.02% / +0.04% | 3.3480 | 2.1510 |
| spd_all | 3.3041 | 2.1448 | −0.08% / −0.02% | 3.3280 | 2.1462 |
| **spd_age_only (control)** | **3.3057** | **2.1441** | **−0.03% / −0.05%** | 3.3780 | 2.1526 |

#### 3.2.1 The control kills the "win"

The control is v1's 17 features plus `speed_age_days` and **no
speed data at all**. It scores 3.3057 / 2.1441 on the full holdout
— essentially the same as spd500 (3.3046 / 2.1447) and spd200
(3.3053 / 2.1434), and better than spd500 on MAE. `speed_age_days`
is 0 through 2026-09-24 and then ramps 1…35, so in October it is
a "days since Sep 24" date proxy; the forest uses it to fit the
October regime (target mean, seasonal drift) slightly better than
the baseline can. The speed variants' full-window gains are that
date effect wearing a speed costume: their fresh-window gains are
~0 (spd200: exactly 0.00% RMSE), so the full-window delta is
manufactured in the stale window. A variant that cannot beat the
control has demonstrated no speed information. None does on both
metrics (spd200 beats the control by 0.0004 RMSE / 0.0007 MAE
while tying the baseline on the fresh window — the same
date-effect shape, one notch further).

XGBoost corroborates: no speed variant beats the XGB baseline on
the fresh window on both metrics, and the best XGB full RMSE
(spd_all 3.3280) is still well above the RF bar (3.3068). The
signal is absent in both model families.

### 3.3 Feature importances (spd500 RF — the best speed variant)

The forest essentially ignores the speed history. Importances
(`work/spd500_importances.json`, one fresh fit with v1
hyper-parameters on the spd500 feature set):

| Feature | Importance |
|---|---|
| roll_mean_7d (red-light) | 0.5611 |
| roll_mean_30d (red-light) | 0.2627 |
| lag_1d (red-light) | 0.1022 |
| cam_mean_pre_test | 0.0354 |
| dow | 0.0104 |
| cam_std_pre_test | 0.0085 |
| … | … |
| spd500_dist_m | 0.0022 |
| spd500_mean_pre_test | 0.0001 |
| spd500_roll_mean_30d | 0.0001 |
| spd500_roll_mean_7d | 0.0001 |
| spd500_lag_1d | 0.0001 |
| has_spd500 | 0.0000 |
| speed_age_days | 0.0000 |

**All seven speed columns combined: 0.0027.** The four actual
speed-history columns are 0.0001 each — the forest does not split
on them. The only speed-related column with any weight,
`spd500_dist_m` (0.0022), is a static distance, not speed history
(it likely proxies for "camera is in a dense-camera area" via
longitude/cam_code neighbours). This is consistent with §1.4's
diagnostic (lag-1 correlation 0.024) and with §3.1/§3.2: there is
no speed signal for the model to use.

## 4. The October data catch — handling, stated explicitly

Speed data ends **2026-09-24**; the test window ends 2026-10-29.
10,307 of 26,752 test rows (38.5%) fall after the speed file ends.

What was done (in `speed_features.py`, documented in code):

1. **Carry-forward, not zero-fill and not dropped.** Each matched
   speed camera's daily series is extended past 2026-09-24 by
   holding its last observed daily count (the 2026-09-24 value, 0
   if that camera recorded no violations that day) constant
   through 2026-10-29. Lags/rolls for October dates are therefore
   computed from that held value — stale, but explicit, and never
   invented from the future (no leakage: the held value is always
   from ≤ 2026-09-24).
2. **Staleness flag.** `speed_age_days` = 0 while speed data is
   fresh, then 1, 2, … days past 2026-09-24 (max 35 in the test
   window, mean 6.9). It is included in every speed variant so
   the model can discount stale features; in the fresh window it
   is constant 0.
3. **Two-window reporting.** The primary comparison (§3.1) uses
   only 2026-08-01 → 2026-09-24, where no staleness exists at
   all. The full holdout (§3.2) is reported as a secondary,
   carry-forward run, and the ship rule (§5) requires a win on
   BOTH windows — a win that exists only while speed data is
   fresh cannot ship, because live speed data arrives ~5 weeks
   late and October-style staleness is the normal case, not the
   exception.

What was NOT done: October rows were not dropped from the full
comparison (that would silently change the bar's window), and
speed features were not set to 0 after 2026-09-24 (that would
masquerade as "no speeding", which is false).

## 5. Ship decision

Rule (enforced in `train_v2.py`, not just in prose): a speed
variant ships as v2 ONLY if its RF beats the same-run baseline on
the fresh window (RMSE and MAE) AND beats the v1 bar
(RMSE 3.3068 / MAE 2.1452) on the full holdout AND beats the
staleness control (v1 + `speed_age_days` only) on the full holdout.

**Outcome: NO SHIP** (`data/processed/model_metrics_v2_speed.json`,
`"shipped": false`, `"complete": true`). No variant clears all
three legs:

- spd500: passes fresh (3.3450/2.1841 vs 3.3504/2.1853) and full
  vs bar (3.3046/2.1447 vs 3.3068/2.1452), but FAILS the control
  leg on MAE (2.1447 vs control 2.1441).
- spd200: ties the baseline on fresh RMSE (3.3504 = 3.3504) —
  fails the fresh leg — and its full gain is control-shaped.
- spd_all: fails the fresh leg on MAE (2.1873 vs 2.1853).
- spd_city: fails everywhere.
- No `redunator_brain_v2.*` files were created. v1 files are
  byte-identical (md5s in §7).

## 6. Files

| File | What it is |
|---|---|
| `speed_features.py` | Join (cKDTree nearest neighbour) + speed features; new file, does not edit `features.py` |
| `train_v2.py` | RF/XGB evaluation on both windows + ship rule; new file, does not edit `train.py`; checkpointed per variant (resume-safe) |
| `data/processed/speed_camera_join.csv` | 365 rows: red-light camera → nearest speed camera + distance + within-R flags |
| `data/processed/red_light_features_speed.parquet` | v1 feature table + speed columns (1,358,095 rows, 40 cols) |
| `data/processed/model_metrics_v2_speed.json` | All variant metrics, both windows, ship decision |
| `SPEED_JOIN_FINDINGS.md` | This file |
| `work/join_explore*.py`, `work/rl_nearest_speed.csv` | Join exploration + correlation diagnostic scratch |
| `work/spd500_importances.py`, `work/spd500_importances.json` | RF importances for the spd500 variant (§3.3) |
| `work/train_v2_*.log` | Per-variant training logs |

## 7. Integrity — v1 untouched

Re-verified at the end of the run (2026-11-16, after all training):

| File | md5 | Matches REPORT.md §8 |
|---|---|---|
| `redunator_brain_v1.joblib` | `9162dcbe76bf842584bd6f208c8bcb99` | yes |
| `redunator_brain_v1.onnx` | `90b9160389dd76d945bc8428a9ec3bf0` | yes |
| `redunator_brain_v1.json` | `28c83534e67267e272108c582ed0c56b` | yes |

`data/processed/model_metrics.json` was not written to by any
script here (only read, for the bar values). `train.py`,
`features.py`, `REPORT.md`, `HOURLY_REPORT.md`, `README.md` were
not edited. No `redunator_brain_v2.*` files exist. New files added
only: `speed_features.py`, `train_v2.py`,
`SPEED_JOIN_FINDINGS.md`, `data/processed/speed_camera_join.csv`,
`data/processed/red_light_features_speed.parquet`,
`data/processed/model_metrics_v2_speed.json`, and scratch under
`work/` (join exploration, per-variant logs, importances).

## 8. Open threads / notes for the coordinator

- The join itself is sound and reusable: `speed_camera_join.csv`
  and the feature parquet are built and verified. If a future
  item wants speed data as an *explanatory* layer (e.g. "which
  red-light cameras sit near a speed camera"), the mapping is
  there — it just does not add predictive signal for tomorrow's
  red-light count.
- A speed feature that was NOT tried: distance-weighted sum over
  ALL speed cameras within 500 m/1 km (instead of nearest only),
  and speed-camera *count* near each red-light camera. Given the
  per-camera lag-1 correlations are ~0.02 at every radius, there
  is no evidence these would help; listed only so the next worker
  does not re-derive the idea as novel.
- The 5 null-camera speed rows (2022-11-22, 2022-12-02,
  2023-06-23, 2023-09-29, 2024-08-27; 1 violation each) were
  dropped as unattributable, mirroring `features.py`'s handling
  of the 289 null-camera red-light rows. Negligible either way.
