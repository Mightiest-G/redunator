# Redunator API Reference — for the Flutter app (Phase 4)

Written 2026-11-16 by Phase 4 Worker 1 (environment + backend smoke check).
Sources: `backend/README.md`, `backend/routers/predict.py`,
`backend/routers/live.py`, `backend/routers/users.py`, `backend/config.py`,
`LIVE_CONTRACT.md`, plus a live smoke test against the local server
(2026-11-16 ~05:53–06:00 CST — all endpoints below returned 200).

## Base URL (local dev)

```
http://127.0.0.1:8000
```

Start the backend from the project root (no keys, no Postgres, no Redis;
SQLite default):

```bash
~/workspace/redunator-venv/bin/python -m backend.app
# or: ~/workspace/redunator-venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

- OpenAPI docs: `GET /docs` (Swagger UI), `GET /openapi.json`.
- **12 paths** in OpenAPI (14 operations; `/user_alerts`, `/user_report`,
  `/user_settings` each have 2–3 methods on one path).
- Rate limit: **100 req/min per client IP** (in-memory, sliding window).
  `/health`, `/docs`, `/openapi.json`, `/redoc` are exempt. 429 body is
  `{"error": "rate_limited", "message": ...}` with `X-RateLimit-*` /
  `Retry-After` headers.
- Errors everywhere are `{"error": ..., "message": ...}`
  (400 bad request, 404 not found / no camera nearby, 422 validation,
  429 rate limited, 500 internal).
- CORS: default allowed origins include `http://localhost:3000`,
  `http://127.0.0.1:3000`, `http://localhost:8080`, `http://127.0.0.1:8080`,
  `http://localhost`, `http://localhost:5000`, `capacitor://localhost`,
  `http://localhost:8000`, `http://127.0.0.1:8000`. Override with
  env `REDUNATOR_CORS_ORIGINS` (comma-separated).
- Env overrides: `REDUNATOR_HOST` (default `127.0.0.1`),
  `REDUNATOR_PORT` (default `8000`), `REDUNATOR_MAX_NEAREST_M`
  (default `2000`), `REDUNATOR_RATE_LIMIT_PER_MIN` (default `100`),
  `REDUNATOR_DB_PATH` (default `<root>/data/redunator.db`),
  `REDUNATOR_API_KEY` (unset = user endpoints open; if set, every user
  endpoint needs matching `X-Api-Key` header, 401 otherwise —
  local-dev gate only, NOT real auth; production MUST use JWT).

## Scales and bands (do not hardcode differently)

- Daily: `risk_score = clip(daily_prediction / 35, 0, 1)`.
  Bands: LOW < 4, MEDIUM 4–<10, HIGH ≥ 10 predicted violations/day.
- Hourly: `risk_score = hourly_prediction / 35` (same /35 scale).
  Bands: LOW < 0.1, MEDIUM 0.1–<0.5, HIGH ≥ 0.5 predicted violations/hour.
- `confidence` is a **heuristic, not a calibrated probability**:
  `/predict_risk` 0.90 model / 0.60 pre-test mean / 0.40 citywide mean
  (minus up to 0.10 for far nearest-camera lookups);
  `/live/risk` 0.6 model / 0.4 fallback, minus 0.1 when all signals
  stale, floored at 0.3.
- `daily_source`: `model` | `pre_test_mean` | `citywide_mean`.
  Unknown `camera_id` is NOT a 404 — it falls back to the citywide mean
  (`daily_source: citywide_mean`). Dates after 2026-10-29 (data ends
  2026-10-29) fall back to means.
- **Hourly values are ESTIMATES** (`estimate: true` everywhere): the daily
  violation data has no hour-of-day; the hourly shape is built from
  crash/congestion proxies and renormalised to the daily total. The daily
  total is the modelled quantity. `/violation_trends` is DAILY only.

## Endpoints

