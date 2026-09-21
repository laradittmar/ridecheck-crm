"""L4.7W5 Gate 1 — the Hybrid Decision Trace contract, version `hybrid-decision-trace/1.0`.

One record per customer turn, keyed by the `turn_id` the engine already mints. It exists to
answer one question from one screen:

    what messages formed this turn, what did the semantic engine produce, what did CE
    produce, which authority rule decided — or failed to exist — what canonical state
    changed, and why was the final action selected?

Two design rules the audit made non-negotiable.

**The burst is proven, not copied.** `ordered_message_ids` plus `input_hash` establish the
exact input both engines received; the readable bodies stay in `whatsapp_messages`, where
they already live. Storing them twice would duplicate PII for no evidentiary gain.

**Absence is a first-class result.** `NO_RULE` is emitted when a turn produced evidence that
no reconciliation authority owns, and `DETERMINISTIC_FLOOR` marks a canonical action taken
by a CE safety floor. The failed Wild of 2026-09-17 was invisible precisely because neither
had a name: the turn simply produced no record at all. A floor must never render as hybrid
agreement — that is how a floor gets mistaken for capability.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

TRACE_VERSION = "hybrid-decision-trace/1.0"


class Classification:
    """How the two evidence producers related on one claim family.

    Row-level values describe ONE reconciliation. `PARTIAL_RECONCILIATION` and
    `SEMANTIC_NOT_ROUTED` are turn-level only: the first is the honest headline when some
    families were compared and others could not be, the second names why.

    The distinction they exist to protect: a producer that said nothing, and a producer
    whose claims never reached this reconciler, are both "not compared" — and neither is
    disagreement. The first deployed trace was labelled CONFLICT for exactly that mistake.
    """
    AGREE = "AGREE"
    CONFLICT = "CONFLICT"
    PARTIAL_RECONCILIATION = "PARTIAL_RECONCILIATION"   # turn-level only
    SEMANTIC_MISSING = "SEMANTIC_MISSING"
    SEMANTIC_NOT_ROUTED = "SEMANTIC_NOT_ROUTED"         # turn-level only
    SEMANTIC_ERROR = "SEMANTIC_ERROR"
    CE_MISSING = "CE_MISSING"
    NO_RULE = "NO_RULE"

    ALL = (AGREE, CONFLICT, PARTIAL_RECONCILIATION, SEMANTIC_MISSING,
           SEMANTIC_NOT_ROUTED, SEMANTIC_ERROR, CE_MISSING, NO_RULE)

    #: Row values meaning "this family was compared end to end".
    COMPARED = (AGREE, CONFLICT)
    #: Row values meaning "one producer's claim was absent, unavailable or unrouted".
    INCOMPARABLE = (SEMANTIC_MISSING, SEMANTIC_NOT_ROUTED, SEMANTIC_ERROR,
                    CE_MISSING, NO_RULE)


class ResultKind:
    """How the canonical outcome was produced. Observability, never a routing input."""
    RECONCILED = "RECONCILED"
    DETERMINISTIC_FLOOR = "DETERMINISTIC_FLOOR"
    CLARIFICATION = "CLARIFICATION"
    HANDOFF = "HANDOFF"
    BLOCKED = "BLOCKED"
    FALLBACK = "FALLBACK"
    NO_ACTION = "NO_ACTION"

    ALL = (RECONCILED, DETERMINISTIC_FLOOR, CLARIFICATION, HANDOFF, BLOCKED, FALLBACK,
           NO_ACTION)


def normalize_burst(messages) -> str:
    """The canonical burst string that is hashed. Mirrors the engine's own normalization.

    Deliberately reproduces `conversation_engine._norm_lower` rather than importing it: the
    hash is a stable evidentiary artifact, and it must not silently change meaning if that
    private helper is ever tuned. A test pins the two against each other.
    """
    joined = " | ".join(m for m in (messages or []) if isinstance(m, str))
    stripped = unicodedata.normalize("NFD", joined)
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    stripped = stripped.lower()
    return " ".join(stripped.split())


def burst_hash(messages) -> str:
    """SHA-256 of the normalized burst — proves WHICH text without storing it.

    Order-sensitive by construction: the separator keeps message boundaries inside the
    hashed string, so two messages swapped produce a different digest. That is the point —
    F7G-R2 routes on message boundaries, so the evidence must be able to prove them.
    """
    return hashlib.sha256(normalize_burst(messages).encode("utf-8")).hexdigest()


def token_fingerprint(token: Optional[str]) -> Optional[str]:
    """Eight characters of a salted-by-purpose digest. Never the token, never reversible."""
    if not token:
        return None
    return hashlib.sha256(f"booking-token:{token}".encode("utf-8")).hexdigest()[:8]


_DIGEST_SAFE = re.compile(r"\s+")


def inputs_digest(*parts: Any) -> str:
    """Short digest of a rule's inputs — enough to see two rules saw the same thing."""
    blob = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(_DIGEST_SAFE.sub(" ", blob).strip().encode("utf-8")).hexdigest()[:12]


