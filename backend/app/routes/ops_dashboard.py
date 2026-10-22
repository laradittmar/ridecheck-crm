"""Operational observability dashboard API — read-only.

All endpoints are SELECT-only. No INSERT/UPDATE/DELETE.

Prefix: /api/ops
Tag: ops

Access: every route requires a CRM session or the machine credential, enforced for the
whole prefix by `main.auth_middleware` (PW1-R1); some routes additionally require the
session alone (`require_crm_session`).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import Integer, case, func, select, text
from sqlalchemy.orm import Session

from ..auth import SESSION_COOKIE, verify_session
from ..db import get_db
from ..models import (
    AiEvent,
    HybridDecisionTraceRow,
    SecurityEvent,
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppThread,
    WhatsAppThreadState,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ops", tags=["ops"])


def require_crm_session(request: Request) -> str:
    """The same signed CRM session `/control` requires. Returns the operator's email.

    Applied to the customer-specific forensic endpoints (traces, offers, booking attention).
    Pre-Wild PW1-R1: the whole of `/api/ops` is also behind `main.auth_middleware`'s
    OPERATIONS class (CRM session or machine credential). That gate runs first and adds
    nothing here — a machine caller it admits is still refused by this dependency.

    The check runs before any database access, so a caller without a session gets the same
    401 for a turn that exists, a turn that does not, and a malformed id. Whether a trace
    exists is itself information, and an unauthenticated caller does not get it.
    """
    payload = verify_session(request.cookies.get(SESSION_COOKIE))
    email = (payload or {}).get("email") or ""
    if not email:
        logger.warning("HYBRID_TRACE_UNAUTHENTICATED path=%s method=%s",
                       request.url.path, request.method)
        raise HTTPException(status_code=401, detail="Unauthorized")
    return email

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
    "OPERATOR_NOTIFICATION",
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
    "OPERATOR_NOTIFICATION": {
        "label": "Aviso al operador",
        "initiator": "Servicio de avisos de reservas",
        "purpose": ("Aviso por WhatsApp (plantilla aprobada) al número del operador "
                    "configurado, uno por reserva confirmada. Nunca a un cliente."),
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


# ---------------------------------------------------------------------------
# L4.7W5 Gate 1/2 — hybrid decision trace (read-only, like everything else here)
# ---------------------------------------------------------------------------

# The message BODIES are deliberately not served here. The trace stores the ordered WAMIDs
# and the hash of the normalized burst; `/api/ops/messages` already serves the text under
# the existing preview and masking policy. One source for customer words, not two.
TRACE_NOT_CAPTURED = "TRACE NOT CAPTURED — predates hybrid-decision-trace/1.0"


def _trace_row_summary(row: HybridDecisionTraceRow) -> dict:
    """One turn, carrying BOTH classifications, each named for what it is.

    `captured_classification` is the value stored when the trace was written and is never
    rewritten — it is forensic evidence, including when the writer got the label wrong.
    `effective_classification` is derived at read time from the same reconciliation rows
    under the current rules, and is what an operator should be shown. For a trace written
    after L4.7W5-HYBRID-TRACE-LABEL-TRUTH the two agree.

    `classification` is retained for compatibility with existing consumers and is the
    **captured** value. It is documented here and in the API docstrings so nothing has to
    guess which of the two it means.
    """
    from ..services.hybrid_trace import effective_from_payload

    effective, conditions = effective_from_payload(row.payload)
    payload = row.payload if isinstance(row.payload, dict) else {}
    return {
        # Which contract wrote this row. A 1.0 trace carries no per-row provenance, so an
        # operator reading `effective_classification` needs to know it was derived under
        # the legacy rule rather than recomputed from recorded participation.
        "contract_version": payload.get("trace_version"),
        "turn_id": row.turn_id,
        "thread_id": row.thread_id,
        "lead_id": row.lead_id,
        "deployment_sha": row.deployment_sha,
        "input_hash": row.input_hash,
        "message_count": row.message_count,
        "result_kind": row.result_kind,
        "classification": row.classification,          # == captured_classification
        "captured_classification": row.classification,
        "effective_classification": effective if effective is not None else row.classification,
        "reclassified": bool(effective is not None and effective != row.classification),
        "supporting_conditions": list(conditions),
        "semantic_status": row.semantic_status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/turns")
def get_turns(
    thread_id: Optional[int] = Query(None),
    result_kind: Optional[str] = Query(None),
    classification: Optional[str] = Query(
        None, description="filters the CAPTURED classification stored on the row"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _operator: str = Depends(require_crm_session),
) -> dict:
    """Recent captured turns, newest first. Absence of a turn means it was not captured."""
    q = select(HybridDecisionTraceRow).order_by(HybridDecisionTraceRow.id.desc()).limit(limit)
    if thread_id is not None:
        q = q.where(HybridDecisionTraceRow.thread_id == thread_id)
    if result_kind:
        q = q.where(HybridDecisionTraceRow.result_kind == result_kind)
    if classification:
        q = q.where(HybridDecisionTraceRow.classification == classification)
    rows = list(db.execute(q).scalars().all())
    return {"count": len(rows), "turns": [_trace_row_summary(r) for r in rows]}


#: G3-7R/S9. The columns an operator may read off an offer, and the ONLY ones. The raw
#: token does not exist in the database to be selected.
#:
#: R2/RV-05. `token_fingerprint` is NOT on this list any more. The HTML page truncated it
#: to twelve characters with a stated rationale — a fingerprint is a matching key, not
#: something an operator reads in full — and the JSON endpoint returned all 64, so the
#: surface that shares this accessor contradicted the rationale that justified including
#: the column. The prefix is derived below instead, so neither surface can emit the whole
#: value and a future reader cannot reintroduce it by reading the field list alone.
_OFFER_FIELDS = (
    "id", "thread_id", "cycle_key", "candidate_id", "tipo_vehiculo", "zone_group",
    "zone_detail", "offered_date", "offered_time", "flow_id", "contract_version",
    "status", "issued_at", "expires_at", "terminal_at", "accepted_at",
    "terminal_reason", "refused_count", "last_refusal", "last_refused_at",
    "replay_count", "last_replay_at", "accepted_revision_id",
    "accepted_thread_revision_id",
)

#: How much of `sha256(token)` an operator needs: enough to tell a thread's handful of
#: offers apart and to match a log line, and no more. Twelve hex characters is what the
#: page already rendered.
OFFER_FINGERPRINT_PREFIX_LEN = 12


def _offer_dict(row) -> dict:
    """One offer row as a plain dict, field by declared field.

    Explicit rather than reflective on purpose: a column added to the table later must be
    chosen for this surface, not inherited by it. That is what keeps a future column
    carrying something private from appearing on an operator page by default.

    The token fingerprint appears only as `token_fingerprint_prefix` — the minimum needed
    for correlation (R2/RV-05).
    """
    out = {name: getattr(row, name, None) for name in _OFFER_FIELDS}
    raw = getattr(row, "token_fingerprint", None)
    out["token_fingerprint_prefix"] = (
        str(raw)[:OFFER_FINGERPRINT_PREFIX_LEN] if raw else None)
    return out


def read_offers(db: Session, *, thread_id: Optional[int] = None,
                limit: int = 200) -> list:
    """Every booking offer, newest first — or one thread's, when a thread is named.

    S9. The single accessor behind both the `/control/offers` page and
    `/api/ops/offers`, so the page and the API cannot tell different stories about the same
    offer. It carries no authorization of its own: `/control` is a protected prefix and the
    endpoint adds its own session check.
    """
    from ..models import WhatsAppBookingOffer

    stmt = select(WhatsAppBookingOffer)
    if thread_id is not None:
        stmt = stmt.where(WhatsAppBookingOffer.thread_id == int(thread_id))
    stmt = stmt.order_by(WhatsAppBookingOffer.id.desc()).limit(int(limit))
    return [_offer_dict(r) for r in db.execute(stmt).scalars().all()]


def read_offer(offer_id: int, db: Session) -> Optional[dict]:
    """One offer, or None. None is rendered as absence, never as an empty offer."""
    from ..models import WhatsAppBookingOffer

    row = db.get(WhatsAppBookingOffer, int(offer_id))
    return _offer_dict(row) if row is not None else None


def read_turn(turn_id: str, db: Session) -> dict:
    """The full trace for one turn — the single accessor.

    Both the JSON endpoint and the `/control/turn/{turn_id}` page read through this, so the
    two can never tell different stories about the same turn. It carries no authorization
    of its own: the endpoint below adds the session check, and the page is already behind
    the `/control` boundary.

    A turn with no row is reported as not captured rather than as an empty decision: an
    inspector that renders absence as "nothing happened" is worse than one that renders
    nothing at all, because it invents evidence.
    """
    row = db.execute(
        select(HybridDecisionTraceRow).where(HybridDecisionTraceRow.turn_id == turn_id)
    ).scalar_one_or_none()
    if row is None:
        return {"turn_id": turn_id, "captured": False, "reason": TRACE_NOT_CAPTURED}
    from ..services.hybrid_trace import counts_from_payload, row_summaries_from_payload

    out = _trace_row_summary(row)
    out.update({
        "captured": True,
        "trace": row.payload,
        # The reconciliation table, with captured and effective carried SEPARATELY on every
        # row. Derived here rather than in the renderer so the JSON consumer and the page
        # cannot tell different stories about the same reconciliation.
        "reconciliation_rows": list(row_summaries_from_payload(row.payload)),
        # Row counts are row counts and family counts deduplicate. "2 of 2 families" was
        # neither, and it was printed beside a claim of agreement.
        "reconciliation_counts": counts_from_payload(row.payload),
    })
    return out


@router.get("/turn/{turn_id}")
def get_turn(turn_id: str, db: Session = Depends(get_db),
             _operator: str = Depends(require_crm_session)) -> dict:
    return read_turn(turn_id, db)


# ── G3-7R/S9: the booking-offer authority, as JSON ───────────────────────────
#
# Behind `require_crm_session`, the same signed CRM session `/control` requires, for the
# same reason the hybrid-trace endpoints are: whether an offer exists at all is itself
# information about a customer, and an unauthenticated caller does not get it. The check
# runs before any database access, so a caller without a session gets the same 401 for an
# offer that exists, one that does not, and a malformed id.

@router.get("/offers")
def get_offers(thread_id: Optional[int] = Query(None),
               limit: int = Query(200, ge=1, le=1000),
               db: Session = Depends(get_db),
               _operator: str = Depends(require_crm_session)) -> dict:
    rows = read_offers(db, thread_id=thread_id, limit=limit)
    return {"count": len(rows), "thread_id": thread_id, "offers": rows}


@router.get("/offer/{offer_id}")
def get_offer(offer_id: int, db: Session = Depends(get_db),
              _operator: str = Depends(require_crm_session)) -> dict:
    row = read_offer(offer_id, db)
    if row is None:
        # Absence is reported as absence. An empty offer object would read like an offer
        # that exists and decided nothing.
        raise HTTPException(status_code=404, detail="offer not found")
    return row


# ---------------------------------------------------------------------------
# SB2B Option B — booking ledger rows that need a person (D1-05, §8.5)
# ---------------------------------------------------------------------------

#: How far back the CE-synchronization check looks. Older rows have long been triaged.
BOOKING_SYNC_LOOKBACK_DAYS = 7
#: The recovery each reason calls for. Never automatic: an operator performs it.
_BOOKING_RECOVERY = {
    "CE_SYNC_INCOMPLETE": "STORED_INBOUND_REPLAY",
    "ACK_PENDING_STALE": "LEDGER_ACK_RECOVERY",
    "ACK_FAILED": "LEDGER_ACK_RECOVERY",
    # Phase 3-R1: a lost sender is UNCERTAIN — held, never resent; a person decides.
    "ACK_LEASE_GONE": "OPERATOR_REVIEW",
    "ACK_AMBIGUOUS": "OPERATOR_REVIEW",
    "ACK_DUPLICATE_RISK": "OPERATOR_REVIEW",
    "ACK_HELD": "OPERATOR_REVIEW",
    "IDENTITY_CONFLICT": "OPERATOR_REVIEW",
    # Convergence Phase 4 (SB2B-RV-04): the booking stands; the team was not told by e-mail.
    "OPERATOR_NOTIFICATION_PENDING": "NOTIFY_TEAM_MANUALLY",
    "OPERATOR_NOTIFICATION_FAILED": "NOTIFY_TEAM_MANUALLY",
    # W1-F2: the operator's WhatsApp notice FAILED twice or is HELD (not configured, refused,
    # or uncertain — never repeated automatically). The booking stands.
    "OPERATOR_NOTIFICATION_REQUIRED": "NOTIFY_TEAM_MANUALLY",
}
_RECOVERY_RANK = ("OPERATOR_REVIEW", "STORED_INBOUND_REPLAY", "LEDGER_ACK_RECOVERY",
                  "NOTIFY_TEAM_MANUALLY")
#: Customer booking sources whose committed booking is owed an operator e-mail.
_NOTIFIED_SOURCES = ("CONVERSATION_ENGINE", "WHATSAPP_FLOW")


def _inbound_correlation(wamid: str) -> str:
    import hashlib
    return "in:" + hashlib.sha256(wamid.encode()).hexdigest()[:32]


def _lease_live(r: dict, now: datetime) -> bool:
    """A row from the authority's view carries `ack_lease_live` (database clock); a row added
    here for CE synchronization carries the raw expiry instead."""
    if "ack_lease_live" in r:
        return bool(r["ack_lease_live"])
    exp = r.get("ack_lease_expires_at")
    if exp is None:
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp >= now


def booking_attention(db: Session, *, now: Optional[datetime] = None,
                      pending_older_than_seconds: Optional[int] = None) -> list[dict]:
    """Ledger rows needing attention, reduced to what an operator may see.

    Shown: the key's NAMESPACE and an 8-hex prefix of its hash (a key never contains a Flow
    token), ids that find the thread, ack state and error CATEGORY, the last 6 characters of
    the acknowledgement's WAMID, the identity outcome and the reasons. Never shown: raw
    tokens, full WAMIDs, names, phones, e-mails or message text.

    Convergence Phase 3-R1 — every row also states, from durable fields only:
    `booking` (COMMITTED: the booking stands and must not be recreated), `ack_state` /
    `ack_delivery` (`booking_authority.AckDelivery`: the row's state and the last classified
    outcome — e.g. HELD_OPERATOR / UNCERTAIN), `automatic_resend` (NONE_OWED, PERMITTED,
    IN_FLIGHT or HELD), `ack_attempts`, and `ack_identity` — a 20-character prefix of the
    command-bound delivery identity, whose per-attempt fingerprints are the outbound rows'
    `content_fingerprint` (`/security/outbound-ledger?fingerprint=`).

    Convergence Phase 4 (SB2B-RV-04) — the OPERATOR notification (the team's booking e-mail),
    kept distinct from the customer acknowledgement above. Every customer booking row also
    states `notification_state`: SENT (`thread_revisions.notification_sent_at` — declared schema
    since Phase 4-R1, migration 20261016_notification_sent_at), FAILED or
    HELD (a `booking_attempt_events` NOTIFICATION row: send failed / not configured), or
    PENDING (no e-mail recorded — a Data Exchange booking, or one whose sender died); with
    `notification_attempts`, `notification_last_reason` and `notification_next_action`. A
    committed customer booking whose e-mail is not SENT once `pending_older_than_seconds` has
    passed is listed with `OPERATOR_NOTIFICATION_PENDING` / `_FAILED`. The next action is to
    tell the team by hand: the booking itself stands and is never recreated from here.

    W1-F2 — a booking that has an `operator_notifications` row (every customer booking
    committed after migration 20261022) reports THAT notice instead, with
    `notification_channel: WHATSAPP` and its own status (PENDING, SENDING, SENT, FAILED, HELD,
    or DELIVERY_FAILED: sent, then reported `failed` by Meta's status callback — never resent).
    FAILED, HELD or DELIVERY_FAILED is listed at once with `OPERATOR_NOTIFICATION_REQUIRED`;
    PENDING or SENDING after the settle window with `OPERATOR_NOTIFICATION_PENDING`.

    `CE_SYNC_INCOMPLETE` is detectable for Conversation-Engine rows only: their
    `correlation_id` names the causal inbound (`in:<sha256>`), so the inbound's `ai_event`
    tells whether CE finished the turn. A Data Exchange row's CE synchronization happens when
    its Flow reply reaches CE and is not linkable from the ledger.
    """
    from ..models import BookingCommandRecord as R
    from ..services.booking_authority import (ACK_PENDING_ATTENTION_SECONDS, BookingAuthority,
                                              acknowledgement_delivery_identity,
                                              acknowledgement_delivery_state)

    now = now or datetime.now(timezone.utc)
    age = (ACK_PENDING_ATTENTION_SECONDS if pending_older_than_seconds is None
           else int(pending_older_than_seconds))
    rows = {r["id"]: r for r in BookingAuthority(db).ledger_rows_needing_attention(
        pending_older_than_seconds=age)}

    ce_rows = db.execute(
        select(R.id, R.idempotency_key, R.source, R.operation, R.revision_id,
               R.thread_revision_id, R.thread_id, R.lead_id, R.occupying, R.committed_at,
               R.correlation_id, R.ack_status, R.ack_attempts, R.ack_duplicate_risk,
               R.ack_last_error, R.ack_transport_id, R.identity_outcome,
               R.ack_lease_expires_at)
        .where(R.source == "CONVERSATION_ENGINE", R.correlation_id.like("in:%"),
               R.command_state == "COMMITTED",       # a DETACHED row has no turn to finish
               R.committed_at >= now - timedelta(days=BOOKING_SYNC_LOOKBACK_DAYS))
    ).all()
    settled = now - timedelta(seconds=age)
    threads = {r.thread_id for r in ce_rows if r.thread_id is not None}
    events: dict[str, tuple] = {}
    if threads:
        for ev in db.execute(select(AiEvent.wa_message_id, AiEvent.status, AiEvent.action)
                             .where(AiEvent.thread_id.in_(threads))).all():
            events[_inbound_correlation(ev.wa_message_id)] = (ev.status, ev.action)
    for r in ce_rows:
        committed = r.committed_at
        if committed is not None and committed.tzinfo is None:
            committed = committed.replace(tzinfo=timezone.utc)
        if committed is not None and committed > settled:
            continue                                   # the turn may still be running
        status, action = events.get(r.correlation_id, (None, None))
        if status == "processed" and action != "error":
            continue
        row = rows.setdefault(r.id, {**dict(r._mapping), "reasons": []})
        row["reasons"].append("CE_SYNC_INCOMPLETE")

    notification = _notification_truth(db, now=now, settled=settled, rows=rows)

    out = []
    for r in sorted(rows.values(), key=lambda x: x["id"]):
        kind, _, digest = (r.get("idempotency_key") or "").partition(":")
        recoveries = {_BOOKING_RECOVERY.get(x, "OPERATOR_REVIEW") for x in r["reasons"]}
        transport = r.get("ack_transport_id") or ""
        committed = r.get("committed_at")
        state, delivery, resend = acknowledgement_delivery_state(
            ack_status=r.get("ack_status"), ack_attempts=r.get("ack_attempts"),
            ack_duplicate_risk=r.get("ack_duplicate_risk"),
            ack_last_error=r.get("ack_last_error"), lease_live=_lease_live(r, now))
        key = r.get("idempotency_key")
        out.append({
            "id": r["id"],
            "key_kind": kind or None,
            "key_hash_prefix": digest[:8] or None,
            "source": r.get("source"),
            "operation": r.get("operation"),
            "revision_id": r.get("revision_id"),
            "thread_revision_id": r.get("thread_revision_id"),
            "thread_id": r.get("thread_id"),
            "lead_id": r.get("lead_id"),
            "occupying": r.get("occupying"),
            "committed_at": committed.isoformat() if committed else None,
            "booking": "COMMITTED",
            "ack_status": r.get("ack_status"),
            "ack_state": state.value,
            "ack_delivery": delivery.value,
            "automatic_resend": resend,
            "ack_identity": (acknowledgement_delivery_identity(key)[:20] if key else None),
            "ack_attempts": r.get("ack_attempts"),
            "ack_duplicate_risk": bool(r.get("ack_duplicate_risk")),
            "ack_error_category": r.get("ack_last_error"),
            "ack_transport_tail": transport[-6:] if transport else None,
            "identity_outcome": r.get("identity_outcome"),
            **notification.get(r["id"], _NO_NOTIFICATION),
            "reasons": list(r["reasons"]),
            "recovery": next(x for x in _RECOVERY_RANK if x in recoveries),
            "thread_url": (f"/whatsapp/thread/{r['thread_id']}"
                           if r.get("thread_id") is not None else None),
        })
    return out


_NO_NOTIFICATION = {"notification_state": "NOT_APPLICABLE", "notification_attempts": 0,
                    "notification_last_reason": None, "notification_next_action": None}


def _notification_truth(db: Session, *, now: datetime, settled: datetime, rows: dict) -> dict:
    """SB2B-RV-04: the operator e-mail state of every customer booking in the lookback, read
    from durable fields only (no write). Adds an OPERATOR_NOTIFICATION_* reason, and the row,
    to `rows` when a settled booking's e-mail is not SENT. Returns {ledger id: fields}."""
    from ..models import BookingAttemptEvent as E
    from ..models import BookingCommandRecord as R
    booked = db.execute(
        select(R.id, R.idempotency_key, R.source, R.operation, R.revision_id,
               R.thread_revision_id, R.thread_id, R.lead_id, R.occupying, R.committed_at,
               R.correlation_id, R.ack_status, R.ack_attempts, R.ack_duplicate_risk,
               R.ack_last_error, R.ack_transport_id, R.identity_outcome,
               R.ack_lease_expires_at)
        .where(R.command_state == "COMMITTED", R.occupying.is_(True),
               R.source.in_(_NOTIFIED_SOURCES), R.thread_revision_id.is_not(None),
               R.committed_at >= now - timedelta(days=BOOKING_SYNC_LOOKBACK_DAYS))
    ).all()
    if not booked:
        return {}
    # Phase 4-R1 (P4RV-04): `notification_sent_at` is declared schema (model + migration
    # 20261016_notification_sent_at), read like any other column. The old "absent column →
    # nothing SENT" fallback made every booking read "no enviado" on a migrated database.
    from ..models import ThreadRevision as TR
    sent = set(db.execute(select(TR.id).where(
        TR.notification_sent_at.is_not(None),
        TR.id.in_([b.thread_revision_id for b in booked]))).scalars().all())
    evidence: dict[int, list] = {}
    for ev in db.execute(select(E.hist_thread_revision_id, E.outcome, E.reason, E.occurred_at)
                         .where(E.stage == "NOTIFICATION",
                                E.hist_thread_revision_id.in_(
                                    [b.thread_revision_id for b in booked]))
                         .order_by(E.occurred_at, E.id)).all():
        evidence.setdefault(ev.hist_thread_revision_id, []).append(ev)
    # W1-F2: where the operator's WhatsApp notice exists it is the notification truth (owner
    # decision: the professional is told on WhatsApp; an e-mail never stands in for it).
    # Bookings without one — committed before migration 20261022 — keep the e-mail truth.
    from ..models import OperatorNotification as ON
    whatsapp = {n.booking_command_id: n for n in db.execute(
        select(ON.booking_command_id, ON.status, ON.attempts, ON.last_error,
               WhatsAppMessage.status.label("delivery"))
        .outerjoin(WhatsAppMessage, WhatsAppMessage.id == ON.outbound_message_id)
        .where(ON.booking_command_id.in_([b.id for b in booked]))).all()}
    out = {}
    for b in booked:
        notice = whatsapp.get(b.id)
        if notice is not None:
            # Meta's own later `failed` status for a notice it had accepted: shown, never
            # resent — a provider callback never creates a second notice.
            state = ("DELIVERY_FAILED" if notice.status == "SENT" and notice.delivery == "failed"
                     else notice.status)
            required = state in ("FAILED", "HELD", "DELIVERY_FAILED")
            out[b.id] = {
                "notification_state": state,
                "notification_channel": "WHATSAPP",
                "notification_attempts": int(notice.attempts),
                "notification_last_reason": notice.last_error,
                "notification_next_action": (None if state == "SENT"
                                             else "NOTIFY_TEAM_MANUALLY"),
            }
            committed = b.committed_at
            if committed is not None and committed.tzinfo is None:
                committed = committed.replace(tzinfo=timezone.utc)
            if required or (state != "SENT"
                            and (committed is None or committed <= settled)):
                row = rows.setdefault(b.id, {**dict(b._mapping), "reasons": []})
                row["reasons"].append("OPERATOR_NOTIFICATION_REQUIRED" if required
                                      else "OPERATOR_NOTIFICATION_PENDING")
            continue
        evs = evidence.get(b.thread_revision_id, [])
        if b.thread_revision_id in sent:
            state = "SENT"
        elif evs:
            state = "FAILED" if any(e.outcome == "FAILED" for e in evs) else "HELD"
        else:
            state = "PENDING"
        out[b.id] = {
            "notification_state": state,
            "notification_attempts": len(evs) + (1 if state == "SENT" else 0),
            "notification_last_reason": evs[-1].reason if evs else None,
            "notification_next_action": (None if state == "SENT" else "NOTIFY_TEAM_MANUALLY"),
        }
        committed = b.committed_at
        if committed is not None and committed.tzinfo is None:
            committed = committed.replace(tzinfo=timezone.utc)
        if state != "SENT" and (state == "FAILED" or committed is None or committed <= settled):
            row = rows.setdefault(b.id, {**dict(b._mapping), "reasons": []})
            row["reasons"].append("OPERATOR_NOTIFICATION_FAILED" if state == "FAILED"
                                  else "OPERATOR_NOTIFICATION_PENDING")
    return out


@router.get("/booking-attention")
def get_booking_attention(
    db: Session = Depends(get_db),
    _operator: str = Depends(require_crm_session),
) -> dict:
    """Booking ledger rows needing a person: stale or failed acknowledgements, ambiguous
    transport, duplicate risk, identity conflicts, CE synchronizations that never finished,
    and (convergence Phase 4) committed customer bookings whose operator e-mail was not sent.
    Pre-Wild PW1-B: `operational_conditions` lists lost booking evidence and booking I/O-guard
    trips on the e-mail path. Read-only, behind the same signed CRM session as `/control`."""
    rows = booking_attention(db)
    return {"count": len(rows), "rows": rows,
            "operational_conditions": operational_conditions(db)}


#: What an operational condition may show of its `details` (PW1-B): typed codes and
#: historical ids, the only keys its writers put there. Anything else is dropped.
_CONDITION_DETAIL_KEYS = ("source", "stage", "outcome", "reason", "error_class", "channel",
                          "sender", "operation", "hist_offer_id", "hist_revision_id",
                          "hist_thread_revision_id")


def operational_conditions(db: Session, *, now: Optional[datetime] = None) -> list[dict]:
    """Pre-Wild PW1-B (P4RV-09): failures of OBSERVABILITY an operator must see — booking
    evidence that could not be written, and an e-mail sender that tripped the booking I/O
    guard (`security_events.OPERATIONAL_CONDITION_TYPES`), newest first, in the booking
    lookback. Read-only and outside every booking decision; listed beside, never merged into,
    the ledger rows above. The read runs under a SAVEPOINT, so a database without the table
    yields an empty list and a usable session."""
    from ..services.security_events import OPERATIONAL_CONDITION_TYPES
    now = now or datetime.now(timezone.utc)
    try:
        with db.begin_nested():
            found = db.execute(
                select(SecurityEvent.id, SecurityEvent.detected_at, SecurityEvent.event_type,
                       SecurityEvent.severity, SecurityEvent.thread_id,
                       SecurityEvent.source_component, SecurityEvent.details)
                .where(SecurityEvent.event_type.in_(OPERATIONAL_CONDITION_TYPES),
                       SecurityEvent.detected_at >= now - timedelta(days=BOOKING_SYNC_LOOKBACK_DAYS))
                .order_by(SecurityEvent.detected_at.desc(), SecurityEvent.id.desc())
                .limit(200)).all()
    except Exception as exc:                       # noqa: BLE001 — a read, never a decision
        logger.warning("OPERATIONAL_CONDITIONS_UNREADABLE: %s", type(exc).__name__)
        return []
    out = []
    for r in found:
        details = r.details if isinstance(r.details, dict) else {}
        out.append({
            "id": r.id,
            "detected_at": r.detected_at.isoformat() if r.detected_at else None,
            "condition": r.event_type,
            "severity": r.severity,
            "thread_id": r.thread_id,
            "component": r.source_component,
            "details": {k: details.get(k) for k in _CONDITION_DETAIL_KEYS if k in details},
            "next_action": "OPERATOR_REVIEW",
            "thread_url": (f"/whatsapp/thread/{r.thread_id}" if r.thread_id is not None
                           else None),
        })
    return out
