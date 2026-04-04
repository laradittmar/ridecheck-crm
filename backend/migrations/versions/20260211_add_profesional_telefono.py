"""add profesionales.telefono

Revision ID: 20260211_add_profesional_telefono
Revises: 20260208_repair_add_lead_flag
Create Date: 2026-02-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "20260211_add_profesional_telefono"
down_revision: Union[str, Sequence[str], None] = "20260208_repair_add_lead_flag"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = [c["name"] for c in insp.get_columns("profesionales")]
    if "telefono" not in cols:
        op.add_column("profesionales", sa.Column("telefono", sa.String(length=40), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = [c["name"] for c in insp.get_columns("profesionales")]
    if "telefono" in cols:
        op.drop_column("profesionales", "telefono")
