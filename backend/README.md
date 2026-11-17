# Redunator backend — Phase 3 (Worker A: core app + prediction endpoints)

Worker A owns `app.py` (the `create_app` factory), `config.py`,
`model_service.py`, `rate_limit.py`, `routers/predict.py` and
`verify_onnx.py`. Worker B owns `routers/live.py`, `routers/users.py`
and `storage.py` (documented below). Do not edit the other worker's
files.

## Files (Worker A)

| File | What it is |
|---|---|
| `app.py` | `create_app()` factory; CORS, JSON-lines logging middleware, in-memory rate limiting (100 req/min/client, `/health` + `/docs` + `/openapi.json` exempt), `{"error","message"}` error handlers, tolerant include of Worker B's routers (missing module → warning logged, app still runs), `GET /health`. `python -m backend.app` runs uvicorn. |
| `config.py` | Env-driven settings: `REDUNATOR_LIVE_MODEL` (pointer path), `REDUNATOR_MAX_NEAREST_M` (default 2000), `REDUNATOR_RATE_LIMIT_PER_MIN` (default 100), `REDUNATOR_CORS_ORIGINS` (comma-separated), `REDUNATOR_HOST`/`REDUNATOR_PORT`. Band thresholds + score scale. |
| `model_service.py` | `ModelService`: ONNX Runtime daily predictions. Resolves model filenames through `live_model.json` (never hardcoded); re-reads the pointer on every call (mtime check) and reloads the session when it moves, so a retrain promotion takes effect without a restart. Builds feature vectors from the parquet exactly as `hourly_risk.daily_prediction` (reuses `train.FEATURES`; lag/roll NaN → 0.0; lat/lon NaN → train medians; cam_code from sorted train cameras). Falls back to pre-test mean / citywide mean (5.34) with `source` reported, exactly as `daily_prediction` does. |
| `rate_limit.py` | Sliding-window in-memory limiter (60 s window, per client IP). Returns 429 `{"error":"rate_limited",...}` + `X-RateLimit-*` / `Retry-After` headers. |
| `routers/predict.py` | `POST /predict_risk`, `POST /predict_risk/batch`, `GET /intersections`, `GET /violation_trends` (see below). |
| `verify_onnx.py` | ONNX-vs-joblib parity check (see below). |

## Running

From the project root, with the project venv:

