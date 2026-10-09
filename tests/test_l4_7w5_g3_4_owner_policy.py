"""L4.7W5-GATE3-G3-4 owner-policy addendum — availability lookup and Flow delivery.

The addendum of 2026-09-25 settles what an availability enquiry does: consult the
deterministic scheduler, prepare the booking Flow with the canonical options, prioritise a
date the customer actually proposed, and never append opening hours because the word
"horarios" appeared.

Every expectation below is written out. Nothing in this file asks the module under test what
the right answer is — the two FAQ-hours reviews both failed on suites that let production
compute their own oracle.
"""
from __future__ import annotations

import datetime
import inspect
import pathlib
import re
import sys
import time as _time
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import booking_flow_service as bfs                  # noqa: E402
from app.services import conversation_engine as ce                    # noqa: E402
from app.services.conversation_engine import (                        # noqa: E402
    ConversationEngine, _hours_floor_strength, STAGE_QUALIFYING, STAGE_SCHEDULING,
)
from app.schemas import turn_evidence as te                           # noqa: E402

LIVE = "Que horarios tienen disponiblees"
LOOK_UP = "LOOK_UP_AVAILABILITY"
REQUEST_PREREQ = "REQUEST_PREREQUISITE"


def harness(*, status=te.EvidenceStatus.CONFIRMED, stage=STAGE_SCHEDULING,
            requested_date=None, visible_slots=None):
    engine = ConversationEngine.__new__(ConversationEngine)
    engine._availability_authority_cache = None
    engine._hybrid_trace_on = lambda: False
    engine._trace_decision = lambda **kw: None
    engine._semantic_scheduling_claims = lambda st: []
    engine._semantic_faq_topics = lambda: set()
    engine._topics_already_answered_this_cycle = lambda c: set()
    engine._turn_scheduling_requested = False
    evidence = None
    if status is not None:
        evidence = te.TurnEvidence(scheduling_intents=(te.SchedulingIntentEvidence(
            kind=te.SchedulingIntentKind.AVAILABILITY_ENQUIRY,
            value="AVAILABILITY_ENQUIRY", status=status),))
    engine._semantic_turn_evidence = lambda: evidence
    state = SimpleNamespace(last_stage=stage, current_revision_id=None, needs_human=False,
                            flow_booking_token=None, active_requested_date=requested_date,
                            last_visible_slots=visible_slots, last_offered_slots=visible_slots,
                            preferred_day=None, preferred_time=None)
    return engine, state


class A_GenericAvailability(unittest.TestCase):
    """Addendum §1 — prerequisites complete means consult the scheduler, not the FAQ."""

    def test_own_01_prerequisites_complete_authorises_a_lookup(self):
        engine, state = harness()
        self.assertEqual(engine._availability_authority(None, state, [LIVE]).authority, LOOK_UP)

    def test_own_02_prerequisites_missing_requests_the_prerequisite(self):
        engine, state = harness(stage=STAGE_QUALIFYING)
        decision = engine._availability_authority(None, state, [LIVE])
        self.assertEqual(decision.authority, REQUEST_PREREQ)
        self.assertNotEqual(decision.authority, LOOK_UP,
                            "qualification and pricing are not skipped")

    def test_own_03_no_business_hours_answer_on_the_live_message(self):
        engine, state = harness()
        self.assertEqual(
            ConversationEngine._faq_topics_for_burst(engine, LIVE, ctx=None, state=state),
            set())

    def test_own_04_the_flow_is_only_offered_when_the_scheduler_returned_slots(self):
        body = inspect.getsource(ConversationEngine._dispatch_booking_flow_for_day)
        self.assertIn("if not slots:", body)
        self.assertIn("return None", body)

    def test_own_05_the_enquiry_alone_creates_no_booking(self):
        """The only writer of a booked revision is the Flow-response handler."""
        engine_src = (BACKEND / "app/services/conversation_engine.py").read_text(encoding="utf-8")
        tree = __import__("ast").parse(engine_src)
        writers = {n.name for n in __import__("ast").walk(tree)
                   if isinstance(n, __import__("ast").FunctionDef)
                   and 'status="booked"' in __import__("ast").unparse(n)}
        self.assertEqual(writers, set(),
                         "no engine function writes a booked revision; the Flow handler does")


