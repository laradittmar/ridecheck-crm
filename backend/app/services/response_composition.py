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
    #: G3-6. The one required-next-information question, keyed by the field it is missing,
    #: so two producers asking for the same field are one proposition.
    CLARIFICATION_REQUIRED = "clarification.required"
    #: G3-6. A canonical fact the response must reconcile rather than imply.
    CANONICAL_VEHICLE = "canonical.vehicle"
    #: G3-6-BC (audit G36-03). The price the pricing service authorized for THIS turn. It is
    #: registered so it can survive the rejection of an opaque primary reply that happened to
    #: carry a false fact beside it — the customer asked what the revision costs, and the
    #: answer is owned by the pricing service, not by the sentence that contained it.
    CANONICAL_PRICE = "canonical.price"
    #: G3-6. Appointment times the thread really holds.
    SLOT_OFFER = "scheduling.slot_offer"
    #: G3-7F. One concise request to confirm a provisional correction. Carries only the
    #: corrected field/value pairs: the reconciler authorizes the proposition, the composer
    #: writes the words, and no prose lives in the reconciliation layer.
    CORRECTION_CONFIRMATION_REQUIRED = "correction.confirmation_required"
    #: G3-7F-R2 (review G37FRR-01). The corrected fact said back, ONCE, after the canonical
    #: write succeeded. Tier 1 applies a price-neutral year immediately, so this proposition
    #: is the only thing that makes an unasked mutation visible to the customer; G3-7F
    #: assembled the pairs and never rendered them.
    CORRECTION_APPLIED = "correction.applied"
    #: G3-6. Why the principal action is held, when P1 needs it said out loud.
    HOLD_EXPLANATION = "turn.hold_explanation"

    @staticmethod
    def faq(topic: str) -> str:
        return f"faq.{topic}"

    @staticmethod
    def clarification(field: str) -> str:
        return f"clarification.{field}"


class RequiredPropositionUnavailable(Exception):
    """A producer that owns a REQUIRED proposition failed.

    G3-7 (audit BC36-A03). The producer wrapper used to swallow every exception from every
    producer, so a broken price or FAQ producer silently dropped its proposition and the
    customer received a message that was quietly missing the thing they asked for — logged,
    but not visible to them and not represented anywhere in the plan.

    Optional producers still degrade quietly (and traceably). A REQUIRED one raises this,
    and the composition boundary fails CLOSED: it sends a bounded deterministic line
    instead of a partial answer that would read as complete. The customer is never shown an
    internal error.
    """

    def __init__(self, producer: str, cause: str):
        super().__init__(f"{producer}: {cause}")
        self.producer = producer
        self.cause = cause


