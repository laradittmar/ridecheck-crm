"""L4.7C.1 — projecting two evidence languages into one.

The semantic interpreter speaks `TurnEvidence`; ConversationEngine speaks `FieldEvidence`
(`field_evidence.py`, M21.1.5). Neither is wrong, and neither can be compared with the
other: one is organised by how language was read, the other by how state was resolved.
This module projects both into `ClaimEvidence` — one atomic claim per canonical field —
so that agreement, complement and conflict become observable facts rather than opinions.

Read-only by construction: nothing here mutates a TurnEvidence, a FieldEvidence snapshot,
the ORM, or any service. It imports no ConversationEngine, no PricingService, no
ScheduleService and no OutboundSafetyGate.

Two projection rules are worth stating out loud, because they are where authority would
leak in if it were going to:

* a make the interpreter *added* to a model-only mention is `SEMANTIC_INFERRED`, never
  `CATALOG_CONFIRMED` — the catalog registers that fact, not the model (L4.7C §5);
* a value the customer wrote in their own words is `EXPLICIT_CUSTOMER` only when it can be
  found in the burst; otherwise it is `IMPLIED` at best.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from ..schemas.claims import (
    ClaimEvidence,
    ClaimType,
    EvidenceClass,
    Explicitness,
    Modality,
    Polarity,
    Temporality,
)
from ..schemas.turn_evidence import (
    AcceptanceSignal,
    EvidenceStatus,
    LocationRole,
    ServiceIntentKind,
    TurnEvidence,
)

PROJECTION_VERSION = "claim-projection/1.0"

# Language cues for temporality and modality. These are *grammatical* markers — tense and
# conditionality — not business phrases: they say nothing about vehicles, prices or zones,
# and they are applied uniformly to every claim of a turn (no-phrase-patch rule §6.1).
# "si" is the hard case in Spanish: unaccented it can introduce a condition, and it is also
# how people write the affirmative "sí" without the accent. The invariant is grammatical, not
# lexical: **a conditional needs a consequence**. "si me cierra te hablo" has a protasis and
# an apodosis; "si avancemos" is a bare affirmation followed by a hortative. So a `si` clause
# counts as conditional only when something follows it that could be the consequence.
_CONDITIONAL_MARKERS = re.compile(r"\b(cuando|en cuanto|apenas|siempre que|capaz|quiz[aá]s?|"
                                  r"tal vez|puede que)\b", re.IGNORECASE)
_SI_CLAUSE = re.compile(r"\bsi\b(?![\s,]*$)", re.IGNORECASE)
_ACCENTED_SI = re.compile(r"\bs[íi]\b", re.IGNORECASE)
# How many words must follow a `si` before the sentence can carry a consequence. One or two
# ("si avancemos", "si dale") is an affirmation; three or more ("si me cierra te hablo") is a
# conditional with its apodosis.
_SI_CONSEQUENCE_WORDS = 3
_HYPOTHETICAL = re.compile(r"\b(hipot[eé]tic\w*|supongamos|imaginate|en teor[ií]a)\b",
                           re.IGNORECASE)
_FUTURE = re.compile(r"\b(voy a|vamos a|te aviso|te escribo|te hablo|te digo|te consulto|"
                     r"m[aá]s adelante|despu[eé]s|luego|pr[oó]xim\w+|cuando)\b", re.IGNORECASE)
_PAST = re.compile(r"\b(hab[ií]a|estuve|fui|ten[ií]a|era|pensaba)\b", re.IGNORECASE)


def _fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKD", text or "")
                    .encode("ascii", "ignore").decode().lower().split())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _si_is_conditional(text: str) -> bool:
    """True when a `si` in this text introduces a condition rather than agreeing.

    Grammatical, not lexical: an accented "sí" is always affirmative, and an unaccented "si"
    is conditional only when enough follows it to be a consequence. "si avancemos" and
    "si dale" agree; "si me cierra te hablo" and "si consigo el auto avanzamos" condition.
    An approximation, and a deliberately conservative one — it errs toward reading a long
    si-clause as conditional, which withholds authorization rather than granting it.
    """
    for match in re.finditer(r"\bsi\b", text, re.IGNORECASE):
        if match.group(0) != "si":            # "sí" with the accent is never a condition
            continue
        remainder = text[match.end():].strip(" ,.;:!?")
        if len(remainder.split()) >= _SI_CONSEQUENCE_WORDS:
            return True
    return False


def turn_modality(texts: Iterable[str]) -> tuple[Temporality, Modality]:
    """Read tense and conditionality off the burst.

    Deliberately coarse and deliberately shared by every claim in the turn: this is the
    difference between "te aviso cuando lo compre" and "lo compro", and it is the reason a
    conditional sentence can never satisfy a HIGH-risk precondition later (L4.7C §8).
    """
    combined = " ".join(t for t in texts if isinstance(t, str))
    if not combined.strip():
        return Temporality.UNKNOWN, Modality.UNKNOWN
    modality = Modality.FACTUAL
    if _HYPOTHETICAL.search(combined):
        modality = Modality.HYPOTHETICAL
    elif _CONDITIONAL_MARKERS.search(combined) or _si_is_conditional(combined):
        modality = Modality.CONDITIONAL
    temporality = Temporality.PRESENT
    if _FUTURE.search(combined):
        temporality = Temporality.FUTURE
    elif _PAST.search(combined):
        temporality = Temporality.PAST
    return temporality, modality


def _explicitness(value: Any, haystack: str) -> Explicitness:
    """STATED only when the value can be found in what the customer actually wrote."""
    if not isinstance(value, str) or not value.strip():
        return Explicitness.IMPLIED
    return Explicitness.STATED if _fold(value) in haystack else Explicitness.IMPLIED


def _class_for(explicitness: Explicitness, default: EvidenceClass) -> EvidenceClass:
    return (EvidenceClass.EXPLICIT_CUSTOMER if explicitness is Explicitness.STATED
            else default)


# ── TurnEvidence → claims ─────────────────────────────────────────────────────

def claims_from_turn_evidence(
    evidence: TurnEvidence,
    *,
    texts: Iterable[str] = (),
    cycle_id: Optional[str] = None,
    revision_id: Optional[int] = None,
) -> list[ClaimEvidence]:
    """Project one semantic interpretation into atomic claims. Never mutates the input."""
    if evidence is None:
        return []
    texts = [t for t in texts if isinstance(t, str)]
    haystack = _fold(" ".join(texts))
    temporality, modality = turn_modality(texts)
    producer = evidence.interpreter or "semantic:understand"
    version = evidence.model_version or ""
    message_ids = tuple(evidence.turn.ordered_message_ids) if evidence.turn else ()
    created = _now()
    out: list[ClaimEvidence] = []

    def add(claim_type: str, value: Any, *, status: EvidenceStatus,
            evidence_class: EvidenceClass, explicitness: Explicitness,
            polarity: Polarity = Polarity.ASSERTED, alternatives: tuple = (),
            confidence: Optional[float] = None, reason: Optional[str] = None,
            temporality_override: Optional[Temporality] = None,
            modality_override: Optional[Modality] = None) -> None:
        out.append(ClaimEvidence(
            claim_type=claim_type, value=value, polarity=polarity, status=status,
            evidence_class=evidence_class, producer=producer, producer_version=version,
            source_message_ids=message_ids, explicitness=explicitness,
            temporality=temporality_override or temporality,
            modality=modality_override or modality,
            confidence=confidence, cycle_id=cycle_id, revision_id=revision_id,
            created_at=created, alternatives=alternatives, reason=reason).with_id())

    # service intents, readiness, quote request, logistics
    for intent in evidence.service_intents:
        kind = intent.kind
        if kind is ServiceIntentKind.INSPECTION:
            add(ClaimType.SERVICE_INTENT, intent.value, status=intent.status,
                evidence_class=EvidenceClass.SEMANTIC_INFERRED,
                explicitness=Explicitness.IMPLIED, confidence=intent.confidence,
                reason=intent.reason)
        elif kind is ServiceIntentKind.QUOTE_REQUEST:
            add(ClaimType.QUOTE_REQUEST, True, status=intent.status,
                evidence_class=EvidenceClass.SEMANTIC_INFERRED,
                explicitness=Explicitness.IMPLIED, confidence=intent.confidence)
        elif kind is ServiceIntentKind.READINESS:
            add(ClaimType.SEARCHING_NOT_READY, intent.value, status=intent.status,
                evidence_class=EvidenceClass.SEMANTIC_INFERRED,
                explicitness=Explicitness.IMPLIED, confidence=intent.confidence)

    # vehicles — make and model are separate claims, and they are not the same strength
    for vehicle in evidence.vehicle_mentions:
        if vehicle.model:
            explicit = _explicitness(vehicle.model, haystack)
            add(ClaimType.VEHICLE_MODEL, vehicle.model, status=vehicle.status,
                evidence_class=_class_for(explicit, EvidenceClass.SEMANTIC_INFERRED),
                explicitness=explicit, confidence=vehicle.confidence,
                alternatives=tuple(a.value for a in vehicle.alternatives),
                polarity=(Polarity.NEGATED if vehicle.is_superseded else Polarity.ASSERTED),
                reason=vehicle.reason)
        if vehicle.make:
            explicit = _explicitness(vehicle.make, haystack)
            # A make the customer did not write is the interpreter's suggestion for the
            # catalog — it is projected as SEMANTIC_INFERRED and can never, on its own,
            # become canonical (L4.7C §5).
            add(ClaimType.VEHICLE_MAKE, vehicle.make, status=vehicle.status,
                evidence_class=_class_for(explicit, EvidenceClass.SEMANTIC_INFERRED),
                explicitness=explicit, confidence=vehicle.confidence,
                polarity=(Polarity.NEGATED if vehicle.is_superseded else Polarity.ASSERTED))
        if vehicle.year is not None:
            add(ClaimType.VEHICLE_YEAR, vehicle.year,
                status=(vehicle.year_status or vehicle.status),
                evidence_class=EvidenceClass.SEMANTIC_INFERRED,
                explicitness=_explicitness(str(vehicle.year), haystack),
                polarity=(Polarity.NEGATED if vehicle.is_superseded else Polarity.ASSERTED))
        if vehicle.category_suggestion:
            add(ClaimType.VEHICLE_CATEGORY, vehicle.category_suggestion,
                status=EvidenceStatus.PROPOSED,           # category is the catalog's word
                evidence_class=EvidenceClass.SEMANTIC_INFERRED,
                explicitness=Explicitness.DERIVED)

    # locations — the role decides the claim type; order never does
    for location in evidence.location_mentions:
        role = location.role
        claim_type = {
            LocationRole.INSPECTION_LOCATION.value: ClaimType.INSPECTION_LOCATION,
            LocationRole.CUSTOMER_ORIGIN.value: ClaimType.CUSTOMER_ORIGIN,
            LocationRole.SELLER_LOCATION.value: ClaimType.SELLER_LOCATION,
        }.get(role)
        if claim_type is None:
            continue                      # UNKNOWN_LOCATION_ROLE is not a canonical claim
        explicit = _explicitness(location.locality, haystack)
        add(claim_type, location.locality, status=location.status,
            evidence_class=_class_for(explicit, EvidenceClass.SEMANTIC_INFERRED),
            explicitness=explicit, confidence=location.confidence,
            alternatives=tuple(a.value for a in location.alternatives))

    # stance
    if evidence.acceptance is not None:
        signal = evidence.acceptance.signal
        if signal is AcceptanceSignal.ACCEPT:
            add(ClaimType.QUOTE_ACCEPTED, True, status=evidence.acceptance.status,
                evidence_class=EvidenceClass.SEMANTIC_INFERRED,
                explicitness=Explicitness.IMPLIED, confidence=evidence.acceptance.confidence)
        elif signal is AcceptanceSignal.REJECT:
            add(ClaimType.QUOTE_ACCEPTED, True, status=evidence.acceptance.status,
                evidence_class=EvidenceClass.SEMANTIC_INFERRED,
                explicitness=Explicitness.IMPLIED, polarity=Polarity.NEGATED)
        elif signal is AcceptanceSignal.FUTURE_INTENT:
            add(ClaimType.FUTURE_INTENT, True, status=evidence.acceptance.status,
                evidence_class=EvidenceClass.SEMANTIC_INFERRED,
                explicitness=Explicitness.IMPLIED,
                temporality_override=Temporality.FUTURE)
        # HESITATE and QUESTION_ONLY carry no claim: doubt is not evidence for or against.

    # scheduling — the ordered branches travel as ONE claim, order intact
    if evidence.scheduling_requests:
        branches = tuple({"priority": s.priority.value, "day": s.day_expression,
                          "time": s.time, "flexible": s.flexible_time, "rank": s.rank}
                         for s in evidence.scheduling_requests)
        add(ClaimType.SCHEDULING_PREFERENCE, branches,
            status=evidence.scheduling_requests[0].status,
            evidence_class=EvidenceClass.SEMANTIC_INFERRED,
            explicitness=Explicitness.IMPLIED)

    # corrections — the relation, and what it supersedes
    for correction in evidence.corrections:
        add(ClaimType.CORRECTION,
            {"relation": correction.relation.value, "from": correction.from_value,
             "to": correction.to_value},
            status=correction.status, evidence_class=EvidenceClass.SEMANTIC_INFERRED,
            explicitness=Explicitness.IMPLIED, reason=correction.reason)

    for faq in evidence.faq_intents:
        add(ClaimType.FAQ_TOPIC, faq.topic, status=faq.status,
            evidence_class=EvidenceClass.SEMANTIC_INFERRED,
            explicitness=Explicitness.IMPLIED)

    if evidence.handoff is not None and evidence.handoff.requested:
        add(ClaimType.NEEDS_HUMAN, True, status=evidence.handoff.status,
            evidence_class=EvidenceClass.SEMANTIC_INFERRED,
            explicitness=Explicitness.IMPLIED)

    return out


# ── FieldEvidence → claims ────────────────────────────────────────────────────

# How `field_evidence.py` labels its sources, mapped to what those sources actually are.
_SOURCE_CLASS = {
    "CURRENT_TURN_EXACT": EvidenceClass.DETERMINISTIC_EXTRACTED,
    "CURRENT_TURN_FUZZY_HIGH": EvidenceClass.DETERMINISTIC_EXTRACTED,
    "CURRENT_TURN_CONFIRMED_FUZZY": EvidenceClass.HUMAN_CONFIRMED,
    "FLOW": EvidenceClass.HUMAN_CONFIRMED,
    "WEBSITE_FORM": EvidenceClass.HUMAN_CONFIRMED,
    "CANDIDATE": EvidenceClass.CATALOG_CONFIRMED,
    "THREAD_STATE": EvidenceClass.DETERMINISTIC_EXTRACTED,
    "REVISION": EvidenceClass.HUMAN_CONFIRMED,
    "AI_EXTRACTED": EvidenceClass.SEMANTIC_INFERRED,
    "DERIVED": EvidenceClass.DETERMINISTIC_EXTRACTED,
}

_FIELD_CLAIMS = (
    ("service_intent", ClaimType.SERVICE_INTENT),
    ("vehicle", ClaimType.VEHICLE_MODEL),
    ("vehicle_year", ClaimType.VEHICLE_YEAR),
    ("vehicle_category", ClaimType.VEHICLE_CATEGORY),
    ("inspection_location", ClaimType.INSPECTION_LOCATION),
    ("customer_origin", ClaimType.CUSTOMER_ORIGIN),
    ("inspectability", ClaimType.INSPECTABILITY),
    ("scheduling", ClaimType.SCHEDULING_PREFERENCE),
)


def claims_from_field_evidence(
    snapshot: Any,
    *,
    texts: Iterable[str] = (),
    cycle_id: Optional[str] = None,
    revision_id: Optional[int] = None,
) -> list[ClaimEvidence]:
    """Project a `FieldEvidenceSnapshot` into claims. Never mutates the snapshot.

    Typed loosely on purpose: the snapshot is a frozen dataclass from another module, and
    this projection must not import ConversationEngine or the ORM to read it.
    """
    if snapshot is None:
        return []
    texts = [t for t in texts if isinstance(t, str)]
    haystack = _fold(" ".join(texts))
    temporality, modality = turn_modality(texts)
    created = _now()
    out: list[ClaimEvidence] = []

    for attribute, claim_type in _FIELD_CLAIMS:
        field = getattr(snapshot, attribute, None)
        value = getattr(field, "value", None)
        if field is None or value in (None, "", [], {}):
            continue                      # nothing said is NEITHER, never FALSE
        source = str(getattr(field, "source", "") or "")
        evidence_class = _SOURCE_CLASS.get(source, EvidenceClass.DETERMINISTIC_EXTRACTED)
        confirmed = bool(getattr(field, "confirmed", False))
        explicit = _explicitness(value, haystack)
        out.append(ClaimEvidence(
            claim_type=claim_type,
            value=value,
            status=(EvidenceStatus.CONFIRMED if confirmed else EvidenceStatus.PROPOSED),
            evidence_class=evidence_class,
            producer=f"ce:field_evidence[{source or 'UNKNOWN'}]",
            producer_version=PROJECTION_VERSION,
            explicitness=explicit,
            temporality=temporality if getattr(field, "current_turn", False) else Temporality.PAST,
            modality=modality if getattr(field, "current_turn", False) else Modality.FACTUAL,
            cycle_id=cycle_id, revision_id=revision_id, created_at=created,
            reason=f"field_evidence.{attribute}").with_id())
    return out


def project_all(
    turn_evidence: Optional[TurnEvidence],
    field_snapshot: Any = None,
    *,
    texts: Iterable[str] = (),
    cycle_id: Optional[str] = None,
    revision_id: Optional[int] = None,
) -> list[ClaimEvidence]:
    """Both producers, one list. Order is producer order, never priority."""
    texts = list(texts)
    return (claims_from_turn_evidence(turn_evidence, texts=texts, cycle_id=cycle_id,
                                      revision_id=revision_id)
            + claims_from_field_evidence(field_snapshot, texts=texts, cycle_id=cycle_id,
                                         revision_id=revision_id))


def in_cycle(claims: Iterable[ClaimEvidence], cycle_id: Optional[str]) -> list[ClaimEvidence]:
    """Claims belonging to the given cycle. A claim from a finished cycle is not evidence
    about this one — the L4.6 stale-candidate defect class, enforced structurally."""
    if cycle_id is None:
        return [c for c in claims if c.cycle_id is None]
    return [c for c in claims if c.cycle_id == cycle_id]
