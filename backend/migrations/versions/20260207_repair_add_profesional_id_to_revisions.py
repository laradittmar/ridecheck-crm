"""repair add profesional_id to revisions

Revision ID: 20260207_repair_profesional_id
Revises: 20260207_add_profesionales
Create Date: 2026-02-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260207_repair_profesional_id"
down_revision: Union[str, Sequence[str], None] = "20260207_add_profesionales"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "revisions"
COL_NAME = "profesional_id"
FK_NAME = "fk_revisions_profesional_id"
IDX_NAME = "ix_revisions_profesional_id"


def _has_column(inspector: sa.Inspector, table: str, col: str) -> bool:
    return any(c["name"] == col for c in inspector.get_columns(table))


def _has_fk(inspector: sa.Inspector, table: str, name: str) -> bool:
    return any(fk.get("name") == name for fk in inspector.get_foreign_keys(table))


def _has_index(inspector: sa.Inspector, table: str, name: str) -> bool:
    return any(ix.get("name") == name for ix in inspector.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not _has_column(insp, TABLE_NAME, COL_NAME):
        op.add_column(TABLE_NAME, sa.Column(COL_NAME, sa.Integer(), nullable=True))

    if not _has_fk(insp, TABLE_NAME, FK_NAME):
        op.create_foreign_key(
            FK_NAME,
            TABLE_NAME,
            "profesionales",
            [COL_NAME],
            ["id"],
            ondelete="SET NULL",
        )

    if not _has_index(insp, TABLE_NAME, IDX_NAME):
        op.create_index(IDX_NAME, TABLE_NAME, [COL_NAME])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if _has_fk(insp, TABLE_NAME, FK_NAME):
        op.drop_constraint(FK_NAME, TABLE_NAME, type_="foreignkey")

    if _has_index(insp, TABLE_NAME, IDX_NAME):
        op.drop_index(IDX_NAME, table_name=TABLE_NAME)

    if _has_column(insp, TABLE_NAME, COL_NAME):
        op.drop_column(TABLE_NAME, COL_NAME)