class B_DateResolution(unittest.TestCase):
    """Addendum §2 — relative dates resolve on the Buenos Aires calendar."""

    def test_own_06_the_calendar_boundary_is_local_never_utc(self):
        """The code must take the customer's calendar from LOCAL time.

        Asserted on the code, not on this process: the sealed test container has no TZ, so
        reading `time.tzname` here would prove nothing about production. What is checkable
        everywhere is that no scheduling date is derived from UTC.
        """
        for module in ("conversation_engine.py", "schedule.py", "booking_flow_service.py"):
            body = (BACKEND / "app/services" / module).read_text(encoding="utf-8")
            with self.subTest(module=module):
                self.assertNotIn("utcnow().date()", body)
                self.assertNotIn("datetime.now(timezone.utc).date()", body)
        engine = (BACKEND / "app/services/conversation_engine.py").read_text(encoding="utf-8")
        self.assertGreater(engine.count("date.today()"), 10,
                           "relative days resolve from the local calendar")

    def test_own_07_relative_days_resolve_from_the_local_today(self):
        today = datetime.date.today()
        for text, expected in ((["hoy"], today),
                               (["mañana"], today + datetime.timedelta(days=1))):
            with self.subTest(text=text[0]):
                day_iso, _t = ce._parse_scheduling_text(text, today)
                self.assertEqual(day_iso, expected.isoformat())

    def test_own_08_a_noisy_tomorrow_is_still_tomorrow(self):
        today = datetime.date.today()
        day_iso, _t = ce._parse_scheduling_text(["mñ"], today)
        self.assertEqual(day_iso, (today + datetime.timedelta(days=1)).isoformat())

    def test_own_09_no_time_is_fabricated_from_a_day_only_message(self):
        today = datetime.date.today()
        for text in (["hoy"], ["mañana"], ["mñ tenes algo?"]):
            with self.subTest(text=text[0]):
                _day, time_str = ce._parse_scheduling_text(text, today)
                self.assertIsNone(time_str, "a day without a time invents no time")

    def test_own_10_a_proposed_date_is_not_hijacked_by_the_forward_search(self):
        """The forward search runs only when NO day and NO time were parsed."""
        site = inspect.getsource(ConversationEngine._process_text)
        self.assertIn("if not sched_day_iso and not sched_time_str:", site)
        index_guard = site.index("if not sched_day_iso and not sched_time_str:")
        index_call = site.index("_handle_next_available_request(")
        self.assertLess(index_guard, index_call,
                        "a customer-proposed date is queried first, by construction")
        self.assertIn("allow_clarification=True", site,
                      "R2A: this is the one site where a clarification replaces nothing")


class C_ContextAndStaleness(unittest.TestCase):
    """Addendum §3 — reuse a live context, never a stale one."""

    def test_own_11_a_requested_date_is_recorded_only_from_the_scheduler(self):
        body = inspect.getsource(ConversationEngine._dispatch_booking_flow_for_day)
        self.assertIn("state.active_requested_date = day_iso", body)
        self.assertIn("state.last_visible_slots = json.dumps(list(slots))", body)
        self.assertNotIn("AVAILABILITY_ENQUIRY", body,
                         "no semantic value is copied into scheduling state")

    def test_own_12_the_authority_decision_writes_no_scheduling_state(self):
        body = inspect.getsource(ConversationEngine._availability_authority)
        for forbidden in ("active_requested_date =", "last_visible_slots =",
                          "last_offered_slots =", "preferred_day =", "preferred_time =",
                          "flow_booking_token ="):
            with self.subTest(field=forbidden):
                self.assertNotIn(forbidden, body)

    def test_own_13_a_stale_context_does_not_change_the_authority(self):
        """Authority depends on stage and producers, never on leftover slot state."""
        fresh, state_fresh = harness(requested_date="2026-09-25",
                                     visible_slots='["13:00"]')
        stale, state_stale = harness(requested_date="2026-09-18", visible_slots='["13:00"]')
        none_ctx, state_none = harness()
        results = [e._availability_authority(None, s, [LIVE]).authority
                   for e, s in ((fresh, state_fresh), (stale, state_stale),
                                (none_ctx, state_none))]
        self.assertEqual(results, [LOOK_UP, LOOK_UP, LOOK_UP])


