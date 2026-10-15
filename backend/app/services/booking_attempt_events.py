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
  the outcome was decided already and evidence must never change or retry it.
* **Idempotency.** `event_uid` = SHA-256 of (source, stage, command key, attempt reference,
  reason), UNIQUE, inserted with ON CONFLICT DO NOTHING. A HANDLER REPLAY of one logical
  attempt — the same inbound WAMID redelivered to CE, the same operator idempotency key sent
  again — derives the same uid and writes nothing more. DISTINCT attempts stay distinct: a
  new inbound message, a new operator key, or a new Data Exchange request (which carries no
  replay identity of its own, so each request gets a fresh reference) derive new uids.
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
    """INSERT … ON CONFLICT (event_uid) DO NOTHING in `session`'s transaction. True when a row
    was written, False when this logical attempt was already recorded. Raises on a database
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
    stmt = insert(E.__table__).values(**values).on_conflict_do_nothing(
        index_elements=["event_uid"])
    return bool(session.execute(stmt).rowcount)


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
        return False
