"""L4.3-SCHEDULING-SEMANTICS — remediation of the L4-WILD-A scheduling defects.

Owner decision implemented here: BOOKING_FLOW (Meta Flow 28104222025943520) is the
authoritative booking/confirmation UX. Text conversation stays responsible only for
interpreting intent, checking deterministic availability, explaining rejections and
reaching ONE valid scheduling option.

TEMP-01  Tuesday + "mñ 15hs"                     → Wednesday 15:00
TEMP-02  Tuesday + "mñ 15hs? o jueves que tenes" → primary Wed 15:00, fallback Thu flexible
TEMP-03  a time stays bound to its own clause
TEMP-04  "viernes por la mañana"                 → Friday + morning period (no relative day)
TEMP-05  "mañana"                                → relative tomorrow
TEMP-06  "manana"                                → relative tomorrow
TEMP-07  "mñ"                                    → relative tomorrow
ORDER-01 primary available   → fallback never evaluated
ORDER-02 primary unavailable → fallback evaluated
ORDER-03 reply names the primary rejection before the fallback offer
HOURS-01 FAQ hours derive from the canonical ScheduleService weekday table
HOURS-02 Thursday 15:00 is rejected as outside operating hours (and explained as such)
FLOW-01  valid selected slot → Booking Flow becomes eligible and is sent
FLOW-02  Booking Flow ID == 28104222025943520
FLOW-03  send uses path_id=BOOKING_FLOW
FLOW-04  booking token minted exactly once per dispatch
FLOW-05  invalid/unavailable slot → no Flow
FLOW-06  accepted quote alone → no Flow yet
FLOW-07  BookingFlowService revalidates the slot before confirmation
FLOW-08  legacy text path cannot complete a booking bypassing the Flow
FORENSIC-01 deployment_id populated from GIT_SHA
FORENSIC-02 correlation_id populated and stable across one CE turn
WILD-A   full reproduction of the preserved Wild A scheduling turn
"""
from __future__ import annotations

import types
import unittest
from datetime import date, time
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine as _sa_create_engine
from sqlalchemy.orm import Session

BOOKING_FLOW_ID = "28104222025943520"
WILD_TEXT = "Mñ 15hs? O nose jueves que tenes"
WILD_TODAY = date(2026, 9, 1)          # Tuesday
WILD_PRIMARY = "2026-09-02"            # Wednesday
WILD_FALLBACK = "2026-09-03"           # Thursday


# ── helpers ───────────────────────────────────────────────────────────────────

def _agenda_session() -> Session:
    """Real SQLite session seeded with the crm_test agenda of the Wild A window."""
    import app.models as models
    engine = _sa_create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(engine)
    db = Session(engine)
    lead = models.Lead(id=1, estado="AGENDADO", necesita_humano=False)
    db.add(lead)
    db.flush()
    rows = [
        # Wednesday 2026-09-02
        (26, date(2026, 9, 2), time(9, 0), "CABA"),
        (14, date(2026, 9, 2), time(10, 0), "CABA"),
        (27, date(2026, 9, 2), time(12, 30), "Norte"),
        (28, date(2026, 9, 2), time(15, 0), "Norte"),
        # Thursday 2026-09-03
        (29, date(2026, 9, 3), time(9, 0), "CABA"),
        (16, date(2026, 9, 3), time(10, 0), "Sur"),
        (30, date(2026, 9, 3), time(11, 30), "Sur"),
    ]
    for rid, d, t, zone in rows:
        db.add(models.Revision(
            id=rid, lead_id=1, turno_fecha=d, turno_hora=t,
            zone_group=zone, estado_revision="PENDIENTE",
        ))
    db.commit()
    return db


def _make_state(**kwargs):
    ns = types.SimpleNamespace(
        home_zone_group="Sur", home_zone_detail="Berazategui",
        current_focus_candidate_id=130,
        preferred_day=None, preferred_time=None,
        active_requested_date=None, last_requested_time=None,
        last_offered_slots=None, last_visible_slots=None,
        is_website_lead=False, last_stage="SCHEDULING", needs_human=False,
        flow_booking_token=None, current_revision_id=None, customer_name=None,
        current_cycle_started_at=None, cycle_reset_pending=False,
    )
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _make_candidate():
    return types.SimpleNamespace(
        id=130, thread_id=2036, marca="Peugeot", modelo="2008", anio=2014,
        tipo_vehiculo="SUV_4X4_DEPORTIVO", zone_group="Sur", zone_detail="Berazategui",
        direccion_texto=None, status="current_focus", label=None,
    )


