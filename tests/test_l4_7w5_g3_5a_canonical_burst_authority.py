"""L4.7W5-GATE3-G3-5A — one burst per turn, and a named date is not opening hours.

The independent R2B audit returned two HIGH findings:

  F-01  the disposition ladder never consulted `_parse_scheduling_text`, the date/time
        parser the engine already trusts (`_turn_has_scheduling_evidence` reads it the same
        way). So "¿Qué horarios tienen? ¿Hay algo mañana?" with the interpretation absent,
        timed out or unusable graded STRONG, answered with the weekday table, and discarded
        the date the customer named. More evidence bought less caution, during an outage.
  F-02  `_availability_authority` was memoised on whichever caller ran first, and its two
        callers pass different text — `current_turn_text` (built from `_current_evidence`,
        INCLUDING database-recovered messages) versus `ai_input_messages` (which can be a
        strict subset). Call order decided the turn.

The audit also rejected the R2B suite's method: separate messages were represented with
"\n" while production joins with " ", and `test_r2b_08` pinned F-01 green. The proofs below
drive the REAL reconstruction path — a real session, real `whatsapp_messages` rows, real
`_fetch_burst_messages` recovery, real `handle()` — so the burst under test is the one
production builds, not one this file joined.

Nothing here adds vocabulary. Every reader used is one the engine already had.
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

# The WILD-04R harness owns the SQLite/app.db bootstrap and the burst-recovery fixtures.
# Importing it is deliberate: these tests must exercise the same reconstruction the burst
# completeness work is certified against, not a private copy of it.
from test_wild04r_cycle_boundary import (                              # noqa: E402
    _WA_ID, _add_inbound_message, _add_state, _make_engine, _new_session,
    _seed_contact_thread_lead,
)

from app.schemas.conversation import ConversationHandleIn              # noqa: E402
import app.services.conversation_engine as ce                          # noqa: E402
from app.services.conversation_engine import (                          # noqa: E402
    ConversationEngine, _canonical_burst_from_texts, STAGE_QUALIFYING, STAGE_QUOTED,
    STAGE_SCHEDULING,
)
from app.services.outbound_safety_gate import GateOutcome               # noqa: E402
from app.schemas import turn_evidence as te                            # noqa: E402

#: Literals. Nothing below is computed by the module under test.
ADMIT = "ADMIT"
BY_AVAILABILITY = "SUPPRESSED_BY_SEMANTIC_AVAILABILITY"
BY_DETERMINISTIC = "SUPPRESSED_BY_DETERMINISTIC_SCHEDULING"
AMBIGUOUS = "SUPPRESSED_AMBIGUOUS"
UNCORROBORATED = "SUPPRESSED_UNCORROBORATED"
NO_CANDIDATE = "NO_CANDIDATE"
CLARIFY, HOLD, LOOK_UP = "CLARIFY", "HOLD", "LOOK_UP_AVAILABILITY"
CONFIRMED = te.EvidenceStatus.CONFIRMED

HOURS_Q = "¿Qué horarios tienen?"
AVAIL_M = "Disponibles para mañana."


# ── the real reconstruction path ─────────────────────────────────────────────

class LiveTurn:
    """A real thread, real persisted inbound rows, and the engine's own `handle()`.

    `db_messages` are written to `whatsapp_messages` so `_fetch_burst_messages` recovers
    them; `unanswered` is what n8n claims to have seen. When `unanswered` is narrower than
    the persisted burst, this is the exact production shape of F-02.
    """

    def __init__(self, db_messages, unanswered, *, stage=STAGE_SCHEDULING,
                 availability=None, faq=(), mode=None):
        self.db = _new_session()
        _, thread, _lead = _seed_contact_thread_lead(self.db)
        _add_state(self.db, thread.id, last_stage=stage,
                   last_processed_inbound_wa_message_id="prev-id")
        _add_inbound_message(self.db, thread.id, "prev-id", "previo", offset_seconds=-30)
        for i, text in enumerate(db_messages):
            _add_inbound_message(self.db, thread.id, f"m-{i}", text, offset_seconds=i * 5)
        self.thread_id = thread.id
        self.current_wamid = f"m-{len(db_messages) - 1}"
        self.current_text = db_messages[-1]
        self.unanswered = list(unanswered)

        self.sent: list[str] = []
        self.traces: list[dict] = []
        self.schedule_calls: list[dict] = []
        self.reads = 0

        engine = _make_engine(self.db)
        # G3-5B (audit G5A-05): `_send_text_to_wa` is NOT replaced. `_compose_secondary_
        # answers` runs inside it, so replacing it bypassed the composer and made a
        # customer-facing assertion vacuous — it passed on the parent defect. Capture
        # happens at the lowest external boundary instead.
        engine._schedule = SimpleNamespace(
            find_next_available=lambda **kw: (self.schedule_calls.append(kw), None)[1])
        original_trace = engine._trace_decision

        def spy(**kw):
            self.traces.append(kw)
            return original_trace(**kw)
        engine._trace_decision = spy

        evidence = None
        if availability is not None or faq:
            evidence = te.TurnEvidence(
                scheduling_intents=((te.SchedulingIntentEvidence(
                    kind=te.SchedulingIntentKind.AVAILABILITY_ENQUIRY,
                    value="AVAILABILITY_ENQUIRY", status=availability),)
                    if availability else ()),
                faq_intents=tuple(te.FaqIntentEvidence(topic=t, value=t) for t in faq))

        def read():
            self.reads += 1
            if mode == "raise":
                raise TimeoutError("provider timeout")
            if mode == "absent":
                return None
            if mode == "malformed":
                class _Broken:
                    @property
                    def scheduling_intents(self):
                        raise ValueError("unusable evidence")
                return _Broken()
            return evidence
        engine._semantic_turn_evidence = read
        self.engine = engine

    def run(self):
        """Run the complete handler. Only external boundaries are controlled."""
        event = ConversationHandleIn(
            thread_id=self.thread_id, wa_message_id=self.current_wamid, wa_id=_WA_ID,
            text=self.current_text, unanswered_recent_user_messages=self.unanswered,
            recent_user_messages=self.unanswered)
        sent = self.sent

        class _Sink:
            """The outbound transport. Records the fully composed customer text."""
            def __init__(self, db): self.db = db
            def attempt(self, *, wa_id, thread_id, text, **kw):
                sent.append(text)
                return SimpleNamespace(outcome=GateOutcome.ALLOWED, message_id=None,
                                       blocked_reason=None)
            def mark_sent(self, *a, **k): return None
            def mark_failed(self, *a, **k): return None

        class _Reply:
            """The reply provider. Neutral: no hours, no date, no scheduling claim."""
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                return json.dumps({"choices": [{"message": {"content": json.dumps({
                    "intent": "QUALIFYING", "reply": "Gracias por escribirnos.",
                    "deferred_interest": False, "candidate": {"action": "none"},
                    "extracted": {}, "lead_flag": None, "needs_human": False})}}]}).encode()

        with patch.object(ce, "OutboundSafetyGate", _Sink), \
             patch.object(ce, "_send_whatsapp_cloud_text", lambda **kw: "wamid.G35A"), \
             patch("urllib.request.urlopen", lambda *a, **k: _Reply()):
            try:
                self.out = self.engine.handle(event)
            except Exception as exc:                 # SQLite lacks NOW(); the composed
                self.out = SimpleNamespace(          # text is already captured above.
                    action=f"<raised {type(exc).__name__}>")
        return self

    @property
    def customer_text(self) -> str:
        return " ".join(self.sent)

    def names_the_weekday_table(self) -> bool:
        low = self.customer_text.lower()
        return any(m in low for m in ("lunes de", "sabados de", "sábados de", "domingos no"))

    @property
    def burst(self):
        return self.engine._canonical_turn_burst

    @property
    def decision(self):
        return self.engine._availability_authority_cache

    def close(self):
        self.db.close()


def outcome_for(texts, *, availability=None, faq=(), mode=None, stage=STAGE_SCHEDULING,
                state_extra=None):
    """The authority and FAQ outcome for an ordered message LIST, via the real factory.

    The canonical burst is built by production's own `_canonical_burst`, from the ordered
    messages — never from a string this file joined.
    """
    engine = ConversationEngine.__new__(ConversationEngine)
    engine._availability_authority_cache = None
    engine._hybrid_trace_on = lambda: True
    traces: list[dict] = []
    engine._trace_decision = lambda **kw: traces.append(kw)
    engine._semantic_scheduling_claims = lambda st: []
    engine._topics_already_answered_this_cycle = lambda c: set()
    engine._turn_scheduling_requested = False
    engine._decision_log = lambda *a, **k: None
    lookups: list[dict] = []
    engine._schedule = SimpleNamespace(
        find_next_available=lambda **kw: (lookups.append(kw), None)[1])
    engine.db = SimpleNamespace(commit=lambda: None)
    engine._send_text_to_wa = lambda ctx, t: "wamid.G35A"

    evidence = None
    if availability is not None or faq:
        evidence = te.TurnEvidence(
            scheduling_intents=((te.SchedulingIntentEvidence(
                kind=te.SchedulingIntentKind.AVAILABILITY_ENQUIRY,
                value="AVAILABILITY_ENQUIRY", status=availability),) if availability else ()),
            faq_intents=tuple(te.FaqIntentEvidence(topic=t, value=t) for t in faq))
    reads = {"n": 0}

    def read():
        reads["n"] += 1
        if mode == "raise":
            raise TimeoutError("provider timeout")
        if mode == "absent":
            return None
        if mode == "malformed":
            class _Broken:
                @property
                def scheduling_intents(self):
                    raise ValueError("unusable evidence")
            return _Broken()
        return evidence
    engine._semantic_turn_evidence = read

    fields = dict(last_stage=stage, current_revision_id=None, needs_human=False,
                  flow_booking_token=None, active_requested_date=None,
                  last_visible_slots=None, last_offered_slots=None, preferred_day=None,
                  preferred_time=None, current_focus_candidate_id=None,
                  home_zone_group=None, home_zone_detail=None)
    fields.update(state_extra or {})
    state = SimpleNamespace(**fields)

    engine._canonical_turn_burst = _canonical_burst_from_texts(
        thread_id=1, turn_id="t-g35a", texts=list(texts),
        provenance=("event_evidence",), presentation_texts=list(texts),
        stage_at_build=stage)

    decision = engine._availability_authority(None, state, list(texts))
    topics = ConversationEngine._faq_topics_for_burst(
        engine, engine._canonical_turn_burst.joined, ctx=None, state=state)
    return SimpleNamespace(decision=decision, topics=tuple(sorted(topics)),
                           burst=engine._canonical_turn_burst, lookups=len(lookups),
                           traces=traces, reads=reads["n"], state=state)


# ══ A — the canonical burst is the turn's, built by production ═══════════════

class A_CanonicalBurst(unittest.TestCase):

    def setUp(self):
        self.turn = LiveTurn([AVAIL_M, HOURS_Q], unanswered=[HOURS_Q], mode="absent").run()

    def tearDown(self):
        self.turn.close()

    def test_g35a_01_database_recovered_messages_are_in_the_canonical_burst(self):
        """n8n sent one message; the thread holds two. The decision sees both."""
        self.assertEqual(self.turn.burst.texts, (AVAIL_M, HOURS_Q))
        self.assertIn("db_recovery", self.turn.burst.provenance)

    def test_g35a_02_the_partial_set_is_presentation_only(self):
        """`ai_input_messages` survives for reply generation and defines no evidence."""
        self.assertEqual(self.turn.burst.presentation_texts, (HOURS_Q,))
        self.assertNotEqual(self.turn.burst.presentation_texts, self.turn.burst.texts)
        self.assertLess(len(self.turn.burst.presentation_texts), len(self.turn.burst.texts))

    def test_g35a_03_the_join_is_the_production_join(self):
        """A space, not a newline. The R2B suite proved the wrong shape."""
        self.assertEqual(self.turn.burst.joined, f"{AVAIL_M} {HOURS_Q}")
        self.assertNotIn("\n", self.turn.burst.joined)

    def test_g35a_04_message_identity_is_ordered_and_recorded(self):
        self.assertEqual(self.turn.burst.ordered_message_ids, ("m-0", "m-1"))

    def test_g35a_05_the_fingerprint_proves_order(self):
        forward = _canonical_burst_from_texts(
            thread_id=1, turn_id=None, texts=[AVAIL_M, HOURS_Q], provenance=())
        reversed_ = _canonical_burst_from_texts(
            thread_id=1, turn_id=None, texts=[HOURS_Q, AVAIL_M], provenance=())
        self.assertNotEqual(forward.fingerprint, reversed_.fingerprint)
        self.assertEqual(len(forward.fingerprint), 64)


# ══ B — F-02: caller order and partial subsets cannot decide ════════════════

class B_CallerOrderInvariance(unittest.TestCase):
    """The audit's exact case, through `handle()`, in both consumer orders."""

    def _turn(self):
        return LiveTurn([AVAIL_M, HOURS_Q], unanswered=[HOURS_Q], mode="absent").run()

    def test_g35a_06_the_partial_input_does_not_win(self):
        """BEFORE (6e12c4d): STRONG / ADMIT / HOLD / business_hours — the subset decided.

        The subset is "¿Qué horarios tienen?" alone, which grades STRONG because the word
        that makes it a calendar question lives in the message n8n omitted.
        """
        turn = self._turn()
        try:
            d = turn.decision
            self.assertEqual(d.floor, "WEAK", "the full burst names the calendar")
            self.assertEqual(d.hours_disposition, UNCORROBORATED)
            self.assertEqual(d.authority, CLARIFY)
            self.assertTrue(d.suppress_hours)
            self.assertEqual(d.burst_fingerprint, turn.burst.fingerprint)
        finally:
            turn.close()

    def test_g35a_07_the_customer_gets_the_clarification_not_the_hours_table(self):
        """REPAIRED by G3-5B (audit G5A-05).

        The old version replaced `_send_text_to_wa`, which is where
        `_compose_secondary_answers` runs — so the composer never executed and the
        "no weekday table" assertion passed on the parent defect as readily as on the fix.
        Capture now happens at the outbound transport boundary, and the test proves it can
        SEE a weekday table by asserting one in the unopposed case.
        """
        from app.services.conversation_engine import _AVAILABILITY_VS_HOURS_CLARIFICATION
        turn = self._turn()
        try:
            self.assertIn(_AVAILABILITY_VS_HOURS_CLARIFICATION, turn.customer_text)
            self.assertFalse(turn.names_the_weekday_table(),
                             "no weekday table may reach the customer here")
        finally:
            turn.close()
        # The control arm: the same harness, an unopposed hours question, and the table
        # DOES reach the customer. Without this the assertion above proves nothing.
        control = LiveTurn(["¿Cuál es el horario de atención?"],
                           unanswered=["¿Cuál es el horario de atención?"],
                           stage=STAGE_QUALIFYING, mode="absent").run()
        try:
            self.assertTrue(control.names_the_weekday_table(),
                            "the harness cannot see the hours table; the proof is vacuous")
        finally:
            control.close()

    def test_g35a_08_both_consumer_orders_agree_on_everything(self):
        """FAQ-first and handler-first, on one engine each, compared field by field."""
        results = []
        for first in ("faq", "handler"):
            turn = LiveTurn([AVAIL_M, HOURS_Q], unanswered=[HOURS_Q], mode="absent")
            engine, state = turn.engine, SimpleNamespace(
                last_stage=STAGE_SCHEDULING, current_revision_id=None, needs_human=False)
            engine._canonical_turn_burst = _canonical_burst_from_texts(
                thread_id=turn.thread_id, turn_id="t", texts=[AVAIL_M, HOURS_Q],
                provenance=("event_evidence", "db_recovery"),
                presentation_texts=[HOURS_Q], stage_at_build=STAGE_SCHEDULING)
            if first == "faq":
                topics = ConversationEngine._faq_topics_for_burst(
                    engine, f"{AVAIL_M} {HOURS_Q}", ctx=None, state=state)
                decision = engine._availability_authority(None, state, [HOURS_Q])
            else:
                decision = engine._availability_authority(None, state, [HOURS_Q])
                topics = ConversationEngine._faq_topics_for_burst(
                    engine, f"{AVAIL_M} {HOURS_Q}", ctx=None, state=state)
            results.append((decision.burst_fingerprint, decision.semantic_status,
                            decision.floor, decision.hours_disposition, decision.authority,
                            decision.suppress_hours, tuple(sorted(topics)),
                            decision.requested_day_iso, decision.scheduling_evidence))
            turn.close()
        self.assertEqual(results[0], results[1], "caller order changed the turn")
        self.assertEqual(results[0][3], UNCORROBORATED)
        self.assertEqual(results[0][6], ())

    def test_g35a_09_a_divergent_caller_is_detected_and_traced(self):
        turn = LiveTurn([AVAIL_M, HOURS_Q], unanswered=[HOURS_Q], mode="absent")
        try:
            engine = turn.engine
            state = SimpleNamespace(last_stage=STAGE_SCHEDULING, needs_human=False)
            engine._canonical_turn_burst = _canonical_burst_from_texts(
                thread_id=1, turn_id="t", texts=[AVAIL_M, HOURS_Q],
                provenance=("event_evidence", "db_recovery"),
                presentation_texts=[HOURS_Q])
            turn.traces.clear()
            decision = engine._availability_authority(None, state, [HOURS_Q])
            sites = [t for t in turn.traces if t.get("outcome") == "BURST_DIVERGENCE"]
            self.assertEqual(len(sites), 1, "the attempt must be recorded, not absorbed")
            self.assertTrue(decision.burst_divergence)
            self.assertIn("canonical_messages=2", sites[0]["business_outcome"])
            self.assertIn("caller_messages=1", sites[0]["business_outcome"])
        finally:
            turn.close()

    def test_g35a_10_an_agreeing_caller_raises_no_divergence(self):
        r = outcome_for([AVAIL_M, HOURS_Q], mode="absent")
        self.assertFalse(r.decision.burst_divergence)
        self.assertEqual([t for t in r.traces if t.get("outcome") == "BURST_DIVERGENCE"], [])

    def test_g35a_11_repeated_consumer_calls_return_the_same_decision_object(self):
        """Duplicate delivery of the same turn must not re-decide it, or re-read the model."""
        turn = LiveTurn([AVAIL_M, HOURS_Q], unanswered=[HOURS_Q], mode="absent").run()
        try:
            state = SimpleNamespace(last_stage=STAGE_SCHEDULING, needs_human=False)
            reads_after_handle = turn.reads
            again = [turn.engine._availability_authority(None, state, [HOURS_Q])
                     for _ in range(3)]
            self.assertIs(again[0], again[1])
            self.assertIs(again[1], again[2])
            self.assertIs(again[0], turn.decision)
            self.assertEqual(turn.reads, reads_after_handle,
                             "a repeated consumer must not reach the interpreter again")
        finally:
            turn.close()


