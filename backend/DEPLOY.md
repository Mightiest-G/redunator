# Redunator backend — deployment guide (Phase 3, Worker C, 2026-11-16)

Deploy target: **Render**, Docker runtime, from `backend/render.yaml`
(a Render Blueprint). **Nothing has been deployed.** Every step below
is something Gabriel (or the coordinator) does by hand; no accounts,
keys, or services were created for this guide.

## 0. Read this first — the free tier does NOT fit this backend

Measured on this VM (one uvicorn worker, warmed with
`/predict_risk` + `/live/risk` + `/violation_trends`):

| What | 2026-11-16 (before slimming) | 2026-11-17 (after slimming) |
|---|---|---|
| Warm RSS, whole backend process | **~1.2 GB** | **~890 MB** |
| `redunator_brain_v1.joblib` loaded by `hourly_risk` | ~295 MB | 0 — the hourly anchor now comes from the shared ONNX session (lever 1) |
| Feature parquet in pandas | full table, loaded twice (once per module) | serving-only table, one shared copy: 92 MB DataFrame (lever 2) |
| ONNX session (`redunator_brain_v1.onnx`) in `model_service` | ~330 MB | ~320 MB — unchanged; the session itself is the floor |
| App import baseline (no models / data) | — | ~132 MB |

Slimming (levers 1+2, 2026-11-17) saved ~200–320 MB, but warm RSS
is still ~890 MB — well above the free tier's 512 MB. The binding
constraint is the ONNX session itself (~320 MB) on top of a ~132 MB
import baseline: ~452 MB before ANY data is loaded, leaving ~60 MB
for the feature table, the hourly-model caches and the XGBoost
runtime. That is why the free tier still does not fit (see §5 for
what would be needed). The paid Starter plan is also 512 MB — also
not enough. The Blueprint therefore still uses **`standard` (1 CPU /
2 GB, $25/mo at time of writing)**. Render's own docs say of free
instances: "Do not use them for production applications."

Free-tier limits that matter here (Render docs, re-checked Nov 2026):

