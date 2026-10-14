"""Convergence Phase 2 — a terminal DETACHED state for booking commands (RV03RV-05).

A tester reset deletes the tester's bookings, and the ledger's two foreign keys
(`revision_id`, `thread_revision_id`) are `ON DELETE SET NULL`, so the command row survives —
but until now it still answered `IDEMPOTENT_REPLAY / ALREADY_COMMITTED` with `occupying=True`
and the original slot for a booking that no longer exists, and `lead_id`, `thread_id` and
`profesional_id` (no foreign key) dangled at deleted ids that a reseeded tester may reuse.

The ledger had no command-lifecycle column to strengthen — `ack_status` is the
acknowledgement's state, not the command's — so this adds one, plus what a detachment must
record:

    command_state      'COMMITTED' (every existing and every new row) | 'DETACHED' (terminal)
    detached_at        when
    detached_reason    why (e.g. TESTER_RESET)
    detached_history   historical, non-authoritative JSON: the slot and ids before the reset

Two CHECK constraints make the shape structural rather than conventional: a DETACHED row has
no live pointer, no reusable id, no slot and is not occupying; a COMMITTED row has no
detachment fields. One PostgreSQL trigger makes DETACHED terminal: no UPDATE may move a row
out of it.

Additive only. No existing value changes (the server default backfills 'COMMITTED'), no
professional is created or modified, no legacy revision is backfilled, the SB1 exclusion
constraint and the partial unique offer index are untouched, and `booking_attempt_events`
(convergence Phase 4) is NOT created here.

Reversibility: `downgrade()` drops exactly what `upgrade()` added — when no row is DETACHED.
With a DETACHED row present it REFUSES: dropping the state would silently turn a terminal,
non-replayable command back into a replayable one (RV03RV-05 reopened). Resolve those rows
deliberately first.

Revision ID: 20261014_ledger_detached_state
"""
from alembic import op
import sqlalchemy as sa

revision: str = "20261014_ledger_detached_state"
down_revision: str = "20261014_converge_track_heads"
branch_labels = None
depends_on = None

_SHAPE = (
    "(command_state = 'COMMITTED' AND detached_at IS NULL AND detached_reason IS NULL "
    "AND detached_history IS NULL) OR "
    "(command_state = 'DETACHED' AND detached_at IS NOT NULL "
    "AND detached_reason IS NOT NULL AND revision_id IS NULL "
    "AND thread_revision_id IS NULL AND lead_id IS NULL AND thread_id IS NULL "
    "AND profesional_id IS NULL AND slot_start IS NULL AND slot_end IS NULL "
    "AND occupying = false)"
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("convergence Phase 2 is PostgreSQL-only")
    op.add_column("booking_commands", sa.Column(
        "command_state", sa.String(16), nullable=False, server_default=sa.text("'COMMITTED'")))
    op.add_column("booking_commands", sa.Column(
        "detached_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("booking_commands", sa.Column("detached_reason", sa.String(64), nullable=True))
    op.add_column("booking_commands", sa.Column("detached_history", sa.Text(), nullable=True))
    op.create_check_constraint("ck_booking_commands_command_state", "booking_commands",
                               "command_state IN ('COMMITTED', 'DETACHED')")
    op.create_check_constraint("ck_booking_commands_detached_shape", "booking_commands", _SHAPE)
    op.execute("""
        CREATE FUNCTION booking_commands_detached_is_terminal() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.command_state = 'DETACHED' AND NEW.command_state IS DISTINCT FROM 'DETACHED' THEN
            RAISE EXCEPTION 'booking command % is DETACHED, which is terminal', OLD.id
              USING ERRCODE = '23514', CONSTRAINT = 'booking_commands_detached_terminal';
          END IF;
          RETURN NEW;
        END $$""")
    op.execute("""
        CREATE TRIGGER trg_booking_commands_detached_terminal
          BEFORE UPDATE OF command_state ON booking_commands
          FOR EACH ROW EXECUTE FUNCTION booking_commands_detached_is_terminal()""")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("convergence Phase 2 is PostgreSQL-only")
    detached = bind.execute(sa.text(
        "SELECT count(*) FROM booking_commands WHERE command_state = 'DETACHED'")).scalar()
    if detached:
        raise RuntimeError(
            f"refusing to downgrade: {detached} booking command(s) are DETACHED; dropping the "
            "state would make them replayable again (RV03RV-05)")
    # IF EXISTS: a `create_all` + `stamp` schema has the columns and checks but no trigger.
    op.execute("DROP TRIGGER IF EXISTS trg_booking_commands_detached_terminal ON booking_commands")
    op.execute("DROP FUNCTION IF EXISTS booking_commands_detached_is_terminal()")
    op.drop_constraint("ck_booking_commands_detached_shape", "booking_commands", type_="check")
    op.drop_constraint("ck_booking_commands_command_state", "booking_commands", type_="check")
    op.drop_column("booking_commands", "detached_history")
    op.drop_column("booking_commands", "detached_reason")
    op.drop_column("booking_commands", "detached_at")
    op.drop_column("booking_commands", "command_state")
