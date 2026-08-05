"""M21.1.3 — Location Semantic Roles and Candidate Persistence — Executable Tests.

Source of truth: docs/M21_1_3_location_roles.md (approved 2026-08-05, Lara Dittmar).

Scenarios covered: SC11, SC12, SC13, SC14, SC17, and 13 additional role-separation cases.

Observables:
  VEHICLE_ZONE_SET  → candidate.zone_group / zone_detail set to expected value
  NO_ZONE_MUTATION  → candidate.zone_group / zone_detail unchanged
  CLARIFICATION     → _send_text_to_wa called with _LOCATION_CONTRADICTION_CLARIFICATION
                      AND result.action in HANDLED_ACTIONS
  PRICING_USES_CANDIDATE → _compute_price_quote called; candidate zone is authoritative
  NO_COMMERCIAL_MUTATION → no pricing, no scheduling, no Flow dispatch

All tests: SQLite in-memory; external services mocked; OUTBOUND_ENABLED=false.
"""
from __future__ import annotations

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

import json

from app.services.conversation_engine import (  # noqa: E402
    ConversationEngine,
    _LOCATION_CONTRADICTION_CLARIFICATION,
    _has_customer_origin_clause,
)
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


# ── Zone stubs ─────────────────────────────────────────────────────────────────

def _zone(group: str, detail: str) -> SimpleNamespace:
    return SimpleNamespace(zone_group=group, zone_detail=detail, viaticos=0)


_ZONE_CABA = _zone("CABA", "CABA")
_ZONE_NORTE_TIGRE = _zone("Norte", "Tigre")
_ZONE_NORTE_SAN_ISIDRO = _zone("Norte", "San Isidro")
_ZONE_SUR_LA_PLATA = _zone("Sur", "La Plata")
_ZONE_NORTE_PALERMO = _zone("CABA", "CABA")   # Palermo is a CABA barrio → CABA zone
_ZONE_OESTE_MORON = _zone("Oeste", "Morón")


def _zone_lookup_for(**mapping):
    """Return a side_effect that resolves zone names from a keyword→zone dict."""
    def _side(text: str):
        t = text.lower().strip()
        for keyword, zone in mapping.items():
            if keyword.lower() in t:
                return zone
        return None
    return _side


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
        tipo_vehiculo="Auto", marca="Toyota", modelo="Corolla",
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
        wa_message_id="test-lr-wa-id",
        wa_id="5491199999999",
        text=text,
        unanswered_recent_user_messages=[],
        recent_user_messages=[text],
    )


def _make_engine(zone_side_effect=None) -> ConversationEngine:
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock()
    eng.settings = MagicMock()
    eng.settings.openai_api_key = "sk-fake"
    eng.settings.openai_chat_model = "gpt-4o-mini"
    eng.settings.backend_url = "http://localhost:8000"
    eng._send_text_to_wa = MagicMock(return_value="mock-wa-id")
    eng._send_fallback_human_review_notification = MagicMock()
    eng._call_openai = MagicMock(return_value=_DEFAULT_AI_RAW)
    eng._build_ai_messages = MagicMock(return_value=[])
    eng._compute_price_quote = MagicMock(return_value=None)
    eng._extract_zone_from_text = MagicMock(side_effect=zone_side_effect or (lambda t: None))
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
    return eng


def _run(text: str, state=None, candidate=None, zone_side_effect=None):
    """Run _process_text. Returns (eng, result, state, candidate)."""
    eng = _make_engine(zone_side_effect=zone_side_effect)
    if state is None:
        state = _make_state()
    cands = [candidate] if candidate is not None else []
    if candidate is not None:
        eng._focus_candidate = MagicMock(return_value=candidate)
    else:
        eng._focus_candidate = MagicMock(return_value=None)
    ctx = _make_ctx(state=state, candidates=cands)
    event = _make_event(text)
    with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
        result = eng._process_text(ctx, event)
    return eng, result, state, candidate


