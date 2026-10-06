"""The single typed clarification decision — pure, and the only authority.

G3-7F clarification lifecycle. This module replaces `route_clarification` in
`pending_correction.py` (which was about pending corrections, not clarification) and
replaces the two inherited substring authorities that actually decided clarification on the
live path: `_routing_gate`'s Priorities 2, 4 and 5, and `_check_fallback_flow_triggers`.
Those decided by matching `_PRICE_INTENT_WORDS`, `_VEHICLE_CONCERN_WORDS` and
`_FAQ_OR_SOFT_CLOSE_PHRASES` against the customer's text.

Nothing here reads customer text. Every input is a boolean another authority established —
the reconciler, the canonical evidence snapshot, the catalogue, the attempt store — and the
function's whole job is to order those facts into one decision.

TWO FACTS, NOT ONE
------------------
`*_attempt_spent` and `*_flow_outstanding` are different inputs on purpose, and conflating
them is the defect this subsystem exists to fix. "Spent" is immutable attempt history from
`whatsapp_clarification_attempts` and cannot be cleared by a completion or a handoff.
"Outstanding" is the mutable `*_fallback_flow_sent` flag and may be cleared freely. Only
"spent" decides availability.
"""
from __future__ import annotations


class ClarificationRoute:
    """What this turn needs in order to proceed safely."""
    NO_CLARIFICATION_REQUIRED = "NO_CLARIFICATION_REQUIRED"
    CORRECTION_CONFIRMATION_REQUIRED = "CORRECTION_CONFIRMATION_REQUIRED"
    VEHICLE_FLOW_REQUIRED = "VEHICLE_FLOW_REQUIRED"
    LOCATION_FLOW_REQUIRED = "LOCATION_FLOW_REQUIRED"
    GENERIC_HUMAN_REQUIRED = "GENERIC_HUMAN_REQUIRED"

    ALL = (NO_CLARIFICATION_REQUIRED, CORRECTION_CONFIRMATION_REQUIRED,
           VEHICLE_FLOW_REQUIRED, LOCATION_FLOW_REQUIRED, GENERIC_HUMAN_REQUIRED)


class ClarificationReason:
    """Why the router chose what it chose. Enumerated so the trace never says "other"."""
    RESOLVED = "RESOLVED"
    HUMAN_OWNS_TURN = "HUMAN_OWNS_TURN"
    CORRECTION_VALUES_KNOWN = "CORRECTION_VALUES_KNOWN"
    VEHICLE_IDENTITY_UNRESOLVED = "VEHICLE_IDENTITY_UNRESOLVED"
    VEHICLE_ATTEMPT_SPENT = "VEHICLE_ATTEMPT_SPENT"
    VEHICLE_FLOW_OUTSTANDING = "VEHICLE_FLOW_OUTSTANDING"
    VEHICLE_CATEGORY_UNRESOLVED = "VEHICLE_CATEGORY_UNRESOLVED"
    LOCATION_UNRESOLVED = "LOCATION_UNRESOLVED"
    LOCATION_ATTEMPT_SPENT = "LOCATION_ATTEMPT_SPENT"
    LOCATION_FLOW_OUTSTANDING = "LOCATION_FLOW_OUTSTANDING"
    #: Owner policy carried forward from the R2 range: a clarification Flow must not be
    #: dispatched beside another live confirmable proposal, or one "sí" has two referents.
    PROPOSAL_WOULD_COEXIST = "PROPOSAL_WOULD_COEXIST"
    DOMAIN_NOT_RELEVANT = "DOMAIN_NOT_RELEVANT"

    ALL = (RESOLVED, HUMAN_OWNS_TURN, CORRECTION_VALUES_KNOWN,
           VEHICLE_IDENTITY_UNRESOLVED, VEHICLE_ATTEMPT_SPENT,
           VEHICLE_FLOW_OUTSTANDING, VEHICLE_CATEGORY_UNRESOLVED,
           LOCATION_UNRESOLVED, LOCATION_ATTEMPT_SPENT, LOCATION_FLOW_OUTSTANDING,
           PROPOSAL_WOULD_COEXIST, DOMAIN_NOT_RELEVANT)


class TurnClarificationDecision:
    """The single clarification decision made for one conversational turn.

    Carries the pure router's verdict together with the two facts the router cannot know:
    which family the verdict is about, and whether the attempt store was able to answer at
    all. Store availability travels WITH the decision so no later branch has to re-derive it
    — and so "the customer has already been asked" can never be confused with "we could not
    find out" (SEQ-02).
    """

    __slots__ = ("route", "reason", "family", "store_available", "store_fault")

    def __init__(self, route: str, reason: str, family=None,
                 store_available: bool = True, store_fault=None) -> None:
        self.route = route
        self.reason = reason
        self.family = family
        self.store_available = store_available
        self.store_fault = store_fault

    @property
    def requires_clarification(self) -> bool:
        """True when this turn cannot proceed without asking or escalating."""
        return self.route in (ClarificationRoute.VEHICLE_FLOW_REQUIRED,
                              ClarificationRoute.LOCATION_FLOW_REQUIRED,
                              ClarificationRoute.GENERIC_HUMAN_REQUIRED)

    @property
    def requires_flow(self) -> bool:
        return self.route in (ClarificationRoute.VEHICLE_FLOW_REQUIRED,
                              ClarificationRoute.LOCATION_FLOW_REQUIRED)

    def __repr__(self) -> str:                                   # pragma: no cover - debug
        return (f"TurnClarificationDecision({self.route}, {self.reason}, "
                f"family={self.family}, store_available={self.store_available})")


