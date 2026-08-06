"""BR-1 — Service Intent Gate (Layer F) — Executable Tests.

Source of truth: docs/BR1_intent_gate.md (approved 2026-08-03, Lara Dittmar).

Each test maps to a row or section of the approved decision table.  The outcome
column is derived in unit-test context from the observable signals listed below.

Observable unit-test proxies:
  CONTINUE_SET   → eng._call_openai.call_count == 1
                   AND state.last_intent == _INTENT_PREPURCHASE after call
  CONTINUE       → eng._call_openai.call_count == 1
                   AND state.last_intent unchanged (already set by provenance)
  CONVERSATIONAL → eng._call_openai.call_count == 1
                   AND state.last_intent is None (no intent mutation)
  FAQ_WITH_CONTEXT → eng._call_openai.call_count == 1
                   AND state.last_intent == _INTENT_PREPURCHASE after call
  UNCERTAIN      → eng._call_openai.call_count == 0
                   AND _UNCERTAIN_SERVICE_REPLY sent to WhatsApp
  BOUNDARY       → handled by higher-priority gate; not tested here
                   (covered by test_m21_1_1_service_intent_gate.py layers A–E)

Assertions that require live infrastructure (candidate created, zone persisted,
pricing called, Flow dispatched, revision created, scheduling called) are marked
# TODO: requires_infra and are verified in integration tests only.

All tests: SQLite in-memory; external services mocked; OUTBOUND_ENABLED=false.
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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

if not isinstance(getattr(_pg_dialect, "JSONB", None), type(sqlalchemy.JSON)):
    _pg_dialect.JSONB = sqlalchemy.JSON      # type: ignore[attr-defined]
if not isinstance(getattr(_pg_json, "JSONB", None), type(sqlalchemy.JSON)):
    _pg_json.JSONB = sqlalchemy.JSON         # type: ignore[attr-defined]

import json
from app.services.conversation_engine import (  # noqa: E402
    ConversationEngine,
    _INTENT_PREPURCHASE,
    _UNCERTAIN_SERVICE_REPLY,
)
from app.schemas.conversation import ConversationHandleIn, HANDLED_ACTIONS  # noqa: E402
from app.services.outbound_guard import OutboundBlockedError               # noqa: E402

STAGE_QUALIFYING = "QUALIFYING"

_DEFAULT_AI_RAW = json.dumps({
    "reply": "Respuesta de prueba.",
    "candidate": {"action": "none"},
    "extracted": {},
    "lead_flag": None,
    "needs_human": False,
})


# ── Fixture helpers (adapted from test_m21_1_1_service_intent_gate.py) ────────

def _make_state(**kw) -> types.SimpleNamespace:
    ns = types.SimpleNamespace(
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
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _make_lead(**kw) -> types.SimpleNamespace:
    ns = types.SimpleNamespace(
        id=1, flag="PRESUPUESTANDO", estado="CONSULTA_NUEVA",
        nombre="Test", telefono=None, necesita_humano=False,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _make_ctx(state=None, lead=None) -> types.SimpleNamespace:
    ctx = types.SimpleNamespace()
    ctx.thread = types.SimpleNamespace(id=42)
    ctx.contact = types.SimpleNamespace(wa_id="5491199999999")
    ctx.lead = lead if lead is not None else _make_lead()
    ctx.state = state if state is not None else _make_state()
    ctx.candidates = []
    return ctx


def _make_event(text=None) -> ConversationHandleIn:
    return ConversationHandleIn(
        thread_id=42,
        wa_message_id="test-br1-wa-id",
        wa_id="5491199999999",
        text=text,
        unanswered_recent_user_messages=[],
        recent_user_messages=[text] if text else [],
    )


def _make_engine(ai_response=None) -> ConversationEngine:
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock()
    eng.settings = MagicMock()
    eng.settings.openai_api_key = "sk-fake"
    eng.settings.openai_chat_model = "gpt-4o-mini"
    eng.settings.backend_url = "http://localhost:8000"
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


def _run(text, lookup_vehicle_return=None, state_kwargs=None, candidate=None):
    """Run _process_text. Returns (eng, result, state)."""
    eng = _make_engine()
    state = _make_state(**(state_kwargs or {}))
    ctx = _make_ctx(state=state, lead=_make_lead())
    if candidate is not None:
        ctx.candidates = [candidate]
    event = _make_event(text=text)
    with patch("app.services.conversation_engine.lookup_vehicle", return_value=lookup_vehicle_return):
        result = eng._process_text(ctx, event)
    return eng, result, state


def _assert_continue_set(test, eng, result, state):
    """CONTINUE_SET: AI called, last_intent persisted."""
    test.assertEqual(result.action, "replied")
    test.assertIn(result.action, HANDLED_ACTIONS)
    test.assertEqual(eng._call_openai.call_count, 1, "CONTINUE_SET must call AI")
    test.assertEqual(state.last_intent, _INTENT_PREPURCHASE, "CONTINUE_SET must persist last_intent")


def _assert_conversational(test, eng, result, state):
    """CONVERSATIONAL: AI called, last_intent not mutated."""
    test.assertEqual(result.action, "replied")
    test.assertIn(result.action, HANDLED_ACTIONS)
    test.assertEqual(eng._call_openai.call_count, 1, "CONVERSATIONAL must call AI")
    test.assertIsNone(state.last_intent, "CONVERSATIONAL must not set last_intent")


def _assert_uncertain(test, eng, result):
    """UNCERTAIN: AI not called, clarification reply sent."""
    test.assertEqual(result.action, "replied")
    test.assertIn(result.action, HANDLED_ACTIONS)
    test.assertEqual(eng._call_openai.call_count, 0, "UNCERTAIN must not call AI")
    test.assertEqual(eng._send_text_to_wa.call_args[0][1], _UNCERTAIN_SERVICE_REPLY)


# ── Row 1: Explicit inspection request ────────────────────────────────────────

class TestBR1Row1ExplicitInspection(unittest.TestCase):
    """Row 1 — Explicit inspection request → CONTINUE_SET (fresh and established)."""

    def test_row1_quiero_que_revisen(self):
        eng, result, state = _run("Quiero que revisen el auto.")
        _assert_continue_set(self, eng, result, state)

    def test_row1_necesito_revision(self):
        eng, result, state = _run("Necesito hacer una revisión del vehículo.")
        _assert_continue_set(self, eng, result, state)

    def test_row1_quiero_revisar_un(self):
        eng, result, state = _run("Quiero revisar un Focus 2019.")
        _assert_continue_set(self, eng, result, state)

    def test_row1_quiero_cotizar(self):
        """'Quiero cotizar un auto' — cotizar pattern added in R2, aligns with Row 1."""
        eng, result, state = _run("Quiero cotizar un auto.")
        _assert_continue_set(self, eng, result, state)

    def test_row1_coordinar_revision(self):
        eng, result, state = _run("Me interesa coordinar una revisión.")
        _assert_continue_set(self, eng, result, state)


# ── Row 2: Explicit pre-purchase signal ───────────────────────────────────────

class TestBR1Row2PrepurchaseSignal(unittest.TestCase):
    """Row 2 — Explicit pre-purchase signal → CONTINUE_SET."""

    def test_row2_estoy_por_comprar(self):
        eng, result, state = _run("Estoy por comprar el auto.")
        _assert_continue_set(self, eng, result, state)

    def test_row2_antes_de_comprar(self):
        eng, result, state = _run("Quiero revisarlo antes de comprarlo.")
        _assert_continue_set(self, eng, result, state)


# ── Row 3: Inspection-specific price ─────────────────────────────────────────

class TestBR1Row3InspectionSpecificPrice(unittest.TestCase):
    """Row 3 — Inspection-specific price → CONTINUE_SET."""

    def test_row3_cuanto_sale_la_revision(self):
        eng, result, state = _run("¿Cuánto sale la revisión?")
        _assert_continue_set(self, eng, result, state)

    def test_row3_precio_de_la_revision(self):
        eng, result, state = _run("¿Cuál es el precio de la revisión?")
        _assert_continue_set(self, eng, result, state)

    def test_row3_last_intent_persisted_after(self):
        """last_intent must be PREPURCHASE_INSPECTION after inspection-price turn."""
        eng, result, state = _run("¿Cuánto sale la revisión?")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)


# ── Row 4: Generic price ──────────────────────────────────────────────────────

class TestBR1Row4GenericPrice(unittest.TestCase):
    """Row 4 — Generic price on fresh thread → CONTINUE_SET."""

    def test_row4_cuanto_sale_fresh(self):
        eng, result, state = _run("¿Cuánto sale?")
        _assert_continue_set(self, eng, result, state)

    def test_row4_hola_cuanto_sale_fresh(self):
        eng, result, state = _run("Hola, cuánto sale?")
        _assert_continue_set(self, eng, result, state)

    def test_row4_cotizacion_fresh(self):
        eng, result, state = _run("Necesito una cotización.")
        _assert_continue_set(self, eng, result, state)

    def test_row4_no_intent_mutation_when_established(self):
        """Row 4 established → CONTINUE (last_intent unchanged from prior value)."""
        eng, result, state = _run(
            "¿Cuánto sale?",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        self.assertEqual(eng._call_openai.call_count, 1, "Established + price must reach AI")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)


# ── Row 5: Bare vehicle ───────────────────────────────────────────────────────

class TestBR1Row5BareVehicle(unittest.TestCase):
    """Row 5 — Bare vehicle on fresh thread → CONTINUE_SET."""

    def test_row5_catalog_hit_sets_intent(self):
        _mock_v = types.SimpleNamespace(matched_alias="Ford Ranger", tipo_vehiculo="AUTO", confidence=0.9)
        eng, result, state = _run("Ford Ranger 2020", lookup_vehicle_return=_mock_v)
        _assert_continue_set(self, eng, result, state)

    def test_row5_no_catalog_hit_fuzzy_confirm(self):
        """M21.1.4 closes the known gap: on exact miss, fuzzy normalization fires
        BEFORE Layer F, returning CONFIRM ("¿Es un Ford Ranger?") rather than the
        former UNCERTAIN. AI is not called; a confirmation question is sent.
        (Previously 'test_row5_no_catalog_hit_uncertain'; gap closed 2026-08-06.)"""
        eng, result, state = _run("Ford Ranger 2020", lookup_vehicle_return=None)
        self.assertEqual(result.action, "replied")
        self.assertIn(result.action, HANDLED_ACTIONS)
        self.assertEqual(eng._call_openai.call_count, 0, "fuzzy CONFIRM must not call AI")
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], "¿Es un Ford Ranger?")


# ── Row 6: Bare location ─────────────────────────────────────────────────────

class TestBR1Row6BareLocation(unittest.TestCase):
    """Row 6 — Bare location on fresh thread → CONTINUE_SET per BR-1.

    Known gap: Layer F has no pre-zone-extraction location detector.
    'Palermo' alone reaches UNCERTAIN until a location-lookup step is added to
    Layer F. This test documents the gap; it will fail when the gap is closed.
    """

    def test_row6_bare_location_known_gap(self):
        """KNOWN GAP — bare 'Palermo' without vehicle-location verb → UNCERTAIN.
        BR-1 Row 6 specifies CONTINUE_SET. Closing this gap requires a zone-name
        lookup in Layer F (pre-extraction). Tracked for M21.1.2."""
        eng, result, state = _run("Palermo")
        _assert_uncertain(self, eng, result)
        # TODO: when gap is closed, change to _assert_continue_set


# ── Row 7: Vehicle-location phrase ───────────────────────────────────────────

class TestBR1Row7VehicleLocationPhrase(unittest.TestCase):
    """Row 7 — Vehicle-location phrase on fresh thread → CONTINUE_SET."""

    def test_row7_esta_en_palermo(self):
        eng, result, state = _run("El auto está en Palermo.")
        _assert_continue_set(self, eng, result, state)

    def test_row7_esta_por_floresta(self):
        eng, result, state = _run("Está por Floresta.")
        _assert_continue_set(self, eng, result, state)

    def test_row7_ubicado_en(self):
        eng, result, state = _run("Está ubicado en San Isidro.")
        _assert_continue_set(self, eng, result, state)

    def test_row7_last_intent_persisted(self):
        eng, result, state = _run("El auto está en Palermo.")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)


# ── Rows 8-10: Structured commercial requests ────────────────────────────────

class TestBR1Rows8to10StructuredCommercial(unittest.TestCase):
    """Rows 8-10 — Vehicle + location / price combinations → CONTINUE_SET."""

    def test_row8_vehicle_plus_location(self):
        _mock_v = types.SimpleNamespace(matched_alias="Ford Ranger", tipo_vehiculo="AUTO", confidence=0.9)
        eng, result, state = _run("Ford Ranger 2020 en Palermo.", lookup_vehicle_return=_mock_v)
        _assert_continue_set(self, eng, result, state)

    def test_row10_vehicle_plus_price(self):
        _mock_v = types.SimpleNamespace(matched_alias="Ford Ranger", tipo_vehiculo="AUTO", confidence=0.9)
        eng, result, state = _run("Ford Ranger 2020, ¿cuánto sale?", lookup_vehicle_return=_mock_v)
        _assert_continue_set(self, eng, result, state)


# ── Row 11: Bare concern ──────────────────────────────────────────────────────

class TestBR1Row11BareConcern(unittest.TestCase):
    """Row 11 — Bare concern on fresh thread → UNCERTAIN."""

    def test_row11_me_preocupa_fresh(self):
        eng, result, state = _run("Me preocupa el estado del auto.")
        _assert_uncertain(self, eng, result)

    def test_row11_concern_with_established_context_continues(self):
        """Row 11 established → CONTINUE."""
        eng, result, state = _run(
            "Me preocupa el estado del auto.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        self.assertEqual(eng._call_openai.call_count, 1)


# ── Row 12: Generic help ──────────────────────────────────────────────────────

class TestBR1Row12GenericHelp(unittest.TestCase):
    """Row 12 — Generic help on fresh thread → UNCERTAIN."""

    def test_row12_necesito_ayuda_fresh(self):
        eng, result, state = _run("Necesito ayuda con un auto.")
        _assert_uncertain(self, eng, result)


# ── Rows 13-14: Courtesy / soft close / general info ─────────────────────────

class TestBR1Rows13to14CourtesySoftClose(unittest.TestCase):
    """Rows 13-14 — Courtesy and general info → CONVERSATIONAL (no intent mutation)."""

    def test_row13_gracias(self):
        eng, result, state = _run("Gracias.")
        _assert_conversational(self, eng, result, state)

    def test_row13_los_tengo_en_cuenta(self):
        eng, result, state = _run("Los tengo en cuenta.")
        _assert_conversational(self, eng, result, state)

    def test_row14_me_pasas_info(self):
        """Row 14: '¿Me pasás info?' → CONVERSATIONAL, no intent mutation.
        BR-1 §5: must end with '¿Tenés algún vehículo en vista?' — verified by AI path."""
        eng, result, state = _run("¿Me pasás info?")
        _assert_conversational(self, eng, result, state)

    def test_row14_me_pasas_info_does_not_set_last_intent(self):
        eng, result, state = _run("¿Me pasás info?")
        self.assertIsNone(state.last_intent, "Row 14 must not set last_intent")

    # TODO: requires_infra — verify reply ends with "¿Tenés algún vehículo en vista?"
    # TODO: requires_infra — verify no candidate created, no zone set, no price called


# ── Row 15: Pure FAQ ──────────────────────────────────────────────────────────

class TestBR1Row15PureFAQ(unittest.TestCase):
    """Row 15 — Pure FAQ → CONVERSATIONAL."""

    def test_row15_que_incluye(self):
        eng, result, state = _run("¿Qué incluye la revisión?")
        _assert_conversational(self, eng, result, state)

    def test_row15_cuanto_dura(self):
        eng, result, state = _run("¿Cuánto dura?")
        _assert_conversational(self, eng, result, state)

    def test_row15_no_intent_mutation(self):
        eng, result, state = _run("¿Qué incluye la revisión?")
        self.assertIsNone(state.last_intent)


# ── Row 16: FAQ + vehicle ─────────────────────────────────────────────────────

class TestBR1Row16FAQWithVehicle(unittest.TestCase):
    """Row 16 — FAQ + vehicle → FAQ_WITH_CONTEXT (AI called, intent persisted)."""

    def test_row16_faq_plus_vehicle_sets_intent(self):
        _mock_v = types.SimpleNamespace(matched_alias="Ford Focus", tipo_vehiculo="AUTO", confidence=0.9)
        eng, result, state = _run("Es un Focus 2019, ¿qué revisan?", lookup_vehicle_return=_mock_v)
        self.assertEqual(eng._call_openai.call_count, 1, "Row 16 must call AI")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE, "Row 16 must persist intent")

    def test_row16_pure_faq_no_vehicle_does_not_set_intent(self):
        """Pure FAQ without vehicle context → CONVERSATIONAL (no intent)."""
        eng, result, state = _run("¿Qué revisan?", lookup_vehicle_return=None)
        _assert_conversational(self, eng, result, state)

    # TODO: requires_infra — verify candidate created/retained from vehicle context


# ── Row 17: FAQ + explicit inspection ────────────────────────────────────────

class TestBR1Row17FAQWithExplicitInspection(unittest.TestCase):
    """Row 17 — FAQ + explicit inspection → CONTINUE_SET."""

    def test_row17_quiero_revisar_plus_faq(self):
        _mock_v = types.SimpleNamespace(matched_alias="Ford Focus", tipo_vehiculo="AUTO", confidence=0.9)
        eng, result, state = _run(
            "Quiero revisar un Focus 2019, ¿qué revisan?",
            lookup_vehicle_return=_mock_v,
        )
        _assert_continue_set(self, eng, result, state)


# ── Row 18: Contextual confirmation ──────────────────────────────────────────

class TestBR1Row18ContextualConfirmation(unittest.TestCase):
    """Row 18 — Contextual confirmation: fresh → UNCERTAIN; established → CONTINUE."""

    def test_row18_perfecto_fresh_uncertain(self):
        eng, result, state = _run("Perfecto.")
        _assert_uncertain(self, eng, result)

    def test_row18_perfecto_established_continues(self):
        eng, result, state = _run(
            "Perfecto.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        self.assertEqual(eng._call_openai.call_count, 1)
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)

    def test_row18_perfecto_established_via_candidate(self):
        _candidate = types.SimpleNamespace(tipo_vehiculo="AUTO")
        eng, result, state = _run("Perfecto.", candidate=_candidate)
        self.assertEqual(eng._call_openai.call_count, 1)


# ── Row 19: Scheduling follow-up ─────────────────────────────────────────────

class TestBR1Row19SchedulingFollowUp(unittest.TestCase):
    """Row 19 — Scheduling follow-up: fresh → UNCERTAIN; established → CONTINUE."""

    def test_row19_a_ver_que_fechas_fresh(self):
        eng, result, state = _run("A ver qué fechas tienen.")
        _assert_uncertain(self, eng, result)

    def test_row19_scheduling_established_continues(self):
        eng, result, state = _run(
            "A ver qué fechas tienen.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        self.assertEqual(eng._call_openai.call_count, 1)


# ── BR-1 §6: Provenance — established context ─────────────────────────────────

class TestBR1Section6Provenance(unittest.TestCase):
    """BR-1 §6 — Valid provenance sources for established context."""

    def test_s6_last_intent_is_provenance(self):
        """§6 pt 1: last_intent=PREPURCHASE_INSPECTION → bare text continues."""
        eng, result, state = _run(
            "Bueno, dale.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        self.assertEqual(eng._call_openai.call_count, 1)

    def test_s6_non_moto_candidate_is_provenance(self):
        """§6 pt 6: non-motorcycle candidate → bare text continues."""
        _candidate = types.SimpleNamespace(tipo_vehiculo="AUTO")
        eng, result, state = _run("Bueno, dale.", candidate=_candidate)
        self.assertEqual(eng._call_openai.call_count, 1)

    def test_s6_moto_candidate_not_provenance(self):
        """Motorcycle candidate is NOT valid provenance for car inspection."""
        _candidate = types.SimpleNamespace(tipo_vehiculo="MOTO")
        eng, result, state = _run("Bueno, dale.", candidate=_candidate)
        _assert_uncertain(self, eng, result)

    def test_s6_location_clarification_sent_is_provenance(self):
        """§6 pt 3: location_clarification_sent=True → Flow was sent for accepted message → provenance."""
        eng, result, state = _run(
            "Bueno, dale.",
            state_kwargs={"location_clarification_sent": True},
        )
        self.assertEqual(eng._call_openai.call_count, 1)

    def test_s6_vehicle_clarification_sent_is_provenance(self):
        """§6 pt 3: vehicle_clarification_sent=True → provenance."""
        eng, result, state = _run(
            "Bueno, dale.",
            state_kwargs={"vehicle_clarification_sent": True},
        )
        self.assertEqual(eng._call_openai.call_count, 1)

    def test_s6_home_zone_group_is_not_provenance(self):
        """BR-1 §6: home_zone_group is supporting state only, not standalone provenance."""
        eng, result, state = _run(
            "Bueno, dale.",
            state_kwargs={"home_zone_group": "norte"},
        )
        _assert_uncertain(self, eng, result)


# ── BR-1 §8: Mutation rules ───────────────────────────────────────────────────

class TestBR1Section8MutationRules(unittest.TestCase):
    """BR-1 §8 — UNCERTAIN and CONVERSATIONAL must not mutate intent or commercial state."""

    def test_s8_uncertain_does_not_set_last_intent(self):
        eng, result, state = _run("Perfecto.")
        self.assertIsNone(state.last_intent)

    def test_s8_conversational_does_not_set_last_intent(self):
        eng, result, state = _run("Gracias.")
        self.assertIsNone(state.last_intent)

    def test_s8_uncertain_does_not_call_ai(self):
        eng, result, state = _run("Perfecto.")
        self.assertEqual(eng._call_openai.call_count, 0)

    # TODO: requires_infra — verify UNCERTAIN/CONVERSATIONAL: no candidate created,
    # no zone mutated, no pricing called, no revision created, no scheduling triggered,
    # no Flow dispatched, no lead flag mutated


if __name__ == "__main__":
    unittest.main()
