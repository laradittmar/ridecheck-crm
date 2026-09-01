"""L4.7D — canonical response validator.

The AI composes natural language; it must not assert business facts that canonical state
does not support.  This module sits between response composition and OutboundSafetyGate:

    COMPOSE → CANONICAL RESPONSE VALIDATE → OutboundSafetyGate → SEND

Design rules (general, not phrase-specific):

* **Only assertive sentences carry claims.**  A question can never be a claim, so
  clarifications ("¿Es un Peugeot 2008?", "¿En qué zona está el auto?") always survive.
* **Every claim class names the canonical proof that licenses it.**  Absence of proof means
  the claim is unsupported, never that the proof is assumed.
* **Surgery, not amputation.**  Unsupported sentences are rewritten or dropped; valid FAQ
  answers and required next questions are preserved.  Only when nothing survives does a
  deterministic fallback replace the message.
* This module is pure: it reads a `CanonicalFacts` snapshot and returns text plus findings.
  It never touches the DB, the ORM or any service.

Claim classes and their canonical proof:

| Claim | Proof that licenses it | Unresolved means |
|---|---|---|
| VEHICLE | current-focus candidate with marca/modelo | no candidate, or a different vehicle |
| LOCATION | candidate zone, or cycle-scoped `state.home_zone_*` | no zone, or a zone that is only customer origin |
| PRICE | a PricingService quote for the active candidate + zone | no quote, or a different amount |
| AVAILABILITY | ScheduleService evaluation this turn, or slots it produced | never evaluated |
| BOOKING | booked ThreadRevision / current_revision_id (Flow completed) | Flow merely sent |
| ACCEPTANCE | lead.flag = ACEPTADO or stage at/after SCHEDULING | conversational tone alone |
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

# ── Claim class identifiers ───────────────────────────────────────────────────

CLAIM_VEHICLE = "VEHICLE"
CLAIM_LOCATION = "LOCATION"
CLAIM_PRICE = "PRICE"
CLAIM_AVAILABILITY = "AVAILABILITY"
CLAIM_BOOKING = "BOOKING"
CLAIM_ACCEPTANCE = "ACCEPTANCE"

ACTION_ALLOWED = "allowed"
ACTION_REMOVED = "removed"
ACTION_REWRITTEN = "rewritten"

# ── Detection lexicons (claim *classes*, not customer phrasings) ──────────────

_PRICE_AMOUNT_RE = re.compile(r"\$\s*\d[\d.,]*|\b\d[\d.,]{3,}\s*pesos\b", re.IGNORECASE)

_AVAILABILITY_RE = re.compile(
    r"\b(?:disponibilidad|disponibles?|hay\s+lugar|hay\s+turno|"
    r"horarios?\s+(?:disponibles?|libres?)|te\s+(?:ofrezco|puedo\s+ofrecer)|"
    r"queda\s+libre|tengo\s+(?:libre|el\s+horario|turno))\b",
    re.IGNORECASE,
)
_TIME_TOKEN_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")

# A NEGATIVE availability statement ("no tenemos disponibilidad a las 15:00") names a time
# precisely because it is unavailable — it must not be checked against the offered list.
_NEGATIVE_AVAILABILITY_RE = re.compile(
    r"\bno\s+(?:tenemos|tengo|hay|queda|quedan)\b", re.IGNORECASE
)

# A booking claim needs a booking noun AND a completed-state participle in the same
# sentence.  Infinitives ("para confirmar el turno") are invitations, not claims.
_BOOKING_PARTICIPLE_RE = re.compile(
    r"\b(?:confirmad|reservad|agendad)[oa]s?\b", re.IGNORECASE
)
_BOOKING_NOUN_RE = re.compile(
    r"\b(?:turno|revisi[oó]n|inspecci[oó]n|reserva|cita|visita)\b", re.IGNORECASE
)

_ACCEPTANCE_RE = re.compile(
    r"\b(?:presupuesto\s+aceptad\w*|cotizaci[oó]n\s+aceptad\w*|"
    r"ya\s+(?:lo\s+)?aceptaste|aceptaste\s+(?:el|la)\s+\w+|"
    r"avanzamos\s+con\s+(?:la\s+)?(?:reserva|contrataci[oó]n))\b",
    re.IGNORECASE,
)

# A sentence that describes where the CUSTOMER is, not where the vehicle is.
_ORIGIN_MARKER_RE = re.compile(
    r"\b(?:sos\s+de|eres\s+de|viv[ií]s?\s+en|est[aá]s\s+en|te\s+encontr[aá]s\s+en|"
    r"desde\s+donde\s+viv)\w*\b",
    re.IGNORECASE,
)

# Words that make a sentence a statement about the inspection subject.
_INSPECTION_SUBJECT_RE = re.compile(
    r"\b(?:auto|veh[ií]culo|camioneta|revisi[oó]n|inspecci[oó]n|turno|cotizaci[oó]n)\b",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

_FALLBACK_REPLY = (
    "Para poder avanzar necesito confirmar algunos datos. "
    "¿Me contás qué vehículo querés revisar y en qué zona está?"
)


def _norm(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return " ".join(stripped.lower().split())


def _is_question(sentence: str) -> bool:
    """A question never asserts a business fact."""
    s = (sentence or "").strip()
    return s.endswith("?") or s.startswith("¿")


# ── Canonical snapshot ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CanonicalFacts:
    """Everything the validator is allowed to treat as proven."""
    vehicle_marca: Optional[str] = None
    vehicle_modelo: Optional[str] = None
    inspection_zone_detail: Optional[str] = None
    inspection_zone_group: Optional[str] = None
    customer_origin_zones: tuple[str, ...] = ()
    known_zone_names: tuple[str, ...] = ()
    quote_total: Optional[int] = None
    quote_base: Optional[int] = None
    quote_viaticos: Optional[int] = None
    # Amounts already sent to this customer in the active cycle. Restating a quote the
    # customer has already received is legitimate ("¿cuánto salía?"), and such an amount
    # was itself validated when it was first sent — the AI cannot introduce a new one.
    previously_quoted: tuple[int, ...] = ()
    availability_checked: bool = False
    offered_slots: tuple[str, ...] = ()
    booking_confirmed: bool = False
    acceptance_confirmed: bool = False

    @property
    def vehicle_known(self) -> bool:
        return bool(self.vehicle_marca or self.vehicle_modelo)

    @property
    def location_known(self) -> bool:
        return bool(self.inspection_zone_detail or self.inspection_zone_group)

    @property
    def quote_known(self) -> bool:
        return self.quote_total is not None


@dataclass
class ClaimFinding:
    claim: str
    allowed: bool
    proof: str
    action: str
    detail: str = ""


@dataclass
class ValidationResult:
    text: str
    findings: list[ClaimFinding] = field(default_factory=list)

    @property
    def blocked(self) -> list[ClaimFinding]:
        return [f for f in self.findings if not f.allowed]

    @property
    def modified(self) -> bool:
        return any(f.action != ACTION_ALLOWED for f in self.findings)


# ── Per-claim sentence checks ─────────────────────────────────────────────────

def _check_price(sentence: str, facts: CanonicalFacts) -> Optional[ClaimFinding]:
    amounts = _PRICE_AMOUNT_RE.findall(sentence)
    if not amounts:
        return None
    if not facts.quote_known:
        return ClaimFinding(CLAIM_PRICE, False, "no PricingService quote", ACTION_REMOVED,
                            "amount stated without a deterministic quote")
    digits = {int(re.sub(r"\D", "", a) or 0) for a in amounts}
    allowed = {v for v in (facts.quote_total, facts.quote_base, facts.quote_viaticos)
               if v is not None} | set(facts.previously_quoted)
    if digits <= allowed:
        return ClaimFinding(CLAIM_PRICE, True, "PricingService quote / prior quote",
                            ACTION_ALLOWED)
    return ClaimFinding(CLAIM_PRICE, False, "PricingService quote", ACTION_REWRITTEN,
                        "amount does not match the canonical quote")


def _rewrite_price(sentence: str, facts: CanonicalFacts) -> str:
    """Replace every non-canonical amount with the canonical total."""
    canonical = f"${facts.quote_total:,.0f}".replace(",", ".")
    allowed = {v for v in (facts.quote_total, facts.quote_base, facts.quote_viaticos)
               if v is not None} | set(facts.previously_quoted)

    def _sub(m: re.Match) -> str:
        value = int(re.sub(r"\D", "", m.group(0)) or 0)
        return m.group(0) if value in allowed else canonical

    return _PRICE_AMOUNT_RE.sub(_sub, sentence)


def _check_vehicle(sentence: str, facts: CanonicalFacts,
                   resolver) -> Optional[ClaimFinding]:
    hit = resolver(sentence) if resolver else None
    if hit is None:
        return None
    named = _norm(f"{getattr(hit, 'marca', '')} {getattr(hit, 'modelo', '')}")
    if not facts.vehicle_known:
        return ClaimFinding(CLAIM_VEHICLE, False, "no current-focus candidate",
                            ACTION_REMOVED, f"named vehicle {named!r} is not canonical")
    canonical = _norm(f"{facts.vehicle_marca or ''} {facts.vehicle_modelo or ''}")
    if named and canonical and named not in canonical and canonical not in named:
        return ClaimFinding(CLAIM_VEHICLE, False, "current-focus candidate",
                            ACTION_REMOVED,
                            f"named vehicle {named!r} differs from canonical {canonical!r}")
    return ClaimFinding(CLAIM_VEHICLE, True, "current-focus candidate", ACTION_ALLOWED)


def _check_location(sentence: str, facts: CanonicalFacts) -> Optional[ClaimFinding]:
    n = _norm(sentence)
    mentioned = [z for z in facts.known_zone_names if z and _norm(z) in n]
    if not mentioned:
        return None
    if not _INSPECTION_SUBJECT_RE.search(sentence):
        # No inspection subject → this is not an inspection-location claim.
        return None
    if _ORIGIN_MARKER_RE.search(sentence):
        # Explicitly about the customer, not the vehicle.
        return ClaimFinding(CLAIM_LOCATION, True, "customer-origin statement", ACTION_ALLOWED)
    canonical = {_norm(z) for z in (facts.inspection_zone_detail, facts.inspection_zone_group) if z}
    if not canonical:
        return ClaimFinding(CLAIM_LOCATION, False, "no canonical inspection location",
                            ACTION_REMOVED, f"stated {mentioned!r} with no canonical zone")
    for zone in mentioned:
        if _norm(zone) not in canonical:
            origins = {_norm(z) for z in facts.customer_origin_zones}
            reason = ("customer origin stated as inspection location"
                      if _norm(zone) in origins else "zone differs from canonical")
            return ClaimFinding(CLAIM_LOCATION, False, "candidate/state zone",
                                ACTION_REMOVED, f"{zone}: {reason}")
    return ClaimFinding(CLAIM_LOCATION, True, "candidate/state zone", ACTION_ALLOWED)


def _check_availability(sentence: str, facts: CanonicalFacts) -> Optional[ClaimFinding]:
    times = [f"{int(h):02d}:{m}" for h, m in _TIME_TOKEN_RE.findall(sentence)]
    lexicon = bool(_AVAILABILITY_RE.search(sentence))
    if not lexicon and not times:
        return None
    if facts.booking_confirmed:
        # The appointment itself is canonical: its date/time may be restated.
        return ClaimFinding(CLAIM_AVAILABILITY, True, "confirmed booking", ACTION_ALLOWED)
    if not facts.availability_checked:
        return ClaimFinding(CLAIM_AVAILABILITY, False, "no ScheduleService evaluation",
                            ACTION_REMOVED, "availability stated without evaluation")
    if _NEGATIVE_AVAILABILITY_RE.search(sentence):
        # Stating that something is NOT available still requires an evaluation, but the
        # named time is by definition not in the offered list.
        return ClaimFinding(CLAIM_AVAILABILITY, True, "ScheduleService evaluation (negative)",
                            ACTION_ALLOWED)
    if times and facts.offered_slots:
        unknown = [t for t in times if t not in facts.offered_slots]
        if unknown:
            return ClaimFinding(CLAIM_AVAILABILITY, False, "ScheduleService slots",
                                ACTION_REMOVED, f"slots not offered: {unknown}")
    return ClaimFinding(CLAIM_AVAILABILITY, True, "ScheduleService evaluation", ACTION_ALLOWED)


def _check_booking(sentence: str, facts: CanonicalFacts) -> Optional[ClaimFinding]:
    if not (_BOOKING_PARTICIPLE_RE.search(sentence) and _BOOKING_NOUN_RE.search(sentence)):
        return None
    if facts.booking_confirmed:
        return ClaimFinding(CLAIM_BOOKING, True, "booked ThreadRevision", ACTION_ALLOWED)
    return ClaimFinding(CLAIM_BOOKING, False, "no booked ThreadRevision", ACTION_REMOVED,
                        "booking asserted before the Flow completed")


def _check_acceptance(sentence: str, facts: CanonicalFacts) -> Optional[ClaimFinding]:
    if not _ACCEPTANCE_RE.search(sentence):
        return None
    if facts.acceptance_confirmed:
        return ClaimFinding(CLAIM_ACCEPTANCE, True, "lead flag / stage", ACTION_ALLOWED)
    return ClaimFinding(CLAIM_ACCEPTANCE, False, "no canonical acceptance", ACTION_REMOVED,
                        "commercial state asserted without acceptance")


# ── Public entry point ────────────────────────────────────────────────────────

def validate_response(
    text: str,
    facts: CanonicalFacts,
    vehicle_resolver=None,
    fallback: str = _FALLBACK_REPLY,
) -> ValidationResult:
    """Validate a composed reply against canonical state.

    `vehicle_resolver` is an optional callable returning a catalog match for a text
    fragment (CE passes its deterministic resolver chain).  Injected rather than imported
    so this module stays free of catalog/DB dependencies.
    """
    if not text or not text.strip():
        return ValidationResult(text=text, findings=[])

    sentences = [s for s in _SENTENCE_SPLIT_RE.split(text) if s and s.strip()]
    kept: list[str] = []
    findings: list[ClaimFinding] = []

    for sentence in sentences:
        if _is_question(sentence):
            kept.append(sentence)
            continue

        sentence_out = sentence
        drop = False
        for check in (
            lambda s: _check_price(s, facts),
            lambda s: _check_vehicle(s, facts, vehicle_resolver),
            lambda s: _check_location(s, facts),
            lambda s: _check_availability(s, facts),
            lambda s: _check_booking(s, facts),
            lambda s: _check_acceptance(s, facts),
        ):
            finding = check(sentence_out)
            if finding is None:
                continue
            findings.append(finding)
            if finding.allowed:
                continue
            if finding.claim == CLAIM_PRICE and finding.action == ACTION_REWRITTEN:
                sentence_out = _rewrite_price(sentence_out, facts)
                continue
            drop = True
            break

        if not drop:
            kept.append(sentence_out)

    if not kept:
        # Nothing survived — never send an unsupported message; send a safe deterministic one.
        return ValidationResult(text=fallback, findings=findings)

    return ValidationResult(text=" ".join(s.strip() for s in kept), findings=findings)
