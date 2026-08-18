"""M21.2 MH — Motorcycle handoff regression tests.

MH01 — exact LIVE07 message
MH02 — existing automotive candidate is untouched
MH03 — generic inquiry ("Revisan motos?")
MH04 — quad/UTV boundary
MH05 — normal automotive message not blocked
MH06 — no false form-completion wording in any motorcycle reply

Root cause fixed: _motorcycle_human_handoff was sending _FALLBACK_WARM_HANDOFF
("Gracias por completar el formulario...") which implies a form was completed when
no form was ever sent.  Fix: dedicated _MOTORCYCLE_HANDOFF_REPLY constant.
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
    _FALLBACK_WARM_HANDOFF,
    _MOTORCYCLE_HANDOFF_REPLY,
)
from app.schemas.conversation import ConversationHandleIn, HANDLED_ACTIONS  # noqa: E402
from app.services.outbound_guard import OutboundBlockedError                # noqa: E402

STAGE_QUALIFYING = "QUALIFYING"
STAGE_SCHEDULING = "SCHEDULING"
STAGE_QUOTED     = "QUOTED"


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
        id=1, flag=None, estado="CONSULTA_NUEVA",
        nombre="Test", telefono=None, necesita_humano=False,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _make_candidate(**kw) -> types.SimpleNamespace:
    ns = types.SimpleNamespace(
        id=99, marca="Ford", modelo="Focus", anio=2019,
        tipo_vehiculo="AUTO", zone_group="CABA", zone_detail="Palermo",
        status="current_focus",
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _make_ctx(state=None, lead=None, candidates=None) -> types.SimpleNamespace:
    ctx = types.SimpleNamespace()
    ctx.thread = types.SimpleNamespace(id=42)
    ctx.contact = types.SimpleNamespace(wa_id="5491199999999")
    ctx.lead = lead if lead is not None else _make_lead()
    ctx.state = state if state is not None else _make_state()
    ctx.candidates = candidates or []
    return ctx


def _make_event(text=None, unanswered=None) -> ConversationHandleIn:
    msgs = unanswered or ([text] if text else [])
    return ConversationHandleIn(
        thread_id=42,
        wa_message_id="test-wa-id",
        wa_id="5491199999999",
        text=text,
        unanswered_recent_user_messages=unanswered or [],
        recent_user_messages=msgs,
    )


def _make_engine(focus_candidate=None) -> ConversationEngine:
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock()
    eng.settings = MagicMock()
    eng.settings.openai_api_key = "sk-fake"
    eng.settings.openai_chat_model = "gpt-4o-mini"
    eng.settings.backend_url = "http://localhost:8000"
    # Explicitly empty so MH tests exercise the fallback plain-text path.
    # MF tests (test_m21_2_mf_motorcycle_flow.py) test the Flow-based path.
    eng.settings.whatsapp_flow_id = ""
    eng._send_text_to_wa = MagicMock(return_value="mock-wa-id")
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


def _run(text, stage=STAGE_QUALIFYING, focus_candidate=None, **state_kw):
    eng = _make_engine(focus_candidate=focus_candidate)
    state = _make_state(last_stage=stage, **state_kw)
    lead = _make_lead()
    ctx = _make_ctx(state=state, lead=lead)
    event = _make_event(text=text)
    with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
        result = eng._process_text(ctx, event)
    return eng, result, state, lead


def _assert_motorcycle_handoff(test, eng, result, state, lead=None):
    test.assertEqual(result.action, "replied")
    test.assertTrue(result.handled)
    test.assertIn(result.action, HANDLED_ACTIONS)
    test.assertTrue(state.needs_human, "needs_human must be True after motorcycle handoff")
    test.assertTrue(eng._send_fallback_human_review_notification.called)
    sent = eng._send_text_to_wa.call_args[0][1]
    test.assertEqual(sent, _MOTORCYCLE_HANDOFF_REPLY,
                     f"expected _MOTORCYCLE_HANDOFF_REPLY; got: {sent!r}")
    test.assertNotIn("completar el formulario", sent,
                     "motorcycle reply must not claim a form was completed")
    test.assertEqual(eng._call_openai.call_count, 0, "AI must not be called for motorcycle")
    if lead is not None:
        test.assertTrue(lead.necesita_humano)


# ══════════════════════════════════════════════════════════════════════════════
# MH01 — Exact LIVE07 scenario
# ══════════════════════════════════════════════════════════════════════════════

class TestMH01Live07Exact(unittest.TestCase):

    def test_mh01_live07_exact(self):
        """MH01: 'Quiero revisar una moto Honda CB500' → motorcycle handoff; no auto quote."""
        eng, result, state, lead = _run("Quiero revisar una moto Honda CB500")
        _assert_motorcycle_handoff(self, eng, result, state, lead)
        self.assertEqual(eng._apply_candidate.call_count, 0,
                         "no automotive candidate must be created")
        self.assertEqual(eng._compute_price_quote.call_count, 0,
                         "no automotive quote must be produced")

    def test_mh01_reply_is_motorcycle_specific(self):
        """MH01b: reply references motos, not form completion."""
        eng, result, state, _ = _run("Quiero revisar una moto Honda CB500")
        sent = eng._send_text_to_wa.call_args[0][1]
        self.assertIn("motos", sent.lower(),
                      "motorcycle reply should mention motos")
        self.assertNotIn("completar el formulario", sent)
        self.assertNotIn("formulario", sent.lower())

    def test_mh01_not_fallback_warm_handoff(self):
        """MH01c: _MOTORCYCLE_HANDOFF_REPLY differs from _FALLBACK_WARM_HANDOFF."""
        self.assertNotEqual(_MOTORCYCLE_HANDOFF_REPLY, _FALLBACK_WARM_HANDOFF)
        eng, _, _, _ = _run("Quiero revisar una moto Honda CB500")
        sent = eng._send_text_to_wa.call_args[0][1]
        self.assertNotEqual(sent, _FALLBACK_WARM_HANDOFF)


# ══════════════════════════════════════════════════════════════════════════════
# MH02 — Existing automotive candidate is untouched
# ══════════════════════════════════════════════════════════════════════════════

class TestMH02AutomotiveCandidateProtected(unittest.TestCase):

    def test_mh02_existing_ford_focus_untouched(self):
        """MH02: Ford Focus 2019/Palermo candidate exists; moto message → candidate unchanged."""
        existing = _make_candidate(marca="Ford", modelo="Focus", anio=2019,
                                   zone_group="CABA", zone_detail="Palermo")
        eng, result, state, lead = _run(
            "Quiero revisar una moto Honda CB500",
            stage=STAGE_SCHEDULING,
            focus_candidate=existing,
        )
        _assert_motorcycle_handoff(self, eng, result, state, lead)
        # Candidate object must not be mutated
        self.assertEqual(existing.marca, "Ford")
        self.assertEqual(existing.modelo, "Focus")
        self.assertEqual(existing.anio, 2019)
        self.assertEqual(existing.zone_detail, "Palermo")
        self.assertEqual(existing.status, "current_focus")
        # No second candidate created
        self.assertEqual(eng._apply_candidate.call_count, 0)
        self.assertEqual(eng._create_candidate_from_catalog.call_count, 0)

    def test_mh02_scheduling_stage_moto_fires_before_scheduler(self):
        """MH02b: SCHEDULING + moto → Layer A preempts scheduler; _try_schedule_and_flow=0."""
        eng, result, state, lead = _run(
            "Quiero revisar una moto Honda CB500",
            stage=STAGE_SCHEDULING,
        )
        _assert_motorcycle_handoff(self, eng, result, state, lead)
        self.assertEqual(eng._try_schedule_and_flow.call_count, 0)

    def test_mh02_quoted_stage_moto_fires_before_acceptance(self):
        """MH02c: QUOTED + moto → Layer A preempts acceptance handler."""
        eng, result, state, _ = _run(
            "sí acepto pero es una moto",
            stage=STAGE_QUOTED,
        )
        _assert_motorcycle_handoff(self, eng, result, state)


# ══════════════════════════════════════════════════════════════════════════════
# MH03 — Generic motorcycle inquiry
# ══════════════════════════════════════════════════════════════════════════════

class TestMH03GenericMotorcycle(unittest.TestCase):

    def test_mh03_revisan_motos(self):
        """MH03: 'Revisan motos?' → motorcycle handoff; no automotive quote."""
        eng, result, state, lead = _run("Revisan motos?")
        _assert_motorcycle_handoff(self, eng, result, state, lead)

    def test_mh03_tengo_una_moto(self):
        """MH03b: 'Tengo una moto' → motorcycle handoff."""
        eng, result, state, _ = _run("Tengo una moto")
        _assert_motorcycle_handoff(self, eng, result, state)

    def test_mh03_motocicleta(self):
        """MH03c: 'Es una motocicleta Honda' → motorcycle handoff."""
        eng, result, state, _ = _run("Es una motocicleta Honda")
        _assert_motorcycle_handoff(self, eng, result, state)


# ══════════════════════════════════════════════════════════════════════════════
# MH04 — Quad/UTV boundary
# ══════════════════════════════════════════════════════════════════════════════

class TestMH04QuadUTV(unittest.TestCase):

    def test_mh04_cuatriciclo(self):
        """MH04: 'Revisan cuatriciclos?' → motorcycle handoff path."""
        eng, result, state, _ = _run("Revisan cuatriciclos?")
        _assert_motorcycle_handoff(self, eng, result, state)

    def test_mh04_utv(self):
        """MH04b: 'Quiero revisar un UTV' → motorcycle handoff path."""
        eng, result, state, _ = _run("Quiero revisar un UTV")
        _assert_motorcycle_handoff(self, eng, result, state)

    def test_mh04_quad(self):
        """MH04c: 'Es un quad' → motorcycle handoff path."""
        eng, result, state, _ = _run("Es un quad")
        _assert_motorcycle_handoff(self, eng, result, state)


# ══════════════════════════════════════════════════════════════════════════════
# MH05 — Normal automotive path is not blocked
# ══════════════════════════════════════════════════════════════════════════════

class TestMH05NormalAutomotiveUnaffected(unittest.TestCase):

    def test_mh05_ford_focus_palermo_goes_to_ai(self):
        """MH05: 'Quiero revisar un Ford Focus 2019 en Palermo' → NOT motorcycle; AI called."""
        eng, result, state, _ = _run("Quiero revisar un Ford Focus 2019 en Palermo")
        self.assertFalse(state.needs_human,
                         "automotive message must not set needs_human")
        # Motorcycle reply must NOT be sent
        if eng._send_text_to_wa.called:
            sent = eng._send_text_to_wa.call_args[0][1]
            self.assertNotEqual(sent, _MOTORCYCLE_HANDOFF_REPLY,
                                "automotive message must not trigger motorcycle reply")

    def test_mh05_revision_precompra_goes_to_ai(self):
        """MH05b: 'Quiero una revisión precompra' → NOT motorcycle; not blocked."""
        eng, result, state, _ = _run("Quiero una revisión precompra")
        self.assertFalse(state.needs_human)
        if eng._send_text_to_wa.called:
            sent = eng._send_text_to_wa.call_args[0][1]
            self.assertNotEqual(sent, _MOTORCYCLE_HANDOFF_REPLY)

    def test_mh05_scooter_electrico_auto_context_not_blocked(self):
        """MH05c: 'motor eléctrico del auto' must not match motorcycle pattern."""
        # 'motor' alone must not trigger; only moto/motocicleta/scooter words
        eng, result, state, _ = _run("El motor eléctrico del auto no arranca")
        self.assertFalse(state.needs_human)


# ══════════════════════════════════════════════════════════════════════════════
# MH06 — No false form-completion wording in any motorcycle reply
# ══════════════════════════════════════════════════════════════════════════════

class TestMH06NoFalseFormCopy(unittest.TestCase):

    _MOTORCYCLE_INPUTS = [
        "Quiero revisar una moto Honda CB500",
        "Revisan motos?",
        "Tengo una moto",
        "Es una motocicleta",
        "Revisan cuatriciclos?",
        "Quiero revisar un UTV",
        "tengo un scooter eléctrico",
    ]

    def test_mh06_no_formulario_in_any_motorcycle_reply(self):
        """MH06: No motorcycle handoff reply contains 'formulario' or 'completar'."""
        for text in self._MOTORCYCLE_INPUTS:
            with self.subTest(text=text):
                eng, _, _, _ = _run(text)
                if eng._send_text_to_wa.called:
                    sent = eng._send_text_to_wa.call_args[0][1]
                    self.assertNotIn(
                        "formulario", sent.lower(),
                        f"'{text}' → reply contains 'formulario': {sent!r}",
                    )
                    self.assertNotIn(
                        "completar", sent.lower(),
                        f"'{text}' → reply contains 'completar': {sent!r}",
                    )

    def test_mh06_motorcycle_reply_constant_has_no_form_wording(self):
        """MH06b: _MOTORCYCLE_HANDOFF_REPLY constant itself has no form-completion wording."""
        self.assertNotIn("formulario", _MOTORCYCLE_HANDOFF_REPLY.lower())
        self.assertNotIn("completar", _MOTORCYCLE_HANDOFF_REPLY.lower())
        self.assertNotIn("gracias por completar", _MOTORCYCLE_HANDOFF_REPLY.lower())

    def test_mh06_fallback_warm_handoff_is_unchanged(self):
        """MH06c: _FALLBACK_WARM_HANDOFF still says 'formulario' — confirming it is only for post-flow paths."""
        self.assertIn("formulario", _FALLBACK_WARM_HANDOFF.lower())


if __name__ == "__main__":
    unittest.main()
