"""Convergence Phase 4-R1 (P4RV-04) — declare `thread_revisions.notification_sent_at`.

M18's operator booking e-mail is deduplicated by, and the operator notification surface reads
its SENT leg from, `thread_revisions.notification_sent_at`. No schema source declared it: the
column existed only because an application start-up hook ran
`ALTER TABLE thread_revisions ADD COLUMN IF NOT EXISTS notification_sent_at TIMESTAMPTZ`. In a
migrated database the application had not started on, the M18 dedup read aborted the turn's
transaction (prices lost from the e-mail, the e-mail never marked and so sent twice on a
redelivery, a false CE_SYNC_INCOMPLETE, every booking reported "no enviado"). The ORM model
now declares the column and this migration owns it; the start-up DDL is removed.

Upgrade — strict one-time adoption (Phase 4 STRICT-SCHEMA-ADOPTION, Decision A). Under a
`SHARE ROW EXCLUSIVE` lock on `thread_revisions`, before any DDL, the column must be EXACTLY:
  * absent (a database the old start-up never touched) → added, nullable
    `timestamp with time zone`, no default, and marked as created by this revision with a
    column COMMENT                                                              (ADDED)
  * the old start-up's column (every deployed database): `timestamp with time zone`, nullable,
    no default, not identity, not generated, no comment → adopted exactly as it is; no value
    rewritten, no marker set (this revision did not create it)       (ADOPTED_LEGACY_COLUMN)
  * the column this revision adds (marker included) → verified, nothing changed
                                                                       (VERIFIED_CANONICAL)
and `thread_revisions` must carry no user trigger, no rewrite rule and no row security — any
of which could set or hide the value. Anything else — above all `DEFAULT now()`, which would
make every new booking read as already e-mailed — REFUSES the upgrade with the version and
the schema unchanged. The shape is the catalogue fingerprint of
`app.services.schema_fingerprint`, compared with the frozen literals below.

Downgrade — never erases evidence:
  * if ANY row holds a non-null `notification_sent_at` it REFUSES: those timestamps are the
    record that the team was told about a booking, and the e-mail's only dedup;
  * otherwise, a column THIS revision created (exactly the marked shape) is dropped, so a
    clean upgrade → downgrade → re-upgrade is exact; a column that pre-existed (exactly the
    legacy shape) is left in place, exactly as the upgrade found it; any other shape refuses.
  The owner-controlled procedure when evidence exists is in
  `docs/operations/RECOVERY_MANIFEST.md` §6.

Revision ID: 20261016_notification_sent_at
"""
from alembic import op
import sqlalchemy as sa

from app.services.schema_fingerprint import (SchemaShapeRefused, column_fingerprint,
                                             differences, require_certified_server)

revision: str = "20261016_notification_sent_at"
down_revision: str = "20261015_booking_attempt_events"
branch_labels = None
depends_on = None

#: The creation marker: present only on a column this revision added.
MARKER = "created-by:20261016_notification_sent_at"

# ── frozen fingerprints, measured on PostgreSQL 16.13 (immutable history: never edit) ──────
#: (type, NOT NULL, identity, generated, default collation, default, comment, options)
_LEGACY = (("type", "timestamp with time zone"), ("not_null", False), ("identity", ""),
           ("generated", ""), ("default_collation", True), ("default", None),
           ("comment", None), ("options", None))
_ADDED = tuple((k, MARKER if k == "comment" else v) for k, v in _LEGACY)
#: `thread_revisions` itself: nothing that could write or hide the value behind the column.
_TABLE_LEVEL = {"table_triggers": [], "table_rules": [], "table_row_security": False}


def _lock_and_read(bind):
    bind.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    bind.execute(sa.text("LOCK TABLE thread_revisions IN SHARE ROW EXCLUSIVE MODE"))
    return column_fingerprint(bind, "thread_revisions", "notification_sent_at")


def _refuse(why, problems):
    raise SchemaShapeRefused(
        f"{revision}: {why}. Refusing before any change — nothing was added, altered or "
        "dropped, no value was touched and the Alembic version is unchanged. Do not repair by "
        "script: capture this message for the owner (docs/operations/RECOVERY_MANIFEST.md §6). "
        "Differences: " + "; ".join(problems))


def _verify(bind, column) -> None:
    found = column_fingerprint(bind, "thread_revisions", "notification_sent_at")
    problems = differences({**_TABLE_LEVEL, "column": column}, found)
    if problems:
        _refuse("thread_revisions.notification_sent_at is not exactly canonical after the "
                "upgrade", problems)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("convergence Phase 4-R1 is PostgreSQL-only")
    require_certified_server(bind, revision)
    found = _lock_and_read(bind)
    if found is None:
        _refuse("thread_revisions is absent", ["object absent"])
    table_level = differences(_TABLE_LEVEL, {k: found[k] for k in _TABLE_LEVEL})
    if table_level:
        _refuse("thread_revisions carries a trigger, rule or row security that could set or "
                "hide notification_sent_at", table_level)
    column = found["column"]
    if column is None:
        op.execute("ALTER TABLE thread_revisions ADD COLUMN notification_sent_at "
                   "TIMESTAMP WITH TIME ZONE NULL")
        op.execute(f"COMMENT ON COLUMN thread_revisions.notification_sent_at IS '{MARKER}'")
        branch, expected = "ADDED", _ADDED
    elif column == _LEGACY:
        # Created by the old application start-up: adopted as it is, values untouched.
        branch, expected = "ADOPTED_LEGACY_COLUMN", _LEGACY
    elif column == _ADDED:
        branch, expected = "VERIFIED_CANONICAL", _ADDED
    else:
        _refuse("thread_revisions.notification_sent_at exists and is not exactly the legacy "
                "start-up column (timestamptz, nullable, no default, not identity, not "
                "generated, no comment)", differences({"column": _LEGACY}, {"column": column}))
    _verify(bind, expected)
    print(f"{revision}: {branch}", flush=True)


def downgrade() -> None:
    bind = op.get_bind()
    require_certified_server(bind, revision)
    found = _lock_and_read(bind)
    if found is None or found["column"] is None:
        return
    held = bind.execute(sa.text("SELECT count(*) FROM thread_revisions "
                                "WHERE notification_sent_at IS NOT NULL")).scalar()
    if held:
        raise RuntimeError(
            f"thread_revisions.notification_sent_at holds {held} delivery timestamp(s); "
            "dropping them is notification-evidence loss and removes the operator e-mail's "
            "only dedup. Downgrade refused — see docs/operations/RECOVERY_MANIFEST.md §6.")
    if found["column"] == _ADDED:
        op.execute("ALTER TABLE thread_revisions DROP COLUMN notification_sent_at")
    elif found["column"] != _LEGACY:
        raise SchemaShapeRefused(
            f"{revision}: downgrade refused — notification_sent_at is neither the column this "
            "revision added nor the legacy start-up column, so it is not touched. "
            + "; ".join(differences({"column": _ADDED}, {"column": found["column"]})))
    # Exactly the legacy shape: the column pre-dated this revision and stays.
