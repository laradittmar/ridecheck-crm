"""M19.R1.2 — PostgreSQL concurrency integration tests for the recipient-level safety gate.

Proves that SELECT ... FOR UPDATE in whatsapp_recipient_locks correctly serialises
concurrent automated sends so that exactly one ALLOWED result emerges from any race,
regardless of whether the lock row pre-exists or is created during the race.

Tests run against the crm_test database.  Each test creates its own contacts/threads
under the TEST_M19R12_ prefix and cleans up unconditionally in tearDown.

Test index:
  A.1  Identical-message race, 8 workers, fresh lock row:
         exactly 1 ALLOWED, 7 BLOCKED_DUPLICATE, 1 pending + 1 dedup + 7 blocked rows.
  A.2  Identical-message race, 8 workers, pre-existing lock row (tests FOR UPDATE path).
  B.1  Distinct-message burst race, 4 workers, fresh lock:
         exactly 3 ALLOWED, 1 BLOCKED_FLOOD, needs_human=True, blocked audit row.
  B.2  Distinct-message burst race, 4 workers, pre-existing lock.
  B.3  Flood-blocked result: message_id has status='blocked', blocked_reason contains FLOOD.
  B.4  No 4th eligible attempt passes after burst (rolling window stays full).
"""
from __future__ import annotations

import multiprocessing
import os
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# psycopg3 must be imported before any psycopg2 stub.
try:
    import psycopg  # noqa: F401
except ImportError:
    pass
for _pg_mod in ["psycopg2", "psycopg2.extensions"]:
    if _pg_mod not in sys.modules:
        sys.modules[_pg_mod] = types.ModuleType(_pg_mod)

for _mod in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models import WhatsAppMessage, WhatsAppOutboundDedup
from app.services.outbound_safety_gate import (
    FLOOD_MAX_MESSAGES,
    GateOutcome,
    OutboundSafetyGate,
)

_DB_URL = os.environ.get("DATABASE_URL", "")
_POSTGRES = "postgresql" in _DB_URL

_NOW = datetime(2026, 6, 29, 22, 0, 0, tzinfo=timezone.utc)
_PREFIX = "TEST_M19R12_"
_WORKER_TIMEOUT = 30  # seconds for barrier.wait() and process.join()


def _require_postgres(test_case):
    if not _POSTGRES:
        raise unittest.SkipTest("Postgres concurrency test — requires DATABASE_URL with postgresql://")


def _mk_engine():
    return create_engine(_DB_URL, pool_size=1, max_overflow=0, pool_pre_ping=True)


def _cleanup(session: Session) -> None:
    for sql, params in [
        ("DELETE FROM whatsapp_outbound_dedup WHERE wa_id LIKE :p", {"p": f"{_PREFIX}%"}),
        ("DELETE FROM whatsapp_recipient_locks WHERE wa_id LIKE :p", {"p": f"{_PREFIX}%"}),
        ("""DELETE FROM whatsapp_messages WHERE thread_id IN (
                SELECT t.id FROM whatsapp_threads t
                JOIN whatsapp_contacts c ON c.id = t.contact_id
                WHERE c.wa_id LIKE :p)""", {"p": f"{_PREFIX}%"}),
        ("""DELETE FROM whatsapp_thread_states WHERE thread_id IN (
                SELECT t.id FROM whatsapp_threads t
                JOIN whatsapp_contacts c ON c.id = t.contact_id
                WHERE c.wa_id LIKE :p)""", {"p": f"{_PREFIX}%"}),
        ("""DELETE FROM whatsapp_threads WHERE contact_id IN (
                SELECT id FROM whatsapp_contacts WHERE wa_id LIKE :p)""", {"p": f"{_PREFIX}%"}),
        ("DELETE FROM whatsapp_contacts WHERE wa_id LIKE :p", {"p": f"{_PREFIX}%"}),
    ]:
        session.execute(text(sql), params)
    session.commit()