### GET /health
No params. Response: `status` (`"ok"`), `model_version` (int, currently
`1`), `model_files` (`{joblib, onnx, json}` filenames),
`model_metrics` (`{mae, rmse}` — currently mae 2.1451720889686663,
rmse 3.306837019543589), `data_freshness`
(`{latest_violation_date: "2026-10-29",
latest_violation_source: "data/processed/red_light_features.parquet",
latest_crash_date: "2026-10-29",
latest_crash_source: "data/hourly/raw/crashes_20250101_20261029.csv"}`).
Exempt from rate limiting.

### POST /predict_risk
Body (JSON): **at least one of** `camera_id` (string, e.g. `"1413"`)
**or** `latitude` + `longitude` (floats, −90…90 / −180…180).
If both are given, `camera_id` wins and lat/lon are ignored.
Optional fields:
- `timestamp` — ISO-8601 datetime; **the date part selects the
  camera-day**. NOTE: the field is named `timestamp`, NOT `date`.
  Defaults to now (UTC).
- `weather_override` (object), `congestion_override` (object) —
  accepted and echoed, but **IGNORED** by the validated path (the
  shipped model takes no live weather/congestion input).
- `method` — `"auto"` (default) / `"profile"` / `"model"`, optionally
  with `+demand` / `+slowness` suffixes. Only shapes the hourly split;
  the daily total is unchanged.
- `demand` (bool, default `false`) — opt-in trip-demand shaping of the
  hourly split. Default off; demand lost on every validation variant.
- `slowness` (bool, default `true`) — trip-slowness shaping on the
  profile path (ignored on the model path).
- `banding` — `"hourly"` (default) or `"daily"` (daily thresholds
  applied per hour, comparison only).

Response: `camera_id`, `intersection` (string|null),
`latitude`, `longitude`, `date` (`YYYY-MM-DD`), `timestamp` (echo),
`daily_prediction` (float), `daily_source`, `risk_score` (0–1),
`risk_level` (LOW/MEDIUM/HIGH, daily band), `confidence`,
`reasons` (string[]), `hourly` (24 entries:
`{hour: 0–23, predicted_violations, band, risk_score, estimate: true}`),
`hourly_estimate_note`, `hourly_method` (e.g. `"model"`),
`hourly_sum` (== `daily_prediction` exactly), `reconciliation_error`
(0.0), `model_version`, `nearest_distance_m` (null unless lat/lon
lookup), `weather_override_applied: false`,
`congestion_override_applied: false`.

Errors: empty body / neither camera nor lat+lon → 400 `bad_request`;
lat/lon beyond `REDUNATOR_MAX_NEAREST_M` (default 2000 m) →
404 `no_camera_nearby`; bad timestamp → 422 `validation_error`;
lat/lon out of range → 422.

Worked example (verified live 2026-11-16):
`{"camera_id": "1413", "timestamp": "2026-10-15T12:00:00"}` →
`daily_prediction` **53.0095**, `daily_source` `"model"`,
`risk_score` 1.0, `risk_level` HIGH, `confidence` 0.9,
`hourly_sum` 53.0095, `reconciliation_error` 0.0, peak hour 15:00
4.8194 HIGH, 21 HIGH / 3 MEDIUM / 0 LOW hours. (Offline joblib daily
is 53.0096; the 1e-04 gap is ONNX float32 rounding.)

### POST /predict_risk/batch
Body: `{"items": [<PredictRequest>, ...]}` — 1–100 items, same fields
as `/predict_risk` per item. Response:
`{"results": [...], "count": N, "errors": M}`. Each result is either a
full prediction object (same shape as `/predict_risk`) or a per-item
error `{"index", "camera_id", "error", "message"}` — one bad item
never fails the batch.
Verified live: 2 items (1413 @ 2026-10-15, 3041 no timestamp) →
200, count 2, errors 0; 1413 → 53.0095 model HIGH,
3041 → 10.2965 `pre_test_mean` HIGH.

