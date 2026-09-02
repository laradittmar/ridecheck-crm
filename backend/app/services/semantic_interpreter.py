"""L4.7B — SemanticTurnInterpreter: one controlled UNDERSTAND pass, shadow only.

    RAW BURST  →  SemanticTurnInterpreter  →  TurnEvidence (turn-evidence/1.0)

The interpreter **proposes meaning and nothing else**. It has no mutation authority: it
never touches the ORM, never calls PricingService / ScheduleService / OutboundSafetyGate,
never decides price, availability, booking, lead lifecycle or candidate persistence. Those
belong to deterministic reconciliation (L4.7C), which may accept, reject, defer or
escalate what is proposed here.

Asymmetric authority (L4.7B Part 11A):

* catalog identity → deterministic catalog is authoritative; a `catalog_candidate` here is
  a suggestion;
* customer-stated roles → this layer may establish *proposed* meaning; reconciliation
  verifies it and preserves ambiguity/conflict when unresolved;
* price / availability / booking → PricingService / ScheduleService / canonical booking
  state are authoritative, and this module is not allowed to express them at all.

Failure is always isolated: `interpret()` returns `None` and never raises, so a shadow run
can never break a customer turn.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from ..schemas.turn_evidence import (
    SCHEMA_VERSION,
    AcceptanceEvidence,
    AcceptanceSignal,
    Alternative,
    AmbiguityNote,
    BurstReconstruction,
    ConflictNote,
    CorrectionEvidence,
    CorrectionRelation,
    EvidenceStatus,
    FaqIntentEvidence,
    HandoffEvidence,
    IdentityEvidence,
    IdentityKind,
    LocationEvidence,
    LocationRole,
    Provenance,
    SchedulingPriority,
    SchedulingRequestEvidence,
    ServiceIntentEvidence,
    ServiceIntentKind,
    SourceKind,
    TurnEvidence,
    TurnRef,
    VehicleEvidence,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = "understand/1.0"
INTERPRETER_ID = "semantic:understand"

# Controlled vocabularies. These are the *schema* the interpreter must speak — not phrase
# rules. Adding a phrase here would violate the no-phrase-patch rule; adding a concept is a
# schema change and belongs in a documented version bump.
SERVICE_INTENT_VALUES = ("PREPURCHASE_INSPECTION",)
READINESS_VALUES = ("SEARCHING_NOT_READY", "FUTURE_CONTACT_INTENDED", "HESITANT_OR_DEFERRED")
FAQ_TOPICS = ("service_scope", "report", "presence", "payment", "business_hours",
              "duration", "coverage", "mixed")
DAY_EXPRESSIONS = ("TODAY", "TOMORROW", "DAY_AFTER_TOMORROW", "MONDAY", "TUESDAY",
                   "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY", "EXPLICIT_DATE")

_SYSTEM_PROMPT = f"""Sos el intérprete semántico de RideCheck (revisión pre-compra de autos, Argentina).

Tu ÚNICA tarea es INTERPRETAR lo que el cliente quiso decir y devolverlo como evidencia
estructurada. No decidís nada del negocio.

Tres capas, no las mezcles:
- RAW EVIDENCE: lo que el cliente escribió. Inmutable.
- TURN EVIDENCE: tu interpretación. Es una PROPUESTA, no una verdad operativa.
- CANONICAL STATE: lo decide después un reconciliador determinístico. No es tu tarea.

NUNCA decidas ni menciones: precio, disponibilidad de agenda, reservas/turnos confirmados,
estado del lead, ni si se crea un candidato. No inventes datos que el cliente no dijo.

Reglas de interpretación:
1. Si algo no fue dicho, no lo completes. Falta = ausente.
2. Si hay varias lecturas posibles, usá status AMBIGUOUS y listá alternativas. No elijas.
3. Si dos afirmaciones se contradicen, usá conflicts y preservá ambos lados.
4. Separá SIEMPRE dónde está el AUTO (INSPECTION_LOCATION) de dónde vive/está el CLIENTE
   (CUSTOMER_ORIGIN) y de dónde está el VENDEDOR (SELLER_LOCATION). El orden en que se
   mencionan no define el rol.
5. Si el cliente ofrece varias opciones de día/horario, mantené el ORDEN: la primera es
   PRIMARY, la siguiente FALLBACK. Un horario pertenece SOLO a la opción donde fue dicho.
6. Distinguí aceptación (ACCEPT) de duda (HESITATE) y de rechazo (REJECT). Una pregunta
   sola es QUESTION_ONLY.
7. Si menciona más de un vehículo, devolvé todos, en orden. Si corrigió uno por otro,
   marcá el anterior con is_superseded=true y agregá la corrección.
