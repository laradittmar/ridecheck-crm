"""add Dock Sud to viaticos_zones (Sur / Avellaneda pricing)

Dock Sud (also written "Doc Sud") is an industrial port locality in Partido
de Avellaneda, GBA Sur — NOT a CABA barrio and NOT La Boca.
Stored as zone_detail="Dock Sud", zone_group="Sur", viaticos=30000
(same tier as Avellaneda).

Revision ID: 20260622_dock_sud_zone
Revises: 20260618_website_lead_flag
Create Date: 2026-06-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "20260622_dock_sud_zone"
down_revision: str | None = "20260618_website_lead_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO viaticos_zones (zone_group, zone_detail, viaticos)
        SELECT 'Sur', 'Dock Sud', 30000
        WHERE NOT EXISTS (
            SELECT 1 FROM viaticos_zones
            WHERE zone_group = 'Sur' AND zone_detail = 'Dock Sud'
        )
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM viaticos_zones
        WHERE zone_group = 'Sur' AND zone_detail = 'Dock Sud'
    """)
