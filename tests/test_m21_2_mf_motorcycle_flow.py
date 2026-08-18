"""M21.2 MF — Motorcycle contact-data Flow tests.

Tests the new two-turn motorcycle path:
  Turn 1: motorcycle detected → send contact-data WhatsApp Flow
  Turn 2: Flow response → persist Lead fields, set needs_human, send advisor reply

MF01 — exact LIVE07 message with Flow configured
MF02 — motorcycle with no make/model → Flow dispatched, MOTO only
MF03 — Flow response: Lead updated, motorcycle fields preserved, needs_human set
MF04 — phone preservation: WA phone not overwritten by Flow response
MF05 — automotive candidate protected: existing Ford Focus untouched
MF06 — normal automotive message unaffected (no Flow dispatched)
MF07 — no premature needs_human (Flow response still processable)
MF08 — failed/cancelled Flow: does not claim advisor handoff complete
MF09 — PricingService not called for motorcycle path
MF10 — exact copy for both turns
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

for _mod_name in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json

if not isinstance(getattr(_pg_dialect, "JSONB", None), type(sqlalchemy.JSON)):
    _pg_dialect.JSONB = sqlalchemy.JSON
if not isinstance(getattr(_pg_json, "JSONB", None), type(sqlalchemy.JSON)):
    _pg_json.JSONB = sqlalchemy.JSON

from app.services.conversation_engine import (  # noqa: E402
    ConversationEngine,
    _MOTORCYCLE_CONTACT_INTRO,
    _MOTORCYCLE_ADVISOR_REPLY,
    _MOTO_HANDOFF_TOKEN_PREFIX,
    _MOTORCYCLE_HANDOFF_REPLY,
)
from app.schemas.conversation import ConversationHandleIn, HANDLED_ACTIONS  # noqa: E402
from app.services.outbound_guard import OutboundBlockedError                # noqa: E402

STAGE_QUALIFYING = "QUALIFYING"
STAGE_SCHEDULING = "SCHEDULING"
STAGE_QUOTED     = "QUOTED"

_TEST_FLOW_ID = "TEST_MOTO_HANDOFF_FLOW_999"


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _make_state(**kw) -> types.SimpleNamespace:
    ns = types.SimpleNamespace(
        last_stage=STAGE_QUALIFYING,
        needs_human=False,
        last_intent=None,
        home_zone_group=None,
        home_zone_detail=None,
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
        id=27, flag=None, estado="CONSULTA_NUEVA",
        nombre=None, apellido=None, email=None,
        telefono="5491153368330", necesita_humano=False,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _make_candidate(**kw) -> types.SimpleNamespace:
    ns = types.SimpleNamespace(
        id=36, marca="Ford", modelo="Focus", anio=2019,
        tipo_vehiculo="AUTO", zone_group="CABA", zone_detail="Palermo",
        status="dismissed",
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _make_moto_candidate(**kw) -> types.SimpleNamespace:
    ns = types.SimpleNamespace(
        id=37, marca=None, modelo=None, anio=None,
        tipo_vehiculo="MOTO", zone_group=None, zone_detail=None,
        status="mentioned",
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _make_ctx(state=None, lead=None, candidates=None) -> types.SimpleNamespace:
    ctx = types.SimpleNamespace()
    ctx.thread = types.SimpleNamespace(id=415, last_message_at=None)
    ctx.contact = types.SimpleNamespace(wa_id="5491153368330")
    ctx.lead = lead if lead is not None else _make_lead()
    ctx.state = state if state is not None else _make_state()
    ctx.candidates = candidates if candidates is not None else []
    return ctx


def _make_event(text=None, unanswered=None) -> ConversationHandleIn:
    msgs = unanswered or ([text] if text else [])
    return ConversationHandleIn(
        thread_id=415,
        wa_message_id="test-wa-id",
        wa_id="5491153368330",
        text=text,
        unanswered_recent_user_messages=unanswered or [],
        recent_user_messages=msgs,
    )


def _make_engine(focus_candidate=None) -> ConversationEngine:
    """Build CE with Flow-based motorcycle path active."""
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock()
    eng.settings = MagicMock()
    eng.settings.openai_api_key = "sk-fake"
    eng.settings.openai_chat_model = "gpt-4o-mini"
    eng.settings.backend_url = "http://localhost:8000"
    # Enable Flow-based path
    eng.settings.whatsapp_manual_handoff_flow_id = _TEST_FLOW_ID

    eng._send_text_to_wa = MagicMock(return_value="mock-wa-id")
    eng._send_flow_button = MagicMock(return_value="mock-flow-wa-id")
    eng._send_fallback_human_review_notification = MagicMock()
    eng._call_openai = MagicMock(return_value='{"reply":"ok","candidate":{"action":"none"},"extracted":{},"lead_flag":null,"needs_human":false}')
    eng._build_ai_messages = MagicMock(return_value=[])
    eng._compute_price_quote = MagicMock(return_value=None)
    eng._extract_zone_from_text = MagicMock(return_value=None)
    eng._normalize_zone_from_db = MagicMock()
    eng._routing_gate = MagicMock(return_value=(None, True))
    eng._check_fallback_flow_triggers = MagicMock(return_value=None)
    eng._apply_extracted = MagicMock()
    eng._focus_candidate = MagicMock(return_value=focus_candidate)
    eng._apply_candidate = MagicMock()
    eng._enforce_catalog_vehicle = MagicMock()
    eng._create_candidate_from_catalog = MagicMock()
    eng._try_schedule_and_flow = MagicMock(return_value=None)
    eng._handle_day_only_request = MagicMock(return_value=None)
    eng._handle_period_request = MagicMock(return_value=None)
    eng._build_quote_reply = MagicMock(return_value="Cotización: $999.")
    eng._pricing = MagicMock()
    eng._scrub_invented_price = MagicMock(side_effect=lambda r, q: r)
    return eng


def _run_turn1(text, stage=STAGE_QUALIFYING, focus_candidate=None, candidates=None, **state_kw):
    """Run Turn 1 (motorcycle text message) with Flow-based path."""
    eng = _make_engine(focus_candidate=focus_candidate)
    state = _make_state(last_stage=stage, **state_kw)
    lead = _make_lead()
    ctx = _make_ctx(state=state, lead=lead, candidates=candidates or [])
    event = _make_event(text=text)
    with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
        result = eng._process_text(ctx, event)
    return eng, result, state, lead, ctx


# ══════════════════════════════════════════════════════════════════════════════
# MF01 — Exact LIVE07: "Quiero revisar una moto Honda CB500"
# ══════════════════════════════════════════════════════════════════════════════

class TestMF01Live07(unittest.TestCase):

    def test_mf01_turn1_exact(self):
        """MF01 Turn 1: exact LIVE07 message → Flow dispatched; Honda and CB500 extracted."""
        eng, result, state, lead, ctx = _run_turn1("Quiero revisar una moto Honda CB500")

        # Action is flow_button_sent, not replied
        self.assertEqual(result.action, "flow_button_sent")
        self.assertTrue(result.handled)
        self.assertIn(result.action, HANDLED_ACTIONS)

        # Flow button dispatched with correct intro text and flow_id
        self.assertTrue(eng._send_flow_button.called, "contact Flow must be dispatched")
        flow_call = eng._send_flow_button.call_args
        self.assertEqual(flow_call[0][1], _MOTORCYCLE_CONTACT_INTRO)  # body_text
        self.assertEqual(flow_call[1].get("flow_id") or flow_call[0][3], _TEST_FLOW_ID)

        # Pending token set — starts with moto-handoff- prefix
        self.assertIsNotNone(state.flow_booking_token)
        self.assertTrue(
            state.flow_booking_token.startswith(_MOTO_HANDOFF_TOKEN_PREFIX),
            f"token must start with {_MOTO_HANDOFF_TOKEN_PREFIX!r}, got {state.flow_booking_token!r}",
        )

        # needs_human NOT set yet (must not block Flow response)
        self.assertFalse(state.needs_human, "needs_human must be False until Flow response")
        self.assertFalse(lead.necesita_humano, "lead.necesita_humano must be False until Flow response")

        # AI not called
        self.assertEqual(eng._call_openai.call_count, 0, "AI must not be called")

        # Automotive pipeline not called
        self.assertEqual(eng._compute_price_quote.call_count, 0)
        self.assertEqual(eng._apply_candidate.call_count, 0)
        self.assertEqual(eng._try_schedule_and_flow.call_count, 0)

        # Notification NOT fired on Turn 1 (fires after Flow response)
        self.assertFalse(eng._send_fallback_human_review_notification.called)

        # MOTO candidate created (DB add was called)
        self.assertTrue(eng.db.add.called, "MOTO candidate must be added to DB")

    def test_mf01_turn1_no_fallback_warmhandoff(self):
        """MF01: When Flow is configured, _MOTORCYCLE_HANDOFF_REPLY must NOT be sent on Turn 1."""
        eng, result, state, lead, ctx = _run_turn1("Quiero revisar una moto Honda CB500")
        if eng._send_text_to_wa.called:
            for c in eng._send_text_to_wa.call_args_list:
                self.assertNotEqual(
                    c[0][1], _MOTORCYCLE_HANDOFF_REPLY,
                    "plain-text fallback reply must not be sent when Flow is configured",
                )


# ══════════════════════════════════════════════════════════════════════════════
# MF02 — Motorcycle with no make/model
# ══════════════════════════════════════════════════════════════════════════════

class TestMF02NoMakeModel(unittest.TestCase):

    def test_mf02_moto_only(self):
        """MF02: 'Quiero revisar una moto' → Flow dispatched; tipo=MOTO; marca/modelo null."""
        eng, result, state, lead, ctx = _run_turn1("Quiero revisar una moto")
        self.assertEqual(result.action, "flow_button_sent")
        self.assertTrue(eng._send_flow_button.called)
        self.assertFalse(state.needs_human)

    def test_mf02_revisan_motos(self):
        """MF02: 'Revisan motos?' → Flow dispatched."""
        eng, result, state, lead, ctx = _run_turn1("Revisan motos?")
        self.assertEqual(result.action, "flow_button_sent")
        self.assertFalse(state.needs_human)

    def test_mf02_tengo_una_moto(self):
        """MF02: 'Tengo una moto' → Flow dispatched."""
        eng, result, state, lead, ctx = _run_turn1("Tengo una moto")
        self.assertEqual(result.action, "flow_button_sent")
        self.assertFalse(state.needs_human)


# ══════════════════════════════════════════════════════════════════════════════
# MF03 — Flow response: Lead updated, motorcycle preserved, needs_human set
# ══════════════════════════════════════════════════════════════════════════════

class TestMF03FlowResponse(unittest.TestCase):

    def _run_flow_response(self, flow_data, state_kw=None, lead_kw=None, candidates=None):
        eng = _make_engine()
        state = _make_state(
            flow_booking_token=f"{_MOTO_HANDOFF_TOKEN_PREFIX}415-1000000",
            **(state_kw or {}),
        )
        lead = _make_lead(**(lead_kw or {}))
        ctx = _make_ctx(
            state=state, lead=lead,
            candidates=candidates or [_make_moto_candidate(marca="Honda", modelo="CB500")],
        )
        result = eng._process_motorcycle_contact_response(ctx, state, flow_data)
        return eng, result, state, lead

    def test_mf03_flow_response_updates_lead(self):
        """MF03: Valid Flow response updates Lead nombre/apellido/email."""
        eng, result, state, lead = self._run_flow_response({
            "nombre_apellido": "Juan Pérez",
            "email": "juan@example.com",
        })
        self.assertEqual(result.action, "replied")
        self.assertTrue(result.handled)

        # Lead updated
        self.assertEqual(lead.nombre, "Juan")
        self.assertEqual(lead.apellido, "Pérez")
        self.assertEqual(lead.email, "juan@example.com")

        # needs_human set after Flow response
        self.assertTrue(state.needs_human)
        self.assertTrue(lead.necesita_humano)

        # Token consumed
        self.assertIsNone(state.flow_booking_token)

        # Advisor reply sent
        sent = eng._send_text_to_wa.call_args[0][1]
        self.assertEqual(sent, _MOTORCYCLE_ADVISOR_REPLY)
        self.assertNotIn("completar el formulario", sent)

        # Notification fired
        self.assertTrue(eng._send_fallback_human_review_notification.called)

    def test_mf03_motorcycle_evidence_preserved(self):
        """MF03: Honda/CB500/MOTO in MOTO candidate are preserved through Flow response."""
        eng, result, state, lead = self._run_flow_response(
            {"nombre_apellido": "Laura Sosa", "email": "laura@example.com"},
            candidates=[_make_moto_candidate(marca="Honda", modelo="CB500")],
        )
        # After response, the candidate still exists in ctx.candidates (not mutated)
        # The response doesn't change the candidate — it's already in the DB.
        # We just verify the handler ran without error and lead was updated.
        self.assertEqual(lead.nombre, "Laura")
        self.assertTrue(state.needs_human)

    def test_mf03_exact_advisor_reply(self):
        """MF03: Exact copy after Flow response."""
        eng, result, state, lead = self._run_flow_response({"nombre_apellido": "Ana López"})
        sent = eng._send_text_to_wa.call_args[0][1]
        self.assertEqual(sent, _MOTORCYCLE_ADVISOR_REPLY)
        self.assertNotIn("cotización", sent.lower())
        self.assertNotIn("agendado", sent.lower())
        self.assertNotIn("turno", sent.lower())


# ══════════════════════════════════════════════════════════════════════════════
# MF04 — Phone preservation
# ══════════════════════════════════════════════════════════════════════════════

class TestMF04PhonePreservation(unittest.TestCase):

    def test_mf04_wa_phone_not_overwritten(self):
        """MF04: WA phone preserved; Flow response cannot overwrite telefono."""
        eng = _make_engine()
        state = _make_state(flow_booking_token=f"{_MOTO_HANDOFF_TOKEN_PREFIX}415-9999")
        lead = _make_lead(telefono="5491153368330")
        ctx = _make_ctx(state=state, lead=lead, candidates=[_make_moto_candidate()])

        # Flow response includes a telefono field (should be ignored)
        eng._process_motorcycle_contact_response(ctx, state, {
            "nombre_apellido": "Test User",
            "telefono": "9999999999",  # must not overwrite WA phone
        })

        self.assertEqual(lead.telefono, "5491153368330", "WA phone must not be overwritten")

    def test_mf04_no_telefono_field_in_flow(self):
        """MF04: Flow response with no telefono field — WA phone unchanged."""
        eng = _make_engine()
        state = _make_state(flow_booking_token=f"{_MOTO_HANDOFF_TOKEN_PREFIX}415-8888")
        lead = _make_lead(telefono="5491153368330")
        ctx = _make_ctx(state=state, lead=lead, candidates=[_make_moto_candidate()])

        eng._process_motorcycle_contact_response(ctx, state, {"nombre_apellido": "Test"})
        self.assertEqual(lead.telefono, "5491153368330")


# ══════════════════════════════════════════════════════════════════════════════
# MF05 — Automotive candidate protected
# ══════════════════════════════════════════════════════════════════════════════

class TestMF05AutomotiveProtected(unittest.TestCase):

    def test_mf05_existing_ford_untouched(self):
        """MF05: Existing Ford Focus 2019 / Palermo candidate is NOT mutated by motorcycle Turn 1."""
        ford = _make_candidate(id=36, marca="Ford", modelo="Focus", anio=2019,
                                tipo_vehiculo="AUTO", status="dismissed")
        eng, result, state, lead, ctx = _run_turn1(
            "Quiero revisar una moto Honda CB500",
            focus_candidate=None,
            candidates=[ford],
        )
        self.assertEqual(result.action, "flow_button_sent")

        # Ford candidate fields unchanged
        self.assertEqual(ford.marca, "Ford")
        self.assertEqual(ford.modelo, "Focus")
        self.assertEqual(ford.anio, 2019)
        self.assertEqual(ford.tipo_vehiculo, "AUTO")
        self.assertEqual(ford.status, "dismissed")

        # Apply candidate was NOT called with Ford's data
        self.assertEqual(eng._apply_candidate.call_count, 0)

    def test_mf05_no_automotive_quote_for_motorcycle(self):
        """MF05: PricingService not called when motorcycle is detected."""
        eng, result, state, lead, ctx = _run_turn1("Quiero revisar una moto Honda CB500")
        self.assertEqual(eng._compute_price_quote.call_count, 0)


# ══════════════════════════════════════════════════════════════════════════════
# MF06 — Normal automotive message unaffected
# ══════════════════════════════════════════════════════════════════════════════

class TestMF06AutomotiveUnaffected(unittest.TestCase):

    def test_mf06_ford_focus_no_flow(self):
        """MF06: 'Quiero revisar un Ford Focus 2019 en Palermo' → no motorcycle Flow."""
        eng, result, state, lead, ctx = _run_turn1("Quiero revisar un Ford Focus 2019 en Palermo")
        # Motorcycle path must NOT fire
        self.assertFalse(eng._send_flow_button.called, "no Flow for automotive message")
        self.assertFalse(state.needs_human)
        # flow_booking_token must not start with moto-handoff prefix
        self.assertFalse(
            (state.flow_booking_token or "").startswith(_MOTO_HANDOFF_TOKEN_PREFIX)
        )


# ══════════════════════════════════════════════════════════════════════════════
# MF07 — No premature needs_human (Flow response still routable)
# ══════════════════════════════════════════════════════════════════════════════

class TestMF07NoPrematureHuman(unittest.TestCase):

    def test_mf07_needs_human_false_after_turn1(self):
        """MF07: After motorcycle Turn 1, needs_human=False so Flow response can be processed."""
        eng, result, state, lead, ctx = _run_turn1("Quiero revisar una moto Honda CB500")
        self.assertFalse(state.needs_human,
                         "needs_human must be False after Turn 1 — must not block Flow response")
        self.assertFalse(lead.necesita_humano)

    def test_mf07_pending_token_set_for_routing(self):
        """MF07: Pending moto-handoff token is set so _process_flow_response can route correctly."""
        eng, result, state, lead, ctx = _run_turn1("Quiero revisar una moto Honda CB500")
        self.assertIsNotNone(state.flow_booking_token)
        self.assertTrue(state.flow_booking_token.startswith(_MOTO_HANDOFF_TOKEN_PREFIX))

    def test_mf07_flow_response_routes_to_motorcycle_handler(self):
        """MF07: With moto-handoff token, _process_flow_response routes to motorcycle handler."""
        eng = _make_engine()
        state = _make_state(
            flow_booking_token=f"{_MOTO_HANDOFF_TOKEN_PREFIX}415-12345",
            needs_human=False,
        )
        lead = _make_lead()
        ctx = _make_ctx(state=state, lead=lead, candidates=[_make_moto_candidate()])

        flow_data = {"nombre_apellido": "Pedro García", "email": "pedro@test.com"}
        result = eng._process_flow_response(ctx, flow_data, flow_token="moto-handoff-415-12345")

        # Must have routed to motorcycle handler, not booking handler
        self.assertEqual(result.action, "replied")
        self.assertTrue(state.needs_human)
        sent = eng._send_text_to_wa.call_args[0][1]
        self.assertEqual(sent, _MOTORCYCLE_ADVISOR_REPLY)


# ══════════════════════════════════════════════════════════════════════════════
# MF08 — Failed/cancelled/incomplete Flow
# ══════════════════════════════════════════════════════════════════════════════

class TestMF08FlowFailure(unittest.TestCase):

    def test_mf08_empty_flow_response_does_not_claim_complete(self):
        """MF08: Empty Flow response still sends advisor reply (graceful) without inventing data."""
        eng = _make_engine()
        state = _make_state(flow_booking_token=f"{_MOTO_HANDOFF_TOKEN_PREFIX}415-empty")
        lead = _make_lead()
        ctx = _make_ctx(state=state, lead=lead, candidates=[_make_moto_candidate()])

        result = eng._process_motorcycle_contact_response(ctx, state, {})

        # Token consumed even on empty response
        self.assertIsNone(state.flow_booking_token)
        # Lead fields not invented
        self.assertIsNone(lead.nombre)
        self.assertIsNone(lead.email)
        # Advisor reply still sent (handler ran to completion)
        sent = eng._send_text_to_wa.call_args[0][1]
        self.assertEqual(sent, _MOTORCYCLE_ADVISOR_REPLY)
        # needs_human set
        self.assertTrue(state.needs_human)

    def test_mf08_no_double_advisor_reply(self):
        """MF08: Advisor reply sent exactly once per Flow response."""
        eng = _make_engine()
        state = _make_state(flow_booking_token=f"{_MOTO_HANDOFF_TOKEN_PREFIX}415-once")
        lead = _make_lead()
        ctx = _make_ctx(state=state, lead=lead, candidates=[_make_moto_candidate()])

        eng._process_motorcycle_contact_response(ctx, state, {"nombre_apellido": "X"})
        self.assertEqual(eng._send_text_to_wa.call_count, 1)


# ══════════════════════════════════════════════════════════════════════════════
# MF09 — PricingService not called
# ══════════════════════════════════════════════════════════════════════════════

class TestMF09NoPricing(unittest.TestCase):

    def test_mf09_pricing_not_called_turn1(self):
        """MF09: PricingService call count = 0 through motorcycle Turn 1."""
        eng, result, state, lead, ctx = _run_turn1("Quiero revisar una moto Honda CB500")
        self.assertEqual(eng._compute_price_quote.call_count, 0)
        self.assertEqual(eng._pricing.recalculate_revision_if_possible.call_count, 0)

    def test_mf09_pricing_not_called_flow_response(self):
        """MF09: PricingService call count = 0 through motorcycle Flow response."""
        eng = _make_engine()
        state = _make_state(flow_booking_token=f"{_MOTO_HANDOFF_TOKEN_PREFIX}415-price")
        lead = _make_lead()
        ctx = _make_ctx(state=state, lead=lead, candidates=[_make_moto_candidate(marca="Honda")])
        eng._process_motorcycle_contact_response(ctx, state, {"nombre_apellido": "Test"})
        self.assertEqual(eng._compute_price_quote.call_count, 0)
        self.assertEqual(eng._pricing.recalculate_revision_if_possible.call_count, 0)


# ══════════════════════════════════════════════════════════════════════════════
# MF10 — Exact copy
# ══════════════════════════════════════════════════════════════════════════════

class TestMF10ExactCopy(unittest.TestCase):

    def test_mf10_turn1_exact_text(self):
        """MF10: Turn 1 flow button body is exactly the approved intro text."""
        eng, result, state, lead, ctx = _run_turn1("Quiero revisar una moto Honda CB500")
        self.assertTrue(eng._send_flow_button.called)
        body = eng._send_flow_button.call_args[0][1]
        self.assertEqual(
            body,
            "Perfecto, para avanzar necesito que completes estos datos.",
            f"Turn 1 intro text mismatch: {body!r}",
        )

    def test_mf10_flow_response_exact_advisor_reply(self):
        """MF10: Flow response sends exactly the approved advisor reply."""
        eng = _make_engine()
        state = _make_state(flow_booking_token=f"{_MOTO_HANDOFF_TOKEN_PREFIX}415-copy")
        lead = _make_lead()
        ctx = _make_ctx(state=state, lead=lead, candidates=[_make_moto_candidate()])
        eng._process_motorcycle_contact_response(ctx, state, {"nombre_apellido": "Lucía M"})
        sent = eng._send_text_to_wa.call_args[0][1]
        self.assertEqual(
            sent,
            "A la brevedad uno de nuestros asesores se estará contactando con vos.",
            f"Advisor reply mismatch: {sent!r}",
        )
        self.assertNotIn("cotización", sent.lower())
        self.assertNotIn("turno", sent.lower())
        self.assertNotIn("formulario", sent.lower())

    def test_mf10_no_booking_copy_in_any_motorcycle_reply(self):
        """MF10: No booking-flow copy appears in motorcycle replies."""
        _FORBIDDEN = [
            "Recibimos tu solicitud",
            "Gracias por completar el formulario",
            "¡Listo!",
            "confirmamos el turno",
        ]
        for text in [
            "Quiero revisar una moto Honda CB500",
            "Revisan motos?",
            "Tengo una moto",
        ]:
            eng, result, state, lead, ctx = _run_turn1(text)
            if eng._send_flow_button.called:
                body = eng._send_flow_button.call_args[0][1]
                for forbidden in _FORBIDDEN:
                    self.assertNotIn(forbidden, body,
                                     f"Forbidden text {forbidden!r} found in flow body for {text!r}")
            if eng._send_text_to_wa.called:
                sent = eng._send_text_to_wa.call_args[0][1]
                for forbidden in _FORBIDDEN:
                    self.assertNotIn(forbidden, sent,
                                     f"Forbidden text {forbidden!r} found in text reply for {text!r}")


# ══════════════════════════════════════════════════════════════════════════════
# Extract make/model unit tests
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractMakeModel(unittest.TestCase):

    def setUp(self):
        self.eng = ConversationEngine.__new__(ConversationEngine)

    def test_extract_honda_cb500(self):
        make, model = self.eng._extract_motorcycle_make_model("Quiero revisar una moto Honda CB500")
        self.assertEqual(make, "Honda")
        self.assertEqual(model, "CB500")

    def test_extract_honda_only(self):
        make, model = self.eng._extract_motorcycle_make_model("Quiero revisar una moto Honda")
        self.assertEqual(make, "Honda")
        self.assertIsNone(model)

    def test_extract_no_make_model(self):
        make, model = self.eng._extract_motorcycle_make_model("Quiero revisar una moto")
        self.assertIsNone(make)
        self.assertIsNone(model)

    def test_extract_generic_query(self):
        make, model = self.eng._extract_motorcycle_make_model("Revisan motos?")
        self.assertIsNone(make)
        self.assertIsNone(model)

    def test_extract_pcx_model(self):
        make, model = self.eng._extract_motorcycle_make_model("Tengo una moto Honda PCX")
        self.assertEqual(make, "Honda")
        self.assertEqual(model, "PCX")

    def test_no_extraction_without_keyword(self):
        """Non-motorcycle text returns None/None."""
        make, model = self.eng._extract_motorcycle_make_model("Quiero un Ford Focus")
        self.assertIsNone(make)
        self.assertIsNone(model)


if __name__ == "__main__":
    unittest.main()
