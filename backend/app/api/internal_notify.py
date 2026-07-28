"""M15.8 — Internal booking notification endpoint (Resend email)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Revision, ThreadRevision
from ..services.resend_email import send_booking_notification
from ..settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/internal", tags=["internal"])

# Fast-path in-memory guard (clears on restart; DB column is the persistent gate).
_sent_revisions: set[int] = set()


def _s(v: object) -> str:
    s = str(v).strip() if v is not None else ""
    return s if s else ""


class NotifyBookingIn(BaseModel):
    revision_id: int
    lead_id: int
    # All booking fields sent directly from n8n context — no DB race possible.
    buyer_name:      str | None = None
    buyer_phone:     str | None = None
    buyer_email:     str | None = None
    source:          str | None = None
    marca:           str | None = None
    modelo:          str | None = None
    anio:            str | None = None
    tipo_vehiculo:   str | None = None
    zone_group:      str | None = None
    zone_detail:     str | None = None
    address:         str | None = None
    seller_type:     str | None = None
    seller_name:     str | None = None
    scheduled_date:  str | None = None
    scheduled_time:  str | None = None


class NotifyBookingOut(BaseModel):
    ok: bool
    error: str | None = None


@router.post("/notify-booking", response_model=NotifyBookingOut)
def notify_booking(payload: NotifyBookingIn, db: Session = Depends(get_db)):
    """Send internal booking notification email via Resend. Never raises."""
    revision_id = payload.revision_id
    lead_id     = payload.lead_id

    # ── Fast-path in-memory dedup ──────────────────────────────────────────
    if revision_id in _sent_revisions:
        logger.warning("[M15.8] In-memory dedup: revision_id=%s already sent — skipped", revision_id)
        return NotifyBookingOut(ok=True, error="duplicate skipped")

    # ── DB-level persistent idempotency ───────────────────────────────────
    try:
        row = db.execute(
            text("SELECT notification_sent_at FROM thread_revisions WHERE id = :id"),
            {"id": revision_id},
        ).first()
        if row is None:
            logger.error("[M15.8] ThreadRevision %s not found in DB", revision_id)
            return NotifyBookingOut(ok=False, error=f"ThreadRevision {revision_id} not found")
        if row.notification_sent_at is not None:
            logger.warning(
                "[M15.8] DB dedup: revision_id=%s already notified at %s — skipped",
                revision_id, row.notification_sent_at,
            )
            _sent_revisions.add(revision_id)
            return NotifyBookingOut(ok=True, error="already sent")
    except Exception as exc:
        # Column might not exist yet on first deploy; fall through.
        logger.warning("[M15.8] DB idempotency check failed for revision_id=%s: %s", revision_id, exc)

    settings = get_settings()
    if not settings.resend_api_key:
        logger.error("[M15.8] RESEND_API_KEY missing — email not sent for revision_id=%s", revision_id)
        return NotifyBookingOut(ok=False, error="RESEND_API_KEY not configured")

    # ── Prices come from DB (auto-calculated by backend on CRM revision) ──
    precio_base = precio_viaticos = precio_total = "Sin dato"
    try:
        crm_rev: Revision | None = db.execute(
            select(Revision)
            .where(Revision.lead_id == lead_id)
            .order_by(Revision.created_at.desc())
            .limit(1)
        ).scalars().first()
        if crm_rev:
            if crm_rev.precio_base  is not None: precio_base     = str(crm_rev.precio_base)
            if crm_rev.viaticos     is not None: precio_viaticos = str(crm_rev.viaticos)
            if crm_rev.precio_total is not None: precio_total    = str(crm_rev.precio_total)
    except Exception as exc:
        logger.warning("[M15.8] Could not fetch CRM revision for prices: %s", exc)

    # ── Build email fields (prefer n8n-provided, fall back to "Sin dato") ─
    def _field(v: str | None, fallback: str = "Sin dato") -> str:
        s = _s(v)
        return s if s else fallback

    ok = send_booking_notification(
        api_key=settings.resend_api_key,
        from_email=settings.internal_booking_email_from,
        to_email=settings.internal_booking_email_to,
        reply_to_email=settings.internal_booking_email_reply_to,
        lead_id=lead_id,
        revision_id=revision_id,
        buyer_name=     _field(payload.buyer_name),
        buyer_phone=    _field(payload.buyer_phone),
        buyer_email=    _field(payload.buyer_email),
        source=         _field(payload.source),
        marca=          _field(payload.marca),
        modelo=         _field(payload.modelo),
        anio=           _field(payload.anio),
        tipo_vehiculo=  _field(payload.tipo_vehiculo),
        zone_group=     _field(payload.zone_group),
        zone_detail=    _field(payload.zone_detail),
        address=        _field(payload.address),
        seller_type=    _field(payload.seller_type),
        seller_name=    _field(payload.seller_name),
        scheduled_date= _field(payload.scheduled_date),
        scheduled_time= _field(payload.scheduled_time),
        precio_base=    precio_base,
        viaticos=       precio_viaticos,
        precio_total=   precio_total,
    )

    if ok:
        _sent_revisions.add(revision_id)
        # Mark in DB — survives backend restarts.
        try:
            db.execute(
                text("UPDATE thread_revisions SET notification_sent_at = NOW() WHERE id = :id"),
                {"id": revision_id},
            )
            db.commit()
            logger.info("[M15.8] revision_id=%s marked as notified in DB", revision_id)
        except Exception as exc:
            logger.warning("[M15.8] Could not mark revision_id=%s in DB: %s", revision_id, exc)
        return NotifyBookingOut(ok=True)

    return NotifyBookingOut(ok=False, error="Resend API call failed — see logs")
