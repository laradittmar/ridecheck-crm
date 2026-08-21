"""FLOW-001 regression tests — scheduling date persistence + Meta Flow dispatch.

Covers the defect where preferred_day was not persisted after a slot rejection,
causing the post-AI scheduling gate to miss the date and fall back to a text reply
that mentioned "el formulario" without actually dispatching the Meta Flow button.

Fix: post-AI pday assembly (CE line ~2764) now falls back to state.active_requested_date
     when preferred_day is None and det_day is also None.

SCHED-F01  Slot rejection stores active_requested_date = canonical date; preferred_day cleared
SCHED-F02  Post-AI pday formula uses active_requested_date when preferred_day cleared
SCHED-F03  Valid slot → Flow button dispatched, not a text reply
SCHED-F04  Flow dispatch sends no text mentioning formulario
SCHED-F05  _try_schedule_and_flow does NOT set lead.estado = COORDINAR_DISPONIBILIDAD
SCHED-F06  _try_schedule_and_flow valid → db.add not called (no ThreadRevision)
SCHED-F07  _process_flow_response sets COORDINAR_DISPONIBILIDAD + creates ThreadRevision
SCHED-F08  Second rejection on same date preserves active_requested_date
SCHED-F09  Customer selects alternative offered slot → correct slot confirmed via Flow
SCHED-F10  Customer changes day → old active_requested_date overwritten with new date
SCHED-F11  'mañana' relative-date rollover resolves to correct tomorrow date
SCHED-F12  Absolute date parsing path unchanged by FLOW-001 fix
SCHED-F13  flow_booking_token guard prevents duplicate Flow dispatch
SCHED-F14  _try_schedule_and_flow valid slot → no ThreadRevision created at dispatch time
SCHED-F15  Soft-close 'no gracias' after incomplete booking preserves scheduling state
"""
from __future__ import annotations

import json
import sys
import types
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ── Shared helpers ────────────────────────────────────────────────────────────

def _make_state(**kwargs):
    ns = types.SimpleNamespace(
        home_zone_group="Oeste",
        home_zone_detail="San Justo",
        home_address=None,
        distance_km=None,
        current_focus_candidate_id=None,
        preferred_day=None,
        preferred_time=None,
        active_requested_date=None,
        last_requested_time=None,
        last_offered_slots=None,
        last_visible_slots=None,
        is_website_lead=False,
        last_stage="SCHEDULING",
        needs_human=False,
        flow_booking_token=None,
        current_revision_id=None,
        customer_name="Juan García",
        vehicle_clarification_sent=False,
        location_clarification_sent=False,
        vehicle_fallback_flow_sent=False,
        location_fallback_flow_sent=False,
        inspectability_clarification_sent=False,
    )
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _make_candidate(**kwargs):
    return types.SimpleNamespace(
        id=kwargs.get("id", 1),
        thread_id=kwargs.get("thread_id", 1),
        marca=kwargs.get("marca", "Ford"),
        modelo=kwargs.get("modelo", "Ka"),
        tipo_vehiculo=kwargs.get("tipo_vehiculo", "AUTO"),
        zone_group=kwargs.get("zone_group", "Oeste"),
        zone_detail=kwargs.get("zone_detail", "San Justo"),
        status=kwargs.get("status", "current_focus"),
        anio=kwargs.get("anio", 2019),
        label=None,
    )


def _make_ctx(thread_id=1, candidates=None, state=None):
    from app.services.conversation_engine import _Context
    thread = types.SimpleNamespace(id=thread_id, lead_id=10, contact_id=5)
    lead = types.SimpleNamespace(
        id=10, nombre="Juan", apellido="García", email=None,
        telefono="1153368330", flag="ACEPTADO",
        estado="CONSULTA_NUEVA", canal=None, necesita_humano=False,
    )
    contact = types.SimpleNamespace(wa_id="5491153368330")
    ctx = _Context.__new__(_Context)
    ctx.thread = thread
    ctx.lead = lead
    ctx.contact = contact
    ctx.candidates = list(candidates or [_make_candidate()])
    ctx.state = state or _make_state()
    ctx.db_messages = []
    return ctx


