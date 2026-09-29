"""L4.7W5-GATE3-G3-5F — the live response-composition authority.

WHAT THIS REPLACES
------------------
Until G3-5F the final customer message was assembled by whichever handler resolved
first, and then *repaired* on the way out: `_send_text_to_wa` consulted a side-channel
attribute (`_faq_reconciliation_burst`) and, if it was armed, appended or stripped
business facts in the already-composed prose. That mechanism had three defects the
G3-5D/G3-5E audits recorded:

  * nothing named the propositions a turn owed the customer, so a second producer could
    emit the same claim twice and nothing could tell;
  * the repair pass was armed by an attribute rather than authorized by a decision, so
    "was this answer allowed?" had no representation at all;
  * the owner policies P1-P6 existed only as prose in the milestone documents.

The plan below is the authority for all three. A turn REGISTERS typed propositions while
it runs; at the dispatch boundary the plan applies the policies, deduplicates by
proposition identity, orders, and composes exactly one message (or the one approved
Flow sequence). Registration is a proposal; inclusion is a decision this object makes.

WHAT IT DELIBERATELY IS NOT
---------------------------
It is not a phrase catalogue and it holds no wording of its own. Every proposition
carries a `render` callable supplied by the component that owns the fact, so canonical
truth continues to come from `schedule.business_hours_for_weekday()`, the pricing
service and the FAQ canon — never from here.

It does not choose the turn's principal action. That belongs to the reconciler and the
deterministic gates, which run before any proposition is registered. The plan decides
what the customer is TOLD, not what the business DOES.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ..schemas.hybrid_trace import (
    CoexistencePolicy, DecisionAuthority, DispatchPath, DispatchResult, FactSource,
    FLOW_COMPLETION_ACK_CONDITIONS, RendererSource, ResponseItem, ResponseItemKind,
    ResponsePlan, ResponsePolicy, TransportMultiplicity,
)


class PropositionId:
    """Stable claim identities. Deduplication keys on these, never on rendered text.

    Two producers asserting the same claim are ONE proposition even when their wording
    differs; two different claims that happen to share wording stay two.
    """
    PRIMARY_REPLY = "turn.primary_reply"
    CLARIFICATION = "turn.clarification"
    NO_RESPONSE = "turn.no_response"
    HANDOFF_ACK = "handoff.human_owned"
    FLOW_INTRO = "scheduling.flow_intro"
    BOOKING_FLOW = "scheduling.booking_flow"
    BOOKING_RECEIPT = "booking.receipt"
    FLOW_COMPLETION_ACK = "booking.flow_completion_ack"

    @staticmethod
    def faq(topic: str) -> str:
        return f"faq.{topic}"


class SuppressionReason:
    """Why an authorized proposition did not reach the customer."""
    HUMAN_OWNS_TURN = "HUMAN_OWNS_TURN"
    ALREADY_STATED = "ALREADY_STATED"
    DUPLICATE_PROPOSITION = "DUPLICATE_PROPOSITION"
    SINGLE_MESSAGE_POLICY = "SINGLE_MESSAGE_POLICY"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"


@dataclass
class Proposition:
    """One thing the turn may tell the customer, with WHO decided it and WHO worded it.

    `render` is a zero-argument callable owned by the component that holds the fact. The
    plan calls it at composition time and never earlier, so a proposition that ends up
    suppressed costs nothing and a canonical value is read as late as possible.
    """
    proposition_id: str
    kind: str
    claim_family: str
    render: Optional[Callable[[], str]] = None
    decision_authority: str = DecisionAuthority.NONE
    fact_sources: tuple = ()
    renderer_source: str = RendererSource.DETERMINISTIC_TEMPLATE
    policy: Optional[str] = None
    priority: int = 100
    coexistence: str = CoexistencePolicy.COMPATIBLE
    classification: Optional[str] = None
    required_action: Optional[str] = None
    dispatch_path: str = DispatchPath.TEXT
    #: Set when the producer has already established the claim is redundant this turn —
    #: P5's "do not repeat known facts mechanically".
    redundant: bool = False
    included: bool = True
    suppression_reason: Optional[str] = None

    def text(self) -> str:
        if self.render is None:
            return ""
        try:
            return (self.render() or "").strip()
        except Exception:
            # A renderer defect must never cost a customer turn: the proposition drops
            # out and the rest of the plan still composes.
            return ""


@dataclass
class LiveResponsePlan:
    """The propositions a turn owes the customer, and the one message they become.

    Lifecycle: constructed once per turn in `handle`; producers `register()` while the
    turn runs; the dispatch boundary calls `compose()` exactly once. After `compose()`
    the plan is sealed — a second text in the same turn is refused by P3 rather than
    silently sent, and the refusal is recorded.
    """
    turn_id: Optional[str] = None
    burst_fingerprint: Optional[str] = None
    decision_phase_id: Optional[str] = None
    propositions: list = field(default_factory=list)
    human_owns_turn: bool = False
    flow_sequence: bool = False
    composed: bool = False
    refused_second_text: int = 0
    dispatch_path: str = DispatchPath.NONE
    dispatch_result: str = DispatchResult.NOT_ATTEMPTED
    outcome: Optional[str] = None

    # ── registration ──────────────────────────────────────────────────────────

    def register(self, prop: Proposition) -> bool:
        """Propose a claim. Returns whether it was newly registered.

        A proposition already registered for this turn is NOT registered twice — this is
        the mechanism that makes "every authorized proposition appears exactly once"
        structural rather than a property of careful call ordering.
        """
        if any(p.proposition_id == prop.proposition_id for p in self.propositions):
            return False
        self.propositions.append(prop)
        return True

    def registered(self, proposition_id: str) -> bool:
        return any(p.proposition_id == proposition_id for p in self.propositions)

    # ── policy ────────────────────────────────────────────────────────────────

    def _apply_policies(self) -> None:
        """P1, P2, P5 and P6, applied to the registered propositions. In this order.

        P2 runs first because it is exclusive: once the human-handoff policy owns the
        turn, no automated factual answer may stand beside the acknowledgement, so the
        later policies have nothing left to weigh.
        """
        # ── P2: human handoff owns the turn ───────────────────────────────────
        if self.human_owns_turn:
            for p in self.propositions:
                if p.kind in (ResponseItemKind.HANDOFF_ACK, ResponseItemKind.NO_RESPONSE):
                    continue
                p.included = False
                p.suppression_reason = SuppressionReason.HUMAN_OWNS_TURN
                # The policy field on a SUPPRESSED item names the policy that withheld it,
                # not the one that would have authorized it. An operator asking "why did
                # the customer not get this?" is answered by the rule that said no.
                p.policy = ResponsePolicy.P2_HUMAN_HANDOFF_EXCLUSIVE
        # ── P6: a fact the producer already established as redundant ──────────
        for p in self.propositions:
            if p.included and p.redundant:
                p.included = False
                p.suppression_reason = SuppressionReason.ALREADY_STATED
                p.policy = ResponsePolicy.P6_NO_ROUTINE_FACT_REPETITION
        # ── P1: a held principal action does not silence a safe unrelated FAQ ──
        # Nothing to suppress: the FAQ propositions registered beside a HOLD are exactly
        # the ones P1 authorizes. Recorded so the policy is visible in the trace.
        if any(p.included and p.kind == ResponseItemKind.FAQ_ANSWER
               for p in self.propositions):
            for p in self.propositions:
                if p.included and p.kind == ResponseItemKind.CLARIFICATION:
                    p.policy = p.policy or ResponsePolicy.P1_HOLD_PLUS_SAFE_FAQ

    def _ordered_included(self) -> list:
        return sorted((p for p in self.propositions if p.included),
                      key=lambda p: (p.priority, p.proposition_id))

    # ── composition ───────────────────────────────────────────────────────────

    def compose(self, primary_text: str) -> str:
        """P3. The turn's propositions become ONE message, in plan order.

        `primary_text` is the prose the resolving handler produced. It enters the plan as
        the PRIMARY_REPLY proposition rather than as a privileged string, so it is ordered,
        policed and attributed exactly like every other contribution — and so a turn whose
        primary action was suppressed by P2 cannot smuggle its prose out anyway.
        """
        primary = next((p for p in self.propositions
                        if p.proposition_id == PropositionId.PRIMARY_REPLY), None)
        if primary is None:
            primary = Proposition(
                proposition_id=PropositionId.PRIMARY_REPLY,
                kind=ResponseItemKind.TEXT, claim_family="turn",
                render=lambda: primary_text, priority=10,
                coexistence=CoexistencePolicy.REQUIRED,
                policy=ResponsePolicy.P3_SINGLE_MESSAGE_DEFAULT)
            self.propositions.insert(0, primary)
        else:
            primary.render = lambda: primary_text
        self._apply_policies()
        parts = [t for t in (p.text() for p in self._ordered_included()) if t]
        self.composed = True
        if not parts:
            return ""
        head, *rest = parts
        return head if not rest else (head.rstrip() + "\n\n" + " ".join(rest))

    def refuse_additional_text(self) -> None:
        """P3. A second free-text message in one decision turn is not a plan, it is a leak."""
        self.refused_second_text += 1

    # ── projection ────────────────────────────────────────────────────────────

    def to_plan(self, *, plan_id: str, renderer_source: str, outcome: Optional[str],
                authority: str = DecisionAuthority.NONE, fact_sources: tuple = (),
                extra_items: tuple = ()) -> ResponsePlan:
        """The typed record of what was planned — included and suppressed alike.

        `authority` and `fact_sources` describe the TURN, and are adopted by propositions
        that did not name their own. The primary reply is the case that matters: it is the
        resolving handler's prose, and the handler recorded who decided and which producers
        supplied the facts on the engine, not on the proposition. Adopting those here keeps
        the plan and the trace saying the same thing about the same turn — a proposition
        that DID name its own authority (the Flow-completion acknowledgement names
        FLOW_TRANSACTION) keeps it.
        """
        for p in self.propositions:
            if p.decision_authority == DecisionAuthority.NONE and authority:
                p.decision_authority = authority
            if not p.fact_sources and fact_sources:
                p.fact_sources = tuple(fact_sources)
            if (p.proposition_id == PropositionId.PRIMARY_REPLY
                    and p.renderer_source == RendererSource.DETERMINISTIC_TEMPLATE
                    and renderer_source not in (RendererSource.NO_PROSE,)):
                p.renderer_source = renderer_source
        items = [
            ResponseItem(
                kind=p.kind, claim_family=p.claim_family,
                proposition_id=p.proposition_id,
                classification=p.classification,
                decision_authority=p.decision_authority,
                fact_sources=tuple(p.fact_sources),
                renderer_source=p.renderer_source,
                required_action=p.required_action,
                priority=p.priority, coexistence=p.coexistence,
                dispatch_path=p.dispatch_path, included=p.included,
                suppression_reason=p.suppression_reason, policy=p.policy)
            for p in sorted(self.propositions,
                            key=lambda p: (p.priority, p.proposition_id))
        ]
        items.extend(extra_items)
        return ResponsePlan(
            plan_id=plan_id, turn_id=self.turn_id,
            burst_fingerprint=self.burst_fingerprint,
            decision_phase_id=self.decision_phase_id,
            items=tuple(items),
            transport_multiplicity=(TransportMultiplicity.FLOW_INTRO_THEN_FLOW
                                    if self.flow_sequence
                                    else TransportMultiplicity.SINGLE_MESSAGE),
            renderer_source=renderer_source, dispatch_path=self.dispatch_path,
            dispatch_result=self.dispatch_result, shadow=False, outcome=outcome)


def flow_completion_ack_authorized(evidence: dict) -> tuple:
    """P5/§12. Every pinned condition, evaluated against evidence the caller supplies.

    Returns `(authorized, unmet)`. The conditions come from
    `FLOW_COMPLETION_ACK_CONDITIONS` rather than from a list written here, so relaxing
    one means editing the pinned contract — which a test watches.

    A missing key is UNMET, never assumed. An acknowledgement that a booking exists is a
    business fact told to a customer; the default for an unanswered question is silence.
    """
    unmet = tuple(c for c in FLOW_COMPLETION_ACK_CONDITIONS
                  if not bool(evidence.get(c, False)))
    return (not unmet), unmet