| Limit | Value |
|---|---|
| Free web instance | 512 MB RAM, 0.1 CPU, 1 instance, no persistent disk |
| Spin-down | After 15 min without traffic; next request takes ~1 min to wake |
| Free instance hours | 750 / workspace / month (one always-on service ≈ 744 h — two don't fit) |
| Bandwidth / build minutes | 5 GB / month, 500 build minutes / month (workspace) |
| Free Postgres | 1 GB, **expires after 30 days** (data deleted) |
| Background workers / cron | No free tier — the monthly-retrain cron cannot run on Render free |

Other facts that shape this deploy:

- The working tree is **3.3 GB** (2.9 GB reference model
  `redunator_brain_v1_rf_unbounded.joblib` + 524 MB `data/`). The
  Docker build honours the repo-root `.dockerignore` and ships only
  the ~200 MB the backend reads; a native-Python Render service
  would clone the whole repo. Do not switch `render.yaml` to
  `env: python` without slimming the repo first.
- The shipped model files in the image are 56 MB joblib + 30 MB ONNX
  (+ 1.4 MB hourly model) — small; the bulk of the image is the
  Python ML stack (pandas / scikit-learn / XGBoost / ONNX Runtime).
- **SQLite user data does not survive on Render.** The default store
  writes `data/redunator.db`, and Render web services have no
  persistent disk — the file is lost on every redeploy and spin-down.
  User reports / alerts / feedback / settings are therefore
  best-effort on the default deploy. For durable user data, add the
  Postgres block in `render.yaml` (free Postgres expires in 30 days;
  a paid Postgres survives).
- Rate limiting is in-memory per process: one worker = one limiter.
  Do not raise uvicorn `--workers` (memory + split limiter state).

## 1. What Gabriel must provide (checklist)

He creates all of these himself; nothing below was created for him.

- [ ] **A Render account** (render.com) — free Hobby workspace is enough to start.
- [ ] **A GitHub repo containing this project** — the project is NOT
      currently a git repo. Create one (e.g. `redunator`), commit the
      tree, push it, and connect it to Render. (Do not push
      `redunator-venv/`, `.tmp/`, or `data/redunator.db` — add a
      `.gitignore` for those. The 2.9 GB unbounded model should also
      stay out of the repo if the repo is re-created slim; it is
      already excluded from the Docker image by `.dockerignore`, but
      GitHub will reject a single 2.9 GB file push — use Git LFS or
      leave it out.)
- [ ] **Nothing else is required to deploy.** Open-Meteo (weather)
      and Divvy need no key. The live layer degrades gracefully
      without the key-gated providers.

Optional — only if he wants them:

- [x] **TomTom API key** (`TOMTOM_API_KEY`) — DONE 2026-11-16: key "Redunator" (Evaluation plan, 2,500/day free) is held in the custom.tomtom connector and verified live; Redunator's TomTom provider falls back to the connector when the env var is unset (see live_signals.py). For production on Render, still set `TOMTOM_API_KEY` as an env var there — the connector only works on this VM.
- [x] **HERE API key** (`HERE_API_KEY`) — DONE 2026-11-16: app "Redunator", free Base plan, key verified live via the here-flow provider (272 flow segments). Set in local `redunator/.env` (gitignored from Docker via `.dockerignore`); still needs setting as an env var on Render for production.
- [x] **CTA Bus Tracker API key** (`CTA_BUS_API_KEY`) — DONE 2026-11-17: free BusTime key issued for gabriel3dragon@gmail.com (account activated via the email link; the activation emails landed in Gmail Spam). Key is wired into local `.env` and VERIFIED LIVE (40 vehicles on 10 sampled routes at ~02:00 CST). Set it as a Render env var for production. Provider notes: getvehicles REQUIRES an `rt` (or `vid`) parameter, and returns NO speed field — the provider samples 10 high-frequency routes and reports the live vehicle count (an activity signal, not a speed); it also tolerates the API's mixed vehicle+error responses at quiet hours.
- [ ] **Apple Developer account** ($99/yr) — only later, for the iOS app (Phase 4). Not needed for the backend.
- [ ] **OpenWeatherMap key** — NOT needed: Open-Meteo is the default
      weather provider and needs no key. If he ever wants to swap
      providers, the swap-in point is a new provider class in
      `live_signals.py` (same `LiveSignal` shape, `OPENWEATHER_API_KEY`
      env convention); no code for it exists today.

## 2. Deploy steps

1. Push the repo to GitHub (see checklist above).
2. Render dashboard → **New → Blueprint** → select the repo.
   Render reads `backend/render.yaml` and proposes one web service,
   `redunator-api` (Docker, plan `standard`).
3. Review the proposed service, then **Apply**. First build takes
   several minutes (ML wheels + model files).
4. When the service is live, note its URL
   (`https://redunator-api.onrender.com` or similar).
5. Verify (replace the host):
   ```bash
   curl https://<host>/health
   # expect: {"status":"ok","model_version":1,...,"data_freshness":{"latest_violation_date":"2026-10-29",...}}

   curl -X POST https://<host>/predict_risk \
     -H 'Content-Type: application/json' \
     -d '{"camera_id":"1413","timestamp":"2026-10-15T12:00:00"}'
   # expect: daily_prediction 53.0095, risk_level HIGH, reconciliation_error 0.0

   curl "https://<host>/live/risk?lat=41.8781&lon=-87.6298"
   # expect: camera 3041, adjustment_applied false
   ```
   Open `https://<host>/docs` in a browser — the OpenAPI page should
   list all 15 paths (including `/auth/device` and
   `/subscriptions/verify`).
6. If the deploy will serve real users, set `REDUNATOR_JWT_SECRET`
   in the host env (a long random value, 32+ bytes — generate one,
   never commit it) and have the app mint a device token at
   `POST /auth/device`. The old `REDUNATOR_API_KEY` / `X-Api-Key`
   gate was removed 2026-11-18 and is no longer honoured; device
   JWTs are the auth now (see §4).

## 3. Environment variables (Render)

| Variable | Set in Blueprint? | Notes |
|---|---|---|
| `REDUNATOR_CORS_ORIGINS` | Yes | Comma-separated allowed origins. Default is now empty (deny) — the API serves a native app only; set only if a web origin ever needs access. |
| `REDUNATOR_RATE_LIMIT_PER_MIN` | Yes (`100`) | Per-client, in-memory, per process. `/health`, `/docs`, `/openapi.json` exempt. |
| `REDUNATOR_JWT_SECRET` | No (**required for user endpoints**) | HS256 secret for device JWTs. Unset → `POST /auth/device` answers 503 and all user endpoints answer 401. Long random value, never committed. |
| `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` | No (optional) | Filesystem path to a Google Play service-account JSON file. Unset → `POST /subscriptions/verify` answers `{"valid": null, "reason": "play_verification_not_configured"}`. |
| `REDUNATOR_MAX_NEAREST_M` | No (default `2000`) | Max distance for lat/lon → nearest camera. |
| `REDUNATOR_DB_PATH` | No | Leave unset on Render (ephemeral SQLite). Set only for local testing. |
| `DATABASE_URL` | No (optional) | `postgresql://…` to switch the user store to Postgres. Needs the Postgres block in `render.yaml`. |
| `REDUNATOR_LIVE_MODEL` | No (default `live_model.json`) | Pointer path. A retrain promotion moves the pointer file; the backend picks it up without a restart. |
| `TOMTOM_API_KEY` / `HERE_API_KEY` / `CTA_BUS_API_KEY` | No (optional) | Key-gated live providers; unset = gracefully dropped, listed in `signals_dropped_stale`. |
| `PORT` | Set by Render | The Dockerfile binds `$PORT` on `0.0.0.0` via `REDUNATOR_HOST` / `REDUNATOR_PORT`. |

## 4. Before any public launch (updated 2026-11-18)

- **JWT auth — DONE in code (2026-11-18).** Device-scoped JWTs
  (``POST /auth/device`` → Bearer token, 30-day expiry, HS256)
  protect all four user endpoints; the spoofable ``X-User-Id``
  header and the ``X-Api-Key`` dev gate are removed. Deploy still
  needs ``REDUNATOR_JWT_SECRET`` set in the host env — without it
  the backend fails closed (503 on ``/auth/device``, 401 on every
  user endpoint). See ``backend/auth.py`` and
  ``backend/API_REFERENCE.md``.
- **Persistent user storage — STILL NEEDS A LIVE PROVISION.**
  The Postgres code path is verified as far as possible without a
  server (DDL variants, ``%s`` placeholders, ``RETURNING id``,
  ``ON CONFLICT`` upsert — see ``backend/tests/test_postgres_path.py``),
  and ``psycopg[binary]`` is installed, but no live Postgres
  round-trip has ever run: there is no server on this VM. Provision
  one (Render Postgres, or the Cloud Run deploy's equivalent) and
  set ``DATABASE_URL``; until then the service runs on ephemeral
  SQLite and user reports/alerts/feedback/settings vanish on
  redeploy.
- **Monitoring — DONE (2026-11-18).** GCP uptime check
  "redunator-api-health" (ID redunator-api-health-L03j_nb8PhQ):
  HTTPS GET /health every 5 min, 60 s timeout (covers the ~30 s
  cold start — the Console's pre-creation test answered 200 in
  15.5 s), response must contain "ok". Verified PASSING/UP, 100%
  uptime since creation, all six checker locations green. Alert
  policy "redunator-api-down" (enabled): fires when more than one
  region fails (20-min rolling window), emails
  gabriel3dragon@gmail.com on open and on closure; no alerts open.
  No other project changes were made.
- **CORS — DONE (2026-11-18).** Default allow-list is now empty
  (deny); the API serves a native app only. ``REDUNATOR_CORS_ORIGINS``
  still overrides it for explicitly configured origins.
- **Subscriptions — endpoint DONE, Google wiring NEEDS CREDENTIALS.**
  ``POST /subscriptions/verify`` is built and tested with a mocked
  Google client, but no service-account JSON exists yet: create one
  in Google Play Console (service account with the
  androidpublisher permission), put the JSON file on the host, and
  set ``GOOGLE_PLAY_SERVICE_ACCOUNT_JSON`` to its path. Until then
  the endpoint answers ``{"valid": null, "reason":
  "play_verification_not_configured"}``. Verification is stateless
  for now — the app caches Pro locally and re-validates on launch;
  no entitlement is stored server-side.
- **Monthly retrain**: the `redunator-monthly-retrain` cron runs on
  this VM (day 1, ~03:45 America/Chicago). A retrain promotion writes
  versioned `redunator_brain_vN.*` files and moves `live_model.json`
  in the *repo* — the Cloud Run image is a snapshot, so the live
  service does not pick that up until a redeploy happens. The
  automation (built 2026-11-18, first live test = the 2026-12-01
  retrain): on PROMOTE the cron runs `scripts/push_promotion.sh`,
  which commits the promoted files + pointer and pushes to main
  via the retrain deploy key (`~/.ssh/redunator-retrain` — public
  key fingerprint SHA256:GkqeJCHCweD5Jd7cQ9JTWZuiLedYsJdC73EE52U6CXM,
  still to be added to the repo's Deploy keys with Write access);
  a Cloud Build trigger on pushes touching model/backend files
  then builds + deploys (`cloudbuild.yaml` now has the deploy
  step — `gcloud run deploy --image` preserves existing env vars).
  The backend re-reads `live_model.json` per request
  (`backend/model_service.py`, mtime check) and reloads the ONNX
  session when the pointer moves, so a redeployed image serves the
  new model with no restart. STILL OPEN: add the deploy key to the
  repo (browser) + create the Cloud Build trigger with its file
  filter (`redunator_brain_v*`, `live_model.json`, `backend/**`,
  `cloudbuild.yaml`, serving parquet) — and grant the Cloud Build
  service account `roles/run.admin` + `roles/iam.serviceAccountUser`
  so the trigger can deploy.

## 5. Getting back to the free tier (levers 1+2 DONE 2026-11-17 — still not enough)

The free tier needs warm RSS under 512 MB. Levers 1+2 below were
implemented 2026-11-17 and re-measured the same way as §0 (one
uvicorn worker, warmed with `/predict_risk` + `/live/risk` +
`/violation_trends`): warm RSS went from ~1.2 GB to **~890 MB**
(after-startup 719 MB, after /predict_risk 877 MB, after
/violation_trends 895 MB; stable across repeat calls, no more
per-request growth). Predictions are unchanged (cam 1413 /
2026-10-15 daily 53.0095 ONNX, hourly sums exactly, reconciliation
0.0; ONNX-vs-joblib max diff 3.4e-06, threshold 2e-05; all 81
backend tests + both smoke tests pass). **The plan was NOT switched
to free: 890 MB is ~380 MB over the limit.**

1. **DONE — Drop the joblib forest from the serving path.**
   `hourly_risk.daily_prediction` now delegates to the backend's
   ONNX service (`backend.model_service.service`) instead of loading
   `redunator_brain_v1.joblib`; the joblib fallback survives only
   for standalone use if the backend package cannot be imported
   (in the backend process sklearn is never imported for the daily
   model). Verified: no `sklearn` import and no joblib load after a
   `daily_prediction` call.
2. **DONE — Slim the feature parquet.** `build_serving_parquet.py`
   (checked in, reproducible) builds
   `data/processed/red_light_features_serving.parquet`: 19 columns
   (lookup keys + the 16 `train.FEATURES` minus derived `cam_code` +
   `violations` for /violation_trends), lat/lon float64 -> float32,
   `days_since_first` int32 -> int16. Verified against the full
   table (row count, cameras, date range, sampled rows incl. cam
   1413 / 2026-10-15; sha256
   e998b969abf3aadce730dc14fe26819ec715d5c628d3ae8218fdd1732b718498).
   The serving table is 92 MB in pandas vs 166 MB for the full
   table. `backend/config.py` FEATURES_PATH points at it; the full
   table stays in the repo for training / retraining and is never
   loaded at serving time. The serving parquet ships in the Docker
   image (tracked in git, not excluded by .dockerignore).
3. **What remains — the ONNX session is the floor.** Measured
   component costs (this VM): app import baseline ~132 MB; ONNX
   session ~320 MB (150 trees, 782k nodes — arena on/off makes no
   difference; the cost is the graph itself, not the allocator);
   serving parquet ~190–270 MB RSS per load (92 MB DataFrame +
   pyarrow + read temporaries); hourly-model path ~157 MB (XGBoost
   runtime + weather/crash/congestion caches). Base + session alone
   is ~452 MB, leaving ~60 MB for all data — so no column-pruning
   of the parquet can close the gap. Closing it would need a
   serving rewrite, e.g.: (a) replace the in-memory parquet with
   per-request SQLite lookups (~180–250 MB saved, pyarrow import
   included); (b) serve the daily model from the joblib forest
   instead of ONNX (sklearn import + forest ≈ 257 MB vs ONNX
   ≈ 339 MB, ~80 MB saved — but it would rework the live-pointer
   contract and retrain promotion, which are ONNX-based); (c) serve
   the hourly Poisson model from an ONNX export instead of the
   XGBoost runtime (removes the xgboost/sklearn import from the
   warm path). Even (a)+(b)+(c) together land near ~500 MB with no
   headroom — the free tier is not a comfortable fit for this model
   stack. Do NOT switch `render.yaml` to `plan: free` until a
   re-measurement lands comfortably under ~450 MB with headroom
   for a traffic burst — and accept the 15-minute spin-down on top.

## 6. Rollback

Render keeps every deploy: Dashboard → service → Deploys → pick the
previous good deploy → **Rollback**. The model files are baked into
the image, so a rollback also rolls the model back to whatever that
deploy shipped — including the `live_model.json` pointer as of that
commit. If a retrain promotion needs undoing, move the pointer back
to `redunator_brain_v1.*` and redeploy (v1 files are never
overwritten).

## 7. Cloud Run deploy — LIVE 2026-11-17 ~09:22 CST (supersedes Render)

Render was dropped: even after the 2026-11-17 slimming (warm RSS
1.2 GB → ~890 MB), the backend cannot fit Render's 512 MB free tier
(ONNX session alone ~320 MB), and Render standard is $25/mo. Cloud
Run's free tier fits the usage instead (scale to zero; ~50 h/month
of 2 GiB runtime free, then ~$0.10/active-hour; the $10 budget alert
on "My Billing Account 2" guards it).

- **Service URL:** https://redunator-api-108691448805.us-central1.run.app
- **Project:** `redunator` (pimjpmaster@gmail.com), region `us-central1`
- **Image:** `us-central1-docker.pkg.dev/redunator/redunator-api/redunator-api:cb1d910`
  (redeployed 2026-11-18 ~00:33, revision `redunator-api-00003-kgm`,
  Cloud Build 1e74f70f, 3m 25s; previous image `:259c8cd`,
  revision `redunator-api-00002-cwb`). Built by Cloud Build from
  the repo root, `-f backend/Dockerfile`; build config is
  `cloudbuild.yaml` at the repo root. **Manual rebuild lesson:**
  a plain `gcloud builds submit --config cloudbuild.yaml .`
  FAILS with "invalid image name" because `$SHORT_SHA` is empty
  on manual submits — always add
  `--region=us-central1 --substitutions=SHORT_SHA=<commit-sha>`.
- **Settings:** memory 2Gi, cpu 1, min-instances 0, max-instances 2,
  `--allow-unauthenticated`, `--port 8000`. The Dockerfile already
  binds `$PORT` (`REDUNATOR_PORT=${PORT:-8000}`), so no code change
  was needed for Cloud Run.
- **Env vars on the service:** `HERE_API_KEY`, `CTA_BUS_API_KEY`,
  `TOMTOM_API_KEY` (all three set; TomTom was added by a second
  deploy after Gabriel supplied the key — it is NOT stored in the
  repo or in memory). **`GITHUB_TOKEN` (set 2026-11-18 ~00:49,
  revision `redunator-api-00004-x5g`):** a read-only fine-grained
  PAT (Contents: read on Mightiest-G/redunator, no expiration,
  named "Redunator app updates") that powers `GET /app/latest`,
  the endpoint the Flutter app's in-app update check calls.
  The token value is never stored in the repo or in memory. It was
  set with `gcloud run services update redunator-api
  --region us-central1 --update-env-vars GITHUB_TOKEN=<token>`
  (preserves the other env vars; no redeploy needed). Verified
  live 2026-11-18 ~00:50: `/app/latest` returns tag "apk-4",
  build 4, download_url for apk-4, reason null.
- **Verified live 2026-11-17 ~09:25 CST (independent curls):**
  `/health` ok, model_version 1, MAE 2.145 / RMSE 3.307, latest
  violation data 2026-10-29; `/predict_risk` cam 1413 2026-10-15 =
  53.0095 HIGH, reconciliation 0.0; `/live/risk` cam 3041 HIGH,
  daily 10.3; `/docs` lists all routes.
- **Cold start is real:** the first request after idle can take
  ~30 s while the model loads, and one first-attempt `/predict_risk`
  call timed out at 120 s during warm-up; the retry answered in
  ~4 s once warm. The Flutter app should tolerate a slow first call.
- **Portability:** the service is a plain Docker image built from
  the GitHub repo, so it can move to another host (Oracle, Render)
  later by rebuilding the same image there — no Cloud Run-specific
  code. A move needs the new host's URL put into the Flutter app
  and the three API keys re-entered there. User data in SQLite is
  ephemeral on Cloud Run (same as Render free) and would not carry
  over a move unless it is on Postgres first.
- **Redeploy after a retrain promotion:** rebuild + redeploy the
  image (same commands as §7 above, new tag), or push the new model
  files and let a fresh build pick them up. The image is a snapshot;
  the live service does not pick up repo changes by itself.
---

## 8. Flutter APK — GitHub Actions build (standing pipeline, live 2026-11-17)

- **Why Actions:** the local VM cannot build the APK anymore — the
  Gradle daemon starts but the client can never dispatch to it
  ("Could not dispatch a message to the daemon", connection reset),
  even after fixing the JDK CA store and the proxy CA rotation; a
  Java NIO loopback test returned proxy-banner bytes, so the sandbox
  intercepts Java TCP. Do not keep retrying Gradle locally.
- **Workflow:** `.github/workflows/build-apk.yml` — on every push to
  main touching `app/**` (or the workflow itself): checkout,
  setup-java 17 (Temurin), subosito/flutter-action@v2 (Flutter
  3.50.0), cache `~/.gradle/wrapper` + `~/.gradle/caches`,
  `flutter pub get`, `flutter build apk --release` (retries once),
  then uploads the APK as artifact `app-release-apk` AND publishes
  a GitHub Release tagged `apk-<run-number>` with the APK attached.
- **Build-number stamping (added 2026-11-18):** the build passes
  `--build-number ${{ github.run_number }}`, so the APK's build
  number equals its release number. The app's in-app update check
  compares its own build number with `GET /app/latest` on the
  backend (which reads the latest release via the `GITHUB_TOKEN`
  env var) and prompts when a newer release exists. Dark mode
  shipped in the same release (apk-4): theme setting
  system/light/dark in Settings, dark map style included.
- **Two fixes baked in (learned from run #1, 2026-11-17):**
  (1) the JVM tried IPv6 first for the Gradle distribution download
  and failed with "Network is unreachable" — the workflow now sets
  `JAVA_TOOL_OPTIONS=-Djava.net.preferIPv4Stack=true`, which every
  JVM the build spawns picks up; (2) the repo's
  `app/android/gradle.properties` carried this VM's sandbox egress
  proxy (`fd8b:4f84:7d32:99::1:3128`) — on a runner that address
  does not exist, so the daemon's downloads would fail. Proxy lines
  now live only in the local `GRADLE_USER_HOME/gradle.properties`
  (never committed), with a note in the repo file.
- **First successful build:** run #2, 2026-11-17 10:42 CST, 19m 13s
  (build step 17m 14s — first run downloads the NDK, so it is slow;
  later runs reuse the cache). Release `apk-2`:
  https://github.com/Mightiest-G/redunator/releases/tag/apk-2 —
  `app-release.apk`, 54,888,506 bytes, sha256
  ced34ea3241dbcdc46ca11327b2a42e06405e8a6a7dcdd99c0a62fdaef4699bc.
  Verified in a Codespace by `strings`: the live Cloud Run URL
  appears 3 times; `10.0.2.2` appears only inside a local-dev help
  string, never as the configured default. The app's
  `defaultBaseUrl` is the Cloud Run URL (commit 61ff475); a fresh
  install uses it directly.
- **Pushing to GitHub:** the Muse GitHub App has NO Contents:write
  on this repo (`push_files` returns 403), so pushes go via a
  Codespace + git bundle + temporary-release transfer (VS Code
  Explorer Upload silently truncates the bundle — always verify
  the byte size and use the temp-release workaround). The workflow
  triggers automatically on the push.

## 9. Google Play readiness (status 2026-11-18 — not started)

Gabriel asked 2026-11-18 how to get Redunator on Google Play and
how subscriptions would work. Status of each Play requirement:

- **Target API — DONE already.** Flutter 3.50.0 sets
  `targetSdkVersion = 36` (Android 16). Google Play has required
  API 36 for new apps and updates since 2026-08-31 (the Nov 1
  extension window has passed), so the app already clears the
  floor with no code change.
- **Release signing — NOT DONE.** The app is debug-signed
  (`signingConfig = signingConfigs.getByName("debug")` in
  `app/android/app/build.gradle.kts`); no `key.properties`, no
  upload keystore. Play needs a release keystore + Play App
  Signing (Google holds the signing key, we keep the upload key).
  The keystore + passwords must live outside the repo (and out of
  chat/memory); the Actions workflow will need the keystore +
  secrets to sign the bundle.
- **App Bundle — NOT DONE.** The workflow builds an APK
  (`flutter build apk --release`); Play requires an AAB
  (`flutter build appbundle --release`). One-line workflow change
  once signing exists.
- **Play Console account — NOT DONE (Gabriel's step).** $25
  one-time fee + identity verification, on his Google account.
  A new *personal* account must then run a **closed test with at
  least 12 testers opted in continuously for 14 days** before
  applying for production access (Google reviews the application,
  usually ~7 days). So the earliest realistic store launch is
  ~3 weeks after the closed test starts. An organization account
  skips the 12-tester rule but needs a D-U-N-S number (up to 30
  days to obtain).
- **Store listing — NOT DONE.** Icon, phone screenshots,
  description, category, content-rating questionnaire, privacy
  policy URL (the app uses location, so a hosted privacy policy
  is required), and the Data Safety form (location data must be
  declared).
- **Subscriptions — APP CODE DONE 2026-11-18 (apk-5, unreleased).**
  Flutter `in_app_purchase` 3.3.1; `lib/services/subscription_service.dart`
  (BillingStore seam + RealBillingStore, fake-store tests); product IDs
  `redunator_pro_monthly` / `redunator_pro_yearly` (must be created in
  Play Console with EXACTLY these IDs). Pro unlocks Route planning +
  proximity alerts; Map + Query stay free (gate = `isPro`, one place).
  A purchase only grants Pro after the backend confirms the token
  (POST /subscriptions/verify); without backend verification Pro stays
  off. Settings has a "Redunator Pro" section (status, products, restore);
  Route shows an upsell card when not Pro. **Backend side DONE
  2026-11-18:** `POST /subscriptions/verify` + device JWTs are
  implemented and tested (129 backend tests pass); still needs the
  Play service-account JSON on the host (see §4). **Not yet
  live-testable:** no products exist in Play Console (Google Play
  part, excluded by Gabriel). 10 new app tests; full suite 66 pass.
- **Tap sounds — DONE 2026-11-18 (apk-5, unreleased).** Synthesized
  `assets/tap.wav` (~70 ms click) + `lib/services/sound_service.dart`
  (reuses the audioplayers setup; alert sound stays separate). Wired
  into buttons, bottom nav, map markers and list selection, with a
  "Tap sounds" toggle + test button in Settings (default on). 6 new
  app tests; full suite 66 pass.
- **Launch hardening (DEPLOY.md §4) — JWT + CORS DONE 2026-11-18
  (backend); still open:** Postgres for user data (SQLite is
  ephemeral on Cloud Run and vanishes on redeploy), monitoring/
  alerts on `/health`, and automating the redeploy after a
  monthly-retrain promotion (Cloud Run does not pick up a promoted
  model until a redeploy happens).
