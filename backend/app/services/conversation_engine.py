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
import os
import re
import secrets
import time as _time
import unicodedata
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
from ..services.vehicle_catalog import (
    VehicleMatch, lookup_vehicle, fuzzy_lookup_vehicle, FuzzyLookupResult,
)
from ..services.field_evidence import (
    resolve_field_evidence,
    INSP_ASSEMBLED_ACCESSIBLE,
    INSP_DISASSEMBLED_BLOCKED,
    INSP_UNRESOLVED_NON_RUNNING,
)
from ..services.narrative_schema import (
    DEFERRED_RESPONSE_ES,
    NarrativeInterpretation,
    parse_narrative_interpretation,
    narrative_needs_ai,
)
from ..services.outbound_guard import OutboundBlockedError
from ..services.outbound_safety_gate import GateOutcome, OutboundSafetyGate
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
    "sí", "si", "yes", "ok", "okay", "dale", "perfecto", "avancemos",
    "listo", "buenísimo", "me sirve", "bueno", "claro",
    "de acuerdo", "por supuesto", "quiero avanzar",
})


def _is_acceptance(texts: list[str]) -> bool:
    """Return True when all user messages together express unambiguous acceptance."""
    combined = " ".join(texts).strip()
    normalized = combined.lower().strip("!.¡ ").strip()
    if normalized in _ACCEPTANCE_KEYWORDS:
        return True
    # Handles word combinations like "Si avancemos" / "Sí, avancemos" where each
    # individual token is itself an acceptance word but the combined string is not.
    words = re.sub(r"[^\w\s]", " ", normalized).split()
    return bool(words) and all(w in _ACCEPTANCE_KEYWORDS for w in words)


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

# Dock Sud aliases: "Doc Sud", "Dock Sud", and common misspellings all map to the
# canonical zone_detail stored in viaticos_zones (zone_group=Sur, viaticos=30000).
# None of these are CABA or La Boca — Dock Sud is in Partido de Avellaneda.
_DOCK_SUD_ALIASES: frozenset[str] = frozenset({
    "dock sud",
    "doc sud",
    "dock sue",
    "doc sue",
    "dique sud",
    "dique sue",
})
_DOCK_SUD_CANONICAL = "Dock Sud"
_DOCK_SUD_GROUP = "Sur"

# Phone-call escalation: customer asking to call instead of chat.
# Detected deterministically before the AI to avoid false "podés llamarme" replies.
_PHONE_CALL_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r'\bllam(?:ar(?:me|te|nos)?|ame|ame)\b', re.IGNORECASE),
    re.compile(r'\bme\s+pod[eé]s?\s+llam(?:ar|aste)\b', re.IGNORECASE),
    re.compile(r'\bpued[oa]?\s+llam(?:ar(?:te|los?)?|ame)\b', re.IGNORECASE),
    re.compile(r'\bte\s+puedo\s+llam(?:ar|aste)\b', re.IGNORECASE),
    re.compile(r'\bquiero\s+llam(?:ar(?:te|los?)?\b)', re.IGNORECASE),
    re.compile(r'\bhac(?:er|emos)\s+una\s+llam(?:ada|ado)\b', re.IGNORECASE),
    re.compile(r'\bhablar\s+con\s+(alguien|un\s+asesor|un\s+agente|alg[uú]n\s+asesor)\b', re.IGNORECASE),
    re.compile(r'\bque\s+me\s+llam(?:en|e)\b', re.IGNORECASE),
    re.compile(r'\bpued(?:en|an)\s+llam(?:ar(?:me|nos)?|arme)\b', re.IGNORECASE),
)

# Guard against AI asking the customer for price/cotización info when missing
# data is vehicle or zone.  Scrubbed before the reply is sent.
_PRICE_REQUEST_RE = re.compile(
    r'(?:confirm(?:ar|a|ame|ar[nm]e)\s+(?:el\s+)?(?:precio|cotizaci[oó]n|importe|valor|presupuesto)'
    r'|(?:cu[aá]l\s+es|me\s+dec[ií]s?|me\s+pas[aá]s?|me\s+pod[eé]s?\s+(?:decir|pasar|indicar))'
    r'\s+(?:el\s+)?(?:precio|cotizaci[oó]n|importe|valor|presupuesto\s+del\s+(?:auto|veh[ií]culo)))',
    re.IGNORECASE,
)


# Qualification fallback Flow: zone_general values from Location Flow payload
# map to canonical internal zone_group names. None means unresolvable → human.
_FLOW_ZONE_GROUP_MAP: dict[str, str | None] = {
    "CABA": "CABA",
    "NORTE": "Norte",
    "OESTE": "Oeste",
    "SUR": "Sur",
    "OTRO": None,
}

# Allowed tipo_vehiculo values from the Vehicle Fallback Flow payload.
# OTRO and missing values trigger human escalation.
_FALLBACK_VEHICLE_TYPES: frozenset[str] = frozenset({
    "AUTO", "SUV_4X4_DEPORTIVO", "PICKUP", "UTILITARIO_FURGON",
})

_FALLBACK_WARM_HANDOFF = (
    "Gracias por completar el formulario. Un agente de Ridecheck "
    "te va a contactar a la brevedad para continuar con la cotización."
)

# ── M21.1.1 Service Intent Gate ───────────────────────────────────────────────
# _PHONE_CALL_PATTERNS     — [EXISTING] line 142 — do not redefine
# _is_phone_call_request() — [EXISTING] line 307 — do not redefine
# _FALLBACK_WARM_HANDOFF   — [EXISTING] line 177 — do not redefine

# Sentinel returned by the qualifying gate to signal FAQ bypass (checked with `is`).
_GATE_FAQ_BYPASS = object()

# Motorcycle/quad/UTV detection — applied to accent-normalized lowercase text.
# Uses lookbehind/lookahead to enforce word boundaries with plural support.
_MOTORCYCLE_PATTERN = re.compile(
    r'(?<![a-z0-9])(motos?|motocicletas?|scooters?|ciclomotor(?:es)?|cuatriciclos?|quads?|atvs?|utvs?)(?![a-z0-9])',
    re.IGNORECASE,
)

# Formulario 12 detection
_F12_REQUEST_PHRASES: frozenset[str] = frozenset({
    "formulario 12", "formulario doce", "formulario12",
    "f 12", "formulario 12b",
})
_F12_PAST_CONTEXT: frozenset[str] = frozenset({
    "ya hice el formulario", "ya tenia el formulario",
    "ya tengo el formulario", "tengo el formulario hecho",
    "ya hice un formulario",
})
# If past-context AND any of these signals coexist, the new request wins.
_F12_NEW_REQUEST_SIGNALS: frozenset[str] = frozenset({
    "pero necesito", "pero quiero", "pero ahora",
    "y tambien", "y también", "necesito otro", "quiero otro",
    "y ademas", "y además", "pero tambien", "pero también",
    "ahora necesito",
})

# Transfer detection
_TRANSFER_REQUEST_PATTERNS = [
    r'\b(hacer|necesito|quiero|tramitar)\s+(la\s+)?transferencia\b',
    r'\b(hagan|gestionen|tramiten|encarguen)\s+(la\s+)?transferencia\b',
    r'\btramitaci[oó]n\b',
    r'\btramitar\b',
    r'\bcambio\s+de\s+titular\b',
    r'\bgesti[oó]n\s+de\s+(papeles|documentos|documentaci[oó]n)\b',
    r'\b(gestoria|gestor[íi]a)\b',
]
# Payment-method or future-step exclusions; can be overridden by explicit request.
_TRANSFER_EXCLUSIONS: frozenset[str] = frozenset({
    "pagar por transferencia", "pago por transferencia",
    "transferencia bancaria", "hago la transferencia",
    "despues de la revision", "después de la revisión",
    "una vez que revisen", "cuando me lo revisen",
})
_EXPLICIT_RIDECHECK_TRANSFER_PATTERNS = [
    r'\bque\s+ustedes\s+(hagan|gestionen|tramiten|encarguen)\b',
    r'\bque\s+(lo|la)\s+(gestionen|tramiten|encarguen)\b',
    r'\bnecesito\s+que\s+ustedes\s+.{0,40}(transferencia|tramite|papeles)\b',
    r'\bquiero\s+que\s+ustedes\s+.{0,40}(transferencia|tramite|papeles)\b',
]

# Repair detection
_REPAIR_REQUEST_PATTERNS = [
    r'\b(reparar|arreglar)\s+(el\s+)?(auto|coche|veh[íi]culo|motor|frenos?)\b',
    r'\b(quiero|necesito|pueden)\s+(que\s+)?(lo\s+)?(arreglen|reparen|arregles|repares)\b',
    r'\bnecesito\s+que\s+(\w+\s+)?(arreglen|reparen|arregles|repares)\b',
    r'\breparaci[oó]n\s+mec[aá]nica\b',
    r'\bservicio\s+mec[aá]nico\b',
    r'\bdiagn[oó]stico\s+mec[aá]nico\b',
    r'\btaller\s+mec[aá]nico\b',
]
# Inspection context: suppresses repair match unconditionally.
_REPAIR_EXCLUSIONS_INSPECTION: frozenset[str] = frozenset({
    "revisar antes de comprar",
    "inspeccion antes de comprar", "inspección antes de comprar",
    "revision precompra", "revisión precompra",
    "quiero que lo revisen antes",
})
# Third-party mechanic context: suppresses unless an explicit RideCheck request coexists.
_REPAIR_EXCLUSIONS_MECHANIC: frozenset[str] = frozenset({
    "mi mecanico", "mi mecánico",
    "el mecanico", "el mecánico",
    "tu mecanico", "tu mecánico",
    "un mecanico", "un mecánico",
})
# Explicit RideCheck-directed repair: checked first; return True immediately.
_EXPLICIT_RIDECHECK_REPAIR_PATTERNS = [
    r'\b(quiero|necesito)\s+que\s+(me\s+)?(lo|la)\s+(arreglen|reparen|arregles|repares)\b',
    r'\bpero\s+necesito\s+que\s+(\w+\s+)?(arreglen|reparen|arregles|repares)\b',
    r'\bnecesito\s+que\s+ustedes\s+(arreglen|reparen)\b',
    r'\bquiero\s+que\s+ustedes\s+(arreglen|reparen)\b',
    r'\bque\s+(me\s+lo\s+|me\s+la\s+)?(arreglen|reparen)\b',
]

# Pre-purchase intent signals
_PREPURCHASE_SIGNALS: frozenset[str] = frozenset({
    "revision precompra", "revisión precompra",
    "revision pre-compra", "revisión pre-compra",
    "revision pre compra", "revisión pre compra",
    "inspeccion precompra", "inspección precompra",
    "inspeccion pre-compra", "inspección pre-compra",
    "revisar antes de comprar", "revisarlo antes de comprar",
    "verificar antes de comprar",
    "ver el estado antes de comprar",
    "inspeccionar antes de comprar",
    "quiero revisar el auto antes",
    "revisar el auto antes de",
    "antes de comprarlo",
    "antes de señarlo", "antes de adquirirlo",
    "cuanto sale revisar", "cuánto sale revisar",
    "cotizar una revision", "cotizar una revisión",
    "cotizar la revision", "cotizar la revisión",
    "hacer una revision", "hacer una revisión",
    "necesito una inspeccion", "necesito una inspección",
    # M21.1.1-R1: Argentine Spanish pre-purchase phrasings
    "estoy por comprar",
    "estoy pensando en comprar",
    "por comprar el auto",
    "por comprar el vehiculo",
    "quiero comprar el auto",
    "quiero comprar un auto",
})

# FAQ / greeting / informational patterns (applied to accent-normalized text).
_FAQ_PATTERNS = [
    r'\bqu[eé]\s+incluye\b',
    r'\bqu[eé]\s+cubre\b',
    r'\bqu[eé]\s+revisan\b',
    r'\bcu[aá]nto\s+(tarda|demora|dura|tiempo)\b',
    r'\btengo\s+que\s+estar\s+presente\b',
    r'\btrabajan?\s+(los\s+)?(s[aá]bados?|domingos?|fin\s+de\s+semana)\b',
    r'\bc[oó]mo\s+se\s+entrega\b',
    r'\bqu[eé]\s+es\s+ridecheck\b',
    r'\bc[oó]mo\s+funciona\b',
    r'\bd[oó]nde\s+atienden\b',
    r'\bsalen?\s+a\s+domicilio\b',
    r'\bvan\s+al\s+lugar\b',
    r'\bqu[eé]\s+verifican\b',
    r'\bcosto\s+del\s+servicio\b',
    r'^(hola|buenas?|buen\s+d[ií]a|buenos\s+d[ií]as|buenas\s+tardes|buenas\s+noches)[,\s!.]*$',
    r'^(hola[,\s]+)?quer[ií]a\s+(hacer|hacer\s+una)\s+consulta',
    r'\bconsulta\b',
]

# M21.1.1-R1: Inspection request patterns for Layer F pass-through.
# Applied to accent-normalized, lowercase text.
_INSPECTION_REQUEST_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r'\b(quiero|necesito|quisiera)\s+que\s+(me\s+)?revis[ae]n\b', re.IGNORECASE),
    re.compile(r'\b(quiero|necesito|quisiera)\s+revis[ae]r\s+(un[ao]?|el|la)\b', re.IGNORECASE),
    re.compile(r'\b(quiero|necesito|quisiera)\s+(hacer\s+(una\s+)?)?revisi[oó]n\b', re.IGNORECASE),
    re.compile(r'\b(quiero|necesito|quisiera)\s+(una|la)\s+(inspecci[oó]n|revisi[oó]n)\b', re.IGNORECASE),
    re.compile(r'\bcoordinar\s+(una|la)\s+revisi[oó]n\b', re.IGNORECASE),
    re.compile(r'\bme\s+inter[eé]sa\s+coordinar\b', re.IGNORECASE),
    # M21.1.1-R2: "quiero/necesito cotizar" signals inspection pricing intent
    re.compile(r'\b(quiero|necesito|quisiera)\s+cotizar\b', re.IGNORECASE),
)

# M21.1.1-R1: Vehicle-location phrase patterns ("está en/por X", "se encuentra en").
_VEHICLE_LOCATION_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r'\best[aá]\s+(en|por|cerca\s+de)\b', re.IGNORECASE),
    re.compile(r'\bse\s+encuentra\s+(en|por)\b', re.IGNORECASE),
    re.compile(r'\bqueda\s+(en|por)\b', re.IGNORECASE),
    re.compile(r'\bubicado\s+(en|cerca\s+de)\b', re.IGNORECASE),
)

# BR-1 Rows 3–4: inspection-specific and generic price questions establish intent on fresh threads.
_PRICE_QUESTION_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r'\bcu[aá]nto\s+(sale|cuesta|vale|cobran|es)\b', re.IGNORECASE),
    re.compile(r'\bcotizaci[oó]n\b', re.IGNORECASE),
    re.compile(r'\bme\s+pas[aá]s?\s+(precio|cotizaci[oó]n|presupuesto)\b', re.IGNORECASE),
    re.compile(r'\bprecio\b', re.IGNORECASE),
)

# Reply constants
_PHONE_CALL_HANDOFF_REPLY = (
    "¡Claro! Un agente de Ridecheck te va a contactar a la brevedad. "
    "Si preferís agilizarlo, también podés escribirnos a info@ridecheck.ar."
)
_F12_BOUNDARY_REPLY = (
    "Nosotros realizamos revisiones precompra; no gestionamos el Formulario 12."
)
_TRANSFER_BOUNDARY_REPLY = (
    "Nos especializamos en revisiones pre-compra de vehículos. "
    "Para trámites de transferencia o gestión documental, "
    "te recomendamos consultar con una gestoría."
)
_REPAIR_BOUNDARY_REPLY = (
    "Nosotros hacemos inspecciones pre-compra de vehículos. "
    "¿Estás pensando en comprar un auto y querés que lo revisemos antes?"
)
_UNCERTAIN_SERVICE_REPLY = (
    "¡Hola! Somos Ridecheck y nos especializamos en revisiones pre-compra de autos. "
    "¿Estás pensando en comprar un vehículo y querés que lo revisemos antes de decidir?"
)

# ── M21.1.2 Vehicle inspectability — approved customer copy ──────────────────
_INSPECTABILITY_DISASSEMBLED_REPLY = (
    "Para poder hacer la revisión, el vehículo tiene que estar armado y accesible. "
    "Si está desarmado, no podemos inspeccionarlo correctamente."
)
_INSPECTABILITY_NONRUNNING_CLARIFY = (
    "¿El vehículo está armado y se puede acceder normalmente al motor, interior, "
    "ruedas y parte inferior, aunque no arranque?"
)

# ── M21.1.2 Detection pattern lists ──────────────────────────────────────────
_INSPECTABILITY_DISASSEMBLED_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r'\bdesarmad[oa]\b', re.IGNORECASE),
    re.compile(r'\bdesmontad[oa]\b', re.IGNORECASE),
    re.compile(r'\bsin\s+motor\b', re.IGNORECASE),
    re.compile(r'\bmotor\s+(?:est[aá]\s+)?afuera\b', re.IGNORECASE),
    re.compile(r'\bmotor\s+desmontado\b', re.IGNORECASE),
    re.compile(r'\bsin\s+ruedas?\b', re.IGNORECASE),
    re.compile(r'\bpartes?\s+sueltas?\b', re.IGNORECASE),
    re.compile(r'\bpiezas?\s+desmontadas?\b', re.IGNORECASE),
]
_INSPECTABILITY_NONRUNNING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r'\bno\s+arranca\b', re.IGNORECASE),
    re.compile(r'\bno\s+enciende\b', re.IGNORECASE),
    re.compile(r'\bno\s+prende\b', re.IGNORECASE),
    re.compile(r'\bbater[ií]a\s+muerta\b', re.IGNORECASE),
    re.compile(r'\best[aá]\s+parad[oa]\b', re.IGNORECASE),
    re.compile(r'\bno\s+funciona\b', re.IGNORECASE),
    re.compile(r'\bno\s+est[aá]\s+andando\b', re.IGNORECASE),
    re.compile(r'\bhay\s+que\s+empujarlo\b', re.IGNORECASE),
    re.compile(r'\bhay\s+que\s+remolcarlo\b', re.IGNORECASE),
]
_INSPECTABILITY_ASSEMBLED_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r'\best[aá]\s+armad[oa]\b', re.IGNORECASE),
    re.compile(r'\best[aá]\s+complet[oa]\b', re.IGNORECASE),
    re.compile(r'\bse\s+puede\s+revisar\b', re.IGNORECASE),
    re.compile(r'\bse\s+puede\s+acceder\b', re.IGNORECASE),
    re.compile(r'\bse\s+puede\s+abrir\b', re.IGNORECASE),
    re.compile(r'\bse\s+puede\s+levantar\b', re.IGNORECASE),
    re.compile(r'\btiene\s+todas?\s+las\s+ruedas?\b', re.IGNORECASE),
    re.compile(r'\bel\s+motor\s+est[aá]\s+puest[oa]\b', re.IGNORECASE),
    re.compile(r'\best[aá]\s+accesible\b', re.IGNORECASE),
]
_INSPECTABILITY_HISTORICAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r'\bestaba\b', re.IGNORECASE),
    re.compile(r'\bestuvo\b', re.IGNORECASE),
    re.compile(r'\bantes\b', re.IGNORECASE),
    re.compile(r'\bsi\s+estuviera\b', re.IGNORECASE),
    re.compile(r'\bhipot[eé]ticamente\b', re.IGNORECASE),
]
_INSPECTABILITY_ACCESS_BARRIER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r'\bno\s+tenemos?\s+las?\s+llaves?\b', re.IGNORECASE),
    re.compile(r'\bsin\s+llaves?\b', re.IGNORECASE),
    re.compile(r'\bno\s+(nos?|te|le)\s+dejan\b', re.IGNORECASE),
    re.compile(r'\bno\s+s[eé]\s+si\s+(nos?|te|le)\s+dejan\b', re.IGNORECASE),
]

