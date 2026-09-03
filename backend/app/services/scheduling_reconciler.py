"""L4.7C.4 — one interpretation of what the customer asked for, and one resolver.

Four stages, and the whole point is that they stay apart:

    SEMANTIC      what preference was expressed        (day expression, time, order)
    RESOLVER      what date that expression means      (deterministic calendar arithmetic)
    SCHEDULE      what is actually possible            (ScheduleService — untouched here)
    BOOKING       what was confirmed                   (Flow — untouched here)

This module owns the first two and nothing else. It cannot say a slot is available and it
cannot say anything is booked: it has no access to either, by construction — no ORM import,
no ScheduleService, no booking symbol.

Two invariants carry over from the semantic work and are enforced here rather than hoped for:

* **order is preference, not availability** — the first branch the customer said is PRIMARY,
  and no later stage may reorder it;
* **a time belongs to its own clause** — "mañana 15 o el jueves" asks for 15:00 tomorrow and
  *any* time on Thursday, never 15:00 twice.

L4.7C.4A adds the third: two producers read the *same* burst, so recency cannot decide
between them. The semantic reading is authoritative while it is a faithful enrichment of the
deterministic one; a real contradiction keeps the certified deterministic reading and is
recorded rather than resolved in the model's favour (`semantic_covers_deterministic`).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Optional

from ..schemas.claims import ClaimEvidence, ClaimType, EvidenceClass, InformationState

logger = logging.getLogger(__name__)

RULE_ID = "reconcile.scheduling_preference"
RULE_VERSION = "v2"          # v2 (L4.7C.4A): same-turn producer precedence

# The controlled day vocabulary. Weekday names carry their Python weekday index.
RELATIVE_DAYS = {"TODAY": 0, "TOMORROW": 1, "DAY_AFTER_TOMORROW": 2}
WEEKDAYS = {"MONDAY": 0, "TUESDAY": 1, "WEDNESDAY": 2, "THURSDAY": 3,
            "FRIDAY": 4, "SATURDAY": 5, "SUNDAY": 6}

PRIORITY_ORDER = ("PRIMARY", "FALLBACK", "ADDITIONAL")


def resolve_day_expression(expression: Optional[str], today: date) -> Optional[str]:
    """The one deterministic resolver. No model reasoning touches a calendar.

    Relative expressions count forward from `today`; a weekday name means its **next**
    occurrence, and never today — the same convention the legacy parser has always used, so
    a Thursday request made on a Thursday means next Thursday. An explicit ISO date passes
    through unchanged; anything outside the vocabulary resolves to nothing at all.
    """
    if not expression:
        return None
    token = str(expression).strip().upper()
    if token in RELATIVE_DAYS:
        return (today + timedelta(days=RELATIVE_DAYS[token])).isoformat()
    if token in WEEKDAYS:
        days_ahead = WEEKDAYS[token] - today.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        return (today + timedelta(days=days_ahead)).isoformat()
    if token == "EXPLICIT_DATE":
        return None                      # the date itself travels in `resolved_date`
    try:                                  # already an ISO date?
        return date.fromisoformat(str(expression)).isoformat()
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class ScheduledBranch:
    """One requested branch, in the order it was spoken. A request — never an offer."""
    priority: str = "PRIMARY"
    rank: int = 1
    day_expression: Optional[str] = None
    resolved_date: Optional[str] = None
    time: Optional[str] = None
    flexible_time: bool = True
    time_band: Optional[str] = None
    superseded: bool = False

    @property
    def is_request_only(self) -> bool:
        """A branch is a request. It asserts nothing about availability or booking."""
        return True


@dataclass(frozen=True)
class SchedulingDecision:
    branches: tuple[ScheduledBranch, ...] = ()
    superseded: tuple[ScheduledBranch, ...] = ()
    rule_id: str = RULE_ID
    rule_version: str = RULE_VERSION
    information_state: str = InformationState.NEITHER.value
    source: str = "none"                  # semantic | deterministic | none
    reason: str = ""
    evidence_ids: tuple[str, ...] = ()

    @property
    def primary(self) -> Optional[ScheduledBranch]:
        return self.branches[0] if self.branches else None


def _branch_from_mapping(raw: Any, index: int, today: date) -> Optional[ScheduledBranch]:
    if not isinstance(raw, dict):
        return None
    day = raw.get("day") or raw.get("day_expression")
    stated_time = raw.get("time")
    explicit_date = raw.get("resolved_date")
    if not day and not stated_time and not explicit_date:
        return None
    priority = str(raw.get("priority")
                   or (PRIORITY_ORDER[index] if index < len(PRIORITY_ORDER)
                       else "ADDITIONAL")).upper()
    resolved = explicit_date or resolve_day_expression(day, today)
    return ScheduledBranch(
        priority=priority,
        rank=int(raw.get("rank") or index + 1),
        day_expression=(str(day).upper() if day else None),
        resolved_date=resolved,
        time=stated_time,
        # A day without a stated time is a day, not a guess. Flexibility is the default and
        # is only switched off by an explicit time.
        flexible_time=bool(raw.get("flexible") if raw.get("flexible") is not None
                           else raw.get("flexible_time", stated_time is None)),
        time_band=raw.get("time_band"))


def _is_semantic(claim: ClaimEvidence) -> bool:
    """Compared by value: an enum that survived a module reload is still the same class."""
    actual = getattr(claim.evidence_class, "value", claim.evidence_class)
    return actual == EvidenceClass.SEMANTIC_INFERRED.value


def _branches_of(claim: ClaimEvidence, today: date) -> list[ScheduledBranch]:
    out: list[ScheduledBranch] = []
    for index, raw in enumerate(claim.value or ()):
        branch = _branch_from_mapping(raw, index, today)
        if branch is not None:
            out.append(branch)
    return out


def semantic_covers_deterministic(semantic: list[ScheduledBranch],
                                  deterministic: list[ScheduledBranch]) -> bool:
    """Is the semantic reading a faithful *enrichment* of the deterministic one?

    L4.7C.4A precedence rule. Two producers read the SAME burst, so neither is "later" —
    "last writer wins" would be meaningless here. The question that can actually be answered
    is whether they disagree:

    * every deterministic branch must appear in the semantic reading, on the same resolved
      date, in the same relative order;
    * a deterministic branch with no time is satisfied by any time — **absence is never a
      contradiction**, the same invariant the information-state model rests on;
    * a deterministic branch with a *different* stated time is a real contradiction.

    Enrichment (the semantic reading adds a branch the parser dropped) is accepted. Any
    contradiction, or a deterministic branch the model lost, is not silently resolved in the
    model's favour — the caller keeps the certified deterministic reading.
    """
    cursor = 0
    for det in deterministic:
        matched = -1
        for index in range(cursor, len(semantic)):
            sem = semantic[index]
            if det.resolved_date and sem.resolved_date != det.resolved_date:
                continue
            if det.time is not None and sem.time != det.time:
                continue
            matched = index
            break
        if matched < 0:
            return False
        cursor = matched + 1
    return True


def reconcile_scheduling(
    claims: Iterable[ClaimEvidence],
    *,
    today: date,
) -> SchedulingDecision:
    """Turn scheduling claims into ordered, date-resolved branches. Availability-free."""
    claims = [c for c in claims if c.claim_type == ClaimType.SCHEDULING_PREFERENCE]
    if not claims:
        return SchedulingDecision(reason="no scheduling evidence")

    # The most recent claim wins the *content*; earlier ones are kept as superseded, so a
    # correction ("mejor el jueves") never erases what it replaced.
    current = claims[-1]
    superseded_claims = claims[:-1]
    conflict = False

    # L4.7C.4A — when BOTH producers spoke about this same burst, recency is meaningless.
    # The richer semantic reading is authoritative only while it does not contradict the
    # deterministic one; otherwise the certified deterministic reading stands.
    semantic_claims = [c for c in claims if _is_semantic(c)]
    other_claims = [c for c in claims if not _is_semantic(c)]
    if semantic_claims and other_claims:
        sem_claim, det_claim = semantic_claims[-1], other_claims[-1]
        if semantic_covers_deterministic(_branches_of(sem_claim, today),
                                         _branches_of(det_claim, today)):
            current = sem_claim
            superseded_claims = [c for c in claims if c is not sem_claim]
        else:
            conflict = True
            current = det_claim
            superseded_claims = [c for c in claims if c is not det_claim]
    branches: list[ScheduledBranch] = []
    for index, raw in enumerate(current.value or ()):
        branch = _branch_from_mapping(raw, index, today)
        if branch is not None:
            branches.append(branch)

    superseded: list[ScheduledBranch] = []
    for claim in superseded_claims:
        for index, raw in enumerate(claim.value or ()):
            branch = _branch_from_mapping(raw, index, today)
            if branch is not None:
                superseded.append(ScheduledBranch(**{**branch.__dict__, "superseded": True}))

    if not branches:
        return SchedulingDecision(reason="no resolvable branch",
                                  superseded=tuple(superseded),
                                  evidence_ids=tuple(c.claim_id for c in claims if c.claim_id))

    source = "semantic" if _is_semantic(current) else "deterministic"
    if conflict:
        source = "deterministic_conflict"
    return SchedulingDecision(
        branches=tuple(branches), superseded=tuple(superseded),
        information_state=InformationState.TRUE_ONLY.value,
        source=source,
        reason=("producers disagree; certified deterministic reading kept" if conflict
                else "ordered preference preserved; dates resolved deterministically"),
        evidence_ids=tuple(c.claim_id for c in claims if c.claim_id))


def to_record(decision: SchedulingDecision, *, cycle_id: Optional[str] = None,
              revision_id: Optional[int] = None) -> dict:
    """Observability payload: the preference and how it was read. No availability, no PII."""
    return {
        "record_version": "scheduling-record/1.0",
        "rule_id": decision.rule_id, "rule_version": decision.rule_version,
        "source": decision.source, "information_state": decision.information_state,
        "branches": [{"priority": b.priority, "rank": b.rank, "day": b.day_expression,
                      "resolved_date": b.resolved_date, "time": b.time,
                      "flexible_time": b.flexible_time, "time_band": b.time_band}
                     for b in decision.branches],
        "superseded": [{"day": b.day_expression, "time": b.time}
                       for b in decision.superseded],
        "evidence_ids": list(decision.evidence_ids),
        "cycle_id": cycle_id, "revision_id": revision_id,
        "requested_only": True,          # never an offer, never a booking
    }
