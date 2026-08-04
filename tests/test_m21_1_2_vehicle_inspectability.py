"""M21.1.2 — Vehicle Inspectability Gate — Executable Tests.

Source of truth: docs/M21_1_2_vehicle_inspectability.md (approved 2026-08-04, Lara Dittmar).

Decision table I01–I18 plus zero-mutation, precedence, idempotency, and kill-switch assertions.

Observable proxies:
  DISASSEMBLED_BOUNDARY  → result.action in HANDLED_ACTIONS
                           AND _send_text_to_wa called with _INSPECTABILITY_DISASSEMBLED_REPLY
                           AND _call_openai.call_count == 0
                           AND no candidate/zone/pricing/routing/revision mutation
  CLARIFY                → result.action in HANDLED_ACTIONS
                           AND _send_text_to_wa called with _INSPECTABILITY_NONRUNNING_CLARIFY
                           AND _call_openai.call_count == 0
  CONTINUE               → _call_openai.call_count == 1  (AI reached)
  MOTORCYCLE_HANDOFF     → _send_text_to_wa called with _FALLBACK_WARM_HANDOFF
  KILL_SWITCH            → result.action == "inspectability_gate_blocked"

All tests: SQLite in-memory; external services mocked; OUTBOUND_ENABLED=false.
"""
from __future__ import annotations

import os
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

_pg_dialect.JSONB = sqlalchemy.JSON   # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON      # type: ignore[attr-defined]

import json
from app.services.conversation_engine import (  # noqa: E402
    ConversationEngine,
    _FALLBACK_WARM_HANDOFF,
    _INSPECTABILITY_DISASSEMBLED_REPLY,
    _INSPECTABILITY_NONRUNNING_CLARIFY,
    _INTENT_PREPURCHASE,
    _detect_disassembled_vehicle,
    _detect_non_running_vehicle,
    _detect_assembled_accessible_confirmation,
    _detect_historical_or_hypothetical_context,
    _detect_access_barrier,
)
from app.schemas.conversation import ConversationHandleIn, HANDLED_ACTIONS  # noqa: E402
from app.services.outbound_guard import OutboundBlockedError                # noqa: E402

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


def _make_ctx(state=None, lead=None, candidates=None) -> types.SimpleNamespace:
    ctx = types.SimpleNamespace()
    ctx.thread = types.SimpleNamespace(id=42)
    ctx.contact = types.SimpleNamespace(wa_id="5491199999999")
    ctx.lead = lead if lead is not None else _make_lead()
    ctx.state = state if state is not None else _make_state()
    ctx.candidates = candidates if candidates is not None else []
    return ctx


