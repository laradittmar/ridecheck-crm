"""M21.1.1 — Service Intent, Motorcycle Handoff & Unsupported-Service Gate Tests.

Test matrix:
  SI-01–SI-06:    Pre-purchase and qualification
  SI-07–SI-08:    Formulario 12 detection
  SI-09–SI-11:    Transfer detection
  SI-12–SI-14:    Repair detection
  SI-15–SI-23:    Motorcycle Layer A (all entry points)
  SI-24–SI-28:    Kill switch (handled=True for all gate branches)
  SI-N1–SI-N4:    Notification failure safety
  SI-H1–SI-H4:    Historical context guards
  SI-28b–SI-28e:  Zero-mutation assertions
  SI-28f–SI-28g:  Regression guards
  SI-PI1–SI-PI5:  Persisted intent + override
  SI-PC1–SI-PC6:  Phone-call escalation (all stages)
  SI-FAQ1–SI-FAQ8: FAQ bypass
  SI-FAQ-MUT1:    FAQ AI mutation guard
  SI-CLA-R1–R2:   Clause-aware repair detection
  SI-CLA-T1–T2:   Clause-aware transfer detection
  SI-MX1–SI-MX4:  Mixed intent

All tests: SQLite in-memory; external services (WhatsApp, OpenAI, Resend) mocked.
No DB migrations. No outbound calls. No container changes.
"""
from __future__ import annotations

import json
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
# Must happen before importing any app module that touches app.models.
import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json

if not isinstance(getattr(_pg_dialect, "JSONB", None), type(sqlalchemy.JSON)):
    _pg_dialect.JSONB = sqlalchemy.JSON      # type: ignore[attr-defined]
if not isinstance(getattr(_pg_json, "JSONB", None), type(sqlalchemy.JSON)):
    _pg_json.JSONB = sqlalchemy.JSON         # type: ignore[attr-defined]

# ── Imports under test ────────────────────────────────────────────────────────
from app.services.conversation_engine import (  # noqa: E402
    ConversationEngine,
    _F12_BOUNDARY_REPLY,
    _FALLBACK_WARM_HANDOFF,
    _INTENT_PREPURCHASE,
    _PHONE_CALL_HANDOFF_REPLY,
    _REPAIR_BOUNDARY_REPLY,
    _TRANSFER_BOUNDARY_REPLY,
    _UNCERTAIN_SERVICE_REPLY,
)
from app.schemas.conversation import ConversationHandleIn, HANDLED_ACTIONS  # noqa: E402
from app.services.outbound_guard import OutboundBlockedError               # noqa: E402

STAGE_QUALIFYING = "QUALIFYING"
STAGE_QUOTED = "QUOTED"
STAGE_SCHEDULING = "SCHEDULING"
STAGE_BOOKED = "BOOKED"

_DEFAULT_AI_RAW = json.dumps({
    "reply": "Respuesta de prueba.",
    "candidate": {"action": "none"},
    "extracted": {},
    "lead_flag": None,
    "needs_human": False,
})


# ── Fixture helpers ───────────────────────────────────────────────────────────

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
        id=1,
        flag="PRESUPUESTANDO",
        estado="CONSULTA_NUEVA",
        nombre="Test",
        telefono=None,
        necesita_humano=False,
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


def _make_event(text=None, unanswered=None, recent=None) -> ConversationHandleIn:
    msgs = unanswered or ([text] if text else [])
    return ConversationHandleIn(
        thread_id=42,
        wa_message_id="test-wa-id",
        wa_id="5491199999999",
        text=text,
        unanswered_recent_user_messages=unanswered or [],
        recent_user_messages=recent or msgs,
    )


def _make_engine(send_raises=False, ai_response=None) -> ConversationEngine:
    """Build CE with all external calls mocked."""
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock()
    eng.settings = MagicMock()
    eng.settings.openai_api_key = "sk-fake"
    eng.settings.openai_chat_model = "gpt-4o-mini"
    eng.settings.backend_url = "http://localhost:8000"

    if send_raises:
        eng._send_text_to_wa = MagicMock(side_effect=OutboundBlockedError(
            sender_path="test", kind="text", to_wa_id="5491199999999"
        ))
    else:
        eng._send_text_to_wa = MagicMock(return_value="mock-wa-id")

    eng._send_fallback_human_review_notification = MagicMock()

    ai_raw = ai_response if ai_response is not None else _DEFAULT_AI_RAW
    eng._call_openai = MagicMock(return_value=ai_raw)
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


def _run(text=None, unanswered=None, stage=STAGE_QUALIFYING, needs_human=False,
         last_intent=None, send_raises=False, ai_response=None, lead_kwargs=None,
         state_kwargs=None):
    """Run _process_text with mocked engine. Returns (eng, result, state, lead)."""
    eng = _make_engine(send_raises=send_raises, ai_response=ai_response)
    state = _make_state(last_stage=stage, needs_human=needs_human, last_intent=last_intent,
                        **(state_kwargs or {}))
    lead = _make_lead(**(lead_kwargs or {}))
    ctx = _make_ctx(state=state, lead=lead)
    event = _make_event(text=text, unanswered=unanswered)
    with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
        result = eng._process_text(ctx, event)
    return eng, result, state, lead


# ── Helpers for assertions ────────────────────────────────────────────────────

def _assert_handled(test: unittest.TestCase, result, expected_action: str):
    test.assertEqual(result.action, expected_action)
    test.assertTrue(result.handled, f"expected handled=True for action={result.action!r}")
    test.assertIn(result.action, HANDLED_ACTIONS, f"action {result.action!r} not in HANDLED_ACTIONS")


def _assert_no_commercial(test: unittest.TestCase, eng):
    """Assert none of the commercial pipeline functions were called."""
    test.assertEqual(eng._compute_price_quote.call_count, 0, "_compute_price_quote must not be called")
    test.assertEqual(eng._apply_candidate.call_count, 0, "_apply_candidate must not be called")
    test.assertEqual(eng._apply_extracted.call_count, 0, "_apply_extracted must not be called")
    test.assertEqual(eng._create_candidate_from_catalog.call_count, 0, "_create_candidate_from_catalog must not be called")
    test.assertEqual(eng._call_openai.call_count, 0, "_call_openai must not be called")
    test.assertEqual(eng._try_schedule_and_flow.call_count, 0, "_try_schedule_and_flow must not be called")


# ══════════════════════════════════════════════════════════════════════════════
# Detection unit tests (no DB, no CE context needed)
# ══════════════════════════════════════════════════════════════════════════════

