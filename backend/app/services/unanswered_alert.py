from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..settings import get_settings

logger = logging.getLogger(__name__)

_ALERT_THRESHOLD_SECONDS = 120  # escalate to ALERT after 2 minutes without a reply
_CHECK_INTERVAL_SECONDS = 60    # poll every 60 seconds

# ── Per-turn SLA alert (WILD-04R Phase 2) ────────────────────────────────────
# Queries ai_events where:
#   reply_required=true, alert_eligible=true, reply_produced is not true
#   unanswered_alert_sent_at IS NULL (no alert sent yet)
#   event older than _ALERT_THRESHOLD_SECONDS
# Updates performance_status=ALERT and unanswered_alert_sent_at to prevent repeat alerts.

_FIND_UNANSWERED_EVENTS_SQL = text("""
    SELECT
        ae.id            AS event_id,
        ae.thread_id,
        ae.wa_message_id,
        wts.customer_name,
        wc.wa_id
    FROM ai_events ae
    JOIN whatsapp_threads wt ON wt.id = ae.thread_id
    JOIN whatsapp_contacts wc ON wc.id = wt.contact_id
    LEFT JOIN whatsapp_thread_states wts ON wts.thread_id = ae.thread_id
    WHERE
        ae.reply_required = true
        AND ae.alert_eligible = true
        AND (ae.reply_produced IS NULL OR ae.reply_produced = false)
        AND ae.unanswered_alert_sent_at IS NULL
        AND ae.created_at < NOW() - INTERVAL ':threshold seconds'
        AND wc.wa_id NOT IN (SELECT phone FROM excluded_phones)
        AND (wts.needs_human IS NULL OR wts.needs_human = false)
""")

_MARK_EVENT_ALERTED_SQL = text("""
    UPDATE ai_events
    SET unanswered_alert_sent_at = NOW(),
        performance_status = 'ALERT'
    WHERE id = :event_id
""")

# ── Thread-level unanswered alert (legacy: human-handoff path) ───────────────
# Preserved for the case where needs_human=true (human took over) and
# no outbound has been sent yet.

_FIND_THREAD_UNANSWERED_SQL = text("""
    SELECT
        wt.id            AS thread_id,
        wts.customer_name,
        wc.wa_id
    FROM whatsapp_threads wt
    JOIN whatsapp_contacts wc ON wc.id = wt.contact_id
    LEFT JOIN whatsapp_thread_states wts ON wts.thread_id = wt.id
    WHERE
        wt.last_message_at < NOW() - INTERVAL ':threshold seconds'
        AND (
            SELECT direction
            FROM whatsapp_messages wm
            WHERE wm.thread_id = wt.id
            AND wm.status NOT IN ('blocked', 'failed')
            ORDER BY wm.timestamp DESC
            LIMIT 1
        ) = 'in'
        AND wc.wa_id NOT IN (SELECT phone FROM excluded_phones)
        AND (wts.unanswered_alert_sent_at IS NULL)
        AND wts.needs_human = true
""")

_UPSERT_THREAD_ALERT_SQL = text("""
    INSERT INTO whatsapp_thread_states
        (thread_id, needs_human, unanswered_alert_sent_at, created_at, updated_at)
    VALUES
        (:thread_id, false, NOW(), NOW(), NOW())
    ON CONFLICT (thread_id) DO UPDATE
        SET unanswered_alert_sent_at = NOW(),
            updated_at = NOW()
""")

_RESET_ALERT_SQL = text("""
    UPDATE whatsapp_thread_states
    SET unanswered_alert_sent_at = NULL,
        updated_at = NOW()
    WHERE thread_id = :thread_id
""")


