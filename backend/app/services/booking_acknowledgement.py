"""The ledger-driven booking acknowledgement (SB2B; carried in by convergence Phase 1).

Track B's ledger-driven booking acknowledgement, verbatim from `870b4e3`
(`booking_flow_service.py`), where it was the Data Exchange path's receipt sender. Its callers:

* the Conversation Engine's Flow booking path and the Data Exchange confirmation, both
  through `acknowledge_committed_booking` (convergence Phase 3 — the only success copy);
* `booking_recovery.recover_acknowledgement`, which is operator-invoked and has no route.

Convergence Phase 3-R1 — what makes a booking command's acknowledgement at most once:

* its delivery identity is the COMMAND's (`booking_authority.acknowledgement_delivery_identity`
  of `booking_commands.idempotency_key`), one fingerprint per attempt, committed by the gate in
  its dedup row and its `pending` outbound row BEFORE the transport is entered — the gate
  never admits one attempt identity twice, at any age, whatever the inbound;
* the ledger issues at most two attempts, and the second only after a DEFINITE non-delivery;
  a sender that died after its claim is held as UNCERTAIN, never reclaimed (the lease is
  concurrency exclusion only);
* the irreversible boundary is the call into `_send_whatsapp_cloud_text`: before it, a failure
  is definite; after it, only a documented Meta 4xx rejection is — everything else
  (timeout, network, 5xx, 2xx without id, any other exception) is UNCERTAIN.
  Pre-Wild PW1-D (RV1-01): "documented" means PROVEN by the response, not inferred from the
  status range — `meta_rejection_is_definite`.
A rare acknowledgement may therefore be missing and held for an operator; one booking command
never produces two customer-visible acknowledgements through an automatic retry.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, time
from types import SimpleNamespace
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: SB2A-R3 — the owner-approved successful-Flow acknowledgement (owner decision, final), and
#: since convergence Phase 3 the ONLY success copy on either Flow booking transport. The
#: date/time receipt (`booking_flow_service.build_booking_receipt_message`) is no longer sent
#: by any booking path; `ConversationEngine._FLOW_COMPLETION_ACK` is this same object.
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

    @property
    def delivery(self):
        """Phase 3-R1 typed outcome of this attempt (`booking_authority.AckDelivery`)."""
        from .booking_authority import AckDelivery
        if self.sent:
            return AckDelivery.SENT_CONFIRMED
        return AckDelivery.UNCERTAIN if self.ambiguous else AckDelivery.DEFINITELY_NOT_SENT


def meta_rejection_is_definite(exc) -> bool:
    """Does this `MetaSendError` PROVE that Meta did not accept the message? Fail-closed.

    Pre-Wild PW1-D (RV1-01). The Phase 3-R1 review measured the old rule — any HTTP 4xx is a
    definite rejection — recording a bare `408` with an HTML body as `META_REJECTED_408` and
    admitting the retry. A status code alone cannot carry that proof: 408 says the exchange
    did not complete, and a 4xx without Graph's error body may not come from Graph at all.
    Definite therefore needs ALL of:

    * a 4xx status other than 408, which is never definite whatever its body says;
    * Graph's error envelope, parsed by `MetaSendError` from the body: a JSON object whose
      `error` is an object carrying an integer `code` and a non-empty string `message`.

    Everything else after the transport boundary stays UNCERTAIN, so it is held for an
    operator and never sent again automatically. This classifies the shape of the response;
    it does not interpret Meta's individual error codes.
    """
    status = getattr(exc, "http_status", None)
    if type(status) is not int or not 400 <= status < 500 or status == 408:
        return False
    try:
        body = json.loads(getattr(exc, "raw_body", "") or "")
    except (TypeError, ValueError):
        return False
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return False
    code, message = err.get("code"), err.get("message")
    return (type(code) is int and isinstance(message, str) and bool(message.strip()))


def _send_ledger_acknowledgement(
    db: Session, ctx, *, name: str, selected_date: date, selected_time: time,
    correlation_id: Optional[str] = None,
    causal_inbound_wa_message_id: Optional[str] = None,
    delivery_fingerprint: Optional[str] = None,
) -> "ReceiptOutcome":
    """Send the single canonical booking acknowledgement.

    Exactly-once comes from the booking ledger, not from a flag: a Meta retry or a
    duplicate confirm_booking replays the committed result (it no longer reaches
    resolve_context and its BookingTokenError), and the receipt is sent only by the caller
    that atomically claims the ledger's acknowledgement (PENDING/FAILED → SENDING). The
    outbound gate's dedup window is a second line of defence, not the mechanism.

    Returns a ReceiptOutcome (SB2A-R3 classification):
      * definitive pre-send refusal (gate) or failure before dispatch → not sent
      * definitive Meta rejection (a 4xx PROVEN by Graph's envelope) → not sent
        (PW1-D: 408, or a 4xx without the envelope, is AMBIGUOUS)
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
    # Phase 3-R1: where the attempt stands relative to the irreversible transport boundary.
    # "before" — nothing can have left; "entered" — the sender was called and has not proven a
    # rejection, so ANY failure from here on is UNCERTAIN; "settled" — classified below.
    boundary = "before"
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
                ("causal_inbound_wa_message_id", causal_inbound_wa_message_id),
                ("delivery_identity", delivery_fingerprint))
               if v is not None},
        )
        if result.outcome != GateOutcome.ALLOWED:
            logger.warning(
                "BOOKING_RECEIPT not sent thread_id=%s outcome=%s — booking stands",
                ctx.thread.id, result.outcome.value)
            if (delivery_fingerprint is not None
                    and result.outcome == GateOutcome.BLOCKED_DUPLICATE):
                # this attempt's identity crossed the gate before: the transport may have
                # accepted that earlier crossing — uncertain, never definite
                return ReceiptOutcome(False, None, "AMBIGUOUS_DELIVERY_IDENTITY_SEEN",
                                      ambiguous=True)
            return ReceiptOutcome(False, None, f"GATE_{result.outcome.value}"[:64])
        try:
            boundary = "entered"
            wa_message_id, _ = _send_whatsapp_cloud_text(
                to_wa_id=ctx.contact.wa_id, text=text)
        except OutboundBlockedError:
            # raised by enforce_outbound_enabled, the transport's FIRST statement: nothing
            # left the process
            boundary = "settled"
            gate.mark_failed(result.message_id)
            return ReceiptOutcome(False, None, "PRE_DISPATCH_OUTBOUND_BLOCKED")
        except MetaSendError as exc:
            status = exc.http_status
            definite = meta_rejection_is_definite(exc)          # PW1-D (RV1-01)
            if definite:
                boundary = "settled"           # Meta's documented rejection: not accepted
            gate.mark_failed(result.message_id, meta_http_status=status,
                             meta_error_payload=exc.to_payload())
            logger.error("BOOKING_RECEIPT Meta send failed thread_id=%s status=%s definite=%s",
                         ctx.thread.id, status, definite)
            if definite:
                return ReceiptOutcome(False, None, f"META_REJECTED_{status}")
            if status is not None and 400 <= status < 500:
                # a 4xx that does not prove non-acceptance (408, or no Graph envelope)
                return ReceiptOutcome(False, None, f"AMBIGUOUS_META_{status}_UNPROVEN",
                                      ambiguous=True)
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
        boundary = "settled"
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
        if boundary == "entered":
            # e.g. gate bookkeeping failed while classifying a transport failure: the sender
            # WAS called and no rejection was proven — never "definitely not sent"
            logger.error("BOOKING_RECEIPT failed after the transport boundary thread_id=%s: %s",
                         ctx.thread.id, type(exc).__name__)
            return ReceiptOutcome(False, None, "AMBIGUOUS_AFTER_TRANSPORT_BOUNDARY",
                                  ambiguous=True)
        logger.error("BOOKING_RECEIPT failed before dispatch thread_id=%s: %s",
                     ctx.thread.id, exc)
    return ReceiptOutcome(False, None, "PRE_SEND_FAILURE")