class TestDetectionFunctions(unittest.TestCase):
    """Pure unit tests for detection helper methods."""

    def setUp(self):
        self.eng = ConversationEngine.__new__(ConversationEngine)
        self.eng.db = MagicMock()

    def _norm(self, text):
        return self.eng._norm_text(text)

    # ── Motorcycle detection ──────────────────────────────────────────────

    def test_moto_singular(self):
        self.assertTrue(self.eng._is_motorcycle_enquiry("Quiero revisar una moto Honda"))

    def test_moto_plural(self):
        self.assertTrue(self.eng._is_motorcycle_enquiry("Tengo dos motos en el garage"))

    def test_motocicleta_plural(self):
        self.assertTrue(self.eng._is_motorcycle_enquiry("son motocicletas de competición"))

    def test_scooter(self):
        self.assertTrue(self.eng._is_motorcycle_enquiry("tengo un scooter eléctrico"))

    def test_scooters_plural(self):
        self.assertTrue(self.eng._is_motorcycle_enquiry("Tengo tres scooters"))

    def test_quad(self):
        self.assertTrue(self.eng._is_motorcycle_enquiry("Es un quad todoterreno"))

    def test_quads_plural(self):
        self.assertTrue(self.eng._is_motorcycle_enquiry("vendo quads usados"))

    def test_atv(self):
        self.assertTrue(self.eng._is_motorcycle_enquiry("Es un ATV 250cc"))

    def test_atvs_plural(self):
        self.assertTrue(self.eng._is_motorcycle_enquiry("importamos atvs de USA"))

    def test_utv(self):
        self.assertTrue(self.eng._is_motorcycle_enquiry("quiero revisar un UTV"))

    def test_ciclomotor(self):
        self.assertTrue(self.eng._is_motorcycle_enquiry("tengo un ciclomotor"))

    def test_cuatriciclo(self):
        self.assertTrue(self.eng._is_motorcycle_enquiry("Es un cuatriciclo de campo"))

    def test_cuatriciclos_plural(self):
        self.assertTrue(self.eng._is_motorcycle_enquiry("vendo cuatriciclos"))

    def test_moto_accent(self):
        self.assertTrue(self.eng._is_motorcycle_enquiry("tengo una motocicleta Honda"))

    def test_no_motorcycle_auto(self):
        self.assertFalse(self.eng._is_motorcycle_enquiry("quiero revisar un auto"))

    def test_no_motorcycle_word_boundary(self):
        # "motorizado" contains "motor" not "moto" as whole word
        # "moto" in "motorizado" — lookbehind says char before 'm' is start or space
        # lookahead says char after 'moto' is 'r' which is [a-z] → no match
        self.assertFalse(self.eng._is_motorcycle_enquiry("vehículo motorizado"))

    # ── F12 detection ─────────────────────────────────────────────────────

    def test_f12_detected(self):
        self.assertTrue(self.eng._detect_f12_request(self._norm("necesito el formulario 12")))

    def test_f12_detected_formulario_doce(self):
        self.assertTrue(self.eng._detect_f12_request(self._norm("quiero el formulario doce")))

    def test_f12_detected_f12(self):
        self.assertTrue(self.eng._detect_f12_request(self._norm("quiero tramitar un formulario 12b")))

    def test_f12_not_detected_no_phrase(self):
        self.assertFalse(self.eng._detect_f12_request(self._norm("quiero revisar un auto")))

    def test_f12_past_context_suppressed(self):
        # Pure past context: no new request
        self.assertFalse(self.eng._detect_f12_request(
            self._norm("Ya hice el Formulario 12 y ahora quiero revisar el auto antes de comprarlo")
        ))

    def test_f12_past_context_with_new_request_wins(self):
        # Mixed: past context AND new request → True (SI-MX4)
        self.assertTrue(self.eng._detect_f12_request(
            self._norm("Ya hice un Formulario 12, pero necesito otro y quiero que ustedes lo gestionen")
        ))

    # ── Transfer detection ────────────────────────────────────────────────

    def test_transfer_detected(self):
        self.assertTrue(self.eng._detect_transfer_request(
            self._norm("Necesito hacer la transferencia del auto")
        ))

    def test_transfer_third_person(self):
        # "que ustedes hagan la transferencia" → explicit override also matches as base
        self.assertTrue(self.eng._detect_transfer_request(
            self._norm("necesito que ustedes hagan la transferencia")
        ))

    def test_transfer_gestoria(self):
        self.assertTrue(self.eng._detect_transfer_request(self._norm("quiero ir a una gestoría")))

    def test_transfer_payment_exclusion(self):
        # "pago por transferencia" → exclusion, no explicit override → False
        self.assertFalse(self.eng._detect_transfer_request(
            self._norm("¿Puedo pagar por transferencia bancaria?")
        ))

    def test_transfer_future_step_exclusion(self):
        # "después de la revisión voy a hacer la transferencia" → exclusion
        self.assertFalse(self.eng._detect_transfer_request(
            self._norm("Después de la revisión voy a hacer la transferencia")
        ))

    def test_transfer_exclusion_overridden_by_explicit(self):
        # Exclusion present + explicit override → True (SI-CLA-T2)
        self.assertTrue(self.eng._detect_transfer_request(
            self._norm("Después de la revisión necesito que ustedes hagan la transferencia.")
        ))

    def test_no_transfer_unrelated(self):
        self.assertFalse(self.eng._detect_transfer_request(
            self._norm("Quiero revisar un Ford Ka antes de comprarlo")
        ))

    # ── Repair detection ──────────────────────────────────────────────────

    def test_repair_detected(self):
        self.assertTrue(self.eng._detect_repair_request(
            self._norm("El auto necesita una reparación mecánica, ¿lo pueden arreglar?")
        ))

    def test_repair_pronoun_form(self):
        # Addendum: pronoun form "necesito que me lo reparen" → explicit pattern
        self.assertTrue(self.eng._detect_repair_request(
            self._norm("Antes de comprarlo necesito que me lo reparen")
        ))

    def test_repair_freno(self):
        self.assertTrue(self.eng._detect_repair_request(
            self._norm("¿Pueden arreglar el freno?")
        ))

    def test_repair_no_mechanic_exclusion_with_pre_purchase(self):
        # "mi mecánico no se puede trasladar, quiero revisar antes de comprarlo" → False
        # No explicit repair pattern → check base patterns → none match for "revisar"
        self.assertFalse(self.eng._detect_repair_request(
            self._norm("Quiero revisar el auto antes de comprarlo, mi mecánico no se puede trasladar")
        ))

    def test_repair_mechanic_exclusion_overridden_by_explicit(self):
        # "mi mecánico no puede venir, pero necesito que ustedes reparen" → True (SI-CLA-R2)
        self.assertTrue(self.eng._detect_repair_request(
            self._norm("Mi mecánico no puede venir, pero necesito que ustedes reparen el auto.")
        ))

    def test_repair_inspection_context_suppressed(self):
        # "revisar antes de comprar" → inspection exclusion → unconditional suppression
        self.assertFalse(self.eng._detect_repair_request(
            self._norm("Quiero que lo revisen antes de comprar el auto")
        ))

    def test_repair_taller_detected(self):
        self.assertTrue(self.eng._detect_repair_request(self._norm("Necesito ir a un taller mecánico")))

    # ── Pre-purchase detection ────────────────────────────────────────────

    def test_prepurchase_revision_precompra(self):
        self.assertTrue(self.eng._detect_prepurchase_signal(
            self._norm("Quiero hacer una revisión precompra")
        ))

    def test_prepurchase_antes_de_comprarlo(self):
        self.assertTrue(self.eng._detect_prepurchase_signal(
            self._norm("Quiero revisar el auto antes de comprarlo")
        ))

    def test_prepurchase_cotizar_revision(self):
        self.assertTrue(self.eng._detect_prepurchase_signal(
            self._norm("Quiero cotizar una revisión")
        ))

    def test_prepurchase_cuanto_sale_revisar(self):
        self.assertTrue(self.eng._detect_prepurchase_signal(
            self._norm("¿Cuánto sale revisar un auto que estoy por comprar?")
        ))

    def test_prepurchase_necesito_inspeccion(self):
        self.assertTrue(self.eng._detect_prepurchase_signal(
            self._norm("necesito una inspección antes de comprarlo")
        ))

    def test_no_prepurchase_generic(self):
        self.assertFalse(self.eng._detect_prepurchase_signal(
            self._norm("Necesito ayuda con un auto")
        ))

    def test_no_prepurchase_f12(self):
        self.assertFalse(self.eng._detect_prepurchase_signal(
            self._norm("necesito el formulario 12")
        ))

    # ── FAQ detection ─────────────────────────────────────────────────────

    def test_faq_que_incluye(self):
        self.assertTrue(self.eng._detect_general_information(
            self._norm("¿Qué incluye la revisión?")
        ))

    def test_faq_cuanto_demora(self):
        self.assertTrue(self.eng._detect_general_information(
            self._norm("¿Cuánto demora?")
        ))

    def test_faq_trabajan_sabado(self):
        self.assertTrue(self.eng._detect_general_information(
            self._norm("¿Trabajan los sábados?")
        ))

    def test_faq_tengo_que_estar_presente(self):
        self.assertTrue(self.eng._detect_general_information(
            self._norm("¿Tengo que estar presente?")
        ))

    def test_faq_como_funciona(self):
        self.assertTrue(self.eng._detect_general_information(
            self._norm("¿Cómo funciona Ridecheck?")
        ))

    def test_faq_hola_greeting(self):
        self.assertTrue(self.eng._detect_general_information(self._norm("Hola")))

    def test_faq_buenas_tardes(self):
        self.assertTrue(self.eng._detect_general_information(self._norm("Buenas tardes")))

    def test_faq_consulta(self):
        self.assertTrue(self.eng._detect_general_information(
            self._norm("Hola, quería hacer una consulta.")
        ))

    def test_faq_donde_atienden(self):
        self.assertTrue(self.eng._detect_general_information(
            self._norm("¿Dónde atienden?")
        ))

    def test_no_faq_prepurchase(self):
        # Pre-purchase signal → not classified as FAQ
        self.assertFalse(self.eng._detect_general_information(
            self._norm("Quiero revisar el auto antes de comprarlo")
        ))

    def test_no_faq_ford_ka(self):
        self.assertFalse(self.eng._detect_general_information(self._norm("Ford Ka 2019")))


# ══════════════════════════════════════════════════════════════════════════════
# SI-01–SI-06: Pre-purchase and qualification
# ══════════════════════════════════════════════════════════════════════════════

class TestPrePurchaseAndQualification(unittest.TestCase):

    def test_si01_revision_precompra_ai_called(self):
        """SI-01: 'Quiero hacer una revisión precompra' → pre-purchase; AI called."""
        eng, result, state, _ = _run(text="Quiero hacer una revisión precompra")
        _assert_handled(self, result, "replied")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)
        self.assertGreater(eng._call_openai.call_count, 0)

    def test_si02_vehicle_zone_pre_purchase_ai_called(self):
        """SI-02: '¿Cuánto sale revisar un auto que estoy por comprar?' → pre-purchase; AI called."""
        eng, result, state, _ = _run(text="¿Cuánto sale revisar un auto que estoy por comprar?")
        _assert_handled(self, result, "replied")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)
        self.assertGreater(eng._call_openai.call_count, 0)

    def test_si03_cuanto_sale_revisar(self):
        """SI-03: Another pre-purchase phrasing → AI called."""
        eng, result, state, _ = _run(text="¿Cuánto sale revisar?")
        _assert_handled(self, result, "replied")
        self.assertGreater(eng._call_openai.call_count, 0)

    def test_si04_necesito_revisar_antes_de_senarlo(self):
        """SI-04: 'Necesito revisar el auto antes de señarlo' → pre-purchase; AI called."""
        eng, result, state, _ = _run(text="Necesito revisar el auto antes de señarlo")
        _assert_handled(self, result, "replied")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)
        self.assertGreater(eng._call_openai.call_count, 0)

    def test_si05_revisar_ford_ka_antes_de_comprarlo(self):
        """SI-05: 'Quiero revisar un Ford Ka antes de comprarlo' → pre-purchase; AI called."""
        eng, result, state, _ = _run(text="Quiero revisar un Ford Ka antes de comprarlo")
        _assert_handled(self, result, "replied")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)
        self.assertGreater(eng._call_openai.call_count, 0)

    def test_si06_uncertain_no_ai(self):
        """SI-06: 'Necesito ayuda con un auto' → UNCERTAIN; no AI; no candidate."""
        eng, result, state, _ = _run(text="Necesito ayuda con un auto")
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _UNCERTAIN_SERVICE_REPLY)
        self.assertEqual(eng._call_openai.call_count, 0)
        _assert_no_commercial(self, eng)


# ══════════════════════════════════════════════════════════════════════════════
# SI-07–SI-08: Formulario 12
# ══════════════════════════════════════════════════════════════════════════════

