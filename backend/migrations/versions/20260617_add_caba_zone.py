"""add CABA city-level sentinel zone (zone_detail='CABA', viaticos=0)

When a user says "CABA" / "Capital Federal" / "Ciudad Autónoma de Buenos Aires"
without specifying a barrio, PricingService must be able to produce a quote with
viaticos=0 rather than returning zone_not_found.

Revision ID: 20260617_add_caba_zone
Revises: 20260517_add_latest_inbound_to_whatsapp_threads
Create Date: 2026-06-17
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260617_add_caba_zone"
down_revision: str | None = "20260517_add_latest_inbound_to_whatsapp_threads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO viaticos_zones (zone_group, zone_detail, viaticos)
            VALUES ('CABA', 'CABA', 0)
            ON CONFLICT DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM viaticos_zones WHERE zone_group = 'CABA' AND zone_detail = 'CABA'"
        )
    )
