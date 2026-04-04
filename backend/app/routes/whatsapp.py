from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from urllib import request as urllib_request

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AiEvent, WhatsAppContact, WhatsAppMessage, WhatsAppThread
from ..settings import get_settings

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

router = APIRouter(prefix="/integrations/whatsapp", tags=["whatsapp"])

STATUS_PRECEDENCE: dict[str, int] = {
    "pending": 0,
    "sent": 1,
    "delivered": 2,
    "read": 3,
    "failed": 4,
}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _parse_wa_timestamp(value: object) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(str(value)), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _verify_signature(raw_body: bytes, signature_header: str | None, app_secret: str) -> bool:
    secret = (app_secret or "").strip()
    if not secret:
        logger.info("Webhook signature skipped (dev mode)")
        return True

    header_value = str(signature_header or "").strip()
    if not header_value:
        logger.warning("Webhook signature invalid")
        return False

    algo, sep, provided_sig = header_value.partition("=")
    if sep != "=" or algo.lower() != "sha256" or not provided_sig:
        logger.warning("Webhook signature invalid")
        return False

    expected_sig = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    valid = hmac.compare_digest(expected_sig, provided_sig.strip().lower())
    if valid:
        logger.info("Webhook signature valid")
    else:
        logger.warning("Webhook signature invalid")
    return valid


def _post_n8n_event(webhook_url: str, payload: dict[str, object]) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib_request.urlopen(req, timeout=3) as response:
        status_code = getattr(response, "status", response.getcode())
        if not 200 <= status_code < 300:
            raise RuntimeError(f"unexpected_status:{status_code}")


@router.get("/webhook")
def verify_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
):
    settings = get_settings()
    if not settings.whatsapp_enabled:
        return Response(content="forbidden", media_type="text/plain", status_code=403)

    if (
        hub_mode == "subscribe"
        and hub_challenge is not None
        and hub_verify_token == settings.whatsapp_verify_token
    ):
        return Response(content=hub_challenge, media_type="text/plain", status_code=200)

    return Response(content="forbidden", media_type="text/plain", status_code=403)


