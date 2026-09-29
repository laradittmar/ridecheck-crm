"""L4.7W5-FAQ-HOURS-TRUTH — operating hours are a fact, and an LLM is not authoritative.

The live turn of 2026-09-24 produced prose saying "sábados de 9 a 13 hs" while the canonical
table says 9 to 15. The guard that exists to stop exactly this let it stand, because it asked
a STRUCTURAL question — does the reply name weekdays and the token "hs"? — and a sentence
with the wrong numbers answers that just as well as one with the right numbers. The canonical
answer was then withheld as redundant, and only the outbound kill switch stopped the
falsehood reaching a customer.

These tests are about OUTPUT TRUTH. Every case below is text this system generated; not one
is a customer phrasing, and nothing here may become production matching logic (test R).

A  reproduction: generated Saturday 9-13 against canonical 9-15
B  the final answer carries the canonical closing time
C  the false value survives nowhere in the final answer
D  an arbitrary different wrong time is corrected by the same rule, with no new case
E  a missing operating day is supplied from canonical truth
F  a fully correct answer is not duplicated
G  a wrong weekday (not Saturday) is corrected
H  a wrong open/closed status is corrected
I  the canonical answer itself is stable under the guard
J  operating hours and appointment availability stay separate concepts
K  no scheduling date/time is written to canonical state
L  no booking is created
M  no additional provider/model call is introduced
N  semantic interpretations and provider calls are counted separately
O  outbound-off behaviour is untouched by this change
P  trace on/off cannot change the business outcome
Q  the presence and payment truth guards still hold
R  no customer phrase, typo or spelling variant entered production matching logic
"""
from __future__ import annotations

import inspect
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import conversation_engine as ce          # noqa: E402
from app.services.conversation_engine import (              # noqa: E402
    ConversationEngine,
    _FAQ_TOPIC_ANSWERS,
    _hours_prose_contradicts,
    _hours_prose_satisfies,
    _hours_truth_violations,
    _norm_lower,
    _stated_hours_claims,
    _strip_contradicting_sentences,
)
from app.services.schedule import (                          # noqa: E402
    business_hours_for_weekday,
    business_hours_summary_es,
    canonical_weekday_hours,
)

#: The exact prose the live turn produced. SYSTEM OUTPUT, not customer text.
LIVE_GENERATED = (
    "Para la revisión del Peugeot 208 2022, ¿qué día y horario te viene mejor? "
    "Trabajamos lunes de 13 a 18 hs, martes de 9.30 a 14 hs, miércoles de 9 a 18 hs, "
    "jueves de 9 a 14 hs, viernes de 9 a 18 hs y sábados de 9 a 13 hs."
)

SATURDAY = 5
SUNDAY = 6


def compose(primary: str, topics=("business_hours",)) -> str:
    """Run the real reconciliation with a controlled topic set — no model, no database."""
    engine = ConversationEngine.__new__(ConversationEngine)
    engine._turn_ctx = None
    engine._contributing_sources = None
    engine._faq_topics_for_burst = lambda text, ctx=None, state=None: set(topics)
    return ConversationEngine._compose_secondary_answers(engine, primary, "irrelevant")


def canonical_pair(weekday: int) -> tuple[str, str]:
    start, end, _closed = business_hours_for_weekday(weekday)
    fmt = lambda t: str(t.hour) if t.minute == 0 else f"{t.hour}.{t.minute:02d}"
    return fmt(start), fmt(end)


