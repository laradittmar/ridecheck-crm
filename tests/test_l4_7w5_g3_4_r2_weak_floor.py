"""L4.7W5-GATE3-G3-4-R2 — a weak word is not an answer, and CLARIFY is a real reply.

The independent G3-4 audit returned FAIL on three findings:

  R34-01  a WEAK business-hours substring became authority whenever semantic interpretation
          was absent, failed or timed out — provider failure turned into business certainty,
          and an availability question was answered with opening hours;
  R34-02  `SchedulingAuthority.CLARIFY` was produced and never consumed, so a turn the trace
          called "clarify" reached the customer as unconstrained prose, or as nothing;
  R34-05  the Buenos Aires calendar boundary was not reproducible from the repository, and
          no test crossed the 21:00–00:00 window in which it fails.

Expected outcomes here are written out. `_hours_floor_strength` is NOT used to decide what a
case should produce — the audit found that using production as its own oracle is how the
defect survived the suite that was supposed to catch it.
"""
from __future__ import annotations

import datetime
import inspect
import pathlib
import sys
import unittest
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import schedule as sched                            # noqa: E402
from app.services.conversation_engine import (                        # noqa: E402
    ConversationEngine, _AVAILABILITY_VS_HOURS_CLARIFICATION,
    STAGE_QUALIFYING, STAGE_SCHEDULING,
)
from app.services.outbound_guard import OutboundBlockedError           # noqa: E402
from app.schemas import turn_evidence as te                            # noqa: E402

LIVE = "Que horarios tienen disponiblees"
#: Literal expectations — never computed by the code under test.
CLARIFY, HOLD, LOOK_UP = "CLARIFY", "HOLD", "LOOK_UP_AVAILABILITY"
EXPECTED_CLARIFICATION = ("¿Querés conocer los turnos disponibles para una revisión "
                          "o nuestros horarios de atención?")
BA = ZoneInfo("America/Argentina/Buenos_Aires")


class Turn:
    """A CE instance carrying only what these decisions read."""

    def __init__(self, *, availability=None, faq=(), mode=None,
                 stage=STAGE_SCHEDULING, outbound=True):
        self.sent: list[str] = []
        self.traces: list[dict] = []
        self.schedule_calls: list[dict] = []
        engine = ConversationEngine.__new__(ConversationEngine)
        engine._availability_authority_cache = None
        engine._hybrid_trace_on = lambda: True
        engine._trace_decision = lambda **kw: self.traces.append(kw)
        engine._semantic_scheduling_claims = lambda st: []
        engine._topics_already_answered_this_cycle = lambda c: set()
        engine._turn_scheduling_requested = False
        engine._decision_log = lambda *a, **k: None
        engine._schedule = SimpleNamespace(
            find_next_available=lambda **kw: self.schedule_calls.append(kw))

        def send(ctx, text):
            if not outbound:
                raise OutboundBlockedError(sender_path="ce_text", kind="text", to_wa_id="x")
            self.sent.append(text)
            return "wamid.SYNTHETICREPLY=="
        engine._send_text_to_wa = send
        engine.db = SimpleNamespace(commit=lambda: None)

        evidence = None
        if availability is not None or faq:
            evidence = te.TurnEvidence(
                scheduling_intents=((te.SchedulingIntentEvidence(
                    kind=te.SchedulingIntentKind.AVAILABILITY_ENQUIRY,
                    value="AVAILABILITY_ENQUIRY", status=availability),)
                    if availability else ()),
                faq_intents=tuple(te.FaqIntentEvidence(topic=t, value=t) for t in faq))

        def read():
            if mode == "raise":
                raise TimeoutError("provider timeout")
            if mode == "absent":
                return None
            if mode == "malformed":
                return SimpleNamespace(scheduling_intents=None, faq_intents="not a list")
            return evidence
        engine._semantic_turn_evidence = read

        self.engine = engine
        self.state = SimpleNamespace(
            last_stage=stage, current_revision_id=None, needs_human=False,
            flow_booking_token=None, active_requested_date=None, last_visible_slots=None,
            last_offered_slots=None, preferred_day=None, preferred_time=None)

    @property
    def ctx(self):
        return SimpleNamespace(thread=SimpleNamespace(id=1), state=self.state,
                               candidates=[], lead=None)

    def authority(self, text):
        return self.engine._availability_authority(None, self.state, [text])

    def topics(self, text):
        return ConversationEngine._faq_topics_for_burst(
            self.engine, text, ctx=None, state=self.state)

    def run(self, text, *, allow_clarification=True):
        """`allow_clarification` mirrors the ONE production site that may clarify — the main
        text path, reached only when no other proposition has claimed the turn."""
        return ConversationEngine._handle_next_available_request(
            self.engine, self.ctx, self.state, [text],
            allow_clarification=allow_clarification)


