"""L4.7C.1 — atomic claims: the common currency of reconciliation.

    TurnEvidence (semantic)  ┐
                             ├─►  ClaimEvidence[]  ─►  reconciliation  ─►  canonical state
    FieldEvidence (CE)       ┘         ▲                                    (NOT YET — C2+)
                                       │
                        organised by CANONICAL CLAIM TYPE, not by producer

`TurnEvidence` is organised the way the interpreter thinks — intents, vehicles, locations.
`FieldEvidence` is organised the way ConversationEngine reads state. Reconciliation needs a
third organisation: one atomic claim per (claim type, value, source), so that two producers
talking about the same canonical field become comparable at all.

Four fields carry the safety weight and exist nowhere else in the system:

* `polarity`     — "el auto no está en Tigre" is evidence AGAINST Tigre, not for it;
* `temporality`  — a promise about later is not a fact about now;
* `modality`     — a conditional acceptance is not an acceptance;
* `cycle_id`     — a claim from a finished cycle is not evidence about this one.

Nothing in this module writes to a database, calls a service, or imports
ConversationEngine: it is a pure schema, defined ahead of the authority it will later serve.
Confidence is carried but is **advisory only** — no function here reads it back, and none
may (`docs/semantic/SEMANTIC_TRUTH_MODEL.md`, L4.7C design §15).
"""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field

from .turn_evidence import EvidenceStatus, SourceSpan

CLAIM_SCHEMA_VERSION = "claim-evidence/1.0"


# ── enumerations ──────────────────────────────────────────────────────────────

class Polarity(str, Enum):
    """Does the evidence support the value, or deny it?"""
    ASSERTED = "ASSERTED"
    NEGATED = "NEGATED"


class EvidenceClass(str, Enum):
    """Where a claim came from. Classifies origin and strength — never authority."""
    EXPLICIT_CUSTOMER = "EXPLICIT_CUSTOMER"            # the customer said it, in words
    SEMANTIC_INFERRED = "SEMANTIC_INFERRED"            # the interpreter added it
    DETERMINISTIC_EXTRACTED = "DETERMINISTIC_EXTRACTED"  # a CE parser matched it
    CATALOG_CONFIRMED = "CATALOG_CONFIRMED"            # the catalog resolved it
    SERVICE_COMPUTED = "SERVICE_COMPUTED"              # Pricing/Schedule produced it
    HUMAN_CONFIRMED = "HUMAN_CONFIRMED"                # an operator entered or approved it


class Explicitness(str, Enum):
    STATED = "STATED"        # present in the customer's own words
    IMPLIED = "IMPLIED"      # a reading of what was said
    DERIVED = "DERIVED"      # computed from other evidence


class Temporality(str, Enum):
    PAST = "PAST"
    PRESENT = "PRESENT"
    FUTURE = "FUTURE"
    UNKNOWN = "UNKNOWN"


class Modality(str, Enum):
    FACTUAL = "FACTUAL"
    CONDITIONAL = "CONDITIONAL"
    HYPOTHETICAL = "HYPOTHETICAL"
    NEGATED = "NEGATED"
    UNKNOWN = "UNKNOWN"


class InformationState(str, Enum):
    """What the evidence, taken together, says about a claim type.

    The distinction that matters: NEITHER is not FALSE_ONLY. Absence of evidence is not
    evidence of absence, and no authorization rule may treat it as such.
    """
    NEITHER = "NEITHER"          # no evidence either way
    TRUE_ONLY = "TRUE_ONLY"      # positive support only
    FALSE_ONLY = "FALSE_ONLY"    # negative support only
    BOTH = "BOTH"                # contradictory support


