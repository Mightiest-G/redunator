# Phase 2d — trip-speed SoQL queries (Worker 1)

Pulled 2026-11-15/16 by `data/hourly/speed_work/speed_pull_one.py`
(run via `run_jobs.py`, two streams: taxi + TNP, one fresh process per
chunk — same egress-proxy lesson as Phase 2c). Server-side aggregation
only; no raw rows downloaded.

## Definitions (partition of ALL trips — exhaustive, mutually exclusive)

- **valid**: `trip_seconds > 0 AND trip_miles > 0 AND trip_miles * 3600 / trip_seconds <= 80`
  (implied speed 0 < v <= 80 mph). Mean speed per cell = sum(miles) / (sum(seconds)/3600).
- **zero**: `trip_seconds = 0` (any miles). Excluded from speed; counted separately.
- **missing**: `trip_seconds IS NULL OR trip_miles IS NULL`. Excluded; counted separately.
- **absurd**: `trip_seconds > 0 AND trip_miles IS NOT NULL AND (trip_miles <= 0 OR trip_miles * 3600 / trip_seconds > 80)`.
  Excluded; counted separately. (Covers zero-distance trips and
  physically implausible speeds, e.g. 2 miles in 60 s = 120 mph — data errors.)
- Check: valid + zero + missing + absurd = total trips in range (reconciled
  against the Phase 2c `trip_hourly_by_area.parquet` totals per dataset-range).

## Query templates

### valid — (dow, hour, area) aggregates, one per dataset-year
```
GET https://data.cityofchicago.org/resource/<DATASET>.json
$select=date_extract_dow(trip_start_timestamp) as dow, date_extract_hh(trip_start_timestamp) as hour, pickup_community_area as area, count(*) as n, sum(trip_miles) as sum_miles, sum(trip_seconds) as sum_seconds
$where=trip_start_timestamp >= '<start>' AND trip_start_timestamp < '<end>' AND trip_seconds > 0 AND trip_miles > 0 AND trip_miles * 3600 / trip_seconds <= 80
$group=dow, hour, area
$order=dow, hour, area
$limit=50000
```
`date_extract_dow`: 0=Sunday … 6=Saturday (verified live: 2026-08-01 Sat→6,
2026-08-02 Sun→0, 2026-08-03 Mon→1). Max groups 7×24×78 = 13,104 (+ null-area
rows), single page, no $offset. Null pickup areas are kept in the pull
(they feed the citywide profile) and excluded from per-area profiles,
mirroring the demand build.

### holiday — (hour, area) aggregates over US-holiday dates only
Same $select minus dow, $where = (OR of one day-range per holiday date
inside the dataset range) AND <valid filter>, $group=hour, area.
Holiday list = `holidays.US` including federally observed dates — the same
convention as `build_demand.py` / `features.py`. Holiday (hour, area) sums
are subtracted from the dow-based weekday/weekend sums when building
profiles, so a holiday weekday does not contaminate the weekday profile.

### edge — single-row counts
```
$select=count(*) as n
$where=trip_start_timestamp >= '<start>' AND trip_start_timestamp < '<end>' AND (<class filter>)
```
One query per class (zero / missing / absurd) per dataset-range.

### series — (day, hour) citywide daily-hourly speed
Same aggregates as valid but grouped by (day, hour), no area.
Range 2025-01-01 → 2026-10-29/30 (crash series starts 2025-01-01).

### area — (day, hour, area) holdout daily-hourly speed
Same aggregates grouped by (day, hour, area); taxi monthly Aug/Sep/Oct 2026,
TNP half-months Aug/Sep 2026 (TNP dataset 6dvr-xwnh ends 2026-09-30, so
October 2026 speed is taxi-only — flagged in the findings).

## Socrata quirks verified live
- `$group` (not `$group_by`); `date_extract_hh()` for hour;
  `date_extract_dow()` for day-of-week (0=Sunday).
- Arithmetic in `$where` works (`trip_miles * 3600 / trip_seconds <= 80`).
- `sum(trip_miles)` / `sum(trip_seconds)` work in `$select` with `$group`.
- A 7-month taxi valid aggregate took 192 s server-side; yearly TNP scans
  are the slow pole (tens of minutes per query possible).
- **Chunk-size lesson (2026-11-16): yearly AND half-yearly TNP valid
  aggregates get proxy-dropped** (`Remote end closed connection`, 2
  attempts each, ~4-7 min of server compute) while taxi yearly aggregates
  (~6M rows, ~185 s) succeed. TNP valid/series pulls were therefore split
  to MONTHLY chunks (the unit that succeeded throughout Phase 2c); TNP
  edge counts were split to half-yearly. m6dm-c72p (2019-2022) is outside
  the profile window (2023-01-01 -> 2026-07-31) and its monthly re-pull
  (48 chunks) is deferred — see SPEED_FINDINGS.md.
