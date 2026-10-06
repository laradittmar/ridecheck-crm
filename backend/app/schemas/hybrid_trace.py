"""L4.7W5 — the Hybrid Decision Trace contract, version `hybrid-decision-trace/1.2`.

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

**1.2 — absence of conflict is not agreement either.** 1.1 fixed the direction the audit
found and left the mirror standing: with two producers present it returned `AGREE` whenever
no explicit conflict was detected, including when the two had spoken about entirely
different things. Semantic supplying `vehicle.model` while CE supplied `vehicle.year` is not
two engines agreeing; it is two engines saying unrelated things.

1.2 requires a **shared canonical proposition** — the same canonical business field —
carrying evidence from at least two attributed producers, and requires that their canonical
values be provably compatible before the word `AGREE` is used. Canonical equivalence comes
from resolvers this system already has (`CANONICAL_PROPOSITIONS` names them); where none can
establish it, the row is `COMPARISON_UNPROVEN`, never agreement. Two producers with nothing
in common are `PARALLEL_EVIDENCE`.

1.2 also removes the row ordinal from comparison identity. A logical comparison is now
identified by what it IS — decision site, proposition, rule, and the sorted content hashes
of its claims — so inserting an unrelated reconciliation earlier in the turn cannot change
it. Repeated executions of the same logical comparison are separated by `occurrence_index`,
which is positional and is deliberately kept out of the identity.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

TRACE_VERSION = "hybrid-decision-trace/1.9"
#: 1.8 added the clarification block; 1.9 adds the ATTEMPT LIFECYCLE inside it, so a
#: dispatch turn, a completion turn and a prevented-second turn can be joined by
#: `attempt_id` into one lifecycle. Additive: no 1.8 field changed meaning.
TRACE_VERSION_1_8 = "hybrid-decision-trace/1.8"
#: G3-5E. 1.4 splits `response_plan_source` — which was `answer_source`, and therefore
#: answered three questions at once — into decision authority, fact sources, renderer source
#: and dispatch result, and adds the reply-generation role and the shadow response plan. A
#: 1.3 row carries none of those fields, so a reader must render them as UNAVAILABLE rather
#: than as empty 1.4 fields; `response_plan_source` on a 1.3 row keeps its old meaning.
#: G3-5F. 1.4 described a plan that watched; 1.5 describes a plan that decides. The bump is
#: a SEMANTIC one, not a convenience: `shadow` used to be constant-true and now distinguishes
#: a live composition from a post-hoc description, and the payload gained the counts §15
#: requires — planned text items, actual text messages, the Flow item counted apart from
#: text. A consumer reading a 1.4 row must not interpret its `shadow` flag as the 1.5 one,
#: so the version has to say which contract it is.
#:
#: Checked against the live table before choosing: no persisted row has ever claimed 1.4, so
#: completing 1.4 in place was truthful for G3-5E2. Rows DO exist at 1.0 and 1.3, and 1.5
#: changes what a reader must conclude, so this one is a new version rather than an edit.
TRACE_VERSION_1_4 = "hybrid-decision-trace/1.4"
TRACE_VERSION_1_3 = "hybrid-decision-trace/1.3"
TRACE_VERSION_1_2 = "hybrid-decision-trace/1.2"
TRACE_VERSION_1_1 = "hybrid-decision-trace/1.1"
#: The first contract. Its reconciliation rows carry no producer provenance, so a
#: reader must never treat their absent 1.1 fields as empty 1.1 fields.
TRACE_VERSION_1_0 = "hybrid-decision-trace/1.0"
READABLE_VERSIONS = (TRACE_VERSION, TRACE_VERSION_1_8, TRACE_VERSION_1_4,
                     TRACE_VERSION_1_3, TRACE_VERSION_1_2, TRACE_VERSION_1_1,
                     TRACE_VERSION_1_0)
#: Contracts whose `response_plan`, where present, only ever DESCRIBED a turn. A reader must
#: not take their `shadow` flag, item counts or suppression reasons as decisions.
VERSIONS_WITH_SHADOW_ONLY_PLAN = (TRACE_VERSION_1_4, TRACE_VERSION_1_3, TRACE_VERSION_1_2,
                                  TRACE_VERSION_1_1, TRACE_VERSION_1_0)
#: Contracts predating the G3-5E attribution split. A reader must not infer the new fields.
VERSIONS_WITHOUT_ATTRIBUTION_SPLIT = (TRACE_VERSION_1_3, TRACE_VERSION_1_2,
                                      TRACE_VERSION_1_1, TRACE_VERSION_1_0)


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
    #: Two or more producers participated and shared no canonical proposition. They spoke
    #: about different things; that is neither agreement nor disagreement.
    PARALLEL_EVIDENCE = "PARALLEL_EVIDENCE"
    #: A proposition IS shared, and the captured evidence cannot establish whether the two
    #: canonical values mean the same thing. Unproven is not compatible.
    COMPARISON_UNPROVEN = "COMPARISON_UNPROVEN"
    SINGLE_PRODUCER = "SINGLE_PRODUCER"          # exactly one source supplied evidence
    #: The only participating producer contradicted itself. Self-contradiction is recorded
    #: as ambiguity of that producer's evidence, never as a conflict between engines.
    AMBIGUOUS_EVIDENCE = "AMBIGUOUS_EVIDENCE"
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

    ROW = (AGREE, CONFLICT, PARALLEL_EVIDENCE, COMPARISON_UNPROVEN, SINGLE_PRODUCER,
           AMBIGUOUS_EVIDENCE, NO_EVIDENCE, NOT_ROUTED, ERROR,
           LEGACY_PROVENANCE_UNAVAILABLE)
    TURN = (AGREE, CONFLICT, PARTIAL_RECONCILIATION, SINGLE_SOURCE_DECISION,
            NO_COMPARISON, TRACE_INCOMPLETE, SEMANTIC_MISSING, SEMANTIC_NOT_ROUTED,
            SEMANTIC_ERROR, CE_MISSING, NO_RULE)
    ALL = tuple(dict.fromkeys(ROW + TURN))

    #: Row values meaning "two or more distinct sources were actually weighed".
    COMPARED = (AGREE, CONFLICT)
    #: Row values meaning "no comparison happened", for whichever reason.
    INCOMPARABLE = (PARALLEL_EVIDENCE, COMPARISON_UNPROVEN, SINGLE_PRODUCER,
                    AMBIGUOUS_EVIDENCE, NO_EVIDENCE, NOT_ROUTED, ERROR,
                    LEGACY_PROVENANCE_UNAVAILABLE)
    #: Row values whose true classification cannot be established from the stored record.
    #: `COMPARISON_UNPROVEN` and `AMBIGUOUS_EVIDENCE` belong here because in both the
    #: captured evidence is insufficient to say what the producers actually did.
    UNKNOWN = (LEGACY_PROVENANCE_UNAVAILABLE, ERROR, COMPARISON_UNPROVEN,
               AMBIGUOUS_EVIDENCE)


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


#: The canonical business fields two producers can be said to speak about. Two claims
#: address the same proposition when they carry the same claim type from this list —
#: never when their raw wording resembles each other, which this system never inspects.
#:
#: `resolver` names an EXISTING function that turns a structured claim value into a
#: canonical identity. Nothing here is a new catalogue, a phrase list or a synonym table:
#: `vehicle_catalog.lookup_vehicle` is the same resolver `reconcile_vehicle_identity`
#: already treats as the authority on what a car is called.
#:
#: `self_canonical` marks a proposition whose structured value IS already its canonical
#: form — a year is a year — so two values may be compared directly.
CANONICAL_PROPOSITIONS = {
    "vehicle.make":        {"resolver": "vehicle_catalog", "self_canonical": False},
    "vehicle.model":       {"resolver": "vehicle_catalog", "self_canonical": False},
    "vehicle.year":        {"resolver": None,              "self_canonical": True},
    "vehicle.category":    {"resolver": None,              "self_canonical": False},
    "inspection_location": {"resolver": None,              "self_canonical": False},
    "customer_origin":     {"resolver": None,              "self_canonical": False},
    "seller_location":     {"resolver": None,              "self_canonical": False},
    "service_intent":      {"resolver": None,              "self_canonical": False},
    "scheduling_preference": {"resolver": None,            "self_canonical": False},
    # G3-1. Both are boolean propositions, so their structured value IS their canonical
    # form and two producers can be compared directly. Without them the acceptance and
    # handoff sites had no shared proposition at all and two producers speaking about the
    # same thing read as PARALLEL_EVIDENCE — an understatement that would have hidden a
    # real quote-acceptance disagreement from the operator.
    "quote_accepted":      {"resolver": None,              "self_canonical": True},
    "needs_human":         {"resolver": None,              "self_canonical": True},
}


class ComparisonVerdict:
    """What one shared proposition proved. Never inferred from the absence of the others."""
    COMPATIBLE = "COMPATIBLE"
    INCOMPATIBLE = "INCOMPATIBLE"
    UNPROVEN = "UNPROVEN"

    ALL = (COMPATIBLE, INCOMPATIBLE, UNPROVEN)


class DomainVerdictAdmission:
    """Whether a domain comparator's finding was allowed to affect the classification.

    G3-1-R2. A domain comparator — `semantic_covers_deterministic` is the only one today —
    compares two producers on one proposition using knowledge the generic comparator does
    not have. Its finding is admissible evidence, but only inside the same preconditions
    every other comparison must satisfy, and never over the top of a generic comparison
    that already proved the opposite.

    A rejection is recorded rather than dropped. The whole point of the Inspector is that
    a disagreement between two comparators is a fact an operator should see, not something
    the trace quietly resolves.
    """
    NOT_SUPPLIED = "NOT_SUPPLIED"
    ADMITTED = "ADMITTED"
    #: fewer than two distinct producers — a comparator cannot invent the second one
    REJECTED_SINGLE_PRODUCER = "REJECTED_SINGLE_PRODUCER"
    #: the producers addressed no proposition in common
    REJECTED_NO_SHARED_PROPOSITION = "REJECTED_NO_SHARED_PROPOSITION"
    #: the generic comparison already proved compatibility or incompatibility
    REJECTED_EVIDENCE_ALREADY_PROVEN = "REJECTED_EVIDENCE_ALREADY_PROVEN"

    ALL = (NOT_SUPPLIED, ADMITTED, REJECTED_SINGLE_PRODUCER,
           REJECTED_NO_SHARED_PROPOSITION, REJECTED_EVIDENCE_ALREADY_PROVEN)
    REJECTIONS = (REJECTED_SINGLE_PRODUCER, REJECTED_NO_SHARED_PROPOSITION,
                  REJECTED_EVIDENCE_ALREADY_PROVEN)


class CanonicalEffect:
    """What a decision did to canonical state. Separate from whether it was allowed.

    G3-1 exists because those two were indistinguishable: a row could say `ACCEPT` and a
    reader could not tell whether anything had been written. `NONE` is the common and
    correct answer for a proposal.
    """
    NONE = "NONE"
    WROTE = "WROTE"
    BLOCKED = "BLOCKED"

    ALL = (NONE, WROTE, BLOCKED)


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
    # ── G3-1: the four decisions that were already hybrid and already invisible ──
    SCHEDULING_REQUEST_RECONCILIATION = "scheduling.request.reconciliation"
    QUOTE_ACCEPTANCE_AUTHORIZATION = "quote.acceptance.authorization"
    HANDOFF_SEMANTIC_REQUEST = "handoff.semantic.request"
    LOCALITY_SEMANTIC_RECOVERY = "locality.semantic.recovery"
    # ── G3-4: the two decisions that were being made invisibly, by absence and by
    # substring. Availability admission used to require the deterministic parser to find a
    # day first, so CE silence vetoed a semantic enquiry; FAQ topic selection used to be
    # won outright by a literal match, with no decision recorded anywhere.
    SCHEDULING_AVAILABILITY_AUTHORIZATION = "scheduling.availability.authorization"
    FAQ_TOPIC_AUTHORIZATION = "faq.topic.authorization"

    ALL = (VEHICLE_IDENTITY_APPLY, LOCATION_INSPECTION_APPLY,
           VEHICLE_FUZZY_ADMISSIBILITY,
           SCHEDULING_REQUEST_RECONCILIATION, QUOTE_ACCEPTANCE_AUTHORIZATION,
           HANDOFF_SEMANTIC_REQUEST, LOCALITY_SEMANTIC_RECOVERY,
           SCHEDULING_AVAILABILITY_AUTHORIZATION, FAQ_TOPIC_AUTHORIZATION)


class SchedulingAuthority:
    """What an availability decision permitted. Bounded and typed, never free text.

    An availability ENQUIRY can never reach a booking outcome: there is no ACCEPT and no
    BOOK member, so no policy branch can produce one. Real slots come from ScheduleService
    and from nowhere else; these values say only what the turn was allowed to go and ask.
    """
    LOOK_UP_AVAILABILITY = "LOOK_UP_AVAILABILITY"     # consult ScheduleService, then answer
    REQUEST_DATE = "REQUEST_DATE"                      # ask which day, per existing policy
    REQUEST_PREREQUISITE = "REQUEST_PREREQUISITE"      # vehicle / location / quote first
    CLARIFY = "CLARIFY"                                # the interpreter itself was ambiguous
    HOLD = "HOLD"                                      # no admission, or unresolved conflict
    DEFER_TO_DETERMINISTIC = "DEFER_TO_DETERMINISTIC"  # CE already owns this turn

    ALL = (LOOK_UP_AVAILABILITY, REQUEST_DATE, REQUEST_PREREQUISITE, CLARIFY, HOLD,
           DEFER_TO_DETERMINISTIC)


class AdmissionCondition:
    """Which producer let a scheduling turn reach its decision site.

    G3-2 DEF-02: the only route in required the deterministic parser to have found a day or
    a time, and the semantic reading was read INSIDE that route — so CE silence was an
    absolute veto on semantic scheduling evidence. Admission is now a disjunction, and
    which half opened it is recorded.
    """
    DETERMINISTIC = "DETERMINISTIC_EVIDENCE"
    SEMANTIC = "SEMANTIC_INTENT"
    BOTH = "BOTH_PRODUCERS"
    NONE = "NOT_ADMITTED"

    ALL = (DETERMINISTIC, SEMANTIC, BOTH, NONE)


class FaqAuthority:
    """What a FAQ-topic decision permitted."""
    ANSWER_CANONICAL = "ANSWER_CANONICAL"      # emit RideCheck's own business truth
    SUPPRESS = "SUPPRESS"                      # a candidate lost; the reason is recorded
    NO_TOPIC = "NO_TOPIC"                      # nothing was asked

    ALL = (ANSWER_CANONICAL, SUPPRESS, NO_TOPIC)


class HoursDisposition:
    """What became of the business-hours candidate, and on whose evidence.

    R2B. This replaced a boolean that keyed on deterministic strength, which let a STRONG
    substring overrule a resolved semantic reading. Naming the outcomes makes the policy
    legible in the trace: an operator can see not merely THAT the hours answer was withheld
    but which evidence withheld it.
    """
    ADMIT = "ADMIT"
    SUPPRESSED_BY_SEMANTIC_AVAILABILITY = "SUPPRESSED_BY_SEMANTIC_AVAILABILITY"
    #: G3-5A (audit F-01). The interpretation did not resolve the proposition, but the
    #: deterministic scheduling parser the engine already trusts DID resolve a competing
    #: one — a requested day, date or time. A lexical hours match may not answer over it
    #: and silently discard the date the customer named.
    SUPPRESSED_BY_DETERMINISTIC_SCHEDULING = "SUPPRESSED_BY_DETERMINISTIC_SCHEDULING"
    SUPPRESSED_AMBIGUOUS = "SUPPRESSED_AMBIGUOUS"
    SUPPRESSED_UNCORROBORATED = "SUPPRESSED_UNCORROBORATED"
    NO_CANDIDATE = "NO_CANDIDATE"

    ALL = (ADMIT, SUPPRESSED_BY_SEMANTIC_AVAILABILITY,
           SUPPRESSED_BY_DETERMINISTIC_SCHEDULING, SUPPRESSED_AMBIGUOUS,
           SUPPRESSED_UNCORROBORATED, NO_CANDIDATE)


class DecisionAuthority:
    """WHO authorized the action or the canonical mutation.

    G3-5E (audit G5D-04/G5D-10). `answer_source` conflated three different questions —
    who decided, who supplied the facts and who wrote the words — and forced
    `DETERMINISTIC_RULE` onto every blocked dispatch, so the Inspector reported a rule as
    the author of prose a model had written. These four vocabularies answer one question
    each. `answer_source` is kept untouched beside them: it is what every existing consumer
    reads, and replacing it would change behaviour this milestone may not change.
    """
    RECONCILER = "RECONCILER"                     # `_availability_authority` and successors
    DETERMINISTIC_RULE = "DETERMINISTIC_RULE"     # a CE handler's own gate
    #: Truthful name for the authority the G3-5D audit found in the reply model: it applies
    #: extracted fields, creates candidates, can force a motorcycle handoff and can reset
    #: the commercial stage. Named, not corrected — removal belongs to a later milestone.
    LEGACY_REPLY_MODEL_EXTRACTION = "LEGACY_REPLY_MODEL_EXTRACTION"
    HUMAN_HANDOFF_POLICY = "HUMAN_HANDOFF_POLICY"
    FLOW_TRANSACTION = "FLOW_TRANSACTION"
    PRICING_SERVICE = "PRICING_SERVICE"
    SCHEDULING_SERVICE = "SCHEDULING_SERVICE"
    NONE = "NONE"

    ALL = (RECONCILER, DETERMINISTIC_RULE, LEGACY_REPLY_MODEL_EXTRACTION,
           HUMAN_HANDOFF_POLICY, FLOW_TRANSACTION, PRICING_SERVICE, SCHEDULING_SERVICE,
           NONE)


class FactSource:
    """WHICH producers supplied the facts in the response. Several may apply."""
    SEMANTIC_EVIDENCE = "SEMANTIC_EVIDENCE"
    DETERMINISTIC_EVIDENCE = "DETERMINISTIC_EVIDENCE"
    CANONICAL_STATE = "CANONICAL_STATE"
    PRICING_SERVICE = "PRICING_SERVICE"
    SCHEDULING_SERVICE = "SCHEDULING_SERVICE"
    FAQ_CANONICAL = "FAQ_CANONICAL"
    FLOW_TRANSACTION = "FLOW_TRANSACTION"

    ALL = (SEMANTIC_EVIDENCE, DETERMINISTIC_EVIDENCE, CANONICAL_STATE, PRICING_SERVICE,
           SCHEDULING_SERVICE, FAQ_CANONICAL, FLOW_TRANSACTION)


class RendererSource:
    """WHO produced the customer's words."""
    DETERMINISTIC_TEMPLATE = "DETERMINISTIC_TEMPLATE"
    REPLY_MODEL = "REPLY_MODEL"
    META_FLOW = "META_FLOW"
    COMPOSED_MIXED = "COMPOSED_MIXED"      # model prose plus a canonical answer appended
    NO_PROSE = "NO_PROSE"

    ALL = (DETERMINISTIC_TEMPLATE, REPLY_MODEL, META_FLOW, COMPOSED_MIXED, NO_PROSE)


