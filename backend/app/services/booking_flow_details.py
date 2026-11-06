"""PRELAUNCH-MINIMUM-LAUNCH (B) — the Booking Flow's DETAILS, kept before the booking commits.

`prepare_summary` is the first and only server-side sighting of what the customer typed on the
DETAILS screen — name, e-mail, inspection address, seller name and phone, listing URL — and of
the date and time they picked. Until this module it was a pure function: the values were
echoed onto the SUMMARY screen and discarded. The 2026-11-01 Wild measured the consequence: the
customer who lost the same-slot race had reached SUMMARY, so their details HAD reached the
server, and none of them survived the refusal.

They are now persisted at `prepare_summary`, in the existing provenance store
(`whatsapp_thread_evidence`, the persistence of a typed claim with producer, cycle, turn,
status and supersession), not in a new table and not in a booking table:

* **Bound** to the thread the token resolved, its current cycle, and the OFFER-BOUND candidate
  (`bound_to_candidate_id`); `turn_id` names the offer. Nothing is read from the payload to
  decide whose data this is.
* **Draft, never authority.** `outcome='HOLD'`, `status='DRAFT'`, producer
  `BOOKING_FLOW_DETAILS`. No locality, vehicle or booking decision reads these rows: the
  locality binder reads `role='INSPECTION_LOCATION'` with `outcome='ACCEPT'` only, and these
  rows carry neither. The booking still writes its own canonical copy when it commits.
* **Idempotent, within one attempt.** An attempt is one candidate in one cycle. A row's identity
  is a hash of (thread, claim type, value, the row it replaces, candidate, cycle). The same
  submission replayed derives the same identities and writes nothing; the table's UNIQUE
  `(thread_id, claim_id)` is the arbiter if two replays race. The same VALUE in another attempt
  is a new fact: a person's e-mail typed for a second car belongs to that attempt too, so it
  gets its own row.
* **Superseded, never duplicated.** A corrected value is a NEW row and the previous live row of
  that claim type IN THE SAME ATTEMPT gets `superseded_by_id` — the old value stays readable as
  history. Another attempt's row is never touched: it stays live and bound to its own candidate
  and cycle, so a thread can hold one live row of a claim type per attempt. An optional field
  submitted empty is "not given this time", not a correction: the previous value stands.
* **No side effect beyond the rows.** No booking, revision, offer, lead or message is written
  and nothing is sent. Serialised per thread by K1 (the thread-state row, bounded) — the same
  first lock every offer writer takes — in its own short session, never the request's.
* **Privacy.** Values are customer-supplied service facts (seller data is a third party's) and
  live only in these rows. Logs carry counts and ids, never a value.

R1 (review F-03): the same function runs AGAIN at `confirm_booking`, before the booking
authority (producer version `confirm_booking/1`), so the details no longer depend on one
earlier request; a replay writes nothing and a correction supersedes, exactly as above. When
the confirm-time write fails, `record_details_condition` records a typed
DETAILS_NOT_PERSISTED condition (`booking_attention_conditions`) and raises the human flags,
so the loss is never silent. A COMMITTED booking also carries its own canonical copy in
`thread_revisions` (incl. `seller_phone`, written by the booking transaction).

VEHICLE IDENTITY + RECOVERY PREFILL (milestone PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY):
`vehicle_mileage` and `vehicle_plate` are two more claims of the same draft, stored canonical
(`services/vehicle_identity`); their committed home is `revisions.kilometraje` / `.dominio`.

These rows are also what a refreshed Flow reads back (`prefill_details`), so a customer who lost
a slot does not retype anything. The read is scoped, and that scope is the safety property:
this thread, this offer's candidate, this cycle. A thread holds the rows of every attempt, so
without it a second car on the same thread would be offered the first car's plate.

Once prefill is on, a blank optional field can be a deliberate correction: the customer saw the
value and removed it. Only then, and only over a value they could have seen, a blank is
recorded as an empty row superseding the old one (`clear_blank`). With prefill off the rule
above stands: blank is "not given this time".
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

logger = logging.getLogger(__name__)

PRODUCER = "BOOKING_FLOW_DETAILS"
PRODUCER_VERSION = "prepare_summary/1"
STATUS = "DRAFT"
OUTCOME = "HOLD"
REASON = "UNCONFIRMED_UNTIL_BOOKING"
EVIDENCE_CLASS = "CUSTOMER_FORM"
LOCK_TIMEOUT_MS = 1500
VALUE_MAX = 500

#: Flow payload key → claim type. The order is the display order.
CLAIM_TYPES = (
    ("name", "FLOW_CUSTOMER_NAME"),
    ("email", "FLOW_CUSTOMER_EMAIL"),
    ("inspection_address", "FLOW_INSPECTION_ADDRESS"),
    ("seller_name", "FLOW_SELLER_NAME"),
    ("seller_phone", "FLOW_SELLER_PHONE"),
    ("listing_url", "FLOW_LISTING_URL"),
    ("vehicle_mileage", "FLOW_VEHICLE_MILEAGE"),
    ("vehicle_plate", "FLOW_VEHICLE_PLATE"),
    ("slot", "FLOW_REQUESTED_SLOT"),
)
CLAIM_TYPE_FOR = dict(CLAIM_TYPES)
ALL_CLAIM_TYPES = tuple(ct for _, ct in CLAIM_TYPES)

#: What a refreshed Flow may offer back: every detail the customer typed. The slot is excluded
#: on purpose — it is what was refused, and it is chosen again from what is free now.
PREFILL_KEY_FOR = {ct: key for key, ct in CLAIM_TYPES if key != "slot"}
#: The Flow's optional inputs, the only ones a customer can empty. `name` and
#: `inspection_address` are required by the screen.
CLEARABLE_KEYS = frozenset({"email", "seller_name", "seller_phone", "listing_url",
                            "vehicle_mileage", "vehicle_plate"})


@dataclass(frozen=True)
class PersistResult:
    persisted: bool          # the transaction committed (possibly with nothing new)
    written: int = 0         # new rows
    superseded: int = 0      # previous live rows superseded by a correction
    reason: Optional[str] = None


def _clean(value) -> str:
    return " ".join(str(value if value is not None else "").split())[:VALUE_MAX]


def details_prefill_enabled() -> bool:
    """BOOKING_FLOW_DETAILS_PREFILL_ENABLED, with the exact `is True` test: anything else is
    OFF. On only together with the Flow that declares `details_prefill`."""
    from ..settings import get_settings
    return getattr(get_settings(), "booking_flow_details_prefill_enabled", False) is True


def prefill_contract_defaults() -> dict:
    """The declared-but-empty prefill key for a screen with no context, on the same switch."""
    return {"details_prefill": {}} if details_prefill_enabled() else {}


def flow_vehicle_identity(data: dict) -> "tuple[Optional[int], Optional[str]]":
    """(kilometraje, dominio) of a Flow payload. Raises `ValueError` on a malformed value;
    absent or blank is None, which is all an older Flow can ever send."""
    from .vehicle_identity import normalize_dominio, parse_kilometraje
    data = data or {}
    return (parse_kilometraje(data.get("vehicle_mileage")),
            normalize_dominio(data.get("vehicle_plate")))


def submitted_values(data: dict, *, date_str: str, time_str: str,
                     clear_blank: bool = False) -> dict:
    """The claim type → value map of one prepare_summary payload (empty values dropped).

    `clear_blank`: an optional key that IS in the payload and is blank maps to "" — "the
    customer emptied this" — which `write_detail_rows` applies only over a value they could
    have seen. An absent key is never a clearing: an older Flow simply does not send it."""
    data = data or {}
    mileage, plate = flow_vehicle_identity(data)
    out = {}
    for key, claim_type in CLAIM_TYPES:
        if key == "slot":
            value = f"{date_str} {time_str}".strip() if date_str and time_str else ""
        elif key == "vehicle_mileage":
            value = "" if mileage is None else str(mileage)
        elif key == "vehicle_plate":
            value = plate or ""
        else:
            value = _clean(data.get(key, ""))
        if value:
            out[claim_type] = value
        elif clear_blank and key in CLEARABLE_KEYS and key in data:
            out[claim_type] = ""
    return out


def _claim_id(thread_id: int, claim_type: str, value: str, predecessor_id,
              candidate_id: Optional[int], cycle: int) -> str:
    raw = "|".join(("flow-details:v2", str(int(thread_id)), claim_type, value,
                    str(predecessor_id or "-"),
                    str(candidate_id if candidate_id is not None else "-"), str(int(cycle))))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


def _live_rows(s, thread_id: int, candidate_id: Optional[int], cycle: int) -> dict:
    """The live rows of ONE attempt: what a submission for it can repeat or correct."""
    from ..models import WhatsAppThreadEvidence as E
    bound = (E.bound_to_candidate_id.is_(None) if candidate_id is None
             else E.bound_to_candidate_id == int(candidate_id))
    rows = s.execute(select(E).where(
        E.thread_id == int(thread_id), E.producer == PRODUCER,
        E.claim_type.in_(ALL_CLAIM_TYPES), E.superseded_by_id.is_(None),
        bound, E.cycle_key == int(cycle))
        .order_by(E.id)).scalars().all()
    live = {}
    for row in rows:                          # newest wins should two ever be live
        live[row.claim_type] = row
    return live


def _visible_to_customer(row, cycle: int, candidate_id: Optional[int]) -> bool:
    """Would `prefill_details` have offered this row to this customer? The same scope, so a
    blank can only clear what could actually have been on their screen."""
    return (row is not None and bool(row.value) and candidate_id is not None
            and row.bound_to_candidate_id == int(candidate_id) and row.cycle_key == int(cycle))


#: confirm_booking's K1 wait for the details write (R1). Deliberately SHORT: a contended
#: confirmation must reach the booking authority — and its own typed contention answer —
#: exactly as before, and a contended attempt writes nothing (Phase 3 p3r_10b). The write
#: that could not happen here is retried where K1 is already held (the slot-recovery
#: admission) or recorded after the commit (`BookingFlowService._settle_confirm_details`).
CONFIRM_LOCK_TIMEOUT_MS = 50


def write_detail_rows(s, *, thread_id: int, offer_id: Optional[int],
                      candidate_id: Optional[int], values: dict, stamp: datetime,
                      producer_version: str = PRODUCER_VERSION) -> "tuple[int, int]":
    """Write one submission's rows on session `s`, which the CALLER holds K1 on and commits.
    Returns (written, superseded). The same rules as `persist_flow_details`."""
    from ..models import WhatsAppThreadEvidence as E, WhatsAppThreadState
    from .clarification_attempt import cycle_key as _cycle_key
    state = s.execute(select(WhatsAppThreadState).where(
        WhatsAppThreadState.thread_id == int(thread_id))).scalar_one_or_none()
    cycle = int(_cycle_key(state)) if state is not None else 0
    live = _live_rows(s, thread_id, candidate_id, cycle)
    written = superseded = 0
    for claim_type in ALL_CLAIM_TYPES:
        value = values.get(claim_type)
        if value is None:
            continue
        previous = live.get(claim_type)
        if value == "" and not _visible_to_customer(previous, cycle, candidate_id):
            continue                                  # nothing they saw, so nothing cleared
        if previous is not None and previous.value == value:
            continue                                  # the same fact again: nothing new
        claim_id = _claim_id(thread_id, claim_type, value, getattr(previous, "id", None),
                             candidate_id, cycle)
        if s.execute(select(E.id).where(E.thread_id == int(thread_id),
                                        E.claim_id == claim_id)).first():
            continue                                  # a replay of this exact correction
        row = E(thread_id=int(thread_id), cycle_key=cycle,
                turn_id=(f"offer:{int(offer_id)}" if offer_id is not None else None),
                claim_type=claim_type, role=None, value=value,
                normalized_value=(value[:160] if claim_type == "FLOW_REQUESTED_SLOT" else None),
                evidence_class=EVIDENCE_CLASS, producer=PRODUCER,
                producer_version=producer_version[:32], status=STATUS, polarity=None,
                explicitness="EXPLICIT", outcome=OUTCOME, reason=REASON,
                source_message_ids=[], validated_as=None, claim_id=claim_id,
                bound_to_candidate_id=(int(candidate_id) if candidate_id is not None else None),
                bound_at=(stamp if candidate_id is not None else None), created_at=stamp)
        s.add(row)
        s.flush()
        written += 1
        if previous is not None:
            previous.superseded_by_id = int(row.id)
            superseded += 1
    return written, superseded


def persist_flow_details(db, *, thread_id: int, offer_id: Optional[int],
                         candidate_id: Optional[int], values: dict,
                         now: Optional[datetime] = None,
                         producer_version: str = PRODUCER_VERSION,
                         lock_timeout_ms: int = LOCK_TIMEOUT_MS) -> PersistResult:
    """Persist one submission's values (see the module docstring) in its own K1-first short
    session. Never raises."""
    from .booking_ce_adapter import isolated_session
    from .offer_lifecycle import OfferLifecycle
    if not values:
        return PersistResult(True)
    stamp = now or datetime.now(timezone.utc)
    try:
        with isolated_session(db) as s:
            contended = OfferLifecycle(s).begin_offer_transaction(
                int(thread_id), lock_timeout_ms=lock_timeout_ms)
            if contended is not None:
                logger.warning("BOOKING_FLOW_DETAILS not persisted thread_id=%s: %s",
                               thread_id, contended)
                return PersistResult(False, reason=str(contended))
            written, superseded = write_detail_rows(
                s, thread_id=thread_id, offer_id=offer_id, candidate_id=candidate_id,
                values=values, stamp=stamp, producer_version=producer_version)
            s.commit()
        logger.info("BOOKING_FLOW_DETAILS persisted thread_id=%s offer_id=%s written=%s "
                    "superseded=%s", thread_id, offer_id, written, superseded)
        return PersistResult(True, written, superseded)
    except Exception as exc:
        # A racing replay that inserted the same identity first lands here (UNIQUE); either
        # way the SUMMARY screen is still answered and nothing else is affected.
        logger.error("BOOKING_FLOW_DETAILS not persisted thread_id=%s: %s", thread_id,
                     type(exc).__name__)
        return PersistResult(False, reason=type(exc).__name__[:64])


CONDITION_KIND = "DETAILS_NOT_PERSISTED"


def record_details_condition(db, *, thread_id: int, lead_id: Optional[int], offer_id,
                             attempt_ref: Optional[str], reason: str,
                             now: Optional[datetime] = None) -> bool:
    """R1 (C-3): make a failed details write VISIBLE — one typed DETAILS_NOT_PERSISTED row per
    (thread, delivery) in `booking_attention_conditions`, `lead.necesita_humano = true`, and
    `thread_state.needs_human = true` when K1 is free within its bounded wait (the row says
    whether it was). Its own short session. Returns whether the condition is recorded (a Meta
    retry of the same delivery finds it and records nothing twice). Never raises."""
    from ..models import BookingAttentionCondition as C, Lead, WhatsAppThreadState
    from .booking_ce_adapter import isolated_session
    from .offer_lifecycle import OfferLifecycle
    stamp = now or datetime.now(timezone.utc)
    uid = hashlib.sha256("|".join((
        "attention:v1", CONDITION_KIND, str(int(thread_id)),
        str(attempt_ref or f"offer:{offer_id}"))).encode()).hexdigest()
    try:
        with isolated_session(db) as s:
            contended = OfferLifecycle(s).begin_offer_transaction(
                int(thread_id), lock_timeout_ms=LOCK_TIMEOUT_MS)
            if s.execute(select(C.id).where(C.condition_uid == uid)).first() is not None:
                s.rollback()
                return True
            applied = False
            if contended is None:
                state = s.execute(select(WhatsAppThreadState).where(
                    WhatsAppThreadState.thread_id == int(thread_id))).scalar_one_or_none()
                if state is not None:
                    state.needs_human = True
                    applied = True
            lead = s.get(Lead, int(lead_id)) if lead_id is not None else None
            if lead is not None:
                lead.necesita_humano = True
            s.add(C(condition_uid=uid, kind=CONDITION_KIND, reason=str(reason)[:64],
                    thread_id=int(thread_id), lead_id=lead_id,
                    offer_id=int(offer_id) if offer_id is not None else None,
                    flags_applied=applied, created_at=stamp))
            s.commit()
        logger.error("BOOKING_FLOW_DETAILS_NOT_PERSISTED thread_id=%s offer_id=%s reason=%s "
                     "— flagged for a person", thread_id, offer_id, str(reason)[:64])
        return True
    except Exception as exc:
        logger.error("BOOKING_FLOW_DETAILS condition NOT recorded thread_id=%s: %s", thread_id,
                     type(exc).__name__)
        return False


def live_details(db, thread_ids) -> dict:
    """{thread_id: {claim_type: value}} of the live (not superseded) rows, the newest attempt's
    value per claim type. Thread-wide, so NOT scoped to a vehicle: see `attempt_details`. An
    empty row is a value the customer removed, so it also hides an older attempt's. Read only."""
    from ..models import WhatsAppThreadEvidence as E
    ids = [int(t) for t in (thread_ids or []) if t is not None]
    if not ids:
        return {}
    rows = db.execute(select(E.thread_id, E.claim_type, E.value).where(
        E.thread_id.in_(ids), E.producer == PRODUCER, E.claim_type.in_(ALL_CLAIM_TYPES),
        E.superseded_by_id.is_(None)).order_by(E.id)).all()
    out: dict = {}
    for thread_id, claim_type, value in rows:
        claims = out.setdefault(int(thread_id), {})
        if value:
            claims[claim_type] = value
        else:
            claims.pop(claim_type, None)
    return {thread_id: claims for thread_id, claims in out.items() if claims}


