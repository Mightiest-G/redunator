# Redunator — Live Risk API Contract (Phase 3)

Data contract for the Phase 3 backend endpoint that serves **live**
(current-hour) red-light-camera risk. This file is the contract the
backend implements against; it is not the backend itself. It is
consistent with the SDD's `/predict_risk` response shape
(`risk_score` 0.0–1.0, `risk_level` LOW/MEDIUM/HIGH, `confidence`,
`reasons`) and with the existing `hourly_risk.predict_hourly`
conventions (hourly `risk_scores` = hourly prediction / 35;
hourly bands LOW < 0.1, MEDIUM 0.1–<0.5, HIGH ≥ 0.5/hour).

Status: **the live adjustment behind this endpoint is EXPERIMENTAL
and does NOT ship** — backtest verdict DO NOT SHIP (see
[REALTIME_REPORT.md](REALTIME_REPORT.md) §4: LIVE-A deviance
0.094370 vs baseline 0.094369; LIVE-B 0.093262 vs 0.093260). The
endpoint below therefore serves the **unadjusted** hourly prediction
for the current hour by default. The `live_adjustment` block in the
response is the defined shape for the adjustment *when and if* a
future backtest passes it; until then the backend MUST return
`adjustment_applied: false` and the unadjusted values. Do not wire
`live_adjust.adjust_current_hour` into the default path on the
strength of this contract alone.

## Endpoint

```
GET /live/risk?lat=<float>&lon=<float>[&at=<ISO-8601>&max_distance_m=<int>]
```

### Request parameters

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `lat` | float | yes | — | WGS84 latitude, −90…90. |
| `lon` | float | yes | — | WGS84 longitude, −180…180. |
| `at` | ISO-8601 timestamp | no | now | The moment the "current hour" is evaluated at. Must be timezone-aware (or end in `Z`); naive values are rejected, not assumed. Must not be more than 24 h in the future. Historical values are allowed (the response then reports the baseline for that hour; live signals are only meaningful near now). |
| `max_distance_m` | int | no | 2000 | Maximum distance to the nearest camera. A request whose nearest camera is farther than this is a 404 (see error cases). Must be 100…20000. |

### Nearest-camera resolution

The nearest camera is resolved by haversine distance over the 361
cameras with coordinates in
`data/hourly/processed/camera_segment_map.csv`
(`live_adjust.nearest_camera`). The 4 coordinate-less cameras
(1623, 2552, 2592, 2662) can never be nearest. Ties are broken by
lowest `camera_id` (string order). The `intersection` in the
response comes from `data/processed/camera_profiles.csv` when the
camera has a row there (300 test cameras), otherwise `null` — the
contract does not invent an intersection name.

### Response — success (200)

All timestamps are ISO-8601 with offset. All counts are predicted
violations for the hour (ESTIMATES — see limits). `risk_score` uses
the same scale as Phase 2: `clip(predicted / 35, 0, 1)`.

```json
{
  "camera_id": "3041",
  "intersection": "MICHIGAN AND JACKSON",
  "distance_m": 443.0,
  "latitude": 41.8782,
  "longitude": -87.6245,
  "at": "2026-11-16T05:28:06-06:00",
  "current_hour": 5,
  "date": "2026-11-16",
  "risk_score": 0.0011,
  "risk_level": "LOW",
  "predicted_violations_this_hour": 0.038,
  "daily_prediction": 10.3,
  "daily_band": "HIGH",
  "daily_source": "pre_test_mean",
  "confidence": 0.4,
  "reasons": [
    "Current hour 05:00 predicted 0.04 violations (LOW hourly band).",
    "Daily total 10.30 predicted (HIGH daily band); daily total is the anchor and is never changed by the live layer.",
    "Daily prediction is the camera's pre-test mean: the shipped model has no feature row for this date (data ends 2026-10-29).",
    "Live weather dry at the current hour (0.0 mm precipitation); no adjustment applied."
  ],
  "hourly_band_thresholds": {"low_max": 0.1, "high_min": 0.5},
  "daily_band_thresholds": {"low_max": 4.0, "high_min": 10.0},
  "live_adjustment": {
    "adjustment_applied": false,
    "adjustment_factor": 1.0,
    "live_weather_multiplier": 1.0,
    "expected_weather_multiplier": 1.0,
    "live_weather": {
      "precipitation_mm": 0.0,
      "snowfall_cm": null,
      "wind_speed_kmh": 6.9,
      "precip_source": "open-meteo-forecast"
    },
    "all_signals_stale": false,
    "signals_used": [
      {"source": "open-meteo-current", "fetched_at": "2026-11-16T11:28:05.789655+00:00", "as_of": "2026-11-16T11:15:00+00:00"},
      {"source": "open-meteo-forecast", "fetched_at": "2026-11-16T11:28:06.625688+00:00", "as_of": "2026-11-16T11:00:00+00:00"}
    ],
    "signals_dropped_stale": [
      {"source": "tomtom-flow", "fetched_at": "2026-11-16T11:28:07.596620+00:00", "as_of": "2026-11-16T11:28:07.596620+00:00", "reason": "tomtom-flow: API key required: set the TOMTOM_API_KEY environment variable."},
      {"source": "here-flow", "fetched_at": "2026-11-16T11:28:07.596666+00:00", "as_of": "2026-11-16T11:28:07.596666+00:00", "reason": "here-flow: API key required: set the HERE_API_KEY environment variable."},
      {"source": "cta-bus", "fetched_at": "2026-11-16T11:28:07.596687+00:00", "as_of": "2026-11-16T11:28:07.596687+00:00", "reason": "cta-bus: API key required: set the CTA_BUS_API_KEY environment variable."}
    ],
    "signals_ignored": [
      {"source": "divvy-gbfs", "fetched_at": "2026-11-16T11:28:06.790762+00:00", "as_of": "2026-11-16T11:26:39+00:00", "stale": false, "reason": "bike_occupancy is a weak contextual activity signal, not a congestion proxy; no validated relationship with hourly crash/violation risk could be shown, so it is never consumed by the adjustment."}
    ]
  },
  "estimate": true,
  "hourly": [0.155, 0.116, 0.156, 0.134, 0.073, 0.038, 0.137, 0.332, 0.551, 0.524, 0.421, 0.425, 0.499, 0.682, 0.585, 0.693, 1.139, 1.305, 0.708, 0.473, 0.367, 0.412, 0.222, 0.149]
}
```

