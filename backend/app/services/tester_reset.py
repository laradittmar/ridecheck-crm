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

G37R-RV-03 — WHY THIS HELPER NOW TOUCHES `whatsapp_booking_offers`.
`20261013_booking_projection` added `thread_revisions.offer_id`, and the offer table
already pointed the other way through `accepted_thread_revision_id` and
`accepted_revision_id`. That is a genuine two-cycle in the foreign-key graph, and the
G3-7R-R3 independent review measured what it does to this function once a tester has
completed one booking: `23503`, and **nothing deleted at all**.

Measured on a schema built through the migration, the offer row blocks the reset at FOUR
separate points, not one — every `NO ACTION` foreign key that leaves the offer table:

    DELETE thread_revisions   -> whatsapp_booking_offers_accepted_thread_revision_id_fkey
    DELETE candidates         -> whatsapp_booking_offers_candidate_id_fkey
    DELETE revisions          -> whatsapp_booking_offers_accepted_revision_id_fkey
    DELETE whatsapp_threads   -> whatsapp_booking_offers_thread_id_fkey
    DELETE offers first       -> fk_thread_revisions_offer_id   (the other half of the cycle)

So clearing the two accepted pointers is necessary and not sufficient: this helper never
deleted the tester's offers at all, which is why the candidate and thread deletions failed
too. The correction is therefore two things, both scoped to the tester being reset:

  * the two accepted-booking pointers are cleared FIRST, which breaks the cycle without
    touching production referential integrity; and
  * the tester's own offer rows are deleted in the canonical order — after the bookings
    that reference them, before the candidates and threads they reference.

Asking the catalogue rather than the code for the full list turned up a SECOND blocking
referent that predates the booking projection entirely:
`whatsapp_clarification_attempts` references the thread and the outbound message it
dispatched, both `NO ACTION`, and this helper never deleted it either. One clarification
attempt is enough to make the whole reset fail and delete nothing. Both referents are now
handled, and `test_rv03_pg_13` derives the list from `pg_constraint` so a third one added
by a future migration fails the gate instead of silently breaking the reset again.

The production foreign keys are deliberately NOT changed to `ON DELETE SET NULL`. A
test-support reset must not weaken the referential integrity of the booking authority to
make its own job easier; the audit's requirement is explicit about that, and the scoped
clear measured here is sufficient.

CONVERGENCE PHASE 2 (RV03RV-05) — THE BOOKING-COMMAND LEDGER IS DETACHED, NOT DELETED.
`booking_commands` survived a reset already (its two booking foreign keys are `ON DELETE SET
NULL`), but the surviving row still answered `IDEMPOTENT_REPLAY / ALREADY_COMMITTED` with
`occupying=True` and the original slot for a booking that no longer exists, and its
`lead_id` / `thread_id` / `profesional_id` dangled at deleted ids a reseeded tester may
reuse. Before anything is deleted, the tester's ledger rows are now made terminally
`DETACHED` (`_detach_ledger`): retained as history with their key still consumed, every live
pointer and reusable id NULL, never replayable, never acknowledged, never bookable again.
This stays a TESTER tool: it selects only by this tester's threads, leads and bookings.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import inspect as sa_inspect, or_, select, text
from sqlalchemy.orm import Session

