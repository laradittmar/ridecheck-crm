"""Convergence Phase 4 (P2R-10) — the ONE canonical refusal finaliser, shared by the
Conversation Engine and the Data Exchange endpoint.

Since the Phase 3 cutover a refused Flow submission is decided by `OfferLifecycle.admit()` (a
read) or by `BookingAuthority.execute` — and the authority rolls back EVERYTHING on a refusal.
So nothing durable said that the offer was refused, nor terminalised an offer that can never be
accepted again (an expired one, one whose cycle or vehicle category moved). This restores that
durability WITHOUT giving any authority back to the refusal path:

1. It runs only after the decision, in its OWN short session — the caller's transaction and the
   authority's are already clean or rolled back. It never books, never confirms, never reads or
   writes the booking ledger, and nothing on a decision path reads what it writes.
2. Lock order K: the thread state (K1) first, bounded, then the offer row (K2) `FOR UPDATE`,
   both through `OfferLifecycle` — the same acquisition primitives every offer writer uses.
3. Under those locks it RE-READS the offer and re-classifies the refusal from canonical state
   with `OfferLifecycle._claim_checks` — the one implementation of what a valid offer is —
   so CE and Data Exchange cannot finalise one refusal two ways:
     * a refusal that proves the offer can never be accepted terminalises THAT offer (by id,
       conditional on ISSUED — never a replacement) and clears the transport mirror:
       OFFER_EXPIRED → EXPIRED; OFFER_CYCLE_CHANGED, OFFER_VEHICLE_CATEGORY_CHANGED,
       OFFER_VEHICLE_CATEGORY_UNRESOLVED → WITHDRAWN;
     * every other refusal (foreign, already terminal, candidate or location moved, slot no
       longer available, pre-claim guards) is RECORDED on the offer it refused against
       (`refused_count`, `last_refusal`) and leaves its status alone — a still-valid offer stays
       ISSUED and may be retried or booked;
     * transient contention never reaches here as a refusal: it is evidence only.
4. Before it commits it appends ONE `booking_attempt_events` row (stage FINALISE) whose uid is
   once-per-attempt, and THAT INSERT IS THE ADMISSION: PostgreSQL reports (ON CONFLICT DO
   NOTHING … RETURNING) whether this call inserted it. Only the first admission of a logical
   attempt keeps its writes; a replay — a CE redelivery of the same inbound WAMID, a
   transport retry of the same Data Exchange envelope — gets "not inserted" and rolls back
   its whole transaction (or savepoint), so `refused_count`, `last_refusal` and any
   terminalisation move with the event, once (Phase 4-R1, P4RV-02/03). If K1 or K2 is
   contended the offer is left untouched and only a CONTENDED event is written.
5. It never raises. Any failure is logged; the customer outcome was decided already.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: The bound on K1/K2 here. Shorter than the authority's: the finaliser is evidence, and a
#: holder of the thread state is a reason to record contention, not to wait.
FINALISE_LOCK_TIMEOUT_MS = 1500

#: Authority/CE refusal reasons that mean "the slot is gone" — recorded on a still-valid offer
#: as SLOT_NO_LONGER_AVAILABLE, which leaves it ISSUED (the customer may pick another slot).
_SLOT_REASONS = frozenset({"SLOT_CONFLICT", "CALENDAR_EXCEPTION", "OVERLAP",
                           "SCHEDULE_REVALIDATION", "EXCLUSION_CONSTRAINT",
                           "SLOT_NO_LONGER_AVAILABLE"})

#: Sentinel: the caller did not resolve the bindings (a refusal decided before it could), so
#: only the offer's own facts — thread, status, expiry — are judged, never a binding.
UNRESOLVED = object()


@dataclass(frozen=True)
class FinaliseResult:
    finalised: bool                 # offer evidence committed by THIS call
    reason: Optional[str]           # the canonical reason recorded (or observed, if none)
    offer_id: Optional[int] = None
    offer_status: Optional[str] = None
    replay: bool = False            # this attempt had already been finalised
    contended: bool = False         # K1/K2 held elsewhere: evidence only, offer untouched


def finalise_offer_refusal(db: Session, *, source: str, thread_id: int, token: Optional[str],
                           observed_reason: str, attempt_ref: Optional[str],
                           command_key: Optional[str] = None, candidate_id=UNRESOLVED,
                           tipo_vehiculo=UNRESOLVED, zone_group=UNRESOLVED,
                           zone_detail=UNRESOLVED, cycle_key=None,
                           in_caller_transaction: bool = False) -> FinaliseResult:
    """Make one refused Flow submission durable on its offer (see the module docstring).

    Normally in its OWN session. `in_caller_transaction=True` is for a caller whose open
    transaction already holds K1 for this thread (a Conversation Engine turn that ran the
    cycle reset before this refusal): an isolated session would wait on its own caller. The
    work then runs in the caller's transaction under a SAVEPOINT — a replay rolls back only
    the savepoint — and commits with the caller's own (K1-first) commit."""
    from .booking_attempt_events import record_attempt_event
    from .booking_ce_adapter import isolated_session
    from .offer_lifecycle import OfferLifecycle

    observed = str(observed_reason or "UNSPECIFIED")[:64]
    event = dict(source=source, command_key=command_key, attempt_ref=attempt_ref,
                 thread_id=thread_id)
    bindings = dict(candidate_id=candidate_id, tipo_vehiculo=tipo_vehiculo,
                    zone_group=zone_group, zone_detail=zone_detail, cycle_key=cycle_key)
    if type(token) is not str or not token.strip():
        record_attempt_event(db, stage="FINALISE", outcome="REFUSED", reason=observed, **event)
        return FinaliseResult(False, observed)
    try:
        if in_caller_transaction:
            nested = db.begin_nested()
            try:
                out = _finalise_in(db, token=token, observed=observed, event=event,
                                   bindings=bindings)
            except BaseException:
                nested.rollback()
                raise
            if out.finalised:
                nested.commit()
            else:
                nested.rollback()
            if out.contended:
                return _contended(db, observed, event, out.reason)
            return out
        with isolated_session(db) as s:
            contended = OfferLifecycle(s).begin_offer_transaction(
                int(thread_id), lock_timeout_ms=FINALISE_LOCK_TIMEOUT_MS)
            if contended is not None:
                return _contended(db, observed, event, contended)
            out = _finalise_in(s, token=token, observed=observed, event=event,
                               bindings=bindings)
            if out.finalised:
                s.commit()
            else:
                s.rollback()
            if out.contended:
                return _contended(db, observed, event, out.reason)
            return out
    except Exception as exc:
        logger.error("BOOKING_REFUSAL_NOT_FINALISED source=%s thread_id=%s reason=%s: %s",
                     source, thread_id, observed, type(exc).__name__)
        record_attempt_event(db, stage="FINALISE", outcome="INTERNAL",
                             reason=f"FINALISE_FAILED:{observed}", **event)
        return FinaliseResult(False, observed)