class DispatchPath:
    """WHICH transport attempted delivery."""
    TEXT = "TEXT"
    META_FLOW = "META_FLOW"
    NONE = "NONE"

    ALL = (TEXT, META_FLOW, NONE)


class DispatchResult:
    """WHAT the outbound safety gate decided. Never a source of reasoning or prose."""
    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    DUPLICATE_SUPPRESSED = "DUPLICATE_SUPPRESSED"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"

    ALL = (ALLOWED, BLOCKED, FAILED, DUPLICATE_SUPPRESSED, NOT_ATTEMPTED)


class ResponseItemKind:
    """What one planned contribution to the customer's turn is."""
    TEXT = "TEXT"
    CLARIFICATION = "CLARIFICATION"
    FAQ_ANSWER = "FAQ_ANSWER"
    HANDOFF_ACK = "HANDOFF_ACK"
    FLOW_INTRO_TEXT = "FLOW_INTRO_TEXT"
    META_FLOW = "META_FLOW"
    #: P5. Authorized ONLY after a Flow completion the backend validated, accepted and
    #: committed. Represented here so the composition milestone can activate it against a
    #: pinned contract; G3-5E never emits it.
    FLOW_COMPLETION_ACK = "FLOW_COMPLETION_ACK"
    #: G3-6. A fact canonical state holds that the response must state or reconcile — the
    #: vehicle confirmation the L4.6 finalizer used to append to finished prose.
    CANONICAL_FACT = "CANONICAL_FACT"
    #: G3-6. Appointment times the thread really has, from `last_visible_slots` /
    #: `last_offered_slots`. Typed apart from FAQ_ANSWER because an offer is never
    #: redundant with the opening-hours answer and must survive a rejected primary reply.
    SLOT_OFFER = "SLOT_OFFER"
    SUPPRESSED = "SUPPRESSED"
    NO_RESPONSE = "NO_RESPONSE"

    ALL = (TEXT, CLARIFICATION, FAQ_ANSWER, HANDOFF_ACK, FLOW_INTRO_TEXT, META_FLOW,
           FLOW_COMPLETION_ACK, CANONICAL_FACT, SLOT_OFFER, SUPPRESSED, NO_RESPONSE)


