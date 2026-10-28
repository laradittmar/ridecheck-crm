"""W1F-W-01 R2 — the system knows what it asked, and recognises the answer.

The Wild's defect, stated exactly: the bot asked where the vehicle was, the customer
answered `en flores`, the interpreter read `INSPECTION_LOCATION = Flores, CONFIRMED` — and
the turn exited at Layer F step 9 and sent the generic qualification greeting, because
nothing in the system knew a question had been asked. The answer arrived as an unprompted
fragment and was treated as one. R1 made the fragment survive; this module makes it an
*answer*.

TWO RULES GOVERN EVERYTHING HERE, AND THEY ARE THE REASON THE MODULE IS SMALL:

    A question becomes ACTIVE because Meta confirmed DELIVERED or READ.
    A question closes because ADMITTED EVIDENCE answered it.

Neither half may be weakened into the other.

**Terminology, corrected by the owner at OC-R2-R1 and used exactly this way throughout:**
`delivered` means Meta handed the message to the recipient's device. It does **not** mean a
human looked at it. Only `read` supports "seen", and this module never needs that stronger
fact — it needs "the customer has the question", which is what `delivered` establishes.
No comment, log line or column here says the customer *saw* a delivered-only message.

**OC-R2 CORRECTION 1 — acceptance is not delivery, and the first version of this module got
that wrong.** It created the question active as soon as `_send_text_to_wa` returned a WAMID.
A WAMID is Meta ACCEPTING the message and handing back a correlation handle; it says nothing
about the recipient. Meta reports `sent`, `delivered`, `read` and `failed` afterwards, over
the status webhook, and this repository already receives and ranks them
(`routes/whatsapp.py`, `STATUS_PRECEDENCE`: pending < sent < delivered < read < failed).
Measured on the deployed databases before this was designed: of 45 outbound rows on
`crm_test`, 43 reached `read`, 1 `delivered` and 1 `failed`, and **none** is stranded at
`sent` or `pending` — so the statuses do arrive, and the correction needs no new transport,
no new route and no new authenticity boundary.

So acceptance produces `PENDING_DELIVERY`, which owns nothing at all, and only an observed
`delivered` or `read` promotes it to `OUTSTANDING`. `failed` ends it as `UNDELIVERED`.
A question nobody received cannot suppress a greeting or claim an answer, which was the
whole point of the contract.

And a row closes only on R1's admitted evidence for its own field, cycle and candidate;
**the model saying "the user answered your question" closes nothing**, because that is the
model deciding a state transition, which is exactly the authority the review refuses it.

Every state is WRITTEN. `OUTSTANDING → ANSWERED | SUPERSEDED | ABANDONED` are three
different facts and the absence of a row is a fourth ("we never asked"). Inferring any of
them from absence is how `_apply_candidate`'s RISK-03 problem was created, and this module
does not repeat it.

Concurrency and replay are handled by the database, not by careful ordering:

* `UNIQUE (identity)` — the identity is a deterministic UUIDv5 over
  (thread, cycle, kind, originating message), so a retried send or a duplicate delivery
  callback recomputes the same value and the second insert is refused by a constraint rather
  than by a read that another worker can also pass;
* `UNIQUE (thread, cycle, kind) WHERE status = 'OUTSTANDING'` — one live question may own a
  field, so two concurrent asks cannot both leave a live row behind;
* closure is a CONDITIONAL UPDATE off `OUTSTANDING`, so two workers answering the same
  question produce one close and one refusal. This is the pattern `offer_lifecycle` uses for
  acceptance, and the G3-7P-R3A review measured what removing it costs;
* activation is a CONDITIONAL UPDATE off `PENDING_DELIVERY`, so duplicate and out-of-order
  status callbacks are idempotent by construction: the second `delivered`, a late `read`
  after `delivered`, and a `delivered` arriving after `failed` all find a row that is no
  longer `PENDING_DELIVERY` and change nothing.

**OC-R2-R1 CORRECTION — ONE DETERMINISTIC ORDERING, AND IT IS THE ROW ID.**
The first version of the delivery contract was still wrong in one case, and the owner found
it: `delivered` callbacks do not arrive in the order the messages were accepted. The
sequence that broke it is

    Q1 accepted (PENDING_DELIVERY) → Q2 accepted for the same thread, cycle and kind
    → Q2 delivered (OUTSTANDING) → Q1 delivered, LATE

and the late callback promoted Q1 and superseded Q2 — a stale question displacing the one
the customer actually has, because supersession asked "who is live?" and never "who is
newer?".

The ordering is now explicit, and it is the one ordering this table already has that cannot
move: **the primary key**, assigned when the question was accepted. Monotonic, immutable,
local, visible to every connection, and independent of webhook arrival time — which is
exactly what may not be used, because it is a property of Meta's retry schedule and not of
our conversation. (`asked_at` is a clock and `wa_message_id` is Meta's opaque handle;
neither orders anything.)

**THE RULE, in the owner's words at OC-R2-R3, and the only statement of it anywhere:**

    Only the newest ELIGIBLE row may newly ACTIVATE. An existing OUTSTANDING incumbent
    remains active until a newer question actually activates. A newer terminal sibling
    prevents an older INACTIVE row from activating later, but does not retroactively
    deactivate an incumbent that was already OUTSTANDING.

**The ordering rule governs ACTIVATION, not DEACTIVATION, and conflating the two is the
mistake OC-R2-R3 corrected.** Read the three clauses against the code:

* a row with a NEWER sibling for its owner tuple `(thread_id, cycle_key, question_kind)`
  can never activate, whatever its own delivery turns out to be; it terminalizes instead
  (`_refuse_stale_activation`);
* when the newest row ACTIVATES, every OLDER sibling leaves its non-terminal state in the
  same transaction — `OUTSTANDING` ones as `SUPERSEDED_BY_REASK`, `PENDING_DELIVERY` ones
  as `SUPERSEDED_BEFORE_DELIVERY` (`_terminalize_older`, from the activation path only);
* **accepting** a newer ask retires older `PENDING_DELIVERY` siblings and NOTHING else
  (`_terminalize_older(include_outstanding=False)`), so an incumbent the customer is
  holding survives a re-ask that Meta has merely accepted — and survives it failing, since
  the `failed` branch writes only the row carrying that WAMID.

So the winner is decided by the accepted order of our own asks, not by the order Meta chose
to tell us about them, and the two possible interleavings of two callbacks reach the same
final state. **Nothing here can take the field from a question the customer demonstrably
received except another question the customer demonstrably received.**

The cost, stated precisely rather than over-generalised: when there was **no active
incumbent** — the first ask was still only `PENDING_DELIVERY` when the re-ask was accepted —
and the re-ask then fails or never confirms, nothing for that field is active and the older
row may not stand in for it, because that would make the outcome depend on callback order
again. The field is then simply unasked and the next turn asks it, which is the fail-closed
direction. When there **was** an active incumbent, no such window exists: it stays active
until it is answered, until a newer ask is genuinely delivered, or until it expires on its
own counted clock.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import select, update

from ..models import (OQ_ABANDONED, OQ_ACTIVE_STATUSES, OQ_ANSWERED, OQ_ASKED_BY,
                      OQ_ASKED_BY_DETERMINISTIC_TEMPLATE, OQ_ASKED_BY_FLOW,
                      OQ_ASKED_BY_OPERATOR, OQ_ASKED_BY_REPLY_MODEL,
                      OQ_DELIVERING_STATUSES, OQ_FAILED_STATUS,
                      OQ_KIND_INSPECTION_LOCATION, OQ_KIND_VEHICLE_IDENTITY, OQ_KINDS,
                      OQ_OUTSTANDING, OQ_PENDING_DELIVERY, OQ_SUPERSEDED, OQ_UNDELIVERED,
                      WhatsAppMessage, WhatsAppOutstandingQuestion)
from ..schemas.claims import ClaimType, ReconciliationOutcome

logger = logging.getLogger(__name__)

CONTRACT = "outstanding-question/1.0"

#: Fixed application namespace for the deterministic identity. A constant UUID, never
#: generated at runtime, so the same (thread, cycle, kind, message) always yields the same
#: identity across processes, restarts and deployments. Distinct from
#: `clarification_attempt.IDEMPOTENCY_NAMESPACE`: these are different facts about different
#: things, and sharing a namespace would let one collide with the other.
IDENTITY_NAMESPACE = uuid.UUID("b7c41d90-2f68-4a1c-8e53-9d07f6b28a41")

#: Which admitted claim families answer which question kind. The *contract* the review's
#: `expected_slots` column carries, declared once here and stored on the row so a reader
#: never has to consult code to know what the question was waiting for.
EXPECTED_SLOTS: dict[str, tuple[str, ...]] = {
    OQ_KIND_INSPECTION_LOCATION: (ClaimType.INSPECTION_LOCATION,),
    OQ_KIND_VEHICLE_IDENTITY: (ClaimType.VEHICLE_MAKE, ClaimType.VEHICLE_MODEL,
                               ClaimType.VEHICLE_CATEGORY),
}

#: Default lifetime, counted in the customer's own turns — never in wall-clock time.
#: 1 would make the contract useless on the Wild itself (the answer came on the very next
#: turn and would have to beat a clock); `W1F-C-11` requires the question to survive one
#: off-topic turn ("bot asks location → `cuánto sale?` → `en flores`"), so the floor is 2 and
#: the default is 3. Expiry is observed at turn boundaries by `observe_turn`.
DEFAULT_EXPIRES_AFTER_TURNS = 3


class CloseReason:
    """Why a question left its active state. A typed code, never prose."""
    ANSWERED_BY_EVIDENCE = "ANSWERED_BY_EVIDENCE"
    #: A NEWER question for the same owner tuple was delivered, so it took the field from
    #: this one, which the customer had already received.
    SUPERSEDED_BY_REASK = "SUPERSEDED_BY_REASK"
    #: OC-R2-R1: this question never became active and never will, because a newer ask for
    #: the same owner tuple exists. Written either when the newer ask is accepted or when
    #: this row's own (late) delivery callback arrives — the row-id ordering decides, not
    #: the callback order.
    SUPERSEDED_BEFORE_DELIVERY = "SUPERSEDED_BEFORE_DELIVERY"
    EXPIRED_TURNS = "EXPIRED_TURNS"
    CYCLE_RESET = "CYCLE_RESET"
    HANDED_OFF = "HANDED_OFF"
    #: OC-R2 CORRECTION 1: Meta reported the message failed, so the customer never got the
    #: question. Terminal and never active.
    DELIVERY_FAILED = "DELIVERY_FAILED"


class NoMatch:
    """Why this turn's evidence did NOT answer a question. Each one is a measured case."""
    NO_LIVE_QUESTION = "NO_LIVE_QUESTION"
    NO_ADMITTED_EVIDENCE = "NO_ADMITTED_EVIDENCE"
    WRONG_FIELD = "WRONG_FIELD"
    STALE_CYCLE = "STALE_CYCLE"
    WRONG_CANDIDATE = "WRONG_CANDIDATE"
    NOT_ADMITTED = "NOT_ADMITTED"
    ALREADY_CLOSED = "ALREADY_CLOSED"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_value(enum_or_str: Any) -> str:
    return getattr(enum_or_str, "value", enum_or_str)


