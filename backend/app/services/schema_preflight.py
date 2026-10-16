"""Convergence Phase 4-R1 (P4RV-04/05) — the start-up schema preflight.

Migrations run BEFORE the replacement application container starts
(`docs/operations/RECOVERY_MANIFEST.md` §6). If they did not, this refuses to let the
application start, so a deployment cannot run a booking path on a schema that lacks what it
depends on:

* `thread_revisions.notification_sent_at` — the operator booking e-mail's only dedup and the
  SENT leg of the operator notification surface. Declared by the model and migration
  `20261016_notification_sent_at`; no longer created by the application.
* on PostgreSQL, the `booking_attempt_events` append-only trigger — absent when the
  application's `create_all` created the table before `20261015_booking_attempt_events` ran.

It reads the catalogue only and writes nothing.
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text

logger = logging.getLogger(__name__)


class DeclaredSchemaMissing(RuntimeError):
    """The database is not at the schema this application declares."""


def missing_declared_booking_schema(engine) -> list[str]:
    """What the declared schema requires and the database lacks (empty when complete)."""
    missing: list[str] = []
    insp = inspect(engine)
    if "thread_revisions" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("thread_revisions")}
        if "notification_sent_at" not in cols:
            missing.append("thread_revisions.notification_sent_at "
                           "(migration 20261016_notification_sent_at)")
    if engine.dialect.name == "postgresql":
        with engine.connect() as conn:
            if conn.execute(text("SELECT to_regclass('booking_attempt_events')")).scalar():
                trig = conn.execute(text(
                    "SELECT count(*) FROM pg_trigger WHERE tgrelid = "
                    "'booking_attempt_events'::regclass AND NOT tgisinternal AND "
                    "tgname = 'trg_booking_attempt_events_append_only' AND tgenabled = 'O'")
                ).scalar()
                if not trig:
                    missing.append("booking_attempt_events append-only trigger "
                                   "(migration 20261015_booking_attempt_events)")
            else:
                missing.append("booking_attempt_events "
                               "(migration 20261015_booking_attempt_events)")
    return missing


def require_declared_booking_schema(engine) -> None:
    missing = missing_declared_booking_schema(engine)
    if missing:
        logger.critical("SCHEMA_PREFLIGHT_FAILED — run `alembic upgrade head` before starting "
                        "this application. Missing: %s", "; ".join(missing))
        raise DeclaredSchemaMissing(
            "database schema is behind this application; run the migrations first. Missing: "
            + "; ".join(missing))
