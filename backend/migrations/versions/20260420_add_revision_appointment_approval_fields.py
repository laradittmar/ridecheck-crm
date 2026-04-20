"""add revision appointment approval fields

Revision ID: 20260420_add_revision_appointment_approval_fields
Revises: 20260420_add_thread_revision_appointment_approval_fields
Create Date: 2026-04-20
"""

from alembic import op


revision = "20260420_add_revision_appointment_approval_fields"
down_revision = "20260420_add_thread_revision_appointment_approval_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE revisions ADD COLUMN appointment_approval_status VARCHAR(20)")
    op.execute("ALTER TABLE revisions ADD COLUMN appointment_approval_token VARCHAR(64)")
    op.execute("ALTER TABLE revisions ADD COLUMN appointment_approval_sent_at TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE revisions ADD COLUMN appointment_approved_at TIMESTAMP WITH TIME ZONE")
    op.create_unique_constraint(
        "uq_revisions_appointment_approval_token",
        "revisions",
        ["appointment_approval_token"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_revisions_appointment_approval_token",
        "revisions",
        type_="unique",
    )
    op.execute("ALTER TABLE revisions DROP COLUMN appointment_approved_at")
    op.execute("ALTER TABLE revisions DROP COLUMN appointment_approval_sent_at")
    op.execute("ALTER TABLE revisions DROP COLUMN appointment_approval_token")
    op.execute("ALTER TABLE revisions DROP COLUMN appointment_approval_status")
