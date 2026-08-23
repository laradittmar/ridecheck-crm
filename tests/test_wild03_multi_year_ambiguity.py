"""WILD-03 — Multi-year numeric model ambiguity regression tests.

Guard: when 2+ distinct year-shaped tokens appear in a burst with no explicit
make/model, _contextual_numeric_model_lookup returns None so CE falls through
to the normal qualification path instead of sending a Peugeot 2008 clarification.

Test classes
────────────
TestW03YPureFunctions   – _contextual_numeric_model_lookup in isolation
TestW03YVehicleResolution – explicit-model cases still resolve (Y04-Y06)
TestW03YCEIntegration   – CE-level integration (Y01, Y02, Y07, Y09)
"""

from __future__ import annotations

import json
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# ── shared helpers from WILD-02 suite ────────────────────────────────────────

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.services.vehicle_catalog import (
    lookup_vehicle,
    _contextual_numeric_model_lookup,
)
from app.services.conversation_engine import (
    ConversationEngine,
    _AWAITING_QUALIFICATION,
    _INTENT_PREPURCHASE,
    _extract_year_from_text,
    _VEHICLE_YEAR_RE,
    _FUZZY_CONFIRMATION_TEMPLATE,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

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
        last_processed_inbound_wa_message_id=None,
        pending_fuzzy_catalog_key=None,
        pending_turn_evidence_text=None,
        unanswered_alert_sent_at=None,
        quote_followup_sent_at=None,
        buscando_followup_sent_at=None,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _make_ctx(state, candidates=None):
    ctx = SimpleNamespace()
    ctx.thread = SimpleNamespace(id=99)
    ctx.contact = SimpleNamespace(wa_id="5491199990000")
    ctx.lead = SimpleNamespace(
        id=1, flag="PRESUPUESTANDO", estado="CONSULTA_NUEVA",
        nombre="Test", telefono=None, necesita_humano=False,
    )
    ctx.state = state
    ctx.candidates = list(candidates or [])
    ctx.db_messages = []
    return ctx


def _make_event(text, recent=None, unanswered=None):
    from app.schemas.conversation import ConversationHandleIn
    msgs = unanswered or [text]
    return ConversationHandleIn(
        thread_id=99,
        wa_message_id=f"msg-{hash(text) % 100000}",
        wa_id="5491199990000",
        text=text,
        recent_user_messages=recent or msgs,
        unanswered_recent_user_messages=msgs,
    )


def _make_engine():
    """CE with real vehicle lookup + _handle_qualifying_intent.
    Mocked: outbound, AI, pricing, zone extraction.
    """
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock()
    eng.settings = MagicMock()
    eng.settings.openai_api_key = "sk-fake"
    eng.settings.openai_chat_model = "gpt-4o-mini"
    eng.settings.backend_url = "http://localhost:8000"
    eng.settings.whatsapp_location_fallback_flow_id = ""
    eng.settings.whatsapp_vehicle_fallback_flow_id = ""
    eng.settings.whatsapp_flow_id = ""

    eng._send_text_to_wa = MagicMock(return_value="mock-wa-id")
    eng._send_fallback_human_review_notification = MagicMock()

    eng._call_openai = MagicMock(return_value=json.dumps({
        "intent": "QUALIFYING", "reply": "¿Qué vehículo querés revisar?",
        "deferred_interest": False,
        "candidate": {"action": "none"}, "extracted": {}, "lead_flag": None,
        "needs_human": False,
    }))
    eng._build_ai_messages = MagicMock(return_value=[])
    eng._compute_price_quote = MagicMock(return_value=None)
    eng._pricing = MagicMock()
    eng._scrub_invented_price = MagicMock(side_effect=lambda r, q: r)
    eng._extract_zone_from_text = MagicMock(return_value=None)
    eng._normalize_zone_from_db = MagicMock()
    eng._try_schedule_and_flow = MagicMock(return_value=None)
    eng._handle_day_only_request = MagicMock(return_value=None)
    eng._build_quote_reply = MagicMock(return_value="Cotización: $999.")
    eng._apply_extracted = MagicMock()
    eng._apply_candidate = MagicMock()
    eng._apply_narrative_interpretation = MagicMock()
    return eng


def _make_full_engine():
    eng = _make_engine()
    eng._routing_gate = MagicMock(return_value=(None, True))
    eng._check_fallback_flow_triggers = MagicMock(return_value=None)
    eng._focus_candidate = MagicMock(return_value=None)
    eng._enforce_catalog_vehicle = MagicMock()
    eng._handle_period_request = MagicMock(return_value=None)
    return eng


def _sent_texts(eng):
    return [c[0][1] for c in eng._send_text_to_wa.call_args_list]


# ─────────────────────────────────────────────────────────────────────────────
# TestW03YPureFunctions — _contextual_numeric_model_lookup in isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestW03YPureFunctions(unittest.TestCase):
    """W03-Y01 through W03-Y03, Y07, Y08 at the pure-function level."""

    def test_y01_two_year_tokens_returns_none(self):
        """W03-Y01a: '2008 o 2014' → None (WILD-03 root case)."""
        result = _contextual_numeric_model_lookup(
            "Revisar una 2008 o 2014 en Balvanera. ¿Cuánto me sale?"
        )
        self.assertIsNone(result, "Two year tokens must return None")

    def test_y02_two_year_tokens_short_returns_none(self):
        """W03-Y02a: 'un 2008 o 2014 en Balvanera' → None."""
        result = _contextual_numeric_model_lookup("un 2008 o 2014 en Balvanera")
        self.assertIsNone(result)

    def test_y03_bare_two_year_tokens_returns_none(self):
        """W03-Y03: '2008 y 2014' → None."""
        result = _contextual_numeric_model_lookup("2008 y 2014")
        self.assertIsNone(result)

    def test_y03_entre_variant(self):
        """'entre 2008 y 2014' → None."""
        result = _contextual_numeric_model_lookup("entre 2008 y 2014")
        self.assertIsNone(result)

    def test_y03_three_tokens(self):
        """Three year tokens → None."""
        result = _contextual_numeric_model_lookup("2006 o 2008 o 2014")
        self.assertIsNone(result)

    def test_y07_single_token_returns_match(self):
        """W03-Y07a: 'un 2008 en Balvanera' single token → Peugeot 2008 (WILD-02 preserved)."""
        result = _contextual_numeric_model_lookup("un 2008 en Balvanera")
        self.assertIsNotNone(result)
        self.assertEqual(result.marca, "Peugeot")
        self.assertEqual(str(result.modelo), "2008")

    def test_y07_berazategui_variant(self):
        """'Si, un 2008 en berazategui' → Peugeot 2008 (WILD-02 preserved)."""
        result = _contextual_numeric_model_lookup("Si, un 2008 en berazategui")
        self.assertIsNotNone(result)
        self.assertEqual(result.marca, "Peugeot")

    def test_y08_generic_vehicle_word_blocks_match(self):
        """W03-Y08a: generic vehicle word 'auto' → None (existing guard preserved)."""
        result = _contextual_numeric_model_lookup(
            "Quiero revisar un auto 2008 en Balvanera"
        )
        self.assertIsNone(result)

    def test_y08_coche_blocks_match(self):
        """'coche 2008' → None."""
        result = _contextual_numeric_model_lookup("coche 2008")
        self.assertIsNone(result)

    def test_empty_text_returns_none(self):
        """Empty text → None."""
        self.assertIsNone(_contextual_numeric_model_lookup(""))

    def test_no_year_token_returns_none(self):
        """Text with no year-shaped token → None."""
        self.assertIsNone(_contextual_numeric_model_lookup("Revisar un Ford Focus en CABA"))


# ─────────────────────────────────────────────────────────────────────────────
# TestW03YVehicleResolution — explicit make/model cases still work
# ─────────────────────────────────────────────────────────────────────────────

class TestW03YVehicleResolution(unittest.TestCase):
    """W03-Y04, Y05, Y06 — explicit model resolution is unaffected by the guard."""

    def test_y04_peugeot_2008_explicit_with_year(self):
        """W03-Y04: 'Peugeot 2008 2014' → lookup_vehicle=Peugeot 2008; year=2014."""
        text = "Peugeot 2008 2014"
        vehicle = lookup_vehicle(text)
        self.assertIsNotNone(vehicle)
        self.assertEqual(vehicle.marca, "Peugeot")
        self.assertEqual(str(vehicle.modelo), "2008")
        # Year extraction with model excluded
        year = _extract_year_from_text(text, exclude_token=str(vehicle.modelo))
        self.assertEqual(year, 2014)

    def test_y05_peugeot_2008_del_2014(self):
        """W03-Y05: 'Peugeot 2008 del 2014' → Peugeot 2008; year=2014."""
        text = "Peugeot 2008 del 2014"
        vehicle = lookup_vehicle(text)
        self.assertIsNotNone(vehicle)
        self.assertEqual(vehicle.marca, "Peugeot")
        year = _extract_year_from_text(text, exclude_token=str(vehicle.modelo))
        self.assertEqual(year, 2014)

    def test_y06_focus_2008(self):
        """W03-Y06: 'Focus 2008' → Ford Focus; year=2008."""
        text = "Focus 2008"
        vehicle = lookup_vehicle(text)
        self.assertIsNotNone(vehicle)
        self.assertEqual(vehicle.marca, "Ford")
        self.assertIn("Focus", str(vehicle.modelo))
        year = _extract_year_from_text(text)
        self.assertEqual(year, 2008)

    def test_y04_contextual_lookup_not_called_for_explicit(self):
        """Explicit 'Peugeot 2008 2014' — lookup_vehicle wins; contextual guard irrelevant."""
        # lookup_vehicle finds Peugeot 2008 → pre_detected_vehicle is set
        # → WILD-02-B block skips; multi-year guard in contextual lookup irrelevant.
        vehicle = lookup_vehicle("Peugeot 2008 2014")
        self.assertIsNotNone(vehicle, "Explicit Peugeot 2008 must resolve via lookup_vehicle")

    def test_year_sync_guard_not_block_resolved_model(self):
        """Phase 3 guard allows year sync when modelo is known ('Peugeot', '2008')."""
        # With modelo='2008' (not None), guard condition: modelo is not None → sync runs.
        # Verify _extract_year_from_text with exclude_token='2008' finds 2014.
        text = "Quiero revisar un Peugeot 2008 del 2014 en Palermo"
        tokens = {m.group(1) for m in _VEHICLE_YEAR_RE.finditer(text)}
        self.assertEqual(len(tokens), 2)  # '2008' and '2014' both present
        year = _extract_year_from_text(text, exclude_token="2008")
        self.assertEqual(year, 2014, "exclude_token guard must skip '2008', return 2014")


# ─────────────────────────────────────────────────────────────────────────────
# TestW03YCEIntegration — CE-level intent/zone/vehicle state
# ─────────────────────────────────────────────────────────────────────────────

class TestW03YCEIntegration(unittest.TestCase):
    """Y01, Y02, Y07, Y09 at the CE level via _process_text (mocked outbound + AI)."""

    def setUp(self):
        self.eng = _make_full_engine()

    def _run(self, text, state):
        ctx = _make_ctx(state)
        event = _make_event(text)
        self.eng._process_text(ctx, event)
        return ctx

    # ── W03-Y01 ──────────────────────────────────────────────────────────────

    def test_y01_multi_year_burst_no_p2008_question(self):
        """W03-Y01: 'Revisar una 2008 o 2014 en Balvanera…' → no P2008 clarification."""
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        self._run("Revisar una 2008 o 2014 en Balvanera. ¿Cuánto me sale?", state)
        p2008_q = _FUZZY_CONFIRMATION_TEMPLATE.format(marca="Peugeot", modelo="2008")
        self.assertNotIn(p2008_q, _sent_texts(self.eng),
                         "Must NOT send Peugeot 2008 clarification for multi-year input")

    def test_y01_no_candidate_created(self):
        """W03-Y01: no vehicle candidate from multi-year burst."""
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        ctx = self._run("Revisar una 2008 o 2014 en Balvanera. ¿Cuánto me sale?", state)
        self.assertEqual(len(ctx.candidates), 0, "No candidate must be created")

    def test_y01_intent_not_set_by_wild02b(self):
        """W03-Y01: multi-year guard blocks WILD-02-B; intent is NOT set by the
        Peugeot 2008 branch (was wrongly set to PREPURCHASE before sending P2008 q).
        CE handles the turn via the normal UNCERTAIN path instead."""
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        self._run("Revisar una 2008 o 2014 en Balvanera. ¿Cuánto me sale?", state)
        # WILD-02-B must NOT have set pending_fuzzy_catalog_key (its side-effect
        # on intent runs just before storing fuzzy key); either no change or
        # Layer F handled it — pending key must be None regardless.
        self.assertIsNone(state.pending_fuzzy_catalog_key,
                          "WILD-02-B must not have committed a fuzzy lookup")

    def test_y01_no_pending_fuzzy_key(self):
        """W03-Y01: pending_fuzzy_catalog_key must stay None."""
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        self._run("Revisar una 2008 o 2014 en Balvanera. ¿Cuánto me sale?", state)
        self.assertIsNone(state.pending_fuzzy_catalog_key)
        self.assertIsNone(state.pending_turn_evidence_text)

    # ── W03-Y02 ──────────────────────────────────────────────────────────────

    def test_y02_short_form_no_p2008_question(self):
        """W03-Y02: 'un 2008 o 2014 en Balvanera' → no P2008 clarification."""
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        ctx = self._run("un 2008 o 2014 en Balvanera", state)
        p2008_q = _FUZZY_CONFIRMATION_TEMPLATE.format(marca="Peugeot", modelo="2008")
        self.assertNotIn(p2008_q, _sent_texts(self.eng))
        self.assertEqual(len(ctx.candidates), 0)
        self.assertIsNone(state.pending_fuzzy_catalog_key)

    # ── W03-Y07 — WILD-02 preserved ──────────────────────────────────────────

    def test_y07_single_token_still_asks_p2008(self):
        """W03-Y07: 'un 2008 en Balvanera' single token → P2008 clarification (WILD-02 preserved)."""
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        self._run("un 2008 en Balvanera", state)
        p2008_q = _FUZZY_CONFIRMATION_TEMPLATE.format(marca="Peugeot", modelo="2008")
        self.assertIn(p2008_q, _sent_texts(self.eng),
                      "Single-token '2008' must still trigger P2008 clarification")
        self.assertEqual(state.pending_fuzzy_catalog_key, "Peugeot||2008")

    def test_y07_location_preserved_in_pending_evidence(self):
        """W03-Y07: 'un 2008 en Balvanera' → pending_turn_evidence_text preserved."""
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        self._run("un 2008 en Balvanera", state)
        self.assertIsNotNone(state.pending_turn_evidence_text)
        self.assertIn("Balvanera", state.pending_turn_evidence_text)

    # ── W03-Y09 — burst test ─────────────────────────────────────────────────

    def test_y09_burst_three_messages_no_p2008(self):
        """W03-Y09: aggregated burst → no P2008 clarification, no P2008 fuzzy key.
        Note: 'quiero revisar algo' intentionally avoids catalog-matching words.
        Real burst 'Quiero revisar uno' would match Fiat Uno (catalog) — a separate concern."""
        burst = "Quiero revisar algo\n2008 o 2014\nEstá en Balvanera"
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        self._run(burst, state)
        p2008_q = _FUZZY_CONFIRMATION_TEMPLATE.format(marca="Peugeot", modelo="2008")
        self.assertNotIn(p2008_q, _sent_texts(self.eng))
        self.assertIsNone(state.pending_fuzzy_catalog_key)

    def test_y09_burst_intent_becomes_prepurchase(self):
        """W03-Y09: 'quiero revisar' prefix → Layer F Step 1 sets PREPURCHASE."""
        burst = "Quiero revisar algo\n2008 o 2014\nEstá en Balvanera"
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        self._run(burst, state)
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)