# ══ C — F-01: a resolved date is a competing proposition ════════════════════

class C_DeterministicSchedulingEvidence(unittest.TestCase):

    DEGRADED = (("absent", "ABSENT"), ("raise", "UNAVAILABLE"), ("malformed", "UNAVAILABLE"))
    #: An explicit hours question standing beside a date the engine's own parser resolves.
    DATED = (
        ("tomorrow",       [HOURS_Q, "¿Hay algo mañana?"]),
        ("weekday",        [HOURS_Q, "¿Y el martes?"]),
        ("exact date",     [HOURS_Q, "¿El 30 de septiembre?"]),
        ("numeric date",   [HOURS_Q, "¿El 30/9?"]),
        ("exact time",     [HOURS_Q, "¿A las 14?"]),
        ("date and time",  [HOURS_Q, "¿El martes a las 14?"]),
    )

    def test_g35a_12_a_named_date_is_never_answered_with_opening_hours(self):
        """BEFORE (6e12c4d): every row was STRONG / ADMIT / HOLD / business_hours."""
        for label, texts in self.DATED:
            for mode, status in self.DEGRADED:
                with self.subTest(case=label, semantic=status):
                    r = outcome_for(texts, mode=mode)
                    self.assertEqual(r.decision.semantic_status, status)
                    self.assertEqual(r.decision.floor, "STRONG",
                                     "the hours question is still explicit")
                    self.assertEqual(r.decision.hours_disposition, BY_DETERMINISTIC)
                    self.assertEqual(r.decision.authority, HOLD)
                    self.assertTrue(r.decision.suppress_hours)
                    self.assertEqual(r.topics, (), "no opening hours over a named date")

    def test_g35a_13_the_resolved_date_is_preserved_not_discarded(self):
        r = outcome_for([HOURS_Q, "¿Hay algo mañana?"], mode="absent")
        self.assertTrue(r.decision.scheduling_evidence)
        self.assertTrue(r.burst.competing_day_iso, "the parsed day stays on the burst")
        self.assertIn("day=", r.decision.hours_suppression_reason)
        self.assertIn("deterministic scheduling parser", r.decision.hours_suppression_reason)

    def test_g35a_14_nothing_is_invented_booked_or_mutated(self):
        for label, texts in self.DATED:
            with self.subTest(case=label):
                r = outcome_for(texts, mode="absent")
                before = dict(r.state.__dict__)
                self.assertEqual(r.lookups, 0, "no availability was invented")
                self.assertIsNone(r.state.active_requested_date)
                self.assertEqual(before, dict(r.state.__dict__))

    def test_g35a_15_a_weekday_inside_the_hours_question_is_not_competing(self):
        """"¿cuándo laburan los sábados?" names a day and asks nothing about the calendar.

        Reading the parser over the whole burst would have withheld the answer to the very
        question asked. Competing evidence is scoped to the units that are not the question.
        """
        for text in ("cuando laburan los sabados?", "¿qué horario tienen los lunes?",
                     "cual es el horario de atencion los sabados?"):
            with self.subTest(text=text[:30]):
                r = outcome_for([text], mode="absent")
                self.assertFalse(r.burst.has_deterministic_scheduling)
                self.assertEqual(r.decision.hours_disposition, ADMIT)
                self.assertEqual(r.topics, ("business_hours",))

    def test_g35a_16_a_bare_hours_question_keeps_its_bounded_fallback(self):
        for mode, status in self.DEGRADED:
            with self.subTest(semantic=status):
                r = outcome_for(["¿Cuál es el horario de atención?"], mode=mode)
                self.assertEqual(r.decision.hours_disposition, ADMIT)
                self.assertEqual(r.topics, ("business_hours",))

    def test_g35a_17_a_weak_candidate_still_clarifies(self):
        """The competing rule displaces an ADMIT; it does not re-decide the WEAK case."""
        for texts in ([HOURS_Q, AVAIL_M], [HOURS_Q, "Libres para el viernes."],
                      ["Que horarios tienen", "disponiblees"]):
            with self.subTest(burst=texts[-1][:20]):
                r = outcome_for(texts, mode="absent")
                self.assertEqual(r.decision.hours_disposition, UNCORROBORATED)
                self.assertEqual(r.decision.authority, CLARIFY)
                self.assertEqual(r.topics, ())

    def test_g35a_18_the_outcome_does_not_depend_on_stage(self):
        for stage in (STAGE_QUALIFYING, STAGE_QUOTED, STAGE_SCHEDULING, None):
            with self.subTest(stage=stage):
                r = outcome_for([HOURS_Q, "¿Hay algo mañana?"], mode="absent", stage=stage)
                self.assertEqual(r.decision.hours_disposition, BY_DETERMINISTIC)
                self.assertEqual(r.topics, ())

    def test_g35a_19_prior_state_neither_promotes_nor_suppresses(self):
        """An outstanding offer or an accepted quote is not a proposition of THIS burst."""
        contexts = (
            ("outstanding offer", {"last_offered_slots": "2026-09-29T14:00",
                                   "active_requested_date": "2026-09-29"}),
            ("accepted quote", {"last_stage": STAGE_QUOTED}),
        )
        for label, extra in contexts:
            with self.subTest(context=label):
                dated = outcome_for([HOURS_Q, "¿Hay algo mañana?"], mode="absent",
                                    state_extra=extra)
                bare = outcome_for(["¿Cuál es el horario de atención?"], mode="absent",
                                   state_extra=extra)
                self.assertEqual(dated.decision.hours_disposition, BY_DETERMINISTIC)
                self.assertEqual(bare.decision.hours_disposition, ADMIT,
                                 "prior state must not withhold a legitimate answer")

    def test_g35a_20_no_hours_candidate_is_still_no_candidate(self):
        r = outcome_for(["¿Hay algo mañana?"], mode="absent")
        self.assertEqual(r.decision.hours_disposition, NO_CANDIDATE)
        self.assertEqual(r.topics, ())


