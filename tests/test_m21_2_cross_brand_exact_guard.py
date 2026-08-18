"""M21.2 regression: brand-contradiction guard in lookup_vehicle (exact lookup).

Root cause: lookup_vehicle() is alias-first with no make constraint. A model-only
alias ("ranger", "civic", "hilux") matched anywhere in the text regardless of
whether an explicit different brand was also present.  "honda ranger" → Ford
Ranger via alias "ranger" → CE created Ford Ranger candidate.

Fix: _detect_make() runs at the start of lookup_vehicle(); if exactly one brand is
present in the text and it differs from the matched alias brand, the alias is
skipped (brand-contradiction guard).  Model-only inputs ("ranger") are unaffected
because make_constrained=False when no brand is present.

SC18 in test_m21_1_4_asr_vehicle_normalization.py only tested fuzzy_lookup_vehicle;
it never exercised the real CE path with the real lookup_vehicle.  These tests
close that gap with permanent integration regression cases.
"""
from __future__ import annotations

import json
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT_DIR = __import__("pathlib").Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

for _mod_name in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = sqlalchemy.JSON   # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON      # type: ignore[attr-defined]

from app.services.vehicle_catalog import lookup_vehicle   # noqa: E402
from app.services.conversation_engine import ConversationEngine  # noqa: E402
from app.schemas.conversation import ConversationHandleIn         # noqa: E402


# ── Unit: lookup_vehicle brand-contradiction guard ────────────────────────────

class TestLookupVehicleBrandContradictionGuard(unittest.TestCase):
    """lookup_vehicle must return None when text explicitly names a different brand."""

    def _lookup(self, text):
        return lookup_vehicle(text)

    # --- Cross-brand contradiction cases (must return None) ---

    def test_honda_ranger_returns_none(self):
        """'honda ranger' must not resolve to Ford Ranger."""
        self.assertIsNone(self._lookup("honda ranger"))

    def test_toyota_ranger_returns_none(self):
        """'toyota ranger' must not resolve to Ford Ranger."""
        self.assertIsNone(self._lookup("toyota ranger"))

    def test_chevrolet_ranger_returns_none(self):
        """'chevrolet ranger' must not resolve to Ford Ranger."""
        self.assertIsNone(self._lookup("chevrolet ranger"))

    def test_ford_civic_returns_none(self):
        """'ford civic' must not resolve to Honda Civic."""
        self.assertIsNone(self._lookup("ford civic"))

    def test_toyota_civic_returns_none(self):
        """'toyota civic' must not resolve to Honda Civic."""
        self.assertIsNone(self._lookup("toyota civic"))

    def test_honda_hilux_returns_none(self):
        """'honda hilux' must not resolve to Toyota Hilux."""
        self.assertIsNone(self._lookup("honda hilux"))

    # --- Correct (no contradiction) cases must still resolve ---

    def test_ford_ranger_resolves(self):
        r = self._lookup("ford ranger")
        self.assertIsNotNone(r)
        self.assertEqual(r.marca, "Ford")
        self.assertEqual(r.modelo, "Ranger")

    def test_ranger_no_brand_resolves(self):
        """Model-only input ('ranger') has no brand → no constraint → Ford Ranger."""
        r = self._lookup("ranger")
        self.assertIsNotNone(r)
        self.assertEqual(r.marca, "Ford")
        self.assertEqual(r.modelo, "Ranger")

    def test_honda_fit_resolves(self):
        r = self._lookup("honda fit")
        self.assertIsNotNone(r)
        self.assertEqual(r.marca, "Honda")
        self.assertEqual(r.modelo, "Fit")

    def test_ford_ka_resolves(self):
        r = self._lookup("ford ka")
        self.assertIsNotNone(r)
        self.assertEqual(r.marca, "Ford")
        self.assertEqual(r.modelo, "Ka")

    def test_toyota_corolla_resolves(self):
        r = self._lookup("toyota corolla")
        self.assertIsNotNone(r)
        self.assertEqual(r.marca, "Toyota")
        self.assertEqual(r.modelo, "Corolla")

    def test_civic_no_brand_resolves(self):
        """'civic' alone → make_constrained=False → Honda Civic returned."""
        r = self._lookup("civic")
        self.assertIsNotNone(r)
        self.assertEqual(r.marca, "Honda")
        self.assertEqual(r.modelo, "Civic")

    def test_hilux_no_brand_resolves(self):
        """'hilux' alone → make_constrained=False → Toyota Hilux returned."""
        r = self._lookup("hilux")
        self.assertIsNotNone(r)
        self.assertEqual(r.marca, "Toyota")


# ── CE integration: real lookup_vehicle, 'honda ranger' must not persist Ford ─