class A_WeakFloorNeverAnswersAlone(unittest.TestCase):
    """R34-01 inverted. Required tests 1–8."""

    def test_r2_01_availability_enquiry_wins_over_the_weak_candidate(self):
        turn = Turn(availability=te.EvidenceStatus.CONFIRMED)
        self.assertEqual(turn.authority(LIVE).authority, LOOK_UP)
        self.assertEqual(turn.topics(LIVE), set(), "no opening-hours answer")

    def test_r2_02_a_confirmed_hours_faq_still_answers(self):
        turn = Turn(faq=("business_hours",))
        self.assertEqual(turn.topics(LIVE), {"business_hours"})

    def test_r2_03_semantic_absent_clarifies_instead_of_answering(self):
        turn = Turn(mode="absent")
        self.assertEqual(turn.authority(LIVE).authority, CLARIFY)
        self.assertEqual(turn.topics(LIVE), set())

    def test_r2_04_semantic_timeout_clarifies(self):
        turn = Turn(mode="raise")
        decision = turn.authority(LIVE)
        self.assertEqual(decision.authority, CLARIFY)
        self.assertEqual(decision.semantic_status, "UNAVAILABLE")
        self.assertEqual(turn.topics(LIVE), set())

    def test_r2_05_a_raising_provider_is_not_business_certainty(self):
        turn = Turn(mode="raise")
        self.assertEqual(turn.authority("horarios").authority, CLARIFY)
        self.assertEqual(turn.topics("horarios"), set())

    def test_r2_06_malformed_evidence_clarifies(self):
        turn = Turn(mode="malformed")
        self.assertEqual(turn.authority("que horarios").authority, CLARIFY)
        self.assertEqual(turn.topics("que horarios"), set())

    def test_r2_07_an_unrelated_sentence_gets_no_hours_answer(self):
        turn = Turn(mode="absent")
        text = "me cambiaron el horario del trabajo, no se si voy a poder"
        self.assertEqual(turn.topics(text), set())

    def test_r2_08_an_explicit_strong_request_is_unchanged(self):
        for text in ("cual es el horario de atencion?", "cuando trabajan?",
                     "cuando laburan los sabados?"):
            with self.subTest(text=text[:24]):
                turn = Turn(mode="absent", stage=STAGE_QUALIFYING)
                self.assertEqual(turn.topics(text), {"business_hours"},
                                 "a genuinely explicit request keeps its answer")

    def test_r2_09_the_detector_was_not_enlarged(self):
        from app.services.conversation_engine import _HOURS_FAQ_DETECTION
        self.assertEqual(set(_HOURS_FAQ_DETECTION), {
            "horarios", "horario", "en que horarios", "que horarios",
            "cuando laburan", "cuando trabajan", "a que hora", "horario de atencion"})


