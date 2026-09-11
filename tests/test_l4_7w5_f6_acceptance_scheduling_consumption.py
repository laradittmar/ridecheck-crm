"""L4.7W5-F6 — accepting a quote and asking when, in one breath.

Live deadlock. The customer answered the quote CTA with "si", then asked "para cuando tenes".
The reply was "¿Qué día y horario te viene mejor?" — their own question handed back. They
repeated it; the identical sentence returned. Stage never left QUOTED.

Three causes, each independently sufficient:

  1. `turn_modality` computes ONE temporality/modality over the whole burst and shares it
     across every claim. "para cuando tenes" made the turn FUTURE/CONDITIONAL, so the
     QUOTE_ACCEPTED claim was not `is_actionable_now` and acceptance HELD.
  2. the forward-search hook sat inside `if last_stage == SCHEDULING`, and the stage advances
     only when acceptance succeeds — so an intent spoken with the acceptance always arrived
     one turn early.
  3. "para cuando tenes" matched no next-available pattern.

ACC-01..11    acceptance scoping and conditional safety
SCHED-01..10  consumption of authorised scheduling intent
"""
from __future__ import annotations

import ast
import pathlib
import sys
import types
import unittest
from unittest.mock import MagicMock

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
for _m in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    sys.modules.setdefault(_m, types.ModuleType(_m))

import sqlalchemy as _sa
import sqlalchemy.dialects.postgresql as _pg
import sqlalchemy.dialects.postgresql.json as _pgj
_pg.JSONB = _sa.JSON
_pgj.JSONB = _sa.JSON

from app.schemas.claims import Modality, Temporality
from app.services.claim_projection import acceptance_modality, turn_modality
from app.services.conversation_engine import ConversationEngine

CE_SOURCE = (ROOT / "backend" / "app" / "services"
             / "conversation_engine.py").read_text(encoding="utf-8-sig")
CP_SOURCE = (ROOT / "backend" / "app" / "services"
             / "claim_projection.py").read_text(encoding="utf-8-sig")


def _fn(source: str, name: str) -> str:
    return next(ast.unparse(n) for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.FunctionDef) and n.name == name)


def _actionable(texts, has_scheduling=True) -> bool:
    t, m = acceptance_modality(texts, has_scheduling)
    return (t in (Temporality.PRESENT, Temporality.UNKNOWN)
            and m in (Modality.FACTUAL, Modality.UNKNOWN))


class TestAcceptanceScoping(unittest.TestCase):

    def test_acc_01_to_04_direct_affirmatives_are_actionable(self):
        """ACC-01..04 — a short affirmative answering the quote CTA accepts."""
        for text in ("si", "sí", "dale", "bueno si", "bueno sí", "avancemos",
                     "ok", "perfecto", "si dale avancemos"):
            with self.subTest(text=text):
                self.assertTrue(_actionable([text]), text)

    def test_acc_05_acceptance_and_scheduling_in_one_message(self):
        """ACC-05 — "si, para cuando tenes?" is one message with two clauses."""
        self.assertTrue(_actionable(["si, para cuando tenes?"]))

    def test_acc_06_acceptance_and_scheduling_as_separate_messages(self):
        """ACC-06 — the exact live burst."""
        self.assertTrue(_actionable(["si", "para cuando tenes"]))
        self.assertTrue(_actionable(["si", "para hoy que tenes?"]))

    def test_the_burst_reading_alone_would_still_hold_it(self):
        """The contamination is real, and scoping is what removes it."""
        t, m = turn_modality(["si", "para cuando tenes"])
        self.assertEqual(t, Temporality.FUTURE)
        self.assertEqual(m, Modality.CONDITIONAL)

    def test_acc_07_to_10_conditional_and_future_acceptance_still_holds(self):
        """ACC-07..10 — quote safety is untouched."""
        for text in ("si consigo la plata", "capaz que si", "si despues te aviso",
                     "si cuando junte la plata te aviso"):
            with self.subTest(text=text):
                self.assertFalse(_actionable([text]), text)

    def test_a_conditional_acceptance_beside_a_scheduling_question_still_holds(self):
        """The relaxation must not become a blanket pass."""
        self.assertFalse(_actionable(["si consigo la plata", "para cuando tenes"]))

    def test_acc_11_the_relaxation_requires_scheduling_evidence(self):
        """ACC-11 — without a scheduling request the coarse reading stands, so an
        unrelated present-tense sentence cannot launder a conditional acceptance."""
        self.assertFalse(_actionable(["si consigo la plata", "hola"], has_scheduling=False))
        self.assertEqual(acceptance_modality(["si consigo la plata", "hola"], False),
                         turn_modality(["si consigo la plata", "hola"]))

    def test_scoping_is_gated_and_documented(self):
        fn = _fn(CP_SOURCE, "acceptance_modality")
        self.assertIn("if not has_scheduling_evidence:", fn)
        self.assertIn("return turn_modality(texts)", fn)
        self.assertIn("re.split", fn)      # clause scope, not message scope

    def test_other_claims_keep_the_coarse_turn_reading(self):
        """Only the acceptance claim is scoped; nothing else changes."""
        self.assertEqual(CP_SOURCE.count("acceptance_modality("), 2)  # def + one call site


