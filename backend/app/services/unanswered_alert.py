from __future__ import annotations

import asyncio
import logging
from typing import Optional

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
#
# What `reply_produced` means in THIS contract (L4.7W5): it is an operational flag meaning
# "the customer-facing reply completed well enough that this turn needs no rescue" — NOT
# "an internal draft once existed". That is why `blocked_dispatch` records false: a reply
# that never reached the customer leaves the customer waiting, whatever was drafted. The
# Hybrid Inspector is where the three cases are told apart — never produced, produced but
# blocked, produced and sent — and this milestone does not change blocked-send alerting.
#   unanswered_alert_sent_at IS NULL (no alert sent yet)
#   event older than _ALERT_THRESHOLD_SECONDS
# Updates performance_status=ALERT and unanswered_alert_sent_at to prevent repeat alerts.
#
# W1F-O1: this is the ONE per-turn query, and `_run_check` executes it. Until 2026-10-22 the
# job ran its own inline copy without the R2 successor clause below, so the clause was tested
# but never ran, and answered turns were e-mailed as "sin respuesta". The threshold is a real
# bind parameter multiplied by a one-second interval — a quoted ':threshold' inside an
# INTERVAL literal is not a bind at all.

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
        AND ae.created_at < NOW() - (:threshold_seconds * INTERVAL '1 second')
        AND wc.wa_id NOT IN (SELECT phone FROM excluded_phones)
        AND (wts.needs_human IS NULL OR wts.needs_human = false)
        -- L4.7W5-R2 successor suppression. A conversation that was answered is not
        -- unanswered, even if an earlier turn in it produced nothing.
        --
        -- Correlation is by thread_id, never by phone number and never by message text:
        -- a successful reply to a different customer must not silence this one.
        --
        -- "Successful" is the transport contract's own vocabulary. `OutboundSafetyGate` is
        -- the sole writer of direction='out' rows; it inserts `pending` BEFORE calling Meta
        -- and only `mark_sent()` promotes a row to `sent` while stamping the WAMID Meta
        -- returned. Status webhooks then advance sent -> delivered -> read by precedence,
        -- or to failed. So `status IN ('sent','delivered','read')` with a WAMID present is
        -- durable proof that Meta accepted the message; `blocked`, `failed` and `pending`
        -- are each proof that it did not, and none of them suppresses.
        --
        -- `timestamp` is stamped once, when the send is ATTEMPTED, and is never moved by
        -- mark_sent or by a status webhook. `> ae.created_at` therefore means "attempted
        -- after the turn that produced nothing", which is the ordering we want: a reply
        -- already in flight before the failure does not excuse it.
        --
        -- Automated, human-CRM and Flow sends all pass through the same gate, so all three
        -- are represented here identically. No path is missing.
        AND NOT EXISTS (
            SELECT 1 FROM whatsapp_messages ob
            WHERE ob.thread_id = ae.thread_id
              AND ob.direction = 'out'
              AND ob.status IN ('sent', 'delivered', 'read')
              AND ob.wa_message_id IS NOT NULL
              AND ob.timestamp > ae.created_at
        )
