"""WILD-03-X — Cross-turn year ambiguity preservation tests.

Root problem: n8n's 'Build Conversation Context' node assembles
recent_user_messages = last 6 inbound messages within the 60-minute window.
Prior-turn text is therefore present in all_recent_text on Turn 2.

Before this fix, Phase 3 year-sync guard only blocked when modelo was None.
Once Ford Ka was detected (Turn 2 "Es un Ford Ka"), modelo="Ka" → guard bypassed
→ year=2008 silently committed even though "2008 o 2014" was still ambiguous.

New WILD-03-X guard uses a current-turn-first strategy:
  Priority 1: current_turn_text year tokens (highest confidence).
    exactly 1 effective year → commit from current turn
    2+ effective years      → current turn is ambiguous → no sync
  Priority 2: all_recent_text year tokens (history fallback).
    Only reached when current turn has 0 year tokens.
    ≤1 effective year in history → commit from history
    2+ effective years in history → prior turns were ambiguous → no sync

Test classes
────────────
TestW03XGuardUnit      – token math verified analytically (no CE call)
TestW03XCEIntegration  – _process_text with realistic n8n-style payloads
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.services.conversation_engine import (
    ConversationEngine,
    _AWAITING_QUALIFICATION,
    _INTENT_PREPURCHASE,
    _extract_year_from_text,
    _VEHICLE_YEAR_RE,
)


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
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
        wa_message_id=f"msg-{abs(hash(text)) % 100000}",
        wa_id="5491199990000",
        text=text,
        recent_user_messages=recent or msgs,
        unanswered_recent_user_messages=msgs,
    )


def _make_candidate(modelo=None, anio=None, tipo_vehiculo="AUTO", **kw):
    """SimpleNamespace mimicking a focus candidate ORM object for Phase 3 tests."""
    ns = SimpleNamespace(
        id=1,
        modelo=modelo,
        anio=anio,
        tipo_vehiculo=tipo_vehiculo,
        zone_group=None,
        zone_detail=None,
        marca=None,
        precio=None,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _make_engine(focus_candidate=None):
    """CE with all outbound/AI/DB mocked; _focus_candidate returns supplied object."""
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
    eng._routing_gate = MagicMock(return_value=(None, True))
    eng._check_fallback_flow_triggers = MagicMock(return_value=None)
    eng._focus_candidate = MagicMock(return_value=focus_candidate)
    eng._enforce_catalog_vehicle = MagicMock()
    eng._handle_period_request = MagicMock(return_value=None)
    eng._create_candidate_from_catalog = MagicMock()

    return eng


# ─────────────────────────────────────────────────────────────────────────────
# TestW03XGuardUnit — token math only, no CE call
# ─────────────────────────────────────────────────────────────────────────────

class TestW03XGuardUnit(unittest.TestCase):
    """Verify the token-set arithmetic that drives the WILD-03-X guard.

    These tests are independent of CE internals — they prove the input/output
    contract so that the CE-level tests can trust the token math is correct.
    """

    # ── W03-X01 guard math ────────────────────────────────────────────────────

    def test_x01_current_turn_two_years_effective_is_two(self):
        """W03-X01: current turn '2008 o 2014' → 2 effective year tokens → no sync."""
        current = "Revisar una 2008 o 2014 en Balvanera. ¿Cuánto me sale?"
        ct_tokens = {m.group(1) for m in _VEHICLE_YEAR_RE.finditer(current)}
        excl = None  # no model known yet
        ct_effective = ct_tokens - {excl} if excl else ct_tokens
        self.assertEqual(len(ct_effective), 2)
        self.assertIn("2008", ct_effective)
        self.assertIn("2014", ct_effective)

    # ── W03-X02 guard math (the cross-turn defect case) ───────────────────────

    def test_x02_current_turn_ford_ka_has_zero_year_tokens(self):
        """W03-X02 math part A: 'Es un Ford Ka' current turn → 0 year tokens."""
        current = "Es un Ford Ka"
        ct_tokens = {m.group(1) for m in _VEHICLE_YEAR_RE.finditer(current)}
        self.assertEqual(len(ct_tokens), 0)

    def test_x02_all_recent_with_prior_turn_has_two_year_tokens(self):
        """W03-X02 math part B: all_recent includes T1 '2008 o 2014' → 2 effective."""
        all_recent = "Revisar una 2008 o 2014, en Balvanera. ¿Cuánto me sale? Es un Ford Ka"
        excl = "Ka"  # modelo
        all_tokens = {m.group(1) for m in _VEHICLE_YEAR_RE.finditer(all_recent)}
        all_effective = all_tokens - {excl}
        # Should have {"2008", "2014"} → len=2 → no sync
        self.assertEqual(len(all_effective), 2)
        self.assertIn("2008", all_effective)
        self.assertIn("2014", all_effective)

    def test_x02_old_guard_would_have_synced(self):
        """W03-X02 proof of defect: old guard fires because modelo is not None.
        '_extract_year_from_text' on all_recent would return 2008 silently."""
        all_recent = "Revisar una 2008 o 2014, en Balvanera. ¿Cuánto me sale? Es un Ford Ka"
        # Old guard only checked: modelo is not None → condition True → sync
        # Prove what the old guard would have done:
        year = _extract_year_from_text(all_recent, exclude_token="Ka")
        self.assertEqual(year, 2008, "Without new guard, 2008 would be silently committed")

    # ── W03-X03 guard math ────────────────────────────────────────────────────

    def test_x03_single_year_in_current_turn_effective_is_one(self):
        """W03-X03: 'Ford Ka 2014' current turn → 1 effective year (after excl 'Ka')."""
        current = "Ford Ka 2014"
        excl = "Ka"
        ct_tokens = {m.group(1) for m in _VEHICLE_YEAR_RE.finditer(current)}
        ct_effective = ct_tokens - {excl}
        self.assertEqual(len(ct_effective), 1)
        self.assertIn("2014", ct_effective)

    # ── W03-X04 guard math ────────────────────────────────────────────────────

    def test_x04_disambiguation_turn_provides_single_year(self):
        """W03-X04: T2 '2014' (disambiguation) current turn → exactly 1 effective year."""
        current = "2014"
        excl = "Ka"
        ct_tokens = {m.group(1) for m in _VEHICLE_YEAR_RE.finditer(current)}
        ct_effective = ct_tokens - {excl}
        self.assertEqual(len(ct_effective), 1)
        year = _extract_year_from_text(current, exclude_token=excl)
        self.assertEqual(year, 2014)

    # ── W03-X05 guard math ────────────────────────────────────────────────────

    def test_x05_location_turn_has_zero_years_history_has_one(self):
        """W03-X05: 'en Palermo' current turn, T1 'Ford Ka 2019' in history → 1 in all_effective."""
        current = "en Palermo"
        all_recent = "Ford Ka 2019 en Palermo"
        excl = "Ka"
        ct_tokens = {m.group(1) for m in _VEHICLE_YEAR_RE.finditer(current)}
        self.assertEqual(len(ct_tokens), 0)  # no year in current turn
        all_tokens = {m.group(1) for m in _VEHICLE_YEAR_RE.finditer(all_recent)}
        all_effective = all_tokens - {excl}
        self.assertEqual(len(all_effective), 1)
        self.assertIn("2019", all_effective)

    # ── Peugeot 2008 + manufacture year ──────────────────────────────────────

    def test_peugeot_2008_del_2014_exclude_model_token(self):
        """Peugeot 2008 + year 2014: excl='2008' → effective={'2014'} → commit 2014."""
        current = "Peugeot 2008 del 2014"
        excl = "2008"
        ct_tokens = {m.group(1) for m in _VEHICLE_YEAR_RE.finditer(current)}
        ct_effective = ct_tokens - {excl}
        self.assertEqual(len(ct_effective), 1)
        self.assertIn("2014", ct_effective)
        year = _extract_year_from_text(current, exclude_token=excl)
        self.assertEqual(year, 2014)


# ─────────────────────────────────────────────────────────────────────────────
# TestW03XCEIntegration — Phase 3 guard via _process_text
# ─────────────────────────────────────────────────────────────────────────────

class TestW03XCEIntegration(unittest.TestCase):
    """W03-X01 through W03-X05 at the CE level.

    _focus_candidate is mocked to return a pre-built candidate with anio=None.
    After _process_text, candidate.anio reflects what Phase 3 committed (or didn't).
    recent_user_messages mirrors the real n8n payload shape.
    """

    # ── W03-X01: single-turn multi-year → anio stays None ────────────────────

    def test_x01_single_turn_multi_year_anio_stays_none(self):
        """W03-X01: 'Revisar una 2008 o 2014' single turn → anio=None.
        Current turn has 2 effective years → no sync."""
        candidate = _make_candidate(modelo=None, anio=None)
        eng = _make_engine(focus_candidate=candidate)
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        ctx = _make_ctx(state)
        text = "Revisar una 2008 o 2014, en Balvanera. ¿Cuánto me sale?"
        event = _make_event(
            text,
            recent=[text],
            unanswered=[text],
        )
        eng._process_text(ctx, event)
        self.assertIsNone(candidate.anio,
                          "Two year tokens in current turn must block year sync")

    # ── W03-X02: cross-turn — the core defect case ───────────────────────────

    def test_x02_cross_turn_vehicle_known_year_ambiguous_anio_stays_none(self):
        """W03-X02 (KEY): T2 'Es un Ford Ka' with T1 '2008 o 2014' in recent → anio=None.

        This is the exact production defect: Ford Ka is detected on Turn 2,
        modelo='Ka' is set, but the year ambiguity from Turn 1 was never resolved.
        The old guard fired (modelo != None) and silently committed 2008.
        New guard: current turn has 0 year tokens → check history →
        history has {'2008', '2014'} → 2 effective → no sync.
        """
        candidate = _make_candidate(modelo="Ka", anio=None)
        eng = _make_engine(focus_candidate=candidate)
        state = _make_state(last_intent=_INTENT_PREPURCHASE)
        ctx = _make_ctx(state)
        t1 = "Revisar una 2008 o 2014, en Balvanera. ¿Cuánto me sale?"
        t2 = "Es un Ford Ka"
        event = _make_event(
            t2,
            recent=[t1, t2],   # n8n payload: both turns in sliding window
            unanswered=[t2],    # only T2 is unanswered (T1 already got a reply)
        )
        eng._process_text(ctx, event)
        self.assertIsNone(candidate.anio,
                          "Ambiguous prior-turn years must not be committed even when modelo is known")

    def test_x02_old_guard_defect_proof_year_would_have_been_2008(self):
        """W03-X02 proof: old guard would commit 2008 (first match in all_recent).
        This test documents the bug by asserting the old behavior on raw text."""
        all_recent = "Revisar una 2008 o 2014, en Balvanera. ¿Cuánto me sale? Es un Ford Ka"
        year = _extract_year_from_text(all_recent, exclude_token="Ka")
        self.assertEqual(year, 2008,
                         "Without WILD-03-X guard, 2008 is silently picked from all_recent_text")

    # ── W03-X03: single turn with single year → commit ───────────────────────

    def test_x03_single_turn_single_year_committed(self):
        """W03-X03: 'Ford Ka 2014' single turn → anio=2014 committed.
        Current turn has 1 effective year (excl 'Ka') → commit from current turn."""
        candidate = _make_candidate(modelo="Ka", anio=None)
        eng = _make_engine(focus_candidate=candidate)
        state = _make_state(last_intent=_INTENT_PREPURCHASE)
        ctx = _make_ctx(state)
        text = "Quiero revisar un Ford Ka 2014 en Palermo"
        event = _make_event(
            text,
            recent=[text],
            unanswered=[text],
        )
        eng._process_text(ctx, event)
        self.assertEqual(candidate.anio, 2014,
                         "Single unambiguous year in current turn must be committed")

    # ── W03-X04: disambiguation turn provides unambiguous year ───────────────

    def test_x04_disambiguation_turn_resolves_year(self):
        """W03-X04: T3 '2014' after ambiguous T1 → anio=2014 committed.
        current_turn_text='2014' has 1 effective year → commit from current turn
        regardless of what's in all_recent_text.
        """
        candidate = _make_candidate(modelo="Ka", anio=None)
        eng = _make_engine(focus_candidate=candidate)
        state = _make_state(last_intent=_INTENT_PREPURCHASE)
        ctx = _make_ctx(state)
        t1 = "Revisar una 2008 o 2014, en Balvanera"
        t2 = "Es un Ford Ka"
        t3 = "2014"
        event = _make_event(
            t3,
            recent=[t1, t2, t3],
            unanswered=[t3],
        )
        eng._process_text(ctx, event)
        self.assertEqual(candidate.anio, 2014,
                         "Explicit disambiguation turn must commit the single stated year")

    # ── W03-X05: location turn, single year in history → commit from history ─

    def test_x05_location_turn_year_falls_back_to_history(self):
        """W03-X05: T2 'en Palermo' (no year), T1 'Ford Ka 2019' → anio=2019 via history.
        current_turn_text has 0 year tokens → history fallback →
        all_recent has exactly {'2019'} → commit.
        """
        candidate = _make_candidate(modelo="Ka", anio=None)
        eng = _make_engine(focus_candidate=candidate)
        state = _make_state(last_intent=_INTENT_PREPURCHASE)
        ctx = _make_ctx(state)
        t1 = "Ford Ka 2019"
        t2 = "en Palermo"
        event = _make_event(
            t2,
            recent=[t1, t2],
            unanswered=[t2],
        )
        eng._process_text(ctx, event)
        self.assertEqual(candidate.anio, 2019,
                         "Unambiguous single year in history must be committed when current turn has no year")


if __name__ == "__main__":
    unittest.main()
