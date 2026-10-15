"""The booking-offer lifecycle — G3-7P.

ONE authority decides whether a submitted booking Flow may become a booking, and ONE
authority invalidates an offer when the thing being booked changes.

Why this module exists. Until G3-7P the offer and the authority over it were the same
nullable string, `whatsapp_thread_states.flow_booking_token`, and the booking path asked

    if expected and flow_token and flow_token != expected:

A NULL `expected` short-circuits that, and NULL is the RESTING STATE of the column after six
production writes: booking consumption, cycle reset, the rejected-offer handoff, the
motorcycle handoff, the text-path correction withdrawal, and `booking_flow_service`'s own
consumption. The G3-7N review measured the consequence on real PostgreSQL — with `expected
IS NULL` the path accepted the old consumed token, a fabricated token, an empty token and NO
TOKEN AT ALL, each creating a ThreadRevision and a CRM Revision. G3-7N replaced one of those
six NULLs with a truthy tombstone, which closed one door and re-armed L4.7W5-F7A through the
eight places that read the column for truthiness.

So authority moved out of the column entirely:

    `whatsapp_booking_offers`  is the authority (row EXISTENCE + `status`)
    `flow_booking_token`       is a transport mirror, written only through here

ABSENCE IS A REFUSAL. `claim()` starts from "there is no offer" and only an ISSUED row,
bound to this thread, this cycle, this candidate, this candidate's CATEGORY and this
location, matching the submitted token's fingerprint, un-expired and never previously
accepted, can move it. Missing evidence is never permission.

THE BINDING CONTRACT (R2). Every stored field is exactly one of two things, and the
distinction is enforced rather than described — the R2 audit found `tipo_vehiculo` and
`cycle_key` recorded as if they were guards and never compared, which is a false assurance:

    ACCEPTANCE BINDINGS — compared on every claim, a mismatch refuses
        token_fingerprint   the lookup key, and UNIQUE in the schema
        thread_id           an offer belongs to one conversation
        cycle_key           a closed cycle's offer cannot book into the next one
        candidate_id        resolved through the SAME authority the caller books with
        tipo_vehiculo       a `PricingService.quote()` input — see A2 below
        zone_group          a `PricingService.quote()` input
        zone_detail         a `PricingService.quote()` input
        expires_at          pre-existing token-age policy
        status              only ISSUED may move

    HISTORICAL METADATA — recorded for audit, never an acceptance guard, and deliberately
    NOT compared, because the customer legitimately chooses inside the Flow:
        offered_date        the slot the customer was SHOWN. The submitted date/time is
        offered_time        what gets booked, revalidated by `ScheduleService` at
                            confirmation; comparing these would refuse a customer who
                            picked a different offered slot on the APPOINTMENT screen.
        flow_id             which Flow asset was dispatched
        contract_version    the payload version this row was written at
        refused_count       audit counters
        last_refusal
        last_refused_at
        accepted_revision_id        what the acceptance produced
        accepted_thread_revision_id
        terminal_at / accepted_at / terminal_reason   lifecycle timestamps and cause

A2 (R2). The G3-7P review measured the cost of storing a binding and not comparing it:
with the candidate's category changed in place from AUTO to PICKUP — same candidate id,
same zone, offer still ISSUED — the submission was ACCEPTED and produced a CRM Revision
carrying `tipo_vehiculo='PICKUP'` with `precio_total=NULL`. An unpriced booking, from an
offer issued for a different vehicle class. A changed pricing basis now requires a NEW
quote and a NEW offer; confirmation never silently recomputes one.

The raw token is never persisted: `token_fingerprint` is sha256(token).

Concurrency is the database's, not the application's. `needs_human` is not idempotency.

    * at most one LIVE offer per thread  — UNIQUE (thread_id) WHERE status = 'ISSUED'
    * at most one offer per token        — UNIQUE (token_fingerprint)
    * at most one BOOKING per offer      — UNIQUE (thread_revisions.offer_id)
                                           WHERE offer_id IS NOT NULL

G3-7R corrected a false assurance in this list. Until now the third line read "at most one
ACCEPTANCE per offer — SELECT ... FOR UPDATE, then a conditional UPDATE ... WHERE status =
'ISSUED'", and described APPLICATION code as if it were a database guarantee. The G3-7P-R3A
independent review measured that it is not: with both of those two statements removed, two
concurrent confirmations of ONE offer produced TWO bookings at two different slots in 6 of
6 trials, and the 6,000-test suite stayed identity-identical. The row lock and the
conditional UPDATE are still here and still do their work — they are what turns the race
into an ordinary typed refusal instead of an exception — but the GUARANTEE is now the
partial unique index on the booking's own `offer_id`, because a constraint holds against
callers that have not been written yet and a convention does not.

    The advisory lock is coordination. The exclusion constraint is the guarantee.

`claim()` deliberately does NOT commit. The caller's existing single commit couples the
acceptance to the booking it authorizes, so either both become authoritative or neither
does; a caller that fails after claiming rolls the acceptance back with its booking.
"""
from __future__ import annotations

import hashlib
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Bump when the stored payload's meaning or structure changes.
OFFER_CONTRACT_VERSION = "offer/1.0"

#: R3/A4 + G3-7R/S1. THE canonical lock order for EVERY writer of this table — not only
#: the two that can accept. R3 enforced it on the two acceptance writers and declared the
#: job done; the G3-7P architecture audit then measured `40P01 deadlock_detected` in 3/3
#: trials between ISSUE and acceptance, because `issue()` was never brought under the
#: protocol. `issue()` calls `supersede()`, which locks the offer row through a conditional
#: UPDATE, and the transport-mirror assignment that follows flushes as an UPDATE on
#: `whatsapp_thread_states` AT COMMIT — so ISSUE took the two resources in the opposite
#: order and the acceptance writer was the victim every time, reported as OFFER_CONTENDED.
#:
#: So the order is acquired by `_acquire_thread_state()` / `_require_thread_state()` at the
#: top of EVERY persisted mutation of this table, and a `40P01` is now a PROTOCOL BUG
#: rather than a contention fact — which is why it carries its own typed refusal
#: (`OFFER_DEADLOCK`) and logs at ERROR.
#:
#: R2/RV-02 MADE THAT SENTENCE TRUE. G3-7R declared it for "every writer" and enforced it
#: for five: `issue`, `_terminalise` (withdraw / supersede / expire), `consume`,
#: `refine_terminal_reason` and `claim`. The independent review inventoried four more that
#: wrote the offer table without entering the order at all — `record_replay`, `_refuse`,
#: `record_refusal` and `record_refusal_out_of_band` — and showed that the inverse order
#: deadlocks `40P01` on demand. They were safe only because no call site happened to follow
#: them with a thread-state write, which is a property of today's callers rather than of
#: the protocol. All nine now take resource 1 first.
#:
#: The lists below are therefore executable, not aspirational: `OFFER_WRITERS` names every
#: persisted mutation of `whatsapp_booking_offers` and `OFFER_LOCK_PARTICIPANTS` names the
#: helpers that take the resources without writing, and the suite derives both from the
#: source — so a writer added without the acquisition, or a helper mislabelled as a writer,
#: fails the gate instead of silently widening the surface.
OFFER_LOCK_ORDER = ("whatsapp_thread_states", "whatsapp_booking_offers")

#: Every method on `OfferLifecycle` that PERSISTS a mutation of `whatsapp_booking_offers`
#: — nine of them, derived from the code and asserted against it.
#:
#: R3/RVR2-08 MADE THIS DECLARATION TRUTHFUL. R2 listed ten names and called all ten
#: "writers". The independent review derived the writers from the source and found NINE:
#: `_claim_checks` was in the list, but it persists nothing. It reads the offer row under
#: resource 2 and delegates every refusal it reaches to `_refuse` and `_terminalise`, which
#: are declared here in their own right. Calling a lock-and-read helper a persisted writer
#: made the list wrong in the one direction a safety declaration must never be wrong:
#: it over-claimed, so a reader could not tell which names the lock-order assertion was
#: actually about.
#:
#: The two roles are therefore declared separately. Both are checked, for different
#: properties: a WRITER must take resource 1 before it writes; a PARTICIPANT must take
#: resource 1 before it takes resource 2.
OFFER_WRITERS = (
    "issue", "_terminalise", "consume", "refine_terminal_reason", "claim",
    "_refuse", "record_refusal", "record_replay", "record_refusal_out_of_band",
)

#: Methods that participate in `OFFER_LOCK_ORDER` without persisting anything themselves.
#: They acquire resource 1 — and `_claim_checks` also acquires resource 2, the offer row,
#: under its own bound — so they are bound by the order; they are not writers, and the
#: writer assertion must not pretend they are. `claim` reaches the order THROUGH
#: `_claim_checks`; that one delegation is named in the test and no other is accepted.
OFFER_LOCK_PARTICIPANTS = ("_claim_checks",)

