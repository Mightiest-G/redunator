# Redunator Phase 2 — Live Sources Survey (VERIFIED)

Worker A — live-source survey only. No provider code built, no sign-ups, no API keys created.
Verification run: **Mon 2026-11-16 ~05:20–05:23 CST** (11:20–11:23 UTC).
All timestamps below are as observed in that window unless noted.

## Summary verdicts

| # | Source | Verdict | One-line finding |
|---|--------|---------|------------------|
| 1 | Chicago Traffic Tracker congestion (Socrata) | **STALE — DEAD** | Last row in BOTH segment + region datasets is 2026-04-30T01:10:21. No successor/replacement exists in the catalog. |
| 2 | CTA Bus Tracker API v2/v3 | **KEY-GATED** | No-key `gettime` → `{"msg": "No API access key supplied"}` (HTTP 200). Free key: sign in at ctabustracker.com, request under My Account; 100k req/day default (v3 guide, 2025-04-21). |
| 3 | CTA Train Tracker API | **KEY-GATED** | No-key call → `errCd 101 "Invalid API key"` (HTTP 200). Free key: application form at transitchicago.com/developers/traintrackerapply/ (agree to Developer License Agreement). |
| 4 | CTA Customer Alerts API | **LIVE (no key)** | No-key call returned live alerts, `TimeStamp 2026-11-16T05:22:37`, `ErrorCode 0`. Alerts only — no vehicle positions. |
| 5 | Divvy GBFS (station_status / station_information / free_bike_status) | **LIVE (no key)** | 2,079 stations; median `last_reported` 2.5 min old; 1,145 stations <5 min. Bike/dock counts only — no trip counts, no speeds. |
| 6 | Open-Meteo forecast API (Chicago) | **LIVE (no key)** | `current.time 2026-11-16T05:15` (5 min before request), 15-min update interval, 72 h hourly forecast returned. Confirmed no key. |
| 7 | National Weather Service API (api.weather.gov) | **LIVE (no key)** | KMDW latest obs 2026-11-16T10:55Z (04:55 CST); hourly forecast grid LOT 76,73. No key; User-Agent header required by ToS. |
| 8 | Beach Weather Stations — Automated Sensors (Socrata k7hf-8y75) | **LIVE-ish (no key)** | Latest measurement 2026-11-16T04:00 CST (~1.3 h old at fetch); hourly cadence; wind speed + rain intensity. Secondary weather source. |
| 9 | TomTom Traffic Flow API | **KEY-GATED** | No-key call → HTTP 401 `{"code":"Unauthorized","message":"You are missing valid authentication credentials"}`. |
| 10 | HERE Traffic Flow API | **KEY-GATED** | No-key call → HTTP 401 `{"error":"Unauthorized","error_description":"Token or apiKey is missing."}`. |
| 11 | Chicago data catalog — everything else swept | **NOT USEFUL / STALE** | 407 unique hits across 18 search terms; no live traffic-camera feed, no snow-plow feed, no live congestion feed. Details in §1. |
| 12 | Traffic Crashes 85ca-t3if (context, not real-time) | **LIVE but historical** | `data_updated_at 2026-11-15T16:54:04Z`. Records arrive when reports finalize — not a real-time stream. Already known. |

**Most promising no-key live source for Redunator: Divvy GBFS station_status** — the only city-mobility feed that is truly live (minute-scale) and key-free, plus Open-Meteo/NWS for live weather. Caveat: Divvy measures bike-share activity, not car traffic; it is a weak/indirect proxy at best (see §3). There is **no no-key live car-traffic/congestion source** — the Traffic Tracker feed is dead and TomTom/HERE are key-gated.

---

## 1. Chicago data catalog sweep (Socrata catalog API)

Method: `https://data.cityofchicago.org/api/catalog/v1?search_context=data.cityofchicago.org&q=<term>&limit=50`
for 18 terms: `traffic, congestion, real-time, live, current, vehicles, speed, camera, CTA, bus, train, Divvy, snow, plow, events, crash, street, road`.
Result: **407 unique resources** (datasets + charts/filters/maps). Sorted by `data_updated_at`, the freshest traffic-relevant hits are listed below; the full sweep was dominated by unrelated "current"/"events" admin datasets.