class TestFormulario12(unittest.TestCase):

    def test_si07_f12_boundary(self):
        """SI-07: F12 request → boundary; no AI."""
        eng, result, state, _ = _run(text="Necesito hacer un formulario 12 para el auto")
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _F12_BOUNDARY_REPLY)
        self.assertEqual(eng._call_openai.call_count, 0)
        _assert_no_commercial(self, eng)

    def test_si08_f12_past_context_pre_purchase_wins(self):
        """SI-08: 'Ya hice el F12 y ahora quiero revisión precompra' → pre-purchase; AI called."""
        eng, result, state, _ = _run(
            text="Ya hice el Formulario 12 y ahora quiero revisar el auto antes de comprarlo"
        )
        _assert_handled(self, result, "replied")
        # F12 boundary must NOT be sent
        if eng._send_text_to_wa.called:
            self.assertNotEqual(
                eng._send_text_to_wa.call_args[0][1], _F12_BOUNDARY_REPLY
            )
        self.assertGreater(eng._call_openai.call_count, 0)
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)


# ══════════════════════════════════════════════════════════════════════════════
# SI-09–SI-11: Transfer
# ══════════════════════════════════════════════════════════════════════════════

class TestTransfer(unittest.TestCase):

    def test_si09_transfer_boundary(self):
        """SI-09: Transfer request → boundary; no AI."""
        eng, result, state, _ = _run(text="Necesito hacer la transferencia del auto y los papeles")
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _TRANSFER_BOUNDARY_REPLY)
        self.assertEqual(eng._call_openai.call_count, 0)
        _assert_no_commercial(self, eng)

    def test_si10_payment_transfer_not_boundary(self):
        """SI-10: In-progress conversation, '¿Puedo pagar por transferencia bancaria?' → NOT transfer; AI called."""
        # last_intent=PREPURCHASE models a realistic in-progress conversation
        eng, result, state, _ = _run(
            text="¿Puedo pagar por transferencia bancaria?",
            last_intent=_INTENT_PREPURCHASE,
        )
        _assert_handled(self, result, "replied")
        if eng._send_text_to_wa.called:
            self.assertNotEqual(
                eng._send_text_to_wa.call_args[0][1], _TRANSFER_BOUNDARY_REPLY
            )
        self.assertGreater(eng._call_openai.call_count, 0)

    def test_si11_future_step_transfer_not_boundary(self):
        """SI-11: In-progress conversation, 'Después de la revisión voy a hacer la transferencia' → NOT transfer; AI called."""
        # last_intent=PREPURCHASE models a realistic in-progress conversation
        eng, result, state, _ = _run(
            text="Después de la revisión voy a hacer la transferencia",
            last_intent=_INTENT_PREPURCHASE,
        )
        _assert_handled(self, result, "replied")
        if eng._send_text_to_wa.called:
            self.assertNotEqual(
                eng._send_text_to_wa.call_args[0][1], _TRANSFER_BOUNDARY_REPLY
            )
        self.assertGreater(eng._call_openai.call_count, 0)


# ══════════════════════════════════════════════════════════════════════════════
# SI-12–SI-14: Repair
# ══════════════════════════════════════════════════════════════════════════════

class TestRepair(unittest.TestCase):

    def test_si12_repair_boundary(self):
        """SI-12: 'El auto necesita una reparación mecánica, ¿lo pueden arreglar?' → repair boundary."""
        eng, result, state, _ = _run(
            text="El auto necesita una reparación mecánica, ¿lo pueden arreglar?"
        )
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _REPAIR_BOUNDARY_REPLY)
        self.assertEqual(eng._call_openai.call_count, 0)
        _assert_no_commercial(self, eng)

    def test_si13_mechanic_cant_come_pre_purchase_not_repair(self):
        """SI-13: 'Quiero revisar el auto antes de comprarlo, mi mecánico no se puede trasladar' → NOT repair."""
        eng, result, state, _ = _run(
            text="Quiero revisar el auto antes de comprarlo, mi mecánico no se puede trasladar"
        )
        _assert_handled(self, result, "replied")
        if eng._send_text_to_wa.called:
            self.assertNotEqual(
                eng._send_text_to_wa.call_args[0][1], _REPAIR_BOUNDARY_REPLY
            )
        self.assertGreater(eng._call_openai.call_count, 0)
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)

    def test_si14_taller_pre_purchase_not_repair(self):
        """SI-14: 'El auto está en un taller, quiero revisarlo antes de comprarlo' → NOT repair."""
        eng, result, state, _ = _run(
            text="El auto está en un taller, quiero revisarlo antes de comprarlo"
        )
        _assert_handled(self, result, "replied")
        if eng._send_text_to_wa.called:
            self.assertNotEqual(
                eng._send_text_to_wa.call_args[0][1], _REPAIR_BOUNDARY_REPLY
            )
        self.assertGreater(eng._call_openai.call_count, 0)
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)


# ══════════════════════════════════════════════════════════════════════════════
# SI-15–SI-23: Motorcycle Layer A
# ══════════════════════════════════════════════════════════════════════════════

class TestMotorcycleLayerA(unittest.TestCase):

    def _assert_motorcycle_handoff(self, eng, result, state):
        _assert_handled(self, result, "replied")
        self.assertTrue(state.needs_human)
        self.assertTrue(eng._send_fallback_human_review_notification.called)
        self.assertEqual(
            eng._send_text_to_wa.call_args[0][1], _FALLBACK_WARM_HANDOFF
        )
        self.assertEqual(eng._call_openai.call_count, 0)

    def test_si15_qualifying_moto_layer_a(self):
        """SI-15: QUALIFYING + moto → Layer A; needs_human=True; no candidate; no AI."""
        eng, result, state, lead = _run(text="Quiero revisar una moto Honda CB500")
        self._assert_motorcycle_handoff(eng, result, state)
        self.assertTrue(lead.necesita_humano)
        self.assertEqual(eng._apply_candidate.call_count, 0)

    def test_si16_quoted_moto_fires_before_acceptance(self):
        """SI-16: QUOTED + 'Sí, acepto, pero en realidad es una moto' → Layer A before acceptance."""
        eng, result, state, _ = _run(
            text="Sí, acepto, pero en realidad es una moto",
            stage=STAGE_QUOTED,
        )
        self._assert_motorcycle_handoff(eng, result, state)

    def test_si17_scheduling_moto_fires_before_scheduler(self):
        """SI-17: SCHEDULING + 'quería aclarar que es una moto' → Layer A before scheduling."""
        eng, result, state, _ = _run(
            text="Sí, pero quería aclarar que es una moto",
            stage=STAGE_SCHEDULING,
        )
        self._assert_motorcycle_handoff(eng, result, state)
        self.assertEqual(eng._try_schedule_and_flow.call_count, 0)

    def test_si18_f12_moto_motorcycle_wins(self):
        """SI-18: 'Necesito el formulario 12 de una moto desarmada' → motorcycle wins."""
        eng, result, state, _ = _run(
            text="Necesito el formulario 12 de una moto desarmada"
        )
        self._assert_motorcycle_handoff(eng, result, state)
        # F12 boundary must NOT be sent
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _FALLBACK_WARM_HANDOFF)

    def test_si19_audio_transcript_moto(self):
        """SI-19: Audio text='tengo una moto', unanswered=[] → Layer A fires."""
        eng, result, state, _ = _run(text="tengo una moto", unanswered=[])
        self._assert_motorcycle_handoff(eng, result, state)

    def test_si20_burst_with_moto_in_event_text(self):
        """SI-20: Burst + event.text='la moto está en buen estado' → event.text in evidence → Layer A."""
        eng, result, state, _ = _run(
            text="la moto está en buen estado",
            unanswered=["Hola, me interesa revisar un auto"],
        )
        self._assert_motorcycle_handoff(eng, result, state)

    def test_si21_vehicle_flow_moto(self):
        """SI-21: Vehicle Flow + tipo_vehiculo='MOTO' → motorcycle_human_handoff."""
        eng = _make_engine()
        state = _make_state()
        lead = _make_lead()
        ctx = _make_ctx(state=state, lead=lead)
        flow_data = {
            "tipo_vehiculo": "MOTO",
            "marca": "Honda",
            "modelo": "CB500",
            "anio": "2020",
        }
        result = eng._process_vehicle_fallback_response(ctx, state, flow_data)
        _assert_handled(self, result, "replied")
        self.assertTrue(state.needs_human)
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _FALLBACK_WARM_HANDOFF)
        self.assertEqual(eng._apply_candidate.call_count, 0)

    def test_si22_ai_extracts_moto_candidate_guard(self):
        """SI-22: AI extracts MOTO candidate → guard before _apply_candidate; needs_human=True."""
        ai_moto = json.dumps({
            "reply": "Entendido.",
            "candidate": {"action": "create", "tipo_vehiculo": "MOTO"},
            "extracted": {},
            "lead_flag": None,
            "needs_human": False,
        })
        eng, result, state, lead = _run(
            text="Quiero revisar el auto antes de comprarlo",  # pre-purchase, no moto in text
            ai_response=ai_moto,
        )
        _assert_handled(self, result, "replied")
        self.assertTrue(state.needs_human)
        self.assertEqual(eng._apply_candidate.call_count, 0)
        self.assertTrue(lead.necesita_humano)

    def test_si23_website_form_moto(self):
        """SI-23: Website form with tipo_vehiculo=MOTO → motorcycle_human_handoff."""
        eng = _make_engine()
        state = _make_state()
        lead = _make_lead()
        ctx = _make_ctx(state=state, lead=lead)
        form_data = {
            "vehicle_text": "Honda PCX 150",
            "submitted_tipo": "moto",
            "zone_detail": "Palermo",
        }
        with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
            with patch("app.services.conversation_engine._normalize_submitted_tipo", return_value="MOTO"):
                result = eng._handle_website_form(ctx, state, form_data)
        _assert_handled(self, result, "replied")
        self.assertTrue(state.needs_human)
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _FALLBACK_WARM_HANDOFF)