#: S1. The name of the partial unique index that IS the one-offer/one-booking guarantee.
#: Declared here, beside the authority, so the model, the migration and the one function
#: that translates a violation of it all read the same string.
BOOKING_PROJECTION_CONSTRAINT = "uq_thread_revision_one_booking_per_offer"

#: A bounded wait, so a contended acceptance fails as a typed domain outcome rather than
#: blocking a conversation turn indefinitely. The ConversationEngine previously set no
#: timeout at all.
DEFAULT_OFFER_LOCK_TIMEOUT_MS = 4000

#: SQLSTATEs that mean "another transaction holds what we need, right now" — a contention
#: fact, never an internal failure. 55P03 lock_not_available (our bounded wait expired),
#: 40001 serialization_failure.
_CONTENTION_SQLSTATES = frozenset({"55P03", "40001"})

#: G3-7R/S4. `40P01 deadlock_detected` is kept SEPARATE from ordinary contention, because
#: it does not mean the same thing. 55P03 means another transaction legitimately holds a
#: resource and our declared bound expired; 40P01 means two transactions took resources in
#: incompatible orders, which under a single declared lock order is impossible — so it is
#: evidence of a protocol violation, not of load. R3 folded it in with the other two and
#: the architecture audit's measured ISSUE/acceptance deadlock was therefore reported to
#: operators as OFFER_CONTENDED: the wrong diagnosis of a real defect.
_DEADLOCK_SQLSTATES = frozenset({"40P01"})

#: Every SQLSTATE the offer writers answer with a typed domain outcome instead of raising.
_HANDLED_SQLSTATES = _CONTENTION_SQLSTATES | _DEADLOCK_SQLSTATES

#: `SHOW lock_timeout` returns a PostgreSQL interval literal (`0`, `250ms`, `4s`, `1min`).
#: `SET` takes no bind parameter, so a restored value is re-inlined — and only after it has
#: matched this shape, so nothing that is not a timeout literal can reach the statement.
_PG_TIMEOUT_LITERAL = re.compile(r"^\d{1,12}(us|ms|s|min|h|d)?$")

#: Expiry is PRE-EXISTING policy, not a new one: `booking_flow_service.TOKEN_MAX_AGE_SECONDS`
#: already refused a token older than this on the data-exchange path. Imported lazily to
#: keep this module free of a circular import.
def _max_age_seconds() -> int:
    from .booking_flow_service import TOKEN_MAX_AGE_SECONDS
    return int(TOKEN_MAX_AGE_SECONDS)


class OfferProtocolContended(RuntimeError):
    """Resource 1 of `OFFER_LOCK_ORDER` could not be taken, so the writer may not proceed.

    G3-7R. `_acquire_thread_state()` returns a typed refusal, and the first draft of this
    module let the terminal transitions IGNORE it and write to the offer table anyway. That
    is the protocol violation the protocol exists to prevent, and it is worse than it
    sounds: the offer-table write that follows would then wait on whatever holds the offer
    row, with no bound, which is how a booking turn hangs instead of answering.

    So a writer that cannot enter the order raises, and the caller's transaction is left
    for the caller to roll back. `claim()` does NOT raise — it is the acceptance boundary
    and owes its caller a typed domain outcome, so it answers `OFFER_CONTENDED` or
    `OFFER_DEADLOCK` instead.
    """

    def __init__(self, refusal: str, thread_id=None):
        super().__init__(f"offer lock order not entered: {refusal} (thread {thread_id})")
        self.refusal = refusal
        self.thread_id = thread_id


class OfferStatus:
    """The complete lifecycle vocabulary. Anything else is refused by a CHECK constraint."""
    ISSUED = "ISSUED"
    ACCEPTED = "ACCEPTED"
    WITHDRAWN = "WITHDRAWN"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"

    TERMINAL = frozenset({ACCEPTED, WITHDRAWN, SUPERSEDED, EXPIRED})
    ALL = frozenset({ISSUED}) | TERMINAL


class OfferRefusal:
    """Why a submission may not book. Every one of these is an honest terminal answer and
    none of them is reported to an operator as a successful state change."""
    NO_TOKEN = "NO_TOKEN"                        # nothing submitted to match
    UNKNOWN_TOKEN = "UNKNOWN_TOKEN"              # fabricated, tampered, or never issued
    NO_OFFER_RECORD = "NO_OFFER_RECORD"          # a token mirror with no authority row
    FOREIGN_OFFER = "FOREIGN_OFFER"              # issued to a different thread
    OFFER_WITHDRAWN = "OFFER_WITHDRAWN"
    SUPERSEDED_OFFER = "SUPERSEDED_OFFER"
    OFFER_ALREADY_ACCEPTED = "OFFER_ALREADY_ACCEPTED"
    OFFER_EXPIRED = "OFFER_EXPIRED"
    OFFER_CANDIDATE_CHANGED = "OFFER_CANDIDATE_CHANGED"
    OFFER_LOCATION_CHANGED = "OFFER_LOCATION_CHANGED"
    #: R2/A2. The candidate's category moved after the offer was issued. Category is a
    #: `PricingService.quote()` input, so the quote the customer accepted no longer
    #: describes what would be booked — a new quote is required, never a silent recompute.
    OFFER_VEHICLE_CATEGORY_CHANGED = "OFFER_VEHICLE_CATEGORY_CHANGED"
    #: R2/A2. A category we cannot resolve cannot be priced, and an unpriced booking is
    #: the harm this refusal exists to prevent.
    OFFER_VEHICLE_CATEGORY_UNRESOLVED = "OFFER_VEHICLE_CATEGORY_UNRESOLVED"
    #: R2/A3. The offer belongs to a conversation cycle that has closed.
    OFFER_CYCLE_CHANGED = "OFFER_CYCLE_CHANGED"
    #: R3/A4. Another writer holds this thread's offer transaction. A contention FACT, and
    #: a deterministic domain outcome — never `internal_error`, and never retried here: a
    #: retry loop at this boundary is how duplicate work gets created.
    OFFER_CONTENDED = "OFFER_CONTENDED"
    #: G3-7R/S4. `40P01`: two transactions took the offer resources in incompatible orders.
    #: Under ONE declared lock order this cannot happen, so it is reported separately from
    #: ordinary contention — an operator who sees this is looking at a protocol bug, not at
    #: load. The customer-facing outcome is the same honest handoff; the DIAGNOSIS differs.
    OFFER_DEADLOCK = "OFFER_DEADLOCK"
    #: G3-7R/S5. The final availability authority refused the submitted slot. The offer is
    #: deliberately left ISSUED: nothing about the offer became invalid, the slot did, and
    #: the customer may still book another one with the same token.
    SLOT_NO_LONGER_AVAILABLE = "SLOT_NO_LONGER_AVAILABLE"
    #: G3-7R/S5. A booking with no canonical inspection location cannot be priced, and an
    #: unpriced appointment is a commercial defect. The Data Exchange writer already
    #: refused this; the conversation writer did not.
    NO_CANONICAL_LOCATION = "NO_CANONICAL_LOCATION"
    #: G3-7R/S5. No usable submitted slot at all. A booking without a date and time is not
    #: an appointment, and the conversation writer used to create one with NULLs.
    NO_SUBMITTED_SLOT = "NO_SUBMITTED_SLOT"


#: Why an offer was invalidated. Stored in `terminal_reason`, so an operator can tell a
#: vehicle change from a zone change from a cycle reset without reading container logs.
class WithdrawReason:
    VEHICLE_CHANGED = "VEHICLE_CHANGED"
    LOCATION_CHANGED = "LOCATION_CHANGED"
    TEXT_CORRECTION = "TEXT_CORRECTION"
    CYCLE_RESET = "CYCLE_RESET"
    OFFER_REJECTED = "OFFER_REJECTED"
    HUMAN_HANDOFF = "HUMAN_HANDOFF"
    MOTO_HANDOFF = "MOTO_HANDOFF"
    REPLACED_BY_NEW_OFFER = "REPLACED_BY_NEW_OFFER"
    CONSUMED_BY_BOOKING = "CONSUMED_BY_BOOKING"
    #: G3-7R/S3. The thread's focus pointer moved off the candidate this offer was issued
    #: for. Until G3-7R the only thing standing between that and a booking of the WRONG
    #: vehicle was the `offer.candidate_id != candidate_id` comparison at acceptance — and
    #: that comparison is exactly what S3 removes, because the offer's own binding becomes
    #: THE candidate authority. So the withdrawal that the comparison used to stand in for
    #: now happens where the focus actually moves, which is also where an operator would
    #: expect to read it.
    FOCUS_CHANGED = "FOCUS_CHANGED"


#: The ONE list of scheduling facts whose validity depended on the withdrawn offer. It lives
#: here and nowhere else — G3-7N had it inline in one handler, which is why the location
#: handler never got it (G3-7N review B-01).
VEHICLE_DEPENDENT_SCHEDULING_FIELDS = (
    "last_offered_slots", "last_visible_slots", "active_requested_date",
    "last_requested_time", "preferred_day", "preferred_time",
)


