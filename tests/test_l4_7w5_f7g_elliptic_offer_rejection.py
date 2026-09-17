r"""L4.7W5-F7G — a rejection with no object, answering an offer, is about that offer.

The live Wild of 2026-09-17 put eight real Friday slots in front of the tester, who replied

    "Mmm, no me sirve"  /  "No tenés algo más temprano?"

and got back "Entiendo, déjame ver. ¿Te sirve alguno de esos?" — the system asking whether
any of the options just rejected would do. No handoff, no operator alert, no answer. The
owner's verdict was NOT CLEAN, and rightly.

`_earliest_option_rejected` had detected the rejection. F7E's scope gate discarded it,
because neither message names a slot and the parser finds no day or time in them. F7E had
deleted the bare "no me sirve" keyword to close five proven false handoffs (price, vehicle,
payment method, report, phone call) and replaced it with a positive scope test — and that
test had no reading for a rejection whose object is simply left unsaid.

F7G adds it, positionally and with no vocabulary: a rejection predicate that ENDS its
clause has omitted its object, and while an offer is outstanding the offer is the only thing
it can be about. A stated object still wins and is still checked first, so every F7E
protection stays closed — including "no me sirve la garantía", whose noun appears in no
lexicon at all. That case is the proof the discriminator is ellipsis, not a word list.

R2 BOUNDARY. F7G's first cut escalated on EITHER message of the burst, because
`_EARLIEST_REJECTED_PATTERNS` carries `\bm[aá]s\s+temprano\b` — an alternative request
living in a set named for rejections — and that match also ends its clause. So
"¿No tenés algo más temprano?" alone became a human handoff, which overshot and quietly
absorbed the separate F-03 defect. The elliptic reading now accepts only
`_UNSUITABILITY_PATTERNS`: a claim that our offer does not work, never a request for a
different one. The burst still escalates in both arrival orders, because OOR-P10 carries the
rejection; the request carries none.
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

import app.services.conversation_engine as ce                              # noqa: E402
from app.services.conversation_engine import (                             # noqa: E402
    _rejection_is_about_scheduling as about_scheduling)

from test_l4_7w5_f7a_rescue_flow_guard import _LiveTurn                    # noqa: E402

CE_SRC = pathlib.Path(ce.__file__).read_text(encoding="utf-8")
CORPUS = ROOT / "tests" / "semantic_corpus" / "offered_options_rejected.jsonl"
CASES = [json.loads(l) for l in CORPUS.read_text(encoding="utf-8").splitlines() if l.strip()]

# The exact burst from the failed Wild, in the order the owner sent it. Message 1 carries the
# rejection; message 2 is an alternative request and carries none (R2 boundary).
WILD_REJECTION = "Mmm, no me sirve"
WILD_ALTERNATIVE_REQUEST = "No tenés algo más temprano?"
WILD_BURST = (WILD_REJECTION, WILD_ALTERNATIVE_REQUEST)
# Every rejection F7E closed. None may escalate, offer outstanding or not.
F7E_PROTECTED = ("No me sirve el precio.", "No me sirve ese auto.",
                 "No me sirve esa forma de pago.", "No me sirve el informe.",
                 "No me sirve que me llame ahora.",
                 # objects in no lexicon — ellipsis is the discriminator, not vocabulary
                 "No me sirve la garantía.", "No me sirve el color.",
                 "No me sirve el kilometraje.")


def _code_only(name: str) -> str:
    fn = next(n for n in ast.walk(ast.parse(CE_SRC))
              if isinstance(n, ast.FunctionDef) and n.name == name)
    body = list(fn.body)
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return "\n".join(ast.unparse(s) for s in body)


def rejected_and_scoped(texts, offer=True):
    ts = list(texts)
    return (ce.ConversationEngine._earliest_option_rejected(ts)
            and about_scheduling(ts, offer))


class TestTheEllipticRule(unittest.TestCase):

    def test_f7g_01_the_wild_burst_is_in_scope_with_an_offer_outstanding(self):
        self.assertTrue(rejected_and_scoped(WILD_BURST, True))

    def test_f7g_02_and_out_of_scope_with_nothing_outstanding(self):
        self.assertFalse(rejected_and_scoped(WILD_BURST, False),
                         "rejecting options nobody offered is not a rejection")

    def test_f7g_03_only_the_rejection_stands_alone(self):
        self.assertTrue(rejected_and_scoped([WILD_REJECTION], True))
        self.assertFalse(rejected_and_scoped([WILD_REJECTION], False))

    def test_f7g_03b_the_alternative_request_alone_is_not_a_rejection(self):
        """R2 boundary. Its correct answer is an earlier slot — the open F-03 defect."""
        self.assertFalse(rejected_and_scoped([WILD_ALTERNATIVE_REQUEST], True))
        self.assertFalse(rejected_and_scoped([WILD_ALTERNATIVE_REQUEST], False))

    def test_f7g_03c_the_root_cause_is_a_request_pattern_in_a_rejection_set(self):
        import re
        normalized = ce._norm_lower(WILD_ALTERNATIVE_REQUEST)
        hits = [p for p in ce._EARLIEST_REJECTED_PATTERNS if re.search(p, normalized)]
        self.assertEqual(hits, [r"\bm[aá]s\s+temprano\b"],
                         "this is what misclassified the request as a rejection")
        self.assertIn(r"\bm[aá]s\s+temprano\b", ce._ALTERNATIVE_REQUEST_PATTERNS)
        self.assertNotIn(r"\bm[aá]s\s+temprano\b", ce._UNSUITABILITY_PATTERNS)
        self.assertEqual(len(ce._EARLIEST_REJECTED_PATTERNS),
                         len(ce._UNSUITABILITY_PATTERNS) + len(ce._ALTERNATIVE_REQUEST_PATTERNS),
                         "the union the pre-existing detector uses is unchanged")

    def test_f7g_03d_the_ellipsis_test_reads_only_unsuitability(self):
        self.assertTrue(ce._rejection_has_no_complement(["no me sirve"]))
        self.assertFalse(ce._rejection_has_no_complement(["no tenes algo mas temprano"]))

    def test_f7g_04_every_f7e_protection_stays_closed(self):
        for text in F7E_PROTECTED:
            with self.subTest(text=text):
                self.assertFalse(rejected_and_scoped([text], True),
                                 "a stated object wins, offer outstanding or not")
                self.assertFalse(rejected_and_scoped([text], False))

    def test_f7g_05_a_stated_object_absent_from_every_lexicon_still_loses(self):
        """The discriminator is ellipsis, not vocabulary."""
        from app.services.scheduling_lexicon import (COMPETING_OBJECTS, SCHEDULING_OBJECTS)
        for noun in ("garantia", "color", "kilometraje"):
            self.assertNotIn(noun, COMPETING_OBJECTS | SCHEDULING_OBJECTS)
        self.assertFalse(rejected_and_scoped(["No me sirve la garantía."], True))

    def test_f7g_06_named_scheduling_objects_still_win(self):
        for text in ("Esos horarios no me sirven.", "No me sirve ninguno de esos horarios.",
                     "no me sirve ese turno"):
            with self.subTest(text=text):
                self.assertTrue(rejected_and_scoped([text], True))

    def test_f7g_07_a_parsed_day_or_time_still_wins(self):
        self.assertTrue(rejected_and_scoped(["no me sirve mañana me lo venden"], True))

    def test_f7g_08_accents_case_and_noise(self):
        for texts in (["no me sirve"], ["NO ME SIRVE"], ["mmm, no me sirve"],
                      ["no me sirve, no tenes algo mas temprano"],
                      ["uf no me sirve"]):
            with self.subTest(texts=texts):
                self.assertTrue(rejected_and_scoped(texts, True))

    def test_f7g_09_both_arrival_orders(self):
        self.assertTrue(rejected_and_scoped(WILD_BURST, True))
        self.assertTrue(rejected_and_scoped(tuple(reversed(WILD_BURST)), True))

    def test_f7g_10_the_lexicon_tokenizes_on_word_boundaries(self):
        """Regression: whitespace splitting left punctuation attached to the noun."""
        from app.services.scheduling_lexicon import (names_competing_object,
                                                     names_scheduling_object)
        self.assertTrue(names_scheduling_object("no me sirve ninguno de esos horarios."))
        self.assertTrue(names_scheduling_object("esos turnos no me sirven!"))
        self.assertTrue(names_competing_object("no me sirve el precio."))
        self.assertTrue(names_competing_object("no me sirve ese auto?"))

    def test_f7g_11_corpus_cases_classify_correctly(self):
        for case in CASES:
            if case["group"] == "elliptic_rejection":
                with self.subTest(id=case["id"]):
                    self.assertTrue(rejected_and_scoped([case["text"]], True))
                    self.assertFalse(rejected_and_scoped([case["text"]], False))
            if case["group"] == "alternative_request":
                with self.subTest(id=case["id"]):
                    self.assertFalse(rejected_and_scoped([case["text"]], True),
                                     "an alternative request is never a rejection")


class _Offered(_LiveTurn):
    """The F7A harness, which already sets up a live offer and a live Flow token."""

    def arm(self):
        self.eng._run_shadow_understand = lambda *a, **k: None
        self.eng._turn_semantic = None
        self.eng._semantic_handoff_requested = lambda state: False

    def effects(self):
        return (len(self.sent), len(self.emails), len(self.flows))

    def assert_rescued_once(self):
        self.db.expire_all()
        self.assertTrue(self.state.needs_human, "human ownership")
        self.assertEqual(self.state.last_stage, ce.STAGE_HUMAN)
        self.assertTrue(self.lead.necesita_humano)
        self.assertEqual(self.lead.estado, "ATENCION_HUMANA")
        self.assertEqual(self.effects(), (1, 1, 0),
                         "one acknowledgement, one operator alert, no Flow re-offered")
        self.assertIn("Julián", self.sent[0], "the standard handoff acknowledgement")
        self.assertIsNone(self.state.flow_booking_token, "the rejected offer is withdrawn")
        from sqlalchemy import select
        from app.models import ThreadRevision
        self.assertEqual(self.db.execute(select(ThreadRevision).where(
            ThreadRevision.status == "booked")).scalars().all(), [], "no booking")


class TestTheWildBurstThroughTheRealHandler(_Offered):

    def test_f7g_12_the_exact_wild_burst_escalates_once(self):
        self.arm()
        self.turn(*WILD_BURST)
        self.assert_rescued_once()

    def test_f7g_13_the_rejection_alone_escalates(self):
        self.arm()
        self.turn(WILD_REJECTION)
        self.assert_rescued_once()

    def test_f7g_13b_the_alternative_request_alone_does_not_escalate(self):
        """R2 boundary, end to end. F-03 owns the right answer; F7G must not invent one."""
        self.arm()
        token = self.state.flow_booking_token
        self.turn(WILD_ALTERNATIVE_REQUEST)
        self.db.expire_all()
        self.assertFalse(self.state.needs_human, "no human ownership from a request")
        self.assertEqual(self.lead.estado, "CONSULTA_NUEVA")
        self.assertEqual(len(self.emails), 0, "no operator alert")
        self.assertEqual(len(self.flows), 0)
        self.assertEqual(self.state.flow_booking_token, token, "the offer survives")

    def test_f7g_13c_the_request_then_the_rejection_escalates_once(self):
        self.arm()
        self.turn(WILD_ALTERNATIVE_REQUEST, WILD_REJECTION)
        self.assert_rescued_once()

    def test_f7g_14_reversed_arrival_order(self):
        self.arm()
        self.turn(*reversed(WILD_BURST))
        self.assert_rescued_once()

    def test_f7g_15_the_rejected_options_are_not_re_offered(self):
        self.arm()
        offered = json.loads(self.state.last_offered_slots)
        self.turn(*WILD_BURST)
        self.db.expire_all()
        reply = self.sent[0]
        for slot in offered:
            self.assertNotIn(slot, reply, f"{slot} was rejected and must not come back")
        self.assertNotIn("¿Te sirve alguno de esos?", reply,
                         "the Wild's actual wrong answer must never reappear")
        self.assertEqual(len(self.flows), 0, "no Flow re-sent")

    def test_f7g_16_a_late_flow_tap_cannot_book(self):
        self.arm()
        stale = self.state.flow_booking_token
        self.assertTrue(stale)
        self.turn(*WILD_BURST)
        self.db.expire_all()
        from app.services.booking_flow_service import BookingFlowService
        with self.assertRaises(Exception):
            BookingFlowService(self.db).resolve_context(stale)

    def test_f7g_17_duplicated_inbound_escalates_once(self):
        from app.schemas.conversation import ConversationHandleIn
        self.arm()
        self.turn(*WILD_BURST, wa_msg_id="wamid.DUP")
        self.assert_rescued_once()
        event = ConversationHandleIn(
            thread_id=self.thread.id, wa_message_id="wamid.DUP.1",
            wa_id=self.thread.contact.wa_id, text=WILD_BURST[-1],
            recent_user_messages=list(WILD_BURST),
            unanswered_recent_user_messages=list(WILD_BURST))
        out = self.eng.handle(event)
        self.db.expire_all()
        self.assertIn(out.action, ("skipped_dedup", "skipped_human"))
        self.assertEqual(self.effects(), (1, 1, 0), "no duplicate ack or alert")

    def test_f7g_18_no_automated_reply_after_the_handoff(self):
        self.arm()
        self.turn(*WILD_BURST, wa_msg_id="wamid.A")
        self.assert_rescued_once()
        self.arm()
        out = self.turn("¿Me avisan entonces?", wa_msg_id="wamid.B")
        self.db.expire_all()
        self.assertEqual(out.action, "skipped_human")
        self.assertEqual(self.effects(), (1, 1, 0))

    def test_f7g_19_with_no_outstanding_offer_nothing_escalates(self):
        self.state.active_requested_date = None
        self.state.last_offered_slots = None
        self.state.last_visible_slots = None
        self.state.flow_booking_token = None
        self.db.commit()
        self.arm()
        self.turn(*WILD_BURST)
        self.assert_not_rescued()

    def test_f7g_20_every_f7e_protection_is_inert_end_to_end(self):
        for text in F7E_PROTECTED:
            with self.subTest(text=text):
                self.setUp(); self.arm()
                token = self.state.flow_booking_token
                self.turn(text)
                self.db.expire_all()
                self.assertFalse(self.state.needs_human)
                self.assertEqual(len(self.emails), 0)
                self.assertEqual(self.state.flow_booking_token, token,
                                 "an unrelated complaint must not withdraw the offer")

    def test_f7g_21_model_absent_or_raising_changes_nothing(self):
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
                self.turn(*WILD_BURST)
                self.assert_rescued_once()

    def test_f7g_22_f7d_r2_and_f7f_still_behave(self):
        self.arm()
        self.turn("Ninguno de esos horarios me sirve.")
        self.assert_rescued_once()
        self.setUp(); self.arm()
        self.turn("No me sirve que me llame ahora.")
        self.assert_not_rescued()


class TestGovernance(unittest.TestCase):

    def test_f7g_23_no_object_exception_list_was_added(self):
        body = _code_only("_rejection_is_about_scheduling")
        for noun in ("precio", "pago", "informe", "auto", "garantia", "color",
                     "kilometraje", "temprano", "sirve"):
            self.assertNotIn(noun, body)
        from app.services import scheduling_lexicon as lex
        self.assertEqual(len(lex.COMPETING_OBJECTS), 22, "the list must not grow")
        self.assertEqual(len(lex.SCHEDULING_OBJECTS), 12)

    def test_f7g_24_no_sentence_constants_and_no_model_call(self):
        for name in ("_rejection_is_about_scheduling", "_rejection_has_no_complement"):
            body = _code_only(name)
            words = [n.value for n in ast.walk(ast.parse(body))
                     if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            self.assertEqual([w for w in words if len(w.split()) > 1], [], name)
            for forbidden in ("SemanticTurnInterpreter", "_semantic_turn_evidence",
                              "openai", "interpret("):
                self.assertNotIn(forbidden, body, name)

    def test_f7g_25_the_readers_mutate_nothing(self):
        for name in ("_rejection_is_about_scheduling", "_rejection_has_no_complement"):
            body = _code_only(name)
            for forbidden in ("needs_human", "necesita_humano", "ATENCION_HUMANA",
                              "flow_booking_token", "_send_text_to_wa", "db.commit"):
                self.assertNotIn(forbidden, body, name)

    def test_f7g_26_escalation_remains_the_sole_side_effect_authority(self):
        self.assertIn("flow_booking_token = None",
                      _code_only("_handle_scheduling_escalation"))
        callers = {n.name for n in ast.walk(ast.parse(CE_SRC)) if isinstance(n, ast.FunctionDef)
                   and "_handle_scheduling_escalation" in ast.unparse(n)
                   and n.name != "_handle_scheduling_escalation"}
        self.assertEqual(callers, {"_process_text", "_handle_next_available_request"})

    def test_f7g_27_the_prior_chain_is_intact(self):
        for marker in ("_semantic_handoff_requested", "_rejects_every_offered_option",
                       "_rejection_is_about_scheduling", "_opens_a_condition", "_NEGATORS"):
            self.assertIn(marker, CE_SRC)
        self.assertNotIn("no me sirve", ce._ESCALATION_KEYWORDS)
        interp = (ROOT / "backend/app/services/semantic_interpreter.py").read_text(encoding="utf-8")
        self.assertIn('PROMPT_VERSION = "understand/1.18"', interp)

    def test_f7g_28_the_wild_burst_is_permanent_and_correctly_labelled(self):
        by_text = {c["text"]: c for c in CASES}
        for text in WILD_BURST:
            self.assertIn(text, by_text, "the failed Wild burst must stay in the corpus")
        rejection = by_text[WILD_REJECTION]
        request = by_text[WILD_ALTERNATIVE_REQUEST]
        self.assertTrue(rejection["label"], "the rejection is the positive half")
        self.assertEqual(rejection["group"], "elliptic_rejection")
        self.assertFalse(request["label"], "the alternative request is NOT a positive")
        self.assertEqual(request["group"], "alternative_request")
        self.assertEqual(request["belongs_to_burst"], rejection["id"],
                         "it is evidence of the ordered burst, nothing more")
        self.assertEqual((rejection["burst_position"], request["burst_position"]), (1, 2))

    def test_f7g_29_f03_was_not_absorbed(self):
        """The alternative request reaches no rescue path, in any detector."""
        t = [WILD_ALTERNATIVE_REQUEST]
        self.assertFalse(about_scheduling(t, True))
        self.assertFalse(ce._rejects_every_offered_option(t, True))
        self.assertFalse(ce._is_human_request(t))
        self.assertFalse(ce._is_phone_call_request(t))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