def _make_ctx(state, candidates=None):
    from app.services.conversation_engine import _Context
    ctx = _Context.__new__(_Context)
    ctx.thread = types.SimpleNamespace(id=2036, lead_id=122, contact_id=2043, last_message_at=None)
    ctx.lead = types.SimpleNamespace(
        id=122, nombre=None, apellido=None, email=None, telefono="5491153368330",
        flag="ACEPTADO", estado="CONSULTA_NUEVA", canal=None, necesita_humano=False,
        ref_code=None, rc_code=None,
    )
    ctx.contact = types.SimpleNamespace(wa_id="5491153368330")
    ctx.candidates = list(candidates if candidates is not None else [_make_candidate()])
    ctx.state = state
    ctx.db_messages = []
    ctx.inbound_wa_message_id = "wamid.TEST"
    ctx.previous_processed_cursor = None
    return ctx


def _make_engine(schedule_db: Session | None = None):
    """CE instance with stubbed externals; ScheduleService bound to a real agenda DB."""
    from app.services.conversation_engine import ConversationEngine
    from app.services.schedule import ScheduleService
    import app.models as models

    sqlite = _sa_create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(sqlite)
    db = MagicMock()
    db.bind = sqlite

    settings = MagicMock()
    settings.openai_api_key = "sk-fake"
    settings.whatsapp_flow_id = "1644218879979041"      # legacy data-collection Flow
    settings.whatsapp_website_flow_id = ""
    settings.booking_flow_id = BOOKING_FLOW_ID          # authoritative booking Flow
    eng = ConversationEngine(db=db, settings=settings)
    if schedule_db is not None:
        eng._schedule = ScheduleService(schedule_db)
    eng._correlation_id = "corr-test-0001"
    eng._booking_flow_send_failed = False
    return eng


def _capture_sends(eng):
    """Patch the two CE send helpers; return (texts, flows)."""
    texts: list[str] = []
    flows: list[dict] = []

    def fake_text(ctx, text):
        texts.append(text)
        return "wamid.OUT.TEXT"

    def fake_flow(ctx, body, token, flow_id="", initial_screen="MAIN",
                  path_id=None, cta_label="Completar datos"):
        flows.append({
            "body": body, "token": token, "flow_id": flow_id,
            "screen": initial_screen, "path_id": path_id, "cta": cta_label,
        })
        return "wamid.OUT.FLOW"

    eng._send_text_to_wa = fake_text          # type: ignore[assignment]
    eng._send_flow_button = fake_flow         # type: ignore[assignment]
    return texts, flows


# ── PHASE A — temporal semantics ─────────────────────────────────────────────

class TestTemporalSemantics(unittest.TestCase):

    def _req(self, text, today=WILD_TODAY):
        from app.services.conversation_engine import _parse_scheduling_requests
        return [(r.day_iso, r.time_str) for r in _parse_scheduling_requests([text], today)]

    def test_temp_01_manana_abbrev_with_time(self):
        """TEMP-01 Tuesday + 'mñ 15hs' → Wednesday 2026-09-02 15:00."""
        self.assertEqual(self._req("mñ 15hs"), [(WILD_PRIMARY, "15:00")])

    def test_temp_02_primary_and_fallback(self):
        """TEMP-02 the real Wild burst yields PRIMARY Wednesday 15:00 + FALLBACK Thursday."""
        self.assertEqual(
            self._req(WILD_TEXT),
            [(WILD_PRIMARY, "15:00"), (WILD_FALLBACK, None)],
        )

    def test_temp_02b_weekday_never_suppresses_earlier_relative_day(self):
        """SCHED-A: a later weekday name must not delete an earlier 'mñ' request."""
        self.assertEqual(self._req("mñ 15hs jueves")[0], (WILD_PRIMARY, "15:00"))

    def test_temp_03_time_stays_bound_to_its_clause(self):
        """TEMP-03 each clause keeps its own time; no cross-branch transplant."""
        self.assertEqual(
            self._req("mñ a las 15 o el jueves a las 11"),
            [(WILD_PRIMARY, "15:00"), (WILD_FALLBACK, "11:00")],
        )
        # SCHED-B: the 15:00 of the Wednesday clause must NOT land on Thursday.
        self.assertIsNone(dict(self._req(WILD_TEXT))[WILD_FALLBACK])

    def test_temp_04_viernes_por_la_manana_is_morning_not_tomorrow(self):
        """TEMP-04 'viernes por la mañana' → Friday, morning period, single branch."""
        from app.services.conversation_engine import _detect_time_period
        parsed = self._req("viernes por la mañana")
        self.assertEqual(parsed, [("2026-09-04", None)])
        self.assertEqual(_detect_time_period(["viernes por la mañana"]), "manana")

    def test_temp_05_manana_accented_is_tomorrow(self):
        """TEMP-05 'mañana' → relative tomorrow."""
        self.assertEqual(self._req("mañana 15hs"), [(WILD_PRIMARY, "15:00")])

    def test_temp_06_manana_unaccented_is_tomorrow(self):
        """TEMP-06 'manana' → relative tomorrow."""
        self.assertEqual(self._req("manana 15hs"), [(WILD_PRIMARY, "15:00")])

    def test_temp_07_mn_abbrev_is_tomorrow(self):
        """TEMP-07 'mñ' alone → relative tomorrow."""
        self.assertEqual(self._req("mñ"), [(WILD_PRIMARY, None)])

    def test_legacy_single_branch_parse_is_unchanged(self):
        """Single-branch utterances keep the exact certified legacy result."""
        from app.services.conversation_engine import (
            _parse_scheduling_requests, _parse_scheduling_text,
        )
        for text in ["mañana 12hs", "el lunes a las 10", "sábado a las 9",
                     "me dijiste hasta las 18hs. sábado a las 9 entonces",
                     "viernes por la mañana", "hoy 11hs"]:
            legacy = _parse_scheduling_text([text], WILD_TODAY)
            new = _parse_scheduling_requests([text], WILD_TODAY)
            self.assertEqual(len(new), 1, text)
            self.assertEqual((new[0].day_iso, new[0].time_str), legacy, text)


