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
    9. revalidation        — the authority's own `ScheduleService.check` of a typed
                            `AvailabilityRevalidation`, under the same locks
   10. write + typed attachments + ledger, defence-in-depth checks, then ONE commit;
                            success is reported only after it

SB2A-R3: callers never hand the authority code. There is no `attach`/`revalidate` callable:
associated writes are a typed `BookingAttachmentPlan` of data that the authority itself
executes, so no caller code ever runs inside the booking transaction. The pre-commit checks
(transaction continuity, a second preflight, occupancy and booked-ThreadRevision sets, the
booking row) are defence in depth, not the sandbox.

The advisory lock is coordination. The exclusion constraint is the guarantee.

SQLite (tests) has neither advisory locks nor exclusion constraints. There the lock step is an
explicit, labelled `sqlite-serialized` mode — SQLite serializes writers database-wide — and
nothing about SQLite is evidence of concurrency safety. Any other dialect fails closed.
"""
from __future__ import annotations

import contextvars
import dataclasses
import hashlib
import json
import logging
import secrets
import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Optional

from sqlalchemy import and_, case, func, literal_column, or_, select, text, update
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
#: Bounded retries. A FAILED acknowledgement with this many attempts is TERMINAL: never claimed
#: again, `ack_last_error` starts with `ACK_EXHAUSTED`, and it is listed for operators
#: (`acknowledgements_needing_attention`). The booking itself is unaffected.
ACK_MAX_ATTEMPTS = 5
ACK_EXHAUSTED_PREFIX = "ACK_EXHAUSTED"
#: An SB2A-era SENDING row has no lease at all: its sender's fate is unknown.
ACK_LEGACY_NO_LEASE = "LEGACY_SENDING_NO_LEASE"
#: Transport outcome categories that leave the delivery unknown (SB2A-R3, R2A-06).
ACK_AMBIGUOUS_PREFIX = "AMBIGUOUS"


def _app_now() -> datetime:
    """The application clock — used for the lease ONLY on SQLite (tests). PostgreSQL leases
    use the database clock, so a skewed application host cannot steal an active lease."""
    return datetime.now(timezone.utc)


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
    """Build a typed refusal (for callers that refuse before reaching the authority)."""
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
#: Every column the authority, its typed attachments and the acknowledgement lifecycle read or
#: write (SB2A-R3). `booking_commands` is taken whole from the ORM model; the rest are listed.
_REQUIRED_COLUMNS_LISTED = {
    "revisions": ("id", "lead_id", "turno_fecha", "turno_hora", "turno_timezone", "estado_revision",
                  "profesional_id", "service_minutes", "buffer_minutes", "booking_version",
                  "occupied_during", "agenda_occupying", "precio_total", "tipo_vehiculo",
                  "zone_group", "zone_detail"),
    "profesionales": ("id", "is_active", "is_launch_default", "resource_key"),
    "calendar_exceptions": ("exception_date", "kind", "start_time", "end_time", "profesional_id"),
    "thread_revisions": ("id", "thread_id", "candidate_id", "status", "scheduled_date",
                         "scheduled_time", "appointment_approval_status",
                         "appointment_approval_token"),
    "whatsapp_thread_states": ("thread_id", "current_revision_id", "last_stage", "needs_human",
                               "flow_booking_token"),
    "whatsapp_threads": ("id", "lead_id"),
    "leads": ("id", "estado", "flag", "necesita_humano", "nombre", "apellido", "email", "canal"),
}


def _required_columns() -> tuple[tuple[str, str], ...]:
    from ..models import BookingCommandRecord
    cols = {(t, c) for t, names in _REQUIRED_COLUMNS_LISTED.items() for c in names}
    cols |= {("booking_commands", c.name) for c in BookingCommandRecord.__table__.columns}
    return tuple(sorted(cols))


def _preflight_sql(columns) -> str:
    trig = ", ".join("'" + t + "'" for t in _CRITICAL_TRIGGERS)
    pairs = ", ".join(f"('{t}', '{c}')" for t, c in columns)
    return f"""
