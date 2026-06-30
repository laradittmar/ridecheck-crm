"""M19.R1.2 — Postgres integration tests for the recipient-level safety gate.

Runs against the ``crm_test`` database (migration 20260629_recipient_lock_rolling_window applied).
Each test creates its own test contacts/threads (prefixed ``TEST_M19R12_``) and
cleans up unconditionally in tearDown.

Safety:
  - DATABASE_URL must target crm_test (not crm).
  - OUTBOUND_ENABLED is controlled per-test; never left as "true" after tearDown.
  - No Meta API calls are made; gate.attempt() is the boundary.
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

# psycopg3 must be imported before any psycopg2 stub.
try:
    import psycopg  # noqa: F401
except ImportError:
    pass
for _pg_mod in ["psycopg2", "psycopg2.extensions"]:
    if _pg_mod not in sys.modules:
        sys.modules[_pg_mod] = types.ModuleType(_pg_mod)

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.models import (
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppOutboundDedup,
    WhatsAppThread,
    WhatsAppThreadState,
)
from app.services.outbound_safety_gate import (
    DEDUP_WINDOW_MINUTES,
    FLOOD_MAX_MESSAGES,
    FLOOD_WINDOW_SECONDS,
    GateOutcome,
    OutboundSafetyGate,
)

_DB_URL = os.environ.get("DATABASE_URL", "")
_POSTGRES = "postgresql" in _DB_URL

_NOW = datetime(2026, 6, 29, 21, 0, 0, tzinfo=timezone.utc)
_PREFIX = "TEST_M19R12_"


def _require_postgres(test_case):
    """Skip test if DATABASE_URL does not point to a Postgres instance."""
    if not _POSTGRES:
        raise unittest.SkipTest("Postgres integration test — requires DATABASE_URL with postgresql://")


def _engine():
    return create_engine(_DB_URL, pool_pre_ping=True)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_recipient(session: Session, suffix: str) -> tuple[str, int, int]:
    wa_id = f"{_PREFIX}{suffix}"
    contact = session.execute(
        text("SELECT id FROM whatsapp_contacts WHERE wa_id = :wa_id"),
        {"wa_id": wa_id},
    ).fetchone()
    if contact is None:
        session.execute(
            text("INSERT INTO whatsapp_contacts (wa_id, display_name, phone, created_at) "
                 "VALUES (:wa_id, 'Test M19R12', '5491100000000', now()) RETURNING id"),
            {"wa_id": wa_id},
        )
        session.flush()
    contact_id = session.execute(
        text("SELECT id FROM whatsapp_contacts WHERE wa_id = :wa_id"), {"wa_id": wa_id}
    ).scalar()

    thread_row = session.execute(
        text("SELECT id FROM whatsapp_threads WHERE contact_id = :cid LIMIT 1"),
        {"cid": contact_id},
    ).fetchone()
    if thread_row is None:
        session.execute(
            text("INSERT INTO whatsapp_threads (contact_id, unread_count, created_at) "
                 "VALUES (:cid, 0, now())"),
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
            text("INSERT INTO whatsapp_thread_states (thread_id, needs_human, created_at, updated_at) "
                 "VALUES (:tid, false, now(), now())"),
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
    """Delete all test rows by TEST_M19R12_ prefix."""
    session.execute(
        text("DELETE FROM whatsapp_outbound_dedup WHERE wa_id LIKE :pfx"),
        {"pfx": f"{_PREFIX}%"},
    )
    session.execute(
        text("DELETE FROM whatsapp_recipient_locks WHERE wa_id LIKE :pfx"),
        {"pfx": f"{_PREFIX}%"},
    )
    # Messages are threaded; delete via thread join.
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
# B.1 — Schema verification
# ══════════════════════════════════════════════════════════════════════════════

class TestPgSchema(unittest.TestCase):
    """B.1 — Verify migration 20260629_recipient_lock_rolling_window applied correctly."""

    def setUp(self):
        _require_postgres(self)
        self._engine = _engine()

    def tearDown(self):
        self._engine.dispose()

    def test_b1_recipient_locks_table_exists(self):
        insp = inspect(self._engine)
        tables = insp.get_table_names()
        self.assertIn("whatsapp_recipient_locks", tables,
                      "whatsapp_recipient_locks table must exist after migration")

    def test_b1_recipient_locks_wa_id_unique(self):
        insp = inspect(self._engine)
        uqs = insp.get_unique_constraints("whatsapp_recipient_locks")
        cols_in_uq = {col for uq in uqs for col in uq["column_names"]}
        # Also check via indexes (Postgres UNIQUE INDEX)
        idxs = insp.get_indexes("whatsapp_recipient_locks")
        cols_in_idx = {col for idx in idxs if idx.get("unique") for col in idx["column_names"]}
        all_unique_cols = cols_in_uq | cols_in_idx
        self.assertIn("wa_id", all_unique_cols,
                      "whatsapp_recipient_locks.wa_id must have a UNIQUE constraint")

    def test_b1_dedup_has_message_kind_column(self):
        insp = inspect(self._engine)
        cols = {c["name"] for c in insp.get_columns("whatsapp_outbound_dedup")}
        self.assertIn("message_kind", cols,
                      "whatsapp_outbound_dedup must have message_kind column (M19.R1.2)")

    def test_b1_dedup_has_no_window_start_column(self):
        insp = inspect(self._engine)
        cols = {c["name"] for c in insp.get_columns("whatsapp_outbound_dedup")}
        self.assertNotIn("window_start", cols,
                         "whatsapp_outbound_dedup must NOT have window_start column (removed in M19.R1.2)")

    def test_b1_dedup_has_no_unique_constraint(self):
        insp = inspect(self._engine)
        uqs = insp.get_unique_constraints("whatsapp_outbound_dedup")
        # Must not have the old uq_outbound_dedup_wa_fp_window constraint or any unique on (wa_id, fp)
        for uq in uqs:
            cols = set(uq["column_names"])
            self.assertFalse(
                {"wa_id", "content_fingerprint"} <= cols and "window_start" not in cols,
                f"Unexpected UNIQUE constraint on whatsapp_outbound_dedup: {uq}",
            )

    def test_b1_dedup_rolling_index_exists(self):
        insp = inspect(self._engine)
        idxs = insp.get_indexes("whatsapp_outbound_dedup")
        found = any(
            set(idx["column_names"]) >= {"wa_id", "message_kind", "content_fingerprint", "created_at"}
            for idx in idxs
        )
        self.assertTrue(found,
                        "ix_dedup_wa_kind_fp_created index must exist on whatsapp_outbound_dedup")

    def test_b1_alembic_head_is_rolling_window(self):
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).fetchone()
        self.assertIsNotNone(row, "alembic_version table must have a row")
        self.assertEqual(
            row[0], "20260629_recipient_lock_rolling_window",
            f"crm_test must be at revision 20260629_recipient_lock_rolling_window, got: {row[0]}",
        )


# ══════════════════════════════════════════════════════════════════════════════
# B.2 — Gate logic against real Postgres
# ══════════════════════════════════════════════════════════════════════════════

class TestPgGateLogic(unittest.TestCase):
    """B.2 — Gate dedup and flood logic against real crm_test Postgres."""

    def setUp(self):
        _require_postgres(self)
        os.environ["OUTBOUND_ENABLED"] = "true"
        self._engine = _engine()
        self.session = Session(self._engine)
        _cleanup(self.session)

    def tearDown(self):
        _cleanup(self.session)
        self.session.close()
        self._engine.dispose()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def test_b2_allowed_sends_create_pending_dedup_lock_rows(self):
        wa_id, thread_id, _ = _make_recipient(self.session, "B2_ALLOW")
        gate = OutboundSafetyGate(self.session)
        r = gate.attempt(wa_id=wa_id, thread_id=thread_id, text="Hola desde test B.2.", now=_NOW)
        self.assertEqual(r.outcome, GateOutcome.ALLOWED)

        # pending message must exist
        msg = self.session.get(WhatsAppMessage, r.message_id)
        self.assertEqual(msg.status, "pending")

        # dedup row must exist
        ded = self.session.query(WhatsAppOutboundDedup).filter_by(wa_id=wa_id).first()
        self.assertIsNotNone(ded)
        self.assertEqual(ded.message_kind, "text")

        # recipient lock row must exist
        lock = self.session.execute(
            text("SELECT id FROM whatsapp_recipient_locks WHERE wa_id = :wa_id"),
            {"wa_id": wa_id},
        ).fetchone()
        self.assertIsNotNone(lock, "whatsapp_recipient_locks must have a row for the recipient")

    def test_b2_rolling_window_dedup_blocks_repeat(self):
        wa_id, thread_id, _ = _make_recipient(self.session, "B2_DEDUP")
        gate = OutboundSafetyGate(self.session)
        text_msg = "Cotización: $130.000 para revisión integral."
        pfx = id(self)

        r1 = gate.attempt(wa_id=wa_id, thread_id=thread_id, text=text_msg, now=_NOW)
        self.assertEqual(r1.outcome, GateOutcome.ALLOWED)
        gate.mark_sent(r1.message_id, f"wamid_b2_dedup_{pfx}")

        r2 = gate.attempt(wa_id=wa_id, thread_id=thread_id, text=text_msg,
                          now=_NOW + timedelta(minutes=5))
        self.assertEqual(r2.outcome, GateOutcome.BLOCKED_DUPLICATE,
                         "Same text within rolling 10-min window must be blocked on Postgres")

    def test_b2_rolling_window_boundary_exact_10min_allowed(self):
        wa_id, thread_id, _ = _make_recipient(self.session, "B2_BOUND")
        gate = OutboundSafetyGate(self.session)
        text_msg = "Boundary test: rolling window."
        pfx = id(self)
        T = _NOW
        T_plus_10 = T + timedelta(minutes=DEDUP_WINDOW_MINUTES)

        r1 = gate.attempt(wa_id=wa_id, thread_id=thread_id, text=text_msg, now=T)
        self.assertEqual(r1.outcome, GateOutcome.ALLOWED)
        gate.mark_sent(r1.message_id, f"wamid_b2_bound_{pfx}")

        # At T+10min: cutoff = T, row.created_at = T → T > T → False → ALLOWED
        r2 = gate.attempt(wa_id=wa_id, thread_id=thread_id, text=text_msg, now=T_plus_10)
        self.assertEqual(r2.outcome, GateOutcome.ALLOWED,
                         "At exactly T+10min the dedup row is at the boundary (strict >) → ALLOWED")
        gate.mark_sent(r2.message_id, f"wamid_b2_bound2_{pfx}")

    def test_b2_flood_gate_blocks_4th_message(self):
        wa_id, thread_id, state_id = _make_recipient(self.session, "B2_FLOOD")
        gate = OutboundSafetyGate(self.session)
        pfx = id(self)
        outcomes = []
        for i in range(4):
            r = gate.attempt(wa_id=wa_id, thread_id=thread_id,
                             text=f"Flood message {pfx}_{i}",
                             now=_NOW + timedelta(seconds=i * 5))
            outcomes.append(r.outcome)
            if r.outcome == GateOutcome.ALLOWED:
                gate.mark_sent(r.message_id, f"wamid_b2_flood_{pfx}_{i}")

        self.assertEqual(outcomes[:3], [GateOutcome.ALLOWED] * 3)
        self.assertEqual(outcomes[3], GateOutcome.BLOCKED_FLOOD)

        # needs_human must be True in Postgres
        state_row = self.session.execute(
            text("SELECT needs_human FROM whatsapp_thread_states WHERE id = :sid"),
            {"sid": state_id},
        ).fetchone()
        self.assertTrue(state_row[0], "needs_human must be True after BLOCKED_FLOOD on Postgres")

    def test_b2_failed_meta_blocks_auto_retry(self):
        wa_id, thread_id, _ = _make_recipient(self.session, "B2_FAIL")
        gate = OutboundSafetyGate(self.session)
        r = gate.attempt(wa_id=wa_id, thread_id=thread_id,
                         text="Auto-retry test message.", now=_NOW)
        self.assertEqual(r.outcome, GateOutcome.ALLOWED)
        gate.mark_failed(r.message_id)

        msg = self.session.get(WhatsAppMessage, r.message_id)
        self.assertEqual(msg.status, "failed")

        r2 = gate.attempt(wa_id=wa_id, thread_id=thread_id,
                          text="Auto-retry test message.", now=_NOW)
        self.assertEqual(r2.outcome, GateOutcome.BLOCKED_DUPLICATE,
                         "Auto-retry within window must be BLOCKED_DUPLICATE on Postgres")

    def test_b2_recipient_lock_on_conflict_do_nothing(self):
        """INSERT ON CONFLICT DO NOTHING for recipient lock is idempotent on Postgres."""
        wa_id, thread_id, _ = _make_recipient(self.session, "B2_LOCK")
        gate = OutboundSafetyGate(self.session)
        pfx = id(self)

        r1 = gate.attempt(wa_id=wa_id, thread_id=thread_id, text=f"Lock test 1 {pfx}", now=_NOW)
        self.assertEqual(r1.outcome, GateOutcome.ALLOWED)
        gate.mark_sent(r1.message_id, f"wamid_b2_lock1_{pfx}")

        # A second attempt on the same wa_id should reuse the lock row silently.
        r2 = gate.attempt(wa_id=wa_id, thread_id=thread_id, text=f"Lock test 2 {pfx}",
                          now=_NOW + timedelta(seconds=5))
        self.assertIn(r2.outcome, (GateOutcome.ALLOWED, GateOutcome.BLOCKED_FLOOD,
                                    GateOutcome.BLOCKED_DUPLICATE),
                      "Second attempt must not crash due to lock row collision")

    def test_b2_message_kind_text_and_flow_separate_dedup_keys(self):
        """text and flow with same body fingerprint are independent dedup keys on Postgres."""
        wa_id, thread_id, _ = _make_recipient(self.session, "B2_KIND")
        gate = OutboundSafetyGate(self.session)
        body = "Completá tus datos para confirmar el turno."
        pfx = id(self)

        r_text = gate.attempt(wa_id=wa_id, thread_id=thread_id,
                              text=body, message_type="text", now=_NOW)
        self.assertEqual(r_text.outcome, GateOutcome.ALLOWED)
        gate.mark_sent(r_text.message_id, f"wamid_b2_kind_text_{pfx}")

        r_flow = gate.attempt(wa_id=wa_id, thread_id=thread_id,
                              text=body, message_type="flow",
                              now=_NOW + timedelta(seconds=3))
        self.assertEqual(r_flow.outcome, GateOutcome.ALLOWED,
                         "Flow message with same body as earlier text must not be deduped (different message_kind)")
        gate.mark_sent(r_flow.message_id, f"wamid_b2_kind_flow_{pfx}")


# ══════════════════════════════════════════════════════════════════════════════
# B.3 — Kill switch on Postgres (isolated session)
# ══════════════════════════════════════════════════════════════════════════════

class TestPgKillSwitch(unittest.TestCase):
    """B.3 — Kill switch creates isolated-session audit record even on Postgres."""

    def setUp(self):
        _require_postgres(self)
        os.environ.pop("OUTBOUND_ENABLED", None)
        self._engine = _engine()
        self.session = Session(self._engine)
        _cleanup(self.session)

    def tearDown(self):
        _cleanup(self.session)
        self.session.close()
        self._engine.dispose()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def test_b3_kill_switch_blocked_record_durable(self):
        wa_id, thread_id, _ = _make_recipient(self.session, "B3_KS")
        gate = OutboundSafetyGate(self.session)
        r = gate.attempt(wa_id=wa_id, thread_id=thread_id,
                         text="Kill switch test.", now=_NOW)
        self.assertEqual(r.outcome, GateOutcome.BLOCKED_KILL_SWITCH)
        self.assertIsNotNone(r.message_id)

        # Verify record was persisted in Postgres via a fresh query.
        msg = self.session.execute(
            text("SELECT status, automated, blocked_reason FROM whatsapp_messages WHERE id = :mid"),
            {"mid": r.message_id},
        ).fetchone()
        self.assertIsNotNone(msg, "kill-switch blocked record must be persisted in Postgres")
        self.assertEqual(msg[0], "blocked")
        self.assertTrue(msg[1])  # automated


if __name__ == "__main__":
    unittest.main(verbosity=2)