# ── PHASE A/E — ordered evaluation + rejection semantics ─────────────────────

class TestOrderedEvaluation(unittest.TestCase):

    def setUp(self):
        self.agenda = _agenda_session()
        self.eng = _make_engine(self.agenda)
        self.state = _make_state()
        self.ctx = _make_ctx(self.state)
        self.texts, self.flows = _capture_sends(self.eng)
        self.checked_days: list[str] = []
        real_check = self.eng._schedule.check
        real_slots = self.eng._schedule.list_slots

        def spy_check(payload):
            self.checked_days.append(payload.preferred_day.isoformat())
            return real_check(payload)

        def spy_slots(payload):
            self.checked_days.append(payload.preferred_day.isoformat())
            return real_slots(payload)

        self.eng._schedule.check = spy_check          # type: ignore[assignment]
        self.eng._schedule.list_slots = spy_slots     # type: ignore[assignment]

    def tearDown(self):
        self.agenda.close()

    def _requests(self, text=WILD_TEXT):
        from app.services.conversation_engine import _parse_scheduling_requests
        return _parse_scheduling_requests([text], WILD_TODAY)

    @patch("app.services.conversation_engine.date")
    def _run(self, text, mock_date):
        mock_date.today.return_value = WILD_TODAY
        mock_date.fromisoformat = date.fromisoformat
        return self.eng._handle_ordered_scheduling_requests(
            self.ctx, self.state, self._requests(text)
        )

    def test_order_01_primary_available_fallback_never_evaluated(self):
        """ORDER-01 an available primary short-circuits: the fallback day is never queried."""
        # Wednesday 09:00 is free of overlap for a CABA-adjacent request; use a day/time
        # proven available by the real agenda: Thursday 13:00 as PRIMARY.
        from app.services.conversation_engine import SchedulingRequest
        requests = [
            SchedulingRequest(day_iso=WILD_FALLBACK, time_str="13:00"),
            SchedulingRequest(day_iso="2026-09-04", time_str=None),
        ]
        with patch("app.services.conversation_engine.date") as mock_date, \
             patch("app.services.booking_flow_service.BookingFlowService.resolve_context",
                   return_value=MagicMock()):
            mock_date.today.return_value = WILD_TODAY
            mock_date.fromisoformat = date.fromisoformat
            out = self.eng._handle_ordered_scheduling_requests(self.ctx, self.state, requests)
        self.assertIsNotNone(out)
        self.assertNotIn("2026-09-04", self.checked_days)
        self.assertEqual(len(self.flows), 1)          # primary booked straight into the Flow
        self.assertEqual(self.flows[0]["flow_id"], BOOKING_FLOW_ID)

    def test_order_02_primary_unavailable_fallback_evaluated(self):
        """ORDER-02 a rejected primary triggers evaluation of the fallback day."""
        out = self._run(WILD_TEXT)
        self.assertIsNotNone(out)
        self.assertIn(WILD_PRIMARY, self.checked_days)
        self.assertIn(WILD_FALLBACK, self.checked_days)

    def test_order_03_reply_names_primary_rejection_before_fallback(self):
        """ORDER-03 the reply resolves Wednesday first, then offers Thursday."""
        self._run(WILD_TEXT)
        self.assertEqual(len(self.texts), 1)
        reply = self.texts[0]
        self.assertIn("miércoles 02/09", reply)
        self.assertIn("jueves 03/09", reply)
        self.assertLess(reply.index("miércoles 02/09"), reply.index("jueves 03/09"))
        self.assertIn("no tengo disponibilidad", reply)
        self.assertIn("13:00", reply)

    def test_primary_request_is_never_silently_discarded(self):
        """The customer must see their explicit primary day addressed."""
        self._run(WILD_TEXT)
        reply = self.texts[0]
        self.assertIn("15:00", reply)          # the time they asked for on Wednesday
        self.assertIn("miércoles", reply)

    def test_rejected_primary_leaves_no_bookable_preference(self):
        self._run(WILD_TEXT)
        self.assertIsNone(self.state.preferred_day)
        self.assertIsNone(self.state.preferred_time)
        self.assertEqual(self.state.active_requested_date, WILD_FALLBACK)

    def test_flow_05_unavailable_slot_sends_no_flow(self):
        """FLOW-05 no valid slot established → no Booking Flow."""
        self._run(WILD_TEXT)
        self.assertEqual(self.flows, [])
        self.assertIsNone(self.state.flow_booking_token)