```bash
~/workspace/redunator-venv/bin/python -m backend.app
# or
~/workspace/redunator-venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Local run needs no Postgres/Redis and no keys. Install deps with
`pip install -r requirements.txt` (fastapi 0.121.0, uvicorn 0.37.0,
httpx 0.28.1 pinned there; onnxruntime 1.32.0 was already installed).

## Endpoints (Worker A)

* `POST /predict_risk` — body: `camera_id` and/or `latitude`+`longitude`,
  optional `timestamp` (ISO-8601; date part selects the camera-day),
  optional `weather_override` / `congestion_override` (accepted and
  echoed, but **ignored** by the validated path — the shipped model
  takes no live weather/congestion input; no live adjustment is
  applied), optional `method` (`auto`/`profile`/`model`, +`demand`/
  +`slowness` suffixes), `demand` (default false), `slowness`
  (default true), `banding` (`hourly`/`daily`). Response: `daily_prediction`
  (ONNX, via the live pointer), `daily_source` (`model` |
  `pre_test_mean` | `citywide_mean`), `risk_score` = clip(daily/35, 0, 1),
  `risk_level` (daily bands LOW <4, MEDIUM 4–<10, HIGH ≥10),
  `confidence` (heuristic, NOT calibrated: 0.90 model / 0.60 pre-test
  mean / 0.40 citywide mean, −up to 0.10 for far nearest-camera lookups),
  `reasons` (human-readable factors from the feature row), 24 `hourly`
  entries (`predicted_violations`, `band` on hourly bands LOW <0.1,
  MEDIUM 0.1–<0.5, HIGH ≥0.5/hr, `risk_score` = hourly/35, `estimate:
  true`), `hourly_method`, `hourly_sum`, `reconciliation_error`,
  `model_version`. The hourly split comes from `hourly_risk.predict_hourly`
  and is rescaled to the ONNX daily total so `hourly_sum == daily_prediction`
  exactly (ONNX/joblib agree to ~1e-05, so the rescale is negligible).
  Lat/lon-only requests resolve the nearest camera by haversine over
  `data/hourly/processed/camera_segment_map.csv` (361 cameras); 404
  `no_camera_nearby` beyond `REDUNATOR_MAX_NEAREST_M` (default 2000 m).
  Unknown `camera_id` is NOT a 404: it falls back to the citywide mean
  (same contract as `hourly_risk.daily_prediction`) with
  `daily_source: citywide_mean`.
* `POST /predict_risk/batch` — list of up to 100 `PredictRequest`s.
  Per-item errors return `{"index","camera_id","error","message"}` in
  place of that item's prediction; one bad item never fails the batch.
* `GET /intersections` — the 300 profiled cameras from
  `data/processed/camera_profiles.csv` with typical weekday/weekend
  predictions, bands and scores. Filters: `q` (substring on the
  intersection name), `min_typical_weekday_pred`,
  `min_typical_weekend_pred`, `band` (typical weekday band),
  `limit` (default 300, max 500), `offset`.
* `GET /violation_trends?camera_id=…[&start_date&end_date]` (or
  `intersection=` for an exact-name lookup; 400 if it matches >1
  camera). DAILY granularity only: observed violations per day from
  the processed feature table + the served ONNX model's daily
  prediction for the same days, with a summary. Defaults to the last
  90 days of data (data ends 2026-10-29); max range 366 days. No
  hourly values are returned or implied.
* `GET /health` — `status`, `model_version` + model filenames +
  metrics from the live pointer, and data freshness: latest violation
  date (source `data/processed/red_light_features_serving.parquet`) and latest
  crash date if the crash file is present (source stated in the
  response). Exempt from rate limiting.

Errors are always `{"error": ..., "message": ...}` (400 bad request,
404 not found / no camera nearby, 422 validation, 429 rate limited,
500 internal). Every response carrying hourly values labels them
`estimate: true` with the note that hourly numbers are ESTIMATES
from proxies, never observed hourly violations.

## ONNX parity (`verify_onnx.py`)

```bash
~/workspace/redunator-venv/bin/python backend/verify_onnx.py [--n 2000]
```

Measured 2026-11-16 on the original protocol (first 2,000 holdout
rows, parquet order, from 2026-08-01): **max abs diff 3.411e-06,
mean abs diff 2.773e-07, max rel diff 3.623e-07 — PASS** against a
2e-05 threshold. Honest note: REPORT.md's 2.4e-06 figure was measured
in the original export environment and is NOT reproduced exactly here
(other 2,000-row slices measure up to ~1.7e-05); the residual is
float32 rounding noise (ONNX takes float32, sklearn predicts in
float64; predictions reach ~97/day, float32 epsilon there is ~7.6e-06),
not a model difference. The script documents this and the 2e-05
threshold bounds every slice measured so far.

## Reconciliation (verified live 2026-11-16, real HTTP)

cam 1413, 2026-10-15: offline `hourly_risk.predict_hourly` (joblib) →
daily **53.0096**, peak **15:00 / 4.8194**, 21 HIGH hours, method
`model`. Live API (ONNX) → daily **53.0095**, hourly_sum **53.0095**,
reconciliation_error **0.0**, peak **15:00 / 4.8194**, 21 HIGH hours.
The 1e-04 daily gap is the ONNX float32 rounding above; the hourly
values are rescaled to the ONNX daily so the API is internally exact.
`/docs` loads (OpenAPI parses, 12 paths incl. Worker B's routers).
Rate limiting demonstrated with `REDUNATOR_RATE_LIMIT_PER_MIN=5`:
5×200 then 429 `{"error":"rate_limited",...}`; `/health` still 200.
CORS preflight from `http://localhost:3000` returns
`access-control-allow-origin: http://localhost:3000`.