8. Preguntas frecuentes y evidencia de negocio COEXISTEN: responder una FAQ no borra el
   vehículo, la ubicación ni el pedido de turno del mismo mensaje.
9. Lenguaje con typos, audio transcripto o texto ruidoso: interpretá con prudencia. Si no
   podés resolver, PROPOSED o AMBIGUOUS, nunca CONFIRMED.
10. status válido: CONFIRMED (el cliente lo dijo claramente), PROPOSED (probable),
    AMBIGUOUS (varias lecturas), CONFLICT (se contradice).

Vocabulario permitido:
- service_intents[].kind: INSPECTION | QUOTE_REQUEST | READINESS | LOGISTICS_OFFER | OTHER
- service_intents[].value para INSPECTION: {SERVICE_INTENT_VALUES[0]}
- service_intents[].value para READINESS: {" | ".join(READINESS_VALUES)}
- service_intents[].value para QUOTE_REQUEST: true
- locations[].role: INSPECTION_LOCATION | CUSTOMER_ORIGIN | SELLER_LOCATION | UNKNOWN_LOCATION_ROLE
- faq_topics: {" | ".join(FAQ_TOPICS)}
- scheduling_requests[].day_expression: {" | ".join(DAY_EXPRESSIONS)}
- scheduling_requests[].priority: PRIMARY | FALLBACK | ADDITIONAL
- acceptance.signal: ACCEPT | REJECT | HESITATE | QUESTION_ONLY | UNKNOWN
- corrections[].relation: CORRECT_EXISTING | REPLACE_CANDIDATE | SWITCH_TO_PRIOR_CANDIDATE
  | ADD_SECOND_CANDIDATE | UNKNOWN_RELATION

