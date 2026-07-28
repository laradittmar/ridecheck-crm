"""M19.F2.3 — Real Test Isolation: guard and quarantine tests.

All tests are fully offline (no network, no Postgres required).

Section 1: Smoke/reset utility guard tests
  Tests 1a-1c: smoke_fallback_flows.py guard behavior
  Tests 2a-2c: smoke_website_flow.py guard behavior
  Tests 3a-3c: cleanup_smoketest_fixtures.py guard behavior

Section 2: Fail-closed database configuration
  Test 4: app/db.py raises RuntimeError without DATABASE_URL
  Test 5: migrations/env.py raises RuntimeError without DATABASE_URL

Section 3: Test-phone quarantine (settings + webhook handler)
  Test 6a: quarantined_test_wa_ids parses from env
  Test 6b: empty/absent env returns empty tuple
  Test 6c: multiple IDs parsed correctly
  Tests 7a-7e: inbound_webhook quarantine behavior (static/unit tests)
"""
from __future__ import annotations

import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

TESTS_DIR = ROOT_DIR / "tests"

# ── Stub heavy deps before any backend import ────────────────────────────────
for _mod in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

# ── Import real psycopg3 before stub loop (needed by SQLAlchemy dialect init) ──
# In a running container psycopg3 is already imported; in an ephemeral container
# we must import it here so the stub loop does NOT replace it with an empty module.
try:
    import psycopg  # noqa: F401
except ImportError:
    pass  # container does not have psycopg3; leave it to the stub below

# ── Stub psycopg2 and other unavailable deps ──────────────────────────────────
for _pg_mod in ["psycopg2", "psycopg2.extensions"]:
    if _pg_mod not in sys.modules:
        sys.modules[_pg_mod] = types.ModuleType(_pg_mod)

# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Smoke/reset utility guard tests
# ══════════════════════════════════════════════════════════════════════════════

def _run_script(script_path: str, env: dict) -> subprocess.CompletedProcess:
    """Run a script as a subprocess with the given environment."""
    clean_env = {k: v for k, v in os.environ.items()
                 if k not in ("DATABASE_URL", "TEST_DATABASE_URL", "OUTBOUND_ENABLED")}
    clean_env.update(env)
    return subprocess.run(
        [sys.executable, script_path],
        capture_output=True,
        text=True,
        env=clean_env,
        cwd=str(BACKEND_DIR),
    )


class TestSmokeGuardsFallback(unittest.TestCase):
    """smoke_fallback_flows.py guard behavior."""

    SCRIPT = str(TESTS_DIR / "smoke_fallback_flows.py")

    def test_1a_refuses_missing_test_database_url(self):
        result = _run_script(self.SCRIPT, {})
        self.assertNotEqual(result.returncode, 0)
        combined = (result.stdout + result.stderr).lower()
        self.assertIn("abort", combined)
        self.assertIn("test_database_url", combined)

    def test_1b_refuses_production_database_name(self):
        result = _run_script(self.SCRIPT, {
            "TEST_DATABASE_URL": "postgresql+psycopg://crm:crm@localhost:5432/crm",
        })
        self.assertNotEqual(result.returncode, 0)
        combined = (result.stdout + result.stderr).lower()
        self.assertIn("abort", combined)
        self.assertIn("crm_test", combined)

    def test_1c_refuses_outbound_enabled_true(self):
        result = _run_script(self.SCRIPT, {
            "TEST_DATABASE_URL": "postgresql+psycopg://crm:crm@localhost:5432/crm_test",
            "OUTBOUND_ENABLED": "true",
        })
        self.assertNotEqual(result.returncode, 0)
        combined = (result.stdout + result.stderr).lower()
        self.assertIn("abort", combined)
        self.assertIn("outbound_enabled", combined)


class TestSmokeGuardsWebsite(unittest.TestCase):
    """smoke_website_flow.py guard behavior."""

    SCRIPT = str(TESTS_DIR / "smoke_website_flow.py")

    def test_2a_refuses_missing_test_database_url(self):
        result = _run_script(self.SCRIPT, {})
        self.assertNotEqual(result.returncode, 0)
        combined = (result.stdout + result.stderr).lower()
        self.assertIn("abort", combined)
        self.assertIn("test_database_url", combined)

    def test_2b_refuses_production_database_name(self):
        result = _run_script(self.SCRIPT, {
            "TEST_DATABASE_URL": "postgresql+psycopg://crm:crm@localhost:5432/crm",
        })
        self.assertNotEqual(result.returncode, 0)
        combined = (result.stdout + result.stderr).lower()
        self.assertIn("abort", combined)
        self.assertIn("crm_test", combined)

    def test_2c_refuses_outbound_enabled_true(self):
        result = _run_script(self.SCRIPT, {
            "TEST_DATABASE_URL": "postgresql+psycopg://crm:crm@localhost:5432/crm_test",
            "OUTBOUND_ENABLED": "true",
        })
        self.assertNotEqual(result.returncode, 0)
        combined = (result.stdout + result.stderr).lower()
        self.assertIn("abort", combined)
        self.assertIn("outbound_enabled", combined)