class TestSchedulingIntent(unittest.TestCase):

    def setUp(self):
        self.eng = ConversationEngine.__new__(ConversationEngine)

    def test_part_7_availability_questions_are_recognised_by_shape(self):
        """The live sentence, plus equivalents that were never observed."""
        for text in ("para cuando tenes", "cuando tienen lugar", "cuando hay turno",
                     "qué disponibilidad tienen", "cuándo pueden", "qué tenés disponible",
                     "para cuándo hay", "qué es lo primero que tenés",
                     "lo antes posible", "decime vos cuando pueden", "cuando tengan"):
            with self.subTest(text=text):
                self.assertTrue(self.eng._wants_next_available([text]), text)

    def test_it_does_not_fire_on_unrelated_or_explicit_turns(self):
        for text in ("el viernes", "mañana a las 11", "cuánto sale", "sí dale",
                     "hola buenas", "tengo que estar presente?",
                     "el auto está en paternal"):
            with self.subTest(text=text):
                self.assertFalse(self.eng._wants_next_available([text]), text)


class TestConsumptionWiring(unittest.TestCase):
    """SCHED-01..10 — asserted where the routing decision lives."""

    def test_sched_01_07_acceptance_consumes_the_intent_in_the_same_turn(self):
        """SCHED-01/07 — the stage advances on the line above; waiting for a later turn is
        what produced the loop."""
        fn = _fn(CE_SOURCE, "_handle_quoted_acceptance")
        self.assertIn("state.last_stage = STAGE_SCHEDULING", fn)
        self.assertIn("_handle_next_available_request", fn)
        i_stage = fn.index("state.last_stage = STAGE_SCHEDULING")
        i_consume = fn.index("_handle_next_available_request")
        self.assertLess(i_stage, i_consume, "the stage must advance before consumption")

    def test_sched_08_no_scheduling_intent_leaves_the_normal_reply(self):
        """SCHED-08 — _handle_next_available_request returns None when the turn carries no
        delegated/earliest intent, so the ordinary acceptance reply still goes out."""
        fn = _fn(CE_SOURCE, "_handle_quoted_acceptance")
        self.assertIn("if forward is not None", fn)
        self.assertIn("¿Qué día y horario te viene mejor", fn)
        handler = _fn(CE_SOURCE, "_handle_next_available_request")
        self.assertIn("if not self._wants_next_available(texts):", handler)
        self.assertIn("return None", handler)

    def test_sched_09_scheduling_authorisation_still_governs(self):
        """SCHED-09 — consumption routes through the same handler, which dispatches only
        via _dispatch_booking_flow_for_day and its progression authorizer."""
        handler = _fn(CE_SOURCE, "_handle_next_available_request")
        self.assertIn("self._schedule.find_next_available", handler)
        self.assertIn("_dispatch_booking_flow_for_day", handler)
        dispatch = _fn(CE_SOURCE, "_dispatch_booking_flow_for_day")
        self.assertIn("_authorize_scheduling_progression", dispatch)

    def test_sched_10_and_part_12_exactly_one_execution(self):
        """SCHED-10 / Part 12 — one acceptance, one scheduling call, one outbound."""
        fn = _fn(CE_SOURCE, "_handle_quoted_acceptance")
        self.assertEqual(fn.count("_handle_next_available_request"), 1)
        self.assertEqual(fn.replace("'", '"').count('lead.flag = "ACEPTADO"'), 1)
        # the early return prevents the generic reply from also being sent
        self.assertIn("return forward", fn)

    def test_a_consumption_failure_never_blocks_the_acceptance(self):
        fn = _fn(CE_SOURCE, "_handle_quoted_acceptance")
        self.assertIn("except OutboundBlockedError", fn)
        self.assertIn("except Exception", fn)

    def test_part_12_no_extra_model_call_is_introduced(self):
        """Consumption is deterministic: pattern match plus ScheduleService."""
        fn = _fn(CE_SOURCE, "_handle_next_available_request")
        for forbidden in ("_semantic_turn_evidence", "_interpret", "openai", "gpt"):
            self.assertNotIn(forbidden, fn)


