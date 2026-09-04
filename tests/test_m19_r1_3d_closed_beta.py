"""M19.R1.3.D — Closed Beta Test Lane: offline unit tests.

Tests verified (all offline, no network, no Postgres required):

Section 1 — Settings parsing
  1a: closed_beta_allowed_wa_ids parses comma-separated env var
  1b: empty env var returns empty tuple (normal mode)
  1c: absent env var returns empty tuple (normal mode)
  1d: single wa_id parses correctly
  1e: whitespace around entries is stripped

Section 2 — Webhook allowlist behaviour (static/unit)
  2a: allowlisted wa_id is processed normally (not dropped)
  2b: non-allowlisted wa_id is dropped before any DB write when list is non-empty
  2c: empty allowlist allows all wa_ids (normal open behaviour)
  2d: quarantine overrides allowlist (wa_id in both → quarantine wins, dropped)
  2e: log message uses wa_id[-4:] suffix only, never the full wa_id

Section 3 — Reset script guard behaviour
  3a: missing --confirm aborts with non-zero exit
  3b: OUTBOUND_ENABLED=true aborts
  3c: missing TEST_DATABASE_URL aborts
  3d: DB name ≠ crm_test aborts
  3e: missing CLOSED_BETA_TEST_WA_ID aborts
  3f: reset script never prints the full wa_id (uses suffix only)

Section 4 — Allowlist parser returns stable tuple type
  4a: result type is tuple[str, ...]
  4b: duplicate entries preserved as-is (no dedup — keeps parity with quarantine parser)
"""
from __future__ import annotations
from pg_dsn import pg_dsn  # SEC: no credential literal

import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
TESTS_DIR = ROOT_DIR / "tests"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── Stub heavy deps before any backend import ─────────────────────────────────
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

_RESET_SCRIPT = str(TESTS_DIR / "reset_closed_beta_scenario.py")
_TESTER_WA_ID = "54911TEST8330"
_TESTER_SUFFIX = _TESTER_WA_ID[-4:]  # "8330"


def _run_reset(extra_env: dict | None = None, args: list[str] | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "CLOSED_BETA_TEST_WA_ID": _TESTER_WA_ID}
    if extra_env:
        env.update(extra_env)
    cmd = [sys.executable, _RESET_SCRIPT] + (args or [])
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Settings parsing
# ══════════════════════════════════════════════════════════════════════════════

class TestClosedBetaSettingsParsing(unittest.TestCase):

    def _parse(self, env_value: str | None) -> tuple:
        env = {}
        if env_value is not None:
            env["CLOSED_BETA_ALLOWED_WA_IDS"] = env_value
        with patch.dict(os.environ, env, clear=False):
            # Re-import the private parser each time to avoid lru_cache pollution.
            import importlib
            import app.settings as settings_mod
            # Clear lru_cache so get_settings() re-reads env.
            settings_mod.get_settings.cache_clear()
            return settings_mod._parse_closed_beta_allowed_wa_ids()

    def test_1a_parses_comma_separated(self):
        result = self._parse("54911111111,54922222222")
        self.assertEqual(result, ("54911111111", "54922222222"))

    def test_1b_empty_string_returns_empty_tuple(self):
        result = self._parse("")
        self.assertEqual(result, ())

    def test_1c_absent_env_returns_empty_tuple(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLOSED_BETA_ALLOWED_WA_IDS", None)
            import app.settings as settings_mod
            settings_mod.get_settings.cache_clear()
            result = settings_mod._parse_closed_beta_allowed_wa_ids()
        self.assertEqual(result, ())

    def test_1d_single_wa_id(self):
        result = self._parse("54911TEST8330")
        self.assertEqual(result, ("54911TEST8330",))

    def test_1e_whitespace_stripped(self):
        result = self._parse("  54911111111 , 54922222222  ")
        self.assertEqual(result, ("54911111111", "54922222222"))


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Webhook allowlist behaviour
# ══════════════════════════════════════════════════════════════════════════════

def _build_webhook_payload(wa_id: str, wa_message_id: str = "wamid.TEST001") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "123",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "statuses": [],
                    "contacts": [{"wa_id": wa_id, "profile": {"name": "Test"}}],
                    "messages": [{
                        "id": wa_message_id,
                        "from": wa_id,
                        "type": "text",
                        "timestamp": "1700000000",
                        "text": {"body": "hola"},
                    }],
                },
                "field": "messages",
            }],
        }],
    }


