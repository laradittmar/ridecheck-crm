"""Clarification attempt lifecycle — the database-enforced one-attempt invariant.

G3-7F clarification lifecycle, implementing the architecture selected on 2026-10-06.

WHY THIS IS NOT APPLICATION LOGIC
---------------------------------
Three milestones tried to bound clarification in Python and all three failed the same way.
The fact "this family already had its attempt in this cycle" lived in the same boolean as
"a Flow is currently outstanding" (`vehicle_fallback_flow_sent` /
`location_fallback_flow_sent`), was assigned from four call sites across three authorities,
and was cleared by completion, by human handoff and by a generic withdrawal loop. A customer
who opened the Vehicle Flow and submitted it with a blank make and model had the attempt
cleared, the identity still unresolved and no human ownership — so the next turn sent the
same form again, without bound.

The invariant is a constraint here:

    UNIQUE (thread_id, cycle_key, family)

Reservation is an INSERT. A conflict is not an error to recover from — it is the answer.
That holds against every caller, including ones not yet written, which is precisely what no
amount of careful checking in the handler could achieve.

TRANSACTION SHAPE
-----------------
The reservation commits in its OWN session, before any Meta call, and survives a rollback of
the surrounding business transaction. This mirrors `OutboundSafetyGate`, whose own comments
state the same requirement ("durable in gate session; caller rollback cannot erase this") —
and for the same reason: a Flow that may already have reached the customer must never be
un-reserved by a later failure.

A savepoint would not do. If the outer transaction rolls back after the Flow was dispatched,
a savepoint-scoped reservation disappears and the customer is asked twice.
"""
from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..models import WhatsAppClarificationAttempt

logger = logging.getLogger(__name__)


# ── typed vocabulary ──────────────────────────────────────────────────────────

#: G3-7L. The reconciler HOLD reasons that a Vehicle clarification Flow may answer.
#:
#: This is a DECISION-level question, not an identity-completeness one. W1/W2 made the
#: reconciler refuse to guess which car a turn is about; the thread's focused candidate
#: can still be a perfectly complete Ford Focus while the turn itself is unresolvable.
#: `_turn_clarification_decision` used to ask only "is the identity resolved?", which
#: canonical completeness answered YES, so every W2 HOLD was silent.
#:
#: Membership is explicit and conservative. A reason is eligible only when showing the
#: customer the vehicle options could actually settle it. Focus-pointer faults, an
#: unreadable store and an unresolvable CATEGORY are deliberately absent: re-asking an
#: identity we already hold is not a question, and those route to a person.
VEHICLE_CLARIFIABLE_HOLDS: frozenset[str] = frozenset({
    "TARGET_SCOPE_UNRESOLVED",          # which car the correction is about is unproven
    "TARGET_SCOPE_CONTRADICTED",        # the stated old value contradicts canonical state
    "EXCLUDED_REFERENT",                # explicitly NOT the car under discussion
    "CONFLICTING_VEHICLE_ATTRIBUTES",   # one car, two stated values for one field
    "UNMARKED_MULTI_IDENTITY",          # two distinct cars, no supersession
    "AMBIGUOUS_MATCH",                  # more than one existing candidate matches
    "INCOMPLETE_IDENTITY",              # a partial reference that places nowhere
    "CREATE_UNCONFIRMED",               # the words do not corroborate the proposed car
    "TARGET_NOT_GROUNDED",              # no admissible ground established the target
})


class ClarificationFamily:
    """Which clarification Flow. Mirrored by a CHECK constraint on the table."""
    VEHICLE = "VEHICLE"
    LOCATION = "LOCATION"

    ALL = (VEHICLE, LOCATION)


