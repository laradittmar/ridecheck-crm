"""add latest_inbound_wa_message_id to whatsapp_threads

Revision ID: 20260517_add_latest_inbound_to_whatsapp_threads
Revises: 20260420_add_whatsapp_message_media_fields
Create Date: 2026-05-17
"""

from alembic import op


revision = "20260517_add_latest_inbound_to_whatsapp_threads"
down_revision = "20260420_add_whatsapp_message_media_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE whatsapp_threads ADD COLUMN latest_inbound_wa_message_id VARCHAR(255)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE whatsapp_threads DROP COLUMN latest_inbound_wa_message_id"
    )
