"""L4.1: add meta_http_status + meta_error_payload to whatsapp_messages.

Captures Meta API transport errors so delivery failures survive container
recreation without relying on ephemeral Docker logs.

Revision ID: 20260901_l4_1
Revises: 20260831_wild01_dedup_causal_inbound
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260901_l4_1"
down_revision = "20260831_wild01_dedup_causal_inbound"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_messages",
        sa.Column("meta_http_status", sa.Integer(), nullable=True),
    )
    op.add_column(
        "whatsapp_messages",
        sa.Column("meta_error_payload", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_messages", "meta_error_payload")
    op.drop_column("whatsapp_messages", "meta_http_status")
