"""add Norte / Benavidez to viaticos_zones

Benavidez is a locality within Tigre partido (GBA Norte).  It was submitted
by a real customer in the Location Fallback Flow but was not in the pricing DB,
causing an avoidable needs_human escalation.  Viaticos = 0, same tier as Tigre.

Revision ID: 20260622_benavidez_zone
Revises: 20260622_fallback_flow_state
Create Date: 2026-06-22
"""

from __future__ import annotations

from alembic import op

revision: str = "20260622_benavidez_zone"
down_revision: str | None = "20260622_fallback_flow_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO viaticos_zones (zone_group, zone_detail, viaticos)
        SELECT 'Norte', 'Benavidez', 0
        WHERE NOT EXISTS (
            SELECT 1 FROM viaticos_zones
            WHERE zone_group = 'Norte' AND zone_detail = 'Benavidez'
        )
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM viaticos_zones
        WHERE zone_group = 'Norte' AND zone_detail = 'Benavidez'
    """)
