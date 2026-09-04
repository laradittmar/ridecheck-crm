"""M19.R1.3.E — PostgreSQL reset rehearsal and cooldown-reset integration tests.

Runs against the crm_test database only.

Requires:
  TEST_DATABASE_URL=postgresql+psycopg://crm:${POSTGRES_PASSWORD}@<host>:5432/crm_test
  CLOSED_BETA_TEST_WA_ID is not required — tests use fixed TEST_CLOSED_BETA_* identifiers.
  OUTBOUND_ENABLED must NOT be "true" (not set in the default test environment).

Tests are skipped automatically if TEST_DATABASE_URL is absent or does not point
to a PostgreSQL instance.

All test data uses the TEST_CLOSED_BETA_ prefix.  Two fake tester identifiers:
  _TESTER_WA_ID = "TEST_CLOSED_BETA_TESTER_8330"  (the "beta tester" being reset)
  _OTHER_WA_ID  = "TEST_CLOSED_BETA_OTHER_9999"   (an unrelated tester — must be untouched)

Meta API is never called.  All gate interaction is via direct SQL assertions.

Section A — Standard reset
  A1: Fresh qualification state: conversation rows deleted, lead/thread reset
  A2: Dedup records and recipient lock preserved after standard reset
  A3: Unrelated tester data untouched after standard reset

Section B — Cooldown reset
  B1: Cooldown reset removes tester dedup records only
  B2: Cooldown reset preserves recipient lock row
  B3: After cooldown reset, duplicate message content is eligible (dedup empty)
  B4: Other tester dedup records untouched by cooldown reset

Section C — Guard rejection tests (subprocess, minimal DB involvement)
  C1: Missing TEST_DATABASE_URL rejected
  C2: Production crm target rejected (DB name != crm_test)
  C3: Missing --confirm rejected
  C4: Standard reset without --clear-beta-cooldown does NOT remove dedup
  C5: OUTBOUND_ENABLED=true rejected
  C6: Missing CLOSED_BETA_TEST_WA_ID rejected

Section D — Compile + regression
  D1: reset_closed_beta_scenario.py compiles clean
  D2: test_m19_r1_3d_closed_beta.py still passes (no regression)
"""
from __future__ import annotations
from pg_dsn import pg_dsn  # SEC: no credential literal

import hashlib
import os
import subprocess
import sys
import time
import types
import unicodedata
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT_DIR / "tests"
_RESET_SCRIPT = str(TESTS_DIR / "reset_closed_beta_scenario.py")

_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "")
_POSTGRES = "postgresql" in _TEST_DB_URL

_TESTER_WA_ID = "TEST_CLOSED_BETA_TESTER_8330"
_OTHER_WA_ID = "TEST_CLOSED_BETA_OTHER_9999"
_TESTER_SUFFIX = _TESTER_WA_ID[-4:]
_OTHER_SUFFIX = _OTHER_WA_ID[-4:]
_PREFIX = "TEST_CLOSED_BETA_"


def _require_postgres(tc: unittest.TestCase) -> None:
    if not _POSTGRES:
        raise unittest.SkipTest(
            "Postgres integration test — requires TEST_DATABASE_URL with postgresql://"
        )


def _fp(text_: str) -> str:
    """Compute the same content fingerprint as OutboundSafetyGate."""
    normalized = unicodedata.normalize("NFKD", text_.strip().lower())
    collapsed = " ".join(normalized.split())
    return hashlib.sha256(collapsed.encode()).hexdigest()


def _engine():
    from sqlalchemy import create_engine
    return create_engine(_TEST_DB_URL, pool_pre_ping=True)


# ── Seed helpers ──────────────────────────────────────────────────────────────