def send_booking_acknowledgement(
    db: Session, *, wa_id, thread_id: int, delivery_fingerprint: str,
    correlation_id: Optional[str] = None,
    causal_inbound_wa_message_id: Optional[str] = None,
) -> "ReceiptOutcome":
    """SB2B (D1 B-5): the booking acknowledgement for callers that hold no BookingContext —
    the shared `acknowledge_committed_booking` and the operator's ledger-driven recovery. The
    caller must already hold the ledger's acknowledgement claim: this function sends, it does
    not decide whether to. Phase 3-R1: `delivery_fingerprint` is REQUIRED — the claim's
    command-bound attempt identity (`AckClaim.delivery_fingerprint`); a booking acknowledgement
    is never deduplicated by its text."""
    if type(delivery_fingerprint) is not str or len(delivery_fingerprint) != 64:
        raise ValueError("a booking acknowledgement needs its claim's delivery fingerprint")
    from .booking_flow_service import BookingContext
    ctx = BookingContext(thread=SimpleNamespace(id=thread_id),
                         contact=SimpleNamespace(wa_id=wa_id), lead=None, state=None,
                         candidate=None, vehicle_summary="", location_summary="",
                         zone_group=None, zone_detail=None, booking_token="")
    return _send_ledger_acknowledgement(
        db, ctx, name="", selected_date=None, selected_time=None, correlation_id=correlation_id,
        causal_inbound_wa_message_id=causal_inbound_wa_message_id,
        delivery_fingerprint=delivery_fingerprint)


