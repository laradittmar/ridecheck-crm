"""L4.7W5-GATE3-G3-4 — admission by either producer, and authority instead of a substring.

Two defects close here.

DEF-02: the only route into the scheduling path required the DETERMINISTIC parser to have
found a day or a time, and the semantic reading was consulted INSIDE that route — so CE
silence was an absolute veto on semantic scheduling evidence. Admission is now a
disjunction, and which half opened it is recorded.

DEF-03: `_HOURS_FAQ_DETECTION` won outright on a bare substring, so the word "horarios"
selected the opening-hours answer while the interpreter was saying, confirmed, that the
customer was asking about the AGENDA. The set is unchanged; it is partitioned, and a weak
match is now a candidate that can lose.

EXPECTATIONS ARE LITERAL. No production classifier computes an expected value anywhere in
this file — every classification, authority, permitted action and call count below is
written out. A defect shared by the code and its own oracle is exactly how the two FAQ-hours
reviews failed, and it must not be repeatable here.
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import conversation_engine as ce                   # noqa: E402
from app.services.conversation_engine import (                       # noqa: E402
    ConversationEngine, _hours_floor_strength, _HOURS_FAQ_DETECTION,
    STAGE_FLOW_SENT, STAGE_QUALIFYING, STAGE_QUOTED, STAGE_SCHEDULING,
)
from app.schemas import turn_evidence as te                          # noqa: E402
from app.schemas.hybrid_trace import DecisionSite                    # noqa: E402

LIVE = "Que horarios tienen disponiblees"
CORPUS = ROOT / "tests/semantic_corpus/scheduling_intent.jsonl"

#: Literal expectations. Spelled out, never derived from the module under test.
ADMIT_SEMANTIC = "SEMANTIC_INTENT"
ADMIT_DETERMINISTIC = "DETERMINISTIC_EVIDENCE"
ADMIT_BOTH = "BOTH_PRODUCERS"
ADMIT_NONE = "NOT_ADMITTED"
LOOK_UP = "LOOK_UP_AVAILABILITY"
REQUEST_PREREQ = "REQUEST_PREREQUISITE"
CLARIFY = "CLARIFY"
HOLD = "HOLD"
SINGLE = "SINGLE_PRODUCER"
AGREE = "AGREE"
AMBIGUOUS = "AMBIGUOUS_EVIDENCE"
NO_EVIDENCE = "NO_EVIDENCE"
PARALLEL = "PARALLEL_EVIDENCE"

#: A deterministic phrasing the EXISTING patterns already admitted before this milestone.
DETERMINISTIC_TEXT = "para cuando tenes turno"


def evidence_with(status, kind="AVAILABILITY_ENQUIRY"):
    if status is None:
        return None
    return te.TurnEvidence(scheduling_intents=(te.SchedulingIntentEvidence(
        kind=te.SchedulingIntentKind(kind), value=kind, status=status),))


class Harness:
    """A CE instance with only what these decisions read. Nothing real is mutated."""

    def __init__(self, *, status=None, stage=STAGE_SCHEDULING, faq_topics=(),
                 answered=(), traces=None):
        self.traces = traces if traces is not None else []
        self.schedule_calls = []
        engine = ConversationEngine.__new__(ConversationEngine)
        engine._availability_authority_cache = None
        engine._hybrid_trace_on = lambda: False
        engine._semantic_turn_evidence = lambda: evidence_with(status)
        engine._semantic_scheduling_claims = lambda st: []
        engine._semantic_faq_topics = lambda: set(faq_topics)
        engine._topics_already_answered_this_cycle = lambda c: set(answered)
        engine._turn_scheduling_requested = False
        engine._trace_decision = lambda **kw: self.traces.append(kw)
        engine._schedule = SimpleNamespace(
            find_next_available=lambda **kw: self.schedule_calls.append(kw))
        self.engine = engine
        self.state = SimpleNamespace(last_stage=stage, current_revision_id=None,
                                     needs_human=False, flow_booking_token=None)

    def authority(self, texts):
        return self.engine._availability_authority(None, self.state, texts)

    def faq(self, text):
        return ConversationEngine._faq_topics_for_burst(
            self.engine, text, ctx=None, state=self.state)


class S1_LiveInput(unittest.TestCase):
    """Scenario 1 and 11 — the exact live defect, from both sides."""

    def test_g34_01_the_live_input_is_admitted_by_the_semantic_producer_alone(self):
        decision = Harness(status=te.EvidenceStatus.CONFIRMED).authority([LIVE])
        self.assertEqual(decision.admission, ADMIT_SEMANTIC)
        self.assertFalse(decision.deterministic, "CE found no day and no time — correctly")
        self.assertTrue(decision.semantic)
        self.assertEqual(decision.classification, SINGLE, "one producer is never AGREE")
        self.assertEqual(decision.authority, LOOK_UP)
        self.assertTrue(decision.admitted)

    def test_g34_02_ce_silence_is_no_longer_a_veto(self):
        """Before G3-4 the deterministic patterns were the only way in."""
        self.assertFalse(ConversationEngine._wants_next_available([LIVE]),
                         "the live text matches no deterministic pattern, as before")
        self.assertTrue(Harness(status=te.EvidenceStatus.CONFIRMED).authority([LIVE]).admitted)

    def test_g34_03_the_bare_hours_match_is_suppressed_not_answered(self):
        harness = Harness(status=te.EvidenceStatus.CONFIRMED)
        self.assertEqual(_hours_floor_strength(LIVE), "WEAK")
        self.assertEqual(harness.faq(LIVE), set(), "no business-hours answer is authorised")

    def test_g34_04_no_conflict_and_no_clarification_are_manufactured(self):
        decision = Harness(status=te.EvidenceStatus.CONFIRMED).authority([LIVE])
        self.assertNotEqual(decision.classification, "CONFLICT")
        self.assertNotEqual(decision.authority, CLARIFY)

    def test_g34_05_a_weak_match_is_not_a_second_producer(self):
        """It is a different proposition, so it cannot make the row AGREE."""
        decision = Harness(status=te.EvidenceStatus.CONFIRMED).authority([LIVE])
        self.assertEqual(decision.classification, SINGLE)
        self.assertEqual(decision.floor, "WEAK")


class S2_StageAndPrerequisites(unittest.TestCase):
    """Scenarios 2, 9 — stage decides, and prerequisites are never skipped."""

    def test_g34_06_a_scheduling_capable_stage_authorises_a_lookup(self):
        for stage in (STAGE_SCHEDULING, STAGE_QUOTED, STAGE_FLOW_SENT):
            with self.subTest(stage=stage):
                decision = Harness(status=te.EvidenceStatus.CONFIRMED,
                                   stage=stage).authority([LIVE])
                self.assertEqual(decision.authority, LOOK_UP)

    def test_g34_07_an_earlier_stage_requests_the_prerequisite_instead(self):
        decision = Harness(status=te.EvidenceStatus.CONFIRMED,
                           stage=STAGE_QUALIFYING).authority([LIVE])
        self.assertEqual(decision.admission, ADMIT_SEMANTIC)
        self.assertEqual(decision.authority, REQUEST_PREREQ)
        self.assertNotEqual(decision.authority, LOOK_UP, "slots are not exposed early")

    def test_g34_08_no_slot_lookup_happens_before_prerequisites(self):
        harness = Harness(status=te.EvidenceStatus.CONFIRMED, stage=STAGE_QUALIFYING)
        result = ConversationEngine._handle_next_available_request(
            harness.engine, None, harness.state, [LIVE])
        self.assertIsNone(result, "the existing canonical path keeps the turn")
        self.assertEqual(harness.schedule_calls, [], "ScheduleService was not consulted")

    def test_g34_09_an_unadmitted_turn_consults_no_service(self):
        harness = Harness(status=None, stage=STAGE_SCHEDULING)
        result = ConversationEngine._handle_next_available_request(
            harness.engine, None, harness.state, ["gracias!"])
        self.assertIsNone(result)
        self.assertEqual(harness.schedule_calls, [])


class S3_Classifications(unittest.TestCase):
    """Scenarios 6, 10, 13, 14 — every label the Inspector may show, asserted literally."""

    def test_g34_10_single_producer_semantic_only(self):
        d = Harness(status=te.EvidenceStatus.CONFIRMED).authority([LIVE])
        self.assertEqual((d.admission, d.classification), (ADMIT_SEMANTIC, SINGLE))

    def test_g34_11_single_producer_deterministic_only(self):
        d = Harness(status=None).authority([DETERMINISTIC_TEXT])
        self.assertEqual((d.admission, d.classification), (ADMIT_DETERMINISTIC, SINGLE))
        self.assertEqual(d.authority, LOOK_UP, "existing deterministic behaviour preserved")

    def test_g34_12_agree_requires_two_producers_on_the_same_proposition(self):
        d = Harness(status=te.EvidenceStatus.CONFIRMED).authority([DETERMINISTIC_TEXT])
        self.assertEqual((d.admission, d.classification), (ADMIT_BOTH, AGREE))

    def test_g34_13_genuine_interpreter_ambiguity_clarifies(self):
        d = Harness(status=te.EvidenceStatus.AMBIGUOUS).authority(["¿Horarios?"])
        self.assertEqual(d.classification, AMBIGUOUS)
        self.assertEqual(d.authority, CLARIFY)
        self.assertFalse(d.admitted, "an ambiguous reading acts on nothing")

    def test_g34_14_an_ambiguous_turn_is_not_settled_silently_by_the_substring(self):
        harness = Harness(status=te.EvidenceStatus.AMBIGUOUS)
        self.assertEqual(harness.faq("¿Horarios?"), set(),
                         "neither reading may be selected without a decision")

    def test_g34_15_no_evidence_holds(self):
        d = Harness(status=None).authority(["gracias!"])
        self.assertEqual((d.admission, d.classification, d.authority),
                         (ADMIT_NONE, NO_EVIDENCE, HOLD))

    def test_g34_16_availability_never_reaches_a_booking_authority(self):
        from app.schemas.hybrid_trace import SchedulingAuthority
        for forbidden in ("ACCEPT", "BOOK", "CONFIRM", "BOOKING"):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, SchedulingAuthority.ALL)
        for texts, status, stage in (([LIVE], te.EvidenceStatus.CONFIRMED, STAGE_SCHEDULING),
                                     ([LIVE], te.EvidenceStatus.CONFIRMED, STAGE_QUALIFYING),
                                     ([DETERMINISTIC_TEXT], None, STAGE_SCHEDULING)):
            decision = Harness(status=status, stage=stage).authority(texts)
            with self.subTest(stage=stage):
                self.assertIn(decision.authority,
                              (LOOK_UP, REQUEST_PREREQ, CLARIFY, HOLD, "REQUEST_DATE",
                               "DEFER_TO_DETERMINISTIC"))


class S4_FaqAuthority(unittest.TestCase):
    """Scenarios 5, 10, 11, 12 — the floor is evidence, and parallel is not agreement."""

    def test_g34_17_a_strong_hours_question_is_still_answered(self):
        for text in ("cual es el horario de atencion?", "cuando trabajan?",
                     "cuando laburan los fines de semana?"):
            with self.subTest(text=text[:24]):
                self.assertEqual(_hours_floor_strength(text), "STRONG")
                self.assertEqual(Harness(status=None, stage=STAGE_QUALIFYING).faq(text),
                                 {"business_hours"})

    def test_g34_18_a_strong_match_survives_a_concurrent_availability_enquiry(self):
        """Different propositions stand in parallel; the hours answer is not suppressed."""
        harness = Harness(status=te.EvidenceStatus.CONFIRMED)
        self.assertEqual(harness.faq("cual es el horario de atencion?"), {"business_hours"})

    def test_g34_19_a_weak_match_survives_when_nothing_outranks_it(self):
        """Semantic silence leaves the deterministic floor as the only evidence there is."""
        self.assertEqual(Harness(status=None).faq(LIVE), {"business_hours"})

    def test_g34_20_semantic_failure_does_not_auto_answer_from_a_weak_match(self):
        """Scenario 12: the floor still answers, but only because it is ALL the evidence —
        and the decision is recorded, where before nothing was."""
        traces = []
        harness = Harness(status=None, traces=traces)
        harness.engine._semantic_turn_evidence = lambda: (_ for _ in ()).throw(RuntimeError())
        self.assertEqual(harness.faq(LIVE), {"business_hours"})

    def test_g34_21_the_detector_was_not_enlarged(self):
        self.assertEqual(set(_HOURS_FAQ_DETECTION), {
            "horarios", "horario", "en que horarios", "que horarios",
            "cuando laburan", "cuando trabajan", "a que hora", "horario de atencion"})

    def test_g34_22_the_partition_covers_the_whole_set_and_nothing_else(self):
        weak = {p for p in _HOURS_FAQ_DETECTION if _hours_floor_strength(p) == "WEAK"}
        strong = {p for p in _HOURS_FAQ_DETECTION if _hours_floor_strength(p) == "STRONG"}
        self.assertEqual(weak, {"horarios", "horario", "en que horarios", "que horarios",
                                "a que hora"})
        self.assertEqual(strong, {"cuando laburan", "cuando trabajan",
                                  "horario de atencion"})
        self.assertEqual(weak | strong, set(_HOURS_FAQ_DETECTION))
        self.assertEqual(weak & strong, set())

    def test_g34_23_no_match_is_no_topic(self):
        self.assertEqual(_hours_floor_strength("gracias, muy amables"), "NONE")
        self.assertEqual(Harness(status=None).faq("gracias, muy amables"), set())


class S5_TraceEvidence(unittest.TestCase):
    """The decisions must be visible, and truthfully labelled."""

    def traced(self, **kw):
        traces = []
        harness = Harness(traces=traces, **kw)
        harness.engine._hybrid_trace_on = lambda: True
        return harness, traces

    def test_g34_24_the_availability_decision_is_traced_at_its_own_site(self):
        harness, traces = self.traced(status=te.EvidenceStatus.CONFIRMED)
        harness.authority([LIVE])
        rows = [t for t in traces
                if t["decision_site_id"] == DecisionSite.SCHEDULING_AVAILABILITY_AUTHORIZATION]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["outcome"], ADMIT_SEMANTIC)
        self.assertEqual(row["authority_result"], LOOK_UP)
        self.assertEqual(row["permitted_action"], LOOK_UP)
        self.assertEqual(row["canonical_effect"], "NONE", "the decision writes nothing")
        self.assertIn("semantic=True", row["business_outcome"])
        self.assertIn("deterministic=False", row["business_outcome"],
                      "CE absence is stated, never hidden")

    def test_g34_25_the_faq_decision_is_traced_at_its_own_site(self):
        harness, traces = self.traced(status=te.EvidenceStatus.CONFIRMED)
        harness.faq(LIVE)
        rows = [t for t in traces
                if t["decision_site_id"] == DecisionSite.FAQ_TOPIC_AUTHORIZATION]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["authority_result"], "SUPPRESS")
        self.assertIn("suppressed=['business_hours']", row["business_outcome"])
        self.assertIn("hours_floor=WEAK", row["business_outcome"])
        self.assertIn("outranked", row["reason"])

    def test_g34_26_one_decision_per_turn(self):
        harness, traces = self.traced(status=te.EvidenceStatus.CONFIRMED)
        for _ in range(4):
            harness.authority([LIVE])
        rows = [t for t in traces
                if t["decision_site_id"] == DecisionSite.SCHEDULING_AVAILABILITY_AUTHORIZATION]
        self.assertEqual(len(rows), 1, "a duplicate delivery must not decide twice")

    def test_g34_27_the_burst_order_does_not_change_the_outcome(self):
        parts = ["hola", LIVE]
        a = Harness(status=te.EvidenceStatus.CONFIRMED).authority(parts)
        b = Harness(status=te.EvidenceStatus.CONFIRMED).authority(list(reversed(parts)))
        self.assertEqual((a.admission, a.classification, a.authority),
                         (b.admission, b.classification, b.authority))

    def test_g34_28_the_two_new_sites_are_registered(self):
        self.assertIn(DecisionSite.SCHEDULING_AVAILABILITY_AUTHORIZATION, DecisionSite.ALL)
        self.assertIn(DecisionSite.FAQ_TOPIC_AUTHORIZATION, DecisionSite.ALL)


class S6_NoAuthorityLeak(unittest.TestCase):
    """Scenarios 18, 19, 20 — the decision decides; it never acts."""

    def new_source(self) -> str:
        """CODE only. A docstring may name a service in order to say it is not called, and
        scanning prose would fail on the sentence that states the guarantee."""
        import ast, inspect, textwrap
        chunks = []
        for obj in (ConversationEngine._availability_authority,
                    ConversationEngine._semantic_availability_enquiry,
                    ConversationEngine._trace_faq_topic_authority,
                    ce._hours_floor_strength):
            tree = ast.parse(textwrap.dedent(inspect.getsource(obj)))
            for node in ast.walk(tree):
                if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                                      ast.Module)) and ast.get_docstring(node)):
                    node.body = node.body[1:]
            chunks.append(ast.unparse(tree))
        return "\n".join(chunks)

    def test_g34_29_the_decision_writes_no_scheduling_state(self):
        source = self.new_source()
        for forbidden in ("active_requested_date =", "preferred_day =", "preferred_time =",
                          "last_offered_slots =", "last_visible_slots =",
                          "flow_booking_token =", "last_stage ="):
            with self.subTest(term=forbidden):
                self.assertFalse(forbidden in source, f"{forbidden} reached the decision")

    def test_g34_30_the_decision_calls_no_service_and_sends_nothing(self):
        source = self.new_source()
        for forbidden in ("ScheduleService", "find_next_available", "_send_text_to_wa",
                          "OutboundSafetyGate", "_dispatch_booking_flow", "db.commit",
                          "_call_openai", "SemanticTurnInterpreter"):
            with self.subTest(term=forbidden):
                self.assertFalse(forbidden in source, f"{forbidden} reached the decision")

    def test_g34_31_no_provider_call_was_added(self):
        import re
        engine = (BACKEND / "app/services/conversation_engine.py").read_text(encoding="utf-8")
        interp = (BACKEND / "app/services/semantic_interpreter.py").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"self\._call_openai\(", engine)), 2)
        self.assertEqual(len(re.findall(r"self\._ai_invoked = True", engine)), 1)
        self.assertEqual(len(re.findall(r"self\._transport\(", interp)), 1)
        self.assertIn('or "gpt-4o-mini"', interp, "the model is unchanged")

    def test_g34_32_the_decision_is_a_pure_function_of_what_it_reads(self):
        first = Harness(status=te.EvidenceStatus.CONFIRMED).authority([LIVE])
        second = Harness(status=te.EvidenceStatus.CONFIRMED).authority([LIVE])
        self.assertEqual((first.admission, first.classification, first.authority,
                          first.floor, first.suppress_hours),
                         (second.admission, second.classification, second.authority,
                          second.floor, second.suppress_hours))


class S7_CorpusAlignment(unittest.TestCase):
    """The G3-3 corpus is the semantic evidence set; it is never a production rule."""

    def corpus(self):
        return [json.loads(l) for l in CORPUS.read_text(encoding="utf-8").splitlines()
                if l.strip()]

    def test_g34_33_every_availability_case_is_admitted_when_the_model_confirms_it(self):
        for case in self.corpus():
            if case["expected_intent"] != "AVAILABILITY_ENQUIRY":
                continue
            with self.subTest(case=case["id"]):
                decision = Harness(status=te.EvidenceStatus.CONFIRMED).authority([case["text"]])
                # SEMANTIC alone, or BOTH when the existing deterministic patterns also
                # happen to cover that phrasing — never NOT_ADMITTED, which is the defect.
                self.assertIn(decision.admission, (ADMIT_SEMANTIC, ADMIT_BOTH))
                self.assertEqual(decision.authority, LOOK_UP)
                self.assertEqual(decision.classification,
                                 AGREE if decision.admission == ADMIT_BOTH else SINGLE)

    def test_g34_34_a_business_hours_case_is_not_admitted_as_availability(self):
        case = [c for c in self.corpus() if c["expected_faq_topic"] == "business_hours"][0]
        decision = Harness(status=None).authority([case["text"]])
        self.assertEqual(decision.admission, ADMIT_NONE)
        self.assertEqual(decision.authority, HOLD)

    def test_g34_35_no_production_module_reads_the_corpus(self):
        for path in (BACKEND / "app").rglob("*.py"):
            with self.subTest(path=path.name):
                body = path.read_text(encoding="utf-8")
                self.assertNotIn("scheduling_intent.jsonl", body)
                self.assertNotIn("disponiblees", body)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
