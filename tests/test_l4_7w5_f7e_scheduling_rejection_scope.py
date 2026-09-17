"""L4.7W5-F7E — a rejection predicate with no object rejected the wrong things.

The F7D-R2 closeout said two contradictory things. Its matrix reported every corpus
negative as producing no handoff; its adversarial section reported that two of those
negatives escalate anyway. The executable finding was right and the matrix was too broad:
the corpus runner scored the *pure new grammar*, while the live router also runs two older
detectors. The negatives were level-1 negatives reported as level-3 negatives.

Driving `handle()` showed the blast radius was wider than the two known sentences. The
predicate `no me sirve` appears unscoped in BOTH `_EARLIEST_REJECTED_PATTERNS` and
`_ESCALATION_KEYWORDS`, and it escalated five different things a customer can dislike:

    No me sirve el precio.            → human handoff
    No me sirve que me llame ahora.   → human handoff
    No me sirve ese auto.             → human handoff
    No me sirve esa forma de pago.    → human handoff
    No me sirve el informe.           → human handoff

The correction is stated positively: a scheduling rejection must be ABOUT a slot. Either
the burst names a scheduling object, or the scheduling parser finds a day or a time in it.
Both mechanisms already existed. Nothing is enumerated, and no list of nouns a customer may
not dislike was created — which is the trap the milestone named, because such a list needs a
new entry every time someone rejects something new.
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import app.services.conversation_engine as ce                            # noqa: E402
from app.services.conversation_engine import (                           # noqa: E402
    _rejection_is_about_scheduling as about_scheduling,
    _rejects_every_offered_option as rejects)

from test_l4_7w5_f7a_rescue_flow_guard import _LiveTurn                  # noqa: E402

CE_SRC = pathlib.Path(ce.__file__).read_text(encoding="utf-8")
CORPUS = ROOT / "tests" / "semantic_corpus" / "offered_options_rejected.jsonl"
CASES = [json.loads(l) for l in CORPUS.read_text(encoding="utf-8").splitlines() if l.strip()]

# The four false handoffs F7E's scope correction removes.
WAS_FALSE_POSITIVE = (
    "No me sirve el precio.",
    "No me sirve ese auto.",
    "No me sirve esa forma de pago.",
    "No me sirve el informe.",
)
# ADJACENT DEFECT, different root cause, NOT fixed here and NOT hidden. "No me sirve que me
# llame ahora." still escalates — through `_is_phone_call_request`, which reads "que me
# llame" as a request for a call and ignores that it is negated. That is a different
# invariant ("a negated request is not a request") in an M21.1.1-certified detector, and
# this milestone is explicitly told not to broaden into a general rewrite. F7E proves its
# own scheduling detectors are silent on it; the follow-up owns the rest.
NEGATED_CALL_STILL_ESCALATES = "No me sirve que me llame ahora."
ADJACENT_DEFECT_IDS = {"OOR-N06"}
# Level-3 positives that the OLDER detectors own, not the F7D-R2 grammar.
LEGACY_OWNED = ("Esos horarios no me sirven.",
                "no me sirve mañana me lo venden",
                "mañana no puedo y lo necesito antes")


def case_id_ok(case_id: str) -> bool:
    return case_id not in ADJACENT_DEFECT_IDS


def _code_only(name: str) -> str:
    fn = next(n for n in ast.walk(ast.parse(CE_SRC))
              if isinstance(n, ast.FunctionDef) and n.name == name)
    body = list(fn.body)
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return "\n".join(ast.unparse(s) for s in body)


class _Router(_LiveTurn):
    """The F7A live harness with the model out of the picture entirely."""

    def arm(self):
        self.eng._run_shadow_understand = lambda *a, **k: None
        self.eng._turn_semantic = None
        self.eng._semantic_handoff_requested = lambda state: False

    def effects(self):
        return (len(self.sent), len(self.emails), len(self.flows))


class TestTheScopeRuleItself(unittest.TestCase):

    def test_f7e_01_the_five_proven_false_positives_are_not_scheduling(self):
        for text in WAS_FALSE_POSITIVE:
            with self.subTest(text=text):
                self.assertFalse(about_scheduling([text]))

    def test_f7e_02_a_scheduling_noun_is_in_scope(self):
        for text in ("Esos horarios no me sirven.", "no me sirve ninguna de esas fechas",
                     "no me sirve ese turno"):
            with self.subTest(text=text):
                self.assertTrue(about_scheduling([text]))

    def test_f7e_03_a_parsed_day_or_time_is_in_scope(self):
        for text in ("no me sirve mañana me lo venden", "el viernes no me sirve"):
            with self.subTest(text=text):
                self.assertTrue(about_scheduling([text]))

    def test_f7e_04_the_rule_is_positive_not_an_exception_list(self):
        """An object nobody anticipated is out of scope without being named anywhere."""
        for text in ("No me sirve la garantía.", "No me sirve el color.",
                     "No me sirve el kilometraje."):
            with self.subTest(text=text):
                self.assertFalse(about_scheduling([text]))

    def test_f7e_05_the_unscoped_keyword_is_gone(self):
        self.assertNotIn("no me sirve", ce._ESCALATION_KEYWORDS)

    def test_f7e_06_the_predicate_still_matches_singular_and_plural(self):
        self.assertTrue(ce.ConversationEngine._earliest_option_rejected(
            ["Esos horarios no me sirven."]))
        self.assertTrue(ce.ConversationEngine._earliest_option_rejected(
            ["no me sirve mañana"]))


class TestUnrelatedRejectionNoLongerEscalates(_Router):

    def test_f7e_07_each_proven_false_positive_end_to_end(self):
        for text in WAS_FALSE_POSITIVE:
            with self.subTest(text=text):
                self.setUp(); self.arm()
                token = self.state.flow_booking_token
                self.turn(text)
                self.db.expire_all()
                self.assertFalse(self.state.needs_human, "no human ownership")
                self.assertEqual(self.lead.estado, "CONSULTA_NUEVA", "lead state untouched")
                self.assertEqual(len(self.emails), 0, "no operator alert")
                self.assertEqual(len(self.flows), 0, "no Flow side effect")
                self.assertEqual(self.state.flow_booking_token, token,
                                 "the offer must survive an unrelated complaint")

    def test_f7e_08a_the_negated_call_is_not_a_scheduling_escalation(self):
        """F7E's detectors are silent on it; a separate detector still escalates it."""
        t = NEGATED_CALL_STILL_ESCALATES
        self.assertFalse(about_scheduling([t]), "not a scheduling rejection")
        self.assertFalse(rejects([t], True), "not a total option rejection")
        self.assertNotIn("no me sirve", ce._ESCALATION_KEYWORDS)
        self.assertTrue(ce._is_phone_call_request([t]),
                        "documents the adjacent defect: a negated call reads as a request")

    def test_f7e_08_every_corpus_negative_end_to_end(self):
        """Level 3, the complete router — the claim F7D-R2 made too broadly."""
        for case in [c for c in CASES
                     if not c["label"] and case_id_ok(c["id"])]:
            with self.subTest(id=case["id"], text=case["text"]):
                self.setUp(); self.arm()
                token = self.state.flow_booking_token
                self.turn(case["text"])
                self.db.expire_all()
                self.assertFalse(self.state.needs_human)
                self.assertEqual(len(self.emails), 0)
                self.assertEqual(self.state.flow_booking_token, token)