def _make_recipient(session: Session, suffix: str) -> tuple[str, int, int]:
    wa_id = f"{_PREFIX}{suffix}"
    session.execute(
        text("INSERT INTO whatsapp_contacts (wa_id, display_name, phone, created_at) "
             "VALUES (:wa_id, 'ConcTest', '5491100000001', now()) "
             "ON CONFLICT (wa_id) DO NOTHING"),
        {"wa_id": wa_id},
    )
    session.flush()
    contact_id = session.execute(
        text("SELECT id FROM whatsapp_contacts WHERE wa_id = :wa_id"), {"wa_id": wa_id}
    ).scalar()
    session.execute(
        text("INSERT INTO whatsapp_threads (contact_id, unread_count, created_at) "
             "VALUES (:cid, 0, now())"),
        {"cid": contact_id},
    )
    session.flush()
    thread_id = session.execute(
        text("SELECT id FROM whatsapp_threads WHERE contact_id = :cid ORDER BY id LIMIT 1"),
        {"cid": contact_id},
    ).scalar()
    session.execute(
        text("INSERT INTO whatsapp_thread_states (thread_id, needs_human, created_at, updated_at) "
             "VALUES (:tid, false, now(), now()) ON CONFLICT (thread_id) DO NOTHING"),
        {"tid": thread_id},
    )
    session.flush()
    state_id = session.execute(
        text("SELECT id FROM whatsapp_thread_states WHERE thread_id = :tid"), {"tid": thread_id}
    ).scalar()
    session.commit()
    return wa_id, thread_id, state_id


# ── Module-level worker function (must be picklable / fork-safe) ──────────────

def _gate_worker(wa_id, thread_id, text_msg, message_type, db_url, barrier, result_queue):
    """Runs in a separate OS process.  Synchronises with peers then calls gate.attempt()."""
    # Set env before doing anything that checks it.
    os.environ["OUTBOUND_ENABLED"] = "true"

    # Each process creates its own engine + session — never shares with parent.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.services.outbound_safety_gate import GateOutcome, OutboundSafetyGate

    engine = create_engine(db_url, pool_size=1, max_overflow=0, pool_pre_ping=True)
    session = Session(engine)

    try:
        # Synchronised start: every worker waits here until all are ready.
        barrier.wait(timeout=_WORKER_TIMEOUT)

        gate = OutboundSafetyGate(session)
        result = gate.attempt(
            wa_id=wa_id, thread_id=thread_id, text=text_msg,
            message_type=message_type, now=_NOW,
        )
        result_queue.put({
            "outcome": result.outcome.value,
            "message_id": result.message_id,
            "blocked_reason": result.blocked_reason or "",
        })
    except Exception as exc:
        result_queue.put({"error": f"{type(exc).__name__}: {exc}"})
    finally:
        try:
            session.close()
        except Exception:
            pass
        try:
            engine.dispose()
        except Exception:
            pass


def _run_workers(
    n_workers: int,
    wa_id: str,
    thread_id: int,
    texts: list[str],
    message_type: str = "text",
    db_url: str = "",
) -> list[dict]:
    """Launch n_workers processes, return list of result dicts.

    Each worker uses texts[i % len(texts)] as its message.
    Raises AssertionError if any worker reports an error or times out.
    """
    ctx = multiprocessing.get_context("fork")
    barrier = ctx.Barrier(n_workers, timeout=_WORKER_TIMEOUT)
    result_queue = ctx.Queue()

    processes = []
    for i in range(n_workers):
        text_msg = texts[i % len(texts)]
        p = ctx.Process(
            target=_gate_worker,
            args=(wa_id, thread_id, text_msg, message_type, db_url, barrier, result_queue),
            daemon=True,
        )
        processes.append(p)

    for p in processes:
        p.start()

    for p in processes:
        p.join(timeout=_WORKER_TIMEOUT)
        if p.is_alive():
            p.terminate()
            raise AssertionError(f"Worker process timed out after {_WORKER_TIMEOUT}s")

    results = []
    while not result_queue.empty():
        results.append(result_queue.get_nowait())

    errors = [r for r in results if "error" in r]
    assert not errors, f"Workers reported errors: {errors}"
    return results


