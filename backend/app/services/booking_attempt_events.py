"""Convergence Phase 4 — the writer of `booking_attempt_events` (evidence, never authority).

An attempt that produced no committed booking command — refused, contended, failed — and an
operator booking e-mail that was not delivered are recorded here, durably and independently of
any business row (see `models.BookingAttemptEvent`). Nothing on a decision path reads this
table: admission, replay, locking, pricing, availability and every customer response are
decided before, and without, it.

Rules (each one is tested):

* **Where.** Always outside the business transaction that decided the outcome: after its
  rollback, or before `execute` for a pre-execute refusal. `record_attempt_event` opens its
  OWN short session; `insert_attempt_event` joins a caller's transaction only when that
  transaction is itself evidence-only (the refusal finaliser, `booking_refusal`).
* **Bounded.** `lock_timeout` 500 ms and `statement_timeout` 1000 ms on PostgreSQL. The
  table has no foreign key, so an insert takes no lock on any business row.
* **Fail safe.** A failure is logged as `BOOKING_ATTEMPT_EVENT_NOT_RECORDED` and swallowed:
  the outcome was decided already and evidence must never change or retry it. Pre-Wild
  PW1-B (P4RV-09): it is also recorded, once and bounded, as an operator-visible
  `SecurityEvent` of the same name (`security_events.record_operational_condition`).
* **Idempotency.** `event_uid` = SHA-256 of (source, stage, command key, attempt reference,
  reason), UNIQUE, inserted with ON CONFLICT DO NOTHING … RETURNING, so PostgreSQL itself
  reports whether this call admitted the row. A REPLAY of one logical attempt — the same
  inbound WAMID redelivered to CE, the same operator idempotency key sent again, the same
  Data Exchange envelope re-sent by the transport (`dx_attempt_ref`) — derives the same uid
  and writes nothing more. DISTINCT attempts stay distinct: a new inbound message, a new
  operator key, a new customer submission (a fresh IV) derive new uids.
* **Privacy.** Keys and attempt references are stored only as 16-hex SHA-256 prefixes; the
  reason is a typed code truncated to 64 characters; `hist_*` ids are historical scalars.
  Never a raw Flow token, raw command key, phone number, customer text, secret or payload.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

STAGES = ("PRE_EXECUTE", "AUTHORITY", "FINALISE", "NOTIFICATION")
OUTCOMES = ("REFUSED", "RETRYABLE", "CONTENDED", "INTERNAL", "FAILED", "HELD")
EVENT_LOCK_TIMEOUT_MS = 500
EVENT_STATEMENT_TIMEOUT_MS = 1000


def short_hash(value: Optional[str]) -> Optional[str]:
    """A 16-hex SHA-256 prefix, or None. Never the value itself."""
    if value is None or value == "":
        return None
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


def dx_attempt_ref(initial_vector, encrypted_flow_data) -> Optional[str]:
    """Phase 4-R1 (P4RV-02): the replay identity of one Data Exchange delivery, derived from
    the request envelope BEFORE decryption: SHA-256 over the base64 `initial_vector` and
    `encrypted_flow_data` exactly as received. A transport retry re-sends the same bytes and
    derives the same reference; a new customer submission is encrypted under a fresh IV and
    derives a new one. Only this digest leaves the route (and only its 16-hex prefix is ever
    stored) — never the IV, the ciphertext or anything decrypted. None when either field is
    missing or not a string, and the caller then falls back to a fresh reference."""
    if not isinstance(initial_vector, str) or not isinstance(encrypted_flow_data, str) \
            or not initial_vector or not encrypted_flow_data:
        return None
    digest = hashlib.sha256()
    digest.update(b"dxe1\x00")
    digest.update(initial_vector.encode("ascii", "replace"))
    digest.update(b"\x00")
    digest.update(encrypted_flow_data.encode("ascii", "replace"))
    return "dx:" + digest.hexdigest()


def fresh_attempt_ref() -> str:
    """A reference for an attempt that carries no replay identity of its own (a Data Exchange
    request): every call is a distinct attempt."""
    return "req:" + uuid.uuid4().hex


def event_uid(*, source: str, stage: str, command_key: Optional[str],
              attempt_ref: Optional[str], reason: str) -> str:
    """The idempotency rule: one logical attempt at one stage with one reason → one row."""
    raw = "|".join(("bae1", source, stage, command_key or "-", attempt_ref or "-", reason))
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def outcome_for(booking_outcome) -> str:
    """The event outcome for a `BookingOutcome` that did not commit."""
    value = getattr(booking_outcome, "value", booking_outcome)
    if value == "RETRYABLE_FAILURE":
        return "RETRYABLE"
    if value == "INTERNAL_FAILURE":
        return "INTERNAL"
    return "REFUSED"


def _values(*, source, stage, outcome, reason, command_key, attempt_ref, thread_id, offer_id,
            revision_id, thread_revision_id) -> dict:
    if stage not in STAGES:
        raise ValueError(f"unknown attempt-event stage {stage!r}")
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown attempt-event outcome {outcome!r}")
    code = (str(reason) if reason else "UNSPECIFIED")[:64]
    return {
        "event_uid": event_uid(source=source, stage=stage, command_key=command_key,
                               attempt_ref=attempt_ref, reason=code),
        "source": str(source)[:32], "stage": stage, "outcome": outcome, "reason": code,
        "command_key_hash": short_hash(command_key), "attempt_ref_hash": short_hash(attempt_ref),
        "hist_thread_id": _int(thread_id), "hist_offer_id": _int(offer_id),
        "hist_revision_id": _int(revision_id),
        "hist_thread_revision_id": _int(thread_revision_id),
    }


def _int(v) -> Optional[int]:
    try:
        return None if v is None or isinstance(v, bool) else int(v)
    except (TypeError, ValueError):
        return None


def _bound(session: Session) -> None:
    from sqlalchemy import text
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text(f"SET LOCAL lock_timeout = '{EVENT_LOCK_TIMEOUT_MS}ms'"))
        session.execute(text(f"SET LOCAL statement_timeout = '{EVENT_STATEMENT_TIMEOUT_MS}ms'"))


def insert_attempt_event(session: Session, *, source: str, stage: str, outcome: str,
                         reason: str, command_key: Optional[str] = None,
                         attempt_ref: Optional[str] = None, thread_id=None, offer_id=None,
                         revision_id=None, thread_revision_id=None,
                         once_per_attempt: bool = False) -> bool:
    """INSERT … ON CONFLICT (event_uid) DO NOTHING … RETURNING in `session`'s transaction.
    True when THIS call admitted the row, False when this logical attempt was already
    recorded (the admission result the finaliser's counters depend on). Raises on a database
    error: the caller owns this transaction (the refusal finaliser) and decides.

    `once_per_attempt`: the uid ignores the reason, so one attempt at this stage is recorded
    once WHATEVER its reason — the finaliser's rule, because a replay of a refusal that has
    already terminalised its offer classifies differently the second time (e.g.
    OFFER_VEHICLE_CATEGORY_CHANGED, then OFFER_WITHDRAWN) and must not count twice."""
    from ..models import BookingAttemptEvent as E
    values = _values(source=source, stage=stage, outcome=outcome, reason=reason,
                     command_key=command_key, attempt_ref=attempt_ref, thread_id=thread_id,
                     offer_id=offer_id, revision_id=revision_id,
                     thread_revision_id=thread_revision_id)
    if once_per_attempt:
        values["event_uid"] = event_uid(source=source, stage=stage, command_key=command_key,
                                        attempt_ref=attempt_ref, reason="*ONCE*")
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    # Phase 4-R1 (P4RV-03): the database decides admission. `rowcount` cannot — psycopg3
    # reports -1 for INSERT … ON CONFLICT DO NOTHING, so `bool(rowcount)` was always True on
    # PostgreSQL. RETURNING yields the new row's id exactly when THIS statement inserted it,
    # and nothing when the uid already existed (a concurrent inserter of the same uid waits
    # on the unique index and then returns nothing): first = admitted, replay = not.
    stmt = insert(E.__table__).values(**values).on_conflict_do_nothing(
        index_elements=["event_uid"]).returning(E.__table__.c.id)
    return session.execute(stmt).first() is not None


def record_attempt_event(db: Session, **event) -> bool:
    """Write one event in its OWN short, bounded transaction (never `db`'s). True when a row
    was written. Never raises: a failure is logged and the already-decided outcome stands."""
    from .booking_ce_adapter import isolated_session
    try:
        with isolated_session(db) as session:
            _bound(session)
            written = insert_attempt_event(session, **event)
            session.commit()
            return written
    except Exception as exc:
        logger.error("BOOKING_ATTEMPT_EVENT_NOT_RECORDED source=%s stage=%s reason=%s: %s",
                     event.get("source"), event.get("stage"), event.get("reason"),
                     type(exc).__name__)
        try:
            _report_lost_evidence(db, event, exc)
        except Exception as report_exc:  # noqa: BLE001 — this function never raises
            logger.error("BOOKING_ATTEMPT_EVENT_LOSS_NOT_REPORTED: %s", type(report_exc).__name__)
        return False


def _report_lost_evidence(db: Session, event: dict, exc: BaseException) -> None:
    """Pre-Wild PW1-B (P4RV-09): the lost row becomes an operator-visible condition instead of
    a log line alone. Typed codes and historical ids only (the module's privacy rule); the
    outcome stands either way, and `record_operational_condition` never raises or recurses."""
    from .security_events import SecurityEventType, record_operational_condition
    record_operational_condition(
        db, SecurityEventType.BOOKING_ATTEMPT_EVIDENCE_NOT_RECORDED,
        source_component="booking_attempt_events.record_attempt_event",
        thread_id=_int(event.get("thread_id")),
        details={
            "source": str(event.get("source") or "")[:32] or None,
            "stage": event.get("stage") if event.get("stage") in STAGES else None,
            "outcome": event.get("outcome") if event.get("outcome") in OUTCOMES else None,
            "reason": (str(event.get("reason")) if event.get("reason") else "UNSPECIFIED")[:64],
            "error_class": type(exc).__name__[:64],
            "hist_offer_id": _int(event.get("offer_id")),
            "hist_revision_id": _int(event.get("revision_id")),
            "hist_thread_revision_id": _int(event.get("thread_revision_id")),
        })