def identity(thread_id: int, cycle: int, kind: str, origin_message_id: str) -> str:
    """Deterministic UUIDv5 over the four identity components. 36 characters.

    Derived from nothing else: no timestamp, no counter, no retry nonce, no customer text and
    no phone number. `origin_message_id` is the WAMID of OUR outbound message — the one that
    carried the question — so asking the same field twice in one cycle produces two different
    identities (two real asks, the second superseding the first) while a duplicate callback
    for one send produces the same identity and is refused by the UNIQUE.
    """
    return str(uuid.uuid5(
        IDENTITY_NAMESPACE,
        f"outstanding:{int(thread_id)}:{int(cycle)}:{kind}:{origin_message_id or ''}"))


# ── creation: only after the customer actually received it ────────────────────

def record_asked(db, *, thread_id: int, cycle_key: int, kind: str, asked_by: str,
                 wa_message_id: Optional[str], outbound_message_id: Optional[int] = None,
                 asked_turn_id: Optional[str] = None, candidate_id: Optional[int] = None,
                 expires_after_turns: int = DEFAULT_EXPIRES_AFTER_TURNS,
                 now: Optional[datetime] = None) -> tuple:
    """Record a question Meta ACCEPTED for delivery. Returns `(row, created)`.

    `wa_message_id` is **required**, and it is the ACCEPTANCE evidence, not delivery
    evidence: Meta returned it for the message that carried the question. Without it there is
    no row, and the three ways a turn can fail even to be accepted — the safety gate blocked
    it, the Meta call raised, composition suppressed every proposition — all converge on that
    same absence.

    The row is created `PENDING_DELIVERY`. It is not active and owns nothing until a status
    callback says the customer has it (`activate_on_status`). The caller should follow this
    with `reconcile_delivery`, which closes the race where the callback arrives BEFORE this
    row was committed — see that function.

    A re-ask of the same field in the same cycle is a NEW row, never a silent reuse: the
    customer was asked again and that is a different event. What it does to its predecessors
    is decided by one rule and by row id — it retires an older row that is also still only
    accepted, and leaves an `OUTSTANDING` incumbent alone until this row is itself delivered
    (OC-R2 correction 1, OC-R2-R1 ordering).
    """
    if not wa_message_id:
        logger.info("R2 outstanding question NOT recorded thread_id=%s kind=%s — Meta "
                    "never accepted the message", thread_id, kind)
        return (None, False)
    if kind not in OQ_KINDS or asked_by not in OQ_ASKED_BY:
        logger.warning("R2 outstanding question refused thread_id=%s kind=%r asked_by=%r",
                       thread_id, kind, asked_by)
        return (None, False)
    stamp = now or _now()
    ident = identity(thread_id, cycle_key, kind, wa_message_id)

    existing = db.execute(
        select(WhatsAppOutstandingQuestion).where(
            WhatsAppOutstandingQuestion.identity == ident)).scalars().first()
    if existing is not None:
        # The same send, seen twice: a retry of the record, or a duplicate delivery
        # callback. One ask, one row.
        return (existing, False)

    row = WhatsAppOutstandingQuestion(
        thread_id=thread_id, cycle_key=int(cycle_key), question_kind=kind,
        expected_slots=list(EXPECTED_SLOTS.get(kind, ())),
        asked_by=asked_by, identity=ident,
        outbound_message_id=outbound_message_id,
        wa_message_id=str(wa_message_id)[:128],
        asked_turn_id=(str(asked_turn_id)[:64] if asked_turn_id else None),
        candidate_id=candidate_id, status=OQ_PENDING_DELIVERY, asked_at=stamp,
        expires_after_turns=max(2, int(expires_after_turns)), turns_observed=0)
    # OC-R2 CORRECTION 1: the previous ACTIVE question is NOT superseded here any more.
    # This row is `PENDING_DELIVERY` and may never be delivered at all — superseding a
    # question the customer demonstrably received, on the strength of one we only handed to
    # Meta, would lose a live contract to a message that may never arrive. Supersession now
    # happens at ACTIVATION, where the new question is known to have been received.
    db.add(row)
    db.flush()
    # OC-R2-R1: this newer ask retires older rows that are *also* still only accepted, so a
    # re-ask cannot leave an obsolete `PENDING_DELIVERY` row behind for a late callback to
    # find. It does NOT touch an `OUTSTANDING` incumbent — see `_terminalize_older`. The
    # ordering authority is the same function activation uses; this is eager hygiene, not a
    # second rule, and the activation guard remains the enforcement point.
    _terminalize_older(db, row=row, stamp=stamp, include_outstanding=False)
    logger.info("R2 question accepted (PENDING_DELIVERY) thread_id=%s kind=%s asked_by=%s "
                "wamid=%s", thread_id, kind, asked_by, wa_message_id)
    return (row, True)


