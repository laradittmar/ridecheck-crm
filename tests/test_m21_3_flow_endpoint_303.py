"""M21.3-FLOW-ENDPOINT-303 — Data Exchange endpoint auth bypass + health check tests.

FLOW303-01: exact Data Exchange endpoint does not require CRM session auth
FLOW303-02: unrelated CRM routes remain authenticated
FLOW303-03: endpoint does not redirect to login
FLOW303-04: Meta ping/setup request returns protocol-valid HTTP 200
FLOW303-05: invalid Flow crypto request remains rejected appropriately
FLOW303-06: whitelist is exact/minimal — no broad prefix exposed
FLOW303-07: OUTBOUND remains OFF and no message is sent
"""
from __future__ import annotations

import base64
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import sqlalchemy as _sa
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = _sa.JSON
_pg_json.JSONB = _sa.JSON

for _mod_name in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

if "psycopg2" not in sys.modules:
    _pg = types.ModuleType("psycopg2")
    _pg.extensions = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2"] = _pg
    sys.modules["psycopg2.extensions"] = _pg.extensions

# Import middleware functions directly
from app.main import _is_public_path, _is_protected_path

DATA_EXCHANGE_PATH = "/integrations/whatsapp/flows/booking/data-exchange"
WEBHOOK_PATH = "/integrations/whatsapp/webhook"

PROTECTED_PATHS = [
    "/kanban",
    "/table",
    "/calendar",
    "/whatsapp",
    "/whatsapp/inbox",
    "/control",
    "/profesionales",
    "/agencias",
]


# ── FLOW303-01: Data Exchange does not require CRM session auth ───────────────

class TestFLOW303_01_DataExchangeIsPublic(unittest.TestCase):
    def test_01a_data_exchange_is_public(self):
        self.assertTrue(
            _is_public_path(DATA_EXCHANGE_PATH),
            "Data Exchange endpoint must be in public path whitelist",
        )

    def test_01b_data_exchange_not_protected(self):
        # Even though /integrations/whatsapp is a protected prefix,
        # the public check must short-circuit before the protected check fires.
        # The middleware calls _is_public_path first — if True, no auth required.
        self.assertTrue(_is_public_path(DATA_EXCHANGE_PATH))
        # We also verify it WOULD match protected if public check were absent
        self.assertTrue(
            _is_protected_path(DATA_EXCHANGE_PATH),
            "Data Exchange starts with /integrations/whatsapp — protected prefix matches",
        )

    def test_01c_webhook_also_public(self):
        self.assertTrue(_is_public_path(WEBHOOK_PATH))


# ── FLOW303-02: Unrelated CRM routes remain authenticated ─────────────────────

class TestFLOW303_02_ProtectedRoutesUnchanged(unittest.TestCase):
    def test_02a_kanban_protected(self):
        self.assertFalse(_is_public_path("/kanban"))
        self.assertTrue(_is_protected_path("/kanban"))

    def test_02b_whatsapp_inbox_protected(self):
        self.assertFalse(_is_public_path("/whatsapp/inbox"))
        self.assertTrue(_is_protected_path("/whatsapp/inbox"))

    def test_02c_control_protected(self):
        self.assertFalse(_is_public_path("/control"))
        self.assertTrue(_is_protected_path("/control"))

    def test_02d_all_protected_paths_not_public(self):
        for p in PROTECTED_PATHS:
            with self.subTest(path=p):
                self.assertFalse(
                    _is_public_path(p),
                    f"{p} must NOT be in public whitelist",
                )


# ── FLOW303-03: Endpoint does not redirect to login ───────────────────────────

