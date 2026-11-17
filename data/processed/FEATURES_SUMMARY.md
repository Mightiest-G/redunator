# Redunator — Feature Table Summary (Step 2)

Built 2026-11-15 by `features.py` from the ingested raw files
(`data/raw/red_light_violations.csv`, `data/raw/speed_violations.csv`).
Output: `data/processed/red_light_features.parquet` (10.8 MB, snappy).

## Headline numbers

| | |
|---|---|
| **Final rows** | **1,358,095** (one row per camera-day) |
| **Date range** | 2014-07-01 → 2026-10-29 (4,504 calendar days) |
| **Cameras** | 365 distinct `camera_id`s |
| **Target mean** | 5.34 violations/camera-day (max 197) |
| **Zero-filled rows** | 182,610 (13.4% of rows) |
| **Observed rows** | 1,175,485 (rows present in the raw file after dedup) |
| **Active segments** | 407 (37 cameras have > 1 segment) |

## Columns (23)

| Column | Type | Definition |
|---|---|---|
| `camera_id` | str | Cleaned camera key, e.g. `"1233"` (source floats like `1233.0` normalised) |
| `segment_id` | int | Activity segment within a camera (1, 2, …). A new segment starts after a > 90-day gap with no observed rows (camera removed / reinstalled / long offline) |
| `date` | date | Calendar day |
| `violations` | int32 | **Target.** Daily red-light violations for that camera; 0 on zero-filled days |
| `is_observed` | int8 | 1 if this camera-day was in the raw file after dedup, 0 if zero-filled |
| `intersection` | str | Modal (most frequent) intersection string for the camera |
| `latitude` / `longitude` | float64 | Per-camera median lat/lon, backfilled from rows that have them. NaN for the 4 cameras that never have coordinates (1623, 2552, 2592, 2662) — 2,924 rows |
| `dow` | int8 | Day of week, 0 = Monday … 6 = Sunday |
| `month` | int8 | 1–12 |
| `year` | int16 | 2014–2026 |
| `season` | int8 | Meteorological: 0 winter (Dec–Feb), 1 spring (Mar–May), 2 summer (Jun–Aug), 3 fall (Sep–Nov) |
| `day_of_year` | int16 | 1–366 |
| `is_weekend` | int8 | 1 if Sat/Sun |
| `is_holiday` | int8 | 1 if a US federal holiday (see below) |
| `holiday_name` | str | Holiday name, or `""` (e.g. `"Independence Day"`, `"Independence Day (observed)"`) |
| `days_since_first` | int32 | Days since that camera's first observed date (proxy for install date; the true install may be earlier if its first days had 0 violations) |
| `lag_1d` | float32 | Prior-day violations for the same camera. NaN (407 rows) on each segment's first day — no prior day within the segment |
| `roll_mean_7d` | float32 | Mean violations over the prior 7 days (shifted — past days only, no leakage). NaN on segment starts |
| `roll_mean_30d` | float32 | Same, prior 30 days. NaN on segment starts |
| `cam_mean_pre_test` | float32 | Per-camera mean violations over the zero-filled series **before 2026-08-01** (see leakage note) |
| `cam_std_pre_test` | float32 | Per-camera std over the same pre-test window |
| `cam_n_days_pre_test` | Int32 | Number of days that went into the pre-test aggregates |

## Dedup decision

**Rule: keep the MAX violations per (`camera_id`, `date`). Never sum**
(summing would double-count a re-issued portal row).

- 135 duplicate pairs among identified cameras (270 rows involved):
  114 with *conflicting* counts, 21 exact duplicates.
  Max-dedup removed **135 rows**.
- Conflicts are heavily clustered in **April 2026** (94 of 114 pairs on
  2026-04-16/17/18 — e.g. camera 1014 on 2026-04-17: 4 vs 2, kept 4).
  Looks like a portal re-issue/correction window; the max is the safer
  reading of a corrected count.
- Note on the ingest summary: it reported "184 duplicate pairs
  (359 rows)". Recounting here gives **175 pairs / 359 rows** — the 184
  is the count of *surplus rows* beyond the first occurrence
  (359 − 175 = 184), not pairs. The extra 40 pairs / 89 rows are
  same-date groups among the null-camera rows (see below), which are
  dropped wholesale.

## Null-camera rows — DROPPED

**289 rows** with null `camera_id` were dropped (290 violations lost,
~0.004% of total volume). They span 112 intersections, 2014-07-01 →
2017-09-01, and are almost all violations == 1 (mean 1.00). They cannot
be attributed to any camera, so no feature row can be built from them.

## Zero-fill decision — YES, zero-fill was required

The raw file only contains rows on days with ≥ 1 violation (min
violations = 1), so a missing camera-day means 0 violations — but only
while the camera plausibly exists. The table was therefore expanded to
a full camera × day grid **within each camera's active segments**:

- A camera's activity is split into a new segment whenever consecutive
  observed dates are **> 90 days** apart (42 such splits; 37 cameras
  have multiple segments). No rows are created in the gaps between
  segments, or before a camera's first appearance / after its last.
- Longest gaps handled this way: camera 1431 (417 days, 2016-06-03 →
  2017-07-25) and 1563 (406 days). Treating those as 0-violation days
  would be wrong — the camera was almost certainly not installed then.
