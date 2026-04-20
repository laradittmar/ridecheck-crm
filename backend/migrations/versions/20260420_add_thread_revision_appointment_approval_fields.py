"""add thread revision appointment approval fields

Revision ID: 20260420_add_thread_revision_appointment_approval_fields
Revises: 20260412_add_excluded_phones_table
Create Date: 2026-04-20
"""

from alembic import op


revision = "20260420_add_thread_revision_appointment_approval_fields"
down_revision = "20260412_add_excluded_phones_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE thread_revisions ADD COLUMN appointment_approval_status VARCHAR(20)")
    op.execute("ALTER TABLE thread_revisions ADD COLUMN appointment_approval_token VARCHAR(64)")
    op.execute("ALTER TABLE thread_revisions ADD COLUMN appointment_approval_sent_at TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE thread_revisions ADD COLUMN appointment_approved_at TIMESTAMP WITH TIME ZONE")
    op.create_unique_constraint(
        "uq_thread_revisions_appointment_approval_token",
        "thread_revisions",
        ["appointment_approval_token"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_thread_revisions_appointment_approval_token",
        "thread_revisions",
        type_="unique",
    )
    op.execute("ALTER TABLE thread_revisions DROP COLUMN appointment_approved_at")
    op.execute("ALTER TABLE thread_revisions DROP COLUMN appointment_approval_sent_at")
    op.execute("ALTER TABLE thread_revisions DROP COLUMN appointment_approval_token")
    op.execute("ALTER TABLE thread_revisions DROP COLUMN appointment_approval_status")
