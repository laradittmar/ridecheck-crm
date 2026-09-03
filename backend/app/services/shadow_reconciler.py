"""L4.7C.1 — shadow reconciliation: decisions recorded, nothing decided.

    ClaimEvidence[]  ──►  reconcile()  ──►  ReconciliationRecord[]  ──►  append-only log

What this module does: groups claims by canonical claim type, computes the four-valued
information state, chooses an outcome under a named and versioned rule, and records why.

What it does **not** do, in C1 and by construction: write canonical state. There is no ORM
import, no service call, no candidate, no quote, no booking, no outbound. Every record it
produces carries `shadow=True`. The authority to act on these decisions arrives in C2/C3,
behind its own flags, after this layer has been observed.

Rules are named and versioned (`reconcile.<claim>.v1`) so that a decision can be re-explained
offline and a policy change is visible as a version bump rather than an edited conditional.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from ..schemas.claims import (
    ClaimEvidence,
    ClaimType,
    EvidenceClass,
    InformationState,
    ReconciliationOutcome,
    RiskTier,
    alternatives_for,
    information_state,
    risk_tier_for,
)
# Resolved lazily inside reconcile(): the record and the log must come from the SAME module
# object, or pydantic rejects a record built from a stale class (a module reload in one test
# suite silently split the two classes in the full run).
from ..schemas import turn_evidence as _te
from ..schemas.turn_evidence import ReconciliationLog, ReconciliationRecord

logger = logging.getLogger(__name__)

RECONCILER_ID = "reconciler:shadow"
RECONCILER_VERSION = "v1"

# What a canonical value of each claim type would depend on. Read by C5 for invalidation;
# recorded now so the dependency is visible from the first record onward.
DEPENDENCIES: dict[str, tuple[str, ...]] = {
    ClaimType.VEHICLE_CATEGORY: (ClaimType.VEHICLE_MAKE, ClaimType.VEHICLE_MODEL,
                                 ClaimType.VEHICLE_YEAR),
    ClaimType.QUOTE_REQUEST: (),
    ClaimType.QUOTE_ACCEPTED: (ClaimType.VEHICLE_CATEGORY, ClaimType.INSPECTION_LOCATION),
    ClaimType.SCHEDULING_PREFERENCE: (ClaimType.INSPECTION_LOCATION,),
}

RULE_IDS: dict[str, str] = {
    ClaimType.SERVICE_INTENT: "reconcile.service_intent",
    ClaimType.VEHICLE_MAKE: "reconcile.vehicle_make",
    ClaimType.VEHICLE_MODEL: "reconcile.vehicle_model",
    ClaimType.VEHICLE_YEAR: "reconcile.vehicle_year",
    ClaimType.VEHICLE_CATEGORY: "reconcile.vehicle_category",
    ClaimType.INSPECTION_LOCATION: "reconcile.location_role",
    ClaimType.CUSTOMER_ORIGIN: "reconcile.location_role",
    ClaimType.SELLER_LOCATION: "reconcile.location_role",
    ClaimType.QUOTE_REQUEST: "reconcile.quote_request",
    ClaimType.QUOTE_ACCEPTED: "reconcile.quote_accepted",
    ClaimType.FUTURE_INTENT: "reconcile.stance",
    ClaimType.SEARCHING_NOT_READY: "reconcile.readiness",
    ClaimType.CORRECTION: "reconcile.correction",
    ClaimType.SCHEDULING_PREFERENCE: "reconcile.scheduling_preference",
    ClaimType.INSPECTABILITY: "reconcile.inspectability",
    ClaimType.FAQ_TOPIC: "reconcile.faq_topic",
    ClaimType.NEEDS_HUMAN: "reconcile.needs_human",
}


def _rule_for(claim_type: str) -> tuple[str, str]:
    return RULE_IDS.get(claim_type, f"reconcile.{claim_type}"), RECONCILER_VERSION


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class ReconciliationResult:
    """One shadow reconciliation pass. Carries no authority and mutates nothing."""
    log: Any
    records: tuple = ()
    claim_count: int = 0
    states: dict = dc_field(default_factory=dict)      # claim_type → InformationState
    outcomes: dict = dc_field(default_factory=dict)    # claim_type → ReconciliationOutcome
    shadow: bool = True


def _outcome_for(state: InformationState, tier: RiskTier,
                 claims: list[ClaimEvidence]) -> tuple[ReconciliationOutcome, str]:
    """Choose an outcome from the state and the consequence — never from confidence.

    C1 deliberately implements only the informational half of the L4.7C policy: nothing
    here authorises an action, so ACCEPT means "this claim is recorded as sufficiently
    supported", not "act on it".
    """
    if state is InformationState.BOTH:
        if tier is RiskTier.HIGH:
            return (ReconciliationOutcome.NEEDS_HUMAN,
                    "contradictory evidence on a high-risk claim")
        return ReconciliationOutcome.CLARIFY, "contradictory evidence"
    if state is InformationState.NEITHER:
        # Absence is not falsehood, and it is not a question either: it is simply nothing
        # to record yet.
        if any(c.status.value in ("AMBIGUOUS", "CONFLICT") for c in claims):
            return ReconciliationOutcome.CLARIFY, "ambiguous evidence, no resolvable value"
        return ReconciliationOutcome.HOLD, "no evidence"
    if state is InformationState.FALSE_ONLY:
        return ReconciliationOutcome.ACCEPT, "negative evidence recorded"
    if tier is RiskTier.HIGH and not any(c.is_actionable_now for c in claims):
        # A future or conditional statement can be recorded, but it can never be the basis
        # of a high-risk fact. C3 turns this into an authorization rule; C1 records it.
        return (ReconciliationOutcome.HOLD,
                "high-risk claim supported only by future/conditional evidence")
    return ReconciliationOutcome.ACCEPT, "supported by evidence"


def _canonical_value(state: InformationState, outcome: ReconciliationOutcome,
                     claims: list[ClaimEvidence]) -> Optional[object]:
    """The value a canonical projection WOULD take. Recorded, never written (C1).

    Only an ACCEPTed claim projects a value. A HOLD — a high-risk claim supported only by
    future or conditional evidence, say — projects nothing at all: recording a value beside
    a decision not to take it is exactly the ambiguity that later turns into a wrong action.
    """
    if state is not InformationState.TRUE_ONLY or outcome is not ReconciliationOutcome.ACCEPT:
        return None
    for preference in (EvidenceClass.HUMAN_CONFIRMED, EvidenceClass.CATALOG_CONFIRMED,
                       EvidenceClass.EXPLICIT_CUSTOMER,
                       EvidenceClass.DETERMINISTIC_EXTRACTED,
                       EvidenceClass.SERVICE_COMPUTED, EvidenceClass.SEMANTIC_INFERRED):
        for claim in claims:
            if claim.evidence_class is preference and claim.value not in (None, "", [], {}):
                return claim.value
    return None


def reconcile(
    claims: Iterable[ClaimEvidence],
    *,
    cycle_id: Optional[str] = None,
    revision_id: Optional[int] = None,
    log: Optional[ReconciliationLog] = None,
) -> ReconciliationResult:
    """Group, decide, record. Pure: no I/O, no ORM, no service, no mutation."""
    claims = [c for c in claims if isinstance(c, ClaimEvidence)]
    if cycle_id is not None:
        claims = [c for c in claims if c.cycle_id in (None, cycle_id)]

    grouped: dict[str, list[ClaimEvidence]] = {}
    for claim in claims:
        grouped.setdefault(claim.claim_type, []).append(claim)

    record_cls = _te.ReconciliationRecord
    log_cls = _te.ReconciliationLog
    status_enum = _te.ReconciliationStatus
    current = log if log is not None else log_cls()
    records: list = []
    states: dict[str, str] = {}
    outcomes: dict[str, str] = {}
    decided_at = _now()

    for claim_type in sorted(grouped):
        group = grouped[claim_type]
        state = information_state(group)
        tier = risk_tier_for(claim_type)
        outcome, reason = _outcome_for(state, tier, group)
        rule_id, rule_version = _rule_for(claim_type)
        canonical = _canonical_value(state, outcome, group)

        record = record_cls(
            evidence_ref=claim_type,
            status=(status_enum.ACCEPTED
                    if outcome is ReconciliationOutcome.ACCEPT else
                    status_enum.NEEDS_CLARIFICATION
                    if outcome is ReconciliationOutcome.CLARIFY else
                    status_enum.CONFLICT_UNRESOLVED
                    if outcome is ReconciliationOutcome.NEEDS_HUMAN else
                    status_enum.DEFERRED),
            reason=reason,
            decided_by=f"{RECONCILER_ID}:{rule_id}",
            decided_at=decided_at,
            canonical_value=canonical,
            claim_type=claim_type,
            evidence_ids=tuple(c.claim_id for c in group if c.claim_id),
            candidate_values=tuple(
                c.value for c in group if c.value not in (None, "", [], {}))
            + alternatives_for(group),
            rule_id=rule_id,
            rule_version=rule_version,
            information_state=state.value,
            outcome=outcome.value,
            risk_tier=tier.value,
            cycle_id=cycle_id,
            revision_id=revision_id,
            depends_on=DEPENDENCIES.get(claim_type, ()),
            supersedes=tuple(sid for c in group for sid in c.supersedes),
            shadow=True,
        )
        current = current.append(record)
        records.append(record)
        states[claim_type] = state.value
        outcomes[claim_type] = outcome.value

    return ReconciliationResult(log=current, records=tuple(records),
                                claim_count=len(claims), states=states,
                                outcomes=outcomes, shadow=True)


def summarise(result: ReconciliationResult) -> dict:
    """Compact, PII-free shape for the shadow record: types and decisions, never values."""
    return {
        "reconciler": f"{RECONCILER_ID}:{RECONCILER_VERSION}",
        "claim_count": result.claim_count,
        "claim_types": sorted(result.states),
        "information_states": dict(sorted(result.states.items())),
        "outcomes": dict(sorted(result.outcomes.items())),
        "rule_ids": sorted({r.rule_id for r in result.records if r.rule_id}),
        "risk_tiers": {r.claim_type: r.risk_tier for r in result.records if r.claim_type},
        "shadow": True,
    }