def token_fingerprint(token: Optional[str]) -> Optional[str]:
    """sha256(token) hex, or None. Never reversible, never the raw token."""
    if not token or not isinstance(token, str):
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_date(value):
    """Coerce `state.preferred_day` (an ISO string) to a date. None when unusable — the
    offered slot is evidence, never a guess."""
    from datetime import date as _d
    if isinstance(value, _d) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return _d.fromisoformat(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def _as_time(value):
    """Coerce `state.preferred_time` ("HH:MM" or "HH:MM:SS") to a time. None when unusable."""
    from datetime import time as _t
    if isinstance(value, _t):
        return value
    if not value:
        return None
    raw = str(value).strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return None


class AdmissionOutcome:
    """What database-first admission decided. Three answers, and only one of them is a
    permission to execute a booking."""
    #: An ISSUED offer row exists for this thread and this token. Its immutable bindings
    #: travel with the grant, so the execution path resolves nothing of its own.
    GRANTED = "GRANTED"
    #: This offer already produced a committed booking. The authoritative booking is
    #: returned; nothing is created, nothing is refused and no counter moves.
    REPLAY = "REPLAY"
    #: Fails closed. `refusal` always says why.
    REFUSED = "REFUSED"


@dataclass(frozen=True)
class OfferAdmission:
    """The typed answer to "may this submitted token reach booking execution?".

    G3-7R/S2. This replaces `ConversationEngine._is_offer_acceptance_turn`, which tried to
    PREDICT at turn entry whether a turn would later book, and took the booking lock on the
    strength of that prediction. Two things were wrong with it and neither was reparable:

      * it answered from the mirror column and the payload shape, so an ordinary turn could
        be classified into the booking lock order — the R3 review measured a second
        conversation turn on the same thread DECLINED after 4.05s, a lost customer message;
      * it was documented as failing closed "to True", which means the fallback behaviour of
        the safety control was to serialise traffic that could never book.

    The stable question is not "will this turn book?" but "does the submitted token match an
    ISSUED offer row for this thread?" — one indexed read of immutable facts, decided at the
    point of acceptance rather than guessed at the door. There is no turn-entry gate left for
    an ordinary turn to be mis-classified into, and no phrase, alias or keyword catalogue is
    involved anywhere in it.

    DELIBERATELY NOT a `BookingCommand`. Execution is Smart Booking's
    `BookingAuthority` in the authorized `PRELAUNCH-SB2B-OFFER-ACCEPTANCE-CONVERGENCE`
    milestone; Gate 3 owns ADMISSION and this type is the admission grant, named so it
    cannot collide with Track B's typed command, result or idempotency key.
    """
    outcome: str
    refusal: Optional[str] = None
    offer_id: Optional[int] = None
    fingerprint: Optional[str] = None
    status: Optional[str] = None

    # ── the immutable bindings the grant carries, so nothing is re-resolved ──
    candidate_id: Optional[int] = None
    tipo_vehiculo: Optional[str] = None
    zone_group: Optional[str] = None
    zone_detail: Optional[str] = None
    cycle_key: Optional[int] = None
    #: HISTORICAL/AUDIT METADATA, never an acceptance binding. Owner policy (2026-10-13):
    #: the active booking Flow may let the customer choose a slot other than the
    #: representative one the offer recorded, so these are what the customer was SHOWN and
    #: are never compared against what they submitted.
    offered_date: Optional[object] = None
    offered_time: Optional[object] = None

    # ── populated on REPLAY only: the authoritative booking that already exists ──
    thread_revision_id: Optional[int] = None
    revision_id: Optional[int] = None

    @property
    def granted(self) -> bool:
        return self.outcome == AdmissionOutcome.GRANTED

    @property
    def replay(self) -> bool:
        return self.outcome == AdmissionOutcome.REPLAY

    def __bool__(self) -> bool:
        """An admission is used as a permission to EXECUTE, so a replay is not one: the
        booking already exists and must be returned, not created again."""
        return self.granted


@dataclass(frozen=True)
class OfferClaim:
    """The typed answer to "may this submission book?". `ok` is the only thing that
    authorizes a booking; `refusal` is always populated when it is False."""
    ok: bool
    refusal: Optional[str] = None
    offer_id: Optional[int] = None
    fingerprint: Optional[str] = None
    status: Optional[str] = None
    offered_date: Optional[object] = None
    offered_time: Optional[object] = None

    def __bool__(self) -> bool:          # a claim is used as a permission, so make it read like one
        return bool(self.ok)


class OfferLifecycle:
    """Issue, supersede, withdraw, expire and accept booking offers. Never commits."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── clock ────────────────────────────────────────────────────────────────

    def _postgres(self) -> bool:
        try:
            return self.db.get_bind().dialect.name == "postgresql"
        except Exception:
            return False

    def _now(self) -> datetime:
        """Database time where available — an offer's expiry must not depend on one
        container's clock."""
        if self._postgres():
            try:
                now = self.db.execute(select(func.now())).scalar()
                if isinstance(now, datetime):
                    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
            except Exception:                       # pragma: no cover - defensive
                logger.warning("OFFER clock fell back to process time")
        return datetime.now(timezone.utc)

    @staticmethod
    def _aware(value) -> Optional[datetime]:
        if not isinstance(value, datetime):
            return None
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    # ── reads ────────────────────────────────────────────────────────────────

    # ── reads NEVER flush the caller's pending writes (G3-7R) ────────────────
    #
    # This is not tidiness; it is a lock-ordering property, and getting it wrong undid
    # R3A's central guarantee. SQLAlchemy autoflushes before a query, so a read issued
    # here flushes whatever the CALLER has pending — and on a booking turn what the caller
    # has pending is the dedup-cursor write to `whatsapp_thread_states`. That UPDATE then
    # ran at the first read on the path, which is `admit()`, OUTSIDE any bound: a second
    # booking turn on the same thread waited on the first one's state-row lock with NO
    # TIMEOUT. Measured: 36s and climbing, where R3's own test requires a bounded wait of
    # about 4s.
    #
    # R3A kept a turn-entry acquisition partly to avoid exactly this — the cursor write
    # then landed on a row the turn already held. G3-7R deletes that gate (S2), because
    # SB2B-1 forbids entering the canonical authority holding this row. So the cursor write
    # must instead be allowed to wait where a bound exists: inside
    # `begin_offer_transaction()`. Suppressing autoflush on these reads is what puts it
    # there, and it costs nothing — none of them depends on a caller's unflushed state.

    def live_offer(self, thread_id: int):
        """The thread's one ISSUED offer, or None. The database guarantees "one"."""
        from ..models import WhatsAppBookingOffer as O
        with self.db.no_autoflush:
            return self.db.execute(
                select(O).where(O.thread_id == thread_id)
                .where(O.status == OfferStatus.ISSUED)
                .execution_options(populate_existing=True)).scalars().first()

    def offer_by_token(self, token: Optional[str]):
        from ..models import WhatsAppBookingOffer as O
        fp = token_fingerprint(token)
        if fp is None:
            return None
        with self.db.no_autoflush:
            return self.db.execute(
                select(O).where(O.token_fingerprint == fp)).scalars().first()

    def latest_offer(self, thread_id: int):
        """Most recent offer of any status — for the trace, so a refusal can name what it
        refused rather than reporting nothing at all."""
        from ..models import WhatsAppBookingOffer as O
        with self.db.no_autoflush:
            return self.db.execute(
                select(O).where(O.thread_id == thread_id)
                .order_by(O.id.desc())
                .execution_options(populate_existing=True)).scalars().first()

    # ── transitions ──────────────────────────────────────────────────────────

    def _terminalise(self, thread_id: int, *, status: str, reason: str,
                     offer_id: Optional[int] = None) -> int:
        """Move every LIVE offer of this thread to a terminal status. Conditional on
        'ISSUED', so it is idempotent and cannot re-terminalise history.

        S1. Resource 1 first. Every terminal transition — withdraw, supersede, expire —
        funnels through here, so one acquisition covers all of them.

        Convergence Phase 4: `offer_id` narrows it to ONE offer. A refusal terminalises the
        offer it examined and nothing else — never a replacement issued in the meantime.
        """
        from ..models import WhatsAppBookingOffer as O
        self._require_thread_state(thread_id)
        now = self._now()
        # Bounded even though resource 1 is held: the order makes a WAIT here impossible in
        # principle, and a bound is what turns "impossible" into "cannot hang" if some
        # future writer ever reaches the offer table another way.
        with self._bounded_lock_wait(DEFAULT_OFFER_LOCK_TIMEOUT_MS):
            stmt = (update(O).where(O.thread_id == thread_id)
                    .where(O.status == OfferStatus.ISSUED))
            if offer_id is not None:
                stmt = stmt.where(O.id == int(offer_id))
            res = self.db.execute(
                stmt.values(status=status, terminal_at=now, terminal_reason=reason[:64])
                .execution_options(synchronize_session="fetch"))
        return int(res.rowcount or 0)

    def withdraw(self, state, *, thread_id: int, reason: str,
                 clear_scheduling: bool = True) -> int:
        """Invalidate the thread's live offer because what it was computed from has moved.

        Clears the transport mirror and, by default, the scheduling facts the offer produced
        — the single field list lives in this module so no handler can own a partial copy of
        it. Returns how many offers were withdrawn (0 when there was nothing live, which is
        not an error: resubmitting an unchanged identity withdraws nothing).
        """
        n = self._terminalise(thread_id, status=OfferStatus.WITHDRAWN, reason=reason)
        self._clear_mirror(state)
        if clear_scheduling:
            self.clear_scheduling_fields(state)
        if n:
            logger.info("OFFER_WITHDRAWN thread_id=%s reason=%s offers=%s",
                        thread_id, reason, n)
        return n

    def supersede(self, state, *, thread_id: int, reason: str) -> int:
        """Invalidate the live offer because a newer one replaces it. Scheduling facts are
        NOT cleared: the caller is about to set its own."""
        n = self._terminalise(thread_id, status=OfferStatus.SUPERSEDED, reason=reason)
        if n:
            logger.info("OFFER_SUPERSEDED thread_id=%s reason=%s offers=%s",
                        thread_id, reason, n)
        return n

    def consume(self, state, *, thread_id: int, revision_id=None,
                thread_revision_id=None) -> None:
        """A booking was created from this thread's accepted offer: record what it produced.

        Acceptance already happened in `claim()`; this only attaches the artifacts, so a
        successful booking's offer row says WHICH revision it became.
        """
        from ..models import WhatsAppBookingOffer as O
        self._require_thread_state(thread_id)          # S1: resource 1 first
        with self._bounded_lock_wait(DEFAULT_OFFER_LOCK_TIMEOUT_MS):
            self.db.execute(
                update(O).where(O.thread_id == thread_id)
                .where(O.status == OfferStatus.ACCEPTED)
                .where(O.accepted_revision_id.is_(None))
                .values(accepted_revision_id=revision_id,
                        accepted_thread_revision_id=thread_revision_id)
                .execution_options(synchronize_session="fetch"))
        self._clear_mirror(state)

    def issue(self, state, *, thread_id: int, token: str, cycle_key: int,
              candidate_id=None, tipo_vehiculo=None, zone_group=None, zone_detail=None,
              offered_date=None, offered_time=None, flow_id=None):
        """Create the thread's one live offer, superseding whatever it replaces.

        The bindings are recorded here and compared at acceptance, which is what makes a
        booking refuse when the vehicle or the zone has moved — rather than relying on every
        handler to remember to withdraw.
        """
        from ..models import WhatsAppBookingOffer as O
        fp = token_fingerprint(token)
        if fp is None:
            raise ValueError("an offer cannot be issued without a token")
        # ── S1: ISSUE joins the protocol, and this is the line that kills the 40P01 ──
        # The architecture audit measured `deadlock_detected` in 3/3 trials between ISSUE
        # and acceptance, with the acceptance writer the victim every time. The cycle:
        # `supersede()` below locks the live offer row through its conditional UPDATE,
        # then `state.flow_booking_token = token` at the end of this method flushes as an
        # UPDATE on `whatsapp_thread_states` AT COMMIT — so ISSUE held resource 2 and
        # wanted resource 1 while acceptance held resource 1 and wanted resource 2.
        # Taking resource 1 HERE, before `supersede()`, removes the cycle entirely; the
        # mirror write at commit then needs a lock this transaction already holds.
        self._require_thread_state(thread_id)
        self.supersede(state, thread_id=thread_id,
                       reason=WithdrawReason.REPLACED_BY_NEW_OFFER)
        now = self._now()
        offer = O(thread_id=thread_id, cycle_key=int(cycle_key or 0),
                  candidate_id=candidate_id, tipo_vehiculo=tipo_vehiculo,
                  zone_group=zone_group, zone_detail=zone_detail,
                  offered_date=_as_date(offered_date), offered_time=_as_time(offered_time),
                  flow_id=(str(flow_id)[:40] if flow_id else None),
                  contract_version=OFFER_CONTRACT_VERSION, token_fingerprint=fp,
                  status=OfferStatus.ISSUED, issued_at=now,
                  expires_at=now + timedelta(seconds=_max_age_seconds()))
        self.db.add(offer)
        self.db.flush()
        if state is not None:
            state.flow_booking_token = token      # transport mirror, never the authority
        logger.info("OFFER_ISSUED thread_id=%s offer_id=%s candidate=%s zone=%s/%s",
                    thread_id, offer.id, candidate_id, zone_group, zone_detail)
        return offer

    # ── the canonical lock order (R3/A4) ─────────────────────────────────────

    @staticmethod
    def _sqlstate(exc) -> Optional[str]:
        """The raw SQLSTATE carried by `exc`, wherever the driver put it."""
        state = getattr(getattr(exc, "orig", None), "sqlstate", None)
        if state is None:
            state = getattr(exc, "sqlstate", None)
        return state

    @classmethod
    def contention_sqlstate(cls, exc) -> Optional[str]:
        """The SQLSTATE if `exc` is one this module answers with a typed outcome, else None.

        G3-7R/S4 widened this to include `40P01` again so a deadlock is still CAUGHT rather
        than escaping as an internal error — but `refusal_for_sqlstate()` classifies it
        separately, because a deadlock and a timed-out wait are not the same event.
        """
        state = cls._sqlstate(exc)
        return state if state in _HANDLED_SQLSTATES else None

    @staticmethod
    def refusal_for_sqlstate(sqlstate: Optional[str]) -> str:
        """S4. `40P01` is a protocol violation and says so; everything else is contention."""
        return (OfferRefusal.OFFER_DEADLOCK if sqlstate in _DEADLOCK_SQLSTATES
                else OfferRefusal.OFFER_CONTENDED)

    @classmethod
    def _note_sqlstate(cls, thread_id, sqlstate: Optional[str], where: str) -> str:
        """Log a handled lock event at the severity its diagnosis deserves, and return the
        typed refusal for it."""
        refusal = cls.refusal_for_sqlstate(sqlstate)
        if refusal == OfferRefusal.OFFER_DEADLOCK:
            # Under ONE declared lock order this is impossible, so it is never "load".
            logger.error(
                "OFFER_DEADLOCK thread_id=%s sqlstate=%s at %s — a writer took the offer "
                "resources outside OFFER_LOCK_ORDER; this is a protocol bug, not contention",
                thread_id, sqlstate, where)
        else:
            logger.info("OFFER_CONTENDED thread_id=%s sqlstate=%s at %s — typed refusal, "
                        "no retry", thread_id, sqlstate, where)
        return refusal

    @contextmanager
    def _bounded_lock_wait(self, lock_timeout_ms: int):
        """Bound ONE lock acquisition, then put the session back EXACTLY as it was.

        R3A scoped `SET LOCAL lock_timeout` to the acquisition it was chosen for, because
        setting it once at the start of a booking turn made it govern every later statement
        in that turn. G3-7R fixes what R3A's exit did: it restored `DEFAULT`, which on this
        database means *no limit at all*. The architecture audit named the consequence —
        a caller that had deliberately set a STRICTER bound, or an outer `_bounded_lock_wait`
        still in scope, was silently handed unlimited waiting by the inner block's cleanup.
        A safety control whose cleanup widens the bound it was protecting is worse than
        none, because the widening is invisible.

        So the prior value is READ on entry and that exact value is restored on exit:
        `DEFAULT` is written only when there genuinely was no prior value to put back.
        Nested bounds therefore compose — the inner block restores the outer block's bound,
        not the session's.

        The restore releases nothing: a row lock taken with FOR NO KEY UPDATE is held until
        the transaction ends, whatever `lock_timeout` says afterwards. The timeout governs
        how long a statement WAITS for a lock, never how long a lock is kept.

        After a transaction abort the restore cannot run — the session rejects every
        statement until rollback — and the original exception is what the caller must see,
        so the failure to restore is swallowed and never allowed to replace it.
        """
        if not self._postgres():
            yield
            return
        prior = None
        # ── R2/RV-01: THIS BLOCK'S OWN STATEMENTS MUST NOT FLUSH THE CALLER'S ──────
        # `Session.execute` autoflushes, so reading `SHOW lock_timeout` could push out
        # whatever the caller had pending — on a booking turn that is the dedup-cursor
        # UPDATE on `whatsapp_thread_states` — BEFORE the bound exists. Measured on the
        # granted path it happened not to; on the refusal paths it did, and the G3-7R
        # independent review measured the result at 42s against a 12s hold where the
        # parent answered in 4s.
        #
        # "Happened not to" is not a guarantee. Suppressing autoflush here makes the
        # ordering structural: nothing of the caller's reaches the database until the
        # bound is in force, whatever SQLAlchemy decides to flush on.
        with self.db.no_autoflush:
            try:
                raw = self.db.execute(text("SHOW lock_timeout")).scalar()
                candidate = str(raw).strip() if raw is not None else ""
                # Only a value that IS a PostgreSQL timeout literal may be re-inlined.
                # `SET` takes no bind parameter, so this check is what keeps the
                # statement closed.
                if _PG_TIMEOUT_LITERAL.match(candidate):
                    prior = candidate
                elif candidate:                     # pragma: no cover - defensive
                    logger.warning("OFFER lock_timeout %r is not a recognised literal — "
                                   "the bound will be restored as DEFAULT", candidate)
            except Exception:                       # pragma: no cover - defensive
                logger.debug("OFFER could not read lock_timeout", exc_info=True)
            # The value is coerced to int here, so nothing caller-supplied reaches the
            # statement as text.
            self.db.execute(text("SET LOCAL lock_timeout = %d" % int(lock_timeout_ms)))
        # The bound is now in force. The caller's pending writes are flushed DELIBERATELY,
        # inside it, so a contended cursor write is a bounded 55P03 the caller's handler
        # classifies as contention — never an open-ended wait.
        #
        # ── R3/RVR2-02: THE DELIBERATE FLUSH IS INSIDE THE PROTECTED REGION ───────
        # R2 put this line between the `SET LOCAL` and the `try`, so a flush that RAISED
        # left `lock_timeout` at this block's widened value and never restored the
        # caller's. The R2 independent review measured it: a caller holding a deliberate
        # 777ms bound entered the 4s block, the flush raised, and the session was left at
        # 4s — the same invisible-widening defect this helper was written to close, moved
        # from the exit path to the entry path.
        #
        # Inside the `try` the `finally` runs on every exit, raising or not, so the
        # caller's bound survives its own flush failing. The original exception still
        # propagates: `finally` restores and re-raises nothing of its own.
        try:
            self.db.flush()
            yield
        finally:
            with self.db.no_autoflush:
                try:
                    if prior is None:
                        self.db.execute(text("SET LOCAL lock_timeout = DEFAULT"))
                    else:
                        self.db.execute(text("SET LOCAL lock_timeout = '%s'" % prior))
                except Exception:
                    # The transaction is aborted or gone. Whatever the caller is about to
                    # see must be the ORIGINAL exception, never this one.
                    logger.debug("OFFER could not restore lock_timeout", exc_info=True)

    # ── step 1 of OFFER_LOCK_ORDER, for every writer (S1) ────────────────────

    def _acquire_thread_state(self, thread_id: int, *,
                              lock_timeout_ms: int = DEFAULT_OFFER_LOCK_TIMEOUT_MS
                              ) -> Optional[str]:
        """Take `whatsapp_thread_states` — resource 1 — under a bounded wait.

        S1. Every transition in this module calls this FIRST, including `issue()`. Taking
        the same row lock twice inside one transaction is free, so a caller that already
        entered through `begin_offer_transaction()` pays nothing for the second call; what
        the repetition buys is that no writer can reach the offer table without having
        passed through resource 1, whoever calls it and in whatever order the callers were
        written.

        Returns None to proceed, or a typed refusal. Does NOT roll back: a transition is
        part of a caller's transaction and only the caller knows what else is in it. The
        acceptance entry point (`begin_offer_transaction`) does roll back, because it owns
        the start of the transaction.
        """
        if not self._postgres():
            return None
        try:
            with self._bounded_lock_wait(lock_timeout_ms):
                # FOR NO KEY UPDATE: it conflicts with itself and with the UPDATE the
                # engine performs on the same row — exactly the mutual exclusion wanted —
                # without blocking readers or the foreign keys that reference the row.
                self.db.execute(
                    text("SELECT 1 FROM whatsapp_thread_states WHERE thread_id = :t "
                         "FOR NO KEY UPDATE").bindparams(t=int(thread_id)))
            from .flush_order import note_thread_states_locked
            note_thread_states_locked(self.db, [thread_id])
            return None
        except Exception as exc:
            sqlstate = self.contention_sqlstate(exc)
            if sqlstate is None:
                raise
            return self._note_sqlstate(thread_id, sqlstate, "the thread-state row")

    def _require_thread_state(self, thread_id: int) -> None:
        """Take resource 1, or refuse to write at all.

        The writers' form of `_acquire_thread_state`. A transition that proceeded without
        resource 1 would both break the declared order and leave its own offer-table write
        unbounded, so it raises instead.
        """
        refusal = self._acquire_thread_state(thread_id)
        if refusal is not None:
            raise OfferProtocolContended(refusal, thread_id)

    def begin_offer_transaction(self, thread_id: int, *,
                                lock_timeout_ms: int = DEFAULT_OFFER_LOCK_TIMEOUT_MS
                                ) -> Optional[str]:
        """Enter the offer-acceptance transaction in the ONE canonical lock order.

        R3/A4. Step 1 is the thread's own state row; step 2 is the offer row, taken inside
        `claim()`. Both booking writers call this first, so they can no longer take the two
        resources in opposite orders — which is what deadlocked them. `lock_timeout` makes
        the wait bounded, so a contended turn gets a typed answer instead of blocking.

        Returns None when the transaction may proceed, or a typed `OfferRefusal` when the
        thread is contended. On contention the aborted transaction is rolled back first, so
        the caller is handed a usable session.
        """
        if not self._postgres():
            return None
        try:
            with self._bounded_lock_wait(lock_timeout_ms):
                self.db.execute(
                    text("SELECT 1 FROM whatsapp_thread_states WHERE thread_id = :t "
                         "FOR NO KEY UPDATE").bindparams(t=int(thread_id)))
            from .flush_order import note_thread_states_locked
            note_thread_states_locked(self.db, [thread_id])
            return None
        except Exception as exc:
            sqlstate = self.contention_sqlstate(exc)
            try:
                self.db.rollback()
            except Exception:                       # pragma: no cover - defensive
                logger.exception("OFFER rollback after contention failed")
            if sqlstate is None:
                raise
            return self._note_sqlstate(thread_id, sqlstate,
                                       "the acceptance transaction entry")

    # ── database-first admission (S2) ────────────────────────────────────────

    def admit(self, *, thread_id: int, token: Optional[str]) -> OfferAdmission:
        """Does the submitted token match an ISSUED offer row for THIS thread?

        S2. The whole of the acceptance boundary, and nothing else. It takes no lock, reads
        no mirror, consults no payload shape and recognises no customer phrase: one indexed
        read on `token_fingerprint` — which is UNIQUE — and the immutable facts on the row
        it finds. Ordinary conversation traffic therefore performs no booking lock
        operation, because there is no longer any turn-entry gate for it to pass through.

        Three outcomes, and `GRANTED` is the only permission:

          GRANTED  an ISSUED row for this thread. Its bindings travel with the grant, so
                   the execution path re-resolves nothing and the two transports cannot
                   disagree about what is being booked.
          REPLAY   the offer is ACCEPTED and already names a committed booking. The caller
                   must return THAT booking: a replay is not a refusal and nothing about it
                   is a counter to increment.
          REFUSED  everything else, including every shape of unknown, malformed, foreign,
                   withdrawn, superseded or expired input. Fails closed: absence of an
                   offer row is never permission.

        An ACCEPTED offer with NO recorded booking is refused rather than replayed. That
        combination means the acceptance committed and the booking did not — so there is no
        authoritative booking to return, and inventing one would be worse than refusing.
        """
        if not token or not isinstance(token, str) or not token.strip():
            return OfferAdmission(AdmissionOutcome.REFUSED, OfferRefusal.NO_TOKEN)
        fp = token_fingerprint(token)
        offer = self.offer_by_token(token)
        if offer is None:
            # No authority row for this token: fabricated, tampered, never issued, or a
            # mirror left by an application older than this table. All refusals.
            return OfferAdmission(AdmissionOutcome.REFUSED, OfferRefusal.UNKNOWN_TOKEN,
                                  fingerprint=fp)
        if offer.thread_id != thread_id:
            return OfferAdmission(AdmissionOutcome.REFUSED, OfferRefusal.FOREIGN_OFFER,
                                  offer_id=offer.id, fingerprint=fp, status=offer.status)

        if offer.status == OfferStatus.ACCEPTED:
            if offer.accepted_thread_revision_id is not None:
                return OfferAdmission(
                    AdmissionOutcome.REPLAY, None, offer_id=offer.id, fingerprint=fp,
                    status=offer.status, candidate_id=offer.candidate_id,
                    tipo_vehiculo=offer.tipo_vehiculo, zone_group=offer.zone_group,
                    zone_detail=offer.zone_detail, cycle_key=offer.cycle_key,
                    offered_date=offer.offered_date, offered_time=offer.offered_time,
                    thread_revision_id=offer.accepted_thread_revision_id,
                    revision_id=offer.accepted_revision_id)
            return OfferAdmission(AdmissionOutcome.REFUSED,
                                  OfferRefusal.OFFER_ALREADY_ACCEPTED, offer_id=offer.id,
                                  fingerprint=fp, status=offer.status)

        if offer.status != OfferStatus.ISSUED:
            refusal = {
                OfferStatus.WITHDRAWN: OfferRefusal.OFFER_WITHDRAWN,
                OfferStatus.SUPERSEDED: OfferRefusal.SUPERSEDED_OFFER,
                OfferStatus.EXPIRED: OfferRefusal.OFFER_EXPIRED,
            }.get(offer.status, OfferRefusal.UNKNOWN_TOKEN)
            return OfferAdmission(AdmissionOutcome.REFUSED, refusal, offer_id=offer.id,
                                  fingerprint=fp, status=offer.status)

        expires = self._aware(offer.expires_at)
        if expires is not None and self._now() > expires:
            # Not terminalised here: admission is a READ. `claim()` performs the lazy
            # expiry under the lock, where the write belongs.
            return OfferAdmission(AdmissionOutcome.REFUSED, OfferRefusal.OFFER_EXPIRED,
                                  offer_id=offer.id, fingerprint=fp, status=offer.status)

        return OfferAdmission(
            AdmissionOutcome.GRANTED, None, offer_id=offer.id, fingerprint=fp,
            status=offer.status, candidate_id=offer.candidate_id,
            tipo_vehiculo=offer.tipo_vehiculo, zone_group=offer.zone_group,
            zone_detail=offer.zone_detail, cycle_key=offer.cycle_key,
            offered_date=offer.offered_date, offered_time=offer.offered_time)

    def record_replay(self, offer_id: int, *, thread_id: int) -> None:
        """Count a replay against its offer, so an operator can tell one from an acceptance.

        S6. A replay is NOT a refusal: it never touches `refused_count` or `last_refusal`,
        which is precisely the confusion the architecture audit measured — a Flow completion
        arriving after the booking committed left the offer reading
        `ACCEPTED, refused_count=1, last_refusal='OFFER_ALREADY_ACCEPTED'`, the shape of a
        rejected submission on the happy path.

        R2/RV-02. `thread_id` is now required, and resource 1 is taken before the write.
        This was one of four bookkeeping writers that mutated the offer table without
        entering the declared lock order at all; the independent review showed the inverse
        order deadlocks `40P01` on demand, so "no writer currently follows this with a
        thread-state write" was a property of today's call sites, not of the protocol.
        """
        from ..models import WhatsAppBookingOffer as O
        self._require_thread_state(int(thread_id))
        try:
            with self._bounded_lock_wait(DEFAULT_OFFER_LOCK_TIMEOUT_MS):
                self.db.execute(
                    update(O).where(O.id == int(offer_id))
                    .values(replay_count=(O.replay_count + 1), last_replay_at=self._now())
                    .execution_options(synchronize_session="fetch"))
        except Exception:                      # auditing must never widen authority
            logger.warning("OFFER replay not recorded offer_id=%s", offer_id, exc_info=True)

    # ── acceptance ───────────────────────────────────────────────────────────

    def _claim_checks(self, state, *, thread_id: int, token: Optional[str],
                      candidate_id=None, tipo_vehiculo=None, zone_group=None,
                      zone_detail=None, cycle_key=None, lock: bool = True,
                      lock_timeout_ms: int = DEFAULT_OFFER_LOCK_TIMEOUT_MS
                      ) -> "tuple[Optional[OfferClaim], object]":
        """Every reason to refuse except losing the race. `(refusal_or_None, offer)`.

        Shared by `verify_bindings()` (read-only, `lock=False`, run BEFORE availability
        revalidation) and `claim()` (`lock=True`, run after it, and followed by the
        conditional UPDATE that decides the race). One implementation, so the two callers
        cannot disagree about what a valid offer is.
        """
        from ..models import WhatsAppBookingOffer as O
        if not token or not isinstance(token, str) or not token.strip():
            return (OfferClaim(False, OfferRefusal.NO_TOKEN), None)
        fp = token_fingerprint(token)
        # S1. Resource 1 before resource 2, for callers that did not enter through
        # `begin_offer_transaction()`. Free for callers that did: taking the same row lock
        # twice inside one transaction costs nothing.
        _blocked = self._acquire_thread_state(thread_id, lock_timeout_ms=lock_timeout_ms)
        if _blocked is not None:
            return (OfferClaim(False, _blocked, fingerprint=fp), None)

        # `populate_existing` is not optional here. A session that already holds this offer
        # — because an earlier operation in the same turn superseded or withdrew it through
        # a bulk UPDATE — would otherwise hand back the STALE identity-mapped instance, and
        # a terminal offer would read as ISSUED and be accepted.
        stmt = select(O).where(O.token_fingerprint == fp).execution_options(
            populate_existing=True)
        if lock and self._postgres():
            stmt = stmt.with_for_update()
        try:
            # R3A: step 2 carries its OWN bound, because step 1 no longer leaves one
            # standing for the rest of the transaction.
            with self._bounded_lock_wait(lock_timeout_ms):
                offer = self.db.execute(stmt).scalars().first()
        except Exception as exc:
            # Step 2 of OFFER_LOCK_ORDER. With the order enforced this is a bounded WAIT
            # that timed out, not a deadlock — and it is a contention fact, so it answers
            # with a typed refusal rather than escaping as an internal failure.
            sqlstate = self.contention_sqlstate(exc)
            if sqlstate is None:
                raise
            try:
                self.db.rollback()
            except Exception:                       # pragma: no cover - defensive
                logger.exception("OFFER rollback after claim contention failed")
            return (OfferClaim(False,
                               self._note_sqlstate(thread_id, sqlstate, "the offer row"),
                               fingerprint=fp), None)

        if offer is None:
            # No authority row for this token. Either fabricated/tampered, or a mirror left
            # by an application older than this table. Both are refusals: the only thing a
            # missing record can honestly authorize is nothing.
            mirror = getattr(state, "flow_booking_token", None)
            refusal = (OfferRefusal.NO_OFFER_RECORD if mirror
                       else OfferRefusal.UNKNOWN_TOKEN)
            return (OfferClaim(False, refusal, fingerprint=fp), None)

        if offer.thread_id != thread_id:
            return (self._refuse(offer, OfferRefusal.FOREIGN_OFFER, fp), offer)

        if offer.status != OfferStatus.ISSUED:
            refusal = {
                OfferStatus.WITHDRAWN: OfferRefusal.OFFER_WITHDRAWN,
                OfferStatus.SUPERSEDED: OfferRefusal.SUPERSEDED_OFFER,
                OfferStatus.ACCEPTED: OfferRefusal.OFFER_ALREADY_ACCEPTED,
                OfferStatus.EXPIRED: OfferRefusal.OFFER_EXPIRED,
            }.get(offer.status, OfferRefusal.UNKNOWN_TOKEN)
            return (self._refuse(offer, refusal, fp), offer)

        expires = self._aware(offer.expires_at)
        if expires is not None and self._now() > expires:
            self._terminalise(thread_id, status=OfferStatus.EXPIRED,
                              reason=OfferRefusal.OFFER_EXPIRED, offer_id=offer.id)
            self._clear_mirror(state)
            return (self._refuse(offer, OfferRefusal.OFFER_EXPIRED, fp,
                                 status=OfferStatus.EXPIRED), offer)

        # The bindings. A live offer for a vehicle or a zone that has since moved is not a
        # permission to book the old one — this is the second line behind withdrawal, and
        # the one that holds when a path forgot to withdraw.
        #
        # ── S3: what the candidate comparison means AFTER the authority moved ────
        # The offer's own `candidate_id` is now THE candidate authority, so the caller no
        # longer resolves a focus of its own and hands the result in — it resolves THIS id
        # to a row and hands back the id of the row it actually found. So this asks the
        # sharper question: is the candidate this offer is bound to still there to be
        # booked, and does the thread's own focus still agree with it? A deleted row, a row
        # outside the active cycle, or a focus pointer that has moved elsewhere all arrive
        # here as None and are refused.
        if offer.candidate_id != candidate_id:
            return (self._refuse(offer, OfferRefusal.OFFER_CANDIDATE_CHANGED, fp), offer)

        # ── R2/A3: the cycle this offer belongs to ────────────────────────────────
        # The ordinary cycle reset already withdraws, so this is the guard for a cycle
        # that moved without it. Compared because it is declared a binding; a recorded
        # binding that is never evaluated is a false assurance.
        if cycle_key is not None and int(offer.cycle_key or 0) != int(cycle_key):
            self._terminalise(thread_id, status=OfferStatus.WITHDRAWN,
                              reason=OfferRefusal.OFFER_CYCLE_CHANGED, offer_id=offer.id)
            self._clear_mirror(state)
            return (self._refuse(offer, OfferRefusal.OFFER_CYCLE_CHANGED, fp,
                                 status=OfferStatus.WITHDRAWN), offer)

        # ── R2/A2: the PRICING CONTEXT, not merely the candidate's identity ───────
        # `PricingService.quote(db, tipo_vehiculo, zone_group, zone_detail)` takes the
        # category, so an offer whose category has moved describes a quote that no longer
        # applies — even though the candidate id and the zone are untouched. The value
        # compared is the canonical column on the candidate the CALLER resolved through its
        # own authority; nothing here reads customer prose, model wording or an alias.
        if offer.tipo_vehiculo != tipo_vehiculo:
            self._terminalise(thread_id, status=OfferStatus.WITHDRAWN,
                              reason=OfferRefusal.OFFER_VEHICLE_CATEGORY_CHANGED, offer_id=offer.id)
            self._clear_mirror(state)
            return (self._refuse(offer, OfferRefusal.OFFER_VEHICLE_CATEGORY_CHANGED, fp,
                                 status=OfferStatus.WITHDRAWN), offer)
        # A category we cannot resolve cannot be priced, and an unpriced booking is exactly
        # the harm A2 produced. Refuse rather than book something with no price basis.
        if tipo_vehiculo is None:
            self._terminalise(thread_id, status=OfferStatus.WITHDRAWN,
                              reason=OfferRefusal.OFFER_VEHICLE_CATEGORY_UNRESOLVED, offer_id=offer.id)
            self._clear_mirror(state)
            return (self._refuse(offer, OfferRefusal.OFFER_VEHICLE_CATEGORY_UNRESOLVED, fp,
                                 status=OfferStatus.WITHDRAWN), offer)

        if (offer.zone_group, offer.zone_detail) != (zone_group, zone_detail):
            return (self._refuse(offer, OfferRefusal.OFFER_LOCATION_CHANGED, fp), offer)

        return (None, offer)

    def claim(self, state, *, thread_id: int, token: Optional[str],
              candidate_id=None, tipo_vehiculo=None, zone_group=None, zone_detail=None,
              cycle_key=None) -> OfferClaim:
        """May this submission become a booking? Fails closed, and never commits.

        Every check is a reason to refuse; nothing here can widen authority. The row is
        locked FOR UPDATE before anything is read from it, so a concurrent withdrawal or a
        second submission of the same token serialises behind this one, and the final
        transition is a conditional UPDATE off 'ISSUED'.

        G3-7R states what that does and does not buy. Those two statements make the race an
        ordinary typed refusal instead of an exception, and that is worth having — but they
        are application code, and the R3A independent review measured that removing BOTH of
        them produced TWO bookings from ONE offer in 6 of 6 trials while the whole test
        suite stayed identity-identical. The GUARANTEE that one offer yields at most one
        booking is the partial unique index on `thread_revisions.offer_id`; this method is
        the coordination in front of it.

        The checks themselves live in `_claim_checks`, shared with `verify_bindings()`, so
        a caller that validated the bindings before revalidating availability (S5) and this
        method cannot disagree about what a valid offer is.
        """
        from ..models import WhatsAppBookingOffer as O
        refusal, offer = self._claim_checks(
            state, thread_id=thread_id, token=token, candidate_id=candidate_id,
            tipo_vehiculo=tipo_vehiculo, zone_group=zone_group, zone_detail=zone_detail,
            cycle_key=cycle_key, lock=True)
        if refusal is not None:
            return refusal
        fp = token_fingerprint(token)

        now = self._now()
        res = self.db.execute(
            update(O).where(O.id == offer.id)
            .where(O.status == OfferStatus.ISSUED)          # the race is decided here
            .values(status=OfferStatus.ACCEPTED, accepted_at=now, terminal_at=now,
                    terminal_reason=WithdrawReason.CONSUMED_BY_BOOKING)
            # `fetch`, not False: after a claim, anything in this session that reads the
            # offer must see ACCEPTED. A stale ISSUED instance is exactly how a second
            # acceptance would talk itself into existence.
            .execution_options(synchronize_session="fetch"))
        if not res.rowcount:
            return self._refuse(offer, OfferRefusal.OFFER_ALREADY_ACCEPTED, fp,
                                status=OfferStatus.ACCEPTED)

        logger.info("OFFER_ACCEPTED thread_id=%s offer_id=%s", thread_id, offer.id)
        return OfferClaim(True, None, offer_id=offer.id, fingerprint=fp,
                          status=OfferStatus.ACCEPTED,
                          offered_date=offer.offered_date, offered_time=offer.offered_time)

    def verify_bindings(self, state, *, thread_id: int, token: Optional[str],
                        candidate_id=None, tipo_vehiculo=None, zone_group=None,
                        zone_detail=None, cycle_key=None) -> Optional[str]:
        """Every reason to refuse EXCEPT losing the race. Read-only when it passes.

        G3-7R split this out of `claim()` so the ORDER of the two kinds of refusal could be
        got right. Both transports must revalidate availability before anything is accepted
        (S5), and revalidation sits between admission and acceptance — so if the binding
        comparisons stayed inside `claim()`, a submission that was BOTH stale-slot and
        category-changed would be reported as a slot conflict and the offer would be left
        ISSUED. R2 built the category refusal precisely because a moved category means the
        accepted quote no longer describes what would be booked, and it made that
        withdrawal durable. Masking it behind the calendar would undo that.

        So the order is: admission, bindings, availability, acceptance. The bindings are
        IMMUTABLE columns on the offer row, so comparing them before the row lock is safe —
        the only mutable thing is `status`, and `claim()` re-reads that under the lock and
        decides the race with a conditional UPDATE.

        Returns the refusal, or None to proceed. On a mismatch that invalidates the offer
        it terminalises and clears the mirror, because that write IS the refusal's
        durability — which is why "read-only" is qualified with "when it passes".
        """
        refusal, _offer = self._claim_checks(
            state, thread_id=thread_id, token=token, candidate_id=candidate_id,
            tipo_vehiculo=tipo_vehiculo, zone_group=zone_group, zone_detail=zone_detail,
            cycle_key=cycle_key, lock=False)
        return None if refusal is None else refusal.refusal

    def _refuse(self, offer, refusal: str, fingerprint: str,
                status: Optional[str] = None) -> OfferClaim:
        """Record the refusal against the offer it refused, then report it.

        G3-7P §9. A Flow turn persists no hybrid-trace row, so without this the
        classification would exist only in container logs — which is precisely the
        container-dependence CLAUDE.md forbids for operator forensics. The counters are on
        the offer because that is the thing the submission was refused against.
        """
        from ..models import WhatsAppBookingOffer as O
        # R2/RV-02. Resource 1 first. Free here in practice — every caller reaches this
        # through `_claim_checks`, which has already taken it — and the point is that the
        # order is now enforced by the writer rather than inferred from its callers.
        self._require_thread_state(int(offer.thread_id))
        try:
            with self._bounded_lock_wait(DEFAULT_OFFER_LOCK_TIMEOUT_MS):
                self.db.execute(
                    update(O).where(O.id == offer.id)
                    .values(refused_count=(O.refused_count + 1), last_refusal=refusal[:64],
                            last_refused_at=self._now())
                    .execution_options(synchronize_session="fetch"))
        except Exception:                      # auditing must never widen authority
            logger.warning("OFFER refusal not recorded offer_id=%s", offer.id, exc_info=True)
        return OfferClaim(False, refusal, offer_id=offer.id, fingerprint=fingerprint,
                          status=status or offer.status)

    # ── the database guarantee, translated (S1) ──────────────────────────────

    def authoritative_booking(self, offer_id) -> "tuple[Optional[int], Optional[int]]":
        """`(thread_revision_id, revision_id)` of the ONE booking this offer produced.

        Read from the booking projection rather than from the offer's own
        `accepted_*` columns, because this is called precisely when the loser of a race has
        just been rolled back: the offer row it can see is the one the WINNER committed, and
        the booking is the authority on what exists. Returns `(None, None)` when no booking
        is recorded, which the caller must treat as a refusal and never as a replay.
        """
        from ..models import ThreadRevision
        from ..models import WhatsAppBookingOffer as O
        if offer_id is None:
            return (None, None)
        thread_revision_id = self.db.execute(
            select(ThreadRevision.id).where(ThreadRevision.offer_id == int(offer_id))
            .execution_options(populate_existing=True)).scalar()
        if thread_revision_id is None:
            return (None, None)
        # The CRM-side Revision is reachable only through the offer's own annotation, which
        # `consume()` writes in the same transaction as the booking. None is an honest
        # answer here and the caller does not depend on it: the ThreadRevision is what
        # makes the booking authoritative.
        revision_id = self.db.execute(
            select(O.accepted_revision_id).where(O.id == int(offer_id))).scalar()
        return (int(thread_revision_id), revision_id)

    @staticmethod
    def is_booking_projection_violation(exc) -> bool:
        """True when `exc` is the database refusing a SECOND booking for one offer.

        S1. The partial unique index `uq_thread_revision_one_booking_per_offer` is the
        guarantee; this is the one place that recognises it, so no caller has to pattern-
        match driver text and none of them may leak `IntegrityError`, the SQLSTATE or the
        constraint name to a customer. `23505 unique_violation` plus the constraint's own
        name — the name is matched, so an unrelated unique violation is NOT silently
        reported as a duplicate booking.
        """
        orig = getattr(exc, "orig", None)
        sqlstate = getattr(orig, "sqlstate", None) or getattr(exc, "sqlstate", None)
        blob = "%s %s" % (exc, orig)
        if sqlstate == "23505" and BOOKING_PROJECTION_CONSTRAINT in blob:
            return True
        # SQLite reports the index by name with no SQLSTATE at all.
        return (sqlstate is None and BOOKING_PROJECTION_CONSTRAINT in blob)

    def finalise_admission_refusal(self, state, *, thread_id: int, admission) -> None:
        """Make an admission refusal DURABLE where the refusal invalidates the offer.

        S2/S6. `admit()` is a READ — it takes no lock and performs no transition, which is
        what lets ordinary traffic past it for free. But two of its refusals are also
        FACTS about the offer that must be recorded, and R3 built that durability
        deliberately: an expired offer must end as `EXPIRED` with the transport mirror
        cleared, or the thread keeps a truthy mirror for an offer that can never be
        accepted, and the L4.7W5-F7A scheduling gate stays closed on it.

        G3-7R had to add this because the reordering moved the expiry detection EARLIER
        than `claim()`, which is where the terminalisation used to happen. Measured: the
        DX expiry refusal stopped being durable, caught by R3's own test. The alternative —
        letting `admit()` write — would make the read that every turn passes through a
        writer, which is the whole thing S2 removed.

        The caller commits. Both transports already do, on the same refusal path.
        """
        refusal = getattr(admission, "refusal", None)
        status = getattr(admission, "status", None)
        if refusal == OfferRefusal.OFFER_EXPIRED and status == OfferStatus.ISSUED:
            self._terminalise(thread_id, status=OfferStatus.EXPIRED,
                              reason=OfferRefusal.OFFER_EXPIRED)
            self._clear_mirror(state)
        self.record_refusal(getattr(admission, "offer_id", None), refusal,
                            thread_id=thread_id)

    def record_refusal(self, offer_id, refusal: str, *, thread_id: int) -> None:
        """Record a refusal against the offer it refused, IN the caller's transaction.

        S2. `_refuse()` does this for the refusals `claim()` decides; admission and the
        final availability revalidation decide theirs before `claim()` is ever called, and
        their verdicts are no less a decision an operator must be able to audit. A Flow turn
        persists no hybrid-trace row, so without this the classification would exist only in
        container logs — exactly the container-dependence CLAUDE.md forbids for forensics.

        Never raises: auditing must not widen or narrow authority.
        """
        from ..models import WhatsAppBookingOffer as O
        if offer_id is None or not refusal:
            return
        # R2/RV-02: resource 1 before the offer row, like every other writer.
        self._require_thread_state(int(thread_id))
        try:
            with self._bounded_lock_wait(DEFAULT_OFFER_LOCK_TIMEOUT_MS):
                self.db.execute(
                    update(O).where(O.id == int(offer_id))
                    .values(refused_count=(O.refused_count + 1), last_refusal=refusal[:64],
                            last_refused_at=self._now())
                    .execution_options(synchronize_session="fetch"))
        except Exception:
            logger.warning("OFFER refusal not recorded offer_id=%s", offer_id, exc_info=True)

    def record_refusal_out_of_band(self, token: Optional[str], refusal: str) -> bool:
        """Record a refusal on its offer in its OWN CLEAN, BOUNDED transaction.

        R3. A caller whose transaction aborted cannot write the in-transaction counters in
        `_refuse()`, so this records the fact afterwards — a durable refusal rather than a
        log line.

        R2/RV-02 made the transaction clean and ordered rather than merely separate. It now:

          1. rolls back first, so it never inherits a caller's aborted or half-built
             transaction — the previous version issued its UPDATE into whatever state the
             caller left behind;
          2. reads the offer to learn its thread, under `no_autoflush`, so nothing of the
             caller's is dragged out;
          3. takes resource 1 through the bounded helper, like every other writer;
          4. writes and commits, or on contention writes NOTHING and answers False.

        Returns True only when a row was actually recorded.
        """
        from ..models import WhatsAppBookingOffer as O
        fp = token_fingerprint(token)
        if fp is None:
            return False
        try:
            self.db.rollback()                      # (1) start clean
        except Exception:                           # pragma: no cover - defensive
            logger.debug("OFFER out-of-band rollback failed", exc_info=True)
        offer = self.offer_by_token(token)          # (2) no_autoflush read
        if offer is None:
            return False
        if self._acquire_thread_state(int(offer.thread_id)) is not None:   # (3)
            logger.info("OFFER out-of-band refusal not recorded thread_id=%s — contended; "
                        "nothing written", offer.thread_id)
            try:
                self.db.rollback()
            except Exception:                       # pragma: no cover - defensive
                pass
            return False
        try:
            with self._bounded_lock_wait(DEFAULT_OFFER_LOCK_TIMEOUT_MS):   # (4)
                res = self.db.execute(
                    update(O).where(O.id == offer.id)
                    .values(refused_count=(O.refused_count + 1), last_refusal=refusal[:64],
                            last_refused_at=self._now())
                    .execution_options(synchronize_session="fetch"))
            self.db.commit()
            return bool(res.rowcount)
        except Exception:
            logger.warning("OFFER out-of-band refusal not recorded", exc_info=True)
            try:
                self.db.rollback()
            except Exception:                       # pragma: no cover - defensive
                pass
            return False

    def withdraw_if_candidate_moved(self, state, *, thread_id: int,
                                    candidate_id) -> int:
        """Withdraw the live offer when the thread's focus has moved off the candidate it
        was issued for.

        S3. This is the withdrawal that the `offer.candidate_id != candidate_id` comparison
        at acceptance used to stand in for. That comparison is still there and still
        refuses, but it can no longer be the mechanism: once the offer's own binding IS the
        candidate authority, a caller that has stopped resolving a focus of its own has
        nothing of its own left to disagree with. So the fact is recorded where it happens
        — the focus pointer moving — instead of being inferred at the boundary two paths
        away, and `terminal_reason` tells an operator which of the two it was.

        Scoped narrowly on purpose. It withdraws only when there IS a live offer, that
        offer names a candidate, and the new focus is a DIFFERENT candidate. Re-confirming
        the same candidate withdraws nothing, which is what keeps the established
        `VEHICLE_CHANGED` and `LOCATION_CHANGED` withdrawals owning their own cases.
        Scheduling facts are cleared, because the slot was negotiated for the old vehicle.
        """
        offer = self.live_offer(thread_id)
        if offer is None or offer.candidate_id is None:
            return 0
        if candidate_id is not None and int(offer.candidate_id) == int(candidate_id):
            return 0
        n = self.withdraw(state, thread_id=thread_id,
                          reason=WithdrawReason.FOCUS_CHANGED)
        if n:
            logger.info("OFFER_WITHDRAWN thread_id=%s reason=FOCUS_CHANGED offer_candidate=%s "
                        "new_focus=%s", thread_id, offer.candidate_id, candidate_id)
        return n

    def refine_terminal_reason(self, thread_id: int, *, when_reason: str,
                               to_reason: str) -> int:
        """Replace a provisional terminal reason with the specific one, same transaction.

        S3. Within one turn the focus-move withdrawal can fire BEFORE the handler that
        knows WHY the focus moved. `_apply_candidate` moves the pointer and withdraws with
        `FOCUS_CHANGED`; the clarification-completion handler then withdraws with
        `VEHICLE_CHANGED` or `LOCATION_CHANGED` and finds nothing left to withdraw — so the
        offer would carry the mechanism instead of the cause, and an operator reading
        `terminal_reason` would lose the distinction G3-7N built it for.
        
        This is not rewriting history: both writes happen inside the SAME transaction and
        the SAME turn, so no observer ever reads the provisional value. It is conditional on
        `when_reason`, so it can only ever refine the reason this turn itself wrote.
        """
        from ..models import WhatsAppBookingOffer as O
        self._require_thread_state(thread_id)
        res = self.db.execute(
            update(O).where(O.thread_id == thread_id)
            .where(O.status == OfferStatus.WITHDRAWN)
            .where(O.terminal_reason == when_reason[:64])
            .values(terminal_reason=to_reason[:64])
            .execution_options(synchronize_session="fetch"))
        n = int(res.rowcount or 0)
        if n:
            logger.info("OFFER_WITHDRAWN thread_id=%s reason refined %s -> %s",
                        thread_id, when_reason, to_reason)
        return n

    # ── state mirror ─────────────────────────────────────────────────────────

    @staticmethod
    def _clear_mirror(state) -> None:
        """The transport mirror follows the authority. Clearing it is now SAFE — authority
        lives in the offer row — and it is what restores L4.7W5-F7A: the eight production
        readers of `flow_booking_token` truthiness see "no live offer" again, so a withdrawn
        offer no longer blocks the deterministic scheduling path or reads as outstanding in
        the trace.
        """
        if state is None:
            return
        if getattr(state, "flow_booking_token", None) is not None:
            state.flow_booking_token = None

    @staticmethod
    def clear_scheduling_fields(state) -> None:
        """Null every scheduling fact whose validity depended on the withdrawn offer."""
        if state is None:
            return
        for field in VEHICLE_DEPENDENT_SCHEDULING_FIELDS:
            if getattr(state, field, None) is not None:
                setattr(state, field, None)
