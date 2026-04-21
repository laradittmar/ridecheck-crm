"""add whatsapp message media fields

Revision ID: 20260420_add_whatsapp_message_media_fields
Revises: 20260420_add_revision_appointment_approval_fields
Create Date: 2026-04-20
"""

from alembic import op


revision = "20260420_add_whatsapp_message_media_fields"
down_revision = "20260420_add_revision_appointment_approval_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE whatsapp_messages ADD COLUMN message_type VARCHAR(20) DEFAULT 'text'")
    op.execute("ALTER TABLE whatsapp_messages ADD COLUMN media_id VARCHAR(191)")


def downgrade() -> None:
    op.execute("ALTER TABLE whatsapp_messages DROP COLUMN media_id")
    op.execute("ALTER TABLE whatsapp_messages DROP COLUMN message_type")
