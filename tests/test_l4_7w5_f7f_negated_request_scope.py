"""L4.7W5-F7F — a refusal to be phoned was being read as a request to be phoned.

F7E scoped scheduling rejection to scheduling and closed four false handoffs. One survived
with a different cause: `_is_phone_call_request` joined the whole burst and searched it, so
the pattern `\\bque\\s+me\\s+llam(?:en|e)\\b` matched inside

    "No me sirve QUE ME LLAME ahora."

and the customer's refusal became a call request, which escalated the thread to a human
nobody had asked for.

Polarity is now read per clause. A call expression governed by a negative particle is not a
request; a conditional burst proposes a call rather than requesting one; and a short
negative afterthought later in the same burst retracts an earlier affirmative. The call
patterns themselves are untouched — nothing was added for any sentence in this file.
"""
from __future__ import annotations

import ast
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import app.services.conversation_engine as ce                            # noqa: E402
from app.services.conversation_engine import _is_phone_call_request as wants_call  # noqa: E402

from test_l4_7w5_f7a_rescue_flow_guard import _LiveTurn                  # noqa: E402

CE_SRC = pathlib.Path(ce.__file__).read_text(encoding="utf-8")

# Measured as True on the detector BEFORE this change; they must stay True.
AFFIRMATIVE = ("Llamame.", "¿Me podés llamar?", "Quiero que me llamen.",
               "Que me llame Julián.", "llamame dale", "me podes llamar?")
# The refusals. Only the first two were True before; the rest were already False because the
# patterns never covered them. Asserted anyway so a future pattern widening cannot regress.
NEGATED = ("No me sirve que me llame ahora.", "No quiero que me llamen.",
           "No me llames ahora.", "Prefiero que no me llamen.",
           "Ahora no puedo hablar por teléfono.", "No hace falta que me llames.",
           "No quiero hablar por teléfono.", "Que no me llame nadie.",
           "no quiero q me llamen", "no me llamen por favor")
CONDITIONAL = ("Si hace falta llamame.", "Capaz hablamos por teléfono.",
               "Después vemos si hablamos.", "Tal vez pueda atender más tarde.")
# NOT recognised before this change and NOT made recognisable by it — the detector's
# recall gap is a separate concern. Recorded so the closeout cannot overclaim.
NEVER_MATCHED = ("Prefiero hablar por teléfono.", "¿Podemos hablar por teléfono?",
                 "¿Me llamás para coordinar?")


def _code_only(name: str) -> str:
    fn = next(n for n in ast.walk(ast.parse(CE_SRC))
              if isinstance(n, ast.FunctionDef) and n.name == name)
    body = list(fn.body)
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return "\n".join(ast.unparse(s) for s in body)


class TestPolarity(unittest.TestCase):

    def test_f7f_01_the_original_false_positive_is_closed(self):
        self.assertFalse(wants_call(["No me sirve que me llame ahora."]))

    def test_f7f_02_affirmative_requests_are_preserved(self):
        for text in AFFIRMATIVE:
            with self.subTest(text=text):
                self.assertTrue(wants_call([text]))

    def test_f7f_03_negated_and_refused_are_not_requests(self):
        for text in NEGATED:
            with self.subTest(text=text):
                self.assertFalse(wants_call([text]))

    def test_f7f_04_conditional_is_not_promoted_to_affirmative(self):
        for text in CONDITIONAL:
            with self.subTest(text=text):
                self.assertFalse(wants_call([text]))

    def test_f7f_05_forms_the_detector_never_matched_are_unchanged(self):
        """Honesty guard: F7F fixes polarity, not recall."""
        for text in NEVER_MATCHED:
            with self.subTest(text=text):
                self.assertFalse(wants_call([text]))

    def test_f7f_06_no_pattern_was_added_for_any_sentence_here(self):
        self.assertEqual(len(ce._PHONE_CALL_PATTERNS), 9)


class TestBurstAndCorrectionOrder(unittest.TestCase):

    def test_f7f_07_a_later_retraction_wins(self):
        self.assertFalse(wants_call(["Llamame.", "No, mejor no."]))

    def test_f7f_08_an_earlier_negation_does_not_kill_a_later_request(self):
        self.assertTrue(wants_call(["No ahora.", "Llamame mañana."]))

    def test_f7f_09_negation_scopes_its_own_clause_only(self):
        self.assertFalse(wants_call(["No me llames ahora, escribime por acá."]))
        self.assertTrue(wants_call(["No me sirve ese horario.", "Llamame."]))

    def test_f7f_10_order_uses_this_burst_not_prior_turns(self):
        body = _code_only("_is_phone_call_request")
        self.assertNotIn("db_messages", body)
        self.assertNotIn("ctx", body)
        self.assertIn("messages", body)

    def test_f7f_11_the_retraction_bound_errs_toward_no_request(self):
        """Documented tradeoff: a short negative afterthought suppresses, and that is safe."""
        self.assertFalse(wants_call(["Llamame mañana, no te olvides."]))