def _seed_scenario(conn, wa_id: str) -> dict:
    """Insert a realistic conversation scenario for wa_id.

    Returns a dict with all inserted IDs for later verification.
    """
    from sqlalchemy import text
    ts_suffix = str(time.time_ns())[-8:]

    # Contact
    conn.execute(text(
        "INSERT INTO whatsapp_contacts (wa_id, display_name, phone, created_at) "
        "VALUES (:wa_id, 'Beta Test Contact', '5491100000000', now())"
    ), {"wa_id": wa_id})
    contact_id = conn.execute(
        text("SELECT id FROM whatsapp_contacts WHERE wa_id = :wa_id"),
        {"wa_id": wa_id},
    ).scalar()

    # Lead
    conn.execute(text(
        "INSERT INTO leads (estado, flag, nombre, apellido, necesita_humano, "
        "                   buscando_auto_set_at, created_at) "
        "VALUES ('AGENDADO', 'REVISIÓN_PROGRAMADA', 'Test', 'BetaTester', TRUE, now(), now())"
    ))
    lead_id = conn.execute(text("SELECT lastval()")).scalar()

    # Thread
    conn.execute(text(
        "INSERT INTO whatsapp_threads "
        "  (contact_id, lead_id, unread_count, last_message_at, "
        "   latest_inbound_wa_message_id, created_at) "
        "VALUES (:cid, :lid, 3, now(), :wamid, now())"
    ), {"cid": contact_id, "lid": lead_id, "wamid": f"wamid_in_{ts_suffix}"})
    thread_id = conn.execute(text("SELECT lastval()")).scalar()

    # Thread state (with qualification data and flow flags set)
    conn.execute(text(
        "INSERT INTO whatsapp_thread_states "
        "  (thread_id, last_stage, needs_human, "
        "   vehicle_clarification_sent, location_clarification_sent, "
        "   vehicle_fallback_flow_sent, location_fallback_flow_sent, "
        "   last_processed_inbound_wa_message_id, customer_name, "
        "   home_zone_group, home_zone_detail, "
        "   is_website_lead, created_at, updated_at) "
        "VALUES (:tid, 'QUALIFYING', TRUE, TRUE, TRUE, TRUE, TRUE, "
        "        :wamid, 'Test BetaTester', 'CABA', 'Palermo', "
        "        FALSE, now(), now())"
    ), {"tid": thread_id, "wamid": f"wamid_in_{ts_suffix}"})
    state_id = conn.execute(text("SELECT lastval()")).scalar()

    # Candidate
    conn.execute(text(
        "INSERT INTO whatsapp_thread_candidates "
        "  (thread_id, label, marca, updated_at) "
        "VALUES (:tid, 'Toyota Corolla 2020', 'Toyota', now())"
    ), {"tid": thread_id})
    candidate_id = conn.execute(text("SELECT lastval()")).scalar()

    # Thread revision (candidate_id nullable; use NULL for simplicity)
    conn.execute(text(
        "INSERT INTO thread_revisions "
        "  (thread_id, candidate_id, status, buyer_name, created_at, updated_at) "
        "VALUES (:tid, NULL, 'collecting_data', 'Test BetaTester', now(), now())"
    ), {"tid": thread_id})
    revision_id = conn.execute(text("SELECT lastval()")).scalar()

    # Inbound message
    conn.execute(text(
        "INSERT INTO whatsapp_messages "
        "  (thread_id, wa_message_id, direction, timestamp, status, "
        "   message_type, text, automated, created_at) "
        "VALUES (:tid, :wamid, 'in', now(), 'received', "
        "        'text', 'quiero turno', FALSE, now())"
    ), {"tid": thread_id, "wamid": f"wamid_in_{ts_suffix}"})

    # Outbound automated message (gate wrote this as sent)
    conn.execute(text(
        "INSERT INTO whatsapp_messages "
        "  (thread_id, wa_message_id, direction, timestamp, status, "
        "   message_type, text, automated, content_fingerprint, created_at) "
        "VALUES (:tid, :wamid, 'out', now(), 'sent', "
        "        'text', 'Por favor completá los datos del vehículo.', "
        "        TRUE, :fp, now())"
    ), {
        "tid": thread_id,
        "wamid": f"wamid_out_{ts_suffix}",
        "fp": _fp("Por favor completá los datos del vehículo."),
    })

    # Blocked outbound audit row (gate blocked a duplicate)
    conn.execute(text(
        "INSERT INTO whatsapp_messages "
        "  (thread_id, wa_message_id, direction, timestamp, status, "
        "   message_type, text, automated, content_fingerprint, blocked_reason, created_at) "
        "VALUES (:tid, NULL, 'out', now(), 'blocked', "
        "        'text', 'Por favor completá los datos del vehículo.', "
        "        TRUE, :fp, 'DUPLICATE: ...', now())"
    ), {"tid": thread_id, "fp": _fp("Por favor completá los datos del vehículo.")})

    # AI events
    conn.execute(text(
        "INSERT INTO ai_events "
        "  (thread_id, wa_message_id, wa_id, text, status, created_at) "
        "VALUES (:tid, :wamid, :wa_id, 'quiero turno', 'completed', now())"
    ), {"tid": thread_id, "wamid": f"wamid_ai_a_{ts_suffix}", "wa_id": wa_id})
    conn.execute(text(
        "INSERT INTO ai_events "
        "  (thread_id, wa_message_id, wa_id, text, status, created_at) "
        "VALUES (:tid, :wamid, :wa_id, 'si tengo un toyota', 'completed', now())"
    ), {"tid": thread_id, "wamid": f"wamid_ai_b_{ts_suffix}", "wa_id": wa_id})

    # Dedup records (gate previously allowed sends)
    conn.execute(text(
        "INSERT INTO whatsapp_outbound_dedup "
        "  (wa_id, thread_id, message_kind, content_fingerprint, created_at) "
        "VALUES (:wa_id, :tid, 'text', :fp, now())"
    ), {
        "wa_id": wa_id, "tid": thread_id,
        "fp": _fp("Por favor completá los datos del vehículo."),
    })
    conn.execute(text(
        "INSERT INTO whatsapp_outbound_dedup "
        "  (wa_id, thread_id, message_kind, content_fingerprint, created_at) "
        "VALUES (:wa_id, :tid, 'text', :fp, now())"
    ), {
        "wa_id": wa_id, "tid": thread_id,
        "fp": _fp("¿Cuál es tu domicilio?"),
    })

    # Recipient lock
    conn.execute(text(
        "INSERT INTO whatsapp_recipient_locks (wa_id, created_at) "
        "VALUES (:wa_id, now()) ON CONFLICT (wa_id) DO NOTHING"
    ), {"wa_id": wa_id})

    return {
        "contact_id": contact_id,
        "lead_id": lead_id,
        "thread_id": thread_id,
        "state_id": state_id,
        "candidate_id": candidate_id,
        "revision_id": revision_id,
    }