class B_ClarifyIsConsumed(unittest.TestCase):
    """R34-02. Required tests 9–11."""

    def test_r2_10_clarify_produces_the_bounded_response(self):
        turn = Turn(mode="absent")
        out = turn.run(LIVE)
        self.assertEqual(turn.sent, [EXPECTED_CLARIFICATION])
        self.assertEqual(getattr(out, "action", None), "replied")

    def test_r2_11_the_constant_is_the_one_the_owner_specified(self):
        self.assertEqual(_AVAILABILITY_VS_HOURS_CLARIFICATION, EXPECTED_CLARIFICATION)

    def test_r2_12_there_is_exactly_one_clarification_constant(self):
        body = (BACKEND / "app/services/conversation_engine.py").read_text(encoding="utf-8")
        self.assertEqual(body.count("¿Querés conocer los turnos disponibles"), 1,
                         "one constant; a second variant starts a catalogue")

    def test_r2_13_it_states_no_hours_and_claims_no_availability(self):
        text = _AVAILABILITY_VS_HOURS_CLARIFICATION
        for forbidden in ("lunes", "martes", "sábados", "hs", "Trabajamos",
                          "13:", "14:", "disponible:"):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, text)
        self.assertTrue(text.endswith("?"), "it asks; it does not assert")

    def test_r2_14_clarification_writes_no_canonical_state(self):
        turn = Turn(mode="absent")
        before = dict(vars(turn.state))
        turn.run(LIVE)
        self.assertEqual({k for k in before if before[k] != vars(turn.state)[k]}, set())

    def test_r2_15_clarification_consults_no_scheduler_and_books_nothing(self):
        turn = Turn(mode="absent")
        turn.run(LIVE)
        self.assertEqual(turn.schedule_calls, [], "no slot was requested or reserved")
        self.assertIsNone(turn.state.flow_booking_token)
        self.assertIsNone(turn.state.active_requested_date)

    def test_r2_16_clarification_does_not_escalate_to_a_human(self):
        turn = Turn(mode="absent")
        turn.run(LIVE)
        self.assertFalse(turn.state.needs_human)

    def test_r2_17_outbound_off_keeps_the_planned_action_and_blocks_dispatch(self):
        turn = Turn(mode="absent", outbound=False)
        out = turn.run(LIVE)
        self.assertEqual(getattr(out, "action", None), "blocked_dispatch")
        self.assertEqual(turn.sent, [], "nothing was delivered")
        rows = [t for t in turn.traces
                if t["decision_site_id"] == "scheduling.availability.authorization"]
        self.assertEqual([r["authority_result"] for r in rows], [CLARIFY],
                         "the planned action is still visible in the trace")


class C_SingleDecisionSingleConsumer(unittest.TestCase):
    """R34-03, to the extent this milestone closes it."""

    def test_r2_18_the_faq_path_consumes_the_decision_rather_than_re_reading(self):
        reads = []
        turn = Turn(availability=te.EvidenceStatus.CONFIRMED)
        inner = turn.engine._semantic_turn_evidence
        turn.engine._semantic_turn_evidence = lambda: (reads.append(1), inner())[1]
        turn.authority(LIVE)
        turn.topics(LIVE)
        self.assertEqual(len(reads), 1, "one semantic read for the whole turn")

    def test_r2_19_the_faq_path_no_longer_calls_the_availability_reader(self):
        body = inspect.getsource(ConversationEngine._faq_topics_for_burst)
        self.assertNotIn("_semantic_availability_enquiry", body)
        self.assertNotIn("_semantic_faq_topics()", body)
        self.assertIn("self._availability_authority(", body)

    def test_r2_20_suppression_comes_from_the_decision(self):
        body = inspect.getsource(ConversationEngine._faq_topics_for_burst)
        self.assertIn("decision.suppress_hours", body, "the field now has a consumer")

    def test_r2_21_one_decision_survives_repeated_consumers(self):
        turn = Turn(mode="absent")
        for _ in range(3):
            turn.authority(LIVE)
            turn.topics(LIVE)
        rows = [t for t in turn.traces
                if t["decision_site_id"] == "scheduling.availability.authorization"]
        self.assertEqual(len(rows), 1, "duplicate delivery decides once")