## Honest limits

* Hourly values are proxy-based ESTIMATES; the daily total is the
  modelled quantity. Daily predictions for dates after 2026-10-29
  (or camera-days with no feature row) fall back to means — the
  response says so via `daily_source`.
* `confidence` is a heuristic, not a calibrated probability.
* Rate limiting is in-memory per process: it does not survive a
  restart and does not share state across uvicorn workers.
* The model pointer is re-read per request (mtime check); a retrain
  promotion is picked up without a restart, but the feature parquet
  is cached in-process (a retrain that rewrites the parquet needs a
  restart to be picked up).
* `weather_override` / `congestion_override` are accepted but ignored
  by design; there is no live adjustment on the default path.

---

# Redunator backend — Phase 3 (Worker B files)

Worker A owns `app.py` (the `create_app` factory, which includes the
routers in `routers/`) and `routers/predict.py`. Worker B owns the
files documented here. Do not edit the other worker's files.

## Files (Worker B)

| File | What it is |
|---|---|
| `routers/live.py` | `GET /live/risk` — exactly per `../LIVE_CONTRACT.md` |
| `routers/users.py` | `POST /user_report`, `GET /user_report/{id}`, `GET/POST /user_alerts`, `DELETE /user_alerts/{id}`, `POST /user_feedback`, `GET/POST /user_settings` |
| `storage.py` | Storage abstraction used by all user endpoints |
| `routers/__init__.py`, `__init__.py` | Package markers (docstrings only) |

No `backend/live_service.py` was needed: the live logic lives in the
top-level `live_adjust.py` / `live_signals.py` modules and is
imported, not re-implemented.

## GET /live/risk

`GET /live/risk?lat=&lon=[&at=<ISO-8601>&max_distance_m=<int>]`

* Nearest camera by haversine via `live_adjust.nearest_camera`
  (361 cameras with coordinates; the 4 coordinate-less cameras can
  never be nearest). Default `max_distance_m` 2000, allowed
  100…20000.
* One `collect_live_signals()` fetch per request, passed into
  `adjust_current_hour` so all `fetched_at` values match a single
  fetch; staleness is re-checked with `is_stale` before responding
  (a signal that went stale between fetch and serve is moved to
  `signals_dropped_stale` and its values are not served).
* **The live adjustment does NOT ship.** The response always has
  `live_adjustment.adjustment_applied: false`,
  `adjustment_factor: 1.0`, and the hourly values are the
  UNADJUSTED baseline from `predict_hourly` (`hourly_unadjusted`
  from `adjust_current_hour`). The `live_adjustment` block still
  carries the real audit data — live weather, multipliers,
  `signals_used` / `signals_dropped_stale` / `signals_ignored` —
  so a future backtest pass can flip the flag without changing the
  shape. The endpoint never calls the adjustment with
  `expected_weather_multiplier` overridden.
* Confidence rule (contract, not a measured quantity): 0.6 when
  `daily_source` is `model`, 0.4 for a fallback mean, minus 0.1
  when `all_signals_stale`, floored at 0.3.
* Error cases (see `LIVE_CONTRACT.md` error table): bad/missing/
  out-of-range lat/lon → 400 `invalid_parameters`; naive,
  unparseable, or >24 h-future `at` → 400; `max_distance_m`
  outside 100…20000 → 400; no camera within range → 404
  `no_camera_nearby` with `nearest_distance_m`; all signals stale
  → 200 degraded; total failure to produce a daily number → 503
  `model_unavailable`.
* Every hourly number is an ESTIMATE from proxies, never an
  observed hourly violation count (`estimate: true`).

