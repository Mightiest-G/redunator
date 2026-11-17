"""Redunator Phase 3 — storage abstraction (Worker B).

One interface used by all user endpoints (reports, alerts, feedback,
settings) so the Postgres swap is a config change, not a rewrite.

Backends
--------
* SQLite (default, zero config): stdlib ``sqlite3`` only. Database
  file defaults to ``data/redunator.db`` under the project root;
  override with the ``REDUNATOR_DB_PATH`` env var (tests use this).
  Tables are created on first use.
* PostgreSQL/PostGIS (opt-in): set ``DATABASE_URL`` to a
  ``postgresql://`` / ``postgres://`` URL. Requires ``psycopg`` (v3)
  or ``psycopg2`` installed — neither is a dependency of the default
  install, and the local run works with zero config when the var is
  unset. PostGIS is not needed yet: locations are stored as plain
  lat/lon columns; a PostGIS migration (``GEOGRAPHY(Point, 4326)``
  + GiST index) is a schema change for a later phase, not a code
  change — the interface below stays the same.

Caching / Redis
---------------
No Redis dependency in this phase. The only cache in the backend is
the in-memory TTL cache already inside ``live_signals.py`` (15 min,
per-process). Swap-in point for Redis: replace the ``_CACHE`` dict
in ``live_signals.py`` with a Redis client and keep the same
``(fetched_epoch, LiveSignal)`` values serialised as JSON; nothing
in this module needs to change. See ``backend/README.md``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "redunator.db"

# Column definitions shared by both backends (TEXT timestamps, JSON
# stored as TEXT). Only the primary-key / upsert spelling differs.
_TABLES_SQLITE = {
    "user_reports": """
        CREATE TABLE IF NOT EXISTS user_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'anonymous',
            camera_id TEXT,
            latitude REAL,
            longitude REAL,
            reported_at TEXT,
            note TEXT,
            created_at TEXT NOT NULL
        )
    """,
    "user_alerts": """
        CREATE TABLE IF NOT EXISTS user_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'anonymous',
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            radius_m REAL NOT NULL DEFAULT 2000,
            threshold TEXT NOT NULL DEFAULT 'MEDIUM',
            threshold_value REAL,
            name TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """,
    "user_feedback": """
        CREATE TABLE IF NOT EXISTS user_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'anonymous',
            text TEXT NOT NULL,
            rating INTEGER,
            category TEXT,
            created_at TEXT NOT NULL
        )
    """,
    "user_settings": """
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id TEXT PRIMARY KEY,
            settings_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        )
    """,
}

_TABLES_PG = {
    name: sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT",
                      "SERIAL PRIMARY KEY")
    for name, sql in _TABLES_SQLITE.items()
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StorageError(RuntimeError):
    """Raised when the configured storage backend cannot be used."""


class Store:
    """Single storage interface for the user endpoints.

    ``Store()`` picks SQLite by default, or PostgreSQL when
    ``DATABASE_URL`` is a postgres URL. ``Store(db_path=...)`` forces
    SQLite at that path (used by tests). One short-lived connection
    per operation: safe under FastAPI's threaded sync endpoints and
    under uvicorn workers without a pool.
    """

    def __init__(self, db_path: str | Path | None = None,
                 database_url: str | None = None):
        self.database_url = database_url if database_url is not None \
            else os.environ.get("DATABASE_URL", "")
        self.pg = self.database_url.startswith(
            ("postgresql://", "postgres://"))
        if self.pg:
            self.db_path: Path | None = None
            self._pg_module = None
            for mod_name in ("psycopg", "psycopg2"):
                try:
                    import importlib
                    self._pg_module = importlib.import_module(mod_name)
                    break
                except ImportError:
                    continue
            if self._pg_module is None:
                raise StorageError(
                    "DATABASE_URL requests PostgreSQL but neither "
                    "'psycopg' nor 'psycopg2' is installed. Install one "
                    "(pip install psycopg[binary]) or unset DATABASE_URL "
                    "to use the default SQLite store.")
        else:
            env_path = os.environ.get("REDUNATOR_DB_PATH", "")
            self.db_path = Path(db_path) if db_path is not None else (
                Path(env_path) if env_path else DEFAULT_DB_PATH)
        self._lock = threading.Lock()
        self._ph = "%s" if self.pg else "?"
        self._init_tables()

    # -- connection -------------------------------------------------
    def _connect(self):
        if self.pg:
            return self._pg_module.connect(self.database_url)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        tables = _TABLES_PG if self.pg else _TABLES_SQLITE
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.cursor()
                for sql in tables.values():
                    cur.execute(sql)
                conn.commit()
            finally:
                conn.close()

    def _fetchall_dicts(self, conn, sql, params=()):
        cur = conn.cursor()
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def _fetchone_dict(self, conn, sql, params=()):
        rows = self._fetchall_dicts(conn, sql, params)
        return rows[0] if rows else None

    def _insert(self, conn, sql, params):
        """Execute an INSERT and return the new row id."""
        cur = conn.cursor()
        if self.pg:
            cur.execute(sql + " RETURNING id", params)
            return cur.fetchone()[0]
        cur.execute(sql, params)
        return cur.lastrowid

    # -- reports ----------------------------------------------------
    def create_report(self, *, user_id: str, camera_id=None,
                      latitude=None, longitude=None, reported_at=None,
                      note=None) -> dict:
        now = _utcnow_iso()
        ph = self._ph
        sql = (f"INSERT INTO user_reports (user_id, camera_id, latitude, "
               f"longitude, reported_at, note, created_at) VALUES "
               f"({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})")
        with self._lock:
            conn = self._connect()
            try:
                rid = self._insert(conn, sql, (
                    user_id, camera_id, latitude, longitude,
                    reported_at, note, now))
                conn.commit()
                return self._fetchone_dict(
                    conn, "SELECT * FROM user_reports WHERE id = " + ph,
                    (rid,))
            finally:
                conn.close()

    def get_report(self, report_id: int) -> dict | None:
        with self._lock:
            conn = self._connect()
            try:
                return self._fetchone_dict(
                    conn, "SELECT * FROM user_reports WHERE id = " + self._ph,
                    (report_id,))
            finally:
                conn.close()

    def list_reports(self, user_id: str | None = None,
                     limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM user_reports"
        params: tuple = ()
        if user_id is not None:
            sql += " WHERE user_id = " + self._ph
            params = (user_id,)
        sql += " ORDER BY id DESC LIMIT " + self._ph
        params = params + (limit,)
        with self._lock:
            conn = self._connect()
            try:
                return self._fetchall_dicts(conn, sql, params)
            finally:
                conn.close()

    # -- alerts -----------------------------------------------------
    def create_alert(self, *, user_id: str, latitude: float,
                     longitude: float, radius_m: float = 2000,
                     threshold: str = "MEDIUM", threshold_value=None,
                     name=None) -> dict:
        now = _utcnow_iso()
        ph = self._ph
        sql = (f"INSERT INTO user_alerts (user_id, latitude, longitude, "
               f"radius_m, threshold, threshold_value, name, active, "
               f"created_at, updated_at) VALUES ({ph}, {ph}, {ph}, {ph}, "
               f"{ph}, {ph}, {ph}, 1, {ph}, {ph})")
        with self._lock:
            conn = self._connect()
            try:
                aid = self._insert(conn, sql, (
                    user_id, latitude, longitude, radius_m, threshold,
                    threshold_value, name, now, now))
                conn.commit()
                return self._fetchone_dict(
                    conn, "SELECT * FROM user_alerts WHERE id = " + ph,
                    (aid,))
            finally:
                conn.close()

    def list_alerts(self, user_id: str, active_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM user_alerts WHERE user_id = " + self._ph
        params: tuple = (user_id,)
        if active_only:
            sql += " AND active = 1"
        sql += " ORDER BY id DESC"
        with self._lock:
            conn = self._connect()
            try:
                rows = self._fetchall_dicts(conn, sql, params)
            finally:
                conn.close()
        for r in rows:
            r["active"] = bool(r["active"])
        return rows

    def deactivate_alert(self, alert_id: int, user_id: str) -> dict | None:
        now = _utcnow_iso()
        ph = self._ph
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute(
                    f"UPDATE user_alerts SET active = 0, updated_at = {ph} "
                    f"WHERE id = {ph} AND user_id = {ph}",
                    (now, alert_id, user_id))
                conn.commit()
                return self._fetchone_dict(
                    conn, "SELECT * FROM user_alerts WHERE id = " + ph,
                    (alert_id,))
            finally:
                conn.close()

    # -- feedback ---------------------------------------------------
    def create_feedback(self, *, user_id: str, text: str, rating=None,
                        category=None) -> dict:
        now = _utcnow_iso()
        ph = self._ph
        sql = (f"INSERT INTO user_feedback (user_id, text, rating, "
               f"category, created_at) VALUES ({ph}, {ph}, {ph}, {ph}, {ph})")
        with self._lock:
            conn = self._connect()
            try:
                fid = self._insert(conn, sql, (
                    user_id, text, rating, category, now))
                conn.commit()
                return self._fetchone_dict(
                    conn, "SELECT * FROM user_feedback WHERE id = " + ph,
                    (fid,))
            finally:
                conn.close()

    # -- settings ---------------------------------------------------
    def get_settings(self, user_id: str) -> dict:
        with self._lock:
            conn = self._connect()
            try:
                row = self._fetchone_dict(
                    conn,
                    "SELECT settings_json FROM user_settings "
                    "WHERE user_id = " + self._ph, (user_id,))
            finally:
                conn.close()
        if not row:
            return {}
        try:
            return json.loads(row["settings_json"])
        except (ValueError, TypeError):
            return {}

    def set_settings(self, user_id: str, settings: dict) -> dict:
        """Upsert (merge over existing) and return the stored settings."""
        merged = self.get_settings(user_id)
        merged.update(settings or {})
        now = _utcnow_iso()
        ph = self._ph
        blob = json.dumps(merged)
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute(
                    f"INSERT INTO user_settings (user_id, settings_json, "
                    f"updated_at) VALUES ({ph}, {ph}, {ph}) "
                    f"ON CONFLICT(user_id) DO UPDATE SET "
                    f"settings_json = {ph}, updated_at = {ph}",
                    (user_id, blob, now, blob, now))
                conn.commit()
            finally:
                conn.close()
        return merged


_store: Store | None = None


def get_store() -> Store:
    """Process-wide default store (SQLite unless DATABASE_URL is set)."""
    global _store
    if _store is None:
        _store = Store()
    return _store