# ══════════════════════════════════════════════════════════════════════════════
# SI-24–SI-28: Kill switch
# ══════════════════════════════════════════════════════════════════════════════

class TestKillSwitch(unittest.TestCase):

    def test_si24_kill_switch_motorcycle(self):
        """SI-24: Kill switch + motorcycle → human_handoff_blocked; handled=True; needs_human=True."""
        eng, result, state, _ = _run(
            text="Quiero revisar una moto Honda",
            send_raises=True,
        )
        _assert_handled(self, result, "human_handoff_blocked")
        self.assertTrue(state.needs_human)

    def test_si25_kill_switch_f12(self):
        """SI-25: Kill switch + F12 → service_gate_blocked; handled=True."""
        eng, result, state, _ = _run(
            text="Necesito hacer un formulario 12",
            send_raises=True,
        )
        _assert_handled(self, result, "service_gate_blocked")

    def test_si26_kill_switch_transfer(self):
        """SI-26: Kill switch + transfer → service_gate_blocked; handled=True."""
        eng, result, state, _ = _run(
            text="Necesito hacer la transferencia del auto",
            send_raises=True,
        )
        _assert_handled(self, result, "service_gate_blocked")

    def test_si27_kill_switch_repair(self):
        """SI-27: Kill switch + repair → service_gate_blocked; handled=True."""
        eng, result, state, _ = _run(
            text="El auto necesita arreglo mecánico, ¿lo pueden reparar?",
            send_raises=True,
        )
        _assert_handled(self, result, "service_gate_blocked")

    def test_si28_kill_switch_uncertain(self):
        """SI-28: Kill switch + UNCERTAIN → service_gate_blocked; handled=True."""
        eng, result, state, _ = _run(
            text="Necesito ayuda con un auto",
            send_raises=True,
        )
        _assert_handled(self, result, "service_gate_blocked")


# ══════════════════════════════════════════════════════════════════════════════
# SI-N1–SI-N4: Notification failure safety
# ══════════════════════════════════════════════════════════════════════════════

class TestNotificationFailure(unittest.TestCase):

    def test_si_n1_notification_fails_wa_succeeds(self):
        """SI-N1: Notification exception + WA succeeds → needs_human=True; replied; handled=True."""
        eng = _make_engine()
        eng._send_fallback_human_review_notification = MagicMock(
            side_effect=Exception("Resend down")
        )
        state = _make_state()
        lead = _make_lead()
        ctx = _make_ctx(state=state, lead=lead)
        event = _make_event(text="Quiero revisar una moto")
        with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
            result = eng._process_text(ctx, event)
        _assert_handled(self, result, "replied")
        self.assertTrue(state.needs_human)
        self.assertTrue(eng._send_text_to_wa.called)

    def test_si_n2_notification_fails_wa_kill_switched(self):
        """SI-N2: Notification exception + WA kill-switched → human_handoff_blocked; handled=True."""
        eng = _make_engine(send_raises=True)
        eng._send_fallback_human_review_notification = MagicMock(
            side_effect=Exception("Resend down")
        )
        state = _make_state()
        lead = _make_lead()
        ctx = _make_ctx(state=state, lead=lead)
        event = _make_event(text="Quiero revisar una moto")
        with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
            result = eng._process_text(ctx, event)
        _assert_handled(self, result, "human_handoff_blocked")
        self.assertTrue(state.needs_human)

    def test_si_n3_needs_human_committed_before_send(self):
        """SI-N3: needs_human is committed to DB before WA send attempt."""
        eng = _make_engine()
        state = _make_state()
        lead = _make_lead()
        ctx = _make_ctx(state=state, lead=lead)
        event = _make_event(text="Quiero revisar una moto Honda")

        db_commit_calls = []

        def _commit_track():
            db_commit_calls.append(state.needs_human)

        eng.db.commit = MagicMock(side_effect=_commit_track)
        with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
            eng._process_text(ctx, event)

        # At least one commit happened when needs_human=True (before WA send)
        self.assertTrue(any(db_commit_calls), "needs_human must be committed before WA send")
        self.assertTrue(db_commit_calls[0], "first commit after needs_human=True")

    def test_si_n4_skipped_human_not_from_process_text(self):
        """SI-N4: needs_human=True path is handled in _handle (not _process_text); verify detection."""
        # _process_text is only called when needs_human=False.
        # This test verifies that setting needs_human=True in a prior turn is respected.
        # The 'skipped_human' action is returned by _handle (not _process_text) when
        # state.needs_human is already True on entry.
        eng = _make_engine()
        # If needs_human is True, the _handle method returns skipped_human before
        # calling _process_text. Verify that Layer F gate also respects needs_human.
        state = _make_state(needs_human=True, last_stage=STAGE_QUALIFYING)
        eng2 = ConversationEngine.__new__(ConversationEngine)
        eng2.db = MagicMock()
        # _handle_qualifying_intent respects not state.needs_human in _process_text Layer F
        # (it is guarded by `not state.needs_human`)
        # M21.1.1-R1: Use a phrase with no vehicle, no inspection signal, no context.
        # "Ford Ka 2019" is now a catalog hit → passes through Layer F (R1 change).
        # Use a bare ambiguous phrase to verify UNCERTAIN is still returned for zero-signal input.
        ctx_dummy = types.SimpleNamespace(
            lead=_make_lead(), thread=types.SimpleNamespace(id=1), candidates=[],
        )
        with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
            intent_result = eng._handle_qualifying_intent(ctx_dummy, state, "quiero saber algo")
        # No inspection signal → Layer F returns UNCERTAIN (not None)
        self.assertIsNotNone(intent_result)


# ══════════════════════════════════════════════════════════════════════════════
# SI-H1–SI-H4: Historical context guards
# ══════════════════════════════════════════════════════════════════════════════

class TestHistoricalContext(unittest.TestCase):

    def test_si_h1_prior_moto_mention_not_in_current_evidence(self):
        """SI-H1: Prior turn had moto; current turn is pre-purchase → Layer A does NOT fire."""
        # Layer A uses _current_evidence (unanswered + event.text), not recent_user_messages.
        # Only the current-turn text "Ford Ka antes de comprarlo" is in evidence — no moto term.
        eng, result, state, _ = _run(
            text="Ford Ka antes de comprarlo",
        )
        _assert_handled(self, result, "replied")
        self.assertFalse(state.needs_human)
        # Should be classified as pre-purchase
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)

    def test_si_h2_prior_f12_current_prepurchase(self):
        """SI-H2: Prior turn: F12. Current: 'Revisión precompra' → pre-purchase; AI called."""
        # Current-turn text has no F12 signal — prior F12 is in historical context only.
        eng, result, state, _ = _run(
            text="Quiero cotizar una revisión precompra",
        )
        _assert_handled(self, result, "replied")
        # F12 boundary must NOT fire since current turn has no F12 signal
        if eng._send_text_to_wa.called:
            self.assertNotEqual(
                eng._send_text_to_wa.call_args[0][1], _F12_BOUNDARY_REPLY
            )
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)

    def test_si_h3_confirmed_intent_turn2_no_reclassification(self):
        """SI-H3: Turn 1 confirms PREPURCHASE. Turn 2: 'Ford Ka 2019' → fast-path; AI called; no re-ask.
        current_focus_candidate_id=1 models the candidate created in Turn 1 (M21.1.4-FV guard fix)."""
        eng, result, state, _ = _run(
            text="Ford Ka 2019",
            last_intent=_INTENT_PREPURCHASE,
            state_kwargs={"current_focus_candidate_id": 1},
        )
        _assert_handled(self, result, "replied")
        # UNCERTAIN reply must NOT be sent
        if eng._send_text_to_wa.called:
            self.assertNotEqual(
                eng._send_text_to_wa.call_args[0][1], _UNCERTAIN_SERVICE_REPLY
            )
        self.assertGreater(eng._call_openai.call_count, 0)

    def test_si_h4_confirmed_intent_current_moto(self):
        """SI-H4: last_intent=PREPURCHASE. Current: 'en realidad es una moto' → Layer A fires."""
        eng, result, state, _ = _run(
            text="en realidad es una moto",
            last_intent=_INTENT_PREPURCHASE,
        )
        _assert_handled(self, result, "replied")
        self.assertTrue(state.needs_human)
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _FALLBACK_WARM_HANDOFF)


# ══════════════════════════════════════════════════════════════════════════════
# SI-28b–SI-28e: Zero-mutation assertions
# ══════════════════════════════════════════════════════════════════════════════

