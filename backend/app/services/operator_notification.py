"""W1-F2 — the operator's WhatsApp notice of a committed customer booking.

The first controlled Wild (2026-10-21) measured that a Flow booking reached nobody on the team
while the customer was told "un representante … se estará comunicando". Owner decision: the
launch professional receives exactly ONE operational WhatsApp notice per committed booking, on
his own business number (never the customer-facing one), idempotently and transaction-backed,
with one retry and then a visible operator-action-required state — and never an e-mail in its
place.

How each guarantee is made:

* **Only after commit.** The trigger is the committed `booking_commands` row, which the
  booking's own transaction wrote (`BookingAuthority`); this module only ever reads committed
  ledger rows, and writes nothing in, or into, a booking transaction. A crash anywhere here
  loses no obligation: the ledger row is still there on the next pass.
* **Exactly one record.** `operator_notifications` holds one row per booking command
  (`UNIQUE (booking_command_id)`, `UNIQUE (identity)`). A Flow resubmission or an idempotent
  replay creates no ledger row and so no notice; a provider status callback only updates the
  gate's outbound row by WAMID and never reaches this module.
* **At most one delivery per attempt, at most two attempts.** Each attempt is claimed by a
  conditional UPDATE (PENDING → SENDING, attempts + 1) and crosses `OutboundSafetyGate` under a
  delivery identity derived from the notice's identity and the attempt number, which the gate
  admits once, ever. A retry follows only a DEFINITE non-delivery (nothing left the process,
  or Meta's documented rejection — `meta_rejection_is_definite`). Anything uncertain — a
  timeout, a 5xx, a success without id, a sender that died holding its lease — is HELD and
  never repeated automatically, so the operator is never told twice.
* **Visible when it cannot be sent.** FAILED (two definite failures) and HELD (not
  configured, no summary, destination refused, uncertain) are terminal and are listed as
  `OPERATOR_NOTIFICATION_REQUIRED` on `/api/ops/booking-attention`.
* **Not a send-to-any-phone path.** The destination is read from protected runtime
  configuration only; no caller can pass one. The gate refuses the OPERATOR_NOTIFICATION path
  for any other recipient. `OUTBOUND_ENABLED` stays the external-send authority: the gate
  checks it and so does the transport's first statement.
* **No backfill.** Only ledger rows above `system_settings[FLOOR_KEY]` — the highest ledger
  id when migration `20261022_operator_notifications` ran — and committed within
  `LOOKBACK_HOURS` are notified.

Message content: an approved Meta TEMPLATE (a business-initiated message to a number outside
its 24-hour customer-service window must be one), with ten body parameters in the order of
`TEMPLATE_PARAMETERS`. The operational summary only — never the conversation transcript.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..settings import get_settings

logger = logging.getLogger(__name__)

FLOOR_KEY = "operator_notification_floor_ledger_id"
KIND = "BOOKING_CREATED"
PROVIDER = "META_WHATSAPP_TEMPLATE"
#: One initial attempt plus at most one retry (owner decision 6).
MAX_ATTEMPTS = 2
RETRY_AFTER_SECONDS = 60
#: A claimed attempt still SENDING after this is a lost sender: HELD, never reclaimed.
LEASE_SECONDS = 120
LOOKBACK_HOURS = 24
CHECK_INTERVAL_SECONDS = 15
#: The ledger sources that are customer bookings (the same two `/api/ops` notifies for).
NOTIFIED_SOURCES = ("WHATSAPP_FLOW", "CONVERSATION_ENGINE")
TERMINAL_REQUIRED = ("FAILED", "HELD")

#: The approved template's body parameters, in order ({{1}} … {{10}}). The template is the
#: owner's to create in Meta; this order is the contract it must follow.
TEMPLATE_PARAMETERS = (
    "evento", "cliente", "whatsapp_cliente", "vehiculo", "lugar", "fecha_hora",
    "profesional", "estado", "identificadores", "crm",
)
EVENT_LABEL = "Nueva reserva confirmada"

_DESTINATION = re.compile(r"[1-9][0-9]{7,14}")
_TEMPLATE_NAME = re.compile(r"[a-z0-9_]{1,128}")
_LANGUAGE = re.compile(r"[a-z]{2,3}(_[A-Z]{2})?")
_PARAM_MAX = 120
_WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


@dataclass(frozen=True)
class OperatorConfig:
    destination: str
    template_name: str
    language: str


def config_problem(settings=None) -> Optional[str]:
    """Why the operator notice may not be sent (a typed code), or None when it may. Reads
    runtime configuration only; never prints a value."""
    s = settings or get_settings()
    if getattr(s, "operator_whatsapp_notify_enabled", False) is not True:
        return "NOT_CONFIGURED_DISABLED"
    dest = getattr(s, "operator_whatsapp_to", "") or ""
    if not dest:
        return "NOT_CONFIGURED_DESTINATION_MISSING"
    if type(dest) is not str or not _DESTINATION.fullmatch(dest):
        return "NOT_CONFIGURED_DESTINATION_MALFORMED"
    name = getattr(s, "operator_booking_template_name", "") or ""
    if not name:
        return "NOT_CONFIGURED_TEMPLATE_MISSING"
    if type(name) is not str or not _TEMPLATE_NAME.fullmatch(name):
        return "NOT_CONFIGURED_TEMPLATE_MALFORMED"
    lang = getattr(s, "operator_booking_template_language", "") or ""
    if not lang:
        return "NOT_CONFIGURED_LANGUAGE_MISSING"
    if type(lang) is not str or not _LANGUAGE.fullmatch(lang):
        return "NOT_CONFIGURED_LANGUAGE_MALFORMED"
    token = (getattr(s, "whatsapp_token", "") or "").strip()
    if not token or token.lower() == "dummy" or not (
            getattr(s, "whatsapp_phone_number_id", "") or "").strip():
        return "NOT_CONFIGURED_TRANSPORT"
    return None


def operator_config(settings=None) -> Optional[OperatorConfig]:
    """The validated configuration, or None (fail closed) — see `config_problem`."""
    s = settings or get_settings()
    if config_problem(s) is not None:
        return None
    return OperatorConfig(destination=s.operator_whatsapp_to,
                          template_name=s.operator_booking_template_name,
                          language=s.operator_booking_template_language)


def notification_identity(ledger_key: str) -> str:
    """The notice's identity: derived from the committed command's ledger key only — never
    from prose or a timestamp. 64 lowercase hex."""
    if type(ledger_key) is not str or not ledger_key:
        raise ValueError("an operator notice needs its booking command's ledger key")
    return hashlib.sha256(f"operator-booking-notification|v1|{ledger_key}".encode()).hexdigest()


def attempt_fingerprint(identity: str, attempt: int) -> str:
    """The gate delivery identity of attempt `attempt` (1 or 2) of a notice."""
    if attempt not in range(1, MAX_ATTEMPTS + 1):
        raise ValueError("an operator notice has at most two attempts")
    return hashlib.sha256(f"{identity}|attempt|{attempt}".encode()).hexdigest()


def _hash16(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


# ── the summary ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BookingSummary:
    """What the operator needs to continue — and nothing else (no transcript)."""
    parameters: tuple
    customer_wa_id: Optional[str]

    @property
    def preview(self) -> str:
        """The rendered notice, recorded as the outbound row's text."""
        return "\n".join(f"{k}: {v}" for k, v in zip(TEMPLATE_PARAMETERS, self.parameters))