Respondé SOLO con este JSON:
{{
  "service_intents": [{{"kind": "INSPECTION", "value": "PREPURCHASE_INSPECTION", "status": "CONFIRMED", "reason": null}}],
  "vehicles": [{{"make": null, "model": null, "year": null, "category_suggestion": null,
                 "is_superseded": false, "status": "PROPOSED", "alternatives": [], "reason": null}}],
  "locations": [{{"locality": null, "role": "UNKNOWN_LOCATION_ROLE", "status": "PROPOSED", "reason": null}}],
  "faq_topics": [],
  "acceptance": null,
  "scheduling_requests": [{{"priority": "PRIMARY", "day_expression": null, "time": null,
                            "flexible_time": false, "rank": 1, "status": "PROPOSED"}}],
  "corrections": [],
  "identity": [],
  "handoff": null,
  "ambiguities": [{{"field": "vehicle", "alternatives": [], "reason": null}}],
  "conflicts": [],
  "notes": []
}}
Listas vacías cuando no aplica. Sin texto fuera del JSON."""


@dataclass
class InterpretationResult:
    """Outcome of one shadow interpretation — evidence plus call telemetry."""
    evidence: Optional[TurnEvidence]
    ok: bool
    latency_ms: int
    model: Optional[str] = None
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    error: Optional[str] = None
    raw_response: Optional[str] = None


def _status(value: Any, default: EvidenceStatus = EvidenceStatus.PROPOSED) -> EvidenceStatus:
    try:
        return EvidenceStatus(str(value))
    except (ValueError, TypeError):
        return default


def _alternatives(raw: Any) -> tuple[Alternative, ...]:
    out: list[Alternative] = []
    for item in (raw or []):
        if isinstance(item, dict):
            out.append(Alternative(value=item.get("value"),
                                   confidence=item.get("confidence"),
                                   reason=item.get("reason")))
        else:
            out.append(Alternative(value=item))
    return tuple(out)


class SemanticTurnInterpreter:
    """One controlled UNDERSTAND pass per burst. Shadow only; no business authority."""

    def __init__(self, settings: Any, transport=None) -> None:
        self.settings = settings
        # Injected for tests; production uses the approved OpenAI chat-completions call.
        self._transport = transport or self._call_openai

    # ── model call ────────────────────────────────────────────────────────────

    def _call_openai(self, messages: list[dict], model: str) -> tuple[str, dict]:
        """Single chat-completions call with structured output. Returns (content, usage)."""
        import json as _json
        from urllib import error as urlerror, request as urlrequest

        api_key = (getattr(self.settings, "openai_api_key", "") or "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")

        body = _json.dumps({
            "model": model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": 1200,
        }).encode("utf-8")
        req = urlrequest.Request(
            "https://api.openai.com/v1/chat/completions",
            data=body, method="POST",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
        )
        try:
            with urlrequest.urlopen(req, timeout=30) as resp:
                payload = _json.loads(resp.read().decode("utf-8", errors="replace"))
        except urlerror.HTTPError as exc:      # never leak the key or the body verbatim
            raise RuntimeError(f"openai http {exc.code}") from None
        content = payload["choices"][0]["message"]["content"]
        return content, (payload.get("usage") or {})

    # ── public entry point ────────────────────────────────────────────────────

    def interpret(
        self,
        messages: Sequence[str],
        *,
        thread_id: Optional[int] = None,
        burst_id: Optional[str] = None,
        message_ids: Sequence[str] = (),
        reconstruction: BurstReconstruction = BurstReconstruction.LIVE_DEBOUNCE,
        context_hint: Optional[str] = None,
    ) -> InterpretationResult:
        """Interpret one burst. Never raises; failures come back as ok=False."""
        started = time.perf_counter()
        model = (getattr(self.settings, "openai_chat_model", "") or "gpt-4o-mini").strip()
        texts = [t for t in (messages or []) if isinstance(t, str) and t.strip()]
        if not texts:
            return InterpretationResult(evidence=None, ok=False, latency_ms=0,
                                        model=model, error="empty_burst")

        user_lines = "\n".join(f"- {t}" for t in texts)
        user = f"MENSAJES DEL CLIENTE (en orden):\n{user_lines}"
        if context_hint:
            user += f"\n\nCONTEXTO (solo para resolver referencias, no es verdad operativa):\n{context_hint}"

        try:
            content, usage = self._transport(
                [{"role": "system", "content": _SYSTEM_PROMPT},
                 {"role": "user", "content": user}],
                model,
            )
            parsed = json.loads(content)
            evidence = self._to_turn_evidence(
                parsed, model=model, thread_id=thread_id, burst_id=burst_id,
                message_ids=tuple(message_ids), reconstruction=reconstruction,
            )
            latency = int((time.perf_counter() - started) * 1000)
            return InterpretationResult(
                evidence=evidence, ok=True, latency_ms=latency, model=model,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                raw_response=content,
            )
        except Exception as exc:   # isolation: a shadow failure must never surface
            latency = int((time.perf_counter() - started) * 1000)
            return InterpretationResult(evidence=None, ok=False, latency_ms=latency,
                                        model=model, error=f"{type(exc).__name__}: {exc}")

    # ── mapping into the typed schema ─────────────────────────────────────────

    def _to_turn_evidence(
        self, payload: dict, *, model: str, thread_id: Optional[int],
        burst_id: Optional[str], message_ids: tuple[str, ...],
        reconstruction: BurstReconstruction,
    ) -> TurnEvidence:
        prov = Provenance(
            source_kind=SourceKind.SEMANTIC,
            interpreter=f"{INTERPRETER_ID}:{PROMPT_VERSION}",
            model_version=model,
            source_message_ids=message_ids,
            spans=(),                     # spans are not fabricated when unavailable
        )

        intents: list[ServiceIntentEvidence] = []
        for raw in payload.get("service_intents") or []:
            if not isinstance(raw, dict):
                continue
            try:
                kind = ServiceIntentKind(str(raw.get("kind", "OTHER")))
            except ValueError:
                kind = ServiceIntentKind.OTHER
            field = {"INSPECTION": "service_intent", "READINESS": "readiness",
                     "QUOTE_REQUEST": "quote_request",
                     "LOGISTICS_OFFER": "customer_logistics_offer"}.get(kind.value, "service_intent")
            intents.append(ServiceIntentEvidence(
                field=field, kind=kind, value=raw.get("value"),
                status=_status(raw.get("status")), reason=raw.get("reason"),
                provenance=prov))

        vehicles: list[VehicleEvidence] = []
        for index, raw in enumerate(payload.get("vehicles") or []):
            if not isinstance(raw, dict):
                continue
            make, model_name = raw.get("make"), raw.get("model")
            combined = " ".join(p for p in (make, model_name) if p) or None
            year = raw.get("year")
            vehicles.append(VehicleEvidence(
                field=("vehicle_superseded" if raw.get("is_superseded") else "vehicle"),
                value=combined, make=make, model=model_name,
                year=(int(year) if isinstance(year, int) else None),
                year_status=(EvidenceStatus.CONFIRMED if isinstance(year, int)
                             else EvidenceStatus.AMBIGUOUS),
                category_suggestion=raw.get("category_suggestion"),
                is_superseded=bool(raw.get("is_superseded")),
                mention_index=index,
                status=_status(raw.get("status")),
                alternatives=_alternatives(raw.get("alternatives")),
                reason=raw.get("reason"), provenance=prov))

        locations: list[LocationEvidence] = []
        for raw in payload.get("locations") or []:
            if not isinstance(raw, dict):
                continue
            try:
                role = LocationRole(str(raw.get("role", "UNKNOWN_LOCATION_ROLE"))).value
            except ValueError:
                role = LocationRole.UNKNOWN_LOCATION_ROLE.value
            locations.append(LocationEvidence(
                value=raw.get("locality"), locality=raw.get("locality"), role=role,
                status=_status(raw.get("status")), reason=raw.get("reason"),
                alternatives=_alternatives(raw.get("alternatives")), provenance=prov))

        faqs = tuple(
            FaqIntentEvidence(value=topic, topic=topic,
                              status=EvidenceStatus.CONFIRMED, provenance=prov)
            for topic in (payload.get("faq_topics") or []) if isinstance(topic, str)
        )

        acceptance = None
        raw_acc = payload.get("acceptance")
        if isinstance(raw_acc, dict) and raw_acc.get("signal"):
            try:
                signal = AcceptanceSignal(str(raw_acc["signal"]))
            except ValueError:
                signal = AcceptanceSignal.UNKNOWN
            acceptance = AcceptanceEvidence(
                value={"ACCEPT": True, "REJECT": False, "HESITATE": False}.get(signal.value),
                signal=signal, status=_status(raw_acc.get("status")),
                reason=raw_acc.get("reason"), provenance=prov)

        schedule: list[SchedulingRequestEvidence] = []
        for index, raw in enumerate(payload.get("scheduling_requests") or []):
            if not isinstance(raw, dict):
                continue
            try:
                priority = SchedulingPriority(str(raw.get("priority", "PRIMARY")))
            except ValueError:
                priority = SchedulingPriority.PRIMARY
            schedule.append(SchedulingRequestEvidence(
                value={"day": raw.get("day_expression"), "time": raw.get("time"),
                       "rank": int(raw.get("rank") or index + 1)},
                priority=priority, day_expression=raw.get("day_expression"),
                resolved_date=raw.get("resolved_date"), time=raw.get("time"),
                flexible_time=bool(raw.get("flexible_time") or raw.get("time") is None),
                rank=int(raw.get("rank") or index + 1),
                status=_status(raw.get("status")), provenance=prov))

        corrections: list[CorrectionEvidence] = []
        for raw in payload.get("corrections") or []:
            if not isinstance(raw, dict):
                continue
            try:
                relation = CorrectionRelation(str(raw.get("relation", "UNKNOWN_RELATION")))
            except ValueError:
                relation = CorrectionRelation.UNKNOWN_RELATION
            corrections.append(CorrectionEvidence(
                value=True, relation=relation, from_value=raw.get("from_value"),
                to_value=raw.get("to_value"), status=_status(raw.get("status")),
                reason=raw.get("reason"), provenance=prov))

        identities: list[IdentityEvidence] = []
        for raw in payload.get("identity") or []:
            if not isinstance(raw, dict):
                continue
            try:
                kind = IdentityKind(str(raw.get("kind", "OTHER_IDENTITY")))
            except ValueError:
                kind = IdentityKind.OTHER_IDENTITY
            identities.append(IdentityEvidence(
                value=raw.get("value"), kind=kind, status=_status(raw.get("status")),
                provenance=prov))

        handoff = None
        raw_handoff = payload.get("handoff")
        if isinstance(raw_handoff, dict) and raw_handoff.get("requested"):
            handoff = HandoffEvidence(value=True, requested=True,
                                      status=_status(raw_handoff.get("status")),
                                      reason=raw_handoff.get("reason"), provenance=prov)

        ambiguities = tuple(
            AmbiguityNote(field=str(raw.get("field", "unknown")),
                          alternatives=_alternatives(raw.get("alternatives")),
                          reason=raw.get("reason"), provenance=prov)
            for raw in (payload.get("ambiguities") or []) if isinstance(raw, dict)
        )
        conflicts = tuple(
            ConflictNote(field=str(raw.get("field", "unknown")),
                         sides=_alternatives(raw.get("sides")),
                         reason=raw.get("reason"), provenance=prov)
            for raw in (payload.get("conflicts") or []) if isinstance(raw, dict)
        )
        notes = tuple(str(n) for n in (payload.get("notes") or []) if isinstance(n, str))

        return TurnEvidence(
            interpreter=f"{INTERPRETER_ID}:{PROMPT_VERSION}",
            model_version=model,
            turn=TurnRef(thread_id=thread_id, burst_id=burst_id,
                         ordered_message_ids=message_ids, reconstruction=reconstruction),
            service_intents=tuple(intents),
            vehicle_mentions=tuple(vehicles),
            location_mentions=tuple(locations),
            faq_intents=faqs,
            acceptance=acceptance,
            scheduling_requests=tuple(schedule),
            corrections=tuple(corrections),
            identity_mentions=tuple(identities),
            handoff=handoff,
            freeform_notes=notes,
            ambiguities=ambiguities,
            conflicts=conflicts,
        )