class TestZeroMutation(unittest.TestCase):
    """Verify that boundary/UNCERTAIN paths skip all commercial pipeline functions."""

    def _assert_no_commercial_plus_zone(self, eng):
        _assert_no_commercial(self, eng)
        self.assertEqual(eng._extract_zone_from_text.call_count, 0,
                         "_extract_zone_from_text must not be called")

    def test_si_28b_f12_no_commercial(self):
        """SI-28b: F12 boundary → no lookup_vehicle, _apply_candidate, _apply_extracted, AI."""
        eng, result, state, _ = _run(text="Necesito hacer un formulario 12")
        self._assert_no_commercial_plus_zone(eng)

    def test_si_28b_uncertain_no_commercial(self):
        """SI-28b: UNCERTAIN → no commercial pipeline."""
        eng, result, state, _ = _run(text="Necesito ayuda con un auto")
        _assert_no_commercial(self, eng)

    def test_si_28b_transfer_no_commercial(self):
        """SI-28b: Transfer boundary → no commercial pipeline."""
        eng, result, state, _ = _run(text="Necesito hacer la transferencia del auto")
        _assert_no_commercial(self, eng)

    def test_si_28b_repair_no_commercial(self):
        """SI-28b: Repair boundary → no commercial pipeline."""
        eng, result, state, _ = _run(
            text="El auto necesita una reparación mecánica, ¿lo pueden arreglar?"
        )
        _assert_no_commercial(self, eng)

    def test_si_28c_f12_no_zone_extraction(self):
        """SI-28c: F12 → _extract_zone_from_text NOT called."""
        eng, result, state, _ = _run(text="Necesito hacer un formulario 12")
        self.assertEqual(eng._extract_zone_from_text.call_count, 0)

    def test_si_28d_uncertain_no_pricing(self):
        """SI-28d: UNCERTAIN → _compute_price_quote NOT called."""
        eng, result, state, _ = _run(text="Necesito ayuda con un auto")
        self.assertEqual(eng._compute_price_quote.call_count, 0)

    def test_si_28e_f12_no_scheduling(self):
        """SI-28e: F12 → _try_schedule_and_flow NOT called."""
        eng, result, state, _ = _run(text="Necesito hacer un formulario 12")
        self.assertEqual(eng._try_schedule_and_flow.call_count, 0)


# ══════════════════════════════════════════════════════════════════════════════
# SI-28f–SI-28g: Regression guards
# ══════════════════════════════════════════════════════════════════════════════

class TestRegressionGuards(unittest.TestCase):

    def test_si_28f_quoted_acceptance_not_gated(self):
        """SI-28f: QUOTED + 'Sí, acepto' (no moto, no phone-call) → acceptance handler runs."""
        eng, result, state, _ = _run(
            text="Sí, acepto",
            stage=STAGE_QUOTED,
        )
        # Acceptance should work: flag moves to ACEPTADO, stage to SCHEDULING
        _assert_handled(self, result, "replied")
        self.assertFalse(state.needs_human)
        # Layer A/B did not fire (no warm handoff)
        self.assertEqual(eng._send_fallback_human_review_notification.call_count, 0)

    def test_si_28g_scheduling_parse_not_gated(self):
        """SI-28g: SCHEDULING + scheduling text → scheduling parse runs; Layer A/A+ do NOT fire."""
        eng, result, state, _ = _run(
            text="el miércoles a las 10",
            stage=STAGE_SCHEDULING,
        )
        _assert_handled(self, result, "replied")
        self.assertFalse(state.needs_human)
        self.assertEqual(eng._send_fallback_human_review_notification.call_count, 0)


# ══════════════════════════════════════════════════════════════════════════════
# SI-PI1–SI-PI5: Persisted intent + override
# ══════════════════════════════════════════════════════════════════════════════

class TestPersistedIntentOverride(unittest.TestCase):

    def test_si_pi1_confirmed_intent_plus_f12_boundary(self):
        """SI-PI1: last_intent=PREPURCHASE + 'Necesito el formulario 12' → F12 boundary; no AI."""
        eng, result, state, _ = _run(
            text="Necesito el formulario 12",
            last_intent=_INTENT_PREPURCHASE,
        )
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _F12_BOUNDARY_REPLY)
        self.assertEqual(eng._call_openai.call_count, 0)

    def test_si_pi2_confirmed_intent_plus_transfer_boundary(self):
        """SI-PI2: last_intent=PREPURCHASE + transfer request → transfer boundary; no AI."""
        eng, result, state, _ = _run(
            text="Necesito que hagan la transferencia",
            last_intent=_INTENT_PREPURCHASE,
        )
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _TRANSFER_BOUNDARY_REPLY)
        self.assertEqual(eng._call_openai.call_count, 0)

    def test_si_pi3_confirmed_intent_plus_repair_boundary(self):
        """SI-PI3: last_intent=PREPURCHASE + '¿Pueden arreglar el freno?' → repair boundary."""
        eng, result, state, _ = _run(
            text="¿Pueden arreglar el freno?",
            last_intent=_INTENT_PREPURCHASE,
        )
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _REPAIR_BOUNDARY_REPLY)
        self.assertEqual(eng._call_openai.call_count, 0)

    def test_si_pi4_confirmed_intent_ford_ka_commercial_steps(self):
        """SI-PI4: last_intent=PREPURCHASE + 'Ford Ka 2019' → Step 6 fast-path; AI called.
        current_focus_candidate_id=1 models the candidate from the prior qualifying turn (M21.1.4-FV guard fix)."""
        eng, result, state, _ = _run(
            text="Ford Ka 2019",
            last_intent=_INTENT_PREPURCHASE,
            state_kwargs={"current_focus_candidate_id": 1},
        )
        _assert_handled(self, result, "replied")
        if eng._send_text_to_wa.called:
            self.assertNotEqual(
                eng._send_text_to_wa.call_args[0][1], _UNCERTAIN_SERVICE_REPLY
            )
        self.assertGreater(eng._call_openai.call_count, 0)

    def test_si_pi5_confirmed_intent_moto_layer_a(self):
        """SI-PI5: last_intent=PREPURCHASE + 'en realidad es una moto' → Layer A."""
        eng, result, state, _ = _run(
            text="en realidad es una moto",
            last_intent=_INTENT_PREPURCHASE,
        )
        _assert_handled(self, result, "replied")
        self.assertTrue(state.needs_human)
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _FALLBACK_WARM_HANDOFF)


# ══════════════════════════════════════════════════════════════════════════════
# SI-PC1–SI-PC6: Phone-call escalation — all stages
# ══════════════════════════════════════════════════════════════════════════════

class TestPhoneCallAllStages(unittest.TestCase):

    def _assert_phone_handoff(self, eng, result, state):
        _assert_handled(self, result, "replied")
        self.assertTrue(state.needs_human)
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _PHONE_CALL_HANDOFF_REPLY)
        self.assertEqual(eng._call_openai.call_count, 0)

    def test_si_pc1_qualifying_phone_call(self):
        """SI-PC1: QUALIFYING + '¿Puedo hablar con alguien?' → escalation; no AI."""
        eng, result, state, _ = _run(text="¿Puedo hablar con alguien?")
        self._assert_phone_handoff(eng, result, state)
        self.assertEqual(eng._compute_price_quote.call_count, 0)

    def test_si_pc2_qualifying_quiero_que_me_llamen(self):
        """SI-PC2: QUALIFYING + 'Quiero que me llamen' → escalation; no candidate; no AI."""
        eng, result, state, _ = _run(text="Quiero que me llamen")
        self._assert_phone_handoff(eng, result, state)
        self.assertEqual(eng._apply_candidate.call_count, 0)
        self.assertEqual(eng._compute_price_quote.call_count, 0)

    def test_si_pc3_quoted_phone_call_before_acceptance(self):
        """SI-PC3: QUOTED + 'Quiero hablar con alguien' → Layer A+ fires before acceptance."""
        eng, result, state, _ = _run(
            text="Quiero hablar con alguien",
            stage=STAGE_QUOTED,
        )
        self._assert_phone_handoff(eng, result, state)

    def test_si_pc4_scheduling_phone_call_before_scheduler(self):
        """SI-PC4: SCHEDULING + '¿Me pueden llamar?' → Layer A+ fires before scheduling."""
        eng, result, state, _ = _run(
            text="¿Me pueden llamar?",
            stage=STAGE_SCHEDULING,
        )
        self._assert_phone_handoff(eng, result, state)
        self.assertEqual(eng._try_schedule_and_flow.call_count, 0)

    def test_si_pc5_kill_switch_phone_call(self):
        """SI-PC5: Kill switch + phone call → human_handoff_blocked; handled=True."""
        eng, result, state, _ = _run(
            text="¿Puedo hablar con alguien?",
            send_raises=True,
        )
        _assert_handled(self, result, "human_handoff_blocked")
        self.assertTrue(state.needs_human)

    def test_si_pc6_moto_plus_phone_call_moto_wins(self):
        """SI-PC6: 'Quiero hablar con alguien sobre una moto' → motorcycle wins (Layer A)."""
        eng, result, state, _ = _run(
            text="Quiero hablar con alguien sobre una moto"
        )
        _assert_handled(self, result, "replied")
        self.assertTrue(state.needs_human)
        # Warm handoff (motorcycle), not phone-call reply
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _FALLBACK_WARM_HANDOFF)
        self.assertTrue(eng._send_fallback_human_review_notification.called)


# ══════════════════════════════════════════════════════════════════════════════
# SI-FAQ1–SI-FAQ8: FAQ bypass (all stages)
# ══════════════════════════════════════════════════════════════════════════════