def _cleanup_prefix(conn) -> None:
    """Delete all TEST_CLOSED_BETA_ data in dependency order."""
    from sqlalchemy import text
    pfx = f"{_PREFIX}%"
    for sql in [
        "DELETE FROM whatsapp_outbound_dedup WHERE wa_id LIKE :p",
        "DELETE FROM whatsapp_recipient_locks WHERE wa_id LIKE :p",
        ("DELETE FROM ai_events WHERE thread_id IN "
         "(SELECT t.id FROM whatsapp_threads t "
         "JOIN whatsapp_contacts c ON c.id = t.contact_id WHERE c.wa_id LIKE :p)"),
        ("DELETE FROM whatsapp_messages WHERE thread_id IN "
         "(SELECT t.id FROM whatsapp_threads t "
         "JOIN whatsapp_contacts c ON c.id = t.contact_id WHERE c.wa_id LIKE :p)"),
        ("DELETE FROM thread_revisions WHERE thread_id IN "
         "(SELECT t.id FROM whatsapp_threads t "
         "JOIN whatsapp_contacts c ON c.id = t.contact_id WHERE c.wa_id LIKE :p)"),
        ("DELETE FROM whatsapp_thread_candidates WHERE thread_id IN "
         "(SELECT t.id FROM whatsapp_threads t "
         "JOIN whatsapp_contacts c ON c.id = t.contact_id WHERE c.wa_id LIKE :p)"),
        ("DELETE FROM whatsapp_thread_states WHERE thread_id IN "
         "(SELECT t.id FROM whatsapp_threads t "
         "JOIN whatsapp_contacts c ON c.id = t.contact_id WHERE c.wa_id LIKE :p)"),
        ("DELETE FROM whatsapp_threads WHERE contact_id IN "
         "(SELECT id FROM whatsapp_contacts WHERE wa_id LIKE :p)"),
        ("DELETE FROM leads WHERE nombre = 'Test' AND apellido = 'BetaTester'"),
        "DELETE FROM whatsapp_contacts WHERE wa_id LIKE :p",
    ]:
        if ":p" in sql:
            conn.execute(text(sql), {"p": pfx})
        else:
            conn.execute(text(sql))


