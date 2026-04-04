"""add lead flag and map legacy estados to flags

Revision ID: 20260207_add_lead_flag
Revises: 20260207_repair_profesional_id
Create Date: 2026-02-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260207_add_lead_flag"
down_revision: Union[str, Sequence[str], None] = "20260207_repair_profesional_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "leads"
COL_NAME = "flag"

FLAG_FROM_ESTADO = {
    "CALIFICANDO": "PRESUPUESTANDO",
    "PRESUPUESTO_ENVIADO": "PRESUPUESTO_ENVIADO",
    "ACEPTADO": "ACEPTADO",
    "RECOMPRA": "RECOMPRA",
    "PERDIDO": "PERDIDO",
}


def _has_column(inspector: sa.Inspector, table: str, col: str) -> bool:
    return any(c["name"] == col for c in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not _has_column(insp, TABLE_NAME, COL_NAME):
        op.add_column(TABLE_NAME, sa.Column(COL_NAME, sa.String(length=30), nullable=True))

    cols = {c["name"] for c in insp.get_columns(TABLE_NAME)}
    if "estado" in cols and COL_NAME in cols:
        for old_estado, flag_val in FLAG_FROM_ESTADO.items():
            op.execute(
                sa.text(
                    f"""
                    UPDATE {TABLE_NAME}
                    SET {COL_NAME} = :flag, estado = :estado
                    WHERE estado = :old
                      AND ({COL_NAME} IS NULL OR {COL_NAME} = '')
                    """
                ),
                {"flag": flag_val, "estado": "CONSULTA_NUEVA", "old": old_estado},
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if _has_column(insp, TABLE_NAME, COL_NAME):
        op.drop_column(TABLE_NAME, COL_NAME)
