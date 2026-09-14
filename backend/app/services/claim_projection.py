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


# ── L4.7W5-F7B: deterministic acceptance, read from the lexicon module ────────
# The vocabulary deliberately does NOT live here: L4.7C-3B holds this module to a
# grammatical invariant — no phrase lists in executable code — and a test enforces it.
from .acceptance_lexicon import (          # noqa: E402
    ACCEPTANCE_KEYWORDS,
    acceptance_clauses as _acceptance_clauses,
    clause_is_acceptance as _clause_is_acceptance,
    declines as _declines,
)


def deterministic_acceptance(texts: Iterable[str], has_scheduling_evidence: bool) -> bool:
    """Acceptance readable from the words alone, scoped to the accepting clause.

    L4.7W5-F7B. `_is_acceptance` required the WHOLE burst to be acceptance words, so the
    Wild burst "si" + "para cuando tenes" produced no deterministic acceptance at all:
    the scheduling clause is not acceptance vocabulary, so the predicate failed on the
    very turn the customer said yes. Acceptance was then readable ONLY by the semantic
    interpreter, and when that call was unavailable the authorizer saw no stance,
    answered HOLD, and the conversation deadlocked — what the customer experienced.

    Scoping fixes that without widening what counts as a yes:

      * some clause must be acceptance THROUGHOUT — the same strictness as before;
      * that clause must itself read as present and factual, so a conditional yes is
        still conditional and still does not accept;
      * if other clauses exist they must be EXPLAINED by scheduling evidence in the same
        turn, which is the F6 rule applied to evidence rather than to modality;
      * any declining clause in the burst suppresses the whole thing, so an acceptance
        cannot advance merely by having arrived first.
    """
    clauses = _acceptance_clauses(texts)
    if not clauses:
        return False
    if _declines(texts):
        return False
    accepting = [c for c in clauses if _clause_is_acceptance(c)]
    if not accepting:
        return False
    if not any(_is_actionable(*turn_modality([clause])) for clause in accepting):
        return False
    if len(accepting) == len(clauses):
        return True            # the whole burst is acceptance — the historical rule
    return bool(has_scheduling_evidence)


def _is_actionable(temporality: Temporality, modality: Modality) -> bool:
    return (temporality in (Temporality.PRESENT, Temporality.UNKNOWN)
            and modality in (Modality.FACTUAL, Modality.UNKNOWN))