class CoexistencePolicy:
    """Whether an item may stand beside others in one turn."""
    REQUIRED = "REQUIRED"        # must be present if authorized
    COMPATIBLE = "COMPATIBLE"    # may stand beside other items
    EXCLUSIVE = "EXCLUSIVE"      # owns the turn; suppresses the rest

    ALL = (REQUIRED, COMPATIBLE, EXCLUSIVE)


class TransportMultiplicity:
    """P3/P4. How many transport messages one turn's plan may occupy."""
    SINGLE_MESSAGE = "SINGLE_MESSAGE"
    #: The one approved exception: a short introduction followed by the Meta Flow, typed as
    #: two items of one sequence so it can never become two unrelated free-text replies.
    FLOW_INTRO_THEN_FLOW = "FLOW_INTRO_THEN_FLOW"

    ALL = (SINGLE_MESSAGE, FLOW_INTRO_THEN_FLOW)


class ResponsePolicy:
    """The owner-approved policies this contract must be able to express (G3-5E §4)."""
    P1_HOLD_PLUS_SAFE_FAQ = "P1_HOLD_PLUS_SAFE_FAQ"
    P2_HUMAN_HANDOFF_EXCLUSIVE = "P2_HUMAN_HANDOFF_EXCLUSIVE"
    P3_SINGLE_MESSAGE_DEFAULT = "P3_SINGLE_MESSAGE_DEFAULT"
    P4_FLOW_INTRO_SEQUENCE = "P4_FLOW_INTRO_SEQUENCE"
    P5_FLOW_COMPLETION_ACK = "P5_FLOW_COMPLETION_ACK"
    P6_NO_ROUTINE_FACT_REPETITION = "P6_NO_ROUTINE_FACT_REPETITION"
    P7_SEMANTIC_ONLY_PERMISSIONS = "P7_SEMANTIC_ONLY_PERMISSIONS"

    ALL = (P1_HOLD_PLUS_SAFE_FAQ, P2_HUMAN_HANDOFF_EXCLUSIVE, P3_SINGLE_MESSAGE_DEFAULT,
           P4_FLOW_INTRO_SEQUENCE, P5_FLOW_COMPLETION_ACK, P6_NO_ROUTINE_FACT_REPETITION,
           P7_SEMANTIC_ONLY_PERMISSIONS)


