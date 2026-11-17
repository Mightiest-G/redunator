# Phase 2c — Trip-demand sources, verified

Verified live 2026-11-15 (America/Chicago) against the Socrata metadata API
(`https://data.cityofchicago.org/api/views/<id>`), the Socrata catalog API
(`https://data.cityofchicago.org/api/catalog/v1`), and SoQL aggregation
queries. **Nothing below is assumed — every claim was observed in an API
response or in the pulled data.**

Ingest scripts: `trip_demand_ingest.py` (monthly / half-month / weekly
chunks, resumable), `trip_pull_one.py` + `trip_pull_remaining.py` (one
chunk per fresh process, used for the last gap — see §7). Combine +
sanity check: `trip_demand_combine.py`, results in
`data/hourly/TRIP_SANITY.json`. Exact queries: `data/hourly/TRIP_QUERIES.md`.

## What the output is (read this first — per the user's correction)

Every row in the delivered files is a **real count** of real trips:
for one calendar day, one hour of day (0–23, Chicago local), and one
pickup community area (1–77, plus NULL where the source suppressed the
area), `trips` = the exact number of trip rows in the source dataset
whose `trip_start_timestamp` falls in that hour — a server-side
`count(*)` grouped by Socrata. **No averages anywhere in the ingest.**
(Averages/shares appear only in the sanity-check summary, never in the
data files.) The reconciliation in §4 proves the counts are complete:
pulled totals + independently counted un-pulled heads equal the
datasets' published row counts **exactly (diff 0)** for all five files.

## ⚠️ Headline findings (read first)

1. **Successor datasets found and used — nothing fell back.** Taxi
   (2024-) is `ajtu-isnz`; TNP splits into (2023–2024) `n26f-ihde`
   and (2025-) `6dvr-xwnh`. All three were found via the catalog API
   and verified by metadata + full pulls. The older files
   (`wrvz-psew`, `m6dm-c72p`) are used only for their 2019+ tails.
2. **Staleness: the trip data ends before the present.** Taxi ends
   **2026-11-01** (effectively 2026-10-31; the last timestamp is a
   single trip at 2026-11-01T00:00:00). TNP ends **2026-09-30** —
   six weeks stale as of 2026-11-15. The TNP (2025-) file was last
   updated 2026-10-27, so a newer pull will not extend it until the
   city posts October 2026. Any trip-demand profile built from this
   pull inherits that lag; it cannot see the Phase 2 holdout window's
   last ~6 weeks for TNP.
3. **The seams are clean.** The five files abut exactly with no
   overlap and no gap: taxi `wrvz-psew` ends 2023-12-31 23:45 →
   `ajtu-isnz` starts 2024-01-01 00:00; TNP `m6dm-c72p` ends
   2022-12-31 23:45 → `n26f-ihde` starts 2023-01-01 00:00 →
   `6dvr-xwnh` starts 2025-01-01 00:00. Day coverage confirms it:
   taxi 2,862/2,862 days present, TNP 2,830/2,830 days present, zero
   missing days in either span.
4. **Null pickup areas are common (5.5% taxi, 8.5% TNP) and are
   kept as their own NULL-area rows** — they are not dropped and not
   imputed. Worker B must decide how to treat them (they carry
   ~55M of the 675M trips). Rates differ by file and era: 2.7% in
   taxi 2024-, 6.9% in taxi 2019–2023, 7.4% in TNP 2019–2022, 9.0%
   in TNP 2023–2024, 9.7% in TNP 2025-. One monthly spike: taxi
   2019-04 is 14.8% null (215,235 of 1,458,362 trips), roughly
   double the surrounding months — cause unknown, flagged not fixed.
5. **COVID collapse is in the data.** TNP weekly trips fall from
   ~2.2M/week (Feb 2020) to ~0.62–0.66M/week (Jun 2020). Any profile
   averaging 2019–present without a COVID adjustment will mix
   pre- and post-collapse regimes.

## Source table (verified IDs)