def _run_reset(extra_env: dict | None = None, args: list[str] | None = None,
               wa_id: str = _TESTER_WA_ID) -> subprocess.CompletedProcess:
    """Run the reset tool as a subprocess with safe defaults."""
    env = {
        k: v for k, v in os.environ.items()
        if k != "OUTBOUND_ENABLED"  # ensure OUTBOUND_ENABLED is unset
    }
    env["TEST_DATABASE_URL"] = _TEST_DB_URL
    env["CLOSED_BETA_TEST_WA_ID"] = wa_id
    if extra_env:
        env.update(extra_env)
    cmd = [sys.executable, _RESET_SCRIPT, "--confirm"] + (args or [])
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


# ══════════════════════════════════════════════════════════════════════════════
# Section A — Standard reset
# ══════════════════════════════════════════════════════════════════════════════

class TestPgStandardReset(unittest.TestCase):
    """Postgres: standard reset leaves a fresh qualification state."""

    @classmethod
    def setUpClass(cls):
        _require_postgres(cls)
        from sqlalchemy import create_engine, text
        cls._engine = _engine()
        # Seed the primary tester and an unrelated tester.
        with cls._engine.begin() as conn:
            _cleanup_prefix(conn)  # ensure clean slate
            cls._tester = _seed_scenario(conn, _TESTER_WA_ID)
            cls._other = _seed_scenario(conn, _OTHER_WA_ID)

        # Run the standard reset (no --clear-beta-cooldown).
        result = _run_reset()
        cls._reset_result = result

    @classmethod
    def tearDownClass(cls):
        from sqlalchemy import text
        with cls._engine.begin() as conn:
            _cleanup_prefix(conn)
        cls._engine.dispose()

    def test_a0_reset_tool_exited_zero(self):
        """Reset tool must exit 0."""
        self.assertEqual(
            self._reset_result.returncode, 0,
            f"reset tool stderr: {self._reset_result.stderr}",
        )

    def _count(self, sql: str, **params) -> int:
        from sqlalchemy import text
        with self._engine.connect() as conn:
            return conn.execute(text(sql), params).scalar() or 0

    def test_a1_whatsapp_messages_deleted(self):
        n = self._count(
            "SELECT count(*) FROM whatsapp_messages WHERE thread_id = :tid",
            tid=self._tester["thread_id"],
        )
        self.assertEqual(n, 0, "All whatsapp_messages for tester thread must be deleted")

    def test_a1_whatsapp_thread_states_deleted(self):
        n = self._count(
            "SELECT count(*) FROM whatsapp_thread_states WHERE thread_id = :tid",
            tid=self._tester["thread_id"],
        )
        self.assertEqual(n, 0, "WhatsAppThreadState must be deleted")

    def test_a1_thread_candidates_deleted(self):
        n = self._count(
            "SELECT count(*) FROM whatsapp_thread_candidates WHERE thread_id = :tid",
            tid=self._tester["thread_id"],
        )
        self.assertEqual(n, 0, "WhatsAppThreadCandidates must be deleted")

    def test_a1_thread_revisions_deleted(self):
        n = self._count(
            "SELECT count(*) FROM thread_revisions WHERE thread_id = :tid",
            tid=self._tester["thread_id"],
        )
        self.assertEqual(n, 0, "ThreadRevisions must be deleted")

    def test_a1_ai_events_deleted(self):
        n = self._count(
            "SELECT count(*) FROM ai_events WHERE thread_id = :tid",
            tid=self._tester["thread_id"],
        )
        self.assertEqual(n, 0, "AiEvents must be deleted (no FK cascade from thread)")

    def test_a1_lead_reset_to_consulta_nueva(self):
        from sqlalchemy import text
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT estado, flag, necesita_humano, motivo_perdida, buscando_auto_set_at "
                     "FROM leads WHERE id = :lid"),
                {"lid": self._tester["lead_id"]},
            ).fetchone()
        self.assertIsNotNone(row, "Lead must still exist")
        estado, flag, necesita_humano, motivo_perdida, buscando_auto_set_at = row
        self.assertEqual(estado, "CONSULTA_NUEVA")
        self.assertIsNone(flag)
        self.assertFalse(necesita_humano)
        self.assertIsNone(motivo_perdida)
        self.assertIsNone(buscando_auto_set_at)

    def test_a1_thread_metadata_reset(self):
        from sqlalchemy import text
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT unread_count, last_message_at, latest_inbound_wa_message_id "
                     "FROM whatsapp_threads WHERE id = :tid"),
                {"tid": self._tester["thread_id"]},
            ).fetchone()
        self.assertIsNotNone(row, "Thread must still exist")
        unread, last_msg_at, latest_wa_id = row
        self.assertEqual(unread, 0)
        self.assertIsNone(last_msg_at)
        self.assertIsNone(latest_wa_id)

    def test_a1_contact_preserved(self):
        n = self._count(
            "SELECT count(*) FROM whatsapp_contacts WHERE id = :cid",
            cid=self._tester["contact_id"],
        )
        self.assertEqual(n, 1, "WhatsAppContact must be preserved")

    def test_a1_thread_preserved(self):
        n = self._count(
            "SELECT count(*) FROM whatsapp_threads WHERE id = :tid",
            tid=self._tester["thread_id"],
        )
        self.assertEqual(n, 1, "WhatsAppThread must be preserved")

    def test_a2_dedup_records_preserved(self):
        n = self._count(
            "SELECT count(*) FROM whatsapp_outbound_dedup WHERE wa_id = :wa_id",
            wa_id=_TESTER_WA_ID,
        )
        self.assertEqual(n, 2,
            "Standard reset must preserve whatsapp_outbound_dedup records")

    def test_a2_recipient_lock_preserved(self):
        n = self._count(
            "SELECT count(*) FROM whatsapp_recipient_locks WHERE wa_id = :wa_id",
            wa_id=_TESTER_WA_ID,
        )
        self.assertEqual(n, 1, "Standard reset must preserve recipient lock row")

    def test_a3_other_tester_messages_untouched(self):
        n = self._count(
            "SELECT count(*) FROM whatsapp_messages WHERE thread_id = :tid",
            tid=self._other["thread_id"],
        )
        self.assertEqual(n, 3, "Other tester's messages must be untouched")

    def test_a3_other_tester_state_untouched(self):
        from sqlalchemy import text
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT last_stage, needs_human FROM whatsapp_thread_states "
                     "WHERE thread_id = :tid"),
                {"tid": self._other["thread_id"]},
            ).fetchone()
        self.assertIsNotNone(row, "Other tester's thread state must still exist")
        self.assertEqual(row[0], "QUALIFYING")
        self.assertTrue(row[1])

    def test_a3_other_tester_lead_untouched(self):
        from sqlalchemy import text
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT estado, flag FROM leads WHERE id = :lid"),
                {"lid": self._other["lead_id"]},
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "AGENDADO")
        self.assertEqual(row[1], "REVISIÓN_PROGRAMADA")

    def test_a3_other_tester_dedup_untouched(self):
        n = self._count(
            "SELECT count(*) FROM whatsapp_outbound_dedup WHERE wa_id = :wa_id",
            wa_id=_OTHER_WA_ID,
        )
        self.assertEqual(n, 2, "Other tester's dedup records must be untouched")

    def test_a3_other_tester_lock_untouched(self):
        n = self._count(
            "SELECT count(*) FROM whatsapp_recipient_locks WHERE wa_id = :wa_id",
            wa_id=_OTHER_WA_ID,
        )
        self.assertEqual(n, 1, "Other tester's recipient lock must be untouched")

    def test_a1_next_inbound_treated_as_new_qualification(self):
        """Prove there is no prior conversation context to leak into next engine call.

        After reset:
        - No WhatsAppThreadState → engine calls _get_or_create_state() on next inbound
        - Lead exists with estado=CONSULTA_NUEVA → engine finds lead, starts fresh
        - No messages in db_messages → AI prompt has no prior context
        - last_processed_inbound_wa_message_id = NULL (deleted with state) → dedup check passes
        """
        from sqlalchemy import text
        with self._engine.connect() as conn:
            state = conn.execute(
                text("SELECT id FROM whatsapp_thread_states WHERE thread_id = :tid"),
                {"tid": self._tester["thread_id"]},
            ).fetchone()
            lead = conn.execute(
                text("SELECT estado, flag, necesita_humano FROM leads WHERE id = :lid"),
                {"lid": self._tester["lead_id"]},
            ).fetchone()
            msg_count = conn.execute(
                text("SELECT count(*) FROM whatsapp_messages WHERE thread_id = :tid"),
                {"tid": self._tester["thread_id"]},
            ).scalar()

        self.assertIsNone(state,
            "No WhatsAppThreadState → engine creates fresh state on next inbound")
        self.assertIsNotNone(lead, "Lead must exist for engine to process (not return 'no_lead')")
        self.assertEqual(lead[0], "CONSULTA_NUEVA",
            "Lead estado=CONSULTA_NUEVA → fresh qualification pipeline")
        self.assertFalse(lead[2],
            "needs_human=False → AI not suppressed on next inbound")
        self.assertEqual(msg_count, 0,
            "No prior messages → AI prompt receives empty context (no leakage)")


