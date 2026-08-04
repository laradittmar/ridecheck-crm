from __future__ import annotations

from pydantic import BaseModel, Field

# Actions that mean the engine fully owned the turn (n8n should stop)
HANDLED_ACTIONS = frozenset({
    "replied", "flow_button_sent", "booking_created",
    "skipped_human", "skipped_dedup",
    "human_handoff_blocked",        # M21.1.1: motorcycle/phone-call under kill switch
    "service_gate_blocked",         # M21.1.1: F12/transfer/repair/uncertain/FAQ under kill switch
    "inspectability_gate_blocked",  # M21.1.2: vehicle inspectability gate under kill switch
})


class ConversationHandleIn(BaseModel):
    # Core identifiers
    thread_id: int
    wa_message_id: str
    wa_id: str

    # Message content
    message_type: str = "text"  # text | audio | image | flow_response
    text: str | None = None  # Plaintext (or n8n-transcribed audio text)

    # n8n ráfaga pre-processing — first-class inputs
    # All messages sent by user since the last bot reply (may be >1 if burst)
    recent_user_messages: list[str] = Field(default_factory=list)
    # Subset of recent_user_messages that have NOT yet received a reply
    unanswered_recent_user_messages: list[str] = Field(default_factory=list)
    # The bot's N most recent outbound messages (for context / avoid repetition)
    recent_outbound_replies: list[str] = Field(default_factory=list)

    # Flow-specific
    flow_response: dict | None = None  # Parsed nfm_reply payload
    flow_token: str | None = None      # Flow token from nfm_reply


class ConversationHandleOut(BaseModel):
    ok: bool
    # replied | flow_button_sent | booking_created | skipped_human | skipped_dedup | no_lead | error | blocked_dispatch
    action: str
    # True  → engine owned the turn; n8n should stop
    # False → engine could not process; n8n may continue legacy fallback
    handled: bool = False
    wa_message_id: str | None = None
    detail: str | None = None
