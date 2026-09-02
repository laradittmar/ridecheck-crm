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
from ..schemas.schedule import ScheduleCheckIn
from ..services.schedule import ScheduleService

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


def _format_customer_summary(name: str, phone: str) -> str:
    parts = [p for p in [name, phone] if p]
    return " · ".join(parts) if parts else ""


# ── Core service ──────────────────────────────────────────────────────────────

class BookingFlowService:
    """Handles all server-side logic for the RideCheck Booking Meta Flow."""

    def __init__(self, db: Session):
        self.db = db
        self._sched = ScheduleService(db)

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
        if candidate:
            return candidate.zone_group, candidate.zone_detail
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
        for delta in range(1, BOOKING_HORIZON_DAYS + 1):
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

        if selected_date_str:
            try:
                selected_date = date.fromisoformat(selected_date_str)
                time_items = self._slots_for_date(selected_date, ctx.zone_group)
                is_time_enabled = bool(time_items)
            except (ValueError, TypeError):
                pass

        data: dict = {
            "booking_token": ctx.booking_token,
            "vehicle_summary": ctx.vehicle_summary,
            "location_summary": ctx.location_summary,
            "date": date_items,
            "is_date_enabled": is_date_enabled,
            "time": time_items,
            "is_time_enabled": is_time_enabled,
        }
        if error_message:
            data["slot_conflict_message"] = error_message
        return data

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
        phone = str(data.get("phone", "")).strip()
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
        """Handle confirm_booking: revalidate slot, atomic booking, consume token.

        Sequence (per milestone spec):
        1. resolve token → active context
        2. parse selected date/time
        3. acquire advisory lock (PostgreSQL) scoped to date
        4. ScheduleService.check() again
        5. if valid: create booking atomically
        6. consume token
        7. return SUCCESS response

        Raises BookingSlotConflictError with refreshed data if slot is gone.
        """
        ctx = self.resolve_context(booking_token)

        date_str = str(data.get("date", "")).strip()
        time_str = str(data.get("time", "")).strip()

        try:
            selected_date = date.fromisoformat(date_str)
            selected_time = time.fromisoformat(time_str)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid date/time: {exc}") from exc

        name = str(data.get("name", "")).strip()
        phone = str(data.get("phone", "")).strip()
        email = str(data.get("email", "")).strip()
        inspection_address = str(data.get("inspection_address", "")).strip()
        seller_name = str(data.get("seller_name", "")).strip()
        seller_phone = str(data.get("seller_phone", "")).strip()
        listing_url = str(data.get("listing_url", "")).strip()

        # Acquire advisory lock to prevent double-booking the same date.
        # Key is deterministic: hash of ISO date string → 32-bit signed int.
        lock_key = int(hashlib.sha256(date_str.encode()).hexdigest()[:8], 16) & 0x7FFFFFFF
        self._acquire_advisory_lock(lock_key, date_str)

        # Revalidate slot
        check_in = ScheduleCheckIn(
            address=inspection_address or "-",
            preferred_day=selected_date,
            preferred_time=selected_time,
            zone_group=ctx.zone_group,
            is_holiday=False,
        )
        check_out = self._sched.check(check_in)

        if not check_out.valid:
            _log_event(
                self.db, ctx.thread.id, "BOOKING_REVALIDATION_FAIL",
                booking_token=booking_token,
                extra={"date": date_str, "time": time_str, "reasons": check_out.reasons},
            )
            # Refresh available slots for conflict recovery
            refreshed_time_items = self._slots_for_date(selected_date, ctx.zone_group)
            raise BookingSlotConflictError({
                "version": FLOW_VERSION,
                "screen": "APPOINTMENT",
                "data": {
                    **self._appointment_screen_data(ctx, selected_date_str=date_str),
                    "slot_conflict_message": (
                        "Justo ese horario dejó de estar disponible. "
                        "Elegí otro de los horarios actualizados."
                    ),
                },
            })

        _log_event(
            self.db, ctx.thread.id, "BOOKING_REVALIDATION_PASS",
            booking_token=booking_token,
            extra={"date": date_str, "time": time_str},
        )

        # Create booking atomically
        candidate = ctx.candidate
        zone_group = ctx.zone_group
        zone_detail = ctx.zone_detail

        thread_rev = ThreadRevision(
            thread_id=ctx.thread.id,
            candidate_id=candidate.id if candidate else None,
            status="booked",
            buyer_name=name or None,
            buyer_phone=phone or None,
            buyer_email=email or None,
            seller_type=None,
            seller_name=seller_name or None,
            address=inspection_address or None,
            scheduled_date=selected_date,
            scheduled_time=selected_time,
            tipo_vehiculo=candidate.tipo_vehiculo if candidate else None,
            marca=candidate.marca if candidate else None,
            modelo=candidate.modelo if candidate else None,
            anio=candidate.anio if candidate else None,
            publication_url=listing_url or None,
            zone_group=zone_group,
            appointment_approval_status="PENDING",
            appointment_approval_token=secrets.token_urlsafe(32),
        )
        self.db.add(thread_rev)
        self.db.flush()

        crm_rev = Revision(
            lead_id=ctx.lead.id,
            tipo_vehiculo=candidate.tipo_vehiculo if candidate else None,
            marca=candidate.marca if candidate else None,
            modelo=candidate.modelo if candidate else None,
            anio=candidate.anio if candidate else None,
            zone_group=zone_group,
            zone_detail=zone_detail,
            direccion_texto=inspection_address or (candidate.direccion_texto if candidate else None),
            vendedor_tipo=None,
            tipo_vendedor=None,
            turno_fecha=selected_date,
            turno_hora=selected_time,
        )
        self.db.add(crm_rev)
        self.db.flush()

        # Lead state
        lead = ctx.lead
        lead.estado = "COORDINAR_DISPONIBILIDAD"
        lead.flag = "ACEPTADO"
        lead.necesita_humano = True
        if name and not lead.nombre:
            parts = name.split()
            lead.nombre = parts[0] if parts else name
            lead.apellido = " ".join(parts[1:]) if len(parts) > 1 else None

        # Thread state — consume token
        state = ctx.state
        state.current_revision_id = thread_rev.id
        state.last_stage = STAGE_BOOKED
        state.needs_human = True
        state.flow_booking_token = None  # token consumed

        self.db.commit()

        _log_event(
            self.db, ctx.thread.id, "BOOKING_CREATED",
            booking_token=booking_token,
            extra={
                "thread_rev_id": thread_rev.id,
                "crm_rev_id": crm_rev.id,
                "date": date_str,
                "time": time_str,
            },
        )

        logger.info(
            "BOOKING_CREATED thread_id=%s thread_rev=%s crm_rev=%s date=%s time=%s",
            ctx.thread.id, thread_rev.id, crm_rev.id, date_str, time_str,
        )

        # Return Flow SUCCESS completion
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

    # ── Concurrency / advisory lock ───────────────────────────────────────────

    def _acquire_advisory_lock(self, lock_key: int, date_str: str) -> None:
        """Acquire a PostgreSQL advisory transaction lock for the booking date.

        On SQLite (tests) this is a no-op — SQLite serializes writes natively.
        On PostgreSQL, two simultaneous confirmations for the same date compete;
        the loser receives an immediate BLOCKER response before revalidation runs.
        """
        try:
            result = self.db.execute(
                text("SELECT pg_try_advisory_xact_lock(:key)"),
                {"key": lock_key},
            ).scalar()
            if result is False:
                # Another transaction holds the lock — slot contention
                _log_event(
                    self.db, 0, "BOOKING_CONCURRENCY_CONFLICT",
                    extra={"date": date_str, "lock_key": lock_key},
                )
                raise BookingSlotConflictError({
                    "version": FLOW_VERSION,
                    "screen": "APPOINTMENT",
                    "data": {
                        "slot_conflict_message": (
                            "Justo ese horario dejó de estar disponible. "
                            "Elegí otro de los horarios actualizados."
                        ),
                    },
                })
        except BookingSlotConflictError:
            raise
        except Exception:
            # On SQLite or any unsupported backend, skip the lock.
            pass


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