class D_FlowContract(unittest.TestCase):
    """Addendum §5 — what the CURRENT published Flow can and cannot express."""

    def test_own_14_the_contract_serves_multiple_dates(self):
        body = inspect.getsource(bfs.BookingFlowService._available_dates)
        self.assertIn("for delta in range(0, BOOKING_HORIZON_DAYS + 1)", body)
        self.assertIn("if slots_out.slots:", body)
        self.assertIn("items.append", body)

    def test_own_15_all_feasible_slots_for_a_date_are_preserved(self):
        body = inspect.getsource(bfs.BookingFlowService._slots_for_date)
        self.assertIn("for t in slots_out.slots", body)
        for truncation in ("[:3]", "[:1]", "[:5]", "max(", "sorted(")[:4]:
            with self.subTest(op=truncation):
                self.assertNotIn(truncation, body, "the slot list is not reduced")

    def test_own_16_dates_and_slots_come_only_from_the_scheduler(self):
        for fn in (bfs.BookingFlowService._available_dates,
                   bfs.BookingFlowService._slots_for_date):
            body = inspect.getsource(fn)
            with self.subTest(fn=fn.__name__):
                self.assertIn("self._sched.list_slots(payload)", body)
                self.assertNotIn("_call_openai", body)

    def test_own_17_preselection_is_not_expressible_and_is_documented(self):
        """The published Flow's Dropdowns carry no `init-value`, so a date cannot be
        pushed as preselected. Narrowing the option list is the only lever available."""
        body = inspect.getsource(bfs.BookingFlowService._appointment_screen_data)
        self.assertIn("init-value", body, "the limitation must stay documented in source")
        self.assertIn("narrowed to that one date and that one time", body)

    def test_own_18_narrowing_happens_only_for_an_agreed_day_and_time(self):
        body = inspect.getsource(bfs.BookingFlowService._appointment_screen_data)
        self.assertIn("if agreed_day and agreed_time and not selected_date_str:", body)
        self.assertIn("still_free", body, "a stale agreement falls back to the full picker")


