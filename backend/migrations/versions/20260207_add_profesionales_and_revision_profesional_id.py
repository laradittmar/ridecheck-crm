"""add profesionales and revision profesional id

Revision ID: 20260207_add_profesionales
Revises: 
Create Date: 2026-02-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260207_add_profesionales"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    table_names = set(insp.get_table_names())
    if "profesionales" not in table_names:
        op.create_table(
            "profesionales",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nombre", sa.String(length=80), nullable=False),
            sa.Column("apellido", sa.String(length=80), nullable=False),
            sa.Column("email", sa.String(length=120), nullable=False),
            sa.Column("cargo", sa.String(length=80), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    rev_cols = {c["name"] for c in insp.get_columns("revisions")}
    if "profesional_id" not in rev_cols:
        op.add_column("revisions", sa.Column("profesional_id", sa.Integer(), nullable=True))

    fks = insp.get_foreign_keys("revisions")
    has_fk = any(fk.get("constrained_columns") == ["profesional_id"] for fk in fks)
    if not has_fk:
        op.create_foreign_key(
            "fk_revisions_profesional_id",
            "revisions",
            "profesionales",
            ["profesional_id"],
            ["id"],
        )

    indexes = insp.get_indexes("revisions")
    has_idx = any(ix.get("name") == "ix_revisions_profesional_id" for ix in indexes)
    if not has_idx:
        op.create_index("ix_revisions_profesional_id", "revisions", ["profesional_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    fks = insp.get_foreign_keys("revisions")
    has_fk = any(fk.get("constrained_columns") == ["profesional_id"] for fk in fks)
    if has_fk:
        op.drop_constraint("fk_revisions_profesional_id", "revisions", type_="foreignkey")

    indexes = insp.get_indexes("revisions")
    has_idx = any(ix.get("name") == "ix_revisions_profesional_id" for ix in indexes)
    if has_idx:
        op.drop_index("ix_revisions_profesional_id", table_name="revisions")

    rev_cols = {c["name"] for c in insp.get_columns("revisions")}
    if "profesional_id" in rev_cols:
        op.drop_column("revisions", "profesional_id")

    table_names = set(insp.get_table_names())
    if "profesionales" in table_names:
        op.drop_table("profesionales")
