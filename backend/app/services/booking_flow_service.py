"""M21.3-C-D — RideCheck Booking Flow backend service.

Handles the complete data exchange lifecycle for the RideCheck Booking Meta Flow
(version 7.3, Data API 3.0, Flow ID 28104222025943520):

  INIT          → APPOINTMENT screen (dynamic dates)
  date_selected → APPOINTMENT screen (dynamic time slots)
  prepare_summary → SUMMARY screen
  confirm_booking → atomic booking + SUCCESS

Convergence Phase 3: confirm_booking books through `BookingAuthority` only. After the
authority commit, the one success acknowledgement is sent through the booking ledger's lease
and the outbound safety gate (`booking_acknowledgement.acknowledge_committed_booking`).
"""
from __future__ import annotations

import base64
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
from .booking_authority import BookingOutcome

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
BOOKING_HORIZON_DAYS = 14
TOKEN_MAX_AGE_SECONDS = 7200        # 2-hour Flow session window
STAGE_BOOKED = "BOOKED"

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


class BookingTokenError(Exception):
    pass


class BookingSlotConflictError(Exception):
    """Raised when the revalidated slot is no longer available."""
    def __init__(self, refreshed_data: dict):
        self.refreshed_data = refreshed_data


class BookingAlreadyCompletedError(Exception):
    """G3-7R/S6. This token's offer already produced a committed booking.

    DELIBERATELY NOT a `BookingTokenError`. The architecture audit measured what happened
    when it was treated as one: `resolve_context` found the transport mirror cleared — which
    is what a successful booking does to it — raised "token invalid or already consumed",
    and the customer was shown *"Tu sesión de reserva expiró"* seconds after their booking
    had in fact succeeded. A replay is the most benign thing that can happen on this path,
    and it was being reported as the most alarming.

    So it is its own type, carrying the authoritative booking, and the route renders the
    completion screen from it: no second booking, no refusal counter, no false expiry, and
    no duplicate acknowledgement — the transport that created the booking already sent one.
    """
    def __init__(self, *, offer_id=None, thread_revision_id=None, revision_id=None,
                 thread_id=None):
        super().__init__("offer already produced a committed booking")
        self.offer_id = offer_id
        self.thread_revision_id = thread_revision_id
        self.revision_id = revision_id
        #: R2/RV-07. Carried so the COMMAND boundary can enter the canonical lock order
        #: and count the replay. `resolve_context` no longer does it: a resolver that
        #: commits can commit a caller's unrelated pending work.
        self.thread_id = thread_id


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

        # ── G3-7R/S6: is this a REPLAY of a booking that already committed? ──────
        # Asked BEFORE the mirror comparison below, because a committed booking CLEARS the
        # mirror — so the comparison cannot tell a replay from a tampered token, and it
        # reported both as an expired session. The offer row can tell them apart: ACCEPTED
        # with a recorded ThreadRevision is a replay and nothing else is.
        # R2/RV-07. `admit()` is a read and this method stays one: it DETECTS the replay
        # and reports it. Convergence Phase 3: `handle_confirm_booking` asks the booking
        # ledger first and counts a replay there; this detection only serves the screens.
        # G3-7R counted and committed here, which meant resolving a context could commit
        # whatever the caller had pending — a durable side effect in a method named for
        # resolution, reachable from `handle_init`, `handle_date_selected` and
        # `handle_prepare_summary`, none of which is a command.
        from .offer_lifecycle import OfferLifecycle as _OfferLifecycle
        _adm = _OfferLifecycle(self.db).admit(thread_id=thread_id, token=booking_token)
        if _adm.replay:
            logger.info(
                "BOOKING_REPLAY thread_id=%s offer_id=%s thread_rev=%s — returning the "
                "authoritative booking; nothing created, nothing refused",
                thread_id, _adm.offer_id, _adm.thread_revision_id)
            raise BookingAlreadyCompletedError(
                offer_id=_adm.offer_id, thread_revision_id=_adm.thread_revision_id,
                revision_id=_adm.revision_id, thread_id=thread_id)

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

        # G3-7R/S3: ONE candidate authority — the binding on the token's own offer.
        candidate = self._offer_bound_candidate(thread_id, state, booking_token)

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

    def _offer_bound_candidate(
        self, thread_id: int, state: WhatsAppThreadState, booking_token: str
    ) -> Optional[WhatsAppThreadCandidate]:
        """THE candidate for this Flow: the one bound into the offer that armed the token.

        G3-7R/S3. This replaced `_load_focus_candidate`, which answered the question
        "which vehicle is this?" with *the most recently updated candidate in the current
        cycle* — the focus pointer ignored, `None` never returned, and "newest updated row
        wins" as the tie-break. The ConversationEngine answered the same question from the
        focus pointer with a single-candidate fallback, and twice within one acceptance.
        The architecture audit enumerated four ways those two resolvers could disagree about
        the vehicle whose price the customer had already accepted.

        The offer's `candidate_id` is immutable, was recorded when the quote was computed,
        and is what `claim()` compares, so it is the one answer that cannot drift — and it
        is the SAME answer the conversation engine now uses, which is what makes the two
        transports equivalent rather than merely similar.

        None when the binding cannot be honoured: no offer for this token, no candidate on
        the offer, the row deleted, the row belonging to another thread, or the row outside
        the active cycle watermark (the same watermark this method used before, and the one
        `ConversationEngine._execute_cycle_reset` sets). None then refuses downstream — at
        `claim()` as `OFFER_CANDIDATE_CHANGED`, or at the no-canonical-location guard —
        which is the safe direction. There is no "newest row wins" fallback left.
        """
        from .offer_lifecycle import OfferLifecycle

        offer = OfferLifecycle(self.db).offer_by_token(booking_token)
        candidate_id = getattr(offer, "candidate_id", None) if offer is not None else None
        if candidate_id is None:
            logger.warning(
                "BOOKING_CANDIDATE_UNBOUND thread_id=%s — the token's offer names no "
                "candidate; nothing to book against", thread_id)
            return None
        candidate = self.db.get(WhatsAppThreadCandidate, int(candidate_id))
        if candidate is None or getattr(candidate, "thread_id", None) != thread_id:
            logger.warning(
                "BOOKING_CANDIDATE_UNAVAILABLE thread_id=%s candidate_id=%s — deleted or "
                "foreign", thread_id, candidate_id)
            return None
        # The same boundary guard the conversation engine applies, for the same reason and
        # with the same shape — which is what makes the two transports equivalent rather
        # than merely similar. It resolves nothing from the pointer; it refuses when an
        # explicit pointer contradicts the binding.
        focus_pointer = getattr(state, "current_focus_candidate_id", None)
        if focus_pointer is not None and int(focus_pointer) != int(candidate_id):
            logger.warning(
                "BOOKING_CANDIDATE_SUPERSEDED thread_id=%s focus=%s offer_bound=%s",
                thread_id, focus_pointer, candidate_id)
            return None
        cycle_start = getattr(state, "current_cycle_started_at", None)
        created_at = getattr(candidate, "created_at", None)
        if cycle_start is not None and created_at is not None and created_at < cycle_start:
            logger.warning(
                "BOOKING_CANDIDATE_OUT_OF_CYCLE thread_id=%s candidate_id=%s — predates "
                "the active cycle watermark", thread_id, candidate_id)
            return None
        return candidate

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
        _booking_event(ctx.thread.id, "BOOKING_FLOW_CONTEXT_CREATED", booking_token=booking_token)
        return {
            "version": FLOW_VERSION,
            "screen": "APPOINTMENT",
            "data": self._appointment_screen_data(ctx),
        }

    def handle_date_selected(self, booking_token: str, selected_date: str) -> dict:
        """Handle date_selected trigger: return slots for the chosen date."""
        ctx = self.resolve_context(booking_token)
        _booking_event(ctx.thread.id, "BOOKING_FLOW_DATE_SELECTED",
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

        _booking_event(ctx.thread.id, "BOOKING_FLOW_SUMMARY_PREPARED",
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
        """confirm_booking. See `_confirm_booking`; a refusal branch that finds the submission
        booked on its ledger re-read answers that booking's SUCCESS instead."""
        try:
            return self._confirm_booking(booking_token, data)
        except _LedgerReplay as replay:
            return replay.response

    def _confirm_booking(self, booking_token: str, data: dict) -> dict:
        """confirm_booking — convergence Phase 3: THE BOOKING AUTHORITY BOOKS, nothing here.

        Track A's in-session writer that stood here (bounded entry → admission → one-argument
        advisory lock → revalidation → claim → ThreadRevision → Revision → consume → one
        request-session commit → date/time receipt) is deleted. Track B's Data Exchange
        authority path (`870b4e3`), reconciled onto the Phase 2 authority, replaces it:

        1. the ledger, FIRST, by the same `flow:` key the Conversation Engine derives for this
           very submission — a hit is the booking: its SUCCESS, its acknowledgement only if
           still owed, nothing re-admitted;
        2. SMART_BOOKING_CE_ENABLED off → nothing books; a person is handed the thread;
        3. context and admission as READS (offer table, offer-bound candidate, canonical
           location) — the request session writes nothing, and is proven clean at the call;
        4. ONE `BookingAuthority.execute` in its own session, carrying the frozen
           `OfferAcceptance` (the authority re-derives every binding under K1/K2);
        5. every refusal re-reads the ledger before it is answered;
        6. only after the commit: the ledger-leased acknowledgement, exactly one success copy.

        Raises BookingSlotConflictError (a refreshed APPOINTMENT screen) when nothing was
        booked, BookingTokenError when the offer may not be accepted.
        """
        from .booking_authority import (AvailabilityRevalidation, BookingAttachmentPlan,
                                        BookingCommand, BookingOperation,
                                        BookingSource, LeadIdentity, LeadUpdate,
                                        OfferAcceptance, PriceRecalculation,
                                        ThreadRevisionAttachment, ThreadStateUpdate,
                                        flow_idempotency_key)
        from .clarification_attempt import cycle_key as _cycle_key
        from .offer_lifecycle import OfferLifecycle, OfferRefusal

        token = booking_token if type(booking_token) is str else ""
        if not token.strip():
            raise BookingTokenError("token missing")
        key = flow_idempotency_key(token)
        # Convergence Phase 4: a Data Exchange request carries no replay identity of its own,
        # so each request is a distinct attempt for the evidence it leaves (P2R-10).
        from .booking_attempt_events import fresh_attempt_ref
        attempt = fresh_attempt_ref()

        # ── 1. the ledger speaks first ───────────────────────────────────────────
        hit = self._ledger_hit(key)
        if hit is not None:
            return self._replayed_success(token, key, hit)

        # ── 2. the OFF switch: no automatic booking writer is reachable ─────────────
        if not _smart_booking_enabled():
            try:
                ctx = self.resolve_context(token)
            except BookingAlreadyCompletedError:
                ctx = None
            self._refusal_reread(token, key)
            self._refusal_evidence(token, key, attempt, "SMART_BOOKING_DISABLED",
                                   ctx=ctx, finalise=False)
            if ctx is not None:
                self._handoff_to_person(ctx, "SMART_BOOKING_DISABLED")
            _booking_event(getattr(getattr(ctx, "thread", None), "id", 0) or 0,
                           "BOOKING_REFUSED", booking_token=token,
                           extra={"reason": "SMART_BOOKING_DISABLED"})
            raise BookingSlotConflictError(self._refusal_screen(
                ctx, str(data.get("date", "")).strip(), BOOKING_HANDOFF_TEXT))

        # ── 3. context and admission, as READS ───────────────────────────────────
        try:
            ctx = self.resolve_context(token)
        except BookingAlreadyCompletedError:
            # The offer table says ACCEPTED and names a booking, but the ledger missed above.
            # The offer projection is never a replay authority (design §8): re-read the
            # ledger; a miss is refused, never confirmed.
            self._refusal_reread(token, key)
            self._refusal_evidence(token, key, attempt, OfferRefusal.OFFER_ALREADY_ACCEPTED)
            raise BookingTokenError(f"offer not acceptable: {OfferRefusal.OFFER_ALREADY_ACCEPTED}")
        adm = OfferLifecycle(self.db).admit(thread_id=ctx.thread.id, token=token)
        if not adm.granted:
            self._refusal_reread(token, key)
            reason = adm.refusal or OfferRefusal.OFFER_ALREADY_ACCEPTED
            self._refusal_evidence(token, key, attempt, reason, ctx=ctx)
            _booking_event(ctx.thread.id, "BOOKING_OFFER_REFUSED", booking_token=token,
                           extra={"reason": reason, "offer_id": adm.offer_id})
            raise BookingTokenError(f"offer not acceptable: {reason}")

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
        listing_url = str(data.get("listing_url", "")).strip()

        # The offer-bound candidate (G3-7R/S3) and ITS canonical location, as resolved.
        candidate = ctx.candidate
        zone_group, zone_detail = ctx.zone_group, ctx.zone_detail
        if candidate is None:
            self._refusal_reread(token, key)
            self._refusal_evidence(token, key, attempt, OfferRefusal.OFFER_CANDIDATE_CHANGED,
                                   ctx=ctx, candidate_id=None)
            _booking_event(ctx.thread.id, "BOOKING_OFFER_REFUSED", booking_token=token,
                           extra={"reason": OfferRefusal.OFFER_CANDIDATE_CHANGED,
                                  "offer_id": adm.offer_id})
            raise BookingTokenError(
                f"offer not acceptable: {OfferRefusal.OFFER_CANDIDATE_CHANGED}")
        # L4.7W4-F4: a booking with no canonical inspection location cannot be priced, and an
        # appointment in the CRM with no money attached is a commercial defect. The message
        # promises an advisor, so a person is handed the thread.
        if not zone_group and not zone_detail:
            self._refusal_reread(token, key)
            self._refusal_evidence(token, key, attempt, OfferRefusal.NO_CANONICAL_LOCATION,
                                   ctx=ctx, candidate_id=int(candidate.id),
                                   tipo_vehiculo=candidate.tipo_vehiculo,
                                   cycle_key=int(_cycle_key(ctx.state)))
            _booking_event(ctx.thread.id, "BOOKING_REFUSED_NO_CANONICAL_LOCATION",
                           booking_token=token,
                           extra={"date": date_str, "time": time_str,
                                  "candidate_id": candidate.id})
            self._handoff_to_person(ctx, OfferRefusal.NO_CANONICAL_LOCATION)
            raise BookingSlotConflictError(self._refusal_screen(
                ctx, date_str,
                "Necesitamos confirmar la zona donde está el vehículo antes de "
                "reservar el turno. Un asesor se contacta a la brevedad."))

        name_parts = name.split()
        acceptance = OfferAcceptance(
            token_fingerprint=adm.fingerprint, thread_id=int(ctx.thread.id),
            cycle_key=int(_cycle_key(ctx.state)), candidate_id=int(candidate.id),
            tipo_vehiculo=candidate.tipo_vehiculo,
            zone_group=zone_group, zone_detail=zone_detail)
        attachments = BookingAttachmentPlan(
            thread_revision=ThreadRevisionAttachment(
                thread_id=int(ctx.thread.id), candidate_id=int(candidate.id),
                buyer_name=name or None, buyer_phone=phone or None, buyer_email=email or None,
                seller_type=None, seller_name=seller_name or None,
                address=inspection_address or None,
                tipo_vehiculo=candidate.tipo_vehiculo, marca=candidate.marca,
                modelo=candidate.modelo, anio=candidate.anio,
                publication_url=listing_url or None, zone_group=zone_group,
                request_approval=True),
            # L4.7W4-F3 (preserved): the price is stamped only from the accepted quote's
            # inputs; a mismatch leaves it unset rather than invent one.
            price=PriceRecalculation(require_inputs=(candidate.tipo_vehiculo, zone_group,
                                                     zone_detail)),
            # SB2A-R4 (R3A-03): the submitted person is ONE identity bundle.
            lead=LeadUpdate(
                estado="COORDINAR_DISPONIBILIDAD", flag="ACEPTADO", necesita_humano=True,
                identity=LeadIdentity(nombre=name_parts[0],
                                      apellido=" ".join(name_parts[1:]) or None,
                                      email=email or None) if name_parts else None),
            thread_state=ThreadStateUpdate(link_current_revision=True,
                                           last_stage=STAGE_BOOKED, needs_human=True,
                                           consume_flow_token=True),
            offer_acceptance=acceptance)
        command = BookingCommand(
            operation=BookingOperation.CREATE, source=BookingSource.WHATSAPP_FLOW,
            idempotency_key=key, lead_id=int(ctx.lead.id), thread_id=int(ctx.thread.id),
            requested_date=selected_date, requested_time=selected_time,
            correlation_id=key[:64], tracks_acknowledgement=True,
            revision_fields={
                "tipo_vehiculo": candidate.tipo_vehiculo, "marca": candidate.marca,
                "modelo": candidate.modelo, "anio": candidate.anio,
                "zone_group": zone_group, "zone_detail": zone_detail,
                "direccion_texto": inspection_address or candidate.direccion_texto,
                "vendedor_tipo": None, "tipo_vendedor": None,
            })
        # Revalidation is the AUTHORITY's, under its own locks (B-7).
        availability = AvailabilityRevalidation(
            address=inspection_address or "-", zone_group=zone_group, is_holiday=False)

        # ── 4. clean request session, then ONE authority transaction ─────────────
        if not _session_clean(self.db):
            self._refusal_reread(token, key)
            self._refusal_evidence(token, key, attempt, "DX_TRANSACTION_NOT_CLEAN", ctx=ctx,
                                   finalise=False)
            logger.error("BOOKING_REFUSED thread_id=%s — the request session holds a "
                         "transaction id; the authority is not called", ctx.thread.id)
            self._handoff_to_person(ctx, "DX_TRANSACTION_NOT_CLEAN")
            raise BookingSlotConflictError(self._refusal_screen(ctx, date_str,
                                                                BOOKING_HANDOFF_TEXT))
        result = _execute_isolated(self.db, command, availability, attachments)
        if result.reason == "SCHEDULE_REVALIDATION":
            _booking_event(ctx.thread.id, "BOOKING_REVALIDATION_FAIL", booking_token=token,
                           extra={"date": date_str, "time": time_str})

        if result.outcome is BookingOutcome.IDEMPOTENT_REPLAY:
            return self._replayed_success(token, key, result, note=False)
        if result.outcome is not BookingOutcome.COMMITTED:
            # ── 5. SB2B B-1: a committed booking always wins (CE may have booked this very
            # submission while this request was in flight) ─────────────────────────
            raced = self._ledger_hit(key)
            if raced is not None:
                _booking_event(ctx.thread.id, "BOOKING_IDEMPOTENT_REPLAY", booking_token=token,
                               extra={"crm_rev_id": raced.revision_id, "after": result.reason})
                return self._replayed_success(token, key, raced, note=False)
            _booking_event(ctx.thread.id, "BOOKING_REFUSED", booking_token=token,
                           extra={"outcome": result.outcome.value, "reason": result.reason,
                                  "date": date_str, "time": time_str})
            # P2R-10: durable evidence, after the authority's rollback — on the offer for an
            # offer or slot refusal (canonical finaliser), as an attempt event otherwise.
            from .booking_attempt_events import outcome_for
            if result.outcome in (BookingOutcome.OFFER_REFUSED, BookingOutcome.SLOT_CONFLICT,
                                  BookingOutcome.CALENDAR_EXCEPTION):
                self._refusal_evidence(
                    token, key, attempt,
                    result.reason if result.outcome is BookingOutcome.OFFER_REFUSED
                    else result.outcome.value,
                    ctx=ctx, stage="AUTHORITY", candidate_id=int(candidate.id),
                    tipo_vehiculo=candidate.tipo_vehiculo, zone_group=zone_group,
                    zone_detail=zone_detail, cycle_key=acceptance.cycle_key)
            else:
                self._refusal_evidence(token, key, attempt,
                                       result.reason or result.outcome.value, ctx=ctx,
                                       finalise=False, stage="AUTHORITY",
                                       outcome=outcome_for(result.outcome))
            if result.outcome is BookingOutcome.OFFER_REFUSED:
                raise BookingTokenError(f"offer not acceptable: {result.reason}")
            message = _FLOW_REFUSAL_MESSAGES.get(result.outcome, BOOKING_HANDOFF_TEXT)
            if message == BOOKING_HANDOFF_TEXT:
                # the screen promises an advisor, so a person is handed the thread
                self._handoff_to_person(ctx, result.reason)
            # Transient contention and a vanished slot write nothing: the customer picks
            # again, or retries in a moment, from what is free now.
            raise BookingSlotConflictError(self._refusal_screen(ctx, date_str, message))

        _booking_event(ctx.thread.id, "BOOKING_CREATED", booking_token=token,
                       extra={"thread_rev_id": result.thread_revision_id,
                              "crm_rev_id": result.revision_id, "date": date_str,
                              "time": time_str, "profesional_id": result.profesional_id,
                              "lock_mode": result.lock_mode})
        logger.info("BOOKING_CREATED thread_id=%s thread_rev=%s crm_rev=%s date=%s time=%s",
                    ctx.thread.id, result.thread_revision_id, result.revision_id,
                    date_str, time_str)
        if result.identity_outcome == "CONFLICT":
            _booking_event(ctx.thread.id, "BOOKING_IDENTITY_CONFLICT", booking_token=token,
                           extra={"crm_rev_id": result.revision_id, "lead_id": ctx.lead.id})
        # ── 6. after the commit: the one acknowledgement, through the ledger's claim ──
        self._acknowledge(key, result, wa_id=ctx.contact.wa_id, thread_id=int(ctx.thread.id))
        return self._success_response(token)

    # ── convergence Phase 3 helpers ───────────────────────────────────────────

    def _refusal_evidence(self, token: str, key: str, attempt: str, reason: str, *, ctx=None,
                          finalise: bool = True, stage: str = "PRE_EXECUTE",
                          outcome: str = "REFUSED", **bindings) -> None:
        """Convergence Phase 4 (P2R-10): the durable evidence of a refused confirmation —
        the canonical finaliser on its offer, or an attempt event for a refusal that concerns
        no offer. Called only after the ledger re-read missed. Never raises; never changes
        the screen this request answers with."""
        from .booking_attempt_events import record_attempt_event
        from .booking_refusal import finalise_offer_refusal
        thread_id = getattr(getattr(ctx, "thread", None), "id", None)
        try:
            if finalise and thread_id is not None:
                finalise_offer_refusal(self.db, source="WHATSAPP_FLOW", thread_id=int(thread_id),
                                       token=token, observed_reason=reason, attempt_ref=attempt,
                                       command_key=key, **bindings)
            else:
                record_attempt_event(self.db, source="WHATSAPP_FLOW", stage=stage,
                                     outcome=outcome, reason=reason, command_key=key,
                                     attempt_ref=attempt, thread_id=thread_id)
        except Exception as exc:
            logger.error("BOOKING refusal evidence failed: %s", type(exc).__name__)

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

    def _ledger_hit(self, key: str):
        """The committed, replayable ledger result for `key` (isolated read), or None. A
        DETACHED key is never a hit."""
        from .booking_ce_adapter import lookup_booking
        return lookup_booking(self.db, key)

    def _refusal_reread(self, token: str, key: str) -> None:
        """SB2B-RV-01: every refusal re-reads the ledger before it is answered. A hit means a
        concurrent writer committed this very submission: the refusal is replaced by that
        booking's SUCCESS (raised, so every refusal branch inherits it)."""
        hit = self._ledger_hit(key)
        if hit is not None:
            raise _LedgerReplay(self._replayed_success(token, key, hit))

    def _replayed_success(self, token: str, key: str, hit, *, note: bool = True) -> dict:
        """A committed booking for this submission: its SUCCESS; its acknowledgement only if
        the ledger says one is still owed (SENT, a live lease, exhausted → silence)."""
        if note:
            try:
                from .booking_ce_adapter import isolated_session
                from .booking_authority import BookingAuthority
                with isolated_session(self.db) as s:
                    BookingAuthority(s).note_replay(key)
            except Exception as exc:                 # bookkeeping only
                logger.warning("BOOKING_REPLAY not counted: %s", type(exc).__name__)
        _booking_event(0, "BOOKING_IDEMPOTENT_REPLAY", booking_token=token,
                       extra={"crm_rev_id": hit.revision_id,
                              "thread_rev_id": hit.thread_revision_id,
                              "ack_status": hit.ack_status})
        wa_id, thread_id = self._recipient_for(hit)
        if wa_id is not None:
            self._acknowledge(key, hit, wa_id=wa_id, thread_id=thread_id)
        return self._success_response(token)

    def _recipient_for(self, hit) -> tuple:
        """The customer of a committed booking, read from its ThreadRevision."""
        tr = self.db.get(ThreadRevision, hit.thread_revision_id) \
            if hit.thread_revision_id else None
        thread = self.db.get(WhatsAppThread, tr.thread_id) if tr is not None else None
        contact = self.db.get(WhatsAppContact, thread.contact_id) if thread is not None else None
        if contact is None:
            return None, None
        return contact.wa_id, int(thread.id)

    def _acknowledge(self, key: str, result, *, wa_id, thread_id: int) -> None:
        """B-5 through the ONE shared sequence (claim → gate → record). The Data Exchange
        request has no causal inbound message and needs none: at most once comes from the
        command-bound delivery identity and the ledger's two-attempt policy (Phase 3-R1), the
        same for both transports — not from the lease, which only excludes concurrent claims."""
        from .booking_acknowledgement import acknowledge_committed_booking
        try:
            acknowledge_committed_booking(self.db, key=key, ack_status=result.ack_status,
                                          wa_id=wa_id, thread_id=thread_id)
        except Exception as exc:
            # The booking is committed; nothing here may undo it. The ledger still says the
            # acknowledgement is owed, so a replay or /control recovers it.
            logger.error("BOOKING_ACK failed thread_id=%s: %s", thread_id, type(exc).__name__)

    def _refusal_screen(self, ctx, date_str: str, message: str) -> dict:
        """The APPOINTMENT screen with what is free now and the reason nothing was booked."""
        if ctx is None:
            return {"version": FLOW_VERSION, "screen": "APPOINTMENT",
                    "data": {"selected_date": "", "selected_time": "",
                             "slot_conflict_message": message}}
        return {
            "version": FLOW_VERSION,
            "screen": "APPOINTMENT",
            "data": {
                **self._appointment_screen_data(ctx, selected_date_str=date_str),
                # the time they picked is exactly what was not booked
                "selected_time": "",
                "slot_conflict_message": message,
            },
        }

    def _handoff_to_person(self, ctx, reason: str) -> None:
        """A refusal whose screen promises an advisor hands the thread to a person: the
        `needs_human` flags, in a short transaction that takes the thread-state row FIRST
        and bounded (K1 before the lead, CD-07). No booking row, no offer row, no ledger row
        is touched. On contention nothing is written and the refusal stands."""
        from .offer_lifecycle import OfferLifecycle
        tid = int(ctx.thread.id)
        try:
            self.db.rollback()                        # the request session only ever read
            with self.db.no_autoflush:
                contended = OfferLifecycle(self.db).begin_offer_transaction(tid)
            if contended is not None:
                logger.warning("BOOKING_HANDOFF thread_id=%s contended (%s) — not recorded",
                               tid, contended)
                return
            state = self.db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == tid)).scalar_one_or_none()
            if state is not None:
                state.needs_human = True
            lead = self.db.get(Lead, ctx.lead.id) if ctx.lead is not None else None
            if lead is not None:
                lead.necesita_humano = True
            self.db.commit()
            _booking_event(tid, "BOOKING_HANDOFF", extra={"reason": reason})
        except Exception as exc:
            logger.error("BOOKING_HANDOFF thread_id=%s failed: %s", tid, type(exc).__name__)
            try:
                self.db.rollback()
            except Exception:
                pass