class SuppressionReason:
    """Why an authorized proposition did not reach the customer."""
    HUMAN_OWNS_TURN = "HUMAN_OWNS_TURN"
    ALREADY_STATED = "ALREADY_STATED"
    DUPLICATE_PROPOSITION = "DUPLICATE_PROPOSITION"
    SINGLE_MESSAGE_POLICY = "SINGLE_MESSAGE_POLICY"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    #: G3-6 / DEF-04. The proposition asserted a consequential value that disagrees with the
    #: canonical one. The WHOLE proposition is withheld and the approved plan is rendered in
    #: its place — the prose is never edited fragment by fragment.
    CONFLICTS_WITH_CANONICAL = "CONFLICTS_WITH_CANONICAL"
    #: The producer registered it and the turn had nothing to say for it.
    NOTHING_TO_RENDER = "NOTHING_TO_RENDER"
    #: G3-7 (audit BC36-A03). An optional producer failed; its claim is absent and says so.
    PRODUCER_FAILED = "PRODUCER_FAILED"


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
    #: P6's "do not repeat known facts mechanically".
    #:
    #: G3-6: for every producer that exists today this is PROSE-derived — the claim is
    #: redundant because the primary reply already states it. That matters: if the primary
    #: is then rejected for contradicting canonical truth, the prose that made this claim
    #: redundant never reaches the customer, and suppressing it would lose the fact
    #: altogether. `_apply_policies` therefore restores every prose-derived redundancy when
    #: the primary is rejected. A producer whose redundancy comes from CANONICAL STATE must
    #: not register the proposition at all — it has nothing to say, not something to hide.
    redundant: bool = False
    #: G3-6 / DEF-04. Set when this proposition asserts a consequential value that
    #: contradicts the canonical one. A conflicting proposition is withheld WHOLE; the
    #: approved plan is rendered in its place. Nothing edits the text.
    conflicts_with_canonical: bool = False
    #: `(field, safe_token)` pairs naming the canonical values this item is answerable for.
    #: Never customer data — a field name and a value the business owns.
    locked_values: tuple = ()
    #: G3-7 (audit BC36-A01). A canonical value this proposition exists to deliver. When set,
    #: redundancy is DERIVED rather than asserted: the plan includes the item unless the
    #: composed message already carries this exact canonical token. The token is a value the
    #: business owns (a formatted amount), so this is canonical-value equality — not a
    #: substring search for a phrase, and not an assumption about what the primary said.
    delivers_value: Optional[str] = None
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
    #: G3-7 (audit BC36-A03). `(producer, exception type)` for every producer that raised.
    #: Optional failures are recorded and the turn continues; a required failure also sets
    #: `degraded`, and the boundary then refuses to send a partial answer.
    producer_failures: list = field(default_factory=list)
    degraded: bool = False
    #: The bounded deterministic line a degraded turn sends instead of a partial answer.
    #: Supplied by the engine so the copy stays with the rest of the engine's wording, and
    #: consumed INSIDE `compose()` so the boundary never rebinds the composed text — the
    #: "nothing is added after composition" invariant stays absolute.
    degraded_fallback: Optional[str] = None

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
        # ── DEF-04: a proposition that contradicts canonical truth is withheld WHOLE ──
        # This runs FIRST because it is not a preference: a message that states a business
        # fact incorrectly may not be sent, and the previous design's answer — cut the
        # offending sentence out of prose the system did not author and staple the canonical
        # answer to what is left — is the defect itself. Here the whole proposition drops
        # and every OTHER authorized proposition still composes, so the customer gets the
        # canonical answer plus anything else the turn owed them, rendered by its owner.
        rejected_primary = False
        for p in self.propositions:
            if p.conflicts_with_canonical:
                p.included = False
                p.suppression_reason = SuppressionReason.CONFLICTS_WITH_CANONICAL
                if p.proposition_id == PropositionId.PRIMARY_REPLY:
                    rejected_primary = True
        if rejected_primary:
            # The prose that made other claims redundant is not being sent, so those claims
            # are not stated anywhere. Restoring them is what makes the replacement
            # complete rather than merely safe: the customer gets every authorized
            # proposition, not only the one that caused the rejection.
            for p in self.propositions:
                if p.redundant and not p.conflicts_with_canonical:
                    p.redundant = False
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
        #
        # G3-7 (audit BC36-A01). A proposition that DELIVERS a canonical value no longer
        # takes the producer's word for whether it was already said. The producer used to
        # assert `redundant=True` on the strength of the turn having declared a
        # PRICING_SERVICE answer — true at every site today, but unenforced, and when it
        # was wrong the customer received no price at all while the trace claimed the
        # price had already been stated.
        #
        # Redundancy is now DERIVED: the item is redundant only if a proposition that IS
        # going out already carries the exact canonical token. That is value equality on a
        # value the business owns, not a phrase search, and it cannot silently swallow the
        # only copy of an authorized fact.
        for p in self.propositions:
            if p.included and p.delivers_value is not None:
                p.redundant = self._value_already_delivered(p)
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

    def _value_already_delivered(self, prop: "Proposition") -> bool:
        """Is `prop.delivers_value` already carried by another INCLUDED proposition?

        Only propositions that are actually going out count: a claim withheld for conflict
        or exclusivity delivers nothing, so it cannot make this one redundant. The
        comparison is on the canonical token itself, which the business owns and this plan
        rendered — never on the customer's or the model's phrasing.
        """
        token = prop.delivers_value
        if not token:
            return False
        for other in self.propositions:
            if other is prop or not other.included:
                continue
            if token in other.text():
                return True
        return False

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
        if self.degraded:
            # BC36-A03: a REQUIRED proposition could not be produced, so whatever survived
            # would read as a complete answer while quietly missing what the customer asked
            # for. Fail closed to the bounded line, here, so nothing downstream has to
            # rebind the composed text.
            self.composed = True
            return (self.degraded_fallback or "").strip()
        # A rejected primary must not leave the turn silent when the plan still holds
        # authorized propositions: composition continues from those below. If it holds
        # none, the turn genuinely has nothing it may truthfully say, and says nothing.
        parts = [t for t in (p.text() for p in self._ordered_included()) if t]
        self.composed = True
        if not parts:
            return ""
        head, *rest = parts
        return head if not rest else (head.rstrip() + "\n\n" + " ".join(rest))

    @property
    def mixed_renderers(self) -> bool:
        """True when the composed message carries words from more than one author.

        The primary reply is usually the model's; a canonical FAQ answer, a clarification
        and a vehicle reconciliation are their owners'. When both kinds are included the
        renderer really is mixed, and the attribution has to say so.
        """
        kinds = {p.renderer_source for p in self._ordered_included()}
        return len(kinds) > 1

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
                suppression_reason=p.suppression_reason, policy=p.policy,
                locked_values=tuple(p.locked_values))
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