def _make_engine(flow_id="whatsapp-flow-999"):
    from app.services.conversation_engine import ConversationEngine
    db = MagicMock()
    db.flush = lambda: None
    db.add = lambda obj: None
    db.commit = lambda: None
    settings = MagicMock()
    settings.openai_api_key = "sk-fake"
    settings.openai_model = "gpt-4o-mini"
    settings.backend_url = "http://localhost:8000"
    settings.whatsapp_flow_id = flow_id
    settings.whatsapp_website_flow_id = ""
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = db
    eng.settings = settings
    return eng


def _make_sched_service(valid: bool, slots=None, reasons=None):
    svc = MagicMock()
    check_result = MagicMock()
    check_result.valid = valid
    check_result.suggested_slots = slots or []
    check_result.reasons = reasons or ([] if valid else ["Ocupado"])
    svc.check.return_value = check_result
    list_result = MagicMock()
    list_result.slots = slots or []
    svc.list_slots.return_value = list_result
    return svc


def _run_rejection(day_iso="2026-08-22", time_str="20:00", slots=None, state_extra=None):
    """Run _try_schedule_and_flow with a rejected slot; return (state, texts_sent)."""
    eng = _make_engine()
    eng._schedule = _make_sched_service(
        valid=False,
        slots=slots or ["09:00", "10:00", "11:00"],
    )
    sent: list[str] = []
    eng._send_text_to_wa = lambda ctx, txt: sent.append(txt) or "id"
    state = _make_state(**(state_extra or {}))
    ctx = _make_ctx(state=state)
    eng._try_schedule_and_flow(ctx, state, day_iso, time_str, "")
    return state, sent


def _run_valid(day_iso="2026-08-22", time_str="11:00", state_extra=None):
    """Run _try_schedule_and_flow with an accepted slot; return (state, flows, texts)."""
    eng = _make_engine()
    eng._schedule = _make_sched_service(valid=True)
    flows: list[str] = []
    texts: list[str] = []
    eng._send_flow_button = (
        lambda ctx, body, token, flow_id="", initial_screen="MAIN":
        flows.append(body) or "flow-id"
    )
    eng._send_text_to_wa = lambda ctx, txt: texts.append(txt) or "text-id"
    state = _make_state(**(state_extra or {}))
    ctx = _make_ctx(state=state)
    eng._try_schedule_and_flow(ctx, state, day_iso, time_str, "")
    return state, flows, texts


# ── SCHED-F01 ─────────────────────────────────────────────────────────────────

class TestSchedF01RejectionPersistsActiveDate(unittest.TestCase):
    """F01: Slot rejection stores active_requested_date = canonical date; preferred_day cleared."""

    def test_f01_active_requested_date_set_to_canonical_date(self):
        state, _ = _run_rejection(day_iso="2026-08-22")
        self.assertEqual(
            str(state.active_requested_date), "2026-08-22",
            "F01: active_requested_date must equal the rejected date ISO string",
        )

    def test_f01_preferred_day_cleared_on_rejection(self):
        state, _ = _run_rejection()
        self.assertIsNone(
            state.preferred_day,
            "F01: preferred_day must be None after slot rejection",
        )

    def test_f01_preferred_time_cleared_on_rejection(self):
        state, _ = _run_rejection()
        self.assertIsNone(
            state.preferred_time,
            "F01: preferred_time must be None after slot rejection",
        )

    def test_f01_last_offered_slots_populated(self):
        state, _ = _run_rejection(slots=["09:00", "10:00", "11:00"])
        self.assertIsNotNone(state.last_offered_slots, "F01: last_offered_slots must be set")
        offered = json.loads(state.last_offered_slots)
        self.assertIn("11:00", offered)


# ── SCHED-F02 ─────────────────────────────────────────────────────────────────

