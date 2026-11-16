"""R6-C4B-R1A — deferred-interest crash correction (R1 closeout residual R-1).

Before R1A, `ConversationEngine._process_text` returned
`self._handle_deferred_interest(ctx, state), True` — a TUPLE — from the M21.1.6 deferred-interest
intercept (present since af694be). `_process_text` is declared `-> ConversationHandleOut`, every
other branch returns one, and `handle()` assigns `out.latency_ce_ms` on the result, so every
deferred-interest turn raised `AttributeError: 'tuple' object has no attribute 'latency_ce_ms'`.
The real interpreter reached it on "no se bien todavia, lo estoy pensando" (R1 case Q3).

These tests drive the REAL route (`POST /api/conversation/handle` → engine → ai_events write-back)
on an in-memory SQLite world, with only the interpreter replaced by a canned decision carrying
`deferred_interest: true` (the real-provider Q3 shape). No network egress is possible.
"""
from __future__ import annotations

import ast
import dataclasses
import json
import os
import pathlib
import sys
import textwrap
import unittest
from datetime import datetime, timezone
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))
sys.path.insert(0, str(_ROOT / "tests"))

import test_r6_full_engine_prose_corpus as C  # noqa: E402  (shared harness module)
import test_r6_c4a_transactional_provenance as A  # noqa: E402

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (AiEvent, Lead, ThreadRevision, WhatsAppBookingOffer,  # noqa: E402
                        WhatsAppMessage, WhatsAppThread, WhatsAppThreadCandidate,
                        WhatsAppThreadState)
from app.schemas.conversation import ConversationHandleIn, ConversationHandleOut  # noqa: E402
from app.services import response_validator as RV  # noqa: E402
from app.services.conversation_engine import ConversationEngine  # noqa: E402
from app.services.narrative_schema import DEFERRED_RESPONSE_ES  # noqa: E402

H = C.H
SRC = (_ROOT / "backend" / "app" / "services" / "conversation_engine.py").read_text(
    encoding="utf-8")

#: the exact synthetic real-provider regression message (R1 case Q3)
Q3 = "no se bien todavia, lo estoy pensando"


def _deferred_decision() -> str:
    """The interpreter shape the real provider returned on Q3 (synthetic reply text)."""
    return json.dumps({
        "intent": "QUALIFYING",
        "reply": "Genial! Te espero el lunes a las 10:00, ya quedó reservado.",  # never delivered
        "lead_flag": None, "needs_human": False,
        "deferred_interest": True,
        "vehicle_make_model": None, "vehicle_year": None, "vehicle_location": None,
        "customer_origin": None, "inspectability": None,
        "asks_price": False, "asks_faq": False, "asks_schedule": False,
        "extracted": {"customer_name": None, "zone_detail": None,
                      "preferred_day_iso": "2026-11-16", "preferred_time_str": "10:00",
                      "tipo_vehiculo": None, "vendedor_tipo": None},
        "candidate": {"action": "none"},
    })


def _all_switches_off_cfg():
    """The seven settings-borne launch switches false (OUTBOUND_ENABLED is the eighth, env)."""
    return dataclasses.replace(
        C._cfg(False, False), smart_booking_ce_enabled=False,
        operator_whatsapp_notify_enabled=False, conversation_engine_direct_webhook_enabled=False,
        evidence_admission_enabled=False, outstanding_question_enabled=False,
        locality_resolution_safety_enabled=False, booking_flow_details_prefill_enabled=False)


def _seed(eng, *, needs_human=False):
    tid, wa = A._seed(eng, stage="QUALIFYING", flag="PRESUPUESTANDO", zone=False, date_iso=None)
    with Session(eng) as db:
        st = db.execute(select(WhatsAppThreadState).where(
            WhatsAppThreadState.thread_id == tid)).scalar_one()
        st.last_offered_slots = None
        st.last_visible_slots = None
        if needs_human:
            st.needs_human = True
        db.commit()
    return tid, wa