class TestLegitimateRescueSurvives(_Router):

    def assert_escalated(self):
        self.db.expire_all()
        self.assertTrue(self.state.needs_human)
        self.assertEqual(self.state.last_stage, ce.STAGE_HUMAN)
        self.assertTrue(self.lead.necesita_humano)
        self.assertEqual(self.lead.estado, "ATENCION_HUMANA")
        self.assertEqual(self.effects(), (1, 1, 0))
        self.assertIsNone(self.state.flow_booking_token, "offer withdrawn")

    def test_f7e_09_every_corpus_positive_still_escalates(self):
        for case in [c for c in CASES if c["label"]]:
            with self.subTest(id=case["id"], text=case["text"]):
                self.setUp(); self.arm()
                self.turn(case["text"])
                self.assert_escalated()

    def test_f7e_10_legacy_owned_positives_still_escalate(self):
        for text in LEGACY_OWNED:
            with self.subTest(text=text):
                self.setUp(); self.arm()
                self.turn(text)
                self.assert_escalated()

    def test_f7e_11_f7c_human_request_still_escalates(self):
        self.eng._run_shadow_understand = lambda *a, **k: None
        self.eng._turn_semantic = None
        self.eng._semantic_handoff_requested = lambda state: True
        self.turn("Buenas, ¿cómo seguimos con esto?")
        self.assert_escalated()

    def test_f7e_12_a_late_tap_on_the_withdrawn_offer_cannot_book(self):
        self.arm()
        stale = self.state.flow_booking_token
        self.turn("Ninguno de esos horarios me sirve.")
        self.db.expire_all()
        from app.services.booking_flow_service import BookingFlowService
        with self.assertRaises(Exception):
            BookingFlowService(self.db).resolve_context(stale)

    def test_f7e_13_no_automated_reply_after_the_handoff(self):
        self.arm()
        self.turn("Esos horarios no me sirven.", wa_msg_id="wamid.A")
        self.assert_escalated()
        self.arm()
        out = self.turn("¿Hola?", wa_msg_id="wamid.B")
        self.db.expire_all()
        self.assertEqual(out.action, "skipped_human")
        self.assertEqual(self.effects(), (1, 1, 0))

    def test_f7e_14_zero_bookings_on_every_positive(self):
        from sqlalchemy import select
        from app.models import ThreadRevision
        self.arm()
        self.turn("No puedo en ninguno de esos turnos.")
        self.db.expire_all()
        self.assertEqual(self.db.execute(select(ThreadRevision).where(
            ThreadRevision.status == "booked")).scalars().all(), [])