class AttemptStatus:
    """The attempt lifecycle. Mirrored by a CHECK constraint on the table."""
    #: Committed, before any transport call. The attempt is spent from this moment.
    RESERVED = "RESERVED"
    DISPATCHED = "DISPATCHED"
    COMPLETED_RESOLVED = "COMPLETED_RESOLVED"
    COMPLETED_UNRESOLVED = "COMPLETED_UNRESOLVED"
    INVALID_COMPLETION = "INVALID_COMPLETION"
    DISPATCH_BLOCKED = "DISPATCH_BLOCKED"
    DISPATCH_FAILED = "DISPATCH_FAILED"
    HANDED_OFF = "HANDED_OFF"

    ALL = (RESERVED, DISPATCHED, COMPLETED_RESOLVED, COMPLETED_UNRESOLVED,
           INVALID_COMPLETION, DISPATCH_BLOCKED, DISPATCH_FAILED, HANDED_OFF)

    #: No further automated clarification for this family and cycle. `RESERVED` is here on
    #: purpose: a row still RESERVED on a LATER turn means the process died between the
    #: reservation and the dispatch, and the honest reading is that we may already have sent
    #: a Flow we cannot account for. Treating it as spent trades a rare lost clarification
    #: for the guarantee that no customer is asked twice.
    TERMINAL_FOR_AUTOMATION = (RESERVED, COMPLETED_UNRESOLVED, INVALID_COMPLETION,
                               DISPATCH_BLOCKED, DISPATCH_FAILED, HANDED_OFF)
    #: The family was genuinely resolved; normal processing resumes.
    RESOLVING = (COMPLETED_RESOLVED,)


#: Legal status transitions. Anything absent is a programming error, not a business case.
ALLOWED_TRANSITIONS: dict = {
    AttemptStatus.RESERVED: (AttemptStatus.DISPATCHED, AttemptStatus.DISPATCH_BLOCKED,
                             AttemptStatus.DISPATCH_FAILED, AttemptStatus.HANDED_OFF),
    AttemptStatus.DISPATCHED: (AttemptStatus.COMPLETED_RESOLVED,
                               AttemptStatus.COMPLETED_UNRESOLVED,
                               AttemptStatus.INVALID_COMPLETION,
                               AttemptStatus.HANDED_OFF),
    AttemptStatus.DISPATCH_BLOCKED: (AttemptStatus.HANDED_OFF,),
    AttemptStatus.DISPATCH_FAILED: (AttemptStatus.HANDED_OFF,),
    AttemptStatus.COMPLETED_UNRESOLVED: (AttemptStatus.HANDED_OFF,),
    AttemptStatus.INVALID_COMPLETION: (AttemptStatus.HANDED_OFF,),
    AttemptStatus.COMPLETED_RESOLVED: (),
    AttemptStatus.HANDED_OFF: (),
}

#: Fixed application namespace for the deterministic key. A constant UUID, never generated
#: at runtime, so the same (thread, cycle, family) always yields the same key — across
#: processes, restarts and deployments.
IDEMPOTENCY_NAMESPACE = uuid.UUID("6f1b8c2e-5a3d-4e7f-9b21-0c4d8e6a7f35")


# ── identity ──────────────────────────────────────────────────────────────────

def cycle_key(state) -> int:
    """`COALESCE(current_cycle_start_message_db_id, 0)` — the canonical cycle identity.

    The integer column is read DIRECTLY, never through `_reconciler_cycle_id()`, which falls
    back to `current_cycle_started_at` and then to `None` — mixing a message id with a
    timestamp into one string, so the same cycle could be keyed two ways.

    `0` is the thread's initial, never-reset cycle, and it is load-bearing rather than
    cosmetic: every live thread state has this column NULL today, and NULL cannot
    participate in a uniqueness constraint. A real message id appears only after
    `_execute_cycle_reset` runs, which happens only under the operator-set
    `cycle_reset_pending` flag. There is no automatic cycle boundary and this function
    invents none.
    """
    raw = getattr(state, "current_cycle_start_message_db_id", None)
    return int(raw) if isinstance(raw, int) else 0


def idempotency_key(thread_id: int, cycle: int, family: str) -> str:
    """Deterministic UUIDv5 over the three identity components. 36 characters.

    Derived from nothing else: no timestamp, no counter, no retry nonce, no customer text,
    no phone number, no WAMID. The same thread, cycle and family always produce the same
    key; a different family or cycle always produces a different one.

    36 characters is deliberate. `whatsapp_messages.correlation_id` and
    `security_events.correlation_id` are both `String(36)`, so this value fits them without
    truncation if it is ever carried there. (The architecture review proposed a 48-character
    truncated digest, which would not have fitted — the widths were checked rather than
    assumed.)
    """
    return str(uuid.uuid5(IDEMPOTENCY_NAMESPACE,
                          f"clarification:{int(thread_id)}:{int(cycle)}:{family}"))


# ── typed store outcomes (SEQ-02) ─────────────────────────────────────────────

