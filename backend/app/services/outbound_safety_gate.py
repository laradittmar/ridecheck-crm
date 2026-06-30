"""Outbound safety gate for automated WhatsApp bot replies.

Enforces two policies before every automated Meta API call:

  1. DEDUP GUARD  — The same normalized text of the same message_kind must not
                    be sent more than once to the same recipient (wa_id) within a
                    rolling 10-minute window.

  2. FLOOD GATE   — No more than 3 automated messages may be attempted for the
                    same recipient (wa_id) within 60 seconds.

Both guards are keyed by recipient wa_id, not by thread_id.

## Transaction durability invariant

ALL gate-owned writes (blocked audit rows, dedup claims, pending records,
needs_human escalation) are committed in DEDICATED sessions that are
completely isolated from the caller's SQLAlchemy session.

The caller session (passed to __init__) is NEVER committed, flushed, or
rolled back by the gate.  This guarantees that gate audit rows survive
even when the caller's session is rolled back — e.g. when
ConversationEngine.handle() calls self.db.rollback() after catching
OutboundBlockedError.

For every gate outcome:

  BLOCKED_KILL_SWITCH
    → blocked audit written and committed in a dedicated gate session.
    → caller session untouched; caller rollback cannot erase audit.

  BLOCKED_DUPLICATE
    → blocked audit written and committed in a dedicated gate session
      (the same session that held the recipient lock).
    → caller session untouched; caller rollback cannot erase audit.

  BLOCKED_FLOOD
    → blocked audit AND needs_human=True written and committed in a
      dedicated gate session (the same session that held the recipient lock).
    → caller session untouched; caller rollback cannot erase audit or escalation.

  ALLOWED
    → recipient lock, flood/dedup evaluation, dedup claim, AND pending audit
      are all committed in a dedicated gate session BEFORE Meta is called.
    → caller session receives no commit from the gate.

  mark_sent / mark_failed
    → update the pending row in their own dedicated gate sessions.
    → caller session untouched.

## Dedicated session strategy

Gate sessions are created from the SAME engine as the caller (via db.bind).
This ensures:
  - Postgres: true connection-level isolation between gate and caller sessions.
  - SQLite (offline unit tests): StaticPool means gate sessions share the same
    connection, so gate commits also commit the caller's in-flight state.
    This is acceptable because offline tests verify gate logic, not durability.
    Rollback durability is proved in crm_test PostgreSQL integration tests only.

## Concurrency guarantee (Postgres)

Before flood/dedup checks, a per-recipient advisory lock is acquired via
INSERT-OR-IGNORE + SELECT FOR UPDATE on whatsapp_recipient_locks.  These run
inside the gate's dedicated session, serialising concurrent automated sends at
the DB level.  On SQLite the FOR UPDATE clause is skipped via SAVEPOINT/rollback.
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from sqlalchemy import func as sqlfunc, select, text
from sqlalchemy.orm import Session

from ..models import (
    WhatsAppMessage,
    WhatsAppOutboundDedup,
    WhatsAppRecipientLock,
    WhatsAppThreadState,
)

logger = logging.getLogger(__name__)

# ── Policy constants ──────────────────────────────────────────────────────────

DEDUP_WINDOW_MINUTES: int = 10
FLOOD_WINDOW_SECONDS: int = 60
FLOOD_MAX_MESSAGES: int = 3


# ── Helpers ───────────────────────────────────────────────────────────────────

def _content_fingerprint(text: str) -> str:
    """SHA-256 of NFKD-normalised, lowercased, whitespace-collapsed text."""
    normalized = unicodedata.normalize("NFKD", text.strip().lower())
    collapsed = " ".join(normalized.split())
    return hashlib.sha256(collapsed.encode()).hexdigest()


# ── Public API ────────────────────────────────────────────────────────────────

class GateOutcome(str, Enum):
    ALLOWED = "allowed"
    BLOCKED_DUPLICATE = "blocked_duplicate"
    BLOCKED_FLOOD = "blocked_flood"
    BLOCKED_KILL_SWITCH = "blocked_kill_switch"


@dataclass
class GateResult:
    outcome: GateOutcome
    message_id: Optional[int]   # whatsapp_messages.id of the created record
    blocked_reason: Optional[str] = None


class OutboundSafetyGate:
    """Transaction-safe outbound safety gate with dedicated-session isolation.

    See module docstring for the full transaction durability invariant.
    """

    def __init__(self, db: Session) -> None:
        # The caller's session — NEVER committed, flushed, or rolled back by gate.
        self._caller_db = db
        self._db = db  # backward-compat alias kept for existing callers

        # Build a session factory that uses the SAME engine as the caller.
        # db.bind works for Session(engine) and sessionmaker(bind=engine)() in SA 2.0.36.
        from sqlalchemy.orm import sessionmaker as _sm
        _engine = db.bind
        if _engine is not None:
            self._GateSession = _sm(bind=_engine, autoflush=True, autocommit=False)
        else:
            # Fallback for sessions where .bind is not set (rare in this codebase).
            from ..db import SessionLocal
            self._GateSession = SessionLocal

    # ── Dedicated-session context manager ────────────────────────────────────

    @contextlib.contextmanager
    def _gate_db(self):
        """Open a dedicated gate session; rollback on exception; always close."""
        sess = self._GateSession()
        try:
            yield sess
        except Exception:
            try:
                sess.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                sess.close()
            except Exception:
                pass

    # ── Main entry point ──────────────────────────────────────────────────────

    def attempt(
        self,
        wa_id: str,
        thread_id: int,
        text: str,
        message_type: str = "text",
        now: Optional[datetime] = None,
    ) -> GateResult:
        """Check all gates and persist a pre-send audit record.

        Must be called BEFORE the Meta API call.  All writes occur in gate-owned
        dedicated sessions; the caller's session is never touched.

        Returns:
          GateOutcome.ALLOWED          → call Meta, then mark_sent / mark_failed
          GateOutcome.BLOCKED_DUPLICATE → do NOT call Meta; durable audit written
          GateOutcome.BLOCKED_FLOOD     → do NOT call Meta; durable audit + needs_human written
          GateOutcome.BLOCKED_KILL_SWITCH → same; audit in separate session
        """
        if now is None:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))

        fp = _content_fingerprint(text)

        # ── 0. Kill switch — dedicated gate session ──────────────────────────
        kill_result = self._check_kill_switch(
            wa_id=wa_id, thread_id=thread_id, text=text, fp=fp,
            message_type=message_type, now=now,
        )
        if kill_result is not None:
            return kill_result

        # ── 1-4. Remaining ops share ONE dedicated session (holds FOR UPDATE) ─
        with self._gate_db() as gate_db:

            # 1. Acquire recipient lock
            self._acquire_recipient_lock(gate_db, wa_id=wa_id, now=now)

            # 2. Flood gate
            flood_result = self._check_flood(
                gate_db, wa_id=wa_id, thread_id=thread_id,
                now=now, text=text, fp=fp, message_type=message_type,
            )
            if flood_result is not None:
                return flood_result  # gate_db already committed inside _check_flood

            # 3. Dedup guard (rolling window)
            dedup_result = self._check_dedup(
                gate_db, wa_id=wa_id, thread_id=thread_id,
                fp=fp, message_type=message_type, now=now, text_content=text,
            )
            if dedup_result is not None:
                return dedup_result  # gate_db already committed inside _check_dedup

            # 4. All clear — insert dedup claim + pending audit, commit before Meta
            dedup_entry = WhatsAppOutboundDedup(
                wa_id=wa_id,
                thread_id=thread_id,
                message_kind=message_type,
                content_fingerprint=fp,
                created_at=now,
            )
            pending = WhatsAppMessage(
                thread_id=thread_id,
                direction="out",
                status="pending",
                timestamp=now,
                created_at=now,
                message_type=message_type,
                text=text,
                automated=True,
                content_fingerprint=fp,
            )
            gate_db.add(dedup_entry)
            gate_db.add(pending)
            gate_db.flush()           # assigns IDs without committing
            pending_id = pending.id   # cache before expire_on_commit clears it
            gate_db.commit()          # durable BEFORE caller reaches Meta

            logger.info(
                "OUTBOUND_GATE_ALLOWED wa_id=...%s thread_id=%s fp=%.8s message_type=%s",
                wa_id[-4:], thread_id, fp, message_type,
            )
            return GateResult(outcome=GateOutcome.ALLOWED, message_id=pending_id)

    # ── Post-send status updates ──────────────────────────────────────────────

    def mark_sent(self, message_id: int, wa_message_id: Optional[str] = None) -> None:
        """Update the pending record to 'sent' after a successful Meta API call.

        Uses a dedicated gate session; the caller's session is not touched.
        """
        with self._gate_db() as gate_db:
            msg = gate_db.get(WhatsAppMessage, message_id)
            if msg is None:
                logger.warning("OUTBOUND_GATE mark_sent: message_id=%s not found", message_id)
                return
            msg.status = "sent"
            msg.wa_message_id = wa_message_id
            gate_db.commit()
            logger.info(
                "OUTBOUND_GATE_SENT thread_id=%s message_id=%s wamid=%s",
                msg.thread_id, message_id, wa_message_id or "-",
            )

    def mark_failed(self, message_id: int) -> None:
        """Update the pending record to 'failed' after a Meta API call error.

        The dedup entry is NOT removed — gate blocks automatic retries within the
        10-minute window.  Uses a dedicated gate session.
        """
        with self._gate_db() as gate_db:
            msg = gate_db.get(WhatsAppMessage, message_id)
            if msg is None:
                logger.warning("OUTBOUND_GATE mark_failed: message_id=%s not found", message_id)
                return
            msg.status = "failed"
            gate_db.commit()
            logger.error(
                "OUTBOUND_GATE_FAILED thread_id=%s message_id=%s — "
                "dedup entry retained to block automatic retries within the 10-min window",
                msg.thread_id, message_id,
            )

    # ── Private gate checks ───────────────────────────────────────────────────

    def _acquire_recipient_lock(self, db: Session, wa_id: str, now: datetime) -> None:
        """Insert-or-ignore a recipient lock row, then SELECT FOR UPDATE.

        Postgres: acquires a row-level lock that serialises concurrent automated
        sends.  The lock is held until db.commit() is called (end of the main
        gate attempt transaction).

        SQLite: FOR UPDATE is not supported; the SAVEPOINT is rolled back silently
        so single-threaded offline tests continue without modification.
        """
        try:
            db.execute(
                text(
                    "INSERT INTO whatsapp_recipient_locks (wa_id, created_at) "
                    "VALUES (:wa_id, :now) ON CONFLICT (wa_id) DO NOTHING"
                ),
                {"wa_id": wa_id, "now": now},
            )
        except Exception:
            return  # table not available (migration not applied)

        nested = db.begin_nested()
        try:
            db.execute(
                text("SELECT id FROM whatsapp_recipient_locks WHERE wa_id = :wa_id FOR UPDATE"),
                {"wa_id": wa_id},
            )
            nested.commit()
        except Exception:
            nested.rollback()  # SQLite: FOR UPDATE not supported — graceful no-op

    def _check_kill_switch(
        self, wa_id: str, thread_id: int, text: str, fp: str,
        message_type: str, now: datetime,
    ) -> Optional[GateResult]:
        """Return a blocked result when OUTBOUND_ENABLED != 'true', else None.

        The blocked audit is committed in a dedicated gate session that is entirely
        independent from the caller's session.  A caller rollback cannot erase it.
        """
        import os
        if os.environ.get("OUTBOUND_ENABLED") == "true":
            return None
        reason = "KILL_SWITCH: OUTBOUND_ENABLED is not 'true'"
        blocked_id: int
        with self._gate_db() as _audit:
            _blocked = WhatsAppMessage(
                thread_id=thread_id,
                direction="out",
                status="blocked",
                timestamp=now,
                created_at=now,
                message_type=message_type,
                text=text,
                automated=True,
                content_fingerprint=fp,
                blocked_reason=reason,
            )
            _audit.add(_blocked)
            _audit.flush()
            blocked_id = _blocked.id
            _audit.commit()
        logger.warning(
            "OUTBOUND_GATE_KILL_SWITCH wa_id=...%s thread_id=%s fp=%.8s blocked_id=%s",
            wa_id[-4:], thread_id, fp, blocked_id,
        )
        return GateResult(
            outcome=GateOutcome.BLOCKED_KILL_SWITCH,
            message_id=blocked_id,
            blocked_reason=reason,
        )

    def _check_flood(
        self,
        db: Session,
        wa_id: str,
        thread_id: int,
        now: datetime,
        text: str,
        fp: str,
        message_type: str,
    ) -> Optional[GateResult]:
        """Count distinct dedup slots in the rolling flood window.

        Writes and commits within `db` (caller-provided gate session).
        """
        cutoff = now - timedelta(seconds=FLOOD_WINDOW_SECONDS)
        count = db.execute(
            select(sqlfunc.count())
            .select_from(WhatsAppOutboundDedup)
            .where(
                WhatsAppOutboundDedup.wa_id == wa_id,
                WhatsAppOutboundDedup.created_at > cutoff,
            )
        ).scalar() or 0

        if count < FLOOD_MAX_MESSAGES:
            return None

        reason = (
            f"FLOOD: {count} automated messages already sent to recipient wa_id='...{wa_id[-4:]}' "
            f"in the last {FLOOD_WINDOW_SECONDS}s "
            f"(limit={FLOOD_MAX_MESSAGES}, thread_id={thread_id})"
        )
        blocked = WhatsAppMessage(
            thread_id=thread_id,
            direction="out",
            status="blocked",
            timestamp=now,
            created_at=now,
            message_type=message_type,
            text=text,
            automated=True,
            content_fingerprint=fp,
            blocked_reason=reason,
        )
        db.add(blocked)

        # needs_human escalation committed in the SAME gate session → durable.
        state = db.execute(
            select(WhatsAppThreadState).where(WhatsAppThreadState.thread_id == thread_id)
        ).scalar_one_or_none()
        if state is not None:
            state.needs_human = True
        else:
            logger.warning(
                "OUTBOUND_GATE_FLOOD wa_id=...%s thread_id=%s — no WhatsAppThreadState found; "
                "could not set needs_human=True",
                wa_id[-4:], thread_id,
            )

        db.flush()
        blocked_id = blocked.id
        db.commit()  # durable in gate session; caller rollback cannot erase this

        logger.warning(
            "OUTBOUND_GATE_FLOOD wa_id=...%s thread_id=%s fp=%.8s reason=%r",
            wa_id[-4:], thread_id, fp, reason,
        )
        return GateResult(
            outcome=GateOutcome.BLOCKED_FLOOD,
            message_id=blocked_id,
            blocked_reason=reason,
        )

    def _check_dedup(
        self,
        db: Session,
        wa_id: str,
        thread_id: int,
        fp: str,
        message_type: str,
        now: datetime,
        text_content: str,
    ) -> Optional[GateResult]:
        """Rolling-window dedup check.

        Writes and commits within `db` (caller-provided gate session).
        """
        cutoff = now - timedelta(minutes=DEDUP_WINDOW_MINUTES)
        dup_row = db.execute(
            select(WhatsAppOutboundDedup).where(
                WhatsAppOutboundDedup.wa_id == wa_id,
                WhatsAppOutboundDedup.message_kind == message_type,
                WhatsAppOutboundDedup.content_fingerprint == fp,
                WhatsAppOutboundDedup.created_at > cutoff,
            ).limit(1)
        ).scalar_one_or_none()

        if dup_row is None:
            return None

        reason = (
            f"DUPLICATE: identical {message_type} (fp='{fp[:8]}...') already sent to "
            f"recipient wa_id='...{wa_id[-4:]}' within rolling {DEDUP_WINDOW_MINUTES}-minute window "
            f"(first_sent={dup_row.created_at.isoformat() if hasattr(dup_row.created_at, 'isoformat') else dup_row.created_at}, "
            f"thread_id={thread_id})"
        )
        blocked = WhatsAppMessage(
            thread_id=thread_id,
            direction="out",
            status="blocked",
            timestamp=now,
            created_at=now,
            message_type=message_type,
            text=text_content,
            automated=True,
            content_fingerprint=fp,
            blocked_reason=reason,
        )
        db.add(blocked)
        db.flush()
        blocked_id = blocked.id
        db.commit()  # durable in gate session; caller rollback cannot erase this

        logger.warning(
            "OUTBOUND_GATE_DUPLICATE wa_id=...%s thread_id=%s fp=%.8s kind=%s cutoff=%s",
            wa_id[-4:], thread_id, fp, message_type, cutoff.isoformat(),
        )
        return GateResult(
            outcome=GateOutcome.BLOCKED_DUPLICATE,
            message_id=blocked_id,
            blocked_reason=reason,
        )
