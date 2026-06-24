"""add group-default viáticos rows (zone_detail=NULL) for CABA/Norte/Oeste/Sur

When the Location Fallback Flow submits a zona_general and a freeform localidad
that does not match any exact row, PricingRepository Step 3 falls back to:
  WHERE zone_group = <group> AND zone_detail IS NULL
These rows did not exist before, causing avoidable needs_human escalation for
any localidad not in the exact-match table (e.g. Villa Nueva, a barrio of Tigre).

Defaults chosen:
  CABA  → 0       (all CABA travel is free or low-cost; unknown barrio same as known ones)
  Norte → 0       (inner Norte ring is free; far-north customers typically know their city)
  Oeste → 30000   (modal viatico across the Oeste locality table)
  Sur   → 30000   (modal viatico across the Sur locality table)

Existing exact-locality rows are NOT modified.  Exact matches (Step 1/2)
always take priority over this group default (Step 3) in the repository.

Revision ID: 20260624_group_default_viaticos
Revises: 20260622_benavidez_zone
Create Date: 2026-06-24
"""

from __future__ import annotations

from alembic import op

revision: str = "20260624_group_default_viaticos"
down_revision: str | None = "20260622_benavidez_zone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for zone_group, viaticos in [
        ("CABA",  0),
        ("Norte", 0),
        ("Oeste", 30000),
        ("Sur",   30000),
    ]:
        op.execute(f"""
            INSERT INTO viaticos_zones (zone_group, zone_detail, viaticos)
            SELECT '{zone_group}', NULL, {viaticos}
            WHERE NOT EXISTS (
                SELECT 1 FROM viaticos_zones
                WHERE zone_group = '{zone_group}' AND zone_detail IS NULL
            )
        """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM viaticos_zones
        WHERE zone_detail IS NULL
          AND zone_group IN ('CABA', 'Norte', 'Oeste', 'Sur')
    """)
