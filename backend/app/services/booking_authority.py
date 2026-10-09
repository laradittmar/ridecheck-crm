"""SB2A — the canonical booking authority.

One command, `BookingAuthority.execute`, is the intended sole way a booking is created, moved,
cancelled, marked for rescheduling or turned into a non-occupying scheduling request. It owns
the transaction boundary: it validates, locks, checks, writes the Revision (and whatever the
caller attaches — a ThreadRevision, lead or thread state), records the idempotency ledger row
and commits once. Every failure rolls everything back and comes back as a typed
`BookingResult`; callers never parse a database message.

Order inside the transaction (PostgreSQL):

    1. preflight          — the SB1/SB2A schema is really there (a `create_all` + `stamp`
                            database is refused), triggers are not disabled for this session
    2. idempotency (fast)  — a committed command with the same key replays its original result
    3. revision row lock   — `SELECT … FOR UPDATE`, then the expected-version check
    4. professional        — explicit, eligible professional, or the single active launch
                            default; never "the first row", never NULL
    5. advisory locks      — SHARED per-date lock (calendar exceptions take it EXCLUSIVE),
                            then EXCLUSIVE (professional, date). Blocking with a bounded
                            `lock_timeout`; a lock failure is RETRYABLE_FAILURE, never swallowed
    6. idempotency (auth.) — re-checked under the locks
    7. calendar exceptions — closed date / overlapping unavailable interval
    8. overlap pre-check   — a typed conflict before the write (unattributed legacy rows count
                            against the launch resource); the SB1 exclusion constraint stays
                            the final authority and its violation is translated, not leaked
    9. caller revalidation — e.g. the Flow's `ScheduleService.check`, under the same locks
   10. write + attach + ledger, then ONE commit; success is reported only after it

The advisory lock is coordination. The exclusion constraint is the guarantee.

SQLite (tests) has neither advisory locks nor exclusion constraints. There the lock step is an
explicit, labelled `sqlite-serialized` mode — SQLite serializes writers database-wide — and
nothing about SQLite is evidence of concurrency safety. Any other dialect fails closed.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Mapping, Optional

from sqlalchemy import and_, event, or_, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from . import booking_representation as br

logger = logging.getLogger(__name__)

#: Advisory-lock namespace for per-date locks — must match the SB2A migration's trigger.
DATE_LOCK_NAMESPACE = -1
DEFAULT_LOCK_TIMEOUT_MS = 5000


# ── canonical statuses ───────────────────────────────────────────────────────

#: The complete canonical vocabulary. Anything else is refused, so no alias, spelling or
#: padding can slip a booking into or out of occupancy.
OCCUPYING_STATUSES: tuple[str, ...] = ("PENDIENTE", "CONFIRMADO", "EN_PROCESO", "COMPLETADO")
NON_OCCUPYING_STATUSES: tuple[str, ...] = ("CANCELADO", "REPROGRAMAR")
CANONICAL_STATUSES: frozenset[str] = frozenset(OCCUPYING_STATUSES + NON_OCCUPYING_STATUSES)
assert frozenset(NON_OCCUPYING_STATUSES) == br.NON_OCCUPYING_ESTADOS


def normalize_status(raw: Optional[str]) -> Optional[str]:
    """Canonical status or None. Trims and upper-cases; never maps one word onto another."""
    if raw is None:
        return None
    value = str(raw).strip().upper()
    return value if value in CANONICAL_STATUSES else None


def status_occupies(status: str) -> bool:
    return status in OCCUPYING_STATUSES


# ── typed command and result ─────────────────────────────────────────────────

class BookingOperation(str, Enum):
    CREATE = "CREATE"
    RESCHEDULE = "RESCHEDULE"
    CANCEL = "CANCEL"
    MARK_FOR_RESCHEDULE = "MARK_FOR_RESCHEDULE"
    SET_STATUS = "SET_STATUS"
    UNSCHEDULE = "UNSCHEDULE"
    REQUEST_MANUAL_SCHEDULING = "REQUEST_MANUAL_SCHEDULING"


class BookingSource(str, Enum):
    WHATSAPP_FLOW = "WHATSAPP_FLOW"
    CRM = "CRM"
    API = "API"
    CONVERSATION_ENGINE = "CONVERSATION_ENGINE"
    OTHER = "OTHER"


class BookingOutcome(str, Enum):
    COMMITTED = "COMMITTED"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
    SLOT_CONFLICT = "SLOT_CONFLICT"
    CALENDAR_EXCEPTION = "CALENDAR_EXCEPTION"
    MISSING_PROFESSIONAL = "MISSING_PROFESSIONAL"
    STALE_VERSION = "STALE_VERSION"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"


class AckStatus(str, Enum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"


#: SB2A-R2 acknowledgement lease. A SENDING claim older than this may be reclaimed.
ACK_LEASE_SECONDS = 120
#: Bounded retries: after this many claims the acknowledgement stays FAILED for an operator.
ACK_MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class AckClaim:
    """The right to send one acknowledgement attempt. Only its holder may record the outcome.

    `duplicate_risk` is True when this claim reclaimed an expired SENDING lease: the earlier
    attempt may already have reached Meta before its process died, so this send may
    duplicate it. Exactly-once external delivery is NOT claimed.
    """
    idempotency_key: str
    token: str
    attempt: int
    duplicate_risk: bool


#: Operator/customer-safe Spanish messages. Never a database message.
_MESSAGES: dict[BookingOutcome, str] = {
    BookingOutcome.COMMITTED: "Turno registrado.",
    BookingOutcome.IDEMPOTENT_REPLAY: "Turno ya registrado.",
    BookingOutcome.SLOT_CONFLICT: "El horario elegido ya no está disponible. Elegí otro horario.",
    BookingOutcome.CALENDAR_EXCEPTION: "Ese día u horario está bloqueado en la agenda. Elegí otro.",
    BookingOutcome.MISSING_PROFESSIONAL: "No hay un profesional asignable para este turno.",
    BookingOutcome.STALE_VERSION: "El turno cambió mientras lo editabas. Recargá y volvé a intentar.",
    BookingOutcome.INVALID_TRANSITION: "Ese cambio de estado no es válido para este turno.",
    BookingOutcome.VALIDATION_FAILED: "Los datos del turno no son válidos.",
    BookingOutcome.RETRYABLE_FAILURE: "La agenda está ocupada en este momento. Volvé a intentar.",
    BookingOutcome.INTERNAL_FAILURE: "No se pudo registrar el turno.",
}

_OUTCOMES_OK = frozenset({BookingOutcome.COMMITTED, BookingOutcome.IDEMPOTENT_REPLAY})


@dataclass(frozen=True)
class BookingCommand:
    operation: BookingOperation
    source: BookingSource
    #: Exact event identity (a Flow submission, a form version, an API key). Never prose and
    #: never a timestamp. None means "not idempotent" — every call is a fresh attempt.
    idempotency_key: Optional[str] = None
    lead_id: Optional[int] = None
    revision_id: Optional[int] = None
    thread_id: Optional[int] = None
    profesional_id: Optional[int] = None
    requested_date: Optional[date] = None
    requested_time: Optional[time] = None
    service_minutes: Optional[int] = None
    buffer_minutes: Optional[int] = None
    target_status: Optional[str] = None
    expected_version: Optional[int] = None
    correlation_id: Optional[str] = None
    actor: Optional[str] = None
    #: Non-booking Revision fields for CREATE / REQUEST_MANUAL_SCHEDULING (vehicle, zone…).
    revision_fields: Mapping[str, Any] = field(default_factory=dict)
    #: Whether this source sends an acknowledgement the ledger must track.
    tracks_acknowledgement: bool = False


@dataclass(frozen=True)
class ConflictInfo:
    """What the slot collided with — times only, never a customer's data."""
    start: datetime
    end: datetime
    kind: str          # "booking" | "closed_date" | "unavailable_interval"