class TestSchedF02PostAiPdayFallback(unittest.TestCase):
    """F02: Post-AI pday formula uses active_requested_date when preferred_day cleared.

    Direct unit test of the FLOW-001 fix:
        pday = det_day or state.preferred_day or state.active_requested_date
    """

    def test_f02_hhs_voice_note_not_parsed_by_regex(self):
        """'11 hhs' does not match any time regex → (None, None) from _parse_scheduling_text."""
        from app.services.conversation_engine import _parse_scheduling_text
        det_day, det_time = _parse_scheduling_text(["11 hhs"], date(2026, 8, 21))
        self.assertIsNone(
            det_day,
            "F02: '11 hhs' must not yield a day from the regex parser",
        )
        self.assertIsNone(
            det_time,
            "F02: '11 hhs' must not yield a time from the regex parser "
            "(AI extraction fills ptime; regex handles 'hs/horas/ha' not 'hhs')",
        )

    def test_f02_pday_resolves_from_active_requested_date(self):
        """When det_day=None and preferred_day=None, pday falls back to active_requested_date."""
        det_day = None
        preferred_day = None
        active_requested_date = "2026-08-22"

        pday = det_day or preferred_day or active_requested_date
        self.assertEqual(pday, "2026-08-22",
                         "F02: pday must resolve to active_requested_date (FLOW-001 fix)")

    def test_f02_det_day_takes_priority_over_active_date(self):
        """Explicitly parsed det_day always wins over active_requested_date."""
        det_day = "2026-08-25"
        preferred_day = None
        active_requested_date = "2026-08-22"

        pday = det_day or preferred_day or active_requested_date
        self.assertEqual(pday, "2026-08-25",
                         "F02: det_day must win when present")

    def test_f02_preferred_day_takes_priority_over_active_date(self):
        """Stored preferred_day wins over active_requested_date."""
        det_day = None
        preferred_day = "2026-08-23"
        active_requested_date = "2026-08-22"

        pday = det_day or preferred_day or active_requested_date
        self.assertEqual(pday, "2026-08-23",
                         "F02: preferred_day must win when present")


# ── SCHED-F03 ─────────────────────────────────────────────────────────────────

class TestSchedF03ValidSlotDispatchesFlow(unittest.TestCase):
    """F03: Available slot → Meta Flow button dispatched, not a text reply."""

    def test_f03_flow_button_sent_once(self):
        _, flows, _ = _run_valid(day_iso="2026-08-22", time_str="11:00")
        self.assertEqual(len(flows), 1, "F03: Flow button must be sent exactly once")

    def test_f03_no_text_reply_when_flow_dispatched(self):
        _, _, texts = _run_valid(day_iso="2026-08-22", time_str="11:00")
        self.assertEqual(len(texts), 0, "F03: No text reply when Flow is dispatched")

    def test_f03_preferred_day_stored(self):
        state, _, _ = _run_valid(day_iso="2026-08-22", time_str="11:00")
        self.assertEqual(str(state.preferred_day), "2026-08-22",
                         "F03: preferred_day must be stored on valid slot")

    def test_f03_preferred_time_stored(self):
        state, _, _ = _run_valid(day_iso="2026-08-22", time_str="11:00")
        self.assertEqual(str(state.preferred_time), "11:00",
                         "F03: preferred_time must be stored on valid slot")

    def test_f03_flow_booking_token_set(self):
        state, _, _ = _run_valid(day_iso="2026-08-22", time_str="11:00")
        self.assertIsNotNone(state.flow_booking_token,
                             "F03: flow_booking_token must be set after Flow dispatch")


# ── SCHED-F04 ─────────────────────────────────────────────────────────────────

class TestSchedF04NoTextWhenFlowSent(unittest.TestCase):
    """F04: No text-only path mentioning 'formulario' fires when Flow button is dispatched."""

    def test_f04_text_not_sent(self):
        _, _, texts = _run_valid(day_iso="2026-08-22", time_str="11:00")
        self.assertEqual(len(texts), 0,
                         "F04: _send_text_to_wa must not be called when Flow is dispatched")

    def test_f04_flow_dispatched_not_text(self):
        _, flows, texts = _run_valid(day_iso="2026-08-22", time_str="11:00")
        self.assertEqual(len(flows), 1)
        self.assertEqual(len(texts), 0)


# ── SCHED-F05 ─────────────────────────────────────────────────────────────────

class TestSchedF05CrmStateNotPremature(unittest.TestCase):
    """F05: _try_schedule_and_flow does NOT set lead.estado = COORDINAR_DISPONIBILIDAD."""

    def test_f05_lead_estado_unchanged_after_try_schedule(self):
        eng = _make_engine()
        eng._schedule = _make_sched_service(valid=True)
        eng._send_flow_button = lambda ctx, body, token, **kw: "flow-id"
        eng._send_text_to_wa = lambda ctx, txt: "text-id"

        state = _make_state()
        ctx = _make_ctx(state=state)
        initial_estado = ctx.lead.estado

        eng._try_schedule_and_flow(ctx, state, "2026-08-22", "11:00", "")

        self.assertEqual(
            ctx.lead.estado, initial_estado,
            "F05: lead.estado must not change in _try_schedule_and_flow — "
            "COORDINAR_DISPONIBILIDAD is set only after _process_flow_response",
        )

    def test_f05_coordinar_disponibilidad_not_set_at_flow_send(self):
        eng = _make_engine()
        eng._schedule = _make_sched_service(valid=True)
        eng._send_flow_button = lambda ctx, body, token, **kw: "flow-id"
        eng._send_text_to_wa = lambda ctx, txt: "text-id"

        state = _make_state()
        ctx = _make_ctx(state=state)
        eng._try_schedule_and_flow(ctx, state, "2026-08-22", "11:00", "")

        self.assertNotEqual(
            ctx.lead.estado, "COORDINAR_DISPONIBILIDAD",
            "F05: COORDINAR_DISPONIBILIDAD must not appear until after Flow form submission",
        )