class _LedgerReplay(Exception):
    """Internal: a refusal branch found the submission booked on re-read."""
    def __init__(self, response: dict):
        super().__init__("ledger replay")
        self.response = response


#: What the customer sees when the authority refuses a confirmation (Track B `870b4e3`).
#: Nothing is booked and nothing is acknowledged; the APPOINTMENT screen comes back with what
#: is free now. Any other outcome shows `BOOKING_HANDOFF_TEXT`, which promises an advisor, and
#: is therefore backed by a recorded handoff.
_FLOW_REFUSAL_MESSAGES: dict = {
    BookingOutcome.SLOT_CONFLICT: ("Justo ese horario dejó de estar disponible. "
                                   "Elegí otro de los horarios actualizados."),
    BookingOutcome.CALENDAR_EXCEPTION: ("Ese día u horario ya no está disponible en la "
                                        "agenda. Elegí otro de los horarios actualizados."),
    BookingOutcome.RETRYABLE_FAILURE: ("No pudimos confirmar el turno en este momento. "
                                       "Volvé a intentarlo en unos segundos."),
}


def _smart_booking_enabled() -> bool:
    """SMART_BOOKING_CE_ENABLED, read per request with the same exact `is True` test the
    Conversation Engine applies: a missing, malformed or merely truthy value is OFF."""
    from ..settings import get_settings
    return getattr(get_settings(), "smart_booking_ce_enabled", False) is True