#: P5 — every condition that must hold before FLOW_COMPLETION_ACK may be authorized. Pinned
#: as data so the composition milestone cannot quietly relax one of them.
FLOW_COMPLETION_ACK_CONDITIONS: tuple = (
    "flow_submitted",              # not merely opened, and not an internal navigation
    "signature_verified",
    "token_valid",
    "payload_complete",
    "validation_passed",
    "persistence_committed",
    "not_already_acknowledged",
)


@dataclass(frozen=True)
class ResponseItem:
    """One authorized (or suppressed) contribution to a customer turn.

    Deduplication is by `proposition_id` — a stable family+subject key — never by comparing
    rendered text. Two producers saying the same thing are one item; two different
    propositions that happen to share wording are two.
    """
    kind: str
    claim_family: str
    proposition_id: str
    evidence_ids: tuple = ()
    classification: Optional[str] = None      # the existing `Classification` vocabulary
    decision_authority: str = DecisionAuthority.NONE
    fact_sources: tuple = ()
    renderer_source: str = RendererSource.NO_PROSE
    required_action: Optional[str] = None
    priority: int = 100
    coexistence: str = CoexistencePolicy.COMPATIBLE
    mutation_authorized: bool = False
    dispatch_path: str = DispatchPath.NONE
    included: bool = True
    suppression_reason: Optional[str] = None
    policy: Optional[str] = None
    #: G3-6. The canonical values this item is answerable for, as
    #: `field -> digest-or-safe-token`. Recorded so an operator can see WHICH fact was
    #: locked without the trace carrying a customer's data.
    locked_values: tuple = ()


