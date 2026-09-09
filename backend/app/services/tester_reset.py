"""L4.7W5-F1 — reset the controlled tester without destroying transport evidence.

The reset used before this milestone deleted the tester's outbound `whatsapp_messages`
rows. Meta does not know we did that: it goes on sending `delivered` / `read` callbacks for
messages still sitting on the handset. On 2026-09-09 the owner opened a Flow ten minutes
after a reset and the callback landed on an empty ledger, raising a HIGH
`META_STATUS_FOR_UNKNOWN_WAMID` for a message the system itself had legitimately sent.

The detector was right. The reset was wrong: it destroyed the evidence needed to answer the
question the detector asks.

Business state and transport evidence have different lifetimes, so they are separated here:

  * conversation/business state  → deleted, so the next inbound is a genuinely new customer
  * outbound ledger rows (WAMIDs) → re-parented to a permanent archive thread

Status resolution matches on `wa_message_id` alone (`routes/whatsapp.py`), never on thread,
so an archived row still answers a callback correctly. A WAMID we never sent remains
genuinely unknown and still raises its security event — detection is not weakened, only
stopped from firing on our own history.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models import (
    AiEvent,
    Lead,
    Revision,
    ThreadRevision,
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppThread,
    WhatsAppThreadCandidate,
    WhatsAppThreadState,
)

logger = logging.getLogger(__name__)

# Synthetic holder for retired transport evidence. Not a real WhatsApp identity: it can
# never collide with a customer, and no inbound can ever route to it.
ARCHIVE_WA_ID = "__RESET_ARCHIVE__"
ARCHIVE_DISPLAY_NAME = "RESET ARCHIVE (transport evidence)"


@dataclass
class ResetResult:
    wa_id: str
    archived_outbound: int = 0
    deleted: dict[str, int] = field(default_factory=dict)
    archive_thread_id: int | None = None


def _archive_thread(db: Session) -> WhatsAppThread:
    """Return the permanent archive thread, creating it once."""
    contact = db.execute(
        select(WhatsAppContact).where(WhatsAppContact.wa_id == ARCHIVE_WA_ID)
    ).scalar_one_or_none()
    if contact is None:
        contact = WhatsAppContact(wa_id=ARCHIVE_WA_ID, display_name=ARCHIVE_DISPLAY_NAME)
        db.add(contact)
        db.flush()
    thread = db.execute(
        select(WhatsAppThread).where(WhatsAppThread.contact_id == contact.id)
    ).scalars().first()
    if thread is None:
        thread = WhatsAppThread(contact_id=contact.id)
        db.add(thread)
        db.flush()
    return thread


def reset_tester_to_zero_state(db: Session, wa_id: str) -> ResetResult:
    """Return the tester to zero state, preserving outbound WAMID attribution.

    Everything a conversation is made of is removed. What survives is the bare outbound
    ledger row — wamid, path_id, deployment_id, status — which is what a later Meta
    callback needs and what a forensic reader needs. It carries no conversational meaning
    once detached, so it cannot seed the next session.
    """
    result = ResetResult(wa_id=wa_id)

    contact = db.execute(
        select(WhatsAppContact).where(WhatsAppContact.wa_id == wa_id)
    ).scalar_one_or_none()
    if contact is None:
        return result

    thread_ids = list(db.execute(
        select(WhatsAppThread.id).where(WhatsAppThread.contact_id == contact.id)
    ).scalars().all())
    lead_ids = [
        lid for lid in db.execute(
            select(WhatsAppThread.lead_id).where(WhatsAppThread.contact_id == contact.id)
        ).scalars().all() if lid
    ]

    if thread_ids:
        # 1. Re-parent outbound evidence BEFORE anything is deleted. A row that was never
        #    transmitted (blocked, no wamid) carries no callback risk and is not kept.
        archive = _archive_thread(db)
        result.archive_thread_id = archive.id
        outbound = db.execute(
            select(WhatsAppMessage).where(
                WhatsAppMessage.thread_id.in_(thread_ids),
                WhatsAppMessage.direction == "out",
                WhatsAppMessage.wa_message_id.is_not(None),
                WhatsAppMessage.wa_message_id != "",
            )
        ).scalars().all()
        for message in outbound:
            message.thread_id = archive.id
            message.lead_id = None
        result.archived_outbound = len(outbound)
        db.flush()

    def _delete(model, condition, label: str) -> None:
        rows = db.execute(select(model).where(condition)).scalars().all()
        for row in rows:
            db.delete(row)
        db.flush()
        result.deleted[label] = len(rows)

    if thread_ids:
        _delete(AiEvent, AiEvent.thread_id.in_(thread_ids), "ai_events")
        _delete(ThreadRevision, ThreadRevision.thread_id.in_(thread_ids), "thread_revisions")
        _delete(WhatsAppMessage, WhatsAppMessage.thread_id.in_(thread_ids), "messages")
        _delete(WhatsAppThreadCandidate,
                WhatsAppThreadCandidate.thread_id.in_(thread_ids), "candidates")
        _delete(WhatsAppThreadState,
                WhatsAppThreadState.thread_id.in_(thread_ids), "thread_states")
    # AiEvent rows can also be keyed by wa_id alone (no thread), e.g. rejected inbound.
    _delete(AiEvent, AiEvent.wa_id == wa_id, "ai_events_by_wa_id")

    for table in ("whatsapp_outbound_dedup", "whatsapp_recipient_locks"):
        try:
            db.execute(text(f"DELETE FROM {table} WHERE wa_id = :wa"), {"wa": wa_id})
        except Exception:                       # table may not exist in a test schema
            logger.debug("reset: %s not present", table)
    db.flush()

    if lead_ids:
        _delete(Revision, Revision.lead_id.in_(lead_ids), "revisions")
    if thread_ids:
        _delete(WhatsAppThread, WhatsAppThread.id.in_(thread_ids), "threads")
    if lead_ids:
        _delete(Lead, Lead.id.in_(lead_ids), "leads")
    db.delete(contact)
    result.deleted["contacts"] = 1

    db.commit()
    logger.info(
        "TESTER_RESET wa_id=...%s archived_outbound=%s deleted=%s archive_thread=%s",
        wa_id[-4:], result.archived_outbound, result.deleted, result.archive_thread_id,
    )
    return result