SELECT
  (SELECT count(*) FROM pg_constraint
     WHERE conname = 'ex_revisions_professional_occupied_overlap' AND contype = 'x'
       AND convalidated),
  (SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname IN ({trig})),
  (SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgenabled IN ('O', 'A')
     AND tgname IN ({trig})),
  (SELECT count(*) FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
     JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = current_schema()
     WHERE NOT a.attisdropped AND a.attnum > 0 AND (c.relname, a.attname) IN ({pairs})),
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
    required = _required_columns()
    constraint, present, enabled, columns, role = db.execute(text(_preflight_sql(required))).one()
    if constraint != 1 or present != len(_CRITICAL_TRIGGERS) or columns != len(required):
        return "BOOKING_SCHEMA_INCOMPLETE"
    if role != "origin":
        return "TRIGGERS_DISABLED_FOR_SESSION"
    if enabled != len(_CRITICAL_TRIGGERS):
        return "CRITICAL_TRIGGER_DISABLED"
    return None


# ── booking transaction scope (no external I/O before the commit) ───────────

class _TxnScope:
    __slots__ = ("operation",)

    def __init__(self, operation: str):
        self.operation = operation


_TXN_SCOPE: contextvars.ContextVar[Optional[_TxnScope]] = contextvars.ContextVar(
    "booking_transaction_scope", default=None)


class ExternalIOInsideBookingTransaction(RuntimeError):
    """Something tried to reach an external transport before the booking committed."""


def booking_transaction_active() -> Optional[str]:
    scope = _TXN_SCOPE.get()
    return scope.operation if scope is not None else None


def forbid_external_io(channel: str) -> None:
    """Called by external transports (the outbound gate). While a booking transaction is open
    in this context nothing may leave the process — the booking may still roll back."""
    if _TXN_SCOPE.get() is not None:
        raise ExternalIOInsideBookingTransaction(channel)


# ── typed plans (SB2A-R3): callers describe operations as DATA ──────────────

#: The only value types a plan may carry. No callables, sessions, ORM objects or mappings.
_PLAIN_TYPES = (str, int, float, bool, Decimal, date, time, datetime, type(None))


@dataclass(frozen=True)
class AvailabilityRevalidation:
    """Revalidate the requested slot against business availability (`ScheduleService.check`)
    inside the booking decision boundary. The AUTHORITY runs the check on its own read of the
    database; the caller only supplies these inputs."""
    address: str = "-"
    zone_group: Optional[str] = None
    is_holiday: bool = False


#: Statuses a non-occupying attachment may carry (never one `schedule.py` counts).
THREAD_REVISION_NON_OCCUPYING = frozenset({"provisional", "collecting_data", "draft"})


@dataclass(frozen=True)
class ThreadRevisionAttachment:
    """Create the conversation-side ThreadRevision linked to this booking. Its status,
    `scheduled_date` and `scheduled_time` are DERIVED by the authority from the booking —
    never supplied — so it cannot diverge from the Revision or occupy anything else."""
    thread_id: int
    candidate_id: Optional[int] = None
    buyer_name: Optional[str] = None
    buyer_phone: Optional[str] = None
    buyer_email: Optional[str] = None
    seller_type: Optional[str] = None
    seller_name: Optional[str] = None
    address: Optional[str] = None
    tipo_vehiculo: Optional[str] = None
    marca: Optional[str] = None
    modelo: Optional[str] = None
    anio: Optional[int] = None
    publication_url: Optional[str] = None
    zone_group: Optional[str] = None
    #: True → approval PENDING with an authority-generated token.
    request_approval: bool = False
    #: Status used when the booking does not occupy (manual scheduling).
    non_occupying_status: str = "provisional"


@dataclass(frozen=True)
class LeadUpdate:
    """Lead fields the booking contract may set. Always the command's own lead."""
    estado: Optional[str] = None
    flag: Optional[str] = None
    necesita_humano: Optional[bool] = None
    nombre_if_empty: Optional[str] = None
    apellido_if_empty: Optional[str] = None
    email_if_empty: Optional[str] = None
    canal_if_empty: Optional[str] = None


@dataclass(frozen=True)
class ThreadStateUpdate:
    """Thread-state fields the booking contract may set, for the attachment's thread."""
    link_current_revision: bool = False      # current_revision_id ← the attached ThreadRevision
    last_stage: Optional[str] = None
    needs_human: Optional[bool] = None
    consume_flow_token: bool = False         # flow_booking_token ← NULL


@dataclass(frozen=True)
class PriceRecalculation:
    """Recalculate the booking's price from its own inputs. With `require_inputs`
    (tipo_vehiculo, zone_group, zone_detail) the price is computed only if the Revision's
    inputs equal them — otherwise it is left unset and logged, never guessed."""
    require_inputs: Optional[tuple] = None