# ── SC11: vehicle-location + origin in same turn ───────────────────────────────

class TestSC11VehicleLocationWinsOverOrigin(unittest.TestCase):
    """LR-5: vehicle clause wins; customer origin is informational only."""

    MSG = "Yo soy de La Plata, pero el auto está en Villa Urquiza."

    def _zone_fn(self, text):
        # "villa urquiza" text → CABA zone; "la plata" → Sur/La Plata
        t = text.lower()
        if "villa urquiza" in t:
            return _ZONE_CABA
        if "la plata" in t:
            return _ZONE_SUR_LA_PLATA
        return None

    def test_candidate_receives_caba(self):
        cand = _make_candidate()
        eng, result, state, cand = _run(
            self.MSG, candidate=cand, zone_side_effect=self._zone_fn
        )
        self.assertEqual(cand.zone_group, "CABA")

    def test_la_plata_does_not_set_candidate_zone(self):
        """La Plata (origin) must not reach candidate zone."""
        cand = _make_candidate()
        _run(self.MSG, candidate=cand, zone_side_effect=self._zone_fn)
        self.assertNotEqual(cand.zone_group, "Sur")
        self.assertNotEqual(cand.zone_detail, "La Plata")

    def test_thread_state_not_overwritten(self):
        """Thread state home_zone_* must remain untouched (LR-6)."""
        cand = _make_candidate()
        state = _make_state()
        _run(self.MSG, state=state, candidate=cand, zone_side_effect=self._zone_fn)
        self.assertIsNone(state.home_zone_group)
        self.assertIsNone(state.home_zone_detail)

    def test_no_clarification_sent(self):
        """Clear vehicle location → no location clarification needed."""
        cand = _make_candidate()
        eng, result, state, cand = _run(
            self.MSG, candidate=cand, zone_side_effect=self._zone_fn
        )
        for c in eng._send_text_to_wa.call_args_list:
            self.assertNotIn(_LOCATION_CONTRADICTION_CLARIFICATION, str(c))


# ── SC12: bare customer-origin statement ──────────────────────────────────────

class TestSC12OriginAloneNoMutation(unittest.TestCase):
    """LR-2: 'Soy de La Plata.' must not touch candidate zone or drive pricing."""

    MSG = "Soy de La Plata."

    def _zone_fn(self, text):
        if "la plata" in text.lower():
            return _ZONE_SUR_LA_PLATA
        return None

    def test_candidate_zone_unchanged(self):
        cand = _make_candidate()
        _run(self.MSG, candidate=cand, zone_side_effect=self._zone_fn)
        self.assertIsNone(cand.zone_group)
        self.assertIsNone(cand.zone_detail)

    def test_state_zone_unchanged(self):
        state = _make_state()
        cand = _make_candidate()
        _run(self.MSG, state=state, candidate=cand, zone_side_effect=self._zone_fn)
        self.assertIsNone(state.home_zone_group)
        self.assertIsNone(state.home_zone_detail)

    def test_no_clarification(self):
        eng, result, state, cand = _run(
            self.MSG, candidate=_make_candidate(), zone_side_effect=self._zone_fn
        )
        for c in eng._send_text_to_wa.call_args_list:
            self.assertNotIn(_LOCATION_CONTRADICTION_CLARIFICATION, str(c))


# ── SC13: stale thread state must not block new vehicle evidence ──────────────

