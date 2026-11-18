# Redunator backend — API reference (2026-11-18)

Backend-owned contract for the Flutter app. The app-side copy is
`app/API_REFERENCE.md` (owned by the app lane — mirror auth changes
there too). Base URL locally is `http://127.0.0.1:8000`; live it is
the Cloud Run URL. OpenAPI docs: `GET /docs`, `GET /openapi.json`
(15 paths). Errors everywhere are `{"error": ..., "message": ...}`
(400 bad request, 401 unauthorized, 404 not found, 422 validation,
429 rate limited, 502 upstream verification failed, 503 not
configured, 500 internal). Rate limit: 100 req/min per client IP
(in-memory, sliding window); `/health`, `/app/latest`, `/docs`,
`/openapi.json`, `/redoc` are exempt.

## Auth — device JWTs (added 2026-11-18)

The app has no user accounts. Identity is a per-device JWT:

1. The app generates a UUID `device_id` once (persist it on the
   device) and mints a token:
2. The app sends `Authorization: Bearer <token>` on every user
   endpoint. The token's `sub` claim IS the user id.
3. Tokens live 30 days; the app re-mints on launch when expired
   (or on a 401).

### POST /auth/device

Request body (JSON): `{"device_id": "<uuid>"}` — any UUID spelling
is accepted and normalised to canonical lowercase form.

Response 200:
```json
{"access_token": "<jwt>", "token_type": "bearer", "expires_in": 2592000}
```

Errors: missing / non-UUID `device_id` → 400 `invalid_parameters`.
When `REDUNATOR_JWT_SECRET` is not set on the server → **503**
```json
{"error": "service_unavailable",
 "message": "Device auth is not configured on this server: REDUNATOR_JWT_SECRET is not set.",
 "reason": "jwt_secret_not_configured"}
```
(same fallback pattern as `GET /app/latest`).

JWT details: HS256, secret `REDUNATOR_JWT_SECRET`, claims
`sub` = device_id, `iss` = `"redunator"`, `iat`, `exp` = +30 days.
Rotating the secret invalidates every outstanding token at once;
devices simply mint a fresh one. There is no server-side token
store. The secret must be a long random value (32+ bytes), never
committed, never printed, never put in a URL.

### Protected endpoints (401 without a valid token)

All four user endpoints plus subscription verification require the
Bearer token. Missing / malformed / expired / mis-signed / wrong-
issuer token → **401** `{"error": "unauthorized", "message": ...}`.
The old `X-User-Id` header and `user_id` body field are ignored
for identity entirely, and the old `X-Api-Key` gate is gone.

- `POST /user_report` → 201. Body: `camera_id` and/or `lat`+`lon`
  (at least one location form required), optional `timestamp`
  (ISO-8601), optional `note`. Returns the stored report.
- `GET /user_report/{id}` → 200 the stored report. A report owned
  by another device → 404 `not_found` (no existence leak).
- `GET /user_alerts` → 200 `{"user_id", "alerts": [...]}`. Query:
  `active_only` (default true).
- `POST /user_alerts` → 201. Body: `lat`, `lon` (required),
  `radius_m` (default 2000, 100…50000), `threshold` (LOW/MEDIUM/
  HIGH, default MEDIUM), optional `threshold_value` (≥ 0),
  optional `name`.
- `DELETE /user_alerts/{id}` → 200 the deactivated alert
  (`active: false`); unknown id for this device → 404.
- `POST /user_feedback` → 201. Body: `text` (required; `message`
  accepted as an alias), optional `rating` (int 1…5), optional
  `category`.
- `GET /user_settings` → 200 `{"user_id", "settings": {...}}`
  merged over defaults (`units: metric`, `alert_radius_m: 2000`,
  `risk_threshold: MEDIUM`, `notifications: {email: false, push:
  false}`).
- `POST /user_settings` → 200 merged settings. Body: flat object
  or `{"settings": {...}}`; merges over stored values.

### POST /subscriptions/verify