def _send_alert_email(thread_id: int, customer_name: str, reason: str = "CE") -> None:
    """Send unanswered-thread operational alert via Resend."""
    from .resend_email import send_unanswered_alert
    s = get_settings()
    if not s.resend_api_key:
        logger.warning(
            "unanswered_alert: RESEND_API_KEY not configured, skipping email for thread_id=%s",
            thread_id,
        )
        return
    if not s.internal_booking_email_to:
        logger.warning(
            "unanswered_alert: INTERNAL_BOOKING_EMAIL_TO not configured, skipping email for thread_id=%s",
            thread_id,
        )
        return
    from_email = s.internal_booking_email_from or "notificaciones@ridecheck.ar"
    ok = send_unanswered_alert(
        api_key=s.resend_api_key,
        from_email=from_email,
        to_email=s.internal_booking_email_to,
        thread_id=thread_id,
        customer_name=customer_name,
        threshold_minutes=_ALERT_THRESHOLD_SECONDS // 60,
        reason=reason,
    )
    if not ok:
        logger.error(
            "unanswered_alert: Resend delivery failed for thread_id=%s reason=%s",
            thread_id, reason,
        )


def reset_unanswered_alert(db: Session, thread_id: int) -> None:
    """Call when an outbound message is committed so the thread-level alert can fire again."""
    db.execute(_RESET_ALERT_SQL, {"thread_id": thread_id})



# OPS-AUDIO-TRANSCRIPTION-RESILIENCE — the gap that hid three lost voice notes.
#
# The per-turn SLA check above can only see events CE has already touched: it requires
# reply_required AND alert_eligible, and those flags are written by ConversationEngine.
# On 2026-09-08 the n8n workflow died at the transcription node, so CE was never invoked,
# the flags stayed NULL, and three customer messages were invisible to alerting for hours —
# discovered only because the owner asked why nobody had answered.
#
# `status='triggered'` is exactly the missing state: the backend accepted the message and
# handed it to n8n, and nothing ever came back. No new table, no new process, no new
# threshold machinery — the same loop, the same de-duplication column.
STALLED_TRANSPORT_THRESHOLD_SECONDS = 180


def _check_forwarded_but_unfinished(db: Session) -> list[int]:
    """Alert on inbound events forwarded to n8n that CE never completed.

    Returns the event ids alerted on. Message content is never logged — thread id,
    wa_message_id and the stalled stage are enough to find the execution in n8n.
    """
    try:
        rows = db.execute(
            text(
                f"""
                SELECT ae.id AS event_id, ae.thread_id, ae.wa_message_id, wc.wa_id
                FROM ai_events ae
                JOIN whatsapp_threads wt ON wt.id = ae.thread_id
                JOIN whatsapp_contacts wc ON wc.id = wt.contact_id
                WHERE
                    ae.status = 'triggered'
                    AND ae.unanswered_alert_sent_at IS NULL
                    AND ae.created_at < NOW() - INTERVAL '{STALLED_TRANSPORT_THRESHOLD_SECONDS} seconds'
                    AND wc.wa_id NOT IN (SELECT phone FROM excluded_phones)
                ORDER BY ae.id
                """
            )
        ).fetchall()
    except Exception:
        logger.exception("stalled_transport check query failed")
        return []

    alerted: list[int] = []
    for row in rows:
        try:
            logger.error(
                "STALLED_TRANSPORT event_id=%s thread_id=%s wa_message_id=%s "
                "stage=forwarded_to_n8n_no_ce_completion age_threshold=%ds",
                row.event_id, row.thread_id, row.wa_message_id,
                STALLED_TRANSPORT_THRESHOLD_SECONDS,
            )
            db.execute(_MARK_EVENT_ALERTED_SQL, {"event_id": row.event_id})
            db.commit()
            alerted.append(int(row.event_id))
        except Exception:
            db.rollback()
            logger.exception("stalled_transport alert failed event_id=%s", row.event_id)

    if alerted:
        logger.error("STALLED_TRANSPORT_SUMMARY count=%s event_ids=%s", len(alerted), alerted)
    return alerted


