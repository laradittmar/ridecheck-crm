"""Add whatsapp_thread_states.pending_correction — L4.7W5-GATE3-G3-7F Commit 3.

Additive, nullable, reversible. One JSONB column on a table that already holds this
thread's pending proposals (`pending_fuzzy_catalog_key`, `pending_location_proposal`,
`pending_turn_evidence_text`), which is the point: one row per thread means at most one
outstanding correction can exist, and that cardinality is what makes a generic affirmation
unambiguous rather than a guess.

It is PROPOSED state. Nothing reads it as canonical customer or candidate truth —
`whatsapp_thread_candidates` remains the source of truth and `_apply_candidate` remains its
only writer. An image rollback therefore leaves the column harmlessly populated and unread:
the writer is behind `PROVISIONAL_CORRECTION_ENABLED`, which defaults false.

Revision ID: 20261005_pending_correction
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261005_pending_correction"
down_revision: str = "20260918_hybrid_traces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_thread_states",
        sa.Column("pending_correction", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=True),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_thread_states", "pending_correction")