# ── delivery: the only thing that may make a question ACTIVE ─────────────────

def newest_question_id(db, *, thread_id: int, cycle_key: int,
                       kind: str) -> Optional[int]:
    """The highest row id for one owner tuple: the NEWEST question we accepted for it.

    `(thread_id, cycle_key, question_kind)` is the owner tuple — the same three columns the
    partial unique index guards, so "who may own this field" and "who is newest for this
    field" are asked of the same grouping and cannot disagree.

    Every status counts, terminal ones included. That is deliberate: if a newer ask exists
    at all then this older row is not the question we are waiting on, and whether the newer
    one has been delivered, failed or already answered must not change the older one's fate.
    Letting a terminal newer sibling hand the field back to an older row would restore the
    exact defect this correction removes — an outcome that depends on which callback arrived
    first.
    """
    return db.execute(
        select(WhatsAppOutstandingQuestion.id).where(
            WhatsAppOutstandingQuestion.thread_id == thread_id,
            WhatsAppOutstandingQuestion.cycle_key == int(cycle_key),
            WhatsAppOutstandingQuestion.question_kind == kind)
        .order_by(WhatsAppOutstandingQuestion.id.desc()).limit(1)).scalars().first()


def _terminalize_older(db, *, row, stamp, include_outstanding: bool) -> int:
    """Every sibling OLDER than `row` leaves its non-terminal state. Returns how many.

    THE ONE IMPLEMENTATION OF THE ORDERING RULE. Both callers — the acceptance of a new ask
    and the activation of the newest one — reach the rule through this function; there is no
    second copy of "who is older" anywhere, which is the mistake mutation M34 found in the
    flag interlock and the mistake this correction must not repeat.

    Two conditional set-based UPDATEs rather than a read-then-assign loop, because the
    guarantee has to survive two PostgreSQL connections:

    * each UPDATE names the status it is leaving, so under READ COMMITTED a row another
      connection is concurrently changing is re-evaluated against its committed version
      after the lock is released, instead of being overwritten from a stale snapshot;
    * the `OUTSTANDING` sweep runs BEFORE the caller promotes `row`, because the partial
      unique index `(thread, cycle, kind) WHERE status = 'OUTSTANDING'` refuses two live
      rows — promoting first and superseding second raises `IntegrityError` at flush, which
      is how the ordering of these two statements was originally established.

    `include_outstanding=False` is the acceptance case: a newly accepted question is only
    `PENDING_DELIVERY` and may never arrive, so it may retire older rows that are *also*
    only accepted, but it must not touch a question the customer demonstrably received
    (OC-R2 correction 1). Activation passes True, having earned the right to. **This
    parameter is the whole of the activation/deactivation boundary OC-R2-R3 names**: an
    incumbent is deactivated by exactly one event, a newer question actually activating, and
    `record_asked` is not that event. `TestR2P_Incumbent` measures all six sequences.

    Nothing here writes `activated_at` or `delivery_status` on another row: a superseded
    question keeps its own delivery provenance, and the newest row's activation provenance
    is never written by any other row's callback.

    `synchronize_session="fetch"` rather than a session-wide `expire_all()`: these statements
    run inside the engine's turn transaction, which holds the thread, the state and the
    candidates, and expiring all of them to refresh two question rows would be a wide and
    surprising side effect for a narrow write.
    """
    plan = [(OQ_PENDING_DELIVERY, CloseReason.SUPERSEDED_BEFORE_DELIVERY)]
    if include_outstanding:
        plan.append((OQ_OUTSTANDING, CloseReason.SUPERSEDED_BY_REASK))
    changed = 0
    for leaving, reason in plan:
        result = db.execute(
            update(WhatsAppOutstandingQuestion)
            .where(WhatsAppOutstandingQuestion.thread_id == row.thread_id,
                   WhatsAppOutstandingQuestion.cycle_key == int(row.cycle_key),
                   WhatsAppOutstandingQuestion.question_kind == row.question_kind,
                   WhatsAppOutstandingQuestion.id < int(row.id),
                   WhatsAppOutstandingQuestion.status == leaving)
            .values(status=OQ_SUPERSEDED, closed_reason=reason, closed_at=stamp,
                    superseded_by_id=int(row.id), updated_at=stamp)
            .execution_options(synchronize_session="fetch"))
        changed += int(result.rowcount or 0)
    if changed:
        db.flush()
        logger.info("R2 ordering: %s older question(s) retired by id=%s thread_id=%s "
                    "kind=%s", changed, row.id, row.thread_id, row.question_kind)
    return changed