| Source | Socrata id | Rows in source (total) | Full range in source | Pulled window | Pulled trips |
|---|---|---|---|---|---|
| Taxi Trips (2013-2023) | `wrvz-psew` | 211,670,894 | 2013-01-01 → 2023-12-31 23:45 | 2019-01-01 → 2023-12-31 | 37,192,437 |
| Taxi Trips (2024-) | `ajtu-isnz` | 19,277,823 | 2024-01-01 → 2026-11-01 00:00 | all of it | 19,277,823 |
| TNP - Trips (2018-2022) | `m6dm-c72p` | 299,602,996 | 2018-11-01 → 2022-12-31 23:45 | 2019-01-01 → 2022-12-31 | 282,141,543 |
| TNP - Trips (2023-2024) | `n26f-ihde` | 174,369,516 | 2023-01-01 → 2024-12-31 23:45 | all of it | 174,369,516 |
| TNP - Trips (2025-) | `6dvr-xwnh` | 161,578,076 | 2025-01-01 → 2026-09-30 23:45 | all of it | 161,578,076 |
| Boundaries - Community Areas | `igwz-8jzy` | 77 | — | all of it | — |

Metadata `rowsUpdatedAt` (UTC): `wrvz-psew` 2024-02-07, `ajtu-isnz`
2026-11-12, `m6dm-c72p` 2023-03-29, `n26f-ihde` 2025-02-27,
`6dvr-xwnh` 2026-10-27, `igwz-8jzy` 2025-04-22. The two old files are
frozen (their descriptions point at the successors); the two current
files (`ajtu-isnz`, `6dvr-xwnh`) were updated within days of this pull.

## 1. Columns observed (all five trip files)

Every file has `trip_id`, `trip_start_timestamp`,
`trip_end_timestamp` (calendar_date, Chicago local, rounded to the
nearest 15 minutes), `trip_seconds`, `trip_miles`,
`pickup_census_tract`, `dropoff_census_tract`,
`pickup_community_area`, `dropoff_community_area` (number),
`fare`, `trip_total`, `pickup_centroid_latitude/longitude`,
`pickup_centroid_location` (point), `dropoff_centroid_latitude/
longitude/location`. Taxi files add `taxi_id`, `tips`, `tolls`,
`extras`, `payment_type`, `company`. TNP files add `tip`,
`additional_charges`, `shared_trip_authorized`, `trips_pooled`;
the 2023+ TNP files add `percent_time_chicago`,
`percent_distance_chicago`, `shared_trip_match`. The aggregation
uses only `trip_start_timestamp` and `pickup_community_area`.

## 2. The SoQL pattern (and the three quirks)

```
GET https://data.cityofchicago.org/resource/<DATASET>.json
$select=date_trunc_ymd(trip_start_timestamp) as day,
        date_extract_hh(trip_start_timestamp) as hour,
        pickup_community_area as area, count(*) as trips
$where=trip_start_timestamp >= '<start>' AND trip_start_timestamp < '<end>'
$group=day, hour, area
$order=day, hour, area
$limit=50000 (+ $offset=50000, ... until a short page)
```

Quirks found live (each cost a failed attempt before it was found):
- The grouping parameter is `$group`, not `$group_by` (`$group_by`
  returns HTTP 400 "Unrecognized arguments").
- Hour extraction is `date_extract_hh()` — `datepart()` does not
  exist on this portal, and neither does `date_trunc_hour()`.
- Socrata caps `$limit` at 50,000 rows per response. A TNP month
  yields up to ~58k (day, hour, area) groups, so a single page
  **silently truncates** — the first three TNP chunks were pulled
  at exactly 50,000 rows, caught by the row-count check, deleted,
  and re-pulled with pagination. Taxi months (~33k rows) never hit
  the cap.

## 3. Chunking used

Monthly by default (95 taxi months). TNP months were split into
halves (1st–15th / 16th–end, ~29k groups max each) and the stubborn
ranges into 7-day weeks (~13k groups max each), each a single page
with no `$offset` — the `$offset=50000` page failed 10+ times for
`m6dm-c72p` 2019-07 while offset-0 pages usually succeeded. Final
tally: 294 chunk files (95 taxi monthly; TNP: 31 monthly + 88 halves +
80 weeks — the mix reflects which ranges were already done when
the split changed, and the combine step is insensitive to it).

## 4. Reconciliation — pulled totals vs published row counts