def _session_clean(db: Session) -> bool:
    """B-3 for the Data Exchange request: may it delegate now? Only when its own session has
    written nothing — `txid_current_if_assigned() IS NULL` on PostgreSQL, the driver's
    `in_transaction` on SQLite; any other dialect, or a failure to ask, is refused."""
    try:
        dialect = db.get_bind().dialect.name
        if dialect == "postgresql":
            return db.execute(text("SELECT txid_current_if_assigned()")).scalar() is None
        if dialect == "sqlite":
            raw = db.connection().connection
            return not bool(getattr(getattr(raw, "driver_connection", raw),
                                    "in_transaction", True))
    except Exception as exc:
        logger.error("BOOKING txid guard failed: %s", type(exc).__name__)
    return False


def _execute_isolated(db: Session, command, availability, attachments):
    """ONE authority transaction on its own session and connection (never the request's)."""
    from .booking_authority import BookingAuthority, refuse
    from .booking_ce_adapter import isolated_session
    try:
        with isolated_session(db) as session:
            return BookingAuthority(session).execute(command, availability=availability,
                                                     attachments=attachments)
    except Exception as exc:                          # the authority returns typed results
        logger.error("BOOKING authority raised: %s", type(exc).__name__)
        return refuse(BookingOutcome.INTERNAL_FAILURE, "AUTHORITY_EXCEPTION")


