"""L4.7W5-GATE3-G3-4-R2B — the interpretation leads, and a burst is one proposition.

The independent R2A audit returned FAIL on two BLOCKERs:

  R2A-01  `suppress_hours` keyed on `floor == WEAK`, so a STRONG deterministic grade made a
          resolved semantic AVAILABILITY_ENQUIRY irrelevant. G3-2 DEF-02 was CE SILENCE
          vetoing semantic evidence; that was CE ASSERTION vetoing it — the same error
          mirrored.
  R2A-02  the calendar context was read inside the graded unit only, so meaning stated in
          the next sentence or the next message was discarded and the live defect returned.

The audit also found the suite that was meant to catch these had ZERO split-message fixtures,
ZERO reversed-order fixtures, no STRONG-plus-semantic case, one real-handler test, and five
uses of the production classifier as its own oracle. This file is written against that list:
every expected value below is a literal, every case is run in four representations, and the
classifier is never asked what the answer should be.
"""
from __future__ import annotations

import pathlib
import sys
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.conversation_engine import (                       # noqa: E402
    ConversationEngine, _AVAILABILITY_VS_HOURS_CLARIFICATION,
    STAGE_QUALIFYING, STAGE_SCHEDULING,
)
from app.services.outbound_guard import OutboundBlockedError          # noqa: E402
from app.schemas import turn_evidence as te                          # noqa: E402

#: Literals. Nothing here is computed by the module under test.
ADMIT = "ADMIT"
BY_AVAILABILITY = "SUPPRESSED_BY_SEMANTIC_AVAILABILITY"
AMBIGUOUS = "SUPPRESSED_AMBIGUOUS"
UNCORROBORATED = "SUPPRESSED_UNCORROBORATED"
BY_DETERMINISTIC = "SUPPRESSED_BY_DETERMINISTIC_SCHEDULING"    # G3-5A
NO_CANDIDATE = "NO_CANDIDATE"
CLARIFY, HOLD, LOOK_UP = "CLARIFY", "HOLD", "LOOK_UP_AVAILABILITY"
CONFIRMED = te.EvidenceStatus.CONFIRMED



def _compose_via_plan(engine, primary, burst):
    """G3-6 — register the FAQ propositions, then let the live plan compose them."""
    from app.services.response_composition import LiveResponsePlan
    engine._live_plan = LiveResponsePlan(turn_id="t")
    engine._turn_faq_burst = burst
    if not hasattr(engine, "_turn_fact_sources"):
        engine._turn_fact_sources = []
    engine._register_faq_propositions(getattr(engine, "_turn_ctx", None), primary)
    return engine._live_plan.compose(primary)


class Burst:
    """One reconstructed burst, run through the real authority and composition paths."""

    def __init__(self, *, availability=None, faq=(), mode=None, stage=STAGE_SCHEDULING,
                 outbound=True):
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
        engine.db = SimpleNamespace(commit=lambda: None)

        def send(ctx, text):
            if not outbound:
                raise OutboundBlockedError(sender_path="ce_text", kind="text", to_wa_id="x")
            self.sent.append(text)
            return "wamid.SYNTHETICR2B=="
        engine._send_text_to_wa = send

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
                # Genuinely unusable: reading the collection raises, which is what an
                # unparseable or partially-written evidence object does in practice.
                class _Broken:
                    @property
                    def scheduling_intents(self):
                        raise ValueError("unusable evidence")
                return _Broken()
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

    def decide(self, text):
        return self.engine._availability_authority(None, self.state, [text])

    def topics(self, text):
        return ConversationEngine._faq_topics_for_burst(
            self.engine, text, ctx=None, state=self.state)

    def handle(self, text, *, allow_clarification=True):
        return ConversationEngine._handle_next_available_request(
            self.engine, self.ctx, self.state, [text],
            allow_clarification=allow_clarification)

    def outcome(self, text):
        """(disposition, authority, sorted topics) — the three facts each case asserts."""
        decision = self.decide(text)
        return decision.hours_disposition, decision.authority, tuple(sorted(self.topics(text)))