class _Router(_LiveTurn):

    def arm(self):
        self.eng._run_shadow_understand = lambda *a, **k: None
        self.eng._turn_semantic = None
        self.eng._semantic_handoff_requested = lambda state: False

    def effects(self):
        return (len(self.sent), len(self.emails), len(self.flows))


class TestLiveRouting(_Router):

    def test_f7f_12_the_refusal_causes_no_handoff_end_to_end(self):
        self.arm()
        token = self.state.flow_booking_token
        self.turn("No me sirve que me llame ahora.")
        self.db.expire_all()
        self.assertFalse(self.state.needs_human, "no human ownership")
        self.assertFalse(self.lead.necesita_humano)
        self.assertEqual(self.lead.estado, "CONSULTA_NUEVA")
        self.assertEqual(len(self.emails), 0, "no operator alert")
        self.assertEqual(len(self.flows), 0)
        self.assertEqual(self.state.flow_booking_token, token, "offer intact")

    def test_f7f_13_every_negated_form_is_inert_end_to_end(self):
        for text in NEGATED:
            with self.subTest(text=text):
                self.setUp(); self.arm()
                token = self.state.flow_booking_token
                self.turn(text)
                self.db.expire_all()
                self.assertFalse(self.state.needs_human)
                self.assertEqual(len(self.emails), 0)
                self.assertEqual(self.state.flow_booking_token, token)

    def test_f7f_14_f7d_r2_option_rejection_still_escalates(self):
        self.arm()
        self.turn("Ninguno de esos horarios me sirve.")
        self.db.expire_all()
        self.assertTrue(self.state.needs_human)
        self.assertEqual(self.state.last_stage, ce.STAGE_HUMAN)
        self.assertEqual(self.lead.estado, "ATENCION_HUMANA")
        self.assertEqual(self.effects(), (1, 1, 0))
        self.assertIsNone(self.state.flow_booking_token)

    def test_f7f_15_f7c_semantic_human_request_still_escalates(self):
        self.eng._run_shadow_understand = lambda *a, **k: None
        self.eng._turn_semantic = None
        self.eng._semantic_handoff_requested = lambda state: True
        self.turn("Buenas, ¿cómo seguimos con esto?")
        self.db.expire_all()
        self.assertTrue(self.state.needs_human)
        self.assertEqual(self.effects(), (1, 1, 0))

    def test_f7f_16_rejection_plus_call_request_yields_exactly_one_handoff(self):
        """Two valid rescue signals in one burst must not produce two handoffs.

        The phone-call path wins here, because the customer explicitly asked to be called;
        it acknowledges once and does not send the scheduling-coordination alert. The
        invariant under test is single ownership and a single customer-facing reply, not
        which of the two authorized paths claims it.
        """
        self.arm()
        self.turn("No me sirve ninguno de esos horarios.", "Llamame.")
        self.db.expire_all()
        self.assertTrue(self.state.needs_human, "one thread, one owner")
        sent, emails, flows = self.effects()
        self.assertEqual(sent, 1, "exactly one customer reply")
        self.assertLessEqual(emails, 1, "never two operator alerts")
        self.assertEqual(flows, 0, "no Flow re-offered")

    def test_f7f_17_no_booking_is_ever_written(self):
        from sqlalchemy import select
        from app.models import ThreadRevision
        for text in ("No me sirve que me llame ahora.", "Ninguno de esos horarios me sirve."):
            with self.subTest(text=text):
                self.setUp(); self.arm(); self.turn(text)
                self.db.expire_all()
                self.assertEqual(self.db.execute(select(ThreadRevision).where(
                    ThreadRevision.status == "booked")).scalars().all(), [])

    def test_f7f_18_an_affirmative_request_still_reaches_its_path(self):
        """Unchanged authorized behavior: the call request is still detected in SCHEDULING."""
        self.arm()
        self.assertTrue(wants_call(["Llamame."]))
        self.turn("Llamame.")
        self.db.expire_all()
        self.assertTrue(self.state.needs_human, "an explicit call request owns the thread")


class TestModelIndependent(_Router):

    def test_f7f_19_the_rule_makes_no_model_call(self):
        body = _code_only("_is_phone_call_request")
        for forbidden in ("SemanticTurnInterpreter", "_semantic_turn_evidence", "openai",
                          "TurnSemanticEvidence", "interpret("):
            self.assertNotIn(forbidden, body)

    def test_f7f_20_negated_stays_negated_with_the_model_absent_or_broken(self):
        for arm in ("none", "exception"):
            with self.subTest(arm=arm):
                self.setUp()
                self.eng._run_shadow_understand = lambda *a, **k: None
                self.eng._turn_semantic = None
                if arm == "exception":
                    def boom(state):
                        raise RuntimeError("model unavailable")
                    self.eng._semantic_handoff_requested = boom
                else:
                    self.eng._semantic_handoff_requested = lambda state: False
                self.assertFalse(wants_call(["No me sirve que me llame ahora."]))
                self.turn("No me sirve que me llame ahora.")
                self.db.expire_all()
                self.assertFalse(self.state.needs_human)


