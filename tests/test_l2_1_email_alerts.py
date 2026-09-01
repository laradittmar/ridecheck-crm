"""L2.1-EMAIL-ALERTS — Resend unanswered-alert delivery tests.

EMAIL-01  Missing RESEND_API_KEY → explicit error log, no send, returns False
EMAIL-02  Missing recipient → explicit error log, no send, returns False
EMAIL-03  Successful Resend API response → returns True
EMAIL-04  Resend HTTP error response → returns False, logs error
EMAIL-05  Resend network/URL error → returns False, logs error
EMAIL-06  No secret value appears in logs
EMAIL-07  unanswered_alert._send_alert_email invokes Resend, not SMTP
EMAIL-08  unanswered_alert logs warning when RESEND_API_KEY missing (no crash)
EMAIL-09  unanswered_alert logs warning when recipient missing (no crash)

All tests offline: no real Resend API calls.
"""
from __future__ import annotations

import io
import json
import logging
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from urllib import error as urllib_error

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if not BACKEND_DIR.exists():
    BACKEND_DIR = ROOT_DIR
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Stub heavy dependencies before any app import
import sqlalchemy as _sa
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = _sa.JSON  # type: ignore[attr-defined]
_pg_json.JSONB = _sa.JSON     # type: ignore[attr-defined]

for _mod in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

if "psycopg2" not in sys.modules:
    _pg = types.ModuleType("psycopg2")
    _pg.extensions = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2"] = _pg
    sys.modules["psycopg2.extensions"] = _pg.extensions

import os
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

# Pre-stub app.db so unanswered_alert can be imported even on hosts with old SQLAlchemy
if "app.db" not in sys.modules:
    _db_stub = types.ModuleType("app.db")
    _db_stub.SessionLocal = MagicMock()
    _db_stub.Base = MagicMock()
    sys.modules["app.db"] = _db_stub
if "app.models" not in sys.modules:
    sys.modules["app.models"] = types.ModuleType("app.models")

from app.services.resend_email import send_unanswered_alert


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_FAKE_KEY = "re_TESTKEY_notreal"
_FROM = "notificaciones@ridecheck.ar"
_TO = "ridecheckassistance@gmail.com"


def _fake_ok_response(body: dict | None = None) -> MagicMock:
    """Mock a successful urllib response."""
    body = body or {"id": "abc123"}
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = json.dumps(body).encode("utf-8")
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL-01: Missing api_key → False + error log
# ─────────────────────────────────────────────────────────────────────────────

class TestMissingApiKey(unittest.TestCase):

    def test_email_01_missing_api_key_returns_false(self):
        """EMAIL-01: send_unanswered_alert with empty api_key returns False."""
        with self.assertLogs("app.services.resend_email", level="ERROR") as cm:
            result = send_unanswered_alert(
                api_key="",
                from_email=_FROM,
                to_email=_TO,
                thread_id=42,
                customer_name="Test User",
                threshold_minutes=2,
                reason="CE",
            )
        self.assertFalse(result)
        self.assertTrue(any("RESEND_API_KEY" in line or "not set" in line for line in cm.output),
                        "Must log error about missing API key")

    def test_email_01b_missing_api_key_no_network_call(self):
        """EMAIL-01b: No HTTP request made when api_key is empty."""
        with patch("app.services.resend_email.urlrequest.urlopen") as mock_open:
            send_unanswered_alert(
                api_key="",
                from_email=_FROM,
                to_email=_TO,
                thread_id=42,
                customer_name="Test User",
                threshold_minutes=2,
            )
            mock_open.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL-02: Missing recipient → False + error log
# ─────────────────────────────────────────────────────────────────────────────

class TestMissingRecipient(unittest.TestCase):

    def test_email_02_missing_recipient_returns_false(self):
        """EMAIL-02: send_unanswered_alert with empty to_email returns False."""
        with self.assertLogs("app.services.resend_email", level="ERROR") as cm:
            result = send_unanswered_alert(
                api_key=_FAKE_KEY,
                from_email=_FROM,
                to_email="",
                thread_id=42,
                customer_name="Test User",
                threshold_minutes=2,
            )
        self.assertFalse(result)
        self.assertTrue(any("recipient" in line.lower() or "not configured" in line.lower()
                            for line in cm.output),
                        "Must log error about missing recipient")

    def test_email_02b_missing_recipient_no_network_call(self):
        """EMAIL-02b: No HTTP request made when to_email is empty."""
        with patch("app.services.resend_email.urlrequest.urlopen") as mock_open:
            send_unanswered_alert(
                api_key=_FAKE_KEY,
                from_email=_FROM,
                to_email="",
                thread_id=42,
                customer_name="Test User",
                threshold_minutes=2,
            )
            mock_open.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL-03: Successful response → True
# ─────────────────────────────────────────────────────────────────────────────

