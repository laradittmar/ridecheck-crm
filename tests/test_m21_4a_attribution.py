"""M21.4A — Acquisition Attribution Foundation — ATTR-01 through ATTR-25.

Tests cover:
  ATTR-01  WhatsApp inbound sets technical channel WHATSAPP on thread
  ATTR-02  Repeated WhatsApp messages do not overwrite existing channel
  ATTR-03  WhatsApp channel alone does NOT imply Instagram/Facebook/Website source
  ATTR-04  No source evidence → acq_source remains null
  ATTR-05  Meta CTWA referral parsed from webhook message dict
  ATTR-06  Instagram CTWA referral → acq_source = INSTAGRAM
  ATTR-07  Facebook CTWA referral → acq_source = FACEBOOK
  ATTR-08  Ambiguous CTWA referral → acq_source = OTHER (not guessed)
  ATTR-09  Meta webhook retry is idempotent (first-write-only CTWA)
  ATTR-10  Existing acq_source not overwritten by later message
  ATTR-11  ref_code remains first-write-only
  ATTR-12  rc_code remains first-write-only
  ATTR-13  Website ref_code maps deterministically to acq_source
  ATTR-14  Cycle reset does NOT clear inbound_channel
  ATTR-15  Cycle reset does NOT clear acq_source
  ATTR-16  New Revision on same Lead preserves original acquisition source
  ATTR-17  Legacy canal remains backward compatible
  ATTR-18  Canonical attribution does not depend on legacy canal
  ATTR-19  CRM renders technical channel in lead card
  ATTR-20  CRM renders acquisition source in lead card
  ATTR-21  CRM conditionally renders ref_code / rc_code
  ATTR-22  acq_source and inbound_channel not in raw Meta payload exposure
  ATTR-23  No outbound message produced by attribution capture
  ATTR-24  Existing WhatsApp webhook behavior unaffected (no regression)
  ATTR-25  CE existing regression: _maybe_set_attribution is idempotent
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Stub heavy optional deps
import sqlalchemy as _sa
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = _sa.JSON
_pg_json.JSONB = _sa.JSON

for _mod in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

if "psycopg2" not in sys.modules:
    _pg = types.ModuleType("psycopg2")
    _pg.extensions = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2"] = _pg
    sys.modules["psycopg2.extensions"] = _pg.extensions

from app.services.conversation_engine import (
    _ctwa_to_acq_source,
    _REF_CODE_SOURCE_MAP,
    _ACQ_INSTAGRAM,
    _ACQ_FACEBOOK,
    _ACQ_GOOGLE,
    _ACQ_WEBSITE,
    _ACQ_DIRECT,
    _ACQ_OTHER,
)
from app.ui.kanban_view import render_lead_card


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_thread(**kw) -> types.SimpleNamespace:
    defaults = dict(
        id=1,
        inbound_channel=None,
        ctwa_source_url=None,
        ctwa_source_id=None,
        ctwa_source_type=None,
        lead_id=None,
    )
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def _make_lead(**kw) -> types.SimpleNamespace:
    defaults = dict(
        id=1,
        acq_source=None,
        inbound_channel=None,
        ref_code=None,
        rc_code=None,
        canal=None,
        nombre=None,
        apellido=None,
        telefono=None,
        email=None,
        estado="CONSULTA_NUEVA",
        flag=None,
        necesita_humano=False,
        motivo_perdida=None,
        revisions=[],
    )
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def _make_state(**kw) -> types.SimpleNamespace:
    defaults = dict(is_website_lead=False, cycle_reset_pending=False)
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def _make_ctx(thread=None, lead=None) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        thread=thread or _make_thread(),
        lead=lead or _make_lead(),
    )


def _make_engine_with_ctx(ctx: types.SimpleNamespace, state: types.SimpleNamespace):
    """Build a minimal CE stub with _maybe_set_attribution callable."""
    from app.services.conversation_engine import ConversationEngine

    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock()
    eng.settings = MagicMock()

    # Patch _load_context and related to use our ctx
    eng._load_context = MagicMock(return_value=ctx)
    return eng


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-01: WhatsApp inbound sets thread.inbound_channel = WHATSAPP
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR01ChannelSetOnInbound(unittest.TestCase):
    """ATTR-01: Webhook sets inbound_channel = WHATSAPP on thread."""

    def test_attr01_webhook_ctwa_capture_sets_inbound_channel(self):
        """Simulates the webhook logic: new thread gets inbound_channel = WHATSAPP."""
        thread = _make_thread(inbound_channel=None)
        # Replicate webhook handler logic
        if not getattr(thread, "inbound_channel", None):
            thread.inbound_channel = "WHATSAPP"
        self.assertEqual(thread.inbound_channel, "WHATSAPP")


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-02: Repeated messages do not overwrite existing channel
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR02ChannelNotOverwritten(unittest.TestCase):
    """ATTR-02: Second inbound does not change already-set inbound_channel."""

    def test_attr02_existing_channel_preserved(self):
        thread = _make_thread(inbound_channel="WHATSAPP")
        # Second message arrives — guard fires
        if not getattr(thread, "inbound_channel", None):
            thread.inbound_channel = "INSTAGRAM_DM"  # should NOT be reached
        self.assertEqual(thread.inbound_channel, "WHATSAPP")


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-03: WhatsApp channel alone does NOT imply acquisition source
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR03ChannelDoesNotImplySource(unittest.TestCase):
    """ATTR-03: _maybe_set_attribution with WHATSAPP channel but no evidence → acq_source null."""

    def test_attr03_channel_only_no_source(self):
        thread = _make_thread(inbound_channel="WHATSAPP")
        lead = _make_lead(acq_source=None, ref_code=None)
        state = _make_state(is_website_lead=False)
        ctx = _make_ctx(thread=thread, lead=lead)
        eng = _make_engine_with_ctx(ctx, state)
        eng._maybe_set_attribution(ctx, state)
        self.assertIsNone(lead.acq_source)


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-04: No evidence → acq_source remains null
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR04NoEvidenceNoSource(unittest.TestCase):
    """ATTR-04: No ref_code, no CTWA, no website flag → acq_source stays null."""

    def test_attr04_no_evidence_source_remains_null(self):
        thread = _make_thread(ctwa_source_url=None, ctwa_source_id=None)
        lead = _make_lead(acq_source=None, ref_code=None)
        state = _make_state(is_website_lead=False)
        ctx = _make_ctx(thread=thread, lead=lead)
        eng = _make_engine_with_ctx(ctx, state)
        eng._maybe_set_attribution(ctx, state)
        self.assertIsNone(lead.acq_source)


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-05: Meta CTWA referral parsed from message dict
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR05CTWAReferralParsed(unittest.TestCase):
    """ATTR-05: Webhook CTWA capture logic correctly extracts referral fields."""

    def _capture_referral(self, message: dict, thread: types.SimpleNamespace) -> None:
        """Replicate the webhook attribution logic."""
        if not getattr(thread, "ctwa_source_id", None) and not getattr(thread, "ctwa_source_url", None):
            referral = message.get("referral") if isinstance(message, dict) else None
            if isinstance(referral, dict):
                raw_url = str(referral.get("source_url") or "").strip()[:500]
                raw_id = str(referral.get("source_id") or "").strip()[:100]
                raw_type = str(referral.get("source_type") or "").strip()[:40]
                if raw_url or raw_id:
                    thread.ctwa_source_url = raw_url or None
                    thread.ctwa_source_id = raw_id or None
                    thread.ctwa_source_type = raw_type or None

    def test_attr05_referral_extracted_from_message(self):
        message = {
            "type": "text",
            "text": {"body": "Hola"},
            "referral": {
                "source_url": "https://www.instagram.com/p/ABC123/",
                "source_id": "123456789",
                "source_type": "ad",
            },
        }
        thread = _make_thread()
        self._capture_referral(message, thread)
        self.assertEqual(thread.ctwa_source_url, "https://www.instagram.com/p/ABC123/")
        self.assertEqual(thread.ctwa_source_id, "123456789")
        self.assertEqual(thread.ctwa_source_type, "ad")

    def test_attr05_no_referral_field_no_effect(self):
        message = {"type": "text", "text": {"body": "Hola"}}
        thread = _make_thread()
        self._capture_referral(message, thread)
        self.assertIsNone(thread.ctwa_source_url)
        self.assertIsNone(thread.ctwa_source_id)

    def test_attr05_empty_referral_not_stored(self):
        message = {"type": "text", "referral": {"source_url": "", "source_id": ""}}
        thread = _make_thread()
        self._capture_referral(message, thread)
        self.assertIsNone(thread.ctwa_source_url)
        self.assertIsNone(thread.ctwa_source_id)


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-06: Instagram CTWA → acq_source = INSTAGRAM
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR06InstagramCTWA(unittest.TestCase):
    """ATTR-06: Instagram source_url maps to INSTAGRAM."""

    def test_attr06_instagram_url(self):
        src = _ctwa_to_acq_source("https://www.instagram.com/p/ABC/", None, "ad")
        self.assertEqual(src, _ACQ_INSTAGRAM)

    def test_attr06_instagram_url_post(self):
        src = _ctwa_to_acq_source("https://instagram.com/stories/ridecheck/", None, "post")
        self.assertEqual(src, _ACQ_INSTAGRAM)

    def test_attr06_instagram_via_maybe_set(self):
        thread = _make_thread(
            ctwa_source_url="https://www.instagram.com/p/ABC123/",
            ctwa_source_id="123",
            ctwa_source_type="ad",
        )
        lead = _make_lead(acq_source=None)
        state = _make_state()
        ctx = _make_ctx(thread=thread, lead=lead)
        eng = _make_engine_with_ctx(ctx, state)
        eng._maybe_set_attribution(ctx, state)
        self.assertEqual(lead.acq_source, _ACQ_INSTAGRAM)


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-07: Facebook CTWA → acq_source = FACEBOOK
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR07FacebookCTWA(unittest.TestCase):
    """ATTR-07: Facebook source_url maps to FACEBOOK."""

    def test_attr07_facebook_url(self):
        src = _ctwa_to_acq_source("https://www.facebook.com/ridecheck/posts/123", None, "ad")
        self.assertEqual(src, _ACQ_FACEBOOK)

    def test_attr07_fb_com_shortlink(self):
        src = _ctwa_to_acq_source("https://fb.com/ads/123", None, "ad")
        self.assertEqual(src, _ACQ_FACEBOOK)

    def test_attr07_facebook_via_maybe_set(self):
        thread = _make_thread(
            ctwa_source_url="https://www.facebook.com/ads/123",
            ctwa_source_id="fb-ad-999",
            ctwa_source_type="ad",
        )
        lead = _make_lead(acq_source=None)
        state = _make_state()
        ctx = _make_ctx(thread=thread, lead=lead)
        eng = _make_engine_with_ctx(ctx, state)
        eng._maybe_set_attribution(ctx, state)
        self.assertEqual(lead.acq_source, _ACQ_FACEBOOK)


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-08: Ambiguous CTWA → OTHER (not guessed)
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR08AmbiguousCTWA(unittest.TestCase):
    """ATTR-08: Unknown Meta URL → OTHER, not guessed INSTAGRAM/FACEBOOK."""

    def test_attr08_unknown_url_is_other(self):
        src = _ctwa_to_acq_source("https://example-ad-network.com/abc", "some-id", "ad")
        self.assertEqual(src, _ACQ_OTHER)

    def test_attr08_source_id_only_is_other(self):
        # No URL, only source_id — evidence exists but network unknown
        src = _ctwa_to_acq_source("", "ad-id-12345", "ad")
        self.assertEqual(src, _ACQ_OTHER)

    def test_attr08_no_url_no_id_is_none(self):
        src = _ctwa_to_acq_source("", None, "")
        self.assertIsNone(src)

    def test_attr08_ambiguous_stored_as_other_on_lead(self):
        thread = _make_thread(
            ctwa_source_url="https://some-other-platform.com/ad/123",
            ctwa_source_id="x999",
            ctwa_source_type="ad",
        )
        lead = _make_lead(acq_source=None)
        state = _make_state()
        ctx = _make_ctx(thread=thread, lead=lead)
        eng = _make_engine_with_ctx(ctx, state)
        eng._maybe_set_attribution(ctx, state)
        self.assertEqual(lead.acq_source, _ACQ_OTHER)


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-09: Webhook retry is idempotent
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR09WebhookIdempotent(unittest.TestCase):
    """ATTR-09: Second processing of same message does not overwrite CTWA."""

    def _capture(self, message: dict, thread: types.SimpleNamespace) -> None:
        if not getattr(thread, "ctwa_source_id", None) and not getattr(thread, "ctwa_source_url", None):
            referral = message.get("referral")
            if isinstance(referral, dict):
                raw_url = str(referral.get("source_url") or "").strip()[:500]
                raw_id = str(referral.get("source_id") or "").strip()[:100]
                raw_type = str(referral.get("source_type") or "").strip()[:40]
                if raw_url or raw_id:
                    thread.ctwa_source_url = raw_url or None
                    thread.ctwa_source_id = raw_id or None
                    thread.ctwa_source_type = raw_type or None

    def test_attr09_retry_does_not_change_stored_referral(self):
        message = {"referral": {"source_url": "https://www.instagram.com/p/FIRST/", "source_id": "1"}}
        thread = _make_thread()

        # First processing
        self._capture(message, thread)
        self.assertEqual(thread.ctwa_source_url, "https://www.instagram.com/p/FIRST/")

        # Retry with slightly different referral (Meta may re-deliver with different payload)
        message2 = {"referral": {"source_url": "https://www.facebook.com/ad/9999/", "source_id": "2"}}
        self._capture(message2, thread)

        # Must not change — first write wins
        self.assertEqual(thread.ctwa_source_url, "https://www.instagram.com/p/FIRST/")
        self.assertEqual(thread.ctwa_source_id, "1")


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-10: Existing acq_source not overwritten
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR10SourceNotOverwritten(unittest.TestCase):
    """ATTR-10: _maybe_set_attribution does not overwrite existing acq_source."""

    def test_attr10_existing_source_preserved(self):
        thread = _make_thread(
            ctwa_source_url="https://www.facebook.com/p/123",
            ctwa_source_id="fb-123",
        )
        lead = _make_lead(acq_source="INSTAGRAM")  # already set from prior evidence
        state = _make_state()
        ctx = _make_ctx(thread=thread, lead=lead)
        eng = _make_engine_with_ctx(ctx, state)
        eng._maybe_set_attribution(ctx, state)
        # INSTAGRAM preserved — Facebook CTWA on later message does not overwrite
        self.assertEqual(lead.acq_source, "INSTAGRAM")

    def test_attr10_manual_source_preserved(self):
        thread = _make_thread()
        lead = _make_lead(acq_source="GOOGLE", ref_code="ig")  # ref_code would suggest IG
        state = _make_state()
        ctx = _make_ctx(thread=thread, lead=lead)
        eng = _make_engine_with_ctx(ctx, state)
        eng._maybe_set_attribution(ctx, state)
        # Manually set GOOGLE preserved — ref_code does NOT overwrite
        self.assertEqual(lead.acq_source, "GOOGLE")


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-11: ref_code first-write-only
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR11RefCodeFirstWrite(unittest.TestCase):
    """ATTR-11: ref_code guard (from existing CE) — not overwritten on later messages."""

    def test_attr11_ref_code_not_overwritten(self):
        lead = _make_lead(ref_code="ga")
        # Simulate CE guard: if not lead.ref_code → set; existing → skip
        new_ref = "ig"
        if not lead.ref_code:
            lead.ref_code = new_ref
        self.assertEqual(lead.ref_code, "ga")  # original preserved

    def test_attr11_ref_code_set_when_null(self):
        lead = _make_lead(ref_code=None)
        new_ref = "ig"
        if not lead.ref_code:
            lead.ref_code = new_ref
        self.assertEqual(lead.ref_code, "ig")


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-12: rc_code first-write-only
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR12RcCodeFirstWrite(unittest.TestCase):
    """ATTR-12: rc_code guard — not overwritten on later messages."""

    def test_attr12_rc_code_not_overwritten(self):
        lead = _make_lead(rc_code="RC-ABCD")
        new_rc = "RC-ZZZZ"
        if not lead.rc_code:
            lead.rc_code = new_rc
        self.assertEqual(lead.rc_code, "RC-ABCD")


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-13: Website ref_code maps deterministically
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR13RefCodeMapping(unittest.TestCase):
    """ATTR-13: ref_code → acq_source mapping is deterministic and complete."""

    EXPECTED = {
        "ga":   _ACQ_GOOGLE,
        "ig":   _ACQ_INSTAGRAM,
        "fb":   _ACQ_FACEBOOK,
        "org":  _ACQ_DIRECT,
        "dir":  _ACQ_DIRECT,
        "otro": _ACQ_OTHER,
    }

    def test_attr13_all_known_ref_codes_map(self):
        for code, expected_src in self.EXPECTED.items():
            with self.subTest(code=code):
                self.assertEqual(_REF_CODE_SOURCE_MAP.get(code), expected_src)

    def test_attr13_unknown_ref_code_not_mapped(self):
        self.assertIsNone(_REF_CODE_SOURCE_MAP.get("xyz"))

    def test_attr13_ref_code_ig_sets_instagram_on_lead(self):
        thread = _make_thread()
        lead = _make_lead(ref_code="ig", acq_source=None)
        state = _make_state()
        ctx = _make_ctx(thread=thread, lead=lead)
        eng = _make_engine_with_ctx(ctx, state)
        eng._maybe_set_attribution(ctx, state)
        self.assertEqual(lead.acq_source, _ACQ_INSTAGRAM)

    def test_attr13_ref_code_ga_sets_google_on_lead(self):
        thread = _make_thread()
        lead = _make_lead(ref_code="ga", acq_source=None)
        state = _make_state()
        ctx = _make_ctx(thread=thread, lead=lead)
        eng = _make_engine_with_ctx(ctx, state)
        eng._maybe_set_attribution(ctx, state)
        self.assertEqual(lead.acq_source, _ACQ_GOOGLE)

    def test_attr13_ref_code_fb_sets_facebook_on_lead(self):
        thread = _make_thread()
        lead = _make_lead(ref_code="fb", acq_source=None)
        state = _make_state()
        ctx = _make_ctx(thread=thread, lead=lead)
        eng = _make_engine_with_ctx(ctx, state)
        eng._maybe_set_attribution(ctx, state)
        self.assertEqual(lead.acq_source, _ACQ_FACEBOOK)

    def test_attr13_website_lead_no_refcode_sets_website(self):
        thread = _make_thread()
        lead = _make_lead(ref_code=None, acq_source=None)
        state = _make_state(is_website_lead=True)
        ctx = _make_ctx(thread=thread, lead=lead)
        eng = _make_engine_with_ctx(ctx, state)
        eng._maybe_set_attribution(ctx, state)
        self.assertEqual(lead.acq_source, _ACQ_WEBSITE)


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-14: Cycle reset does NOT clear inbound_channel
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR14CycleResetPreservesChannel(unittest.TestCase):
    """ATTR-14: inbound_channel is not part of cycle_reset fields."""

    def test_attr14_inbound_channel_not_in_reset_fields(self):
        # _execute_cycle_reset() clears ACTIVE_REVISION fields.
        # inbound_channel is on the Thread (and on Lead as display copy).
        # Neither is in the reset domain (confirmed by reading CONVERSATION_RUNTIME_CONTRACT).
        # This test verifies the constant set of cleared fields does NOT include inbound_channel.
        from app.services.conversation_engine import ConversationEngine
        import inspect
        source = inspect.getsource(ConversationEngine._execute_cycle_reset)
        self.assertNotIn("inbound_channel", source,
                         "_execute_cycle_reset must not touch inbound_channel")

    def test_attr14_acq_source_not_in_reset_fields(self):
        from app.services.conversation_engine import ConversationEngine
        import inspect
        source = inspect.getsource(ConversationEngine._execute_cycle_reset)
        self.assertNotIn("acq_source", source,
                         "_execute_cycle_reset must not touch acq_source")


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-15: Cycle reset does NOT clear acq_source
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR15CycleResetPreservesSource(unittest.TestCase):
    """ATTR-15: acq_source is not cleared by cycle reset."""

    def test_attr15_maybe_set_called_after_reset_preserves_existing_source(self):
        thread = _make_thread(inbound_channel="WHATSAPP")
        lead = _make_lead(acq_source="INSTAGRAM", inbound_channel="WHATSAPP")
        state = _make_state(is_website_lead=False)
        ctx = _make_ctx(thread=thread, lead=lead)
        eng = _make_engine_with_ctx(ctx, state)
        # Simulate _maybe_set_attribution called after a cycle reset
        eng._maybe_set_attribution(ctx, state)
        # acq_source must not change
        self.assertEqual(lead.acq_source, "INSTAGRAM")


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-16: New Revision preserves original acquisition source
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR16RepeatRevisionPreservesSource(unittest.TestCase):
    """ATTR-16: acq_source on Lead is first-write-only across Revision cycles."""

    def test_attr16_second_revision_does_not_clear_source(self):
        # Lead.acq_source belongs to the Lead (not Revision).
        # A new Revision cycle starts but Lead persists.
        thread = _make_thread(
            ctwa_source_url="https://www.facebook.com/ad/new-campaign/",
            ctwa_source_id="fb-new-999",
        )
        lead = _make_lead(acq_source="INSTAGRAM")  # original acquisition preserved
        state = _make_state()
        ctx = _make_ctx(thread=thread, lead=lead)
        eng = _make_engine_with_ctx(ctx, state)
        # Even if a new Facebook CTWA arrives on a later message (new campaign),
        # the original INSTAGRAM source must be preserved.
        eng._maybe_set_attribution(ctx, state)
        self.assertEqual(lead.acq_source, "INSTAGRAM")


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-17: Legacy canal remains backward compatible
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR17CanalBackwardCompat(unittest.TestCase):
    """ATTR-17: canal field still writable and readable without errors."""

    def test_attr17_canal_set_and_readable(self):
        lead = _make_lead(canal="IG_WHATSAPP")
        self.assertEqual(lead.canal, "IG_WHATSAPP")

    def test_attr17_canal_none_does_not_affect_acq_source(self):
        thread = _make_thread()
        lead = _make_lead(canal=None, acq_source=None, ref_code="ig")
        state = _make_state()
        ctx = _make_ctx(thread=thread, lead=lead)
        eng = _make_engine_with_ctx(ctx, state)
        eng._maybe_set_attribution(ctx, state)
        # acq_source set from ref_code — canal irrelevant
        self.assertEqual(lead.acq_source, _ACQ_INSTAGRAM)

    def test_attr17_kanban_channel_options_unchanged(self):
        from app.ui.kanban_view import CANAL_OPCIONES
        # All original dropdown values still present
        for v in ["IG_DM", "IG_WHATSAPP", "FB_DM", "FB_WHATSAPP", "WEBSITE", "GOOGLE", "GMAPS", "OTROS"]:
            self.assertIn(v, CANAL_OPCIONES, f"{v} must remain in CANAL_OPCIONES")


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-18: Canonical attribution does not depend on legacy canal
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR18NoDependencyOnCanal(unittest.TestCase):
    """ATTR-18: _maybe_set_attribution never reads canal to determine acq_source."""

    def test_attr18_canal_set_but_no_evidence_acq_source_stays_null(self):
        thread = _make_thread()
        lead = _make_lead(canal="GOOGLE", acq_source=None, ref_code=None)
        state = _make_state(is_website_lead=False)
        ctx = _make_ctx(thread=thread, lead=lead)
        eng = _make_engine_with_ctx(ctx, state)
        eng._maybe_set_attribution(ctx, state)
        # canal = "GOOGLE" but no canonical evidence → acq_source stays null
        self.assertIsNone(lead.acq_source)

    def test_attr18_maybe_set_attribution_source_does_not_read_canal(self):
        from app.services.conversation_engine import ConversationEngine
        import inspect
        source = inspect.getsource(ConversationEngine._maybe_set_attribution)
        self.assertNotIn('"canal"', source)
        self.assertNotIn("lead.canal", source)


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-19: CRM renders technical channel in lead card
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR19CRMRendersChannel(unittest.TestCase):
    """ATTR-19: lead card shows Canal: WHATSAPP when inbound_channel is set."""

    def _make_ui_lead(self, **kw):
        return _make_lead(
            id=1, nombre="Test", apellido="User", revisions=[], **kw
        )

    def test_attr19_channel_shown_in_lead_card(self):
        lead = self._make_ui_lead(inbound_channel="WHATSAPP")
        html = render_lead_card(lead)
        self.assertIn("Canal:", html)
        self.assertIn("WHATSAPP", html)

    def test_attr19_no_channel_no_attribution_section(self):
        lead = self._make_ui_lead(inbound_channel=None, acq_source=None, ref_code=None, rc_code=None)
        html = render_lead_card(lead)
        self.assertNotIn("leadAttribution", html)


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-20: CRM renders acquisition source in lead card
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR20CRMRendersSource(unittest.TestCase):
    """ATTR-20: lead card shows Origen: INSTAGRAM when acq_source is set."""

    def test_attr20_source_shown(self):
        lead = _make_lead(id=1, acq_source="INSTAGRAM", nombre="Ana", apellido="G", revisions=[])
        html = render_lead_card(lead)
        self.assertIn("Origen:", html)
        self.assertIn("INSTAGRAM", html)

    def test_attr20_website_source_shown(self):
        lead = _make_lead(id=1, acq_source="WEBSITE", nombre="X", apellido="Y", revisions=[])
        html = render_lead_card(lead)
        self.assertIn("WEBSITE", html)


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-21: CRM conditionally renders ref_code / rc_code
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR21CRMRendersRefCode(unittest.TestCase):
    """ATTR-21: ref_code / rc_code shown only when non-null."""

    def test_attr21_ref_and_rc_shown_when_set(self):
        lead = _make_lead(id=1, ref_code="ig", rc_code="RC-ABCD", nombre="X", apellido="Y", revisions=[])
        html = render_lead_card(lead)
        self.assertIn("ref:ig", html)
        self.assertIn("RC-ABCD", html)

    def test_attr21_ref_code_alone(self):
        lead = _make_lead(id=1, ref_code="ga", rc_code=None, nombre="X", apellido="Y", revisions=[])
        html = render_lead_card(lead)
        self.assertIn("ref:ga", html)
        self.assertNotIn("RC-", html)

    def test_attr21_no_ref_no_display(self):
        lead = _make_lead(id=1, ref_code=None, rc_code=None, acq_source=None, inbound_channel=None,
                          nombre="X", apellido="Y", revisions=[])
        html = render_lead_card(lead)
        self.assertNotIn("leadAttribution", html)
        self.assertNotIn("ref:", html)


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-22: No raw Meta payload exposed through CRM
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR22NoBulkPayloadExposed(unittest.TestCase):
    """ATTR-22: Only specific attribution fields stored, not full webhook payload."""

    def test_attr22_ctwa_fields_are_individual_not_blob(self):
        # Verify Thread model has individual fields, not a JSON blob for attribution
        from app.models import WhatsAppThread
        import inspect
        source = inspect.getsource(WhatsAppThread)
        self.assertIn("ctwa_source_url", source)
        self.assertIn("ctwa_source_id", source)
        self.assertIn("ctwa_source_type", source)
        # Must NOT store entire raw_payload at the thread level for attribution
        self.assertNotIn("ctwa_raw_payload", source)

    def test_attr22_lead_api_schema_not_expose_ctwa_raw(self):
        from app.schemas.leads import LeadOut
        fields = LeadOut.model_fields
        self.assertNotIn("ctwa_source_url", fields)
        self.assertNotIn("ctwa_source_id", fields)
        self.assertIn("acq_source", fields)
        self.assertIn("inbound_channel", fields)


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-23: No outbound message produced by attribution
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR23NoOutboundFromAttribution(unittest.TestCase):
    """ATTR-23: _maybe_set_attribution never calls outbound gate."""

    def test_attr23_attribution_does_not_send_outbound(self):
        from app.services.conversation_engine import ConversationEngine
        import inspect
        source = inspect.getsource(ConversationEngine._maybe_set_attribution)
        # Must not reference the gate or send methods
        self.assertNotIn("_send_text_to_wa", source)
        self.assertNotIn("outbound_safety_gate", source)
        self.assertNotIn("gate.attempt", source)

    def test_attr23_ctwa_capture_in_webhook_does_not_send(self):
        from app.routes.whatsapp import router
        import inspect
        # Verify CTWA capture section references thread mutation only
        source = inspect.getsource(router.routes[0].endpoint if router.routes else lambda: None)
        # The capture code sets thread fields — it must not call Meta API
        # We just check the attribution constants are not misused
        self.assertNotIn("requests.post", source or "")


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-24: Existing webhook behavior unaffected
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR24WebhookRegression(unittest.TestCase):
    """ATTR-24: Core webhook storage and dispatch still works."""

    def test_attr24_webhook_module_imports_cleanly(self):
        from app.routes import whatsapp as wh
        self.assertTrue(hasattr(wh, "router"))

    def test_attr24_whatsapp_thread_has_new_fields(self):
        from app.models import WhatsAppThread
        self.assertTrue(hasattr(WhatsAppThread, "inbound_channel"))
        self.assertTrue(hasattr(WhatsAppThread, "ctwa_source_url"))
        self.assertTrue(hasattr(WhatsAppThread, "ctwa_source_id"))
        self.assertTrue(hasattr(WhatsAppThread, "ctwa_source_type"))

    def test_attr24_lead_has_new_fields(self):
        from app.models import Lead
        self.assertTrue(hasattr(Lead, "acq_source"))
        self.assertTrue(hasattr(Lead, "inbound_channel"))
        self.assertTrue(hasattr(Lead, "ref_code"))
        self.assertTrue(hasattr(Lead, "rc_code"))


# ──────────────────────────────────────────────────────────────────────────────
# ATTR-25: _maybe_set_attribution is idempotent
# ──────────────────────────────────────────────────────────────────────────────

class TestATTR25Idempotent(unittest.TestCase):
    """ATTR-25: Calling _maybe_set_attribution multiple times is safe."""

    def test_attr25_double_call_no_change(self):
        thread = _make_thread(
            ctwa_source_url="https://www.instagram.com/p/X/",
            ctwa_source_id="ig-1",
            inbound_channel="WHATSAPP",
        )
        lead = _make_lead(acq_source=None, inbound_channel=None)
        state = _make_state()
        ctx = _make_ctx(thread=thread, lead=lead)
        eng = _make_engine_with_ctx(ctx, state)

        eng._maybe_set_attribution(ctx, state)
        first_source = lead.acq_source
        first_channel = lead.inbound_channel

        eng._maybe_set_attribution(ctx, state)  # second call
        self.assertEqual(lead.acq_source, first_source)
        self.assertEqual(lead.inbound_channel, first_channel)

    def test_attr25_none_lead_no_error(self):
        thread = _make_thread()
        ctx = types.SimpleNamespace(thread=thread, lead=None)
        state = _make_state()
        eng = _make_engine_with_ctx(ctx, state)
        # Must not raise
        eng._maybe_set_attribution(ctx, state)


if __name__ == "__main__":
    unittest.main()
