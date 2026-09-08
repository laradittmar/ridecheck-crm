"""OPS-AUDIO-TRANSCRIPTION-RESILIENCE — a voice note must not vanish silently.

On 2026-09-08 three owner voice notes were lost. n8n ran all three; each died at the
`Transcribe Audio` node, which calls OUR OWN backend, not OpenAI directly. The backend
turned every upstream failure into a bare 502, so an OpenAI 429 `credit_balance_exhausted`
was indistinguishable from a transient gateway blip — and that masking sent the first
investigation chasing a non-existent n8n wedge for hours.

Two defects, both covered here: no retry at all, and no error fidelity. Plus the detection
gap that let the loss stay invisible, and the WAL forensic rule that caused the misdiagnosis.

AUDIO-01 transient 5xx retries and succeeds   AUDIO-06 no duplicate CE invocation
AUDIO-02 retry is bounded                     AUDIO-07 no transcript is ever invented
AUDIO-03 exhausted credit is NOT retried      AUDIO-08 error fidelity preserved
AUDIO-04 bad key is NOT retried               ALERT-01..03 stalled-transport detection
AUDIO-05 transport error retries              WAL-01..02 forensic rule
"""
from __future__ import annotations

import ast
import io
import json
import pathlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
for _m in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    sys.modules.setdefault(_m, types.ModuleType(_m))

import sqlalchemy as _sa
import sqlalchemy.dialects.postgresql as _pg
import sqlalchemy.dialects.postgresql.json as _pgj
_pg.JSONB = _sa.JSON
_pgj.JSONB = _sa.JSON

from fastapi import HTTPException
from urllib import error as urlerror

from app.api import whatsapp as wa_api

ALERT_SOURCE = (ROOT / "backend" / "app" / "services"
                / "unanswered_alert.py").read_text(encoding="utf-8-sig")

QUOTA_BODY = json.dumps({"error": {
    "message": "You have no credits remaining. Add credits to continue using the API.",
    "type": "insufficient_quota", "code": "credit_balance_exhausted"}})


def _http_error(code: int, body: str) -> urlerror.HTTPError:
    return urlerror.HTTPError("https://api.openai.com/v1/audio/transcriptions",
                              code, "err", {}, io.BytesIO(body.encode()))


class _OkResponse:
    def __init__(self, text="hola, quiero revisar un auto"):
        self._b = json.dumps({"text": text}).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return self._b


def _transcribe():
    return wa_api._transcribe_audio_bytes(
        media_id="MEDIA1", audio_bytes=b"\x00\x01ogg", mime_type="audio/ogg")


