"""fix whatsapp nullability

Revision ID: 20260219_fix_whatsapp_nullability
Revises: 20260219_add_whatsapp_mirroring_tables
Create Date: 2026-02-19 00:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260219_fix_whatsapp_nullability"
down_revision: Union[str, Sequence[str], None] = "20260219_add_whatsapp_mirroring_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in set(inspector.get_table_names())


def _column_nullable(inspector: sa.Inspector, table_name: str, column_name: str) -> bool | None:
    for col in inspector.get_columns(table_name):
        if col.get("name") == column_name:
            return bool(col.get("nullable"))
    return None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector, "whatsapp_threads"):
        lead_id_nullable = _column_nullable(inspector, "whatsapp_threads", "lead_id")
        if lead_id_nullable is False:
            op.alter_column(
                "whatsapp_threads",
                "lead_id",
                existing_type=sa.Integer(),
                nullable=True,
            )

    if _table_exists(inspector, "whatsapp_messages"):
        wa_message_id_nullable = _column_nullable(inspector, "whatsapp_messages", "wa_message_id")
        if wa_message_id_nullable is False:
            op.alter_column(
                "whatsapp_messages",
                "wa_message_id",
                existing_type=sa.String(length=191),
                nullable=True,
            )

        raw_payload_nullable = _column_nullable(inspector, "whatsapp_messages", "raw_payload")
        if raw_payload_nullable is False:
            op.alter_column(
                "whatsapp_messages",
                "raw_payload",
                existing_type=postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            )


def downgrade() -> None:
    # Intentionally no-op: this migration relaxes nullability only.
    pass