def _inbound(eng, tid, wa, text=Q3):
    n = next(C._SEQ)
    wamid = f"wamid.R1A.IN.{n:07d}"
    with Session(eng) as db:
        db.add(WhatsAppMessage(thread_id=tid, direction="in", wa_message_id=wamid,
                               message_type="text", text=text, status="received",
                               timestamp=datetime.now(timezone.utc)))
        db.add(AiEvent(thread_id=tid, wa_message_id=wamid, wa_id=wa, status="triggered"))
        db.commit()
    return ConversationHandleIn(thread_id=tid, wa_message_id=wamid, wa_id=wa,
                                message_type="text", text=text)


def _snapshot(eng, tid):
    with Session(eng) as db:
        st = db.execute(select(WhatsAppThreadState).where(
            WhatsAppThreadState.thread_id == tid)).scalar_one()
        lead = db.get(Lead, db.get(WhatsAppThread, tid).lead_id)
        count = lambda M, *w: db.execute(select(func.count()).select_from(M).where(  # noqa: E731
            M.thread_id == tid, *w)).scalar_one()
        return dict(
            stage=st.last_stage, needs_human=st.needs_human, flag=lead.flag,
            estado=lead.estado, lead_needs_human=lead.necesita_humano,
            preferred_day=st.preferred_day, preferred_time=st.preferred_time,
            token=st.flow_booking_token, revision_id=st.current_revision_id,
            focus=st.current_focus_candidate_id,
            cursor=st.last_processed_inbound_wa_message_id,
            revisions=count(ThreadRevision), offers=count(WhatsAppBookingOffer),
            candidates=count(WhatsAppThreadCandidate),
            out_rows=count(WhatsAppMessage, WhatsAppMessage.direction == "out"))


def _run(eng, ev, *, outbound=True, cfg=None):
    calls = {"interpreter": 0, "process_text": []}
    flows = []

    def hook(ce):
        def interp(messages):
            calls["interpreter"] += 1
            return _deferred_decision()
        ce._call_openai_inner = interp
        inner = ce._process_text

        def spy(ctx, event):
            r = inner(ctx, event)
            calls["process_text"].append(r)
            return r
        ce._process_text = spy

    with mock.patch.object(RV, "_utcnow", lambda: A.NOON_WED), \
            mock.patch("app.services.conversation_engine._send_whatsapp_cloud_flow",
                       lambda *a, **k: flows.append(1) or ("wamid.R1A.FLOW.X", 200)), \
            H.Meta() as m, \
            mock.patch.dict(os.environ, {"OUTBOUND_ENABLED": "true" if outbound else "false"}):
        out, ce = H.run_turn(eng, ev, cfg=cfg or C._cfg(False, False), engine_hook=hook)
    return out, ce, m, flows, calls


class _Base(unittest.TestCase):
    eng = None

    @classmethod
    def setUpClass(cls):
        cls.eng = C._engine()