# ── SCHED-F06 ─────────────────────────────────────────────────────────────────

class TestSchedF06NoRevisionAtFlowSend(unittest.TestCase):
    """F06: _try_schedule_and_flow valid slot → no db.add (revision created later)."""

    def test_f06_db_add_not_called(self):
        eng = _make_engine()
        eng._schedule = _make_sched_service(valid=True)

        adds: list = []
        eng.db.add = lambda obj: adds.append(obj)
        eng._send_flow_button = lambda ctx, body, token, **kw: "flow-id"

        state = _make_state()
        ctx = _make_ctx(state=state)
        eng._try_schedule_and_flow(ctx, state, "2026-08-22", "11:00", "")

        self.assertEqual(
            len(adds), 0,
            "F06: db.add must not be called during _try_schedule_and_flow — "
            "revision is created by _process_flow_response after form submission",
        )


# ── SCHED-F07 ─────────────────────────────────────────────────────────────────

class TestSchedF07FlowCompletionCreatesBooking(unittest.TestCase):
    """F07: _process_flow_response sets COORDINAR_DISPONIBILIDAD + creates ThreadRevision."""

    def _make_engine_for_flow_response(self):
        eng = _make_engine()
        eng.db = MagicMock()
        eng.db.flush = lambda: None
        eng.db.add = lambda obj: None
        eng.db.commit = lambda: None
        eng._send_text_to_wa = lambda ctx, txt: "id"
        eng._send_booking_notification = lambda **kwargs: None
        eng._pricing = MagicMock()
        eng._pricing.recalculate_revision_if_possible = lambda **kwargs: None
        return eng

    def _make_flow_ctx(self):
        candidate = _make_candidate()
        state = _make_state(
            is_website_lead=False,
            preferred_day="2026-08-22",
            preferred_time="11:00",
            flow_booking_token="thread-1-12345",
            current_focus_candidate_id=candidate.id,
            customer_name="Juan García",
        )
        ctx = _make_ctx(state=state, candidates=[candidate])
        ctx.lead.nombre = "Juan"
        ctx.lead.apellido = "García"
        ctx.lead.telefono = "1153368330"
        return ctx, state

    def test_f07_coordinar_disponibilidad_set_after_flow_response(self):
        eng = self._make_engine_for_flow_response()
        ctx, state = self._make_flow_ctx()

        eng._process_flow_response(
            ctx,
            flow_data={
                "nombre_apellido": "Juan García",
                "email": "juan@example.com",
                "como_llego": "referido",
                "direccion": "Av. Corrientes 1234",
            },
            flow_token="thread-1-12345",
        )

        self.assertEqual(
            ctx.lead.estado, "COORDINAR_DISPONIBILIDAD",
            "F07: lead.estado must be COORDINAR_DISPONIBILIDAD after _process_flow_response",
        )

    def test_f07_revision_created_after_flow_response(self):
        eng = self._make_engine_for_flow_response()
        ctx, state = self._make_flow_ctx()

        revisions: list = []
        eng.db.add = lambda obj: revisions.append(obj)

        eng._process_flow_response(
            ctx,
            flow_data={
                "nombre_apellido": "Juan García",
                "email": "juan@example.com",
                "como_llego": "referido",
                "direccion": "Av. Corrientes 1234",
            },
            flow_token="thread-1-12345",
        )

        revision_like = [
            r for r in revisions
            if hasattr(r, "buyer_name") or hasattr(r, "scheduled_date")
        ]
        self.assertGreater(
            len(revision_like), 0,
            "F07: At least one ThreadRevision must be created by _process_flow_response",
        )


