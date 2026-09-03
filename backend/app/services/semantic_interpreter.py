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

PROMPT_VERSION = "understand/1.18"
INTERPRETER_ID = "semantic:understand"

# Controlled vocabularies. These are the *schema* the interpreter must speak — not phrase
# rules. Adding a phrase here would violate the no-phrase-patch rule; adding a concept is a
# schema change and belongs in a documented version bump.
SERVICE_INTENT_VALUES = ("PREPURCHASE_INSPECTION",)
# L4.7B.3: stance lives in `acceptance`. READINESS keeps only the process fact a stance
# cannot express — "I have not chosen a car yet".
READINESS_VALUES = ("SEARCHING_NOT_READY",)
# No "mixed" sentinel: a burst with three questions carries three topics (L4.7B.2B).
FAQ_TOPICS = ("service_scope", "report", "presence", "payment", "business_hours",
              "duration", "coverage")
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

═══ REGLAS GENERALES ═══
G1. Si algo no fue dicho, no lo completes. Falta = ausente. Las listas sin evidencia van
    vacías ([]); nunca devuelvas un objeto con todos los campos en null.
G2. Varias lecturas posibles → status AMBIGUOUS + alternatives. No elijas por el cliente.
G3. Dos afirmaciones que se contradicen → conflicts, con ambos lados.
G4. status: CONFIRMED (lo dijo claramente) | PROPOSED (probable, o texto ruidoso) |
    AMBIGUOUS (varias lecturas) | CONFLICT (se contradice). Con typos, audio transcripto o
    texto muy ruidoso: PROPOSED o AMBIGUOUS, nunca CONFIRMED. Pero el ruido NO borra la
    evidencia: si a través de los errores de tipeo se entiende lo que pide, emitilo con
    status PROPOSED en lugar de devolver nada.
G5. Todo COEXISTE. Un mismo mensaje puede tener FAQ + intención + vehículo + ubicación +
    postura + pedido de día. Contestar una cosa no borra las otras. Ninguna categoría
    tiene prioridad sobre otra.
G6. Nada de valores de relleno: si no se sabe, va null. NUNCA "UNKNOWN", "N/A", "-".
G7b. EVIDENCIA ACOMPAÑANTE: cuando el mensaje implica a la vez UN DATO y LA RELACIÓN que
    explica cómo ese dato cambia lo que ya se sabía, devolvé LOS DOS. Un valor corregido
    viene con su corrección; un auto que reemplaza a otro viene con la relación de
    reemplazo y con el anterior marcado; un hecho del proceso de compra convive con la
    postura. Emitir sólo la mitad más visible es perder evidencia que el cliente dio.
G7. Recorré TODOS los slots por separado — intenciones, vehículos, ubicaciones, postura,
    día/hora, correcciones, temas de FAQ — y completá cada uno con lo que le corresponda.
    No elijas "lo más importante" del mensaje: un mensaje corto puede llenar dos o tres
    slots a la vez, y omitir uno es perder evidencia que el cliente sí dio.

═══ 1. SEÑALES DEL MENSAJE (service_intents) — pueden convivir varias ═══
Este array no es sólo "lo que quiere": también lleva HECHOS de su proceso de compra.
Cuatro kinds, con su disparador y su value:

  kind=INSPECTION        value="PREPURCHASE_INSPECTION"
    El mensaje habla DEL SERVICIO o de CHEQUEAR EL ESTADO de un auto: lo pide, pregunta si
    lo hacemos, qué incluye, cómo funciona, qué informe entrega, si van al lugar; u ofrece
    un auto concreto (o dónde está) dentro de una pregunta sobre el servicio (status
    PROPOSED); o dice que quiere conocer la condición de un auto antes de comprarlo.

  kind=QUOTE_REQUEST     value=true   ← el value SIEMPRE va, es exactamente true
    El mensaje pregunta CUÁNTO CUESTA o pide una cotización/presupuesto: precio, costo,
    valor, cuánto sale, cuánto cobran, cuánto salía. SIEMPRE va acá, nunca como tema de
    FAQ. Si además nombra el servicio, emití TAMBIÉN INSPECTION.

  kind=READINESS         value="SEARCHING_NOT_READY"
    El cliente dice que todavía NO ELIGIÓ / NO TIENE el auto: sigue buscando, mirando,
    consultando por ahora, no decidió cuál, "cuando lo tenga", "cuando decida", "cuando lo
    vaya a ver". Es un HECHO de su proceso de compra, no una postura, y es OBLIGATORIO
    emitirlo cada vez que aparece — incluso (y sobre todo) cuando en el mismo mensaje ya
    emitiste una postura como FUTURE_INTENT. Los dos ítems, siempre.
    PERO: si ya nombró un auto concreto (marca, modelo o ambos), YA ELIGIÓ: en ese caso NO
    emitas SEARCHING_NOT_READY, aunque hable de plazos futuros.

  kind=LOGISTICS_OFFER   value="CUSTOMER_OFFERS_TRANSPORT"
    El cliente ofrece logística propia: tiene movilidad, puede llevar o acercar el auto.

