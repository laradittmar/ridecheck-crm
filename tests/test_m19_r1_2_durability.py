"""M19.R1.2 — Gate Transaction Durability Tests (Postgres only).

Verifies the invariant:

  ALL gate-owned writes (blocked audit rows, dedup claims, pending records,
  needs_human escalation) are committed in DEDICATED sessions that are
  completely isolated from the caller's SQLAlchemy session.

  A caller rollback MUST NOT erase any gate-owned write.
  The gate MUST NOT commit or flush the caller's dirty state.

Tests:

  D.1  BLOCKED_DUPLICATE audit survives caller session rollback
  D.2  BLOCKED_FLOOD audit + needs_human=True survive caller session rollback
  D.3  ALLOWED path does NOT commit the caller's dirty (uncommitted) state;
       gate's pending row IS committed before returning
  D.4  ALLOWED success lifecycle: pending → mark_sent → sent;
       re-attempt with same text → BLOCKED_DUPLICATE
  D.5  Each gate call produces exactly ONE audit row (no double-write from
       caller session + gate session)
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

for _mod in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

try:
    import psycopg  # noqa: F401
except ImportError:
    pass
for _pg_mod in ["psycopg2", "psycopg2.extensions"]:
    if _pg_mod not in sys.modules:
        sys.modules[_pg_mod] = types.ModuleType(_pg_mod)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models import WhatsAppMessage
from app.services.outbound_safety_gate import (
    FLOOD_MAX_MESSAGES,
    FLOOD_WINDOW_SECONDS,
    GateOutcome,
    OutboundSafetyGate,
)

_DB_URL = os.environ.get("DATABASE_URL", "")
_POSTGRES = "postgresql" in _DB_URL

_NOW = datetime(2026, 6, 29, 22, 0, 0, tzinfo=timezone.utc)
_PREFIX = "TEST_M19DUR_"


def _require_postgres(test_case):
    if not _POSTGRES:
        raise unittest.SkipTest("Durability tests require Postgres — set DATABASE_URL to postgresql://")


def _engine():
    return create_engine(_DB_URL, pool_pre_ping=True)


# ── Fixture helpers (same pattern as test_m19_r1_2_pg_integration.py) ─────────

def _make_recipient(session: Session, suffix: str) -> tuple[str, int, int]:
    wa_id = f"{_PREFIX}{suffix}"
    contact_row = session.execute(
        text("SELECT id FROM whatsapp_contacts WHERE wa_id = :wa_id"),
        {"wa_id": wa_id},
    ).fetchone()
    if contact_row is None:
        session.execute(
            text(
                "INSERT INTO whatsapp_contacts (wa_id, display_name, phone, created_at) "
                "VALUES (:wa_id, 'Test M19 Dur', '5491100000001', now())"
            ),
            {"wa_id": wa_id},
        )
        session.flush()
    contact_id = session.execute(
        text("SELECT id FROM whatsapp_contacts WHERE wa_id = :wa_id"),
        {"wa_id": wa_id},
    ).scalar()

    thread_row = session.execute(
        text("SELECT id FROM whatsapp_threads WHERE contact_id = :cid LIMIT 1"),
        {"cid": contact_id},
    ).fetchone()
    if thread_row is None:
        session.execute(
            text(
                "INSERT INTO whatsapp_threads (contact_id, unread_count, created_at) "
                "VALUES (:cid, 0, now())"
            ),
            {"cid": contact_id},
        )
        session.flush()
    thread_id = session.execute(
        text("SELECT id FROM whatsapp_threads WHERE contact_id = :cid LIMIT 1"),
        {"cid": contact_id},
    ).scalar()

    state_row = session.execute(
        text("SELECT id FROM whatsapp_thread_states WHERE thread_id = :tid"),
        {"tid": thread_id},
    ).fetchone()
    if state_row is None:
        session.execute(
            text(
                "INSERT INTO whatsapp_thread_states "
                "(thread_id, needs_human, created_at, updated_at) "
                "VALUES (:tid, false, now(), now())"
            ),
            {"tid": thread_id},
        )
        session.flush()
    state_id = session.execute(
        text("SELECT id FROM whatsapp_thread_states WHERE thread_id = :tid"),
        {"tid": thread_id},
    ).scalar()

    session.commit()
    return wa_id, thread_id, state_id


def _cleanup(session: Session) -> None:
    session.execute(
        text("DELETE FROM whatsapp_outbound_dedup WHERE wa_id LIKE :pfx"),
        {"pfx": f"{_PREFIX}%"},
    )
    session.execute(
        text("DELETE FROM whatsapp_recipient_locks WHERE wa_id LIKE :pfx"),
        {"pfx": f"{_PREFIX}%"},
    )
    session.execute(
        text("""
            DELETE FROM whatsapp_messages
            WHERE thread_id IN (
                SELECT t.id FROM whatsapp_threads t
                JOIN whatsapp_contacts c ON c.id = t.contact_id
                WHERE c.wa_id LIKE :pfx
            )
        """),
        {"pfx": f"{_PREFIX}%"},
    )
    session.execute(
        text("""
            DELETE FROM whatsapp_thread_states
            WHERE thread_id IN (
                SELECT t.id FROM whatsapp_threads t
                JOIN whatsapp_contacts c ON c.id = t.contact_id
                WHERE c.wa_id LIKE :pfx
            )
        """),
        {"pfx": f"{_PREFIX}%"},
    )
    session.execute(
        text("""
            DELETE FROM whatsapp_threads
            WHERE contact_id IN (
                SELECT id FROM whatsapp_contacts WHERE wa_id LIKE :pfx
            )
        """),
        {"pfx": f"{_PREFIX}%"},
    )
    session.execute(
        text("DELETE FROM whatsapp_contacts WHERE wa_id LIKE :pfx"),
        {"pfx": f"{_PREFIX}%"},
    )
    session.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Shared base class
# ══════════════════════════════════════════════════════════════════════════════

class _DurabilityBase(unittest.TestCase):
    def setUp(self):
        _require_postgres(self)
        self.engine = _engine()
        setup_db = Session(self.engine)
        try:
            self.wa_id, self.thread_id, self.state_id = _make_recipient(setup_db, "D")
        finally:
            setup_db.close()
        # caller_db represents ConversationEngine's self.db (or the FastAPI route's db dep)
        self.caller_db = Session(self.engine)
        os.environ.pop("OUTBOUND_ENABLED", None)

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        try:
            self.caller_db.rollback()
        except Exception:
            pass
        try:
            self.caller_db.close()
        except Exception:
            pass
        cleanup_db = Session(self.engine)
        try:
            _cleanup(cleanup_db)
        finally:
            cleanup_db.close()
        self.engine.dispose()

    def _fresh(self) -> Session:
        """Open an independent verify session from the same engine."""
        return Session(self.engine)


# ══════════════════════════════════════════════════════════════════════════════
# D.1 — BLOCKED_DUPLICATE audit survives caller rollback
# ══════════════════════════════════════════════════════════════════════════════

class TestDuplicateBlockDurability(_DurabilityBase):

    def test_d1_duplicate_block_audit_survives_caller_rollback(self):
        """A BLOCKED_DUPLICATE audit must persist even after caller_db.rollback()."""
        os.environ["OUTBOUND_ENABLED"] = "true"

        # First send — ALLOWED (seeds the dedup entry)
        r1 = OutboundSafetyGate(self.caller_db).attempt(
            wa_id=self.wa_id, thread_id=self.thread_id,
            text="D1 durability duplicate text",
            message_type="text", now=_NOW,
        )
        self.assertEqual(r1.outcome, GateOutcome.ALLOWED,
                         "First send must be ALLOWED to seed dedup entry")

        # Second send — same text within window → BLOCKED_DUPLICATE
        r2 = OutboundSafetyGate(self.caller_db).attempt(
            wa_id=self.wa_id, thread_id=self.thread_id,
            text="D1 durability duplicate text",
            message_type="text", now=_NOW + timedelta(seconds=30),
        )
        self.assertEqual(r2.outcome, GateOutcome.BLOCKED_DUPLICATE)
        blocked_id = r2.message_id

        # ── This is the critical rollback — ConversationEngine does this on OutboundBlockedError
        self.caller_db.rollback()

        # ── Verify: blocked audit row survived the caller rollback ──────────
        with self._fresh() as verify_db:
            blocked = verify_db.get(WhatsAppMessage, blocked_id)
            self.assertIsNotNone(
                blocked,
                "BLOCKED_DUPLICATE audit row must exist after caller_db.rollback() — "
                "gate committed it in a dedicated session",
            )
            self.assertEqual(blocked.status, "blocked")
            self.assertIn(
                "DUPLICATE", blocked.blocked_reason,
                "blocked_reason must contain 'DUPLICATE'",
            )
            self.assertEqual(blocked.thread_id, self.thread_id)

    def test_d1b_first_allowed_pending_also_survives(self):
        """The ALLOWED pending row from the first send also survives independently."""
        os.environ["OUTBOUND_ENABLED"] = "true"

        r1 = OutboundSafetyGate(self.caller_db).attempt(
            wa_id=self.wa_id, thread_id=self.thread_id,
            text="D1b pending survival text",
            message_type="text", now=_NOW,
        )
        self.assertEqual(r1.outcome, GateOutcome.ALLOWED)
        pending_id = r1.message_id

        # Caller rolls back (simulates an error in the caller after the gate returned ALLOWED)
        self.caller_db.rollback()

        with self._fresh() as verify_db:
            pending = verify_db.get(WhatsAppMessage, pending_id)
            self.assertIsNotNone(
                pending,
                "ALLOWED pending row must survive caller rollback — "
                "gate committed it in a dedicated session before Meta was called",
            )
            self.assertEqual(pending.status, "pending")


# ══════════════════════════════════════════════════════════════════════════════
# D.2 — BLOCKED_FLOOD audit + needs_human survive caller rollback
# ══════════════════════════════════════════════════════════════════════════════

class TestFloodBlockDurability(_DurabilityBase):

    def test_d2_flood_block_and_needs_human_survive_caller_rollback(self):
        """A BLOCKED_FLOOD audit and needs_human=True must persist after caller_db.rollback()."""
        os.environ["OUTBOUND_ENABLED"] = "true"

        # Send FLOOD_MAX_MESSAGES distinct messages to fill the flood window
        for i in range(FLOOD_MAX_MESSAGES):
            r = OutboundSafetyGate(self.caller_db).attempt(
                wa_id=self.wa_id, thread_id=self.thread_id,
                text=f"D2 flood filler message #{i}",
                message_type="text", now=_NOW + timedelta(seconds=i),
            )
            self.assertEqual(
                r.outcome, GateOutcome.ALLOWED,
                f"Filler send #{i} should be ALLOWED (flood window not yet full)",
            )

        # (FLOOD_MAX_MESSAGES + 1)-th message → BLOCKED_FLOOD
        r_flood = OutboundSafetyGate(self.caller_db).attempt(
            wa_id=self.wa_id, thread_id=self.thread_id,
            text="D2 flood trigger",
            message_type="text", now=_NOW + timedelta(seconds=FLOOD_MAX_MESSAGES),
        )
        self.assertEqual(r_flood.outcome, GateOutcome.BLOCKED_FLOOD)
        flood_blocked_id = r_flood.message_id

        # ── Simulate ConversationEngine.handle() rollback ───────────────────
        self.caller_db.rollback()

        # ── Verify: blocked audit row survived ──────────────────────────────
        with self._fresh() as verify_db:
            blocked = verify_db.get(WhatsAppMessage, flood_blocked_id)
            self.assertIsNotNone(
                blocked,
                "BLOCKED_FLOOD audit row must survive caller_db.rollback()",
            )
            self.assertEqual(blocked.status, "blocked")
            self.assertIn("FLOOD", blocked.blocked_reason)

            # ── Verify: needs_human=True survived ───────────────────────────
            state_row = verify_db.execute(
                text("SELECT needs_human FROM whatsapp_thread_states WHERE thread_id = :tid"),
                {"tid": self.thread_id},
            ).fetchone()
            self.assertIsNotNone(state_row, "WhatsAppThreadState row must exist")
            self.assertTrue(
                state_row[0],
                "needs_human must be True in DB after BLOCKED_FLOOD, "
                "even after caller_db.rollback() — gate committed it in a dedicated session",
            )


# ══════════════════════════════════════════════════════════════════════════════
# D.3 — ALLOWED path does NOT commit caller dirty state
# ══════════════════════════════════════════════════════════════════════════════

class TestAllowedCallerIsolation(_DurabilityBase):

    def test_d3_allowed_does_not_commit_caller_dirty_state(self):
        """Gate ALLOWED path must NOT commit or flush the caller's session.

        If the gate incorrectly called self._db.commit() (the old bug), the
        caller's uncommitted 'canary' message would appear in the DB after
        gate.attempt() returns, before the caller explicitly commits.
        """
        os.environ["OUTBOUND_ENABLED"] = "true"

        # Add a dirty (uncommitted) object to the caller session.
        # This represents work-in-progress by the caller (e.g., updating a candidate record).
        canary_text = f"{_PREFIX}CANARY_D3_DIRTY_{int(_NOW.timestamp())}"
        import hashlib
        canary_fp = hashlib.sha256(canary_text.encode()).hexdigest()
        canary_msg = WhatsAppMessage(
            thread_id=self.thread_id,
            direction="out",
            status="pending",
            timestamp=_NOW,
            created_at=_NOW,
            message_type="text",
            text=canary_text,
            automated=True,
            content_fingerprint=canary_fp,
        )
        self.caller_db.add(canary_msg)
        # Do NOT commit or flush — caller's session is dirty

        # Gate attempt with a DIFFERENT text so it is ALLOWED
        gate_text = "D3 gate-allowed text distinct from canary"
        result = OutboundSafetyGate(self.caller_db).attempt(
            wa_id=self.wa_id, thread_id=self.thread_id,
            text=gate_text, message_type="text", now=_NOW,
        )
        self.assertEqual(result.outcome, GateOutcome.ALLOWED,
                         "Gate must ALLOW a fresh text")

        # ── Gate's pending row MUST be in DB (gate committed its dedicated session)
        with self._fresh() as verify_db:
            gate_msg = verify_db.get(WhatsAppMessage, result.message_id)
            self.assertIsNotNone(
                gate_msg,
                "Gate's pending row must be committed to DB before gate.attempt() returns",
            )
            self.assertEqual(gate_msg.status, "pending")
            self.assertEqual(gate_msg.text, gate_text)

        # ── Caller's canary must NOT be in DB yet (gate must not have committed caller_db)
        with self._fresh() as verify_db:
            count = verify_db.execute(
                text("SELECT COUNT(*) FROM whatsapp_messages WHERE text = :t"),
                {"t": canary_text},
            ).scalar()
            self.assertEqual(
                count, 0,
                "Caller's dirty uncommitted canary message must NOT be in DB after "
                "gate.attempt() — the gate must never commit the caller's session. "
                "(If this fails, the gate is calling self._db.commit() — old bug.)",
            )

        # Caller explicitly commits (normal success path)
        self.caller_db.commit()

        # After explicit caller commit, canary IS in DB
        with self._fresh() as verify_db:
            count = verify_db.execute(
                text("SELECT COUNT(*) FROM whatsapp_messages WHERE text = :t"),
                {"t": canary_text},
            ).scalar()
            self.assertEqual(
                count, 1,
                "After explicit caller_db.commit(), canary must appear in DB",
            )

    def test_d3b_allowed_pending_committed_before_caller_touches_meta(self):
        """Gate must commit the pending row BEFORE returning ALLOWED to the caller.

        The caller then calls Meta.  If the pending row were not committed first,
        a crash between ALLOWED return and Meta call would leave an orphaned send
        with no audit trail.
        """
        os.environ["OUTBOUND_ENABLED"] = "true"

        result = OutboundSafetyGate(self.caller_db).attempt(
            wa_id=self.wa_id, thread_id=self.thread_id,
            text="D3b pre-meta pending text",
            message_type="text", now=_NOW,
        )
        self.assertEqual(result.outcome, GateOutcome.ALLOWED)

        # Verify immediately after gate.attempt() returns (before any caller action)
        with self._fresh() as verify_db:
            msg = verify_db.get(WhatsAppMessage, result.message_id)
            self.assertIsNotNone(msg, "Pending row must be in DB before caller reaches Meta")
            self.assertEqual(msg.status, "pending")


# ══════════════════════════════════════════════════════════════════════════════
# D.4 — ALLOWED success lifecycle (pending → sent, then re-attempt blocked)
# ══════════════════════════════════════════════════════════════════════════════

class TestAllowedSuccessLifecycle(_DurabilityBase):

    def test_d4_pending_to_sent_via_mark_sent(self):
        """mark_sent() updates status to 'sent' in its own dedicated session."""
        os.environ["OUTBOUND_ENABLED"] = "true"
        wamid = "wamid.test.d4.durability.abc123"

        gate = OutboundSafetyGate(self.caller_db)
        result = gate.attempt(
            wa_id=self.wa_id, thread_id=self.thread_id,
            text="D4 lifecycle sent text", message_type="text", now=_NOW,
        )
        self.assertEqual(result.outcome, GateOutcome.ALLOWED)

        # Simulate Meta API call succeeds
        gate.mark_sent(result.message_id, wa_message_id=wamid)

        with self._fresh() as verify_db:
            msg = verify_db.get(WhatsAppMessage, result.message_id)
            self.assertIsNotNone(msg)
            self.assertEqual(msg.status, "sent",
                             "Status must be 'sent' after mark_sent()")
            self.assertEqual(msg.wa_message_id, wamid,
                             "wa_message_id must be persisted after mark_sent()")

    def test_d4b_re_attempt_same_text_blocked_duplicate_after_sent(self):
        """Re-attempting the same text within the dedup window → BLOCKED_DUPLICATE
        even after mark_sent() was called."""
        os.environ["OUTBOUND_ENABLED"] = "true"

        gate = OutboundSafetyGate(self.caller_db)
        text_msg = "D4b re-attempt blocked duplicate after sent"
        r1 = gate.attempt(
            wa_id=self.wa_id, thread_id=self.thread_id,
            text=text_msg, message_type="text", now=_NOW,
        )
        self.assertEqual(r1.outcome, GateOutcome.ALLOWED)
        gate.mark_sent(r1.message_id, wa_message_id="wamid.d4b.xyz")

        # Re-attempt same text within 10-min window
        r2 = OutboundSafetyGate(self.caller_db).attempt(
            wa_id=self.wa_id, thread_id=self.thread_id,
            text=text_msg, message_type="text",
            now=_NOW + timedelta(minutes=5),
        )
        self.assertEqual(
            r2.outcome, GateOutcome.BLOCKED_DUPLICATE,
            "Same text within dedup window must be BLOCKED_DUPLICATE even after mark_sent",
        )

    def test_d4c_mark_failed_sets_failed_status_in_dedicated_session(self):
        """mark_failed() updates status to 'failed' in its own dedicated session."""
        os.environ["OUTBOUND_ENABLED"] = "true"

        gate = OutboundSafetyGate(self.caller_db)
        result = gate.attempt(
            wa_id=self.wa_id, thread_id=self.thread_id,
            text="D4c mark failed text", message_type="text", now=_NOW,
        )
        self.assertEqual(result.outcome, GateOutcome.ALLOWED)

        gate.mark_failed(result.message_id)

        with self._fresh() as verify_db:
            msg = verify_db.get(WhatsAppMessage, result.message_id)
            self.assertIsNotNone(msg)
            self.assertEqual(msg.status, "failed",
                             "Status must be 'failed' after mark_failed()")


# ══════════════════════════════════════════════════════════════════════════════
# D.5 — Exactly one audit row per gate call (no double-write)
# ══════════════════════════════════════════════════════════════════════════════

class TestExactlyOneAuditRow(_DurabilityBase):

    def test_d5_blocked_duplicate_creates_exactly_one_audit_row(self):
        """Each gate.attempt() that results in a block creates EXACTLY ONE audit row.

        If the gate wrote to both the caller session and the dedicated gate session,
        an explicit caller_db.commit() would produce a second row.
        """
        os.environ["OUTBOUND_ENABLED"] = "true"

        # First send — ALLOWED
        OutboundSafetyGate(self.caller_db).attempt(
            wa_id=self.wa_id, thread_id=self.thread_id,
            text="D5 duplicate one audit text", message_type="text", now=_NOW,
        )

        # Second send — BLOCKED_DUPLICATE
        r2 = OutboundSafetyGate(self.caller_db).attempt(
            wa_id=self.wa_id, thread_id=self.thread_id,
            text="D5 duplicate one audit text", message_type="text",
            now=_NOW + timedelta(minutes=1),
        )
        self.assertEqual(r2.outcome, GateOutcome.BLOCKED_DUPLICATE)

        # Explicit caller commit — should NOT create a second blocked row
        self.caller_db.commit()

        with self._fresh() as verify_db:
            count = verify_db.execute(
                text("""
                    SELECT COUNT(*) FROM whatsapp_messages
                    WHERE thread_id = :tid
                      AND status = 'blocked'
                      AND (blocked_reason LIKE '%DUPLICATE%' OR blocked_reason LIKE '%FLOOD%')
                """),
                {"tid": self.thread_id},
            ).scalar()
            self.assertEqual(
                count, 1,
                "EXACTLY ONE blocked audit row must exist after one blocked gate call "
                "(double-write would indicate gate is still using caller session)",
            )

    def test_d5b_blocked_flood_creates_exactly_one_audit_row(self):
        """BLOCKED_FLOOD also produces exactly one audit row after explicit caller commit."""
        os.environ["OUTBOUND_ENABLED"] = "true"

        for i in range(FLOOD_MAX_MESSAGES):
            OutboundSafetyGate(self.caller_db).attempt(
                wa_id=self.wa_id, thread_id=self.thread_id,
                text=f"D5b flood filler {i}", message_type="text",
                now=_NOW + timedelta(seconds=i),
            )

        r_flood = OutboundSafetyGate(self.caller_db).attempt(
            wa_id=self.wa_id, thread_id=self.thread_id,
            text="D5b flood trigger", message_type="text",
            now=_NOW + timedelta(seconds=FLOOD_MAX_MESSAGES),
        )
        self.assertEqual(r_flood.outcome, GateOutcome.BLOCKED_FLOOD)

        # Explicit caller commit — must not produce a second blocked row
        self.caller_db.commit()

        with self._fresh() as verify_db:
            count = verify_db.execute(
                text("""
                    SELECT COUNT(*) FROM whatsapp_messages
                    WHERE thread_id = :tid
                      AND status = 'blocked'
                      AND blocked_reason LIKE '%FLOOD%'
                """),
                {"tid": self.thread_id},
            ).scalar()
            self.assertEqual(
                count, 1,
                "EXACTLY ONE BLOCKED_FLOOD audit row must exist after explicit caller commit",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
