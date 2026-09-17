"""L4.7W5-F7D-R2 — rejecting every offered slot, read as grammar rather than as sentences.

F7D tried to teach the interpreter this concept and was refused certification: the prompt
had been revised three times against the same 19 cases it was then scored on, and a price
objection still came back as a scheduling rejection. The owner's decision was to ship the
deterministic floor for this Wild and defer semantic modelling to the real-client corpus.

The invariant is one sentence: *a universal negative quantifier scoped to a scheduling
object is a rejection of every option we offered.* No verb appears in the implementation —
"no me sirve", "no puedo", "no me queda bien", "no me cierra" are the same act and the code
never enumerates them. The discriminator is the NOUN, which is why `ninguno de esos autos`
and `ninguno de esos horarios` separate here and nowhere else.

Two readings; the elliptic one borrows its object from the conversation and is admitted only
while an offer is genuinely outstanding:

    explicit   "ninguno de esos horarios me sirve"   quantifier + scheduling object
    elliptic   "ninguna me cierra"                   quantifier, object omitted

`_EARLIEST_REJECTED_PATTERNS` and `_ESCALATION_KEYWORDS` are untouched and still run first.
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

import app.services.conversation_engine as ce                          # noqa: E402
from app.services.conversation_engine import _rejects_every_offered_option as rejects  # noqa: E402

from test_l4_7w5_f7a_rescue_flow_guard import _LiveTurn                # noqa: E402

CE_SRC = pathlib.Path(ce.__file__).read_text(encoding="utf-8")
CORPUS = ROOT / "tests" / "semantic_corpus" / "offered_options_rejected.jsonl"
CASES = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]
# OOR-P04 carries no universal quantifier; the pre-existing floor owns it. See the README.
GRAMMAR_EXEMPT = {"OOR-P04"}


def _code_only(name: str) -> str:
    fn = next(n for n in ast.walk(ast.parse(CE_SRC))
              if isinstance(n, ast.FunctionDef) and n.name == name)
    body = list(fn.body)
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return "\n".join(ast.unparse(s) for s in body)


class TestTheGrammarItself(unittest.TestCase):

    def test_r2_01_the_corpus_is_classified_correctly(self):
        for case in CASES:
            if case["id"] in GRAMMAR_EXEMPT:
                continue
            with self.subTest(id=case["id"], text=case["text"]):
                self.assertEqual(rejects([case["text"]], True), case["label"])

    def test_r2_02_the_noun_is_the_discriminator(self):
        """Same quantifier, same verb — only the object differs."""
        self.assertTrue(rejects(["Ninguno de esos horarios me sirve."], True))
        self.assertFalse(rejects(["Ninguno de esos autos me sirve."], True))

    def test_r2_03_accents_case_and_number_do_not_matter(self):
        for text in ("ninguno de esos horarios me sirve",
                     "NINGUNA DE ESAS OPCIONES ME SIRVE",
                     "ningun horario me viene bien",
                     "ningunos de esos turnos me sirven",
                     "no me sirve ninguna de esas fechas"):
            with self.subTest(text=text):
                self.assertTrue(rejects([text], True))

    def test_r2_04_the_elliptic_reading_needs_an_outstanding_offer(self):
        for text in ("ninguna me cierra", "uf ninguno de esos me viene bien"):
            with self.subTest(text=text):
                self.assertTrue(rejects([text], True), "answering an offer")
                self.assertFalse(rejects([text], False), "nothing offered, nothing rejected")

    def test_r2_05_hedged_and_conditional_bursts_are_not_rejections(self):
        self.assertFalse(rejects(["Capaz que ninguno, después te confirmo."], True))

    def test_r2_06_no_verb_vocabulary_was_invented(self):
        """A verb the code never saw still works, because the code reads nouns."""
        self.assertTrue(rejects(["Ninguno de esos turnos me contempla."], True))


class _Floor(_LiveTurn):
    """The F7A live-turn harness with the model out of the picture entirely."""

    def arm_no_semantics(self):
        self.eng._run_shadow_understand = lambda *a, **k: None
        self.eng._turn_semantic = None
        self.eng._semantic_handoff_requested = lambda state: False


class TestLivePathPositives(_Floor):

    def test_r2_07_every_corpus_positive_escalates_through_handle(self):
        for case in [c for c in CASES if c["label"]]:
            with self.subTest(id=case["id"], text=case["text"]):
                self.setUp(); self.arm_no_semantics()
                self.turn(case["text"])
                self.assert_rescued()

    def test_r2_08_the_owners_two_target_sentences(self):
        for text in ("Ninguno de esos horarios me sirve.",
                     "Ninguna de esas opciones me sirve."):
            with self.subTest(text=text):
                self.setUp(); self.arm_no_semantics()
                self.turn(text)
                self.assert_rescued()

    def test_r2_09_the_outstanding_token_is_withdrawn(self):
        self.arm_no_semantics()
        self.assertTrue(self.state.flow_booking_token)
        self.turn("Ninguno de esos horarios me sirve.")
        self.db.expire_all()
        self.assertIsNone(self.state.flow_booking_token)

    def test_r2_10_a_late_tap_on_the_withdrawn_offer_cannot_book(self):
        self.arm_no_semantics()
        stale = self.state.flow_booking_token
        self.turn("Ninguno de esos horarios me sirve.")
        self.db.expire_all()
        from app.services.booking_flow_service import BookingFlowService
        with self.assertRaises(Exception):
            BookingFlowService(self.db).resolve_context(stale)

    def test_r2_11_no_automated_reply_after_the_handoff(self):
        self.arm_no_semantics()
        self.turn("Ninguno de esos horarios me sirve.", wa_msg_id="wamid.A")
        self.assert_rescued()
        self.arm_no_semantics()
        out = self.turn("¿Hola? ¿Alguna novedad?", wa_msg_id="wamid.B")
        self.db.expire_all()
        self.assertEqual(out.action, "skipped_human")
        self.assertEqual((len(self.sent), len(self.emails), len(self.flows)), (1, 1, 0))


# Pre-existing, NOT introduced by F7D-R2: `_ESCALATION_KEYWORDS` contains the substring
# "no me sirve", so a price or call objection already escalates today. Verified against
# HEAD with this milestone's change stashed. The F7D-R2 grammar correctly returns False for
# both; the escalation comes from the older detector, which this milestone is not authorized
# to change. Recorded as a finding and excluded here so the test measures THIS rule.
PRE_EXISTING_FLOOR_ESCALATES = {"OOR-N06", "OOR-N08"}


class TestLivePathNegatives(_Floor):

    def test_r2_12_every_corpus_negative_leaves_the_thread_alone(self):
        for case in [c for c in CASES
                     if not c["label"] and c["id"] not in PRE_EXISTING_FLOOR_ESCALATES]:
            with self.subTest(id=case["id"], text=case["text"]):
                self.setUp(); self.arm_no_semantics()
                self.turn(case["text"])
                self.db.expire_all()
                self.assertFalse(self.state.needs_human)
                self.assertEqual(len(self.emails), 0)
                self.assertIsNotNone(self.state.flow_booking_token,
                                     "a non-rejection must not withdraw the offer")

    def test_r2_13_wrong_stage_does_not_escalate(self):
        self.state.last_stage = "QUOTED"; self.db.commit()
        self.arm_no_semantics()
        self.turn("Ninguno de esos horarios me sirve.")
        self.assert_not_rescued()

    def test_r2_14_no_outstanding_offer_does_not_escalate(self):
        self.state.active_requested_date = None
        self.state.last_offered_slots = None
        self.state.last_visible_slots = None
        self.state.flow_booking_token = None
        self.db.commit()
        self.arm_no_semantics()
        self.turn("ninguna me cierra")
        self.assert_not_rescued()

    def test_r2_15_an_option_taken_in_the_same_turn_refuses_escalation(self):
        self.arm_no_semantics()
        self.turn("El viernes no puedo; el sábado a las 14 sí.")
        self.assert_not_rescued()

    def test_r2_12b_the_grammar_never_claimed_these_and_f7e_closed_them(self):
        """Updated by L4.7W5-F7E, which removed the unscoped rejection predicate.

        When F7D-R2 shipped, both sentences escalated through the older detector and this
        test pinned that fact so it could be found. F7E scoped the predicate to scheduling,
        so the price complaint no longer escalates at all. The negated phone call still does,
        through `_is_phone_call_request` — a different detector with a different invariant,
        recorded as an adjacent defect rather than silently absorbed.
        """
        for text in ("No me sirve que me llame ahora.", "No me sirve el precio."):
            with self.subTest(text=text):
                self.assertFalse(rejects([text], True),
                                 "the F7D-R2 grammar never claimed these")
                self.assertFalse(ce._should_escalate_scheduling_to_human([text], self.state),
                                 "F7E removed the unscoped keyword")
        self.assertFalse(ce._is_phone_call_request(["No me sirve que me llame ahora."]),
                         "closed by L4.7W5-F7F: a negated call is no longer a request")

    def test_r2_16_an_already_human_owned_thread_is_untouched(self):
        self.state.needs_human = True; self.db.commit()
        self.arm_no_semantics()
        out = self.turn("Ninguno de esos horarios me sirve.")
        self.assertEqual(out.action, "skipped_human")
        self.assertEqual((len(self.sent), len(self.emails)), (0, 0))


class TestConvergence(_Floor):

    def effects(self):
        return (len(self.sent), len(self.emails), len(self.flows))

    def test_r2_17_grammar_only(self):
        self.arm_no_semantics()
        self.turn("Ninguno de esos horarios me sirve.")
        self.assert_rescued(); self.assertEqual(self.effects(), (1, 1, 0))

    def test_r2_18_pre_existing_detector_only(self):
        self.arm_no_semantics()
        self.turn("Esos horarios no me sirven.")
        self.assert_rescued(); self.assertEqual(self.effects(), (1, 1, 0))

    def test_r2_19_f7c_semantic_human_request_only(self):
        self.eng._run_shadow_understand = lambda *a, **k: None
        self.eng._turn_semantic = None
        self.eng._semantic_handoff_requested = lambda state: True
        self.turn("Buenas, ¿cómo seguimos con esto?")
        self.assert_rescued(); self.assertEqual(self.effects(), (1, 1, 0))

    def test_r2_20_grammar_and_f7c_in_the_same_turn_escalate_once(self):
        self.eng._run_shadow_understand = lambda *a, **k: None
        self.eng._turn_semantic = None
        self.eng._semantic_handoff_requested = lambda state: True
        self.turn("Ninguno de esos horarios me sirve, ¿me ayuda una persona?")
        self.assert_rescued(); self.assertEqual(self.effects(), (1, 1, 0))

    def test_r2_21_a_redelivered_inbound_escalates_once(self):
        """Meta redelivers the same WAMID; the row already exists, so only handle() repeats."""
        from app.schemas.conversation import ConversationHandleIn
        self.arm_no_semantics()
        text = "Ninguno de esos horarios me sirve."
        self.turn(text, wa_msg_id="wamid.DUP")
        self.assert_rescued()
        event = ConversationHandleIn(
            thread_id=self.thread.id, wa_message_id="wamid.DUP.0", wa_id=self.thread.contact.wa_id,
            text=text, recent_user_messages=[text], unanswered_recent_user_messages=[text])
        out = self.eng.handle(event)
        self.db.expire_all()
        self.assertIn(out.action, ("skipped_dedup", "skipped_human"))
        self.assertEqual(self.effects(), (1, 1, 0))

    def test_r2_22_a_deliberate_cycle_reset_still_reclaims(self):
        self.arm_no_semantics()
        self.turn("Ninguno de esos horarios me sirve.")
        self.assert_rescued()
        self.state.cycle_reset_pending = True; self.db.commit()
        self.arm_no_semantics()
        self.turn("Hola, quiero revisar otro auto.", wa_msg_id="wamid.C")
        self.db.expire_all()
        self.assertFalse(self.state.needs_human)


class TestTimeoutPosture(_Floor):
    """The floor never waits on, and never needs, a model."""

    def test_r2_23_the_grammar_makes_no_model_call(self):
        body = _code_only("_rejects_every_offered_option")
        for forbidden in ("SemanticTurnInterpreter", "interpret(", "_semantic_turn_evidence",
                          "TurnSemanticEvidence", "openai"):
            self.assertNotIn(forbidden, body)

    def test_r2_24_target_sentences_work_with_semantics_absent(self):
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
                self.turn("Ninguno de esos horarios me sirve.")
                self.assert_rescued()


class TestGovernance(unittest.TestCase):

    def test_r2_25_no_semantic_evidence_field_or_claim_was_introduced(self):
        for name in ("OfferedOptionsEvidence", "offered_options"):
            self.assertNotIn(name, (ROOT / "backend/app/schemas/turn_evidence.py")
                             .read_text(encoding="utf-8"))
        self.assertNotIn("OFFERED_OPTIONS_REJECTED",
                         (ROOT / "backend/app/schemas/claims.py").read_text(encoding="utf-8"))

    def test_r2_26_the_interpreter_prompt_is_unchanged(self):
        src = (ROOT / "backend/app/services/semantic_interpreter.py").read_text(encoding="utf-8")
        self.assertIn('PROMPT_VERSION = "understand/1.18"', src)
        self.assertNotIn("offered_options", src)

    def test_r2_27_vocabulary_lives_in_the_lexicon_not_the_engine(self):
        body = _code_only("_rejects_every_offered_option")
        self.assertIn("scheduling_lexicon", body)
        words = [n.value for n in ast.walk(ast.parse(body))
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        sentences = [w for w in words if len(w.split()) > 1]
        self.assertEqual(sentences, [], "no customer sentence may appear in the engine")

    def test_r2_28_the_lexicon_holds_nouns_not_sentences(self):
        from app.services import scheduling_lexicon as lex
        for word in lex.SCHEDULING_OBJECTS | lex.COMPETING_OBJECTS:
            self.assertNotIn(" ", word, f"{word!r} is a phrase, not a noun")

    def test_r2_29_the_detector_mutates_nothing(self):
        body = _code_only("_rejects_every_offered_option")
        for forbidden in ("needs_human", "necesita_humano", "ATENCION_HUMANA",
                          "flow_booking_token", "_send_text_to_wa", "db.commit", "self."):
            self.assertNotIn(forbidden, body)

    def test_r2_30_escalation_remains_the_sole_side_effect_authority(self):
        handler = _code_only("_handle_scheduling_escalation")
        self.assertIn("flow_booking_token = None", handler,
                      "token withdrawal stays inside the canonical handler")
        tree = ast.parse(CE_SRC)
        callers = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                   and "_handle_scheduling_escalation" in ast.unparse(n)
                   and n.name != "_handle_scheduling_escalation"}
        self.assertEqual(callers, {"_process_text", "_handle_next_available_request"},
                         "only the router and the pre-existing forward-search may escalate")

    def test_r2_31_f7c_is_preserved(self):
        self.assertIn("_semantic_handoff_requested", CE_SRC)
        self.assertIn("L4.7W5-F7C", CE_SRC)
        self.assertTrue((ROOT / "tests/test_l4_7w5_f7c_scheduling_handoff_evidence.py").exists())

    def test_r2_32_the_corpus_is_labelled_as_regression_not_evaluation(self):
        readme = (ROOT / "tests/semantic_corpus/README_offered_options_rejected.md")
        text = readme.read_text(encoding="utf-8")
        self.assertIn("NOT", text)
        self.assertIn("held-out", text)
        self.assertGreaterEqual(len(CASES), 19, "no original case may be deleted")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
