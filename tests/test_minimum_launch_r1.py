"""PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION-R1 — the SQLite half.

The independent review (audit 2026-11-03) found a recovery claim that could be SILENT (F-01),
provider calls inside the Data Exchange request on the event loop (F-02), a silent loss of
the Flow details (F-03), stored HTML injection on the CRM card (F-04), lead-identity callers
that 500 or discard (F-11) and lost "fresh IV is a new attempt" coverage (F-10). This suite
proves the corrections with production functions and provider fakes at the network edge only
(no socket is ever opened; synthetic `@example.invalid` / `54911…` fixtures).

  W  crash windows 1–8 (process death = a BaseException raised at the boundary), each
     followed by the worker's lease-expiry resume
  L  Data Exchange latency with slow provider fakes; the event loop is not blocked; the
     total transport deadline; one stuck job blocks neither other jobs nor notifications
  F  human-flag ownership: raised at admission, cleared only when this job raised it
  O  /api/ops/booking-attention lists every incomplete recovery and condition, without PII
  G  detail persistence at confirm_booking and its failure behaviour
  E  CRM card HTML escaping
  K  lead-identity callers (typed conflict, digit-free phones)
  M  a fresh delivery on a still-ISSUED offer is a new attempt, counted once
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import threading
import time as _time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _sb2b_harness as H  # noqa: E402
import test_minimum_launch as ML  # noqa: E402

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (BookingAttentionCondition, Lead, Revision, SlotRecoveryJob,  # noqa: E402
                        ThreadRevision, WhatsAppBookingOffer, WhatsAppMessage,
                        WhatsAppThreadState)
from app.services import booking_flow_service as bfs  # noqa: E402
from app.services import booking_slot_recovery as R  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


class _Crash(BaseException):
    """A simulated process death: not an Exception, so no handler in the code catches it."""


class Edge(ML.Meta):
    """The provider edge with per-call behaviour for text and Flow:
    ok | 4xx | timeout | crash (Meta ACCEPTED, then the process died) | sleep:<s> | block."""

    def __init__(self, text=("ok",), flow=("ok",)):
        super().__init__(text_modes=("ok",), flow_modes=("ok",))
        self.text_plan, self.flow_plan = list(text), list(flow)
        self.release = threading.Event()
        self.calls: list = []

    def _behave(self, plan, kind):
        mode = plan.pop(0) if len(plan) > 1 else plan[0]
        self.calls.append((kind, mode, _time.monotonic()))
        if mode.startswith("sleep:"):
            _time.sleep(float(mode.split(":", 1)[1]))
        elif mode == "block":
            self.release.wait(30)
        return mode

    def _send(self, *, to_wa_id, text):
        mode = self._behave(self.text_plan, "text")
        self.mode = {"crash": "ok"}.get(mode, mode if mode in ("4xx", "timeout") else "ok")
        out = H.Meta._send(self, to_wa_id=to_wa_id, text=text)
        if mode == "crash":
            raise _Crash("process died after Meta accepted the text")
        return out

    def _flow(self, *, to_wa_id, flow_id, flow_token, body_text, cta_label="",
              initial_screen="", mode=None):
        m = self._behave(self.flow_plan, "flow")
        self.flow_modes = ["4xx" if m == "4xx" else "timeout" if m == "timeout" else "ok"]
        out = ML.Meta._flow(self, to_wa_id=to_wa_id, flow_id=flow_id, flow_token=flow_token,
                            body_text=body_text, cta_label=cta_label,
                            initial_screen=initial_screen, mode=mode)
        if m == "crash":
            raise _Crash("process died after Meta accepted the Flow")
        return out


def later(minutes=5):
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def work(engine, now=None):
    with Session(engine) as db, mock.patch("app.settings.get_settings",
                                           lambda: H.settings(flag=True)):
        return R.run_once(db, now=now)


#: One booking's service + buffer blocks 90 minutes; these are the harness day's bookable
#: starts one after another (a04 already proves 11:30 after 10:00).
_SLOTS = (H.time(10, 0), H.time(11, 30), H.time(13, 0))


def lose_race(engine, *, attempt_ref=None, same_as_prepare=False):
    """Winner books; the loser's confirm reaches a typed SLOT_CONFLICT and ADMITS a job.
    Every race on one engine uses its own slot."""
    n = getattr(engine, "_r1_races", 0)
    engine._r1_races = n + 1
    at = _SLOTS[n]
    win, lose = H.seed(engine, at=at), H.seed(engine, at=at)
    with ML.Meta():
        ML.confirm(engine, win, same_as_prepare=same_as_prepare)
    with ML.Meta() as m:
        try:
            ML.confirm(engine, lose, attempt_ref=attempt_ref, same_as_prepare=same_as_prepare,
                       name="Bruno Fixture" if same_as_prepare else "Ana Fixture")
            raise AssertionError("the loser booked")
        except bfs.BookingSlotConflictError:
            pass
        assert (m.sent, m.flows) == ([], []), "a provider call inside Data Exchange"
    return win, lose


def job(engine, thread_id):
    with Session(engine) as db:
        j = db.execute(select(SlotRecoveryJob).where(
            SlotRecoveryJob.thread_id == thread_id)).scalars().all()
        assert len(j) <= 1, "more than one recovery job for one thread"
        if j:
            db.expunge(j[0])
        return j[0] if j else None


def attention(engine):
    with Session(engine) as db:
        return R.recovery_attention(db, now=datetime.now(timezone.utc)), \
            R.condition_attention(db)


def flags(engine, w):
    with Session(engine) as db:
        st = db.execute(select(WhatsAppThreadState).where(
            WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
        return bool(st.needs_human), bool(db.get(Lead, w.lead_id).necesita_humano), st.last_stage


def sent_per_identity(engine, thread_id):
    """{delivery identity: number of rows the gate recorded as SENT} — must never exceed 1."""
    with Session(engine) as db:
        rows = db.execute(select(WhatsAppMessage.content_fingerprint, func.count())
                          .where(WhatsAppMessage.thread_id == thread_id,
                                 WhatsAppMessage.direction == "out",
                                 WhatsAppMessage.status == "sent")
                          .group_by(WhatsAppMessage.content_fingerprint)).all()
        return dict(rows)


class _Base(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()

    def assert_owed_and_visible(self, lose, *, listed_reason=None):
        """Windows 2–8: the loser booked nothing, has exactly one job, is flagged for a
        person and is listed on the ops surface."""
        with Session(self.engine) as db:
            self.assertEqual(db.query(Revision).filter(Revision.lead_id == lose.lead_id).count(), 0)
            self.assertEqual(db.query(ThreadRevision).filter(
                ThreadRevision.thread_id == lose.thread_id).count(), 0)
            self.assertEqual(db.query(SlotRecoveryJob).filter(
                SlotRecoveryJob.thread_id == lose.thread_id).count(), 1)
        needs, lead_flag, _stage = flags(self.engine, lose)
        self.assertTrue(needs or lead_flag, "the thread is not visible as needing a person")
        rows, _ = attention(self.engine)
        mine = [r for r in rows if r["thread_id"] == lose.thread_id]
        self.assertEqual(len(mine), 1, rows)
        self.assertEqual(mine[0]["thread_url"], f"/whatsapp/thread/{lose.thread_id}")
        if listed_reason:
            self.assertIn(listed_reason, mine[0]["reasons"])
        return mine[0]

    def assert_settled(self, lose):
        """After the resume: terminal; at most one success per identity; a degraded end is
        still listed (never silent)."""
        j = job(self.engine, lose.thread_id)
        self.assertIn(j.state, R.TERMINAL)
        self.assertTrue(all(n <= 1 for n in sent_per_identity(self.engine, lose.thread_id)
                            .values()))
        rows, _ = attention(self.engine)
        listed = [r for r in rows if r["thread_id"] == lose.thread_id]
        self.assertEqual(bool(listed), j.state != "RECOVERED")
        return j


# ══ W — crash windows ═══════════════════════════════════════════════════════════════════

class W_CrashWindows(_Base):

    def test_w1_death_before_admission_self_heals_on_the_meta_retry(self):
        win, lose = H.seed(self.engine), H.seed(self.engine)
        with ML.Meta():
            ML.confirm(self.engine, win)
        ref = "dx:" + "1" * 64
        with ML.Meta(), mock.patch.object(R, "admit_recovery", side_effect=_Crash("died")):
            with self.assertRaises(_Crash):
                ML.confirm(self.engine, lose, attempt_ref=ref)
        self.assertIsNone(job(self.engine, lose.thread_id))     # nothing durable to list
        with ML.Meta():                                          # Meta retries the delivery
            with self.assertRaises(bfs.BookingSlotConflictError):
                ML.confirm(self.engine, lose, attempt_ref=ref)
        self.assert_owed_and_visible(lose, listed_reason="SLOT_RECOVERY_PENDING")
        with Edge() as e:
            work(self.engine)
        self.assertEqual(self.assert_settled(lose).state, "RECOVERED")
        self.assertEqual([k for k, *_ in e.calls], ["text", "flow"])

    def test_w2_death_after_admission_before_text(self):
        win, lose = lose_race(self.engine)
        self.assert_owed_and_visible(lose, listed_reason="SLOT_RECOVERY_PENDING")
        with Edge() as e, mock.patch.object(R, "_send_text", side_effect=_Crash("died")):
            with self.assertRaises(_Crash):
                work(self.engine)
        self.assertEqual(e.calls, [])
        row = self.assert_owed_and_visible(lose)
        self.assertEqual(row["state"], "PENDING")
        with Session(self.engine) as db:     # the dead claimant's lease, seen after it expires
            rows = R.recovery_attention(db, now=later())
        self.assertIn("SLOT_RECOVERY_LEASE_EXPIRED",
                      [r for r in rows if r["thread_id"] == lose.thread_id][0]["reasons"])
        with Edge() as e:
            work(self.engine, now=later())
        self.assertEqual(self.assert_settled(lose).state, "RECOVERED")
        self.assertEqual([k for k, *_ in e.calls], ["text", "flow"])

    def test_w3a_meta_accepted_the_text_then_death_before_any_record(self):
        win, lose = lose_race(self.engine)
        with Edge(text=("crash",)):
            with self.assertRaises(_Crash):
                work(self.engine)
        self.assertEqual(self.assert_owed_and_visible(lose)["state"], "SENDING_TEXT")
        with Edge() as e:
            work(self.engine, now=later())
        j = self.assert_settled(lose)
        # the gate holds a `pending` row for that identity: uncertain — never sent again
        self.assertEqual((j.state, j.outcome), ("HANDOFF", "TEXT_UNCERTAIN_LOST_SENDER"))
        self.assertEqual(e.calls, [])
        self.assertEqual(flags(self.engine, lose)[:2], (True, True))

    def test_w3b_text_recorded_by_the_gate_then_death_before_the_job_recorded_it(self):
        from app.services.outbound_safety_gate import OutboundSafetyGate
        win, lose = lose_race(self.engine)
        real = OutboundSafetyGate.mark_sent

        def mark_then_die(self_, *a, **k):
            real(self_, *a, **k)
            raise _Crash("died after the gate recorded SENT")
        with Edge() as e, mock.patch.object(OutboundSafetyGate, "mark_sent", mark_then_die):
            with self.assertRaises(_Crash):
                work(self.engine)
        self.assertEqual(self.assert_owed_and_visible(lose)["state"], "SENDING_TEXT")
        with Edge() as e2:
            work(self.engine, now=later())
        j = self.assert_settled(lose)
        self.assertEqual(j.state, "RECOVERED")
        # one text in total (the first), then the Flow — the text was never re-sent
        self.assertEqual([k for k, *_ in e.calls] + [k for k, *_ in e2.calls], ["text", "flow"])

    def test_w4_death_after_the_text_was_recorded_before_the_fresh_offer(self):
        win, lose = lose_race(self.engine)
        with Edge() as e, mock.patch.object(R, "_issue_fresh_offer", side_effect=_Crash("x")):
            with self.assertRaises(_Crash):
                work(self.engine)
        row = self.assert_owed_and_visible(lose)
        self.assertEqual(row["state"], "TEXT_SENT")   # told "elegí otro" — and still owed
        self.assertEqual([x.status for x in ML.offers(self.engine, lose.thread_id)], ["ISSUED"])
        with Edge() as e2:
            work(self.engine, now=later())
        self.assertEqual(self.assert_settled(lose).state, "RECOVERED")
        self.assertEqual([k for k, *_ in e.calls], ["text"])
        self.assertEqual([k for k, *_ in e2.calls], ["flow"])

    def test_w5_death_after_the_new_offer_commit_before_the_flow(self):
        win, lose = lose_race(self.engine)
        with Edge() as e, mock.patch.object(R, "_send_flow", side_effect=_Crash("x")):
            with self.assertRaises(_Crash):
                work(self.engine)
        self.assertEqual(self.assert_owed_and_visible(lose)["state"], "SENDING_FLOW")
        self.assertEqual([x.status for x in ML.offers(self.engine, lose.thread_id)],
                         ["SUPERSEDED", "ISSUED"])
        with Edge() as e2:
            work(self.engine, now=later())
        self.assertEqual(self.assert_settled(lose).state, "RECOVERED")
        self.assertEqual([k for k, *_ in e.calls] + [k for k, *_ in e2.calls], ["text", "flow"])
        # the SAME fresh offer was dispatched — no third offer
        self.assertEqual([x.status for x in ML.offers(self.engine, lose.thread_id)],
                         ["SUPERSEDED", "ISSUED"])

    def test_w6a_flow_accepted_and_recorded_then_death_before_completion(self):
        win, lose = lose_race(self.engine)
        with Edge() as e, mock.patch.object(R, "_recovered", side_effect=_Crash("x")):
            with self.assertRaises(_Crash):
                work(self.engine)
        self.assertEqual(self.assert_owed_and_visible(lose)["state"], "SENDING_FLOW")
        with Edge() as e2:
            work(self.engine, now=later())
        self.assertEqual(self.assert_settled(lose).state, "RECOVERED")
        self.assertEqual([k for k, *_ in e.calls], ["text", "flow"])
        self.assertEqual(e2.calls, [])                           # no second Flow

    def test_w6b_meta_accepted_the_flow_then_death_before_any_record(self):
        win, lose = lose_race(self.engine)
        with Edge(flow=("crash",)):
            with self.assertRaises(_Crash):
                work(self.engine)
        with Edge() as e2:
            work(self.engine, now=later())
        j = self.assert_settled(lose)
        self.assertEqual((j.state, j.outcome), ("HANDOFF", "FLOW_UNCERTAIN_LOST_SENDER"))
        self.assertEqual(e2.calls, [])
        self.assertEqual(flags(self.engine, lose)[:2], (True, True))

    def test_w7_unexpected_exception_after_admission_is_listed_then_failed(self):
        win, lose = lose_race(self.engine)
        with Edge() as e, mock.patch.object(R, "_ctx", side_effect=RuntimeError("boom")):
            work(self.engine)
            row = self.assert_owed_and_visible(lose, listed_reason="SLOT_RECOVERY_INTERNAL")
            self.assertEqual(row["last_error"], "INTERNAL_RuntimeError")
            for n in range(2, 8):
                work(self.engine, now=later(10 * n))
        self.assertEqual(e.calls, [])
        j = self.assert_settled(lose)
        self.assertEqual((j.state, j.outcome), ("FAILED", "INTERNAL_ERROR"))
        rows, _ = attention(self.engine)
        self.assertIn("SLOT_RECOVERY_INTERNAL",
                      [r for r in rows if r["thread_id"] == lose.thread_id][0]["reasons"])
        self.assertEqual(flags(self.engine, lose)[:2], (True, True))

    def test_w8a_k1_contended_at_admission_and_after_exhausts_bounded_retries(self):
        with mock.patch.object(R, "_k1", return_value="OFFER_LOCK_TIMEOUT"):
            win, lose = lose_race(self.engine)
            rows, _ = attention(self.engine)
            mine = [r for r in rows if r["thread_id"] == lose.thread_id][0]
            self.assertIn("SLOT_RECOVERY_FLAGS_NOT_RAISED", mine["reasons"])
            with Edge() as e:
                for n in range(0, 8):
                    work(self.engine, now=later(10 * n))
        self.assertEqual(e.calls, [])                 # nothing is sent before the flags
        j = job(self.engine, lose.thread_id)
        self.assertEqual((j.state, j.outcome), ("FAILED", "HANDOFF_LOCK_CONTENDED"))
        rows, _ = attention(self.engine)
        mine = [r for r in rows if r["thread_id"] == lose.thread_id][0]
        self.assertIn("SLOT_RECOVERY_LOCK_CONTENDED", mine["reasons"])
        self.assertTrue(flags(self.engine, lose)[1], "the lead flag is raised best-effort")

    def test_w8b_k1_contended_once_at_the_fresh_offer_then_recovers(self):
        win, lose = lose_race(self.engine)
        real, seen = R._k1, []

        def once(s, thread_id):
            seen.append(1)
            return "OFFER_LOCK_TIMEOUT" if len(seen) == 1 else real(s, thread_id)
        with Edge() as e, mock.patch.object(R, "_k1", once):
            work(self.engine)
            row = self.assert_owed_and_visible(lose, listed_reason="SLOT_RECOVERY_LOCK_CONTENDED")
            self.assertEqual(row["state"], "TEXT_SENT")
            work(self.engine, now=later())
        self.assertEqual(self.assert_settled(lose).state, "RECOVERED")
        self.assertEqual([k for k, *_ in e.calls], ["text", "flow"])

    def test_w9_replay_after_every_window_sends_nothing_more(self):
        """The losing delivery replayed after the recovery completed: same screen, nothing."""
        ref = "dx:" + "9" * 64
        win, lose = lose_race(self.engine, attempt_ref=ref)
        with Edge():
            work(self.engine)
        before = (ML.outbound(self.engine, lose.thread_id), ML.offers(self.engine, lose.thread_id))
        with Edge() as e:
            with self.assertRaises(bfs.BookingSlotConflictError):
                ML.confirm(self.engine, lose, attempt_ref=ref)
            work(self.engine, now=later())
        self.assertEqual(e.calls, [])
        self.assertEqual((ML.outbound(self.engine, lose.thread_id),
                          ML.offers(self.engine, lose.thread_id)), before)


# ══ L — latency, the event loop, the transport deadline ═════════════════════════════════

class L_Latency(_Base):

    def test_l1_data_exchange_returns_before_any_provider_call_with_slow_fakes(self):
        win, lose = H.seed(self.engine), H.seed(self.engine)
        with ML.Meta():
            ML.confirm(self.engine, win)
        with Edge(text=("sleep:1.5",), flow=("sleep:1.5",)) as e:
            t0 = _time.monotonic()
            with self.assertRaises(bfs.BookingSlotConflictError):
                ML.confirm(self.engine, lose)
            dx_elapsed = _time.monotonic() - t0
            calls_at_return = list(e.calls)
            t1 = _time.monotonic()
            work(self.engine)
            worker_elapsed = _time.monotonic() - t1
        print(f"\nDX_RESPONSE_SECONDS={dx_elapsed:.3f} WORKER_SECONDS={worker_elapsed:.3f}")
        self.assertEqual(calls_at_return, [], "a provider call before the DX response")
        self.assertLess(dx_elapsed, 1.0)
        self.assertGreaterEqual(worker_elapsed, 3.0)        # the slow sends happened here
        self.assertEqual(job(self.engine, lose.thread_id).state, "RECOVERED")
        self.assertTrue(all(c[2] >= t1 for c in e.calls))

    def test_l2_a_stuck_provider_call_is_bounded_by_the_total_deadline(self):
        win, lose = lose_race(self.engine)
        edge = Edge(text=("block",))
        with edge, mock.patch.object(R, "TRANSPORT_DEADLINE_SECONDS", 0.5):
            t0 = _time.monotonic()
            work(self.engine)
            elapsed = _time.monotonic() - t0
            self.assertEqual(R.inflight_transports(), 1)    # abandoned, NOT cancelled
            j = job(self.engine, lose.thread_id)
            self.assertEqual((j.state, j.outcome), ("HANDOFF", "TEXT_TRANSPORT_DEADLINE"))
            self.assertEqual(j.last_error, "TRANSPORT_DEADLINE_EXCEEDED")
            before = ML.outbound(self.engine, lose.thread_id)
            edge.release.set()                              # the orphan finishes late
            for _ in range(50):
                if R.inflight_transports() == 0:
                    break
                _time.sleep(0.05)
        print(f"\nDEADLINE_BOUNDED_SECONDS={elapsed:.3f}")
        self.assertLess(elapsed, 2.0)
        self.assertEqual(R.inflight_transports(), 0)
        self.assertEqual(ML.outbound(self.engine, lose.thread_id), before,
                         "a late result was recorded")
        self.assertEqual([k for k, *_ in edge.calls], ["text"])   # no retry, no Flow
        self.assertEqual(flags(self.engine, lose)[:2], (True, True))
        self.assertTrue([r for r in attention(self.engine)[0] if r["thread_id"] == lose.thread_id])

    def test_l3_one_stuck_job_blocks_neither_other_jobs_nor_operator_notifications(self):
        from app.services import operator_notification as N
        w1, l1 = lose_race(self.engine)
        w2, l2 = lose_race(self.engine)
        edge = Edge(text=("block", "ok"))
        with edge, mock.patch.object(R, "TRANSPORT_DEADLINE_SECONDS", 0.5):
            work(self.engine)
            with Session(self.engine) as db:                # the notification pass still runs
                N.run_once(db)
            edge.release.set()
        self.assertEqual(job(self.engine, l1.thread_id).outcome, "TEXT_TRANSPORT_DEADLINE")
        self.assertEqual(job(self.engine, l2.thread_id).state, "RECOVERED")

    def test_l4_no_free_transport_slot_defers_and_nothing_is_claimed_into_a_transport(self):
        win, lose = lose_race(self.engine)
        with mock.patch.object(R, "transport_capacity_available", return_value=False), Edge() as e:
            work(self.engine)
        self.assertEqual(e.calls, [])
        self.assertEqual(job(self.engine, lose.thread_id).state, "PENDING")
        with Edge():
            work(self.engine, now=later())
        self.assertEqual(job(self.engine, lose.thread_id).state, "RECOVERED")

    def test_l5_the_event_loop_is_not_blocked_by_a_slow_data_exchange_body(self):
        from app.routes import flow_data_exchange as route

        class Req:
            async def json(self):
                return {"any": "body"}

        class SlowService:                       # the synchronous service boundary
            def __init__(self, db):
                pass

            def handle_init(self, token):
                _time.sleep(1.0)
                return {"screen": "APPOINTMENT"}
        ticks = []

        async def ticker():
            for _ in range(15):
                ticks.append(_time.monotonic())
                await asyncio.sleep(0.05)

        async def scenario():
            t = asyncio.create_task(ticker())
            out = await route.booking_flow_data_exchange(Req(), db=None)
            await t
            return out
        with mock.patch.object(route, "decrypt_flow_request",
                               lambda body: ({"action": "INIT", "flow_token": "t"}, b"k", b"i")), \
                mock.patch.object(route, "BookingFlowService", SlowService), \
                mock.patch.object(route, "_guarded_return", lambda result, *a, **k: result):
            out = asyncio.run(scenario())
        self.assertEqual(out, {"screen": "APPOINTMENT"})
        # the loop kept running while the synchronous body slept on the threadpool
        self.assertGreaterEqual(len([x for x in ticks if x - ticks[0] < 1.0]), 10)

    def test_l6_the_data_exchange_path_contains_no_provider_transport(self):
        src = (ROOT / "backend/app/services/booking_flow_service.py").read_text()
        body = src[src.index("    def _recover_slot_conflict"):
                   src.index("    def _persist_confirm_details")]
        for forbidden in ("_send_whatsapp_cloud", "run_once", "_send_ledger_acknowledgement",
                          "import recover_slot_conflict", "bounded_call"):
            self.assertNotIn(forbidden, body)
        route = (ROOT / "backend/app/routes/flow_data_exchange.py").read_text()
        self.assertIn("result = await run_in_threadpool(_dispatch)", route)

    def test_l7_bounded_call_returns_errors_and_results_and_abandons_on_deadline(self):
        self.assertEqual(R.bounded_call(lambda x: x + 1, {"x": 1}, 1.0), 2)
        with self.assertRaises(ValueError):
            R.bounded_call(lambda: (_ for _ in ()).throw(ValueError("v")), {}, 1.0)
        gate = threading.Event()
        t0 = _time.monotonic()
        with self.assertRaises(R.TransportDeadlineExceeded):
            R.bounded_call(lambda: gate.wait(5), {}, 0.2)
        self.assertLess(_time.monotonic() - t0, 1.0)
        gate.set()


# ══ F — human-flag ownership ════════════════════════════════════════════════════════════

class F_FlagOwnership(_Base):

    def test_f1_admission_raises_both_flags_and_scheduling_before_anything_is_sent(self):
        win, lose = lose_race(self.engine)
        self.assertEqual(flags(self.engine, lose), (True, True, "SCHEDULING"))
        j = job(self.engine, lose.thread_id)
        self.assertEqual((j.state, j.attention_applied, j.owns_state_flag, j.owns_lead_flag),
                         ("PENDING", True, True, True))

    def test_f2_recovered_clears_only_what_it_raised(self):
        win, lose = lose_race(self.engine)
        with Edge():
            work(self.engine)
        self.assertEqual(flags(self.engine, lose)[:2], (False, False))

    def test_f3_a_pre_existing_flag_is_never_cleared(self):
        win, lose = H.seed(self.engine), H.seed(self.engine, needs_human=True)
        with Session(self.engine) as db:
            db.get(Lead, lose.lead_id).necesita_humano = True
            db.commit()
        with ML.Meta():
            ML.confirm(self.engine, win)
        with ML.Meta(), self.assertRaises(bfs.BookingSlotConflictError):
            ML.confirm(self.engine, lose)
        j = job(self.engine, lose.thread_id)
        self.assertEqual((j.owns_state_flag, j.owns_lead_flag), (False, False))
        with Edge():
            work(self.engine)
        self.assertEqual(job(self.engine, lose.thread_id).state, "RECOVERED")
        self.assertEqual(flags(self.engine, lose)[:2], (True, True))

    def test_f4_a_turn_processed_after_admission_keeps_the_flags(self):
        win, lose = lose_race(self.engine)
        with Session(self.engine) as db:            # a CE turn ran (and may have handed off)
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == lose.thread_id)).scalar_one()
            st.last_processed_inbound_wa_message_id = "wamid.R1.NEWER"
            db.commit()
        with Edge():
            work(self.engine)
        j = job(self.engine, lose.thread_id)
        self.assertEqual((j.state, j.last_error), ("RECOVERED", "FLAGS_KEPT_OTHER_ATTENTION"))
        self.assertEqual(flags(self.engine, lose)[:2], (True, True))

    def test_f5_another_active_condition_keeps_the_flags(self):
        from app.services.booking_flow_details import record_details_condition
        win, lose = lose_race(self.engine)
        with Session(self.engine) as db:
            record_details_condition(db, thread_id=lose.thread_id, lead_id=lose.lead_id,
                                     offer_id=None, attempt_ref="x", reason="TEST")
        with Edge():
            work(self.engine)
        self.assertEqual(flags(self.engine, lose)[:2], (True, True))

    def test_f6_handoff_and_failed_keep_the_flags(self):
        win, lose = lose_race(self.engine)
        with Edge(text=("timeout",)):
            work(self.engine)
        self.assertEqual(job(self.engine, lose.thread_id).state, "HANDOFF")
        self.assertEqual(flags(self.engine, lose), (True, True, "SCHEDULING"))


# ══ O — the ops attention surface ═══════════════════════════════════════════════════════

class O_OpsAttention(_Base):

    def test_o1_route_lists_pending_and_degraded_and_omits_recovered(self):
        from app.routes.ops_dashboard import get_booking_attention
        _w1, pending = lose_race(self.engine)
        _w2, handed = lose_race(self.engine)
        with Session(self.engine) as db:      # only `handed` is processed (timeout → HANDOFF)
            db.query(SlotRecoveryJob).filter(SlotRecoveryJob.thread_id == pending.thread_id) \
                .update({"next_attempt_at": later(60)})
            db.commit()
        with Edge(text=("timeout",)):
            work(self.engine)
        _w3, recovered = lose_race(self.engine)
        with Session(self.engine) as db:
            db.query(SlotRecoveryJob).filter(SlotRecoveryJob.thread_id == pending.thread_id) \
                .update({"next_attempt_at": later(60)})
            db.commit()
        with Edge():
            work(self.engine)
        with Session(self.engine) as db:
            out = get_booking_attention(db=db, _operator="op")
        by = {r["thread_id"]: r for r in out["slot_recoveries"]}
        self.assertEqual(set(by), {pending.thread_id, handed.thread_id})
        self.assertEqual(by[pending.thread_id]["reasons"], ["SLOT_RECOVERY_PENDING"])
        self.assertEqual(by[handed.thread_id]["reasons"], ["SLOT_RECOVERY_HANDOFF"])
        self.assertEqual(by[handed.thread_id]["outcome"], "TEXT_UNCERTAIN")
        self.assertEqual(out["slot_recovery_count"], 2)

    def test_o2_a_pending_job_older_than_the_bound_is_stale(self):
        _w, lose = lose_race(self.engine)
        with Session(self.engine) as db:
            rows = R.recovery_attention(db, now=later(10))
        self.assertIn("SLOT_RECOVERY_STALE", rows[0]["reasons"])

    def test_o3_no_customer_pii_in_the_rows(self):
        _w, lose = lose_race(self.engine, same_as_prepare=True)
        with Edge(text=("timeout",)):
            work(self.engine)
        blob = json.dumps(attention(self.engine), default=str)
        for pii in (lose.wa_id, "Bruno", "example.invalid", "Siempreviva", "1100000000",
                    R.RECOVERY_TEXT[:20]):
            self.assertNotIn(pii, blob)


# ══ G — detail persistence ══════════════════════════════════════════════════════════════

class G_Details(_Base):

    def live(self, w):
        from app.services.booking_flow_details import live_details
        with Session(self.engine) as db:
            return live_details(db, [w.thread_id]).get(w.thread_id, {})

    def conditions(self, w=None):
        with Session(self.engine) as db:
            q = db.query(BookingAttentionCondition)
            if w is not None:
                q = q.filter(BookingAttentionCondition.thread_id == w.thread_id)
            return [(c.kind, c.reason, c.flags_applied) for c in q.order_by(
                BookingAttentionCondition.id)]

    def test_g1_prepare_summary_db_failure_is_recovered_at_confirm(self):
        from app.services import booking_flow_details as D
        w = H.seed(self.engine)
        with mock.patch.object(D, "persist_flow_details", side_effect=RuntimeError("db")):
            self.assertEqual(ML.prepare(self.engine, w)["screen"], "SUMMARY")
        self.assertEqual(self.live(w), {})
        with ML.Meta():
            self.assertEqual(ML.confirm(self.engine, w, name="Bruno Fixture",
                                        same_as_prepare=True)["screen"], "SUCCESS")
        live = self.live(w)
        self.assertEqual((live["FLOW_CUSTOMER_NAME"], live["FLOW_SELLER_PHONE"]),
                         ("Bruno Fixture", "1100000000"))
        self.assertEqual(self.conditions(w), [])

    def test_g2_confirm_persistence_failure_is_flagged_and_the_booking_is_canonical(self):
        from app.services import booking_flow_details as D
        from app.services.lead_booking_details import lead_booking_details
        from app.ui.kanban_view import render_lead_card
        w = H.seed(self.engine)
        failed = D.PersistResult(False, reason="OperationalError")
        with mock.patch.object(D, "persist_flow_details", return_value=failed), ML.Meta():
            ML.prepare(self.engine, w)
            screen = ML.confirm(self.engine, w, name="Bruno Fixture", same_as_prepare=True)
        # booking MAY complete: its own transaction wrote the canonical copy
        self.assertEqual(screen["screen"], "SUCCESS")
        self.assertEqual(self.conditions(w),
                         [("DETAILS_NOT_PERSISTED", "CONFIRM_OperationalError", True)])
        self.assertEqual(flags(self.engine, w)[:2], (True, True))
        with Session(self.engine) as db:
            tr = db.execute(select(ThreadRevision).where(
                ThreadRevision.thread_id == w.thread_id)).scalar_one()
            self.assertEqual((tr.buyer_name, tr.seller_name, tr.seller_phone, tr.address,
                              tr.publication_url),
                             ("Bruno Fixture", "Vendedor Fixture", "1100000000",
                              "Avenida Siempreviva 742", "https://example.invalid/aviso/1"))
            d = lead_booking_details(db, [w.lead_id])[w.lead_id]   # card falls back to it
            self.assertEqual((d.customer_name, d.seller_phone, d.inspection_address),
                             ("Bruno Fixture", "1100000000", "Avenida Siempreviva 742"))
            html = render_lead_card(db.get(Lead, w.lead_id), booking_details=d)
            self.assertIn("1100000000", html)
        _rows, conds = attention(self.engine)
        self.assertEqual([(c["thread_id"], c["kind"]) for c in conds],
                         [(w.thread_id, "DETAILS_NOT_PERSISTED")])
        self.assertNotIn("Bruno", json.dumps(conds))

    def test_g3_every_write_failing_flags_the_loser(self):
        """prepare_summary, confirm_booking AND the retry inside the recovery admission all
        fail: only then is the loss recorded — typed, listed, flagged."""
        from app.services import booking_flow_details as D
        failed = D.PersistResult(False, reason="OFFER_LOCK_TIMEOUT")
        with mock.patch.object(D, "persist_flow_details", return_value=failed), \
                mock.patch.object(D, "write_detail_rows", side_effect=RuntimeError("db")):
            win, lose = lose_race(self.engine, same_as_prepare=True)
        self.assertEqual(self.live(lose), {})
        self.assertEqual([c[:2] for c in self.conditions(lose)],
                         [("DETAILS_NOT_PERSISTED", "CONFIRM_OFFER_LOCK_TIMEOUT")])
        self.assertEqual(flags(self.engine, lose)[:2], (True, True))
        with Edge():
            work(self.engine)
        # the recovery completes but never clears the flags another condition still needs
        self.assertEqual(job(self.engine, lose.thread_id).state, "RECOVERED")
        self.assertEqual(flags(self.engine, lose)[:2], (True, True))

    def test_g4_winner_and_loser_both_keep_their_details_including_seller_phone(self):
        win, lose = H.seed(self.engine), H.seed(self.engine)
        with ML.Meta():
            ML.confirm(self.engine, win, name="Ana Ganadora", same_as_prepare=True)
        with ML.Meta(), self.assertRaises(bfs.BookingSlotConflictError):
            ML.confirm(self.engine, lose, name="Bruno Fixture", same_as_prepare=True)
        # no prepare_summary ran at all: confirm alone kept everything
        self.assertEqual(self.live(lose)["FLOW_SELLER_PHONE"], "1100000000")
        self.assertEqual(self.live(lose)["FLOW_CUSTOMER_NAME"], "Bruno Fixture")
        self.assertEqual(self.live(win)["FLOW_CUSTOMER_NAME"], "Ana Ganadora")
        with Session(self.engine) as db:
            self.assertEqual(db.execute(select(ThreadRevision.seller_phone).where(
                ThreadRevision.thread_id == win.thread_id)).scalar_one(), "1100000000")
        self.assertEqual(self.conditions(), [])

    def test_g5_retry_is_idempotent(self):
        from app.models import WhatsAppThreadEvidence as E
        from app.services import booking_flow_details as D
        win, lose = H.seed(self.engine), H.seed(self.engine)
        with ML.Meta():
            ML.confirm(self.engine, win)
        ref = "dx:" + "5" * 64

        def count():
            with Session(self.engine) as db:
                return db.query(E).filter(E.thread_id == lose.thread_id).count()
        with ML.Meta():
            for _ in range(3):
                with self.assertRaises(bfs.BookingSlotConflictError):
                    ML.confirm(self.engine, lose, attempt_ref=ref, same_as_prepare=True)
        n = count()
        self.assertGreater(n, 0)
        failed = D.PersistResult(False, reason="X")
        with mock.patch.object(D, "persist_flow_details", return_value=failed), \
                mock.patch.object(D, "write_detail_rows", side_effect=RuntimeError("db")), \
                ML.Meta():
            for _ in range(2):
                with self.assertRaises(bfs.BookingSlotConflictError):
                    ML.confirm(self.engine, lose, attempt_ref="dx:" + "6" * 64)
        self.assertEqual(count(), n)
        self.assertEqual(len(self.conditions(lose)), 1)        # one per delivery, not per retry

    def test_g7_a_confirm_write_that_met_k1_is_kept_by_the_recovery_admission(self):
        """The confirm-time write only TRIES K1; the loser's pending details are written in a
        savepoint of the admission transaction (which holds K1): kept, no condition."""
        from app.services import booking_flow_details as D
        real = D.persist_flow_details

        def contended_at_confirm(db, **kw):
            if kw.get("producer_version") == "confirm_booking/1":
                return D.PersistResult(False, reason="OFFER_LOCK_TIMEOUT")
            return real(db, **kw)
        with mock.patch.object(D, "persist_flow_details", contended_at_confirm):
            win, lose = lose_race(self.engine, same_as_prepare=True)
        live = self.live(lose)
        self.assertEqual((live["FLOW_CUSTOMER_NAME"], live["FLOW_SELLER_PHONE"]),
                         ("Bruno Fixture", "1100000000"))
        with Session(self.engine) as db:
            from app.models import WhatsAppThreadEvidence as E
            self.assertEqual({v for v, in db.execute(select(E.producer_version).where(
                E.thread_id == lose.thread_id))}, {"confirm_booking/1"})
        self.assertEqual(self.conditions(lose), [])

    def test_g8_a_winner_whose_confirm_write_failed_is_retried_after_the_commit(self):
        from app.services import booking_flow_details as D
        real, calls = D.persist_flow_details, []

        def first_fails(db, **kw):
            calls.append(kw.get("lock_timeout_ms"))
            if len(calls) == 1:
                return D.PersistResult(False, reason="OFFER_LOCK_TIMEOUT")
            return real(db, **kw)
        w = H.seed(self.engine)
        with mock.patch.object(D, "persist_flow_details", first_fails), ML.Meta():
            self.assertEqual(ML.confirm(self.engine, w, name="Bruno Fixture",
                                        same_as_prepare=True)["screen"], "SUCCESS")
        # the confirm-time try (short wait), then the post-commit retry with the default
        # bounded wait (no override passed: LOCK_TIMEOUT_MS)
        self.assertEqual(calls, [D.CONFIRM_LOCK_TIMEOUT_MS, None])
        self.assertEqual(self.live(w)["FLOW_SELLER_PHONE"], "1100000000")
        self.assertEqual(self.conditions(w), [])

    def test_g6_a_corrected_submission_supersedes(self):
        w = H.seed(self.engine)
        ML.prepare(self.engine, w)                              # Avenida Siempreviva 742
        with ML.Meta():
            ML.confirm(self.engine, w, same_as_prepare=False)  # Calle Falsa 123
        self.assertEqual(self.live(w)["FLOW_INSPECTION_ADDRESS"], "Calle Falsa 123")


# ══ E — CRM card escaping ═══════════════════════════════════════════════════════════════

PAYLOAD = '<img src=x onerror=alert(1)>"\'<script>alert(2)</script>'


class E_Escaping(_Base):

    def test_e1_every_customer_derived_value_is_escaped_exactly_once(self):
        from app.services.lead_booking_details import LeadBookingDetails
        from app.ui.kanban_view import render_lead_card
        w = H.seed(self.engine)
        with Session(self.engine) as db:
            lead = db.get(Lead, w.lead_id)
            lead.nombre, lead.apellido = "N" + PAYLOAD, "A" + PAYLOAD
            lead.telefono, lead.email = "T" + PAYLOAD, "E" + PAYLOAD
            db.add(Revision(lead_id=lead.id, estado_revision="PENDIENTE", marca="M" + PAYLOAD,
                            modelo="O" + PAYLOAD, direccion_texto="D" + PAYLOAD,
                            zone_group="Z" + PAYLOAD, zone_detail="Y" + PAYLOAD,
                            link_compra="javascript:alert(3)", turno_notas="X" + PAYLOAD))
            db.commit()
            details = LeadBookingDetails(
                thread_id=w.thread_id, customer_name="C" + PAYLOAD, email="F" + PAYLOAD,
                vehicle="V" + PAYLOAD, locality="L" + PAYLOAD, inspection_address="I" + PAYLOAD,
                requested_slot="S" + PAYLOAD, seller_name="R" + PAYLOAD,
                seller_phone="P" + PAYLOAD, listing_url="U" + PAYLOAD)
            html = render_lead_card(db.get(Lead, w.lead_id), booking_details=details)
        for raw in ("<img", "<script>alert", "onerror=alert(1)>", 'href="javascript:'):
            self.assertNotIn(raw, html)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
        self.assertNotIn("&amp;lt;", html, "escaped twice")
        for prefix in "NATEMODZYX":
            self.assertIn(f"{prefix}&lt;img", html, prefix)
        for prefix in "FVLISRPU":                                 # the Flow details block
            self.assertIn(f"{prefix}&lt;img", html, prefix)
        self.assertIn("javascript:alert(3)", html)                # shown as text, not a link

    def test_e2_the_title_fallback_name_is_escaped_once(self):
        from app.services.lead_booking_details import LeadBookingDetails
        from app.ui.kanban_view import render_lead_card
        w = H.seed(self.engine)
        with Session(self.engine) as db:
            html = render_lead_card(db.get(Lead, w.lead_id), booking_details=LeadBookingDetails(
                thread_id=w.thread_id, customer_name="Bruno <b>&</b>"))
        self.assertIn("<span>Bruno &lt;b&gt;&amp;&lt;/b&gt;</span>", html)

    def test_e3_trusted_structure_is_not_escaped(self):
        from app.ui.kanban_view import render_lead_card
        w = H.seed(self.engine)
        with Session(self.engine) as db:
            html = render_lead_card(db.get(Lead, w.lead_id))
        for fragment in ('<div class="leadDetailsBody"', '<form method="post"',
                         '<svg class="icon"', '<button'):
            self.assertIn(fragment, html)

    def test_e4_url_link_allows_only_http_and_escapes(self):
        from app.ui.kanban_view import _url_link
        self.assertEqual(_url_link('https://ex.invalid/a?b=1&c="2"'),
                         '<a href="https://ex.invalid/a?b=1&amp;c=&quot;2&quot;" '
                         'target="_blank" rel="noopener">Abrir</a>')
        self.assertEqual(_url_link("javascript:alert(1)"), "javascript:alert(1)")
        self.assertEqual(_url_link('" onmouseover="x'), "&quot; onmouseover=&quot;x")


# ══ K — lead identity callers ═══════════════════════════════════════════════════════════

class K_LeadIdentity(_Base):

    def db(self):
        return H.session_factory(self.engine)()

    def test_k1_ui_lead_update_to_a_held_phone_is_a_typed_conflict(self):
        from app.ui.kanban_actions import ui_lead_update
        with Session(self.engine) as db:
            a = Lead(estado="CONSULTA_NUEVA", telefono="5491100000501", nombre="A",
                     necesita_humano=False)
            b = Lead(estado="CONSULTA_NUEVA", telefono="5491100000502", nombre="B",
                     necesita_humano=False)
            db.add_all([a, b])
            db.commit()
            a_id, b_id = a.id, b.id
        db = self.db()
        try:
            resp = ui_lead_update(lead_id=b_id, nombre="B2", apellido=None,
                                  telefono="+5491100000501", tel=None, email=None, canal=None,
                                  compro_el_auto=None, necesita_humano=None, estado=None, db=db)
        finally:
            db.close()
        self.assertEqual(resp.status_code, 409)
        body = resp.body.decode()
        self.assertIn("LEAD_PHONE_IDENTITY_CONFLICT", body)
        self.assertIn(f"lead #{a_id}", body)
        with Session(self.engine) as db:
            b = db.get(Lead, b_id)
            self.assertEqual((b.telefono, b.nombre), ("5491100000502", "B"))   # nothing changed

    def test_k2_ui_lead_create_with_a_held_phone_says_so_and_echoes_the_form(self):
        from app.ui.kanban_actions import ui_lead_create
        with Session(self.engine) as db:
            a = Lead(estado="CONSULTA_NUEVA", telefono="5491100000601", necesita_humano=False)
            db.add(a)
            db.commit()
            a_id = a.id
        db = self.db()
        try:
            resp = ui_lead_create(nombre="Op<b>", apellido="X", telefono="+5491100000601",
                                  tel=None, email="op@example.invalid", canal=None,
                                  compro_el_auto=None, db=db)
        finally:
            db.close()
        self.assertEqual(resp.status_code, 409)
        body = resp.body.decode()
        self.assertIn(f"lead #{a_id}", body)
        self.assertIn("Op&lt;b&gt;", body)
        self.assertIn("op@example.invalid", body)
        with Session(self.engine) as db:
            self.assertEqual(db.query(Lead).count(), 1)

    def test_k3_digit_free_phones_have_no_identity_and_never_collide(self):
        from app.services.lead_identity import find_lead_by_phone, find_or_create_lead
        from app.services.phone_utils import canonical_phone_identity
        db = self.db()
        try:
            ids = [find_or_create_lead(db, telefono=v, values=dict(
                estado="CONSULTA_NUEVA", necesita_humano=False))[0].id
                for v in ("-", "s/n", "-", "")]
            self.assertEqual(len(set(ids)), 4)
            self.assertIsNone(find_lead_by_phone(db, "-"))
            self.assertIsNone(canonical_phone_identity("s/n"))
        finally:
            db.close()

    def test_k4_assign_phone_if_free_refuses_a_held_identity(self):
        from app.services.lead_identity import assign_phone_if_free
        with Session(self.engine) as db:
            db.add(Lead(estado="CONSULTA_NUEVA", telefono="5491100000701",
                        necesita_humano=False))
            free = Lead(estado="CONSULTA_NUEVA", telefono=None, necesita_humano=False)
            db.add(free)
            db.commit()
            self.assertEqual(assign_phone_if_free(db, free, "+5491100000701"), "HELD_BY_OTHER")
            self.assertIsNone(free.telefono)
            self.assertEqual(assign_phone_if_free(db, free, "5491100000702"), "ASSIGNED")
            self.assertEqual(assign_phone_if_free(db, free, "-"), "NO_IDENTITY")

    def test_k5_the_ce_website_form_path_uses_the_deterministic_assignment(self):
        src = (ROOT / "backend/app/services/conversation_engine.py").read_text()
        block = src[src.index("    def _handle_website_form"):]
        block = block[:block.index("# ── Resolve vehicle")]
        self.assertIn("assign_phone_if_free(self.db, lead, form_data[\"phone\"])", block)
        self.assertNotIn('lead.telefono = form_data["phone"]', block)

    def test_k6_one_identity_rule_everywhere(self):
        from app.models import Lead as L
        from app.services import phone_utils as P
        idx = [i for i in L.__table__.indexes if i.name == "uq_leads_phone_identity"][0]
        self.assertEqual(str(idx.dialect_options["postgresql"]["where"]),
                         P.PHONE_IDENTITY_PREDICATE)
        mig = (ROOT / "backend/migrations/versions/20261102_lead_phone_identity.py").read_text()
        self.assertIn(f'PREDICATE = "{P.PHONE_IDENTITY_PREDICATE}"', mig)
        self.assertEqual(P.PHONE_IDENTITY_PREDICATE,
                         "regexp_replace(telefono, '[^0-9]', '', 'g') <> ''")

    def test_k7_no_phone_derived_digest_in_migration_output_or_logs(self):
        mig = (ROOT / "backend/migrations/versions/20261102_lead_phone_identity.py").read_text()
        self.assertNotIn("sha256(convert_to", mig)
        self.assertNotIn("identity sha256[:16]", mig)
        src = (ROOT / "backend/app/services/lead_identity.py").read_text()
        self.assertNotIn("identity_hash", src)
        self.assertNotIn("hashlib", src)


# ══ M — restored coverage: a fresh delivery on a still-ISSUED offer ═════════════════════

class M_FreshDelivery(_Base):

    def refused(self, w):
        with Session(self.engine) as db:
            return db.execute(select(WhatsAppBookingOffer.status,
                                     WhatsAppBookingOffer.refused_count).where(
                WhatsAppBookingOffer.thread_id == w.thread_id)
                .order_by(WhatsAppBookingOffer.id)).all()

    def test_m1_new_attempt_counted_once_and_distinct_from_a_superseded_offer(self):
        a, b = "dx:" + "a" * 64, "dx:" + "b" * 64
        win, lose = lose_race(self.engine, attempt_ref=a)
        self.assertEqual([tuple(x) for x in self.refused(lose)], [("ISSUED", 1)])
        with ML.Meta() as m:
            for _ in range(2):            # a NEW delivery (fresh IV), then its Meta retry
                with self.assertRaises(bfs.BookingSlotConflictError):
                    ML.confirm(self.engine, lose, attempt_ref=b)
        # the offer is still ISSUED (the worker has not run): a new attempt, counted ONCE
        self.assertEqual([tuple(x) for x in self.refused(lose)], [("ISSUED", 2)])
        self.assertEqual(m.sent, [])
        self.assertEqual(len(ML.jobs(self.engine, lose.thread_id)), 1)
        with Edge():
            work(self.engine)
        self.assertEqual([x[0] for x in self.refused(lose)], ["SUPERSEDED", "ISSUED"])
        # a fresh delivery on the SUPERSEDED offer fails closed: no count, job or message
        with Edge() as e:
            with self.assertRaises(bfs.BookingTokenError):
                ML.confirm(self.engine, lose, attempt_ref="dx:" + "c" * 64)
            work(self.engine, now=later())
        self.assertEqual(self.refused(lose)[0][1], 2)
        self.assertEqual(e.calls, [])
        self.assertEqual(len(ML.jobs(self.engine, lose.thread_id)), 1)
        # and the same-delivery replay guarantee is intact: the first delivery is answered
        # with the same refusal screen
        with ML.Meta():
            with self.assertRaises(bfs.BookingSlotConflictError):
                ML.confirm(self.engine, lose, attempt_ref=a)
        self.assertEqual(self.refused(lose)[0][1], 2)


if __name__ == "__main__":
    unittest.main()