def _param(value) -> str:
    """One template parameter: Meta refuses newlines, tabs and long runs of spaces, and an
    empty parameter."""
    text = " ".join(str(value if value is not None else "").split())
    return (text or "Sin dato")[:_PARAM_MAX]


def build_summary(db: Session, ledger) -> Optional[BookingSummary]:
    """The operational summary of a committed booking, from committed rows only, or None when
    the booking's revision is gone."""
    from ..models import Lead, Profesional, Revision, WhatsAppContact, WhatsAppThread
    rev = db.get(Revision, ledger.revision_id) if ledger.revision_id is not None else None
    if rev is None:
        return None
    lead = db.get(Lead, ledger.lead_id) if ledger.lead_id is not None else None
    thread = db.get(WhatsAppThread, ledger.thread_id) if ledger.thread_id is not None else None
    contact = db.get(WhatsAppContact, thread.contact_id) if thread is not None else None
    prof_id = rev.profesional_id or ledger.profesional_id
    prof = db.get(Profesional, prof_id) if prof_id is not None else None

    name = " ".join(x for x in ((lead.nombre if lead else None),
                                (lead.apellido if lead else None)) if x)
    wa_id = contact.wa_id if contact is not None else None
    vehicle = " ".join(str(x) for x in (rev.marca, rev.modelo, rev.anio) if x)
    if rev.tipo_vehiculo:
        vehicle = f"{vehicle} ({rev.tipo_vehiculo})" if vehicle else rev.tipo_vehiculo
    zone = ", ".join(x for x in (rev.zone_detail, rev.zone_group) if x)
    place = " · ".join(x for x in (rev.direccion_texto, zone) if x)
    when = None
    if rev.turno_fecha is not None:
        when = f"{_WEEKDAYS[rev.turno_fecha.weekday()]} {rev.turno_fecha:%d/%m/%Y}"
        if rev.turno_hora is not None:
            when += f" {rev.turno_hora:%H:%M}"
    professional = " ".join(x for x in ((prof.nombre if prof else None),
                                        (prof.apellido if prof else None)) if x)
    status = f"Reservado por el cliente · confirmación al cliente: {ledger.ack_status}"
    ids = f"Reserva {ledger.id} · Lead {ledger.lead_id} · Conversación {ledger.thread_id}"
    crm = f"/whatsapp/thread/{ledger.thread_id}" if ledger.thread_id is not None else None
    values = (EVENT_LABEL, name, f"+{wa_id}" if wa_id else None, vehicle, place, when,
              professional, status, ids, crm)
    return BookingSummary(parameters=tuple(_param(v) for v in values), customer_wa_id=wa_id)


