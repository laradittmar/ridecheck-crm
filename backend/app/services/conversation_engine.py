"""M18 — Backend-owned WhatsApp conversation engine.

Entry point: POST /api/conversation/handle
Called by n8n AFTER it has:
  - Waited for ráfaga window (20 s burst de-bounce)
  - Transcribed audio (Whisper) and placed result in `text`
  - Built recent_user_messages / unanswered_recent_user_messages
  - Built recent_outbound_replies

State machine rules (official):
  A. New inquiry / collecting data  → estado=CONSULTA_NUEVA, flag=PRESUPUESTANDO
  B. Quote sent                     → estado=CONSULTA_NUEVA, flag=PRESUPUESTO_ENVIADO
                                       (flag committed AFTER WhatsApp send succeeds)
  C. Client accepts quote           → estado=CONSULTA_NUEVA, flag=ACEPTADO, stage=SCHEDULING
  D. Flow button sent               → estado=CONSULTA_NUEVA, flag=ACEPTADO, flow_booking_token saved
  E. Flow submitted / revision      → estado=COORDINAR_DISPONIBILIDAD, flag=ACEPTADO, needs_human=True
  F. Human approves                 → estado=AGENDADO  (CRM only — never by this engine)

Forbidden engine writes:
  - estado: AGENDADO, REVISION_COMPLETA
  - flag:   PERDIDO, BUSCANDO_AUTO, RECOMPRA
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from urllib import error as urlerror, request as urlrequest

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Lead,
    Revision,
    ThreadRevision,
    ViaticosZone,
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppThread,
    WhatsAppThreadCandidate,
    WhatsAppThreadState,
)
from ..repositories.pricing_repository import PricingRepository
from ..schemas.conversation import (
    HANDLED_ACTIONS,
    ConversationHandleIn,
    ConversationHandleOut,
)
from ..schemas.schedule import ScheduleCheckIn
from ..services.pricing import PricingNotFoundError, PricingQuote, PricingService
from ..services.schedule import ScheduleService
from ..services.unanswered_alert import reset_unanswered_alert
from ..services.vehicle_catalog import VehicleMatch, lookup_vehicle
from ..settings import Settings
from ..ui.whatsapp_ui import _send_whatsapp_cloud_flow, _send_whatsapp_cloud_text

logger = logging.getLogger(__name__)

_ALLOWED_FLAGS = {"PRESUPUESTANDO", "PRESUPUESTO_ENVIADO", "ACEPTADO"}

STAGE_QUALIFYING = "QUALIFYING"
STAGE_QUOTED = "QUOTED"
STAGE_SCHEDULING = "SCHEDULING"
STAGE_FLOW_SENT = "FLOW_SENT"
STAGE_BOOKED = "BOOKED"
STAGE_HUMAN = "HUMAN_REQUIRED"

_ACCEPTANCE_KEYWORDS = frozenset({
    "sí", "si", "yes", "ok", "dale", "perfecto", "avancemos",
    "listo", "buenísimo", "me sirve", "bueno", "claro",
    "de acuerdo", "por supuesto", "quiero avanzar",
})


def _is_acceptance(texts: list[str]) -> bool:
    """Return True when all user messages together express unambiguous acceptance."""
    combined = " ".join(texts).strip()
    normalized = combined.lower().strip("!.¡ ").strip()
    return normalized in _ACCEPTANCE_KEYWORDS


# Matches a vehicle model year: 1980–2029
_VEHICLE_YEAR_RE = re.compile(r"\b(19[89]\d|20[012]\d)\b")

# Matches any price-like token: "$5.000", "$5000", "5000 pesos", etc.
# Used to detect AI-hallucinated prices when real_price_quote is None.
_PRICE_RE = re.compile(r'\$\s*\d[\d.,]*|\b\d[\d.,]+\s*pesos\b', re.IGNORECASE)

# Matches "te envío la cotización / te paso el precio" patterns — quote-promise
# without an actual amount.  Scrubbed when real_price_quote is None.
_QUOTE_INTENT_RE = re.compile(
    r'(?:te\s+(?:envío|paso|mando|alcanzo)|envíamos|les\s+enviamos|ya\s+te\s+(?:envío|paso|mando))'
    r'\s+(?:el\s+precio|la\s+cotizaci[oó]n|el\s+presupuesto)',
    re.IGNORECASE,
)

# CABA synonyms: all strings that mean "Ciudad Autónoma de Buenos Aires".
# Stored as lowercase/normalized for direct substring matching.
# Does NOT include "caba" itself — that's handled by the zone_detail="CABA" DB row.
_CABA_SYNONYMS: frozenset[str] = frozenset({
    "capital federal",
    "ciudad autonoma de buenos aires",
    "ciudad autónoma de buenos aires",
    "cdad autonoma de buenos aires",
    "cdad autónoma de buenos aires",
    "c.a.b.a.",
    "ciudad de buenos aires",
})
_CABA_CANONICAL_DETAIL = "CABA"
_CABA_CANONICAL_GROUP = "CABA"


def _is_caba_synonym(value: str) -> bool:
    """Return True if value (case-insensitive) is a known CABA synonym."""
    return " ".join((value or "").lower().split()) in _CABA_SYNONYMS


def _extract_year_from_text(text: str) -> int | None:
    m = _VEHICLE_YEAR_RE.search(text)
    return int(m.group(1)) if m else None


# Maps lowercased Spanish day names (accented + unaccented) to Python weekday numbers.
_SPANISH_DAY_TO_WEEKDAY: dict[str, int] = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "miércoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "sábado": 5,
    "domingo": 6,
}


def _parse_scheduling_text(texts: list[str], today: date) -> tuple[str | None, str | None]:
    """Parse a Spanish day+time expression from SCHEDULING-stage user messages.

    Returns (iso_date_str, "HH:MM").  Either value may be None if not found.
    The result is always DB-safe: iso_date_str fits VARCHAR(20) and "HH:MM"
    fits VARCHAR(10).
    """
    combined = " ".join(texts).lower()

    # ── Day extraction ────────────────────────────────────────────────────
    # Specific day names take priority over relative words so that
    # "viernes por la mañana" is resolved as "viernes", not "tomorrow".
    day_name_found = any(name in combined for name in _SPANISH_DAY_TO_WEEKDAY)
    day_iso: str | None = None

    if "pasado mañana" in combined or "pasado manana" in combined:
        day_iso = (today + timedelta(days=2)).isoformat()
    elif ("mañana" in combined or "manana" in combined) and not day_name_found:
        day_iso = (today + timedelta(days=1)).isoformat()
    elif "hoy" in combined and not day_name_found:
        day_iso = today.isoformat()

    if day_iso is None:
        for day_name, weekday in _SPANISH_DAY_TO_WEEKDAY.items():
            if day_name in combined:
                days_ahead = weekday - today.weekday()
                if days_ahead <= 0:  # today or past → next week
                    days_ahead += 7
                day_iso = (today + timedelta(days=days_ahead)).isoformat()
                break

    # ── Time extraction ───────────────────────────────────────────────────
    time_str: str | None = None

    # "12hs", "12h", "9:30hs", "9:30h"  (most common Argentine pattern)
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*h(?:s|oras?)?\b", combined)
    if m:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        if 0 <= h <= 23 and 0 <= mi <= 59:
            time_str = f"{h:02d}:{mi:02d}"

    if not time_str:
        # "las 12", "la 1", "a las 9:30"
        m = re.search(r"\bla[s]?\s+(\d{1,2})(?::(\d{2}))?\b", combined)
        if m:
            h, mi = int(m.group(1)), int(m.group(2) or 0)
            if 0 <= h <= 23 and 0 <= mi <= 59:
                time_str = f"{h:02d}:{mi:02d}"

    if not time_str:
        # Standalone "9:30" or "12:00"
        m = re.search(r"\b(\d{1,2}):(\d{2})\b", combined)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            if 0 <= h <= 23 and 0 <= mi <= 59:
                time_str = f"{h:02d}:{mi:02d}"

    return day_iso, time_str


def _out(action: str, **kwargs) -> ConversationHandleOut:
    return ConversationHandleOut(
        ok=action not in ("error",),
        action=action,
        handled=action in HANDLED_ACTIONS,
        **kwargs,
    )


@dataclass
class _Context:
    thread: WhatsAppThread
    contact: WhatsAppContact
    lead: Lead | None
    state: WhatsAppThreadState | None
    candidates: list[WhatsAppThreadCandidate]
    db_messages: list[WhatsAppMessage]  # last 20 messages from DB (fallback only)


class ConversationEngine:
    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._pricing = PricingService(repository=PricingRepository())
        self._schedule = ScheduleService(db=db)

    # ── Public entrypoint ─────────────────────────────────────────────────

    def handle(self, event: ConversationHandleIn) -> ConversationHandleOut:
        try:
            return self._handle(event)
        except Exception:
            logger.exception(
                "M18 engine unhandled error thread_id=%s wa=%s",
                event.thread_id, event.wa_message_id,
            )
            try:
                self.db.rollback()
            except Exception:
                pass
            return _out("error", detail="internal_error")

    # ── Core dispatch ─────────────────────────────────────────────────────

    def _handle(self, event: ConversationHandleIn) -> ConversationHandleOut:
        ctx = self._load_context(event.thread_id)
        if ctx is None:
            logger.warning("M18 thread_id=%s not found", event.thread_id)
            return _out("error", detail="thread_not_found")

        # Dedup: already processed this wa_message_id
        if ctx.state and ctx.state.last_processed_inbound_wa_message_id == event.wa_message_id:
            logger.info("M18 dedup thread_id=%s wa=%s", event.thread_id, event.wa_message_id)
            return _out("skipped_dedup")

        # Mark as processing — committed atomically with the final successful action,
        # so a failure mid-flight does not leave the message appearing processed.
        state = self._get_or_create_state(ctx)
        state.last_processed_inbound_wa_message_id = event.wa_message_id

        # No lead linked — cannot make business decisions; commit dedup marker now
        if ctx.lead is None:
            logger.info("M18 thread_id=%s no linked lead", event.thread_id)
            self.db.commit()
            return _out("no_lead")

        # Human takeover active: AI is suppressed; commit dedup marker now
        if state.needs_human:
            logger.info("M18 thread_id=%s needs_human — AI suppressed", event.thread_id)
            self.db.commit()
            return _out("skipped_human")

        # Route by message type
        if event.message_type == "flow_response" and event.flow_response:
            return self._process_flow_response(ctx, event.flow_response, event.flow_token)

        # Text path: uses n8n-provided content (audio already transcribed by n8n)
        return self._process_text(ctx, event)

    # ── Flow response (deterministic, no AI) ─────────────────────────────

    def _process_flow_response(
        self,
        ctx: _Context,
        flow_data: dict,
        flow_token: str | None,
    ) -> ConversationHandleOut:
        lead = ctx.lead
        assert lead is not None
        state = ctx.state
        assert state is not None

        # Token validation (warn only — don't hard-block in case of token mismatch due to clock skew)
        expected = state.flow_booking_token
        if expected and flow_token and flow_token != expected:
            logger.warning(
                "M18 flow token mismatch thread_id=%s expected=%s got=%s",
                ctx.thread.id, expected, flow_token,
            )

        # Parse flow fields
        full_name = (flow_data.get("nombre_apellido") or "").strip()
        name_parts = full_name.split() if full_name else []
        buyer_first = name_parts[0] if name_parts else ""
        buyer_last = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""
        buyer_email = (flow_data.get("email") or "").strip() or None
        buyer_phone = (flow_data.get("telefono") or lead.telefono or "").strip() or None
        direccion = (
            flow_data.get("direccion")
            or flow_data.get("direccion_texto")
            or ""
        ).strip() or None
        seller_type = (flow_data.get("tipo_vendedor") or "").strip() or None
        seller_name = (flow_data.get("nombre_vendedor") or "").strip() or None
        publication_url = (flow_data.get("link_publicacion") or "").strip() or None
        canal = (flow_data.get("como_llego") or "").strip() or None

        # Date/time from thread state (set during scheduling stage)
        sched_date: date | None = None
        sched_time: time | None = None
        if state.preferred_day:
            try:
                sched_date = date.fromisoformat(str(state.preferred_day))
            except (ValueError, TypeError):
                pass
        if state.preferred_time:
            try:
                sched_time = time.fromisoformat(str(state.preferred_time))
            except (ValueError, TypeError):
                pass

        # Vehicle from focus candidate
        focus = self._focus_candidate(ctx)
        tipo_vehiculo = focus.tipo_vehiculo if focus else None
        marca = focus.marca if focus else None
        modelo = focus.modelo if focus else None
        anio = focus.anio if focus else None
        zone_group = state.home_zone_group or (focus.zone_group if focus else None)
        zone_detail = state.home_zone_detail or (focus.zone_detail if focus else None)

        # Create ThreadRevision (WhatsApp side) with status=booked
        thread_rev = ThreadRevision(
            thread_id=ctx.thread.id,
            candidate_id=focus.id if focus else None,
            status="booked",
            buyer_name=full_name or None,
            buyer_phone=buyer_phone,
            buyer_email=buyer_email,
            seller_type=seller_type,
            seller_name=seller_name,
            address=direccion,
            scheduled_date=sched_date,
            scheduled_time=sched_time,
            tipo_vehiculo=tipo_vehiculo,
            marca=marca,
            modelo=modelo,
            anio=anio,
            publication_url=publication_url,
            appointment_approval_status="PENDING",
            appointment_approval_token=secrets.token_urlsafe(32),
        )
        self.db.add(thread_rev)
        self.db.flush()

        # Create CRM Revision
        crm_rev = Revision(
            lead_id=lead.id,
            tipo_vehiculo=tipo_vehiculo,
            marca=marca,
            modelo=modelo,
            anio=anio,
            zone_group=zone_group,
            zone_detail=zone_detail,
            direccion_texto=direccion,
            vendedor_tipo=seller_type,
            tipo_vendedor=seller_type,
            turno_fecha=sched_date,
            turno_hora=sched_time,
        )
        self.db.add(crm_rev)
        self.db.flush()
        self._pricing.recalculate_revision_if_possible(db=self.db, revision=crm_rev)

        # Lead state: E — Flow submitted
        lead.estado = "COORDINAR_DISPONIBILIDAD"
        lead.flag = "ACEPTADO"
        lead.necesita_humano = True
        if buyer_first and not lead.nombre:
            lead.nombre = buyer_first
        if buyer_last and not lead.apellido:
            lead.apellido = buyer_last
        if canal and not lead.canal:
            lead.canal = canal
        if buyer_email and not lead.email:
            lead.email = buyer_email

        # Thread state
        state.current_revision_id = thread_rev.id
        state.last_stage = STAGE_BOOKED
        state.needs_human = True
        state.flow_booking_token = None  # consumed

        self.db.commit()
        logger.info(
            "M18 booking_created thread_id=%s thread_rev=%s crm_rev=%s",
            ctx.thread.id, thread_rev.id, crm_rev.id,
        )

        self._send_booking_notification(
            thread_rev_id=thread_rev.id,
            crm_rev_id=crm_rev.id,
            lead_id=lead.id,
            buyer_name=full_name or None,
            buyer_phone=buyer_phone,
            buyer_email=buyer_email,
            marca=marca,
            modelo=modelo,
            anio=str(anio) if anio else None,
            tipo_vehiculo=tipo_vehiculo,
            zone_group=zone_group,
            zone_detail=zone_detail,
            address=direccion,
            seller_type=seller_type,
            seller_name=seller_name,
            scheduled_date=sched_date.isoformat() if sched_date else None,
            scheduled_time=sched_time.strftime("%H:%M") if sched_time else None,
        )

        buyer_display = buyer_first or state.customer_name or "cliente"
        confirm_text = (
            f"¡Perfecto, {buyer_display}! Tu solicitud de turno quedó registrada 🎉\n\n"
            "Un asesor va a revisar la disponibilidad y te confirma el turno a la brevedad. "
            "Cualquier consulta respondé por acá."
        )
        sent_id = self._send_text_to_wa(ctx, confirm_text)
        return _out("booking_created", wa_message_id=sent_id)

    # ── Text / AI path ────────────────────────────────────────────────────

    def _process_text(self, ctx: _Context, event: ConversationHandleIn) -> ConversationHandleOut:
        lead = ctx.lead
        assert lead is not None
        state = ctx.state
        assert state is not None

        # Ensure lead has a flag on first contact
        if not lead.flag:
            lead.flag = "PRESUPUESTANDO"
            state.last_stage = STAGE_QUALIFYING
            self.db.commit()

        # ── Ráfaga: use unanswered messages as primary input ──────────────
        # If n8n provided unanswered messages, respond to all of them together.
        # This is the core of the ráfaga (burst) solution.
        if event.unanswered_recent_user_messages:
            ai_input_messages = event.unanswered_recent_user_messages
        elif event.text:
            ai_input_messages = [event.text]
        else:
            logger.info("M18 no text content thread_id=%s — ignored", ctx.thread.id)
            return _out("skipped_dedup", detail="no_text")

        # ── Deterministic QUOTED acceptance (pre-AI) ─────────────────────
        # When the client is in QUOTED stage and sends a clear acceptance word,
        # skip the AI entirely: set flag=ACEPTADO, stage=SCHEDULING, and ask
        # for day/time only.  No revision, no Flow, no re-quoting.
        if state.last_stage == STAGE_QUOTED and _is_acceptance(ai_input_messages):
            return self._handle_quoted_acceptance(ctx, state)

        # ── Deterministic SCHEDULING day/time parse (pre-AI) ─────────────
        # Parse Spanish day names and time expressions without calling the AI.
        # The AI historically misformats preferred_time_str (e.g. "Viernes 12hs"
        # instead of "12:00"), overflowing the VARCHAR(10) column.
        if (
            state.last_stage == STAGE_SCHEDULING
            and not state.needs_human
            and not state.flow_booking_token
        ):
            sched_day_iso, sched_time_str = _parse_scheduling_text(ai_input_messages, date.today())
            if sched_day_iso:
                logger.info(
                    "M18 scheduling deterministic parse thread_id=%s day=%s time=%s",
                    ctx.thread.id, sched_day_iso, sched_time_str,
                )
                result = self._try_schedule_and_flow(ctx, state, sched_day_iso, sched_time_str, "")
                if result is not None:
                    return result

        # ── Deterministic vehicle catalog lookup (pre-AI) ─────────────────
        # Search across all recent messages (not just current burst) so a
        # vehicle name from a prior turn is still recognised.
        all_recent_text = " ".join(
            list(event.recent_user_messages or []) + list(ai_input_messages)
        )
        pre_detected_vehicle = lookup_vehicle(all_recent_text)
        if pre_detected_vehicle:
            logger.info(
                "M18 vehicle catalog hit thread_id=%s alias=%r tipo=%s confidence=%s",
                ctx.thread.id, pre_detected_vehicle.matched_alias,
                pre_detected_vehicle.tipo_vehiculo, pre_detected_vehicle.confidence,
            )
            # Proactively create a candidate when catalog fires but none exists yet.
            if not ctx.candidates:
                self._create_candidate_from_catalog(
                    ctx, state, pre_detected_vehicle, source_text=all_recent_text
                )

        # ── Pre-AI zone detection ──────────────────────────────────────────
        # Look up zone in DB from text so zone_group is deterministic (not
        # guessed) before the AI runs. Also populates state.home_zone_group.
        if not state.home_zone_detail:
            zone_hit = self._extract_zone_from_text(all_recent_text)
            if zone_hit:
                state.home_zone_detail = zone_hit.zone_detail
                if zone_hit.zone_group:
                    state.home_zone_group = zone_hit.zone_group

        # Normalise zone_group when detail is known but group is still blank.
        self._normalize_zone_from_db(ctx, state)

        # ── Pre-AI deterministic price quote ──────────────────────────────
        real_price_quote = self._compute_price_quote(ctx, state)

        messages_for_ai = self._build_ai_messages(
            ctx, event, ai_input_messages,
            pre_detected_vehicle=pre_detected_vehicle,
            real_price_quote=real_price_quote,
        )

        try:
            ai_raw = self._call_openai(messages_for_ai)
            decision = json.loads(ai_raw)
        except Exception as exc:
            logger.warning("M18 AI call failed thread_id=%s: %s", ctx.thread.id, exc)
            decision = {
                "intent": "OTHER",
                "reply": "Disculpá la demora, ya te ayudamos.",
                "needs_human": True,
                "lead_flag": None,
                "extracted": {},
                "candidate": {"action": "none"},
            }

        # Apply extracted data to thread state
        extracted = decision.get("extracted") or {}
        self._apply_extracted(ctx, state, extracted)

        # Normalise zone_group again in case AI extracted a zone_detail.
        self._normalize_zone_from_db(ctx, state)

        # Candidate updates
        self._apply_candidate(ctx, decision.get("candidate") or {})

        # ── Catalog enforcement (post-AI) ─────────────────────────────────
        # If catalog found a vehicle, override the candidate's tipo_vehiculo
        # regardless of what the AI returned.  marca/modelo are only set if
        # not already filled (AI may have added year / version details).
        if pre_detected_vehicle:
            self._enforce_catalog_vehicle(ctx, pre_detected_vehicle)

        # Sync state zone onto focus candidate when candidate fields are blank.
        focus_after = self._focus_candidate(ctx)
        if focus_after:
            if state.home_zone_group and not focus_after.zone_group:
                focus_after.zone_group = state.home_zone_group
            if state.home_zone_detail and not focus_after.zone_detail:
                focus_after.zone_detail = state.home_zone_detail

        # Sync year onto focus candidate deterministically if AI missed it.
        if focus_after and focus_after.anio is None:
            year_hit = _extract_year_from_text(all_recent_text)
            if year_hit:
                focus_after.anio = year_hit

        # Recompute price after all extractions have run.
        real_price_quote = self._compute_price_quote(ctx, state)

        # Lead flag — only allowed values, engine validates
        new_flag = decision.get("lead_flag")
        flag_accepted = new_flag and new_flag in _ALLOWED_FLAGS
        # Guard: never set PRESUPUESTO_ENVIADO without a deterministic price.
        if flag_accepted and new_flag == "PRESUPUESTO_ENVIADO" and real_price_quote is None:
            logger.warning(
                "M18 blocking PRESUPUESTO_ENVIADO — no deterministic price thread_id=%s",
                ctx.thread.id,
            )
            flag_accepted = False

        # BUG-3 guard: never advance QUALIFYING → ACEPTADO without a deterministic quote.
        # Acceptance words ("dale", "sí") in QUALIFYING stage must not move to SCHEDULING
        # when we have no confirmed price — they could be accepting a hallucinated amount.
        if (
            flag_accepted
            and new_flag == "ACEPTADO"
            and state.last_stage in (STAGE_QUALIFYING, None)
            and real_price_quote is None
        ):
            logger.warning(
                "M18 blocking ACEPTADO in QUALIFYING — no deterministic quote thread_id=%s",
                ctx.thread.id,
            )
            flag_accepted = False
            focus_c = self._focus_candidate(ctx)
            if state.home_zone_group and not state.home_zone_detail:
                decision["reply"] = (
                    f"¿En qué barrio de {state.home_zone_group} está el auto? "
                    "Así te paso el valor exacto."
                )
            elif not (focus_c and focus_c.tipo_vehiculo):
                decision["reply"] = (
                    "Para avanzar necesito saber qué tipo de vehículo es. "
                    "¿Es un auto, SUV u otro tipo?"
                )
            elif not (state.home_zone_group or state.home_zone_detail):
                decision["reply"] = (
                    "Para avanzar necesito saber en qué zona está el auto. "
                    "¿Me podés indicar el barrio?"
                )
            else:
                decision["reply"] = (
                    "Antes de avanzar necesito confirmar el precio. "
                    "Falta información de la zona o el vehículo."
                )

        if flag_accepted and new_flag != lead.flag:
            lead.flag = new_flag
            if new_flag == "PRESUPUESTO_ENVIADO":
                state.last_stage = STAGE_QUOTED
            elif new_flag == "ACEPTADO":
                state.last_stage = STAGE_SCHEDULING

        # Human escalation
        if decision.get("needs_human"):
            lead.necesita_humano = True
            state.needs_human = True
            state.last_stage = STAGE_HUMAN

        # ── Deterministic quote override ───────────────────────────────────
        # If pricing succeeded and the conversation is still in qualifying
        # stage, force the quote regardless of what the AI decided to do.
        # This guarantees the price is never blocked or replaced by invented
        # requirements (e.g. "necesito el precio del vehículo").
        if (
            real_price_quote is not None
            and lead.flag not in ("PRESUPUESTO_ENVIADO", "ACEPTADO")
            and state.last_stage in (STAGE_QUALIFYING, None)
            and not state.needs_human
        ):
            logger.info(
                "M18 deterministic quote force thread_id=%s total=%s",
                ctx.thread.id, real_price_quote.precio_total,
            )
            lead.flag = "PRESUPUESTO_ENVIADO"
            state.last_stage = STAGE_QUOTED
            ai_reply = str(decision.get("reply") or "")
            total_str = f"${real_price_quote.precio_total:,.0f}".replace(",", ".")
            # Inject price into reply when AI omitted it.
            if str(real_price_quote.precio_total) not in ai_reply and total_str not in ai_reply:
                decision["reply"] = (
                    ai_reply
                    + f"\n\nEl precio de la revisión es {total_str} "
                    f"(base ${real_price_quote.precio_base:,.0f}".replace(",", ".")
                    + f" + viáticos ${real_price_quote.viaticos:,.0f})".replace(",", ".")
                )

        # Schedule check + flow when in SCHEDULING stage
        stage = state.last_stage
        if stage == STAGE_SCHEDULING and not state.needs_human and not state.flow_booking_token:
            # Deterministic parser runs first to prevent AI hallucinating dates.
            # This handles the ráfaga case where "si" + "mañana 12hs" arrive together
            # and the AI transitions from QUOTED→SCHEDULING in the same turn.
            det_day, det_time = _parse_scheduling_text(ai_input_messages, date.today())
            pday = det_day or state.preferred_day
            ptime = det_time or extracted.get("preferred_time_str") or state.preferred_time
            if pday:
                result = self._try_schedule_and_flow(ctx, state, pday, ptime, decision.get("reply") or "")
                if result is not None:
                    return result

        reply = str(decision.get("reply") or "")
        # BUG-2 guard: if AI invented a price and we have no deterministic quote, scrub it.
        reply = self._scrub_invented_price(reply, real_price_quote)
        if not reply:
            self.db.commit()
            return _out("replied", detail="no_reply_text")

        # All paths: _send_text_to_wa commits everything atomically after the send.
        # For PRESUPUESTO_ENVIADO (rule B), this means the flag is only committed
        # after the WhatsApp send succeeds — which is the correct ordering.
        sent_id = self._send_text_to_wa(ctx, reply)
        return _out("replied", wa_message_id=sent_id)

    # ── Deterministic QUOTED acceptance ───────────────────────────────────

    def _handle_quoted_acceptance(
        self, ctx: _Context, state: WhatsAppThreadState,
    ) -> ConversationHandleOut:
        """Client said yes after receiving a quote.

        Transitions: flag → ACEPTADO, stage → SCHEDULING.
        Does not create a revision, does not send the Flow, does not ask for
        buyer/seller/address.  Just asks for preferred day and time.
        All DB writes are committed inside _send_text_to_wa so that
        last_processed_inbound_wa_message_id is only persisted once the
        outbound message is durably stored.
        """
        lead = ctx.lead
        assert lead is not None

        lead.flag = "ACEPTADO"
        state.last_stage = STAGE_SCHEDULING
        # lead.estado stays CONSULTA_NUEVA
        # state.current_revision_id stays null
        # state.needs_human stays false

        customer = (state.customer_name or "").strip() or (lead.nombre or "").strip() or "cliente"
        reply = (
            f"Genial, {customer}! "
            "¿Qué día y horario te viene mejor para la revisión?"
        )
        sent_id = self._send_text_to_wa(ctx, reply)
        return _out("replied", wa_message_id=sent_id)

    # ── Schedule check + flow button ─────────────────────────────────────

    def _try_schedule_and_flow(
        self,
        ctx: _Context,
        state: WhatsAppThreadState,
        preferred_day_iso: str,
        preferred_time_str: str | None,
        ai_reply: str,
    ) -> ConversationHandleOut | None:
        try:
            preferred_day = date.fromisoformat(preferred_day_iso)
        except (ValueError, TypeError):
            return None

        try:
            preferred_time_obj = time.fromisoformat(preferred_time_str) if preferred_time_str else time(9, 0)
        except (ValueError, TypeError):
            preferred_time_obj = time(9, 0)

        zone_parts = [state.home_zone_detail, state.home_zone_group, "Buenos Aires, Argentina"]
        address = ", ".join(p for p in zone_parts if p)

        sched_in = ScheduleCheckIn(
            address=address,
            preferred_day=preferred_day,
            preferred_time=preferred_time_obj,
            zone_group=state.home_zone_group,
            zone_detail=state.home_zone_detail,
        )

        try:
            sched_out = self._schedule.check(sched_in)
        except Exception as exc:
            logger.warning("M18 schedule check failed thread_id=%s: %s", ctx.thread.id, exc)
            return None

        state.preferred_day = preferred_day_iso
        state.preferred_time = preferred_time_obj.strftime("%H:%M")

        if sched_out.valid:
            flow_id = (self.settings.whatsapp_flow_id or "").strip()
            flow_token = f"{ctx.thread.id}-{int(_time.time())}"
            state.flow_booking_token = flow_token
            # last_stage stays SCHEDULING — flow_booking_token signals the form was sent.
            # Stage only advances to BOOKED when the form response arrives.
            self.db.commit()

            if not flow_id:
                logger.error("M18 WHATSAPP_FLOW_ID not set — sending text fallback")
                self.db.commit()
                fallback = ai_reply or "Perfecto, tenemos disponibilidad. ¡Ya te confirmo!"
                sent_id = self._send_text_to_wa(ctx, fallback)
                return _out("replied", wa_message_id=sent_id)

            body = (
                "Perfecto, ese horario está disponible 🎉 "
                "Para confirmar el turno, completá el formulario con tus datos."
            )
            try:
                sent_id = self._send_flow_button(ctx, body, flow_token)
                return _out("flow_button_sent", wa_message_id=sent_id)
            except Exception as exc:
                logger.error("M18 flow button send failed thread_id=%s: %s", ctx.thread.id, exc)
                state.flow_booking_token = None
                state.last_stage = STAGE_SCHEDULING
                self.db.commit()
                return None
        else:
            self.db.commit()
            slots = sched_out.suggested_slots[:3]
            if slots:
                msg = (
                    f"Para ese horario no hay disponibilidad. "
                    f"Horarios disponibles el {preferred_day.strftime('%d/%m')}: "
                    + ", ".join(slots)
                )
            else:
                reasons = "; ".join(sched_out.reasons[:2]) if sched_out.reasons else "sin disponibilidad"
                msg = f"Para ese día no tenemos horarios disponibles ({reasons}). ¿Tenés otro día preferido?"
            sent_id = self._send_text_to_wa(ctx, msg)
            return _out("replied", wa_message_id=sent_id)

    # ── AI prompt ─────────────────────────────────────────────────────────

    def _build_ai_messages(
        self,
        ctx: _Context,
        event: ConversationHandleIn,
        ai_input_messages: list[str],
        pre_detected_vehicle: VehicleMatch | None = None,
        real_price_quote: "PricingQuote | None" = None,
    ) -> list[dict]:
        lead = ctx.lead
        assert lead is not None
        state = ctx.state
        assert state is not None
        focus = self._focus_candidate(ctx)

        # Pre-calculated price (injected from caller — never computed inline)
        precio_info = ""
        if real_price_quote is not None:
            q = real_price_quote
            precio_info = (
                f"\n\nPRECIO CALCULADO (usalo exactamente si vas a cotizar): "
                f"${q.precio_total:,.0f} (base ${q.precio_base:,.0f} + viáticos ${q.viaticos:,.0f})"
            ).replace(",", ".")

        # History: prefer n8n-provided arrays, fall back to DB messages
        history_lines: list[str] = []
        if event.recent_outbound_replies or event.recent_user_messages:
            # Interleave outbound and user messages in approximate order
            # n8n provides them in order already; build a simple labeled list
            for msg in event.recent_outbound_replies[-5:]:
                history_lines.append(f"BOT: {msg[:200]}")
            for msg in event.recent_user_messages[-10:]:
                history_lines.append(f"CLIENTE: {msg[:200]}")
        else:
            for m in ctx.db_messages[-15:]:
                direction = "CLIENTE" if m.direction == "in" else "BOT"
                txt = (m.text or "[multimedia]").replace("\n", " ")[:200]
                history_lines.append(f"{direction}: {txt}")

        history = "\n".join(history_lines) if history_lines else "(sin historial)"

        stage = state.last_stage or STAGE_QUALIFYING
        flag = lead.flag or "PRESUPUESTANDO"
        customer = state.customer_name or lead.nombre or "desconocido"
        zone = state.home_zone_detail or state.home_zone_group or "desconocida"
        vehicle_txt = "ninguno"
        if focus:
            parts = [focus.marca, focus.modelo, str(focus.anio) if focus.anio else None]
            vehicle_txt = " ".join(p for p in parts if p) or focus.label or "sin datos"

        # Ráfaga context
        if len(ai_input_messages) > 1:
            rafaga_note = f"(el cliente envió {len(ai_input_messages)} mensajes seguidos — respondé a todos juntos)"
        else:
            rafaga_note = ""

        # Pre-detected vehicle block (injected when catalog matched)
        detected_vehicle_block = ""
        if pre_detected_vehicle:
            detected_vehicle_block = (
                f"\n\nVEHÍCULO DETECTADO DETERMINÍSTICAMENTE (catálogo, no modificar):\n"
                f"  Marca: {pre_detected_vehicle.marca}\n"
                f"  Modelo: {pre_detected_vehicle.modelo}\n"
                f"  tipo_vehiculo: {pre_detected_vehicle.tipo_vehiculo}\n"
                f"  → Usar EXACTAMENTE este tipo_vehiculo en el campo candidate.tipo_vehiculo."
            )

        # Candidate id for update actions (AI must reference it explicitly)
        focus_id_block = ""
        if focus and focus.id:
            focus_id_block = f"\n- ID candidato en foco: {focus.id} (usalo en candidate.id cuando hagas action=update)"

        system_prompt = f"""Sos el asistente de Ridecheck, servicio de revisión pre-compra de autos en Argentina. Ayudás a coordinar inspecciones antes de que el cliente compre un vehículo usado.