def acceptance_modality(
    texts: Iterable[str], has_scheduling_evidence: bool
) -> tuple[Temporality, Modality]:
    """Temporality/modality for an ACCEPTANCE claim, scoped to the clause that accepts.

    L4.7W5-F6. `turn_modality` is deliberately coarse — one reading shared by every claim in
    the turn — and that is right for most evidence. It is wrong for acceptance when the same
    burst also asks about scheduling:

        "si"  +  "para cuando tenes"
            -> combined: FUTURE / CONDITIONAL   ("cuando" and the trailing clause)
            -> the QUOTE_ACCEPTED claim is not actionable_now
            -> authorize.quote_acceptance HOLDs, stage never leaves QUOTED

    A live customer answered "si" to "¿podemos avanzar?" and asked when — twice — and the
    conversation deadlocked. The "si" was as present and factual as acceptance gets; the
    future tense belonged to a different sentence.

    The relaxation is deliberately narrow. It applies ONLY when the turn carries separate
    scheduling evidence, because that is what EXPLAINS the future/conditional markers as
    belonging to something other than the acceptance. Without it the coarse reading stands,
    so "si consigo la plata" followed by an unrelated "hola" is still conditional.

    Scope is the clause, not the message: "si, para cuando tenes?" arrives as one message and
    must behave like the two-message form.
    """
    if not has_scheduling_evidence:
        return turn_modality(texts)

    clauses: list[str] = []
    for text in texts:
        if not isinstance(text, str):
            continue
        clauses.extend(part for part in re.split(r"[,.;!?]| pero | aunque ", text)
                       if part and part.strip())
    if not clauses:
        return turn_modality(texts)

    # The acceptance is actionable if ANY clause states it in the present, as fact. Other
    # clauses may legitimately be about the future — that is the scheduling question.
    best = turn_modality(texts)
    for clause in clauses:
        temporality, modality = turn_modality([clause])
        if (temporality in (Temporality.PRESENT, Temporality.UNKNOWN)
                and modality in (Modality.FACTUAL, Modality.UNKNOWN)):
            return temporality, modality
    return best


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
        # Compared by VALUE, not identity: a module reload in another test suite splits the
        # enum class, and an identity check would silently drop every stance. Fourth
        # occurrence of this hazard in the programme — see AcceptanceSignal (L4.7B.2),
        # CorrectionRelation (L4.7B.4) and the record classes (L4.7C.1).
        signal_value = getattr(evidence.acceptance.signal, "value",
                               evidence.acceptance.signal)
        signal = AcceptanceSignal(signal_value) if signal_value in {
            s.value for s in AcceptanceSignal} else AcceptanceSignal.UNKNOWN
        if signal is AcceptanceSignal.ACCEPT:
            # L4.7W5-F6: scoped to the accepting clause, so a scheduling question in the
            # same burst cannot make the acceptance future or conditional.
            acc_temporality, acc_modality = acceptance_modality(
                texts, bool(getattr(evidence, "scheduling_requests", ())))
            add(ClaimType.QUOTE_ACCEPTED, True, status=evidence.acceptance.status,
                evidence_class=EvidenceClass.SEMANTIC_INFERRED,
                explicitness=Explicitness.IMPLIED, confidence=evidence.acceptance.confidence,
                temporality_override=acc_temporality, modality_override=acc_modality)
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
        # Priority by value and flexibility by fact: a branch with no stated time IS
        # flexible whatever the model said about it, and an enum that survived a module
        # reload is still the same priority.
        branches = tuple({"priority": getattr(s.priority, "value", s.priority),
                          "day": s.day_expression,
                          "time": s.time,
                          "flexible": bool(s.flexible_time or s.time is None),
                          "rank": s.rank}
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


# ── L4.7W5-F7B: THE canonical acceptance producer ─────────────────────────────

# Claim families the acceptance authorizer reasons about. A stance outside this set
# (hesitation, a bare question) contributes nothing, which is the safe default.
ACCEPTANCE_CLAIM_TYPES = (ClaimType.QUOTE_ACCEPTED, ClaimType.FUTURE_INTENT,
                          ClaimType.SEARCHING_NOT_READY)


def acceptance_claims(
    texts: Iterable[str],
    evidence: Optional[TurnEvidence] = None,
    *,
    cycle_id: Optional[str] = None,
    revision_id: Optional[int] = None,
    has_scheduling_evidence: Optional[bool] = None,
) -> list[ClaimEvidence]:
    """The single canonical producer of acceptance evidence for authorization.

    ONE BUSINESS CLAIM, ONE CANONICAL PRODUCER. Before F7B, `conversation_engine`
    assembled its own QUOTE_ACCEPTED claim with its own modality classifier
    (`turn_modality`) while the projection used the F6-scoped `acceptance_modality`.
    The two never actually disagreed — `_is_acceptance` required the whole burst to be
    acceptance words, so it could not fire on the very bursts where the classifiers
    differ — but that is a coincidence of their firing conditions, not a guarantee. One
    regex widened on either side and they diverge silently, with the authorizer taking
    whichever claim happens to be actionable.

    Worse, that disjointness WAS the live defect: for "si" + "para cuando tenes" the
    deterministic side produced nothing, so acceptance depended entirely on a semantic
    model call. When that call was unavailable the authorizer saw no stance at all and
    answered HOLD — the deadlock the customer hit, reachable again on any timeout.

    So both readings now converge here:

      * deterministic — clause-scoped, model-independent, and therefore able to read
        "si" + "para cuando tenes" with no model call at all;
      * semantic — the projection F6 corrected, unchanged and still authoritative for
        REJECT / FUTURE_INTENT / SEARCHING_NOT_READY.

    Both are stamped by ONE modality policy (`acceptance_modality`). This function never
    decides, never mutates state and never sends: it returns evidence for
    `authorize.quote_acceptance@v1`, which remains the only authority.
    """
    texts = [t for t in (texts or ()) if isinstance(t, str)]
    if has_scheduling_evidence is None:
        has_scheduling_evidence = bool(getattr(evidence, "scheduling_requests", ()) or ())

    # 1. Semantic stance, from the projection F6 fixed. Unchanged and not reclassified.
    out: list[ClaimEvidence] = []
    if evidence is not None:
        projected = claims_from_turn_evidence(
            evidence, texts=texts, cycle_id=cycle_id, revision_id=revision_id)
        out.extend(c for c in projected if c.claim_type in ACCEPTANCE_CLAIM_TYPES)

    # 2. An explicit rejection anywhere in the burst withdraws deterministic acceptance.
    #    Same-burst conflict must not resolve by whichever evidence was produced first.
    rejected = any(c.claim_type == ClaimType.QUOTE_ACCEPTED
                   and c.polarity is Polarity.NEGATED for c in out)

    # 3. Deterministic stance — the model-independent floor. It is a FLOOR, not a second
    #    opinion: when the semantic reading already asserted acceptance, adding a
    #    deterministic duplicate would put two QUOTE_ACCEPTED claims in front of the
    #    authorizer for one burst. Exactly one canonical claim leaves this function.
    already_accepted = any(c.claim_type == ClaimType.QUOTE_ACCEPTED
                           and c.polarity is Polarity.ASSERTED for c in out)
    if (not rejected and not already_accepted
            and deterministic_acceptance(texts, has_scheduling_evidence)):
        temporality, modality = acceptance_modality(texts, has_scheduling_evidence)
        out.append(ClaimEvidence(
            claim_type=ClaimType.QUOTE_ACCEPTED, value=True, polarity=Polarity.ASSERTED,
            # Deterministic extraction: the accepting words are literally present, so the
            # reading is not a proposal to be confirmed later.
            status=EvidenceStatus.CONFIRMED,
            evidence_class=EvidenceClass.DETERMINISTIC_EXTRACTED,
            producer="canonical:deterministic_acceptance",
            explicitness=Explicitness.IMPLIED,
            temporality=temporality, modality=modality,
            cycle_id=cycle_id, revision_id=revision_id).with_id())
    elif rejected:
        # Keep only the rejection and any non-acceptance stance: an ASSERTED acceptance
        # from the same burst cannot outvote an explicit "no" by ordering.
        out = [c for c in out if not (c.claim_type == ClaimType.QUOTE_ACCEPTED
                                      and c.polarity is Polarity.ASSERTED)]
    return out
