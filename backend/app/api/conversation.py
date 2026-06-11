"""M18 — Conversation engine endpoint.

Called by the webhook background task when CONVERSATION_ENGINE_ENABLED=true.
Can also be called directly from n8n or any external orchestrator.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas.conversation import ConversationHandleIn, ConversationHandleOut
from ..services.conversation_engine import ConversationEngine
from ..settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conversation", tags=["conversation"])


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
        "M18 handle thread_id=%s wa=%s action=%s ok=%s",
        payload.thread_id,
        payload.wa_message_id,
        result.action,
        result.ok,
    )
    return result