# ── G3-7F-R2: correction wording lives HERE ───────────────────────────────────────────
#
# Review findings G37FRR-01 and G37FRR-02: the reconciler assembled a typed correction
# proposition and an echo, and then rendered the customer-facing sentence itself in
# `ConversationEngine._render_correction_confirmation` while the typed values went nowhere.
# Both halves were wrong in the same way — the authority that decides WHAT changed was also
# choosing the WORDS. These renderers are the composer's, they take canonical field/value
# pairs and nothing else, and they are the only place correction prose exists.

#: Canonical field -> the fragment a customer hears for it. `anio` renders as "modelo 2018"
#: because that is how a model year is spoken in Argentina; the vehicle's model renders the
#: same way for the same reason, and a turn correcting both reads naturally.
_CORRECTION_PHRASE: dict = {
    "anio": "modelo {value}",
    "marca": "marca {value}",
    "modelo": "modelo {value}",
    "version_text": "versión {value}",
    "tipo_vehiculo": "un {value}",
    "zone_detail": "la inspección en {value}",
}


def correction_fragments(fields) -> list:
    """The renderable fragments for `((field, value), ...)`, in the order given.

    `zone_group` is deliberately absent from the phrase table: it is the administrative
    grouping behind a locality the customer never names, and repeating it would state a fact
    they did not give us. A field with no fragment contributes nothing.
    """
    out = []
    for entry in (fields or ()):
        try:
            field, value = entry[0], entry[1]
        except (TypeError, IndexError):
            continue
        template = _CORRECTION_PHRASE.get(str(field))
        if template is None or value is None or str(value).strip() == "":
            continue
        out.append(template.format(value=str(value).strip()))
    return out


def render_correction_applied(fields) -> str:
    """Tier 1: the corrected fact, stated back. Empty when nothing is renderable."""
    parts = correction_fragments(fields)
    if not parts:
        return ""
    return f"Perfecto, entonces es {', '.join(parts)}."


def render_correction_confirmation(fields) -> str:
    """Tier 2: ask once about the corrected fact, and about nothing else.

    No quote, no price, no slot, no unrelated known value — a provisional patch has not been
    applied, so the only thing this may assert is what the customer just said.
    """
    parts = correction_fragments(fields)
    if not parts:
        return ""
    return f"Entonces es {', '.join(parts)}, ¿es correcto?"