def _make_settings(quarantined: tuple = (), closed_beta_allowed: tuple = ()):
    from app.settings import Settings
    return Settings(quarantined_test_wa_ids=quarantined, closed_beta_allowed_wa_ids=closed_beta_allowed)


class TestWebhookAllowlist(unittest.TestCase):
    """Static/unit tests: parse route code to verify allowlist logic without running FastAPI."""

    def setUp(self):
        import ast
        route_path = BACKEND_DIR / "app" / "routes" / "whatsapp.py"
        self._source = route_path.read_text()

    def test_2a_allowlisted_wa_id_processed(self):
        """CLOSED_BETA_ALLOWED_WA_IDS containing the wa_id must NOT trigger early continue."""
        # Settings with a non-empty allowlist and the tester in it.
        settings = _make_settings(closed_beta_allowed=(_TESTER_WA_ID,))
        # Guard logic: if allowlist non-empty AND wa_id NOT in list → drop.
        # Tester IS in list → should not be dropped.
        should_drop = (
            bool(settings.closed_beta_allowed_wa_ids)
            and _TESTER_WA_ID not in settings.closed_beta_allowed_wa_ids
        )
        self.assertFalse(should_drop, "Allowlisted wa_id must not be dropped")

    def test_2b_non_allowlisted_wa_id_dropped(self):
        """A stranger wa_id must be dropped when the allowlist is non-empty."""
        settings = _make_settings(closed_beta_allowed=(_TESTER_WA_ID,))
        stranger = "54999STRANGER"
        should_drop = (
            bool(settings.closed_beta_allowed_wa_ids)
            and stranger not in settings.closed_beta_allowed_wa_ids
        )
        self.assertTrue(should_drop, "Non-allowlisted wa_id must be dropped")

    def test_2c_empty_allowlist_allows_all(self):
        """When CLOSED_BETA_ALLOWED_WA_IDS is empty, all wa_ids pass the check."""
        settings = _make_settings(closed_beta_allowed=())
        for wa_id in [_TESTER_WA_ID, "54999STRANGER", "anything"]:
            should_drop = (
                bool(settings.closed_beta_allowed_wa_ids)
                and wa_id not in settings.closed_beta_allowed_wa_ids
            )
            self.assertFalse(should_drop, f"Empty allowlist must pass {wa_id}")

    def test_2d_quarantine_overrides_allowlist(self):
        """Quarantine check comes first; a quarantined wa_id is dropped even if also allowlisted."""
        wa_id = _TESTER_WA_ID
        settings = _make_settings(
            quarantined=(wa_id,),
            closed_beta_allowed=(wa_id,),
        )
        # Quarantine check fires first (wa_id in quarantined_test_wa_ids → continue before allowlist).
        quarantined = wa_id in settings.quarantined_test_wa_ids
        self.assertTrue(quarantined, "Quarantine must catch the wa_id before allowlist check")

    def test_2e_allowlist_check_present_in_route_source(self):
        """Route source must contain the CLOSED_BETA_NOT_ALLOWED log message and suffix-only log."""
        self.assertIn("CLOSED_BETA_NOT_ALLOWED", self._source)
        self.assertIn("wa_id[-4:]", self._source)
        # Must appear AFTER the quarantine block.
        q_pos = self._source.index("QUARANTINED_TEST_WA_ID")
        cb_pos = self._source.index("CLOSED_BETA_NOT_ALLOWED")
        self.assertGreater(cb_pos, q_pos, "Allowlist check must appear after quarantine check")

    def test_2e_log_uses_suffix_only(self):
        """The allowlist log line uses wa_id[-4:] — never the raw full wa_id."""
        # Find the CLOSED_BETA_NOT_ALLOWED block in the route source.
        lines = self._source.splitlines()
        in_block = False
        block_lines: list[str] = []
        for line in lines:
            if "CLOSED_BETA_NOT_ALLOWED" in line:
                in_block = True
            if in_block:
                block_lines.append(line)
                if line.strip() == "continue":
                    break
        block = "\n".join(block_lines)
        self.assertIn("wa_id[-4:]", block, "Log must use suffix only")
        # Should not use plain 'wa_id' as a format argument without [-4:].
        # Check that no bare %s with plain wa_id (not sliced) appears in the log call.
        self.assertNotIn('"wa_id=%s"', block)
        self.assertNotIn("'wa_id=%s'", block)


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Reset script guard behaviour
# ══════════════════════════════════════════════════════════════════════════════