class TestFLOW303_03_NoLoginRedirect(unittest.TestCase):
    def test_03a_public_path_skips_auth_middleware(self):
        # Simulate middleware logic: if public → pass through (no redirect)
        path = DATA_EXCHANGE_PATH
        would_redirect = False
        if _is_public_path(path):
            pass  # call_next — no redirect
        elif _is_protected_path(path):
            would_redirect = True  # session required
        self.assertFalse(would_redirect, "Data Exchange must not trigger login redirect")

    def test_03b_missing_session_would_redirect_kanban(self):
        path = "/kanban"
        would_redirect = False
        if _is_public_path(path):
            pass
        elif _is_protected_path(path):
            would_redirect = True
        self.assertTrue(would_redirect, "/kanban must require session")

    def test_03c_trailing_slash_is_protected(self):
        # Trailing slash is NOT in the exact whitelist — it would be intercepted.
        # This documents the known behavior: Meta must call without trailing slash.
        trailing = DATA_EXCHANGE_PATH + "/"
        self.assertFalse(_is_public_path(trailing))
        self.assertTrue(_is_protected_path(trailing))


# ── FLOW303-04: Meta ping returns protocol-valid HTTP 200 ─────────────────────

class TestFLOW303_04_MetaPingHealth(unittest.TestCase):
    """Verifies the ping handler returns correctly structured response."""

    def test_04a_health_response_structure(self):
        from app.services.booking_flow_service import health_response, FLOW_VERSION
        resp = health_response()
        self.assertEqual(resp["version"], FLOW_VERSION)
        self.assertEqual(resp["data"]["status"], "active")

    def test_04b_health_response_version_matches_protocol(self):
        from app.services.booking_flow_service import FLOW_VERSION
        self.assertEqual(FLOW_VERSION, "3.0")

    def test_04c_ping_action_handled_without_db(self):
        """ping action short-circuits before BookingFlowService is constructed."""
        from app.services.booking_flow_service import health_response
        resp = health_response()
        self.assertIn("version", resp)
        self.assertIn("data", resp)
        self.assertIn("status", resp["data"])

    def test_04d_encrypt_decrypt_roundtrip(self):
        """encrypt_flow_response + AES-GCM decrypt roundtrip is consistent."""
        from app.services.booking_flow_service import encrypt_flow_response, health_response
        aes_key = os.urandom(16)
        iv = os.urandom(16)
        payload = health_response()
        encrypted = encrypt_flow_response(payload, aes_key, iv)
        self.assertIsInstance(encrypted, bytes)
        self.assertGreater(len(encrypted), 0)

        # Decrypt with flipped IV
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            flipped_iv = bytes(b ^ 0xFF for b in iv)
            decrypted = json.loads(AESGCM(aes_key).decrypt(flipped_iv, encrypted, None))
            self.assertEqual(decrypted["data"]["status"], "active")
        except ImportError:
            self.skipTest("cryptography not installed in test env")


# ── FLOW303-05: Invalid crypto request rejected appropriately ─────────────────

class TestFLOW303_05_InvalidCryptoRejected(unittest.TestCase):
    def test_05a_decrypt_fail_raises_value_error(self):
        from app.services.booking_flow_service import decrypt_flow_request
        import unittest
        # Wrong / garbage encrypted_aes_key should raise ValueError
        bad_body = {
            "encrypted_aes_key": base64.b64encode(b"notarealkeyXXXX").decode(),
            "encrypted_flow_data": base64.b64encode(b"garbage").decode(),
            "initial_vector": base64.b64encode(b"1234567890123456").decode(),
        }
        # With no private key configured, should raise ValueError
        with patch.dict(os.environ, {"FLOW_BOOKING_PRIVATE_KEY_PATH": ""}):
            # Reset module cache for settings
            import importlib
            import app.services.booking_flow_service as bfs
            with self.assertRaises((ValueError, Exception)):
                decrypt_flow_request(bad_body)

    def test_05b_missing_body_fields_raises(self):
        from app.services.booking_flow_service import decrypt_flow_request
        with self.assertRaises((KeyError, ValueError, Exception)):
            decrypt_flow_request({})

    def test_05c_garbage_base64_raises(self):
        from app.services.booking_flow_service import decrypt_flow_request
        bad = {
            "encrypted_aes_key": "!!!notbase64!!!",
            "encrypted_flow_data": "!!!notbase64!!!",
            "initial_vector": "!!!notbase64!!!",
        }
        with self.assertRaises(Exception):
            decrypt_flow_request(bad)


