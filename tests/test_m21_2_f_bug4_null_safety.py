"""M21.2-F BUG-4 null-safety regression pack.

BUG-4: AI returns candidate.tipo_vehiculo=null → AttributeError crash in CE.

ROOT CAUSE
conversation_engine.py:2477:
    _cand_data.get("tipo_vehiculo", "").upper() == "MOTO"
dict.get(key, default) returns the stored value (None) when the key exists
with value null; the default "" is only used when the key is absent.
None.upper() → AttributeError. CE exits with no reply sent to customer.

FIX
    (_cand_data.get("tipo_vehiculo") or "").upper() == "MOTO"
`or ""` coerces None (and "") to a safe empty string.

TRIGGER
Any inbound message on a clean/reset thread where:
  - The AI includes "tipo_vehiculo": null in the candidate dict
  - (This is a valid AI response when vehicle type is unknown)

BUG4-01  tipo_vehiculo=null in candidate → no AttributeError, MOTO branch inactive
BUG4-02  tipo_vehiculo absent entirely   → same safe behavior
BUG4-03  tipo_vehiculo=""               → same safe behavior
BUG4-04  tipo_vehiculo="MOTO"           → motorcycle handoff fires unchanged
BUG4-05  candidate=null                 → decision.get("candidate") or {} safe
BUG4-06  LIVE04 crash reproduction: "Yo vivo en Tigre pero el auto está en Palermo."
         with clean state and AI returning tipo_vehiculo=null
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

from app.services.conversation_engine import ConversationEngine  # noqa: E402
from app.schemas.conversation import ConversationHandleIn         # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_state(**kw):
    ns = SimpleNamespace(
        last_stage=None, needs_human=False, last_intent=None,
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


def _make_ctx(state, candidates=None, lead_flag="PRESUPUESTANDO"):
    ctx = SimpleNamespace()
    ctx.thread = SimpleNamespace(id=99)
    ctx.contact = SimpleNamespace(wa_id="5491199990000")
    ctx.lead = SimpleNamespace(
        id=1, flag=lead_flag, estado="CONSULTA_NUEVA",
        nombre="Test", telefono=None, necesita_humano=False,
    )
    ctx.state = state
    ctx.candidates = list(candidates or [])
    ctx.db_messages = []
    return ctx


def _make_event(text, thread_id=99):
    return ConversationHandleIn(
        thread_id=thread_id,
        wa_message_id=f"msg-{abs(hash(text)) % 100000}",
        wa_id="5491199990000",
        text=text,
        recent_user_messages=[text],
        unanswered_recent_user_messages=[text],
    )


def _make_engine_bug4(candidate_dict: dict | None = None, ai_reply: str = "Entendido, te ayudo."):
    """CE factory for BUG-4 null-safety tests.

    Mocks AI to return the given candidate dict.
    candidate_dict=None → AI returns candidate: null (tests BUG4-05).
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
    eng._motorcycle_human_handoff = MagicMock(return_value={"status": "moto_handoff"})

    ai_candidate = candidate_dict  # may be None, {}, or a real dict
    eng._call_openai = MagicMock(return_value=json.dumps({
        "intent": "QUALIFYING",
        "reply": ai_reply,
        "deferred_interest": False,
        "candidate": ai_candidate,
        "extracted": {},
        "lead_flag": None,
        "needs_human": False,
    }))
    eng._build_ai_messages = MagicMock(return_value=[])

    # No pricing data — _compute_price_quote returns None (no vehicle/zone)
    eng._pricing = MagicMock()
    eng._pricing.quote.side_effect = Exception("no vehicle data")

    eng._scrub_invented_price = MagicMock(side_effect=lambda r, q: r)
    eng._extract_zone_from_text = MagicMock(return_value=None)
    eng._normalize_zone_from_db = MagicMock()
    eng._try_schedule_and_flow = MagicMock(return_value=None)
    eng._handle_day_only_request = MagicMock(return_value=None)
    eng._apply_extracted = MagicMock()
    eng._apply_candidate = MagicMock()
    eng._apply_narrative_interpretation = MagicMock()

    return eng


