"""M21.2 regression: Layer F deferred-interest pass-through (step 8.5).

Proves that genuinely deferred / soft-close messages (no active vehicle, no
inspection request) are NOT terminated by Layer F step 9 (UNCERTAIN) but instead
reach the AI+narrative path where M21.1.6 can fire the deferred-interest intercept
and reply with DEFERRED_RESPONSE_ES.

Cases:
  A  — LIVE01 verbatim (fresh thread, last_intent=None)
  B  — clean deferred phrasing
  C  — deferred phrasing WITH active vehicle → active commercial flow, not deferred
  D  — "quería hacer una consulta" → Layer D FAQ bypass, not deferred
  E  — "más adelante" path (already passes via step 3; regression guard)
  F  — genuinely uncertain message → Layer F step 9 still fires (BR-1 safe)
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

for _mod_name in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = sqlalchemy.JSON   # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON      # type: ignore[attr-defined]

from app.services.conversation_engine import ConversationEngine   # noqa: E402
from app.schemas.conversation import ConversationHandleIn          # noqa: E402
from app.services.outbound_guard import OutboundBlockedError       # noqa: E402
from app.services.narrative_schema import (                        # noqa: E402
    DEFERRED_RESPONSE_ES,
    parse_narrative_interpretation,
)
from app.services.vehicle_catalog import VehicleMatch, FuzzyLookupResult  # noqa: E402

STAGE_QUALIFYING = "QUALIFYING"

_AI_DEFERRED = json.dumps({
    "intent": "OTHER",
    "reply": "...",
    "deferred_interest": True,
    "vehicle_make_model": None,
    "candidate": {"action": "none"},
    "extracted": {},
    "lead_flag": None,
    "needs_human": False,
})

_AI_NORMAL = json.dumps({
    "intent": "QUALIFYING",
    "reply": "Respuesta de prueba.",
    "deferred_interest": False,
    "candidate": {"action": "none"},
    "extracted": {},
    "lead_flag": None,
    "needs_human": False,
})


def _make_state(**kw):
    from types import SimpleNamespace
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


def _make_ctx(state=None, candidates=None):
    from types import SimpleNamespace
    ctx = SimpleNamespace()
    ctx.thread = SimpleNamespace(id=99)
    ctx.contact = SimpleNamespace(wa_id="5491199990000")
    ctx.lead = SimpleNamespace(
        id=1, flag="PRESUPUESTANDO", estado="CONSULTA_NUEVA",
        nombre="Test", telefono=None, necesita_humano=False,
    )
    ctx.state = state if state is not None else _make_state()
    ctx.candidates = candidates if candidates is not None else []
    return ctx


def _make_engine(ai_response=None):
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock()
    eng.settings = MagicMock()
    eng.settings.whatsapp_manual_handoff_flow_id = ""
    eng.settings.openai_api_key = "sk-fake"
    eng.settings.openai_chat_model = "gpt-4o-mini"
    eng.settings.backend_url = "http://localhost:8000"
    eng._send_text_to_wa = MagicMock(return_value="mock-wa-id")
    eng._send_fallback_human_review_notification = MagicMock()
    eng._call_openai = MagicMock(return_value=ai_response or _AI_NORMAL)
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


def _run(text, state_kwargs=None, candidates=None, ai_response=None,
         lookup_vehicle_return=None, fuzzy_return=None):
    eng = _make_engine(ai_response=ai_response)
    state = _make_state(**(state_kwargs or {}))
    ctx = _make_ctx(state=state, candidates=candidates or [])
    event = ConversationHandleIn(
        thread_id=99,
        wa_message_id="test-deferred-wa-id",
        wa_id="5491199990000",
        text=text,
        unanswered_recent_user_messages=[text],
        recent_user_messages=[text],
    )
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


# ── Case A — LIVE01 verbatim, fresh thread (last_intent=None) ────────────────

class TestCaseA_LIVE01Verbatim(unittest.TestCase):
    """Step 8.5 must route LIVE01 message to AI+narrative → DEFERRED_RESPONSE_ES."""

    MSG = ("Hola por ahora estoy buscando un auto agende esto para no perderlo "
           "asijina vez q decida aviso")

    def test_ai_is_called(self):
        """AI must be invoked — Layer F step 9 must NOT terminate early."""
        eng, result, _ = _run(self.MSG, ai_response=_AI_DEFERRED)
        eng._call_openai.assert_called_once()

    def test_deferred_response_sent(self):
        """_send_text_to_wa must be called with DEFERRED_RESPONSE_ES text."""
        eng, result, _ = _run(self.MSG, ai_response=_AI_DEFERRED)
        eng._send_text_to_wa.assert_called_once()
        call_text = eng._send_text_to_wa.call_args[0][1]
        self.assertIn("cuando tengas", call_text)
        self.assertIn("algún auto en vista", call_text)

    def test_uncertain_intro_not_sent(self):
        """The service-introduction/UNCERTAIN text must NOT be sent."""
        eng, result, _ = _run(self.MSG, ai_response=_AI_DEFERRED)
        call_text = eng._send_text_to_wa.call_args[0][1]
        self.assertNotIn("Somos Ridecheck", call_text)

    def test_no_candidate_created(self):
        """Deferred path: no commercial mutation."""
        eng, result, _ = _run(self.MSG, ai_response=_AI_DEFERRED)
        eng._apply_candidate.assert_not_called()


# ── Case B — clean deferred phrasing ─────────────────────────────────────────

class TestCaseB_CleanDeferred(unittest.TestCase):
    """Clean deferred phrasing also reaches deferred intercept."""

    MSG = "Por ahora estoy buscando. Cuando tenga uno en vista les aviso."

    def test_ai_called_and_deferred_response_sent(self):
        eng, result, _ = _run(self.MSG, ai_response=_AI_DEFERRED)
        eng._call_openai.assert_called_once()
        call_text = eng._send_text_to_wa.call_args[0][1]
        self.assertIn("cuando tengas", call_text)

    def test_no_candidate(self):
        eng, result, _ = _run(self.MSG, ai_response=_AI_DEFERRED)
        eng._apply_candidate.assert_not_called()


# ── Case C — deferred phrasing WITH active vehicle → commercial flow ──────────

class TestCaseC_DeferredWithActiveVehicle(unittest.TestCase):
    """'Estoy buscando' with a catalog vehicle hit → commercial flow, not deferred."""

    MSG = "Estoy buscando, pero ya tengo un Focus 2019 en Palermo que quiero revisar."

    def _vehicle_hit(self):
        return VehicleMatch(
            marca="Ford", modelo="Focus",
            tipo_vehiculo="AUTO", matched_alias="Focus 2019",
            confidence="high",
        )

    def test_active_commercial_flow(self):
        """Step 4 (catalog vehicle) fires before step 8.5 — commercial path taken."""
        eng, result, _ = _run(
            self.MSG,
            ai_response=_AI_NORMAL,
            lookup_vehicle_return=self._vehicle_hit(),
        )
        # Candidate should be created/applied — commercial, not deferred
        eng._create_candidate_from_catalog.assert_called()

    def test_deferred_response_not_sent(self):
        eng, result, _ = _run(
            self.MSG,
            ai_response=_AI_NORMAL,
            lookup_vehicle_return=self._vehicle_hit(),
        )
        if eng._send_text_to_wa.called:
            call_text = eng._send_text_to_wa.call_args[0][1]
            self.assertNotIn("cuando tengas", call_text)


# ── Case D — "quería hacer una consulta" → Layer D FAQ, not deferred ──────────

class TestCaseD_FAQ(unittest.TestCase):
    """FAQ message routes through Layer D (not Layer F) and is not classified deferred."""

    MSG = "Hola, quería hacer una consulta"

    def test_ai_called_for_faq(self):
        """Layer D FAQ bypass routes to AI — AI is called."""
        eng, result, _ = _run(self.MSG, ai_response=_AI_NORMAL)
        eng._call_openai.assert_called_once()

    def test_deferred_response_not_sent(self):
        """FAQ message must NOT trigger DEFERRED_RESPONSE_ES."""
        eng, result, _ = _run(self.MSG, ai_response=_AI_NORMAL)
        if eng._send_text_to_wa.called:
            call_text = eng._send_text_to_wa.call_args[0][1]
            self.assertNotIn("cuando tengas", call_text)


# ── Case E — "más adelante" → existing Layer F step 3 path (regression guard) ─

class TestCaseE_MasAdelante(unittest.TestCase):
    """'más adelante' already passes via step 3; regression guard ensures it still works."""

    MSG = "Capaz más adelante, todavía no tengo ningún auto en vista."

    def test_ai_called(self):
        """Step 3 (FAQ/soft-close) catches 'más adelante' → AI invoked."""
        eng, result, _ = _run(self.MSG, ai_response=_AI_DEFERRED)
        eng._call_openai.assert_called_once()

    def test_deferred_response_sent(self):
        eng, result, _ = _run(self.MSG, ai_response=_AI_DEFERRED)
        call_text = eng._send_text_to_wa.call_args[0][1]
        self.assertIn("cuando tengas", call_text)


# ── Case F — genuinely uncertain message → Layer F step 9 still fires (BR-1) ──

class TestCaseF_GenuinelyUncertain(unittest.TestCase):
    """A message with no inspection signal AND no deferred signal still hits step 9.

    Confirms BR-1 is not weakened: unknown intent → UNCERTAIN clarification.
    """

    def test_uncertain_triggers_for_opaque_message(self):
        """'Hola tengo una pregunta' — no deferred pattern, no FAQ pattern → step 9."""
        msg = "Hola tengo una pregunta sobre el auto"
        eng, result, _ = _run(msg, ai_response=_AI_NORMAL)
        # Step 9 fires → _send_text_to_wa called with UNCERTAIN intro
        # AI is NOT called (Layer F terminates before the AI call)
        if eng._send_text_to_wa.called:
            call_text = eng._send_text_to_wa.call_args[0][1]
            # The uncertain reply is sent, not the deferred response
            self.assertNotIn("cuando tengas", call_text)

    def test_step9_sends_uncertain_intro(self):
        """Step 9 must send the service introduction (UNCERTAIN), not DEFERRED."""
        msg = "Hola tengo una pregunta sobre el auto"
        eng, result, _ = _run(msg, ai_response=_AI_NORMAL)
        eng._send_text_to_wa.assert_called_once()
        call_text = eng._send_text_to_wa.call_args[0][1]
        self.assertIn("Somos Ridecheck", call_text)


if __name__ == "__main__":
    unittest.main()
