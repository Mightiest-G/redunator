"""Storage backends: SQLite (default) + PostgreSQL wiring (Worker C).

A live PostgreSQL server is NOT available in this environment, so
the Postgres path is exercised only as far as it can be without one:
the driver import, URL parsing, and the connection attempt (which
must fail with a connection error from the driver — NOT with the
"neither psycopg nor psycopg2 is installed" StorageError). A live
Postgres round-trip remains UNTESTED; see backend/README.md.
"""

from __future__ import annotations

import pytest


def test_sqlite_store_round_trip(tmp_path):
    from backend.storage import Store

    store = Store(db_path=tmp_path / "roundtrip.db")
    rec = store.create_report(user_id="u1", camera_id="1413",
                              note="sqlite works")
    assert rec["id"]
    assert store.get_report(rec["id"])["note"] == "sqlite works"
    alert = store.create_alert(user_id="u1", latitude=41.9,
                               longitude=-87.6, threshold="HIGH")
    assert store.list_alerts("u1") != []
    assert store.deactivate_alert(alert["id"], "u1")["active"] == 0
    assert store.list_alerts("u1") == []
    store.set_settings("u1", {"units": "imperial"})
    assert store.get_settings("u1")["units"] == "imperial"
    fb = store.create_feedback(user_id="u1", text="hi", rating=4)
    assert fb["text"] == "hi"


def test_postgres_connection_failure_is_driver_error_not_missing_driver():
    """With psycopg installed, a postgres DATABASE_URL is treated as
    Postgres (not SQLite) and the failure is a driver connection
    error — proving the driver import + URL parsing up to the
    network. There is no server at 127.0.0.1:1, so connect() must
    refuse fast. (If the URL were mis-parsed as SQLite, no
    OperationalError would occur at all.)"""
    import psycopg

    from backend.storage import Store

    with pytest.raises(psycopg.OperationalError, match="Connection refused"):
        Store(database_url="postgresql://redunator:secret@127.0.0.1:1/redunator")


def test_postgres_missing_driver_error_message(monkeypatch):
    """If neither psycopg nor psycopg2 can be imported, Store raises
    the clear StorageError naming the missing package (simulated by
    blocking the imports)."""
    import importlib

    from backend.storage import StorageError, Store

    real_import_module = importlib.import_module

    def fake_import_module(name, *args, **kwargs):
        if name in ("psycopg", "psycopg2"):
            raise ImportError(f"No module named {name!r} (blocked for test)")
        return real_import_module(name, *args, **kwargs)

    # Patch importlib.import_module itself: patching builtins.__import__
    # does NOT work — importlib._bootstrap captured the original at
    # import time. (Also evict any cached psycopg entry so the patched
    # function is actually consulted.)
    import sys

    for mod in ("psycopg", "psycopg2"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    with pytest.raises(StorageError, match="neither 'psycopg' nor 'psycopg2'"):
        Store(database_url="postgresql://u:p@127.0.0.1:1/db")