def _finalise_in(s: Session, *, token: str, observed: str, event: dict,
                 bindings: dict) -> FinaliseResult:
    """The finalisation itself, in `s` (which holds or will take K1 first). Writes nothing
    the caller must keep unless it returns `finalised=True`; the caller commits or rolls
    back (a whole session, or a savepoint)."""
    from ..models import WhatsAppBookingOffer as O, WhatsAppThreadState
    from .booking_attempt_events import insert_attempt_event
    from .offer_lifecycle import OfferLifecycle, OfferRefusal, token_fingerprint
    thread_id = int(event["thread_id"])
    offers = OfferLifecycle(s)
    state = s.execute(select(WhatsAppThreadState).where(
        WhatsAppThreadState.thread_id == thread_id)).scalar_one_or_none()
    # A binding the caller did not resolve is judged against itself: only what the caller
    # actually resolved can make a binding refusal.
    given = [bindings[k] for k in ("candidate_id", "tipo_vehiculo", "zone_group",
                                   "zone_detail")]
    if any(v is UNRESOLVED for v in given):
        own = s.execute(select(O.candidate_id, O.tipo_vehiculo, O.zone_group,
                               O.zone_detail).where(
            O.token_fingerprint == token_fingerprint(token))).first()
        own = tuple(own) if own is not None else (None, None, None, None)
        given = [o if v is UNRESOLVED else v for v, o in zip(given, own)]
    candidate_id, tipo_vehiculo, zone_group, zone_detail = given
    refusal, offer = offers._claim_checks(
        state, thread_id=thread_id, token=token, candidate_id=candidate_id,
        tipo_vehiculo=tipo_vehiculo, zone_group=zone_group, zone_detail=zone_detail,
        cycle_key=bindings["cycle_key"], lock=True, lock_timeout_ms=FINALISE_LOCK_TIMEOUT_MS)
    canonical = getattr(refusal, "refusal", None)
    if canonical in (OfferRefusal.OFFER_CONTENDED, OfferRefusal.OFFER_DEADLOCK):
        return FinaliseResult(False, canonical, contended=True)
    if canonical is None and offer is not None:
        # The offer is still acceptable on its own terms: record the observed refusal against
        # it and leave it ISSUED.
        canonical = "SLOT_NO_LONGER_AVAILABLE" if observed in _SLOT_REASONS else observed
        offers.record_refusal(offer.id, canonical, thread_id=thread_id)
    reason = canonical or observed
    offer_id = getattr(offer, "id", None)
    # The admission: the counters / reason / terminalisation written above survive ONLY if
    # this insert is the first for the attempt; on a replay the caller rolls all of it back.
    if not insert_attempt_event(s, stage="FINALISE", outcome="REFUSED", reason=reason,
                                offer_id=offer_id, once_per_attempt=True, **event):
        logger.info("BOOKING_REFUSAL_REPLAY source=%s thread_id=%s offer_id=%s — attempt "
                    "already finalised; nothing kept", event["source"], thread_id, offer_id)
        return FinaliseResult(False, reason, offer_id, replay=True)   # already finalised
    status = None
    if offer is not None:
        s.flush()
        status = s.execute(select(O.status).where(O.id == offer.id)).scalar()
    logger.info("BOOKING_REFUSAL_FINALISED source=%s thread_id=%s offer_id=%s reason=%s "
                "status=%s", event["source"], thread_id, offer_id, reason, status)
    return FinaliseResult(True, reason, offer_id, status)


def _contended(db, observed, event, why) -> FinaliseResult:
    from .booking_attempt_events import record_attempt_event
    record_attempt_event(db, stage="FINALISE", outcome="CONTENDED",
                         reason=f"{why}:{observed}", **event)
    logger.warning("BOOKING_REFUSAL_FINALISE_CONTENDED thread_id=%s reason=%s (%s) — offer "
                   "left untouched, evidence recorded", event.get("thread_id"), observed, why)
    return FinaliseResult(False, observed, contended=True)