class AttemptStoreStatus:
    """What the store actually managed to do. Three outcomes, never two.

    The preserved implementation returned `(attempt, won)`, so "the customer has already
    been asked" and "the store could not answer" collapsed into the same `won=False`. The
    sequencing audit (SEQ-02) measured the consequence: a flush against an unusable session
    raised `FlushError` out of `reserve()`, uncaught, and destroyed the customer's turn.
    Availability and history are different facts and are reported as different values.
    """
    WON = "WON"
    ALREADY_ATTEMPTED = "ALREADY_ATTEMPTED"
    STORE_UNAVAILABLE = "STORE_UNAVAILABLE"

    ALL = (WON, ALREADY_ATTEMPTED, STORE_UNAVAILABLE)


class StoreFault:
    """Why the store was unavailable. A TYPE, never a driver message.

    Driver text can carry a DSN, a host or a user name. Only the exception class name is
    ever logged and only one of these constants is ever returned, so a fault can be traced
    and alerted on without a credential reaching a log, a trace row or the Inspector.
    """
    INTEGRITY_NOT_CONFLICT = "INTEGRITY_NOT_CONFLICT"
    FLUSH = "FLUSH"
    OPERATIONAL = "OPERATIONAL"
    PROGRAMMING = "PROGRAMMING"
    UNEXPECTED = "UNEXPECTED"

    ALL = (INTEGRITY_NOT_CONFLICT, FLUSH, OPERATIONAL, PROGRAMMING, UNEXPECTED)


def _fault_kind(exc: BaseException) -> str:
    """Classify a database failure into a typed, credential-free kind."""
    name = type(exc).__name__
    if name == "FlushError":
        return StoreFault.FLUSH
    if name in ("OperationalError", "InterfaceError", "DBAPIError", "TimeoutError",
                "DisconnectionError", "InvalidRequestError", "ResourceClosedError"):
        return StoreFault.OPERATIONAL
    if name in ("ProgrammingError", "NoSuchTableError", "InternalError"):
        return StoreFault.PROGRAMMING
    return StoreFault.UNEXPECTED


class ReservationResult:
    """The complete answer to "may I send this Flow?"."""

    __slots__ = ("status", "attempt", "fault")

    def __init__(self, status: str, attempt=None, fault: Optional[str] = None) -> None:
        self.status = status
        self.attempt = attempt
        self.fault = fault

    @property
    def won(self) -> bool:
        return self.status == AttemptStoreStatus.WON

    @property
    def already_attempted(self) -> bool:
        return self.status == AttemptStoreStatus.ALREADY_ATTEMPTED

    @property
    def available(self) -> bool:
        """False only when the STORE could not answer — never because history said no."""
        return self.status != AttemptStoreStatus.STORE_UNAVAILABLE

    def __repr__(self) -> str:                                   # pragma: no cover - debug
        return f"ReservationResult({self.status}, fault={self.fault})"


class SpentFamiliesResult:
    """Which families are spent, and whether that answer is trustworthy."""

    __slots__ = ("families", "available", "fault")

    def __init__(self, families: frozenset, available: bool = True,
                 fault: Optional[str] = None) -> None:
        self.families = families
        self.available = available
        self.fault = fault

    def __contains__(self, family) -> bool:
        return family in self.families

    def __iter__(self):
        return iter(self.families)

    def __len__(self) -> int:
        return len(self.families)


# ── the independent session ───────────────────────────────────────────────────

@dataclass(frozen=True)
class ActivationReadiness:
    """Whether this deployment may have clarification turned on at all.

    G3-7L. The runtime already fails closed — `spent_families` reports
    `available=False` and the turn goes to a person — but a deployment should never reach
    that state by accident. This is the explicit gate: the activation step asks before
    arming anything, and a `False` here means the flags must stay off.

    Read-only by construction: it inspects the catalogue, never the data, and never writes.
    """
    ready: bool
    reason: str                    # "" when ready
    table_present: bool
    cycle_uniqueness: bool         # UNIQUE(thread_id, cycle_key, family)
    idempotency_uniqueness: bool   # UNIQUE(idempotency_key)