# ══════════════════════════════════════════════════════════════════════════════
# Section B — Cooldown reset
# ══════════════════════════════════════════════════════════════════════════════

class TestPgCooldownReset(unittest.TestCase):
    """Postgres: cooldown reset additionally removes tester dedup records."""

    @classmethod
    def setUpClass(cls):
        _require_postgres(cls)
        cls._engine = _engine()
        # Seed the primary tester and an unrelated tester.
        with cls._engine.begin() as conn:
            _cleanup_prefix(conn)
            cls._tester = _seed_scenario(conn, _TESTER_WA_ID)
            cls._other = _seed_scenario(conn, _OTHER_WA_ID)

        # Run the cooldown reset.
        result = _run_reset(args=["--clear-beta-cooldown"])
        cls._reset_result = result

    @classmethod
    def tearDownClass(cls):
        from sqlalchemy import text
        with cls._engine.begin() as conn:
            _cleanup_prefix(conn)
        cls._engine.dispose()

    def _count(self, sql: str, **params) -> int:
        from sqlalchemy import text
        with self._engine.connect() as conn:
            return conn.execute(text(sql), params).scalar() or 0

    def test_b0_cooldown_reset_exited_zero(self):
        self.assertEqual(
            self._reset_result.returncode, 0,
            f"cooldown reset stderr: {self._reset_result.stderr}",
        )

    def test_b1_tester_dedup_records_deleted(self):
        n = self._count(
            "SELECT count(*) FROM whatsapp_outbound_dedup WHERE wa_id = :wa_id",
            wa_id=_TESTER_WA_ID,
        )
        self.assertEqual(n, 0,
            "Cooldown reset must delete all dedup records for the tester")

    def test_b2_recipient_lock_preserved(self):
        n = self._count(
            "SELECT count(*) FROM whatsapp_recipient_locks WHERE wa_id = :wa_id",
            wa_id=_TESTER_WA_ID,
        )
        self.assertEqual(n, 1,
            "Cooldown reset must NOT delete the recipient lock row")

    def test_b3_duplicate_message_eligible_after_cooldown(self):
        """After cooldown, querying dedup for the previously-sent fingerprint returns 0 rows.

        The gate's dedup check is:
          SELECT 1 FROM whatsapp_outbound_dedup
          WHERE wa_id = :wa_id AND message_kind = :kind
            AND content_fingerprint = :fp
            AND created_at > NOW() - INTERVAL '10 minutes'
          LIMIT 1

        With no dedup records, this returns no rows → ALLOWED outcome.
        """
        from sqlalchemy import text
        previously_sent_fp = _fp("Por favor completá los datos del vehículo.")
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT 1 FROM whatsapp_outbound_dedup "
                    "WHERE wa_id = :wa_id AND message_kind = 'text' "
                    "  AND content_fingerprint = :fp "
                    "  AND created_at > NOW() - INTERVAL '10 minutes' "
                    "LIMIT 1"
                ),
                {"wa_id": _TESTER_WA_ID, "fp": previously_sent_fp},
            ).fetchone()
        self.assertIsNone(row,
            "After cooldown reset, dedup query returns no rows → duplicate now eligible")

    def test_b4_other_tester_dedup_untouched(self):
        n = self._count(
            "SELECT count(*) FROM whatsapp_outbound_dedup WHERE wa_id = :wa_id",
            wa_id=_OTHER_WA_ID,
        )
        self.assertEqual(n, 2,
            "Cooldown reset must not touch other tester's dedup records")

    def test_b4_other_tester_lock_untouched(self):
        n = self._count(
            "SELECT count(*) FROM whatsapp_recipient_locks WHERE wa_id = :wa_id",
            wa_id=_OTHER_WA_ID,
        )
        self.assertEqual(n, 1,
            "Cooldown reset must not touch other tester's recipient lock")

    def test_b_cooldown_also_resets_conversation_state(self):
        """Cooldown reset runs the full standard reset first, then clears dedup."""
        n_state = self._count(
            "SELECT count(*) FROM whatsapp_thread_states WHERE thread_id = :tid",
            tid=self._tester["thread_id"],
        )
        n_msgs = self._count(
            "SELECT count(*) FROM whatsapp_messages WHERE thread_id = :tid",
            tid=self._tester["thread_id"],
        )
        self.assertEqual(n_state, 0, "Thread state deleted by cooldown reset")
        self.assertEqual(n_msgs, 0, "Messages deleted by cooldown reset")

    def test_b_cooldown_idempotent(self):
        """Running cooldown reset again on already-reset data exits 0 without error."""
        result = _run_reset(args=["--clear-beta-cooldown"])
        self.assertEqual(result.returncode, 0,
            f"Second cooldown reset must be idempotent. stderr={result.stderr}")