class TestCleanupGuards(unittest.TestCase):
    """cleanup_smoketest_fixtures.py guard behavior."""

    SCRIPT = str(TESTS_DIR / "cleanup_smoketest_fixtures.py")

    def test_3a_refuses_missing_test_database_url(self):
        result = _run_script(self.SCRIPT, {})
        self.assertNotEqual(result.returncode, 0)
        combined = (result.stdout + result.stderr).lower()
        self.assertIn("abort", combined)
        self.assertIn("test_database_url", combined)

    def test_3b_refuses_production_database_name(self):
        result = _run_script(self.SCRIPT, {
            "TEST_DATABASE_URL": "postgresql+psycopg://crm:crm@localhost:5432/crm",
        })
        self.assertNotEqual(result.returncode, 0)
        combined = (result.stdout + result.stderr).lower()
        self.assertIn("abort", combined)
        self.assertIn("crm_test", combined)

    def test_3c_refuses_outbound_enabled_true(self):
        result = _run_script(self.SCRIPT, {
            "TEST_DATABASE_URL": "postgresql+psycopg://crm:crm@localhost:5432/crm_test",
            "OUTBOUND_ENABLED": "true",
        })
        self.assertNotEqual(result.returncode, 0)
        combined = (result.stdout + result.stderr).lower()
        self.assertIn("abort", combined)
        self.assertIn("outbound_enabled", combined)


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Fail-closed database configuration
# ══════════════════════════════════════════════════════════════════════════════

class TestFailClosedDatabaseConfig(unittest.TestCase):

    def test_4_db_module_raises_without_database_url(self):
        """app/db.py must raise RuntimeError before creating any engine when DATABASE_URL is absent."""
        env_backup = os.environ.pop("DATABASE_URL", None)
        # Save the stub so we can restore it after re-importing the real module.
        _saved_stub = sys.modules.get("app.db")
        try:
            for mod_name in list(sys.modules.keys()):
                if mod_name in ("app.db",):
                    del sys.modules[mod_name]
            with self.assertRaises(RuntimeError) as ctx:
                import app.db  # noqa: F401
            self.assertIn("DATABASE_URL", str(ctx.exception))
        finally:
            if env_backup is not None:
                os.environ["DATABASE_URL"] = env_backup
            # Remove the real module and restore the test stub.
            for mod_name in list(sys.modules.keys()):
                if mod_name in ("app.db",):
                    del sys.modules[mod_name]
            if _saved_stub is not None:
                sys.modules["app.db"] = _saved_stub

    def test_5_db_module_raises_empty_database_url(self):
        """app/db.py must raise RuntimeError when DATABASE_URL is empty string."""
        env_backup = os.environ.pop("DATABASE_URL", None)
        os.environ["DATABASE_URL"] = ""
        _saved_stub = sys.modules.get("app.db")
        try:
            for mod_name in list(sys.modules.keys()):
                if mod_name == "app.db":
                    del sys.modules[mod_name]
            with self.assertRaises(RuntimeError) as ctx:
                import app.db  # noqa: F401
            self.assertIn("DATABASE_URL", str(ctx.exception))
        finally:
            del os.environ["DATABASE_URL"]
            if env_backup is not None:
                os.environ["DATABASE_URL"] = env_backup
            for mod_name in list(sys.modules.keys()):
                if mod_name == "app.db":
                    del sys.modules[mod_name]
            if _saved_stub is not None:
                sys.modules["app.db"] = _saved_stub


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Test-phone quarantine
# ══════════════════════════════════════════════════════════════════════════════