def _refuse_stale_activation(db, *, row, state, stamp) -> Optional[int]:
    """A late delivery for a question a newer ask has already replaced. Terminalize it.

    Returns `None` when `row` IS the newest for its owner tuple — the caller then proceeds
    to activate it. Otherwise returns how many rows this call changed, which is 1 for the
    first late callback and 0 for every duplicate of it, because the write is conditional
    off `PENDING_DELIVERY`. That number reaches the webhook as part of
    `activate_on_status`'s total, and the webhook commits on a non-zero total — so the
    terminalization is durable rather than left in an uncommitted session.

    This is the owner's sequence 4: Q1 was accepted, Q2 was accepted and delivered, and
    Q1's `delivered` turns up afterwards. Q1 may not activate, may not suppress routing, may
    not match evidence — and Q2 must be left exactly as it is. So the row records the
    delivery that did eventually happen (`delivery_status`, so the provenance is not lost)
    and becomes terminal in the same statement, conditionally off `PENDING_DELIVERY` so a
    duplicate of the same late callback changes nothing the second time.
    """
    newest = newest_question_id(db, thread_id=row.thread_id, cycle_key=row.cycle_key,
                               kind=row.question_kind)
    if newest is None or int(newest) == int(row.id):
        return None
    result = db.execute(
        update(WhatsAppOutstandingQuestion)
        .where(WhatsAppOutstandingQuestion.id == row.id,
               WhatsAppOutstandingQuestion.status == OQ_PENDING_DELIVERY)
        .values(status=OQ_SUPERSEDED, delivery_status=state,
                closed_reason=CloseReason.SUPERSEDED_BEFORE_DELIVERY, closed_at=stamp,
                superseded_by_id=int(newest), updated_at=stamp)
        .execution_options(synchronize_session="fetch"))
    if result.rowcount:
        db.flush()
        logger.info("R2 LATE delivery refused activation id=%s newer=%s status=%s "
                    "thread_id=%s", row.id, newest, state, row.thread_id)
    return int(result.rowcount or 0)


