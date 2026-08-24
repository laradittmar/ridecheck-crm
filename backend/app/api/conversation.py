"""M18 — Conversation engine endpoint.

Called by the webhook background task when CONVERSATION_ENGINE_ENABLED=true.
Can also be called directly from n8n or any external orchestrator.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AiEvent, WhatsAppMessage, WhatsAppThreadState
from ..schemas.conversation import ConversationHandleIn, ConversationHandleOut
from ..services.conversation_engine import ConversationEngine
from ..settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conversation", tags=["conversation"])

_PERF_OK_MS = 60_000
_PERF_MEDIUM_MS = 120_000


def _compute_performance_status(result: ConversationHandleOut, latency_total_ms: int | None) -> str:
    if not result.reply_required:
        return "NO_REPLY_REQUIRED"
    if not result.reply_produced:
        return "PENDING"  # alert checker will escalate to ALERT after 120s
    ms = latency_total_ms
    if ms is None:
        return "OK"
    if ms <= _PERF_OK_MS:
        return "OK"
    if ms <= _PERF_MEDIUM_MS:
        return "MEDIUM"
    return "ALERT"


def _write_ai_event_telemetry(
    db: Session,
    payload: ConversationHandleIn,
    result: ConversationHandleOut,
) -> None:
    try:
        ai_event = db.execute(
            select(AiEvent).where(AiEvent.wa_message_id == payload.wa_message_id)
        ).scalar_one_or_none()
        if ai_event is None:
            return

        now_utc = datetime.now(timezone.utc)

        # latency_total_ms: from AiEvent.created_at (webhook arrival) to now (CE finished)
        # This includes n8n debounce + CE processing — the full customer wait time.
        ai_event_created = ai_event.created_at
        if ai_event_created is not None:
            if ai_event_created.tzinfo is None:
                ai_event_created = ai_event_created.replace(tzinfo=timezone.utc)
            latency_total_ms = int((now_utc - ai_event_created).total_seconds() * 1000)
        else:
            latency_total_ms = None

        # pre_ce_wait_ms: time before CE started processing
        # = total_latency - CE processing time
        # Labeled latency_debounce_ms but represents "pre-CE wait" (n8n debounce + overhead).
        if latency_total_ms is not None and result.latency_ce_ms is not None:
            latency_debounce_ms = max(0, latency_total_ms - result.latency_ce_ms)
        else:
            latency_debounce_ms = None

        # burst_message_count: inbound messages since previous cursor → this event
        burst_count = _count_burst_messages(db, payload)

        # cycle_message_count: inbound messages since cycle watermark
        cycle_count = _count_cycle_messages(db, payload)

        perf_status = _compute_performance_status(result, latency_total_ms)

        ai_event.status = "processed"
        ai_event.action = result.action
        ai_event.reply_required = result.reply_required
        ai_event.reply_produced = result.reply_produced
        ai_event.alert_eligible = result.alert_eligible
        ai_event.answer_source = result.answer_source
        ai_event.contributing_sources = (
            json.dumps(result.contributing_sources) if result.contributing_sources else None
        )
        ai_event.ai_invoked = result.ai_invoked
        ai_event.latency_ce_ms = result.latency_ce_ms
        ai_event.latency_total_ms = latency_total_ms
        ai_event.latency_debounce_ms = latency_debounce_ms
        ai_event.burst_message_count = burst_count
        ai_event.cycle_message_count = cycle_count
        ai_event.performance_status = perf_status
        db.commit()
    except Exception:
        logger.warning(
            "WILD-04R telemetry writeback failed wa_message_id=%s",
            payload.wa_message_id,
            exc_info=True,
        )
        try:
            db.rollback()
        except Exception:
            pass


def _count_burst_messages(db: Session, payload: ConversationHandleIn) -> int | None:
    try:
        # Find current event DB row
        current_msg = db.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == payload.wa_message_id)
        ).scalar_one_or_none()
        if current_msg is None:
            return None
        # Find thread state to get previous cursor
        from ..models import WhatsAppThread
        thread = db.get(WhatsAppThread, payload.thread_id)
        if thread is None:
            return None
        state = thread.state
        prev_cursor = None
        if state is not None:
            # After CE ran, last_processed_inbound_wa_message_id is NOW the current event.
            # We need the PREVIOUS cursor. We can't recover it here; return 1 as minimum.
            pass
        # Without previous cursor, count this turn as burst=1
        return 1
    except Exception:
        return None


def _count_cycle_messages(db: Session, payload: ConversationHandleIn) -> int | None:
    try:
        from ..models import WhatsAppThread
        thread = db.get(WhatsAppThread, payload.thread_id)
        if thread is None:
            return None
        state = thread.state
        if state is None:
            return None
        msg_query = select(WhatsAppMessage).where(
            WhatsAppMessage.thread_id == payload.thread_id,
            WhatsAppMessage.direction == "in",
        )
        if state.current_cycle_start_message_db_id is not None:
            msg_query = msg_query.where(
                WhatsAppMessage.id >= state.current_cycle_start_message_db_id
            )
        count = len(db.execute(msg_query).scalars().all())
        return count
    except Exception:
        return None


@router.post("/handle", response_model=ConversationHandleOut)
def handle_message(
    payload: ConversationHandleIn,
    db: Session = Depends(get_db),
) -> ConversationHandleOut:
    """Process an inbound WhatsApp message through the M18 conversation engine."""
    settings = get_settings()
    engine = ConversationEngine(db=db, settings=settings)
    result = engine.handle(payload)
    logger.info(
        "M18 handle thread_id=%s wa=%s action=%s ok=%s latency_ce_ms=%s",
        payload.thread_id,
        payload.wa_message_id,
        result.action,
        result.ok,
        result.latency_ce_ms,
    )
    _write_ai_event_telemetry(db, payload, result)
    return result
