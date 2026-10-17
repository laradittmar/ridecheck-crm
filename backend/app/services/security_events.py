"""Security event creation and alerting — M2.

Creates SecurityEvent records and emits alerts for unauthorized outbound path
detections.  All writes use the caller-supplied session (or a fresh one from
the engine).  Alerts use SMTP when available; if not, the DB record IS the
durable evidence and the alert is logged at WARNING.

Event types:
  UNREGISTERED_OUTBOUND_SOURCE        — path_id unknown to registry
  DIRECT_META_CALL_OUTSIDE_AUTHORITY  — Meta API called without gate approval
  META_WAMID_WITHOUT_LOCAL_ATTEMPT    — WAMID in status but no outbound attempt
  META_STATUS_FOR_UNKNOWN_WAMID       — status webhook WAMID not in DB
  SUCCESSFUL_META_SEND_WHILE_OUTBOUND_OFF — delivered/sent status + OUTBOUND=false + unknown WAMID
  OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE — gate called without path_id
  LEGACY_SENDER_REACHED               — attempt via a retired send path
  DEPLOYMENT_WITH_UNREGISTERED_META_SEND_PATH — startup: new unregistered call site

Operational conditions (pre-Wild PW1-B, P4RV-09) — failures of OBSERVABILITY, never of a
booking decision, written by `record_operational_condition`:
  BOOKING_ATTEMPT_EVIDENCE_NOT_RECORDED   — a `booking_attempt_events` row could not be written
  EXTERNAL_IO_INSIDE_BOOKING_TRANSACTION  — an e-mail sender tripped the booking I/O guard
"""
from __future__ import annotations

import hashlib
import logging
import smtplib
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any

from sqlalchemy.orm import Session

from ..models import SecurityEvent
from ..settings import get_settings

logger = logging.getLogger(__name__)

_ALERT_EMAIL_TO = "ridecheckassistance@gmail.com"


class SecurityEventType:
    UNREGISTERED_OUTBOUND_SOURCE = "UNREGISTERED_OUTBOUND_SOURCE"
    DIRECT_META_CALL_OUTSIDE_AUTHORITY = "DIRECT_META_CALL_OUTSIDE_AUTHORITY"
    META_WAMID_WITHOUT_LOCAL_ATTEMPT = "META_WAMID_WITHOUT_LOCAL_ATTEMPT"
    META_STATUS_FOR_UNKNOWN_WAMID = "META_STATUS_FOR_UNKNOWN_WAMID"
    SUCCESSFUL_META_SEND_WHILE_OUTBOUND_OFF = "SUCCESSFUL_META_SEND_WHILE_OUTBOUND_OFF"
    OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE = "OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE"
    LEGACY_SENDER_REACHED = "LEGACY_SENDER_REACHED"
    DEPLOYMENT_WITH_UNREGISTERED_META_SEND_PATH = "DEPLOYMENT_WITH_UNREGISTERED_META_SEND_PATH"
    BOOKING_ATTEMPT_EVIDENCE_NOT_RECORDED = "BOOKING_ATTEMPT_EVIDENCE_NOT_RECORDED"
    EXTERNAL_IO_INSIDE_BOOKING_TRANSACTION = "EXTERNAL_IO_INSIDE_BOOKING_TRANSACTION"


#: The operational conditions `/api/ops/booking-attention` lists (PW1-B).
OPERATIONAL_CONDITION_TYPES = (
    SecurityEventType.BOOKING_ATTEMPT_EVIDENCE_NOT_RECORDED,
    SecurityEventType.EXTERNAL_IO_INSIDE_BOOKING_TRANSACTION,
)
CONDITION_LOCK_TIMEOUT_MS = 500
CONDITION_STATEMENT_TIMEOUT_MS = 1000


class SecuritySeverity:
    HIGH = "HIGH"
    BLOCKER = "BLOCKER"


def _wa_id_hash(wa_id: str | None) -> str | None:
    if not wa_id:
        return None
    return hashlib.sha256(wa_id.encode()).hexdigest()[:16]


