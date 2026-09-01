from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import SessionLocal

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 300  # every 5 minutes
_QUOTE_SILENCE_HOURS = 24

_FOLLOWUP_MSG = (
    "Hola! Te escribimos de Ridecheck 🔍 "
    "¿Pudiste revisar el presupuesto que te enviamos? "
    "Quedamos a disposición para cualquier consulta."
)

_UPSERT_SQL = text("""
    INSERT INTO whatsapp_thread_states
        (thread_id, needs_human, quote_followup_sent_at, created_at, updated_at)
    VALUES
        (:thread_id, false, NOW(), NOW(), NOW())
    ON CONFLICT (thread_id) DO UPDATE
        SET quote_followup_sent_at = NOW(),
            updated_at = NOW()
""")

_RESET_SQL = text("""
    UPDATE whatsapp_thread_states
    SET quote_followup_sent_at = NULL,
        updated_at = NOW()
    WHERE thread_id = :thread_id
""")


def reset_quote_followup(db: Session, thread_id: int) -> None:
    """Call when client sends an inbound message so the follow-up can fire again if needed."""
    db.execute(_RESET_SQL, {"thread_id": thread_id})


def _run_check() -> None:
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                f"""
                SELECT
                    wt.id   AS thread_id,
                    wc.wa_id,
                    wts.customer_name
                FROM whatsapp_threads wt
                JOIN whatsapp_contacts wc ON wc.id = wt.contact_id
                JOIN leads l ON l.id = wt.lead_id
                LEFT JOIN whatsapp_thread_states wts ON wts.thread_id = wt.id
                WHERE
                    l.flag = 'PRESUPUESTO_ENVIADO'
                    AND (
                        SELECT direction
                        FROM whatsapp_messages wm
                        WHERE wm.thread_id = wt.id
                        ORDER BY wm.timestamp DESC
                        LIMIT 1
                    ) = 'in'
                    AND wt.last_message_at < NOW() - INTERVAL '{_QUOTE_SILENCE_HOURS} hours'
                    AND (wts.quote_followup_sent_at IS NULL)
                    AND wc.wa_id NOT IN (SELECT phone FROM excluded_phones)
                """
            )
        ).fetchall()

        thread_ids = [r.thread_id for r in rows]
        logger.info(
            "quote_followup wake-up: candidates=%s",
            thread_ids if thread_ids else "none",
        )

        for row in rows:
            thread_id: int = row.thread_id
            wa_id: str = row.wa_id
            gate_result = None
            try:
                from ..ui.whatsapp_ui import _send_whatsapp_cloud_text
                from .outbound_safety_gate import GateOutcome, OutboundSafetyGate
                from .outbound_path_registry import OutboundPathId
                gate = OutboundSafetyGate(db)
                gate_result = gate.attempt(wa_id=wa_id, thread_id=thread_id, text=_FOLLOWUP_MSG,
                                           path_id=OutboundPathId.SYSTEM_NOTIFICATION.value)
                if gate_result.outcome != GateOutcome.ALLOWED:
                    logger.info(
                        "quote_followup gate-blocked thread_id=%s wa_id=...%s: %s",
                        thread_id, wa_id[-4:], gate_result.blocked_reason,
                    )
                    continue
                wa_mid, _ = _send_whatsapp_cloud_text(to_wa_id=wa_id, text=_FOLLOWUP_MSG)
                gate.mark_sent(gate_result.message_id, wa_mid)
                db.execute(_UPSERT_SQL, {"thread_id": thread_id})
                db.commit()
                logger.info("quote_followup sent thread_id=%s wa_id=...%s", thread_id, wa_id[-4:])
            except Exception:
                if gate_result is not None and gate_result.outcome == GateOutcome.ALLOWED:
                    try:
                        gate.mark_failed(gate_result.message_id)
                    except Exception:
                        pass
                db.rollback()
                logger.exception("quote_followup failed thread_id=%s", thread_id)
    except Exception:
        logger.exception("quote_followup check query failed")
    finally:
        db.close()


async def quote_followup_loop() -> None:
    """Background asyncio task: sends a follow-up when client is silent after quote is sent."""
    while True:
        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)
        try:
            _run_check()
        except Exception:
            logger.exception("quote_followup_loop unhandled error")