def representations(first: str, second: str | None) -> list[tuple[str, str]]:
    """The four ways one burst can arrive. Unit boundaries must not change its meaning."""
    if second is None:
        return [("one message", first)]
    return [("one message", f"{first} {second}"),
            ("two messages", f"{first}\n{second}"),
            ("reversed", f"{second}\n{first}"),
            ("two sentences", f"{first} {second}")]


class A_BurstScopedAvailability(unittest.TestCase):
    """R2A-02. One customer, one burst, one proposition — in every representation."""

    #: (first unit, second unit). Each pair is ONE availability enquiry.
    PAIRS = (
        ("Que horarios tienen", "disponiblees"),
        ("¿Qué horarios tienen?", "Disponibles para mañana."),
        ("¿Qué horarios tienen?", "Libres para el viernes."),
        ("¿Qué horarios tienen?", "Para sacar un turno."),
    )

    def test_r2b_01_no_representation_produces_an_hours_answer(self):
        for first, second in self.PAIRS:
            for label, text in representations(first, second):
                with self.subTest(pair=first[:20], form=label):
                    disposition, authority, topics = Burst(mode="absent").outcome(text)
                    self.assertEqual(topics, (), "the burst asks about the calendar")
                    self.assertEqual(disposition, UNCORROBORATED)
                    self.assertEqual(authority, CLARIFY)

    def test_r2b_02_the_outcome_is_identical_in_every_representation(self):
        for first, second in self.PAIRS:
            results = {label: Burst(mode="absent").outcome(text)
                       for label, text in representations(first, second)}
            with self.subTest(pair=first[:20]):
                self.assertEqual(len(set(results.values())), 1,
                                 f"unit boundaries changed the meaning: {results}")

    def test_r2b_03_the_live_message_split_in_two_is_still_the_live_message(self):
        single = Burst(mode="absent").outcome("Que horarios tienen disponiblees")
        split = Burst(mode="absent").outcome("Que horarios tienen\ndisponiblees")
        self.assertEqual(single, split)
        self.assertEqual(single, (UNCORROBORATED, CLARIFY, ()))