def activate_on_status(db, *, wa_message_id: str, status: str,
                       now: Optional[datetime] = None) -> int:
    """A Meta status arrived for one of our messages. Activate or end the question.

    Returns how many rows this call changed — 0 for every status that is not decisive and
    for every callback that is a duplicate, which is what makes it safe to call from the
    webhook on every single status event.

    THE RULES, AND EACH IS ONE OF THE OWNER'S:

    * `delivered` or `read` → `PENDING_DELIVERY` becomes `OUTSTANDING`, atomically, by a
      CONDITIONAL UPDATE. `sent` is **not** sufficient and is ignored here: it means Meta
      accepted the message, which the WAMID already told us.
    * `read` without a prior observed `delivered` still activates. You cannot read a message
      that was not delivered, and Meta does not guarantee the intermediate callback arrives.
    * a row with a NEWER sibling for its owner tuple `(thread, cycle, kind)` does **not**
      activate, however its own delivery turns out: it becomes `SUPERSEDED`
      (`SUPERSEDED_BEFORE_DELIVERY`) and keeps the delivery status that arrived, so a late
      callback for a replaced question can never displace the question the customer has.
      OC-R2-R1, and the ordering is the row id — never webhook arrival time.
    * when the newest row activates, every older sibling leaves its non-terminal state in
      the same transaction, `OUTSTANDING` as `SUPERSEDED_BY_REASK` and `PENDING_DELIVERY` as
      `SUPERSEDED_BEFORE_DELIVERY`.
    * `failed` → `PENDING_DELIVERY` becomes `UNDELIVERED`, terminal and never active. It
      touches **only** the row carrying that WAMID: a failed re-ask does not retire an
      `OUTSTANDING` incumbent, so the field stays asked by the question the customer has
      (OC-R2-R3, `p02`).
    * duplicate and out-of-order callbacks are idempotent by construction: the conditional
      update only fires from `PENDING_DELIVERY`, so a second `delivered`, a `read` after
      `delivered`, and a `delivered` arriving **after** `failed` all find a row that has
      moved on and change nothing. The late `delivered` in particular must not resurrect a
      failed question, and it cannot.
    * an already `ANSWERED`, `SUPERSEDED` or `ABANDONED` row is untouched for the same
      reason.
    * the lookup is by `wa_message_id` and nothing else, so a status for another message or
      another thread matches no row of ours and does nothing.
    """
    wamid = str(wa_message_id or "").strip()
    state = str(status or "").strip().lower()
    if not wamid or not state:
        return 0
    stamp = now or _now()

    if state in OQ_DELIVERING_STATUSES:
        pending = list(db.execute(
            select(WhatsAppOutstandingQuestion).where(
                WhatsAppOutstandingQuestion.wa_message_id == wamid,
                WhatsAppOutstandingQuestion.status == OQ_PENDING_DELIVERY)
        ).scalars().all())
        changed = 0
        for row in pending:
            # OC-R2-R1 — THE ORDERING GUARD, and it comes first. A delivery for a question
            # a newer ask has already replaced must not activate it, however late it is and
            # whatever state the newer one is in. Anything but `None` means this row was not
            # the newest: it has been terminalized instead, and the count of what that
            # changed is reported so the webhook commits it.
            stale = _refuse_stale_activation(db, row=row, state=state, stamp=stamp)
            if stale is not None:
                changed += stale
                continue
            # This row IS the newest for its field. Every older sibling leaves its
            # non-terminal state **before** this row enters OUTSTANDING: the partial unique
            # index `(thread, cycle, kind) WHERE status = 'OUTSTANDING'` refuses two live
            # rows, so activating first and superseding second raises `IntegrityError` at
            # flush. The first version did exactly that and the suite caught it.
            _terminalize_older(db, row=row, stamp=stamp, include_outstanding=True)
            result = db.execute(
                update(WhatsAppOutstandingQuestion)
                .where(WhatsAppOutstandingQuestion.id == row.id,
                       WhatsAppOutstandingQuestion.status == OQ_PENDING_DELIVERY)
                .values(status=OQ_OUTSTANDING, delivery_status=state,
                        activated_at=stamp, updated_at=stamp))
            if not result.rowcount:
                continue           # a concurrent callback won; nothing to do
            db.flush()
            try:
                db.refresh(row)
            except Exception:
                row.status = OQ_OUTSTANDING
            changed += 1
            logger.info("R2 question ACTIVATED thread_id=%s kind=%s via=%s wamid=%s",
                        row.thread_id, row.question_kind, state, wamid)
        return changed

    if state == OQ_FAILED_STATUS:
        result = db.execute(
            update(WhatsAppOutstandingQuestion)
            .where(WhatsAppOutstandingQuestion.wa_message_id == wamid,
                   WhatsAppOutstandingQuestion.status == OQ_PENDING_DELIVERY)
            .values(status=OQ_UNDELIVERED, delivery_status=state,
                    closed_reason=CloseReason.DELIVERY_FAILED, closed_at=stamp,
                    updated_at=stamp))
        if result.rowcount:
            db.flush()
            logger.info("R2 question UNDELIVERED wamid=%s — Meta reported failed", wamid)
        return int(result.rowcount or 0)

    # `sent`, `pending` and anything else: recorded by the ledger, decisive for nothing here.
    return 0


