"""SB2B dormant capability, carried into the integration branch (convergence Phase 1).

Track B's ledger-driven booking acknowledgement, verbatim from `870b4e3`
(`booking_flow_service.py`), where it was the Data Exchange path's receipt sender. In Phase 1
the active Data Exchange path is Track A's and keeps Track A's own receipt, so this code lives
apart from it. The only callers are dormant:

* the SB2B CE-W1 path, which runs only when SMART_BOOKING_CE_ENABLED is exactly True;
* `booking_recovery.recover_acknowledgement`, which is operator-invoked and has no route.

The Data Exchange cutover onto the booking authority is convergence Phase 3.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, time
from types import SimpleNamespace
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: SB2A-R3 — the owner-approved successful-Flow acknowledgement (owner decision, final). It
#: supersedes the earlier "Recibimos tu solicitud…" receipt on Track B's Flow confirmation
#: path. `build_booking_receipt_message` below is left untouched because the Conversation
#: Engine (Track A) still imports it; aligning CE copy is a Track A item.
BOOKING_FLOW_RECEIPT_TEXT = (
    "Gracias por completarlo. Un representante de RideCheck se estará comunicando con vos."
)

#: SB2B (B-6): what CE-W1 tells a customer whose Flow submission was NOT booked — a refused,
#: retryable or failed booking. It never claims a booking: it is the Data Exchange path's own
#: refusal copy for the outcomes a person must resolve (`_FLOW_REFUSAL_MESSAGES` below).
BOOKING_HANDOFF_TEXT = "No pudimos confirmar el turno. Un asesor se contacta a la brevedad."


@dataclass(frozen=True)
class ReceiptOutcome:
    """Result of one receipt attempt. Truthy only when Meta accepted the message AND returned
    a usable message id. `ambiguous` means the request may have reached Meta but the outcome
    is unknown (timeout after dispatch, 2xx without id, malformed success) — recorded as
    duplicate risk, never as delivered or as not delivered."""
    sent: bool
    wamid: Optional[str]
    error: Optional[str]
    ambiguous: bool = False

    def __bool__(self) -> bool:
        return self.sent


def _send_ledger_acknowledgement(
    db: Session, ctx, *, name: str, selected_date: date, selected_time: time,
    correlation_id: Optional[str] = None,
    causal_inbound_wa_message_id: Optional[str] = None,
) -> "ReceiptOutcome":
    """Send the single canonical booking acknowledgement.

    Exactly-once comes from the booking ledger, not from a flag: a Meta retry or a
    duplicate confirm_booking replays the committed result (it no longer reaches
    resolve_context and its BookingTokenError), and the receipt is sent only by the caller
    that atomically claims the ledger's acknowledgement (PENDING/FAILED → SENDING). The
    outbound gate's dedup window is a second line of defence, not the mechanism.

    Returns a ReceiptOutcome (SB2A-R3 classification):
      * definitive pre-send refusal (gate) or failure before dispatch → not sent
      * definitive Meta rejection (4xx)                              → not sent
      * success with a non-empty WAMID                               → sent
      * ambiguous: network/timeout after dispatch, 2xx without a usable id, malformed
        success, Meta 5xx                                            → AMBIGUOUS
    Exactly-once external delivery is never claimed.

    Routed through OutboundSafetyGate on the registered BOOKING_FLOW path — the same
    path that dispatched the Flow — so the acknowledgement is attributed to the booking
    that caused it and can never appear as MANUAL_CRM or unattributed.

    SB2B: CE-W1 sends this same acknowledgement through `send_booking_acknowledgement`,
    passing its turn's `correlation_id` and the causal inbound WAMID so the gate's causal
    dedup and the turn's trace see the send. The Data Exchange path passes neither, so
    its gate call is exactly what it was.
    """
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo

    from ..ui.whatsapp_ui import MetaSendError, _send_whatsapp_cloud_text
    from .outbound_guard import OutboundBlockedError
    from .outbound_path_registry import OutboundPathId, get_deployment_id
    from .outbound_safety_gate import GateOutcome, OutboundSafetyGate

    text = BOOKING_FLOW_RECEIPT_TEXT          # owner-approved copy (SB2A-R3)
    # SB2A-R4 (R3A-05): failures KNOWN to precede any transport attempt are definitive —
    # no gate record, no network call, no duplicate risk, retryable once fixed.
    config_error = _receipt_pre_dispatch_error(ctx)
    if config_error is not None:
        logger.error("BOOKING_RECEIPT not attempted thread_id=%s: %s", ctx.thread.id, config_error)
        return ReceiptOutcome(False, None, config_error)
    try:
        gate = OutboundSafetyGate(db)
        result = gate.attempt(
            wa_id=ctx.contact.wa_id, thread_id=ctx.thread.id, text=text,
            now=_dt.now(ZoneInfo("America/Argentina/Buenos_Aires")),
            path_id=OutboundPathId.BOOKING_FLOW.value,
            deployment_id=get_deployment_id(),
            **{k: v for k, v in (
                ("correlation_id", correlation_id),
                ("causal_inbound_wa_message_id", causal_inbound_wa_message_id))
               if v is not None},
        )
        if result.outcome != GateOutcome.ALLOWED:
            logger.warning(
                "BOOKING_RECEIPT not sent thread_id=%s outcome=%s — booking stands",
                ctx.thread.id, result.outcome.value)
            return ReceiptOutcome(False, None, f"GATE_{result.outcome.value}"[:64])
        try:
            wa_message_id, _ = _send_whatsapp_cloud_text(
                to_wa_id=ctx.contact.wa_id, text=text)
        except OutboundBlockedError:
            # raised by enforce_outbound_enabled, the transport's FIRST statement: nothing
            # left the process
            gate.mark_failed(result.message_id)
            return ReceiptOutcome(False, None, "PRE_DISPATCH_OUTBOUND_BLOCKED")
        except MetaSendError as exc:
            status = exc.http_status
            gate.mark_failed(result.message_id, meta_http_status=status,
                             meta_error_payload=exc.to_payload())
            logger.error("BOOKING_RECEIPT Meta send failed thread_id=%s status=%s",
                         ctx.thread.id, status)
            if status is not None and 400 <= status < 500:
                return ReceiptOutcome(False, None, f"META_REJECTED_{status}")
            if status is None:
                return ReceiptOutcome(False, None, "AMBIGUOUS_NETWORK_OR_TIMEOUT",
                                      ambiguous=True)
            if 200 <= status < 300:
                return ReceiptOutcome(False, None, "AMBIGUOUS_2XX_WITHOUT_MESSAGE_ID",
                                      ambiguous=True)
            return ReceiptOutcome(False, None, f"AMBIGUOUS_META_{status}", ambiguous=True)
        except Exception as exc:
            gate.mark_failed(result.message_id)
            logger.error("BOOKING_RECEIPT transport outcome unknown thread_id=%s: %s",
                         ctx.thread.id, type(exc).__name__)
            return ReceiptOutcome(False, None, "AMBIGUOUS_TRANSPORT_EXCEPTION",
                                  ambiguous=True)
        wamid = (wa_message_id or "").strip()
        if not wamid:
            return ReceiptOutcome(False, None, "AMBIGUOUS_SUCCESS_WITHOUT_WAMID",
                                  ambiguous=True)
        try:
            gate.mark_sent(result.message_id, wamid)
        except Exception:                    # bookkeeping failure ≠ delivery failure
            logger.exception("BOOKING_RECEIPT sent but gate bookkeeping failed")
        logger.info("BOOKING_RECEIPT sent thread_id=%s wamid=%s", ctx.thread.id, wamid)
        return ReceiptOutcome(True, wamid, None)
    except Exception as exc:
        # The booking is already committed; nothing here may undo it.
        logger.error("BOOKING_RECEIPT failed before dispatch thread_id=%s: %s",
                     ctx.thread.id, exc)
    return ReceiptOutcome(False, None, "PRE_SEND_FAILURE")


def send_booking_acknowledgement(
    db: Session, *, wa_id, thread_id: int, correlation_id: Optional[str] = None,
    causal_inbound_wa_message_id: Optional[str] = None,
) -> "ReceiptOutcome":
    """SB2B (D1 B-5): the booking acknowledgement for callers that hold no BookingContext —
    CE-W1 and the operator's ledger-driven recovery. It IS the Data Exchange path's
    `_send_booking_receipt` (owner text, BOOKING_FLOW path, gate, outcome classification),
    never a second copy of it. The caller must already hold the ledger's acknowledgement
    claim: this function sends, it does not decide whether to."""
    from .booking_flow_service import BookingContext
    ctx = BookingContext(thread=SimpleNamespace(id=thread_id),
                         contact=SimpleNamespace(wa_id=wa_id), lead=None, state=None,
                         candidate=None, vehicle_summary="", location_summary="",
                         zone_group=None, zone_detail=None, booking_token="")
    return _send_ledger_acknowledgement(
        db, ctx, name="", selected_date=None, selected_time=None, correlation_id=correlation_id,
        causal_inbound_wa_message_id=causal_inbound_wa_message_id)


def _receipt_pre_dispatch_error(ctx) -> Optional[str]:
    """Local transport configuration that would make the send fail BEFORE the network
    boundary (mirrors `_send_whatsapp_cloud_text`'s own pre-network checks). None if a send
    could be attempted."""
    from ..settings import get_settings
    wa_id = ctx.contact.wa_id if ctx is not None and ctx.contact is not None else None
    if type(wa_id) is not str or not wa_id.strip():
        return "PRE_DISPATCH_CONFIG_DESTINATION_MISSING"
    settings = get_settings()
    token = (settings.whatsapp_token or "").strip()
    phone_number_id = (settings.whatsapp_phone_number_id or "").strip()
    if token and token.lower() != "dummy" and not phone_number_id:
        return "PRE_DISPATCH_CONFIG_PHONE_NUMBER_ID_MISSING"
    return None
