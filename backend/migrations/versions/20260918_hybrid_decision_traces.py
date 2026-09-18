"""Add hybrid_decision_traces — L4.7W5 Gate 1, `hybrid-decision-trace/1.0`.

Additive and observational. No existing table, column, constraint or index is touched, so an
image rollback leaves the table harmlessly present and unread: nothing in the conversation
path queries it, and the writer is behind its own flag.

The customer's message bodies are deliberately absent. `payload.ordered_message_ids` points
at `whatsapp_messages`, which already stores them under the existing retention and masking
policy; copying them here would duplicate PII and buy no evidence the hash does not already
provide.

Revision ID: 20260918_hybrid_traces
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260918_hybrid_traces"
down_revision: str = "20260906_pending_location_proposal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hybrid_decision_traces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("turn_id", sa.String(length=64), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=True),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("deployment_sha", sa.String(length=40), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_kind", sa.String(length=32), nullable=True),
        sa.Column("classification", sa.String(length=32), nullable=True),
        sa.Column("semantic_status", sa.String(length=16), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_hdt_turn_id", "hybrid_decision_traces", ["turn_id"], unique=True)
    op.create_index("ix_hdt_thread_id", "hybrid_decision_traces", ["thread_id"])
    op.create_index("ix_hdt_lead_id", "hybrid_decision_traces", ["lead_id"])
    op.create_index("ix_hdt_deployment_sha", "hybrid_decision_traces", ["deployment_sha"])
    op.create_index("ix_hdt_input_hash", "hybrid_decision_traces", ["input_hash"])
    op.create_index("ix_hdt_result_kind", "hybrid_decision_traces", ["result_kind"])
    op.create_index("ix_hdt_classification", "hybrid_decision_traces", ["classification"])
    op.create_index("ix_hdt_semantic_status", "hybrid_decision_traces", ["semantic_status"])
    op.create_index("ix_hdt_created_at", "hybrid_decision_traces", ["created_at"])


def downgrade() -> None:
    op.drop_table("hybrid_decision_traces")
