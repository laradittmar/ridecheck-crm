"""WILD-02 Qualification Continuity regression pack.

Tests for two root causes of a repeated opening message in QUALIFYING-stage:

  Root A: After CE sends _UNCERTAIN_SERVICE_REPLY, it sets no state.
          On the next turn, step 7 of _handle_qualifying_intent fails and
          step 9 fires the opening again.

  Root B: "2008" has no bare alias in vehicle_catalog.json — only "peugeot 2008".
          Contextual numeric resolution is needed.

  Secondary: _create_candidate_from_catalog extracts year=2008 from "Peugeot 2008"
             (model number, not year). exclude_token guard prevents this.

Test groups:
  W02-N: Pure function tests (no CE machinery needed)
  W02-A: CE layer tests — _handle_qualifying_intent mock pattern
  W02-L: Linked scenario tests
  W02-D: Duplicate-opening prevention (regression guard)
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

import os
os.environ["OUTBOUND_ENABLED"] = "true"

from app.services.conversation_engine import (  # noqa: E402
    ConversationEngine,
    _extract_year_from_text,
    _AWAITING_QUALIFICATION,
    _INTENT_PREPURCHASE,
    _UNCERTAIN_SERVICE_REPLY,
    _FUZZY_CONFIRMATION_TEMPLATE,
)
from app.services.vehicle_catalog import (      # noqa: E402
    lookup_vehicle,
    _contextual_numeric_model_lookup,
)
from app.schemas.conversation import ConversationHandleIn  # noqa: E402


# ── Shared test infrastructure ────────────────────────────────────────────────

def _make_engine():
    """CE with real vehicle lookup + _handle_qualifying_intent.
    Mocked: outbound (no WhatsApp calls), AI, pricing.
    """
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock()
    eng.settings = MagicMock()
    eng.settings.openai_api_key = "sk-fake"
    eng.settings.openai_chat_model = "gpt-4o-mini"
    eng.settings.backend_url = "http://localhost:8000"
    eng.settings.whatsapp_location_fallback_flow_id = ""
    eng.settings.whatsapp_vehicle_fallback_flow_id = ""

    eng._send_text_to_wa = MagicMock(return_value="mock-wa-id")
    eng._send_fallback_human_review_notification = MagicMock()

    eng._call_openai = MagicMock(return_value=json.dumps({
        "intent": "QUALIFYING", "reply": "Seguimos.", "deferred_interest": False,
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


def _make_event(text, thread_id=99, recent=None, unanswered=None):
    msgs = unanswered or [text]
    return ConversationHandleIn(
        thread_id=thread_id,
        wa_message_id=f"msg-{hash(text) % 100000}",
        wa_id="5491199990000",
        text=text,
        recent_user_messages=recent or msgs,
        unanswered_recent_user_messages=msgs,
    )


def _call_qualifying_intent(eng, state, ctx, text):
    """Directly call _handle_qualifying_intent for unit testing Step 0/9."""
    return eng._handle_qualifying_intent(ctx, state, text)


# ── W02-N: Pure function tests ────────────────────────────────────────────────

class TestW02NPureFunctions(unittest.TestCase):
    """W02-N: No CE machinery — pure function correctness."""

    def test_n01_year_extract_excludes_model_token(self):
        """W02-N01: Peugeot 2008 with exclude_token='2008' → None."""
        result = _extract_year_from_text("Peugeot 2008", exclude_token="2008")
        self.assertIsNone(result, "2008 is model number; should return None when excluded")

    def test_n02_year_extract_second_year_still_found(self):
        """W02-N02: Peugeot 2008 2020 with exclude_token='2008' → 2020."""
        result = _extract_year_from_text("Peugeot 2008 2020", exclude_token="2008")
        self.assertEqual(result, 2020, "2020 is a real year and should be returned")

    def test_n03_focus_2008_lookup_and_year_not_excluded(self):
        """W02-N03: Focus 2008 → Ford Focus; year extraction with Focus's modelo is not excluded."""
        match = lookup_vehicle("Focus 2008")
        self.assertIsNotNone(match, "Focus should be found in catalog")
        self.assertEqual(match.marca, "Ford")
        self.assertIn("Focus", str(match.modelo))
        # The exclude_token for Focus would be "Focus" (not "2008"), so 2008 is still returned
        year = _extract_year_from_text("Focus 2008", exclude_token=str(match.modelo))
        self.assertEqual(year, 2008, "2008 should still be recognized as a year for Focus")

    def test_n04_gol_2008_year_extraction(self):
        """W02-N04: Gol 2008 → VW Gol; year extraction → 2008."""
        match = lookup_vehicle("Gol 2008")
        self.assertIsNotNone(match, "Gol should be found in catalog")
        self.assertEqual(match.marca, "Volkswagen")
        year = _extract_year_from_text("Gol 2008", exclude_token=str(match.modelo))
        self.assertEqual(year, 2008, "2008 should still be year for Gol")

    def test_n05_generic_vehicle_word_blocks_contextual(self):
        """W02-N05: 'un auto 2008' → None (generic vehicle word blocks model resolution)."""
        result = _contextual_numeric_model_lookup("un auto 2008")
        self.assertIsNone(result, "'auto' is a generic word; should return None")

    def test_n06_bare_2008_returns_peugeot(self):
        """W02-N06: '2008' bare → Peugeot 2008 (no generic word, no competing model)."""
        result = _contextual_numeric_model_lookup("2008")
        self.assertIsNotNone(result, "Bare '2008' should resolve to Peugeot 2008 in context")
        self.assertEqual(result.marca, "Peugeot")
        self.assertEqual(str(result.modelo), "2008")
        self.assertEqual(result.confidence, "medium")

    def test_n07_contextual_with_location_hint(self):
        """W02-N07: 'un 2008 en berazategui' → Peugeot 2008 (location tokens not generic words)."""
        result = _contextual_numeric_model_lookup("un 2008 en berazategui")
        self.assertIsNotNone(result, "'un' and location tokens don't block resolution")
        self.assertEqual(result.marca, "Peugeot")
        self.assertEqual(str(result.modelo), "2008")

    def test_n08_focus_caught_by_lookup_not_contextual(self):
        """W02-N08: 'Focus 2008' is caught by lookup_vehicle first; contextual is never called.

        This test documents that the real-flow ordering (exact lookup first, contextual only
        on miss) prevents 'Focus 2008' from wrongly resolving to Peugeot 2008 via contextual.
        The contextual function itself (when called directly) would return Peugeot 2008 because
        'focus' is not in _GENERIC_VEHICLE_WORDS — but in the real flow, lookup_vehicle fires
        first and catches Focus. This test asserts that lookup_vehicle catches it.
        """
        match = lookup_vehicle("Focus 2008")
        self.assertIsNotNone(match, "lookup_vehicle must catch 'Focus 2008' before contextual runs")
        self.assertEqual(match.marca, "Ford")

    def test_n09_peugeot_2008_candidate_year_is_none(self):
        """W02-N09: Candidate created from 'Peugeot 2008' source text → anio=None (not 2008)."""
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state)
        match = lookup_vehicle("Peugeot 2008")
        self.assertIsNotNone(match)
        # _create_candidate_from_catalog with source_text="Peugeot 2008"
        eng._create_candidate_from_catalog(ctx, state, match, source_text="Peugeot 2008")
        self.assertEqual(len(ctx.candidates), 1)
        cand = ctx.candidates[0]
        self.assertIsNone(cand.anio, f"anio must be None (not 2008) for Peugeot 2008; got {cand.anio}")

    def test_n09b_peugeot_2008_with_real_year(self):
        """W02-N09b: Candidate created from 'Peugeot 2008 2020' → anio=2020."""
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state)
        match = lookup_vehicle("Peugeot 2008")
        eng._create_candidate_from_catalog(ctx, state, match, source_text="Peugeot 2008 2020")
        self.assertEqual(len(ctx.candidates), 1)
        cand = ctx.candidates[0]
        self.assertEqual(cand.anio, 2020, f"anio should be 2020; got {cand.anio}")

    def test_n09c_backward_compat_no_exclude(self):
        """W02-N09c: _extract_year_from_text with no exclude_token → original behavior."""
        self.assertEqual(_extract_year_from_text("Focus 2008"), 2008)
        self.assertEqual(_extract_year_from_text("Gol 2015"), 2015)
        self.assertIsNone(_extract_year_from_text("hola como estas"))
        self.assertEqual(_extract_year_from_text("2019 Peugeot 2008"), 2019)


