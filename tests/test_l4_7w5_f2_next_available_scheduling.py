"""L4.7W5-F2 — the customer may hand us the date, and we must be able to answer.

In the complete Wild the customer said "decime vos cuando pueden", then "que sea lo antes
posible porque me lo venden". The system asked them to name another day. Twice. Berazategui
had nothing on Thursday or Friday and two free slots on Saturday — one query away.

Every scheduling handler required a customer-named day, so a delegated choice had nothing
to land on, and because the Booking Flow only dispatches once a NAMED day has slots, the
Flow could never open. That is the whole defect.

NEXT-01..15   delegated/earliest intent and bounded forward search
HANDOFF-01..15 escalation when the earliest option is not good enough
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
import types
import unittest
from datetime import date, time, timedelta
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
for _m in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    sys.modules.setdefault(_m, types.ModuleType(_m))

import sqlalchemy as _sa
import sqlalchemy.dialects.postgresql as _pg
import sqlalchemy.dialects.postgresql.json as _pgj
_pg.JSONB = _sa.JSON
_pgj.JSONB = _sa.JSON

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import app.models
from app.models import Lead, Revision, WhatsAppContact, WhatsAppThread, WhatsAppThreadState
from app.schemas.schedule import ScheduleSlotsOut
from app.services.conversation_engine import ConversationEngine
from app.services.schedule import (NEXT_AVAILABLE_HORIZON_DAYS, NextAvailable,
                                   ScheduleService, business_hours_for_weekday)

CE_SOURCE = (ROOT / "backend" / "app" / "services"
             / "conversation_engine.py").read_text(encoding="utf-8-sig")

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})


@event.listens_for(_engine, "connect")
def _pragmas(conn, _rec):
    conn.execute("PRAGMA foreign_keys=OFF")


app.models.Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

# The exact Wild state: Berazategui, Thu/Fri empty, Saturday free.
THU, FRI, SAT = date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 12)
WILD_AVAILABILITY = {THU: [], FRI: [], SAT: ["13:30", "14:00"]}


def _ce() -> ConversationEngine:
    eng = ConversationEngine.__new__(ConversationEngine)
    eng._answer_source = None
    eng._availability_checked = False
    eng._turn_ctx = None
    return eng


def _stub_schedule(availability: dict) -> ScheduleService:
    """A ScheduleService whose list_slots answers from a fixture, closed on Sunday."""
    svc = MagicMock(spec=ScheduleService)

    def _list_slots(payload):
        day = payload.preferred_day
        _s, _e, closed = business_hours_for_weekday(day.weekday())
        return ScheduleSlotsOut(preferred_day=day,
                                business_hours="cerrado" if closed else "09:00-18:00",
                                slots=list(availability.get(day, [])))
    svc.list_slots.side_effect = _list_slots
    svc.find_next_available.side_effect = lambda **kw: ScheduleService.find_next_available(
        svc, **kw)
    svc._business_hours.side_effect = lambda d, h: type(
        "H", (), {"start": business_hours_for_weekday(d.weekday())[0],
                  "end": business_hours_for_weekday(d.weekday())[1],
                  "closed": business_hours_for_weekday(d.weekday())[2]})()
    return svc


class TestIntentDetection(unittest.TestCase):

    def test_next_01_to_05_delegated_and_earliest_phrasings(self):
        """NEXT-01..05 — meaning, not a list of sentences."""
        eng = _ce()
        for phrasing in (
            "decime vos cuando pueden",                       # NEXT-01 (the Wild)
            "lo antes posible",                               # NEXT-02
            "el primer turno que haya",                       # NEXT-03
            "cuando tengan",                                  # NEXT-04
            "que sea lo antes posible porque me lo venden",   # NEXT-05 delegated + urgency
            "cuanto antes",
            "¿cuándo tienen algo?",
            "me sirve el primer horario",
            "buscame lo antes posible",
            "elegí vos",
            "cualquier horario me sirve",
        ):
            with self.subTest(phrasing=phrasing):
                self.assertTrue(eng._wants_next_available([phrasing]), phrasing)

    def test_next_13_14_15_named_dates_and_hesitation_do_not_trigger(self):
        """NEXT-13/14/15 — a named day, an exact time and mere hesitation are untouched."""
        eng = _ce()
        for phrasing in ("el viernes", "mañana a las 11", "puede ser el sábado?",
                         "mmm no sé", "déjame ver", "¿cuánto sale?"):
            with self.subTest(phrasing=phrasing):
                self.assertFalse(eng._wants_next_available([phrasing]), phrasing)

    def test_urgency_is_detected_separately_from_delegation(self):
        eng = _ce()
        self.assertTrue(eng._urgency_signalled(["me lo venden"]))
        self.assertTrue(eng._urgency_signalled(["lo necesito urgente"]))
        self.assertFalse(eng._urgency_signalled(["el viernes está bien"]))


class TestForwardSearch(unittest.TestCase):

    def setUp(self):
        self.db = _Session()

    def tearDown(self):
        self.db.close()

    def _search(self, availability, start=THU, horizon=NEXT_AVAILABLE_HORIZON_DAYS):
        svc = _stub_schedule(availability)
        return ScheduleService.find_next_available(
            svc, zone_group="Sur", zone_detail="Berazategui",
            start_day=start, horizon_days=horizon)

    def test_next_06_07_skips_empty_days_and_finds_the_first_with_capacity(self):
        """NEXT-06/07 — the exact Wild case: Thu and Fri empty, Saturday free."""
        found = self._search(WILD_AVAILABILITY)
        self.assertIsNotNone(found)
        self.assertEqual(found.day, SAT)
        self.assertEqual(found.slots, ["13:30", "14:00"])
        self.assertEqual(found.days_checked, 3)
        self.assertEqual([d for d, _ in found.skipped], ["2026-09-10", "2026-09-11"])

    def test_next_08_sunday_is_skipped_as_closed(self):
        """NEXT-08 — Sunday is never offered, and is recorded as closed not empty."""
        found = self._search({date(2026, 9, 14): ["14:30"]}, start=SAT)
        self.assertEqual(found.day, date(2026, 9, 14))
        reasons = dict(found.skipped)
        self.assertEqual(reasons.get("2026-09-13"), "closed")

    def test_next_09_10_travel_and_occupancy_are_the_services_word(self):
        """NEXT-09/10 — the search adds no availability rules of its own: a day with no
        travel-valid or unoccupied slot simply returns none, exactly as list_slots said."""
        found = self._search({THU: [], FRI: [], SAT: []})
        self.assertIsNone(found)

    def test_next_11_earliest_day_returns_all_its_slots_in_order(self):
        found = self._search({THU: [], FRI: ["16:30", "09:00", "12:00"]})
        self.assertEqual(found.day, FRI)
        self.assertEqual(found.slots, ["09:00", "12:00", "16:30"])

    def test_next_12_no_capacity_inside_the_horizon_returns_none(self):
        """NEXT-12 — bounded, never an unbounded scan."""
        found = self._search({}, horizon=14)
        self.assertIsNone(found)

    def test_the_search_is_bounded_and_matches_the_flow_picker_horizon(self):
        from app.services.booking_flow_service import BOOKING_HORIZON_DAYS
        self.assertEqual(NEXT_AVAILABLE_HORIZON_DAYS, 14)
        self.assertEqual(NEXT_AVAILABLE_HORIZON_DAYS, BOOKING_HORIZON_DAYS,
                         "offering a day the Flow cannot display would be a dead end")
        svc = _stub_schedule({})
        ScheduleService.find_next_available(
            svc, zone_group="Sur", zone_detail="Berazategui", start_day=THU)
        self.assertLessEqual(svc.list_slots.call_count, NEXT_AVAILABLE_HORIZON_DAYS)


class TestAuthorityContract(unittest.TestCase):
    """Part 9 — the model detects intent; ScheduleService owns dates."""

    def test_the_handler_never_computes_a_date_itself(self):
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_handle_next_available_request")
        self.assertIn("self._schedule.find_next_available", fn)
        for forbidden in ("timedelta(days=2)", "random", "choice("):
            self.assertNotIn(forbidden, fn)
        # The offered day is ALWAYS the one the service returned — never computed here.
        # date.today() appears only as the search lower bound and for human formatting;
        # neither is a date offered to the customer.
        self.assertIn("day_iso=found.day.isoformat()", fn)
        self.assertIn("slots=list(found.slots)", fn)
        self.assertNotIn("found.day +", fn)
        self.assertNotIn("found.day -", fn)

    def test_it_returns_none_for_a_non_delegated_turn(self):
        """The named-day path must remain completely untouched."""
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_handle_next_available_request")
        self.assertIn("if not self._wants_next_available(texts):", fn)
        self.assertIn("return None", fn)

    def test_no_booking_is_created_by_searching(self):
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_handle_next_available_request")
        self.assertNotIn('status="booked"', fn.replace("'", '"'))
        self.assertNotIn("ThreadRevision(", fn)

    def test_flow_first_is_used_rather_than_a_prose_slot_dump(self):
        """Part 6 — Flow-first; prose only if the Flow could not open."""
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_handle_next_available_request")
        i_flow = fn.index("_dispatch_booking_flow_for_day")
        i_prose = fn.index("Tengo estos horarios")
        self.assertLess(i_flow, i_prose, "the Flow must be attempted before prose")
        self.assertIn("if flow_out is not None", fn)


class TestWildRegression(unittest.TestCase):
    """Part 7 — the exact failing turns, wired through the real handler."""

    def setUp(self):
        self.db = _Session()
        self.eng = _ce()
        self.eng.db = self.db
        self.eng._schedule = _stub_schedule(WILD_AVAILABILITY)
        self.eng._decision_log = lambda *a, **k: None
        self.eng._dispatch_booking_flow_for_day = MagicMock(return_value="FLOW_SENT")
        self.eng._handle_scheduling_escalation = MagicMock(return_value="ESCALATED")
        self.eng._send_text_to_wa = MagicMock(return_value="wamid.X")
        self.eng._get_active_inspection_location = lambda ctx, st: ("Sur", "Berazategui")
        self.state = WhatsAppThreadState(thread_id=1)
        self.ctx = MagicMock()
        self.ctx.thread.id = 1

    def tearDown(self):
        self.db.close()

    def _run(self, text):
        with patch("app.services.conversation_engine.date") as d:
            d.today.return_value = date(2026, 9, 9)
            d.fromisoformat = date.fromisoformat
            return self.eng._handle_next_available_request(self.ctx, self.state, [text])

    def test_decime_vos_cuando_pueden_searches_forward_and_opens_the_flow(self):
        out = self._run("decime vos cuando pueden")
        self.assertEqual(out, "FLOW_SENT")
        self.eng._dispatch_booking_flow_for_day.assert_called_once()
        kwargs = self.eng._dispatch_booking_flow_for_day.call_args.kwargs
        self.assertEqual(kwargs["day_iso"], "2026-09-12")
        self.assertEqual(kwargs["slots"], ["13:30", "14:00"])

    def test_it_never_asks_the_customer_to_name_another_day(self):
        self._run("decime vos cuando pueden")
        self.eng._send_text_to_wa.assert_not_called()

    def test_urgency_reaches_the_same_earliest_slot_without_acceleration(self):
        out = self._run("que sea lo antes posible porque me lo venden")
        self.assertEqual(out, "FLOW_SENT")
        kwargs = self.eng._dispatch_booking_flow_for_day.call_args.kwargs
        self.assertEqual(kwargs["day_iso"], "2026-09-12",
                         "urgency must not surface a day the scheduler rejected")

    def test_the_offered_day_is_recorded_on_state(self):
        self._run("decime vos cuando pueden")
        self.assertEqual(self.state.active_requested_date, "2026-09-12")
        self.assertIn("13:30", self.state.last_offered_slots)

    def test_handoff_05_no_capacity_in_horizon_escalates_to_a_human(self):
        """HANDOFF-05 / NEXT-12 — not an endless request to pick another day."""
        self.eng._schedule = _stub_schedule({})
        out = self._run("decime vos cuando pueden")
        self.assertEqual(out, "ESCALATED")
        self.eng._handle_scheduling_escalation.assert_called_once()
        self.eng._dispatch_booking_flow_for_day.assert_not_called()


class TestHandoffRouting(unittest.TestCase):
    """HANDOFF-01..04 — preconditions, asserted where the routing decision lives."""

    def setUp(self):
        self.routing = CE_SOURCE[CE_SOURCE.index("# 1a. L4.7W5-F2"):
                                 CE_SOURCE.index("# 2. Period request")]

    def test_handoff_02_03_rejection_of_the_offered_option_escalates(self):
        eng = _ce()
        for phrasing in ("es muy tarde", "necesito antes", "no me sirve",
                         "no hay algo antes?", "eso no me sirve",
                         "mañana ya es tarde", "lo necesito hoy"):
            with self.subTest(phrasing=phrasing):
                self.assertTrue(eng._earliest_option_rejected([phrasing]), phrasing)
        self.assertIn("_handle_scheduling_escalation", self.routing)

    def test_handoff_04_urgency_alone_before_a_search_does_not_escalate(self):
        """HANDOFF-04 — the gate is an offered date, not a mood."""
        self.assertIn("state.active_requested_date", self.routing)
        self.assertIn("and self._earliest_option_rejected", self.routing)
        eng = _ce()
        self.assertFalse(eng._earliest_option_rejected(["lo antes posible"]),
                         "wanting it soon is not rejecting what we offered")

    def test_handoff_01_accepting_the_offered_slot_is_not_a_rejection(self):
        eng = _ce()
        for phrasing in ("dale el sábado", "perfecto", "sí, 13:30 me sirve", "buenísimo"):
            with self.subTest(phrasing=phrasing):
                self.assertFalse(eng._earliest_option_rejected([phrasing]))

    def test_handoff_06_to_12_reuse_the_existing_escalation_mechanism(self):
        """The escalation already persists context, flags human and emails — it is reused,
        not reimplemented, so those guarantees are inherited rather than duplicated."""
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_handle_scheduling_escalation")
        self.assertIn("lead.necesita_humano = True", fn)          # HANDOFF-08
        self.assertIn("state.needs_human = True", fn)             # HANDOFF-07
        self.assertIn("STAGE_HUMAN", fn)
        self.assertIn("ATENCION_HUMANA", fn)
        self.assertIn("recalculate_revision_if_possible", fn)     # HANDOFF-06 price
        self.assertIn("_send_scheduling_handoff_email", fn)       # HANDOFF-10
        self.assertNotIn("_send_flow_button", fn)                 # HANDOFF-11
        self.assertNotIn('status="booked"', fn.replace("'", '"')) # HANDOFF-12

    def test_handoff_13_14_the_message_promises_nothing(self):
        """HANDOFF-13/14 — a human will look; no earlier slot is implied."""
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_handle_scheduling_escalation")
        self.assertIn("Julián", fn)
        for promise in ("te consigo", "hay lugar", "podemos hacerlo antes"):
            self.assertNotIn(promise, fn)


class TestNoRegression(unittest.TestCase):

    def test_named_day_handler_still_answers_a_plain_day_request(self):
        """A day with slots must still behave exactly as before."""
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                  if isinstance(n, ast.FunctionDef) and n.name == "_handle_day_only_request")
        self.assertIn("_dispatch_booking_flow_for_day", fn)
        self.assertIn("no hay horarios libres en este momento", fn)
        # forward search only on an EMPTY day AND explicit delegation
        self.assertIn("if texts and self._wants_next_available(texts):", fn)

    def test_burst_message_count_counts_the_opening_burst(self):
        """Part 12 — observability only; a first-turn burst read 1 for 3 messages."""
        self.assertIn("max(1, len(_current_evidence))", CE_SOURCE)

    def test_the_zone_capacity_prep_gate_exists(self):
        """Part 11 — scarcity must be visible before a Wild, not found inside one."""
        script = ROOT / "scripts" / "verify_agenda_capacity.sh"
        self.assertTrue(script.exists())
        self.assertTrue(script.stat().st_mode & 0o111)
        body = script.read_text(encoding="utf-8")
        self.assertIn("ZONE SCARCITY", body)
        self.assertIn("Berazategui", body)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