from ..models import (
    AiEvent,
    BookingCommandRecord,
    Lead,
    Revision,
    ThreadRevision,
    WhatsAppBookingOffer,
    WhatsAppClarificationAttempt,
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppOutstandingQuestion,
    WhatsAppThread,
    WhatsAppThreadCandidate,
    WhatsAppThreadEvidence,
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
    #: G37R-RV-03. How many of the tester's offer rows had an accepted-booking pointer
    #: cleared so the two-cycle could be broken. Reported rather than inferred, because a
    #: reset that silently cleared nothing would fail later and much less legibly.
    cleared_offer_pointers: int = 0
    #: Convergence Phase 2 (RV03RV-05). How many of the tester's booking-command ledger rows
    #: were DETACHED: retained as history, terminally non-replayable, nothing joinable left.
    detached_commands: int = 0


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


def _offer_table_present(db: Session) -> bool:
    """Is `whatsapp_booking_offers` in this database?

    G37R-RV-03. `20261013_booking_projection` is NOT yet applied to `crm` or `crm_test`, so
    this helper has to keep working against a schema that has no offer table — the same
    reason the dedup and recipient-lock deletions below are guarded. Asked through the
    inspector rather than by catching an exception, because a failed statement would abort
    the reset's own transaction and there would be nothing left to continue with.

    THE INSPECTOR IS GIVEN THE SESSION'S OWN CONNECTION, not its engine. Asking the engine
    opens a second connection and closes it again, and on the in-memory SQLite the test
    suite uses — one DBAPI connection shared through a `StaticPool` — that close issues a
    ROLLBACK on the very transaction this reset is in the middle of. Measured: the outbound
    re-parenting was undone and the archived ledger row was then deleted along with the
    tester, breaking RESET-WAMID-01, -02 and -04. `db.connection()` stays inside the
    transaction, which is also the only honest thing to ask: whether the table exists in
    the database this session is talking to.
    """
    try:
        return sa_inspect(db.connection()).has_table(
            WhatsAppBookingOffer.__tablename__)
    except Exception:                           # pragma: no cover - defensive
        logger.debug("reset: could not inspect for the offer table", exc_info=True)
        return False


def _r2_table_present(db: Session, model) -> bool:
    """Is one of W1F-W-01 R2's two tables in this database?

    Same reasoning and the same session-own connection as `_offer_table_present`: the R2
    migration is not applied to `crm` or `crm_test` when this is written, and the reset has
    to keep working against a schema that predates it. Asked through the inspector rather
    than by catching an exception, because a failed statement would abort the reset's own
    transaction.
    """
    try:
        return sa_inspect(db.connection()).has_table(model.__tablename__)
    except Exception:                           # pragma: no cover - defensive
        logger.debug("reset: could not inspect for %s", model.__tablename__,
                     exc_info=True)
        return False


def _ledger_table_present(db: Session) -> bool:
    """Is `booking_commands` in this database? Same reasoning, and the same session-own
    connection, as `_offer_table_present`: `crm` / `crm_test` do not have the table yet."""
    try:
        return sa_inspect(db.connection()).has_table(BookingCommandRecord.__tablename__)
    except Exception:                           # pragma: no cover - defensive
        logger.debug("reset: could not inspect for the ledger table", exc_info=True)
        return False


def _detach_ledger(db: Session, thread_ids: list, lead_ids: list) -> int:
    """Convergence Phase 2 (RV03RV-05): DETACH the tester's booking-command ledger rows.

    Retained, never deleted — the ledger is forensic history and its key stays consumed — but
    made TERMINAL: `command_state = 'DETACHED'` with the reason and the time, every live
    pointer (`revision_id`, `thread_revision_id`) and reusable id (`lead_id`, `thread_id`,
    `profesional_id`) NULL, no slot and `occupying = false`. What the row named before is
    kept only as historical JSON that nothing joins. `BookingAuthority.lookup()` then answers
    `COMMAND_DETACHED`, never a replay.

    It must run BEFORE the deletions: two of the four identities it selects by are foreign
    keys that the deletion of the bookings would set NULL. Scoped to this tester's threads,
    leads and their bookings, and nothing else — another tester's command is not read.
    """
    from .booking_authority import DETACH_REASON_TESTER_RESET, LEDGER_COMMITTED, LEDGER_DETACHED
    R = BookingCommandRecord
    revision_ids = list(db.execute(
        select(Revision.id).where(Revision.lead_id.in_(lead_ids))).scalars().all()) if lead_ids else []
    thread_revision_ids = list(db.execute(
        select(ThreadRevision.id).where(ThreadRevision.thread_id.in_(thread_ids))
    ).scalars().all())
    scope = [R.thread_id.in_(thread_ids), R.thread_revision_id.in_(thread_revision_ids)]
    if lead_ids:
        scope += [R.lead_id.in_(lead_ids), R.revision_id.in_(revision_ids)]
    rows = db.execute(select(R).where(R.command_state == LEDGER_COMMITTED)
                      .where(or_(*scope))).scalars().all()
    now = datetime.now(timezone.utc)
    for rec in rows:
        rec.detached_history = json.dumps({
            "historical": True, "detached_by": DETACH_REASON_TESTER_RESET,
            "revision_id": rec.revision_id, "thread_revision_id": rec.thread_revision_id,
            "lead_id": rec.lead_id, "thread_id": rec.thread_id,
            "profesional_id": rec.profesional_id,
            "slot_start": rec.slot_start.isoformat() if rec.slot_start else None,
            "slot_end": rec.slot_end.isoformat() if rec.slot_end else None,
            "occupying": rec.occupying,
        }, sort_keys=True)
        rec.command_state = LEDGER_DETACHED
        rec.detached_at = now
        rec.detached_reason = DETACH_REASON_TESTER_RESET
        rec.revision_id = None
        rec.thread_revision_id = None
        rec.lead_id = None
        rec.thread_id = None
        rec.profesional_id = None
        rec.slot_start = None
        rec.slot_end = None
        rec.occupying = False
    db.flush()
    return len(rows)


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

        # 2. G37R-RV-03: BREAK THE TWO-CYCLE BEFORE ANYTHING DEPENDENT IS DELETED.
        #    `whatsapp_booking_offers.accepted_thread_revision_id` and
        #    `accepted_revision_id` are audit metadata recording what an acceptance
        #    produced; `thread_revisions.offer_id` points back and is the safety control
        #    that carries the one-offer/one-booking guarantee. The control side must not
        #    move, so the audit side yields — exactly as the model's own comment on
        #    `use_alter` says it does for the schema graph.
        #
        #    Scoped to THIS tester's threads and to nothing else. A `WHERE thread_id IN
        #    (...)` is the whole guard: no other tester's offer, booking, lead or candidate
        #    is read or written, and a table-wide clear would make this helper capable of
        #    destroying the audit trail of every booking in the database.
        #
        #    Done with an ORM update rather than a Core `UPDATE ... WHERE`, so the offers
        #    are identified once, counted, and already in the session when the deletion
        #    below removes them — and so a SQLite test schema behaves the same way.
        if _offer_table_present(db):
            offers = db.execute(
                select(WhatsAppBookingOffer).where(
                    WhatsAppBookingOffer.thread_id.in_(thread_ids)
                )
            ).scalars().all()
            for offer in offers:
                if (offer.accepted_thread_revision_id is not None
                        or offer.accepted_revision_id is not None):
                    offer.accepted_thread_revision_id = None
                    offer.accepted_revision_id = None
                    result.cleared_offer_pointers += 1
            # The pointers must be durable in this transaction BEFORE the bookings they
            # reference are deleted. Without this flush the ORM is free to order the
            # UPDATE after the DELETE and the reset fails exactly as it did before.
            db.flush()

        # 3. Convergence Phase 2 (RV03RV-05): detach the tester's booking-command ledger
        #    rows while the identities that select them still exist (see `_detach_ledger`).
        if _ledger_table_present(db):
            result.detached_commands = _detach_ledger(db, thread_ids, lead_ids)

    def _delete(model, condition, label: str) -> None:
        rows = db.execute(select(model).where(condition)).scalars().all()
        for row in rows:
            db.delete(row)
        db.flush()
        result.deleted[label] = len(rows)

    if thread_ids:
        _delete(AiEvent, AiEvent.thread_id.in_(thread_ids), "ai_events")
        _delete(ThreadRevision, ThreadRevision.thread_id.in_(thread_ids), "thread_revisions")
        # G37R-RV-03: the offers go HERE and nowhere else in this order — after the
        # bookings whose `offer_id` references them, and before the candidates and threads
        # that THEY reference. Both of those deletions used to fail on this table.
        if _offer_table_present(db):
            _delete(WhatsAppBookingOffer,
                    WhatsAppBookingOffer.thread_id.in_(thread_ids), "booking_offers")
        # G37R-RV-03, SECOND BLOCKING REFERENT — found while proving the first, and older
        # than it. `whatsapp_clarification_attempts` references BOTH the thread
        # (`thread_id`) and the outbound row it dispatched (`outbound_message_id`), both
        # `NO ACTION`, and this helper never deleted it. Measured: one clarification
        # attempt makes the whole reset fail `23503` on
        # `whatsapp_clarification_attempts_thread_id_fkey` and delete NOTHING — with or
        # without the offer table, so it is live on `crm_test` today. It is deleted here,
        # before the messages its `outbound_message_id` may reference.
        _delete(WhatsAppClarificationAttempt,
                WhatsAppClarificationAttempt.thread_id.in_(thread_ids),
                "clarification_attempts")
        # W1F-W-01 R2, THIRD AND FOURTH BLOCKING REFERENTS — and they are here because
        # G37R-RV-03 taught the rule the hard way: `ask pg_constraint, not the code`.
        # `whatsapp_outstanding_questions` references the thread, and
        # `whatsapp_thread_evidence` references the thread and (SET NULL) the candidates, so
        # both must go before the thread; the questions must also go before this helper's
        # own `whatsapp_messages` deletion is irrelevant for them (`outbound_message_id` is
        # a HISTORICAL scalar with no foreign key, deliberately — see the model), but the
        # ORDER is kept here anyway so a future FK cannot reintroduce the 23503 the review
        # measured. The evidence rows go before the candidates whose ids they carry.
        if _r2_table_present(db, WhatsAppOutstandingQuestion):
            _delete(WhatsAppOutstandingQuestion,
                    WhatsAppOutstandingQuestion.thread_id.in_(thread_ids),
                    "outstanding_questions")
        if _r2_table_present(db, WhatsAppThreadEvidence):
            _delete(WhatsAppThreadEvidence,
                    WhatsAppThreadEvidence.thread_id.in_(thread_ids),
                    "thread_evidence")
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
        "TESTER_RESET wa_id=...%s archived_outbound=%s cleared_offer_pointers=%s "
        "detached_commands=%s deleted=%s archive_thread=%s",
        wa_id[-4:], result.archived_outbound, result.cleared_offer_pointers,
        result.detached_commands, result.deleted, result.archive_thread_id,
    )
    return result