# ══════════════════════════════════════════════════════════════════════════════
# A — Identical-message race
# ══════════════════════════════════════════════════════════════════════════════

class TestPgConcurrencyIdenticalMessage(unittest.TestCase):
    """A — 8 concurrent workers send the same text to the same wa_id.

    Expected: exactly 1 ALLOWED, 7 BLOCKED_DUPLICATE.
    SELECT FOR UPDATE serialises the race; the first worker inserts a dedup claim,
    and all subsequent workers find it and are blocked.
    """

    def setUp(self):
        _require_postgres(self)
        os.environ["OUTBOUND_ENABLED"] = "true"
        self._engine = _mk_engine()
        self.session = Session(self._engine)
        _cleanup(self.session)
        self.wa_id, self.thread_id, _ = _make_recipient(self.session, "CONC_A")
        self.session.close()
        self._engine.dispose()

    def tearDown(self):
        self._engine = _mk_engine()
        self.session = Session(self._engine)
        _cleanup(self.session)
        self.session.close()
        self._engine.dispose()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def _run(self) -> list[dict]:
        return _run_workers(
            n_workers=8,
            wa_id=self.wa_id,
            thread_id=self.thread_id,
            texts=["Para cotizarte la revisión integral necesito el vehículo."],
            message_type="text",
            db_url=_DB_URL,
        )

    def test_a1_fresh_lock_exactly_one_allowed(self):
        """A.1 — Fresh lock row: race of 8 identical sends → exactly 1 ALLOWED."""
        results = self._run()
        self.assertEqual(len(results), 8, f"Expected 8 results, got {len(results)}: {results}")
        outcomes = [r["outcome"] for r in results]
        allowed = outcomes.count(GateOutcome.ALLOWED.value)
        blocked_dup = outcomes.count(GateOutcome.BLOCKED_DUPLICATE.value)
        self.assertEqual(allowed, 1,
                         f"Expected exactly 1 ALLOWED, got {allowed}. Outcomes: {outcomes}")
        self.assertEqual(blocked_dup, 7,
                         f"Expected exactly 7 BLOCKED_DUPLICATE, got {blocked_dup}. Outcomes: {outcomes}")

    def test_a1_fresh_lock_audit_rows(self):
        """A.1 — Exactly 1 pending + 1 dedup claim + 7 blocked audit rows committed."""
        self._run()
        verify_engine = _mk_engine()
        verify_session = Session(verify_engine)
        try:
            pending = verify_session.execute(
                text("SELECT COUNT(*) FROM whatsapp_messages "
                     "WHERE thread_id = :tid AND status = 'pending' AND automated = true"),
                {"tid": self.thread_id},
            ).scalar()
            self.assertEqual(pending, 1, f"Expected 1 pending row, got {pending}")

            blocked = verify_session.execute(
                text("SELECT COUNT(*) FROM whatsapp_messages "
                     "WHERE thread_id = :tid AND status = 'blocked' AND automated = true"),
                {"tid": self.thread_id},
            ).scalar()
            self.assertEqual(blocked, 7, f"Expected 7 blocked rows, got {blocked}")

            dedup = verify_session.execute(
                text("SELECT COUNT(*) FROM whatsapp_outbound_dedup WHERE wa_id = :wa_id"),
                {"wa_id": self.wa_id},
            ).scalar()
            self.assertEqual(dedup, 1, f"Expected 1 dedup claim, got {dedup}")
        finally:
            verify_session.close()
            verify_engine.dispose()

    def test_a2_preexisting_lock_exactly_one_allowed(self):
        """A.2 — Pre-existing lock row: proves FOR UPDATE serialises, not just INSERT path."""
        # Pre-insert the recipient lock row before the race.
        self._engine = _mk_engine()
        self.session = Session(self._engine)
        self.session.execute(
            text("INSERT INTO whatsapp_recipient_locks (wa_id, created_at) "
                 "VALUES (:wa_id, now()) ON CONFLICT (wa_id) DO NOTHING"),
            {"wa_id": self.wa_id},
        )
        self.session.commit()
        self.session.close()
        self._engine.dispose()

        results = self._run()
        self.assertEqual(len(results), 8)
        outcomes = [r["outcome"] for r in results]
        allowed = outcomes.count(GateOutcome.ALLOWED.value)
        blocked_dup = outcomes.count(GateOutcome.BLOCKED_DUPLICATE.value)
        self.assertEqual(allowed, 1,
                         f"Pre-existing lock: expected 1 ALLOWED, got {allowed}. Outcomes: {outcomes}")
        self.assertEqual(blocked_dup, 7,
                         f"Pre-existing lock: expected 7 BLOCKED_DUPLICATE, got {blocked_dup}. Outcomes: {outcomes}")


