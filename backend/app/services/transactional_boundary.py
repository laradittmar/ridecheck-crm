"""R6-C4A — the transactional provenance boundary.

Transactional truth (a date, a time, slot availability, a price, a booking confirmation or a
booking status) is authorised by PROVENANCE and canonical structured facts, never by a
successful parse of generated prose.

The route of a reply-model decision is classified HERE, from the decision's role and the
thread's canonical state alone — never from the model's words. On a transactional route the
reply model's customer-visible body is discarded at the one boundary every reply decision
crosses (`ConversationEngine._reply_decision`), so it cannot reach the customer however it is
worded, and the engine renders the canonical deterministic response instead. The temporal
analyser (`response_validator`) stays as defence in depth; it no longer grants AI prose any
authority on these routes.

Pure functions and closed enums only: nothing here reads text, writes state or sends.
"""
from __future__ import annotations

import enum


class TransactionalRoute(enum.Enum):
    """What the reply of this turn is FOR. Closed set; classified from canonical state."""
    #: ordinary conversation / FAQ: AI prose is permitted (it carries no authority)
    NON_TRANSACTIONAL = "NON_TRANSACTIONAL"
    #: a price is, or would be, the subject (QUOTED stage, or a price question)
    QUOTATION = "QUOTATION"
    #: availability, a day or a time is the subject (SCHEDULING stage, or a scheduling request)
    SCHEDULING = "SCHEDULING"
    #: a Booking Flow is outstanding (FLOW_SENT, or a live booking token)
    FLOW_PENDING = "FLOW_PENDING"
    #: a booking is committed (BOOKED, or a committed revision is linked)
    BOOKED = "BOOKED"

    @property
    def transactional(self) -> bool:
        return self is not TransactionalRoute.NON_TRANSACTIONAL


#: model-extracted scheduling fields. They are never state, on any route: the deterministic
#: reading of the customer's own words (and ScheduleService) is the only scheduling source.
AI_SCHEDULE_FIELDS: tuple = ("preferred_time_str", "preferred_day_iso")

#: reply roles whose words may be AI prose on a non-transactional route
_COMMERCIAL = "COMMERCIAL_TURN"
_GENERAL_INFORMATION = "GENERAL_INFORMATION"

_STAGE_ROUTE = {
    "BOOKED": TransactionalRoute.BOOKED,
    "FLOW_SENT": TransactionalRoute.FLOW_PENDING,
    "SCHEDULING": TransactionalRoute.SCHEDULING,
    "QUOTED": TransactionalRoute.QUOTATION,
}


def classify_route(role, *, stage, has_flow_token: bool, has_committed_revision: bool,
                   price_signal: bool, scheduling_signal: bool) -> TransactionalRoute:
    """The route of one reply-model decision. Total and fail-closed.

    `role` is the decision's `ReplyRole` value. Only the two known roles exist; anything else
    is treated as the most constrained commercial reply (fail closed, never as FAQ).

    * GENERAL_INFORMATION (Layer D's FAQ route) is non-transactional by the owner's rule —
      unless the same turn carries a price or scheduling signal, or the thread holds a booking
      or an outstanding Booking Flow, in which case it is classified exactly like a commercial
      turn.
    * COMMERCIAL_TURN: a committed revision → BOOKED; a live booking token → FLOW_PENDING; the
      stage's own route; else a price signal → QUOTATION; a scheduling signal → SCHEDULING;
      else NON_TRANSACTIONAL (qualification conversation).

    The signals are the deterministic readings of the CUSTOMER's words that the engine already
    routes on. They can only make a route MORE constrained: failing to recognise one never
    authorises AI prose to state a transactional fact — on a non-transactional route the
    prose still carries no claim, writes no transactional state, and passes the default-deny
    validator.
    """
    role_v = getattr(role, "value", role)
    if has_committed_revision or stage == "BOOKED":
        return TransactionalRoute.BOOKED
    if has_flow_token or stage == "FLOW_SENT":
        return TransactionalRoute.FLOW_PENDING
    if role_v == _GENERAL_INFORMATION:
        if price_signal:
            return TransactionalRoute.QUOTATION
        if scheduling_signal:
            return TransactionalRoute.SCHEDULING
        return TransactionalRoute.NON_TRANSACTIONAL
    staged = _STAGE_ROUTE.get(stage)
    if staged is not None:
        return staged
    if price_signal:
        return TransactionalRoute.QUOTATION
    if scheduling_signal:
        return TransactionalRoute.SCHEDULING
    if role_v == _COMMERCIAL:
        return TransactionalRoute.NON_TRANSACTIONAL
    return TransactionalRoute.SCHEDULING          # unknown role: fail closed


def normalized(text) -> str:
    """Whitespace- and case-folded text, for the discarded-body tripwire only."""
    return " ".join(str(text or "").split()).casefold()
