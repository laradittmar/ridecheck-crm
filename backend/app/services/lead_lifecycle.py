"""WILD-04R: Centralized Lead lifecycle helper.

All CRM writes to Lead.estado MUST go through set_lead_estado() so that
cycle_reset_pending is set correctly when a human transitions a lead back
to CONSULTA_NUEVA.

CE internal writes to Lead.estado (COORDINAR_DISPONIBILIDAD, ATENCION_HUMANA)
must NOT use this helper — those are engine-internal transitions within an
active cycle and must not trigger cycle reset.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Lead, WhatsAppThread, WhatsAppThreadState

logger = logging.getLogger(__name__)


def set_lead_estado(db: Session, lead: Lead, new_estado: str) -> None:
    """Set lead.estado and signal a cycle reset when re-entering CONSULTA_NUEVA.

    Rules:
    - old_estado != CONSULTA_NUEVA AND new_estado == CONSULTA_NUEVA
      → set cycle_reset_pending=True on all associated WhatsAppThreadState rows
    - old_estado == CONSULTA_NUEVA AND new_estado == CONSULTA_NUEVA
      → no reset signal (already in new-inquiry state, no cycle transition)
    - new_estado != CONSULTA_NUEVA
      → no reset signal (not a new-cycle transition)

    Callers are responsible for committing the session.
    """
    old_estado = lead.estado
    if old_estado != "CONSULTA_NUEVA" and new_estado == "CONSULTA_NUEVA":
        _set_cycle_reset_signal(db, lead)
    lead.estado = new_estado


def _set_cycle_reset_signal(db: Session, lead: Lead) -> None:
    """Set cycle_reset_pending=True on all thread states linked to this lead."""
    threads = db.execute(
        select(WhatsAppThread).where(WhatsAppThread.lead_id == lead.id)
    ).scalars().all()

    for thread in threads:
        state = thread.state
        if state is None:
            state = WhatsAppThreadState(thread_id=thread.id)
            thread.state = state
            db.add(state)
            db.flush()
        state.cycle_reset_pending = True
        logger.info(
            "WILD-04R cycle_reset_pending=True set for lead_id=%s thread_id=%s",
            lead.id, thread.id,
        )