class TestFAQBypass(unittest.TestCase):

    def _assert_faq_bypass(self, eng, result, state):
        _assert_handled(self, result, "replied")
        self.assertGreater(eng._call_openai.call_count, 0, "_call_openai must be called for FAQ")
        self.assertGreater(eng._build_ai_messages.call_count, 0)
        self.assertEqual(eng._apply_candidate.call_count, 0, "_apply_candidate must not be called")
        self.assertEqual(eng._apply_extracted.call_count, 0, "_apply_extracted must not be called")
        self.assertIsNone(state.last_intent, "state.last_intent must not be set by FAQ bypass")
        self.assertEqual(eng._compute_price_quote.call_count, 0)
        self.assertEqual(eng._extract_zone_from_text.call_count, 0)

    def test_si_faq1_que_incluye(self):
        """SI-FAQ1: '¿Qué incluye la revisión?' → FAQ bypass; no commercial mutation."""
        eng, result, state, _ = _run(text="¿Qué incluye la revisión?")
        self._assert_faq_bypass(eng, result, state)

    def test_si_faq2_cuanto_demora(self):
        """SI-FAQ2: '¿Cuánto demora?' → FAQ bypass; AI called; no commercial."""
        eng, result, state, _ = _run(text="¿Cuánto demora?")
        self._assert_faq_bypass(eng, result, state)

    def test_si_faq3_tengo_que_estar_presente(self):
        """SI-FAQ3: '¿Tengo que estar presente?' → FAQ bypass; state.last_intent NOT set."""
        eng, result, state, _ = _run(text="¿Tengo que estar presente?")
        self._assert_faq_bypass(eng, result, state)

    def test_si_faq4_trabajan_sabados(self):
        """SI-FAQ4: '¿Trabajan los sábados?' → FAQ bypass; AI called."""
        eng, result, state, _ = _run(text="¿Trabajan los sábados?")
        self._assert_faq_bypass(eng, result, state)

    def test_si_faq5_como_se_entrega_informe(self):
        """SI-FAQ5: '¿Cómo se entrega el informe?' → FAQ bypass; AI called."""
        eng, result, state, _ = _run(text="¿Cómo se entrega el informe?")
        self._assert_faq_bypass(eng, result, state)

    def test_si_faq6_hola_consulta_not_uncertain(self):
        """SI-FAQ6: 'Hola, quería hacer una consulta.' → FAQ bypass; no UNCERTAIN reply."""
        eng, result, state, _ = _run(text="Hola, quería hacer una consulta.")
        _assert_handled(self, result, "replied")
        if eng._send_text_to_wa.called:
            self.assertNotEqual(
                eng._send_text_to_wa.call_args[0][1], _UNCERTAIN_SERVICE_REPLY
            )
        self.assertGreater(eng._call_openai.call_count, 0)

    def test_si_faq7_kill_switch_faq(self):
        """SI-FAQ7: Kill switch + FAQ → service_gate_blocked; handled=True."""
        eng, result, state, _ = _run(
            text="¿Cuánto demora la revisión?",
            send_raises=True,
        )
        _assert_handled(self, result, "service_gate_blocked")

    def test_si_faq8_confirmed_intent_faq_still_bypasses(self):
        """SI-FAQ8: last_intent=PREPURCHASE + '¿Cuánto demora?' → FAQ bypass before persisted intent."""
        eng, result, state, _ = _run(
            text="¿Cuánto demora?",
            last_intent=_INTENT_PREPURCHASE,
        )
        # FAQ bypass at Layer D fires before Layer F persisted intent check
        _assert_handled(self, result, "replied")
        self.assertGreater(eng._call_openai.call_count, 0)
        self.assertEqual(eng._apply_candidate.call_count, 0)
        self.assertEqual(eng._compute_price_quote.call_count, 0)
        # last_intent must remain PREPURCHASE (not changed by FAQ bypass)
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)

    def test_si_faq_all_stages_quoted(self):
        """FAQ bypass fires in QUOTED stage before quoted-acceptance handler."""
        eng, result, state, _ = _run(
            text="¿Cuánto demora la revisión?",
            stage=STAGE_QUOTED,
        )
        _assert_handled(self, result, "replied")
        self.assertGreater(eng._call_openai.call_count, 0)
        self.assertEqual(eng._apply_candidate.call_count, 0)

    def test_si_faq_all_stages_scheduling(self):
        """FAQ bypass fires in SCHEDULING stage before scheduling handler."""
        eng, result, state, _ = _run(
            text="¿Cuánto demora la revisión?",
            stage=STAGE_SCHEDULING,
        )
        _assert_handled(self, result, "replied")
        self.assertGreater(eng._call_openai.call_count, 0)
        self.assertEqual(eng._try_schedule_and_flow.call_count, 0)


# ══════════════════════════════════════════════════════════════════════════════
# SI-FAQ-MUT1: FAQ AI mutation guard
# ══════════════════════════════════════════════════════════════════════════════

class TestFAQMutationGuard(unittest.TestCase):

    def test_si_faq_mut1_ai_mutations_ignored(self):
        """SI-FAQ-MUT1: FAQ AI returns candidate/extracted/flag/needs_human → all ignored."""
        ai_response = json.dumps({
            "reply": "La revisión dura aproximadamente una hora.",
            "candidate": {"action": "create", "tipo_vehiculo": "AUTO"},
            "extracted": {"zone_detail": "Palermo"},
            "lead_flag": "PRESUPUESTO_ENVIADO",
            "needs_human": True,
        })
        eng, result, state, lead = _run(
            text="¿Cuánto demora?",
            ai_response=ai_response,
        )
        _assert_handled(self, result, "replied")

        # Only the reply is sent
        self.assertIn("una hora", eng._send_text_to_wa.call_args[0][1])

        # Mutations are ignored
        self.assertEqual(eng._apply_candidate.call_count, 0)
        self.assertEqual(eng._apply_extracted.call_count, 0)
        self.assertEqual(lead.flag, "PRESUPUESTANDO")  # unchanged
        self.assertFalse(state.needs_human)            # unchanged
        self.assertIsNone(state.last_intent)           # not set


# ══════════════════════════════════════════════════════════════════════════════
# SI-CLA-R1–R2: Clause-aware repair detection
# ══════════════════════════════════════════════════════════════════════════════

class TestClauseAwareRepair(unittest.TestCase):

    def test_si_cla_r1_mechanic_context_pre_purchase_not_repair(self):
        """SI-CLA-R1: Mechanic context + 'quiero revisar antes de comprarlo' → NOT repair."""
        eng, result, state, _ = _run(
            text="Mi mecánico no puede venir, quiero revisar el auto antes de comprarlo."
        )
        _assert_handled(self, result, "replied")
        if eng._send_text_to_wa.called:
            self.assertNotEqual(
                eng._send_text_to_wa.call_args[0][1], _REPAIR_BOUNDARY_REPLY
            )
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)

    def test_si_cla_r2_mechanic_context_with_explicit_repair(self):
        """SI-CLA-R2: 'Mi mecánico no puede venir, pero necesito que ustedes reparen.' → repair."""
        eng, result, state, _ = _run(
            text="Mi mecánico no puede venir, pero necesito que ustedes reparen el auto."
        )
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _REPAIR_BOUNDARY_REPLY)
        self.assertEqual(eng._call_openai.call_count, 0)


# ══════════════════════════════════════════════════════════════════════════════
# SI-CLA-T1–T2: Clause-aware transfer detection
# ══════════════════════════════════════════════════════════════════════════════

class TestClauseAwareTransfer(unittest.TestCase):

    def test_si_cla_t1_future_step_not_transfer(self):
        """SI-CLA-T1: In-progress convo, 'Después de la revisión voy a hacer la transferencia.' → NOT transfer; AI called."""
        # last_intent=PREPURCHASE models a realistic in-progress conversation
        eng, result, state, _ = _run(
            text="Después de la revisión voy a hacer la transferencia.",
            last_intent=_INTENT_PREPURCHASE,
        )
        _assert_handled(self, result, "replied")
        if eng._send_text_to_wa.called:
            self.assertNotEqual(
                eng._send_text_to_wa.call_args[0][1], _TRANSFER_BOUNDARY_REPLY
            )
        self.assertGreater(eng._call_openai.call_count, 0)

    def test_si_cla_t2_explicit_ridecheck_transfer_wins(self):
        """SI-CLA-T2: Exclusion context + explicit RideCheck request → transfer boundary."""
        eng, result, state, _ = _run(
            text="Después de la revisión necesito que ustedes hagan la transferencia."
        )
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _TRANSFER_BOUNDARY_REPLY)
        self.assertEqual(eng._call_openai.call_count, 0)


# ══════════════════════════════════════════════════════════════════════════════
# SI-MX1–SI-MX4: Mixed intent
# ══════════════════════════════════════════════════════════════════════════════

class TestMixedIntent(unittest.TestCase):

    def test_si_mx1_pre_purchase_plus_transfer_transfer_wins(self):
        """SI-MX1: Pre-purchase + transfer → transfer boundary; _compute_price_quote NOT called."""
        eng, result, state, _ = _run(
            text="Quiero revisar el auto antes de comprarlo y también necesito que ustedes hagan la transferencia"
        )
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _TRANSFER_BOUNDARY_REPLY)
        self.assertEqual(eng._compute_price_quote.call_count, 0)

    def test_si_mx2_pre_purchase_pronoun_repair_wins(self):
        """SI-MX2: 'Antes de comprarlo necesito que me lo reparen' → repair boundary; not pre-purchase."""
        eng, result, state, _ = _run(
            text="Antes de comprarlo necesito que me lo reparen"
        )
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _REPAIR_BOUNDARY_REPLY)
        self.assertEqual(eng._call_openai.call_count, 0)
        self.assertNotEqual(state.last_intent, _INTENT_PREPURCHASE)

    def test_si_mx3_f12_past_context_plus_pre_purchase(self):
        """SI-MX3: 'Ya hice el F12 y ahora quiero una revisión precompra' → pre-purchase; AI called."""
        eng, result, state, _ = _run(
            text="Ya hice el Formulario 12 y ahora quiero una revisión precompra"
        )
        _assert_handled(self, result, "replied")
        if eng._send_text_to_wa.called:
            self.assertNotEqual(
                eng._send_text_to_wa.call_args[0][1], _F12_BOUNDARY_REPLY
            )
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)
        self.assertGreater(eng._call_openai.call_count, 0)

    def test_si_mx4_f12_past_context_plus_new_f12_request(self):
        """SI-MX4: 'Ya hice F12, pero necesito otro' → F12 boundary."""
        eng, result, state, _ = _run(
            text="Ya hice un Formulario 12, pero necesito otro y quiero que ustedes lo gestionen"
        )
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _F12_BOUNDARY_REPLY)
        self.assertEqual(eng._call_openai.call_count, 0)