# ─────────────────────────────────────────────────────────────────────────────
# TestW03YPhase3YearSync — defensive year-sync guard
# ─────────────────────────────────────────────────────────────────────────────

class TestW03YPhase3YearSync(unittest.TestCase):
    """Phase 3: multi-year token guard prevents arbitrary year commit.

    Tests _VEHICLE_YEAR_RE token counting and _extract_year_from_text
    at the unit level to verify the guard logic without needing a full
    modelless-candidate AI scenario.
    """

    def test_two_year_tokens_detected(self):
        """Both '2008' and '2014' detected as year tokens."""
        text = "Revisar una 2008 o 2014 en Balvanera"
        tokens = {m.group(1) for m in _VEHICLE_YEAR_RE.finditer(text)}
        self.assertEqual(len(tokens), 2)
        self.assertIn("2008", tokens)
        self.assertIn("2014", tokens)

    def test_single_token_not_blocked(self):
        """Single year token → guard condition not triggered (len <= 1)."""
        text = "un 2008 en Balvanera"
        tokens = {m.group(1) for m in _VEHICLE_YEAR_RE.finditer(text)}
        self.assertEqual(len(tokens), 1)

    def test_resolved_model_bypasses_guard(self):
        """When modelo is resolved ('2008'), guard allows sync even with 2 tokens."""
        # Condition: focus_after.modelo is not None → sync runs
        text = "Peugeot 2008 del 2014"
        tokens = {m.group(1) for m in _VEHICLE_YEAR_RE.finditer(text)}
        self.assertEqual(len(tokens), 2)
        # With modelo known, year sync extracts 2014 (exclude '2008')
        year = _extract_year_from_text(text, exclude_token="2008")
        self.assertEqual(year, 2014)

    def test_unresolved_model_with_two_tokens_first_is_ambiguous(self):
        """With no modelo (None), first year token would be '2008' — this is the
        year that would be wrongly committed without the Phase 3 guard."""
        text = "Revisar una 2008 o 2014 en Balvanera"
        # Simulates what year sync would do without guard (exclude_token = str(None) = 'None')
        wrong_year = _extract_year_from_text(text, exclude_token="None")
        self.assertEqual(wrong_year, 2008, "Without guard, '2008' would be committed")
        tokens = {m.group(1) for m in _VEHICLE_YEAR_RE.finditer(text)}
        # Guard fires: len(tokens) > 1 AND modelo is None → skip sync
        self.assertGreater(len(tokens), 1, "Guard would fire for this input")


if __name__ == "__main__":
    unittest.main()
