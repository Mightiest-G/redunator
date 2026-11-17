"""Redunator Phase 3 backend package.

Worker A owns ``app.py`` (the ``create_app`` factory, which includes
the routers). Worker B owns ``routers/live.py``, ``routers/users.py``
and ``storage.py`` (plus ``live_service.py`` if needed — not needed:
the live logic lives in the top-level ``live_adjust`` /
``live_signals`` modules and is imported, not re-implemented).
"""