## User endpoints

Identity: the `X-User-Id` header, else a `user_id` field in the
JSON body, else `"anonymous"`. There is no account system in this
phase.

Auth: **no auth in local dev by default.** If the
`REDUNATOR_API_KEY` env var is set, every user endpoint requires
the matching `X-Api-Key` header (401 otherwise). That is a
local-dev gate only — **production MUST use JWT auth before any
public deployment** (per the SDD). No full auth system is built
here, and no accounts, keys, or paid services were created.

* `POST /user_report` — "Report Spike". Body: `camera_id` and/or
  `lat`+`lon` (at least one location form required), optional
  `timestamp` (ISO-8601), optional `note`. Returns 201 with the
  stored report (`id`, `created_at`). `GET /user_report/{id}`
  retrieves one.
* `GET /user_alerts` — lists the caller's active proximity-alert
  configs; an empty list is a valid response. `?active_only=false`
  includes deactivated ones.
* `POST /user_alerts` — create one. Body: `lat`, `lon` (required),
  `radius_m` (default 2000, 100…50000), `threshold` (LOW/MEDIUM/
  HIGH, default MEDIUM), optional `threshold_value`, optional
  `name`. Returns 201. `DELETE /user_alerts/{id}` deactivates it.
* `POST /user_feedback` — body: `text` (required; `message`
  accepted as an alias), optional `rating` (int 1…5), optional
  `category`. Returns 201 with the stored record.
* `GET /user_settings` / `POST /user_settings` — per-user settings
  (`units` metric/imperial, `alert_radius_m`, `risk_threshold`,
  `notification` prefs). POST accepts either a flat object or
  `{"settings": {...}}` and merges over stored values; GET returns
  stored values merged over defaults
  (`units=metric`, `alert_radius_m=2000`, `risk_threshold=MEDIUM`,
  `notifications={email:false, push:false}`).

## Storage (`storage.py`)

* Default: SQLite via stdlib `sqlite3`, file `data/redunator.db`
  (override with `REDUNATOR_DB_PATH`; tables created on first use).
  One short-lived connection per operation — safe under FastAPI's
  threaded sync endpoints and multi-worker uvicorn without a pool.
* PostgreSQL: set `DATABASE_URL` to a `postgresql://` /
  `postgres://` URL. Requires `psycopg` (v3) or `psycopg2` — neither
  is installed by default, and the code raises a clear error naming
  the missing package instead of failing obscurely. The local run
  works with zero config when the var is unset. PostGIS is not
  needed yet (locations are plain lat/lon columns); a PostGIS
  migration is a schema change for a later phase, not a code change.
* No Redis dependency in this phase. The only cache is the
  in-memory TTL cache inside `live_signals.py` (15 min,
  per-process). **Redis swap-in point:** replace the `_CACHE` dict
  in `live_signals.py` with a Redis client keeping the same
  `(fetched_epoch, LiveSignal)` values serialised as JSON; nothing
  in `storage.py` or the routers needs to change. (Note the
  contract's warning: a cached response must keep its original
  `fetched_at` values, never re-stamp them, so staleness stays
  auditable.)

## Running

Worker A owns `app.py`; run the app as that module documents
(`create_app` factory). These routers are included by it — they
also mount standalone under TestClient for verification (see the
verification notes in the Worker B handoff).

---

# Redunator backend — Phase 3 (Worker C: tests, docs, deploy — 2026-11-16)

