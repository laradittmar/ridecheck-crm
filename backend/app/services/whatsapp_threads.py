from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import WhatsAppContact, WhatsAppMessage, WhatsAppThread
from ..schemas.whatsapp_api import WhatsAppMessageOut, WhatsAppThreadOut


def _thread_preview(text: str | None) -> str | None:
    preview = (text or "").replace("\r", " ").replace("\n", " ").strip()
    return preview or None


def load_thread_payload(db: Session, thread_id: int) -> WhatsAppThreadOut | None:
    row = db.execute(
        select(
            WhatsAppThread.id.label("thread_id"),
            WhatsAppThread.lead_id.label("lead_id"),
            WhatsAppThread.contact_id.label("contact_id"),
            WhatsAppThread.unread_count.label("unread_count"),
            WhatsAppThread.last_message_at.label("last_message_at"),
            WhatsAppContact.wa_id.label("wa_id"),
            WhatsAppContact.display_name.label("display_name"),
        )
        .join(WhatsAppContact, WhatsAppThread.contact_id == WhatsAppContact.id)
        .where(WhatsAppThread.id == thread_id)
    ).first()
    if not row:
        return None

    preview = db.execute(
        select(WhatsAppMessage.id, WhatsAppMessage.text)
        .where(WhatsAppMessage.thread_id == thread_id)
        .order_by(WhatsAppMessage.timestamp.desc(), WhatsAppMessage.id.desc())
        .limit(1)
    ).first()

    return WhatsAppThreadOut(
        thread_id=int(row.thread_id),
        lead_id=row.lead_id,
        contact_id=int(row.contact_id),
        wa_id=str(row.wa_id),
        display_name=row.display_name,
        unread_count=int(row.unread_count or 0),
        last_message_at=row.last_message_at,
        last_message_preview=_thread_preview(preview.text if preview else None),
        last_message_id=int(preview.id) if preview is not None else None,
    )


def load_recent_thread_messages(db: Session, thread_id: int, limit: int) -> list[WhatsAppMessageOut]:
    rows = db.execute(
        select(
            WhatsAppMessage.id,
            WhatsAppMessage.thread_id,
            WhatsAppMessage.direction,
            WhatsAppMessage.text,
            WhatsAppMessage.timestamp,
            WhatsAppMessage.status,
            WhatsAppMessage.wa_message_id,
        )
        .where(WhatsAppMessage.thread_id == thread_id)
        .order_by(WhatsAppMessage.timestamp.desc(), WhatsAppMessage.id.desc())
        .limit(limit)
    ).all()

    rows_asc = list(reversed(rows))
    return [
        WhatsAppMessageOut(
            id=int(row.id),
            thread_id=int(row.thread_id),
            direction=str(row.direction),
            text=row.text,
            timestamp=row.timestamp,
            status=row.status,
            wa_message_id=row.wa_message_id,
        )
        for row in rows_asc
    ]
