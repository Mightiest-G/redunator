"""Redunator Phase 3 backend — app factory (Worker A: core app).

Run from the project root:
    ~/workspace/redunator-venv/bin/python -m backend.app
    # or
    ~/workspace/redunator-venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port 8000

Routers from Worker B (backend/routers/live.py, backend/routers/users.py)
are included tolerantly: if the module is importable its router is
mounted, otherwise a warning is logged and the app still runs. Do not
create placeholder live.py/users.py here — Worker B owns those files.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config
from .auth import issue_device_token
from .rate_limit import limiter

PROJECT_ROOT = config.PROJECT_ROOT
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

log = logging.getLogger("redunator.backend")
logging.basicConfig(level=logging.INFO, format="%(message)s")

RATE_LIMIT_EXEMPT = {"/health", "/app/latest", "/docs", "/openapi.json",
                     "/redoc", "/openapi.json", "/docs/oauth2-redirect"}


def _release_build(tag: str | None) -> int | None:
    """Build number from a release tag like 'apk-4' (None otherwise).

    The Actions workflow tags releases apk-<run-number> and stamps the
    same number into the APK as its build number, so the app's update
    check compares these two numbers.
    """
    if not tag:
        return None
    m = re.match(r"apk-(\d+)$", tag)
    return int(m.group(1)) if m else None


def _latest_release_unavailable(reason: str) -> dict:
    """Shape of GET /app/latest when GitHub cannot be reached."""
    return {
        "tag": None,
        "build": None,
        "name": None,
        "published_at": None,
        "download_url": None,
        "notes": None,
        "reason": reason,
    }


def _log_line(level: str, event: str, **fields) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "level": level,
           "event": event, **fields}
    (log.info if level == "info" else log.warning)(json.dumps(rec))


def _error_body(status: int, error: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": error, "message": message})


def _latest_crash_date() -> tuple[str | None, str | None]:
    """(latest crash date, source) — None if no crash file is present."""
    p = config.CRASHES_PATH
    if not p.exists():
        return None, None
    try:
        # crash_date is the first data column after the id; read only it.
        df = pd.read_csv(p, usecols=["crash_date"], parse_dates=["crash_date"])
        return str(df["crash_date"].max().date()), str(p.relative_to(PROJECT_ROOT))
    except Exception as e:
        log.warning(json.dumps({"event": "crash_date_read_failed",
                                "error": str(e)}))
        return None, str(p.relative_to(PROJECT_ROOT))


def create_app() -> FastAPI:
    app = FastAPI(
        title="Redunator API",
        description=(
            "Chicago red-light-camera ticket-risk predictions. Daily "
            "predictions come from the served Random Forest model (ONNX, "
            "version named by live_model.json). Hourly values are ESTIMATES "
            "from proxies, never observed hourly violations. No live "
            "adjustment is applied on the default path."),
        version="3.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # -- structured logging + rate limiting ----------------------------------
    @app.middleware("http")
    async def logging_and_rate_limit(request: Request, call_next):
        start = time.monotonic()
        client = request.client.host if request.client else "unknown"
        path = request.url.path
        status = 500
        try:
            if path not in RATE_LIMIT_EXEMPT:
                allowed, remaining, reset = limiter.check(client)
                if not allowed:
                    resp = _error_body(429, "rate_limited",
                                       f"Rate limit exceeded: "
                                       f"{limiter.per_min} requests/minute "
                                       f"per client. Retry after "
                                       f"{reset:.0f}s.")
                    resp.headers["X-RateLimit-Limit"] = str(limiter.per_min)
                    resp.headers["X-RateLimit-Remaining"] = "0"
                    resp.headers["Retry-After"] = str(int(reset) + 1)
                    _log_line("warning", "rate_limited", path=path,
                              client=client, status=429)
                    return resp
                response = await call_next(request)
                response.headers["X-RateLimit-Limit"] = str(limiter.per_min)
                response.headers["X-RateLimit-Remaining"] = str(remaining)
                status = response.status_code
            else:
                response = await call_next(request)
                status = response.status_code
        except Exception:
            _log_line("warning", "unhandled_error", path=path, client=client)
            raise
        finally:
            _log_line("info", "request", method=request.method, path=path,
                      status=status, client=client,
                      duration_ms=round((time.monotonic() - start) * 1000, 1))
        return response

    # -- clean error responses: {"error": ..., "message": ...} ----------------
    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        details = "; ".join(
            f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}"
            for e in exc.errors())
        return _error_body(422, "validation_error", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException):
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return _error_body(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        _log_line("warning", "unhandled_exception", path=request.url.path,
                  error=str(exc))
        return _error_body(500, "internal_error",
                           "Internal server error. See server logs.")

    # -- core routers (Worker A) ----------------------------------------------
    from .routers import predict
    app.include_router(predict.router)

    # -- Worker B routers, included tolerantly --------------------------------
    for mod_name in ("live", "users", "subscriptions"):
        try:
            import importlib
            mod = importlib.import_module(f".routers.{mod_name}",
                                          package=__package__)
            app.include_router(mod.router)
            _log_line("info", "router_included", module=mod_name)
        except ImportError as e:
            _log_line("warning", "router_missing",
                      module=mod_name, note=f"{mod_name}.py not present yet "
                      f"(Worker B); continuing without it.", error=str(e))
        except Exception as e:
            _log_line("warning", "router_failed", module=mod_name,
                      error=str(e))

    # -- health ----------------------------------------------------------------
    @app.get("/health", summary="Liveness + model version + data freshness")
    def health():
        from .model_service import service
        crash_date, crash_source = _latest_crash_date()
        ptr = {}
        try:
            ptr = service.pointer()
        except Exception as e:
            return {"status": "degraded",
                    "error": "model_pointer_unreadable", "message": str(e)}
        return {
            "status": "ok",
            "model_version": ptr.get("live_version"),
            "model_files": {
                "joblib": ptr.get("joblib"),
                "onnx": ptr.get("onnx"),
                "json": ptr.get("json"),
            },
            "model_metrics": ptr.get("metrics", {}),
            "data_freshness": {
                "latest_violation_date": service.latest_violation_date,
                "latest_violation_source":
                    "data/processed/red_light_features_serving.parquet",
                "latest_crash_date": crash_date,
                "latest_crash_source": crash_source,
            },
        }

    # -- latest app release (for the Flutter app's update check) ---------
    @app.get("/app/latest", summary="Latest published APK release")
    def app_latest():
        """Latest GitHub release, for the app's in-app update check.

        Reads a read-only GitHub token from the GITHUB_TOKEN env var
        (fine-grained PAT, Contents: read on Mightiest-G/redunator).
        Without a token — or when GitHub cannot be reached — the
        endpoint still answers 200, with nulls and a reason, so the
        app simply skips the update check and keeps working.
        """
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            return _latest_release_unavailable(
                "github_token_not_configured")
        try:
            resp = requests.get(
                "https://api.github.com/repos/Mightiest-G/redunator"
                "/releases/latest",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "redunator-backend",
                },
                timeout=10,
            )
        except Exception as e:
            _log_line("warning", "app_latest_lookup_failed",
                      error=str(e))
            return _latest_release_unavailable("github_lookup_failed")
        if resp.status_code != 200:
            _log_line("warning", "app_latest_lookup_failed",
                      status=resp.status_code)
            return _latest_release_unavailable("github_lookup_failed")
        data = resp.json()
        tag = data.get("tag_name")
        apk = next(
            (a for a in data.get("assets", [])
             if a.get("name") == "app-release.apk"),
            None,
        )
        url = (apk.get("browser_download_url") if apk
               else data.get("html_url"))
        return {
            "tag": tag,
            "build": _release_build(tag),
            "name": data.get("name"),
            "published_at": data.get("published_at"),
            "download_url": url,
            "notes": (data.get("body") or "")[:500],
            "reason": None,
        }

    # -- device auth (no user accounts; see backend/auth.py) ----------------
    @app.post("/auth/device", summary="Mint a device JWT")
    def auth_device(payload: dict = Body(...)):
        """Mint a 30-day bearer token for a device UUID.

        Request: {"device_id": "<uuid>"}. Response: {"access_token",
        "token_type": "bearer", "expires_in": 2592000}. The app sends
        the token as `Authorization: Bearer <token>` on every user
        endpoint; the token's `sub` claim is the device's user id.

        When REDUNATOR_JWT_SECRET is not set the endpoint answers
        503 with reason "jwt_secret_not_configured" (same fallback
        pattern as GET /app/latest) instead of minting anything.
        """
        import uuid as _uuid

        raw = payload.get("device_id")
        if not raw or not isinstance(raw, str):
            return _error_body(400, "invalid_parameters",
                               "device_id is required (a UUID string).")
        try:
            device_id = str(_uuid.UUID(raw))
        except ValueError:
            return _error_body(400, "invalid_parameters",
                               "device_id must be a UUID.")
        try:
            token, expires_in = issue_device_token(device_id)
        except ValueError as e:
            if "not configured" in str(e):
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "service_unavailable",
                        "message": "Device auth is not configured on this "
                                   "server: REDUNATOR_JWT_SECRET is not set.",
                        "reason": "jwt_secret_not_configured",
                    })
            return _error_body(400, "invalid_parameters", str(e))
        return {"access_token": token, "token_type": "bearer",
                "expires_in": expires_in}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app:app", host=config.HOST, port=config.PORT,
                log_level="info")