class A_Reproduction(unittest.TestCase):
    """The defect, stated as the test that was missing."""

    def test_a01_canonical_saturday_is_not_what_the_prose_said(self):
        start, end, closed = business_hours_for_weekday(SATURDAY)
        self.assertFalse(closed)
        self.assertEqual((end.hour, end.minute), (15, 0),
                         "canonical Saturday closing time changed; re-read this suite")
        self.assertIn("9 a 13", LIVE_GENERATED, "the recorded prose must keep its false value")

    def test_a02_the_false_hours_are_detected_as_a_contradiction(self):
        self.assertTrue(_hours_prose_contradicts(_norm_lower(LIVE_GENERATED)))

    def test_a03_the_false_hours_are_not_accepted_as_satisfied(self):
        """The exact assertion that used to return True and shipped the falsehood."""
        self.assertFalse(ConversationEngine._satisfies_canonical(
            "business_hours", _norm_lower(LIVE_GENERATED)))

    def test_a04_the_violation_names_the_day_it_is_about(self):
        self.assertEqual(_hours_truth_violations(_norm_lower(LIVE_GENERATED)),
                         [f"{SATURDAY}:wrong_hours"])


class BC_FinalAnswer(unittest.TestCase):

    def setUp(self):
        self.final = compose(LIVE_GENERATED)

    def test_b01_canonical_closing_time_is_present(self):
        _start, end = canonical_pair(SATURDAY)
        self.assertIn(f"sábados de 9 a {end} hs", self.final)

    def test_b02_the_final_answer_states_the_canonical_hours_for_every_open_day(self):
        self.assertFalse(_hours_truth_violations(_norm_lower(self.final)))
        self.assertTrue(_hours_prose_satisfies(_norm_lower(self.final)))

    def test_c01_the_false_value_survives_nowhere(self):
        self.assertNotIn("9 a 13", self.final)

    def test_c02_the_rest_of_the_reply_is_preserved(self):
        """Only the false sentence is removed — the question the customer needs stays."""
        self.assertIn("¿qué día y horario te viene mejor?", self.final)


class D_ArbitraryWrongValue(unittest.TestCase):
    """No special case was added for 13:00. Any wrong value is wrong the same way."""

    def test_d01_a_different_wrong_saturday_closing_time_is_corrected(self):
        prose = ("Trabajamos lunes de 13 a 18 hs, martes de 9.30 a 14 hs, "
                 "miércoles de 9 a 18 hs, jueves de 9 a 14 hs, viernes de 9 a 18 hs "
                 "y sábados de 9 a 17 hs.")
        self.assertTrue(_hours_prose_contradicts(_norm_lower(prose)))
        final = compose(prose)
        self.assertNotIn("9 a 17", final)
        self.assertIn("9 a 15", final)

    def test_d02_a_wrong_opening_time_is_corrected_too(self):
        prose = "Trabajamos sábados de 11 a 15 hs."
        self.assertTrue(_hours_prose_contradicts(_norm_lower(prose)))
        self.assertNotIn("11 a 15", compose(prose))

    def test_d03_minutes_are_compared_not_ignored(self):
        """Tuesday opens at 9.30. '9 a 14' is a different claim from '9.30 a 14'."""
        self.assertTrue(_hours_prose_contradicts(_norm_lower("Trabajamos martes de 9 a 14 hs.")))
        self.assertFalse(_hours_prose_contradicts(
            _norm_lower("Trabajamos martes de 9.30 a 14 hs.")))

    def test_d04_the_rule_is_derived_from_the_table_not_from_a_list(self):
        """Every open day is checkable, and the expected value comes from the table."""
        for wd, label, start, end, closed in canonical_weekday_hours():
            if closed:
                continue
            with self.subTest(day=label):
                wrong_end = (end.hour + 1) % 24
                prose = f"Trabajamos {label} de {start.hour} a {wrong_end} hs."
                self.assertTrue(_hours_prose_contradicts(_norm_lower(prose)))