def clarification_activation_ready(db: Session) -> ActivationReadiness:
    """May clarification be activated against this database?

    Refuses when the attempts table is absent — that is the whole point — and also when
    either uniqueness constraint is missing, because without them "one attempt per family
    per cycle" and duplicate-delivery idempotency are assertions rather than guarantees.
    A table with no constraints would let a retry ask the customer twice.
    """
    from sqlalchemy import inspect as _sa_inspect

    try:
        insp = _sa_inspect(db.get_bind())
        table = "whatsapp_clarification_attempts"
        if table not in set(insp.get_table_names()):
            return ActivationReadiness(False, "ATTEMPTS_TABLE_ABSENT", False, False, False)
        cols = {tuple(sorted(u.get("column_names") or ()))
                for u in (insp.get_unique_constraints(table) or ())}
        cycle_ok = ("cycle_key", "family", "thread_id") in cols
        idem_ok = ("idempotency_key",) in cols
        if not cycle_ok:
            return ActivationReadiness(False, "CYCLE_UNIQUENESS_MISSING", True, False, idem_ok)
        if not idem_ok:
            return ActivationReadiness(False, "IDEMPOTENCY_UNIQUENESS_MISSING", True, True, False)
        return ActivationReadiness(True, "", True, True, True)
    except Exception as exc:                       # noqa: BLE001 - absence, not denial
        return ActivationReadiness(False, f"INSPECTION_FAILED:{type(exc).__name__}",
                                   False, False, False)