Requires the device JWT. Request body (JSON):
```json
{"product_id": "pro_monthly", "purchase_token": "<from Play Billing>",
 "device_id": "<same device as the token>"}
```
`device_id` must match the token's device (mismatch → 401).

Response 200 when Google Play verification is configured
(service-account JSON at `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON`):
```json
{"valid": true, "product_id": "pro_monthly",
 "expiry": "2026-12-18T06:44:14Z", "auto_renewing": true,
 "reason": null}
```
- `valid` is true for `SUBSCRIPTION_STATE_ACTIVE` and
  `SUBSCRIPTION_STATE_IN_GRACE_PERIOD`, false otherwise
  (cancelled, expired, on hold, paused).
- `expiry` is the matching line item's `expiryTime` (RFC 3339).
- `auto_renewing` is false when the line item has no
  `autoRenewingPlan`, else its `autoRenewEnabled` value.
- A purchase token Google does not know → `valid: false` with
  `reason: "purchase_token_invalid"`.
- A Google-side failure (bad service account, 5xx, network) →
  **502** `{"error": "play_verification_failed", ...}`.

Response 200 when the service account is NOT configured:
```json
{"valid": null, "reason": "play_verification_not_configured"}
```

**Stateless (deliberate, for now):** no entitlement is stored
server-side. The app caches Pro status locally and re-validates
on launch. A later phase can persist verified entitlements keyed
by device_id.

## Public endpoints (no token)

- `GET /health` — liveness + model version + data freshness.
- `GET /app/latest` — latest GitHub release for the in-app update
  check. Without `GITHUB_TOKEN` (or when GitHub is unreachable)
  answers 200 with nulls + `reason`
  (`github_token_not_configured` / `github_lookup_failed`).
- `POST /predict_risk`, `POST /predict_risk/batch`,
  `GET /intersections`, `GET /violation_trends`, `GET /live/risk`
  — unchanged (see `app/API_REFERENCE.md` for their shapes).

## CORS

Default allow-list is empty (deny): the API serves a native app
only, so no browser origin is allowed unless explicitly listed in
`REDUNATOR_CORS_ORIGINS` (comma-separated).

## Storage

SQLite by default (`data/redunator.db`, override
`REDUNATOR_DB_PATH`); PostgreSQL when `DATABASE_URL` is a
`postgresql://` / `postgres://` URL (driver `psycopg[binary]`,
installed). The Postgres code path is verified as far as possible
without a live server (DDL variants, `%s` placeholders,
`RETURNING id`, `ON CONFLICT` upsert — see
`backend/tests/test_postgres_path.py`); **a live Postgres
round-trip has never run** — provision one and set `DATABASE_URL`
before relying on it.

## Environment variables (backend)

| Variable | Default | What it does |
|---|---|---|
| `REDUNATOR_JWT_SECRET` | (unset → auth fails closed) | HS256 secret for device JWTs. Required for the user endpoints. |
| `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` | (unset → verify returns `valid: null`) | Filesystem path to a Google Play service-account JSON file. |
| `DATABASE_URL` | (unset → SQLite) | `postgresql://…` switches the user store to Postgres. |
| `REDUNATOR_DB_PATH` | `<root>/data/redunator.db` | SQLite file for the user store. |
| `REDUNATOR_CORS_ORIGINS` | (empty = deny) | Comma-separated allowed origins. |
| `REDUNATOR_RATE_LIMIT_PER_MIN` | `100` | Requests/minute per client IP. |
| `REDUNATOR_HOST` / `REDUNATOR_PORT` | `127.0.0.1` / `8000` | Bind host/port. |
| `REDUNATOR_MAX_NEAREST_M` | `2000` | Max distance for lat/lon → nearest camera. |
| `GITHUB_TOKEN` | (unset) | Read-only GitHub token for `GET /app/latest`. |
| `TOMTOM_API_KEY` / `HERE_API_KEY` / `CTA_BUS_API_KEY` | (unset) | Key-gated live providers for `/live/risk`. |