# ── M21.1.3 Location role inference ──────────────────────────────────────────

_LOCATION_CONTRADICTION_CLARIFICATION = (
    "¿Dónde está físicamente el auto para hacer la revisión?"
)

# Explicit vehicle-location clauses. Each pattern has exactly one capture group
# for the locality text (1–4 words). The 7th pattern captures alternative-location
# continuations ("o puede ser X") which, combined with uncertainty markers, signals
# a same-turn contradiction (SC17).
_VEHICLE_LOCATION_CLAUSE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r'\bel\s+(?:auto|veh[ií]culo|coche|m[oó]vil)\s+(?:ahora\s+)?'
        r'est[aá](?:\s+ubicad[ao])?\s+en\s+((?:\w+\s*){1,4})',
        re.IGNORECASE,
    ),
    re.compile(
        r'\best[aá]\s+para\s+revisar\s+en\s+((?:\w+\s*){1,4})',
        re.IGNORECASE,
    ),
    re.compile(
        r'\bla\s+revisi[oó]n\s+ser[ií]a\s+en\s+((?:\w+\s*){1,4})',
        re.IGNORECASE,
    ),
    re.compile(
        r'\bhay\s+que\s+verlo\s+en\s+((?:\w+\s*){1,4})',
        re.IGNORECASE,
    ),
    re.compile(
        r'\blo\s+tienen\s+en\s+((?:\w+\s*){1,4})',
        re.IGNORECASE,
    ),
    re.compile(
        r'\best[aá]\s+ubicad[ao]\s+en\s+((?:\w+\s*){1,4})',
        re.IGNORECASE,
    ),
    re.compile(
        r'\bo\s+puede\s+(?:ser|estar)\s+(?:en\s+)?((?:\w+\s*){1,3})',
        re.IGNORECASE,
    ),
]

# Customer-origin language. These phrases describe where the CUSTOMER is,
# not where the vehicle is for inspection (LR-2).
_CUSTOMER_ORIGIN_RE = re.compile(
    r'\b(?:soy\s+de|vivo\s+en|estoy\s+en|vengo\s+de|me\s+encuentro\s+en)\b',
    re.IGNORECASE,
)

# Uncertainty markers that, combined with two distinct vehicle-location zones,
# signal a same-turn contradiction (SC17 / LR-8).
_LOCATION_UNCERTAINTY_RE = re.compile(
    r'\b(?:o\s+puede\s+(?:ser|estar)|no\s+s[eé]|no\s+estoy\s+segur[ao]|'
    r'tal\s+vez|quiz[aá]s)\b',
    re.IGNORECASE,
)


def _has_customer_origin_clause(text: str) -> bool:
    """True when text contains explicit customer-origin language (LR-2)."""
    return bool(_CUSTOMER_ORIGIN_RE.search(text))


# ── M21.1.4: ASR fuzzy vehicle confirmation ───────────────────────────────────
_FUZZY_CONFIRMATION_TEMPLATE = "¿Es un {marca} {modelo}?"

_FUZZY_ACCEPTANCE_RE = re.compile(
    r'\b(?:s[íi]|s[íi]\s+es|exacto|correcto|eso\s+es|es\s+ese|efectivamente|'
    r'confirm[oa]|dale|ok)\b',
    re.IGNORECASE,
)
_FUZZY_REJECTION_RE = re.compile(
    r'\b(?:no(?:\s+es|\s+era|\s+ese)?|nada\s+que\s+ver|incorrecto|equivocado)\b',
    re.IGNORECASE,
)
_FUZZY_ASK_VEHICLE_REPLY = (
    "Para cotizarte la revisión necesito saber qué vehículo tenés. "
    "¿Me podés indicar la marca y el modelo?"
)


# Persisted intent token (22 chars, fits String(30) in WhatsAppThreadState.last_intent).
_INTENT_PREPURCHASE = "PREPURCHASE_INSPECTION"

# ── M20.6D.2B Routing gate constants ─────────────────────────────────────────

# Clearly out-of-coverage cities/provinces. Accent-stripped lowercase forms;
# matched against _norm_lower(message). GBA localities are NOT listed here.
_OUTSIDE_COVERAGE_CITIES: frozenset[str] = frozenset({
    "cordoba", "rosario", "mendoza", "tucuman", "santa fe",
    "mar del plata", "salta", "jujuy", "san juan", "san luis",
    "neuquen", "bariloche", "bahia blanca", "resistencia",
    "corrientes", "posadas", "formosa", "rio cuarto",
    "comodoro rivadavia", "rio gallegos", "santa rosa", "rawson", "ushuaia",
})

# Words indicating the customer has a concern about the vehicle condition.
_VEHICLE_CONCERN_WORDS: frozenset[str] = frozenset({
    "temperatura", "calor", "sobrecalenta", "humo", "fuma",
    "ruido", "golpe", "traqueteo", "vibracion", "vibra",
    "fuga", "pierde", "perdida",
    "freno", "frenos",
    "falla", "fallo", "problema", "preocupa",
})

# Words/phrases signalling the customer wants a price or quote.
# Accent-stripped lowercase; matched as substrings of _norm_lower(message).
_PRICE_INTENT_WORDS: frozenset[str] = frozenset({
    "cuanto sale", "cuanto cuesta", "cuanto seria",
    "cuanto cobra", "cuanto esta el servicio",
    "precio", "cotizacion", "cotizar", "presupuesto",
})

# Location Flow body — concern-aware variant
_LOC_FLOW_BODY_CONCERN = (
    "Entiendo. En la revisión podemos revisar ese punto junto con el resto del auto.\n\n"
    "Para calcular los viáticos, completá dónde está el vehículo."
)
# Location Flow body — standard (no concern)
_LOC_FLOW_BODY_STANDARD = (
    "Para calcular los viáticos de la revisión, completá dónde está el auto."
)
# Vehicle Flow body — generic vehicle or logistics question
_VEH_FLOW_BODY_GENERIC = (
    "La revisión se realiza donde está el auto.\n\n"
    "Para cotizarte bien, completá estos datos del vehículo."
)
# Vehicle Flow body — garbled/unrecognised vehicle model
_VEH_FLOW_BODY_GARBLED = (
    "Para cotizarte bien, completá estos datos del vehículo."
)
# Out-of-coverage deterministic response (exact copy)
_COVERAGE_RESPONSE = (
    "Por el momento trabajamos en CABA y GBA.\n\n"
    "Para una revisión fuera de esa zona tenemos que evaluar la logística. "
    "Te confirmamos si podemos hacerlo."
)

# Accent-stripped lowercase phrases that signal a service FAQ or soft conversation close.
# Matched as substrings of _norm_lower(message). Conservative set — do not add price
# or concern words here; those are handled by _wants_price_quote / _has_vehicle_concern.
_FAQ_OR_SOFT_CLOSE_PHRASES: frozenset[str] = frozenset({
    # Service-information / FAQ
    "que revisan",
    "que incluye",
    "cuanto dura",
    "como trabajan",
    "como hacen",
    "con cuanto tiempo de anticipo",
    "con cuanta anticipacion",
    "como coordinan",
    # Soft-close / polite exit
    "gracias",
    "lo tengo en cuenta",
    "los tengo en cuenta",
    "cualquier cosa te escribo",
    "te aviso",
    "por ahora no",
    "mas adelante",
    # M21.1.1-R1: Argentine info-request pass-through ("¿Me pasás info?")
    "me pasas info",
})


def _norm_lower(s: str) -> str:
    """Accent-strip, lowercase, and collapse whitespace — for keyword matching."""
    return " ".join(_strip_accents(s).lower().split())


def _is_outside_coverage(text: str) -> bool:
    n = _norm_lower(text)
    return any(city in n for city in _OUTSIDE_COVERAGE_CITIES)


def _has_vehicle_concern(text: str) -> bool:
    n = _norm_lower(text)
    return any(w in n for w in _VEHICLE_CONCERN_WORDS)


def _wants_price_quote(text: str) -> bool:
    n = _norm_lower(text)
    return any(phrase in n for phrase in _PRICE_INTENT_WORDS)


def _is_generic_vehicle_text(text: str) -> bool:
    """True when text mentions 'auto'/'vehículo' generically (not a specific model)."""
    n = _norm_lower(text)
    return "auto" in n.split() or "vehiculo" in n


def _is_general_faq_or_soft_close(text: str) -> bool:
    """True when text signals a service FAQ, info question, or soft conversation close.

    Deterministic substring match — no LLM involved. Conservative phrase set.
    Caller must separately guard against concern words and price-intent phrases
    to ensure those paths are not suppressed.
    """
    n = _norm_lower(text)
    return any(phrase in n for phrase in _FAQ_OR_SOFT_CLOSE_PHRASES)


def _is_caba_synonym(value: str) -> bool:
    """Return True if value (case-insensitive) is a known CABA synonym."""
    return " ".join((value or "").lower().split()) in _CABA_SYNONYMS


def _is_dock_sud_alias(value: str) -> bool:
    """Return True if value is a known Dock Sud alias (case-insensitive)."""
    return " ".join((value or "").lower().split()) in _DOCK_SUD_ALIASES


def _is_phone_call_request(messages: list[str]) -> bool:
    """Return True if any message contains a phone-call request pattern."""
    combined = " ".join(messages)
    return any(p.search(combined) for p in _PHONE_CALL_PATTERNS)


# ── M21.1.2 Vehicle inspectability detection functions ───────────────────────

def _detect_disassembled_vehicle(text: str) -> bool:
    return any(p.search(text) for p in _INSPECTABILITY_DISASSEMBLED_PATTERNS)


def _detect_non_running_vehicle(text: str) -> bool:
    return any(p.search(text) for p in _INSPECTABILITY_NONRUNNING_PATTERNS)


def _detect_assembled_accessible_confirmation(text: str) -> bool:
    return any(p.search(text) for p in _INSPECTABILITY_ASSEMBLED_PATTERNS)


def _detect_historical_or_hypothetical_context(text: str) -> bool:
    return any(p.search(text) for p in _INSPECTABILITY_HISTORICAL_PATTERNS)


def _detect_access_barrier(text: str) -> bool:
    return any(p.search(text) for p in _INSPECTABILITY_ACCESS_BARRIER_PATTERNS)


def _extract_year_from_text(text: str) -> int | None:
    m = _VEHICLE_YEAR_RE.search(text)
    return int(m.group(1)) if m else None


# ── Scheduling escalation ─────────────────────────────────────────────────────

# Phrases that indicate the customer is insisting on an unavailable slot or
# rejecting all alternatives — triggers human handoff.
_ESCALATION_KEYWORDS: frozenset[str] = frozenset({
    "solo puedo",
    "necesito a las",
    "no me sirve",
    "no puedo por la tarde",
    "no puedo por la mañana",
    "no puedo por la manana",
    "sí o sí",
    "si o si",
    "no tenés a las",
    "no tenes a las",
    "podés hacer una excepción",
    "podes hacer una excepcion",
    "hablá con julián",
    "habla con julian",
    "quería ese horario",
    "queria ese horario",
    "no, quería",
    "no, queria",
    "me lo podés ver",
    "me lo podes ver",
    "excepción",
    "excepcion",
    "ninguno me viene",
    "ninguno me sirve",
    "no me viene ninguno",
    "no tengo otro horario",
    "no puedo otro día",
    "no puedo otro dia",
})

_AFTERNOON_HOUR: int = 13  # slots at HH >= 13 are classified as "tarde"

_SPANISH_WEEKDAY_NAMES: list[str] = [
    "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo",
]

# Phrases indicating the user cannot open the WhatsApp Flow button
# (WhatsApp Web, desktop browser, or UI glitch).
_FLOW_FAILURE_KEYWORDS: frozenset[str] = frozenset({
    "no me abre",
    "no abre el formulario",
    "no funciona el formulario",
    "no puedo abrir",
    "no puedo completar",
    "no me aparece el formulario",
    "estoy en web",
    "whatsapp web",
    "desde la computadora",
    "desde la compu",
    "desde el pc",
    "no me carga",
    "no carga el formulario",
})


def _format_date_human(date_iso: str, today: date | None = None) -> str:
    """Human-readable date: 'mañana viernes 19/06' or 'lunes 22/06'."""
    try:
        d = date.fromisoformat(date_iso)
    except (ValueError, TypeError):
        return date_iso
    today = today or date.today()
    delta = (d - today).days
    prefix = "hoy " if delta == 0 else ("mañana " if delta == 1 else "")
    day_name = _SPANISH_WEEKDAY_NAMES[d.weekday()]
    return f"{prefix}{day_name} {d.strftime('%d/%m')}"


def _time_hour(time_str: str) -> int:
    """Extract the hour component from an HH:MM or YYYY-MM-DDTHH:MM string."""
    try:
        s = str(time_str)
        if "T" in s:
            s = s.split("T")[1]  # handle ISO datetime like "2026-06-19T14:00"
        return int(s.split(":")[0])
    except (ValueError, IndexError, AttributeError):
        return 0


