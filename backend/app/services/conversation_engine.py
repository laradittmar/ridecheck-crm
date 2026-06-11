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
import secrets
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from urllib import error as urlerror, request as urlrequest

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Lead,
    Revision,
    ThreadRevision,
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
from ..services.pricing import PricingNotFoundError, PricingService
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

        # Mark as processing immediately to prevent concurrent re-entry
        state = self._get_or_create_state(ctx)
        state.last_processed_inbound_wa_message_id = event.wa_message_id
        self.db.commit()

        # No lead linked — cannot make business decisions
        if ctx.lead is None:
            logger.info("M18 thread_id=%s no linked lead", event.thread_id)
            return _out("no_lead")

        # Human takeover active: AI is suppressed
        if state.needs_human:
            logger.info("M18 thread_id=%s needs_human — AI suppressed", event.thread_id)
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

        # ── Deterministic vehicle catalog lookup (pre-AI) ─────────────────
        # Scan all user messages for a known vehicle alias. If found, we
        # inject the result into the AI prompt AND enforce it after AI output
        # so tipo_vehiculo is never guessed/hallucinated.
        combined_user_text = " ".join(ai_input_messages)
        pre_detected_vehicle = lookup_vehicle(combined_user_text)
        if pre_detected_vehicle:
            logger.info(
                "M18 vehicle catalog hit thread_id=%s alias=%r tipo=%s confidence=%s",
                ctx.thread.id, pre_detected_vehicle.matched_alias,
                pre_detected_vehicle.tipo_vehiculo, pre_detected_vehicle.confidence,
            )

        messages_for_ai = self._build_ai_messages(
            ctx, event, ai_input_messages,
            pre_detected_vehicle=pre_detected_vehicle,
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

        # Candidate updates
        self._apply_candidate(ctx, decision.get("candidate") or {})

        # ── Catalog enforcement (post-AI) ─────────────────────────────────
        # If catalog found a vehicle, override the candidate's tipo_vehiculo
        # regardless of what the AI returned.  marca/modelo are only set if
        # not already filled (AI may have added year / version details).
        if pre_detected_vehicle:
            self._enforce_catalog_vehicle(ctx, pre_detected_vehicle)

        # Lead flag — only allowed values, engine validates
        new_flag = decision.get("lead_flag")
        flag_accepted = new_flag and new_flag in _ALLOWED_FLAGS
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
            self.db.commit()

        # Schedule check + flow when in SCHEDULING stage
        stage = state.last_stage
        if stage == STAGE_SCHEDULING and not state.needs_human and not state.flow_booking_token:
            pday = extracted.get("preferred_day_iso") or state.preferred_day
            ptime = extracted.get("preferred_time_str") or state.preferred_time
            if pday:
                result = self._try_schedule_and_flow(ctx, state, pday, ptime, decision.get("reply") or "")
                if result is not None:
                    return result

        reply = str(decision.get("reply") or "")
        if not reply:
            self.db.commit()
            return _out("replied", detail="no_reply_text")

        # For PRESUPUESTO_ENVIADO: send FIRST, commit flag AFTER (rule B)
        if flag_accepted and new_flag == "PRESUPUESTO_ENVIADO":
            sent_id = self._send_text_to_wa(ctx, reply)
            self.db.commit()
            return _out("replied", wa_message_id=sent_id)

        self.db.commit()
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
            state.last_stage = STAGE_FLOW_SENT
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
    ) -> list[dict]:
        lead = ctx.lead
        assert lead is not None
        state = ctx.state
        assert state is not None
        focus = self._focus_candidate(ctx)

        # Pre-calculated price (inject if available)
        precio_info = ""
        if focus and focus.tipo_vehiculo and (state.home_zone_group or state.home_zone_detail):
            try:
                q = self._pricing.quote(
                    db=self.db,
                    tipo_vehiculo=focus.tipo_vehiculo,
                    zone_group=state.home_zone_group or "",
                    zone_detail=state.home_zone_detail or "",
                )
                precio_info = (
                    f"\n\nPRECIO CALCULADO (usalo si vas a cotizar): "
                    f"${q.precio_total:,.0f} (base ${q.precio_base:,.0f} + viáticos ${q.viaticos:,.0f})"
                ).replace(",", ".")
            except PricingNotFoundError:
                pass

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

        system_prompt = f"""Sos el asistente de Ridecheck, servicio de revisión pre-compra de autos en Argentina. Ayudás a coordinar inspecciones antes de que el cliente compre un vehículo usado.

ESTADO ACTUAL:
- Etapa: {stage}
- Flag del lead: {flag}
- Nombre del cliente: {customer}
- Zona: {zone}
- Vehículo en foco: {vehicle_txt}{precio_info}{detected_vehicle_block}

HISTORIAL RECIENTE:
{history}

REGLAS DE NEGOCIO:
1. PRESUPUESTANDO → colectá tipo de vehículo, zona/ciudad y tipo de vendedor (agencia/particular). Podés pedir marca/modelo/año también.
2. Cuando tenés tipo_vehiculo + zona → cotizá. Si hay precio arriba, incluilo y seteá lead_flag="PRESUPUESTO_ENVIADO".
3. Aceptación ("sí", "dale", "ok", "me sirve", "avanzamos") → lead_flag="ACEPTADO".
4. Etapa SCHEDULING → preguntá qué día y horario le viene mejor.
5. Derivá a humano si hay queja, solicitud especial o no podés resolver → needs_human=true.
6. NUNCA uses lead_flag="PERDIDO", "BUSCANDO_AUTO", ni "RECOMPRA".
7. Respondé en español, registro informal argentino (voseo), conciso.

TIPOS DE VEHÍCULO VÁLIDOS: AUTO, SUV_4X4_DEPORTIVO, SUV/4x4, CLASICO, MOTO, ESCANEO_MOTOR

Respondé SOLO con JSON válido:
{{
  "intent": "GREETING|QUALIFYING|QUOTE_SENT|ACCEPTANCE|SCHEDULING|OBJECTION|ESCALATE|OTHER",
  "reply": "texto en español",
  "lead_flag": null,
  "needs_human": false,
  "extracted": {{
    "customer_name": null,
    "zone_group": null,
    "zone_detail": null,
    "preferred_day_iso": null,
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
    "status": "mentioned"
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
        if extracted.get("zone_group"):
            state.home_zone_group = extracted["zone_group"]
        if extracted.get("zone_detail"):
            state.home_zone_detail = extracted["zone_detail"]
        if extracted.get("preferred_day_iso"):
            state.preferred_day = extracted["preferred_day_iso"]
        if extracted.get("preferred_time_str"):
            state.preferred_time = extracted["preferred_time_str"]

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

        elif action == "update" and candidate_data.get("id"):
            target_id = int(candidate_data["id"])
            target = next((c for c in ctx.candidates if c.id == target_id), None)
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

    # ── WhatsApp sends ────────────────────────────────────────────────────

    def _send_text_to_wa(self, ctx: _Context, text: str) -> str | None:
        from zoneinfo import ZoneInfo
        now_utc = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
        wa_id = ctx.contact.wa_id

        outbound = WhatsAppMessage(
            thread_id=ctx.thread.id,
            wa_message_id=None,
            direction="out",
            status="pending",
            timestamp=now_utc,
            text=text,
        )
        self.db.add(outbound)
        ctx.thread.last_message_at = now_utc
        self.db.commit()
        reset_unanswered_alert(self.db, ctx.thread.id)
        self.db.commit()

        try:
            wa_message_id, _ = _send_whatsapp_cloud_text(to_wa_id=wa_id, text=text)
            outbound.status = "sent"
            outbound.wa_message_id = wa_message_id
            self.db.commit()
            return wa_message_id
        except Exception as exc:
            logger.error("M18 send_text failed thread_id=%s: %s", ctx.thread.id, exc)
            outbound.status = "failed"
            self.db.commit()
            return None

    def _send_flow_button(self, ctx: _Context, body_text: str, flow_token: str) -> str:
        flow_id = (self.settings.whatsapp_flow_id or "").strip()
        if not flow_id:
            raise RuntimeError("WHATSAPP_FLOW_ID not configured")

        from zoneinfo import ZoneInfo
        now_utc = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
        wa_id = ctx.contact.wa_id

        outbound = WhatsAppMessage(
            thread_id=ctx.thread.id,
            wa_message_id=None,
            direction="out",
            status="pending",
            timestamp=now_utc,
            text=body_text,
        )
        self.db.add(outbound)
        ctx.thread.last_message_at = now_utc
        self.db.commit()
        reset_unanswered_alert(self.db, ctx.thread.id)
        self.db.commit()

        try:
            wa_message_id, _ = _send_whatsapp_cloud_flow(
                to_wa_id=wa_id,
                flow_id=flow_id,
                flow_token=flow_token,
                body_text=body_text,
                cta_label="Completar datos",
            )
            outbound.status = "sent"
            outbound.wa_message_id = wa_message_id
            self.db.commit()
            return wa_message_id
        except Exception as exc:
            outbound.status = "failed"
            self.db.commit()
            raise exc

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
