"""SB2A → SB2B: the stable interface the Conversation Engine will call.

SB2A does not edit `conversation_engine.py` (Track A owns it). This module is the smallest
adapter its two booking writers need, so SB2B is pure wiring: replace each direct
`Revision(...)` + commit with one call here, and act on the typed result.

It is NOT a second authority. Both functions build a `BookingCommand` and call
`BookingAuthority.execute`, which owns the transaction, the locks, the checks and the commit.
`docs/architecture/SB2B_CE_INTEGRATION_MANIFEST.json` maps each CE call site to its function
here, and a test keeps the mapping mechanically true.

CE-W1  `_process_flow_response`        → `book_from_flow_response`
CE-W2  `_handle_scheduling_escalation` → `request_manual_scheduling`

SB2A-R2 corrections:

* **Isolation (SB2A-R-02).** The authority commits and rolls back. Run on the caller's
  session it would commit the Conversation Engine's half-finished turn on success and erase
  its pending work on a refusal. Every adapter call therefore runs in its OWN short-lived
  session on its own connection, closed before returning. The caller's session is never
  flushed, committed or rolled back here.
* **Identity (SB2A-R-09).** Every command needs a stable, non-empty event identity, scoped
  to the thread. With none, the call fails closed (`VALIDATION_FAILED /
  EVENT_IDENTITY_REQUIRED`) without touching the database; an empty string is never
  hashed into a shared key.
* **Partial slots (SB2A-R-01).** A manual-scheduling request stores whatever part of the
  requested slot is known — complete, date only, time only or nothing — as non-occupying
  `REPROGRAMAR`. It can never collide, so the human escalation always survives.

SB2A-R3: no callback. CE describes its associated writes (the ThreadRevision, lead and thread
state) as a typed `BookingAttachmentPlan`; the booking authority performs them inside its
own transaction. The binding SB2B rules are in the integration manifest.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, time
from typing import Any, Mapping, Optional

from sqlalchemy.orm import Session

from .booking_authority import (
    DEFAULT_LOCK_TIMEOUT_MS,
    BookingAttachmentPlan,
    BookingAuthority,
    BookingCommand,
    BookingOperation,
    BookingOutcome,
    BookingResult,
    BookingSource,
    refuse,
)

#: What SB2B must do with each CE-W1 result. Anything not COMMITTED/IDEMPOTENT_REPLAY books
#: nothing; a slot that turned out to be unavailable falls back to a manual-scheduling request
#: so the customer is still handed to a human.
CE_W1_FALLBACK_TO_MANUAL = frozenset({BookingOutcome.SLOT_CONFLICT,
                                      BookingOutcome.CALENDAR_EXCEPTION})


@dataclass(frozen=True)
class CeFlowBookingRequest:
    """CE-W1: a booking Flow response received as a WhatsApp message (nfm_reply)."""
    thread_id: int
    lead_id: int
    #: The inbound message id of the Flow response (`ctx.inbound_wa_message_id`).
    inbound_event_id: Optional[str]
    flow_token: Optional[str]
    requested_date: Optional[date] = None
    requested_time: Optional[time] = None
    #: Non-booking Revision fields CE already computes (vehicle, zone, address, seller…).
    revision_fields: Mapping[str, Any] = field(default_factory=dict)
    #: Typed associated writes (booked ThreadRevision, lead, thread state) — data only.
    attachments: Optional[BookingAttachmentPlan] = None


@dataclass(frozen=True)
class CeSchedulingEscalationRequest:
    """CE-W2: the customer insists on a time CE judged unavailable → human coordination."""
    thread_id: int
    lead_id: int
    #: Explicit escalation identity, e.g. "<thread_id>:<cycle_key>". Optional when
    #: `cycle_key` or `inbound_event_id` identifies the event instead.
    escalation_id: Optional[str] = None
    requested_date: Optional[date] = None
    requested_time: Optional[time] = None
    revision_fields: Mapping[str, Any] = field(default_factory=dict)
    #: `COALESCE(state.current_cycle_start_message_db_id, 0)` — one escalation per cycle.
    cycle_key: Optional[int] = None
    inbound_event_id: Optional[str] = None
    #: Typed associated writes (provisional ThreadRevision, lead, thread state) — data only.
    attachments: Optional[BookingAttachmentPlan] = None


def _nonblank(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    return value or None


def _key(prefix: str, thread_id: int, kind: str, identity: str) -> str:
    return f"{prefix}:" + hashlib.sha256(f"{int(thread_id)}|{kind}|{identity}".encode()).hexdigest()[:48]


def ce_flow_booking_key(req: CeFlowBookingRequest) -> Optional[str]:
    """Flow token, else inbound message id — both thread-scoped. None if neither exists."""
    token = _nonblank(req.flow_token)
    if token:
        return _key("ce-flow-response", req.thread_id, "token", token)
    msg = _nonblank(req.inbound_event_id)
    if msg:
        return _key("ce-flow-response", req.thread_id, "msg", msg)
    return None


def ce_escalation_key(req: CeSchedulingEscalationRequest) -> Optional[str]:
    """Explicit escalation id, else the cycle, else the inbound message — thread-scoped."""
    explicit = _nonblank(req.escalation_id)
    if explicit:
        return _key("ce-escalation", req.thread_id, "esc", explicit)
    if req.cycle_key is not None:
        return _key("ce-escalation", req.thread_id, "cycle", str(int(req.cycle_key)))
    msg = _nonblank(req.inbound_event_id)
    if msg:
        return _key("ce-escalation", req.thread_id, "msg", msg)
    return None


@contextmanager
def isolated_session(db: Session):
    """A dedicated short-lived session on its own connection — never the caller's.

    `db` only names the database: its engine is reused, its session and transaction are not.
    The session is closed (and its connection returned) whatever happens.
    """
    bind = db.get_bind()
    engine = getattr(bind, "engine", bind)
    session = Session(bind=engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()


def _no_identity() -> BookingResult:
    return refuse(BookingOutcome.VALIDATION_FAILED, "EVENT_IDENTITY_REQUIRED")


def book_from_flow_response(
    db: Session, req: CeFlowBookingRequest, *,
    lock_timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS,
) -> BookingResult:
    """CE-W1. Books the agreed slot and performs `req.attachments` (typically the booked
    ThreadRevision, lead and thread-state updates) in the authority's transaction; CE keeps
    keying its `flow-txn:<ThreadRevision.id>` acknowledgement on `result.thread_revision_id`.

    A response without a complete agreed slot cannot be a booking: it is recorded as a
    non-occupying manual-scheduling request with whatever part of the slot is known.
    """
    key = ce_flow_booking_key(req)
    if key is None:
        return _no_identity()
    complete = req.requested_date is not None and req.requested_time is not None
    command = BookingCommand(
        operation=BookingOperation.CREATE if complete else BookingOperation.REQUEST_MANUAL_SCHEDULING,
        source=BookingSource.CONVERSATION_ENGINE, idempotency_key=key, lead_id=req.lead_id,
        thread_id=req.thread_id, requested_date=req.requested_date,
        requested_time=req.requested_time, revision_fields=req.revision_fields,
        tracks_acknowledgement=complete)
    with isolated_session(db) as session:
        return BookingAuthority(session, lock_timeout_ms=lock_timeout_ms).execute(
            command, attachments=req.attachments)


def request_manual_scheduling(
    db: Session, req: CeSchedulingEscalationRequest, *,
    lock_timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS,
) -> BookingResult:
    """CE-W2. Records the requested time — complete, partial or absent — as NON-OCCUPYING
    (`REPROGRAMAR`): it cannot collide with any booking, so the human escalation is never
    lost to an overlap or to an incomplete slot. `req.attachments` may add CE's provisional
    ThreadRevision (non-occupying status) in the same transaction.
    """
    key = ce_escalation_key(req)
    if key is None:
        return _no_identity()
    command = BookingCommand(
        operation=BookingOperation.REQUEST_MANUAL_SCHEDULING,
        source=BookingSource.CONVERSATION_ENGINE, idempotency_key=key, lead_id=req.lead_id,
        thread_id=req.thread_id, requested_date=req.requested_date,
        requested_time=req.requested_time, revision_fields=req.revision_fields)
    with isolated_session(db) as session:
        return BookingAuthority(session, lock_timeout_ms=lock_timeout_ms).execute(
            command, attachments=req.attachments)