@dataclass(frozen=True)
class ResponsePlan:
    """What one decision phase owed the customer.

    G3-5E builds this in SHADOW only: it is constructed from the decision path that already
    ran, compared with what actually happened, and traced. It cannot dispatch, cannot mutate
    canonical state, cannot cause a model call and cannot create a second reply. Exactly one
    plan exists per decision phase.
    """
    plan_id: str
    turn_id: Optional[str]
    burst_fingerprint: Optional[str]
    decision_phase_id: Optional[str]
    items: tuple = ()
    transport_multiplicity: str = TransportMultiplicity.SINGLE_MESSAGE
    renderer_source: str = RendererSource.NO_PROSE
    dispatch_path: str = DispatchPath.NONE
    dispatch_result: str = DispatchResult.NOT_ATTEMPTED
    shadow: bool = True
    outcome: Optional[str] = None

    @property
    def included_items(self) -> tuple:
        return tuple(i for i in self.items if i.included)

    @property
    def suppressed_items(self) -> tuple:
        return tuple(i for i in self.items if not i.included)

    #: Everything that occupies a TEXT message. `NO_RESPONSE` and `SUPPRESSED` occupy none,
    #: and `META_FLOW` is a Flow — counting it as text is what made "one message" ambiguous.
    _TEXT_KINDS = (ResponseItemKind.TEXT, ResponseItemKind.CLARIFICATION,
                   ResponseItemKind.FAQ_ANSWER, ResponseItemKind.HANDOFF_ACK,
                   ResponseItemKind.FLOW_INTRO_TEXT, ResponseItemKind.FLOW_COMPLETION_ACK,
                   ResponseItemKind.CANONICAL_FACT, ResponseItemKind.SLOT_OFFER)

    @property
    def planned_text_items(self) -> tuple:
        return tuple(i for i in self.included_items if i.kind in self._TEXT_KINDS)

    @property
    def planned_flow_items(self) -> tuple:
        return tuple(i for i in self.included_items
                     if i.kind == ResponseItemKind.META_FLOW)

    def to_payload(self) -> dict:
        return {
            "plan_id": self.plan_id, "turn_id": self.turn_id,
            "burst_fingerprint": self.burst_fingerprint,
            "decision_phase_id": self.decision_phase_id,
            "transport_multiplicity": self.transport_multiplicity,
            "renderer_source": self.renderer_source,
            "dispatch_path": self.dispatch_path, "dispatch_result": self.dispatch_result,
            "shadow": self.shadow, "outcome": self.outcome,
            "item_count": len(self.included_items),
            "suppressed_count": len(self.suppressed_items),
            "item_kinds": [i.kind for i in self.included_items],
            # §15. The Flow is counted APART from text, because "two messages" means
            # something different when the second one is a form. `planned_text_items` is
            # what the plan authorized; the trace carries what was actually sent beside it,
            # so a divergence is visible instead of averaged away.
            "planned_text_items": len(self.planned_text_items),
            "planned_flow_items": len(self.planned_flow_items),
            "items": [
                {"kind": i.kind, "claim_family": i.claim_family,
                 "proposition_id": i.proposition_id,
                 # G3-5F §15. `evidence_ids` are WAMIDs. The plan needs to prove WHICH
                 # messages a proposition rests on and HOW MANY — it never needs the
                 # identifiers themselves, and a trace row is read by operators. Each is
                 # reduced to a short digest: still comparable between items and against
                 # the burst, no longer a reversible identifier.
                 "evidence_ids": [hashlib.sha256(str(x).encode("utf-8")).hexdigest()[:12]
                                  for x in i.evidence_ids],
                 "evidence_count": len(i.evidence_ids),
                 "classification": i.classification,
                 "decision_authority": i.decision_authority,
                 "fact_sources": list(i.fact_sources),
                 "renderer_source": i.renderer_source,
                 "required_action": i.required_action, "priority": i.priority,
                 "coexistence": i.coexistence,
                 "mutation_authorized": i.mutation_authorized,
                 "dispatch_path": i.dispatch_path, "included": i.included,
                 "suppression_reason": i.suppression_reason, "policy": i.policy,
                 "locked_values": [list(pair) for pair in i.locked_values]}
                for i in self.items],
        }


class CandidateAction:
    """What the candidate reconciler decided to do with the turn's admitted evidence.

    G3-7-R2. These are the actions the certified writer already implements; the reconciler
    names them so the decision is inspectable BEFORE it is applied, instead of being an
    emergent property of a dict the reply model happened to produce.
    """
    NONE = "NONE"                       # no admitted vehicle evidence — noise
    REUSE_FOCUS = "REUSE_FOCUS"         # the mention is the candidate already in focus
    REUSE_EXISTING = "REUSE_EXISTING"   # exactly one non-focused candidate matches
    CREATE = "CREATE"                   # a distinct vehicle, not seen on this thread
    HOLD = "HOLD"                       # more than one candidate is equally compatible
    #: G3-7B. An existing candidate's ATTRIBUTES are corrected. Distinct from REUSE_FOCUS
    #: because the authority differs: a re-mention names the car, a correction does not.
    #: Focus never moves on this action — the writer receives no `status` key.
    CORRECT_EXISTING = "CORRECT_EXISTING"

    ALL = (NONE, REUSE_FOCUS, REUSE_EXISTING, CREATE, HOLD, CORRECT_EXISTING)


