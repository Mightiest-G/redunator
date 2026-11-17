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
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config
from .rate_limit import limiter

PROJECT_ROOT = config.PROJECT_ROOT
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

log = logging.getLogger("redunator.backend")
logging.basicConfig(level=logging.INFO, format="%(message)s")

RATE_LIMIT_EXEMPT = {"/health", "/docs", "/openapi.json", "/redoc",
                     "/openapi.json", "/docs/oauth2-redirect"}


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
    for mod_name in ("live", "users"):
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

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app:app", host=config.HOST, port=config.PORT,
                log_level="info")