class TestR1ADeferredInterestTurn(_Base):

    def test_01_exact_regression_message_does_not_crash_and_replies_once(self):
        tid, wa = _seed(self.eng)
        before = _snapshot(self.eng, tid)
        ev = _inbound(self.eng, tid, wa)
        out, ce, m, flows, calls = _run(self.eng, ev)
        after = _snapshot(self.eng, tid)
        # 1/3 not crashed, latency assigned by handle()
        self.assertIsInstance(out, ConversationHandleOut)
        self.assertEqual(out.action, "replied", out)
        self.assertIsInstance(out.latency_ce_ms, int)
        self.assertGreaterEqual(out.latency_ce_ms, 0)
        self.assertEqual(out.answer_source, "DETERMINISTIC_RULE")
        self.assertTrue(out.ai_invoked)
        self.assertEqual(calls["interpreter"], 1)
        # 2 the branch returns the declared shape, not a tuple
        self.assertEqual(len(calls["process_text"]), 1)
        self.assertIsInstance(calls["process_text"][0], ConversationHandleOut)
        self.assertNotIsInstance(calls["process_text"][0], tuple)
        # 4 exactly one outbound reply: the approved deferred copy, no bridge, no model prose.
        # (R1A-O1: on a QUALIFYING thread whose focus vehicle has a type and no zone, the
        # pre-existing G3-6 clarification proposition appends the canonical location question;
        # that composition is outside R1A and is recorded, not changed.)
        self.assertEqual(len(m.sent), 1, m.sent)
        sent = m.sent[0][1]
        # R6-C4B-R1B: this world holds a focus vehicle, so the terminal focus copy is the
        # whole message (R1A-O1/O2 corrected; see test_r6_c4b_r1b_deferred_semantics).
        self.assertEqual(sent, "Perfecto, pensalo tranquilo. Cuando quieras retomar la "
                               "revisión de ese auto, escribinos.")
        self.assertNotIn("reservado", sent)
        self.assertNotIn("10:00", sent)
        self.assertEqual(m.egress, [])
        self.assertFalse(getattr(ce, "_turn_bridge_sent", None))
        self.assertEqual(after["out_rows"], before["out_rows"] + 1)
        self.assertEqual(out.wa_message_id is not None, True)
        # 5 no quote / revision / offer / booking / Flow
        self.assertEqual(flows, [])
        self.assertEqual((after["revisions"], after["offers"]), (0, 0))
        self.assertIsNone(after["token"])
        self.assertIsNone(after["revision_id"])
        self.assertEqual(after["candidates"], before["candidates"])
        self.assertEqual(after["focus"], before["focus"])
        self.assertEqual((after["flag"], after["estado"]), (before["flag"], before["estado"]))
        # 6 the intended deferred-interest transition: no commercial mutation, cursor persisted
        self.assertEqual(after["stage"], "QUALIFYING")
        self.assertNotEqual(after["stage"], "BOOKED")
        self.assertIsNone(after["preferred_day"])     # 7 not set from the model's extraction
        self.assertIsNone(after["preferred_time"])
        self.assertFalse(after["needs_human"])         # NU-6: no needs_human
        self.assertFalse(after["lead_needs_human"])
        self.assertEqual(after["cursor"], ev.wa_message_id)
        with Session(self.eng) as db:
            row = db.execute(select(WhatsAppMessage).where(
                WhatsAppMessage.thread_id == tid,
                WhatsAppMessage.direction == "out")).scalars().all()[-1]
            self.assertEqual(row.text, sent)
            self.assertNotEqual(row.status, "blocked")
            self.assertEqual(row.path_id, "CE_TEXT")   # 11 through the outbound safety gate
            ai = db.execute(select(AiEvent).where(
                AiEvent.wa_message_id == ev.wa_message_id)).scalar_one()
            self.assertNotEqual(ai.status, "triggered")

    def test_02_retry_of_the_same_inbound_does_not_duplicate_the_reply(self):
        tid, wa = _seed(self.eng)
        ev = _inbound(self.eng, tid, wa)
        out1, _ce, m1, _f, _c = _run(self.eng, ev)
        mid = _snapshot(self.eng, tid)
        out2, _ce, m2, flows, calls = _run(self.eng, ev)
        after = _snapshot(self.eng, tid)
        self.assertEqual(out1.action, "replied")
        self.assertEqual(out2.action, "skipped_dedup")
        self.assertIsInstance(out2.latency_ce_ms, int)
        self.assertEqual(m2.sent, [])
        self.assertEqual(calls["interpreter"], 0)
        self.assertEqual(flows, [])
        self.assertEqual(after, mid)

    def test_03_needs_human_remains_authoritative(self):
        tid, wa = _seed(self.eng, needs_human=True)
        before = _snapshot(self.eng, tid)
        ev = _inbound(self.eng, tid, wa)
        out, _ce, m, flows, calls = _run(self.eng, ev)
        after = _snapshot(self.eng, tid)
        self.assertEqual(out.action, "skipped_human")
        self.assertIsInstance(out.latency_ce_ms, int)
        self.assertEqual(calls["interpreter"], 0)
        self.assertEqual(calls["process_text"], [])
        self.assertEqual(m.sent, [])
        self.assertEqual(flows, [])
        self.assertTrue(after["needs_human"])
        self.assertEqual(after["cursor"], ev.wa_message_id)
        self.assertEqual({k: v for k, v in after.items() if k != "cursor"},
                         {k: v for k, v in before.items() if k != "cursor"})

    def test_04_all_eight_launch_switches_false(self):
        """OUTBOUND_ENABLED=false plus the seven settings switches false: the deferred turn
        still returns the declared shape, the gate refuses the send, nothing reaches Meta."""
        tid, wa = _seed(self.eng)
        before = _snapshot(self.eng, tid)
        ev = _inbound(self.eng, tid, wa)
        out, _ce, m, flows, calls = _run(self.eng, ev, outbound=False,
                                         cfg=_all_switches_off_cfg())
        after = _snapshot(self.eng, tid)
        self.assertIsInstance(calls["process_text"][0], ConversationHandleOut)
        self.assertEqual(out.action, "service_gate_blocked", out)
        self.assertIsInstance(out.latency_ce_ms, int)
        self.assertEqual(m.sent, [])
        self.assertEqual(m.egress, [])
        self.assertEqual(flows, [])
        self.assertEqual((after["revisions"], after["offers"]), (0, 0))
        self.assertEqual(after["stage"], "QUALIFYING")
        self.assertIsNone(after["token"])
        self.assertEqual(after["cursor"], ev.wa_message_id)
        # the kill switch records exactly one BLOCKED ledger row and nothing reaches Meta
        self.assertEqual(after["out_rows"], before["out_rows"] + 1)
        with Session(self.eng) as db:
            rows = db.execute(select(WhatsAppMessage).where(
                WhatsAppMessage.thread_id == tid,
                WhatsAppMessage.direction == "out")).scalars().all()
            self.assertEqual([r.status for r in rows], ["blocked"])
            self.assertEqual(rows[0].path_id, "CE_TEXT")
        # and the retry is still deduplicated
        out2, _ce, m2, _f, calls2 = _run(self.eng, ev, outbound=False,
                                         cfg=_all_switches_off_cfg())
        self.assertEqual(out2.action, "skipped_dedup")
        self.assertEqual((m2.sent, calls2["interpreter"]), ([], 0))


