"""Central outbound kill switch — M19.F2.2.

No WhatsApp / Meta Cloud API call may proceed unless ``OUTBOUND_ENABLED``
is set to the literal string ``"true"`` in the process environment.
Missing, empty, "false", "1", "True" — anything other than exactly
``"true"`` — means outbound is disabled.

The guard is enforced at two independent layers:

  Layer 1 (gate)   — ``OutboundSafetyGate.attempt()`` returns
                      ``GateOutcome.BLOCKED_KILL_SWITCH`` before writing
                      any pending record.  Gate-mediated paths (engine,
                      follow-up loops) never reach the network call.

  Layer 2 (sender) — ``enforce_outbound_enabled()`` is called at the top
                      of every ``_send_whatsapp_cloud_*`` function in
                      ``whatsapp_ui.py``.  Raises ``OutboundBlockedError``
                      for any path (API endpoints, dead code, future code)
                      that bypasses the gate.

In ``conversation_engine._check_fallback_flow_triggers`` two paths commit
state flags (``vehicle_clarification_sent``, ``location_clarification_sent``)
BEFORE calling the outbound send.  ``enforce_outbound_enabled()`` is
inserted between the flag mutation and ``db.commit()`` so that the flag is
never persisted when outbound is disabled; the caller's rollback in
``handle()`` reverts the in-memory mutation as well.
"""
from __future__ import annotations

import hashlib
import logging
import os
import unicodedata

logger = logging.getLogger(__name__)


class OutboundBlockedError(Exception):
    """Raised when an outbound send is blocked (kill switch, duplicate, or flood)."""

    def __init__(
        self,
        *,
        sender_path: str,
        kind: str,
        to_wa_id: str,
        thread_id: int | None = None,
        text: str | None = None,
        gate_outcome: str = "BLOCKED_KILL_SWITCH",
    ) -> None:
        self.sender_path = sender_path
        self.kind = kind
        self.to_wa_id = to_wa_id
        self.thread_id = thread_id
        self.text = text
        self.gate_outcome = gate_outcome
        suffix = to_wa_id[-4:] if len(to_wa_id) >= 4 else to_wa_id
        if gate_outcome == "BLOCKED_KILL_SWITCH":
            msg = (
                f"OUTBOUND_DISABLED: {sender_path} kind={kind} "
                f"to=...{suffix} thread_id={thread_id}"
            )
        else:
            msg = (
                f"OUTBOUND_BLOCKED({gate_outcome}): {sender_path} kind={kind} "
                f"to=...{suffix} thread_id={thread_id}"
            )
        super().__init__(msg)


def _fp(text: str | None) -> str:
    """Short fingerprint of text for log redaction — no full content in logs."""
    if not text:
        return "-"
    normalized = unicodedata.normalize("NFKD", text.strip().lower())
    return hashlib.sha256(" ".join(normalized.split()).encode()).hexdigest()[:8]


def enforce_outbound_enabled(
    sender_path: str,
    kind: str,
    to_wa_id: str,
    thread_id: int | None = None,
    text: str | None = None,
) -> None:
    """Raise ``OutboundBlockedError`` unless ``OUTBOUND_ENABLED == 'true'``.

    Must be called as the first action in every outbound send path, before
    any DB commit or Meta API call.  When the guard passes (env var is
    exactly ``"true"``), this function is a no-op with no side effects.
    """
    if os.environ.get("OUTBOUND_ENABLED") == "true":
        return
    suffix = to_wa_id[-4:] if len(to_wa_id) >= 4 else to_wa_id
    logger.warning(
        "OUTBOUND_KILL_SWITCH sender=%s kind=%s to=...%s thread_id=%s fp=%s",
        sender_path,
        kind,
        suffix,
        thread_id,
        _fp(text),
    )
    raise OutboundBlockedError(
        sender_path=sender_path,
        kind=kind,
        to_wa_id=to_wa_id,
        thread_id=thread_id,
        text=text,
    )