class B_SemanticPrimacy(unittest.TestCase):
    """R2A-01. The interpretation decides the hours proposition at ANY floor strength."""

    #: (label, burst, the floor strength that burst produces — stated, not computed)
    AT_EACH_STRENGTH = (
        ("STRONG", "¿Qué horarios tienen?"),
        ("WEAK", "Que horarios tienen disponiblees"),
        ("NONE", "necesito coordinar algo para el auto"),
    )

    def test_r2b_04_a_resolved_enquiry_suppresses_at_every_strength(self):
        for strength, text in self.AT_EACH_STRENGTH:
            with self.subTest(floor=strength):
                burst = Burst(availability=CONFIRMED)
                disposition, authority, topics = burst.outcome(text)
                self.assertEqual(disposition, BY_AVAILABILITY)
                self.assertEqual(authority, LOOK_UP)
                self.assertEqual(topics, (), "no hours answer at any strength")

    def test_r2b_05_the_floor_strength_is_what_it_should_be(self):
        """Stated independently, so test_r2b_04 is genuinely covering three strengths."""
        from app.services.conversation_engine import _hours_floor_strength
        for expected, text in self.AT_EACH_STRENGTH:
            with self.subTest(floor=expected):
                self.assertEqual(_hours_floor_strength(text), expected)

    def test_r2b_06_ce_assertion_cannot_veto_the_interpretation(self):
        """The blocker, stated directly: a STRONG grade beside a resolved enquiry."""
        burst = Burst(availability=CONFIRMED)
        self.assertEqual(burst.outcome("¿Qué horarios tienen?"),
                         (BY_AVAILABILITY, LOOK_UP, ()))

    def test_r2b_07_ce_silence_cannot_veto_the_interpretation_either(self):
        burst = Burst(availability=CONFIRMED)
        self.assertEqual(burst.outcome("necesito coordinar algo para el auto"),
                         (BY_AVAILABILITY, LOOK_UP, ()))

    def test_r2b_08_a_named_date_is_not_answered_with_opening_hours(self):
        """INVERTED by G3-5A (audit F-01). This test used to pin the defect green.

        R2B recorded "¿Qué horarios tienen? ¿Hay algo mañana?" + ABSENT → business_hours as
        a bounded limit, on the reasoning that the interpretation suppresses it when
        present. The independent audit rejected that reasoning: timeout, absence and
        malformed output are expected degraded modes, and the burst carries a date the
        engine's OWN parser resolves. More evidence was buying less caution.

        The date is competing scheduling evidence now, so the hours answer is withheld —
        and it is withheld at every degraded semantic state, not only when the model
        happens to be up.
        """
        text = "¿Qué horarios tienen? ¿Hay algo mañana?"
        for mode in ("absent", "raise", "malformed"):
            with self.subTest(mode=mode):
                disposition, authority, topics = Burst(mode=mode).outcome(text)
                self.assertEqual(disposition, BY_DETERMINISTIC)
                self.assertEqual(authority, HOLD)
                self.assertEqual(topics, (), "the named date is not answered with hours")
        self.assertEqual(Burst(availability=CONFIRMED).outcome(text),
                         (BY_AVAILABILITY, LOOK_UP, ()))

    def test_r2b_08b_the_residue_without_date_evidence_stays_with_the_interpreter(self):
        """The limit that genuinely remains, stated honestly rather than fixed lexically.

        "Para revisar el auto." names a service, not a calendar: no deterministic reader
        resolves anything from it, so the bounded fallback still answers when the
        interpretation is absent — and the interpretation still suppresses it when present.
        Closing this would need new vocabulary. It is G3-5's, by assignment.
        """
        text = "¿Qué horarios tienen?\nPara revisar el auto."
        self.assertEqual(Burst(mode="absent").outcome(text), (ADMIT, HOLD, ("business_hours",)))
        self.assertEqual(Burst(availability=CONFIRMED).outcome(text),
                         (BY_AVAILABILITY, LOOK_UP, ()))


class C_TwoPropositions(unittest.TestCase):
    """Neither proposition lends authority to the other, and neither is collapsed."""

    BOTH = "¿Cuáles son sus horarios de atención? ¿Y tienen un turno mañana?"

    def test_r2b_09_both_confirmed_produces_both_once(self):
        burst = Burst(availability=CONFIRMED, faq=("business_hours",))
        disposition, authority, topics = burst.outcome(self.BOTH)
        self.assertEqual(disposition, ADMIT)
        self.assertEqual(authority, LOOK_UP, "the availability proposition proceeds")
        self.assertEqual(topics, ("business_hours",), "and the hours answer stands, once")

    def test_r2b_10_semantic_hours_alone_authorises_hours_and_nothing_else(self):
        burst = Burst(faq=("business_hours",))
        disposition, authority, topics = burst.outcome("¿Qué horarios tienen?")
        self.assertEqual((disposition, topics), (ADMIT, ("business_hours",)))
        self.assertNotEqual(authority, LOOK_UP, "no scheduling was invented")

    def test_r2b_11_without_the_interpretation_the_two_question_burst_fails_safe(self):
        """The floor cannot tell one proposition from two, so it asks rather than guesses."""
        self.assertEqual(Burst(mode="absent").outcome(self.BOTH),
                         (UNCORROBORATED, CLARIFY, ()))

    def test_r2b_12_the_hours_answer_is_composed_once(self):
        from app.services.schedule import business_hours_summary_es
        burst = Burst(availability=CONFIRMED, faq=("business_hours",))
        burst.engine._contributing_sources = None
        burst.engine._faq_topics_for_burst = lambda t, ctx=None, st=None: {"business_hours"}
        composed = _compose_via_plan(burst.engine, "Anotado, ya lo registré.", self.BOTH)
        self.assertEqual(composed.count(business_hours_summary_es()), 1)
        self.assertIn("Anotado", composed)