ESTADO ACTUAL:
- Etapa: {stage}
- Flag del lead: {flag}
- Nombre del cliente: {customer}
- Zona: {zone}
- Vehículo en foco: {vehicle_txt}{focus_id_block}{precio_info}{detected_vehicle_block}

HISTORIAL RECIENTE:
{history}

REGLAS DE NEGOCIO:
1. PRESUPUESTANDO → colectá tipo de vehículo y zona/ciudad. Eso es todo lo que necesitás para cotizar. Tipo de vendedor (agencia/particular) es útil pero NO bloquea la cotización; si el cliente lo ofrece, registralo, pero no lo pidas como requisito.
2. Cuando tenés tipo_vehiculo + zona Y aparece "PRECIO CALCULADO" arriba → enviá la cotización YA con ese precio exacto y seteá lead_flag="PRESUPUESTO_ENVIADO". No preguntes nada más. Si no hay PRECIO CALCULADO, preguntá solo qué dato falta (tipo de vehículo o zona).
3. Aceptación ("sí", "dale", "ok", "me sirve", "avanzamos") → lead_flag="ACEPTADO".
4. Etapa SCHEDULING → preguntá qué día y horario le viene mejor.
5. Derivá a humano si hay queja, solicitud especial o no podés resolver → needs_human=true.
6. NUNCA uses lead_flag="PERDIDO", "BUSCANDO_AUTO", ni "RECOMPRA".
7. Respondé en español, registro informal argentino (voseo), conciso.
8. NUNCA inventes ni calcules precios. El único precio válido es el que aparece en PRECIO CALCULADO.
9. NUNCA preguntes el precio de venta, valor o tasación del vehículo. Ridecheck NO necesita eso para cotizar la inspección.
10. Si la zona no está disponible en tu sistema, pedí confirmación de una zona/barrio cercano. NUNCA pidas precio del vehículo ni datos de la venta.