# ── PHASE B — business hours single authority ────────────────────────────────

class TestBusinessHoursAuthority(unittest.TestCase):

    def test_hours_01_faq_answer_derives_from_schedule_service(self):
        """HOURS-01 every weekday in the FAQ answer matches ScheduleService hours."""
        from app.services.conversation_engine import _faq_hours_answer
        from app.services.schedule import business_hours_for_weekday, _format_hour_es
        answer = _faq_hours_answer()
        for weekday in range(6):           # Monday..Saturday
            start, end, closed = business_hours_for_weekday(weekday)
            self.assertFalse(closed)
            phrase = f"de {_format_hour_es(start)} a {_format_hour_es(end)} hs"
            self.assertIn(phrase, answer, f"weekday {weekday} missing from FAQ answer")

    def test_hours_01b_legacy_wrong_answer_is_gone(self):
        """The hard-coded 'lunes a viernes de 9 a 18' claim must no longer exist."""
        from app.services.conversation_engine import _faq_hours_answer
        self.assertNotIn("lunes a viernes de 9 a 18", _faq_hours_answer())

    def test_hours_01c_answer_cannot_diverge_from_scheduler(self):
        """Changing the scheduling table changes the FAQ answer — one authority only."""
        import app.services.schedule as sched
        from app.services.conversation_engine import _faq_hours_answer
        original = dict(sched._WEEKDAY_HOURS)
        try:
            sched._WEEKDAY_HOURS[3] = (time(10, 0), time(12, 0), False)
            self.assertIn("jueves de 10 a 12 hs", _faq_hours_answer())
        finally:
            sched._WEEKDAY_HOURS.clear()
            sched._WEEKDAY_HOURS.update(original)

    def test_hours_02_thursday_1500_outside_operating_hours(self):
        """HOURS-02 Thursday closes at 14:00 → 15:00 is rejected as outside hours."""
        from app.schemas.schedule import ScheduleCheckIn
        from app.services.schedule import ScheduleService
        db = _agenda_session()
        try:
            out = ScheduleService(db).check(ScheduleCheckIn(
                address="Berazategui, Sur, Buenos Aires, Argentina",
                preferred_day=date(2026, 9, 3), preferred_time=time(15, 0),
                zone_group="Sur", zone_detail="Berazategui",
            ))
            self.assertFalse(out.valid)
            self.assertTrue(any("horario operativo" in r for r in out.reasons))
        finally:
            db.close()

    def test_hours_02b_rejection_explains_the_real_reason(self):
        """PHASE E — the customer is told the day's hours, not a generic 'no disponibilidad'."""
        from app.schemas.schedule import ScheduleCheckIn
        from app.services.schedule import ScheduleService
        db = _agenda_session()
        try:
            eng = _make_engine(db)
            out = ScheduleService(db).check(ScheduleCheckIn(
                address="Berazategui, Sur, Buenos Aires, Argentina",
                preferred_day=date(2026, 9, 3), preferred_time=time(15, 0),
                zone_group="Sur", zone_detail="Berazategui",
            ))
            reason = eng._rejection_reason_es(out, "2026-09-03")
            self.assertEqual(reason, "ese día trabajamos de 9 a 14 hs")
        finally:
            db.close()

    def test_rejection_reason_taxonomy(self):
        """PHASE E — occupied and travel rejections carry their own explanation."""
        eng = _make_engine()
        occupied = types.SimpleNamespace(
            reasons=["El horario solicitado se superpone con un turno ya reservado en el CRM"])
        travel = types.SimpleNamespace(
            reasons=["El turno no satisface las restricciones de traslado (origen, turno anterior o siguiente)"])
        sunday = types.SimpleNamespace(reasons=["Domingo: sin operaciones."])
        self.assertEqual(eng._rejection_reason_es(occupied, "2026-09-02"),
                         "ese horario ya está reservado")
        self.assertEqual(eng._rejection_reason_es(travel, "2026-09-02"),
                         "no llegamos a tiempo desde el turno anterior")
        self.assertEqual(eng._rejection_reason_es(sunday, "2026-09-06"),
                         "los domingos no trabajamos")