class E_MissingHours(unittest.TestCase):

    def test_e01_a_missing_saturday_is_not_satisfied(self):
        prose = ("Trabajamos lunes de 13 a 18 hs, martes de 9.30 a 14 hs, "
                 "miércoles de 9 a 18 hs, jueves de 9 a 14 hs y viernes de 9 a 18 hs.")
        self.assertFalse(_hours_truth_violations(_norm_lower(prose)), "nothing stated is false")
        self.assertFalse(_hours_prose_satisfies(_norm_lower(prose)), "but Saturday is missing")

    def test_e02_the_canonical_answer_supplies_it(self):
        prose = ("Trabajamos lunes de 13 a 18 hs, martes de 9.30 a 14 hs, "
                 "miércoles de 9 a 18 hs, jueves de 9 a 14 hs y viernes de 9 a 18 hs.")
        self.assertIn("sábados de 9 a 15 hs", compose(prose))

    def test_e03_prose_with_no_hours_at_all_receives_the_canonical_answer(self):
        final = compose("¿Qué día te viene mejor?")
        self.assertIn(business_hours_summary_es(), final)


class F_NoDuplication(unittest.TestCase):

    def test_f01_the_canonical_answer_is_recognised_as_satisfying(self):
        self.assertTrue(_hours_prose_satisfies(_norm_lower(business_hours_summary_es())))

    def test_f02_a_reply_that_already_states_canonical_hours_is_not_supplemented(self):
        primary = "Genial. " + business_hours_summary_es()
        self.assertEqual(compose(primary), primary)

    def test_f03_the_canonical_hours_appear_exactly_once(self):
        final = compose("Genial. " + business_hours_summary_es())
        self.assertEqual(final.count("sábados de 9 a 15 hs"), 1)


class G_WrongWeekday(unittest.TestCase):

    def test_g01_a_wrong_monday_is_corrected(self):
        prose = "Trabajamos lunes de 9 a 18 hs."      # canonical Monday opens at 13
        self.assertTrue(_hours_prose_contradicts(_norm_lower(prose)))
        final = compose(prose)
        self.assertNotIn("lunes de 9 a 18", final)
        self.assertIn("lunes de 13 a 18 hs", final)

    def test_g02_a_day_range_carries_its_hours_to_every_day_it_spans(self):
        """'lunes a viernes de 9 a 18' is false for Monday, Tuesday and Thursday."""
        claims, _closed, _open = _stated_hours_claims(
            _norm_lower("Trabajamos lunes a viernes de 9 a 18 hs."))
        self.assertEqual(sorted(claims), [0, 1, 2, 3, 4])
        self.assertTrue(_hours_prose_contradicts(_norm_lower("Trabajamos lunes a viernes de 9 a 18 hs.")))

    def test_g03_a_correct_day_range_is_accepted(self):
        """Wednesday and Friday genuinely share 9-18; a range naming only those is true."""
        self.assertFalse(_hours_truth_violations(_norm_lower("Trabajamos miércoles de 9 a 18 hs "
                                                             "y viernes de 9 a 18 hs.")))


class H_OpenClosedStatus(unittest.TestCase):

    def test_h01_claiming_we_open_on_sunday_is_a_contradiction(self):
        self.assertTrue(_hours_prose_contradicts(_norm_lower("Los domingos también trabajamos.")))

    def test_h02_claiming_sunday_hours_is_a_contradiction(self):
        violations = _hours_truth_violations(_norm_lower("Trabajamos domingos de 9 a 13 hs."))
        self.assertEqual(violations, [f"{SUNDAY}:hours_on_closed_day"])

    def test_h03_claiming_we_are_closed_on_an_open_day_is_a_contradiction(self):
        self.assertTrue(_hours_prose_contradicts(_norm_lower("Los sábados no trabajamos.")))

    def test_h04_the_canonical_sunday_statement_is_true(self):
        self.assertFalse(_hours_truth_violations(_norm_lower("Los domingos no trabajamos.")))

    def test_h05_a_false_sunday_claim_is_replaced_by_canonical_truth(self):
        final = compose("Trabajamos todos los días. Los domingos abrimos de 9 a 13 hs.")
        self.assertNotIn("domingos abrimos", final)
        self.assertIn("Los domingos no trabajamos.", final)


