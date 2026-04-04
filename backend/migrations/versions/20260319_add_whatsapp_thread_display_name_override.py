"""add display_name_override to whatsapp_threads

Revision ID: 20260319_add_whatsapp_thread_display_name_override
Revises: 20260303_add_ai_events_table
Create Date: 2026-03-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260319_add_whatsapp_thread_display_name_override"
down_revision: Union[str, Sequence[str], None] = "20260303_add_ai_events_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in set(inspector.get_table_names())


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(col.get("name") == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector, "whatsapp_threads") and not _column_exists(
        inspector,
        "whatsapp_threads",
        "display_name_override",
    ):
        op.add_column(
            "whatsapp_threads",
            sa.Column("display_name_override", sa.String(length=255), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector, "whatsapp_threads") and _column_exists(
        inspector,
        "whatsapp_threads",
        "display_name_override",
    ):
        op.drop_column("whatsapp_threads", "display_name_override")