class TestContextAndNoise(_Router):

    def test_f7e_15_wrong_stage_does_not_escalate(self):
        self.state.last_stage = "QUOTED"; self.db.commit()
        self.arm(); self.turn("Esos horarios no me sirven.")
        self.assert_not_rescued()

    def test_f7e_16_no_outstanding_offer_does_not_escalate(self):
        self.state.active_requested_date = None
        self.state.last_offered_slots = None
        self.state.last_visible_slots = None
        self.state.flow_booking_token = None
        self.db.commit()
        self.arm(); self.turn("ninguna me cierra")
        self.assert_not_rescued()

    def test_f7e_17_accepted_option_in_the_same_turn(self):
        self.arm(); self.turn("El viernes no puedo; el sábado a las 14 sí.")
        self.assert_not_rescued()

    def test_f7e_18_already_human_owned(self):
        self.state.needs_human = True; self.db.commit()
        self.arm()
        out = self.turn("Esos horarios no me sirven.")
        self.assertEqual(out.action, "skipped_human")
        self.assertEqual(self.effects(), (0, 0, 0))

    def test_f7e_19_accents_and_noise(self):
        for text in ("esos horarios no me sirven", "NO ME SIRVEN ESOS HORARIOS",
                     "uf ninguno de esos me viene bien laburo todo el dia"):
            with self.subTest(text=text):
                self.setUp(); self.arm(); self.turn(text)
                self.db.expire_all()
                self.assertTrue(self.state.needs_human)

    def test_f7e_20_both_burst_orders_reach_one_escalation(self):
        for parts in (("Hola", "Ninguno de esos horarios me sirve."),
                      ("Ninguno de esos horarios me sirve.", "gracias")):
            with self.subTest(parts=parts):
                self.setUp(); self.arm(); self.turn(*parts)
                self.db.expire_all()
                self.assertTrue(self.state.needs_human)
                self.assertEqual(self.effects(), (1, 1, 0))

    def test_f7e_21_duplicated_delivery_escalates_once(self):
        from app.schemas.conversation import ConversationHandleIn
        self.arm()
        text = "Esos horarios no me sirven."
        self.turn(text, wa_msg_id="wamid.DUP")
        event = ConversationHandleIn(
            thread_id=self.thread.id, wa_message_id="wamid.DUP.0",
            wa_id=self.thread.contact.wa_id, text=text,
            recent_user_messages=[text], unanswered_recent_user_messages=[text])
        self.eng.handle(event)
        self.db.expire_all()
        self.assertEqual(self.effects(), (1, 1, 0))

    def test_f7e_22_model_raising_does_not_break_the_floor(self):
        def boom(state):
            raise RuntimeError("model unavailable")
        self.eng._run_shadow_understand = lambda *a, **k: None
        self.eng._turn_semantic = None
        self.eng._semantic_handoff_requested = boom
        self.turn("Esos horarios no me sirven.")
        self.db.expire_all()
        self.assertTrue(self.state.needs_human)