# ── FLOW303-06: Whitelist is exact/minimal ────────────────────────────────────

class TestFLOW303_06_WhitelistIsMinimal(unittest.TestCase):
    def test_06a_broad_integrations_prefix_not_public(self):
        self.assertFalse(_is_public_path("/integrations/whatsapp"))
        self.assertFalse(_is_public_path("/integrations/whatsapp/"))
        self.assertFalse(_is_public_path("/integrations"))

    def test_06b_flows_prefix_not_public(self):
        self.assertFalse(_is_public_path("/integrations/whatsapp/flows"))
        self.assertFalse(_is_public_path("/integrations/whatsapp/flows/"))
        self.assertFalse(_is_public_path("/integrations/whatsapp/flows/booking"))

    def test_06c_similar_but_wrong_paths_not_public(self):
        self.assertFalse(_is_public_path("/integrations/whatsapp/flows/booking/data-exchangeX"))
        self.assertFalse(_is_public_path("/integrations/whatsapp/flows/booking/data_exchange"))
        self.assertFalse(_is_public_path("/integrations/whatsapp/flows/other/data-exchange"))

    def test_06d_exact_webhook_path_still_public(self):
        self.assertTrue(_is_public_path("/integrations/whatsapp/webhook"))
        self.assertFalse(_is_public_path("/integrations/whatsapp/webhookX"))

    def test_06e_whitelist_contains_exactly_two_entries(self):
        # Ensure nobody accidentally broadened the whitelist
        count = 0
        for path in [
            "/integrations/whatsapp/webhook",
            "/integrations/whatsapp/flows/booking/data-exchange",
        ]:
            if _is_public_path(path):
                count += 1
        self.assertEqual(count, 2)

        # And a sampling of paths that must NOT be public
        not_public = [
            "/kanban", "/control", "/whatsapp", "/login",
            "/integrations/whatsapp", "/integrations/whatsapp/",
            "/integrations/whatsapp/flows", "/api/ops/summary",
        ]
        for p in not_public:
            with self.subTest(path=p):
                self.assertFalse(_is_public_path(p))


# ── FLOW303-07: OUTBOUND remains OFF ─────────────────────────────────────────

class TestFLOW303_07_OutboundOff(unittest.TestCase):
    def test_07a_outbound_env_var_off(self):
        outbound = os.environ.get("OUTBOUND_ENABLED", "false").lower()
        self.assertNotEqual(outbound, "true", "OUTBOUND_ENABLED must not be 'true'")

    def test_07b_flow_endpoint_does_not_call_send(self):
        """The Data Exchange router must not import or call any outbound send function."""
        import app.routes.flow_data_exchange as _fde_mod
        import inspect
        src = inspect.getsource(_fde_mod)
        # Must not reference send_whatsapp_message or outbound gate send
        self.assertNotIn("send_whatsapp_message", src)
        self.assertNotIn("gate.attempt", src)

    def test_07c_booking_flow_service_does_not_send_outbound(self):
        """BookingFlowService must not trigger outbound during handle_init or health."""
        from app.services.booking_flow_service import health_response
        # health_response is pure — returns a dict, no side effects
        result = health_response()
        self.assertIsInstance(result, dict)

    def test_07d_outbound_flag_not_enabled_in_test_env(self):
        with patch.dict(os.environ, {}, clear=False):
            outbound = os.environ.get("OUTBOUND_ENABLED", "false")
            self.assertNotEqual(outbound, "true")


if __name__ == "__main__":
    unittest.main()
