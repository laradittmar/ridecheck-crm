"""repair add leads.flag

Revision ID: 20260208_repair_add_lead_flag
Revises: 20260207_add_lead_flag
Create Date: 2026-02-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260208_repair_add_lead_flag"
down_revision: Union[str, Sequence[str], None] = "20260207_add_lead_flag"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS flag VARCHAR(40)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_leads_flag ON leads (flag)")
    op.execute(
        """
        UPDATE leads
        SET flag = CASE
            WHEN estado IN ('Presupuestando', 'CALIFICANDO', 'PRESUPUESTANDO') THEN 'PRESUPUESTANDO'
            WHEN estado IN ('Presupuesto Enviado', 'PRESUPUESTO_ENVIADO') THEN 'PRESUPUESTO_ENVIADO'
            WHEN estado IN ('Aceptado', 'ACEPTADO') THEN 'ACEPTADO'
            WHEN estado IN ('Re-compra', 'Recompra', 'RECOMPRA') THEN 'RECOMPRA'
            WHEN estado IN ('Perdido', 'PERDIDO') THEN 'PERDIDO'
            ELSE flag
        END
        WHERE flag IS NULL
          AND estado IN (
            'Presupuestando', 'CALIFICANDO', 'PRESUPUESTANDO',
            'Presupuesto Enviado', 'PRESUPUESTO_ENVIADO',
            'Aceptado', 'ACEPTADO',
            'Re-compra', 'Recompra', 'RECOMPRA',
            'Perdido', 'PERDIDO'
          )
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_leads_flag")
    op.execute("ALTER TABLE leads DROP COLUMN IF EXISTS flag")