def _run_turn(eng, ctx, text):
    return eng._process_text(ctx, _make_event(text, ctx.thread.id))


# ── BUG4-01: tipo_vehiculo=null → no crash ────────────────────────────────────

class TestBUG401NullTipoVehiculo(unittest.TestCase):
    """BUG4-01 — AI returns candidate.tipo_vehiculo=null: CE must not crash."""

    def setUp(self):
        self.eng = _make_engine_bug4(candidate_dict={"tipo_vehiculo": None, "action": "create"})
        state = _make_state()
        self.ctx = _make_ctx(state)

    def test_no_attribute_error(self):
        """Fix verified: None.upper() no longer raised."""
        try:
            _run_turn(self.eng, self.ctx, "Quiero revisar el auto.")
        except AttributeError as e:
            self.fail(f"AttributeError raised after fix: {e}")

    def test_moto_branch_does_not_activate(self):
        """tipo_vehiculo=null must not trigger motorcycle handoff."""
        _run_turn(self.eng, self.ctx, "Quiero revisar el auto.")
        self.eng._motorcycle_human_handoff.assert_not_called()

    def test_apply_candidate_reached(self):
        """Execution continues past the guard: _apply_candidate is called."""
        _run_turn(self.eng, self.ctx, "Quiero revisar el auto.")
        self.eng._apply_candidate.assert_called_once()

    def test_reply_sent(self):
        """CE sends a reply (no silent failure)."""
        _run_turn(self.eng, self.ctx, "Quiero revisar el auto.")
        self.eng._send_text_to_wa.assert_called()


# ── BUG4-02: tipo_vehiculo absent → safe ──────────────────────────────────────

class TestBUG402AbsentTipoVehiculo(unittest.TestCase):
    """BUG4-02 — candidate dict has no tipo_vehiculo key: safe behavior."""

    def setUp(self):
        self.eng = _make_engine_bug4(candidate_dict={"action": "create"})
        state = _make_state()
        self.ctx = _make_ctx(state)

    def test_no_crash(self):
        try:
            _run_turn(self.eng, self.ctx, "Quiero revisar el auto.")
        except AttributeError as e:
            self.fail(f"AttributeError: {e}")

    def test_moto_branch_does_not_activate(self):
        _run_turn(self.eng, self.ctx, "Quiero revisar el auto.")
        self.eng._motorcycle_human_handoff.assert_not_called()

    def test_apply_candidate_reached(self):
        _run_turn(self.eng, self.ctx, "Quiero revisar el auto.")
        self.eng._apply_candidate.assert_called_once()


# ── BUG4-03: tipo_vehiculo="" → safe ──────────────────────────────────────────

class TestBUG403EmptyTipoVehiculo(unittest.TestCase):
    """BUG4-03 — candidate.tipo_vehiculo="" (empty string): safe behavior."""

    def setUp(self):
        self.eng = _make_engine_bug4(candidate_dict={"tipo_vehiculo": "", "action": "create"})
        state = _make_state()
        self.ctx = _make_ctx(state)

    def test_no_crash(self):
        try:
            _run_turn(self.eng, self.ctx, "Quiero revisar el auto.")
        except AttributeError as e:
            self.fail(f"AttributeError: {e}")

    def test_moto_branch_does_not_activate(self):
        _run_turn(self.eng, self.ctx, "Quiero revisar el auto.")
        self.eng._motorcycle_human_handoff.assert_not_called()

    def test_apply_candidate_reached(self):
        _run_turn(self.eng, self.ctx, "Quiero revisar el auto.")
        self.eng._apply_candidate.assert_called_once()


# ── BUG4-04: tipo_vehiculo="MOTO" → handoff fires ────────────────────────────

class TestBUG404MotoHandoff(unittest.TestCase):
    """BUG4-04 — candidate.tipo_vehiculo="MOTO": motorcycle handoff fires as before."""

    def setUp(self):
        self.eng = _make_engine_bug4(candidate_dict={"tipo_vehiculo": "MOTO", "action": "create"})
        state = _make_state()
        self.ctx = _make_ctx(state)

    def test_moto_branch_activates(self):
        """Existing motorcycle behavior unchanged after fix."""
        _run_turn(self.eng, self.ctx, "Tengo una moto.")
        self.eng._motorcycle_human_handoff.assert_called_once()

    def test_apply_candidate_not_called_for_moto(self):
        """Motorcycle handoff returns early; _apply_candidate skipped."""
        _run_turn(self.eng, self.ctx, "Tengo una moto.")
        self.eng._apply_candidate.assert_not_called()