class TestSuccessfulSend(unittest.TestCase):

    def test_email_03_successful_response_returns_true(self):
        """EMAIL-03: Resend 200 OK response → send_unanswered_alert returns True."""
        with patch("app.services.resend_email.urlrequest.urlopen",
                   return_value=_fake_ok_response()) as mock_open:
            result = send_unanswered_alert(
                api_key=_FAKE_KEY,
                from_email=_FROM,
                to_email=_TO,
                thread_id=7,
                customer_name="María García",
                threshold_minutes=2,
                reason="CE-SLA",
            )
        self.assertTrue(result)
        mock_open.assert_called_once()

    def test_email_03b_request_contains_correct_recipient(self):
        """EMAIL-03b: HTTP request body contains the correct recipient address."""
        captured_request = []

        def _capture(req, timeout=15):
            captured_request.append(req)
            return _fake_ok_response()

        with patch("app.services.resend_email.urlrequest.urlopen", side_effect=_capture):
            send_unanswered_alert(
                api_key=_FAKE_KEY,
                from_email=_FROM,
                to_email=_TO,
                thread_id=7,
                customer_name="Test",
                threshold_minutes=2,
            )

        self.assertEqual(len(captured_request), 1)
        body = json.loads(captured_request[0].data.decode("utf-8"))
        self.assertIn(_TO, body["to"])
        self.assertEqual(body["from"], _FROM)

    def test_email_03c_subject_contains_thread_id(self):
        """EMAIL-03c: Email subject includes thread ID."""
        captured_request = []

        def _capture(req, timeout=15):
            captured_request.append(req)
            return _fake_ok_response()

        with patch("app.services.resend_email.urlrequest.urlopen", side_effect=_capture):
            send_unanswered_alert(
                api_key=_FAKE_KEY,
                from_email=_FROM,
                to_email=_TO,
                thread_id=99,
                customer_name="Pedro López",
                threshold_minutes=2,
            )

        body = json.loads(captured_request[0].data.decode("utf-8"))
        self.assertIn("99", body["subject"])


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL-04: HTTP error response → False
# ─────────────────────────────────────────────────────────────────────────────

class TestHttpErrorResponse(unittest.TestCase):

    def test_email_04_http_error_returns_false(self):
        """EMAIL-04: Resend API HTTP error → returns False."""
        http_err = urllib_error.HTTPError(
            url="https://api.resend.com/emails",
            code=422,
            msg="Unprocessable Entity",
            hdrs={},
            fp=io.BytesIO(b'{"message":"Invalid from address"}'),
        )
        with patch("app.services.resend_email.urlrequest.urlopen", side_effect=http_err):
            with self.assertLogs("app.services.resend_email", level="ERROR") as cm:
                result = send_unanswered_alert(
                    api_key=_FAKE_KEY,
                    from_email=_FROM,
                    to_email=_TO,
                    thread_id=5,
                    customer_name="Test",
                    threshold_minutes=2,
                )
        self.assertFalse(result)
        self.assertTrue(any("422" in line or "HTTP error" in line.lower() for line in cm.output))


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL-05: Network/URL error → False
# ─────────────────────────────────────────────────────────────────────────────

class TestNetworkError(unittest.TestCase):

    def test_email_05_url_error_returns_false(self):
        """EMAIL-05: Network error → returns False, logs error."""
        url_err = urllib_error.URLError("Connection refused")
        with patch("app.services.resend_email.urlrequest.urlopen", side_effect=url_err):
            with self.assertLogs("app.services.resend_email", level="ERROR") as cm:
                result = send_unanswered_alert(
                    api_key=_FAKE_KEY,
                    from_email=_FROM,
                    to_email=_TO,
                    thread_id=5,
                    customer_name="Test",
                    threshold_minutes=2,
                )
        self.assertFalse(result)
        self.assertTrue(any("URL error" in line or "url error" in line.lower() for line in cm.output))


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL-06: No secret leakage in logs
# ─────────────────────────────────────────────────────────────────────────────