class I_Stability(unittest.TestCase):

    def test_i01_the_canonical_answer_passes_its_own_guard(self):
        canonical = business_hours_summary_es()
        self.assertFalse(_hours_prose_contradicts(_norm_lower(canonical)))
        self.assertTrue(_hours_prose_satisfies(_norm_lower(canonical)))

    def test_i02_the_canonical_answer_is_never_stripped(self):
        self.assertEqual(
            _strip_contradicting_sentences(business_hours_summary_es(), "business_hours"),
            business_hours_summary_es())

    def test_i03_the_answer_is_generated_from_the_table_not_hard_coded(self):
        for _wd, label, start, end, closed in canonical_weekday_hours():
            if closed:
                continue
            fmt = lambda t: str(t.hour) if t.minute == 0 else f"{t.hour}.{t.minute:02d}"
            with self.subTest(day=label):
                self.assertIn(f"de {fmt(start)} a {fmt(end)} hs", business_hours_summary_es())


class J_HoursVersusAvailability(unittest.TestCase):
    """Operating hours are a weekly fact. Appointment availability is a per-date lookup."""

    def test_j01_the_guard_says_nothing_about_slots(self):
        source = inspect.getsource(ce._hours_truth_violations) + inspect.getsource(
            ce._stated_hours_claims) + inspect.getsource(ce._hours_prose_satisfies)
        for forbidden in ("ScheduleService", "slot", "available", "disponib", "booking",
                          "turno", "revision"):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, source)

    def test_j02_an_hours_answer_never_names_a_date(self):
        self.assertNotRegex(business_hours_summary_es(), r"\d{4}-\d{2}-\d{2}")

    def test_j03_the_hours_guard_does_not_consult_offered_slots(self):
        """A reply offering real slots is not an hours claim and must not be touched."""
        prose = "Tengo estos horarios: 13:30, 14:00 y 14:30."
        self.assertFalse(_hours_truth_violations(_norm_lower(prose)))

    def test_j04_this_milestone_did_not_change_availability_classification(self):
        """DEF-01/02/03 stay open: a semantic availability enquiry is still unrepresentable."""
        from app.schemas.turn_evidence import SchedulingRequestEvidence
        # pydantic wraps a private attr on the class; read it off an instance.
        self.assertEqual(SchedulingRequestEvidence()._MEANINGFUL_FIELDS,
                         ("day_expression", "resolved_date", "time"))
        self.assertTrue(SchedulingRequestEvidence(flexible_time=True).is_semantically_empty(),
                        "a day-less availability enquiry is still the empty row (DEF-01)")


class KL_NoStateNoBooking(unittest.TestCase):

    NEW_FUNCTIONS = ("_es_day_tokens", "_hours_patterns", "_parse_stated_hour",
                     "_stated_hours_claims", "_hours_truth_violations",
                     "_hours_prose_contradicts", "_hours_prose_satisfies")

    def new_source(self) -> str:
        return "\n".join(inspect.getsource(getattr(ce, name)) for name in self.NEW_FUNCTIONS)

    def test_k01_no_canonical_scheduling_write(self):
        for forbidden in ("preferred_day", "preferred_time", "active_requested_date",
                          "last_offered_slots", "flow_booking_token"):
            with self.subTest(field=forbidden):
                self.assertNotIn(forbidden, self.new_source())

    def test_k02_no_database_access_of_any_kind(self):
        for forbidden in ("self.db", "session", "commit", "add(", "select(", "Session"):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, self.new_source())

    def test_l01_no_booking_is_created(self):
        for forbidden in ("Revision", "ThreadRevision", "booked", "_process_flow_response",
                          "BOOKING_FLOW"):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, self.new_source())

    def test_l02_the_guard_is_a_pure_function_of_its_text(self):
        text = _norm_lower(LIVE_GENERATED)
        first = _hours_truth_violations(text)
        second = _hours_truth_violations(text)
        self.assertEqual(first, second)
        self.assertEqual(first, _hours_truth_violations(text))