""").bindparams(threshold_seconds=_ALERT_THRESHOLD_SECONDS)

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


# W1-F1 — a committed, acknowledged Flow booking is not an unanswered customer.
#
# On a Flow booking, Meta delivers the Flow's completion as an inbound `flow_response` message,
# and it can be stored AFTER the booking acknowledgement (first Wild, 2026-10-21: ack 21:27:23,
# completion 21:27:27). The booking sets `needs_human=true` by design, so the thread-level
# human-handoff query saw "latest message inbound" and e-mailed the operator a false
# "sin respuesta" at 21:30:00. That inbound IS answered: it is the same submission the booking
# authority committed and acknowledged.
#
# The decision is structural — never prose, never timing:
#   1. the thread's latest message (the one the query found inbound) is a stored Flow
#      response;
#   2. its booking-command key is the key the booking writers execute under
#      (`booking_ce_adapter.flow_booking_key` over the stored Flow token — the Data Exchange
#      endpoint's `flow:` key — or the inbound's own id), parsed from the stored payload with
#      the webhook's own rules (`booking_recovery.stored_inbound_event`);
#   3. that ledger row is a COMMITTED CREATE on THIS thread with its booking projection;
#   4. its acknowledgement is SENT, and the acknowledgement's WAMID is an outbound message on
#      THIS thread that Meta accepted (sent / delivered / read).
# Anything short of all four — another thread's booking, a refused or failed submission, an
# acknowledgement never delivered, a fallback or handoff Flow that books nothing, a customer
# writing again after the booking — leaves the alert exactly as it was.

_ACCEPTED_STATUSES = ("sent", "delivered", "read")


def latest_inbound_is_acknowledged_booking_completion(db: Session, thread_id: int) -> Optional[int]:
    """The booking command whose committed, acknowledged booking the thread's latest message
    completes, or None. Read-only."""
    from sqlalchemy import select

    from ..models import BookingCommandRecord as R
    from ..models import WhatsAppMessage as M
    from .booking_ce_adapter import flow_booking_key
    from .booking_recovery import stored_inbound_event

    latest = db.execute(
        select(M.direction, M.message_type, M.wa_message_id)
        .where(M.thread_id == thread_id, M.status.notin_(("blocked", "failed")))
        .order_by(M.timestamp.desc(), M.id.desc()).limit(1)).first()
    if (latest is None or latest.direction != "in" or latest.message_type != "flow_response"
            or not latest.wa_message_id):
        return None
    event = stored_inbound_event(db, latest.wa_message_id)
    if event is None or event.thread_id != thread_id:
        return None
    key = flow_booking_key(int(thread_id), event.flow_token, latest.wa_message_id)
    if key is None:
        return None
    ledger = db.execute(select(R.id, R.thread_id, R.command_state, R.operation,
                               R.thread_revision_id, R.ack_status, R.ack_transport_id)
                        .where(R.idempotency_key == key)).first()
    if (ledger is None or ledger.command_state != "COMMITTED" or ledger.operation != "CREATE"
            or ledger.thread_id != thread_id or ledger.thread_revision_id is None
            or ledger.ack_status != "SENT" or not ledger.ack_transport_id):
        return None
    acknowledged = db.execute(
        select(M.id).where(M.wa_message_id == ledger.ack_transport_id, M.direction == "out",
                           M.thread_id == thread_id, M.status.in_(_ACCEPTED_STATUSES))
    ).first()
    return int(ledger.id) if acknowledged is not None else None


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

# L4.7W5-F1: the first version of this check alerted on ANY event left at 'triggered', and
# immediately produced false positives on a real 3-message burst. n8n debounces a burst for
# ~20 s and calls CE once, with the LAST message: the earlier events stay 'triggered' for
# ever, by design, and were answered perfectly well. "Old and triggered" is not "stalled".
#
# An event is explained — not stalled — when a LATER event on the same thread reached CE
# within the debounce window. The window is generous against the 20 s debounce so that a
# slow burst is still recognised as a burst, and short enough that a genuine failure an hour
# later is never excused by an unrelated conversation.
DEBOUNCE_SUCCESSOR_WINDOW_SECONDS = 120


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
                    -- a later event on the same thread that DID reach CE inside the
                    -- debounce window explains this one: it was aggregated, not lost.
                    AND NOT EXISTS (
                        SELECT 1 FROM ai_events later
                        WHERE later.thread_id = ae.thread_id
                          AND later.id > ae.id
                          AND later.status = 'processed'
                          AND later.created_at >= ae.created_at
                          AND later.created_at <= ae.created_at
                              + INTERVAL '{DEBOUNCE_SUCCESSOR_WINDOW_SECONDS} seconds'
                    )
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
        event_rows = db.execute(_FIND_UNANSWERED_EVENTS_SQL).fetchall()

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
                booked = latest_inbound_is_acknowledged_booking_completion(db, thread_id)
                db.rollback()
                if booked is not None:
                    logger.info(
                        "unanswered_alert: Thread #%s latest inbound completes acknowledged "
                        "booking command #%s — answered, no handoff alert", thread_id, booked)
                    continue
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
