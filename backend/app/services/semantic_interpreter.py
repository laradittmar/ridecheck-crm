"""L4.7B — SemanticTurnInterpreter: one controlled UNDERSTAND pass, shadow only.

    RAW BURST  →  SemanticTurnInterpreter  →  TurnEvidence (turn-evidence/1.1)

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
import re
import time
from dataclasses import dataclass, field as dc_field
from datetime import date
from typing import Any, Optional, Sequence

from ..schemas.turn_evidence import (
    SCHEMA_VERSION,
    UNRESOLVED_STATUSES,
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

PROMPT_VERSION = "understand/1.4"
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

ACCEPTANCE_VALUES = ("ACCEPT", "REJECT", "HESITATE", "FUTURE_INTENT", "QUESTION_ONLY",
                     "UNKNOWN")

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
1. Si algo no fue dicho, no lo completes. Falta = ausente. NO devuelvas ítems vacíos:
   una lista sin evidencia va vacía ([]), nunca con un objeto de campos en null.
2. Si hay varias lecturas posibles, usá status AMBIGUOUS y listá alternativas. No elijas.
3. Si dos afirmaciones se contradicen, usá conflicts y preservá ambos lados.
4. Separá SIEMPRE dónde está el AUTO (INSPECTION_LOCATION) de dónde vive/está el CLIENTE
   (CUSTOMER_ORIGIN) y de dónde está el VENDEDOR (SELLER_LOCATION). El orden en que se
   mencionan no define el rol.
5. Si el cliente ofrece varias opciones de día/horario, mantené el ORDEN: la primera es
   PRIMARY, la siguiente FALLBACK. Un horario pertenece SOLO a la opción donde fue dicho.
6. Distinguí aceptación (ACCEPT) de duda (HESITATE) y de rechazo (REJECT). Una pregunta
   sola es QUESTION_ONLY. Si dice que va a volver/avisar/contactar MÁS ADELANTE, eso es
   FUTURE_INTENT: nunca ACCEPT.
7. Si menciona más de un vehículo, devolvé todos, en orden. Si corrigió uno por otro,
   marcá el anterior con is_superseded=true y agregá la corrección.
8. Preguntas frecuentes y evidencia de negocio COEXISTEN: responder una FAQ no borra el
   vehículo, la ubicación ni el pedido de turno del mismo mensaje. Un mismo mensaje puede
   tener FAQ y service_intent a la vez. Devolvé TODOS los temas consultados, uno por cada
   pregunta distinta del mensaje; no elijas sólo el principal.
9. Lenguaje con typos, audio transcripto o texto ruidoso: interpretá con prudencia. Si no
   podés resolver, PROPOSED o AMBIGUOUS, nunca CONFIRMED.
10. status válido: CONFIRMED (el cliente lo dijo claramente), PROPOSED (probable),
    AMBIGUOUS (varias lecturas), CONFLICT (se contradice).
11. TIEMPO: usá el CONTEXTO TEMPORAL sólo para mapear expresiones relativas al vocabulario
    de días (TODAY / TOMORROW / DAY_AFTER_TOMORROW / nombre del día). NO devuelvas fechas
    ISO: resolved_date no es tuyo, lo calcula la capa determinística. Un día sin hora es
    día sin hora; no inventes horario.
12. NÚMEROS DEL VEHÍCULO: si aparecen dos números y uno nombra el modelo y el otro el año,
    conservá LOS DOS. Nunca descartes uno. Si no podés decidir cuál es cuál, devolvé
    status AMBIGUOUS con las alternativas y dejá el año en year_status AMBIGUOUS.
13. CATÁLOGO: todo lo que agregues y el cliente NO haya dicho literalmente (marca deducida
    del modelo, categoría, modelo normalizado) es como máximo PROPOSED, y va también en
    catalog_candidate. El catálogo determinístico decide después.
14. INTENCIÓN: emitida cuando este mensaje dice algo sobre EL SERVICIO — pedirlo, preguntar
    su precio (QUOTE_REQUEST), ofrecer logística (LOGISTICS_OFFER), o decir que todavía no
    está listo / que avisa más adelante (READINESS). Nombrar un auto, una zona, un día o
    aceptar una propuesta NO es, por sí solo, intención de servicio. Pueden coexistir
    varias intenciones en un mismo mensaje.
15. DÍAS ABREVIADOS O MAL ESCRITOS: resolvé a la expresión relativa o al día de la semana
    sólo si la abreviatura es inequívoca; si no lo es, AMBIGUOUS con las alternativas.
    Nunca elijas un día de la semana cuando lo dicho apunta a una expresión relativa.
16. Si un dato no se conoce, va en null: NUNCA escribas "UNKNOWN", "N/A", "-" ni similares
    como si fueran valores.

Vocabulario permitido:
- service_intents[].kind: INSPECTION | QUOTE_REQUEST | READINESS | LOGISTICS_OFFER | OTHER
- service_intents[].value para INSPECTION: {SERVICE_INTENT_VALUES[0]}
- service_intents[].value para READINESS: {" | ".join(READINESS_VALUES)}
- service_intents[].value para QUOTE_REQUEST: true
- locations[].role: INSPECTION_LOCATION | CUSTOMER_ORIGIN | SELLER_LOCATION | UNKNOWN_LOCATION_ROLE
- faq_topics: {" | ".join(FAQ_TOPICS)}
- scheduling_requests[].day_expression: {" | ".join(DAY_EXPRESSIONS)}
- scheduling_requests[].priority: PRIMARY | FALLBACK | ADDITIONAL
- acceptance.signal: {" | ".join(ACCEPTANCE_VALUES)}
- corrections[].relation: CORRECT_EXISTING | REPLACE_CANDIDATE | SWITCH_TO_PRIOR_CANDIDATE
  | ADD_SECOND_CANDIDATE | UNKNOWN_RELATION

Respondé SOLO con este JSON (las listas van vacías si no hay evidencia; los objetos, null):
{{
  "service_intents": [],
  "vehicles": [],
  "locations": [],
  "faq_topics": [],
  "acceptance": null,
  "scheduling_requests": [],
  "corrections": [],
  "identity": [],
  "handoff": null,
  "ambiguities": [],
  "conflicts": [],
  "notes": []
}}

Forma de cada ítem cuando SÍ hay evidencia:
- service_intents[]: {{"kind", "value", "status", "reason"}}
- vehicles[]: {{"make", "model", "year", "category_suggestion", "catalog_candidate",
               "is_superseded", "status", "year_status", "alternatives", "reason"}}
- locations[]: {{"locality", "role", "status", "reason"}}
- acceptance: {{"signal", "status", "reason"}}
- scheduling_requests[]: {{"priority", "day_expression", "time", "flexible_time", "rank",
                          "status"}}
- corrections[]: {{"relation", "from_value", "to_value", "status", "reason"}}
- faq_topics[]: strings del vocabulario de arriba, uno por cada pregunta del mensaje
- identity[]: {{"kind", "value", "status"}}
- handoff: {{"requested", "status", "reason"}}

No devuelvas ítems cuyos campos estén todos en null. Sin texto fuera del JSON."""


