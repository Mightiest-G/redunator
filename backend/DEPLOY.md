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
   list all 12 routes.
6. If the deploy will serve real users, set `REDUNATOR_API_KEY` in
   Render (Dashboard → service → Environment → add, Generate a random
   value) and pass it as `X-Api-Key` on the user endpoints. This is a
   shared-secret gate, **not** user auth — JWT auth is still required
   before any public launch (see §4).

## 3. Environment variables (Render)

| Variable | Set in Blueprint? | Notes |
|---|---|---|
| `REDUNATOR_CORS_ORIGINS` | Yes | Comma-separated allowed origins. Add the Flutter web origin when it exists. |
| `REDUNATOR_RATE_LIMIT_PER_MIN` | Yes (`100`) | Per-client, in-memory, per process. `/health`, `/docs`, `/openapi.json` exempt. |
| `REDUNATOR_API_KEY` | No (optional) | If set, every user endpoint requires matching `X-Api-Key`. Dev gate only. |
| `REDUNATOR_MAX_NEAREST_M` | No (default `2000`) | Max distance for lat/lon → nearest camera. |
| `REDUNATOR_DB_PATH` | No | Leave unset on Render (ephemeral SQLite). Set only for local testing. |
| `DATABASE_URL` | No (optional) | `postgresql://…` to switch the user store to Postgres. Needs the Postgres block in `render.yaml`. |
| `REDUNATOR_LIVE_MODEL` | No (default `live_model.json`) | Pointer path. A retrain promotion moves the pointer file; the backend picks it up without a restart. |
| `TOMTOM_API_KEY` / `HERE_API_KEY` / `CTA_BUS_API_KEY` | No (optional) | Key-gated live providers; unset = gracefully dropped, listed in `signals_dropped_stale`. |
| `PORT` | Set by Render | The Dockerfile binds `$PORT` on `0.0.0.0` via `REDUNATOR_HOST` / `REDUNATOR_PORT`. |

## 4. Before any public launch (not done in Phase 3)

- **JWT auth** for the user endpoints. Today identity is an
  `X-User-Id` header anyone can set, and the API-key gate is a single
  shared secret. Do not expose the user endpoints publicly as-is.
- **Persistent user storage**: Postgres (see §0), or accept that
  reports/alerts/feedback/settings vanish on redeploy.
- **Paid plan + monitoring**: the `standard` plan does not sleep, but
  there is no alerting wired up. At minimum watch `/health` and the
  Render metrics page after launch.
- **CORS**: tighten `REDUNATOR_CORS_ORIGINS` to the real app origin(s)
  only; the Blueprint ships the local-dev list.
- **Monthly retrain**: the `redunator-monthly-retrain` cron runs on
  this VM (day 1, ~03:45 America/Chicago). A retrain promotion writes
  versioned `redunator_brain_vN.*` files and moves `live_model.json`
  in the *repo* — Render does NOT pick that up until a redeploy
  happens (the image is a snapshot). The deploy-time answer is: after
  a promotion, redeploy the service (Render dashboard → Manual Deploy,
  or push the new model files + pointer and let Render auto-deploy).
  Automating that (webhook / scheduled redeploy) is Phase 4 work.

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
- **Image:** `us-central1-docker.pkg.dev/redunator/redunator-api/redunator-api:259c8cd`
  (built by Cloud Build from the repo root, `-f backend/Dockerfile`;
  build config is `cloudbuild.yaml` at the repo root)
- **Settings:** memory 2Gi, cpu 1, min-instances 0, max-instances 2,
  `--allow-unauthenticated`, `--port 8000`. The Dockerfile already
  binds `$PORT` (`REDUNATOR_PORT=${PORT:-8000}`), so no code change
  was needed for Cloud Run.
- **Env vars on the service:** `HERE_API_KEY`, `CTA_BUS_API_KEY`,
  `TOMTOM_API_KEY` (all three set; TomTom was added by a second
  deploy after Gabriel supplied the key — it is NOT stored in the
  repo or in memory).
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
