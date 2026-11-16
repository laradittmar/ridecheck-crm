"""R6-C4B-R1B — deferred-interest semantics (R1A observations O1, O2, O4).

R1A made the M21.1.6 deferred-interest branch complete for the first time. On a QUALIFYING
thread that already holds a focus vehicle it then sent "cuando tengas algún auto en vista…"
followed by the G3-6 location question, and attributed the turn to REPLY_MODEL.

R1B: the deferred turn is terminal. One deterministic message, chosen by whether canonical
state holds a focus vehicle; no proposition joins it; no outstanding question; no bridge;
deterministic attribution. The normal (non-deferred) clarification is unchanged (matrix C).

Every turn runs on a FROZEN Buenos Aires business date (Wed 2026-11-11 12:00 ART) for both
the engine clock and the validator clock, so the R1A-O3 clock skew cannot reach this file.
"""
from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import sys
import unittest
from datetime import date, datetime, timezone
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))
sys.path.insert(0, str(_ROOT / "tests"))

import test_r6_full_engine_prose_corpus as C  # noqa: E402  (shared harness module)
import test_r6_c4a_transactional_provenance as A  # noqa: E402

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (AiEvent, Lead, ThreadRevision, WhatsAppBookingOffer,  # noqa: E402
                        WhatsAppMessage, WhatsAppOutstandingQuestion, WhatsAppThread,
                        WhatsAppThreadCandidate, WhatsAppThreadState)
from app.schemas.conversation import ConversationHandleIn, ConversationHandleOut  # noqa: E402
from app.services import response_validator as RV  # noqa: E402
from app.services.narrative_schema import DEFERRED_RESPONSE_ES  # noqa: E402

H = C.H
Q3 = "no se bien todavia, lo estoy pensando"
#: B: with no candidate, Q3 itself is answered by the pre-existing Layer F service greeting
#: before the interpreter (it matches no step-8.5 pattern); this is a deferred no-vehicle turn.
NO_VEHICLE = "por ahora estoy mirando, todavía no tengo ningún auto en vista"
FOCUS_COPY = ("Perfecto, pensalo tranquilo. Cuando quieras retomar la revisión de ese auto, "
              "escribinos.")
NO_FOCUS_COPY = ("Perfecto, cuando tengas algún auto en vista escribinos y te ayudamos con la "
                 "revisión.")
LOCATION_ASK = "¿En qué localidad o barrio está el auto?"
MODEL_BODY = "Genial! Te espero el lunes a las 10:00, ya quedó reservado. ¿En qué barrio está?"

_FROZEN_UTC = A.NOON_WED                                     # 2026-11-11 15:00Z = 12:00 ART


class _FrozenDate(date):
    @classmethod
    def today(cls):
        return date(2026, 11, 11)


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _FROZEN_UTC if tz is None else _FROZEN_UTC.astimezone(tz)

    @classmethod
    def utcnow(cls):
        return _FROZEN_UTC.replace(tzinfo=None)


def _decision(*, deferred: bool, body: str = MODEL_BODY, intent: str = "QUALIFYING") -> str:
    return json.dumps({
        "intent": intent, "reply": body, "lead_flag": None, "needs_human": False,
        "deferred_interest": deferred,
        "vehicle_make_model": None, "vehicle_year": None, "vehicle_location": None,
        "customer_origin": None, "inspectability": None,
        "asks_price": False, "asks_faq": False, "asks_schedule": False,
        "extracted": {"customer_name": None, "zone_detail": None,
                      "preferred_day_iso": "2026-11-16", "preferred_time_str": "10:00",
                      "tipo_vehiculo": None, "vendedor_tipo": None},
        "candidate": {"action": "none"},
    })


def _cfg(*, oq: bool):
    return dataclasses.replace(C._cfg(False, False), outstanding_question_enabled=oq)


def _seed(eng, *, focus=True, needs_human=False):
    tid, wa = A._seed(eng, stage="QUALIFYING", flag="PRESUPUESTANDO", zone=False, date_iso=None)
    with Session(eng) as db:
        st = db.execute(select(WhatsAppThreadState).where(
            WhatsAppThreadState.thread_id == tid)).scalar_one()
        st.last_offered_slots = None
        st.last_visible_slots = None
        st.needs_human = needs_human
        if not focus:
            st.current_focus_candidate_id = None
            db.flush()
            for c in db.execute(select(WhatsAppThreadCandidate).where(
                    WhatsAppThreadCandidate.thread_id == tid)).scalars():
                db.delete(c)
        db.commit()
    return tid, wa