# ── BUG4-05: candidate=null → or {} guard safe ───────────────────────────────

class TestBUG405NullCandidate(unittest.TestCase):
    """BUG4-05 — AI returns candidate=null: decision.get("candidate") or {} safe."""

    def setUp(self):
        # candidate_dict=None → AI JSON has "candidate": null
        self.eng = _make_engine_bug4(candidate_dict=None)
        state = _make_state()
        self.ctx = _make_ctx(state)

    def test_no_crash(self):
        try:
            _run_turn(self.eng, self.ctx, "Quiero revisar el auto.")
        except (AttributeError, TypeError) as e:
            self.fail(f"Exception with null candidate: {e}")

    def test_moto_branch_does_not_activate(self):
        _run_turn(self.eng, self.ctx, "Quiero revisar el auto.")
        self.eng._motorcycle_human_handoff.assert_not_called()

    def test_apply_candidate_called_with_empty_dict(self):
        """null candidate coerced to {}; _apply_candidate called (it guards action=none)."""
        _run_turn(self.eng, self.ctx, "Quiero revisar el auto.")
        self.eng._apply_candidate.assert_called_once()
        call_arg = self.eng._apply_candidate.call_args[0][1]
        self.assertEqual(call_arg, {})


# ── BUG4-06: LIVE04 crash reproduction ───────────────────────────────────────

class TestBUG406LIVE04CrashReproduction(unittest.TestCase):
    """BUG4-06 — Exact LIVE04 crash scenario: location msg / clean state / tipo=null.

    Reproduces: "Yo vivo en Tigre pero el auto está en Palermo." on a clean
    thread where AI returns candidate.tipo_vehiculo=null.

    Assertions are limited to behavior the state supports (per directive):
    no exception, no customer silence, location CE path continues.
    """

    def setUp(self):
        # Clean state: no candidates, no zone, no stage — exactly as wiped for LIVE04.
        self.eng = _make_engine_bug4(
            candidate_dict={"tipo_vehiculo": None, "action": "create", "zone_detail": "Palermo"},
            ai_reply="Para confirmar, ¿el auto está en Palermo?",
        )
        # Zone lookup: CE may detect Palermo or Tigre from text.
        def _zone_lookup(text):
            t = (text or "").lower()
            if "palermo" in t:
                return SimpleNamespace(zone_group="CABA", zone_detail="Palermo", viaticos=0)
            return None
        self.eng._extract_zone_from_text = MagicMock(side_effect=_zone_lookup)

        state = _make_state()  # clean: all None
        self.ctx = _make_ctx(state, candidates=[], lead_flag="NUEVO")

    def test_no_attribute_error(self):
        """The fix: None.upper() is no longer raised on this exact message."""
        try:
            _run_turn(self.eng, self.ctx, "Yo vivo en Tigre pero el auto está en Palermo.")
        except AttributeError as e:
            self.fail(
                f"BUG-4 still present — AttributeError on LIVE04 message: {e}"
            )

    def test_no_customer_silence(self):
        """CE sends a reply; customer does not receive silence."""
        _run_turn(self.eng, self.ctx, "Yo vivo en Tigre pero el auto está en Palermo.")
        self.eng._send_text_to_wa.assert_called()

    def test_moto_branch_not_activated(self):
        """Location message must not trigger motorcycle handoff."""
        _run_turn(self.eng, self.ctx, "Yo vivo en Tigre pero el auto está en Palermo.")
        self.eng._motorcycle_human_handoff.assert_not_called()

    def test_apply_candidate_reached(self):
        """Normal CE candidate path reached (no early-exit from crash)."""
        _run_turn(self.eng, self.ctx, "Yo vivo en Tigre pero el auto está en Palermo.")
        self.eng._apply_candidate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