def _strip_accents(text: str) -> str:
    """Decompose text and drop combining diacritical marks → ASCII-safe lowercase."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _detect_time_period(texts: list[str]) -> str | None:
    """Return 'tarde' or 'manana' if the message requests a broad time period.

    Normalizes accents first so 'À la tarde', 'a la tarde', 'tarde?' all match.
    Also handles standalone 'tarde' (one-word reply).
    """
    combined = _strip_accents(" ".join(texts))
    # "a la tarde" / "por la tarde" / "a la tarde?" / "à la tarde" (after normalization)
    if re.search(r"\b(?:por|a)\s+la\s+tarde\b", combined):
        return "tarde"
    # Standalone "tarde" or "tarde?" as a very short reply
    if re.match(r"^\s*[¿¡]*tarde[?!.]*\s*$", combined.strip()):
        return "tarde"
    # "por la mañana" / "a la mañana" / "temprano"
    if re.search(r"\b(?:por|a)\s+la\s+manana\b|\btemprano\b", combined):
        return "manana"
    return None


def _should_escalate_scheduling_to_human(
    texts: list[str],
    state: "WhatsAppThreadState",
) -> bool:
    """Return True when the user is insisting on an unavailable slot or
    explicitly rejecting the offered alternatives."""
    combined = " ".join(texts).lower()
    if any(kw in combined for kw in _ESCALATION_KEYWORDS):
        return True
    # Re-requesting the exact same time that was already rejected, after
    # *real* alternatives were offered for that same date.
    # Guards:
    #   1. Offered slots must be non-empty — a closed/Sunday rejection stores "[]"
    #      and must NOT count as insistence (no alternatives were ever shown).
    #   2. The user must not have switched to a different date — changing date is a
    #      fresh attempt, not re-insisting on the previous rejected slot.
    if state.last_requested_time and state.last_offered_slots:
        try:
            offered = json.loads(str(state.last_offered_slots))
        except (json.JSONDecodeError, TypeError):
            offered = []
        if offered:  # real alternatives were actually offered
            parsed_day, parsed_time = _parse_scheduling_text(texts, date.today())
            if parsed_time and parsed_time == str(state.last_requested_time):
                # Only escalate when the user hasn't switched to a new date
                if parsed_day is None or parsed_day == str(state.active_requested_date):
                    return True
    return False


def _is_flow_failure(texts: list[str]) -> bool:
    """Return True when the user reports they cannot open the WhatsApp Flow form."""
    combined = " ".join(texts).lower()
    return any(kw in combined for kw in _FLOW_FAILURE_KEYWORDS)


def _is_pure_scheduling_rafaga(messages: list[str], today: "date") -> bool:
    """Return True when every message in the burst contributes only to scheduling
    (parses to a day or time, or is a short acceptance).  A message with
    substantive non-scheduling text (> 2 words, no day/time component) signals
    a vehicle correction or other content that the AI must process first."""
    for msg in messages:
        if _is_acceptance([msg]):
            continue
        d, t = _parse_scheduling_text([msg], today)
        if d is None and t is None and len(msg.split()) > 2:
            return False
    return True


def _nearest_slots(slots: list[str], requested_time_str: str, n: int = 3) -> list[str]:
    """Return up to n slots from slots[], ordered by proximity to requested_time_str (HH:MM)."""
    try:
        parts = requested_time_str.split(":")
        req_total = int(parts[0]) * 60 + (int(parts[1]) if len(parts) > 1 else 0)
    except (ValueError, IndexError, AttributeError):
        return slots[:n]

    def distance(slot: str) -> int:
        h = _time_hour(slot)
        try:
            m = int(str(slot).split(":")[1]) if ":" in str(slot) else 0
        except (ValueError, IndexError):
            m = 0
        return abs(h * 60 + m - req_total)

    return sorted(slots, key=distance)[:n]


def _select_slot_from_offered(texts: list[str], last_offered_slots: str | None) -> str | None:
    """Detect ordinal/positional slot selection and return the matching HH:MM string.

    Handles 'el último horario', 'el ultomp horario' (typo), 'el primero',
    'el segundo', 'el tercero'.  Uses _strip_accents so 'último' normalises to
    'ultimo' before matching.  last_offered_slots is a sorted JSON list of HH:MM.
    """
    try:
        offered = json.loads(last_offered_slots or "[]")
    except (json.JSONDecodeError, TypeError):
        return None
    if not offered:
        return None
    combined = _strip_accents(" ".join(texts))
    # "último"/"ultima" → "ultimo"/"ultima" after accent stripping.
    # Also matches common typos: ultomp (ult+omp), ultom (ult+om), ultiom (ult+iom).
    if re.search(r"\bult(?:im[ao]?|om[ap]?|iom)\b", combined):
        return offered[-1]
    if re.search(r"\bprim(?:er[ao]?)?\b", combined):
        return offered[0]
    if re.search(r"\bsegund[oa]?\b", combined) and len(offered) >= 2:
        return offered[1]
    if re.search(r"\btercer[oa]?\b", combined) and len(offered) >= 3:
        return offered[2]
    return None


# Phrases that must never appear in an AI reply during SCHEDULING or QUOTED stages.
# The engine has not yet created a booking; any confirmation language is a hallucination.
_SCHEDULING_FORBIDDEN_PHRASES: tuple[str, ...] = (
    "confirmamos la revisión",
    "turno confirmado",
    "queda confirmado",
    "quedó confirmado",
    "reserva confirmada",
    "te enviaré un recordatorio",
    "te enviaremos un recordatorio",
    "recordatorio el día anterior",
    "queda reservado",
    "te agendo",
    "te agendamos",
    "ya te agendé",
    "quedaste agendad",
)


def _scrub_scheduling_confirmation(reply: str, stage: str) -> str:
    """Replace AI-hallucinated booking confirmations in SCHEDULING/QUOTED stages.

    The flow button has not been sent yet at this point, so any language that
    implies the turn is booked is incorrect.  We substitute a safe redirect
    so the client keeps interacting instead of believing they have a confirmed slot.
    """
    if stage not in (STAGE_SCHEDULING, STAGE_QUOTED):
        return reply
    lower = reply.lower()
    for phrase in _SCHEDULING_FORBIDDEN_PHRASES:
        if phrase in lower:
            logger.warning(
                "M18 scheduling_scrub removed %r from AI reply (stage=%s)", phrase, stage
            )
            return (
                "Perfecto, recibí tu preferencia. "
                "Para confirmar el turno necesitamos que completes el formulario con tus datos. "
                "¿Hay algo más que quieras ajustar?"
            )
    return reply


# ── Website form (wa.me prefilled link) detection ────────────────────────────

_WEBSITE_FORM_HEADER = "hola, quiero solicitar una revision pre-compra"

# Maps form-submitted "tipo" values to internal tipo_vehiculo codes.
_SUBMITTED_TIPO_MAP: dict[str, str] = {
    "auto pequeño o mediano": "AUTO",
    "auto pequeño": "AUTO",
    "auto mediano": "AUTO",
    "auto grande": "AUTO",
    "auto": "AUTO",
    "suv": "SUV/4x4",
    "pickup": "SUV/4x4",
    "4x4": "SUV/4x4",
    "suv/4x4": "SUV/4x4",
    "suv 4x4": "SUV/4x4",
    "moto": "MOTO",
    "clasico": "CLASICO",
    "clásico": "CLASICO",
    "deportivo": "SUV_4X4_DEPORTIVO",
}


def _normalize_submitted_tipo(submitted: str) -> str:
    """Map a website-form-submitted tipo string to internal tipo_vehiculo."""
    normalized = _strip_accents((submitted or "").lower().strip())
    for key, val in _SUBMITTED_TIPO_MAP.items():
        if key in normalized:
            return val
    return "AUTO"


def _parse_website_form(texts: list[str]) -> dict | None:
    """Parse a wa.me prefilled form message.  Returns field dict or None.

    Expected format:
        Hola, quiero solicitar una revisión pre-compra.
        * Nombre: Juan Pérez
        * Teléfono: 1112345678
        * Auto a revisar: Toyota Corolla 2020
        * Tipo: Auto pequeño o mediano
        * Localidad: Palermo
        * Total estimado: $130.000
        ref: abc123
    """
    combined = "\n".join(texts)
    # Fast reject: must contain the characteristic form opener.
    if _WEBSITE_FORM_HEADER not in _strip_accents(combined).lower():
        return None

    fields: dict = {}
    for raw_line in combined.splitlines():
        line = raw_line.strip().lstrip("*-• ").strip()
        if ":" not in line:
            continue
        key_raw, _, val_raw = line.partition(":")
        key = _strip_accents(key_raw.strip().lower())
        val = val_raw.strip()
        if not val:
            continue
        if key == "nombre":
            fields["customer_name"] = val
        elif key in ("telefono", "teléfono", "celular", "cel"):
            fields["phone"] = re.sub(r"[^\d+]", "", val)
        elif key in ("auto a revisar", "auto", "vehiculo", "vehículo"):
            fields["vehicle_text"] = val
        elif key == "tipo":
            fields["submitted_tipo"] = val
        elif key in ("localidad", "zona", "barrio", "ciudad"):
            fields["zone_detail"] = val
        elif key in ("total estimado", "total", "precio estimado"):
            nums = re.sub(r"[^\d]", "", val)
            fields["submitted_total"] = int(nums) if nums else None

    # Extract optional ref= from the raw text (value may contain hyphens/underscores).
    ref_m = re.search(r"\bref:\s*([\w][\w\-]*)", combined, re.IGNORECASE)
    if ref_m:
        fields["ref"] = ref_m.group(1)

    # Require at least vehicle or zone to proceed.
    if not fields.get("vehicle_text") and not fields.get("zone_detail"):
        return None

    return fields


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

_SPANISH_MONTHS: dict[str, int] = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
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
    elif (
        "mañana" in combined
        or "manana" in combined
        or re.search(r"\bmñ\b", combined)  # mobile abbreviation for "mañana"
    ) and not day_name_found:
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

    # Explicit numeric date: "21 del 6", "21 de 6" (Argentine DD del/de MM)
    if day_iso is None:
        m = re.search(r"\b(\d{1,2})\s+del?\s+(\d{1,2})\b", combined)
        if m:
            d, mo = int(m.group(1)), int(m.group(2))
            if 1 <= d <= 31 and 1 <= mo <= 12:
                try:
                    candidate = date(today.year, mo, d)
                    if candidate < today:
                        candidate = date(today.year + 1, mo, d)
                    day_iso = candidate.isoformat()
                except ValueError:
                    pass

    # Spanish month name: "21 de junio", "3 de marzo"
    if day_iso is None:
        for mname, mnum in _SPANISH_MONTHS.items():
            m = re.search(rf"\b(\d{{1,2}})\s+de\s+{mname}\b", combined)
            if m:
                d = int(m.group(1))
                if 1 <= d <= 31:
                    try:
                        candidate = date(today.year, mnum, d)
                        if candidate < today:
                            candidate = date(today.year + 1, mnum, d)
                        day_iso = candidate.isoformat()
                    except ValueError:
                        pass
                break

    # Slash/dash date: "21/6" or "21-6" (Argentine: day first)
    if day_iso is None:
        m = re.search(r"\b(\d{1,2})[/-](\d{1,2})\b", combined)
        if m:
            d, mo = int(m.group(1)), int(m.group(2))
            if 1 <= d <= 31 and 1 <= mo <= 12:
                try:
                    candidate = date(today.year, mo, d)
                    if candidate < today:
                        candidate = date(today.year + 1, mo, d)
                    day_iso = candidate.isoformat()
                except ValueError:
                    pass

    # ── Time extraction ───────────────────────────────────────────────────
    time_str: str | None = None

    # "12hs", "12h", "12 ha", "9:30hs", "9:30h"  (most common Argentine patterns)
    # "ha" is a common WhatsApp typo for "hs" (e.g. "20 ha" → 20:00)
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*h(?:s|oras?|a)?\b", combined)
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
        # Standalone "9:30", "12:00", or abbreviated "11:3" (single digit = tens: 3→30).
        m = re.search(r"\b(\d{1,2}):(\d{1,2})\b", combined)
        if m:
            h, mi_raw = int(m.group(1)), int(m.group(2))
            mi = mi_raw * 10 if len(m.group(2)) == 1 else mi_raw
            if 0 <= h <= 23 and 0 <= mi <= 59:
                time_str = f"{h:02d}:{mi:02d}"

    return day_iso, time_str


def _out(action: str, **kwargs) -> ConversationHandleOut:
    return ConversationHandleOut(
        ok=action not in ("error", "blocked_dispatch"),
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
        except OutboundBlockedError as exc:
            outcome = getattr(exc, "gate_outcome", "BLOCKED_KILL_SWITCH")
            detail = f"OUTBOUND_GATE_{outcome.upper()}"
            logger.warning(
                "M19 OUTBOUND_BLOCKED thread_id=%s outcome=%s wa=%s",
                event.thread_id, outcome, event.wa_message_id,
            )
            if outcome.upper() == "BLOCKED_KILL_SWITCH":
                # Kill switch: CE computed the full logical result before reaching
                # the send. Commit CE session to persist conversation state.
                # Blocked audit is already committed in the gate's dedicated session.
                try:
                    self.db.commit()
                except Exception:
                    try:
                        self.db.rollback()
                    except Exception:
                        pass
                return _out("blocked_dispatch", detail=detail)
            # Duplicate / flood: CE may have partial state — do not commit.
            try:
                self.db.rollback()
            except Exception:
                pass
            return _out("error", detail=detail)
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

        # Route to qualification fallback handlers BEFORE booking flow processing.
        # Detection is by distinctive payload keys, not by token.
        if "tipo_vehiculo" in flow_data:
            return self._process_vehicle_fallback_response(ctx, state, flow_data)
        if "zona_general" in flow_data:
            return self._process_location_fallback_response(ctx, state, flow_data)

        # Token validation (warn only — don't hard-block in case of token mismatch due to clock skew)
        expected = state.flow_booking_token
        if expected and flow_token and flow_token != expected:
            logger.warning(
                "M18 flow token mismatch thread_id=%s expected=%s got=%s",
                ctx.thread.id, expected, flow_token,
            )

        # Determine lead source so we can fill missing fields from context.
        is_website = getattr(state, "is_website_lead", False)

        # Parse flow fields — website Flow only sends email/direccion/tipo_vendedor/nombre_vendedor.
        buyer_email = (flow_data.get("email") or "").strip() or None
        direccion = (
            flow_data.get("direccion")
            or flow_data.get("direccion_texto")
            or ""
        ).strip() or None
        seller_type = (flow_data.get("tipo_vendedor") or "").strip() or None
        seller_name = (flow_data.get("nombre_vendedor") or "").strip() or None
        publication_url = (flow_data.get("link_publicacion") or "").strip() or None

        if is_website:
            # Website Flow: nombre_apellido / telefono / como_llego are NOT collected
            # by the form — fill them from the context set during form processing.
            ctx_name = (state.customer_name or "").strip()
            if not ctx_name:
                parts = [lead.nombre or "", lead.apellido or ""]
                ctx_name = " ".join(p for p in parts if p).strip()
            full_name = ctx_name
            buyer_phone = (lead.telefono or ctx.contact.wa_id or "").strip() or None
            canal = "Formulario web"
        else:
            # Generic Flow: all fields are collected by the form.
            full_name = (flow_data.get("nombre_apellido") or "").strip()
            buyer_phone = (flow_data.get("telefono") or lead.telefono or "").strip() or None
            canal = (flow_data.get("como_llego") or "").strip() or None

        name_parts = full_name.split() if full_name else []
        buyer_first = name_parts[0] if name_parts else ""
        buyer_last = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

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

        name_display = buyer_first or state.customer_name or ""
        date_display = _format_date_human(sched_date.isoformat(), date.today()) if sched_date else None
        time_display = sched_time.strftime("%H:%M") if sched_time else None
        if name_display and date_display and time_display:
            opener = f"¡Listo, {name_display}! Recibimos tu solicitud para el {date_display} a las {time_display} 🎉"
        elif name_display:
            opener = f"¡Listo, {name_display}! Recibimos tu solicitud 🎉"
        else:
            opener = "¡Listo! Recibimos tu solicitud 🎉"
        confirm_text = (
            f"{opener}\n\n"
            "Un asesor va a revisar los datos y te confirma el turno a la brevedad."
        )
        sent_id = self._send_text_to_wa(ctx, confirm_text)
        return _out("booking_created", wa_message_id=sent_id)

    # ── Qualification fallback Flows ─────────────────────────────────────

    @staticmethod
    def _build_quote_reply(
        marca: "str | None",
        modelo: "str | None",
        location: "str | None",
        precio_total: int,
        anio: "int | None" = None,
    ) -> str:
        """Return the approved customer-facing quote copy.

        Genial! La cotización para la revisión del {vehicle} en {location}
        es de ${price}. Si te parece bien, podemos avanzar.
        """
        total = f"${precio_total:,.0f}".replace(",", ".")
        vehicle = " ".join(
            p for p in [
                (marca or "").strip(),
                (modelo or "").strip(),
                str(anio) if anio else None,
            ] if p
        ).strip() or None
        if vehicle and location:
            return (
                f"Genial! La cotización para la revisión del {vehicle} en {location} "
                f"es de {total}. Si te parece bien, podemos avanzar."
            )
        if vehicle:
            return (
                f"Genial! La cotización para la revisión del {vehicle} "
                f"es de {total}. Si te parece bien, podemos avanzar."
            )
        if location:
            return (
                f"Genial! La cotización para la revisión en {location} "
                f"es de {total}. Si te parece bien, podemos avanzar."
            )
        return (
            f"Genial! La cotización para la revisión es de {total}. "
            "Si te parece bien, podemos avanzar."
        )

    def _process_vehicle_fallback_response(
        self,
        ctx: _Context,
        state: "WhatsAppThreadState",
        flow_data: dict,
    ) -> ConversationHandleOut:
        """Handle a Vehicle Fallback Flow submit.

        Updates the candidate deterministically and resumes qualification.
        Never creates a revision, never sends the booking Flow, never sets AGENDADO.
        """
        lead = ctx.lead
        assert lead is not None

        tipo = (flow_data.get("tipo_vehiculo") or "").strip().upper()
        if tipo == "MOTO" or self._is_motorcycle_enquiry(tipo):
            return self._motorcycle_human_handoff(ctx, state)
        marca = (flow_data.get("marca") or "").strip()
        modelo = (flow_data.get("modelo") or "").strip()
        anio_raw = flow_data.get("anio")
        anio: int | None = None
        try:
            anio = int(anio_raw) if anio_raw else None
        except (ValueError, TypeError):
            pass

        state.vehicle_fallback_flow_sent = False  # consumed

        if tipo not in _FALLBACK_VEHICLE_TYPES:
            logger.info(
                "M18 vehicle_fallback unresolvable tipo=%r thread_id=%s — needs_human",
                tipo, ctx.thread.id,
            )
            state.needs_human = True
            lead.necesita_humano = True
            self.db.commit()
            sent_id = self._send_text_to_wa(ctx, _FALLBACK_WARM_HANDOFF)
            self._send_fallback_human_review_notification(
                ctx, state, reason="otro_vehicle_type"
            )
            return _out("replied", wa_message_id=sent_id)

        # Update or create candidate
        focus = self._focus_candidate(ctx)
        if focus:
            if marca:
                focus.marca = marca
            if modelo:
                focus.modelo = modelo
            if tipo:
                focus.tipo_vehiculo = tipo
            if anio:
                focus.anio = anio
        else:
            from app.models import WhatsAppThreadCandidate
            cand = WhatsAppThreadCandidate(
                thread_id=ctx.thread.id,
                marca=marca or None,
                modelo=modelo or None,
                tipo_vehiculo=tipo,
                anio=anio,
                status="current_focus",
                source_text=f"vehicle_fallback_flow: {marca} {modelo}".strip(),
            )
            self.db.add(cand)
            self.db.flush()
            ctx.candidates.append(cand)
            state.current_focus_candidate_id = cand.id

        logger.info(
            "M18 vehicle_fallback resolved tipo=%s thread_id=%s",
            tipo, ctx.thread.id,
        )

        real_price_quote = self._compute_price_quote(ctx, state)
        if real_price_quote:
            reply = self._build_quote_reply(
                marca or None,
                modelo or None,
                state.home_zone_detail or None,
                real_price_quote.precio_total,
            )
            lead.flag = "PRESUPUESTO_ENVIADO"
            state.last_stage = STAGE_QUOTED
            self.db.commit()
            sent_id = self._send_text_to_wa(ctx, reply)
            return _out("replied", wa_message_id=sent_id)

        # Zone not yet known — dispatch Location Fallback Flow directly.
        self.db.commit()
        result = self._dispatch_location_flow_direct(ctx, state, concern=False)
        if result is not None:
            return result
        # Flow not configured: fall back to text prompt.
        reply = (
            "¡Gracias! Ya registré el vehículo. "
            "Para completar la cotización, ¿en qué barrio o zona de Buenos Aires está el auto?"
        )
        sent_id = self._send_text_to_wa(ctx, reply)
        return _out("replied", wa_message_id=sent_id)

    def _process_location_fallback_response(
        self,
        ctx: _Context,
        state: "WhatsAppThreadState",
        flow_data: dict,
    ) -> ConversationHandleOut:
        """Handle a Location Fallback Flow submit.

        Updates zone state deterministically and resumes qualification.
        Never creates a revision, never sends the booking Flow, never sets AGENDADO.
        """
        lead = ctx.lead
        assert lead is not None

        zona_general = (flow_data.get("zona_general") or "").strip().upper()
        localidad = (flow_data.get("localidad") or "").strip()
        referencia = (flow_data.get("referencia_ubicacion") or "").strip() or None

        state.location_fallback_flow_sent = False  # consumed

        canonical_group = _FLOW_ZONE_GROUP_MAP.get(zona_general)

        if canonical_group is None:
            logger.info(
                "M18 location_fallback unresolvable zona_general=%r thread_id=%s — needs_human",
                zona_general, ctx.thread.id,
            )
            state.needs_human = True
            lead.necesita_humano = True
            self.db.commit()
            sent_id = self._send_text_to_wa(ctx, _FALLBACK_WARM_HANDOFF)
            self._send_fallback_human_review_notification(
                ctx, state, reason="otro_zona_general"
            )
            return _out("replied", wa_message_id=sent_id)

        # Try to resolve localidad to a DB-validated zone_detail.
        db_zone = self._extract_zone_from_text(localidad) if localidad else None
        state.home_zone_group = db_zone.zone_group if db_zone else canonical_group
        state.home_zone_detail = db_zone.zone_detail if db_zone else (localidad or None)
        if referencia and not state.home_zone_detail:
            state.home_zone_detail = referencia

        logger.info(
            "M18 location_fallback resolved zone_group=%s zone_detail=%s thread_id=%s",
            state.home_zone_group, state.home_zone_detail, ctx.thread.id,
        )

        real_price_quote = self._compute_price_quote(ctx, state)
        if real_price_quote:
            focus = self._focus_candidate(ctx)
            reply = self._build_quote_reply(
                (focus.marca or "").strip() or None if focus else None,
                (focus.modelo or "").strip() or None if focus else None,
                state.home_zone_detail or None,
                real_price_quote.precio_total,
            )
            lead.flag = "PRESUPUESTO_ENVIADO"
            state.last_stage = STAGE_QUOTED
        else:
            # Zone known but vehicle still missing — or zone not in pricing DB.
            focus = self._focus_candidate(ctx)
            if not (focus and focus.tipo_vehiculo):
                reply = (
                    "¡Gracias! Ya tenemos la zona. "
                    "Para completar la cotización, ¿cuál es la marca y modelo del auto?"
                )
            else:
                # Vehicle known, zone known via zona_general, but exact localidad
                # viaticos not in DB.  Send an explicit, non-generic customer message
                # and fire a Resend internal alert so an agent can follow up.
                state.needs_human = True
                lead.necesita_humano = True
                customer_msg = (
                    "Gracias, ya recibimos los datos. Estamos verificando la zona "
                    "exacta para calcular el traslado y te confirmamos a la brevedad."
                )
                self.db.commit()
                sent_id = self._send_text_to_wa(ctx, customer_msg)
                self._send_fallback_human_review_notification(
                    ctx, state, reason="unresolved_localidad"
                )
                return _out("replied", wa_message_id=sent_id)

        self.db.commit()
        sent_id = self._send_text_to_wa(ctx, reply)
        return _out("replied", wa_message_id=sent_id)

    # ── M20.6D.2B Routing gate ────────────────────────────────────────────

    def _routing_gate(
        self,
        ctx: "_Context",
        state: "WhatsAppThreadState",
        pre_detected_vehicle: "VehicleMatch | None",
        ai_input_messages: list[str],
    ) -> "tuple[ConversationHandleOut | None, bool]":
        """Deterministic routing gate that runs before _check_fallback_flow_triggers.

        Returns (result, skip_fallback):
          result is not None   → return it immediately.
          result is None, skip_fallback=True  → go to AI, skip old fallback trigger.
          result is None, skip_fallback=False → continue to _check_fallback_flow_triggers.

        Priority order (spec M20.6D.2B.1):
          1. Clearly outside coverage → deterministic coverage response.
          2. Vehicle known + concern + zone unknown → concern-aware Location Flow.
          3. FAQ / soft-close / service-info (not concern, not price) → AI.
          4. Vehicle known + zone unknown (general) → standard Location Flow.
             Covers price questions, scheduling, bare vehicle/location messages.
          5. Vehicle unknown + price intent → Vehicle Flow.
             Vehicle unknown + no price intent → AI.
          6. Fall through to existing qualification behavior.
        """
        if state.last_stage not in (STAGE_QUALIFYING, None):
            return None, False
        if state.needs_human:
            return None, False

        combined = " ".join(ai_input_messages)

        # Priority 1 — outside coverage
        if _is_outside_coverage(combined):
            return self._send_coverage_response(ctx, state), False

        _snap = resolve_field_evidence(ctx, state)
        # pre_detected_vehicle covers the case where no candidate was created yet (e.g.
        # direct-call tests). In normal CE flow a candidate already exists at this point
        # and snap.vehicle_known() picks it up from the focused candidate.
        vehicle_known = _snap.vehicle_known() or bool(
            pre_detected_vehicle is not None
            and getattr(pre_detected_vehicle, "tipo_vehiculo", None)
        )
        zone_known = _snap.location_known()

        # Priority 2 — vehicle known + concern + zone missing → concern-aware Location Flow.
        # Concern takes precedence over FAQ/soft-close; cannot be suppressed by the FAQ check.
        if vehicle_known and not zone_known and not state.location_fallback_flow_sent:
            if _has_vehicle_concern(combined):
                result = self._dispatch_location_flow_direct(ctx, state, concern=True)
                if result is not None:
                    return result, False

        # Priority 3 — FAQ / soft-close / service-info → AI (regardless of vehicle/zone state).
        # Only fires when neither concern nor price intent is present.
        # This carves out service questions and polite exits from the Location/Vehicle Flow paths.
        if (
            _is_general_faq_or_soft_close(combined)
            and not _has_vehicle_concern(combined)
            and not _wants_price_quote(combined)
        ):
            return None, True

        # Priority 4 — vehicle known + zone missing → standard Location Flow (general case).
        # Covers: price questions, scheduling queries, bare vehicle mentions, location mentions,
        # and any other message that is not a FAQ, soft-close, concern, or outside-coverage.
        # Preserves D.2B direct dispatch for the full "vehicle known + zone unknown" envelope.
        if vehicle_known and not zone_known and not state.location_fallback_flow_sent:
            result = self._dispatch_location_flow_direct(ctx, state, concern=False)
            if result is not None:
                return result, False

        # Priority 5 — vehicle not resolved
        if not vehicle_known:
            if _wants_price_quote(combined) and not state.vehicle_fallback_flow_sent:
                generic = _is_generic_vehicle_text(combined)
                intro = _VEH_FLOW_BODY_GENERIC if generic else _VEH_FLOW_BODY_GARBLED
                result = self._dispatch_vehicle_flow_direct(ctx, state, intro)
                if result is not None:
                    return result, False
            # Unknown vehicle + no price intent → AI (skip old fallback trigger)
            return None, True

        return None, False

    def _dispatch_vehicle_flow_direct(
        self,
        ctx: "_Context",
        state: "WhatsAppThreadState",
        intro: str,
    ) -> "ConversationHandleOut | None":
        """Dispatch the Vehicle Fallback Flow immediately, skipping the text step.

        Returns None when no flow_id is configured (caller should fall through).
        """
        flow_id = (
            getattr(self.settings, "whatsapp_vehicle_fallback_flow_id", "") or ""
        ).strip()
        if not flow_id:
            return None
        logger.info(
            "M20 vehicle_flow_direct thread_id=%s flow_id=%s", ctx.thread.id, flow_id
        )
        if os.environ.get("OUTBOUND_ENABLED") != "true":
            OutboundSafetyGate(self.db).attempt(
                wa_id=ctx.contact.wa_id, thread_id=ctx.thread.id, text=intro,
            )
            self.db.commit()
            return _out("blocked_dispatch", detail="OUTBOUND_GATE_BLOCKED_KILL_SWITCH")
        state.vehicle_fallback_flow_sent = True
        flow_token = secrets.token_urlsafe(24)
        sent_id = self._send_flow_button(
            ctx, intro, flow_token,
            flow_id=flow_id, initial_screen="VEHICLE_DETAILS",
        )
        return _out("replied", wa_message_id=sent_id)

    def _dispatch_location_flow_direct(
        self,
        ctx: "_Context",
        state: "WhatsAppThreadState",
        *,
        concern: bool,
    ) -> "ConversationHandleOut | None":
        """Dispatch the Location Fallback Flow immediately, skipping the text step.

        Returns None when no flow_id is configured (caller should fall through).
        """
        flow_id = (
            getattr(self.settings, "whatsapp_location_fallback_flow_id", "") or ""
        ).strip()
        if not flow_id:
            return None
        body = _LOC_FLOW_BODY_CONCERN if concern else _LOC_FLOW_BODY_STANDARD
        logger.info(
            "M20 location_flow_direct thread_id=%s flow_id=%s concern=%s",
            ctx.thread.id, flow_id, concern,
        )
        if os.environ.get("OUTBOUND_ENABLED") != "true":
            OutboundSafetyGate(self.db).attempt(
                wa_id=ctx.contact.wa_id, thread_id=ctx.thread.id, text=body,
            )
            self.db.commit()
            return _out("blocked_dispatch", detail="OUTBOUND_GATE_BLOCKED_KILL_SWITCH")
        state.location_fallback_flow_sent = True
        flow_token = secrets.token_urlsafe(24)
        sent_id = self._send_flow_button(
            ctx, body, flow_token,
            flow_id=flow_id, initial_screen="LOCATION_DETAILS",
        )
        return _out("replied", wa_message_id=sent_id)

    def _send_coverage_response(
        self,
        ctx: "_Context",
        state: "WhatsAppThreadState",
    ) -> "ConversationHandleOut":
        """Send the deterministic out-of-coverage response and set needs_human."""
        state.needs_human = True
        if ctx.lead:
            ctx.lead.necesita_humano = True
        logger.info("M20 coverage_response thread_id=%s", ctx.thread.id)
        if os.environ.get("OUTBOUND_ENABLED") != "true":
            OutboundSafetyGate(self.db).attempt(
                wa_id=ctx.contact.wa_id,
                thread_id=ctx.thread.id,
                text=_COVERAGE_RESPONSE,
            )
            self.db.commit()
            return _out("blocked_dispatch", detail="OUTBOUND_GATE_BLOCKED_KILL_SWITCH")
        sent_id = self._send_text_to_wa(ctx, _COVERAGE_RESPONSE)
        return _out("replied", wa_message_id=sent_id)

    def _check_fallback_flow_triggers(
        self,
        ctx: _Context,
        state: "WhatsAppThreadState",
        pre_detected_vehicle: "VehicleMatch | None",
        ai_input_messages: list[str],
    ) -> "ConversationHandleOut | None":
        """Check if a qualification fallback Flow should be triggered.

        Vehicle is always resolved before location (Rule 8). When vehicle is
        unresolved in ANY state, location check is skipped entirely so the two
        paths never interleave. Returns None to continue normal AI processing.
        """
        _snap = resolve_field_evidence(ctx, state)
        # Require tipo_vehiculo to be explicitly set — a brand-only catalog hit that
        # resolves marca but leaves tipo_vehiculo empty must NOT block the flow trigger.
        # pre_detected_vehicle covers the case where no candidate was created yet (e.g.
        # existing candidates list was non-empty so CE skipped _create_candidate_from_catalog).
        vehicle_known = _snap.vehicle_known() or bool(
            pre_detected_vehicle is not None
            and getattr(pre_detected_vehicle, "tipo_vehiculo", None)
        )
        zone_known = _snap.location_known()

        # ── Vehicle fallback (takes priority over location) ───────────────
        if not vehicle_known:
            if state.vehicle_fallback_flow_sent:
                # Flow already dispatched — do NOT call AI, do NOT re-dispatch.
                # Send a brief reminder so the customer knows to complete the form.
                reply = (
                    "Por favor completá el formulario que te enviamos sobre tu vehículo "
                    "para que podamos cotizarte la revisión."
                )
                self.db.commit()
                sent_id = self._send_text_to_wa(ctx, reply)
                return _out("replied", wa_message_id=sent_id)
            flow_id = (
                getattr(self.settings, "whatsapp_vehicle_fallback_flow_id", "") or ""
            ).strip()
            if state.vehicle_clarification_sent and flow_id:
                # One chat clarification already failed — send Flow.
                logger.info(
                    "M18 vehicle_fallback flow dispatched thread_id=%s flow_id=%s",
                    ctx.thread.id, flow_id,
                )
                body = (
                    "Completá los datos del vehículo para que podamos cotizarte "
                    "la revisión mecánica."
                )
                if os.environ.get("OUTBOUND_ENABLED") != "true":
                    OutboundSafetyGate(self.db).attempt(
                        wa_id=ctx.contact.wa_id, thread_id=ctx.thread.id, text=body,
                    )
                    self.db.commit()
                    return _out("blocked_dispatch", detail="OUTBOUND_GATE_BLOCKED_KILL_SWITCH")
                state.vehicle_fallback_flow_sent = True
                flow_token = secrets.token_urlsafe(24)
                sent_id = self._send_flow_button(
                    ctx, body, flow_token, flow_id=flow_id, initial_screen="VEHICLE_DETAILS"
                )
                return _out("replied", wa_message_id=sent_id)
            if not state.vehicle_clarification_sent:
                # First attempt — ask via chat.
                reply = (
                    "Para cotizarte la revisión necesito saber qué vehículo tenés. "
                    "¿Me podés indicar la marca y el modelo?"
                )
                if os.environ.get("OUTBOUND_ENABLED") != "true":
                    OutboundSafetyGate(self.db).attempt(
                        wa_id=ctx.contact.wa_id, thread_id=ctx.thread.id, text=reply,
                    )
                    self.db.commit()
                    return _out("blocked_dispatch", detail="OUTBOUND_GATE_BLOCKED_KILL_SWITCH")
                state.vehicle_clarification_sent = True
                self.db.commit()
                sent_id = self._send_text_to_wa(ctx, reply)
                return _out("replied", wa_message_id=sent_id)
            # Clarification sent but no flow ID configured — fall through to AI.
            return None

        # ── Location fallback (only when vehicle IS known) ────────────────
        if not zone_known:
            if state.location_fallback_flow_sent:
                # Flow already dispatched — do NOT call AI, do NOT re-dispatch.
                reply = (
                    "Por favor completá el formulario que te enviamos sobre la ubicación "
                    "del auto para que podamos cotizarte la revisión."
                )
                self.db.commit()
                sent_id = self._send_text_to_wa(ctx, reply)
                return _out("replied", wa_message_id=sent_id)
            flow_id = (
                getattr(self.settings, "whatsapp_location_fallback_flow_id", "") or ""
            ).strip()
            if state.location_clarification_sent and flow_id:
                logger.info(
                    "M18 location_fallback flow dispatched thread_id=%s flow_id=%s",
                    ctx.thread.id, flow_id,
                )
                body = (
                    "Indicanos dónde está el auto para calcular los viáticos "
                    "de la revisión."
                )
                if os.environ.get("OUTBOUND_ENABLED") != "true":
                    OutboundSafetyGate(self.db).attempt(
                        wa_id=ctx.contact.wa_id, thread_id=ctx.thread.id, text=body,
                    )
                    self.db.commit()
                    return _out("blocked_dispatch", detail="OUTBOUND_GATE_BLOCKED_KILL_SWITCH")
                state.location_fallback_flow_sent = True
                flow_token = secrets.token_urlsafe(24)
                sent_id = self._send_flow_button(
                    ctx, body, flow_token, flow_id=flow_id, initial_screen="LOCATION_DETAILS"
                )
                return _out("replied", wa_message_id=sent_id)
            if not state.location_clarification_sent:
                reply = (
                    "¿En qué localidad o barrio está el auto? "
                    "Por ejemplo: Palermo, Tigre, Dock Sud o Lomas de Zamora."
                )
                if os.environ.get("OUTBOUND_ENABLED") != "true":
                    OutboundSafetyGate(self.db).attempt(
                        wa_id=ctx.contact.wa_id, thread_id=ctx.thread.id, text=reply,
                    )
                    self.db.commit()
                    return _out("blocked_dispatch", detail="OUTBOUND_GATE_BLOCKED_KILL_SWITCH")
                state.location_clarification_sent = True
                self.db.commit()
                sent_id = self._send_text_to_wa(ctx, reply)
                return _out("replied", wa_message_id=sent_id)
            # Clarification sent but no flow ID configured — fall through to AI.
            return None

        return None

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

        # ── M21.1.1: Current-turn evidence construction ───────────────────
        # Audio transcripts arrive as event.text (DB text=NULL); must be
        # included even when unanswered_recent_user_messages is non-empty.
        _current_evidence: list[str] = list(event.unanswered_recent_user_messages or [])
        if event.text and event.text not in _current_evidence:
            _current_evidence.append(event.text)

        if not _current_evidence:
            logger.info("M18 no text content thread_id=%s — ignored", ctx.thread.id)
            return _out("skipped_dedup", detail="no_text")

        current_turn_text = " ".join(_current_evidence)

        # Preserve existing ráfaga behavior for all downstream handlers.
        if event.unanswered_recent_user_messages:
            ai_input_messages = event.unanswered_recent_user_messages
        else:
            ai_input_messages = _current_evidence

        # ── M21.1.1 Layer A: Motorcycle pre-gate (all stages) ─────────────
        if self._is_motorcycle_enquiry(current_turn_text):
            return self._motorcycle_human_handoff(ctx, state)

        # ── M21.1.1 Layer B: Phone-call/human-request pre-gate (all stages)
        # Uses existing module-level _is_phone_call_request (line 307).
        # Motorcycle (Layer A) has higher priority and is checked first.
        if _is_phone_call_request(_current_evidence):
            return self._handle_phone_call_escalation(ctx, state)

        # ── M21.1.1 Layer C: Explicit unsupported-service pre-gate (all stages)
        # F12/transfer/repair boundaries fire before QUOTED/SCHEDULING handlers.
        _svc_out = self._handle_explicit_service_gate(ctx, current_turn_text)
        if _svc_out is not None:
            return _svc_out

        # ── M21.1.1 Layer D: FAQ bypass (all stages) ──────────────────────
        # Informational unless the same message also contains a pre-purchase signal,
        # or unless a specific vehicle is recognized (those need the full AI path to
        # create the candidate and deliver a commercial reply).
        _text_norm_d = self._norm_text(current_turn_text)
        if (
            self._detect_general_information(_text_norm_d)
            and not self._detect_prepurchase_signal(_text_norm_d)
            and lookup_vehicle(current_turn_text) is None
        ):
            return self._handle_general_information_ai(ctx, event, ai_input_messages)

        # ── M21.1.2 Layer G: Vehicle inspectability gate (all stages) ─────
        # After FAQ bypass (Layer D); before website-form, QUOTED acceptance,
        # QUOTED date proposal, SCHEDULING, BR-1, and all downstream mutation.
        # Motorcycle (Layer A) takes absolute precedence over this gate.
        # A pure hypothetical FAQ ('¿Qué pasa si estuviera desarmado?') is
        # either intercepted by Layer D above, or passes here via the
        # historical-context detector (is_historical=True → returns None).
        _insp_out = self._handle_vehicle_inspectability_gate(ctx, state, current_turn_text)
        if _insp_out is not None:
            return _insp_out

        # ── Website form from wa.me prefilled link (pre-AI) ─────────────
        # Detect the structured form message before any other state-based checks.
        # The form contains vehicle, zone, and submitted price from the website.
        if state.last_stage in (STAGE_QUALIFYING, None) and not state.needs_human:
            _form_data = _parse_website_form(ai_input_messages)
            if _form_data:
                return self._handle_website_form(ctx, state, _form_data)

        # ── Deterministic QUOTED acceptance (pre-AI) ─────────────────────
        # When the client is in QUOTED stage and sends a clear acceptance word,
        # skip the AI entirely: set flag=ACEPTADO, stage=SCHEDULING, and ask
        # for day/time only.  No revision, no Flow, no re-quoting.
        if state.last_stage == STAGE_QUOTED and _is_acceptance(ai_input_messages):
            return self._handle_quoted_acceptance(ctx, state)

        # ── Deterministic QUOTED + date proposal (pre-AI) ────────────────
        # User proposes a day (with or without exact time) while still in QUOTED
        # stage.  Treat as implicit quote acceptance + scheduling request; skip AI,
        # advance flag to ACEPTADO and immediately check availability.
        # Sunday / closed days → rejection message with alternatives.
        if state.last_stage == STAGE_QUOTED and not state.needs_human:
            sched_day_iso, sched_time_str = _parse_scheduling_text(ai_input_messages, date.today())
            period = _detect_time_period(ai_input_messages)
            if sched_day_iso and sched_time_str:
                lead.flag = "ACEPTADO"
                state.last_stage = STAGE_SCHEDULING
                logger.info(
                    "M18 QUOTED scheduling proposal thread_id=%s day=%s time=%s",
                    ctx.thread.id, sched_day_iso, sched_time_str,
                )
                result = self._try_schedule_and_flow(ctx, state, sched_day_iso, sched_time_str, "")
                if result is not None:
                    return result
            elif sched_day_iso:
                # Day only (e.g. "okay, puede ser sábado?") — list available slots.
                lead.flag = "ACEPTADO"
                state.last_stage = STAGE_SCHEDULING
                logger.info(
                    "M18 QUOTED day-only proposal thread_id=%s day=%s",
                    ctx.thread.id, sched_day_iso,
                )
                return self._handle_day_only_request(ctx, state, sched_day_iso, period=period)

        # ── Flow-failure: user can't open the WhatsApp Flow form ────────────
        if state.flow_booking_token and _is_flow_failure(ai_input_messages):
            return self._handle_flow_failure(ctx, state, " ".join(ai_input_messages))

        # ── Deterministic SCHEDULING day/time parse + escalation (pre-AI) ──
        if (
            state.last_stage == STAGE_SCHEDULING
            and not state.needs_human
            and not state.flow_booking_token
        ):
            # 1. Escalation: insistence on unavailable slot → human handoff
            if _should_escalate_scheduling_to_human(ai_input_messages, state):
                return self._handle_scheduling_escalation(
                    ctx, state, " ".join(ai_input_messages)
                )

            sched_day_iso, sched_time_str = _parse_scheduling_text(ai_input_messages, date.today())
            # Detect period once — used by both the period-only and day+period branches.
            period = _detect_time_period(ai_input_messages)

            # 2. Period request: no explicit day/time in the message.
            #    Use active_requested_date (set by prior rejection) as the target date.
            if not sched_day_iso and not sched_time_str and period:
                if state.active_requested_date and state.last_offered_slots:
                    # Fast path: filter the already-stored full-day slots by period.
                    result = self._handle_period_request(ctx, state, period)
                    if result is not None:
                        return result
                elif state.active_requested_date:
                    # No cached slots — fetch fresh slots and filter by period.
                    return self._handle_day_only_request(
                        ctx, state, str(state.active_requested_date), period=period
                    )

            # 2b. Ordinal slot selection from offered alternatives: "el último horario",
            #     "el ultomp horario" (typo), "el primero", "el segundo", etc.
            #     Uses last_visible_slots — the exact slots displayed to the user —
            #     so "el último" refers to the last SEEN option, not the full-day list.
            #     Bypasses the _pure_sched gate because ordinal phrases (3+ words,
            #     no parseable date/time) would otherwise fall to the AI.
            if not sched_day_iso and not sched_time_str and not period:
                if state.active_requested_date and state.last_visible_slots:
                    _chosen = _select_slot_from_offered(
                        ai_input_messages, state.last_visible_slots
                    )
                    if _chosen:
                        logger.info(
                            "M18 ordinal slot selected thread_id=%s day=%s time=%s",
                            ctx.thread.id, str(state.active_requested_date), _chosen,
                        )
                        result = self._try_schedule_and_flow(
                            ctx, state, str(state.active_requested_date), _chosen, ""
                        )
                        if result is not None:
                            return result

            # 3. Time-only reply on active date: user picks "14:30" from alternatives
            if not sched_day_iso and sched_time_str and state.active_requested_date:
                sched_day_iso = str(state.active_requested_date)

            # 4. "Si" re-confirmation of a stored valid preferred_day
            if not sched_day_iso and state.preferred_day and _is_acceptance(ai_input_messages):
                try:
                    stored = date.fromisoformat(str(state.preferred_day))
                    if stored.weekday() != 6:
                        sched_day_iso = str(state.preferred_day)
                        sched_time_str = str(state.preferred_time) if state.preferred_time else None
                except (ValueError, TypeError):
                    pass

            # 4b. "Si" when the AI proposed a time from the offered list but
            #     preferred_day was cleared by a prior rejection.  Handles the
            #     pattern: AI asked "¿Te sirve ese horario a las X?" → user says "si".
            if (
                not sched_day_iso
                and state.preferred_time
                and state.active_requested_date
                and _is_acceptance(ai_input_messages)
            ):
                try:
                    active_date = date.fromisoformat(str(state.active_requested_date))
                    if active_date.weekday() != 6:
                        sched_day_iso = str(state.active_requested_date)
                        sched_time_str = str(state.preferred_time)
                except (ValueError, TypeError):
                    pass

            # Skip deterministic scheduling when the burst contains non-scheduling
            # content (vehicle correction, zone update, etc.) — let the AI process it.
            _pure_sched = _is_pure_scheduling_rafaga(ai_input_messages, date.today())
            if sched_day_iso and sched_time_str and _pure_sched:
                logger.info(
                    "M18 scheduling deterministic parse thread_id=%s day=%s time=%s",
                    ctx.thread.id, sched_day_iso, sched_time_str,
                )
                result = self._try_schedule_and_flow(ctx, state, sched_day_iso, sched_time_str, "")
                if result is not None:
                    return result
            elif sched_day_iso and _pure_sched:
                # Day only (or day + period like "mañana a la tarde") — list available slots.
                return self._handle_day_only_request(ctx, state, sched_day_iso, period=period)

        # ── M21.1.4: Pending fuzzy confirmation handler ───────────────────
        # Intercepts the turn when a CONFIRM vehicle proposal is outstanding.
        # Must run after all higher-priority gates (motorcycle, inspectability,
        # scheduling) so those gates always win. Must run before vehicle detection
        # so an exact current-turn vehicle can clear the pending proposal.
        if getattr(state, "pending_fuzzy_catalog_key", None) and not state.needs_human:
            _exact_now = lookup_vehicle(current_turn_text)
            if _exact_now:
                # User supplied an exact vehicle — clear pending, fall through
                state.pending_fuzzy_catalog_key = None
            elif _FUZZY_ACCEPTANCE_RE.search(current_turn_text):
                # User confirmed — create candidate from stored key
                _pending_match = self._resolve_fuzzy_key(state.pending_fuzzy_catalog_key)
                state.pending_fuzzy_catalog_key = None
                if _pending_match and not ctx.candidates:
                    self._create_candidate_from_catalog(
                        ctx, state, _pending_match, source_text=current_turn_text
                    )
                # continue normal qualification — fall through
            elif _FUZZY_REJECTION_RE.search(current_turn_text):
                # User rejected — clear proposal, ask for make/model
                state.pending_fuzzy_catalog_key = None
                self.db.commit()
                try:
                    sent_id = self._send_text_to_wa(ctx, _FUZZY_ASK_VEHICLE_REPLY)
                    return _out("replied", wa_message_id=sent_id)
                except OutboundBlockedError:
                    return _out("vehicle_fuzzy_blocked", detail="outbound_disabled")
            else:
                # Ambiguous follow-up — re-ask the same confirmation question
                _parts = state.pending_fuzzy_catalog_key.split("||", 1)
                _pq = _FUZZY_CONFIRMATION_TEMPLATE.format(
                    marca=_parts[0], modelo=_parts[1] if len(_parts) > 1 else "",
                )
                try:
                    sent_id = self._send_text_to_wa(ctx, _pq)
                    return _out("replied", wa_message_id=sent_id)
                except OutboundBlockedError:
                    return _out("vehicle_fuzzy_blocked", detail="outbound_disabled")

        # ── Deterministic vehicle catalog lookup (pre-AI) ─────────────────
        # Search across all recent messages (not just current burst) so a
        # vehicle name from a prior turn is still recognised.
        # Runs BEFORE the Layer F intent gate so that vehicle evidence (exact
        # or fuzzy AUTO_ACCEPT) can set last_intent and let the gate pass.
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
            # Exact match clears any stale fuzzy pending proposal (VN-1, req 6).
            if getattr(state, "pending_fuzzy_catalog_key", None):
                state.pending_fuzzy_catalog_key = None
            # Proactively create a candidate when catalog fires but none exists yet.
            if not ctx.candidates:
                self._create_candidate_from_catalog(
                    ctx, state, pre_detected_vehicle, source_text=all_recent_text
                )
        else:
            # ── M21.1.4: Fuzzy lookup on exact miss (VN-2, VN-3) ─────────
            # Only when: no exact match, no needs_human, no pending proposal,
            # and no existing focused candidate to protect (VN-13).
            _pending = getattr(state, "pending_fuzzy_catalog_key", None)
            if not _pending and not state.needs_human and not ctx.candidates:
                _fuzzy = fuzzy_lookup_vehicle(current_turn_text)
                if _fuzzy.outcome == "AUTO_ACCEPT":
                    logger.info(
                        "M21.1.4 fuzzy AUTO_ACCEPT thread_id=%s hit=%s score=%.3f gap=%.3f",
                        ctx.thread.id,
                        f"{_fuzzy.hit.marca} {_fuzzy.hit.modelo}" if _fuzzy.hit else "?",
                        _fuzzy.score, _fuzzy.gap,
                    )
                    # Set intent so Layer F gate passes (check 7).
                    state.last_intent = _INTENT_PREPURCHASE
                    self._create_candidate_from_catalog(
                        ctx, state, _fuzzy.hit, source_text=current_turn_text
                    )
                    pre_detected_vehicle = _fuzzy.hit
                elif _fuzzy.outcome == "CONFIRM":
                    # Fire CONFIRM only when there is no existing focused candidate to
                    # protect. An established thread has either a candidate in ctx.candidates
                    # or a non-None current_focus_candidate_id set by a prior turn.
                    # Threads that carry last_intent but have no candidate yet (e.g. a
                    # PREPURCHASE thread whose vehicle was garbled by ASR before any
                    # candidate was created) should still receive the confirmation question.
                    _has_candidate_ctx = bool(ctx.candidates) or bool(
                        getattr(state, "current_focus_candidate_id", None)
                    )
                    if not _has_candidate_ctx:
                        logger.info(
                            "M21.1.4 fuzzy CONFIRM thread_id=%s hit=%s score=%.3f gap=%.3f",
                            ctx.thread.id,
                            f"{_fuzzy.hit.marca} {_fuzzy.hit.modelo}" if _fuzzy.hit else "?",
                            _fuzzy.score, _fuzzy.gap,
                        )
                        return self._handle_fuzzy_confirm(ctx, state, _fuzzy)
                    # fall through to AI when established candidate context exists
                # UNRESOLVED: fall through to existing unknown-vehicle path

        # ── M21.1.1 Layer F: QUALIFYING intent gate (QUALIFYING/None only) ──
        # F12/transfer/repair handled by all-stage Layer C above.
        # FAQ bypass handled by all-stage Layer D above.
        # This gate covers: pre-purchase signal, persisted intent, UNCERTAIN.
        if state.last_stage in (STAGE_QUALIFYING, None) and not state.needs_human:
            _intent_out = self._handle_qualifying_intent(ctx, state, current_turn_text)
            if _intent_out is not None:
                return _intent_out

        # ── M21.1.3: Role-aware zone detection ────────────────────────────
        # Replaces the stale-zone guard. Uses current-turn text only (not
        # all_recent_text) to avoid picking up prior-turn zone mentions.
        # Vehicle-location evidence → candidate only (LR-3, LR-6).
        # Customer-origin language → no zone mutation (LR-2).
        # SC17 contradiction → clarification, full early return (LR-8).
        _vehicle_location_written = False
        _vlzones = self._extract_vehicle_location_zones(current_turn_text)
        if len(_vlzones) >= 2 and _LOCATION_UNCERTAINTY_RE.search(current_turn_text):
            # SC17: same-turn contradiction — clarify, no zone mutation
            return self._handle_location_contradiction(ctx, state)
        if _vlzones:
            # Explicit vehicle-location evidence → write to candidate only (LR-3)
            _vzone = _vlzones[0]
            _fc = self._focus_candidate(ctx)
            if _fc:
                _fc.zone_group = _vzone.zone_group
                _fc.zone_detail = _vzone.zone_detail
            _vehicle_location_written = True
        elif not _has_customer_origin_clause(current_turn_text):
            # No vehicle clause, no strong origin signal → bare locality or no info.
            # Preserve existing detection for thread state (location flow compatibility),
            # and write to candidate if it lacks zone (SC14: bare locality in context).
            if not state.home_zone_detail or not state.home_zone_group:
                _zh = self._extract_zone_from_text(current_turn_text)
                if _zh:
                    state.home_zone_detail = _zh.zone_detail
                    if _zh.zone_group:
                        state.home_zone_group = _zh.zone_group
                    _fc2 = self._focus_candidate(ctx)
                    if _fc2 and not _fc2.zone_group:
                        _fc2.zone_group = _zh.zone_group
                        _fc2.zone_detail = _zh.zone_detail

        # Normalise zone_group when detail is known but group is still blank.
        self._normalize_zone_from_db(ctx, state)

        # ── Pre-AI deterministic price quote ──────────────────────────────
        real_price_quote = self._compute_price_quote(ctx, state)

        # ── M20.6D.2B routing gate → then fallback Flow triggers ─────────────
        # Gate runs first in QUALIFYING to direct the turn to:
        #   A. Out-of-coverage response, B. Direct Flow, or C. AI (skipping old fallback).
        # Only if gate returns (None, False) does the old two-step fallback trigger run.
        if state.last_stage in (STAGE_QUALIFYING, None) and not state.needs_human:
            _gate_result, _skip_fallback = self._routing_gate(
                ctx, state, pre_detected_vehicle, ai_input_messages
            )
            if _gate_result is not None:
                return _gate_result
            if not _skip_fallback:
                _fb_result = self._check_fallback_flow_triggers(
                    ctx, state, pre_detected_vehicle, ai_input_messages
                )
                if _fb_result is not None:
                    return _fb_result

        # ── Deterministic phone-call escalation (dedup-safe fallback) ────────
        # Layer B handles all-stage phone-call detection above.
        # This is a safety net in case of routing edge cases after the AI path.
        if _is_phone_call_request(ai_input_messages):
            return self._handle_phone_call_escalation(ctx, state)

        # M21.1.6: determine whether narrative AI adds value for this turn (NU-14)
        _snap_pre = resolve_field_evidence(ctx, state)
        _use_narrative = narrative_needs_ai(_snap_pre, current_turn_text)
        _narr: NarrativeInterpretation | None = None

        messages_for_ai = self._build_ai_messages(
            ctx, event, ai_input_messages,
            pre_detected_vehicle=pre_detected_vehicle,
            real_price_quote=real_price_quote,
            include_narrative=_use_narrative,
        )

        try:
            ai_raw = self._call_openai(messages_for_ai)
            decision = json.loads(ai_raw)
            if _use_narrative:
                _narr = parse_narrative_interpretation(decision)
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

        # M21.1.6 deferred-interest intercept (NU-6, NU-7): must run before any
        # commercial mutation. Only fires when no active vehicle overrides deferred signal.
        if _narr is not None and _narr.is_effectively_deferred():
            return self._handle_deferred_interest(ctx, state), True

        # Apply extracted data to thread state
        extracted = decision.get("extracted") or {}
        _state_zone_before_ai = (state.home_zone_group, state.home_zone_detail)
        self._apply_extracted(ctx, state, extracted)
        _ai_set_zone = (state.home_zone_group, state.home_zone_detail) != _state_zone_before_ai

        # Normalise zone_group again in case AI extracted a zone_detail.
        self._normalize_zone_from_db(ctx, state)

        # Candidate updates
        _focus_before = self._focus_candidate(ctx)
        _focus_before_tipo = _focus_before.tipo_vehiculo if _focus_before else None
        _cand_data = decision.get("candidate") or {}
        if (
            _cand_data.get("tipo_vehiculo", "").upper() == "MOTO"
            and _cand_data.get("action") in ("create", "update")
        ):
            return self._motorcycle_human_handoff(ctx, state)
        self._apply_candidate(ctx, _cand_data)

        # ── Catalog enforcement (post-AI) ─────────────────────────────────
        # If catalog found a vehicle, override the candidate's tipo_vehiculo
        # regardless of what the AI returned.  marca/modelo are only set if
        # not already filled (AI may have added year / version details).
        if pre_detected_vehicle:
            self._enforce_catalog_vehicle(ctx, pre_detected_vehicle)

        # M21.1.6 — apply validated narrative facts (NU-9: deterministic gates remain
        # authoritative; narrative only updates state when evidence is active/confirmed).
        if _narr is not None:
            self._apply_narrative_interpretation(ctx, state, _narr)

        # Sync state zone onto focus candidate when candidate fields are blank.
        focus_after = self._focus_candidate(ctx)

        # ── Vehicle-change re-quote guard ─────────────────────────────────
        # When the user corrects the vehicle in SCHEDULING/QUOTED stage, the
        # previously-sent quote is now stale.  Reset to QUALIFYING so the
        # deterministic quote override below re-prices and sends the new quote.
        if (
            focus_after is not None
            and _focus_before_tipo is not None
            and focus_after.tipo_vehiculo != _focus_before_tipo
            and state.last_stage in (STAGE_SCHEDULING, STAGE_QUOTED, STAGE_FLOW_SENT)
            and not state.needs_human
        ):
            logger.info(
                "M18 vehicle change %r→%r in %s — resetting to QUALIFYING for re-quote thread_id=%s",
                _focus_before_tipo, focus_after.tipo_vehiculo, state.last_stage, ctx.thread.id,
            )
            lead.flag = "PRESUPUESTANDO"
            state.last_stage = STAGE_QUALIFYING
        # Sync thread state → candidate only when zone was set THIS turn (by the
        # pre-AI bare-locality path or by AI extraction). Never copy stale thread
        # zone that was set in a prior turn (LR-6/LR-7).
        if focus_after and not _vehicle_location_written and _ai_set_zone:
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

        # Prevent stage regression: once in SCHEDULING/later, the quote has already
        # been sent — block the AI from moving back to QUOTED (e.g. re-quoting while
        # user is proposing a day/time).
        if (
            flag_accepted
            and new_flag == "PRESUPUESTO_ENVIADO"
            and state.last_stage in (STAGE_SCHEDULING, STAGE_FLOW_SENT, STAGE_BOOKED)
        ):
            logger.warning(
                "M18 blocking PRESUPUESTO_ENVIADO regression from %s thread_id=%s",
                state.last_stage, ctx.thread.id,
            )
            flag_accepted = False
            # Also clear the AI's re-quote reply so it isn't sent verbatim.
            # The post-AI SCHEDULING block will produce the correct scheduling response.
            decision["reply"] = None

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
                if real_price_quote is not None and not state.needs_human:
                    _q_focus = self._focus_candidate(ctx)
                    decision["reply"] = self._build_quote_reply(
                        (_q_focus.marca or "").strip() or None if _q_focus else None,
                        (_q_focus.modelo or "").strip() or None if _q_focus else None,
                        state.home_zone_detail or None,
                        real_price_quote.precio_total,
                        _q_focus.anio if _q_focus else None,
                    )
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
            _q_focus = self._focus_candidate(ctx)
            decision["reply"] = self._build_quote_reply(
                (_q_focus.marca or "").strip() or None if _q_focus else None,
                (_q_focus.modelo or "").strip() or None if _q_focus else None,
                state.home_zone_detail or None,
                real_price_quote.precio_total,
                _q_focus.anio if _q_focus else None,
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
            if pday and ptime:
                result = self._try_schedule_and_flow(ctx, state, pday, ptime, decision.get("reply") or "")
                if result is not None:
                    return result
            elif pday and not ptime:
                # Day only in SCHEDULING — list available slots for that day.
                logger.info(
                    "M18 post-AI day-only in SCHEDULING thread_id=%s day=%s",
                    ctx.thread.id, pday,
                )
                return self._handle_day_only_request(ctx, state, pday)

        # If the AI proposed a time in its reply (e.g., "¿Te parece bien las 11:30?"),
        # store it in state.preferred_time so the next "Sí" triggers block 4b.
        if stage == STAGE_SCHEDULING and not state.needs_human and not state.flow_booking_token:
            ai_reply_text = decision.get("reply") or ""
            if ai_reply_text and state.active_requested_date and not state.preferred_time:
                _, ai_proposed_time = _parse_scheduling_text([ai_reply_text], date.today())
                if ai_proposed_time:
                    state.preferred_time = ai_proposed_time
                    logger.info(
                        "M20 stored AI-proposed time=%s in preferred_time thread_id=%s",
                        ai_proposed_time, ctx.thread.id,
                    )

        reply = str(decision.get("reply") or "")
        # BUG-2 guard: if AI invented a price and we have no deterministic quote, scrub it.
        reply = self._scrub_invented_price(reply, real_price_quote)
        # Scrub AI-hallucinated booking confirmations in SCHEDULING/QUOTED stages.
        reply = _scrub_scheduling_confirmation(reply, stage)
        if not reply:
            self.db.commit()
            return _out("replied", detail="no_reply_text")

        # All paths: _send_text_to_wa commits everything atomically after the send.
        # For PRESUPUESTO_ENVIADO (rule B), this means the flag is only committed
        # after the WhatsApp send succeeds — which is the correct ordering.
        sent_id = self._send_text_to_wa(ctx, reply)
        return _out("replied", wa_message_id=sent_id)

    # ── M21.1.1 Service Intent Gate helpers ───────────────────────────────

    def _norm_text(self, text: str) -> str:
        """NFD-normalize, lowercase, strip accent marks."""
        n = unicodedata.normalize("NFD", text.lower())
        return "".join(c for c in n if unicodedata.category(c) != "Mn")

    def _is_motorcycle_enquiry(self, text: str) -> bool:
        """Word-boundary, accent-insensitive match for motorcycle/quad/UTV terms with plural forms."""
        norm = self._norm_text(text)
        return bool(_MOTORCYCLE_PATTERN.search(norm))

    def _detect_f12_request(self, text_norm: str) -> bool:
        """True when text requests Formulario 12. Mixed past+new context: new request wins."""
        f12_phrase_found = any(
            self._norm_text(phrase) in text_norm
            for phrase in _F12_REQUEST_PHRASES
        )
        if not f12_phrase_found:
            return False

        for past_phrase in _F12_PAST_CONTEXT:
            if self._norm_text(past_phrase) in text_norm:
                for signal in _F12_NEW_REQUEST_SIGNALS:
                    if self._norm_text(signal) in text_norm:
                        return True
                return False

        return True

    def _detect_transfer_request(self, text_norm: str) -> bool:
        """Clause-aware transfer detection. Exclusions suppressed by explicit RideCheck request."""
        transfer_found = any(
            re.search(pattern, text_norm, re.IGNORECASE)
            for pattern in _TRANSFER_REQUEST_PATTERNS
        )
        if not transfer_found:
            return False

        for excl in _TRANSFER_EXCLUSIONS:
            if self._norm_text(excl) in text_norm:
                if any(
                    re.search(pat, text_norm, re.IGNORECASE)
                    for pat in _EXPLICIT_RIDECHECK_TRANSFER_PATTERNS
                ):
                    return True
                return False

        return True

    def _detect_repair_request(self, text_norm: str) -> bool:
        """Clause-aware repair detection. Explicit RideCheck-directed patterns checked first."""
        if any(
            re.search(pat, text_norm, re.IGNORECASE)
            for pat in _EXPLICIT_RIDECHECK_REPAIR_PATTERNS
        ):
            return True

        repair_found = any(
            re.search(pattern, text_norm, re.IGNORECASE)
            for pattern in _REPAIR_REQUEST_PATTERNS
        )
        if not repair_found:
            return False

        for excl in _REPAIR_EXCLUSIONS_INSPECTION:
            if self._norm_text(excl) in text_norm:
                return False

        for excl in _REPAIR_EXCLUSIONS_MECHANIC:
            if self._norm_text(excl) in text_norm:
                return False

        return True

    def _detect_prepurchase_signal(self, text_norm: str) -> bool:
        """True if text contains an explicit pre-purchase intent signal."""
        return any(
            self._norm_text(signal) in text_norm
            for signal in _PREPURCHASE_SIGNALS
        )

    def _detect_general_information(self, text_norm: str) -> bool:
        """True if text is a greeting, FAQ, or conversational question about RideCheck."""
        return any(
            re.search(pattern, text_norm, re.IGNORECASE)
            for pattern in _FAQ_PATTERNS
        )

    # ── M21.1.1-R1: Layer F extended detection helpers ────────────────────

    def _detect_explicit_inspection_request(self, text_norm: str) -> bool:
        """True when text contains an explicit request to book or coordinate a revision."""
        return any(p.search(text_norm) for p in _INSPECTION_REQUEST_PATTERNS)

    def _detect_vehicle_location_phrase(self, text_norm: str) -> bool:
        """True when text contains a vehicle-location phrase ('está en/por X')."""
        return any(p.search(text_norm) for p in _VEHICLE_LOCATION_PATTERNS)

    def _detect_price_question(self, text_norm: str) -> bool:
        """True when text asks for a price or quote ('¿cuánto sale?', 'cotización')."""
        return any(p.search(text_norm) for p in _PRICE_QUESTION_PATTERNS)

    def _has_established_inspection_context(
        self,
        ctx: "_Context",
        state: "WhatsAppThreadState",
    ) -> bool:
        """True when thread has confirmed inspection provenance (BR-1 §6).

        Valid provenance: non-moto candidate from accepted flow, or a
        clarification Flow sent because of an accepted message.
        home_zone_group is supporting state only — not provenance.
        """
        if ctx.candidates and any(
            c.tipo_vehiculo and c.tipo_vehiculo.upper() not in ("MOTO", "MOTOCICLETA")
            for c in ctx.candidates
        ):
            return True
        if (
            state.location_clarification_sent
            or state.vehicle_clarification_sent
            or state.vehicle_fallback_flow_sent
            or state.location_fallback_flow_sent
        ):
            return True
        return False

    def _motorcycle_human_handoff(
        self,
        ctx: "_Context",
        state: "WhatsAppThreadState",
    ) -> "ConversationHandleOut":
        """Centralized motorcycle/quad/UTV human handoff. All entry points call this."""
        state.needs_human = True
        if ctx.lead:
            ctx.lead.necesita_humano = True
        self.db.commit()

        try:
            self._send_fallback_human_review_notification(
                ctx, state, reason="motorcycle_enquiry"
            )
        except Exception as exc:
            logger.error(
                "M21.1.1 motorcycle notification failed thread_id=%s: %s",
                ctx.thread.id, exc,
            )

        try:
            sent_id = self._send_text_to_wa(ctx, _FALLBACK_WARM_HANDOFF)
            return _out("replied", wa_message_id=sent_id)
        except OutboundBlockedError:
            self.db.commit()
            return _out("human_handoff_blocked", detail="motorcycle_enquiry_kill_switch")

    def _handle_phone_call_escalation(
        self,
        ctx: "_Context",
        state: "WhatsAppThreadState",
    ) -> "ConversationHandleOut":
        """Centralized phone-call/human-agent escalation handler."""
        logger.info("M18 phone-call escalation thread_id=%s", ctx.thread.id)
        state.needs_human = True
        if ctx.lead:
            ctx.lead.necesita_humano = True
        self.db.commit()

        try:
            sent_id = self._send_text_to_wa(ctx, _PHONE_CALL_HANDOFF_REPLY)
            return _out("replied", wa_message_id=sent_id)
        except OutboundBlockedError:
            self.db.commit()
            return _out("human_handoff_blocked", detail="phone_call_kill_switch")

    def _send_service_boundary(
        self,
        ctx: "_Context",
        reply: str,
    ) -> "ConversationHandleOut":
        """Send a boundary or clarification reply with no state mutation."""
        try:
            sent_id = self._send_text_to_wa(ctx, reply)
            return _out("replied", wa_message_id=sent_id)
        except OutboundBlockedError:
            self.db.commit()
            return _out("service_gate_blocked", detail="outbound_disabled")

    def _send_inspectability_reply(
        self,
        ctx: "_Context",
        reply: str,
    ) -> "ConversationHandleOut":
        """Send an inspectability boundary or clarification reply with no state mutation."""
        try:
            sent_id = self._send_text_to_wa(ctx, reply)
            return _out("replied", wa_message_id=sent_id)
        except OutboundBlockedError:
            self.db.commit()
            return _out("inspectability_gate_blocked", detail="outbound_disabled")

    def _escalate_inspectability_to_human(
        self,
        ctx: "_Context",
        state: "WhatsAppThreadState",
    ) -> "ConversationHandleOut":
        """M21.1.2-R1: Second unresolved inspectability turn → warm human handoff."""
        state.needs_human = True
        try:
            sent_id = self._send_text_to_wa(ctx, _FALLBACK_WARM_HANDOFF)
            return _out("replied", wa_message_id=sent_id)
        except OutboundBlockedError:
            self.db.commit()
            return _out("inspectability_gate_blocked", detail="escalation_kill_switch")

    def _extract_vehicle_location_zones(self, text: str) -> list:
        """M21.1.3: Return resolved zones from explicit vehicle-location clauses in text.

        Each pattern in _VEHICLE_LOCATION_CLAUSE_PATTERNS captures a locality
        substring; that substring is passed to _extract_zone_from_text for DB
        resolution. Results are deduplicated by zone_detail.
        """
        seen: set[str | None] = set()
        results = []
        for pattern in _VEHICLE_LOCATION_CLAUSE_PATTERNS:
            for m in pattern.finditer(text):
                locality = m.group(1).strip()
                if not locality:
                    continue
                zone = self._extract_zone_from_text(locality)
                if zone and zone.zone_detail not in seen:
                    seen.add(zone.zone_detail)
                    results.append(zone)
        return results

    def _handle_location_contradiction(
        self,
        ctx: "_Context",
        state: "WhatsAppThreadState",
    ) -> "ConversationHandleOut":
        """M21.1.3: SC17 — same-turn contradictory vehicle locations → clarification.

        No candidate zone mutation, no pricing, no scheduling, no Flow dispatch.
        """
        try:
            sent_id = self._send_text_to_wa(ctx, _LOCATION_CONTRADICTION_CLARIFICATION)
            return _out("replied", wa_message_id=sent_id)
        except OutboundBlockedError:
            self.db.commit()
            return _out("location_contradiction_blocked", detail="outbound_disabled")

    # ── M21.1.4: ASR fuzzy confirmation ───────────────────────────────────────

    def _resolve_fuzzy_key(self, key: str) -> "VehicleMatch | None":
        """Resolve 'Marca||Modelo' back to a VehicleMatch from the live catalog.

        Returns None when the key does not map to a known catalog entry.
        """
        if not key or "||" not in key:
            return None
        marca, modelo = key.split("||", 1)
        from ..services.vehicle_catalog import _FULL_FORM_TO_ENTRY, _norm, _entry_to_match
        nfull = _norm(f"{marca} {modelo}")
        entry = _FULL_FORM_TO_ENTRY.get(nfull)
        if entry:
            return _entry_to_match(entry)
        return None

    def _handle_fuzzy_confirm(
        self,
        ctx: "_Context",
        state: "WhatsAppThreadState",
        result: "FuzzyLookupResult",
    ) -> "ConversationHandleOut":
        """M21.1.4: CONFIRM outcome — store pending key, send confirmation question.

        No candidate created, no pricing, no scheduling, no Flow (VN-12).
        Kill-switch: returns vehicle_fuzzy_blocked when outbound is disabled (VN-14).
        """
        if result.hit is None:
            return _out("replied", wa_message_id=None)
        question = _FUZZY_CONFIRMATION_TEMPLATE.format(
            marca=result.hit.marca, modelo=result.hit.modelo,
        )
        try:
            sent_id = self._send_text_to_wa(ctx, question)
        except OutboundBlockedError:
            self.db.commit()
            return _out("vehicle_fuzzy_blocked", detail="outbound_disabled")
        state.pending_fuzzy_catalog_key = f"{result.hit.marca}||{result.hit.modelo}"
        self.db.commit()
        return _out("replied", wa_message_id=sent_id)

    def _handle_vehicle_inspectability_gate(
        self,
        ctx: "_Context",
        state: "WhatsAppThreadState",
        current_turn_text: str,
    ) -> "ConversationHandleOut | None":
        """M21.1.2 Layer G: vehicle inspectability gate.

        Position: after FAQ bypass (Layer D), before website-form, QUOTED acceptance,
        QUOTED date proposal, SCHEDULING, BR-1, and all downstream mutation.
        Motorcycle (Layer A) takes absolute precedence.

        R1 persistence: inspectability_clarification_sent tracks whether a clarification
        has been sent. On the second unresolved non-running turn, escalates to warm
        human handoff rather than repeating the clarification.
        """
        # Preserve existing needs_human short-circuit — do not duplicate handoffs
        if state.needs_human:
            return None

        is_assembled = _detect_assembled_accessible_confirmation(current_turn_text)
        is_disassembled = _detect_disassembled_vehicle(current_turn_text)
        is_non_running = _detect_non_running_vehicle(current_turn_text)
        is_historical = _detect_historical_or_hypothetical_context(current_turn_text)
        has_access_barrier = _detect_access_barrier(current_turn_text)

        # BR-I6: true contradiction (assembled AND disassembled, not historical) → clarify
        if is_assembled and is_disassembled and not is_historical:
            logger.info("M21.1.2 inspectability contradiction thread_id=%s", ctx.thread.id)
            return self._send_inspectability_reply(ctx, _INSPECTABILITY_NONRUNNING_CLARIFY)

        # BR-I4a: assembled but access-blocked → clarify
        if is_assembled and has_access_barrier:
            logger.info("M21.1.2 inspectability access_barrier thread_id=%s", ctx.thread.id)
            return self._send_inspectability_reply(ctx, _INSPECTABILITY_NONRUNNING_CLARIFY)

        # BR-I3: assembled and accessible (no contradiction, no access barrier) → clear field, continue
        if is_assembled:
            state.inspectability_clarification_sent = False  # R1-C: reset on positive confirmation
            return None

        # BR-I1: current disassembly (not historical) → boundary, clear field
        if is_disassembled and not is_historical:
            state.inspectability_clarification_sent = False  # R1-D: clear on disassembled evidence
            logger.info("M21.1.2 inspectability disassembled thread_id=%s", ctx.thread.id)
            return self._send_inspectability_reply(ctx, _INSPECTABILITY_DISASSEMBLED_REPLY)

        # BR-I5: historical/hypothetical disassembly → continue
        if is_disassembled:
            return None

        # R1-B: second unresolved turn → human escalation.
        # Checked before non-running/access-barrier so repetition escalates instead of re-clarifying.
        if state.inspectability_clarification_sent:
            logger.info("M21.1.2 inspectability repeated_unresolved thread_id=%s", ctx.thread.id)
            return self._escalate_inspectability_to_human(ctx, state)

        # BR-I2: non-running only → first clarification, persist flag
        if is_non_running:
            state.inspectability_clarification_sent = True  # R1-A: persist first clarification
            logger.info("M21.1.2 inspectability non_running thread_id=%s", ctx.thread.id)
            return self._send_inspectability_reply(ctx, _INSPECTABILITY_NONRUNNING_CLARIFY)

        # BR-I4b: access barrier without assembled confirmation → first clarification, persist flag
        if has_access_barrier:
            state.inspectability_clarification_sent = True  # R1-A: persist first clarification
            logger.info("M21.1.2 inspectability access_barrier_only thread_id=%s", ctx.thread.id)
            return self._send_inspectability_reply(ctx, _INSPECTABILITY_NONRUNNING_CLARIFY)

        return None

    def _handle_explicit_service_gate(
        self,
        ctx: "_Context",
        current_turn_text: str,
    ) -> "ConversationHandleOut | None":
        """Layer C: explicit unsupported-service gate (all stages).

        Returns a boundary response for F12/transfer/repair, or None to continue.
        Motorcycle and phone-call are handled by Layers A and B (higher priority).
        """
        text_norm = self._norm_text(current_turn_text)

        if self._detect_f12_request(text_norm):
            return self._send_service_boundary(ctx, _F12_BOUNDARY_REPLY)

        if self._detect_transfer_request(text_norm):
            return self._send_service_boundary(ctx, _TRANSFER_BOUNDARY_REPLY)

        if self._detect_repair_request(text_norm):
            return self._send_service_boundary(ctx, _REPAIR_BOUNDARY_REPLY)

        return None

    def _handle_qualifying_intent(
        self,
        ctx: "_Context",
        state: "WhatsAppThreadState",
        current_turn_text: str,
    ) -> "ConversationHandleOut | None":
        """Layer F: QUALIFYING intent gate (QUALIFYING/None only).

        F12/transfer/repair handled by all-stage Layer C.
        FAQ bypass handled by all-stage Layer D.
        Returns a response for UNCERTAIN turns, or None when intent is confirmed.

        M21.1.1-R1: Nine-step pass-through order (first match wins):
          1. Explicit inspection request → set intent, continue
          2. Pre-purchase signal (expanded) → set intent, continue
          3. FAQ / soft-close phrase → continue (no intent change)
          4. Vehicle catalog hit → continue (no intent change)
          5. Vehicle-location phrase ("está en/por X") → continue
          6. Price question → continue
          7. Prior confirmed intent → continue
          8. Established inspection context (candidate or state flags) → continue
          9. No signal → UNCERTAIN clarification reply
        """
        text_norm = self._norm_text(current_turn_text)

        # 1. Explicit inspection request → set intent, continue to full AI path
        if self._detect_explicit_inspection_request(text_norm):
            state.last_intent = _INTENT_PREPURCHASE
            self.db.commit()
            return None

        # 2. Pre-purchase signal → set intent, continue
        if self._detect_prepurchase_signal(text_norm):
            state.last_intent = _INTENT_PREPURCHASE
            self.db.commit()
            return None

        # 3. FAQ / soft-close → continue; if a specific vehicle is also present, set intent (BR-1 Row 16)
        if _is_general_faq_or_soft_close(current_turn_text):
            if lookup_vehicle(current_turn_text) is not None:
                state.last_intent = _INTENT_PREPURCHASE
                self.db.commit()
            return None

        # 4. Specific vehicle recognized → CONTINUE_SET (BR-1 Row 5)
        if lookup_vehicle(current_turn_text) is not None:
            state.last_intent = _INTENT_PREPURCHASE
            self.db.commit()
            return None

        # 5. Vehicle-location phrase ("está en/por X") → CONTINUE_SET (BR-1 Rows 7–9)
        if self._detect_vehicle_location_phrase(text_norm):
            state.last_intent = _INTENT_PREPURCHASE
            self.db.commit()
            return None

        # 6. Inspection-specific price question ("¿cuánto sale la revisión?") → set intent, continue
        if self._detect_price_question(text_norm):
            state.last_intent = _INTENT_PREPURCHASE
            self.db.commit()
            return None

        # 7. Prior confirmed intent persisted from earlier turn → continue
        if state.last_intent == _INTENT_PREPURCHASE:
            return None

        # 8. Established inspection context (non-moto candidate or delivery flags) → continue
        if self._has_established_inspection_context(ctx, state):
            return None

        # 9. No inspection service signal found → UNCERTAIN clarification
        return self._send_service_boundary(ctx, _UNCERTAIN_SERVICE_REPLY)

    def _handle_general_information_ai(
        self,
        ctx: "_Context",
        event: "ConversationHandleIn",
        ai_input_messages: "list[str]",
    ) -> "ConversationHandleOut":
        """Handle FAQ/greeting/informational messages using the AI pipeline.

        Reads only decision['reply']. Does not call _apply_candidate, _apply_extracted,
        or mutate lead.flag or state.needs_human. Does not set state.last_intent.
        """
        if ctx.lead is None:
            return self._send_service_boundary(ctx, _UNCERTAIN_SERVICE_REPLY)

        messages = self._build_ai_messages(ctx, event, ai_input_messages)

        try:
            ai_raw = self._call_openai(messages)
            decision = json.loads(ai_raw)
        except Exception as exc:
            logger.warning(
                "M21.1.1 FAQ AI call failed thread_id=%s: %s", ctx.thread.id, exc
            )
            decision = {"reply": "Disculpá la demora, ya te ayudamos."}

        reply = decision.get("reply") or ""
        if reply:
            try:
                sent_id = self._send_text_to_wa(ctx, reply)
                return _out("replied", wa_message_id=sent_id)
            except OutboundBlockedError:
                self.db.commit()
                return _out("service_gate_blocked", detail="faq_kill_switch")
        return _out("skipped_dedup", detail="faq_no_reply")

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

        reply = "¡Perfecto! ¿Qué día y horario te viene mejor para la revisión?"
        sent_id = self._send_text_to_wa(ctx, reply)
        return _out("replied", wa_message_id=sent_id)

    # ── Website form handler ──────────────────────────────────────────────

    def _handle_website_form(
        self,
        ctx: _Context,
        state: "WhatsAppThreadState",
        form_data: dict,
    ) -> "ConversationHandleOut":
        """Process a wa.me prefilled form submission.

        Parses vehicle + zone, runs deterministic pricing, creates a candidate,
        advances to SCHEDULING, and asks for day/time.  Sets flag=PRESUPUESTO_ENVIADO
        because the website already showed the customer a price.
        """
        lead = ctx.lead
        assert lead is not None

        # ── Update lead contact details from form ─────────────────────────
        if form_data.get("customer_name"):
            first = form_data["customer_name"].split()[0]
            lead.nombre = first
            state.customer_name = form_data["customer_name"]
        if form_data.get("phone") and not lead.telefono:
            lead.telefono = form_data["phone"]

        # ── Resolve vehicle from catalog first, fall back to form tipo ────
        vehicle_text = form_data.get("vehicle_text", "")
        marca: str | None = None
        modelo: str | None = None
        tipo_vehiculo: str | None = None

        catalog_match = lookup_vehicle(vehicle_text) if vehicle_text else None
        if catalog_match:
            tipo_vehiculo = catalog_match.tipo_vehiculo
            marca = catalog_match.marca
            modelo = catalog_match.modelo
            logger.info(
                "M18 website_form catalog hit thread_id=%s alias=%r tipo=%s",
                ctx.thread.id, catalog_match.matched_alias, tipo_vehiculo,
            )
        else:
            # Split "Toyota Corolla 2020" → marca=Toyota, modelo=Corolla 2020
            parts = vehicle_text.split(None, 1)
            marca = parts[0] if parts else None
            modelo = parts[1] if len(parts) > 1 else None

        if not tipo_vehiculo and form_data.get("submitted_tipo"):
            tipo_vehiculo = _normalize_submitted_tipo(form_data["submitted_tipo"])
        tipo_vehiculo = tipo_vehiculo or "AUTO"

        if tipo_vehiculo.upper() == "MOTO":
            return self._motorcycle_human_handoff(ctx, state)
        state.last_intent = _INTENT_PREPURCHASE  # website form = confirmed pre-purchase intent

        # ── Create candidate ──────────────────────────────────────────────
        self._apply_candidate(ctx, {
            "action": "create",
            "marca": marca,
            "modelo": modelo,
            "tipo_vehiculo": tipo_vehiculo,
            "status": "current_focus",
        })

        # ── Resolve zone ──────────────────────────────────────────────────
        if form_data.get("zone_detail") and not state.home_zone_detail:
            state.home_zone_detail = form_data["zone_detail"]
            self._normalize_zone_from_db(ctx, state)

        # ── Deterministic price ───────────────────────────────────────────
        real_price_quote = self._compute_price_quote(ctx, state)

        submitted_total = form_data.get("submitted_total")
        if real_price_quote and submitted_total is not None:
            if abs(submitted_total - real_price_quote.precio_total) > 100:
                logger.warning(
                    "M18 website_form price_mismatch thread_id=%s submitted=%s deterministic=%s",
                    ctx.thread.id, submitted_total, real_price_quote.precio_total,
                )

        if form_data.get("ref"):
            logger.info(
                "M18 website_form ref=%r thread_id=%s", form_data["ref"], ctx.thread.id
            )

        # ── Advance state ─────────────────────────────────────────────────
        lead.flag = "PRESUPUESTO_ENVIADO"
        state.last_stage = STAGE_SCHEDULING
        state.is_website_lead = True  # route to short website Flow at booking time

        # ── Build reply ───────────────────────────────────────────────────
        customer = (state.customer_name or "").split()[0] if state.customer_name else (lead.nombre or "")
        greeting = f"Perfecto, {customer}!" if customer else "Perfecto!"

        vehicle_desc = " ".join(filter(None, [marca, modelo])) or vehicle_text or "el vehículo"
        zone_desc = state.home_zone_detail or form_data.get("zone_detail") or "la zona"

        if real_price_quote:
            total_str = f"${real_price_quote.precio_total:,.0f}".replace(",", ".")
            price_block = f" El precio de la revisión es {total_str}."
        else:
            price_block = ""

        reply = (
            f"{greeting} Recibí tu solicitud para revisar el {vehicle_desc} en {zone_desc}.{price_block} "
            f"¿Qué día y horario te viene mejor?"
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

        if sched_out.valid:
            # Slot is available — store it, clear scheduling context, send Flow.
            state.preferred_day = preferred_day_iso
            state.preferred_time = preferred_time_obj.strftime("%H:%M")
            state.active_requested_date = None
            state.last_requested_time = None
            state.last_offered_slots = None
            state.last_visible_slots = None
            flow_token = f"{ctx.thread.id}-{int(_time.time())}"
            state.flow_booking_token = flow_token
            # last_stage stays SCHEDULING — flow_booking_token signals the form was sent.
            # Stage only advances to BOOKED when the form response arrives.
            self.db.commit()

            # Choose Flow based on lead source: website leads use the short
            # website-specific Flow; all others use the generic full-data Flow.
            is_website = getattr(state, "is_website_lead", False)
            if is_website:
                flow_id = (self.settings.whatsapp_website_flow_id or "").strip()
                if not flow_id:
                    logger.error(
                        "M18 WHATSAPP_WEBSITE_FLOW_ID not set — "
                        "website lead thread_id=%s, falling back to chat collection",
                        ctx.thread.id,
                    )
                    # Don't send the generic Flow (it would re-ask data already collected).
                    # Ask for the two remaining missing pieces via chat.
                    fallback = (
                        "Ese horario está disponible 🎉 "
                        "Para confirmar el turno necesito dos datos más: "
                        "¿Me podés dar tu email y la dirección exacta donde está el vehículo?"
                    )
                    sent_id = self._send_text_to_wa(ctx, fallback)
                    return _out("replied", wa_message_id=sent_id)
            else:
                flow_id = (self.settings.whatsapp_flow_id or "").strip()
                if not flow_id:
                    logger.error("M18 WHATSAPP_FLOW_ID not set — sending text fallback")
                    fallback = ai_reply or "Perfecto, tenemos disponibilidad. ¡Ya te confirmo!"
                    sent_id = self._send_text_to_wa(ctx, fallback)
                    return _out("replied", wa_message_id=sent_id)

            body = (
                "Ese horario está disponible 🎉 "
                "Para confirmar el turno, completá el formulario con tus datos."
            )
            screen = "WEBSITE_FINAL_DATA" if is_website else "MAIN"
            try:
                sent_id = self._send_flow_button(
                    ctx, body, flow_token, flow_id=flow_id, initial_screen=screen
                )
                return _out("flow_button_sent", wa_message_id=sent_id)
            except Exception as exc:
                logger.error("M18 flow button send failed thread_id=%s: %s", ctx.thread.id, exc)
                state.flow_booking_token = None
                state.last_stage = STAGE_SCHEDULING
                self.db.commit()
                return None
        else:
            # Slot rejected — fetch the full day's availability via list_slots so
            # "por la tarde" follow-ups can find afternoon options.
            # sched_out.suggested_slots is capped at 5 and starts from the beginning
            # of business hours (morning only); list_slots uses max_results=24 and
            # returns every usable slot across the whole day.
            all_slots: list[str] = []
            try:
                all_day = self._schedule.list_slots(sched_in)
                all_slots = [s for s in (all_day.slots or []) if isinstance(s, str)]
            except Exception as exc:
                logger.warning(
                    "M18 list_slots failed for full-day alternatives thread_id=%s: %s",
                    ctx.thread.id, exc,
                )
            if not all_slots:
                all_slots = [s for s in sched_out.suggested_slots if isinstance(s, str)]
            all_slots = sorted(all_slots)  # always store chronologically

            state.preferred_day = None
            state.preferred_time = None
            state.active_requested_date = preferred_day_iso
            state.last_requested_time = preferred_time_obj.strftime("%H:%M")
            state.last_offered_slots = json.dumps(all_slots)

            date_human = _format_date_human(preferred_day_iso, date.today())
            if all_slots:
                # Show all available slots (capped at 12) in chronological order.
                shown = all_slots[:12]
                state.last_visible_slots = json.dumps(shown)
                slot_list = ", ".join(shown[:-1]) + f" o {shown[-1]}" if len(shown) >= 2 else shown[0]
                msg = (
                    f"Para {date_human} a las {preferred_time_obj.strftime('%H:%M')} "
                    f"no tenemos disponibilidad. "
                    f"Horarios disponibles: {slot_list}. ¿Alguno te viene bien?"
                )
            else:
                state.last_visible_slots = None
                reasons = "; ".join(sched_out.reasons[:2]) if sched_out.reasons else "sin disponibilidad"
                msg = (
                    f"Para {date_human} no hay horarios disponibles "
                    f"({reasons}). ¿Tenés otro día preferido?"
                )
            sent_id = self._send_text_to_wa(ctx, msg)
            return _out("replied", wa_message_id=sent_id)

    # ── Period-based scheduling: "por la tarde" ───────────────────────────

    def _handle_period_request(
        self,
        ctx: _Context,
        state: WhatsAppThreadState,
        period: str,
    ) -> ConversationHandleOut | None:
        """Filter last_offered_slots by time period and reply with available options."""
        try:
            offered = json.loads(state.last_offered_slots or "[]")
        except (json.JSONDecodeError, TypeError):
            return None

        if period == "tarde":
            filtered = [s for s in offered if _time_hour(s) >= _AFTERNOON_HOUR]
            period_label, alt_label = "tarde", "mañana"
        else:
            filtered = [s for s in offered if _time_hour(s) < _AFTERNOON_HOUR]
            period_label, alt_label = "mañana", "tarde"

        date_human = _format_date_human(str(state.active_requested_date), date.today())
        if filtered:
            # Show all available slots for the period (not capped) so "el último" and
            # "el primero" refer to what the user actually sees, not a hidden subset.
            shown = filtered
            state.last_visible_slots = json.dumps(shown)
            if len(shown) >= 2:
                slot_list = ", ".join(shown[:-1]) + f" o {shown[-1]}"
            else:
                slot_list = shown[0]
            msg = f"Sí, para {date_human} por la {period_label} tengo: {slot_list}. ¿Cuál te sirve?"
        else:
            state.last_visible_slots = None
            msg = (
                f"Para {date_human} no hay horarios disponibles por la {period_label}. "
                f"¿Podría ser por la {alt_label}?"
            )
        sent_id = self._send_text_to_wa(ctx, msg)
        return _out("replied", wa_message_id=sent_id)

    # ── Day-only request: user gives a day but no time ────────────────────

    def _handle_day_only_request(
        self,
        ctx: _Context,
        state: WhatsAppThreadState,
        day_iso: str,
        period: str | None = None,
    ) -> ConversationHandleOut:
        """User named a day (with optional time period) but no exact slot.
        Fetches full-day availability, stores sorted slots, and replies with options
        filtered by period ('tarde'/'manana') when specified."""
        from ..schemas.schedule import ScheduleCheckIn
        assert ctx.lead is not None
        zone_parts = [state.home_zone_detail, state.home_zone_group, "Buenos Aires, Argentina"]
        address = ", ".join(p for p in zone_parts if p)
        sched_in = ScheduleCheckIn(
            preferred_day=date.fromisoformat(day_iso),
            preferred_time=time(9, 0),
            address=address,
            zone_group=state.home_zone_group,
            zone_detail=state.home_zone_detail,
            exclude_revision_id=state.current_revision_id,
            is_holiday=False,
        )
        is_closed = False
        try:
            slots_out = self._schedule.list_slots(sched_in)
            all_slots = sorted([s for s in (slots_out.slots or []) if isinstance(s, str)])
            is_closed = slots_out.business_hours == "cerrado"
        except Exception as exc:
            logger.warning("M18 list_slots failed in day_only thread_id=%s: %s", ctx.thread.id, exc)
            all_slots = []

        state.active_requested_date = day_iso
        state.last_offered_slots = json.dumps(all_slots)  # full-day, sorted — for period filtering

        date_human = _format_date_human(day_iso, date.today())

        # Filter by time period when the user requested morning or afternoon.
        if period == "tarde":
            display_slots = [s for s in all_slots if _time_hour(s) >= _AFTERNOON_HOUR]
            period_label: str | None = "tarde"
            alt_label: str | None = "mañana"
        elif period == "manana":
            display_slots = [s for s in all_slots if _time_hour(s) < _AFTERNOON_HOUR]
            period_label = "mañana"
            alt_label = "tarde"
        else:
            display_slots = all_slots[:12]  # no period — show all reasonable slots
            period_label = None
            alt_label = None

        if display_slots:
            shown = display_slots  # show all filtered; period filter already limits scope
            state.last_visible_slots = json.dumps(shown)
            slot_list = ", ".join(shown[:-1]) + f" o {shown[-1]}" if len(shown) >= 2 else shown[0]
            if period_label:
                msg = f"Para {date_human} por la {period_label} tengo: {slot_list}. ¿A qué hora te viene bien?"
            else:
                msg = f"Para {date_human} tengo disponible: {slot_list}. ¿A qué hora te viene bien?"
        elif period_label and all_slots:
            # Requested period has no slots — offer the other period.
            shown_all = all_slots[:12]
            state.last_visible_slots = json.dumps(shown_all)
            sl = ", ".join(shown_all[:-1]) + f" o {shown_all[-1]}" if len(shown_all) >= 2 else shown_all[0]
            msg = (
                f"Para {date_human} no hay horarios por la {period_label}. "
                f"Tengo disponible: {sl}. ¿Alguno te sirve?"
            )
        elif is_closed:
            state.last_visible_slots = None
            msg = (
                f"El {date_human} no tenemos operaciones. "
                "¿Qué otro día te funciona? De lunes a sábado estamos disponibles."
            )
        else:
            state.last_visible_slots = None
            msg = (
                f"Para {date_human} no hay horarios libres en este momento. "
                "¿Tenés otro día preferido?"
            )
        logger.info(
            "M18 day_only_request thread_id=%s day=%s period=%s display_slots=%d",
            ctx.thread.id, day_iso, period, len(display_slots),
        )
        sent_id = self._send_text_to_wa(ctx, msg)
        return _out("replied", wa_message_id=sent_id)

    # ── Flow failure: user can't open the WhatsApp Flow form ─────────────

    def _handle_flow_failure(
        self,
        ctx: _Context,
        state: WhatsAppThreadState,
        last_message: str,
    ) -> ConversationHandleOut:
        """User reports they cannot open the Flow form (e.g. on WhatsApp Web).
        Hand off to human immediately."""
        lead = ctx.lead
        assert lead is not None

        lead.necesita_humano = True
        lead.estado = "ATENCION_HUMANA"
        state.needs_human = True
        state.last_stage = STAGE_HUMAN

        logger.info(
            "M18 flow_failure escalation thread_id=%s token=%s",
            ctx.thread.id, state.flow_booking_token,
        )

        self._send_scheduling_handoff_email(
            ctx=ctx, state=state, thread_rev_id=state.current_revision_id, last_message=last_message,
        )

        msg = (
            "Entiendo, el formulario a veces no abre bien desde WhatsApp Web o la computadora. "
            "Le paso tu solicitud a Julián para que la procese manualmente "
            "y te confirma el turno por acá. 🙌"
        )
        sent_id = self._send_text_to_wa(ctx, msg)
        return _out("replied", wa_message_id=sent_id)

    # ── Scheduling escalation to human ────────────────────────────────────

    def _handle_scheduling_escalation(
        self,
        ctx: _Context,
        state: WhatsAppThreadState,
        last_message: str,
    ) -> ConversationHandleOut:
        """Customer insists on an unavailable slot — create a provisional revision
        and hand off to Julián.  Does NOT send Flow, does NOT set AGENDADO."""
        lead = ctx.lead
        assert lead is not None

        lead.necesita_humano = True
        lead.flag = "ACEPTADO"
        lead.estado = "ATENCION_HUMANA"
        state.needs_human = True
        state.last_stage = STAGE_HUMAN

        thread_rev_id: int | None = state.current_revision_id

        if thread_rev_id is None:
            # Create provisional ThreadRevision and CRM Revision with all known data.
            focus = self._focus_candidate(ctx)
            real_price_quote = self._compute_price_quote(ctx, state)

            prov_date: date | None = None
            prov_time: time | None = None
            act_date_str = str(state.active_requested_date or "")
            if act_date_str:
                try:
                    prov_date = date.fromisoformat(act_date_str)
                except ValueError:
                    pass
            last_t_str = str(state.last_requested_time or "")
            if last_t_str:
                try:
                    prov_time = time.fromisoformat(last_t_str)
                except ValueError:
                    pass

            buyer_name = (
                (state.customer_name or "").strip()
                or (lead.nombre or "").strip()
                or None
            )
            thread_rev = ThreadRevision(
                thread_id=ctx.thread.id,
                candidate_id=focus.id if focus else None,
                status="provisional",
                buyer_name=buyer_name,
                buyer_phone=ctx.contact.wa_id or None,
                buyer_email=lead.email or None,
                scheduled_date=prov_date,
                scheduled_time=prov_time,
                tipo_vehiculo=focus.tipo_vehiculo if focus else None,
                marca=focus.marca if focus else None,
                modelo=focus.modelo if focus else None,
                anio=focus.anio if focus else None,
            )
            self.db.add(thread_rev)

            crm_rev = Revision(
                lead_id=lead.id,
                tipo_vehiculo=focus.tipo_vehiculo if focus else None,
                marca=focus.marca if focus else None,
                modelo=focus.modelo if focus else None,
                anio=focus.anio if focus else None,
                zone_group=state.home_zone_group,
                zone_detail=state.home_zone_detail,
                turno_fecha=prov_date,
                turno_hora=prov_time,
                turno_notas="Pendiente coordinación manual — cliente insiste con horario no disponible",
            )
            self.db.add(crm_rev)
            self.db.flush()
            self._pricing.recalculate_revision_if_possible(db=self.db, revision=crm_rev)

            state.current_revision_id = thread_rev.id
            thread_rev_id = thread_rev.id
            logger.info(
                "M18 provisional revision created thread_id=%s thread_rev=%s",
                ctx.thread.id, thread_rev_id,
            )

        self._send_scheduling_handoff_email(
            ctx=ctx, state=state, thread_rev_id=thread_rev_id, last_message=last_message,
        )

        msg = (
            "Entiendo, querés ese horario puntual. "
            "Lo paso con Julián para ver si puede acomodarlo manualmente "
            "y te respondemos por acá apenas lo revise. 🙌"
        )
        sent_id = self._send_text_to_wa(ctx, msg)
        return _out("replied", wa_message_id=sent_id)

    def _send_scheduling_handoff_email(
        self,
        ctx: _Context,
        state: WhatsAppThreadState,
        thread_rev_id: int | None,
        last_message: str,
    ) -> None:
        from ..services.resend_email import send_scheduling_handoff_notification
        settings = self.settings
        if not settings.resend_api_key:
            logger.warning("M18 RESEND_API_KEY missing — handoff email skipped")
            return

        lead = ctx.lead
        assert lead is not None
        focus = self._focus_candidate(ctx)
        real_price_quote = self._compute_price_quote(ctx, state)

        offered_str = "Sin dato"
        if state.last_offered_slots:
            try:
                offered_str = ", ".join(json.loads(state.last_offered_slots)) or "Sin dato"
            except (json.JSONDecodeError, TypeError):
                pass

        act_date = str(state.active_requested_date or "")
        last_t = str(state.last_requested_time or "")
        requested_str = (
            f"{_format_date_human(act_date, date.today())} a las {last_t}"
            if act_date and last_t
            else (act_date or "Sin dato")
        )

        vehicle_parts = [
            focus.marca if focus else None,
            focus.modelo if focus else None,
            str(focus.anio) if focus and focus.anio else None,
        ]
        vehicle_str = " ".join(p for p in vehicle_parts if p) or "Sin dato"

        send_scheduling_handoff_notification(
            api_key=settings.resend_api_key,
            from_email=settings.internal_booking_email_from,
            to_email=settings.internal_booking_email_to,
            reply_to_email=settings.internal_booking_email_reply_to,
            lead_id=lead.id,
            thread_id=ctx.thread.id,
            revision_id=thread_rev_id or "—",
            buyer_name=state.customer_name or lead.nombre or "Sin dato",
            buyer_phone=ctx.contact.wa_id or "Sin dato",
            vehicle=vehicle_str,
            tipo_vehiculo=focus.tipo_vehiculo if focus else "Sin dato",
            zone_group=state.home_zone_group or "Sin dato",
            zone_detail=state.home_zone_detail or "Sin dato",
            precio_total=str(real_price_quote.precio_total) if real_price_quote else "Sin dato",
            requested_slot=requested_str,
            offered_slots=offered_str,
            last_message=last_message or "Sin dato",
        )

    # ── AI prompt ─────────────────────────────────────────────────────────

    def _build_ai_messages(
        self,
        ctx: _Context,
        event: ConversationHandleIn,
        ai_input_messages: list[str],
        pre_detected_vehicle: VehicleMatch | None = None,
        real_price_quote: "PricingQuote | None" = None,
        include_narrative: bool = False,
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

        # History: always use DB messages (chronological). The n8n-provided
        # arrays arrive in separate buckets (all BOT, then all CLIENT) which
        # scrambles time order — the DB is always correctly ordered by created_at.
        history_lines: list[str] = []
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

        # Narrative interpretation block (NU-14: only included when narrative AI adds value)
        narrative_block = ""
        narrative_json_fields = ""
        if include_narrative:
            narrative_block = """