# ══════════════════════════════════════════════════════════════════════════════
# B — Distinct-message burst race
# ══════════════════════════════════════════════════════════════════════════════

class TestPgConcurrencyDistinctBurst(unittest.TestCase):
    """B — 4 concurrent workers send 4 distinct texts to the same wa_id in 60s.

    Expected: exactly 3 ALLOWED, 1 BLOCKED_FLOOD.
    The FOR UPDATE lock serialises the flood count; the first 3 workers each see
    a count below FLOOD_MAX_MESSAGES (3), insert their dedup rows, and are allowed.
    The 4th worker sees count = 3 and is blocked.
    """

    def setUp(self):
        _require_postgres(self)
        os.environ["OUTBOUND_ENABLED"] = "true"
        self._engine = _mk_engine()
        self.session = Session(self._engine)
        _cleanup(self.session)
        self.wa_id, self.thread_id, self.state_id = _make_recipient(self.session, "CONC_B")
        self.session.close()
        self._engine.dispose()

    def tearDown(self):
        self._engine = _mk_engine()
        self.session = Session(self._engine)
        _cleanup(self.session)
        self.session.close()
        self._engine.dispose()
        os.environ.pop("OUTBOUND_ENABLED", None)

    _DISTINCT_TEXTS = [
        f"Cotización automática: tu revisión integral cuesta $130.000 + traslado.",
        f"Para confirmar el turno necesito tu dirección completa por favor.",
        f"¿Cuál es el mejor día de la semana para la revisión de tu vehículo?",
        f"Podemos ofrecerte el próximo lunes a las 10:00 hs. ¿Te viene bien?",
    ]

    def _run(self) -> list[dict]:
        return _run_workers(
            n_workers=4,
            wa_id=self.wa_id,
            thread_id=self.thread_id,
            texts=self._DISTINCT_TEXTS,
            message_type="text",
            db_url=_DB_URL,
        )

    def test_b1_fresh_lock_three_allowed_one_flood(self):
        """B.1 — Fresh lock: 3 ALLOWED, 1 BLOCKED_FLOOD from 4 distinct messages."""
        self.assertEqual(FLOOD_MAX_MESSAGES, 3, "Test assumes FLOOD_MAX_MESSAGES == 3")
        results = self._run()
        self.assertEqual(len(results), 4, f"Expected 4 results, got {len(results)}: {results}")
        outcomes = [r["outcome"] for r in results]
        allowed = outcomes.count(GateOutcome.ALLOWED.value)
        flood = outcomes.count(GateOutcome.BLOCKED_FLOOD.value)
        self.assertEqual(allowed, 3,
                         f"Expected 3 ALLOWED, got {allowed}. Outcomes: {outcomes}")
        self.assertEqual(flood, 1,
                         f"Expected 1 BLOCKED_FLOOD, got {flood}. Outcomes: {outcomes}")

    def test_b1_flood_blocked_sets_needs_human(self):
        """B.1 — The BLOCKED_FLOOD result sets needs_human=True on the thread state."""
        results = self._run()
        flood_results = [r for r in results if r.get("outcome") == GateOutcome.BLOCKED_FLOOD.value]
        self.assertEqual(len(flood_results), 1, "Expected exactly one BLOCKED_FLOOD")

        verify_engine = _mk_engine()
        verify_session = Session(verify_engine)
        try:
            nh = verify_session.execute(
                text("SELECT needs_human FROM whatsapp_thread_states WHERE id = :sid"),
                {"sid": self.state_id},
            ).scalar()
            self.assertTrue(nh, "needs_human must be True after BLOCKED_FLOOD")
        finally:
            verify_session.close()
            verify_engine.dispose()

    def test_b2_preexisting_lock_three_allowed_one_flood(self):
        """B.2 — Pre-existing lock row: same result as B.1, proving FOR UPDATE path."""
        self._engine = _mk_engine()
        self.session = Session(self._engine)
        self.session.execute(
            text("INSERT INTO whatsapp_recipient_locks (wa_id, created_at) "
                 "VALUES (:wa_id, now()) ON CONFLICT (wa_id) DO NOTHING"),
            {"wa_id": self.wa_id},
        )
        self.session.commit()
        self.session.close()
        self._engine.dispose()

        results = self._run()
        outcomes = [r["outcome"] for r in results]
        self.assertEqual(outcomes.count(GateOutcome.ALLOWED.value), 3,
                         f"Pre-existing lock: expected 3 ALLOWED. Outcomes: {outcomes}")
        self.assertEqual(outcomes.count(GateOutcome.BLOCKED_FLOOD.value), 1,
                         f"Pre-existing lock: expected 1 BLOCKED_FLOOD. Outcomes: {outcomes}")

    def test_b3_blocked_flood_audit_row_status_and_reason(self):
        """B.3 — BLOCKED_FLOOD result: blocked audit row with FLOOD in reason."""
        results = self._run()
        flood = next((r for r in results if r.get("outcome") == GateOutcome.BLOCKED_FLOOD.value), None)
        self.assertIsNotNone(flood, "Expected a BLOCKED_FLOOD result")

        verify_engine = _mk_engine()
        verify_session = Session(verify_engine)
        try:
            msg = verify_session.execute(
                text("SELECT status, blocked_reason FROM whatsapp_messages WHERE id = :mid"),
                {"mid": flood["message_id"]},
            ).fetchone()
            self.assertIsNotNone(msg, "BLOCKED_FLOOD audit row must exist in DB")
            self.assertEqual(msg[0], "blocked", f"Expected status='blocked', got {msg[0]!r}")
            self.assertIn("FLOOD", msg[1] or "", f"blocked_reason must contain FLOOD: {msg[1]!r}")
        finally:
            verify_session.close()
            verify_engine.dispose()

    def test_b4_no_fifth_message_passes_after_burst(self):
        """B.4 — After the burst completes, a 5th distinct message is also blocked (flood still full)."""
        # Run initial burst (fills the flood window).
        self._run()

        verify_engine = _mk_engine()
        verify_session = Session(verify_engine)
        try:
            os.environ["OUTBOUND_ENABLED"] = "true"
            gate = OutboundSafetyGate(verify_session)
            fifth = gate.attempt(
                wa_id=self.wa_id,
                thread_id=self.thread_id,
                text="Un quinto mensaje diferente que no debería pasar.",
                now=_NOW,
            )
            self.assertEqual(
                fifth.outcome, GateOutcome.BLOCKED_FLOOD,
                f"5th distinct message in 60s should still be BLOCKED_FLOOD, got {fifth.outcome}",
            )
        finally:
            verify_session.close()
            verify_engine.dispose()


