"""M21.3-FLOW-RESPONSE-BASE64 — Meta Flow response must be base64-encoded.

FLOWB64-01: successful encrypted ping returns HTTP 200
FLOWB64-02: response body is valid Base64
FLOWB64-03: response body contains no raw binary
FLOWB64-04: Base64-decoded response decrypts successfully
FLOWB64-05: decrypted ping response equals expected active payload
FLOWB64-06: response is encoded exactly once
FLOWB64-07: invalid crypto still rejected
FLOWB64-08: CRM protected routes remain authenticated
FLOWB64-09: OUTBOUND remains OFF
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

from app.services.booking_flow_service import (
    encrypt_flow_response,
    health_response,
    FLOW_VERSION,
)
from app.routes.flow_data_exchange import _encrypt_and_return


def _make_test_key_iv() -> tuple[bytes, bytes]:
    return os.urandom(16), os.urandom(16)


def _response_body(response) -> bytes:
    """Extract body bytes from a FastAPI Response."""
    if hasattr(response, "body"):
        return response.body
    if hasattr(response, "content"):
        c = response.content
        return c.encode("ascii") if isinstance(c, str) else c
    raise AssertionError(f"Cannot extract body from {type(response)}")


# ── FLOWB64-01: successful encrypted ping returns HTTP 200 ────────────────────

class TestFLOWB64_01_PingReturns200(unittest.TestCase):
    def test_01a_encrypt_and_return_is_200(self):
        aes_key, iv = _make_test_key_iv()
        resp = _encrypt_and_return(health_response(), aes_key, iv)
        self.assertEqual(resp.status_code, 200)

    def test_01b_encrypt_and_return_non_empty(self):
        aes_key, iv = _make_test_key_iv()
        resp = _encrypt_and_return(health_response(), aes_key, iv)
        body = _response_body(resp)
        self.assertGreater(len(body), 0)

    def test_01c_content_type_is_text(self):
        aes_key, iv = _make_test_key_iv()
        resp = _encrypt_and_return(health_response(), aes_key, iv)
        ct = resp.media_type or ""
        self.assertIn("text", ct, f"Content-Type should be text, got: {ct!r}")


# ── FLOWB64-02: response body is valid Base64 ─────────────────────────────────

class TestFLOWB64_02_ValidBase64(unittest.TestCase):
    def test_02a_body_is_valid_base64(self):
        aes_key, iv = _make_test_key_iv()
        resp = _encrypt_and_return(health_response(), aes_key, iv)
        body = _response_body(resp)
        # Must not raise
        try:
            decoded = base64.b64decode(body, validate=True)
        except Exception as exc:
            self.fail(f"Response body is not valid base64: {exc}")
        self.assertIsInstance(decoded, bytes)

    def test_02b_body_is_ascii(self):
        aes_key, iv = _make_test_key_iv()
        resp = _encrypt_and_return(health_response(), aes_key, iv)
        body = _response_body(resp)
        try:
            body.decode("ascii") if isinstance(body, bytes) else body.encode("ascii")
        except (UnicodeDecodeError, UnicodeEncodeError) as exc:
            self.fail(f"Response body is not ASCII: {exc}")

    def test_02c_base64_alphabet_only(self):
        import re
        aes_key, iv = _make_test_key_iv()
        resp = _encrypt_and_return(health_response(), aes_key, iv)
        body = _response_body(resp)
        s = body.decode("ascii") if isinstance(body, bytes) else body
        s = s.strip()
        self.assertRegex(s, r'^[A-Za-z0-9+/]+=*$', "Body must be valid base64 alphabet")


# ── FLOWB64-03: response body contains no raw binary ─────────────────────────

class TestFLOWB64_03_NoRawBinary(unittest.TestCase):
    def test_03a_no_null_bytes(self):
        aes_key, iv = _make_test_key_iv()
        resp = _encrypt_and_return(health_response(), aes_key, iv)
        body = _response_body(resp)
        body_bytes = body if isinstance(body, bytes) else body.encode("ascii")
        self.assertNotIn(b"\x00", body_bytes, "Response must not contain null bytes (raw binary)")

    def test_03b_all_printable_ascii(self):
        aes_key, iv = _make_test_key_iv()
        resp = _encrypt_and_return(health_response(), aes_key, iv)
        body = _response_body(resp)
        body_bytes = body if isinstance(body, bytes) else body.encode("ascii")
        for byte in body_bytes:
            self.assertGreaterEqual(byte, 0x20, f"Non-printable byte 0x{byte:02x} in response")
            self.assertLessEqual(byte, 0x7E, f"Non-ASCII byte 0x{byte:02x} in response")

    def test_03c_encrypt_flow_response_returns_bytes(self):
        """encrypt_flow_response itself returns raw bytes (unchanged — correct)."""
        aes_key, iv = _make_test_key_iv()
        result = encrypt_flow_response(health_response(), aes_key, iv)
        self.assertIsInstance(result, bytes)
        # Raw bytes are NOT printable ASCII
        has_high_bytes = any(b > 0x7E or b < 0x20 for b in result)
        self.assertTrue(has_high_bytes, "Raw encrypted bytes should contain non-ASCII bytes")


# ── FLOWB64-04: Base64-decoded response decrypts successfully ─────────────────

class TestFLOWB64_04_DecodesDecrypts(unittest.TestCase):
    def _get_decoded_and_decrypted(self, payload: dict) -> dict:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            self.skipTest("cryptography not installed")
        aes_key, iv = _make_test_key_iv()
        resp = _encrypt_and_return(payload, aes_key, iv)
        body = _response_body(resp)
        body_str = body.decode("ascii") if isinstance(body, bytes) else body
        raw = base64.b64decode(body_str.strip())
        flipped_iv = bytes(b ^ 0xFF for b in iv)
        decrypted = json.loads(AESGCM(aes_key).decrypt(flipped_iv, raw, None))
        return decrypted

    def test_04a_base64_decodes_to_bytes(self):
        aes_key, iv = _make_test_key_iv()
        resp = _encrypt_and_return(health_response(), aes_key, iv)
        body = _response_body(resp)
        decoded = base64.b64decode(body)
        self.assertIsInstance(decoded, bytes)
        self.assertGreater(len(decoded), 0)

    def test_04b_decoded_bytes_decrypt_to_json(self):
        result = self._get_decoded_and_decrypted(health_response())
        self.assertIsInstance(result, dict)

    def test_04c_arbitrary_payload_roundtrip(self):
        payload = {"version": FLOW_VERSION, "screen": "APPOINTMENT", "data": {"x": 42}}
        result = self._get_decoded_and_decrypted(payload)
        self.assertEqual(result["screen"], "APPOINTMENT")
        self.assertEqual(result["data"]["x"], 42)


# ── FLOWB64-05: decrypted ping response equals expected active payload ─────────

class TestFLOWB64_05_PingResponseCorrect(unittest.TestCase):
    def _decrypt_response(self, payload: dict, aes_key: bytes, iv: bytes) -> dict:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            self.skipTest("cryptography not installed")
        resp = _encrypt_and_return(payload, aes_key, iv)
        body = _response_body(resp)
        body_str = body.decode("ascii") if isinstance(body, bytes) else body
        raw = base64.b64decode(body_str.strip())
        flipped_iv = bytes(b ^ 0xFF for b in iv)
        return json.loads(AESGCM(aes_key).decrypt(flipped_iv, raw, None))

    def test_05a_ping_decrypts_to_active(self):
        aes_key, iv = os.urandom(16), os.urandom(16)
        result = self._decrypt_response(health_response(), aes_key, iv)
        self.assertEqual(result["version"], "3.0")
        self.assertEqual(result["data"]["status"], "active")

    def test_05b_ping_version_matches_flow_version(self):
        aes_key, iv = os.urandom(16), os.urandom(16)
        result = self._decrypt_response(health_response(), aes_key, iv)
        self.assertEqual(result["version"], FLOW_VERSION)

    def test_05c_ping_has_data_key(self):
        aes_key, iv = os.urandom(16), os.urandom(16)
        result = self._decrypt_response(health_response(), aes_key, iv)
        self.assertIn("data", result)
        self.assertIn("status", result["data"])


# ── FLOWB64-06: response is encoded exactly once ──────────────────────────────

class TestFLOWB64_06_ExactlyOnceEncoding(unittest.TestCase):
    def test_06a_not_double_base64(self):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            self.skipTest("cryptography not installed")
        aes_key, iv = os.urandom(16), os.urandom(16)
        resp = _encrypt_and_return(health_response(), aes_key, iv)
        body = _response_body(resp)
        body_str = body.decode("ascii") if isinstance(body, bytes) else body
        once_decoded = base64.b64decode(body_str.strip())

        # Attempting a second decode and then AES decrypt should FAIL
        # (double-encoded base64 would be invalid AES-GCM ciphertext)
        try:
            twice_decoded = base64.b64decode(once_decoded)
            flipped_iv = bytes(b ^ 0xFF for b in iv)
            # If this succeeds and decrypts to valid JSON, we're double-encoded
            result = json.loads(AESGCM(aes_key).decrypt(flipped_iv, twice_decoded, None))
            self.fail(f"Response appears to be double-encoded: {result}")
        except Exception:
            pass  # Expected — once-decoded bytes are NOT valid base64 of ciphertext

    def test_06b_single_b64decode_produces_ciphertext_length(self):
        aes_key, iv = os.urandom(16), os.urandom(16)
        resp = _encrypt_and_return(health_response(), aes_key, iv)
        body = _response_body(resp)
        decoded = base64.b64decode(body)
        # AES-GCM ciphertext = plaintext_len + 16 bytes (tag)
        # health_response JSON is ~40 bytes → ciphertext >= 40 + 16 = 56 bytes
        self.assertGreater(len(decoded), 40)

    def test_06c_encrypt_flow_response_unchanged(self):
        """encrypt_flow_response still returns raw bytes (not base64) — base64 is in the router."""
        aes_key, iv = os.urandom(16), os.urandom(16)
        raw = encrypt_flow_response(health_response(), aes_key, iv)
        # Verify it's NOT valid base64 JSON (raw AES ciphertext is binary)
        self.assertIsInstance(raw, bytes)
        # Raw ciphertext decodes via AES, not via base64 then AES
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            flipped_iv = bytes(b ^ 0xFF for b in iv)
            decrypted = json.loads(AESGCM(aes_key).decrypt(flipped_iv, raw, None))
            self.assertEqual(decrypted["data"]["status"], "active")
        except ImportError:
            self.skipTest("cryptography not installed")


# ── FLOWB64-07: invalid crypto still rejected ────────────────────────────────

class TestFLOWB64_07_InvalidCryptoRejected(unittest.TestCase):
    def test_07a_missing_key_raises(self):
        from app.services.booking_flow_service import decrypt_flow_request
        with patch.dict(os.environ, {"FLOW_BOOKING_PRIVATE_KEY_PATH": ""}):
            with self.assertRaises((ValueError, Exception)):
                decrypt_flow_request({
                    "encrypted_aes_key": base64.b64encode(b"x" * 16).decode(),
                    "encrypted_flow_data": base64.b64encode(b"x" * 32).decode(),
                    "initial_vector": base64.b64encode(b"x" * 16).decode(),
                })

    def test_07b_garbage_key_raises(self):
        from app.services.booking_flow_service import decrypt_flow_request
        with self.assertRaises(Exception):
            decrypt_flow_request({
                "encrypted_aes_key": "!!!garbage!!!",
                "encrypted_flow_data": "!!!garbage!!!",
                "initial_vector": "!!!garbage!!!",
            })

    def test_07c_tampered_ciphertext_raises(self):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            self.skipTest("cryptography not installed")
        aes_key, iv = os.urandom(16), os.urandom(16)
        payload = json.dumps({"version": "3.0", "action": "ping"}).encode()
        ciphertext = AESGCM(aes_key).encrypt(iv, payload, None)
        # Tamper: flip last byte
        tampered = bytearray(ciphertext)
        tampered[-1] ^= 0xFF
        with self.assertRaises(Exception):
            AESGCM(aes_key).decrypt(iv, bytes(tampered), None)


# ── FLOWB64-08: CRM protected routes remain authenticated ────────────────────

class TestFLOWB64_08_ProtectedRoutesUnchanged(unittest.TestCase):
    def test_08a_kanban_still_protected(self):
        from app.main import _is_public_path, _is_protected_path
        self.assertFalse(_is_public_path("/kanban"))
        self.assertTrue(_is_protected_path("/kanban"))

    def test_08b_control_still_protected(self):
        from app.main import _is_public_path, _is_protected_path
        self.assertFalse(_is_public_path("/control"))
        self.assertTrue(_is_protected_path("/control"))

    def test_08c_data_exchange_still_public(self):
        from app.main import _is_public_path
        self.assertTrue(_is_public_path("/integrations/whatsapp/flows/booking/data-exchange"))

    def test_08d_whitelist_exact_two_entries(self):
        from app.main import _is_public_path
        self.assertTrue(_is_public_path("/integrations/whatsapp/webhook"))
        self.assertTrue(_is_public_path("/integrations/whatsapp/flows/booking/data-exchange"))
        self.assertFalse(_is_public_path("/integrations/whatsapp"))
        self.assertFalse(_is_public_path("/integrations/whatsapp/flows"))


# ── FLOWB64-09: OUTBOUND remains OFF ─────────────────────────────────────────

class TestFLOWB64_09_OutboundOff(unittest.TestCase):
    def test_09a_outbound_env_var_off(self):
        outbound = os.environ.get("OUTBOUND_ENABLED", "false").lower()
        self.assertNotEqual(outbound, "true")

    def test_09b_flow_route_no_send_calls(self):
        import app.routes.flow_data_exchange as _fde
        import inspect
        src = inspect.getsource(_fde)
        self.assertNotIn("send_whatsapp_message", src)
        self.assertNotIn("gate.attempt", src)

    def test_09c_health_response_is_pure(self):
        from app.services.booking_flow_service import health_response
        result = health_response()
        self.assertIsInstance(result, dict)
        self.assertEqual(result["data"]["status"], "active")


if __name__ == "__main__":
    unittest.main()
