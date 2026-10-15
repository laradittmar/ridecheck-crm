"""Convergence Phase 4 — `booking_attempt_events`: durable, FK-free, append-only evidence.

A booking attempt that produced no committed command — a refusal, a contention, an internal
failure — left no durable trace outside CE's own telemetry (P2R-10 / R3RV-02): the authority
rolls its transaction back entirely, and the Data Exchange endpoint and operator commands have
no telemetry at all. An operator booking e-mail that was not delivered left nothing either
(SB2B-RV-04). This table is that evidence, and only evidence:

    id                        identity
    event_uid                 the idempotency rule (UNIQUE): one logical attempt, one row
    occurred_at               when
    source / stage / outcome  typed codes (two CHECK constraints)
    reason                    a typed code, never prose
    command_key_hash          16-hex SHA-256 prefix of the ledger key — never the key
    attempt_ref_hash          16-hex SHA-256 prefix of the attempt's identity
    hist_*                    HISTORICAL, NON-AUTHORITATIVE scalar ids (thread, offer,
                              revision, thread revision) — never a foreign key

No foreign key, by design: an FK to an offer would take `FOR KEY SHARE` on a row a contender
holds `FOR UPDATE`, and an FK to any business row would block a tester reset. Append-only: one
trigger refuses every UPDATE and DELETE (TRUNCATE, a table-level administrative operation, is
not a row event and is not blocked).

Additive only. Nothing existing changes; no business row is read or written; no professional
is created; `DETACHED`, the exclusion constraint and the partial offer index are untouched.

Reversibility: `downgrade()` drops the table, its trigger and function — ONLY while the table
is EMPTY. With evidence rows present it REFUSES: dropping them is audit-data loss and cannot
be undone. Export or deliberately discard the rows first. Nothing else is touched, so a
downgrade restores the previous schema exactly.

Revision ID: 20261015_booking_attempt_events
"""
from alembic import op
import sqlalchemy as sa

revision: str = "20261015_booking_attempt_events"
down_revision: str = "20261014_ledger_detached_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("convergence Phase 4 is PostgreSQL-only")
    op.create_table(
        "booking_attempt_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("event_uid", sa.String(40), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("command_key_hash", sa.String(16), nullable=True),
        sa.Column("attempt_ref_hash", sa.String(16), nullable=True),
        sa.Column("hist_thread_id", sa.Integer(), nullable=True),
        sa.Column("hist_offer_id", sa.Integer(), nullable=True),
        sa.Column("hist_revision_id", sa.Integer(), nullable=True),
        sa.Column("hist_thread_revision_id", sa.Integer(), nullable=True),
        sa.UniqueConstraint("event_uid", name="booking_attempt_events_event_uid_key"),
        sa.CheckConstraint(
            "stage IN ('PRE_EXECUTE', 'AUTHORITY', 'FINALISE', 'NOTIFICATION')",
            name="ck_booking_attempt_events_stage"),
        sa.CheckConstraint(
            "outcome IN ('REFUSED', 'RETRYABLE', 'CONTENDED', 'INTERNAL', 'FAILED', 'HELD')",
            name="ck_booking_attempt_events_outcome"),
    )
    op.create_index("ix_booking_attempt_events_occurred", "booking_attempt_events",
                    ["occurred_at"])
    op.create_index("ix_booking_attempt_events_thread", "booking_attempt_events",
                    ["hist_thread_id"])
    op.create_index("ix_booking_attempt_events_thread_revision", "booking_attempt_events",
                    ["hist_thread_revision_id"])
    op.execute("""
        CREATE FUNCTION booking_attempt_events_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'booking_attempt_events is append-only (% refused)', TG_OP
            USING ERRCODE = '23514', CONSTRAINT = 'booking_attempt_events_append_only';
        END $$""")
    op.execute("""
        CREATE TRIGGER trg_booking_attempt_events_append_only
          BEFORE UPDATE OR DELETE ON booking_attempt_events
          FOR EACH ROW EXECUTE FUNCTION booking_attempt_events_append_only()""")


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT count(*) FROM booking_attempt_events")).scalar()
    if rows:
        raise RuntimeError(
            f"booking_attempt_events holds {rows} evidence row(s); dropping them is audit-data "
            "loss and cannot be undone. Export or deliberately discard them first.")
    # IF EXISTS: a schema built by `create_all` and stamped (the project's test bootstrap)
    # has the table and indexes but not the trigger, and must still downgrade cleanly.
    op.execute("DROP TRIGGER IF EXISTS trg_booking_attempt_events_append_only "
               "ON booking_attempt_events")
    op.execute("DROP FUNCTION IF EXISTS booking_attempt_events_append_only()")
    op.execute("DROP INDEX IF EXISTS ix_booking_attempt_events_thread_revision")
    op.execute("DROP INDEX IF EXISTS ix_booking_attempt_events_thread")
    op.execute("DROP INDEX IF EXISTS ix_booking_attempt_events_occurred")
    op.drop_table("booking_attempt_events")
