"""add whatsapp thread memory tables

Revision ID: 20260330_add_whatsapp_thread_memory_tables
Revises: 20260319_add_whatsapp_thread_display_name_override
Create Date: 2026-03-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260330_add_whatsapp_thread_memory_tables"
down_revision: Union[str, Sequence[str], None] = "20260319_add_whatsapp_thread_display_name_override"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in set(inspector.get_table_names())


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {idx.get("name") for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, "whatsapp_thread_states"):
        op.create_table(
            "whatsapp_thread_states",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("thread_id", sa.Integer(), nullable=False),
            sa.Column("last_intent", sa.String(length=30), nullable=True),
            sa.Column("last_stage", sa.String(length=30), nullable=True),
            sa.Column("needs_human", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("current_focus_candidate_id", sa.Integer(), nullable=True),
            sa.Column("customer_name", sa.String(length=120), nullable=True),
            sa.Column("home_zone_group", sa.String(length=50), nullable=True),
            sa.Column("home_zone_detail", sa.String(length=80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(
                ["thread_id"],
                ["whatsapp_threads.id"],
                name="fk_whatsapp_thread_states_thread_id",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("thread_id", name="uq_whatsapp_thread_states_thread_id"),
        )

    if _table_exists(inspector, "whatsapp_thread_states"):
        state_indexes = _index_names(inspector, "whatsapp_thread_states")
        if "ix_whatsapp_thread_states_thread_id" not in state_indexes:
            op.create_index(
                "ix_whatsapp_thread_states_thread_id",
                "whatsapp_thread_states",
                ["thread_id"],
                unique=True,
            )

    if not _table_exists(inspector, "whatsapp_thread_candidates"):
        op.create_table(
            "whatsapp_thread_candidates",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("thread_id", sa.Integer(), nullable=False),
            sa.Column("label", sa.String(length=120), nullable=True),
            sa.Column("marca", sa.String(length=50), nullable=True),
            sa.Column("modelo", sa.String(length=50), nullable=True),
            sa.Column("version_text", sa.String(length=120), nullable=True),
            sa.Column("anio", sa.Integer(), nullable=True),
            sa.Column("tipo_vehiculo", sa.String(length=30), nullable=True),
            sa.Column("zone_group", sa.String(length=50), nullable=True),
            sa.Column("zone_detail", sa.String(length=80), nullable=True),
            sa.Column("direccion_texto", sa.Text(), nullable=True),
            sa.Column("source_text", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="mentioned"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(
                ["thread_id"],
                ["whatsapp_threads.id"],
                name="fk_whatsapp_thread_candidates_thread_id",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    if _table_exists(inspector, "whatsapp_thread_candidates"):
        candidate_indexes = _index_names(inspector, "whatsapp_thread_candidates")
        if "ix_whatsapp_thread_candidates_thread_id" not in candidate_indexes:
            op.create_index(
                "ix_whatsapp_thread_candidates_thread_id",
                "whatsapp_thread_candidates",
                ["thread_id"],
                unique=False,
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector, "whatsapp_thread_candidates"):
        candidate_indexes = _index_names(inspector, "whatsapp_thread_candidates")
        if "ix_whatsapp_thread_candidates_thread_id" in candidate_indexes:
            op.drop_index("ix_whatsapp_thread_candidates_thread_id", table_name="whatsapp_thread_candidates")
        op.drop_table("whatsapp_thread_candidates")

    if _table_exists(inspector, "whatsapp_thread_states"):
        state_indexes = _index_names(inspector, "whatsapp_thread_states")
        if "ix_whatsapp_thread_states_thread_id" in state_indexes:
            op.drop_index("ix_whatsapp_thread_states_thread_id", table_name="whatsapp_thread_states")
        op.drop_table("whatsapp_thread_states")