def _inbound(eng, tid, wa, text=Q3):
    n = next(C._SEQ)
    wamid = f"wamid.R1B.IN.{n:07d}"
    with Session(eng) as db:
        db.add(WhatsAppMessage(thread_id=tid, direction="in", wa_message_id=wamid,
                               message_type="text", text=text, status="received",
                               timestamp=datetime.now(timezone.utc)))
        db.add(AiEvent(thread_id=tid, wa_message_id=wamid, wa_id=wa, status="triggered"))
        db.commit()
    return ConversationHandleIn(thread_id=tid, wa_message_id=wamid, wa_id=wa,
                                message_type="text", text=text)


def _snap(eng, tid):
    with Session(eng) as db:
        st = db.execute(select(WhatsAppThreadState).where(
            WhatsAppThreadState.thread_id == tid)).scalar_one()
        lead = db.get(Lead, db.get(WhatsAppThread, tid).lead_id)
        count = lambda M, *w: db.execute(select(func.count()).select_from(M).where(  # noqa: E731
            M.thread_id == tid, *w)).scalar_one()
        cands = sorted((c.id, c.status, c.marca, c.modelo, c.anio, c.tipo_vehiculo)
                       for c in db.execute(select(WhatsAppThreadCandidate).where(
                           WhatsAppThreadCandidate.thread_id == tid)).scalars())
        return dict(
            stage=st.last_stage, needs_human=st.needs_human, flag=lead.flag, estado=lead.estado,
            preferred_day=st.preferred_day, preferred_time=st.preferred_time,
            token=st.flow_booking_token, revision_id=st.current_revision_id,
            focus=st.current_focus_candidate_id, candidates=cands,
            zone=(st.home_zone_group, st.home_zone_detail),
            cursor=st.last_processed_inbound_wa_message_id,
            revisions=count(ThreadRevision), offers=count(WhatsAppBookingOffer),
            oq=count(WhatsAppOutstandingQuestion),
            out_rows=count(WhatsAppMessage, WhatsAppMessage.direction == "out"))


def _run(eng, ev, *, deferred=True, oq=False, body=MODEL_BODY, intent="QUALIFYING"):
    calls = {"interpreter": 0, "generator": 0, "verifier": 0}
    flows = []

    def hook(ce):
        def interp(messages):
            calls["interpreter"] += 1
            return _decision(deferred=deferred, body=body, intent=intent)
        ce._call_openai_inner = interp
        g, v = ce._call_generator, ce._call_verifier

        def gen(*a, **k):
            calls["generator"] += 1
            return g(*a, **k)

        def ver(*a, **k):
            calls["verifier"] += 1
            return v(*a, **k)
        ce._call_generator, ce._call_verifier = gen, ver

    with mock.patch.object(RV, "_utcnow", lambda: _FROZEN_UTC), \
            mock.patch("app.services.conversation_engine.date", _FrozenDate), \
            mock.patch("app.services.conversation_engine.datetime", _FrozenDateTime), \
            mock.patch("app.services.conversation_engine._send_whatsapp_cloud_flow",
                       lambda *a, **k: flows.append(1) or ("wamid.R1B.FLOW.X", 200)), \
            H.Meta() as m:
        out, ce = H.run_turn(eng, ev, cfg=_cfg(oq=oq), engine_hook=hook)
    return out, ce, [t for _w, t in m.sent], m, flows, calls