class TestR1AReturnContract(unittest.TestCase):

    def _process_text(self):
        tree = ast.parse(SRC)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_process_text":
                return node
        self.fail("_process_text not found")

    def test_05_process_text_declares_and_returns_one_result_object(self):
        fn = self._process_text()
        self.assertEqual(ast.unparse(fn.returns), "ConversationHandleOut")
        own = []

        def visit(n):
            for ch in ast.iter_child_nodes(n):
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    continue
                if isinstance(ch, ast.Return):
                    own.append(ch)
                visit(ch)
        visit(fn)
        self.assertGreater(len(own), 20)
        tuples = [ast.unparse(r) for r in own if isinstance(r.value, ast.Tuple)]
        self.assertEqual(tuples, [])
        bare = [r.lineno for r in own if r.value is None]
        self.assertEqual(bare, [])

    def test_06_deferred_intercept_returns_the_handler_result(self):
        self.assertIn("            return self._handle_deferred_interest(ctx, state)\n", SRC)
        self.assertNotIn("_handle_deferred_interest(ctx, state), True", SRC)
        src = textwrap.dedent(ast.get_source_segment(SRC, next(
            n for n in ast.walk(ast.parse(SRC))
            if isinstance(n, ast.FunctionDef) and n.name == "_handle_deferred_interest")))
        self.assertIn("-> \"ConversationHandleOut\"", src)
        # R6-C4B-R1B: the handler chooses the focus / no-focus copy, still a service boundary
        self.assertIn("return self._send_service_boundary( ctx, _DEFERRED_RESPONSE_FOCUS_ES if "
                      "has_focus else DEFERRED_RESPONSE_ES)", " ".join(src.split()))


if __name__ == "__main__":
    unittest.main()