# ── Observability helpers ─────────────────────────────────────────────────────

def _booking_event(
    thread_id: int,
    event_type: str,
    booking_token: str = "",
    extra: Optional[dict] = None,
) -> None:
    """Emit ONE structured log line for a booking lifecycle event. Persists NOTHING.

    R3/RVR2-07. This used to be called `_log_event`, take a `Session` it never used, and
    say "Persist a booking lifecycle event" — while its body was a single `logger.info`.
    The R2 independent review found what that cost on the contended branch of
    `handle_confirm_booking`: `begin_offer_transaction` had already ROLLED BACK, and the
    next line handed `self.db` to something documented as persisting, so the code read as
    though a durable `BOOKING_OFFER_CONTENDED` record survived the contention. None ever
    did, on that branch or any other.

    The lie was in the signature and the docstring, not in the behaviour, so the
    correction is to the signature and the docstring: no session is taken, nothing claims
    durability, and no caller can read a database event into it. Diagnostic evidence must
    never delay or alter a bounded response, and a log line cannot.

    The durable record of a contended booking turn remains what the audit says it is: the
    typed outcome returned to the caller and the structured line below.
    """
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


# ── the ledger-driven acknowledgement and the handoff copy (convergence Phase 3) ─────────
# Re-exported for callers that import them from here (CE's handoff, booking_recovery). The
# Data Exchange confirmation above uses `BOOKING_HANDOFF_TEXT` for refusal screens that
# promise an advisor, and sends its success acknowledgement through
# `booking_acknowledgement.acknowledge_committed_booking`.
from .booking_acknowledgement import (  # noqa: E402,F401
    BOOKING_FLOW_RECEIPT_TEXT, BOOKING_HANDOFF_TEXT, ReceiptOutcome, send_booking_acknowledgement)