_SPANISH_WEEKDAYS = ("LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO",
                     "DOMINGO")

# A number that could plausibly be a model year in this market. Deliberately a *range*,
# not a list of models: no phrase or catalog entry is hard-coded here.
_YEAR_MIN, _YEAR_MAX = 1980, 2100
_NUMBER_RE = re.compile(r"\b(\d{4})\b")


@dataclass
class TurnContext:
    """L4.7B.2 — the bounded context handed to the interpreter.

    Deliberately small and current-cycle only. Prior-cycle history is excluded: a vehicle
    or a locality from a *previous* inspection cycle must never leak into this turn's
    interpretation (the L4.6 stale-candidate defect class). Nothing here is authoritative;
    it exists so relative references ("ese", "mñ", "sí") can be read at all.
    """
    current_local_date: Optional[date] = None
    current_weekday: Optional[str] = None
    timezone: Optional[str] = None
    previous_customer_turn: Optional[str] = None   # current cycle only
    stage: Optional[str] = None                    # canonical stage label, not a decision
    pending_clarification: Optional[str] = None    # what we last asked, if anything
    offered_slots: tuple[str, ...] = ()            # slots already offered this cycle

    @classmethod
    def now(cls, tz: Optional[str] = None, today: Optional[date] = None) -> "TurnContext":
        today = today or date.today()
        return cls(current_local_date=today,
                   current_weekday=_SPANISH_WEEKDAYS[today.weekday()],
                   timezone=tz)

    def render(self) -> str:
        """The context block sent to the model. Empty string when nothing is known."""
        lines: list[str] = []
        if self.current_local_date:
            lines.append(f"- fecha local actual: {self.current_local_date.isoformat()}"
                         + (f" ({self.current_weekday})" if self.current_weekday else ""))
        if self.timezone:
            lines.append(f"- zona horaria: {self.timezone}")
        if self.stage:
            lines.append(f"- etapa actual de la conversación: {self.stage}")
        if self.pending_clarification:
            lines.append(f"- última pregunta pendiente nuestra: {self.pending_clarification}")
        if self.offered_slots:
            lines.append("- horarios ya ofrecidos: " + ", ".join(self.offered_slots))
        if self.previous_customer_turn:
            lines.append(f"- mensaje anterior del cliente (mismo ciclo): {self.previous_customer_turn}")
        if not lines:
            return ""
        return ("CONTEXTO TEMPORAL Y DE CONVERSACIÓN (hechos para resolver referencias; "
                "NO son verdad operativa y NO son evidencia del cliente):\n" + "\n".join(lines))

    def supplied_keys(self) -> tuple[str, ...]:
        """Which context slots were populated — recorded as provenance, without values."""
        present = []
        for name in ("current_local_date", "current_weekday", "timezone",
                     "previous_customer_turn", "stage", "pending_clarification"):
            if getattr(self, name):
                present.append(name)
        if self.offered_slots:
            present.append("offered_slots")
        return tuple(present)


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
    context_keys: tuple[str, ...] = ()          # WHICH context was supplied, never values
    sanitized_items: int = 0                    # semantically empty rows dropped (Phase A)


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


