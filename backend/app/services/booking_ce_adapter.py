"""SB2A → SB2B: the stable interface the Conversation Engine will call.

SB2A does not edit `conversation_engine.py` (Track A owns it). This module is the smallest
adapter its two booking writers need, so SB2B is pure wiring: replace each direct
`Revision(...)` + commit with one call here, and act on the typed result.

It is NOT a second authority. Both functions build a `BookingCommand` and call
`BookingAuthority.execute`, which owns the transaction, the locks, the checks and the commit.
`docs/architecture/SB2B_CE_INTEGRATION_MANIFEST.json` maps each CE call site to its function
here, and a test keeps the mapping mechanically true.

CE-W1  `_process_flow_response` → `_converged_flow_book` → `book_from_flow_response`
CE-W2  `_handle_scheduling_escalation` → `_sb_escalation` → `request_manual_scheduling`

Convergence Phase 3: these are the ONLY Conversation Engine booking writers. The Data
Exchange confirmation calls `BookingAuthority.execute` directly with the same `flow:` key.

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

SB2B Option B (D1 §8.2, binding rules B-1..B-8) — what the adapter now enforces itself:

* **B-1 one key per Flow submission.** A token-bearing Flow response is keyed exactly like the
  Data Exchange confirmation, `flow_idempotency_key(token)`, so the two writers of one Flow
  submission share ONE ledger row and can never both book. `ce-flow-response:` survives only
  for a genuinely token-less event. `lookup_booking` is the read CE performs before any
  execute.
* **B-3 CE-W2 plans are booking-owned only.** CE-W2 runs after arbitrary CE flushes, so a
  lead or thread-state write in its plan would wait on CE's own uncommitted row lock. Such a
  plan is refused before any database access.
* **B-7 availability.** An occupying CE-W1 command carries the same `AvailabilityRevalidation`
  as the endpoint path; without one it is refused, so CE can never book a stale slot.
* **R7-O1 / R7RV-04 metadata ownership.** CE may set only the non-booking Revision fields of
  the approved booking plan (`CE_REVISION_FIELDS`). Timestamps, approval workflow state, ids,
  booking-version and canonical booking columns are refused before any database access.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, time
from typing import Any, Mapping, Optional

from sqlalchemy.orm import Session

from .booking_authority import (
    DEFAULT_LOCK_TIMEOUT_MS,
    AvailabilityRevalidation,
    BookingAttachmentPlan,
    BookingAuthority,
    BookingCommand,
    BookingOperation,
    BookingOutcome,
    BookingResult,
    BookingSource,
    flow_idempotency_key,
    refuse,
)

#: What SB2B must do with each CE-W1 result. Anything not COMMITTED/IDEMPOTENT_REPLAY books
#: nothing; a slot that turned out to be unavailable falls back to a manual-scheduling request
#: so the customer is still handed to a human.
CE_W1_FALLBACK_TO_MANUAL = frozenset({BookingOutcome.SLOT_CONFLICT,
                                      BookingOutcome.CALENDAR_EXCEPTION})

#: SB2B (R7-O1 / R7RV-04 carried forward): the ONLY Revision columns a Conversation Engine
#: command may set — the non-booking fields of the approved booking plan, i.e. exactly what
#: the Data Exchange confirmation (`booking_flow_service.handle_confirm_booking`) and the two
#: legacy CE writers already write. Everything else is refused before any database access:
#: `created_at`, the appointment-approval workflow (`appointment_approval_*`), ids,
#: `booking_version` and the canonical booking columns the authority itself owns.
CE_REVISION_FIELDS = frozenset({
    "tipo_vehiculo", "marca", "modelo", "anio", "zone_group", "zone_detail",
    "direccion_texto", "vendedor_tipo", "tipo_vendedor", "turno_notas",
})


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
    #: SB2B B-7: the authority's own `ScheduleService` revalidation inputs. Required for an
    #: occupying booking; the check itself runs inside the authority, under its locks.
    availability: Optional[AvailabilityRevalidation] = None


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


#: SB2A-R4: request fields admitted by exact type before ANY use (the key derivation below
#: would otherwise apply truthiness/strip to caller objects). kind, nullable.
_REQUEST_SCHEMA = {
    "CeFlowBookingRequest": {"thread_id": ("int", False), "lead_id": ("int", False),
                             "inbound_event_id": ("str", True), "flow_token": ("str", True),
                             "requested_date": ("date", True), "requested_time": ("time", True),
                             "revision_fields": ("dict", False), "attachments": ("plan", True),
                             "availability": ("avail", True)},
    "CeSchedulingEscalationRequest": {"thread_id": ("int", False), "lead_id": ("int", False),
                                      "escalation_id": ("str", True), "requested_date": ("date", True),
                                      "requested_time": ("time", True), "revision_fields": ("dict", False),
                                      "cycle_key": ("int", True), "inbound_event_id": ("str", True),
                                      "attachments": ("plan", True)},
}


def _capture_request(req, cls) -> Optional[dict]:
    """SB2A-R5: capture the CE request ONCE into authority-owned values, then validate the
    capture. Returns a plain dict (revision_fields a NEW dict, attachments a REBUILT plan) or
    None. After this, the adapter never reads `req` again."""
    from . import booking_authority as ba
    vals = ba._owned_fields(req, cls)                    # one atomic dict.copy, exact type only
    schema = _REQUEST_SCHEMA[cls.__name__]
    if vals is None or set(vals) != set(schema):
        return None
    rf = vals["revision_fields"]
    vals["revision_fields"] = dict.copy(rf) if type(rf) is dict else None
    att = ba.snapshot_attachment_plan(vals["attachments"])
    if att is ba._CAPTURE_FAILED:
        return None
    vals["attachments"] = att
    if "availability" in vals:
        avail = ba._capture_optional(vals["availability"], ba.AvailabilityRevalidation)
        if avail is ba._CAPTURE_FAILED:
            return None
        vals["availability"] = avail
    exact = {"int": int, "str": str, "date": date, "time": time, "dict": dict,
             "plan": ba.BookingAttachmentPlan, "avail": ba.AvailabilityRevalidation}
    for name, (kind, nullable) in schema.items():       # validate the CAPTURE
        v = vals[name]
        if v is None:
            if not nullable:
                return None
        elif type(v) is not exact[kind] or (kind == "int" and v < 0):
            return None
    return vals


def _nonblank(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    return value or None


def _key(prefix: str, thread_id: int, kind: str, identity: str) -> str:
    return f"{prefix}:" + hashlib.sha256(f"{int(thread_id)}|{kind}|{identity}".encode()).hexdigest()[:48]


def _flow_key(v: dict) -> Optional[str]:
    token = _nonblank(v["flow_token"])
    if token:
        # SB2B B-1 (D1-01): the Data Exchange endpoint's key for the same Flow submission.
        # One token, one ledger row, whichever writer arrives first.
        return flow_idempotency_key(token)
    msg = _nonblank(v["inbound_event_id"])
    if msg:
        return _key("ce-flow-response", v["thread_id"], "msg", msg)
    return None


def flow_booking_key(thread_id: int, flow_token: Optional[str],
                     inbound_event_id: Optional[str]) -> Optional[str]:
    """The B-1 key CE looks up BEFORE anything else — the very key `book_from_flow_response`
    executes under. None when the event has no identity (or is not plain data)."""
    if type(thread_id) is not int or thread_id < 0:
        return None
    for value in (flow_token, inbound_event_id):
        if value is not None and type(value) is not str:
            return None
    return _flow_key({"thread_id": thread_id, "flow_token": flow_token,
                      "inbound_event_id": inbound_event_id})


def lookup_booking(db: Session, key: Optional[str]) -> Optional[BookingResult]:
    """SB2B B-1/B-2: the committed result for `key`, read in an isolated session (CE's own
    session is not touched, so the read cannot assign CE a transaction id). None for a miss
    or a missing key. Read-only: not counted as a replay.

    Convergence Phase 2: only a REPLAYABLE result is a hit. A key whose ledger row a tester
    reset DETACHED answers `COMMAND_DETACHED` from the authority; to CE that is not a booking
    to replay, acknowledge or synchronise, so it is a miss here — and the key, still consumed,
    makes any booking attempt under it a terminal refusal inside the authority."""
    if type(key) is not str or not key:
        return None
    with isolated_session(db) as session:
        found = BookingAuthority(session).lookup(key)
    return found if found is not None and found.outcome is BookingOutcome.IDEMPOTENT_REPLAY else None


def inbound_correlation(inbound_event_id: Optional[str]) -> Optional[str]:
    """A ledger `correlation_id` naming the inbound event that caused a CE command, without
    storing the WAMID itself: `in:` + 32 hex of its SHA-256. Lets an operator surface tell
    whether CE finished synchronizing after the booking committed."""
    msg = _nonblank(inbound_event_id) if type(inbound_event_id) is str else None
    return None if msg is None else "in:" + hashlib.sha256(msg.encode()).hexdigest()[:32]


def _fields_refusal(fields: dict) -> Optional[BookingResult]:
    """R7-O1 / R7RV-04: refuse any Revision field outside `CE_REVISION_FIELDS`, before SQL."""
    for k in fields:
        # SB2B-RV-02 (convergence Phase 3): a key is admitted by EXACT type before any use. A
        # `str` subclass would run its own `__hash__`/`__eq__` inside the membership test and
        # its own `__format__` in the reason, so it is refused unread.
        if type(k) is not str:
            return refuse(BookingOutcome.VALIDATION_FAILED, "CE_FIELD_KEY_NOT_STR")
        if k not in CE_REVISION_FIELDS:
            return refuse(BookingOutcome.VALIDATION_FAILED, f"CE_FIELD_NOT_ALLOWED:{k}"[:64])
    return None


def _escalation_key(v: dict) -> Optional[str]:
    explicit = _nonblank(v["escalation_id"])
    if explicit:
        return _key("ce-escalation", v["thread_id"], "esc", explicit)
    if v["cycle_key"] is not None:
        return _key("ce-escalation", v["thread_id"], "cycle", str(int(v["cycle_key"])))
    msg = _nonblank(v["inbound_event_id"])
    if msg:
        return _key("ce-escalation", v["thread_id"], "msg", msg)
    return None


def ce_flow_booking_key(req: CeFlowBookingRequest) -> Optional[str]:
    """Flow token, else inbound message id — both thread-scoped. None if neither exists (or
    the request is not exactly data)."""
    v = _capture_request(req, CeFlowBookingRequest)
    return None if v is None else _flow_key(v)


def ce_escalation_key(req: CeSchedulingEscalationRequest) -> Optional[str]:
    """Explicit escalation id, else the cycle, else the inbound message — thread-scoped."""
    v = _capture_request(req, CeSchedulingEscalationRequest)
    return None if v is None else _escalation_key(v)


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
    ThreadRevision, lead and thread-state updates) in the authority's transaction. SB2B
    B-5: the acknowledgement is the ledger row's (`claim_acknowledgement` on this key), not a
    `flow-txn:<ThreadRevision.id>` outbound lookup.

    A response without a complete agreed slot cannot be a booking: it is recorded as a
    non-occupying manual-scheduling request with whatever part of the slot is known.
    """
    v = _capture_request(req, CeFlowBookingRequest)
    del req                                              # caller reference ends here (R5)
    if v is None:
        return refuse(BookingOutcome.VALIDATION_FAILED, "REQUEST_NOT_DATA")
    key = _flow_key(v)
    if key is None:
        return _no_identity()
    refused = _fields_refusal(v["revision_fields"])
    if refused is not None:
        return replace(refused, idempotency_key=key)
    complete = v["requested_date"] is not None and v["requested_time"] is not None
    if complete and v["availability"] is None:           # SB2B B-7
        return refuse(BookingOutcome.VALIDATION_FAILED, "AVAILABILITY_REVALIDATION_REQUIRED",
                      idempotency_key=key)
    command = BookingCommand(
        operation=BookingOperation.CREATE if complete else BookingOperation.REQUEST_MANUAL_SCHEDULING,
        source=BookingSource.CONVERSATION_ENGINE, idempotency_key=key, lead_id=v["lead_id"],
        thread_id=v["thread_id"], requested_date=v["requested_date"],
        requested_time=v["requested_time"], revision_fields=v["revision_fields"],
        correlation_id=inbound_correlation(v["inbound_event_id"]),
        tracks_acknowledgement=complete)
    with isolated_session(db) as session:
        return BookingAuthority(session, lock_timeout_ms=lock_timeout_ms).execute(
            command, availability=v["availability"] if complete else None,
            attachments=v["attachments"])