INTERPRETACIÓN NARRATIVA — completá estos campos en el JSON:
Interpretá el significado del MENSAJE COMPLETO, no palabras sueltas. Distinguí hechos actuales de históricos, hipotéticos o corregidos.

- "deferred_interest": true SOLO si el mensaje en su conjunto significa "todavía estoy buscando / no estoy listo / ya les aviso" Y no hay ningún vehículo concreto en la consulta actual.
- "vehicle_make_model": {"value": "Marca Modelo", "status": "CONFIRMED|LIKELY|UNCERTAIN|SUPERSEDED|HISTORICAL|HYPOTHETICAL|ABSENT"} — vehículo ACTUAL. Si fue corregido ("no, es un..."), usá el corregido como CONFIRMED y el previo como SUPERSEDED.
- "vehicle_year": {"value": 2019, "status": "CONFIRMED|UNCERTAIN|..."} — año del vehículo actual.
- "vehicle_location": {"value": "Palermo", "status": "CONFIRMED|..."} — dónde ESTÁ el auto (no dónde vive el cliente).
- "customer_origin": {"value": "La Plata", "status": "CONFIRMED|..."} — dónde vive el CLIENTE (diferente de vehicle_location).
- "inspectability": {"value": "ASSEMBLED_ACCESSIBLE|DISASSEMBLED_BLOCKED|UNRESOLVED_NON_RUNNING", "status": "CONFIRMED|HYPOTHETICAL|..."} — estado físico para la revisión. Solo CONFIRMED si el cliente lo afirma explícitamente. HYPOTHETICAL para preguntas hipotéticas.
- "asks_price": true si pregunta por precio/cotización.
- "asks_faq": true si pregunta cómo funciona el servicio.
- "asks_schedule": true si quiere coordinar fecha/horario.