# ══ D — the R2B behaviours this milestone must not regress ══════════════════

class D_SemanticPrimacyPreserved(unittest.TestCase):

    def test_g35a_21_a_resolved_enquiry_suppresses_at_every_strength(self):
        cases = ((["¿Hay algo para el lunes?"], "NONE"),
                 (["Que horarios tienen disponiblees"], "WEAK"),
                 ([HOURS_Q, "¿Hay algo mañana?"], "STRONG"),
                 (["¿Cuál es el horario de atención?"], "STRONG"))
        for texts, strength in cases:
            with self.subTest(floor=strength, burst=texts[-1][:22]):
                r = outcome_for(texts, availability=CONFIRMED)
                self.assertEqual(r.decision.floor, strength)
                self.assertEqual(r.decision.hours_disposition, BY_AVAILABILITY)
                self.assertEqual(r.decision.authority, LOOK_UP)
                self.assertEqual(r.topics, ())

    def test_g35a_22_semantic_hours_outrank_competing_deterministic_evidence(self):
        """The interpretation confirming the hours question is the top of the ladder."""
        r = outcome_for([HOURS_Q, "¿Hay algo mañana?"], faq=("business_hours",))
        self.assertEqual(r.decision.hours_disposition, ADMIT)
        self.assertEqual(r.topics, ("business_hours",))

    def test_g35a_23_two_confirmed_propositions_are_both_preserved(self):
        r = outcome_for(["¿Cuáles son sus horarios de atención?", "¿Y tienen un turno mañana?"],
                        availability=CONFIRMED, faq=("business_hours",))
        self.assertEqual(r.decision.hours_disposition, ADMIT)
        self.assertEqual(r.decision.authority, LOOK_UP)
        self.assertEqual(r.topics, ("business_hours",))

    def test_g35a_24_an_ambiguous_reading_still_suppresses_on_its_own_grounds(self):
        r = outcome_for([HOURS_Q, "¿Hay algo mañana?"],
                        availability=te.EvidenceStatus.AMBIGUOUS)
        self.assertEqual(r.decision.hours_disposition, AMBIGUOUS)
        self.assertEqual(r.decision.authority, CLARIFY)
        self.assertEqual(r.topics, ())

    def test_g35a_25_one_interpretation_per_turn_accessor_reads_may_differ(self):
        """G3-5B: the invariant is one INTERPRETATION, not one accessor call.

        The real handler reads the memoised interpretation more than once; that is not a
        second interpretation and must not be reported as one.
        """
        turn = LiveTurn([HOURS_Q, "¿Hay algo mañana?"],
                        unanswered=[HOURS_Q, "¿Hay algo mañana?"], mode="absent").run()
        try:
            self.assertEqual(getattr(turn.engine, "_turn_semantic_calls", 0), 0,
                             "the provider was controlled, so no model call was made")
            self.assertGreaterEqual(getattr(turn.engine, "_semantic_accessor_reads", 0), 1)
        finally:
            turn.close()

    def test_g35a_26_every_disposition_is_reachable_and_named(self):
        from app.schemas.hybrid_trace import HoursDisposition
        reached = {
            outcome_for(["¿Horarios?"], faq=("business_hours",)).decision.hours_disposition,
            outcome_for(["¿Cuál es el horario de atención?"], mode="absent").decision.hours_disposition,
            outcome_for([HOURS_Q], availability=CONFIRMED).decision.hours_disposition,
            outcome_for([HOURS_Q], availability=te.EvidenceStatus.AMBIGUOUS).decision.hours_disposition,
            outcome_for(["Que horarios tienen disponiblees"], mode="absent").decision.hours_disposition,
            outcome_for([HOURS_Q, "¿Hay algo mañana?"], mode="absent").decision.hours_disposition,
            outcome_for(["gracias, muy amables"], mode="absent").decision.hours_disposition,
        }
        self.assertEqual(reached, set(HoursDisposition.ALL))


