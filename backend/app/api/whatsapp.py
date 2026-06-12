from __future__ import annotations

import json
import mimetypes
from datetime import datetime, timezone
from urllib import error, request as urlrequest
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Lead,
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppThread,
    WhatsAppThreadCandidate,
)
from ..schemas.whatsapp_api import (
    InteractiveButton,
    LatestInboundMessageOut,
    LatestInboundOut,
    SetLatestInboundIn,
    WhatsAppThreadCandidateCreate,
    WhatsAppThreadCandidatePatch,
    WhatsAppThreadCandidateRead,
    WhatsAppThreadDisplayNamePatch,
    WhatsAppSendFlowIn,
    WhatsAppSendFlowOut,
    WhatsAppSendInteractiveIn,
    WhatsAppSendInteractiveOut,
    WhatsAppSendListIn,
    WhatsAppSendListOut,
    WhatsAppSendTextIn,
    WhatsAppSendTextOut,
    WhatsAppThreadStatePatch,
    WhatsAppThreadStateRead,
    WhatsAppThreadLinkIn,
    WhatsAppThreadMessagesOut,
    WhatsAppThreadOut,
)
from ..settings import get_settings
from ..services.db_errors import commit_or_400
from ..services.unanswered_alert import reset_unanswered_alert
from ..services.whatsapp_thread_state import build_thread_state_read, upsert_thread_state
from ..services.whatsapp_threads import load_latest_inbound_message, load_recent_thread_messages, load_thread_payload
from ..ui.whatsapp_ui import _send_whatsapp_cloud_flow, _send_whatsapp_cloud_interactive, _send_whatsapp_cloud_list, _send_whatsapp_cloud_text

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])
thread_router = APIRouter(tags=["whatsapp"])



class WhatsAppMediaInfoOut(BaseModel):
    media_id: str
    url: str
    mime_type: str | None = None
    file_size: int | None = None


class WhatsAppMediaTranscriptionOut(BaseModel):
    text: str


def _require_whatsapp_token() -> str:
    token = (get_settings().whatsapp_token or "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="WHATSAPP_TOKEN missing")
    return token


def _require_openai_api_key() -> str:
    token = (get_settings().openai_api_key or "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY missing")
    return token


def _load_whatsapp_media_info(media_id: str) -> WhatsAppMediaInfoOut:
    media_id = str(media_id or "").strip()
    if not media_id:
        raise HTTPException(status_code=400, detail="media_id is required")

    token = _require_whatsapp_token()
    req = urlrequest.Request(
        f"https://graph.facebook.com/v19.0/{media_id}",
        method="GET",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urlrequest.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"WhatsApp media lookup failed: HTTP {exc.code}: {err_body}") from exc
    except error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"WhatsApp media lookup failed: {exc.reason}") from exc

    try:
        payload = json.loads(body) if body.strip() else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="WhatsApp media lookup returned invalid JSON") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="WhatsApp media lookup returned an invalid payload")

    url = str(payload.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=502, detail="WhatsApp media lookup did not return a download URL")

    file_size_raw = payload.get("file_size")
    try:
        file_size = int(file_size_raw) if file_size_raw not in (None, "") else None
    except (TypeError, ValueError):
        file_size = None

    return WhatsAppMediaInfoOut(
        media_id=media_id,
        url=url,
        mime_type=str(payload.get("mime_type") or "").strip() or None,
        file_size=file_size,
    )


def _open_whatsapp_media_stream(info: WhatsAppMediaInfoOut):
    token = _require_whatsapp_token()
    req = urlrequest.Request(
        info.url,
        method="GET",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        return urlrequest.urlopen(req, timeout=30)
    except error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"WhatsApp media download failed: HTTP {exc.code}: {err_body}") from exc
    except error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"WhatsApp media download failed: {exc.reason}") from exc


def _download_whatsapp_media_bytes(media_id: str) -> tuple[WhatsAppMediaInfoOut, bytes]:
    info = _load_whatsapp_media_info(media_id)
    upstream = _open_whatsapp_media_stream(info)
    try:
        data = upstream.read()
    finally:
        upstream.close()
    return info, data