Ejemplos: "Hola estoy buscando un auto, agendé esto para cuando decida" → deferred_interest: true | "Pensaba comprar un Focus pero al final es un Corolla 2020" → vehicle_make_model: {value: "Toyota Corolla", status: "CONFIRMED"} | "vivo en La Plata pero el auto está en Palermo" → customer_origin: {value: "La Plata"}, vehicle_location: {value: "Palermo"} | "Es un Ford Ka... no, perdón, es un Ford Kuga" → vehicle_make_model: {value: "Ford Kuga", status: "CONFIRMED"}"""
            narrative_json_fields = """
  "deferred_interest": false,
  "vehicle_make_model": null,
  "vehicle_year": null,
  "vehicle_location": null,
  "customer_origin": null,
  "inspectability": null,
  "asks_price": false,
  "asks_faq": false,
  "asks_schedule": false,"""

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
4. Etapa SCHEDULING → preguntá qué día y horario le viene mejor. Si el cliente menciona un día, confirmá que lo recibiste y ofrecé opciones de horario. Si el cliente menciona un horario, confirmá y avanzá.
5. Derivá a humano si hay queja, solicitud especial o no podés resolver → needs_human=true.
6. NUNCA uses lead_flag="PERDIDO", "BUSCANDO_AUTO", ni "RECOMPRA".
7. Respondé en español, registro informal argentino (voseo), conciso.
8. NUNCA inventes ni calcules precios. El único precio válido es el que aparece en PRECIO CALCULADO.
9. NUNCA preguntes el precio de venta, valor o tasación del vehículo. Ridecheck NO necesita eso para cotizar la inspección.
10. Si la zona no está disponible en tu sistema, pedí confirmación de una zona/barrio cercano. NUNCA pidas precio del vehículo ni datos de la venta.
11. NUNCA uses "cliente" como nombre de persona. Si no sabés el nombre, salteá el nombre por completo — decí "Genial!" en lugar de "Genial, cliente!".
12. En etapa SCHEDULING, NO menciones precio ni cotización. El cliente ya recibió la cotización. Solo pedí día y horario disponible.
13. NUNCA digas que la revisión quedó confirmada, el turno está reservado, ni prometas enviar recordatorios. El turno se confirma solo cuando el cliente completa el formulario de datos. En SCHEDULING, tu única función es coordinar día y horario.
14. La revisión se realiza en el lugar donde está el auto (revisión pre-compra a domicilio).
15. La revisión suele durar aproximadamente una hora.
16. Cuanto antes nos avise el cliente, mejor. Coordinamos según la disponibilidad de agenda.

TIPOS DE VEHÍCULO VÁLIDOS: AUTO, SUV_4X4_DEPORTIVO, SUV/4x4, CLASICO, MOTO, ESCANEO_MOTOR{narrative_block}

Respondé SOLO con JSON válido:
{{
  "intent": "GREETING|QUALIFYING|QUOTE_SENT|ACCEPTANCE|SCHEDULING|OBJECTION|ESCALATE|OTHER",
  "reply": "texto en español",
  "lead_flag": null,
  "needs_human": false,{narrative_json_fields}
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

    def _handle_deferred_interest(
        self, ctx: "_Context", state: "WhatsAppThreadState"
    ) -> "ConversationHandleOut":
        """NU-6: send approved deferred-interest copy; no commercial mutation."""
        return self._send_service_boundary(ctx, DEFERRED_RESPONSE_ES)

    def _apply_narrative_interpretation(
        self,
        ctx: "_Context",
        state: "WhatsAppThreadState",
        narr: "NarrativeInterpretation",
    ) -> None:
        """Apply active narrative facts to thread state after deterministic validation.

        NU-9: deterministic gates remain authoritative — this method only updates
        state when narrative evidence is CONFIRMED/LIKELY and the field is not
        already set by a higher-priority source (catalog, Flow, candidate).
        NU-16 safety: called only when narr is not None; failures are swallowed.
        """
        try:
            # Inspectability: resolve pending clarification if narrative confirms state
            if narr.inspectability and narr.inspectability.is_active():
                val = narr.inspectability.value
                if val == INSP_ASSEMBLED_ACCESSIBLE:
                    # Vehicle confirmed accessible — clear any pending clarification flag
                    state.inspectability_clarification_sent = False
                elif val in (INSP_DISASSEMBLED_BLOCKED, INSP_UNRESOLVED_NON_RUNNING):
                    # Confirmed blocked/non-running — flag as needing clarification
                    if not state.inspectability_clarification_sent:
                        state.inspectability_clarification_sent = True
        except Exception as exc:
            logger.warning(
                "M21.1.6 narrative application error thread_id=%s: %s",
                ctx.thread.id, exc,
            )

    def _apply_extracted(self, ctx: _Context, state: WhatsAppThreadState, extracted: dict) -> None:
        if extracted.get("customer_name"):
            state.customer_name = extracted["customer_name"]
            if ctx.lead and not ctx.lead.nombre:
                ctx.lead.nombre = extracted["customer_name"].split()[0]
        if extracted.get("zone_detail") and not state.home_zone_group:
            # Only accept AI zone if we don't already have a DB-validated zone_group.
            # This prevents AI from overwriting a confirmed zone with a hallucination.
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
        # M21.1.3 LR-7: do not inherit stale thread zone — candidate starts empty.
        # Vehicle-location evidence from the current turn is written to the candidate
        # by the role-aware zone detection block that follows this call in _process_text.
        candidate = WhatsAppThreadCandidate(
            thread_id=ctx.thread.id,
            marca=match.marca,
            modelo=match.modelo,
            tipo_vehiculo=match.tipo_vehiculo,
            anio=anio,
            zone_group=None,
            zone_detail=None,
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

        # Normalize: strip Unicode diacritics, lowercase, collapse whitespace.
        # Applied to both the customer text and every DB/alias value compared below
        # so that "Benavídez" matches the stored "Benavidez" (and vice-versa).
        def _n(s: str) -> str:
            return " ".join(_strip_accents(s).split())

        normalized_text = _n(text)
        zones = list(self.db.execute(_select(ViaticosZone)).scalars().all())

        # Dock Sud fast-path: "doc sud", "dock sud" and typo variants → Sur/Dock Sud.
        # Must be checked BEFORE the CABA synonyms and zone_detail loop to prevent
        # a partial "sud" match from accidentally resolving to "Avellaneda".
        for alias in _DOCK_SUD_ALIASES:
            if alias in normalized_text:
                for z in zones:
                    if (
                        _n(z.zone_group or "") == "sur"
                        and _n(z.zone_detail or "") == "dock sud"
                    ):
                        return z
                # Defensive: migration not yet applied
                from types import SimpleNamespace as _NS
                return _NS(zone_group=_DOCK_SUD_GROUP, zone_detail=_DOCK_SUD_CANONICAL, viaticos=30000)  # type: ignore[return-value]

        # CABA synonym fast-path: "capital federal", "ciudad autónoma de buenos aires" etc.
        # Checked before the zone_detail loop because these strings don't appear
        # as zone_detail values in the DB.  "caba" / "CABA" are NOT in this set —
        # they're handled by the zone_detail="CABA" sentinel row in the loop below.
        for synonym in _CABA_SYNONYMS:
            if _n(synonym) in normalized_text:
                # Return the CABA sentinel row from the already-loaded zones list.
                for z in zones:
                    if _n(z.zone_group or "") == "caba" and _n(z.zone_detail or "") == "caba":
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
            zone_norm = _n(zone.zone_detail)
            if zone_norm in normalized_text:
                return zone

        # Pre-AI group detection: if text mentions a bare zone_group name (e.g. "Oeste")
        # that has no matching zone_detail, return a sentinel so the caller can set
        # home_zone_group and the ACEPTADO guard can ask for the specific barrio.
        seen_groups: set[str] = set()
        for zone in zones:
            if zone.zone_group:
                g_norm = _n(zone.zone_group)
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

        # Dock Sud synonym normalization BEFORE the DB lookup.
        if _is_dock_sud_alias(state.home_zone_detail):
            logger.info(
                "M18 Dock Sud alias normalized: %r → %r thread_id=%s",
                state.home_zone_detail, _DOCK_SUD_CANONICAL,
                ctx.thread.id if ctx else "?",
            )
            state.home_zone_detail = _DOCK_SUD_CANONICAL
            state.home_zone_group = _DOCK_SUD_GROUP
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
        if _PRICE_REQUEST_RE.search(reply):
            # AI asked the customer to supply price info — never allowed.
            logger.warning("M18 scrubbing AI price-request reply — customer must never be asked for price")
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
        # M21.1.3 LR-1: candidate zone is authoritative; thread state is fallback only.
        zone_group = (focus.zone_group or "") or (state.home_zone_group or "")
        zone_detail = (focus.zone_detail or "") or (state.home_zone_detail or "")
        if not (zone_group or zone_detail):
            return None
        try:
            q = self._pricing.quote(
                db=self.db,
                tipo_vehiculo=focus.tipo_vehiculo,
                zone_group=zone_group,
                zone_detail=zone_detail,
            )
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
        gate = OutboundSafetyGate(self.db)
        result = gate.attempt(wa_id=wa_id, thread_id=ctx.thread.id, text=text, now=now_utc)
        if result.outcome != GateOutcome.ALLOWED:
            raise OutboundBlockedError(
                sender_path="engine._send_text_to_wa", kind="text",
                to_wa_id=wa_id, thread_id=ctx.thread.id, text=text,
                gate_outcome=result.outcome.value,
            )
        wa_message_id: str | None = None
        try:
            wa_message_id, _ = _send_whatsapp_cloud_text(to_wa_id=wa_id, text=text)
            gate.mark_sent(result.message_id, wa_message_id)
        except Exception as exc:
            logger.error("M18 send_text failed thread_id=%s: %s", ctx.thread.id, exc)
            gate.mark_failed(result.message_id)
        ctx.thread.last_message_at = now_utc
        self.db.commit()
        reset_unanswered_alert(self.db, ctx.thread.id)
        self.db.commit()
        return wa_message_id

    def _send_flow_button(
        self, ctx: _Context, body_text: str, flow_token: str, flow_id: str = "",
        initial_screen: str = "MAIN",
    ) -> str:
        if not flow_id:
            flow_id = (self.settings.whatsapp_flow_id or "").strip()
        if not flow_id:
            raise RuntimeError("WHATSAPP_FLOW_ID not configured")

        from zoneinfo import ZoneInfo
        now_utc = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
        wa_id = ctx.contact.wa_id

        # Safety gate: pre-send audit + duplicate/flood checks.
        gate = OutboundSafetyGate(self.db)
        result = gate.attempt(wa_id=wa_id, thread_id=ctx.thread.id, text=body_text, message_type="flow", now=now_utc)
        if result.outcome != GateOutcome.ALLOWED:
            raise OutboundBlockedError(
                sender_path="engine._send_flow_button",
                kind="flow",
                to_wa_id=wa_id,
                thread_id=ctx.thread.id,
                gate_outcome=result.outcome.value,
            )

        try:
            wa_message_id, _ = _send_whatsapp_cloud_flow(
                to_wa_id=wa_id,
                flow_id=flow_id,
                flow_token=flow_token,
                body_text=body_text,
                cta_label="Completar datos",
                initial_screen=initial_screen,
            )
            gate.mark_sent(result.message_id, wa_message_id)
        except Exception:
            gate.mark_failed(result.message_id)
            raise  # caller can revert state on flow-send failure

        ctx.thread.last_message_at = now_utc
        self.db.commit()
        reset_unanswered_alert(self.db, ctx.thread.id)
        self.db.commit()
        return wa_message_id

    # ── Fallback human-review alert ───────────────────────────────────────

    def _send_fallback_human_review_notification(
        self,
        ctx: "_Context",
        state: "WhatsAppThreadState",
        reason: str,
    ) -> None:
        """Fire a Resend internal alert when a fallback Flow submit needs human review."""
        from ..services.resend_email import send_human_review_notification

        settings = self.settings
        if not settings.resend_api_key:
            logger.warning(
                "M18 fallback human review — RESEND_API_KEY not set, skipping notification"
            )
            return
        to_email = (getattr(settings, "internal_booking_email_to", "") or "").strip()
        from_email = (
            getattr(settings, "internal_booking_email_from", "")
            or "notificaciones@ridecheck.ar"
        ).strip()
        reply_to_email = (getattr(settings, "internal_booking_email_reply_to", "") or "").strip()
        if not to_email:
            return
        lead = ctx.lead
        focus = self._focus_candidate(ctx)
        vehicle_parts: list[str] = []
        tipo_str = ""
        if focus:
            if focus.marca:
                vehicle_parts.append(focus.marca)
            if focus.modelo:
                vehicle_parts.append(focus.modelo)
            tipo_str = focus.tipo_vehiculo or ""
        vehicle_str = " ".join(vehicle_parts).strip() or tipo_str

        try:
            send_human_review_notification(
                api_key=settings.resend_api_key,
                from_email=from_email,
                to_email=to_email,
                reply_to_email=reply_to_email,
                lead_id=lead.id if lead else ctx.thread.lead_id or "?",
                thread_id=ctx.thread.id,
                wa_id=ctx.contact.wa_id,
                vehicle=vehicle_str,
                zone_group=state.home_zone_group or "",
                zone_detail=state.home_zone_detail or "",
                reason=reason,
            )
        except Exception as exc:
            logger.error(
                "M18 fallback human review notification failed thread_id=%s: %s",
                ctx.thread.id, exc,
            )

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
            reply_to_email=settings.internal_booking_email_reply_to,
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