# ── PHASE C — Booking Flow wiring ────────────────────────────────────────────

class TestBookingFlowWiring(unittest.TestCase):

    def setUp(self):
        self.agenda = _agenda_session()
        self.eng = _make_engine(self.agenda)
        self.state = _make_state()
        self.ctx = _make_ctx(self.state)
        self.texts, self.flows = _capture_sends(self.eng)

    def tearDown(self):
        self.agenda.close()

    def _book(self, day=WILD_FALLBACK, hhmm="13:00"):
        with patch("app.services.conversation_engine.date") as mock_date, \
             patch("app.services.booking_flow_service.BookingFlowService.resolve_context") as ctxmock:
            mock_date.today.return_value = WILD_TODAY
            mock_date.fromisoformat = date.fromisoformat
            ctxmock.return_value = MagicMock()
            return self.eng._try_schedule_and_flow(self.ctx, self.state, day, hhmm, "")

    def test_flow_01_valid_slot_makes_booking_flow_eligible(self):
        """FLOW-01 an available slot dispatches the Booking Flow."""
        out = self._book()
        self.assertIsNotNone(out)
        self.assertEqual(out.action, "flow_button_sent")
        self.assertEqual(len(self.flows), 1)

    def test_flow_02_uses_published_booking_flow_id(self):
        """FLOW-02 the dispatched Flow is the published RideCheck Booking Flow."""
        self._book()
        self.assertEqual(self.flows[0]["flow_id"], BOOKING_FLOW_ID)
        self.assertEqual(self.flows[0]["screen"], "APPOINTMENT")

    def test_flow_02b_settings_default_is_the_published_flow(self):
        """FLOW-02 the runtime default resolves to 28104222025943520."""
        import os
        from app.settings import get_settings
        previous = os.environ.pop("WHATSAPP_BOOKING_FLOW_ID", None)
        try:
            self.assertEqual(get_settings().booking_flow_id, BOOKING_FLOW_ID)
        finally:
            if previous is not None:
                os.environ["WHATSAPP_BOOKING_FLOW_ID"] = previous

    def test_flow_03_send_uses_booking_flow_path_id(self):
        """FLOW-03 outbound attribution is path_id=BOOKING_FLOW."""
        from app.services.outbound_path_registry import OutboundPathId
        self._book()
        self.assertEqual(self.flows[0]["path_id"], OutboundPathId.BOOKING_FLOW.value)
        self.assertEqual(self.flows[0]["path_id"], "BOOKING_FLOW")

    def test_flow_03b_gate_receives_booking_flow_path(self):
        """FLOW-03 the real _send_flow_button hands BOOKING_FLOW to the safety gate."""
        from app.services.conversation_engine import GateOutcome
        eng = _make_engine(self.agenda)
        state = _make_state()
        ctx = _make_ctx(state)
        gate = MagicMock()
        gate.attempt.return_value = types.SimpleNamespace(outcome=GateOutcome.ALLOWED, message_id=7)
        with patch("app.services.conversation_engine.OutboundSafetyGate", return_value=gate), \
             patch("app.services.conversation_engine._send_whatsapp_cloud_flow",
                   return_value=("wamid.FLOW", {})), \
             patch("app.services.booking_flow_service.BookingFlowService.resolve_context",
                   return_value=MagicMock()), \
             patch("app.services.conversation_engine.date") as mock_date:
            mock_date.today.return_value = WILD_TODAY
            mock_date.fromisoformat = date.fromisoformat
            eng._try_schedule_and_flow(ctx, state, WILD_FALLBACK, "13:00", "")
        kwargs = gate.attempt.call_args.kwargs
        self.assertEqual(kwargs["path_id"], "BOOKING_FLOW")
        self.assertEqual(kwargs["message_type"], "flow")

    def test_flow_04_booking_token_minted_exactly_once(self):
        """FLOW-04 one dispatch → exactly one token, stored on the thread state."""
        with patch("app.services.booking_flow_service.make_booking_token",
                   wraps=__import__("app.services.booking_flow_service", fromlist=["x"]).make_booking_token) as spy, \
             patch("app.services.booking_flow_service.BookingFlowService.resolve_context",
                   return_value=MagicMock()), \
             patch("app.services.conversation_engine.date") as mock_date:
            mock_date.today.return_value = WILD_TODAY
            mock_date.fromisoformat = date.fromisoformat
            self.eng._try_schedule_and_flow(self.ctx, self.state, WILD_FALLBACK, "13:00", "")
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(self.state.flow_booking_token, self.flows[0]["token"])
        self.assertEqual(len(self.state.flow_booking_token.split("-")), 3)

    def test_flow_04b_token_is_parseable_by_the_flow_endpoint(self):
        """The minted token must resolve back to this thread for the data exchange."""
        from app.services.booking_flow_service import parse_booking_token
        self._book()
        thread_id, _issued = parse_booking_token(self.state.flow_booking_token)
        self.assertEqual(thread_id, self.ctx.thread.id)

    def test_flow_05_invalid_slot_sends_no_flow(self):
        """FLOW-05 an unavailable slot never dispatches the Flow."""
        out = self._book(day=WILD_FALLBACK, hhmm="15:00")   # Thursday closes at 14:00
        self.assertEqual(self.flows, [])
        self.assertEqual(len(self.texts), 1)
        self.assertIsNone(self.state.flow_booking_token)
        self.assertEqual(out.action, "replied")

    def test_flow_06_accepted_quote_alone_sends_no_flow(self):
        """FLOW-06 acceptance without an established slot must not dispatch the Flow."""
        state = _make_state(last_stage="QUOTED")
        ctx = _make_ctx(state)
        eng = _make_engine(self.agenda)
        texts, flows = _capture_sends(eng)
        eng._handle_quoted_acceptance(ctx, state)
        self.assertEqual(flows, [])
        self.assertIsNone(state.flow_booking_token)
        self.assertEqual(state.last_stage, "SCHEDULING")

    def test_flow_07_booking_service_revalidates_before_confirmation(self):
        """FLOW-07 BookingFlowService re-checks the slot inside handle_confirm_booking."""
        import inspect
        from app.services.booking_flow_service import BookingFlowService
        source = inspect.getsource(BookingFlowService.handle_confirm_booking)
        self.assertIn("self._sched.check(", source)
        self.assertIn("check_out.valid", source)
        self.assertIn("BookingSlotConflictError", source)

    def test_flow_08_text_path_cannot_create_a_booking(self):
        """FLOW-08 only the Flow response path creates a booked ThreadRevision."""
        import inspect
        from app.services.conversation_engine import ConversationEngine
        booked_creators = []
        for name, member in inspect.getmembers(ConversationEngine, inspect.isfunction):
            try:
                src = inspect.getsource(member)
            except (OSError, TypeError):
                continue
            if 'ThreadRevision(' in src and 'status="booked"' in src:
                booked_creators.append(name)
        self.assertEqual(booked_creators, ["_process_flow_response"])

    def test_flow_08b_scheduling_escalation_is_provisional_only(self):
        """FLOW-08 the human-handoff path creates a provisional record, never a booking."""
        import inspect
        from app.services.conversation_engine import ConversationEngine
        src = inspect.getsource(ConversationEngine._handle_scheduling_escalation)
        self.assertIn('status="provisional"', src)
        self.assertNotIn('status="booked"', src)

    def test_booking_flow_prerequisites_block_dispatch(self):
        """No candidate / no zone → no Flow (prerequisites are not duplicated in CE)."""
        state = _make_state(current_focus_candidate_id=None,
                            home_zone_group=None, home_zone_detail=None)
        ctx = _make_ctx(state, candidates=[])
        eng = _make_engine(self.agenda)
        texts, flows = _capture_sends(eng)
        out = eng._send_booking_flow(ctx, state, BOOKING_FLOW_ID)
        self.assertIsNone(out)
        self.assertEqual(flows, [])
        self.assertIsNone(state.flow_booking_token)

    def test_booking_flow_token_reverted_when_contract_rejects(self):
        """A rejected BookingFlowService contract must not leave a live token behind."""
        from app.services.booking_flow_service import BookingTokenError
        with patch("app.services.booking_flow_service.BookingFlowService.resolve_context",
                   side_effect=BookingTokenError("nope")):
            out = self.eng._send_booking_flow(self.ctx, self.state, BOOKING_FLOW_ID)
        self.assertIsNone(out)
        self.assertIsNone(self.state.flow_booking_token)
        self.assertEqual(self.flows, [])