@dataclass(frozen=True)
class BookingAttachmentPlan:
    thread_revision: Optional[ThreadRevisionAttachment] = None
    lead: Optional[LeadUpdate] = None
    thread_state: Optional[ThreadStateUpdate] = None
    price: Optional[PriceRecalculation] = None


_PLAN_TYPES = (AvailabilityRevalidation, ThreadRevisionAttachment, LeadUpdate, ThreadStateUpdate,
               PriceRecalculation, BookingAttachmentPlan)


#: The only nested plans, each in exactly its declared slot.
_NESTED_PLANS = {"thread_revision": ThreadRevisionAttachment, "lead": LeadUpdate,
                 "thread_state": ThreadStateUpdate, "price": PriceRecalculation}


def _plan_is_data(obj, expected: type) -> bool:
    """True iff `obj` is exactly `expected` and contains only plain data / nested plans of
    exactly their declared type."""
    if type(obj) is not expected:
        return False
    for f in dataclasses.fields(obj):
        value = getattr(obj, f.name)
        if expected is BookingAttachmentPlan:
            if value is not None and not _plan_is_data(value, _NESTED_PLANS[f.name]):
                return False
            continue
        if isinstance(value, _PLAN_TYPES):
            return False                       # plans nest only inside BookingAttachmentPlan
        elif type(value) is tuple:
            if not all(type(v) in _PLAIN_TYPES for v in value):
                return False
        elif type(value) not in _PLAIN_TYPES:          # exact built-ins: no subclasses, no Enum
            return False
    return True


class _InvariantBreach(Exception):
    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