# ── enqueue: committed ledger rows → one notice row each ─────────────────────

def _floor(db: Session) -> Optional[int]:
    from ..models import SystemSetting
    row = db.get(SystemSetting, FLOOR_KEY)
    try:
        return int(row.value) if row is not None else None
    except (TypeError, ValueError):
        return None


def enqueue_committed_bookings(db: Session, *, now: Optional[datetime] = None) -> list[int]:
    """Create the notice row for every eligible committed booking that has none. Returns the
    ledger ids enqueued. Without a valid floor nothing is enqueued (fail closed: no backfill)."""
    from ..models import BookingCommandRecord as R
    from ..models import OperatorNotification as N
    now = now or datetime.now(timezone.utc)
    floor = _floor(db)
    if floor is None:
        logger.error("OPERATOR_NOTIFICATION floor %s missing or malformed — nothing enqueued",
                     FLOOR_KEY)
        return []
    rows = db.execute(
        select(R.id, R.idempotency_key, R.thread_id, R.lead_id, R.revision_id,
               R.thread_revision_id)
        .where(R.id > floor, R.command_state == "COMMITTED", R.operation == "CREATE",
               R.occupying.is_(True), R.source.in_(NOTIFIED_SOURCES),
               R.revision_id.is_not(None), R.thread_revision_id.is_not(None),
               R.committed_at >= now - timedelta(hours=LOOKBACK_HOURS),
               ~select(N.id).where(N.booking_command_id == R.id).exists())
        .order_by(R.id)
    ).all()
    db.rollback()                                   # the read leaves no transaction open
    problem = config_problem()
    enqueued = []
    for r in rows:
        held = problem is not None
        try:
            with db.begin():
                db.add(N(booking_command_id=r.id, identity=notification_identity(
                    r.idempotency_key), kind=KIND, status="HELD" if held else "PENDING",
                    attempts=0, provider=PROVIDER, last_error=problem if held else None,
                    next_attempt_at=None if held else now, created_at=now, updated_at=now,
                    hist_thread_id=r.thread_id, hist_lead_id=r.lead_id,
                    hist_revision_id=r.revision_id,
                    hist_thread_revision_id=r.thread_revision_id))
        except IntegrityError:
            continue                                 # another pass recorded it first
        enqueued.append(int(r.id))
        logger.info("OPERATOR_NOTIFICATION enqueued ledger=%s status=%s%s", r.id,
                    "HELD" if held else "PENDING", f" reason={problem}" if held else "")
    return enqueued