def create_security_event(
    db: Session,
    event_type: str,
    severity: str,
    *,
    path_id: str | None = None,
    source_component: str | None = None,
    wamid: str | None = None,
    wa_id: str | None = None,
    deployment_id: str | None = None,
    correlation_id: str | None = None,
    thread_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> SecurityEvent:
    """Persist a SecurityEvent and emit an alert email.

    The caller controls the session lifecycle.  This function adds the event,
    flushes to get an ID, then attempts the alert.  The caller must commit.
    """
    if correlation_id is None:
        correlation_id = str(uuid.uuid4())

    event = SecurityEvent(
        detected_at=datetime.now(timezone.utc),
        event_type=event_type,
        severity=severity,
        path_id=path_id,
        source_component=source_component,
        wamid=wamid,
        wa_id_hash=_wa_id_hash(wa_id),
        deployment_id=deployment_id,
        correlation_id=correlation_id,
        thread_id=thread_id,
        details=details or {},
    )
    db.add(event)
    db.flush()

    logger.warning(
        "SECURITY_EVENT type=%s severity=%s id=%s path=%s wamid=%s corr=%s",
        event_type, severity, event.id, path_id or "-", wamid or "-", correlation_id,
    )

    _try_send_alert(event)
    return event


def _try_send_alert(event: SecurityEvent) -> None:
    """Send email for HIGH/BLOCKER events.  Failures are non-fatal — DB is the record."""
    s = get_settings()
    if not s.smtp_host or not s.smtp_user or not s.smtp_password:
        logger.warning(
            "M2_ALERT_SMTP_UNAVAILABLE event_id=%s type=%s severity=%s — "
            "persisted in security_events only",
            event.id, event.event_type, event.severity,
        )
        return

    subject = f"[{event.severity}] RideCheck security event: {event.event_type}"
    body_lines = [
        f"Security event detected.",
        f"",
        f"Type:        {event.event_type}",
        f"Severity:    {event.severity}",
        f"Event ID:    {event.id}",
        f"Detected at: {event.detected_at.isoformat()}",
        f"Path:        {event.path_id or '-'}",
        f"Component:   {event.source_component or '-'}",
        f"WAMID:       {event.wamid or '-'}",
        f"WA ID hash:  {event.wa_id_hash or '-'}",
        f"Thread:      {event.thread_id or '-'}",
        f"Deployment:  {event.deployment_id or '-'}",
        f"Correlation: {event.correlation_id or '-'}",
        f"",
        f"Details: {event.details}",
        f"",
        f"Action required: investigate immediately.",
    ]
    body = "\n".join(body_lines)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = s.smtp_from or s.smtp_user
    msg["To"] = _ALERT_EMAIL_TO
    msg.set_content(body)

    try:
        from .booking_authority import forbid_external_io  # convergence Phase 4 (P2R-05)
        forbid_external_io("smtp")
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as conn:
            conn.starttls()
            conn.login(s.smtp_user, s.smtp_password)
            conn.send_message(msg)
        logger.info(
            "M2_ALERT_SENT event_id=%s type=%s severity=%s",
            event.id, event.event_type, event.severity,
        )
    except Exception as exc:
        logger.warning(
            "M2_ALERT_SMTP_FAILED event_id=%s type=%s: %s — "
            "event persisted in security_events",
            event.id, event.event_type, exc,
        )


def record_operational_condition(db: Session | None, event_type: str, *,
                                 source_component: str, thread_id: int | None = None,
                                 details: dict[str, Any] | None = None) -> bool:
    """Make a failure of observability visible to an operator. True when the row was written.

    Pre-Wild PW1-B (P4RV-09). Two failures used to reach only the container log: a
    `booking_attempt_events` row that could not be written, and an
    `ExternalIOInsideBookingTransaction` swallowed by an e-mail sender's `except Exception`
    (read as an ordinary "send failed"). This records them as a HIGH `SecurityEvent` — the
    existing critical-event store — which `/api/ops/booking-attention` lists behind the CRM
    session.

    Rules:

    * Evidence, never authority. It runs after the outcome was decided and nothing reads it
      back on a decision path: booking, offer, ledger, retry and customer reply are unchanged
      whether it succeeds or not.
    * Its OWN short session on `db`'s engine (the application engine when `db` is None),
      never the caller's transaction, bounded (`lock_timeout` 500 ms, `statement_timeout`
      1000 ms on PostgreSQL). `security_events` has no foreign key, so the insert takes no
      lock on a business row even while a booking transaction is open.
    * Never raises, and never recurses: if this write fails too, the failure is logged once
      and the call returns False. It does not call itself, `record_attempt_event`, or anything
      else that could report a failure back here.
    * `details` carries typed codes and historical ids only — never a token, command key,
      phone, e-mail address or customer text.
    """
    try:
        from sqlalchemy import text as _text
        if db is not None:
            bind = db.get_bind()
        else:
            from ..db import engine as bind
        engine = getattr(bind, "engine", bind)
        with Session(bind=engine, expire_on_commit=False) as session:
            if engine.dialect.name == "postgresql":
                session.execute(_text(f"SET LOCAL lock_timeout = '{CONDITION_LOCK_TIMEOUT_MS}ms'"))
                session.execute(_text(
                    f"SET LOCAL statement_timeout = '{CONDITION_STATEMENT_TIMEOUT_MS}ms'"))
            create_security_event(session, event_type, SecuritySeverity.HIGH,
                                  source_component=source_component[:200],
                                  thread_id=thread_id, details=details or {})
            session.commit()
        return True
    except Exception as exc:  # noqa: BLE001 — the terminal fallback is this log line
        logger.error("OPERATIONAL_CONDITION_NOT_RECORDED type=%s component=%s: %s",
                     event_type, source_component, type(exc).__name__)
        return False
