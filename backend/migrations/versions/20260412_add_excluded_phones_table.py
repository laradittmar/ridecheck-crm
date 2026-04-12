"""add excluded phones table

Revision ID: 20260412_add_excluded_phones_table
Revises: 20260412_add_system_settings_table
Create Date: 2026-04-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20260412_add_excluded_phones_table"
down_revision = "20260412_add_system_settings_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "excluded_phones",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("phone", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("phone", name="uq_excluded_phones_phone"),
    )
    op.create_index("ix_excluded_phones_phone", "excluded_phones", ["phone"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_excluded_phones_phone", table_name="excluded_phones")
    op.drop_table("excluded_phones")