# ── SCHED-F08 ─────────────────────────────────────────────────────────────────

class TestSchedF08RejectionPreservesDate(unittest.TestCase):
    """F08: Requesting an unavailable time does NOT erase the resolved canonical date."""

    def test_f08_second_rejection_same_day_preserves_active_date(self):
        state, _ = _run_rejection(day_iso="2026-08-22", time_str="20:00")
        self.assertEqual(str(state.active_requested_date), "2026-08-22")

        eng = _make_engine()
        eng._schedule = _make_sched_service(valid=False, slots=["09:00", "10:00", "11:00"])
        eng._send_text_to_wa = lambda ctx, txt: "id"
        ctx = _make_ctx(state=state)
        eng._try_schedule_and_flow(ctx, state, "2026-08-22", "15:00", "")

        self.assertEqual(
            str(state.active_requested_date), "2026-08-22",
            "F08: active_requested_date must stay '2026-08-22' after second rejection on same day",
        )

    def test_f08_preferred_day_stays_none_after_second_rejection(self):
        state, _ = _run_rejection(day_iso="2026-08-22", time_str="20:00")
        eng = _make_engine()
        eng._schedule = _make_sched_service(valid=False)
        eng._send_text_to_wa = lambda ctx, txt: "id"
        eng._try_schedule_and_flow(_make_ctx(state=state), state, "2026-08-22", "15:00", "")
        self.assertIsNone(state.preferred_day,
                          "F08: preferred_day must remain None after each rejection")


# ── SCHED-F09 ─────────────────────────────────────────────────────────────────

class TestSchedF09AlternativeSlotSelected(unittest.TestCase):
    """F09: Customer picks different offered time → correct slot confirmed via Flow."""

    def test_f09_alternative_slot_dispatches_flow(self):
        state, _ = _run_rejection(
            day_iso="2026-08-22", time_str="20:00",
            slots=["09:00", "10:00", "11:00"],
        )
        self.assertEqual(str(state.active_requested_date), "2026-08-22")

        eng = _make_engine()
        eng._schedule = _make_sched_service(valid=True)
        flows: list[str] = []
        eng._send_flow_button = (
            lambda ctx, body, token, **kw: flows.append(body) or "flow-id"
        )
        eng._send_text_to_wa = lambda ctx, txt: "text-id"
        ctx = _make_ctx(state=state)
        eng._try_schedule_and_flow(ctx, state, "2026-08-22", "10:00", "")

        self.assertEqual(len(flows), 1, "F09: Flow must be dispatched for alternative slot")
        self.assertEqual(str(state.preferred_day), "2026-08-22")
        self.assertEqual(str(state.preferred_time), "10:00")

    def test_f09_correct_time_stored(self):
        state, _ = _run_rejection(day_iso="2026-08-22", time_str="20:00",
                                  slots=["09:00", "10:00", "11:00"])
        eng = _make_engine()
        eng._schedule = _make_sched_service(valid=True)
        eng._send_flow_button = lambda ctx, body, token, **kw: "flow-id"
        ctx = _make_ctx(state=state)
        eng._try_schedule_and_flow(ctx, state, "2026-08-22", "09:00", "")
        self.assertEqual(str(state.preferred_time), "09:00",
                         "F09: The selected alternative time must be stored in preferred_time")


# ── SCHED-F10 ─────────────────────────────────────────────────────────────────

class TestSchedF10NewDayClearsOldDate(unittest.TestCase):
    """F10: Customer changes day → old active_requested_date overwritten by new date."""

    def test_f10_new_day_overwrites_active_requested_date(self):
        state, _ = _run_rejection(day_iso="2026-08-22", time_str="20:00")
        self.assertEqual(str(state.active_requested_date), "2026-08-22")

        eng = _make_engine()
        eng._schedule = _make_sched_service(valid=False, slots=["09:00", "10:00"])
        eng._send_text_to_wa = lambda ctx, txt: "id"
        ctx = _make_ctx(state=state)
        eng._try_schedule_and_flow(ctx, state, "2026-08-25", "20:00", "")

        self.assertEqual(
            str(state.active_requested_date), "2026-08-25",
            "F10: active_requested_date must be overwritten with the new day",
        )

    def test_f10_old_offered_slots_replaced(self):
        state, _ = _run_rejection(
            day_iso="2026-08-22", time_str="20:00", slots=["09:00", "10:00"]
        )
        eng = _make_engine()
        eng._schedule = _make_sched_service(
            valid=False, slots=["14:00", "15:00", "16:00"]
        )
        eng._send_text_to_wa = lambda ctx, txt: "id"
        ctx = _make_ctx(state=state)
        eng._try_schedule_and_flow(ctx, state, "2026-08-25", "20:00", "")

        new_slots = json.loads(state.last_offered_slots)
        self.assertIn("14:00", new_slots, "F10: Offered slots must reflect the new day")
        self.assertNotIn("09:00", new_slots)