| Dataset | Pulled trips | Un-pulled head (independently counted) | Sum | Published total | Diff |
|---|---|---|---|---|---|
| `ajtu-isnz` (all) | 19,277,823 | — | 19,277,823 | 19,277,823 | **0** |
| `6dvr-xwnh` (all) | 161,578,076 | — | 161,578,076 | 161,578,076 | **0** |
| `n26f-ihde` (all) | 174,369,516 | — | 174,369,516 | 174,369,516 | **0** |
| `m6dm-c72p` (2019–2022) | 282,141,543 | 17,461,453 (Nov–Dec 2018, `count(*)` where `< 2019-01-01`) | 299,602,996 | 299,602,996 | **0** |
| `wrvz-psew` (2019–2023) | 37,192,437 | 174,478,457 (2013–2018, `count(*)` where `< 2019-01-01`) | 211,670,894 | 211,670,894 | **0** |

All five datasets reconcile **exactly**. The three current/mid files
were pulled in full, so their pulled totals equal their published
totals directly. The two old files were pulled from 2019 onward, and
the un-pulled heads were counted independently with a windowed
`count(*)` — both sums land exactly on the published totals.

Null pickup-area share, pulled window vs full file (metadata):
`ajtu-isnz` 2.70% / 2.70%; `6dvr-xwnh` 9.73% / 9.73%;
`n26f-ihde` 8.98% / 8.98%; `m6dm-c72p` 7.45% (2019–2022) / 7.36%
(full); `wrvz-psew` 6.94% (2019–2023) / 11.42% (full — the
2013–2018 years had much higher null rates).

## 5. Sanity checks (full results: `data/hourly/TRIP_SANITY.json`)

| Check | Taxi | TNP |
|---|---|---|
| Combined rows | 3,010,264 | 5,269,687 |
| Duplicate (day, hour, area) | 0 | 0 |
| Total trips | 56,470,260 | 618,089,135 |
| Null-area trips / share | 3,102,393 / 5.49% | 52,385,184 / 8.48% |
| Date range | 2019-01-01 → 2026-11-01 | 2019-01-01 → 2026-09-30 |
| Days present / expected | 2,862 / 2,862 (0 missing) | 2,830 / 2,830 (0 missing) |
| Distinct pickup areas | 77 (1–77, none missing) | 77 (1–77, none missing) |
| Peak hour (citywide share) | 17:00 (7.23%) | 18:00 (6.45%) |
| Trough hour (citywide share) | 04:00 (0.67%) | 03:00 (1.19%) |
| Days with < 24 hours | 9 — the 8 spring-forward Sundays (no 02:00) + 2026-11-01 (1 hour: only the midnight trip) | 8 — the 8 spring-forward Sundays (no 02:00) |

Hour shapes are sane: a strong evening peak (17:00 taxi, 18:00
rideshare), an overnight trough (03:00–04:00), and a morning rise
from 07:00. The combined file (`trip_hourly_by_area.parquet`)
reports 2,975,061 "duplicates" on (day, hour, area) — that is
expected, not a defect: taxi and TNP rows share keys and are
distinguished by the `source` column. Within each source the
duplicate count is 0.

Timestamps are naive Chicago-local hourly labels in a flat 24/day
grid (same convention as the Phase 2b hourly files): the 02:00 row
does not exist on spring-forward days, and 01:00 aggregates both
fall-back occurrences. Do not treat the label as true local time on
those two days a year.

## 6. Community-area boundaries — `igwz-8jzy`

- **Metadata:** name "Boundaries - Community Areas", 77 rows,
  updated 2025-04-22. Description: "Community area boundaries in
  Chicago."
- **Columns (6):** `the_geom` (**multipolygon** — the geometry
  column for a point-in-polygon join), `area_numbe` (number 1–77),
  `community` (text, e.g. "ROGERS PARK"), `area_num_1` (text),
  `shape_area`, `shape_len`.
- **Verified:** GeoJSON download (77 features, `area_numbe` 1–77
  complete with no gaps, all `MultiPolygon` geometry) saved as
  `data/hourly/raw/community_areas.geojson` (2.1 MB). The join
  itself was NOT done here — Worker B owns it; the area key to
  join on is `area_numbe` = `pickup_community_area`.
- **Caveat:** boundary vintage is not dated in the metadata beyond
  the 2025-04-22 update; Chicago's 77 community areas have been
  stable since 1930, so this is a non-issue in practice.

## 7. How the pull actually ran (for the record)