NO emitas ninguna intención por: saludar, agradecer, ser amable, escribirnos, prometer que
vuelve más adelante, o contar que está buscando un auto (eso último es READINESS, no
INSPECTION). El canal no es evidencia.
Contraste:
  "estoy buscando un auto, cuando decida te aviso"   → READINESS, sin INSPECTION
  "¿ustedes revisan autos usados?"                   → INSPECTION
  "quiero ver en qué estado está antes de comprarlo"  → INSPECTION
  "¿cuánto sale?"                                     → QUOTE_REQUEST (sin FAQ)
  "¿cuánto sale la revisión?"                         → QUOTE_REQUEST + INSPECTION
  "¿tengo que estar presente? el auto está en <zona>" → INSPECTION (PROPOSED) + FAQ + ubicación

═══ 2. POSTURA DEL CLIENTE (acceptance) — una sola por mensaje ═══
acceptance.signal describe la postura frente a algo que YA le propusimos:
  ACCEPT        acepta AHORA y explícitamente avanzar.
  REJECT        dice que no.
  HESITATE      duda sobre LA PROPUESTA: lo va a pensar, lo tiene que ver, le parece caro,
                no decidió, "capaz", "no sé". Pensarlo o mirarlo es duda, no promesa.
  FUTURE_INTENT promete volver / avisar / escribir / consultar MÁS ADELANTE.
  QUESTION_ONLY el mensaje es sólo una pregunta, sin postura.
Si el cliente expresa CONFORMIDAD con avanzar — aunque sea con una sola palabra
afirmativa — es ACCEPT. La regla de cortesía excluye sólo el agradecimiento o el saludo que
NO expresan conformidad: agradecer o saludar sin aceptar nada no es ACCEPT. Elegir o
corregir un día tampoco es una postura: eso es un pedido de turno.
Cada vez que emitas FUTURE_INTENT, preguntate también si el cliente dijo que todavía no
tiene el auto: si NO nombró ningún auto concreto y sigue buscando/mirando/consultando, van
LOS DOS ítems (READINESS + FUTURE_INTENT). Si nombró un auto, va sólo la postura.
FUTURE_INTENT exige una promesa explícita de volver/avisar/escribir. La simple indecisión
("no lo decidí", "no sé", "lo pienso") es HESITATE, no FUTURE_INTENT. Y si la promesa viene
con duda ("capaz", "quizás", "puede ser", "no sé si"), manda la DUDA: es HESITATE.
Contraste:
  "lo voy a pensar"        → HESITATE          "dale, avancemos"     → ACCEPT
  "si me cierra te escribo" → FUTURE_INTENT    "gracias!"            → sin acceptance
  "por ahora no"           → REJECT            "¿cuándo pueden?"     → ACCEPT si acepta avanzar
Además, si el cliente dice que TODAVÍA NO ELIGIÓ / NO TIENE el auto (sigue buscando,
mirando, no decidió cuál, "cuando lo tenga", "cuando decida"), emití SIEMPRE
service_intents kind=READINESS value=SEARCHING_NOT_READY. Eso es un HECHO sobre su proceso
de compra y CONVIVE con la postura; emitir la postura no te exime de emitir el hecho:
"sigo buscando, cuando encuentre te aviso" = READINESS SEARCHING_NOT_READY +
acceptance FUTURE_INTENT (los dos, no uno).

