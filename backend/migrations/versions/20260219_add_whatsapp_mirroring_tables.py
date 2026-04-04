"""add whatsapp mirroring tables

Revision ID: 20260219_add_whatsapp_mirroring_tables
Revises: 20260216_add_users_table
Create Date: 2026-02-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260219_add_whatsapp_mirroring_tables"
down_revision: Union[str, Sequence[str], None] = "20260216_add_users_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in set(inspector.get_table_names())


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {idx.get("name") for idx in inspector.get_indexes(table_name)}


def _fk_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {fk.get("name") for fk in inspector.get_foreign_keys(table_name)}


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {col.get("name") for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, "whatsapp_contacts"):
        op.create_table(
            "whatsapp_contacts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("wa_id", sa.String(length=80), nullable=False),
            sa.Column("display_name", sa.String(length=255), nullable=True),
            sa.Column("phone", sa.String(length=40), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("wa_id", name="uq_whatsapp_contacts_wa_id"),
        )

    inspector = sa.inspect(bind)
    if _table_exists(inspector, "whatsapp_contacts"):
        contact_indexes = _index_names(inspector, "whatsapp_contacts")
        if "ix_whatsapp_contacts_wa_id" not in contact_indexes:
            op.create_index("ix_whatsapp_contacts_wa_id", "whatsapp_contacts", ["wa_id"], unique=False)

    if not _table_exists(inspector, "whatsapp_threads"):
        op.create_table(
            "whatsapp_threads",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("contact_id", sa.Integer(), nullable=False),
            sa.Column("lead_id", sa.Integer(), nullable=True),
            sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("unread_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(
                ["contact_id"],
                ["whatsapp_contacts.id"],
                name="fk_whatsapp_threads_contact_id",
                ondelete="CASCADE",
            ),
        )

    inspector = sa.inspect(bind)
    if _table_exists(inspector, "whatsapp_threads"):
        thread_indexes = _index_names(inspector, "whatsapp_threads")
        if "ix_whatsapp_threads_contact_id" not in thread_indexes:
            op.create_index("ix_whatsapp_threads_contact_id", "whatsapp_threads", ["contact_id"], unique=False)
        if "lead_id" in _column_names(inspector, "whatsapp_threads") and "ix_whatsapp_threads_lead_id" not in thread_indexes:
            op.create_index("ix_whatsapp_threads_lead_id", "whatsapp_threads", ["lead_id"], unique=False)

        thread_fks = _fk_names(inspector, "whatsapp_threads")
        if (
            _table_exists(inspector, "leads")
            and "lead_id" in _column_names(inspector, "whatsapp_threads")
            and "fk_whatsapp_threads_lead_id" not in thread_fks
        ):
            op.create_foreign_key(
                "fk_whatsapp_threads_lead_id",
                "whatsapp_threads",
                "leads",
                ["lead_id"],
                ["id"],
                ondelete="SET NULL",
            )

    if not _table_exists(inspector, "whatsapp_messages"):
        op.create_table(
            "whatsapp_messages",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("thread_id", sa.Integer(), nullable=False),
            sa.Column("wa_message_id", sa.String(length=191), nullable=True),
            sa.Column("direction", sa.String(length=10), nullable=False),
            sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
            sa.Column("text", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'received'")),
            sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.CheckConstraint("direction IN ('in', 'out')", name="ck_whatsapp_messages_direction"),
            sa.CheckConstraint(
                "status IN ('received', 'sent', 'delivered', 'read', 'failed')",
                name="ck_whatsapp_messages_status",
            ),
            sa.ForeignKeyConstraint(
                ["thread_id"],
                ["whatsapp_threads.id"],
                name="fk_whatsapp_messages_thread_id",
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint("wa_message_id", name="uq_whatsapp_messages_wa_message_id"),
        )

    inspector = sa.inspect(bind)
    if _table_exists(inspector, "whatsapp_messages"):
        message_indexes = _index_names(inspector, "whatsapp_messages")
        if "ix_whatsapp_messages_thread_id_timestamp" not in message_indexes:
            op.create_index(
                "ix_whatsapp_messages_thread_id_timestamp",
                "whatsapp_messages",
                ["thread_id", "timestamp"],
                unique=False,
            )
        if "ix_whatsapp_messages_wa_message_id" not in message_indexes:
            op.create_index(
                "ix_whatsapp_messages_wa_message_id",
                "whatsapp_messages",
                ["wa_message_id"],
                unique=True,
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector, "whatsapp_messages"):
        message_indexes = _index_names(inspector, "whatsapp_messages")
        if "ix_whatsapp_messages_wa_message_id" in message_indexes:
            op.drop_index("ix_whatsapp_messages_wa_message_id", table_name="whatsapp_messages")
        if "ix_whatsapp_messages_thread_id_timestamp" in message_indexes:
            op.drop_index("ix_whatsapp_messages_thread_id_timestamp", table_name="whatsapp_messages")
        op.drop_table("whatsapp_messages")

    inspector = sa.inspect(bind)
    if _table_exists(inspector, "whatsapp_threads"):
        thread_indexes = _index_names(inspector, "whatsapp_threads")
        if "ix_whatsapp_threads_lead_id" in thread_indexes:
            op.drop_index("ix_whatsapp_threads_lead_id", table_name="whatsapp_threads")
        if "ix_whatsapp_threads_contact_id" in thread_indexes:
            op.drop_index("ix_whatsapp_threads_contact_id", table_name="whatsapp_threads")
        op.drop_table("whatsapp_threads")

    inspector = sa.inspect(bind)
    if _table_exists(inspector, "whatsapp_contacts"):
        contact_indexes = _index_names(inspector, "whatsapp_contacts")
        if "ix_whatsapp_contacts_wa_id" in contact_indexes:
            op.drop_index("ix_whatsapp_contacts_wa_id", table_name="whatsapp_contacts")
        op.drop_table("whatsapp_contacts")
