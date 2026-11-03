"""PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION-R2 — the SQLite half.

The R1 independent review (audit 2026-11-03) found:
  R1-01 HIGH  with OUTBOUND_ENABLED=true and SMART_BOOKING_CE_ENABLED=false the recovery
              worker still sent the recovery text, and a lease-expired SENDING_FLOW job sent
              the Booking Flow;
  R1-02 MED   the worker thread was non-daemon and checked no stop signal;
  R1-03 LOW   professional / agency names reached <option> text unescaped, and lead.flag a
              class attribute.

Every test drives the production worker (`run_once` / `start_worker`) with provider fakes at
the network edge only (no socket is opened; synthetic fixtures). The flags are switched AT a
boundary through the module's deterministic stage barrier (`_STAGE_HOOK`), never by timing.

  A  Smart Booking OFF (OUTBOUND on): every job state ends HANDOFF with zero sends
  B  the gate turns OFF before the text           C  … after the text, before the Flow
  D  … at an expired lease's resumed Flow         E  … before a text retry
  F  both ON: the accepted path, boundary by boundary
  G  the emergency-OFF posture, then the flags return: nothing stale is sent
  H  the original Meta request retried: R1 semantics, nothing sent, no booking
  S  worker shutdown (1–10 of the R2 prompt)
  X  lead-card dropdowns and lead.flag
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import threading
import time as _time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _sb2b_harness as H  # noqa: E402
import test_minimum_launch as ML  # noqa: E402
from test_minimum_launch_r1 import (PAYLOAD, Edge, _Crash, attention, flags, job,  # noqa: E402
                                    later, lose_race)

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.models import (Agencia, Profesional, Revision, SlotRecoveryJob,  # noqa: E402
                        ThreadRevision, WhatsAppMessage)
from app.services import booking_flow_service as bfs  # noqa: E402
from app.services import booking_slot_recovery as R  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The boundaries of one complete recovery, in order (F proves this sequence).
FULL_PATH = ["claim", "process", "text", "text_transport", "text_transport_locked",
             "after_text", "offer", "flow", "flow_transport", "flow_transport_locked", "claim"]


class Gate:
    """The effective Smart Booking flag, switchable AT a named boundary. Patched in through
    `get_settings`, i.e. read by the canonical `booking_dispatch_allowed()`."""

    def __init__(self, sb=True, *, off_at=None, stop_at=None, on_stage=None):
        self.sb, self.off_at, self.stop_at, self.on_stage = sb, off_at, stop_at, on_stage
        self.stages: list = []
        self.stopped_at = None
        #: set when a pass reaches its NEXT claim boundary (the job before it is settled) —
        #: lifecycle tests wait on this, never poll the database beside the worker
        self.passed = threading.Event()

    def hook(self, stage, job_id):
        self.stages.append(stage)
        if stage == "claim" and self.stages.count("claim") >= 2:
            self.passed.set()
        if stage == self.off_at:
            self.sb = False
        if stage == self.stop_at:
            R.stop_worker(timeout=0)
            self.stopped_at = _time.monotonic()
        if self.on_stage is not None:
            self.on_stage(stage)

    def __enter__(self):
        self._p = mock.patch("app.settings.get_settings", lambda: H.settings(flag=self.sb))
        self._p.start()
        R._STAGE_HOOK = self.hook
        return self

    def __exit__(self, *exc):
        R._STAGE_HOOK = None
        self._p.stop()


def run(engine, gate, now=None):
    with gate, Session(engine) as db:
        return R.run_once(db, now=now)


def outbound_rows(engine, thread_id):
    with Session(engine) as db:
        return db.execute(select(WhatsAppMessage.message_type, WhatsAppMessage.status,
                                 WhatsAppMessage.content_fingerprint)
                          .where(WhatsAppMessage.thread_id == thread_id,
                                 WhatsAppMessage.direction == "out")
                          .order_by(WhatsAppMessage.id)).all()


def kinds(edge):
    return [k for k, *_ in edge.calls]


class _Base(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()
        R._STOP = threading.Event()

    def tearDown(self):
        R._STAGE_HOOK = None
        R.stop_worker(timeout=2.0)
        R._STOP = threading.Event()
        R._WORKER = None

    def listed(self, lose, *reasons):
        rows, _ = attention(self.engine)
        mine = [r for r in rows if r["thread_id"] == lose.thread_id]
        self.assertEqual(len(mine), 1, rows)
        for reason in reasons:
            self.assertIn(reason, mine[0]["reasons"])
        self.assertEqual(mine[0]["recovery"], "CONTACT_CUSTOMER")
        return mine[0]

    def assert_halted(self, lose, outcome, error, *, reason="SLOT_RECOVERY_DISPATCH_OFF"):
        """Terminal HANDOFF with the typed refusal, flags kept, listed, nothing booked, the
        original SLOT_CONFLICT job intact."""
        j = job(self.engine, lose.thread_id)
        self.assertEqual((j.state, j.outcome, j.last_error), ("HANDOFF", outcome, error))
        self.assertEqual(flags(self.engine, lose)[:2], (True, True))
        self.listed(lose, "SLOT_RECOVERY_HANDOFF", reason)
        with Session(self.engine) as db:
            self.assertEqual(db.query(Revision).filter(Revision.lead_id == lose.lead_id).count(), 0)
            self.assertEqual(db.query(ThreadRevision).filter(
                ThreadRevision.thread_id == lose.thread_id).count(), 0)
        self.assertIsNotNone(j.losing_offer_id)                 # SLOT_CONFLICT evidence kept
        self.assertEqual(j.recovery_identity, R.recovery_identity(j.losing_offer_id))
        return j

    def assert_no_offer_live(self, lose, expected):
        self.assertEqual([x.status for x in ML.offers(self.engine, lose.thread_id)], expected)

    def assert_stays_quiet(self, lose, *, minutes=(10, 20)):
        """The flags return: still nothing is sent, the job stays HANDOFF."""
        before = outbound_rows(self.engine, lose.thread_id)
        with Edge() as e:
            for m in minutes:
                run(self.engine, Gate(True), now=later(m))
        self.assertEqual(e.calls, [], "a stale recovery send after the flags returned")
        self.assertEqual(job(self.engine, lose.thread_id).state, "HANDOFF")
        self.assertEqual(outbound_rows(self.engine, lose.thread_id), before)


# ── states a lost claimant leaves behind (lease expired at `later()`) ──────────────────────

def leave_pending(engine):
    win, lose = lose_race(engine)
    return lose


def leave_sending_text(engine):
    """SENDING_TEXT committed, the process died before the gate: nothing crossed."""
    win, lose = lose_race(engine)
    with Edge(), mock.patch("app.services.booking_acknowledgement._send_ledger_acknowledgement",
                            side_effect=_Crash("died")):
        try:
            run(engine, Gate(True))
        except _Crash:
            pass
    assert job(engine, lose.thread_id).state == "SENDING_TEXT"
    return lose


def leave_text_sent(engine):
    win, lose = lose_race(engine)
    with Edge(), mock.patch.object(R, "_issue_fresh_offer", side_effect=_Crash("died")):
        try:
            run(engine, Gate(True))
        except _Crash:
            pass
    assert job(engine, lose.thread_id).state == "TEXT_SENT"
    return lose


def leave_sending_flow(engine):
    """The fresh offer and SENDING_FLOW committed, the process died before the Flow."""
    win, lose = lose_race(engine)
    with Edge(), mock.patch.object(R, "_send_flow", side_effect=_Crash("died")):
        try:
            run(engine, Gate(True))
        except _Crash:
            pass
    assert job(engine, lose.thread_id).state == "SENDING_FLOW"
    return lose


# ══ A — Smart Booking OFF, OUTBOUND on ══════════════════════════════════════════════════

class A_SmartBookingOff(_Base):

    def test_a1_pending_job_sends_nothing_and_hands_off(self):
        lose = leave_pending(self.engine)
        with Edge() as e:                                       # OUTBOUND_ENABLED=true here
            self.assertEqual(os.environ.get("OUTBOUND_ENABLED"), "true")
            run(self.engine, Gate(False))
        self.assertEqual(e.calls, [])
        self.assertEqual(outbound_rows(self.engine, lose.thread_id), [])   # not even a gate row
        self.assert_halted(lose, "DISPATCH_OFF", "AT_PROCESS")
        self.assert_no_offer_live(lose, ["ISSUED"])             # the losing offer, untouched
        self.assert_stays_quiet(lose)

    def test_a2_every_lease_expired_state_sends_nothing_and_hands_off(self):
        cases = (("SENDING_TEXT", leave_sending_text, ["ISSUED"]),
                 ("TEXT_SENT", leave_text_sent, ["ISSUED"]),
                 ("SENDING_FLOW", leave_sending_flow, ["SUPERSEDED", "WITHDRAWN"]))
        for state, leave, offers in cases:
            with self.subTest(state=state):
                self.engine = H.sqlite_engine()
                lose = leave(self.engine)
                before = outbound_rows(self.engine, lose.thread_id)
                with Edge() as e:
                    run(self.engine, Gate(False), now=later())
                self.assertEqual(e.calls, [])
                self.assertEqual(outbound_rows(self.engine, lose.thread_id), before)
                self.assert_halted(lose, "DISPATCH_OFF", "AT_PROCESS")
                # the fresh offer of a Flow that was never sent is not left ISSUED
                self.assert_no_offer_live(lose, offers)
                self.assert_stays_quiet(lose)


# ══ B — OFF before the text ═════════════════════════════════════════════════════════════

class B_OffBeforeText(_Base):

    def test_b1_off_at_each_boundary_before_the_text(self):
        for stage, outcome, gate_rows in (("claim", "DISPATCH_OFF", 0),
                                          ("process", "DISPATCH_OFF", 0),
                                          ("text", "TEXT_DISPATCH_OFF", 0),
                                          ("text_transport", "TEXT_DISPATCH_OFF", 1),
                                          ("text_transport_locked", "TEXT_DISPATCH_OFF", 1)):
            with self.subTest(stage=stage):
                self.engine = H.sqlite_engine()
                lose = leave_pending(self.engine)
                with Edge() as e:
                    run(self.engine, Gate(True, off_at=stage))
                self.assertEqual(e.calls, [])
                rows = outbound_rows(self.engine, lose.thread_id)
                # before the gate nothing is recorded; at the last line the gate row exists and
                # is marked failed — nothing left the process
                self.assertEqual(len(rows), gate_rows, rows)
                self.assertTrue(all(r.status == "failed" for r in rows), rows)
                error = ("AT_PROCESS" if stage == "claim" else "AT_TEXT_TRANSPORT"
                         if stage.startswith("text_transport") else f"AT_{stage.upper()}")
                self.assert_halted(lose, outcome, error)
                self.assert_stays_quiet(lose)


# ══ C — OFF after the text, before the Flow ═════════════════════════════════════════════

class C_OffBetweenTextAndFlow(_Base):

    def test_c1_off_at_each_boundary_after_the_text(self):
        for stage, gate_flow_rows, offers in (
                ("after_text", 0, ["ISSUED"]),
                ("offer", 0, ["ISSUED"]),
                ("flow", 0, ["SUPERSEDED", "WITHDRAWN"]),
                ("flow_transport", 1, ["SUPERSEDED", "WITHDRAWN"]),
                ("flow_transport_locked", 1, ["SUPERSEDED", "WITHDRAWN"])):
            with self.subTest(stage=stage):
                self.engine = H.sqlite_engine()
                lose = leave_pending(self.engine)
                with Edge() as e:
                    run(self.engine, Gate(True, off_at=stage))
                self.assertEqual(kinds(e), ["text"])            # one text at most, no Flow
                flow_rows = [r for r in outbound_rows(self.engine, lose.thread_id)
                             if r.message_type == "flow"]
                self.assertEqual(len(flow_rows), gate_flow_rows)
                self.assertTrue(all(r.status == "failed" for r in flow_rows))
                self.assert_halted(lose, "FLOW_DISPATCH_OFF",
                                   f"AT_{stage.upper()}".replace("_LOCKED", ""))
                self.assert_no_offer_live(lose, offers)
                self.assert_stays_quiet(lose)


# ══ D — OFF at an expired lease, before the resumed Flow ════════════════════════════════

class D_OffAtLeaseExpiryResume(_Base):

    def test_d1_resumed_flow_is_never_sent(self):
        for stage in ("process", "flow_resume", "flow", "flow_transport"):
            with self.subTest(stage=stage):
                self.engine = H.sqlite_engine()
                lose = leave_sending_flow(self.engine)
                before = [r for r in outbound_rows(self.engine, lose.thread_id)
                          if r.message_type == "flow"]
                with Edge() as e:
                    gate = Gate(True, off_at=stage)
                    run(self.engine, gate, now=later())
                self.assertEqual(e.calls, [])                   # zero resumed Flow sends
                self.assertIn("flow_resume" if stage != "process" else "process", gate.stages)
                self.assertNotIn("offer", gate.stages)          # no second offer
                outcome = "DISPATCH_OFF" if stage == "process" else "FLOW_DISPATCH_OFF"
                self.assert_halted(lose, outcome, f"AT_{stage.upper()}")
                self.assert_no_offer_live(lose, ["SUPERSEDED", "WITHDRAWN"])
                after = [r for r in outbound_rows(self.engine, lose.thread_id)
                         if r.message_type == "flow"]
                self.assertTrue(all(r.status == "failed" for r in after))
                self.assertEqual(len(after), len(before) + (stage == "flow_transport"))
                self.assert_stays_quiet(lose)

    def test_d2_resumed_text_after_expiry_is_never_sent(self):
        for stage in ("process", "text_resume", "text_transport"):
            with self.subTest(stage=stage):
                self.engine = H.sqlite_engine()
                lose = leave_sending_text(self.engine)
                with Edge() as e:
                    run(self.engine, Gate(True, off_at=stage), now=later())
                self.assertEqual(e.calls, [])
                outcome = "DISPATCH_OFF" if stage == "process" else "TEXT_DISPATCH_OFF"
                self.assert_halted(lose, outcome, f"AT_{stage.upper()}")
                self.assert_stays_quiet(lose)


# ══ E — OFF before a retry ══════════════════════════════════════════════════════════════

class E_OffBeforeRetry(_Base):

    def test_e1_no_retry_send(self):
        lose = leave_pending(self.engine)
        with Edge(text=("4xx", "ok")) as e:                     # definite failure → retry owed
            run(self.engine, Gate(True, off_at="text_retry"))
        self.assertEqual(kinds(e), ["text"])                    # the first attempt only
        self.assertEqual(job(self.engine, lose.thread_id).text_attempts, 1)
        rows = outbound_rows(self.engine, lose.thread_id)
        retry_identity = R.delivery_identity(job(self.engine, lose.thread_id).recovery_identity,
                                             "text", 2)
        self.assertNotIn(retry_identity, [r.content_fingerprint for r in rows])
        self.assert_halted(lose, "TEXT_DISPATCH_OFF", "AT_TEXT_RETRY")
        self.assert_stays_quiet(lose)

    def test_e2_with_the_gate_on_the_retry_is_unchanged(self):
        lose = leave_pending(self.engine)
        with Edge(text=("4xx", "ok")) as e:
            gate = Gate(True)
            run(self.engine, gate)
        self.assertEqual(kinds(e), ["text", "text", "flow"])
        self.assertIn("text_retry", gate.stages)
        self.assertEqual(job(self.engine, lose.thread_id).state, "RECOVERED")


# ══ F — both ON ═════════════════════════════════════════════════════════════════════════

class F_BothOn(_Base):

    def test_f1_the_accepted_path_is_unchanged_and_crosses_every_boundary(self):
        win, lose = lose_race(self.engine)
        with Edge() as e:
            gate = Gate(True)
            run(self.engine, gate)
        self.assertEqual(gate.stages, FULL_PATH)
        self.assertEqual(kinds(e), ["text", "flow"])
        j = job(self.engine, lose.thread_id)
        self.assertEqual((j.state, j.outcome), ("RECOVERED", "RECOVERED"))
        self.assert_no_offer_live(lose, ["SUPERSEDED", "ISSUED"])
        self.assertEqual(flags(self.engine, lose)[:2], (False, False))

    def test_f2_resume_with_both_on_is_unchanged(self):
        lose = leave_sending_flow(self.engine)
        with Edge() as e:
            gate = Gate(True)
            run(self.engine, gate, now=later())
        self.assertEqual(kinds(e), ["flow"])
        self.assertIn("flow_resume", gate.stages)
        self.assertEqual(job(self.engine, lose.thread_id).state, "RECOVERED")


# ══ G — the emergency-OFF posture, then the flags return ════════════════════════════════

class G_EmergencyPosture(_Base):

    def test_g1_all_off_sends_nothing_and_nothing_stale_after_the_flags_return(self):
        """The kit's OFF posture: OUTBOUND_ENABLED and SMART_BOOKING_CE_ENABLED false (the
        two of the seven flags this path reads; the other five are not read by the worker).
        Every lost-claimant state is consumed to HANDOFF; ON afterwards sends nothing."""
        losers = []
        for leave in (leave_pending, leave_sending_text, leave_text_sent, leave_sending_flow):
            eng = H.sqlite_engine()
            losers.append((eng, leave(eng)))
        for eng, lose in losers:
            before = outbound_rows(eng, lose.thread_id)
            sent: list = []
            with mock.patch.dict(os.environ, {}, clear=False), \
                    mock.patch("app.ui.whatsapp_ui._send_whatsapp_cloud_text",
                               lambda **k: sent.append(("text", k))), \
                    mock.patch("app.ui.whatsapp_ui._send_whatsapp_cloud_flow",
                               lambda **k: sent.append(("flow", k))):
                os.environ.pop("OUTBOUND_ENABLED", None)
                run(eng, Gate(False), now=later())
            self.assertEqual(sent, [])
            self.assertEqual(outbound_rows(eng, lose.thread_id), before)
            self.engine = eng
            self.assert_halted(lose, "DISPATCH_OFF", "AT_PROCESS")
            self.assert_stays_quiet(lose, minutes=(10, 20, 60))


# ══ H — the original Meta request retried ═══════════════════════════════════════════════

class H_MetaRetry(_Base):

    def test_h1_retry_with_smart_booking_on_is_the_r1_replay(self):
        ref = "dx:" + "7" * 64
        win, lose = lose_race(self.engine, attempt_ref=ref)
        with Edge():
            run(self.engine, Gate(False))
        self.assert_halted(lose, "DISPATCH_OFF", "AT_PROCESS")
        before = (outbound_rows(self.engine, lose.thread_id), ML.offers(self.engine, lose.thread_id))
        with Edge() as e:
            with self.assertRaises(bfs.BookingSlotConflictError):   # R1's replay semantics
                ML.confirm(self.engine, lose, attempt_ref=ref)
            run(self.engine, Gate(True), now=later())
        self.assertEqual(e.calls, [])
        self.assertEqual((outbound_rows(self.engine, lose.thread_id),
                          ML.offers(self.engine, lose.thread_id)), before)
        with Session(self.engine) as db:
            self.assertEqual(db.query(SlotRecoveryJob).count(), 1)
        self.assert_halted(lose, "DISPATCH_OFF", "AT_PROCESS")

    def test_h2_retry_while_smart_booking_is_off_is_refused_before_the_authority(self):
        ref = "dx:" + "8" * 64
        win, lose = lose_race(self.engine, attempt_ref=ref)
        with Edge():
            run(self.engine, Gate(False))
        with Edge() as e, mock.patch("app.settings.get_settings",
                                     lambda: H.settings(flag=False)):
            with Session(self.engine) as db:
                try:
                    out = bfs.BookingFlowService(db).handle_confirm_booking(
                        lose.token, {"date": lose.day.isoformat(),
                                     "time": lose.at.strftime("%H:%M"), "name": "Ana Fixture",
                                     "email": "ana@example.invalid",
                                     "inspection_address": "Calle Falsa 123"},
                        attempt_ref=ref)
                except bfs.BookingSlotConflictError:            # pragma: no cover
                    out = "SLOT_CONFLICT"
            self.assertNotEqual((out or {}).get("screen") if isinstance(out, dict) else out,
                                "SUCCESS")
            run(self.engine, Gate(False), now=later())
        self.assertEqual(e.calls, [])
        with Session(self.engine) as db:
            self.assertEqual(db.query(SlotRecoveryJob).count(), 1)
        self.assert_halted(lose, "DISPATCH_OFF", "AT_PROCESS")


# ══ S — worker shutdown ═════════════════════════════════════════════════════════════════

class TrackedSession(Session):
    opened: list = []
    closed: list = []

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        TrackedSession.opened.append(id(self))

    def close(self):
        TrackedSession.closed.append(id(self))
        super().close()


class S_Shutdown(_Base):

    def lifecycle(self, gate):
        """The real application worker on the test engine (tracked Sessions)."""
        TrackedSession.opened, TrackedSession.closed = [], []
        factory = sessionmaker(bind=self.engine, class_=TrackedSession, autoflush=False)
        return mock.patch("app.db.SessionLocal", factory), \
            mock.patch.object(R, "CHECK_INTERVAL_SECONDS", 0.01)

    def wait_for(self, cond, timeout=10.0):
        end = _time.monotonic() + timeout
        while _time.monotonic() < end:
            if cond():
                return True
            _time.sleep(0.01)
        return False

    def assert_nothing_after_stop(self, edge, gate):
        self.assertIsNotNone(gate.stopped_at)
        self.assertTrue(all(c[2] < gate.stopped_at for c in edge.calls),
                        "a provider call began after the stop signal")

    def test_s1_shutdown_before_claim(self):
        lose = leave_pending(self.engine)
        with Edge() as e:
            gate = Gate(True, stop_at="claim")
            run(self.engine, gate)
        self.assertEqual(e.calls, [])
        j = job(self.engine, lose.thread_id)
        self.assertEqual((j.state, j.attempts, j.claim_token), ("PENDING", 0, None))
        self.assertEqual(gate.stages, ["claim"])                # nothing claimed
        rows, _ = attention(self.engine)
        self.assertEqual([r["state"] for r in rows if r["thread_id"] == lose.thread_id],
                         ["PENDING"])                           # still listed, still owed
        # the next process, Smart Booking OFF: no stale send
        R._STOP = threading.Event()
        with Edge() as e2:
            run(self.engine, Gate(False), now=later())
        self.assertEqual(e2.calls, [])
        self.assert_halted(lose, "DISPATCH_OFF", "AT_PROCESS")

    def test_s2_shutdown_after_claim_before_text(self):
        for stage in ("process", "text", "text_transport"):
            with self.subTest(stage=stage):
                self.engine = H.sqlite_engine()
                R._STOP = threading.Event()
                lose = leave_pending(self.engine)
                with Edge() as e:
                    gate = Gate(True, stop_at=stage)
                    run(self.engine, gate)
                self.assertEqual(e.calls, [])
                self.assertEqual(gate.stages.count("process"), 1)
                self.assertEqual(gate.stages[-1], "claim")          # …and no next claim
                self.assertEqual(job(self.engine, lose.thread_id).attempts, 1)
                outcome = "WORKER_STOPPING" if stage == "process" else "TEXT_WORKER_STOPPING"
                self.assert_halted(lose, outcome, f"AT_{stage.upper()}",
                                   reason="SLOT_RECOVERY_WORKER_STOPPED")
                R._STOP = threading.Event()
                self.assert_stays_quiet(lose)

    def test_s3_shutdown_after_text_before_flow(self):
        for stage, offers in (("after_text", ["ISSUED"]), ("offer", ["ISSUED"]),
                              ("flow", ["SUPERSEDED", "WITHDRAWN"]),
                              ("flow_transport", ["SUPERSEDED", "WITHDRAWN"])):
            with self.subTest(stage=stage):
                self.engine = H.sqlite_engine()
                R._STOP = threading.Event()
                lose = leave_pending(self.engine)
                with Edge() as e:
                    gate = Gate(True, stop_at=stage)
                    run(self.engine, gate)
                self.assertEqual(kinds(e), ["text"])
                self.assert_nothing_after_stop(e, gate)
                self.assert_halted(lose, "FLOW_WORKER_STOPPING", f"AT_{stage.upper()}",
                                   reason="SLOT_RECOVERY_WORKER_STOPPED")
                self.assert_no_offer_live(lose, offers)
                R._STOP = threading.Event()
                self.assert_stays_quiet(lose)

    def test_s4_shutdown_while_a_provider_transport_is_blocked(self):
        lose = leave_pending(self.engine)
        db_patch, interval = self.lifecycle(None)
        edge = Edge(text=("block",))
        with edge, db_patch, interval, Gate(True) as gate:
            R.start_worker()
            self.assertTrue(self.wait_for(lambda: edge.calls), "the worker never sent")
            t0 = _time.monotonic()
            ended = R.stop_worker(timeout=3.0)
            elapsed = _time.monotonic() - t0
            gate.stopped_at = t0
            self.assertTrue(ended, "shutdown waited for the blocked provider call")
            self.assertLess(elapsed, 1.5)                       # not the 8 s deadline
            self.assertEqual(R.inflight_transports(), 1)        # abandoned, NOT cancelled
            j = job(self.engine, lose.thread_id)
            self.assertEqual((j.state, j.outcome, j.last_error),
                             ("HANDOFF", "TEXT_TRANSPORT_DEADLINE",
                              "TRANSPORT_ABANDONED_AT_SHUTDOWN"))
            before = outbound_rows(self.engine, lose.thread_id)
            edge.release.set()                                  # the orphan finishes late
            self.assertTrue(self.wait_for(lambda: R.inflight_transports() == 0))
        print(f"\nSHUTDOWN_WITH_BLOCKED_TRANSPORT_SECONDS={elapsed:.3f}")
        self.assertEqual(kinds(edge), ["text"])                 # no Flow, no retry
        self.assertEqual(outbound_rows(self.engine, lose.thread_id), before)
        self.assert_nothing_after_stop(edge, gate)
        self.listed(lose, "SLOT_RECOVERY_HANDOFF", "SLOT_RECOVERY_WORKER_STOPPED")
        self.assertEqual(flags(self.engine, lose)[:2], (True, True))

    def test_s5_expired_lease_then_restart_with_smart_booking_off(self):
        """A process force-terminated mid-recovery (the halt never recorded): the next
        process, Smart Booking OFF, resumes nothing."""
        for leave in (leave_sending_text, leave_text_sent, leave_sending_flow):
            with self.subTest(state=leave.__name__):
                self.engine = H.sqlite_engine()
                lose = leave(self.engine)
                db_patch, interval = self.lifecycle(None)
                with Edge() as e, db_patch, interval, Gate(False) as gate:
                    with mock.patch.object(R, "_utcnow", lambda: later()):
                        R.start_worker()
                        self.assertTrue(gate.passed.wait(10))
                        self.assertTrue(R.stop_worker())
                self.assertEqual(e.calls, [])
                self.assertIn("process", gate.stages)
                R._STOP = threading.Event()
                self.assert_halted(lose, "DISPATCH_OFF", "AT_PROCESS")
                self.assert_stays_quiet(lose)

    def test_s6_repeated_startup_starts_one_daemon_worker(self):
        db_patch, interval = self.lifecycle(None)
        with db_patch, interval, Gate(True):
            t1 = R.start_worker()
            t2 = R.start_worker()
            t3 = R.start_worker()
            self.assertIs(t1, t2)
            self.assertIs(t2, t3)
            self.assertTrue(t1.daemon)
            alive = [t for t in threading.enumerate()
                     if t.name == "slot-recovery-worker" and t.is_alive()]
            self.assertEqual(alive, [t1])
            self.assertTrue(R.stop_worker())
            self.assertFalse(t1.is_alive())
            t4 = R.start_worker()                               # a new lifecycle
            self.assertIsNot(t4, t1)
            self.assertTrue(t4.daemon)
            self.assertEqual([t for t in threading.enumerate()
                              if t.name == "slot-recovery-worker" and t.is_alive()], [t4])
            self.assertTrue(R.stop_worker())

    def test_s6b_a_restart_never_unstops_a_worker_still_finishing(self):
        """stop → start while the old pass is still blocked: the old worker keeps ITS stop
        signal and sends nothing more; exactly one live, unstopped worker exists."""
        lose = leave_pending(self.engine)
        db_patch, interval = self.lifecycle(None)
        release, entered, old_done = threading.Event(), threading.Event(), threading.Event()
        first: list = []

        def hold(stage):
            me = threading.current_thread()
            if stage == "process" and not entered.is_set():
                first.append(me)
                entered.set()
                release.wait(10)
            elif stage == "claim" and first and me is not first[0]:
                old_done.wait(10)          # the new worker touches the DB only after the old
        with Edge() as e, db_patch, interval, Gate(True, on_stage=hold):
            old = R.start_worker()
            self.assertTrue(entered.wait(10))
            self.assertFalse(R.stop_worker(timeout=0.1))        # still inside its pass
            new = R.start_worker()
            self.assertIsNot(new, old)
            self.assertTrue(new.daemon)
            release.set()
            old.join(5)
            self.assertFalse(old.is_alive())
            old_done.set()
            self.assertTrue(R.stop_worker())
        self.assertEqual(e.calls, [])                           # the old pass halted
        self.assert_halted(lose, "WORKER_STOPPING", "AT_PROCESS",
                           reason="SLOT_RECOVERY_WORKER_STOPPED")

    def test_s7_repeated_shutdown_is_harmless(self):
        self.assertTrue(R.stop_worker())                        # never started
        db_patch, interval = self.lifecycle(None)
        with db_patch, interval, Gate(True):
            R.start_worker()
            self.assertTrue(R.stop_worker())
            self.assertTrue(R.stop_worker())
            self.assertTrue(R.stop_worker(timeout=0))
        self.assertIsNone(R._WORKER)

    def test_s8_the_worker_cannot_keep_the_process_alive(self):
        """A pass blocked in something that is NOT a provider call (a database wait): the
        process still exits at once after the bounded shutdown."""
        script = (
            "import time, sys\n"
            "from app.services import booking_slot_recovery as R\n"
            "R.CHECK_INTERVAL_SECONDS = 0.01\n"
            "entered = []\n"
            "def stuck():\n"
            "    entered.append(1); time.sleep(30)\n"
            "R._run_check = stuck\n"
            "R.start_worker()\n"
            "while not entered: time.sleep(0.01)\n"
            "t0 = time.monotonic()\n"
            "ended = R.stop_worker(timeout=0.2)\n"
            "print('ENDED', ended, 'STOP_SECONDS %.3f' % (time.monotonic() - t0))\n"
            "sys.stdout.flush()\n")
        t0 = _time.monotonic()
        out = subprocess.run([sys.executable, "-c", script], cwd=str(ROOT / "backend"),
                             capture_output=True, text=True, timeout=25,
                             env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        elapsed = _time.monotonic() - t0
        print(f"\nPROCESS_EXIT_SECONDS={elapsed:.3f} {out.stdout.strip()}")
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        self.assertIn("ENDED False", out.stdout)                # the pass was still blocked
        self.assertLess(elapsed, 10.0)                          # not the 30 s it was blocked

    def test_s9_no_session_leak(self):
        lose = leave_pending(self.engine)
        db_patch, interval = self.lifecycle(None)
        with Edge() as e, db_patch, interval, Gate(True, stop_at="after_text") as gate:
            worker = R.start_worker()
            self.assertTrue(gate.passed.wait(10))
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertTrue(R.stop_worker())
        self.assertEqual(kinds(e), ["text"])
        self.assert_halted(lose, "FLOW_WORKER_STOPPING", "AT_AFTER_TEXT",
                           reason="SLOT_RECOVERY_WORKER_STOPPED")
        self.assertTrue(TrackedSession.opened)
        self.assertEqual(sorted(TrackedSession.opened), sorted(TrackedSession.closed))
        # an internal error inside a pass also closes its Session
        TrackedSession.opened, TrackedSession.closed = [], []
        with mock.patch("app.db.SessionLocal", sessionmaker(bind=self.engine,
                                                            class_=TrackedSession)), \
                mock.patch.object(R, "run_once", side_effect=RuntimeError("boom")):
            R._run_check()
        self.assertEqual(len(TrackedSession.opened), 1)
        self.assertEqual(TrackedSession.opened, TrackedSession.closed)

    def test_s10_no_external_send_begins_after_the_stop_signal(self):
        """The stop races the last check: `stop_worker` takes `_SEND_LOCK`, so it returns
        either before the check (the call is refused) or after the provider thread started
        (that one call is the in-flight one). Nothing ever begins after it returned."""
        lose = leave_pending(self.engine)
        stopper_done = {}

        def stop_from_another_thread(stage):
            if stage == "text_transport_locked":                # we hold _SEND_LOCK here
                def stopper():
                    R.stop_worker(timeout=0)
                    stopper_done["at"] = _time.monotonic()
                threading.Thread(target=stopper, daemon=True).start()
                _time.sleep(0.05)                               # it is now blocked on the lock
                stopper_done["returned_while_locked"] = "at" in stopper_done
        with Edge() as e:
            gate = Gate(True, on_stage=stop_from_another_thread)
            run(self.engine, gate)
        self.assertTrue(self.wait_for(lambda: "at" in stopper_done))
        self.assertIs(stopper_done["returned_while_locked"], False,
                      "stop_worker returned while the last check held _SEND_LOCK")
        self.assertEqual(kinds(e), ["text"])                    # started before the signal
        self.assertLess(e.calls[0][2], stopper_done["at"])
        self.assertTrue(all(c[2] < stopper_done["at"] for c in e.calls))
        self.assert_halted(lose, "FLOW_WORKER_STOPPING", "AT_AFTER_TEXT",
                           reason="SLOT_RECOVERY_WORKER_STOPPED")

    def test_s11_the_application_owns_the_lifecycle(self):
        src = (ROOT / "backend/app/main.py").read_text()
        self.assertIn('@app.on_event("shutdown")\ndef _stop_slot_recovery_worker()', src)
        self.assertIn("    stop_slot_recovery_worker()", src)
        self.assertIn("    start_slot_recovery_worker()", src)
        self.assertNotIn("slot_recovery_loop", src)
        mod = (ROOT / "backend/app/services/booking_slot_recovery.py").read_text()
        self.assertNotIn("ThreadPoolExecutor", mod)


# ══ X — lead-card dropdowns and lead.flag ═══════════════════════════════════════════════

HOSTILE = 'Q"\'><svg/onload=pwn(7)><b>Z</b> & <i>'


class X_LeadCardSinks(_Base):

    def people(self):
        p = Profesional(id=7, nombre=HOSTILE, apellido="A" + HOSTILE, cargo="C" + HOSTILE)
        a = Agencia(id=9, nombre_agencia="G" + HOSTILE)
        return p, a

    def assert_safe(self, html):
        for raw in ("<svg/onload", "onload=pwn(7)>", "<b>Z</b>", "& <i>", 'Q"\'>'):
            self.assertNotIn(raw, html)
        self.assertNotIn("&amp;lt;", html, "escaped twice")
        self.assertNotIn("&amp;amp;", html, "escaped twice")

    def test_x1_edit_form_professional_and_agency_options(self):
        from app.ui.kanban_view import render_edit_latest_revision_form
        p, a = self.people()
        rev = Revision(id=3, lead_id=1, estado_revision="PENDIENTE", profesional_id=7,
                       agencia_id=9)
        html = render_edit_latest_revision_form(1, rev, 1, {}, [p], [a])
        self.assert_safe(html)
        esc = "Q&quot;&#x27;&gt;&lt;svg/onload=pwn(7)&gt;&lt;b&gt;Z&lt;/b&gt; &amp; &lt;i&gt;"
        self.assertIn(f'<option value="7" selected>{esc} A{esc} (C{esc})</option>', html)
        self.assertIn(f">G{esc}</option>", html)

    def test_x2_filters_form_professional_option(self):
        from app.ui.kanban_view import _filters_form_html
        p, _ = self.people()
        html = _filters_form_html(
            q="", estado=None, flag=None, profesional_id="7", profesionales=[p], canal="",
            tipo_vehiculo="", marca="", modelo="", anio="", zone_group="", zone_detail="",
            estado_revision="", from_date="", to_date="", date_field="", zones_map={},
            action="/", include_back_link=False, back_href="/", include_open_filters=False)
        self.assert_safe(html)
        self.assertIn("&lt;svg/onload=pwn(7)&gt;", html)

    def test_x3_ordinary_labels_are_unchanged(self):
        from app.ui.kanban_view import render_edit_latest_revision_form
        p = Profesional(id=1, nombre="Ana", apellido="Pérez", cargo="Inspectora")
        a = Agencia(id=2, nombre_agencia="Autos José & Hijos")
        rev = Revision(id=3, lead_id=1, estado_revision="PENDIENTE", profesional_id=1,
                       agencia_id=2)
        html = render_edit_latest_revision_form(1, rev, 1, {}, [p], [a])
        self.assertIn('<option value="1" selected>Ana Pérez (Inspectora)</option>', html)
        self.assertIn(">Autos José &amp; Hijos</option>", html)
        self.assertIn('name="profesional_id"', html)
        self.assertIn('name="agencia_id"', html)

    def test_x4_lead_flag_class_is_an_allowlist(self):
        from app.ui.kanban_view import FLAG_LABELS, _flag_class, render_lead_card
        from app.models import Lead
        for known in FLAG_LABELS:
            self.assertEqual(_flag_class(known), f"flag-{known}")
        for unknown in (HOSTILE, 'x" onmouseover="a', "<b>", "a&b", "perdido", "PERDIDO x",
                        "flag-PERDIDO", "", None, 7):
            self.assertEqual(_flag_class(unknown), "flag-other", unknown)
        w = H.seed(self.engine)
        for value, cls, label in (("PERDIDO", "flag-PERDIDO", "Perdido"),
                                  (HOSTILE, "flag-other", None),
                                  ('x" onmouseover="a', "flag-other", None)):
            with self.subTest(flag=value), Session(self.engine) as db:
                lead = db.get(Lead, w.lead_id)
                lead.flag = value
                db.flush()
                html = render_lead_card(lead)
                db.rollback()
                self.assertIn(f'class="cardHeaderRow {cls} card-header"', html)
                self.assert_safe(html)
                self.assertNotIn('onmouseover="a', html)
                if label:
                    self.assertIn(label, html)
                else:
                    self.assertIn("&lt;svg/onload=pwn(7)&gt;" if value == HOSTILE
                                  else "x&quot; onmouseover=&quot;a", html)

    def test_x6_customer_fields_still_escaped_exactly_once(self):
        """The R1 customer-field payload, re-rendered with the R2 changes (no double
        escaping anywhere)."""
        import test_minimum_launch_r1 as R1T
        case = R1T.E_Escaping("test_e1_every_customer_derived_value_is_escaped_exactly_once")
        result = unittest.TestResult()
        case.run(result)
        self.assertEqual((result.failures, result.errors), ([], []))
        self.assertIn("<", PAYLOAD)


if __name__ == "__main__":                                      # pragma: no cover
    unittest.main()