class CandidateHoldReason:
    AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"
    # ── G3-7B: every way a correction can fail to find one safe target ──────────
    #: Evidence identifies no vehicle well enough to create one, and no target to correct.
    INCOMPLETE_IDENTITY = "INCOMPLETE_IDENTITY"
    #: A correction was stated but its target could not be resolved at all.
    TARGET_UNRESOLVED = "TARGET_UNRESOLVED"
    #: The correction names a vehicle this thread does not have.
    TARGET_CONFLICT = "TARGET_CONFLICT"
    FOCUS_POINTER_MISSING = "FOCUS_POINTER_MISSING"
    FOCUS_POINTER_INVALID = "FOCUS_POINTER_INVALID"
    FOCUS_NOT_UNIQUE = "FOCUS_NOT_UNIQUE"
    FOCUS_INCONSISTENT = "FOCUS_INCONSISTENT"
    FOCUS_NOT_ESTABLISHED = "FOCUS_NOT_ESTABLISHED"
    # ── G3-7F: the Grounded-Target Invariant and the category floor ─────────────
    #: No admissible ground (G1/G2/G3) established the target.
    TARGET_NOT_GROUNDED = "TARGET_NOT_GROUNDED"
    #: The evidence excludes the candidate implicit focus would have chosen.
    EXCLUDED_REFERENT = "EXCLUDED_REFERENT"
    #: Only implicit focus was available, and the evidence is not CONFIRMED.
    IMPLICIT_FOCUS_UNCONFIRMED = "IMPLICIT_FOCUS_UNCONFIRMED"
    #: Creation needs make AND model said literally by the customer.
    CREATE_UNCONFIRMED = "CREATE_UNCONFIRMED"
    #: The vehicle category is established by neither the catalogue nor confirmed evidence.
    CATEGORY_NOT_ESTABLISHED = "CATEGORY_NOT_ESTABLISHED"


class CandidateGroundingBasis:
    """HOW the reconciler established the target, independently of what the model claimed.

    G3-7F. The G3-7C/G3-7D evaluations showed a model can assert `CONFIRMED` for a referent
    the customer explicitly excluded. So target selection may not read status or scope as
    its ground. These three are the only admissible grounds, and each is checked against
    canonical state and the literal burst — never against the interpreter's confidence.
    """
    NONE = "NONE"
    #: G1 — an admitted mention whose make AND model were said literally, matching one row.
    G1_IDENTITY = "G1_IDENTITY"
    #: G2 — exactly one candidate, strict canonical focus, and nothing excluding it.
    G2_SINGLETON = "G2_SINGLETON"
    #: G3 — a typed in-burst reference resolving to one row (superseded mentions included).
    G3_REFERENCE = "G3_REFERENCE"

    ALL = (NONE, G1_IDENTITY, G2_SINGLETON, G3_REFERENCE)


class CandidateSelectionBasis:
    """WHY the reconciler chose the candidate it chose.

    G3-7B. Two decisions can share an action and a target and still rest on different
    authority — a re-mention of the focused car and a subjectless correction of it both
    update the same row. The Inspector has to be able to tell them apart, which means the
    basis is recorded rather than inferred from the action.
    """
    NONE = "NONE"
    #: The customer's own words identified the vehicle.
    EVIDENCE_IDENTITY = "EVIDENCE_IDENTITY"
    #: The canonical focus pointer, under the strict four-condition predicate.
    FOCUS_POINTER = "FOCUS_POINTER"
    #: A correction's `target_ref` pointed at a vehicle mentioned in this same burst.
    EXPLICIT_REFERENCE = "EXPLICIT_REFERENCE"
    #: "el otro" — the one candidate that is not the current focus.
    SOLE_NON_FOCUS = "SOLE_NON_FOCUS"

    ALL = (NONE, EVIDENCE_IDENTITY, FOCUS_POINTER, EXPLICIT_REFERENCE, SOLE_NON_FOCUS)


#: The reconciler's rule set, versioned so a decision can be replayed against the rules that
#: made it. Bump when the SELECTION rules change, never for wording.
CANDIDATE_RULE_ID = "reconcile.candidate_selection"
#: G3-7F. v2 is the Grounded-Target Invariant: the reconciler establishes the target from
#: a ground it verifies itself, never from the interpreter's own confidence labels.
CANDIDATE_RULE_VERSION = "v2"