class RiskTier(str, Enum):
    """Consequence of acting on a claim. Metadata in C1; authorization input in C3/C4."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ReconciliationOutcome(str, Enum):
    ACCEPT = "ACCEPT"
    CLARIFY = "CLARIFY"
    HOLD = "HOLD"
    NEEDS_HUMAN = "NEEDS_HUMAN"


# ── claim types and their risk ────────────────────────────────────────────────

class ClaimType:
    """Canonical claim identifiers. Strings, so an unknown type never crashes a shadow run."""
    SERVICE_INTENT = "service_intent"
    VEHICLE_MAKE = "vehicle.make"
    VEHICLE_MODEL = "vehicle.model"
    VEHICLE_YEAR = "vehicle.year"
    VEHICLE_CATEGORY = "vehicle.category"
    INSPECTION_LOCATION = "inspection_location"
    CUSTOMER_ORIGIN = "customer_origin"
    SELLER_LOCATION = "seller_location"
    QUOTE_REQUEST = "quote_request"
    QUOTE_ACCEPTED = "quote_accepted"
    FUTURE_INTENT = "future_intent"
    SEARCHING_NOT_READY = "searching_not_ready"
    CORRECTION = "correction"
    SCHEDULING_PREFERENCE = "scheduling_preference"
    INSPECTABILITY = "inspectability"
    FAQ_TOPIC = "faq_topic"
    NEEDS_HUMAN = "needs_human"


RISK_TIERS: dict[str, RiskTier] = {
    ClaimType.SERVICE_INTENT: RiskTier.LOW,
    ClaimType.CUSTOMER_ORIGIN: RiskTier.LOW,
    ClaimType.SELLER_LOCATION: RiskTier.LOW,
    ClaimType.FAQ_TOPIC: RiskTier.LOW,
    ClaimType.FUTURE_INTENT: RiskTier.LOW,
    ClaimType.SEARCHING_NOT_READY: RiskTier.LOW,
    ClaimType.VEHICLE_MAKE: RiskTier.MEDIUM,
    ClaimType.VEHICLE_MODEL: RiskTier.MEDIUM,
    ClaimType.VEHICLE_YEAR: RiskTier.MEDIUM,
    ClaimType.VEHICLE_CATEGORY: RiskTier.MEDIUM,
    ClaimType.INSPECTION_LOCATION: RiskTier.MEDIUM,
    ClaimType.QUOTE_REQUEST: RiskTier.MEDIUM,
    ClaimType.SCHEDULING_PREFERENCE: RiskTier.MEDIUM,
    ClaimType.CORRECTION: RiskTier.MEDIUM,
    ClaimType.INSPECTABILITY: RiskTier.MEDIUM,
    ClaimType.QUOTE_ACCEPTED: RiskTier.HIGH,
    ClaimType.NEEDS_HUMAN: RiskTier.HIGH,
}


def risk_tier_for(claim_type: str) -> RiskTier:
    """Unknown claim types are treated as HIGH: an unclassified consequence is not a small one."""
    return RISK_TIERS.get(claim_type, RiskTier.HIGH)


# ── the claim ─────────────────────────────────────────────────────────────────

class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ClaimEvidence(_Frozen):
    """One atomic thing one producer says about one canonical field."""
    claim_type: str
    value: Any = None
    polarity: Polarity = Polarity.ASSERTED
    status: EvidenceStatus = EvidenceStatus.PROPOSED
    evidence_class: EvidenceClass = EvidenceClass.SEMANTIC_INFERRED
    producer: str = "unknown"
    producer_version: str = ""
    source_message_ids: tuple[str, ...] = ()
    source_span: Optional[SourceSpan] = None
    explicitness: Explicitness = Explicitness.IMPLIED
    temporality: Temporality = Temporality.UNKNOWN
    modality: Modality = Modality.UNKNOWN
    confidence: Optional[float] = None          # ADVISORY ONLY — never read by any rule
    cycle_id: Optional[str] = None
    revision_id: Optional[int] = None
    created_at: Optional[str] = None
    supersedes: tuple[str, ...] = ()
    alternatives: tuple[Any, ...] = ()          # ambiguity survives projection
    reason: Optional[str] = None
    claim_id: str = ""

    def with_id(self) -> "ClaimEvidence":
        """Return a copy carrying a stable content hash as `claim_id`."""
        if self.claim_id:
            return self
        payload = json.dumps(
            {"t": self.claim_type, "v": _hashable(self.value), "p": self.polarity.value,
             "s": self.status.value, "e": self.evidence_class.value,
             "pr": self.producer, "pv": self.producer_version,
             "m": list(self.source_message_ids), "c": self.cycle_id,
             "tm": self.temporality.value, "md": self.modality.value,
             "at": self.created_at},
            sort_keys=True, separators=(",", ":"), default=str)
        return self.model_copy(update={
            "claim_id": hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]})

    @property
    def risk_tier(self) -> RiskTier:
        return risk_tier_for(self.claim_type)

    @property
    def is_actionable_now(self) -> bool:
        """A claim about the present, asserted as fact. NOT an authorization — a shape test.

        A future or conditional statement can never satisfy a HIGH-risk precondition
        (L4.7C §8). The rule that uses this lands in C3; C1 only makes it expressible.
        """
        return (self.polarity is Polarity.ASSERTED
                and self.temporality in (Temporality.PRESENT, Temporality.UNKNOWN)
                and self.modality in (Modality.FACTUAL, Modality.UNKNOWN))


def _hashable(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [_hashable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _hashable(v) for k, v in sorted(value.items())}
    return value


# ── information state ─────────────────────────────────────────────────────────

def information_state(claims: Iterable[ClaimEvidence]) -> InformationState:
    """Fold the claims about ONE claim type into a four-valued state.

    Rules, in order of importance:
      * no claims at all → NEITHER. Absence is never FALSE.
      * AMBIGUOUS / CONFLICT contribute uncertainty, not support: an AMBIGUOUS claim on its
        own leaves the state NEITHER, with its alternatives preserved elsewhere.
      * asserted and negated support together → BOTH, and so do two asserted claims with
        incompatible values. Disagreement stays visible; nothing is averaged away.
    """
    claims = list(claims)
    if not claims:
        return InformationState.NEITHER

    asserted: list = []
    negated: list = []
    for claim in claims:
        if claim.status in (EvidenceStatus.AMBIGUOUS, EvidenceStatus.CONFLICT):
            continue                          # uncertainty is not support
        if claim.value in (None, "", [], {}):
            continue                          # a claim with no value supports nothing
        target = (negated
                  if (claim.polarity is Polarity.NEGATED
                      or claim.modality is Modality.NEGATED)
                  else asserted)
        target.append(json.dumps(_hashable(claim.value), sort_keys=True, default=str))

    # A negation contradicts only the value it denies. "It is not a Ka, it is a Kuga" is a
    # REPLACEMENT, not a contradiction: denying X while asserting Y leaves Y standing, and
    # the supersession is recorded separately (L4.7C.2).
    if asserted and set(asserted) & set(negated):
        return InformationState.BOTH
    if len(set(asserted)) > 1:
        return InformationState.BOTH
    if asserted:
        return InformationState.TRUE_ONLY
    if negated:
        return InformationState.FALSE_ONLY
    return InformationState.NEITHER


def alternatives_for(claims: Iterable[ClaimEvidence]) -> tuple[Any, ...]:
    """Every reading that survived, so ambiguity can be shown rather than resolved."""
    out: list[Any] = []
    for claim in claims:
        for alternative in claim.alternatives:
            if alternative not in out:
                out.append(alternative)
        if (claim.status in (EvidenceStatus.AMBIGUOUS, EvidenceStatus.CONFLICT)
                and claim.value not in (None, "", [], {}) and claim.value not in out):
            out.append(claim.value)
    return tuple(out)


__all__ = [
    "CLAIM_SCHEMA_VERSION", "Polarity", "EvidenceClass", "Explicitness", "Temporality",
    "Modality", "InformationState", "RiskTier", "ReconciliationOutcome", "ClaimType",
    "RISK_TIERS", "risk_tier_for", "ClaimEvidence", "information_state", "alternatives_for",
]