# ── W02-A: CE layer tests ─────────────────────────────────────────────────────

class TestW02AHandleQualifyingIntent(unittest.TestCase):
    """W02-A: Direct _handle_qualifying_intent behavior with AWAITING_QUALIFICATION."""

    def setUp(self):
        self.eng = _make_engine()

    def test_a01_step9_sets_awaiting_qualification(self):
        """W02-A01: Unknown message → step 9 fires → state.last_intent == AWAITING_QUALIFICATION."""
        state = _make_state(last_intent=None)
        ctx = _make_ctx(state)
        result = _call_qualifying_intent(self.eng, state, ctx, "xyz abc def completamente desconocido")
        self.assertIsNotNone(result, "Step 9 must return a boundary reply")
        self.assertEqual(state.last_intent, _AWAITING_QUALIFICATION,
                         f"Step 9 must set AWAITING_QUALIFICATION; got {state.last_intent!r}")

    def test_a02_affirmative_si_returns_none(self):
        """W02-A02: state.last_intent==AWAITING_QUALIFICATION + 'si' → Step 0 fires → returns None."""
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        ctx = _make_ctx(state)
        result = _call_qualifying_intent(self.eng, state, ctx, "si")
        self.assertIsNone(result, "After affirmative, _handle_qualifying_intent must return None (proceed to AI)")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE,
                         "Step 0 must upgrade intent to PREPURCHASE")

    def test_a02_affirmative_dale_returns_none(self):
        """W02-A02 variant: 'dale' also counts as affirmative."""
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        ctx = _make_ctx(state)
        result = _call_qualifying_intent(self.eng, state, ctx, "dale")
        self.assertIsNone(result)
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)

    def test_a02_affirmative_claro_returns_none(self):
        """W02-A02 variant: 'claro' also counts as affirmative."""
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        ctx = _make_ctx(state)
        result = _call_qualifying_intent(self.eng, state, ctx, "claro")
        self.assertIsNone(result)
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)

    def test_a03_affirmative_with_vehicle_returns_none(self):
        """W02-A03: AWAITING + 'si, un focus 2019 en berazategui' → Step 0 + Step 4 → None."""
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        ctx = _make_ctx(state)
        result = _call_qualifying_intent(self.eng, state, ctx, "si, un focus 2019 en berazategui")
        self.assertIsNone(result, "Vehicle in text means step 4 also fires; must return None")

    def test_a04_awaiting_plus_si_2008_returns_none(self):
        """W02-A04: AWAITING + 'si, un 2008 en berazategui' → Step 0 sets PREPURCHASE → step 7 passes → None.

        With Root A fixed, step 0 upgrades intent to PREPURCHASE.
        Even though lookup_vehicle('si, un 2008 en berazategui') is None (bare 2008 not in catalog),
        step 7 passes because intent was set by step 0.
        """
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        ctx = _make_ctx(state)
        result = _call_qualifying_intent(self.eng, state, ctx, "si, un 2008 en berazategui")
        self.assertIsNone(result,
            "After affirmative + 2008, must return None (no repeated opening question)")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE,
                         "Intent must be PREPURCHASE after step 0")

    def test_a05_negative_no_does_not_trigger_step0(self):
        """W02-A05: AWAITING + 'no' → Step 0 does NOT fire → step 9 re-fires → AWAITING reset."""
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        ctx = _make_ctx(state)
        result = _call_qualifying_intent(self.eng, state, ctx, "no")
        self.assertIsNotNone(result, "Step 9 must return boundary reply for 'no'")
        self.assertEqual(state.last_intent, _AWAITING_QUALIFICATION,
                         "Step 9 must re-set AWAITING_QUALIFICATION")

    def test_a06_fresh_si_without_awaiting_fires_step9(self):
        """W02-A06: state.last_intent==None + 'si' → Step 0 does NOT fire → step 9 fires."""
        state = _make_state(last_intent=None)
        ctx = _make_ctx(state)
        result = _call_qualifying_intent(self.eng, state, ctx, "si")
        # "si" alone without AWAITING context is ambiguous; step 9 fires (returns UNCERTAIN)
        self.assertIsNotNone(result, "Bare 'si' with no AWAITING context → step 9 fires")
        self.assertEqual(state.last_intent, _AWAITING_QUALIFICATION)


