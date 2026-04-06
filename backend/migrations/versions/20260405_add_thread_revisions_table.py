"""add thread revisions table

Revision ID: 20260405_add_thread_revisions_table
Revises: 20260404_add_last_processed_inbound_wa_message_id_to_thread_state
Create Date: 2026-04-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260405_add_thread_revisions_table"
down_revision: Union[str, Sequence[str], None] = "20260404_add_last_processed_inbound_wa_message_id_to_thread_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in set(inspector.get_table_names())


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {idx.get("name") for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, "thread_revisions"):
        op.create_table(
            "thread_revisions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("thread_id", sa.Integer(), nullable=False),
            sa.Column("candidate_id", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="collecting_data"),
            sa.Column("buyer_name", sa.String(length=120), nullable=True),
            sa.Column("buyer_phone", sa.String(length=40), nullable=True),
            sa.Column("buyer_email", sa.String(length=120), nullable=True),
            sa.Column("seller_type", sa.String(length=30), nullable=True),
            sa.Column("seller_name", sa.String(length=120), nullable=True),
            sa.Column("address", sa.Text(), nullable=True),
            sa.Column("scheduled_date", sa.Date(), nullable=True),
            sa.Column("scheduled_time", sa.Time(), nullable=True),
            sa.Column("tipo_vehiculo", sa.String(length=30), nullable=True),
            sa.Column("marca", sa.String(length=50), nullable=True),
            sa.Column("modelo", sa.String(length=50), nullable=True),
            sa.Column("anio", sa.Integer(), nullable=True),
            sa.Column("publication_url", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint(
                "status IN ('draft', 'collecting_data', 'booked', 'completed')",
                name="ck_thread_revisions_status",
            ),
            sa.ForeignKeyConstraint(
                ["thread_id"],
                ["whatsapp_threads.id"],
                name="fk_thread_revisions_thread_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["candidate_id"],
                ["whatsapp_thread_candidates.id"],
                name="fk_thread_revisions_candidate_id",
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    if _table_exists(inspector, "thread_revisions"):
        indexes = _index_names(inspector, "thread_revisions")
        if "ix_thread_revisions_thread_id" not in indexes:
            op.create_index("ix_thread_revisions_thread_id", "thread_revisions", ["thread_id"], unique=False)
        if "ix_thread_revisions_candidate_id" not in indexes:
            op.create_index("ix_thread_revisions_candidate_id", "thread_revisions", ["candidate_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector, "thread_revisions"):
        indexes = _index_names(inspector, "thread_revisions")
        if "ix_thread_revisions_candidate_id" in indexes:
            op.drop_index("ix_thread_revisions_candidate_id", table_name="thread_revisions")
        if "ix_thread_revisions_thread_id" in indexes:
            op.drop_index("ix_thread_revisions_thread_id", table_name="thread_revisions")
        op.drop_table("thread_revisions")