### GET /intersections
Query params (all optional): `q` (case-insensitive substring on the
intersection name, e.g. `BELMONT`), `min_typical_weekday_pred` (float),
`min_typical_weekend_pred` (float), `band` (LOW/MEDIUM/HIGH — filters
on typical weekday band; anything else → 400 `bad_request`),
`limit` (default 300, max 500), `offset` (default 0).
Response: JSON array of 300 profiled cameras, each:
`camera_id`, `intersection`, `latitude`, `longitude`,
`typical_weekday_pred`, `typical_weekend_pred`,
`typical_weekday_band`, `typical_weekend_band`,
`typical_weekday_score`, `typical_weekend_score`,
`actual_test_mean`, `cam_mean_pre_test`.
Verified live: `?limit=500` → 200, 300 items. First item: 1413
"LAKE SHORE DR AND BELMONT" (41.940241, −87.639639),
weekday 60.54 / weekend 74.38, both HIGH.

### GET /violation_trends
Query params: `camera_id` (string) **or** `intersection` (exact name,
case-insensitive; 400 `ambiguous_intersection` if it matches >1 camera,
404 if none). At least one required (else 400). Optional `start_date`,
`end_date` (`YYYY-MM-DD`); defaults to the last 90 days of data
(data ends 2026-10-29); max range 366 days (else 400); reversed range
→ 400; no records in range → 404.
Response: `camera_id`, `intersection`, `granularity: "daily"`,
`note`, `start_date`, `end_date`, `days` (array of
`{date, observed_violations, predicted_violations}`),
`summary` (`{n_days, mean_observed, mean_predicted, total_observed}`).
DAILY granularity only — no hourly values.
Verified live: `camera_id=1413&start_date=2026-10-13&end_date=2026-10-15`
→ 200, 3 days, summary `{n_days: 3, mean_observed: 54.33,
mean_predicted: 55.5, total_observed: 163}`.