# ── W02-L: Linked scenario tests ──────────────────────────────────────────────

class TestW02LLinkedScenarios(unittest.TestCase):
    """W02-L: Linked scenarios combining contextual lookup + year guard."""

    def test_l01_contextual_2008_lookup_and_year_guard(self):
        """W02-L01: 'si, un 2008 en berazategui' → Peugeot 2008; year not poisoned."""
        match = _contextual_numeric_model_lookup("si, un 2008 en berazategui")
        self.assertIsNotNone(match)
        self.assertEqual(match.marca, "Peugeot")
        self.assertEqual(str(match.modelo), "2008")
        # Year extraction with model excluded → None (no real year in text)
        year = _extract_year_from_text("si, un 2008 en berazategui", exclude_token="2008")
        self.assertIsNone(year, "2008 is the model; no real year in 'si, un 2008 en berazategui'")

    def test_l02_year_still_extracted_when_different(self):
        """W02-L02: 'peugeot 2008 2020' with exclude_token='2008' → 2020."""
        year = _extract_year_from_text("peugeot 2008 2020", exclude_token="2008")
        self.assertEqual(year, 2020, "2020 is a real year and must survive exclusion of 2008")


# ── W02-D: Duplicate-opening prevention ──────────────────────────────────────

class TestW02DDuplicateOpeningPrevention(unittest.TestCase):
    """W02-D: Prove the duplicate opening message never fires after an affirmative."""

    def setUp(self):
        self.eng = _make_engine()

    def test_d01_no_duplicate_after_affirmative(self):
        """W02-D01: AWAITING_QUALIFICATION + affirmative → _handle_qualifying_intent returns None.

        Proves the UNCERTAIN_SERVICE_REPLY is NOT sent again on the affirmative turn.
        """
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        ctx = _make_ctx(state)
        result = _call_qualifying_intent(self.eng, state, ctx, "si")
        self.assertIsNone(result,
            "Must return None — not the UNCERTAIN_SERVICE_REPLY — after affirmative")

    def test_d02_generic_vehicle_word_blocked_and_backward_compat(self):
        """W02-D02: Generic words block contextual; _extract_year_from_text backward compatible."""
        # Generic vehicle words block contextual lookup
        self.assertIsNone(_contextual_numeric_model_lookup("coche 2008"))
        self.assertIsNone(_contextual_numeric_model_lookup("vehiculo 2008"))

        # _extract_year_from_text with no exclude_token works as original
        self.assertEqual(_extract_year_from_text("Focus 2008"), 2008)
        self.assertIsNone(_extract_year_from_text("no year here"))
        self.assertEqual(_extract_year_from_text("2019 test"), 2019)

    def test_d03_awaiting_qualification_constant_fits_db_column(self):
        """W02-D03: _AWAITING_QUALIFICATION fits String(30) DB column."""
        self.assertLessEqual(
            len(_AWAITING_QUALIFICATION), 30,
            f"_AWAITING_QUALIFICATION is {len(_AWAITING_QUALIFICATION)} chars; must be ≤30"
        )
        self.assertLessEqual(
            len(_INTENT_PREPURCHASE), 30,
            f"_INTENT_PREPURCHASE is {len(_INTENT_PREPURCHASE)} chars; must be ≤30"
        )