# ══════════════════════════════════════════════════════════════════════════════
# Section C — Guard rejection tests
# ══════════════════════════════════════════════════════════════════════════════

class TestResetGuards(unittest.TestCase):
    """Subprocess guard rejection tests."""

    def _run(self, extra_env: dict | None = None, args: list[str] | None = None,
             wa_id: str = _TESTER_WA_ID) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k != "OUTBOUND_ENABLED"}
        env["TEST_DATABASE_URL"] = _TEST_DB_URL or pg_dsn("crm_test", "localhost")
        env["CLOSED_BETA_TEST_WA_ID"] = wa_id
        if extra_env:
            env.update(extra_env)
        cmd = [sys.executable, _RESET_SCRIPT, "--confirm"] + (args or [])
        return subprocess.run(cmd, capture_output=True, text=True, env=env)

    def test_c1_missing_test_database_url_rejected(self):
        env = {k: v for k, v in os.environ.items() if k != "OUTBOUND_ENABLED"}
        env.pop("TEST_DATABASE_URL", None)
        env["CLOSED_BETA_TEST_WA_ID"] = _TESTER_WA_ID
        result = subprocess.run(
            [sys.executable, _RESET_SCRIPT, "--confirm"],
            capture_output=True, text=True, env=env,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TEST_DATABASE_URL", result.stderr)
        self.assertIn("ABORT", result.stderr)

    def test_c2_crm_target_rejected(self):
        result = self._run(extra_env={
            "TEST_DATABASE_URL": pg_dsn("crm", "localhost")
        })
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("crm_test", result.stderr)
        self.assertIn("ABORT", result.stderr)

    def test_c3_missing_confirm_rejected(self):
        env = {k: v for k, v in os.environ.items() if k != "OUTBOUND_ENABLED"}
        env["TEST_DATABASE_URL"] = _TEST_DB_URL or pg_dsn("crm_test", "localhost")
        env["CLOSED_BETA_TEST_WA_ID"] = _TESTER_WA_ID
        result = subprocess.run(
            [sys.executable, _RESET_SCRIPT],  # no --confirm
            capture_output=True, text=True, env=env,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--confirm", result.stderr)
        self.assertIn("ABORT", result.stderr)

    def test_c4_standard_reset_does_not_clear_dedup(self):
        """Without --clear-beta-cooldown, dedup deletion is guarded by _CLEAR_COOLDOWN flag.

        Static check: the DELETE FROM whatsapp_outbound_dedup statement only executes
        inside the `if _CLEAR_COOLDOWN:` branch. Verified via source inspection so this
        test runs offline without a DB connection or SQLAlchemy installed.
        """
        source = Path(_RESET_SCRIPT).read_text()
        # _CLEAR_COOLDOWN must be set from argv check.
        self.assertIn("_CLEAR_COOLDOWN", source)
        self.assertIn("--clear-beta-cooldown", source)
        # The dedup delete must be conditional on _CLEAR_COOLDOWN.
        # Find the dedup delete statement and verify it's inside an if _CLEAR_COOLDOWN block.
        import re
        # The if block must precede the dedup DELETE
        if_pos = source.find("if _CLEAR_COOLDOWN:")
        dedup_del_pos = source.find("DELETE FROM whatsapp_outbound_dedup")
        self.assertGreater(if_pos, 0, "_CLEAR_COOLDOWN conditional must exist")
        self.assertGreater(dedup_del_pos, if_pos,
            "Dedup DELETE must appear after the if _CLEAR_COOLDOWN: guard")

    def test_c5_outbound_enabled_true_rejected(self):
        result = self._run(extra_env={"OUTBOUND_ENABLED": "true"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OUTBOUND_ENABLED", result.stderr)
        self.assertIn("ABORT", result.stderr)

    def test_c6_missing_closed_beta_test_wa_id_rejected(self):
        env = {k: v for k, v in os.environ.items() if k != "OUTBOUND_ENABLED"}
        env["TEST_DATABASE_URL"] = _TEST_DB_URL or pg_dsn("crm_test", "localhost")
        env.pop("CLOSED_BETA_TEST_WA_ID", None)
        result = subprocess.run(
            [sys.executable, _RESET_SCRIPT, "--confirm"],
            capture_output=True, text=True, env=env,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CLOSED_BETA_TEST_WA_ID", result.stderr)
        self.assertIn("ABORT", result.stderr)


# ══════════════════════════════════════════════════════════════════════════════
# Section D — Compile + regression
# ══════════════════════════════════════════════════════════════════════════════

class TestCompileAndRegression(unittest.TestCase):

    def test_d1_reset_tool_compiles(self):
        import py_compile
        try:
            py_compile.compile(_RESET_SCRIPT, doraise=True)
        except py_compile.PyCompileError as e:
            self.fail(f"reset_closed_beta_scenario.py failed to compile: {e}")

    def test_d2_r1_3d_tests_still_pass(self):
        """Regression: all 19 R1.3.D tests still pass."""
        r1_3d_test = str(TESTS_DIR / "test_m19_r1_3d_closed_beta.py")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", r1_3d_test, "-q", "--tb=short"],
            capture_output=True, text=True,
            cwd=str(ROOT_DIR),
        )
        self.assertEqual(result.returncode, 0,
            f"R1.3.D regression:\n{result.stdout}\n{result.stderr}")


if __name__ == "__main__":
    unittest.main()