# ══════════════════════════════════════════════════════════════════════════════
# HANDLED_ACTIONS schema check
# ══════════════════════════════════════════════════════════════════════════════

class TestHandledActionsSchema(unittest.TestCase):

    def test_human_handoff_blocked_in_handled_actions(self):
        self.assertIn("human_handoff_blocked", HANDLED_ACTIONS)

    def test_service_gate_blocked_in_handled_actions(self):
        self.assertIn("service_gate_blocked", HANDLED_ACTIONS)

    def test_existing_actions_still_present(self):
        for action in ("replied", "flow_button_sent", "booking_created", "skipped_human", "skipped_dedup"):
            self.assertIn(action, HANDLED_ACTIONS)


# ══════════════════════════════════════════════════════════════════════════════
# Addendum: All-stage service boundary (QUOTED/SCHEDULING stages)
# ══════════════════════════════════════════════════════════════════════════════

class TestAllStageServiceBoundary(unittest.TestCase):
    """F12/transfer/repair boundaries fire in QUOTED and SCHEDULING stages (addendum)."""

    def test_f12_in_quoted_stage(self):
        """F12 boundary fires in QUOTED stage before quoted-acceptance handler."""
        eng, result, state, _ = _run(
            text="Necesito el formulario 12",
            stage=STAGE_QUOTED,
        )
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _F12_BOUNDARY_REPLY)
        self.assertEqual(eng._call_openai.call_count, 0)

    def test_transfer_in_quoted_stage(self):
        """Transfer boundary fires in QUOTED stage."""
        eng, result, state, _ = _run(
            text="Necesito que hagan la transferencia",
            stage=STAGE_QUOTED,
        )
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _TRANSFER_BOUNDARY_REPLY)

    def test_repair_in_scheduling_stage(self):
        """Repair boundary fires in SCHEDULING stage before scheduling handler."""
        eng, result, state, _ = _run(
            text="¿Pueden arreglar el freno antes de la revisión?",
            stage=STAGE_SCHEDULING,
        )
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _REPAIR_BOUNDARY_REPLY)
        self.assertEqual(eng._try_schedule_and_flow.call_count, 0)

    def test_f12_in_scheduling_stage(self):
        """F12 boundary fires in SCHEDULING stage."""
        eng, result, state, _ = _run(
            text="Necesito el formulario 12 urgente",
            stage=STAGE_SCHEDULING,
        )
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _F12_BOUNDARY_REPLY)


# ══════════════════════════════════════════════════════════════════════════════
# Addendum: Motorcycle plural forms
# ══════════════════════════════════════════════════════════════════════════════

class TestMotorcyclePluralForms(unittest.TestCase):
    """Addendum: plural forms must be detected (motos, motocicletas, scooters, etc.)."""

    def _assert_moto(self, text):
        eng, result, state, _ = _run(text=text)
        self.assertTrue(state.needs_human, f"expected motorcycle handoff for: {text!r}")
        self.assertEqual(result.action, "replied")

    def test_motos_plural(self):
        self._assert_moto("Tengo dos motos que me gustan")

    def test_motocicletas_plural(self):
        self._assert_moto("Son motocicletas de competición")

    def test_scooters_plural(self):
        self._assert_moto("Importamos scooters eléctricos")

    def test_quads_plural(self):
        self._assert_moto("Vendo quads usados")

    def test_atvs_plural(self):
        self._assert_moto("Tenemos atvs disponibles")

    def test_cuatriciclos_plural(self):
        self._assert_moto("Son cuatriciclos para campo")


# ══════════════════════════════════════════════════════════════════════════════
# R1-01–R1-17: M21.1.1-R1 Primary Inspection Flow Restore
# ══════════════════════════════════════════════════════════════════════════════

class TestR1PrimaryInspectionFlowRestore(unittest.TestCase):
    """M21.1.1-R1: Validates the 9-step Layer F _handle_qualifying_intent logic."""

    # ── Steps 1-2: Explicit inspection request / pre-purchase signal ──────────

    def test_r1_01_explicit_inspection_request_sets_intent(self):
        """R1-01: Explicit inspection 'Quiero que revisen el auto' → step 1 sets intent → AI called."""
        eng, result, state, _ = _run(text="Quiero que revisen el auto")
        _assert_handled(self, result, "replied")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)
        self.assertEqual(eng._call_openai.call_count, 1)

    def test_r1_02_explicit_inspection_request_hacer_revision(self):
        """R1-02: 'Necesito hacer una revisión del vehículo' → step 1 sets intent → AI called."""
        eng, result, state, _ = _run(text="Necesito hacer una revisión del vehículo")
        _assert_handled(self, result, "replied")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)
        self.assertEqual(eng._call_openai.call_count, 1)

    def test_r1_03_new_prepurchase_signal_estoy_por_comprar(self):
        """R1-03: 'Estoy por comprar el auto' → R1 prepurchase signal → step 2 sets intent → AI called."""
        eng, result, state, _ = _run(text="Estoy por comprar el auto")
        _assert_handled(self, result, "replied")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)
        self.assertEqual(eng._call_openai.call_count, 1)

    # ── Step 3: FAQ / soft-close phrases (pass-through, intent not set) ───────

    def test_r1_04_soft_close_gracias_passes_through(self):
        """R1-04: 'Gracias' → soft-close step 3 → pass-through without setting intent → AI called."""
        eng, result, state, _ = _run(text="Gracias")
        _assert_handled(self, result, "replied")
        self.assertIsNone(state.last_intent, "Step 3 must not set last_intent")
        self.assertEqual(eng._call_openai.call_count, 1)

    def test_r1_05_soft_close_los_tengo_en_cuenta(self):
        """R1-05: 'Los tengo en cuenta' → soft-close step 3 → pass-through → AI called."""
        eng, result, state, _ = _run(text="Los tengo en cuenta")
        _assert_handled(self, result, "replied")
        self.assertIsNone(state.last_intent)
        self.assertEqual(eng._call_openai.call_count, 1)

    def test_r1_09_me_pasas_info_soft_close(self):
        """R1-09: '¿Me pasás info?' → soft-close step 3 (R1 addition) → pass-through → AI called."""
        eng, result, state, _ = _run(text="¿Me pasás info?")
        _assert_handled(self, result, "replied")
        self.assertIsNone(state.last_intent)
        self.assertEqual(eng._call_openai.call_count, 1)

    # ── Step 4: Vehicle catalog hit ────────────────────────────────────────────

    def test_r1_06_vehicle_catalog_hit_passes_through(self):
        """R1-06: Recognized vehicle name → step 4 catalog hit → CONTINUE_SET → intent set → AI called (BR-1 Row 5)."""
        _mock_v = types.SimpleNamespace(
            matched_alias="Ford Ranger",
            tipo_vehiculo="AUTO",
            confidence=0.9,
        )
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state=state, lead=_make_lead())
        event = _make_event(text="Ford Ranger 2020")
        with patch("app.services.conversation_engine.lookup_vehicle", return_value=_mock_v):
            result = eng._process_text(ctx, event)
        _assert_handled(self, result, "replied")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE, "Step 4 catalog hit must set last_intent")
        self.assertEqual(eng._call_openai.call_count, 1)

    # ── Step 5: Vehicle-location phrase ("está en/por X") ─────────────────────

    def test_r1_07_vehicle_location_phrase_passes_through(self):
        """R1-07: 'El auto está en Palermo' → step 5 vehicle-location → CONTINUE_SET → intent set → AI called (BR-1 Row 7)."""
        eng, result, state, _ = _run(text="El auto está en Palermo")
        _assert_handled(self, result, "replied")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE, "Step 5 vehicle-location must set last_intent")
        self.assertEqual(eng._call_openai.call_count, 1)

    # ── Step 6: Price question ─────────────────────────────────────────────────

    def test_r1_08_price_question_passes_through(self):
        """R1-08: '¿Cuánto sale la revisión?' → inspection-specific price step 6 → intent set → AI called.
        M21.1.1-R2: step 6 now sets last_intent=PREPURCHASE_INSPECTION (was None in R1)."""
        eng, result, state, _ = _run(text="¿Cuánto sale la revisión?")
        _assert_handled(self, result, "replied")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE, "Step 6 must set last_intent in R2")
        self.assertEqual(eng._call_openai.call_count, 1)

    # ── Step 7: Prior persisted intent ────────────────────────────────────────

    def test_r1_10_persisted_intent_passes_through(self):
        """R1-10: last_intent=PREPURCHASE_INSPECTION from prior turn → step 7 → pass-through → AI called."""
        eng, result, state, _ = _run(
            text="Está bien, dale",
            last_intent=_INTENT_PREPURCHASE,
        )
        _assert_handled(self, result, "replied")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)
        self.assertEqual(eng._call_openai.call_count, 1)

    # ── Step 8: Established inspection context ────────────────────────────────

    def test_r1_11_established_context_via_candidate(self):
        """R1-11: Non-moto candidate in ctx → step 8 established context → AI called."""
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state=state, lead=_make_lead())
        ctx.candidates = [types.SimpleNamespace(tipo_vehiculo="AUTO")]
        event = _make_event(text="Perfecto")
        with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
            result = eng._process_text(ctx, event)
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._call_openai.call_count, 1)

    def test_r1_17_established_context_via_clarification_flag(self):
        """R1-17: location_clarification_sent=True → step 8 established context → AI called (BR-1 §6).
        Clarification Flows sent because of an accepted message are valid provenance."""
        eng = _make_engine()
        state = _make_state(location_clarification_sent=True)
        ctx = _make_ctx(state=state, lead=_make_lead())
        event = _make_event(text="A ver qué fechas tienen")
        with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
            result = eng._process_text(ctx, event)
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._call_openai.call_count, 1, "Clarification flag is valid provenance per BR-1 §6")

    # ── Step 9: Negative controls — UNCERTAIN fires ───────────────────────────

    def test_r1_12_negative_no_signals_uncertain(self):
        """R1-12: No qualifying signal → step 9 UNCERTAIN → _UNCERTAIN_SERVICE_REPLY sent; AI NOT called."""
        eng, result, state, _ = _run(text="A ver qué hago")
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._call_openai.call_count, 0, "AI must not be called on UNCERTAIN")
        self.assertEqual(
            eng._send_text_to_wa.call_args[0][1],
            _UNCERTAIN_SERVICE_REPLY,
            "Must send _UNCERTAIN_SERVICE_REPLY",
        )

    def test_r1_13_negative_no_signals_uncertain_v2(self):
        """R1-13: Generic hesitation text → step 9 UNCERTAIN → AI NOT called."""
        eng, result, state, _ = _run(text="Déjame pensarlo")
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._call_openai.call_count, 0)
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _UNCERTAIN_SERVICE_REPLY)

    # ── Kill switch + R1 pass-through conditions ──────────────────────────────

    def test_r1_14_kill_switch_explicit_inspection_ai_path(self):
        """R1-14: Kill switch + explicit inspection → gate passes → AI called → OutboundBlockedError."""
        eng = _make_engine(send_raises=True)
        state = _make_state()
        ctx = _make_ctx(state=state, lead=_make_lead())
        event = _make_event(text="Quiero que revisen el auto")
        with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
            with self.assertRaises(OutboundBlockedError):
                eng._process_text(ctx, event)
        self.assertEqual(eng._call_openai.call_count, 1, "AI must be called before kill switch fires")

    def test_r1_15_kill_switch_catalog_hit_ai_path(self):
        """R1-15: Kill switch + vehicle catalog hit → gate passes → AI called → OutboundBlockedError."""
        _mock_v = types.SimpleNamespace(
            matched_alias="Honda Fit",
            tipo_vehiculo="AUTO",
            confidence=0.9,
        )
        eng = _make_engine(send_raises=True)
        state = _make_state()
        ctx = _make_ctx(state=state, lead=_make_lead())
        event = _make_event(text="Honda Fit 2018")
        with patch("app.services.conversation_engine.lookup_vehicle", return_value=_mock_v):
            with self.assertRaises(OutboundBlockedError):
                eng._process_text(ctx, event)
        self.assertEqual(eng._call_openai.call_count, 1)

    def test_r1_16_kill_switch_price_question_ai_path(self):
        """R1-16: Kill switch + price question → gate passes → AI called → OutboundBlockedError."""
        eng = _make_engine(send_raises=True)
        state = _make_state()
        ctx = _make_ctx(state=state, lead=_make_lead())
        event = _make_event(text="¿Cuánto sale la revisión?")
        with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
            with self.assertRaises(OutboundBlockedError):
                eng._process_text(ctx, event)
        self.assertEqual(eng._call_openai.call_count, 1)