- **'absurd' is DERIVED, not queried, for TNP**: its count(*) query carries
  the arithmetic `$where` over a half-year scan and proxy-dropped twice
  (~10 min each); the partition is exact, so
  absurd = total(2c) − valid − zero − missing. Taxi absurd counts WERE
  pulled directly (fast) and serve as a cross-check of the derivation.

## Chunks pulled
Per-chunk records live in `data/hourly/raw/trip_speed_chunks/chunk_summary_speed.json`
(valid/holiday/series/area) and `speed_<did>_edge_<class>.json` (edge counts).

## Chunks pulled (102)

| kind | dataset | label | rows | valid trips | seconds |
|---|---|---|---|---|---|
| area | 6dvr-xwnh | area_2026-08a | 28021 | 3786423 | 130.2 |
| area | 6dvr-xwnh | area_2026-08b | 29871 | 3790023 | 149.7 |
| area | 6dvr-xwnh | area_2026-09a | 28005 | 3601952 | 39.3 |
| area | 6dvr-xwnh | area_2026-09b | 27981 | 3757039 | 120.5 |
| holiday | 6dvr-xwnh | holiday | 1872 | 4035619 | 41.2 |
| series | 6dvr-xwnh | series_2025-01 | 744 | 7605501 | 180.7 |
| series | 6dvr-xwnh | series_2025-02 | 672 | 7288052 | 149.6 |
| series | 6dvr-xwnh | series_2025-03 | 743 | 8036625 | 213.9 |
| series | 6dvr-xwnh | series_2025-04 | 720 | 7535064 | 269.7 |
| series | 6dvr-xwnh | series_2025-05 | 744 | 8087172 | 151.8 |
| series | 6dvr-xwnh | series_2025-06 | 720 | 7690443 | 107.1 |
| series | 6dvr-xwnh | series_2025-07 | 744 | 8039012 | 137.8 |
| series | 6dvr-xwnh | series_2025-08 | 744 | 8218503 | 50.3 |
| series | 6dvr-xwnh | series_2025-09 | 720 | 7495588 | 74.9 |
| series | 6dvr-xwnh | series_2025-10 | 744 | 8156750 | 254.3 |
| series | 6dvr-xwnh | series_2025-11a | 360 | 4062681 | 268.7 |
| series | 6dvr-xwnh | series_2025-11b | 360 | 3561025 | 269.7 |
| series | 6dvr-xwnh | series_2025-12 | 744 | 7727290 | 135.0 |
| series | 6dvr-xwnh | series_2026-01 | 744 | 7536531 | 156.7 |
| series | 6dvr-xwnh | series_2026-02 | 672 | 7197865 | 121.7 |
| series | 6dvr-xwnh | series_2026-03 | 743 | 8122192 | 132.9 |
| series | 6dvr-xwnh | series_2026-04 | 720 | 7632162 | 134.6 |
| series | 6dvr-xwnh | series_2026-05 | 744 | 7774386 | 102.1 |
| series | 6dvr-xwnh | series_2026-06 | 720 | 7257533 | 140.9 |
| series | 6dvr-xwnh | series_2026-07 | 744 | 7598537 | 80.9 |
| series | 6dvr-xwnh | series_2026-08 | 744 | 7576446 | 136.5 |
| series | 6dvr-xwnh | series_2026-09 | 720 | 7358991 | 221.7 |
| valid | 6dvr-xwnh | valid_2025-01 | 13104 | 7605501 | 165.8 |
| valid | 6dvr-xwnh | valid_2025-02 | 13103 | 7288052 | 199.6 |
| valid | 6dvr-xwnh | valid_2025-03 | 13103 | 8036625 | 262.9 |
| valid | 6dvr-xwnh | valid_2025-04 | 13104 | 7535064 | 105.0 |
| valid | 6dvr-xwnh | valid_2025-05 | 13104 | 8087172 | 113.7 |
| valid | 6dvr-xwnh | valid_2025-06 | 13104 | 7690443 | 46.7 |
| valid | 6dvr-xwnh | valid_2025-07 | 13104 | 8039012 | 64.3 |
| valid | 6dvr-xwnh | valid_2025-08 | 13104 | 8218503 | 192.0 |
| valid | 6dvr-xwnh | valid_2025-09 | 13104 | 7495588 | 143.5 |
| valid | 6dvr-xwnh | valid_2025-10 | 13104 | 8156750 | 208.3 |
| valid | 6dvr-xwnh | valid_2025-11 | 13104 | 7623706 | 179.7 |
| valid | 6dvr-xwnh | valid_2025-12 | 13104 | 7727290 | 61.9 |
| valid | 6dvr-xwnh | valid_2026-01 | 13102 | 7536531 | 132.9 |
| valid | 6dvr-xwnh | valid_2026-02 | 13103 | 7197865 | 147.8 |
| valid | 6dvr-xwnh | valid_2026-03 | 13104 | 8122192 | 259.5 |
| valid | 6dvr-xwnh | valid_2026-04 | 13104 | 7632162 | 233.1 |
| valid | 6dvr-xwnh | valid_2026-05 | 13103 | 7774386 | 227.7 |
| valid | 6dvr-xwnh | valid_2026-06 | 13104 | 7257533 | 196.0 |
| valid | 6dvr-xwnh | valid_2026-07 | 13104 | 7598537 | 228.4 |
| area | ajtu-isnz | area_2026-08 | 31731 | 560131 | 7.5 |
| area | ajtu-isnz | area_2026-09 | 30684 | 559552 | 17.9 |
| area | ajtu-isnz | area_2026-10 | 31368 | 574989 | 18.5 |
| holiday | ajtu-isnz | holiday | 1808 | 365269 | 6.0 |
| series | ajtu-isnz | series_202501_202610 | 16006 | 11813042 | 41.2 |
| valid | ajtu-isnz | valid_2024 | 12792 | 5804669 | 161.8 |
| valid | ajtu-isnz | valid_2025 | 12788 | 6289914 | 203.8 |
| valid | ajtu-isnz | valid_2026H1 | 12441 | 3859469 | 192.2 |
| holiday | n26f-ihde | holiday | 1872 | 4625355 | 42.8 |
| valid | n26f-ihde | valid_2023-01 | 13104 | 6158194 | 121.6 |
| valid | n26f-ihde | valid_2023-02 | 13103 | 6134362 | 135.3 |
| valid | n26f-ihde | valid_2023-03 | 13103 | 7080676 | 132.5 |
| valid | n26f-ihde | valid_2023-04 | 13099 | 6688069 | 123.9 |
| valid | n26f-ihde | valid_2023-05 | 13103 | 6984944 | 40.3 |
| valid | n26f-ihde | valid_2023-06 | 13103 | 6972401 | 130.0 |
| valid | n26f-ihde | valid_2023-07 | 13102 | 7115316 | 158.8 |
| valid | n26f-ihde | valid_2023-08 | 13104 | 7136152 | 191.3 |
| valid | n26f-ihde | valid_2023-09 | 13102 | 7129852 | 144.0 |
| valid | n26f-ihde | valid_2023-10 | 13104 | 7548516 | 193.1 |
| valid | n26f-ihde | valid_2023-11 | 13104 | 6999643 | 192.0 |
| valid | n26f-ihde | valid_2023-12 | 13104 | 7443280 | 57.0 |
| valid | n26f-ihde | valid_2024-01 | 13104 | 7061425 | 68.3 |
| valid | n26f-ihde | valid_2024-02 | 13102 | 7093456 | 167.8 |
| valid | n26f-ihde | valid_2024-03a | 13097 | 3815207 | 76.6 |
| valid | n26f-ihde | valid_2024-03b | 13096 | 4155285 | 255.5 |
| valid | n26f-ihde | valid_2024-04 | 13101 | 7370993 | 221.4 |
| valid | n26f-ihde | valid_2024-05 | 13103 | 7752441 | 156.9 |
| valid | n26f-ihde | valid_2024-06 | 13104 | 7616277 | 132.0 |
| valid | n26f-ihde | valid_2024-07 | 13104 | 7511082 | 127.0 |
| valid | n26f-ihde | valid_2024-08 | 13104 | 7883941 | 67.7 |
| valid | n26f-ihde | valid_2024-09 | 13104 | 7397571 | 104.7 |
| valid | n26f-ihde | valid_2024-10 | 13102 | 7860912 | 37.9 |
| valid | n26f-ihde | valid_2024-11 | 13104 | 7616819 | 165.0 |
| valid | n26f-ihde | valid_2024-12 | 13104 | 7812338 | 181.1 |
| holiday | wrvz-psew | holiday | 1695 | 144729 | 2.2 |
| valid | wrvz-psew | valid_2023 | 12805 | 5803639 | 184.8 |

Edge counts (single-row JSONs, not in the table above): taxi absurd/zero/missing pulled directly per dataset-range; TNP zero/missing pulled per half-year, TNP absurd derived (see SPEED_FINDINGS.md §1).