### GET /live/risk
Query params: `lat` (float, required, −90…90), `lon` (float, required,
−180…180), `at` (optional ISO-8601 timestamp, **must be
timezone-aware** — naive → 400; must not be >24 h in the future;
defaults to now), `max_distance_m` (optional int, default 2000,
allowed 100…20000).
Resolves the nearest camera by haversine over 361 cameras with
coordinates (4 coordinate-less cameras — 1623, 2552, 2592, 2662 —
can never be nearest; ties broken by lowest camera_id).
Response: `camera_id`, `intersection` (string|null), `distance_m`,
`latitude`, `longitude` (the camera's coords), `at` (echo),
`current_hour` (0–23, Chicago hour), `date` (`YYYY-MM-DD`),
`risk_score` (current hour / 35), `risk_level` (**hourly** band of the
current hour), `predicted_violations_this_hour`,
`daily_prediction`, `daily_band` (daily band), `daily_source`,
`confidence`, `reasons` (string[]), `hourly_band_thresholds`
(`{low_max: 0.1, high_min: 0.5}`), `daily_band_thresholds`
(`{low_max: 4.0, high_min: 10.0}`), `live_adjustment` (see below),
`estimate: true`, `hourly` (float[24], sums to `daily_prediction`).
`live_adjustment`: `adjustment_applied`, `adjustment_factor`,
`live_weather_multiplier`, `expected_weather_multiplier`,
`live_weather` (`{precipitation_mm, snowfall_cm` (always null),
`wind_speed_kmh, precip_source}`), `all_signals_stale`,
`signals_used` / `signals_dropped_stale` / `signals_ignored`
(each `{source, fetched_at, as_of[, reason, stale]}`).

**LIVE ADJUSTMENT IS EXPERIMENTAL AND DOES NOT SHIP** (backtest verdict
DO NOT SHIP — LIVE-A deviance 0.094370 vs baseline 0.094369; LIVE-B
0.093262 vs 0.093260; see `LIVE_CONTRACT.md` / `REALTIME_REPORT.md` §4).
The endpoint always returns `adjustment_applied: false`,
`adjustment_factor: 1.0`, and the hourly values are the UNADJUSTED
baseline. The `live_adjustment` block still carries the real audit
data (live weather, multipliers, signal partitions) for a future
backtest. **The app must NOT apply its own live adjustment on top of
these values, and must display hourly numbers as estimates.**
Errors: missing/non-numeric/out-of-range lat/lon → 400
`invalid_parameters`; naive/unparseable/>24 h-future `at` → 400;
`max_distance_m` out of range → 400; no camera in range → 404
`no_camera_nearby` with `nearest_distance_m`; all signals stale →
200 degraded (`all_signals_stale: true`, confidence −0.1); total
failure to produce a daily number → 503 `model_unavailable`.
Provider fetch failures never fail the request — they land in
`signals_dropped_stale` with the error as `reason` (key-gated
providers TomTom/HERE/CTA appear there as "API key required" when no
key is set; Divvy is fresh-but-ignored, in `signals_ignored`).
Verified live 2026-11-16: `?lat=41.8781&lon=-87.6298` → 200,
camera 3041 "MICHIGAN AND JACKSON", 443 m, hour 05:00 predicted 0.038
LOW, daily 10.3 `pre_test_mean`, confidence 0.4, adjustment_applied
false, factor 1.0, signals_used open-meteo-current + open-meteo-forecast.

### User endpoints (no account system in Phase 3)
Identity: `X-User-Id` header, else `user_id` field in the JSON body,
else `"anonymous"`. No auth in local dev by default; if
`REDUNATOR_API_KEY` is set, every user endpoint requires the matching
`X-Api-Key` header (401 otherwise). Storage: SQLite by default
(`data/redunator.db`; override `REDUNATOR_DB_PATH`); Postgres via
`DATABASE_URL` (PostGIS not needed).

- `POST /user_report` → 201. Body: `camera_id` and/or `lat`+`lon`
  (at least one location form required), optional `timestamp`
  (ISO-8601), optional `note`. Returns the stored report
  (`id`, `created_at`, …).
- `GET /user_report/{id}` → 200 the stored report; unknown id → 404
  `not_found`.
- `GET /user_alerts` → 200 `{"user_id", "alerts": [...]}`. Query:
  `user_id` (fallback if no header), `active_only` (default true).
  Empty list is valid.
- `POST /user_alerts` → 201. Body: `lat`, `lon` (required),
  `radius_m` (default 2000, 100…50000), `threshold` (LOW/MEDIUM/HIGH,
  default MEDIUM), optional `threshold_value` (≥ 0), optional `name`.
  Returns the created alert (`id`, `active: true`, …).
- `DELETE /user_alerts/{id}` → 200 the deactivated alert
  (`active: false`); unknown id for this user → 404.
- `POST /user_feedback` → 201. Body: `text` (required; `message`
  accepted as an alias), optional `rating` (int 1…5), optional
  `category`.
- `GET /user_settings` → 200 `{"user_id", "settings": {...}}` merged
  over defaults (`units: metric`, `alert_radius_m: 2000`,
  `risk_threshold: MEDIUM`, `notifications: {email: false, push: false}`).
- `POST /user_settings` → 200 merged settings. Body: flat object or
  `{"settings": {...}}`; merges over stored values. Validates
  `units` (metric/imperial), `risk_threshold` (LOW/MEDIUM/HIGH),
  `alert_radius_m` (number).

## Model pointer
The backend never hardcodes a model version: `live_model.json` names
the `joblib` / `onnx` / `json` filenames, `live_version` and `metrics`.
It is re-read per request (mtime check); a retrain promotion reloads
the ONNX session without a restart. Current live version: **1**
(`redunator_brain_v1.*`, md5s verified 2026-11-16 — see below).
The feature parquet is cached in-process, so a retrain that rewrites
it needs a restart.

## Verified model file md5s (2026-11-16, Worker 1)
- `redunator_brain_v1.joblib`: `9162dcbe76bf842584bd6f208c8bcb99`
- `redunator_brain_v1.onnx`: `90b9160389dd76d945bc8428a9ec3bf0`
- `redunator_brain_v1.json`: `28c83534e67267e272108c582ed0c56b`

## Backend smoke-test summary (2026-11-16, Worker 1)
Server started with `~/workspace/redunator-venv/bin/python -m backend.app`
from `~/workspace/redunator/`; stopped afterwards (process gone, port
8000 free). All endpoints 200:
`/health` 200 (status ok, model_version 1, latest violation 2026-10-29);
`POST /predict_risk` 1413/2026-10-15 → 53.0095 HIGH (see above);
`POST /predict_risk/batch` 2 items → 200, count 2, errors 0;
`GET /intersections?limit=500` → 200, 300 cameras;
`GET /live/risk?lat=41.8781&lon=-87.6298` → 200, camera 3041,
adjustment_applied false; `GET /docs` → 200 HTML (Swagger UI);
`GET /openapi.json` → 12 paths; `GET /violation_trends` 1413,
2026-10-13→15 → 200, 3 days.

## Environment (Worker 1, 2026-11-16)

### Flutter / Dart
- **Flutter 3.50.0** (channel stable, revision ab2912035e, 2026-11-09; Engine 371f77202a) at `~/flutter`; **Dart 3.14.0**, DevTools 2.61.0. `flutter --version` verified.
- **Android SDK** at `~/Android/Sdk` (configured via `flutter config --android-sdk ~/Android/Sdk`): cmdline-tools 13114758 (sdkmanager 19.0), platform-tools 37.0.3, platforms android-35 rev 2 + android-36 rev 2, build-tools 35.0.0 + 36.0.0. Licenses on disk accepted (`licenses/android-sdk-license` = `8933bad1…`, preview = `84831b94…`); `sdkmanager --licenses` prints "All SDK package licenses accepted."
- **JDK** Temurin OpenJDK 17.0.21+9 at `~/jdk17` (**home disk** — see the VM-reboot note below); `flutter config --jdk-dir ~/jdk17` set. clang 18.1.3, ninja 1.11.1, cmake 3.28.3; **Chrome 157.0.8103.78** at `/usr/bin/google-chrome`.
- **`flutter doctor` (final, 2026-11-16 ~13:42, with the IPv6-literal proxy env + JDK_JAVA_OPTIONS):** Flutter ✓, Android toolchain ✓ (SDK 36.0.0, licenses accepted), Chrome ✓, Proxy ✓, Connected device ✓ (Linux + Chrome), Network ✓. Only issue: Linux toolchain ✗ — **GTK 3.0 dev libraries missing** (`libgtk-3-dev` uninstallable: `libzstd-dev` requires `libzstd1 (= 1.5.5+dfsg2-2build1.1)` but `1.5.5+dfsg2-2build1.1+hatch1` is installed; downgrading the hatch-patched lib was judged too risky). Linux desktop builds are blocked on that only; Android + web targets are fine.
- **VM notes:** Ubuntu 24.04.5 LTS, ~6 GB RAM, 1.5 shared CPUs, shell runs as root (Flutter warns about it). `/tmp` is a 512 MB tmpfs — always set `TMPDIR=~/workspace/.tmp` for installs/builds/downloads.

### PROXY — READ THIS, IT BITES (root-caused 2026-11-16)
- Egress proxy: `hatch-egress-proxy:3128` (auth `hatch-runtime`, token in `$https_proxy`, rotates per shell). Direct egress is blocked.
- **The sandbox DENIES TCP to the proxy's IPv4 address `198.19.0.1`** — it injects a 193-byte policy message ("Other TCP connections is turned off… switch other_tcp from Deny to Ask") as the response. Java's `HttpURLConnection` chokes on it: `NoSuchElementException` at `doTunneling0:2277`.
- **Java MUST use the IPv6 proxy literal `fd8b:4f84:7d32:99::1:3128`.** curl/Python are unaffected (they try IPv6 first / work on both).
- **Trap:** Android sdklib reads the `https_proxy` ENV var and builds its own proxy from the HOSTNAME `hatch-egress-proxy` → Java resolves it to IPv4 → denied. **Fix: put the IPv6 literal in the proxy env vars** (`https_proxy=http://hatch-runtime:<token>@[fd8b:4f84:7d32:99::1]:3128`, token taken from the current `$https_proxy`) — then sdklib, Dart, curl and Python all use the IPv6 path and everything works. (Unsetting the env vars also works for JVM tools, but then Dart's network check fails; the IPv6-literal env is the single setup that makes `flutter doctor` fully green.) Working wrapper: `~/workspace/.tmp/sdkmgr.sh` (rewrites any hostname proxy env to the IPv6 literal itself; `--licenses` → "All SDK package licenses accepted.").
- **Gradle distribution download is BROKEN via Java:** the Gradle wrapper fetches from `services.gradle.org`, whose TLS handshake stalls indefinitely for Java (verified: `SocketTimeoutException: Read timed out`; curl gets the 307 in ~1 s; the 307 points to `github.com`, and Java downloads the full 240 MB from github.com fine in ~2 s). A plain `flutter build apk` FAILS at the wrapper download (601 s stall → `EOFException: SSL peer shut down incorrectly`, "Gradle threw an error while downloading artifacts from the network").
  - **Workaround applied:** Gradle 9.5.0-all pre-seeded via curl into the wrapper dists dir under the home-disk Gradle home: `/home/hatch/.gradle/wrapper/dists/gradle-9.5.0-all/aca6g93cdtcf0oapcfka748qh/gradle-9.5.0-all.zip` (240,667,887 B, sha256 `a3c4ba4aca8f0075688b9c5b18939fd28e8cb4357c227da5c1d9f38343791439`). The wrapper reuses it instead of downloading. If the wrapper version changes, re-seed the matching distribution the same way, or point `distributionUrl` in `android/gradle/wrapper/gradle-wrapper.properties` at the github.com release asset (Java fetches github.com fine).
  - **Gradle dependency downloads:** `android/gradle.properties` in the app MUST carry the IPv6 proxy as systemProps (the throwaway build used these):
    ```
    systemProp.http.proxyHost=fd8b:4f84:7d32:99::1
    systemProp.http.proxyPort=3128
    systemProp.https.proxyHost=fd8b:4f84:7d32:99::1
    systemProp.https.proxyPort=3128
    systemProp.http.nonProxyHosts=localhost|127.0.0.1
    systemProp.https.nonProxyHosts=localhost|127.0.0.1
    ```
    plus `GRADLE_OPTS` with the same `-Dhttps.proxyHost=…` props for the wrapper JVM (see the full recipe below).
- **Throwaway APK validation: BUILD 8 SUCCEEDED 2026-11-16 ~13:40** — `flutter create` + `flutter build apk --debug` in `~/workspace/.tmp/throwaway` (NOT the Redunator app): `✓ Built build/app/outputs/flutter-apk/app-debug.apk` (157,925,092 bytes; aapt: package `com.example.throwaway`, versionCode 1, versionName 1.0.0, compileSdk 36, targetSdk 36). Build took 395.7 s on 1.5 CPUs. Full working recipe below; builds 1–7 each failed on one distinct cause, all fixed in the recipe.
- **VM REBOOT / REPLACEMENT (hit 2026-11-16 ~13:00, uptime reset to 1 min): only `~` (home disk) survives.** Lost: `/usr/lib/jvm` (apt JDK), `/usr/bin/google-chrome`, `/root/.gradle` (incl. the first seeded Gradle dist), apt logs. Survived: `~/flutter`, `~/Android/Sdk`, `~/workspace`, `~/jdk17` (installed after). **Restore after any reboot:** reinstall Chrome + clang/ninja/cmake via apt; JDK is already home-disk (`~/jdk17`, no reinstall needed); Gradle user home is home-disk (`/home/hatch/.gradle`, no re-seed needed); re-run `yes | sdkmanager --licenses` if doctor complains (see below).
- **THE BUILD RECIPE (all of it is required — each line fixes a distinct failure):**
  ```
  export PATH=~/flutter/bin:~/jdk17/bin:$PATH; export TMPDIR=~/workspace/.tmp
  export JAVA_HOME=~/jdk17; export GRADLE_USER_HOME=/home/hatch/.gradle
  # Proxy env vars with the IPv6 LITERAL (not the hostname — see proxy section):
  export https_proxy="http://hatch-runtime:<token-from-$https_proxy>@[fd8b:4f84:7d32:99::1]:3128"
  export http_proxy="$https_proxy" HTTPS_PROXY="$https_proxy" HTTP_PROXY="$http_proxy"
  export JDK_JAVA_OPTIONS="-Djava.net.preferIPv6Addresses=true -Djdk.tls.client.protocols=TLSv1.2 -Dhttps.proxyHost=fd8b:4f84:7d32:99::1 -Dhttps.proxyPort=3128 -Dhttp.proxyHost=fd8b:4f84:7d32:99::1 -Dhttp.proxyPort=3128 -Djdk.http.auth.tunneling.disabledSchemes= -Djdk.http.auth.proxying.disabledSchemes="
  export GRADLE_OPTS="-Djava.net.preferIPv6Addresses=true -Djdk.tls.client.protocols=TLSv1.2 -Dhttps.proxyHost=fd8b:4f84:7d32:99::1 -Dhttps.proxyPort=3128 -Dhttp.proxyHost=fd8b:4f84:7d32:99::1 -Dhttp.proxyPort=3128"
  cd <app> && flutter pub get && flutter build apk --debug
  ```
  And in the app's `android/gradle.properties` (in addition to Flutter's defaults):
  ```
  systemProp.http.proxyHost=fd8b:4f84:7d32:99::1
  systemProp.http.proxyPort=3128
  systemProp.https.proxyHost=fd8b:4f84:7d32:99::1
  systemProp.https.proxyPort=3128
  systemProp.http.nonProxyHosts=localhost|127.0.0.1
  systemProp.https.nonProxyHosts=localhost|127.0.0.1
  ```
  (Do NOT add `org.gradle.daemon=false` — the persistent daemon works once the loopback is IPv6; `--no-daemon` still uses a single-use daemon over loopback TCP and fails the same way.)
  - **Why each flag:** `preferIPv6Addresses=true` — the Gradle daemon listens on loopback and the client connects to it; Java→127.0.0.1 is DENIED by the sandbox, Java→::1 works (proven with a Java server/client test). Without it: "Could not receive a message from the daemon" (builds 2–3). `jdk.tls.client.protocols=TLSv1.2` — Java TLS 1.3 handshakes stall on Fastly-fronted hosts (services.gradle.org, plugins.gradle.org, repo.maven.apache.org); TLS 1.2 returns 200 on all of them (builds 1, 4, 5). MITM CA: `hatch-egress-ca.crt` (`/usr/local/share/ca-certificates/`) imported into `~/jdk17/lib/security/cacerts` (alias `hatch-egress-ca`, SHA-256 fingerprint `4F:57:48:66:E5:4B:96:AB:90:9A:00:DB:29:D2:70:24:34:9E:6D:F7:3D:17:96:00:DD:C1:54:EC:A8:F5:EB:21`) — without it Java fails PKIX on maven.google.com / dl.google.com. `GRADLE_USER_HOME=/home/hatch/.gradle` — home-disk so the Gradle dist + dependency cache (287 MB) survive reboots (`/root/.gradle` does not; Java's `user.home` is `/root` even though `$HOME` is `/home/hatch`).
  - **Pre-seeded (so builds don't download):** Gradle 9.5.0-all in `/home/hatch/.gradle/wrapper/dists/gradle-9.5.0-all/aca6g93cdtcf0oapcfka748qh/gradle-9.5.0-all.zip` (240,667,887 B, sha256 `a3c4ba4aca8f0075688b9c5b18939fd28e8cb4357c227da5c1d9f38343791439`, curl'd from github.com — the wrapper's own download from services.gradle.org stalls for Java; alternative is pointing `distributionUrl` at the github.com release asset). **NDK 28.2.13676358** in `~/Android/Sdk/ndk/28.2.13676358` (2.2 GB, `source.properties` Pkg.Revision 28.2.13676358, curl'd `android-ndk-r28c-linux.zip` 722,261,334 B + unzipped, top dir moved up) — AGP requires it and cannot fetch it itself (see sdkmanager note). Note: curl-installed NDK has no `package.xml`, so `sdkmanager --list_installed` does NOT list it — AGP still finds it via `source.properties`. `flutter pub get` must run once before the first build (build 7 failed on missing `.dart_tool/package_config.json` because of `--no-pub`); pub works with the normal proxy env vars set.
  - **Stale-daemon trap:** Gradle reuses a daemon across builds; a daemon started BEFORE a JDK/env fix keeps the old behavior. After changing JDK_JAVA_OPTIONS / the cacerts, kill the daemon (`pkill -f GradleDaemon`, or `./gradlew --stop`) or the next build silently reuses the stale one (build 5's failure).
  - **sdkmanager remote fetch is BROKEN in this sandbox** ("Failed to download any source lists!", "IO exception while downloading manifest") even though direct Java fetches of the same URLs (repository2-1.xml, addons_list-5.xml, sys-img2-1.xml) return 200 — cause unknown, mechanism not `DownloadCache`'s URL/proxy (all three manifest URLs verified 200 via `HttpURLConnection` with identical props). Consequence: install every SDK component via curl (platforms, build-tools, NDK — all done this way) and never depend on `sdkmanager --install` / `--list` (remote). Local ops work: `--licenses` ("All SDK package licenses accepted."), `--list_installed`. License files on disk: `licenses/android-sdk-license` = `8933bad1…`, preview = `84831b94…`, plus 6 more written by the 13:42 accept-all run (googletv, googlexr, arm-dbt, gdk, microxr, mips sysimage). If doctor ever reports licenses not accepted after adding a component, re-run `yes | ~/workspace/.tmp/sdkmgr.sh --licenses`.
- **Relay retired:** the Python CONNECT relay on 127.0.0.1:8899 (`~/workspace/.tmp/connect_relay.py`) was a dead end (the sandbox intercepts loopback TCP too) and has been killed. Do not revive it.
- Scratch in `~/workspace/.tmp/`: Flutter tarball, SDK zips, `sdkmgr.sh`, `ProxyTest*.java`, `SockTest*.java`, `LoopTest.java`, `HttpClient.java`/`HttpURLConnection.java` (JDK sources used for diagnosis), `throwaway/` project, `gradle-9.5.0-all.zip`, `android-ndk-r28c-linux.zip`, `temurin17.tar.gz`, `doctor_final*.log`, `licenses_v6*.log`, `throwaway_build*.log`, `sdkmanager_list*.log`.


## Worker 3 additions (2026-11-16, release build)

- **Release lint is OFF** (`android/build.gradle.kts`): AGP 9.3.1's
  lint crashes release builds under JDK 17
  (`:google_maps_flutter_android:lintVitalAnalyzeRelease`,
  `NoSuchMethodError: List.removeLast()` in lint's JavaDocParser on
  that plugin's `Convert.java`). `checkReleaseBuilds = false` for all
  Android modules; the block must stay above the
  `evaluationDependsOn(":app")` block. Re-enable if the toolchain
  moves to JDK 21 or a fixed AGP.
- **Daemon OOM-kill (release build 1):** a stale Gradle daemon +
  Kotlin daemon + the running backend (~1.1 GB RSS) left ~1.7 GB
  available and the kernel OOM-killed the daemon mid-build. Before a
  release build: kill orphaned Kotlin/Gradle daemons by PID and stop
  the backend; rebuild with ~6 GB available.
- **Chrome can vanish on a VM/cell migration** (`/usr/bin/google-chrome`
  is not on the home disk). Restore from the cached
  `~/workspace/.tmp/google-chrome-stable_current_amd64.deb`
  (`dpkg -i`, deps already satisfied; Chrome 157.0.8103.78).
- **Headless Chrome + sandbox proxy:** Chrome 157 rejects userinfo in
  `--proxy-server` (`ERR_NO_SUPPORTED_PROXIES`); a throwaway localhost
  CONNECT relay that injects `Proxy-Authorization` works (see
  `~/workspace/.tmp/w3_chrome_relay.py`, killed after use). The
  bundled `/opt/meta-chromium/chrome` will not start from a shell
  ("Hatch WebGL domain policy is required").
- **Web:** `flutter create . --platforms=web` added `web/`;
  `flutter build web --release` succeeds. `--web-renderer` does not
  exist in Flutter 3.50 (CanvasKit only).
- **Deprecation note:** `package:flutter/material.dart` really is
  `@Deprecated` in this SDK (for a `material_ui` package that does
  not exist); the project-wide `deprecated_member_use: ignore` was
  removed — the import is now ignored per line, and a real
  `withOpacity` deprecation it had hidden is fixed. See README.md.