- Typical gaps are 1 day (1,069,727) or 2–3 days (92,405) — ordinary
  zero-violation days, correctly zero-filled.
- **182,610 zero rows added** (13.4% of the table). Median camera
  coverage inside its span is 89.4%.
- 65 cameras are decommissioned: last seen before 2020 (none of them
  reappear). 300 cameras are active in the test window.

## Coordinates

- Red-light lat/lon is populated for 96.4% of raw rows. Backfilled by
  per-camera **median** lat/lon (289 cameras have 2–4 slightly
  different lat values in the raw file — jitter of ~10–75 m, so the
  median is the right summary).
- **4 cameras never have coordinates anywhere: 1623, 2552, 2592, 2662**
  (2,924 feature rows, lat/lon NaN). They cannot be placed on a map
  from this dataset; the train worker should either drop them or
  impute (e.g. from the intersection name via geocoding).
- Intersection per camera = modal string. 4 cameras have 2 intersection
  strings each; two are just reversed street order ("FOSTER AND
  NORTHWEST HIGHWAY" vs "NORTHWEST HIGHWAY AND FOSTER"). One is a
  likely data-entry stray (camera 1421: 1 row "DAMEN AND DIVERSEY"
  against 2,971 "LARAMIE AND FULLERTON").
- Addresses are truncated at 25 chars in the source and were never used
  as keys.

## Holidays

Source: **`holidays` package v0.108, `holidays.US()`** (added to
`requirements.txt`; it downgraded `python-dateutil` 4.0.0 → 2.9.0.post0
in the venv — pandas 3.1.1 verified working with it).

- Flags US federal holidays **including federally observed dates**:
  a Saturday holiday is observed the preceding Friday, a Sunday holiday
  the following Monday. 15 observed dates fall inside the table's range
  (e.g. 2026-07-03 = "Independence Day (observed)", since 2026-07-04
  is a Saturday).
- 43,436 camera-days flagged as holidays (151 distinct federal holidays
  2014–2026, 15 of them observed).

## Leakage notes (for the train worker)

- `lag_1d`, `roll_mean_7d`, `roll_mean_30d` are computed with a shift,
  from **past days only**, **within each (camera, segment)** — a
  segment's first day does not inherit a lag from the previous segment.
  They are NaN on the 407 segment-start rows; fill with 0 or drop.
- `cam_mean_pre_test` / `cam_std_pre_test` are computed over the
  zero-filled series **excluding the final 3 months** (all data before
  2026-08-01), so they are safe to use with the recommended split
  below. Every camera has pre-test history (0 NaN rows).
- `days_since_first` is a proxy for install date, not the true install
  date (see column definition).

## Recommended train/test split (time-based)

- **Train: 2014-07-01 → 2026-07-31** (1,331,343 rows, 365 cameras)
- **Test: 2026-08-01 → 2026-10-29** (26,752 rows, 300 cameras — the
  65 decommissioned cameras have no test rows; per-camera test rows
  range 8–90, since the window is 90 days and some cameras' segments
  end mid-window)
- Do not use a random split: violations are strongly autocorrelated
  per camera, and a random split would leak.

## Speed-camera file (context only — validated, no features built)

`speed_violations.csv` loads cleanly: 504,853 rows, 2014-07-01 →
2026-09-24, 236 cameras, mean 42.45 violations/camera-day (max 1,888),
5 null-camera rows, 2 duplicate (camera, date) pairs — both
conflicting (CHI095 2026-09-16: 63 vs 1; CHI225 2026-08-10: 106 vs 56).
Note it ends ~5 weeks before the red-light data (Sep 24 vs Oct 29).
No speed features are in this table; the train worker can join it
later if speed-camera context proves useful (different camera IDs —
`CHI041` style — so joining needs an intersection/address match, not
a camera-ID match).

## Things that look odd / worth knowing

1. **April 2026 re-issue cluster** — 94 of 114 conflicting duplicate
   pairs fall on 2026-04-16/17/18 across many cameras. The portal
   appears to have re-published those days with different counts.
   Max-dedup kept the higher count; if the lower count is the
   correction, the last ~6 months of data for those camera-days is
   slightly overstated.
2. **65 dead cameras** — no rows after 2019 for 65 of 365 cameras.
   They contribute only training history.
3. **Camera 1671's second intersection** — 196 rows say "DAMEN AND
   FULLERTON" vs 4,169 "WESTERN AND FULLERTON". Possibly a moved
   camera or a sustained mislabel; modal intersection used.
4. **Zero-fill assumption** — a missing day inside a segment is treated
   as 0 violations. If the portal ever *omits* a camera-day that did
   have violations (e.g. a reporting outage), those days are wrongly
   zero. Nothing in the data distinguishes the two cases.
5. **No hour-of-day** in either dataset — daily granularity only. Any
   intraday risk pattern needs another source.
6. **Date format** in the raw CSVs is MM/DD/YYYY; raw headers are
   UPPER-CASE with spaces (normalised in `features.py`).

## Reproduce

```bash
~/workspace/redunator-venv/bin/python ~/workspace/redunator/features.py --check
```

Runs in ~18 s on this VM. Deterministic from the raw CSVs.
`requirements.txt` now pins `holidays==0.108`, `pyarrow==26.0.1`,
`six==1.17.0`, `python-dateutil==2.9.0.post0` (see Holidays note).