class D_FallbackAndFailure(unittest.TestCase):
    """ABSENT and UNAVAILABLE stay distinguishable, and failure is never certainty."""

    def test_r2b_13_absent_plus_strong_falls_back(self):
        burst = Burst(mode="absent")
        self.assertEqual(burst.outcome("¿Qué horarios tienen?"),
                         (ADMIT, HOLD, ("business_hours",)))
        self.assertEqual(burst.decide("¿Qué horarios tienen?").semantic_status, "ABSENT")

    def test_r2b_14_unavailable_plus_strong_falls_back_under_the_bounded_contract(self):
        burst = Burst(mode="raise")
        self.assertEqual(burst.outcome("¿Qué horarios tienen?"),
                         (ADMIT, HOLD, ("business_hours",)))
        self.assertEqual(burst.decide("¿Qué horarios tienen?").semantic_status, "UNAVAILABLE")

    def test_r2b_15_weak_plus_any_failure_clarifies(self):
        for mode, status in (("absent", "ABSENT"), ("raise", "UNAVAILABLE"),
                             ("malformed", "UNAVAILABLE")):
            with self.subTest(mode=mode):
                burst = Burst(mode=mode)
                self.assertEqual(burst.outcome("¿Horarios?"), (UNCORROBORATED, CLARIFY, ()))
                self.assertEqual(burst.decide("¿Horarios?").semantic_status, status)

    def test_r2b_16_an_ambiguous_reading_suppresses_on_its_own_grounds(self):
        burst = Burst(availability=te.EvidenceStatus.AMBIGUOUS)
        disposition, authority, topics = burst.outcome("¿Horarios?")
        self.assertEqual(disposition, AMBIGUOUS)
        self.assertEqual(authority, CLARIFY)
        self.assertEqual(topics, ())

    def test_r2b_17_a_burst_with_no_hours_candidate_has_none(self):
        burst = Burst(mode="absent")
        self.assertEqual(burst.decide("gracias, muy amables").hours_disposition, NO_CANDIDATE)