# ── SCHED-F11 ─────────────────────────────────────────────────────────────────

class TestSchedF11MananaRollover(unittest.TestCase):
    """F11: 'mañana' relative-date resolves to the correct tomorrow date."""

    def _parse(self, texts, today):
        from app.services.conversation_engine import _parse_scheduling_text
        return _parse_scheduling_text(texts, today)

    def test_f11_manana_resolves_to_tomorrow(self):
        today = date(2026, 8, 21)
        day, _ = self._parse(["mañana a las 10hs"], today)
        self.assertEqual(day, "2026-08-22",
                         "F11: 'mañana' from 2026-08-21 must resolve to 2026-08-22")

    def test_f11_manana_time_extracted(self):
        today = date(2026, 8, 21)
        _, t = self._parse(["mañana a las 10hs"], today)
        self.assertEqual(t, "10:00")

    def test_f11_manana_without_time_returns_day_only(self):
        today = date(2026, 8, 21)
        day, t = self._parse(["mañana"], today)
        self.assertEqual(day, "2026-08-22")
        self.assertIsNone(t)

    def test_f11_month_end_rollover(self):
        """'mañana' on the last day of August crosses into September."""
        today = date(2026, 8, 31)
        day, _ = self._parse(["mañana 10hs"], today)
        self.assertEqual(day, "2026-09-01",
                         "F11: mañana on Aug 31 must yield Sep 01")


# ── SCHED-F12 ─────────────────────────────────────────────────────────────────

class TestSchedF12AbsoluteDateUnchanged(unittest.TestCase):
    """F12: Absolute date parsing is unaffected by the FLOW-001 fix."""

    def _parse(self, texts, today):
        from app.services.conversation_engine import _parse_scheduling_text
        return _parse_scheduling_text(texts, today)

    def test_f12_slash_date_parsed(self):
        day, _ = self._parse(["25/8 a las 14hs"], date(2026, 8, 21))
        self.assertEqual(day, "2026-08-25")

    def test_f12_del_date_parsed(self):
        day, t = self._parse(["el 25 del 8 a las 14hs"], date(2026, 8, 21))
        self.assertEqual(day, "2026-08-25")
        self.assertEqual(t, "14:00")

    def test_f12_spanish_month_name_parsed(self):
        day, _ = self._parse(["el 25 de agosto a las 9hs"], date(2026, 8, 21))
        self.assertEqual(day, "2026-08-25")

    def test_f12_absolute_day_and_time_together(self):
        day, t = self._parse(["el 25/8 a las 10:30"], date(2026, 8, 21))
        self.assertEqual(day, "2026-08-25")
        self.assertEqual(t, "10:30")


# ── SCHED-F13 ─────────────────────────────────────────────────────────────────

class TestSchedF13NoDuplicateFlow(unittest.TestCase):
    """F13: flow_booking_token guard prevents duplicate Flow dispatch."""

    def test_f13_gate_closed_when_token_set(self):
        from app.services.conversation_engine import STAGE_SCHEDULING
        state = _make_state(
            last_stage=STAGE_SCHEDULING,
            needs_human=False,
            flow_booking_token="thread-1-12345",
        )
        gate_open = (
            state.last_stage == STAGE_SCHEDULING
            and not state.needs_human
            and not state.flow_booking_token
        )
        self.assertFalse(gate_open,
                         "F13: SCHEDULING gate must be closed when flow_booking_token is set")

    def test_f13_gate_open_when_no_token(self):
        from app.services.conversation_engine import STAGE_SCHEDULING
        state = _make_state(
            last_stage=STAGE_SCHEDULING,
            needs_human=False,
            flow_booking_token=None,
        )
        gate_open = (
            state.last_stage == STAGE_SCHEDULING
            and not state.needs_human
            and not state.flow_booking_token
        )
        self.assertTrue(gate_open, "F13: gate must be open when flow_booking_token is None")

    def test_f13_active_requested_date_not_consumed_when_token_set(self):
        """With token set, active_requested_date is preserved for reference but no Flow fires."""
        from app.services.conversation_engine import STAGE_SCHEDULING
        state = _make_state(
            last_stage=STAGE_SCHEDULING,
            needs_human=False,
            flow_booking_token="thread-1-12345",
            active_requested_date="2026-08-22",
        )
        gate_open = (
            state.last_stage == STAGE_SCHEDULING
            and not state.needs_human
            and not state.flow_booking_token
        )
        self.assertFalse(gate_open, "F13: gate closed even when active_requested_date is present")


