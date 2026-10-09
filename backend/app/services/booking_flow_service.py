"""M21.3-C-D — RideCheck Booking Flow backend service.

Handles the complete data exchange lifecycle for the RideCheck Booking Meta Flow
(version 7.3, Data API 3.0, Flow ID 28104222025943520):

  INIT          → APPOINTMENT screen (dynamic dates)
  date_selected → APPOINTMENT screen (dynamic time slots)
  prepare_summary → SUMMARY screen
  confirm_booking → atomic booking + SUCCESS

Outbound is NOT triggered here.  The data exchange endpoint receives and responds
to Flow interaction; any eventual WhatsApp messages (confirmation, conflict) are
emitted by CE through the normal gate when outbound is enabled.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models import (
    Lead,
    Revision,
    ThreadRevision,
    WhatsAppContact,
    WhatsAppThread,
    WhatsAppThreadCandidate,
    WhatsAppThreadState,
)
from ..repositories.pricing_repository import PricingRepository
from ..schemas.schedule import ScheduleCheckIn
from ..services.pricing import PricingService
from ..services.schedule import ScheduleService
from .booking_authority import (
    AvailabilityRevalidation,
    BookingAttachmentPlan,
    BookingAuthority,
    BookingCommand,
    BookingOperation,
    BookingOutcome,
    BookingSource,
    LeadIdentity,
    LeadUpdate,
    PriceRecalculation,
    ThreadRevisionAttachment,
    ThreadStateUpdate,
    flow_idempotency_key,
)

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
BOOKING_HORIZON_DAYS = 14
TOKEN_MAX_AGE_SECONDS = 7200        # 2-hour Flow session window
STAGE_BOOKED = "BOOKED"

#: SB2A-R3 — the owner-approved successful-Flow acknowledgement (owner decision, final). It
#: supersedes the earlier "Recibimos tu solicitud…" receipt on Track B's Flow confirmation
#: path. `build_booking_receipt_message` below is left untouched because the Conversation
#: Engine (Track A) still imports it; aligning CE copy is a Track A item.
BOOKING_FLOW_RECEIPT_TEXT = (
    "Gracias por completarlo. Un representante de RideCheck se estará comunicando con vos."
)

FLOW_VERSION = "3.0"

# Days-of-week labels (Spanish) — used to construct human-readable date titles
_DAY_NAMES = {
    0: "lunes",
    1: "martes",
    2: "miércoles",
    3: "jueves",
    4: "viernes",
    5: "sábado",
    6: "domingo",
}
_MONTH_NAMES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
    5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}


# ── Booking context ───────────────────────────────────────────────────────────

@dataclass
class BookingContext:
    thread: WhatsAppThread
    contact: WhatsAppContact
    lead: Lead
    state: WhatsAppThreadState
    candidate: Optional[WhatsAppThreadCandidate]
    vehicle_summary: str
    location_summary: str
    zone_group: Optional[str]
    zone_detail: Optional[str]
    booking_token: str


#: What the customer sees when the authority refuses a confirmation. Nothing is booked and
#: nothing is acknowledged; the APPOINTMENT screen comes back with what is free now.
_FLOW_REFUSAL_MESSAGES: dict = {
    BookingOutcome.SLOT_CONFLICT: ("Justo ese horario dejó de estar disponible. "
                                   "Elegí otro de los horarios actualizados."),
    BookingOutcome.CALENDAR_EXCEPTION: ("Ese día u horario ya no está disponible en la agenda. "
                                        "Elegí otro de los horarios actualizados."),
    BookingOutcome.RETRYABLE_FAILURE: ("No pudimos confirmar el turno en este momento. "
                                       "Volvé a intentarlo en unos segundos."),
    BookingOutcome.MISSING_PROFESSIONAL: ("No pudimos confirmar el turno. "
                                          "Un asesor se contacta a la brevedad."),
    BookingOutcome.INTERNAL_FAILURE: ("No pudimos confirmar el turno. "
                                      "Un asesor se contacta a la brevedad."),
}


@dataclass(frozen=True)
class ReceiptOutcome:
    """Result of one receipt attempt. Truthy only when Meta accepted the message AND returned
    a usable message id. `ambiguous` means the request may have reached Meta but the outcome
    is unknown (timeout after dispatch, 2xx without id, malformed success) — recorded as
    duplicate risk, never as delivered or as not delivered."""
    sent: bool
    wamid: Optional[str]
    error: Optional[str]
    ambiguous: bool = False

    def __bool__(self) -> bool:
        return self.sent


class BookingTokenError(Exception):
    pass


class BookingSlotConflictError(Exception):
    """Raised when the revalidated slot is no longer available."""
    def __init__(self, refreshed_data: dict):
        self.refreshed_data = refreshed_data


# ── Crypto helpers (Meta Flows Data Exchange, Data API 3.0) ───────────────────

def load_private_key_pem() -> bytes | None:
    """Return raw PEM bytes for the booking Flow private key, or None if not configured."""
    path = os.environ.get("FLOW_BOOKING_PRIVATE_KEY_PATH", "").strip()
    if not path:
        return None
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError as exc:
        logger.warning("BOOKING_FLOW_CRYPTO cannot read private key: %s", exc)
        return None


def decrypt_flow_request(body: dict) -> tuple[dict, bytes, bytes]:
    """Decrypt an encrypted Meta Flow Data Exchange request.

    Returns (decrypted_payload, aes_key, iv).

    Raises ValueError on any crypto failure.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
    except ImportError as exc:
        raise ValueError(f"cryptography package required: {exc}") from exc

    private_key_pem = load_private_key_pem()
    if not private_key_pem:
        raise ValueError("FLOW_BOOKING_PRIVATE_KEY_PATH not configured")

    try:
        encrypted_aes_key = base64.b64decode(body["encrypted_aes_key"])
        encrypted_flow_data = base64.b64decode(body["encrypted_flow_data"])
        iv = base64.b64decode(body["initial_vector"])
    except (KeyError, Exception) as exc:
        raise ValueError(f"Malformed encrypted request: {exc}") from exc

    try:
        private_key = load_pem_private_key(private_key_pem, password=None)
        aes_key = private_key.decrypt(
            encrypted_aes_key,
            asym_padding.OAEP(
                mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    except Exception as exc:
        raise ValueError(f"RSA decryption failed: {exc}") from exc

    try:
        aesgcm = AESGCM(aes_key)
        decrypted_bytes = aesgcm.decrypt(iv, encrypted_flow_data, None)
        payload = json.loads(decrypted_bytes)
    except Exception as exc:
        raise ValueError(f"AES-GCM decryption failed: {exc}") from exc

    return payload, aes_key, iv


def encrypt_flow_response(response: dict, aes_key: bytes, iv: bytes) -> bytes:
    """Encrypt a Flow response dict using AES-128-GCM with the IV flipped."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise ValueError(f"cryptography package required: {exc}") from exc

    flipped_iv = bytes([b ^ 0xFF for b in iv])
    aesgcm = AESGCM(aes_key)
    response_bytes = json.dumps(response, ensure_ascii=False).encode("utf-8")
    return aesgcm.encrypt(flipped_iv, response_bytes, None)


# ── Token helpers ─────────────────────────────────────────────────────────────

def make_booking_token(thread_id: int) -> str:
    """Create a new opaque booking token embedding thread_id and current timestamp."""
    nonce = secrets.token_hex(8)
    return f"{thread_id}-{int(_time.time())}-{nonce}"


def parse_booking_token(token: str) -> tuple[int, int]:
    """Extract (thread_id, issued_at) from a token. Raises BookingTokenError on malformed."""
    if not token or not isinstance(token, str):
        raise BookingTokenError("missing token")
    parts = token.split("-")
    if len(parts) < 2:
        raise BookingTokenError("malformed token")
    try:
        thread_id = int(parts[0])
        issued_at = int(parts[1])
        return thread_id, issued_at
    except (ValueError, IndexError) as exc:
        raise BookingTokenError(f"malformed token: {exc}") from exc


# ── Date/time formatting ──────────────────────────────────────────────────────

def _date_title(d: date) -> str:
    """e.g. 'lunes 1 de septiembre'"""
    return f"{_DAY_NAMES[d.weekday()]} {d.day} de {_MONTH_NAMES[d.month]}"


def _format_appointment_summary(d: date, t: time) -> str:
    return f"{_date_title(d)} a las {t.strftime('%H:%M')}"


def _mask_phone(wa_id: str | None) -> str:
    """Last four digits only. A full number has no business in a screen we also log."""
    digits = "".join(ch for ch in (wa_id or "") if ch.isdigit())
    return f"…{digits[-4:]}" if len(digits) >= 4 else ""


def _lead_display_name(lead) -> str:
    """Customer name from the Lead, or empty.

    L4.7W5-GATE-A: `nombre`/`apellido` are business identity — an operator typed them, or a
    Flow submission persisted them. WhatsApp `display_name` is a profile label the customer
    can set to anything, so it is deliberately NOT elevated into a verified name here. Empty
    is an honest answer; an invented one is not.
    """
    first = (getattr(lead, "nombre", None) or "").strip()
    last = (getattr(lead, "apellido", None) or "").strip()
    return " ".join(p for p in (first, last) if p)


def build_booking_receipt_message(
    name: str | None, date_human: str | None, time_str: str | None
) -> str:
    """The one customer-facing acknowledgement of a completed Booking Flow.

    L4.7W5-F3. The complete Wild booked successfully and then said nothing: WhatsApp showed
    "Formulario completado" and the conversation stopped dead. The wording below already
    existed inside ConversationEngine._process_flow_response, but that path no longer runs
    for the endpoint-backed Flow — the booking is written by handle_confirm_booking, which
    sets needs_human=True, and CE's human-takeover guard then returns skipped_human before
    any reply is composed.

    So this is the SAME copy, moved to where the booking actually happens, and imported back
    by CE so the two paths can never drift into competing wording.

    Deliberately says "solicitud" and "te confirma el turno", never "turno confirmado":
    appointment_approval_status is PENDING at this moment and an operator still has to
    approve it. Telling a customer their appointment is confirmed when it is not would be a
    false business fact, which is the class of defect this project keeps closing.
    """
    if name and date_human and time_str:
        opener = f"¡Listo, {name}! Recibimos tu solicitud para el {date_human} a las {time_str} 🎉"
    elif name:
        opener = f"¡Listo, {name}! Recibimos tu solicitud 🎉"
    else:
        opener = "¡Listo! Recibimos tu solicitud 🎉"
    return f"{opener}\n\nUn asesor va a revisar los datos y te confirma el turno a la brevedad."


def _format_customer_summary(name: str, phone: str) -> str:
    parts = [p for p in [name, phone] if p]
    return " · ".join(parts) if parts else ""


# ── Core service ──────────────────────────────────────────────────────────────

class BookingFlowService:
    """Handles all server-side logic for the RideCheck Booking Meta Flow."""

    def __init__(self, db: Session):
        self.db = db
        self._sched = ScheduleService(db)
        # The SAME pricing authority the conversation used to produce the accepted quote.
        # Booking must not own a second one: a booking that prices itself is a booking that
        # can disagree with what the customer was told.
        self._pricing = PricingService(repository=PricingRepository())
        # SB2A: the one booking authority. Every booking this service makes goes through it.
        self._authority = BookingAuthority(db)

    # ── Context resolution ────────────────────────────────────────────────────

    def resolve_context(self, booking_token: str) -> BookingContext:
        """Validate a booking token and return the active context.

        Raises BookingTokenError for any invalid/stale/tampered condition.
        """
        try:
            thread_id, issued_at = parse_booking_token(booking_token)
        except BookingTokenError:
            raise

        now_ts = int(_time.time())
        if now_ts - issued_at > TOKEN_MAX_AGE_SECONDS:
            raise BookingTokenError("token expired")

        # Load thread state and verify token matches
        state = self.db.execute(
            select(WhatsAppThreadState).where(WhatsAppThreadState.thread_id == thread_id)
        ).scalar_one_or_none()
        if state is None or state.flow_booking_token != booking_token:
            raise BookingTokenError("token invalid or already consumed")

        # Load thread
        thread = self.db.get(WhatsAppThread, thread_id)
        if thread is None:
            raise BookingTokenError("thread not found")

        # Load contact
        contact = self.db.get(WhatsAppContact, thread.contact_id)
        if contact is None:
            raise BookingTokenError("contact not found")

        # Load lead
        lead_id = thread.lead_id
        lead = self.db.get(Lead, lead_id) if lead_id else None
        if lead is None:
            raise BookingTokenError("lead not found")

        # Load active candidate (current focus)
        candidate = self._load_focus_candidate(thread_id, state)

        # Build summaries
        vehicle_summary = self._vehicle_summary(candidate)
        zone_group, zone_detail = self._location_from_candidate(candidate, state)
        location_summary = self._location_summary(zone_group, zone_detail, candidate)

        return BookingContext(
            thread=thread,
            contact=contact,
            lead=lead,
            state=state,
            candidate=candidate,
            vehicle_summary=vehicle_summary,
            location_summary=location_summary,
            zone_group=zone_group,
            zone_detail=zone_detail,
            booking_token=booking_token,
        )

    def _load_focus_candidate(
        self, thread_id: int, state: WhatsAppThreadState
    ) -> Optional[WhatsAppThreadCandidate]:
        """Return the most-recent candidate for the CURRENT active cycle only.

        Uses the same cycle watermark (current_cycle_started_at) that CE sets in
        _execute_cycle_reset(), so Booking Flow cannot surface a candidate from a
        previous Revision cycle.  When no watermark exists (first cycle), all
        candidates for the thread are eligible.
        """
        q = (
            select(WhatsAppThreadCandidate)
            .where(WhatsAppThreadCandidate.thread_id == thread_id)
        )
        cycle_start = getattr(state, "current_cycle_started_at", None)
        if cycle_start is not None:
            q = q.where(WhatsAppThreadCandidate.created_at >= cycle_start)
        q = q.order_by(WhatsAppThreadCandidate.updated_at.desc()).limit(1)
        rows = self.db.execute(q).scalars().all()
        return rows[0] if rows else None

    @staticmethod
    def _vehicle_summary(candidate: Optional[WhatsAppThreadCandidate]) -> str:
        if candidate is None:
            return ""
        parts = [
            candidate.marca or "",
            candidate.modelo or "",
            str(candidate.anio) if candidate.anio else "",
        ]
        return " ".join(p for p in parts if p).strip()

    @staticmethod
    def _location_from_candidate(
        candidate: Optional[WhatsAppThreadCandidate],
        state: WhatsAppThreadState,
    ) -> tuple[Optional[str], Optional[str]]:
        """Canonical inspection location — the same hierarchy ConversationEngine uses.

        L4.7W5-F4. This previously returned the candidate's zones whenever a candidate
        existed, *even when both were NULL*, and only consulted thread state when there was
        no candidate at all. In the live Wild the candidate existed with both zones NULL
        while thread state held CABA / Paternal — the very location CE had just quoted
        $150.000 from — so the booking was written with no zone, and PricingService could
        not price it. The customer was told a number that exists nowhere in the CRM.

        CE's `_get_active_inspection_location` had the correct rule all along: a candidate is
        authoritative only when it actually HAS a location, and a missing half is filled from
        state. Two near-duplicate implementations, and booking used the weaker one.

        Customer origin is never used — only the candidate's and the thread's inspection
        location, which is where the vehicle is.
        """
        if candidate is not None:
            cand_group = candidate.zone_group or None
            cand_detail = candidate.zone_detail or None
            if cand_group or cand_detail:
                return (cand_group or state.home_zone_group,
                        cand_detail or state.home_zone_detail)
        return state.home_zone_group, state.home_zone_detail

    @staticmethod
    def _location_summary(
        zone_group: Optional[str],
        zone_detail: Optional[str],
        candidate: Optional[WhatsAppThreadCandidate],
    ) -> str:
        parts = []
        if zone_detail:
            parts.append(zone_detail)
        elif zone_group:
            parts.append(zone_group)
        if candidate and candidate.direccion_texto:
            parts.append(candidate.direccion_texto)
        return ", ".join(parts) if parts else (zone_group or "")

    # ── Dynamic date/slot logic ───────────────────────────────────────────────

    def _available_dates(self, zone_group: Optional[str]) -> list[dict]:
        """Return date items for APPOINTMENT screen (14-day horizon, slots > 0)."""
        today = date.today()
        items: list[dict] = []
        # L4.7W5-F5: range starts at 0 so TODAY can be offered when it still has capacity.
        # _slots_for_date asks ScheduleService, which applies the same-day now-cutoff, so a
        # today with nothing left simply yields no items and is skipped.
        for delta in range(0, BOOKING_HORIZON_DAYS + 1):
            d = today + timedelta(days=delta)
            payload = ScheduleCheckIn(
                address="-",  # zone_group drives availability; address is not used for slot lookup
                preferred_day=d,
                preferred_time=time(9, 0),
                zone_group=zone_group,
                is_holiday=False,
            )
            slots_out = self._sched.list_slots(payload)
            if slots_out.slots:
                items.append({"id": d.isoformat(), "title": _date_title(d)})
        return items

    def _slots_for_date(self, d: date, zone_group: Optional[str]) -> list[dict]:
        """Return time items for APPOINTMENT screen for a given date."""
        payload = ScheduleCheckIn(
            address="-",  # zone_group drives availability; address is not used for slot lookup
            preferred_day=d,
            preferred_time=time(9, 0),
            zone_group=zone_group,
            is_holiday=False,
        )
        slots_out = self._sched.list_slots(payload)
        return [{"id": t, "title": t} for t in slots_out.slots]

    # ── Screen builders ───────────────────────────────────────────────────────

    def _appointment_screen_data(
        self,
        ctx: BookingContext,
        selected_date_str: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> dict:
        date_items = self._available_dates(ctx.zone_group)
        time_items: list[dict] = []
        is_date_enabled = bool(date_items)
        is_time_enabled = False

        # ── L4.7W5-F4: do not ask again for a slot already agreed in conversation ──
        #
        # In the live Wild the customer negotiated Monday 17:00 in chat and the Flow then
        # made them pick the day and the time over again from full lists.
        #
        # The published Flow (id 28104222025943520, v7.3) declares its Dropdowns WITHOUT an
        # `init-value`, so nothing the back end sends can preselect them — that needs a Flow
        # republish on Meta, which is an owner action. What IS available to us is the option
        # list itself: when an exact slot is already agreed AND still free, the picker is
        # narrowed to that one date and that one time. The customer confirms rather than
        # re-chooses, the date→time round trip disappears, and a wrong slot cannot be picked.
        #
        # Only when a slot is genuinely agreed. A day-only or NEXT_AVAILABLE dispatch keeps
        # the full picker, because there the choice is still real (Part 9).
        agreed_day = (getattr(ctx.state, "preferred_day", None) or "").strip()
        agreed_time = (getattr(ctx.state, "preferred_time", None) or "").strip()
        if agreed_day and agreed_time and not selected_date_str:
            try:
                agreed_date = date.fromisoformat(agreed_day)
            except (ValueError, TypeError):
                agreed_date = None
            if agreed_date is not None:
                still_free = [i for i in self._slots_for_date(agreed_date, ctx.zone_group)
                              if i.get("id") == agreed_time]
                if still_free:
                    # Narrowing is a UX convenience only — confirm_booking revalidates the
                    # slot regardless, so a stale agreement is still caught there.
                    # GATE A: both selections are IDs that exist in their own data-source
                    # above, which is what makes the Flow render them as chosen.
                    return {
                        "booking_token": ctx.booking_token,
                        "vehicle_summary": ctx.vehicle_summary,
                        "location_summary": ctx.location_summary,
                        "date": [{"id": agreed_day, "title": _date_title(agreed_date)}],
                        "is_date_enabled": True,
                        "time": still_free,
                        "is_time_enabled": True,
                        "selected_date": agreed_day,
                        "selected_time": agreed_time,
                        **self._identity_fields(ctx),
                    }
                logger.info(
                    "BOOKING_FLOW agreed slot %s %s no longer free — full picker offered",
                    agreed_day, agreed_time,
                )

        if selected_date_str:
            try:
                selected_date = date.fromisoformat(selected_date_str)
                time_items = self._slots_for_date(selected_date, ctx.zone_group)
                is_time_enabled = bool(time_items)
            except (ValueError, TypeError):
                pass

        # GATE A: a selection is offered ONLY when it exists in its own data-source. A day
        # the customer named is selectable; a time they never chose is not, and preselecting
        # the earliest would be us deciding for them.
        selected_date = selected_date_str if any(
            i.get("id") == selected_date_str for i in date_items) else ""
        data: dict = {
            "booking_token": ctx.booking_token,
            "vehicle_summary": ctx.vehicle_summary,
            "location_summary": ctx.location_summary,
            "date": date_items,
            "is_date_enabled": is_date_enabled,
            "time": time_items,
            "is_time_enabled": is_time_enabled,
            "selected_date": selected_date or "",
            "selected_time": "",
            **self._identity_fields(ctx),
        }
        if error_message:
            data["slot_conflict_message"] = error_message
        return data

    def _identity_fields(self, ctx: BookingContext) -> dict:
        """Canonical identity carried to DETAILS through the navigate payload.

        APPOINTMENT → DETAILS is client-side `navigate`: the back end is never called
        between those screens, so anything DETAILS needs has to leave from here.

        `contact_phone` is `wa_id` — the transport identity WhatsApp already proved. It is
        carried as data, never as an editable field, and it is re-asserted server-side at
        prepare_summary and confirm_booking so the UI is not the thing enforcing it.
        """
        return {
            "customer_name": _lead_display_name(ctx.lead),
            "contact_phone": ctx.contact.wa_id or "",
            "contact_phone_display": _mask_phone(ctx.contact.wa_id),
        }

    def _summary_screen_data(
        self,
        ctx: BookingContext,
        date_str: str,
        time_str: str,
        name: str,
        phone: str,
        email: str,
        inspection_address: str,
        seller_name: str,
        seller_phone: str,
        listing_url: str,
    ) -> dict:
        try:
            d = date.fromisoformat(date_str)
            t = time.fromisoformat(time_str)
            appointment_summary = _format_appointment_summary(d, t)
        except (ValueError, TypeError):
            appointment_summary = f"{date_str} {time_str}".strip()

        customer_summary = _format_customer_summary(name, phone)
        return {
            "booking_token": ctx.booking_token,
            "appointment_summary": appointment_summary,
            "customer_summary": customer_summary,
            "date": date_str,
            "time": time_str,
            "name": name,
            "phone": phone,
            "email": email,
            "inspection_address": inspection_address,
            "seller_name": seller_name,
            "seller_phone": seller_phone,
            "listing_url": listing_url,
        }

    # ── Public handlers ───────────────────────────────────────────────────────

    def handle_init(self, booking_token: str) -> dict:
        """Handle INIT action: return initial APPOINTMENT screen."""
        ctx = self.resolve_context(booking_token)
        _log_event(self.db, ctx.thread.id, "BOOKING_FLOW_CONTEXT_CREATED", booking_token=booking_token)
        return {
            "version": FLOW_VERSION,
            "screen": "APPOINTMENT",
            "data": self._appointment_screen_data(ctx),
        }

    def handle_date_selected(self, booking_token: str, selected_date: str) -> dict:
        """Handle date_selected trigger: return slots for the chosen date."""
        ctx = self.resolve_context(booking_token)
        _log_event(
            self.db, ctx.thread.id, "BOOKING_FLOW_DATE_SELECTED",
            booking_token=booking_token, extra={"date": selected_date},
        )
        return {
            "version": FLOW_VERSION,
            "screen": "APPOINTMENT",
            "data": self._appointment_screen_data(ctx, selected_date_str=selected_date),
        }

    def handle_prepare_summary(self, booking_token: str, data: dict) -> dict:
        """Handle prepare_summary: validate inputs, produce SUMMARY screen data.

        Does NOT create a booking.
        """
        ctx = self.resolve_context(booking_token)

        date_str = str(data.get("date", "")).strip()
        time_str = str(data.get("time", "")).strip()

        # Validate date/time shapes
        try:
            date.fromisoformat(date_str)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid date: {exc}") from exc
        try:
            time.fromisoformat(time_str)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid time: {exc}") from exc

        name = str(data.get("name", "")).strip()
        # GATE A: the phone is not negotiable from the Flow. wa_id is the identity WhatsApp
        # proved; a value arriving in the payload is at best a copy and at worst a typo that
        # would become the number an operator calls. Enforced here, server-side, rather than
        # trusting the screen not to offer an edit.
        phone = (ctx.contact.wa_id or "").strip()
        if not name:
            raise ValueError("name is required")
        if not phone:
            raise ValueError("phone is required")

        email = str(data.get("email", "")).strip()
        inspection_address = str(data.get("inspection_address", "")).strip()
        seller_name = str(data.get("seller_name", "")).strip()
        seller_phone = str(data.get("seller_phone", "")).strip()
        listing_url = str(data.get("listing_url", "")).strip()

        _log_event(
            self.db, ctx.thread.id, "BOOKING_FLOW_SUMMARY_PREPARED",
            booking_token=booking_token,
            extra={"date": date_str, "time": time_str},
        )

        return {
            "version": FLOW_VERSION,
            "screen": "SUMMARY",
            "data": self._summary_screen_data(
                ctx, date_str, time_str, name, phone, email,
                inspection_address, seller_name, seller_phone, listing_url,
            ),
        }

    def handle_confirm_booking(self, booking_token: str, data: dict) -> dict:
        """Handle confirm_booking through the canonical booking authority (SB2A).

        Sequence:
        1. idempotent replay — a submission that already committed returns its ORIGINAL
           SUCCESS (a Meta retry, a lost response, a double tap); its acknowledgement is
           recovered only if it was never sent, otherwise the replay is silent
        2. resolve token → active context; parse the selected date/time
        3. `BookingAuthority.execute`: fail-closed (professional, date) advisory lock,
           calendar exceptions, overlap pre-check, ScheduleService.check() revalidation
           under the lock, then Revision + ThreadRevision + lead/state + token consumption +
           ledger in ONE commit; the SB1 exclusion constraint is the final authority
        4. a refused booking raises BookingSlotConflictError with refreshed data — no
           booking, no acknowledgement
        5. only after the commit: the single acknowledgement, recorded in the ledger

        Exactly-once is the ledger's UNIQUE idempotency key, derived from this Flow
        submission's booking token (never stored raw): a retry cannot book twice, and a
        BookingTokenError can no longer turn a booked customer's retry into an error screen.
        """
        key = flow_idempotency_key(booking_token)
        prior = self._authority.lookup(key)
        if prior is not None:
            _log_event(self.db, 0, "BOOKING_IDEMPOTENT_REPLAY", booking_token=booking_token,
                       extra={"crm_rev_id": prior.revision_id,
                              "thread_rev_id": prior.thread_revision_id,
                              "ack_status": prior.ack_status})
            self._authority.note_replay(key)
            self._recover_acknowledgement(key, prior)
            return self._success_response(booking_token)

        ctx = self.resolve_context(booking_token)

        date_str = str(data.get("date", "")).strip()
        time_str = str(data.get("time", "")).strip()

        try:
            selected_date = date.fromisoformat(date_str)
            selected_time = time.fromisoformat(time_str)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid date/time: {exc}") from exc

        name = str(data.get("name", "")).strip()
        # GATE A: the phone is not negotiable from the Flow. wa_id is the identity WhatsApp
        # proved; a value arriving in the payload is at best a copy and at worst a typo that
        # would become the number an operator calls. Enforced here, server-side, rather than
        # trusting the screen not to offer an edit.
        phone = (ctx.contact.wa_id or "").strip()
        email = str(data.get("email", "")).strip()
        inspection_address = str(data.get("inspection_address", "")).strip()
        seller_name = str(data.get("seller_name", "")).strip()
        seller_phone = str(data.get("seller_phone", "")).strip()
        listing_url = str(data.get("listing_url", "")).strip()

        candidate = ctx.candidate
        zone_group = ctx.zone_group
        zone_detail = ctx.zone_detail

        # Revalidation is the AUTHORITY's: it runs ScheduleService.check itself, inside its
        # transaction and under its locks, from these typed inputs (SB2A-R3 — no callback).
        availability = AvailabilityRevalidation(
            address=inspection_address or "-", zone_group=ctx.zone_group, is_holiday=False)

        # L4.7W4-F4: a booking with no canonical inspection location cannot be priced, and an
        # appointment in the CRM with no money attached is a commercial defect — the operator
        # sees a job and no figure, and the amount the customer was quoted lives nowhere.
        # Better to send the customer back one screen than to write that record.
        if not zone_group and not zone_detail:
            _log_event(
                self.db, ctx.thread.id, "BOOKING_REFUSED_NO_CANONICAL_LOCATION",
                booking_token=booking_token,
                extra={"date": date_str, "time": time_str,
                       "candidate_id": getattr(candidate, "id", None)},
            )
            logger.error(
                "BOOKING_REFUSED_NO_CANONICAL_LOCATION thread_id=%s — refusing to create "
                "an unpriced booking", ctx.thread.id,
            )
            raise BookingSlotConflictError({
                "version": FLOW_VERSION,
                "screen": "APPOINTMENT",
                "data": {
                    **self._appointment_screen_data(ctx, selected_date_str=date_str),
                    "slot_conflict_message": (
                        "Necesitamos confirmar la zona donde está el vehículo antes de "
                        "reservar el turno. Un asesor se contacta a la brevedad."
                    ),
                },
            })

        # SB2A-R3: the associated writes are DATA the booking authority executes in its own
        # transaction — the booked ThreadRevision (status and slot derived from the booking),
        # the price, the lead and the thread state with the consumed token. No code of ours runs
        # inside the booking transaction.
        #
        # L4.7W4-F3 (preserved): the quote has no stored identity — it is a deterministic
        # function of (tipo_vehiculo, zone_group, zone_detail) over the pricing catalog, and the
        # Revision persists all three. The price is stamped only if the Revision's inputs equal
        # the live focus candidate's; otherwise it is left unset (BOOKING_PRICE_INPUT_MISMATCH)
        # rather than written from mismatched commercial inputs.
        name_parts = name.split()
        attachments = BookingAttachmentPlan(
            thread_revision=ThreadRevisionAttachment(
                thread_id=ctx.thread.id,
                candidate_id=candidate.id if candidate else None,
                buyer_name=name or None,
                buyer_phone=phone or None,
                buyer_email=email or None,
                seller_type=None,
                seller_name=seller_name or None,
                address=inspection_address or None,
                tipo_vehiculo=candidate.tipo_vehiculo if candidate else None,
                marca=candidate.marca if candidate else None,
                modelo=candidate.modelo if candidate else None,
                anio=candidate.anio if candidate else None,
                publication_url=listing_url or None,
                zone_group=zone_group,
                request_approval=True,
            ),
            price=PriceRecalculation(require_inputs=(
                candidate.tipo_vehiculo, zone_group, zone_detail)) if candidate else None,
            # SB2A-R4 (R3A-03): the submitted person is ONE identity bundle — admitted whole,
            # used to fill gaps only when consistent, or not applied at all (CONFLICT).
            lead=LeadUpdate(
                estado="COORDINAR_DISPONIBILIDAD", flag="ACEPTADO", necesita_humano=True,
                identity=LeadIdentity(
                    nombre=name_parts[0],
                    apellido=" ".join(name_parts[1:]) or None,
                    email=email or None) if name_parts else None,
            ),
            thread_state=ThreadStateUpdate(
                link_current_revision=True, last_stage=STAGE_BOOKED, needs_human=True,
                consume_flow_token=True),
        )
        if candidate is None:
            _log_event(self.db, ctx.thread.id, "BOOKING_PRICE_INPUT_MISMATCH",
                       booking_token=booking_token, extra={"reason": "no_focus_candidate"})

        command = BookingCommand(
            operation=BookingOperation.CREATE,
            source=BookingSource.WHATSAPP_FLOW,
            idempotency_key=key,
            lead_id=ctx.lead.id,
            thread_id=ctx.thread.id,
            requested_date=selected_date,
            requested_time=selected_time,
            correlation_id=key[:64],
            tracks_acknowledgement=True,
            revision_fields={
                "tipo_vehiculo": candidate.tipo_vehiculo if candidate else None,
                "marca": candidate.marca if candidate else None,
                "modelo": candidate.modelo if candidate else None,
                "anio": candidate.anio if candidate else None,
                "zone_group": zone_group,
                "zone_detail": zone_detail,
                "direccion_texto": inspection_address
                or (candidate.direccion_texto if candidate else None),
                "vendedor_tipo": None,
                "tipo_vendedor": None,
            },
        )
        result = self._authority.execute(command, availability=availability,
                                         attachments=attachments)
        if result.reason == "SCHEDULE_REVALIDATION":
            _log_event(self.db, ctx.thread.id, "BOOKING_REVALIDATION_FAIL",
                       booking_token=booking_token, extra={"date": date_str, "time": time_str})

        if result.outcome is BookingOutcome.IDEMPOTENT_REPLAY:
            # A concurrent duplicate of this very submission committed first.
            self._recover_acknowledgement(key, result)
            return self._success_response(booking_token)

        if result.outcome is not BookingOutcome.COMMITTED:
            _log_event(
                self.db, ctx.thread.id, "BOOKING_REFUSED",
                booking_token=booking_token,
                extra={"outcome": result.outcome.value, "reason": result.reason,
                       "date": date_str, "time": time_str},
            )
            # Refreshed slots for conflict recovery. Nothing was booked and nothing will be
            # acknowledged; the customer picks again from what is free now.
            raise BookingSlotConflictError({
                "version": FLOW_VERSION,
                "screen": "APPOINTMENT",
                "data": {
                    **self._appointment_screen_data(ctx, selected_date_str=date_str),
                    # the time they picked is exactly what became unavailable
                    "selected_time": "",
                    "slot_conflict_message": _FLOW_REFUSAL_MESSAGES.get(
                        result.outcome, _FLOW_REFUSAL_MESSAGES[BookingOutcome.SLOT_CONFLICT]),
                },
            })

        _log_event(
            self.db, ctx.thread.id, "BOOKING_CREATED",
            booking_token=booking_token,
            extra={
                "thread_rev_id": result.thread_revision_id,
                "crm_rev_id": result.revision_id,
                "date": date_str,
                "time": time_str,
                "profesional_id": result.profesional_id,
                "lock_mode": result.lock_mode,
            },
        )

        logger.info(
            "BOOKING_CREATED thread_id=%s thread_rev=%s crm_rev=%s date=%s time=%s",
            ctx.thread.id, result.thread_revision_id, result.revision_id, date_str, time_str,
        )
        if result.identity_outcome == "CONFLICT":
            # SB2A-R4: the booking stands on the canonical lead; the submitted contact details
            # disagreed with it and were NOT applied. Recorded for operator review.
            _log_event(self.db, ctx.thread.id, "BOOKING_IDENTITY_CONFLICT",
                       booking_token=booking_token,
                       extra={"crm_rev_id": result.revision_id, "lead_id": ctx.lead.id})

        # The booking is committed above. The acknowledgement is best-effort BY DESIGN:
        # a delivery problem must never roll back or cast doubt on a booking that exists.
        # Its state is a bounded lease in the ledger, apart from the commit, so a replay can
        # recover it — including after a crash mid-send (SB2A-R2).
        claim = self._authority.claim_acknowledgement(key)
        if claim is not None:
            outcome = self._send_booking_receipt(ctx, name=name, selected_date=selected_date,
                                                 selected_time=selected_time)
            self._record_receipt(claim, outcome)

        # Return Flow SUCCESS completion
        return self._success_response(booking_token)

    @staticmethod
    def _success_response(booking_token: str) -> dict:
        return {
            "version": FLOW_VERSION,
            "screen": "SUCCESS",
            "data": {
                "extension_message_response": {
                    "params": {
                        "flow_token": booking_token,
                    }
                }
            },
        }

    def _record_receipt(self, claim, outcome) -> None:
        """Record one receipt attempt under its lease (WAMID or error category)."""
        recorded = self._authority.record_acknowledgement(
            claim, sent=bool(outcome), transport_id=getattr(outcome, "wamid", None),
            error=getattr(outcome, "error", None) or (None if outcome else "NOT_SENT"),
            ambiguous=bool(getattr(outcome, "ambiguous", False)))
        if not recorded:
            logger.warning("BOOKING_RECEIPT outcome not recorded: lease was reclaimed "
                           "attempt=%s", claim.attempt)
        if claim.duplicate_risk:
            logger.warning("BOOKING_RECEIPT attempt %s reclaimed an expired lease — the "
                           "earlier attempt may have reached Meta; possible duplicate",
                           claim.attempt)

    def _recover_acknowledgement(self, key: str, prior) -> None:
        """Acknowledgement recovery for a replayed, already-committed booking.

        SENT → silent. PENDING, FAILED or an EXPIRED SENDING lease → this caller claims it
        atomically and sends once; an active lease or a concurrent replay cannot also claim.
        A reclaimed lease carries `duplicate_risk`: the dead attempt may already have reached
        Meta, so exactly-once delivery is not claimed.
        """
        if prior.ack_status in ("SENT", "NOT_APPLICABLE"):
            return
        claim = self._authority.claim_acknowledgement(key)
        if claim is None:
            return
        rev = self.db.get(Revision, prior.revision_id) if prior.revision_id else None
        thread_rev = self.db.get(ThreadRevision, prior.thread_revision_id) \
            if prior.thread_revision_id else None
        thread = self.db.get(WhatsAppThread, thread_rev.thread_id) if thread_rev else None
        contact = self.db.get(WhatsAppContact, thread.contact_id) if thread else None
        if rev is not None and thread is not None and contact is not None:
            ctx = BookingContext(thread=thread, contact=contact, lead=None, state=None,
                                 candidate=None, vehicle_summary="", location_summary="",
                                 zone_group=None, zone_detail=None, booking_token="")
            outcome = self._send_booking_receipt(
                ctx, name=thread_rev.buyer_name or "", selected_date=rev.turno_fecha,
                selected_time=rev.turno_hora)
            self._record_receipt(claim, outcome)
        else:
            self._authority.record_acknowledgement(claim, sent=False, error="CONTEXT_MISSING")

    def _send_booking_receipt(
        self, ctx: BookingContext, *, name: str, selected_date: date, selected_time: time
    ) -> "ReceiptOutcome":
        """Send the single canonical booking acknowledgement.

        Exactly-once comes from the booking ledger, not from a flag: a Meta retry or a
        duplicate confirm_booking replays the committed result (it no longer reaches
        resolve_context and its BookingTokenError), and the receipt is sent only by the caller
        that atomically claims the ledger's acknowledgement (PENDING/FAILED → SENDING). The
        outbound gate's dedup window is a second line of defence, not the mechanism.

        Returns a ReceiptOutcome (SB2A-R3 classification):
          * definitive pre-send refusal (gate) or failure before dispatch → not sent
          * definitive Meta rejection (4xx)                              → not sent
          * success with a non-empty WAMID                               → sent
          * ambiguous: network/timeout after dispatch, 2xx without a usable id, malformed
            success, Meta 5xx                                            → AMBIGUOUS
        Exactly-once external delivery is never claimed.

        Routed through OutboundSafetyGate on the registered BOOKING_FLOW path — the same
        path that dispatched the Flow — so the acknowledgement is attributed to the booking
        that caused it and can never appear as MANUAL_CRM or unattributed.
        """
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo

        from ..ui.whatsapp_ui import MetaSendError, _send_whatsapp_cloud_text
        from .outbound_guard import OutboundBlockedError
        from .outbound_path_registry import OutboundPathId, get_deployment_id
        from .outbound_safety_gate import GateOutcome, OutboundSafetyGate

        text = BOOKING_FLOW_RECEIPT_TEXT          # owner-approved copy (SB2A-R3)
        # SB2A-R4 (R3A-05): failures KNOWN to precede any transport attempt are definitive —
        # no gate record, no network call, no duplicate risk, retryable once fixed.
        config_error = _receipt_pre_dispatch_error(ctx)
        if config_error is not None:
            logger.error("BOOKING_RECEIPT not attempted thread_id=%s: %s", ctx.thread.id, config_error)
            return ReceiptOutcome(False, None, config_error)
        try:
            gate = OutboundSafetyGate(self.db)
            result = gate.attempt(
                wa_id=ctx.contact.wa_id, thread_id=ctx.thread.id, text=text,
                now=_dt.now(ZoneInfo("America/Argentina/Buenos_Aires")),
                path_id=OutboundPathId.BOOKING_FLOW.value,
                deployment_id=get_deployment_id(),
            )
            if result.outcome != GateOutcome.ALLOWED:
                logger.warning(
                    "BOOKING_RECEIPT not sent thread_id=%s outcome=%s — booking stands",
                    ctx.thread.id, result.outcome.value)
                return ReceiptOutcome(False, None, f"GATE_{result.outcome.value}"[:64])
            try:
                wa_message_id, _ = _send_whatsapp_cloud_text(
                    to_wa_id=ctx.contact.wa_id, text=text)
            except OutboundBlockedError:
                # raised by enforce_outbound_enabled, the transport's FIRST statement: nothing
                # left the process
                gate.mark_failed(result.message_id)
                return ReceiptOutcome(False, None, "PRE_DISPATCH_OUTBOUND_BLOCKED")
            except MetaSendError as exc:
                status = exc.http_status
                gate.mark_failed(result.message_id, meta_http_status=status,
                                 meta_error_payload=exc.to_payload())
                logger.error("BOOKING_RECEIPT Meta send failed thread_id=%s status=%s",
                             ctx.thread.id, status)
                if status is not None and 400 <= status < 500:
                    return ReceiptOutcome(False, None, f"META_REJECTED_{status}")
                if status is None:
                    return ReceiptOutcome(False, None, "AMBIGUOUS_NETWORK_OR_TIMEOUT",
                                          ambiguous=True)
                if 200 <= status < 300:
                    return ReceiptOutcome(False, None, "AMBIGUOUS_2XX_WITHOUT_MESSAGE_ID",
                                          ambiguous=True)
                return ReceiptOutcome(False, None, f"AMBIGUOUS_META_{status}", ambiguous=True)
            except Exception as exc:
                gate.mark_failed(result.message_id)
                logger.error("BOOKING_RECEIPT transport outcome unknown thread_id=%s: %s",
                             ctx.thread.id, type(exc).__name__)
                return ReceiptOutcome(False, None, "AMBIGUOUS_TRANSPORT_EXCEPTION",
                                      ambiguous=True)
            wamid = (wa_message_id or "").strip()
            if not wamid:
                return ReceiptOutcome(False, None, "AMBIGUOUS_SUCCESS_WITHOUT_WAMID",
                                      ambiguous=True)
            try:
                gate.mark_sent(result.message_id, wamid)
            except Exception:                    # bookkeeping failure ≠ delivery failure
                logger.exception("BOOKING_RECEIPT sent but gate bookkeeping failed")
            logger.info("BOOKING_RECEIPT sent thread_id=%s wamid=%s", ctx.thread.id, wamid)
            return ReceiptOutcome(True, wamid, None)
        except Exception as exc:
            # The booking is already committed; nothing here may undo it.
            logger.error("BOOKING_RECEIPT failed before dispatch thread_id=%s: %s",
                         ctx.thread.id, exc)
        return ReceiptOutcome(False, None, "PRE_SEND_FAILURE")

def _receipt_pre_dispatch_error(ctx) -> Optional[str]:
    """Local transport configuration that would make the send fail BEFORE the network
    boundary (mirrors `_send_whatsapp_cloud_text`'s own pre-network checks). None if a send
    could be attempted."""
    from ..settings import get_settings
    wa_id = ctx.contact.wa_id if ctx is not None and ctx.contact is not None else None
    if type(wa_id) is not str or not wa_id.strip():
        return "PRE_DISPATCH_CONFIG_DESTINATION_MISSING"
    settings = get_settings()
    token = (settings.whatsapp_token or "").strip()
    phone_number_id = (settings.whatsapp_phone_number_id or "").strip()
    if token and token.lower() != "dummy" and not phone_number_id:
        return "PRE_DISPATCH_CONFIG_PHONE_NUMBER_ID_MISSING"
    return None


# ── Observability helpers ─────────────────────────────────────────────────────

def _log_event(
    db: Session,
    thread_id: int,
    event_type: str,
    booking_token: str = "",
    extra: Optional[dict] = None,
) -> None:
    """Persist a booking lifecycle event. Uses AiEvent-compatible logging."""
    details = extra or {}
    if booking_token:
        details = {"booking_token_prefix": booking_token[:12] + "...", **details}
    logger.info(
        "BOOKING_EVENT type=%s thread_id=%s %s",
        event_type,
        thread_id,
        " ".join(f"{k}={v}" for k, v in details.items()),
    )
    # No PII beyond what already lands in application logs.
    # A future milestone can persist these to a dedicated booking_events table.


# ── Health-check response ─────────────────────────────────────────────────────

def health_response() -> dict:
    """Return the standard Meta Flow health-check response."""
    return {"version": FLOW_VERSION, "data": {"status": "active"}}