def _guess_media_filename(media_id: str, mime_type: str | None) -> str:
    mime_base = (mime_type or "").split(";")[0].strip().lower()
    extension = mimetypes.guess_extension(mime_base) or ".bin"
    if extension == ".oga":
        extension = ".ogg"
    if mime_base in ("audio/ogg", "audio/opus"):
        extension = ".ogg"
    if mime_base == "audio/mpeg":
        extension = ".mp3"
    if mime_base == "audio/mp4":
        extension = ".mp4"
    return f"{media_id}{extension}"


def _transcribe_audio_bytes(media_id: str, audio_bytes: bytes, mime_type: str | None) -> str:
    api_key = _require_openai_api_key()
    boundary = f"----CodexBoundary{uuid4().hex}"
    filename = _guess_media_filename(media_id, mime_type)
    parts = [
        f"--{boundary}\r\n".encode("utf-8"),
        f'Content-Disposition: form-data; name="model"\r\n\r\nwhisper-1\r\n'.encode("utf-8"),
        f"--{boundary}\r\n".encode("utf-8"),
        f'Content-Disposition: form-data; name="language"\r\n\r\nes\r\n'.encode("utf-8"),
        f"--{boundary}\r\n".encode("utf-8"),
        (
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {mime_type or 'application/octet-stream'}\r\n\r\n"
        ).encode("utf-8"),
        audio_bytes,
        b"\r\n",
        f"--{boundary}--\r\n".encode("utf-8"),
    ]
    body = b"".join(parts)
    req = urlrequest.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
    )
    try:
        with urlrequest.urlopen(req, timeout=60) as resp:
            response_body = resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"OpenAI transcription failed: HTTP {exc.code}: {err_body}") from exc
    except error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI transcription failed: {exc.reason}") from exc

    try:
        payload = json.loads(response_body) if response_body.strip() else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="OpenAI transcription returned invalid JSON") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="OpenAI transcription returned an invalid payload")

    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=502, detail="OpenAI transcription did not return text")
    return text