class TestStageDependencyRemoved(unittest.TestCase):

    def test_part_5_the_turn_texts_are_available_to_later_handlers(self):
        self.assertIn("self._turn_burst_texts = list(ai_input_messages or [])", CE_SOURCE)
        self.assertIn("def _burst_texts_for_turn", CE_SOURCE)

    def test_the_scheduling_branch_still_exists_for_later_turns(self):
        """Removing the dependency must not remove the ordinary path."""
        self.assertIn("if not sched_day_iso and not sched_time_str:", CE_SOURCE)
        self.assertIn("forward = self._handle_next_available_request(ctx, state, ai_input_messages)",
                      CE_SOURCE)


class TestExactWildRegression(unittest.TestCase):
    """Part 9 — the recorded sequence, end to end through the real handler."""

    def setUp(self):
        self.eng = ConversationEngine.__new__(ConversationEngine)
        self.eng._answer_source = None
        self.eng._turn_burst_texts = ["si", "para cuando tenes"]
        self.eng._faq_reconciliation_burst = None
        self.eng.db = MagicMock()
        self.eng._send_text_to_wa = MagicMock(return_value="wamid.X")
        self.eng._handle_next_available_request = MagicMock(return_value="SCHEDULED")
        self.ctx = MagicMock()
        self.ctx.lead = MagicMock()
        self.state = MagicMock()

    def test_the_wild_burst_accepts_and_schedules_without_a_round_trip(self):
        out = self.eng._handle_quoted_acceptance(self.ctx, self.state)
        self.assertEqual(out, "SCHEDULED")
        self.assertEqual(self.ctx.lead.flag, "ACEPTADO")
        self.eng._handle_next_available_request.assert_called_once()
        texts = self.eng._handle_next_available_request.call_args[0][2]
        self.assertEqual(texts, ["si", "para cuando tenes"])
        self.eng._send_text_to_wa.assert_not_called()   # no generic question returned

    def test_the_same_day_variant_takes_the_same_path(self):
        self.eng._turn_burst_texts = ["si", "para hoy que tenes?"]
        out = self.eng._handle_quoted_acceptance(self.ctx, self.state)
        self.assertEqual(out, "SCHEDULED")
        self.eng._send_text_to_wa.assert_not_called()

    def test_acceptance_without_scheduling_still_asks_once(self):
        self.eng._turn_burst_texts = ["si"]
        self.eng._handle_next_available_request = MagicMock(return_value=None)
        self.eng._handle_quoted_acceptance(self.ctx, self.state)
        self.eng._send_text_to_wa.assert_called_once()
        self.assertIn("¿Qué día y horario", self.eng._send_text_to_wa.call_args[0][1])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