def reconcile_delivery(db, row, *, now: Optional[datetime] = None) -> bool:
    """Apply the status the outbound ledger ALREADY holds for this question's message.

    This is the answer to "a callback arriving before the local send-finalization
    transaction completes must not be lost", and it needs no new mechanism because the
    ordering in `_send_text_to_wa` makes the ledger the durable rendezvous:

        gate.attempt()      the `whatsapp_messages` row is created and COMMITTED
        Meta call           returns the WAMID
        gate.mark_sent()    writes the WAMID and `status='sent'` and COMMITS, in its own
                            dedicated session
        ...                 ← a status callback can land anywhere from here on, and it
                              updates `whatsapp_messages.status` in ITS own session
        record_asked()      this row is created PENDING_DELIVERY
        reconcile_delivery  ← reads the status the callback may already have written

    So the callback cannot be lost: either it arrives after this row exists and
    `activate_on_status` finds it, or it arrived earlier and this function sees its effect on
    the ledger. Both paths end in the same conditional update, so a callback that happens to
    land exactly between them activates the row once and the other path changes nothing.

    Returns whether this call changed the row — which includes the OC-R2-R1 case where the
    ledger's status belongs to a question a newer ask has already replaced: the row is
    terminalized rather than activated, and that is still a change.
    """
    if row is None or not row.wa_message_id:
        return False
    if row.status != OQ_PENDING_DELIVERY:
        return False
    ledger_status = db.execute(
        select(WhatsAppMessage.status).where(
            WhatsAppMessage.wa_message_id == row.wa_message_id)).scalars().first()
    state = str(ledger_status or "").strip().lower()
    if state not in tuple(OQ_DELIVERING_STATUSES) + (OQ_FAILED_STATUS,):
        return False
    logger.info("R2 question reconciled from the ledger wamid=%s status=%s",
                row.wa_message_id, state)
    return bool(activate_on_status(db, wa_message_id=row.wa_message_id, status=state,
                                   now=now))