def attempt_details(db, attempts: dict) -> dict:
    """{thread_id: {claim_type: value}} of each thread's ONE attempt, given as
    {thread_id: (candidate_id, cycle_key)}: the scope of `prefill_details`, for many threads.
    An emptied value is present as "", which is how a reader tells it from one never given.
    A thread not in `attempts` has no scope and gets nothing. Read only."""
    from ..models import WhatsAppThreadEvidence as E
    if not attempts:
        return {}
    rows = db.execute(select(E.thread_id, E.claim_type, E.value, E.bound_to_candidate_id,
                             E.cycle_key).where(
        E.thread_id.in_([int(t) for t in attempts]), E.producer == PRODUCER,
        E.claim_type.in_(ALL_CLAIM_TYPES), E.superseded_by_id.is_(None))
        .order_by(E.id)).all()
    out: dict = {}
    for thread_id, claim_type, value, candidate_id, cycle in rows:
        if attempts[int(thread_id)] == (candidate_id, cycle):
            out.setdefault(int(thread_id), {})[claim_type] = value
    return out


def prefill_details(db, *, thread_id: int, candidate_id: Optional[int], cycle_key: int) -> dict:
    """{Flow input name: value} a refreshed Flow may offer back, from the durable rows alone —
    never request or process memory, so it is the same after a restart, a lease expiry or a
    retried refusal. Blank values are left out: an input is never initialised with nothing.

    Scoped to this thread, this offer's candidate and this cycle. A row outside that scope is
    another conversation's fact, however recent. No candidate, nothing offered. Read only."""
    from ..models import WhatsAppThreadEvidence as E
    if candidate_id is None:
        return {}
    rows = db.execute(select(E.claim_type, E.value).where(
        E.thread_id == int(thread_id), E.producer == PRODUCER,
        E.claim_type.in_(tuple(PREFILL_KEY_FOR)), E.superseded_by_id.is_(None),
        E.bound_to_candidate_id == int(candidate_id), E.cycle_key == int(cycle_key))
        .order_by(E.id)).all()
    latest = {claim_type: value for claim_type, value in rows}
    return {PREFILL_KEY_FOR[claim_type]: value for claim_type, value in latest.items() if value}