def clarification_decision(*,
                           human_owns_turn: bool = False,
                           correction_target_known: bool = False,
                           correction_values_known: bool = False,
                           vehicle_domain_relevant: bool = False,
                           vehicle_identity_resolved: bool = True,
                           vehicle_category_resolved: bool = True,
                           vehicle_attempt_spent: bool = False,
                           vehicle_flow_outstanding: bool = False,
                           location_domain_relevant: bool = False,
                           location_resolved: bool = True,
                           location_attempt_spent: bool = False,
                           location_flow_outstanding: bool = False,
                           other_confirmable_proposal: bool = False,
                           required_next_information_authorized: bool = False
                           ) -> tuple[str, str]:
    """`(route, reason)` for this turn. Pure; every argument is an established fact.

    The order encodes the owner's policy and is the whole of it:

      * human ownership stops autonomous clarification outright;
      * a grounded correction whose values are already known asks the ordinary typed
        confirmation and never a Flow — a correction is not a clarification;
      * vehicle uncertainty outranks location uncertainty, so a both-unresolved turn offers
        only the Vehicle Flow and Location waits for the next reconciliation;
      * a known identity whose CATEGORY cannot be resolved does not get the Vehicle Flow,
        because re-asking an identity we already hold is not a question — it goes to a
        person, and never as a motorcycle;
      * a family whose attempt is already SPENT escalates rather than repeating, and spent
        is immutable history, not a flag a completion can clear;
      * a family whose Flow is still OUTSTANDING asks nothing further and waits;
      * a Flow is never dispatched beside another live confirmable proposal, because one
        affirmation would then have two referents;
      * and a domain that is not relevant to this turn gets nothing. Stored incomplete state
        is not relevance: an old half-filled candidate must not turn a payment question into
        a vehicle form.

    Defaults are the safe reading — `*_resolved` default True and `*_relevant` default False
    — so a caller that omits an argument gets silence rather than an unasked-for Flow.
    """
    if human_owns_turn:
        return (ClarificationRoute.NO_CLARIFICATION_REQUIRED,
                ClarificationReason.HUMAN_OWNS_TURN)

    if correction_target_known and correction_values_known:
        # Owner policy 8 outranks the confirmation: a typed correction confirmation is
        # itself a confirmable proposal, so asking for it beside another live one would
        # give a single "sí" two referents. A person resolves that, and the correction is
        # not lost — it is simply not auto-confirmed in the same breath as something else.
        if other_confirmable_proposal:
            return (ClarificationRoute.GENERIC_HUMAN_REQUIRED,
                    ClarificationReason.PROPOSAL_WOULD_COEXIST)
        return (ClarificationRoute.CORRECTION_CONFIRMATION_REQUIRED,
                ClarificationReason.CORRECTION_VALUES_KNOWN)

    # Relevance is current-turn evidence or an authorized required-next-information
    # proposition. Never the mere existence of stored state.
    vehicle_in_play = vehicle_domain_relevant or required_next_information_authorized
    location_in_play = location_domain_relevant or required_next_information_authorized

    if vehicle_in_play and not vehicle_identity_resolved:
        if vehicle_flow_outstanding:
            return (ClarificationRoute.NO_CLARIFICATION_REQUIRED,
                    ClarificationReason.VEHICLE_FLOW_OUTSTANDING)
        if vehicle_attempt_spent:
            return (ClarificationRoute.GENERIC_HUMAN_REQUIRED,
                    ClarificationReason.VEHICLE_ATTEMPT_SPENT)
        if other_confirmable_proposal:
            return (ClarificationRoute.GENERIC_HUMAN_REQUIRED,
                    ClarificationReason.PROPOSAL_WOULD_COEXIST)
        return (ClarificationRoute.VEHICLE_FLOW_REQUIRED,
                ClarificationReason.VEHICLE_IDENTITY_UNRESOLVED)

    # A known vehicle the catalogue cannot place: the Flow would ask for what we hold.
    if vehicle_in_play and vehicle_identity_resolved and not vehicle_category_resolved:
        return (ClarificationRoute.GENERIC_HUMAN_REQUIRED,
                ClarificationReason.VEHICLE_CATEGORY_UNRESOLVED)

    if location_in_play and not location_resolved:
        if location_flow_outstanding:
            return (ClarificationRoute.NO_CLARIFICATION_REQUIRED,
                    ClarificationReason.LOCATION_FLOW_OUTSTANDING)
        if location_attempt_spent:
            return (ClarificationRoute.GENERIC_HUMAN_REQUIRED,
                    ClarificationReason.LOCATION_ATTEMPT_SPENT)
        if other_confirmable_proposal:
            return (ClarificationRoute.GENERIC_HUMAN_REQUIRED,
                    ClarificationReason.PROPOSAL_WOULD_COEXIST)
        return (ClarificationRoute.LOCATION_FLOW_REQUIRED,
                ClarificationReason.LOCATION_UNRESOLVED)

    if not (vehicle_in_play or location_in_play):
        return (ClarificationRoute.NO_CLARIFICATION_REQUIRED,
                ClarificationReason.DOMAIN_NOT_RELEVANT)
    return (ClarificationRoute.NO_CLARIFICATION_REQUIRED, ClarificationReason.RESOLVED)