def _make_event(text=None) -> ConversationHandleIn:
    return ConversationHandleIn(
        thread_id=42,
        wa_message_id="test-insp-wa-id",
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


def _run(text, state_kwargs=None, candidates=None, lookup_vehicle_return=None):
    """Run _process_text. Returns (eng, result, state)."""
    eng = _make_engine()
    state = _make_state(**(state_kwargs or {}))
    ctx = _make_ctx(state=state, candidates=candidates or [])
    event = _make_event(text=text)
    with patch("app.services.conversation_engine.lookup_vehicle", return_value=lookup_vehicle_return):
        result = eng._process_text(ctx, event)
    return eng, result, state


def _run_with_kill_switch(text, state_kwargs=None):
    """Run with OUTBOUND_ENABLED unset (kill switch active)."""
    eng = _make_engine()
    eng._send_text_to_wa = MagicMock(side_effect=OutboundBlockedError(
        sender_path="test", kind="text", to_wa_id="5491199999999"
    ))
    state = _make_state(**(state_kwargs or {}))
    ctx = _make_ctx(state=state)
    event = _make_event(text=text)
    with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
        result = eng._process_text(ctx, event)
    return eng, result, state


# ── Zero-mutation assertion helpers ───────────────────────────────────────────

def _assert_zero_commercial_mutation(test, eng):
    """Verify no candidate, zone, pricing, routing, or AI mutation occurred."""
    test.assertEqual(eng._call_openai.call_count, 0, "AI must not be called")
    test.assertEqual(eng._create_candidate_from_catalog.call_count, 0, "No candidate creation")
    test.assertEqual(eng._apply_candidate.call_count, 0, "No candidate mutation")
    test.assertEqual(eng._apply_extracted.call_count, 0, "No extracted data applied")
    test.assertEqual(eng._routing_gate.call_count, 0, "Routing gate must not run")


def _assert_disassembled_boundary(test, eng, result):
    test.assertIn(result.action, HANDLED_ACTIONS)
    test.assertEqual(eng._call_openai.call_count, 0, "Disassembled boundary must not call AI")
    sent_text = eng._send_text_to_wa.call_args[0][1]
    test.assertEqual(sent_text, _INSPECTABILITY_DISASSEMBLED_REPLY,
                     "Must send disassembled boundary reply")


def _assert_clarify(test, eng, result):
    test.assertIn(result.action, HANDLED_ACTIONS)
    test.assertEqual(eng._call_openai.call_count, 0, "Clarification must not call AI")
    sent_text = eng._send_text_to_wa.call_args[0][1]
    test.assertEqual(sent_text, _INSPECTABILITY_NONRUNNING_CLARIFY,
                     "Must send accessibility clarification")


def _assert_continue(test, eng):
    test.assertEqual(eng._call_openai.call_count, 1, "Continue must reach AI")


# ═══════════════════════════════════════════════════════════════════════════════
# Unit tests for detection functions
# ═══════════════════════════════════════════════════════════════════════════════

class TestDetectionFunctions(unittest.TestCase):
    """Unit tests for the five module-level detection helpers."""

    def test_disassembled_desarmado(self):
        self.assertTrue(_detect_disassembled_vehicle("El auto está desarmado"))

    def test_disassembled_desmontado(self):
        self.assertTrue(_detect_disassembled_vehicle("Está desmontado"))

    def test_disassembled_sin_motor(self):
        self.assertTrue(_detect_disassembled_vehicle("Está sin motor"))

    def test_disassembled_motor_afuera(self):
        self.assertTrue(_detect_disassembled_vehicle("Tiene el motor afuera"))

    def test_disassembled_motor_desmontado(self):
        self.assertTrue(_detect_disassembled_vehicle("Motor desmontado"))

    def test_disassembled_sin_ruedas(self):
        self.assertTrue(_detect_disassembled_vehicle("Está sin ruedas"))

    def test_disassembled_partes_sueltas(self):
        self.assertTrue(_detect_disassembled_vehicle("Tiene partes sueltas"))

    def test_disassembled_piezas_desmontadas(self):
        self.assertTrue(_detect_disassembled_vehicle("Tiene piezas desmontadas"))

    def test_disassembled_accent_insensitive(self):
        self.assertTrue(_detect_disassembled_vehicle("esta desarmado"))

    def test_disassembled_false_for_running(self):
        self.assertFalse(_detect_disassembled_vehicle("No arranca pero está completo"))

    def test_nonrunning_no_arranca(self):
        self.assertTrue(_detect_non_running_vehicle("No arranca"))

    def test_nonrunning_no_enciende(self):
        self.assertTrue(_detect_non_running_vehicle("No enciende"))

    def test_nonrunning_bateria_muerta(self):
        self.assertTrue(_detect_non_running_vehicle("Tiene la batería muerta"))

    def test_nonrunning_esta_parado(self):
        self.assertTrue(_detect_non_running_vehicle("El auto está parado"))

    def test_nonrunning_no_funciona(self):
        self.assertTrue(_detect_non_running_vehicle("No funciona"))

    def test_nonrunning_no_andando(self):
        self.assertTrue(_detect_non_running_vehicle("No está andando"))

    def test_nonrunning_empujarlo(self):
        self.assertTrue(_detect_non_running_vehicle("Hay que empujarlo"))

    def test_nonrunning_remolcarlo(self):
        self.assertTrue(_detect_non_running_vehicle("Hay que remolcarlo"))

    def test_nonrunning_false_for_assembled(self):
        self.assertFalse(_detect_non_running_vehicle("Está armado"))

    def test_assembled_esta_armado(self):
        self.assertTrue(_detect_assembled_accessible_confirmation("Está armado"))

    def test_assembled_esta_completo(self):
        self.assertTrue(_detect_assembled_accessible_confirmation("Está completo"))

    def test_assembled_se_puede_revisar(self):
        self.assertTrue(_detect_assembled_accessible_confirmation("Se puede revisar"))

    def test_assembled_se_puede_acceder(self):
        self.assertTrue(_detect_assembled_accessible_confirmation("Se puede acceder"))

    def test_assembled_se_puede_abrir(self):
        self.assertTrue(_detect_assembled_accessible_confirmation("Se puede abrir"))

    def test_assembled_se_puede_levantar(self):
        self.assertTrue(_detect_assembled_accessible_confirmation("Se puede levantar"))

    def test_assembled_tiene_todas_las_ruedas(self):
        self.assertTrue(_detect_assembled_accessible_confirmation("Tiene todas las ruedas"))

    def test_assembled_motor_puesto(self):
        self.assertTrue(_detect_assembled_accessible_confirmation("El motor está puesto"))

    def test_assembled_esta_accesible(self):
        self.assertTrue(_detect_assembled_accessible_confirmation("Está accesible"))

    def test_assembled_false_for_disassembled(self):
        self.assertFalse(_detect_assembled_accessible_confirmation("Está desarmado"))

    def test_historical_estaba(self):
        self.assertTrue(_detect_historical_or_hypothetical_context("Estaba desarmado"))

    def test_historical_estuvo(self):
        self.assertTrue(_detect_historical_or_hypothetical_context("Estuvo desarmado"))

    def test_historical_antes(self):
        self.assertTrue(_detect_historical_or_hypothetical_context("Antes estaba mal"))

    def test_historical_si_estuviera(self):
        self.assertTrue(_detect_historical_or_hypothetical_context("Si estuviera desarmado"))

    def test_historical_hipoteticamente(self):
        self.assertTrue(_detect_historical_or_hypothetical_context("Hipotéticamente"))

    def test_historical_false_for_current(self):
        self.assertFalse(_detect_historical_or_hypothetical_context("El auto está desarmado"))

    def test_access_barrier_no_llaves(self):
        self.assertTrue(_detect_access_barrier("No tenemos las llaves"))

    def test_access_barrier_sin_llaves(self):
        self.assertTrue(_detect_access_barrier("Sin llaves"))

    def test_access_barrier_no_dejan(self):
        self.assertTrue(_detect_access_barrier("No nos dejan revisarlo"))

    def test_access_barrier_no_se_si_dejan(self):
        self.assertTrue(_detect_access_barrier("No sé si nos dejan"))

    def test_access_barrier_false_for_clear(self):
        self.assertFalse(_detect_access_barrier("El auto está armado"))


# ═══════════════════════════════════════════════════════════════════════════════
# Decision table I01–I18
# ═══════════════════════════════════════════════════════════════════════════════

class TestI01DisassembledBoundary(unittest.TestCase):
    """I01: 'El auto está desarmado.' → Disassembled boundary."""

    def test_i01_disassembled_boundary(self):
        eng, result, state = _run(
            "El auto está desarmado.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_disassembled_boundary(self, eng, result)
        _assert_zero_commercial_mutation(self, eng)

    def test_i01_no_needs_human(self):
        _, _, state = _run(
            "El auto está desarmado.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        self.assertFalse(state.needs_human, "needs_human must not be set by disassembled gate")


class TestI02MotorAfueraBoundary(unittest.TestCase):
    """I02: 'Tiene el motor afuera.' → Disassembled boundary."""

    def test_i02_motor_afuera(self):
        eng, result, state = _run(
            "Tiene el motor afuera.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_disassembled_boundary(self, eng, result)
        _assert_zero_commercial_mutation(self, eng)


class TestI03SinRuedasBoundary(unittest.TestCase):
    """I03: 'Está sin ruedas.' → Disassembled boundary."""

    def test_i03_sin_ruedas(self):
        eng, result, state = _run(
            "Está sin ruedas.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_disassembled_boundary(self, eng, result)
        _assert_zero_commercial_mutation(self, eng)


class TestI04NoArrancaClarify(unittest.TestCase):
    """I04: 'No arranca.' → Clarify assembled/accessibility (never disassembled)."""

    def test_i04_no_arranca_clarify(self):
        eng, result, state = _run(
            "No arranca.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_clarify(self, eng, result)

    def test_i04_no_arranca_not_disassembled_reply(self):
        eng, result, state = _run(
            "No arranca.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        sent_text = eng._send_text_to_wa.call_args[0][1]
        self.assertNotEqual(sent_text, _INSPECTABILITY_DISASSEMBLED_REPLY,
                            "No arranca alone must never send disassembled reply")


class TestI05NoEnciendeButAssembled(unittest.TestCase):
    """I05: 'No enciende, pero está armado y completo.' → Continue."""

    def test_i05_no_enciende_assembled_continue(self):
        eng, result, state = _run(
            "No enciende, pero está armado y completo.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_continue(self, eng)


class TestI06EstuvoDesarmadoPeroArmado(unittest.TestCase):
    """I06: 'Estuvo desarmado, pero ya está armado.' → Continue (historical)."""

    def test_i06_historical_disassembly_continue(self):
        eng, result, state = _run(
            "Estuvo desarmado, pero ya está armado.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_continue(self, eng)


class TestI07MedioDesarmadoClarify(unittest.TestCase):
    """I07: 'Está medio desarmado, pero se puede revisar.' → Clarify (contradiction)."""

    def test_i07_contradiction_clarify(self):
        eng, result, state = _run(
            "Está medio desarmado, pero se puede revisar.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_clarify(self, eng, result)


class TestI08NoArrancaMotorAfuera(unittest.TestCase):
    """I08: 'No arranca y tiene el motor afuera.' → Disassembled boundary (disassembled wins)."""

    def test_i08_nonrunning_plus_disassembled_boundary(self):
        eng, result, state = _run(
            "No arranca y tiene el motor afuera.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_disassembled_boundary(self, eng, result)
        _assert_zero_commercial_mutation(self, eng)


class TestI09NoArrancaSeededAccessible(unittest.TestCase):
    """I09: 'No arranca, pero se puede abrir y revisar todo.' → Continue."""

    def test_i09_nonrunning_accessible_continue(self):
        eng, result, state = _run(
            "No arranca, pero se puede abrir y revisar todo.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_continue(self, eng)


class TestI10ArmadoSinLlavesClarify(unittest.TestCase):
    """I10: 'Está armado, pero no tenemos las llaves.' → Clarify access (BR-I4)."""

    def test_i10_armado_no_llaves_clarify(self):
        eng, result, state = _run(
            "Está armado, pero no tenemos las llaves.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_clarify(self, eng, result)


class TestI11TallerAccess(unittest.TestCase):
    """I11: 'Está en un taller y no sé si nos dejan revisarlo.' → Clarify or human review."""

    def test_i11_taller_access_clarify(self):
        eng, result, state = _run(
            "Está en un taller y no sé si nos dejan revisarlo.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_clarify(self, eng, result)


class TestI12HypotheticalDisassembled(unittest.TestCase):
    """I12: '¿Qué pasa si estuviera desarmado?' → Informational only (gate passes)."""

    def test_i12_hypothetical_gate_passes(self):
        eng, result, state = _run(
            "¿Qué pasa si estuviera desarmado?",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_continue(self, eng)


class TestI13MotorcyclePrecedence(unittest.TestCase):
    """I13: 'Tengo una moto desarmada.' → Motorcycle handoff (Layer A wins)."""

    def test_i13_motorcycle_wins_over_inspectability(self):
        eng, result, state = _run(
            "Tengo una moto desarmada.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        # Motorcycle handoff fires; inspectability must not send disassembled reply
        self.assertIn(result.action, HANDLED_ACTIONS)
        sent_texts = [call[0][1] for call in eng._send_text_to_wa.call_args_list]
        self.assertNotIn(_INSPECTABILITY_DISASSEMBLED_REPLY, sent_texts,
                         "Inspectability reply must not fire when motorcycle gate wins")
        self.assertNotIn(_INSPECTABILITY_NONRUNNING_CLARIFY, sent_texts,
                         "Inspectability clarify must not fire when motorcycle gate wins")
        # Verify warm handoff was sent (motorcycle handoff path)
        self.assertIn(_FALLBACK_WARM_HANDOFF, sent_texts)


class TestI14QuieroRevisarDesarmado(unittest.TestCase):
    """I14: 'Quiero revisar un auto desarmado en Palermo.' → Disassembled boundary; no commercial mutation."""

    def test_i14_disassembled_boundary_no_mutation(self):
        eng, result, state = _run(
            "Quiero revisar un auto desarmado en Palermo.",
        )
        _assert_disassembled_boundary(self, eng, result)
        _assert_zero_commercial_mutation(self, eng)

    def test_i14_no_needs_human(self):
        _, _, state = _run("Quiero revisar un auto desarmado en Palermo.")
        self.assertFalse(state.needs_human)


class TestI15PriorIntentMotorAfuera(unittest.TestCase):
    """I15: Prior inspection intent + 'Ahora me dicen que tiene el motor afuera.' → Disassembled boundary."""

    def test_i15_prior_intent_current_disassembly(self):
        eng, result, state = _run(
            "Ahora me dicen que tiene el motor afuera.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_disassembled_boundary(self, eng, result)
        _assert_zero_commercial_mutation(self, eng)


class TestI16PriorClarifyConfirmedAssembled(unittest.TestCase):
    """I16: Prior clarification + 'Sí, está armado y accesible.' → Continue; do not repeat."""

    def test_i16_confirmed_assembled_continue(self):
        eng, result, state = _run(
            "Sí, está armado y accesible.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_continue(self, eng)

    def test_i16_no_clarification_repeated(self):
        eng, result, state = _run(
            "Sí, está armado y accesible.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        sent_texts = [call[0][1] for call in eng._send_text_to_wa.call_args_list]
        self.assertNotIn(_INSPECTABILITY_NONRUNNING_CLARIFY, sent_texts,
                         "Clarification must not be repeated after assembly confirmed")


class TestI17KillSwitchDisassembled(unittest.TestCase):
    """I17: Kill switch + disassembled → handled blocked action."""

    def test_i17_kill_switch_disassembled_blocked(self):
        eng, result, state = _run_with_kill_switch(
            "El auto está desarmado.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        self.assertEqual(result.action, "inspectability_gate_blocked")
        self.assertIn(result.action, HANDLED_ACTIONS)

    def test_i17_kill_switch_handled_true(self):
        _, result, _ = _run_with_kill_switch(
            "El auto está desarmado.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        self.assertTrue(result.handled)


class TestI18KillSwitchClarification(unittest.TestCase):
    """I18: Kill switch + clarification (non-running) → handled blocked action."""

    def test_i18_kill_switch_nonrunning_blocked(self):
        eng, result, state = _run_with_kill_switch(
            "No arranca.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        self.assertEqual(result.action, "inspectability_gate_blocked")
        self.assertIn(result.action, HANDLED_ACTIONS)

    def test_i18_kill_switch_handled_true(self):
        _, result, _ = _run_with_kill_switch(
            "No arranca.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        self.assertTrue(result.handled)


# ═══════════════════════════════════════════════════════════════════════════════
# Additional zero-mutation guarantees
# ═══════════════════════════════════════════════════════════════════════════════

class TestZeroMutationGuarantees(unittest.TestCase):
    """Disassembled boundary and clarification must not touch commercial state."""

    def _assert_state_unchanged(self, state_before: dict, state: types.SimpleNamespace):
        for k, v in state_before.items():
            self.assertEqual(getattr(state, k), v, f"state.{k} must not be mutated")

    def test_disassembled_no_lead_flag_change(self):
        lead = _make_lead(flag="PRESUPUESTANDO")
        eng = _make_engine()
        state = _make_state(last_intent=_INTENT_PREPURCHASE)
        ctx = _make_ctx(state=state, lead=lead)
        event = _make_event("El auto está desarmado.")
        with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
            eng._process_text(ctx, event)
        self.assertEqual(lead.flag, "PRESUPUESTANDO", "lead.flag must not change on disassembled")

    def test_disassembled_no_extract_zone(self):
        eng, result, state = _run(
            "El auto está desarmado en San Isidro.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_disassembled_boundary(self, eng, result)
        self.assertEqual(eng._extract_zone_from_text.call_count, 0,
                         "Zone extraction must not run on disassembled boundary")

    def test_disassembled_no_pricing(self):
        eng, result, state = _run(
            "El auto está desarmado.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_disassembled_boundary(self, eng, result)
        self.assertEqual(eng._compute_price_quote.call_count, 0,
                         "Pricing must not run on disassembled boundary")

    def test_nonrunning_no_commercial_mutation(self):
        eng, result, state = _run(
            "No arranca.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_clarify(self, eng, result)
        self.assertEqual(eng._call_openai.call_count, 0)
        self.assertEqual(eng._routing_gate.call_count, 0)
        self.assertEqual(eng._apply_extracted.call_count, 0)

    def test_access_barrier_no_commercial_mutation(self):
        eng, result, state = _run(
            "No tenemos las llaves.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_clarify(self, eng, result)
        self.assertEqual(eng._call_openai.call_count, 0)
        self.assertEqual(eng._routing_gate.call_count, 0)


# ═══════════════════════════════════════════════════════════════════════════════
# Additional behavioral guards
# ═══════════════════════════════════════════════════════════════════════════════

class TestBehavioralGuards(unittest.TestCase):
    """Extra assertions not mapped to a single decision-table row."""

    def test_no_arranca_alone_is_never_disassembled(self):
        eng, result, state = _run(
            "No arranca",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        sent_text = eng._send_text_to_wa.call_args[0][1]
        self.assertNotEqual(sent_text, _INSPECTABILITY_DISASSEMBLED_REPLY)

    def test_historical_disassembled_does_not_trigger_decline(self):
        eng, result, state = _run(
            "Estaba desarmado el mes pasado.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        # Gate passes; AI is reached
        _assert_continue(self, eng)

    def test_established_intent_does_not_suppress_disassembled(self):
        """Prior last_intent must not suppress inspectability detection."""
        eng, result, state = _run(
            "El auto está desarmado.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_disassembled_boundary(self, eng, result)

    def test_motor_afuera_no_arranca_disassembled_wins(self):
        """BR-I6: 'no arranca y tiene el motor afuera' → disassembled boundary."""
        eng, result, state = _run(
            "No arranca y tiene el motor afuera.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_disassembled_boundary(self, eng, result)

    def test_no_arranca_pero_completo_continue(self):
        """BR-I6: 'no arranca, pero está completo' → continue."""
        eng, result, state = _run(
            "No arranca, pero está completo.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_continue(self, eng)

    def test_desarmada_feminine_detected(self):
        """Accent/gender-insensitive: desarmada triggers boundary."""
        eng, result, state = _run(
            "La moto está desarmada.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE, "needs_human": True},
        )
        # needs_human=True → engine returns early before inspectability gate
        # Test the detection function directly for gender insensitivity
        self.assertTrue(_detect_disassembled_vehicle("La moto está desarmada."))

    def test_desmontada_feminine_detected(self):
        self.assertTrue(_detect_disassembled_vehicle("Está desmontada."))

    def test_idempotent_disassembled_no_duplicate_candidate(self):
        """Repeated disassembled messages produce no candidate mutation."""
        for _ in range(3):
            eng, result, state = _run(
                "El auto está desarmado.",
                state_kwargs={"last_intent": _INTENT_PREPURCHASE},
            )
            _assert_disassembled_boundary(self, eng, result)
            self.assertEqual(eng._create_candidate_from_catalog.call_count, 0)

    def test_assembled_after_nonrunning_no_clarify_repeated(self):
        """Once assembled confirmed (I16), gate returns None — no second clarification."""
        eng, result, state = _run(
            "Sí, el auto está armado y se puede revisar.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_continue(self, eng)
        sent_texts = [call[0][1] for call in eng._send_text_to_wa.call_args_list]
        self.assertNotIn(_INSPECTABILITY_NONRUNNING_CLARIFY, sent_texts)

    def test_inspectability_blocked_is_in_handled_actions(self):
        self.assertIn("inspectability_gate_blocked", HANDLED_ACTIONS)

    def test_quoted_stage_disassembled_still_blocked(self):
        """Inspectability gate fires at all stages, not only QUALIFYING."""
        eng, result, state = _run(
            "Ahora me dicen que está desarmado.",
            state_kwargs={"last_stage": STAGE_QUOTED, "last_intent": _INTENT_PREPURCHASE},
        )
        _assert_disassembled_boundary(self, eng, result)

    def test_scheduling_stage_disassembled_still_blocked(self):
        eng, result, state = _run(
            "Ahora me dicen que está desarmado.",
            state_kwargs={"last_stage": STAGE_SCHEDULING, "last_intent": _INTENT_PREPURCHASE},
        )
        _assert_disassembled_boundary(self, eng, result)


# ═══════════════════════════════════════════════════════════════════════════════
# M21.1.2-FV Phase 2 — Precedence tests (QUOTED and SCHEDULING stage)
# ═══════════════════════════════════════════════════════════════════════════════
#
# These tests use messages that combine an acceptance/scheduling signal with
# an inspectability signal.  Layer G must intercept the turn BEFORE any QUOTED
# or SCHEDULING handler runs.
#
# Observable proxies:
#   _try_schedule_and_flow.call_count == 0  → scheduling handler not called
#   _handle_quoted_acceptance.call_count == 0  → quoted acceptance not called
#   lead.flag unchanged  → no commercial flag mutation
# ─────────────────────────────────────────────────────────────────────────────

def _run_with_extras(text, state_kwargs=None, lead_kwargs=None):
    """Like _run but also mocks _handle_quoted_acceptance and returns lead."""
    eng = _make_engine()
    eng._handle_quoted_acceptance = MagicMock(return_value=MagicMock(action="replied", handled=True))
    eng._handle_scheduling_escalation = MagicMock(return_value=None)
    state = _make_state(**(state_kwargs or {}))
    lead = _make_lead(**(lead_kwargs or {}))
    ctx = _make_ctx(state=state, lead=lead)
    event = _make_event(text=text)
    with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
        result = eng._process_text(ctx, event)
    return eng, result, state, lead


class TestFVP1QuotedAcceptanceWithDisassembled(unittest.TestCase):
    """P1: Stage=QUOTED, 'Sí, acepto, pero ahora me dicen que tiene el motor afuera.'
    Require: disassembled boundary; _handle_quoted_acceptance NOT called;
    no accepted flag; no scheduling mutation; no pricing.
    """

    def test_p1_disassembled_boundary(self):
        eng, result, state, lead = _run_with_extras(
            "Sí, acepto, pero ahora me dicen que tiene el motor afuera.",
            state_kwargs={"last_stage": STAGE_QUOTED, "last_intent": _INTENT_PREPURCHASE},
        )
        _assert_disassembled_boundary(self, eng, result)

    def test_p1_quoted_acceptance_not_called(self):
        eng, result, state, lead = _run_with_extras(
            "Sí, acepto, pero ahora me dicen que tiene el motor afuera.",
            state_kwargs={"last_stage": STAGE_QUOTED, "last_intent": _INTENT_PREPURCHASE},
        )
        self.assertEqual(eng._handle_quoted_acceptance.call_count, 0,
                         "P1: _handle_quoted_acceptance must not fire")

    def test_p1_no_accepted_flag(self):
        eng, result, state, lead = _run_with_extras(
            "Sí, acepto, pero ahora me dicen que tiene el motor afuera.",
            state_kwargs={"last_stage": STAGE_QUOTED, "last_intent": _INTENT_PREPURCHASE},
            lead_kwargs={"flag": "PRESUPUESTANDO"},
        )
        self.assertNotEqual(lead.flag, "ACEPTADO", "P1: lead.flag must not become ACEPTADO")

    def test_p1_no_scheduling_mutation(self):
        eng, result, state, lead = _run_with_extras(
            "Sí, acepto, pero ahora me dicen que tiene el motor afuera.",
            state_kwargs={"last_stage": STAGE_QUOTED, "last_intent": _INTENT_PREPURCHASE},
        )
        self.assertEqual(eng._try_schedule_and_flow.call_count, 0,
                         "P1: scheduling must not be attempted")

    def test_p1_no_pricing(self):
        eng, result, state, lead = _run_with_extras(
            "Sí, acepto, pero ahora me dicen que tiene el motor afuera.",
            state_kwargs={"last_stage": STAGE_QUOTED, "last_intent": _INTENT_PREPURCHASE},
        )
        self.assertEqual(eng._compute_price_quote.call_count, 0,
                         "P1: pricing must not run")


class TestFVP1bQuotedDateWithDisassembled(unittest.TestCase):
    """P1b: Stage=QUOTED, 'El miércoles a las 10, pero el motor está afuera.'
    Disassembled gate must fire BEFORE QUOTED date handler can mutate lead.flag.
    """

    def test_p1b_disassembled_boundary(self):
        eng, result, state, lead = _run_with_extras(
            "El miércoles a las 10, pero el motor está afuera.",
            state_kwargs={"last_stage": STAGE_QUOTED, "last_intent": _INTENT_PREPURCHASE},
            lead_kwargs={"flag": "PRESUPUESTANDO"},
        )
        _assert_disassembled_boundary(self, eng, result)

    def test_p1b_lead_flag_not_mutated(self):
        eng, result, state, lead = _run_with_extras(
            "El miércoles a las 10, pero el motor está afuera.",
            state_kwargs={"last_stage": STAGE_QUOTED, "last_intent": _INTENT_PREPURCHASE},
            lead_kwargs={"flag": "PRESUPUESTANDO"},
        )
        self.assertEqual(lead.flag, "PRESUPUESTANDO",
                         "P1b: lead.flag must not mutate to ACEPTADO on disassembled boundary")

    def test_p1b_no_scheduling_attempt(self):
        eng, result, state, lead = _run_with_extras(
            "El miércoles a las 10, pero el motor está afuera.",
            state_kwargs={"last_stage": STAGE_QUOTED, "last_intent": _INTENT_PREPURCHASE},
        )
        self.assertEqual(eng._try_schedule_and_flow.call_count, 0,
                         "P1b: scheduling must not be attempted")


class TestFVP2QuotedAcceptanceWithNonRunning(unittest.TestCase):
    """P2: Stage=QUOTED, 'Sí, acepto, pero el auto no arranca.'
    Require: inspectability clarification; _handle_quoted_acceptance NOT called.
    """

    def test_p2_clarification(self):
        eng, result, state, lead = _run_with_extras(
            "Sí, acepto, pero el auto no arranca.",
            state_kwargs={"last_stage": STAGE_QUOTED, "last_intent": _INTENT_PREPURCHASE},
        )
        _assert_clarify(self, eng, result)

    def test_p2_quoted_acceptance_not_called(self):
        eng, result, state, lead = _run_with_extras(
            "Sí, acepto, pero el auto no arranca.",
            state_kwargs={"last_stage": STAGE_QUOTED, "last_intent": _INTENT_PREPURCHASE},
        )
        self.assertEqual(eng._handle_quoted_acceptance.call_count, 0,
                         "P2: _handle_quoted_acceptance must not fire")


class TestFVP3SchedulingWithDisassembled(unittest.TestCase):
    """P3: Stage=SCHEDULING, 'El miércoles a las 10, pero ahora me dicen que está desarmado.'
    Require: disassembled boundary; scheduling handler NOT called; no booking or Flow.
    """

    def test_p3_disassembled_boundary(self):
        eng, result, state, lead = _run_with_extras(
            "El miércoles a las 10, pero ahora me dicen que está desarmado.",
            state_kwargs={"last_stage": STAGE_SCHEDULING, "last_intent": _INTENT_PREPURCHASE},
        )
        _assert_disassembled_boundary(self, eng, result)

    def test_p3_scheduling_handler_not_called(self):
        eng, result, state, lead = _run_with_extras(
            "El miércoles a las 10, pero ahora me dicen que está desarmado.",
            state_kwargs={"last_stage": STAGE_SCHEDULING, "last_intent": _INTENT_PREPURCHASE},
        )
        self.assertEqual(eng._try_schedule_and_flow.call_count, 0,
                         "P3: scheduling must not be attempted before inspectability gate blocks")

    def test_p3_no_flow(self):
        eng, result, state, lead = _run_with_extras(
            "El miércoles a las 10, pero ahora me dicen que está desarmado.",
            state_kwargs={"last_stage": STAGE_SCHEDULING, "last_intent": _INTENT_PREPURCHASE},
        )
        self.assertEqual(result.action, "replied")
        sent_text = eng._send_text_to_wa.call_args[0][1]
        self.assertEqual(sent_text, _INSPECTABILITY_DISASSEMBLED_REPLY)


class TestFVP4SchedulingWithNonRunning(unittest.TestCase):
    """P4: Stage=SCHEDULING, 'El miércoles a las 10, pero no arranca.'
    Require: inspectability clarification; scheduling handler NOT called.
    """

    def test_p4_clarification(self):
        eng, result, state, lead = _run_with_extras(
            "El miércoles a las 10, pero no arranca.",
            state_kwargs={"last_stage": STAGE_SCHEDULING, "last_intent": _INTENT_PREPURCHASE},
        )
        _assert_clarify(self, eng, result)

    def test_p4_scheduling_handler_not_called(self):
        eng, result, state, lead = _run_with_extras(
            "El miércoles a las 10, pero no arranca.",
            state_kwargs={"last_stage": STAGE_SCHEDULING, "last_intent": _INTENT_PREPURCHASE},
        )
        self.assertEqual(eng._try_schedule_and_flow.call_count, 0,
                         "P4: scheduling must not be attempted before inspectability gate blocks")


# ═══════════════════════════════════════════════════════════════════════════════
# M21.1.2-FV Phase 3 — Repeated non-running idempotency
# ═══════════════════════════════════════════════════════════════════════════════

class TestFVPhase3NonRunningIdempotency(unittest.TestCase):
    """Phase 3: multi-turn non-running clarification behavior.

    Turn 1: 'No arranca.' → one clarification.
    Turn 2 (same state): 'No arranca.' → document behavior.
    Turn 1 + Turn 2 assembly confirmation: continue without repeating.

    NOTE: Without a new schema field (inspectability_clarification_sent),
    the engine cannot distinguish Turn 2 from Turn 1. This class documents
    the limitation and verifies the assembled-confirmation exit path.
    """

    def test_turn1_no_arranca_sends_clarification(self):
        """Turn 1: 'No arranca.' → inspectability clarification sent."""
        eng, result, state = _run(
            "No arranca.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_clarify(self, eng, result)

    def test_turn1_no_state_mutation(self):
        """Turn 1 clarification must not mutate any commercial state."""
        eng, result, state = _run(
            "No arranca.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        self.assertFalse(state.needs_human, "Turn 1: needs_human must not be set")
        self.assertEqual(eng._create_candidate_from_catalog.call_count, 0)
        self.assertEqual(eng._routing_gate.call_count, 0)

    def test_turn2_assembly_confirmation_continues(self):
        """Turn 1 'No arranca.' → Turn 2 'Sí, está armado y accesible.' → continue.

        This is the primary idempotency exit: once the user confirms assembly,
        the gate passes and does NOT repeat the clarification.
        State at Turn 2: same as after Turn 1 (no persistent flags set).
        """
        eng, result, state = _run(
            "Sí, está armado y accesible.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        _assert_continue(self, eng)
        sent_texts = [call[0][1] for call in eng._send_text_to_wa.call_args_list]
        self.assertNotIn(_INSPECTABILITY_NONRUNNING_CLARIFY, sent_texts,
                         "After assembly confirmed, clarification must not be sent again")

    def test_turn2_repeat_no_arranca_behavior(self):
        """Turn 2 with unchanged state: documents current behavior.

        Without a new schema field (inspectability_clarification_sent: bool),
        the engine cannot distinguish this from Turn 1. Both turns send the
        same clarification. This is an acknowledged limitation — the fix
        requires a schema migration that must be approved separately.

        This test documents the behavior rather than asserting a loop-prevention
        that is not yet implemented. Assertion: clarification is still sent
        (not an error reply, not an infinite loop trigger within this turn).
        """
        eng, result, state = _run(
            "No arranca.",
            state_kwargs={"last_intent": _INTENT_PREPURCHASE},
        )
        # Clarification IS sent (same as Turn 1 — current known behavior)
        _assert_clarify(self, eng, result)
        # No commercial mutation on either turn
        self.assertEqual(eng._call_openai.call_count, 0)
        self.assertEqual(eng._create_candidate_from_catalog.call_count, 0)


if __name__ == "__main__":
    unittest.main()
