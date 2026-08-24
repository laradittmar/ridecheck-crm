"""Add ref_code and rc_code attribution fields to leads — M21.2-DATA.

Change:
  1. New nullable VARCHAR(10) column ``ref_code`` on ``leads``.
     Stores first-touch marketing ref from website tracking.js detectRef():
     values ga / ig / fb / org / dir / otro.  NULL when no attribution token
     arrived with the lead.

  2. New nullable VARCHAR(8) column ``rc_code`` on ``leads``, with an index.
     Stores the website-generated session code RC-XXXX (31-char alphabet,
     4-char suffix, 923 521 combinations).  Indexed for lookup; not unique
     (same code may appear on retries or page reloads within one session).
     NULL when no cod: token arrived.

Transport path:
  Website tracking.js appends "ref: <ref> · cod: RC-XXXX" to the WhatsApp
  prefill.  CRM _parse_website_form() extracts both tokens from the message
  body and persists them here.  gclid/gbraid/wbraid are NOT in the WhatsApp
  message and are NOT stored in this migration.

Revision ID: 20260824_lead_attribution_fields
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_lead_attribution_fields"
down_revision: str = "20260813_pending_turn_evidence_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column("ref_code", sa.String(10), nullable=True, server_default=None),
    )
    op.add_column(
        "leads",
        sa.Column("rc_code", sa.String(8), nullable=True, server_default=None),
    )
    op.create_index("ix_leads_rc_code", "leads", ["rc_code"])


def downgrade() -> None:
    op.drop_index("ix_leads_rc_code", table_name="leads")
    op.drop_column("leads", "rc_code")
    op.drop_column("leads", "ref_code")
