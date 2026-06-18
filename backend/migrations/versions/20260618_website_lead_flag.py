"""add is_website_lead to whatsapp_thread_states

Marks threads that originated from the wa.me prefilled website form.
Used to route the booking Flow to the short website-lead form (WHATSAPP_WEBSITE_FLOW_ID)
instead of the generic full-data form (WHATSAPP_FLOW_ID).

Revision ID: 20260618_website_lead_flag
Revises: 20260618_last_visible_slots
Create Date: 2026-06-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260618_website_lead_flag"
down_revision: str | None = "20260618_last_visible_slots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_thread_states",
        sa.Column(
            "is_website_lead",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_thread_states", "is_website_lead")