═══ 3. DÍA Y HORA ═══
S1. Si el cliente usó una expresión RELATIVA (hoy, mañana, pasado mañana — incluso
    abreviada o mal escrita), devolvé TODAY / TOMORROW / DAY_AFTER_TOMORROW. NUNCA la
    conviertas en el nombre del día que da la cuenta: el CONTEXTO TEMPORAL sirve para
    ENTENDER, no para convertir. Tampoco devuelvas fechas ISO: resolved_date lo calcula la
    capa determinística.
S2. Si ofrece varias opciones, mantené el ORDEN: la primera es PRIMARY, la siguiente
    FALLBACK. Cada hora pertenece SÓLO a la opción donde fue dicha; si una opción no trae
    hora, va time=null y flexible_time=true. Nunca traslades la hora de una opción a otra
    ni fusiones dos opciones en una.
S3. Si el cliente NOMBRA un día de la semana, ese es el día: no lo cambies por una
    expresión relativa. La regla de abreviaturas aplica SÓLO cuando la palabra está
    abreviada o mal escrita y no puede leerse como el nombre de un día: en ese caso, si
    apunta a hoy / mañana / pasado mañana, devolvé la expresión relativa correspondiente.
S4. Un día sin hora es un día sin hora: no inventes horario.
S5. Una FRANJA del día (por la mañana, a la tarde, temprano, al mediodía) NO es una hora:
    time=null y flexible_time=true. Sólo un horario concreto va en time, en formato HH:MM.
S6. Un número suelto junto a un día es la HORA de esa opción ("jueves 11" = jueves 11:00).
S7. Preguntar CUÁNDO podemos, qué horarios tenemos o qué disponibilidad hay NO es proponer
    un día: no inventes scheduling_requests para una pregunta de disponibilidad.