class TestRetryPolicy(unittest.TestCase):

    def setUp(self):
        self._key = patch.object(wa_api, "_require_openai_api_key", return_value="k")
        self._key.start()
        self._sleep = patch.object(wa_api._time, "sleep")
        self.sleep = self._sleep.start()

    def tearDown(self):
        self._key.stop(); self._sleep.stop()

    def test_audio_01_a_transient_5xx_is_retried_and_then_succeeds(self):
        """AUDIO-01 — the exact shape that lost the three voice notes, now survivable."""
        calls = []
        def side(req, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                raise _http_error(502, '{"error":{"type":"server_error"}}')
            return _OkResponse()
        with patch.object(wa_api.urlrequest, "urlopen", side):
            self.assertEqual(_transcribe(), "hola, quiero revisar un auto")
        self.assertEqual(len(calls), 2, "should have retried exactly once")

    def test_audio_02_retry_is_bounded(self):
        """AUDIO-02 — a persistently failing upstream must not retry forever."""
        calls = []
        def side(req, timeout=None):
            calls.append(1)
            raise _http_error(503, '{"error":{"type":"server_error"}}')
        with patch.object(wa_api.urlrequest, "urlopen", side):
            with self.assertRaises(HTTPException) as ctx:
                _transcribe()
        self.assertEqual(len(calls), wa_api.TRANSCRIPTION_MAX_ATTEMPTS)
        self.assertEqual(len(calls), 3)
        self.assertEqual(ctx.exception.status_code, 502)

    def test_audio_03_exhausted_credit_is_not_retried(self):
        """AUDIO-03 — the real 2026-09-08 failure. Retrying an unpaid balance only
        makes the customer wait longer for the same answer."""
        calls = []
        def side(req, timeout=None):
            calls.append(1)
            raise _http_error(429, QUOTA_BODY)
        with patch.object(wa_api.urlrequest, "urlopen", side):
            with self.assertRaises(HTTPException) as ctx:
                _transcribe()
        self.assertEqual(len(calls), 1, "permanent condition must fail fast")
        self.assertIn("permanent", ctx.exception.detail)
        self.assertIn("credit_balance_exhausted", ctx.exception.detail)

    def test_audio_04_an_invalid_key_is_not_retried(self):
        calls = []
        def side(req, timeout=None):
            calls.append(1)
            raise _http_error(401, '{"error":{"code":"invalid_api_key"}}')
        with patch.object(wa_api.urlrequest, "urlopen", side):
            with self.assertRaises(HTTPException):
                _transcribe()
        self.assertEqual(len(calls), 1)

    def test_audio_05_a_transport_error_is_retried(self):
        calls = []
        def side(req, timeout=None):
            calls.append(1)
            if len(calls) < 3:
                raise urlerror.URLError("connection reset")
            return _OkResponse()
        with patch.object(wa_api.urlrequest, "urlopen", side):
            self.assertEqual(_transcribe(), "hola, quiero revisar un auto")
        self.assertEqual(len(calls), 3)

    def test_a_true_rate_limit_is_still_treated_as_transient(self):
        """429 is ambiguous: rate limiting clears, an unpaid balance does not."""
        kind, code = wa_api._classify_transcription_error(
            429, '{"error":{"type":"rate_limit_exceeded","code":"rate_limit_exceeded"}}')
        self.assertEqual(kind, "transient")
        kind2, code2 = wa_api._classify_transcription_error(429, QUOTA_BODY)
        self.assertEqual(kind2, "permanent")
        self.assertEqual(code2, "credit_balance_exhausted")

    def test_audio_07_no_transcript_is_ever_invented(self):
        """AUDIO-07 — failure must raise, never return a plausible-sounding string."""
        with patch.object(wa_api.urlrequest, "urlopen",
                          lambda *a, **k: (_ for _ in ()).throw(_http_error(500, "{}"))):
            with self.assertRaises(HTTPException):
                _transcribe()
        # and an empty upstream transcript is a failure, not an empty message
        class _Empty(_OkResponse):
            def __init__(self): self._b = json.dumps({"text": "   "}).encode()
        with patch.object(wa_api.urlrequest, "urlopen", lambda *a, **k: _Empty()):
            with self.assertRaises(HTTPException):
                _transcribe()

    def test_audio_08_error_fidelity_reaches_the_caller(self):
        """AUDIO-08 — the masking that caused a wrong root cause for hours."""
        with patch.object(wa_api.urlrequest, "urlopen",
                          lambda *a, **k: (_ for _ in ()).throw(_http_error(429, QUOTA_BODY))):
            with self.assertRaises(HTTPException) as ctx:
                _transcribe()
        detail = ctx.exception.detail
        self.assertIn("HTTP 429", detail, "the upstream status must survive")
        self.assertIn("credit_balance_exhausted", detail)
        self.assertNotEqual(detail, "OpenAI transcription failed")

    def test_backoff_is_applied_between_attempts_only(self):
        calls = []
        def side(req, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                raise _http_error(500, "{}")
            return _OkResponse()
        with patch.object(wa_api.urlrequest, "urlopen", side):
            _transcribe()
        self.assertEqual(self.sleep.call_count, 1)
        self.assertIn(self.sleep.call_args[0][0], wa_api.TRANSCRIPTION_BACKOFF_SECONDS)


class TestNoDuplication(unittest.TestCase):
    """AUDIO-06 — retries live strictly inside the transcription call."""

    def test_audio_06_retry_does_not_re_enter_ce_or_outbound(self):
        src = (ROOT / "backend" / "app" / "api" / "whatsapp.py").read_text(encoding="utf-8-sig")
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "_transcribe_audio_bytes")
        for forbidden in ("conversation/handle", "OutboundSafetyGate", "gate.attempt",
                          "_send_text_to_wa", "_send_flow_button", "ConversationEngine"):
            self.assertNotIn(forbidden, fn,
                             "the retry loop must not touch CE or outbound")
        # the endpoint persists the transcript once, after the retry loop resolves
        ep = next(ast.unparse(n) for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "transcribe_media")
        self.assertEqual(ep.count("_transcribe_audio_bytes"), 1)
        self.assertEqual(ep.count("db.commit()"), 1)


class TestStalledTransportDetection(unittest.TestCase):

    def test_alert_01_the_check_exists_and_is_wired_into_the_loop(self):
        self.assertIn("def _check_forwarded_but_unfinished", ALERT_SOURCE)
        run = next(ast.unparse(n) for n in ast.walk(ast.parse(ALERT_SOURCE))
                   if isinstance(n, ast.FunctionDef) and n.name == "_run_check")
        self.assertIn("_check_forwarded_but_unfinished(db)", run)

    def test_alert_02_it_targets_exactly_the_invisible_state(self):
        """ALERT-02 — status='triggered' is 'forwarded to n8n, never returned from CE',
        the state the pre-existing SLA check structurally cannot see."""
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(ALERT_SOURCE))
                  if isinstance(n, ast.FunctionDef) and n.name == "_check_forwarded_but_unfinished")
        norm = fn.replace("'", '"')
        self.assertIn('ae.status = "triggered"', norm)
        self.assertIn("unanswered_alert_sent_at IS NULL", fn)
        self.assertIn("excluded_phones", fn)
        # must NOT depend on the CE-written flags, or it would inherit the same blind spot
        self.assertNotIn("reply_required", fn)
        self.assertNotIn("alert_eligible", fn)

    def test_alert_03_it_surfaces_identifiers_not_message_content(self):
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(ALERT_SOURCE))
                  if isinstance(n, ast.FunctionDef) and n.name == "_check_forwarded_but_unfinished")
        self.assertIn("thread_id", fn)
        self.assertIn("wa_message_id", fn)
        self.assertIn("stage=forwarded_to_n8n_no_ce_completion", fn)
        self.assertNotIn("ae.text", fn)
        self.assertNotIn("row.text", fn)

    def test_the_threshold_is_the_agreed_180_seconds(self):
        from app.services.unanswered_alert import STALLED_TRANSPORT_THRESHOLD_SECONDS
        self.assertEqual(STALLED_TRANSPORT_THRESHOLD_SECONDS, 180)

    def test_the_pre_existing_sla_check_is_unchanged(self):
        from app.services.unanswered_alert import _ALERT_THRESHOLD_SECONDS
        self.assertEqual(_ALERT_THRESHOLD_SECONDS, 120)
        self.assertIn("ae.reply_required = true", ALERT_SOURCE)
        self.assertIn("ae.alert_eligible = true", ALERT_SOURCE)


class TestWalForensicRule(unittest.TestCase):
    """WAL-01/02 — the rule that would have prevented the wrong root cause."""

    def test_wal_01_the_rule_is_documented_where_an_operator_will_find_it(self):
        doc = (ROOT / "docs" / "operations" / "N8N_FORENSIC_READ_RULE.md")
        self.assertTrue(doc.exists(), "N8N_FORENSIC_READ_RULE.md missing")
        body = doc.read_text(encoding="utf-8")
        for token in ("-wal", "-shm", "WAL", "n8nEventLog"):
            self.assertIn(token, body)

    def test_wal_02_the_helper_copies_all_companion_files(self):
        script = ROOT / "scripts" / "n8n_forensic_copy.sh"
        self.assertTrue(script.exists(), "scripts/n8n_forensic_copy.sh missing")
        self.assertTrue(script.stat().st_mode & 0o111, "not executable")
        body = script.read_text(encoding="utf-8")
        for token in ("database.sqlite", "database.sqlite-wal", "database.sqlite-shm"):
            self.assertIn(token, body)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
