"""Authorized outbound path registry — M2.

Defines every permitted WhatsApp outbound path.  Any send attempt that does not
identify itself with a path_id from AUTHORIZED_PATHS is blocked by the
OutboundSafetyGate and produces a BLOCKER SecurityEvent.

Canonical path IDs discovered by M21.3-TRACE-BLOCKER-META audit:

  CE_TEXT          — ConversationEngine text reply via _send_text_to_wa
  CE_FLOW          — ConversationEngine Flow dispatch via _send_flow_button
  CE_INTERACTIVE   — ConversationEngine interactive button message
  CE_LIST          — ConversationEngine list message
  MANUAL_CRM       — Operator-initiated manual reply from CRM UI
  BOOKING_FLOW     — Booking confirmation flow triggered after scheduling
  SYSTEM_NOTIFICATION — System-level operational notification (non-conversation)

Legacy paths (defined for detection only — NOT in AUTHORIZED_PATHS):
  LEGACY_N8N_AI_PIPELINE — n8n AI fallback branch (dead code since M18/CE).
                           Any attempt triggers LEGACY_SENDER_REACHED event.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from enum import Enum


class OutboundPathId(str, Enum):
    CE_TEXT = "CE_TEXT"
    CE_FLOW = "CE_FLOW"
    CE_INTERACTIVE = "CE_INTERACTIVE"
    CE_LIST = "CE_LIST"
    MANUAL_CRM = "MANUAL_CRM"
    BOOKING_FLOW = "BOOKING_FLOW"
    SYSTEM_NOTIFICATION = "SYSTEM_NOTIFICATION"
    # Legacy — detection only; not in AUTHORIZED_PATHS
    LEGACY_N8N_AI_PIPELINE = "LEGACY_N8N_AI_PIPELINE"


@dataclass(frozen=True)
class AuthorizedPath:
    path_id: OutboundPathId
    source_component: str
    allowed_message_types: frozenset[str]
    description: str


AUTHORIZED_PATHS: dict[OutboundPathId, AuthorizedPath] = {
    OutboundPathId.CE_TEXT: AuthorizedPath(
        path_id=OutboundPathId.CE_TEXT,
        source_component="conversation_engine.ConversationEngine._send_text_to_wa",
        allowed_message_types=frozenset({"text"}),
        description="CE text reply — _send_text_to_wa → _send_whatsapp_cloud_text",
    ),
    OutboundPathId.CE_FLOW: AuthorizedPath(
        path_id=OutboundPathId.CE_FLOW,
        source_component="conversation_engine.ConversationEngine._send_flow_button",
        allowed_message_types=frozenset({"flow"}),
        description="CE Flow dispatch — _send_flow_button → _send_whatsapp_cloud_flow",
    ),
    OutboundPathId.CE_INTERACTIVE: AuthorizedPath(
        path_id=OutboundPathId.CE_INTERACTIVE,
        source_component="conversation_engine.ConversationEngine",
        allowed_message_types=frozenset({"interactive"}),
        description="CE interactive button message via _send_whatsapp_cloud_interactive",
    ),
    OutboundPathId.CE_LIST: AuthorizedPath(
        path_id=OutboundPathId.CE_LIST,
        source_component="conversation_engine.ConversationEngine",
        allowed_message_types=frozenset({"list"}),
        description="CE list message via _send_whatsapp_cloud_list",
    ),
    OutboundPathId.MANUAL_CRM: AuthorizedPath(
        path_id=OutboundPathId.MANUAL_CRM,
        source_component="api.whatsapp.manual_send",
        allowed_message_types=frozenset({"text", "interactive"}),
        description="Operator-initiated manual reply from CRM UI",
    ),
    OutboundPathId.BOOKING_FLOW: AuthorizedPath(
        path_id=OutboundPathId.BOOKING_FLOW,
        source_component="conversation_engine.ConversationEngine._send_flow_button",
        allowed_message_types=frozenset({"flow"}),
        description="Booking confirmation flow after scheduling acceptance",
    ),
    OutboundPathId.SYSTEM_NOTIFICATION: AuthorizedPath(
        path_id=OutboundPathId.SYSTEM_NOTIFICATION,
        source_component="services.system_notification",
        allowed_message_types=frozenset({"text"}),
        description="System-level operational notification (non-conversation)",
    ),
}

LEGACY_PATHS: frozenset[str] = frozenset({
    OutboundPathId.LEGACY_N8N_AI_PIPELINE.value,
})


def is_authorized(path_id: str | None) -> bool:
    """Return True iff path_id resolves to a known authorized path."""
    if path_id is None:
        return False
    try:
        return OutboundPathId(path_id) in AUTHORIZED_PATHS
    except ValueError:
        return False


def is_legacy(path_id: str | None) -> bool:
    """Return True iff path_id is a known legacy (retired) send path."""
    return bool(path_id and path_id in LEGACY_PATHS)


# ── Deployment identity ───────────────────────────────────────────────────────

def _compute_deployment_id() -> str:
    """Read git SHA from env (injected at build time) or via git subprocess."""
    env_sha = os.environ.get("GIT_SHA", "").strip()
    if env_sha:
        return env_sha[:12]
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=os.path.dirname(__file__),
        ).decode().strip()
        return sha if sha else "unknown"
    except Exception:
        return "unknown"


_DEPLOYMENT_ID: str = _compute_deployment_id()


def get_deployment_id() -> str:
    return _DEPLOYMENT_ID