# ══ E — the trace, and the freeze ═══════════════════════════════════════════

class E_TraceAndFreeze(unittest.TestCase):

    def test_g35a_27_one_decision_names_the_burst_it_was_taken_on(self):
        r = outcome_for([HOURS_Q, "¿Hay algo mañana?"], mode="absent")
        rows = {t.get("rule_id"): t for t in r.traces}
        authority = rows["scheduling.availability_admission"]
        faq = rows["faq.topic_authority"]
        for fragment in (f"burst={r.burst.fingerprint[:12]}", "burst_messages=2",
                         "burst_source=event_evidence", "burst_divergence=False",
                         f"hours_disposition={BY_DETERMINISTIC}",
                         "deterministic_scheduling=day="):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, authority["business_outcome"])
        self.assertIn(f"burst={r.burst.fingerprint[:12]}", faq["business_outcome"])
        self.assertEqual(authority["rule_version"], "v2")
        self.assertEqual(authority["authority_result"], HOLD)
        self.assertEqual(authority["canonical_effect"], "NONE")

    def test_g35a_28_no_vocabulary_was_added(self):
        from app.services import scheduling_lexicon as lex
        from app.services.conversation_engine import _HOURS_FAQ_DETECTION
        self.assertEqual(set(_HOURS_FAQ_DETECTION), {
            "horarios", "horario", "en que horarios", "que horarios",
            "cuando laburan", "cuando trabajan", "a que hora", "horario de atencion"})
        self.assertEqual(lex.OPERATION_PREDICATES, frozenset({
            "hacen", "hacian", "tienen", "trabajan", "laburan", "atienden",
            "abren", "cierran", "atencion"}))
        self.assertEqual(lex.AVAILABILITY_OBJECTS, frozenset({
            "disponible", "disponibles", "disponibilidad", "libre", "libres",
            "vacante", "vacantes", "cupo", "cupos"}))

    def test_g35a_29_the_model_and_contracts_are_frozen(self):
        interp = (ROOT / "backend/app/services/semantic_interpreter.py").read_text(
            encoding="utf-8")
        self.assertIn('or "gpt-4o-mini"', interp)
        self.assertIn('PROMPT_VERSION = "understand/1.19"', interp)
        self.assertEqual(te.SCHEMA_VERSION, "turn-evidence/1.3")

    def test_g35a_30_the_business_calendar_is_used_for_the_parse(self):
        """The competing-evidence parse resolves dates on the business calendar."""
        import inspect
        from app.services import conversation_engine as ce
        body = inspect.getsource(ce._canonical_burst)
        self.assertIn("_business_today()", body)
        self.assertNotIn("date.today()", body)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
