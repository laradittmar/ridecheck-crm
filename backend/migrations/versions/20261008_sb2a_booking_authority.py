"""SB2A — booking authority: idempotency ledger, row version, eligibility, lock hooks.

SB1 made overlap structurally impossible per professional. SB2A adds what one canonical
booking command needs to be correct around that constraint, and nothing else:

booking_commands
    The idempotency and acknowledgement ledger. One row per COMMITTED booking command that
    carried an idempotency key, written in the SAME transaction as the booking, so "a
    booking exists" and "this submission already produced it" can never disagree.
    `UNIQUE (idempotency_key)` is the final authority on duplicates. Acknowledgement state
    (`ack_status`) is separate from commit state: a booking is committed or not; its
    acknowledgement is pending, being sent, sent or failed, and recoverable on replay.
    `revision_id` / `thread_revision_id` link the CRM and conversation rows of one booking —
    the domain model has no FK between them, so this is the only durable pairing.

revisions.booking_version
    Optimistic concurrency for reschedules and status changes. The command bumps it; a BEFORE
    UPDATE trigger bumps it for any writer that changes the slot, durations, status or
    professional without doing so, so a stale form can never overwrite a newer booking even
    through a writer that has not been cut over yet.

profesionales.is_active
    Eligibility. An inactive professional can neither be named nor serve as the launch
    default for a new booking (enforced by the command; defaults TRUE, so no row changes).

thread_revisions.status
    Widened with 'cancelled' and 'reschedule_pending' so cancelling or marking a linked
    booking for rescheduling vacates its conversation-side twin in the same transaction —
    `schedule.py` counts booked/completed thread revisions as occupying.

calendar_exceptions lock trigger
    Any insert, update or delete of an exception takes the exclusive per-date advisory lock
    the booking command takes in SHARED mode, so an exception and a booking on the same date
    serialize — even when the exception is written by raw SQL.

Additive and reversible. Downgrade refuses (and changes nothing) while any thread revision
uses a status SB2A introduced, rather than silently rewriting history.

Revision ID: 20261008_sb2a_booking_authority
"""
from alembic import op
import sqlalchemy as sa

revision: str = "20261008_sb2a_booking_authority"
down_revision: str = "20261007_sb1_booking_repr"
branch_labels = None
depends_on = None

#: Advisory-lock namespace for per-date locks. Professional ids are positive, so the
#: two-integer key (-1, YYYYMMDD) can never collide with a (professional_id, YYYYMMDD) key.
DATE_LOCK_NAMESPACE = -1

_SOURCES = "('WHATSAPP_FLOW', 'CRM', 'API', 'CONVERSATION_ENGINE', 'OTHER')"
_OPERATIONS = ("('CREATE', 'RESCHEDULE', 'CANCEL', 'MARK_FOR_RESCHEDULE', 'SET_STATUS', "
               "'UNSCHEDULE', 'REQUEST_MANUAL_SCHEDULING')")
_ACK = "('NOT_APPLICABLE', 'PENDING', 'SENDING', 'SENT', 'FAILED')"
_TR_STATUSES_OLD = "('draft', 'collecting_data', 'booked', 'completed', 'provisional')"
_TR_STATUSES_NEW = ("('draft', 'collecting_data', 'booked', 'completed', 'provisional', "
                    "'cancelled', 'reschedule_pending')")


def _require_postgresql() -> None:
    dialect = op.get_bind().dialect.name
    if dialect != "postgresql":
        raise RuntimeError(f"SB2A booking authority is PostgreSQL-only (got {dialect})")


