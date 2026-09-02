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
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.booking_flow_service import (
    FLOW_VERSION,
    BookingFlowService,
    BookingSlotConflictError,
    BookingTokenError,
    decrypt_flow_request,
    encrypt_flow_response,
    health_response,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["flows"])

_TEXT_PLAIN = "text/plain"


def _encrypt_and_return(response_dict: dict, aes_key: bytes, iv: bytes) -> Response:
    """Encrypt response_dict, base64-encode, and return as text/plain."""
    encrypted = encrypt_flow_response(response_dict, aes_key, iv)
    b64_body = base64.b64encode(encrypted).decode("ascii")
    return Response(content=b64_body, media_type=_TEXT_PLAIN)


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

    # ── Booking token ─────────────────────────────────────────────────────────
    # The Flow sends booking_token in data (or falls back to flow_token).
    booking_token = str(data.get("booking_token") or flow_token or "").strip()

    # ── Route by action ───────────────────────────────────────────────────────
    try:
        if action == "INIT":
            result = svc.handle_init(booking_token)

        elif action == "data_exchange":
            trigger = str(data.get("trigger", "")).strip()

            if trigger == "date_selected":
                selected_date = str(data.get("date", "")).strip()
                result = svc.handle_date_selected(booking_token, selected_date)

            elif trigger == "prepare_summary":
                result = svc.handle_prepare_summary(booking_token, data)

            elif trigger == "confirm_booking":
                result = svc.handle_confirm_booking(booking_token, data)

            else:
                logger.warning("BOOKING_FLOW unknown trigger=%r", trigger)
                raise HTTPException(status_code=422, detail=f"Unknown trigger: {trigger!r}")

        else:
            logger.warning("BOOKING_FLOW unknown action=%r", action)
            raise HTTPException(status_code=422, detail=f"Unknown action: {action!r}")

    except BookingSlotConflictError as exc:
        # Return the conflict screen encrypted (not an HTTP error — Flow expects a valid encrypted response)
        return _encrypt_and_return(exc.refreshed_data, aes_key, iv)

    except BookingTokenError as exc:
        logger.warning("BOOKING_FLOW_TOKEN_ERROR: %s", exc)
        # Flow session expired or tampered — return an error screen
        error_response = {
            "version": FLOW_VERSION,
            "screen": "APPOINTMENT",
            "data": {
                "error_message": "Tu sesión de reserva expiró. Por favor, volvé a iniciar el proceso.",
                "booking_token": "",
                "date": [],
                "is_date_enabled": False,
                "time": [],
                "is_time_enabled": False,
                "vehicle_summary": "",
                "location_summary": "",
            },
        }
        return _encrypt_and_return(error_response, aes_key, iv)

    except ValueError as exc:
        logger.warning("BOOKING_FLOW_VALIDATION_ERROR: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))

    except Exception as exc:
        logger.error("BOOKING_FLOW_INTERNAL_ERROR: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

    return _encrypt_and_return(result, aes_key, iv)
