from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import SessionLocal

logger = logging.getLogger(__name__)

_ALERT_TO_WA_ID = "5491153368330"
_UNANSWERED_MINUTES = 1  # TODO: restore to 6 after testing
_CHECK_INTERVAL_SECONDS = 60

_FIND_UNANSWERED_SQL = text("""
    SELECT
        wt.id            AS thread_id,
        wts.customer_name,
        wc.wa_id
    FROM whatsapp_threads wt
    JOIN whatsapp_contacts wc ON wc.id = wt.contact_id
    LEFT JOIN whatsapp_thread_states wts ON wts.thread_id = wt.id
    WHERE
        wt.last_message_at < NOW() - INTERVAL ':minutes minutes'
        AND (
            SELECT direction
            FROM whatsapp_messages wm
            WHERE wm.thread_id = wt.id
            ORDER BY wm.timestamp DESC
            LIMIT 1
        ) = 'in'
        AND wc.wa_id NOT IN (SELECT phone FROM excluded_phones)
        AND (wts.unanswered_alert_sent_at IS NULL)
""")

_UPSERT_ALERT_SQL = text("""
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


def reset_unanswered_alert(db: Session, thread_id: int) -> None:
    """Call when an outbound message is committed so the alert can fire again next cycle."""
    db.execute(_RESET_ALERT_SQL, {"thread_id": thread_id})


def _run_check() -> None:
    db = SessionLocal()
    try:
        rows = db.execute(
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
                    wt.last_message_at < NOW() - INTERVAL '{_UNANSWERED_MINUTES} minutes'
                    AND (
                        SELECT direction
                        FROM whatsapp_messages wm
                        WHERE wm.thread_id = wt.id
                        ORDER BY wm.timestamp DESC
                        LIMIT 1
                    ) = 'in'
                    AND wc.wa_id NOT IN (SELECT phone FROM excluded_phones)
                    AND (wts.unanswered_alert_sent_at IS NULL)
                    AND (wts.needs_human IS NULL OR wts.needs_human = false)
                    AND (
                        wt.lead_id IS NULL
                        OR EXISTS (
                            SELECT 1 FROM leads l
                            WHERE l.id = wt.lead_id
                            AND l.estado = 'CONSULTA_NUEVA'
                        )
                    )
                """
            )
        ).fetchall()

        thread_ids = [r.thread_id for r in rows]
        logger.info(
            "unanswered_alert wake-up: threshold=%dm, candidates=%s",
            _UNANSWERED_MINUTES,
            thread_ids if thread_ids else "none",
        )

        for row in rows:
            thread_id: int = row.thread_id
            customer_name: str = row.customer_name or "desconocido"
            try:
                from ..ui.whatsapp_ui import _send_whatsapp_cloud_text
                msg = (
                    f"Hilo #{thread_id} de {customer_name} "
                    f"lleva más de {_UNANSWERED_MINUTES} minutos sin respuesta."
                )
                _send_whatsapp_cloud_text(to_wa_id=_ALERT_TO_WA_ID, text=msg)
                db.execute(_UPSERT_ALERT_SQL, {"thread_id": thread_id})
                db.commit()
                logger.info("unanswered_alert sent thread_id=%s customer=%s", thread_id, customer_name)
            except Exception:
                db.rollback()
                logger.exception("unanswered_alert failed thread_id=%s", thread_id)
    except Exception:
        logger.exception("unanswered_alert check query failed")
    finally:
        db.close()


async def unanswered_alert_loop() -> None:
    """Background asyncio task: checks every minute for unanswered threads."""
    while True:
        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)
        try:
            _run_check()
        except Exception:
            logger.exception("unanswered_alert_loop unhandled error")