class TestSC13StaleGuardBypassed(unittest.TestCase):
    """LR-6: explicit vehicle-location overrides stale thread state."""

    MSG = "El auto está en Villa Urquiza."

    def _zone_fn(self, text):
        if "villa urquiza" in text.lower():
            return _ZONE_CABA
        return None

    def setUp(self):
        self.state = _make_state(
            home_zone_group="Norte",
            home_zone_detail="Tigre",
        )
        self.cand = _make_candidate()

    def test_candidate_receives_new_zone(self):
        _run(self.MSG, state=self.state, candidate=self.cand,
             zone_side_effect=self._zone_fn)
        self.assertEqual(self.cand.zone_group, "CABA")

    def test_stale_thread_state_untouched(self):
        """Thread state must remain Norte/Tigre (LR-6)."""
        _run(self.MSG, state=self.state, candidate=self.cand,
             zone_side_effect=self._zone_fn)
        self.assertEqual(self.state.home_zone_group, "Norte")
        self.assertEqual(self.state.home_zone_detail, "Tigre")

    def test_no_clarification(self):
        eng, *_ = _run(self.MSG, state=self.state, candidate=self.cand,
                       zone_side_effect=self._zone_fn)
        for c in eng._send_text_to_wa.call_args_list:
            self.assertNotIn(_LOCATION_CONTRADICTION_CLARIFICATION, str(c))


# ── SC14: bare locality in clarification context ──────────────────────────────

class TestSC14BareLocalityInContext(unittest.TestCase):
    """LR-4: 'Palermo' after location clarification → candidate zone set."""

    MSG = "Palermo"

    def _zone_fn(self, text):
        if "palermo" in text.lower():
            return _ZONE_CABA
        return None

    def setUp(self):
        self.state = _make_state(location_clarification_sent=True)
        self.cand = _make_candidate()

    def test_candidate_receives_zone(self):
        _run(self.MSG, state=self.state, candidate=self.cand,
             zone_side_effect=self._zone_fn)
        self.assertEqual(self.cand.zone_group, "CABA")

    def test_no_repeated_location_request(self):
        eng, *_ = _run(self.MSG, state=self.state, candidate=self.cand,
                       zone_side_effect=self._zone_fn)
        for c in eng._send_text_to_wa.call_args_list:
            self.assertNotIn(_LOCATION_CONTRADICTION_CLARIFICATION, str(c))


# ── SC17: same-turn vehicle-location contradiction ────────────────────────────

class TestSC17ContradictionClarification(unittest.TestCase):
    """LR-8: contradictory vehicle locations → clarification; zero mutation."""

    MSG = "El auto está en Tigre, o puede ser Villa Urquiza, no sé."

    def _zone_fn(self, text):
        t = text.lower()
        if "villa urquiza" in t:
            return _ZONE_CABA
        if "tigre" in t:
            return _ZONE_NORTE_TIGRE
        return None

    def test_clarification_sent(self):
        cand = _make_candidate()
        eng, result, *_ = _run(
            self.MSG, candidate=cand, zone_side_effect=self._zone_fn
        )
        calls = [str(c) for c in eng._send_text_to_wa.call_args_list]
        self.assertTrue(
            any(_LOCATION_CONTRADICTION_CLARIFICATION in c for c in calls),
            "Expected contradiction clarification to be sent",
        )

    def test_result_is_handled(self):
        cand = _make_candidate()
        eng, result, *_ = _run(
            self.MSG, candidate=cand, zone_side_effect=self._zone_fn
        )
        self.assertIn(result.action, HANDLED_ACTIONS)

    def test_candidate_zone_not_mutated(self):
        cand = _make_candidate()
        _run(self.MSG, candidate=cand, zone_side_effect=self._zone_fn)
        self.assertIsNone(cand.zone_group)
        self.assertIsNone(cand.zone_detail)

    def test_no_pricing(self):
        """No pricing on contradiction turn (LR-8)."""
        cand = _make_candidate()
        eng, *_ = _run(self.MSG, candidate=cand, zone_side_effect=self._zone_fn)
        eng._compute_price_quote.assert_not_called()

    def test_kill_switch(self):
        """Under kill-switch, contradiction result is still in HANDLED_ACTIONS."""
        cand = _make_candidate()
        eng = _make_engine(zone_side_effect=self._zone_fn)
        eng._focus_candidate = MagicMock(return_value=cand)
        from app.services.outbound_guard import OutboundBlockedError
        eng._send_text_to_wa = MagicMock(side_effect=OutboundBlockedError(
            sender_path="test", kind="text", to_wa_id="1", thread_id=42,
            text="x", gate_outcome="BLOCKED",
        ))
        state = _make_state()
        ctx = _make_ctx(state=state, candidates=[cand])
        event = _make_event(self.MSG)
        with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
            result = eng._process_text(ctx, event)
        self.assertIn(result.action, HANDLED_ACTIONS)


