"""M21.1.4 — ASR Vehicle Normalization and Confidence Handling — Executable Tests.

Source of truth: docs/M21_1_4_asr_vehicle_normalization.md (approved 2026-08-06).

Scenarios:
  SC07  — high-confidence corruption: AUTO_ACCEPT → candidate persisted
  SC08  — low-confidence input: UNRESOLVED → no mutation
  SC09  — corrected Ford KSL near-collision: CONFIRM → clarification
  SC18  — make-constrained matching: Honda constraint prevents Ford Ranger

Unit tests of fuzzy_lookup_vehicle() are independent of CE.
CE integration tests control fuzzy_lookup_vehicle via mock.

Tests run RED before Phase 5 implementation (import will fail until
fuzzy_lookup_vehicle / FuzzyLookupResult exist in vehicle_catalog).
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── Stub heavy deps before any app import ─────────────────────────────────────
for _mod_name in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

# ── Stub PostgreSQL JSONB before any app import (SQLite compat) ───────────────
import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json

_pg_dialect.JSONB = sqlalchemy.JSON   # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON      # type: ignore[attr-defined]

# ── Catalog imports (will fail until fuzzy_lookup_vehicle is added) ───────────
from app.services.vehicle_catalog import (   # noqa: E402
    lookup_vehicle,
    fuzzy_lookup_vehicle,    # M21.1.4 — does not exist yet; import RED until Phase 5
    FuzzyLookupResult,       # M21.1.4 — does not exist yet; import RED until Phase 5
    VehicleMatch,
)
from app.services.conversation_engine import ConversationEngine  # noqa: E402
from app.schemas.conversation import ConversationHandleIn, HANDLED_ACTIONS  # noqa: E402

STAGE_QUALIFYING = "QUALIFYING"
STAGE_QUOTED = "QUOTED"
STAGE_SCHEDULING = "SCHEDULING"

_DEFAULT_AI_RAW = json.dumps({
    "reply": "Respuesta de prueba.",
    "candidate": {"action": "none"},
    "extracted": {},
    "lead_flag": None,
    "needs_human": False,
})


# ── Fixture helpers ────────────────────────────────────────────────────────────

def _make_state(**kw) -> SimpleNamespace:
    ns = SimpleNamespace(
        last_stage=STAGE_QUALIFYING,
        needs_human=False,
        last_intent=None,
        home_zone_group=None,
        home_zone_detail=None,
        home_address=None,
        distance_km=None,
        current_focus_candidate_id=None,
        preferred_day=None,
        preferred_time=None,
        active_requested_date=None,
        last_requested_time=None,
        last_offered_slots=None,
        last_visible_slots=None,
        is_website_lead=False,
        flow_booking_token=None,
        current_revision_id=None,
        customer_name=None,
        vehicle_clarification_sent=False,
        location_clarification_sent=False,
        vehicle_fallback_flow_sent=False,
        location_fallback_flow_sent=False,
        inspectability_clarification_sent=False,
        last_processed_inbound_wa_message_id=None,
        pending_fuzzy_catalog_key=None,   # M21.1.4: pending confirmation field
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _make_lead(**kw) -> SimpleNamespace:
    ns = SimpleNamespace(
        id=1, flag="PRESUPUESTANDO", estado="CONSULTA_NUEVA",
        nombre="Test", telefono=None, necesita_humano=False,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _make_candidate(**kw) -> SimpleNamespace:
    ns = SimpleNamespace(
        id=10, thread_id=42, status="current_focus",
        tipo_vehiculo="AUTO", marca="Toyota", modelo="Corolla",
        anio=2020, zone_group=None, zone_detail=None,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _make_ctx(state=None, lead=None, candidates=None) -> SimpleNamespace:
    ctx = SimpleNamespace()
    ctx.thread = SimpleNamespace(id=42)
    ctx.contact = SimpleNamespace(wa_id="5491199999999")
    ctx.lead = lead if lead is not None else _make_lead()
    ctx.state = state if state is not None else _make_state()
    ctx.candidates = candidates if candidates is not None else []
    return ctx


def _make_event(text: str) -> ConversationHandleIn:
    return ConversationHandleIn(
        thread_id=42,
        wa_message_id="test-vn-wa-id",
        wa_id="5491199999999",
        text=text,
        unanswered_recent_user_messages=[],
        recent_user_messages=[text],
    )


def _make_engine() -> ConversationEngine:
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock()
    eng.settings = MagicMock()
    eng.settings.whatsapp_flow_id = ""
    eng.settings.openai_api_key = "sk-fake"
    eng.settings.openai_chat_model = "gpt-4o-mini"
    eng.settings.backend_url = "http://localhost:8000"
    eng._send_text_to_wa = MagicMock(return_value="mock-wa-id")
    eng._send_fallback_human_review_notification = MagicMock()
    eng._call_openai = MagicMock(return_value=_DEFAULT_AI_RAW)
    eng._build_ai_messages = MagicMock(return_value=[])
    eng._compute_price_quote = MagicMock(return_value=None)
    eng._extract_zone_from_text = MagicMock(return_value=None)
    eng._normalize_zone_from_db = MagicMock()
    eng._routing_gate = MagicMock(return_value=(None, True))
    eng._check_fallback_flow_triggers = MagicMock(return_value=None)
    eng._apply_extracted = MagicMock()
    eng._apply_candidate = MagicMock()
    eng._enforce_catalog_vehicle = MagicMock()
    eng._create_candidate_from_catalog = MagicMock()
    eng._try_schedule_and_flow = MagicMock(return_value=None)
    eng._handle_day_only_request = MagicMock(return_value=None)
    eng._handle_period_request = MagicMock(return_value=None)
    eng._build_quote_reply = MagicMock(return_value="Cotización: $999.")
    eng._pricing = MagicMock()
    eng._scrub_invented_price = MagicMock(side_effect=lambda r, q: r)
    eng._focus_candidate = MagicMock(return_value=None)
    return eng


def _fuzzy_result(outcome, marca=None, modelo=None, tipo="AUTO",
                  score=0.0, second_score=0.0, gap=0.0, make_constrained=False):
    """Build a FuzzyLookupResult for use in mocks."""
    hit = None
    if marca and modelo:
        hit = VehicleMatch(
            marca=marca, modelo=modelo, tipo_vehiculo=tipo,
            confidence="high", matched_alias=f"{marca.lower()} {modelo.lower()}",
        )
    second_hit = None
    return FuzzyLookupResult(
        outcome=outcome,
        hit=hit,
        score=score,
        second_hit=second_hit,
        second_score=second_score,
        gap=gap,
        make_constrained=make_constrained,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Unit tests: fuzzy_lookup_vehicle() pure function
# ═══════════════════════════════════════════════════════════════════════════════

class TestFuzzyLookupVehicleUnit(unittest.TestCase):
    """Direct tests of fuzzy_lookup_vehicle() against the live catalog."""

    def test_sc09_ford_ksl_top_is_ford_ka(self):
        r = fuzzy_lookup_vehicle("ford ksl")
        self.assertEqual(r.outcome, "CONFIRM")
        self.assertIsNotNone(r.hit)
        self.assertEqual(r.hit.marca, "Ford")
        self.assertEqual(r.hit.modelo, "Ka")

    def test_sc09_ford_ksl_second_is_ford_kuga(self):
        r = fuzzy_lookup_vehicle("ford ksl")
        self.assertIsNotNone(r.second_hit)
        self.assertEqual(r.second_hit.marca, "Ford")
        self.assertEqual(r.second_hit.modelo, "Kuga")

    def test_sc09_ford_ksl_scores(self):
        r = fuzzy_lookup_vehicle("ford ksl")
        self.assertAlmostEqual(r.score, 0.8000, places=3)
        self.assertAlmostEqual(r.second_score, 0.7059, places=2)
        self.assertAlmostEqual(r.gap, 0.0941, places=2)
        self.assertLess(r.gap, 0.15)

    def test_sc07_ford_fiestah_auto_accept(self):
        r = fuzzy_lookup_vehicle("ford fiestah")
        self.assertEqual(r.outcome, "AUTO_ACCEPT")
        self.assertIsNotNone(r.hit)
        self.assertEqual(r.hit.marca, "Ford")
        self.assertEqual(r.hit.modelo, "Fiesta")
        self.assertGreaterEqual(r.score, 0.87)
        self.assertGreaterEqual(r.gap, 0.15)

    def test_sc08_garbled_unresolved(self):
        r = fuzzy_lookup_vehicle("xyz abc")
        self.assertEqual(r.outcome, "UNRESOLVED")
        self.assertIsNone(r.hit)
        self.assertLess(r.score, 0.70)

    def test_sc18_honda_ranger_constrained_unresolved(self):
        r = fuzzy_lookup_vehicle("honda ranger")
        # With Honda make constraint, top Honda model is 0.6667 → UNRESOLVED
        self.assertEqual(r.outcome, "UNRESOLVED")
        self.assertTrue(r.make_constrained)
        if r.hit:
            self.assertEqual(r.hit.marca, "Honda")

    def test_sc18_honda_ranger_not_ford_ranger(self):
        r = fuzzy_lookup_vehicle("honda ranger")
        # Must not offer Ford Ranger
        if r.hit:
            self.assertNotEqual(r.hit.marca, "Ford")

    def test_exact_alias_not_routed_through_fuzzy(self):
        # "ka" is an exact alias — lookup_vehicle handles it, fuzzy should not be called
        exact = lookup_vehicle("ka")
        self.assertIsNotNone(exact)
        self.assertEqual(exact.marca, "Ford")
        self.assertEqual(exact.modelo, "Ka")
        # fuzzy on "ka" may return a result but it's irrelevant — VN-1 ensures exact wins first

    def test_short_one_char_unresolved(self):
        r = fuzzy_lookup_vehicle("a")
        self.assertEqual(r.outcome, "UNRESOLVED")

    def test_short_two_char_unresolved(self):
        r = fuzzy_lookup_vehicle("vw")
        self.assertEqual(r.outcome, "UNRESOLVED")

    def test_greeting_unresolved(self):
        r = fuzzy_lookup_vehicle("hola buenas")
        self.assertEqual(r.outcome, "UNRESOLVED")
        self.assertLess(r.score, 0.70)

    def test_toyota_corola_auto_accept(self):
        r = fuzzy_lookup_vehicle("toyota corola")
        self.assertEqual(r.outcome, "AUTO_ACCEPT")
        self.assertIsNotNone(r.hit)
        self.assertEqual(r.hit.marca, "Toyota")
        self.assertEqual(r.hit.modelo, "Corolla")

    def test_make_constraint_applied_for_ford(self):
        r = fuzzy_lookup_vehicle("ford ksl")
        self.assertTrue(r.make_constrained)
        if r.hit:
            self.assertEqual(r.hit.marca, "Ford")

    def test_confirm_band_score_below_high_threshold(self):
        # renolt clio → score 0.8696 < 0.87 → CONFIRM
        r = fuzzy_lookup_vehicle("renolt clio")
        self.assertEqual(r.outcome, "CONFIRM")
        self.assertIsNotNone(r.hit)
        self.assertEqual(r.hit.marca, "Renault")

    def test_confirm_band_gap_below_gap_threshold(self):
        # chevrolet crkz → score 0.8966 >= 0.87 BUT gap 0.1224 < 0.15 → CONFIRM
        r = fuzzy_lookup_vehicle("chevrolet crkz")
        self.assertEqual(r.outcome, "CONFIRM")
        self.assertIsNotNone(r.hit)
        self.assertEqual(r.hit.marca, "Chevrolet")

    def test_result_has_required_fields(self):
        r = fuzzy_lookup_vehicle("ford ksl")
        self.assertIsInstance(r, FuzzyLookupResult)
        self.assertIn(r.outcome, ("AUTO_ACCEPT", "CONFIRM", "UNRESOLVED"))
        self.assertIsInstance(r.score, float)
        self.assertIsInstance(r.second_score, float)
        self.assertIsInstance(r.gap, float)
        self.assertIsInstance(r.make_constrained, bool)

    def test_empty_input_unresolved(self):
        r = fuzzy_lookup_vehicle("")
        self.assertEqual(r.outcome, "UNRESOLVED")
        self.assertIsNone(r.hit)


# ═══════════════════════════════════════════════════════════════════════════════
# SC07 — HIGH_CONFIDENCE: AUTO_ACCEPT → candidate persisted without confirmation
# ═══════════════════════════════════════════════════════════════════════════════

class TestSC07AutoAccept(unittest.TestCase):
    """VN-6 AUTO_ACCEPT: exact miss + high score + wide gap → create candidate immediately."""

    MSG = "ford fiestah"

    def _run(self, fuzzy_result_obj):
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state=state)
        event = _make_event(self.MSG)
        with (
            patch("app.services.conversation_engine.lookup_vehicle", return_value=None),
            patch("app.services.conversation_engine.fuzzy_lookup_vehicle",
                  return_value=fuzzy_result_obj),
        ):
            result = eng._process_text(ctx, event)
        return eng, result, state

    def test_exact_miss_then_fuzzy_called(self):
        fr = _fuzzy_result("AUTO_ACCEPT", "Ford", "Fiesta", "AUTO", 0.9565, 0.6957, 0.2609)
        eng, result, state = self._run(fr)
        eng._create_candidate_from_catalog.assert_called_once()

    def test_auto_accept_candidate_created_with_correct_vehicle(self):
        fr = _fuzzy_result("AUTO_ACCEPT", "Ford", "Fiesta", "AUTO", 0.9565, 0.6957, 0.2609)
        eng, result, state = self._run(fr)
        args, kwargs = eng._create_candidate_from_catalog.call_args
        match_arg = args[2] if len(args) >= 3 else kwargs.get("match")
        self.assertEqual(match_arg.marca, "Ford")
        self.assertEqual(match_arg.modelo, "Fiesta")

    def test_auto_accept_no_confirmation_message_sent(self):
        fr = _fuzzy_result("AUTO_ACCEPT", "Ford", "Fiesta", "AUTO", 0.9565, 0.6957, 0.2609)
        eng, result, state = self._run(fr)
        for call_args in eng._send_text_to_wa.call_args_list:
            text = call_args[0][1] if len(call_args[0]) >= 2 else str(call_args)
            self.assertNotIn("¿Es un", text)

    def test_auto_accept_no_pending_key_stored(self):
        fr = _fuzzy_result("AUTO_ACCEPT", "Ford", "Fiesta", "AUTO", 0.9565, 0.6957, 0.2609)
        eng, result, state = self._run(fr)
        self.assertIsNone(getattr(state, "pending_fuzzy_catalog_key", None))


# ═══════════════════════════════════════════════════════════════════════════════
# SC08 — UNRESOLVED: no candidate mutation, no pricing, no confirmation
# ═══════════════════════════════════════════════════════════════════════════════

class TestSC08Unresolved(unittest.TestCase):
    """VN-6 UNRESOLVED: score < 0.70 → no mutation; existing unknown-vehicle path."""

    MSG = "xyz abc"

    def _run(self, fuzzy_result_obj):
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state=state)
        event = _make_event(self.MSG)
        with (
            patch("app.services.conversation_engine.lookup_vehicle", return_value=None),
            patch("app.services.conversation_engine.fuzzy_lookup_vehicle",
                  return_value=fuzzy_result_obj),
        ):
            result = eng._process_text(ctx, event)
        return eng, result, state

    def test_unresolved_no_candidate_created(self):
        fr = _fuzzy_result("UNRESOLVED", score=0.4286)
        eng, result, state = self._run(fr)
        eng._create_candidate_from_catalog.assert_not_called()

    def test_unresolved_no_pending_key_stored(self):
        fr = _fuzzy_result("UNRESOLVED", score=0.4286)
        eng, result, state = self._run(fr)
        self.assertIsNone(getattr(state, "pending_fuzzy_catalog_key", None))

    def test_unresolved_no_fuzzy_confirmation_sent(self):
        fr = _fuzzy_result("UNRESOLVED", score=0.4286)
        eng, result, state = self._run(fr)
        for call_args in eng._send_text_to_wa.call_args_list:
            text = call_args[0][1] if len(call_args[0]) >= 2 else str(call_args)
            self.assertNotIn("¿Es un", text)


# ═══════════════════════════════════════════════════════════════════════════════
# SC09 — CONFIRM (ford ksl): near-collision → clarification, no candidate
# ═══════════════════════════════════════════════════════════════════════════════

class TestSC09FordKSL(unittest.TestCase):
    """VN-7: ford ksl → CONFIRM → exact question ¿Es un Ford Ka?, no candidate."""

    MSG = "ford ksl"

    def _fuzzy_ka(self):
        return _fuzzy_result(
            "CONFIRM", "Ford", "Ka", "AUTO",
            score=0.8000, second_score=0.7059, gap=0.0941, make_constrained=True,
        )

    def _run(self, fuzzy_result_obj):
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state=state)
        event = _make_event(self.MSG)
        with (
            patch("app.services.conversation_engine.lookup_vehicle", return_value=None),
            patch("app.services.conversation_engine.fuzzy_lookup_vehicle",
                  return_value=fuzzy_result_obj),
        ):
            result = eng._process_text(ctx, event)
        return eng, result, state

    def test_exact_lookup_misses(self):
        # Verify baseline: lookup_vehicle("ford ksl") is None
        self.assertIsNone(lookup_vehicle("ford ksl"))

    def test_confirm_sends_exact_question(self):
        eng, result, state = self._run(self._fuzzy_ka())
        texts = [c[0][1] for c in eng._send_text_to_wa.call_args_list if len(c[0]) >= 2]
        self.assertTrue(
            any("¿Es un Ford Ka?" in t for t in texts),
            f"Expected '¿Es un Ford Ka?' in outbound texts: {texts}",
        )

    def test_confirm_no_candidate_created_before_confirmation(self):
        eng, result, state = self._run(self._fuzzy_ka())
        eng._create_candidate_from_catalog.assert_not_called()

    def test_confirm_pending_key_stored(self):
        eng, result, state = self._run(self._fuzzy_ka())
        self.assertEqual(getattr(state, "pending_fuzzy_catalog_key", None), "Ford||Ka")

    def test_confirm_result_is_handled(self):
        eng, result, state = self._run(self._fuzzy_ka())
        self.assertIn(result.action, HANDLED_ACTIONS | {"replied"})

    def test_confirm_no_pricing(self):
        eng, result, state = self._run(self._fuzzy_ka())
        # Price quote should not be used for vehicle proposal
        eng._build_quote_reply.assert_not_called()

    def test_confirm_kill_switch_blocked(self):
        """When outbound is disabled, SC09 CONFIRM must return handled action."""
        from app.services.outbound_guard import OutboundBlockedError
        _err = OutboundBlockedError(
            sender_path="test", kind="text",
            to_wa_id="5491199999999", thread_id=42,
        )
        eng = _make_engine()
        eng._send_text_to_wa = MagicMock(side_effect=_err)
        state = _make_state()
        ctx = _make_ctx(state=state)
        event = _make_event(self.MSG)
        with (
            patch("app.services.conversation_engine.lookup_vehicle", return_value=None),
            patch("app.services.conversation_engine.fuzzy_lookup_vehicle",
                  return_value=self._fuzzy_ka()),
        ):
            result = eng._process_text(ctx, event)
        self.assertTrue(result.handled)
        self.assertIn(result.action, HANDLED_ACTIONS)


# ═══════════════════════════════════════════════════════════════════════════════
# SC18 — Make-constrained matching
# ═══════════════════════════════════════════════════════════════════════════════

class TestSC18MakeConstrained(unittest.TestCase):
    """VN-5: Honda make constraint prevents Ford Ranger auto-correction.

    SC18 input: 'honda ranger'
    Without constraint: Ford Ranger (0.7826) → CONFIRM (wrong brand)
    With Honda constraint: Honda HR-V (0.6667) → UNRESOLVED (correct — no match)
    """

    def test_sc18_honda_ranger_is_unresolved(self):
        r = fuzzy_lookup_vehicle("honda ranger")
        self.assertEqual(r.outcome, "UNRESOLVED")

    def test_sc18_honda_ranger_make_constrained(self):
        r = fuzzy_lookup_vehicle("honda ranger")
        self.assertTrue(r.make_constrained)

    def test_sc18_honda_ranger_no_ford_in_hit(self):
        r = fuzzy_lookup_vehicle("honda ranger")
        if r.hit:
            self.assertNotEqual(r.hit.marca, "Ford")

    def test_sc18_ce_no_candidate_for_honda_ranger(self):
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state=state)
        event = _make_event("honda ranger")
        unresolved = _fuzzy_result("UNRESOLVED", score=0.6667, make_constrained=True)
        with (
            patch("app.services.conversation_engine.lookup_vehicle", return_value=None),
            patch("app.services.conversation_engine.fuzzy_lookup_vehicle",
                  return_value=unresolved),
        ):
            result = eng._process_text(ctx, event)
        eng._create_candidate_from_catalog.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# Multi-turn: CONFIRM → accept/reject
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultiTurnConfirmation(unittest.TestCase):
    """VN-8: Acceptance creates candidate + clears pending key.
             Rejection creates no candidate; rejection with vehicle uses that vehicle.
    """

    def _run_turn(self, text, state, fuzzy_result_obj=None, exact_result=None):
        eng = _make_engine()
        ctx = _make_ctx(state=state)
        event = _make_event(text)
        exact_patch = exact_result
        fuzzy_patch = fuzzy_result_obj or _fuzzy_result("UNRESOLVED", score=0.4)
        with (
            patch("app.services.conversation_engine.lookup_vehicle", return_value=exact_patch),
            patch("app.services.conversation_engine.fuzzy_lookup_vehicle",
                  return_value=fuzzy_patch),
        ):
            result = eng._process_text(ctx, event)
        return eng, result

    def test_acceptance_si_creates_ford_ka(self):
        state = _make_state(pending_fuzzy_catalog_key="Ford||Ka")
        eng, result = self._run_turn("Sí", state)
        eng._create_candidate_from_catalog.assert_called_once()
        args, kwargs = eng._create_candidate_from_catalog.call_args
        match_arg = args[2] if len(args) >= 3 else kwargs.get("match")
        self.assertEqual(match_arg.marca, "Ford")
        self.assertEqual(match_arg.modelo, "Ka")

    def test_acceptance_clears_pending_key(self):
        state = _make_state(pending_fuzzy_catalog_key="Ford||Ka")
        self._run_turn("Sí", state)
        self.assertIsNone(getattr(state, "pending_fuzzy_catalog_key", None))

    def test_acceptance_creates_candidate_exactly_once(self):
        state = _make_state(pending_fuzzy_catalog_key="Ford||Ka")
        eng, result = self._run_turn("Sí", state)
        self.assertEqual(eng._create_candidate_from_catalog.call_count, 1)

    def test_rejection_no_creates_no_candidate(self):
        state = _make_state(pending_fuzzy_catalog_key="Ford||Ka")
        eng, result = self._run_turn("No", state)
        eng._create_candidate_from_catalog.assert_not_called()

    def test_rejection_clears_pending_key(self):
        state = _make_state(pending_fuzzy_catalog_key="Ford||Ka")
        self._run_turn("No", state)
        self.assertIsNone(getattr(state, "pending_fuzzy_catalog_key", None))

    def test_rejection_with_vehicle_uses_that_vehicle(self):
        state = _make_state(pending_fuzzy_catalog_key="Ford||Ka")
        kuga_match = VehicleMatch(
            marca="Ford", modelo="Kuga", tipo_vehiculo="SUV_4X4_DEPORTIVO",
            confidence="high", matched_alias="ford kuga",
        )
        eng, result = self._run_turn("No, es un Ford Kuga", state, exact_result=kuga_match)
        eng._create_candidate_from_catalog.assert_called_once()
        args, kwargs = eng._create_candidate_from_catalog.call_args
        match_arg = args[2] if len(args) >= 3 else kwargs.get("match")
        self.assertEqual(match_arg.modelo, "Kuga")

    def test_repeated_message_does_not_duplicate_proposal(self):
        """ford ksl sent twice: second turn sees pending key already set → no re-proposal."""
        state = _make_state(pending_fuzzy_catalog_key="Ford||Ka")
        eng = _make_engine()
        ctx = _make_ctx(state=state)
        event = _make_event("ford ksl")
        fuzzy_ka = _fuzzy_result("CONFIRM", "Ford", "Ka", "AUTO", 0.8, 0.7059, 0.0941, True)
        with (
            patch("app.services.conversation_engine.lookup_vehicle", return_value=None),
            patch("app.services.conversation_engine.fuzzy_lookup_vehicle", return_value=fuzzy_ka),
        ):
            result = eng._process_text(ctx, event)
        # Should NOT create candidate or store a second proposal — still awaiting confirmation
        eng._create_candidate_from_catalog.assert_not_called()

    def test_existing_candidate_not_overwritten_by_fuzzy_text(self):
        """VN-13: existing focused candidate survives unrelated fuzzy phrase."""
        existing = _make_candidate(marca="Toyota", modelo="Corolla")
        eng = _make_engine()
        eng._focus_candidate = MagicMock(return_value=existing)
        state = _make_state()
        ctx = _make_ctx(state=state, candidates=[existing])
        event = _make_event("ford fiestah")
        fr = _fuzzy_result("AUTO_ACCEPT", "Ford", "Fiesta", "AUTO", 0.9565, 0.6957, 0.2609)
        with (
            patch("app.services.conversation_engine.lookup_vehicle", return_value=None),
            patch("app.services.conversation_engine.fuzzy_lookup_vehicle", return_value=fr),
        ):
            result = eng._process_text(ctx, event)
        # Existing candidate must not be overwritten
        self.assertEqual(existing.marca, "Toyota")
        self.assertEqual(existing.modelo, "Corolla")


# ═══════════════════════════════════════════════════════════════════════════════
# Higher-priority gate tests: fuzzy must not fire before motorcycle/inspectability
# ═══════════════════════════════════════════════════════════════════════════════

class TestHigherPriorityGates(unittest.TestCase):
    """VN-11/VN-12: motorcycle, inspectability gates run before fuzzy lookup."""

    def test_motorcycle_text_fuzzy_not_called(self):
        eng = _make_engine()
        eng._handle_vehicle_inspectability_gate = MagicMock(return_value=None)
        state = _make_state()
        ctx = _make_ctx(state=state)
        event = _make_event("tengo una moto honda cg 150")
        with (
            patch("app.services.conversation_engine.lookup_vehicle", return_value=None),
            patch("app.services.conversation_engine.fuzzy_lookup_vehicle") as fuzz_mock,
        ):
            try:
                eng._process_text(ctx, event)
            except Exception:
                pass
            # Motorcycle gate (Layer A) fires before fuzzy lookup
            fuzz_mock.assert_not_called()

    def test_location_plus_asr_vehicle_cooperates_with_m21_1_3(self):
        """Location M21.1.3 and fuzzy vehicle AUTO_ACCEPT cooperate on same turn.

        No pre-existing candidate: fuzzy fires and creates Ford Fiesta;
        zone detection also runs (M21.1.3 bare-locality path, Palermo → CABA).
        """
        from types import SimpleNamespace as _NS
        eng = _make_engine()
        # No pre-existing candidate → fuzzy fires (VN-13: no existing candidate to protect)
        eng._focus_candidate = MagicMock(return_value=None)
        zone = _NS(zone_group="CABA", zone_detail="Palermo", viaticos=0)
        eng._extract_zone_from_text = MagicMock(return_value=zone)
        state = _make_state()
        ctx = _make_ctx(state=state, candidates=[])
        event = _make_event("ford fiestah en Palermo")
        fr = _fuzzy_result("AUTO_ACCEPT", "Ford", "Fiesta", "AUTO", 0.9565, 0.6957, 0.2609)
        with (
            patch("app.services.conversation_engine.lookup_vehicle", return_value=None),
            patch("app.services.conversation_engine.fuzzy_lookup_vehicle", return_value=fr),
        ):
            result = eng._process_text(ctx, event)
        # Fuzzy creates candidate (AUTO_ACCEPT)
        eng._create_candidate_from_catalog.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# VN-1: exact lookup always wins — fuzzy must not be invoked on exact alias
# ═══════════════════════════════════════════════════════════════════════════════

class TestExactLookupAlwaysWins(unittest.TestCase):
    """VN-1: when exact lookup succeeds, fuzzy is never called."""

    def test_ford_ka_exact_match_no_fuzzy(self):
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state=state)
        event = _make_event("ford ka")
        ford_ka = VehicleMatch(
            marca="Ford", modelo="Ka", tipo_vehiculo="AUTO",
            confidence="high", matched_alias="ford ka",
        )
        with (
            patch("app.services.conversation_engine.lookup_vehicle", return_value=ford_ka),
            patch("app.services.conversation_engine.fuzzy_lookup_vehicle") as fuzz_mock,
        ):
            try:
                eng._process_text(ctx, event)
            except Exception:
                pass
            fuzz_mock.assert_not_called()

    def test_exact_toyota_corolla_no_fuzzy(self):
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state=state)
        event = _make_event("toyota corolla")
        corolla = VehicleMatch(
            marca="Toyota", modelo="Corolla", tipo_vehiculo="AUTO",
            confidence="high", matched_alias="toyota corolla",
        )
        with (
            patch("app.services.conversation_engine.lookup_vehicle", return_value=corolla),
            patch("app.services.conversation_engine.fuzzy_lookup_vehicle") as fuzz_mock,
        ):
            try:
                eng._process_text(ctx, event)
            except Exception:
                pass
            fuzz_mock.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 4 — Established-intent guard validation (M21.1.4-FV)
# ═══════════════════════════════════════════════════════════════════════════════

_INTENT_PREPURCHASE = "PREPURCHASE_INSPECTION"


class TestEstablishedIntentGuard(unittest.TestCase):
    """E1–E3: validate that the CONFIRM guard protects candidate context, not all
    PREPURCHASE threads.  The guard was narrowed in M21.1.4-FV from
    'last_intent != PREPURCHASE' to 'has existing candidate context'.
    """

    def test_e1_prepurchase_no_candidate_ford_ksl_confirms(self):
        """E1: last_intent=PREPURCHASE_INSPECTION, no focused candidate, 'ford ksl'.
        Expected: CONFIRM fired; '¿Es un Ford Ka?' sent; pending key set; no candidate."""
        eng = _make_engine()
        state = _make_state(
            last_intent=_INTENT_PREPURCHASE,
            current_focus_candidate_id=None,  # no prior candidate
        )
        ctx = _make_ctx(state=state, candidates=[])
        event = _make_event("ford ksl")
        with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
            result = eng._process_text(ctx, event)
        self.assertEqual(result.action, "replied")
        self.assertIn(result.action, HANDLED_ACTIONS)
        self.assertEqual(eng._call_openai.call_count, 0, "E1: fuzzy CONFIRM must not call AI")
        self.assertEqual(
            eng._send_text_to_wa.call_args[0][1], "¿Es un Ford Ka?",
            "E1: must send exact CONFIRM question",
        )
        self.assertEqual(state.pending_fuzzy_catalog_key, "Ford||Ka", "E1: pending key must be set")
        self.assertEqual(ctx.candidates, [], "E1: no candidate created before confirmation")

    def test_e2_prepurchase_focused_candidate_fuzzy_blocked(self):
        """E2: last_intent=PREPURCHASE_INSPECTION, focused candidate exists, unrelated text.
        Expected: existing candidate unchanged; VN-13 blocks fuzzy; AI handles turn."""
        cand = _make_candidate(marca="Ford", modelo="Ka", anio=2019)
        eng = _make_engine()
        state = _make_state(
            last_intent=_INTENT_PREPURCHASE,
            current_focus_candidate_id=cand.id,
        )
        ctx = _make_ctx(state=state, candidates=[cand])
        event = _make_event("algo raro qsx plm")
        with (
            patch("app.services.conversation_engine.lookup_vehicle", return_value=None),
            patch("app.services.conversation_engine.fuzzy_lookup_vehicle") as fuzz_mock,
        ):
            eng._process_text(ctx, event)
        fuzz_mock.assert_not_called()  # VN-13: fuzzy blocked when candidate exists
        self.assertIsNone(state.pending_fuzzy_catalog_key)

    def test_e3_prepurchase_focused_candidate_explicit_correction_no_fuzzy(self):
        """E3: last_intent=PREPURCHASE_INSPECTION, focused candidate, explicit correction text.
        Expected: fuzzy must not silently replace the candidate (VN-13 blocks fuzzy)."""
        cand = _make_candidate(marca="Ford", modelo="Ka", anio=2019)
        eng = _make_engine()
        state = _make_state(
            last_intent=_INTENT_PREPURCHASE,
            current_focus_candidate_id=cand.id,
        )
        ctx = _make_ctx(state=state, candidates=[cand])
        event = _make_event("No, en realidad es un Ford Kuga")
        with (
            patch("app.services.conversation_engine.lookup_vehicle", return_value=None),
            patch("app.services.conversation_engine.fuzzy_lookup_vehicle") as fuzz_mock,
        ):
            eng._process_text(ctx, event)
        fuzz_mock.assert_not_called()  # VN-13: candidates present → fuzzy never runs
        # Candidate itself must not have been silently overwritten by fuzzy
        self.assertEqual(ctx.candidates[0].marca, "Ford")
        self.assertEqual(ctx.candidates[0].modelo, "Ka")


if __name__ == "__main__":
    unittest.main(verbosity=2)
