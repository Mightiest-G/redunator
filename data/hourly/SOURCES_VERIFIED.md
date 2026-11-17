# Phase 2b — Hourly sources, verified

Verified live 2026-11-15 (America/Chicago) against the Socrata metadata API
(`https://data.cityofchicago.org/api/views/<id>`) and the Socrata catalog
API, plus the Open-Meteo archive API. **Nothing below is assumed — every
claim was observed in an API response or in the pulled data.**

Ingest script: `hourly_ingest.py` (`--steps segments,map,congestion,region,
crashes,aggregate,weather,process-congestion,verify`).
Provider interface: `traffic_flow.py`.

## ⚠️ Headline findings (read first)

1. **All five dataset IDs in the original Phase 2b brief are wrong.**
   `8v94-mt3p`, `3v54-hgmi`, `n4j6-ws8y`, `ft7m-7xmn`, `85fq-67pd` all return
   HTTP 404 `{"code": "not_found"}` from the Socrata metadata API. The real
   IDs were found through the Socrata catalog API and are used throughout
   (table below).
2. **The Traffic Tracker congestion feed is DEAD. Data ends 2026-04-30
   01:10:21.** Both "current" datasets are labelled *Deprecated* / "no
   longer updated", and the newest row in both historical datasets is
   2026-04-30T01:10:21. Congestion therefore **cannot cover the Phase 2
   holdout window (2026-08-01 → 2026-10-29) at all** — it is usable only as
   a training-period feature (2025-01-01 → 2026-04-30). No newer congestion
   dataset exists in the catalog (searched "congestion" and "Traffic
   Tracker Congestion", 2026-11-15). If the hourly index needs a live
   congestion signal after April 2026, it must come from TomTom/HERE (keys
   required; none created — see `traffic_flow.py`) or another source.
3. **The segment table is 101M rows.** Raw download of the congestion window
   was not attempted: all congestion pulls are **server-side hourly
   aggregates** (mean of valid 10-minute speed readings + `n_reads`), filtered
   to the ~200 segments nearest the cameras, in monthly chunks.
4. **Crashes and weather DO cover the full window** (2025-01-01 →
   2026-10-29) and are the only hourly sources that reach the holdout window.

## Source table (verified IDs)

| Source | Socrata / API id | Rows in source (total) | Window ingested |
|---|---|---|---|
| Traffic Tracker — Congestion by Segments, historical 2024–2026 | `4g9f-3jbs` | 101,139,655 | 2025-01-01 → 2026-04-30 (feed end) |
| Traffic Tracker — Congestion by Regions, historical 2018–2026 | `kf7e-cur8` | not counted (29 regions × ~10-min slices) | 2025-01-01 → 2026-04-30 (feed end) |
| Traffic Tracker — Congestion by Segments, "current" (DEPRECATED snapshot) | `n4j6-wkkf` | 1,257 | snapshot only, all `_last_updt` = 2026-04-30 01:10:xx |
| Traffic Tracker — Congestion by Regions, "current" (DEPRECATED snapshot) | `t2qc-9pjd` | 29 | snapshot only, all `_last_updt` = 2026-04-30 01:10:21 |
| Traffic Crashes — Crashes | `85ca-t3if` | 1,113,360 | 2025-01-01 → 2026-10-29 |
| Open-Meteo archive, Chicago 41.8781,-87.6298 | `archive-api.open-meteo.com/v1/archive` | — (API, no key) | 2025-01-01 → 2026-10-29 |

Brief IDs that 404: `8v94-mt3p` (was: current by segment), `3v54-hgmi`
(was: current by region), `n4j6-ws8y` (was: historical by segment),
`ft7m-7xmn` (was: historical by region), `85fq-67pd` (was: crashes).
`u6pd-qa9d` (People) and `68nd-jvt3` (Vehicles) were **not** pulled — no
concrete need arose; the Crashes table alone carries date/time + lat/lon +
type/severity.

## 1. Congestion by segment — `4g9f-3jbs`

- **Metadata:** name "Chicago Traffic Tracker - Historical Congestion
  Estimates by Segment - 2024-2026". Description: *"This dataset is no
  longer updated. … starting 6/11/2024 (except for a single time slice on
  3/8/2024). Older records are in sxs8-h27x."* `rowsUpdatedAt` epoch
  1780207661 (≈ 2026-05-31, metadata refresh — data itself ends earlier).
- **Columns actually observed (22):** `time` (calendar_date), `segment_id`
  (number), `speed` (number, mph), `street`, `direction`, `from_street`,
  `to_street`, `length` (number), `street_heading`, `comments`,
  `bus_count`, `message_count`, `hour` (number 0–23), `day_of_week`,
  `month`, `record_id`, `start_latitude`, `start_longitude`,
  `end_latitude`, `end_longitude`, `start_location` (point),
  `end_location` (point). **There is no congestion-level / text field** —
  only numeric `speed`. No `congestion` column exists.
- **Timestamp fields:** `time` is a full timestamp (e.g.
  `2025-06-02T00:01:02.000`, Chicago local, naive); `hour` is the derived
  hour of `time`. **Granularity is ~10 minutes, not hourly**: one segment
  on 2025-06-02 shows slices at 00:01, 00:10, 00:20, 00:30, 00:40, 00:50,
  01:01, … (6 slices/hour). Hourly values in this ingest are the mean of
  the valid slices in each hour.
- **Update cadence:** dead. Last row `2026-04-30T01:10:21`. First row
  `2024-03-05T09:20:34`. The feed stopped updating at the end of April 2026.
- **Row counts observed (SoQL `count(*)`):** total 101,139,655; 2025 full
  year 53,589,648; 2026-01-01 → 2026-04-30 16,954,071; one day
  (2025-06-02) 124,593 rows across 1,047 distinct segments.
- **Missing-data sentinel:** `speed = -1` means "no reading"
  (`bus_count = 0`, `message_count = 0`). On 2025-06-02, 57,053 of 124,593
  rows (45.8%) are `-1`. All `-1` rows are excluded before averaging;
  hours with no valid slice are absent from the hourly file (not zero-filled).
- **What was ingested:** server-side hourly aggregates for the 212 camera-
  nearest segments (later remapped, see below), monthly chunks, `speed >= 0`:
  **1,947,880 rows**, 2025-01-01 00:00 → 2026-04-30 01:00, 193 distinct
  segments. `n_reads` per segment-hour: min 1, median 4, max 6 (of 6
  possible 10-min slices). All 1,947,880 timestamps resolve exactly to the
  hour (0 off-hour). Median segment coverage 10,498 of 11,618 possible
  hours (90%); 9 of 193 segments cover <50% of hours; one segment has only
  12 hourly rows.
- **Files:** `data/hourly/raw/congestion_segments_hourly_raw.csv` (95 MB),
  `data/hourly/processed/congestion_hourly.parquet`.

## 2. Congestion by region — `kf7e-cur8`

- **Metadata:** name "Chicago Traffic Tracker - Historical Congestion
  Estimates by Region - 2018-2026". Description: *"This dataset is no
  longer updated. … starting in approximately March 2018."*
- **Columns actually observed (20):** `time` (calendar_date), `region_id`
  (number 1–29), `speed` (number), `region`, `bus_count`, `num_reads`,
  `hour`, `day_of_week`, `month`, `description`, `record_id`, `west`,
  `east`, `south`, `north`, `nw_location` (point), `se_location` (point),
  plus 3 computed-region columns. Same `time` + `hour` timestamp scheme,
  same ~10-minute cadence (3,886 rows on 2025-06-02 = 29 regions × ~134).
- **Update cadence / range:** first row 2018-03-09T15:40:41; last row
  **2026-04-30T01:10:21** (same feed death as segments).
- **Missing-data sentinel:** `speed = 0` (301 of 3,886 rows on 2025-06-02,
  7.7%). Excluded (`speed > 0`) before averaging.
- **What was ingested:** server-side hourly aggregates for all 29 regions,
  monthly chunks: **323,594 rows**, 2025-01-01 00:00 → 2026-04-30 01:00
  (of 29 × 11,618 = 336,922 possible region-hours, 96%).
- **Files:** `data/hourly/raw/congestion_regions_hourly_raw.csv`,
  `data/hourly/processed/congestion_region_hourly.parquet`.
- **Role:** fallback / coverage check for the segment file; same feed, so it
  cannot extend coverage past 2026-04-30 either.

## 3. "Current" congestion datasets — snapshots only (confirmed)

- `n4j6-wkkf` (by segment): 1,257 rows. Description: *"This dataset is no
  longer updated and contains no data not also present in 4g9f-3jbs."*
  Columns are Socrata-mangled text: `segmentid`, `street`, `_direction`,
  `_fromst`, `_tost`, `_length`, `_strheading`, `_comments`, `start_lon`,
  `_lif_lat` (= start latitude), `_lit_lon` (= end longitude), `_lit_lat`
  (= end latitude), `_traffic` (speed, text; `-1` = missing),
  `_last_updt` (text, e.g. `2026-04-30 01:10:20.0`). Sample rows confirm
  `_last_updt` is frozen at the final feed tick for live segments.
- `t2qc-9pjd` (by region): 29 rows, `region`, `_region_id`, `_west`,
  `_east`, `_south`, `_north`, `_description`, `current_speed`,
  `_last_updt` — all `_last_updt` = 2026-04-30 01:10:21.
- **Surprise — the snapshot mixes live and decade-dead segments:** 1,047
  segments have `_last_updt` in 2026, but **210 segments were last updated
  in 2010–2012** (186 in 2010, 19 in 2011, 5 in 2012). Those dead segments
  have geometry but no rows in the 2024–2026 historical table. 19 of them
  were the nearest segment for 41 cameras; those cameras were remapped to
  their nearest *live* segment (see §6). The snapshot was used **only for
  segment geometry**; no speeds were taken from it.
- **Files:** `data/hourly/raw/segments_snapshot.csv` (1,257 rows).

## 4. Traffic Crashes — `85ca-t3if`

- **Metadata:** name "Traffic Crashes - Crashes". Live dataset, updated
  2026-11-14T16:32:34Z. Description notes IDOT_CONTROL_NO added 5/20/2026.
  Records are CPD E-Crash reports for crashes on city streets.
- **Columns actually observed (50):** `crash_record_id`, `crash_date_est_i`,
  `crash_date` (calendar_date), `posted_speed_limit`,
  `traffic_control_device`, `device_condition`, `weather_condition`,
  `lighting_condition`, `first_crash_type`, `trafficway_type`, `lane_cnt`,
  `alignment`, `roadway_surface_cond`, `road_defect`, `report_type`,
  `crash_type`, `intersection_related_i`, `private_property_i`,
  `hit_and_run_i`, `damage`, `date_police_notified`, `prim_contributory_cause`,
  `sec_contributory_cause`, `street_no`, `street_direction`, `street_name`,
  `beat_of_occurrence`, `photos_taken_i`, `statements_taken_i`, `dooring_i`,
  `work_zone_i`, `work_zone_type`, `workers_present_i`, `num_units`,
  `crash_month`, `most_severe_injury`, `injuries_total`, `injuries_fatal`,
  `injuries_incapacitating`, `injuries_non_incapacitating`,
  `injuries_reported_not_evident`, `injuries_no_indication`,
  `injuries_unknown`, `crash_hour` (number 0–23), `crash_day_of_week`,
  `idot_control_no`, `latitude`, `longitude`, `location` (point),
  `:@computed_region_rpca_8um6`.
- **Timestamp fields:** `crash_date` is a **full timestamp** (e.g.
  `2013-03-03T16:48:00.000`, Chicago local) — there is no separate
  time-of-day column; `crash_hour` (0–23) is the derived hour and agrees
  with `crash_date`. Hourly aggregation floors `crash_date` to the hour.
- **Update cadence / range:** live. `min(crash_date)` 2013-03-03T16:48,
  `max(crash_date)` 2026-11-14T01:38 (queried 2026-11-15).
- **Row counts observed (SoQL `count(*)`):** total 1,113,360; window
  2025-01-01 → 2026-10-29: **202,151**; of those, with lat/lon: **199,936**
  (2,215 rows, 1.1%, have no coordinates and are excluded from the
  per-camera aggregation but retained in the raw file); holdout window
  2026-08-01 → 2026-10-29: 28,417.
- **What was ingested (raw):** `crash_record_id, crash_date, crash_hour,
  latitude, longitude, crash_type, first_crash_type, most_severe_injury,
  injuries_total, injuries_fatal, prim_contributory_cause`, `$where`
  crash_date in window, paged 50k at a time: **202,151 rows** — exactly the
  Socrata count — 2025-01-01 00:00 → 2026-10-29 23:50.
- **What was ingested (aggregated):** hourly counts within **200 m** of each
  camera (haversine, chunked 20k rows × 361 cameras; never held the full
  join in memory). **59,158 camera-hours with ≥1 crash; 60,513 crashes
  attributed to ≥1 camera** (30.3% of the 199,936 geocoded crashes — the
  rest are >200 m from every red-light camera); all 361 cameras with
  coordinates have ≥1 attributed crash; max 6 crashes in one camera-hour.
  Citywide hourly file: 16,008 hours, total **199,936** — exactly the
  geocoded Socrata count, so no crashes were lost in aggregation.
- **Sanity — hour distribution (citywide, window total per hour-of-day):**
  00h 4,318 · 01h 3,688 · 02h 3,201 · 03h 2,664 · **04h 2,255 (quietest)** ·
  05h 2,793 · 06h 4,124 · 07h 9,079 · 08h 11,629 · 09h 9,193 · 10h 9,103 ·
  11h 9,939 · 12h 11,210 · 13h 11,824 · 14h 13,319 · **15h 16,051 (peak)** ·
  16h 15,560 · 17h 14,615 · 18h 11,730 · 19h 8,783 · 20h 7,154 · 21h 6,453 ·
  22h 5,968 · 23h 5,283. Peaks at the 15:00–17:00 rush, trough at 04:00 —
  plausible.
- **Files:** `data/hourly/raw/crashes_20250101_20261029.csv` (202,151 rows),
  `data/hourly/processed/crashes_hourly_per_camera.parquet` (59,158 rows,
  only non-zero camera-hours),
  `data/hourly/processed/crashes_hourly_citywide.parquet` (16,008 rows).

## 5. Hourly weather — Open-Meteo archive (no key)

- **Endpoint:** `https://archive-api.open-meteo.com/v1/archive`,
  `latitude=41.8781, longitude=-87.6298`,
  `hourly=temperature_2m,precipitation,snowfall,wind_speed_10m`,
  `timezone=America/Chicago`, `start_date=2025-01-01`,
  `end_date=2026-10-29`. Free, no API key (OpenWeatherMap deliberately
  **not** used — no key on file).
- **Columns observed:** `time` (ISO hourly, America/Chicago),
  `temperature_2m` (°C), `precipitation` (mm), `snowfall` (cm),
  `wind_speed_10m` (km/h). Stored as `timestamp, temperature_2m_c,
  precipitation_mm, snowfall_cm, wind_speed_10m_kmh`.
- **Coverage:** **16,008 rows = 16,008 expected hours → 0 gaps**,
  0 duplicate timestamps, 0 nulls in any column. Range 2025-01-01 00:00 →
  2026-10-29 23:00. Temperature range observed −23.0 °C … 35.8 °C.
- **Files:** `data/hourly/raw/weather_chicago_hourly.csv`,
  `data/hourly/processed/weather_hourly.parquet`.

## 6. Camera → segment mapping

- Coordinates from `data/processed/red_light_features.parquet` (365 cameras).
  **4 cameras have no coordinates and cannot be mapped or crash-counted:
  1623, 2552, 2592, 2662** (confirmed in the parquet; `camera_profiles.csv`
  only lists 300 cameras, all with coordinates).
- Nearest segment = minimum haversine distance to the segment midpoint or
  either endpoint. 361 cameras mapped to 212 distinct segments; median
  distance 16 m, 75th percentile 226 m — but 94 cameras are >200 m and 74
  are >500 m from their nearest segment (max 2,410 m, cameras 1453/1454).
  Those are weak proxies and are flagged by the `distance_to_segment_m`
  column.
- **Remap (2026-11-15):** 19 of the 212 nearest segments were dead
  2010–2012 snapshot segments with no historical rows, leaving 41 cameras
  with zero congestion coverage. Those 41 were remapped to their nearest
  **live** segment (one with rows in `4g9f-3jbs` in-window). All 17 newly
  needed segments were already in the pulled aggregates (they were nearest
  segments for other cameras), so no extra pull was required. Remapped
  distances are larger (median 802 m, max 2,415 m) — a real quality
  trade-off, recorded per camera in `remapped_to_live_segment = true` with
  `original_segment_id` preserved.
- **Final coverage:** all **361** cameras with coordinates map to a live
  segment; 193 distinct live segments in use; the congestion parquet
  covers all 361 cameras.
- **File:** `data/hourly/processed/camera_segment_map.csv` (365 rows).

## 7. TomTom / HERE — not used

No accounts, keys, or sign-ups. `traffic_flow.py` defines the plug-in
interface (`FlowProvider` base, `CongestionFlowProvider` backed by the
ingested aggregates, `TomTomFlowProvider` / `HereFlowProvider`
placeholders that raise `FlowProviderError("…no API key configured — set
the TOMTOM_API_KEY / HERE_API_KEY environment variable…")`). No network
calls to TomTom/HERE were made.

## 8. Files written (all new; no Phase 2 artifact modified)

| File | Rows | Range |
|---|---|---|
| `data/hourly/raw/segments_snapshot.csv` | 1,257 | snapshot, `_last_updt` 2010-07-21 → 2026-04-30 |
| `data/hourly/raw/congestion_segments_hourly_raw.csv` | 1,947,880 | 2025-01-01 00:00 → 2026-04-30 01:00 |
| `data/hourly/raw/congestion_regions_hourly_raw.csv` | 323,594 | 2025-01-01 00:00 → 2026-04-30 01:00 |
| `data/hourly/raw/crashes_20250101_20261029.csv` | 202,151 | 2025-01-01 00:00 → 2026-10-29 23:50 |
| `data/hourly/raw/weather_chicago_hourly.csv` | 16,008 | 2025-01-01 00:00 → 2026-10-29 23:00 |
| `data/hourly/processed/camera_segment_map.csv` | 365 | — |
| `data/hourly/processed/congestion_hourly.parquet` | 1,947,880 | 2025-01-01 00:00 → 2026-04-30 01:00 |
| `data/hourly/processed/congestion_region_hourly.parquet` | 323,594 | 2025-01-01 00:00 → 2026-04-30 01:00 |
| `data/hourly/processed/crashes_hourly_per_camera.parquet` | 59,158 | 2025-01-01 00:00 → 2026-10-29 23:00 |
| `data/hourly/processed/crashes_hourly_citywide.parquet` | 16,008 | 2025-01-01 00:00 → 2026-10-29 23:00 |
| `data/hourly/processed/weather_hourly.parquet` | 16,008 | 2025-01-01 00:00 → 2026-10-29 23:00 |
| `data/hourly/INGEST_SUMMARY.json` | — | row counts + ranges per file |

Total on disk: ~166 MB under `data/hourly/`.

## 9. Caveats for the modelling step (worker B)

- Congestion ends 2026-04-30: any hourly feature built from it is
  **missing for the entire holdout window**. Do not forward-fill it across
  the 3-month gap as if it were observed. Region aggregates have the same
  end date.
- All timestamps are naive hourly labels in a flat 24-rows-per-day grid.
  Checked explicitly: the weather file **contains a 02:00 row on the
  spring-forward days** (2025-03-09, 2026-03-08) and a single 01:00 row on
  the fall-back day (2025-11-02) — i.e. Open-Meteo returns a plain naive
  hourly sequence, not a true DST-aware local clock. The crash and
  congestion files use the same naive hourly grid, so the three sources
  join cleanly on `timestamp` — but the 02:00 label on the two
  spring-forward days does not correspond to a real local hour. Worker B
  should be aware of this if aligning to true local time or to UTC.
- `speed = -1` (segments) and `speed = 0` (regions) are missing sentinels,
  already excluded; `n_reads` (1–6) is the per-hour quality signal.
- 41 remapped cameras sit a median 802 m from their congestion segment —
  treat their congestion feature as low-trust.
- Crash attribution is radius-sensitive: 200 m captures 30.3% of geocoded
  crashes. A crash near two cameras counts for both.
- `requirements.txt` was **not** changed: the ingest uses only `requests`,
  `pandas`, `numpy`, `pyarrow` — all already pinned there. Nothing new was
  installed.