Worker C proves the backend works end to end, documents it, and
writes the deploy guide. One real bug was found by the tests and
fixed (see below); no other backend behaviour was changed. Two stale
comments were corrected to the documented ONNX parity threshold
(`backend/routers/predict.py`, `backend/model_service.py`: the
`2.4e-06` figure is replaced by the `2e-05` threshold — see the
ONNX parity note in Worker A's section above).

## Bug found by the tests (fixed 2026-11-16)

`POST /user_alerts` returned `"active": 1` (SQLite integer) while
`GET /user_alerts` and `DELETE /user_alerts/{id}` returned
`"active": true` (boolean). One-line fix in
`backend/routers/users.py`: the create handler now coerces
`rec["active"] = bool(rec["active"])`, exactly as the delete handler
already did. Covered by `test_alerts_create_list_delete`.

## Test suite (`backend/tests/`)

```bash
cd ~/workspace/redunator
~/workspace/redunator-venv/bin/python -m pytest backend/tests/ -q
# 81 passed (2026-11-16, ~26 s)
```

`pytest` is pinned in `requirements.txt` (9.2.0). Tests use FastAPI's
`TestClient` against the real app and the real served models. What
they cover:

| File | Coverage |
|---|---|
| `test_health_docs.py` | `/health` (model v1, files, freshness 2026-10-29), `/openapi.json` lists all 12 routes, `/docs` loads, CORS preflight + response header, unknown route 404 JSON. |
| `test_predict.py` | `/predict_risk` by camera / lat-lon / camera-wins-over-lat-lon / unknown camera → citywide mean / future date → pre-test mean / overrides ignored / `banding=daily`; errors: empty body 400, far lat/lon 404, bad timestamp 422, lat/lon out of range 422; batch incl. one-bad-item, empty, >100; `/intersections` incl. filters + pagination + bad band 400; `/violation_trends` incl. default 90 days, explicit range, by-intersection, missing params 400, unknown camera 404, ambiguous intersection 400, reversed/too-long range 400. |
| `test_live.py` | `/live/risk` LIVE_CONTRACT worked example (3041, deterministic fields) + default `at` + degraded all-stale 200; all error cases: missing/non-numeric/out-of-range lat/lon, naive/unparseable/>24h-future `at`, `max_distance_m` out of range/non-numeric, far coords 404 with `nearest_distance_m`. |
| `test_users.py` | `/user_report` create + retrieve + all 400s + unknown id 404 + anonymous; `/user_alerts` create/list/delete + defaults + 400s + unknown delete 404; `/user_feedback` incl. `message` alias + rating 400s; `/user_settings` defaults → set → merge (flat + `{"settings": …}`) + 400s; API-key gate (401 without/wrong key, 201 with). |
| `test_pointer_ratelimit.py` + `_subprocess_checks.py` | Pointer + rate limit in fresh subprocesses (env vars are read at import, so in-process tests cannot exercise them). Pointer COPY with `live_version` 99 → `/health` reports 99, predictions still serve, real pointer untouched. `REDUNATOR_RATE_LIMIT_PER_MIN=5` → 5×200 then 429 `rate_limited` with `X-RateLimit-*`/`Retry-After` headers, `/health` still 200. |
| `test_storage.py` | SQLite round-trip (reports/alerts/settings/feedback); Postgres wiring up to the network (driver import + URL parsing → connection error, NOT the "driver missing" error); the clear `StorageError` when neither `psycopg` nor `psycopg2` can be imported. |

Key facts about the suite:

* **Reconciliation test** (`test_reconciliation_cam_1413_2026_10_15`):
  cam 1413 / 2026-10-15 → daily **53.0095**, hourly sum **53.0095**,
  reconciliation error **0.0**, peak **15:00 / 4.8194**, 21 HIGH / 3
  MEDIUM / 0 LOW hours — the offline joblib daily is 53.0096, so the
  1e-04 gap is ONNX float32 rounding, as documented.
* **Live worked-example test**: the live-signal fetch is patched with
  fake fresh dry signals (0.0 mm precip, wind 6.9 km/h) reproducing
  the LIVE_CONTRACT example exactly, so the deterministic fields
  hold regardless of network or time of day. A real-network run
  against uvicorn returned the same fields live (wind 6.3 km/h —
  live weather moves; the deterministic fields matched).
* **Temp DB**: `conftest.py` points `REDUNATOR_DB_PATH` at a
  throwaway file under `/tmp/redunator-tests/` before the app is
  imported, so tests never touch the real `data/redunator.db`.
  Each test uses a fresh `X-User-Id`.
* **Rate limiter**: the in-process limiter is raised for the suite
  (the suite makes more requests than the 100/min default); the real
  limit is exercised by the subprocess test above.
* **Postgres**: no live Postgres exists here. The driver
  (`psycopg[binary]` 3.3.9, pinned in `requirements.txt`) is
  installed, the URL parsing + connection attempt is exercised to a
  fast connection-refused, and the missing-driver error is tested.
  A live Postgres round-trip is **explicitly untested**.

## Endpoint cookbook (copy-paste against `http://127.0.0.1:8000`)

Start the server first (see "Running" in Worker A's section), then:

```bash
# Health + model version + data freshness
curl http://127.0.0.1:8000/health

# Daily + hourly risk for one camera (reconciliation example)
curl -X POST http://127.0.0.1:8000/predict_risk \
  -H 'Content-Type: application/json' \
  -d '{"camera_id":"1413","timestamp":"2026-10-15T12:00:00"}'
# -> daily_prediction 53.0095, HIGH, hourly_sum 53.0095, reconciliation_error 0.0

# Same, by location (nearest camera; 404 beyond REDUNATOR_MAX_NEAREST_M)
curl -X POST http://127.0.0.1:8000/predict_risk \
  -H 'Content-Type: application/json' \
  -d '{"latitude":41.940241,"longitude":-87.639639,"timestamp":"2026-10-15T12:00:00"}'

# Batch (max 100; per-item errors don't fail the batch)
curl -X POST http://127.0.0.1:8000/predict_risk/batch \
  -H 'Content-Type: application/json' \
  -d '{"items":[{"camera_id":"1413","timestamp":"2026-10-15T12:00:00"},{"camera_id":"3041"}]}'

# Intersections (300 profiled cameras; filters: q, min_typical_weekday_pred,
# min_typical_weekend_pred, band, limit, offset)
curl 'http://127.0.0.1:8000/intersections?q=BELMONT&limit=5'
curl 'http://127.0.0.1:8000/intersections?band=HIGH&limit=5'

# Daily violation trends (daily granularity only; default last 90 days)
curl 'http://127.0.0.1:8000/violation_trends?camera_id=1413'
curl 'http://127.0.0.1:8000/violation_trends?camera_id=1413&start_date=2026-10-13&end_date=2026-10-15'
curl 'http://127.0.0.1:8000/violation_trends?intersection=NORTHWEST%20HIGHWAY%20AND%20FOSTER'

# Live current-hour risk (LIVE_CONTRACT.md worked example)
curl 'http://127.0.0.1:8000/live/risk?lat=41.8781&lon=-87.6298&at=2026-11-16T05:28:06-06:00'
# -> camera 3041, MICHIGAN AND JACKSON, 443 m, hour 05:00 0.038 LOW,
#    daily 10.3 pre_test_mean, adjustment_applied false

# User endpoints (X-User-Id header, else user_id in body, else "anonymous")
curl -X POST http://127.0.0.1:8000/user_report \
  -H 'Content-Type: application/json' -H 'X-User-Id: gabriel' \
  -d '{"camera_id":"1413","note":"spike at lunch"}'
curl http://127.0.0.1:8000/user_report/1
curl -X POST http://127.0.0.1:8000/user_alerts \
  -H 'Content-Type: application/json' -H 'X-User-Id: gabriel' \
  -d '{"lat":41.8781,"lon":-87.6298,"radius_m":1500,"threshold":"HIGH","name":"Loop"}'
curl http://127.0.0.1:8000/user_alerts -H 'X-User-Id: gabriel'
curl -X DELETE http://127.0.0.1:8000/user_alerts/1 -H 'X-User-Id: gabriel'
curl -X POST http://127.0.0.1:8000/user_feedback \
  -H 'Content-Type: application/json' -H 'X-User-Id: gabriel' \
  -d '{"text":"Prediction felt right today","rating":5}'
curl http://127.0.0.1:8000/user_settings -H 'X-User-Id: gabriel'
curl -X POST http://127.0.0.1:8000/user_settings \
  -H 'Content-Type: application/json' -H 'X-User-Id: gabriel' \
  -d '{"units":"imperial","risk_threshold":"HIGH"}'

# OpenAPI
curl http://127.0.0.1:8000/openapi.json | python3 -m json.tool | head -n 20
# or open http://127.0.0.1:8000/docs in a browser
```

## Environment variables

| Variable | Default | What it does |
|---|---|---|
| `REDUNATOR_LIVE_MODEL` | `<root>/live_model.json` | Model pointer path. Filenames + version are resolved through it; re-read per request (mtime check). For tests, point it at a COPY. |
| `REDUNATOR_MAX_NEAREST_M` | `2000` | Max distance (m) for a lat/lon-only request to resolve to a camera; beyond it → 404 `no_camera_nearby`. |
| `REDUNATOR_RATE_LIMIT_PER_MIN` | `100` | Requests/minute per client IP, in-memory, sliding window. `/health`, `/docs`, `/openapi.json`, `/redoc` exempt. |
| `REDUNATOR_CORS_ORIGINS` | localhost dev list (see `config.py`) | Comma-separated allowed origins. Set to the real app origin(s) in production. |
| `REDUNATOR_HOST` / `REDUNATOR_PORT` | `127.0.0.1` / `8000` | Bind host/port for `python -m backend.app`. Render sets `PORT`; the Dockerfile binds `0.0.0.0:$PORT`. |
| `REDUNATOR_DB_PATH` | `<root>/data/redunator.db` | SQLite file for the user store. Tests point it at `/tmp`. |
| `DATABASE_URL` | (unset → SQLite) | `postgresql://…` / `postgres://…` switches the user store to Postgres (needs `psycopg`, installed). PostGIS not needed. |
| `REDUNATOR_API_KEY` | (unset → open) | If set, every user endpoint requires the matching `X-Api-Key` header (401 otherwise). Local-dev gate only — JWT auth is required before any public deployment. |
| `TOMTOM_API_KEY` / `HERE_API_KEY` / `CTA_BUS_API_KEY` | (unset) | Key-gated live providers for `/live/risk`. Unset = gracefully dropped, listed in `signals_dropped_stale`. Open-Meteo + Divvy need no key. |

## Model pointer — how it works

The backend never hardcodes a model version. `live_model.json`
(names `joblib` / `onnx` / `json` filenames, `live_version`, `md5`,
`metrics`) is re-read on every prediction call; if its mtime
changed, the ONNX session reloads, so a monthly-retrain promotion
takes effect **without a restart**. The feature parquet is cached
in-process, so a retrain that rewrites the parquet still needs a
restart. v1 files are never overwritten; a promotion writes
versioned `redunator_brain_vN.*` files and moves the pointer.
**Never edit the real pointer by hand** — the retrain script owns
it; tests use a copy.

## Auth

No auth in local dev by default. `REDUNATOR_API_KEY` gates the user
endpoints behind a shared-secret header when set. That is a
local-dev gate, **not** user auth: identity is still just the
`X-User-Id` header, which anyone can set. **Production MUST use JWT
auth before any public deployment** (per the SDD). No accounts,
keys, or paid services were created in Phase 3.

## Storage

SQLite by default (`data/redunator.db`, one short-lived connection
per operation — safe under threaded sync endpoints and multi-worker
uvicorn). Postgres via `DATABASE_URL` (driver `psycopg[binary]`
3.3.9 in `requirements.txt`); PostGIS not needed yet. No Redis in
this phase; the only cache is the in-memory 15-min TTL cache in
`live_signals.py` — the Redis swap-in point is replacing the
`_CACHE` dict there with a Redis client keeping the same
`(fetched_epoch, LiveSignal)` values as JSON (and never re-stamping
`fetched_at`). The OpenWeatherMap swap-in point, if Gabriel ever
wants it, is a new provider class in `live_signals.py` in the same
`LiveSignal` shape; Open-Meteo is the default and needs no key.

## Deployment

See [`DEPLOY.md`](DEPLOY.md) and `render.yaml` (Render Blueprint,
Docker runtime). Headline facts: the Blueprint uses plan
`standard` ($25/mo) because measured warm RSS is **~1.2 GB** and the
free instance (512 MB) will OOM; the repo is 3.3 GB but the Docker
image ships only the ~200 MB the backend reads (repo-root
`.dockerignore`); SQLite user data does not survive redeploys on
Render (no persistent disk) — Postgres is the durable option, and
free Postgres expires after 30 days. **Nothing has been deployed.**
The deploy checklist (Render account, GitHub repo, optional API
keys, Apple Developer later) is in `DEPLOY.md` §1 — Gabriel creates
all of them himself.

## Honest limits (consolidated)

* Hourly values are proxy-based **ESTIMATES** (`estimate: true`
  everywhere); the daily total is the modelled quantity. Dates after
  2026-10-29 fall back to means, labelled by `daily_source`.
* The **live adjustment does NOT ship**: `/live/risk` always returns
  `adjustment_applied: false` with unadjusted hourly values (backtest
  verdict DO NOT SHIP — LIVE-A 0.094370 vs baseline 0.094369,
  LIVE-B 0.093262 vs 0.093260).
* `confidence` (0.90 / 0.60 / 0.40 by `daily_source`, 0.6 / 0.4 on
  `/live/risk`, −0.1 when all signals stale) is a **heuristic, not a
  calibrated probability**.
* **Memory**: warm RSS ~1.2 GB for one worker — Render free (512 MB)
  will OOM. Slimming path (drop the joblib load, slim the parquet)
  is documented in `DEPLOY.md` §5, not done.
* **Postgres**: driver installed, wiring exercised to a connection
  error; a live Postgres round-trip is **untested** (no server here).
* Rate limiting is **in-memory per process**: lost on restart, not
  shared across workers.
* The feature parquet is cached in-process: a retrain rewriting it
  needs a restart; a pointer move does not. On Render, a retrain
  promotion also needs a redeploy (the image is a snapshot).
* `weather_override` / `congestion_override` are accepted but
  **ignored** by design on the validated path.
* A live Postgres is the only durable user store; on the default
  SQLite deploy, user reports/alerts/feedback/settings are lost on
  redeploy/spin-down.

## Files added / changed by Worker C

| File | What |
|---|---|
| `backend/tests/` (`conftest.py`, `test_health_docs.py`, `test_predict.py`, `test_live.py`, `test_users.py`, `test_pointer_ratelimit.py`, `_subprocess_checks.py`, `test_storage.py`) | 81-test suite (see above). |
| `backend/Dockerfile`, `.dockerignore` (repo root), `backend/render.yaml`, `backend/DEPLOY.md` | Render deploy (Docker runtime, standard plan, checklist). |
| `backend/routers/users.py` | One-line fix: `POST /user_alerts` now returns `"active": true` (was `1`). |
| `backend/routers/predict.py`, `backend/model_service.py` | Comment/docstring only: `2.4e-06` → documented `2e-05` ONNX parity threshold. |
| `requirements.txt` | Added `pytest==9.2.0`, `psycopg[binary]==3.3.9`. |
| `backend/README.md` (this file) | Worker C sections appended; A/B sections untouched. |
| `../README.md`, `../REPORT.md` | Phase 3 sections added. |