# ── claim → send → record ─────────────────────────────────────────────────────

def hold_lost_senders(db: Session, *, now: Optional[datetime] = None) -> int:
    """A SENDING row whose lease expired had a sender that may have reached Meta: HELD as
    uncertain, never reclaimed (an operator decides)."""
    from ..models import OperatorNotification as N
    now = now or datetime.now(timezone.utc)
    with db.begin():
        res = db.execute(update(N).where(N.status == "SENDING", N.lease_expires_at < now)
                         .values(status="HELD", last_error="UNCERTAIN_LOST_SENDER",
                                 claim_token=None, updated_at=now))
    return int(res.rowcount or 0)


@dataclass(frozen=True)
class Claim:
    notification_id: int
    token: str
    attempt: int
    identity: str
    booking_command_id: int


def claim_next(db: Session, *, now: Optional[datetime] = None) -> Optional[Claim]:
    """Claim one due PENDING notice: PENDING → SENDING and attempts + 1, in one conditional
    UPDATE committed before anything is sent. None when nothing is due."""
    from ..models import OperatorNotification as N
    now = now or datetime.now(timezone.utc)
    candidates = db.execute(
        select(N.id).where(N.status == "PENDING", N.attempts < MAX_ATTEMPTS,
                           N.next_attempt_at <= now).order_by(N.id).limit(5)).scalars().all()
    db.rollback()
    for nid in candidates:
        token = str(uuid.uuid4())
        with db.begin():
            res = db.execute(
                update(N).where(N.id == nid, N.status == "PENDING", N.attempts < MAX_ATTEMPTS)
                .values(status="SENDING", claim_token=token, attempts=N.attempts + 1,
                        lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
                        updated_at=now))
        if res.rowcount != 1:
            continue                                 # another sender took it
        row = db.execute(select(N.attempts, N.identity, N.booking_command_id)
                         .where(N.id == nid)).one()
        db.rollback()
        return Claim(notification_id=int(nid), token=token, attempt=int(row.attempts),
                     identity=row.identity, booking_command_id=int(row.booking_command_id))
    return None


@dataclass(frozen=True)
class Outcome:
    sent: bool
    #: True only when it is PROVEN nothing reached the operator AND a second attempt could
    #: succeed. False for anything uncertain, and for a definite refusal no retry can change
    #: (not configured, no summary, destination refused) — both are HELD, never repeated.
    retryable: bool
    error: Optional[str] = None
    wamid: Optional[str] = None
    outbound_message_id: Optional[int] = None