Field-by-field:

| Field | Type | Meaning |
|---|---|---|
| `camera_id` | string | Nearest camera (see resolution above). |
| `intersection` | string \| null | Intersection name from `camera_profiles.csv`, else null. |
| `distance_m` | float | Haversine distance from (`lat`, `lon`) to the camera, metres. |
| `latitude` / `longitude` | float | The camera's coordinates (from the segment map). |
| `at` | string | The evaluated moment, echoing the request (or now). |
| `current_hour` | int 0–23 | Chicago hour of `at`. |
| `date` | string `YYYY-MM-DD` | Chicago date of `at`. |
| `risk_score` | float 0.0–1.0 | `clip(predicted_violations_this_hour / 35, 0, 1)` — same /35 scale as Phase 2. |
| `risk_level` | `LOW` \| `MEDIUM` \| `HIGH` | Hourly band of the current hour: LOW < 0.1, MEDIUM 0.1–<0.5, HIGH ≥ 0.5 predicted violations/hour. **Not** the daily band. |
| `predicted_violations_this_hour` | float ≥ 0 | Adjusted (or, until the adjustment ships, unadjusted) hourly prediction for the current hour. ESTIMATE. |
| `daily_prediction` | float ≥ 0 | Shipped daily-model prediction (or fallback) for the camera-date. Never changed by the live layer. |
| `daily_band` | `LOW` \| `MEDIUM` \| `HIGH` | Daily band: LOW < 4, MEDIUM 4–<10, HIGH ≥ 10/day. |
| `daily_source` | `model` \| `pre_test_mean` \| `citywide_mean` | Where the daily number came from (`hourly_risk.daily_prediction`). |
| `confidence` | float 0.0–1.0 | Contract rule (not a measured quantity): 0.6 when `daily_source` is `model`, 0.4 when it is a fallback mean; minus 0.1 when `all_signals_stale` is true; floored at 0.3. Hourly values are proxy-based estimates either way. |
| `reasons` | string[] | Human-readable reasons, in order: current-hour value + band; daily total + band and the anchor statement; daily-source note when it is a fallback; live-weather / adjustment note. |
| `hourly_band_thresholds` | `{low_max: 0.1, high_min: 0.5}` | The hourly band cutoffs in force. |
| `daily_band_thresholds` | `{low_max: 4.0, high_min: 10.0}` | The daily band cutoffs in force. |
| `live_adjustment` | object | The adjustment audit block (see below). |
| `estimate` | bool, always `true` | Every hourly number is an estimate. |
| `hourly` | float[24] | The full 24 hourly values for the camera-date (adjusted when the adjustment ships, else unadjusted). Sums to `daily_prediction` within float rounding. Included so a client can show the day shape without a second call. |

The `live_adjustment` block mirrors `live_adjust.adjust_current_hour`
exactly:

