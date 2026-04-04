"""add agencias, vendedores and revision commercial fields

Revision ID: 20260216_add_agencias_and_revision_fields
Revises: 20260211_add_profesional_telefono
Create Date: 2026-02-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260216_add_agencias_and_revision_fields"
down_revision: Union[str, Sequence[str], None] = "20260211_add_profesional_telefono"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "vendedores" not in tables:
        op.create_table(
            "vendedores",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nombre", sa.String(length=120), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )

    if "agencias" not in tables:
        op.create_table(
            "agencias",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nombre_agencia", sa.String(length=120), nullable=False),
            sa.Column("direccion", sa.Text(), nullable=True),
            sa.Column("gmaps", sa.Text(), nullable=True),
            sa.Column("mail", sa.String(length=120), nullable=True),
            sa.Column("vendedor_id", sa.Integer(), nullable=True),
            sa.Column("telefono", sa.String(length=40), nullable=True),
            sa.Column("file_path", sa.String(length=255), nullable=True),
            sa.Column("file_name", sa.String(length=255), nullable=True),
            sa.Column("fecha_subido", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["vendedor_id"], ["vendedores.id"], name="fk_agencias_vendedor_id"),
        )

    rev_cols = {c["name"] for c in insp.get_columns("revisions")}
    if "tipo_vendedor" not in rev_cols:
        op.add_column("revisions", sa.Column("tipo_vendedor", sa.String(length=20), nullable=True))
    if "agencia_id" not in rev_cols:
        op.add_column("revisions", sa.Column("agencia_id", sa.Integer(), nullable=True))
    if "compro" not in rev_cols:
        op.add_column("revisions", sa.Column("compro", sa.String(length=12), nullable=True))
    if "resultado_link" not in rev_cols:
        op.add_column("revisions", sa.Column("resultado_link", sa.Text(), nullable=True))
    if "comision" not in rev_cols:
        op.add_column("revisions", sa.Column("comision", sa.Integer(), nullable=True))
    if "cobrado" not in rev_cols:
        op.add_column("revisions", sa.Column("cobrado", sa.String(length=5), nullable=True))
    if "fecha_cobro" not in rev_cols:
        op.add_column("revisions", sa.Column("fecha_cobro", sa.Date(), nullable=True))

    rev_fks = {fk.get("name") for fk in insp.get_foreign_keys("revisions")}
    if "fk_revisions_agencia_id" not in rev_fks:
        op.create_foreign_key(
            "fk_revisions_agencia_id",
            "revisions",
            "agencias",
            ["agencia_id"],
            ["id"],
        )

    rev_indexes = {ix.get("name") for ix in insp.get_indexes("revisions")}
    if "ix_revisions_agencia_id" not in rev_indexes:
        op.create_index("ix_revisions_agencia_id", "revisions", ["agencia_id"])

    op.execute(
        """
        UPDATE revisions
        SET tipo_vendedor = vendedor_tipo
        WHERE (tipo_vendedor IS NULL OR tipo_vendedor = '')
          AND vendedor_tipo IS NOT NULL
          AND vendedor_tipo <> ''
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    rev_indexes = {ix.get("name") for ix in insp.get_indexes("revisions")}
    if "ix_revisions_agencia_id" in rev_indexes:
        op.drop_index("ix_revisions_agencia_id", table_name="revisions")

    rev_fks = {fk.get("name") for fk in insp.get_foreign_keys("revisions")}
    if "fk_revisions_agencia_id" in rev_fks:
        op.drop_constraint("fk_revisions_agencia_id", "revisions", type_="foreignkey")

    rev_cols = {c["name"] for c in insp.get_columns("revisions")}
    for col in ["fecha_cobro", "cobrado", "comision", "resultado_link", "compro", "agencia_id", "tipo_vendedor"]:
        if col in rev_cols:
            op.drop_column("revisions", col)

    tables = set(insp.get_table_names())
    if "agencias" in tables:
        op.drop_table("agencias")
    if "vendedores" in tables:
        op.drop_table("vendedores")