def _run_check() -> None:
    db = SessionLocal()
    try:
        # ── Per-turn SLA check (WILD-04R) ──────────────────────────────────
        event_rows = db.execute(
            text(
                f"""
                SELECT
                    ae.id            AS event_id,
                    ae.thread_id,
                    ae.wa_message_id,
                    wts.customer_name,
                    wc.wa_id
                FROM ai_events ae
                JOIN whatsapp_threads wt ON wt.id = ae.thread_id
                JOIN whatsapp_contacts wc ON wc.id = wt.contact_id
                LEFT JOIN whatsapp_thread_states wts ON wts.thread_id = ae.thread_id
                WHERE
                    ae.reply_required = true
                    AND ae.alert_eligible = true
                    AND (ae.reply_produced IS NULL OR ae.reply_produced = false)
                    AND ae.unanswered_alert_sent_at IS NULL
                    AND ae.created_at < NOW() - INTERVAL '{_ALERT_THRESHOLD_SECONDS} seconds'
                    AND wc.wa_id NOT IN (SELECT phone FROM excluded_phones)
                    AND (wts.needs_human IS NULL OR wts.needs_human = false)
                """
            )
        ).fetchall()

        event_ids = [r.event_id for r in event_rows]
        logger.info(
            "unanswered_alert wake-up: threshold=%ds, event_candidates=%s",
            _ALERT_THRESHOLD_SECONDS,
            event_ids if event_ids else "none",
        )

        _check_forwarded_but_unfinished(db)

        for row in event_rows:
            event_id: int = row.event_id
            thread_id: int = row.thread_id
            customer_name: str = row.customer_name or "desconocido"
            try:
                logger.warning(
                    "unanswered_alert: AiEvent #%s thread #%s de %s sin respuesta >%ds",
                    event_id, thread_id, customer_name, _ALERT_THRESHOLD_SECONDS,
                )
                _send_alert_email(thread_id, customer_name, reason="CE-SLA")
                db.execute(_MARK_EVENT_ALERTED_SQL, {"event_id": event_id})
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("unanswered_alert event failed event_id=%s", event_id)

        # ── Thread-level human-handoff alert (legacy path preserved) ───────
        thread_rows = db.execute(
            text(
                f"""
                SELECT
                    wt.id            AS thread_id,
                    wts.customer_name,
                    wc.wa_id
                FROM whatsapp_threads wt
                JOIN whatsapp_contacts wc ON wc.id = wt.contact_id
                LEFT JOIN whatsapp_thread_states wts ON wts.thread_id = wt.id
                WHERE
                    wt.last_message_at < NOW() - INTERVAL '{_ALERT_THRESHOLD_SECONDS} seconds'
                    AND (
                        SELECT direction
                        FROM whatsapp_messages wm
                        WHERE wm.thread_id = wt.id
                        AND wm.status NOT IN ('blocked', 'failed')
                        ORDER BY wm.timestamp DESC
                        LIMIT 1
                    ) = 'in'
                    AND wc.wa_id NOT IN (SELECT phone FROM excluded_phones)
                    AND (wts.unanswered_alert_sent_at IS NULL)
                    AND wts.needs_human = true
                """
            )
        ).fetchall()

        for row in thread_rows:
            thread_id = row.thread_id
            customer_name = row.customer_name or "desconocido"
            try:
                logger.warning(
                    "unanswered_alert: Thread #%s de %s (human handoff) sin respuesta >%ds",
                    thread_id, customer_name, _ALERT_THRESHOLD_SECONDS,
                )
                _send_alert_email(thread_id, customer_name, reason="HUMAN")
                db.execute(_UPSERT_THREAD_ALERT_SQL, {"thread_id": thread_id})
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("unanswered_alert thread failed thread_id=%s", thread_id)

    except Exception:
        logger.exception("unanswered_alert check query failed")
    finally:
        db.close()


async def unanswered_alert_loop() -> None:
    """Background asyncio task: checks every 60 seconds for unanswered CE turns."""
    while True:
        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(_run_check)
        except Exception:
            logger.exception("unanswered_alert_loop unhandled error")
