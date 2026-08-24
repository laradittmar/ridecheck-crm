"""WILD-04R observability fields on ai_events.

Adds 13 columns that expose CE decision metadata, latency measurements, and
SLA-driven alert eligibility to the ai_events audit table.

Fields:
  reply_required    — customer expected a reply on this turn
  reply_produced    — CE actually sent an outbound message
  alert_eligible    — unanswered-alert service should consider this event
  action            — CE action string (replied, skipped_dedup, error, …)
  answer_source     — primary answer origin (DETERMINISTIC_RULE, FAQ_RULE,
                      PRICING_SERVICE, SCHEDULING_SERVICE, VEHICLE_RESOLVER,
                      CE_AI, FLOW_RESPONSE, ERROR_FALLBACK, HUMAN)
  ai_invoked        — OpenAI was called for this turn
  contributing_sources — space-separated secondary sources (free text)
  latency_total_ms  — ms from earliest burst message created_at to outbound
  latency_debounce_ms — ms from first to last burst message created_at
  latency_ce_ms     — ms spent inside CE (Python perf_counter)
  burst_message_count — inbound messages in the debounce burst
  cycle_message_count — active-cycle messages visible to CE
  performance_status — OK / MEDIUM / ALERT per SLA thresholds

Revision ID: 20260824_wild04r_ai_events_observability
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_wild04r_ai_events_observability"
down_revision: str = "20260824_wild04r_cycle_boundary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col_name, col_type in [
        ("reply_required", sa.Boolean),
        ("reply_produced", sa.Boolean),
        ("alert_eligible", sa.Boolean),
        ("ai_invoked", sa.Boolean),
    ]:
        op.add_column("ai_events", sa.Column(col_name, col_type, nullable=True))

    for col_name, col_type in [
        ("action", sa.String(50)),
        ("answer_source", sa.String(50)),
        ("performance_status", sa.String(20)),
    ]:
        op.add_column("ai_events", sa.Column(col_name, col_type, nullable=True))

    op.add_column("ai_events", sa.Column("contributing_sources", sa.Text, nullable=True))

    for col_name in [
        "latency_total_ms",
        "latency_debounce_ms",
        "latency_ce_ms",
        "burst_message_count",
        "cycle_message_count",
    ]:
        op.add_column("ai_events", sa.Column(col_name, sa.Integer, nullable=True))


def downgrade() -> None:
    for col_name in [
        "reply_required", "reply_produced", "alert_eligible", "ai_invoked",
        "action", "answer_source", "performance_status", "contributing_sources",
        "latency_total_ms", "latency_debounce_ms", "latency_ce_ms",
        "burst_message_count", "cycle_message_count",
    ]:
        op.drop_column("ai_events", col_name)
