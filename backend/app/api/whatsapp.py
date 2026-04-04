from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Lead,
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppThread,
    WhatsAppThreadCandidate,
)
from ..schemas.whatsapp_api import (
    WhatsAppThreadCandidateCreate,
    WhatsAppThreadCandidatePatch,
    WhatsAppThreadCandidateRead,
    WhatsAppSendTextIn,
    WhatsAppSendTextOut,
    WhatsAppThreadStatePatch,
    WhatsAppThreadStateRead,
    WhatsAppThreadLinkIn,
    WhatsAppThreadMessagesOut,
    WhatsAppThreadOut,
)
from ..services.db_errors import commit_or_400
from ..services.whatsapp_thread_state import build_thread_state_read, upsert_thread_state
from ..services.whatsapp_threads import load_recent_thread_messages, load_thread_payload
from ..ui.whatsapp_ui import _send_whatsapp_cloud_text

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])
thread_router = APIRouter(tags=["whatsapp"])


def _require_thread(db: Session, thread_id: int) -> WhatsAppThread:
    thread = db.get(WhatsAppThread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return thread


@router.get("/threads", response_model=list[WhatsAppThreadOut])
def list_threads(db: Session = Depends(get_db)):
    thread_ids = db.execute(
        select(WhatsAppThread.id).order_by(WhatsAppThread.last_message_at.desc().nullslast(), WhatsAppThread.id.desc())
    ).scalars().all()
    return [payload for tid in thread_ids if (payload := load_thread_payload(db, int(tid))) is not None]


@router.get("/thread/{thread_id}", response_model=WhatsAppThreadOut)
def get_thread(thread_id: int, db: Session = Depends(get_db)):
    payload = load_thread_payload(db, thread_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return payload


@router.get("/thread/{thread_id}/messages", response_model=WhatsAppThreadMessagesOut)
def get_thread_messages(thread_id: int, limit: int = Query(default=10, ge=1, le=20), db: Session = Depends(get_db)):
    _require_thread(db, thread_id)

    messages = load_recent_thread_messages(db=db, thread_id=thread_id, limit=limit)
    return WhatsAppThreadMessagesOut(thread_id=thread_id, messages=messages)


@router.get("/thread/{thread_id}/state", response_model=WhatsAppThreadStateRead)
def get_thread_state(thread_id: int, db: Session = Depends(get_db)):
    thread = _require_thread(db, thread_id)
    return build_thread_state_read(thread_id=thread_id, state=thread.state)


@router.patch("/thread/{thread_id}/state", response_model=WhatsAppThreadStateRead)
def patch_thread_state(thread_id: int, payload: WhatsAppThreadStatePatch, db: Session = Depends(get_db)):
    thread = _require_thread(db, thread_id)
    return upsert_thread_state(db=db, thread=thread, payload=payload)


@router.get("/thread/{thread_id}/candidates", response_model=list[WhatsAppThreadCandidateRead])
def list_thread_candidates(thread_id: int, db: Session = Depends(get_db)):
    _require_thread(db, thread_id)
    return db.execute(
        select(WhatsAppThreadCandidate)
        .where(WhatsAppThreadCandidate.thread_id == thread_id)
        .order_by(WhatsAppThreadCandidate.updated_at.desc(), WhatsAppThreadCandidate.id.desc())
    ).scalars().all()


@router.post("/thread/{thread_id}/candidates", response_model=WhatsAppThreadCandidateRead)
def create_thread_candidate(
    thread_id: int,
    payload: WhatsAppThreadCandidateCreate,
    db: Session = Depends(get_db),
):
    _require_thread(db, thread_id)
    candidate = WhatsAppThreadCandidate(thread_id=thread_id, **payload.model_dump(exclude_unset=True))
    db.add(candidate)
    try:
        db.commit()
    except (DataError, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="No se pudo crear el candidato del thread") from exc

    db.refresh(candidate)
    return candidate


@router.patch("/thread/{thread_id}/candidates/{candidate_id}", response_model=WhatsAppThreadCandidateRead)
def patch_thread_candidate(
    thread_id: int,
    candidate_id: int,
    payload: WhatsAppThreadCandidatePatch,
    db: Session = Depends(get_db),
):
    _require_thread(db, thread_id)
    candidate = db.execute(
        select(WhatsAppThreadCandidate)
        .where(WhatsAppThreadCandidate.id == candidate_id)
        .where(WhatsAppThreadCandidate.thread_id == thread_id)
    ).scalar_one_or_none()
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(candidate, field, value)

    try:
        db.commit()
    except (DataError, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="No se pudo actualizar el candidato del thread") from exc

    db.refresh(candidate)
    return candidate


@router.post("/thread/{thread_id}/send-text", response_model=WhatsAppSendTextOut)
def send_thread_text(thread_id: int, payload: WhatsAppSendTextIn, db: Session = Depends(get_db)):
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    thread_data = db.execute(
        select(WhatsAppThread, WhatsAppContact.wa_id)
        .join(WhatsAppContact, WhatsAppThread.contact_id == WhatsAppContact.id)
        .where(WhatsAppThread.id == thread_id)
    ).first()
    if thread_data is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    thread = thread_data[0]
    to_wa_id = str(thread_data.wa_id or "").strip()
    if not to_wa_id:
        raise HTTPException(status_code=400, detail="Thread has no wa_id")

    now_utc = datetime.now(timezone.utc)
    outbound = WhatsAppMessage(
        thread_id=thread_id,
        wa_message_id=None,
        direction="out",
        status="pending",
        timestamp=now_utc,
        text=text,
        raw_payload={"reply_to_message_id": payload.reply_to_message_id}
        if payload.reply_to_message_id is not None
        else None,
    )
    db.add(outbound)
    thread.last_message_at = now_utc
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        outbound.status = "sent"
        db.add(outbound)
        thread.last_message_at = now_utc
        db.commit()

    try:
        wa_message_id, _ = _send_whatsapp_cloud_text(to_wa_id=to_wa_id, text=text)
        outbound.status = "sent"
        outbound.wa_message_id = wa_message_id
        db.add(outbound)
        db.commit()
        return WhatsAppSendTextOut(ok=True, thread_id=thread_id, wa_message_id=wa_message_id, text=text)
    except Exception as exc:
        db.rollback()
        outbound.status = "failed"
        db.add(outbound)
        db.commit()
        raise HTTPException(status_code=502, detail=f"WhatsApp outbound send failed: {exc}") from exc


@router.post("/thread/{thread_id}/link", response_model=WhatsAppThreadOut)
def link_thread(thread_id: int, payload: WhatsAppThreadLinkIn, db: Session = Depends(get_db)):
    thread = db.get(WhatsAppThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    if not db.get(Lead, payload.lead_id):
        raise HTTPException(status_code=404, detail="Lead not found")

    thread.lead_id = payload.lead_id
    commit_or_400(db, detail="No se pudo vincular el thread de WhatsApp")
    refreshed = load_thread_payload(db, thread_id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return refreshed


@router.post("/thread/{thread_id}/unlink", response_model=WhatsAppThreadOut)
def unlink_thread(thread_id: int, db: Session = Depends(get_db)):
    thread = db.get(WhatsAppThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    thread.lead_id = None
    commit_or_400(db, detail="No se pudo desvincular el thread de WhatsApp")
    refreshed = load_thread_payload(db, thread_id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return refreshed


@thread_router.post("/whatsapp/thread/{thread_id}/link-lead", response_model=WhatsAppThreadOut)
def link_thread_lead(thread_id: int, payload: WhatsAppThreadLinkIn, db: Session = Depends(get_db)):
    return link_thread(thread_id=thread_id, payload=payload, db=db)


@thread_router.post("/whatsapp/thread/{thread_id}/unlink-lead", response_model=WhatsAppThreadOut)
def unlink_thread_lead(thread_id: int, db: Session = Depends(get_db)):
    return unlink_thread(thread_id=thread_id, db=db)