def _require_thread(db: Session, thread_id: int) -> WhatsAppThread:
    thread = db.get(WhatsAppThread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return thread


@router.get("/media/{media_id}/info", response_model=WhatsAppMediaInfoOut)
def get_media_info(media_id: str):
    return _load_whatsapp_media_info(media_id)


@router.get("/media/{media_id}")
def download_media(media_id: str):
    info = _load_whatsapp_media_info(media_id)
    upstream = _open_whatsapp_media_stream(info)

    def _iter_stream():
        try:
            while True:
                chunk = upstream.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            upstream.close()

    headers: dict[str, str] = {}
    if info.file_size is not None:
        headers["Content-Length"] = str(info.file_size)

    return StreamingResponse(_iter_stream(), media_type=info.mime_type or "application/octet-stream", headers=headers)


@router.post("/media/{media_id}/transcribe", response_model=WhatsAppMediaTranscriptionOut)
def transcribe_media(media_id: str):
    info, audio_bytes = _download_whatsapp_media_bytes(media_id)
    text = _transcribe_audio_bytes(media_id=info.media_id, audio_bytes=audio_bytes, mime_type=info.mime_type)
    return WhatsAppMediaTranscriptionOut(text=text)


@router.get("/threads", response_model=list[WhatsAppThreadOut])
def list_threads(db: Session = Depends(get_db)):
    thread_ids = db.execute(
        select(WhatsAppThread.id).order_by(WhatsAppThread.last_message_at.desc().nullslast(), WhatsAppThread.id.desc())
    ).scalars().all()
    return [payload for tid in thread_ids if (payload := load_thread_payload(db, int(tid))) is not None]


@router.get("/thread/{thread_id}", response_model=WhatsAppThreadOut)
def get_thread(thread_id: int, db: Session = Depends(get_db)):
    payload = load_thread_payload(db, thread_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return payload


@router.get("/thread/{thread_id}/messages", response_model=WhatsAppThreadMessagesOut)
def get_thread_messages(thread_id: int, limit: int = Query(default=10, ge=1, le=20), db: Session = Depends(get_db)):
    _require_thread(db, thread_id)

    messages = load_recent_thread_messages(db=db, thread_id=thread_id, limit=limit)
    return WhatsAppThreadMessagesOut(thread_id=thread_id, messages=messages)


@router.get("/thread/{thread_id}/latest-inbound", response_model=LatestInboundMessageOut)
def get_thread_latest_inbound(
    thread_id: int,
    before: datetime | None = Query(default=None),
    before_message_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    _require_thread(db, thread_id)
    if before_message_id is not None:
        row = db.execute(
            select(WhatsAppMessage.timestamp).where(WhatsAppMessage.wa_message_id == before_message_id)
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="before_message_id not found")
        before = row.timestamp
    return load_latest_inbound_message(db=db, thread_id=thread_id, before=before)


@router.get("/thread/{thread_id}/state", response_model=WhatsAppThreadStateRead)
def get_thread_state(thread_id: int, db: Session = Depends(get_db)):
    thread = _require_thread(db, thread_id)
    return build_thread_state_read(thread_id=thread_id, state=thread.state)


@router.patch("/thread/{thread_id}/state", response_model=WhatsAppThreadStateRead)
def patch_thread_state(thread_id: int, payload: WhatsAppThreadStatePatch, db: Session = Depends(get_db)):
    thread = _require_thread(db, thread_id)
    return upsert_thread_state(db=db, thread=thread, payload=payload)


@router.patch("/thread/{thread_id}/display-name", response_model=WhatsAppThreadOut)
def patch_thread_display_name(
    thread_id: int,
    payload: WhatsAppThreadDisplayNamePatch,
    db: Session = Depends(get_db),
):
    new_name = (payload.display_name or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="display_name is required")

    thread_data = db.execute(
        select(WhatsAppThread, WhatsAppContact)
        .join(WhatsAppContact, WhatsAppThread.contact_id == WhatsAppContact.id)
        .where(WhatsAppThread.id == thread_id)
    ).first()
    if thread_data is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    contact = thread_data[1]
    contact.display_name = new_name
    commit_or_400(db, detail="No se pudo actualizar el nombre del chat")
    refreshed = load_thread_payload(db, thread_id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return refreshed


@router.get("/thread/{thread_id}/candidates", response_model=list[WhatsAppThreadCandidateRead])
def list_thread_candidates(thread_id: int, db: Session = Depends(get_db)):
    _require_thread(db, thread_id)
    return db.execute(
        select(WhatsAppThreadCandidate)
        .where(WhatsAppThreadCandidate.thread_id == thread_id)
        .order_by(WhatsAppThreadCandidate.updated_at.desc(), WhatsAppThreadCandidate.id.desc())
    ).scalars().all()


@router.post("/thread/{thread_id}/candidates", response_model=WhatsAppThreadCandidateRead)
def create_thread_candidate(
    thread_id: int,
    payload: WhatsAppThreadCandidateCreate,
    db: Session = Depends(get_db),
):
    _require_thread(db, thread_id)
    candidate = WhatsAppThreadCandidate(thread_id=thread_id, **payload.model_dump(exclude_unset=True))
    db.add(candidate)
    try:
        db.commit()
    except (DataError, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="No se pudo crear el candidato del thread") from exc

    db.refresh(candidate)
    return candidate


@router.patch("/thread/{thread_id}/candidates/{candidate_id}", response_model=WhatsAppThreadCandidateRead)
def patch_thread_candidate(
    thread_id: int,
    candidate_id: int,
    payload: WhatsAppThreadCandidatePatch,
    db: Session = Depends(get_db),
):
    _require_thread(db, thread_id)
    candidate = db.execute(
        select(WhatsAppThreadCandidate)
        .where(WhatsAppThreadCandidate.id == candidate_id)
        .where(WhatsAppThreadCandidate.thread_id == thread_id)
    ).scalar_one_or_none()
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(candidate, field, value)

    try:
        db.commit()
    except (DataError, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="No se pudo actualizar el candidato del thread") from exc

    db.refresh(candidate)
    return candidate


@router.post("/thread/{thread_id}/send-text", response_model=WhatsAppSendTextOut)
def send_thread_text(thread_id: int, payload: WhatsAppSendTextIn, db: Session = Depends(get_db)):
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    thread_data = db.execute(
        select(WhatsAppThread, WhatsAppContact.wa_id)
        .join(WhatsAppContact, WhatsAppThread.contact_id == WhatsAppContact.id)
        .where(WhatsAppThread.id == thread_id)
    ).first()
    if thread_data is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    thread = thread_data[0]
    to_wa_id = str(thread_data.wa_id or "").strip()
    if not to_wa_id:
        raise HTTPException(status_code=400, detail="Thread has no wa_id")

    from zoneinfo import ZoneInfo; now_utc = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
    outbound = WhatsAppMessage(
        thread_id=thread_id,
        wa_message_id=None,
        direction="out",
        status="pending",
        timestamp=now_utc,
        text=text,
        raw_payload={"reply_to_message_id": payload.reply_to_message_id}
        if payload.reply_to_message_id is not None
        else None,
    )
    db.add(outbound)
    thread.last_message_at = now_utc
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        outbound.status = "sent"
        db.add(outbound)
        thread.last_message_at = now_utc
        db.commit()
    reset_unanswered_alert(db, thread_id)
    db.commit()

    try:
        wa_message_id, _ = _send_whatsapp_cloud_text(to_wa_id=to_wa_id, text=text)
        outbound.status = "sent"
        outbound.wa_message_id = wa_message_id
        db.add(outbound)
        db.commit()
        return WhatsAppSendTextOut(ok=True, thread_id=thread_id, wa_message_id=wa_message_id, text=text)
    except Exception as exc:
        db.rollback()
        outbound.status = "failed"
        db.add(outbound)
        db.commit()
        raise HTTPException(status_code=502, detail=f"WhatsApp outbound send failed: {exc}") from exc


def _store_outbound_and_send(
    db: Session,
    thread_id: int,
    thread: WhatsAppThread,
    to_wa_id: str,
    body_text: str,
    send_fn,
) -> tuple[WhatsAppMessage, str]:
    """Shared helper: persist outbound message then call send_fn(to_wa_id, ...) → wa_message_id."""
    from zoneinfo import ZoneInfo
    now_utc = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
    outbound = WhatsAppMessage(
        thread_id=thread_id,
        wa_message_id=None,
        direction="out",
        status="pending",
        timestamp=now_utc,
        text=body_text,
    )
    db.add(outbound)
    thread.last_message_at = now_utc
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        outbound.status = "sent"
        db.add(outbound)
        thread.last_message_at = now_utc
        db.commit()
    reset_unanswered_alert(db, thread_id)
    db.commit()

    try:
        wa_message_id = send_fn(to_wa_id)
        outbound.status = "sent"
        outbound.wa_message_id = wa_message_id
        db.add(outbound)
        db.commit()
        return outbound, wa_message_id
    except Exception as exc:
        db.rollback()
        outbound.status = "failed"
        db.add(outbound)
        db.commit()
        raise exc


def _resolve_thread_wa_id(db: Session, thread_id: int) -> tuple[WhatsAppThread, str]:
    thread_data = db.execute(
        select(WhatsAppThread, WhatsAppContact.wa_id)
        .join(WhatsAppContact, WhatsAppThread.contact_id == WhatsAppContact.id)
        .where(WhatsAppThread.id == thread_id)
    ).first()
    if thread_data is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    to_wa_id = str(thread_data.wa_id or "").strip()
    if not to_wa_id:
        raise HTTPException(status_code=400, detail="Thread has no wa_id")
    return thread_data[0], to_wa_id


@router.post("/thread/{thread_id}/send-interactive", response_model=WhatsAppSendInteractiveOut)
def send_thread_interactive(thread_id: int, payload: WhatsAppSendInteractiveIn, db: Session = Depends(get_db)):
    """M16.3 — Send an interactive button message (max 3 buttons) to a WhatsApp thread."""
    body_text = (payload.body or "").strip()
    if not body_text:
        raise HTTPException(status_code=400, detail="body is required")
    if not payload.buttons:
        raise HTTPException(status_code=400, detail="At least one button is required")

    thread, to_wa_id = _resolve_thread_wa_id(db, thread_id)
    buttons = [{"id": btn.id, "title": btn.title} for btn in payload.buttons]

    try:
        _, wa_message_id = _store_outbound_and_send(
            db, thread_id, thread, to_wa_id, body_text,
            lambda wa_id: _send_whatsapp_cloud_interactive(wa_id, body_text, buttons)[0],
        )
        return WhatsAppSendInteractiveOut(ok=True, thread_id=thread_id, wa_message_id=wa_message_id, body=body_text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"WhatsApp interactive send failed: {exc}") from exc


@router.post("/thread/{thread_id}/send-list", response_model=WhatsAppSendListOut)
def send_thread_list(thread_id: int, payload: WhatsAppSendListIn, db: Session = Depends(get_db)):
    """M16.4 — Send an interactive list message (up to 10 rows) to a WhatsApp thread."""
    body_text = (payload.body or "").strip()
    if not body_text:
        raise HTTPException(status_code=400, detail="body is required")
    if not payload.rows:
        raise HTTPException(status_code=400, detail="At least one row is required")

    thread, to_wa_id = _resolve_thread_wa_id(db, thread_id)
    rows = [{"id": r.id, "title": r.title, "description": r.description} for r in payload.rows]

    try:
        _, wa_message_id = _store_outbound_and_send(
            db, thread_id, thread, to_wa_id, body_text,
            lambda wa_id: _send_whatsapp_cloud_list(
                wa_id, body_text, payload.button_label, payload.section_title, rows
            )[0],
        )
        return WhatsAppSendListOut(ok=True, thread_id=thread_id, wa_message_id=wa_message_id, body=body_text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"WhatsApp list send failed: {exc}") from exc


@router.post("/thread/{thread_id}/send-flow", response_model=WhatsAppSendFlowOut)
def send_thread_flow(thread_id: int, payload: WhatsAppSendFlowIn, db: Session = Depends(get_db)):
    """M17 — Send a WhatsApp Flow button to a thread. The Flow collects booking data
    (name, email, address, seller type, etc.) natively within WhatsApp.
    Requires WHATSAPP_FLOW_ID env var to be set."""
    settings = get_settings()
    flow_id = (settings.whatsapp_flow_id or "").strip()
    if not flow_id:
        raise HTTPException(status_code=503, detail="WHATSAPP_FLOW_ID not configured")

    body_text = (payload.body or "").strip()
    if not body_text:
        raise HTTPException(status_code=400, detail="body is required")

    thread, to_wa_id = _resolve_thread_wa_id(db, thread_id)

    from zoneinfo import ZoneInfo
    import time as _time
    flow_token = (payload.flow_token or "").strip() or f"{thread_id}-{int(_time.time())}"
    cta_label = (payload.cta_label or "Completar datos").strip()

    try:
        _, wa_message_id = _store_outbound_and_send(
            db, thread_id, thread, to_wa_id, body_text,
            lambda wa_id: _send_whatsapp_cloud_flow(
                to_wa_id=wa_id,
                flow_id=flow_id,
                flow_token=flow_token,
                body_text=body_text,
                cta_label=cta_label,
            )[0],
        )
        return WhatsAppSendFlowOut(
            ok=True,
            thread_id=thread_id,
            wa_message_id=wa_message_id,
            body=body_text,
            flow_token=flow_token,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"WhatsApp Flow send failed: {exc}") from exc


@router.post("/thread/{thread_id}/link", response_model=WhatsAppThreadOut)
def link_thread(thread_id: int, payload: WhatsAppThreadLinkIn, db: Session = Depends(get_db)):
    thread = db.get(WhatsAppThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    if not db.get(Lead, payload.lead_id):
        raise HTTPException(status_code=404, detail="Lead not found")

    thread.lead_id = payload.lead_id
    commit_or_400(db, detail="No se pudo vincular el thread de WhatsApp")
    refreshed = load_thread_payload(db, thread_id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return refreshed


@router.post("/thread/{thread_id}/unlink", response_model=WhatsAppThreadOut)
def unlink_thread(thread_id: int, db: Session = Depends(get_db)):
    thread = db.get(WhatsAppThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    thread.lead_id = None
    commit_or_400(db, detail="No se pudo desvincular el thread de WhatsApp")
    refreshed = load_thread_payload(db, thread_id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return refreshed


@router.post("/thread/{thread_id}/set-latest", response_model=LatestInboundOut)
def set_thread_latest_inbound(thread_id: int, payload: SetLatestInboundIn, db: Session = Depends(get_db)):
    thread = _require_thread(db, thread_id)
    thread.latest_inbound_wa_message_id = payload.wa_message_id
    commit_or_400(db, detail="No se pudo actualizar latest inbound")
    return LatestInboundOut(thread_id=thread_id, wa_message_id=payload.wa_message_id)


@router.get("/thread/{thread_id}/get-latest", response_model=LatestInboundOut)
def get_thread_latest_inbound_ephemeral(thread_id: int, db: Session = Depends(get_db)):
    thread = _require_thread(db, thread_id)
    return LatestInboundOut(thread_id=thread_id, wa_message_id=thread.latest_inbound_wa_message_id)


class SendToPhoneIn(BaseModel):
    wa_id: str
    text: str
    reply_to_message_id: str | None = None


class SendToPhoneOut(BaseModel):
    ok: bool
    wa_message_id: str | None = None
    note: str | None = None


@router.post("/send-to-phone", response_model=SendToPhoneOut)
def send_to_phone(payload: SendToPhoneIn, db: Session = Depends(get_db)):
    """Send a WhatsApp text message to an arbitrary wa_id (no thread required).
    Used for internal notifications, e.g. Julián booking alerts.
    ok=True means Meta accepted the request (WAMID issued). Actual delivery requires
    the recipient to have messaged the business number within the last 24 hours.
    Saves message to DB so Meta delivery status webhooks are tracked and failures
    surface as ERROR-level logs (not silently discarded)."""
    from zoneinfo import ZoneInfo
    wa_id = (payload.wa_id or "").strip()
    text = (payload.text or "").strip()
    if not wa_id:
        raise HTTPException(status_code=400, detail="wa_id is required")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    now_utc = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))

    # Find or create contact + thread so delivery status webhooks can update the record.
    contact = db.execute(
        select(WhatsAppContact).where(WhatsAppContact.wa_id == wa_id)
    ).scalar_one_or_none()
    if contact is None:
        contact = WhatsAppContact(wa_id=wa_id, display_name=None, phone=None)
        db.add(contact)
        db.flush()

    thread = db.execute(
        select(WhatsAppThread)
        .where(WhatsAppThread.contact_id == contact.id)
        .order_by(WhatsAppThread.id.asc())
    ).scalars().first()
    if thread is None:
        thread = WhatsAppThread(contact_id=contact.id, lead_id=None, unread_count=0)
        db.add(thread)
        db.flush()

    db.commit()

    # Send to Meta first, then persist the result for delivery tracking.
    send_exc: Exception | None = None
    wa_message_id: str | None = None
    final_status = "failed"
    try:
        wa_message_id, _ = _send_whatsapp_cloud_text(to_wa_id=wa_id, text=text)
        final_status = "sent"
    except Exception as exc:
        send_exc = exc

    # Save the message record with the real status so the webhook handler can track delivery.
    try:
        outbound = WhatsAppMessage(
            thread_id=thread.id,
            wa_message_id=wa_message_id,
            direction="out",
            status=final_status,
            timestamp=now_utc,
            text=text,
        )
        db.add(outbound)
        thread.last_message_at = now_utc
        db.commit()
    except Exception:
        db.rollback()

    if send_exc is not None:
        raise HTTPException(status_code=502, detail=f"WhatsApp send failed: {send_exc}") from send_exc

    return SendToPhoneOut(
        ok=True,
        wa_message_id=wa_message_id,
        note="Meta accepted. If Julian has not messaged this number within 24h, delivery will fail.",
    )


@thread_router.post("/whatsapp/thread/{thread_id}/link-lead", response_model=WhatsAppThreadOut)
def link_thread_lead(thread_id: int, payload: WhatsAppThreadLinkIn, db: Session = Depends(get_db)):
    return link_thread(thread_id=thread_id, payload=payload, db=db)


@thread_router.post("/whatsapp/thread/{thread_id}/unlink-lead", response_model=WhatsAppThreadOut)
def unlink_thread_lead(thread_id: int, db: Session = Depends(get_db)):
    return unlink_thread(thread_id=thread_id, db=db)