# ══════════════════════════════════════════════════════════════════════════════
# C — Recipient lock serialisation proof
# ══════════════════════════════════════════════════════════════════════════════

class TestPgRecipientLockProof(unittest.TestCase):
    """C — Prove SELECT FOR UPDATE prevents any interleaving across concurrent identical sends.

    The whatsapp_recipient_locks.wa_id UNIQUE index ensures at most one row exists per
    recipient.  INSERT-OR-IGNORE + SELECT FOR UPDATE means:

      1. The first transaction to INSERT creates the row and immediately holds FOR UPDATE.
      2. All other transactions block at INSERT (waiting for the first to commit) then
         proceed to SELECT FOR UPDATE (which succeeds immediately because the first has
         already committed and released its lock).
      3. No two transactions can hold the lock simultaneously.

    Net effect: full serialisation of gate.attempt() calls per recipient.
    """

    def setUp(self):
        _require_postgres(self)
        os.environ["OUTBOUND_ENABLED"] = "true"
        self._engine = _mk_engine()
        self.session = Session(self._engine)
        _cleanup(self.session)
        self.wa_id_fresh, self.thread_id_fresh, _ = _make_recipient(self.session, "LOCK_FRESH")
        self.wa_id_pre, self.thread_id_pre, _ = _make_recipient(self.session, "LOCK_PRE")
        # Pre-insert lock for the _pre variant.
        self.session.execute(
            text("INSERT INTO whatsapp_recipient_locks (wa_id, created_at) "
                 "VALUES (:wa_id, now()) ON CONFLICT (wa_id) DO NOTHING"),
            {"wa_id": self.wa_id_pre},
        )
        self.session.commit()
        self.session.close()
        self._engine.dispose()

    def tearDown(self):
        self._engine = _mk_engine()
        self.session = Session(self._engine)
        _cleanup(self.session)
        self.session.close()
        self._engine.dispose()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def _assert_one_allowed(self, wa_id, thread_id, n_workers=6, label=""):
        results = _run_workers(
            n_workers=n_workers, wa_id=wa_id, thread_id=thread_id,
            texts=["Prueba de concurrencia: mismo mensaje."],
            message_type="text", db_url=_DB_URL,
        )
        outcomes = [r.get("outcome") for r in results]
        allowed = outcomes.count(GateOutcome.ALLOWED.value)
        self.assertEqual(
            allowed, 1,
            f"{label} expected exactly 1 ALLOWED out of {n_workers} concurrent workers. "
            f"Outcomes: {outcomes}. Results: {results}",
        )

    def test_c1_fresh_lock_serialises_6_workers(self):
        """C.1 — 6 concurrent identical sends, fresh lock row: exactly 1 ALLOWED."""
        self._assert_one_allowed(self.wa_id_fresh, self.thread_id_fresh, n_workers=6,
                                 label="C.1 fresh lock")

    def test_c2_preexisting_lock_serialises_6_workers(self):
        """C.2 — 6 concurrent identical sends, pre-existing lock row: exactly 1 ALLOWED.

        This specifically tests that the FOR UPDATE path (not the INSERT path) provides
        the serialisation guarantee when the lock row already exists.
        """
        self._assert_one_allowed(self.wa_id_pre, self.thread_id_pre, n_workers=6,
                                 label="C.2 pre-existing lock")

    def test_c3_lock_row_exists_after_first_race(self):
        """C.3 — After any concurrent race, exactly one lock row exists for the wa_id."""
        _run_workers(
            n_workers=4, wa_id=self.wa_id_fresh, thread_id=self.thread_id_fresh,
            texts=["Lock existence check."], message_type="text", db_url=_DB_URL,
        )
        verify_engine = _mk_engine()
        verify_session = Session(verify_engine)
        try:
            count = verify_session.execute(
                text("SELECT COUNT(*) FROM whatsapp_recipient_locks WHERE wa_id = :wa_id"),
                {"wa_id": self.wa_id_fresh},
            ).scalar()
            self.assertEqual(count, 1, f"Expected exactly 1 lock row, got {count}")
        finally:
            verify_session.close()
            verify_engine.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=2)