class TestR2OptInBoundaryRestore(unittest.TestCase):
    """M21.1.1-R2: Verify opt-in boundary tightening — fresh-thread bare values → UNCERTAIN;
    only strong signals pass Layer F."""

    # ── UNCERTAIN: bare values on fresh thread ────────────────────────────────

    def test_r2_01_bare_vehicle_name_sets_intent(self):
        """R2-01: Bare vehicle name with catalog hit → step 4 CONTINUE_SET → last_intent set → AI called (BR-1 Row 5)."""
        _mock_v = types.SimpleNamespace(matched_alias="Ford Ranger", tipo_vehiculo="AUTO", confidence=0.9)
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state=state, lead=_make_lead())
        event = _make_event(text="Ford Ranger 2020")
        with patch("app.services.conversation_engine.lookup_vehicle", return_value=_mock_v):
            result = eng._process_text(ctx, event)
        _assert_handled(self, result, "replied")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE, "Catalog hit must set last_intent")
        self.assertEqual(eng._call_openai.call_count, 1)

    def test_r2_02_bare_location_uncertain(self):
        """R2-02: Bare zone name 'Palermo' → no signal → step 9 UNCERTAIN."""
        eng, result, state, _ = _run(text="Palermo")
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._call_openai.call_count, 0)
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _UNCERTAIN_SERVICE_REPLY)

    def test_r2_03_vehicle_location_phrase_passes_via_step5(self):
        """R2-03: 'El auto está en Palermo' → step 5 vehicle-location → CONTINUE_SET → intent set → AI called (BR-1 Row 7)."""
        eng, result, state, _ = _run(text="El auto está en Palermo")
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._call_openai.call_count, 1)
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE, "Step 5 vehicle-location must set last_intent")

    def test_r2_04_bare_price_question_sets_intent(self):
        """R2-04: '¿Cuánto sale?' → step 6 broad price → CONTINUE_SET → intent set → AI called (BR-1 Row 4)."""
        eng, result, state, _ = _run(text="¿Cuánto sale?")
        _assert_handled(self, result, "replied")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE, "Broad price must set last_intent")
        self.assertEqual(eng._call_openai.call_count, 1)

    # ── Pass-through: strong signals ─────────────────────────────────────────

    def test_r2_05_inspection_price_question_sets_intent(self):
        """R2-05: '¿Cuánto sale la revisión?' → step 6 inspection-specific price → intent set → AI called."""
        eng, result, state, _ = _run(text="¿Cuánto sale la revisión?")
        _assert_handled(self, result, "replied")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE, "Step 6 must set last_intent")
        self.assertEqual(eng._call_openai.call_count, 1)

    def test_r2_06_quiero_cotizar_passes_step1(self):
        """R2-06: 'Quiero cotizar un auto' → R2 addition to _INSPECTION_REQUEST_PATTERNS → step 1 → intent set."""
        eng, result, state, _ = _run(text="Quiero cotizar un auto")
        _assert_handled(self, result, "replied")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)
        self.assertEqual(eng._call_openai.call_count, 1)

    def test_r2_07_greeting_with_price_sets_intent(self):
        """R2-07: 'Hola, cuánto sale?' → step 6 broad price → CONTINUE_SET → intent set → AI called (BR-1 Row 4)."""
        eng, result, state, _ = _run(text="Hola, cuánto sale?")
        _assert_handled(self, result, "replied")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE, "Price signal in greeting must set last_intent")
        self.assertEqual(eng._call_openai.call_count, 1)

    def test_r2_08_non_moto_candidate_passes_step8(self):
        """R2-08: Non-moto candidate in ctx + bare continuation text → step 8 → AI called."""
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state=state, lead=_make_lead())
        ctx.candidates = [types.SimpleNamespace(tipo_vehiculo="AUTO")]
        event = _make_event(text="A ver cómo sigue")
        with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
            result = eng._process_text(ctx, event)
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._call_openai.call_count, 1)

    # ── Narrowed step 8: flags and zone alone are INSUFFICIENT ───────────────

    def test_r2_09_home_zone_group_alone_insufficient(self):
        """R2-09: home_zone_group set + no candidate + bare text → step 9 UNCERTAIN.
        R2 removes home_zone_group from step 8; zone alone is not proof of inspection intent."""
        eng = _make_engine()
        state = _make_state(home_zone_group="sur")
        ctx = _make_ctx(state=state, lead=_make_lead())
        event = _make_event(text="Perfecto")
        with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
            result = eng._process_text(ctx, event)
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._call_openai.call_count, 0, "home_zone_group alone must not bypass UNCERTAIN")
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _UNCERTAIN_SERVICE_REPLY)

    def test_r2_10_clarification_flag_is_provenance(self):
        """R2-10: location_clarification_sent=True → step 8 established context → AI called (BR-1 §6).
        Clarification Flows sent because of an accepted message are valid provenance."""
        eng = _make_engine()
        state = _make_state(location_clarification_sent=True)
        ctx = _make_ctx(state=state, lead=_make_lead())
        event = _make_event(text="A ver qué fechas tienen")
        with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
            result = eng._process_text(ctx, event)
        _assert_handled(self, result, "replied")
        self.assertEqual(eng._call_openai.call_count, 1, "Clarification flag is valid provenance per BR-1 §6")

    # ── BR-1 exception: vehicle + FAQ cannot be blocked ──────────────────────

    def test_r2_11_faq_with_vehicle_sets_intent(self):
        """R2-11: 'Es un Focus 2019, ¿qué revisan?' → step 3 FAQ+vehicle → FAQ_WITH_CONTEXT → intent set → AI called (BR-1 Row 16)."""
        _mock_v = types.SimpleNamespace(matched_alias="Ford Focus", tipo_vehiculo="AUTO", confidence=0.9)
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state=state, lead=_make_lead())
        event = _make_event(text="Es un Focus 2019, ¿qué revisan?")
        with patch("app.services.conversation_engine.lookup_vehicle", return_value=_mock_v):
            result = eng._process_text(ctx, event)
        _assert_handled(self, result, "replied")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE, "FAQ+vehicle must set last_intent per BR-1 Row 16")
        self.assertEqual(eng._call_openai.call_count, 1)

    def test_r2_12_explicit_inspection_with_vehicle_passes_step1(self):
        """R2-12: 'Quiero revisar un Focus 2019' → step 1 explicit inspection → intent set → AI called."""
        _mock_v = types.SimpleNamespace(matched_alias="Ford Focus", tipo_vehiculo="AUTO", confidence=0.9)
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state=state, lead=_make_lead())
        event = _make_event(text="Quiero revisar un Focus 2019")
        with patch("app.services.conversation_engine.lookup_vehicle", return_value=_mock_v):
            result = eng._process_text(ctx, event)
        _assert_handled(self, result, "replied")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)
        self.assertEqual(eng._call_openai.call_count, 1)


if __name__ == "__main__":
    unittest.main()