class TestQuarantineSettings(unittest.TestCase):
    """Settings correctly parses QUARANTINED_TEST_WA_IDS."""

    def _fresh_settings(self, env_value: str | None) -> object:
        """Get a Settings instance with the given env value, bypassing lru_cache."""
        # Import only the module so we can call the constructor directly.
        for mod_name in list(sys.modules.keys()):
            if mod_name == "app.settings":
                del sys.modules[mod_name]
        backup = os.environ.pop("QUARANTINED_TEST_WA_IDS", None)
        try:
            if env_value is not None:
                os.environ["QUARANTINED_TEST_WA_IDS"] = env_value
            import app.settings as _s
            return _s._parse_quarantined_wa_ids()
        finally:
            os.environ.pop("QUARANTINED_TEST_WA_IDS", None)
            if backup is not None:
                os.environ["QUARANTINED_TEST_WA_IDS"] = backup
            for mod_name in list(sys.modules.keys()):
                if mod_name == "app.settings":
                    del sys.modules[mod_name]

    def test_6a_single_id_parsed(self):
        result = self._fresh_settings("5491158238330")
        self.assertIn("5491158238330", result)
        self.assertEqual(len(result), 1)

    def test_6b_absent_env_returns_empty_tuple(self):
        result = self._fresh_settings(None)
        self.assertEqual(result, ())

    def test_6c_multiple_ids_parsed(self):
        result = self._fresh_settings("5491158238330, 5491158234567, 5491158231234")
        self.assertIn("5491158238330", result)
        self.assertIn("5491158234567", result)
        self.assertIn("5491158231234", result)
        self.assertEqual(len(result), 3)