def upgrade() -> None:
    _require_postgresql()

    op.add_column("profesionales", sa.Column(
        "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")))

    op.add_column("revisions", sa.Column(
        "booking_version", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.create_check_constraint("ck_revisions_booking_version_nonneg", "revisions",
                               "booking_version >= 0")
    op.execute("""
        CREATE FUNCTION sb2a_revisions_bump_booking_version() RETURNS trigger
        LANGUAGE plpgsql AS $fn$
        BEGIN
            IF NEW.booking_version = OLD.booking_version AND (
                   NEW.turno_fecha IS DISTINCT FROM OLD.turno_fecha
                OR NEW.turno_hora IS DISTINCT FROM OLD.turno_hora
                OR NEW.service_minutes IS DISTINCT FROM OLD.service_minutes
                OR NEW.buffer_minutes IS DISTINCT FROM OLD.buffer_minutes
                OR NEW.estado_revision IS DISTINCT FROM OLD.estado_revision
                OR NEW.profesional_id IS DISTINCT FROM OLD.profesional_id) THEN
                NEW.booking_version := OLD.booking_version + 1;
            END IF;
            RETURN NEW;
        END
        $fn$
    """)
    op.execute("""
        CREATE TRIGGER trg_revisions_sb2a_booking_version
          BEFORE UPDATE ON revisions
          FOR EACH ROW EXECUTE FUNCTION sb2a_revisions_bump_booking_version()
    """)

    op.drop_constraint("ck_thread_revisions_status", "thread_revisions", type_="check")
    op.create_check_constraint("ck_thread_revisions_status", "thread_revisions",
                               f"status IN {_TR_STATUSES_NEW}")

    op.execute(f"""
        CREATE FUNCTION sb2a_calendar_exception_lock() RETURNS trigger
        LANGUAGE plpgsql AS $fn$
        BEGIN
            IF TG_OP IN ('UPDATE', 'DELETE') THEN
                PERFORM pg_advisory_xact_lock({DATE_LOCK_NAMESPACE},
                                              to_char(OLD.exception_date, 'YYYYMMDD')::int);
            END IF;
            IF TG_OP IN ('INSERT', 'UPDATE') THEN
                PERFORM pg_advisory_xact_lock({DATE_LOCK_NAMESPACE},
                                              to_char(NEW.exception_date, 'YYYYMMDD')::int);
                RETURN NEW;
            END IF;
            RETURN OLD;
        END
        $fn$
    """)
    op.execute("""
        CREATE TRIGGER trg_calendar_exceptions_sb2a_lock
          BEFORE INSERT OR UPDATE OR DELETE ON calendar_exceptions
          FOR EACH ROW EXECUTE FUNCTION sb2a_calendar_exception_lock()
    """)

    op.create_table(
        "booking_commands",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("revision_id", sa.Integer(),
                  sa.ForeignKey("revisions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("thread_revision_id", sa.Integer(),
                  sa.ForeignKey("thread_revisions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("thread_id", sa.Integer(), nullable=True),
        sa.Column("profesional_id", sa.Integer(), nullable=True),
        sa.Column("slot_start", sa.DateTime(), nullable=True),
        sa.Column("slot_end", sa.DateTime(), nullable=True),
        sa.Column("occupying", sa.Boolean(), nullable=False),
        sa.Column("booking_version", sa.Integer(), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("actor", sa.String(120), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("ack_status", sa.String(16), nullable=False,
                  server_default=sa.text("'NOT_APPLICABLE'")),
        sa.Column("ack_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("ack_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replay_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_replayed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_booking_commands_idempotency_key"),
        sa.CheckConstraint(f"source IN {_SOURCES}", name="ck_booking_commands_source"),
        sa.CheckConstraint(f"operation IN {_OPERATIONS}", name="ck_booking_commands_operation"),
        sa.CheckConstraint(f"ack_status IN {_ACK}", name="ck_booking_commands_ack_status"),
    )
    op.create_index("ix_booking_commands_revision_id", "booking_commands", ["revision_id"])
    op.create_index("ix_booking_commands_thread_revision_id", "booking_commands",
                    ["thread_revision_id"])


def downgrade() -> None:
    _require_postgresql()
    # Fail closed rather than rewrite history: SB2A-only statuses would violate the old CHECK.
    op.execute("""
        DO $chk$
        BEGIN
            IF EXISTS (SELECT 1 FROM thread_revisions
                       WHERE status IN ('cancelled', 'reschedule_pending')) THEN
                RAISE EXCEPTION 'SB2A downgrade refused: thread_revisions use SB2A statuses';
            END IF;
        END
        $chk$
    """)
    op.drop_index("ix_booking_commands_thread_revision_id", table_name="booking_commands")
    op.drop_index("ix_booking_commands_revision_id", table_name="booking_commands")
    op.drop_table("booking_commands")

    op.execute("DROP TRIGGER trg_calendar_exceptions_sb2a_lock ON calendar_exceptions")
    op.execute("DROP FUNCTION sb2a_calendar_exception_lock()")

    op.drop_constraint("ck_thread_revisions_status", "thread_revisions", type_="check")
    op.create_check_constraint("ck_thread_revisions_status", "thread_revisions",
                               f"status IN {_TR_STATUSES_OLD}")

    op.execute("DROP TRIGGER trg_revisions_sb2a_booking_version ON revisions")
    op.execute("DROP FUNCTION sb2a_revisions_bump_booking_version()")
    op.drop_constraint("ck_revisions_booking_version_nonneg", "revisions", type_="check")
    op.drop_column("revisions", "booking_version")

    op.drop_column("profesionales", "is_active")
