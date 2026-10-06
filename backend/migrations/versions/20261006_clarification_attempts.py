"""Add whatsapp_clarification_attempts — G3-7F clarification lifecycle.

The clarification-bounding invariant failed three times as application logic because the
fact it depends on was written from four places across three authorities and could be
cleared by completion, by handoff and by a withdrawal loop. This table makes the invariant a
database constraint instead:

    UNIQUE (thread_id, cycle_key, family)

Reservation is an INSERT. A conflict is not an error to recover from — it IS the answer to
"has this family already had its one attempt in this cycle?", and it holds against every
caller, present and future, which is exactly what application-level checks could not do.

The row's EXISTENCE is immutable attempt history; `status` advances along the lifecycle.
Nothing deletes rows in normal operation. `vehicle_fallback_flow_sent` /
`location_fallback_flow_sent` keep their one legitimate meaning — a Flow is currently
outstanding — and may be cleared freely, because availability no longer lives there.

Additive and reversible: it creates one table and touches no existing column or row. An
image rollback leaves the table unread, and the downgrade drops only what this created.

Revision ID: 20261006_clarification_attempts
"""
from alembic import op
import sqlalchemy as sa

revision: str = "20261006_clarification_attempts"
down_revision: str = "20261005_pending_correction"
branch_labels = None
depends_on = None

#: Mirrors `ClarificationFamily` / `AttemptStatus` in
#: `app/services/clarification_attempt.py`. Kept as literal SQL so the constraint is
#: readable in the database itself, where an operator will meet it.
_FAMILIES = "('VEHICLE', 'LOCATION')"
_STATUSES = (
    "('RESERVED', 'DISPATCHED', 'COMPLETED_RESOLVED', 'COMPLETED_UNRESOLVED', "
    "'INVALID_COMPLETION', 'DISPATCH_BLOCKED', 'DISPATCH_FAILED', 'HANDED_OFF')"
)


def upgrade() -> None:
    op.create_table(
        "whatsapp_clarification_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("thread_id", sa.Integer(),
                  sa.ForeignKey("whatsapp_threads.id"), nullable=False),
        # COALESCE(state.current_cycle_start_message_db_id, 0). 0 is the thread's initial,
        # never-reset cycle; a real message id appears only after an operator-authorized
        # cycle reset writes one. Integer, never a timestamp.
        sa.Column("cycle_key", sa.Integer(), nullable=False),
        sa.Column("family", sa.String(16), nullable=False),
        # Deterministic UUIDv5 over (namespace, thread, cycle, family); 36 characters, which
        # fits `whatsapp_messages.correlation_id` / `security_events.correlation_id`
        # (String(36)) should it ever be carried there.
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outbound_message_id", sa.Integer(),
                  sa.ForeignKey("whatsapp_messages.id"), nullable=True),
        sa.Column("wa_message_id", sa.String(128), nullable=True),
        sa.Column("dispatch_result", sa.String(32), nullable=True),
        sa.Column("completion_result", sa.String(32), nullable=True),
        sa.Column("resolved_family", sa.Boolean(), nullable=True),
        sa.Column("terminal_reason", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        # ── the business invariant ────────────────────────────────────────────
        sa.UniqueConstraint("thread_id", "cycle_key", "family",
                            name="uq_clarification_attempt_thread_cycle_family"),
        sa.UniqueConstraint("idempotency_key",
                            name="uq_clarification_attempt_idempotency_key"),
        sa.CheckConstraint(f"family IN {_FAMILIES}",
                           name="ck_clarification_attempt_family"),
        sa.CheckConstraint(f"status IN {_STATUSES}",
                           name="ck_clarification_attempt_status"),
    )
    op.create_index("ix_clarification_attempt_thread_cycle",
                    "whatsapp_clarification_attempts", ["thread_id", "cycle_key"])


def downgrade() -> None:
    op.drop_index("ix_clarification_attempt_thread_cycle",
                  table_name="whatsapp_clarification_attempts")
    op.drop_table("whatsapp_clarification_attempts")