@dataclass(frozen=True)
class CandidateResolution:
    """Which candidate the turn's admitted evidence affects, and what may change.

    G3-7-R2 (cutover audit G37-A01). The reply model used to answer this by emitting a
    `candidate` dict carrying a database id and a CRM status — reconciliation work dressed
    as interpretation. `VehicleEvidence` correctly describes only what the customer
    MENTIONED; this is the deterministic step that turns a mention plus the thread's current
    candidates into a canonical decision.

    It carries no customer prose: evidence is referenced by ref, never quoted.
    """
    thread_id: object = None
    turn_id: Optional[str] = None
    action: str = CandidateAction.NONE
    target_candidate_id: Optional[int] = None
    created_candidate_id: Optional[int] = None
    previous_focus_id: Optional[int] = None
    resulting_focus_id: Optional[int] = None
    focus_transition_required: bool = False
    locality_binding_permitted: bool = False
    zone_group: Optional[str] = None
    zone_detail: Optional[str] = None
    vehicle_evidence_ref: Optional[str] = None
    location_evidence_ref: Optional[str] = None
    hold_reason: Optional[str] = None
    rule_id: str = CANDIDATE_RULE_ID
    rule_version: str = CANDIDATE_RULE_VERSION
    mutation_applied: bool = False
    correlation_id: Optional[str] = None
    #: How many existing candidates the evidence was compatible with. 2+ is why HOLD.
    compatible_candidate_count: int = 0
    # ── G3-7B: correction-target authority ─────────────────────────────────────
    #: Why this candidate — see `CandidateSelectionBasis`.
    selection_basis: str = CandidateSelectionBasis.NONE
    #: Evidence-local ref of the correction that licensed the decision. Never prose.
    correction_evidence_ref: Optional[str] = None
    #: The linguistic relation the interpreter reported (`CorrectionRelation` value).
    target_relation: Optional[str] = None
    #: `((field, before, after), ...)` — canonical VALUES only, never customer text.
    corrected_fields: tuple = ()
    #: G3-7F. WHICH ground established the target — see `CandidateGroundingBasis`.
    grounding_basis: str = CandidateGroundingBasis.NONE
    #: G3-7F. Was every corrected value present literally in the canonical burst?
    literally_grounded: Optional[bool] = None
    #: Did the canonical zone table recognise the corrected locality?
    zone_lookup_hit: Optional[bool] = None
    #: Set by the turn once the re-quote guards have run, so the trace shows the
    #: consequence of the correction and not merely its intent.
    quote_invalidated: Optional[bool] = None
    quote_recomputed: Optional[bool] = None

    @property
    def mutates(self) -> bool:
        return self.action in (CandidateAction.REUSE_FOCUS, CandidateAction.REUSE_EXISTING,
                               CandidateAction.CREATE, CandidateAction.CORRECT_EXISTING)

    def to_payload(self) -> dict:
        return {
            "action": self.action,
            "target_candidate_id": self.target_candidate_id,
            "created_candidate_id": self.created_candidate_id,
            "previous_focus_id": self.previous_focus_id,
            "resulting_focus_id": self.resulting_focus_id,
            "focus_transition_required": self.focus_transition_required,
            "locality_binding_permitted": self.locality_binding_permitted,
            "zone_group": self.zone_group, "zone_detail": self.zone_detail,
            "vehicle_evidence_ref": self.vehicle_evidence_ref,
            "location_evidence_ref": self.location_evidence_ref,
            "hold_reason": self.hold_reason,
            "compatible_candidate_count": self.compatible_candidate_count,
            "rule_id": self.rule_id, "rule_version": self.rule_version,
            "mutation_applied": self.mutation_applied,
            "correlation_id": self.correlation_id,
            "selection_basis": self.selection_basis,
            "grounding_basis": self.grounding_basis,
            "literally_grounded": self.literally_grounded,
            "correction_evidence_ref": self.correction_evidence_ref,
            "target_relation": self.target_relation,
            "corrected_fields": [
                {"field": f, "before": b, "after": a} for f, b, a in self.corrected_fields],
            "zone_lookup": {"locality": self.zone_detail, "hit": self.zone_lookup_hit,
                            "zone_group": self.zone_group},
            "quote_invalidated": self.quote_invalidated,
            "quote_recomputed": self.quote_recomputed,
        }


@dataclass
class ReplyModelRecord:
    """The reply-generation role, recorded separately from the semantic role.

    One configured model may serve both; the roles are distinguished by call site, prompt
    and consumer, so attribution stays truthful even when the model name is identical.
    """
    model: Optional[str] = None
    prompt_version: Optional[str] = None
    #: G3-5E2 (audit G5E-05). WHICH reply call site ran — COMMERCIAL_TURN or
    #: GENERAL_INFORMATION. The 1.4 contract promised it and 1.4 shipped without it.
    role: Optional[str] = None
    #: The typed admission verdict for this turn's reply result (G3-5E2, audit G5E-01).
    admission: Optional[str] = None
    calls: int = 0
    latency_ms: Optional[int] = None
    status: str = "ABSENT"          # ABSENT | OK | ERROR | TIMEOUT

    def to_payload(self) -> dict:
        return {"model": self.model, "prompt_version": self.prompt_version,
                "role": self.role, "admission": self.admission,
                "calls": self.calls, "latency_ms": self.latency_ms, "status": self.status}


class FloorStrength:
    """How much a deterministic FAQ floor match is worth as evidence.

    G3-2 DEF-03: `_HOURS_FAQ_DETECTION` won outright on a bare substring, so the word
    "horarios" — which is equally the vocabulary of asking about the AGENDA — selected the
    opening-hours answer and no decision was recorded. The set is unchanged and is not
    enlarged here; it is PARTITIONED, so a match that names the business's activity can
    still carry a decision and a bare time-noun cannot win against clear semantic evidence.
    """
    NONE = "NONE"
    WEAK = "WEAK"       # a bare time noun: ambiguous between opening hours and the agenda
    STRONG = "STRONG"   # names the business's own hours or working days

    ALL = (NONE, WEAK, STRONG)