class D_BuenosAiresBoundary(unittest.TestCase):
    """R34-05. Required test 12 — computed instants, never the machine clock."""

    #: (instant in UTC, the Buenos Aires calendar date it belongs to)
    BOUNDARY = (
        (datetime.datetime(2026, 9, 25, 23, 30, tzinfo=datetime.timezone.utc),
         datetime.date(2026, 9, 25)),   # 20:30 BA — same date in both zones
        (datetime.datetime(2026, 9, 26, 0, 30, tzinfo=datetime.timezone.utc),
         datetime.date(2026, 9, 25)),   # 21:30 BA — UTC has already rolled over
        (datetime.datetime(2026, 9, 26, 2, 59, tzinfo=datetime.timezone.utc),
         datetime.date(2026, 9, 25)),   # 23:59 BA — still yesterday in Argentina
        (datetime.datetime(2026, 9, 26, 3, 0, tzinfo=datetime.timezone.utc),
         datetime.date(2026, 9, 26)),   # 00:00 BA — the real boundary
    )

    def test_r2_22_the_business_date_of_an_instant_is_argentine(self):
        for moment, expected in self.BOUNDARY:
            with self.subTest(utc=moment.isoformat()):
                self.assertEqual(sched.business_date_at(moment), expected)

    def test_r2_23_utc_would_have_given_the_wrong_day_in_that_window(self):
        """UTC has rolled over, Argentina has not — the 21:00-00:00 window the audit named.

        Two of the four instants diverge: 00:30 and 02:59 UTC are still the previous day in
        Buenos Aires. 23:30 UTC is 20:30 BA (same date) and 03:00 UTC is 00:00 BA exactly,
        where the two calendars meet again.
        """
        diverging = [m for m, expected in self.BOUNDARY if m.date() != expected]
        self.assertEqual(len(diverging), 2)
        for moment in diverging:
            with self.subTest(utc=moment.isoformat()):
                self.assertNotEqual(moment.date(), sched.business_date_at(moment))

    def test_r2_24_the_zone_is_named_in_the_repository(self):
        self.assertEqual(sched.BUSINESS_TIMEZONE, "America/Argentina/Buenos_Aires")

    def test_r2_25_business_today_matches_the_zone_not_the_host(self):
        expected = datetime.datetime.now(BA).date()
        self.assertEqual(sched.business_today(), expected)

    def test_r2_26_an_aware_instant_is_required(self):
        with self.assertRaises(ValueError):
            sched.business_date_at(datetime.datetime(2026, 9, 25, 23, 30))

    def test_r2_27_relative_dates_resolve_through_the_contract(self):
        body = (BACKEND / "app/services/conversation_engine.py").read_text(encoding="utf-8")
        self.assertEqual(body.count("_parse_scheduling_text(list(texts), date.today())"), 0)
        self.assertEqual(body.count("_parse_scheduling_text(texts, date.today())"), 0)
        self.assertEqual(body.count("_parse_scheduling_text(ai_input_messages, date.today())"), 0)
        self.assertGreaterEqual(body.count("_business_today()"), 9)

    def test_r2_28_the_shipped_compose_declares_the_zone(self):
        """It was declared only in the base compose, which is outside this repository."""
        import yaml
        compose = yaml.safe_load((ROOT / "docker-compose.beta.yml").read_text(encoding="utf-8"))
        self.assertEqual(compose["services"]["backend"]["environment"]["TZ"],
                         "America/Argentina/Buenos_Aires")


class E_NoRegressionInScope(unittest.TestCase):
    """Required tests 13–14, plus the boundaries this milestone must not cross."""

    def test_r2_29_g3_3_representation_is_untouched(self):
        self.assertEqual(te.SchedulingRequestEvidence()._MEANINGFUL_FIELDS,
                         ("day_expression", "resolved_date", "time"))
        self.assertEqual(te.SCHEMA_VERSION, "turn-evidence/1.3")

    def test_r2_30_the_model_is_unchanged(self):
        interp = (BACKEND / "app/services/semantic_interpreter.py").read_text(encoding="utf-8")
        self.assertIn('or "gpt-4o-mini"', interp)
        self.assertIn('PROMPT_VERSION = "understand/1.19"', interp)

    def test_r2_31_no_provider_call_was_added(self):
        import re
        engine = (BACKEND / "app/services/conversation_engine.py").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"self\._call_openai\(", engine)), 2)
        self.assertEqual(len(re.findall(r"self\._ai_invoked = True", engine)), 1)

    def test_r2_32_the_booking_and_flow_paths_are_untouched(self):
        for path in ("app/services/booking_flow_service.py", "app/routes/flow_data_exchange.py"):
            body = (BACKEND / path).read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertNotIn("G3-4-R2", body)
                self.assertNotIn("_AVAILABILITY_VS_HOURS_CLARIFICATION", body)

    def test_r2_33_deterministic_admission_still_behaves_as_before(self):
        turn = Turn(mode="absent")
        decision = turn.authority("para cuando tenes turno")
        self.assertEqual(decision.admission, "DETERMINISTIC_EVIDENCE")
        self.assertEqual(decision.authority, LOOK_UP)

    def test_r2_34_hold_still_exists_for_a_turn_about_nothing(self):
        turn = Turn(mode="absent")
        decision = turn.authority("gracias, muy amables")
        self.assertEqual(decision.authority, HOLD)
        self.assertEqual(turn.sent, [], "a turn about nothing is not clarified at")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()


