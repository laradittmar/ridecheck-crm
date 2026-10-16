"""Convergence Phase 4-R1 (P4RV-04/05) and Phase 4 STRICT-SCHEMA-ADOPTION — the start-up
schema preflight.

Migrations run BEFORE the replacement application container starts
(`docs/operations/RECOVERY_MANIFEST.md` §6). If they did not, or if the two objects whose
silent drift falsifies evidence or notification truth were changed by hand afterwards, this
refuses to let the application start. On PostgreSQL it requires:

* `alembic_version` to hold exactly one row, equal to this code's single head
  (`EXPECTED_HEAD`) — proof that the migrations ran and certified the schema;
* `booking_attempt_events` to be EXACTLY the canonical fingerprint of migration
  `20261015_booking_attempt_events` (columns with defaults, CHECKs by deparse, UNIQUE, indexes,
  no foreign key, the append-only trigger and function and no other trigger, rule or policy);
* `thread_revisions.notification_sent_at` to be EXACTLY the column of migration
  `20261016_notification_sent_at` — nullable `timestamp with time zone`, no default, not
  identity, not generated, with or without its marker — on a table with no user trigger, rule
  or row security. A default would make every new booking read as already e-mailed.

It reads the catalogue only. It never creates, alters, repairs or adopts anything: it is not
a second migration engine, and it fingerprints nothing else (the rest of the schema is the
migrations' responsibility). The literals below are this head's own copy; a test pins them to
the migrations' and to a hand-written DDL oracle.

On SQLite (tests only, built by `create_all`, no `alembic_version`) only the column's presence
is checked. The dialect decides the branch, never a flag.
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from .schema_fingerprint import (SchemaShapeRefused, column_fingerprint, differences,
                                 function_fingerprint, require_certified_server,
                                 table_fingerprint)

logger = logging.getLogger(__name__)

#: The single Alembic head this code requires (a test pins it to the script directory).
EXPECTED_HEAD = "20261016_notification_sent_at"


class DeclaredSchemaMissing(RuntimeError):
    """The database is not at the schema this application declares."""


def _col(name, type_, not_null, default=None):
    return (name, type_, not_null, "", "", True, default, None, None)


def _btree(name, column, opclass, collation="", unique=False, primary=False):
    return (name, "btree", unique, primary, False, True, True, 1, 1, (column,), "[0:0]={0}",
            (opclass,), (collation,), None, None, None)


def _check(name, column, values):
    arr = ", ".join(f"'{v}'::character varying" for v in values)
    return (name, "c", (column,), False, False, True, False, True,
            f"CHECK ((({column})::text = ANY ((ARRAY[{arr}])::text[])))")


_EVENTS_CANONICAL = {
    "table": (("relkind", "r"), ("persistence", "p"), ("owned_by_current_user", True),
              ("row_security", False), ("force_row_security", False), ("partition", False),
              ("typed", True), ("default_tablespace", True), ("options", None),
              ("comment", None), ("inherits_from", 0), ("inherited_by", 0)),
    "columns": [
        _col("id", "bigint", True, "nextval('booking_attempt_events_id_seq'::regclass)"),
        _col("event_uid", "character varying(40)", True),
        _col("occurred_at", "timestamp with time zone", True, "now()"),
        _col("source", "character varying(32)", True),
        _col("stage", "character varying(16)", True),
        _col("outcome", "character varying(16)", True),
        _col("reason", "character varying(64)", True),
        _col("command_key_hash", "character varying(16)", False),
        _col("attempt_ref_hash", "character varying(16)", False),
        _col("hist_thread_id", "integer", False),
        _col("hist_offer_id", "integer", False),
        _col("hist_revision_id", "integer", False),
        _col("hist_thread_revision_id", "integer", False),
    ],
    "sequences": [("id", "booking_attempt_events_id_seq", "a", "bigint", 1, 1, 1,
                   9223372036854775807, 1, False)],
    "constraints": [
        ("booking_attempt_events_event_uid_key", "u", ("event_uid",), False, False, True, True,
         True, "UNIQUE (event_uid)"),
        ("booking_attempt_events_pkey", "p", ("id",), False, False, True, True, True,
         "PRIMARY KEY (id)"),
        _check("ck_booking_attempt_events_outcome", "outcome",
               ("REFUSED", "RETRYABLE", "CONTENDED", "INTERNAL", "FAILED", "HELD")),
        _check("ck_booking_attempt_events_stage", "stage",
               ("PRE_EXECUTE", "AUTHORITY", "FINALISE", "NOTIFICATION")),
    ],
    "referenced_by": [],
    "indexes": [
        _btree("booking_attempt_events_event_uid_key", "event_uid", "text_ops", "default",
               unique=True),
        _btree("booking_attempt_events_pkey", "id", "int8_ops", unique=True, primary=True),
        _btree("ix_booking_attempt_events_occurred", "occurred_at", "timestamptz_ops"),
        _btree("ix_booking_attempt_events_thread", "hist_thread_id", "int4_ops"),
        _btree("ix_booking_attempt_events_thread_revision", "hist_thread_revision_id",
               "int4_ops"),
    ],
    "triggers": [("trg_booking_attempt_events_append_only", 27, "O",
                  "booking_attempt_events_append_only", True, 0, "{}", None, True, False,
                  False, None, None)],
    "rules": [],
    "policies": [],
}
_EVENTS_FUNCTION = [("booking_attempt_events_append_only", "plpgsql", "trigger", 0, "f", False,
                     "v", False, False, None, True, "348ed4cacd362157761a7ef24ec208da")]
_NOTIFICATION_COLUMN = (("type", "timestamp with time zone"), ("not_null", False),
                        ("identity", ""), ("generated", ""), ("default_collation", True),
                        ("default", None), ("comment", None), ("options", None))
#: The column as adopted (no comment) or as added (the 20261016 marker): nothing else.
_NOTIFICATION_PERMITTED = (
    _NOTIFICATION_COLUMN,
    tuple((k, "created-by:20261016_notification_sent_at" if k == "comment" else v)
          for k, v in _NOTIFICATION_COLUMN),
)


def _postgres_problems(conn) -> list[str]:
    problems: list[str] = []
    try:
        require_certified_server(conn, "schema preflight")
    except SchemaShapeRefused as exc:
        return [str(exc)]
    if conn.execute(text("SELECT to_regclass('alembic_version')")).scalar() is None:
        problems.append(f"alembic_version absent (expected exactly {EXPECTED_HEAD})")
    else:
        heads = conn.execute(text("SELECT version_num FROM alembic_version ORDER BY 1")
                             ).scalars().all()
        if heads != [EXPECTED_HEAD]:
            problems.append(f"Alembic version is {heads!r}, expected exactly "
                            f"[{EXPECTED_HEAD!r}]")
    events = differences(_EVENTS_CANONICAL, table_fingerprint(conn, "booking_attempt_events"))
    events += differences({"function": _EVENTS_FUNCTION},
                          {"function": function_fingerprint(
                              conn, "booking_attempt_events_append_only")})
    if events:
        problems.append("booking_attempt_events is not the canonical shape of migration "
                        "20261015_booking_attempt_events: " + "; ".join(events))
    col = column_fingerprint(conn, "thread_revisions", "notification_sent_at")
    if col is None or col["column"] is None:
        problems.append("thread_revisions.notification_sent_at "
                        "(migration 20261016_notification_sent_at) absent")
    else:
        bad = [] if col["column"] in _NOTIFICATION_PERMITTED else differences(
            {"column": _NOTIFICATION_COLUMN}, {"column": col["column"]})
        bad += differences({"table_triggers": [], "table_rules": [],
                            "table_row_security": False},
                           {k: col[k] for k in ("table_triggers", "table_rules",
                                                "table_row_security")})
        if bad:
            problems.append("thread_revisions.notification_sent_at is not the canonical shape "
                            "of migration 20261016_notification_sent_at: " + "; ".join(bad))
    return problems


def missing_declared_booking_schema(engine) -> list[str]:
    """What the declared schema requires and the database lacks or contradicts (empty when
    complete). Read-only."""
    if engine.dialect.name == "postgresql":
        with engine.connect() as conn:
            try:
                return _postgres_problems(conn)
            finally:
                conn.rollback()
    missing: list[str] = []
    insp = inspect(engine)
    if "thread_revisions" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("thread_revisions")}
        if "notification_sent_at" not in cols:
            missing.append("thread_revisions.notification_sent_at "
                           "(migration 20261016_notification_sent_at)")
    return missing


def require_declared_booking_schema(engine) -> None:
    missing = missing_declared_booking_schema(engine)
    if missing:
        logger.critical("SCHEMA_PREFLIGHT_FAILED — run `alembic upgrade head` before starting "
                        "this application. Missing: %s", "; ".join(missing))
        raise DeclaredSchemaMissing(
            "database schema is behind this application; run the migrations first. Missing: "
            + "; ".join(missing))
