"""M21.3-C-D — Meta Flow Data Exchange endpoint.

Route: POST /integrations/whatsapp/flows/booking/data-exchange

Implements the Meta Flows Data API 3.0 request/response protocol:
  - RSA-OAEP (SHA-256) decryption of AES key
  - AES-128-GCM decryption of flow payload
  - AES-128-GCM encryption of response with IV flipped (XOR 0xFF)

Security perimeter:
  - This endpoint receives encrypted data only (no plaintext from Meta).
  - Private key lives on server (FLOW_BOOKING_PRIVATE_KEY_PATH).
  - OUTBOUND is NOT enabled here; booking creation writes DB state only.
  - Flow is NOT published; endpoint is not connected to live Meta infra.
"""
from __future__ import annotations

import base64
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.booking_attempt_events import dx_attempt_ref
from ..services.booking_flow_service import (
    FLOW_VERSION,
    BookingFlowService,
    BookingAlreadyCompletedError,
    BookingSlotConflictError,
    BookingTokenError,
    decrypt_flow_request,
    encrypt_flow_response,
    health_response,
)

logger = logging.getLogger(__name__)

from ..services.flow_contract import (  # WILD1-F3, WILD1-R3
    RECOVERY_SLOT_CONFLICT,
    RECOVERY_TOKEN_INVALID,
    appointment_payload,
    validate_response_screen,
)
from ..services.flow_contract import FORWARD_ROUTES as _FORWARD_ROUTES
from ..services.flow_contract import assert_forward_only as _assert_forward_only

router = APIRouter(tags=["flows"])

_TEXT_PLAIN = "text/plain"


#: The routing model as a plain dict, for the forward-route check. `FORWARD_ROUTES` is the
#: published graph, and it is the published graph that the check must be made against —
#: not a copy that could drift from the artifact.
_ROUTING_MODEL = {screen: list(targets) for screen, targets in _FORWARD_ROUTES.items()}

# WILD1-R3-C1 (review finding R-F05): the forward-only rules are now enforced BY THE RUNNING
# PROCESS, not only by the test suite. Until this line, `assert_forward_only` was defined and
# called from nowhere outside tests, so an edit reintroducing a backward edge into
# `FORWARD_ROUTES` would have been caught by CI and by nothing in the deployed container.
# Import fails loudly rather than serving a graph Meta would reject.
_assert_forward_only(_ROUTING_MODEL)


def _encrypt_and_return(response_dict: dict, aes_key: bytes, iv: bytes) -> Response:
    """Encrypt response_dict, base64-encode, and return as text/plain."""
    encrypted = encrypt_flow_response(response_dict, aes_key, iv)
    b64_body = base64.b64encode(encrypted).decode("ascii")
    return Response(content=b64_body, media_type=_TEXT_PLAIN)


def _guarded_return(response_dict: dict, trigger: str, aes_key: bytes, iv: bytes,
                    *, outcome: str | None = None) -> Response:
    """WILD1-R3 — the ONE place a Data API response screen is authorised.

    Every response that names a screen passes through here, and the screen is authorised
    one of exactly three ways:

      * an ordinary forward route in Meta's published `routing_model` (or a self-refresh,
        or the Flow opening);
      * the Data API terminate instruction, carrying a real extension payload;
      * a recognised typed refusal whose (origin, trigger, outcome) triple is on the
        narrow recovery allowlist in `flow_contract`.

    `outcome` is supplied by the backend's own `except` clause from the typed exception it
    caught. It is never read from request data, and neither is the screen: the response
    dicts are built by `booking_flow_service`, and nothing in this module copies a screen
    name out of `data`. A client that sends `{"screen": "APPOINTMENT"}` or
    `{"target": "APPOINTMENT"}` changes nothing — those keys are not read anywhere.

    A response this guard refuses is a server defect, so it is logged and never emitted.
    Emitting it is the failure mode this guard exists to prevent: the 2026-10-29 Wild sent
    ten responses naming a screen the Flow could not accept, and the customer saw Meta's
    generic error ten times.
    """
    screen = str(response_dict.get("screen", ""))
    try:
        validate_response_screen(
            trigger, screen, _ROUTING_MODEL, outcome=outcome,
            terminal_payload=response_dict.get("data"))
    except ValueError as exc:
        logger.error("BOOKING_FLOW_RESPONSE_REFUSED trigger=%r screen=%r outcome=%r: %s",
                     trigger, screen, outcome, exc)
        raise HTTPException(status_code=500,
                            detail="Response screen not authorised") from exc
    return _encrypt_and_return(response_dict, aes_key, iv)


