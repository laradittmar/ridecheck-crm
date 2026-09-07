"""Operational observability dashboard API — read-only.

All endpoints are SELECT-only. No INSERT/UPDATE/DELETE.

Prefix: /api/ops
Tag: ops
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Integer, case, func, select, text
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    AiEvent,
    SecurityEvent,
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppThread,
    WhatsAppThreadState,
)

router = APIRouter(prefix="/api/ops", tags=["ops"])

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

AUTHORIZED_PATHS: list[str] = [
    "CE_TEXT",
    "CE_FLOW",
    "CE_INTERACTIVE",
    "CE_LIST",
    "MANUAL_CRM",
    "BOOKING_FLOW",
    "SYSTEM_NOTIFICATION",
]

LEGACY_PATHS: list[str] = ["LEGACY_N8N_AI_PIPELINE"]

# All known paths (authorized + legacy) — anything outside this set is critical
_KNOWN_PATHS: set[str] = set(AUTHORIZED_PATHS) | set(LEGACY_PATHS)

UNANSWERED_WARNING_SECONDS = 120
UNANSWERED_CRITICAL_SECONDS = 300

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EVENT_TYPE_TO_CATEGORY: dict[str, str] = {
    "OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE": "UNAUTHORIZED_PATH",
    "UNREGISTERED_OUTBOUND_SOURCE": "UNAUTHORIZED_PATH",
    "LEGACY_SENDER_REACHED": "LEGACY_PATH_REACHED",
    "META_STATUS_FOR_UNKNOWN_WAMID": "UNKNOWN_WAMID",
    "SUCCESSFUL_META_SEND_WHILE_OUTBOUND_OFF": "OUTBOUND_OFF_BUT_META_SUCCESS",
}


def _window_range(window: str) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    if window == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif window == "7d":
        start = now - timedelta(days=7)
    else:  # 24h default
        start = now - timedelta(hours=24)
    return start, now


def _mask_wa_id(wa_id: str | None) -> str:
    if not wa_id:
        return "—"
    s = wa_id.strip()
    if len(s) <= 7:
        return s[:3] + "*" * (len(s) - 3)
    return s[:5] + "*" * (len(s) - 7) + s[-2:]


def _preview(text: str | None, message_type: str | None) -> str:
    if text:
        return text[:80]
    return message_type or ""


def _is_path_critical(path_id: str | None) -> bool:
    if path_id is None:
        return True
    if path_id in LEGACY_PATHS:
        return True
    if path_id not in AUTHORIZED_PATHS:
        return True
    return False


def _percentile(sorted_vals: list[int], p: float) -> int | None:
    """Return the p-th percentile (0–1) of a pre-sorted list, or None if empty."""
    if not sorted_vals:
        return None
    idx = int(len(sorted_vals) * p)
    idx = min(idx, len(sorted_vals) - 1)
    return sorted_vals[idx]


# ---------------------------------------------------------------------------
# Unanswered / thread health helpers
# ---------------------------------------------------------------------------

def _classify_thread_health(
    needs_human: bool | None,
    latest_direction: str | None,
) -> str:
    nh = bool(needs_human)
    if latest_direction == "out":
        return "WAITING_CUSTOMER"
    if latest_direction == "in":
        if nh:
            return "WAITING_HUMAN"
        return "UNANSWERED_BOT"
    return "OK"


def _age_tier(health: str, waiting_seconds: float | None) -> str | None:
    if health not in ("UNANSWERED_BOT", "WAITING_HUMAN"):
        return None
    if waiting_seconds is None:
        return "NORMAL"
    if waiting_seconds < UNANSWERED_WARNING_SECONDS:
        return "NORMAL"
    if waiting_seconds < UNANSWERED_CRITICAL_SECONDS:
        return "WARNING"
    return "CRITICAL"


# ---------------------------------------------------------------------------
# Shared query: thread health rows
# ---------------------------------------------------------------------------

def _query_thread_health_rows(db: Session, cutoff: datetime) -> list:
    """
    Return one row per thread with last_message_at >= cutoff.

    Each row has:
        thread_id, lead_id, display_name, wa_id, latest_msg_id,
        latest_direction, latest_ts, latest_text, latest_message_type,
        latest_path_id, needs_human, last_stage
    """
    now = datetime.now(timezone.utc)

    # Subquery: max message id per thread (latest message)
    sq_max = (
        select(
            WhatsAppMessage.thread_id.label("thread_id"),
            func.max(WhatsAppMessage.id).label("max_id"),
        )
        .group_by(WhatsAppMessage.thread_id)
        .subquery("sq_max")
    )

    q = (
        select(
            WhatsAppThread.id.label("thread_id"),
            WhatsAppThread.lead_id.label("lead_id"),
            WhatsAppThread.last_message_at.label("last_message_at"),
            WhatsAppContact.display_name.label("display_name"),
            WhatsAppContact.wa_id.label("wa_id"),
            WhatsAppThread.contact_id.label("contact_id"),
            WhatsAppMessage.id.label("latest_msg_id"),
            WhatsAppMessage.direction.label("latest_direction"),
            WhatsAppMessage.timestamp.label("latest_ts"),
            WhatsAppMessage.text.label("latest_text"),
            WhatsAppMessage.message_type.label("latest_message_type"),
            WhatsAppMessage.path_id.label("latest_path_id"),
            WhatsAppThreadState.needs_human.label("needs_human"),
            WhatsAppThreadState.last_stage.label("last_stage"),
        )
        .join(WhatsAppContact, WhatsAppContact.id == WhatsAppThread.contact_id)
        .join(sq_max, sq_max.c.thread_id == WhatsAppThread.id)
        .join(WhatsAppMessage, WhatsAppMessage.id == sq_max.c.max_id)
        .outerjoin(
            WhatsAppThreadState,
            WhatsAppThreadState.thread_id == WhatsAppThread.id,
        )
        .where(WhatsAppThread.last_message_at >= cutoff)
    )

    return db.execute(q).mappings().all()


# ---------------------------------------------------------------------------
# 1. GET /api/ops/summary
# ---------------------------------------------------------------------------

@router.get("/summary")
def get_summary(
    window: str = Query("today", description="today | 24h | 7d"),
    db: Session = Depends(get_db),
) -> dict:
    if window not in ("today", "24h", "7d"):
        window = "today"

    start, now = _window_range(window)
    outbound_enabled = os.environ.get("OUTBOUND_ENABLED") == "true"

    # --- Message counts ---
    inbound_count: int = db.execute(
        select(func.count(WhatsAppMessage.id)).where(
            WhatsAppMessage.direction == "in",
            WhatsAppMessage.timestamp >= start,
            WhatsAppMessage.timestamp <= now,
        )
    ).scalar_one()

    outbound_count: int = db.execute(
        select(func.count(WhatsAppMessage.id)).where(
            WhatsAppMessage.direction == "out",
            WhatsAppMessage.automated == True,  # noqa: E712
            WhatsAppMessage.timestamp >= start,
            WhatsAppMessage.timestamp <= now,
        )
    ).scalar_one()

    blocked_count: int = db.execute(
        select(func.count(WhatsAppMessage.id)).where(
            WhatsAppMessage.direction == "out",
            WhatsAppMessage.automated == True,  # noqa: E712
            WhatsAppMessage.status == "blocked",
            WhatsAppMessage.timestamp >= start,
            WhatsAppMessage.timestamp <= now,
        )
    ).scalar_one()

    # --- Critical events ---
    critical_events_count: int = db.execute(
        select(func.count(SecurityEvent.id)).where(
            SecurityEvent.detected_at >= start,
            SecurityEvent.detected_at <= now,
        )
    ).scalar_one()

    # --- Processing failures ---
    processing_failures_count: int = db.execute(
        select(func.count(AiEvent.id)).where(
            AiEvent.status == "failed",
            AiEvent.created_at >= start,
            AiEvent.created_at <= now,
        )
    ).scalar_one()

    # --- Latency (always last 24h for unanswered, but latency uses the window) ---
    latency_rows = db.execute(
        select(AiEvent.latency_total_ms).where(
            AiEvent.latency_total_ms.is_not(None),
            AiEvent.created_at >= start,
            AiEvent.created_at <= now,
        )
    ).scalars().all()

    latency_vals: list[int] = sorted(latency_rows)
    latency_sample_count = len(latency_vals)
    latency_p50 = _percentile(latency_vals, 0.50)
    latency_p95 = _percentile(latency_vals, 0.95)
    latency_max = latency_vals[-1] if latency_vals else None

    # --- Unanswered / waiting counts (always last 24h) ---
    cutoff_24h = now - timedelta(hours=24)
    thread_rows = _query_thread_health_rows(db, cutoff_24h)

    waiting_human_count = 0
    unanswered_bot_count = 0
    waiting_customer_count = 0

    for row in thread_rows:
        health = _classify_thread_health(row["needs_human"], row["latest_direction"])
        if health == "WAITING_HUMAN":
            waiting_human_count += 1
        elif health == "UNANSWERED_BOT":
            unanswered_bot_count += 1
        elif health == "WAITING_CUSTOMER":
            waiting_customer_count += 1

    return {
        "outbound_enabled": outbound_enabled,
        "window": window,
        "inbound_count": inbound_count,
        "outbound_count": outbound_count,
        "blocked_count": blocked_count,
        "unanswered_bot_count": unanswered_bot_count,
        "waiting_human_count": waiting_human_count,
        "waiting_customer_count": waiting_customer_count,
        "critical_events_count": critical_events_count,
        "processing_failures_count": processing_failures_count,
        "latency_p50_ms": latency_p50,
        "latency_p95_ms": latency_p95,
        "latency_max_ms": latency_max,
        "latency_sample_count": latency_sample_count,
    }


# ---------------------------------------------------------------------------
# 2. GET /api/ops/messages
# ---------------------------------------------------------------------------

def _path_display(direction: str | None, path_id: str | None) -> str:
    """What the CAMINO column should read. Never a bare dash for an outbound record."""
    if direction != "out":
        return "INBOUND"
    if not path_id:
        return "UNATTRIBUTED"
    if path_id not in _KNOWN_PATHS:
        return f"UNKNOWN ({path_id})"
    return path_id


def _path_class(direction: str | None, path_id: str | None) -> str:
    """inbound | authorized | legacy | unknown — drives the badge, not the text."""
    if direction != "out":
        return "inbound"
    if not path_id or path_id not in _KNOWN_PATHS:
        return "unknown"
    if path_id in LEGACY_PATHS:
        return "legacy"
    return "authorized"


# ---------------------------------------------------------------------------
# GET /api/ops/path-registry — the legend, read from the live registry
# ---------------------------------------------------------------------------

# Owner-facing descriptions. The SET of paths is never hardcoded here: it comes from
# OutboundPathId at call time, so a path added to the registry appears automatically and
# one described here but no longer registered simply never renders.
_PATH_DOC: dict[str, dict] = {
    "CE_TEXT": {
        "label": "Respuesta de texto (motor)",
        "initiator": "ConversationEngine",
        "purpose": "Respuesta de texto generada automáticamente en la conversación.",
        "kind": "AUTOMATED",
    },
    "CE_FLOW": {
        "label": "Flow (motor)",
        "initiator": "ConversationEngine",
        "purpose": "Envío de un Flow de WhatsApp (vehículo o ubicación) por el motor.",
        "kind": "AUTOMATED",
    },
    "CE_INTERACTIVE": {
        "label": "Mensaje interactivo (motor)",
        "initiator": "ConversationEngine",
        "purpose": "Mensaje con botones interactivos.",
        "kind": "AUTOMATED",
    },
    "CE_LIST": {
        "label": "Lista (motor)",
        "initiator": "ConversationEngine",
        "purpose": "Mensaje de lista de opciones.",
        "kind": "AUTOMATED",
    },
    "MANUAL_CRM": {
        "label": "Envío manual del operador",
        "initiator": "Operador con sesión iniciada en el CRM",
        "purpose": "Mensaje escrito y enviado a mano desde el CRM.",
        "kind": "HUMAN",
    },
    "BOOKING_FLOW": {
        "label": "Flow de turno",
        "initiator": "Servicio de reservas",
        "purpose": "Flow de coordinación de turno, único camino que confirma una reserva.",
        "kind": "AUTOMATED",
    },
    "SYSTEM_NOTIFICATION": {
        "label": "Notificación del sistema",
        "initiator": "Tarea programada",
        "purpose": "Aviso automático determinista (seguimiento, recordatorio).",
        "kind": "AUTOMATED",
    },
    "LEGACY_N8N_AI_PIPELINE": {
        "label": "Camino legacy n8n (retirado)",
        "initiator": "Pipeline de IA legacy en n8n",
        "purpose": "Camino histórico. No autorizado: cualquier intento queda bloqueado.",
        "kind": "AUTOMATED",
    },
}


@router.get("/path-registry")
def get_path_registry() -> dict:
    """Every path currently registered in executable source, with its authority.

    Read from `OutboundPathId` at call time rather than from a list written here, so the
    legend cannot drift away from what the gate actually accepts.
    """
    from ..services.outbound_path_registry import OutboundPathId

    entries = []
    for member in OutboundPathId:
        pid = member.value
        doc = _PATH_DOC.get(pid, {})
        authorized = pid in AUTHORIZED_PATHS
        entries.append({
            "path_id": pid,
            "label": doc.get("label", pid),
            "initiator": doc.get("initiator", "—"),
            "purpose": doc.get("purpose", "Sin descripción registrada."),
            "kind": doc.get("kind", "AUTOMATED"),
            "authorized": authorized,
            "legacy": pid in LEGACY_PATHS,
            "authority": ("Autorizado — pasa por OutboundSafetyGate"
                          if authorized else "BLOQUEADO — no autorizado para enviar"),
        })
    entries.sort(key=lambda e: (not e["authorized"], e["path_id"]))
    return {"count": len(entries), "paths": entries}


@router.get("/messages")
def get_messages(
    window: str = Query("today", description="today | 24h | 7d"),
    direction: Optional[str] = Query(None, description="in | out"),
    thread_id: Optional[int] = Query(None),
    contact_id: Optional[int] = Query(None, description="filter to one customer"),
    path_id: Optional[str] = Query(None, description="outbound path, or UNKNOWN"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    if window not in ("today", "24h", "7d"):
        window = "today"

    start, now = _window_range(window)

    q = (
        select(
            WhatsAppMessage.id,
            WhatsAppMessage.thread_id,
            WhatsAppThread.lead_id.label("lead_id"),
            WhatsAppContact.display_name.label("display_name"),
            WhatsAppContact.wa_id.label("wa_id"),
            WhatsAppThread.contact_id.label("contact_id"),
            WhatsAppMessage.direction,
            WhatsAppMessage.message_type,
            WhatsAppMessage.text,
            WhatsAppMessage.path_id,
            WhatsAppMessage.status,
            WhatsAppMessage.blocked_reason,
            WhatsAppMessage.timestamp,
            WhatsAppMessage.wa_message_id,
            WhatsAppMessage.deployment_id,
            WhatsAppMessage.correlation_id,
            WhatsAppMessage.meta_http_status,
            WhatsAppMessage.meta_error_payload,
            AiEvent.latency_total_ms.label("latency_ms"),
        )
        .join(WhatsAppThread, WhatsAppThread.id == WhatsAppMessage.thread_id)
        .join(WhatsAppContact, WhatsAppContact.id == WhatsAppThread.contact_id)
        .outerjoin(AiEvent, AiEvent.wa_message_id == WhatsAppMessage.wa_message_id)
        .where(
            WhatsAppMessage.timestamp >= start,
            WhatsAppMessage.timestamp <= now,
        )
        .order_by(WhatsAppMessage.timestamp.desc())
        .limit(limit)
    )

    if direction in ("in", "out"):
        q = q.where(WhatsAppMessage.direction == direction)

    if thread_id is not None:
        q = q.where(WhatsAppMessage.thread_id == thread_id)

    # OPS-CONTROL: filters combine — customer AND direction AND path.
    # These endpoints are also called directly (not through FastAPI) by the certified
    # dashboard tests, where an unsupplied parameter arrives as a Query object rather
    # than None. Type-check rather than truth-check so those callers keep working.
    if isinstance(contact_id, int):
        q = q.where(WhatsAppThread.contact_id == contact_id)

    if isinstance(path_id, str) and path_id:
        if path_id.upper() == "UNKNOWN":
            # Unattributed outbound: the case that must never hide behind a dash.
            q = q.where(WhatsAppMessage.direction == "out",
                        WhatsAppMessage.path_id.is_(None))
        else:
            q = q.where(WhatsAppMessage.path_id == path_id)

    rows = db.execute(q).mappings().all()

    messages = []
    for row in rows:
        messages.append(
            {
                "id": row["id"],
                "thread_id": row["thread_id"],
                "lead_id": row["lead_id"],
                # OPS-CONTROL: the trace filters by customer, so the dropdown
                # needs a stable id that is not a phone number.
                "contact_id": row["contact_id"],
                "display_name": row["display_name"],
                "wa_id": row["wa_id"],
                "wa_id_masked": _mask_wa_id(row["wa_id"]),
                "direction": row["direction"],
                "message_type": row["message_type"],
                "text": row["text"],
                "preview": _preview(row["text"], row["message_type"]),
                "path_id": row["path_id"],
                "status": row["status"],
                "blocked_reason": row["blocked_reason"],
                "timestamp": row["timestamp"].isoformat() if row["timestamp"] else None,
                "wa_message_id": row["wa_message_id"],
                "deployment_id": row["deployment_id"],
                "correlation_id": row["correlation_id"],
                "is_path_critical": _is_path_critical(row["path_id"]),
                # OPS-CONTROL: an inbound message HAS no send path, and an outbound one
                # without a registered path is a forensic finding. The UI must be able to
                # tell those apart without inferring from a dash.
                "path_display": _path_display(row["direction"], row["path_id"]),
                "path_class": _path_class(row["direction"], row["path_id"]),
                "latency_ms": row["latency_ms"],
                "meta_http_status": row["meta_http_status"],
                "meta_error_payload": row["meta_error_payload"],
            }
        )

    return {
        "window": window,
        "count": len(messages),
        "messages": messages,
    }


# ---------------------------------------------------------------------------
# 3. GET /api/ops/threads
# ---------------------------------------------------------------------------

@router.get("/threads")
def get_threads(
    health: str = Query("all", description="all | unanswered | critical | needs_human | waiting_customer"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    now = datetime.now(timezone.utc)
    cutoff_48h = now - timedelta(hours=48)

    rows = _query_thread_health_rows(db, cutoff_48h)

    threads = []
    for row in rows:
        health_label = _classify_thread_health(row["needs_human"], row["latest_direction"])

        latest_ts: datetime | None = row["latest_ts"]
        waiting_seconds: float | None = None
        if latest_ts is not None:
            # Ensure tz-aware comparison
            if latest_ts.tzinfo is None:
                latest_ts = latest_ts.replace(tzinfo=timezone.utc)
            waiting_seconds = (now - latest_ts).total_seconds()

        tier = _age_tier(health_label, waiting_seconds)

        # Apply health filter
        if health == "unanswered" and health_label not in ("UNANSWERED_BOT", "WAITING_HUMAN"):
            continue
        elif health == "critical" and tier != "CRITICAL":
            continue
        elif health == "needs_human" and health_label != "WAITING_HUMAN":
            continue
        elif health == "waiting_customer" and health_label != "WAITING_CUSTOMER":
            continue
        # 'all' passes everything

        latest_preview = _preview(row["latest_text"], row["latest_message_type"])
        latest_ts_iso = latest_ts.isoformat() if latest_ts else None

        threads.append(
            {
                "thread_id": row["thread_id"],
                "lead_id": row["lead_id"],
                "contact_id": row["contact_id"],
                "display_name": row["display_name"],
                "wa_id_masked": _mask_wa_id(row["wa_id"]),
                "health": health_label,
                "latest_direction": row["latest_direction"],
                "latest_ts": latest_ts_iso,
                "latest_preview": latest_preview,
                "needs_human": bool(row["needs_human"]) if row["needs_human"] is not None else False,
                "last_stage": row["last_stage"],
                "waiting_seconds": int(waiting_seconds) if waiting_seconds is not None else None,
                "age_tier": tier,
                "last_path_id": row["latest_path_id"],
                "inbox_link": f"/whatsapp/thread/{row['thread_id']}",
            }
        )

        if len(threads) >= limit:
            break

    return {
        "count": len(threads),
        "threads": threads,
    }


# ---------------------------------------------------------------------------
# 4. GET /api/ops/paths
# ---------------------------------------------------------------------------

@router.get("/paths")
def get_paths(
    window: str = Query("today", description="today | 24h | 7d"),
    db: Session = Depends(get_db),
) -> dict:
    if window not in ("today", "24h", "7d"):
        window = "today"

    start, now = _window_range(window)

    # Query all automated outbound messages in window, grouped by path_id + status
    q = (
        select(
            WhatsAppMessage.path_id,
            WhatsAppMessage.status,
            func.count(WhatsAppMessage.id).label("cnt"),
        )
        .where(
            WhatsAppMessage.direction == "out",
            WhatsAppMessage.automated == True,  # noqa: E712
            WhatsAppMessage.timestamp >= start,
            WhatsAppMessage.timestamp <= now,
        )
        .group_by(WhatsAppMessage.path_id, WhatsAppMessage.status)
    )

    rows = db.execute(q).mappings().all()

    # Aggregate per path_id
    path_agg: dict[str, dict] = {}
    for row in rows:
        pid: str = row["path_id"] if row["path_id"] is not None else "UNKNOWN"
        status: str = row["status"] or ""
        cnt: int = row["cnt"]

        if pid not in path_agg:
            path_agg[pid] = {
                "count": 0,
                "success_count": 0,
                "blocked_count": 0,
                "failed_count": 0,
            }

        path_agg[pid]["count"] += cnt
        if status in ("sent", "delivered", "read"):
            path_agg[pid]["success_count"] += cnt
        elif status == "blocked":
            path_agg[pid]["blocked_count"] += cnt
        elif status == "failed":
            path_agg[pid]["failed_count"] += cnt

    paths = []
    unregistered_count = 0

    for pid, agg in path_agg.items():
        is_legacy = pid in LEGACY_PATHS
        is_authorized = pid in AUTHORIZED_PATHS
        # Legacy paths are also critical (consistent with _is_path_critical)
        is_critical = not is_authorized

        if is_critical:
            unregistered_count += agg["count"]

        paths.append(
            {
                "path_id": pid,
                "count": agg["count"],
                # The dashboard reads `total`; it was never emitted, so the TOTAL column
                # rendered a dash for every path. Same number, both names.
                "total": agg["count"],
                "success_count": agg["success_count"],
                "blocked_count": agg["blocked_count"],
                "failed_count": agg["failed_count"],
                "is_authorized": is_authorized,
                "is_legacy": is_legacy,
                "is_critical": is_critical,
            }
        )

    # Sort: authorized first, then legacy, then critical; within group by count desc
    def _sort_key(p: dict) -> tuple:
        if p["is_authorized"]:
            tier = 0
        elif p["is_legacy"]:
            tier = 1
        else:
            tier = 2
        return (tier, -p["count"])

    paths.sort(key=_sort_key)

    return {
        "window": window,
        "paths": paths,
        "unregistered_count": unregistered_count,
    }


# ---------------------------------------------------------------------------
# 5. GET /api/ops/critical-events
# ---------------------------------------------------------------------------

@router.get("/critical-events")
def get_critical_events(
    window: str = Query("today", description="today | 24h | 7d"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    if window not in ("today", "24h", "7d"):
        window = "today"

    start, now = _window_range(window)

    q = (
        select(
            SecurityEvent.id,
            SecurityEvent.detected_at,
            SecurityEvent.event_type,
            SecurityEvent.severity,
            SecurityEvent.thread_id,
            SecurityEvent.path_id,
            SecurityEvent.wamid,
        )
        .where(
            SecurityEvent.detected_at >= start,
            SecurityEvent.detected_at <= now,
        )
        .order_by(SecurityEvent.detected_at.desc())
        .limit(limit)
    )

    rows = db.execute(q).mappings().all()

    # Count total in window (may exceed limit)
    count_q = select(func.count(SecurityEvent.id)).where(
        SecurityEvent.detected_at >= start,
        SecurityEvent.detected_at <= now,
    )
    total_count: int = db.execute(count_q).scalar_one()

    events = []
    for row in rows:
        event_type: str = row["event_type"] or ""
        category = _EVENT_TYPE_TO_CATEGORY.get(event_type, "SECURITY")

        events.append(
            {
                "id": row["id"],
                "event_category": category,
                "timestamp": row["detected_at"].isoformat() if row["detected_at"] else None,
                "severity": row["severity"],
                "event_type": event_type,
                "thread_id": row["thread_id"],
                "path_id": row["path_id"],
                "wamid": row["wamid"],
                "description": _event_description(event_type, category),
            }
        )

    return {
        "window": window,
        "count": total_count,
        "events": events,
    }


def _event_description(event_type: str, category: str) -> str:
    descriptions: dict[str, str] = {
        "OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE": "Unauthorized outbound path attempt",
        "UNREGISTERED_OUTBOUND_SOURCE": "Unregistered outbound source detected",
        "LEGACY_SENDER_REACHED": "Legacy n8n AI pipeline path reached",
        "META_STATUS_FOR_UNKNOWN_WAMID": "Meta status callback for unknown WAMID",
        "SUCCESSFUL_META_SEND_WHILE_OUTBOUND_OFF": "Successful Meta send while outbound is disabled",
    }
    return descriptions.get(event_type, f"Security event: {event_type}")