def record(db: Session, claim: Claim, outcome: Outcome, config: Optional[OperatorConfig], *,
           now: Optional[datetime] = None) -> bool:
    """Record one attempt's outcome, only while the claim still owns the row. SENT; a definite
    retryable failure → PENDING again (retry later) or FAILED after the second; anything else
    → HELD. Returns whether the row was updated."""
    from ..models import OperatorNotification as N
    now = now or datetime.now(timezone.utc)
    if outcome.sent:
        values = dict(status="SENT", provider_message_id=outcome.wamid, sent_at=now,
                      last_error=None)
    elif outcome.retryable and claim.attempt < MAX_ATTEMPTS:
        values = dict(status="PENDING", last_error=outcome.error,
                      next_attempt_at=now + timedelta(seconds=RETRY_AFTER_SECONDS))
    elif outcome.retryable:
        values = dict(status="FAILED", last_error=outcome.error, next_attempt_at=None)
    else:
        values = dict(status="HELD", last_error=outcome.error, next_attempt_at=None)
    if config is not None:
        values.update(destination_hash=_hash16(config.destination),
                      template_name=config.template_name, template_language=config.language)
    if outcome.outbound_message_id is not None:
        values["outbound_message_id"] = outcome.outbound_message_id
    with db.begin():
        res = db.execute(update(N).where(N.id == claim.notification_id,
                                         N.claim_token == claim.token, N.status == "SENDING")
                         .values(**values, claim_token=None, lease_expires_at=None,
                                 updated_at=now))
    ok = res.rowcount == 1
    logger.log(logging.INFO if outcome.sent else logging.ERROR,
               "OPERATOR_NOTIFICATION ledger=%s attempt=%s -> %s%s", claim.booking_command_id,
               claim.attempt, values["status"], f" ({outcome.error})" if outcome.error else "")
    return ok


def _operator_thread(db: Session, destination: str) -> tuple[Optional[int], Optional[str]]:
    """The operator's own thread (created on first use), or a refusal code. A thread that is
    already a customer conversation (linked to a lead) is never used."""
    from ..models import WhatsAppContact, WhatsAppThread
    with db.begin():
        contact = db.execute(select(WhatsAppContact).where(
            WhatsAppContact.wa_id == destination)).scalar_one_or_none()
        if contact is None:
            contact = WhatsAppContact(wa_id=destination, display_name="Operador RideCheck")
            db.add(contact)
            db.flush()
        thread = db.execute(select(WhatsAppThread).where(
            WhatsAppThread.contact_id == contact.id).order_by(WhatsAppThread.id)
        ).scalars().first()
        if thread is None:
            thread = WhatsAppThread(contact_id=contact.id, lead_id=None, unread_count=0)
            db.add(thread)
            db.flush()
        if thread.lead_id is not None:
            return None, "DESTINATION_IS_CUSTOMER_THREAD"
        return int(thread.id), None


