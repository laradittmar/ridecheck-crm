"""Convergence Phase 4-R1 (P4RV-04) — declare `thread_revisions.notification_sent_at`.

M18's operator booking e-mail is deduplicated by, and the operator notification surface reads
its SENT leg from, `thread_revisions.notification_sent_at`. No schema source declared it: the
column existed only because an application start-up hook ran
`ALTER TABLE thread_revisions ADD COLUMN IF NOT EXISTS notification_sent_at TIMESTAMPTZ`. In a
migrated database the application had not started on, the M18 dedup read aborted the turn's
transaction (prices lost from the e-mail, the e-mail never marked and so sent twice on a
redelivery, a false CE_SYNC_INCOMPLETE, every booking reported "no enviado"). The ORM model
now declares the column and this migration owns it; the start-up DDL is removed.

Upgrade — converges on BOTH database shapes that exist:
  * column absent (a database the old start-up never touched): it is added, nullable
    `timestamp with time zone`, and marked as created by this revision (a column COMMENT);
  * column present because the old start-up created it (every deployed database): its type
    is verified to be `timestamp with time zone` and nullable, and it is left exactly as it is
    — no value rewritten, no marker set (this revision did not create it).
  Any other existing shape refuses the upgrade with an explicit error.

Downgrade — never erases evidence:
  * if ANY row holds a non-null `notification_sent_at` it REFUSES: those timestamps are the
    record that the team was told about a booking, and the e-mail's only dedup;
  * otherwise, a column THIS revision created (marker present) is dropped, so a clean
    upgrade → downgrade → re-upgrade is exact; a column that pre-existed (no marker) is left
    in place, exactly as the upgrade found it.
  The owner-controlled procedure when evidence exists is in
  `docs/operations/RECOVERY_MANIFEST.md` §6.

Revision ID: 20261016_notification_sent_at
"""
from alembic import op
import sqlalchemy as sa

revision: str = "20261016_notification_sent_at"
down_revision: str = "20261015_booking_attempt_events"
branch_labels = None
depends_on = None

#: The creation marker: present only on a column this revision added.
MARKER = "created-by:20261016_notification_sent_at"


def _shape(bind):
    return bind.execute(sa.text(
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'thread_revisions' "
        "AND column_name = 'notification_sent_at'")).first()


def _comment(bind):
    return bind.execute(sa.text(
        "SELECT col_description('thread_revisions'::regclass, a.attnum) FROM pg_attribute a "
        "WHERE a.attrelid = 'thread_revisions'::regclass "
        "AND a.attname = 'notification_sent_at' AND NOT a.attisdropped")).scalar()


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("convergence Phase 4-R1 is PostgreSQL-only")
    shape = _shape(bind)
    if shape is None:
        op.execute("ALTER TABLE thread_revisions ADD COLUMN notification_sent_at "
                   "TIMESTAMP WITH TIME ZONE NULL")
        op.execute(f"COMMENT ON COLUMN thread_revisions.notification_sent_at IS '{MARKER}'")
        return
    if tuple(shape) != ("timestamp with time zone", "YES"):
        raise RuntimeError(
            "thread_revisions.notification_sent_at exists with an unexpected shape "
            f"{tuple(shape)!r}; expected ('timestamp with time zone', 'YES'). Refusing to "
            "adopt it — reconcile it by hand first.")
    # Created by the old application start-up: adopted as it is, values untouched.


def downgrade() -> None:
    bind = op.get_bind()
    if _shape(bind) is None:
        return
    held = bind.execute(sa.text("SELECT count(*) FROM thread_revisions "
                                "WHERE notification_sent_at IS NOT NULL")).scalar()
    if held:
        raise RuntimeError(
            f"thread_revisions.notification_sent_at holds {held} delivery timestamp(s); "
            "dropping them is notification-evidence loss and removes the operator e-mail's "
            "only dedup. Downgrade refused — see docs/operations/RECOVERY_MANIFEST.md §6.")
    if _comment(bind) == MARKER:
        op.execute("ALTER TABLE thread_revisions DROP COLUMN notification_sent_at")
    # No marker: the column pre-dated this revision (the old start-up created it) and stays.