class MN_ModelCallAccounting(unittest.TestCase):
    """Semantic interpretations and provider calls are DIFFERENT counts.

    The G3-1 closeout said "one model call" when it meant "one semantic interpretation".
    The live turn made ONE semantic interpretation and TWO OpenAI calls; the second wrote
    the customer-facing prose. This milestone adds neither.
    """

    def source(self) -> str:
        return (ROOT / "backend/app/services/conversation_engine.py").read_text(encoding="utf-8")

    def test_m01_the_new_guard_makes_no_provider_call(self):
        new = "\n".join(inspect.getsource(getattr(ce, n)) for n in KL_NoStateNoBooking.NEW_FUNCTIONS)
        for forbidden in ("_call_openai", "openai", "interpret(", "SemanticTurnInterpreter",
                          "urlrequest", "requests."):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, new)

    def test_m02_the_number_of_provider_call_sites_is_unchanged(self):
        """The guarantee is "no provider call was added", and it got STRONGER.

        Two call sites existed when this was written: the AI path and the FAQ AI path.
        G3-5F §4.3 put both behind one boundary, `_reply_decision`, so there is now exactly
        one place that calls the provider and exactly two consumers that ask it for a
        decision. The count asserted here is the count that matters — how many places can
        issue a reply request — and it fell from two to one. A third would fail this.
        """
        src = self.source()
        self.assertEqual(len(re.findall(r"self\._call_openai\(", src)), 1)
        self.assertEqual(len(re.findall(r"self\._reply_decision\(", src)), 2)

    def test_n01_a_provider_call_is_marked_in_exactly_one_place(self):
        """`ai_invoked` means an OpenAI request was actually issued — nothing else."""
        marks = re.findall(r"self\._ai_invoked = True", self.source())
        self.assertEqual(len(marks), 1)
        body = inspect.getsource(ConversationEngine._call_openai)
        self.assertIn("self._ai_invoked = True", body)

    def test_n02_a_semantic_interpretation_is_counted_by_a_different_mechanism(self):
        """CE_SHADOW_UNDERSTAND counts interpretations. It is not the provider-call count."""
        self.assertIn("CE_SHADOW_UNDERSTAND",
                      (ROOT / "backend/app/services/shadow_recorder.py").read_text(encoding="utf-8"))
        self.assertNotIn("CE_SHADOW_UNDERSTAND", inspect.getsource(ConversationEngine._call_openai))

    def test_n03_the_two_counts_are_not_the_same_field(self):
        from app.schemas.conversation import ConversationHandleOut
        fields = ConversationHandleOut.model_fields
        self.assertIn("ai_invoked", fields)
        self.assertNotIn("semantic_interpretations", fields,
                         "if this is ever added, it must be a SEPARATE count, not a rename")


class OP_OutboundAndTraceParity(unittest.TestCase):

    def new_source(self) -> str:
        return "\n".join(inspect.getsource(getattr(ce, n))
                         for n in KL_NoStateNoBooking.NEW_FUNCTIONS)

    def test_o01_the_guard_never_sends_and_never_touches_the_gate(self):
        for forbidden in ("OutboundSafetyGate", "gate.attempt", "_send_text_to_wa",
                          "OUTBOUND_ENABLED", "path_id"):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, self.new_source())

    def test_o02_composition_is_independent_of_delivery(self):
        """The corrected text is produced before any gate decision, so a blocked send
        records the corrected text, not the false one."""
        self.assertNotIn("9 a 13", compose(LIVE_GENERATED))

    def test_p01_the_guard_does_not_read_the_trace_flag(self):
        for forbidden in ("hybrid_trace_enabled", "HYBRID_TRACE", "_trace_decision",
                          "get_settings"):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, self.new_source())

    def test_p02_the_business_outcome_is_identical_with_tracing_on_or_off(self):
        import os
        outcomes = []
        previous = os.environ.get("HYBRID_TRACE_ENABLED")
        try:
            for value in ("true", "false"):
                os.environ["HYBRID_TRACE_ENABLED"] = value
                outcomes.append(compose(LIVE_GENERATED))
        finally:
            if previous is None:
                os.environ.pop("HYBRID_TRACE_ENABLED", None)
            else:
                os.environ["HYBRID_TRACE_ENABLED"] = previous
        self.assertEqual(outcomes[0], outcomes[1])