### 1a. Congestion — the dead feed, confirmed, no successor

| Dataset | ID | data_updated_at / rowsUpdatedAt | Latest actual row (SoQL `max(time)` / `ORDER BY time DESC`) |
|---|---|---|---|
| Chicago Traffic Tracker — Congestion Estimates by Segments — Deprecated | n4j6-wkkf | 2026-04-30T06:33:30Z | — (deprecated live feed) |
| Chicago Traffic Tracker — Congestion Estimates by Regions — Deprecated | t2qc-9pjd | 2026-04-30T06:33:30Z | — (deprecated live feed) |
| Chicago Traffic Tracker — Historical Congestion Estimates by Segment — 2024-2026 | 4g9f-3jbs | rowsUpdatedAt 2026-05-31T06:07:41Z | **2026-04-30T01:10:21** (segment 1307, Clark SB, speed −1) |
| Chicago Traffic Tracker — Historical Congestion Estimates by Region — 2018-2026 | kf7e-cur8 | rowsUpdatedAt 2026-08-06T15:09:16Z | **2026-04-30T01:10:21** (region 29, Downtown Lakefront, speed 0) |
| Historical Congestion by Segment — 2018-2023 | sxs8-h27x | 2025-05-15T22:28:10Z | older archive |
| Historical Congestion by Segment — 2011-2018 | 77hq-huss | 2018-05-04T17:28:41Z | older archive |
| Historical Congestion by Region — 2013-2018 | emtn-qqdi | 2018-05-03T20:11:15Z | older archive |

Notes:
- The description of 4g9f-3jbs begins **"This dataset is no longer updated."** Same for kf7e-cur8.
- `rowsUpdatedAt` (2026-05-31 / 2026-08-06) is misleading — it reflects later metadata/admin touches, not new data. The newest `time` value in the data is 2026-04-30T01:10:21 in both datasets (~6.5 months stale at verification).
- Catalog search `q=congestion` returned exactly these 7 datasets. **No replacement / successor congestion feed exists in the catalog.**
- Verdict: **STALE — DEAD**. Do not build a provider on it; keep only as historical training data.

### 1b. Other catalog hits of note

| Dataset | ID | data_updated_at | Observation | Verdict |
|---|---|---|---|---|
| Traffic Crashes — Crashes | 85ca-t3if | 2026-11-15T16:54:04Z | Live-ish (updated yesterday); records finalize with lag, not a stream | LIVE but historical |
| Traffic Crashes — Vehicles | 68nd-jvt3 | 2026-11-15T16:24:09Z | Companion to crashes | LIVE but historical |
| Traffic Crashes — People | u6pd-qa9d | 2026-11-14T16:11:08Z | Companion to crashes | LIVE but historical |
| Red Light Camera Violations | spqx-js37 | 2026-11-16T10:23:12Z | Updated today — but DAILY counts, ~14-day lag (already known) | LIVE, daily only |
| Speed Camera Violations | hhkd-xvj4 | 2026-11-16T10:07:45Z | Same daily/lag profile as red-light | LIVE, daily only |
| Speed Camera Locations | 4i42-qv3h | 2026-11-06T11:12:21Z | Static camera locations, not a feed | STATIC |
| Average Daily Traffic Counts | gc7y-n4xa | 2026-11-15T14:02:26Z | Static/periodic counts, not live | NOT USEFUL (live) |
| Divvy Bicycle Stations | bbyy-e7gq | 2026-11-15T12:04:40Z | Socrata mirror of GBFS station list (static snapshot of a live feed) | Mirror — use GBFS directly (§3) |
| CTA — Ridership — Daily Boarding Totals | 6iiy-9s97 | 2026-10-28T16:53:41Z | Daily aggregates, ~3-week lag | NOT USEFUL (live) |
| CTA — Ridership — 'L' Station Entries — Daily Totals | 5neh-572f | 2026-10-28T16:52:14Z | Same lag profile | NOT USEFUL (live) |
| CTA — Ridership — Bus Routes — Daily Totals by Route | jyb9-n7fm | 2026-10-28T16:50:20Z | Same lag profile | NOT USEFUL (live) |
| Towed Vehicles | ygr5-vcbg | 2026-11-16T11:03:46Z | Verified sample row `tow_date 2026-11-16T00:00:00` — near-real-time, but tows ≠ traffic risk signal | LIVE, NOT USEFUL |
| Beach Weather Stations — Automated Sensors | k7hf-8y75 | 2026-11-16T10:48:22Z | See §8 — hourly lakefront weather sensors | LIVE-ish |
| Transportation Dept Permits — Street Closures (filter) | jdis-5sry | 2026-11-16T07:52:41Z | Current/future closures — could be a weak event feature later; not traffic flow | POSSIBLE LATER |
| Snow Route Parking Restrictions | i6k4-giaj | 2021-12-06T23:19:08Z | Static restrictions | STALE |
| Snow Alerts | jpfu-k3rv | 2012-01-25T18:23:09Z | Ancient | STALE |

