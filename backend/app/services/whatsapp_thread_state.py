from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from ..models import WhatsAppThread, WhatsAppThreadState
from ..schemas.whatsapp_api import WhatsAppThreadStatePatch, WhatsAppThreadStateRead


def build_thread_state_read(thread_id: int, state: WhatsAppThreadState | None) -> WhatsAppThreadState | WhatsAppThreadStateRead:
    if state is None:
        return WhatsAppThreadStateRead(thread_id=thread_id)
    return state


def upsert_thread_state(
    db: Session,
    thread: WhatsAppThread,
    payload: WhatsAppThreadStatePatch,
) -> WhatsAppThreadState:
    state = thread.state
    if state is None:
        state = WhatsAppThreadState(thread_id=thread.id)
        thread.state = state
        db.add(state)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(state, field, value)

    try:
        db.commit()
    except (DataError, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="No se pudo persistir el estado del thread") from exc

    db.refresh(state)
    return state
