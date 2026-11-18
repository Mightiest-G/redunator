"""PostgreSQL path, exercised as far as possible WITHOUT a live
server (there is none on this VM, and no Docker daemon).

What this file proves:
* ``DATABASE_URL`` (arg or env var) switches the store to Postgres
  mode (``.pg`` True, ``%s`` placeholders).
* Table creation uses the Postgres DDL variants (``SERIAL`` primary
  keys, no SQLite ``AUTOINCREMENT``).
* The Postgres-specific SQL spellings are generated: ``RETURNING
  id`` on inserts, ``ON CONFLICT(user_id) DO UPDATE`` on the
  settings upsert — by running the real Store methods against a
  fake psycopg-style connection that records every statement.
* The SQLite default is untouched (see test_storage.py for the live
  SQLite round-trip).

What it does NOT prove (still needs a live server): that a real
Postgres accepts the DDL, that ``RETURNING id`` / ``ON CONFLICT``
execute against a real database, or that ``psycopg`` connects to a
real host. test_storage.py already proves the driver import + URL
parsing up to the refused connection. See backend/README.md.
"""

from __future__ import annotations

import pytest

from backend.storage import Store


class FakeCursor:
    """A psycopg-shaped cursor: records (sql, params), answers canned
    rows based on the last statement executed."""

    COLS = {
        "user_reports": ["id", "user_id", "camera_id", "latitude",
                         "longitude", "reported_at", "note", "created_at"],
        "user_alerts": ["id", "user_id", "latitude", "longitude",
                        "radius_m", "threshold", "threshold_value", "name",
                        "active", "created_at", "updated_at"],
        "user_settings": ["settings_json"],
    }

    def __init__(self, log):
        self.log = log
        self._sql = ""
        self.description = None
        self.lastrowid = 42

    def execute(self, sql, params=()):
        self.log.append((sql, params))
        self._sql = sql
        # _fetchall_dicts reads cur.description right after execute(),
        # before fetchall() — so describe the result set here, not in
        # _rows().
        if "RETURNING id" in sql:
            self.description = [("id",)]
        elif "FROM user_reports WHERE id" in sql:
            self.description = [(c,) for c in self.COLS["user_reports"]]
        elif "FROM user_alerts WHERE id" in sql:
            self.description = [(c,) for c in self.COLS["user_alerts"]]
        elif "SELECT settings_json FROM user_settings" in sql:
            self.description = [("settings_json",)]
        else:
            self.description = []
        return self

    def _rows(self):
        sql = self._sql
        if "RETURNING id" in sql:
            return [(42,)]
        if "FROM user_reports WHERE id" in sql:
            return [(42, "dev-1", "1413", None, None, None, None,
                     "2026-11-18T00:00:00+00:00")]
        if "FROM user_alerts WHERE id" in sql:
            return [(7, "dev-1", 41.9, -87.6, 2000, "MEDIUM", None, None,
                     1, "t", "t")]
        if "SELECT settings_json FROM user_settings" in sql:
            return []
        return []

    def fetchall(self):
        return list(self._rows())

    def fetchone(self):
        rows = self._rows()
        return rows[0] if rows else None


class FakeConnection:
    def __init__(self, log):
        self._log = log

    def cursor(self):
        return FakeCursor(self._log)

    def commit(self):
        pass

    def close(self):
        pass


@pytest.fixture()
def pg_store(monkeypatch):
    """A Store in Postgres mode whose connections are faked. Returns
    (store, log) where log is the list of (sql, params) executed."""
    log: list = []
    monkeypatch.setattr(Store, "_connect",
                        lambda self: FakeConnection(log))
    store = Store(database_url="postgresql://u:p@localhost:5432/redunator")
    return store, log


def test_database_url_switches_to_postgres_mode(pg_store):
    store, _ = pg_store
    assert store.pg is True
    assert store._ph == "%s"
    assert store.db_path is None


def test_database_url_from_env(monkeypatch):
    log: list = []
    monkeypatch.setenv("DATABASE_URL",
                       "postgres://u:p@localhost:5432/redunator")
    monkeypatch.setattr(Store, "_connect",
                        lambda self: FakeConnection(log))
    store = Store()
    assert store.pg is True
    assert store._ph == "%s"


def test_sqlite_default_unchanged(tmp_path):
    store = Store(db_path=tmp_path / "default.db")
    assert store.pg is False
    assert store._ph == "?"
    rec = store.create_report(user_id="u1", camera_id="1413")
    assert rec["id"]


def test_pg_ddl_uses_serial_not_autoincrement():
    from backend.storage import _TABLES_PG, _TABLES_SQLITE

    for name in ("user_reports", "user_alerts", "user_feedback"):
        assert "SERIAL PRIMARY KEY" in _TABLES_PG[name], name
        assert "AUTOINCREMENT" not in _TABLES_PG[name], name
        assert "INTEGER PRIMARY KEY AUTOINCREMENT" in _TABLES_SQLITE[name]


def test_pg_table_creation_uses_pg_ddl(pg_store):
    _, log = pg_store
    created = " ".join(sql for sql, _ in log)
    assert "SERIAL PRIMARY KEY" in created
    assert "AUTOINCREMENT" not in created
    assert "CREATE TABLE IF NOT EXISTS user_reports" in created
    assert "CREATE TABLE IF NOT EXISTS user_alerts" in created
    assert "CREATE TABLE IF NOT EXISTS user_feedback" in created
    assert "CREATE TABLE IF NOT EXISTS user_settings" in created


def test_pg_insert_uses_returning_id_and_percent_s(pg_store):
    store, log = pg_store
    rec = store.create_report(user_id="dev-1", camera_id="1413",
                              note="pg insert")
    assert rec["id"] == 42
    assert rec["user_id"] == "dev-1"
    inserts = [sql for sql, _ in log if "INSERT INTO user_reports" in sql]
    assert len(inserts) == 1
    assert "RETURNING id" in inserts[0]
    assert "%s" in inserts[0] and "?" not in inserts[0]


def test_pg_alert_insert_and_deactivate_use_percent_s(pg_store):
    store, log = pg_store
    rec = store.create_alert(user_id="dev-1", latitude=41.9,
                             longitude=-87.6)
    assert rec["id"] == 7
    assert rec["active"] == 1  # raw value; list_alerts coerces to bool
    alert_sql = [sql for sql, _ in log if "INSERT INTO user_alerts" in sql]
    assert alert_sql and "%s" in alert_sql[0]
    store.deactivate_alert(7, "dev-1")
    updates = [sql for sql, _ in log if "UPDATE user_alerts" in sql]
    assert updates and "%s" in updates[0]


def test_pg_settings_upsert_uses_on_conflict(pg_store):
    store, log = pg_store
    merged = store.set_settings("dev-1", {"units": "imperial"})
    assert merged == {"units": "imperial"}
    upserts = [sql for sql, _ in log
               if "INSERT INTO user_settings" in sql]
    assert len(upserts) == 1
    assert "ON CONFLICT(user_id) DO UPDATE" in upserts[0]
    assert "%s" in upserts[0] and "?" not in upserts[0]
