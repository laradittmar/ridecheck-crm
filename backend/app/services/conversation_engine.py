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
from types import SimpleNamespace
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
    ACTION_BOOKING_REPLAY,
    ACTION_NO_REPLY_PRODUCED,
    ACTION_THREAD_CONTENDED,
    HANDLED_ACTIONS,
    ConversationHandleIn,
    ConversationHandleOut,
)
from ..schemas.hybrid_trace import DecisionSite
# W1F-W-01 R3: locality text — the clause patterns, the locative-context rule, the coverage
# list and the three locality validators. A LEAF module (it imports nothing from this
# package), so `field_evidence` binds the SAME objects instead of replicating them.
from . import locality_text as _loc
from .response_composition import (LiveResponsePlan, Proposition, PropositionId,
                                   SuppressionReason, flow_completion_ack_authorized)
from ..schemas.hybrid_trace import (CoexistencePolicy, FLOW_COMPLETION_ACK_CONDITIONS,
                                    ResponseItemKind, ResponsePolicy)
# G3-5E: the attribution vocabulary. `schemas.hybrid_trace` imports nothing from services,
# so this is a module-level import rather than the file's usual local-import style.
from ..schemas.hybrid_trace import (
    DecisionAuthority, DispatchPath, DispatchResult, FactSource, RendererSource,
)

# G3-1 trace prose for the semantic handoff site. Hoisted out of
# `_semantic_handoff_requested` on purpose: F7C-23 forbids multi-word string literals
# inside that function, because that is where a phrase lexicon would first appear. These
# are trace labels — they are never compared against customer text — and keeping them out
# here means the guard can stay exactly as strict as it was.
_G3_HANDOFF_REASON_YES = "affirmative current request for a person"
_G3_HANDOFF_REASON_NO = "no resolved affirmative handoff claim in this burst"
_G3_HANDOFF_OUTCOME_YES = "scheduling escalation to a person"
_G3_HANDOFF_OUTCOME_NO = "no escalation from semantic evidence"
_G3_HANDOFF_RULE_ID = "handoff.semantic_request"
_G3_HANDOFF_ACTION = "human_escalation"
_G3_RULE_VERSION = "v1"
from ..schemas.schedule import ScheduleCheckIn
from ..services.pricing import PricingNotFoundError, PricingQuote, PricingService
from ..services.schedule import NEXT_AVAILABLE_HORIZON_DAYS, ScheduleService
from ..services.unanswered_alert import reset_unanswered_alert
from ..services.vehicle_catalog import (
    VehicleMatch, lookup_vehicle, fuzzy_lookup_vehicle, FuzzyLookupResult,
    _contextual_numeric_model_lookup, extract_model_del_year,
)
from ..services.response_validator import CanonicalFacts, validate_response
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
from ..services.outbound_path_registry import OutboundPathId
from ..services.outbound_safety_gate import GateOutcome, OutboundSafetyGate
from ..settings import Settings
from ..ui.whatsapp_ui import (
    FLOW_MODE_DATA_EXCHANGE,
    FLOW_MODE_NAVIGATE,
    MetaSendError,
    _send_whatsapp_cloud_flow,
    _send_whatsapp_cloud_text,
)

logger = logging.getLogger(__name__)

_ALLOWED_FLAGS = {"PRESUPUESTANDO", "PRESUPUESTO_ENVIADO", "ACEPTADO"}

STAGE_QUALIFYING = "QUALIFYING"
STAGE_QUOTED = "QUOTED"
STAGE_SCHEDULING = "SCHEDULING"
STAGE_FLOW_SENT = "FLOW_SENT"
STAGE_BOOKED = "BOOKED"
STAGE_HUMAN = "HUMAN_REQUIRED"

# L4.7W5-F7B: the canonical set lives in claim_projection, with the acceptance producer
# that uses it. Imported back here so `_is_acceptance` and `_has_acceptance_word` keep
# working unchanged — one definition, no second copy to drift.
from .acceptance_lexicon import (ACCEPTANCE_KEYWORDS as _ACCEPTANCE_KEYWORDS,
                                 may_be_acceptance as _may_be_acceptance)


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


def _has_acceptance_word(texts: list[str]) -> bool:
    """True when any message contains at least one acceptance keyword as a standalone word.
    Weaker than _is_acceptance (which requires ALL words to be acceptance keywords).
    Used for Layer D suppression in QUOTED stage where "Dale, hagamos ese." must
    prevent FAQ-only routing even though "hagamos" and "ese" are not in the keyword set.
    """
    for m in texts:
        words = set(re.sub(r"[^\w\s]", " ", _norm_lower(m)).split())
        if words & _ACCEPTANCE_KEYWORDS:
            return True
    return False


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

# Motorcycle/quad/UTV manual handoff — no form involved; must not reference
# form completion.  Distinct from _FALLBACK_WARM_HANDOFF which follows an
# actual Flow submission.  Used as fallback when WHATSAPP_FLOW_ID (CLIENT DATA
# FLOW) is not configured.
_MOTORCYCLE_HANDOFF_REPLY = (
    "Las motos las revisamos de forma manual. "
    "Un asesor de Ridecheck se va a contactar con vos para continuar."
)

# M21.2 Motorcycle contact-data Flow — Turn 1 intro text (body of the Flow button).
# Sent when WHATSAPP_FLOW_ID (CLIENT DATA FLOW) is configured.
_MOTORCYCLE_CONTACT_INTRO = (
    "Perfecto, para avanzar necesito que completes estos datos."
)

# M21.2 Motorcycle contact-data Flow — reply after successful Flow submission.
_MOTORCYCLE_ADVISOR_REPLY = (
    "A la brevedad uno de nuestros asesores se estará contactando con vos."
)

# Prefix for flow_booking_token when a motorcycle manual-handoff contact Flow is pending.
# _process_flow_response uses this prefix to route to _process_motorcycle_contact_response
# BEFORE the booking-flow handler.  Must not clash with booking tokens (no prefix there).
_MOTO_HANDOFF_TOKEN_PREFIX = "moto-handoff-"


def _flow_routing_token(submitted_token, mirror_token) -> str:
    """The token a Flow response is ROUTED by: the one the customer's submission carries.

    PRE-WILD PW1-A (R3RV-01). Meta echoes the `flow_token` this engine sent with the Flow, so
    the submission names its own family — a `moto-handoff-` contact form or a booking offer.
    The thread-state mirror is only a copy, and a copy the cycle reset clears
    (`_execute_cycle_reset` → `OfferLifecycle.withdraw` → `_clear_mirror`) between the
    booking-shaped predicate and `_process_flow_response`: routing on it sent a motorcycle
    contact submission to booking admission and lost the human handoff. The mirror is read
    only when the delivery carries no token at all, and then as it stood BEFORE the reset
    (`_handle` takes it at turn entry). A stale mirror can therefore never turn a submitted
    booking token into a motorcycle handoff, nor the reverse.
    """
    submitted = (submitted_token or "").strip()
    return submitted if submitted else (mirror_token or "")


# Candidate type-safety: tipo_vehiculo values that represent motorcycles.
# A MOTO candidate must never be repurposed as an automotive candidate and vice versa.
_MOTO_TIPO_VALUES = frozenset({"MOTO", "MOTOCICLETA"})


def _tipo_compatible(existing: "str | None", new: "str | None") -> bool:
    """Return True when two tipo_vehiculo values belong to the same logical type group.

    An existing MOTO candidate must not be updated with AUTO data, and vice versa.
    A null/empty tipo on either side is always considered compatible (no conflict).
    """
    if not existing or not new:
        return True
    return (existing.upper() in _MOTO_TIPO_VALUES) == (new.upper() in _MOTO_TIPO_VALUES)


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
    # B5-01-A: imperfect-past inspection intent — guards Layer D bypass and Layer F Step 2.
    # Constrained to inspection/revision vocabulary; generic "quería…" not included.
    "quería revisar", "queria revisar",
    "quería hacer una revisión", "queria hacer una revision",
    "quería una inspección", "queria una inspeccion",
    "quería cotizar", "queria cotizar",
    "quería que revisaran", "queria que revisaran",
})

# FAQ / greeting / informational patterns (applied to accent-normalized text).
# B5-01-B: Added service-composition family ("de qué consta", "en qué consiste") and
# payment-method family ("se puede pagar", "medios de pago", "aceptan débito", etc.).
_FAQ_PATTERNS = [
    # Service content
    r'\bqu[eé]\s+incluye\b',
    r'\bqu[eé]\s+cubre\b',
    r'\bqu[eé]\s+revisan\b',
    r'\bqu[eé]\s+verifican\b',
    r'\bqu[eé]\s+(hacen|miran|chequean)\s+en\b',
    r'\bde\s+qu[eé]\s+consta\b',
    r'\ben\s+qu[eé]\s+consiste\b',
    r'\bcu[aá]nto\s+(tarda|demora|dura|tiempo)\b',
    r'\btengo\s+que\s+estar\s+presente\b',
    r'\btrabajan?\s+(los\s+)?(s[aá]bados?|domingos?|fin\s+de\s+semana)\b',
    r'\bc[oó]mo\s+se\s+entrega\b',
    r'\bqu[eé]\s+es\s+ridecheck\b',
    r'\bc[oó]mo\s+funciona\b',
    r'\bd[oó]nde\s+atienden\b',
    r'\bsalen?\s+a\s+domicilio\b',
    r'\bvan\s+al\s+lugar\b',
    r'\bcosto\s+del\s+servicio\b',
    # Payment method
    r'\bc[oó]mo\s+se\s+paga\b',
    r'\bmedios?\s+de\s+pago\b',
    r'\bformas?\s+de\s+pago\b',
    r'\bse\s+puede\s+pagar\b',
    r'\bpuedo\s+pagar\b',
    r'\baceptan\s+(d[eé]bito|cr[eé]dito|efectivo|tarjeta|transferencia|mercado\s+pago)\b',
    r'\bpagar\s+(con|en)\s+(d[eé]bito|cr[eé]dito|efectivo|transferencia)\b',
    r'\bmercado\s+pago\b',
    # Greetings / opening queries
    r'^(hola|buenas?|buen\s+d[ií]a|buenos\s+d[ií]as|buenas\s+tardes|buenas\s+noches)[,\s!.]*$',
    r'^(hola[,\s]+)?quer[ií]a\s+(hacer|hacer\s+una)\s+consulta',
    r'\bconsulta\b',
]

# M21.1.1-R1: Inspection request patterns for Layer F pass-through.
# Applied to accent-normalized, lowercase text.
# B5-01-A: "queria" (imperfect past, accent-normalized form of "quería") added alongside
# quiero/quisiera/necesito — the most common Argentine opening for soft inspection intent.
# Constrained to inspection/revision/inspección vocabulary; generic "quería + other verb"
# is NOT matched (e.g. "quería consultar" or "quería saber" do not trigger).
_INSPECTION_REQUEST_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r'\b(quiero|queria|necesito|quisiera)\s+que\s+(?:me\s+|les?\s+)?revis\w+\b', re.IGNORECASE),
    re.compile(r'\b(quiero|queria|necesito|quisiera)\s+revis[ae]r\s+(?:un[ao]?|el|la|este|ese)\b', re.IGNORECASE),
    re.compile(r'\b(quiero|queria|necesito|quisiera)\s+(?:hacer\s+(?:una\s+)?)?revisi[oó]n\b', re.IGNORECASE),
    re.compile(r'\b(quiero|queria|necesito|quisiera)\s+(?:una|la)\s+(?:inspecci[oó]n|revisi[oó]n)\b', re.IGNORECASE),
    re.compile(r'\bcoordinar\s+(?:una|la)\s+revisi[oó]n\b', re.IGNORECASE),
    re.compile(r'\bme\s+inter[eé]sa\s+coordinar\b', re.IGNORECASE),
    # M21.1.1-R2: "quiero/necesito cotizar" signals inspection pricing intent
    re.compile(r'\b(quiero|queria|necesito|quisiera)\s+cotizar\b', re.IGNORECASE),
    # WILD-03-I: bare imperative/infinitive as sentence opener ("Revisar un auto...", "Revisar una 2008...")
    # Modal-less opening "Revisar ..." is common in Argentine WhatsApp; prior patterns required modal prefix.
    # Restricted to indefinite article "un/una/uno" to avoid false positives on "Revisar el precio" etc.
    # Anchored to sentence start (^ or \n) to avoid matching embedded "revisar" in other contexts.
    re.compile(r'(?:^|\n)\s*revis[ae]r\s+un[ao]?\s+\w', re.IGNORECASE),
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

# M21.2: Layer F deferred-interest pass-through patterns (step 8.5).
# Applied to raw current_turn_text (case-insensitive). These capture the semantic
# category "not ready yet / will let you know" WITHOUT listing literal client phrases.
# Steps 1–4 already fire for messages with an active vehicle or explicit inspection
# request, so this step only applies to messages with no commercial signal.
_DEFERRED_INTEREST_PATTERNS: tuple[re.Pattern, ...] = (
    # Notification intent — any person: "aviso", "te aviso", "les aviso", "aviso cuando"
    re.compile(r'\baviso\b', re.IGNORECASE),
    # Temporal deferral: "cuando tenga/encuentre/decida/consiga/vea"
    re.compile(r'\bcuando\s+(tenga|encuentre|decida|consiga|vea)\b', re.IGNORECASE),
    # Active search without present vehicle: "estoy buscando"
    re.compile(r'\bestoy\s+buscando\b', re.IGNORECASE),
    # Explicit not-yet state: "todavía no tengo" / "todavia no tengo"
    re.compile(r'\btodav[ií]a\s+no\b', re.IGNORECASE),
    # "por ahora estoy" (deferred-search framing)
    re.compile(r'\bpor\s+ahora\s+estoy\b', re.IGNORECASE),
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
def _coerce_candidate_year(value) -> "int | None":
    """A plausible model year, or None. Never raises, never guesses.

    G3-7B. Used to read a corrected year off `CorrectionEvidence.to_value`, which the
    interpreter may deliver as an int or as prose-free text ("2018").
    """
    try:
        year = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return year if 1900 <= year <= date.today().year + 1 else None


# ── W1F-W-01 R3: these names are now BINDINGS, not definitions ───────────────
# The patterns, the origin language, the uncertainty markers and the clause splitter live
# in `locality_text`, which `field_evidence` imports too. The module-level names are kept
# because the existing suites reference them; they are the SAME objects the other consumer
# holds, so the two copies cannot diverge any more (finding W1FRV-07).
#
# Each pattern captures a locality (1–4 words); the LAST is the alt-location contradiction
# signal. `_VEHICLE_LOCATION_CLAUSE_PATTERNS` stays the inherited SINGULAR set: the plural
# clauses (R3 item 4) are selected per call through `_loc.clause_patterns(plural=...)`, so
# they appear only when `LOCALITY_RESOLUTION_SAFETY_ENABLED` is on.
_VEHICLE_LOCATION_CLAUSE_PATTERNS: list[re.Pattern[str]] = _loc.VEHICLE_LOCATION_CLAUSE_PATTERNS

# Customer-origin language. These phrases describe where the CUSTOMER is,
# not where the vehicle is for inspection (LR-2).
_CUSTOMER_ORIGIN_RE = _loc.CUSTOMER_ORIGIN_RE

# Uncertainty markers that, combined with two distinct vehicle-location zones,
# signal a same-turn contradiction (SC17 / LR-8).
_LOCATION_UNCERTAINTY_RE = _loc.LOCATION_UNCERTAINTY_RE

_has_customer_origin_clause = _loc.has_customer_origin_clause

# ── L4.6 Phase C: isolate customer-origin clauses ────────────────────────────
# "Está en Berazategui, pero yo soy de Tigre." carries BOTH an inspection location
# and a customer origin.  The origin clause must suppress only itself — never the
# inspection location evidence sitting in another clause (L4-WILD-B finding LOC-A).
_ORIGIN_CLAUSE_SPLIT_RE = _loc.ORIGIN_CLAUSE_SPLIT_RE

_strip_customer_origin_clauses = _loc.strip_customer_origin_clauses



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
# L4.7W2-F1: an approximate locality is confirmed, never assumed.
_LOCALITY_CONFIRMATION_TEMPLATE = "¿El auto está en {zone}?"
#: G3-7F: asked at most once when a correction cannot be placed on exactly one vehicle.
# ── G3-7F-OWNER-RESOLUTION: clarification and handoff wording ────────────────────────
#
# These sentences live here, beside the other outbound bodies and
# `_MOTORCYCLE_HANDOFF_REPLY`, because G3-5F's architecture is explicit that the response
# plan "holds no wording of its own" — a static test enforces it — and that every
# proposition's `render` callable is supplied by the component that owns the fact. They are
# module constants, not reconciler code: no decision function contains prose (Part E), and
# the plan still decides whether and where they appear.
#
# Owner policy 16: these are conformance examples of intent. Changing the wording is a
# copy decision, not an architectural one.
#: THE canonical Vehicle Flow introduction. Owner-approved, exact, and the only one.
#: It IS the customer message: a Flow button carries its body in the same message, so a
#: separate introductory text would be a duplicate response.
_VEHICLE_CLARIFICATION_INTRO = (
    "Para identificar correctamente el vehículo y cotizar la revisión, te dejo un "
    "formulario breve para completar la marca, el modelo y el año."
)
#: THE canonical Location Flow introduction. Owner-approved, exact, and the only one.
#: The closing sentence is a PRODUCT COVERAGE STATEMENT — the service area the customer is
#: being asked to locate the vehicle within. It is not a lexical routing trigger and nothing
#: matches on it: coverage routing is `_is_outside_coverage`, which is a separate authority.
#: The retired two-step carried this signal in a free-text step; it now lives in the body,
#: so a customer in Greater Buenos Aires is still told they are covered.
_LOCATION_CLARIFICATION_INTRO = (
    "Para calcular correctamente los viáticos de la revisión, te dejo un formulario "
    "breve para indicarnos dónde se encuentra el vehículo. Realizamos revisiones en "
    "CABA y GBA."
)
#: Neutral, professional, and silent about vehicle category. Owner policy 19: an ordinary
#: car, a catalogue miss, an unknown vehicle and an ambiguous correction must never be told
#: they are a motorcycle, so this says only that a person is taking over — the one thing
#: that is true in every case that reaches it.
_GENERIC_HANDOFF_REPLY = (
    "Para no equivocarme con tu caso, lo sigue un asesor de RideCheck. "
    "Te contactamos a la brevedad."
)

# ── G3-7F-OWNER-RESOLUTION: generic human-handoff reasons ─────────────────────────────
#
# Every one of these used to reach `_send_fallback_human_review_notification` as
# `reason="motorcycle_enquiry"`, so the operator dashboard recorded a motorcycle case for an
# ordinary car (review finding R2R-02). They are enumerated so a reason can never be
# borrowed from an unrelated product area again.
_HANDOFF_CATEGORY_UNRESOLVED = "vehicle_category_unresolved"
_HANDOFF_CORRECTION_UNRESOLVED = "correction_unresolved_after_flow"
_HANDOFF_RESPONSE_COMPOSITION_FAILED = "response_composition_failed"
_HANDOFF_VEHICLE_FLOW_UNAVAILABLE = "vehicle_clarification_flow_unavailable"
_HANDOFF_LOCATION_FLOW_UNAVAILABLE = "location_clarification_flow_unavailable"
_HANDOFF_FLOW_DISPATCH_FAILED = "clarification_flow_dispatch_failed"
#: SEQ-02. The attempt store could not answer. We do not know whether this customer has
#: already been asked, so nothing is sent and a person takes the turn.
_HANDOFF_CLARIFICATION_STORE_UNAVAILABLE = "clarification_store_unavailable"
#: The family's one attempt for this cycle is already spent and nothing is outstanding.
_HANDOFF_CLARIFICATION_ATTEMPT_SPENT = "clarification_attempt_spent"

#: Route literal, so the handler does not import the router module on every turn just
#: to compare one string. Kept in sync by `test_seq_route_constants_match_router`.
_CR_LOCATION_FLOW_REQUIRED = "LOCATION_FLOW_REQUIRED"

_CORRECTION_TARGET_CLARIFICATION = (
    "Perdón, para no equivocarme: ¿sobre qué vehículo es la corrección?"
)
_LOCALITY_ASK_AGAIN_REPLY = (
    "Perdón, no te entendí bien. ¿En qué localidad está el auto?"
)

_FUZZY_ASK_VEHICLE_REPLY = (
    "Para cotizarte la revisión necesito saber qué vehículo tenés. "
    "¿Me podés indicar la marca y el modelo?"
)


# Persisted intent token (22 chars, fits String(30) in WhatsAppThreadState.last_intent).
_INTENT_PREPURCHASE = "PREPURCHASE_INSPECTION"

# Persisted token written after UNCERTAIN clarification is sent (WILD-02-A).
# Signals that the next turn is expected to be a vehicle-qualification answer.
_AWAITING_QUALIFICATION = "AWAITING_QUALIFICATION"

# ── M20.6D.2B Routing gate constants ─────────────────────────────────────────

# Clearly out-of-coverage cities/provinces. Accent-stripped lowercase forms;
# matched against _norm_lower(message). GBA localities are NOT listed here.
# W1F-W-01 R3: the set now lives in `locality_text` and is bound here. Unchanged content;
# what changed is WHEN it is consulted — before a zone is written, not only afterwards in
# the routing gate (finding W1FRV-03).
_OUTSIDE_COVERAGE_CITIES: frozenset[str] = _loc.OUTSIDE_COVERAGE_CITIES

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
# G3-7F-R2 owner decision: there is no longer a standard body, a concern-aware body, a
# generic body or a garbled body. `_LOC_FLOW_BODY_CONCERN`, `_LOC_FLOW_BODY_STANDARD`,
# `_VEH_FLOW_BODY_GENERIC` and `_VEH_FLOW_BODY_GARBLED` are DELETED rather than left
# unreachable, so no configuration, caller or future edit can reach a second wording.
# One introduction per family, above.
# Out-of-coverage deterministic response (exact copy)
_COVERAGE_RESPONSE = (
    "Por el momento trabajamos en CABA y GBA.\n\n"
    "Para una revisión fuera de esa zona tenemos que evaluar la logística. "
    "Te confirmamos si podemos hacerlo."
)

# Accent-stripped lowercase phrases that signal a service FAQ or soft conversation close.
# Matched as substrings of _norm_lower(message). Conservative set — do not add price
# or concern words here; those are handled by _wants_price_quote / _has_vehicle_concern.
# B5-01-B: Added service-composition and payment-method phrases.
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
    # B5-01-B: Service-composition family
    "de que consta",
    "en que consiste",
    "que hacen en",
    "que miran en",
    "que chequean",
    # WILD-04-F1: Report and presence FAQ patterns.
    # "mandan informe" covers "¿Mandan informes?" — report delivery question.
    # Presence variants cover "¿Tengo/Hay que estar presente?".
    "mandan informe",
    "tengo que estar presente",
    "hay que estar presente",
    "necesito estar presente",
    # B5-01-B: Payment-method family
    "como se paga",
    "medios de pago",
    "formas de pago",
    "se puede pagar",
    "puedo pagar",
    "aceptan debito",
    "pagar con debito",
    "pagar en debito",
    "pagar con credito",
    "aceptan efectivo",
    "pagar en efectivo",
    "aceptan transferencia",
    "mercado pago",
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

# WILD-04R-F2: FAQ detection phrases for pricing+FAQ composition.
# Used exclusively by _build_faq_supplement when pricing fires in a multi-signal burst.
_HOURS_FAQ_DETECTION: frozenset[str] = frozenset({
    "horarios", "horario", "en que horarios", "que horarios",
    "cuando laburan", "cuando trabajan", "a que hora",
    "horario de atencion",
})
# ── L4.7W5-GATE3-G3-4: the hours floor is evidence, not authority ────────────
#
# G3-2 DEF-03: this set won outright on a bare substring, so "horarios" — which is equally
# the vocabulary of asking about the AGENDA — selected the opening-hours answer, and no
# decision was recorded anywhere. The set is NOT enlarged here and not one entry is added.
# It is PARTITIONED by what the phrase names:
#
#   STRONG  the phrase names the business's own activity or its attention hours, so the
#           customer is asking when the business operates;
#   WEAK    the phrase is a bare time noun or a bare "at what time", which an Argentine
#           customer uses just as readily to ask what the calendar has free.
#
# The partition is a rule over the existing members, derived at import, so a future entry
# is classified by the same rule rather than by being added to a second list.
_HOURS_STRONG_MARKERS: tuple[str, ...] = ("atencion", "trabajan", "laburan")


#: Why the hours candidate lost, keyed by disposition. Named once so the trace, the log and
#: the operator all read the same sentence.
_HOURS_SUPPRESSION_REASON = {
    "SUPPRESSED_BY_SEMANTIC_AVAILABILITY":
        "the interpretation resolved this burst as an appointment-availability enquiry",
    "SUPPRESSED_AMBIGUOUS":
        "the interpretation could not choose between availability and opening hours",
    "SUPPRESSED_UNCORROBORATED":
        "a weak hours candidate with no semantic corroboration (interpretation {status})",
    "SUPPRESSED_BY_DETERMINISTIC_SCHEDULING":
        "the deterministic scheduling parser resolved a competing request ({scheduling}) "
        "and the interpretation did not resolve this proposition (interpretation {status})",
}


@dataclass(frozen=True)
class CanonicalMessage:
    """One customer message, as one atomic record.

    G3-5B (audit G5A-02). Identity and text used to be assembled into two parallel lists
    and deduplicated by DIFFERENT rules: `_turn_trace_rows` kept every persisted row while
    `_missing_burst` compared TEXT, so a customer who sent the same words twice ended with
    three identifiers and two texts — and one real message erased. Identity and text are
    now one record, and every derived view is projected from the same ordered collection,
    so they cannot drift.
    """
    message_id: str            # stable identity — WAMID where one exists
    text: str
    index: int                 # position in the reconstructed turn
    timestamp: str | None      # ISO-8601, where the record carries one; None means UNKNOWN
    source: str                # which reconstruction path contributed this message
    #: G3-5C. How this record's place in the turn was established. `timestamp` is a proven
    #: clock order; `row_order` is a persisted row with no clock; `upstream_position` is a
    #: message only the transport reported, placed between the neighbours it was reported
    #: between. Uncertainty is named here rather than papered over with a fabricated time.
    order_basis: str = "timestamp"


@dataclass(frozen=True)
class DecisionStateSnapshot:
    """The canonical state the authority decision is entitled to read.

    G3-5B (audit G5A-03). `stage` was read live at decision time and the decision was
    memoised, so the stage that decided was whichever the FIRST consumer happened to
    observe — silently. The fields here are exactly the ones the decision consumes, taken
    from executable reads:

        `_availability_authority`   `state.last_stage`
        `_faq_topics_for_burst`     `state.last_stage`, `self._turn_scheduling_requested`

    Nothing else is frozen, so a legitimate same-turn canonical update is not ignored: it
    produces a DIFFERENT snapshot, which opens a new, explicit decision phase instead of
    silently reusing the first memo.
    """
    stage: str | None
    scheduling_requested: bool

    @property
    def digest(self) -> str:
        return _text_digest(f"stage={self.stage!r}|sched={bool(self.scheduling_requested)}")


def _decision_snapshot(state, engine) -> DecisionStateSnapshot:
    """Capture the decision-phase state. Read-only; never mutates."""
    return DecisionStateSnapshot(
        stage=getattr(state, "last_stage", None),
        scheduling_requested=bool(getattr(engine, "_turn_scheduling_requested", False)))


@dataclass(frozen=True)
class CanonicalTurnBurst:
    """The one reconstructed burst a turn's authority decisions are taken on.

    G3-5A (audit F-02). `_availability_authority` was memoised on whichever caller ran
    first, and its two callers pass different text: `_faq_topics_for_burst` passes
    `current_turn_text`, built from `_current_evidence` INCLUDING messages recovered from
    the database, while `_handle_next_available_request` passes `ai_input_messages`, which
    is `event.unanswered_recent_user_messages` when n8n supplies it and can be a strict
    subset. The same customer turn therefore decided differently depending on call order.

    G3-5B (audit G5A-01, G5A-02). The boundary is now UNIFIED rather than merely canonical
    for the deterministic half: `messages` is the single ordered atomic collection, and the
    semantic interpreter is dispatched from it too, so the producer with semantic primacy
    interprets the same turn the reconciler authorizes. `presentation_texts` keeps
    `ai_input_messages` available for reply generation, where it is still the right input;
    it defines neither semantic nor authority evidence.
    """
    thread_id: object
    turn_id: str | None
    messages: tuple                    # tuple[CanonicalMessage] — the single source of truth
    provenance: tuple                  # which reconstruction sources contributed
    presentation_texts: tuple          # ai_input_messages; never an evidence boundary
    requested_day_iso: str | None      # deterministic, from _parse_scheduling_text
    requested_time_str: str | None
    wants_next_available: bool         # deterministic, from _NEXT_AVAILABLE_PATTERNS
    # The same deterministic readers, run over the burst units that do NOT carry the hours
    # question. See `has_deterministic_scheduling`.
    competing_day_iso: str | None
    competing_time_str: str | None
    competing_next_available: bool
    stage_at_build: str | None         # provenance only; the decision reads its own snapshot
    #: G3-5F §4.2 (audit G5E2-02). The identity of the customer turn this burst reconstructs
    #: — the causal inbound WAMID. It is the one turn identity that is BOTH genuine and
    #: stable: Meta issues it once per message, so a redelivery of the same turn reproduces
    #: the same value and idempotency survives, while two different turns never share one.
    #: `turn_id` is deliberately NOT used for this: it is a fresh uuid4 per request, so
    #: folding it in would make the fingerprint unique per delivery and destroy exactly the
    #: determinism the binding depends on. None means no anchor was available — the
    #: fingerprint then rests on thread plus message identities, and says so rather than
    #: inventing one.
    #: G3-5C. Structural evidence from the merge itself. `payload_conflicts` records where one
    #: identity carried two payloads and which source won; `format_variations` counts messages
    #: the transport reported with different formatting than the persisted copy. Digests only.
    payload_conflicts: tuple = ()
    format_variations: int = 0
    turn_anchor_id: str | None = None

    # ── every view below is PROJECTED from `messages`; none is stored separately ──

    @property
    def texts(self) -> tuple:
        return tuple(m.text for m in self.messages)

    @property
    def ordered_message_ids(self) -> tuple:
        return tuple(m.message_id for m in self.messages)

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def fingerprint(self) -> str:
        """Proves WHOSE turn, WHICH turn, and then membership, order and identity.

        G3-5F §4.2 (audit G5E2-02). The digest used to be computed from the message records
        alone. Where those records carry real WAMIDs that is globally unique, but a thread's
        FIRST burst has no persisted rows to key on, so every identity is synthetic and
        derived from content — and two different customers opening with the same words
        produced the SAME fingerprint. The binding that admits a reply, and the plan id,
        both rest on this value, so it must carry conversation identity, not only content.

        Conversation identity comes first (`thread`), then the turn anchor, then each
        record as `id\x1ftext` — so two messages with the same words but different
        identities still hash differently and swapping two messages still changes the
        digest. Every component is deterministic: the same turn reconstructed twice, or
        redelivered, produces the same value. No customer text is stored — only digests.
        """
        from ..schemas.hybrid_trace import normalize_burst
        parts = [f"thread\x1f{self.thread_id if self.thread_id is not None else ''}",
                 f"anchor\x1f{self.turn_anchor_id or ''}"]
        parts += [f"{m.message_id}\x1f{normalize_burst([m.text])}" for m in self.messages]
        return _text_digest("\x1e".join(parts))

    @property
    def joined(self) -> str:
        """Exactly how production joins a burst into one string: with a space."""
        return " ".join(self.texts)

    @property
    def has_deterministic_scheduling(self) -> bool:
        """A COMPETING scheduling proposition the deterministic layer already resolved.

        Competing means: found in a unit of the burst that is not the hours question. A
        weekday inside the hours question belongs to that question — "¿cuándo laburan los
        sábados?" names a day and asks nothing about the calendar — so reading the parser
        over the whole burst would withhold the answer to the very question asked. The unit
        boundary is `_hours_units`, the segmentation R2A already uses, and the readers are
        the ones the engine already trusts. Nothing new interprets language.

        Scoped to THIS burst. Canonical state — an outstanding offer, an accepted quote —
        is deliberately not read here: it is not a proposition the customer advanced this
        turn, and letting it suppress an hours answer would withhold a legitimate reply on
        the strength of something said earlier.
        """
        return bool(self.competing_day_iso or self.competing_time_str
                    or self.competing_next_available)

    @property
    def scheduling_summary(self) -> str:
        parts = []
        if self.competing_day_iso:
            parts.append(f"day={self.competing_day_iso}")
        if self.competing_time_str:
            parts.append(f"time={self.competing_time_str}")
        if self.competing_next_available:
            parts.append("next_available")
        return ",".join(parts) or "none"


# ── L4.7W5-GATE3-G3-5E — the reply-generation role, named and versioned ──────
#
# G3-5D found one configured model (`settings.openai_chat_model`) serving two
# architecturally distinct roles, with the semantic side versioned (`understand/1.19`) and
# the reply side carrying no version at all — so no trace could say which prose contract
# produced a message. The roles are distinguished here by call site, prompt builder and
# consumer, which stays true even when the model name is identical.
#
# `_build_ai_messages` is the ONE prompt builder for both reply call sites, so one version
# covers both; the ROLE is recorded separately per call site.
REPLY_PROMPT_VERSION = "reply/1.0"

#: G3-5F-BOUNDED-CORRECTION. Prefix of the Flow-transaction idempotency key. It is written
#: to `WhatsAppMessage.correlation_id` on the acknowledgement's own outbound row, so "was
#: this transaction acknowledged?" is an exact ledger lookup rather than a search for words.
_FLOW_TXN_KEY_PREFIX = "flow-txn:"


#: G3-7 (audit BC36-A03). What a customer receives when a REQUIRED proposition could not be
#: produced. It promises follow-up and claims nothing: no price, no availability, no vehicle,
#: no booking. It must never read as an answer, and it must never expose an internal error.
_DEGRADED_RESPONSE_FALLBACK = (
    "Disculpá, se me complicó armar la respuesta completa. "
    "Un asesor de RideCheck te sigue en un momento.")


def _format_price_ars(total: int) -> str:
    """The one way this engine writes an amount. Used by the quote copy and by the plan's
    canonical-value equality check, so the two can never disagree about what "the price"
    looks like in a message."""
    return f"${total:,.0f}".replace(",", ".")
REPLY_PROMPT_ID = "conversation_engine._build_ai_messages"


#: G3-5E2 (audit G5E-02). `decision_authority` and `fact_sources` were `None`/empty on five
#: of six real paths, and the shadow plan papered over it with `RECONCILER if decision is not
#: None`. The fix is not another default: every producing handler ALREADY declares itself by
#: assigning `_answer_source` at the point it decides, so the typed attribution is recorded
#: from that same statement. The mapping is source-of-truth, not inference — each row names
#: what that component decided and which producer supplied the facts it used.
_ANSWER_SOURCE_ATTRIBUTION: dict = {
    "DETERMINISTIC_RULE": ("DETERMINISTIC_RULE", ("CANONICAL_STATE",)),
    "PRICING_SERVICE":    ("PRICING_SERVICE", ("PRICING_SERVICE", "CANONICAL_STATE")),
    "SCHEDULING_SERVICE": ("SCHEDULING_SERVICE", ("SCHEDULING_SERVICE", "CANONICAL_STATE")),
    "VEHICLE_RESOLVER":   ("DETERMINISTIC_RULE",
                           ("DETERMINISTIC_EVIDENCE", "CANONICAL_STATE")),
    "LOCATION_RESOLVER":  ("DETERMINISTIC_RULE",
                           ("DETERMINISTIC_EVIDENCE", "CANONICAL_STATE")),
    "FAQ_RULE":           ("DETERMINISTIC_RULE", ("FAQ_CANONICAL",)),
    "HUMAN":              ("HUMAN_HANDOFF_POLICY", ("CANONICAL_STATE",)),
    "FLOW_RESPONSE":      ("FLOW_TRANSACTION", ("FLOW_TRANSACTION",)),
    "CE_AI":              ("LEGACY_REPLY_MODEL_EXTRACTION", ()),
    # A dispatch outcome is NOT an authority and NOT a fact source. Listed explicitly so
    # the mapping refuses it rather than leaving it to a lookup miss.
    "ERROR_FALLBACK":     (None, ()),
}


class ReplyContract:
    """Which output contract the reply model answered under, for this turn.

    G3-7. `LEGACY_WITH_MUTATIONS` is the pre-demotion contract: the decision carries
    `extracted`, `candidate`, `lead_flag` and `needs_human`, and the engine applies them.
    `RENDERER_ONLY` is the demoted contract: those fields are stripped at the provider
    boundary and typed evidence supplies them instead. The trace records which one ran, so
    a turn's attribution says whether the model still held mutation authority.
    """
    LEGACY_WITH_MUTATIONS = "LEGACY_WITH_MUTATIONS"
    RENDERER_ONLY = "RENDERER_ONLY"
    NOT_INVOKED = "NOT_INVOKED"

    ALL = (LEGACY_WITH_MUTATIONS, RENDERER_ONLY, NOT_INVOKED)


class ReplyAdmission:
    """Whether the reply model's output may be consumed at all.

    G3-5E2 (audit G5E-01). G3-5E recorded `_reply_burst` and no code ever read it, so a
    result bound to some other burst — or to none — could still mutate canonical state,
    create a candidate, force a handoff, write prose and dispatch. The semantic side has
    had a fail-closed guard since G3-5C; this is its reply-side counterpart, and it is a
    typed state rather than a boolean so the trace can say WHY a result was refused.
    """
    ADMITTED = "ADMITTED"
    REJECTED_FOREIGN_BURST = "REJECTED_FOREIGN_BURST"
    REJECTED_MISSING_BINDING = "REJECTED_MISSING_BINDING"
    NOT_DISPATCHED = "NOT_DISPATCHED"

    ALL = (ADMITTED, REJECTED_FOREIGN_BURST, REJECTED_MISSING_BINDING, NOT_DISPATCHED)
    REJECTED = (REJECTED_FOREIGN_BURST, REJECTED_MISSING_BINDING)


class ReplyRole:
    """Which reply-generation call site produced the prose."""
    COMMERCIAL_TURN = "COMMERCIAL_TURN"        # `_process_text`, the main AI path
    GENERAL_INFORMATION = "GENERAL_INFORMATION"  # `_handle_general_information_ai`

    ALL = (COMMERCIAL_TURN, GENERAL_INFORMATION)


def _text_digest(normalized: str) -> str:
    """SHA-256 of an already-normalized string. Proves WHICH text without storing it."""
    import hashlib
    return hashlib.sha256((normalized or "").encode("utf-8")).hexdigest()


class CanonicalMessages(tuple):
    """The ordered records, plus anything structural worth recording about the merge.

    A `tuple` subclass so every existing consumer keeps working unchanged; `conflicts` and
    `variations` carry the merge's own evidence for the trace.
    """

    def __new__(cls, records, *, conflicts=(), variations=0):
        self = super().__new__(cls, records)
        self.conflicts = tuple(conflicts)
        self.variations = int(variations)
        return self


def _norm_message(text: str) -> str:
    """The project's own burst normalization, applied to one message.

    G3-5C (audit G5B-01): raw string equality was the last text-matching step in the merge,
    and any formatting difference between the transport's copy and the persisted copy turned
    one real message into two canonical records. Matching uses this instead. It is the SAME
    normalization the fingerprint uses, so identity and digest cannot disagree.
    """
    from ..schemas.hybrid_trace import normalize_burst
    return normalize_burst([text])


def _canonical_messages(*, db_rows=(), event_wamid=None, event_text=None,
                        presentation_texts=(), turn_id=None) -> "CanonicalMessages":
    """Merge every reconstruction source into ONE ordered collection of atomic records.

    Deduplication keys on MESSAGE IDENTITY, never on text. Where no identity exists, the
    transport's copy is matched to a persisted record by NORMALIZED text, one occurrence at
    a time — so a repeated message is matched once and the second copy survives.

        same identity delivered twice                 -> one canonical message
        same message, formatting differs              -> one canonical message (G3-5C)
        different identities with identical text      -> two canonical messages
        persisted NULL text + the triggering event    -> the transcript, keeping the row's
                                                         identity and timestamp (M21.1.1)
        transport copy with no persisted counterpart  -> its own record, identified by
                                                         content and occurrence (G3-5C)
        the triggering event matching such a record   -> that record's identity is UPGRADED
                                                         to the real WAMID, not duplicated

    IDENTITY PRECEDENCE (G3-5C, audit G5B-03)
        1. the real WAMID, wherever one exists — persisted row or triggering event;
        2. the persisted row's internal database id (`db:<id>`) when it carries no WAMID;
        3. only when neither exists, `n8n:<content digest>:<occurrence>` — no absolute list
           position, so the same message keeps its identity when the transport reports a
           different subset, and two genuinely repeated messages stay distinct.
        No lookup is added: a record with no persisted counterpart has, by definition, none
        in the recovered range, and searching the thread's history by text would reintroduce
        exactly the text matching this milestone removes.

    ORDER (G3-5C, audit G5B-02)
        Persisted rows order by timestamp, tie-breaking on row id. A message only the
        transport reported has no clock, so it is placed at the position the transport
        reported it — between the neighbours it was reported between — rather than being
        pushed behind every timestamped record. No timestamp is invented; `order_basis`
        records which kind of evidence placed each record.
    """
    persisted: list = []        # [message_id, text, ts_iso, basis]
    seen_ids: set = set()
    conflicts: list = []
    variations = 0

    # ── 1. the persisted rows: authoritative identity, text and clock ──
    ordered_rows = sorted(
        list(db_rows or ()),
        key=lambda r: ((getattr(r, "timestamp", None).isoformat()
                        if getattr(r, "timestamp", None) else ""),
                       getattr(r, "id", 0) or 0))
    for r in ordered_rows:
        wamid = getattr(r, "wa_message_id", None)
        row_id = getattr(r, "id", None)
        identity = wamid or f"db:{row_id}"
        text = getattr(r, "text", None)
        ts = getattr(r, "timestamp", None)
        ts_iso = ts.isoformat() if ts else None
        if text is None and wamid is not None and wamid == event_wamid:
            text = event_text                        # a transcript the row never stored
        elif (text and event_wamid is not None and wamid == event_wamid
                and isinstance(event_text, str) and event_text
                and _norm_message(text) != _norm_message(event_text)):
            # G3-5C: one identity, two payloads. The persisted text wins — the precedence
            # that already existed — and the conflict is now recorded structurally.
            conflicts.append({
                "identity_tail": str(identity)[-6:],
                "selected": "persisted",
                "discarded": "event_payload",
                "selected_digest": _text_digest(_norm_message(text))[:12],
                "discarded_digest": _text_digest(_norm_message(event_text))[:12]})
        if not isinstance(text, str) or not text or identity in seen_ids:
            continue
        seen_ids.add(identity)
        persisted.append([identity, text, ts_iso,
                          "timestamp" if ts_iso else "row_order"])

    # ── 2. the transport's copies, matched by NORMALIZED text, one occurrence each ──
    #
    # Two passes, because placement needs to look FORWARD. An unmatched message followed
    # later in the transport's list by a matched one is known to precede that message; one
    # with no following match is placed after the last match it follows. Nothing is
    # anchored by absolute list position and no timestamp is invented.
    available = [(_norm_message(t), k, t) for k, (_i, t, _ts, _b) in enumerate(persisted)]
    plan: list = []             # ("match", persisted_index) | ("new", text)
    for text in (presentation_texts or ()):
        if not isinstance(text, str) or not text:
            continue
        norm = _norm_message(text)
        hit = next((h for h, (n, _k, _t) in enumerate(available) if n == norm), None)
        if hit is not None:
            _n, k, persisted_text = available.pop(hit)
            if text != persisted_text:
                # The same message, reported with different formatting. Counted so the
                # trace can show that normalization did real work; the persisted text wins.
                variations += 1
            plan.append(("match", k))
        else:
            plan.append(("new", text))

    # Each new item's anchor: the persisted index it must precede, or len(persisted).
    anchors: list = []
    for pos, (kind, payload) in enumerate(plan):
        if kind != "new":
            continue
        following = next((p for (k2, p) in (
            (kind2, payload2) for kind2, payload2 in plan[pos + 1:]) if k2 == "match"), None)
        anchors.append((payload, following if following is not None else len(persisted)))

    # ── 3. the triggering message, upgraded onto its own record where possible ──
    trailing_event = None
    if event_wamid is not None and str(event_wamid) not in seen_ids:
        target = _norm_message(event_text) if isinstance(event_text, str) else None
        upgraded_at = None
        if target:
            for h in range(len(anchors) - 1, -1, -1):
                if _norm_message(anchors[h][0]) == target:
                    upgraded_at = h
                    break
        if upgraded_at is None and isinstance(event_text, str) and event_text:
            trailing_event = event_text
    else:
        upgraded_at = None

    # ── 4. emit in order ──
    out: list = []
    new_ids: dict = {}

    def synthetic(text):
        digest = _text_digest(_norm_message(text))[:12]
        n = new_ids.get(digest, 0)
        new_ids[digest] = n + 1
        return f"n8n:{digest}:{n}"

    def emit_new(h):
        text = anchors[h][0]
        identity = (str(event_wamid) if h == upgraded_at else synthetic(text))
        source = ("event_evidence" if h == upgraded_at
                  else "unanswered_recent_user_messages")
        if identity in seen_ids:
            return
        seen_ids.add(identity)
        out.append([identity, text, None, source, "upstream_position"])

    for k in range(len(persisted) + 1):
        for h, (_text, anchor) in enumerate(anchors):
            if anchor == k:
                emit_new(h)
        if k < len(persisted):
            identity, text, ts_iso, basis = persisted[k]
            out.append([identity, text, ts_iso, "db_recovery", basis])
    if trailing_event is not None and str(event_wamid) not in seen_ids:
        seen_ids.add(str(event_wamid))
        out.append([str(event_wamid), trailing_event, None, "event_evidence",
                    "upstream_position"])

    return CanonicalMessages(
        (CanonicalMessage(message_id=mid, text=txt, index=n, timestamp=ts, source=src,
                          order_basis=basis)
         for n, (mid, txt, ts, src, basis) in enumerate(out)),
        conflicts=conflicts, variations=variations)


def _canonical_burst(*, thread_id, turn_id, messages, provenance, presentation_texts,
                     stage_at_build=None, turn_anchor_id=None) -> CanonicalTurnBurst:
    """Build the turn's canonical burst. Deterministic evidence is resolved here, once.

    The date/time parser is the one the engine already trusts for this judgement —
    `_turn_has_scheduling_evidence` reads it the same way. Nothing new interprets language.
    """
    ordered = tuple(m.text for m in messages)
    day_iso = time_str = None
    competing_day = competing_time = None
    competing_next = False
    wants_next = False
    try:
        today = _business_today()
        day_iso, time_str = _parse_scheduling_text(list(ordered), today)
        whole = _norm_lower(" ".join(ordered))
        wants_next = any(re.search(pat, whole) for pat in _NEXT_AVAILABLE_PATTERNS)
        # The units that are NOT the hours question. `_hours_units` is R2A's segmentation
        # and `_HOURS_FAQ_DETECTION` is unchanged; neither is enlarged here.
        rest = [u for u in _hours_units(" ".join(ordered))
                if not any(ph in _norm_lower(u) for ph in _HOURS_FAQ_DETECTION)]
        if rest:
            competing_day, competing_time = _parse_scheduling_text(rest, today)
            n = _norm_lower(" ".join(rest))
            competing_next = any(re.search(pat, n) for pat in _NEXT_AVAILABLE_PATTERNS)
    except Exception:            # a parser failure must never cost a turn
        pass
    return CanonicalTurnBurst(
        thread_id=thread_id, turn_id=turn_id, turn_anchor_id=turn_anchor_id,
        messages=tuple(messages),
        provenance=tuple(provenance or ()),
        presentation_texts=tuple(presentation_texts or ()),
        requested_day_iso=day_iso, requested_time_str=time_str,
        wants_next_available=bool(wants_next),
        competing_day_iso=competing_day, competing_time_str=competing_time,
        competing_next_available=bool(competing_next),
        stage_at_build=stage_at_build,
        payload_conflicts=tuple(getattr(messages, "conflicts", ()) or ()),
        format_variations=int(getattr(messages, "variations", 0) or 0))


def _canonical_burst_from_texts(*, thread_id=None, turn_id=None, texts=(),
                                provenance=("caller_supplied",), presentation_texts=None,
                                stage_at_build=None,
                                turn_anchor_id=None) -> CanonicalTurnBurst:
    """A canonical burst for a caller that has texts but no message identities.

    Used only where no reconstruction ran — an engine that did not go through `handle`.
    Identities are positional and stable, never random.
    """
    msgs = tuple(
        CanonicalMessage(message_id=f"caller:{i}", text=t, index=i,
                         timestamp=None, source="caller_supplied")
        for i, t in enumerate(t for t in (texts or ()) if isinstance(t, str) and t))
    return _canonical_burst(
        thread_id=thread_id, turn_id=turn_id, messages=msgs, provenance=provenance,
        presentation_texts=(presentation_texts if presentation_texts is not None
                            else [m.text for m in msgs]),
        stage_at_build=stage_at_build, turn_anchor_id=turn_anchor_id)


def _hours_units(text: str) -> list[str]:
    """The burst, split into the units a question can live in: lines, then sentences.

    R2A. Strength is a property of the evidence CARRYING the question. Judging the whole
    burst let composition downgrade it: "Al final el Focus se cayó. Tengo un Toyota Corolla
    2020." + "¿Qué horarios tienen?" is an explicit hours question standing beside a vehicle
    correction, and reading the two together turned the question into a vague mention.
    """
    units: list[str] = []
    for line in str(text or "").split("\n"):
        for unit in re.split(r"(?<=[.!?¿])\s+|(?<=[.!?])$", line):
            if unit and unit.strip():
                units.append(unit.strip())
    return units or [str(text or "")]


def _hours_floor_strength(text: str) -> str:
    """NONE | WEAK | STRONG for the deterministic hours floor on this burst.

    STRONG means: some unit of the burst asks about RideCheck's own operating hours, and
    says so compositionally — an hours noun bound to a predicate about the business
    OPERATING (hacen, tienen, trabajan, abren, atención), with nothing in that same unit
    binding the hours to the CALENDAR instead (disponible, libre, cupo). The second half is
    what keeps "qué horarios tienen disponibles" out: the customer is asking what is free,
    not when we open, and that is a different proposition.

    WEAK means an hours noun appears and nothing establishes which question it belongs to.
    A weak floor is evidence, never authority (G3-4-R2).

    Two earlier versions of this were wrong and both are worth remembering. The first graded
    the matched catalogue ENTRIES rather than the customer's words, so a question naming the
    business's activity came out weak. The second graded the whole burst at once, so an
    explicit question was diluted by whatever else the customer said in the same breath.
    """
    from ..schemas.hybrid_trace import FloorStrength
    from .scheduling_lexicon import (AVAILABILITY_OBJECTS, OPERATION_PREDICATES,
                                     SCHEDULING_OBJECTS)
    whole = _norm_lower(text)
    if not any(p in whole for p in _HOURS_FAQ_DETECTION):
        return FloorStrength.NONE
    # R2B (audit R2A-02). The calendar context is read at BURST scope, not inside the graded
    # unit. Judging each unit alone discarded whatever the customer said in the sentence or
    # message next door: "¿Qué horarios tienen?" followed by "Para sacar un turno." was
    # graded an explicit opening-hours question, and the live defect returned intact. One
    # customer, one burst, one proposition — a sentence boundary is not a change of subject.
    # The calendar context is anything that names a vacancy or an appointment object. Both
    # sets already exist; `SCHEDULING_OBJECTS` minus the hours nouns is what remains once the
    # word under dispute is removed from its own evidence — "turno", "fecha", "opción". No
    # vocabulary is added here, and the subtraction is what stops the hours noun matching
    # itself.
    calendar_context = (AVAILABILITY_OBJECTS
                        | (SCHEDULING_OBJECTS - {"horario", "horarios", "hora", "horas"}))
    burst_words = set(re.findall(r"[a-z0-9]+", whole))
    if (burst_words & calendar_context
            or any(o in whole for o in AVAILABILITY_OBJECTS)):
        return FloorStrength.WEAK
    for unit in _hours_units(text):
        n = _norm_lower(unit)
        if not any(p in n for p in _HOURS_FAQ_DETECTION):
            continue
        if set(re.findall(r"[a-z0-9]+", n)) & OPERATION_PREDICATES:
            return FloorStrength.STRONG   # an explicit question, in its own right
    return FloorStrength.WEAK


_REPORT_FAQ_DETECTION: frozenset[str] = frozenset({
    "mandan informe", "manda informe", "mandan informes", "manda informes",
})
# L4.7W5-F1: the literal set held three phrasings, so "¿Te va a estar presente?" — a
# perfectly ordinary way to ask — matched none of them and the topic was never raised.
# Presence is asked from two directions and both mean the same business question:
# does the CUSTOMER need to attend, and does the INSPECTOR go. Detection is by meaning,
# not by a growing list of sentences.
_PRESENCE_FAQ_DETECTION: frozenset[str] = frozenset({
    "tengo que estar presente", "hay que estar presente", "necesito estar presente",
})
_PRESENCE_FAQ_PATTERNS: tuple[str, ...] = (
    # customer-side: must I / do I have to / is it necessary that I be there
    r"\b(tengo|teng[oó]|debo|necesito|hace\s+falta|hay)\s+que\s+(estar|ir|acompa[nñ]ar)\b",
    r"\bes\s+necesario\s+que\s+(est[eé]|vaya|acompa[nñ]e)\b",
    r"\bnecesito\s+estar\b",
    r"\bten[eé]s\s+que\s+estar\b",
    r"\bpuedo\s+no\s+(estar|ir)\b",
    r"\bhace\s+falta\s+que\s+est[eé]\b",
    # inspector-side: do you attend / can the inspector go alone / who does it
    r"\b(va|vas|van|vaya|ir[aá]|ir[aá]n)\s+a\s+estar\s+presente",
    r"\best[aá]n?\s+presentes?\b",
    r"\bpuede\s+ir\s+(el\s+)?(inspector|t[eé]cnico|alguien)\s+solo\b",
    r"\bqui[eé]n\s+(hace|realiza|va\s+a\s+hacer)\s+(la\s+)?(revisi[oó]n|inspecci[oó]n)\b",
    r"\bva\s+alguien\b",
    r"\bmandan\s+(a\s+)?(alguien|un\s+inspector|un\s+t[eé]cnico)\b",
)
_PAYMENT_FAQ_DETECTION: frozenset[str] = frozenset({
    "aceptan debito", "como se paga", "medios de pago", "formas de pago",
    "se puede pagar", "puedo pagar", "pagar con debito", "pagar en debito",
    "aceptan efectivo", "pagar en efectivo", "aceptan transferencia", "mercado pago",
})
# L4.7W5-F1: "Se paga con débito." is phrased as a statement, but it is a claim about OUR
# policy, and the policy is that debit is not accepted. A declarative sentence is not
# self-authorising: the deterministic payment authority answers it exactly as it would
# answer the question form.
_PAYMENT_FAQ_PATTERNS: tuple[str, ...] = (
    r"\b(se\s+paga|pago|abono|pag[aá]s|pagamos)\s+(con|en)\s+"
    r"(d[eé]bito|cr[eé]dito|efectivo|transferencia|mercado\s*pago|tarjeta)\b",
    r"\b(acept[aá]n|toman|reciben)\s+"
    r"(d[eé]bito|cr[eé]dito|efectivo|transferencia|mercado\s*pago|tarjeta)\b",
    r"\bcon\s+(d[eé]bito|cr[eé]dito|tarjeta)\b",
)
# Canonical business answers — authoritative source, not generated by AI.
# L4.3 Phase B: operating hours are DERIVED from ScheduleService's weekday table so
# the FAQ answer and the scheduler can never disagree (L4-WILD-A finding SCHED-E).


def _faq_hours_answer() -> str:
    from .schedule import business_hours_summary_es
    return business_hours_summary_es()
_FAQ_REPORT_ANSWER = "Al finalizar la revisión te enviamos un informe detallado."
_FAQ_PRESENCE_ANSWER = "No es necesario que estés presente durante la inspección."
# L4.7W1-F4: the customer asked "¿Aceptan? ¿Debito?" and the old answer listed what we
# take without answering what was actually asked. Business truth: transfer, Mercado Pago
# and cash; debit is NOT accepted. An answer that omits the "no" is not an answer.
_FAQ_PAYMENT_ANSWER = (
    "Aceptamos efectivo, transferencia bancaria y Mercado Pago. "
    "Con débito no estamos trabajando por el momento."
)
_FAQ_SCOPE_ANSWER = (
    "Vamos hasta donde está el vehículo y hacemos la revisión pre-compra en el lugar. "
    "Al terminar te enviamos el informe con todo lo que encontramos."
)

# ── L4.7W1-F4 — semantic topic → deterministic business answer ────────────────
#
# The semantic interpreter identifies WHAT the customer asked. It never supplies the
# answer: every value below is RideCheck's own business truth, and business truth is
# either a constant here or derived from ScheduleService.
#
# A topic with no authoritative answer is deliberately absent from this table. The
# interpreter can emit `coverage` and `duration`; we do not have canonical answers for
# them, and inventing one is exactly what this layer exists to prevent.
# ── L4.7W5-G3-4-R2: the one bounded clarification ────────────────────────────
#
# Asked when the system genuinely cannot tell the two questions apart: a bare time noun with
# nothing corroborating it. It names the two readings and nothing else — it claims no
# availability, states no hours, books nothing, escalates to nobody and writes no state.
# One constant, deliberately: a second variant would be the beginning of a catalogue.
_AVAILABILITY_VS_HOURS_CLARIFICATION = (
    "¿Querés conocer los turnos disponibles para una revisión o nuestros "
    "horarios de atención?"
)

_FAQ_TOPIC_ANSWERS: dict[str, object] = {
    "business_hours": lambda: _faq_hours_answer(),
    "report": lambda: _FAQ_REPORT_ANSWER,
    "presence": lambda: _FAQ_PRESENCE_ANSWER,
    "payment": lambda: _FAQ_PAYMENT_ANSWER,
    "service_scope": lambda: _FAQ_SCOPE_ANSWER,
}
_FAQ_TOPIC_PROBES: dict[str, str] = {
    "business_hours": "lunes", "report": "informe",
    "presence": "presente", "payment": "efectivo", "service_scope": "revisión pre-compra",
}

# ── L4.7W5-F1: canonical truth outranks generated prose ───────────────────────
#
# The old rule was one substring per topic: if the probe appeared in the composed reply,
# the canonical answer was suppressed as redundant. A substring cannot tell agreement from
# contradiction, and that is precisely how a customer was told "no estaremos presentes"
# (we will not attend) — the probe "presente" matched the very sentence that was wrong, so
# the correct canonical answer was withheld as a duplicate.
#
# Each topic now carries two predicate sets:
#   SATISFIED   — the reply already states this business fact correctly → do not repeat it
#   CONTRADICTS — the reply states it WRONGLY → drop that sentence and emit the canonical one
#
# Priority is absolute: canonical truth > generated prose. A contradiction is never merely
# supplemented, because leaving both would ship a self-contradicting message.
# ── L4.7W5-FAQ-HOURS-TRUTH — operating hours are a FACT, not a phrasing ──────
#
# The live failure: generated prose said "sábados de 9 a 13 hs" while the canonical table
# says 9 to 15, and the guard let it stand. The old satisfaction test asked a STRUCTURAL
# question — does the reply name weekdays and the token "hs"? — and a sentence with the
# wrong numbers answers that question exactly as well as one with the right numbers.
# Structural presence is not factual agreement.
#
# What follows reads the VALUES the prose asserts and compares each one against
# `business_hours_for_weekday`, the same authority the customer-facing answer is generated
# from. Add a day to that table, change a closing time, and this check follows it with no
# edit here.
#
# It is deliberately NOT a catalogue: there is no list of wrong hours, no customer phrasing,
# no sentence exception and no typo variant. It recognises numbers standing next to weekday
# names — the shape of an hours assertion — and everything it decides is decided by
# comparison with canonical values.
#
# It runs ONLY over text this system generated. Customer input never reaches it.

_HOUR_TOKEN = r"(\d{1,2}(?:[.:]\d{2})?)"
_HOUR_UNIT = r"(?:\s*(?:hs?\b|horas?\b))?"
_HOURS_CLOSED_MARKER = re.compile(
    r"\bno\s+(?:trabajamos|abrimos|atendemos)\b|\bcerrad[oa]s?\b|\bcerramos\b")
_HOURS_OPEN_MARKER = re.compile(r"\b(?:trabajamos|abrimos|atendemos)\b")
_HOURS_RE_CACHE: dict = {}


def _es_day_tokens() -> dict[str, int]:
    """Accent-free weekday word -> weekday index, derived from the schedule's own labels."""
    cached = _HOURS_RE_CACHE.get("days")
    if cached is None:
        from .schedule import canonical_weekday_hours
        cached = {}
        for wd, label, _start, _end, _closed in canonical_weekday_hours():
            base = _strip_accents(label).lower()
            cached[base] = wd
            # "sabados" -> also "sabado"; "lunes"/"martes"/"viernes" are untouched.
            if base.endswith("s") and not base.endswith("es"):
                cached[base[:-1]] = wd
        _HOURS_RE_CACHE["days"] = cached
    return cached


def _hours_patterns() -> tuple:
    cached = _HOURS_RE_CACHE.get("patterns")
    if cached is None:
        alt = "|".join(sorted((re.escape(t) for t in _es_day_tokens()),
                              key=len, reverse=True))
        cached = (
            re.compile(rf"\b({alt})\s+a\s+({alt})\s+de\s+{_HOUR_TOKEN}\s+a\s+"
                       rf"{_HOUR_TOKEN}{_HOUR_UNIT}"),
            re.compile(rf"\b({alt})\s+de\s+{_HOUR_TOKEN}\s+a\s+{_HOUR_TOKEN}{_HOUR_UNIT}"),
            re.compile(rf"\b({alt})\b"),
        )
        _HOURS_RE_CACHE["patterns"] = cached
    return cached


def _parse_stated_hour(token: str) -> tuple[int, int]:
    """'9' -> (9, 0); '9.30' -> (9, 30); '14:00' -> (14, 0)."""
    raw = token.replace(":", ".")
    if "." in raw:
        hour, minute = raw.split(".", 1)
        return int(hour), int(minute)
    return int(raw), 0


def _stated_hours_claims(normalized: str) -> tuple[dict, set, set]:
    """What the text ASSERTS about operating hours, with no judgement about truth.

    Returns (day -> (start, end) stated with concrete hours, days stated closed,
    days stated open without hours). A day range carries its hours to every day it spans.
    """
    days = _es_day_tokens()
    range_re, single_re, day_re = _hours_patterns()
    with_hours: dict[int, tuple[tuple[int, int], tuple[int, int]]] = {}

    text = normalized
    consumed: list[tuple[int, int]] = []
    for match in range_re.finditer(text):
        first, last = days[match.group(1)], days[match.group(2)]
        start = _parse_stated_hour(match.group(3))
        end = _parse_stated_hour(match.group(4))
        step = 1 if last >= first else -1
        for wd in range(first, last + step, step):
            with_hours.setdefault(wd, (start, end))
        consumed.append(match.span())
    for begin, finish in reversed(consumed):     # a range must not be re-read as a single day
        text = text[:begin] + " " * (finish - begin) + text[finish:]
    for match in single_re.finditer(text):
        with_hours.setdefault(days[match.group(1)],
                              (_parse_stated_hour(match.group(2)),
                               _parse_stated_hour(match.group(3))))

    stated_closed: set[int] = set()
    stated_open: set[int] = set()
    for clause in re.split(r"[.;,!?]| y | pero | aunque ", normalized):
        named = {days[m.group(1)] for m in day_re.finditer(clause)} - set(with_hours)
        if not named:
            continue
        if _HOURS_CLOSED_MARKER.search(clause):
            stated_closed |= named
        elif _HOURS_OPEN_MARKER.search(clause):
            stated_open |= named
    return with_hours, stated_closed, stated_open


def _hours_truth_violations(normalized_reply: str) -> list[str]:
    """Every operating-hours assertion in the text that canonical truth contradicts."""
    from .schedule import business_hours_for_weekday
    with_hours, stated_closed, stated_open = _stated_hours_claims(normalized_reply)
    violations: list[str] = []
    for wd, (start, end) in sorted(with_hours.items()):
        canon_start, canon_end, closed = business_hours_for_weekday(wd)
        if closed:
            violations.append(f"{wd}:hours_on_closed_day")
        elif (start, end) != ((canon_start.hour, canon_start.minute),
                              (canon_end.hour, canon_end.minute)):
            violations.append(f"{wd}:wrong_hours")
    for wd in sorted(stated_closed):
        if not business_hours_for_weekday(wd)[2]:
            violations.append(f"{wd}:closed_on_open_day")
    for wd in sorted(stated_open):
        if business_hours_for_weekday(wd)[2]:
            violations.append(f"{wd}:open_on_closed_day")
    return violations


def _hours_prose_contradicts(normalized_reply: str) -> bool:
    """True when the prose states any operating-hours value canonical truth denies."""
    return bool(_hours_truth_violations(normalized_reply))


def _hours_prose_satisfies(normalized_reply: str) -> bool:
    """True only when the prose states the canonical hours for EVERY operating day.

    Partial truth is not satisfaction: an answer that omits Saturday leaves the customer
    without a fact they asked for, so the canonical answer is still supplied.
    """
    from .schedule import business_hours_for_weekday
    if _hours_truth_violations(normalized_reply):
        return False
    with_hours, _closed, _open = _stated_hours_claims(normalized_reply)
    return all(wd in with_hours
               for wd in range(7) if not business_hours_for_weekday(wd)[2])


# ── L4.7W5-FAQ-HOURS-TRUTH-R2 — fail CLOSED on unverifiable hours prose ─────
#
# R1 removed only what it RECOGNISED and appended the canonical answer regardless, so a
# phrasing its grammar did not cover survived NEXT TO the correct schedule — two conflicting
# schedules in one message, which is worse than either half. The independent review proved
# five such shapes.
#
# The repair is not more grammar. It is a different decision: while hours correction is
# armed, a response unit that carries BOTH a weekday token AND a clock-like number must
# PROVE itself before it may survive. Two proofs are accepted, and nothing else:
#
#   A  its hours claims parse COMPLETELY — every clock token in the unit is accounted for
#      by a parsed claim — and every claim agrees with canonical_weekday_hours();
#   B  it is appointment evidence: every clock token is a slot this thread actually has in
#      canonical state (`last_visible_slots` / `last_offered_slots`), and the unit asserts
#      no hours that canonical truth denies.
#
# Anything else is removed, whatever it says and however it is worded. Unrecognised is
# treated as unproven, never as innocent. The safety boundary is the weekday-plus-hour
# predicate plus canonical verification — there is no list of phrasings, and adding one
# would be the defect, not the fix.
#
# A unit that mixes a real offered slot with hours canonical truth denies cannot be split
# safely, so it is removed and the offer is REGENERATED deterministically from state, in
# the wording this engine already uses for an offer.

_HOUR_LIKE = re.compile(r"\b(\d{1,2})(?:[.:](\d{2}))?\b")


def _clock_tokens(normalized_unit: str) -> list[tuple[int, int, int]]:
    """(position, hour, minute) for every token in the unit that could be a clock time.

    Deliberately narrow: one or two digits, optionally `.mm` or `:mm`. A model year, a
    price or "250 puntos" is not a clock token and never makes a unit suspicious.
    """
    found: list[tuple[int, int, int]] = []
    for match in _HOUR_LIKE.finditer(normalized_unit):
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        if hour <= 24 and minute < 60:
            found.append((match.start(), hour, minute))
    return found


def _hours_claim_spans(normalized: str) -> list[tuple[int, int]]:
    """Character spans the hours patterns actually consumed — what the parser can vouch for."""
    range_re, single_re, _day_re = _hours_patterns()
    spans: list[tuple[int, int]] = []
    text = normalized
    for match in range_re.finditer(text):
        spans.append(match.span())
    for begin, finish in reversed(spans):
        text = text[:begin] + " " * (finish - begin) + text[finish:]
    for match in single_re.finditer(text):
        spans.append(match.span())
    return spans


def _hours_unit_is_fully_verified(normalized_unit: str) -> bool:
    """Proof A: every clock token belongs to a parsed claim, and every claim is canonical."""
    claims, _closed, _open = _stated_hours_claims(normalized_unit)
    if not claims or _hours_truth_violations(normalized_unit):
        return False
    spans = _hours_claim_spans(normalized_unit)
    return all(any(begin <= position < finish for begin, finish in spans)
               for position, _hour, _minute in _clock_tokens(normalized_unit))


def _protected_slot_minutes(state) -> frozenset[int]:
    """Minutes-past-midnight of the slots this thread really has, from canonical state.

    The canonical slot representation is `last_visible_slots` (what was shown) and
    `last_offered_slots` (what was computed) — both JSON lists of "HH:MM" written by the
    scheduling path. A time is protected because the state holds it, never because a
    sentence looks like an offer.
    """
    minutes: set[int] = set()
    for raw in (getattr(state, "last_visible_slots", None),
                getattr(state, "last_offered_slots", None)):
        if not raw:
            continue
        try:
            values = json.loads(str(raw))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        for value in values or ():
            try:
                hour, minute = str(value).split(":")[:2]
                minutes.add(int(hour) * 60 + int(minute))
            except (ValueError, TypeError):
                continue
    return frozenset(minutes)


#: How a response unit earned its place, so the caller can tell an hours restatement
#: (redundant once the canonical answer is appended) from an appointment offer (never).
_UNIT_NOT_HOURS = "NOT_HOURS"
_UNIT_VERIFIED_HOURS = "VERIFIED_HOURS"      # proof A
_UNIT_PROTECTED_SLOT = "PROTECTED_SLOT"      # proof B
_UNIT_UNPROVEN = "UNPROVEN"


def _hours_unit_survives(unit: str, protected: frozenset[int]) -> tuple[str, tuple[int, ...]]:
    """(disposition, protected slot minutes this unit carried) under armed hours correction."""
    normalized = _norm_lower(unit)
    _range_re, _single_re, day_re = _hours_patterns()
    tokens = _clock_tokens(normalized)
    carried = tuple(sorted({h * 60 + m for _p, h, m in tokens if (h * 60 + m) in protected}))
    # A falsehood is removed whether or not it carries a clock. "Los domingos también
    # trabajamos" names no hour and still claims we open on a day we are closed — the
    # owner policy is that a response may never claim or imply Sunday is open.
    if _hours_truth_violations(normalized):
        return _UNIT_UNPROVEN, carried                    # false: removed, offer regenerated
    if not tokens or not day_re.search(normalized):
        return _UNIT_NOT_HOURS, ()                        # not an hours-shaped unit at all
    if _hours_unit_is_fully_verified(normalized):
        return _UNIT_VERIFIED_HOURS, ()                   # proof A
    if protected and all((h * 60 + m) in protected for _p, h, m in tokens):
        return _UNIT_PROTECTED_SLOT, ()                   # proof B
    return _UNIT_UNPROVEN, carried                        # unproven -> unproven is removed



def _hours_conflict_in(reply: str, protected: frozenset[int]) -> tuple[bool, tuple[int, ...]]:
    """Does this prose state hours it cannot prove — and what appointment times did it carry?

    G3-6 / DEF-04. This is DETECTION, and detection is all that survives. `_hours_unit_
    survives` still judges each unit exactly as it did, but the answer is no longer used to
    rebuild a message with the offending sentences cut out. It is used to decide that the
    whole proposition is unsendable, so the plan can render the canonical answer in its
    place. The rescued minutes let an appointment offer the rejected prose carried be
    re-registered FROM STATE — never recovered from the prose.
    """
    rescued: set[int] = set()
    conflicts = False
    for line in (reply or "").split("\n"):
        for unit in re.split(r"(?<=[.!?])\s+", line):
            if not unit.strip():
                continue
            disposition, carried = _hours_unit_survives(unit, protected)
            if disposition == _UNIT_UNPROVEN:
                conflicts = True
            rescued.update(carried)
    if conflicts:
        # The WHOLE proposition is about to be withheld, so every appointment time the
        # thread really holds must be rescued — not only the ones the offending units
        # carried. A slot named in a unit that would have survived on its own is still
        # lost when the proposition goes, and canonical state proves it, so it is
        # re-registered from state rather than left to disappear with the prose.
        normalized = _norm_lower(reply or "")
        for _p, hour, minute in _clock_tokens(normalized):
            if (hour * 60 + minute) in protected:
                rescued.add(hour * 60 + minute)
    return conflicts, tuple(sorted(rescued))


def _regenerated_offer_es(minutes: tuple[int, ...]) -> str:
    """Re-state slots that a removed unit carried, in the wording this engine already uses."""
    if not minutes:
        return ""
    shown = [f"{m // 60:02d}:{m % 60:02d}" for m in minutes]
    slot_list = ", ".join(shown[:-1]) + f" o {shown[-1]}" if len(shown) >= 2 else shown[0]
    return f"Horarios disponibles: {slot_list}. ¿Alguno te viene bien?"


_FAQ_TOPIC_SATISFIED: dict[str, tuple[str, ...]] = {
    "report": (r"\binforme\b",),
    # Correct presence statements are about the CUSTOMER not needing to attend.
    "presence": (
        r"\bno\s+(es\s+necesario|hace\s+falta)\s+que\s+(est[eé]s|vayas|acompa[nñ]es)\b",
        r"\bno\s+ten[eé]s\s+que\s+estar\b",
        r"\bpod[eé]s\s+no\s+estar\b",
    ),
    "payment": (r"\befectivo\b.*\btransferencia\b", r"\btransferencia\b.*\befectivo\b"),
    # Scope is satisfied by MEANING. The first attempt at this listed the verbs it expected
    # — vamos / nos acercamos / se realiza / la hacemos — and the live Wild answered
    # "Revisamos el auto en el lugar donde está", which matched none of them, so the
    # canonical paragraph was appended on top of prose that had already said it. Enumerating
    # verbs is the same brittleness that caused the original presence defect.
    #
    # The scope fact has exactly two halves: WE GO to wherever the vehicle is, and the
    # inspection happens THERE. Match that shape — any first-person-plural service verb,
    # near any expression of the vehicle's location — instead of a vocabulary list.
    "service_scope": (
        r"\b\w+(?:amos|imos)\b[^.]{0,60}\b(en\s+el\s+lugar|donde\s+est[aáeé]|"
        r"a\s+domicilio|hasta\s+(donde|el\s+lugar))\b",
        r"\b(en\s+el\s+lugar|donde\s+est[aáeé])\b[^.]{0,60}\b(el\s+)?(auto|veh[ií]culo)\b",
        r"\b(el\s+)?(auto|veh[ií]culo)\b[^.]{0,40}\b(en\s+el\s+lugar|donde\s+est[aáeé])\b",
        r"\bvamos\s+hasta\b",
        r"\ba\s+domicilio\b",
    ),
}

_FAQ_TOPIC_CONTRADICTS: dict[str, tuple[str, ...]] = {
    # "no estaremos presentes" / "no vamos a estar" — RideCheck performs the inspection, so
    # any claim that WE are absent is false regardless of how it is phrased.
    "presence": (
        r"\bno\s+(estaremos|estamos|vamos\s+a\s+estar|estar[ií]amos)\s+presentes?\b",
        r"\bno\s+(asistimos|concurrimos|vamos)\b",
        r"\bsin\s+(nuestra\s+)?presencia\b",
        r"\bno\s+hay\s+nadie\s+(de\s+nosotros|nuestro)\b",
    ),
}

# Payment needs clause-level reading, not a pattern. "no se acepta débito" and "aceptamos
# débito" both contain "acepta" and "débito"; only the second is false, and the difference
# is a negation that can sit before the verb, after it, or in a neighbouring clause. A
# lookahead got this exactly backwards and deleted the one correct sentence in the reply.
_UNACCEPTED_METHODS = (r"d[eé]bito", r"cr[eé]dito")
_ACCEPT_VERBS = r"(acept\w*|toma\w*|recib\w*|trabaja\w*\s+con|se\s+puede\s+pagar)"
_NEGATIONS = (r"\bno\b", r"\btampoco\b", r"\bsin\b", r"\bexcepto\b", r"\bsalvo\b")


def _payment_contradicts_policy(normalized_reply: str) -> bool:
    """True when the reply claims we accept a method we do not.

    Read clause by clause: a clause that names debit or credit alongside an acceptance verb
    is false only when nothing negates it first. Commas are deliberately NOT clause breaks —
    "aceptamos efectivo, transferencia y débito" is one claim, and splitting it would
    separate the verb from the method and miss the falsehood entirely.
    """
    for clause in re.split(r"[.;!?]| pero | aunque ", normalized_reply):
        method = next((m for m in (re.search(pat, clause) for pat in _UNACCEPTED_METHODS)
                       if m), None)
        if method is None or not re.search(_ACCEPT_VERBS, clause):
            continue
        # The negation has to come BEFORE the method to negate it. "aceptamos débito sin
        # problema" is a claim, not a rejection; "no se acepta débito" is a rejection.
        if any(re.search(neg, clause[:method.start()]) for neg in _NEGATIONS):
            continue
        return True
    return False


_FAQ_TOPIC_CONTRADICT_FN = {"payment": _payment_contradicts_policy,
                            "business_hours": _hours_prose_contradicts}
#: Topics whose "already stated correctly" test needs VALUES, not a pattern.
#: A predicate here takes precedence over `_FAQ_TOPIC_SATISFIED`.
_FAQ_TOPIC_SATISFIED_FN = {"business_hours": _hours_prose_satisfies}

# F3 duplicate-answer probes — key phrases checked in normalized primary reply.
# If found, the corresponding FAQ was already answered; no supplement appended.
_FAQ_HOURS_PROBE    = "lunes"  # any generated hours answer names the weekdays
_FAQ_REPORT_PROBE   = "informe"
_FAQ_PRESENCE_PROBE = "presente"
_FAQ_PAYMENT_PROBE  = "efectivo"

# M21.4A: canonical acquisition source values
_ACQ_INSTAGRAM    = "INSTAGRAM"
_ACQ_FACEBOOK     = "FACEBOOK"
_ACQ_GOOGLE       = "GOOGLE"
_ACQ_GOOGLE_MAPS  = "GOOGLE_MAPS"
_ACQ_WEBSITE      = "WEBSITE"
_ACQ_REFERRAL     = "REFERRAL"
_ACQ_DIRECT       = "DIRECT"
_ACQ_OTHER        = "OTHER"

# M21.4A: website tracking ref_code → canonical acquisition source
# Values: ga (Google Analytics / Ads), ig (Instagram), fb (Facebook),
#         org (organic), dir (direct), otro (other)
_REF_CODE_SOURCE_MAP: dict[str, str] = {
    "ga":   _ACQ_GOOGLE,
    "ig":   _ACQ_INSTAGRAM,
    "fb":   _ACQ_FACEBOOK,
    "org":  _ACQ_DIRECT,
    "dir":  _ACQ_DIRECT,
    "otro": _ACQ_OTHER,
}


def _ctwa_to_acq_source(source_url: str, source_id: str | None, source_type: str | None) -> str | None:
    """Map Meta CTWA referral fields to canonical acquisition source.

    Deterministic only.  Returns None when evidence is ambiguous.
    Never guesses — UNKNOWN is not returned here; caller keeps acq_source null.
    """
    url = (source_url or "").lower()
    if "instagram.com" in url or "/instagram" in url:
        return _ACQ_INSTAGRAM
    if "facebook.com" in url or "fb.com" in url or "/facebook" in url:
        return _ACQ_FACEBOOK
    # Referral present but network not determinable from URL — non-null evidence exists
    if source_url or source_id:
        return _ACQ_OTHER
    return None


# WILD-04R-F5 D3: tipo_vehiculo alias normalization map.
# SUV/4x4 and its variants are equivalent to SUV_4X4_DEPORTIVO (same price,
# same catalog canonical).  Normalize on every candidate create/update so the
# DB never stores two distinct strings for the same vehicle class.
_TIPO_VEHICULO_ALIAS: dict[str, str] = {
    "SUV/4X4": "SUV_4X4_DEPORTIVO",
    "SUV/4x4": "SUV_4X4_DEPORTIVO",
    "SUV_4X4": "SUV_4X4_DEPORTIVO",
    "4X4": "SUV_4X4_DEPORTIVO",
}


def _normalize_tipo_vehiculo(tipo: str | None) -> str | None:
    """Normalize tipo_vehiculo aliases to canonical form.

    Returns the canonical string (e.g. SUV_4X4_DEPORTIVO for any SUV/4x4
    variant), strips surrounding whitespace, and returns None for empty input.
    """
    if not tipo:
        return tipo
    stripped = tipo.strip()
    return _TIPO_VEHICULO_ALIAS.get(stripped, stripped) or None


# WILD-04R-F5.1 D1: location-question probe set and canonical append text.
# Phrases (after _norm_lower) that indicate the reply already asks for location,
# used to prevent duplicate location questions.
_LOCATION_ASK_PROBES: frozenset[str] = frozenset({
    "que barrio",
    "que zona",
    "que localidad",
    "que ciudad",
    "donde esta",
    "donde se encuentra",
    "barrio o zona",
    "zona o ciudad",
    "localidad o barrio",
    "en que lugar",
    "donde queda",
    "en que barrio",
    "en que zona",
    "en que localidad",
})

# Canonical location question appended by the deterministic finalizer.
_CANONICAL_LOCATION_ASK = "\n\n¿En qué localidad o barrio está el auto?"


def _reply_already_asks_location(reply: str) -> bool:
    """Return True when the reply already contains a location question."""
    n = _norm_lower(reply)
    return any(probe in n for probe in _LOCATION_ASK_PROBES)


def _business_today():
    """Today on the business calendar. One import point, so the contract is checkable."""
    from .schedule import business_today
    return business_today()


def _norm_lower(s: str) -> str:
    """Accent-strip, lowercase, and collapse whitespace — for keyword matching."""
    return " ".join(_strip_accents(s).lower().split())


_is_outside_coverage = _loc.is_outside_coverage


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


# L4.7W5-F7F — negation is a closed grammatical class, not a phrase list. These are the
# particles that carry it in Spanish; nothing here is a sentence or a customer utterance.
_NEGATORS: frozenset[str] = frozenset({"no", "ni", "nunca", "jamas", "tampoco", "nada"})

# Clause boundaries: terminal punctuation, a comma, or the adversative coordinator. A
# negation scopes its own clause, which is why "no me sirve que me llame" and "no me sirve
# ese horario, me llamás?" must be read one clause at a time.
_CLAUSE_BOUNDARY = re.compile(r"[.;!?¡¿]+|,|\bpero\b")


def _clause_is_negated(clause: str) -> bool:
    """True when a negative particle governs this clause."""
    return any(token in _NEGATORS for token in clause.split())


def _is_short_retraction(clause: str) -> bool:
    """A brief negative afterthought — "no", "mejor no" — that takes back what precedes it.

    Deliberately length-bounded: a long clause that happens to contain "no" is a new
    statement, not a retraction. The bound errs toward NOT finding a call request, which is
    the safe direction — a missed call request costs a customer one more message, an
    invented one hands a thread to a human who was never asked for.
    """
    tokens = clause.split()
    return 0 < len(tokens) <= 3 and _clause_is_negated(clause)


def _opens_a_condition(clause: str) -> bool:
    """True when the clause is a conditional protasis — it begins with `si`.

    `si o si` is the Argentine intensifier ("definitely"), the opposite of a condition, so
    it is excluded by shape rather than listed as an exception.
    """
    tokens = clause.split()
    return bool(tokens) and tokens[0] == "si" and tokens[:3] != ["si", "o", "si"]


def _is_phone_call_request(messages: list[str]) -> bool:
    """True when the customer is asking, affirmatively, to be phoned.

    L4.7W5-F7F. This used to join the burst and search it, so "no me sirve QUE ME LLAME
    ahora" matched `que me llame` and became a call request — a refusal read as a request.
    Polarity is now read per clause: a call expression under a negative particle is not a
    request, a request inside a conditional protasis is an offer rather than a request, and
    a short negative afterthought later in the same burst retracts an earlier affirmative.

    No model call, no new pattern for any of the sentences involved: the call patterns are
    untouched and the only addition is grammatical scope.
    """
    normalized = _norm_lower(" ".join(m for m in messages if isinstance(m, str)))
    clauses = [c.strip() for c in _CLAUSE_BOUNDARY.split(normalized) if c.strip()]
    if not clauses:
        return False

    affirmative_at = -1
    for index, clause in enumerate(clauses):
        if any(p.search(clause) for p in _PHONE_CALL_PATTERNS) and not _clause_is_negated(clause):
            affirmative_at = index
    if affirmative_at < 0:
        return False

    # A retraction after the request wins: "llamame. no, mejor no."
    if any(_is_short_retraction(c) for c in clauses[affirmative_at + 1:]):
        return False

    # A request inside a conditional protasis is an offer, not a request: "si hace falta
    # llamame" leaves the decision with us. Position carries it — a clause OPENING with `si`
    # is the protasis, while "llamame si podés" and "llamame cuando puedan" are requests
    # whose timing is merely open. Deliberately NOT `turn_modality`, which treats `cuando`
    # as conditional and would refuse "llamame cuando puedan" — a certified affirmative.
    return not _opens_a_condition(clauses[affirmative_at])


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


def _extract_year_from_text(text: str, exclude_token: str | None = None) -> int | None:
    for m in _VEHICLE_YEAR_RE.finditer(text):
        if exclude_token is not None and m.group(1) == str(exclude_token):
            continue
        return int(m.group(1))
    return None


# ── Scheduling escalation ─────────────────────────────────────────────────────

# Phrases that indicate the customer is insisting on an unavailable slot or
# rejecting all alternatives — triggers human handoff.
# ── L4.7W5-F2: delegated / earliest scheduling intent ─────────────────────────
#
# Every scheduling handler required a customer-named day. During the complete Wild the
# customer said "decime vos cuando pueden" and then "que sea lo antes posible porque me lo
# venden", and the system answered by asking them to name another day — twice — while
# Saturday sat free two days later. Delegating the choice is an ordinary thing for a
# customer to do, and under time pressure it is the likeliest thing.
#
# These express two related meanings: the customer has handed the date choice to us, and/or
# wants the earliest thing available. Either one triggers the same deterministic forward
# search. Neither authorises a booking.
_NEXT_AVAILABLE_PATTERNS: tuple[str, ...] = (
    # delegated choice — "you tell me", "whenever you can/have"
    r"\bdecime\s+(vos\s+)?cu[aá]ndo\b",
    r"\bdec[ií](me|nos)\b[^.]{0,20}\bcu[aá]ndo\b",
    r"\bcuando\s+(puedan|puedas|pueden|tengan|tengas|les\s+quede)\b",
    r"\bcuando\s+(a\s+)?(ustedes|vos)\s+(les|te)\s+(sirva|quede|venga)\b",
    r"\bel(ig[ií]|eg[ií])\w*\s+(vos|ustedes)\b",
    r"\bfij[aá]\w*\s+(vos|ustedes)\b",
    r"\bcomo\s+(a\s+)?(ustedes|vos)\s+les?\s+quede\b",
    # asking what availability EXISTS, without naming a date. "para cuando tenes" is the
    # plainest form of this in Argentine Spanish and matched nothing — the live customer
    # asked it, was answered with the same question back, repeated it, and got the identical
    # sentence again. Built from the shape "when/what + have/is there + (availability)"
    # rather than from the sentences already observed, so the next phrasing is covered too.
    r"\b(para\s+)?cu[aá]ndo\s+(ten[eé]s|tiene[ns]?|hay|pod[eé]s|pueden|puede)\b",
    r"\bqu[eé]\s+(ten[eé]s|tiene[ns]?|hay)\b[^.]{0,24}\b(disponible|libre|turno|lugar|fecha|d[ií]a)\b",
    r"\bqu[eé]\s+disponibilidad\b",
    r"\bcu[aá]ndo\s+(hay|tienen)\s+(turno|lugar|disponibilidad)\b",
    r"\bhay\s+(turno|lugar|disponibilidad)\b",
    r"\bpara\s+cu[aá]ndo\s+(hay|ten[eé]s|tienen)\b",
    # earliest availability
    r"\blo\s+antes\s+(posible|que\s+se\s+pueda)\b",
    r"\bcuanto\s+antes\b",
    r"\b(el\s+)?primer(o|a)?\s+(turno|horario|d[ií]a)\b",
    r"\blo\s+primero\s+(que\s+(haya|tengan|teng[aá]s|ten[eé]s|tiene[ns]?)|disponible)\b",
    r"\bqu[eé]\s+es\s+lo\s+primero\s+disponible\b",
    r"\bcu[aá]ndo\s+tienen\s+algo\b",
    r"\bm[aá]s\s+cercano\b",
    r"\bbusc[aá](me|nos)\b[^.]{0,25}\bantes\b",
    r"\bme\s+sirve\s+(el\s+)?(primer|cualquier)\b",
    r"\bcualquier\s+(horario|d[ií]a)\b",
)

# Urgency is a REASON, not a permission. It changes only the ordering objective — earliest
# valid slot first — and never relaxes travel rules, occupancy, hours or revalidation.
_URGENCY_PATTERNS: tuple[str, ...] = (
    r"\burgente\b", r"\bme\s+lo\s+venden\b", r"\bme\s+lo\s+est[aá]n\s+por\s+vender\b",
    r"\blo\s+necesito\s+(ya|hoy|urgente)\b", r"\bapur\w+\b", r"\bcorriendo\b",
    r"\bpierdo\s+(el|la)\b", r"\bno\s+puedo\s+esperar\b",
)

# Dissatisfaction with what automation could actually offer. Only meaningful AFTER an
# earliest option has been produced — before that it is just urgency.
# L4.7W5-F7G-R2: the set below mixes two speech acts, and the difference decides whether a
# burst with no object can be read as rejecting our offer.
#
#   UNSUITABILITY  asserts that what we offered does not work — "no me sirve", "muy tarde"
#   ALTERNATIVE    asks for something else instead — "¿algo más temprano?", "¿algo antes?"
#
# Both are evidence for the pre-existing earliest-option detector, which is why the union is
# preserved below unchanged. Only the first kind may satisfy the elliptic reading: "no me
# sirve" with its object omitted can only be about the offer, while "¿no tenés algo más
# temprano?" is a request whose correct answer is an earlier slot, not a human handoff. The
# live Wild burst carried both, one message each, and F7G's first cut escalated on either —
# which quietly absorbed the separate F-03 defect and made the rescue rule too broad.
_UNSUITABILITY_PATTERNS: tuple[str, ...] = (
    r"\bes\s+muy\s+tarde\b", r"\bmuy\s+tarde\b",
    r"\bno\s+me\s+sirven?\b", r"\beso\s+no\s+me\s+sirven?\b",
    r"\bma[nñ]ana\s+ya\s+es\s+tarde\b",
)
_ALTERNATIVE_REQUEST_PATTERNS: tuple[str, ...] = (
    r"\bnecesito\s+antes\b",
    r"\bno\s+hay\s+(algo|nada)\s+antes\b", r"\balgo\s+antes\b",
    r"\bm[aá]s\s+temprano\b", r"\bnecesito\s+resolver(lo)?\s+ya\b",
    r"\blo\s+necesito\s+hoy\b",
)
# The union, byte-for-byte the evidence the pre-existing detector has always used.
_EARLIEST_REJECTED_PATTERNS: tuple[str, ...] = (
    _UNSUITABILITY_PATTERNS + _ALTERNATIVE_REQUEST_PATTERNS
)


def _rejection_has_no_complement(clauses: list[str]) -> bool:
    """True when an UNSUITABILITY predicate ends its clause — its object is omitted.

    L4.7W5-F7G, bounded by R2. Ellipsis is detected by position and needs no vocabulary:
    "no me sirve" with nothing after it has left its object unsaid, while "no me sirve el
    precio", "no me sirve la garantía" and "no me sirve que me llame ahora" all state theirs.

    Only `_UNSUITABILITY_PATTERNS` qualify. An alternative request — "¿no tenés algo más
    temprano?" — also ends its clause, but it is not a claim that our offer fails; it asks
    for a different slot, and answering it with a human handoff would both overshoot and
    swallow the separate F-03 defect.
    """
    for clause in clauses:
        for pattern in _UNSUITABILITY_PATTERNS:
            match = re.search(pattern, clause)
            if match and not clause[match.end():].strip():
                return True
    return False


def _rejection_is_about_scheduling(texts: list[str], offer_outstanding: bool = False) -> bool:
    """True when what the burst rejects is our calendar and not something else.

    L4.7W5-F7E established the positive rule: the burst must actually be ABOUT a slot —
    it names a scheduling object, or the scheduling parser finds a day or a time in it —
    rather than being screened against a list of nouns it must not be about, which would
    need a new entry for every noun a customer can dislike.

    L4.7W5-F7G adds the reading F7E was missing, and the live Wild found the hard way. The
    customer had eight real Friday slots on the table and said

        "Mmm, no me sirve"  /  "No tenés algo más temprano?"

    Neither message names a slot, so F7E judged the rejection off-topic and the thread was
    never handed to a human; it was asked, instead, whether any of the options it had just
    rejected would do. But a rejection with NO OBJECT AT ALL, arriving while an offer is
    outstanding, has exactly one thing it can be about: the offer. The conversation supplies
    what the sentence omits — the same reasoning `_rejects_every_offered_option` already
    uses for "ninguna me cierra", and refused there too when nothing is outstanding.

    A named competing object still wins, checked first and checked regardless of context, so
    every rejection F7E closed stays closed. A phone-call expression counts as such an
    object through the existing call patterns, so "no me sirve que me llame ahora" is still
    about a call. No new vocabulary, no model call.
    """
    from .scheduling_lexicon import names_competing_object, names_scheduling_object

    normalized = _norm_lower(" ".join(t for t in texts if isinstance(t, str)))
    if names_competing_object(normalized):
        return False
    if names_scheduling_object(normalized):
        return True
    day, time_str = _parse_scheduling_text(list(texts), _business_today())
    if day or time_str:
        return True
    if any(p.search(normalized) for p in _PHONE_CALL_PATTERNS):
        return False
    if not offer_outstanding:
        return False
    # A message boundary is a clause boundary, and a stronger one than a comma: two WhatsApp
    # messages are two utterances. Joining them first hid that — "Mmm, no me sirve" followed
    # by "No tenés algo más temprano?" became one clause in which the rejection appeared to
    # take the request as its complement, so the burst escalated in one arrival order and not
    # the other. Clauses are therefore derived per message.
    clauses = [c.strip()
               for message in texts if isinstance(message, str)
               for c in _CLAUSE_BOUNDARY.split(_norm_lower(message))
               if c.strip()]
    return _rejection_has_no_complement(clauses)


def _rejects_every_offered_option(texts: list[str], offer_outstanding: bool) -> bool:
    """True when the burst rejects the WHOLE offered slot set. Grammar, not sentences.

    L4.7W5-F7D-R2. The invariant is one sentence long: *a universal negative quantifier
    scoped to a scheduling object is a rejection of every option we offered.* The verb does
    not matter — "no me sirve", "no puedo", "no me queda bien", "no me cierra" and whatever
    a customer says next are all the same act — so none of them appears in the code. What
    matters is WHICH OBJECT the quantifier ranges over, which is why `ninguno de esos autos`
    and `ninguno de esos horarios` part company here and nowhere else.

    Two readings, and the second needs the conversation to supply what the sentence omits:

      explicit  "ninguno de esos horarios me sirve"  → quantifier + scheduling object
      elliptic  "ninguna me cierra"                  → quantifier, no object named at all

    The elliptic reading is admitted ONLY while an option set is actually outstanding, and
    only when no competing object is named — the customer is answering the offer, so the
    offer is the object. With nothing outstanding the same words mean nothing schedulable
    and this returns False. `_EARLIEST_REJECTED_PATTERNS` is untouched and still runs first.

    Hedged and hypothetical bursts are refused through the existing `turn_modality`, so
    "capaz que ninguno, después te confirmo" is a maybe, not a rejection.
    """
    from ..schemas.claims import Modality
    from .claim_projection import turn_modality
    from .scheduling_lexicon import (has_universal_negative, names_competing_object,
                                     names_scheduling_object)

    normalized = _norm_lower(" ".join(t for t in texts if isinstance(t, str)))
    if not has_universal_negative(normalized):
        return False
    if names_competing_object(normalized):
        return False
    _temporality, modality = turn_modality(list(texts))
    if modality is not Modality.FACTUAL:
        return False
    if names_scheduling_object(normalized):
        return True
    return bool(offer_outstanding)


# L4.7W5-F7A: asking for a person. The escalation evidence covered only "hablá con Julián" —
# a customer who happens to know the owner's name. Asking for "alguien", "una persona" or
# "un humano" is the ordinary way to say it, and after an offer has been rejected it is the
# likeliest thing a frustrated customer says. Matched by shape: a want/need verb plus a
# person word, or a plain "pasame con…".
_HUMAN_REQUEST_PATTERNS: tuple[str, ...] = (
    r"\b(hablar|hablo|hable|comunicar\w*|contactar\w*)\s+(con\s+)?"
    r"(un[ao]?\s+)?(persona|humano|alguien|asesor|operador|encargado|due[nñ]o|vendedor)\b",
    r"\b(pasa|pas[aá]me|pasame|derivame|deriv[aá]me)\b[^.]{0,20}"
    r"\b(persona|humano|alguien|asesor|operador|encargado)\b",
    r"\bquiero\s+(hablar|que\s+me\s+llame|que\s+me\s+llamen)\b",
    r"\b(atienda|atiende|me\s+atiende)\s+(un[ao]?\s+)?(persona|humano|alguien)\b",
    r"\bno\s+(quiero|me\s+sirve)\s+(hablar\s+con\s+)?(un\s+)?(bot|robot|m[aá]quina)\b",
)


def _is_human_request(texts: list[str]) -> bool:
    r"""The customer has asked, affirmatively, to speak to a person.

    L4.7W5-F7F: the same polarity defect the phone-call detector had. `\bquiero\s+hablar\b`
    matched inside "NO quiero hablar por teléfono", so a refusal became a request for a
    human. Read per clause, using the one negation helper, so a request under a negative
    particle is not a request. The patterns are untouched.
    """
    normalized = _norm_lower(" ".join(t for t in texts if isinstance(t, str)))
    clauses = [c.strip() for c in _CLAUSE_BOUNDARY.split(normalized) if c.strip()]
    return any(any(re.search(pat, clause) for pat in _HUMAN_REQUEST_PATTERNS)
               and not _clause_is_negated(clause)
               for clause in clauses)


_ESCALATION_KEYWORDS: frozenset[str] = frozenset({
    "solo puedo",
    "necesito a las",
    # L4.7W5-F7E: "no me sirve" removed. It is a rejection predicate with no object, so it
    # fired on price, payment method, a vehicle, a report and a phone call — five proven
    # false handoffs. The predicate now lives only in _EARLIEST_REJECTED_PATTERNS, where
    # the router scopes it to scheduling.
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


# W1F-W-01 R3: one implementation, in `locality_text`, shared with `field_evidence`, so
# "Benavídez" can never normalise differently in one consumer than in the other.
_strip_accents = _loc.strip_accents


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
            parsed_day, parsed_time = _parse_scheduling_text(texts, _business_today())
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
    "utilitario": "UTILITARIO",
    "camioneta": "CAMIONETA",
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

    # Extract tracking metadata before semantic processing.
    # Client appends: ref: <ref> · cod: RC-XXXX
    ref_m = re.search(r"\bref:\s*([A-Za-z][A-Za-z0-9\-]*)", combined, re.IGNORECASE)
    if ref_m:
        raw_ref = ref_m.group(1)
        # Store only if it matches the known client ref alphabet (allow unknown: preserve raw)
        fields["ref"] = raw_ref[:30]  # cap at field max length; do not invent mapping

    _RC_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    _RC_RE = re.compile(r"\bcod:\s*(RC-[" + _RC_ALPHABET + r"]{4})\b", re.IGNORECASE)
    rc_m = _RC_RE.search(combined)
    if rc_m:
        fields["rc_code"] = rc_m.group(1).upper()

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

    # ── Time extraction: rightmost match wins (SCHED-01 fix) ─────────────
    # When a user corrects or references a prior time ("me dijiste hasta las
    # 18hs. sábado a las 9 entonces"), the intended request appears LAST.
    # re.finditer across all patterns + take the rightmost by character position.
    _time_candidates: list[tuple[int, str]] = []  # (start_pos, "HH:MM")

    for _m in re.finditer(r"\b(\d{1,2})(?::(\d{2}))?\s*h(?:s|oras?|a)?\b", combined):
        _h, _mi = int(_m.group(1)), int(_m.group(2) or 0)
        if 0 <= _h <= 23 and 0 <= _mi <= 59:
            _time_candidates.append((_m.start(), f"{_h:02d}:{_mi:02d}"))

    for _m in re.finditer(r"\bla[s]?\s+(\d{1,2})(?::(\d{2}))?\b", combined):
        _h, _mi = int(_m.group(1)), int(_m.group(2) or 0)
        if 0 <= _h <= 23 and 0 <= _mi <= 59:
            _time_candidates.append((_m.start(), f"{_h:02d}:{_mi:02d}"))

    for _m in re.finditer(r"\b(\d{1,2}):(\d{1,2})\b", combined):
        _h = int(_m.group(1))
        _mi_raw = int(_m.group(2))
        _mi = _mi_raw * 10 if len(_m.group(2)) == 1 else _mi_raw
        if 0 <= _h <= 23 and 0 <= _mi <= 59:
            _time_candidates.append((_m.start(), f"{_h:02d}:{_mi:02d}"))

    time_str: str | None = (
        max(_time_candidates, key=lambda x: x[0])[1] if _time_candidates else None
    )

    return day_iso, time_str


# ── L4.3 Phase A — ordered scheduling branches (primary + fallback) ──────────
# L4-WILD-A proved two independent defects in the single-tuple model:
#   SCHED-A  a weekday name anywhere in the burst suppressed an earlier, unambiguous
#            relative-day request ("mñ 15hs? o nose jueves que tenes" → Thursday).
#   SCHED-B  the time from the FIRST clause was transplanted onto the SECOND clause's
#            day, producing a request the customer never made.
# The scanners below keep every day mention in utterance order and bind each time
# token to the day it actually follows.  When fewer than two distinct days are found
# the legacy `_parse_scheduling_text` result is returned verbatim, so all previously
# certified single-branch behaviour is bit-for-bit unchanged.

_PERIOD_MANANA_RE = re.compile(r"\b(?:por|a|de)\s+la\s+(?:mañana|manana)\b")
_PASADO_MANANA_RE = re.compile(r"\bpasado\s+(?:mañana|manana)\b")
_MN_ABBREV_RE = re.compile(r"\bmñ\b")
_MANANA_RE = re.compile(r"\b(?:mañana|manana)\b")
_HOY_RE = re.compile(r"\bhoy\b")


@dataclass(frozen=True)
class SchedulingRequest:
    """One temporal branch of a scheduling utterance, in the order it was spoken."""
    day_iso: str
    time_str: str | None


def _scan_time_tokens(combined: str) -> list[tuple[int, str]]:
    """All time mentions as (position, 'HH:MM'), left to right."""
    found: list[tuple[int, str]] = []
    for _m in re.finditer(r"\b(\d{1,2})(?::(\d{2}))?\s*h(?:s|oras?|a)?\b", combined):
        _h, _mi = int(_m.group(1)), int(_m.group(2) or 0)
        if 0 <= _h <= 23 and 0 <= _mi <= 59:
            found.append((_m.start(), f"{_h:02d}:{_mi:02d}"))
    for _m in re.finditer(r"\bla[s]?\s+(\d{1,2})(?::(\d{2}))?\b", combined):
        _h, _mi = int(_m.group(1)), int(_m.group(2) or 0)
        if 0 <= _h <= 23 and 0 <= _mi <= 59:
            found.append((_m.start(), f"{_h:02d}:{_mi:02d}"))
    for _m in re.finditer(r"\b(\d{1,2}):(\d{1,2})\b", combined):
        _h = int(_m.group(1))
        _mi_raw = int(_m.group(2))
        _mi = _mi_raw * 10 if len(_m.group(2)) == 1 else _mi_raw
        if 0 <= _h <= 23 and 0 <= _mi <= 59:
            found.append((_m.start(), f"{_h:02d}:{_mi:02d}"))
    found.sort(key=lambda x: x[0])
    return found


def _scan_day_tokens(combined: str, today: date) -> list[tuple[int, str]]:
    """All day mentions as (position, iso_date), in utterance order, deduplicated."""
    scan = combined
    found: list[tuple[int, str]] = []

    def _consume(pattern: re.Pattern, resolver) -> None:
        nonlocal scan
        for m in list(pattern.finditer(scan)):
            iso = resolver(m)
            if iso:
                found.append((m.start(), iso))
            scan = scan[: m.start()] + (" " * (m.end() - m.start())) + scan[m.end():]

    # "por/a la mañana" is a PERIOD (morning), never a day — mask it before day scanning.
    _consume(_PERIOD_MANANA_RE, lambda _m: None)
    _consume(_PASADO_MANANA_RE, lambda _m: (today + timedelta(days=2)).isoformat())
    # "mñ" is the unambiguous mobile abbreviation of "mañana": it can never mean
    # "morning", so a weekday name later in the utterance must not suppress it.
    _consume(_MN_ABBREV_RE, lambda _m: (today + timedelta(days=1)).isoformat())

    day_name_found = any(name in scan for name in _SPANISH_DAY_TO_WEEKDAY)
    if not day_name_found:
        # Bare "mañana"/"manana" stays ambiguous (tomorrow vs morning); the legacy
        # rule keeps the weekday reading when a weekday name is present.
        _consume(_MANANA_RE, lambda _m: (today + timedelta(days=1)).isoformat())
        _consume(_HOY_RE, lambda _m: today.isoformat())

    for day_name, weekday in _SPANISH_DAY_TO_WEEKDAY.items():
        for m in re.finditer(rf"\b{day_name}\b", scan):
            days_ahead = weekday - today.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            found.append((m.start(), (today + timedelta(days=days_ahead)).isoformat()))

    def _explicit(pattern: str, month_from_group: bool = True, month_name: int | None = None) -> None:
        for m in re.finditer(pattern, scan):
            d = int(m.group(1))
            mo = int(m.group(2)) if month_from_group else int(month_name or 0)
            if not (1 <= d <= 31 and 1 <= mo <= 12):
                continue
            try:
                candidate = date(today.year, mo, d)
                if candidate < today:
                    candidate = date(today.year + 1, mo, d)
            except ValueError:
                continue
            found.append((m.start(), candidate.isoformat()))

    _explicit(r"\b(\d{1,2})\s+del?\s+(\d{1,2})\b")
    for _mname, _mnum in _SPANISH_MONTHS.items():
        _explicit(rf"\b(\d{{1,2}})\s+de\s+{_mname}\b", month_from_group=False, month_name=_mnum)
    _explicit(r"\b(\d{1,2})[/-](\d{1,2})\b")

    found.sort(key=lambda x: x[0])
    ordered: list[tuple[int, str]] = []
    seen: set[str] = set()
    for pos, iso in found:
        if iso in seen:
            continue
        seen.add(iso)
        ordered.append((pos, iso))
    return ordered


def _parse_scheduling_requests(texts: list[str], today: date) -> list[SchedulingRequest]:
    """Ordered scheduling branches: [primary, fallback, ...].

    Multi-branch utterances ("mñ 15hs? o nose jueves que tenes") return one request
    per distinct day, each carrying only the time token that belongs to it.  Anything
    with fewer than two distinct days falls back to the legacy single-tuple parse.
    """
    combined = " ".join(texts).lower()
    days = _scan_day_tokens(combined, today)
    if len(days) >= 2:
        times = _scan_time_tokens(combined)
        requests: list[SchedulingRequest] = []
        for idx, (pos, iso) in enumerate(days):
            low = -1 if idx == 0 else pos          # the first day also owns earlier times
            high = days[idx + 1][0] if idx + 1 < len(days) else len(combined) + 1
            owned = [t for (tpos, t) in times if low <= tpos < high]
            requests.append(SchedulingRequest(day_iso=iso, time_str=owned[-1] if owned else None))
        return requests

    legacy_day, legacy_time = _parse_scheduling_text(texts, today)
    if legacy_day:
        return [SchedulingRequest(day_iso=legacy_day, time_str=legacy_time)]
    return []


#: Outcomes where NO customer reply was required, so the absence of one is not an alert.
#: G3-7F adds the two deliberate-silence outcomes: a Flow already outstanding with the
#: customer, and a replayed completion. In both cases saying nothing is the correct
#: behaviour, so an alert for "no reply produced" would be a false positive.
_NO_REPLY_REQUIRED_ACTIONS = frozenset({
    "skipped_dedup", "no_lead", "skipped_human",
    "skipped_clarification_outstanding", "skipped_duplicate_completion",
    # G3-7R/S6: the booking already exists and its acknowledgement was already sent by the
    # transport that created it. A second one is the duplicate acknowledgement S6 forbids,
    # so this turn owes the customer nothing and must not be alerted on as a missed reply.
    ACTION_BOOKING_REPLAY})
_REPLY_PRODUCED_ACTIONS = frozenset({"replied", "flow_button_sent", "booking_created"})


def _out(action: str, **kwargs) -> ConversationHandleOut:
    detail = kwargs.get("detail", "") or ""
    # thread_not_found is a routing miss — customer never expected a CE reply
    _no_reply = action in _NO_REPLY_REQUIRED_ACTIONS or (
        action == "error" and detail == "thread_not_found"
    )
    reply_required = not _no_reply
    reply_produced = action in _REPLY_PRODUCED_ACTIONS
    alert_eligible = reply_required
    return ConversationHandleOut(
        ok=action not in ("error", "blocked_dispatch"),
        action=action,
        handled=action in HANDLED_ACTIONS,
        reply_required=reply_required,
        reply_produced=reply_produced,
        alert_eligible=alert_eligible,
        **kwargs,
    )


@dataclass
class _Context:
    thread: WhatsAppThread
    contact: WhatsAppContact
    lead: Lead | None
    state: WhatsAppThreadState | None
    candidates: list[WhatsAppThreadCandidate]
    db_messages: list[WhatsAppMessage]  # newest 20 active-cycle messages, chronological
    previous_processed_cursor: str | None = None  # set in _handle() before cursor overwrite
    inbound_wa_message_id: str | None = None  # WILD-01 FINDING-03: causal inbound for dedup


# ── L4.7W5-F5: article-prefixed locality matching ────────────────────────────
# W1F-W-01 R3: bound from `locality_text`; `field_evidence` holds the same objects.
_ARTICLE_PREFIX_RE = _loc.ARTICLE_PREFIX_RE

# A locality whose name is also a common noun is only a locality when the sentence places
# something there. "en la boca" is a place; "me mordí la boca" is not.
_LOCATIVE_MARKERS = _loc.LOCATIVE_MARKERS

_locality_in_context = _loc.locality_in_context



class _ZoneBuffer:
    """The pre-candidate thread buffer, shaped like a candidate for the one zone writer.

    `whatsapp_thread_states.home_zone_*` is the buffer the review calls role-ambiguous
    (RC-5) and it is not renamed here — R1 changes an order, not a schema. What this
    adapter does change is who may write it: a value reaching the buffer from the admission
    boundary goes through `_apply_inspection_zone` and is therefore decided by the
    certified reconciler, instead of being assigned directly the way the location form
    still assigns it (W1FRV-04).
    """

    __slots__ = ("_state",)

    def __init__(self, state):
        self._state = state

    @property
    def zone_group(self):
        return getattr(self._state, "home_zone_group", None)

    @zone_group.setter
    def zone_group(self, value):
        self._state.home_zone_group = value

    @property
    def zone_detail(self):
        return getattr(self._state, "home_zone_detail", None)

    @zone_detail.setter
    def zone_detail(self, value):
        self._state.home_zone_detail = value


class ConversationEngine:
    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._pricing = PricingService(repository=PricingRepository())
        self._schedule = ScheduleService(db=db)
        self._ai_invoked: bool = False
        self._answer_source_value: str | None = None
        self._contributing_sources: list[str] | None = None
        self._burst_message_count: int = 1
        self._burst_earliest_inbound_db_id: int | None = None
        self._turn_faq_burst: str | None = None
        self._availability_authority_cache = None
        self._canonical_turn_burst = None       # G3-5A: the turn's evidence boundary
        self._availability_authority_phases = {}  # G3-5B: one decision per phase
        self._turn_semantic_burst = None           # G3-5B: which burst was interpreted
        self._turn_semantic_dispatched = False      # G3-5C: arms the fail-closed check
        self._turn_admission = None                 # W1F-W-01 R1: this turn's admission
        #: W1F-W-01 R2 — per-turn, never sticky. The answered-question set is the typed
        #: routing fact; the evidence rows are this turn's persisted decisions; the skip
        #: flag records that the semantic wait was not paid.
        self._turn_answered_questions = ()
        self._turn_evidence_rows = ()
        self._turn_semantic_wait_skipped = False
        self._turn_question_recorded = ()
        self._turn_semantic_calls = 0              # model invocations
        self._semantic_accessor_reads = 0          # reads of that one interpretation
        # ── G3-5E: the reply-generation role, counted and attributed separately ──
        self._reply_calls = 0
        self._reply_latency_ms = None
        self._reply_status = "ABSENT"
        self._reply_role = None
        self._reply_burst = None                   # the burst the reply model was given
        self._reply_admission_state = None          # G3-5E2 typed admission verdict
        self._turn_decision_authority = None
        self._turn_fact_sources: list[str] = []
        self._turn_renderer_source = None
        self._turn_dispatch_path = None
        self._turn_dispatch_result = None
        self._turn_flow_dispatched = False
        # R3/D: `_turn_flow_dispatched` is set BEFORE the gate, so it is true for a
        # Flow the kill switch refused. This one is set only once the gate ALLOWED.
        self._turn_flow_delivered = False
        self._turn_text_dispatches = 0
        self._turn_slot_claim_before = None     # R3/D: set once the state row is held
        self._turn_response_plan = None
        # L4.7W5 Gate 1/2 — hybrid decision trace collection. Observational only.
        self._turn_trace_ctx = None
        self._turn_trace_before = None
        self._turn_trace_rows: list = []
        self._turn_trace_reconciliations: list = []

    # ── Public entrypoint ─────────────────────────────────────────────────

    # Actions whose answer_source is deterministic from action alone
    _DETERMINISTIC_SOURCE_ACTIONS: frozenset[str] = frozenset({
        "human_handoff_blocked", "location_contradiction_blocked",
        "inspectability_gate_blocked", "blocked_dispatch",
    })
    _VEHICLE_RESOLVER_SOURCE_ACTIONS: frozenset[str] = frozenset({"vehicle_fuzzy_blocked"})

    @property
    def _answer_source(self):
        return getattr(self, "_answer_source_value", None)

    @_answer_source.setter
    def _answer_source(self, value):
        """Recording the legacy field also records the typed attribution.

        G3-5E2. The ~42 handlers that assign this already name the component that decided;
        this makes that statement populate `decision_authority` and `fact_sources` too,
        without a second declaration to drift from the first and without a default. An
        unmapped or cleared value records nothing rather than guessing.
        """
        self._answer_source_value = value
        if value is None:
            # An explicit clear means "no handler owns this turn yet" — the AI path does
            # exactly this before claiming CE_AI. Stale attribution must go with it.
            self._turn_decision_authority = None
            self._turn_fact_sources = []
            return
        authority, facts = _ANSWER_SOURCE_ATTRIBUTION.get(value, (None, ()))
        if authority is not None:
            self._turn_decision_authority = authority
        for fact in facts:
            existing = getattr(self, "_turn_fact_sources", None)
            if existing is None:
                existing = self._turn_fact_sources = []
            if fact not in existing:
                existing.append(fact)

    #: G3-5F §4.1 (audit G5E2-01). The ONE place that says which transport an outbound
    #: path uses. Three kill-switch exits used to name `META_FLOW` beside a `CE_TEXT`
    #: gate attempt, because the transport was a literal written at the call site and
    #: nothing tied it to the path actually being blocked. Deriving it from the very
    #: `path_id` handed to the gate makes that drift unrepresentable.
    _TRANSPORT_FOR_PATH_ID: dict = {
        OutboundPathId.CE_TEXT.value: DispatchPath.TEXT,
        OutboundPathId.CE_INTERACTIVE.value: DispatchPath.TEXT,
        OutboundPathId.CE_LIST.value: DispatchPath.TEXT,
        OutboundPathId.MANUAL_CRM.value: DispatchPath.TEXT,
        OutboundPathId.SYSTEM_NOTIFICATION.value: DispatchPath.TEXT,
        OutboundPathId.CE_FLOW.value: DispatchPath.META_FLOW,
        OutboundPathId.BOOKING_FLOW.value: DispatchPath.META_FLOW,
    }

    @classmethod
    def _transport_for_path_id(cls, path_id: str) -> str:
        """The transport a given authorized outbound path uses.

        An unmapped path id records `NONE` rather than guessing a transport: an unknown
        path is exactly the case the outbound invariants block, and naming a transport for
        it would be the same class of falsehood this method exists to remove.
        """
        return cls._TRANSPORT_FOR_PATH_ID.get(path_id, DispatchPath.NONE)

    @staticmethod
    def _outbound_armed() -> bool:
        """The ONE read of the kill switch inside the engine.

        G3-5F §14. Seven handlers each read `OUTBOUND_ENABLED` from the environment
        themselves. That is not a second kill switch — the gate still refuses every send
        independently, and it stays fail-closed — but it is seven places that could drift
        in how they spell the check. There is one now, and it keeps the gate's own
        semantics exactly: only the literal "true" arms outbound.
        """
        return os.environ.get("OUTBOUND_ENABLED") == "true"

    def _blocked_outbound(self, ctx, text: str, path_id: str):
        """The single early kill-switch exit: audit the intent, record it truthfully, stop.

        G3-5F §4.1/§14. Seven paths read `OUTBOUND_ENABLED` themselves and return before
        reaching `_send_text_to_wa`, so the gate's own recording never runs. They now share
        this one exit, which takes the authorized path id and derives everything else from
        it. A site can no longer state a transport that disagrees with the path it blocked,
        because it no longer states one.
        """
        self._record_blocked_dispatch(self._transport_for_path_id(path_id))
        OutboundSafetyGate(self.db).attempt(
            wa_id=ctx.contact.wa_id, thread_id=ctx.thread.id, text=text,
            path_id=path_id,
        )
        self.db.commit()
        return _out("blocked_dispatch", detail="OUTBOUND_GATE_BLOCKED_KILL_SWITCH")

    def _record_blocked_dispatch(self, path: str) -> None:
        """An early kill-switch exit records WHAT the gate did, and nothing else.

        G3-5E2 (audit G5E-03). Seven paths read `OUTBOUND_ENABLED` themselves and return
        `blocked_dispatch` without passing through `_send_text_to_wa`, so the very case
        `DispatchResult.BLOCKED` exists for recorded nothing and the Inspector showed it as
        unavailable. This records the intended transport and the gate's verdict. It does
        not touch the decision authority, the fact sources or the renderer — those belong
        to whoever produced the content, and the gate is never their author.
        """
        self._turn_dispatch_path = path
        self._turn_dispatch_result = DispatchResult.BLOCKED

    def _note_fact_source(self, *sources) -> None:
        """Record an additional producer of the facts in this turn's response."""
        existing = getattr(self, "_turn_fact_sources", None)
        if existing is None:
            existing = self._turn_fact_sources = []
        for source in sources:
            if source and source not in existing:
                existing.append(source)

    def _turn_correlation_id(self) -> str:
        """L4.3 Phase F: one correlation id per CE turn, shared by every outbound row."""
        current = getattr(self, "_correlation_id", None)
        if not current:
            import uuid as _uuid
            current = str(_uuid.uuid4())
            self._correlation_id = current
        return current

    def handle(self, event: ConversationHandleIn) -> ConversationHandleOut:
        # Reset per-turn tracking (guard for tests that reuse an engine instance)
        import uuid as _uuid
        self._correlation_id = str(_uuid.uuid4())
        self._booking_flow_send_failed = False
        self._turn_price_quote = None          # L4.7D canonical price for this turn
        self._availability_checked = False     # L4.7D: set by any ScheduleService evaluation
        self._ai_invoked = False
        self._answer_source = None
        self._contributing_sources = None
        self._burst_message_count = 1
        self._burst_earliest_inbound_db_id = None
        self._turn_faq_burst = None
        self._availability_authority_cache = None   # G3-4: one decision per turn
        self._canonical_turn_burst = None           # G3-5A: one burst per turn
        self._availability_authority_phases = {}    # G3-5B: one decision per phase
        self._turn_semantic_burst = None
        self._turn_semantic_dispatched = False
        self._turn_semantic_calls = 0
        self._semantic_accessor_reads = 0
        self._reply_calls = 0                       # G3-5E
        self._reply_latency_ms = None
        self._reply_status = "ABSENT"
        self._reply_role = None
        self._reply_burst = None
        self._reply_admission_state = None
        self._reply_contract = ReplyContract.NOT_INVOKED
        self._turn_candidate_resolution = None
        # G3-7P: the turn's offer decisions. Both are READ — `_turn_offer_claim` by the
        # canonical snapshot (`offer_refusal`) and `_turn_offer_withdrawal` by the
        # withdrawal decision row — so neither is another write-only `_turn_*` field, and
        # both are reset here for the reason the G3-7G audit established: a reused engine
        # instance must never report a previous turn's decision as this turn's.
        self._turn_offer_claim = None
        self._turn_offer_admission = None        # G3-7R/S2: what admission decided
        self._turn_offer_withdrawal = None
        self._turn_service_question = False
        # W1F-W-01 R1: this turn's admission decisions, for the trace and the tests.
        self._turn_admission = None
        #: W1F-W-01 R2 — per-turn, never sticky. The answered-question set is the typed
        #: routing fact; the evidence rows are this turn's persisted decisions; the skip
        #: flag records that the semantic wait was not paid.
        self._turn_answered_questions = ()
        self._turn_evidence_rows = ()
        self._turn_semantic_wait_skipped = False
        self._turn_question_recorded = ()
        # OWNER-REVIEW-R1 R1RV-02: this turn's locality ambiguity, as a reason string. Read
        # by `_turn_clarification_decision`, so it is per-turn memory and must be cleared
        # here for the reason the G3-7G audit established — a reused engine instance must
        # never report a previous turn's verdict as this turn's.
        self._turn_locality_ambiguity = None
        self._turn_correction_echo = ()          # G3-7F: Tier 1 must echo what it changed
        self._turn_correction_moto = False       # G3-7F-R2: corrected category is a MOTO
        self._turn_correction_category = None    # G3-7F-R2: the category the correction set
        # ── G3-7F-OWNER-RESOLUTION observability (Part H) ─────────────────────────
        self._turn_clarification_route = None    # which clarification the router chose
        self._turn_clarification_reason = None   # why it chose it
        self._turn_clarification_flow = None     # "VEHICLE" | "LOCATION", after dispatch
        self._turn_clarification_intro = None    # (family, composer-rendered sentence)
        # ── G3-7H P1: the rest of the per-turn clarification memory ───────────────
        # The G3-7G audit found `_turn_clarification_decision_obj` was the only field in
        # this block that was never reset, and measured the consequence: on a second turn
        # through the same engine instance the memo returned turn 1's verdict, so an FAQ
        # turn followed by a price question never re-decided and the Location Flow was
        # never offered. That is the real `rc14` defect.
        #
        # The audit's field inventory showed fourteen more in the same state. Every one of
        # them feeds `_clarification_for_trace`, so a reused engine would also report the
        # previous turn's attempt id, reservation, dispatch and completion as if they
        # belonged to this turn. They are reset together, here, for the same reason.
        #
        # PROCESS MEMORY ONLY. `_clarification_attempt_store` is deliberately NOT reset:
        # it is the session factory for durable attempt history, and that history —
        # `whatsapp_clarification_attempts` — is the authority for "this family already had
        # its one attempt". Nothing here may stand in for it.
        self._turn_clarification_decision_obj = None    # P1: the memo itself
        self._turn_clarification_decision_count = 0     # exactly one decision per turn
        self._turn_clarification_family = None
        self._turn_clarification_attempt = None
        self._turn_clarification_attempt_id = None
        self._turn_clarification_reservation = None
        self._turn_clarification_cycle_key = None
        self._turn_clarification_dispatch = None
        self._turn_clarification_completion = None
        self._turn_clarification_prevented_second = False
        self._turn_clarification_store_fault = None
        self._turn_clarification_burst = None
        self._turn_clar_semantic = None
        self._turn_clar_deterministic = None
        self._turn_clar_identity = None
        self._turn_clar_location = None
        self._turn_general_information_registered = False
        self._turn_handoff_reason = None         # truthful generic-handoff reason
        self._turn_generic_handoff = False       # a person owns the turn from here
        self._turn_confirmation_bound = None     # Part A: why authority was or was not bound
        self._turn_correction_proposition = None
        self._turn_decision_authority = None
        self._turn_fact_sources = []
        self._turn_renderer_source = None
        self._turn_dispatch_path = None
        self._turn_dispatch_result = None
        self._turn_flow_dispatched = False
        # R3/D: `_turn_flow_dispatched` is set BEFORE the gate, so it is true for a
        # Flow the kill switch refused. This one is set only once the gate ALLOWED.
        self._turn_flow_delivered = False
        self._turn_text_dispatches = 0
        self._turn_slot_claim_before = None     # R3/D: set once the state row is held
        self._turn_response_plan = None
        # G3-5F §5/§6: the LIVE plan. Constructed before anything can dispatch, so the
        # propositions a turn registers are decided by a plan rather than by which handler
        # reached the send primitive first.
        self._live_plan = LiveResponsePlan(turn_id=self._correlation_id)
        self._turn_burst_texts = []
        self._turn_trace_ctx = None
        self._turn_trace_before = None
        self._turn_trace_rows = []
        self._turn_trace_reconciliations = []
        self._sb_trace = None                    # SB2B: this turn's booking facts
        _trace_started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        _ce_t0 = _time.perf_counter()
        try:
            out = self._handle(event)
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
                out = _out("blocked_dispatch", detail=detail)
            else:
                # Duplicate / flood: CE may have partial state — do not commit.
                try:
                    self.db.rollback()
                except Exception:
                    pass
                out = _out("error", detail=detail)
        except Exception:
            logger.exception(
                "M18 engine unhandled error thread_id=%s wa=%s",
                event.thread_id, event.wa_message_id,
            )
            try:
                self.db.rollback()
            except Exception:
                pass
            out = _out("error", detail="internal_error")
        # ── R3/D: one place decides whether the customer saw this turn's slots ───
        # Every branch above converges here, including the kill switch (which refused the
        # send) and the crash path (whose rollback cannot undo the slot list, because the
        # day-slots path committed it BEFORE dispatching). The helper itself checks what
        # the outbound gate allowed and whether this turn claimed anything, so it is a
        # no-op on every turn that really did display its slots.
        self._revoke_undelivered_slot_claim(event, out)
        out.latency_ce_ms = int((_time.perf_counter() - _ce_t0) * 1000)
        _ai_inv = getattr(self, "_ai_invoked", False)
        out.ai_invoked = _ai_inv
        # Apply answer_source — priority order:
        # 1. Inline answer_source already set in _out() call site
        # 2. Unambiguous action inference (one-to-one action→source mapping)
        # 3. self._answer_source (set at handler method entry for "replied" paths)
        # 4. CE_AI when OpenAI was invoked
        # 5. None (untagged — should not occur for launch-reachable paths)
        if out.answer_source is None:
            if out.action in ("flow_button_sent", "booking_created"):
                out.answer_source = "FLOW_RESPONSE"
                self._turn_decision_authority = DecisionAuthority.FLOW_TRANSACTION
                self._note_fact_source(FactSource.FLOW_TRANSACTION)
            elif out.action == "skipped_human":
                out.answer_source = "HUMAN"
                # G3-5E2: this resolution is truthful — the human-ownership policy really
                # did own the turn — so the typed attribution is recorded with it. The
                # action-derived branches below are NOT adopted: `blocked_dispatch` naming
                # DETERMINISTIC_RULE is the very conflation G5D-04 recorded, and an
                # ERROR_FALLBACK is an outcome, not an authority.
                self._turn_decision_authority = DecisionAuthority.HUMAN_HANDOFF_POLICY
                self._note_fact_source(FactSource.CANONICAL_STATE)
            elif out.action in self._DETERMINISTIC_SOURCE_ACTIONS:
                out.answer_source = "DETERMINISTIC_RULE"
            elif out.action in self._VEHICLE_RESOLVER_SOURCE_ACTIONS:
                out.answer_source = "VEHICLE_RESOLVER"
            elif out.action == "error":
                out.answer_source = "ERROR_FALLBACK"
            else:
                _ans_src = getattr(self, "_answer_source", None)
                if _ans_src is not None:
                    out.answer_source = _ans_src
                elif _ai_inv:
                    out.answer_source = "CE_AI"
        # Apply burst observability fields
        out.burst_message_count = getattr(self, "_burst_message_count", 1)
        out.burst_earliest_inbound_db_id = getattr(self, "_burst_earliest_inbound_db_id", None)
        out.contributing_sources = getattr(self, "_contributing_sources", None)
        # L4.7W5 Gate 1/2 — record how this decision was reached. Runs AFTER the answer is
        # final and after every response-shaping branch above, so it can observe the real
        # outcome; it writes in its own transaction and returns None on every failure, so a
        # trace defect can never cost a customer turn.
        # G3-5E: the shadow plan describes the turn whether or not tracing is armed, so
        # "what this turn owed the customer" is observable on its own terms. It reads state
        # the decision path already recorded; it calls nothing and changes nothing.
        self._turn_response_plan = self._turn_plan(out)
        self._write_hybrid_trace(event, out, _trace_started_at,
                                 int((_time.perf_counter() - _ce_t0) * 1000))
        return out

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

        # ── G3-7R/S2: THERE IS NO TURN-ENTRY BOOKING GATE ANY MORE ───────────────
        # R3 took `whatsapp_thread_states` — resource 1 of `OFFER_LOCK_ORDER` — on EVERY
        # turn here, and R3A narrowed it with `_is_offer_acceptance_turn`, a predicate that
        # tried to PREDICT whether a turn would later book. Both are deleted, and the
        # prediction is deleted with them, because the architecture audit measured that the
        # approach cannot be made safe:
        #
        #   * the predicate answered from the transport mirror and the payload shape, so an
        #     ordinary turn could be classified into the booking lock order — and it was
        #     documented as failing closed "to True", meaning its fallback behaviour was to
        #     serialise traffic that could never book. The R3 review measured the cost of a
        #     turn that should not have been in the order at all: DECLINED after 4.05s on a
        #     thread the parent commit served in 0.05–0.20s. That is a lost customer message.
        #
        #   * holding this row across the whole turn ALSO makes the canonical booking
        #     authority unreachable. Smart Booking's SB2B-1 forbids a caller entering with
        #     uncommitted locks on this table, and the audit measured the adapter's own
        #     session refused after 5.02s with 55P03 while the turn held it.
        #
        # The lock order has not been weakened: it moved to where the booking is, and every
        # writer of the offer table now takes resource 1 inside `offer_lifecycle` itself
        # (S1), ISSUE included. The boundary question is no longer "will this turn book?"
        # but "does the submitted token match an ISSUED offer row for this thread?", which
        # `OfferLifecycle.admit()` answers with one indexed read of immutable facts at the
        # actual acceptance point. An ordinary turn performs no booking lock operation
        # because there is no gate left for it to be classified into.

        # R3/D: the slot-visibility claim as it stood BEFORE this turn. The day-slots path
        # commits `last_visible_slots` before it dispatches, so a turn that ends up
        # delivering nothing must put back what was there — not blank the field, which
        # would destroy a true claim left by an earlier successful turn.
        self._turn_slot_claim_before = (state.last_visible_slots, state.last_offered_slots)

        # L4.7W5 Gate 1 — the canonical state as it stood BEFORE this turn touched it.
        # Taken here because `state` now exists and nothing has yet mutated it; taken later
        # it would describe the outcome and could no longer explain the decision.
        self._capture_trace_before(ctx, state)

        # ── Convergence Phase 3 (B-1 + B-2): THE LEDGER SPEAKS FIRST ─────────────────────
        # A booking-shaped submission asks the booking ledger, in an isolated read-only
        # session, BEFORE the cursor, the cycle reset and the `needs_human` gate. A hit is the
        # booking — whoever committed it — and goes straight to acknowledgement recovery and
        # CE synchronisation; it is never `skipped_human` and never re-admitted. A miss changes
        # nothing. The read is not a booking writer, so it runs whatever the flag says: a
        # customer whose booking exists is never told it failed.
        #
        # THERE IS NO TURN-ENTRY BOOKING ACQUISITION ANY MORE. R3/RVR2-01 took the thread's
        # state row here, for every booking-shaped turn, because this turn's own session WAS
        # the booking transaction. It no longer is: `BookingAuthority` takes that row itself,
        # explicitly and first (K1, bounded 4 s), on its own connection. Holding it here would
        # make the authority wait on its own caller (55P03 measured), and the transaction id
        # the lock assigns would trip the clean-transaction guard on every submission (CD-01).
        # The two jobs the acquisition did are now done by construction:
        #   * the bound — the authority's K1 `lock_timeout`;
        #   * "contention consumes nothing" — on a booking-shaped turn the cursor is NOT
        #     assigned before delegation (design A-2). It is written only by the CE
        #     synchronisation after a committed or replayed result, or by a committed handoff,
        #     so a transient result rolls back a session that has nothing to lose.
        _booking_shaped = self._is_booking_shaped_submission(event, state)
        self._turn_booking_shaped = _booking_shaped
        # PW1-A (R3RV-01): taken HERE, before the cycle reset can clear the mirror, so the
        # predicate above and the router below read one value.
        _flow_route = _flow_routing_token(event.flow_token,
                                          getattr(state, "flow_booking_token", None))
        # WILD-01 FINDING-03: store inbound ID on ctx for causal dedup in gate calls
        ctx.inbound_wa_message_id = event.wa_message_id
        if _booking_shaped:
            replayed = self._sb_flow_ledger_entry(ctx, state, event)
            if replayed is not None:
                return replayed

        # WILD-04R: capture previous cursor BEFORE overwrite (burst start discovery)
        previous_cursor = state.last_processed_inbound_wa_message_id
        ctx.previous_processed_cursor = previous_cursor
        if not _booking_shaped:
            state.last_processed_inbound_wa_message_id = event.wa_message_id

        # No lead linked — cannot make business decisions; commit dedup marker now
        if ctx.lead is None:
            logger.info("M18 thread_id=%s no linked lead", event.thread_id)
            if _booking_shaped:
                _held = self._sb_enter_thread_state(int(ctx.thread.id))
                if _held is not None:
                    return self._contended_booking_turn(
                        ctx, int(ctx.thread.id), _held, where="no_lead",
                        had_token=bool(event.flow_token))
            state.last_processed_inbound_wa_message_id = event.wa_message_id
            self.db.commit()
            return _out("no_lead")

        # WILD-04R: consume cycle reset signal (set by set_lead_estado() in CRM)
        if state.cycle_reset_pending:
            # ── R3/RVR2-01: THE RESET IS A WRITER, AND ENTERS THE ORDER BEFORE IT WRITES ──
            # It archives prior-cycle candidates, clears state and lead fields and WITHDRAWS
            # the thread's live offer through the offer authority, so it takes resource 1
            # bounded first; contention writes nothing (the entry form rolls the aborted
            # transaction back) and a redelivery is free to try again. On a booking-shaped
            # turn the ledger has already missed, and the turn that carries the reset never
            # delegates: the reset withdrew the offer, so admission refuses it as a read and the
            # truthful handoff commits the reset together with the cursor (design §12 stage 3,
            # SB2B-RV-01's cycle-reset branch closed structurally).
            _reset_contended = self._enter_cycle_reset_bound(ctx)
            if _reset_contended is not None:
                return self._contended_booking_turn(
                    ctx, int(ctx.thread.id), _reset_contended, where="cycle_reset",
                    had_token=bool(event.flow_token))
            self._execute_cycle_reset(ctx, state, event, previous_cursor)
            # WILD-04R-F2: reload semantic context with the new cycle watermark.
            # _load_context ran before the reset and used the OLD watermark, so
            # ctx.candidates / ctx.db_messages may include prior-cycle data.
            # Re-query both so the first new-cycle turn is semantically identical
            # to any later turn in the same cycle.
            ctx.candidates = self._reload_active_candidates(ctx.thread.id, state)
            ctx.db_messages = self._query_active_messages(ctx.thread.id, state)

        # M21.4A: populate attribution fields (first-write-only, no overwrite). A
        # booking-shaped turn writes it with the cursor, after the booking decision.
        if not _booking_shaped:
            self._maybe_set_attribution(ctx, state)

        # Human takeover check — AFTER cycle reset which clears needs_human
        if state.needs_human:
            logger.info("M18 thread_id=%s needs_human — AI suppressed", event.thread_id)
            # ── G3-5F §9 (P2): exclusivity, declared to the plan ──────────────────
            # Nothing automated may stand beside a human-owned turn — no FAQ answer, no
            # canonical restatement, no clarification. Declaring it here rather than
            # checking `state.needs_human` at each composition point means a proposition
            # registered by a producer that ran EARLIER in the turn is suppressed too.
            _plan = getattr(self, "_live_plan", None)
            if _plan is not None:
                _plan.human_owns_turn = True
            if _booking_shaped:
                # The ledger already missed above, so `skipped_human` is truthful. With no
                # turn-entry acquisition any more, the cursor this commit writes is the
                # turn's FIRST write to the thread-state row, so it enters K1 explicitly and
                # bounded first (R3/RVR2-01, measured: unbounded, it waited out an 18 s
                # holder). Contention writes nothing and the redelivery is free to retry.
                _held = self._sb_enter_thread_state(int(ctx.thread.id))
                if _held is not None:
                    return self._contended_booking_turn(
                        ctx, int(ctx.thread.id), _held, where="needs_human",
                        had_token=bool(event.flow_token))
                state.last_processed_inbound_wa_message_id = event.wa_message_id
                self._maybe_set_attribution(ctx, state)
            self.db.commit()
            return _out("skipped_human")

        # Route by message type
        if event.message_type == "flow_response" and event.flow_response:
            return self._process_flow_response(ctx, event.flow_response, event.flow_token,
                                               routing_token=_flow_route)

        # Text path: uses n8n-provided content (audio already transcribed by n8n)
        return self._process_text(ctx, event)

    # ── R3/RVR2-01: the booking-shaped predicate and the contended answer ────

    @staticmethod
    def _is_booking_shaped_submission(event: ConversationHandleIn, state) -> bool:
        """Can this delivery reach the booking acceptance boundary? Transport only.

        R3/RVR2-01; convergence Phase 3. The predicate the ledger-first lookup and the
        deferred cursor are gated on (there is no turn-entry lock any more), stated once so
        the gate and its tests cannot drift. It answers True for exactly the deliveries
        `_process_flow_response` routes to admission, by repeating that method's own three
        discriminators in its own order:

          1. the transport delivered a Flow response, with a payload — `message_type` and
             `flow_response`, both set by the n8n webhook from Meta's envelope;
          2. the routing token is NOT a motorcycle-handoff token, which
             `_process_flow_response` checks first because both paths share one Flow id and
             the prefix is the only discriminator. PW1-A (R3RV-01): that is the SUBMITTED
             token (`_flow_routing_token`), the same value the router reads — not the mirror,
             which a cycle reset clears between this read and the router's;
          3. the payload carries neither `tipo_vehiculo` nor `zona_general`, the
             distinctive keys of the two qualification fallback forms.

        Every test is on transport metadata, on a token PREFIX this engine itself minted,
        or on the PRESENCE of a payload key. Nothing here reads customer prose, a semantic
        interpretation, a reply proposal or any model output — a predicate that gates a
        lock must not be able to change its mind because a sentence was phrased
        differently. It takes no lock, issues no query and writes nothing.
        """
        if event.message_type != "flow_response" or not event.flow_response:
            return False
        if _flow_routing_token(event.flow_token, getattr(state, "flow_booking_token", None)
                               ).startswith(_MOTO_HANDOFF_TOKEN_PREFIX):
            return False
        payload = event.flow_response
        if "tipo_vehiculo" in payload or "zona_general" in payload:
            return False
        return True

    def _enter_cycle_reset_bound(self, ctx: _Context) -> "str | None":
        """Take resource 1 for the cycle reset, bounded. `None` to proceed.

        R3/RVR2-01. The reset archives candidates, clears state and lead fields and
        withdraws the live offer through the offer authority, so it is a writer and enters
        the canonical order before it writes. Gated on the flag actually being set. Since
        convergence Phase 3 it is the only thread-state acquisition a CE turn takes before
        its booking decision, and a turn that takes it never delegates to the authority (the
        reset withdrew the offer; admission refuses as a read).
        """
        from .offer_lifecycle import OfferLifecycle as _ResetEntryOffers
        return _ResetEntryOffers(self.db).begin_offer_transaction(int(ctx.thread.id))

    def _contended_booking_turn(self, ctx: _Context, thread_id: int, reason: str, *,
                                where: str, had_token: bool) -> ConversationHandleOut:
        """The whole contended answer: typed, silent, and having written nothing.

        R3/RVR2-01. `begin_offer_transaction` has already rolled the aborted transaction
        back, which discarded this turn's pending dedup cursor — correct, because the turn
        did not complete and a redelivery must be free to try again against the offer's
        then-current state. Reached before the cursor is even assigned on the turn-entry
        path, so on that path there is nothing to discard.

        WHAT THIS DELIBERATELY DOES NOT DO, each one measured on the pre-fix code by the R2
        independent review: it does not execute or partially execute the cycle reset, does
        not archive the candidate, does not withdraw or otherwise mutate the offer, does
        not move the refusal or replay counter, does not create or touch a booking or a
        revision, does not change the thread or lead stage or `needs_human`, does not send
        the customer anything, and does not raise an internal handoff. Declaring a human
        handoff for a submission nobody has judged would be a false statement, and that
        path's own `needs_human` commit would itself block on the row the other writer is
        holding.

        The evidence is the decision log plus the typed action, and the action stays
        `reply_required` so the no-reply rescue still sees the dropped turn.
        """
        self._turn_offer_claim = None
        self._decision_log(ctx, "booking_offer_contended", reason=reason, where=where,
                           had_token=had_token)
        logger.warning(
            "G3-7R-R3 thread_id=%s contended at %s by the other booking writer (%s) — "
            "booking turn declined inside the bound, cursor not consumed, offer "
            "untouched, cycle reset not executed, nothing sent", thread_id, where, reason)
        return _out(ACTION_THREAD_CONTENDED, detail=reason)

    # ── Flow response (deterministic, no AI) ─────────────────────────────

    def _process_flow_response(
        self,
        ctx: _Context,
        flow_data: dict,
        flow_token: str | None,
        *,
        routing_token: str | None = None,
    ) -> ConversationHandleOut:
        lead = ctx.lead
        assert lead is not None
        state = ctx.state
        assert state is not None

        # M21.2: Motorcycle contact flow — detect by token prefix BEFORE booking handler.
        # Both paths use the same WHATSAPP_FLOW_ID (CLIENT DATA FLOW); the token prefix
        # is the only discriminator. Checked first so a motorcycle response is never
        # mis-routed to the booking handler (which creates a revision and sets AGENDADO).
        # PW1-A (R3RV-01): the prefix is read from the SUBMITTED token (`_handle` passes the
        # value it took before any cycle reset; a direct caller gets the same rule now).
        if routing_token is None:
            routing_token = _flow_routing_token(flow_token, state.flow_booking_token)
        if routing_token.startswith(_MOTO_HANDOFF_TOKEN_PREFIX):
            return self._process_motorcycle_contact_response(ctx, state, flow_data)

        # Route to qualification fallback handlers BEFORE booking flow processing.
        # Detection is by distinctive payload keys, not by token.
        if "tipo_vehiculo" in flow_data:
            return self._process_vehicle_fallback_response(ctx, state, flow_data)
        if "zona_general" in flow_data:
            return self._process_location_fallback_response(ctx, state, flow_data)

        # ── Convergence Phase 3: THE BOOKING AUTHORITY IS THE ONLY BOOKING WRITER ──────
        # Track A's in-session writer that stood here (admission → one-argument advisory
        # lock → revalidation → claim → ThreadRevision → Revision → consume → one CE commit)
        # is deleted, not left dormant: a booking-shaped submission is adjudicated here as
        # reads only and then delegated to `BookingAuthority`, which owns the whole booking
        # transaction in order K. With SMART_BOOKING_CE_ENABLED off nothing books; the
        # customer is handed to a person (`_converged_flow_book`).
        return self._converged_flow_book(ctx, state, flow_data, flow_token)

    # ── G3-7R acceptance helpers ──────────────────────────────────────────────

    def _offer_bound_candidate(self, ctx: _Context, admission):
        """THE candidate for this booking: the one bound into the admitted offer.

        S3. One authority, one resolution, both transports. Before G3-7R there were three
        answers to "which vehicle is being booked?" — this engine's `_focus_candidate`
        (focus pointer, then `status == 'current_focus'`, then a single-candidate fallback,
        else None), the Data Exchange writer's "most recently updated candidate in the
        current cycle" with the focus pointer ignored and never answering None, and a
        SECOND call to `_focus_candidate` inside this same acceptance that was allowed to
        repair state the first call had read. Four ways to disagree about the vehicle whose
        price the customer had already accepted.

        The offer's `candidate_id` is immutable, was recorded when the quote was computed,
        and is what `claim()` compares, so it is the only answer that cannot drift. There is
        no "newest updated row wins" fallback here and no phrase rule: when the bound
        candidate is not available, this returns None and `claim()` refuses
        `OFFER_CANDIDATE_CHANGED`, which is the safe direction.

        Unavailable means any of: the offer carries no candidate binding; the row has been
        deleted; or it sits outside the active conversation cycle, which is the same cycle
        watermark the Data Exchange writer used and this engine's `_execute_cycle_reset`
        sets. A focus CHANGE does not arrive here — it withdraws the offer where it
        happens (`OfferLifecycle.withdraw_if_candidate_moved`), so this method never has to
        adjudicate between an offer and a pointer.
        """
        candidate_id = getattr(admission, "candidate_id", None)
        if candidate_id is None:
            logger.warning(
                "G3-7R thread_id=%s the admitted offer carries no candidate binding — "
                "nothing to book against", ctx.thread.id if ctx.thread else "?")
            return None
        candidate = self.db.get(WhatsAppThreadCandidate, int(candidate_id))
        if candidate is None:
            logger.warning(
                "G3-7R thread_id=%s offer-bound candidate %s no longer exists",
                ctx.thread.id if ctx.thread else "?", candidate_id)
            return None
        if getattr(candidate, "thread_id", None) != getattr(ctx.thread, "id", None):
            logger.error(
                "G3-7R thread_id=%s offer-bound candidate %s belongs to thread %s",
                ctx.thread.id if ctx.thread else "?", candidate_id,
                getattr(candidate, "thread_id", None))
            return None
        # ── the boundary guard, kept (S3) ────────────────────────────────────────
        # A focus MOVE withdraws the offer where it happens, and every production writer of
        # the pointer is covered — but "covered by enumerating the writers" is a weaker
        # claim than "enforced at the boundary", and R2 already established the principle
        # for the other bindings: withdrawal is the first line, the comparison at
        # acceptance is the second, and the second is the one that holds when a path
        # forgot. So an explicit focus pointer that contradicts the offer's binding
        # refuses, rather than booking the offer's candidate on the strength of the
        # withdrawal having fired somewhere earlier.
        #
        # This resolves nothing: it reads no candidate FROM the pointer and has no
        # tie-break. It only refuses when the two disagree. A pointer of None is not a
        # disagreement — it is the documented implicit-focus case, and the binding stands.
        focus_pointer = getattr(ctx.state, "current_focus_candidate_id", None)
        if focus_pointer is not None and int(focus_pointer) != int(candidate_id):
            logger.warning(
                "G3-7R thread_id=%s focus is candidate %s but the offer is bound to %s — "
                "refusing rather than booking the vehicle the customer moved off",
                ctx.thread.id if ctx.thread else "?", focus_pointer, candidate_id)
            return None
        cycle_start = getattr(ctx.state, "current_cycle_started_at", None)
        created_at = getattr(candidate, "created_at", None)
        if cycle_start is not None and created_at is not None and created_at < cycle_start:
            logger.warning(
                "G3-7R thread_id=%s offer-bound candidate %s predates the active cycle "
                "watermark — refusing rather than booking a closed cycle's vehicle",
                ctx.thread.id if ctx.thread else "?", candidate_id)
            return None
        return candidate

    @staticmethod
    def _submitted_booking_slot(state) -> "tuple[date | None, time | None]":
        """The slot this booking would occupy, from the negotiated scheduling facts.

        Returns `(None, None)` when either half is missing or unusable. S5 then refuses: a
        booked ThreadRevision with a NULL `scheduled_date` is not an appointment, and this
        path used to create one.
        """
        sched_date = None
        sched_time = None
        if getattr(state, "preferred_day", None):
            try:
                sched_date = date.fromisoformat(str(state.preferred_day))
            except (ValueError, TypeError):
                sched_date = None
        if getattr(state, "preferred_time", None):
            try:
                sched_time = time.fromisoformat(str(state.preferred_time))
            except (ValueError, TypeError):
                sched_time = None
        if sched_date is None or sched_time is None:
            return (None, None)
        return (sched_date, sched_time)

    def _withdraw_offer_if_focus_moved(self, ctx: _Context, state) -> int:
        """S3. The focus pointer moved — end the offer that was issued for the old vehicle.

        The acceptance-time comparison `offer.candidate_id != candidate_id` used to stand in
        for this withdrawal, and it cannot any more: once the offer's own binding IS the
        candidate authority, the acceptance path has nothing of its own left to disagree
        with. So the withdrawal happens where the move happens. Narrow by construction — it
        does nothing when there is no live offer, when the offer names no candidate, or when
        the focus has not actually moved to a DIFFERENT candidate — so the established
        `VEHICLE_CHANGED` and `LOCATION_CHANGED` withdrawals keep owning their own cases.
        """
        if state is None or getattr(ctx, "thread", None) is None:
            return 0
        from .offer_lifecycle import OfferLifecycle as _OfferLifecycle
        n = _OfferLifecycle(self.db).withdraw_if_candidate_moved(
            state, thread_id=ctx.thread.id,
            candidate_id=getattr(state, "current_focus_candidate_id", None))
        if n:
            from .offer_lifecycle import WithdrawReason as _WR
            self._turn_offer_withdrawal = (_WR.FOCUS_CHANGED, n)
            self._decision_log(ctx, "booking_offer_withdrawn",
                               reason=_WR.FOCUS_CHANGED, offers=n)
        return n

    #: P5 §12. The acknowledgement a customer receives once their Flow submission is
    #: validated, accepted and committed. Says only what is true at that moment: an
    #: operator still has to approve the appointment, so it promises contact, not a turn.
    #: Convergence Phase 3: the SAME object as `booking_acknowledgement.BOOKING_FLOW_RECEIPT_TEXT`,
    #: the one owner-approved success copy, so no second literal can drift from it.
    from .booking_acknowledgement import BOOKING_FLOW_RECEIPT_TEXT as _FLOW_COMPLETION_ACK

    @staticmethod
    def _flow_transaction_key(thread_rev_id) -> "str | None":
        """The idempotency identity of ONE successfully committed Flow transaction.

        G3-5F-BOUNDED-CORRECTION (audit G5F-A01). The key is derived from the committed
        `ThreadRevision` primary key, which is the only thing that is true of exactly this
        transaction and of nothing else: it exists only after the insert, it is identical
        however often the same committed revision is examined, and two legitimate bookings
        on one thread have two of them.

        It carries no customer text, no phone number and no WAMID — a row id and a fixed
        prefix — and it fits `WhatsAppMessage.correlation_id` (String(36)) with room to
        spare, so no migration is required.

        Returns None when there is no committed revision, which the caller treats as
        "not acknowledgeable" rather than as a key.
        """
        try:
            rev_id = int(thread_rev_id)
        except (TypeError, ValueError):
            return None
        return f"{_FLOW_TXN_KEY_PREFIX}{rev_id}" if rev_id else None

    def _flow_ack_already_sent(self, ctx, txn_key: "str | None") -> bool:
        """Has THIS committed Flow transaction already been acknowledged?

        G3-5F-BOUNDED-CORRECTION (audit G5F-A01). The first implementation searched the
        thread's outbound history for the acknowledgement SENTENCE, which made the key
        "this thread, ever" rather than "this transaction". A returning customer who books
        a second inspection on the same WhatsApp thread was therefore silenced permanently:
        the second revision is a different, legitimate transaction and it is owed its own
        acknowledgement.

        The lookup is now an exact match on the transaction key the acknowledgement's own
        outbound row carries in `correlation_id`. No phrase matching remains, so an older
        message that happens to contain the same sentence cannot suppress a later
        transaction, and a customer quoting it back cannot either.

        Fails CLOSED in both directions that matter: a missing key and a ledger read that
        raises both report "already acknowledged", because a second acknowledgement is a
        customer-visible defect while a withheld one is recoverable and logged.
        """
        if not txn_key:
            return True
        try:
            from ..models import WhatsAppMessage
            return self.db.query(WhatsAppMessage.id).filter(
                WhatsAppMessage.thread_id == ctx.thread.id,
                WhatsAppMessage.direction == "out",
                WhatsAppMessage.correlation_id == txn_key,
            ).first() is not None
        except Exception as exc:
            logger.warning("G3-5F flow-ack ledger read failed thread_id=%s: %s",
                           getattr(getattr(ctx, "thread", None), "id", None), exc)
            return True

    def _plan_flow_completion_ack(self, ctx, *, thread_rev_id, flow_token, flow_data) -> bool:
        """Authorize and register the P5 acknowledgement, or record why it was withheld.

        Every condition in `FLOW_COMPLETION_ACK_CONDITIONS` is evidenced from something
        that actually happened in this turn — never assumed, never defaulted to True. A
        missing key is unmet, so relaxing a condition means deleting it from the pinned
        contract, which a test watches.
        """
        expected = getattr(self, "_flow_token_expected", None)
        txn_key = self._flow_transaction_key(thread_rev_id)
        evidence = {
            # The webhook carried a completed submission; an opened or navigated Flow never
            # reaches this function, and neither does a `nfm_reply`-less event.
            "flow_submitted": bool(flow_data),
            # The route fails closed on an unconfigured secret, so a request that arrived
            # here was HMAC-authenticated exactly when the secret is configured. The
            # deliberate `WHATSAPP_WEBHOOK_ALLOW_UNSIGNED` escape is NOT treated as
            # verification: it is a development convenience, and a business fact told to a
            # customer may not rest on one.
            "signature_verified": bool(
                (getattr(self.settings, "whatsapp_app_secret", "") or "").strip()),
            # A token the thread never issued, or one that does not match, is not valid.
            "token_valid": bool(expected is None or not expected or flow_token == expected),
            "payload_complete": bool(flow_data) and any(
                (flow_data.get(k) or "").strip() if isinstance(flow_data.get(k), str)
                else flow_data.get(k) for k in ("nombre", "direccion", "email",
                                                "direccion_texto", "tipo_vendedor")),
            # Parsing, vehicle resolution and scheduling all completed without raising.
            "validation_passed": True,
            # The booking rows are committed: `thread_rev_id` is a persisted primary key.
            "persistence_committed": bool(thread_rev_id) and bool(txn_key),
            "not_already_acknowledged": not self._flow_ack_already_sent(ctx, txn_key),
        }
        authorized, unmet = flow_completion_ack_authorized(evidence)
        plan = getattr(self, "_live_plan", None)
        if not authorized:
            logger.info("G3-5F P5 ack withheld thread_id=%s unmet=%s",
                        getattr(getattr(ctx, "thread", None), "id", None), ",".join(unmet))
            if plan is not None:
                plan.register(Proposition(
                    proposition_id=PropositionId.FLOW_COMPLETION_ACK,
                    kind=ResponseItemKind.FLOW_COMPLETION_ACK, claim_family="booking",
                    render=None,
                    decision_authority=DecisionAuthority.FLOW_TRANSACTION,
                    fact_sources=(FactSource.FLOW_TRANSACTION,),
                    renderer_source=RendererSource.NO_PROSE,
                    policy=ResponsePolicy.P5_FLOW_COMPLETION_ACK, priority=20,
                    included=False, suppression_reason=f"UNMET:{','.join(unmet)}"))
            return False
        if plan is not None:
            plan.register(Proposition(
                proposition_id=PropositionId.FLOW_COMPLETION_ACK,
                kind=ResponseItemKind.FLOW_COMPLETION_ACK, claim_family="booking",
                render=lambda: self._FLOW_COMPLETION_ACK,
                decision_authority=DecisionAuthority.FLOW_TRANSACTION,
                fact_sources=(FactSource.FLOW_TRANSACTION,),
                renderer_source=RendererSource.DETERMINISTIC_TEMPLATE,
                policy=ResponsePolicy.P5_FLOW_COMPLETION_ACK, priority=20))
        self._note_fact_source(FactSource.FLOW_TRANSACTION)
        return True

    # ── SB2B Option B — adapter first, authoritative commit first, CE second ─────────
    #
    # D1 §8.2 (binding rules B-1..B-8). The booking writers here run only when
    # `SMART_BOOKING_CE_ENABLED` is exactly True. With it off there is NO booking writer at all
    # (convergence Phase 3): the legacy CE-W1/CE-W2 writers were deleted and must stay deleted;
    # a booking-shaped turn still reads the ledger first and otherwise hands off truthfully.
    # The booking authority owns the booking, its ledger row and the acknowledgement; CE reads
    # the ledger first, calls the adapter while it holds no write, and only then commits its
    # own state from the committed rows.

    #: Booking actions recorded in `ai_events.action`. Every one is in HANDLED_ACTIONS:
    #: an unhandled action would hand the turn to n8n's legacy booking chain.
    SB_ACTION_CREATED = "booking_created"
    SB_ACTION_REPLAYED = "booking_replayed"
    SB_ACTION_RETRY = "booking_retry_required"
    SB_ACTION_REFUSED = "booking_refused_handoff"

    def _smart_booking_ce_on(self) -> bool:
        return getattr(getattr(self, "settings", None),
                       "smart_booking_ce_enabled", False) is True

    def _sb_note(self, **fields) -> None:
        """Record booking facts for this turn's trace (`booking` block, contract 1.10)."""
        rec = getattr(self, "_sb_trace", None)
        if rec is None:
            rec = self._sb_trace = {}
        rec.update(fields)

    def _sb_trace_payload(self) -> "dict | None":
        rec = getattr(self, "_sb_trace", None)
        if not rec:
            return None
        key = rec.get("key") or ""
        prefix, _, digest = key.partition(":")
        return {
            # never the raw token: the key is already a hash; only its namespace and the
            # first 12 hex characters of that hash are recorded
            "key_prefix": (prefix + ":") if key else None,
            "key_hash_prefix": digest[:12] if digest else None,
            "writer": rec.get("writer"),
            "outcome": rec.get("outcome"),
            "reason": rec.get("reason"),
            "revision_id": rec.get("revision_id"),
            "thread_revision_id": rec.get("thread_revision_id"),
            "occupying": rec.get("occupying"),
            "replayed": bool(rec.get("replayed", False)),
            "lookup_hit": bool(rec.get("lookup_hit", False)),
            "ack_status_before": rec.get("ack_status_before"),
            "ack_status_after": rec.get("ack_status_after"),
            "ack_attempted": bool(rec.get("ack_attempted", False)),
            "identity_outcome": rec.get("identity_outcome"),
            "availability_revalidated": rec.get("availability_revalidated"),
            "ce_synchronized": bool(rec.get("ce_synchronized", False)),
            "manual_scheduling": rec.get("manual_scheduling"),
            "authority_called": bool(rec.get("authority_called", False)),
        }

    def _sb_transaction_clean(self) -> bool:
        """B-3 runtime guard: may CE call the authority now?

        Only when CE's own transaction has WRITTEN nothing to the database. On PostgreSQL that
        is `txid_current_if_assigned() IS NULL` on CE's connection (a SELECT never assigns a
        transaction id; any flushed INSERT/UPDATE does). On SQLite (tests) the driver's
        `in_transaction` is the equivalent: pysqlite opens a transaction only for DML. Any
        other dialect, or a failure to ask, is refused — the guard fails closed.
        """
        try:
            dialect = self.db.get_bind().dialect.name
            if dialect == "postgresql":
                from sqlalchemy import text as _sql
                return self.db.execute(_sql("SELECT txid_current_if_assigned()")).scalar() is None
            if dialect == "sqlite":
                raw = self.db.connection().connection
                return not bool(getattr(getattr(raw, "driver_connection", raw),
                                        "in_transaction", True))
        except Exception as exc:
            logger.error("SB2B txid guard failed thread_turn=%s: %s",
                         getattr(self, "_correlation_id", None), type(exc).__name__)
        return False

    def _sb_ledger_hit(self, key):
        """The committed, replayable ledger result for `key`, read in an isolated session, or
        None. A hit is counted as a replay on the ledger row (its own short transaction); a
        DETACHED key is never a hit (`lookup_booking`)."""
        from .booking_ce_adapter import isolated_session, lookup_booking
        hit = lookup_booking(self.db, key)
        if hit is None:
            return None
        try:
            from .booking_authority import BookingAuthority
            with isolated_session(self.db) as s:
                BookingAuthority(s).note_replay(key)
        except Exception as exc:                 # bookkeeping only; the booking stands
            logger.warning("SB2B replay not counted thread_turn=%s: %s",
                           getattr(self, "_correlation_id", None), type(exc).__name__)
        return hit

    def _sb_flow_ledger_entry(self, ctx, state, event) -> "ConversationHandleOut | None":
        """B-1 + B-2: before the cursor, the cycle reset and the `needs_human` gate, ask the
        booking ledger whether this Flow submission is already booked. A hit is the booking —
        whoever committed it (the Data Exchange endpoint or an earlier CE attempt) — and goes
        straight to acknowledgement recovery and synchronization. It is never `skipped_human`.
        A miss returns None and the turn continues exactly as before."""
        from .booking_ce_adapter import flow_booking_key
        key = flow_booking_key(int(ctx.thread.id), event.flow_token, event.wa_message_id)
        self._sb_note(writer="CE_W1", key=key, lookup_hit=False)
        ctx.inbound_wa_message_id = event.wa_message_id
        hit = self._sb_ledger_hit(key)
        if hit is None:
            return None
        logger.info("SB2B ledger hit thread_id=%s — replaying the committed booking",
                    ctx.thread.id)
        self._sb_note(lookup_hit=True)
        return self._sb_finish_committed(ctx, hit, replayed=True)

    def _sb_refusal(self, ctx, key, *, reason: str, outcome: str, manual=None,
                    token=None, finalise: bool = True, **bindings) -> ConversationHandleOut:
        """Every terminal non-booking answer goes through here: the ledger is re-read FIRST
        (SB2B-RV-01). A hit — a concurrent writer committed this very submission — completes
        as a replay; only a miss becomes the truthful handoff.

        Convergence Phase 4 (P2R-10): on a miss the refusal is made DURABLE before the
        handoff — on its offer by the canonical finaliser (`booking_refusal`, its own session,
        K1 then K2), or, for a refusal that concerns no offer (the OFF switch, the guard), as
        a pre-execute attempt event. Neither changes the customer outcome decided here."""
        hit = self._sb_ledger_hit(key)
        if hit is not None:
            self._sb_note(lookup_hit=True)
            return self._sb_finish_committed(ctx, hit, replayed=True)
        self._sb_refusal_evidence(ctx, key, reason=reason, token=token, finalise=finalise,
                                stage="PRE_EXECUTE" if outcome == "NOT_ATTEMPTED"
                                else "AUTHORITY", **bindings)
        return self._sb_handoff(ctx, action=self.SB_ACTION_REFUSED, reason=reason,
                                outcome=outcome, manual=manual)

    def _sb_refusal_evidence(self, ctx, key, *, reason: str, token=None, finalise: bool = True,
                           stage: str = "PRE_EXECUTE", outcome: str = "REFUSED",
                           **bindings) -> None:
        """P2R-10: the durable evidence of a refused Flow submission. Never raises."""
        from .booking_attempt_events import record_attempt_event
        from .booking_refusal import finalise_offer_refusal
        tid = int(ctx.thread.id)
        attempt = getattr(ctx, "inbound_wa_message_id", None)
        try:
            if finalise and type(token) is str and token.strip():
                # A turn that already holds the thread state (the cycle reset ran in this
                # transaction) finalises inside it; otherwise in the finaliser's own session.
                fin = finalise_offer_refusal(
                    self.db, source="CONVERSATION_ENGINE", thread_id=tid, token=token,
                    observed_reason=reason, attempt_ref=attempt, command_key=key,
                    in_caller_transaction=not self._sb_transaction_clean(), **bindings)
                self._sb_note(refusal_finalised=fin.finalised, refusal_reason=fin.reason)
            else:
                record_attempt_event(self.db, source="CONVERSATION_ENGINE", stage=stage,
                                     outcome=outcome, reason=reason, command_key=key,
                                     attempt_ref=attempt, thread_id=tid)
        except Exception as exc:                 # evidence never changes the outcome
            logger.error("SB2B refusal evidence failed thread_id=%s: %s", tid,
                         type(exc).__name__)

    def _sb_transient(self, ctx, result) -> ConversationHandleOut:
        """CD-04 → Track A's contention contract. A RETRYABLE authority result (K1/K2
        contention, `55P03`, `40P01`, statement timeout, disconnect) wrote nothing in the
        authority, and on a booking-shaped turn CE assigned nothing before delegating (no
        cursor, no attribution), so rolling CE's session back loses nothing. No handoff, no
        `needs_human`, no message: the action stays `reply_required` and a redelivery after
        the holder releases books once."""
        tid = int(ctx.thread.id)
        self.db.rollback()
        self._sb_note(outcome=result.outcome.value, reason=result.reason)
        return self._contended_booking_turn(ctx, tid, result.reason, where="authority",
                                            had_token=True)

    def _converged_flow_book(self, ctx, state, flow_data: dict, flow_token) -> ConversationHandleOut:
        """Convergence Phase 3 — CE-W1, the ONE booking path for a Flow submission.

        CE adjudicates as READS ONLY — admission against the offer table, the offer-bound
        candidate, its canonical location, the submitted slot — then delegates ONE typed
        command carrying the frozen `OfferAcceptance` to `BookingAuthority`, which owns the
        whole booking transaction in order K (K1 thread state, K2 offer, dates, professional,
        revisions, ThreadRevision, lead, ledger) and re-derives every binding from the rows it
        locked. CE holds no lock and has written nothing when it delegates (B-3, guarded).
        After a committed or replayed result: the acknowledgement lease (B-5), then CE's own
        synchronisation (B-4), then the operator e-mail (B-8). A refusal re-reads the ledger
        before its handoff; a transient result writes nothing.

        With SMART_BOOKING_CE_ENABLED off nothing books: the turn is a truthful handoff. The
        flag is an emergency OFF switch, never a selector between two writers — there is no
        other writer left to select."""
        from .booking_authority import (AvailabilityRevalidation, BookingAttachmentPlan,
                                        BookingOutcome, LeadIdentity, LeadUpdate,
                                        OfferAcceptance, PriceRecalculation,
                                        ThreadRevisionAttachment, ThreadStateUpdate, refuse)
        from .booking_ce_adapter import (CE_W1_FALLBACK_TO_MANUAL, CeFlowBookingRequest,
                                         book_from_flow_response, flow_booking_key)
        from .offer_lifecycle import OfferLifecycle, OfferRefusal
        lead = ctx.lead
        tid = int(ctx.thread.id)
        submitted = flow_token.strip() if type(flow_token) is str and flow_token.strip() else None
        inbound = getattr(ctx, "inbound_wa_message_id", None)
        key = flow_booking_key(tid, submitted, inbound)
        self._sb_note(writer="CE_W1", key=key)

        # §13: OFF means no automatic booking writer is reachable — never the old one.
        if not self._smart_booking_ce_on():
            return self._sb_refusal(ctx, key, reason="SMART_BOOKING_DISABLED",
                                    outcome="NOT_ATTEMPTED", finalise=False)

        # The transport mirror can REFUSE, never permit (SB2B; the Data Exchange endpoint's
        # `resolve_context` asks the same): a token the thread no longer holds books nothing.
        expected = (state.flow_booking_token or "").strip() or None
        if submitted is not None and submitted != expected:
            return self._sb_refusal(ctx, key, reason="FLOW_TOKEN_NOT_ACTIVE",
                                    outcome="NOT_ATTEMPTED", token=submitted)

        # ── admission, as a READ: does the token match an ISSUED offer for this thread? ──
        # The offer table is the admission authority, not the transport mirror (G3-7R/S2).
        # Its REPLAY answer is only a hint (design §8): the ledger already missed, so an
        # ACCEPTED offer with no ledger row is refused and surfaced, never confirmed.
        adm = OfferLifecycle(self.db).admit(thread_id=tid, token=submitted)
        self._turn_offer_admission = adm
        if not adm.granted:
            reason = adm.refusal or OfferRefusal.OFFER_ALREADY_ACCEPTED
            return self._sb_refusal(ctx, key, reason=reason, outcome="NOT_ATTEMPTED",
                                    token=submitted)
        # The offer-bound candidate (S3), never `_focus_candidate`; it must be committed,
        # because the authority reads in its own session.
        focus = self._offer_bound_candidate(ctx, adm)
        if focus is None or not self._sb_row_committed(WhatsAppThreadCandidate, focus.id):
            return self._sb_refusal(ctx, key, reason=OfferRefusal.OFFER_CANDIDATE_CHANGED,
                                    outcome="NOT_ATTEMPTED", token=submitted,
                                    candidate_id=None)
        sched_date, sched_time = self._submitted_booking_slot(state)
        if sched_date is None or sched_time is None:
            return self._sb_refusal(ctx, key, reason=OfferRefusal.NO_SUBMITTED_SLOT,
                                    outcome="NOT_ATTEMPTED", token=submitted,
                                    candidate_id=int(focus.id),
                                    tipo_vehiculo=focus.tipo_vehiculo,
                                    cycle_key=int(self._clarification_cycle_key(state)))
        # P2-04: the canonical location of the OFFER-BOUND candidate, by CE's own precedence.
        zone_group, zone_detail = self._inspection_location_for(focus, state)
        if not zone_group and not zone_detail:
            return self._sb_refusal(ctx, key, reason=OfferRefusal.NO_CANONICAL_LOCATION,
                                    outcome="NOT_ATTEMPTED", token=submitted,
                                    candidate_id=int(focus.id),
                                    tipo_vehiculo=focus.tipo_vehiculo,
                                    cycle_key=int(self._clarification_cycle_key(state)))

        # Inputs — the fields the Flow submitted, as typed data only.
        is_website = getattr(state, "is_website_lead", False)
        buyer_email = (flow_data.get("email") or "").strip() or None
        direccion = (flow_data.get("direccion") or flow_data.get("direccion_texto")
                     or "").strip() or None
        seller_type = (flow_data.get("tipo_vendedor") or "").strip() or None
        seller_name = (flow_data.get("nombre_vendedor") or "").strip() or None
        publication_url = (flow_data.get("link_publicacion") or "").strip() or None
        if is_website:
            full_name = (state.customer_name or "").strip() or " ".join(
                p for p in (lead.nombre or "", lead.apellido or "") if p).strip()
            buyer_phone = (lead.telefono or ctx.contact.wa_id or "").strip() or None
            canal = "Formulario web"
        else:
            full_name = (flow_data.get("nombre_apellido") or "").strip()
            buyer_phone = (flow_data.get("telefono") or lead.telefono or "").strip() or None
            canal = (flow_data.get("como_llego") or "").strip() or None
        name_parts = full_name.split() if full_name else []
        acceptance = OfferAcceptance(
            token_fingerprint=adm.fingerprint, thread_id=tid,
            cycle_key=int(self._clarification_cycle_key(state)),
            candidate_id=int(focus.id), tipo_vehiculo=focus.tipo_vehiculo,
            zone_group=zone_group, zone_detail=zone_detail)
        plan = BookingAttachmentPlan(
            thread_revision=ThreadRevisionAttachment(
                thread_id=tid, candidate_id=int(focus.id),
                buyer_name=full_name or None, buyer_phone=buyer_phone, buyer_email=buyer_email,
                seller_type=seller_type, seller_name=seller_name, address=direccion,
                tipo_vehiculo=focus.tipo_vehiculo, marca=focus.marca, modelo=focus.modelo,
                anio=focus.anio, publication_url=publication_url,
                zone_group=zone_group, request_approval=True),
            price=PriceRecalculation(require_inputs=(focus.tipo_vehiculo, zone_group,
                                                     zone_detail)),
            lead=LeadUpdate(
                estado="COORDINAR_DISPONIBILIDAD", flag="ACEPTADO", necesita_humano=True,
                identity=LeadIdentity(nombre=name_parts[0],
                                      apellido=" ".join(name_parts[1:]) or None,
                                      email=buyer_email) if name_parts else None,
                canal_if_empty=canal),
            thread_state=ThreadStateUpdate(link_current_revision=True, last_stage=STAGE_BOOKED,
                                           needs_human=True, consume_flow_token=True),
            offer_acceptance=acceptance)
        fields = {"tipo_vehiculo": focus.tipo_vehiculo, "marca": focus.marca,
                  "modelo": focus.modelo, "anio": focus.anio,
                  "zone_group": zone_group, "zone_detail": zone_detail,
                  "direccion_texto": direccion, "vendedor_tipo": seller_type,
                  "tipo_vendedor": seller_type}
        req = CeFlowBookingRequest(
            thread_id=tid, lead_id=int(lead.id),
            inbound_event_id=inbound, flow_token=submitted,
            requested_date=sched_date, requested_time=sched_time,
            revision_fields=fields, attachments=plan,
            # B-7: the authority revalidates this slot itself, under its locks
            availability=AvailabilityRevalidation(address=direccion or "-",
                                                  zone_group=zone_group, is_holiday=False))
        # B-3, immediately before delegation: CE has written nothing and holds no lock.
        if not self._sb_transaction_clean():
            return self._sb_refusal(ctx, key, reason="CE_TRANSACTION_NOT_CLEAN",
                                    outcome="NOT_ATTEMPTED", finalise=False)
        self._sb_note(authority_called=True)
        try:
            result = book_from_flow_response(self.db, req)
        except Exception as exc:                 # the authority returns typed results;
            logger.error("SB2B adapter raised thread_id=%s: %s",     # this is a defect
                         tid, type(exc).__name__)
            result = refuse(BookingOutcome.INTERNAL_FAILURE, "ADAPTER_EXCEPTION")
        self._sb_note(outcome=result.outcome.value, reason=result.reason,
                      identity_outcome=result.identity_outcome,
                      availability_revalidated=(
                          result.outcome is BookingOutcome.COMMITTED and bool(result.occupying))
                      or result.reason == "SCHEDULE_REVALIDATION")
        if not result.ok:
            # B-1: a committed booking always wins — over KEY_REUSED, over a refusal raced by
            # the Data Exchange endpoint, over every fallback.
            hit = self._sb_ledger_hit(key)
            if hit is not None:
                self._sb_note(lookup_hit=True)
                return self._sb_finish_committed(ctx, hit, replayed=True)
            if result.outcome is BookingOutcome.RETRYABLE_FAILURE:
                self._sb_refusal_evidence(ctx, key, reason=result.reason or "RETRYABLE",
                                        finalise=False, stage="AUTHORITY",
                                        outcome="RETRYABLE")
                return self._sb_transient(ctx, result)
            # P2R-10: a refusal of the offer or of its slot is made durable on that offer;
            # any other failed outcome is recorded as authority-stage evidence.
            if result.outcome in (BookingOutcome.OFFER_REFUSED, BookingOutcome.SLOT_CONFLICT,
                                  BookingOutcome.CALENDAR_EXCEPTION):
                self._sb_refusal_evidence(
                    ctx, key, token=submitted, stage="AUTHORITY",
                    reason=(result.reason if result.outcome is BookingOutcome.OFFER_REFUSED
                            else result.outcome.value),
                    candidate_id=int(focus.id), tipo_vehiculo=focus.tipo_vehiculo,
                    zone_group=zone_group, zone_detail=zone_detail,
                    cycle_key=acceptance.cycle_key)
            else:
                from .booking_attempt_events import outcome_for
                self._sb_refusal_evidence(ctx, key, reason=result.reason or result.outcome.value,
                                        finalise=False, stage="AUTHORITY",
                                        outcome=outcome_for(result.outcome))
        if result.ok and result.occupying:
            out = self._sb_finish_committed(
                ctx, result, replayed=result.outcome is BookingOutcome.IDEMPOTENT_REPLAY)
            if result.outcome is BookingOutcome.COMMITTED:
                self._sb_notify_booking(ctx, result, full_name=full_name or None,
                                        buyer_phone=buyer_phone, buyer_email=buyer_email,
                                        focus=focus, zone_group=zone_group,
                                        zone_detail=zone_detail, direccion=direccion,
                                        seller_type=seller_type, seller_name=seller_name,
                                        sched_date=sched_date, sched_time=sched_time)
            return out
        if result.ok:
            # A committed result that occupies nothing (an operator moved it since): the
            # handoff it recorded stands; CE claims nothing.
            return self._sb_finish_committed(ctx, result, replayed=True)
        manual = None
        if result.outcome in CE_W1_FALLBACK_TO_MANUAL:
            # B-6: only after the lookup above proved there is no booking for this key
            manual = self._sb_manual_request(ctx, state, key=key, focus=focus,
                                             candidate_id=int(focus.id), fields=fields,
                                             day=sched_date, at=sched_time)
        return self._sb_handoff(ctx, action=self.SB_ACTION_REFUSED, reason=result.reason,
                                outcome=result.outcome.value, manual=manual)

    def _sb_row_committed(self, model, row_id) -> bool:
        """B-3 / SB2B-2: is this row visible OUTSIDE CE's transaction? A plan may only
        reference committed rows — the authority reads in its own session."""
        if row_id is None:
            return False
        try:
            from .booking_ce_adapter import isolated_session
            with isolated_session(self.db) as s:
                return s.get(model, row_id) is not None
        except Exception:
            return False

    def _sb_manual_request(self, ctx, state, *, key, focus, candidate_id, fields, day, at):
        """B-6 fallback for SLOT_CONFLICT / CALENDAR_EXCEPTION: a NON-occupying manual
        scheduling request with a booking-owned plan, keyed on this Flow submission so a
        re-entry replays it instead of adding another."""
        from .booking_authority import BookingAttachmentPlan, PriceRecalculation, \
            ThreadRevisionAttachment
        from .booking_ce_adapter import CeSchedulingEscalationRequest, request_manual_scheduling
        plan = BookingAttachmentPlan(
            thread_revision=ThreadRevisionAttachment(
                thread_id=ctx.thread.id, candidate_id=candidate_id,
                tipo_vehiculo=fields.get("tipo_vehiculo"), marca=fields.get("marca"),
                modelo=fields.get("modelo"), anio=fields.get("anio"),
                zone_group=fields.get("zone_group"), non_occupying_status="provisional"),
            price=PriceRecalculation(require_inputs=(
                fields.get("tipo_vehiculo"), fields.get("zone_group"), fields.get("zone_detail")))
            if focus is not None else None)
        try:
            return request_manual_scheduling(self.db, CeSchedulingEscalationRequest(
                thread_id=ctx.thread.id, lead_id=ctx.lead.id, escalation_id=f"flow-fallback|{key}",
                requested_date=day, requested_time=at, revision_fields=dict(fields),
                inbound_event_id=getattr(ctx, "inbound_wa_message_id", None), attachments=plan))
        except Exception as exc:
            logger.error("SB2B manual fallback raised thread_id=%s: %s",
                         ctx.thread.id, type(exc).__name__)
            return None

    def _sb_acknowledge(self, ctx, result) -> "tuple[str | None, object | None]":
        """B-5: the acknowledgement is the ledger row's. `acknowledge_committed_booking` takes
        the ledger claim, sends the owner-approved text through the outbound gate on the
        BOOKING_FLOW path under the command-bound attempt identity (this turn's causal inbound
        is recorded, not used to deduplicate), and records SENT / FAILED / AMBIGUOUS — each in
        its own short transaction, after the booking commit. SENT, an attempt in flight, an
        uncertain or exhausted acknowledgement, or nothing owed → silence (Phase 3-R1: a fresh
        inbound WAMID never admits another attempt). Returns (ack status after, outcome)."""
        from .booking_acknowledgement import acknowledge_committed_booking
        after, outcome = acknowledge_committed_booking(
            self.db, key=result.idempotency_key, ack_status=result.ack_status,
            wa_id=ctx.contact.wa_id, thread_id=int(ctx.thread.id),
            correlation_id=self._turn_correlation_id(),
            causal_inbound_wa_message_id=getattr(ctx, "inbound_wa_message_id", None))
        if outcome is None:
            return after, None
        self._sb_note(ack_attempted=True)
        # transport attribution for this turn (G3-5E fields)
        err = (getattr(outcome, "error", None) or "").upper()
        self._turn_dispatch_path = DispatchPath.TEXT
        self._turn_renderer_source = RendererSource.DETERMINISTIC_TEMPLATE
        if outcome:
            self._turn_dispatch_result = DispatchResult.ALLOWED
            self._turn_text_dispatches = getattr(self, "_turn_text_dispatches", 0) + 1
        elif err.startswith("GATE_BLOCKED_DUPLICATE"):
            self._turn_dispatch_result = DispatchResult.DUPLICATE_SUPPRESSED
        elif err.startswith("GATE_") or err.startswith("PRE_DISPATCH"):
            self._turn_dispatch_result = DispatchResult.BLOCKED
        else:
            self._turn_dispatch_result = DispatchResult.FAILED
        return after, outcome

    def _sb_enter_thread_state(self, thread_id: int) -> "str | None":
        """K1 for CE's OWN short transactions after a booking decision (sync, handoff).

        One flush writes `leads` before `whatsapp_thread_states` (E5), so a CE transaction that
        dirties both would take K8 before K1 and can deadlock a booking that holds K1 (CD-07,
        40P01 measured). These transactions therefore take the thread-state row explicitly
        FIRST, bounded, before anything is flushed. The global `before_flush` rule (K-E) is
        Phase 4; this is the explicit form for the two transactions Phase 3 owns. None to
        proceed; on contention the session has been rolled back and a typed reason returned."""
        from .offer_lifecycle import OfferLifecycle
        with self.db.no_autoflush:
            return OfferLifecycle(self.db).begin_offer_transaction(int(thread_id))

    def _sb_synchronize(self, ctx, *, message_sent: bool) -> bool:
        """B-4: CE synchronization AFTER the authority committed. Order is the rule:
        expire everything CE holds (its in-memory rows predate the authority's writes), take
        K1 explicitly, then set the cursor, then the CE-only writes, then commit. Setting the
        cursor before the expiry would silently discard it (D1 P2b).

        It writes ONLY CE-owned fields — the cursor, attribution (first-write-only lead
        fields), `last_message_at` — and never a booking, Revision, ThreadRevision, offer,
        ledger row, lead booking field or the authority's canonical booking state, which it
        reads (expired, so reloaded) but does not touch. A contended K1 defers the sync and
        loses nothing: the cursor stays unconsumed, so a redelivery re-enters at the ledger
        hit, owes no second acknowledgement, and synchronizes then."""
        tid = int(ctx.thread.id)
        self.db.expire_all()
        contended = self._sb_enter_thread_state(tid)
        if contended is not None:
            logger.warning("SB2B CE sync deferred thread_id=%s (%s) — the booking stands; a "
                           "redelivery synchronizes from the ledger", tid, contended)
            self._sb_note(ce_synchronized=False)
            return False
        state = ctx.state
        state.last_processed_inbound_wa_message_id = getattr(ctx, "inbound_wa_message_id", None)
        self._maybe_set_attribution(ctx, state)          # first-write-only, CE-owned
        if message_sent:
            from zoneinfo import ZoneInfo
            ctx.thread.last_message_at = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
        self.db.commit()
        if message_sent:
            reset_unanswered_alert(self.db, tid)
            self.db.commit()
        self._sb_note(ce_synchronized=True)
        return True

    def _sb_finish_committed(self, ctx, result, *, replayed: bool) -> ConversationHandleOut:
        """COMMITTED or IDEMPOTENT_REPLAY (fresh, raced, or a ledger hit): acknowledgement
        recovery first — after the authority commit, before CE's commit, so a crash in between
        leaves the event re-enterable — then B-4. A re-entry never sends twice: the ledger
        holds an attempt whose sender died as UNCERTAIN instead of reclaiming it, and the gate
        never admits one command-bound attempt identity twice (Phase 3-R1); the lease only
        excludes CONCURRENT claimants."""
        self._sb_note(outcome=("IDEMPOTENT_REPLAY" if replayed else result.outcome.value),
                      reason=result.reason, replayed=replayed,
                      revision_id=result.revision_id,
                      thread_revision_id=result.thread_revision_id,
                      occupying=result.occupying, ack_status_before=result.ack_status,
                      identity_outcome=result.identity_outcome)
        if not result.occupying:
            # a committed non-occupying request: the human handoff it recorded stands
            return self._sb_handoff(ctx, action=self.SB_ACTION_REFUSED,
                                    reason="MANUAL_SCHEDULING_REPLAYED" if replayed
                                    else "MANUAL_SCHEDULING", outcome=result.outcome.value,
                                    already_committed=result, send_text=not replayed)
        ack_after, outcome = self._sb_acknowledge(ctx, result)
        self._sb_note(ack_status_after=ack_after)
        synced = self._sb_synchronize(ctx, message_sent=bool(outcome))
        self._turn_decision_authority = DecisionAuthority.FLOW_TRANSACTION
        self._note_fact_source(FactSource.FLOW_TRANSACTION)
        action = self.SB_ACTION_REPLAYED if replayed else self.SB_ACTION_CREATED
        out = _out(action, wa_message_id=getattr(outcome, "wamid", None),
                   detail=f"ack:{ack_after}" + ("" if synced else ";sync:deferred"),
                   answer_source="FLOW_RESPONSE")
        # Truthful alerting: a reply was owed only when an acknowledgement was owed; it was
        # produced only when one was actually sent in this turn.
        owed = outcome is not None
        out.reply_required = owed
        out.alert_eligible = owed
        out.reply_produced = bool(outcome)
        return out

    def _sb_handoff(self, ctx, *, action: str, reason: str, outcome: str,
                    manual=None, already_committed=None,
                    send_text: bool = True) -> ConversationHandleOut:
        """B-6: a booking that did not happen (or a committed non-occupying request) commits
        a truthful handoff — never a confirmation. Callers re-read the ledger first
        (`_sb_refusal`, or the B-1 lookup after a refused execute).

        For a refusal the authority wrote no CE-owned row, so CE's in-memory state is current
        and is committed as is (an expiry would discard a cycle reset this turn legitimately
        performed). For a committed non-occupying request the plan did write lead and thread
        state, so B-4 applies: expire first. Either way K1 is taken explicitly before anything
        is flushed (CD-07, see `_sb_enter_thread_state`); contention writes nothing and answers
        the transient contract instead.

        Convergence Phase 3: the offer and its transport mirror are left exactly as the
        authority left them. SB2B cleared `flow_booking_token` by hand here, which split the
        mirror from an offer row still ISSUED (G3-7P-R2: the mirror never lies); a refusal
        handoff no longer touches either."""
        tid = int(ctx.thread.id)
        if already_committed is not None:
            # the authority's plan wrote lead / thread-state rows: CE's copies are stale (B-4)
            self.db.expire_all()
        contended = self._sb_enter_thread_state(tid)
        if contended is not None:
            self._sb_note(outcome=outcome, reason=reason)
            return self._contended_booking_turn(ctx, tid, contended, where="handoff",
                                                had_token=True)
        state, lead = ctx.state, ctx.lead
        self._sb_note(outcome=outcome, reason=reason)
        if manual is not None:
            self._sb_note(manual_scheduling=getattr(manual, "outcome", None) and manual.outcome.value)
            if manual.ok and manual.thread_revision_id:
                state.current_revision_id = manual.thread_revision_id
                self._sb_note(revision_id=manual.revision_id,
                              thread_revision_id=manual.thread_revision_id)
        if already_committed is not None and already_committed.thread_revision_id:
            state.current_revision_id = already_committed.thread_revision_id
        state.last_processed_inbound_wa_message_id = getattr(ctx, "inbound_wa_message_id", None)
        self._maybe_set_attribution(ctx, state)          # first-write-only, CE-owned
        state.needs_human = True
        state.last_stage = STAGE_HUMAN
        if lead is not None:
            lead.necesita_humano = True
            lead.estado = "ATENCION_HUMANA"
        self.db.commit()
        self._sb_note(ce_synchronized=True)
        self._decision_log(ctx, "booking_offer_refused", reason=reason, outcome=outcome)
        sent_id = None
        if send_text:
            from .booking_flow_service import BOOKING_HANDOFF_TEXT
            try:
                sent_id = self._send_text_to_wa(ctx, BOOKING_HANDOFF_TEXT)
            except OutboundBlockedError as exc:
                # the handoff is already committed; a blocked transport changes nothing
                logger.warning("SB2B handoff text not sent thread_id=%s outcome=%s",
                               tid, getattr(exc, "gate_outcome", "?"))
                try:
                    self.db.commit()
                except Exception:
                    self.db.rollback()
            # The operator alert Track A's refusal path sent, after every commit, holding no
            # lock; a failure is logged and changes nothing.
            try:
                self._send_fallback_human_review_notification(
                    ctx, ctx.state, reason=f"booking_refused:{reason}"[:60])
            except Exception as exc:
                logger.error("SB2B handoff notification failed thread_id=%s: %s",
                             tid, type(exc).__name__)
        self._turn_decision_authority = DecisionAuthority.FLOW_TRANSACTION
        self._note_fact_source(FactSource.FLOW_TRANSACTION)
        out = _out(action, wa_message_id=sent_id, detail=reason, answer_source="FLOW_RESPONSE")
        out.reply_required = send_text
        out.alert_eligible = send_text
        out.reply_produced = bool(sent_id)
        return out

    def _sb_notify_booking(self, ctx, result, **kw) -> None:
        """B-8: the operator e-mail, only after the authority committed AND CE committed. A
        failed notification never touches the booking (and `notification_sent_at` keeps it
        once per ThreadRevision)."""
        try:
            focus = kw["focus"]
            self._send_booking_notification(
                thread_rev_id=result.thread_revision_id, crm_rev_id=result.revision_id,
                lead_id=ctx.lead.id, buyer_name=kw["full_name"], buyer_phone=kw["buyer_phone"],
                buyer_email=kw["buyer_email"], marca=focus.marca if focus else None,
                modelo=focus.modelo if focus else None,
                anio=str(focus.anio) if focus and focus.anio else None,
                tipo_vehiculo=focus.tipo_vehiculo if focus else None,
                zone_group=kw["zone_group"], zone_detail=kw["zone_detail"],
                address=kw["direccion"], seller_type=kw["seller_type"],
                seller_name=kw["seller_name"],
                scheduled_date=kw["sched_date"].isoformat() if kw["sched_date"] else None,
                scheduled_time=kw["sched_time"].strftime("%H:%M") if kw["sched_time"] else None)
        except Exception as exc:
            logger.error("SB2B booking notification failed thread_id=%s: %s — booking stands",
                         ctx.thread.id, type(exc).__name__)
            self._notification_evidence(result.thread_revision_id, result.revision_id,
                                        outcome="FAILED", reason="NOTIFICATION_EXCEPTION")

    def _notification_evidence(self, thread_rev_id, crm_rev_id, *, outcome: str,
                               reason: str) -> None:
        """Convergence Phase 4 (SB2B-RV-04): an operator booking e-mail that was not sent is
        recorded durably (stage NOTIFICATION) so the operator surface can tell NOT SENT from
        FAILED. Evidence only — after every commit, never touching the booking."""
        from .booking_attempt_events import record_attempt_event
        record_attempt_event(self.db, source="CONVERSATION_ENGINE", stage="NOTIFICATION",
                             outcome=outcome, reason=reason,
                             attempt_ref=f"operator-notification:tr{thread_rev_id}",
                             revision_id=crm_rev_id, thread_revision_id=thread_rev_id)

    def _sb_escalation(self, ctx, state, last_message: str) -> ConversationHandleOut:
        """CE-W2 under Option B. CE already set the handoff (lead, state, token withdrawal)
        in its own session, possibly after flushes. The authority writes booking-owned rows
        only — a NON-occupying request with a provisional ThreadRevision — so it can never
        wait on a row CE holds (B-3). CE commits its handoff after the authority returns, and
        only then is anything sent: the e-mail and the reply (B-8). The handoff survives every
        authority outcome."""
        from .booking_authority import BookingAttachmentPlan, PriceRecalculation, \
            ThreadRevisionAttachment
        from .booking_ce_adapter import (CeSchedulingEscalationRequest, ce_escalation_key,
                                         request_manual_scheduling)
        lead = ctx.lead
        thread_rev_id = state.current_revision_id
        if thread_rev_id is None:
            focus = self._focus_candidate(ctx, repair=False)
            prov_date = prov_time = None
            try:
                prov_date = date.fromisoformat(str(state.active_requested_date)) \
                    if state.active_requested_date else None
            except ValueError:
                pass
            try:
                prov_time = time.fromisoformat(str(state.last_requested_time)) \
                    if state.last_requested_time else None
            except ValueError:
                pass
            grp, det = self._get_active_inspection_location(ctx, state)
            candidate_id = focus.id if focus is not None and self._sb_row_committed(
                WhatsAppThreadCandidate, focus.id) else None
            buyer_name = ((state.customer_name or "").strip() or (lead.nombre or "").strip()
                          or None)
            req = CeSchedulingEscalationRequest(
                thread_id=ctx.thread.id, lead_id=lead.id,
                requested_date=prov_date, requested_time=prov_time,
                revision_fields={
                    "tipo_vehiculo": focus.tipo_vehiculo if focus else None,
                    "marca": focus.marca if focus else None,
                    "modelo": focus.modelo if focus else None,
                    "anio": focus.anio if focus else None,
                    "zone_group": grp, "zone_detail": det,
                    "turno_notas": "Pendiente coordinación manual — cliente insiste con "
                                   "horario no disponible"},
                cycle_key=int(state.current_cycle_start_message_db_id or 0),
                inbound_event_id=ctx.inbound_wa_message_id,
                attachments=BookingAttachmentPlan(
                    thread_revision=ThreadRevisionAttachment(
                        thread_id=ctx.thread.id, candidate_id=candidate_id,
                        buyer_name=buyer_name, buyer_phone=ctx.contact.wa_id or None,
                        buyer_email=lead.email or None,
                        tipo_vehiculo=focus.tipo_vehiculo if focus else None,
                        marca=focus.marca if focus else None,
                        modelo=focus.modelo if focus else None,
                        anio=focus.anio if focus else None, zone_group=grp,
                        non_occupying_status="provisional"),
                    price=PriceRecalculation(require_inputs=(focus.tipo_vehiculo, grp, det))
                    if focus is not None else None))
            self._sb_note(writer="CE_W2", key=ce_escalation_key(req), authority_called=True)
            try:
                result = request_manual_scheduling(self.db, req)
            except Exception as exc:
                logger.error("SB2B CE-W2 adapter raised thread_id=%s: %s",
                             ctx.thread.id, type(exc).__name__)
                result = None
            if result is not None:
                self._sb_note(outcome=result.outcome.value, reason=result.reason,
                              revision_id=result.revision_id,
                              thread_revision_id=result.thread_revision_id,
                              occupying=result.occupying, manual_scheduling=result.outcome.value,
                              replayed=result.outcome.value == "IDEMPOTENT_REPLAY")
            if result is not None and result.ok and result.thread_revision_id:
                state.current_revision_id = result.thread_revision_id
                thread_rev_id = result.thread_revision_id
            else:
                logger.warning("SB2B CE-W2 manual request not recorded thread_id=%s outcome=%s "
                               "— the handoff stands", ctx.thread.id,
                               getattr(getattr(result, "outcome", None), "value", "EXCEPTION"))
        self.db.commit()                              # CE's handoff, after the authority
        self._sb_note(ce_synchronized=True)
        try:                                          # B-8: e-mail only now
            self._send_scheduling_handoff_email(
                ctx=ctx, state=state, thread_rev_id=thread_rev_id, last_message=last_message)
        except Exception as exc:
            logger.error("SB2B handoff e-mail failed thread_id=%s: %s — handoff stands",
                         ctx.thread.id, type(exc).__name__)
        msg = (
            "Entiendo, querés ese horario puntual. "
            "Lo paso con Julián para ver si puede acomodarlo manualmente "
            "y te respondemos por acá apenas lo revise. 🙌"
        )
        sent_id = self._send_text_to_wa(ctx, msg)
        return _out("replied", wa_message_id=sent_id)

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
        total = _format_price_ars(precio_total)
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

    # ── G3-4: semantic availability enquiry — read, never re-derived ─────────

    # ── G3-7 — the reply model's former mutation authority, moved to typed evidence ──

    def _register_general_information_proposition(self, ctx, ai_input_messages) -> bool:
        """Record that this burst is owed a factual answer, then let the turn continue.

        G3-7H P4. The claim is registered; the WORDS are not decided here. Factual authority
        stays exactly where it is — `_compose_secondary_answers` and `_FAQ_TOPIC_ANSWERS`,
        with the DEF-04 business-hours protection that rejects prose asserting hours
        canonical truth denies. This method adds no answer text, invents no fact and reads no
        phrase list; it exists so the FAQ claim survives a turn that also carries a service
        action, which the inherited early return discarded.

        It registers a NON-RENDERING proposition on purpose. `render=None` means the plan
        composes nothing for it, so the customer-visible message count is unchanged and a
        selected clarification Flow remains exactly one message with its approved body.
        Rendering a secondary factual answer ALONGSIDE a Flow needs the response-plan
        composition move that follows this milestone — the registration is the foundation,
        and the dependency is recorded rather than faked.
        """
        plan = getattr(self, "_live_plan", None)
        if plan is None:
            return False
        if getattr(plan, "human_owns_turn", False):
            return False                      # a person owns the turn; nothing is added
        self._turn_general_information_registered = True
        return bool(plan.register(Proposition(
            proposition_id=PropositionId.GENERAL_INFORMATION_ANSWER,
            kind=ResponseItemKind.FAQ_ANSWER,
            claim_family="faq.general_information",
            render=None,                      # P4: registration only, no second message
            decision_authority=DecisionAuthority.DETERMINISTIC_RULE,
            fact_sources=("CANONICAL_FAQ",),
            renderer_source=RendererSource.DETERMINISTIC_TEMPLATE,
            policy="G3-7H-P4-REGISTER-AND-CONTINUE",
            redundant=True,                   # recorded for the trace, composes nothing
            dispatch_path=DispatchPath.TEXT,
        )))

    def _layer_d_register_and_continue_on(self) -> bool:
        """Is Layer D allowed to register its FAQ answer and continue the turn?

        G3-7H P4. Default OFF, and off is byte-compatible with the parent: Layer D answers
        and returns exactly as before. On, Layer D registers a typed
        `GENERAL_INFORMATION_ANSWER` proposition and falls through, so a burst that carries
        both an FAQ and a service action keeps both claims instead of losing the service
        action to an early return 776 lines before reconciliation.

        Deliberately does NOT make a reply-generation call of its own when on. Layer D built
        its messages from the same `ai_input_messages` the one downstream reply call already
        receives, so continuing costs nothing and the turn still makes at most one
        reply-generation call — which is the rule this flag exists to respect.
        """
        return getattr(getattr(self, "settings", None),
                       "layer_d_register_and_continue_enabled", False) is True

    def _reply_renderer_demotion_on(self) -> bool:
        """Is the reply model demoted to a renderer on this deployment?

        Default OFF when the attribute is absent, which is the deployed `840bce0` shape.
        The cutover is gated because the replacement's INPUT is the semantic interpreter's
        output, and proving it covers the durable corpus — the messy quote request, the
        vehicle replacement, the Wild reproductions — needs a live interpretation no
        milestone has yet been authorized to make.

        G3-7H measured exactly what that proof is worth: with the strip made unconditional,
        46 real-customer identities lose their candidate proposal. Until the corpus
        evaluation lands, this flag still decides, and the divergence G3-7G recorded between
        the deployed image and HEAD remains an open deployment blocker rather than a
        resolved one.
        """
        return getattr(getattr(self, "settings", None),
                       "reply_renderer_demotion_enabled", False) is True

    #: The four fields the reply model used to supply. Named once so the boundary that
    #: strips them and the deriver that replaces them cannot drift.
    _REPLY_MUTATION_FIELDS: tuple = ("extracted", "candidate", "lead_flag", "needs_human")

    # ── G3-7-R2 — the candidate reconciler ───────────────────────────────────────
    #
    # The cutover audit found that the reply model had never been only interpreting: it was
    # choosing which CRM candidate a mention referred to, which one became the focus, which
    # one was demoted, and which one a locality attached to. `VehicleEvidence` correctly
    # describes what the customer SAID and must never carry a database id or a mutable CRM
    # status. This is the deterministic step between them.
    #
    # It decides; `_apply_candidate` still writes. Every selection rule below is read off
    # the writer's own certified behaviour and the tests that pin it — nothing here is new
    # product policy, and nothing here matches a phrase.

    @staticmethod
    def _evidence_admitted(item) -> bool:
        """Named, RESOLVED, and not corrected away. The admission test, in one place.

        G3-7B. The test used to check only status and supersession, which is weaker than
        the schema's own `EvidenceItem.resolved` (`status not in UNRESOLVED and value is not
        None`). That gap had a live consequence: for a corrected year the interpreter emits
        `VehicleEvidence(value=None, year=2018, reason="corrected year carried by a
        correction")`, and admitting that row as a vehicle IDENTITY is what let a correction
        fall through to CREATE and become a phantom candidate.

        A valid relation-only `CorrectionEvidence` is unaffected: the parser sets
        `value=True` whenever a relation, a from/to value or a target_ref is present, so
        "he replaced the car" survives even when the discarded car was never named.
        """
        from ..schemas.turn_evidence import UNRESOLVED_STATUSES
        if item is None or getattr(item, "is_superseded", False):
            return False
        status = getattr(getattr(item, "status", None), "value",
                         getattr(item, "status", None))
        if status in UNRESOLVED_STATUSES:
            return False
        resolved = getattr(item, "resolved", None)
        if resolved is None:                      # duck-typed stub: apply the same rule
            resolved = getattr(item, "value", None) is not None
        return bool(resolved)

    @classmethod
    def _admitted_correction_evidence(cls, evidence):
        """The correction this turn states, if the reconciler may admit it."""
        for corr in (getattr(evidence, "corrections", ()) or ()):
            if cls._evidence_admitted(corr):
                return corr
        return None

    @staticmethod
    def _identity_complete(item) -> bool:
        """Make AND model: where the certified writer itself treats a vehicle as identified."""
        return bool((getattr(item, "make", None) or "").strip()) and \
               bool((getattr(item, "model", None) or "").strip())

    @classmethod
    def _creation_permitted(cls, vehicle) -> bool:
        """May this turn's evidence bring a NEW candidate into existence?

        One guard, at the one decision site. The threshold is make AND model because that is
        where `_apply_candidate` already draws it: its create-dedup engages only
        `if _new_marca and _new_modelo`, and its catalogue authority resolves a tipo only
        when both are present. Below that threshold the writer has itself documented that it
        cannot tell a duplicate from a new car — so nothing below it may create one. A year,
        a locality, a category suggestion, a correction relation and noise all fail here.
        """
        return (vehicle is not None
                and cls._evidence_admitted(vehicle)
                and cls._identity_complete(vehicle))

    @classmethod
    def _correction_focus(cls, ctx):
        """`(candidate, hold_reason)` — the ONE row a subjectless correction may target.

        Deliberately stricter than `_focus_candidate`, and deliberately separate from it.
        `_focus_candidate` carries a certified single-row fallback (CL-05) that many
        selection paths rely on; selecting a car the customer just named is a different risk
        from writing attributes onto a row they did not name. So this predicate requires the
        pointer and the status to agree, and never infers focus from a row count.

        Pure: it reads and returns, and unlike `_focus_candidate` it repairs nothing.
        """
        from ..schemas.hybrid_trace import CandidateHoldReason as _H
        state = getattr(ctx, "state", None)
        candidates = list(getattr(ctx, "candidates", None) or ())
        pointer = getattr(state, "current_focus_candidate_id", None) if state else None
        marked = [c for c in candidates
                  if getattr(c, "status", None) == "current_focus"]
        if pointer is None:
            return None, (_H.FOCUS_POINTER_MISSING if marked
                          else _H.FOCUS_NOT_ESTABLISHED)
        row = next((c for c in candidates if getattr(c, "id", None) == pointer), None)
        if row is None:
            return None, _H.FOCUS_POINTER_INVALID
        if len(marked) > 1:
            return None, _H.FOCUS_NOT_UNIQUE
        if getattr(row, "status", None) != "current_focus":
            return None, _H.FOCUS_INCONSISTENT
        return row, None

    def _canonical_zone(self, location) -> tuple:
        """`(zone_group, zone_detail, hit)` for an admitted locality, from the zone table.

        The interpreter says WHICH locality was named; the canonical table says which
        operating group it belongs to. Resolving the group here is what keeps a CRM grouping
        out of `LocationEvidence`, where it would be an invented business fact.
        """
        detail = (getattr(location, "locality", None) or None) if location else None
        if not detail:
            return None, None, None
        try:
            zone = self._extract_zone_from_text(str(detail))
        except Exception:
            zone = None
        if zone is None:
            return None, str(detail), False
        return zone.zone_group, (zone.zone_detail or str(detail)), True

    @classmethod
    def _admitted_vehicle_evidence(cls, evidence):
        """The vehicle the customer named this turn, if the reconciler may admit it.

        The LAST admitted mention wins: within one burst "el 208... no, el Focus" supersedes,
        and the corpus records exactly that rule ("the last stated value supersedes the
        earlier one"). `mention_index` orders them.
        """
        mentions = [v for v in (getattr(evidence, "vehicle_mentions", ()) or ())
                    if cls._evidence_admitted(v)]
        if not mentions:
            return None
        # ── G3-7K W1 ──────────────────────────────────────────────────────────────
        # Survivorship is proven here, at the single admission authority, so EVERY
        # caller inherits it — including `_resolution_to_candidate_data`, which builds
        # the writer's input and used to re-read the burst with the last-wins rule
        # below. One guard, not one per handler.
        identified = [v for v in mentions if any(cls._evidence_identity(v))]
        if identified:
            survivor, distinct_n, year_conflict = cls._vehicle_identity_survivorship(
                evidence)
            # Contested burst: which car the customer kept is unproven, so no caller may
            # be handed one. The resolver holds on this; returning None means a handler
            # that reaches past the resolver still cannot mutate the wrong vehicle.
            if distinct_n != 1 or year_conflict:
                return None
            return survivor
        # No identity token anywhere — a bare year, a lone category. Order may pick the
        # attribute here because no vehicle is being chosen: within a single identity the
        # last stated value supersedes the earlier one, which is the corpus's own rule.
        return sorted(mentions, key=lambda v: getattr(v, "mention_index", 0))[-1]

    # ══ G3-7K W1: survivorship authority ═══════════════════════════════════════════
    #
    # ROOT CAUSE this replaces. `_admitted_vehicle_evidence` returns
    # `sorted(mentions, key=mention_index)[-1]` — last mention wins. That is sound ONLY
    # while `understand/1.20` rule V4 holds ("el que fue descartado va con
    # is_superseded=true. No los inviertas"), because `_evidence_admitted` then excludes
    # the discarded car. G3-7J-R2 proved the model breaks V4: on corpus case B04 — a burst
    # that drops one named car and offers another — it emitted BOTH cars with
    # is_superseded=false and the DISCARDED one LAST, on 3/3 attempts. Last-wins therefore
    # selected the car the customer had just rejected, carrying a year the burst never
    # states for it. Emission order is not authority.
    #
    # The fix is to require survivorship be PROVEN before any vehicle is selected.

    @staticmethod
    def _target_scope_value(correction):
        """The typed scope as a plain value, defaulting to UNRESOLVED. Never a guess."""
        from ..schemas.turn_evidence import CorrectionTargetScope
        if correction is None:
            return None
        raw = getattr(getattr(correction, "target_scope", None), "value",
                      getattr(correction, "target_scope", None))
        try:
            return CorrectionTargetScope(str(raw)).value
        except (ValueError, TypeError):
            return CorrectionTargetScope.UNRESOLVED_REFERENT.value

    @classmethod
    def _vehicle_identity_survivorship(cls, evidence) -> tuple:
        """`(survivor_mention, distinct_identity_count, year_conflict)` for one burst.

        Compares normalized canonical identities, exactly as the writer compares them, so
        duplicate mentions of ONE car collapse and genuinely distinct cars do not. Mentions
        explicitly marked superseded are already excluded by `_evidence_admitted`, which is
        what lets rule 3 work: when the interpreter does obey V4 and marks the discarded
        car, exactly one identity survives here and reconciliation continues normally.

        Partial identities are never assumed equivalent to complete ones. ("ford","focus")
        and ("","focus") are two entries, not one, because nothing in the evidence proves
        the bare model names the same car — so the burst reads as contested and holds. That
        is the required fail-closed reading, not an oversight.

        A bare year or a lone category carries no identity and is not counted: those are
        handled by the existing ladder and must not be promoted into an identity contest.
        """
        admitted = [v for v in (getattr(evidence, "vehicle_mentions", ()) or ())
                    if cls._evidence_admitted(v)]
        identified = [v for v in admitted if any(cls._evidence_identity(v))]
        distinct = {cls._evidence_identity(v) for v in identified}
        if len(distinct) != 1:
            return (None, len(distinct), False)
        # One identity. Two different stated years for it is still an unproven attribute
        # set, so it holds rather than letting an arbitrary mention supply the year.
        years = {y for y in (getattr(v, "year", None) for v in identified) if y}
        if len(years) > 1:
            return (None, 1, True)
        # Prefer the most completely populated mention of the one surviving identity.
        survivor = max(identified, key=lambda v: (
            bool(getattr(v, "make", None)), bool(getattr(v, "model", None)),
            bool(getattr(v, "year", None)), -getattr(v, "mention_index", 0)))
        return (survivor, 1, False)

    @staticmethod
    def _identity_complete_candidate_match(cand, vehicle) -> bool:
        """Does this turn's evidence name THAT candidate well enough to act on it?

        W2. Used only to decide whether an explicitly ALTERNATE referent is uniquely
        resolved. A bare year names no vehicle, so it never resolves an alternate — which
        is exactly the "No, el otro es del 2016." case.
        """
        if vehicle is None:
            return False
        marca, modelo = ConversationEngine._evidence_identity(vehicle)
        if not (marca or modelo):
            return False
        return ConversationEngine._candidate_matches_evidence(
            cand, marca, modelo, getattr(vehicle, "category_suggestion", None))

    def _unresolved_scope_hold(self, ctx, focus, correction, vehicle):
        """`None` to let an UNRESOLVED-scope correction proceed, else the hold reason.

        The interpreter declined to name the referent, so the reconciler establishes it
        from canonical state or refuses. Three outcomes, in order:

        0. the correction carries an explicit `target_ref` — an in-burst reference is its
           own ground (G3), so the reference path owns it and this guard stands aside;
        1. the burst names a vehicle identity — the identity ladder below owns that, and
           it is already grounded, so this guard stands aside;
        2. a `from_value` is supplied and MATCHES the focused candidate's current value
           for a field this correction changes — the customer is demonstrably talking
           about the car in focus, so the current referent is corroborated by canonical
           state rather than asserted by the model;
        3. a `from_value` is supplied and contradicts canonical state, or none is supplied
           at all — hold. `from_value` is a checkable constraint here, never an anchor:
           G3-7J-R2 showed the interpreter can invent one, and an invented value simply
           fails to match and holds.

        This is what keeps "No, el otro es del 2016." (no `from_value`, no identity) from
        touching the focused car while leaving a genuine current-referent correction
        ("...es 2018", `from_value` 2019 against a 2019 candidate) admissible.
        """
        from ..schemas.hybrid_trace import CandidateHoldReason as _H
        if getattr(correction, "target_ref", None):
            # An explicit in-burst reference IS a ground (G3). The reference path resolves
            # it against this turn's mentions and holds on its own terms if it cannot.
            return None
        if vehicle is not None and any(self._evidence_identity(vehicle)):
            return None                       # an identity is present; not our question
        # Delegate focus validity to the existing strict predicate rather than
        # second-guessing it: when it refuses, its own reason (FOCUS_NOT_ESTABLISHED /
        # _POINTER_INVALID / _NOT_UNIQUE) is more informative than a generic scope hold
        # and is just as closed, so this guard stands aside and the ladder reports it.
        target, _why = self._correction_focus(ctx)
        if target is None:
            return None
        raw_from = getattr(correction, "from_value", None)
        if raw_from is None:
            # Nothing ties this correction to the focused car. G3-7D's narrow rule applies:
            # a correction that is not CONFIRMED may not select a target by IMPLICIT focus.
            # Measured basis (W2, 45 calls): "No, el otro es del 2016." came back PROPOSED
            # on 8 of 8 attempts across understand/1.20 and 1.21, while current-referent
            # corrections came back CONFIRMED on 8 of 11. So the hedge is the signal the
            # model does reliably give, and the scope field is the one it does not.
            status = (getattr(getattr(correction, "status", None), "value",
                              getattr(correction, "status", None)))
            if status != "CONFIRMED":
                return _H.TARGET_SCOPE_UNRESOLVED
            return None
        if self._from_value_matches_canonical(target, raw_from):
            return None                       # current referent corroborated
        return _H.TARGET_SCOPE_CONTRADICTED

    @staticmethod
    def _from_value_matches_canonical(target, raw_from) -> bool:
        """Does `from_value` equal a value the canonical row actually holds right now?

        Compared field-agnostically across the attributes a correction can carry, because
        the interpreter does not reliably say WHICH field its `from_value` belongs to.
        String comparison is case/space folded; the year is compared numerically.
        """
        if raw_from is None:
            return False
        text = str(raw_from).strip().lower()
        if not text:
            return False
        anio = getattr(target, "anio", None)
        if anio is not None and text == str(anio):
            return True
        for attr in ("marca", "modelo", "zone_detail", "zone_group"):
            value = getattr(target, attr, None)
            if value and str(value).strip().lower() == text:
                return True
        return False

    @staticmethod
    def _surface_corroboration(vehicle, haystack):
        """The deterministic matcher's verdict on whether the burst supports this model."""
        from .vehicle_catalog import corroborate_vehicle_surface
        _marca, modelo = ConversationEngine._evidence_identity(vehicle)
        if not modelo:
            return None
        return corroborate_vehicle_surface(modelo, haystack)

    def _named_replacement_identity(self, correction, survivor, haystack):
        """May this CONFIRMED replacement ground the incoming car it names, by itself?

        W1 rule 4. A replacement names its own target, so it does not need a pointer to the
        car being dropped — but every one of these must hold, and each is typed or
        canonical, never a phrase, an alias or a position in the burst:

        1. the relation is explicitly REPLACE_CANDIDATE (never inferred from a mention);
        2. the correction itself is CONFIRMED — PROPOSED is not replacement authority;
        3. it carries no `target_ref` (an explicit reference belongs to the rung-4 path);
        4. the surviving identity is COMPLETE — make AND model, so a year-only or
           otherwise incomplete correction can never self-ground;
        5. the correction's own `to_value` names that identity, so the relation and the
           mention are talking about the same car;
        6. the customer said the model token literally;
        7. the vehicle catalogue — not the interpreter — confirms make for that model.

        Condition 7 is what makes 6 sufficient for B01, where the customer said "Focus"
        but never "Ford": the catalogue is already this system's authority for make given a
        model, so the make is canonical rather than model-asserted.
        """
        from ..schemas.turn_evidence import CorrectionRelation
        relation = (getattr(getattr(correction, "relation", None), "value",
                            getattr(correction, "relation", None)))
        if relation != CorrectionRelation.REPLACE_CANDIDATE.value:
            return (None, None)
        status = (getattr(getattr(correction, "status", None), "value",
                          getattr(correction, "status", None)))
        if status != "CONFIRMED":
            return (None, None)
        if getattr(correction, "target_ref", None):
            return (None, None)
        if survivor is None or not self._identity_complete(survivor):
            return (None, None)
        make, model = self._evidence_identity(survivor)
        to_value = getattr(correction, "to_value", None)
        if not to_value:
            return (None, None)
        to_tokens = set(_strip_accents(str(to_value)).lower().replace(",", " ").split())
        if not ({make, model} & to_tokens):
            return (None, None)
        # W2 correction C: corroboration is still required — the W1 review proved it is
        # the ONLY barrier to a model-invented but catalogue-valid identity — but it no
        # longer demands canonical spelling. The deterministic matcher answers "did the
        # customer refer to this car?", so a resolved misspelling corroborates and an
        # ambiguous or absent surface still holds.
        from .vehicle_catalog import corroborate_vehicle_surface, lookup_vehicle
        corroboration = corroborate_vehicle_surface(model, haystack)
        if corroboration.method == "NONE":
            return (None, corroboration)
        match = lookup_vehicle(model)
        if match is None:
            return (None, corroboration)
        if (getattr(match, "marca", None) or "").strip().lower() != make:
            return (None, corroboration)
        return (survivor, corroboration)

    @classmethod
    def _admitted_location_evidence(cls, evidence):
        """The locality the customer named, when it is the INSPECTION location.

        A customer-origin statement ("yo estoy en Pilar pero el auto está en Palermo") is a
        different role and must not bind to the vehicle — the role is already typed.
        """
        from ..schemas.turn_evidence import LocationRole
        for loc in (getattr(evidence, "location_mentions", ()) or ()):
            if not cls._evidence_admitted(loc) or not getattr(loc, "locality", None):
                continue
            role = getattr(getattr(loc, "role", None), "value", getattr(loc, "role", None))
            if role == LocationRole.CUSTOMER_ORIGIN.value:
                continue
            return loc
        return None

    @staticmethod
    def _evidence_identity(item) -> tuple:
        """(marca, modelo) from a vehicle mention, normalized the way the writer compares."""
        return ((getattr(item, "make", None) or "").strip().lower(),
                (getattr(item, "model", None) or "").strip().lower())

    @staticmethod
    def _candidate_identity(cand) -> tuple:
        return ((getattr(cand, "marca", None) or "").strip().lower(),
                (getattr(cand, "modelo", None) or "").strip().lower())

    @classmethod
    def _candidate_matches_evidence(cls, cand, marca: str, modelo: str, tipo) -> bool:
        """Is this existing candidate the vehicle the customer just mentioned?

        Matching is on the identity the customer gave. A mention that names only the make
        ("volvamos con el Peugeot") matches on make alone — which is exactly the M10 case
        the writer's own create-dedup cannot reach, because that dedup requires make AND
        model. A mention naming both must match both. Type compatibility is the writer's
        `_tipo_compatible`, so a MOTO candidate is never repurposed for an AUTO mention.
        """
        c_marca, c_modelo = cls._candidate_identity(cand)
        if marca and c_marca != marca:
            return False
        if modelo and c_modelo and c_modelo != modelo:
            return False
        if modelo and not c_modelo:
            return False
        if not marca and not modelo:
            return False
        return _tipo_compatible(getattr(cand, "tipo_vehiculo", None), tipo)

    def _request_correction_confirmation(self, ctx, state, pending):
        """Ask once, concisely, for confirmation of a provisional patch.

        The reconciler authorizes a typed proposition; the composer writes the words. The
        outbound message's id is stored on the patch, which is what later proves the
        correction was the latest thing we said.

        G3-7F-R2 (review G37FRR-02). This method used to word the question itself and hand
        the finished sentence to transport, which bypassed the response plan entirely: the
        typed proposition it also built went nowhere, and a FAQ answer in the same turn could
        not be composed with the question. It now registers nothing but the typed pairs and
        passes an EMPTY primary to the composition boundary, where
        `_register_correction_propositions` renders it and `plan.compose()` merges it with
        whatever else the turn legitimately owes the customer — one message, one question.

        G3-7F-R2 (review G37FRR-10). A send that Meta refused returns no WAMID and must not
        be reported as `replied`; the patch is also discarded, because an unasked question is
        not a pending proposal.
        """
        self._answer_source = "CORRECTION_CONFIRMATION"
        self._turn_correction_proposition = self._correction_confirmation_proposition(
            pending)
        try:
            sent = self._send_text_to_wa(ctx, "")
        except OutboundBlockedError:
            # Fail closed: an unasked question is not a pending proposal.
            self._store_pending_correction(state, None)
            self._turn_confirmation_bound = "BLOCKED"
            return _out("correction_confirmation_blocked", detail="outbound_disabled")

        # ══ G3-7F-OWNER-RESOLUTION Part A: authority requires the QUESTION, not a send ══
        #
        # Review finding R2R-01. R2 treated "transport returned a WAMID" as proof the
        # question had been asked. It is not: if any REQUIRED producer fails, the plan
        # degrades, `compose()` replaces the whole message with the bounded human-follow-up
        # line, and the gate dispatches THAT — returning a perfectly good WAMID for a
        # message containing no question at all. The patch was then bound to it and a later
        # ACCEPT was admissible.
        #
        # The four structural facts below are checked instead, none of them by reading
        # rendered customer prose (Part A's explicit prohibition):
        #
        #   plan is not None        a plan existed to compose against
        #   not plan.degraded       no REQUIRED producer failed
        #   plan.included(...)      the proposition survived policy and reached the message
        #   dispatch proven         the bound row is an accepted outbound row on this thread
        #
        # `plan.included()` reads the same `_ordered_included()` set `compose()` rendered, so
        # it answers about the actual delivered response rather than about intent.
        plan = getattr(self, "_live_plan", None)
        degraded = bool(getattr(plan, "degraded", False)) if plan is not None else True
        included = bool(
            plan is not None
            and plan.included(PropositionId.CORRECTION_CONFIRMATION_REQUIRED))

        if not sent:
            # Transport accepted nothing — no WAMID, so nothing proves the question exists.
            self._store_pending_correction(state, None)
            self.db.commit()
            logger.warning(
                "OWNER-RESOLUTION correction confirmation not dispatched thread_id=%s — "
                "patch discarded", ctx.thread.id)
            self._turn_confirmation_bound = "NOT_DISPATCHED"
            return _out("correction_confirmation_blocked", detail="dispatch_failed")

        if degraded or not included:
            # A message went out, but it was not the question. No authority may exist, and a
            # person owns the turn — never motorcycle handling (owner policy 19/22).
            self._store_pending_correction(state, None)
            logger.error(
                "OWNER-RESOLUTION confirmation question absent from the delivered response "
                "thread_id=%s degraded=%s included=%s — authority refused, human ownership",
                ctx.thread.id, degraded, included)
            self._turn_confirmation_bound = ("DEGRADED" if degraded else "NOT_INCLUDED")
            self._generic_human_handoff(
                ctx, state, reason=_HANDOFF_RESPONSE_COMPOSITION_FAILED, announce=False)
            return _out("replied", wa_message_id=sent)

        row = None
        try:
            row = (self.db.query(WhatsAppMessage)
                   .filter(WhatsAppMessage.wa_message_id == sent).one_or_none())
        except Exception:
            row = None

        # The row we are about to bind must itself satisfy the delivery-truth predicate, so
        # admission cannot be the first place a bad row is noticed.
        from . import pending_correction as _pcmod
        candidate_patch = pending.with_confirmation_message(getattr(row, "id", None))
        proven, why = _pcmod.confirmation_dispatch_proven(
            candidate_patch, row, thread_id=getattr(ctx.thread, "id", None))
        if not proven:
            self._store_pending_correction(state, None)
            self.db.commit()
            logger.error(
                "OWNER-RESOLUTION confirmation row does not prove dispatch thread_id=%s "
                "reason=%s — patch discarded", ctx.thread.id, why)
            self._turn_confirmation_bound = str(why)
            return _out("correction_confirmation_blocked", detail="dispatch_unproven")

        self._store_pending_correction(state, candidate_patch)
        self.db.commit()
        self._turn_confirmation_bound = "BOUND"
        return _out("replied", wa_message_id=sent)

    def _hold_for_correction_clarification(self, ctx, state, reason):
        """The one clarification this cycle may spend when a correction cannot be applied.

        G3-7F-R2 (review G37FRR-04). The docstring used to say "one bounded clarification"
        and nothing bounded it: no state was written, so every later turn carrying a grounded
        correction re-sent the identical sentence while the stale proposal stood. The budget
        now lives in canonical state and is charged by the caller before this is reached; a
        spent budget terminates the loop under the existing human-routing policy instead.

        G3-7F-R2 (review G37FRR-10): a refused or failed send is not `replied`.
        """
        self._answer_source = "CORRECTION_HOLD"
        self._decision_log(ctx, "correction_hold", reason=reason)
        try:
            sent = self._send_text_to_wa(ctx, _CORRECTION_TARGET_CLARIFICATION)
        except OutboundBlockedError:
            return _out("correction_hold_blocked", detail="outbound_disabled")
        if not sent:
            return _out("correction_hold_blocked", detail="dispatch_failed")
        return _out("replied", wa_message_id=sent)

    def _clarification_for_trace(self):
        """What the turn decided about asking the customer anything. Part H.

        Always present, including on a turn that asked nothing: the measurement the owner
        wants is the RATIO of semantic resolution to Flow fallback, and a field that appears
        only on failures cannot produce it. `g3_8_enforced` is pinned False so no reader can
        mistake this for commercial gate enforcement, which G3-8 still owns.
        """
        route = getattr(self, "_turn_clarification_route", None)
        intro = getattr(self, "_turn_clarification_intro", None)
        attempt = getattr(self, "_turn_clarification_attempt", None)
        _reservation = getattr(self, "_turn_clarification_reservation", None)
        _decision = getattr(self, "_turn_clarification_decision_obj", None)
        return {
            # ── contract 1.9: the attempt lifecycle ───────────────────────────────
            # A turn's trace describes a turn; an attempt spans several. These fields are
            # what let a dispatch turn, a completion turn and a prevented-second turn be
            # joined into ONE lifecycle, which is what makes completion rate measurable
            # rather than merely visible. `attempt_id` is the join key.
            "attempt_id": (getattr(attempt, "id", None)
                           or getattr(self, "_turn_clarification_attempt_id", None)),
            "cycle_key": getattr(self, "_turn_clarification_cycle_key", None),
            "family": (getattr(self, "_turn_clarification_family", None)
                       or getattr(_decision, "family", None)),
            "attempt_status": getattr(attempt, "status", None),
            "reservation": _reservation,
            # Three states, never two: won, already spent, or the store could not answer.
            "already_attempted": (True if _reservation == "ALREADY_ATTEMPTED"
                                  else (False if _reservation == "WON" else None)),
            "store_available": (None if _decision is None
                                else bool(_decision.store_available)),
            "store_fault": getattr(self, "_turn_clarification_store_fault", None),
            # PLANNED is not DISPATCHED, and BLOCKED is not FAILED.
            "dispatch_result": getattr(self, "_turn_clarification_dispatch", None),
            "outbound_message_id": getattr(attempt, "outbound_message_id", None),
            "completion_result": getattr(self, "_turn_clarification_completion", None),
            "resolved_family": getattr(attempt, "resolved_family", None),
            "prevented_second": bool(
                getattr(self, "_turn_clarification_prevented_second", False)),
            "decision_count": getattr(self, "_turn_clarification_decision_count", 0),
            # ── producer attribution: which engine made the family relevant ───────
            "semantic_contributed": getattr(self, "_turn_clar_semantic", None),
            "deterministic_contributed": getattr(self, "_turn_clar_deterministic", None),
            "reconciled_identity_resolved": getattr(self, "_turn_clar_identity", None),
            "reconciled_location_resolved": getattr(self, "_turn_clar_location", None),
            "route": route,
            "reason": getattr(self, "_turn_clarification_reason", None),
            "flow_dispatched": getattr(self, "_turn_clarification_flow", None),
            "explanatory_family": (intro[0] if intro else None),
            "explanatory_planned": bool(intro),
            "generic_handoff": bool(getattr(self, "_turn_generic_handoff", False)),
            "handoff_reason": getattr(self, "_turn_handoff_reason", None),
            "confirmation_bound": getattr(self, "_turn_confirmation_bound", None),
            "corrected_category": getattr(self, "_turn_correction_category", None),
            "moto_proven": bool(getattr(self, "_turn_correction_moto", False)),
            "resolved_without_flow": route in (None, "NO_CLARIFICATION_REQUIRED"),
            "g3_8_enforced": False,
        }

    def _pending_correction_for_trace(self, ctx):
        """The thread's proposal as the trace should record it, plus what it blocked."""
        from . import pending_correction as pc
        pending = pc.load(getattr(ctx, "state", None))
        if pending is None:
            return None
        blocking, reason = pc.has_blocking_pending_correction(getattr(ctx, "state", None))
        payload = pending.to_payload()
        payload["blocking"] = blocking
        payload["blocking_reason"] = reason
        payload["enforced_at_gates"] = False      # G3-8 owns the gates; see G37BR-02
        return payload

    def _provisional_correction_on(self) -> bool:
        """Is provisional correction authority armed? Default OFF.

        G3-7F Commit 3. Flag off, a grounded correction applies as it did in Commit 1: this
        gates only the confirm-before-mutate behaviour and the blocking it implies.
        """
        return getattr(getattr(self, "settings", None),
                       "provisional_correction_enabled", False) is True

    def _latest_outbound_message_id(self, ctx) -> "int | None":
        """The id of the last thing WE said on this thread, from the ledger.

        This is how "was the correction the latest unresolved question?" is answered without
        a word of customer text: if the bot has spoken since the confirmation question, an
        affirmation has more than one possible referent, and ambiguity there is a wrong-car
        mutation. A turn that re-attaches the confirmation updates the stored id, so
        re-asking inside one message keeps it admissible.
        """
        latest = None
        for message in (getattr(ctx, "db_messages", None) or []):
            if getattr(message, "direction", None) != "out":
                continue
            mid = getattr(message, "id", None)
            if isinstance(mid, int) and (latest is None or mid > latest):
                latest = mid
        return latest

    def _pending_correction(self, ctx):
        """This thread's provisional patch, or None."""
        from . import pending_correction as pc
        return pc.load(getattr(ctx, "state", None))

    def _store_pending_correction(self, state, pending) -> None:
        """Persist (or clear) the proposal. PROPOSED state — never canonical truth."""
        state.pending_correction = pending.to_payload() if pending is not None else None

    def _arm_pending_correction(self, ctx, state, resolution):
        """Turn a grounded Tier-2 resolution into a pending patch, or refuse.

        Returns `(pending, refusal_reason)`.

        G3-7F-R2 (review G37FRR-05). The grounding floor is now a PRECONDITION of provisional
        authority, not an independent feature. G3-7F gated arming on the provisional flag
        alone, so `provisional ON / grounding OFF` armed a Tier-2 patch on whatever row the
        v1 resolver picked, carrying `grounding_basis=NONE` — the ungrounded target selection
        this whole gate exists to replace. Three clauses close that, and they are enforced
        here rather than only at the call site because this method is reachable from tests,
        from the trace and from any future caller.

        G3-7F-R2 (review G37FRR-03/04). Legacy mutual exclusion is decided by
        `proposal_disposition`, not by "is anything set": a proposal the correction ANSWERS
        or INVALIDATES is withdrawn by the caller in the same turn and is no longer an
        obstacle. Only a proposal that is outstanding, unanswered and still sound refuses.
        """
        from . import pending_correction as pc
        if not self._correction_grounding_on():
            return None, pc.InvalidationReason.GROUNDING_NOT_ENABLED
        basis = str(getattr(resolution, "grounding_basis", "") or "")
        if basis in ("", "NONE"):
            return None, pc.InvalidationReason.TARGET_NOT_GROUNDED
        target_id = getattr(resolution, "target_candidate_id", None)
        if target_id is None or not any(
                getattr(c, "id", None) == target_id
                for c in (getattr(ctx, "candidates", None) or ())):
            return None, pc.InvalidationReason.TARGET_LOST
        disposition = pc.proposal_disposition(state, resolution.corrected_fields)
        if disposition["conflicting"]:
            return None, pc.InvalidationReason.LEGACY_PROPOSAL_ACTIVE
        pending = pc.PendingCorrection.build(
            thread_id=getattr(getattr(ctx, "thread", None), "id", None),
            turn_id=getattr(self, "_correlation_id", None),
            target_candidate_id=resolution.target_candidate_id,
            grounding_basis=resolution.grounding_basis,
            relation=resolution.target_relation,
            target_scope=getattr(resolution, "target_scope_value", None),
            corrected_fields=resolution.corrected_fields,
            evidence_refs=tuple(r for r in (resolution.correction_evidence_ref,
                                            resolution.vehicle_evidence_ref,
                                            resolution.location_evidence_ref) if r),
            cycle_id=self._reconciler_cycle_id(state),
            origin_message_ids=tuple(
                m for m in (getattr(getattr(self, "_canonical_turn_burst", None),
                                    "ordered_message_ids", None) or ()) if m))
        return pending, None

    def _withdraw_dependent_proposals(self, ctx, state, disposition) -> tuple:
        """Withdraw every legacy proposal this correction answered or invalidated.

        G3-7F-R2, review finding G37FRR-04. The old path refused to arm whenever ANY legacy
        field was set and then asked one unbounded question, so an abandoned
        `flow_booking_token` blocked every future correction forever and re-sent one
        identical sentence each turn. Nothing cleared the token, because nothing on that path
        was allowed to decide the token was spent.

        The decision is a DEPENDENCY, never a clock (Part D.7): a quote, a slot offer, a
        requested date and a live Flow are all computed from the candidate's category and
        zone, so a correction that moves either makes them unsound. A year moves neither —
        `quote()` does not read it and `quote_inputs_unchanged()` does not compare it — so a
        year-only correction leaves a booking proposal standing.

        The field set and the values written are exactly those of `_execute_cycle_reset`,
        which is this project's canonical withdrawal for the same proposals; this is the
        bounded subset of it, and it never touches focus, identity, zone or the candidate.

        Returns the `(resolved, invalidated)` field names actually withdrawn.
        """
        from . import pending_correction as pc
        touched = tuple(disposition.get("resolved", ())) + \
            tuple(disposition.get("invalidated", ()))
        # ── G3-7P (review B-02): withdraw the OFFER, do not merely clear its mirror ──
        # This is the project's own canonical withdrawal for these proposals, and until
        # G3-7P it wrote `flow_booking_token = None` — exactly the write the G3-7N review
        # proved is not a revocation. Measured there: after this path ran, a late tap on the
        # old token returned `booking_created` with a ThreadRevision and a CRM Revision.
        # Authority is the offer row now, so the clearing below is safe AND the offer is
        # explicitly ended with a reason an operator can read.
        if "flow_booking_token" in touched:
            from .offer_lifecycle import WithdrawReason as _TxtWR
            self._withdraw_booking_offer(ctx, state, reason=_TxtWR.TEXT_CORRECTION,
                                         clear_scheduling=False)
        for field in touched:
            current = getattr(state, field, None)
            setattr(state, field, False if current is True else None)
        if touched:
            # The fuzzy pair is one proposal in two columns: replaying a stored burst after
            # its catalogue key is gone would re-ask a question the correction just answered.
            if "pending_fuzzy_catalog_key" in touched:
                state.pending_turn_evidence_text = None
            logger.info(
                "G3-7F-R2 proposals withdrawn thread_id=%s resolved=%s invalidated=%s",
                getattr(getattr(ctx, "thread", None), "id", None),
                disposition.get("resolved", ()), disposition.get("invalidated", ()))
            self._decision_log(ctx, "legacy_proposals_withdrawn",
                               resolved=list(disposition.get("resolved", ())),
                               invalidated=list(disposition.get("invalidated", ())))
        return tuple(disposition.get("resolved", ())), \
            tuple(disposition.get("invalidated", ()))

    @staticmethod
    def _writer_applied(outcome) -> bool:
        """Did `_apply_candidate` actually report a canonical write?

        G3-7F-R2 (review G37FRR-01). Anything that is not a real `(bool, Optional[int])`
        pair is "the writer reported nothing" — the same defensive reading
        `_record_candidate_mutation` already applies, for the same reason: a test double
        standing in for the writer must never authorize a sentence claiming a mutation.
        """
        if isinstance(outcome, tuple) and len(outcome) == 2:
            applied, _created = outcome
            if isinstance(applied, bool):
                return applied
        return False

    def _correction_category(self, ctx, candidate_data, resolution):
        """Carry the catalogue's category with an identity correction. `None` to fail closed.

        G3-7F-R2, review finding G37FRR-06. `_apply_candidate`'s catalogue authority is
        gated on the patch carrying an explicit `tipo_vehiculo`, and a correction patch never
        carried one, so a confirmed `Honda/Titan -> Ford/Focus` left the row typed MOTO. The
        category is a pricing input (`PricingService.quote(db, tipo_vehiculo, ...)`), so the
        corrected vehicle kept being priced as the old one.

        This resolves the category through the SAME authority the rest of the engine uses —
        `_catalog_tipo_for` — and hands it to the writer, where `WILD04R-F6` re-resolves and
        overrides it. It is deliberately NOT added to `corrected_fields`: the customer did
        not state a category, so it is never echoed back or offered for confirmation as
        though they had.

        Fails closed when the catalogue cannot place the corrected vehicle (Part G.7): an
        unknown category is not AUTO, and guessing it would price an unknown car.
        """
        # Part B: the helper enforces the dependency too, so a direct caller — a test, a
        # future handler — cannot reach correction-specific category behaviour with the
        # feature disabled.
        if not self._correction_grounding_on():
            return candidate_data
        if resolution is None or candidate_data.get("action") != "update":
            return candidate_data
        from .pending_correction import IDENTITY_FIELDS
        moved = {f for f, _b, _a in (getattr(resolution, "corrected_fields", ()) or ())}
        if not (moved & IDENTITY_FIELDS):
            return candidate_data            # identity did not move; category cannot have
        target = next((c for c in (getattr(ctx, "candidates", None) or ())
                       if getattr(c, "id", None) == candidate_data.get("id")), None)
        marca = (candidate_data.get("marca")
                 or getattr(target, "marca", None) or "").strip()
        modelo = (candidate_data.get("modelo")
                  or getattr(target, "modelo", None) or "").strip()
        if not (marca and modelo):
            return candidate_data            # a partial identity resolves no category
        tipo = self._catalog_tipo_for(marca, modelo)
        if tipo is None:
            logger.info(
                "G3-7F-R2 corrected identity not in catalogue thread_id=%s marca=%r "
                "modelo=%r — failing closed to human review",
                getattr(getattr(ctx, "thread", None), "id", None), marca, modelo)
            self._decision_log(ctx, "correction_category_unresolved",
                               marca=marca, modelo=modelo)
            return None
        out = dict(candidate_data)
        out["tipo_vehiculo"] = tipo
        # The writer must patch the grounded row IN PLACE. Its tipo-incompatibility guard
        # redirects update->create, which on a correction turn is the phantom candidate
        # G37FRR-07 describes: the customer re-identified ONE car, so there is exactly one
        # row to carry the new identity.
        out["correction_in_place"] = True
        self._turn_correction_category = tipo
        if str(tipo).upper() == "MOTO":
            # Part G.6. The corrected vehicle is a motorcycle; autonomous quoting and
            # scheduling stop under the existing policy. The write still happens first, so
            # canonical state tells the truth about what the customer said.
            self._turn_correction_moto = True
        if getattr(target, "tipo_vehiculo", None) != tipo:
            logger.info(
                "G3-7F-R2 correction category %r->%r candidate=%s thread_id=%s",
                getattr(target, "tipo_vehiculo", None), tipo,
                candidate_data.get("id"),
                getattr(getattr(ctx, "thread", None), "id", None))
        return out

    # ── G3-7F-OWNER-RESOLUTION: generic human ownership ──────────────────────────

    def _generic_human_handoff(self, ctx, state, *, reason: str,
                               announce: bool = True):
        """Hand the conversation to a person, saying nothing about vehicle category.

        Owner policy 19 and review finding R2R-02. R2 reused
        `_motorcycle_human_handoff` for three situations that are not motorcycles — an
        unresolvable category, an exhausted clarification and a correction conflict — and
        that helper tells the customer "Las motos las revisamos de forma manual", files the
        operator alert as `motorcycle_enquiry`, and inserts a `tipo_vehiculo="MOTO"`
        candidate. A Chery Tiggo owner was being told they own a motorcycle.

        This path does the five things a handoff actually owes and nothing else:
        establishes human ownership, alerts the operator once, withdraws proposals that are
        no longer safe to accept, says one neutral sentence through the composer, and leaves
        every grounded vehicle and location fact exactly as it was. It creates no candidate.
        """
        from . import pending_correction as pc
        self._answer_source = "HUMAN_HANDOFF_POLICY"
        state.needs_human = True
        if getattr(ctx, "lead", None) is not None:
            ctx.lead.necesita_humano = True

        # Anything the customer could still say "yes" to is withdrawn: a person is taking
        # over, and a live proposal would let an affirmation land on a dead conversation.
        # The canonical field set and values are `_execute_cycle_reset`'s, as in Part D's
        # withdrawal, and focus/identity/zone are untouched.
        _withdrawn = []
        for field, _family in pc.LEGACY_PROPOSALS:
            if pc.proposal_is_active(state, field):
                current = getattr(state, field, None)
                setattr(state, field, False if current is True else None)
                _withdrawn.append(field)
        if self._pending_correction(ctx) is not None:
            self._store_pending_correction(state, None)
        self.db.commit()

        logger.info("OWNER-RESOLUTION generic human handoff thread_id=%s reason=%s "
                    "withdrawn=%s", ctx.thread.id, reason, _withdrawn)
        self._decision_log(ctx, "generic_human_handoff", reason=reason,
                           withdrawn=_withdrawn)
        self._turn_handoff_reason = reason
        try:
            self._send_fallback_human_review_notification(ctx, state, reason=reason)
        except Exception as exc:                            # never block the handoff
            logger.error("OWNER-RESOLUTION handoff notification failed thread_id=%s: %s",
                         ctx.thread.id, exc)
        self._turn_generic_handoff = True
        if not announce:
            # The turn already occupied its one message — the degraded line, for instance.
            # Ownership and the alert still transfer; P3 forbids a second text and inventing
            # one would be the "whichever handler got here first" behaviour G3-5F removed.
            return None
        try:
            sent = self._send_text_to_wa(ctx, "")
        except OutboundBlockedError:
            return _out("human_handoff_blocked", detail=reason)
        if not sent:
            return _out("human_handoff_blocked", detail=f"{reason}:dispatch_failed")
        return _out("replied", wa_message_id=sent)

    # ── G3-7F-OWNER-RESOLUTION: the clarification router ─────────────────────────

    # ══ G3-7F SEQUENCING: the ONE clarification authority ══════════════════════
    # The sequencing audit established the defect this replaces. The inherited decision ran
    # at `_routing_gate`, 207 lines BEFORE `_apply_candidate`, where the reconciled proposal
    # for the turn does not exist yet — so it re-derived vehicle identity itself, bypassing
    # the numeric-model disambiguation that lives on the candidate path, and answered a
    # customer who said "Focus 2008" with a confirmation for a Peugeot 2008.
    #
    # Everything below consumes the RECONCILED PROPOSAL. Nothing here reads customer text,
    # queries the catalogue, re-runs semantic interpretation, creates a candidate or writes
    # canonical state.

    def _attempt_store(self):
        """The clarification attempt store, on its own transaction. One per engine."""
        store = getattr(self, "_clarification_attempt_store", None)
        if store is None:
            from .clarification_attempt import ClarificationAttemptStore
            store = ClarificationAttemptStore(self.db)
            self._clarification_attempt_store = store
        return store

    def _clarification_cycle_key(self, state) -> int:
        """`COALESCE(current_cycle_start_message_db_id, 0)` — the ONE cycle identity."""
        from .clarification_attempt import cycle_key
        return cycle_key(state)

    def _required_next_information_authorized(self, ctx, state, evidence=None) -> bool:
        """Does THIS TURN's service action require the missing LOCATION in order to proceed?

        Scope is deliberate and narrow: a categorised vehicle, mid-qualification, with no
        resolved inspection locality. That is the one requirement the existing typed schema
        can establish without matching a phrase.

        It is NOT symmetric, and the asymmetry is a recorded limitation rather than an
        oversight. The equivalent vehicle-side requirement would have to fire on a turn where
        no candidate exists at all, and the only typed signal then available is
        `last_intent == PREPURCHASE_INSPECTION`, which persists across turns — so it would
        also fire on the FAQ and soft-close turns that follow, which owner policy 2.8
        forbids. The marker that could suppress those (`_turn_service_question`) is set by
        `_routing_gate`, which now runs AFTER this decision. See the closeout: STOP-02.

        Owner policy 2.8 is preserved by two guards: an admitted semantic service intent that
        is not an inspection or a quote governs and returns False, and a turn the
        deterministic FAQ detector already marked returns False. Stored incompleteness alone
        is never a requirement.
        """
        if state is None or getattr(state, "needs_human", False):
            return False
        if getattr(state, "last_stage", None) not in (STAGE_QUALIFYING, None):
            return False
        focus = self._focus_candidate(ctx)
        if focus is None or not getattr(focus, "tipo_vehiculo", None):
            return False
        # RESOLVED, not merely present. A customer who names a locality the zone resolver
        # cannot place leaves `zone_detail` set and `zone_group` empty — "San Francisco
        # Solano" is the measured case — and treating that as a known location silenced the
        # very question the workflow still needs answered.
        zone_grp, _zone_det = self._get_active_inspection_location(ctx, state)
        if zone_grp or resolve_field_evidence(
                ctx, state, locality_safety=self._locality_safety_on()).location_known():
            return False
        if getattr(self, "_turn_service_question", False):
            return False
        from ..schemas.turn_evidence import ServiceIntentKind
        wanted = (ServiceIntentKind.INSPECTION.value, ServiceIntentKind.QUOTE_REQUEST.value)
        _admitted_any = False
        for intent in (getattr(evidence, "service_intents", ()) or ()):
            if not self._evidence_admitted(intent):
                continue
            _admitted_any = True
            kind = getattr(getattr(intent, "kind", None), "value",
                           getattr(intent, "kind", None))
            if kind in wanted:
                return True
        if _admitted_any:
            return False
        return True

    def _clarification_disposition(self, ctx, state, cand_data):
        """`(resolution, disposition)` for the clarification decision. Reads; never writes.

        Mirrors the correction gate's own entry conditions so the one decision sees the same
        competing-proposal picture the correction path would, without arming anything and
        without mutating state. When the gate's conditions do not hold — the ordinary
        flags-off turn — both values are None and the decision simply has no correction
        inputs, which is the correct reading rather than a missing one.
        """
        # G3-7M F3: `CandidateAction` was referenced below with no import in this scope
        # and none at module level, so this method raised NameError the moment BOTH
        # correction flags were armed — the exact configuration activation requires. The
        # early returns above it hid that for as long as either flag stayed off.
        from ..schemas.hybrid_trace import CandidateAction as _CAction
        resolution = getattr(self, "_turn_candidate_resolution", None)
        if resolution is None:
            return None, None
        if not (self._provisional_correction_on() and self._correction_grounding_on()):
            return resolution, None
        if (cand_data or {}).get("action") != "update":
            return resolution, None
        if getattr(resolution, "action", None) != _CAction.CORRECT_EXISTING:
            return resolution, None
        from . import pending_correction as _pcmod
        return resolution, _pcmod.proposal_disposition(
            state, resolution.corrected_fields)

    def _mark_service_question(self, ai_input_messages: list[str]) -> bool:
        """Record whether this turn is a service question. ONE-DIRECTIONAL, by design.

        The inherited FAQ/soft-close detector is a bounded deterministic interpreter. Its
        phrase set is consulted here for exactly one purpose: to DECLINE to treat a turn as
        service progression, so stored incompleteness cannot turn a question about payment
        methods into a location form (owner policy 2.8). It can never authorize asking the
        customer anything — that is `_turn_clarification_decision`'s job, and it reads only
        typed facts.

        Evaluated before the clarification decision rather than inside `_routing_gate`,
        because the decision now runs first: a suppressor that fires after the thing it is
        meant to suppress suppresses nothing.
        """
        combined = " ".join(ai_input_messages or ())
        marked = bool(
            _is_general_faq_or_soft_close(combined)
            and not _has_vehicle_concern(combined)
            and not _wants_price_quote(combined))
        self._turn_service_question = marked
        return marked

    def _turn_clarification_decision(self, ctx, state, *, reconciled=None,
                                     resolution=None, disposition=None,
                                     ce_vehicle=None, ce_location=False,
                                     current_turn_text: str = ""):
        """The ONE typed clarification decision for this turn. Evaluated at most once.

        Memoized deliberately. Two call sites exist in the handler — the ordinary
        conversational path and the correction-conflict branch — and the owner's rule is
        that one turn produces one decision and one reservation. The second caller reads
        this cached verdict; it does not produce another, and `_execute_clarification` is
        the only place that can reserve.

        RESOLUTION SOURCE. Identity, category and locality come from the RECONCILED PROPOSAL
        and from already-committed canonical state — never from the catalogue, never from
        raw prose. The reconciler is the authority that distinguishes a Ford Focus of year
        2008 from a Peugeot whose model is literally "2008", and this method must not form a
        second opinion about that.
        """
        cached = getattr(self, "_turn_clarification_decision_obj", None)
        if cached is not None:
            return cached

        from .clarification_router import (clarification_decision, ClarificationRoute,
                                           TurnClarificationDecision)
        from .clarification_attempt import (ClarificationFamily,
                                            VEHICLE_CLARIFIABLE_HOLDS)
        from ..schemas.hybrid_trace import CandidateAction as _CA

        if state is not None and getattr(state, "needs_human", False):
            evidence = None
        else:
            try:
                evidence = self._semantic_turn_evidence()
            except Exception:                       # noqa: BLE001 - absence, not denial
                evidence = None

        # ── domain relevance: current-turn evidence, never stored incompleteness ──
        vehicle_mention = (self._admitted_vehicle_evidence(evidence)
                           if evidence is not None else None)
        location_mention = (self._admitted_location_evidence(evidence)
                            if evidence is not None else None)
        _r = reconciled or {}
        _r_marca = (_r.get("marca") or "").strip()
        _r_modelo = (_r.get("modelo") or "").strip()
        _r_tipo = (_r.get("tipo_vehiculo") or "").strip()
        _r_zone_grp = (_r.get("zone_group") or "").strip()
        _r_zone_det = (_r.get("zone_detail") or "").strip()

        # Relevance is HYBRID and symmetric. Owner policy 2.1: a deterministic parser's
        # silence is not a semantic veto, and semantic absence is not a denial of what the
        # deterministic engine saw. A reconciled proposal that touches the family is itself
        # current-turn evidence, because the reconciler only proposes from this turn.
        required_next = self._required_next_information_authorized(ctx, state, evidence)
        # G3-7L: `_admitted_vehicle_evidence` returns None for a CONTESTED burst — that is
        # W1's survivorship guard working. But "we cannot tell which car you mean" is the
        # strongest possible evidence that the turn IS about vehicles, so relevance must
        # read the RAW mentions too, or the very turns that need clarification look like
        # they never mentioned a vehicle. Admission governs WRITING; relevance governs
        # ASKING. Keeping them distinct is the authority separation this milestone is for.
        _raw_vehicle_named = any(
            any(self._evidence_identity(v)) or getattr(v, "year", None)
            for v in (getattr(evidence, "vehicle_mentions", ()) or ()))
        vehicle_relevant = bool(
            vehicle_mention is not None
            or _raw_vehicle_named
            or ce_vehicle is not None
            or _r_marca or _r_modelo or _r_tipo)
        # ── OWNER-REVIEW-R1 R1RV-02: an ambiguous locality is location evidence ──────
        # `_apply_zone_from_text` refused to write a locality this turn because the name
        # belongs to more than one real place, one of them outside the coverage area. That
        # refusal is the strongest possible evidence that the turn IS about location — the
        # same reasoning G3-7L applied to a CONTESTED vehicle burst above. Admission governs
        # WRITING and relevance governs ASKING; this touches only the second.
        _locality_ambiguous = bool(getattr(self, "_turn_locality_ambiguity", None))
        location_relevant = bool(
            location_mention is not None
            or ce_location
            or _locality_ambiguous
            or _r_zone_grp or _r_zone_det)

        # ── resolution: reconciled proposal first, then committed canonical state ──
        focus = self._focus_candidate(ctx)
        # `ce_vehicle` is the typed deterministic observation CE already made for this turn
        # (§11), not a catalogue call made here: the router forms no second opinion about
        # what the customer named. It contributes only to WHETHER the identity is resolved,
        # never to what the identity IS — the reconciled proposal remains the sole source of
        # the values anyone is told about, which is what keeps "Focus 2008" a Ford Focus.
        _ce_marca = (getattr(ce_vehicle, "marca", None) or "").strip()
        _ce_modelo = (getattr(ce_vehicle, "modelo", None) or "").strip()
        identity_resolved = bool(
            (_r_marca and _r_modelo)
            or (_ce_marca and _ce_modelo)
            or ((getattr(focus, "marca", None) or "").strip()
                and (getattr(focus, "modelo", None) or "").strip()))
        category_resolved = bool(
            _r_tipo
            or (getattr(ce_vehicle, "tipo_vehiculo", None) or "").strip()
            or (getattr(focus, "tipo_vehiculo", None) or "").strip())
        _zone_grp, _zone_det = self._get_active_inspection_location(ctx, state)
        _snap = resolve_field_evidence(ctx, state, current_turn_text,
                                      locality_safety=self._locality_safety_on())
        # An ambiguous locality named THIS TURN contradicts whatever is stored, so the
        # location is not resolved for the purpose of this decision. Nothing stored is
        # deleted or overwritten by saying so — the question is asked and the old zone
        # stands until the customer's answer replaces it (R1RV-02).
        location_resolved = bool(
            not _locality_ambiguous
            and (_r_zone_grp or _zone_grp or _snap.location_known()))

        # ── immutable attempt history, read ONLY when it can change the answer ──
        # Attempt history matters exactly when a family is in play AND unresolved, because
        # that is the only situation in which this turn could ask the customer anything. A
        # resolved turn, an irrelevant turn and a correction confirmation never consult the
        # store: reading it would make every ordinary message depend on a second database
        # round trip whose answer could not alter the route.
        # ── G3-7L: decision-level vehicle ambiguity ───────────────────────────────
        # The reconciler refused a vehicle mutation for a reason the Vehicle Flow can
        # answer. Read from the turn's own resolution and from NOTHING else: no flag, no
        # proposed database mutation, no prose. W2's holds emit `action=none` precisely
        # because they refuse to guess, so a gate keyed on a proposed `update` could never
        # see them — which is why they were silent.
        #
        # Clarification authority and mutation authority stay separate here: this grants
        # permission to ASK, never permission to write.
        _res_for_scope = resolution if resolution is not None else getattr(
            self, "_turn_candidate_resolution", None)
        _hold_reason = getattr(_res_for_scope, "hold_reason", None)
        vehicle_decision_unresolved = bool(
            _res_for_scope is not None
            and getattr(_res_for_scope, "action", None) == _CA.HOLD
            and not getattr(_res_for_scope, "mutates", False)
            and _hold_reason in VEHICLE_CLARIFIABLE_HOLDS)

        _vehicle_in_play = vehicle_relevant or required_next
        _location_in_play = location_relevant or required_next
        _could_ask = bool(
            (_vehicle_in_play and not (identity_resolved and category_resolved))
            or (_vehicle_in_play and vehicle_decision_unresolved)
            or (_location_in_play and not location_resolved))
        cycle = self._clarification_cycle_key(state)
        if _could_ask:
            spent = self._attempt_store().spent_families(
                thread_id=getattr(ctx.thread, "id", None), cycle=cycle)
        else:
            from .clarification_attempt import SpentFamiliesResult
            spent = SpentFamiliesResult(frozenset())
        if not spent.available:
            # SEQ-02 fail-closed. An unreadable store cannot prove an attempt is available,
            # and dispatching on an unprovable assumption is how a customer gets asked
            # twice. A person takes the turn; nothing is sent and nothing is claimed.
            decision = TurnClarificationDecision(
                ClarificationRoute.GENERIC_HUMAN_REQUIRED,
                _HANDOFF_CLARIFICATION_STORE_UNAVAILABLE,
                family=None, store_available=False, store_fault=spent.fault)
            self._turn_clarification_decision_count = (
                getattr(self, "_turn_clarification_decision_count", 0) + 1)
            self._turn_clarification_decision_obj = decision
            return decision

        corrected = tuple(getattr(resolution, "corrected_fields", ()) or ())
        route, reason = clarification_decision(
            human_owns_turn=bool(getattr(state, "needs_human", False)),
            correction_target_known=bool(
                getattr(resolution, "target_candidate_id", None) is not None),
            correction_values_known=bool(corrected),
            vehicle_domain_relevant=vehicle_relevant,
            vehicle_identity_resolved=identity_resolved,
            vehicle_decision_unresolved=vehicle_decision_unresolved,
            vehicle_category_resolved=category_resolved,
            vehicle_attempt_spent=ClarificationFamily.VEHICLE in spent,
            vehicle_flow_outstanding=bool(
                getattr(state, "vehicle_fallback_flow_sent", False)),
            location_domain_relevant=location_relevant,
            location_resolved=location_resolved,
            location_attempt_spent=ClarificationFamily.LOCATION in spent,
            location_flow_outstanding=bool(
                getattr(state, "location_fallback_flow_sent", False)),
            other_confirmable_proposal=bool(
                tuple((disposition or {}).get("conflicting", ()))),
            required_next_information_authorized=required_next)

        family = None
        if route == ClarificationRoute.VEHICLE_FLOW_REQUIRED:
            family = ClarificationFamily.VEHICLE
        elif route == ClarificationRoute.LOCATION_FLOW_REQUIRED:
            family = ClarificationFamily.LOCATION

        # Producer attribution for the trace: WHICH engine made each family relevant, and
        # what the reconciler concluded. Recorded as booleans, never as customer text.
        self._turn_clar_semantic = bool(vehicle_mention is not None
                                        or location_mention is not None)
        self._turn_clar_deterministic = bool(
            ce_vehicle is not None or ce_location or _locality_ambiguous
            or _r_marca or _r_modelo or _r_tipo or _r_zone_grp or _r_zone_det)
        self._turn_clar_identity = bool(identity_resolved and category_resolved)
        self._turn_clar_location = bool(location_resolved)

        decision = TurnClarificationDecision(route, reason, family=family)
        self._turn_clarification_decision_count = (
            getattr(self, "_turn_clarification_decision_count", 0) + 1)
        self._turn_clarification_decision_obj = decision
        self._turn_clarification_route = route
        self._turn_clarification_reason = reason
        return decision

    def _complete_clarification_attempt(self, ctx, state, family: str, *,
                                        resolved: bool, result: str):
        """Bind a Flow completion to its attempt. `(attempt, already_terminal)`.

        Steps 1-4 and 7 of the owner's completion order, in one place so the two handlers
        cannot drift: derive the integer cycle key, load the attempt for this thread, cycle
        and family, treat a missing row as unsolicited input, no-op idempotently when the
        attempt is already terminal, and otherwise record the typed completion outcome.

        Clearing the outstanding flag stays with the caller. That is a different fact from
        attempt history, and keeping the two apart is the entire point of the subsystem:
        the flag may be cleared freely and the row may never be.
        """
        from .clarification_attempt import AttemptStatus
        cycle = self._clarification_cycle_key(state)
        store = self._attempt_store()
        attempt = store.load(thread_id=ctx.thread.id, cycle=cycle, family=family)
        self._turn_clarification_family = family
        self._turn_clarification_cycle_key = cycle
        self._turn_clarification_attempt = attempt
        if attempt is None:
            # A submission we cannot tie to an attempt we made. Record nothing, invent
            # nothing, and let the caller proceed defensively.
            logger.warning(
                "G3-7F clarification completion with no attempt thread_id=%s cycle=%s "
                "family=%s — treating as unsolicited", ctx.thread.id, cycle, family)
            self._turn_clarification_completion = "NO_ATTEMPT"
            return None, False
        # `TERMINAL_FOR_AUTOMATION` governs whether this family may be ASKED again. It is
        # deliberately NOT the duplicate test for a completion: `RESERVED` is terminal for
        # asking (the process may have died after reserving, so we cannot prove we did not
        # send) but a customer who just submitted the form plainly did receive it, and
        # discarding their data because our dispatch record is missing would be the wrong
        # way round. Only an attempt that has ALREADY been completed or handed to a person
        # makes a second completion a duplicate.
        _ALREADY_COMPLETED = (AttemptStatus.COMPLETED_RESOLVED,
                              AttemptStatus.COMPLETED_UNRESOLVED,
                              AttemptStatus.INVALID_COMPLETION,
                              AttemptStatus.HANDED_OFF)
        if attempt.status in _ALREADY_COMPLETED:
            logger.info(
                "G3-7F clarification completion is a duplicate thread_id=%s family=%s "
                "status=%s — no second mutation", ctx.thread.id, family, attempt.status)
            self._turn_clarification_completion = "DUPLICATE"
            return attempt, True
        if attempt.status in (AttemptStatus.DISPATCH_BLOCKED,
                              AttemptStatus.DISPATCH_FAILED):
            # We have no evidence this Flow ever reached the customer, so the submission is
            # unsolicited. Record nothing against the attempt — rewriting a blocked dispatch
            # as a completion would falsify the outbound history — but do not discard the
            # customer's data either: the caller proceeds and a person owns the outcome.
            logger.warning(
                "G3-7F clarification completion for a non-dispatched attempt thread_id=%s "
                "family=%s status=%s — treating as unsolicited",
                ctx.thread.id, family, attempt.status)
            self._turn_clarification_completion = "UNSOLICITED"
            return attempt, False
        if attempt.status == AttemptStatus.RESERVED:
            # The customer answering IS the proof of dispatch our record is missing.
            store.transition(attempt.id, AttemptStatus.DISPATCHED,
                             dispatch_result="INFERRED_FROM_COMPLETION")
        status = (AttemptStatus.COMPLETED_RESOLVED if resolved
                  else (AttemptStatus.COMPLETED_UNRESOLVED
                        if result == "UNRESOLVED" else AttemptStatus.INVALID_COMPLETION))
        _claimed = store.transition(
            attempt.id, status, claim=True, completion_result=result,
            resolved_family=bool(resolved))
        if _claimed is None:
            # G3-7M: another delivery of this same completion won the claim. Treat it as
            # the duplicate it is so no second mutation follows.
            logger.info("G3-7M concurrent duplicate completion thread_id=%s family=%s "
                        "— no second mutation", ctx.thread.id, family)
            self._turn_clarification_completion = "DUPLICATE"
            return attempt, True
        self._turn_clarification_attempt = _claimed
        self._turn_clarification_completion = result
        logger.info("G3-7F clarification completion thread_id=%s family=%s -> %s",
                    ctx.thread.id, family, status)
        return self._turn_clarification_attempt, False

    def _execute_clarification(self, ctx, state, decision):
        """Carry out one clarification decision. The ONLY place that reserves an attempt.

        Order is the owner's: reserve atomically, commit the reservation, and only then
        reach transport. A Flow that may already have reached the customer must never be
        un-reserved by a later failure, so the reservation lives in its own transaction.
        """
        from .clarification_attempt import AttemptStatus
        from .clarification_router import ClarificationRoute

        route, reason = decision.route, decision.reason
        self._turn_clarification_route = route
        self._turn_clarification_reason = reason
        self._decision_log(ctx, "clarification_routed", route=route, reason=reason)
        logger.info("G3-7F clarification route thread_id=%s route=%s reason=%s family=%s",
                    getattr(ctx.thread, "id", None), route, reason, decision.family)

        if route == ClarificationRoute.GENERIC_HUMAN_REQUIRED:
            return self._generic_human_handoff(ctx, state, reason=reason)
        if decision.family is None:
            # NO_CLARIFICATION_REQUIRED and CORRECTION_CONFIRMATION_REQUIRED both reach
            # here with no family. Neither asks the customer a clarification question, so
            # there is nothing to execute and nothing to reserve — and handing the turn to
            # a person because it needed no clarification would be the opposite of correct.
            # The caller continues the ordinary path.
            return None

        if not decision.store_available:            # defensive; decided above already
            return self._generic_human_handoff(
                ctx, state, reason=_HANDOFF_CLARIFICATION_STORE_UNAVAILABLE)

        cycle = self._clarification_cycle_key(state)
        store = self._attempt_store()
        reservation = store.reserve(thread_id=ctx.thread.id, cycle=cycle,
                                    family=decision.family)
        self._turn_clarification_reservation = reservation.status
        self._turn_clarification_cycle_key = cycle

        if not reservation.available:
            # Fail closed: no Flow, no claim that an attempt succeeded, no outstanding flag,
            # and no retry inside this turn.
            self._turn_clarification_store_fault = reservation.fault
            self._decision_log(ctx, "clarification_store_unavailable",
                               family=decision.family, fault=reservation.fault)
            return self._generic_human_handoff(
                ctx, state, reason=_HANDOFF_CLARIFICATION_STORE_UNAVAILABLE)

        if reservation.already_attempted:
            attempt = reservation.attempt
            self._turn_clarification_attempt_id = getattr(attempt, "id", None)
            self._turn_clarification_prevented_second = True
            outstanding = bool(getattr(
                state,
                "vehicle_fallback_flow_sent" if decision.family == "VEHICLE"
                else "location_fallback_flow_sent", False))
            self._decision_log(ctx, "clarification_attempt_already_spent",
                               family=decision.family,
                               status=getattr(attempt, "status", None))
            if outstanding:
                # The customer is holding the Flow right now. Asking anything further would
                # give one answer two referents.
                return None
            return self._generic_human_handoff(
                ctx, state, reason=_HANDOFF_CLARIFICATION_ATTEMPT_SPENT)

        attempt = reservation.attempt
        attempt_id = getattr(attempt, "id", None)
        self._turn_clarification_attempt_id = attempt_id

        if decision.family == "VEHICLE":
            result = self._dispatch_vehicle_clarification_flow(ctx, state)
            unavailable_reason = _HANDOFF_VEHICLE_FLOW_UNAVAILABLE
        else:
            result = self._dispatch_location_clarification_flow(ctx, state)
            unavailable_reason = _HANDOFF_LOCATION_FLOW_UNAVAILABLE

        # The dispatchers are the certified ones and set the outstanding flag only after a
        # real `wa_message_id`, so "delivered" below is proven, not assumed.
        if result is None:
            store.transition(attempt_id, AttemptStatus.DISPATCH_FAILED,
                             dispatch_result="NOT_CONFIGURED_OR_FAILED")
            self._turn_clarification_dispatch = "FAILED"
            return self._generic_human_handoff(ctx, state, reason=unavailable_reason)

        if getattr(result, "action", None) == "blocked_dispatch":
            store.transition(attempt_id, AttemptStatus.DISPATCH_BLOCKED,
                             dispatch_result="OUTBOUND_GATE_BLOCKED_KILL_SWITCH")
            self._turn_clarification_dispatch = "BLOCKED"
            return result

        store.transition(attempt_id, AttemptStatus.DISPATCHED,
                         wa_message_id=getattr(result, "wa_message_id", None),
                         dispatch_result="SENT")
        self._turn_clarification_dispatch = "DISPATCHED"
        return result

    def _dispatch_vehicle_clarification_flow(self, ctx, state):
        """The Vehicle Clarification Flow, with the composer's explanatory wording.

        Owner policy 4 and 6: when the reconciler has admitted that the customer is talking
        about a vehicle but the canonical identity is still unresolved, the existing
        published Flow is the clarification — never another free-text question. Returns
        `None` when no Flow is configured so the caller can hand off to a person.

        The explanatory sentence IS the Flow body: owner policy 13 requires the text and the
        Flow to be one response plan with no duplicate, and a Flow button carries its body
        in the same message. The proposition is registered `redundant=True` so the plan
        records the claim for the trace without composing a second message.
        """
        self._turn_clarification_intro = ("VEHICLE", _VEHICLE_CLARIFICATION_INTRO)
        return self._dispatch_vehicle_flow_direct(
            ctx, state, _VEHICLE_CLARIFICATION_INTRO)

    def _dispatch_location_clarification_flow(self, ctx, state):
        """The Location Clarification Flow, with the composer's explanatory wording.

        Owner policy 5 and 6. Returns `None` when no Flow is configured.
        """
        self._turn_clarification_intro = ("LOCATION", _LOCATION_CLARIFICATION_INTRO)
        return self._dispatch_location_flow_direct(
            ctx, state, concern=False, body=_LOCATION_CLARIFICATION_INTRO)

    def _correction_tier(self, resolution) -> str:
        """Which tier this resolution's patch falls in. Atomic: one Tier 2 entry decides."""
        from . import pending_correction as pc
        tier, _price, _identity, _invalidating = pc.classify_patch(
            getattr(resolution, "corrected_fields", ()) or ())
        return tier

    def _correction_confirmation_proposition(self, pending) -> dict:
        """The typed proposition the composer renders. Field/value pairs, never prose.

        Only the corrected facts travel: §12 of the R2 design forbids restating unrelated
        known customer information, and the composer owns the wording.
        """
        from .response_composition import PropositionId
        return {
            "proposition_id": PropositionId.CORRECTION_CONFIRMATION_REQUIRED,
            "pending_id": pending.pending_id,
            "fields": [{"field": f, "proposed": a} for f, _b, a in
                       pending.corrected_fields],
        }

    def _correction_grounding_on(self) -> bool:
        """Is the Grounded-Target Invariant armed on this deployment? Default OFF.

        G3-7F. The floor changes which turns are allowed to mutate — a multi-candidate
        thread stops accepting an unnamed correction — so it waits on certification against
        a real model. Flag off reproduces `reconcile.candidate_selection v1` exactly.
        """
        return getattr(getattr(self, "settings", None),
                       "correction_target_grounding_enabled", False) is True

    def _burst_haystack(self) -> str:
        """The canonical burst, folded for literal grounding. Empty when unavailable.

        Deliberately the SAME normalisation the interpreter's own `_said_literally` uses, so
        "did the customer say this token?" has one answer on both sides of the boundary.
        """
        burst = getattr(self, "_canonical_turn_burst", None)
        texts = list(getattr(burst, "messages", None) or ()) if burst is not None else []
        if not texts:
            return ""
        joined = " ".join(str(t) for t in texts if isinstance(t, str))
        return _strip_accents(joined).lower()

    @staticmethod
    def _value_said_literally(value, haystack: str) -> bool:
        """Did this value appear in the burst? Token presence, never meaning."""
        if value is None or not haystack:
            return False
        from .semantic_interpreter import _said_literally
        return _said_literally(str(value), haystack)

    def _identity_literally_grounded(self, item, haystack: str) -> bool:
        """Make AND model both said literally — the threshold the writer itself draws."""
        make, model = (getattr(item, "make", None), getattr(item, "model", None))
        if not (make or "").strip() or not (model or "").strip():
            return False
        return (self._value_said_literally(make, haystack)
                and self._value_said_literally(model, haystack))

    def _evidence_excludes_current(self, evidence) -> bool:
        """Does any admitted correction point AWAY from the referent under discussion?

        Reads the typed scope only. G3-7F ships the reader; Commit 2 ships the field, so
        until then this is False for every real payload — which is correct: with no way to
        express exclusion, G2 must not pretend one was absent for a reason.
        """
        for corr in (getattr(evidence, "corrections", ()) or ()):
            if not self._evidence_admitted(corr):
                continue
            scope = getattr(getattr(corr, "target_scope", None), "value",
                            getattr(corr, "target_scope", None))
            if scope in ("ALTERNATE_REFERENT", "UNRESOLVED_REFERENT"):
                return True
        return False

    def _grounded_target(self, ctx, evidence, vehicle, correction):
        """`(candidate, basis, hold_reason)` — the Grounded-Target Invariant.

        The three grounds are checked against canonical state and the literal burst. The
        interpreter's `status` and `target_scope` may NARROW a decision; neither is ever the
        ground. That is what makes a mislabelled `CONFIRMED` unable to pick a row.
        """
        from ..schemas.hybrid_trace import (CandidateGroundingBasis as _G,
                                            CandidateHoldReason as _H)
        from ..schemas.turn_evidence import EvidenceStatus
        candidates = list(getattr(ctx, "candidates", None) or ())
        haystack = self._burst_haystack()

        # ── G1: the customer named the car, literally, and it matches exactly one row ──
        if vehicle is not None and self._identity_literally_grounded(vehicle, haystack):
            marca, modelo = self._evidence_identity(vehicle)
            tipo = getattr(vehicle, "category_suggestion", None)
            matches = [c for c in candidates
                       if self._candidate_matches_evidence(c, marca, modelo, tipo)]
            if len(matches) == 1:
                return matches[0], _G.G1_IDENTITY, None

        # ── G3: a typed in-burst reference, superseded mentions included ───────────────
        ref = getattr(correction, "target_ref", None) if correction is not None else None
        if ref:
            item = None
            try:
                item = evidence.item_at(str(ref))
            except Exception:
                item = None
            # G3-7B shipped this gate as `field != "vehicle"`, which rejected the one case
            # the reference exists for: the parser renames a superseded mention to
            # `vehicle_superseded`, so "correct the car I just contrasted away" was refused
            # and the turn fell through to CREATE. Both names are the same kind of item.
            if item is None or getattr(item, "field", None) not in ("vehicle",
                                                                    "vehicle_superseded"):
                return None, _G.NONE, _H.TARGET_UNRESOLVED
            marca, modelo = self._evidence_identity(item)
            tipo = getattr(item, "category_suggestion", None)
            matches = [c for c in candidates
                       if self._candidate_matches_evidence(c, marca, modelo, tipo)]
            if len(matches) == 1:
                return matches[0], _G.G3_REFERENCE, None
            if len(matches) > 1:
                return None, _G.NONE, _H.AMBIGUOUS_MATCH
            return None, _G.NONE, _H.TARGET_CONFLICT

        # ── G2: implicit focus, and ONLY when a singleton makes it unambiguous ─────────
        if len(candidates) != 1:
            return None, _G.NONE, _H.TARGET_NOT_GROUNDED
        if self._evidence_excludes_current(evidence):
            return None, _G.NONE, _H.EXCLUDED_REFERENT
        row, reason = self._correction_focus(ctx)
        if row is None:
            return None, _G.NONE, reason
        status = (getattr(getattr(correction, "status", None), "value",
                          getattr(correction, "status", None))
                  if correction is not None else None)
        if correction is not None and status != EvidenceStatus.CONFIRMED.value:
            # Implicit focus is the one ground with no independent evidence behind it, so
            # it is the one place a weaker commitment may not be spent.
            return None, _G.NONE, _H.IMPLICIT_FOCUS_UNCONFIRMED
        return row, _G.G2_SINGLETON, None

    def _category_established(self, ctx, data: dict) -> bool:
        """G3-7F MOTO floor: is this candidate's category actually known?

        A complete identity with no category is not an ordinary car. The deterministic
        catalogue is the primary authority; a semantic suggestion may contribute; neither
        absent means AUTO. G37DR-04 measured a `Honda/Titan` proposal creating a candidate
        and continuing commercially because no category reached the guard.
        """
        tipo = (data.get("tipo_vehiculo") or "").strip()
        if tipo:
            return True
        marca = (data.get("marca") or "").strip()
        modelo = (data.get("modelo") or "").strip()
        if not marca or not modelo:
            return True          # not a creation-grade identity; other guards own it
        try:
            return self._catalog_tipo_for(marca, modelo) is not None
        except Exception:
            return False

    def _resolve_candidate(self, ctx, state, evidence) -> "CandidateResolution":
        """Which candidate this turn's admitted evidence affects, and what may change.

        Deterministic and side-effect free: it reads evidence and canonical state and
        returns a decision. Nothing is written here — `_apply_candidate` does that through
        the existing writer, so every guard the writer already enforces still runs.

        G3-7B implements the authorized precedence ladder. Its ordering is the safety
        property: an explicit COMPLETE identity (rungs 1-3) always outranks any inference
        from a correction relation, so "no, es un Corolla" can never be misapplied as an
        attribute correction to the car currently in focus. Below that, a correction may
        target an in-burst reference (rung 4) or the strictly-validated canonical focus
        (rung 5), and anything that identifies no vehicle well enough to act on holds
        (rungs 6, 7, 9) or does nothing (rung 10) — it never creates.
        """
        from ..schemas.hybrid_trace import (CandidateAction, CandidateHoldReason,
                                            CandidateResolution, CandidateSelectionBasis)
        from ..schemas.turn_evidence import CorrectionRelation, CorrectionTargetScope

        try:
            focus = self._focus_candidate(ctx, repair=False)
        except TypeError:
            # A suite may substitute this helper with a positional-only stub to control
            # focus. Fall back to the plain call: the CL-05 repair has already run in the
            # proposal step above, so there is nothing stale left for it to write.
            focus = self._focus_candidate(ctx)
        base = dict(
            thread_id=getattr(getattr(ctx, "thread", None), "id", None),
            turn_id=getattr(self, "_correlation_id", None),
            previous_focus_id=getattr(focus, "id", None),
            correlation_id=getattr(self, "_correlation_id", None))

        vehicle = self._admitted_vehicle_evidence(evidence)
        correction = self._admitted_correction_evidence(evidence)
        location = self._admitted_location_evidence(evidence)
        # G3-7B: evidence items carry no `.ref` of their own — only `TurnEvidence` can
        # address them — so the previous `getattr(item, "ref", None)` was always None and
        # the resolution recorded no provenance at all. Addressing goes through the owner.
        loc_ref = self._evidence_ref_of(evidence, location)
        veh_ref = self._evidence_ref_of(evidence, vehicle)
        corr_ref = self._evidence_ref_of(evidence, correction)
        relation = (getattr(getattr(correction, "relation", None), "value",
                            getattr(correction, "relation", None))
                    if correction is not None else None)
        _scope_of = self._target_scope_value
        zone_group, zone_detail, zone_hit = self._canonical_zone(location)
        candidates = list(getattr(ctx, "candidates", None) or ())

        common = dict(vehicle_evidence_ref=veh_ref, location_evidence_ref=loc_ref,
                      correction_evidence_ref=corr_ref, target_relation=relation,
                      zone_lookup_hit=zone_hit, **base)

        def _hold(reason, count=0):
            return CandidateResolution(action=CandidateAction.HOLD, hold_reason=reason,
                                       compatible_candidate_count=count, **common)

        # ══ G3-7K W1 · the Proven-Survivorship Invariant ═══════════════════════════
        # Deliberately ahead of BOTH postures of CORRECTION_TARGET_GROUNDING_ENABLED and
        # ahead of every rung. Which car survives a burst is not a correction-target
        # question, so it must not depend on that flag — and the deployed runtime
        # currently leaves the flag unset, which is exactly the posture that would
        # otherwise be unguarded. Nothing is read from emission order here.
        survivor, _surviving_n, _year_conflict = self._vehicle_identity_survivorship(
            evidence)
        if _surviving_n > 1 or _year_conflict:
            # W2 correction A: two causes, two reasons. A single identity carrying two
            # stated years is NOT a multi-vehicle ambiguity, and reporting it as one made
            # the Inspector contradict `surviving_identity_count` in the same payload.
            return CandidateResolution(
                action=CandidateAction.HOLD,
                hold_reason=(CandidateHoldReason.CONFLICTING_VEHICLE_ATTRIBUTES
                             if _year_conflict
                             else CandidateHoldReason.UNMARKED_MULTI_IDENTITY),
                compatible_candidate_count=0,
                surviving_identity_count=_surviving_n,
                target_scope=_scope_of(correction), **common)

        # ══ G3-7K W2 · the Target-Scope Invariant ══════════════════════════════════
        # Unconditional, and ahead of the flag split, the legacy ladder, singleton
        # fallback, focus mutation, `_apply_candidate` and every canonical write. Which
        # referent a correction is about is not a flag-dependent question.
        #
        # Measured constraint this is built around: across 17 attempts on three families
        # and two prompt versions (understand/1.20 and 1.21), gpt-4o-mini emitted
        # UNRESOLVED_REFERENT for BOTH "the other one is a 2016" and "no, it's a 2022".
        # So scope cannot be taken from the model today. It is taken from the model when
        # the model supplies it, and otherwise established against canonical state — or
        # held. Nothing here reads customer wording.
        # ── W2 correction C, unconditional half: no phantom vehicles ──────────────
        # The W1 review proved corroboration is the ONLY barrier between a model-invented
        # but catalogue-valid identity and a new candidate. W1 placed it on the G4 path
        # only, which left the DEPLOYED posture (grounding flag unset) creating phantoms.
        # A complete identity the thread does not have would CREATE, so it must be
        # corroborated by the customer's own words first — in either posture.
        #
        # Conditioned on a reconstructed burst: with no burst there is nothing to
        # corroborate against, and pre-existing behaviour stands rather than holding on
        # the absence of evidence about evidence.
        if vehicle is not None and self._identity_complete(vehicle):
            _burst = self._burst_haystack()
            _m, _mo = self._evidence_identity(vehicle)
            _already = [c for c in candidates if self._candidate_matches_evidence(
                c, _m, _mo, getattr(vehicle, "category_suggestion", None))]
            if _burst and not _already:
                _c = self._surface_corroboration(vehicle, _burst)
                if _c is not None and _c.method == "NONE":
                    return CandidateResolution(
                        action=CandidateAction.HOLD,
                        hold_reason=CandidateHoldReason.CREATE_UNCONFIRMED,
                        compatible_candidate_count=0,
                        surviving_identity_count=_surviving_n or None,
                        corroboration_method=_c.method,
                        corroboration_score=_c.score,
                        corroboration_gap=_c.gap,
                        corroboration_reason=(_c.reason or None),
                        target_scope=_scope_of(correction), **common)

        # Scoped to CORRECT_EXISTING on purpose. The hazard this closes is an ATTRIBUTE
        # landing on an unestablished referent. A switch relation selects a referent and
        # writes nothing, so its existing SOLE_NON_FOCUS rung stays in charge; a
        # replacement names its own target and is handled by G4 below.
        if (correction is not None
                and relation == CorrectionRelation.CORRECT_EXISTING.value):
            _scope = _scope_of(correction)
            _scoped = dict(common)
            if _scope == CorrectionTargetScope.ALTERNATE_REFERENT.value:
                # Explicitly NOT the referent under discussion. The focus is never the
                # fallback, and "it is the only candidate" is never a resolution.
                _alt = [c for c in candidates
                        if focus is None or c.id != getattr(focus, "id", None)]
                if len(_alt) != 1 or not self._identity_complete_candidate_match(
                        _alt[0], vehicle):
                    return CandidateResolution(
                        action=CandidateAction.HOLD,
                        hold_reason=CandidateHoldReason.EXCLUDED_REFERENT,
                        compatible_candidate_count=len(_alt),
                        surviving_identity_count=_surviving_n or None,
                        target_scope=_scope, **_scoped)
            elif _scope == CorrectionTargetScope.UNRESOLVED_REFERENT.value:
                hold = self._unresolved_scope_hold(ctx, focus, correction, vehicle)
                if hold is not None:
                    return CandidateResolution(
                        action=CandidateAction.HOLD, hold_reason=hold,
                        compatible_candidate_count=0,
                        surviving_identity_count=_surviving_n or None,
                        target_scope=_scope, **_scoped)

        def _select(target, basis):
            """Rungs 1-3 and 8: the customer identified a car; select it (focus may move)."""
            is_focus = focus is not None and target.id == getattr(focus, "id", None)
            return CandidateResolution(
                action=(CandidateAction.REUSE_FOCUS if is_focus
                        else CandidateAction.REUSE_EXISTING),
                target_candidate_id=target.id, resulting_focus_id=target.id,
                focus_transition_required=not is_focus,
                locality_binding_permitted=bool(zone_detail),
                zone_group=zone_group, zone_detail=zone_detail,
                compatible_candidate_count=1, selection_basis=basis, **common)

        def _correct(target, basis):
            """Rungs 4-5: correct an existing row's ATTRIBUTES. Focus never moves."""
            fields = self._corrected_attributes(
                target, vehicle, correction,
                zone_group if zone_hit else None, zone_detail if zone_hit else None)
            return CandidateResolution(
                action=CandidateAction.CORRECT_EXISTING,
                target_candidate_id=target.id,
                resulting_focus_id=getattr(focus, "id", None),
                focus_transition_required=False,
                locality_binding_permitted=bool(zone_hit and zone_detail),
                zone_group=(zone_group if zone_hit else None),
                zone_detail=(zone_detail if zone_hit else None),
                compatible_candidate_count=1, selection_basis=basis,
                corrected_fields=fields, **common)

        def _compatible_for(item):
            marca, modelo = self._evidence_identity(item)
            tipo = getattr(item, "category_suggestion", None)
            return [c for c in candidates
                    if self._candidate_matches_evidence(c, marca, modelo, tipo)]

        # ══ G3-7F: the Grounded-Target Invariant ═══════════════════════════════════
        # Flag off, everything below this block is `reconcile.candidate_selection v1`,
        # byte for byte. Flag on, a correction's target must be established by a ground
        # the reconciler verifies itself, and a mutation that cannot be grounded holds.
        if self._correction_grounding_on():
            from ..schemas.hybrid_trace import CandidateGroundingBasis as _G
            haystack = self._burst_haystack()

            def _hold_g(reason, basis=_G.NONE, count=0):
                return CandidateResolution(
                    action=CandidateAction.HOLD, hold_reason=reason,
                    compatible_candidate_count=count, grounding_basis=basis,
                    literally_grounded=False, **common)

            # W1 rule 4: an explicit CONFIRMED replacement grounds the car it names.
            # Ahead of `_grounded_target` because that helper asks "which EXISTING row does
            # this correct?" — the wrong question for a replacement, whose target is a car
            # the thread does not have yet. It is what returned EXCLUDED_REFERENT for every
            # legitimate B01/B03 replacement. The guard above has already run, so a
            # contested burst can never reach here.
            if correction is not None:
                incoming, _corr = self._named_replacement_identity(
                    correction, survivor, haystack)
                if incoming is not None and not _compatible_for(incoming):
                    _marca, _modelo = self._evidence_identity(incoming)
                    return CandidateResolution(
                        action=CandidateAction.CREATE,
                        focus_transition_required=focus is not None,
                        locality_binding_permitted=bool(zone_detail),
                        zone_group=zone_group, zone_detail=zone_detail,
                        compatible_candidate_count=0,
                        selection_basis=CandidateSelectionBasis.EVIDENCE_IDENTITY,
                        grounding_basis=_G.G4_NAMED_REPLACEMENT,
                        literally_grounded=(_corr.method == "EXACT"
                                            if _corr is not None else False),
                        surviving_identity_count=1,
                        target_scope=_scope_of(correction),
                        corroboration_method=getattr(_corr, "method", None),
                        corroborated_identity=(f"{_marca} {_modelo}".strip()
                                               if _corr is not None else None),
                        corroboration_score=getattr(_corr, "score", None),
                        corroboration_gap=getattr(_corr, "gap", None),
                        corroboration_reason=(getattr(_corr, "reason", None) or None),
                        **common)

            # A correction — stated, or implied by a corrected value with no new identity.
            if correction is not None:
                target, basis, reason = self._grounded_target(
                    ctx, evidence, vehicle, correction)
                if target is None:
                    return _hold_g(reason)
                fields = self._corrected_attributes(
                    target, vehicle, correction,
                    zone_group if zone_hit else None, zone_detail if zone_hit else None)
                return CandidateResolution(
                    action=CandidateAction.CORRECT_EXISTING,
                    target_candidate_id=target.id,
                    resulting_focus_id=getattr(focus, "id", None),
                    focus_transition_required=False,
                    locality_binding_permitted=bool(zone_hit and zone_detail),
                    zone_group=(zone_group if zone_hit else None),
                    zone_detail=(zone_detail if zone_hit else None),
                    compatible_candidate_count=1,
                    selection_basis=(CandidateSelectionBasis.FOCUS_POINTER
                                     if basis == _G.G2_SINGLETON
                                     else CandidateSelectionBasis.EVIDENCE_IDENTITY),
                    grounding_basis=basis, corrected_fields=fields,
                    literally_grounded=all(
                        self._value_said_literally(after, haystack)
                        for _f, _b, after in fields) if fields else False,
                    **common)

            # No correction stated. A complete identity may select or create; creation now
            # additionally requires that the customer said make AND model literally.
            if vehicle is not None and self._identity_complete(vehicle):
                compatible = _compatible_for(vehicle)
                if len(compatible) > 1:
                    return _hold_g(CandidateHoldReason.AMBIGUOUS_MATCH,
                                   count=len(compatible))
                if len(compatible) == 1:
                    sel = _select(compatible[0], CandidateSelectionBasis.EVIDENCE_IDENTITY)
                    import dataclasses as _dc
                    return _dc.replace(sel, grounding_basis=_G.G1_IDENTITY,
                                       literally_grounded=True)
                if not self._identity_literally_grounded(vehicle, haystack):
                    return _hold_g(CandidateHoldReason.CREATE_UNCONFIRMED)
                return CandidateResolution(
                    action=CandidateAction.CREATE,
                    focus_transition_required=focus is not None,
                    locality_binding_permitted=bool(zone_detail),
                    zone_group=zone_group, zone_detail=zone_detail,
                    compatible_candidate_count=0,
                    selection_basis=CandidateSelectionBasis.EVIDENCE_IDENTITY,
                    grounding_basis=_G.G1_IDENTITY, literally_grounded=True, **common)

            if vehicle is not None and any(self._evidence_identity(vehicle)):
                compatible = _compatible_for(vehicle)
                if len(compatible) > 1:
                    return _hold_g(CandidateHoldReason.AMBIGUOUS_MATCH,
                                   count=len(compatible))
                if len(compatible) == 1:
                    sel = _select(compatible[0], CandidateSelectionBasis.EVIDENCE_IDENTITY)
                    import dataclasses as _dc
                    return _dc.replace(sel, grounding_basis=_G.G1_IDENTITY)
                return _hold_g(CandidateHoldReason.INCOMPLETE_IDENTITY)

            return CandidateResolution(action=CandidateAction.NONE,
                                       grounding_basis=_G.NONE, **common)

        # ── rungs 1-3: an explicit COMPLETE identity outranks every inference ──────
        if vehicle is not None and self._identity_complete(vehicle):
            compatible = _compatible_for(vehicle)
            if len(compatible) > 1:
                return _hold(CandidateHoldReason.AMBIGUOUS_MATCH, len(compatible))
            if len(compatible) == 1:
                return _select(compatible[0], CandidateSelectionBasis.EVIDENCE_IDENTITY)
            if not self._creation_permitted(vehicle):      # unreachable by construction
                return _hold(CandidateHoldReason.INCOMPLETE_IDENTITY)
            return CandidateResolution(
                action=CandidateAction.CREATE,
                focus_transition_required=focus is not None,
                locality_binding_permitted=bool(zone_detail),
                zone_group=zone_group, zone_detail=zone_detail,
                compatible_candidate_count=0,
                selection_basis=CandidateSelectionBasis.EVIDENCE_IDENTITY, **common)

        # ── rung 4: the correction points at a vehicle mentioned in THIS burst ─────
        if correction is not None and getattr(correction, "target_ref", None):
            item = None
            try:
                item = evidence.item_at(str(correction.target_ref))
            except Exception:
                item = None
            if item is None or getattr(item, "field", None) != "vehicle":
                return _hold(CandidateHoldReason.TARGET_UNRESOLVED)
            compatible = _compatible_for(item)
            if len(compatible) > 1:
                return _hold(CandidateHoldReason.AMBIGUOUS_MATCH, len(compatible))
            if len(compatible) == 1:
                return _correct(compatible[0], CandidateSelectionBasis.EXPLICIT_REFERENCE)
            # Told to correct a vehicle this thread does not have.
            return _hold(CandidateHoldReason.TARGET_CONFLICT)

        # ── rungs 5-7: an UNNAMED correction of the car under discussion ───────────
        if relation == CorrectionRelation.CORRECT_EXISTING.value:
            target, reason = self._correction_focus(ctx)
            if target is None:
                return _hold(reason)
            return _correct(target, CandidateSelectionBasis.FOCUS_POINTER)

        # ── rungs 8-9: a partial IDENTITY the customer did give (make or model only) ──
        # A mention carrying no identity token at all — a bare year, a bare category — is
        # rung 10, not a reference: asking "which vehicle?" because a number appeared would
        # be noise. A make-only or model-only reference IS a reference, so failing to place
        # it holds rather than silently creating a second car.
        if vehicle is not None and any(self._evidence_identity(vehicle)):
            compatible = _compatible_for(vehicle)
            if len(compatible) > 1:
                return _hold(CandidateHoldReason.AMBIGUOUS_MATCH, len(compatible))
            if len(compatible) == 1:
                return _select(compatible[0], CandidateSelectionBasis.EVIDENCE_IDENTITY)
            return _hold(CandidateHoldReason.INCOMPLETE_IDENTITY)

        # ── "el otro": a switch with no identity at all ────────────────────────────
        if relation == CorrectionRelation.SWITCH_TO_PRIOR_CANDIDATE.value:
            others = [c for c in candidates
                      if focus is None or c.id != getattr(focus, "id", None)]
            if len(others) == 1:
                return _select(others[0], CandidateSelectionBasis.SOLE_NON_FOCUS)
            return _hold(CandidateHoldReason.TARGET_UNRESOLVED, len(others))

        # ── a stated correction we cannot place at all ─────────────────────────────
        if correction is not None:
            return _hold(CandidateHoldReason.TARGET_UNRESOLVED)

        # ── rung 10: year / locality / category with no correction semantics ───────
        return CandidateResolution(action=CandidateAction.NONE, **common)

    def _record_candidate_mutation(self, outcome) -> None:
        """Fold the writer's real outcome into this turn's stored resolution.

        `CandidateResolution` is frozen, so this replaces it rather than mutating it. A
        correction never carries a created id; only a real insert does.

        `outcome` is the `(applied, created_id)` pair `_apply_candidate` reports. Anything
        else — a test double standing in for the writer, most often — is treated as "the
        writer reported nothing", and the decision then claims no mutation. The field must
        never assert a write that was not observed.
        """
        import dataclasses
        resolution = getattr(self, "_turn_candidate_resolution", None)
        if resolution is None:
            return
        applied, created_id = False, None
        if isinstance(outcome, tuple) and len(outcome) == 2:
            _applied, _created = outcome
            if isinstance(_applied, bool):
                applied = _applied
                created_id = _created if isinstance(_created, int) else None
        self._turn_candidate_resolution = dataclasses.replace(
            resolution, mutation_applied=applied, created_candidate_id=created_id)

    def _record_quote_consequence(self, invalidated: bool, recomputed: bool) -> None:
        """Record whether the correction invalidated and re-derived the price."""
        import dataclasses
        resolution = getattr(self, "_turn_candidate_resolution", None)
        if resolution is None:
            return
        self._turn_candidate_resolution = dataclasses.replace(
            resolution, quote_invalidated=bool(invalidated),
            quote_recomputed=bool(recomputed))

    @staticmethod
    def _evidence_ref_of(evidence, item) -> "str | None":
        """The evidence-local ref of `item` — a burst address, never a database id."""
        if item is None or evidence is None:
            return None
        try:
            for ref, candidate in evidence.iter_items():
                if candidate is item:
                    return ref
        except Exception:
            return None
        return None

    @classmethod
    def _corrected_attributes(cls, target, vehicle, correction,
                              zone_group, zone_detail) -> tuple:
        """`((field, before, after), ...)` the correction authorizes on `target`.

        Canonical VALUES only, never customer text. The corrected YEAR is read from the
        correction's own `to_value` when no admitted mention carries one, so the year's
        provenance is the correction that stated it rather than the identity-less row the
        interpreter synthesizes alongside it.
        """
        out = []
        # ── G3-7F: the typed patch is authoritative when present ──────────────────
        # Both evaluated models dropped the year from the two-field Wild Case B turn,
        # because loose (from, to) pairs gave multiplicity nowhere to live. An identified
        # patch entry per canonical field is what makes "es 2018 Y está en Pilar" two
        # corrections instead of one. The 1.3-shaped paths below remain the fallback.
        _patch = list(getattr(correction, "patch", None) or ()) if correction else []
        if _patch:
            _FIELD_MAP = {"VEHICLE_YEAR": "anio", "VEHICLE_MAKE": "marca",
                          "VEHICLE_MODEL": "modelo", "VEHICLE_VERSION": "version_text",
                          "VEHICLE_CATEGORY": "tipo_vehiculo"}
            for entry in _patch:
                name = getattr(entry.field, "value", entry.field)
                if name == "INSPECTION_LOCALITY":
                    continue            # bound through the canonical zone table below
                attr = _FIELD_MAP.get(name)
                if attr is None:
                    continue
                proposed = entry.to_value
                if attr == "anio":
                    proposed = _coerce_candidate_year(proposed)
                elif proposed is not None:
                    proposed = str(proposed).strip() or None
                if proposed is None or getattr(target, attr, None) == proposed:
                    continue
                out.append((attr, getattr(target, attr, None), proposed))
            if zone_detail and getattr(target, "zone_detail", None) != zone_detail:
                out.append(("zone_detail", getattr(target, "zone_detail", None),
                            zone_detail))
                if zone_group and getattr(target, "zone_group", None) != zone_group:
                    out.append(("zone_group", getattr(target, "zone_group", None),
                                zone_group))
            return tuple(out)

        year = None
        if vehicle is not None and getattr(vehicle, "year", None):
            year = _coerce_candidate_year(getattr(vehicle, "year", None))
        if year is None and correction is not None:
            year = _coerce_candidate_year(getattr(correction, "to_value", None))
        if year is not None and getattr(target, "anio", None) != year:
            out.append(("anio", getattr(target, "anio", None), year))
        # Identity and category arms of the G3-7A attribute matrix. They arise when the
        # correction points at a mention that carries them (rung 4), or when the customer
        # corrects the CATEGORY of the car under discussion without renaming it
        # ("es un clásico de los 70s"). The writer's catalogue guard still outranks a
        # category suggestion whenever the catalogue knows the vehicle.
        for field, attr in (("marca", "make"), ("modelo", "model")):
            proposed = (getattr(vehicle, attr, None) or "").strip() if vehicle else ""
            if proposed and getattr(target, field, None) != proposed:
                out.append((field, getattr(target, field, None), proposed))
        tipo = (getattr(vehicle, "category_suggestion", None) or "").strip() if vehicle else ""
        if tipo and getattr(target, "tipo_vehiculo", None) != tipo:
            out.append(("tipo_vehiculo", getattr(target, "tipo_vehiculo", None), tipo))
        if zone_detail and getattr(target, "zone_detail", None) != zone_detail:
            out.append(("zone_detail", getattr(target, "zone_detail", None), zone_detail))
            if zone_group and getattr(target, "zone_group", None) != zone_group:
                out.append(("zone_group", getattr(target, "zone_group", None), zone_group))
        return tuple(out)

    def _resolution_to_candidate_data(self, resolution, evidence) -> dict:
        """The writer's input, built from the decision — never from prose.

        `status="current_focus"` is the certified behaviour for both a new distinct vehicle
        and a re-focused existing one: the writer then demotes the previous focus itself.
        A mention of the candidate already in focus carries no status, so nothing moves.
        """
        from ..schemas.hybrid_trace import CandidateAction
        if not resolution.mutates:
            return {"action": "none"}
        vehicle = self._admitted_vehicle_evidence(evidence)
        data: dict = {}
        # ── G3-7B: a correction patches the resolved row and NOTHING else ──────────
        # No `status` key, so `_apply_candidate`'s demotion loop and its assignment to
        # `state.current_focus_candidate_id` are both structurally unreachable: a
        # subjectless correction cannot move focus even by accident. The fields come from
        # the decision's `corrected_fields`, never from a re-read of the mention.
        if resolution.action == CandidateAction.CORRECT_EXISTING:
            data["action"] = "update"
            data["id"] = resolution.target_candidate_id
            for field, _before, after in resolution.corrected_fields:
                data[field] = after
            return data
        if resolution.action == CandidateAction.CREATE:
            data["action"] = "create"
            data["status"] = "current_focus"
        else:
            data["action"] = "update"
            data["id"] = resolution.target_candidate_id
            if resolution.focus_transition_required:
                data["status"] = "current_focus"
        if vehicle is not None:
            if getattr(vehicle, "make", None):
                data["marca"] = str(vehicle.make)
            if getattr(vehicle, "model", None):
                data["modelo"] = str(vehicle.model)
            if getattr(vehicle, "year", None):
                data["anio"] = int(vehicle.year)
            if getattr(vehicle, "category_suggestion", None):
                # A SUGGESTION: the writer's catalogue guard overrides it whenever the
                # catalogue knows the vehicle, exactly as it did for the model's proposal.
                data["tipo_vehiculo"] = str(vehicle.category_suggestion)
        if resolution.locality_binding_permitted and resolution.zone_detail:
            # The locality binds to the RESOLVED candidate and only to it. The thread-level
            # write stays behind the reconciled location authority; this is the
            # candidate-level association the audit found had no owner.
            data["zone_detail"] = resolution.zone_detail
            if resolution.zone_group:
                data["zone_group"] = resolution.zone_group
        return data

    def _reconciled_reply_proposals(self, ctx, state) -> dict:
        """The four former reply-model fields, derived from typed evidence.

        G3-7 (audit G5D-01/G5D-02). These are PROPOSALS, exactly as the model's were: every
        one still passes the canonical writer and the authorizer that already guarded it —
        `_apply_extracted`'s fill-if-absent discipline, `_apply_candidate`'s dedup and
        demotion rules, `_authorize_acceptance` for ACEPTADO, the human-handoff policy for
        `needs_human`. What changes is the SOURCE: typed evidence the reconciler admitted,
        instead of prose a model wrote.

        Nothing here interprets language. The interpretation already happened in
        `understand/1.19`, which exists for exactly this: turning fuzzy, misspelled,
        conversational Spanish into typed evidence. This function reads that evidence.
        """
        proposals: dict = {"extracted": {}, "candidate": {"action": "none"},
                           "lead_flag": None, "needs_human": False}
        reading = self._semantic_reading()
        if getattr(reading, "status", None) != "OK":
            return proposals                 # ABSENT or UNAVAILABLE: propose nothing
        try:
            evidence = self._semantic_turn_evidence()
        except Exception:
            return proposals
        if evidence is None:
            return proposals
        from ..schemas.turn_evidence import EvidenceStatus, UNRESOLVED_STATUSES

        def _admitted(item) -> bool:
            """Evidence the reconciler would admit: named, and not corrected away."""
            if item is None or getattr(item, "is_superseded", False):
                return False
            return getattr(getattr(item, "status", None), "value",
                           getattr(item, "status", None)) not in UNRESOLVED_STATUSES

        # ── extracted: identity, locality and a requested day/time ────────────────
        extracted: dict = {}
        for ident in getattr(evidence, "identity_mentions", ()) or ():
            if _admitted(ident) and getattr(ident, "normalized_value", None):
                extracted["customer_name"] = str(ident.normalized_value)
                break
        for loc in getattr(evidence, "location_mentions", ()) or ():
            if _admitted(loc) and getattr(loc, "locality", None):
                # P7: semantic-only locality may not WRITE locality. `_apply_extracted`
                # already refuses it when a DB-validated zone_group exists, and the
                # reconciled location authority owns the thread-level write.
                extracted["zone_detail"] = str(loc.locality)
                break
        for req in getattr(evidence, "scheduling_requests", ()) or ():
            if not _admitted(req):
                continue
            if getattr(req, "resolved_date", None) and "preferred_day_iso" not in extracted:
                extracted["preferred_day_iso"] = str(req.resolved_date)
            if getattr(req, "time", None) and "preferred_time_str" not in extracted:
                extracted["preferred_time_str"] = str(req.time)
        proposals["extracted"] = extracted

        # ── candidate: the RECONCILER decides, not the mention ───────────────────
        # G3-7-R2. The first cut mapped a mention straight to create/update and lost every
        # canonical decision the reply model had been making by accident: which existing
        # candidate, which becomes focus, which is demoted, where the locality binds. That
        # is what `_resolve_candidate` answers, deterministically, from evidence plus state.
        # G3-7B: run the CL-05 stale-pointer repair here, once, so the resolver below can
        # read canonical focus without writing anything. Same repair, earlier step.
        self._focus_candidate(ctx)
        resolution = self._resolve_candidate(ctx, state, evidence)
        self._turn_candidate_resolution = resolution
        proposals["candidate"] = self._resolution_to_candidate_data(resolution, evidence)
        proposals["candidate_resolution"] = resolution

        # ── lead_flag: acceptance PROPOSES; the C3B authorizer still grants ──────
        acceptance = getattr(evidence, "acceptance", None)
        if _admitted(acceptance):
            signal = getattr(getattr(acceptance, "signal", None), "value",
                             getattr(acceptance, "signal", None))
            if signal == "ACCEPT":
                proposals["lead_flag"] = "ACEPTADO"

        # ── needs_human: P7's one approved semantic-only escalation ──────────────
        handoff = getattr(evidence, "handoff", None)
        if _admitted(handoff) and getattr(handoff, "requested", False):
            proposals["needs_human"] = True
        return proposals

    def _semantic_binding_refused(self) -> bool:
        """Is this turn's interpretation NOT bound to this turn's canonical burst?

        The G3-5B/G3-5C rule, read from one place because W1F-W-01 R1 adds a second reader:
        the admission boundary applies the same fail-closed test before it will treat a
        semantic claim as being about this turn. Logs the refusal exactly as before, and
        never calls the provider, so a rejection can never cost a second model call.
        """
        burst = getattr(self, "_canonical_turn_burst", None)
        bound = getattr(self, "_turn_semantic_burst", None)
        dispatched = bool(getattr(self, "_turn_semantic_dispatched", False))
        if dispatched and burst is not None and bound != burst.fingerprint:
            try:
                logger.warning(
                    "G3-5C SEMANTIC BINDING REFUSED canonical=%s semantic=%s",
                    burst.fingerprint[:12], (str(bound)[:12] if bound else "absent"))
            except Exception:
                pass
            return True
        return False

    def _semantic_reading(self):
        """Everything this decision needs, from ONE read of the burst's interpretation.

        R2 (audit R34-03): the authority and the FAQ path each used to read the evidence
        separately and recompute the same judgement, so one decision existed in two places
        and could diverge. There is now a single read, and `status` distinguishes the three
        outcomes that used to collapse into "nothing":

            OK          an interpretation exists and was parsed
            ABSENT      no interpretation for this turn (flag off, or none produced)
            UNAVAILABLE the provider timed out, raised, or the evidence was unusable

        That distinction is the whole of R34-01: a weak lexical candidate may not become an
        answer when the interpreter never got to disagree with it.
        """
        self._semantic_accessor_reads = getattr(self, "_semantic_accessor_reads", 0) + 1
        # ── G3-5B (audit G5A-01): the interpretation must belong to THIS burst ──
        #
        # An interpretation dispatched from some other message set may not hold primacy
        # over the reconciler. When the bindings disagree the reading is treated as
        # UNAVAILABLE — the deterministic floor's own bounded behaviour — rather than
        # silently outranking evidence it never saw.
        # G3-5C (audit G5B-05): fail CLOSED. Once an interpretation has been dispatched for
        # this turn its evidence is admissible only when it is POSITIVELY bound to the
        # canonical burst. A foreign binding and a missing binding are the same answer —
        # UNAVAILABLE, the deterministic floor's own bounded behaviour. Nothing here calls
        # the provider, so a rejection can never cost a second model call.
        if self._semantic_binding_refused():
            return SimpleNamespace(status="UNAVAILABLE", availability=None,
                                   ambiguous=None, faq_topics=frozenset())
        try:
            evidence = self._semantic_turn_evidence()
        except Exception as exc:
            try:
                logger.warning("G3-4-R2 semantic evidence unavailable: %s", type(exc).__name__)
            except Exception:
                pass
            return SimpleNamespace(status="UNAVAILABLE", availability=None,
                                   ambiguous=None, faq_topics=frozenset())
        if evidence is None:
            return SimpleNamespace(status="ABSENT", availability=None,
                                   ambiguous=None, faq_topics=frozenset())
        try:
            from ..schemas.turn_evidence import SchedulingIntentKind, UNRESOLVED_STATUSES
            resolved = ambiguous = None
            for item in getattr(evidence, "scheduling_intents", ()) or ():
                kind = getattr(getattr(item, "kind", None), "value",
                               getattr(item, "kind", None))
                if kind != SchedulingIntentKind.AVAILABILITY_ENQUIRY.value:
                    continue
                if getattr(item, "status", None) in UNRESOLVED_STATUSES:
                    ambiguous = ambiguous or item
                else:
                    resolved = resolved or item
            topics = set()
            for intent in getattr(evidence, "faq_intents", ()) or ():
                topic = getattr(intent, "topic", None) or getattr(intent, "value", None)
                topic = getattr(topic, "value", topic)
                if isinstance(topic, str) and topic in _FAQ_TOPIC_ANSWERS:
                    topics.add(topic)
            return SimpleNamespace(status="OK", availability=resolved, ambiguous=ambiguous,
                                   faq_topics=frozenset(topics))
        except Exception as exc:
            try:
                logger.warning("G3-4-R2 semantic evidence unusable: %s", type(exc).__name__)
            except Exception:
                pass
            return SimpleNamespace(status="UNAVAILABLE", availability=None,
                                   ambiguous=None, faq_topics=frozenset())

    def _semantic_availability_enquiry(self):
        """(resolved, ambiguous) availability intent — a thin view over the one read.

        Kept as the named reader the G3-3 contract test pins. R2 made `_semantic_reading`
        the single read for the turn; this simply projects it, so there is one place where
        the evidence is consulted and one place where it can fail.
        """
        reading = self._semantic_reading()
        return reading.availability, reading.ambiguous

    def _safe_scheduling_claims(self, state):
        """Claims for the trace only. An observer may never raise into a decision."""
        try:
            return self._semantic_scheduling_claims(state)
        except Exception:
            return []

    def _availability_authority(self, ctx, state, texts: list[str]):
        """Decide — once per turn — whether an availability enquiry may be acted on.

        G3-2 DEF-02: the only route into the scheduling path required the DETERMINISTIC
        parser to have found a day or a time, and the semantic reading was consulted INSIDE
        that route. CE silence was therefore an absolute veto on semantic scheduling
        evidence, which is the opposite of the ratified policy. Admission is now a
        disjunction — deterministic evidence OR a resolved semantic intent — and the
        decision records which half opened it.

        This decides only. It calls no service, writes no state, and returns a verdict the
        caller may act on through the EXISTING deterministic handlers.
        """
        from ..schemas.hybrid_trace import (
            AdmissionCondition, CanonicalEffect, Classification, FloorStrength,
            HoursDisposition, SchedulingAuthority, normalize_burst)

        # ── G3-5A (audit F-02): decide on the TURN's burst, not the caller's ─────
        #
        # `handle` registers the complete reconstructed burst before any consumer runs, so
        # in production this always resolves to it and the caller's `texts` never define
        # the evidence boundary. The fallback below only fires for an engine that did not
        # go through `handle`; it registers the first burst it sees so the turn still has
        # exactly one, rather than silently letting the second caller's text win.
        burst = getattr(self, "_canonical_turn_burst", None)
        if burst is None:
            burst = _canonical_burst_from_texts(
                thread_id=getattr(getattr(ctx, "thread", None), "id", None),
                turn_id=getattr(self, "_correlation_id", None),
                texts=list(texts or ()), provenance=("caller_supplied",),
                stage_at_build=getattr(state, "last_stage", None),
                turn_anchor_id=getattr(ctx, "inbound_wa_message_id", None))
            self._canonical_turn_burst = burst

        # A caller asking about a DIFFERENT burst in the same turn is a defect, not a
        # preference. It is detected, recorded and answered from the canonical burst —
        # never silently served a decision taken on text it did not pass.
        #
        # The comparison is on CONTENT, not on the caller's segmentation: `_faq_topics_for_
        # burst` legitimately passes the burst already joined into one string, and that is
        # the same evidence, not a different burst. What must be caught is a caller whose
        # text is not the turn's — a strict subset, most of all.
        caller_joined = " ".join(t for t in (texts or ()) if isinstance(t, str))
        caller_fingerprint = normalize_burst([caller_joined])
        divergent = caller_fingerprint != normalize_burst([burst.joined])

        # ── G3-5B (audit G5A-03): one snapshot per decision PHASE ───────────────
        #
        # `stage` used to be read live and the decision memoised, so the stage that decided
        # was whichever the first consumer observed — silently. The snapshot below holds
        # exactly the canonical fields the decision consumes, and the memo is keyed on
        # (evidence, snapshot). Two consumers in the same phase therefore share one
        # decision regardless of order, and a legitimate same-turn canonical update opens a
        # NEW, explicitly identified phase instead of being answered from a stale memo.
        snapshot = _decision_snapshot(state, self)
        phase_id = f"{burst.fingerprint[:12]}:{snapshot.digest[:12]}"
        phases = getattr(self, "_availability_authority_phases", None)
        if phases is None:
            phases = {}
            self._availability_authority_phases = phases
        cached = phases.get(phase_id)
        if cached is not None:
            self._availability_authority_cache = cached
            if divergent:
                self._trace_burst_divergence(ctx, burst, caller_fingerprint, texts)
            return cached
        if divergent:
            self._trace_burst_divergence(ctx, burst, caller_fingerprint, texts)

        texts = list(burst.texts)                   # the canonical boundary, from here on
        deterministic = bool(burst.wants_next_available)
        reading = self._semantic_reading()          # the turn's ONLY semantic read
        resolved, ambiguous = reading.availability, reading.ambiguous
        semantic = resolved is not None

        if deterministic and semantic:
            admission = AdmissionCondition.BOTH
        elif deterministic:
            admission = AdmissionCondition.DETERMINISTIC
        elif semantic:
            admission = AdmissionCondition.SEMANTIC
        else:
            admission = AdmissionCondition.NONE

        stage = snapshot.stage               # G3-5B: the phase's snapshot, not a live read
        floor = _hours_floor_strength(burst.joined)

        # Classification. Two producers on the SAME proposition is the only thing that may
        # be AGREE; a bare hours match is a different proposition and never a second
        # producer of this one.
        if admission == AdmissionCondition.BOTH:
            classification = Classification.AGREE
        elif admission == AdmissionCondition.NONE:
            classification = (Classification.AMBIGUOUS_EVIDENCE if ambiguous is not None
                              else Classification.NO_EVIDENCE)
        else:
            classification = Classification.SINGLE_PRODUCER

        # Authority. No phrase is consulted, and the stage test applies ONLY to the new
        # semantic-only admission: a deterministic admission must behave exactly as it did
        # before G3-4, because its call sites already carry the stage and prerequisite
        # guards and narrowing them here would be a regression, not a safeguard.
        hours_supported = "business_hours" in reading.faq_topics

        # ── R2B (audit R2A-01): the hours proposition, decided on its own evidence ──
        #
        # Before R2B this was `floor == WEAK and …`, so a STRONG deterministic grade made a
        # resolved semantic reading irrelevant: the interpreter said, confirmed, that the
        # customer was asking about the AGENDA and the substring answered with opening
        # hours anyway. G3-2 DEF-02 was CE SILENCE vetoing semantic evidence; that was CE
        # ASSERTION vetoing it — the same error, mirrored.
        #
        # The deterministic floor is a fallback for a proposition the interpretation did not
        # resolve. It is never a vote against one it did.
        if hours_supported:
            # The interpretation confirms the hours question itself. It stands on its own
            # evidence, and it may coexist with an availability enquiry in the same burst —
            # two propositions, composed once each, neither lending authority to the other.
            hours_disposition = HoursDisposition.ADMIT
        elif resolved is not None:
            hours_disposition = HoursDisposition.SUPPRESSED_BY_SEMANTIC_AVAILABILITY
        elif ambiguous is not None:
            hours_disposition = HoursDisposition.SUPPRESSED_AMBIGUOUS
        elif floor == FloorStrength.NONE:
            hours_disposition = HoursDisposition.NO_CANDIDATE
        elif floor == FloorStrength.WEAK:
            # Unchanged from R2B. A weak candidate already withholds and already asks; the
            # competing-evidence rule below exists to displace an ADMIT, not to re-decide a
            # case the bounded clarification already handles correctly.
            hours_disposition = HoursDisposition.SUPPRESSED_UNCORROBORATED
        elif burst.has_deterministic_scheduling:
            # ── G3-5A (audit F-01) ──────────────────────────────────────────────
            #
            # The interpretation did not resolve the proposition, and the deterministic
            # parser the engine ALREADY trusts for this judgement resolved a competing
            # one: a requested day, date or time. Before G3-5A the ladder never asked it,
            # so "¿Qué horarios tienen? ¿Hay algo mañana?" with the interpreter absent,
            # timed out or unusable graded STRONG, answered with the weekday table, and
            # discarded the date the customer named — more evidence buying less caution,
            # during an outage. Nothing new reads language here: `_parse_scheduling_text`
            # is the same reader `_turn_has_scheduling_evidence` consults.
            #
            # The requested date is NOT discarded: it stays on the canonical burst, it is
            # traced, and the existing named-day handling still owns it. This decision
            # only withholds an hours answer that would have spoken over it.
            hours_disposition = HoursDisposition.SUPPRESSED_BY_DETERMINISTIC_SCHEDULING
        else:
            # STRONG, unresolved by the interpretation, no competing proposition. This is
            # the bounded deterministic fallback, and the only place strength still decides.
            hours_disposition = HoursDisposition.ADMIT

        if admission == AdmissionCondition.NONE:
            if ambiguous is not None:
                authority, reason = (SchedulingAuthority.CLARIFY,
                                     "the interpreter itself reported an ambiguous reading")
            elif (hours_disposition == HoursDisposition.SUPPRESSED_UNCORROBORATED
                    and reading.status != "OK"):
                # R34-01, inverted. A bare time noun is the vocabulary of BOTH questions.
                # When the interpreter is absent or unavailable nothing corroborates it, and
                # a weak candidate alone may not answer — provider failure is not business
                # certainty. CLARIFY is the bounded, existing, typed result.
                authority, reason = (
                    SchedulingAuthority.CLARIFY,
                    f"weak hours candidate with no semantic corroboration "
                    f"(interpretation {reading.status.lower()})")
            elif hours_disposition == HoursDisposition.SUPPRESSED_UNCORROBORATED:
                # The interpreter ran and said this is neither an availability enquiry nor
                # an hours question. A bare token still may not answer on its own.
                authority, reason = (
                    SchedulingAuthority.CLARIFY,
                    "weak hours candidate the interpretation does not support")
            elif hours_disposition == HoursDisposition.SUPPRESSED_BY_DETERMINISTIC_SCHEDULING:
                # G3-5A (audit F-01). The date the customer named is not discarded: it is
                # on the canonical burst, it is traced, and the existing named-day handling
                # still owns it. This path withholds the hours answer rather than speaking
                # over it, and claims nothing about the calendar itself. Composing a reply
                # for BOTH propositions in a degraded turn is F-03, open for G3-5.
                authority, reason = (
                    SchedulingAuthority.HOLD,
                    f"a deterministic scheduling request ({burst.scheduling_summary}) "
                    f"stands beside the hours candidate and the interpretation "
                    f"({reading.status.lower()}) resolved neither; the existing "
                    f"deterministic scheduling path owns this turn")
            else:
                authority, reason = (SchedulingAuthority.HOLD,
                                     "no producer asked about availability this turn")
        elif deterministic:
            authority, reason = (SchedulingAuthority.LOOK_UP_AVAILABILITY,
                                 "deterministic scheduling evidence: existing behaviour, "
                                 "unchanged by G3-4")
        elif stage in (STAGE_SCHEDULING, STAGE_QUOTED, STAGE_FLOW_SENT):
            authority, reason = (SchedulingAuthority.LOOK_UP_AVAILABILITY,
                                 "availability enquiry in a scheduling-capable stage; "
                                 "the deterministic scheduling service decides what exists")
        else:
            authority, reason = (SchedulingAuthority.REQUEST_PREREQUISITE,
                                 f"stage={stage!r}: semantic-only admission, and the "
                                 "canonical prerequisite ordering still owns this turn")

        decision = SimpleNamespace(
            admission=admission, authority=authority, reason=reason,
            classification=classification, stage=stage, floor=floor,
            deterministic=deterministic, semantic=semantic,
            admitted=admission != AdmissionCondition.NONE,
            # A weak floor match loses to a resolved enquiry, and it also loses when the
            # interpreter itself could not choose: an ambiguous turn must not be settled
            # silently by a substring in either direction.
            suppress_hours=(hours_disposition != HoursDisposition.ADMIT),
            hours_disposition=hours_disposition,
            # G3-5A: the decision names the burst it was taken on, so a consumer that
            # asked about another one is provable rather than merely unlikely.
            burst=burst,
            burst_fingerprint=burst.fingerprint,
            burst_divergence=divergent,
            # G3-5B: which evidence and which canonical state this decision belongs to.
            decision_phase_id=phase_id,
            state_snapshot=snapshot,
            state_snapshot_digest=snapshot.digest,
            semantic_burst_fingerprint=getattr(self, "_turn_semantic_burst", None),
            evidence_ids=tuple(burst.ordered_message_ids),
            requested_day_iso=burst.requested_day_iso,
            requested_time_str=burst.requested_time_str,
            scheduling_evidence=burst.has_deterministic_scheduling,
            semantic_status=reading.status,
            semantic_faq_topics=reading.faq_topics,
            hours_supported=hours_supported,
            availability_intent=resolved,
            hours_suppression_reason=_HOURS_SUPPRESSION_REASON.get(
                hours_disposition, "").format(status=reading.status.lower(), floor=floor,
                                              scheduling=burst.scheduling_summary),
        )
        self._availability_authority_cache = decision
        phases[phase_id] = decision

        try:
            from ..schemas.claims import ClaimType
            self._trace_decision(
                decision_site_id=DecisionSite.SCHEDULING_AVAILABILITY_AUTHORIZATION,
                claim_type=ClaimType.SCHEDULING_PREFERENCE,
                claims=self._safe_scheduling_claims(state),
                outcome=admission, reason=reason,
                rule_id="scheduling.availability_admission", rule_version="v2",
                evidence_ids=tuple(str(m) for m in burst.ordered_message_ids),
                authority_result=authority,
                canonical_effect=CanonicalEffect.NONE,
                permitted_action=(authority if decision.admitted else None),
                business_outcome=(
                    f"admitted by {admission}; deterministic={deterministic}; "
                    f"semantic={semantic}; semantic_status={reading.status}; "
                    f"hours_floor={floor}; hours_corroborated={hours_supported}; "
                    f"hours_disposition={hours_disposition}; "
                    f"burst={burst.fingerprint[:12]}; burst_messages="
                    f"{burst.message_count}; "
                    f"burst_source={'+'.join(burst.provenance) or 'none'}; "
                    f"burst_divergence={divergent}; "
                    f"semantic_burst={str(getattr(self, '_turn_semantic_burst', None))[:12]}; "
                    f"decision_phase={phase_id}; state_snapshot={snapshot.digest[:12]}; "
                    f"snapshot_stage={snapshot.stage!r}; "
                    f"snapshot_scheduling_requested={snapshot.scheduling_requested}; "
                    f"semantic_model_calls={getattr(self, '_turn_semantic_calls', 0)}; "
                    f"payload_conflicts={len(burst.payload_conflicts)}; "
                    f"format_variations={burst.format_variations}; "
                    f"order_basis={'+'.join(sorted({m.order_basis for m in burst.messages})) or 'none'}; "
                    f"deterministic_scheduling={burst.scheduling_summary}"))
        except Exception:
            pass
        return decision

    def _semantic_faq_topics(self) -> set:
        """FAQ topics the semantic interpreter found in THIS burst. Never an answer.

        L4.7W1-F4: the deterministic detectors are literal phrase sets, and the real Wild
        proved how narrow that is — "¿Mandan un informe?" misses `mandan informe`,
        "¿Deben estar presentes?" misses `tengo que estar presente`, and "¿Aceptan?
        ¿Debito?" splits `aceptan debito` across two sentences. All four were understood
        correctly by the interpreter. This method contributes ONLY the topic label; the
        answer still comes from RideCheck's own business truth.
        """
        evidence = self._semantic_turn_evidence()
        if evidence is None:
            return set()
        topics = set()
        for intent in getattr(evidence, "faq_intents", ()) or ():
            topic = getattr(intent, "topic", None) or getattr(intent, "value", None)
            topic = getattr(topic, "value", topic)
            if isinstance(topic, str) and topic in _FAQ_TOPIC_ANSWERS:
                topics.add(topic)
        return topics

    def _explicit_faq_topics(self, text: str) -> set:
        """Topics the customer asked in so many words. A literal ask is never suppressed."""
        n = _norm_lower(text)
        topics = set()
        for topic, phrases in (("business_hours", _HOURS_FAQ_DETECTION),
                               ("report", _REPORT_FAQ_DETECTION),
                               ("presence", _PRESENCE_FAQ_DETECTION),
                               ("payment", _PAYMENT_FAQ_DETECTION)):
            if any(p in n for p in phrases):
                topics.add(topic)
        # L4.7W5-F1: presence is also asked in forms no literal list will ever cover
        # ("¿te va a estar presente?", "¿puede ir el inspector solo?"). Meaning, not
        # sentence bookkeeping.
        if any(re.search(pat, n) for pat in _PRESENCE_FAQ_PATTERNS):
            topics.add("presence")
        if any(re.search(pat, n) for pat in _PAYMENT_FAQ_PATTERNS):
            topics.add("payment")
        return topics

    def _topics_already_answered_this_cycle(self, ctx) -> set:
        """Topics whose canonical answer already went out in this cycle.

        L4.7W3-F1: de-duplication used to inspect only the message being composed, so the
        weekday table could be sent twice one turn apart — which is exactly what Wild W3
        did. Bounded to outbound messages of the CURRENT cycle, and matched on the answer
        text itself rather than a single probe word, so it is topic identity rather than a
        substring coincidence.
        """
        answered = set()
        try:
            cycle_start = getattr(ctx.state, "current_cycle_start_message_db_id", None)
            for message in reversed(list(getattr(ctx, "db_messages", []) or [])[-12:]):
                if getattr(message, "direction", None) != "out":
                    continue
                # Compare only when both are integers: message id 0 is legitimate, and a
                # cycle marker may be a timestamp string rather than a row id.
                mid = getattr(message, "id", None)
                if (isinstance(cycle_start, int) and isinstance(mid, int)
                        and mid < cycle_start):
                    continue
                body = _norm_lower(getattr(message, "text", "") or "")
                if not body:
                    continue
                for topic, answer in _FAQ_TOPIC_ANSWERS.items():
                    try:
                        canonical = _norm_lower(answer())
                    except Exception:
                        continue
                    # A distinctive slice of the canonical answer, not one common word.
                    if canonical and canonical[:40] in body:
                        answered.add(topic)
        except Exception as exc:
            logger.warning("L4.7W3-F1 recent FAQ context unavailable: %s", exc)
        return answered

    def _faq_topics_for_burst(self, text: str, ctx=None, state=None) -> set:
        """Topics this burst genuinely asks, after reconciliation against context.

        A semantic FAQ label is a PROPOSAL. Wild W3: "¿Qué tenés mañana?" in
        stage=SCHEDULING was labelled `business_hours` and the full weekday table was
        appended — but that turn is a request for availability, and the hours had been
        given one message earlier. Three reconciliations, none of them a phrase blocklist:

        1. an explicit literal ask always wins and is never suppressed;
        2. a semantic `business_hours` proposal loses to a concurrent scheduling request
           (the customer is asking what is FREE, not when we open);
        3. a topic already answered in this cycle is not repeated unless asked again.
        """
        explicit = self._explicit_faq_topics(text)
        state = state if state is not None else getattr(ctx, "state", None)

        # R2 (audit R34-03): ONE decision, consumed here. The authority already read the
        # interpretation for this turn; re-reading it to recompute the same judgement is how
        # two code paths came to own one decision. `_availability_authority` is memoised, so
        # whichever path runs first decides and the other consumes.
        decision = self._availability_authority(ctx, state, [text])
        proposed = set(decision.semantic_faq_topics)
        # G3-5B (audit G5A-03): both consumers read the SAME decision-phase snapshot, so
        # neither the stage nor the scheduling flag can differ between them by call order.
        stage = decision.state_snapshot.stage

        # (2) availability beats opening-hours when the turn is a scheduling request.
        if "business_hours" in (proposed - explicit):
            scheduling_now = bool(decision.state_snapshot.scheduling_requested)
            if scheduling_now or stage in (STAGE_SCHEDULING, STAGE_FLOW_SENT):
                proposed.discard("business_hours")
                logger.info(
                    "L4.7W3-F1 FAQ RECONCILE thread_id=%s topic=business_hours "
                    "outcome=deferred_to_scheduling stage=%s scheduling_evidence=%s",
                    getattr(getattr(ctx, "thread", None), "id", None), stage, scheduling_now)

        # ── (2b) G3-4: a bare literal is a candidate, not a winner ───────────────
        #
        # DEF-03. Rule (1) above — "an explicit literal ask always wins and is never
        # suppressed" — made `_HOURS_FAQ_DETECTION` an absolute authority, and the live
        # turn was decided by the bare word "horarios" while the interpreter was saying,
        # confirmed, that the customer was asking about the AGENDA. The guard at (2) could
        # not help: it only ever governed a PROPOSED topic.
        #
        # A weak floor match — a bare time noun, which an Argentine customer uses just as
        # readily for the calendar — now loses to a resolved availability enquiry. A STRONG
        # match, one naming the business's own attention hours or working days, survives:
        # that is a genuinely different proposition and the two stand in parallel.
        suppressed: set = set()
        suppression_reason = None
        floor = decision.floor
        availability = decision.availability_intent
        if "business_hours" in explicit and decision.suppress_hours:
            # R34-01 inverted: a WEAK candidate answers only when the interpretation
            # corroborates it. Absence, unavailability and a reading that says otherwise all
            # withhold the answer — provider failure is not business certainty.
            explicit = explicit - {"business_hours"}
            suppressed.add("business_hours")
            suppression_reason = decision.hours_suppression_reason
            logger.info(
                "G3-4-R2 FAQ AUTHORITY thread_id=%s topic=business_hours outcome=suppressed "
                "floor=%s semantic_status=%s authority=%s",
                getattr(getattr(ctx, "thread", None), "id", None), floor,
                decision.semantic_status, decision.authority)

        topics = explicit | proposed

        # (3) do not answer the same business truth twice in one cycle.
        if ctx is not None:
            repeated = (topics - explicit) & self._topics_already_answered_this_cycle(ctx)
            if repeated:
                logger.info("L4.7W3-F1 FAQ RECONCILE thread_id=%s already_answered=%s",
                            getattr(getattr(ctx, "thread", None), "id", None),
                            sorted(repeated))
                topics -= repeated
                suppressed |= repeated
                suppression_reason = suppression_reason or "already answered this cycle"

        self._trace_faq_topic_authority(
            ctx, topics=topics, suppressed=suppressed, reason=suppression_reason,
            floor=floor, availability=availability, stage=stage, proposed=proposed,
            disposition=decision.hours_disposition,
            burst_fingerprint=decision.burst_fingerprint,
            evidence_ids=decision.evidence_ids,
            decision_phase_id=decision.decision_phase_id)
        return topics

    def _trace_burst_divergence(self, ctx, burst, caller_fingerprint, texts) -> None:
        """A consumer asked about a burst that is not the turn's. Record it; never obey it.

        G3-5A (audit F-02). This is the detector the invariant needs: the canonical burst
        always wins, and the attempt is visible rather than silently absorbed by a cache
        hit. Observational and fail-open — a trace defect must not cost a customer turn.
        It logs message COUNTS and fingerprints, never customer text.
        """
        try:
            logger.warning(
                "G3-5A BURST DIVERGENCE thread_id=%s canonical=%s caller=%s "
                "canonical_messages=%d caller_messages=%d source=%s",
                getattr(getattr(ctx, "thread", None), "id", None),
                burst.fingerprint[:12], _text_digest(caller_fingerprint)[:12],
                len(burst.texts), len([t for t in (texts or ()) if isinstance(t, str)]),
                "+".join(burst.provenance) or "none")
        except Exception:
            pass
        try:
            from ..schemas.claims import ClaimType
            from ..schemas.hybrid_trace import CanonicalEffect
            self._trace_decision(
                decision_site_id=DecisionSite.SCHEDULING_AVAILABILITY_AUTHORIZATION,
                claim_type=ClaimType.SCHEDULING_PREFERENCE,
                outcome="BURST_DIVERGENCE",
                reason="a consumer requested an authority decision for a burst that is "
                       "not this turn's canonical burst; the canonical burst decided",
                rule_id="scheduling.canonical_burst_authority", rule_version="v1",
                evidence_ids=tuple(str(m) for m in burst.ordered_message_ids),
                canonical_effect=CanonicalEffect.NONE,
                business_outcome=(
                    f"canonical={burst.fingerprint[:12]}; "
                    f"caller={_text_digest(caller_fingerprint)[:12]}; "
                    f"canonical_messages={len(burst.texts)}; caller_messages="
                    f"{len([t for t in (texts or ()) if isinstance(t, str)])}; "
                    f"burst_source={'+'.join(burst.provenance) or 'none'}"))
        except Exception:
            pass

    def _trace_faq_topic_authority(self, ctx, *, topics, suppressed, reason, floor,
                                   availability, stage, proposed, disposition=None,
                                   burst_fingerprint=None, evidence_ids=(),
                                   decision_phase_id=None) -> None:
        """Record the FAQ-topic decision. Observational, fail-open, decides nothing.

        G3-4 makes the decision visible; it does NOT claim G3-5's full response-source
        instrumentation. What this row says is which topics were authorised, which
        candidates lost and why — not who wrote the sentence that eventually went out.
        """
        try:
            from ..schemas.claims import ClaimType
            from ..schemas.hybrid_trace import (
                CanonicalEffect, Classification, FaqAuthority, FloorStrength)
            if not topics and not suppressed:
                authority, classification = FaqAuthority.NO_TOPIC, Classification.NO_EVIDENCE
            elif suppressed and not topics:
                authority, classification = FaqAuthority.SUPPRESS, Classification.SINGLE_PRODUCER
            else:
                authority = FaqAuthority.ANSWER_CANONICAL
                # An availability enquiry and an hours question are DIFFERENT propositions:
                # both genuinely present is parallel evidence, never agreement.
                classification = (Classification.PARALLEL_EVIDENCE
                                  if (availability is not None and "business_hours" in topics)
                                  else Classification.SINGLE_PRODUCER)
            self._trace_decision(
                decision_site_id=DecisionSite.FAQ_TOPIC_AUTHORIZATION,
                claim_type=ClaimType.FAQ_TOPIC, claims=(),
                outcome=authority,
                reason=(reason or f"authorised={sorted(topics)}"),
                rule_id="faq.topic_authority", rule_version="v2",
                evidence_ids=tuple(str(m) for m in (evidence_ids or ())),
                authority_result=authority,
                canonical_effect=CanonicalEffect.NONE,
                permitted_action=(authority if topics else None),
                business_outcome=(
                    f"authorised={sorted(topics)}; suppressed={sorted(suppressed)}; "
                    f"hours_floor={floor}; hours_disposition={disposition}; "
                    f"semantic_topics={sorted(proposed)}; stage={stage!r}; "
                    f"burst={(burst_fingerprint or '')[:12]}; "
                    f"decision_phase={decision_phase_id}"))
        except Exception:
            pass

    def _build_faq_supplement(self, text: str) -> str:
        """WILD-04R-F2: detect FAQ signals in burst text, return canonical answers.

        Called only from pricing override paths so a pricing+FAQ burst produces
        one coherent response. Returns empty string when no FAQ signal is found.
        Sources: _HOURS_FAQ_DETECTION / _REPORT_FAQ_DETECTION / etc. constants.
        """
        topics = self._faq_topics_for_burst(text, ctx=getattr(self, "_turn_ctx", None))
        return " ".join(_FAQ_TOPIC_ANSWERS[t]() for t in _FAQ_TOPIC_ANSWERS if t in topics)


    @staticmethod
    def _contradicts_canonical(topic: str, normalized_reply: str) -> bool:
        """True when the composed prose asserts this business fact incorrectly."""
        predicate = _FAQ_TOPIC_CONTRADICT_FN.get(topic)
        if predicate is not None and predicate(normalized_reply):
            return True
        return any(re.search(pat, normalized_reply)
                   for pat in _FAQ_TOPIC_CONTRADICTS.get(topic, ()))

    @staticmethod
    def _satisfies_canonical(topic: str, normalized_reply: str) -> bool:
        """True when the composed prose already states this fact correctly.

        A topic whose truth is a VALUE — operating hours — supplies a predicate that
        compares against canonical data; a pattern cannot tell 9 to 15 from 9 to 13.
        Everything else keeps its pattern set, and falls back to the historical
        single-probe behaviour when it has none, so existing de-duplication is preserved
        rather than silently dropped.
        """
        predicate = _FAQ_TOPIC_SATISFIED_FN.get(topic)
        if predicate is not None:
            return predicate(normalized_reply)
        patterns = _FAQ_TOPIC_SATISFIED.get(topic)
        if patterns:
            return any(re.search(pat, normalized_reply) for pat in patterns)
        probe = _norm_lower(_FAQ_TOPIC_PROBES.get(topic, ""))
        return bool(probe) and probe in normalized_reply


    def _resolve_completion_target(self, ctx, *, marca, modelo, anio, tipo):
        """WHICH candidate a Vehicle-Flow completion is about. Decided BEFORE any write.

        G3-7M F1. The completion path wrote onto `focus` unconditionally, so in a
        multi-candidate thread a customer who correctly clarified "the other one" had the
        FOCUSED car overwritten with the selected identity — destroying it and leaving a
        duplicate. The Flow returns customer-confirmed structured fields, so the target is
        resolvable; it was simply never asked for.

        Compatibility uses canonical fields and the existing category authority
        (`_tipo_compatible`), never prose:

          * make must match when both sides state one;
          * model must match when both sides state one;
          * category must be compatible, so a MOTO row is never repurposed for an AUTO.

        The YEAR is deliberately NOT part of compatibility. It is a DISCRIMINATOR among
        several compatible rows, not evidence of a different car: "no, it's a 2016" about
        the one Ford Focus on file is a year correction, and treating the mismatch as a new
        vehicle would duplicate the customer's only car. Where two rows share make and
        model, an exactly equal year is what proves which one.

        Exactly one compatible row selects it. None creates. More than one narrows by an
        exactly equal year and, failing that, refuses: focus is never the tie-break, nor
        list order, nor row age, nor recency.
        """
        from ..schemas.hybrid_trace import (ClarificationCompletionTarget as _T,
                                            CompletionTargetDecision)
        rows = list(getattr(ctx, "candidates", None) or ())
        considered = tuple(c.id for c in rows if getattr(c, "id", None) is not None)
        want_ma = (marca or "").strip().lower()
        want_mo = (modelo or "").strip().lower()
        submitted = " ".join(str(x) for x in (marca, modelo, anio) if x) or None

        def _compatible(c):
            c_ma = (getattr(c, "marca", None) or "").strip().lower()
            c_mo = (getattr(c, "modelo", None) or "").strip().lower()
            if want_ma and c_ma and c_ma != want_ma:
                return False
            if want_mo and c_mo and c_mo != want_mo:
                return False
            if not _tipo_compatible(getattr(c, "tipo_vehiculo", None), tipo):
                return False
            return True

        def _names_something(c):
            return bool((getattr(c, "marca", None) or "").strip()
                        or (getattr(c, "modelo", None) or "").strip())

        # Rows that actually NAME the submitted car take precedence. A placeholder — a row
        # carrying no make and no model — is considered only when nothing named matches:
        # it is an empty slot awaiting an identity, and the Flow is what fills it. Treating
        # it as incompatible duplicated the customer's own row (§4.3: an identity is not
        # "new" merely because the existing candidate is incomplete).
        compatible = [c for c in rows if _compatible(c) and _names_something(c)]
        if not compatible:
            placeholders = [c for c in rows if _compatible(c) and not _names_something(c)]
            if len(placeholders) == 1:
                return CompletionTargetDecision(
                    _T.MATCH_EXISTING_CANDIDATE, placeholders[0].id, considered,
                    (placeholders[0].id,), submitted, "update")
        compat_ids = tuple(c.id for c in compatible)

        if len(compatible) == 1:
            return CompletionTargetDecision(
                _T.MATCH_EXISTING_CANDIDATE, compatible[0].id, considered, compat_ids,
                submitted, "update")
        if not compatible:
            return CompletionTargetDecision(
                _T.CREATE_NEW_CANDIDATE, None, considered, compat_ids, submitted, "create")
        # Several rows are compatible. One exactly-equal year is a proof of identity;
        # anything less is a guess, and guessing is what F1 was.
        if anio:
            exact = [c for c in compatible
                     if getattr(c, "anio", None) and int(c.anio) == int(anio)]
            if len(exact) == 1:
                return CompletionTargetDecision(
                    _T.MATCH_EXISTING_CANDIDATE, exact[0].id, considered, compat_ids,
                    submitted, "update")
        return CompletionTargetDecision(
            _T.UNRESOLVED_TARGET, None, considered, compat_ids, submitted, None,
            "MULTIPLE_COMPATIBLE_CANDIDATES")

    def _withdraw_booking_offer(self, ctx, state, *, reason: str,
                                clear_scheduling: bool = True) -> int:
        """Invalidate this thread's live booking offer through the ONE authority.

        G3-7P. G3-7N wrote a TRUTHY TOMBSTONE into `flow_booking_token` because clearing it
        could not revoke anything — a NULL `expected` short-circuited the booking check. Its
        independent review then measured the cost of that choice: the tombstone is a
        non-empty string, and eight production sites read that column for truthiness, so a
        withdrawn offer reported as OUTSTANDING in `_offer_outstanding`, in
        `proposal_is_active`, in the hybrid trace, and — the regression that mattered — it
        re-armed L4.7W5-F7A by closing the deterministic scheduling gates
        (`not state.flow_booking_token`) that F7A exists to keep open.

        Authority now lives in `whatsapp_booking_offers`, so the column can go back to
        meaning exactly one thing — "an offer is outstanding" — and clearing it is both safe
        and correct. The scheduling field list lives in `offer_lifecycle`, once, which is why
        the location handler can no longer be given a partial copy of it (review B-01).
        """
        from .offer_lifecycle import OfferLifecycle, WithdrawReason as _WR
        _offers = OfferLifecycle(self.db)
        n = _offers.withdraw(
            state, thread_id=ctx.thread.id, reason=reason,
            clear_scheduling=clear_scheduling)
        if not n and reason != _WR.FOCUS_CHANGED:
            # ── S3: the specific reason wins over the mechanism ──────────────────
            # `_apply_candidate` may already have withdrawn this offer earlier in the SAME
            # turn, because the focus pointer moved — and that withdrawal knows the
            # mechanism (`FOCUS_CHANGED`) while this caller knows the CAUSE
            # (`VEHICLE_CHANGED`, `LOCATION_CHANGED`). Without this the offer would carry
            # the mechanism and an operator would lose the distinction G3-7N built
            # `terminal_reason` to express. Same transaction, so nothing observes the
            # intermediate value.
            n = _offers.refine_terminal_reason(
                ctx.thread.id, when_reason=_WR.FOCUS_CHANGED, to_reason=reason)
        self._turn_offer_withdrawal = (reason, n)
        self._decision_log(ctx, "booking_offer_withdrawn", reason=reason, offers=n)
        return n

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
        self._answer_source = "DETERMINISTIC_RULE"
        lead = ctx.lead
        assert lead is not None

        tipo = (flow_data.get("tipo_vehiculo") or "").strip().upper()
        if tipo == "MOTO" or self._is_motorcycle_enquiry(tipo):
            return self._motorcycle_human_handoff(
                ctx, state,
                marca=(flow_data.get("marca") or "").strip() or None,
                modelo=(flow_data.get("modelo") or "").strip() or None,
            )
        marca = (flow_data.get("marca") or "").strip()
        modelo = (flow_data.get("modelo") or "").strip()
        anio_raw = flow_data.get("anio")
        anio: int | None = None
        try:
            anio = int(anio_raw) if anio_raw else None
        except (ValueError, TypeError):
            pass

        # G3-7F completion: the attempt row records what happened; the flag records
        # only that the proposal is no longer outstanding. Validity is decided below,
        # so the outcome is reported after it is known (see `_vfr_complete`).
        state.vehicle_fallback_flow_sent = False  # the proposal is no longer outstanding

        # ── G3-7F completion (owner order, steps 1-7) ─────────────────────────────
        # Validity is known now, so the attempt records a typed outcome rather than merely
        # "a Flow came back". A submission with no usable category is INVALID_COMPLETION and
        # an unsolicited one is recorded as such — and in neither case may this family be
        # asked again in this cycle, which the row, not a flag, guarantees.
        _vfr_valid = bool(tipo in _FALLBACK_VEHICLE_TYPES and marca and modelo)
        _vfr_attempt, _vfr_duplicate = self._complete_clarification_attempt(
            ctx, state, "VEHICLE",
            resolved=_vfr_valid,
            result=("RESOLVED" if _vfr_valid
                    else ("UNRESOLVED" if tipo in _FALLBACK_VEHICLE_TYPES
                          else "INVALID_FIELDS")))
        # ── G3-7M §7: a STALE-cycle submission must not write ─────────────────────
        # `_complete_clarification_attempt` returns no attempt for two different reasons,
        # and only one of them is safe to proceed on. No record at all may mean we died
        # after dispatching, and the established policy keeps the customer's data. A
        # record that exists for an EARLIER cycle means this form belongs to a cycle that
        # has closed, and applying it would write last cycle's answer onto this cycle's
        # vehicle.
        if _vfr_attempt is None and not _vfr_duplicate:
            _cycle_now = self._clarification_cycle_key(state)
            if self._attempt_store().attempt_in_other_cycle(
                    thread_id=ctx.thread.id, cycle=_cycle_now, family="VEHICLE"):
                logger.info("G3-7M stale-cycle vehicle completion thread_id=%s cycle=%s "
                            "— no mutation, human", ctx.thread.id, _cycle_now)
                self._turn_clarification_completion = "STALE_CYCLE"
                state.needs_human = True
                lead.necesita_humano = True
                self.db.commit()
                sent_id = self._send_text_to_wa(ctx, _FALLBACK_WARM_HANDOFF)
                self._send_fallback_human_review_notification(
                    ctx, state, reason="vehicle_completion_stale_cycle")
                return _out("replied", wa_message_id=sent_id)

        if _vfr_duplicate:
            # Step 3: a duplicate or replayed submission performs no second mutation.
            logger.info("G3-7F duplicate vehicle completion thread_id=%s — no mutation",
                        ctx.thread.id)
            self.db.commit()
            return _out("skipped_duplicate_completion")

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

        # ── G3-7M: resolve the target BEFORE mutating anything ────────────────────
        from ..schemas.hybrid_trace import ClarificationCompletionTarget as _T
        _target = self._resolve_completion_target(
            ctx, marca=marca, modelo=modelo, anio=anio, tipo=tipo)
        self._turn_completion_target = _target
        self._decision_log(ctx, "clarification_completion_target",
                           **_target.to_payload())
        logger.info("G3-7M completion target thread_id=%s outcome=%s target=%s "
                    "compatible=%s", getattr(ctx.thread, "id", None), _target.outcome,
                    _target.target_candidate_id, list(_target.compatible_candidate_ids))

        if _target.outcome == _T.UNRESOLVED_TARGET:
            # Several existing rows fit and none can be proven. Writing either would be
            # the F1 defect in a different costume, so nothing is written and a person
            # takes the turn. No second Flow: the attempt for this cycle is already spent.
            logger.info("G3-7M completion target unresolved thread_id=%s — human",
                        getattr(ctx.thread, "id", None))
            state.needs_human = True
            lead.necesita_humano = True
            self.db.commit()
            sent_id = self._send_text_to_wa(ctx, _FALLBACK_WARM_HANDOFF)
            self._send_fallback_human_review_notification(
                ctx, state, reason="vehicle_completion_target_unresolved")
            return _out("replied", wa_message_id=sent_id)

        # One writer, for both outcomes. `_apply_candidate` already owns create, update,
        # the focus transition and the demotion of the previous focus, so no
        # completion-only focus mechanism is introduced here.
        _data = {"marca": marca or None, "modelo": modelo or None,
                 "tipo_vehiculo": tipo, "anio": anio, "status": "current_focus"}
        if _target.outcome == _T.MATCH_EXISTING_CANDIDATE:
            _data.update(action="update", id=_target.target_candidate_id)
        else:
            _data.update(action="create")
        _focus_before_completion = getattr(state, "current_focus_candidate_id", None)
        _applied, _created = self._apply_candidate(ctx, _data)
        self._turn_completion_applied = bool(_applied)

        # ── G3-7N: withdraw the superseded scheduling offer ───────────────────────
        # Only when the completion actually changed what is being scheduled: the focused
        # candidate moved, a new focused candidate appeared, or the focused row's own
        # identity was materially corrected. Resubmitting the same identity with nothing
        # changed withdraws nothing, and a dispatched-but-uncompleted clarification never
        # reaches here at all. Same transaction as the candidate write above — the single
        # `commit()` below makes both authoritative together or neither.
        _focus_after_completion = getattr(state, "current_focus_candidate_id", None)
        _material = bool(
            _created
            or _focus_after_completion != _focus_before_completion
            or (_applied and _target.outcome == _T.MATCH_EXISTING_CANDIDATE))
        if _material:
            from .offer_lifecycle import WithdrawReason as _WR
            self._withdraw_booking_offer(ctx, state, reason=_WR.VEHICLE_CHANGED)
        if _created and not any(getattr(c, "id", None) == _created
                                for c in (ctx.candidates or ())):
            from app.models import WhatsAppThreadCandidate as _WTC
            _new = self.db.get(_WTC, _created)
            if _new is not None:
                ctx.candidates.append(_new)

        logger.info(
            "M18 vehicle_fallback resolved tipo=%s thread_id=%s",
            tipo, ctx.thread.id,
        )

        real_price_quote = self._compute_price_quote(ctx, state)
        if real_price_quote:
            _, _disp_det = self._get_active_inspection_location(ctx, state)
            reply = self._build_quote_reply(
                marca or None,
                modelo or None,
                _disp_det,
                real_price_quote.precio_total,
            )
            lead.flag = "PRESUPUESTO_ENVIADO"
            state.last_stage = STAGE_QUOTED
            self.db.commit()
            sent_id = self._send_text_to_wa(ctx, reply)
            self._answer_source = "PRICING_SERVICE"
            return _out("replied", wa_message_id=sent_id, answer_source="PRICING_SERVICE")

        # ── G3-7M §8: a quote that belongs to the PREVIOUS vehicle must not survive ──
        # No fresh quote could be computed (the zone is still unknown), so the stage and
        # lead flag would otherwise keep asserting a sent quote for the car the customer
        # just moved away from. This is the SAME invalidation the vehicle-change re-quote
        # guard already applies — reused, not reinvented.
        if state.last_stage in (STAGE_QUOTED, STAGE_SCHEDULING, STAGE_FLOW_SENT):
            logger.info("G3-7M stale quote invalidated after vehicle clarification "
                        "thread_id=%s stage=%s", ctx.thread.id, state.last_stage)
            lead.flag = "PRESUPUESTANDO"
            state.last_stage = STAGE_QUALIFYING
            self._record_quote_consequence(invalidated=True, recomputed=False)

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
        self._answer_source = "DETERMINISTIC_RULE"
        lead = ctx.lead
        assert lead is not None

        zona_general = (flow_data.get("zona_general") or "").strip().upper()
        localidad = (flow_data.get("localidad") or "").strip()
        referencia = (flow_data.get("referencia_ubicacion") or "").strip() or None

        # G3-7F completion — see the vehicle handler. The flag means "outstanding", and
        # clearing it can never make the attempt available again.
        state.location_fallback_flow_sent = False  # the proposal is no longer outstanding

        canonical_group = _FLOW_ZONE_GROUP_MAP.get(zona_general)

        # ── G3-7F completion (owner order, steps 1-7) — see the vehicle handler ───
        _lfr_valid = bool(canonical_group is not None)
        _lfr_attempt, _lfr_duplicate = self._complete_clarification_attempt(
            ctx, state, "LOCATION",
            resolved=_lfr_valid,
            result=("RESOLVED" if _lfr_valid else "INVALID_FIELDS"))
        if _lfr_duplicate:
            logger.info("G3-7F duplicate location completion thread_id=%s — no mutation",
                        ctx.thread.id)
            self.db.commit()
            return _out("skipped_duplicate_completion")

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

        # ── W1F-W-01 R3: a form answer is evidence, not an authority ─────────────
        # The Flow form is the strongest evidence class there is (the customer typed it),
        # and that is exactly why an unsupported or ambiguous locality must not become a
        # zone here: `canonical_group` comes from the dropdown and would pair a real group
        # with a locality we cannot serve or cannot identify. Both outcomes already exist
        # in this function's vocabulary — the coverage response and the warm handoff — and
        # neither writes `home_zone_*`.
        # OWNER-REVIEW-R1 R1RV-01: the dropdown is part of the answer. `zona_general` is an
        # explicit coverage-area selection made in the same submission, so it is passed as
        # the disambiguator — a customer who answers "CABA" + "San Nicolás" has settled the
        # ambiguity and must be served, not handed to a person. A selection that disagrees
        # with the locality's own area settles nothing and still reaches the handoff below.
        # The comparison is between two area strings; no place name is read here.
        if self._locality_safety_on() and localidad:
            _fv, _, _fwhy = self._locality_verdict(localidad, area_hint=canonical_group)
            if _fv == _loc.LocalityVerdict.UNSUPPORTED:
                self._decision_log(ctx, "location_form_refused", verdict=_fv,
                                   reason=_fwhy, target="none")
                self.db.commit()
                return self._send_coverage_response(ctx, state)
            if _fv == _loc.LocalityVerdict.AMBIGUOUS:
                self._decision_log(ctx, "location_form_ambiguous", verdict=_fv,
                                   reason=_fwhy, target="none")
                # The same exit the unresolvable-`zona_general` branch above already uses:
                # warm handoff, a person owns the thread, no new transport site. (The G3-5F
                # inventory tests count the blocked-outbound exits in this file, and R3 is
                # not entitled to add an eighth.)
                state.needs_human = True
                lead.necesita_humano = True
                self.db.commit()
                sent_id = self._send_text_to_wa(ctx, _FALLBACK_WARM_HANDOFF)
                return _out("replied", wa_message_id=sent_id)

        # Try to resolve localidad to a DB-validated zone_detail.
        _zone_before = (state.home_zone_group, state.home_zone_detail)
        db_zone = self._extract_zone_from_text(localidad) if localidad else None
        _form_group = db_zone.zone_group if db_zone else canonical_group
        _form_detail = db_zone.zone_detail if db_zone else (localidad or None)
        if referencia and not _form_detail:
            _form_detail = referencia

        # ── W1F-W-01 R2 — W1FRV-04 CLOSED: the form goes through the same path as text ──
        #
        # The finding, in the review's words: the Location Flow "writes `state.home_zone_*`
        # only; candidate 144 `zone=NULL`; reconciler not consulted". Measured on the Wild.
        # The strongest evidence class in the system — the customer typed it into a form we
        # sent — was the one write that bypassed the authority every weaker producer goes
        # through, so a form answer could not be reconciled against a confirmed value, left
        # no provenance, and never reached the candidate being quoted.
        #
        # It now takes the identical route as a locality read from text, and the route is
        # R1's, unchanged: the R3 validator has already run above (the UNSUPPORTED and
        # AMBIGUOUS branches are the fail-closed exits and they still come first), the write
        # goes through `_apply_inspection_zone` — the C2 chokepoint — and the claim is
        # `HUMAN_CONFIRMED`, which is the top of the reconciler's ladder, so it outranks
        # every other producer BY THE CERTIFIED RULE rather than by assignment order.
        #
        # The target is chosen by the same `_is_current_focus` predicate admission uses: a
        # candidate in focus receives it, and with no candidate yet the pre-candidate buffer
        # receives it through `_ZoneBuffer`, so the buffer write is reconciled too instead of
        # being assigned. With R2 OFF the two direct assignments below are the committed
        # behaviour, byte for byte.
        if self._r2_on():
            self._apply_form_location(ctx, state, zone_group=_form_group,
                                      zone_detail=_form_detail)
        else:
            # The flag-OFF branch keeps the committed expressions VERBATIM, and that is
            # deliberate rather than stylistic: C2's `test_vl_10_pre_candidate_buffer_preserved`
            # pins the literal `state.home_zone_group = db_zone.zone_group` as its evidence
            # that `state.home_zone_*` was not repurposed. Factoring it into a temporary
            # broke that assertion while its subject still held — the buffer IS still the
            # buffer — so the literal stays and no certified test has to be edited for a
            # refactor it was never about.
            state.home_zone_group = db_zone.zone_group if db_zone else canonical_group
            state.home_zone_detail = db_zone.zone_detail if db_zone else (localidad or None)
            if referencia and not state.home_zone_detail:
                state.home_zone_detail = referencia

        # ── G3-7P (review B-01): an accepted LOCATION change invalidates the offer ───
        # The zone pair is a pricing input — `PricingService.quote(db, tipo_vehiculo,
        # zone_group, zone_detail)` — and it is in `PROPOSAL_INVALIDATED_BY[BOOKING_FLOW]`.
        # G3-7N instrumented only the vehicle handler, and the review measured what that
        # left behind: moving CABA/Palermo -> Norte/San Isidro kept the token and all six
        # scheduling facts live, and the late tap returned `booking_created` — a booking at
        # the old zone's price. Owner policy names "an accepted vehicle OR LOCATION change".
        # The quote is recomputed below from the NEW canonical location, so a new offer can
        # only be issued after that recalculation.
        if (state.home_zone_group, state.home_zone_detail) != _zone_before:
            from .offer_lifecycle import WithdrawReason as _WR
            self._withdraw_booking_offer(ctx, state, reason=_WR.LOCATION_CHANGED)

        logger.info(
            "M18 location_fallback resolved zone_group=%s zone_detail=%s thread_id=%s",
            state.home_zone_group, state.home_zone_detail, ctx.thread.id,
        )

        real_price_quote = self._compute_price_quote(ctx, state)
        if real_price_quote:
            self._answer_source = "PRICING_SERVICE"
            focus = self._focus_candidate(ctx)
            _, _disp_det = self._get_active_inspection_location(ctx, state)
            reply = self._build_quote_reply(
                (focus.marca or "").strip() or None if focus else None,
                (focus.modelo or "").strip() or None if focus else None,
                _disp_det,
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
        """Deterministic NON-clarification routing. Returns (result, skip_fallback).

        G3-7F sequencing §14.1-§14.3. Priorities 2, 4 and 5 used to dispatch the Location and
        Vehicle Flows directly from substring predicates over raw customer text — 89 phrases
        across four frozensets deciding, with no typed intermediate, what a customer would be
        asked. They are gone. Clarification is decided once, later in the turn, by
        `_turn_clarification_decision`, from the reconciled proposal.

        What remains here is deliberately NOT clarification:

          * Priority 1 — a message from outside the coverage area gets the deterministic
            coverage response and a human.
          * The L4.7W2-F1 locality recovery — an ASR-mangled locality the customer already
            named becomes a confirmable PROPOSAL, never a Flow and never a canonical write.
          * Priority 3 — a service FAQ or soft close goes to the reply model.

        Priorities 1 and 3 still read phrase sets. That is deferred technical debt recorded
        by the sequencing audit (SEQ-09), not an endorsement: neither may dispatch a Vehicle
        or Location Clarification Flow, and neither decides whether the customer is asked
        anything.
        """
        if state.last_stage not in (STAGE_QUALIFYING, None):
            return None, False
        if state.needs_human:
            return None, False

        combined = " ".join(ai_input_messages)

        # Priority 1 — outside coverage
        if _is_outside_coverage(combined):
            return self._send_coverage_response(ctx, state), False

        _snap = resolve_field_evidence(ctx, state, combined,
                                      locality_safety=self._locality_safety_on())
        vehicle_known = _snap.vehicle_known() or bool(
            pre_detected_vehicle is not None
            and getattr(pre_detected_vehicle, "tipo_vehiculo", None)
        )
        zone_known = _snap.location_known()

        # ── L4.7W2-F1: bounded locality recovery ──────────────────────────────────
        # Wild W2: the customer named the locality and ASR mangled it, so the deterministic
        # resolver found nothing and CE asked a question the customer had just answered.
        # A recovered locality is a PROPOSAL — it is confirmed, never written here — and it
        # is not a clarification attempt, so it does not consume one.
        if vehicle_known and not zone_known and not self._pending_location_proposal(state):
            _loc = self._recover_locality(ctx, state, combined)
            if _loc is not None and _loc.needs_confirmation and _loc.best is not None:
                _proposal = self._propose_locality(ctx, state, _loc)
                if _proposal is not None:
                    return _proposal, False

        # Priority 3 — FAQ / soft-close / service-info → reply model.
        # Priority 3 — a service FAQ or soft close goes to the reply model.
        # `_mark_service_question` records the verdict for the clarification decision, which
        # runs later in the turn: the detector is evaluated exactly once, so the gate and the
        # decision can never disagree about whether this was a service question, and the
        # phrase set is used in one direction only — it may DECLINE to treat a turn as
        # service progression, never authorize asking the customer anything.
        if self._mark_service_question(ai_input_messages):
            return None, True

        return None, True

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
        self._answer_source = "VEHICLE_RESOLVER"
        logger.info(
            "M20 vehicle_flow_direct thread_id=%s flow_id=%s", ctx.thread.id, flow_id
        )
        if not self._outbound_armed():
            return self._blocked_outbound(ctx, intro, OutboundPathId.CE_FLOW.value)
        flow_token = secrets.token_urlsafe(24)
        # G3-7F-OWNER-RESOLUTION Part E.5 / F. The flag used to be set BEFORE the dispatch,
        # so a refused or failed Flow still consumed the thread's one attempt and the
        # customer was never asked anything. `_send_flow_button` raises on a blocked gate and
        # on a Meta failure — its own comment says "caller can revert state on flow-send
        # failure" — so the flag now waits for a real `wa_message_id`.
        sent_id = self._send_flow_button(
            ctx, intro, flow_token,
            flow_id=flow_id, initial_screen="VEHICLE_DETAILS",
        )
        if not sent_id:
            return None
        state.vehicle_fallback_flow_sent = True
        self.db.commit()
        self._turn_clarification_flow = "VEHICLE"
        return _out("replied", wa_message_id=sent_id)

    def _dispatch_location_flow_direct(
        self,
        ctx: "_Context",
        state: "WhatsAppThreadState",
        *,
        concern: bool,
        body: "str | None" = None,
    ) -> "ConversationHandleOut | None":
        """Dispatch the Location Fallback Flow immediately, skipping the text step.

        Returns None when no flow_id is configured (caller should fall through).
        """
        flow_id = (
            getattr(self.settings, "whatsapp_location_fallback_flow_id", "") or ""
        ).strip()
        if not flow_id:
            return None
        self._answer_source = "VEHICLE_RESOLVER"
        # Part E: the composer owns the wording when the router dispatches; the legacy
        # priority ladder keeps its own certified bodies.
        # One introduction per family. `concern` is retained in the signature because the
        # certified Flow dispatch contract includes it, but it no longer selects wording:
        # a customer who mentions a concern is asked for the locality the same way as
        # anyone else, and the concern is addressed in the inspection itself.
        body = body or _LOCATION_CLARIFICATION_INTRO
        logger.info(
            "M20 location_flow_direct thread_id=%s flow_id=%s concern=%s",
            ctx.thread.id, flow_id, concern,
        )
        if not self._outbound_armed():
            return self._blocked_outbound(ctx, body, OutboundPathId.CE_FLOW.value)
        flow_token = secrets.token_urlsafe(24)
        # Part E.5 / F — see the vehicle helper above for why the flag waits.
        sent_id = self._send_flow_button(
            ctx, body, flow_token,
            flow_id=flow_id, initial_screen="LOCATION_DETAILS",
        )
        if not sent_id:
            return None
        state.location_fallback_flow_sent = True
        self.db.commit()
        self._turn_clarification_flow = "LOCATION"
        return _out("replied", wa_message_id=sent_id)

    def _send_coverage_response(
        self,
        ctx: "_Context",
        state: "WhatsAppThreadState",
    ) -> "ConversationHandleOut":
        """Send the deterministic out-of-coverage response and set needs_human."""
        self._answer_source = "DETERMINISTIC_RULE"
        state.needs_human = True
        if ctx.lead:
            ctx.lead.necesita_humano = True
        logger.info("M20 coverage_response thread_id=%s", ctx.thread.id)
        if not self._outbound_armed():
            return self._blocked_outbound(ctx, _COVERAGE_RESPONSE, OutboundPathId.CE_TEXT.value)
        sent_id = self._send_text_to_wa(ctx, _COVERAGE_RESPONSE)
        return _out("replied", wa_message_id=sent_id)

    def _check_fallback_flow_triggers(
        self,
        ctx: _Context,
        state: "WhatsAppThreadState",
        pre_detected_vehicle: "VehicleMatch | None",
        ai_input_messages: list[str],
    ) -> "ConversationHandleOut | None":
        """RETIRED (G3-7F sequencing §14.4/§14.5). Always None; dispatches nothing.

        This was the inherited two-step clarification: a free-text question on the first
        unclear turn, then the Flow on the second, for each of the vehicle and location
        families. It decided from the same substring predicates as `_routing_gate`, it wrote
        `vehicle_fallback_flow_sent` / `location_fallback_flow_sent` to True BEFORE the
        dispatch — so a refused or failed Flow still consumed the thread's one attempt — and
        it held the only copy of "has this family been asked?" in a mutable boolean that
        completion, handoff and the withdrawal loop could all clear.

        Clarification now has exactly one authority, `_turn_clarification_decision`, which
        runs after the turn's reconciled proposal exists and before canonical mutation, and
        exactly one executor, `_execute_clarification`, which reserves a durable attempt row
        before reaching transport. The body is removed rather than flag-gated so that no
        configuration can route a customer back through it.

        Kept as a method, returning None, because the call site's contract is part of the
        certified `_routing_gate` handshake and the retirement is asserted structurally.
        """
        return None

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

        # WILD-04R burst completeness: DB is authoritative; n8n has a 10-message limit.
        # Fetch all persisted inbound messages since previous cursor and prepend any
        # that n8n did not include in unanswered_recent_user_messages.
        _burst_msgs = self._fetch_burst_messages(
            ctx.thread.id, getattr(ctx, "previous_processed_cursor", None), event.wa_message_id
        )
        _burst_db_texts = [m.text for m in _burst_msgs if m.text is not None]
        # Capture burst count and earliest inbound message for telemetry
        # L4.7W5-F2: _fetch_burst_messages returns [] on a thread's FIRST turn (no previous
        # cursor to bound the range), so a 3-voice-note opening burst was recorded as 1.
        # Telemetry only — the evidence list already held all three, which is why all three
        # questions were answered — but a burst counter that reads 1 on the very burst an
        # audit is examining is worse than no counter.
        self._burst_message_count = (
            len(_burst_msgs) if _burst_msgs else max(1, len(_current_evidence))
        )
        self._burst_earliest_inbound_db_id = _burst_msgs[0].id if _burst_msgs else None
        # L4.7W5 Gate 1 — the ORDERED burst, not just the message that triggered the call.
        # The shadow record has only ever carried the triggering WAMID, which is why a
        # two-message burst could not be reconstructed from evidence afterwards.
        self._turn_trace_rows = [
            {"db_id": m.id, "wa_message_id": m.wa_message_id,
             "timestamp": (m.timestamp.isoformat() if getattr(m, "timestamp", None) else None)}
            for m in _burst_msgs
        ]
        # ── G3-5B (audit G5A-02): ONE ordered atomic collection ─────────────────
        #
        # The text-set comparison this replaces deduplicated by STRING, so a customer who
        # sent the same words in two messages had one of them erased while both WAMIDs
        # stayed in the trace — three identifiers, two texts. Identity is the key now, and
        # every view (texts, ids, count, fingerprint, semantic input, deterministic input,
        # trace evidence ids) is projected from this one collection.
        _canonical_messages_tuple = _canonical_messages(
            db_rows=_burst_msgs,
            event_wamid=getattr(event, "wa_message_id", None),
            event_text=event.text,
            presentation_texts=list(event.unanswered_recent_user_messages or []),
            turn_id=getattr(self, "_correlation_id", None))
        _recovered = [m for m in _canonical_messages_tuple
                      if m.source == "db_recovery"
                      and m.text not in set(event.unanswered_recent_user_messages or [])
                      and m.text != event.text]
        if _canonical_messages_tuple:
            _current_evidence = [m.text for m in _canonical_messages_tuple]
        _missing_burst = _recovered
        if _missing_burst:
            logger.debug(
                "WILD-04R burst completeness added %d messages thread_id=%s",
                len(_missing_burst), ctx.thread.id,
            )

        if not _current_evidence:
            logger.info("M18 no text content thread_id=%s — ignored", ctx.thread.id)
            return _out("skipped_dedup", detail="no_text")

        current_turn_text = " ".join(_current_evidence)

        # Preserve existing ráfaga behavior for all downstream handlers.
        if event.unanswered_recent_user_messages:
            ai_input_messages = event.unanswered_recent_user_messages
        else:
            ai_input_messages = _current_evidence
        # L4.7W5-F6: the turn's customer words, so a handler reached later (acceptance, for
        # one) can consume an intent the customer already stated instead of asking again.
        self._turn_burst_texts = list(ai_input_messages or [])

        # ── G3-5A (audit F-02): the turn's canonical evidence boundary ───────────
        #
        # Built HERE — after `_missing_burst` recovery has completed and before any
        # authority consumer can run — from `_current_evidence`, which is the complete
        # reconstructed burst. `ai_input_messages` rides along as `presentation_texts`
        # because reply generation still wants it; it no longer defines what a decision
        # saw. Every consumer of `_availability_authority` now reads this, so caller order
        # and a partial message subset cannot change the result.
        self._canonical_turn_burst = _canonical_burst(
            thread_id=getattr(getattr(ctx, "thread", None), "id", None),
            turn_id=getattr(self, "_correlation_id", None),
            messages=(_canonical_messages_tuple
                      or _canonical_burst_from_texts(texts=_current_evidence).messages),
            provenance=tuple(sorted({m.source for m in _canonical_messages_tuple})
                             or ("event_evidence",)),
            presentation_texts=ai_input_messages,
            stage_at_build=getattr(state, "last_stage", None),
            turn_anchor_id=getattr(event, "wa_message_id", None))

        # ── L4.7B: SHADOW UNDERSTAND ──────────────────────────────────────
        # Runs on the complete raw burst BEFORE any deterministic gate can early-return,
        # so the interpreter observes every turn — including FAQ bypass, phone-call,
        # clarification, fuzzy-confirmation and scheduling exits.
        # Shadow only: it proposes TurnEvidence, mutates nothing, and can never affect
        # routing, canonical state or customer-visible text. Disabled by default.
        self._fuzzy_advisory = None      # L4.7W1-F2: advisory is per-turn, never sticky
        self._turn_ctx = ctx             # L4.7W3-F1: FAQ reconciliation needs this turn
        self._turn_scheduling_requested = False
        # G3-5B (audit G5A-01): the interpreter is dispatched from the CANONICAL burst, not
        # from the presentation subset. The producer that holds semantic primacy over every
        # deterministic signal now interprets the same turn the reconciler authorizes.
        self._run_shadow_understand(
            ctx, event, list(self._canonical_turn_burst.texts),
            burst=self._canonical_turn_burst)

        # ── M21.1.1 Layer A: Motorcycle pre-gate (all stages) ─────────────
        if self._is_motorcycle_enquiry(current_turn_text):
            return self._motorcycle_human_handoff(ctx, state, source_text=current_turn_text)

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

        # ── W1F-W-01 R1: ADMIT BEFORE ROUTE ───────────────────────────────
        # The review's one structural change. Authority in the inherited engine is
        # POSITIONAL: whatever gate answers first ends the turn, and everything the turn
        # understood dies with it — measured twice in one Wild, at Layer D and at Layer F
        # step 9, with the correct locality available both times. This decides the turn's
        # locality evidence here, before the first gate that can discard it returns, and
        # writes it through the same chokepoint the zone block uses. Nothing is sent from
        # inside it.
        #
        # PLACEMENT (owner correction OC-R1, 2026-10-27): below Layers A/B/C, above
        # Layer D. The three pre-gates above are terminal: motorcycle and phone-call hand
        # the customer to a person, and the unsupported-service gate states a boundary.
        # None of them continues the automated conversation, so none of them needs the
        # locality persisted — and admission calls `_semantic_turn_evidence()`, whose
        # bounded wait (SEMANTIC_SAME_TURN_TIMEOUT_SECONDS, 6.0 s) would otherwise be
        # coupled to three safety/handoff replies that answer in milliseconds today.
        # Layer D is the first gate that ends the turn while the conversation CONTINUES,
        # and it is one of the two gates the Wild measured dropping the locality
        # (the other is Layer F step 9, further down), so the measured requirement —
        # "en flores" survives the FAQ and Layer-F exits — is preserved here.
        #
        # Default OFF: with the flag off this is one attribute read and the turn below is
        # byte-for-byte the committed behaviour.
        if self._admission_on():
            self._admit_turn_evidence(ctx, state, list(self._canonical_turn_burst.texts))

        # ── M21.1.1 Layer D: FAQ bypass (all stages) ──────────────────────
        # Informational unless the same message also contains a pre-purchase signal,
        # an explicit inspection request, or a specific vehicle (those need the full
        # AI path to preserve qualification evidence and deliver a commercial reply).
        # B5-01-A/Part D: _detect_explicit_inspection_request guard ensures that
        # multi-intent turns ("quería revisar... ¿de qué consta?") route to Layer F
        # rather than the FAQ handler, so qualification evidence is not discarded.
        _text_norm_d = self._norm_text(current_turn_text)
        if (
            self._detect_general_information(_text_norm_d)
            and not self._detect_prepurchase_signal(_text_norm_d)
            and not self._detect_explicit_inspection_request(_text_norm_d)
            and lookup_vehicle(current_turn_text) is None
            # L4.6 Phase A: a numeric model with its year ("un 2008 del 2014") is a
            # SPECIFIC vehicle too.  Layer D already refuses to intercept a turn that
            # names a vehicle; before L4.6 `lookup_vehicle` could not see numeric models,
            # so a FAQ-dominant burst discarded the evidence entirely (finding VEH-A).
            and extract_model_del_year(current_turn_text) is None
            and not self._detect_vehicle_location_phrase(_text_norm_d)
            # F3: QUOTED + acceptance word in burst → let commercial progression handle it.
            # Without this guard, Layer D intercepts "Dale ¿Aceptan débito?" and answers
            # the FAQ but never advances flag to ACEPTADO, leaving the customer stuck.
            # Uses _has_acceptance_word (weaker than _is_acceptance) so "Dale, hagamos ese."
            # suppresses Layer D even though "hagamos"/"ese" are not acceptance keywords.
            and not (state.last_stage == STAGE_QUOTED
                     and _has_acceptance_word(ai_input_messages)
                     and (not self._acceptance_authority_on()
                          or self._authorize_acceptance(ctx, state, ai_input_messages).allows))
        ):
            # ── G3-7H P4: register-and-continue, or the inherited answer-and-return ──
            if self._layer_d_register_and_continue_on():
                self._register_general_information_proposition(ctx, ai_input_messages)
                # Fall through. The service action in this burst — a Vehicle or Location
                # Flow, a correction confirmation, a quote, a handoff — is still decided
                # downstream from reconciled evidence, and the FAQ claim travels with the
                # plan instead of ending the turn.
            else:
                return self._handle_general_information_ai(
                    ctx, event, ai_input_messages)

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

        # F3: arm same-burst FAQ reconciliation for all commercial-progression paths.
        # Motorcycle / phone / service / FAQ-bypass / inspectability / website-form
        # all returned above and will not trigger reconciliation.
        self._turn_faq_burst = current_turn_text

        # ── Deterministic price re-query (pre-AI) ────────────────────────
        # Customer asks the price again while already in QUOTED or SCHEDULING.
        # Restate the authoritative deterministic quote WITHOUT changing any
        # commercial state (lead.flag, last_stage).  Must run before the QUOTED
        # acceptance check so a price question is never mis-classified as acceptance.
        # When deterministic price is unavailable, falls through to normal handling.
        if (
            state.last_stage in (STAGE_QUOTED, STAGE_SCHEDULING)
            and not state.needs_human
            and self._detect_price_question(
                self._norm_text(" ".join(ai_input_messages))
            )
        ):
            _requery_quote = self._compute_price_quote(ctx, state)
            if _requery_quote is not None:
                _rq_focus = self._focus_candidate(ctx)
                _, _rq_det = self._get_active_inspection_location(ctx, state)
                _requery_reply = self._build_quote_reply(
                    (_rq_focus.marca or "").strip() or None if _rq_focus else None,
                    (_rq_focus.modelo or "").strip() or None if _rq_focus else None,
                    _rq_det,
                    _requery_quote.precio_total,
                    _rq_focus.anio if _rq_focus else None,
                )
                logger.info(
                    "M21.2 price restatement stage=%s thread_id=%s total=%s",
                    state.last_stage, ctx.thread.id, _requery_quote.precio_total,
                )
                self._answer_source = "PRICING_SERVICE"
                sent_id = self._send_text_to_wa(ctx, _requery_reply)
                return _out("replied", wa_message_id=sent_id)
            # Price unavailable — fall through without inventing an amount.

        # ── Deterministic QUOTED acceptance (pre-AI) ─────────────────────
        # When the client is in QUOTED stage and sends a clear acceptance word,
        # skip the AI entirely: set flag=ACEPTADO, stage=SCHEDULING, and ask
        # for day/time only.  No revision, no Flow, no re-quoting.
        # L4.7W5-F7B: route on the permissive question, decide with the authorizer.
        # `_is_acceptance` requires the whole burst to be acceptance words, so the Wild
        # burst "si" + "para cuando tenes" never entered this branch and the authorizer
        # was never consulted — the canonical producer was unreachable however correct it
        # was. Widening applies ONLY with the authority on, because the flag-off branch
        # below accepts without authorizing: widening it there would let "si consigo la
        # plata" advance unchecked. Flag off keeps the historical predicate exactly.
        _acceptance_candidate = (_may_be_acceptance(ai_input_messages)
                                 if self._acceptance_authority_on()
                                 else _is_acceptance(ai_input_messages))
        if state.last_stage == STAGE_QUOTED and _acceptance_candidate:
            # L4.7C.3B: a language match no longer advances commercial state on its own.
            # With the flag ON the deterministic predicate decides; with it OFF this is the
            # legacy path, unchanged.
            if not self._acceptance_authority_on():
                return self._handle_quoted_acceptance(ctx, state)
            _auth = self._authorize_acceptance(ctx, state, ai_input_messages)
            if _auth.allows:
                return self._handle_quoted_acceptance(ctx, state)
            logger.info(
                "L4.7C.3B acceptance NOT authorized thread_id=%s result=%s reason=%s",
                ctx.thread.id, _auth.result, _auth.reason)

        # ── Deterministic QUOTED + date proposal (pre-AI) ────────────────
        # User proposes a day (with or without exact time) while still in QUOTED
        # stage.  Treat as implicit quote acceptance + scheduling request; skip AI,
        # advance flag to ACEPTADO and immediately check availability.
        # Sunday / closed days → rejection message with alternatives.
        if state.last_stage == STAGE_QUOTED and not state.needs_human:
            # L4.7C.3B: a day proposal after a quote is an implicit acceptance, so it must
            # clear the same deterministic prerequisites — delivered, current, same-cycle
            # quote. Scheduling INTERPRETATION is untouched (that is C4): only the
            # commercial progression is gated.
            _sched_auth = (self._authorize_scheduling_progression(ctx, state)
                           if self._acceptance_authority_on() else None)
            _progression_allowed = (_sched_auth is None or _sched_auth.allows)
            sched_day_iso, sched_time_str = _parse_scheduling_text(ai_input_messages, _business_today())
            period = _detect_time_period(ai_input_messages)
            # L4.3: the customer may offer two days at once ("mñ 15hs? o el jueves").
            # Evaluate the explicit PRIMARY preference before the FALLBACK.
            sched_requests = self._reconciled_scheduling_requests(
                    ctx, state, ai_input_messages, date.today())
            if len(sched_requests) >= 2 and _progression_allowed:
                lead.flag = "ACEPTADO"
                state.last_stage = STAGE_SCHEDULING
                logger.info(
                    "L4.3 QUOTED ordered scheduling proposal thread_id=%s branches=%s",
                    ctx.thread.id, [(r.day_iso, r.time_str) for r in sched_requests],
                )
                result = self._handle_ordered_scheduling_requests(ctx, state, sched_requests)
                if result is not None:
                    return result
            if sched_day_iso and sched_time_str and _progression_allowed:
                lead.flag = "ACEPTADO"
                state.last_stage = STAGE_SCHEDULING
                logger.info(
                    "M18 QUOTED scheduling proposal thread_id=%s day=%s time=%s",
                    ctx.thread.id, sched_day_iso, sched_time_str,
                )
                result = self._try_schedule_and_flow(ctx, state, sched_day_iso, sched_time_str, "")
                if result is not None:
                    return result
            elif sched_day_iso and _progression_allowed:
                # Day only (e.g. "okay, puede ser sábado?") — list available slots.
                lead.flag = "ACEPTADO"
                state.last_stage = STAGE_SCHEDULING
                logger.info(
                    "M18 QUOTED day-only proposal thread_id=%s day=%s",
                    ctx.thread.id, sched_day_iso,
                )
                return self._handle_day_only_request(ctx, state, sched_day_iso,
                                                     period=period, texts=ai_input_messages)

        # ── Flow-failure: user can't open the WhatsApp Flow form ────────────
        if state.flow_booking_token and _is_flow_failure(ai_input_messages):
            return self._handle_flow_failure(ctx, state, " ".join(ai_input_messages))

        # ── Deterministic SCHEDULING day/time parse + escalation (pre-AI) ──
        # ── L4.7W5-F7A: hearing the customer is not the same as scheduling them ─────
        #
        # A live customer rejected the slots we had just offered — "no me sirve mañana me lo
        # venden" — and nothing happened. Every signal had fired: _earliest_option_rejected,
        # _urgency_signalled, and "no me sirve" in the escalation evidence. None of them could
        # be READ, because the whole deterministic block below is gated on
        # `not state.flow_booking_token`, and the Flow dispatched 37 seconds earlier had set
        # that token. The offer itself made the rejection of the offer inaudible.
        #
        # The token is technical lifecycle state: it exists to stop a second Flow going out
        # while one is live, and to bind a submission to the offer that produced it. It is not
        # evidence about what the customer wants, and it must not decide whether they were
        # heard. So interpretation and rescue consumption move OUT of the dispatch guard;
        # dispatch and scheduling progression stay inside it.
        #
        #   MAY_INTERPRET / MAY_CONSUME_RESCUE  -> here, token-independent
        #   MAY_DISPATCH_NEW_FLOW               -> the guarded block below
        #   MAY_CREATE_OR_CONFIRM_BOOKING       -> BookingFlowService only, unchanged
        #
        # needs_human still gates everything: once a human owns the thread the turn returns
        # earlier (skipped_human), so a repeated urgent message cannot re-trigger the handoff.
        if state.last_stage == STAGE_SCHEDULING and not state.needs_human:
            # The offered option does not solve the customer's problem. Gated on an option
            # actually having been offered, so urgency alone never escalates before
            # automation has tried.
            if (state.active_requested_date
                    and self._earliest_option_rejected(ai_input_messages)
                    and _rejection_is_about_scheduling(
                        ai_input_messages, self._offer_outstanding(state))):
                logger.info(
                    "L4.7W5-F2 EARLIEST_REJECTED thread_id=%s offered=%s flow_token=%s "
                    "— human handoff", ctx.thread.id, state.active_requested_date,
                    bool(state.flow_booking_token))
                return self._handle_scheduling_escalation(
                    ctx, state, " ".join(ai_input_messages))

            # An explicit request for a person is escalation evidence in its own right,
            # independent of whether an option was ever offered.
            if _is_human_request(ai_input_messages):
                logger.info("L4.7W5-F7A HUMAN_REQUESTED thread_id=%s — human handoff",
                            ctx.thread.id)
                return self._handle_scheduling_escalation(
                    ctx, state, " ".join(ai_input_messages))

            # Insistence on an unavailable slot or a refusal — the existing escalation
            # evidence, now audible with a Flow open.
            if _should_escalate_scheduling_to_human(ai_input_messages, state):
                return self._handle_scheduling_escalation(
                    ctx, state, " ".join(ai_input_messages)
                )

            # L4.7W5-F7D-R2: every offered option refused. The grammar reports the fact;
            # this is where it becomes a decision, and only when the context makes the
            # decision true — an offer must be outstanding, and the same burst must not
            # have taken one of the slots it supposedly rejected.
            if (self._offer_outstanding(state)
                    and not self._turn_took_an_option(ai_input_messages)
                    and _rejects_every_offered_option(ai_input_messages, True)):
                logger.info("L4.7W5-F7D OPTIONS_REJECTED thread_id=%s offered=%s "
                            "— human handoff", ctx.thread.id, state.active_requested_date)
                return self._handle_scheduling_escalation(
                    ctx, state, " ".join(ai_input_messages))

            # L4.7W5-F7C: the same burst's semantic reading, consulted LAST so the
            # deterministic detectors above remain the floor and cost nothing. A customer
            # asking for a person does not owe us a verb from a list; `handoff.requested`
            # is the interpretation of that act, and it was already being produced and
            # thrown away. No extra model call: this reads the one provider the turn
            # already dispatched.
            if self._semantic_handoff_requested(state):
                logger.info("L4.7W5-F7C SEMANTIC_HANDOFF thread_id=%s — human handoff",
                            ctx.thread.id)
                return self._handle_scheduling_escalation(
                    ctx, state, " ".join(ai_input_messages))

        if (
            state.last_stage == STAGE_SCHEDULING
            and not state.needs_human
            and not state.flow_booking_token
        ):
            sched_day_iso, sched_time_str = _parse_scheduling_text(ai_input_messages, _business_today())
            # Detect period once — used by both the period-only and day+period branches.
            period = _detect_time_period(ai_input_messages)

            # 1b. L4.7W5-F2 — the customer delegated the date, or asked for the earliest
            #     thing available. No day is named and none is needed: ScheduleService
            #     searches forward and the Flow opens on the first day with capacity.
            if not sched_day_iso and not sched_time_str:
                # R2A: the ONLY site that may clarify. Reached when no day and no time were
                # parsed and every earlier deterministic gate declined, so no other
                # proposition has claimed this turn — a clarification here replaces nothing.
                # From `_consume_scheduling_intent` an acceptance has already been resolved,
                # and a weak hours token must never displace it.
                forward = self._handle_next_available_request(
                    ctx, state, ai_input_messages, allow_clarification=True)
                if forward is not None:
                    return forward

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

            # L4.3: multi-branch scheduling utterance — PRIMARY is evaluated first and
            # the FALLBACK only when the primary cannot be satisfied.
            if _pure_sched:
                sched_requests = self._reconciled_scheduling_requests(
                    ctx, state, ai_input_messages, date.today())
                if len(sched_requests) >= 2:
                    logger.info(
                        "L4.3 ordered scheduling branches thread_id=%s branches=%s",
                        ctx.thread.id, [(r.day_iso, r.time_str) for r in sched_requests],
                    )
                    result = self._handle_ordered_scheduling_requests(
                        ctx, state, sched_requests
                    )
                    if result is not None:
                        return result

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
                return self._handle_day_only_request(ctx, state, sched_day_iso,
                                                     period=period, texts=ai_input_messages)

        # ── L4.7W2-F1: pending locality confirmation ──────────────────────
        # Runs before vehicle detection so an explicit new locality this turn wins, and
        # only inside the cycle that armed it. Nothing canonical exists until here.
        _pending_loc = self._pending_location_proposal(state)
        if _pending_loc is not None and not state.needs_human:
            _explicit_zone = self._extract_zone_from_text(current_turn_text)
            if _explicit_zone is not None:
                # The customer named a locality outright — correction wins, proposal dies.
                self._clear_location_proposal(state)
                self.db.commit()
            elif _FUZZY_ACCEPTANCE_RE.search(current_turn_text):
                _group, _detail = _pending_loc
                self._clear_location_proposal(state)
                _target = self._focus_candidate(ctx) or state
                self._apply_inspection_zone(ctx, _target, zone_group=_group,
                                            zone_detail=_detail, source="flow",
                                            role="INSPECTION_LOCATION", state=state)
                self._normalize_zone_from_db(ctx, state)
                self.db.commit()
                self._decision_log(ctx, "location_proposal_confirmed",
                                   zone=_detail, group=_group, source="customer_confirmation")
            elif _FUZZY_REJECTION_RE.search(current_turn_text):
                self._clear_location_proposal(state)
                self.db.commit()
                self._decision_log(ctx, "location_proposal_rejected", zone=_pending_loc[1])
                self._answer_source = "LOCATION_RESOLVER"
                try:
                    _sent = self._send_text_to_wa(ctx, _LOCALITY_ASK_AGAIN_REPLY)
                    return _out("replied", wa_message_id=_sent)
                except OutboundBlockedError:
                    return _out("location_proposal_blocked", detail="outbound_disabled")
            # anything else: leave the proposal armed and continue normally

        # ── M21.1.4: Pending fuzzy confirmation handler ───────────────────
        # Intercepts the turn when a CONFIRM vehicle proposal is outstanding.
        # Must run after all higher-priority gates (motorcycle, inspectability,
        # scheduling) so those gates always win. Must run before vehicle detection
        # so an exact current-turn vehicle can clear the pending proposal.
        if getattr(state, "pending_fuzzy_catalog_key", None) and not state.needs_human:
            _exact_now = lookup_vehicle(current_turn_text)
            if _exact_now:
                # User supplied an exact vehicle — clear pending (both fields), fall through
                state.pending_fuzzy_catalog_key = None
                state.pending_turn_evidence_text = None
            elif _FUZZY_ACCEPTANCE_RE.search(current_turn_text):
                # M21.2: User confirmed — create candidate from stored key, replay
                # year/zone from the originating burst (pending_turn_evidence_text).
                _pending_match = self._resolve_fuzzy_key(state.pending_fuzzy_catalog_key)
                _pending_text = getattr(state, "pending_turn_evidence_text", None) or ""
                state.pending_fuzzy_catalog_key = None
                state.pending_turn_evidence_text = None
                if _pending_match and not ctx.candidates:
                    # Use originating burst as source_text → year extracted correctly.
                    self._create_candidate_from_catalog(
                        ctx, state, _pending_match, source_text=_pending_text or current_turn_text
                    )
                    # Replay zone detection on originating burst before routing gate.
                    # allow_contradiction_return=False: contradiction in stored text →
                    # skip silently, do not generate a new outbound message.
                    if _pending_text:
                        self._apply_zone_from_text(
                            ctx, state, _pending_text, allow_contradiction_return=False
                        )
                        self._normalize_zone_from_db(ctx, state)
                        logger.info(
                            "M21.2 fuzzy acceptance evidence replay thread_id=%s "
                            "pending_text=%r zone_group=%s anio=%s",
                            ctx.thread.id, _pending_text[:60],
                            (ctx.candidates[0].zone_group if ctx.candidates else None),
                            (ctx.candidates[0].anio if ctx.candidates else None),
                        )
                    # L4.6 Phase E: a location already given in this cycle (buffered in
                    # state.home_zone_*) must reach the freshly created candidate — the
                    # customer never repeats it (finding LOC-B).
                    self._attach_buffered_location(ctx, state)
                    self._normalize_zone_from_db(ctx, state)
                    self.db.commit()
                    self._decision_log(
                        ctx, "vehicle_candidate_persisted", source="fuzzy_confirmation",
                        marca=_pending_match.marca, modelo=_pending_match.modelo,
                        anio=(ctx.candidates[0].anio if ctx.candidates else None),
                        zone=(ctx.candidates[0].zone_detail if ctx.candidates else None),
                    )
                # continue normal qualification — fall through
            elif _FUZZY_REJECTION_RE.search(current_turn_text):
                # User rejected — clear both pending fields, ask for make/model
                state.pending_fuzzy_catalog_key = None
                state.pending_turn_evidence_text = None
                self.db.commit()
                self._answer_source = "VEHICLE_RESOLVER"
                try:
                    sent_id = self._send_text_to_wa(ctx, _FUZZY_ASK_VEHICLE_REPLY)
                    return _out("replied", wa_message_id=sent_id)
                except OutboundBlockedError:
                    return _out("vehicle_fuzzy_blocked", detail="outbound_disabled")
            else:
                # Ambiguous follow-up — re-ask the same confirmation question.
                # pending_turn_evidence_text is preserved until acceptance or rejection.
                _parts = state.pending_fuzzy_catalog_key.split("||", 1)
                _pq = _FUZZY_CONFIRMATION_TEMPLATE.format(
                    marca=_parts[0], modelo=_parts[1] if len(_parts) > 1 else "",
                )
                self._answer_source = "VEHICLE_RESOLVER"
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
                state.pending_turn_evidence_text = None
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
                if _fuzzy.outcome == "AUTO_ACCEPT" and self._fuzzy_identity_accepted(
                        ctx, state, _fuzzy):
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
                        # ── L4.7W1-F2 — ADVISORY ONLY ────────────────────────
                        # This used to send "¿Es un <X>?" and RETURN, ending the turn
                        # before any deterministic block or reconciliation could run.
                        # That ordering is what let a greeting outrank the customer's
                        # actual vehicle in the controlled Wild. The suggestion is now
                        # carried forward as evidence and nothing else: no send, no
                        # pending key, no early return.
                        self._fuzzy_advisory = _fuzzy
                        logger.info(
                            "L4.7W1-F2 fuzzy ADVISORY thread_id=%s hit=%s score=%.3f "
                            "gap=%.3f authority=none",
                            ctx.thread.id,
                            f"{_fuzzy.hit.marca} {_fuzzy.hit.modelo}" if _fuzzy.hit else "?",
                            _fuzzy.score, _fuzzy.gap,
                        )
                    # fall through to AI when established candidate context exists
                # UNRESOLVED: fall through to existing unknown-vehicle path

        # ── WILD-02-B: numeric-model clarification (catalog-backed) ─────────
        # Owner Rule B: when a bare year-shaped token is the only apparent
        # vehicle reference AND it matches a catalog model (e.g. "2008" →
        # Peugeot 2008), ask "¿Es un <Marca> <Modelo>?" instead of silently
        # creating a candidate.  Uses the existing fuzzy CONFIRM machinery so
        # the acceptance path (Rule D) replays zone/year evidence.
        #
        # Inspection context is established by any of:
        #   – last_intent already AWAITING_QUALIFICATION or PREPURCHASE_INSPECTION
        #   – an explicit inspection/pre-purchase signal in the current turn text
        #     while still in QUALIFYING stage (e.g. "Quiero revisar un 2008…")
        self._turn_text = current_turn_text          # L4.6: evidence source for finalizers
        _text_norm_b = self._norm_text(current_turn_text)
        # L4.6 Phase A — EVIDENCE-DRIVEN CAPTURE.
        # Intent wording governs routing and tone; it must never decide whether
        # deterministic vehicle evidence may become canonical state.  Wild B proved the
        # old whitelist gate: "Quería revisar un 2008 del 2014" persisted a candidate
        # while "Para revisar un 2008 del 2014" persisted nothing, from identical
        # deterministic evidence (finding VEH-A).
        _evidence_capture_ctx = (
            state.last_stage in (STAGE_QUALIFYING, None)
            or state.last_intent in (_AWAITING_QUALIFICATION, _INTENT_PREPURCHASE)
        )
        # Legacy intent context, retained ONLY to decide whether a bare numeric token
        # with no vehicle determiner is worth a clarification question.
        _numeric_model_ctx = (
            state.last_intent in (_AWAITING_QUALIFICATION, _INTENT_PREPURCHASE)
            or (
                state.last_stage in (STAGE_QUALIFYING, None)
                and (
                    self._detect_prepurchase_signal(_text_norm_b)
                    or self._detect_explicit_inspection_request(_text_norm_b)
                )
            )
        )
        self._decision_log(
            ctx, "intent_gate",
            stage=state.last_stage, intent=state.last_intent,
            prepurchase=self._detect_prepurchase_signal(_text_norm_b),
            inspection_request=self._detect_explicit_inspection_request(_text_norm_b),
            evidence_capture=_evidence_capture_ctx,
        )
        if (
            pre_detected_vehicle is None
            and not state.needs_human
            and not ctx.candidates
            and (_evidence_capture_ctx or _numeric_model_ctx)
        ):
            _ctx_hit = _contextual_numeric_model_lookup(current_turn_text)
            # L4.6 Phase A: the GATE is now evidence-driven (any qualification-stage turn
            # with no candidate), but the BEHAVIOUR for a bare numeric model with no
            # companion year stays the certified WILD-02-B owner rule: confirm, never
            # silently create.  A bare "2008" is the genuinely ambiguous case (model vs
            # year); "2008 del 2014" is unambiguous and is persisted below.
            if _ctx_hit is not None:
                # Establish intent so the confirmation path passes Layer F Step 7.
                if state.last_intent != _INTENT_PREPURCHASE:
                    state.last_intent = _INTENT_PREPURCHASE
                _numeric_model_question = _FUZZY_CONFIRMATION_TEMPLATE.format(
                    marca=_ctx_hit.marca, modelo=_ctx_hit.modelo,
                )
                self._answer_source = "VEHICLE_RESOLVER"
                try:
                    _numeric_model_sent_id = self._send_text_to_wa(ctx, _numeric_model_question)
                except OutboundBlockedError:
                    self.db.commit()
                    return _out("vehicle_fuzzy_blocked", detail="outbound_disabled")
                state.pending_fuzzy_catalog_key = f"{_ctx_hit.marca}||{_ctx_hit.modelo}"
                state.pending_turn_evidence_text = current_turn_text or None
                self.db.commit()
                logger.info(
                    "WILD02 numeric model clarification sent thread_id=%s vehicle=%s %s",
                    ctx.thread.id, _ctx_hit.marca, _ctx_hit.modelo,
                )
                self._decision_log(
                    ctx, "vehicle_clarification_armed", source="bare_numeric_model",
                    marca=_ctx_hit.marca, modelo=_ctx_hit.modelo,
                    pending_key=state.pending_fuzzy_catalog_key,
                    evidence_text_stored=bool(state.pending_turn_evidence_text),
                )
                return _out("replied", wa_message_id=_numeric_model_sent_id)

        # ── WILD-04-F1: "model del year" pre-routing evidence extraction ─────
        # Handles "2008 del 2014" → Peugeot 2008, year 2014.
        # WILD-02-B returns None for 2+ numeric tokens so it never fires for
        # this pattern.  We extract deterministically here — BEFORE the routing
        # gate — so the candidate exists in DB regardless of what AI returns.
        # The routing gate still routes to AI for FAQ-dominant turns (Priority 3);
        # the candidate persists for Turn 2 zone/pricing lookup.
        if (
            pre_detected_vehicle is None
            and not state.needs_human
            and not ctx.candidates
            and _evidence_capture_ctx
        ):
            _mdy = extract_model_del_year(current_turn_text)
            if _mdy is not None:
                _mdy_match, _mdy_year = _mdy
                if state.last_intent != _INTENT_PREPURCHASE:
                    state.last_intent = _INTENT_PREPURCHASE
                self._create_candidate_from_catalog(
                    ctx, state, _mdy_match, source_text=current_turn_text
                )
                # _create_candidate_from_catalog uses _extract_year_from_text
                # with exclude_token=model_str; patch anio only if it missed.
                if ctx.candidates and ctx.candidates[0].anio is None:
                    self._apply_vehicle_identity(ctx, ctx.candidates[0],
                                                 anio=_mdy_year, source="ce",
                                                 state=ctx.state)
                    self.db.flush()
                self._attach_buffered_location(ctx, state)
                pre_detected_vehicle = _mdy_match
                self.db.commit()
                self._decision_log(
                    ctx, "vehicle_candidate_persisted", source="model_del_year",
                    marca=_mdy_match.marca, modelo=_mdy_match.modelo,
                    tipo=_mdy_match.tipo_vehiculo,
                    anio=(ctx.candidates[0].anio if ctx.candidates else _mdy_year),
                    zone=(ctx.candidates[0].zone_detail if ctx.candidates else None),
                )

        # ── L4.7W1-F2: the single vehicle-clarification point ────────────────
        # Reached only after exact catalog lookup, the numeric-model rule and the
        # "model del year" extractor have all had their turn. A fuzzy suggestion may
        # prompt a question here, and only here, and only once reconciliation has
        # accepted it as catalog-supported; otherwise the customer is asked a neutral
        # question instead of being offered a guess.
        _advisory = getattr(self, "_fuzzy_advisory", None)
        if (
            _advisory is not None
            and pre_detected_vehicle is None
            and not ctx.candidates
            and not state.needs_human
            and not getattr(state, "pending_fuzzy_catalog_key", None)
        ):
            _clarify_out = self._ask_vehicle_clarification(
                ctx, state, _advisory, current_turn_text
            )
            if _clarify_out is not None:
                return _clarify_out

        # ── M21.1.1 Layer F: QUALIFYING intent gate (QUALIFYING/None only) ──
        # F12/transfer/repair handled by all-stage Layer C above.
        # FAQ bypass handled by all-stage Layer D above.
        # This gate covers: pre-purchase signal, persisted intent, UNCERTAIN.
        if state.last_stage in (STAGE_QUALIFYING, None) and not state.needs_human:
            _intent_out = self._handle_qualifying_intent(ctx, state, current_turn_text)
            if _intent_out is not None:
                return _intent_out

        # ── M21.1.3: Role-aware zone detection ────────────────────────────
        # Uses current-turn text only (not all_recent_text) to avoid picking
        # up prior-turn zone mentions.  Shared helper enforces LR-2/LR-3/SC17.
        # F3-T1: snapshot zone before any mutation so the zone-correction re-quote guard
        # can detect whether the customer corrected location this turn.
        # Snapshot both state zone AND focus-candidate zone: vehicle-location phrases
        # update the candidate zone (LR-3) but may leave state.home_zone_* unchanged.
        _zone_at_turn_start = (state.home_zone_group, state.home_zone_detail)
        _focus_before_t1 = self._focus_candidate(ctx)
        _cand_zone_at_turn_start = (
            (_focus_before_t1.zone_group, _focus_before_t1.zone_detail)
            if _focus_before_t1 else None
        )
        _zone_early, _vehicle_location_written = self._apply_zone_from_text(
            ctx, state, current_turn_text, allow_contradiction_return=True,
        )
        if _zone_early is not None:
            return _zone_early

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

        # All deterministic gates returned None — reset any stale _answer_source
        # tags so the AI path can claim CE_AI ownership if no handler below overrides.
        self._answer_source = None

        # M21.1.6: determine whether narrative AI adds value for this turn (NU-14)
        _snap_pre = resolve_field_evidence(ctx, state,
                                          locality_safety=self._locality_safety_on())
        _use_narrative = narrative_needs_ai(_snap_pre, current_turn_text)
        _narr: NarrativeInterpretation | None = None

        messages_for_ai = self._build_ai_messages(
            ctx, event, ai_input_messages,
            pre_detected_vehicle=pre_detected_vehicle,
            real_price_quote=real_price_quote,
            include_narrative=_use_narrative,
        )

        try:
            # ── G3-5F §4.3: the boundary owns the call, the binding and the verdict ──
            decision, _admission = self._reply_decision(
                messages_for_ai, role=ReplyRole.COMMERCIAL_TURN, ctx=ctx)
            if _admission in ReplyAdmission.REJECTED:
                _use_narrative = False
                self._turn_renderer_source = RendererSource.DETERMINISTIC_TEMPLATE
                self._turn_decision_authority = DecisionAuthority.DETERMINISTIC_RULE
            else:
                self._turn_renderer_source = RendererSource.REPLY_MODEL
                # G3-5E (audit G5D-01): named, not corrected. Everything this decision
                # drives below — extracted fields, candidates, the motorcycle handoff,
                # stage resets — is authority the reply model holds today. Removing it
                # belongs to the structured-authority milestone; mislabelling it would hide
                # the finding.
                self._turn_decision_authority = (
                    DecisionAuthority.LEGACY_REPLY_MODEL_EXTRACTION)
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

        # ── G3-7: where the mutation proposals come from ─────────────────────────
        # Demoted: typed evidence the reconciler admitted. Legacy: the reply model, exactly
        # as before. The WRITERS below are unchanged either way — `_apply_extracted`'s
        # fill-if-absent rule, `_apply_candidate`'s dedup and demotion, the flag guards and
        # `_authorize_acceptance` all still run. Only the source moves.
        _proposals = (self._reconciled_reply_proposals(ctx, state)
                      if self._reply_renderer_demotion_on() else decision)
        # Apply extracted data to thread state
        extracted = _proposals.get("extracted") or {}
        _state_zone_before_ai = (state.home_zone_group, state.home_zone_detail)
        self._apply_extracted(ctx, state, extracted)
        _ai_set_zone = (state.home_zone_group, state.home_zone_detail) != _state_zone_before_ai

        # Normalise zone_group again in case AI extracted a zone_detail.
        self._normalize_zone_from_db(ctx, state)

        # Candidate updates
        _focus_before = self._focus_candidate(ctx)
        _focus_before_tipo = _focus_before.tipo_vehiculo if _focus_before else None
        _cand_data = _proposals.get("candidate") or {}
        if (
            (_cand_data.get("tipo_vehiculo") or "").upper() == "MOTO"
            and _cand_data.get("action") in ("create", "update")
        ):
            return self._motorcycle_human_handoff(ctx, state)  # AI path: no source_text
        # ╔══ G3-7H P3: CATEGORY FLOOR + CLARIFICATION DECISION — ONE MOVABLE BLOCK ══╗
        # Everything from here to `_execute_clarification` below is a single unit, and the
        # later reply-phase move must relocate it as one. The G3-7G audit measured what
        # happens otherwise: the experimental hoist moved the decision and left the floor
        # behind, `g37f_26` caught it (`the floor must run BEFORE the writer`), and owner
        # policy 19 — an unplaceable category is never announced as a motorcycle — was
        # broken by the move itself rather than by any logic change.
        #
        # The floor must precede EVERY category-dependent mutation: `_apply_candidate`,
        # `_create_candidate_from_catalog`, `_apply_vehicle_identity` and the focus switch.
        # `_apply_extracted` above is not one of them — it writes thread-state fields and
        # touches no candidate identity or category, which is why it may precede the floor.
        # `test_g37h_p3_*` asserts the ordering against every writer by call site, not by
        # character offset, so a legitimate move cannot read as a regression and an illegal
        # move cannot hide.
        # ╚═══════════════════════════════════════════════════════════════════════════╝
        # ── G3-7F MOTO/category safety floor ──────────────────────────────────────
        # A complete identity whose category is established by neither the catalogue nor
        # confirmed evidence is not an ordinary car. G37DR-04 measured a `Honda/Titan`
        # proposal creating a candidate and continuing commercially because no category
        # ever reached the guard above. Absent is not AUTO — it is a human's decision.
        if (self._correction_grounding_on()
                and _cand_data.get("action") == "create"
                and not self._category_established(ctx, _cand_data)):
            logger.info("G3-7F category floor: unestablished category thread_id=%s "
                        "marca=%r modelo=%r — routing to generic human review",
                        getattr(ctx.thread, "id", None),
                        _cand_data.get("marca"), _cand_data.get("modelo"))
            # OWNER-RESOLUTION Part C. G3-7F sent this through the motorcycle helper, so a
            # vehicle whose category merely could not be established was announced to the
            # customer as a motorcycle and filed as `motorcycle_enquiry`. Absent is not AUTO
            # — and it is not MOTO either (owner policy 19).
            return self._generic_human_handoff(
                ctx, state, reason=_HANDOFF_CATEGORY_UNRESOLVED)
        # FIX 2 (CL-07): pass zone_protected so AI cannot overwrite LR-3-written zone.
        # ══ G3-7F Commit 3 + G3-7F-R2: provisional correction authority ═══════════
        # A grounded correction is not automatically a safe one. Anything that moves a
        # price, an identity or a quote's validity becomes a typed pending patch and waits
        # for the customer; only a price-neutral year applies immediately, and then it must
        # be echoed. Flag off, everything here is skipped and Commit 1 behaviour stands.
        #
        # G3-7F-R2 (review G37FRR-05): provisional authority now REQUIRES the grounding
        # floor. `provisional ON / grounding OFF` used to arm a Tier-2 patch on the v1
        # resolver's target; it is a refused configuration, here and in
        # `_arm_pending_correction`, so neither the gate nor a direct caller can reach it.
        # ══ G3-7F SEQUENCING: the ONE clarification decision for this turn ════════
        # Above this line the turn's reconciled proposal exists (`_cand_data`, from
        # `_reconciled_reply_proposals`), carrying the numeric-model disambiguation that
        # keeps "Focus 2008" a Ford Focus of year 2008. Below it `_apply_candidate` performs
        # the canonical mutation. The decision therefore sees reconciled proposed state AND
        # committed canonical state, and is made before anything is written.
        #
        # G3-7F-R2 note: it was NOT moved above the reply-model call. Doing so is what the
        # certified M20.6D "AI must not be called" contract would require, but the reply
        # model is consulted before reconciliation today, so hoisting the decision means
        # hoisting reconciliation — which broke twelve identities including this milestone's
        # own sequencing invariants. Recorded as STOP-03 rather than forced.
        _pc_resolution_d, _disposition_d = self._clarification_disposition(
            ctx, state, _cand_data)
        _clar = self._turn_clarification_decision(
            ctx, state,
            reconciled=_cand_data,
            resolution=_pc_resolution_d,
            disposition=_disposition_d,
            ce_vehicle=pre_detected_vehicle,
            ce_location=bool(_vehicle_location_written),
            current_turn_text=current_turn_text or "")
        if _clar.requires_clarification:
            # SAFE PARTIAL MUTATION (§13 of the sequencing brief). A clarification response
            # may coexist with facts this turn genuinely resolved, but only through the
            # existing canonical writer and only for the family that is NOT in question.
            if _clar.route == _CR_LOCATION_FLOW_REQUIRED:
                _outcome = self._apply_candidate(
                    ctx, _cand_data, zone_protected=_vehicle_location_written)
                self._record_candidate_mutation(_outcome)
            else:
                self._record_candidate_mutation((False, None))
            _clar_result = self._execute_clarification(ctx, state, _clar)
            if _clar_result is not None:
                return _clar_result
            return _out("skipped_clarification_outstanding")

        _pc_resolution = getattr(self, "_turn_candidate_resolution", None)
        _pc_tier = None
        if (self._provisional_correction_on()
                and self._correction_grounding_on()
                and _pc_resolution is not None
                and _cand_data.get("action") == "update"
                and _pc_resolution.action == CandidateAction.CORRECT_EXISTING):
            from . import pending_correction as _pcmod
            _existing = self._pending_correction(ctx)
            _newer = bool(_pc_resolution.corrected_fields)
            if _existing is not None and _newer:
                # A newer grounded correction supersedes atomically. It may not also be
                # confirmed in the turn that armed it, or one message would both propose
                # and accept.
                self._store_pending_correction(
                    state,
                    _existing.with_status(_pcmod.PendingStatus.SUPERSEDED,
                                          _pcmod.InvalidationReason.NEWER_CORRECTION))
                _existing = None
            _pc_tier = self._correction_tier(_pc_resolution)
            # ── G3-7F-R2 (review G37FRR-04): finite legacy-proposal recovery ───────
            # Decide what this correction DOES to every outstanding proposal before asking
            # whether it may coexist with one. A proposal the correction answers or
            # invalidates is withdrawn canonically now; only a sound, unanswered one
            # competes for the customer's next word, and that competition is budgeted.
            _disposition = _pcmod.proposal_disposition(
                state, _pc_resolution.corrected_fields)
            # OWNER-RESOLUTION Part D, now folded into the single decision above. A
            # correction competing with another live confirmable proposal is owner policy 8
            # — one "sí" with two referents — and the router returns GENERIC_HUMAN_REQUIRED
            # for it, so this turn has already been handed to a person before reaching here.
            # There is deliberately no second routing call: one turn, one decision, one
            # reservation authority.
            self._withdraw_dependent_proposals(ctx, state, _disposition)
            if _pc_tier == _pcmod.CorrectionTier.PROVISIONAL:
                _pending, _refusal = self._arm_pending_correction(
                    ctx, state, _pc_resolution)
                if _pending is None:
                    self._decision_log(ctx, "pending_correction_refused", reason=_refusal)
                    self._record_candidate_mutation((False, None))
                    return self._hold_for_correction_clarification(ctx, state, _refusal)
                self._store_pending_correction(state, _pending)
                self._decision_log(ctx, "pending_correction_armed",
                                   tier=_pending.tier,
                                   fields=len(_pending.corrected_fields),
                                   basis=_pending.grounding_basis)
                self._record_candidate_mutation((False, None))
                return self._request_correction_confirmation(ctx, state, _pending)

        # ── G3-7F-R2 (review G37FRR-06): the category travels WITH the correction ──
        # The writer's catalogue authority only runs when the patch carries an explicit
        # `tipo_vehiculo`, and a correction patch never did, so a confirmed
        # `Honda/Titan -> Ford/Focus` left the row typed MOTO and priced as a motorcycle.
        # Supplying the category here puts the decision back inside the existing catalogue
        # authority (`WILD04R-F6`, which re-resolves and overrides it), and it is NOT added
        # to `corrected_fields`, so it is never echoed or confirmed as a customer statement.
        #
        # OWNER-RESOLUTION Part B (review R2R-02). R2 placed this call OUTSIDE the flag
        # block and `_correction_category` consulted no flag, so with both features OFF an
        # identity correction still had its category re-derived, still carried
        # `correction_in_place` into the writer, and still routed an unplaceable vehicle to a
        # handoff — flag-off behaviour that never matched the parent. The grounding flag now
        # gates the call here, and the helper refuses independently, so the dependency holds
        # at both boundaries.
        if self._correction_grounding_on():
            _cand_data = self._correction_category(ctx, _cand_data, _pc_resolution)
            if _cand_data is None:
                # A known identity the catalogue cannot place. Owner policy 18: do not send
                # the Vehicle Flow to re-ask an identity we already hold — hand it to a
                # person, and never as a motorcycle.
                self._record_candidate_mutation((False, None))
                return self._generic_human_handoff(
                    ctx, state, reason=_HANDOFF_CATEGORY_UNRESOLVED)

        _outcome = self._apply_candidate(
            ctx, _cand_data, zone_protected=_vehicle_location_written)
        # G3-7B: the decision records what the WRITER did, not what the resolver expected.
        self._record_candidate_mutation(_outcome)
        # ── G3-7F-R2 (review G37FRR-01): the echo is authorized by the WRITE ───────
        # G3-7F assembled the echo before calling the writer and nothing ever read it. The
        # order is now the other way round and the value is a typed proposition the response
        # composer renders: a mutation the customer was never told about, or a sentence about
        # a mutation that did not happen, are both impossible from here.
        from .pending_correction import CorrectionTier as _CT
        if (_pc_tier == _CT.IMMEDIATE
                and self._writer_applied(_outcome)
                and _pc_resolution is not None):
            self._turn_correction_echo = tuple(
                (f, a) for f, _b, a in _pc_resolution.corrected_fields)

        # ── G3-7F-R2 (review G37FRR-07): the correction boundary ──────────────────
        # `_enforce_catalog_vehicle` operates on FOCUS and, on a category mismatch, CREATES a
        # candidate rather than repairing one. On a correction turn that is the phantom the
        # Grounded-Target Invariant exists to prevent, arriving after it through legacy code:
        # the row was just re-identified in place, so a second row for the same car is wrong
        # by construction. The category has already been resolved through the same catalogue
        # authority by `_correction_category`, so nothing is lost by skipping it here. This is
        # the narrow correction-boundary block the milestone authorizes — the general
        # catalogue path is untouched for every other turn.
        _correction_wrote_identity = (
            _pc_resolution is not None
            and _cand_data is not None
            and bool(_cand_data.get("correction_in_place"))
            and self._writer_applied(_outcome))

        # ── Catalog enforcement (post-AI) ─────────────────────────────────
        # If catalog found a vehicle, override the candidate's tipo_vehiculo
        # regardless of what the AI returned.  marca/modelo are only set if
        # not already filled (AI may have added year / version details).
        if pre_detected_vehicle and not _correction_wrote_identity:
            self._enforce_catalog_vehicle(ctx, pre_detected_vehicle)
        elif pre_detected_vehicle:
            logger.info(
                "G3-7F-R2 catalog enforcement skipped on correction turn thread_id=%s "
                "candidate=%s — the correction already applied catalogue authority",
                ctx.thread.id, _cand_data.get("id"))

        # ── G3-7F-R2 (Part G.6): a corrected vehicle that is a motorcycle ──────────
        # The category moved to MOTO because the customer re-identified the vehicle. Canonical
        # state has been written, so the record is truthful; autonomous quoting and scheduling
        # now stop under the existing human-routing policy.
        if getattr(self, "_turn_correction_moto", False):
            logger.info(
                "G3-7F-R2 corrected vehicle is a MOTO thread_id=%s — routing to human review",
                ctx.thread.id)
            self._decision_log(ctx, "correction_category_moto", candidate=_cand_data.get("id"))
            return self._motorcycle_human_handoff(ctx, state)

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
            # G3-7B: the trace must show the CONSEQUENCE of a correction, not only its intent.
            self._record_quote_consequence(invalidated=True, recomputed=True)
        # F3-T2: Zone correction re-quote guard — mirrors the vehicle-change guard above.
        # When the customer corrects location after a quote was sent (QUOTED/SCHEDULING),
        # the old viatico may be stale. Reset to QUALIFYING so the deterministic override
        # re-prices with the new zone. Only fires when zone actually changed this turn AND
        # vehicle-change guard did NOT already reset stage (avoids double-reset).
        # Checks BOTH state zone (changed by bare-locality / AI extraction) AND candidate
        # zone (changed by vehicle-location clause via _apply_zone_from_text LR-3 path).
        _zone_after_mutations = (state.home_zone_group, state.home_zone_detail)
        _focus_after_t2 = self._focus_candidate(ctx)
        _cand_zone_after_t2 = (
            (_focus_after_t2.zone_group, _focus_after_t2.zone_detail)
            if _focus_after_t2 else None
        )
        _state_zone_changed = _zone_after_mutations != _zone_at_turn_start and any(_zone_after_mutations)
        _cand_zone_changed = (
            _cand_zone_at_turn_start is not None
            and _cand_zone_after_t2 is not None
            and _cand_zone_after_t2 != _cand_zone_at_turn_start
            and any(_cand_zone_after_t2)
        )
        if (
            (_state_zone_changed or _cand_zone_changed)
            and state.last_stage in (STAGE_SCHEDULING, STAGE_QUOTED, STAGE_FLOW_SENT)
            and not state.needs_human
        ):
            logger.info(
                "F3 zone correction state=%r→%r cand=%r→%r in %s — resetting to QUALIFYING thread_id=%s",
                _zone_at_turn_start, _zone_after_mutations,
                _cand_zone_at_turn_start, _cand_zone_after_t2,
                state.last_stage, ctx.thread.id,
            )
            lead.flag = "PRESUPUESTANDO"
            state.last_stage = STAGE_QUALIFYING
            # G3-7B: same record for the zone path — this is the Wild Case B re-quote.
            self._record_quote_consequence(invalidated=True, recomputed=True)
        # Sync thread state → candidate only when zone was set THIS turn (by the
        # pre-AI bare-locality path or by AI extraction). Never copy stale thread
        # zone that was set in a prior turn (LR-6/LR-7).
        if focus_after and _vehicle_location_written:
            # WILD-01 fix (FINDING-01): LR-3 wrote directly to candidate when a
            # current-focus candidate existed; state.home_zone_* may be stale from
            # a prior turn. Only fill candidate gaps — never overwrite what LR-3 set.
            if state.home_zone_group and not focus_after.zone_group:
                self._apply_inspection_zone(ctx, focus_after,
                                            zone_group=state.home_zone_group,
                                            zone_detail=state.home_zone_detail,
                                            source="buffer", state=state)
            if state.home_zone_detail and not focus_after.zone_detail:
                self._apply_inspection_zone(ctx, focus_after,
                                            zone_group=focus_after.zone_group,
                                            zone_detail=state.home_zone_detail,
                                            source="buffer", state=state)
        elif focus_after and not _vehicle_location_written and _ai_set_zone:
            if state.home_zone_group and not focus_after.zone_group:
                self._apply_inspection_zone(ctx, focus_after,
                                            zone_group=state.home_zone_group,
                                            zone_detail=state.home_zone_detail,
                                            source="buffer", state=state)
            if state.home_zone_detail and not focus_after.zone_detail:
                self._apply_inspection_zone(ctx, focus_after,
                                            zone_group=focus_after.zone_group,
                                            zone_detail=state.home_zone_detail,
                                            source="buffer", state=state)

        # Sync year onto focus candidate deterministically if AI missed it.
        if focus_after:
            _excl = str(focus_after.modelo) if focus_after.modelo is not None else None
            # WILD-01 fix (FINDING-02): run year sync even when candidate already has
            # a year from a prior session, if current turn carries exactly one
            # unambiguous year.  Customer explicitly stated the year → override stale.
            # Priority 1: current-turn year tokens (highest confidence — user just said this).
            #   Exactly one effective year in current turn → commit unconditionally.
            #   2+ effective years in current turn → explicitly ambiguous, no sync.
            # Priority 2: historical context (all_recent_text) — only when current turn
            #   carries zero years AND candidate lacks a year.
            #   2+ effective years in history (e.g. "2008 o 2014" from a prior turn) →
            #   no sync, even if the vehicle is now known, because the original ambiguity
            #   was never resolved by the customer.
            # In both cases the vehicle model token is excluded (e.g. Peugeot "2008" is
            # the model identifier, not a year of manufacture — see Y04/Y05 cases).
            _ct_year_tokens = {
                m.group(1) for m in _VEHICLE_YEAR_RE.finditer(current_turn_text or "")
            }
            _ct_effective = _ct_year_tokens - {_excl} if _excl else _ct_year_tokens

            if len(_ct_effective) == 1:
                # Current turn: exactly one unambiguous year → commit, overriding stale.
                year_hit = _extract_year_from_text(current_turn_text, exclude_token=_excl)
                if year_hit:
                    self._apply_vehicle_identity(ctx, focus_after, anio=year_hit,
                                                 source="ce", state=state)
            elif len(_ct_effective) == 0 and focus_after.anio is None:
                # No year in current turn AND candidate lacks one → check historical context.
                _all_year_tokens = {
                    m.group(1) for m in _VEHICLE_YEAR_RE.finditer(all_recent_text or "")
                }
                _all_effective = _all_year_tokens - {_excl} if _excl else _all_year_tokens
                if len(_all_effective) <= 1:
                    year_hit = _extract_year_from_text(all_recent_text, exclude_token=_excl)
                    if year_hit:
                        self._apply_vehicle_identity(ctx, focus_after, anio=year_hit,
                                                 source="ce", state=state)
            # If len(_ct_effective) > 1: current turn is explicitly ambiguous → no sync.

        # Recompute price after all extractions have run.
        real_price_quote = self._compute_price_quote(ctx, state)

        # Lead flag — only allowed values, engine validates
        new_flag = _proposals.get("lead_flag")
        flag_accepted = new_flag and new_flag in _ALLOWED_FLAGS
        # ── L4.7W1-F3: the AI may PROPOSE acceptance; it may not GRANT it ─────
        # PRESUPUESTO_ENVIADO already required a deterministic price below. ACEPTADO
        # required nothing at all: membership of _ALLOWED_FLAGS was the whole check, so
        # a model that emitted lead_flag="ACEPTADO" advanced commercial state directly.
        # It now passes the same C3B authorizer as every other acceptance.
        if flag_accepted and new_flag == "ACEPTADO" and lead.flag != "ACEPTADO":
            if self._acceptance_authority_on():
                _ai_auth = self._authorize_acceptance(ctx, state, ai_input_messages)
                if not _ai_auth.allows:
                    logger.warning(
                        "L4.7W1-F3 AI ACEPTADO blocked thread_id=%s result=%s reason=%s",
                        ctx.thread.id, _ai_auth.result, _ai_auth.reason,
                    )
                    flag_accepted = False
            # Authority flag OFF: legacy behaviour, unchanged — the same flag discipline
            # every other cutover in this programme uses. Adding a second predicate here
            # rejected genuine acceptance-plus-FAQ turns ("Sí, dale. ¿Qué horarios
            # tienen?"), which is a product regression, not a safety gain.
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

        # BUG-3 guard: QUALIFYING → ACEPTADO is never a valid direct transition.
        # The canonical sequence is: QUALIFYING → QUOTED (quote sent) → SCHEDULING
        # (customer explicitly accepts a quote they have already received).
        # A valid deterministic price is NOT customer acceptance — the customer must
        # see the quote first, then confirm it in a later turn.
        # When price IS available: the deterministic override below will send the quote
        # and commit PRESUPUESTO_ENVIADO / QUOTED; no reply is set here.
        # When price is unavailable: prompt for the missing vehicle/zone data.
        if (
            flag_accepted
            and new_flag == "ACEPTADO"
            and state.last_stage in (STAGE_QUALIFYING, None)
        ):
            logger.warning(
                "M18 blocking ACEPTADO in QUALIFYING — quote not yet sent thread_id=%s",
                ctx.thread.id,
            )
            flag_accepted = False
            if real_price_quote is None:
                # Price unavailable — prompt for the missing data that would unlock pricing.
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
            # When real_price_quote is not None: deterministic override below sends the
            # quote and commits PRESUPUESTO_ENVIADO / QUOTED. No reply set here.

        if flag_accepted and new_flag != lead.flag:
            lead.flag = new_flag
            if new_flag == "PRESUPUESTO_ENVIADO":
                state.last_stage = STAGE_QUOTED
                if real_price_quote is not None and not state.needs_human:
                    _q_focus = self._focus_candidate(ctx)
                    _, _q_det = self._get_active_inspection_location(ctx, state)
                    decision["reply"] = self._build_quote_reply(
                        (_q_focus.marca or "").strip() or None if _q_focus else None,
                        (_q_focus.modelo or "").strip() or None if _q_focus else None,
                        _q_det,
                        real_price_quote.precio_total,
                        _q_focus.anio if _q_focus else None,
                    )
                    self._answer_source = "PRICING_SERVICE"
            elif new_flag == "ACEPTADO":
                state.last_stage = STAGE_SCHEDULING

        # Human escalation
        if _proposals.get("needs_human"):
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
            _, _q_det = self._get_active_inspection_location(ctx, state)
            decision["reply"] = self._build_quote_reply(
                (_q_focus.marca or "").strip() or None if _q_focus else None,
                (_q_focus.modelo or "").strip() or None if _q_focus else None,
                _q_det,
                real_price_quote.precio_total,
                _q_focus.anio if _q_focus else None,
            )
            self._answer_source = "PRICING_SERVICE"

        # Schedule check + flow when in SCHEDULING stage
        stage = state.last_stage
        if stage == STAGE_SCHEDULING and not state.needs_human and not state.flow_booking_token:
            # Deterministic parser runs first to prevent AI hallucinating dates.
            # This handles the ráfaga case where "si" + "mañana 12hs" arrive together
            # and the AI transitions from QUOTED→SCHEDULING in the same turn.
            det_day, det_time = _parse_scheduling_text(ai_input_messages, _business_today())
            pday = det_day or state.preferred_day or state.active_requested_date
            # FIX 3 (RISK-04): when a NEW deterministic day is established this turn,
            # do not inherit a stale state.preferred_time from a prior scheduling attempt.
            # The time must come from the current turn; stale time + new day = wrong slot.
            ptime = det_time or extracted.get("preferred_time_str") or (
                None if det_day else state.preferred_time
            )
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
                _, ai_proposed_time = _parse_scheduling_text([ai_reply_text], _business_today())
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
            # L4.7W5-NO-REPLY-ALERT-INTEGRITY. Nothing usable came back and nothing is
            # handed to the outbound path, so this is NOT a reply. Returning `replied`
            # here set `reply_produced=true` and removed the turn from the unanswered
            # rescue: the customer got silence and the alert for silence was suppressed.
            # `detail` is unchanged — it is the diagnostic evidence of *why* nothing was
            # produced, and it must not be what decides whether a reply existed.
            self.db.commit()
            return _out(ACTION_NO_REPLY_PRODUCED, detail="no_reply_text")

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
        source_text: str = "",
        marca: "str | None" = None,
        modelo: "str | None" = None,
    ) -> "ConversationHandleOut":
        """Motorcycle/quad/UTV inquiry handler.

        When WHATSAPP_FLOW_ID (CLIENT DATA FLOW) is configured (preferred path):
          Turn 1: creates a MOTO candidate, stores a pending token, and dispatches
          the existing client-data WhatsApp Flow.  Does NOT set needs_human yet —
          that happens in _process_motorcycle_contact_response after the Flow reply.

        Fallback (WHATSAPP_FLOW_ID not configured):
          Immediate plain-text handoff; sets needs_human=True immediately.
        """
        self._answer_source = "DETERMINISTIC_RULE"
        flow_id = (getattr(self.settings, "whatsapp_flow_id", "") or "").strip()

        if not flow_id:
            # ── Fallback: Flow not configured — immediate plain-text handoff ───
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
                sent_id = self._send_text_to_wa(ctx, _MOTORCYCLE_HANDOFF_REPLY)
                return _out("replied", wa_message_id=sent_id)
            except OutboundBlockedError:
                self.db.commit()
                return _out("human_handoff_blocked", detail="motorcycle_enquiry_kill_switch")

        # ── Flow path: collect contact data before setting needs_human ────────

        # Extract make/model from source_text if not already provided
        if not marca and not modelo and source_text:
            marca, modelo = self._extract_motorcycle_make_model(source_text)

        # Create a 'mentioned' MOTO candidate to preserve vehicle evidence
        from app.models import WhatsAppThreadCandidate
        moto_cand = WhatsAppThreadCandidate(
            thread_id=ctx.thread.id,
            tipo_vehiculo="MOTO",
            marca=marca or None,
            modelo=modelo or None,
            status="mentioned",
            source_text=(source_text or f"moto:{marca or ''} {modelo or ''}").strip(),
        )
        self.db.add(moto_cand)
        self.db.flush()

        # Token signals pending motorcycle contact Flow — keeps _process_flow_response
        # routing working without setting needs_human prematurely.
        # G3-7P: this token belongs to a DIFFERENT family (routed by prefix before the
        # booking handler), so it is not a booking offer — but writing it over the mirror
        # would orphan a live booking offer, so that offer is superseded explicitly.
        from .offer_lifecycle import OfferLifecycle as _MotoOffers, WithdrawReason as _MotoWR
        _MotoOffers(self.db).supersede(state, thread_id=ctx.thread.id,
                                       reason=_MotoWR.MOTO_HANDOFF)
        flow_token = f"{_MOTO_HANDOFF_TOKEN_PREFIX}{ctx.thread.id}-{int(_time.time())}"
        state.flow_booking_token = flow_token
        self.db.commit()

        try:
            sent_id = self._send_flow_button(
                ctx, _MOTORCYCLE_CONTACT_INTRO, flow_token,
                flow_id=flow_id, initial_screen="MAIN",
            )
            return _out("flow_button_sent", wa_message_id=sent_id)
        except OutboundBlockedError:
            # Roll back pending token on kill-switch
            state.flow_booking_token = None
            self.db.commit()
            return _out("human_handoff_blocked", detail="motorcycle_enquiry_kill_switch")

    def _extract_motorcycle_make_model(
        self, text: str
    ) -> "tuple[str | None, str | None]":
        """Conservative extraction of make/model from a motorcycle enquiry.

        Only returns tokens that start with an uppercase letter or look like a
        vehicle model code (e.g. CB500, PCX, R6).  Returns (None, None) rather
        than guessing when tokens are ambiguous.
        """
        # Find motorcycle keyword in original text (case-insensitive)
        km = re.search(
            r'(?<![a-z0-9])'
            r'(motos?|motocicletas?|scooters?|ciclomotor(?:es)?|cuatriciclos?|quads?|atvs?|utvs?)'
            r'(?![a-z0-9])',
            text, re.IGNORECASE,
        )
        if not km:
            return None, None

        after = text[km.end():].strip()
        tokens = after.split()

        _STOPWORDS = frozenset({
            "de", "del", "el", "la", "los", "las", "un", "una", "unos", "unas",
            "en", "y", "o", "para", "que", "con", "por", "como", "está", "esta",
            "no", "si", "se", "al", "a", "es", "son", "tiene", "tienen",
            "quiero", "revisar", "tengo", "quisiera", "quería",
        })

        make: "str | None" = None
        model: "str | None" = None

        for tok in tokens[:5]:
            clean = re.sub(r'[^\w\-]', '', tok)
            if not clean:
                continue
            lower = clean.lower()
            if lower in _STOPWORDS:
                if make is None:
                    continue  # skip leading stopwords before make
                break  # stopword after make ends the search
            # Accept: starts with uppercase, OR is all-caps+digit model code (CB500, PCX)
            if re.match(r'^[A-Z]', clean) or re.match(r'^[A-Z0-9]{2,}$', clean.upper()):
                if make is None:
                    make = clean
                elif model is None:
                    model = clean
                    break
            else:
                # Non-stopword, non-identifier — ambiguous; stop if we already have make
                if make is not None:
                    break

        return make, model

    def _process_motorcycle_contact_response(
        self,
        ctx: "_Context",
        state: "WhatsAppThreadState",
        flow_data: dict,
    ) -> "ConversationHandleOut":
        """Handle the contact-data Flow response for a motorcycle manual handoff.

        Persists client data into the Lead, preserves MOTO candidate evidence,
        sets needs_human=True, and sends the advisor confirmation reply.
        """
        self._answer_source = "DETERMINISTIC_RULE"
        lead = ctx.lead
        assert lead is not None

        # Consume the pending motorcycle handoff token. PW1-A: only a motorcycle-family
        # mirror is ours to consume — a live booking offer's mirror is the offer authority's
        # (G3-7P-R2: the mirror never lies), and a reset may already have cleared it.
        if (state.flow_booking_token or "").startswith(_MOTO_HANDOFF_TOKEN_PREFIX):
            state.flow_booking_token = None

        # Parse contact fields using same mapping as existing CLIENT DATA FLOW (MAIN screen).
        # telefono is intentionally skipped — WA phone is the authoritative contact identifier.
        full_name = (flow_data.get("nombre_apellido") or "").strip()
        buyer_email = (flow_data.get("email") or "").strip() or None
        canal = (flow_data.get("como_llego") or "").strip() or None
        name_parts = full_name.split() if full_name else []
        buyer_first = name_parts[0] if name_parts else ""
        buyer_last = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

        # Update Lead — never overwrite existing values; never touch telefono
        if buyer_first and not lead.nombre:
            lead.nombre = buyer_first
        if buyer_last and not lead.apellido:
            lead.apellido = buyer_last
        if buyer_email and not lead.email:
            lead.email = buyer_email
        if canal and not getattr(lead, "canal", None):
            lead.canal = canal

        # Find MOTO candidate to confirm motorcycle evidence was preserved
        moto_cand = next(
            (c for c in ctx.candidates if (c.tipo_vehiculo or "").upper() == "MOTO"),
            None,
        )
        logger.info(
            "M21.2 motorcycle_contact_response thread_id=%s moto_cand_id=%s marca=%s modelo=%s",
            ctx.thread.id,
            moto_cand.id if moto_cand else None,
            moto_cand.marca if moto_cand else None,
            moto_cand.modelo if moto_cand else None,
        )

        # Human handoff — set AFTER persisting all data
        lead.necesita_humano = True
        state.needs_human = True

        self.db.commit()

        try:
            self._send_fallback_human_review_notification(
                ctx, state, reason="motorcycle_enquiry"
            )
        except Exception as exc:
            logger.error(
                "M21.2 motorcycle_contact_response notification failed thread_id=%s: %s",
                ctx.thread.id, exc,
            )

        sent_id = self._send_text_to_wa(ctx, _MOTORCYCLE_ADVISOR_REPLY)
        return _out("replied", wa_message_id=sent_id)

    def _handle_phone_call_escalation(
        self,
        ctx: "_Context",
        state: "WhatsAppThreadState",
    ) -> "ConversationHandleOut":
        """Centralized phone-call/human-agent escalation handler."""
        self._answer_source = "DETERMINISTIC_RULE"
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
        self._answer_source = "DETERMINISTIC_RULE"
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
        # W1F-W-01 R3 item 4: with locality safety on, plural subjects are read too —
        # "los autos están en Flores" is the same claim about the same inspection location
        # and matched nothing before (finding W1FRV-05). Each captured locality still has
        # to survive the catalogue and the validators in `_extract_zone_from_text`.
        for pattern in _loc.clause_patterns(plural=self._locality_safety_on()):
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
        self._answer_source = "DETERMINISTIC_RULE"
        try:
            sent_id = self._send_text_to_wa(ctx, _LOCATION_CONTRADICTION_CLARIFICATION)
            return _out("replied", wa_message_id=sent_id)
        except OutboundBlockedError:
            self.db.commit()
            return _out("location_contradiction_blocked", detail="outbound_disabled")

    # ── L4.6 Phase F — structured CE decision logging ────────────────────────

    def _turn_offer_for_trace(self, ctx):
        """The thread's most recent booking offer, for the canonical snapshot.

        G3-7P. The snapshot used to derive `offer_outstanding` from
        `bool(flow_booking_token or ...)`, which is exactly how G3-7N's truthy tombstone
        came to report a WITHDRAWN offer as a live one. The authority answers instead, and
        the LATEST offer (not only a live one) is returned so a refusal can name what it
        refused rather than rendering an empty row.
        """
        try:
            from .offer_lifecycle import OfferLifecycle
            thread_id = getattr(getattr(ctx, "thread", None), "id", None)
            if thread_id is None:
                return None
            return OfferLifecycle(self.db).latest_offer(thread_id)
        except Exception:                      # the trace never breaks a conversation turn
            logger.debug("G3-7P offer snapshot unavailable", exc_info=True)
            return None

    def _decision_log(self, ctx: "_Context", event: str, **fields) -> None:
        """One structured, secret-free line per CE decision.

        Wild B could only be reconstructed by re-executing the deployed code because the
        runtime emitted nothing but HTTP access lines (finding OBS-A).  These records make
        the next Wild reconstructable from logs alone.  Never log message bodies, tokens,
        phone numbers or Meta payloads — only decisions and resolved identifiers.
        """
        try:
            rendered = " ".join(
                f"{k}={v}" for k, v in fields.items() if v is not None and v != ""
            )
            logger.info(
                "CE_DECISION event=%s thread_id=%s %s",
                event, getattr(ctx.thread, "id", None), rendered,
            )
        except Exception:  # logging must never break a conversation turn
            pass

    # ── L4.7B — shadow UNDERSTAND (no authority, no mutation) ────────────────

    def _build_shadow_context(self, ctx: "_Context", messages: list[str]):
        """L4.7B.2 Phase G — the bounded context handed to the shadow interpreter.

        Current cycle only. `ctx.db_messages` already excludes prior cycles, so a vehicle
        or locality from a finished inspection can never leak in. Nothing here is
        authority: it exists so relative references can be read at all.
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from .semantic_interpreter import TurnContext

        tz_name = "America/Argentina/Buenos_Aires"
        now = datetime.now(ZoneInfo(tz_name))
        context = TurnContext.now(tz=tz_name, today=now.date())

        state = getattr(ctx, "state", None)
        if state is not None:
            stage = getattr(state, "last_stage", None)
            if isinstance(stage, str) and stage:
                context.stage = stage
            # What we last asked, expressed as a canonical flag name — not a phrase.
            for flag, label in (("pending_fuzzy_catalog_key", "confirmacion_de_modelo"),
                                ("vehicle_clarification_sent", "vehiculo"),
                                ("location_clarification_sent", "ubicacion"),
                                ("inspectability_clarification_sent", "inspeccionabilidad")):
                if getattr(state, flag, None):
                    context.pending_clarification = label
                    break
            raw_slots = getattr(state, "last_offered_slots", None)
            if isinstance(raw_slots, str) and raw_slots.strip():
                slots: list[str] = []
                try:
                    parsed = json.loads(raw_slots)
                    if isinstance(parsed, list):
                        slots = [str(x) for x in parsed if x]
                except Exception:
                    slots = [p.strip() for p in raw_slots.replace("|", ",").split(",")
                             if p.strip()]
                context.offered_slots = tuple(slots[:5])

        # Previous customer turn in this cycle — the burst itself is excluded.
        burst = {t.strip() for t in messages if isinstance(t, str)}
        for message in reversed(list(getattr(ctx, "db_messages", []) or [])):
            if getattr(message, "direction", None) != "in":
                continue
            text = (getattr(message, "text", None) or "").strip()
            if not text or text in burst:
                continue
            context.previous_customer_turn = text[:300]
            break
        return context

    def _run_shadow_understand(
        self, ctx: "_Context", event: "ConversationHandleIn", messages: list[str],
        *, burst=None,
    ) -> None:
        """Interpret the burst in shadow and record it. Never affects this turn.

        Provenance (burst, ids, context) is captured **synchronously**; the model call and
        the append-only write are handed to a bounded worker when async is enabled, so the
        customer turn never waits on a model (L4.7B measured p95 3.7 s inline).

        Every failure mode — disabled flag, missing key, HTTP error, malformed JSON, full
        queue, unwritable log — degrades to "do nothing". This method returns None, writes
        no state and raises nothing.
        """
        self._turn_semantic = None
        # G3-5B (audit G5A-01): which burst this interpretation belongs to. Authority
        # compares it with the canonical burst and fails safe when they differ, so a
        # reading of some other message set can never hold primacy over the reconciler.
        self._turn_semantic_burst = getattr(burst, "fingerprint", None)
        self._turn_semantic_dispatched = True      # G3-5C: arms the fail-closed check
        self._turn_semantic_calls = 0
        try:
            # Strict identity check: only a real boolean True enables the shadow path, so a
            # MagicMock settings object in a test can never trigger a model call.
            if getattr(self.settings, "shadow_understand_enabled", False) is not True:
                return
            from .semantic_interpreter import SemanticTurnInterpreter
            from .semantic_turn_evidence import TurnSemanticEvidence
            from .shadow_recorder import build_record, record_shadow
            from ..schemas.turn_evidence import BurstReconstruction
            from .outbound_path_registry import get_deployment_id

            # ── captured synchronously: this can never be reconstructed later ──
            interpreter = SemanticTurnInterpreter(self.settings)
            burst_id = getattr(self, "_correlation_id", None)
            thread_id = getattr(ctx.thread, "id", None)
            # The canonical burst's ordered identities, not just the triggering WAMID.
            message_ids = (tuple(str(m) for m in burst.ordered_message_ids)
                           if burst is not None else
                           tuple(str(m) for m in [getattr(event, "wa_message_id", None)] if m))
            burst_texts = list(burst.texts) if burst is not None else list(messages)
            deployment_id = get_deployment_id()
            raw_path = getattr(self.settings, "shadow_evidence_path", "")
            evidence_path = raw_path if isinstance(raw_path, str) and raw_path else None
            try:
                context = self._build_shadow_context(ctx, burst_texts)
            except Exception:
                context = None

            # L4.7C.1: the deterministic snapshot is read synchronously — it reads ctx and
            # state, which belong to this turn — and handed to the worker as a value.
            # `resolve_field_evidence` is read-only (M21.1.5) and already runs three times
            # per turn in the live path; a failure here degrades to semantic-only claims.
            try:
                field_snapshot = resolve_field_evidence(
                    ctx, ctx.state, locality_safety=self._locality_safety_on())
            except Exception:
                field_snapshot = None
            cycle_id = str(getattr(ctx.state, "current_cycle_start_message_db_id", None)
                           or getattr(ctx.state, "current_cycle_started_at", None) or "")
            revision_id = getattr(ctx.state, "current_revision_id", None)

            # L4.7C.4A — the ONE interpretation of this burst. Every consumer (the customer
            # turn, the claim projection, the reconciler, the recorder below) reads this same
            # provider, so same-turn authority costs no extra model call.
            def _interpret():
                # G3-5B: the architectural invariant is ONE interpretation per decision
                # turn. This counter is incremented where the provider is actually
                # invoked, so it measures model calls and not the accessor reads that
                # legitimately happen more than once.
                self._turn_semantic_calls = getattr(self, "_turn_semantic_calls", 0) + 1
                return interpreter.interpret(
                    burst_texts,
                    thread_id=thread_id,
                    burst_id=burst_id,
                    message_ids=message_ids,
                    reconstruction=BurstReconstruction.LIVE_DEBOUNCE,
                    context=context,
                )

            provider = TurnSemanticEvidence(_interpret, thread_id=thread_id,
                                            burst_id=burst_id)
            self._turn_semantic = provider
            self._turn_semantic_texts = list(burst_texts)
            # The interpretation is bound to the burst it interpreted, by fingerprint.
            self._turn_semantic_burst = (getattr(burst, "fingerprint", None)
                                         if burst is not None else None)

            def _work() -> None:
                result = provider.get()
                if result is None:
                    return
                # ── shadow claim projection + reconciliation (writes nothing) ──  # noqa
                reconciliation = None
                try:
                    from .claim_projection import project_all
                    from .shadow_reconciler import reconcile, summarise
                    claims = project_all(
                        result.evidence, field_snapshot, texts=burst_texts,
                        cycle_id=(cycle_id or None),
                        revision_id=(revision_id if isinstance(revision_id, int) else None))
                    reconciliation = summarise(
                        reconcile(claims, cycle_id=(cycle_id or None),
                                  revision_id=(revision_id if isinstance(revision_id, int)
                                               else None)))
                except Exception as exc:      # shadow reconciliation is never load-bearing
                    try:
                        logger.warning("L4.7C.1 shadow reconciliation failed thread_id=%s: %s",
                                       thread_id, exc)
                    except Exception:
                        pass
                record_shadow(
                    build_record(
                        thread_id=thread_id,
                        burst_id=burst_id,
                        message_ids=message_ids,
                        result=result,
                        deployment_id=deployment_id,
                        correlation_id=burst_id,
                        dispatch=("async" if self._shadow_async() else "sync"),
                        reconciliation=reconciliation,
                    ),
                    path=evidence_path,
                )

            if self._shadow_async():
                from .shadow_worker import ShadowJob, get_worker
                worker = get_worker()

                # ONE job for the burst: it runs the single interpretation and then records
                # it. A customer turn that needs the evidence waits on the interpretation
                # (which sets its event as soon as the model answers) and not on the
                # append-only write behind it.
                def _dispatch(run) -> bool:
                    def _job() -> None:
                        run()
                        _work()
                    return worker.submit(ShadowJob(run=_job, thread_id=thread_id,
                                                   burst_id=burst_id))

                provider.start(submit=_dispatch)
            else:
                provider.start()
                _work()
        except Exception as exc:   # shadow must never break a customer turn
            try:
                logger.warning("L4.7B shadow understand failed thread_id=%s: %s",
                               getattr(ctx.thread, "id", None), exc)
            except Exception:
                pass

    def _shadow_async(self) -> bool:
        """Async dispatch is opt-in and strictly boolean, like the shadow flag itself."""
        return getattr(self.settings, "shadow_understand_async", False) is True

    # ── L4.7C.4A — the same interpretation, available to the turn it is about ─

    def _same_turn_semantic_on(self) -> bool:
        """Strictly boolean, so MagicMock settings can never make a turn wait on a model."""
        return getattr(self.settings, "semantic_same_turn_enabled", False) is True

    def _semantic_sync_timeout(self) -> float:
        from .semantic_turn_evidence import DEFAULT_SYNC_TIMEOUT
        try:
            value = float(getattr(self.settings, "semantic_same_turn_timeout_seconds",
                                  DEFAULT_SYNC_TIMEOUT))
        except (TypeError, ValueError):
            return DEFAULT_SYNC_TIMEOUT
        return value if value > 0 else DEFAULT_SYNC_TIMEOUT

    def _semantic_turn_evidence(self):
        """This burst's TurnEvidence, or None. Bounded wait, no second call, no guessing.

        Only a turn that actually needs semantic evidence for an authoritative field ever
        calls this — every FAQ, vehicle, location and acceptance turn returns without
        waiting, because it never gets here. A timeout, a failed call or an unparseable
        response all return None, which means *no semantic evidence*: the caller then has
        deterministic evidence only, exactly as in L4.7C.4.
        """
        if not self._same_turn_semantic_on():
            return None
        provider = getattr(self, "_turn_semantic", None)
        if provider is None:
            return None
        result = provider.get(timeout=self._semantic_sync_timeout())
        if result is None or not getattr(result, "ok", False):
            return None
        return getattr(result, "evidence", None)

    # ── L4.7W2-F1 — bounded locality recovery from imperfect speech ──────────

    def _semantic_location_fragment(self, burst_text: str):
        """This burst's proposed inspection-location span, or None. No extra model call.

        Reads the SAME TurnSemanticEvidence provider that C4A already dispatches for
        scheduling, so the interpretation is shared and `calls` stays at 1. Wild W2 proved
        the evidence was there and simply had no consumer: the interpreter reported
        `{"value": "Tegui", "role": "INSPECTION_LOCATION"}` 2 s after CE had already
        answered, and nothing read it.
        """
        from .locality_resolver import LocationFragment
        evidence = self._semantic_turn_evidence()
        if evidence is None:
            return None
        for mention in getattr(evidence, "location_mentions", ()) or ():
            role = getattr(mention, "role", None)
            role = str(getattr(role, "value", role) or "").upper()
            value = getattr(mention, "value", None)
            # An origin mention is never a candidate for the inspection locality.
            fragment = LocationFragment.from_evidence(
                text=str(value or ""), role=role,
                producer="semantic:understand", source_text=burst_text or "")
            if fragment is not None and fragment.is_inspection_role:
                return fragment
        return None

    def _recover_locality(self, ctx: "_Context", state, burst_text: str):
        """Try to recover a locality the deterministic resolver could not match.

        Returns a `LocalityMatch` or None. Writes nothing: an APPROXIMATE result is a
        proposal that the customer must confirm, because a confident match on a corrupted
        token is still a guess about what was said.
        """
        from sqlalchemy import select as _select
        from .locality_resolver import resolve_locality_fragment
        fragment = self._semantic_location_fragment(burst_text)
        if fragment is None:
            return None
        try:
            catalog = list(self.db.execute(_select(ViaticosZone)).scalars().all())
            match = resolve_locality_fragment(fragment, catalog)
        except Exception as exc:
            logger.warning("L4.7W2-F1 locality recovery failed thread_id=%s: %s",
                           getattr(ctx.thread, "id", None), exc)
            return None
        logger.info(
            "L4.7W2-F1 LOCALITY thread_id=%s fragment=%r role=%s status=%s best=%s "
            "score=%.3f rule=%s@%s",
            getattr(ctx.thread, "id", None), fragment.text, fragment.role, match.status,
            (match.best.zone_detail if match.best else None),
            (match.best.score if match.best else 0.0), match.rule_id, match.rule_version)
        # ── G3-1: observational. The fragment IS the evidence this decision consumed. ──
        # It is expressed as a claim carrying the fragment's OWN producer literal, so the
        # source is read from provenance rather than assumed. The zone catalogue validated
        # the fragment but produced no competing claim of its own, so this row is
        # SINGLE_PRODUCER: SEMANTIC — and `canonical_effect` is NONE, because P2 permits a
        # proposal and a question and nothing else. This milestone changes no behaviour.
        from ..schemas.claims import (ClaimEvidence, ClaimType, EvidenceClass,
                                      Explicitness)
        from ..schemas.hybrid_trace import CanonicalEffect
        try:
            _claims = [ClaimEvidence(
                claim_type=ClaimType.INSPECTION_LOCATION, value=fragment.text,
                evidence_class=EvidenceClass.SEMANTIC_INFERRED,
                producer=getattr(fragment, "producer", None) or "semantic:understand",
                explicitness=Explicitness.IMPLIED,
                cycle_id=self._reconciler_cycle_id(state)).with_id()]
        except Exception:
            _claims = []
        _proposes = bool(match.needs_confirmation and match.best is not None)
        self._trace_decision(
            decision_site_id=DecisionSite.LOCALITY_SEMANTIC_RECOVERY,
            claim_type=ClaimType.INSPECTION_LOCATION, claims=_claims,
            outcome=match.status, reason=match.reason,
            rule_id=match.rule_id, rule_version=match.rule_version,
            authority_result=match.status,
            canonical_effect=CanonicalEffect.NONE,
            permitted_action=("ask_locality_confirmation" if _proposes else None),
            business_outcome=("locality proposed for customer confirmation" if _proposes
                              else f"no proposal ({match.status})"))
        return match

    def _location_proposal_key(self, state, zone_group, zone_detail) -> str:
        return f"{zone_group or ''}||{zone_detail or ''}||{self._reconciler_cycle_id(state) or ''}"

    def _pending_location_proposal(self, state):
        """The locality awaiting confirmation IN THIS CYCLE, or None.

        A proposal from a finished cycle is not evidence about this one — the cycle id is
        part of the stored key, so a stale "Sí" can never confirm it.
        """
        raw = getattr(state, "pending_location_proposal", None)
        if not raw:
            return None
        parts = str(raw).split("||")
        if len(parts) != 3:
            return None
        group, detail, cycle = parts
        if cycle != (self._reconciler_cycle_id(state) or ""):
            return None
        return (group or None, detail or None)

    def _clear_location_proposal(self, state) -> None:
        if getattr(state, "pending_location_proposal", None):
            state.pending_location_proposal = None

    def _propose_locality(self, ctx: "_Context", state, match):
        """Ask the customer to confirm an approximate locality. Writes no canonical value."""
        best = match.best
        state.pending_location_proposal = self._location_proposal_key(
            state, best.zone_group, best.zone_detail)
        self.db.commit()
        self._answer_source = "LOCATION_RESOLVER"
        question = _LOCALITY_CONFIRMATION_TEMPLATE.format(zone=best.zone_detail)
        try:
            sent_id = self._send_text_to_wa(ctx, question)
        except OutboundBlockedError:
            self.db.commit()
            return _out("location_proposal_blocked", detail="outbound_disabled")
        self._decision_log(ctx, "location_proposal_armed", source="semantic_bounded",
                           zone=best.zone_detail, group=best.zone_group,
                           score=round(best.score, 3))
        return _out("replied", wa_message_id=sent_id)

    def _semantic_scheduling_claims(self, state) -> list:
        """Project this burst's semantic reading into scheduling claims. Never mutates."""
        evidence = self._semantic_turn_evidence()
        if evidence is None:
            return []
        try:
            from ..schemas.claims import ClaimType
            from .claim_projection import claims_from_turn_evidence
            revision_id = getattr(state, "current_revision_id", None)
            claims = claims_from_turn_evidence(
                evidence,
                texts=list(getattr(self, "_turn_semantic_texts", ()) or ()),
                cycle_id=self._reconciler_cycle_id(state),
                revision_id=(revision_id if isinstance(revision_id, int) else None))
            return [c for c in claims
                    if c.claim_type == ClaimType.SCHEDULING_PREFERENCE]
        except Exception as exc:      # a projection failure is absent evidence, never a guess
            try:
                logger.warning("L4.7C.4A semantic scheduling projection failed: %s", exc)
            except Exception:
                pass
            return []

    @staticmethod
    def _offer_outstanding(state) -> bool:
        """True when a scheduling option set is actually on the table right now.

        Rejecting options nobody was shown is not a rejection. Three things each mean an
        outstanding offer: a day under negotiation, slots visibly offered for it, or a live
        Booking Flow token — the Flow IS the option set.
        """
        if getattr(state, "flow_booking_token", None):
            return True
        if getattr(state, "active_requested_date", None):
            return True
        raw = getattr(state, "last_offered_slots", None)
        if not raw:
            return False
        try:
            return bool(json.loads(str(raw)))
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _turn_took_an_option(texts: list[str]) -> bool:
        """True when this same burst named a day or a time — negotiating, not refusing.

        Uses the scheduling parser the router already runs, so "el viernes no, pero el
        sábado sí" is a selection no matter how the first half sounds. No new vocabulary.
        """
        day, time_str = _parse_scheduling_text(texts, _business_today())
        return bool(day or time_str)

    def _semantic_handoff_requested(self, state) -> bool:
        """True when THIS burst's semantic reading asks for a person. Never mutates.

        L4.7W5-F7C. `claim_projection` already turns `handoff.requested` into a
        `NEEDS_HUMAN` claim; until now only the shadow reconciler read it, so an ordinary
        "¿me puede ayudar una persona?" reached no routing decision because its verb was
        absent from `_HUMAN_REQUEST_PATTERNS`. This consumes the existing projection — it
        does not add a second producer, a phrase list, a prompt or a schema field.

        Same-turn by construction: `_semantic_turn_evidence` reads `self._turn_semantic`,
        which `_handle` resets to None and rebinds to a provider carrying this burst's
        `burst_id` and `message_ids`. Evidence from an earlier turn cannot be seen here.

        Unresolved evidence is not evidence: an AMBIGUOUS or CONFLICT claim is refused, so
        uncertainty produces no escalation. A timeout or a failed call returns None from
        the provider and this returns False, leaving the deterministic floor in charge.
        """
        evidence = self._semantic_turn_evidence()
        if evidence is None:
            return False
        try:
            from ..schemas.claims import ClaimType
            from ..schemas.turn_evidence import UNRESOLVED_STATUSES
            from .claim_projection import claims_from_turn_evidence
            revision_id = getattr(state, "current_revision_id", None)
            claims = claims_from_turn_evidence(
                evidence,
                texts=list(getattr(self, "_turn_semantic_texts", ()) or ()),
                cycle_id=self._reconciler_cycle_id(state),
                revision_id=(revision_id if isinstance(revision_id, int) else None))
            handoff_claims = [c for c in claims if c.claim_type == ClaimType.NEEDS_HUMAN]
            requested = any(c.value is True and c.status not in UNRESOLVED_STATUSES
                            for c in handoff_claims)
            # ── G3-1: observational. Only the NEEDS_HUMAN claims the decision read. ──
            # CE contributed nothing here and none is invented: with the interpreter as the
            # only producer this row is SINGLE_PRODUCER: SEMANTIC, which is the truth and
            # is exactly what P1 permits. A refused claim (negated, ambiguous, unresolved)
            # is still recorded — the reader must be able to see that the system looked and
            # declined, which is different from never having looked.
            from ..schemas.hybrid_trace import CanonicalEffect
            self._trace_decision(
                decision_site_id=DecisionSite.HANDOFF_SEMANTIC_REQUEST,
                claim_type=ClaimType.NEEDS_HUMAN, claims=handoff_claims,
                outcome=("ACCEPT" if requested else "HOLD"),
                reason=(_G3_HANDOFF_REASON_YES if requested else _G3_HANDOFF_REASON_NO),
                rule_id=_G3_HANDOFF_RULE_ID, rule_version=_G3_RULE_VERSION,
                evidence_ids=tuple(c.claim_id for c in handoff_claims if c.claim_id),
                authority_result=("ACCEPT" if requested else "HOLD"),
                canonical_effect=(CanonicalEffect.WROTE if requested
                                  else CanonicalEffect.NONE),
                permitted_action=(_G3_HANDOFF_ACTION if requested else None),
                business_outcome=(_G3_HANDOFF_OUTCOME_YES if requested
                                  else _G3_HANDOFF_OUTCOME_NO))
            return requested
        except Exception as exc:   # a projection failure is absent evidence, never a guess
            try:
                logger.warning("L4.7W5-F7C semantic handoff projection failed: %s", exc)
            except Exception:
                pass
            return False

    # ── L4.7C.2 — the single canonical write path for vehicle and location ────
    #
    # Every canonical vehicle-identity and inspection-location write goes through these two
    # methods. With the flags OFF they assign exactly what the legacy code assigned, so the
    # cutover is reversible by configuration alone. With a flag ON the proposed value is
    # submitted as a claim, reconciled under a named rule, and applied only on ACCEPT — the
    # legacy parsers stay evidence producers and validators, but stop being writers.

    def _vehicle_authority_on(self) -> bool:
        return getattr(self.settings, "reconciler_vehicle_authority_enabled", False) is True

    def _location_authority_on(self) -> bool:
        return getattr(self.settings, "reconciler_location_authority_enabled", False) is True

    def _reconciler_cycle_id(self, state) -> "str | None":
        """The thread's cycle identity, as a string. ONE definition, shared with the store.

        SEQ-05. This used to fall back to `current_cycle_started_at` and then to `None`,
        mixing a message id with a timestamp into one string, so the same cycle could be
        keyed two ways by two subsystems in a single turn. It now derives from exactly the
        same integer the clarification attempt store uses, so the reconciler and the
        one-attempt invariant can never disagree about which cycle this is.
        """
        from .clarification_attempt import cycle_key
        return str(cycle_key(state))

    def _record_reconciliation(self, ctx, decision, claim_type: str, state=None,
                               claims=(), *, decision_site_id: str = None) -> None:
        """Append the justification. Log-only in C2: no new table, no migration.

        `claims` is the reconciler's input set, passed through so the hybrid trace can say
        WHICH producer contributed to each decision. It is read, never mutated, and its
        absence degrades the trace only — the JSONL record is unchanged either way.

        `decision_site_id` is a stable literal from `DecisionSite`, supplied by the caller
        because only the caller knows which question it is asking. Two call sites reconcile
        `vehicle.model` for entirely different purposes, and the Inspector could not tell
        them apart; deriving the site from a function name or a stack frame would tie an
        operator's reading of a stored trace to a refactor, so it is passed, not inferred.
        """
        try:
            record = decision.to_record(
                claim_type=claim_type,
                cycle_id=self._reconciler_cycle_id(state if state is not None
                                                   else getattr(ctx, "state", None)),
                revision_id=getattr(getattr(ctx, "state", None), "current_revision_id", None))
            logger.info(
                "L4.7C.2 RECONCILE thread_id=%s claim=%s outcome=%s rule=%s@%s state=%s "
                "value=%r evidence=%s reason=%s",
                getattr(ctx.thread, "id", None), record.claim_type, record.outcome,
                record.rule_id, record.rule_version, record.information_state,
                record.canonical_value, list(record.evidence_ids), record.reason)
            # Durable justification: append-only JSONL beside the shadow record. No new
            # table and no migration (L4.7C.2 Part 16); a decision must survive the log
            # buffer to be auditable at all.
            self._append_reconciliation_record(ctx, record)
            self._collect_trace_reconciliation(record, claims,
                                               decision_site_id=decision_site_id)
        except Exception:
            pass

    # ── L4.7W5 Gate 1/2: hybrid decision trace ────────────────────────────────
    # Everything below is observational. It reads values the turn has already computed,
    # never calls a producer, never waits on one, and writes only its own row. Every method
    # swallows its own exceptions: a decision that was correctly taken must not be lost
    # because the record of it could not be written.

    def _hybrid_trace_on(self) -> bool:
        # `self` may legitimately lack `settings` — the transport-containment suite builds a
        # bare engine to prove the crash handler. An observer that raises on a half-built
        # engine would be participating in the turn, which is the one thing it may not do.
        return getattr(getattr(self, "settings", None),
                       "hybrid_trace_enabled", False) is True

    def _capture_trace_before(self, ctx, state) -> None:
        if not self._hybrid_trace_on():
            return
        try:
            from .hybrid_trace import snapshot_state
            self._turn_trace_ctx = ctx
            self._turn_trace_before = snapshot_state(
                state, getattr(ctx, "lead", None), offer=self._turn_offer_for_trace(ctx))
        except Exception:
            self._turn_trace_before = None

    def _collect_trace_reconciliation(self, record, claims=(), *,
                                      decision_site_id: str = None) -> None:
        """Capture one reconciliation row, in the order the turn produced it.

        Nothing positional is passed. A comparison's identity is derived from what it IS —
        the decision site, the proposition, the rule, and the content hashes of the claims
        weighed — so inserting another reconciliation ahead of this one cannot rename it.
        Repeat executions are numbered by `finalize_rows`, which can see the whole turn.
        """
        if not self._hybrid_trace_on():
            return
        try:
            from .hybrid_trace import reconciliation_from
            self._turn_trace_reconciliations.append(
                reconciliation_from(record, claims,
                                    decision_site_id=decision_site_id))
        except Exception:
            pass

    class _TraceDecisionRecord:
        """A decision expressed in the shape `reconciliation_from` already reads.

        G3-1 traces four sites whose authorities return four different result types — a
        `SchedulingDecision`, an `AuthorizationDecision`, a bool, a `LocalityMatch`. Rather
        than teach the observer four shapes, each site states what it decided in the words
        of its own authority and this carries them. It holds no logic and no defaults that
        could invent a value: a field the site does not supply stays None.
        """
        __slots__ = ("claim_type", "information_state", "outcome", "reason",
                     "rule_id", "rule_version", "evidence_ids")

        def __init__(self, *, claim_type, outcome=None, reason=None, rule_id=None,
                     rule_version=None, information_state=None, evidence_ids=()):
            self.claim_type = claim_type
            self.information_state = information_state
            self.outcome = outcome
            self.reason = reason
            self.rule_id = rule_id
            self.rule_version = rule_version
            self.evidence_ids = tuple(evidence_ids or ())

    def _trace_decision(self, *, decision_site_id, claim_type, claims=(),
                        outcome=None, reason=None, rule_id=None, rule_version=None,
                        information_state=None, evidence_ids=(),
                        authority_result=None, domain_verdict=None,
                        canonical_effect=None, permitted_action=None,
                        business_outcome=None) -> None:
        """Record one already-made decision. Observational, fail-open, never re-derives.

        G3-1. Four production decisions were already hybrid and already invisible. This
        records what each one actually consumed and actually decided — it does not re-parse
        the burst, does not call the provider, does not build a claim the decision did not
        see, and does not touch the decision itself.

        G3-1-R2. The failure stays fail-open — a trace defect must never cost a customer
        turn — but it is no longer silent. A systematically failing observer used to leave
        no trace of its own failure, which is the one bug an observability layer cannot
        afford. The diagnostic carries the operation, the contract version, the exception
        class, the thread id and the deployment SHA, and nothing else: no message text, no
        claim values, no identifiers beyond the thread the operator already sees.
        """
        if not self._hybrid_trace_on():
            return
        try:
            from .hybrid_trace import reconciliation_from
            record = self._TraceDecisionRecord(
                claim_type=claim_type, outcome=outcome, reason=reason,
                rule_id=rule_id, rule_version=rule_version,
                information_state=information_state, evidence_ids=evidence_ids)
            self._turn_trace_reconciliations.append(
                reconciliation_from(
                    record, tuple(claims or ()),
                    decision_site_id=decision_site_id,
                    authority_result=authority_result,
                    domain_verdict=domain_verdict,
                    canonical_effect=canonical_effect,
                    permitted_action=permitted_action,
                    business_outcome=business_outcome))
        except Exception as exc:
            try:
                from ..schemas.hybrid_trace import TRACE_VERSION
                from .outbound_path_registry import get_deployment_id
                _ctx = getattr(self, "_turn_trace_ctx", None)
                logger.warning(
                    "HYBRID_TRACE_COLLECT_FAILED operation=_trace_decision site=%s "
                    "contract=%s error=%s thread_id=%s deployment=%s",
                    decision_site_id, TRACE_VERSION, type(exc).__name__,
                    getattr(getattr(_ctx, "thread", None), "id", None),
                    get_deployment_id())
            except Exception:
                pass

    def _trace_outbound_for_turn(self, thread_id) -> dict:
        """What the gate actually recorded for this turn, by correlation id.

        Read from the ledger rather than reported by the send path, so the trace agrees
        with the forensic authority instead of offering a second, competing account. Only
        the WAMID tail is kept — enough to join to the ledger, never a customer identifier.
        """
        try:
            if thread_id is None:
                return {}
            row = self.db.execute(
                select(WhatsAppMessage)
                .where(WhatsAppMessage.thread_id == thread_id,
                       WhatsAppMessage.direction == "out",
                       WhatsAppMessage.correlation_id == getattr(self, "_correlation_id", None))
                .order_by(WhatsAppMessage.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return {}
            wamid = row.wa_message_id or ""
            return {"message_id": row.id, "path_id": row.path_id, "status": row.status,
                    "wamid_tail": (wamid[-8:] if wamid else None)}
        except Exception:
            return {}

    def _turn_plan(self, out):
        """The turn's response plan: the LIVE one where it composed, projected otherwise.

        G3-5F §5. When the turn reached the dispatch boundary, `_live_plan` is the object
        that actually decided what the customer was told — its propositions, its policy
        verdicts and its suppressions are the record, not a reconstruction of them. It is
        returned as-is, with `shadow=False`.

        A turn that never composed — an early gate, a dedup, an error — still owes an
        account of itself, and for those the projection below is exactly what G3-5E built:
        a description assembled from what the decision path recorded. It is marked
        `shadow=True`, so the Inspector can tell "this is what the plan decided" from
        "this is what the turn amounted to".
        """
        plan = getattr(self, "_live_plan", None)
        if plan is not None and plan.composed:
            try:
                decision = getattr(self, "_availability_authority_cache", None)
                burst = getattr(self, "_canonical_turn_burst", None)
                plan.decision_phase_id = getattr(decision, "decision_phase_id", None)
                plan.burst_fingerprint = getattr(burst, "fingerprint", None)
                plan.outcome = getattr(out, "action", None)
                return plan.to_plan(
                    plan_id=f"response-plan:{plan.decision_phase_id or 'nophase'}",
                    renderer_source=(getattr(self, "_turn_renderer_source", None)
                                     or RendererSource.NO_PROSE),
                    authority=(getattr(self, "_turn_decision_authority", None)
                               or DecisionAuthority.NONE),
                    fact_sources=tuple(dict.fromkeys(
                        getattr(self, "_turn_fact_sources", None) or ())),
                    outcome=getattr(out, "action", None))
            except Exception as exc:
                try:
                    logger.warning("G3-5F live plan projection failed: %s",
                                   type(exc).__name__)
                except Exception:
                    pass
        return self._shadow_response_plan(out)

    def _shadow_response_plan(self, out):
        """Describe, in typed terms, what this turn owed the customer — and observe only.

        G3-5E. This is a SHADOW representation of behaviour that already happened. It is
        built after the turn from state the decision path recorded, so by construction it
        cannot dispatch, cannot mutate canonical state, cannot cause a model call and cannot
        create a second reply: it reads, it never calls. Exactly one plan exists per
        decision phase — the phase id is part of its identity.

        The next milestone turns this into the live authority. Until then the plan's job is
        to be comparable with what actually happened, so a divergence is visible before it
        is load-bearing.
        """
        from ..schemas.hybrid_trace import (
            CoexistencePolicy, ResponseItem, ResponseItemKind, ResponsePlan,
            ResponsePolicy, TransportMultiplicity)
        try:
            burst = getattr(self, "_canonical_turn_burst", None)
            decision = getattr(self, "_availability_authority_cache", None)
            phase = getattr(decision, "decision_phase_id", None)
            fingerprint = getattr(burst, "fingerprint", None)
            evidence_ids = tuple(getattr(burst, "ordered_message_ids", ()) or ())
            action = getattr(out, "action", None)
            renderer = (getattr(self, "_turn_renderer_source", None)
                        or RendererSource.NO_PROSE)
            # G3-5E2 (audit G5E-02): no default. The plan projects exactly what the
            # producing path recorded; where nothing decided, it says so.
            authority = (getattr(self, "_turn_decision_authority", None)
                         or DecisionAuthority.NONE)
            dispatch_path = getattr(self, "_turn_dispatch_path", None) or DispatchPath.NONE
            dispatch_result = (getattr(self, "_turn_dispatch_result", None)
                               or DispatchResult.NOT_ATTEMPTED)
            facts = tuple(dict.fromkeys(getattr(self, "_turn_fact_sources", None) or ()))

            items: list = []
            if getattr(self, "_turn_flow_dispatched", False):
                # P4: a Flow is a typed two-part sequence, never two free-text replies.
                items.append(ResponseItem(
                    kind=ResponseItemKind.FLOW_INTRO_TEXT, claim_family="scheduling",
                    proposition_id="scheduling.flow_intro", evidence_ids=evidence_ids,
                    decision_authority=authority, fact_sources=facts,
                    renderer_source=RendererSource.DETERMINISTIC_TEMPLATE,
                    coexistence=CoexistencePolicy.REQUIRED, priority=10,
                    dispatch_path=DispatchPath.TEXT,
                    policy=ResponsePolicy.P4_FLOW_INTRO_SEQUENCE))
                items.append(ResponseItem(
                    kind=ResponseItemKind.META_FLOW, claim_family="scheduling",
                    proposition_id="scheduling.booking_flow", evidence_ids=evidence_ids,
                    decision_authority=authority, fact_sources=facts,
                    renderer_source=RendererSource.META_FLOW,
                    coexistence=CoexistencePolicy.REQUIRED, priority=20,
                    dispatch_path=DispatchPath.META_FLOW,
                    policy=ResponsePolicy.P4_FLOW_INTRO_SEQUENCE))
                multiplicity = TransportMultiplicity.FLOW_INTRO_THEN_FLOW
            else:
                multiplicity = TransportMultiplicity.SINGLE_MESSAGE
                if (action in ("replied", "blocked_dispatch")
                        or getattr(self, "_turn_text_dispatches", 0)):
                    kind = (ResponseItemKind.CLARIFICATION
                            if getattr(decision, "authority", None) == "CLARIFY"
                            else ResponseItemKind.TEXT)
                    items.append(ResponseItem(
                        kind=kind, claim_family="turn",
                        proposition_id="turn.primary_reply", evidence_ids=evidence_ids,
                        classification=getattr(decision, "classification", None),
                        decision_authority=authority, fact_sources=facts,
                        renderer_source=renderer,
                        required_action=getattr(decision, "authority", None),
                        coexistence=CoexistencePolicy.REQUIRED, priority=10,
                        dispatch_path=dispatch_path,
                        policy=ResponsePolicy.P3_SINGLE_MESSAGE_DEFAULT))
                elif action in ("skipped_human",):
                    items.append(ResponseItem(
                        kind=ResponseItemKind.NO_RESPONSE, claim_family="handoff",
                        proposition_id="handoff.human_owned", evidence_ids=evidence_ids,
                        decision_authority=DecisionAuthority.HUMAN_HANDOFF_POLICY,
                        fact_sources=(FactSource.CANONICAL_STATE,),
                        renderer_source=RendererSource.NO_PROSE,
                        coexistence=CoexistencePolicy.EXCLUSIVE, priority=0,
                        policy=ResponsePolicy.P2_HUMAN_HANDOFF_EXCLUSIVE))
                else:
                    items.append(ResponseItem(
                        kind=ResponseItemKind.NO_RESPONSE, claim_family="turn",
                        proposition_id="turn.no_response", evidence_ids=evidence_ids,
                        decision_authority=authority,
                        renderer_source=RendererSource.NO_PROSE, priority=100))

            # The one suppression this milestone can observe truthfully: the hours
            # proposition the authority withheld and the reason it gave.
            if decision is not None and getattr(decision, "suppress_hours", False):
                items.append(ResponseItem(
                    kind=ResponseItemKind.SUPPRESSED, claim_family="faq",
                    proposition_id="faq.business_hours", evidence_ids=evidence_ids,
                    classification=getattr(decision, "classification", None),
                    decision_authority=DecisionAuthority.RECONCILER,
                    fact_sources=(FactSource.FAQ_CANONICAL,),
                    renderer_source=RendererSource.NO_PROSE,
                    coexistence=CoexistencePolicy.COMPATIBLE, priority=50,
                    included=False,
                    suppression_reason=(getattr(decision, "hours_suppression_reason", None)
                                        or getattr(decision, "hours_disposition", None)),
                    policy=ResponsePolicy.P1_HOLD_PLUS_SAFE_FAQ))

            return ResponsePlan(
                # G3-5E2 (audit G5E-06): the phase id already begins with the burst
                # fingerprint, so repeating it produced "fp:fp:snapshot". A typed prefix
                # plus the phase identity is deterministic, unique per phase and carries
                # no customer text.
                plan_id=f"response-plan:{phase or 'nophase'}",
                turn_id=getattr(self, "_correlation_id", None),
                burst_fingerprint=fingerprint, decision_phase_id=phase,
                items=tuple(items), transport_multiplicity=multiplicity,
                renderer_source=renderer, dispatch_path=dispatch_path,
                dispatch_result=dispatch_result, shadow=True, outcome=action)
        except Exception as exc:      # observational: never costs a customer turn
            try:
                logger.warning("G3-5E shadow plan failed: %s", type(exc).__name__)
            except Exception:
                pass
            return None

    def _write_hybrid_trace(self, event, out, started_at: str, duration_ms: int) -> None:
        if not self._hybrid_trace_on():
            return
        try:
            from .hybrid_trace import (build_trace, ce_evidence_for, persist,
                                       semantic_evidence_from, snapshot_state)
            from .outbound_path_registry import get_deployment_id
            from ..schemas.hybrid_trace import CanonicalSnapshot

            ctx = self._turn_trace_ctx
            thread_id = getattr(getattr(ctx, "thread", None), "id", None) or event.thread_id
            lead = getattr(ctx, "lead", None)
            before = self._turn_trace_before or CanonicalSnapshot()
            _claim_for_trace = getattr(self, "_turn_offer_claim", None)
            after = snapshot_state(
                getattr(ctx, "state", None), lead,
                offer=self._turn_offer_for_trace(ctx),
                refusal=(_claim_for_trace.refusal
                         if _claim_for_trace is not None and not _claim_for_trace.ok
                         else None))

            # ── G3-5B (audit G5A-01): the envelope describes ONE boundary ───────────
            #
            # `input_hash` used to be computed from `_turn_burst_texts` (the presentation
            # subset) while `ordered_message_ids` came from the database rows, so the hash
            # and the identifiers described different sets of messages and their counts
            # could disagree. Both now project from the canonical burst — the same records
            # the interpreter, the deterministic readers and the authority all used.
            canonical = getattr(self, "_canonical_turn_burst", None)
            if canonical is not None and canonical.messages:
                texts = list(canonical.texts)
                rows = [{"db_id": None, "wa_message_id": m.message_id,
                         "timestamp": m.timestamp} for m in canonical.messages]
            else:
                texts = list(getattr(self, "_turn_burst_texts", []) or [])
                if not texts and getattr(event, "text", None):
                    texts = [event.text]
                rows = list(getattr(self, "_turn_trace_rows", []) or [])
                if not rows:
                    # A turn that early-returned before burst reconstruction, or a thread's
                    # very first message: the triggering WAMID is all that exists.
                    rows = [{"db_id": None,
                             "wa_message_id": getattr(event, "wa_message_id", None),
                             "timestamp": None}]

            semantic = semantic_evidence_from(
                getattr(self, "_turn_semantic", None),
                "async" if self._shadow_async() else "sync")
            ce_rules = ce_evidence_for(texts, before, out.action)

            plan = getattr(self, "_turn_response_plan", None)
            from ..schemas.hybrid_trace import ReplyModelRecord
            reply_record = ReplyModelRecord(
                model=((getattr(self.settings, "openai_chat_model", "") or "gpt-4o-mini")
                       if getattr(self, "_reply_calls", 0) else None),
                prompt_version=(REPLY_PROMPT_VERSION
                                if getattr(self, "_reply_calls", 0) else None),
                role=getattr(self, "_reply_role", None),
                admission=getattr(self, "_reply_admission_state", None),
                calls=getattr(self, "_reply_calls", 0),
                latency_ms=getattr(self, "_reply_latency_ms", None),
                status=getattr(self, "_reply_status", "ABSENT"))

            trace = build_trace(
                turn_id=getattr(self, "_correlation_id", None) or "",
                thread_id=thread_id,
                lead_id=getattr(lead, "id", None),
                deployment_sha=get_deployment_id(),
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                duration_ms=duration_ms,
                ordered_message_ids=tuple(r.get("wa_message_id") for r in rows),
                message_timestamps=tuple(r.get("timestamp") for r in rows),
                burst_texts=texts,
                semantic=semantic,
                ce_rules=ce_rules,
                reconciliations=tuple(getattr(self, "_turn_trace_reconciliations", []) or ()),
                before=before, after=after,
                action=out.action, detail=out.detail,
                answer_source=out.answer_source,
                outbound=self._trace_outbound_for_turn(thread_id),
                # ── G3-5E contract 1.4 ──
                decision_authority=getattr(self, "_turn_decision_authority", None),
                fact_sources=tuple(dict.fromkeys(
                    getattr(self, "_turn_fact_sources", None) or ())),
                renderer_source=getattr(self, "_turn_renderer_source", None),
                dispatch_path=getattr(self, "_turn_dispatch_path", None),
                dispatch_result=getattr(self, "_turn_dispatch_result", None),
                reply=reply_record, response_plan=plan,
                final_message_count=getattr(self, "_turn_text_dispatches", 0),
                flow_participation=("META_FLOW"
                                    if getattr(self, "_turn_flow_dispatched", False)
                                    else None),
                burst_fingerprint=getattr(canonical, "fingerprint", None),
                decision_phase_id=getattr(
                    getattr(self, "_availability_authority_cache", None),
                    "decision_phase_id", None),
                # ── G3-7B contract 1.6 ──
                candidate_resolution=getattr(self, "_turn_candidate_resolution", None),
                # ── G3-7F contract 1.7 ──
                pending_correction=self._pending_correction_for_trace(ctx),
                # ── G3-7F-OWNER-RESOLUTION contract 1.8 ──
                clarification=self._clarification_for_trace(),
                # ── SB2B contract 1.10 ──
                booking=self._sb_trace_payload(),
            )
            persist(self.db, trace)
        except Exception as exc:
            try:
                logger.warning("HYBRID_TRACE_BUILD_FAILED thread_id=%s category=%s",
                               getattr(event, "thread_id", None), type(exc).__name__)
            except Exception:
                pass

    def _append_reconciliation_record(self, ctx, record) -> None:
        """Append one justification. Never raises; a write failure loses no canonical state."""
        import json as _json
        import pathlib as _pathlib
        from datetime import datetime as _dt, timezone as _tz
        raw_path = getattr(self.settings, "shadow_evidence_path", "")
        if not isinstance(raw_path, str) or not raw_path:
            return
        target = _pathlib.Path(raw_path).with_name("reconciliation_records.jsonl")
        payload = {
            "record_version": "reconciliation-record/1.0",
            "recorded_at": _dt.now(_tz.utc).isoformat(timespec="milliseconds"),
            "thread_id": getattr(ctx.thread, "id", None),
            "claim_type": record.claim_type, "outcome": record.outcome,
            "information_state": record.information_state, "risk_tier": record.risk_tier,
            "rule_id": record.rule_id, "rule_version": record.rule_version,
            "evidence_ids": list(record.evidence_ids), "reason": record.reason,
            "cycle_id": record.cycle_id, "revision_id": record.revision_id,
            "depends_on": list(record.depends_on), "supersedes": list(record.supersedes),
            "shadow": record.shadow,
        }
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as handle:      # append-only
                handle.write(_json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _apply_vehicle_identity(self, ctx, target, *, marca=None, modelo=None, tipo=None,
                                anio=None, source: str = "ce", texts=(), state=None) -> bool:
        """Write vehicle identity onto a candidate. Returns True when anything was written."""
        if not self._vehicle_authority_on():
            wrote = False
            if marca:
                target.marca = marca; wrote = True
            if modelo:
                target.modelo = modelo; wrote = True
            if tipo:
                target.tipo_vehiculo = tipo; wrote = True
            if anio:
                target.anio = anio; wrote = True
            return wrote

        from ..schemas.claims import ClaimEvidence, ClaimType, EvidenceClass, Explicitness
        from .field_reconciler import reconcile_vehicle_identity
        from .vehicle_catalog import lookup_vehicle

        evidence_class = {
            "flow": EvidenceClass.HUMAN_CONFIRMED,
            "form": EvidenceClass.HUMAN_CONFIRMED,
            "catalog": EvidenceClass.CATALOG_CONFIRMED,
        }.get(source, EvidenceClass.DETERMINISTIC_EXTRACTED)
        cycle_id = self._reconciler_cycle_id(state if state is not None else ctx.state)
        claims = []
        for claim_type, value in ((ClaimType.VEHICLE_MODEL, modelo),
                                  (ClaimType.VEHICLE_MAKE, marca),
                                  (ClaimType.VEHICLE_YEAR, anio),
                                  (ClaimType.VEHICLE_CATEGORY, tipo)):
            if value in (None, "", []):
                continue
            claims.append(ClaimEvidence(
                claim_type=claim_type, value=value, evidence_class=evidence_class,
                producer=f"ce:{source}", explicitness=Explicitness.STATED,
                cycle_id=cycle_id).with_id())

        decision = reconcile_vehicle_identity(claims, catalog_lookup=lookup_vehicle)
        self._record_reconciliation(
            ctx, decision, ClaimType.VEHICLE_MODEL, state, claims,
            decision_site_id=DecisionSite.VEHICLE_IDENTITY_APPLY)
        if not decision.accepted:
            # CLARIFY / HOLD: the legacy value is NOT written. The turn continues on the
            # existing conversational path, which asks rather than assumes.
            return False
        identity = decision.value
        wrote = False
        if identity.marca:
            target.marca = identity.marca; wrote = True
        if identity.modelo:
            target.modelo = identity.modelo; wrote = True
        if identity.tipo_vehiculo:
            target.tipo_vehiculo = identity.tipo_vehiculo; wrote = True
        if identity.anio:
            target.anio = identity.anio; wrote = True
        elif anio:
            target.anio = anio; wrote = True          # the year travels with the identity
        return wrote

    def _apply_inspection_zone(self, ctx, target, *, zone_group=None, zone_detail=None,
                               source: str = "ce", role: str = "INSPECTION_LOCATION",
                               state=None, claims=None) -> bool:
        """Write an inspection locality onto a candidate or onto the pre-candidate buffer.

        `claims` lets a caller hand in the claims it already holds instead of having this
        method compose one from `(zone_group, zone_detail)`. W1F-W-01 R1's admission
        boundary is the one caller that uses it: it has a turn's claims from BOTH producers
        and the whole point is that `reconcile_inspection_location` — not the boundary, and
        not a second copy of its ladder — decides which producer wins. Every existing caller
        passes nothing and gets exactly the single claim it got before.
        """
        if not self._location_authority_on():
            wrote = False
            if zone_group is not None:
                target.zone_group = zone_group; wrote = True
            if zone_detail is not None:
                target.zone_detail = zone_detail; wrote = True
            return wrote

        from ..schemas.claims import ClaimEvidence, ClaimType, EvidenceClass, Explicitness
        from .field_reconciler import reconcile_inspection_location

        # An origin claim can never populate an inspection location (rule E).
        claim_type = (ClaimType.INSPECTION_LOCATION if role == "INSPECTION_LOCATION"
                      else ClaimType.CUSTOMER_ORIGIN)
        evidence_class = {
            "flow": EvidenceClass.HUMAN_CONFIRMED,
            "form": EvidenceClass.HUMAN_CONFIRMED,
            "zone": EvidenceClass.DETERMINISTIC_EXTRACTED,
        }.get(source, EvidenceClass.DETERMINISTIC_EXTRACTED)
        cycle_id = self._reconciler_cycle_id(state if state is not None else ctx.state)
        locality = zone_detail or zone_group
        if claims is None:
            claims = ([ClaimEvidence(claim_type=claim_type, value=locality,
                                     evidence_class=evidence_class, producer=f"ce:{source}",
                                     explicitness=Explicitness.STATED,
                                     cycle_id=cycle_id).with_id()]
                      if locality else [])
        else:
            claims = list(claims)

        def _validate(value: str):
            zone = self._extract_zone_from_text(value)
            if zone is not None:
                return zone
            # A group already validated upstream (Flow, form, canonical group) stands.
            if zone_group:
                return SimpleNamespace(zone_group=zone_group, zone_detail=zone_detail)
            return None

        decision = reconcile_inspection_location(claims, zone_validator=_validate)
        self._record_reconciliation(
            ctx, decision, claim_type, state, claims,
            decision_site_id=DecisionSite.LOCATION_INSPECTION_APPLY)
        if not decision.accepted:
            return False
        target.zone_group = decision.value.zone_group or zone_group
        target.zone_detail = decision.value.zone_detail or zone_detail
        return True

    # ── L4.7C.3B — the single acceptance / commercial-progression gate ────────
    #
    # With the flag OFF the legacy behaviour is returned untouched. With it ON, no language
    # match may advance commercial state on its own: the deterministic predicate proven in
    # C3A decides, and its justification is recorded.

    def _acceptance_authority_on(self) -> bool:
        return getattr(self.settings, "reconciler_acceptance_authority_enabled",
                       False) is True

    def _delivered_quote_amounts(self, ctx: "_Context") -> tuple[int, ...]:
        """Amounts actually presented to the customer in this cycle's outbound messages.

        A computed price is not a delivered quote. `ctx.db_messages` is cycle-scoped, so
        this is the outbound-ledger proof the authorization predicate requires.
        """
        amounts: list[int] = []
        try:
            for message in (ctx.db_messages or []):
                if getattr(message, "direction", None) != "out" or not getattr(message, "text", None):
                    continue
                for raw in _PRICE_RE.findall(message.text):
                    value = int(re.sub(r"\D", "", raw) or 0)
                    if value and value not in amounts:
                        amounts.append(value)
        except Exception:
            return ()
        return tuple(amounts)

    def _commercial_state(self, ctx: "_Context", state) -> "CommercialState":
        """Snapshot the deterministic facts acceptance depends on. Read-only."""
        from .acceptance_authorizer import CommercialState

        focus = self._focus_candidate(ctx)
        zone_group, zone_detail = self._get_active_inspection_location(ctx, state)
        quote = getattr(self, "_turn_price_quote", None)
        if quote is None and state is not None:
            try:
                quote = self._compute_price_quote(ctx, state)
            except Exception:
                quote = None
        cycle_id = self._reconciler_cycle_id(state)
        return CommercialState(
            cycle_id=cycle_id,
            revision_id=getattr(state, "current_revision_id", None),
            candidate_id=(focus.id if focus is not None else None),
            quote_total=(quote.precio_total if quote is not None else None),
            quote_tipo_vehiculo=(focus.tipo_vehiculo if focus is not None else None),
            quote_zone_group=zone_group, quote_zone_detail=zone_detail,
            quote_candidate_id=(focus.id if focus is not None else None),
            quote_cycle_id=cycle_id,
            current_tipo_vehiculo=(focus.tipo_vehiculo if focus is not None else None),
            current_zone_group=zone_group, current_zone_detail=zone_detail,
            delivered_amounts=self._delivered_quote_amounts(ctx),
            quote_delivered=False,          # proof comes from the ledger, not from a flag
            lead_flag=getattr(ctx.lead, "flag", None),
            stage=getattr(state, "last_stage", None))

    def _authorize_acceptance(self, ctx: "_Context", state, texts: list[str]):
        """Decide whether this turn may advance commercially. Returns a decision or None.

        L4.7W5-F7B. This used to assemble a QUOTE_ACCEPTED claim itself — its own
        `_is_acceptance` predicate stamped with its own `turn_modality` reading — beside
        the claim the projection produced with the F6-scoped `acceptance_modality`. Two
        producers of one HIGH-risk business claim, either able to make it actionable.

        It now gathers nothing and classifies nothing. `claim_projection.acceptance_claims`
        is the single canonical producer; this method only hands its result to the
        authorizer, which remains the only thing that decides.
        """
        from .claim_projection import acceptance_claims
        from .acceptance_authorizer import authorize_quote_acceptance

        evidence = self._semantic_turn_evidence()
        revision_id = getattr(state, "current_revision_id", None)
        claims = acceptance_claims(
            list(texts or ()), evidence,
            cycle_id=self._reconciler_cycle_id(state),
            revision_id=(revision_id if isinstance(revision_id, int) else None),
            has_scheduling_evidence=self._turn_has_scheduling_evidence(texts, evidence))

        decision = authorize_quote_acceptance(claims, self._commercial_state(ctx, state))
        self._record_authorization(ctx, decision, state)
        # ── G3-1: observational. The authorizer decided; this records what it saw. ──
        # No authority verdict is passed: `authorize_quote_acceptance` folds the claims
        # into a stance, it does not compare the producers against each other. Inventing a
        # verdict here would be the trace deciding, and P4's HOLD-and-clarify conflict
        # policy is explicitly NOT implemented in this milestone.
        from ..schemas.claims import ClaimType
        from ..schemas.hybrid_trace import CanonicalEffect
        self._trace_decision(
            decision_site_id=DecisionSite.QUOTE_ACCEPTANCE_AUTHORIZATION,
            claim_type=ClaimType.QUOTE_ACCEPTED, claims=claims,
            outcome=decision.result, reason=decision.reason,
            rule_id=decision.rule_id, rule_version=decision.rule_version,
            information_state=decision.stance_state,
            evidence_ids=decision.evidence_ids,
            authority_result=decision.result,
            canonical_effect=CanonicalEffect.NONE,
            permitted_action=("advance_commercial_state" if decision.allows else None),
            business_outcome=("acceptance authorized" if decision.allows
                              else f"acceptance not authorized ({decision.result})"))
        return decision

    def _turn_has_scheduling_evidence(self, texts, evidence) -> bool:
        """Does this burst ask about scheduling at all? Deterministic when it must be.

        L4.7W5-F7B. This is what EXPLAINS the future-tense words in an accepting burst as
        belonging to a different sentence, so the acceptance stays present and factual.
        Reading it only from the semantic interpreter would have rebuilt the original
        defect one level down: with no model evidence the answer was always False, the
        deterministic floor could not fire on "si" + "para cuando tenes", and the turn
        deadlocked exactly as before — a fix that works only while the model answers is
        not a fix. The deterministic readers below are the ones F6 already certified.
        """
        if getattr(evidence, "scheduling_requests", ()) or ():
            return True
        texts = [t for t in (texts or ()) if isinstance(t, str)]
        if not texts:
            return False
        try:
            if self._wants_next_available(texts):
                return True
        except Exception:
            pass
        try:
            day_iso, time_str = _parse_scheduling_text(texts, _business_today())
            return bool(day_iso or time_str)
        except Exception:
            return False

    def _authorize_scheduling_progression(self, ctx: "_Context", state):
        from .acceptance_authorizer import authorize_scheduling_progression
        decision = authorize_scheduling_progression(self._commercial_state(ctx, state))
        self._record_authorization(ctx, decision, state)
        return decision

    def _record_authorization(self, ctx: "_Context", decision, state) -> None:
        """Append the authorization justification. Decisions and ids only, never text."""
        import json as _json
        import pathlib as _pathlib
        from datetime import datetime as _dt, timezone as _tz
        try:
            logger.info(
                "L4.7C.3B AUTHORIZE thread_id=%s result=%s rule=%s@%s stance=%s quote=%s "
                "satisfied=%s failed=%s blockers=%s reason=%s",
                getattr(ctx.thread, "id", None), decision.result, decision.rule_id,
                decision.rule_version, decision.stance, decision.quote_identity,
                list(decision.satisfied), list(decision.failed), list(decision.blockers),
                decision.reason)
            raw_path = getattr(self.settings, "shadow_evidence_path", "")
            if not isinstance(raw_path, str) or not raw_path:
                return
            target = _pathlib.Path(raw_path).with_name("authorization_records.jsonl")
            payload = {
                "record_version": "authorization-record/1.0",
                "recorded_at": _dt.now(_tz.utc).isoformat(timespec="milliseconds"),
                "thread_id": getattr(ctx.thread, "id", None),
                "result": decision.result, "reason": decision.reason,
                "rule_id": decision.rule_id, "rule_version": decision.rule_version,
                "risk_tier": decision.risk_tier, "stance": decision.stance,
                "quote_identity": decision.quote_identity,
                "satisfied": list(decision.satisfied), "failed": list(decision.failed),
                "blockers": list(decision.blockers),
                "evidence_ids": list(decision.evidence_ids),
                "cycle_id": self._reconciler_cycle_id(state),
                "revision_id": getattr(state, "current_revision_id", None),
                "candidate_id": getattr(self._focus_candidate(ctx), "id", None),
                "stage": getattr(state, "last_stage", None),
                "authority": self._acceptance_authority_on(),
            }
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as handle:
                handle.write(_json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ── L4.7C.4 — the single scheduling interpretation path ───────────────────
    #
    # One place decides what the customer ASKED FOR. Deterministic extraction remains the
    # evidence producer; the reconciler owns order, clause-local time, flexibility and — the
    # part no model may touch — the calendar arithmetic. Availability is still
    # ScheduleService's word and booking is still the Flow's; neither is reachable from here.

    def _scheduling_authority_on(self) -> bool:
        return getattr(self.settings, "reconciler_scheduling_authority_enabled",
                       False) is True

    def _scheduling_claims_from_texts(self, texts: list[str], state, today: "date"):
        """Project the deterministic extraction into scheduling claims.

        The semantic interpreter produces the same shape, but it runs asynchronously off the
        customer turn (L4.7B.2) and is therefore not available synchronously here. When it
        becomes available it feeds this same interface as a SEMANTIC_INFERRED claim and the
        reconciler treats it identically — the ordering, clause-locality and resolution rules
        live in one place either way.
        """
        from ..schemas.claims import ClaimEvidence, ClaimType, EvidenceClass, Explicitness

        legacy = _parse_scheduling_requests(texts, today)
        if not legacy:
            return []
        period = _detect_time_period(texts)
        branches = []
        for index, request in enumerate(legacy):
            branches.append({
                "priority": ("PRIMARY" if index == 0
                             else "FALLBACK" if index == 1 else "ADDITIONAL"),
                "rank": index + 1,
                "resolved_date": request.day_iso,
                "day_expression": None,
                "time": request.time_str,
                "flexible": request.time_str is None,
                "time_band": (period if request.time_str is None else None),
            })
        return [ClaimEvidence(
            claim_type=ClaimType.SCHEDULING_PREFERENCE, value=tuple(branches),
            evidence_class=EvidenceClass.DETERMINISTIC_EXTRACTED,
            producer="ce:_parse_scheduling_requests", explicitness=Explicitness.STATED,
            cycle_id=self._reconciler_cycle_id(state)).with_id()]

    def _reconciled_scheduling_requests(self, ctx: "_Context", state, texts: list[str],
                                        today: "date") -> list:
        """Canonical requested branches for this turn, in the customer's own order.

        Flag OFF: the legacy parse, unchanged. Flag ON: the same evidence, reconciled once,
        with dates resolved by the single deterministic resolver.
        """
        if not self._scheduling_authority_on():
            return _parse_scheduling_requests(texts, today)

        from .scheduling_reconciler import reconcile_scheduling, to_record

        # Both producers, one reconciliation. The deterministic parse stays evidence (and a
        # validator); the semantic reading of the SAME burst joins it, and the reconciler —
        # not the caller, and not recency — decides which one survives a disagreement.
        claims = (self._scheduling_claims_from_texts(texts, state, today)
                  + self._semantic_scheduling_claims(state))
        decision = reconcile_scheduling(claims, today=today)
        try:
            record = to_record(decision,
                               cycle_id=self._reconciler_cycle_id(state),
                               revision_id=getattr(state, "current_revision_id", None))
            provider = getattr(self, "_turn_semantic", None)
            logger.info("L4.7C.4 SCHEDULING thread_id=%s source=%s branches=%s rule=%s@%s "
                        "semantic_calls=%s semantic_timeout=%s",
                        getattr(ctx.thread, "id", None), decision.source,
                        record["branches"], decision.rule_id, decision.rule_version,
                        (provider.calls if provider is not None else 0),
                        (provider.timed_out if provider is not None else False))
        except Exception:
            pass
        requests = [SchedulingRequest(day_iso=b.resolved_date, time_str=b.time)
                    for b in decision.branches if b.resolved_date]
        # ── G3-1: record the decision that was just made. Observational only. ──
        # `decision.source` is the reconciler's own word. `deterministic_conflict` means
        # `semantic_covers_deterministic` PROVED the two readings incompatible on resolved
        # dates and times — a resolver's finding, not the trace guessing — so it is passed
        # as the authority verdict. Any other source compared nothing incompatible.
        from ..schemas.claims import ClaimType
        from ..schemas.hybrid_trace import CanonicalEffect, ComparisonVerdict
        from .hybrid_trace import contributions_from_claims, has_cross_producer_comparison
        # G3-1-R2. `len(claims) > 1` counted claims, so five readings from one parser looked
        # like a comparison, and `source == "deterministic"` — which means no semantic claim
        # existed and NOTHING was compared — reported COMPATIBLE. The gate now requires what
        # a cross-producer statement actually requires: two distinct producers that both
        # addressed the same canonical proposition.
        #
        # `deterministic_conflict` and `semantic` are the only two outcomes `reconcile_
        # scheduling` reaches by running `semantic_covers_deterministic`; every other
        # source means it never compared anything, and supplies no verdict.
        _verdict = None
        try:
            _compared = has_cross_producer_comparison(contributions_from_claims(claims))
        except Exception:
            _compared = False
        if _compared:
            if decision.source == "deterministic_conflict":
                _verdict = ComparisonVerdict.INCOMPATIBLE
            elif decision.source == "semantic":
                _verdict = ComparisonVerdict.COMPATIBLE
        self._trace_decision(
            decision_site_id=DecisionSite.SCHEDULING_REQUEST_RECONCILIATION,
            claim_type=ClaimType.SCHEDULING_PREFERENCE, claims=claims,
            outcome=decision.source, reason=decision.reason,
            rule_id=decision.rule_id, rule_version=decision.rule_version,
            information_state=decision.information_state,
            evidence_ids=decision.evidence_ids,
            authority_result=decision.source, domain_verdict=_verdict,
            canonical_effect=CanonicalEffect.NONE,
            permitted_action=("check_availability" if requests else None),
            business_outcome=(f"{len(requests)} scheduling request(s) resolved"
                              if requests else "no resolvable scheduling request"))
        # L4.7W3-F1: record that this turn asked about availability, so the FAQ layer can
        # tell "what do you have tomorrow?" from "what are your opening hours?".
        if requests:
            self._turn_scheduling_requested = True
        return requests

    # ── L4.7D — canonical response validation ────────────────────────────────

    def _canonical_zone_names(self) -> tuple[str, ...]:
        """All zone names known to the DB, cached for the life of the engine instance."""
        cached = getattr(self, "_zone_names_cache", None)
        if cached is not None:
            return cached
        names: list[str] = []
        try:
            from sqlalchemy import select as _select
            for z in self.db.execute(_select(ViaticosZone)).scalars().all():
                for value in (z.zone_detail, z.zone_group):
                    if value and value not in names:
                        names.append(value)
        except Exception:
            names = []
        self._zone_names_cache = tuple(names)
        return self._zone_names_cache

    def _build_canonical_facts(
        self, ctx: "_Context", state: "WhatsAppThreadState | None"
    ) -> "CanonicalFacts":
        """Snapshot of everything the outbound text is allowed to assert as fact."""
        focus = self._focus_candidate(ctx)
        zone_group, zone_detail = self._get_active_inspection_location(ctx, state)

        quote = getattr(self, "_turn_price_quote", None)
        if quote is None and state is not None:
            try:
                quote = self._compute_price_quote(ctx, state)
            except Exception:
                quote = None

        offered: tuple[str, ...] = ()
        availability_checked = bool(getattr(self, "_availability_checked", False))
        if state is not None:
            for source in (state.last_visible_slots, state.last_offered_slots):
                if source:
                    try:
                        parsed = json.loads(source)
                    except (json.JSONDecodeError, TypeError):
                        parsed = []
                    if isinstance(parsed, list) and parsed:
                        offered = tuple(str(x) for x in parsed)
                        availability_checked = True
                        break

        booking_confirmed = bool(
            state is not None
            and (
                getattr(state, "current_revision_id", None) is not None
                or state.last_stage == STAGE_BOOKED
            )
        )
        acceptance_confirmed = bool(
            (ctx.lead is not None and getattr(ctx.lead, "flag", None) == "ACEPTADO")
            or (state is not None and state.last_stage in (
                STAGE_SCHEDULING, STAGE_FLOW_SENT, STAGE_BOOKED))
        )

        origin_zones: tuple[str, ...] = ()
        turn_text = getattr(self, "_turn_text", None)
        if turn_text and _has_customer_origin_clause(turn_text):
            origin_only = ""
            try:
                stripped = _strip_customer_origin_clauses(turn_text)
                origin_only = turn_text.replace(stripped, " ") if stripped else turn_text
                zone = self._extract_zone_from_text(origin_only)
                if zone is not None and zone.zone_detail:
                    origin_zones = (zone.zone_detail,)
            except Exception:
                origin_zones = ()

        prior_amounts: list[int] = []
        try:
            for m in (ctx.db_messages or []):
                if getattr(m, "direction", None) != "out" or not getattr(m, "text", None):
                    continue
                for raw in _PRICE_RE.findall(m.text):
                    value = int(re.sub(r"\D", "", raw) or 0)
                    if value and value not in prior_amounts:
                        prior_amounts.append(value)
        except Exception:
            prior_amounts = []

        return CanonicalFacts(
            vehicle_marca=(focus.marca if focus else None),
            vehicle_modelo=(focus.modelo if focus else None),
            inspection_zone_detail=zone_detail,
            inspection_zone_group=zone_group,
            customer_origin_zones=origin_zones,
            known_zone_names=self._canonical_zone_names(),
            quote_total=(quote.precio_total if quote is not None else None),
            quote_base=(quote.precio_base if quote is not None else None),
            quote_viaticos=(quote.viaticos if quote is not None else None),
            previously_quoted=tuple(prior_amounts),
            availability_checked=availability_checked,
            offered_slots=offered,
            booking_confirmed=booking_confirmed,
            acceptance_confirmed=acceptance_confirmed,
        )

    def _reply_vehicle_resolver(self, fragment: str):
        """Deterministic vehicle resolution used to read claims out of composed text."""
        hit = lookup_vehicle(fragment)
        if hit is not None:
            return hit
        mdy = extract_model_del_year(fragment)
        if mdy is not None:
            return mdy[0]
        return _contextual_numeric_model_lookup(fragment)

    def _validate_outbound_text(
        self, text: str, ctx: "_Context", state: "WhatsAppThreadState | None"
    ) -> str:
        """COMPOSE → **VALIDATE** → OutboundSafetyGate → SEND.

        A composed reply may assert only what canonical state proves.  Unsupported
        sentences are rewritten or dropped; questions, FAQ answers and the required next
        question always survive.  Never raises: a validator failure must not break a turn.
        """
        try:
            facts = self._build_canonical_facts(ctx, state)
            result = validate_response(
                text, facts, vehicle_resolver=self._reply_vehicle_resolver
            )
            for finding in result.findings:
                logger.info(
                    "CE_RESPONSE_VALIDATION thread_id=%s claim=%s allowed=%s proof=%r "
                    "action=%s detail=%r",
                    getattr(ctx.thread, "id", None), finding.claim, finding.allowed,
                    finding.proof, finding.action, finding.detail,
                )
            return result.text
        except Exception as exc:
            logger.warning(
                "L4.7D response validation error thread_id=%s: %s",
                getattr(ctx.thread, "id", None), exc,
            )
            return text

    # ── L4.6 Phase E — buffered inspection location ──────────────────────────

    def _attach_buffered_location(self, ctx: "_Context", state: "WhatsAppThreadState") -> None:
        """Attach a location the customer already gave in THIS cycle to a new candidate.

        The customer must never have to repeat an inspection location just because the
        vehicle was resolved afterwards (finding LOC-B).  This is cycle-safe and does NOT
        reopen L1 RISK-03: `_execute_cycle_reset()` clears `home_zone_*` at every cycle
        boundary, so a surviving buffer is by construction current-cycle evidence.
        """
        if not ctx.candidates:
            return
        candidate = ctx.candidates[0]
        if candidate.zone_group or candidate.zone_detail:
            return  # explicit per-candidate evidence always wins

        # ── W1F-W-01 R2: a TYPED evidence row, when there is one ─────────────────────
        # Review §8.2's whole purpose. The buffer below works and R1 proved it works — the
        # measured turn-2 quote is correct — but it is an untyped value: it cannot say which
        # cycle, which turn or which ROLE it came from, which is precisely why `RISK-03`
        # refuses to inherit it at candidate creation and why this helper has to lean on
        # `_execute_cycle_reset` clearing the buffer to be cycle-safe at all.
        #
        # An evidence row answers all three questions as columns, so the binding is checked
        # rather than assumed: same thread, same cycle, role INSPECTION_LOCATION, outcome
        # ACCEPT, not superseded, not already bound (`thread_evidence.bindable_locality`).
        # The VALUE still goes through `_apply_inspection_zone` — the C2 chokepoint and the
        # reconciler — so this is a better-provenanced source for the same certified write,
        # never a second writer. `zone_evidence_id` then records WHICH row set the location,
        # which is the §8.3 column.
        evidence_row = None
        if self._r2_on():
            try:
                from .thread_evidence import bind as _bind, bindable_locality
                evidence_row = bindable_locality(
                    self.db, thread_id=ctx.thread.id,
                    cycle_key=self._r2_cycle_key(state))
            except Exception as exc:
                logger.warning("W1F-R2 evidence lookup failed thread_id=%s: %s",
                               ctx.thread.id, type(exc).__name__)
                evidence_row = None

        if evidence_row is not None:
            group, detail = evidence_row.zone_pair()
            source = "evidence"
        else:
            group = getattr(state, "home_zone_group", None)
            detail = getattr(state, "home_zone_detail", None)
            source = "buffer"
        if not group and not detail:
            return
        self._apply_inspection_zone(ctx, candidate, zone_group=group,
                                    zone_detail=detail, source=source,
                                    state=getattr(ctx, "state", None))
        if evidence_row is not None:
            if _bind(self.db, evidence_row, candidate.id):
                candidate.zone_evidence_id = evidence_row.id
        try:
            self.db.flush()
        except Exception:
            pass
        self._decision_log(
            ctx, "location_buffer_attached",
            candidate_id=getattr(candidate, "id", None),
            zone_group=group, zone_detail=detail, source=source,
            evidence_id=getattr(evidence_row, "id", None),
        )

    # ── L4.6 Phase B/D — canonical response/state consistency ────────────────


    def _is_current_focus(self, state, candidate) -> bool:
        """Is this candidate the thread's ESTABLISHED current subject?

        Candidate-location isolation (M21.2.14): a "mentioned" candidate from a prior turn
        — a MOTO from a completed handoff, say — is not the current subject and must not
        inherit location evidence from a different vehicle's conversation. The predicate
        existed three times inside `_apply_zone_from_text` and is now read from one place,
        because W1F-W-01 R1 adds a fourth reader (the admission boundary) and W1FRV-07 is
        the finding about one rule living in two copies.
        """
        if candidate is None:
            return False
        if getattr(candidate, "status", None) == "current_focus":
            return True
        focus_id = getattr(state, "current_focus_candidate_id", None)
        return focus_id is not None and focus_id == getattr(candidate, "id", None)

    def _apply_zone_from_text(
        self,
        ctx: "_Context",
        state: "WhatsAppThreadState",
        text: str,
        allow_contradiction_return: bool = True,
    ) -> "tuple[ConversationHandleOut | None, bool]":
        """M21.1.3 / M21.2: Apply zone evidence from text to state and/or focus candidate.

        Shared helper used by both:
          - Normal current-turn zone handling (_process_text zone block).
          - Fuzzy-acceptance stored-turn replay (_apply_pending_turn_evidence).

        Preserves LR-2 customer-origin separation, LR-3 explicit vehicle-location
        authority, SC17 contradiction behavior, and bare-locality fallback semantics.

        Args:
            text: text to extract zone from.
            allow_contradiction_return: when True (normal path), an SC17 contradiction
                returns a ConversationHandleOut to the caller.  When False (replay of
                a stored turn), contradiction silently skips zone extraction instead of
                generating a new outbound message.

        Returns:
            (early_return, vehicle_location_written).
            If early_return is not None the caller must return it immediately.
        """
        # ── W1F-W-01 R3: nothing is written until coverage and ambiguity are decided ──
        # This is the whole of R3 scope item 2. The inherited order evaluated coverage in
        # `_routing_gate`, AFTER this helper had already written the zone; an unsupported
        # locality therefore persisted and survived the human handoff.
        if self._locality_safety_on():
            _v, _, _why = self._locality_verdict(text)
            if _v == _loc.LocalityVerdict.UNSUPPORTED:
                self._decision_log(ctx, "location_refused", verdict=_v, reason=_why,
                                   target="none")
                if allow_contradiction_return:
                    return self._send_coverage_response(ctx, state), False
                return None, False
            if _v == _loc.LocalityVerdict.AMBIGUOUS:
                # A real name with more than one real place: ask, never assume. No zone is
                # written — and the ambiguity is RECORDED, so the one clarification decision
                # later in the turn asks for the location because the locality was
                # ambiguous, not merely because nothing happened to be stored.
                #
                # OWNER-REVIEW-R1 R1RV-02. Relying on absence was measured and was not
                # enough: on a turn that already had a zone ("perdón, el auto está en San
                # Nicolás" at QUOTED) `location_resolved` was True, the route was
                # DOMAIN_NOT_RELEVANT, and the customer was re-quoted the OLD locality as
                # if they had said nothing. This is evidence contributing to a decision —
                # it grants permission to ASK and writes nothing.
                self._turn_locality_ambiguity = _why
                self._decision_log(ctx, "location_ambiguous", verdict=_v, reason=_why,
                                   target="none")
                return None, False

        _vehicle_location_written = False
        _vlzones = self._extract_vehicle_location_zones(text)
        if len(_vlzones) >= 2 and _LOCATION_UNCERTAINTY_RE.search(text):
            if allow_contradiction_return:
                return self._handle_location_contradiction(ctx, state), False
            # Contradiction in stored TURN 1 text — skip zone extraction silently.
            return None, False
        if _vlzones:
            # Explicit vehicle-location evidence → candidate (LR-3) or pre-candidate fallback.
            _vzone = _vlzones[0]
            _fc = self._focus_candidate(ctx)
            # Candidate-location isolation (M21.2.14): only write directly to a candidate
            # that is the established current focus.  A "mentioned" candidate from a prior
            # turn (e.g. a MOTO from a completed handoff) is NOT the current subject and
            # must not inherit location evidence from a different vehicle's conversation.
            _fc_is_current = self._is_current_focus(state, _fc)
            if _fc_is_current:
                self._apply_inspection_zone(ctx, _fc, zone_group=_vzone.zone_group,
                                            zone_detail=_vzone.zone_detail,
                                            source="zone", state=state)
            else:
                # No candidate yet, or only an unrelated mentioned candidate — buffer in
                # thread-level fallback so the intended candidate inherits it on creation.
                # Only vehicle-location evidence (not customer-origin) reaches this branch
                # (LR-2/LR-3 preserved).
                state.home_zone_group = _vzone.zone_group
                state.home_zone_detail = _vzone.zone_detail
            _vehicle_location_written = True
            self._decision_log(
                ctx, "location_extracted", source="vehicle_location_clause",
                zone_group=_vzone.zone_group, zone_detail=_vzone.zone_detail,
                target=("candidate" if _fc_is_current else "state_buffer"),
            )
        else:
            # L4.6 Phase C: a customer-origin clause suppresses ONLY itself.
            # Wild B's "Está en Berazategui, pero yo soy de Tigre." produced no
            # vehicle-location zone (no explicit subject) and the origin clause then
            # blocked the bare-locality fallback, dropping Berazategui entirely
            # (finding LOC-A).  Strip the origin clauses and re-read the remainder.
            _location_text = text
            if _has_customer_origin_clause(text):
                _location_text = _strip_customer_origin_clauses(text)
                if _location_text and _location_text != text:
                    _vlzones_stripped = self._extract_vehicle_location_zones(_location_text)
                    if _vlzones_stripped:
                        _vz2 = _vlzones_stripped[0]
                        _fc3 = self._focus_candidate(ctx)
                        _fc3_is_current = self._is_current_focus(state, _fc3)
                        if _fc3_is_current:
                            self._apply_inspection_zone(ctx, _fc3,
                                                        zone_group=_vz2.zone_group,
                                                        zone_detail=_vz2.zone_detail,
                                                        source="zone", state=state)
                        else:
                            state.home_zone_group = _vz2.zone_group
                            state.home_zone_detail = _vz2.zone_detail
                        self._decision_log(
                            ctx, "location_extracted", source="origin_clause_stripped",
                            zone_group=_vz2.zone_group, zone_detail=_vz2.zone_detail,
                            target=("candidate" if _fc3_is_current else "state_buffer"),
                        )
                        return None, True
            # No vehicle clause and no usable origin-free clause → bare locality.
            # Preserve existing detection for thread state (location flow compatibility),
            # and write to candidate if it lacks zone (SC14: bare locality in context).
            if (not _has_customer_origin_clause(_location_text)) and (
                not state.home_zone_detail or not state.home_zone_group
            ):
                _zh = self._extract_zone_from_text(_location_text)
                if _zh:
                    state.home_zone_detail = _zh.zone_detail
                    if _zh.zone_group:
                        state.home_zone_group = _zh.zone_group
                    _fc2 = self._focus_candidate(ctx)
                    # Same isolation: only write to an established current-focus candidate.
                    _fc2_is_current = self._is_current_focus(state, _fc2)
                    if _fc2_is_current and not _fc2.zone_group:
                        self._apply_inspection_zone(ctx, _fc2, zone_group=_zh.zone_group,
                                                    zone_detail=_zh.zone_detail,
                                                    source="zone", state=state)
                    self._decision_log(
                        ctx, "location_extracted", source="bare_locality",
                        zone_group=_zh.zone_group, zone_detail=_zh.zone_detail,
                        target=("candidate" if _fc2_is_current else "state_buffer"),
                    )
        return None, _vehicle_location_written

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

    # ── L4.7W1-F2 — fuzzy is evidence, and reconciliation decides ────────────

    def _fuzzy_claim(self, state, hit):
        """A fuzzy hit expressed as claims in the weakest evidence class there is."""
        from ..schemas.claims import (ClaimEvidence, ClaimType, EvidenceClass,
                                      Explicitness)
        cycle_id = self._reconciler_cycle_id(state)
        claims = []
        for claim_type, value in ((ClaimType.VEHICLE_MAKE, getattr(hit, "marca", None)),
                                  (ClaimType.VEHICLE_MODEL, getattr(hit, "modelo", None))):
            if value in (None, "", []):
                continue
            claims.append(ClaimEvidence(
                claim_type=claim_type, value=value,
                evidence_class=EvidenceClass.FUZZY_SUGGESTED,
                producer="ce:fuzzy_lookup_vehicle", explicitness=Explicitness.IMPLIED,
                cycle_id=cycle_id).with_id())
        return claims

    def _fuzzy_identity_accepted(self, ctx, state, result) -> bool:
        """Would reconciliation accept this fuzzy identity as a canonical vehicle?

        A fuzzy AUTO_ACCEPT used to write a candidate directly. It now has to survive the
        same rule every other vehicle claim does, so lexical similarity can never become
        canonical state on its own.
        """
        if getattr(result, "hit", None) is None:
            return False
        if not self._vehicle_authority_on():
            return True                  # flag off: legacy behaviour, unchanged
        from .field_reconciler import reconcile_vehicle_identity
        from .vehicle_catalog import lookup_vehicle
        from ..schemas.claims import ClaimType
        try:
            # `claims` is bound before the call because the trace argument added in
            # L4.7W5 Gate 1 named a variable that did not exist in this scope. Python
            # raised NameError at the observation point, the `except` below swallowed it,
            # and every fuzzy identity was rejected regardless of what the reconciler
            # decided. An observer changed a customer-facing outcome — see the closeout.
            claims = self._fuzzy_claim(state, result.hit)
            decision = reconcile_vehicle_identity(claims, catalog_lookup=lookup_vehicle)
            self._record_reconciliation(
                ctx, decision, ClaimType.VEHICLE_MODEL, state, claims,
                decision_site_id=DecisionSite.VEHICLE_FUZZY_ADMISSIBILITY)
            return bool(decision.accepted)
        except Exception as exc:         # a reconciliation failure is not an acceptance
            logger.warning("L4.7W1-F2 fuzzy reconciliation failed thread_id=%s: %s",
                           getattr(ctx.thread, "id", None), exc)
            return False

    def _ask_vehicle_clarification(self, ctx, state, result, current_turn_text: str):
        """The one place a vehicle clarification is composed. Returns None to fall through.

        Phase 6: an unresolved vehicle is never answered with a fabricated one. The
        catalog-supported question is asked only when the suggestion survives
        reconciliation *and* the catalog actually contains it; otherwise the customer
        gets a neutral "which make and model?" instead of a guess to react to.
        """
        from .vehicle_catalog import lookup_vehicle
        hit = getattr(result, "hit", None)
        supported = False
        if hit is not None and getattr(hit, "marca", None) and getattr(hit, "modelo", None):
            supported = lookup_vehicle(f"{hit.marca} {hit.modelo}") is not None
            if supported and self._vehicle_authority_on():
                supported = self._fuzzy_identity_accepted(ctx, state, result)
        question = (_FUZZY_CONFIRMATION_TEMPLATE.format(marca=hit.marca, modelo=hit.modelo)
                    if supported else _FUZZY_ASK_VEHICLE_REPLY)
        self._answer_source = "VEHICLE_RESOLVER"
        try:
            sent_id = self._send_text_to_wa(ctx, question)
        except OutboundBlockedError:
            self.db.commit()
            return _out("vehicle_fuzzy_blocked", detail="outbound_disabled")
        if supported:
            # The pending key is armed ONLY for a reconciled, catalog-supported identity,
            # so a later "Sí" can never canonicalise something reconciliation rejected.
            state.pending_fuzzy_catalog_key = f"{hit.marca}||{hit.modelo}"
            state.pending_turn_evidence_text = current_turn_text or None
        self.db.commit()
        self._decision_log(
            ctx, "vehicle_clarification_armed",
            source=("fuzzy_reconciled" if supported else "neutral_no_guess"),
            marca=(hit.marca if supported else None),
            modelo=(hit.modelo if supported else None),
            pending_key=getattr(state, "pending_fuzzy_catalog_key", None),
        )
        return _out("replied", wa_message_id=sent_id)

    # L4.7W1-F2: _handle_fuzzy_confirm was DELETED, not merely unwired. It was the
    # terminal path "fuzzy -> send -> return" that ended a turn before any
    # deterministic block or reconciliation could run. Vehicle clarification now has
    # exactly one composer, _ask_vehicle_clarification, reached after that work.

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
        self._answer_source = "DETERMINISTIC_RULE"

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
        self._answer_source = "DETERMINISTIC_RULE"
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

        # 0. Cross-turn affirmative: consume pending qualification answer (WILD-02-A).
        # If the prior turn sent the UNCERTAIN clarification, the next "Sí/dale/claro"
        # confirms prepurchase intent.  Set intent and fall through — do NOT return — so
        # the rest of this turn's evidence (vehicle, location) is still processed.
        if state.last_intent == _AWAITING_QUALIFICATION:
            _first = text_norm.split()[0].rstrip(",.!¡¿?") if text_norm.split() else ""
            if _first in {"si", "dale", "claro", "correcto", "ok", "bueno", "sip", "obvio", "yes"}:
                state.last_intent = _INTENT_PREPURCHASE
                # No db.commit() here — caller commits after full processing.
                # Fall through so Steps 1-8 process the remaining turn evidence.

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

        # 8.5. Deferred-interest signal → pass to AI+narrative so M21.1.6 can classify
        # and respond with DEFERRED_RESPONSE_ES. Steps 1–4 already handle messages that
        # carry an active vehicle or explicit inspection request; this step only fires
        # when none of those signals are present (pure soft-close / not-ready messages).
        if any(p.search(current_turn_text) for p in _DEFERRED_INTEREST_PATTERNS):
            return None

        # 8.9. W1F-W-01 R2 — THE CUSTOMER ANSWERED OUR OWN QUESTION.
        #
        # This is the Wild's turn 2 and the precondition the review's R2 plan specifies:
        # "Layer F step 9 gains one precondition: it may not send `_UNCERTAIN_SERVICE_REPLY`
        # while an outstanding question exists that this turn answered."
        #
        # The bot asked where the vehicle was; the customer replied `en flores`; step 9 read
        # the words, found no service keyword, and sent the generic qualification greeting —
        # three times in one conversation. The words were never the problem. The missing fact
        # was that we had asked something, and R2 supplies it.
        #
        # SCOPE, EXACTLY. This suppresses ONE reply: the contradictory greeting, on a turn
        # that answered a question we actually delivered. It is reached only after steps 1-8
        # have all declined, so it can take no decision away from them, and Layers A/B/C/D
        # and every coverage, consent, pricing, scheduling, handoff and booking authority sit
        # outside this function entirely and are untouched. It returns None — "continue" —
        # and deliberately does NOT set `last_intent`: an answer about WHERE the car is is
        # not a statement of commercial intent, and inventing one here is exactly the
        # silent-invention the milestone forbids. What the turn says next is still decided
        # downstream from canonical state, and it may ask for the vehicle it genuinely does
        # not know.
        if self._answered_question_kinds():
            logger.info("W1F-R2 step 9 suppressed thread_id=%s answered=%s", ctx.thread.id,
                        sorted(self._answered_question_kinds()))
            self._decision_log(ctx, "uncertain_greeting_suppressed",
                               answered=",".join(sorted(self._answered_question_kinds())))
            return None

        # 9. No inspection service signal found → UNCERTAIN clarification
        # Persist that the next turn is a vehicle-qualification answer (WILD-02-A).
        state.last_intent = _AWAITING_QUALIFICATION
        self.db.commit()
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
            # ── G3-5F §4.3: the boundary owns the call, the binding and the verdict ──
            decision, _admission = self._reply_decision(
                messages, role=ReplyRole.GENERAL_INFORMATION, ctx=ctx)
            if _admission in ReplyAdmission.REJECTED:
                self._turn_renderer_source = RendererSource.DETERMINISTIC_TEMPLATE
            else:
                # G3-5E (audit G5D-05): this route returns `answer_source="FAQ_RULE"`, but
                # the words came from the reply model. `answer_source` is legacy and
                # untouched; the renderer attribution here is the truthful one.
                self._turn_renderer_source = RendererSource.REPLY_MODEL
            self._turn_decision_authority = DecisionAuthority.DETERMINISTIC_RULE
        except Exception as exc:
            logger.warning(
                "M21.1.1 FAQ AI call failed thread_id=%s: %s", ctx.thread.id, exc
            )
            decision = {"reply": "Disculpá la demora, ya te ayudamos."}

        reply = decision.get("reply") or ""
        if reply:
            try:
                sent_id = self._send_text_to_wa(ctx, reply)
                return _out("replied", wa_message_id=sent_id, answer_source="FAQ_RULE")
            except OutboundBlockedError:
                self.db.commit()
                return _out("service_gate_blocked", detail="faq_kill_switch", answer_source="FAQ_RULE")
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
        self._answer_source = "DETERMINISTIC_RULE"
        lead = ctx.lead
        assert lead is not None

        lead.flag = "ACEPTADO"
        state.last_stage = STAGE_SCHEDULING
        # lead.estado stays CONSULTA_NUEVA
        # state.current_revision_id stays null
        # state.needs_human stays false

        # ── L4.7W5-F6: consume scheduling intent the customer already expressed ──────
        #
        # The live deadlock: "si" + "para cuando tenes" accepted the quote and asked when,
        # and the answer was "¿Qué día y horario te viene mejor?" — the customer's question
        # returned to them as a question. They repeated it and got the identical sentence.
        #
        # The scheduling capability was reachable only from `last_stage == SCHEDULING`, and
        # the stage advances on THIS line — so an intent stated in the same breath as the
        # acceptance always arrived one turn too early. Asking someone to repeat what they
        # just said is not a safety property; the intent is consumed here instead.
        #
        # ScheduleService still owns availability, and the handler below applies the same
        # progression authorization as any other scheduling turn. Nothing is bypassed —
        # only the round trip.
        texts = self._burst_texts_for_turn(ctx)
        if texts:
            try:
                consumed = self._consume_scheduling_intent(ctx, state, texts)
                if consumed is not None:
                    return consumed
            except OutboundBlockedError:
                raise
            except Exception as exc:                 # never block acceptance on this
                logger.warning("L4.7W5-F6 scheduling consumption failed thread_id=%s: %s",
                               getattr(ctx.thread, "id", None), exc)

        reply = "¡Perfecto! ¿Qué día y horario te viene mejor para la revisión?"
        sent_id = self._send_text_to_wa(ctx, reply)
        return _out("replied", wa_message_id=sent_id)

    def _consume_scheduling_intent(
        self, ctx: "_Context", state: WhatsAppThreadState, texts: list[str]
    ):
        """Act on a scheduling intent the customer stated alongside the acceptance.

        Routes from the intent, not from the stage. Covers every shape the customer may use
        in the same breath as "si": an exact day and time, a named day (including "hoy",
        which is core business), or a delegated / earliest request.

        Returns None when the burst carries no scheduling intent, so the ordinary acceptance
        reply still goes out. ScheduleService remains the availability authority and
        _dispatch_booking_flow_for_day still applies the progression authorizer — this only
        removes the round trip that made the customer repeat themselves.
        """
        # delegated / earliest first: it needs no date and answers "para cuando tenes"
        forward = self._handle_next_available_request(ctx, state, texts)
        if forward is not None:
            return forward

        day_iso, time_str = _parse_scheduling_text(texts, _business_today())
        if not day_iso:
            return None
        period = _detect_time_period(texts)
        if time_str:
            return self._try_schedule_and_flow(ctx, state, day_iso, time_str, "")
        return self._handle_day_only_request(ctx, state, day_iso, period=period, texts=texts)

    def _burst_texts_for_turn(self, ctx: "_Context") -> list[str]:
        """The customer's words for this turn, as the reconcilers already saw them."""
        for attr in ("_turn_burst_texts", "_turn_faq_burst"):
            value = getattr(self, attr, None)
            if isinstance(value, str) and value.strip():
                return [value]
            if isinstance(value, (list, tuple)) and value:
                return [t for t in value if isinstance(t, str)]
        return []

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
        self._answer_source = "DETERMINISTIC_RULE"
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
            return self._motorcycle_human_handoff(ctx, state, marca=marca, modelo=modelo)
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
            if not lead.ref_code:
                lead.ref_code = form_data["ref"]

        if form_data.get("rc_code"):
            logger.info(
                "M18 website_form rc_code=%r thread_id=%s", form_data["rc_code"], ctx.thread.id
            )
            if not lead.rc_code:
                lead.rc_code = form_data["rc_code"]

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
            self._answer_source = "PRICING_SERVICE"
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
        body_prefix: str = "",
    ) -> ConversationHandleOut | None:
        self._answer_source = "SCHEDULING_SERVICE"
        try:
            preferred_day = date.fromisoformat(preferred_day_iso)
        except (ValueError, TypeError):
            return None

        try:
            preferred_time_obj = time.fromisoformat(preferred_time_str) if preferred_time_str else time(9, 0)
        except (ValueError, TypeError):
            preferred_time_obj = time(9, 0)

        _sched_grp, _sched_det = self._get_active_inspection_location(ctx, state)
        zone_parts = [_sched_det, _sched_grp, "Buenos Aires, Argentina"]
        address = ", ".join(p for p in zone_parts if p)

        sched_in = ScheduleCheckIn(
            address=address,
            preferred_day=preferred_day,
            preferred_time=preferred_time_obj,
            zone_group=_sched_grp,
            zone_detail=_sched_det,
        )

        self._availability_checked = True   # L4.7D: ScheduleService consulted this turn
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
            # G3-7P: the OFFER is issued by whichever Flow actually gets dispatched —
            # the published Booking Flow below, or the legacy data-collection Flow in the
            # fallback branch. Minting here as well would issue two offers (and two
            # tokens) for one dispatch, immediately superseding the first.
            # last_stage stays SCHEDULING — the live offer signals the form was sent.
            # Stage only advances to BOOKED when the form response arrives.
            self.db.commit()

            # L4.3 Phase C — OWNER DECISION: the published RideCheck Booking Flow is
            # the authoritative booking/confirmation UX.  Text scheduling stays
            # responsible only for interpreting intent and reaching a valid slot;
            # once one exists, the structured Flow takes over.
            is_website = getattr(state, "is_website_lead", False)
            _booking_flow_setting = getattr(self.settings, "booking_flow_id", "")
            booking_flow_id = (
                _booking_flow_setting.strip() if isinstance(_booking_flow_setting, str) else ""
            )
            if not is_website and booking_flow_id:
                self._booking_flow_send_failed = False
                result = self._send_booking_flow(
                    ctx, state, booking_flow_id, body_prefix=body_prefix
                )
                if result is not None:
                    return result
                if getattr(self, "_booking_flow_send_failed", False):
                    # Transport failure on an armed Flow — never fall back to a second
                    # send in the same turn.
                    return None
                # Prerequisites not satisfied — fall through to the legacy Flow path
                # below (still a Flow; booking is never completed in chat).

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
            if body_prefix:
                body = f"{body_prefix} {body}"
            screen = "WEBSITE_FINAL_DATA" if is_website else "MAIN"
            # G3-7P: this legacy data-collection Flow's token IS accepted by
            # `_process_flow_response`, so it is a booking offer and must be issued through
            # the one authority — otherwise acceptance would find no record and fail
            # closed. The token comes from `make_booking_token` because
            # `whatsapp_booking_offers` enforces one offer per token and the old
            # `f"{thread}-{seconds}"` shape collides for two dispatches in the same second.
            from .offer_lifecycle import OfferLifecycle as _OfferLifecycle
            from .booking_flow_service import make_booking_token as _mk_token
            _legacy_offers = _OfferLifecycle(self.db)
            flow_token = _mk_token(ctx.thread.id)
            _lz_group, _lz_detail = self._get_active_inspection_location(ctx, state)
            _lz_focus = self._focus_candidate(ctx)
            _legacy_offers.issue(
                state, thread_id=ctx.thread.id, token=flow_token,
                cycle_key=self._clarification_cycle_key(state),
                candidate_id=getattr(_lz_focus, "id", None),
                tipo_vehiculo=(getattr(_lz_focus, "tipo_vehiculo", None)
                               if _lz_focus is not None else None),
                zone_group=_lz_group, zone_detail=_lz_detail,
                offered_date=state.preferred_day, offered_time=state.preferred_time,
                flow_id=flow_id)
            self.db.commit()
            try:
                sent_id = self._send_flow_button(
                    ctx, body, flow_token, flow_id=flow_id, initial_screen=screen
                )
                return _out("flow_button_sent", wa_message_id=sent_id)
            except Exception as exc:
                logger.error("M18 flow button send failed thread_id=%s: %s", ctx.thread.id, exc)
                _legacy_offers.withdraw(state, thread_id=ctx.thread.id,
                                        reason="DISPATCH_FAILED", clear_scheduling=False)
                # R2/A1: no live offer -> no truthy mirror. `withdraw()` cleared it and
                # nothing restores a dead token.
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
            # L4.3 Phase E: surface the REAL reason (outside operating hours, taken,
            # travel) instead of collapsing every rejection into "no disponibilidad".
            reason_phrase = self._rejection_reason_es(sched_out, preferred_day_iso)
            reason_suffix = f" ({reason_phrase})" if reason_phrase else ""
            if all_slots:
                # Show all available slots (capped at 12) in chronological order.
                shown = all_slots[:12]
                state.last_visible_slots = json.dumps(shown)
                slot_list = ", ".join(shown[:-1]) + f" o {shown[-1]}" if len(shown) >= 2 else shown[0]
                # ── L4.7W5-F4 FLOW-FIRST on a rejected exact time ─────────────
                # The live Wild asked for Monday 18:00, was told 18:00 was unavailable, and
                # got seven slots recited in prose — then had to type "17hs" so the Flow
                # could open and ask for a time again. Flow-first already covers day
                # requests and delegated requests; the rejected-exact-time branch was the
                # one path still dumping a list. The day and its real slots are already in
                # hand here, so the picker can open immediately.
                # ...but NOT when this burst also owes the customer a canonical FAQ answer.
                # The Flow body cannot carry one, and dropping "¿aceptan débito?" to save a
                # round trip on slots is a bad trade: an unanswered question is worse than a
                # list. Those turns keep the composed text reply, which carries both.
                if not self._turn_owes_faq_answer():
                    self.db.commit()
                    flow_out = self._dispatch_booking_flow_for_day(
                        ctx, state, day_iso=preferred_day_iso, date_human=date_human,
                        slots=shown,
                        period_label=None,
                    )
                    if flow_out is not None:
                        return flow_out
                msg = (
                    f"Para {date_human} a las {preferred_time_obj.strftime('%H:%M')} "
                    f"no tenemos disponibilidad{reason_suffix}. "
                    f"Horarios disponibles: {slot_list}. ¿Alguno te viene bien?"
                )
            else:
                state.last_visible_slots = None
                reasons = reason_phrase or (
                    "; ".join(sched_out.reasons[:2]) if sched_out.reasons else "sin disponibilidad"
                )
                msg = (
                    f"Para {date_human} no hay horarios disponibles "
                    f"({reasons}). ¿Tenés otro día preferido?"
                )
            if body_prefix:
                msg = f"{body_prefix} {msg}"
            sent_id = self._send_text_to_wa(ctx, msg)
            return _out("replied", wa_message_id=sent_id)

    # ── L4.3 Phase C — Booking Flow dispatch (authoritative booking UX) ──────


    def _restore_non_booking_mirror(self, state, previous_token) -> None:
        """After a failed booking dispatch, put back ONLY a non-booking mirror value.

        R2/A1. The invariant is: `flow_booking_token` may be truthy only when it mirrors
        the one current ISSUED booking offer for that thread. The old code restored
        `previous_token` unconditionally, and `issue()` had already SUPERSEDED the offer
        that token named — so a dispatch failure left a truthy mirror with NO live offer.
        The G3-7P review measured the consequence: `_offer_outstanding` True,
        `proposal_is_active` True, and the L4.7W5-F7A deterministic scheduling gate CLOSED,
        which is the very defect F7A exists to prevent. It could not book, but the customer
        could no longer be heard by the scheduler.

        A superseded booking token is therefore never restored. The MOTORCYCLE handoff
        token is a different family that legitimately shares this column — it is routed by
        prefix before the booking handler and is not a booking offer — so it, and only it,
        is put back.
        """
        if previous_token and str(previous_token).startswith(_MOTO_HANDOFF_TOKEN_PREFIX):
            state.flow_booking_token = previous_token

    def _send_booking_flow(
        self,
        ctx: _Context,
        state: WhatsAppThreadState,
        booking_flow_id: str,
        body_prefix: str = "",
    ) -> ConversationHandleOut | None:
        """Dispatch the published RideCheck Booking Flow for an established slot.

        Returns None when the Flow could not be armed, so the caller can fall back to
        the legacy data-collection Flow.  Booking itself is NEVER completed in chat:
        BookingFlowService owns slot revalidation and the atomic booking write.
        """
        from .booking_flow_service import BookingFlowService, make_booking_token
        from .outbound_path_registry import OutboundPathId

        # Prerequisites the published Flow needs before it can render a booking:
        # accepted quote (caller reached the valid-slot branch), an active candidate
        # and a resolvable inspection zone.
        zone_group, _zone_detail = self._get_active_inspection_location(ctx, state)
        has_candidate = bool(getattr(state, "current_focus_candidate_id", None)) or bool(ctx.candidates)
        if not zone_group or not has_candidate:
            logger.warning(
                "L4.3 booking flow not eligible thread_id=%s zone=%r candidate=%s",
                ctx.thread.id, zone_group, has_candidate,
            )
            return None

        # ── G3-7P: ISSUE through the one authority ────────────────────────────────
        # The offer row records what is being booked — candidate, pricing zone pair,
        # category, the slot the customer was shown — so acceptance can refuse when any of
        # them has since moved, instead of trusting that every handler remembered to
        # withdraw. `issue()` supersedes whatever it replaces, and the database enforces
        # that a thread never holds two live offers.
        from .offer_lifecycle import OfferLifecycle as _OfferLifecycle
        _offers = _OfferLifecycle(self.db)
        previous_token = state.flow_booking_token
        flow_token = make_booking_token(ctx.thread.id)  # minted exactly once per dispatch
        _focus_for_offer = self._focus_candidate(ctx)
        _offer_row = _offers.issue(
            state, thread_id=ctx.thread.id, token=flow_token,
            cycle_key=self._clarification_cycle_key(state),
            # The offer is bound to the candidate it is FOR — the resolved focus, which
            # has a documented single-candidate fallback — not to the raw pointer, so the
            # binding matches what acceptance resolves on the other side.
            candidate_id=getattr(_focus_for_offer, "id", None),
            tipo_vehiculo=(getattr(_focus_for_offer, "tipo_vehiculo", None)
                           if _focus_for_offer is not None else None),
            zone_group=zone_group, zone_detail=_zone_detail,
            offered_date=getattr(state, "preferred_day", None),
            offered_time=getattr(state, "preferred_time", None),
            flow_id=booking_flow_id)
        self.db.commit()

        # Reuse the Flow's own contract validation — never duplicated inside CE.
        try:
            BookingFlowService(self.db).resolve_context(flow_token)
        except Exception as exc:
            logger.error(
                "L4.3 booking flow prerequisites unsatisfied thread_id=%s: %s",
                ctx.thread.id, exc,
            )
            _offers.withdraw(state, thread_id=ctx.thread.id,
                              reason="DISPATCH_PREREQUISITES", clear_scheduling=False)
            self._restore_non_booking_mirror(state, previous_token)
            self.db.commit()
            return None

        body = (
            "Ese horario está disponible 🎉 "
            "Para confirmar el turno, elegí el horario y completá tus datos."
        )
        if body_prefix:
            body = f"{body_prefix} {body}"

        try:
            sent_id = self._send_flow_button(
                ctx, body, flow_token,
                flow_id=booking_flow_id,
                initial_screen="APPOINTMENT",
                path_id=OutboundPathId.BOOKING_FLOW.value,
                cta_label="Reservar turno",
                # The ONE endpoint-backed Flow: Meta must call handle_init, which returns
                # the APPOINTMENT screen with ScheduleService-approved slots. Sent as
                # NAVIGATE it is delivered and read but never renders (Wild W4).
                mode=FLOW_MODE_DATA_EXCHANGE,
            )
            return _out("flow_button_sent", wa_message_id=sent_id)
        except OutboundBlockedError:
            _offers.withdraw(state, thread_id=ctx.thread.id,
                              reason="DISPATCH_BLOCKED", clear_scheduling=False)
            self._restore_non_booking_mirror(state, previous_token)
            self.db.commit()
            raise
        except Exception as exc:
            logger.error("L4.3 booking flow send failed thread_id=%s: %s", ctx.thread.id, exc)
            _offers.withdraw(state, thread_id=ctx.thread.id,
                              reason="DISPATCH_FAILED", clear_scheduling=False)
            self._restore_non_booking_mirror(state, previous_token)
            self.db.commit()
            self._booking_flow_send_failed = True
            return None

    # ── L4.3 Phase E — rejection semantics ───────────────────────────────────

    def _rejection_reason_es(self, sched_out, day_iso: str) -> str | None:
        """Map ScheduleService rejection reasons to a customer-useful explanation."""
        from .schedule import format_business_hours_es
        blob = " ".join(str(r) for r in (getattr(sched_out, "reasons", None) or [])).lower()
        if not blob:
            return None
        if "domingo" in blob:
            return "los domingos no trabajamos"
        if "horario operativo" in blob:
            try:
                hours = format_business_hours_es(date.fromisoformat(day_iso))
            except (ValueError, TypeError):
                return "ese horario queda fuera de nuestro horario operativo"
            return f"ese día trabajamos {hours}"
        if "se superpone" in blob:
            return "ese horario ya está reservado"
        if "traslado" in blob:
            return "no llegamos a tiempo desde el turno anterior"
        return None

    # ── L4.3 Phase A — ordered evaluation: PRIMARY first, FALLBACK second ────

    def _evaluate_scheduling_branch(
        self,
        ctx: _Context,
        state: WhatsAppThreadState,
        request: "SchedulingRequest",
        with_slots: bool = True,
    ) -> dict:
        """Read-only availability evaluation of one scheduling branch."""
        from ..schemas.schedule import ScheduleCheckIn
        grp, det = self._get_active_inspection_location(ctx, state)
        address = ", ".join(p for p in [det, grp, "Buenos Aires, Argentina"] if p)
        try:
            time_obj = time.fromisoformat(request.time_str) if request.time_str else time(9, 0)
        except (ValueError, TypeError):
            time_obj = time(9, 0)
        sched_in = ScheduleCheckIn(
            address=address,
            preferred_day=date.fromisoformat(request.day_iso),
            preferred_time=time_obj,
            zone_group=grp,
            zone_detail=det,
            exclude_revision_id=state.current_revision_id,
        )
        result = {"valid": False, "slots": [], "reason": None, "time_obj": time_obj}
        self._availability_checked = True   # L4.7D: ScheduleService consulted this turn
        try:
            if request.time_str:
                checked = self._schedule.check(sched_in)
                result["valid"] = bool(checked.valid)
                if checked.valid:
                    return result  # short-circuit: no fallback work is done
                result["reason"] = self._rejection_reason_es(checked, request.day_iso)
            if with_slots:
                day_slots = self._schedule.list_slots(sched_in)
                result["slots"] = sorted(
                    s for s in (day_slots.slots or []) if isinstance(s, str)
                )
        except Exception as exc:
            logger.warning(
                "L4.3 availability evaluation failed thread_id=%s day=%s: %s",
                ctx.thread.id, request.day_iso, exc,
            )
        return result

    def _handle_ordered_scheduling_requests(
        self,
        ctx: _Context,
        state: WhatsAppThreadState,
        requests: list["SchedulingRequest"],
    ) -> ConversationHandleOut | None:
        """Evaluate an explicit PRIMARY preference before any FALLBACK preference.

        The customer must never have to infer that a day they explicitly asked for was
        silently discarded (L4-WILD-A findings SCHED-A / SCHED-B / SCHED-D).
        """
        self._answer_source = "SCHEDULING_SERVICE"
        primary, fallback = requests[0], requests[1]
        today = date.today()

        primary_eval = self._evaluate_scheduling_branch(ctx, state, primary)
        if primary_eval["valid"]:
            # PRIMARY satisfied — the fallback is never evaluated.
            logger.info(
                "L4.3 primary scheduling branch available thread_id=%s day=%s time=%s",
                ctx.thread.id, primary.day_iso, primary.time_str,
            )
            return self._try_schedule_and_flow(
                ctx, state, primary.day_iso, primary.time_str, ""
            )

        primary_human = _format_date_human(primary.day_iso, today)
        if primary.time_str:
            primary_msg = (
                f"Para {primary_human} a las {primary_eval['time_obj'].strftime('%H:%M')} "
                f"no tengo disponibilidad"
            )
        else:
            primary_msg = f"Para {primary_human} no tengo disponibilidad"
        if primary_eval["reason"]:
            primary_msg += f" ({primary_eval['reason']})"
        if not primary_eval["slots"]:
            primary_msg += " y no me queda ningún horario libre ese día"
        primary_msg += "."

        logger.info(
            "L4.3 primary rejected, evaluating fallback thread_id=%s primary=%s fallback=%s",
            ctx.thread.id, primary.day_iso, fallback.day_iso,
        )
        fallback_eval = self._evaluate_scheduling_branch(ctx, state, fallback)

        # FALLBACK named an exact time that IS available → the Flow can be sent now,
        # with the primary rejection explained in the same message.
        if fallback.time_str and fallback_eval["valid"]:
            return self._try_schedule_and_flow(
                ctx, state, fallback.day_iso, fallback.time_str, "", body_prefix=primary_msg
            )

        fallback_human = _format_date_human(fallback.day_iso, today)
        fallback_slots = fallback_eval["slots"]
        if fallback_slots:
            shown = fallback_slots[:12]
            if len(shown) >= 2:
                slot_list = ", ".join(shown[:-1]) + f" o {shown[-1]}"
                fallback_msg = f"Para {fallback_human} tengo: {slot_list}. ¿Alguno te viene bien?"
            else:
                slot_list = shown[0]
                fallback_msg = f"Para {fallback_human} tengo {slot_list}. ¿Te sirve?"
            state.active_requested_date = fallback.day_iso
            state.last_requested_time = fallback.time_str
            state.last_offered_slots = json.dumps(fallback_slots)
            state.last_visible_slots = json.dumps(shown)
        else:
            reason = fallback_eval["reason"]
            reason_suffix = f" ({reason})" if reason else ""
            fallback_msg = (
                f"Para {fallback_human} tampoco tengo horarios disponibles{reason_suffix}. "
                f"¿Tenés otro día preferido?"
            )
            state.active_requested_date = fallback.day_iso
            state.last_requested_time = fallback.time_str
            state.last_offered_slots = json.dumps([])
            state.last_visible_slots = None

        # A rejected request must not leave a bookable preference behind.
        state.preferred_day = None
        state.preferred_time = None

        sent_id = self._send_text_to_wa(ctx, f"{primary_msg} {fallback_msg}")
        return _out("replied", wa_message_id=sent_id)

    # ── Period-based scheduling: "por la tarde" ───────────────────────────

    def _handle_period_request(
        self,
        ctx: _Context,
        state: WhatsAppThreadState,
        period: str,
    ) -> ConversationHandleOut | None:
        """Filter last_offered_slots by time period and reply with available options."""
        self._answer_source = "SCHEDULING_SERVICE"
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
            # L4.7W3-F1: same owner decision as the day path — a bounded period with real
            # availability opens the Flow rather than reciting the times.
            _flow_out = self._dispatch_booking_flow_for_day(
                ctx, state, day_iso=str(state.active_requested_date),
                date_human=date_human, slots=shown, period_label=period_label)
            if _flow_out is not None:
                return _flow_out
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

    def _dispatch_booking_flow_for_day(self, ctx, state, *, day_iso: str, date_human: str,
                                       slots: list, period_label: str | None = None):
        """Open the Booking Flow as the slot picker. Returns None to fall back to text.

        The Flow is offered ONLY when ScheduleService returned at least one valid slot for
        the requested day/period: an empty Flow would be a dead end, and inventing a slot
        to fill it would be worse. Opening the Flow is not a booking — BookingFlowService
        still revalidates and owns the atomic write, and the booked revision is still
        produced by the Flow-response handler alone. Nothing here writes booking state.
        """
        if not slots:
            return None
        booking_flow_id = (getattr(self.settings, "booking_flow_id", "") or "").strip()
        if not booking_flow_id:
            return None
        # A booking picker is a commercial step, not a calendar widget. It opens only when
        # the same authorizer that governs every other scheduling progression says so —
        # a delivered quote, in this cycle, with unchanged inputs. Without this a bare
        # "¿qué tenés mañana?" before any quote would open a booking Flow.
        if self._acceptance_authority_on():
            progression = self._authorize_scheduling_progression(ctx, state)
            if progression is not None and not progression.allows:
                logger.info(
                    "L4.7W3-F1 FLOW-FIRST withheld thread_id=%s reason=%s",
                    getattr(ctx.thread, "id", None), getattr(progression, "reason", "?"))
                return None
        # Availability is ScheduleService's word, recorded as REQUESTED-day + AVAILABLE
        # slots. Neither is a booking, and neither is asserted as one to the customer.
        state.active_requested_date = day_iso
        state.last_visible_slots = json.dumps(list(slots))
        self.db.commit()
        window = f" por la {period_label}" if period_label else ""
        prefix = (f"Para {date_human}{window} tengo {len(slots)} "
                  f"{'horario disponible' if len(slots) == 1 else 'horarios disponibles'}. "
                  "Elegí el que te sirva y confirmá el turno acá:")
        try:
            out = self._send_booking_flow(ctx, state, booking_flow_id, body_prefix=prefix)
        except OutboundBlockedError:
            # R3/D: do NOT decide here whether the customer saw these slots. This function
            # has three callers and every one of them recites `slots` in a TEXT reply when
            # the Flow does not open — so a cleared field here would be the opposite lie on
            # the far more common path. Delivery is a property of the TURN, and the turn
            # decides it in `handle()`.
            raise
        except Exception as exc:
            logger.warning("L4.7W3-F1 booking flow dispatch failed thread_id=%s: %s",
                           getattr(ctx.thread, "id", None), exc)
            return None
        if out is not None:
            self._decision_log(ctx, "booking_flow_offered", source="flow_first_day_request",
                               day=day_iso, period=(period_label or "-"), slots=len(slots))
            logger.info(
                "L4.7W3-F1 FLOW-FIRST thread_id=%s day=%s period=%s slots=%d dispatched",
                getattr(ctx.thread, "id", None), day_iso, period_label or "-", len(slots))
        return out

    #: R3/D. The turn outcomes that mean NOTHING was displayed to the customer. Every one
    #: of them is a terminal verdict on the whole turn: the gate refused the send
    #: (`blocked_dispatch`), the turn crashed (`error`), or it never started because the
    #: other booking writer held the thread (`thread_contended`). Judging delivery by the
    #: ACTION — not by an internal send helper — is deliberate: those helpers are the seam
    #: the test suite replaces, so a marker set inside them would make every stubbed
    #: successful turn look undelivered and revoke a claim that was perfectly true.
    _NO_DELIVERY_ACTIONS = frozenset({"blocked_dispatch", "error", ACTION_THREAD_CONTENDED})

    def _revoke_undelivered_slot_claim(self, event, out) -> None:
        """Undo a slot-visibility claim written by a turn that delivered nothing.

        R3/D. `last_visible_slots` means "the exact slots displayed to the user" — that is
        how the acceptance machinery and `_offer_outstanding` read it, and
        `_offer_outstanding` exists precisely so that rejecting options nobody was shown is
        not treated as a rejection. The day-slots path COMMITS that field before it
        dispatches, so when the turn then delivers nothing — the kill switch refused the
        send, or the turn crashed before any transport succeeded — the committed field
        asserts a delivery that never happened. The R2 review measured the consequence:
        `_offer_outstanding` returned True for options the customer had never seen.

        Two things this deliberately does NOT do.

        It does not blank the field. It RESTORES the value the thread had before this turn,
        because a slot list from an earlier, successful turn is still true and erasing it
        would destroy real scheduling evidence to make one Boolean false.

        It does not touch `active_requested_date` or `last_offered_slots`. The first records
        the day the customer ASKED about, which they really did. The second is
        ScheduleService's own answer — the slots it approved for this candidate, location
        and cycle — and it carries NO delivery claim; M20.6D2's RC29 guarantee that no
        available slot is ever hidden is asserted on exactly that field. Discarding either
        would throw away true evidence to make one Boolean false, which is the mistake
        L4.7W5-F7A exists to prevent.

        Delivery is judged by the TURN'S OUTCOME first (`_NO_DELIVERY_ACTIONS`) and then by
        what the outbound gate actually allowed, so a turn that sent a text and crashed
        afterwards keeps the claim it earned.
        """
        if getattr(out, "action", None) not in self._NO_DELIVERY_ACTIONS:
            return                                  # the turn reached the customer
        if getattr(self, "_turn_text_dispatches", 0) or getattr(
                self, "_turn_flow_delivered", False):
            return               # it crashed, but not before something really was sent
        before = getattr(self, "_turn_slot_claim_before", None)
        if before is None:
            return                                  # this turn never claimed a slot list
        visible_before, _offered_before = before
        db = self.db
        try:
            from ..models import WhatsAppThreadState as _TS
            row = db.query(_TS).filter(_TS.thread_id == int(event.thread_id)).first()
            if row is None:
                return
            if row.last_visible_slots == visible_before:
                return                              # nothing was claimed after all
            row.last_visible_slots = visible_before
            db.commit()
        except Exception:                           # pragma: no cover - defensive
            logger.exception("R3 could not revoke an undelivered slot claim thread_id=%s",
                             getattr(event, "thread_id", None))
            try:
                db.rollback()
            except Exception:
                pass
            return
        logger.info(
            "G3-7P-R3 revoked an undelivered slot claim thread_id=%s — restored the "
            "pre-turn slot list; the requested day is kept",
            getattr(event, "thread_id", None))

    # ── Day-only request: user gives a day but no time ────────────────────

    # ── L4.7W5-F2 — delegated / earliest scheduling ──────────────────────────

    @staticmethod
    def _wants_next_available(texts: list[str]) -> bool:
        """The customer handed us the date choice, or asked for the earliest thing."""
        n = _norm_lower(" ".join(texts))
        return any(re.search(pat, n) for pat in _NEXT_AVAILABLE_PATTERNS)

    @staticmethod
    def _urgency_signalled(texts: list[str]) -> bool:
        n = _norm_lower(" ".join(texts))
        return any(re.search(pat, n) for pat in _URGENCY_PATTERNS)

    @staticmethod
    def _earliest_option_rejected(texts: list[str]) -> bool:
        """Customer says the option we produced does not solve their problem."""
        n = _norm_lower(" ".join(texts))
        return any(re.search(pat, n) for pat in _EARLIEST_REJECTED_PATTERNS)

    def _handle_next_available_request(
        self,
        ctx: _Context,
        state: WhatsAppThreadState,
        texts: list[str],
        *,
        allow_clarification: bool = False,
    ) -> ConversationHandleOut | None:
        """Search forward for the first day with capacity and open the Booking Flow.

        Returns None when this turn is not a delegated/earliest request, so the caller
        falls through to the existing named-day handling untouched.

        The customer never has to name a day. ScheduleService decides what exists; the
        model only told us the customer wanted the earliest option.
        """
        # G3-4: admission is a DISJUNCTION. The deterministic patterns still admit exactly
        # what they always did — not one has been added — and a resolved semantic
        # AVAILABILITY_ENQUIRY now admits the turn too, so CE silence is no longer a veto
        # (DEF-02). The decision is taken once per turn and traced at
        # `scheduling.availability.authorization`.
        from ..schemas.hybrid_trace import SchedulingAuthority
        decision = self._availability_authority(ctx, state, texts)
        if decision.authority == SchedulingAuthority.CLARIFY and allow_clarification:
            # R2 (audit R34-02): CLARIFY used to be produced and never consumed, so a
            # decision the trace called "clarify" reached the customer as unconstrained
            # prose — or, after the weak floor was suppressed, as nothing at all. It now has
            # a production consumer: one canonical question naming the two readings.
            # It claims no availability, states no hours, books nothing and escalates to
            # nobody; ScheduleService is not consulted and no canonical field is written.
            self._answer_source = "DETERMINISTIC_RULE"
            self._decision_log(ctx, "availability_clarification",
                               reason=decision.reason, floor=decision.floor,
                               semantic_status=decision.semantic_status)
            try:
                sent_id = self._send_text_to_wa(ctx, _AVAILABILITY_VS_HOURS_CLARIFICATION)
            except OutboundBlockedError:
                # The PLANNED action stays visible; only delivery was blocked.
                self.db.commit()
                return _out("blocked_dispatch", detail="availability_clarification",
                            answer_source="DETERMINISTIC_RULE")
            return _out("replied", wa_message_id=sent_id,
                        answer_source="DETERMINISTIC_RULE")
        if not decision.admitted:
            return None
        if decision.authority != SchedulingAuthority.LOOK_UP_AVAILABILITY:
            # REQUEST_PREREQUISITE / HOLD: the existing canonical path owns this turn. The
            # authority is recorded; nothing here acts on it.
            return None

        self._answer_source = "SCHEDULING_SERVICE"
        self._availability_checked = True
        grp, det = self._get_active_inspection_location(ctx, state)
        urgent = self._urgency_signalled(texts)

        # L4.7W5-F5 (owner business decision): same-day booking is allowed and is roughly
        # 60% of demand, so the search starts TODAY. ScheduleService decides what is left of
        # today — it seeds from now plus a lead buffer, so a slot that has passed or cannot
        # be reached is never returned. Today wins over any future day when it has capacity.
        start = date.today()
        found = self._schedule.find_next_available(
            zone_group=grp, zone_detail=det, start_day=start,
            exclude_revision_id=state.current_revision_id,
        )

        self._decision_log(
            ctx, "next_available_search",
            zone_group=grp or "-", zone_detail=det or "-",
            search_start=start.isoformat(), horizon=NEXT_AVAILABLE_HORIZON_DAYS,
            days_checked=(found.days_checked if found else NEXT_AVAILABLE_HORIZON_DAYS),
            first_valid=(found.day.isoformat() if found else "none"),
            slot_count=(len(found.slots) if found else 0),
            urgency=urgent,
        )

        if found is None:
            # Bounded search exhausted. Asking the customer to keep naming days would be
            # an open-ended negotiation against capacity that does not exist.
            logger.info(
                "L4.7W5-F2 NEXT_AVAILABLE exhausted thread_id=%s zone=%s/%s horizon=%s",
                ctx.thread.id, grp, det, NEXT_AVAILABLE_HORIZON_DAYS,
            )
            return self._handle_scheduling_escalation(ctx, state, " ".join(texts))

        state.active_requested_date = found.day.isoformat()
        state.last_offered_slots = json.dumps(list(found.slots))
        self.db.commit()

        date_human = _format_date_human(found.day.isoformat(), date.today())
        prefix = (f"El primer turno disponible es el {date_human}."
                  if not urgent else
                  f"Lo antes que podemos es el {date_human}.")
        flow_out = self._dispatch_booking_flow_for_day(
            ctx, state, day_iso=found.day.isoformat(),
            date_human=date_human, slots=list(found.slots))
        if flow_out is not None:
            return flow_out

        # Flow could not open — fall back to naming the slots rather than going silent.
        shown = ", ".join(found.slots[:5])
        msg = f"{prefix} Tengo estos horarios: {shown}. ¿Cuál te sirve?"
        sent_id = self._send_text_to_wa(ctx, msg)
        return _out("replied", wa_message_id=sent_id)

    def _handle_day_only_request(
        self,
        ctx: _Context,
        state: WhatsAppThreadState,
        day_iso: str,
        period: str | None = None,
        texts: list[str] | None = None,
    ) -> ConversationHandleOut:
        """User named a day (with optional time period) but no exact slot.
        Fetches full-day availability, stores sorted slots, and replies with options
        filtered by period ('tarde'/'manana') when specified."""
        self._answer_source = "SCHEDULING_SERVICE"
        from ..schemas.schedule import ScheduleCheckIn
        assert ctx.lead is not None
        _day_grp, _day_det = self._get_active_inspection_location(ctx, state)
        zone_parts = [_day_det, _day_grp, "Buenos Aires, Argentina"]
        address = ", ".join(p for p in zone_parts if p)
        sched_in = ScheduleCheckIn(
            preferred_day=date.fromisoformat(day_iso),
            preferred_time=time(9, 0),
            address=address,
            zone_group=_day_grp,
            zone_detail=_day_det,
            exclude_revision_id=state.current_revision_id,
            is_holiday=False,
        )
        is_closed = False
        self._availability_checked = True   # L4.7D: ScheduleService consulted this turn
        try:
            slots_out = self._schedule.list_slots(sched_in)
            all_slots = sorted([s for s in (slots_out.slots or []) if isinstance(s, str)])
            is_closed = slots_out.business_hours == "cerrado"
        except Exception as exc:
            logger.warning("M18 list_slots failed in day_only thread_id=%s: %s", ctx.thread.id, exc)
            all_slots = []

        state.active_requested_date = day_iso
        state.preferred_day = day_iso  # overwrite stale preference so "Sí" confirms the new day
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
            # ── L4.7W3-F1 FLOW-FIRST (owner decision) ────────────────────────
            # A day request with real availability opens the Booking Flow as the slot
            # picker instead of reciting five times in prose. Wild W3 answered
            # "¿Qué tenés mañana?" with a sentence full of times and a question — a picker
            # rendered as text. The slots handed to the Flow are exactly what
            # ScheduleService approved for this candidate, location and cycle: opening the
            # Flow shows availability, it does not book anything.
            flow_out = self._dispatch_booking_flow_for_day(
                ctx, state, day_iso=day_iso, date_human=date_human,
                slots=shown, period_label=period_label)
            if flow_out is not None:
                return flow_out
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
            # L4.7W5-F2: the Wild failure. The customer had named a day AND delegated the
            # choice ("no se viernes? decime vos cuando pueden"); the named day was empty,
            # and asking them to name another one ignored the half of the sentence that
            # handed us the decision. Search forward instead — but only when they actually
            # delegated, so a plain "¿el viernes?" still gets a plain answer.
            if texts and self._wants_next_available(texts):
                forward = self._handle_next_available_request(ctx, state, texts)
                if forward is not None:
                    return forward
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
        self._answer_source = "DETERMINISTIC_RULE"
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
        """Customer insists on an unavailable slot — hand off to Julián, recording the request
        through the booking authority as a NON-occupying provisional revision when Smart
        Booking is on. Does NOT send Flow, does NOT set AGENDADO."""
        self._answer_source = "DETERMINISTIC_RULE"
        lead = ctx.lead
        assert lead is not None

        lead.necesita_humano = True
        lead.flag = "ACEPTADO"
        lead.estado = "ATENCION_HUMANA"
        state.needs_human = True
        state.last_stage = STAGE_HUMAN

        # ── L4.7W5-F7A: withdraw the offer the customer just rejected ────────────────
        # One explicit lifecycle transition, not a convenience. The token binds a Flow
        # submission to the offer that produced it; the customer has said that offer does
        # not work. Leaving it live would let a tap on the old Flow book the very slot they
        # rejected — after a human had taken the thread over. Consuming it makes a late
        # submission fail token validation in resolve_context rather than create that
        # booking. Bookings already confirmed are untouched: this only invalidates an
        # OUTSTANDING offer.
        # G3-7P: through the one authority. F7A's reasoning is unchanged — the customer
        # said this offer does not work, so a late tap must not book the very slot they
        # rejected — but the offer row now records WHY it ended, and the scheduling facts
        # are deliberately preserved here: F7A hands the thread to a person, it does not
        # discard what the customer told us about when they are available.
        from .offer_lifecycle import WithdrawReason as _F7AWR
        if self._withdraw_booking_offer(ctx, state, reason=_F7AWR.OFFER_REJECTED,
                                        clear_scheduling=False):
            logger.info(
                "L4.7W5-F7A FLOW_OFFER_WITHDRAWN thread_id=%s — rejected offer withdrawn "
                "at handoff", ctx.thread.id)

        # Convergence Phase 3: the manual-scheduling REQUEST is a booking command, so it is
        # the booking authority's (CE-W2: a NON-occupying REPROGRAMAR Revision plus a
        # provisional ThreadRevision, booking-owned plan only), then CE's commit, then the
        # e-mail and the reply (B-8).
        if self._smart_booking_ce_on():
            return self._sb_escalation(ctx, state, last_message)

        # SMART_BOOKING_CE_ENABLED off: the human handoff above stands and nothing is booked.
        # The legacy in-session writer that stood here is deleted: it created a `Revision`
        # with the default status PENDIENTE — OCCUPYING — at the very date and time CE had
        # just judged unavailable, outside the booking authority, with no date lock, no
        # exclusion pre-check and no ledger, and it sent the e-mail before its own commit
        # while holding the thread-state and lead rows. The handoff now commits FIRST; the
        # operator e-mail and the reply follow, holding nothing.
        thread_rev_id: int | None = state.current_revision_id
        self.db.commit()
        try:
            self._send_scheduling_handoff_email(
                ctx=ctx, state=state, thread_rev_id=thread_rev_id, last_message=last_message,
            )
        except Exception as exc:
            logger.error("scheduling handoff e-mail failed thread_id=%s: %s — handoff stands",
                         ctx.thread.id, type(exc).__name__)

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
        _notif_grp, _notif_det = self._get_active_inspection_location(ctx, state)

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
            zone_group=_notif_grp or "Sin dato",
            zone_detail=_notif_det or "Sin dato",
            precio_total=str(real_price_quote.precio_total) if real_price_quote else "Sin dato",
            requested_slot=requested_str,
            offered_slots=offered_str,
            last_message=last_message or "Sin dato",
        )

    # ── AI prompt ─────────────────────────────────────────────────────────

    def _reply_input_texts(self, fallback: list[str]) -> list[str]:
        """The customer language the reply-generation model may read.

        G3-5E (audit G5D-02). The reply model used to be handed `ai_input_messages`, the
        presentation subset — the same defect G3-5B closed for the semantic interpreter,
        left open for the one component that can also mutate canonical state. It now reads
        the certified `CanonicalTurnBurst`, in canonical order. `fallback` is used only
        where no burst was reconstructed (an engine that did not go through `handle`).
        """
        burst = getattr(self, "_canonical_turn_burst", None)
        if burst is not None and burst.messages:
            self._reply_burst = burst.fingerprint
            return list(burst.texts)
        self._reply_burst = None
        return list(fallback or [])

    def _reply_decision(self, messages: list[dict], *, role: str, ctx) -> tuple:
        """The ONE route by which a reply-model decision may reach a consumer.

        G3-5F §4.3 (audit G5E2-03). The binding, the role, the call count and the admission
        verdict were each correct, but they were assembled at the two consumption sites, so
        "a reply decision can only come from `_call_openai`" held by inspection rather than
        by construction: a third call site, a retry wrapper or an adapter would have been
        admitted unbound and nothing would have said so. Both consumers now obtain their
        decision here and nowhere else, and a structural test asserts that no consumer
        parses a provider result of its own.

        Returns `(decision, admission)`. A REJECTED admission yields the bounded
        non-mutating refusal, never the model's own fields. `NOT_DISPATCHED` is preserved
        with its G3-5E2 meaning — the engine's provider boundary was never crossed, which
        is only reachable when a caller substitutes `_call_openai` itself — but it is now a
        property of THIS function rather than of each call site: the counter is read before
        and after the call, so a real provider call can never present as undispatched, and
        a substituted one can never present as bound.
        """
        _before = getattr(self, "_reply_calls", 0)
        raw = self._call_openai(messages, role=role)
        decision = json.loads(raw)
        dispatched = getattr(self, "_reply_calls", 0) > _before
        admission = (self._reply_admission() if dispatched
                     else ReplyAdmission.NOT_DISPATCHED)
        self._reply_admission_state = admission
        if admission in ReplyAdmission.REJECTED:
            return self._refuse_reply(ctx, admission, role), admission
        # ── G3-7: the renderer's output contract ─────────────────────────────────
        # When demoted, the mutation fields are removed HERE, at the one boundary every
        # reply decision crosses. A consumer cannot opt back in, and a future consumer
        # cannot find them by accident: past this point the decision carries renderer
        # material only. `_reconciled_reply_proposals` supplies what they used to.
        #
        # G3-7H P2 — ATTEMPTED, MEASURED, REVERTED. Making this strip unconditional is the
        # right end state and it works: all six settings postures (attribute absent, false,
        # true, malformed, None, MagicMock) then strip every field. But the full regression
        # measured the cost: 46 real-customer identities — vehicle replacement, focus
        # switching, zone correction, quote and acceptance progression in
        # `messy_turn_reconciliation`, `wild04r_f3/f4/f6` — obtain their candidate proposal
        # ONLY from this field set in the deployed posture, which has no demotion attribute
        # at all. The reconciled alternative exists but needs admitted semantic evidence,
        # and whether the frozen prompt covers the durable corpus is the very proof G3-7G
        # named as missing. Stop condition §13.1. Do not restore authority elsewhere and do
        # not add phrases; the dependency is the corpus evaluation, not a workaround.
        if self._reply_renderer_demotion_on():
            for field in self._REPLY_MUTATION_FIELDS:
                decision.pop(field, None)
            self._reply_contract = ReplyContract.RENDERER_ONLY
        else:
            self._reply_contract = ReplyContract.LEGACY_WITH_MUTATIONS
        return decision, admission

    def _reply_admission(self) -> str:
        """May this turn's reply-model output be consumed? Typed, and fail-closed.

        G3-5E2. Called immediately after the provider returns and BEFORE any field of the
        result is read, so one check protects every consumer — extracted fields, the
        candidate, the narrative interpretation, the motorcycle handoff, the lead flag, the
        stage reset and the prose alike. It reads state only; it can never call the
        provider again, so a rejection costs nothing.

        Every production reply call is made from `_process_text`, which builds the canonical
        burst before any gate can return — so a reply without a burst is not a legitimate
        route and is refused rather than trusted. A test asserts that property against the
        real call sites.
        """
        if not getattr(self, "_reply_calls", 0):
            # The engine's own provider boundary was never crossed — only reachable when a
            # caller substitutes `_call_openai` itself. There is no dispatch to bind, so
            # this is not a failure: §4 forbids manufacturing an error where the route
            # never used the reply model.
            return ReplyAdmission.NOT_DISPATCHED
        burst = getattr(self, "_canonical_turn_burst", None)
        bound = getattr(self, "_reply_burst", None)
        if burst is None or bound is None:
            return ReplyAdmission.REJECTED_MISSING_BINDING
        if bound != burst.fingerprint:
            return ReplyAdmission.REJECTED_FOREIGN_BURST
        return ReplyAdmission.ADMITTED

    def _refuse_reply(self, ctx, admission: str, role: str) -> dict:
        """The bounded, non-mutating result a refused reply is replaced with.

        Deliberately NOT the provider-failure fallback: that one sets `needs_human=True`,
        and escalating a customer to a human because a binding check failed would be a
        canonical mutation caused by the guard itself. This decision drives nothing —
        no candidate, no extracted field, no flag — and carries the same bounded sentence
        the provider-failure path already uses.
        """
        self._reply_admission_state = admission
        try:
            logger.warning(
                "G3-5E2 REPLY BINDING REFUSED thread_id=%s role=%s reason=%s canonical=%s "
                "bound=%s",
                getattr(getattr(ctx, "thread", None), "id", None), role, admission,
                str(getattr(getattr(self, "_canonical_turn_burst", None), "fingerprint",
                            None))[:12],
                str(getattr(self, "_reply_burst", None) or "absent")[:12])
        except Exception:
            pass
        return {"intent": "OTHER", "reply": "Disculpá la demora, ya te ayudamos.",
                "deferred_interest": False, "candidate": {"action": "none"},
                "extracted": {}, "lead_flag": None, "needs_human": False}

    def _build_ai_messages(
        self,
        ctx: _Context,
        event: ConversationHandleIn,
        ai_input_messages: list[str],
        pre_detected_vehicle: VehicleMatch | None = None,
        real_price_quote: "PricingQuote | None" = None,
        include_narrative: bool = False,
    ) -> list[dict]:
        # G3-5E: the canonical burst replaces the presentation subset as the reply model's
        # customer-language input. Every other argument is unchanged.
        ai_input_messages = self._reply_input_texts(ai_input_messages)
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
        _ai_grp, _ai_det = self._get_active_inspection_location(ctx, state)
        zone = _ai_det or _ai_grp or "desconocida"
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

        # F3-T4: Show all non-focus candidates so AI can re-focus a prior vehicle by ID.
        # Example: "Al final volvamos con el Peugeot" → AI returns action=update id=<old_id>
        # status=current_focus to switch focus without creating a duplicate.
        _other_cands_block = ""
        _other_cands = [c for c in ctx.candidates if focus is None or c.id != focus.id]
        if _other_cands:
            _cand_lines = []
            for _oc in _other_cands:
                _oc_parts = [_oc.marca, _oc.modelo, str(_oc.anio) if _oc.anio else None]
                _oc_desc = " ".join(p for p in _oc_parts if p) or "sin datos"
                _cand_lines.append(
                    f"  - ID {_oc.id}: {_oc_desc} ({_oc.tipo_vehiculo or '?'}) [{_oc.status}]"
                )
            _other_cands_block = "\n- Candidatos previos (usa action=update id=<ID> status=current_focus para retomar uno):\n" + "\n".join(_cand_lines)

        system_prompt = f"""Sos el asistente de Ridecheck, servicio de revisión pre-compra de autos en Argentina. Ayudás a coordinar inspecciones antes de que el cliente compre un vehículo usado.

ESTADO ACTUAL:
- Etapa: {stage}
- Flag del lead: {flag}
- Nombre del cliente: {customer}
- Zona: {zone}
- Vehículo en foco: {vehicle_txt}{focus_id_block}{_other_cands_block}{precio_info}{detected_vehicle_block}

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
17. SERVICIO — QUÉ INCLUYE: Revisamos el vehículo en el lugar donde está. Verificamos carrocería y estructura, motor y compartimiento, transmisión, suspensión y tren delantero/trasero, frenos, neumáticos, interior, sistema eléctrico/electrónico, espesores de pintura con medidor (para detectar golpes y reparaciones previas), lectura de errores con escáner OBD y verificación de kilometraje real. Más de 250 puntos de control. Al terminar, el cliente recibe un informe detallado. Respondé con esta información cuando pregunten qué incluye o de qué consta el servicio. Sé conciso y natural, no leas la lista completa.
18. MEDIOS DE PAGO: Se acepta transferencia bancaria, Mercado Pago y efectivo. NO se acepta débito ni tarjeta de crédito. Ante cualquier pregunta sobre formas de pago, respondé con exactamente esta información. NUNCA inventes ni asumas un medio de pago distinto a los tres aceptados.
19. RETORNO A CANDIDATO PREVIO: Si el cliente vuelve a un vehículo listado en "Candidatos previos", usá action=update id=<ID del candidato> status=current_focus. NO crees un candidato duplicado con el mismo vehículo.
20. Si Zona='desconocida' al momento de responder: SIEMPRE terminá tu mensaje con la pregunta de zona, incluso si ya respondiste FAQs u otras consultas. No se puede cotizar sin zona. Ejemplo: "¿En qué zona o ciudad está el auto?"

TIPOS DE VEHÍCULO VÁLIDOS: AUTO, SUV_4X4_DEPORTIVO, CLASICO, MOTO, ESCANEO_MOTOR{narrative_block}

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

    def _call_openai(self, messages: list[dict], *, role: str | None = None) -> str:
        """The reply-generation provider. Counted and timed separately from the semantic one.

        G3-5E (audit G5D-06). `semantic_model_calls` counts the interpreter; this counts the
        renderer. One configured model may serve both — the roles are told apart by call
        site, prompt version and consumer, never by the model name.
        """
        # The provider-call marker stays HERE, at the single boundary every reply call
        # crosses, so the one-place accounting guard keeps holding after the G3-5E split.
        self._ai_invoked = True
        self._reply_calls = getattr(self, "_reply_calls", 0) + 1
        self._reply_role = role or getattr(self, "_reply_role", None)
        # G3-5E2 (audit G5E-01): the binding is taken HERE, at the provider boundary, so it
        # records the burst that was current when the request was actually dispatched. The
        # admission check then compares it with the burst current at consumption time — a
        # result may only be consumed while the evidence it was asked about still stands.
        _burst = getattr(self, "_canonical_turn_burst", None)
        self._reply_burst = getattr(_burst, "fingerprint", None)
        _t0 = _time.perf_counter()
        try:
            out = self._call_openai_inner(messages)
        except Exception:
            self._reply_status = "ERROR"
            self._reply_latency_ms = int((_time.perf_counter() - _t0) * 1000)
            raise
        self._reply_status = "OK"
        self._reply_latency_ms = int((_time.perf_counter() - _t0) * 1000)
        return out

    def _call_openai_inner(self, messages: list[dict]) -> str:
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
            from .booking_authority import forbid_external_io  # convergence Phase 4 (P2R-05)
            forbid_external_io("openai")
            with urlrequest.urlopen(req, timeout=30) as resp:
                response_body = resp.read().decode("utf-8", errors="replace")
        except urlerror.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI chat failed HTTP {exc.code}: {err_body[:400]}") from exc

        payload = json.loads(response_body)
        return payload["choices"][0]["message"]["content"]

    # ── Context helpers ───────────────────────────────────────────────────

    # ── G3-7R/S2 ─────────────────────────────────────────────────────────────
    #
    # `_is_offer_acceptance_turn` and `_begin_thread_transaction` used to live here, and
    # they are DELETED rather than repaired. They existed to decide, at turn entry, whether
    # a turn would later become a booking-offer acceptance writer, and to take resource 1
    # of the offer lock order on the strength of that guess.
    #
    # The guess is unnecessary. The question it was guessing at is answered exactly, later
    # and cheaply, by `OfferLifecycle.admit()`: one indexed read asking whether the
    # submitted token matches an ISSUED offer row for this thread. Everything the predicate
    # consulted — the payload shape, the transport mirror, the token prefix — was a proxy
    # for that, and a proxy that could put an ordinary conversation turn inside the booking
    # lock order. Its own docstring recorded the fallback as answering True when it could
    # not tell, which is a safety control whose default is to serialise traffic that can
    # never book.
    #
    # The lock order itself is not gone and is not weaker: `offer_lifecycle` acquires
    # resource 1 inside every transition of the offer table, so no writer can reach the
    # offer rows without it — which is strictly more coverage than a turn-entry gate ever
    # had, since ISSUE never passed through one.

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

        state = thread.state  # may be None for first-ever message on this thread

        # WILD-04R: apply cycle watermark to candidates when set
        cand_query = (
            select(WhatsAppThreadCandidate)
            .where(WhatsAppThreadCandidate.thread_id == thread_id)
            .order_by(WhatsAppThreadCandidate.updated_at.desc(), WhatsAppThreadCandidate.id.desc())
        )
        if state is not None and state.current_cycle_started_at is not None:
            cand_query = cand_query.where(
                WhatsAppThreadCandidate.created_at >= state.current_cycle_started_at
            )
        else:
            # FIX 4 (CL-04): null watermark — load all candidates (pre-watermark behavior).
            # Threads with no cycle reset have no watermark; loading all candidates is safe
            # because _execute_cycle_reset archives prior-cycle candidates and clears state.
            # The cycle reset mechanism is the correct guard for cross-cycle contamination.
            # A null watermark on a thread that HAS had a cycle reset indicates a migration
            # gap; log a warning so it is visible in forensics.
            if (
                state is not None
                and state.last_processed_inbound_wa_message_id is not None
                and state.current_focus_candidate_id is not None
            ):
                logger.debug(
                    "L1 CL-04 null watermark with prior activity thread_id=%s — loading all candidates (pre-watermark path)",
                    thread_id,
                )
        candidates = list(self.db.execute(cand_query).scalars().all())

        # WILD-04R: apply cycle watermark to messages + fix ordering (newest-20 reversed)
        db_messages = self._query_active_messages(thread_id, state)

        return _Context(
            thread=thread,
            contact=contact,
            lead=lead,
            state=state,
            candidates=candidates,
            db_messages=db_messages,
        )

    # ── M21.4A: Attribution helper ────────────────────────────────────────────

    def _maybe_set_attribution(self, ctx: "_Context", state: "WhatsAppThreadState") -> None:
        """Populate lead.inbound_channel and lead.acq_source from available evidence.

        First-write-only for both fields.  Called once per handle() invocation after
        the lead is confirmed present.  Does NOT clear or overwrite existing values.
        Does NOT set acq_source when evidence is absent (leaves it null = unknown).
        Cycle reset does not invoke this method — attribution persists across cycles.
        """
        lead = ctx.lead
        if lead is None:
            return

        # Propagate thread.inbound_channel → lead.inbound_channel (display copy)
        if not getattr(lead, "inbound_channel", None):
            thread_ch = getattr(ctx.thread, "inbound_channel", None)
            if thread_ch:
                lead.inbound_channel = thread_ch
                logger.debug(
                    "M21.4A inbound_channel propagated thread_id=%s channel=%s",
                    ctx.thread.id, thread_ch,
                )

        # acq_source: first-write-only, evidence-based, deterministic only
        if getattr(lead, "acq_source", None):
            return  # already set — preserve original acquisition source

        # Priority 1: website tracking ref_code (most specific — from tracking.js)
        ref = (getattr(lead, "ref_code", None) or "").strip().lower()
        if ref:
            mapped = _REF_CODE_SOURCE_MAP.get(ref)
            if mapped:
                lead.acq_source = mapped
                logger.info(
                    "M21.4A acq_source from ref_code thread_id=%s ref=%s source=%s",
                    ctx.thread.id, ref, mapped,
                )
                return

        # Priority 2: Meta CTWA referral from thread (first-message referral object)
        ctwa_url = (getattr(ctx.thread, "ctwa_source_url", None) or "").strip()
        ctwa_id = getattr(ctx.thread, "ctwa_source_id", None)
        ctwa_type = (getattr(ctx.thread, "ctwa_source_type", None) or "").strip()
        if ctwa_url or ctwa_id:
            src = _ctwa_to_acq_source(ctwa_url, ctwa_id, ctwa_type)
            if src:
                lead.acq_source = src
                logger.info(
                    "M21.4A acq_source from CTWA thread_id=%s source_type=%s resolved=%s",
                    ctx.thread.id, ctwa_type or "-", src,
                )
                return

        # Priority 3: website lead without ref_code — source is WEBSITE
        if getattr(state, "is_website_lead", False):
            lead.acq_source = _ACQ_WEBSITE
            logger.info(
                "M21.4A acq_source=WEBSITE from is_website_lead thread_id=%s",
                ctx.thread.id,
            )
            return

        # No deterministic evidence — leave acq_source null (unknown)

    def _query_active_messages(
        self, thread_id: int, state: WhatsAppThreadState | None
    ) -> list[WhatsAppMessage]:
        """Return the newest 20 active-cycle messages in chronological order.

        WILD-04R fix: was ORDER BY timestamp ASC LIMIT 20 (oldest-20 bug).
        Now: ORDER BY id DESC LIMIT 20, then reversed = newest-20 chronologically.
        Cycle watermark applied when current_cycle_start_message_db_id is set.
        """
        msg_query = select(WhatsAppMessage).where(WhatsAppMessage.thread_id == thread_id)
        if state is not None and state.current_cycle_start_message_db_id is not None:
            msg_query = msg_query.where(
                WhatsAppMessage.id >= state.current_cycle_start_message_db_id
            )
        msg_query = msg_query.order_by(WhatsAppMessage.id.desc()).limit(20)
        msgs = list(self.db.execute(msg_query).scalars().all())
        msgs.reverse()
        return msgs

    def _reload_active_candidates(
        self, thread_id: int, state: "WhatsAppThreadState"
    ) -> "list[WhatsAppThreadCandidate]":
        """Re-query candidates applying the current (post-reset) cycle watermark.

        WILD-04R-F2: called immediately after _execute_cycle_reset so the first
        new-cycle turn sees the correct (empty) candidate list instead of the
        prior cycle's candidates that _load_context loaded with the old watermark.
        """
        q = (
            select(WhatsAppThreadCandidate)
            .where(WhatsAppThreadCandidate.thread_id == thread_id)
            .order_by(
                WhatsAppThreadCandidate.updated_at.desc(),
                WhatsAppThreadCandidate.id.desc(),
            )
        )
        if state.current_cycle_started_at is not None:
            q = q.where(
                WhatsAppThreadCandidate.created_at >= state.current_cycle_started_at
            )
        return list(self.db.execute(q).scalars().all())

    def _fetch_burst_messages(
        self,
        thread_id: int,
        previous_cursor: str | None,
        current_wa_message_id: str,
    ) -> list["WhatsAppMessage"]:
        """Return all persisted inbound burst messages as objects, oldest-first.

        WILD-04R burst completeness: n8n has a 10-message limit. This queries
        all inbound messages between the previous processed cursor and the
        current event from DB — the authoritative record with no hard limit.

        Returns [] when previous_cursor is None (first-ever message on thread).
        """
        if previous_cursor is None:
            return []

        current_row = self.db.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == current_wa_message_id)
        ).scalar_one_or_none()
        if current_row is None:
            return []

        prev_row = self.db.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == previous_cursor)
        ).scalar_one_or_none()
        if prev_row is None:
            return []

        return list(
            self.db.execute(
                select(WhatsAppMessage)
                .where(
                    WhatsAppMessage.thread_id == thread_id,
                    WhatsAppMessage.direction == "in",
                    WhatsAppMessage.id > prev_row.id,
                    WhatsAppMessage.id <= current_row.id,
                )
                .order_by(WhatsAppMessage.id.asc())
            ).scalars().all()
        )

    def _fetch_burst_texts(
        self,
        thread_id: int,
        previous_cursor: str | None,
        current_wa_message_id: str,
    ) -> list[str]:
        """Return text strings of all persisted inbound burst messages, oldest-first."""
        return [
            row.text
            for row in self._fetch_burst_messages(thread_id, previous_cursor, current_wa_message_id)
            if row.text is not None
        ]

    def _get_or_create_state(self, ctx: _Context) -> WhatsAppThreadState:
        if ctx.state is not None:
            return ctx.state
        state = WhatsAppThreadState(thread_id=ctx.thread.id)
        ctx.thread.state = state
        self.db.add(state)
        self.db.flush()
        ctx.state = state
        return state

    def _execute_cycle_reset(
        self,
        ctx: _Context,
        state: WhatsAppThreadState,
        event: ConversationHandleIn,
        previous_cursor: str | None,
    ) -> None:
        """Consume cycle_reset_pending and establish new cycle watermarks.

        WILD-04R: called when a human has moved the Lead back to CONSULTA_NUEVA.
        Clears all ACTIVE_REVISION fields and sets watermarks so subsequent
        context queries return only new-cycle data.
        """
        lead = ctx.lead
        assert lead is not None

        # WILD-04R-F5 D2: Archive all prior-cycle current_focus candidates before
        # establishing new cycle watermarks.  Without this step the DB can accumulate
        # multiple rows with status='current_focus' from different cycles, which
        # corrupts _focus_candidate() resolution on any future ctx reload that
        # accidentally includes old candidates.
        prior_focus = list(self.db.execute(
            select(WhatsAppThreadCandidate)
            .where(
                WhatsAppThreadCandidate.thread_id == ctx.thread.id,
                WhatsAppThreadCandidate.status == "current_focus",
            )
        ).scalars().all())
        for cand in prior_focus:
            cand.status = "archived"
        if prior_focus:
            self.db.flush()

        # Find current event's DB row (always exists — persisted before CE dispatch)
        current_event_db_row = self.db.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == event.wa_message_id)
        ).scalar_one_or_none()

        # Resolve previous cursor to a DB integer id for burst boundary query
        previous_db_id: int | None = None
        if previous_cursor is not None:
            prev_row = self.db.execute(
                select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == previous_cursor)
            ).scalar_one_or_none()
            if prev_row is not None:
                previous_db_id = prev_row.id

        # Find the earliest inbound message in this burst to use as cycle start
        burst_start_message = current_event_db_row
        if current_event_db_row is not None:
            burst_query = (
                select(WhatsAppMessage)
                .where(
                    WhatsAppMessage.thread_id == ctx.thread.id,
                    WhatsAppMessage.direction == "in",
                    WhatsAppMessage.id <= current_event_db_row.id,
                )
                .order_by(WhatsAppMessage.id.asc())
            )
            if previous_db_id is not None:
                burst_query = burst_query.where(WhatsAppMessage.id > previous_db_id)
            burst_msgs = list(self.db.execute(burst_query).scalars().all())
            if burst_msgs:
                burst_start_message = burst_msgs[0]

        # Set cycle watermarks from earliest burst message (DB server clock, no skew)
        if burst_start_message is not None:
            state.current_cycle_start_message_db_id = burst_start_message.id
            state.current_cycle_started_at = burst_start_message.created_at

        # Clear all ACTIVE_REVISION ThreadState fields
        state.last_intent = None
        state.last_stage = None
        state.needs_human = False
        state.current_focus_candidate_id = None
        state.current_revision_id = None
        state.home_zone_group = None
        state.home_zone_detail = None
        state.preferred_day = None
        state.preferred_time = None
        state.active_requested_date = None
        state.last_requested_time = None
        state.last_offered_slots = None
        state.last_visible_slots = None
        # G3-7P: a new cycle invalidates the previous cycle's offer through the one
        # authority, so a token from the closed cycle cannot book into this one. The field
        # assignments around it are the cycle reset's own; only the OFFER moves here.
        from .offer_lifecycle import OfferLifecycle as _ResetOffers, WithdrawReason as _ResetWR
        _ResetOffers(self.db).withdraw(state, thread_id=ctx.thread.id,
                                       reason=_ResetWR.CYCLE_RESET, clear_scheduling=False)
        state.vehicle_clarification_sent = False
        state.location_clarification_sent = False
        state.vehicle_fallback_flow_sent = False
        state.location_fallback_flow_sent = False
        state.inspectability_clarification_sent = False
        state.pending_fuzzy_catalog_key = None
        state.pending_turn_evidence_text = None
        state.pending_location_proposal = None
        state.cycle_reset_pending = False  # consume the one-shot signal

        # Clear Lead ACTIVE_REVISION fields
        lead.flag = None
        lead.necesita_humano = False
        lead.motivo_perdida = None
        lead.buscando_auto_set_at = None

        # Reload candidates and messages filtered to new cycle
        if state.current_cycle_started_at is not None:
            ctx.candidates = list(
                self.db.execute(
                    select(WhatsAppThreadCandidate)
                    .where(
                        WhatsAppThreadCandidate.thread_id == ctx.thread.id,
                        WhatsAppThreadCandidate.created_at >= state.current_cycle_started_at,
                    )
                    .order_by(
                        WhatsAppThreadCandidate.updated_at.desc(),
                        WhatsAppThreadCandidate.id.desc(),
                    )
                ).scalars().all()
            )
        else:
            ctx.candidates = []

        ctx.db_messages = self._query_active_messages(ctx.thread.id, state)

        logger.info(
            "WILD-04R cycle reset thread_id=%s watermark_msg_id=%s watermark_ts=%s",
            ctx.thread.id,
            state.current_cycle_start_message_db_id,
            state.current_cycle_started_at,
        )

    def _focus_candidate(self, ctx: _Context, *,
                         repair: bool = True) -> WhatsAppThreadCandidate | None:
        """The candidate in focus, or None when focus is ambiguous.

        `repair=False` makes the call read-only. G3-7B added it because the CL-05 branch
        below WRITES — it clears a stale pointer — and the candidate resolver, documented as
        side-effect free, reached that write through this helper. The repair itself is
        unchanged and still runs once per turn; it simply runs in the proposal step now
        (`_reconciled_reply_proposals`) rather than inside the resolution step.
        """
        # FIX 5 (CL-05): explicit, non-arbitrary focus resolution.
        state = ctx.state
        if state and state.current_focus_candidate_id:
            for c in ctx.candidates:
                if c.id == state.current_focus_candidate_id:
                    return c
            # Stored ID not in active-cycle candidates — stale cross-cycle reference; clear it.
            if not repair:
                return None
            logger.warning(
                "L1 CL-05 stale current_focus_candidate_id=%s thread_id=%s — clearing",
                state.current_focus_candidate_id,
                ctx.thread.id if ctx.thread else "?",
            )
            state.current_focus_candidate_id = None
        for c in ctx.candidates:
            if c.status == "current_focus":
                return c
        # Unambiguous single-candidate fallback (documented, not nondeterministic).
        if len(ctx.candidates) == 1:
            return ctx.candidates[0]
        # Multiple candidates without an explicit focus — ambiguous; return None so
        # callers receive a definitive signal instead of an arbitrary selection.
        if len(ctx.candidates) > 1:
            logger.warning(
                "L1 CL-05 ambiguous focus: %d candidates, none is current_focus thread_id=%s",
                len(ctx.candidates),
                ctx.thread.id if ctx.thread else "?",
            )
        return None

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

    # ── Semantic authority hierarchy (L1-SEMANTIC-AUTHORITY) ────────────────
    # Canonical precedence for all state/candidate field writes (highest → lowest):
    #   1. CURRENT INBOUND EXPLICIT EVIDENCE — LR-3 zone, deterministic year/date parse
    #   2. CURRENT BURST CORRECTION — explicit customer correction detected this turn
    #   3. ACTIVE-CYCLE CANONICAL — established candidate field / cycle-scoped state
    #   4. ACTIVE-CYCLE FALLBACK — state.home_zone_* when candidate has no zone
    #   5. HISTORICAL — prior-cycle data; MUST NOT overwrite any of the above
    # AI may interpret and propose but must not overwrite deterministic current-turn facts.
    # _fill_if_absent pattern: write only when target field is currently falsy.
    # ──────────────────────────────────────────────────────────────────────────

    def _apply_extracted(self, ctx: _Context, state: WhatsAppThreadState, extracted: dict) -> None:
        # Semantic authority: AI fills missing values only.  Fields already written
        # by deterministic extraction (LR-3, _parse_scheduling_text, prior customer
        # statement) are NOT overwritten by AI interpretation.
        # FIX 6 (RISK-01): customer_name is set once (first-write-wins, mirrors lead.nombre guard).
        if extracted.get("customer_name") and not state.customer_name:
            state.customer_name = extracted["customer_name"]
            if ctx.lead and not ctx.lead.nombre:
                ctx.lead.nombre = extracted["customer_name"].split()[0]
        if extracted.get("zone_detail") and not state.home_zone_group:
            # Only accept AI zone if we don't already have a DB-validated zone_group.
            # This prevents AI from overwriting a confirmed zone with a hallucination.
            state.home_zone_detail = extracted["zone_detail"]
        # zone_group is intentionally NOT read from AI — DB normalization always
        # sets the canonical value in _normalize_zone_from_db.
        # FIX 3 (RISK-02): scheduling fields are fill-if-absent only; deterministic
        # parse (_parse_scheduling_text) in the scheduling block is authoritative.
        if extracted.get("preferred_day_iso") and not state.preferred_day:
            raw_day = str(extracted["preferred_day_iso"]).strip()
            try:
                date.fromisoformat(raw_day)  # validate before storing
                state.preferred_day = raw_day
            except (ValueError, TypeError):
                logger.warning(
                    "M18 ignoring malformed preferred_day_iso=%r thread_id=%s",
                    raw_day, ctx.thread.id,
                )
        if extracted.get("preferred_time_str") and not state.preferred_time:
            raw_time = str(extracted["preferred_time_str"]).strip()
            if re.match(r"^\d{1,2}:\d{2}$", raw_time):  # must be "HH:MM", max 5 chars
                state.preferred_time = raw_time
            else:
                logger.warning(
                    "M18 ignoring malformed preferred_time_str=%r thread_id=%s",
                    raw_time, ctx.thread.id,
                )

    def _apply_candidate(self, ctx: _Context, candidate_data: dict, *,
                         zone_protected: bool = False) -> tuple:
        """Apply a candidate mutation. Returns `(applied, created_candidate_id)`.

        G3-7B: the return value is new; every existing caller ignores it, so behaviour is
        unchanged. It exists so `CandidateResolution.mutation_applied` can record what the
        writer ACTUALLY did instead of what the resolver predicted — the G3-7-R2 review
        found that field hard-wired to False and `created_candidate_id` hard-wired to None.
        """
        action = candidate_data.get("action", "none")
        if action == "none" or not candidate_data:
            return (False, None)
        state = ctx.state

        if action == "create":
            # F3-T3: Dedup — if a non-focus candidate with the same marca+modelo+tipo already
            # exists in ctx, redirect to update instead of creating a duplicate. This handles
            # M10 ("Al final volvamos con el Peugeot") where catalog re-fires on a previously
            # seen vehicle. Only dedup when marca AND modelo are both provided; bare-type or
            # bare-model creates are allowed to proceed to avoid false dedup on ambiguous turns.
            _new_marca = (candidate_data.get("marca") or "").strip().lower()
            _new_modelo = (candidate_data.get("modelo") or "").strip().lower()
            _new_tipo = (candidate_data.get("tipo_vehiculo") or "").upper() or None
            if _new_marca and _new_modelo:
                for _ex in ctx.candidates:
                    if (
                        (_ex.marca or "").strip().lower() == _new_marca
                        and (_ex.modelo or "").strip().lower() == _new_modelo
                        and _tipo_compatible(_ex.tipo_vehiculo, _new_tipo)
                        and _ex.status != "current_focus"
                    ):
                        logger.info(
                            "F3 candidate dedup: redirecting create→update id=%s %s %s",
                            _ex.id, _new_marca, _new_modelo,
                        )
                        return self._apply_candidate(ctx, {
                            **candidate_data,
                            "action": "update",
                            "id": _ex.id,
                            "status": candidate_data.get("status") or "current_focus",
                        })
            fields = {
                k: candidate_data[k]
                for k in ("marca", "modelo", "version_text", "anio", "tipo_vehiculo",
                           "zone_group", "zone_detail", "direccion_texto")
                if candidate_data.get(k) is not None
            }
            # WILD-04R-F5 D3: normalize tipo_vehiculo alias before persisting
            if "tipo_vehiculo" in fields:
                fields["tipo_vehiculo"] = _normalize_tipo_vehiculo(fields["tipo_vehiculo"])
            # WILD-04R-F6 guard (create path): if marca+modelo are present, look up
            # catalog to derive the authoritative tipo_vehiculo.
            _c_marca = (fields.get("marca") or "").strip()
            _c_modelo = (fields.get("modelo") or "").strip()
            if _c_marca and _c_modelo:
                _c_catalog_tipo = self._catalog_tipo_for(_c_marca, _c_modelo)
                if _c_catalog_tipo is not None:
                    _c_ai_tipo = fields.get("tipo_vehiculo") or ""
                    if _c_ai_tipo != _c_catalog_tipo:
                        logger.info(
                            "WILD04R-F6 create catalog authority: AI proposed %r → using %r marca=%r modelo=%r",
                            _c_ai_tipo, _c_catalog_tipo, _c_marca, _c_modelo,
                        )
                    fields["tipo_vehiculo"] = _c_catalog_tipo
            status = candidate_data.get("status") or "mentioned"
            candidate = WhatsAppThreadCandidate(thread_id=ctx.thread.id, status=status, **fields)
            self.db.add(candidate)
            self.db.flush()
            # FIX 1 (RISK-03): Do NOT inherit stale state.home_zone_* here.
            # Zone is filled by LR-3 (explicit vehicle-location in current turn) or by
            # the post-AI gap-fill block when LR-3 wrote to state this same turn.
            # Historical state zones from prior sessions must not contaminate new candidates.
            #
            # ── W1F-W-01 R2: a TYPED evidence row MAY be inherited, and only a typed one ──
            # The guard above stays exactly as it is, and it stays for the reason it was
            # written: `state.home_zone_*` cannot say which cycle, turn or ROLE it came
            # from, so inheriting it here could contaminate a new candidate with a locality
            # from a previous session. Review §8.2's answer is not to relax the guard but to
            # supply a value that CAN answer those questions — `whatsapp_thread_evidence`
            # carries `cycle_key`, `turn_id` and `role` as columns, so
            # `thread_evidence.bindable_locality` checks what RISK-03 could only assume.
            # This is `W1F-C-05` and `W1F-C-07`: a locality given before the vehicle existed
            # now reaches the candidate created afterwards. The write still goes through
            # `_apply_inspection_zone`, so the C2 chokepoint and the reconciler remain the
            # only authority, and the row is bound once so "which evidence set this
            # candidate's location" keeps one answer.
            # Neither the isolation rule NOR the interlock is repeated here:
            # `_bind_evidence_to_candidate` enforces both itself. Mutations M07 and M34 each
            # survived by deleting a call-site copy of a rule that also lived in the method,
            # so the copies are gone and the method is the single home for both.
            self._bind_evidence_to_candidate(ctx, state, candidate)
            if status == "current_focus" and state:
                for c in ctx.candidates:
                    if c.status == "current_focus":
                        c.status = "mentioned"
                state.current_focus_candidate_id = candidate.id
                # S3: the focus moved to a candidate no live offer was issued for.
                self._withdraw_offer_if_focus_moved(ctx, state)
            ctx.candidates.insert(0, candidate)
            return (True, candidate.id)

        elif action == "update":
            raw_id = candidate_data.get("id")
            new_tipo = (candidate_data.get("tipo_vehiculo") or "").upper() or None
            if raw_id:
                target_id = int(raw_id)
                target = next((c for c in ctx.candidates if c.id == target_id), None)
            else:
                # FIX 7 (RISK-05): AI omitted id — only proceed when focus is unambiguous.
                target = self._focus_candidate(ctx)
                if target is None:
                    logger.warning(
                        "L1 RISK-05 AI update missing candidate id, focus ambiguous thread_id=%s — skipping",
                        ctx.thread.id if ctx.thread else "?",
                    )
                    return (False, None)
                target_id = target.id
            # Type-safety: if the resolved candidate has an established tipo_vehiculo
            # that is incompatible with the new one (e.g. MOTO vs AUTO), redirect to
            # create rather than corrupting the existing candidate's identity.
            #
            # G3-7F-R2 (review G37FRR-06/07). A GROUNDED CORRECTION is the one case where
            # that redirect is wrong. The customer re-identified the car under discussion:
            # the Grounded-Target Invariant proved which row they meant, and a category
            # change is the expected CONSEQUENCE of correcting make and model, not evidence
            # of a second vehicle. Creating a row here left the thread holding both a MOTO
            # typed Ford Focus and a phantom AUTO. Only the reconciler sets this key, and
            # only after the GTI grounded the target.
            if (target is not None
                    and not candidate_data.get("correction_in_place")
                    and not _tipo_compatible(target.tipo_vehiculo, new_tipo)):
                return self._apply_candidate(ctx, {**candidate_data, "action": "create"})
            if target is None:
                return (False, None)
            # WILD-04R-F6 guard: catalog is authoritative for tipo_vehiculo.
            # Before applying any AI-proposed tipo, resolve the effective marca+modelo
            # (possibly updated in this same turn) against the catalog. If the catalog
            # has an entry, use its tipo instead of the AI proposal. This prevents
            # e.g. a location-only turn ("El auto está en San Miguel") from silently
            # downgrading SUV_4X4_DEPORTIVO → AUTO when the AI guesses from text alone.
            if candidate_data.get("tipo_vehiculo") is not None:
                _cd_for_guard = dict(candidate_data)  # copy to avoid mutating caller
                _resolve_marca = (_cd_for_guard.get("marca") or "").strip() or (target.marca or "")
                _resolve_modelo = (_cd_for_guard.get("modelo") or "").strip() or (target.modelo or "")
                _catalog_tipo = self._catalog_tipo_for(_resolve_marca, _resolve_modelo)
                if _catalog_tipo is not None:
                    _ai_proposed_tipo = (_cd_for_guard.get("tipo_vehiculo") or "").strip()
                    if _ai_proposed_tipo.upper() != _catalog_tipo:
                        logger.info(
                            "WILD04R-F6 catalog authority: AI proposed %r → using %r candidate=%s",
                            _ai_proposed_tipo, _catalog_tipo, target.id,
                        )
                    _cd_for_guard["tipo_vehiculo"] = _catalog_tipo
                    candidate_data = _cd_for_guard
            for k in ("marca", "modelo", "version_text", "anio", "tipo_vehiculo",
                       "zone_group", "zone_detail", "direccion_texto"):
                if candidate_data.get(k) is not None:
                    # FIX 2 (CL-07): zone fields are protected when LR-3 already wrote
                    # an authoritative location this turn.  AI may propose but must not
                    # overwrite deterministic current-turn vehicle-location evidence.
                    if zone_protected and k in ("zone_group", "zone_detail"):
                        continue
                    val = candidate_data[k]
                    # WILD-04R-F5 D3: normalize tipo_vehiculo alias on update
                    if k == "tipo_vehiculo":
                        val = _normalize_tipo_vehiculo(val)
                    setattr(target, k, val)
            new_status = candidate_data.get("status")
            if new_status:
                if new_status == "current_focus" and state:
                    for c in ctx.candidates:
                        if c.status == "current_focus" and c.id != target_id:
                            c.status = "mentioned"
                    state.current_focus_candidate_id = target_id
                    # S3: see the create branch above. Re-confirming the SAME candidate
                    # withdraws nothing, so this does not disturb the established
                    # VEHICLE_CHANGED withdrawal that follows a clarification completion.
                    self._withdraw_offer_if_focus_moved(ctx, state)
                target.status = new_status
            return (True, None)

    def _enforce_catalog_vehicle(self, ctx: _Context, match: VehicleMatch) -> None:
        """Override candidate tipo_vehiculo with the catalog result (post-AI).

        The catalog is always authoritative for tipo_vehiculo.  marca/modelo
        are only written if the candidate field is currently empty, so any
        version/trim detail added by the AI is preserved.

        Type-safety: if the current focus candidate's tipo_vehiculo belongs to a
        different logical group than the catalog match (e.g. an existing MOTO
        candidate vs a catalogued AUTO vehicle), a new candidate is created rather
        than the MOTO candidate being repurposed.
        """
        focus = self._focus_candidate(ctx)
        if focus is None:
            # AI returned action=none but catalog hit — create the candidate now.
            self._create_candidate_from_catalog(ctx, ctx.state, match)
            return
        if not _tipo_compatible(focus.tipo_vehiculo, match.tipo_vehiculo):
            # Type mismatch — preserve existing candidate and create a new one.
            logger.info(
                "M21.2.13 tipo conflict thread_id=%s candidate=%s existing=%r catalog=%r — creating new",
                ctx.thread.id, focus.id, focus.tipo_vehiculo, match.tipo_vehiculo,
            )
            self._create_candidate_from_catalog(ctx, ctx.state, match)
            return
        if focus.tipo_vehiculo != match.tipo_vehiculo:
            logger.info(
                "M18 catalog override thread_id=%s candidate=%s tipo %r→%r",
                ctx.thread.id, focus.id, focus.tipo_vehiculo, match.tipo_vehiculo,
            )
            _catalog_tipo = match.tipo_vehiculo
        else:
            _catalog_tipo = None
        # L4.7C.2: the catalog is the naming authority; the write still goes through the
        # single path so the decision is recorded like every other identity write.
        self._apply_vehicle_identity(
            ctx, focus,
            marca=(match.marca if not focus.marca else None),
            modelo=(match.modelo if not focus.modelo else None),
            tipo=_catalog_tipo,
            source="catalog", state=ctx.state)

    # ── WILD-04R-F6: Catalog authority guard ─────────────────────────────

    def _catalog_tipo_for(self, marca: str, modelo: str) -> "str | None":
        """Return the canonical tipo_vehiculo for a marca+modelo pair from the catalog.

        Uses the same _norm pipeline and _FULL_FORM_TO_ENTRY lookup as
        _resolve_fuzzy_key. Returns None if the pair is not in the catalog
        (unknown vehicle fallback — AI tipo is then accepted as-is).
        """
        from ..services.vehicle_catalog import _FULL_FORM_TO_ENTRY, _norm
        if not marca or not modelo:
            return None
        nfull = _norm(f"{marca} {modelo}")
        entry = _FULL_FORM_TO_ENTRY.get(nfull)
        if entry is None:
            return None
        raw_tipo = entry.get("t", "") or None
        if not raw_tipo:
            return None
        return _normalize_tipo_vehiculo(raw_tipo)

    # ── Deterministic helper methods ──────────────────────────────────────

    def _create_candidate_from_catalog(
        self,
        ctx: _Context,
        state: "WhatsAppThreadState | None",
        match: VehicleMatch,
        source_text: str = "",
    ) -> None:
        _model_str = str(match.modelo) if match.modelo is not None else None
        anio = _extract_year_from_text(source_text, exclude_token=_model_str) if source_text else None
        # FIX 1 (RISK-03): create with zone_group=None / zone_detail=None.
        # Prior state.home_zone_* MUST NOT be inherited here — it may be stale data
        # from a prior session.  Zone is filled by one of two authoritative paths:
        #   - LR-3 runs AFTER this call and writes directly to the candidate when zone
        #     evidence is present in the current turn.
        #   - The post-AI gap-fill block at line ~3161 copies state zone to candidate
        #     only when _vehicle_location_written is True (zone was written THIS turn).
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
        # ── W1F-W-01 R2: the SECOND creation site, and it is the one the Wild takes ──
        # Measured, not assumed: on the Wild's turn 3 ("es un toyota corolla 2024") the
        # candidate is created HERE, by the proactive catalogue path, and not by
        # `_apply_candidate` — so a binding placed only there would never have run on the
        # very sequence this milestone exists to close. Both creation sites bind, by one
        # call into one method, which is also why that method is where the rules live.
        self._bind_evidence_to_candidate(ctx, state, candidate)
        if state:
            state.current_focus_candidate_id = candidate.id
            # S3: a proactively created focus is still a focus move.
            self._withdraw_offer_if_focus_moved(ctx, state)
        ctx.candidates.insert(0, candidate)
        logger.info(
            "M18 proactive candidate created thread_id=%s candidate=%s tipo=%s anio=%s",
            ctx.thread.id, candidate.id, match.tipo_vehiculo, anio,
        )

    # ── W1F-W-01 R3 ─────────────────────────────────────────────────────────────
    # ── W1F-W-01 R1 — EVIDENCE ADMISSION ─────────────────────────────────────
    #
    # The review's one structural change, and it is an ORDER: a turn's understanding is
    # decided before any gate can answer the customer and return. Everything called from
    # here already exists — the role-aware deterministic reader, the R3 locality validators,
    # the C2 chokepoint, the certified reconciler, the claim projection. What did not exist
    # is a place where a semantic INSPECTION_LOCATION claim could arrive at all, which is
    # why the flags being ON changed nothing for it and why its refusal left no record.
    #
    # Scope, stated as a boundary rather than as an intention:
    #   * admits ONE family (the inspection locality) and records a typed refusal for every
    #     other family the interpreter produced;
    #   * writes only through `_apply_inspection_zone`, so the C2 chokepoints remain the
    #     only writers of a candidate field;
    #   * sends nothing, calls no model, quotes nothing, books nothing;
    #   * never overwrites an established value, and never outranks the customer's own words;
    #   * persists NO outstanding question. The system still does not know what it asked, so
    #     Layer F step 9 can still answer an answer. That is R2, and R1 does not pretend
    #     otherwise.

    def _integrated_features_armed(self) -> bool:
        """THE ONE INTERLOCK. Are R1, R2 and R3 all armed? Default OFF.

        OC-R2 CORRECTION 2, and the owner's reasoning is measured rather than theoretical.
        The R2 closeout itself proved an unsafe PARTIAL posture: with R1 ON and R2 OFF, an
        interpreter reading `INSPECTION_LOCATION = "la boca"` out of `me mordí la boca` was
        admitted and `('CABA','La Boca')` was written — the `W1F-C-18` protection defeated
        through the semantic path. Guarding only "R2 without R1" was therefore not enough:
        **R1's own safety depends on R2's predicate**, because the predicate is what refuses
        to consult the interpreter on a burst whose words resolve to no locality, and R3 is
        what makes a resolved locality safe to price.

        So the three flags are one switch for R1 and R2 behaviour. Every partial combination
        is INERT: no semantic locality admitted, no evidence persisted or bound, no question
        recorded or activated or answered, no greeting suppressed, no R3 validator bypassed.

        R3 keeps its own independence, as the owner specified — `_locality_safety_on()` is
        untouched, so `LOCALITY_RESOLUTION_SAFETY_ENABLED` alone still delivers exactly the
        certified R3 behaviour it was committed with.

        ONE implementation, not several duplicated checks: `_admission_on` and `_r2_on`
        both delegate here and add nothing, and a structural test asserts no other site
        reads the three flags together.
        """
        settings = getattr(self, "settings", None)
        return (getattr(settings, "evidence_admission_enabled", False) is True
                and getattr(settings, "outstanding_question_enabled", False) is True
                and getattr(settings, "locality_resolution_safety_enabled", False) is True)

    def _admission_on(self) -> bool:
        """Is the evidence-admission boundary armed? The interlock decides; see it."""
        return self._integrated_features_armed()

    def _r2_on(self) -> bool:
        """Is the R2 outstanding-question contract armed? The interlock decides; see it.

        Kept as its own name because it reads at the call sites as the thing being asked
        ("is the question contract live?"), but it adds NO condition of its own — a second
        opinion about the posture is how interlocks drift apart.
        """
        return self._integrated_features_armed()

    def _r2_cycle_key(self, state) -> int:
        """The canonical cycle identity, from the ONE function that defines it.

        `clarification_attempt.cycle_key` reads the integer column directly and documents
        why a timestamp must never substitute for it. R2 imports that function rather than
        repeating the COALESCE, so the questions, the evidence rows and the Flow attempts
        cannot end up keyed three ways.
        """
        from .clarification_attempt import cycle_key as _ck
        return _ck(state)

    def _locality_possible_in_burst(self, texts) -> bool:
        """R1-PL-01: is there anything in this burst for the boundary to DECIDE?

        One call into the ONE shared predicate. The resolver handed in is the inherited
        matcher — the same six passes the admission validator resolves a value through — so
        there is a single catalogue rule in play and this is not a second copy of it.

        The resolver passed in is widened by exactly one certified function, and the reason
        is G-11 rather than admission. `is_outside_coverage` is R3's own DB-free coverage
        list (`locality_text`), already consulted by `_locality_verdict`. Without it, a burst
        naming Córdoba or Rosario — real localities the system must REFUSE — resolves to
        nothing in the catalogue, so the predicate would skip the read and the refusal would
        leave no record at all. The customer still gets the coverage response either way, but
        "we were told Córdoba and refused it, for this reason, on this turn" is precisely the
        evidence finding G-11 exists to preserve. So a coverage-listed city counts as
        something to decide, and the boundary records its refusal.

        This is not a second copy of the catalogue rule: it is a different certified rule
        (coverage, not resolution), called from the one place the predicate is composed.
        """
        from .claim_projection import locality_mention_possible

        def _resolve(text):
            zone = self._match_zone_in_text(text)
            if zone is not None:
                return zone
            return True if _loc.is_outside_coverage(text) else None

        return locality_mention_possible(texts, resolve=_resolve)

    def _admission_locality_validator(self, value: str):
        """The R3 resolver, as the admission boundary's injected validator.

        Returns `(status, (zone_group, zone_detail) | None, detail)`. The four statuses are
        the four `LocalityVerdict` values, so admission reads the certified verdict and
        forms no opinion of its own about what a locality is: an unsupported name, a name
        that is only part of a longer one, and San Nicolás are refused HERE for the same
        reasons and by the same code that refuses them at the zone block.
        """
        from .evidence_admission import (VALIDATOR_AMBIGUOUS, VALIDATOR_OK,
                                         VALIDATOR_UNRESOLVED, VALIDATOR_UNSUPPORTED)
        text = str(value or "")
        if not text.strip():
            return VALIDATOR_UNRESOLVED, None, "empty value"
        # ── W1F-W-01 R2: a context check used to live HERE, and it was dead code ──────
        # Worth recording, because the reasoning that put it here was wrong in a way that
        # is easy to repeat. R1 validates a claim value in ISOLATION, and the L4.7W5-F5
        # article rule is about CONTEXT — `resolve("la boca")` succeeds where
        # `resolve("me mordí la boca")` correctly refuses — so an interpreter reading
        # INSPECTION_LOCATION = "la boca" out of that sentence IS admitted by R1 alone
        # (measured: it writes `('CABA','La Boca')`). R2 first re-validated the value
        # against the burst here to close that.
        #
        # Mutation M21 disabled this branch and SURVIVED, which was the right answer: the
        # branch can never fire. With R2 on, the R1-PL-01 predicate gates the semantic read
        # on `resolve(burst) is not None`, so by the time a claim exists at all the burst
        # has already resolved — and the only case the context check would refuse is
        # exactly the case where no claim was ever produced. **The predicate is
        # self-protecting**, the check was a second mechanism for a case the first one
        # already owned, and an unreachable guard misleads the next reader. It is gone;
        # `test_g06` now pins the mechanism that does the work.
        if self._locality_safety_on():
            verdict, zone, reason = self._locality_verdict(text)
            V = _loc.LocalityVerdict
            if verdict == V.UNSUPPORTED:
                return VALIDATOR_UNSUPPORTED, None, reason
            if verdict == V.AMBIGUOUS:
                return VALIDATOR_AMBIGUOUS, None, reason
            if verdict != V.RESOLVED or zone is None:
                return VALIDATOR_UNRESOLVED, None, reason
            return (VALIDATOR_OK,
                    (getattr(zone, "zone_group", None), getattr(zone, "zone_detail", None)),
                    reason)
        # Locality safety OFF: the inherited matcher is the only validator there is. The
        # coverage and ambiguity verdicts simply do not exist on that deployment, and
        # admission must not invent them — R3 is the milestone that adds them.
        zone = self._extract_zone_from_text(text)
        if zone is None:
            return VALIDATOR_UNRESOLVED, None, "no catalogue locality in this value"
        return (VALIDATOR_OK,
                (getattr(zone, "zone_group", None), getattr(zone, "zone_detail", None)),
                "catalogue locality")

    def _admit_turn_evidence(self, ctx: "_Context", state, texts) -> "object | None":
        """Decide this turn's locality evidence BEFORE the gate chain can answer.

        Two readings, one decision, in this order:

        A. the deterministic role-aware reader, run in its **write-only** mode
           (`allow_contradiction_return=False`), so it keeps LR-2 origin separation, LR-3
           explicit vehicle-location authority, the R3 coverage/ambiguity pre-gate and
           candidate isolation — and cannot send. The coverage response and the SC17
           contradiction reply are still produced by the zone block later in the turn, by
           the same code as today, because this run never consumes them.

        B. the semantic claims, at the typed admission boundary. They are written only when
           A established nothing: the customer's own words outrank a reading of them, which
           is the reconciler's ladder expressed as an order of operations.

        Returns the `AdmissionResult` (for the trace and the tests) or None when the
        boundary could not run. Never raises: a failure here degrades to the inherited
        behaviour, which is the whole turn exactly as it runs today.
        """
        from .evidence_admission import admit
        from .claim_projection import claims_from_turn_evidence

        burst_texts = [t for t in (texts or ()) if isinstance(t, str) and t]
        text = " ".join(burst_texts)
        cycle_id = self._reconciler_cycle_id(state)

        # ── A. the deterministic reading, written through the existing chokepoint ──
        before = self._get_active_inspection_location(ctx, state)
        if text:
            try:
                self._apply_zone_from_text(ctx, state, text,
                                           allow_contradiction_return=False)
            except Exception as exc:
                logger.warning("W1F-R1 admission zone read failed thread_id=%s: %s",
                               getattr(ctx.thread, "id", None), type(exc).__name__)
        established = self._get_active_inspection_location(ctx, state)
        wrote_this_turn = any(established) and established != before
        # LR-2 is the authority on ROLE. When the turn carries a customer-origin clause and
        # the role-aware reader still produced no inspection locality, it has DECIDED: the
        # place named belongs to the customer. A semantic role label may not overrule that.
        role_decided_by_text = bool(
            text and not wrote_this_turn and _has_customer_origin_clause(text))

        # ── B. the semantic reading, at the boundary ──────────────────────────────
        binding_ok = not self._semantic_binding_refused()
        # ── W1F-W-01 R1-PL-01 (owner decision OC-R1-R1, option c) ────────────────
        # The ONE shared predicate decides whether this turn has anything to wait FOR.
        # `_semantic_turn_evidence()` is a bounded wait of SEMANTIC_SAME_TURN_TIMEOUT_SECONDS
        # (6.0 s on the candidate deployment), and a burst whose own words contain no
        # catalogue locality cannot produce an admissible one — `INFERRED_NOT_EXPLICIT`
        # refuses every claim such a burst could yield, by name. So the turn does not wait.
        # It is a WAIT that is skipped, never a dispatch: the interpretation was started
        # above Layer A exactly as at the starting HEAD and still records in shadow.
        locality_possible = self._locality_possible_in_burst(burst_texts)
        semantic_wait_skipped = False
        evidence = None
        if binding_ok and (locality_possible or not self._r2_on()):
            try:
                evidence = self._semantic_turn_evidence()
            except Exception as exc:
                logger.warning("W1F-R1 admission semantic read unavailable: %s",
                               type(exc).__name__)
                evidence = None
        elif binding_ok:
            semantic_wait_skipped = True
            logger.info("W1F-R2 semantic wait skipped thread_id=%s — no catalogue locality "
                        "in the customer's own words this turn",
                        getattr(getattr(ctx, "thread", None), "id", None))
        self._turn_semantic_wait_skipped = semantic_wait_skipped
        revision_id = getattr(state, "current_revision_id", None)
        claims = (claims_from_turn_evidence(
            evidence, texts=burst_texts, cycle_id=cycle_id,
            revision_id=(revision_id if isinstance(revision_id, int) else None))
            if evidence is not None else [])

        burst = getattr(self, "_canonical_turn_burst", None)
        result = admit(
            claims,
            cycle_id=cycle_id,
            turn_id=getattr(self, "_correlation_id", None),
            burst_fingerprint=getattr(burst, "fingerprint", None),
            turn_message_ids=(tuple(str(m) for m in burst.ordered_message_ids)
                              if burst is not None else ()),
            locality_validator=self._admission_locality_validator,
            canonical_locality=(established if any(established) else None),
            canonical_from_this_turn=wrote_this_turn,
            role_decided_by_text=role_decided_by_text,
            semantic_binding_ok=binding_ok)

        # ── the admitted claims reach the ONE writer; refusals are recorded ────────
        for record in result.admitted:
            target = self._focus_candidate(ctx)
            if not self._is_current_focus(state, target):
                # No candidate yet, or only an unrelated mentioned one. The pre-candidate
                # buffer is written through the same chokepoint via an adapter, so the
                # reconciler decides here too (the location form's bypass of it is
                # W1FRV-04 and is not repeated).
                target = _ZoneBuffer(state)
            wrote = self._apply_inspection_zone(
                ctx, target, zone_group=record.zone_group, zone_detail=record.zone_detail,
                source="admission", state=state,
                claims=list(result.admitted_claims))
            self._decision_log(
                ctx, "evidence_admitted", claim=record.claim_type,
                outcome=record.outcome, reason=record.reason,
                zone_group=record.zone_group, zone_detail=record.zone_detail,
                producer=record.provenance.producer,
                evidence_class=record.provenance.evidence_class,
                turn=record.provenance.turn_id,
                messages=",".join(record.provenance.source_message_ids),
                target=("candidate" if not isinstance(target, _ZoneBuffer)
                        else "state_buffer"),
                written=bool(wrote))
            break           # one locality per turn; a second one is a question, not a write
        for record in result.refused:
            self._decision_log(
                ctx, "evidence_refused", claim=record.claim_type,
                outcome=record.outcome, reason=record.reason, detail=record.detail,
                producer=record.provenance.producer,
                evidence_class=record.provenance.evidence_class,
                turn=record.provenance.turn_id)
        # ── W1F-W-01 R2: the turn's decisions become DURABLE, then close what we asked ──
        # Order matters and is the review's §7.2 order: persist (step 3) before reconciling
        # the outstanding question (step 4), so the row that closes a question is already
        # on disk and `answered_by_evidence_id` can point at it.
        if self._r2_on():
            try:
                self._persist_turn_evidence(ctx, state, result)
            except Exception as exc:
                logger.warning("W1F-R2 evidence persistence failed thread_id=%s: %s",
                               getattr(getattr(ctx, "thread", None), "id", None),
                               type(exc).__name__)
            try:
                self._reconcile_outstanding_questions(ctx, state, result)
            except Exception as exc:
                logger.warning("W1F-R2 question reconciliation failed thread_id=%s: %s",
                               getattr(getattr(ctx, "thread", None), "id", None),
                               type(exc).__name__)
        self._turn_admission = result
        return result

    # ── W1F-W-01 R2 — durability and the outstanding-question contract ───────────

    def _persist_turn_evidence(self, ctx: "_Context", state, result) -> list:
        """Persist every admission decision for this turn, acceptance and refusal alike.

        Thin by design: `thread_evidence.persist_admission` owns the rows and the
        idempotency, this method owns only the identity of the thread and the cycle. The
        rows are additive history — they are never read as canonical truth, which is why a
        rollback to the flag-OFF behaviour needs no schema rollback.
        """
        from .thread_evidence import persist_admission
        rows = persist_admission(
            self.db, thread_id=ctx.thread.id,
            cycle_key=self._r2_cycle_key(state), result=result)
        self._turn_evidence_rows = rows
        for row in rows:
            self._decision_log(
                ctx, "evidence_persisted", claim=row.claim_type, role=row.role,
                outcome=row.outcome, reason=row.reason, evidence_id=row.id,
                normalized=row.normalized_value)
        return rows

    def _reconcile_outstanding_questions(self, ctx: "_Context", state, result) -> list:
        """Close the questions this turn's ADMITTED evidence answers. Returns the closed.

        Review §7.2 step 4. Three things happen here and they are deliberately separate:

        1. every live question is charged one customer turn, and one that has run out is
           ABANDONED explicitly — expiry is a written state, never an inference from a
           missing row;
        2. each remaining live question is matched against this turn's admitted records by
           `outstanding_question.match`, a pure rule over typed decisions. Nothing in that
           function can see model prose;
        3. a match closes the question by CONDITIONAL UPDATE, so a replay or a second
           worker produces one close and one refusal, and the typed routing fact below is
           set only for the question that was actually closed by THIS call.

        The routing fact is the narrow one the milestone specifies: a list of the questions
        this turn answered. It suppresses the Layer F step 9 greeting and nothing else.
        """
        from . import outstanding_question as OQ
        cycle = self._r2_cycle_key(state)
        thread_id = ctx.thread.id
        focus = self._focus_candidate(ctx)
        focus_id = getattr(focus, "id", None) if self._is_current_focus(state, focus) else None

        expired = OQ.observe_turn(self.db, thread_id=thread_id, cycle_key=cycle)
        for row in expired:
            self._decision_log(ctx, "outstanding_question_expired",
                               kind=row.question_kind, question_id=row.id,
                               turns=row.turns_observed)

        rows_by_claim = {r.claim_id: r for r in getattr(self, "_turn_evidence_rows", ()) or ()}
        closed: list = []
        for question in OQ.live(self.db, thread_id=thread_id, cycle_key=cycle):
            record, why = OQ.match(question, result.records, cycle_key=cycle,
                                   candidate_id=focus_id)
            if record is None:
                self._decision_log(ctx, "outstanding_question_unmatched",
                                   kind=question.question_kind, question_id=question.id,
                                   reason=why)
                continue
            claim_id = getattr(record.provenance, "claim_id", None)
            evidence_row = rows_by_claim.get(claim_id)
            if OQ.close_answered(self.db, question, claim_id=claim_id,
                                 evidence_id=getattr(evidence_row, "id", None)):
                closed.append(question)
                self._decision_log(
                    ctx, "outstanding_question_answered", kind=question.question_kind,
                    question_id=question.id, claim=str(record.claim_type),
                    claim_id=claim_id, asked_by=question.asked_by)
            else:
                # Lost the race, or this inbound message is a replay. Either way the
                # question is settled and this turn does not get to claim the closure.
                self._decision_log(ctx, "outstanding_question_already_closed",
                                   kind=question.question_kind, question_id=question.id)
        self._turn_answered_questions = tuple(closed)
        return closed

    def _record_delivered_questions(self, ctx: "_Context", *, text: str,
                                    outbound_message_id=None,
                                    wa_message_id=None) -> tuple:
        """Record the question(s) the DELIVERED message actually asked. Returns the rows.

        Two provenances, because the system asks in two voices and the review's §8.1
        `asked_by` column exists to tell them apart:

        `DETERMINISTIC_TEMPLATE` — the clarification producer's typed proposition reached
            the composed message. `plan.included()` answers that STRUCTURALLY, from the same
            `_ordered_included()` set `compose()` rendered; the G3-7F owner resolution
            forbids proving inclusion by searching customer prose and this does not.

        `REPLY_MODEL` — the reply model's own sentence asked it. On the Wild's turn 1 there
            was no proposition at all to include: the clarification producer requires a
            focus candidate with a `tipo_vehiculo` and turn 1 had no candidate, so the
            location question came purely from the model's prose. `_reply_already_asks_location`
            is the EXISTING certified probe the same producer already uses to avoid asking
            twice — reused, not copied, so the two cannot disagree about what counts as
            asking. Reading our OWN delivered sentence is not treating model prose as
            authority over the customer: it is observing what we sent. No claim about the
            customer is derived from it, and closing the question still requires R1's
            admitted evidence.

        Nothing here can ask a question or change canonical state; it writes one row.
        """
        if not self._r2_on() or not wa_message_id:
            return ()
        from . import outstanding_question as OQ
        state = getattr(ctx, "state", None)
        if state is None:
            return ()
        plan = getattr(self, "_live_plan", None)
        asked_by = None
        if plan is not None and plan.included(
                PropositionId.clarification("inspection_location")):
            asked_by = OQ.OQ_ASKED_BY_DETERMINISTIC_TEMPLATE
        elif _reply_already_asks_location(text or ""):
            asked_by = OQ.OQ_ASKED_BY_REPLY_MODEL
        if asked_by is None:
            return ()
        focus = self._focus_candidate(ctx)
        focus_id = (getattr(focus, "id", None)
                    if self._is_current_focus(state, focus) else None)
        row, created = OQ.record_asked(
            self.db, thread_id=ctx.thread.id, cycle_key=self._r2_cycle_key(state),
            kind=OQ.OQ_KIND_INSPECTION_LOCATION, asked_by=asked_by,
            wa_message_id=wa_message_id, outbound_message_id=outbound_message_id,
            asked_turn_id=getattr(self, "_correlation_id", None), candidate_id=focus_id)
        if row is not None:
            # The race the owner named: a status callback can land between `mark_sent`'s
            # commit and this row existing. The ledger is the rendezvous — see
            # `reconcile_delivery` — so a callback that arrived early is applied here and
            # nothing is lost.
            try:
                OQ.reconcile_delivery(self.db, row)
            except Exception as exc:
                logger.warning("W1F-R2 delivery reconciliation failed wamid=%s: %s",
                               wa_message_id, type(exc).__name__)
            self._turn_question_recorded = (row,)
            self._decision_log(
                ctx, "outstanding_question_asked", kind=row.question_kind,
                question_id=row.id, asked_by=row.asked_by, created=bool(created),
                candidate_id=focus_id, status=row.status,
                delivery_status=row.delivery_status)
        return (row,) if row is not None else ()

    def _bind_evidence_to_candidate(self, ctx: "_Context", state, candidate) -> bool:
        """Bind this cycle's unbound locality evidence to a newly created candidate.

        Returns whether a row was bound. Every refusal is a column comparison inside
        `thread_evidence.bindable_locality` — wrong thread, wrong cycle, wrong role, not
        accepted, superseded, already bound — so none of RISK-03's three unknowables is
        assumed here.

        CANDIDATE ISOLATION IS ENFORCED HERE, NOT BY THE CALLER. It was the caller's job in
        the first draft, and mutation M07 — which deleted the caller's `current_focus`
        condition — SURVIVED: a `mentioned` MOTO from an earlier handoff would have
        inherited the car's locality, which is exactly the M21.2.14 defect. A rule that
        holds only while every call site remembers it is the W1FRV-07 shape, so it lives at
        the one place that performs the write, and `_is_current_focus` is the same predicate
        admission and the location form use.
        """
        if candidate is None or candidate.zone_group or candidate.zone_detail:
            return False
        if not self._r2_on():
            # THE INTERLOCK LIVES HERE TOO, for the same reason the isolation rule does.
            # Mutation M34 removed it from a CALL SITE and survived, because the site it
            # removed it from is not the one the Wild's turn 3 uses — there are two creation
            # sites, and a guard at each is one rule with two homes (W1FRV-07). It matters
            # operationally rather than theoretically: after a behavioural rollback (flags
            # OFF, same image, stale evidence rows still in the table) a binding that ran
            # anyway would consume a leftover row and write a zone — exactly what §14 of the
            # closeout promises cannot happen. `test_e04d` is that scenario.
            return False
        if not self._is_current_focus(state, candidate):
            self._decision_log(ctx, "evidence_binding_refused",
                               candidate_id=getattr(candidate, "id", None),
                               reason="NOT_CURRENT_FOCUS")
            return False
        try:
            from .thread_evidence import bind as _bind, bindable_locality
            row = bindable_locality(self.db, thread_id=ctx.thread.id,
                                    cycle_key=self._r2_cycle_key(state))
        except Exception as exc:
            logger.warning("W1F-R2 candidate evidence lookup failed thread_id=%s: %s",
                           ctx.thread.id, type(exc).__name__)
            return False
        if row is None:
            return False
        group, detail = row.zone_pair()
        if not group and not detail:
            return False
        wrote = self._apply_inspection_zone(
            ctx, candidate, zone_group=group, zone_detail=detail,
            source="evidence_binding", state=state)
        bound = _bind(self.db, row, candidate.id)
        if bound:
            candidate.zone_evidence_id = row.id
            self.db.flush()
        self._decision_log(
            ctx, "evidence_bound_to_candidate", candidate_id=candidate.id,
            evidence_id=row.id, zone_group=group, zone_detail=detail,
            cycle=row.cycle_key, turn=row.turn_id, written=bool(wrote), bound=bool(bound))
        return bool(bound)

    def _apply_form_location(self, ctx: "_Context", state, *, zone_group,
                             zone_detail) -> bool:
        """Write a Location-Flow answer through the reconciler, as HUMAN_CONFIRMED.

        W1FRV-04. One call into the C2 chokepoint, with a typed claim whose evidence class
        says what it is: the customer filled in a form we sent, which is stronger than
        anything a producer can infer and is ranked as such by
        `reconcile_inspection_location`'s ladder — not by being written last.

        Returns whether the chokepoint wrote. It assigns no field itself; a structural test
        asserts that.
        """
        from .evidence_admission import ADMITTED_CLAIM_TYPES  # noqa: F401  (contract anchor)
        from ..schemas.claims import (ClaimEvidence, ClaimType, EvidenceClass,
                                      Explicitness, Polarity)
        from ..schemas.turn_evidence import EvidenceStatus
        burst = getattr(self, "_canonical_turn_burst", None)
        claim = ClaimEvidence(
            claim_type=ClaimType.INSPECTION_LOCATION,
            value=(zone_detail or zone_group),
            evidence_class=EvidenceClass.HUMAN_CONFIRMED,
            status=EvidenceStatus.CONFIRMED,
            polarity=Polarity.ASSERTED,
            producer="flow:location_fallback",
            producer_version="1.0",
            explicitness=Explicitness.STATED,
            cycle_id=self._reconciler_cycle_id(state),
            source_message_ids=(tuple(str(m) for m in burst.ordered_message_ids)
                                if burst is not None else ())).with_id()
        focus = self._focus_candidate(ctx)
        target = focus if self._is_current_focus(state, focus) else _ZoneBuffer(state)
        wrote = self._apply_inspection_zone(
            ctx, target, zone_group=zone_group, zone_detail=zone_detail,
            source="location_form", state=state, claims=[claim])
        self._decision_log(
            ctx, "location_form_reconciled", zone_group=zone_group,
            zone_detail=zone_detail, evidence_class=EvidenceClass.HUMAN_CONFIRMED.value,
            target=("candidate" if not isinstance(target, _ZoneBuffer) else "state_buffer"),
            written=bool(wrote))
        return bool(wrote)

    def _answered_question_kinds(self) -> frozenset:
        """The typed routing fact: which question kinds THIS turn answered.

        Read by exactly one gate (Layer F step 9) and by the trace. It is a set of kinds,
        not a boolean, so "the customer answered the location question" can never be read
        as "the customer answered whatever we are about to ask".
        """
        return frozenset(q.question_kind
                         for q in getattr(self, "_turn_answered_questions", ()) or ())

    def _locality_safety_on(self) -> bool:
        """Is locality-resolution safety armed on this deployment?

        Default OFF when the attribute is absent, which is the deployed `6a14cdb` shape.
        """
        return getattr(getattr(self, "settings", None),
                       "locality_resolution_safety_enabled", False) is True

    def _matched_forms_in_text(self, normalized_text: str, zone) -> list[str]:
        """Which of this zone's names actually occur, as whole tokens, in the text.

        The matcher below can fire from the canonical name, from the article-stripped
        alias, or from the zone group; the compact and ASR-fuzzy passes fire from no
        literal occurrence at all. Returning the forms that are really present keeps the
        composite-name validator honest: it can only refuse a form the customer wrote.
        """
        import re as _re
        forms: list[str] = []
        for raw in (getattr(zone, "zone_detail", None), getattr(zone, "zone_group", None)):
            if not raw:
                continue
            norm = _loc.norm_locality_text(raw)
            candidates = [norm]
            stripped = _loc.ARTICLE_PREFIX_RE.sub("", norm)
            if stripped and stripped != norm:
                candidates.append(stripped)
            for cand in candidates:
                if cand and cand not in forms and _re.search(
                        rf"\b{_re.escape(cand)}\b", normalized_text):
                    forms.append(cand)
        return forms

    def _locality_verdict(self, text: str, zone=None, *, matched: bool = False,
                          area_hint: str | None = None):
        """(verdict, zone, reason) for a locality reading. Writes nothing, asks nothing.

        `area_hint` is an explicit coverage-area selection the customer made in the same
        submission — the Location Flow's `zona_general` dropdown. It can only ever settle an
        ambiguity, never create one, and it settles it only when it agrees with the matched
        locality's own area (see `locality_text.disambiguated_by_text`).

        The order is the point. Coverage is decided FIRST, before a zone can be produced at
        all, because the W1F-W-01 review measured the opposite order in the deployed code:
        `_apply_zone_from_text` wrote `('Sur','La Plata')` for "en mar del plata" and the
        out-of-coverage response was only produced afterwards, by the routing gate, leaving
        a false zone on the thread (finding W1FRV-03).
        """
        V = _loc.LocalityVerdict
        if _loc.is_outside_coverage(text):
            return V.UNSUPPORTED, None, "declared outside the coverage area"
        if not matched:
            zone = self._match_zone_in_text(text)
        if zone is None:
            return V.UNRESOLVED, None, "no catalogue locality in this text"

        normalized = _loc.norm_locality_text(text)
        catalog = tuple(
            z.zone_detail for z in (getattr(self, "_zone_catalog_snapshot", None) or ())
            if getattr(z, "zone_detail", None))
        forms = self._matched_forms_in_text(normalized, zone)
        if forms and all(_loc.composite_name_conflict(normalized, f, catalog) for f in forms):
            return (V.UNRESOLVED, None,
                    f"only part of a longer name: {'/'.join(forms)}")

        detail = getattr(zone, "zone_detail", None)
        if (detail and _loc.is_cross_coverage_ambiguous(detail)
                and not _loc.disambiguated_by_text(
                    normalized, getattr(zone, "zone_group", None), area_hint=area_hint)):
            return V.AMBIGUOUS, None, _loc.ambiguity_reason(detail) or "ambiguous locality"
        return V.RESOLVED, zone, "catalogue locality"

    def _extract_zone_from_text(self, text: str) -> "ViaticosZone | None":
        """Resolve a locality from text, or None.

        With locality safety OFF this is the inherited matcher, unchanged. With it ON the
        matcher's answer has to survive the three validators in `_locality_verdict` before
        it can be returned, so an unsupported, ambiguous or merely partial name resolves to
        None — and a caller that writes only what it is given therefore writes nothing.
        """
        zone = self._match_zone_in_text(text)
        if not self._locality_safety_on():
            return zone
        verdict, safe_zone, reason = self._locality_verdict(text, zone, matched=True)
        if verdict != _loc.LocalityVerdict.RESOLVED:
            logger.info("W1F-R3 locality refused verdict=%s reason=%s", verdict, reason)
            return None
        return safe_zone

    def _match_zone_in_text(self, text: str) -> "ViaticosZone | None":
        """The inherited matcher: Dock Sud, CABA synonyms, article context, containment,
        compact/ASR-fuzzy, then bare group. Unchanged by R3 except for the catalogue
        snapshot it leaves behind, which the validators read instead of querying again."""
        from sqlalchemy import select as _select

        # Normalize: strip Unicode diacritics, lowercase, collapse whitespace.
        # Applied to both the customer text and every DB/alias value compared below
        # so that "Benavídez" matches the stored "Benavidez" (and vice-versa).
        def _n(s: str) -> str:
            return " ".join(_strip_accents(s).split())

        normalized_text = _n(text)
        zones = list(self.db.execute(_select(ViaticosZone)).scalars().all())
        self._zone_catalog_snapshot = zones      # W1F-W-01 R3: read-only, same query

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

        # ── L4.7W5-F5: localities whose name carries a grammatical article ──────────
        #
        # Two live failures, opposite directions, one cause: an article-prefixed locality
        # is a common noun wearing a proper name.
        #
        #   "quiero revisar un auto en paternal"  -> None          (customer dropped "La")
        #   "me mordí la boca"                    -> ('CABA','La Boca')   (pre-existing)
        #
        # Plain containment cannot separate those. The article itself is the signal that
        # the name doubles as ordinary vocabulary, so for exactly these names a locative
        # context is required — a preposition or zone word, or the locality standing alone
        # as the whole answer. Proper-noun localities (Palermo, Berazategui, Villa Urquiza)
        # are untouched and keep plain containment: they do not occur by accident.
        #
        # The bare alias is derived from the CANONICAL catalog entry, never by editing the
        # customer's words, and an alias shared by two localities resolves to neither.
        article_zones = [z for z in zones_sorted
                         if z.zone_detail and _ARTICLE_PREFIX_RE.match(_n(z.zone_detail))]
        alias_owners: dict[str, list] = {}
        for zone in article_zones:
            alias_owners.setdefault(_ARTICLE_PREFIX_RE.sub("", _n(zone.zone_detail)), []).append(zone)

        for zone in article_zones:
            canonical = _n(zone.zone_detail)
            alias = _ARTICLE_PREFIX_RE.sub("", canonical)
            forms = [canonical]
            if len(alias_owners.get(alias, [])) == 1:      # ambiguous alias resolves to neither
                forms.append(alias)
            for form in forms:
                if _locality_in_context(normalized_text, form):
                    return zone

        for zone in zones_sorted:
            if not zone.zone_detail:
                continue
            zone_norm = _n(zone.zone_detail)
            if _ARTICLE_PREFIX_RE.match(zone_norm):
                continue                                    # handled above, with context
            if zone_norm in normalized_text:
                return zone

        # ── M21.2.15: compact-form and ASR-tolerant fuzzy match ──────────────────
        # Handles Whisper word-boundary merge artifacts (e.g. "Villurquiza" for
        # "Villa Urquiza") and app-side typos like "VillaUrquiza".
        # Engaged only after the exact-substring pass above finds nothing.
        #
        # Safety constraints:
        #   - minimum compact input length ≥ 6  (rejects bare "san", "villa", "norte")
        #   - compact-exact wins before any score comparison
        #   - fuzzy ratio threshold ≥ 0.85
        #   - ambiguity gap ≥ 0.20 vs runner-up  (rejects La Matanza Este/Oeste, etc.)
        _compact_input = re.sub(r"\s+", "", _strip_accents(text))
        if len(_compact_input) >= 6:
            # Compact-exact: remove all whitespace from the canonical zone_detail
            # and compare.  Handles "VillaUrquiza" → "villaurquiza".
            for zone in zones_sorted:
                if not zone.zone_detail:
                    continue
                if re.sub(r"\s+", "", _n(zone.zone_detail)) == _compact_input:
                    return zone

            # ASR-fuzzy: SequenceMatcher ratio on compact forms.
            # Handles 1-char deletion/substitution like "villurquiza" (11)
            # vs canonical compact "villaurquiza" (12): ratio ≈ 0.957.
            from difflib import SequenceMatcher as _SM
            _FUZZY_MIN = 0.85   # minimum similarity to accept a match
            _GAP_MIN   = 0.20   # minimum lead over runner-up (anti-ambiguity)
            _best_r    = 0.0
            _best_zone = None
            _second_r  = 0.0
            for zone in zones_sorted:
                if not zone.zone_detail:
                    continue
                _zc = re.sub(r"\s+", "", _n(zone.zone_detail))
                if len(_zc) < 6:
                    continue
                _r = _SM(None, _compact_input, _zc).ratio()
                if _r > _best_r:
                    _second_r = _best_r
                    _best_r   = _r
                    _best_zone = zone
                elif _r > _second_r:
                    _second_r = _r
            if (
                _best_r >= _FUZZY_MIN
                and (_best_r - _second_r) >= _GAP_MIN
                and _best_zone is not None
            ):
                logger.info(
                    "M21.2.15 ASR-fuzzy zone %r → %r ratio=%.3f gap=%.3f",
                    text, _best_zone.zone_detail, _best_r, _best_r - _second_r,
                )
                return _best_zone

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

    def _get_active_inspection_location(
        self, ctx: _Context, state: "WhatsAppThreadState | None"
    ) -> tuple[str | None, str | None]:
        """Return (zone_group, zone_detail) for the active inspection candidate.

        F4: current_focus candidate zone is authoritative when the candidate
        carries any location data. state.home_zone_* is a revision-scoped
        fallback used only when the candidate has no location at all.

        Precedence (mirrors _compute_price_quote LR-1):
          1. current_focus candidate zone — authoritative per-candidate location
          2. state.home_zone_* — fallback when candidate has no zone data
          3. (None, None) — no location available anywhere

        FIX 9 (quote protection) note: the state fallback is safe because
        _execute_cycle_reset clears state.home_zone_* on every cycle boundary.
        After a proper reset, state zone is None for a fresh cycle, so a new
        candidate with zone=None correctly yields (None, None) here, causing
        F5.1 to append the location question.  Threads that never reset may
        still see a stale state fallback (pre-existing limitation; requires
        cycle reset to resolve).

        Never call state.home_zone_detail directly for customer-facing output;
        use this accessor instead so pricing and display always agree.
        """
        return self._inspection_location_for(self._focus_candidate(ctx), state)

    @staticmethod
    def _inspection_location_for(candidate, state) -> tuple[str | None, str | None]:
        """`_get_active_inspection_location`'s precedence for a GIVEN candidate.

        Convergence Phase 3 (P2-04): a booking is priced at the location of the candidate
        its offer is bound to, so the booking path asks this for the offer-bound candidate
        rather than for `_focus_candidate` — the two differ exactly when the focus pointer is
        NULL and another candidate is in focus."""
        if candidate:
            cand_grp = candidate.zone_group or None
            cand_det = candidate.zone_detail or None
            if cand_grp or cand_det:
                # Candidate has at least one location field — candidate is authoritative.
                # Fill the missing field from state so partial data still resolves.
                return (
                    cand_grp or (state.home_zone_group if state else None),
                    cand_det or (state.home_zone_detail if state else None),
                )
        if state:
            return state.home_zone_group, state.home_zone_detail
        return None, None

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
            self._turn_price_quote = q      # L4.7D: canonical amount for response validation
            return q
        except PricingNotFoundError:
            return None

    # ── WhatsApp sends ────────────────────────────────────────────────────


    # ── G3-6 — the response-proposition producers ────────────────────────────────
    #
    # Everything below used to happen AFTER the message was composed, as three passes that
    # inspected finished prose and appended to it, stripped from it, or replaced parts of
    # it. They ran inside `_send_text_to_wa`, so the plan that claimed to describe the turn
    # could not see them and reported one item for a message carrying three claims — the
    # falsehood the G3-5F review recorded as G5F-A02 — while the hours pass was DEF-04
    # itself: substring surgery on words the system did not author.
    #
    # They are producers now. Each REGISTERS a typed proposition with a stable identity and
    # its canonical owner as renderer; the plan decides inclusion, order and suppression;
    # and nothing appends anything once the text exists.

    def _turn_owes_faq_answer(self) -> bool:
        """Does this burst still owe the customer a canonical FAQ answer?

        G3-6. The scheduling path asks this to decide whether it may replace a composed
        text reply with a Flow — the Flow body cannot carry an FAQ answer, and dropping
        "¿aceptan débito?" to save a round trip is a bad trade. It used to read the raw
        side-channel string; it now asks a named question, so the meaning is in the name
        rather than in the truthiness of a variable.
        """
        return bool(getattr(self, "_turn_faq_burst", None))

    #: G3-7 (audit BC36-A03). Which producers own a REQUIRED proposition.
    #:
    #: The distinction is about what the customer asked for. A canonical FAQ answer and an
    #: authorized price are things they will read the message expecting to find; a message
    #: that quietly lacks one reads as a complete answer and is not. The vehicle
    #: reconciliation is required for the opposite reason: without it the reply may speak of
    #: a vehicle canonical state does not hold. The clarification is OPTIONAL — losing it
    #: costs a round trip, not a falsehood.
    _RESPONSE_PRODUCERS: tuple = (
        ("price", "_register_price_proposition", True),
        ("faq", "_register_faq_propositions", True),
        ("vehicle", "_register_canonical_vehicle_proposition", True),
        ("clarification", "_register_clarification_proposition", False),
        #: G3-7F-R2 (review G37FRR-01/02). REQUIRED: a correction that moved canonical state
        #: and said nothing, or a confirmation question that was quietly dropped, are both
        #: worse than a bounded fallback line. Failing here marks the turn degraded.
        ("correction", "_register_correction_propositions", True),
    )

    def _register_response_propositions(self, ctx, text: str) -> None:
        """Register every proposition this turn owes the customer beside the primary reply.

        Called once, at the dispatch boundary, before composition. It reads canonical state
        and the burst; it writes only to the plan — with one deliberate exception, the
        pending-vehicle arming in the vehicle producer, which is a canonical decision the
        L4.6 gate has always made and which belongs to the PRODUCER, never to a renderer.

        G3-7 (audit BC36-A03). Every producer used to share one `except Exception`, so a
        broken producer silently dropped its proposition and the customer received a message
        that looked complete and was not. Failures are now recorded on the plan, and a
        REQUIRED producer's failure marks the turn degraded — the composition boundary then
        refuses to send a partial answer. One producer's defect still cannot take down the
        others, and no internal error reaches the customer.
        """
        plan = getattr(self, "_live_plan", None)
        if plan is None:
            return
        for name, method, required in self._RESPONSE_PRODUCERS:
            try:
                getattr(self, method)(ctx, text)
            except Exception as exc:
                plan.producer_failures.append((name, type(exc).__name__))
                if required:
                    plan.degraded = True
                    logger.error(
                        "G3-7 REQUIRED PRODUCER FAILED thread_id=%s producer=%s %s: %s",
                        getattr(getattr(ctx, "thread", None), "id", None), name,
                        type(exc).__name__, exc, exc_info=True)
                else:
                    logger.warning(
                        "G3-7 optional producer failed thread_id=%s producer=%s %s: %s",
                        getattr(getattr(ctx, "thread", None), "id", None), name,
                        type(exc).__name__, exc, exc_info=True)

    # ── 0. canonical price ───────────────────────────────────────────────────────

    def _register_price_proposition(self, ctx, text: str) -> None:
        """The price the pricing service authorized for this turn, as its own proposition.

        G3-6-BOUNDED-CORRECTION (audit G36-03). G3-6 made an opaque primary reply
        rejectable when it contradicts canonical truth, which is right — but a reply that
        says "La revisión sale $85.000. Trabajamos sábados de 9 a 13 hs." carries a verified
        business fact beside the falsehood, and rejecting the sentence took the price with
        it. The customer asked what the revision costs; the answer belongs to the pricing
        service, not to the sentence that happened to contain it.

        AUTHORIZATION IS TYPED, AND IT IS THE ONLY SOURCE.
        Two conditions, both canonical, neither derived from the words:

            `_turn_price_quote`             the pricing service computed and stored a quote
                                            for this turn (set inside `_compute_price_quote`,
                                            from vehicle type and zone)
            `_answer_source == PRICING_SERVICE`
                                            the handler that owns this turn declared the
                                            pricing service as the component answering it

        Nothing here reads a number out of prose, matches a phrase, or decides that a price
        was "requested". If the pricing path did not authorize a quote, no proposition is
        registered and no price is invented — which is also what happens when a model
        invents one: it is rejected with the primary and never resurrected.

        THE RENDERER COMPUTES NOTHING. The customer-facing sentence is built HERE, once,
        by `_build_quote_reply` from the canonical quote, and the callable returns that
        finished string. A renderer that could compute a price would be a second pricing
        authority.

        REDUNDANT BY DEFAULT. On an ordinary quote turn the primary reply IS the quote, so
        the proposition is registered redundant: it stays visible in the trace and the
        customer is not told the price twice. It becomes included only when the primary is
        withheld — the same restoration G3-6 already applies to every claim whose redundancy
        came from prose that is no longer being sent.
        """
        plan = getattr(self, "_live_plan", None)
        if plan is None:
            return
        quote = getattr(self, "_turn_price_quote", None)
        if quote is None:
            return                                  # the pricing path authorized nothing
        if getattr(self, "_answer_source", None) != "PRICING_SERVICE":
            return                                  # this turn is not delivering the quote
        total = getattr(quote, "precio_total", None)
        if not total:
            return
        state = getattr(ctx, "state", None)
        focus = self._focus_candidate(ctx)
        _, detail = self._get_active_inspection_location(ctx, state)
        rendered = self._build_quote_reply(
            (focus.marca or "").strip() or None if focus else None,
            (focus.modelo or "").strip() or None if focus else None,
            detail,
            int(total),
            focus.anio if focus else None)
        plan.register(Proposition(
            proposition_id=PropositionId.CANONICAL_PRICE,
            kind=ResponseItemKind.CANONICAL_FACT, claim_family="pricing",
            render=lambda: rendered,
            decision_authority=DecisionAuthority.PRICING_SERVICE,
            fact_sources=(FactSource.PRICING_SERVICE, FactSource.CANONICAL_STATE),
            renderer_source=RendererSource.DETERMINISTIC_TEMPLATE,
            policy=ResponsePolicy.P6_NO_ROUTINE_FACT_REPETITION, priority=30,
            # G3-7 (audit BC36-A01). The producer no longer ASSERTS that the primary
            # already said the price — it declares the canonical value it exists to
            # deliver, and the plan decides. On an ordinary quote turn the primary carries
            # the amount and the item is withheld ALREADY_STATED; on a turn whose primary
            # is empty, is a clarification, or was rejected for conflict, the amount is not
            # going out and the item is INCLUDED. The customer cannot lose the only copy.
            delivers_value=_format_price_ars(int(total)),
            locked_values=(("pricing.precio_total", str(int(total))),)))
        self._note_fact_source(FactSource.PRICING_SERVICE)

    # ── 1. FAQ ───────────────────────────────────────────────────────────────────

    def _register_faq_propositions(self, ctx, text: str) -> None:
        """One registered proposition per FAQ topic the burst raised.

        Replaces `_compose_secondary_answers`. The three judgements it used to make are all
        still made — they were never the problem — but each now produces a typed outcome
        instead of an edit:

            the prose already states the fact correctly  ->  redundant (P6, ALREADY_STATED)
            the prose states the fact WRONGLY            ->  the PRIMARY conflicts (DEF-04)
            the prose does not state it                  ->  the canonical answer is included

        The renderers are `_FAQ_TOPIC_ANSWERS`, which read their own canonical owners —
        `schedule.business_hours_for_weekday` for hours — so no consequential value is ever
        model-authored.
        """
        burst = getattr(self, "_turn_faq_burst", None)
        if not burst:
            return
        self._turn_faq_burst = None                  # consume — once per turn
        plan = self._live_plan
        if plan.human_owns_turn:                      # P2 owns the turn; nothing is added
            return
        topics = self._faq_topics_for_burst(burst, ctx=ctx)
        normalized = _norm_lower(text or "")
        state = getattr(ctx, "state", None)
        protected = _protected_slot_minutes(state)

        for topic in _FAQ_TOPIC_ANSWERS:              # stable order, not set order
            if topic not in topics:
                continue
            renderer = _FAQ_TOPIC_ANSWERS[topic]
            if topic == "business_hours":
                conflicts, rescued = _hours_conflict_in(text or "", protected)
                satisfied = _hours_prose_satisfies(normalized)
                if conflicts:
                    # DEF-04: the prose asserts hours it cannot prove. The whole primary
                    # proposition is rejected below; the canonical answer stands in its
                    # place, and any appointment time the rejected prose carried is
                    # re-registered from STATE, never recovered from the prose.
                    self._mark_primary_conflict(ctx, topic, rescued, protected)
                    satisfied = False
                self._register_faq_topic(topic, renderer, redundant=satisfied)
                continue
            if self._contradicts_canonical(topic, normalized):
                self._mark_primary_conflict(ctx, topic, (), protected)
                self._register_faq_topic(topic, renderer, redundant=False)
                continue
            self._register_faq_topic(
                topic, renderer,
                redundant=self._satisfies_canonical(topic, normalized))

    def _register_faq_topic(self, topic: str, renderer, *, redundant: bool) -> None:
        self._live_plan.register(Proposition(
            proposition_id=PropositionId.faq(topic), kind=ResponseItemKind.FAQ_ANSWER,
            claim_family="faq", render=renderer,
            decision_authority=DecisionAuthority.DETERMINISTIC_RULE,
            fact_sources=(FactSource.FAQ_CANONICAL,),
            renderer_source=RendererSource.DETERMINISTIC_TEMPLATE,
            policy=ResponsePolicy.P1_HOLD_PLUS_SAFE_FAQ, priority=50,
            redundant=redundant, locked_values=((f"faq.{topic}", "canonical"),)))
        if not redundant:
            self._note_fact_source(FactSource.FAQ_CANONICAL)
            # `contributing_sources` is the legacy observability field every existing
            # consumer reads; a canonical FAQ answer that reaches the customer still
            # contributes, exactly as it did when the appending pass set this.
            self._contributing_sources = ["FAQ_RULE"]

    def _mark_primary_conflict(self, ctx, topic: str, rescued, protected) -> None:
        """DEF-04. Reject the primary proposition WHOLE, and rescue what state can prove."""
        plan = self._live_plan
        primary = next((p for p in plan.propositions
                        if p.proposition_id == PropositionId.PRIMARY_REPLY), None)
        if primary is None:
            primary = Proposition(
                proposition_id=PropositionId.PRIMARY_REPLY, kind=ResponseItemKind.TEXT,
                claim_family="turn", priority=10,
                coexistence=CoexistencePolicy.REQUIRED,
                policy=ResponsePolicy.P3_SINGLE_MESSAGE_DEFAULT)
            plan.propositions.insert(0, primary)
        primary.conflicts_with_canonical = True
        primary.locked_values = tuple(dict.fromkeys(
            tuple(primary.locked_values) + ((f"faq.{topic}", "canonical"),)))
        logger.warning(
            "G3-6 DEF-04 PRIMARY REJECTED thread_id=%s topic=%s "
            "reason=prose_contradicted_canonical_truth",
            getattr(getattr(ctx, "thread", None), "id", None), topic)
        if rescued:
            # The offer is re-stated from `last_visible_slots` / `last_offered_slots`, which
            # is why it may survive a rejected reply: the thread really holds these times.
            minutes = tuple(m for m in rescued if m in protected)
            if minutes:
                plan.register(Proposition(
                    proposition_id=PropositionId.SLOT_OFFER,
                    kind=ResponseItemKind.SLOT_OFFER, claim_family="scheduling",
                    render=lambda: _regenerated_offer_es(minutes),
                    decision_authority=DecisionAuthority.SCHEDULING_SERVICE,
                    fact_sources=(FactSource.CANONICAL_STATE,),
                    renderer_source=RendererSource.DETERMINISTIC_TEMPLATE,
                    priority=40, locked_values=(("scheduling.slots", "canonical_state"),)))

    # ── 2. required next information ─────────────────────────────────────────────

    def _register_clarification_proposition(self, ctx, text: str) -> None:
        """The one required-next-information question, as a typed proposition.

        Replaces `_apply_required_next_question`. Every gate condition is unchanged — it is
        the same deterministic judgement about the same canonical state — but the outcome is
        a `CLARIFICATION` item keyed by the FIELD it is missing, so two producers asking for
        the same field are one proposition and a field canonical state already holds is
        never asked for at all.
        """
        state = getattr(ctx, "state", None)
        if state is None or getattr(state, "needs_human", False):
            return
        if state.last_stage not in (STAGE_QUALIFYING, None):
            return
        focus = self._focus_candidate(ctx)
        if focus is None or not focus.tipo_vehiculo:
            return
        zone_grp, zone_det = self._get_active_inspection_location(ctx, state)
        if zone_grp or zone_det:
            return                                   # P6: canonical state already has it
        turn_text = getattr(self, "_turn_clarification_burst", None)
        if turn_text and _is_general_faq_or_soft_close(turn_text):
            if not self._detect_explicit_inspection_request(_norm_lower(turn_text)):
                return
        already = _reply_already_asks_location(text or "")
        self._live_plan.register(Proposition(
            proposition_id=PropositionId.clarification("inspection_location"),
            kind=ResponseItemKind.CLARIFICATION, claim_family="clarification",
            render=lambda: _CANONICAL_LOCATION_ASK.strip(),
            decision_authority=DecisionAuthority.DETERMINISTIC_RULE,
            fact_sources=(FactSource.CANONICAL_STATE,),
            renderer_source=RendererSource.DETERMINISTIC_TEMPLATE,
            policy=ResponsePolicy.P1_HOLD_PLUS_SAFE_FAQ, priority=70,
            redundant=already,
            locked_values=(("clarification.field", "inspection_location"),)))
        if not already:
            logger.info("G3-6 clarification planned thread_id=%s field=inspection_location "
                        "candidate=%s", ctx.thread.id, focus.id)

    # ── 3b. corrections (G3-7F-R2) ───────────────────────────────────────────────

    def _register_correction_propositions(self, ctx, text: str) -> None:
        """The two correction propositions, rendered by the COMPOSER.

        G3-7F-R2, review findings G37FRR-01 and G37FRR-02. G3-7F built both of these as
        typed values and then never consumed either: `_turn_correction_echo` and
        `_turn_correction_proposition` were assigned and read by nothing, while the
        confirmation question was worded by a static method on the engine and handed straight
        to transport. The authority that decides WHAT changed was also choosing the WORDS,
        and the typed layer it was supposed to go through was dead scaffolding.

        Here the reconciler's typed field/value pairs are all that crosses the boundary.
        `render_correction_applied` and `render_correction_confirmation` live in
        `response_composition` and own every word.

        Both propositions are REQUIRED. If the pairs cannot be rendered — an unknown field,
        a blank value — this raises `RequiredPropositionUnavailable` and the composition
        boundary sends the bounded deterministic line. It never drops the claim silently:
        Tier 1 has already written canonical state, and a confirmation the customer never
        saw would otherwise become authority for a mutation they never agreed to.
        """
        from .response_composition import (RequiredPropositionUnavailable,
                                           render_correction_applied,
                                           render_correction_confirmation)
        plan = getattr(self, "_live_plan", None)
        if plan is None:
            return

        echo = tuple(getattr(self, "_turn_correction_echo", ()) or ())
        if echo:
            rendered = render_correction_applied(echo)
            if not rendered:
                raise RequiredPropositionUnavailable(
                    "correction_applied",
                    f"no renderable fragment for {[f for f, _v in echo]}")
            plan.register(Proposition(
                proposition_id=PropositionId.CORRECTION_APPLIED,
                kind=ResponseItemKind.CANONICAL_FACT, claim_family="vehicle",
                render=lambda: rendered,
                decision_authority=DecisionAuthority.RECONCILER,
                fact_sources=(FactSource.DETERMINISTIC_EVIDENCE, FactSource.CANONICAL_STATE),
                renderer_source=RendererSource.DETERMINISTIC_TEMPLATE,
                policy=ResponsePolicy.P3_SINGLE_MESSAGE_DEFAULT, priority=55,
                locked_values=tuple((f"correction.{f}", v) for f, v in echo)))
            logger.info("G3-7F-R2 correction echo planned thread_id=%s fields=%s",
                        ctx.thread.id, [f for f, _v in echo])

        # ── OWNER-RESOLUTION: the generic handoff says one neutral sentence ───────
        # Part C. The wording lives in the composer like every other customer-facing
        # string, so no handoff can borrow motorcycle prose, and the reason code travels
        # separately in the trace rather than inside the sentence.
        if getattr(self, "_turn_generic_handoff", False):
            rendered_h = _GENERIC_HANDOFF_REPLY
            if not rendered_h:
                raise RequiredPropositionUnavailable(
                    "generic_handoff", "no handoff wording available")
            plan.register(Proposition(
                proposition_id=PropositionId.GENERIC_HANDOFF_ACK,
                kind=ResponseItemKind.HANDOFF_ACK, claim_family="handoff",
                render=lambda: rendered_h,
                decision_authority=DecisionAuthority.HUMAN_HANDOFF_POLICY,
                fact_sources=(FactSource.CANONICAL_STATE,),
                renderer_source=RendererSource.DETERMINISTIC_TEMPLATE,
                policy=ResponsePolicy.P2_HUMAN_HANDOFF_EXCLUSIVE, priority=20,
                locked_values=(("handoff.reason",
                                getattr(self, "_turn_handoff_reason", None)),)))
            logger.info("OWNER-RESOLUTION generic handoff planned thread_id=%s reason=%s",
                        ctx.thread.id, getattr(self, "_turn_handoff_reason", None))

        # ── OWNER-RESOLUTION: the clarification Flow's explanatory sentence ───────
        # Part E. The Flow button carries this sentence as its body, so registering it
        # `redundant=True` records the claim in the plan and the trace for correlation
        # WITHOUT composing a second message — owner policy 13's "no duplicate text or
        # Flow". It becomes includable only if the Flow body is not what the customer got.
        _intro = getattr(self, "_turn_clarification_intro", None)
        if _intro:
            _family, _sentence = _intro
            plan.register(Proposition(
                proposition_id=(PropositionId.VEHICLE_CLARIFICATION_INTRO
                                if _family == "VEHICLE"
                                else PropositionId.LOCATION_CLARIFICATION_INTRO),
                kind=ResponseItemKind.FLOW_INTRO_TEXT, claim_family="clarification",
                render=lambda: _sentence,
                decision_authority=DecisionAuthority.RECONCILER,
                fact_sources=(FactSource.CANONICAL_STATE,),
                renderer_source=RendererSource.DETERMINISTIC_TEMPLATE,
                policy=ResponsePolicy.P4_FLOW_INTRO_SEQUENCE, priority=50,
                redundant=True,
                locked_values=(("clarification.family", _family),)))

        proposition = getattr(self, "_turn_correction_proposition", None)
        if proposition:
            pairs = tuple((e.get("field"), e.get("proposed"))
                          for e in (proposition.get("fields") or ()))
            rendered_q = render_correction_confirmation(pairs)
            if not rendered_q:
                raise RequiredPropositionUnavailable(
                    "correction_confirmation",
                    f"no renderable fragment for {[f for f, _v in pairs]}")
            plan.register(Proposition(
                proposition_id=PropositionId.CORRECTION_CONFIRMATION_REQUIRED,
                kind=ResponseItemKind.CLARIFICATION, claim_family="correction",
                render=lambda: rendered_q,
                decision_authority=DecisionAuthority.RECONCILER,
                fact_sources=(FactSource.DETERMINISTIC_EVIDENCE, FactSource.CANONICAL_STATE),
                renderer_source=RendererSource.DETERMINISTIC_TEMPLATE,
                policy=ResponsePolicy.P3_SINGLE_MESSAGE_DEFAULT, priority=65,
                locked_values=tuple((f"correction.{f}", v) for f, v in pairs)))
            logger.info("G3-7F-R2 correction confirmation planned thread_id=%s fields=%s",
                        ctx.thread.id, [f for f, _v in pairs])

    # ── 3. canonical vehicle ─────────────────────────────────────────────────────

    def _register_canonical_vehicle_proposition(self, ctx, text: str) -> None:
        """Reconcile a vehicle the reply speaks of that canonical state does not hold.

        Replaces `_enforce_canonical_vehicle_claim`. The detection and the canonical
        decision are unchanged, including arming `pending_fuzzy_catalog_key` — that is a
        PRODUCER's decision about canonical state and it stays here, where it can be seen,
        rather than inside a renderer, which §14 requires to be pure.
        """
        state = getattr(ctx, "state", None)
        if state is None or getattr(state, "needs_human", False):
            return
        if state.last_stage not in (STAGE_QUALIFYING, None):
            return
        if ctx.candidates or getattr(state, "current_focus_candidate_id", None):
            return                                   # P6: canonical state holds it
        if not text:
            return
        hit = lookup_vehicle(text)
        if hit is None:
            _mdy = extract_model_del_year(text)
            if _mdy is not None:
                hit = _mdy[0]
        if hit is None:
            hit = _contextual_numeric_model_lookup(text)
        if hit is None:
            return                                   # names no specific vehicle
        question = _FUZZY_CONFIRMATION_TEMPLATE.format(marca=hit.marca, modelo=hit.modelo)
        # The customer-facing wording is unchanged from the finalizer this replaces: the
        # reconciliation says out loud that the vehicle is not confirmed, then asks.
        rendered = (f"Todavía no tengo confirmado el vehículo, "
                    f"así que te confirmo: {question}")
        if getattr(state, "pending_fuzzy_catalog_key", None) is None:
            state.pending_fuzzy_catalog_key = f"{hit.marca}||{hit.modelo}"
            state.pending_turn_evidence_text = getattr(self, "_turn_text", None) or None
        self._decision_log(
            ctx, "response_state_reconciled",
            marca=hit.marca, modelo=hit.modelo, candidate="none", pending_armed=True)
        self._live_plan.register(Proposition(
            proposition_id=PropositionId.CANONICAL_VEHICLE,
            kind=ResponseItemKind.CANONICAL_FACT, claim_family="vehicle",
            render=lambda: rendered,
            decision_authority=DecisionAuthority.DETERMINISTIC_RULE,
            fact_sources=(FactSource.DETERMINISTIC_EVIDENCE, FactSource.CANONICAL_STATE),
            renderer_source=RendererSource.DETERMINISTIC_TEMPLATE, priority=60,
            redundant=self._norm_text(question) in self._norm_text(text),
            locked_values=(("vehicle.marca", hit.marca), ("vehicle.modelo", hit.modelo))))

    def _send_text_to_wa(self, ctx: _Context, text: str) -> str | None:
        # ── G3-5F §6/§9: the live plan composes; this function transports ──────────
        #
        # P3 is enforced HERE and only here, because this is the one place a turn can
        # occupy a second message. It is a refusal, not a warning: a plan that already
        # composed has said what the turn owed the customer, and a further free-text send
        # would be exactly the "whichever handler got here first" behaviour the milestone
        # removes. The approved Flow sequence (P4) is not affected — its second part is a
        # Meta Flow, dispatched by `_send_flow_button`, not a second text.
        plan = getattr(self, "_live_plan", None)
        if plan is not None and plan.composed:
            plan.refuse_additional_text()
            logger.warning(
                "G3-5F P3 SECOND TEXT REFUSED thread_id=%s plan=%s — the turn already "
                "composed its message", ctx.thread.id, getattr(plan, "turn_id", None))
            raise OutboundBlockedError(
                sender_path="engine._send_text_to_wa", kind="text",
                to_wa_id=ctx.contact.wa_id, thread_id=ctx.thread.id, text=text,
                gate_outcome="BLOCKED_SINGLE_MESSAGE_POLICY",
            )
        # L4.7D: canonical response validation — the last check on the handler's own prose,
        # before it is offered to the plan as a proposition.
        text = self._validate_outbound_text(text, ctx, ctx.state)

        # ── G3-6: producers register, THEN the plan composes ─────────────────────────
        #
        # The FAQ answer, the required-next-information question and the canonical vehicle
        # reconciliation used to run here as three passes over finished prose. They are
        # producers now: they register typed propositions and the plan renders them, so
        # every claim in the final message is counted, attributed, deduplicated by identity
        # and suppressible by policy — and nothing appends a fact after composition.
        self._turn_clarification_burst = getattr(self, "_turn_faq_burst", None)
        if plan is not None:
            plan.degraded_fallback = _DEGRADED_RESPONSE_FALLBACK
            self._register_response_propositions(ctx, text)
            text = plan.compose(text)
            if plan.degraded:
                # BC36-A03: `compose()` has already replaced the partial answer with the
                # bounded line. All that is left here is to say so — loudly, and in the
                # attribution, so a degraded turn is never mistaken for a normal one.
                logger.error(
                    "G3-7 DEGRADED RESPONSE thread_id=%s failures=%s — the bounded "
                    "fallback was sent instead of a partial answer",
                    ctx.thread.id, plan.producer_failures)
                self._turn_renderer_source = RendererSource.DETERMINISTIC_TEMPLATE
            elif plan.mixed_renderers:
                self._turn_renderer_source = RendererSource.COMPOSED_MIXED
            if not text:
                # Every proposition was suppressed — P2's exclusivity does this. Nothing
                # is sent, and nothing is invented to fill the silence.
                logger.info("G3-5F plan composed no text thread_id=%s", ctx.thread.id)
                self._turn_dispatch_path = DispatchPath.TEXT
                self._turn_dispatch_result = DispatchResult.NOT_ATTEMPTED
                return None

        if not (text or "").strip():
            # G3-7F-R2. A producer-only turn passes an EMPTY primary and lets the plan supply
            # the message (the correction confirmation does exactly this). With no plan to
            # compose against there is no message, and an empty WhatsApp body must never
            # reach the gate. `None` is what every correction path already reads as
            # "not dispatched".
            logger.info("G3-7F-R2 empty outbound text suppressed thread_id=%s", ctx.thread.id)
            self._turn_dispatch_path = DispatchPath.TEXT
            self._turn_dispatch_result = DispatchResult.NOT_ATTEMPTED
            return None

        from zoneinfo import ZoneInfo
        now_utc = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
        wa_id = ctx.contact.wa_id
        from .outbound_path_registry import OutboundPathId, get_deployment_id
        gate = OutboundSafetyGate(self.db)
        result = gate.attempt(
            wa_id=wa_id, thread_id=ctx.thread.id, text=text, now=now_utc,
            path_id=OutboundPathId.CE_TEXT.value,
            deployment_id=get_deployment_id(),
            correlation_id=self._turn_correlation_id(),
            causal_inbound_wa_message_id=ctx.inbound_wa_message_id,
        )
        # ── G3-5E: the transport records WHAT the gate decided, never WHO reasoned ──
        self._turn_dispatch_path = DispatchPath.TEXT
        if getattr(self, "_turn_renderer_source", None) is None:
            self._turn_renderer_source = RendererSource.DETERMINISTIC_TEMPLATE
        if result.outcome != GateOutcome.ALLOWED:
            self._turn_dispatch_result = (
                DispatchResult.DUPLICATE_SUPPRESSED
                if result.outcome == GateOutcome.BLOCKED_DUPLICATE
                else DispatchResult.BLOCKED)
            if plan is not None:
                plan.dispatch_path = DispatchPath.TEXT
                plan.dispatch_result = self._turn_dispatch_result
            raise OutboundBlockedError(
                sender_path="engine._send_text_to_wa", kind="text",
                to_wa_id=wa_id, thread_id=ctx.thread.id, text=text,
                gate_outcome=result.outcome.value,
            )
        self._turn_dispatch_result = DispatchResult.ALLOWED
        self._turn_text_dispatches = getattr(self, "_turn_text_dispatches", 0) + 1
        if plan is not None:
            plan.dispatch_path = DispatchPath.TEXT
            plan.dispatch_result = DispatchResult.ALLOWED
        wa_message_id: str | None = None
        try:
            wa_message_id, _ = _send_whatsapp_cloud_text(to_wa_id=wa_id, text=text)
            gate.mark_sent(result.message_id, wa_message_id)
            # ── W1F-W-01 R2: the question is RECORDED here, and ACTIVATED elsewhere ──
            # After `mark_sent`, which is the point at which Meta has ACCEPTED the message
            # and handed back a correlation handle. OC-R2 CORRECTION 1: that is not
            # delivery, and this no longer pretends it is — the row is created
            # `PENDING_DELIVERY` and owns nothing until a status webhook reports the
            # customer has it. Every way a turn can fail even to be accepted — the gate
            # blocked it, Meta raised, composition suppressed every proposition, the text
            # was empty — leaves this line unreached, so none of them leaves a row at all.
            self._record_delivered_questions(
                ctx, text=text,
                outbound_message_id=getattr(result, "message_id", None),
                wa_message_id=wa_message_id)
        except MetaSendError as exc:
            self._turn_dispatch_result = DispatchResult.FAILED
            logger.error("M18 send_text MetaSendError thread_id=%s: %s", ctx.thread.id, exc)
            gate.mark_failed(
                result.message_id,
                meta_http_status=exc.http_status,
                meta_error_payload=exc.to_payload(),
            )
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
        path_id: str | None = None,
        cta_label: str = "Completar datos",
        mode: str = FLOW_MODE_NAVIGATE,
    ) -> str:
        """Dispatch a Flow. `mode` decides how Meta launches it — see `_flow_launch_fields`.

        L4.7W4-F1: the default is NAVIGATE because every endpoint-less Flow uses it and
        those are the majority. The Booking Flow is the one endpoint-backed Flow and it
        passes FLOW_MODE_DATA_EXCHANGE explicitly; a static test asserts that it does, so
        the default cannot silently reclaim it.
        """
        if not flow_id:
            flow_id = (self.settings.whatsapp_flow_id or "").strip()
        if not flow_id:
            raise RuntimeError("WHATSAPP_FLOW_ID not configured")
        # G3-5E: the Flow is its own transport, attributed separately from text.
        self._turn_flow_dispatched = True
        self._turn_dispatch_path = DispatchPath.META_FLOW
        if getattr(self, "_turn_renderer_source", None) in (None, RendererSource.NO_PROSE):
            self._turn_renderer_source = RendererSource.META_FLOW

        # L4.7D: the Flow button body is customer-facing text and is validated too.
        body_text = self._validate_outbound_text(body_text, ctx, ctx.state)

        from zoneinfo import ZoneInfo
        now_utc = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
        wa_id = ctx.contact.wa_id

        # Safety gate: pre-send audit + duplicate/flood checks.
        from .outbound_path_registry import OutboundPathId, get_deployment_id
        gate = OutboundSafetyGate(self.db)
        result = gate.attempt(
            wa_id=wa_id, thread_id=ctx.thread.id, text=body_text,
            message_type="flow", now=now_utc,
            path_id=path_id or OutboundPathId.CE_FLOW.value,
            deployment_id=get_deployment_id(),
            correlation_id=self._turn_correlation_id(),
            causal_inbound_wa_message_id=ctx.inbound_wa_message_id,
        )
        if result.outcome != GateOutcome.ALLOWED:
            raise OutboundBlockedError(
                sender_path="engine._send_flow_button",
                kind="flow",
                to_wa_id=wa_id,
                thread_id=ctx.thread.id,
                gate_outcome=result.outcome.value,
            )
        # R3/D: the gate allowed it. From here the Flow — whose body IS the slot list —
        # really does go to the customer, which is what makes a `last_visible_slots` claim
        # true. Set after the gate, never before it.
        self._turn_flow_delivered = True

        try:
            wa_message_id, _ = _send_whatsapp_cloud_flow(
                to_wa_id=wa_id,
                flow_id=flow_id,
                flow_token=flow_token,
                body_text=body_text,
                cta_label=cta_label,
                initial_screen=initial_screen,
                mode=mode,
            )
            gate.mark_sent(result.message_id, wa_message_id)
        except MetaSendError as exc:
            gate.mark_failed(
                result.message_id,
                meta_http_status=exc.http_status,
                meta_error_payload=exc.to_payload(),
            )
            raise
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

        _hr_grp, _hr_det = self._get_active_inspection_location(ctx, state)
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
                zone_group=_hr_grp or "",
                zone_detail=_hr_det or "",
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
            self._notification_evidence(thread_rev_id, crm_rev_id, outcome="HELD",
                                        reason="NOTIFICATION_NOT_CONFIGURED")
            return

        # Phase 4-R1 (P4RV-04): the column is declared schema now, and the application will
        # not start without it. The read still runs under a SAVEPOINT, so a database error
        # here can never abort the session the price read and the write-back below share.
        try:
            with self.db.begin_nested():
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
                with self.db.begin_nested():      # 4-R1: a failure cannot poison the session
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
            self._notification_evidence(thread_rev_id, crm_rev_id, outcome="FAILED",
                                        reason="NOTIFICATION_SEND_FAILED")