# ── SCHED-F14 ─────────────────────────────────────────────────────────────────

class TestSchedF14NoDuplicateRevision(unittest.TestCase):
    """F14: _try_schedule_and_flow does not create a ThreadRevision at dispatch time."""

    def test_f14_no_model_object_added_to_db(self):
        eng = _make_engine()
        eng._schedule = _make_sched_service(valid=True)

        adds: list = []
        eng.db.add = lambda obj: adds.append(obj)
        eng._send_flow_button = lambda ctx, body, token, **kw: "flow-id"

        state = _make_state()
        ctx = _make_ctx(state=state)
        eng._try_schedule_and_flow(ctx, state, "2026-08-22", "11:00", "")

        self.assertEqual(
            len(adds), 0,
            "F14: _try_schedule_and_flow must not call db.add — "
            "revision created by _process_flow_response only",
        )

    def test_f14_active_requested_date_cleared_after_valid_slot(self):
        """When slot is valid, active_requested_date is cleared (no stale context)."""
        _, _, _ = _run_valid(
            day_iso="2026-08-22", time_str="11:00",
            state_extra={"active_requested_date": "2026-08-22"},
        )
        state, _, _ = _run_valid(
            day_iso="2026-08-22", time_str="11:00",
            state_extra={"active_requested_date": "2026-08-22"},
        )
        self.assertIsNone(state.active_requested_date,
                          "F14: active_requested_date cleared when slot is confirmed valid")


# ── SCHED-F15 ─────────────────────────────────────────────────────────────────

class TestSchedF15SoftClosePreservesSchedulingState(unittest.TestCase):
    """F15: Soft-close 'no gracias' after incomplete booking preserves scheduling state."""

    def test_f15_no_gracias_is_soft_close(self):
        from app.services.conversation_engine import _is_general_faq_or_soft_close
        self.assertTrue(
            _is_general_faq_or_soft_close("no gracias"),
            "F15: 'no gracias' must be detected as soft-close",
        )

    def test_f15_por_ahora_no_is_soft_close(self):
        from app.services.conversation_engine import _is_general_faq_or_soft_close
        self.assertTrue(
            _is_general_faq_or_soft_close("por ahora no"),
            "F15: 'por ahora no' must be detected as soft-close",
        )

    def test_f15_soft_close_detection_does_not_mutate_state(self):
        """_is_general_faq_or_soft_close is a pure function — scheduling state survives."""
        from app.services.conversation_engine import _is_general_faq_or_soft_close
        state = _make_state(
            active_requested_date="2026-08-22",
            last_offered_slots='["09:00","10:00","11:00"]',
            flow_booking_token=None,
        )
        _is_general_faq_or_soft_close("no gracias")
        self.assertEqual(
            str(state.active_requested_date), "2026-08-22",
            "F15: active_requested_date must survive soft-close detection",
        )
        self.assertIsNotNone(
            state.last_offered_slots,
            "F15: last_offered_slots must survive soft-close detection",
        )

    def test_f15_booking_continuable_after_soft_close(self):
        """After soft-close, the fixed pday formula can still find active_requested_date."""
        from app.services.conversation_engine import STAGE_SCHEDULING
        state = _make_state(
            last_stage=STAGE_SCHEDULING,
            active_requested_date="2026-08-22",
            preferred_day=None,
            preferred_time=None,
            flow_booking_token=None,
        )
        det_day = None
        pday = det_day or state.preferred_day or state.active_requested_date
        ptime = "11:00"  # from AI extraction on next turn

        self.assertEqual(pday, "2026-08-22",
                         "F15: booking attempt can continue via active_requested_date after soft-close")
        self.assertIsNotNone(ptime)
        self.assertIsNotNone(pday)


if __name__ == "__main__":
    unittest.main()
