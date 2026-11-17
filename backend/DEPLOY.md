# Redunator backend — deployment guide (Phase 3, Worker C, 2026-11-16)

Deploy target: **Render**, Docker runtime, from `backend/render.yaml`
(a Render Blueprint). **Nothing has been deployed.** Every step below
is something Gabriel (or the coordinator) does by hand; no accounts,
keys, or services were created for this guide.

## 0. Read this first — the free tier does NOT fit this backend

Measured on this VM 2026-11-16 (one uvicorn worker, warmed with
`/predict_risk` + `/live/risk` + `/violation_trends`):

| What | Measured |
|---|---|
| Warm RSS, whole backend process | **~1.2 GB** |
| `redunator_brain_v1.joblib` (Random Forest) loaded by `hourly_risk` | ~295 MB |
| `data/processed/red_light_features.parquet` (1.3M rows) in pandas | ~390 MB |
| ONNX session (`redunator_brain_v1.onnx`) in `model_service` | ~330 MB |

Render's free web instance has **512 MB RAM** and will OOM-kill this
app (the process holds the joblib forest AND the ONNX session AND the
full parquet at once). The paid Starter plan is also 512 MB — also
not enough. The Blueprint therefore uses **`standard` (1 CPU / 2 GB,
$25/mo at time of writing)**. Render's own docs say of free instances:
"Do not use them for production applications."

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
- [ ] **CTA Bus Tracker API key** (`CTA_BUS_API_KEY`) — Sign up at transitchicago.com / CTA Bus Tracker developer page.
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

## 5. Getting back to the free tier (future work, NOT done)

The free tier needs warm RSS under 512 MB; measured is ~1.2 GB.
The three levers, in order of payoff:

1. **Drop the joblib forest from the serving path (~295 MB).**
   `hourly_risk.daily_prediction` loads `redunator_brain_v1.joblib`
   for the daily anchor while `model_service` serves the same model
   from ONNX. Make the hourly path use the ONNX service and the
   joblib file stops being loaded.
2. **Slim the feature parquet (~390 MB).** The serving path uses a
   handful of columns per row plus `cam_mean_pre_test`; a
   serving-only parquet with just the needed columns (and/or
   per-camera lookup instead of the whole 1.3M-row table) would cut
   this sharply. The full table is only needed for training and for
   `/violation_trends` (which scans one camera's rows — a per-camera
   file or a DB query would do).
3. **Re-measure.** After (1)+(2), re-run the RSS check from the
   README test section; only switch `render.yaml` to `plan: free`
   if warm RSS is comfortably under 512 MB with headroom for a
   traffic burst — and accept the 15-minute spin-down on top.

## 6. Rollback

Render keeps every deploy: Dashboard → service → Deploys → pick the
previous good deploy → **Rollback**. The model files are baked into
the image, so a rollback also rolls the model back to whatever that
deploy shipped — including the `live_model.json` pointer as of that
commit. If a retrain promotion needs undoing, move the pointer back
to `redunator_brain_v1.*` and redeploy (v1 files are never
overwritten).