class TestGovernance(unittest.TestCase):

    def test_f7e_23_no_semantic_change(self):
        interp = (ROOT / "backend/app/services/semantic_interpreter.py").read_text(encoding="utf-8")
        self.assertIn('PROMPT_VERSION = "understand/1.18"', interp)
        self.assertNotIn("offered_options", interp)
        self.assertNotIn("OFFERED_OPTIONS_REJECTED",
                         (ROOT / "backend/app/schemas/claims.py").read_text(encoding="utf-8"))

    def test_f7e_24_the_scope_rule_makes_no_model_call_and_mutates_nothing(self):
        body = _code_only("_rejection_is_about_scheduling")
        for forbidden in ("SemanticTurnInterpreter", "_semantic_turn_evidence", "openai",
                          "needs_human", "necesita_humano", "flow_booking_token",
                          "_send_text_to_wa", "db.commit", "self."):
            self.assertNotIn(forbidden, body)

    def test_f7e_25_no_negative_object_exception_list_was_added(self):
        """The rule names what scheduling IS, never what it is not."""
        body = _code_only("_rejection_is_about_scheduling")
        for noun in ("precio", "pago", "informe", "auto", "llamada", "garantia", "color"):
            self.assertNotIn(noun, body)
        from app.services import scheduling_lexicon as lex
        self.assertEqual(len(lex.COMPETING_OBJECTS), 22,
                         "COMPETING_OBJECTS must not grow to chase new nouns")

    def test_f7e_26_no_sentence_constants_in_the_scope_rule(self):
        body = _code_only("_rejection_is_about_scheduling")
        words = [n.value for n in ast.walk(ast.parse(body))
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        self.assertEqual([w for w in words if len(w.split()) > 1], [])

    def test_f7e_27_f7c_and_f7d_r2_are_intact(self):
        self.assertIn("_semantic_handoff_requested", CE_SRC)
        self.assertIn("_rejects_every_offered_option", CE_SRC)
        for name in ("test_l4_7w5_f7c_scheduling_handoff_evidence.py",
                     "test_l4_7w5_f7d_r2_option_rejection_floor.py"):
            self.assertTrue((ROOT / "tests" / name).exists())
        self.assertTrue(rejects(["Ninguno de esos horarios me sirve."], True))

    def test_f7e_28_escalation_remains_the_sole_mutation_authority(self):
        handler = _code_only("_handle_scheduling_escalation")
        self.assertIn("flow_booking_token = None", handler)
        callers = {n.name for n in ast.walk(ast.parse(CE_SRC))
                   if isinstance(n, ast.FunctionDef)
                   and "_handle_scheduling_escalation" in ast.unparse(n)
                   and n.name != "_handle_scheduling_escalation"}
        self.assertEqual(callers, {"_process_text", "_handle_next_available_request"})

    def test_f7e_29_the_corpus_kept_every_case_and_its_governance_note(self):
        self.assertGreaterEqual(len(CASES), 21)
        readme = (ROOT / "tests/semantic_corpus/README_offered_options_rejected.md").read_text(encoding="utf-8")
        self.assertIn("held-out", readme)
        self.assertIn("complete live router", readme)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
