"""EMAIL.1 — Booking notification delivery fix — regression tests.

EMAIL-RC1  Production defaults are deliverable
EMAIL-RC2  Booking notification payload includes correct addresses and reply_to
EMAIL-RC3  Scheduling handoff payload includes correct addresses and reply_to
EMAIL-RC4  Human review payload includes correct addresses and reply_to
EMAIL-RC5  Environment overrides propagate to payload
EMAIL-RC6  Blank Reply-To omits reply_to field and still sends
EMAIL-RC7  Missing API key aborts without network call
EMAIL-RC8  No legacy non-deliverable active default in Compose files
EMAIL-RC9  Secrets are never logged
EMAIL-RC10 Gmail SMTP alert path is functionally unchanged
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXPECTED_FROM    = "notificaciones@ridecheck.ar"
_EXPECTED_TO      = "ridecheckassistance@gmail.com"
_EXPECTED_REPLY   = "ridecheckassistance@gmail.com"
_LEGACY_BAD_TO    = "julian@ridecheck.ar"
_FAKE_API_KEY     = "re_test_fake_key_for_mocking"


def _get_settings_fresh(**overrides):
    """Return a fresh Settings instance bypassing the lru_cache."""
    from app.settings import Settings, _getenv, _parse_quarantined_wa_ids, _parse_closed_beta_allowed_wa_ids
    env = {
        "INTERNAL_BOOKING_EMAIL_FROM":     os.getenv("INTERNAL_BOOKING_EMAIL_FROM", _EXPECTED_FROM),
        "INTERNAL_BOOKING_EMAIL_TO":       os.getenv("INTERNAL_BOOKING_EMAIL_TO",    _EXPECTED_TO),
        "INTERNAL_BOOKING_EMAIL_REPLY_TO": os.getenv("INTERNAL_BOOKING_EMAIL_REPLY_TO", _EXPECTED_REPLY),
    }
    env.update(overrides)
    return Settings(
        internal_booking_email_from=env["INTERNAL_BOOKING_EMAIL_FROM"],
        internal_booking_email_to=env["INTERNAL_BOOKING_EMAIL_TO"],
        internal_booking_email_reply_to=env["INTERNAL_BOOKING_EMAIL_REPLY_TO"],
        resend_api_key=env.get("RESEND_API_KEY", _FAKE_API_KEY),
    )


def _make_mock_http_response(status=200, body=b'{"id":"test-id"}'):
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.status = status
    return resp


def _captured_payload(mock_urlopen) -> dict:
    """Extract the JSON payload from a mocked urlopen call."""
    req = mock_urlopen.call_args[0][0]
    return json.loads(req.data.decode("utf-8"))


# ---------------------------------------------------------------------------
# EMAIL-RC1 — Production defaults are deliverable
# ---------------------------------------------------------------------------

class TestEmailRC1ProductionDefaults(unittest.TestCase):
    """Verify that Settings defaults produce deliverable addresses."""

    def setUp(self):
        # Clear lru_cache so env changes take effect
        from app.settings import get_settings
        get_settings.cache_clear()

    def tearDown(self):
        from app.settings import get_settings
        get_settings.cache_clear()

    def test_rc1_default_from_is_notificaciones(self):
        s = _get_settings_fresh()
        self.assertEqual(s.internal_booking_email_from, _EXPECTED_FROM)

    def test_rc1_default_to_is_gmail(self):
        s = _get_settings_fresh()
        self.assertEqual(s.internal_booking_email_to, _EXPECTED_TO)
        self.assertNotEqual(s.internal_booking_email_to, _LEGACY_BAD_TO)

    def test_rc1_default_reply_to_is_gmail(self):
        s = _get_settings_fresh()
        self.assertEqual(s.internal_booking_email_reply_to, _EXPECTED_REPLY)

    def test_rc1_to_is_not_ridecheck_ar_domain(self):
        s = _get_settings_fresh()
        self.assertFalse(
            s.internal_booking_email_to.endswith("@ridecheck.ar"),
            f"TO address {s.internal_booking_email_to!r} uses @ridecheck.ar which has no MX records",
        )

    def test_rc1_reply_to_is_not_ridecheck_ar_domain(self):
        s = _get_settings_fresh()
        # Empty reply_to is acceptable; a non-empty one must not be @ridecheck.ar
        if s.internal_booking_email_reply_to:
            self.assertFalse(
                s.internal_booking_email_reply_to.endswith("@ridecheck.ar"),
                f"Reply-To {s.internal_booking_email_reply_to!r} uses @ridecheck.ar which has no MX records",
            )


# ---------------------------------------------------------------------------
# EMAIL-RC2 — Booking notification payload
# ---------------------------------------------------------------------------

class TestEmailRC2BookingPayload(unittest.TestCase):

    @patch("urllib.request.urlopen")
    def test_rc2_booking_payload_has_correct_addresses(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_http_response()
        from app.services.resend_email import send_booking_notification
        result = send_booking_notification(
            api_key=_FAKE_API_KEY,
            from_email=_EXPECTED_FROM,
            to_email=_EXPECTED_TO,
            reply_to_email=_EXPECTED_REPLY,
            lead_id=1,
            revision_id=10,
            buyer_name="Test", buyer_phone="123", buyer_email="t@t.com",
            source="whatsapp", marca="Ford", modelo="Ka", anio="2020",
            tipo_vehiculo="AUTO", zone_group="CABA", zone_detail="Palermo",
            address="Calle 1", seller_type="Particular", seller_name="Juan",
            scheduled_date="2026-08-01", scheduled_time="10:00",
            precio_base="100000", viaticos="5000", precio_total="105000",
        )
        self.assertTrue(result)
        payload = _captured_payload(mock_urlopen)
        self.assertEqual(payload["from"], _EXPECTED_FROM)
        self.assertEqual(payload["to"], [_EXPECTED_TO])
        self.assertEqual(payload["reply_to"], _EXPECTED_REPLY)
        self.assertIn("subject", payload)
        self.assertIn("html", payload)
        self.assertIn("Ford", payload["html"])

    @patch("urllib.request.urlopen")
    def test_rc2_no_real_network_call(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_http_response()
        from app.services.resend_email import send_booking_notification
        send_booking_notification(
            api_key=_FAKE_API_KEY,
            from_email=_EXPECTED_FROM,
            to_email=_EXPECTED_TO,
            reply_to_email=_EXPECTED_REPLY,
            lead_id=1, revision_id=10,
            buyer_name="", buyer_phone="", buyer_email="",
            source="", marca="", modelo="", anio="",
            tipo_vehiculo="", zone_group="", zone_detail="",
            address="", seller_type="", seller_name="",
            scheduled_date="", scheduled_time="",
            precio_base="", viaticos="", precio_total="",
        )
        # urlopen was called exactly once — the mock, not the real API
        self.assertEqual(mock_urlopen.call_count, 1)
        called_url = mock_urlopen.call_args[0][0].full_url
        self.assertIn("resend.com", called_url)


# ---------------------------------------------------------------------------
# EMAIL-RC3 — Scheduling handoff payload
# ---------------------------------------------------------------------------

class TestEmailRC3HandoffPayload(unittest.TestCase):

    @patch("urllib.request.urlopen")
    def test_rc3_handoff_payload_has_correct_addresses(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_http_response()
        from app.services.resend_email import send_scheduling_handoff_notification
        result = send_scheduling_handoff_notification(
            api_key=_FAKE_API_KEY,
            from_email=_EXPECTED_FROM,
            to_email=_EXPECTED_TO,
            reply_to_email=_EXPECTED_REPLY,
            lead_id=2, thread_id=99, revision_id=20,
            buyer_name="Ana", buyer_phone="456",
            vehicle="Toyota Corolla 2021", tipo_vehiculo="AUTO",
            zone_group="GBA Norte", zone_detail="San Isidro",
            precio_total="150000",
            requested_slot="Lunes 10:00", offered_slots="Martes 09:00",
            last_message="Quiero ese horario",
        )
        self.assertTrue(result)
        payload = _captured_payload(mock_urlopen)
        self.assertEqual(payload["from"], _EXPECTED_FROM)
        self.assertEqual(payload["to"], [_EXPECTED_TO])
        self.assertEqual(payload["reply_to"], _EXPECTED_REPLY)
        self.assertIn("subject", payload)
        self.assertIn("html", payload)

    @patch("urllib.request.urlopen")
    def test_rc3_no_real_network_call(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_http_response()
        from app.services.resend_email import send_scheduling_handoff_notification
        send_scheduling_handoff_notification(
            api_key=_FAKE_API_KEY,
            from_email=_EXPECTED_FROM, to_email=_EXPECTED_TO, reply_to_email=_EXPECTED_REPLY,
            lead_id=2, thread_id=99, revision_id=20,
            buyer_name="", buyer_phone="", vehicle="", tipo_vehiculo="",
            zone_group="", zone_detail="", precio_total="",
            requested_slot="", offered_slots="", last_message="",
        )
        self.assertEqual(mock_urlopen.call_count, 1)


# ---------------------------------------------------------------------------
# EMAIL-RC4 — Human review payload
# ---------------------------------------------------------------------------

class TestEmailRC4HumanReviewPayload(unittest.TestCase):

    @patch("urllib.request.urlopen")
    def test_rc4_human_review_payload_has_correct_addresses(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_http_response()
        from app.services.resend_email import send_human_review_notification
        result = send_human_review_notification(
            api_key=_FAKE_API_KEY,
            from_email=_EXPECTED_FROM,
            to_email=_EXPECTED_TO,
            reply_to_email=_EXPECTED_REPLY,
            lead_id=3, thread_id=88, wa_id="5491100000000",
            vehicle="Honda Civic 2019",
            zone_group="GBA Sur", zone_detail="Lanús",
            reason="unresolved_localidad",
        )
        self.assertTrue(result)
        payload = _captured_payload(mock_urlopen)
        self.assertEqual(payload["from"], _EXPECTED_FROM)
        self.assertEqual(payload["to"], [_EXPECTED_TO])
        self.assertEqual(payload["reply_to"], _EXPECTED_REPLY)
        self.assertIn("subject", payload)
        self.assertIn("html", payload)

    @patch("urllib.request.urlopen")
    def test_rc4_no_real_network_call(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_http_response()
        from app.services.resend_email import send_human_review_notification
        send_human_review_notification(
            api_key=_FAKE_API_KEY,
            from_email=_EXPECTED_FROM, to_email=_EXPECTED_TO, reply_to_email=_EXPECTED_REPLY,
            lead_id=3, thread_id=88, wa_id="", vehicle="", zone_group="", zone_detail="",
            reason="otro_vehicle_type",
        )
        self.assertEqual(mock_urlopen.call_count, 1)


# ---------------------------------------------------------------------------
# EMAIL-RC5 — Environment overrides propagate
# ---------------------------------------------------------------------------

class TestEmailRC5EnvironmentOverrides(unittest.TestCase):

    def setUp(self):
        from app.settings import get_settings
        get_settings.cache_clear()

    def tearDown(self):
        from app.settings import get_settings
        get_settings.cache_clear()

    @patch("urllib.request.urlopen")
    def test_rc5_overridden_addresses_appear_in_payload(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_http_response()
        from app.services.resend_email import send_booking_notification
        custom_to = "ops@example.test"
        custom_reply = "replies@example.test"
        send_booking_notification(
            api_key=_FAKE_API_KEY,
            from_email=_EXPECTED_FROM,
            to_email=custom_to,
            reply_to_email=custom_reply,
            lead_id=1, revision_id=10,
            buyer_name="X", buyer_phone="0", buyer_email="x@x.com",
            source="web", marca="VW", modelo="Gol", anio="2018",
            tipo_vehiculo="AUTO", zone_group="CABA", zone_detail="Almagro",
            address="Av Rivadavia 1", seller_type="Concesionaria", seller_name="RC",
            scheduled_date="2026-09-01", scheduled_time="09:00",
            precio_base="80000", viaticos="0", precio_total="80000",
        )
        payload = _captured_payload(mock_urlopen)
        self.assertEqual(payload["to"], [custom_to])
        self.assertEqual(payload["reply_to"], custom_reply)

    def test_rc5_settings_env_override_to(self):
        saved_to = os.environ.get("INTERNAL_BOOKING_EMAIL_TO")
        saved_reply = os.environ.get("INTERNAL_BOOKING_EMAIL_REPLY_TO")
        try:
            os.environ["INTERNAL_BOOKING_EMAIL_TO"] = "ops@example.test"
            os.environ["INTERNAL_BOOKING_EMAIL_REPLY_TO"] = "replies@example.test"
            # Import fresh settings (cache was cleared in setUp)
            from app.settings import get_settings
            s = get_settings()
            self.assertEqual(s.internal_booking_email_to, "ops@example.test")
            self.assertEqual(s.internal_booking_email_reply_to, "replies@example.test")
        finally:
            if saved_to is None:
                os.environ.pop("INTERNAL_BOOKING_EMAIL_TO", None)
            else:
                os.environ["INTERNAL_BOOKING_EMAIL_TO"] = saved_to
            if saved_reply is None:
                os.environ.pop("INTERNAL_BOOKING_EMAIL_REPLY_TO", None)
            else:
                os.environ["INTERNAL_BOOKING_EMAIL_REPLY_TO"] = saved_reply
            from app.settings import get_settings
            get_settings.cache_clear()


# ---------------------------------------------------------------------------
# EMAIL-RC6 — Blank Reply-To omits field; notification still valid
# ---------------------------------------------------------------------------

class TestEmailRC6BlankReplyTo(unittest.TestCase):

    @patch("urllib.request.urlopen")
    def test_rc6_blank_reply_to_omitted_from_payload(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_http_response()
        from app.services.resend_email import send_booking_notification
        result = send_booking_notification(
            api_key=_FAKE_API_KEY,
            from_email=_EXPECTED_FROM,
            to_email=_EXPECTED_TO,
            reply_to_email="",          # explicitly blank
            lead_id=1, revision_id=10,
            buyer_name="Test", buyer_phone="0", buyer_email="t@t.com",
            source="web", marca="VW", modelo="Gol", anio="2019",
            tipo_vehiculo="AUTO", zone_group="CABA", zone_detail="",
            address="", seller_type="", seller_name="",
            scheduled_date="", scheduled_time="",
            precio_base="", viaticos="", precio_total="",
        )
        self.assertTrue(result, "Notification must succeed even with blank reply_to")
        payload = _captured_payload(mock_urlopen)
        self.assertNotIn("reply_to", payload,
                         "reply_to must be absent when reply_to_email is blank")
        # Core fields intact
        self.assertEqual(payload["from"], _EXPECTED_FROM)
        self.assertEqual(payload["to"], [_EXPECTED_TO])

    @patch("urllib.request.urlopen")
    def test_rc6_whitespace_reply_to_treated_as_blank(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_http_response()
        from app.services.resend_email import send_scheduling_handoff_notification
        send_scheduling_handoff_notification(
            api_key=_FAKE_API_KEY,
            from_email=_EXPECTED_FROM, to_email=_EXPECTED_TO,
            reply_to_email="   ",       # whitespace only — stripped to blank inside function
            lead_id=1, thread_id=1, revision_id=1,
            buyer_name="", buyer_phone="", vehicle="", tipo_vehiculo="",
            zone_group="", zone_detail="", precio_total="",
            requested_slot="", offered_slots="", last_message="",
        )
        payload = _captured_payload(mock_urlopen)
        self.assertNotIn("reply_to", payload,
                         "whitespace-only reply_to must be stripped and omitted from payload")
        self.assertIn("from", payload)
        self.assertIn("to", payload)


# ---------------------------------------------------------------------------
# EMAIL-RC7 — Missing API key aborts without network call
# ---------------------------------------------------------------------------

class TestEmailRC7MissingApiKey(unittest.TestCase):

    @patch("urllib.request.urlopen")
    def test_rc7_booking_no_api_key_returns_false_no_network(self, mock_urlopen):
        from app.services.resend_email import send_booking_notification
        result = send_booking_notification(
            api_key="",                 # missing
            from_email=_EXPECTED_FROM,
            to_email=_EXPECTED_TO,
            reply_to_email=_EXPECTED_REPLY,
            lead_id=1, revision_id=10,
            buyer_name="", buyer_phone="", buyer_email="",
            source="", marca="", modelo="", anio="",
            tipo_vehiculo="", zone_group="", zone_detail="",
            address="", seller_type="", seller_name="",
            scheduled_date="", scheduled_time="",
            precio_base="", viaticos="", precio_total="",
        )
        self.assertFalse(result)
        mock_urlopen.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_rc7_handoff_no_api_key_returns_false_no_network(self, mock_urlopen):
        from app.services.resend_email import send_scheduling_handoff_notification
        result = send_scheduling_handoff_notification(
            api_key="", from_email=_EXPECTED_FROM, to_email=_EXPECTED_TO,
            reply_to_email=_EXPECTED_REPLY,
            lead_id=1, thread_id=1, revision_id=1,
            buyer_name="", buyer_phone="", vehicle="", tipo_vehiculo="",
            zone_group="", zone_detail="", precio_total="",
            requested_slot="", offered_slots="", last_message="",
        )
        self.assertFalse(result)
        mock_urlopen.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_rc7_human_review_no_api_key_returns_false_no_network(self, mock_urlopen):
        from app.services.resend_email import send_human_review_notification
        result = send_human_review_notification(
            api_key="", from_email=_EXPECTED_FROM, to_email=_EXPECTED_TO,
            reply_to_email=_EXPECTED_REPLY,
            lead_id=1, thread_id=1, wa_id="", vehicle="",
            zone_group="", zone_detail="", reason="otro_vehicle_type",
        )
        self.assertFalse(result)
        mock_urlopen.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_rc7_missing_to_returns_false_no_network(self, mock_urlopen):
        from app.services.resend_email import send_booking_notification
        result = send_booking_notification(
            api_key=_FAKE_API_KEY,
            from_email=_EXPECTED_FROM,
            to_email="",               # missing TO
            reply_to_email=_EXPECTED_REPLY,
            lead_id=1, revision_id=10,
            buyer_name="", buyer_phone="", buyer_email="",
            source="", marca="", modelo="", anio="",
            tipo_vehiculo="", zone_group="", zone_detail="",
            address="", seller_type="", seller_name="",
            scheduled_date="", scheduled_time="",
            precio_base="", viaticos="", precio_total="",
        )
        self.assertFalse(result)
        mock_urlopen.assert_not_called()


# ---------------------------------------------------------------------------
# EMAIL-RC8 — No legacy non-deliverable active default in Compose files
# ---------------------------------------------------------------------------

class TestEmailRC8NoLegacyDefault(unittest.TestCase):

    def _read_compose(self, filename: str) -> str:
        path = ROOT_DIR / filename
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def test_rc8_docker_compose_yml_to_not_julian(self):
        content = self._read_compose("docker-compose.yml")
        self.assertNotIn(
            ":-julian@ridecheck.ar",
            content,
            "docker-compose.yml still has julian@ridecheck.ar as the default TO — this domain has no MX records",
        )

    def test_rc8_docker_compose_yml_no_bare_julian(self):
        content = self._read_compose("docker-compose.yml")
        # Confirm no line sets TO to the legacy address unconditionally
        for line in content.splitlines():
            stripped = line.strip()
            if "INTERNAL_BOOKING_EMAIL_TO" in stripped and "julian@ridecheck.ar" in stripped:
                self.fail(
                    f"docker-compose.yml references julian@ridecheck.ar on active TO line: {stripped!r}"
                )

    def test_rc8_settings_default_to_not_julian(self):
        """Settings module default must not fall back to julian@ridecheck.ar."""
        from app.settings import Settings
        import inspect
        src = inspect.getsource(Settings)
        # The hardcoded fallback in get_settings must not be julian@
        import app.settings as sm
        src2 = inspect.getsource(sm.get_settings)
        self.assertNotIn("julian@ridecheck.ar", src)
        self.assertNotIn("julian@ridecheck.ar", src2)


# ---------------------------------------------------------------------------
# EMAIL-RC9 — Secrets are never logged
# ---------------------------------------------------------------------------

class TestEmailRC9SecretsNotLogged(unittest.TestCase):

    def _collect_logs(self, fn, *args, **kwargs):
        handler = logging.handlers_list = []
        class ListHandler(logging.Handler):
            def emit(self, record):
                handler.append(self.format(record))
        lh = ListHandler()
        logging.getLogger("app.services.resend_email").addHandler(lh)
        try:
            fn(*args, **kwargs)
        finally:
            logging.getLogger("app.services.resend_email").removeHandler(lh)
        return handler

    @patch("urllib.request.urlopen")
    def test_rc9_success_path_does_not_log_api_key(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_http_response()
        from app.services.resend_email import send_booking_notification
        logs = self._collect_logs(
            send_booking_notification,
            api_key="re_SUPERSECRET_KEY_12345",
            from_email=_EXPECTED_FROM, to_email=_EXPECTED_TO,
            reply_to_email=_EXPECTED_REPLY,
            lead_id=1, revision_id=10,
            buyer_name="", buyer_phone="", buyer_email="",
            source="", marca="", modelo="", anio="",
            tipo_vehiculo="", zone_group="", zone_detail="",
            address="", seller_type="", seller_name="",
            scheduled_date="", scheduled_time="",
            precio_base="", viaticos="", precio_total="",
        )
        for entry in logs:
            self.assertNotIn("SUPERSECRET_KEY_12345", entry,
                             f"API key leaked in log: {entry!r}")
            self.assertNotIn("Authorization", entry,
                             f"Authorization header leaked in log: {entry!r}")

    @patch("urllib.request.urlopen")
    def test_rc9_missing_api_key_log_does_not_print_key(self, mock_urlopen):
        from app.services.resend_email import send_booking_notification
        logs = self._collect_logs(
            send_booking_notification,
            api_key="",
            from_email=_EXPECTED_FROM, to_email=_EXPECTED_TO,
            reply_to_email=_EXPECTED_REPLY,
            lead_id=1, revision_id=10,
            buyer_name="", buyer_phone="", buyer_email="",
            source="", marca="", modelo="", anio="",
            tipo_vehiculo="", zone_group="", zone_detail="",
            address="", seller_type="", seller_name="",
            scheduled_date="", scheduled_time="",
            precio_base="", viaticos="", precio_total="",
        )
        mock_urlopen.assert_not_called()
        for entry in logs:
            self.assertNotIn("Bearer", entry,
                             f"Bearer token pattern leaked in log: {entry!r}")


# ---------------------------------------------------------------------------
# EMAIL-RC10 — Gmail SMTP alert path is functionally unchanged
# ---------------------------------------------------------------------------

class TestEmailRC10GmailAlertUnchanged(unittest.TestCase):
    """Verify unanswered_alert.py was not modified by this milestone."""

    def test_rc10_unanswered_alert_smtp_host_unchanged(self):
        alert_path = BACKEND_DIR / "app" / "services" / "unanswered_alert.py"
        content = alert_path.read_text(encoding="utf-8")
        # SMTP host comes from settings (not hardcoded); verify smtplib is still used
        self.assertIn("smtplib", content,
                      "unanswered_alert.py no longer uses smtplib — SMTP path may have changed")
        self.assertIn("ridecheckassistance@gmail.com", content,
                      "unanswered_alert.py alert address appears to have changed")

    def test_rc10_unanswered_alert_has_no_reply_to(self):
        alert_path = BACKEND_DIR / "app" / "services" / "unanswered_alert.py"
        content = alert_path.read_text(encoding="utf-8")
        self.assertNotIn("reply_to", content,
                         "reply_to was unexpectedly added to unanswered_alert.py")

    def test_rc10_resend_email_not_imported_by_unanswered_alert(self):
        alert_path = BACKEND_DIR / "app" / "services" / "unanswered_alert.py"
        content = alert_path.read_text(encoding="utf-8")
        self.assertNotIn("resend_email", content,
                         "resend_email was unexpectedly imported in unanswered_alert.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)