def _jsonable(value):
    """Tuples → lists, recursively. Everything else is already JSON-native."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


@dataclass
class RuleEvidence:
    """One deterministic CE rule evaluation, named for the business, not for Python."""
    rule_id: str
    rule_version: str
    value: Any
    inputs_digest: Optional[str] = None
    note: Optional[str] = None


@dataclass
class ReconciliationEvidence:
    claim_family: str
    semantic_input: str            # PRESENT | ABSENT | ERROR
    ce_input: str                  # PRESENT | ABSENT
    classification: str
    rule_id: Optional[str] = None
    rule_version: Optional[str] = None
    outcome: Optional[str] = None
    accepted: tuple = ()
    rejected: tuple = ()
    reason_code: Optional[str] = None


@dataclass
class SemanticEvidence:
    ok: bool = False
    # PENDING is a real, frequent outcome and not a defect: the interpreter is dispatched
    # asynchronously, so a decision can legitimately be taken before it answers.
    status: str = "ABSENT"         # OK | ABSENT | PENDING | ERROR | TIMEOUT | MALFORMED
    model: Optional[str] = None
    prompt_version: Optional[str] = None
    schema_version: Optional[str] = None
    dispatch: Optional[str] = None
    latency_ms: Optional[int] = None
    total_tokens: Optional[int] = None
    error_category: Optional[str] = None
    evidence: Optional[dict] = None          # validated structured TurnEvidence only
    produced_claims: tuple = ()


@dataclass
class CanonicalSnapshot:
    """Allowlisted state, never the ORM object.

    Every field earns its place by being readable in the inspector's "what changed" column:
    `stage` and `needs_human` are the transition itself; `lead_estado`/`lead_necesita_humano`
    are the operator-visible half of ownership; `candidate_id` and `revision_id` say which
    vehicle and which job; `zone_group`/`zone_detail` explain pricing and availability;
    `offer_outstanding`, `active_requested_date` and `offered_slots_count` are the context
    every rejection rule depends on; `booking_token_present` plus `booking_token_fingerprint`
    prove withdrawal without ever disclosing the token.
    """
    stage: Optional[str] = None
    needs_human: Optional[bool] = None
    lead_estado: Optional[str] = None
    lead_necesita_humano: Optional[bool] = None
    candidate_id: Optional[int] = None
    revision_id: Optional[int] = None
    zone_group: Optional[str] = None
    zone_detail: Optional[str] = None
    offer_outstanding: Optional[bool] = None
    active_requested_date: Optional[str] = None
    offered_slots_count: Optional[int] = None
    booking_token_present: Optional[bool] = None
    booking_token_fingerprint: Optional[str] = None


@dataclass
class HybridDecisionTrace:
    trace_version: str = TRACE_VERSION
    turn_id: str = ""
    thread_id: Optional[int] = None
    lead_id: Optional[int] = None
    deployment_sha: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None

    ordered_message_ids: tuple = ()
    message_timestamps: tuple = ()
    message_count: int = 0
    input_hash: Optional[str] = None

    semantic: SemanticEvidence = field(default_factory=SemanticEvidence)
    ce_evidence: tuple = ()
    reconciliation: tuple = ()
    # Why one or more claim families could not be compared end to end. Empty when every
    # family that ran was compared. Read alongside the headline, never instead of it.
    supporting_conditions: tuple = ()

    canonical_before: CanonicalSnapshot = field(default_factory=CanonicalSnapshot)
    canonical_after: CanonicalSnapshot = field(default_factory=CanonicalSnapshot)

    transition: Optional[str] = None
    allowed: Optional[bool] = None
    result_kind: str = ResultKind.NO_ACTION
    reason_code: Optional[str] = None
    response_plan_kind: Optional[str] = None
    response_plan_source: Optional[str] = None
    outbound_message_id: Optional[int] = None
    outbound_path_id: Optional[str] = None
    outbound_status: Optional[str] = None
    outbound_wamid_tail: Optional[str] = None

    badges: tuple = ()

    def to_payload(self) -> dict:
        """JSON-native, so the payload read back from the database is the payload built here.

        `asdict` leaves tuples as tuples; the database round-trip turns them into lists. A
        trace that changes shape on the way through storage is a trace an operator cannot
        compare against itself, so the conversion happens once, here.
        """
        return _jsonable(asdict(self))