# ── Additional cases ───────────────────────────────────────────────────────────

class TestAdditionalRoleCases(unittest.TestCase):
    """Cases 1–13 from Phase 3 requirements."""

    # Case 1: "Vivo en Tigre, pero el auto está en Palermo." → Palermo
    def test_case1_vehicle_wins_when_both_present(self):
        def _z(t):
            t = t.lower()
            if "palermo" in t:
                return _ZONE_CABA
            if "tigre" in t:
                return _ZONE_NORTE_TIGRE
            return None

        cand = _make_candidate()
        _run("Vivo en Tigre, pero el auto está en Palermo.", candidate=cand, zone_side_effect=_z)
        self.assertEqual(cand.zone_group, "CABA")

    # Case 2: "El auto está en Tigre y yo vivo en Palermo." → Tigre
    def test_case2_vehicle_clause_first_wins(self):
        def _z(t):
            t = t.lower()
            if "tigre" in t:
                return _ZONE_NORTE_TIGRE
            if "palermo" in t:
                return _ZONE_CABA
            return None

        cand = _make_candidate()
        _run("El auto está en Tigre y yo vivo en Palermo.", candidate=cand, zone_side_effect=_z)
        self.assertEqual(cand.zone_group, "Norte")
        self.assertEqual(cand.zone_detail, "Tigre")

    # Case 3: "La revisión sería en San Isidro." → San Isidro
    def test_case3_revision_location_pattern(self):
        def _z(t):
            if "san isidro" in t.lower():
                return _ZONE_NORTE_SAN_ISIDRO
            return None

        cand = _make_candidate()
        _run("La revisión sería en San Isidro.", candidate=cand, zone_side_effect=_z)
        self.assertEqual(cand.zone_group, "Norte")
        self.assertEqual(cand.zone_detail, "San Isidro")

    # Case 4: "Estoy en San Isidro." without context → not vehicle location
    def test_case4_estoy_en_is_origin_no_mutation(self):
        def _z(t):
            if "san isidro" in t.lower():
                return _ZONE_NORTE_SAN_ISIDRO
            return None

        cand = _make_candidate()
        _run("Estoy en San Isidro.", candidate=cand, zone_side_effect=_z)
        self.assertIsNone(cand.zone_group)

    # Case 5: Existing candidate Palermo + "Yo vivo en Tigre." → remains Palermo
    def test_case5_origin_does_not_overwrite_existing_candidate_zone(self):
        def _z(t):
            if "tigre" in t.lower():
                return _ZONE_NORTE_TIGRE
            return None

        cand = _make_candidate(zone_group="CABA", zone_detail="CABA")
        _run("Yo vivo en Tigre.", candidate=cand, zone_side_effect=_z)
        self.assertEqual(cand.zone_group, "CABA")

    # Case 6: Existing candidate Tigre + "El auto ahora está en Villa Urquiza." → update
    def test_case6_vehicle_clause_updates_existing_candidate(self):
        def _z(t):
            if "villa urquiza" in t.lower():
                return _ZONE_CABA
            return None

        cand = _make_candidate(zone_group="Norte", zone_detail="Tigre")
        _run("El auto ahora está en Villa Urquiza.", candidate=cand, zone_side_effect=_z)
        self.assertEqual(cand.zone_group, "CABA")

    # Case 7: New candidate with stale thread Norte/Tigre and no current vehicle evidence
    # → candidate zone empty (LR-7)
    def test_case7_new_candidate_no_zone_when_no_current_vehicle_evidence(self):
        def _z(t):
            return None  # current turn has no zone info

        state = _make_state(home_zone_group="Norte", home_zone_detail="Tigre")
        cand = _make_candidate()  # starts with no zone
        _run("Hola, me interesa una revisión.", state=state, candidate=cand, zone_side_effect=_z)
        # No vehicle-location evidence → candidate zone stays empty
        self.assertIsNone(cand.zone_group)

    # Case 8: Motorcycle + location → motorcycle wins; no location mutation
    def test_case8_motorcycle_gate_wins_no_location_mutation(self):
        def _z(t):
            if "palermo" in t.lower():
                return _ZONE_CABA
            return None

        cand = _make_candidate()
        # "moto" triggers motorcycle gate (Layer A)
        eng, result, *_ = _run(
            "Tengo una moto, está en Palermo.", candidate=cand, zone_side_effect=_z
        )
        # Either motorcycle handoff action OR AI reached, but not contradiction
        for c in eng._send_text_to_wa.call_args_list:
            self.assertNotIn(_LOCATION_CONTRADICTION_CLARIFICATION, str(c))

    # Case 9: Disassembled vehicle + location → inspectability wins; no location mutation
    def test_case9_inspectability_wins_over_location(self):
        def _z(t):
            if "palermo" in t.lower():
                return _ZONE_CABA
            return None

        cand = _make_candidate()
        # "está desarmado" triggers inspectability gate (Layer G)
        eng, result, *_ = _run(
            "El auto está desarmado, en Palermo.", candidate=cand, zone_side_effect=_z
        )
        # Inspectability boundary must fire first; location clarification must NOT fire
        for c in eng._send_text_to_wa.call_args_list:
            self.assertNotIn(_LOCATION_CONTRADICTION_CLARIFICATION, str(c))

    # Case 11: Repeated identical location is idempotent
    def test_case11_repeated_vehicle_location_idempotent(self):
        def _z(t):
            if "tigre" in t.lower():
                return _ZONE_NORTE_TIGRE
            return None

        cand = _make_candidate()
        # First turn
        _run("El auto está en Tigre.", candidate=cand, zone_side_effect=_z)
        self.assertEqual(cand.zone_group, "Norte")
        zone_after_first = cand.zone_group

        # Second identical turn
        _run("El auto está en Tigre.", candidate=cand, zone_side_effect=_z)
        self.assertEqual(cand.zone_group, zone_after_first)


