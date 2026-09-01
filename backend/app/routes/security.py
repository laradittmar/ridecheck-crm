"""Security operator query endpoint — M2.

Read-only surface for unauthorized outbound path events and outbound ledger.

  GET /security/unauthorized-path-events
      ?since=ISO8601   (default: last 24 hours)
      ?until=ISO8601   (default: now)
      ?wamid=...       filter by Meta WAMID
      ?thread_id=...
      ?deployment_id=...
      ?severity=HIGH|BLOCKER
      ?fingerprint=... filter by content_fingerprint

  GET /security/outbound-ledger
      ?since=ISO8601
      ?until=ISO8601
      ?wamid=...       exact WAMID lookup
      ?thread_id=...
      ?path_id=...     filter by authorized path
      ?fingerprint=... filter by content_fingerprint
      ?status=...      filter by message status
      ?limit=200

Answers: "What outbound messages did the system attempt to send?"
         "Did anything try to send WhatsApp outside an approved path?"
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import SecurityEvent, WhatsAppMessage

router = APIRouter(prefix="/security", tags=["security"])


@router.get("/unauthorized-path-events")
def list_unauthorized_path_events(
    since: Optional[str] = Query(None, description="ISO8601 start (default: 24h ago)"),
    until: Optional[str] = Query(None, description="ISO8601 end (default: now)"),
    wamid: Optional[str] = Query(None, description="Exact Meta WAMID"),
    thread_id: Optional[int] = Query(None),
    deployment_id: Optional[str] = Query(None),
    severity: Optional[str] = Query(None, description="HIGH or BLOCKER"),
    fingerprint: Optional[str] = Query(None, description="content_fingerprint (hex prefix ok)"),
    limit: int = Query(200, le=1000),
    db: Session = Depends(get_db),
):
    """Return SecurityEvent records matching the query parameters."""
    now = datetime.now(timezone.utc)
    since_dt = _parse_dt(since) if since else (now - timedelta(hours=24))
    until_dt = _parse_dt(until) if until else now

    q = select(SecurityEvent).where(
        SecurityEvent.detected_at >= since_dt,
        SecurityEvent.detected_at <= until_dt,
    )
    if wamid:
        q = q.where(SecurityEvent.wamid == wamid)
    if thread_id is not None:
        q = q.where(SecurityEvent.thread_id == thread_id)
    if deployment_id:
        q = q.where(SecurityEvent.deployment_id == deployment_id)
    if severity:
        q = q.where(SecurityEvent.severity == severity.upper())
    if fingerprint:
        q = q.where(SecurityEvent.details["fp"].as_string().startswith(fingerprint))

    q = q.order_by(SecurityEvent.detected_at.desc()).limit(limit)
    rows = db.execute(q).scalars().all()

    return {
        "query": {
            "since": since_dt.isoformat(),
            "until": until_dt.isoformat(),
            "wamid": wamid,
            "thread_id": thread_id,
            "deployment_id": deployment_id,
            "severity": severity,
            "fingerprint": fingerprint,
        },
        "count": len(rows),
        "deployment_id": os.environ.get("GIT_SHA", "unknown"),
        "events": [_serialize(e) for e in rows],
    }


@router.get("/outbound-ledger")
def list_outbound_ledger(
    since: Optional[str] = Query(None, description="ISO8601 start (default: 24h ago)"),
    until: Optional[str] = Query(None, description="ISO8601 end (default: now)"),
    wamid: Optional[str] = Query(None, description="Exact Meta WAMID lookup"),
    thread_id: Optional[int] = Query(None),
    path_id: Optional[str] = Query(None, description="Authorized path_id filter"),
    fingerprint: Optional[str] = Query(None, description="content_fingerprint (hex prefix ok)"),
    status: Optional[str] = Query(None, description="pending|sent|delivered|read|failed|blocked"),
    limit: int = Query(200, le=1000),
    db: Session = Depends(get_db),
):
    """Return outbound WhatsAppMessage records for forensic reconstruction.

    WAMID lookup: ?wamid=wamid.xxx returns the single record tied to that Meta ID.
    Fingerprint lookup: ?fingerprint=ab12 matches content_fingerprint prefix.
    Window: ?since=...&until=... scopes to a time range.
    """
    now = datetime.now(timezone.utc)
    since_dt = _parse_dt(since) if since else (now - timedelta(hours=24))
    until_dt = _parse_dt(until) if until else now

    q = select(WhatsAppMessage).where(
        WhatsAppMessage.direction == "out",
        WhatsAppMessage.automated == True,
        WhatsAppMessage.timestamp >= since_dt,
        WhatsAppMessage.timestamp <= until_dt,
    )
    if wamid:
        q = q.where(WhatsAppMessage.wa_message_id == wamid)
    if thread_id is not None:
        q = q.where(WhatsAppMessage.thread_id == thread_id)
    if path_id:
        q = q.where(WhatsAppMessage.path_id == path_id)
    if fingerprint:
        q = q.where(WhatsAppMessage.content_fingerprint.startswith(fingerprint))
    if status:
        q = q.where(WhatsAppMessage.status == status.lower())

    q = q.order_by(WhatsAppMessage.timestamp.desc()).limit(limit)
    rows = db.execute(q).scalars().all()

    return {
        "query": {
            "since": since_dt.isoformat(),
            "until": until_dt.isoformat(),
            "wamid": wamid,
            "thread_id": thread_id,
            "path_id": path_id,
            "fingerprint": fingerprint,
            "status": status,
        },
        "count": len(rows),
        "deployment_id": os.environ.get("GIT_SHA", "unknown"),
        "records": [_serialize_outbound(r) for r in rows],
    }


def _parse_dt(s: str) -> datetime:
    """Parse ISO8601 string; assume UTC if no timezone."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _serialize_outbound(m: WhatsAppMessage) -> dict:
    return {
        "id": m.id,
        "thread_id": m.thread_id,
        "status": m.status,
        "wa_message_id": m.wa_message_id,
        "path_id": m.path_id,
        "deployment_id": m.deployment_id,
        "correlation_id": m.correlation_id,
        "content_fingerprint": m.content_fingerprint,
        "message_type": m.message_type,
        "timestamp": m.timestamp.isoformat() if m.timestamp else None,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "blocked_reason": m.blocked_reason,
        "meta_http_status": getattr(m, "meta_http_status", None),
        "meta_error_payload": getattr(m, "meta_error_payload", None),
    }


def _serialize(e: SecurityEvent) -> dict:
    return {
        "id": e.id,
        "detected_at": e.detected_at.isoformat() if e.detected_at else None,
        "event_type": e.event_type,
        "severity": e.severity,
        "path_id": e.path_id,
        "source_component": e.source_component,
        "wamid": e.wamid,
        "wa_id_hash": e.wa_id_hash,
        "deployment_id": e.deployment_id,
        "correlation_id": e.correlation_id,
        "thread_id": e.thread_id,
        "details": e.details,
        "alert_sent_at": e.alert_sent_at.isoformat() if e.alert_sent_at else None,
    }
