"""Add pending_location_proposal to whatsapp_thread_states — L4.7W2-F1.

An APPROXIMATE locality recovered from corrupted speech is a PROPOSAL, never a canonical
value. It is held here as "zone_group||zone_detail||cycle_id" until the customer confirms
it. The cycle id is embedded so a proposal cannot be confirmed after a cycle reset.

Revision ID: 20260906_pending_location_proposal
"""
from alembic import op
import sqlalchemy as sa

revision: str = "20260906_pending_location_proposal"
down_revision: str = "20260901_l4_1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_thread_states",
        sa.Column("pending_location_proposal", sa.String(length=160), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_thread_states", "pending_location_proposal")