- The sandbox egress proxy drops long-lived connections in bursts
  ("Remote end closed connection without response"). Early long-lived
  workers accumulated stale pooled connections and then failed every
  attempt for 25+ minutes at a time — including on queries that
  succeed in ~120s from a fresh process. Mitigations that worked:
  half-month and weekly chunks (single page, no `$offset`),
  fresh `requests.Session` per attempt, exponential backoff
  (30s × attempt), and finally one chunk per fresh process
  (`trip_pull_one.py` + `trip_pull_remaining.py`) for the last
  20 week-chunks. The VM also restarted several times during the
  run (uptime reset to 0 four times); all progress survived because
  chunks are checkpointed per range and the scripts skip existing
  files.
- Three TNP chunks were silently truncated at the 50,000-row cap
  before pagination was added; they were deleted and re-pulled.
  No other chunk shows a suspicious exactly-50,000 row count.
- `requirements.txt` was **not** changed: the ingest uses only
  `requests`, `pandas`, `numpy`, `pyarrow` — all already pinned
  there. Nothing new was installed.

## 8. Files written (all new; no Phase 2 artifact modified)

| File | Rows | Range / notes |
|---|---|---|
| `data/hourly/raw/trip_chunks/` (294 chunk parquets + `chunk_summary*.json` + `dataset_meta.json`) | 8,279,951 | per-range raw aggregates |
| `data/hourly/processed/taxi_hourly_by_area.parquet` | 3,010,264 | 2019-01-01 → 2026-11-01 |
| `data/hourly/processed/tnp_hourly_by_area.parquet` | 5,269,687 | 2019-01-01 → 2026-09-30 |
| `data/hourly/processed/trip_hourly_by_area.parquet` | 8,279,951 | both sources, + `source` column |
| `data/hourly/raw/community_areas.geojson` | 77 features | `the_geom` MultiPolygon |
| `data/hourly/TRIP_QUERIES.md` | 294 chunk rows | exact SoQL per range |
| `data/hourly/TRIP_SANITY.json` | — | sanity-check numbers (§5) |
| `data/hourly/TRIP_SOURCES_VERIFIED.md` | — | this file |
| `trip_demand_ingest.py`, `trip_demand_combine.py`, `trip_pull_one.py`, `trip_pull_remaining.py`, `trip_meta_counts.py` | — | scripts |

Total on disk: ~45 MB under `data/hourly/` for the trip layer
(16 MB chunks + 27 MB combined parquets + 2 MB GeoJSON).

## 9. Caveats for the modelling step (Worker B)

- **Use the real counts, not averages.** Each row is a true
  `count(*)` for its (day, hour, area). Profiles should be built
  from these counts (e.g. sum over days, then normalise) — do not
  average pre-aggregated daily means.
- **TNP ends 2026-09-30, taxi ends 2026-11-01.** Do not forward-fill
  either series past its end date; treat missing months as missing.
- **NULL pickup areas (8.2% combined, ~55M trips)** are their own
  rows. Decide explicitly: exclude, or distribute pro-rata. Do not
  silently drop them without noting the 8% loss.
- **COVID regime break:** Mar–Jun 2020 TNP volume is ~30% of Feb
  2020. A single 2019–present average profile mixes two regimes;
  consider a post-2021 window or a regime flag.
- **Taxi 2019-04 null spike (14.8%)** — one anomalous month,
  cause unknown.
- **Spring-forward days have no 02:00 row** (8 days in each span);
  2026-11-01 has a single 00:00 taxi row. Build profiles on
  hour-of-day with these days handled, not zero-filled as if the
  hour existed.
- **Community-area join:** use `area_numbe` from
  `community_areas.geojson` (77 polygons, `the_geom`).

## 10. Integrity — Phase 2 artifacts untouched

The daily model files are byte-identical to the Phase 2 baseline
(verified 2026-11-15 by md5, same values as `HOURLY_REPORT.md` §7):
`redunator_brain_v1.joblib` `9162dcbe76bf842584bd6f208c8bcb99`,
`redunator_brain_v1.onnx` `90b9160389dd76d945bc8428a9ec3bf0`,
`redunator_brain_v1.json` `28c83534e67267e272108c582ed0c56b`.
No Phase 2b file (`hourly_risk.py`, `hourly_model.py`,
`validate_hourly.py`, anything under `data/hourly/raw/` or
`data/hourly/processed/` from Phase 2b) was modified — only new
Phase 2c files were added.