TIPOS DE VEHÍCULO VÁLIDOS: AUTO, SUV_4X4_DEPORTIVO, SUV/4x4, CLASICO, MOTO, ESCANEO_MOTOR

Respondé SOLO con JSON válido:
{{
  "intent": "GREETING|QUALIFYING|QUOTE_SENT|ACCEPTANCE|SCHEDULING|OBJECTION|ESCALATE|OTHER",
  "reply": "texto en español",
  "lead_flag": null,
  "needs_human": false,
  "extracted": {{
    "customer_name": null,
    "zone_detail": null,
    "preferred_time_str": null,
    "tipo_vehiculo": null,
    "vendedor_tipo": null
  }},
  "candidate": {{
    "action": "none",
    "id": null,
    "marca": null,
    "modelo": null,
    "version_text": null,
    "anio": null,
    "tipo_vehiculo": null,
    "status": "current_focus"
  }}
}}"""

        # Build the user turn: ráfaga messages
        if len(ai_input_messages) == 1:
            user_content = f"MENSAJE DEL CLIENTE: {ai_input_messages[0]}"
        else:
            lines = "\n".join(f"- {m}" for m in ai_input_messages)
            user_content = f"MENSAJES SIN RESPONDER {rafaga_note}:\n{lines}"

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

    # ── OpenAI HTTP call (no sdk dependency) ─────────────────────────────

    def _call_openai(self, messages: list[dict]) -> str:
        api_key = (self.settings.openai_api_key or "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")

        model = (self.settings.openai_chat_model or "gpt-4o-mini").strip()

        body = json.dumps({
            "model": model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
            "max_tokens": 1200,
        }).encode("utf-8")

        req = urlrequest.Request(
            "https://api.openai.com/v1/chat/completions",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            with urlrequest.urlopen(req, timeout=30) as resp:
                response_body = resp.read().decode("utf-8", errors="replace")
        except urlerror.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI chat failed HTTP {exc.code}: {err_body[:400]}") from exc

        payload = json.loads(response_body)
        return payload["choices"][0]["message"]["content"]

    # ── Context helpers ───────────────────────────────────────────────────

    def _load_context(self, thread_id: int) -> _Context | None:
        thread = self.db.get(WhatsAppThread, thread_id)
        if thread is None:
            return None
        contact = self.db.get(WhatsAppContact, thread.contact_id)
        if contact is None:
            return None

        lead: Lead | None = None
        if thread.lead_id:
            lead = self.db.get(Lead, thread.lead_id)

        candidates = list(
            self.db.execute(
                select(WhatsAppThreadCandidate)
                .where(WhatsAppThreadCandidate.thread_id == thread_id)
                .order_by(WhatsAppThreadCandidate.updated_at.desc(), WhatsAppThreadCandidate.id.desc())
            ).scalars().all()
        )

        db_messages = list(
            self.db.execute(
                select(WhatsAppMessage)
                .where(WhatsAppMessage.thread_id == thread_id)
                .order_by(WhatsAppMessage.timestamp.asc(), WhatsAppMessage.id.asc())
                .limit(20)
            ).scalars().all()
        )

        return _Context(
            thread=thread,
            contact=contact,
            lead=lead,
            state=thread.state,
            candidates=candidates,
            db_messages=db_messages,
        )

    def _get_or_create_state(self, ctx: _Context) -> WhatsAppThreadState:
        if ctx.state is not None:
            return ctx.state
        state = WhatsAppThreadState(thread_id=ctx.thread.id)
        ctx.thread.state = state
        self.db.add(state)
        self.db.flush()
        ctx.state = state
        return state

    def _focus_candidate(self, ctx: _Context) -> WhatsAppThreadCandidate | None:
        state = ctx.state
        if state and state.current_focus_candidate_id:
            for c in ctx.candidates:
                if c.id == state.current_focus_candidate_id:
                    return c
        for c in ctx.candidates:
            if c.status == "current_focus":
                return c
        return ctx.candidates[0] if ctx.candidates else None

    def _apply_extracted(self, ctx: _Context, state: WhatsAppThreadState, extracted: dict) -> None:
        if extracted.get("customer_name"):
            state.customer_name = extracted["customer_name"]
            if ctx.lead and not ctx.lead.nombre:
                ctx.lead.nombre = extracted["customer_name"].split()[0]
        if extracted.get("zone_detail"):
            state.home_zone_detail = extracted["zone_detail"]
        # zone_group is intentionally NOT read from AI — DB normalization always
        # sets the canonical value in _normalize_zone_from_db.
        if extracted.get("preferred_day_iso"):
            raw_day = str(extracted["preferred_day_iso"]).strip()
            try:
                date.fromisoformat(raw_day)  # validate before storing
                state.preferred_day = raw_day
            except (ValueError, TypeError):
                logger.warning(
                    "M18 ignoring malformed preferred_day_iso=%r thread_id=%s",
                    raw_day, ctx.thread.id,
                )
        if extracted.get("preferred_time_str"):
            raw_time = str(extracted["preferred_time_str"]).strip()
            if re.match(r"^\d{1,2}:\d{2}$", raw_time):  # must be "HH:MM", max 5 chars
                state.preferred_time = raw_time
            else:
                logger.warning(
                    "M18 ignoring malformed preferred_time_str=%r thread_id=%s",
                    raw_time, ctx.thread.id,
                )

    def _apply_candidate(self, ctx: _Context, candidate_data: dict) -> None:
        action = candidate_data.get("action", "none")
        if action == "none" or not candidate_data:
            return
        state = ctx.state

        if action == "create":
            fields = {
                k: candidate_data[k]
                for k in ("marca", "modelo", "version_text", "anio", "tipo_vehiculo",
                           "zone_group", "zone_detail", "direccion_texto")
                if candidate_data.get(k) is not None
            }
            status = candidate_data.get("status") or "mentioned"
            candidate = WhatsAppThreadCandidate(thread_id=ctx.thread.id, status=status, **fields)
            self.db.add(candidate)
            self.db.flush()
            if status == "current_focus" and state:
                for c in ctx.candidates:
                    if c.status == "current_focus":
                        c.status = "mentioned"
                state.current_focus_candidate_id = candidate.id
            ctx.candidates.insert(0, candidate)

        elif action == "update":
            raw_id = candidate_data.get("id")
            if raw_id:
                target_id = int(raw_id)
                target = next((c for c in ctx.candidates if c.id == target_id), None)
            else:
                # AI omitted id — fall back to the current focus candidate.
                target = self._focus_candidate(ctx)
                target_id = target.id if target else None
            if target is None:
                return
            for k in ("marca", "modelo", "version_text", "anio", "tipo_vehiculo",
                       "zone_group", "zone_detail", "direccion_texto"):
                if candidate_data.get(k) is not None:
                    setattr(target, k, candidate_data[k])
            new_status = candidate_data.get("status")
            if new_status:
                if new_status == "current_focus" and state:
                    for c in ctx.candidates:
                        if c.status == "current_focus" and c.id != target_id:
                            c.status = "mentioned"
                    state.current_focus_candidate_id = target_id
                target.status = new_status

    def _enforce_catalog_vehicle(self, ctx: _Context, match: VehicleMatch) -> None:
        """Override candidate tipo_vehiculo with the catalog result (post-AI).

        The catalog is always authoritative for tipo_vehiculo.  marca/modelo
        are only written if the candidate field is currently empty, so any
        version/trim detail added by the AI is preserved.
        """
        focus = self._focus_candidate(ctx)
        if focus is None:
            # AI returned action=none but catalog hit — create the candidate now.
            self._create_candidate_from_catalog(ctx, ctx.state, match)
            return
        if focus.tipo_vehiculo != match.tipo_vehiculo:
            logger.info(
                "M18 catalog override thread_id=%s candidate=%s tipo %r→%r",
                ctx.thread.id, focus.id, focus.tipo_vehiculo, match.tipo_vehiculo,
            )
            focus.tipo_vehiculo = match.tipo_vehiculo
        if not focus.marca:
            focus.marca = match.marca
        if not focus.modelo:
            focus.modelo = match.modelo

    # ── Deterministic helper methods ──────────────────────────────────────

    def _create_candidate_from_catalog(
        self,
        ctx: _Context,
        state: "WhatsAppThreadState | None",
        match: VehicleMatch,
        source_text: str = "",
    ) -> None:
        anio = _extract_year_from_text(source_text) if source_text else None
        candidate = WhatsAppThreadCandidate(
            thread_id=ctx.thread.id,
            marca=match.marca,
            modelo=match.modelo,
            tipo_vehiculo=match.tipo_vehiculo,
            anio=anio,
            zone_group=state.home_zone_group if state else None,
            zone_detail=state.home_zone_detail if state else None,
            status="current_focus",
        )
        self.db.add(candidate)
        self.db.flush()
        if state:
            state.current_focus_candidate_id = candidate.id
        ctx.candidates.insert(0, candidate)
        logger.info(
            "M18 proactive candidate created thread_id=%s candidate=%s tipo=%s anio=%s",
            ctx.thread.id, candidate.id, match.tipo_vehiculo, anio,
        )

    def _extract_zone_from_text(self, text: str) -> "ViaticosZone | None":
        from sqlalchemy import select as _select
        normalized_text = " ".join(text.lower().split())
        zones = list(self.db.execute(_select(ViaticosZone)).scalars().all())

        # CABA synonym fast-path: "capital federal", "ciudad autónoma de buenos aires" etc.
        # Checked before the zone_detail loop because these strings don't appear
        # as zone_detail values in the DB.  "caba" / "CABA" are NOT in this set —
        # they're handled by the zone_detail="CABA" sentinel row in the loop below.
        for synonym in _CABA_SYNONYMS:
            if synonym in normalized_text:
                # Return the CABA sentinel row from the already-loaded zones list.
                for z in zones:
                    if (
                        " ".join((z.zone_group or "").lower().split()) == "caba"
                        and " ".join((z.zone_detail or "").lower().split()) == "caba"
                    ):
                        return z
                # Defensive sentinel if migration hasn't run yet.
                from types import SimpleNamespace
                return SimpleNamespace(  # type: ignore[return-value]
                    zone_group=_CABA_CANONICAL_GROUP,
                    zone_detail=_CABA_CANONICAL_DETAIL,
                    viaticos=0,
                )

        # Longer detail strings first to avoid partial matches shadowing full names.
        # The zone_detail="CABA" sentinel row (len=4) is checked here too, matching
        # "caba" as a substring in any user message that mentions CABA explicitly.
        zones_sorted = sorted(zones, key=lambda z: len(z.zone_detail or ""), reverse=True)
        for zone in zones_sorted:
            if not zone.zone_detail:
                continue
            zone_norm = " ".join(zone.zone_detail.lower().split())
            if zone_norm in normalized_text:
                return zone

        # Pre-AI group detection: if text mentions a bare zone_group name (e.g. "Oeste")
        # that has no matching zone_detail, return a sentinel so the caller can set
        # home_zone_group and the ACEPTADO guard can ask for the specific barrio.
        seen_groups: set[str] = set()
        for zone in zones:
            if zone.zone_group:
                g_norm = " ".join(zone.zone_group.lower().split())
                if g_norm in seen_groups:
                    continue
                seen_groups.add(g_norm)
                if g_norm in normalized_text:
                    group_zones = [z for z in zones if z.zone_group == zone.zone_group and z.zone_detail is None]
                    if group_zones:
                        return group_zones[0]
                    from types import SimpleNamespace
                    return SimpleNamespace(zone_group=zone.zone_group, zone_detail=None, viaticos=None)  # type: ignore[return-value]
        return None

    def _normalize_zone_from_db(self, ctx: _Context, state: "WhatsAppThreadState | None") -> None:
        if not state or not state.home_zone_detail:
            return

        # Normalize CABA synonyms BEFORE the DB lookup so that all city-level
        # CABA inputs map to the canonical sentinel row (zone_detail="CABA").
        # "CABA" itself is NOT a synonym — it's the canonical value and resolves
        # via the normal DB lookup below.
        if _is_caba_synonym(state.home_zone_detail):
            logger.info(
                "M18 CABA synonym normalized: %r → %r thread_id=%s",
                state.home_zone_detail, _CABA_CANONICAL_DETAIL,
                ctx.thread.id if ctx else "?",
            )
            state.home_zone_detail = _CABA_CANONICAL_DETAIL
            state.home_zone_group = _CABA_CANONICAL_GROUP
            return  # No DB lookup needed; canonical values are hardwired

        # DB is always authoritative for zone_group.  Overwrite whatever the AI
        # extracted (AI often puts the city name as zone_group instead of the
        # correct CABA/Norte/Oeste/Sur canonical value).
        zone = self._pricing.repository.find_zone_by_group_and_detail(
            db=self.db,
            zone_group=None,
            zone_detail=state.home_zone_detail,
        )
        if zone is not None:
            if zone.zone_group:
                state.home_zone_group = zone.zone_group
        else:
            # BUG-1: zone_detail might actually be a group name (e.g. "Oeste").
            # If it matches a known zone_group, promote it and clear zone_detail so
            # the engine knows to ask for a specific barrio.
            group_name = self._find_zone_group(state.home_zone_detail)
            if group_name:
                logger.info(
                    "M18 group-level zone detected: %r promoted to zone_group=%r thread_id=%s",
                    state.home_zone_detail, group_name, ctx.thread.id if ctx else "?",
                )
                state.home_zone_group = group_name
                state.home_zone_detail = None  # need a specific neighborhood

    def _find_zone_group(self, value: str) -> str | None:
        """Return canonical zone_group name if value matches one in the DB; else None."""
        from sqlalchemy import select as _select
        normalized = " ".join((value or "").lower().split())
        if not normalized:
            return None
        rows = self.db.execute(
            _select(ViaticosZone.zone_group).distinct()
        ).scalars().all()
        for g in rows:
            if g and " ".join(g.lower().split()) == normalized:
                return g
        return None

    def _scrub_invented_price(self, reply: str, real_price_quote: "PricingQuote | None") -> str:
        """BUG-2: Replace AI reply when no deterministic quote is available but the reply
        either contains an invented price OR promises to send a quote without the amount.

        This is a hard backend guard — prompt instructions alone are not reliable.
        """
        if real_price_quote is not None:
            return reply
        if _PRICE_RE.search(reply):
            logger.warning("M18 scrubbing AI-invented price from reply — no deterministic quote")
            return (
                "Necesito confirmar algunos datos antes de darte el precio exacto. "
                "¿Me podés indicar en qué barrio o zona de la ciudad está el auto?"
            )
        if _QUOTE_INTENT_RE.search(reply):
            logger.warning("M18 scrubbing AI quote-promise without amount — no deterministic quote")
            return (
                "Necesito confirmar algunos datos antes de darte el precio exacto. "
                "¿Me podés indicar en qué barrio o zona de la ciudad está el auto?"
            )
        return reply

    def _compute_price_quote(
        self, ctx: _Context, state: "WhatsAppThreadState | None"
    ) -> "PricingQuote | None":
        if not state:
            return None
        focus = self._focus_candidate(ctx)
        if not focus or not focus.tipo_vehiculo:
            return None
        if not (state.home_zone_group or state.home_zone_detail):
            return None
        try:
            q = self._pricing.quote(
                db=self.db,
                tipo_vehiculo=focus.tipo_vehiculo,
                zone_group=state.home_zone_group or "",
                zone_detail=state.home_zone_detail or "",
            )
            # Back-fill zone_group on state if the pricing lookup resolved it.
            if not state.home_zone_group and q.zone_group:
                state.home_zone_group = q.zone_group
            return q
        except PricingNotFoundError:
            return None

    # ── WhatsApp sends ────────────────────────────────────────────────────

    def _send_text_to_wa(self, ctx: _Context, text: str) -> str | None:
        from zoneinfo import ZoneInfo
        now_utc = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
        wa_id = ctx.contact.wa_id

        # Call the WhatsApp API BEFORE writing to the DB so that all session
        # state (including last_processed_inbound_wa_message_id) is only
        # committed after we know whether the send succeeded or failed.
        wa_message_id = None
        final_status = "failed"
        try:
            wa_message_id, _ = _send_whatsapp_cloud_text(to_wa_id=wa_id, text=text)
            final_status = "sent"
        except Exception as exc:
            logger.error("M18 send_text failed thread_id=%s: %s", ctx.thread.id, exc)

        outbound = WhatsAppMessage(
            thread_id=ctx.thread.id,
            wa_message_id=wa_message_id,
            direction="out",
            status=final_status,
            timestamp=now_utc,
            text=text,
        )
        self.db.add(outbound)
        ctx.thread.last_message_at = now_utc
        self.db.commit()
        reset_unanswered_alert(self.db, ctx.thread.id)
        self.db.commit()
        return wa_message_id

    def _send_flow_button(self, ctx: _Context, body_text: str, flow_token: str) -> str:
        flow_id = (self.settings.whatsapp_flow_id or "").strip()
        if not flow_id:
            raise RuntimeError("WHATSAPP_FLOW_ID not configured")

        from zoneinfo import ZoneInfo
        now_utc = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
        wa_id = ctx.contact.wa_id

        # Send first; raises on failure so the caller can revert state.
        wa_message_id, _ = _send_whatsapp_cloud_flow(
            to_wa_id=wa_id,
            flow_id=flow_id,
            flow_token=flow_token,
            body_text=body_text,
            cta_label="Completar datos",
        )

        outbound = WhatsAppMessage(
            thread_id=ctx.thread.id,
            wa_message_id=wa_message_id,
            direction="out",
            status="sent",
            timestamp=now_utc,
            text=body_text,
        )
        self.db.add(outbound)
        ctx.thread.last_message_at = now_utc
        self.db.commit()
        reset_unanswered_alert(self.db, ctx.thread.id)
        self.db.commit()
        return wa_message_id

    # ── Booking notification ──────────────────────────────────────────────

    def _send_booking_notification(self, thread_rev_id: int, crm_rev_id: int, lead_id: int, **kwargs) -> None:
        from sqlalchemy import text as sql_text
        from ..services.resend_email import send_booking_notification

        settings = self.settings
        if not settings.resend_api_key:
            logger.warning("M18 RESEND_API_KEY missing — booking notification skipped")
            return

        try:
            row = self.db.execute(
                sql_text("SELECT notification_sent_at FROM thread_revisions WHERE id = :id"),
                {"id": thread_rev_id},
            ).first()
            if row and row.notification_sent_at is not None:
                logger.info("M18 booking notification already sent thread_rev=%s", thread_rev_id)
                return
        except Exception as exc:
            logger.warning("M18 notification dedup check failed: %s", exc)

        precio_base = precio_viaticos = precio_total = "Sin dato"
        try:
            crm_rev = self.db.get(Revision, crm_rev_id)
            if crm_rev:
                if crm_rev.precio_base is not None:
                    precio_base = str(crm_rev.precio_base)
                if crm_rev.viaticos is not None:
                    precio_viaticos = str(crm_rev.viaticos)
                if crm_rev.precio_total is not None:
                    precio_total = str(crm_rev.precio_total)
        except Exception as exc:
            logger.warning("M18 could not fetch CRM revision prices: %s", exc)

        def _f(v) -> str:
            s = str(v).strip() if v is not None else ""
            return s if s else "Sin dato"

        ok = send_booking_notification(
            api_key=settings.resend_api_key,
            from_email=settings.internal_booking_email_from,
            to_email=settings.internal_booking_email_to,
            lead_id=lead_id,
            revision_id=crm_rev_id,
            buyer_name=_f(kwargs.get("buyer_name")),
            buyer_phone=_f(kwargs.get("buyer_phone")),
            buyer_email=_f(kwargs.get("buyer_email")),
            source="whatsapp",
            marca=_f(kwargs.get("marca")),
            modelo=_f(kwargs.get("modelo")),
            anio=_f(kwargs.get("anio")),
            tipo_vehiculo=_f(kwargs.get("tipo_vehiculo")),
            zone_group=_f(kwargs.get("zone_group")),
            zone_detail=_f(kwargs.get("zone_detail")),
            address=_f(kwargs.get("address")),
            seller_type=_f(kwargs.get("seller_type")),
            seller_name=_f(kwargs.get("seller_name")),
            scheduled_date=_f(kwargs.get("scheduled_date")),
            scheduled_time=_f(kwargs.get("scheduled_time")),
            precio_base=precio_base,
            viaticos=precio_viaticos,
            precio_total=precio_total,
        )

        if ok:
            try:
                self.db.execute(
                    sql_text("UPDATE thread_revisions SET notification_sent_at = NOW() WHERE id = :id"),
                    {"id": thread_rev_id},
                )
                self.db.commit()
                logger.info("M18 notification sent thread_rev=%s crm_rev=%s", thread_rev_id, crm_rev_id)
            except Exception as exc:
                logger.warning("M18 could not mark notification_sent_at: %s", exc)
        else:
            logger.error("M18 notification send failed thread_rev=%s", thread_rev_id)
