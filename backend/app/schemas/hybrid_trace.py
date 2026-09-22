"""L4.7W5 — the Hybrid Decision Trace contract, version `hybrid-decision-trace/1.1`.

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

**1.1 — participation is what makes a comparison.** The first contract derived a row's
classification from `InformationState`, which describes the POLARITY of the evidence about
one claim type and says nothing about who supplied it. `TRUE_ONLY` therefore rendered as
`AGREE` on rows where exactly one producer had spoken, and `NEITHER` rendered as
`SEMANTIC_MISSING` on rows where nobody had. Both are false statements about producers.

1.1 records, per row, WHICH sources participated, WHAT each of them supplied, and WHICH
decision site asked — and derives the classification from that. `InformationState` remains
in the row as evidence about polarity; it can no longer decide agreement on its own.

A 1.0 row carries none of those fields. Their absence is not an empty modern row, and a
reader must never fill it in: `Classification.LEGACY_PROVENANCE_UNAVAILABLE` is the only
honest label for a 1.0 row in which anything at all participated.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

TRACE_VERSION = "hybrid-decision-trace/1.1"
#: The first contract. Its reconciliation rows carry no producer provenance, so a
#: reader must never treat their absent 1.1 fields as empty 1.1 fields.
TRACE_VERSION_1_0 = "hybrid-decision-trace/1.0"
READABLE_VERSIONS = (TRACE_VERSION, TRACE_VERSION_1_0)


class Classification:
    """What a reconciliation row proves about producer participation.

    Two vocabularies live here, and 1.1 keeps them apart on purpose.

    **Row values** describe ONE reconciliation call: `AGREE`, `CONFLICT`,
    `SINGLE_PRODUCER`, `NO_EVIDENCE`, `NOT_ROUTED`, `ERROR`, and — for a 1.0 row that
    cannot be read this way — `LEGACY_PROVENANCE_UNAVAILABLE`.

    **Turn values** summarise the rows: `PARTIAL_RECONCILIATION`, `SINGLE_SOURCE_DECISION`,
    `NO_COMPARISON`, `TRACE_INCOMPLETE`, and the 1.0 producer-level readings that still
    apply when a turn recorded no reconciliation at all.

    The distinction they exist to protect: a producer that said nothing, a producer whose
    claims never reached this reconciler, and a decision only one producer ever informed
    are three different facts — and none of them is agreement or disagreement. The first
    deployed trace was headlined CONFLICT for confusing the first two; its surviving
    `AGREE` row was the third.
    """
    # ── proven comparisons: two or more distinct sources actually participated ──
    AGREE = "AGREE"
    CONFLICT = "CONFLICT"
    # ── proven non-comparisons ──
    SINGLE_PRODUCER = "SINGLE_PRODUCER"          # exactly one source supplied evidence
    NO_EVIDENCE = "NO_EVIDENCE"                  # no source supplied any; blames nobody
    NOT_ROUTED = "NOT_ROUTED"                    # evidence existed in the turn, not here
    ERROR = "ERROR"                              # a producer or the reconciler failed
    # ── a 1.0 row in which something participated, but not provably what ──
    LEGACY_PROVENANCE_UNAVAILABLE = "LEGACY_PROVENANCE_UNAVAILABLE"

    # ── turn-level headlines ──
    PARTIAL_RECONCILIATION = "PARTIAL_RECONCILIATION"
    SINGLE_SOURCE_DECISION = "SINGLE_SOURCE_DECISION"
    NO_COMPARISON = "NO_COMPARISON"
    TRACE_INCOMPLETE = "TRACE_INCOMPLETE"
    SEMANTIC_MISSING = "SEMANTIC_MISSING"
    SEMANTIC_NOT_ROUTED = "SEMANTIC_NOT_ROUTED"
    SEMANTIC_ERROR = "SEMANTIC_ERROR"
    CE_MISSING = "CE_MISSING"
    NO_RULE = "NO_RULE"

    ROW = (AGREE, CONFLICT, SINGLE_PRODUCER, NO_EVIDENCE, NOT_ROUTED, ERROR,
           LEGACY_PROVENANCE_UNAVAILABLE)
    TURN = (AGREE, CONFLICT, PARTIAL_RECONCILIATION, SINGLE_SOURCE_DECISION,
            NO_COMPARISON, TRACE_INCOMPLETE, SEMANTIC_MISSING, SEMANTIC_NOT_ROUTED,
            SEMANTIC_ERROR, CE_MISSING, NO_RULE)
    ALL = tuple(dict.fromkeys(ROW + TURN))

    #: Row values meaning "two or more distinct sources were actually weighed".
    COMPARED = (AGREE, CONFLICT)
    #: Row values meaning "no comparison happened", for whichever reason.
    INCOMPARABLE = (SINGLE_PRODUCER, NO_EVIDENCE, NOT_ROUTED, ERROR,
                    LEGACY_PROVENANCE_UNAVAILABLE)
    #: Row values whose true classification cannot be established from the stored record.
    UNKNOWN = (LEGACY_PROVENANCE_UNAVAILABLE, ERROR)


class EvidenceSource:
    """Where one claim in a reconciliation came from. Proven, not assumed.

    Derived from `ClaimEvidence.producer`, which every producer sets explicitly as a
    namespaced literal, and never from `evidence_class` — a class describes how strong a
    claim is, not who made it. That distinction is not academic: CE's fuzzy catalog lookup
    produces `FUZZY_SUGGESTED` claims under the producer `ce:fuzzy_lookup_vehicle`, and
    reading the class alone reported them as semantic participation in a decision the
    semantic engine had no part in.

    Only namespaces the executable audit proved are listed. `SERVICE_COMPUTED` exists as an
    evidence class but no code constructs a claim with it, so there is no `SYSTEM_DERIVED`
    source here: an unproven vocabulary entry is an invitation to label something with it.
    """
    SEMANTIC = "SEMANTIC"                  # producer `semantic:*`
    DETERMINISTIC = "DETERMINISTIC"        # producer `ce:*`
    CANONICAL_STATE = "CANONICAL_STATE"    # producer `canonical:*`
    #: A claim whose producer is missing or outside the proven namespaces. It is recorded,
    #: and it never counts as a participant: an unattributed claim cannot prove agreement.
    UNATTRIBUTED = "UNATTRIBUTED"

    ALL = (SEMANTIC, DETERMINISTIC, CANONICAL_STATE, UNATTRIBUTED)
    #: Sources that may be counted when deciding whether a comparison happened.
    PARTICIPATING = (SEMANTIC, DETERMINISTIC, CANONICAL_STATE)


#: Producer namespace → source. The prefix is the part before the first ":".
PRODUCER_NAMESPACE_TO_SOURCE = {
    "semantic": EvidenceSource.SEMANTIC,
    "ce": EvidenceSource.DETERMINISTIC,
    "canonical": EvidenceSource.CANONICAL_STATE,
}


def source_of_producer(producer: Optional[str]) -> str:
    """The proven source for a producer literal. Unknown namespaces are UNATTRIBUTED."""
    namespace = str(producer or "").split(":", 1)[0].strip().lower()
    return PRODUCER_NAMESPACE_TO_SOURCE.get(namespace, EvidenceSource.UNATTRIBUTED)


class DecisionSite:
    """Stable identifiers for the places that ask a reconciler to decide.

    Passed explicitly by the call site as a literal. Not a line number, not a stack frame,
    not a function `__name__`: a rename or a refactor must not change an operator's reading
    of a stored trace, and stack inspection has no place in a production path.

    Two reconciliations in one turn can share `claim_family="vehicle.model"` — one writing
    the canonical identity, one deciding whether a fuzzy catalog match is admissible at
    all. They are different questions with different consequences, and the Inspector could
    not tell them apart.
    """
    VEHICLE_IDENTITY_APPLY = "vehicle.identity.apply"
    LOCATION_INSPECTION_APPLY = "location.inspection.apply"
    VEHICLE_FUZZY_ADMISSIBILITY = "vehicle.fuzzy.admissibility"

    ALL = (VEHICLE_IDENTITY_APPLY, LOCATION_INSPECTION_APPLY,
           VEHICLE_FUZZY_ADMISSIBILITY)


#: What each decision site is actually deciding, in the operator's language.
DECISION_PURPOSE = {
    DecisionSite.VEHICLE_IDENTITY_APPLY:
        "escribir la identidad del vehículo en el candidato",
    DecisionSite.LOCATION_INSPECTION_APPLY:
        "escribir la localidad de inspección",
    DecisionSite.VEHICLE_FUZZY_ADMISSIBILITY:
        "admitir o rechazar una coincidencia difusa del catálogo como identidad",
}


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
class SourceContribution:
    """What ONE source supplied to ONE reconciliation call.

    The five per-claim tuples are index-aligned: entry `i` of `claim_types`, `claim_ids`,
    `value_keys`, `polarities` and `values` all describe the same claim.

    `values` holds only what the Inspector's privacy allowlist permits to be displayed;
    every other entry is `None`. `value_keys` always has an entry — a short, non-reversible
    digest of the claim type and value — so two different values can still be told apart
    from one value repeated, which is all a reader needs to see a comparison happen.
    """
    source: str
    producer: Optional[str] = None
    producer_version: Optional[str] = None
    evidence_classes: tuple = ()
    claim_types: tuple = ()
    claim_ids: tuple = ()
    value_keys: tuple = ()
    polarities: tuple = ()
    values: tuple = ()                  # displayable entries, `None` where withheld
    withheld: bool = False


@dataclass
class ReconciliationEvidence:
    """One reconciliation call, told as who participated and what they supplied.

    The 1.0 fields are kept so a 1.1 payload stays readable by a 1.0 consumer, but they are
    now DERIVED from participation rather than being the source of truth:
    `semantic_input` is PRESENT exactly when `EvidenceSource.SEMANTIC` participated.
    """
    claim_family: str
    semantic_input: str            # PRESENT | ABSENT — derived from participating_sources
    ce_input: str                  # PRESENT | ABSENT — derived from participating_sources
    classification: str
    rule_id: Optional[str] = None
    rule_version: Optional[str] = None
    outcome: Optional[str] = None
    accepted: tuple = ()
    rejected: tuple = ()
    reason_code: Optional[str] = None

    # ── hybrid-decision-trace/1.1 ────────────────────────────────────────────
    contract_version: str = TRACE_VERSION
    #: Stable within the turn; distinguishes two calls that share a claim family.
    comparison_id: Optional[str] = None
    #: An explicit literal from `DecisionSite`, supplied by the call site.
    decision_site_id: Optional[str] = None
    decision_purpose: Optional[str] = None
    #: Distinct sources that actually supplied usable evidence to THIS call.
    participating_sources: tuple = ()
    #: Sources that produced evidence elsewhere in the turn and did not reach this call.
    not_routed_sources: tuple = ()
    source_evidence: tuple = ()          # tuple[SourceContribution]
    #: Evidence about POLARITY. Never sufficient on its own to classify the row.
    information_state: Optional[str] = None
    error_category: Optional[str] = None


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