Negative results (searched, nothing found):
- **`q=plow` → zero results.** No snow-plow feed in the catalog.
- **No live traffic-camera feed.** `q=camera` hits are only static camera locations + violation-count datasets.
- **`q=live` / `q=real-time`** returned no live traffic dataset (top hits were admin datasets such as City-Owned Land Inventory and Workforce Vacancies).

---

## 2. CTA real-time feeds — key-gating verified

### 2a. CTA Bus Tracker API (v2 endpoint, v3 current)

- Endpoint tested (no key): `http://www.ctabustracker.com/bustime/api/v2/gettime?format=json`
- Response: HTTP 200, body `{"bustime-response": {"error": [{"msg": "No API access key supplied"}]}}`
- Verdict: **KEY-GATED** (confirmed live service — it answers, but refuses without a key).
- Docs: Bus Tracker API Developer Guide v3.0, rev. 2025-04-21 — https://lapi.transitchicago.com/assets/1/6/cta_Bus_Tracker_API_Developer_Guide_and_Documentation_2025-04-21.pdf
- Signup path (from the v3 guide, verified via search of CTA docs): **sign in to your CTA Bus Tracker account at https://www.ctabustracker.com, then request an API key under "My Account". One key per account; the key is emailed after approval.** No payment mentioned — free tier.
- Free-tier facts (v3 guide): default limit **100,000 requests/day** per key (v2 guide said 10,000/day); higher cap on request. Data updated every minute. Key is a 25-digit string passed as `?key=...`.
- Developer hub: https://www.transitchicago.com/developers/bustracker/
- Note: the `/bustime/api/v2/doc` URL also returns the same "No API access key supplied" error — docs are not fetchable without a key from that host; use the PDF above.

### 2b. CTA Train Tracker API

- Endpoint tested (no key): `https://lapi.transitchicago.com/api/1.0/ttarrivals.aspx?key=&mapid=40380&outputType=JSON`
- Response: HTTP 200, body `{"ctatt":{"tmst":"2026-11-16T05:20:44","TimeStamp":"2026-11-16T05:20:44","errCd":"101","errNm":"Invalid API key"}}`
- Verdict: **KEY-GATED** (confirmed live service — server clock 05:20:44 matches request time; it refuses only on the key).
- Signup path (verified live 2026-11-16): application form at **https://www.transitchicago.com/developers/traintrackerapply/** — "Train Tracker API Key Application". Requires agreeing to the CTA Developer License Agreement and Terms of Use. The service is described as a beta. No payment mentioned — free tier.
- Docs: https://www.transitchicago.com/developers/traintracker/ and Train Tracker API Developer Guide PDF at https://www.transitchicago.com/assets/1/6/cta_Train_Tracker_API_Developer_Guide_and_Documentation.pdf
- Note: bus and train keys are **separate** — two different keys, two different application paths.

### 2c. CTA Customer Alerts API — no key needed (bonus find)