class _Base(unittest.TestCase):
    eng = None

    @classmethod
    def setUpClass(cls):
        cls.eng = C._engine()

    def terminal(self, *, focus, oq):
        tid, wa = _seed(self.eng, focus=focus)
        before = _snap(self.eng, tid)
        ev = _inbound(self.eng, tid, wa, text=Q3 if focus else NO_VEHICLE)
        out, ce, sent, m, flows, calls = _run(self.eng, ev, oq=oq)
        after = _snap(self.eng, tid)
        copy = FOCUS_COPY if focus else NO_FOCUS_COPY
        # one reply, the exact copy, nothing appended
        self.assertIsInstance(out, ConversationHandleOut)
        self.assertEqual(out.action, "replied", out)
        self.assertEqual(sent, [copy])
        self.assertNotIn("?", sent[0])
        self.assertNotIn(LOCATION_ASK, sent[0])
        if focus:
            self.assertNotIn("cuando tengas algún auto en vista", sent[0])
        self.assertEqual(after["out_rows"], before["out_rows"] + 1)
        self.assertEqual(m.egress, [])
        # no outstanding question, no bridge
        self.assertEqual(after["oq"], 0)
        self.assertEqual((calls["generator"], calls["verifier"]), (0, 0))
        self.assertFalse(getattr(ce, "_turn_bridge_sent", None))
        # no commercial mutation, no Flow
        self.assertEqual(flows, [])
        self.assertEqual((after["revisions"], after["offers"]), (0, 0))
        self.assertIsNone(after["token"])
        self.assertIsNone(after["revision_id"])
        for k in ("stage", "flag", "estado", "focus", "candidates", "zone", "needs_human"):
            self.assertEqual(after[k], before[k], k)
        self.assertIsNone(after["preferred_day"])
        self.assertIsNone(after["preferred_time"])
        # deterministic attribution; the interpreter really ran and its body was discarded
        self.assertEqual(out.answer_source, "DETERMINISTIC_RULE")
        self.assertTrue(out.ai_invoked)
        self.assertEqual(calls["interpreter"], 1)
        self.assertEqual(ce._turn_renderer_source, "DETERMINISTIC_TEMPLATE")
        self.assertEqual(ce._turn_decision_authority, "DETERMINISTIC_RULE")
        self.assertNotIn("reservado", sent[0])
        self.assertNotIn("10:00", sent[0])
        self.assertIsInstance(out.latency_ce_ms, int)
        # retry of the same inbound is deduplicated
        out2, _ce, sent2, _m, flows2, calls2 = _run(self.eng, ev, oq=oq)
        self.assertEqual(out2.action, "skipped_dedup")
        self.assertEqual((sent2, flows2, calls2["interpreter"]), ([], [], 0))
        self.assertEqual(_snap(self.eng, tid), after)
        return tid


class A_FocusVehicleExists(_Base):

    def test_a_outstanding_question_switch_false(self):
        self.terminal(focus=True, oq=False)

    def test_a_outstanding_question_switch_true(self):
        self.terminal(focus=True, oq=True)


class B_NoFocusVehicle(_Base):

    def test_b_outstanding_question_switch_false(self):
        self.terminal(focus=False, oq=False)

    def test_b_outstanding_question_switch_true(self):
        self.terminal(focus=False, oq=True)


class C_NormalTurnControl(_Base):
    """Same incomplete vehicle/location state, deferred_interest=false: the canonical G3-6
    location clarification is still composed, and with the switch on it is still recorded."""

    def _normal(self, oq):
        tid, wa = _seed(self.eng, focus=True)
        # the R1 real-provider ordinary QUALIFYING turn (Q1), interpreter says not deferred
        ev = _inbound(self.eng, tid, wa, text="Dale, gracias por responder")
        out, ce, sent, _m, flows, _calls = _run(self.eng, ev, deferred=False, oq=oq,
                                                body="¡De nada! Siempre estoy para ayudarte.", intent="OTHER")
        self.assertEqual(out.action, "replied", out)
        self.assertEqual(len(sent), 1, sent)
        self.assertIn(LOCATION_ASK, sent[0])
        self.assertNotIn(FOCUS_COPY, sent[0])
        self.assertNotIn(NO_FOCUS_COPY, sent[0])
        self.assertFalse(getattr(ce, "_turn_deferred_terminal", False))
        self.assertEqual(flows, [])
        return _snap(self.eng, tid)

    def test_c_switch_false_still_asks_location(self):
        self.assertEqual(self._normal(False)["oq"], 0)

    def test_c_switch_true_still_asks_and_records_location(self):
        self.assertEqual(self._normal(True)["oq"], 1)


class D_NeedsHumanControl(_Base):

    def test_d_needs_human_short_circuit_sends_nothing(self):
        for focus in (True, False):
            with self.subTest(focus=focus):
                tid, wa = _seed(self.eng, focus=focus, needs_human=True)
                before = _snap(self.eng, tid)
                ev = _inbound(self.eng, tid, wa)
                out, _ce, sent, _m, flows, calls = _run(self.eng, ev, oq=True)
                after = _snap(self.eng, tid)
                self.assertEqual(out.action, "skipped_human")
                self.assertEqual((sent, flows, calls["interpreter"]), ([], [], 0))
                self.assertTrue(after["needs_human"])
                self.assertEqual({k: v for k, v in after.items() if k != "cursor"},
                                 {k: v for k, v in before.items() if k != "cursor"})


if __name__ == "__main__":
    unittest.main()