# ── reading ───────────────────────────────────────────────────────────────────

def live(db, *, thread_id: int, cycle_key: int, kind: Optional[str] = None) -> list:
    """Every question still OUTSTANDING for this thread and cycle, oldest first.

    Cycle-scoped by construction: a question asked before a cycle reset is not live after
    it, which is the same rule `in_cycle()` applies to claims and `cycle_key` applies to
    clarification attempts.
    """
    stmt = (select(WhatsAppOutstandingQuestion)
            .where(WhatsAppOutstandingQuestion.thread_id == thread_id,
                   WhatsAppOutstandingQuestion.cycle_key == int(cycle_key),
                   WhatsAppOutstandingQuestion.status == OQ_OUTSTANDING)
            .order_by(WhatsAppOutstandingQuestion.id))
    if kind is not None:
        stmt = stmt.where(WhatsAppOutstandingQuestion.question_kind == kind)
    return list(db.execute(stmt).scalars().all())


# ── matching: a pure rule over R1's admitted records ──────────────────────────

def match(question, records: Iterable, *, cycle_key: int,
          candidate_id: Optional[int] = None) -> tuple:
    """Does this turn's ADMITTED evidence answer this question? `(record|None, reason)`.

    Pure: no database, no engine, no clock. Every refusal below is one of the cases the
    milestone enumerates, and each is a column comparison rather than a judgement:

    * `NOT_ADMITTED` — the record is one of R1's refusals. This single condition covers
      ambiguous, unsupported, negated, inferred-not-explicit, conflicting and
      outranked evidence, because R1 already refused every one of them by name and a
      refusal is never a match. R2 adds no second opinion about any of them.
    * `WRONG_FIELD` — the admitted claim is not in this question's `expected_slots`.
      `W1F-C-12`: the bot asked for the VEHICLE and the customer answered with a locality;
      the locality is admitted, and the vehicle question stays open.
    * `STALE_CYCLE` — the question belongs to another conversation cycle.
    * `WRONG_CANDIDATE` — the question was about a specific candidate and this turn's
      evidence belongs to a different one.
    * `ALREADY_CLOSED` — not OUTSTANDING any more.

    What is deliberately absent: any path by which model prose, a confidence score or a
    "the user seems to have answered" signal reaches this function. The only inputs are
    R1's typed decisions.
    """
    if question is None:
        return (None, NoMatch.NO_LIVE_QUESTION)
    if question.status != OQ_OUTSTANDING:
        return (None, NoMatch.ALREADY_CLOSED)
    if int(question.cycle_key) != int(cycle_key):
        return (None, NoMatch.STALE_CYCLE)
    if question.candidate_id is not None and candidate_id is not None \
            and int(question.candidate_id) != int(candidate_id):
        return (None, NoMatch.WRONG_CANDIDATE)

    expected = set(question.expected_slots or EXPECTED_SLOTS.get(
        question.question_kind, ()))
    records = list(records or ())
    if not records:
        return (None, NoMatch.NO_ADMITTED_EVIDENCE)
    saw_admitted = False
    for record in records:
        if not getattr(record, "admitted", False):
            continue
        saw_admitted = True
        if str(_as_value(record.claim_type)) in expected:
            return (record, None)
    return (None, NoMatch.WRONG_FIELD if saw_admitted else NoMatch.NOT_ADMITTED)