class TestBookingFlowEndToEndContract(unittest.TestCase):
    """FLOW-01 with a REAL session: the published Flow's own contract must accept the
    token CE mints, with no validation duplicated inside CE."""

    def setUp(self):
        import app.models as models
        self.engine_db = _sa_create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False})
        models.Base.metadata.create_all(self.engine_db)
        self.db = Session(self.engine_db)
        contact = models.WhatsAppContact(id=2043, wa_id="5491153368330", display_name="Tester")
        lead = models.Lead(id=122, estado="CONSULTA_NUEVA", flag="ACEPTADO",
                           telefono="5491153368330", necesita_humano=False)
        self.db.add_all([contact, lead])
        self.db.flush()
        thread = models.WhatsAppThread(id=2036, contact_id=2043, lead_id=122)
        self.db.add(thread)
        self.db.flush()
        state = models.WhatsAppThreadState(thread_id=2036, last_stage="SCHEDULING",
                                           current_focus_candidate_id=130,
                                           home_zone_group="Sur", home_zone_detail="Berazategui")
        candidate = models.WhatsAppThreadCandidate(
            id=130, thread_id=2036, marca="Peugeot", modelo="2008", anio=2014,
            tipo_vehiculo="SUV_4X4_DEPORTIVO", zone_group="Sur", zone_detail="Berazategui",
            status="current_focus")
        self.db.add_all([state, candidate])
        self.db.commit()
        self.state = state
        self.candidate = candidate

    def tearDown(self):
        self.db.close()

    def test_flow_01b_real_contract_accepts_the_minted_token(self):
        from app.services.conversation_engine import ConversationEngine
        from app.services.booking_flow_service import BookingFlowService
        settings = MagicMock()
        settings.whatsapp_flow_id = "1644218879979041"
        settings.whatsapp_website_flow_id = ""
        settings.booking_flow_id = BOOKING_FLOW_ID
        eng = ConversationEngine(db=self.db, settings=settings)
        eng._correlation_id = "corr-e2e"
        ctx = _make_ctx(self.state, candidates=[self.candidate])
        ctx.thread = self.db.get(__import__("app.models", fromlist=["x"]).WhatsAppThread, 2036)
        ctx.lead = self.db.get(__import__("app.models", fromlist=["x"]).Lead, 122)
        ctx.contact = self.db.get(__import__("app.models", fromlist=["x"]).WhatsAppContact, 2043)
        texts, flows = _capture_sends(eng)

        out = eng._send_booking_flow(ctx, self.state, BOOKING_FLOW_ID)

        self.assertIsNotNone(out)
        self.assertEqual(out.action, "flow_button_sent")
        self.assertEqual(flows[0]["flow_id"], BOOKING_FLOW_ID)
        self.assertEqual(flows[0]["path_id"], "BOOKING_FLOW")
        # The Flow's own contract resolves the very token CE stored.
        resolved = BookingFlowService(self.db).resolve_context(self.state.flow_booking_token)
        self.assertEqual(resolved.thread.id, 2036)
        self.assertEqual(resolved.zone_group, "Sur")
        self.assertEqual(resolved.candidate.id, 130)