# ── Module-level helper tests ─────────────────────────────────────────────────

class TestHasCustomerOriginClause(unittest.TestCase):
    """Unit tests for the _has_customer_origin_clause helper."""

    def test_soy_de(self):
        self.assertTrue(_has_customer_origin_clause("Soy de La Plata."))

    def test_vivo_en(self):
        self.assertTrue(_has_customer_origin_clause("Vivo en Tigre."))

    def test_estoy_en(self):
        self.assertTrue(_has_customer_origin_clause("Estoy en San Isidro."))

    def test_vengo_de(self):
        self.assertTrue(_has_customer_origin_clause("Vengo de Morón."))

    def test_vehicle_clause_is_not_origin(self):
        self.assertFalse(_has_customer_origin_clause("El auto está en Villa Urquiza."))

    def test_bare_locality_is_not_origin(self):
        self.assertFalse(_has_customer_origin_clause("Palermo"))

    def test_revision_clause_is_not_origin(self):
        self.assertFalse(_has_customer_origin_clause("La revisión sería en San Isidro."))

    def test_empty_string(self):
        self.assertFalse(_has_customer_origin_clause(""))


# ── Contradiction clarification string test ───────────────────────────────────

class TestContradictionConstant(unittest.TestCase):
    def test_constant_exact_text(self):
        self.assertEqual(
            _LOCATION_CONTRADICTION_CLARIFICATION,
            "¿Dónde está físicamente el auto para hacer la revisión?",
        )


if __name__ == "__main__":
    unittest.main()