@dataclass(frozen=True)
class BookingResult:
    outcome: BookingOutcome
    reason: str
    message: str
    revision_id: Optional[int] = None
    thread_revision_id: Optional[int] = None
    profesional_id: Optional[int] = None
    slot_start: Optional[datetime] = None
    slot_end: Optional[datetime] = None
    occupying: Optional[bool] = None
    booking_version: Optional[int] = None
    idempotency_key: Optional[str] = None
    lock_mode: Optional[str] = None
    retryable: bool = False
    conflicts: tuple[ConflictInfo, ...] = ()
    ack_status: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.outcome in _OUTCOMES_OK


def _result(outcome: BookingOutcome, reason: str, **kw) -> BookingResult:
    return BookingResult(outcome=outcome, reason=reason, message=_MESSAGES[outcome],
                         retryable=outcome is BookingOutcome.RETRYABLE_FAILURE, **kw)


class _Refused(Exception):
    def __init__(self, result: BookingResult):
        super().__init__(result.reason)
        self.result = result


def refuse(outcome: BookingOutcome, reason: str, **kw) -> BookingResult:
    """For revalidation callbacks: build a typed refusal."""
    return _result(outcome, reason, **kw)


# ── fingerprints and keys ────────────────────────────────────────────────────

def request_fingerprint(cmd: BookingCommand) -> str:
    """Hash of the semantic request. Correlation, actor and key are not part of it."""
    body = {
        "op": cmd.operation.value, "src": cmd.source.value, "lead": cmd.lead_id,
        "rev": cmd.revision_id, "thread": cmd.thread_id, "prof": cmd.profesional_id,
        "date": cmd.requested_date.isoformat() if cmd.requested_date else None,
        "time": cmd.requested_time.strftime("%H:%M:%S") if cmd.requested_time else None,
        "svc": cmd.service_minutes, "buf": cmd.buffer_minutes,
        "status": normalize_status(cmd.target_status) or cmd.target_status,
        "ver": cmd.expected_version,
        "fields": {k: (v.isoformat() if isinstance(v, (date, time, datetime)) else v)
                   for k, v in sorted(dict(cmd.revision_fields).items())},
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def flow_idempotency_key(booking_token: str) -> str:
    """One Flow offer token → at most one booking. The raw token is never stored."""
    return "flow:" + hashlib.sha256(booking_token.encode()).hexdigest()[:48]


def form_idempotency_key(source: BookingSource, revision_id: int, expected_version: Optional[int],
                         fingerprint: str) -> str:
    """A form edit is one event per (revision, version it was rendered at, requested change)."""
    return f"{source.value.lower()}:rev{revision_id}:v{expected_version}:{fingerprint[:24]}"


# ── schema preflight (every transaction; never cached) ──────────────────────

#: Critical database objects. Checked in ONE catalog round trip at the start of every booking
#: transaction (SB2A-R2): a trigger disabled after an earlier successful booking must stop the
#: very next booking in the same process (SB2A-R-07). `tgenabled IN ('O','A')` — 'D' is
#: disabled and 'R' fires only in replica mode, i.e. never for an application session.
_CRITICAL_TRIGGERS = ("trg_revisions_sb1_attribute_professional",
                      "trg_revisions_sb2a_booking_version",
                      "trg_calendar_exceptions_sb2a_lock")
_CRITICAL_COLUMNS = (("revisions", "occupied_during"), ("revisions", "agenda_occupying"),
                     ("revisions", "booking_version"), ("revisions", "service_minutes"),
                     ("revisions", "buffer_minutes"), ("profesionales", "is_active"),
                     ("profesionales", "is_launch_default"), ("booking_commands", "idempotency_key"),
                     ("booking_commands", "ack_claim_token"),
                     ("booking_commands", "ack_lease_expires_at"))
_PREFLIGHT_SQL = f"""
SELECT
  (SELECT count(*) FROM pg_constraint
     WHERE conname = 'ex_revisions_professional_occupied_overlap' AND contype = 'x'
       AND convalidated),
  (SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal
     AND tgname IN ({", ".join("'" + t + "'" for t in _CRITICAL_TRIGGERS)})),
  (SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgenabled IN ('O', 'A')
     AND tgname IN ({", ".join("'" + t + "'" for t in _CRITICAL_TRIGGERS)})),
  (SELECT count(*) FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
     WHERE NOT a.attisdropped AND a.attnum > 0
       AND (c.relname, a.attname) IN ({", ".join(f"('{t}', '{c}')" for t, c in _CRITICAL_COLUMNS)})),
  current_setting('session_replication_role')
"""


def verify_booking_schema(db: Session) -> Optional[str]:
    """None when this session may book; otherwise the reason it may not.

    Runs every time — no process-wide cache. A database built by `create_all` + `alembic stamp
    head` (no SB1/SB2A objects), a disabled critical trigger, or a session whose replication
    role disables triggers must not accept a booking.
    """
    if db.get_bind().dialect.name != "postgresql":
        return None
    constraint, present, enabled, columns, role = db.execute(text(_PREFLIGHT_SQL)).one()
    if constraint != 1 or present != len(_CRITICAL_TRIGGERS) or columns != len(_CRITICAL_COLUMNS):
        return "BOOKING_SCHEMA_INCOMPLETE"
    if role != "origin":
        return "TRIGGERS_DISABLED_FOR_SESSION"
    if enabled != len(_CRITICAL_TRIGGERS):
        return "CRITICAL_TRIGGER_DISABLED"
    return None


# ── callback guard ───────────────────────────────────────────────────────────

class CallbackViolation(RuntimeError):
    """A revalidate/attach callback tried something only the authority may do."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


@dataclass
class _CallbackScope:
    phase: str
    violations: list = field(default_factory=list)
    #: every ORM object the callback inserted, modified or deleted (captured at each flush
    #: and once more after it returns) — reads never land here
    written: list = field(default_factory=list)

    def violate(self, kind: str) -> None:
        self.violations.append(kind)
        raise CallbackViolation(f"{self.phase}:{kind}")


_CALLBACK_SCOPE: contextvars.ContextVar[Optional[_CallbackScope]] = contextvars.ContextVar(
    "booking_callback_scope", default=None)


def booking_callback_active() -> Optional[str]:
    scope = _CALLBACK_SCOPE.get()
    return scope.phase if scope is not None else None


def forbid_external_io(channel: str) -> None:
    """Called by external transports (the outbound gate). Inside a booking callback this
    records a violation and raises: nothing may leave the process before the commit."""
    scope = _CALLBACK_SCOPE.get()
    if scope is not None:
        scope.violate(f"external_io:{channel}")


#: Session methods a callback may not call — the transaction belongs to the authority.
_FORBIDDEN_SESSION_METHODS = ("commit", "rollback", "begin", "begin_nested", "close",
                              "invalidate", "reset", "expunge_all")
#: Transaction- and trigger-control SQL a callback may not issue through the session.
_FORBIDDEN_SQL_HEADS = ("COMMIT", "ROLLBACK", "BEGIN", "START TRANSACTION", "END", "ABORT",
                        "SAVEPOINT", "RELEASE", "PREPARE TRANSACTION", "SET TRANSACTION",
                        "SET SESSION_REPLICATION_ROLE", "SET SESSION AUTHORIZATION",
                        "RESET", "ALTER ", "DROP ", "CREATE ", "TRUNCATE")


# ── the authority ────────────────────────────────────────────────────────────

AttachFn = Callable[[Session, Any], Optional[Any]]
RevalidateFn = Callable[[Session, Any], Optional[BookingResult]]


class BookingAuthority:
    def __init__(self, db: Session, *, lock_timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS):
        self.db = db
        self.lock_timeout_ms = int(lock_timeout_ms)

    # ── read-only lookups ────────────────────────────────────────────────

    def lookup(self, idempotency_key: str) -> Optional[BookingResult]:
        """The committed result for a key, or None. Read-only; does not count as a replay."""
        from ..models import BookingCommandRecord
        rec = self.db.execute(select(BookingCommandRecord).where(
            BookingCommandRecord.idempotency_key == idempotency_key)).scalar_one_or_none()
        return None if rec is None else self._replay_result(rec)

    def _replay_result(self, rec) -> BookingResult:
        return _result(BookingOutcome.IDEMPOTENT_REPLAY, "ALREADY_COMMITTED",
                       revision_id=rec.revision_id, thread_revision_id=rec.thread_revision_id,
                       profesional_id=rec.profesional_id, slot_start=rec.slot_start,
                       slot_end=rec.slot_end, occupying=rec.occupying,
                       booking_version=rec.booking_version, idempotency_key=rec.idempotency_key,
                       ack_status=rec.ack_status)

    # ── acknowledgement (separate from commit) ───────────────────────────

    def claim_acknowledgement(self, idempotency_key: str, *, now: Optional[datetime] = None,
                              lease_seconds: int = ACK_LEASE_SECONDS) -> Optional[AckClaim]:
        """Atomically take a bounded lease to send the acknowledgement, or None.

        Claimable: PENDING; FAILED (retry); SENDING whose lease has EXPIRED (its claimant
        died). Never: SENT; an active SENDING lease; attempts exhausted. One UPDATE with the
        whole condition — under concurrency PostgreSQL re-evaluates it after the first
        claimant commits, so exactly one reclaimer wins.
        """
        from ..models import BookingCommandRecord as R
        now = now or datetime.now(timezone.utc)
        token = str(uuid.uuid4())
        res = self.db.execute(
            update(R)
            .where(R.idempotency_key == idempotency_key)
            .where(R.ack_attempts < ACK_MAX_ATTEMPTS)
            .where(or_(R.ack_status.in_((AckStatus.PENDING.value, AckStatus.FAILED.value)),
                       and_(R.ack_status == AckStatus.SENDING.value,
                            R.ack_lease_expires_at < now)))
            # every SET expression reads the OLD row: reclaiming SENDING ⇒ duplicate risk
            .values(ack_duplicate_risk=or_(R.ack_duplicate_risk,
                                           R.ack_status == AckStatus.SENDING.value),
                    ack_status=AckStatus.SENDING.value,
                    ack_attempts=R.ack_attempts + 1,
                    ack_claim_token=token, ack_claimed_at=now,
                    ack_lease_expires_at=now + timedelta(seconds=int(lease_seconds)),
                    ack_updated_at=now)
            .execution_options(synchronize_session=False))
        self.db.commit()
        if res.rowcount != 1:
            return None
        rec = self.db.execute(select(R.ack_attempts, R.ack_duplicate_risk).where(
            R.idempotency_key == idempotency_key)).one()
        return AckClaim(idempotency_key=idempotency_key, token=token, attempt=int(rec[0]),
                        duplicate_risk=bool(rec[1]))

    def record_acknowledgement(self, claim: AckClaim, *, sent: bool,
                               transport_id: Optional[str] = None,
                               error: Optional[str] = None) -> bool:
        """Record the outcome of THIS claim. A claimant whose lease was reclaimed by someone
        else no longer owns the row and records nothing (returns False)."""
        from ..models import BookingCommandRecord as R
        values = {"ack_status": (AckStatus.SENT if sent else AckStatus.FAILED).value,
                  "ack_updated_at": datetime.now(timezone.utc)}
        if transport_id:
            values["ack_transport_id"] = str(transport_id)[:128]
        if error:
            values["ack_last_error"] = str(error)[:64]
        res = self.db.execute(
            update(R)
            .where(R.idempotency_key == claim.idempotency_key)
            .where(R.ack_claim_token == claim.token)
            .where(R.ack_status == AckStatus.SENDING.value)
            .values(**values)
            .execution_options(synchronize_session=False))
        self.db.commit()
        return res.rowcount == 1

    def note_replay(self, idempotency_key: str) -> None:
        from ..models import BookingCommandRecord
        self.db.execute(
            update(BookingCommandRecord)
            .where(BookingCommandRecord.idempotency_key == idempotency_key)
            .values(replay_count=BookingCommandRecord.replay_count + 1,
                    last_replayed_at=datetime.now(timezone.utc)))
        self.db.commit()

    # ── the command ──────────────────────────────────────────────────────

    def execute(self, cmd: BookingCommand, *, revalidate: Optional[RevalidateFn] = None,
                attach: Optional[AttachFn] = None) -> BookingResult:
        fingerprint = request_fingerprint(cmd)
        lock_mode: Optional[str] = None
        try:
            spec = self._validate(cmd)
            if self._postgres():
                reason = verify_booking_schema(self.db)        # every transaction
                if reason:
                    raise _Refused(_result(BookingOutcome.INTERNAL_FAILURE, reason))
                self.db.execute(text(f"SET LOCAL lock_timeout = {self.lock_timeout_ms}"))

            if cmd.idempotency_key:
                replay = self._check_replay(cmd.idempotency_key, fingerprint)
                if replay is not None:
                    self.db.rollback()
                    self.note_replay(cmd.idempotency_key)
                    return replay

            revision = self._load_revision(cmd)
            current = _Snapshot.of(revision)
            if cmd.expected_version is not None and revision is not None \
                    and current.version != cmd.expected_version:
                raise _Refused(_result(BookingOutcome.STALE_VERSION, "VERSION_MISMATCH",
                                       revision_id=revision.id,
                                       booking_version=current.version))
            target = self._target(cmd, spec, current)
            self._check_transition(cmd, current, target, revision)

            profesional_id = None
            if target.occupying:
                profesional_id = self._resolve_professional(cmd, current)
                lock_mode = self._acquire_locks(profesional_id, target)
                if cmd.idempotency_key:
                    replay = self._check_replay(cmd.idempotency_key, fingerprint)
                    if replay is not None:
                        self.db.rollback()
                        self.note_replay(cmd.idempotency_key)
                        return replay
                self._check_calendar(profesional_id, target)
                self._check_overlap(profesional_id, target,
                                    exclude_id=revision.id if revision is not None else None)
            elif cmd.operation is not BookingOperation.UNSCHEDULE:
                profesional_id = current.profesional_id if revision is not None else None

            if revalidate is not None and target.occupying:
                refusal, wrote = self._run_callback("revalidate", revalidate, target)
                if wrote:
                    raise CallbackViolation("revalidate:wrote")       # revalidation is read-only
                if refusal is not None:
                    raise _Refused(replace(refusal, lock_mode=lock_mode))

            revision = self._write(cmd, revision, target, profesional_id, current)
            expected = self._booking_row(revision.id)
            thread_revision = None
            if attach is not None:
                thread_revision, wrote = self._run_callback("attach", attach, revision)
                self._verify_after_attach(revision, expected, target, wrote)
            thread_revision_id = getattr(thread_revision, "id", None)
            linked_id = self._sync_thread_revision(cmd, revision, target, thread_revision_id)
            self.db.flush()
            version = revision.booking_version
            if cmd.idempotency_key:
                self._record(cmd, fingerprint, revision, thread_revision_id or linked_id,
                             target, version)
            self.db.commit()
            return _result(BookingOutcome.COMMITTED, "COMMITTED", revision_id=revision.id,
                           thread_revision_id=thread_revision_id,
                           profesional_id=revision.profesional_id,
                           slot_start=target.start, slot_end=target.end,
                           occupying=target.occupying, booking_version=version,
                           idempotency_key=cmd.idempotency_key, lock_mode=lock_mode,
                           ack_status=(AckStatus.PENDING if cmd.tracks_acknowledgement
                                       else AckStatus.NOT_APPLICABLE).value)
        except CallbackViolation as violation:
            self._safe_rollback()
            logger.error("BOOKING_AUTHORITY callback violation %s op=%s source=%s",
                         violation.kind, cmd.operation.value, cmd.source.value)
            return _result(BookingOutcome.INTERNAL_FAILURE, f"CALLBACK_VIOLATION:{violation.kind}",
                           idempotency_key=cmd.idempotency_key, lock_mode=lock_mode)
        except _Refused as refused:
            self._safe_rollback()
            return replace(refused.result, lock_mode=refused.result.lock_mode or lock_mode,
                           idempotency_key=cmd.idempotency_key)
        except IntegrityError as exc:
            self._safe_rollback()
            return self._translate_integrity(exc, cmd, fingerprint)
        except DBAPIError as exc:
            self._safe_rollback()
            state = getattr(getattr(exc, "orig", None), "sqlstate", None) or ""
            if state in ("55P03", "40P01", "40001") or state.startswith("08") \
                    or exc.connection_invalidated:
                return _result(BookingOutcome.RETRYABLE_FAILURE,
                               "LOCK_TIMEOUT" if state == "55P03" else f"DB_{state or 'DISCONNECT'}",
                               idempotency_key=cmd.idempotency_key, lock_mode=lock_mode)
            logger.error("BOOKING_AUTHORITY database error sqlstate=%s", state)
            return _result(BookingOutcome.INTERNAL_FAILURE, f"DB_{state or 'ERROR'}",
                           idempotency_key=cmd.idempotency_key)
        except Exception:
            self._safe_rollback()
            logger.exception("BOOKING_AUTHORITY internal failure op=%s source=%s",
                             cmd.operation.value, cmd.source.value)
            return _result(BookingOutcome.INTERNAL_FAILURE, "INTERNAL",
                           idempotency_key=cmd.idempotency_key)

    # ── steps ────────────────────────────────────────────────────────────

    def _postgres(self) -> bool:
        return self.db.get_bind().dialect.name == "postgresql"

    def _safe_rollback(self) -> None:
        try:
            self.db.rollback()
        except Exception:                       # the original failure is what we report
            logger.exception("BOOKING_AUTHORITY rollback failed")

    def _run_callback(self, phase: str, fn, arg):
        """Run a caller callback with the transaction locked against it.

        Enforced, not documented: commit/rollback/begin on the session raise; transaction- or
        trigger-control SQL through the session raises; external transport (the outbound
        gate) raises. Violations are recorded even if the callback swallows the exception,
        and everything it writes is captured. All hooks are removed in `finally`, including
        when the callback raises. Returns (callback result, objects it wrote).
        """
        db = self.db
        scope = _CallbackScope(phase=phase)

        def _forbid(name):
            def _raise(*_a, **_k):
                scope.violate(f"session.{name}")
            return _raise

        def _capture(session, _ctx, _instances):
            scope.written.extend(list(session.new) + list(session.dirty) + list(session.deleted))

        def _sql_hook(_conn, _cursor, statement, _params, _ctx, _many):
            head = " ".join(str(statement).split()).upper()
            if head.startswith(_FORBIDDEN_SQL_HEADS):
                scope.violate("sql:" + head.split(" ")[0])

        def _conn_txn(*_a, **_k):
            scope.violate("connection.transaction_control")

        def _second_connection(*_a, **_k):
            # a side channel: another connection could commit outside this transaction.
            # Engine-wide listener — only THIS context's callback is accused (contextvar).
            if _CALLBACK_SCOPE.get() is scope:
                scope.violate("engine.new_connection")

        db.flush()                    # pending work before the callback is not the callback's
        conn = db.connection()
        engine = conn.engine
        conn_events = ("commit", "rollback", "savepoint", "begin", "release_savepoint",
                       "rollback_savepoint", "begin_twophase")
        for name in _FORBIDDEN_SESSION_METHODS:
            setattr(db, name, _forbid(name))
        event.listen(db, "before_flush", _capture)
        event.listen(conn, "before_cursor_execute", _sql_hook)
        for ev in conn_events:
            event.listen(conn, ev, _conn_txn)
        event.listen(engine, "engine_connect", _second_connection)
        token = _CALLBACK_SCOPE.set(scope)
        try:
            try:
                result = fn(db, arg)
                _capture(db, None, None)
            except Exception as exc:
                if scope.violations:          # a swallowed violation still decides the outcome
                    raise CallbackViolation(f"{phase}:{scope.violations[0]}") from exc
                raise
        finally:
            _CALLBACK_SCOPE.reset(token)
            event.remove(engine, "engine_connect", _second_connection)
            for ev in conn_events:
                event.remove(conn, ev, _conn_txn)
            event.remove(conn, "before_cursor_execute", _sql_hook)
            event.remove(db, "before_flush", _capture)
            for name in _FORBIDDEN_SESSION_METHODS:
                db.__dict__.pop(name, None)
        if scope.violations:
            raise CallbackViolation(f"{phase}:{scope.violations[0]}")
        return result, scope.written

    def _booking_row(self, revision_id: int) -> tuple:
        return tuple(self.db.execute(text(
            "SELECT turno_fecha, turno_hora, service_minutes, buffer_minutes, estado_revision, "
            "profesional_id, lead_id FROM revisions WHERE id = :i"), {"i": revision_id}).one())

    def _verify_after_attach(self, revision, expected: tuple, target: "_Target",
                             written: list) -> None:
        """After `attach`: the authority's revision is untouched (so its status is still the
        canonical one the authority wrote), and the callback wrote no other Revision and no
        booked ThreadRevision anywhere but the booked slot."""
        from ..models import Revision, ThreadRevision
        if revision not in self.db or revision.id is None:
            raise CallbackViolation("attach:revision_detached")
        self.db.flush()
        if self._booking_row(revision.id) != expected:
            raise CallbackViolation("attach:booking_fields_changed")
        for obj in written:
            if obj is revision:
                continue
            if isinstance(obj, Revision):
                raise CallbackViolation("attach:wrote_another_revision")
            if isinstance(obj, ThreadRevision) and obj.status in ("booked", "completed") \
                    and obj.scheduled_date is not None and obj.scheduled_time is not None \
                    and (obj.scheduled_date, obj.scheduled_time) != (target.day, target.at):
                raise CallbackViolation("attach:thread_revision_elsewhere")
        if self._postgres():
            # raw-SQL writes bypass the ORM: ask the database what THIS transaction touched
            mine = "xmin::text::bigint = txid_current() % 4294967296"
            if self.db.execute(text(f"SELECT count(*) FROM revisions WHERE {mine} AND id <> :i"),
                               {"i": revision.id}).scalar():
                raise CallbackViolation("attach:wrote_another_revision")
            if self.db.execute(text(
                    f"SELECT count(*) FROM thread_revisions WHERE {mine} "
                    "AND status IN ('booked', 'completed') AND scheduled_date IS NOT NULL "
                    "AND scheduled_time IS NOT NULL AND (scheduled_date, scheduled_time) "
                    "IS DISTINCT FROM (CAST(:d AS date), CAST(:t AS time))"),
                    {"d": target.day, "t": target.at}).scalar():
                raise CallbackViolation("attach:thread_revision_elsewhere")

    def _validate(self, cmd: BookingCommand) -> "_Spec":
        op = cmd.operation
        if op in (BookingOperation.CREATE, BookingOperation.REQUEST_MANUAL_SCHEDULING):
            if cmd.revision_id is None and cmd.lead_id is None:
                raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, "LEAD_OR_REVISION_REQUIRED"))
        elif cmd.revision_id is None:
            raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, "REVISION_REQUIRED"))
        # A manual-scheduling request may carry no agreed slot at all; it never occupies.
        if op in (BookingOperation.CREATE, BookingOperation.RESCHEDULE) \
                and (cmd.requested_date is None or cmd.requested_time is None):
            raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, "SLOT_REQUIRED"))
        # A human-escalation placeholder stores only what is known (SB2A-R-01); everything
        # that can occupy still needs a complete slot.
        if op is not BookingOperation.REQUEST_MANUAL_SCHEDULING \
                and (cmd.requested_date is None) != (cmd.requested_time is None):
            raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, "PARTIAL_SLOT"))
        status = None
        if cmd.target_status is not None:
            status = normalize_status(cmd.target_status)
            if status is None:
                raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, "UNKNOWN_STATUS"))
        for name, value in (("service", cmd.service_minutes), ("buffer", cmd.buffer_minutes)):
            if value is not None and (not isinstance(value, int) or value < (1 if name == "service" else 0)):
                raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, f"INVALID_{name.upper()}"))
        if cmd.idempotency_key is not None and not (0 < len(cmd.idempotency_key) <= 128):
            raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, "INVALID_IDEMPOTENCY_KEY"))
        return _Spec(status=status)

    def _check_replay(self, key: str, fingerprint: str) -> Optional[BookingResult]:
        from ..models import BookingCommandRecord
        rec = self.db.execute(select(BookingCommandRecord).where(
            BookingCommandRecord.idempotency_key == key)).scalar_one_or_none()
        if rec is None:
            return None
        if rec.request_fingerprint != fingerprint:
            raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, "IDEMPOTENCY_KEY_REUSED"))
        return self._replay_result(rec)

    def _load_revision(self, cmd: BookingCommand):
        from ..models import Revision
        if cmd.revision_id is None:
            return None
        stmt = select(Revision).where(Revision.id == cmd.revision_id)
        if self._postgres():
            stmt = stmt.with_for_update()
        revision = self.db.execute(stmt).scalar_one_or_none()
        if revision is None:
            raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, "REVISION_NOT_FOUND"))
        if cmd.lead_id is not None and revision.lead_id != cmd.lead_id:
            raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, "REVISION_LEAD_MISMATCH"))
        return revision

    def _target(self, cmd: BookingCommand, spec: "_Spec", current: "_Snapshot") -> "_Target":
        op = cmd.operation
        if op is BookingOperation.CANCEL:
            status = "CANCELADO"
        elif op in (BookingOperation.MARK_FOR_RESCHEDULE, BookingOperation.REQUEST_MANUAL_SCHEDULING):
            status = "REPROGRAMAR"
        else:
            status = spec.status or current.status or "PENDIENTE"
        if op is BookingOperation.UNSCHEDULE:
            day, at = None, None
        elif op is BookingOperation.REQUEST_MANUAL_SCHEDULING:
            day, at = cmd.requested_date, cmd.requested_time   # never invent the missing half
        elif cmd.requested_date is not None:
            day, at = cmd.requested_date, cmd.requested_time
        else:
            day, at = current.day, current.at
        service = cmd.service_minutes if cmd.service_minutes is not None else \
            (current.service if current.exists else br.SERVICE_MINUTES_DEFAULT)
        buffer = cmd.buffer_minutes if cmd.buffer_minutes is not None else \
            (current.buffer if current.exists else br.BUFFER_MINUTES_DEFAULT)
        interval = br.occupied_interval(day, at, service, buffer)
        occupying = interval is not None and status_occupies(status)
        return _Target(status=status, day=day, at=at, service=service, buffer=buffer,
                       start=interval.start if interval else None,
                       end=interval.end if interval else None, occupying=occupying)

    def _check_transition(self, cmd, current: "_Snapshot", target: "_Target", revision) -> None:
        op = cmd.operation
        bad = None
        if op is BookingOperation.CREATE:
            if not target.occupying:
                bad = "CREATE_MUST_OCCUPY"
            elif revision is not None and current.occupying:
                bad = "ALREADY_BOOKED_USE_RESCHEDULE"
        elif op is BookingOperation.RESCHEDULE:
            if not current.occupying:
                bad = "NOT_BOOKED_USE_CREATE"
            elif not target.occupying:
                bad = "RESCHEDULE_MUST_OCCUPY"
        elif op is BookingOperation.SET_STATUS:
            if not (current.occupying and target.occupying):
                bad = "SET_STATUS_BETWEEN_OCCUPYING_ONLY"
            elif (target.day, target.at) != (current.day, current.at):
                bad = "SET_STATUS_CANNOT_MOVE"
        elif op is BookingOperation.REQUEST_MANUAL_SCHEDULING and revision is not None:
            bad = "MANUAL_SCHEDULING_CREATES_A_NEW_RECORD"
        if bad:
            raise _Refused(_result(BookingOutcome.INVALID_TRANSITION, bad,
                                   revision_id=getattr(revision, "id", None)))

    def _resolve_professional(self, cmd: BookingCommand, current: "_Snapshot") -> int:
        from ..models import Profesional
        wanted = cmd.profesional_id
        if wanted is None and current.exists and current.profesional_id is not None:
            wanted = current.profesional_id
        if wanted is not None:
            prof = self.db.get(Profesional, wanted)
            if prof is None:
                raise _Refused(_result(BookingOutcome.MISSING_PROFESSIONAL, "PROFESSIONAL_NOT_FOUND"))
            if not prof.is_active:
                raise _Refused(_result(BookingOutcome.MISSING_PROFESSIONAL, "PROFESSIONAL_INACTIVE",
                                       profesional_id=prof.id))
            return prof.id
        defaults = self.db.execute(select(Profesional).where(
            Profesional.is_launch_default.is_(True))).scalars().all()
        if not defaults:
            raise _Refused(_result(BookingOutcome.MISSING_PROFESSIONAL, "NO_LAUNCH_DEFAULT"))
        if len(defaults) > 1:
            raise _Refused(_result(BookingOutcome.MISSING_PROFESSIONAL, "AMBIGUOUS_LAUNCH_DEFAULT"))
        if not defaults[0].is_active:
            raise _Refused(_result(BookingOutcome.MISSING_PROFESSIONAL, "LAUNCH_DEFAULT_INACTIVE",
                                   profesional_id=defaults[0].id))
        return defaults[0].id

    def _acquire_locks(self, profesional_id: int, target: "_Target") -> str:
        dialect = self.db.get_bind().dialect.name
        if dialect == "sqlite":
            return "sqlite-serialized"         # labelled; never evidence of concurrency safety
        if dialect != "postgresql":
            raise _Refused(_result(BookingOutcome.INTERNAL_FAILURE, "NO_LOCK_PRIMITIVE"))
        days = sorted({target.start.date(), (target.end - timedelta(microseconds=1)).date()})
        for d in days:
            self.db.execute(text("SELECT pg_advisory_xact_lock_shared(:ns, :d)"),
                            {"ns": DATE_LOCK_NAMESPACE, "d": _ymd(d)})
        for d in days:
            self.db.execute(text("SELECT pg_advisory_xact_lock(:p, :d)"),
                            {"p": int(profesional_id), "d": _ymd(d)})
        return "pg-advisory-xact"

    def _check_calendar(self, profesional_id: int, target: "_Target") -> None:
        from ..models import CalendarException
        days = {target.start.date(), (target.end - timedelta(microseconds=1)).date()}
        rows = self.db.execute(select(CalendarException).where(
            CalendarException.exception_date.in_(sorted(days)))).scalars().all()
        for ex in rows:
            if ex.profesional_id is not None and ex.profesional_id != profesional_id:
                continue
            if ex.kind == "CLOSED_DATE":
                day_start = datetime.combine(ex.exception_date, time(0, 0))
                raise _Refused(_result(BookingOutcome.CALENDAR_EXCEPTION, "CLOSED_DATE",
                                       conflicts=(ConflictInfo(day_start, day_start + timedelta(days=1),
                                                               "closed_date"),)))
            if ex.kind == "UNAVAILABLE_INTERVAL" and ex.start_time and ex.end_time:
                s = datetime.combine(ex.exception_date, ex.start_time)
                e = datetime.combine(ex.exception_date, ex.end_time)
                if target.start < e and s < target.end:
                    raise _Refused(_result(BookingOutcome.CALENDAR_EXCEPTION, "UNAVAILABLE_INTERVAL",
                                           conflicts=(ConflictInfo(s, e, "unavailable_interval"),)))

    def _check_overlap(self, profesional_id: int, target: "_Target", exclude_id: Optional[int]) -> None:
        """Typed conflict before the write. Unattributed legacy rows count against the launch
        resource — under the single-inspector policy (OD-01) they are its appointments."""
        from ..models import Profesional, Revision
        is_default = bool(self.db.execute(select(Profesional.is_launch_default).where(
            Profesional.id == profesional_id)).scalar())
        owner = Revision.profesional_id == profesional_id
        if is_default:
            owner = owner | Revision.profesional_id.is_(None)
        days = [target.start.date() - timedelta(days=1), target.start.date(), target.end.date()]
        stmt = select(Revision).where(owner).where(Revision.turno_fecha.in_(days)) \
            .where(Revision.turno_hora.is_not(None))
        if exclude_id is not None:
            stmt = stmt.where(Revision.id != exclude_id)
        conflicts = []
        for other in self.db.execute(stmt).scalars().all():
            if not br.is_occupying(other.turno_fecha, other.turno_hora, other.estado_revision):
                continue
            iv = br.revision_interval(other)
            if iv.start < target.end and target.start < iv.end:
                conflicts.append(ConflictInfo(iv.start, iv.end, "booking"))
        if conflicts:
            raise _Refused(_result(BookingOutcome.SLOT_CONFLICT, "OVERLAP",
                                   profesional_id=profesional_id, conflicts=tuple(conflicts)))

    def _write(self, cmd: BookingCommand, revision, target: "_Target",
               profesional_id: Optional[int], current: "_Snapshot"):
        from ..models import Revision
        if revision is None:
            revision = Revision(lead_id=cmd.lead_id)
            for key, value in dict(cmd.revision_fields).items():
                if key in _BOOKING_FIELDS or not hasattr(Revision, key):
                    raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, f"FIELD_NOT_ALLOWED:{key}"))
                setattr(revision, key, value)
            revision.booking_version = 0
            self.db.add(revision)
        else:
            revision.booking_version = (current.version or 0) + 1
        revision.turno_fecha = target.day
        revision.turno_hora = target.at
        revision.service_minutes = target.service
        revision.buffer_minutes = target.buffer
        revision.estado_revision = target.status
        if profesional_id is not None:
            revision.profesional_id = profesional_id
        self.db.flush()
        return revision

    def _sync_thread_revision(self, cmd, revision, target: "_Target",
                              attached_id: Optional[int]) -> Optional[int]:
        """Keep the linked conversation-side record in the same transaction.

        Only pairs the ledger links are touched — there is no FK between Revision and
        ThreadRevision, so an unlinked legacy pair cannot be paired safely and is left alone.
        """
        if cmd.operation in (BookingOperation.CREATE, BookingOperation.REQUEST_MANUAL_SCHEDULING) \
                or revision.id is None:
            return None
        from ..models import BookingCommandRecord, ThreadRevision
        linked = self.db.execute(
            select(BookingCommandRecord.thread_revision_id)
            .where(BookingCommandRecord.revision_id == revision.id)
            .where(BookingCommandRecord.thread_revision_id.is_not(None))
            .order_by(BookingCommandRecord.id.desc()).limit(1)).scalar()
        if linked is None or linked == attached_id:
            return linked
        tr = self.db.get(ThreadRevision, linked)
        if tr is None or tr.status not in ("booked", "completed", "cancelled", "reschedule_pending"):
            return linked
        tr.scheduled_date = target.day
        tr.scheduled_time = target.at
        if target.occupying:
            tr.status = "completed" if target.status == "COMPLETADO" else "booked"
        elif target.status == "CANCELADO":
            tr.status = "cancelled"
        else:
            tr.status = "reschedule_pending"
        return linked

    def _record(self, cmd, fingerprint, revision, linked, target, version) -> None:
        from ..models import BookingCommandRecord
        self.db.add(BookingCommandRecord(
            idempotency_key=cmd.idempotency_key, source=cmd.source.value,
            operation=cmd.operation.value, request_fingerprint=fingerprint,
            revision_id=revision.id, thread_revision_id=linked, lead_id=revision.lead_id,
            thread_id=cmd.thread_id, profesional_id=revision.profesional_id,
            slot_start=target.start, slot_end=target.end, occupying=target.occupying,
            booking_version=version, correlation_id=cmd.correlation_id, actor=cmd.actor,
            ack_status=(AckStatus.PENDING if cmd.tracks_acknowledgement
                        else AckStatus.NOT_APPLICABLE).value))
        self.db.flush()

    def _translate_integrity(self, exc: IntegrityError, cmd: BookingCommand,
                             fingerprint: str) -> BookingResult:
        orig = getattr(exc, "orig", None)
        state = getattr(orig, "sqlstate", None)
        constraint = getattr(getattr(orig, "diag", None), "constraint_name", None) or ""
        text_ = str(orig or exc)
        if state == "23P01" or "ex_revisions_professional_occupied_overlap" in constraint:
            return _result(BookingOutcome.SLOT_CONFLICT, "EXCLUSION_CONSTRAINT",
                           idempotency_key=cmd.idempotency_key)
        if "uq_booking_commands_idempotency_key" in (constraint or text_):
            # A concurrent identical submission committed first: return its result.
            try:
                replay = self._check_replay(cmd.idempotency_key, fingerprint)
            except _Refused as refused:
                return refused.result
            if replay is not None:
                self.db.rollback()
                return replay
        if state == "23514" and "sb1_occupying_revision_requires_professional" in (constraint or text_):
            return _result(BookingOutcome.MISSING_PROFESSIONAL, "DATABASE_ATTRIBUTION_REFUSED",
                           idempotency_key=cmd.idempotency_key)
        logger.error("BOOKING_AUTHORITY integrity error sqlstate=%s constraint=%s", state, constraint)
        return _result(BookingOutcome.INTERNAL_FAILURE, f"INTEGRITY_{state or 'ERROR'}",
                       idempotency_key=cmd.idempotency_key)


_BOOKING_FIELDS = frozenset({"turno_fecha", "turno_hora", "service_minutes", "buffer_minutes",
                             "estado_revision", "profesional_id", "booking_version", "lead_id",
                             "id", "turno_timezone"})


def _ymd(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


@dataclass(frozen=True)
class _Spec:
    status: Optional[str]


@dataclass(frozen=True)
class _Target:
    status: str
    day: Optional[date]
    at: Optional[time]
    service: int
    buffer: int
    start: Optional[datetime]
    end: Optional[datetime]
    occupying: bool


@dataclass(frozen=True)
class _Snapshot:
    exists: bool
    status: Optional[str]
    day: Optional[date]
    at: Optional[time]
    service: Optional[int]
    buffer: Optional[int]
    profesional_id: Optional[int]
    version: Optional[int]
    occupying: bool

    @classmethod
    def of(cls, revision) -> "_Snapshot":
        if revision is None:
            return cls(False, None, None, None, None, None, None, None, False)
        status = normalize_status(revision.estado_revision) or revision.estado_revision
        return cls(True, status, revision.turno_fecha, revision.turno_hora,
                   revision.service_minutes, revision.buffer_minutes, revision.profesional_id,
                   revision.booking_version or 0,
                   br.is_occupying(revision.turno_fecha, revision.turno_hora,
                                   revision.estado_revision))


# ── calendar exception writer (takes the exclusive per-date lock via its trigger) ──

def create_calendar_exception(db: Session, *, kind: str, exception_date: date,
                              start_time: Optional[time] = None, end_time: Optional[time] = None,
                              profesional_id: Optional[int] = None, reason: Optional[str] = None,
                              created_by: Optional[str] = None):
    """Record an operator exception. Never mutates existing bookings — they stay as they are
    and are surfaced by availability, not silently cancelled."""
    from ..models import CalendarException
    row = CalendarException(kind=kind, exception_date=exception_date, start_time=start_time,
                            end_time=end_time, profesional_id=profesional_id, reason=reason,
                            created_by=created_by)
    db.add(row)
    db.commit()
    return row


# ── edit → command (shared by the CRM and the API) ───────────────────────────

def command_for_edit(revision, *, source: BookingSource, new_date: Optional[date],
                     new_time: Optional[time], new_status: Optional[str],
                     new_profesional_id: Optional[int], expected_version: Optional[int],
                     actor: Optional[str] = None,
                     idempotency_key: Optional[str] = None) -> Optional[BookingCommand]:
    """The booking command an edit of `revision` amounts to, or None if the edit does not
    touch the booking (slot, status or professional).

    `new_status` None means "unchanged". `new_profesional_id` None means "not explicitly
    changed" — an occupying booking then keeps its eligible professional or receives the launch
    default; it can never be left without one.

    Idempotency: a form edit is one event per (revision, version it was rendered at, requested
    change). With no expected version there is no stable event identity, so the edit is not
    idempotent rather than wrongly deduplicated against an older, identical request.
    """
    cur_status = normalize_status(revision.estado_revision) or revision.estado_revision
    tgt_status_raw = new_status if new_status is not None else cur_status
    tgt_status = normalize_status(tgt_status_raw)
    slot_changed = (new_date, new_time) != (revision.turno_fecha, revision.turno_hora)
    prof_changed = new_profesional_id is not None and new_profesional_id != revision.profesional_id
    status_changed = tgt_status != cur_status
    if not (slot_changed or prof_changed or status_changed):
        return None

    cur_occ = br.is_occupying(revision.turno_fecha, revision.turno_hora, revision.estado_revision)
    tgt_occ = tgt_status is not None and new_date is not None and new_time is not None \
        and status_occupies(tgt_status)
    if tgt_status is None:
        op = BookingOperation.SET_STATUS             # the authority refuses the unknown status
    elif not tgt_occ:
        if tgt_status == "CANCELADO":
            op = BookingOperation.CANCEL
        elif tgt_status == "REPROGRAMAR":
            op = BookingOperation.MARK_FOR_RESCHEDULE
        else:
            op = BookingOperation.UNSCHEDULE
    elif not cur_occ:
        op = BookingOperation.CREATE
    elif slot_changed or prof_changed:
        op = BookingOperation.RESCHEDULE
    else:
        op = BookingOperation.SET_STATUS

    # A partial slot (date without time, or the reverse) is passed through as given so the
    # authority refuses it (PARTIAL_SLOT) instead of it being silently dropped.
    cmd = BookingCommand(
        operation=op, source=source, revision_id=revision.id, lead_id=revision.lead_id,
        profesional_id=new_profesional_id,
        requested_date=new_date, requested_time=new_time,
        target_status=tgt_status_raw, expected_version=expected_version, actor=actor)
    if idempotency_key is None and expected_version is not None:
        idempotency_key = form_idempotency_key(source, revision.id, expected_version,
                                               request_fingerprint(cmd))
    return replace(cmd, idempotency_key=idempotency_key)