# ── PHASE F — forensic attribution ───────────────────────────────────────────

class TestForensicAttribution(unittest.TestCase):

    def test_forensic_01_deployment_id_from_git_sha(self):
        """FORENSIC-01 GIT_SHA injected at runtime becomes the ledger deployment_id."""
        import importlib
        import os
        import app.services.outbound_path_registry as registry
        previous = os.environ.get("GIT_SHA")
        os.environ["GIT_SHA"] = "l43abcdef012"
        try:
            importlib.reload(registry)
            self.assertEqual(registry.get_deployment_id(), "l43abcdef012")
            self.assertNotEqual(registry.get_deployment_id(), "unknown")
        finally:
            if previous is None:
                os.environ.pop("GIT_SHA", None)
            else:
                os.environ["GIT_SHA"] = previous
            importlib.reload(registry)

    def test_forensic_02_correlation_id_passed_to_gate(self):
        """FORENSIC-02 CE outbound rows carry a correlation id (was NULL in Wild A)."""
        from app.services.conversation_engine import GateOutcome
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state)
        gate = MagicMock()
        gate.attempt.return_value = types.SimpleNamespace(outcome=GateOutcome.ALLOWED, message_id=11)
        with patch("app.services.conversation_engine.OutboundSafetyGate", return_value=gate), \
             patch("app.services.conversation_engine._send_whatsapp_cloud_text",
                   return_value=("wamid.T", {})):
            eng._send_text_to_wa(ctx, "hola")
            eng._send_text_to_wa(ctx, "otra cosa")
        first = gate.attempt.call_args_list[0].kwargs["correlation_id"]
        second = gate.attempt.call_args_list[1].kwargs["correlation_id"]
        self.assertTrue(first)
        self.assertEqual(first, second)          # stable within one CE turn

    def test_forensic_02b_correlation_id_is_generated_when_absent(self):
        eng = _make_engine()
        eng._correlation_id = None
        generated = eng._turn_correlation_id()
        self.assertTrue(generated)
        self.assertEqual(generated, eng._turn_correlation_id())


# ── REAL WILD A REPRODUCTION ─────────────────────────────────────────────────

