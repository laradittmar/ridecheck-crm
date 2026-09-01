from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import SessionLocal

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 3600  # every hour is sufficient for a 4-day trigger
_BUSCANDO_DAYS = 4

# NOTE: action is intentionally isolated in _send_followup() so it can be
# swapped out when the matchmaker engine is ready.
_FOLLOWUP_MSG = (
    "Hola! Te escribimos de Ridecheck 🔍 "
    "¿Seguís buscando vehículo? Cuando lo encuentres, "
    "avisanos y te ayudamos con la revisión previa a la compra."
)

_UPSERT_SQL = text("""
    INSERT INTO whatsapp_thread_states
        (thread_id, needs_human, buscando_followup_sent_at, created_at, updated_at)
    VALUES
        (:thread_id, false, NOW(), NOW(), NOW())
    ON CONFLICT (thread_id) DO UPDATE
        SET buscando_followup_sent_at = NOW(),
            updated_at = NOW()
""")

_RESET_SQL = text("""
    UPDATE whatsapp_thread_states
    SET buscando_followup_sent_at = NULL,
        updated_at = NOW()
    WHERE thread_id = :thread_id
""")


def reset_buscando_followup(db: Session, thread_id: int) -> None:
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
                    l.flag = 'BUSCANDO_AUTO'
                    AND l.buscando_auto_set_at < NOW() - INTERVAL '{_BUSCANDO_DAYS} days'
                    AND (wts.buscando_followup_sent_at IS NULL)
                    AND wc.wa_id NOT IN (SELECT phone FROM excluded_phones)
                """
            )
        ).fetchall()

        thread_ids = [r.thread_id for r in rows]
        logger.info(
            "buscando_followup wake-up: candidates=%s",
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
                        "buscando_followup gate-blocked thread_id=%s wa_id=...%s: %s",
                        thread_id, wa_id[-4:], gate_result.blocked_reason,
                    )
                    continue
                wa_mid, _ = _send_whatsapp_cloud_text(to_wa_id=wa_id, text=_FOLLOWUP_MSG)
                gate.mark_sent(gate_result.message_id, wa_mid)
                db.execute(_UPSERT_SQL, {"thread_id": thread_id})
                db.commit()
                logger.info("buscando_followup sent thread_id=%s wa_id=...%s", thread_id, wa_id[-4:])
            except Exception:
                if gate_result is not None and gate_result.outcome == GateOutcome.ALLOWED:
                    try:
                        gate.mark_failed(gate_result.message_id)
                    except Exception:
                        pass
                db.rollback()
                logger.exception("buscando_followup failed thread_id=%s", thread_id)
    except Exception:
        logger.exception("buscando_followup check query failed")
    finally:
        db.close()


async def buscando_followup_loop() -> None:
    """Background asyncio task: sends a follow-up to BUSCANDO_AUTO leads after 4 days."""
    while True:
        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)
        try:
            _run_check()
        except Exception:
            logger.exception("buscando_followup_loop unhandled error")