def request_manual_scheduling(
    db: Session, req: CeSchedulingEscalationRequest, *,
    lock_timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS,
) -> BookingResult:
    """CE-W2. Records the requested time — complete, partial or absent — as NON-OCCUPYING
    (`REPROGRAMAR`): it cannot collide with any booking, so the human escalation is never
    lost to an overlap or to an incomplete slot. `req.attachments` may add CE's provisional
    ThreadRevision (non-occupying status) in the same transaction.
    """
    v = _capture_request(req, CeSchedulingEscalationRequest)
    del req                                              # caller reference ends here (R5)
    if v is None:
        return refuse(BookingOutcome.VALIDATION_FAILED, "REQUEST_NOT_DATA")
    key = _escalation_key(v)
    if key is None:
        return _no_identity()
    refused = _fields_refusal(v["revision_fields"])
    if refused is not None:
        return replace(refused, idempotency_key=key)
    plan = v["attachments"]
    # SB2B B-3: booking-owned rows only. CE performs needs_human, the canonical state and the
    # token withdrawal in its own transaction; the authority never locks a lead or thread-state
    # row CE may already hold. No approval workflow is started from an escalation either.
    if plan is not None and (plan.lead is not None or plan.thread_state is not None or (
            plan.thread_revision is not None and plan.thread_revision.request_approval)):
        return refuse(BookingOutcome.VALIDATION_FAILED, "CE_W2_PLAN_NOT_BOOKING_OWNED",
                      idempotency_key=key)
    command = BookingCommand(
        operation=BookingOperation.REQUEST_MANUAL_SCHEDULING,
        source=BookingSource.CONVERSATION_ENGINE, idempotency_key=key, lead_id=v["lead_id"],
        thread_id=v["thread_id"], requested_date=v["requested_date"],
        requested_time=v["requested_time"], revision_fields=v["revision_fields"],
        correlation_id=inbound_correlation(v["inbound_event_id"]))
    with isolated_session(db) as session:
        return BookingAuthority(session, lock_timeout_ms=lock_timeout_ms).execute(
            command, attachments=v["attachments"])