def _make_engine():
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock()
    eng.settings = MagicMock()
    eng.settings.openai_api_key = "sk-fake"
    eng.settings.openai_chat_model = "gpt-4o-mini"
    eng.settings.backend_url = "http://localhost:8000"
    eng._send_text_to_wa = MagicMock(return_value="mock-wa-id")
    eng._send_fallback_human_review_notification = MagicMock()
    eng._call_openai = MagicMock(return_value=json.dumps({
        "intent": "QUALIFYING", "reply": "Test reply.", "deferred_interest": False,
        "candidate": {"action": "none"}, "extracted": {}, "lead_flag": None,
        "needs_human": False,
    }))
    eng._build_ai_messages = MagicMock(return_value=[])
    eng._compute_price_quote = MagicMock(return_value=None)
    eng._extract_zone_from_text = MagicMock(return_value=None)
    eng._normalize_zone_from_db = MagicMock()
    eng._routing_gate = MagicMock(return_value=(None, True))
    eng._check_fallback_flow_triggers = MagicMock(return_value=None)
    eng._apply_extracted = MagicMock()
    eng._focus_candidate = MagicMock(return_value=None)
    eng._apply_candidate = MagicMock()
    eng._enforce_catalog_vehicle = MagicMock()
    eng._create_candidate_from_catalog = MagicMock()
    eng._try_schedule_and_flow = MagicMock(return_value=None)
    eng._handle_day_only_request = MagicMock(return_value=None)
    eng._build_quote_reply = MagicMock(return_value="Cotización: $999.")
    eng._pricing = MagicMock()
    eng._scrub_invented_price = MagicMock(side_effect=lambda r, q: r)
    return eng


def _make_state(**kw):
    ns = SimpleNamespace(
        last_stage="QUALIFYING", needs_human=False, last_intent=None,
        home_zone_group=None, home_zone_detail=None, home_address=None,
        distance_km=None, current_focus_candidate_id=None, preferred_day=None,
        preferred_time=None, active_requested_date=None, last_requested_time=None,
        last_offered_slots=None, last_visible_slots=None, is_website_lead=False,
        flow_booking_token=None, current_revision_id=None, customer_name=None,
        vehicle_clarification_sent=False, location_clarification_sent=False,
        vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False,
        inspectability_clarification_sent=False,
        last_processed_inbound_wa_message_id=None, pending_fuzzy_catalog_key=None,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _make_ctx(state):
    ctx = SimpleNamespace()
    ctx.thread = SimpleNamespace(id=99)
    ctx.contact = SimpleNamespace(wa_id="5491199990000")
    ctx.lead = SimpleNamespace(
        id=1, flag="PRESUPUESTANDO", estado="CONSULTA_NUEVA",
        nombre="Test", telefono=None, necesita_humano=False,
    )
    ctx.state = state
    ctx.candidates = []
    return ctx


def _run_ce_real_lookup(text):
    """Run CE._process_text with real lookup_vehicle (not mocked)."""
    eng = _make_engine()
    state = _make_state()
    ctx = _make_ctx(state)
    event = ConversationHandleIn(
        thread_id=99, wa_message_id="test-cross-brand",
        wa_id="5491199990000", text=text,
        unanswered_recent_user_messages=[text],
        recent_user_messages=[text],
    )
    result = eng._process_text(ctx, event)
    return eng, result


class TestCECrossBrandIntegration(unittest.TestCase):
    """Real CE path must never persist a Ford candidate for 'honda ranger'."""

    def test_honda_ranger_no_candidate_created(self):
        """Real lookup_vehicle must not fire for 'honda ranger'; no candidate persisted."""
        eng, result = _run_ce_real_lookup("honda ranger")
        eng._create_candidate_from_catalog.assert_not_called()

    def test_honda_ranger_no_ford_candidate(self):
        """Even if a candidate is somehow created, it must not be Ford Ranger."""
        eng, result = _run_ce_real_lookup("honda ranger")
        for call in eng._create_candidate_from_catalog.call_args_list:
            args = call[0]
            if len(args) >= 3 and hasattr(args[2], "marca"):
                self.assertNotEqual(args[2].marca, "Ford",
                                    "Ford Ranger created for 'honda ranger' — DEFECT")

    def test_ford_ranger_still_creates_candidate(self):
        """Control: 'ford ranger' (no contradiction) must still create a candidate."""
        eng, result = _run_ce_real_lookup("ford ranger")
        eng._create_candidate_from_catalog.assert_called()
        args = eng._create_candidate_from_catalog.call_args[0]
        if len(args) >= 3 and hasattr(args[2], "marca"):
            self.assertEqual(args[2].marca, "Ford")
            self.assertEqual(args[2].modelo, "Ranger")


if __name__ == "__main__":
    unittest.main()