═══ 4. UBICACIONES: EL ROL NO DEPENDE DEL ORDEN ═══
L1. Una localidad dicha SOBRE EL AUTO ("está en X", "el auto está en X", "es un <modelo>
    en X", "lo tengo en X", "queda en X") es INSPECTION_LOCATION.
L2. Sólo es CUSTOMER_ORIGIN si la frase habla DEL CLIENTE ("yo soy de X", "vivo en X",
    "estoy en X", "me manejo desde X", "trabajo en X").
L3. Si se nombra UNA sola localidad y la frase es sobre el auto o sobre la COMPRA del auto
    ("estoy por comprar un usado en X", "lo vamos a ver en X"), es INSPECTION_LOCATION: ahí
    está el auto. NO inventes un origen del cliente. Si de verdad no podés decidir el rol,
    usá UNKNOWN_LOCATION_ROLE, no adivines.
L4. SÓLO son ubicaciones los NOMBRES DE LUGAR. Una expresión de tiempo, una muletilla o una
    palabra que no reconocés como localidad NO es una ubicación: si no estás seguro de que
    el token nombra un lugar, no emitas ninguna ubicación. En texto ruidoso, preferí omitir
    la ubicación antes que inventarla.
L5. Ojo con "en": introduce lugares PERO TAMBIÉN TIEMPO. "en breves", "en un rato", "en
    unos días" son expresiones de TIEMPO, no localidades. Nunca emitas una ubicación cuya
    palabra sea una expresión temporal, por más que venga precedida de "en".
Contraste:
  "El auto está en Berazategui"                → Berazategui INSPECTION_LOCATION
  "Está en Berazategui, pero yo soy de Tigre"  → Berazategui INSPECTION_LOCATION +
                                                  Tigre CUSTOMER_ORIGIN
  "Yo vivo en Tigre"                           → sólo Tigre CUSTOMER_ORIGIN

═══ 5. CORRECCIONES ═══
C1. Cuando el cliente REEMPLAZA o CORRIGE algo dicho antes (en este mensaje o en el turno
    anterior del mismo ciclo que figura en el contexto), devolvé DOS cosas: la corrección
    en corrections[] Y el valor corregido como evidencia normal (vehículo, año, localidad,
    día). El valor viejo va con is_superseded=true si es un vehículo.
C1b. El disparador es semántico: si el mensaje CONTRAPONE algo nuevo con algo anterior —
    lo descarta, lo rectifica, lo cambia, prefiere otra cosa o vuelve a una anterior —
    ESO ES una corrección, y el ítem en corrections[] es obligatorio aunque el valor viejo
    no aparezca escrito en el mensaje. Emitir sólo el valor nuevo es perder la corrección.
C2. relation: CORRECT_EXISTING (arregla un dato del mismo auto: año, localidad, día) |
    REPLACE_CANDIDATE (pasa a otro auto) | SWITCH_TO_PRIOR_CANDIDATE (vuelve a un auto
    anterior, sólo si el contexto del ciclo actual lo respalda) | ADD_SECOND_CANDIDATE.
C3. Nunca uses historia de ciclos anteriores: sólo lo que está en el contexto de este ciclo.
C4. Toda corrección lleva SIEMPRE su ítem en corrections[], incluso cuando lo corregido es
    sólo un año, sólo una localidad o sólo un día, y aunque no haya vehículo en el mensaje.
    Y al revés: el ítem de corrección NO reemplaza al valor corregido — si corregiste un
    año, el año corregido tiene que estar también como evidencia de vehículo.
C5. Volver a un auto mencionado antes también NOMBRA ese auto: emitilo en vehicles[] además
    de la corrección.
Contraste:
  "es 2015, no 2014"           → correction CORRECT_EXISTING + year 2015
  "no, es un <otro modelo>"    → correction REPLACE_CANDIDATE + vehículo nuevo +
                                  vehículo anterior is_superseded
  "mejor el jueves"            → correction CORRECT_EXISTING + scheduling THURSDAY

═══ 6. PEDIDO DE PRECIO ═══
Q1. Emití kind=QUOTE_REQUEST value=true SÓLO si el mensaje pregunta por dinero: precio,
    costo, valor, cotización, presupuesto, cuánto sale / cuánto cobran.
Q2. NO lo deduzcas de: dar un auto, dar una zona, preguntar por el servicio, mostrar
    interés, ni aceptar avanzar. Preguntar "¿hacen esto?" NO es pedir precio.
Q2b. La palabra "cuánto" sola no alcanza: "cuánto tarda" es DURACIÓN (tema de FAQ) y
    "cuánto falta" es tiempo. Sólo es QUOTE_REQUEST si pregunta por DINERO.
Q3. El dinero NO es un tema de FAQ. En el vocabulario de faq_topics no existe ningún tema
    de precio ni de cotización: una pregunta por plata va SIEMPRE en QUOTE_REQUEST y en
    ningún tema. (El tema `payment` es CÓMO se paga — medios de pago, antes o después —
    nunca CUÁNTO cuesta.)

═══ 7. TEMAS DE FAQ ═══
F1. Devolvé TODOS los temas consultados: recorré el mensaje pregunta por pregunta y emití
    un tema por cada una. Un tema no suprime a otro; tres preguntas son tres temas.
F2. Usá sólo el vocabulario de temas. No existe un tema genérico, y NO existe ningún tema
    de precio: `payment` es CÓMO o CUÁNDO se paga (medio de pago, antes o después), nunca
    CUÁNTO cuesta. Una pregunta por plata no es una FAQ: es QUOTE_REQUEST.
F3. Una FAQ NO borra el vehículo, la ubicación, la intención ni el pedido de día. Y al
    revés: una pregunta al final de un mensaje que empieza aceptando SIGUE siendo una FAQ —
    la postura y el tema conviven.

═══ 8. VEHÍCULO Y CATÁLOGO ═══
V1. Si menciona más de un vehículo, devolvé todos, en orden.
V1b. Hay modelos que se llaman con cifras. Si el número nombra un modelo conocido,
    completá igual su marca (status PROPOSED) además del modelo: un modelo numérico sin
    marca no identifica nada.
V2. Dos números donde uno nombra el modelo y el otro el año: conservá LOS DOS. Nunca
    descartes uno. Si no podés decidir cuál es cuál: status AMBIGUOUS + alternatives y
    year_status AMBIGUOUS.
V3. SIEMPRE devolvé el auto que el cliente nombró. Si el modelo implica una única marca,
    completá make con esa marca (y repetila en catalog_candidate). Lo que vos agregues y el
    cliente no haya dicho literalmente va como máximo con status PROPOSED — pero se
    devuelve, no se omite. El catálogo determinístico confirma después.
V4. El vehículo que QUEDA vigente va en vehicles[] con is_superseded=false. El que fue
    descartado va con is_superseded=true. No los inviertas: lo último que el cliente eligió
    es lo vigente.

Vocabulario permitido:
- service_intents[].kind: INSPECTION | QUOTE_REQUEST | READINESS | LOGISTICS_OFFER | OTHER
- service_intents[].value para INSPECTION: {SERVICE_INTENT_VALUES[0]}
- service_intents[].value para READINESS: {" | ".join(READINESS_VALUES)}
- service_intents[].value para QUOTE_REQUEST: true
- service_intents[].value para LOGISTICS_OFFER: CUSTOMER_OFFERS_TRANSPORT
- locations[].role: INSPECTION_LOCATION | CUSTOMER_ORIGIN | SELLER_LOCATION | UNKNOWN_LOCATION_ROLE
- faq_topics: {" | ".join(FAQ_TOPICS)}
- scheduling_requests[].day_expression: {" | ".join(DAY_EXPRESSIONS)}
- scheduling_requests[].priority: PRIMARY | FALLBACK | ADDITIONAL
- acceptance.signal: {" | ".join(ACCEPTANCE_VALUES)}
- corrections[].relation: CORRECT_EXISTING | REPLACE_CANDIDATE | SWITCH_TO_PRIOR_CANDIDATE
  | ADD_SECOND_CANDIDATE | UNKNOWN_RELATION

═══ EJEMPLOS DE SALIDA (forma, no vocabulario de superficie) ═══
Pregunta por plata:
  {{"service_intents": [{{"kind": "QUOTE_REQUEST", "value": true, "status": "CONFIRMED"}}]}}
Sigue buscando y promete volver (DOS ítems, uno de proceso y uno de postura):
  {{"service_intents": [{{"kind": "READINESS", "value": "SEARCHING_NOT_READY",
                        "status": "CONFIRMED"}}],
   "acceptance": {{"signal": "FUTURE_INTENT", "status": "CONFIRMED"}}}}
Dos opciones de día, la hora pertenece sólo a la primera:
  {{"scheduling_requests": [
     {{"priority": "PRIMARY",  "day_expression": "TOMORROW", "time": "15:00",
      "flexible_time": false, "rank": 1, "status": "CONFIRMED"}},
     {{"priority": "FALLBACK", "day_expression": "THURSDAY", "time": null,
      "flexible_time": true,  "rank": 2, "status": "CONFIRMED"}}]}}
Reemplazo de vehículo (el vigente sin marca de superseded, el descartado con ella):
  {{"corrections": [{{"relation": "REPLACE_CANDIDATE", "from_value": "<auto viejo>",
                    "to_value": "<auto nuevo>", "status": "CONFIRMED"}}],
   "vehicles": [{{"make": "<marca nueva>", "model": "<modelo nuevo>", "is_superseded": false,
                "status": "CONFIRMED"}},
                {{"make": "<marca vieja>", "model": "<modelo viejo>", "is_superseded": true,
                "status": "CONFIRMED"}}]}}
Corrección de un dato (la corrección Y el valor corregido):
  {{"corrections": [{{"relation": "CORRECT_EXISTING", "from_value": 2014, "to_value": 2015,
                    "status": "CONFIRMED"}}],
   "vehicles": [{{"model": null, "year": 2015, "status": "CONFIRMED",
                "year_status": "CONFIRMED"}}]}}

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

ANTES DE RESPONDER, repasá slot por slot:
- ¿El cliente dijo que todavía no tiene o no eligió NINGÚN auto? → falta READINESS
  (si ya nombró un auto, NO va).
- ¿Ofreció movilidad propia o llevar el auto? → falta LOGISTICS_OFFER.
- ¿El modelo que nombró es un número? → la marca de ese modelo tiene que estar igual.
- ¿Preguntó por dinero? → falta QUOTE_REQUEST (y no va como tema de FAQ).
- ¿Corrigió, reemplazó o descartó algo dicho antes? → falta el ítem en corrections[],
  ADEMÁS del valor nuevo y del viejo marcado is_superseded.
- ¿Hay más de una pregunta? → falta un tema de FAQ por cada una.
- ¿Usó una expresión relativa de día? → tiene que quedar relativa, no un día de la semana.
- ¿Alguna ubicación que emitiste podría ser una expresión de tiempo? → quitala.

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

# The controlled constant each intent kind carries when the interpreter names the kind but
# leaves the value out. Not a phrase rule: one value per schema kind.
_KIND_DEFAULT_VALUE = {
    "INSPECTION": SERVICE_INTENT_VALUES[0],
    "QUOTE_REQUEST": True,
    "READINESS": READINESS_VALUES[0],
    "LOGISTICS_OFFER": "CUSTOMER_OFFERS_TRANSPORT",
}


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

    # ── companion evidence (L4.7B.4) ──────────────────────────────────────────

    @staticmethod
    def _derive_companions(
        vehicles: list, corrections: list, prov: Provenance,
    ) -> tuple[list, list]:
        """Pair a fact with the relation that explains it, using only what was said.

        Two derivations, both strictly downstream of the interpreter's own output:

        * a vehicle marked `is_superseded` IS a replacement — if no correction accompanies
          it, the relation is recorded (the discarded car is the `from`, the surviving one
          the `to`);
        * a correction that moves a YEAR carries the corrected year — if no vehicle carries
          a year, the year becomes vehicle evidence.

        Nothing is invented: with no superseded vehicle and no year correction, both lists
        come back untouched.
        """
        # Only a REAL superseded mention counts. A template echo — an empty row that merely
        # carries is_superseded=true — is pruned a moment later as semantically empty, and
        # deriving a replacement from it would invent a correction the customer never made.
        # A replacement needs a NAMED car on the discarded side. A row that only carries
        # `is_superseded=true` with no value is a template echo, and deriving a correction
        # from it would invent a change the customer never made.
        superseded = [v for v in vehicles if v.is_superseded and v.value]
        current = [v for v in vehicles if not v.is_superseded and v.value]
        if superseded and not corrections:
            corrections = list(corrections) + [CorrectionEvidence(
                value=True,
                relation=CorrectionRelation.REPLACE_CANDIDATE,
                from_value=superseded[0].value,
                to_value=(current[0].value if current else None),
                status=superseded[0].status,
                reason="derived from a superseded vehicle mention",
                provenance=prov)]

        if not any(v.year is not None for v in vehicles):
            for correction in corrections:
                year = _coerce_year(correction.to_value)
                if year is None:
                    continue
                vehicles = list(vehicles) + [VehicleEvidence(
                    value=None, year=year, year_status=correction.status,
                    status=correction.status,
                    reason="corrected year carried by a correction",
                    mention_index=len(vehicles), provenance=prov)]
                break

        return list(vehicles), list(corrections)

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
        # L4.7B.4: the process fact may arrive in its own slot or inside the intents array.
        # Both spellings mean the same thing and map to the same evidence item.
        raw_readiness = payload.get("readiness")
        if isinstance(raw_readiness, dict):
            intents.append(ServiceIntentEvidence(
                field="readiness", kind=ServiceIntentKind.READINESS,
                value=(_clean(raw_readiness.get("value")) or READINESS_VALUES[0]),
                status=_status(raw_readiness.get("status")),
                reason=raw_readiness.get("reason"), provenance=prov))
        elif isinstance(raw_readiness, str) and _clean(raw_readiness):
            intents.append(ServiceIntentEvidence(
                field="readiness", kind=ServiceIntentKind.READINESS,
                value=_clean(raw_readiness), provenance=prov))
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
            # L4.7B.3: for these kinds the KIND is the evidence and the value is a controlled
            # constant. When the model names the kind but omits the value, the item used to
            # be dropped as "semantically empty" — a silent loss, not an interpretation.
            value = _clean(raw.get("value"))
            if value is None:
                value = _KIND_DEFAULT_VALUE.get(kind.value)
            intents.append(ServiceIntentEvidence(
                field=field, kind=kind, value=value,
                status=_status(raw.get("status")), reason=raw.get("reason"),
                confidence=_confidence(raw.get("confidence")), provenance=prov))

        seen_readiness = 0
        deduped: list[ServiceIntentEvidence] = []
        for item in intents:
            if item.field == "readiness":
                seen_readiness += 1
                if seen_readiness > 1:
                    continue
            deduped.append(item)
        intents = deduped

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
                value=(True if (from_value or to_value or raw.get("target_ref")
                                or relation.value != CorrectionRelation.UNKNOWN_RELATION.value)
                       else None),
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

        # ── L4.7B.4: companion evidence ──────────────────────────────────────
        # A turn that carries a fact AND the relation explaining how it changes prior state
        # must carry both. These derivations add nothing about the customer: they reshape
        # what the interpreter itself already said into the slot the schema keeps for it.
        vehicles, corrections = self._derive_companions(vehicles, corrections, prov)

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