class Q_SiblingTruthGuards(unittest.TestCase):
    """presence and payment were fixed earlier and must stay fixed."""

    def test_q01_presence_falsehood_is_still_caught(self):
        self.assertTrue(ConversationEngine._contradicts_canonical(
            "presence", _norm_lower("No estaremos presentes durante la inspección.")))

    def test_q02_correct_presence_prose_is_still_satisfied(self):
        self.assertTrue(ConversationEngine._satisfies_canonical(
            "presence", _norm_lower("No es necesario que estés en la revisión.")))

    def test_q03_payment_falsehood_is_still_caught(self):
        self.assertTrue(ConversationEngine._contradicts_canonical(
            "payment", _norm_lower("Aceptamos efectivo, transferencia y débito.")))

    def test_q04_correct_payment_prose_is_still_satisfied(self):
        self.assertTrue(ConversationEngine._satisfies_canonical(
            "payment", _norm_lower("Aceptamos efectivo y transferencia bancaria.")))

    def test_q05_the_other_topics_still_use_their_pattern_sets(self):
        from app.services.conversation_engine import _FAQ_TOPIC_SATISFIED
        for topic in ("report", "presence", "payment", "service_scope"):
            with self.subTest(topic=topic):
                self.assertIn(topic, _FAQ_TOPIC_SATISFIED)


class R_NoPhraseCatalogue(unittest.TestCase):
    """The fix must not be a list of sentences, spellings or typos."""

    #: Frozen at the start of this milestone. Growth here is the defect, not the fix.
    HOURS_DETECTION_AT_161AA8D = frozenset({
        "horarios", "horario", "en que horarios", "que horarios",
        "cuando laburan", "cuando trabajan", "a que hora", "horario de atencion",
    })

    def test_r01_the_customer_phrase_detector_was_not_touched(self):
        from app.services.conversation_engine import _HOURS_FAQ_DETECTION
        self.assertEqual(set(_HOURS_FAQ_DETECTION), set(self.HOURS_DETECTION_AT_161AA8D))

    def test_r02_the_live_customer_text_is_nowhere_in_production_code(self):
        for path in (BACKEND / "app").rglob("*.py"):
            with self.subTest(path=path.name):
                body = path.read_text(encoding="utf-8")
                self.assertNotIn("disponiblees", body)
                self.assertNotIn("Que horarios tienen", body)

    def test_r03_the_guard_contains_no_wrong_hour_values(self):
        """Correctness is decided by comparison with the table, never by enumeration."""
        new = "\n".join(inspect.getsource(getattr(ce, n))
                        for n in KL_NoStateNoBooking.NEW_FUNCTIONS)
        self.assertNotIn("13", new.replace("{1,2}", ""))
        self.assertNotIn("15", new.replace("{1,2}", ""))

    def test_r04_the_guard_reads_the_canonical_table(self):
        for name in ("_hours_truth_violations", "_hours_prose_satisfies"):
            with self.subTest(fn=name):
                self.assertIn("business_hours_for_weekday", inspect.getsource(getattr(ce, name)))

    def test_r05_no_new_customer_language_pattern_was_added(self):
        """Every regex the fix introduced is about NUMBERS beside weekday names, which are
        this system's own output vocabulary, not a customer's phrasing."""
        self.assertEqual(set(_es_day_tokens_labels()), {
            "lunes", "martes", "miercoles", "jueves", "viernes",
            "sabado", "sabados", "domingo", "domingos"})


def _es_day_tokens_labels():
    return set(ce._es_day_tokens())


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
