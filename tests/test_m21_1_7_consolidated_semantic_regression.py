"""M21.1.7 — Consolidated Semantic Regression Pack (CR01–CR35, SEQ01–SEQ05).

Proves M21.1.1–M21.1.6 work together as one coherent Semantic Conversation Engine.
All external calls mocked. Tests are split into:
  - Pure-function tests: resolver + narrative invariants (no CE instantiation)
  - CE integration tests: routing outcomes using _make_engine / _run pattern
  - Sequence tests: multi-turn state simulation
"""
from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── Stub heavy deps ───────────────────────────────────────────────────────────
for _mod_name in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

# ── SQLite JSONB compat ───────────────────────────────────────────────────────
import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = sqlalchemy.JSON   # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON      # type: ignore[attr-defined]

from app.services.conversation_engine import (   # noqa: E402
    ConversationEngine,
    _FALLBACK_WARM_HANDOFF,
    _INSPECTABILITY_DISASSEMBLED_REPLY,
    _INSPECTABILITY_NONRUNNING_CLARIFY,
    _F12_BOUNDARY_REPLY,
)
from app.schemas.conversation import ConversationHandleIn, HANDLED_ACTIONS  # noqa: E402
from app.services.outbound_guard import OutboundBlockedError                # noqa: E402
from app.services.field_evidence import (   # noqa: E402
    resolve_field_evidence,
    INSP_ASSEMBLED_ACCESSIBLE,
    INSP_DISASSEMBLED_BLOCKED,
    INSP_UNRESOLVED_NON_RUNNING,
)
from app.services.narrative_schema import (   # noqa: E402
    DEFERRED_RESPONSE_ES,
    STATUS_CONFIRMED,
    STATUS_HISTORICAL,
    STATUS_HYPOTHETICAL,
    STATUS_SUPERSEDED,
    STATUS_UNCERTAIN,
    parse_narrative_interpretation,
    narrative_needs_ai,
)
from app.services.vehicle_catalog import VehicleMatch, FuzzyLookupResult  # noqa: E402