@router.post("/webhook")
async def inbound_webhook(request: Request, db: Session = Depends(get_db)):
    logger.info(
        "WHATSAPP_WEBHOOK_HIT content_type=%s content_length=%s",
        request.headers.get("content-type"),
        request.headers.get("content-length"),
    )
    settings = get_settings()
    if not settings.whatsapp_enabled:
        return Response(content="forbidden", media_type="text/plain", status_code=403)

    try:
        raw_body = await request.body()
    except Exception:
        logger.warning("WHATSAPP_WEBHOOK_BAD_BODY")
        return Response(content="forbidden", media_type="text/plain", status_code=403)

    if not _verify_signature(
        raw_body=raw_body,
        signature_header=request.headers.get("X-Hub-Signature-256"),
        app_secret=settings.whatsapp_app_secret,
    ):
        return Response(content="forbidden", media_type="text/plain", status_code=403)

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        logger.warning("WHATSAPP_WEBHOOK_BAD_JSON")
        return Response(content="ok", media_type="text/plain", status_code=200)

    if not isinstance(payload, dict):
        logger.warning("WHATSAPP_WEBHOOK_NOT_OBJECT")
        return Response(content="ok", media_type="text/plain", status_code=200)
    logger.info("WHATSAPP_WEBHOOK_KEYS keys=%s", sorted(payload.keys()))

    try:
        for entry in _as_list(payload.get("entry")):
            if not isinstance(entry, dict):
                continue

            for change in _as_list(entry.get("changes")):
                if not isinstance(change, dict):
                    continue

                value = change.get("value")
                if not isinstance(value, dict):
                    continue

                contacts = _as_list(value.get("contacts"))
                first_contact = contacts[0] if contacts and isinstance(contacts[0], dict) else {}
                wa_id_from_contact = str(first_contact.get("wa_id") or "").strip()

                profile = first_contact.get("profile") if isinstance(first_contact.get("profile"), dict) else {}
                display_name_raw = profile.get("name") if isinstance(profile, dict) else None
                display_name = str(display_name_raw).strip() if display_name_raw is not None else None
                if display_name == "":
                    display_name = None

                for message in _as_list(value.get("messages")):
                    if not isinstance(message, dict) or message.get("type") != "text":
                        continue

                    wa_message_id = str(message.get("id") or "").strip()
                    if wa_message_id == "":
                        logger.warning("whatsapp inbound text message missing id")
                        continue

                    existing_message_id = db.execute(
                        select(WhatsAppMessage.id).where(WhatsAppMessage.wa_message_id == wa_message_id)
                    ).scalar_one_or_none()
                    if existing_message_id is not None:
                        logger.info("WHATSAPP_WEBHOOK_DEDUPED wa_message_id=%s", wa_message_id)
                        continue

                    wa_id = wa_id_from_contact or str(message.get("from") or "").strip()
                    if wa_id == "":
                        logger.warning("whatsapp inbound text message missing wa_id wa_message_id=%s", wa_message_id)
                        continue

                    message_ts = _parse_wa_timestamp(message.get("timestamp"))
                    if message_ts is None:
                        logger.warning(
                            "whatsapp inbound text message invalid timestamp wa_message_id=%s", wa_message_id
                        )
                        continue

                    text_block = message.get("text")
                    text_body = text_block.get("body") if isinstance(text_block, dict) else None
                    text = str(text_body) if text_body is not None else None

                    try:
                        contact = db.execute(
                            select(WhatsAppContact).where(WhatsAppContact.wa_id == wa_id)
                        ).scalar_one_or_none()
                        if contact is None:
                            contact = WhatsAppContact(wa_id=wa_id, display_name=display_name, phone=None)
                            db.add(contact)
                            db.flush()
                        elif display_name and contact.display_name != display_name:
                            contact.display_name = display_name

                        thread = db.execute(
                            select(WhatsAppThread)
                            .where(WhatsAppThread.contact_id == contact.id)
                            .order_by(WhatsAppThread.id.asc())
                        ).scalars().first()
                        if thread is None:
                            thread = WhatsAppThread(contact_id=contact.id, lead_id=None, unread_count=0)
                            db.add(thread)
                            db.flush()

                        db.add(
                            WhatsAppMessage(
                                thread_id=thread.id,
                                wa_message_id=wa_message_id,
                                direction="in",
                                status="received",
                                timestamp=message_ts,
                                text=text,
                                raw_payload=payload,
                            )
                        )
                        thread.last_message_at = message_ts
                        thread.unread_count = (thread.unread_count or 0) + 1
                        db.commit()
                        text_preview = ((text or "").replace("\r", " ").replace("\n", " "))[:80]
                        logger.info(
                            "WHATSAPP_WEBHOOK_STORED wa_id=%s wa_message_id=%s thread_id=%s timestamp=%s preview=%s",
                            wa_id,
                            wa_message_id,
                            thread.id,
                            message_ts.isoformat(),
                            text_preview,
                        )

                        ai_event: AiEvent | None = None
                        ai_event_created = False
                        try:
                            ai_event = AiEvent(
                                event_type="inbound_message",
                                thread_id=thread.id,
                                wa_message_id=wa_message_id,
                                wa_id=wa_id,
                                text=text,
                                status="pending",
                            )
                            db.add(ai_event)
                            db.commit()
                            ai_event_created = True
                        except IntegrityError:
                            db.rollback()
                            ai_event = db.execute(
                                select(AiEvent).where(AiEvent.wa_message_id == wa_message_id)
                            ).scalar_one_or_none()
                        except Exception:
                            db.rollback()
                            logger.warning(
                                "whatsapp ai_event insert failed wa_message_id=%s",
                                wa_message_id,
                                exc_info=True,
                            )
                            ai_event = None

                        if ai_event_created and ai_event is not None and settings.n8n_webhook_url:
                            try:
                                _post_n8n_event(
                                    settings.n8n_webhook_url,
                                    {
                                        "event": "inbound_message",
                                        "thread_id": thread.id,
                                        "wa_message_id": wa_message_id,
                                        "wa_id": wa_id,
                                        "text": text,
                                    },
                                )
                                ai_event.status = "triggered"
                                ai_event.last_error = None
                                db.commit()
                            except Exception as exc:
                                db.rollback()
                                try:
                                    ai_event = db.execute(
                                        select(AiEvent).where(AiEvent.wa_message_id == wa_message_id)
                                    ).scalar_one_or_none()
                                    if ai_event is not None:
                                        ai_event.status = "failed"
                                        ai_event.last_error = str(exc)
                                        db.commit()
                                except Exception:
                                    db.rollback()
                                logger.warning(
                                    "whatsapp ai_event n8n trigger failed wa_message_id=%s",
                                    wa_message_id,
                                    exc_info=True,
                                )
                    except IntegrityError:
                        db.rollback()
                        dedup_after_race = db.execute(
                            select(WhatsAppMessage.id).where(WhatsAppMessage.wa_message_id == wa_message_id)
                        ).scalar_one_or_none()
                        if dedup_after_race is not None:
                            logger.info("WHATSAPP_WEBHOOK_DEDUPED wa_message_id=%s", wa_message_id)
                        else:
                            logger.warning(
                                "whatsapp inbound integrity error wa_message_id=%s",
                                wa_message_id,
                                exc_info=True,
                            )
                    except Exception:
                        db.rollback()
                        logger.warning(
                            "whatsapp inbound failed processing wa_message_id=%s",
                            wa_message_id,
                            exc_info=True,
                        )

                for status_item in _as_list(value.get("statuses")):
                    if not isinstance(status_item, dict):
                        continue

                    wa_message_id = str(status_item.get("id") or "").strip()
                    if wa_message_id == "":
                        logger.warning("WHATSAPP_STATUS_MISSING_ID")
                        continue

                    incoming_status = str(status_item.get("status") or "").strip().lower()
                    if incoming_status not in STATUS_PRECEDENCE:
                        logger.info(
                            "WHATSAPP_STATUS_PROCESSED status=%s wa_message_id=%s result=ignored_unknown_status",
                            incoming_status or "-",
                            wa_message_id,
                        )
                        continue

                    try:
                        existing_msg = db.execute(
                            select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == wa_message_id)
                        ).scalar_one_or_none()
                        if existing_msg is None:
                            logger.info(
                                "WHATSAPP_STATUS_PROCESSED status=%s wa_message_id=%s result=not_found",
                                incoming_status,
                                wa_message_id,
                            )
                            continue

                        current_status = str(existing_msg.status or "").strip().lower()
                        current_rank = STATUS_PRECEDENCE.get(current_status, -1)
                        incoming_rank = STATUS_PRECEDENCE[incoming_status]
                        if incoming_rank < current_rank:
                            logger.info(
                                "WHATSAPP_STATUS_PROCESSED status=%s wa_message_id=%s result=ignored_downgrade current=%s",
                                incoming_status,
                                wa_message_id,
                                current_status or "-",
                            )
                            continue

                        if incoming_rank == current_rank:
                            logger.info(
                                "WHATSAPP_STATUS_PROCESSED status=%s wa_message_id=%s result=no_change",
                                incoming_status,
                                wa_message_id,
                            )
                            continue

                        existing_msg.status = incoming_status
                        db.commit()
                        logger.info(
                            "WHATSAPP_STATUS_PROCESSED status=%s wa_message_id=%s result=updated",
                            incoming_status,
                            wa_message_id,
                        )
                    except Exception:
                        db.rollback()
                        logger.warning(
                            "whatsapp status update failed wa_message_id=%s status=%s",
                            wa_message_id,
                            incoming_status,
                            exc_info=True,
                        )
    except Exception:
        db.rollback()
        logger.warning("whatsapp inbound webhook processing failed", exc_info=True)

    return Response(content="ok", media_type="text/plain", status_code=200)