#: What each decision site is actually deciding, in the operator's language.
DECISION_PURPOSE = {
    DecisionSite.VEHICLE_IDENTITY_APPLY:
        "escribir la identidad del vehículo en el candidato",
    DecisionSite.LOCATION_INSPECTION_APPLY:
        "escribir la localidad de inspección",
    DecisionSite.VEHICLE_FUZZY_ADMISSIBILITY:
        "admitir o rechazar una coincidencia difusa del catálogo como identidad",
    DecisionSite.SCHEDULING_REQUEST_RECONCILIATION:
        "decidir qué día y hora pidió el cliente",
    DecisionSite.QUOTE_ACCEPTANCE_AUTHORIZATION:
        "autorizar (o no) que el presupuesto quede aceptado",
    DecisionSite.HANDOFF_SEMANTIC_REQUEST:
        "decidir si el cliente pidió hablar con una persona",
    DecisionSite.LOCALITY_SEMANTIC_RECOVERY:
        "proponer una localidad para confirmar con el cliente",
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
    #: Canonical identity per claim, from an existing resolver — `None` when none applies
    #: or the value did not resolve. Display-safe by construction: a catalog identity is
    #: the catalog's own name for a thing, never the customer's words.
    canonical_values: tuple = ()
    #: `ClaimEvidence.confidence`, advisory only and never read by any rule. Carried so the
    #: authenticated Inspector can show what the producer itself claimed.
    confidences: tuple = ()
    withheld: bool = False


@dataclass
class PropositionComparison:
    """One canonical proposition, and what two or more producers proved about it.

    This is the unit 1.2 added. Before it, a row asked only "did anyone contradict anyone",
    and two producers who had never spoken about the same field answered no — which was
    then read as agreement.
    """
    claim_type: str
    sources: tuple = ()                 # the attributed sources that spoke to it
    canonical_by_source: tuple = ()     # (source, canonical-or-displayable, key) triples
    verdict: str = "UNPROVEN"           # ComparisonVerdict
    basis: Optional[str] = None         # which existing resolver, or why it is unproven


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

    # ── hybrid-decision-trace/1.1, amended by 1.2 ───────────────────────────
    contract_version: str = TRACE_VERSION
    #: What this comparison IS: decision site, proposition, rule, and the sorted content
    #: hashes of the claims weighed. Deliberately carries NO positional component, so
    #: inserting an unrelated reconciliation earlier in the turn cannot change it, and the
    #: same logical comparison keeps its identity across deployments.
    logical_comparison_id: Optional[str] = None
    #: Which execution of that logical comparison this is, within this turn. Positional,
    #: and kept OUT of the identity above — that separation is the whole point.
    occurrence_index: int = 0
    #: An explicit literal from `DecisionSite`, supplied by the call site.
    decision_site_id: Optional[str] = None
    decision_purpose: Optional[str] = None
    #: Distinct sources that actually supplied usable evidence to THIS call.
    participating_sources: tuple = ()
    #: Sources that produced evidence elsewhere in the turn and did not reach this call.
    not_routed_sources: tuple = ()
    source_evidence: tuple = ()          # tuple[SourceContribution]
    #: One entry per canonical proposition at least two attributed producers spoke to.
    #: Empty means no proposition was shared — which is `PARALLEL_EVIDENCE`, not agreement.
    compared_propositions: tuple = ()    # tuple[PropositionComparison]
    #: Sources whose own claims contradict each other. Recorded separately from any
    #: cross-producer verdict: a producer disagreeing with itself is not two engines
    #: disagreeing, and must never be rendered as one.
    self_contradiction_sources: tuple = ()
    #: Evidence about POLARITY. Never sufficient on its own to classify the row.
    information_state: Optional[str] = None
    error_category: Optional[str] = None

    # ── hybrid-decision-trace/1.3 (G3-1) ─────────────────────────────────────
    # Five things a reader must be able to tell apart. Before G3-1 a row carried the
    # first and the sixth and nothing between them, so "the reconciler accepted it" and
    # "canonical state changed" and "the customer was answered" were one undifferentiated
    # fact. They are not one fact, and conflating them is how an evidence classification
    # gets mistaken for permission.
    #
    #: What the real authority returned, in ITS OWN vocabulary — `ACCEPT`/`HOLD` from a
    #: field reconciler, `ALLOW`/`DENY`/`CLARIFY`/`HOLD` from the acceptance authorizer,
    #: `semantic`/`deterministic`/`deterministic_conflict` from the scheduling reconciler.
    #: Never translated, because translation is where meaning is lost.
    authority_result: Optional[str] = None
    #: A DOMAIN COMPARATOR's own cross-producer finding, when one was made — a
    #: `ComparisonVerdict`. Renamed from `authority_verdict` in G3-1-R2 because that name
    #: invited exactly the confusion the audit found: this is an EVIDENCE comparison made
    #: with domain knowledge (`semantic_covers_deterministic` compares resolved dates and
    #: times), not an action-authority result. `authority_result` above is the action one,
    #: and it never touches the classification.
    domain_verdict: Optional[str] = None
    #: Whether that finding was admitted, and if not, why — a `DomainVerdictAdmission`.
    #: A rejection is recorded so a disagreement between the generic and domain comparators
    #: is visible rather than silently resolved.
    domain_verdict_admission: str = "NOT_SUPPLIED"
    #: What this decision did to canonical state — a `CanonicalEffect`.
    canonical_effect: Optional[str] = None
    #: The action this decision permitted, named for the business, or `None` for a decision
    #: that permitted nothing.
    permitted_action: Optional[str] = None
    #: The customer-facing outcome that actually followed, when the site knows it.
    business_outcome: Optional[str] = None


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
    #: LEGACY (1.0–1.3): this is `answer_source`, which conflates decision, facts and prose.
    #: Kept verbatim so existing consumers and older rows keep their meaning. The truthful
    #: separation is in the four fields below.
    response_plan_source: Optional[str] = None
    # ── G3-5E (audit G5D-04/05/08), contract 1.4 ────────────────────────────
    decision_authority: Optional[str] = None      # who authorized the action
    fact_sources: tuple = ()                      # who supplied the facts (several)
    renderer_source: Optional[str] = None         # who wrote the words
    dispatch_path: Optional[str] = None           # which transport attempted it
    dispatch_result: Optional[str] = None         # what the gate decided — never a source
    reply: Optional[Any] = None                   # ReplyModelRecord payload
    response_plan: Optional[Any] = None           # shadow ResponsePlan payload
    response_plan_id: Optional[str] = None
    #: G3-7B, contract 1.6. The candidate decision for this turn — which row the reconciler
    #: selected, why, what it corrected, and what the writer actually did. Absent on 1.5 and
    #: earlier rows, which therefore remain readable without a migration.
    candidate_resolution: Optional[Any] = None
    #: G3-7F, contract 1.7. The provisional patch this turn armed, confirmed, rejected,
    #: superseded or expired — and every commercial action it blocked. Absent on 1.6 rows.
    pending_correction: Optional[Any] = None
    # ── G3-7F-OWNER-RESOLUTION contract 1.8 ──
    #: Why this turn did or did not ask the customer anything: the proven domain, the route
    #: the typed router chose, its reason, which Flow was dispatched and how it went, and
    #: the generic-handoff reason when a person took over. The owner's Part H makes this
    #: measurable on purpose — the hybrid architecture is supposed to REDUCE clarification
    #: Flows, so "resolved without a Flow" has to be countable, not merely absent.
    clarification: Optional[Any] = None
    response_item_count: Optional[int] = None
    response_item_kinds: tuple = ()
    suppressed_item_count: Optional[int] = None
    suppressed_reasons: tuple = ()
    final_message_count: Optional[int] = None
    flow_participation: Optional[str] = None
    burst_fingerprint: Optional[str] = None
    decision_phase_id: Optional[str] = None
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