class ClarificationAttemptStore:
    """Reservation and lifecycle authority, on its own transaction.

    Session handling follows `OutboundSafetyGate` exactly: a factory bound to the caller's
    engine, a context manager that rolls back on exception and always closes, and the
    caller's own session is never committed, flushed or rolled back from here.
    """

    def __init__(self, db: Session) -> None:
        self._caller_db = db
        engine = getattr(db, "bind", None)
        if engine is not None:
            self._Session = sessionmaker(bind=engine, autoflush=True, autocommit=False)
        else:                                   # rare in this codebase; same fallback as the gate
            from ..db import SessionLocal
            self._Session = SessionLocal

    @contextlib.contextmanager
    def _session(self):
        sess = self._Session()
        try:
            yield sess
        except Exception:
            try:
                sess.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                sess.close()
            except Exception:
                pass

    # ── reservation ───────────────────────────────────────────────────────────

    def reserve(self, *, thread_id: int, cycle: int, family: str) -> "ReservationResult":
        """Try to claim this family's single attempt for this cycle. Never raises on I/O.

        Three outcomes, and they are genuinely different facts:

          * `WON` — this session's INSERT created the row. The attempt is spent from the
            moment it commits, which is before any transport call.
          * `ALREADY_ATTEMPTED` — a uniqueness conflict whose conflicting row was then
            FOUND. That is the complete answer to "may I send this Flow?": no, and here is
            what happened to the one attempt that was made.
          * `STORE_UNAVAILABLE` — the store could not answer. The customer may or may not
            have been asked before; we do not know, and saying either would be a lie.

        The INSERT is the atomic guard. There is no read-then-insert window: two concurrent
        sessions both issue the INSERT and the unique constraint decides.

        Committed before returning, in its own transaction, so a later rollback of the
        caller's business transaction cannot un-spend the attempt.

        SEQ-02. A uniqueness conflict is not the only integrity error an INSERT can raise,
        and an integrity error is not the only failure a flush can produce. A foreign-key
        violation, a CHECK violation, a missing table, a dead connection or a flush against
        an unusable session must never be reported as "already attempted" — that would tell
        the caller the customer had been asked when nothing was written at all — and must
        never escape as an exception that destroys the turn. Both are handled here, by type.
        """
        if family not in ClarificationFamily.ALL:
            raise ValueError(f"unknown clarification family: {family!r}")
        now = datetime.now(timezone.utc)
        key = idempotency_key(thread_id, cycle, family)
        try:
            with self._session() as sess:
                row = WhatsAppClarificationAttempt(
                    thread_id=thread_id, cycle_key=cycle, family=family,
                    idempotency_key=key, status=AttemptStatus.RESERVED,
                    reserved_at=now, updated_at=now)
                sess.add(row)
                try:
                    sess.commit()
                except IntegrityError:
                    # Expected whenever a second turn, a duplicate delivery or a concurrent
                    # session asks for the same reservation. Confirmed, never assumed: the
                    # conflict is only an answer if the conflicting row is actually there.
                    sess.rollback()
                    existing = sess.execute(
                        select(WhatsAppClarificationAttempt).where(
                            WhatsAppClarificationAttempt.thread_id == thread_id,
                            WhatsAppClarificationAttempt.cycle_key == cycle,
                            WhatsAppClarificationAttempt.family == family)
                    ).scalar_one_or_none()
                    if existing is None:
                        logger.error(
                            "CLARIFICATION reservation failed for a reason that is NOT a "
                            "uniqueness conflict thread_id=%s cycle=%s family=%s",
                            thread_id, cycle, family)
                        return ReservationResult(
                            AttemptStoreStatus.STORE_UNAVAILABLE,
                            fault=StoreFault.INTEGRITY_NOT_CONFLICT)
                    logger.info(
                        "CLARIFICATION attempt already spent thread_id=%s cycle=%s "
                        "family=%s status=%s", thread_id, cycle, family, existing.status)
                    return ReservationResult(AttemptStoreStatus.ALREADY_ATTEMPTED,
                                             attempt=self._detach(existing, sess))
                logger.info("CLARIFICATION attempt reserved thread_id=%s cycle=%s "
                            "family=%s id=%s", thread_id, cycle, family, row.id)
                return ReservationResult(AttemptStoreStatus.WON,
                                         attempt=self._detach(row, sess))
        except Exception as exc:                      # noqa: BLE001 - deliberate boundary
            kind = _fault_kind(exc)
            # Class name only. A driver message can carry a DSN, a host or a user name.
            logger.critical(
                "CLARIFICATION attempt store UNAVAILABLE on reserve thread_id=%s cycle=%s "
                "family=%s fault=%s exc=%s",
                thread_id, cycle, family, kind, type(exc).__name__)
            return ReservationResult(AttemptStoreStatus.STORE_UNAVAILABLE, fault=kind)

    def load(self, *, thread_id: int, cycle: int, family: str):
        """The attempt for this thread, cycle and family, or None. Never raises on I/O.

        `None` means "no such attempt" only when the store answered. A caller that must
        distinguish absence from unavailability uses `spent_families`, which reports both.
        """
        try:
            with self._session() as sess:
                found = sess.execute(
                    select(WhatsAppClarificationAttempt).where(
                        WhatsAppClarificationAttempt.thread_id == thread_id,
                        WhatsAppClarificationAttempt.cycle_key == cycle,
                        WhatsAppClarificationAttempt.family == family)
                ).scalar_one_or_none()
                return self._detach(found, sess)
        except Exception as exc:                      # noqa: BLE001 - deliberate boundary
            logger.critical(
                "CLARIFICATION attempt store UNAVAILABLE on load thread_id=%s cycle=%s "
                "family=%s fault=%s exc=%s",
                thread_id, cycle, family, _fault_kind(exc), type(exc).__name__)
            return None

    def attempt_in_other_cycle(self, *, thread_id: int, cycle: int, family: str) -> bool:
        """Is there an attempt for this family in a DIFFERENT cycle of this thread?

        G3-7M. `load()` returning None is two different facts. "We have no record at all"
        may mean we died after dispatching, and the existing policy deliberately lets the
        customer's data through rather than discarding it. "We have a record, but for an
        earlier cycle" means this submission belongs to a cycle that has since closed —
        a stale form, and acting on it would write last cycle's answer onto this cycle's
        vehicle. Read-only; never raises on I/O, and an unreadable store answers True so
        the caller fails closed.
        """
        try:
            with self._session() as session:
                row = session.execute(
                    select(WhatsAppClarificationAttempt.id)
                    .where(WhatsAppClarificationAttempt.thread_id == thread_id,
                           WhatsAppClarificationAttempt.family == family,
                           WhatsAppClarificationAttempt.cycle_key != cycle)
                    .limit(1)).first()
                return row is not None
        except Exception:                      # noqa: BLE001 - absence is not denial
            logger.warning("G3-7M stale-cycle probe unavailable thread_id=%s — failing closed",
                           thread_id)
            return True

    def spent_families(self, *, thread_id: int, cycle: int) -> "SpentFamiliesResult":
        """Which families have already spent their attempt in this cycle.

        Returns availability alongside the answer. An empty set from a store that could not
        be read looks exactly like an empty set from a thread that has never been asked —
        and acting on the second reading when the first is true is how a customer gets asked
        twice. The caller must check `available` before treating emptiness as permission.
        """
        try:
            with self._session() as sess:
                rows = sess.execute(
                    select(WhatsAppClarificationAttempt.family).where(
                        WhatsAppClarificationAttempt.thread_id == thread_id,
                        WhatsAppClarificationAttempt.cycle_key == cycle)
                ).scalars().all()
                return SpentFamiliesResult(frozenset(rows))
        except Exception as exc:                      # noqa: BLE001 - deliberate boundary
            kind = _fault_kind(exc)
            logger.critical(
                "CLARIFICATION attempt store UNAVAILABLE on spent_families thread_id=%s "
                "cycle=%s fault=%s exc=%s",
                thread_id, cycle, kind, type(exc).__name__)
            return SpentFamiliesResult(frozenset(), available=False, fault=kind)

    # ── transitions ───────────────────────────────────────────────────────────

    def transition(self, attempt_id: int, new_status: str, *, claim: bool = False,
                   **fields):
        """Advance one attempt. Illegal transitions raise rather than corrupt the record.

        The row is never deleted and `thread_id`, `cycle_key`, `family` and
        `idempotency_key` are never rewritten — the identity is immutable by construction,
        so the only thing that moves is where the attempt got to.
        """
        if new_status not in AttemptStatus.ALL:
            raise ValueError(f"unknown attempt status: {new_status!r}")
        try:
            with self._session() as sess:
                if claim:
                    # G3-7M §13. A completion must be won, not merely recorded. The row is
                    # locked so two concurrent deliveries serialize here, and a status that
                    # already equals the target means someone else won: return None rather
                    # than the usual no-op, so the caller treats it as the duplicate it is.
                    # Without the lock both callers read a non-terminal status and both
                    # proceed to mutate — convergent, but two writes where one is allowed.
                    row = sess.execute(
                        select(WhatsAppClarificationAttempt)
                        .where(WhatsAppClarificationAttempt.id == attempt_id)
                        .with_for_update()).scalar_one_or_none()
                else:
                    row = sess.get(WhatsAppClarificationAttempt, attempt_id)
                if row is None:
                    return None
                if claim and row.status == new_status:
                    logger.info("G3-7M completion already claimed attempt_id=%s status=%s",
                                attempt_id, row.status)
                    return None
                allowed = ALLOWED_TRANSITIONS.get(row.status, ())
                if new_status != row.status and new_status not in allowed:
                    raise ValueError(
                        f"illegal attempt transition {row.status} -> {new_status}")
                row.status = new_status
                for name in ("outbound_message_id", "wa_message_id", "dispatch_result",
                             "completion_result", "resolved_family", "terminal_reason"):
                    if name in fields:
                        setattr(row, name, fields[name])
                row.updated_at = datetime.now(timezone.utc)
                sess.commit()
                return self._detach(row, sess)
        except ValueError:
            # An illegal transition is a programming error, not a business case, and is
            # NOT swallowed here. Only infrastructure failure is.
            raise
        except Exception as exc:                      # noqa: BLE001 - deliberate boundary
            # SEQ-02. A transition records what already happened to the customer. Losing
            # that record is bad; destroying the turn over it is worse, and the attempt row
            # itself — the thing that bounds re-asking — is already committed.
            logger.critical(
                "CLARIFICATION attempt store UNAVAILABLE on transition attempt_id=%s "
                "new_status=%s fault=%s exc=%s",
                attempt_id, new_status, _fault_kind(exc), type(exc).__name__)
            return None

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _detach(row, sess):
        """A plain snapshot, so callers cannot accidentally mutate a live ORM row.

        The caller holds a different session; handing it an attached object from this one
        would make the attempt row writable from the business transaction, which is exactly
        the coupling this module exists to prevent.
        """
        if row is None:
            return None
        from types import SimpleNamespace
        snap = SimpleNamespace(
            id=row.id, thread_id=row.thread_id, cycle_key=row.cycle_key,
            family=row.family, idempotency_key=row.idempotency_key, status=row.status,
            reserved_at=row.reserved_at, outbound_message_id=row.outbound_message_id,
            wa_message_id=row.wa_message_id, dispatch_result=row.dispatch_result,
            completion_result=row.completion_result,
            resolved_family=row.resolved_family, terminal_reason=row.terminal_reason,
            updated_at=row.updated_at)
        return snap


def is_terminal_for_automation(attempt) -> bool:
    """Has this family finished with automated clarification for its cycle?"""
    if attempt is None:
        return False
    return getattr(attempt, "status", None) in AttemptStatus.TERMINAL_FOR_AUTOMATION
