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


#: Pre-Wild PW1-C (CD-09) — fields this generic PATCH may never write, with their owner.
#: Writing any of them by hand moves or forges a booking pointer behind the authority that
#: keeps it true, so a PATCH naming one is refused whole (typed 409, nothing applied) — also
#: when it names it with null, which used to be a silent no-op. There is no generic
#: operator operation on them to route to.
AUTHORITY_OWNED_FIELDS = {
    "flow_booking_token": "offer authority (OfferLifecycle): the live offer's transport mirror",
    "current_focus_candidate_id": "candidate authority: a focus move withdraws the live offer",
    "current_revision_id": "booking authority: the accepted booking pointer",
}

#: The negotiated facts a live ISSUED offer is accepted against: the zone is an acceptance
#: binding of the offer, and the Conversation Engine books the slot recorded here. While such
#: an offer exists a generic edit could invalidate it or redirect the booking, so it is
#: refused; with no live offer they stay ordinary operator-editable fields.
OFFER_BOUND_FIELDS = ("home_zone_group", "home_zone_detail", "preferred_day", "preferred_time")


def _refuse(code: str, fields: list[str], message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "fields": fields,
                                                  "message": message})


def _live_offer_id(db: Session, thread_id: int):
    from sqlalchemy import select
    from ..models import WhatsAppBookingOffer as O
    from .offer_lifecycle import OfferStatus
    row = db.execute(select(O.id).where(O.thread_id == thread_id)
                     .where(O.status == OfferStatus.ISSUED).limit(1)).first()
    return row[0] if row else None


def upsert_thread_state(
    db: Session,
    thread: WhatsAppThread,
    payload: WhatsAppThreadStatePatch,
) -> WhatsAppThreadState:
    changes = payload.model_dump(exclude_unset=True)
    owned = sorted(f for f in changes if f in AUTHORITY_OWNED_FIELDS)
    if owned:
        raise _refuse("THREAD_STATE_FIELD_AUTHORITY_OWNED", owned,
                      "Estos campos los mantiene la autoridad de reservas/candidatos; "
                      "no se editan desde aquí.")
    bound = sorted(f for f in changes if f in OFFER_BOUND_FIELDS and changes[f] is not None)
    if bound:
        # Same canonical order as every offer writer: the thread's state row first, bounded
        # (a no-op off PostgreSQL), so this check and an acceptance cannot interleave.
        from .offer_lifecycle import OfferLifecycle
        if OfferLifecycle(db).begin_offer_transaction(int(thread.id)) is not None:
            raise _refuse("THREAD_STATE_CONTENDED", bound,
                          "La conversación está en uso por una reserva; reintentá.")
        if _live_offer_id(db, int(thread.id)) is not None:
            db.rollback()
            raise _refuse("THREAD_STATE_FIELD_BOUND_TO_LIVE_OFFER", bound,
                          "Hay una oferta de turno vigente: zona y horario se cambian "
                          "desde la conversación, no desde esta edición.")

    state = thread.state
    if state is None:
        state = WhatsAppThreadState(thread_id=thread.id)
        thread.state = state
        db.add(state)

    for field, value in changes.items():
        if value is not None:
            setattr(state, field, value)

    try:
        db.commit()
    except (DataError, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="No se pudo persistir el estado del thread") from exc

    db.refresh(state)
    return state
