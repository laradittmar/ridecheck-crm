"""L4.7C.3A — may this conversation advance commercially?

Reconciliation answers *what is supported*. Authorization answers *what may now happen*.
They are different questions, and conflating them is how "the customer said dale" becomes
"the customer bought the thing we last computed a price for".

This module implements the second question for the highest-risk transition in the product —
`quote_accepted` — and it is **shadow only** in C3A: it returns a decision, records why, and
writes nothing. ConversationEngine's legacy acceptance path remains authoritative.

Two asymmetries are deliberate and load-bearing:

* **A positive prerequisite must be positively proven.** Acceptance evidence, a delivered
  quote, an unchanged quote — each must be shown, never assumed from silence.
* **A blocker blocks when present and proves nothing when absent.** `SEARCHING_NOT_READY`
  absent does not mean "ready": it means nothing, which is why readiness is never a
  prerequisite for ALLOW — the positive prerequisites carry that weight alone.

Nothing here imports the ORM: the caller passes a `CommercialState` snapshot it has already
read. Confidence is not an input to any branch.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from ..schemas.claims import (
    ClaimEvidence,
    ClaimType,
    EvidenceClass,
    Explicitness,
    InformationState,
    Modality,
    Polarity,
    ReconciliationOutcome,
    RiskTier,
    Temporality,
    information_state,
)

logger = logging.getLogger(__name__)

RULE_ID = "authorize.quote_acceptance"
RULE_VERSION = "v1"


# ── the deterministic state acceptance depends on ─────────────────────────────

@dataclass(frozen=True)
class CommercialState:
    """Everything deterministic that acceptance depends on, read by the caller.

    `quote_identity` is derived, not stored: RideCheck has no quote version column, and the
    audit found it does not need one. A quote is identified by what it was computed FROM —
    revision, candidate, category, zone and amount. Change any input and the identity
    changes, which is exactly the staleness test acceptance needs.
    """
    cycle_id: Optional[str] = None
    revision_id: Optional[int] = None
    candidate_id: Optional[int] = None
    quote_total: Optional[int] = None
    quote_tipo_vehiculo: Optional[str] = None
    quote_zone_group: Optional[str] = None
    quote_zone_detail: Optional[str] = None
    quote_candidate_id: Optional[int] = None
    quote_cycle_id: Optional[str] = None
    # Current canonical inputs, which may already have moved away from the quote's.
    current_tipo_vehiculo: Optional[str] = None
    current_zone_group: Optional[str] = None
    current_zone_detail: Optional[str] = None
    # Delivery proof (§5): amounts actually present in outbound messages of this cycle.
    delivered_amounts: tuple[int, ...] = ()
    quote_delivered: bool = False
    lead_flag: Optional[str] = None
    stage: Optional[str] = None
    candidate_conflict: bool = False
    location_conflict: bool = False

    def quote_identity(self) -> Optional[str]:
        if self.quote_total is None:
            return None
        payload = json.dumps({
            "cycle": self.quote_cycle_id or self.cycle_id,
            "revision": self.revision_id,
            "candidate": self.quote_candidate_id if self.quote_candidate_id is not None
            else self.candidate_id,
            "tipo": self.quote_tipo_vehiculo,
            "zone": [self.quote_zone_group, self.quote_zone_detail],
            "total": self.quote_total,
        }, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def quote_inputs_unchanged(self) -> bool:
        """True when nothing the quote was computed from has moved since."""
        if self.quote_total is None:
            return False
        if self.quote_candidate_id is not None and self.candidate_id is not None:
            if self.quote_candidate_id != self.candidate_id:
                return False
        for quoted, current in ((self.quote_tipo_vehiculo, self.current_tipo_vehiculo),
                                (self.quote_zone_group, self.current_zone_group),
                                (self.quote_zone_detail, self.current_zone_detail)):
            if quoted is not None and current is not None and quoted != current:
                return False
        return True

    def quote_is_delivered(self) -> bool:
        """Computing a price is not telling the customer. Delivery must be evidenced."""
        if self.quote_total is None:
            return False
        if self.quote_total in self.delivered_amounts:
            return True
        return bool(self.quote_delivered)


# ── the decision ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AuthorizationDecision:
    result: str                                   # ALLOW | DENY | CLARIFY | HOLD
    reason: str = ""
    rule_id: str = RULE_ID
    rule_version: str = RULE_VERSION
    risk_tier: str = RiskTier.HIGH.value
    stance: Optional[str] = None
    stance_state: str = InformationState.NEITHER.value
    quote_identity: Optional[str] = None
    satisfied: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    shadow: bool = True

    @property
    def allows(self) -> bool:
        return self.result == "ALLOW"


ALLOW, DENY, CLARIFY, HOLD = "ALLOW", "DENY", "CLARIFY", "HOLD"


# ── stance reconciliation ─────────────────────────────────────────────────────

def reconcile_stance(claims: Iterable[ClaimEvidence]) -> tuple[Optional[str], str, tuple]:
    """Return (stance, information_state, evidence_ids) for this turn.

    ACCEPT is the only stance that can contribute to progression, and only as *one* of the
    prerequisites. REJECT, HESITATE, FUTURE_INTENT and QUESTION_ONLY each block or simply
    fail to advance — they are never averaged into a "mostly yes".
    """
    claims = list(claims)
    accept = [c for c in claims
              if c.claim_type == ClaimType.QUOTE_ACCEPTED and c.polarity is Polarity.ASSERTED]
    reject = [c for c in claims
              if c.claim_type == ClaimType.QUOTE_ACCEPTED and c.polarity is Polarity.NEGATED]
    future = [c for c in claims if c.claim_type == ClaimType.FUTURE_INTENT]

    ids = tuple(c.claim_id for c in claims
                if c.claim_type in (ClaimType.QUOTE_ACCEPTED, ClaimType.FUTURE_INTENT)
                and c.claim_id)
    state = information_state([c for c in claims
                               if c.claim_type == ClaimType.QUOTE_ACCEPTED])
    if reject and not accept:
        return "REJECT", state.value, ids
    if future and not accept:
        return "FUTURE_INTENT", state.value, ids
    if accept:
        return "ACCEPT", state.value, ids
    return None, state.value, ids


# ── the authorization predicate ───────────────────────────────────────────────

def authorize_quote_acceptance(
    claims: Iterable[ClaimEvidence],
    state: CommercialState,
) -> AuthorizationDecision:
    """The full predicate. Every prerequisite is positive; every blocker is explicit.

        ALLOW  ⟺  stance == ACCEPT
                  ∧ acceptance evidence is ASSERTED ∧ PRESENT ∧ FACTUAL
                  ∧ acceptance evidence is not SEMANTIC_INFERRED alone
                  ∧ a quote exists for this cycle
                  ∧ that quote was DELIVERED to the customer
                  ∧ the quote's inputs are unchanged (candidate, category, zone)
                  ∧ the acceptance belongs to the same cycle as the quote
                  ∧ no unresolved candidate conflict
                  ∧ no unresolved inspection-location conflict
                  ∧ SEARCHING_NOT_READY is not TRUE_ONLY
    """
    claims = list(claims)
    stance, stance_state, evidence_ids = reconcile_stance(claims)
    identity = state.quote_identity()
    satisfied: list[str] = []
    failed: list[str] = []
    blockers: list[str] = []

    def decide(result: str, reason: str) -> AuthorizationDecision:
        return AuthorizationDecision(
            result=result, reason=reason, stance=stance, stance_state=stance_state,
            quote_identity=identity, satisfied=tuple(satisfied), failed=tuple(failed),
            blockers=tuple(blockers), evidence_ids=evidence_ids)

    # ── blockers first: they need no positive proof of their own ──────────────
    readiness = information_state(
        [c for c in claims if c.claim_type == ClaimType.SEARCHING_NOT_READY])
    if readiness is InformationState.TRUE_ONLY:
        blockers.append("searching_not_ready")
        return decide(HOLD, "the customer is still choosing a car")
    if state.candidate_conflict:
        blockers.append("candidate_conflict")
        return decide(CLARIFY, "unresolved candidate conflict")
    if state.location_conflict:
        blockers.append("location_conflict")
        return decide(CLARIFY, "unresolved inspection-location conflict")

    # ── prerequisite 1: an acceptance stance exists at all ────────────────────
    if stance == "REJECT":
        failed.append("stance_is_accept")
        return decide(DENY, "the customer declined")
    if stance is None:
        failed.append("stance_is_accept")
        # Absence of acceptance evidence authorises nothing — and says nothing either.
        return decide(HOLD, "no acceptance evidence in this turn")
    if stance != "ACCEPT":
        failed.append("stance_is_accept")
        return decide(HOLD, f"stance {stance} does not accept anything")
    satisfied.append("stance_is_accept")

    accept_claims = [c for c in claims if c.claim_type == ClaimType.QUOTE_ACCEPTED
                     and c.polarity is Polarity.ASSERTED]

    # ── prerequisite 2: the acceptance is about NOW, and is factual ───────────
    actionable = [c for c in accept_claims if c.is_actionable_now]
    if not actionable:
        failed.append("acceptance_is_present_and_factual")
        return decide(HOLD, "acceptance is conditional or about the future")
    satisfied.append("acceptance_is_present_and_factual")

    # ── prerequisite 3: read from the customer, not computed from other facts ──
    # A stance is always an interpretation of words — the value is a signal, not a substring,
    # so "explicit" cannot mean "found verbatim". What must never authorise is a stance
    # DERIVED from other evidence (a state machine concluding agreement from a day being
    # proposed, say). That is the acceptance nobody actually gave.
    if all(c.explicitness is Explicitness.DERIVED for c in actionable):
        failed.append("acceptance_read_not_derived")
        return decide(CLARIFY, "acceptance derived from other facts, not stated by the customer")
    satisfied.append("acceptance_read_not_derived")

    # ── prerequisite 4: a quote exists ────────────────────────────────────────
    if state.quote_total is None:
        failed.append("quote_exists")
        return decide(DENY, "no quote exists to accept")
    satisfied.append("quote_exists")

    # ── prerequisite 5: the quote was DELIVERED, not merely computed ──────────
    if not state.quote_is_delivered():
        failed.append("quote_delivered")
        return decide(DENY, "the quote was computed but never delivered to the customer")
    satisfied.append("quote_delivered")

    # ── prerequisite 6: same cycle ────────────────────────────────────────────
    if (state.quote_cycle_id is not None and state.cycle_id is not None
            and state.quote_cycle_id != state.cycle_id):
        failed.append("quote_in_current_cycle")
        return decide(DENY, "the quote belongs to a previous cycle")
    if any(c.cycle_id is not None and state.cycle_id is not None
           and c.cycle_id != state.cycle_id for c in actionable):
        failed.append("acceptance_in_current_cycle")
        return decide(DENY, "the acceptance belongs to a previous cycle")
    satisfied.append("quote_in_current_cycle")

    # ── prerequisite 7: the quote is still the one that was delivered ─────────
    if not state.quote_inputs_unchanged():
        failed.append("quote_inputs_unchanged")
        return decide(DENY, "the quote is stale: candidate, category or zone changed since")
    satisfied.append("quote_inputs_unchanged")

    return decide(ALLOW, "explicit present acceptance of a delivered, current quote")


def to_shadow_record(decision: AuthorizationDecision, state: CommercialState,
                     *, legacy_decision: Optional[str] = None,
                     comparison: Optional[str] = None) -> dict:
    """Observability payload. Decisions and identifiers only — no customer text."""
    return {
        "record_version": "authorization-record/1.0",
        "rule_id": decision.rule_id, "rule_version": decision.rule_version,
        "risk_tier": decision.risk_tier,
        "result": decision.result, "reason": decision.reason,
        "stance": decision.stance, "stance_state": decision.stance_state,
        "quote_identity": decision.quote_identity,
        "satisfied": list(decision.satisfied), "failed": list(decision.failed),
        "blockers": list(decision.blockers),
        "evidence_ids": list(decision.evidence_ids),
        "cycle_id": state.cycle_id, "revision_id": state.revision_id,
        "candidate_id": state.candidate_id, "stage": state.stage,
        "lead_flag": state.lead_flag,
        "legacy_decision": legacy_decision, "comparison": comparison,
        "shadow": True,
    }