# ── the authority ────────────────────────────────────────────────────────────

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

    def _clock(self):
        """The lease clock: the DATABASE clock on PostgreSQL (`clock_timestamp()`), so lease
        decisions never depend on an application host's clock; the app clock on SQLite."""
        return func.clock_timestamp() if self._postgres() else _app_now()

    def _clock_plus(self, seconds: int):
        if self._postgres():
            return literal_column(f"clock_timestamp() + interval '{int(seconds)} seconds'")
        return _app_now() + timedelta(seconds=int(seconds))

    def _terminalize_exhausted(self, key: Optional[str] = None) -> int:
        """SENDING rows whose lease is gone (expired, or SB2A-era with none) and whose attempts
        are exhausted can never be claimed again: make that explicit and terminal instead of
        leaving them indistinguishable from an in-flight send (R2A-05). The dead attempt may
        have reached Meta, so duplicate risk is recorded."""
        from ..models import BookingCommandRecord as R
        now = self._clock()
        stmt = (update(R)
                .where(R.ack_status == AckStatus.SENDING.value)
                .where(R.ack_attempts >= ACK_MAX_ATTEMPTS)
                .where(or_(R.ack_lease_expires_at.is_(None), R.ack_lease_expires_at < now))
                .values(ack_status=AckStatus.FAILED.value, ack_duplicate_risk=True,
                        ack_last_error=f"{ACK_EXHAUSTED_PREFIX}_AFTER_CRASH",
                        ack_lease_expires_at=None, ack_updated_at=now)
                .execution_options(synchronize_session=False))
        if key is not None:
            stmt = stmt.where(R.idempotency_key == key)
        res = self.db.execute(stmt)
        if res.rowcount:
            logger.error("BOOKING_ACK_EXHAUSTED rows=%s key_prefix=%s — terminal, needs an operator",
                         res.rowcount, (key or "*")[:12])
        return res.rowcount

    def claim_acknowledgement(self, idempotency_key: str, *,
                              lease_seconds: int = ACK_LEASE_SECONDS) -> Optional[AckClaim]:
        """Atomically take a bounded lease to send the acknowledgement, or None.

        Claimable: PENDING; FAILED below the attempt bound (retry); SENDING whose lease has
        EXPIRED by the DATABASE clock (its claimant died), or an SB2A-era SENDING row with no
        lease at all (one bounded recovery, recorded as LEGACY_SENDING_NO_LEASE). Never: SENT;
        an active lease; exhausted attempts. Reclaiming any SENDING row records duplicate
        risk. One UPDATE with the whole condition — PostgreSQL re-evaluates it after a
        concurrent claimant commits, so exactly one wins.
        """
        from ..models import BookingCommandRecord as R
        now = self._clock()
        token = str(uuid.uuid4())
        self._terminalize_exhausted(idempotency_key)
        no_lease = and_(R.ack_status == AckStatus.SENDING.value, R.ack_lease_expires_at.is_(None))
        res = self.db.execute(
            update(R)
            .where(R.idempotency_key == idempotency_key)
            .where(R.ack_attempts < ACK_MAX_ATTEMPTS)
            .where(or_(R.ack_status.in_((AckStatus.PENDING.value, AckStatus.FAILED.value)),
                       and_(R.ack_status == AckStatus.SENDING.value,
                            or_(R.ack_lease_expires_at.is_(None), R.ack_lease_expires_at < now))))
            # every SET expression reads the OLD row: reclaiming SENDING ⇒ duplicate risk
            .values(ack_duplicate_risk=or_(R.ack_duplicate_risk,
                                           R.ack_status == AckStatus.SENDING.value),
                    ack_last_error=case((no_lease, ACK_LEGACY_NO_LEASE), else_=R.ack_last_error),
                    ack_status=AckStatus.SENDING.value,
                    ack_attempts=R.ack_attempts + 1,
                    ack_claim_token=token, ack_claimed_at=now,
                    ack_lease_expires_at=self._clock_plus(lease_seconds),
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
                               error: Optional[str] = None, ambiguous: bool = False) -> bool:
        """Record the outcome of THIS claim. A claimant whose lease was reclaimed by someone
        else no longer owns the row and records nothing (returns False).

        * SENT requires a non-empty transport identity (WAMID). A "success" without one is
          recorded as AMBIGUOUS — FAILED with duplicate risk — never as SENT.
        * An ambiguous outcome (e.g. a timeout after the request left, a 2xx without a usable
          id) is FAILED with duplicate risk and an `AMBIGUOUS_*` category: retried only within
          the attempt bound, never claimed as delivered or as not delivered.
        * A FAILED outcome on the last allowed attempt is terminal (`ACK_EXHAUSTED_*`).
        """
        from ..models import BookingCommandRecord as R
        wamid = (str(transport_id).strip() if transport_id is not None else "")[:128]
        if sent and not wamid:
            sent, ambiguous, error = False, True, f"{ACK_AMBIGUOUS_PREFIX}_SENT_WITHOUT_TRANSPORT_ID"
        if ambiguous and not (error or "").startswith(ACK_AMBIGUOUS_PREFIX):
            error = f"{ACK_AMBIGUOUS_PREFIX}_{error or 'UNKNOWN'}"
        now = self._clock()
        values: dict = {"ack_status": (AckStatus.SENT if sent else AckStatus.FAILED).value,
                        "ack_updated_at": now, "ack_lease_expires_at": None}
        if sent:
            values["ack_transport_id"] = wamid
        else:
            category = (error or "NOT_SENT")[:48]
            values["ack_last_error"] = case(
                (R.ack_attempts >= ACK_MAX_ATTEMPTS, f"{ACK_EXHAUSTED_PREFIX}:{category}"),
                else_=category)
            if ambiguous:
                values["ack_duplicate_risk"] = True
        res = self.db.execute(
            update(R)
            .where(R.idempotency_key == claim.idempotency_key)
            .where(R.ack_claim_token == claim.token)
            .where(R.ack_status == AckStatus.SENDING.value)
            .values(**values)
            .execution_options(synchronize_session=False))
        self.db.commit()
        return res.rowcount == 1

    def acknowledgements_needing_attention(self) -> list[dict]:
        """Operator view: terminal (exhausted) acknowledgements, rows with duplicate risk, and
        SENDING rows whose lease is gone. Exhausted crashed SENDING rows are first made
        terminal, so nothing stays invisibly stuck."""
        from ..models import BookingCommandRecord as R
        self._terminalize_exhausted(None)
        self.db.commit()
        now = self._clock()
        rows = self.db.execute(
            select(R.idempotency_key, R.ack_status, R.ack_attempts, R.ack_duplicate_risk,
                   R.ack_last_error, R.ack_transport_id, R.revision_id, R.ack_updated_at)
            .where(R.ack_status != AckStatus.NOT_APPLICABLE.value)
            .where(or_(and_(R.ack_status == AckStatus.FAILED.value,
                            R.ack_attempts >= ACK_MAX_ATTEMPTS),
                       R.ack_duplicate_risk.is_(True),
                       and_(R.ack_status == AckStatus.SENDING.value,
                            or_(R.ack_lease_expires_at.is_(None),
                                R.ack_lease_expires_at < now))))
            .order_by(R.id)).all()
        return [dict(r._mapping) for r in rows]

    def note_replay(self, idempotency_key: str) -> None:
        from ..models import BookingCommandRecord
        self.db.execute(
            update(BookingCommandRecord)
            .where(BookingCommandRecord.idempotency_key == idempotency_key)
            .values(replay_count=BookingCommandRecord.replay_count + 1,
                    last_replayed_at=datetime.now(timezone.utc)))
        self.db.commit()

    # ── the command ──────────────────────────────────────────────────────

    def execute(self, cmd: BookingCommand, *,
                availability: Optional[AvailabilityRevalidation] = None,
                attachments: Optional[BookingAttachmentPlan] = None) -> BookingResult:
        """Run one booking command in ONE authority-owned transaction.

        `availability` and `attachments` are typed DATA (SB2A-R3). No caller code runs inside
        the transaction; the authority performs every read and write itself.
        """
        lock_mode: Optional[str] = None
        fingerprint = ""
        token = _TXN_SCOPE.set(_TxnScope(str(getattr(cmd, "operation", "?"))))
        try:
            spec = self._validate(cmd)
            fingerprint = request_fingerprint(cmd)
            self._validate_plans(cmd, availability, attachments)
            txid = None
            if self._postgres():
                reason = verify_booking_schema(self.db)        # every transaction
                if reason:
                    raise _Refused(_result(BookingOutcome.INTERNAL_FAILURE, reason))
                self.db.execute(text(f"SET LOCAL lock_timeout = {self.lock_timeout_ms}"))
                txid = self.db.execute(text("SELECT txid_current()")).scalar()

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

            if availability is not None and target.occupying:
                self._revalidate_availability(availability, target, lock_mode)

            window = self._window_days(target, current)
            before = self._window_state(window, lock=True)
            revision = self._write(cmd, revision, target, profesional_id, current)
            expected_row = self._booking_row(revision.id)
            thread_revision_id = self._apply_attachments(cmd, attachments, revision, target) \
                if attachments is not None else None
            self._assert_continuous(txid)          # before any further write (e.g. the ledger)
            linked_id = self._sync_thread_revision(cmd, revision, target, thread_revision_id)
            self.db.flush()
            version = revision.booking_version
            if cmd.idempotency_key:
                self._record(cmd, fingerprint, revision, thread_revision_id or linked_id,
                             target, version)
            self._verify_before_commit(txid, revision, expected_row, target, window, before,
                                       allowed_tr={i for i in (thread_revision_id, linked_id) if i})
            self.db.commit()
            return _result(BookingOutcome.COMMITTED, "COMMITTED", revision_id=revision.id,
                           thread_revision_id=thread_revision_id,
                           profesional_id=revision.profesional_id,
                           slot_start=target.start, slot_end=target.end,
                           occupying=target.occupying, booking_version=version,
                           idempotency_key=cmd.idempotency_key, lock_mode=lock_mode,
                           ack_status=(AckStatus.PENDING if cmd.tracks_acknowledgement
                                       else AckStatus.NOT_APPLICABLE).value)
        except _InvariantBreach as breach:
            self._safe_rollback()
            logger.error("BOOKING_AUTHORITY invariant breach %s op=%s source=%s",
                         breach.kind, cmd.operation.value, cmd.source.value)
            return _result(BookingOutcome.INTERNAL_FAILURE, f"INVARIANT_BREACH:{breach.kind}",
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
        finally:
            _TXN_SCOPE.reset(token)

    # ── steps ────────────────────────────────────────────────────────────

    def _postgres(self) -> bool:
        return self.db.get_bind().dialect.name == "postgresql"

    def _safe_rollback(self) -> None:
        try:
            self.db.rollback()
        except Exception:                       # the original failure is what we report
            logger.exception("BOOKING_AUTHORITY rollback failed")

    def _booking_row(self, revision_id: int) -> tuple:
        return tuple(self.db.execute(text(
            "SELECT turno_fecha, turno_hora, service_minutes, buffer_minutes, estado_revision, "
            "profesional_id, lead_id FROM revisions WHERE id = :i"), {"i": revision_id}).one())

    # ── typed plans ──────────────────────────────────────────────────────

    def _validate_plans(self, cmd, availability, attachments) -> None:
        if availability is not None and not _plan_is_data(availability, AvailabilityRevalidation):
            raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, "PLAN_NOT_DATA:availability"))
        if attachments is None:
            return
        if not _plan_is_data(attachments, BookingAttachmentPlan):
            raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, "PLAN_NOT_DATA:attachments"))
        tr = attachments.thread_revision
        if tr is not None:
            if cmd.operation not in (BookingOperation.CREATE,
                                     BookingOperation.REQUEST_MANUAL_SCHEDULING):
                raise _Refused(_result(BookingOutcome.VALIDATION_FAILED,
                                       "ATTACHMENT_ONLY_FOR_NEW_BOOKINGS"))
            if tr.non_occupying_status not in THREAD_REVISION_NON_OCCUPYING:
                raise _Refused(_result(BookingOutcome.VALIDATION_FAILED,
                                       "ATTACHMENT_STATUS_NOT_ALLOWED"))
            if cmd.thread_id is not None and tr.thread_id != cmd.thread_id:
                raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, "ATTACHMENT_THREAD_MISMATCH"))
        if attachments.thread_state is not None and tr is None and cmd.thread_id is None:
            raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, "THREAD_STATE_WITHOUT_THREAD"))
        if attachments.thread_state is not None and attachments.thread_state.link_current_revision \
                and tr is None:
            raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, "LINK_WITHOUT_THREAD_REVISION"))

    def _revalidate_availability(self, plan: AvailabilityRevalidation, target: "_Target",
                                 lock_mode: Optional[str]) -> None:
        """Authority-owned availability check, under the booking locks. Read-only."""
        from ..schemas.schedule import ScheduleCheckIn
        out = self._availability_service().check(ScheduleCheckIn(
            address=plan.address or "-", preferred_day=target.day, preferred_time=target.at,
            zone_group=plan.zone_group, is_holiday=plan.is_holiday))
        if not out.valid:
            logger.info("BOOKING_REVALIDATION_FAIL day=%s time=%s reasons=%s",
                        target.day, target.at, getattr(out, "reasons", None))
            raise _Refused(_result(BookingOutcome.SLOT_CONFLICT, "SCHEDULE_REVALIDATION",
                                   lock_mode=lock_mode))

    def _availability_service(self):
        """The authority's own availability reader on its own session. Not a caller input;
        tests replace this method on an instance to control availability."""
        from .schedule import ScheduleService
        return ScheduleService(self.db)

    def _apply_attachments(self, cmd: BookingCommand, plan: BookingAttachmentPlan, revision,
                           target: "_Target") -> Optional[int]:
        """Perform the typed associated writes. Every row touched is chosen by the authority:
        the booking's own lead, the attachment's thread (verified to belong to that lead), and
        a new ThreadRevision whose status and slot are derived from the booking."""
        from ..models import Lead, ThreadRevision, WhatsAppThread, WhatsAppThreadCandidate, \
            WhatsAppThreadState
        tr_id = None
        thread_id = plan.thread_revision.thread_id if plan.thread_revision else cmd.thread_id
        if plan.thread_revision is not None or plan.thread_state is not None:
            thread = self.db.get(WhatsAppThread, thread_id)
            if thread is None:
                raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, "ATTACHMENT_THREAD_NOT_FOUND"))
            if thread.lead_id is not None and thread.lead_id != revision.lead_id:
                raise _Refused(_result(BookingOutcome.VALIDATION_FAILED,
                                       "ATTACHMENT_THREAD_OF_ANOTHER_LEAD"))
        a = plan.thread_revision
        if a is not None:
            if a.candidate_id is not None:
                cand = self.db.get(WhatsAppThreadCandidate, a.candidate_id)
                if cand is None or cand.thread_id != a.thread_id:
                    raise _Refused(_result(BookingOutcome.VALIDATION_FAILED,
                                           "ATTACHMENT_CANDIDATE_NOT_IN_THREAD"))
            if target.occupying:
                status = "completed" if target.status == "COMPLETADO" else "booked"
            else:
                status = a.non_occupying_status
            tr = ThreadRevision(
                thread_id=a.thread_id, candidate_id=a.candidate_id, status=status,
                buyer_name=a.buyer_name, buyer_phone=a.buyer_phone, buyer_email=a.buyer_email,
                seller_type=a.seller_type, seller_name=a.seller_name, address=a.address,
                scheduled_date=target.day, scheduled_time=target.at,
                tipo_vehiculo=a.tipo_vehiculo, marca=a.marca, modelo=a.modelo, anio=a.anio,
                publication_url=a.publication_url, zone_group=a.zone_group)
            if a.request_approval:
                tr.appointment_approval_status = "PENDING"
                tr.appointment_approval_token = secrets.token_urlsafe(32)
            self.db.add(tr)
            self.db.flush()
            tr_id = tr.id
        if plan.price is not None:
            self._recalculate_price(plan.price, revision)
        if plan.lead is not None:
            lead = self.db.get(Lead, revision.lead_id)
            if lead is None:
                raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, "LEAD_NOT_FOUND"))
            u = plan.lead
            for name in ("estado", "flag", "necesita_humano"):
                if getattr(u, name) is not None:
                    setattr(lead, name, getattr(u, name))
            for name in ("nombre", "apellido", "email", "canal"):
                value = getattr(u, f"{name}_if_empty")
                if value is not None and not getattr(lead, name):
                    setattr(lead, name, value)
        if plan.thread_state is not None:
            st = self.db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == thread_id)).scalar_one_or_none()
            if st is None:
                raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, "THREAD_STATE_NOT_FOUND"))
            u = plan.thread_state
            if u.link_current_revision:
                st.current_revision_id = tr_id
            if u.last_stage is not None:
                st.last_stage = u.last_stage
            if u.needs_human is not None:
                st.needs_human = u.needs_human
            if u.consume_flow_token:
                st.flow_booking_token = None
        self.db.flush()
        return tr_id

    def _recalculate_price(self, plan: PriceRecalculation, revision) -> None:
        if plan.require_inputs is not None:
            actual = (revision.tipo_vehiculo, revision.zone_group, revision.zone_detail)
            if tuple(plan.require_inputs) != actual:
                logger.warning("BOOKING_PRICE_INPUT_MISMATCH revision_id=%s — price left unset",
                               revision.id)
                return
        from ..repositories.pricing_repository import PricingRepository
        from .pricing import PricingService
        PricingService(repository=PricingRepository()).recalculate_revision_if_possible(
            db=self.db, revision=revision)
        if revision.precio_total is None:
            logger.warning("BOOKING_PRICE_UNRESOLVED revision_id=%s", revision.id)

    # ── defence in depth (not the sandbox) ───────────────────────────────

    def _window_days(self, target: "_Target", current: "_Snapshot") -> tuple:
        days = set()
        if target.start is not None:
            days |= {target.start.date(), (target.end - timedelta(microseconds=1)).date()}
        if current.exists and current.day is not None:
            days.add(current.day)
        return tuple(sorted(days))

    def _window_state(self, days: tuple, *, lock: bool = False) -> tuple:
        """Occupying Revisions and booked/completed ThreadRevisions on `days`, as
        {id: (fields…)}. With `lock` (PostgreSQL, before the write) the existing rows are taken
        FOR SHARE: until this transaction ends nobody else can delete or change them, so any
        later disappearance or change of one of them can only be this transaction's own."""
        from ..models import Revision, ThreadRevision
        if not days:
            return {}, {}
        rq = select(Revision).where(Revision.turno_fecha.in_(days))
        tq = (select(ThreadRevision).where(ThreadRevision.scheduled_date.in_(days))
              .where(ThreadRevision.scheduled_time.is_not(None))
              .where(ThreadRevision.status.in_(("booked", "completed"))))
        if lock and self._postgres():
            rq, tq = rq.with_for_update(read=True), tq.with_for_update(read=True)
        occ = {r.id: (r.turno_fecha, r.turno_hora, normalize_status(r.estado_revision) or r.estado_revision,
                      r.profesional_id)
               for r in self.db.execute(rq).scalars().all()
               if br.is_occupying(r.turno_fecha, r.turno_hora, r.estado_revision)}
        trs = {t.id: (t.status, t.scheduled_date, t.scheduled_time)
               for t in self.db.execute(tq).scalars().all()}
        return occ, trs

    def _written_by_me(self, table: str, ids) -> set:
        """Of `ids`, those last written by THIS transaction (PostgreSQL xmin)."""
        ids = [int(i) for i in ids]
        if not ids or not self._postgres():
            return set()
        return set(self.db.execute(text(
            f"SELECT id FROM {table} WHERE id = ANY(:ids) "
            "AND xmin::text::bigint = txid_current() % 4294967296"), {"ids": ids}).scalars())

    def _assert_continuous(self, txid) -> None:
        """The server still holds the transaction we opened. The driver's server-reported
        status is read FIRST — after a stray COMMIT/ROLLBACK every further statement would
        autocommit on its own — then the transaction id is compared."""
        if not self._postgres():
            return
        dbapi = self.db.connection().connection.dbapi_connection
        info = getattr(dbapi, "info", None)
        if info is not None:
            from psycopg.pq import TransactionStatus
            if info.transaction_status != TransactionStatus.INTRANS:
                raise _InvariantBreach("TRANSACTION_NOT_CONTINUOUS")
        if self.db.execute(text("SELECT txid_current()")).scalar() != txid:
            raise _InvariantBreach("TRANSACTION_NOT_CONTINUOUS")

    def _verify_before_commit(self, txid, revision, expected_row: tuple, target: "_Target",
                              window: tuple, before: tuple, allowed_tr: set) -> None:
        """The transaction is still the one we opened, the schema and session state are still
        bookable, the booking row is as written, and in the locked window no occupying
        Revision or booked ThreadRevision appeared or disappeared except this booking's own."""
        self.db.flush()
        if self._postgres():
            self._assert_continuous(txid)
            reason = verify_booking_schema(self.db)
            if reason:
                raise _InvariantBreach(f"POST_OPERATION_{reason}")
        if self._booking_row(revision.id) != expected_row:
            raise _InvariantBreach("BOOKING_FIELDS_CHANGED")
        occ_before, tr_before = before
        occ_after, tr_after = self._window_state(window)
        own_rev = {revision.id}
        # 1. no pre-existing occupying booking was removed or changed (they are locked FOR
        #    SHARE on PostgreSQL, so only this transaction could have done it)
        for rid, row in occ_before.items():
            if rid not in own_rev and occ_after.get(rid) != row:
                raise _InvariantBreach("OCCUPANCY_SET_CHANGED")
        added = set(occ_after) - set(occ_before) - own_rev
        if self._postgres():
            # 2. new occupying rows: other professionals' concurrent bookings are legitimate;
            #    a new row for OUR professional cannot come from any authority writer while we
            #    hold its exclusive lock, and none may come from this transaction itself
            mine = {i for i in added if occ_after[i][3] == revision.profesional_id}
            if mine or self._written_by_me("revisions", added):
                raise _InvariantBreach("OCCUPANCY_SET_CHANGED")
        elif added:                                   # SQLite: one writer — any change is ours
            raise _InvariantBreach("OCCUPANCY_SET_CHANGED")
        if target.occupying and target.day in window and revision.id not in occ_after:
            raise _InvariantBreach("BOOKING_NOT_OCCUPYING")
        # 3. no pre-existing booked ThreadRevision was un-booked, moved or deleted, and no
        #    booked ThreadRevision other than this booking's own was written by us
        for tid, row in tr_before.items():
            if tid not in allowed_tr and tr_after.get(tid) != row:
                raise _InvariantBreach("BOOKED_THREAD_REVISION_SET_CHANGED")
        tr_added = set(tr_after) - set(tr_before) - set(allowed_tr)
        if tr_added and (not self._postgres() or self._written_by_me("thread_revisions", tr_added)):
            raise _InvariantBreach("BOOKED_THREAD_REVISION_SET_CHANGED")

    def _validate(self, cmd: BookingCommand) -> "_Spec":
        # SB2A-R3: the command is DATA too — exact built-in types only, so nothing a caller
        # supplies can execute code inside the transaction (a Mapping or date subclass would).
        if type(cmd) is not BookingCommand or type(cmd.operation) is not BookingOperation \
                or type(cmd.source) is not BookingSource or type(cmd.revision_fields) is not dict:
            raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, "COMMAND_NOT_DATA"))
        for f in dataclasses.fields(cmd):
            if f.name in ("operation", "source", "revision_fields"):
                continue
            if type(getattr(cmd, f.name)) not in _PLAIN_TYPES:
                raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, f"COMMAND_NOT_DATA:{f.name}"))
        if any(type(k) is not str or type(v) not in _PLAIN_TYPES
               for k, v in cmd.revision_fields.items()):
            raise _Refused(_result(BookingOutcome.VALIDATION_FAILED, "COMMAND_NOT_DATA:revision_fields"))
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
            allowed = _revision_data_columns()
            for key, value in dict(cmd.revision_fields).items():
                # only mapped, non-booking data columns, and only plain values (SB2A-R3)
                if key not in allowed or type(value) not in _PLAIN_TYPES:
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
                             "id", "turno_timezone", "occupied_during", "agenda_occupying"})


def _revision_data_columns() -> frozenset:
    from ..models import Revision
    return frozenset(c.name for c in Revision.__table__.columns) - _BOOKING_FIELDS


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