class TestWildAReproduction(unittest.TestCase):
    """Exact preserved Wild A scheduling state, reproduced in isolation.

    vehicle Peugeot 2008 2014 · Sur/Berazategui · quote 240000 accepted ·
    input "Mñ 15hs? O nose jueves que tenes" on Tuesday 2026-09-01 ·
    agenda: Wednesday 15:00 unavailable, Thursday 13:00 available.
    """

    def setUp(self):
        self.agenda = _agenda_session()
        self.eng = _make_engine(self.agenda)
        self.state = _make_state(last_stage="SCHEDULING")
        self.ctx = _make_ctx(self.state)
        self.texts, self.flows = _capture_sends(self.eng)
        self.evaluated: list[tuple[str, str]] = []
        real_check = self.eng._schedule.check
        real_slots = self.eng._schedule.list_slots

        def spy_check(payload):
            self.evaluated.append(("check", payload.preferred_day.isoformat()))
            return real_check(payload)

        def spy_slots(payload):
            self.evaluated.append(("slots", payload.preferred_day.isoformat()))
            return real_slots(payload)

        self.eng._schedule.check = spy_check       # type: ignore[assignment]
        self.eng._schedule.list_slots = spy_slots  # type: ignore[assignment]

    def tearDown(self):
        self.agenda.close()

    def _turn(self):
        from app.services.conversation_engine import _parse_scheduling_requests
        requests = _parse_scheduling_requests([WILD_TEXT], WILD_TODAY)
        with patch("app.services.conversation_engine.date") as mock_date:
            mock_date.today.return_value = WILD_TODAY
            mock_date.fromisoformat = date.fromisoformat
            return self.eng._handle_ordered_scheduling_requests(self.ctx, self.state, requests)

    def test_wild_a_full_sequence(self):
        out = self._turn()

        # 1. Wednesday interpreted first  2. Wednesday checked
        self.assertEqual(self.evaluated[0], ("check", WILD_PRIMARY))
        # 3. Wednesday rejected (no Flow, no stored preference)
        self.assertIsNone(self.state.preferred_day)
        # 4. Thursday checked second
        thursday_positions = [i for i, (_k, d) in enumerate(self.evaluated) if d == WILD_FALLBACK]
        wednesday_positions = [i for i, (_k, d) in enumerate(self.evaluated) if d == WILD_PRIMARY]
        self.assertTrue(min(wednesday_positions) < min(thursday_positions))
        # 5. Thursday 13:00 offered
        self.assertIn("13:00", self.texts[0])
        self.assertEqual(self.state.active_requested_date, WILD_FALLBACK)
        # 6. the reply explains both branches, primary first
        reply = self.texts[0]
        self.assertLess(reply.index("miércoles 02/09"), reply.index("jueves 03/09"))
        self.assertIn("no tengo disponibilidad", reply)
        # 7. no Booking Flow before a valid slot is selected
        self.assertEqual(self.flows, [])
        self.assertIsNone(self.state.flow_booking_token)
        self.assertEqual(out.action, "replied")

    def test_wild_a_next_turn_accepting_1300_sends_booking_flow(self):
        """8/9/10 — once 13:00 is accepted, the published Flow is sent with BOOKING_FLOW."""
        self._turn()
        # Customer replies "13:00": CE resolves the time against active_requested_date.
        with patch("app.services.conversation_engine.date") as mock_date, \
             patch("app.services.booking_flow_service.BookingFlowService.resolve_context",
                   return_value=MagicMock()):
            mock_date.today.return_value = WILD_TODAY
            mock_date.fromisoformat = date.fromisoformat
            out = self.eng._try_schedule_and_flow(
                self.ctx, self.state, str(self.state.active_requested_date), "13:00", "",
            )
        self.assertEqual(out.action, "flow_button_sent")
        self.assertEqual(len(self.flows), 1)
        self.assertEqual(self.flows[0]["flow_id"], BOOKING_FLOW_ID)
        self.assertEqual(self.flows[0]["path_id"], "BOOKING_FLOW")
        self.assertEqual(self.state.preferred_day, WILD_FALLBACK)
        self.assertEqual(self.state.preferred_time, "13:00")

    def test_wild_a_agenda_matches_the_preserved_forensic_facts(self):
        """The fixture agenda reproduces the certified availability of the Wild window."""
        from app.schemas.schedule import ScheduleCheckIn
        from app.services.schedule import ScheduleService
        svc = ScheduleService(self.agenda)
        wed = svc.check(ScheduleCheckIn(
            address="Berazategui, Sur, Buenos Aires, Argentina",
            preferred_day=date(2026, 9, 2), preferred_time=time(15, 0),
            zone_group="Sur", zone_detail="Berazategui"))
        thu = svc.list_slots(ScheduleCheckIn(
            address="Berazategui, Sur, Buenos Aires, Argentina",
            preferred_day=date(2026, 9, 3), preferred_time=time(15, 0),
            zone_group="Sur", zone_detail="Berazategui"))
        self.assertFalse(wed.valid)
        self.assertEqual(thu.slots, ["13:00"])


if __name__ == "__main__":
    unittest.main()