# ── W02-O: Owner rule reconciliation tests ───────────────────────────────────

_P2008_CLARIFICATION = _FUZZY_CONFIRMATION_TEMPLATE.format(marca="Peugeot", modelo="2008")


def _make_full_engine():
    """CE with real vehicle lookup + full _process_text path mocked for routing."""
    eng = _make_engine()
    eng._routing_gate = MagicMock(return_value=(None, True))
    eng._check_fallback_flow_triggers = MagicMock(return_value=None)
    eng._focus_candidate = MagicMock(return_value=None)
    eng._enforce_catalog_vehicle = MagicMock()
    eng._handle_period_request = MagicMock(return_value=None)
    eng.settings.whatsapp_flow_id = ""
    return eng


def _sent_texts(eng):
    """Return list of text strings passed to _send_text_to_wa."""
    return [c[0][1] for c in eng._send_text_to_wa.call_args_list]


class TestW02OOwnerRules(unittest.TestCase):
    """W02-O: Owner numeric-model interpretation rules (Rule A/B/C/D)."""

    def setUp(self):
        self.eng = _make_full_engine()

    # ── Rule A: another recognized vehicle present → 2008 is year ─────────

    def test_o01_focus_2008_is_ford_focus_year_2008(self):
        """W02-O01: 'Focus 2008' → Ford Focus / year 2008 (Rule A)."""
        state = _make_state()
        ctx = _make_ctx(state)
        event = _make_event("Focus 2008")
        self.eng._process_text(ctx, event)
        self.assertEqual(len(ctx.candidates), 1, "Ford Focus candidate must be created")
        cand = ctx.candidates[0]
        self.assertEqual(cand.marca, "Ford", f"marca={cand.marca!r}")
        self.assertEqual(cand.anio, 2008, f"year must be 2008; got {cand.anio}")
        for txt in _sent_texts(self.eng):
            self.assertNotIn("Peugeot 2008", txt, "P2008 clarification must not fire for Focus 2008")

    def test_o02_gol_2008_is_vw_gol_year_2008(self):
        """W02-O02: 'Gol 2008' → VW Gol / year 2008 (Rule A)."""
        state = _make_state()
        ctx = _make_ctx(state)
        event = _make_event("Gol 2008")
        self.eng._process_text(ctx, event)
        self.assertEqual(len(ctx.candidates), 1)
        cand = ctx.candidates[0]
        self.assertEqual(cand.marca, "Volkswagen", f"marca={cand.marca!r}")
        self.assertEqual(cand.anio, 2008, f"year={cand.anio}")
        for txt in _sent_texts(self.eng):
            self.assertNotIn("Peugeot 2008", txt)

    def test_o03_corolla_2008_is_toyota_corolla_year_2008(self):
        """W02-O03: 'Toyota Corolla 2008' → Toyota Corolla / year 2008 (Rule A)."""
        state = _make_state()
        ctx = _make_ctx(state)
        event = _make_event("Toyota Corolla 2008")
        self.eng._process_text(ctx, event)
        self.assertEqual(len(ctx.candidates), 1)
        cand = ctx.candidates[0]
        self.assertEqual(cand.marca, "Toyota", f"marca={cand.marca!r}")
        self.assertEqual(cand.anio, 2008, f"year={cand.anio}")
        for txt in _sent_texts(self.eng):
            self.assertNotIn("Peugeot 2008", txt)

    # ── Rule C: explicit Peugeot 2008 ─────────────────────────────────────

    def test_o04_peugeot_2008_explicit_year_null(self):
        """W02-O04: 'Peugeot 2008' → Peugeot 2008 / year=NULL (Rule C)."""
        state = _make_state()
        ctx = _make_ctx(state)
        event = _make_event("Peugeot 2008")
        self.eng._process_text(ctx, event)
        self.assertEqual(len(ctx.candidates), 1)
        cand = ctx.candidates[0]
        self.assertEqual(cand.marca, "Peugeot", f"marca={cand.marca!r}")
        self.assertIsNone(cand.anio, f"model token must not be interpreted as year; got {cand.anio}")

    def test_o05_peugeot_2008_with_year_2020(self):
        """W02-O05: 'Peugeot 2008 2020' → Peugeot 2008 / year=2020 (Rule C)."""
        state = _make_state()
        ctx = _make_ctx(state)
        event = _make_event("Peugeot 2008 2020")
        self.eng._process_text(ctx, event)
        self.assertEqual(len(ctx.candidates), 1)
        cand = ctx.candidates[0]
        self.assertEqual(cand.marca, "Peugeot", f"marca={cand.marca!r}")
        self.assertEqual(cand.anio, 2020, f"real year 2020 must survive; got {cand.anio}")

    # ── Rule B: no other vehicle → ask clarification, not silent creation ──

    def test_o06_auto_2008_generic_word_blocks(self):
        """W02-O06: 'un auto 2008' → do not create Peugeot 2008 (generic word)."""
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        ctx = _make_ctx(state)
        event = _make_event("un auto 2008")
        self.eng._process_text(ctx, event)
        self.assertEqual(len(ctx.candidates), 0, "Generic word 'auto' must block P2008 creation")
        for txt in _sent_texts(self.eng):
            self.assertNotIn("Peugeot 2008", txt, "P2008 clarification must not fire")

    def test_o07_standalone_2008_no_inspection_context(self):
        """W02-O07: Standalone '2008' with no inspection context → no silent P2008 creation."""
        state = _make_state(last_intent=None, last_stage="QUALIFYING")
        ctx = _make_ctx(state)
        event = _make_event("2008")
        self.eng._process_text(ctx, event)
        self.assertEqual(len(ctx.candidates), 0,
                         "No inspection context → no candidate created silently")

    def test_o08_si_un_2008_en_berazategui_after_awaiting(self):
        """W02-O08: 'Sí, un 2008 en Berazategui' after AWAITING_QUALIFICATION → P2008 clarification."""
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        ctx = _make_ctx(state)
        event = _make_event("Sí, un 2008 en Berazategui")
        result = self.eng._process_text(ctx, event)
        self.assertEqual(result.action, "replied", f"action={result.action!r}")
        texts = _sent_texts(self.eng)
        self.assertTrue(any("Peugeot 2008" in t for t in texts),
                        f"P2008 clarification must be sent; sent={texts}")
        self.assertEqual(state.pending_fuzzy_catalog_key, "Peugeot||2008",
                         "pending_fuzzy_catalog_key must be set")
        self.assertIsNotNone(state.pending_turn_evidence_text)
        self.assertEqual(len(ctx.candidates), 0, "No candidate created before confirmation")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE,
                         "Intent must be upgraded to PREPURCHASE when clarification sent")

    def test_o09_quiero_revisar_2008_en_berazategui_fresh(self):
        """W02-O09: 'Quiero revisar un 2008 en Berazategui' (fresh) → P2008 clarification."""
        state = _make_state(last_intent=None, last_stage="QUALIFYING")
        ctx = _make_ctx(state)
        event = _make_event("Quiero revisar un 2008 en Berazategui")
        result = self.eng._process_text(ctx, event)
        self.assertEqual(result.action, "replied")
        texts = _sent_texts(self.eng)
        self.assertTrue(any("Peugeot 2008" in t for t in texts),
                        f"P2008 clarification must be sent; sent={texts}")
        self.assertEqual(state.pending_fuzzy_catalog_key, "Peugeot||2008")
        self.assertEqual(len(ctx.candidates), 0)
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)

    def test_o10_un_2008_en_berazategui_after_awaiting(self):
        """W02-O10: 'Un 2008 en Berazategui' after AWAITING_QUALIFICATION → P2008 clarification."""
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        ctx = _make_ctx(state)
        event = _make_event("Un 2008 en Berazategui")
        result = self.eng._process_text(ctx, event)
        self.assertEqual(result.action, "replied")
        self.assertEqual(state.pending_fuzzy_catalog_key, "Peugeot||2008")
        self.assertEqual(len(ctx.candidates), 0)

    def test_o11_burst_focus_2008_berazategui(self):
        """W02-O11: Burst Focus + 2008 + Berazategui → Ford Focus / year 2008 (Rule A)."""
        state = _make_state()
        ctx = _make_ctx(state)
        event = _make_event(
            "Berazategui",
            unanswered=["Focus", "2008", "Berazategui"],
            recent=["Focus", "2008", "Berazategui"],
        )
        self.eng._process_text(ctx, event)
        self.assertEqual(len(ctx.candidates), 1)
        cand = ctx.candidates[0]
        self.assertEqual(cand.marca, "Ford", f"marca={cand.marca!r}")
        self.assertEqual(cand.anio, 2008, f"year={cand.anio}")
        for txt in _sent_texts(self.eng):
            self.assertNotIn("Peugeot 2008", txt)

    def test_o12_burst_si_un_2008_berazategui_after_awaiting(self):
        """W02-O12: Burst Sí + un 2008 + Berazategui after AWAITING → P2008 clarification."""
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        ctx = _make_ctx(state)
        event = _make_event(
            "Berazategui",
            unanswered=["Sí", "un 2008", "Berazategui"],
            recent=["Sí", "un 2008", "Berazategui"],
        )
        result = self.eng._process_text(ctx, event)
        self.assertEqual(result.action, "replied")
        texts = _sent_texts(self.eng)
        self.assertTrue(any("Peugeot 2008" in t for t in texts),
                        f"P2008 clarification must be sent; sent={texts}")
        self.assertEqual(state.pending_fuzzy_catalog_key, "Peugeot||2008")
        self.assertEqual(len(ctx.candidates), 0)

    # ── Rule D: confirmation after clarification ───────────────────────────

    def test_o13_after_p2008_clarification_si_confirms(self):
        """W02-O13: 'Sí' after P2008 clarification → Peugeot 2008 / year NULL / Berazategui stored."""
        state = _make_state(
            last_intent=_INTENT_PREPURCHASE,
            pending_fuzzy_catalog_key="Peugeot||2008",
            pending_turn_evidence_text="Sí, un 2008 en Berazategui",
        )
        ctx = _make_ctx(state)
        event = _make_event("Sí")
        result = self.eng._process_text(ctx, event)
        self.assertIn(result.action, ("replied",), f"action={result.action!r}")
        self.assertEqual(len(ctx.candidates), 1, "Peugeot 2008 candidate must be created")
        cand = ctx.candidates[0]
        self.assertEqual(cand.marca, "Peugeot", f"marca={cand.marca!r}")
        self.assertIsNone(cand.anio,
                          f"model number '2008' must not become year; got {cand.anio}")
        self.assertIsNone(state.pending_fuzzy_catalog_key,
                          "pending key must be cleared after confirmation")
        for txt in _sent_texts(self.eng):
            self.assertNotIn(_P2008_CLARIFICATION, txt,
                             "P2008 clarification must not re-fire on confirmation turn")


if __name__ == "__main__":
    unittest.main()