def send_claimed(db: Session, claim: Claim, config: Optional[OperatorConfig]) -> Outcome:
    """One claimed attempt, end to end, through `OutboundSafetyGate`. Never raises."""
    from ..models import BookingCommandRecord
    from ..ui.whatsapp_ui import MetaSendError, _send_whatsapp_cloud_template
    from .booking_acknowledgement import meta_rejection_is_definite
    from .outbound_guard import OutboundBlockedError
    from .outbound_path_registry import OutboundPathId, get_deployment_id
    from .outbound_safety_gate import GateOutcome, OutboundSafetyGate

    if config is None:
        return Outcome(False, False, config_problem() or "NOT_CONFIGURED")
    boundary = "before"
    gate = result = None
    try:
        ledger = db.get(BookingCommandRecord, claim.booking_command_id)
        summary = build_summary(db, ledger) if ledger is not None else None
        db.rollback()
        if summary is None:
            return Outcome(False, False, "SUMMARY_UNAVAILABLE")
        if summary.customer_wa_id == config.destination:
            return Outcome(False, False, "DESTINATION_IS_CUSTOMER")
        thread_id, refused = _operator_thread(db, config.destination)
        if refused is not None:
            return Outcome(False, False, refused)
        gate = OutboundSafetyGate(db)
        result = gate.attempt(
            wa_id=config.destination, thread_id=thread_id, text=summary.preview,
            message_type="template", path_id=OutboundPathId.OPERATOR_NOTIFICATION.value,
            deployment_id=get_deployment_id(), correlation_id=f"opn:{claim.identity[:32]}",
            delivery_identity=attempt_fingerprint(claim.identity, claim.attempt))
        if result.outcome != GateOutcome.ALLOWED:
            if result.outcome == GateOutcome.BLOCKED_DUPLICATE:
                # this attempt's identity crossed the gate before: it may have been sent
                return Outcome(False, False, "UNCERTAIN_DELIVERY_IDENTITY_SEEN",
                               outbound_message_id=result.message_id)
            # nothing left the process; a refused recipient will not change by retrying
            return Outcome(False, result.outcome != GateOutcome.BLOCKED_UNAUTHORIZED_PATH,
                           f"GATE_{result.outcome.value}"[:64],
                           outbound_message_id=result.message_id)
        try:
            boundary = "entered"
            wamid, _ = _send_whatsapp_cloud_template(
                to_wa_id=config.destination, template_name=config.template_name,
                language_code=config.language, body_parameters=list(summary.parameters))
        except OutboundBlockedError:
            boundary = "settled"                       # the transport's first statement
            gate.mark_failed(result.message_id)
            return Outcome(False, True, "PRE_DISPATCH_OUTBOUND_BLOCKED",
                           outbound_message_id=result.message_id)
        except MetaSendError as exc:
            definite = meta_rejection_is_definite(exc)
            gate.mark_failed(result.message_id, meta_http_status=exc.http_status,
                             meta_error_payload=exc.to_payload())
            boundary = "settled"
            if definite:
                return Outcome(False, True, f"META_REJECTED_{exc.http_status}",
                               outbound_message_id=result.message_id)
            return Outcome(False, False, f"UNCERTAIN_META_{exc.http_status or 'NETWORK'}"[:64],
                           outbound_message_id=result.message_id)
        except RuntimeError as exc:
            # raised by the template sender's own configuration checks, before the network
            boundary = "settled"
            gate.mark_failed(result.message_id)
            logger.error("OPERATOR_NOTIFICATION transport not configured: %s",
                         type(exc).__name__)
            return Outcome(False, False, "PRE_DISPATCH_TRANSPORT_NOT_CONFIGURED",
                           outbound_message_id=result.message_id)
        boundary = "settled"
        try:
            gate.mark_sent(result.message_id, wamid)
        except Exception:                               # bookkeeping ≠ delivery
            logger.exception("OPERATOR_NOTIFICATION sent but gate bookkeeping failed")
        return Outcome(True, False, None, wamid=wamid, outbound_message_id=result.message_id)
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        logger.error("OPERATOR_NOTIFICATION attempt failed at %s: %s", boundary,
                     type(exc).__name__)
        mid = result.message_id if result is not None else None
        if boundary == "before":
            return Outcome(False, True, "PRE_SEND_FAILURE", outbound_message_id=mid)
        return Outcome(False, False, "UNCERTAIN_AFTER_TRANSPORT_BOUNDARY",
                       outbound_message_id=mid)


def run_once(db: Session, *, now: Optional[datetime] = None, max_sends: int = 10) -> dict:
    """One pass: hold lost senders, enqueue new committed bookings, send what is due."""
    held = hold_lost_senders(db, now=now)
    enqueued = enqueue_committed_bookings(db, now=now)
    config = operator_config()
    sent = []
    for _ in range(max_sends):
        claim = claim_next(db, now=now)
        if claim is None:
            break
        outcome = send_claimed(db, claim, config)
        record(db, claim, outcome, config, now=now)
        sent.append((claim.booking_command_id, claim.attempt, outcome.sent))
    return {"held_lost": held, "enqueued": enqueued, "attempts": sent}


def _run_check() -> None:
    from ..db import SessionLocal
    db = SessionLocal()
    try:
        run_once(db)
    except Exception:
        logger.exception("operator_notification pass failed")
    finally:
        db.close()


async def operator_notification_loop() -> None:
    """Background task. Inert while the notice is not configured: committed bookings are then
    recorded HELD (NOT_CONFIGURED_*) and listed for the team, and nothing is sent."""
    while True:
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(_run_check)
        except Exception:
            logger.exception("operator_notification_loop unhandled error")