_PLACEHOLDERS = {"unknown", "n/a", "na", "null", "none", "-", "?", "desconocido",
                 "sin datos", "no especificado"}


def _clean(value: Any) -> Optional[Any]:
    """A placeholder is an absence dressed as a value. Absence stays absence."""
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in _PLACEHOLDERS:
            return None
        return text
    return value


def _coerce_year(raw: Any) -> Optional[int]:
    """Accept 2014 and "2014" alike. A year the customer said must not be lost to typing."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.strip().isdigit():
        value = int(raw.strip())
    else:
        return None
    return value if _YEAR_MIN <= value <= _YEAR_MAX else None


def _year_candidates(texts: Sequence[str]) -> list[int]:
    """Every plausible year-shaped number in the burst, in order, de-duplicated."""
    seen: list[int] = []
    for text in texts:
        for match in _NUMBER_RE.finditer(text or ""):
            value = int(match.group(1))
            if _YEAR_MIN <= value <= _YEAR_MAX and value not in seen:
                seen.append(value)
    return seen


def _said_literally(value: Any, haystack: str) -> bool:
    """True when the customer actually used this token (accent/casing tolerant)."""
    if not isinstance(value, str) or not value.strip():
        return False
    needle = value.strip().lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")):
        needle = needle.replace(a, b)
    return needle in haystack


def _confidence(raw: Any) -> Optional[float]:
    """Phase J — confidence is ADVISORY. It is recorded when offered and clamped to [0,1],
    and it is never allowed to raise or lower a status: nothing in this module reads it
    back. Reconciliation may weigh it; it may not be a decision on its own.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value:                      # NaN
        return None
    return min(1.0, max(0.0, value))