| Field | Type | Meaning |
|---|---|---|
| `adjustment_applied` | bool | Whether the current hour was changed. `false` until the adjustment ships, and `false` whenever no fresh weather signal exists or the live multiplier equals the expected one. |
| `adjustment_factor` | float | `live_weather_multiplier / expected_weather_multiplier`, clipped to [0.8, 1.25]. 1.0 when not applied. |
| `live_weather_multiplier` | float | `weather_multiplier` of the live conditions (1.0–1.25). |
| `expected_weather_multiplier` | float | Multiplier the baseline already embedded for the hour (1.0 for live dates with no archive weather row). |
| `live_weather` | object | `precipitation_mm` (float \| null), `snowfall_cm` (always null — neither live provider carries snowfall), `wind_speed_kmh` (float \| null), `precip_source` (`open-meteo-forecast` \| `open-meteo-current` \| null). |
| `all_signals_stale` | bool | True when no fresh weather signal was available at call time. The response is still 200 — it degrades to the unadjusted prediction. |
| `signals_used` | array | Sources whose values entered the response, each with `source`, `fetched_at`, `as_of` (ISO-8601 UTC). |
| `signals_dropped_stale` | array | Dropped sources with `source`, `fetched_at`, `as_of`, `reason` (stale, errored, or "API key required" for the key-ready providers). |
| `signals_ignored` | array | Fresh-but-unconsumed sources (Divvy, and any fresh key-ready provider with no validated use) with `source`, `fetched_at`, `as_of`, `stale`, `reason`. |

### Example request/response

Request (captured live run, 2026-11-16 ~05:28 CST):

```
GET /live/risk?lat=41.8781&lon=-87.6298
```

Response: 200, the JSON above — camera **3041** (MICHIGAN AND
JACKSON) at **443 m** from the Loop, from a real live run on
2026-11-16 ~05:28 CST: daily 10.30 (`pre_test_mean` — 3041 has no
feature row after 2026-10-29), current hour 05:00 predicted 0.038
(LOW), live weather dry (0.0 mm, wind 6.9 km/h), factor 1.0,
adjustment not applied, TomTom/HERE/CTA dropped as key-gated,
Divvy ignored. The `hourly` array in a real response carries that
camera's 24 values (3041 that morning: 0.155, 0.116, 0.156, 0.134,
0.073, 0.038, 0.137, 0.332, 0.551, 0.524, 0.421, 0.425, 0.499,
0.682, 0.585, 0.693, 1.139, 1.305, 0.708, 0.473, 0.367, 0.412,
0.222, 0.149 — peak 17:00, 1.305 HIGH).

### Error cases

| Case | HTTP | Body | Notes |
|---|---|---|---|
| Missing `lat` or `lon`; non-numeric; out of range | 400 | `{"error": "invalid_parameters", "message": "..."}` | Validate before any fetch. |
| `at` naive (no offset), unparseable, or > 24 h in the future | 400 | `{"error": "invalid_parameters", ...}` | Naive timestamps are rejected, never assumed to be Chicago time. |
| `max_distance_m` outside 100…20000 | 400 | `{"error": "invalid_parameters", ...}` | |
| No camera within `max_distance_m` | 404 | `{"error": "no_camera_nearby", "message": "...", "nearest_distance_m": <float>}` | `nearest_distance_m` is the distance to the closest camera even though it is beyond the limit, so the client can decide whether to retry with a larger radius. The 4 coordinate-less cameras are never candidates. |
| All live signals stale | 200 (degraded) | Normal response with `all_signals_stale: true`, `adjustment_applied: false`, `confidence` reduced by 0.1 | **Not** an error: the unadjusted prediction is still served. |
| Daily model file missing/unloadable AND no fallback mean available | 503 | `{"error": "model_unavailable", "message": "..."}` | In practice the `pre_test_mean` / `citywide_mean` fallbacks cover this; 503 is only for a total failure to produce a daily number (e.g. feature table unreadable). |
| Live provider fetch failure (network, bad payload) | 200 (degraded) | As all-stale | Provider failures never fail the request; they are recorded in `signals_dropped_stale` with the error as `reason`. |

### Non-goals / out of scope for Phase 3

* No authentication, rate limiting, or caching rules are set here —
  the backend owner decides those; the contract only requires that
  a cached response still carries the original `fetched_at` values
  (never re-stamp them) so staleness stays auditable.
* No `POST` / batch endpoint in this version. A client that needs
  several cameras calls the endpoint once per camera.
* The contract does not change the daily `/predict_risk` shape:
  daily predictions, daily bands, and the /35 risk score are
  untouched. `GET /live/risk` is an additional endpoint, not a
  replacement.

## Implementation notes for Phase 3

* Implement with `live_adjust.adjust_current_hour` +
  `live_adjust.nearest_camera` (both in this repo, stdlib +
  `requests` only). Do not re-implement the weather multiplier,
  the renormalisation, or the staleness rules — import them.
* `adjust_current_hour` fetches live signals itself when
  `live_signals=None`; the backend should call
  `collect_live_signals()` once per request and pass the result in,
  so the response's `fetched_at` values match a single fetch.
* The in-memory TTL cache in `live_signals.py` (15 min) is
  per-process; a multi-worker backend must accept that different
  workers can return different `fetched_at` values within a TTL
  window, or share the fetch. Either way, never serve a response
  whose `signals_used` entries are stale at serve time — re-check
  with `is_stale` before responding.
* Until the adjustment ships, the backend MUST NOT call the
  adjustment with `expected_weather_multiplier` overridden to
  force a factor, and MUST return `adjustment_applied: false`.
  The backtest that would change this is
  `validate_live_adjust.py`; its ship rule is deviance-only.