class F_PropositionSeparation(unittest.TestCase):
    """R2A. Each proposition earns its own authority — required proofs 1-16.

    The owner's invariant: a primary commercial action does not authorize a supplementary
    business-hours claim, and an explicit hours question qualifies on its own even when it
    shares a burst with another valid action.
    """

    #: Explicit hours questions. Each qualifies by COMPOSITION — an hours noun bound to a
    #: predicate about the business operating — not because it appears on a list.
    EXPLICIT = (
        "¿Qué horarios hacen?",
        "¿Qué horarios tienen?",
        "¿En qué horarios trabajan?",
        "¿A qué hora abren?",
        "¿Cuál es el horario de atención?",
    )
    #: Hours nouns with nothing establishing which question they belong to.
    WEAK_MENTIONS = (
        "¿Horarios?",
        "horarios",
        "me cambiaron el horario del trabajo, no se si voy a poder",
        "Que horarios tienen disponiblees",
        "¿Qué horarios tienen libres?",
    )
    #: Other propositions that may share the burst. None of them is about opening hours.
    COMPANIONS = (
        ("acceptance", "Okay!"),
        ("scheduling", "El martes a las 14"),
        ("vehicle correction", "Al final el Focus se cayó. Tengo un Toyota Corolla 2020."),
        ("pricing", "¿Cuánto sale la revisión?"),
    )

    def strength(self, text):
        """The classification under test — asserted against LITERALS below, never used to
        derive what a case should produce."""
        from app.services.conversation_engine import _hours_floor_strength
        return _hours_floor_strength(text)

    # 1, 2, 3, 4 — an explicit question qualifies alone AND in company
    def test_r2a_01_an_explicit_question_alone_is_strong(self):
        for text in self.EXPLICIT:
            with self.subTest(text=text):
                self.assertEqual(self.strength(text), "STRONG")
                turn = Turn(mode="absent", stage=STAGE_QUALIFYING)
                self.assertEqual(turn.topics(text), {"business_hours"})

    def test_r2a_02_it_stays_strong_beside_every_other_proposition(self):
        for label, companion in self.COMPANIONS:
            for question in self.EXPLICIT:
                burst = f"{companion} {question}"
                with self.subTest(companion=label, question=question):
                    self.assertEqual(self.strength(burst), "STRONG",
                                     "composition must not downgrade an explicit question")
                    turn = Turn(mode="absent")
                    self.assertEqual(turn.topics(burst), {"business_hours"})

    def test_r2a_03_it_stays_strong_when_the_burst_arrives_as_separate_messages(self):
        for label, companion in self.COMPANIONS:
            burst = companion + "\n" + "¿Qué horarios tienen?"
            with self.subTest(companion=label):
                self.assertEqual(self.strength(burst), "STRONG")

    # 5, 6 — a weak token alone still clarifies
    def test_r2a_04_a_weak_mention_alone_clarifies(self):
        for text in self.WEAK_MENTIONS:
            with self.subTest(text=text):
                self.assertEqual(self.strength(text), "WEAK")
                turn = Turn(mode="absent")
                self.assertEqual(turn.authority(text).authority, CLARIFY)
                self.assertEqual(turn.topics(text), set())

    def test_r2a_05_a_weak_mention_clarifies_under_provider_failure_too(self):
        for mode in ("raise", "malformed"):
            for text in ("¿Horarios?", "Que horarios tienen disponiblees"):
                with self.subTest(mode=mode, text=text):
                    turn = Turn(mode=mode)
                    self.assertEqual(turn.authority(text).authority, CLARIFY)
                    self.assertEqual(turn.topics(text), set())

    # 7, 8, 9 — a companion action must not lend its authority to a weak token
    def test_r2a_06_another_authority_never_promotes_a_weak_mention(self):
        weak = "me cambiaron el horario del trabajo"
        for label, companion in self.COMPANIONS:
            burst = f"{companion} {weak}"
            with self.subTest(companion=label):
                self.assertEqual(self.strength(burst), "WEAK",
                                 "a resolved action does not make a vague mention explicit")
                turn = Turn(mode="absent")
                self.assertEqual(turn.topics(burst), set(),
                                 "no canonical hours from borrowed authority")

    def test_r2a_07_the_companion_action_is_not_replaced_by_a_clarification(self):
        """The clarification fires only where nothing else has claimed the turn."""
        turn = Turn(mode="absent")
        out = turn.run("Okay! me cambiaron el horario del trabajo", allow_clarification=False)
        self.assertIsNone(out, "the acceptance path keeps the turn")
        self.assertEqual(turn.sent, [], "no clarification displaced the primary action")

    def test_r2a_08_only_one_production_site_may_clarify(self):
        source = (BACKEND / "app/services/conversation_engine.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("allow_clarification=True"), 1)

    # 10 — the promotion rule, stated directly
    def test_r2a_09_strength_is_a_function_of_the_burst_alone(self):
        """No state, no stage, no other authority is consulted to grade the evidence.

        CODE only: the docstring names those things in order to say they are not consulted,
        and scanning prose would fail on the sentence that states the guarantee.
        """
        import ast, textwrap
        from app.services.conversation_engine import _hours_floor_strength
        tree = ast.parse(textwrap.dedent(inspect.getsource(_hours_floor_strength)))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.Module)) and ast.get_docstring(node):
                node.body = node.body[1:]
        body = ast.unparse(tree)
        for forbidden in ("state", "stage", "authority", "acceptance", "ctx", "decision"):
            with self.subTest(term=forbidden):
                self.assertFalse(forbidden in body, f"{forbidden} reached the classifier")

    # 11 — both propositions may contribute, without duplication
    def test_r2a_10_two_authorised_propositions_compose_without_duplicating(self):
        burst = "El martes a las 14 ¿Qué horarios tienen?"
        turn = Turn(mode="absent")
        self.assertEqual(turn.topics(burst), {"business_hours"})
        from app.services.schedule import business_hours_summary_es
        engine = Turn(mode="absent").engine
        engine._faq_topics_for_burst = lambda t, ctx=None, st=None: {"business_hours"}
        engine._contributing_sources = None
        # The primary answer deliberately carries no weekday-plus-clock: the quarantined
        # FAQ-HOURS R2 stripper removes such prose as unverifiable, which is that
        # milestone's known over-removal and not this one's concern.
        composed = ConversationEngine._compose_secondary_answers(
            engine, "Anotado, ya lo registré.", burst)
        self.assertEqual(composed.count(business_hours_summary_es()), 1)
        self.assertIn("Anotado", composed, "the primary answer survives")

    # 12 — no catalogue
    def test_r2a_11_the_classifier_holds_no_sentence_or_exception_catalogue(self):
        from app.services import scheduling_lexicon as lex
        for word in lex.OPERATION_PREDICATES | lex.AVAILABILITY_OBJECTS:
            with self.subTest(word=word):
                self.assertNotIn(" ", word, f"{word!r} is a phrase, not a word")
        # STRING LITERALS only. A comment may quote a customer sentence to explain which
        # proposition it belongs to; what must never exist is a literal the code MATCHES on.
        import ast
        source = (BACKEND / "app/services/conversation_engine.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):                       # a docstring is a literal too
            if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                  ast.AsyncFunctionDef)) and ast.get_docstring(node)):
                node.body = node.body[1:]
        literals = [n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        haystack = "\n".join(literals)
        for sentence in ("¿Qué horarios hacen?", "¿Qué horarios tienen?", "disponiblees",
                         "me cambiaron el horario"):
            with self.subTest(sentence=sentence):
                self.assertNotIn(sentence, haystack)
        from app.services.conversation_engine import _HOURS_FAQ_DETECTION
        self.assertEqual(len(_HOURS_FAQ_DETECTION), 8, "the detector was not enlarged")

    # 13 — one interpretation
    def test_r2a_12_one_semantic_read_per_turn_survives_the_change(self):
        reads = []
        turn = Turn(availability=te.EvidenceStatus.CONFIRMED)
        inner = turn.engine._semantic_turn_evidence
        turn.engine._semantic_turn_evidence = lambda: (reads.append(1), inner())[1]
        turn.authority(LIVE)
        turn.topics(LIVE)
        turn.topics(LIVE)
        self.assertEqual(len(reads), 1)

    # 16 — a clarification mutates nothing
    def test_r2a_13_clarification_still_books_and_writes_nothing(self):
        turn = Turn(mode="absent")
        before = dict(vars(turn.state))
        turn.run("¿Horarios?")
        self.assertEqual(turn.sent, [EXPECTED_CLARIFICATION])
        self.assertEqual({k for k in before if before[k] != vars(turn.state)[k]}, set())
        self.assertEqual(turn.schedule_calls, [])
        self.assertFalse(turn.state.needs_human)