class TestTheSameDefectInTheHumanRequestDetector(unittest.TestCase):
    """`_is_human_request` carried the identical blindness and is corrected identically."""

    def test_f7f_28_a_refused_human_contact_is_not_a_request(self):
        for text in ("No quiero que me llamen.", "No quiero hablar por teléfono.",
                     "No quiero hablar con nadie."):
            with self.subTest(text=text):
                self.assertFalse(ce._is_human_request([text]))

    def test_f7f_29_genuine_human_requests_are_preserved(self):
        for text in ("¿Puedo hablar con una persona para coordinar otro horario?",
                     "Necesito que me atienda una persona.",
                     "Pasame con alguien por favor.",
                     "Quiero hablar con un asesor."):
            with self.subTest(text=text):
                self.assertTrue(ce._is_human_request([text]))

    def test_f7f_30_negation_scopes_its_clause_here_too(self):
        self.assertTrue(ce._is_human_request(
            ["No me sirve ese horario.", "Quiero hablar con una persona."]))


class TestGovernance(unittest.TestCase):

    def test_f7f_21_no_sentence_lexicon_and_no_ahora_exception(self):
        body = _code_only("_is_phone_call_request")
        words = [n.value for n in ast.walk(ast.parse(body))
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        self.assertEqual([w for w in words if len(w.split()) > 1], [])
        for noun in ("ahora", "precio", "sirve", "mejor"):
            self.assertNotIn(noun, body)

    def test_f7f_22_negators_are_a_closed_grammatical_class(self):
        for token in ce._NEGATORS:
            self.assertNotIn(" ", token, f"{token!r} is a phrase, not a particle")
        self.assertLessEqual(len(ce._NEGATORS), 8, "this class must not grow into a list")

    def test_f7f_23_the_detector_is_side_effect_free(self):
        for name in ("_is_phone_call_request", "_clause_is_negated", "_is_short_retraction"):
            body = _code_only(name)
            for forbidden in ("needs_human", "necesita_humano", "ATENCION_HUMANA",
                              "flow_booking_token", "_send_text_to_wa", "db.commit"):
                self.assertNotIn(forbidden, body, name)

    def test_f7f_24_no_semantic_schema_claim_or_prompt_change(self):
        interp = (ROOT / "backend/app/services/semantic_interpreter.py").read_text(encoding="utf-8")
        self.assertIn('PROMPT_VERSION = "understand/1.18"', interp)
        self.assertNotIn("offered_options", interp)
        self.assertNotIn("OFFERED_OPTIONS_REJECTED",
                         (ROOT / "backend/app/schemas/claims.py").read_text(encoding="utf-8"))

    def test_f7f_25_the_helpers_have_narrow_callers(self):
        tree = ast.parse(CE_SRC)
        for helper in ("_clause_is_negated", "_is_short_retraction"):
            callers = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                       and helper in ast.unparse(n) and n.name != helper}
            self.assertTrue(
                callers <= {"_is_phone_call_request", "_is_human_request",
                            "_is_short_retraction"},
                f"{helper} leaked to {callers}")

    def test_f7f_26_f7c_f7d_r2_and_f7e_are_intact(self):
        for marker in ("_semantic_handoff_requested", "_rejects_every_offered_option",
                       "_rejection_is_about_scheduling"):
            self.assertIn(marker, CE_SRC)
        self.assertNotIn("no me sirve", ce._ESCALATION_KEYWORDS)
        for name in ("test_l4_7w5_f7c_scheduling_handoff_evidence.py",
                     "test_l4_7w5_f7d_r2_option_rejection_floor.py",
                     "test_l4_7w5_f7e_scheduling_rejection_scope.py"):
            self.assertTrue((ROOT / "tests" / name).exists())

    def test_f7f_27_token_withdrawal_stays_in_the_canonical_handler(self):
        self.assertIn("flow_booking_token = None",
                      _code_only("_handle_scheduling_escalation"))
        callers = {n.name for n in ast.walk(ast.parse(CE_SRC)) if isinstance(n, ast.FunctionDef)
                   and "_handle_scheduling_escalation" in ast.unparse(n)
                   and n.name != "_handle_scheduling_escalation"}
        self.assertEqual(callers, {"_process_text", "_handle_next_available_request"})


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
