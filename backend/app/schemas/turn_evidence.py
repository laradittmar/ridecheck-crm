"""L4.7A — TurnEvidence: the structured contract between raw language and canonical state.

    RAW EVIDENCE  →  **TURN EVIDENCE**  →  CANONICAL STATE
    (immutable)      (interpretation)      (deterministic reconciliation)

TurnEvidence is *not* canonical state. An interpreter — the LLM today, anything tomorrow —
may propose it; only deterministic reconciliation may create or update CRM state. Nothing
in this module writes to a database, calls a service, or imports ConversationEngine: it is
a pure schema, defined ahead of the pipeline change it will later serve.

Design rules
------------
* **Unknown stays unknown.** Every value is optional; absence is never filled with a
  default, and confidence is `None` when the interpreter cannot supply one.
* **No winner is chosen here.** AMBIGUOUS and CONFLICT keep their alternatives; resolution
  is reconciliation's job.
* **Everything coexists.** A burst can carry FAQ questions, a vehicle, a location, an
  acceptance and a scheduling preference at once — no single "intent" field erases the
  rest (the L4.6 FAQ-bypass defect class).
* **Interpretation is immutable.** Models are frozen. A later reconciliation outcome is
  recorded in a separate append-only log, never by rewriting what was interpreted.

See `docs/semantic/SEMANTIC_TRUTH_MODEL.md` for the governing contract.
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Any, Iterator, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "turn-evidence/1.0"


# ── enumerations ──────────────────────────────────────────────────────────────

class EvidenceStatus(str, Enum):
    """How strongly the interpreter stands behind an item (L4.7E truth model)."""
    CONFIRMED = "CONFIRMED"
    PROPOSED = "PROPOSED"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICT = "CONFLICT"


UNRESOLVED_STATUSES = frozenset({EvidenceStatus.AMBIGUOUS, EvidenceStatus.CONFLICT})


class SourceKind(str, Enum):
    SEMANTIC = "SEMANTIC"              # LLM / semantic interpreter
    DETERMINISTIC = "DETERMINISTIC"    # catalog, regex, zone resolver
    FLOW = "FLOW"                      # structured WhatsApp Flow submission
    HUMAN = "HUMAN"                    # operator entry
    TRANSPORT = "TRANSPORT"            # n8n transcription / metadata
    UNKNOWN = "UNKNOWN"


class LocationRole(str, Enum):
    INSPECTION_LOCATION = "INSPECTION_LOCATION"
    CUSTOMER_ORIGIN = "CUSTOMER_ORIGIN"
    SELLER_LOCATION = "SELLER_LOCATION"
    UNKNOWN_LOCATION_ROLE = "UNKNOWN_LOCATION_ROLE"


class ServiceIntentKind(str, Enum):
    INSPECTION = "INSPECTION"                  # pre-purchase inspection interest
    QUOTE_REQUEST = "QUOTE_REQUEST"
    READINESS = "READINESS"                    # e.g. SEARCHING_NOT_READY
    LOGISTICS_OFFER = "LOGISTICS_OFFER"        # customer offers transport, etc.
    OTHER = "OTHER"


class AcceptanceSignal(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    HESITATE = "HESITATE"
    QUESTION_ONLY = "QUESTION_ONLY"
    UNKNOWN = "UNKNOWN"


class CorrectionRelation(str, Enum):
    CORRECT_EXISTING = "CORRECT_EXISTING"
    REPLACE_CANDIDATE = "REPLACE_CANDIDATE"
    SWITCH_TO_PRIOR_CANDIDATE = "SWITCH_TO_PRIOR_CANDIDATE"
    ADD_SECOND_CANDIDATE = "ADD_SECOND_CANDIDATE"
    UNKNOWN_RELATION = "UNKNOWN_RELATION"


class SchedulingPriority(str, Enum):
    PRIMARY = "PRIMARY"
    FALLBACK = "FALLBACK"
    ADDITIONAL = "ADDITIONAL"


class IdentityKind(str, Enum):
    CUSTOMER_NAME = "CUSTOMER_NAME"
    CUSTOMER_EMAIL = "CUSTOMER_EMAIL"
    SELLER_TYPE = "SELLER_TYPE"
    SELLER_NAME = "SELLER_NAME"
    ADDRESS = "ADDRESS"
    OTHER_IDENTITY = "OTHER_IDENTITY"


class ReconciliationStatus(str, Enum):
    """Outcomes deterministic reconciliation may record — never inside TurnEvidence."""
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    CONFLICT_UNRESOLVED = "CONFLICT_UNRESOLVED"
    SUPERSEDED = "SUPERSEDED"


class BurstReconstruction(str, Enum):
    """How the burst handed to the interpreter was assembled (L4.7E: PARTIAL)."""
    LIVE_DEBOUNCE = "LIVE_DEBOUNCE"            # produced by the n8n 20s window
    REPLAY_CHRONOLOGICAL = "REPLAY_CHRONOLOGICAL"   # rebuilt by timestamp order
    REPLAY_CAUSAL_MARKER = "REPLAY_CAUSAL_MARKER"   # rebuilt via causal_inbound_wa_message_id
    CORPUS_FIXTURE = "CORPUS_FIXTURE"
    UNKNOWN = "UNKNOWN"


# ── provenance ────────────────────────────────────────────────────────────────

class _Frozen(BaseModel):
    """Interpretation is immutable once produced."""
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceSpan(_Frozen):
    """Where in the raw text an item came from. All parts optional — spans are best-effort."""
    message_id: Optional[str] = None      # WAMID or DB id, as available
    start: Optional[int] = None
    end: Optional[int] = None
    excerpt: Optional[str] = None


class Provenance(_Frozen):
    """Auditable origin of one evidence item."""
    source_kind: SourceKind = SourceKind.UNKNOWN
    interpreter: Optional[str] = None          # e.g. "semantic:gpt-4o-mini"
    model_version: Optional[str] = None
    schema_version: str = SCHEMA_VERSION
    source_message_ids: tuple[str, ...] = ()   # messages this item was read from
    spans: tuple[SourceSpan, ...] = ()


class TurnRef(_Frozen):
    """Identifies the burst that was interpreted, and how it was assembled."""
    thread_id: Optional[int] = None
    burst_id: Optional[str] = None
    ordered_message_ids: tuple[str, ...] = ()
    reconstruction: BurstReconstruction = BurstReconstruction.UNKNOWN
    corpus_case_id: Optional[str] = None


class Alternative(_Frozen):
    """One of several readings the interpreter could not choose between."""
    value: Any = None
    normalized_value: Any = None
    confidence: Optional[float] = None
    reason: Optional[str] = None


# ── generic item contract ─────────────────────────────────────────────────────

class EvidenceItem(_Frozen):
    """Base contract every semantic evidence item satisfies."""
    field: str
    value: Any = None
    normalized_value: Any = None
    role: Optional[str] = None
    status: EvidenceStatus = EvidenceStatus.PROPOSED
    confidence: Optional[float] = None          # None = interpreter gave none; never faked
    alternatives: tuple[Alternative, ...] = ()
    catalog_candidate: Optional[str] = None
    reason: Optional[str] = None
    provenance: Provenance = Field(default_factory=Provenance)

    @property
    def resolved(self) -> bool:
        """True when the item asserts something concrete enough to reconcile."""
        return self.status not in UNRESOLVED_STATUSES and self.value is not None


# ── typed evidence ────────────────────────────────────────────────────────────

class ServiceIntentEvidence(EvidenceItem):
    field: str = "service_intent"
    kind: ServiceIntentKind = ServiceIntentKind.OTHER


class VehicleEvidence(EvidenceItem):
    field: str = "vehicle"
    make: Optional[str] = None
    model: Optional[str] = None
    year: Optional[int] = None
    year_status: Optional[EvidenceStatus] = None
    category_suggestion: Optional[str] = None     # e.g. SUV_4X4_DEPORTIVO — a suggestion
    is_superseded: bool = False                   # named, then corrected away
    mention_index: int = 0                        # order within the burst


class LocationEvidence(EvidenceItem):
    field: str = "location"
    locality: Optional[str] = None
    zone_hint: Optional[str] = None
    role: str = LocationRole.UNKNOWN_LOCATION_ROLE.value   # role is mandatory here


class FaqIntentEvidence(EvidenceItem):
    field: str = "faq_intent"
    topic: Optional[str] = None


class AcceptanceEvidence(EvidenceItem):
    field: str = "acceptance"
    signal: AcceptanceSignal = AcceptanceSignal.UNKNOWN


class SchedulingRequestEvidence(EvidenceItem):
    field: str = "scheduling_request"
    priority: SchedulingPriority = SchedulingPriority.PRIMARY
    day_expression: Optional[str] = None      # what the customer said: "mñ", "jueves"
    resolved_date: Optional[str] = None       # ISO date, only if the interpreter resolved it
    time: Optional[str] = None                # "HH:MM"
    flexible_time: bool = False
    rank: int = 1


class CorrectionEvidence(EvidenceItem):
    field: str = "correction"
    relation: CorrectionRelation = CorrectionRelation.UNKNOWN_RELATION
    from_value: Any = None
    to_value: Any = None
    target_ref: Optional[str] = None          # ref of the item being corrected


class IdentityEvidence(EvidenceItem):
    field: str = "identity"
    kind: IdentityKind = IdentityKind.OTHER_IDENTITY


class HandoffEvidence(EvidenceItem):
    field: str = "handoff"
    requested: bool = False


class AmbiguityNote(_Frozen):
    """Several plausible readings survive — the interpreter must not pick one."""
    field: str
    alternatives: tuple[Alternative, ...] = ()
    reason: Optional[str] = None
    provenance: Provenance = Field(default_factory=Provenance)


class ConflictNote(_Frozen):
    """Two or more readings contradict each other — both sides are preserved."""
    field: str
    sides: tuple[Alternative, ...] = ()
    reason: Optional[str] = None
    provenance: Provenance = Field(default_factory=Provenance)


# ── the turn container ────────────────────────────────────────────────────────

class TurnEvidence(_Frozen):
    """Everything one interpreter proposed about one burst. Immutable."""
    schema_version: str = SCHEMA_VERSION
    interpreter: Optional[str] = None
    model_version: Optional[str] = None
    turn: TurnRef = Field(default_factory=TurnRef)

    service_intents: tuple[ServiceIntentEvidence, ...] = ()
    vehicle_mentions: tuple[VehicleEvidence, ...] = ()
    location_mentions: tuple[LocationEvidence, ...] = ()
    faq_intents: tuple[FaqIntentEvidence, ...] = ()
    acceptance: Optional[AcceptanceEvidence] = None
    scheduling_requests: tuple[SchedulingRequestEvidence, ...] = ()
    corrections: tuple[CorrectionEvidence, ...] = ()
    identity_mentions: tuple[IdentityEvidence, ...] = ()
    handoff: Optional[HandoffEvidence] = None
    freeform_notes: tuple[str, ...] = ()
    ambiguities: tuple[AmbiguityNote, ...] = ()
    conflicts: tuple[ConflictNote, ...] = ()

    # ── iteration and refs ────────────────────────────────────────────────────

    _COLLECTIONS = (
        "service_intents", "vehicle_mentions", "location_mentions", "faq_intents",
        "scheduling_requests", "corrections", "identity_mentions",
    )

    def iter_items(self) -> Iterator[tuple[str, EvidenceItem]]:
        """Yield (stable_ref, item) for every evidence item in the turn."""
        for name in self._COLLECTIONS:
            for index, item in enumerate(getattr(self, name)):
                yield f"{name}[{index}]", item
        if self.acceptance is not None:
            yield "acceptance", self.acceptance
        if self.handoff is not None:
            yield "handoff", self.handoff

    def refs(self) -> tuple[str, ...]:
        return tuple(ref for ref, _ in self.iter_items())

    def item_at(self, ref: str) -> Optional[EvidenceItem]:
        for candidate_ref, item in self.iter_items():
            if candidate_ref == ref:
                return item
        return None

    def is_empty(self) -> bool:
        """True when the interpreter proposed nothing — a legitimate outcome."""
        return not any(True for _ in self.iter_items()) and not self.ambiguities and not self.conflicts

    # ── deterministic serialization ───────────────────────────────────────────

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")

    def to_canonical_json(self) -> str:
        """Stable JSON: sorted keys, no whitespace drift, unicode preserved.

        Used for structured-output contracts, shadow replay records and evaluation input,
        so byte equality is a meaningful comparison.
        """
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"))

    @classmethod
    def from_json(cls, payload: str | dict) -> "TurnEvidence":
        data = json.loads(payload) if isinstance(payload, str) else dict(payload)
        version = str(data.get("schema_version", ""))
        if version and not version.startswith("turn-evidence/"):
            raise ValueError(f"unrecognised TurnEvidence schema_version: {version!r}")
        major = version.split("/")[-1].split(".")[0] if version else None
        if major and major != SCHEMA_VERSION.split("/")[-1].split(".")[0]:
            raise ValueError(
                f"incompatible TurnEvidence major version {version!r} "
                f"(this build speaks {SCHEMA_VERSION})"
            )
        return cls.model_validate(data)


# ── reconciliation disposition (separate, append-only) ────────────────────────

class ReconciliationRecord(_Frozen):
    """What deterministic reconciliation decided about ONE evidence item.

    Stored apart from the interpretation so historical evidence is never rewritten to
    match a later canonical truth.
    """
    evidence_ref: str
    status: ReconciliationStatus
    reason: Optional[str] = None
    decided_by: Optional[str] = None            # e.g. "reconciler:catalog_authority"
    decided_at: Optional[str] = None            # ISO timestamp
    canonical_value: Any = None                 # what canonical state actually took, if any


class ReconciliationLog(_Frozen):
    """Append-only log for one turn. `append` returns a new log; nothing mutates."""
    turn_schema_version: str = SCHEMA_VERSION
    records: tuple[ReconciliationRecord, ...] = ()

    def append(self, record: ReconciliationRecord) -> "ReconciliationLog":
        return ReconciliationLog(
            turn_schema_version=self.turn_schema_version,
            records=self.records + (record,),
        )

    def for_ref(self, ref: str) -> tuple[ReconciliationRecord, ...]:
        return tuple(r for r in self.records if r.evidence_ref == ref)


__all__ = [
    "SCHEMA_VERSION", "EvidenceStatus", "UNRESOLVED_STATUSES", "SourceKind", "LocationRole",
    "ServiceIntentKind", "AcceptanceSignal", "CorrectionRelation", "SchedulingPriority",
    "IdentityKind", "ReconciliationStatus", "BurstReconstruction",
    "SourceSpan", "Provenance", "TurnRef", "Alternative", "EvidenceItem",
    "ServiceIntentEvidence", "VehicleEvidence", "LocationEvidence", "FaqIntentEvidence",
    "AcceptanceEvidence", "SchedulingRequestEvidence", "CorrectionEvidence",
    "IdentityEvidence", "HandoffEvidence", "AmbiguityNote", "ConflictNote",
    "TurnEvidence", "ReconciliationRecord", "ReconciliationLog",
]
