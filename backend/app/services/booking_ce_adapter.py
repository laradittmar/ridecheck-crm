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

The CE-W2 rule is the important one: a time CE has already judged unavailable must never
become an OCCUPYING booking. Under SB1 that write can lose an overlap race and roll back the
whole turn — and with it the human escalation. `request_manual_scheduling` records the request
as non-occupying (`REPROGRAMAR`), so it cannot conflict, and the escalation always survives.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, time
from typing import Any, Callable, Mapping, Optional

from sqlalchemy.orm import Session

from .booking_authority import (
    BookingAuthority,
    BookingCommand,
    BookingOperation,
    BookingOutcome,
    BookingResult,
    BookingSource,
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
    #: The inbound message id of the Flow response — the event identity.
    inbound_event_id: str
    flow_token: Optional[str]
    requested_date: Optional[date]
    requested_time: Optional[time]
    #: Non-booking Revision fields CE already computes (vehicle, zone, address, seller…).
    revision_fields: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CeSchedulingEscalationRequest:
    """CE-W2: the customer insists on a time CE judged unavailable → human coordination."""
    thread_id: int
    lead_id: int
    #: Stable escalation identity, e.g. "<thread_id>:<cycle_key>" — one request per cycle.
    escalation_id: str
    requested_date: Optional[date]
    requested_time: Optional[time]
    revision_fields: Mapping[str, Any] = field(default_factory=dict)


def ce_flow_booking_key(req: CeFlowBookingRequest) -> str:
    """The Flow token identifies the offer; the inbound message id is the fallback identity."""
    if req.flow_token:
        return "ce-flow-response:" + hashlib.sha256(req.flow_token.encode()).hexdigest()[:48]
    return "ce-flow-response-msg:" + hashlib.sha256(req.inbound_event_id.encode()).hexdigest()[:48]


def ce_escalation_key(req: CeSchedulingEscalationRequest) -> str:
    return "ce-escalation:" + hashlib.sha256(req.escalation_id.encode()).hexdigest()[:48]


def book_from_flow_response(
    db: Session, req: CeFlowBookingRequest, *,
    create_thread_revision: Callable[[Session, Any], Any],
) -> BookingResult:
    """CE-W1. `create_thread_revision(db, revision)` writes CE's booked ThreadRevision and the
    lead/thread-state updates inside the authority's transaction and returns the
    ThreadRevision; CE keeps keying its `flow-txn:<ThreadRevision.id>` acknowledgement on the
    returned `thread_revision_id`.

    A response with no agreed slot cannot be a booking: it is recorded as a non-occupying
    scheduling request instead (it never could occupy the agenda).
    """
    if req.requested_date is None or req.requested_time is None:
        return BookingAuthority(db).execute(BookingCommand(
            operation=BookingOperation.REQUEST_MANUAL_SCHEDULING,
            source=BookingSource.CONVERSATION_ENGINE,
            idempotency_key=ce_flow_booking_key(req), lead_id=req.lead_id,
            thread_id=req.thread_id, requested_date=req.requested_date,
            requested_time=req.requested_time, revision_fields=req.revision_fields,
        ), attach=create_thread_revision)
    return BookingAuthority(db).execute(BookingCommand(
        operation=BookingOperation.CREATE,
        source=BookingSource.CONVERSATION_ENGINE,
        idempotency_key=ce_flow_booking_key(req), lead_id=req.lead_id, thread_id=req.thread_id,
        requested_date=req.requested_date, requested_time=req.requested_time,
        revision_fields=req.revision_fields, tracks_acknowledgement=True,
    ), attach=create_thread_revision)


def request_manual_scheduling(
    db: Session, req: CeSchedulingEscalationRequest, *,
    create_thread_revision: Optional[Callable[[Session, Any], Any]] = None,
) -> BookingResult:
    """CE-W2. Records the requested time as NON-OCCUPYING (`REPROGRAMAR`): it cannot collide
    with any booking, so the human escalation is never lost to an overlap. The optional
    `create_thread_revision` writes CE's provisional ThreadRevision in the same transaction.
    """
    return BookingAuthority(db).execute(BookingCommand(
        operation=BookingOperation.REQUEST_MANUAL_SCHEDULING,
        source=BookingSource.CONVERSATION_ENGINE,
        idempotency_key=ce_escalation_key(req), lead_id=req.lead_id, thread_id=req.thread_id,
        requested_date=req.requested_date, requested_time=req.requested_time,
        revision_fields=req.revision_fields,
    ), attach=create_thread_revision)