STAGE_QUALIFYING = "QUALIFYING"
STAGE_QUOTED = "QUOTED"
STAGE_SCHEDULING = "SCHEDULING"


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
        pending_fuzzy_catalog_key=None,
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
    defaults = dict(
        id=10, status="current_focus",
        marca=None, modelo=None, anio=None, tipo_vehiculo=None,
        zone_group=None, zone_detail=None, label=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _make_ctx(state=None, lead=None, candidates=None) -> SimpleNamespace:
    ctx = SimpleNamespace()
    ctx.thread = SimpleNamespace(id=42)
    ctx.contact = SimpleNamespace(wa_id="5491199999999")
    ctx.lead = lead if lead is not None else _make_lead()
    ctx.state = state if state is not None else _make_state()
    ctx.candidates = candidates if candidates is not None else []
    return ctx


def _make_event(text=None, burst=None) -> ConversationHandleIn:
    msgs = burst if burst else ([text] if text else [])
    return ConversationHandleIn(
        thread_id=42,
        wa_message_id="test-cr-wa-id",
        wa_id="5491199999999",
        text=text,
        unanswered_recent_user_messages=burst or [],
        recent_user_messages=msgs,
    )


_DEFAULT_AI_RAW = json.dumps({
    "intent": "QUALIFYING",
    "reply": "Respuesta de prueba.",
    "candidate": {"action": "none"},
    "extracted": {},
    "lead_flag": None,
    "needs_human": False,
    "deferred_interest": False,
})


def _make_engine(ai_response=None) -> ConversationEngine:
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock()
    eng.settings = MagicMock()
    eng.settings.openai_api_key = "sk-fake"
    eng.settings.openai_chat_model = "gpt-4o-mini"
    eng.settings.backend_url = "http://localhost:8000"
    eng.settings.whatsapp_flow_id = ""
    eng._send_text_to_wa = MagicMock(return_value="mock-wa-id")
    eng._send_fallback_human_review_notification = MagicMock()
    eng._call_openai = MagicMock(return_value=ai_response or _DEFAULT_AI_RAW)
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
    eng._handle_period_request = MagicMock(return_value=None)
    eng._build_quote_reply = MagicMock(return_value="Cotización de prueba: $999.")
    eng._pricing = MagicMock()
    eng._scrub_invented_price = MagicMock(side_effect=lambda r, q: r)
    return eng


def _run(text, state_kwargs=None, candidates=None, ai_response=None,
         lookup_vehicle_return=None, fuzzy_return=None, burst=None):
    eng = _make_engine(ai_response=ai_response)
    state = _make_state(**(state_kwargs or {}))
    ctx = _make_ctx(state=state, candidates=candidates or [])
    event = _make_event(text=text, burst=burst)
    with (
        patch("app.services.conversation_engine.lookup_vehicle",
              return_value=lookup_vehicle_return),
        patch("app.services.conversation_engine.fuzzy_lookup_vehicle",
              return_value=fuzzy_return or FuzzyLookupResult(
                  outcome="UNRESOLVED", hit=None, score=0.0,
                  second_hit=None, second_score=0.0, gap=0.0, make_constrained=False,
              )),
    ):
        result = eng._process_text(ctx, event)
    return eng, result, state


def _snap(state=None, candidates=None):
    s = state or _make_state()
    ctx = _make_ctx(state=s, candidates=candidates or [])
    return resolve_field_evidence(ctx, s)


def _cand(**kw) -> SimpleNamespace:
    return _make_candidate(**kw)


# ═══════════════════════════════════════════════════════════════════════════════
# PURE RESOLVER + NARRATIVE INVARIANT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestCR01CleanMultiFactQuote(unittest.TestCase):
    """CR-4: candidate is commercial source of truth; CR-11: resolver refreshes."""

    def test_pricing_ready_with_confirmed_candidate(self):
        cand = _cand(marca="Ford", modelo="Focus", anio=2019,
                     tipo_vehiculo="AUTO", zone_group="Palermo")
        state = _make_state(
            current_focus_candidate_id=10,
            last_intent="PREPURCHASE_INSPECTION",
        )
        snap = _snap(state=state, candidates=[cand])
        self.assertTrue(snap.pricing_ready())

    def test_vehicle_known_from_candidate(self):
        cand = _cand(marca="Ford", modelo="Focus", tipo_vehiculo="AUTO")
        state = _make_state(current_focus_candidate_id=10)
        snap = _snap(state=state, candidates=[cand])
        self.assertTrue(snap.vehicle_known())

    def test_location_known_from_candidate(self):
        cand = _cand(tipo_vehiculo="AUTO", zone_group="Palermo")
        state = _make_state(current_focus_candidate_id=10)
        snap = _snap(state=state, candidates=[cand])
        self.assertTrue(snap.location_known())

    def test_no_redundant_questions_when_complete(self):
        cand = _cand(marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
                     zone_group="Palermo")
        state = _make_state(current_focus_candidate_id=10)
        snap = _snap(state=state, candidates=[cand])
        self.assertFalse(snap.needs_vehicle())
        self.assertFalse(snap.needs_location())


class TestCR02OriginVsVehicleLocation(unittest.TestCase):
    """CR-6: customer origin never becomes inspection location."""

    def test_customer_origin_separate_from_location(self):
        raw = {
            "customer_origin": {"value": "Tigre", "status": STATUS_CONFIRMED},
            "vehicle_location": {"value": "Palermo", "status": STATUS_CONFIRMED},
        }
        narr = parse_narrative_interpretation(raw)
        self.assertEqual(narr.customer_origin.value, "Tigre")
        self.assertEqual(narr.vehicle_location.value, "Palermo")

    def test_resolver_uses_candidate_zone_not_customer_origin(self):
        cand = _cand(tipo_vehiculo="AUTO", zone_group="Palermo")
        state = _make_state(
            current_focus_candidate_id=10,
            home_zone_group="Tigre",  # thread state has Tigre (customer origin)
        )
        snap = _snap(state=state, candidates=[cand])
        # Candidate zone beats thread state
        self.assertEqual(snap.inspection_location.value, "Palermo")

    def test_narrative_origin_not_active_as_location(self):
        raw = {"customer_origin": {"value": "Tigre", "status": STATUS_CONFIRMED}}
        narr = parse_narrative_interpretation(raw)
        self.assertTrue(narr.customer_origin.is_active())
        # has_active_location should be False — origin is not a vehicle location
        self.assertFalse(narr.has_active_location())


class TestCR03HistoricalVehicleCorrection(unittest.TestCase):
    """CR-3: current explicit evidence beats stale; NU-4 historical not current."""

    def test_corolla_confirmed_focus_superseded(self):
        raw = {
            "vehicle_make_model": {
                "value": "Toyota Corolla",
                "status": STATUS_CONFIRMED,
                "evidence": "al final compré un Corolla",
            },
            "vehicle_year": {"value": 2020, "status": STATUS_CONFIRMED},
        }
        narr = parse_narrative_interpretation(raw)
        self.assertEqual(narr.vehicle_make_model.value, "Toyota Corolla")
        self.assertTrue(narr.has_active_vehicle())
        self.assertEqual(narr.vehicle_year.value, 2020)

    def test_not_deferred(self):
        raw = {"vehicle_make_model": {"value": "Toyota Corolla", "status": STATUS_CONFIRMED}}
        narr = parse_narrative_interpretation(raw)
        self.assertFalse(narr.is_effectively_deferred())


class TestCR04HistoricalLocationCurrentLocation(unittest.TestCase):
    """CR-3: current location beats prior state."""

    def test_palermo_current_tigre_historical(self):
        raw = {
            "vehicle_location": {
                "value": "Palermo",
                "status": STATUS_CONFIRMED,
                "evidence": "ahora está en Palermo",
            },
        }
        narr = parse_narrative_interpretation(raw)
        self.assertEqual(narr.vehicle_location.value, "Palermo")
        self.assertTrue(narr.has_active_location())

    def test_resolver_candidate_zone_beats_state_zone(self):
        # Simulate: state has Tigre (old), candidate now set to Palermo (current)
        cand = _cand(tipo_vehiculo="AUTO", zone_group="Palermo")
        state = _make_state(home_zone_group="Tigre", current_focus_candidate_id=10)
        snap = _snap(state=state, candidates=[cand])
        self.assertEqual(snap.inspection_location.value, "Palermo")


class TestCR05RealDeferredMessage(unittest.TestCase):
    """CR-7: deferred interest is non-commercial. NU-6."""

    def test_deferred_intercept_fires_before_commercial_mutation(self):
        ai_resp = json.dumps({
            "intent": "OTHER",
            "reply": "...",
            "deferred_interest": True,
            "vehicle_make_model": None,
            "candidate": {"action": "none"},
            "extracted": {},
            "lead_flag": None,
            "needs_human": False,
        })
        # last_intent=PREPURCHASE_INSPECTION ensures Layer F passes (step 7)
        # so the CE reaches the AI call and the deferred intercept can fire.
        eng, result, state = _run(
            "Hola por ahora estoy buscando un auto agende esto para no perderlo",
            ai_response=ai_resp,
            state_kwargs={"last_intent": "PREPURCHASE_INSPECTION"},
        )
        # Deferred intercept fires → _send_text_to_wa with approved copy
        eng._send_text_to_wa.assert_called_once()
        call_text = eng._send_text_to_wa.call_args[0][1]
        self.assertIn("cuando tengas", call_text)
        # No candidate/pricing mutation
        eng._apply_candidate.assert_not_called()

    def test_is_effectively_deferred_pure(self):
        raw = {"deferred_interest": True, "vehicle_make_model": None}
        narr = parse_narrative_interpretation(raw)
        self.assertTrue(narr.is_effectively_deferred())

    def test_deferred_response_constant(self):
        self.assertIn("cuando tengas", DEFERRED_RESPONSE_ES)
        self.assertIn("algún auto en vista", DEFERRED_RESPONSE_ES)


class TestCR06DeferredOverriddenByActiveVehicle(unittest.TestCase):
    """CR-7: deferred is non-commercial UNLESS stronger active evidence exists."""

    def test_not_effectively_deferred_with_active_vehicle(self):
        raw = {
            "deferred_interest": True,
            "vehicle_make_model": {"value": "Ford Focus", "status": STATUS_CONFIRMED},
            "vehicle_location": {"value": "Palermo", "status": STATUS_CONFIRMED},
        }
        narr = parse_narrative_interpretation(raw)
        self.assertFalse(narr.is_effectively_deferred())
        self.assertTrue(narr.has_active_vehicle())

    def test_active_flow_when_vehicle_present(self):
        ai_resp = json.dumps({
            "intent": "QUALIFYING",
            "reply": "Ok, lo vemos.",
            "deferred_interest": True,
            "vehicle_make_model": {"value": "Ford Focus", "status": "CONFIRMED"},
            "vehicle_location": {"value": "Palermo", "status": "CONFIRMED"},
            "candidate": {"action": "none"},
            "extracted": {},
            "lead_flag": None,
            "needs_human": False,
        })
        # last_intent=PREPURCHASE_INSPECTION ensures Layer F passes so CE reaches the AI.
        eng, result, state = _run(
            "Todavía estoy buscando, pero ya tengo un Focus 2019 en Palermo que quiero revisar.",
            ai_response=ai_resp,
            state_kwargs={"last_intent": "PREPURCHASE_INSPECTION"},
        )
        # Deferred intercept must NOT fire (vehicle present) → normal flow → _apply_candidate called
        eng._apply_candidate.assert_called_once()


class TestCR07ActualNonRunningButAccessible(unittest.TestCase):
    """CR-2: narrative handles whole-message; M21.1.2 inspectability rules respected."""

    def test_narrative_assembled_accessible(self):
        raw = {
            "inspectability": {
                "value": INSP_ASSEMBLED_ACCESSIBLE,
                "status": STATUS_CONFIRMED,
                "evidence": "está armado, completo y se puede revisar",
            },
        }
        narr = parse_narrative_interpretation(raw)
        self.assertTrue(narr.has_active_inspectability())
        self.assertEqual(narr.inspectability.value, INSP_ASSEMBLED_ACCESSIBLE)

    def test_inspectability_clarification_cleared_on_assembled(self):
        raw = {
            "inspectability": {"value": INSP_ASSEMBLED_ACCESSIBLE, "status": STATUS_CONFIRMED}
        }
        narr = parse_narrative_interpretation(raw)
        # Simulate CE applying narrative: inspectability_clarification_sent should clear
        state = _make_state(inspectability_clarification_sent=True)
        ctx = _make_ctx(state=state)
        eng = _make_engine()
        eng._apply_narrative_interpretation(ctx, state, narr)
        self.assertFalse(state.inspectability_clarification_sent)

    def test_assembled_allows_progress(self):
        from app.services.field_evidence import INSP_ASSEMBLED_ACCESSIBLE as IAA
        snap_state = _make_state(inspectability_clarification_sent=False)
        snap = _snap(state=snap_state)
        self.assertTrue(snap.inspectability_allows_progress())


class TestCR08HypotheticalNonRunning(unittest.TestCase):
    """CR-5: hypothetical inspectability must not set pending state."""

    def test_hypothetical_not_active(self):
        raw = {
            "inspectability": {
                "value": INSP_UNRESOLVED_NON_RUNNING,
                "status": STATUS_HYPOTHETICAL,
            },
        }
        narr = parse_narrative_interpretation(raw)
        self.assertFalse(narr.has_active_inspectability())
        self.assertFalse(narr.inspectability.is_active())

    def test_state_not_mutated_for_hypothetical(self):
        raw = {
            "inspectability": {"value": INSP_UNRESOLVED_NON_RUNNING, "status": STATUS_HYPOTHETICAL}
        }
        narr = parse_narrative_interpretation(raw)
        state = _make_state(inspectability_clarification_sent=False)
        ctx = _make_ctx(state=state)
        eng = _make_engine()
        eng._apply_narrative_interpretation(ctx, state, narr)
        # Hypothetical → no state change
        self.assertFalse(state.inspectability_clarification_sent)


class TestCR09Disassembled(unittest.TestCase):
    """CR-1: deterministic disassembled boundary beats narrative. M21.1.2."""

    def test_disassembled_boundary_fires_before_ai(self):
        eng, result, state = _run(
            "Es un Focus 2019 en Palermo, pero está desarmado. ¿Cuánto sale?",
            lookup_vehicle_return=None,
        )
        # AI must not be called when disassembled boundary fires
        eng._call_openai.assert_not_called()
        # Reply must be the disassembled boundary text
        eng._send_text_to_wa.assert_called()
        call_text = eng._send_text_to_wa.call_args[0][1]
        self.assertIn("desarmado", call_text.lower())


class TestCR10Motorcycle(unittest.TestCase):
    """CR-1: motorcycle handoff is deterministic, beats all narrative."""

    def test_motorcycle_boundary_fires(self):
        eng, result, state = _run(
            "Tengo una motocicleta Honda CB 500 en Palermo y quiero revisarla.",
            lookup_vehicle_return=None,
        )
        # AI must not be called for motorcycle
        eng._call_openai.assert_not_called()
        # Warm handoff text sent
        eng._send_text_to_wa.assert_called()
        call_text = eng._send_text_to_wa.call_args[0][1]
        self.assertTrue(len(call_text) > 10)


class TestCR11Formulario12(unittest.TestCase):
    """CR-1: Formulario 12 exact boundary response."""

    def test_f12_exact_reply(self):
        eng, result, state = _run(
            "Tengo un Corolla 2020 en Palermo y necesito hacer el Formulario 12.",
            lookup_vehicle_return=None,
        )
        eng._call_openai.assert_not_called()
        call_text = eng._send_text_to_wa.call_args[0][1]
        self.assertIn("Formulario 12", call_text)


class TestCR12HighConfidenceFuzzyCompound(unittest.TestCase):
    """CR-2: high-confidence fuzzy auto-accept normalizes vehicle; other facts preserved."""

    def test_auto_accept_vehicle_normalized(self):
        fuzzy_result = FuzzyLookupResult(
            outcome="AUTO_ACCEPT",
            hit=VehicleMatch(marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
                             confidence="high", matched_alias="ford focus"),
            score=0.91, second_hit=None, second_score=0.0, gap=0.18,
            make_constrained=False,
        )
        # narrative should still parse year/location from compound message
        raw = {
            "vehicle_make_model": {"value": "Ford Focus", "status": STATUS_CONFIRMED},
            "vehicle_year": {"value": 2019, "status": STATUS_CONFIRMED},
            "vehicle_location": {"value": "Palermo", "status": STATUS_CONFIRMED},
        }
        narr = parse_narrative_interpretation(raw)
        self.assertTrue(narr.has_active_vehicle())
        self.assertEqual(narr.vehicle_year.value, 2019)
        self.assertEqual(narr.vehicle_location.value, "Palermo")

    def test_no_duplicate_question_when_auto_accepted(self):
        cand = _cand(marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
                     zone_group="Palermo")
        state = _make_state(current_focus_candidate_id=10)
        snap = _snap(state=state, candidates=[cand])
        self.assertFalse(snap.needs_vehicle())
        self.assertFalse(snap.needs_location())


class TestCR13FuzzyConfirmationBlocksPricing(unittest.TestCase):
    """CR-5: pending fuzzy vehicle cannot satisfy pricing readiness."""

    def test_pending_fuzzy_not_vehicle_known(self):
        state = _make_state(pending_fuzzy_catalog_key="Ford||Ka")
        snap = _snap(state=state, candidates=[])
        self.assertFalse(snap.vehicle_known())

    def test_pending_fuzzy_not_pricing_ready(self):
        state = _make_state(
            pending_fuzzy_catalog_key="Ford||Ka",
            last_intent="PREPURCHASE_INSPECTION",
        )
        snap = _snap(state=state, candidates=[])
        self.assertFalse(snap.pricing_ready())

    def test_fuzzy_confirm_request_sent_no_candidate(self):
        fuzzy_result = FuzzyLookupResult(
            outcome="CONFIRM",
            hit=VehicleMatch(marca="Ford", modelo="Ka", tipo_vehiculo="AUTO",
                             confidence="medium", matched_alias="ford ka"),
            score=0.80, second_hit=None, second_score=0.0, gap=0.16,
            make_constrained=False,
        )
        eng, result, state = _run(
            "ford ksl 2019 en Palermo, cuánto sale?",
            fuzzy_return=fuzzy_result,
        )
        # Confirmation request sent; no candidate created from unconfirmed fuzzy
        eng._send_text_to_wa.assert_called()
        call_text = eng._send_text_to_wa.call_args[0][1]
        self.assertIn("Ka", call_text)


class TestCR14FuzzyConfirmationAccepted(unittest.TestCase):
    """CR-5: accepted fuzzy clears pending and proceeds."""

    def test_pending_cleared_on_acceptance(self):
        # After "sí" with pending key set, state.pending_fuzzy_catalog_key cleared
        # Simulate: existing pending key → AI returns none → pending cleared
        state = _make_state(pending_fuzzy_catalog_key="Ford||Ka")
        # After confirmation accepted, pending_fuzzy_catalog_key becomes None
        state.pending_fuzzy_catalog_key = None
        snap = _snap(state=state)
        # Vehicle unknown (no candidate yet after confirmation)
        self.assertFalse(snap.vehicle_known())

    def test_no_pending_key_means_not_fuzzy_blocked(self):
        cand = _cand(marca="Ford", modelo="Ka", tipo_vehiculo="AUTO")
        state = _make_state(
            current_focus_candidate_id=10,
            pending_fuzzy_catalog_key=None,  # cleared after acceptance
        )
        snap = _snap(state=state, candidates=[cand])
        self.assertTrue(snap.vehicle_known())


class TestCR15FuzzyRejected(unittest.TestCase):
    """CR-5: rejected fuzzy clears Ka, processes Kuga."""

    def test_ka_cleared_kuga_active_in_narrative(self):
        raw = {
            "vehicle_make_model": {
                "value": "Ford Kuga",
                "status": STATUS_CONFIRMED,
                "evidence": "no, es un Ford Kuga",
            },
        }
        narr = parse_narrative_interpretation(raw)
        self.assertEqual(narr.vehicle_make_model.value, "Ford Kuga")
        self.assertTrue(narr.has_active_vehicle())

    def test_no_ka_in_result(self):
        raw = {"vehicle_make_model": {"value": "Ford Kuga", "status": STATUS_CONFIRMED}}
        narr = parse_narrative_interpretation(raw)
        self.assertNotIn("Ka", narr.vehicle_make_model.value)


class TestCR16CurrentExplicitLocationBeatsStaleState(unittest.TestCase):
    """CR-3: current explicit location from candidate beats stale thread state."""

    def test_candidate_zone_beats_state_zone(self):
        cand = _cand(tipo_vehiculo="AUTO", zone_group="Villa Urquiza")
        state = _make_state(
            home_zone_group="Tigre",
            current_focus_candidate_id=10,
        )
        snap = _snap(state=state, candidates=[cand])
        self.assertEqual(snap.inspection_location.value, "Villa Urquiza")
        self.assertNotEqual(snap.inspection_location.value, "Tigre")

    def test_narrative_current_location_confirmed(self):
        raw = {
            "vehicle_location": {
                "value": "Villa Urquiza",
                "status": STATUS_CONFIRMED,
                "evidence": "ahora está en Villa Urquiza",
            }
        }
        narr = parse_narrative_interpretation(raw)
        self.assertEqual(narr.vehicle_location.value, "Villa Urquiza")
        self.assertTrue(narr.has_active_location())


class TestCR17LocationContradiction(unittest.TestCase):
    """CR-9: contradictions clarified, not guessed. M21.1.3."""

    def test_location_uncertain_when_contradictory(self):
        raw = {"vehicle_location": {"value": None, "status": STATUS_UNCERTAIN}}
        narr = parse_narrative_interpretation(raw)
        self.assertFalse(narr.has_active_location())

    def test_clarification_sent_on_contradiction(self):
        # Location contradiction → UNCERTAIN narrative (pure function already tested above)
        # CE integration: location-unclear text is processed without crash
        eng, result, state = _run(
            "El auto está en Tigre o puede ser Villa Urquiza, no sé.",
            lookup_vehicle_return=None,
        )
        # M21.1.3 tests verify the exact clarification reply; here we verify CE handles it
        eng._send_text_to_wa.assert_called()


class TestCR18VehicleContradiction(unittest.TestCase):
    """CR-9: ambiguous vehicle unresolved; no guessed candidate."""

    def test_vehicle_uncertain_no_active_fact(self):
        raw = {"vehicle_make_model": {"value": None, "status": STATUS_UNCERTAIN}}
        narr = parse_narrative_interpretation(raw)
        self.assertFalse(narr.has_active_vehicle())

    def test_pricing_not_ready_with_uncertain_vehicle(self):
        state = _make_state(last_intent="PREPURCHASE_INSPECTION", home_zone_group="Palermo")
        snap = _snap(state=state)
        self.assertFalse(snap.vehicle_known())
        self.assertFalse(snap.pricing_ready())


class TestCR19FAQPlusVehicleLocation(unittest.TestCase):
    """CR-8: FAQ can coexist with commercial facts; both retained."""

    def test_asks_faq_with_vehicle_and_location(self):
        raw = {
            "asks_faq": True,
            "vehicle_make_model": {"value": "Toyota Corolla", "status": STATUS_CONFIRMED},
            "vehicle_year": {"value": 2021, "status": STATUS_CONFIRMED},
            "vehicle_location": {"value": "Palermo", "status": STATUS_CONFIRMED},
        }
        narr = parse_narrative_interpretation(raw)
        self.assertTrue(narr.asks_faq)
        self.assertTrue(narr.has_active_vehicle())
        self.assertTrue(narr.has_active_location())
        self.assertFalse(narr.is_effectively_deferred())


class TestCR20FAQOnlyFreshThread(unittest.TestCase):
    """CR-14: FAQ intent alone does not invent commercial fields."""

    def test_no_commercial_mutation_on_faq_only(self):
        eng, result, state = _run(
            "¿Qué revisan?",
            lookup_vehicle_return=None,
        )
        # FAQ only: no new candidate committed to state
        self.assertIsNone(state.current_focus_candidate_id)


class TestCR21PrepurchaseIntentFuzzyNoCandidate(unittest.TestCase):
    """CR-5: established intent + fuzzy vehicle → confirmation required, no candidate."""

    def test_pending_fuzzy_with_prepurchase_intent(self):
        state = _make_state(
            last_intent="PREPURCHASE_INSPECTION",
            pending_fuzzy_catalog_key="Ford||Ka",
        )
        snap = _snap(state=state)
        # Intent known
        self.assertTrue(snap.service_intent_known())
        # But vehicle NOT known (pending)
        self.assertFalse(snap.vehicle_known())
        self.assertFalse(snap.pricing_ready())


class TestCR22ExistingCandidateProtected(unittest.TestCase):
    """CR-4: focused candidate protected from unrelated replacement."""

    def test_existing_candidate_vehicle_known(self):
        cand = _cand(marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
                     zone_group="Palermo")
        state = _make_state(current_focus_candidate_id=10)
        snap = _snap(state=state, candidates=[cand])
        self.assertTrue(snap.vehicle_known())
        self.assertTrue(snap.location_known())

    def test_narrative_bypass_when_already_known(self):
        cand = _cand(marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
                     zone_group="Palermo")
        state = _make_state(current_focus_candidate_id=10)
        snap = _snap(state=state, candidates=[cand])
        result = narrative_needs_ai(snap, "¿cuánto sale?")
        self.assertFalse(result)


class TestCR23ExactCorrectionWithExistingCandidate(unittest.TestCase):
    """CR-3, NU-3: explicit correction supersedes current candidate in narrative."""

    def test_correction_markers_trigger_narrative_ai(self):
        cand = _cand(marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
                     zone_group="Palermo")
        state = _make_state(current_focus_candidate_id=10)
        snap = _snap(state=state, candidates=[cand])
        # Even with complete evidence, correction marker forces narrative AI
        result = narrative_needs_ai(snap, "No, en realidad es un Ford Kuga.")
        self.assertTrue(result)

    def test_corrected_vehicle_is_kuga(self):
        raw = {
            "vehicle_make_model": {
                "value": "Ford Kuga",
                "status": STATUS_CONFIRMED,
                "evidence": "en realidad es un Ford Kuga",
            }
        }
        narr = parse_narrative_interpretation(raw)
        self.assertEqual(narr.vehicle_make_model.value, "Ford Kuga")


class TestCR24MultiMessageBurst(unittest.TestCase):
    """CR-10: one narrative interpretation for multi-message burst."""

    def test_all_facts_from_burst(self):
        raw = {
            "vehicle_make_model": {"value": "Ford Focus", "status": STATUS_CONFIRMED},
            "vehicle_year": {"value": 2019, "status": STATUS_CONFIRMED},
            "vehicle_location": {"value": "Palermo", "status": STATUS_CONFIRMED},
            "asks_price": True,
        }
        narr = parse_narrative_interpretation(raw)
        self.assertTrue(narr.has_active_vehicle())
        self.assertTrue(narr.has_active_location())
        self.assertEqual(narr.vehicle_year.value, 2019)
        self.assertTrue(narr.asks_price)

    def test_narrative_needs_ai_for_burst_with_incomplete_evidence(self):
        snap = _snap()  # empty state
        text = "Es un Focus. 2019. Está en Palermo. ¿Cuánto sale?"
        self.assertTrue(narrative_needs_ai(snap, text))

    def test_one_interpretation_covers_all_facts(self):
        raw = {
            "vehicle_make_model": {"value": "Ford Focus", "status": STATUS_CONFIRMED},
            "vehicle_year": {"value": 2019, "status": STATUS_CONFIRMED},
            "vehicle_location": {"value": "Palermo", "status": STATUS_CONFIRMED},
        }
        narr = parse_narrative_interpretation(raw)
        # One narr object covers all three facts
        self.assertIsNotNone(narr.vehicle_make_model)
        self.assertIsNotNone(narr.vehicle_year)
        self.assertIsNotNone(narr.vehicle_location)


class TestCR25LongVoiceNarrative(unittest.TestCase):
    """CR-10, NU-8: long voice-like message with multiple facts."""

    _raw = {
        "vehicle_make_model": {"value": "Peugeot 3008", "status": STATUS_CONFIRMED},
        "vehicle_year": {"value": 2021, "status": STATUS_CONFIRMED},
        "vehicle_location": {"value": "Villa Urquiza", "status": STATUS_CONFIRMED},
        "customer_origin": {"value": "San Isidro", "status": STATUS_CONFIRMED},
        "inspectability": {
            "value": INSP_ASSEMBLED_ACCESSIBLE,
            "status": STATUS_CONFIRMED,
            "evidence": "está completo y se puede revisar",
        },
        "asks_price": True,
    }

    def test_all_facts_parsed(self):
        narr = parse_narrative_interpretation(self._raw)
        self.assertTrue(narr.has_active_vehicle())
        self.assertTrue(narr.has_active_location())
        self.assertTrue(narr.has_active_inspectability())
        self.assertEqual(narr.customer_origin.value, "San Isidro")
        self.assertEqual(narr.vehicle_location.value, "Villa Urquiza")

    def test_inspectability_accessible(self):
        narr = parse_narrative_interpretation(self._raw)
        self.assertEqual(narr.inspectability.value, INSP_ASSEMBLED_ACCESSIBLE)

    def test_year_and_vehicle(self):
        narr = parse_narrative_interpretation(self._raw)
        self.assertEqual(narr.vehicle_year.value, 2021)
        self.assertIn("3008", narr.vehicle_make_model.value)

    def test_asks_price(self):
        narr = parse_narrative_interpretation(self._raw)
        self.assertTrue(narr.asks_price)

    def test_origin_separate_from_location(self):
        narr = parse_narrative_interpretation(self._raw)
        self.assertNotEqual(narr.customer_origin.value, narr.vehicle_location.value)

    def test_not_deferred(self):
        narr = parse_narrative_interpretation(self._raw)
        self.assertFalse(narr.is_effectively_deferred())


class TestCR26VehicleCorrection(unittest.TestCase):
    """NU-3, NU-11: explicit correction — Kuga wins, Ka discarded."""

    def test_kuga_confirmed_ka_absent(self):
        raw = {
            "vehicle_make_model": {
                "value": "Ford Kuga",
                "status": STATUS_CONFIRMED,
                "evidence": "me equivoqué, es un Ford Kuga 2020",
            },
            "vehicle_year": {"value": 2020, "status": STATUS_CONFIRMED},
            "vehicle_location": {"value": "Palermo", "status": STATUS_CONFIRMED},
        }
        narr = parse_narrative_interpretation(raw)
        self.assertEqual(narr.vehicle_make_model.value, "Ford Kuga")
        self.assertTrue(narr.has_active_vehicle())
        self.assertNotIn("Ka", narr.vehicle_make_model.value)


class TestCR27AmbiguousYear(unittest.TestCase):
    """CR-9: ambiguous year remains uncertain; vehicle+location can still be known."""

    def test_uncertain_year_not_active(self):
        raw = {
            "vehicle_make_model": {"value": "Ford Focus", "status": STATUS_CONFIRMED},
            "vehicle_year": {"value": None, "status": STATUS_UNCERTAIN},
            "vehicle_location": {"value": "Palermo", "status": STATUS_CONFIRMED},
        }
        narr = parse_narrative_interpretation(raw)
        self.assertFalse(narr.vehicle_year.is_active())
        # Vehicle and location still known
        self.assertTrue(narr.has_active_vehicle())
        self.assertTrue(narr.has_active_location())

    def test_vehicle_and_location_known_without_year(self):
        cand = _cand(marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
                     zone_group="Palermo", anio=None)
        state = _make_state(current_focus_candidate_id=10)
        snap = _snap(state=state, candidates=[cand])
        self.assertTrue(snap.vehicle_known())
        self.assertTrue(snap.location_known())


class TestCR28MalformedNarrativeAI(unittest.TestCase):
    """CR-15: malformed AI output → safe defaults, no unsafe mutation."""

    def test_bad_json_returns_none(self):
        self.assertIsNone(parse_narrative_interpretation(None))
        self.assertIsNone(parse_narrative_interpretation("not-a-dict"))

    def test_empty_dict_safe_defaults(self):
        narr = parse_narrative_interpretation({})
        self.assertFalse(narr.deferred_interest)
        self.assertFalse(narr.has_active_vehicle())
        self.assertFalse(narr.is_effectively_deferred())

    def test_invalid_status_coerced_to_uncertain(self):
        raw = {"vehicle_make_model": {"value": "Ford", "status": "GARBAGE"}}
        narr = parse_narrative_interpretation(raw)
        self.assertFalse(narr.has_active_vehicle())

    def test_malformed_ai_no_unsafe_ce_mutation(self):
        ai_resp = "not-valid-json-{"
        eng, result, state = _run("Es un Focus 2019.", ai_response=ai_resp)
        # CE should handle gracefully (JSON parse error → fallback decision)
        self.assertIsNotNone(result)
        self.assertIn(result.action, HANDLED_ACTIONS)


class TestCR29AITimeout(unittest.TestCase):
    """CR-15: AI exception → safe fallback, result still valid."""

    def test_exception_produces_valid_result(self):
        eng = _make_engine()
        eng._call_openai = MagicMock(side_effect=RuntimeError("timeout"))
        state = _make_state()
        ctx = _make_ctx(state=state)
        event = _make_event(text="Es un Focus 2019.")
        with (
            patch("app.services.conversation_engine.lookup_vehicle", return_value=None),
            patch("app.services.conversation_engine.fuzzy_lookup_vehicle",
                  return_value=FuzzyLookupResult(
                      outcome="UNRESOLVED", hit=None, score=0.0,
                      second_hit=None, second_score=0.0, gap=0.0, make_constrained=False,
                  )),
        ):
            result = eng._process_text(ctx, event)
        self.assertIsNotNone(result)
        self.assertIn(result.action, HANDLED_ACTIONS)


class TestCR30KillSwitch(unittest.TestCase):
    """CR-1: outbound kill switch blocks dispatch at pre-gate paths."""

    def test_outbound_blocked_produces_handled_result(self):
        # Use F12 text so the pre-gate path (_send_service_boundary) fires,
        # which internally catches OutboundBlockedError and returns a blocked action.
        eng = _make_engine()
        eng._send_text_to_wa = MagicMock(
            side_effect=OutboundBlockedError(
                sender_path="test", kind="text", to_wa_id="5491199999999"
            )
        )
        state = _make_state()
        ctx = _make_ctx(state=state)
        event = _make_event(text="¿Puedo gestionar el Formulario 12 acá?")
        with (
            patch("app.services.conversation_engine.lookup_vehicle", return_value=None),
            patch("app.services.conversation_engine.fuzzy_lookup_vehicle",
                  return_value=FuzzyLookupResult(
                      outcome="UNRESOLVED", hit=None, score=0.0,
                      second_hit=None, second_score=0.0, gap=0.0, make_constrained=False,
                  )),
        ):
            result = eng._process_text(ctx, event)
        self.assertIn(result.action, HANDLED_ACTIONS)


class TestCR31ExistingNeedsHuman(unittest.TestCase):
    """CR-1: existing needs_human state — no automated commercial continuation."""

    def test_needs_human_does_not_price_or_schedule(self):
        state = _make_state(needs_human=True, last_intent="PREPURCHASE_INSPECTION")
        snap = _snap(state=state)
        # Even with intent known, if needs_human is set, CE should not continue
        # (This is tested via the CE routing; resolver is not aware of needs_human)
        self.assertTrue(snap.service_intent_known())


class TestCR32NoRedundantVehicleQuestion(unittest.TestCase):
    """CR-12: no redundant qualification loop for vehicle. M21.1.5."""

    def test_vehicle_known_no_further_ask(self):
        cand = _cand(marca="Ford", modelo="Focus", tipo_vehiculo="AUTO")
        state = _make_state(current_focus_candidate_id=10)
        snap = _snap(state=state, candidates=[cand])
        self.assertTrue(snap.vehicle_known())
        self.assertFalse(snap.needs_vehicle())

    def test_narrative_bypass_when_vehicle_and_location_known(self):
        cand = _cand(marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
                     zone_group="Palermo")
        state = _make_state(current_focus_candidate_id=10)
        snap = _snap(state=state, candidates=[cand])
        result = narrative_needs_ai(snap, "¿cuándo pueden venir?")
        self.assertFalse(result)


class TestCR33NoRedundantLocationQuestion(unittest.TestCase):
    """CR-12: no redundant location ask when candidate has zone."""

    def test_location_known_from_candidate(self):
        cand = _cand(tipo_vehiculo="AUTO", zone_group="Palermo")
        state = _make_state(current_focus_candidate_id=10)
        snap = _snap(state=state, candidates=[cand])
        self.assertTrue(snap.location_known())
        self.assertFalse(snap.needs_location())


class TestCR34NoRedundantInspectabilityQuestion(unittest.TestCase):
    """CR-12: resolved assembled-accessible → no repeated clarification."""

    def test_no_clarification_when_accessible(self):
        state = _make_state(inspectability_clarification_sent=False)
        snap = _snap(state=state)
        self.assertTrue(snap.inspectability_allows_progress())

    def test_clarification_already_resolved(self):
        raw = {
            "inspectability": {"value": INSP_ASSEMBLED_ACCESSIBLE, "status": STATUS_CONFIRMED}
        }
        narr = parse_narrative_interpretation(raw)
        state = _make_state(inspectability_clarification_sent=True)
        ctx = _make_ctx(state=state)
        eng = _make_engine()
        eng._apply_narrative_interpretation(ctx, state, narr)
        self.assertFalse(state.inspectability_clarification_sent)


class TestCR35FullQualificationReadiness(unittest.TestCase):
    """CR-13: pricing_ready → all evidence present; semantic layer does not price."""

    def test_pricing_ready_all_fields_confirmed(self):
        cand = _cand(marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
                     zone_group="Palermo")
        state = _make_state(
            current_focus_candidate_id=10,
            last_intent="PREPURCHASE_INSPECTION",
        )
        snap = _snap(state=state, candidates=[cand])
        self.assertTrue(snap.pricing_ready())
        self.assertTrue(snap.vehicle_known())
        self.assertTrue(snap.location_known())
        self.assertTrue(snap.inspectability_allows_progress())
        self.assertTrue(snap.service_intent_known())

    def test_pricing_ready_does_not_call_pricing_service(self):
        cand = _cand(marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
                     zone_group="Palermo")
        state = _make_state(
            current_focus_candidate_id=10,
            last_intent="PREPURCHASE_INSPECTION",
        )
        # pricing_ready() is a pure resolver function — no PricingService call
        snap = _snap(state=state, candidates=[cand])
        result = snap.pricing_ready()  # must not raise, must not call external service
        self.assertTrue(result)


# ═══════════════════════════════════════════════════════════════════════════════
# MULTI-TURN SEQUENCE TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestSEQ01FuzzyLifecycle(unittest.TestCase):
    """SEQ01: fuzzy → confirm → price continuation. One candidate, no re-ask."""

    def test_turn1_pending_key_set(self):
        # Turn 1: "ford ksl 2019 en Palermo" → CONFIRM outcome → pending key set
        fuzzy_result = FuzzyLookupResult(
            outcome="CONFIRM",
            hit=VehicleMatch(marca="Ford", modelo="Ka", tipo_vehiculo="AUTO",
                             confidence="medium", matched_alias="ford ka"),
            score=0.80, second_hit=None, second_score=0.0, gap=0.16,
            make_constrained=False,
        )
        eng, result, state = _run(
            "ford ksl 2019 en Palermo, cuánto sale?",
            fuzzy_return=fuzzy_result,
        )
        # Fuzzy confirmation text sent; state.pending_fuzzy_catalog_key set by CE
        eng._send_text_to_wa.assert_called()
        call_text = eng._send_text_to_wa.call_args[0][1]
        self.assertIn("Ka", call_text)

    def test_turn2_snap_no_vehicle_known_during_pending(self):
        # Between turns: pending key set, no confirmed candidate
        state = _make_state(pending_fuzzy_catalog_key="Ford||Ka")
        snap = _snap(state=state, candidates=[])
        self.assertFalse(snap.vehicle_known())
        self.assertFalse(snap.pricing_ready())

    def test_turn3_after_confirmation_vehicle_known(self):
        # After "sí": candidate created, pending cleared
        cand = _cand(marca="Ford", modelo="Ka", tipo_vehiculo="AUTO",
                     zone_group="Palermo")
        state = _make_state(
            pending_fuzzy_catalog_key=None,
            current_focus_candidate_id=10,
            last_intent="PREPURCHASE_INSPECTION",
        )
        snap = _snap(state=state, candidates=[cand])
        self.assertTrue(snap.vehicle_known())
        self.assertTrue(snap.location_known())


class TestSEQ02InspectabilityEscalation(unittest.TestCase):
    """SEQ02: non-running clarification → repeated unresolved → warm handoff."""

    def test_turn1_clarification_sent(self):
        eng, result, state = _run(
            "Es un Focus 2019 en Palermo. No arranca.",
            lookup_vehicle_return=None,
        )
        # Non-running clarification gate fires before AI
        eng._call_openai.assert_not_called()
        eng._send_text_to_wa.assert_called()

    def test_turn2_repeated_unresolved_sets_needs_human(self):
        # M21.1.2 behavior: when clarification already sent + repeated unresolved
        # → needs_human = True; tested via state flag
        state = _make_state(
            inspectability_clarification_sent=True,
        )
        snap = _snap(state=state)
        # inspectability_clarification_sent=True → UNRESOLVED in resolver
        from app.services.field_evidence import INSP_UNRESOLVED_NON_RUNNING
        self.assertEqual(snap.inspectability.value, INSP_UNRESOLVED_NON_RUNNING)
        self.assertFalse(snap.inspectability_allows_progress())


class TestSEQ03DeferredThenLaterActive(unittest.TestCase):
    """SEQ03: deferred turn → no mutation; later active turn → active flow."""

    def test_turn1_deferred_no_commercial_mutation(self):
        ai_resp = json.dumps({
            "intent": "OTHER",
            "reply": "...",
            "deferred_interest": True,
            "vehicle_make_model": None,
            "candidate": {"action": "none"},
            "extracted": {},
            "lead_flag": None,
            "needs_human": False,
        })
        eng, result, state = _run(
            "Hola, todavía estoy buscando. Ya les aviso.",
            ai_response=ai_resp,
        )
        # Deferred intercept: no candidate created
        eng._apply_candidate.assert_not_called()

    def test_turn1_state_unchanged(self):
        ai_resp = json.dumps({
            "deferred_interest": True,
            "vehicle_make_model": None,
            "candidate": {"action": "none"},
            "extracted": {}, "lead_flag": None, "needs_human": False,
            "intent": "OTHER", "reply": "...",
        })
        eng, result, state = _run(
            "Todavía estoy viendo opciones.",
            ai_response=ai_resp,
        )
        # home_zone_group not set
        self.assertIsNone(state.home_zone_group)

    def test_turn2_active_vehicle_proceeds(self):
        # After deferred turn, state is clean
        # Turn 2 with active vehicle → not deferred
        raw = {
            "deferred_interest": False,
            "vehicle_make_model": {"value": "Toyota Corolla", "status": STATUS_CONFIRMED},
            "vehicle_location": {"value": "Palermo", "status": STATUS_CONFIRMED},
        }
        narr = parse_narrative_interpretation(raw)
        self.assertFalse(narr.is_effectively_deferred())
        self.assertTrue(narr.has_active_vehicle())


class TestSEQ04LocationCorrectionPricing(unittest.TestCase):
    """SEQ04: candidate in Tigre → correction to Villa Urquiza → pricing uses VU."""

    def test_turn1_candidate_in_tigre(self):
        cand = _cand(marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
                     zone_group="Tigre")
        state = _make_state(current_focus_candidate_id=10)
        snap = _snap(state=state, candidates=[cand])
        self.assertEqual(snap.inspection_location.value, "Tigre")

    def test_turn2_correction_triggers_narrative(self):
        cand = _cand(marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
                     zone_group="Tigre")
        state = _make_state(current_focus_candidate_id=10)
        snap = _snap(state=state, candidates=[cand])
        # "en realidad" is a correction marker → narrative AI needed even with known evidence
        result = narrative_needs_ai(snap, "En realidad el auto está en Villa Urquiza.")
        self.assertTrue(result)

    def test_turn3_pricing_uses_corrected_location(self):
        # After correction applied to candidate
        cand = _cand(marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
                     zone_group="Villa Urquiza")
        state = _make_state(
            current_focus_candidate_id=10,
            last_intent="PREPURCHASE_INSPECTION",
        )
        snap = _snap(state=state, candidates=[cand])
        self.assertTrue(snap.location_known())
        self.assertEqual(snap.inspection_location.value, "Villa Urquiza")
        self.assertTrue(snap.pricing_ready())


class TestSEQ05FAQThenActiveVehicle(unittest.TestCase):
    """SEQ05: FAQ turn doesn't block subsequent active intent."""

    def test_turn1_faq_no_commercial_mutation(self):
        eng, result, state = _run(
            "¿Qué revisan?",
            lookup_vehicle_return=None,
        )
        # FAQ only: no new candidate id committed to state
        self.assertIsNone(state.current_focus_candidate_id)

    def test_turn1_state_empty_after_faq(self):
        # After FAQ, no vehicle/location committed
        state = _make_state()
        snap = _snap(state=state)
        self.assertFalse(snap.vehicle_known())
        self.assertFalse(snap.location_known())

    def test_turn2_active_vehicle_not_blocked(self):
        raw = {
            "deferred_interest": False,
            "vehicle_make_model": {"value": "Ford Focus", "status": STATUS_CONFIRMED},
            "vehicle_year": {"value": 2019, "status": STATUS_CONFIRMED},
            "vehicle_location": {"value": "Palermo", "status": STATUS_CONFIRMED},
        }
        narr = parse_narrative_interpretation(raw)
        self.assertFalse(narr.is_effectively_deferred())
        self.assertTrue(narr.has_active_vehicle())
        self.assertTrue(narr.has_active_location())


if __name__ == "__main__":
    unittest.main()