# Manual test snippets:
# Correct token (expect 200 + challenge):
# curl -i "http://localhost:8000/integrations/whatsapp/webhook?hub.mode=subscribe&hub.challenge=12345&hub.verify_token=YOUR_VERIFY_TOKEN"
#
# Wrong token (expect 403):
# curl -i "http://localhost:8000/integrations/whatsapp/webhook?hub.mode=subscribe&hub.challenge=12345&hub.verify_token=WRONG"
#
# Inbound insert once (expect 1 message inserted):
# curl.exe -s -X POST "http://localhost:8000/integrations/whatsapp/webhook" ^
#   -H "Content-Type: application/json" ^
#   --data-binary "@tests/fixtures/whatsapp_inbound_text.json" -i
#
# Repeat same POST twice; count should remain 1 (dedup):
# docker compose exec postgres psql -U crm -d crm -c "select count(*) from whatsapp_messages;"
#
# unread_count should increment only once:
# docker compose exec postgres psql -U crm -d crm -c "select unread_count, last_message_at from whatsapp_threads;"
#
# Status update test:
# curl.exe -s -X POST "http://localhost:8000/integrations/whatsapp/webhook" ^
#   -H "Content-Type: application/json" ^
#   --data-binary "@tests/fixtures/whatsapp_status_delivered.json" -i