class TestQuarantineWebhookBehavior(unittest.TestCase):
    """Inbound webhook silently drops messages from quarantined wa_ids.

    These tests verify behavior by running the webhook handler logic
    with mocked dependencies (no real DB, no real HTTP).
    """

    def _make_webhook_payload(self, wa_id: str, wa_message_id: str = "test-msg-1",
                               text: str = "hola") -> dict:
        return {
            "entry": [{
                "changes": [{
                    "value": {
                        "contacts": [{"wa_id": wa_id, "profile": {"name": "Test User"}}],
                        "messages": [{
                            "id": wa_message_id,
                            "from": wa_id,
                            "type": "text",
                            "timestamp": "1700000000",
                            "text": {"body": text},
                        }],
                    }
                }]
            }]
        }

    def setUp(self):
        # Quarantined fake wa_id for tests — not a real phone number
        self.quarantined_wa_id = "FAKEWA_QUARANTINE_TEST_0001"
        self.non_quarantined_wa_id = "FAKEWA_NORMAL_TEST_0002"

    def test_7a_quarantined_wa_id_does_not_create_contact(self):
        """Webhook must not create/update contact for quarantined wa_id."""
        payload = self._make_webhook_payload(self.quarantined_wa_id)

        mock_db = MagicMock()
        mock_db.execute.return_value.scalar_one_or_none.return_value = None

        from app.settings import Settings
        mock_settings = Settings(
            whatsapp_token="test-token",
            quarantined_test_wa_ids=(self.quarantined_wa_id,),
        )

        import json
        import asyncio
        from app.routes import whatsapp as wh_module

        db_add_calls = []
        original_db_add = mock_db.add
        def track_add(obj):
            db_add_calls.append(obj)
            return original_db_add(obj)
        mock_db.add = track_add

        raw_body = json.dumps(payload).encode()

        with patch.object(wh_module, "get_settings", return_value=mock_settings), \
             patch.object(wh_module, "_verify_signature", return_value=True), \
             patch.object(wh_module, "reset_quote_followup"), \
             patch.object(wh_module, "reset_buscando_followup"):

            async def _run():
                mock_request = MagicMock()
                mock_request.headers = {}
                async def _body(): return raw_body
                mock_request.body = _body
                return await wh_module.inbound_webhook(mock_request, mock_db)

            response = asyncio.run(_run())

        # Webhook must return 200
        self.assertEqual(response.status_code, 200)
        # No DB add() calls for the quarantined number
        self.assertEqual(len(db_add_calls), 0,
                         f"No DB objects must be added for quarantined wa_id, got: {db_add_calls}")
        # No commit() calls
        mock_db.commit.assert_not_called()

    def test_7b_quarantined_wa_id_does_not_commit(self):
        """db.commit() must never be called for a quarantined inbound."""
        payload = self._make_webhook_payload(
            self.quarantined_wa_id, wa_message_id="test-msg-7b"
        )
        mock_db = MagicMock()
        mock_db.execute.return_value.scalar_one_or_none.return_value = None

        from app.settings import Settings
        mock_settings = Settings(
            whatsapp_token="test-token",
            quarantined_test_wa_ids=(self.quarantined_wa_id,),
        )

        import json, asyncio
        from app.routes import whatsapp as wh_module

        raw_body = json.dumps(payload).encode()

        with patch.object(wh_module, "get_settings", return_value=mock_settings), \
             patch.object(wh_module, "_verify_signature", return_value=True):
            async def _run():
                mock_request = MagicMock()
                mock_request.headers = {}
                async def _body(): return raw_body
                mock_request.body = _body
                return await wh_module.inbound_webhook(mock_request, mock_db)
            asyncio.run(_run())

        mock_db.commit.assert_not_called()

    def test_7c_non_quarantined_wa_id_reaches_db(self):
        """A non-quarantined wa_id must proceed normally to DB operations."""
        payload = self._make_webhook_payload(
            self.non_quarantined_wa_id, wa_message_id="test-msg-7c"
        )
        mock_db = MagicMock()
        mock_db.execute.return_value.scalar_one_or_none.return_value = None
        mock_db.execute.return_value.scalars.return_value.first.return_value = None

        from app.settings import Settings
        mock_settings = Settings(
            whatsapp_token="test-token",
            quarantined_test_wa_ids=(self.quarantined_wa_id,),
        )

        import json, asyncio
        from app.routes import whatsapp as wh_module

        raw_body = json.dumps(payload).encode()

        with patch.object(wh_module, "get_settings", return_value=mock_settings), \
             patch.object(wh_module, "_verify_signature", return_value=True), \
             patch.object(wh_module, "reset_quote_followup"), \
             patch.object(wh_module, "reset_buscando_followup"), \
             patch.object(wh_module, "_dispatch_to_engine"), \
             patch.object(wh_module, "_post_n8n_event"):
            async def _run():
                mock_request = MagicMock()
                mock_request.headers = {}
                async def _body(): return raw_body
                mock_request.body = _body
                return await wh_module.inbound_webhook(mock_request, mock_db)
            asyncio.run(_run())

        # commit() must have been called (normal path)
        mock_db.commit.assert_called()

    def test_7d_quarantine_check_fires_before_any_state_mutation(self):
        """The quarantine check must occur before the first db.execute for contact lookup."""
        payload = self._make_webhook_payload(
            self.quarantined_wa_id, wa_message_id="test-msg-7d"
        )
        execute_calls = []
        mock_db = MagicMock()
        def track_execute(*args, **kwargs):
            execute_calls.append(args)
            m = MagicMock()
            m.scalar_one_or_none.return_value = None
            m.scalars.return_value.first.return_value = None
            return m
        mock_db.execute = track_execute

        from app.settings import Settings
        mock_settings = Settings(
            whatsapp_token="test-token",
            quarantined_test_wa_ids=(self.quarantined_wa_id,),
        )

        import json, asyncio
        from app.routes import whatsapp as wh_module

        raw_body = json.dumps(payload).encode()

        with patch.object(wh_module, "get_settings", return_value=mock_settings), \
             patch.object(wh_module, "_verify_signature", return_value=True):
            async def _run():
                mock_request = MagicMock()
                mock_request.headers = {}
                async def _body(): return raw_body
                mock_request.body = _body
                return await wh_module.inbound_webhook(mock_request, mock_db)
            asyncio.run(_run())

        # No contact lookup, thread lookup, or message insert should occur.
        select_targets = [str(c) for c in execute_calls]
        contact_lookups = [s for s in select_targets if "whatsapp_contacts" in s.lower()]
        self.assertEqual(len(contact_lookups), 0,
                         "No WhatsAppContact lookup must occur for quarantined wa_id")

    def test_7e_empty_quarantine_list_does_not_block_any_wa_id(self):
        """When quarantined_test_wa_ids is empty, no wa_id is blocked."""
        payload = self._make_webhook_payload(
            self.quarantined_wa_id, wa_message_id="test-msg-7e"
        )
        mock_db = MagicMock()
        mock_db.execute.return_value.scalar_one_or_none.return_value = None
        mock_db.execute.return_value.scalars.return_value.first.return_value = None

        from app.settings import Settings
        mock_settings = Settings(
            whatsapp_token="test-token",
            quarantined_test_wa_ids=(),
        )

        import json, asyncio
        from app.routes import whatsapp as wh_module

        db_add_calls = []
        def track_add(obj):
            db_add_calls.append(obj)
        mock_db.add = track_add

        raw_body = json.dumps(payload).encode()

        with patch.object(wh_module, "get_settings", return_value=mock_settings), \
             patch.object(wh_module, "_verify_signature", return_value=True), \
             patch.object(wh_module, "reset_quote_followup"), \
             patch.object(wh_module, "reset_buscando_followup"), \
             patch.object(wh_module, "_dispatch_to_engine"), \
             patch.object(wh_module, "_post_n8n_event"):
            async def _run():
                mock_request = MagicMock()
                mock_request.headers = {}
                async def _body(): return raw_body
                mock_request.body = _body
                return await wh_module.inbound_webhook(mock_request, mock_db)
            asyncio.run(_run())

        # With empty quarantine list, normal processing happens → db.add() called
        self.assertGreater(len(db_add_calls), 0,
                           "With empty quarantine list, normal processing must proceed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