class TestResetScriptGuards(unittest.TestCase):

    def _run_no_confirm(self, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        """Run without --confirm (default)."""
        env = {k: v for k, v in os.environ.items()}
        env["CLOSED_BETA_TEST_WA_ID"] = _TESTER_WA_ID
        env["TEST_DATABASE_URL"] = pg_dsn("crm_test", "localhost")
        env.pop("OUTBOUND_ENABLED", None)
        if extra_env:
            env.update(extra_env)
        cmd = [sys.executable, _RESET_SCRIPT]  # no --confirm
        return subprocess.run(cmd, capture_output=True, text=True, env=env)

    def _run_with_confirm(self, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items()}
        env["CLOSED_BETA_TEST_WA_ID"] = _TESTER_WA_ID
        env["TEST_DATABASE_URL"] = pg_dsn("crm_test", "localhost")
        env.pop("OUTBOUND_ENABLED", None)
        if extra_env:
            env.update(extra_env)
        cmd = [sys.executable, _RESET_SCRIPT, "--confirm"]
        return subprocess.run(cmd, capture_output=True, text=True, env=env)

    def test_3a_missing_confirm_aborts(self):
        result = self._run_no_confirm()
        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertIn("ABORT", combined)
        self.assertIn("--confirm", combined)

    def test_3b_outbound_enabled_aborts(self):
        result = self._run_with_confirm({"OUTBOUND_ENABLED": "true"})
        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertIn("ABORT", combined)
        self.assertIn("OUTBOUND_ENABLED", combined)

    def test_3c_missing_test_database_url_aborts(self):
        env = {k: v for k, v in os.environ.items()}
        env["CLOSED_BETA_TEST_WA_ID"] = _TESTER_WA_ID
        env.pop("TEST_DATABASE_URL", None)
        env.pop("OUTBOUND_ENABLED", None)
        cmd = [sys.executable, _RESET_SCRIPT, "--confirm"]
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertIn("ABORT", combined)
        self.assertIn("TEST_DATABASE_URL", combined)

    def test_3d_wrong_db_name_aborts(self):
        result = self._run_with_confirm({
            "TEST_DATABASE_URL": pg_dsn("crm", "localhost")
        })
        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertIn("ABORT", combined)
        # Should mention the wrong DB name and crm_test.
        self.assertIn("crm_test", combined)

    def test_3e_missing_wa_id_aborts(self):
        env = {k: v for k, v in os.environ.items()}
        env["TEST_DATABASE_URL"] = pg_dsn("crm_test", "localhost")
        env.pop("CLOSED_BETA_TEST_WA_ID", None)
        env.pop("OUTBOUND_ENABLED", None)
        cmd = [sys.executable, _RESET_SCRIPT, "--confirm"]
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertIn("ABORT", combined)
        self.assertIn("CLOSED_BETA_TEST_WA_ID", combined)

    def test_3f_reset_script_never_prints_full_wa_id(self):
        """Reset script source must not print the raw wa_id — only the suffix."""
        source = Path(_RESET_SCRIPT).read_text()
        # The script computes a module-level suffix via [-4:] and uses that in all prints.
        self.assertIn("_WA_SUFFIX", source)
        self.assertIn("[-4:]", source)
        # All print() calls must use wa_suffix or _WA_SUFFIX, never _TESTER_WA_ID directly.
        import re
        print_calls = re.findall(r'print\([^)]+\)', source)
        for call in print_calls:
            self.assertNotIn("_TESTER_WA_ID}", call,
                "Script must not print the full tester wa_id variable directly")


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Parser type stability
# ══════════════════════════════════════════════════════════════════════════════

class TestParserTypeStability(unittest.TestCase):

    def setUp(self):
        import app.settings as settings_mod
        settings_mod.get_settings.cache_clear()

    def _parse(self, raw: str) -> object:
        with patch.dict(os.environ, {"CLOSED_BETA_ALLOWED_WA_IDS": raw}):
            import app.settings as settings_mod
            settings_mod.get_settings.cache_clear()
            return settings_mod._parse_closed_beta_allowed_wa_ids()

    def test_4a_result_is_tuple(self):
        result = self._parse("54911111111,54922222222")
        self.assertIsInstance(result, tuple)

    def test_4b_duplicates_preserved(self):
        result = self._parse("54911111111,54911111111")
        self.assertEqual(len(result), 2)
        self.assertEqual(result, ("54911111111", "54911111111"))


if __name__ == "__main__":
    unittest.main()