# ── closure ───────────────────────────────────────────────────────────────────

def close_answered(db, question, *, claim_id: Optional[str] = None,
                   evidence_id: Optional[int] = None,
                   now: Optional[datetime] = None) -> bool:
    """Close a question as ANSWERED. Returns whether THIS call closed it.

    A CONDITIONAL UPDATE off `OUTSTANDING`, not a read-then-assign: two workers processing
    the same answer produce one close and one `False`, and the loser does not overwrite the
    winner's `answered_by_claim_id`. Idempotent for a replayed inbound message, because the
    second attempt finds the row already ANSWERED and reports that it changed nothing.
    """
    if question is None:
        return False
    stamp = now or _now()
    result = db.execute(
        update(WhatsAppOutstandingQuestion)
        .where(WhatsAppOutstandingQuestion.id == question.id,
               WhatsAppOutstandingQuestion.status == OQ_OUTSTANDING)
        .values(status=OQ_ANSWERED,
                answered_by_claim_id=(str(claim_id)[:64] if claim_id else None),
                answered_by_evidence_id=evidence_id,
                closed_reason=CloseReason.ANSWERED_BY_EVIDENCE,
                closed_at=stamp, updated_at=stamp))
    won = bool(result.rowcount)
    if won:
        db.flush()
        try:
            db.refresh(question)
        except Exception:
            question.status = OQ_ANSWERED
        logger.info("R2 outstanding question ANSWERED thread_id=%s kind=%s claim=%s",
                    question.thread_id, question.question_kind, claim_id)
    return won


def abandon(db, question, *, reason: str, now: Optional[datetime] = None) -> bool:
    """Close a question as ABANDONED, for an explicit reason. Conditional, like closure."""
    if question is None:
        return False
    stamp = now or _now()
    result = db.execute(
        update(WhatsAppOutstandingQuestion)
        .where(WhatsAppOutstandingQuestion.id == question.id,
               WhatsAppOutstandingQuestion.status == OQ_OUTSTANDING)
        .values(status=OQ_ABANDONED, closed_reason=reason[:48], closed_at=stamp,
                updated_at=stamp))
    if result.rowcount:
        db.flush()
        return True
    return False


def observe_turn(db, *, thread_id: int, cycle_key: int,
                 now: Optional[datetime] = None) -> list:
    """Count one customer turn against every live question; abandon those that ran out.

    Expiry is COUNTED, never timed. A customer who answers an hour later has still answered
    the question they were asked; a customer who has had three unrelated turns has moved on.
    The returned list is the questions this call abandoned, so the caller can trace them.

    Called once per turn, BEFORE matching, and it does not expire a question on the very
    turn it was asked (`turns_observed` starts at 0 and the floor is 2).
    """
    stamp = now or _now()
    expired = []
    for row in live(db, thread_id=thread_id, cycle_key=cycle_key):
        row.turns_observed = int(row.turns_observed or 0) + 1
        if row.turns_observed >= int(row.expires_after_turns):
            if abandon(db, row, reason=CloseReason.EXPIRED_TURNS, now=stamp):
                expired.append(row)
    db.flush()
    return expired