- Endpoint tested (no key): `http://www.transitchicago.com/api/1.0/alerts.aspx?outputType=JSON`
- Response: HTTP 200, `{"CTAAlerts":{"TimeStamp":"2026-11-16T05:22:37","ErrorCode":"0","ErrorMessage":null,"Alert":[...]}}` — live alerts returned (e.g. AlertId 93306, "Temporary Bus Stop Change", SB #22/#24 Clark between Madison and Monroe, SeverityScore 9).
- Verdict: **LIVE (no key)**. Alerts/service disruptions only — no vehicle positions or arrival times. Could serve as a weak disruption/event feature; not a traffic-flow proxy.
- CTA GTFS static schedules are also public/no-key at https://www.transitchicago.com/developers/gtfs/ (HTTP 200 verified) — schedules only, not real-time.

---

## 3. Divvy GBFS feed — verified live, no key

### 3a. station_status

- URL: `https://gbfs.divvybikes.com/gbfs/en/station_status.json` (canonical index: https://gbfs.divvybikes.com/gbfs/gbfs.json → feeds also mirrored at `https://gbfs.lyft.com/gbfs/1.1/chi/en/...`)
- HTTP 200, 762,219 bytes, fetched 2026-11-16 11:20:57 UTC (05:20:57 CST) in ~1.6 s.
- Top-level: `{"data": {"stations": [...]}, "last_updated": 1794828035, "ttl": 60, "version": "1.1"}` — `last_updated` = 2026-11-16 11:20:35 UTC (22 s before fetch); **TTL 60 s** (feed refreshes every minute).
- Stations: **2,079**. Fields per station: `station_id, num_bikes_available, num_bikes_disabled, num_docks_available, num_docks_disabled, is_installed, is_renting, is_returning, last_reported, legacy_id, num_ebikes_available, num_scooters_available, num_scooters_unavailable, eightd_has_available_keys`.
- Freshness of `last_reported` (vs fetch time 11:20:57 UTC):
  - median: 11:18:30 UTC — **2.45 min old**
  - max (freshest): 11:20:19 UTC — **0.63 min old**
  - <5 min old: **1,145 / 2,079** stations; <15 min: 1,165; <60 min: 1,165
  - ≥60 min old: **914 / 2,079** — all still `is_installed=1` (900 also `is_renting=1`); oldest `last_reported` 2025-06-13. These look like low-activity / virtual / seasonal stations whose status rarely changes; treat as a data-quality caveat, not a feed outage — the active core (~1,165 stations) reports within 15 min.
- Totals at fetch: 6,512 bikes available (3,331 e-bikes), 931 scooters available, 14,013 docks available; 2,063/2,079 stations renting and returning.
- Verdict: **LIVE**.

### 3b. station_information

- URL: `https://gbfs.divvybikes.com/gbfs/en/station_information.json` — HTTP 200, 1,134,473 bytes.
- Fields per station: `station_id, name, short_name, lon, lat, capacity, rental_uris, rental_methods, has_kiosk, station_type, external_id, ...` (e.g. "Western Blvd & 48th Pl", lat 41.805661, lon −87.683392, capacity 11). 2,079 stations — matches station_status. Static reference data; join key is `station_id` (also `legacy_id`).

### 3c. free_bike_status + system_alerts (checked same run)

- `free_bike_status.json`: HTTP 200, 700,453 bytes — **2,136 dockless bikes** with per-bike `bike_id, lon, lat, is_reserved, is_disabled, type` (sample: electric_bike at 41.882348776, −87.639356852). Live positions of free-floating bikes/scooters.
- `system_alerts.json`: HTTP 200, `{"data": {"alerts": []}, "last_updated": 1794828050, "ttl": 60}` — no active alerts at fetch.
- GBFS index lists: gbfs, ebikes_at_stations, system_information, station_information, station_status, free_bike_status, system_hours, system_calendar, system_regions, system_pricing_plans, system_alerts, gbfs_versions. **No trip-history feed exists in GBFS.**

### 3d. What Divvy can and cannot tell us (honest assessment)

- CAN: minute-scale citywide bike-share activity — bikes/docks available per station, net flow if polled repeatedly (deltas in `num_bikes_available` ≈ rentals/returns), dockless bike positions, station outages. A plausible weak proxy for "people moving around / activity level" by hour and neighborhood.
- CANNOT: car traffic volume, vehicle speeds, congestion, red-light risk directly. No trip counts, no trip origins/destinations, no speeds, no road-segment data. Any Redunator use would be as a *correlate* (activity/errand pressure) to be validated against the crash/hourly model — not a replacement for the dead congestion feed.
- Recommendation: if Phase 2 polls one no-key mobility feed, this is it — but validate the correlation before wiring it into scoring.

---

## 4. Open-Meteo live endpoints — verified, no key

- URL tested: `https://api.open-meteo.com/v1/forecast?latitude=41.8781&longitude=-87.6298&current=temperature_2m,precipitation,wind_speed_10m&hourly=temperature_2m,precipitation_probability&forecast_days=3&timezone=America%2FChicago`
- Response: HTTP 200, no key sent, no key requested.
- `current`: `{"time": "2026-11-16T05:15", "interval": 900, "temperature_2m": -0.4, "precipitation": 0.00, "wind_speed_10m": 6.9}` — units °C / mm / km/h; `timezone America/Chicago (GMT-6)`, `utc_offset_seconds -21600`, `elevation 241.0`.
- What "current" means: model-analysis values stamped **05:15 Chicago time, 5 min before the 05:20 request**, on a **900 s (15-min) update interval**. It is a nowcast/model blend, not a raw station observation.
- Forecast horizon: hourly arrays returned for **72 h** (2026-11-16T00:00 → 2026-11-18T23:00 Chicago) — `temperature_2m` and `precipitation_probability` (all 0% in this window). Default horizon is 7 days; `forecast_days` controls it (up to 16).
- Verdict: **LIVE, no key**. Already used for historical weather; the same endpoint covers live current + forecast. Free for non-commercial use; commercial use requires the paid API (per Open-Meteo terms — provider worker should note this, not re-verify pricing here).

---

## 5. National Weather Service API (secondary weather, no key)

- Base: `https://api.weather.gov/points/41.8781,-87.6298` — HTTP 200 (with `User-Agent: RedunatorSurvey/1.0 (research)` header; NWS requires a User-Agent).
- Resolves to grid **LOT (Chicago/Romeoville office), gridX 76, gridY 73**; forecast URLs: `https://api.weather.gov/gridpoints/LOT/76,73/forecast` and `.../forecast/hourly`.
- Hourly forecast: HTTP 200; first period `2026-11-16T04:00:00-06:00`, 41 °F, wind 5 mph SSW, "Mostly Clear".
- Latest observation, station **KMDW** (Chicago Midway): `https://api.weather.gov/stations/KMDW/observations/latest` — HTTP 200; `timestamp 2026-11-16T10:55:00+00:00` (04:55 CST, ~25 min before fetch), temperature 2 °C, wind 0 km/h, `textDescription "Clear"`, `precipitationLastHour null`.
- Verdict: **LIVE, no key**. Good secondary/ground-truth weather source (real station observations, unlike Open-Meteo's model blend). Rate limits are generous but undocumented; send a descriptive User-Agent.

---

## 6. Commercial traffic APIs — key-gating verified (no sign-up)

| API | No-key test | Response | Verdict |
|---|---|---|---|
| TomTom Traffic Flow | `https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json?point=41.8781,-87.6298&key=` | HTTP **401** `{"detailedError":{"code":"Unauthorized","message":"You are missing valid authentication credentials"}}` | **KEY-GATED** |
| HERE Traffic Flow | `https://data.traffic.hereapi.com/v7/flow?in=41.8781,-87.6298;r=500&apiKey=` | HTTP **401** `{"error":"Unauthorized","error_description":"Token or apiKey is missing."}` | **KEY-GATED** |

No accounts created, no keys requested. Both vendors advertise free tiers on their public pricing pages (TomTom: free daily transaction allowance; HERE: free base plan) — provider worker should confirm current free-tier limits from the vendors' own pricing pages before recommending either; not re-verified here.

---

## 7. Other live-ish sources checked (context)

| Source | Observation | Verdict |
|---|---|---|
| Beach Weather Stations — Automated Sensors (k7hf-8y75) | Latest row: Oak Street Weather Station, `measurement_timestamp 2026-11-16T04:00:00` (~1.3 h before fetch), air temp 7.6 °C, humidity 69%, wind speed 1 (units as recorded), wind direction 323°, rain intensity 0, barometric pressure 1011.3. `rowsUpdatedAt 1794826102` = 2026-11-16 10:48:22 UTC. Columns: station_name, measurement_timestamp, air_temperature, wet_bulb_temperature, humidity, rain_intensity, interval_rain, total_rain, precipitation_type, wind_direction, wind_speed, maximum_wind_speed, barometric_pressure, solar_radiation, heading, battery_life. Hourly cadence, lakefront stations only. | **LIVE-ish** — secondary weather; lakefront-only coverage, hourly not minute-scale |
| Towed Vehicles (ygr5-vcbg) | Updated 2026-11-16T11:03:46Z; sample tow dated today. Near-real-time, but tow counts are not a traffic-risk signal. | LIVE, NOT USEFUL |
| Open Air Chicago (air-quality sensors) | Day aggregations rtmx-vkjr updated 2026-11-15T16:03:02Z; individual measurements xfya-dxtq 2026-11-14T19:30:36Z. Not traffic. | NOT USEFUL |

---

## 8. Raw verification commands (as run)

```bash
# --- time base ---
date -u '+UTC %Y-%m-%d %H:%M:%S'; TZ=America/Chicago date '+Chicago %Y-%m-%d %H:%M:%S %Z'
# UTC 2026-11-16 11:20:42 ; Chicago 2026-11-16 05:20:42 CST

# --- 1. Socrata catalog sweep (18 terms; 407 unique hits) ---
curl -s -m 30 'https://data.cityofchicago.org/api/catalog/v1?search_context=data.cityofchicago.org&q=traffic&limit=50'
# repeated for q=congestion, real-time, live, current, vehicles, speed, camera, CTA, bus, train, Divvy, snow, plow, events, crash, street, road
curl -s -m 30 'https://data.cityofchicago.org/api/catalog/v1?search_context=data.cityofchicago.org&q=congestion&limit=20'
curl -s -m 30 'https://data.cityofchicago.org/api/catalog/v1?search_context=data.cityofchicago.org&q=plow&limit=20'   # zero results
curl -s -m 30 'https://data.cityofchicago.org/api/catalog/v1?search_context=data.cityofchicago.org&q=snow&limit=20'

# --- 1a. congestion death check (SoQL) ---
curl -s -m 30 'https://data.cityofchicago.org/resource/4g9f-3jbs.json?$select=max(time)'
# [{"max_time":"2026-04-30T01:10:21.000"}]
curl -s -m 30 'https://data.cityofchicago.org/resource/kf7e-cur8.json?$select=max(time)'
# [{"max_time":"2026-04-30T01:10:21.000"}]
curl -s -m 30 'https://data.cityofchicago.org/resource/4g9f-3jbs.json?$limit=1&$order=time%20DESC'
curl -s -m 30 'https://data.cityofchicago.org/resource/kf7e-cur8.json?$limit=1&$order=time%20DESC'
curl -s -m 30 'https://data.cityofchicago.org/api/views/4g9f-3jbs'   # rowsUpdatedAt 1780207661 = 2026-05-31; desc: "This dataset is no longer updated."
curl -s -m 30 'https://data.cityofchicago.org/api/views/kf7e-cur8'   # rowsUpdatedAt 1786028956 = 2026-08-06; desc: "This dataset is no longer updated."

# --- 2. CTA (no key) ---
curl -s -m 30 -w '\nHTTP:%{http_code}\n' 'http://www.ctabustracker.com/bustime/api/v2/gettime?format=json'
# {"bustime-response": {"error": [{"msg": "No API access key supplied"}]}}  HTTP:200
curl -s -m 30 -w '\nHTTP:%{http_code}\n' 'https://lapi.transitchicago.com/api/1.0/ttarrivals.aspx?key=&mapid=40380&outputType=JSON'
# {"ctatt":{"tmst":"2026-11-16T05:20:44","errCd":"101","errNm":"Invalid API key"}}  HTTP:200
curl -s -m 30 -w '\nHTTP:%{http_code}\n' 'http://www.transitchicago.com/api/1.0/alerts.aspx?outputType=JSON'
# {"CTAAlerts":{"TimeStamp":"2026-11-16T05:22:37","ErrorCode":"0", ... live alerts ...}}  HTTP:200
curl -s -m 30 -L 'https://www.transitchicago.com/developers/traintrackerapply/'   # HTTP 200, key application form (live)
curl -s -m 30 -L 'https://www.transitchicago.com/developers/bustracker/'        # HTTP 200, Developer Center: Bus Tracker API
curl -s -m 30 -L 'https://www.transitchicago.com/developers/gtfs/'               # HTTP 200, GTFS static (no key)

# --- 3. Divvy GBFS (no key) ---
curl -s -m 30 -o /tmp/divvy_status.json -w 'HTTP:%{http_code} size:%{size_download}\n' 'https://gbfs.divvybikes.com/gbfs/en/station_status.json'
# HTTP:200 size:762219
curl -s -m 30 -o /tmp/divvy_info.json -w 'HTTP:%{http_code} size:%{size_download}\n' 'https://gbfs.divvybikes.com/gbfs/en/station_information.json'
# HTTP:200 size:1134473
curl -s -m 30 'https://gbfs.divvybikes.com/gbfs/gbfs.json'          # feed index (gbfs 1.1)
curl -s -m 30 -o /tmp/divvy_free.json -w 'HTTP:%{http_code} size:%{size_download}\n' 'https://gbfs.divvybikes.com/gbfs/en/free_bike_status.json'
# HTTP:200 size:700453 ; 2,136 free bikes
curl -s -m 30 'https://gbfs.divvybikes.com/gbfs/en/system_alerts.json'  # {"data":{"alerts":[]},"last_updated":1794828050,"ttl":60}

# --- 4. Open-Meteo (no key) ---
curl -s -m 30 -w '\nHTTP:%{http_code}\n' 'https://api.open-meteo.com/v1/forecast?latitude=41.8781&longitude=-87.6298&current=temperature_2m,precipitation,wind_speed_10m&hourly=temperature_2m,precipitation_probability&forecast_days=3&timezone=America%2FChicago'
# HTTP:200 ; current.time 2026-11-16T05:15, interval 900, temp -0.4 C, precip 0.00 mm, wind 6.9 km/h

# --- 5. NWS (no key, User-Agent required) ---
curl -s -m 30 -H 'User-Agent: RedunatorSurvey/1.0 (research)' 'https://api.weather.gov/points/41.8781,-87.6298'
curl -s -m 30 -H 'User-Agent: RedunatorSurvey/1.0 (research)' 'https://api.weather.gov/gridpoints/LOT/76,73/forecast/hourly'
curl -s -m 30 -H 'User-Agent: RedunatorSurvey/1.0 (research)' 'https://api.weather.gov/stations/KMDW/observations/latest'
# obs timestamp 2026-11-16T10:55:00+00:00, temp 2 C, wind 0 km/h, "Clear"

# --- 6. TomTom / HERE (no key) ---
curl -s -m 30 -w '\nHTTP:%{http_code}\n' 'https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json?point=41.8781,-87.6298&key='
# HTTP:401 Unauthorized
curl -s -m 30 -w '\nHTTP:%{http_code}\n' 'https://data.traffic.hereapi.com/v7/flow?in=41.8781,-87.6298;r=500&apiKey='
# HTTP:401 Unauthorized

# --- 7. context checks ---
curl -s -m 30 'https://data.cityofchicago.org/resource/k7hf-8y75.json?$limit=1&$order=measurement_timestamp%20DESC'
curl -s -m 30 'https://data.cityofchicago.org/resource/ygr5-vcbg.json?$limit=1&$order=tow_date%20DESC'
curl -s -m 30 'https://data.cityofchicago.org/resource/bbyy-e7gq.json?$limit=1'
```

## 9. Notes / caveats for the provider worker

- Socrata `rowsUpdatedAt` / `data_updated_at` can reflect admin/metadata touches — always confirm with `max(time)`-style SoQL on the data itself (the congestion datasets "updated" May/Aug 2026 but contain nothing after 2026-04-30).
- Divvy `last_reported` staleness: 914/2,079 stations report ≥60 min stale (oldest 2025-06-13) while still flagged installed/renting. Poll the feed and use only stations with fresh `last_reported`, or use citywide aggregates (total bikes/docks available) which are dominated by the fresh core.
- CTA Bus Tracker is now on API **v3** (guide rev. 2025-04-21); v2 endpoints still answer. The 100k/day default limit is from the v3 guide; v2 said 10k/day.
- No code was written and no existing file was modified. This file is the only artifact.
