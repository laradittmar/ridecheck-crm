"""SB2B Option B — the two OPERATOR-controlled recovery entries (D1 §8.5).

Nothing here runs on its own: there is no worker, no schedule and no HTTP endpoint. An
operator who finds a row on `/control` → «Reservas que requieren atención» uses one of:

* `stored_inbound_event(db, wamid)` — rebuild the stored inbound Flow submission exactly as
  the webhook handed it to the Conversation Engine, for a re-POST to
  `/api/conversation/handle`. Safe to repeat: the booking ledger is consulted before anything
  else (B-1/B-2), a committed booking is replayed and never booked twice, and the
  acknowledgement is the ledger's lease (B-5).
* `recover_acknowledgement(db, key)` — when only the acknowledgement is stranded (the CE turn
  finished): claim the ledger lease and send the owner-approved acknowledgement once. Needs no
  CE context and creates no booking.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def stored_inbound_event(db: Session, wa_message_id: str):
    """The `ConversationHandleIn` for a stored inbound Flow submission, or None.

    Parsed from `whatsapp_messages.raw_payload` with the webhook's own rules
    (`routes/whatsapp.py`: `interactive.nfm_reply.response_json`, `flow_token` inside it).
    """
    from ..models import WhatsAppContact, WhatsAppMessage, WhatsAppThread
    from ..schemas.conversation import ConversationHandleIn
    msg = db.execute(select(WhatsAppMessage).where(
        WhatsAppMessage.wa_message_id == wa_message_id,
        WhatsAppMessage.direction == "in")).scalar_one_or_none()
    if msg is None or msg.thread_id is None:
        return None
    raw = msg.raw_payload
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    interactive = (raw or {}).get("interactive") if isinstance(raw, dict) else None
    nfm = (interactive or {}).get("nfm_reply") if isinstance(interactive, dict) else None
    if not isinstance(nfm, dict):
        return None
    try:
        flow_data = json.loads(str(nfm.get("response_json") or "")) or {}
    except json.JSONDecodeError:
        flow_data = {}
    if not isinstance(flow_data, dict):
        flow_data = {}
    thread = db.get(WhatsAppThread, msg.thread_id)
    contact = db.get(WhatsAppContact, thread.contact_id) if thread is not None else None
    if contact is None:
        return None
    return ConversationHandleIn(
        thread_id=msg.thread_id, wa_message_id=wa_message_id, wa_id=contact.wa_id,
        text=None, message_type="flow_response", flow_response=flow_data,
        flow_token=str(flow_data.get("flow_token") or "").strip() or None)


def recover_acknowledgement(db: Session, idempotency_key: str) -> Optional[str]:
    """Ledger-driven acknowledgement recovery. Returns the acknowledgement status after the
    attempt (None if the key is unknown). SENT, an active lease, exhausted attempts and
    NOT_APPLICABLE are left alone — the claim decides, atomically, not this function."""
    from ..models import BookingCommandRecord, WhatsAppContact, WhatsAppThread
    from .booking_authority import BookingAuthority
    from .booking_ce_adapter import isolated_session
    from .booking_flow_service import send_booking_acknowledgement
    with isolated_session(db) as s:
        auth = BookingAuthority(s)
        found = s.execute(select(BookingCommandRecord.id, BookingCommandRecord.thread_id).where(
            BookingCommandRecord.idempotency_key == idempotency_key)).one_or_none()
        if found is None:
            return None
        thread_id = found.thread_id
        claim = auth.claim_acknowledgement(idempotency_key)
        if claim is None:
            after = auth.lookup(idempotency_key)
            return after.ack_status if after else None
        thread = s.get(WhatsAppThread, thread_id) if thread_id is not None else None
        contact = s.get(WhatsAppContact, thread.contact_id) if thread is not None else None
        if contact is None:
            auth.record_acknowledgement(claim, sent=False, error="CONTEXT_MISSING")
        else:
            outcome = send_booking_acknowledgement(
                db, wa_id=contact.wa_id, thread_id=thread_id,
                delivery_fingerprint=claim.delivery_fingerprint)
            auth.record_acknowledgement(
                claim, sent=bool(outcome), transport_id=outcome.wamid,
                error=outcome.error or (None if outcome else "NOT_SENT"),
                ambiguous=outcome.ambiguous)
        after = auth.lookup(idempotency_key)
        return after.ack_status if after else None