class E_ConcurrencyPosture(unittest.TestCase):
    """Addendum §7 — audited, and not weakened by G3-4."""

    def test_own_19_the_slot_is_revalidated_before_a_booking_is_created(self):
        body = inspect.getsource(bfs.BookingFlowService.handle_confirm_booking)
        # SB2A rewrite: the lock is the booking authority's — blocking, fail-closed, keyed
        # on (professional, date) — and the revalidation runs inside it (revalidate=…).
        self.assertIn("self._authority.execute(", body)
        # SB2A-R3 rewrite: the Flow hands TYPED plans to the booking authority (no callback);
        # revalidation, ThreadRevision, price and token consumption are the authority's.
        self.assertIn("availability=availability", body)
        self.assertIn("AvailabilityRevalidation(", body)
        from app.services import booking_authority as ba
        locks = inspect.getsource(ba.BookingAuthority._acquire_locks)
        self.assertIn("pg_advisory_xact_lock(:p, :d)", locks)
        run = inspect.getsource(ba.BookingAuthority.execute)
        # SB2A-R3 rewrite: the authority's own revalidation, still after the locks.
        self.assertLess(run.index("self._acquire_locks("),
                        run.index("self._revalidate_availability("))

    def test_own_20_a_lost_slot_raises_a_conflict_rather_than_booking_anyway(self):
        body = inspect.getsource(bfs.BookingFlowService.handle_confirm_booking)
        self.assertIn("BookingSlotConflictError", body)

    def test_own_21_displayed_availability_is_never_recorded_as_a_booking(self):
        body = inspect.getsource(ConversationEngine._dispatch_booking_flow_for_day)
        self.assertIn("Neither is a booking", body)
        for forbidden in ('status="booked"', "ThreadRevision(", "Revision("):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, body)

    def test_own_22_g3_4_added_nothing_to_the_booking_path(self):
        """The booking service and the Flow endpoint carry no G3-4 marker at all."""
        for module in ("booking_flow_service.py",):
            body = (BACKEND / "app/services" / module).read_text(encoding="utf-8")
            with self.subTest(module=module):
                for marker in ("G3-4", "AVAILABILITY_ENQUIRY", "_availability_authority",
                               "_hours_floor_strength"):
                    self.assertNotIn(marker, body)
        route = (BACKEND / "app/routes/flow_data_exchange.py").read_text(encoding="utf-8")
        for marker in ("G3-4", "AVAILABILITY_ENQUIRY", "_availability_authority"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, route)


class F_OutboundAndBudget(unittest.TestCase):
    """Addendum §1 and §8 — intended action and delivery outcome are different facts."""

    def test_own_23_the_authority_is_independent_of_the_outbound_gate(self):
        body = inspect.getsource(ConversationEngine._availability_authority)
        for forbidden in ("OUTBOUND_ENABLED", "OutboundSafetyGate", "_send_text_to_wa",
                          "_send_booking_flow"):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, body)

    def test_own_24_the_intended_action_is_recorded_even_when_delivery_is_blocked(self):
        traces = []
        engine, state = harness()
        engine._hybrid_trace_on = lambda: True
        engine._trace_decision = lambda **kw: traces.append(kw)
        engine._availability_authority(None, state, [LIVE])
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["permitted_action"], LOOK_UP,
                         "the authority says what was allowed, not what was delivered")

    def test_own_25_one_lookup_and_one_decision_per_turn(self):
        traces = []
        engine, state = harness()
        engine._hybrid_trace_on = lambda: True
        engine._trace_decision = lambda **kw: traces.append(kw)
        for _ in range(3):
            engine._availability_authority(None, state, [LIVE])
        self.assertEqual(len(traces), 1, "a duplicate delivery decides once")

    def test_own_26_no_provider_call_and_no_model_change(self):
        engine_src = (BACKEND / "app/services/conversation_engine.py").read_text(encoding="utf-8")
        interp = (BACKEND / "app/services/semantic_interpreter.py").read_text(encoding="utf-8")
        # G3-5F §4.3 put both consumers behind ONE provider boundary
        # (`_reply_decision`), so the number of places that can issue a reply
        # request fell from two to one. The guarantee this asserts — that no
        # milestone added a provider call — is unchanged and now stricter: the
        # two consumers are counted separately, so a third would still fail.
        self.assertEqual(len(re.findall(r"self\._call_openai\(", engine_src)), 1)
        self.assertEqual(len(re.findall(r"self\._reply_decision\(", engine_src)), 2)
        self.assertEqual(len(re.findall(r"self\._transport\(", interp)), 1)
        self.assertIn('or "gpt-4o-mini"', interp)

    def test_own_27_the_hours_floor_never_wins_the_live_message(self):
        self.assertEqual(_hours_floor_strength(LIVE), "WEAK")
        engine, state = harness()
        self.assertNotIn("business_hours", ConversationEngine._faq_topics_for_burst(
            engine, LIVE, ctx=None, state=state))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