class TestNoSecretLeakage(unittest.TestCase):

    def test_email_06_api_key_not_logged_on_error(self):
        """EMAIL-06: RESEND_API_KEY value must not appear in any log output."""
        secret_key = "re_SUPERSECRET_DONOTLOG_12345"
        http_err = urllib_error.HTTPError(
            url="https://api.resend.com/emails",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=io.BytesIO(b'{"message":"server error"}'),
        )
        with patch("app.services.resend_email.urlrequest.urlopen", side_effect=http_err):
            with self.assertLogs("app.services.resend_email", level="ERROR") as cm:
                send_unanswered_alert(
                    api_key=secret_key,
                    from_email=_FROM,
                    to_email=_TO,
                    thread_id=5,
                    customer_name="Test",
                    threshold_minutes=2,
                )
        all_log_output = "\n".join(cm.output)
        self.assertNotIn(secret_key, all_log_output,
                         "API key value must not appear in log output")

    def test_email_06b_api_key_not_logged_on_success(self):
        """EMAIL-06b: API key value must not appear in success log."""
        secret_key = "re_SUPERSECRET_SUCCESS_67890"
        with patch("app.services.resend_email.urlrequest.urlopen",
                   return_value=_fake_ok_response()):
            with self.assertLogs("app.services.resend_email", level="INFO") as cm:
                send_unanswered_alert(
                    api_key=secret_key,
                    from_email=_FROM,
                    to_email=_TO,
                    thread_id=10,
                    customer_name="Test",
                    threshold_minutes=2,
                )
        all_log_output = "\n".join(cm.output)
        self.assertNotIn(secret_key, all_log_output,
                         "API key value must not appear in success log output")


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL-07: unanswered_alert._send_alert_email invokes Resend, not SMTP
# ─────────────────────────────────────────────────────────────────────────────

class TestUnansweredAlertInvokesResend(unittest.TestCase):

    def test_email_07_invokes_resend_not_smtp(self):
        """EMAIL-07: _send_alert_email uses send_unanswered_alert from resend_email, not smtplib."""
        import inspect
        import app.services.unanswered_alert as _ua
        source = inspect.getsource(_ua)
        # Must NOT import or use smtplib
        self.assertNotIn("smtplib", source,
                         "_send_alert_email must not use smtplib after L2.1")
        # Must import and call Resend
        self.assertIn("resend_email", source)
        self.assertIn("send_unanswered_alert", source)

    def test_email_07b_send_alert_calls_resend_with_correct_recipient(self):
        """EMAIL-07b: _send_alert_email passes INTERNAL_BOOKING_EMAIL_TO to Resend."""
        from app.services.unanswered_alert import _send_alert_email
        from unittest.mock import MagicMock

        captured: list[dict] = []

        def _fake_send_unanswered_alert(**kwargs):
            captured.append(kwargs)
            return True

        mock_settings = MagicMock()
        mock_settings.resend_api_key = _FAKE_KEY
        mock_settings.internal_booking_email_to = "ridecheckassistance@gmail.com"
        mock_settings.internal_booking_email_from = "notificaciones@ridecheck.ar"

        with patch("app.services.unanswered_alert.get_settings", return_value=mock_settings):
            with patch("app.services.resend_email.send_unanswered_alert",
                       side_effect=_fake_send_unanswered_alert):
                _send_alert_email(thread_id=55, customer_name="Test Cliente", reason="CE-SLA")

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["to_email"], "ridecheckassistance@gmail.com")
        self.assertEqual(captured[0]["thread_id"], 55)
        self.assertEqual(captured[0]["reason"], "CE-SLA")


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL-08: Missing RESEND_API_KEY in unanswered_alert → warning, no crash
# ─────────────────────────────────────────────────────────────────────────────

class TestUnansweredAlertMissingKey(unittest.TestCase):

    def test_email_08_missing_resend_key_logs_warning_no_crash(self):
        """EMAIL-08: _send_alert_email with no RESEND_API_KEY logs warning, does not raise."""
        from app.services.unanswered_alert import _send_alert_email

        mock_settings = MagicMock()
        mock_settings.resend_api_key = ""
        mock_settings.internal_booking_email_to = "ridecheckassistance@gmail.com"
        mock_settings.internal_booking_email_from = "notificaciones@ridecheck.ar"

        with patch("app.services.unanswered_alert.get_settings", return_value=mock_settings):
            with self.assertLogs("app.services.unanswered_alert", level="WARNING") as cm:
                _send_alert_email(thread_id=66, customer_name="Sin clave", reason="CE")

        self.assertTrue(any("RESEND_API_KEY" in line for line in cm.output))


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL-09: Missing recipient in unanswered_alert → warning, no crash
# ─────────────────────────────────────────────────────────────────────────────

class TestUnansweredAlertMissingRecipient(unittest.TestCase):

    def test_email_09_missing_recipient_logs_warning_no_crash(self):
        """EMAIL-09: _send_alert_email with no recipient logs warning, does not raise."""
        from app.services.unanswered_alert import _send_alert_email

        mock_settings = MagicMock()
        mock_settings.resend_api_key = _FAKE_KEY
        mock_settings.internal_booking_email_to = ""
        mock_settings.internal_booking_email_from = "notificaciones@ridecheck.ar"

        with patch("app.services.unanswered_alert.get_settings", return_value=mock_settings):
            with self.assertLogs("app.services.unanswered_alert", level="WARNING") as cm:
                _send_alert_email(thread_id=77, customer_name="Sin destino", reason="HUMAN")

        self.assertTrue(any("INTERNAL_BOOKING_EMAIL_TO" in line for line in cm.output))


if __name__ == "__main__":
    unittest.main()