@router.post(
    "/integrations/whatsapp/flows/booking/data-exchange",
    include_in_schema=False,
)
async def booking_flow_data_exchange(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    """Handle encrypted Meta Flow Data Exchange requests for the booking flow.

    Protocol (Data API 3.0):
      Request body: JSON with encrypted_aes_key, encrypted_flow_data, initial_vector (all base64)
      Response body: AES-128-GCM encrypted JSON (application/octet-stream)

    Actions handled:
      ping   → health check
      INIT   → APPOINTMENT screen with available dates
      data_exchange (date_selected)     → time slots for selected date
      data_exchange (prepare_summary)   → SUMMARY screen data
      data_exchange (confirm_booking)   → atomic booking + SUCCESS
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # ── Decrypt ───────────────────────────────────────────────────────────────
    try:
        payload, aes_key, iv = decrypt_flow_request(body)
    except ValueError as exc:
        logger.warning("BOOKING_FLOW_DECRYPT_FAIL: %s", exc)
        raise HTTPException(status_code=421, detail=str(exc))

    action: str = payload.get("action", "")
    flow_token: str = payload.get("flow_token", "")
    data: dict[str, Any] = payload.get("data", {})

    logger.info("BOOKING_FLOW_REQUEST action=%s flow_token_prefix=%.12s", action, flow_token)

    # ── Health check ─────────────────────────────────────────────────────────
    if action == "ping":
        return _encrypt_and_return(health_response(), aes_key, iv)

    svc = BookingFlowService(db)

    # The trigger, derived once from the envelope, so the refusal handlers below can name
    # the same one the dispatch used. `INIT` is its own trigger; anything unrecognised
    # stays as it arrived and is refused by the dispatch and by the guard alike.
    trigger: str = ("INIT" if action == "INIT"
                    else str(data.get("trigger", "")).strip() if action == "data_exchange"
                    else "")

    # ── Booking token ─────────────────────────────────────────────────────────
    # The Flow sends booking_token in data (or falls back to flow_token).
    booking_token = str(data.get("booking_token") or flow_token or "").strip()

    # ── Route by action ───────────────────────────────────────────────────────
    # PRELAUNCH-MINIMUM-LAUNCH-R1 (review F-02 / C-2): the dispatch below is synchronous —
    # database reads and the booking authority — and used to run directly on the single
    # uvicorn event loop, so one slow request stalled every webhook and CRM page. It is
    # unchanged, but it now runs on the threadpool (`run_in_threadpool`) and the event loop
    # only awaits it; every exception it raises reaches the SAME handlers below. No provider
    # call happens on this path any more: a slot conflict only ADMITS a recovery job, which
    # the background worker executes.
    def _dispatch() -> dict:
        result: dict
        if action == "INIT":
            result = svc.handle_init(booking_token)

        elif action == "data_exchange":
            if trigger == "date_selected":
                selected_date = str(data.get("date", "")).strip()
                result = svc.handle_date_selected(booking_token, selected_date)

            elif trigger == "prepare_summary":
                result = svc.handle_prepare_summary(booking_token, data)

            elif trigger == "confirm_booking":
                # Phase 4-R1 (P4RV-02): the delivery's replay identity, from the envelope as
                # received — a digest only; the IV and ciphertext themselves go nowhere.
                result = svc.handle_confirm_booking(
                    booking_token, data,
                    attempt_ref=dx_attempt_ref(
                        body.get("initial_vector") if isinstance(body, dict) else None,
                        body.get("encrypted_flow_data") if isinstance(body, dict) else None))

            else:
                # WILD1-F4: an empty trigger is what the real handset sent four times.
                # Logged as a validation refusal, never as an internal error, and the
                # payload itself is never logged.
                logger.warning("BOOKING_FLOW_VALIDATION_REFUSED kind=unknown_trigger "
                               "trigger=%r token_present=%s", trigger, bool(booking_token))
                raise HTTPException(status_code=422, detail=f"Unknown trigger: {trigger!r}")

        else:
            logger.warning("BOOKING_FLOW_VALIDATION_REFUSED kind=unknown_action "
                           "action=%r token_present=%s", action, bool(booking_token))
            raise HTTPException(status_code=422, detail=f"Unknown action: {action!r}")
        return result

    try:
        result = await run_in_threadpool(_dispatch)

    except BookingAlreadyCompletedError as exc:
        # ── G3-7R/S6: a REPLAY, caught BEFORE BookingTokenError on purpose ───────
        # The architecture audit measured this case being reported as an expired session:
        # a committed booking clears the transport mirror, `resolve_context` read the
        # cleared mirror as "token invalid or already consumed", and the customer was told
        # *"Tu sesión de reserva expiró"* about the booking that had just succeeded.
        #
        # The authoritative booking exists, so the Flow is shown its completion. Nothing is
        # created, no refusal counter moves, and no second acknowledgement is sent — the
        # transport that created the booking already sent one.
        logger.info("BOOKING_FLOW_REPLAY offer_id=%s thread_rev=%s — returning completion",
                    exc.offer_id, exc.thread_revision_id)
        return _guarded_return({
            "version": FLOW_VERSION,
            "screen": "SUCCESS",
            "data": {
                "extension_message_response": {
                    "params": {
                        "flow_token": booking_token,
                    }
                }
            },
        }, trigger, aes_key, iv)

    except BookingSlotConflictError as exc:
        # Return the conflict screen encrypted (not an HTTP error — Flow expects a valid
        # encrypted response). WILD1-R3: this is the one RECOVERABLE refusal. The context
        # is valid, the screen comes back populated with what is free now, and picking
        # again is a real path — which is why the bounded handset Wild must prove recovery
        # with a real slot conflict and never with a deliberately expired token.
        return _guarded_return(exc.refreshed_data, trigger, aes_key, iv,
                               outcome=RECOVERY_SLOT_CONFLICT)

    except BookingTokenError as exc:
        logger.warning("BOOKING_FLOW_TOKEN_ERROR: %s", exc)
        # Flow session expired or tampered — return an error screen
        # WILD1-F3: built by the one contract builder, which supplies every declared key
        # and refuses an undeclared one. `has_error` is what makes the message visible on
        # the target Flow; the hand-rolled dict that stood here carried `error_message`
        # with no flag at all.
        #
        # WILD1-F3-R2: the builder's defaults supply the five prefill keys as empty
        # strings, which is the same thing the hand-rolled dict did deliberately and for
        # the same reason — an expired or tampered session HAS no authoritative identity to
        # assert. The token is what names the contact, and the token is what just failed, so
        # there is nothing to read `ctx.contact.wa_id` from. Empty is the honest value and a
        # declared-but-empty key is renderable where a missing one is not; the screen's own
        # message tells the customer to start again rather than continue.
        error_response = {
            "version": FLOW_VERSION,
            "screen": "APPOINTMENT",
            "data": appointment_payload(
                error_message="Tu sesión de reserva expiró. Por favor, volvé a iniciar el proceso.",
                has_error=True,
            ),
        }
        # WILD1-R3: FAIL-CLOSED, and classified separately from the slot conflict above.
        # The token is what names the contact and the token is what just failed, so the
        # screen carries no identity and no availability. Continuing from it is not a
        # recovery path; the copy tells the customer to start again, and it must never
        # claim retry is supported here.
        return _guarded_return(error_response, trigger, aes_key, iv,
                               outcome=RECOVERY_TOKEN_INVALID)

    except HTTPException:
        # ── WILD1-F4 ────────────────────────────────────────────────────────────────
        # `HTTPException` subclasses `Exception`, so the generic handler below used to
        # catch the 422s raised INSIDE this try (unknown trigger, unknown action) and
        # re-raise them as 500. On the real handset that turned four empty-trigger
        # requests into "Internal server error", which Meta shows as its own generic
        # failure — and it logged a client fault as a server fault, corrupting the one
        # signal alerting would key on. An intended status is a decision already made:
        # it passes through untouched, and is logged as the validation event it is.
        raise

    except ValueError as exc:
        logger.warning("BOOKING_FLOW_VALIDATION_ERROR: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))

    except Exception as exc:
        logger.error("BOOKING_FLOW_INTERNAL_ERROR: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

    return _guarded_return(result, trigger, aes_key, iv)