def acknowledge_committed_booking(
    db: Session, *, key: Optional[str], ack_status: Optional[str], wa_id, thread_id: int,
    correlation_id: Optional[str] = None,
    causal_inbound_wa_message_id: Optional[str] = None,
) -> "tuple[Optional[str], Optional[ReceiptOutcome]]":
    """Convergence Phase 3 (B-5): THE one acknowledgement sequence for a committed or replayed
    Flow booking, shared by the Conversation Engine and the Data Exchange endpoint.

    1. the ledger claim is taken in its own short transaction (isolated session) — SENT,
       NOT_APPLICABLE, an attempt in flight, an UNCERTAIN or exhausted row → nothing is sent
       (Phase 3-R1: at most two attempts per command, the second only after a definite
       non-delivery; an expired lease is a lost sender, held, never reclaimed);
    2. the owner-approved text (`BOOKING_FLOW_RECEIPT_TEXT`, the only success copy) goes
       through `OutboundSafetyGate` on the BOOKING_FLOW path under the claim's command-bound
       attempt identity, with the causal inbound where one exists (CE) recorded as evidence;
    3. SENT / FAILED / AMBIGUOUS is recorded in its own short transaction.

    It is called only after the booking transaction has committed, so no booking lock is held
    across the send. Returns (ack status after, send outcome or None when nothing was sent)."""
    from .booking_authority import BookingAuthority
    from .booking_ce_adapter import isolated_session, lookup_booking
    if type(key) is not str or not key or ack_status in (None, "SENT", "NOT_APPLICABLE"):
        return ack_status, None
    with isolated_session(db) as session:
        claim = BookingAuthority(session).claim_acknowledgement(key)
    if claim is None:                          # SENT meanwhile, a live lease, exhausted
        after = lookup_booking(db, key)
        return (after.ack_status if after else ack_status), None
    outcome = send_booking_acknowledgement(
        db, wa_id=wa_id, thread_id=thread_id, delivery_fingerprint=claim.delivery_fingerprint,
        correlation_id=correlation_id,
        causal_inbound_wa_message_id=causal_inbound_wa_message_id)
    with isolated_session(db) as session:
        recorded = BookingAuthority(session).record_acknowledgement(
            claim, sent=bool(outcome), transport_id=getattr(outcome, "wamid", None),
            error=getattr(outcome, "error", None) or (None if outcome else "NOT_SENT"),
            ambiguous=bool(getattr(outcome, "ambiguous", False)))
    if not recorded:
        logger.warning("BOOKING_ACK outcome not recorded: the claim no longer owns the row "
                       "attempt=%s", claim.attempt)
    logger.info("BOOKING_ACK attempt=%s delivery=%s", claim.attempt, outcome.delivery.value)
    after = lookup_booking(db, key)
    return (after.ack_status if after else None), outcome


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