def _cap(status: EvidenceStatus, ceiling: EvidenceStatus) -> EvidenceStatus:
    """Lower a status to a ceiling, leaving AMBIGUOUS/CONFLICT untouched."""
    if status in UNRESOLVED_STATUSES:
        return status
    order = {EvidenceStatus.PROPOSED: 0, EvidenceStatus.CONFIRMED: 1}
    if order.get(status, 0) > order.get(ceiling, 0):
        return ceiling
    return status


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
        context: Optional[TurnContext] = None,
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
        context_keys: tuple[str, ...] = ()
        if context is not None:
            block = context.render()
            if block:
                user += "\n\n" + block
                context_keys = context.supplied_keys()
        if context_hint:
            user += f"\n\nCONTEXTO (solo para resolver referencias, no es verdad operativa):\n{context_hint}"

        try:
            content, usage = self._transport(
                [{"role": "system", "content": _SYSTEM_PROMPT},
                 {"role": "user", "content": user}],
                model,
            )
            parsed = json.loads(content)
            raw_evidence = self._to_turn_evidence(
                parsed, model=model, thread_id=thread_id, burst_id=burst_id,
                message_ids=tuple(message_ids), reconstruction=reconstruction,
                texts=texts,
            )
            # Phase A: semantically empty rows are an artifact of the response template,
            # never customer evidence. They are dropped before anything can consume them.
            evidence = raw_evidence.without_empty_items()
            dropped = sum(1 for _ in raw_evidence.iter_items()) - sum(1 for _ in evidence.iter_items())
            latency = int((time.perf_counter() - started) * 1000)
            return InterpretationResult(
                evidence=evidence, ok=True, latency_ms=latency, model=model,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                raw_response=content,
                context_keys=context_keys,
                sanitized_items=max(0, dropped),
            )
        except Exception as exc:   # isolation: a shadow failure must never surface
            latency = int((time.perf_counter() - started) * 1000)
            return InterpretationResult(evidence=None, ok=False, latency_ms=latency,
                                        model=model, error=f"{type(exc).__name__}: {exc}")

    # ── mapping into the typed schema ─────────────────────────────────────────

    def _to_turn_evidence(
        self, payload: dict, *, model: str, thread_id: Optional[int],
        burst_id: Optional[str], message_ids: tuple[str, ...],
        reconstruction: BurstReconstruction, texts: Sequence[str] = (),
    ) -> TurnEvidence:
        prov = Provenance(
            source_kind=SourceKind.SEMANTIC,
            interpreter=f"{INTERPRETER_ID}:{PROMPT_VERSION}",
            model_version=model,
            source_message_ids=message_ids,
            spans=(),                     # spans are not fabricated when unavailable
        )

        # Lower-cased, accent-folded burst — used only to check what was *said*, never to
        # match phrases: it answers "did this token appear?", not "does this mean X?".
        haystack = " ".join(texts).lower()
        for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")):
            haystack = haystack.replace(a, b)
        burst_years = _year_candidates(texts)

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
                field=field, kind=kind, value=_clean(raw.get("value")),
                status=_status(raw.get("status")), reason=raw.get("reason"),
                confidence=_confidence(raw.get("confidence")), provenance=prov))

        vehicles: list[VehicleEvidence] = []
        for index, raw in enumerate(payload.get("vehicles") or []):
            if not isinstance(raw, dict):
                continue
            make, model_name = _clean(raw.get("make")), _clean(raw.get("model"))
            combined = " ".join(p for p in (make, model_name) if p) or None
            year = _coerce_year(raw.get("year"))
            year_status = _status(raw.get("year_status"),
                                  EvidenceStatus.CONFIRMED if year else EvidenceStatus.AMBIGUOUS)
            status = _status(raw.get("status"))
            alternatives = _alternatives(raw.get("alternatives"))
            reason = raw.get("reason")

            # Phase C — number pair. A model name and a year are both numbers here; the
            # interpreter may only keep one. What the customer said stays evidence: the
            # remaining year-shaped number in the burst is retained as PROPOSED, and when
            # the pair cannot be assigned the vehicle stays AMBIGUOUS with both readings.
            if year is None and burst_years:
                leftover = [y for y in burst_years
                            if str(y) != str(model_name or "").strip()]
                if len(leftover) == 1:
                    year = leftover[0]
                    year_status = EvidenceStatus.PROPOSED
                    reason = reason or "year recovered from burst number pair"
                elif len(leftover) > 1:
                    year_status = EvidenceStatus.AMBIGUOUS
                    if not alternatives:
                        alternatives = tuple(
                            Alternative(value=y, reason="year candidate in burst")
                            for y in leftover)

            # Phase D — catalog ceiling. Anything the customer did not say literally is a
            # suggestion for the deterministic catalog, never a confirmed fact.
            inferred = [v for v in (make, _clean(raw.get("category_suggestion")))
                        if v and not _said_literally(v, haystack)]
            if inferred:
                status = _cap(status, EvidenceStatus.PROPOSED)
            catalog_candidate = raw.get("catalog_candidate") or (
                combined if inferred else None)

            vehicles.append(VehicleEvidence(
                field=("vehicle_superseded" if raw.get("is_superseded") else "vehicle"),
                value=combined, make=make, model=model_name,
                year=year, year_status=year_status,
                category_suggestion=_clean(raw.get("category_suggestion")),
                catalog_candidate=catalog_candidate,
                is_superseded=bool(raw.get("is_superseded")),
                mention_index=index,
                status=status,
                alternatives=alternatives,
                confidence=_confidence(raw.get("confidence")),
                reason=reason, provenance=prov))

        locations: list[LocationEvidence] = []
        for raw in payload.get("locations") or []:
            if not isinstance(raw, dict):
                continue
            try:
                role = LocationRole(str(raw.get("role", "UNKNOWN_LOCATION_ROLE"))).value
            except ValueError:
                role = LocationRole.UNKNOWN_LOCATION_ROLE.value
            locations.append(LocationEvidence(
                value=_clean(raw.get("locality")), locality=_clean(raw.get("locality")),
                role=role,
                status=_status(raw.get("status")), reason=raw.get("reason"),
                confidence=_confidence(raw.get("confidence")),
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
                value={"ACCEPT": True, "REJECT": False, "HESITATE": False,
                       "FUTURE_INTENT": False}.get(signal.value),
                signal=signal, status=_status(raw_acc.get("status")),
                confidence=_confidence(raw_acc.get("confidence")),
                reason=raw_acc.get("reason"), provenance=prov)

        schedule: list[SchedulingRequestEvidence] = []
        for index, raw in enumerate(payload.get("scheduling_requests") or []):
            if not isinstance(raw, dict):
                continue
            try:
                priority = SchedulingPriority(str(raw.get("priority", "PRIMARY")))
            except ValueError:
                priority = SchedulingPriority.PRIMARY
            # Vocabulary hygiene: a day expression outside the controlled vocabulary is
            # not a day. It becomes absence, never a literal like "UNKNOWN".
            day_expr = _clean(raw.get("day_expression"))
            if isinstance(day_expr, str) and day_expr.upper() not in DAY_EXPRESSIONS:
                day_expr = None
            at_time = _clean(raw.get("time"))
            schedule.append(SchedulingRequestEvidence(
                value=({"day": day_expr, "time": at_time,
                        "rank": int(raw.get("rank") or index + 1)}
                       if (day_expr or at_time) else None),
                priority=priority, day_expression=day_expr,
                # Phase B: date resolution is deterministic. A date proposed here is
                # dropped, not trusted — the interpreter only names the day expression.
                resolved_date=None, time=at_time,
                flexible_time=bool(raw.get("flexible_time") or at_time is None),
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
            from_value, to_value = raw.get("from_value"), raw.get("to_value")
            corrections.append(CorrectionEvidence(
                value=(True if (from_value or to_value or raw.get("target_ref")) else None),
                relation=relation, from_value=from_value,
                to_value=to_value, status=_status(raw.get("status")),
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