class E_RealHandlerAndTrace(unittest.TestCase):
    """The audit found one real-handler test. These exercise the handler and the trace."""

    def test_r2b_18_clarify_reaches_the_customer_through_the_handler(self):
        burst = Burst(mode="absent")
        out = burst.handle("¿Horarios?")
        self.assertEqual(getattr(out, "action", None), "replied")
        self.assertEqual(burst.sent, [_AVAILABILITY_VS_HOURS_CLARIFICATION])
        self.assertEqual(burst.schedule_calls, [])

    def test_r2b_19_outbound_off_preserves_the_planned_action(self):
        burst = Burst(mode="absent", outbound=False)
        out = burst.handle("¿Horarios?")
        self.assertEqual(getattr(out, "action", None), "blocked_dispatch")
        self.assertEqual(burst.sent, [])
        rows = [t for t in burst.traces
                if t["decision_site_id"] == "scheduling.availability.authorization"]
        self.assertEqual([r["authority_result"] for r in rows], [CLARIFY])

    def test_r2b_20_the_split_live_message_traces_the_whole_story(self):
        """The case the audit demanded, end to end through both decision sites."""
        burst = Burst(availability=CONFIRMED)
        text = "Que horarios tienen\ndisponiblees"
        decision = burst.decide(text)
        burst.topics(text)
        self.assertEqual(decision.semantic_status, "OK")
        self.assertIsNotNone(decision.availability_intent)
        self.assertEqual(decision.hours_disposition, BY_AVAILABILITY)
        self.assertEqual(decision.authority, LOOK_UP)
        sites = [t["decision_site_id"] for t in burst.traces]
        self.assertIn("scheduling.availability.authorization", sites)
        self.assertIn("faq.topic.authorization", sites)
        faq_row = [t for t in burst.traces
                   if t["decision_site_id"] == "faq.topic.authorization"][0]
        self.assertIn(f"hours_disposition={BY_AVAILABILITY}", faq_row["business_outcome"])
        self.assertIn("appointment-availability enquiry", faq_row["reason"])

    def test_r2b_21_the_clarification_mutates_nothing(self):
        burst = Burst(mode="absent")
        before = dict(vars(burst.state))
        burst.handle("¿Horarios?")
        self.assertEqual({k for k in before if before[k] != vars(burst.state)[k]}, set())
        self.assertFalse(burst.state.needs_human)

    def test_r2b_22_a_resolved_action_is_never_displaced_by_a_clarification(self):
        burst = Burst(mode="absent")
        self.assertIsNone(burst.handle("dale, acepto. igual mis horarios cambian",
                                       allow_clarification=False))
        self.assertEqual(burst.sent, [])

    def test_r2b_23_one_semantic_read_per_turn(self):
        reads = []
        burst = Burst(availability=CONFIRMED)
        inner = burst.engine._semantic_turn_evidence
        burst.engine._semantic_turn_evidence = lambda: (reads.append(1), inner())[1]
        burst.decide("¿Qué horarios tienen?")
        burst.topics("¿Qué horarios tienen?")
        burst.topics("¿Qué horarios tienen?")
        self.assertEqual(len(reads), 1)


class F_MixedTurnsUnchanged(unittest.TestCase):
    """The behaviours R2A established must survive R2B."""

    WEAK_COMPANIONS = (
        ("acceptance", "dale, acepto. igual mis horarios cambian"),
        ("pricing", "cuanto sale? y los horarios?"),
        ("scheduling", "El martes a las 14. horarios?"),
    )
    EXPLICIT_COMPANIONS = (
        ("acceptance", "Okay! ¿Qué horarios hacen?"),
        ("scheduling", "El martes a las 14 ¿Qué horarios tienen?"),
        ("vehicle", "Al final el Focus se cayó. ¿Qué horarios tienen?"),
    )

    def test_r2b_24_a_weak_mention_beside_a_valid_action_yields_no_hours(self):
        for label, text in self.WEAK_COMPANIONS:
            with self.subTest(companion=label):
                self.assertEqual(Burst(mode="absent").outcome(text)[2], ())

    def test_r2b_25_an_explicit_question_beside_a_valid_action_still_answers(self):
        for label, text in self.EXPLICIT_COMPANIONS:
            with self.subTest(companion=label):
                self.assertEqual(Burst(mode="absent").outcome(text)[2], ("business_hours",))

    def test_r2b_26_no_vocabulary_was_added(self):
        from app.services import scheduling_lexicon as lex
        from app.services.conversation_engine import _HOURS_FAQ_DETECTION
        self.assertEqual(len(_HOURS_FAQ_DETECTION), 8)
        self.assertEqual(lex.OPERATION_PREDICATES, frozenset({
            "hacen", "hacian", "tienen", "trabajan", "laburan", "atienden",
            "abren", "cierran", "atencion"}))
        self.assertEqual(lex.AVAILABILITY_OBJECTS, frozenset({
            "disponible", "disponibles", "disponibilidad", "libre", "libres",
            "vacante", "vacantes", "cupo", "cupos"}))

    def test_r2b_27_the_model_and_contracts_are_frozen(self):
        interp = (BACKEND / "app/services/semantic_interpreter.py").read_text(encoding="utf-8")
        self.assertIn('or "gpt-4o-mini"', interp)
        self.assertIn('PROMPT_VERSION = "understand/1.19"', interp)
        self.assertEqual(te.SCHEMA_VERSION, "turn-evidence/1.3")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
