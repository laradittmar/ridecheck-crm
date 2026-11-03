"""PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION — the SQLite half.

Production functions only, provider fakes at the network edge (no socket is ever opened:
`urllib.request.urlopen` raises). Synthetic identities only (`@example.invalid`, generated
`54911…` fixture numbers, the operator-signal suite's synthetic destination).

  A  slot-conflict recovery: one claim per losing offer, one explanation, then a fresh Flow;
     a definite failure hands the conversation to a person and sends no unexplained Flow;
  B  the DETAILS submitted at prepare_summary survive a refused confirmation, and the CRM
     card shows them for the winner and the loser;
  C  exactly one operator notice per COMMITTED booking, none for a refusal, a replay or a
     cancellation;
  D  one lead per canonical phone identity (concurrency itself: `test_minimum_launch_pg.py`);
  F  the payment fact stated in other words counts as answered across turns.
"""
from __future__ import annotations

import contextlib
import dataclasses
import logging
import os
import pathlib
import re
import sys
import unittest
from datetime import time, timedelta
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _sb2b_harness as H  # noqa: E402

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (BookingAttemptEvent, BookingCommandRecord, Lead,  # noqa: E402
                        OperatorNotification, Revision, SlotRecoveryJob, ThreadRevision,
                        WhatsAppBookingOffer, WhatsAppMessage, WhatsAppThreadEvidence,
                        WhatsAppThreadState)
from app.services import booking_flow_service as bfs  # noqa: E402
from app.services import booking_slot_recovery as R  # noqa: E402
from app.services.booking_flow_details import (ALL_CLAIM_TYPES, PRODUCER,  # noqa: E402
                                               live_details)

ROOT = pathlib.Path(__file__).resolve().parents[1]
DETAILS = {"name": "Bruno Fixture", "email": "bruno@example.invalid",
           "inspection_address": "Avenida Siempreviva 742", "seller_name": "Vendedor Fixture",
           "seller_phone": "1100000000", "listing_url": "https://example.invalid/aviso/1"}


class Meta(H.Meta):
    """The harness's recorder, plus the Flow sender. `text_modes` / `flow_modes` are
    consumed one per call: ok | 4xx | timeout."""

    def __init__(self, text_modes=("ok",), flow_modes=("ok",)):
        super().__init__("ok")
        self.text_modes, self.flow_modes = list(text_modes), list(flow_modes)
        self.flows: list[tuple[str, str, str]] = []

    @staticmethod
    def _next(modes):
        return modes.pop(0) if len(modes) > 1 else modes[0]

    def _send(self, *, to_wa_id, text):
        self.mode = self._next(self.text_modes)
        return super()._send(to_wa_id=to_wa_id, text=text)

    def _flow(self, *, to_wa_id, flow_id, flow_token, body_text, cta_label="",
              initial_screen="", mode=None):
        from app.ui.whatsapp_ui import MetaSendError
        self.flows.append((to_wa_id, flow_token, mode))
        m = self._next(self.flow_modes)
        if m == "4xx":
            raise MetaSendError(400, '{"error": {"code": 100, "message": "rejected"}}')
        if m == "timeout":
            raise MetaSendError(None, "timed out")
        return f"wamid.MIN.FLOW.{len(self.flows):04d}", 200

    def __enter__(self):
        super().__enter__()
        self._stack.enter_context(mock.patch("app.ui.whatsapp_ui._send_whatsapp_cloud_flow",
                                             self._flow))
        return self


def outbound(engine, thread_id):
    with Session(engine) as db:
        return db.execute(select(WhatsAppMessage.message_type, WhatsAppMessage.path_id,
                                 WhatsAppMessage.status, WhatsAppMessage.text)
                          .where(WhatsAppMessage.thread_id == thread_id,
                                 WhatsAppMessage.direction == "out")
                          .order_by(WhatsAppMessage.id)).all()


def offers(engine, thread_id):
    with Session(engine) as db:
        return db.execute(select(WhatsAppBookingOffer.id, WhatsAppBookingOffer.status,
                                 WhatsAppBookingOffer.candidate_id,
                                 WhatsAppBookingOffer.zone_group)
                          .where(WhatsAppBookingOffer.thread_id == thread_id)
                          .order_by(WhatsAppBookingOffer.id)).all()


def state(engine, thread_id):
    with Session(engine) as db:
        return db.execute(select(WhatsAppThreadState).where(
            WhatsAppThreadState.thread_id == thread_id)).scalar_one()


def events(engine, reason_prefix="SLOT_RECOVERY"):
    with Session(engine) as db:
        return db.execute(select(BookingAttemptEvent.reason, BookingAttemptEvent.outcome,
                                 BookingAttemptEvent.hist_thread_id)
                          .where(BookingAttemptEvent.reason.like(f"{reason_prefix}%"))
                          .order_by(BookingAttemptEvent.id)).all()


def jobs(engine, thread_id=None):
    """R1: the slot-recovery jobs — the admission authority (OLD: attempt-event rows)."""
    with Session(engine) as db:
        q = select(SlotRecoveryJob.state, SlotRecoveryJob.outcome,
                   SlotRecoveryJob.text_attempts, SlotRecoveryJob.flow_attempts)
        if thread_id is not None:
            q = q.where(SlotRecoveryJob.thread_id == thread_id)
        return [tuple(r) for r in db.execute(q.order_by(SlotRecoveryJob.id)).all()]


def work(engine):
    """R1: one pass of the background recovery worker (the production `run_once`), under the
    same settings the Data Exchange ran with. OLD: the recovery ran inside the request."""
    with Session(engine) as db, mock.patch("app.settings.get_settings",
                                           lambda: H.settings(flag=True)):
        return R.run_once(db)


def prepare(engine, w, *, token=None, at=None, **over):
    data = {"date": w.day.isoformat(), "time": (at or w.at).strftime("%H:%M"), **DETAILS,
            **over}
    with Session(engine) as db, mock.patch("app.settings.get_settings",
                                           lambda: H.settings(flag=True)):
        return bfs.BookingFlowService(db).handle_prepare_summary(token or w.token, data)


def confirm(engine, w, *, token=None, at=None, name="Ana Fixture", same_as_prepare=False,
            attempt_ref=None):
    """`same_as_prepare` (R1): the payload the real Flow sends at confirm_booking — the SAME
    details the customer reviewed on SUMMARY (the Flow JSON carries every DETAILS field on
    both triggers). Default: the historical minimal payload."""
    data = {"date": w.day.isoformat(), "time": (at or w.at).strftime("%H:%M"),
            "name": name, "email": "ana@example.invalid",
            "inspection_address": "Calle Falsa 123"}
    if same_as_prepare:
        data.update({k: v for k, v in DETAILS.items() if k != "name"})
    with Session(engine) as db, mock.patch("app.settings.get_settings",
                                           lambda: H.settings(flag=True)):
        return bfs.BookingFlowService(db).handle_confirm_booking(
            token or w.token, data, attempt_ref=attempt_ref)


def race(engine, *, loser_meta=None):
    """Two customers, one slot. The winner books; the loser's confirmation is refused with a
    typed SLOT_CONFLICT. Returns (winner, loser, winner_meta, loser_meta)."""
    win, lose = H.seed(engine), H.seed(engine)
    with Meta() as wm:
        assert confirm(engine, win)["screen"] == "SUCCESS"
    lm = loser_meta or Meta()
    with lm:
        try:
            confirm(engine, lose)
            raise AssertionError("the loser booked")
        except bfs.BookingSlotConflictError:
            pass
        # R1: the Data Exchange request only ADMITS the recovery; nothing is sent in it.
        assert (lm.sent, lm.flows) == ([], []), "a provider call inside Data Exchange"
        work(engine)
    return win, lose, wm, lm


@contextlib.contextmanager
def dispatch_on():
    with mock.patch("app.settings.get_settings", lambda: H.settings(flag=True)):
        yield


# ══ A — slot-conflict recovery ══════════════════════════════════════════════════════════

class A_Recovery(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()

    def test_a01_loser_gets_one_explanation_then_one_fresh_flow_and_no_booking(self):
        win, lose, wm, lm = race(self.engine)
        with Session(self.engine) as db:
            self.assertEqual(db.query(Revision).filter(Revision.lead_id == lose.lead_id).count(), 0)
            self.assertEqual(db.query(ThreadRevision).filter(
                ThreadRevision.thread_id == lose.thread_id).count(), 0)
        out = outbound(self.engine, lose.thread_id)
        texts = [o for o in out if o.message_type == "text"]
        flows = [o for o in out if o.message_type == "flow"]
        self.assertEqual([(t.path_id, t.status, t.text) for t in texts],
                         [("BOOKING_FLOW", "sent", R.RECOVERY_TEXT)])
        self.assertEqual([(f.path_id, f.status) for f in flows], [("BOOKING_FLOW", "sent")])
        # the explanation was sent BEFORE the Flow, and the Flow is data_exchange (INIT reads
        # current availability) on a NEW token
        self.assertEqual([t for _w, t in lm.sent], [R.RECOVERY_TEXT])
        self.assertEqual(len(lm.flows), 1)
        self.assertEqual(lm.flows[0][2], "data_exchange")
        st = state(self.engine, lose.thread_id)
        self.assertEqual(st.flow_booking_token, lm.flows[0][1])
        self.assertNotEqual(st.flow_booking_token, lose.token)
        self.assertEqual(st.last_stage, "SCHEDULING")
        self.assertIsNone(st.preferred_day)          # not narrowed to the lost slot
        # R1: the job raised the flag at admission (it owned the false → true) and cleared
        # exactly that flag on RECOVERED.
        self.assertFalse(st.needs_human)
        o = offers(self.engine, lose.thread_id)
        self.assertEqual([x.status for x in o], ["SUPERSEDED", "ISSUED"])
        self.assertEqual(o[0].candidate_id, o[1].candidate_id)   # same vehicle
        self.assertEqual(o[0].zone_group, o[1].zone_group)
        # R1: OLD one SLOT_RECOVERY_CLAIMED attempt-event row; NEW one recovery job, RECOVERED,
        # and booking_attempt_events carries no recovery row at all (forensic table only).
        self.assertEqual(jobs(self.engine), [("RECOVERED", "RECOVERED", 1, 1)])
        self.assertEqual(events(self.engine), [])
        # SQLite does not enforce VARCHAR widths; PostgreSQL does (measured: a 40-character
        # correlation id made the gate's pending row fail). Every outbound row fits its column.
        width = WhatsAppMessage.__table__.c.correlation_id.type.length
        with Session(self.engine) as db:
            ids = db.execute(select(WhatsAppMessage.correlation_id).where(
                WhatsAppMessage.thread_id == lose.thread_id,
                WhatsAppMessage.direction == "out")).scalars().all()
        self.assertTrue(ids and all(i and len(i) <= width for i in ids), (width, ids))

    def test_a02_no_identity_crossing_winner_gets_only_its_acknowledgement(self):
        win, lose, wm, lm = race(self.engine)
        self.assertEqual([t for _w, t in wm.sent], [H.OWNER_TEXT])
        self.assertTrue(all(to == lose.wa_id for to, _t in lm.sent))
        self.assertTrue(all(to == lose.wa_id for to, _t, _m in lm.flows))
        win_out = outbound(self.engine, win.thread_id)
        self.assertEqual([o.text for o in win_out], [H.OWNER_TEXT])
        self.assertNotIn(R.RECOVERY_TEXT, [o.text for o in win_out])

    def test_a03_replay_of_the_losing_submission_sends_nothing_and_books_nothing(self):
        win, lose, wm, lm = race(self.engine)
        before = outbound(self.engine, lose.thread_id)
        with Meta() as m2:
            with self.assertRaises((bfs.BookingTokenError, bfs.BookingSlotConflictError)):
                confirm(self.engine, lose)                     # the dead token, again
        self.assertEqual(outbound(self.engine, lose.thread_id), before)
        self.assertEqual((m2.sent, m2.flows), ([], []))
        # and a direct second admission for the same losing offer creates nothing (R1: the
        # job's UNIQUE identity; OLD: the attempt-event claim), and the worker sends nothing
        with Session(self.engine) as db:
            ctx = SimpleNamespace(thread=SimpleNamespace(id=lose.thread_id),
                                  contact=SimpleNamespace(wa_id=lose.wa_id, id=None),
                                  lead=SimpleNamespace(id=lose.lead_id), candidate=None)
            losing_offer = offers(self.engine, lose.thread_id)[0].id
            out = R.admit_recovery(db, ctx, losing_offer_id=losing_offer)
        with Meta() as m3:
            work(self.engine)
        self.assertEqual((out.admitted, out.reason, m3.sent, m3.flows),
                         (False, "ALREADY_ADMITTED", [], []))
        self.assertEqual(jobs(self.engine), [("RECOVERED", "RECOVERED", 1, 1)])

    def test_a03b_a_retried_delivery_gets_the_same_answer_and_nothing_more(self):
        """Meta re-sends the SAME envelope (same attempt reference). The recovery has already
        superseded the losing offer; the retry must still get the refusal it got the first
        time — not "session expired" — and must write and send nothing."""
        win, lose = H.seed(self.engine), H.seed(self.engine)
        with Meta():
            confirm(self.engine, win)

        def deliver():
            with Session(self.engine) as db, mock.patch("app.settings.get_settings",
                                                        lambda: H.settings(flag=True)):
                try:
                    bfs.BookingFlowService(db).handle_confirm_booking(lose.token, {
                        "date": lose.day.isoformat(), "time": lose.at.strftime("%H:%M"),
                        "name": "Ana", "inspection_address": "x"}, attempt_ref="dx:" + "a" * 64)
                except bfs.BookingSlotConflictError as exc:
                    return exc.refreshed_data
                raise AssertionError("not a slot refusal")
        with Meta() as m1:
            first = deliver()
            work(self.engine)                   # R1: the recovery is the worker's
        before = (outbound(self.engine, lose.thread_id), offers(self.engine, lose.thread_id),
                  events(self.engine, ""), jobs(self.engine))
        with Meta() as m2:
            second, third = deliver(), deliver()
            work(self.engine)
        self.assertEqual(first, second)
        self.assertEqual(first, third)
        self.assertEqual(len(m1.sent), 1)
        self.assertEqual((m2.sent, m2.flows), ([], []))
        self.assertEqual((outbound(self.engine, lose.thread_id), offers(self.engine, lose.thread_id),
                          events(self.engine, ""), jobs(self.engine)), before)

    def test_a04_loser_books_a_second_available_slot_through_the_fresh_flow(self):
        win, lose, wm, lm = race(self.engine)
        new_token = state(self.engine, lose.thread_id).flow_booking_token
        with Meta() as m:
            screen = confirm(self.engine, lose, token=new_token, at=time(11, 30))
        self.assertEqual(screen["screen"], "SUCCESS")
        self.assertEqual([t for _w, t in m.sent], [H.OWNER_TEXT])
        with Session(self.engine) as db:
            revs = db.query(Revision).filter(Revision.lead_id == lose.lead_id).all()
            self.assertEqual([(r.turno_fecha, r.turno_hora) for r in revs],
                             [(lose.day, time(11, 30))])
            self.assertEqual(db.query(BookingCommandRecord).count(), 2)

    def test_a05_definite_text_failure_twice_no_flow_handoff_to_scheduling(self):
        win, lose, wm, lm = race(self.engine, loser_meta=Meta(text_modes=("4xx",)))
        out = outbound(self.engine, lose.thread_id)
        self.assertEqual([(o.message_type, o.status) for o in out],
                         [("text", "failed"), ("text", "failed")])
        self.assertEqual(lm.flows, [])
        st = state(self.engine, lose.thread_id)
        self.assertEqual((st.last_stage, st.needs_human), ("SCHEDULING", True))
        with Session(self.engine) as db:
            self.assertTrue(db.get(Lead, lose.lead_id).necesita_humano)
        # R1: OLD two attempt-event rows; NEW the job's typed terminal outcome
        self.assertEqual(jobs(self.engine), [("HANDOFF", "TEXT_NOT_DELIVERED", 2, 0)])
        # the losing offer and its evidence are retained, not withdrawn and not deleted
        self.assertEqual([x.status for x in offers(self.engine, lose.thread_id)], ["ISSUED"])

    def test_a06_ambiguous_text_is_never_retried_and_no_flow_follows(self):
        win, lose, wm, lm = race(self.engine, loser_meta=Meta(text_modes=("timeout",)))
        out = outbound(self.engine, lose.thread_id)
        self.assertEqual([(o.message_type, o.status) for o in out], [("text", "failed")])
        self.assertEqual(lm.flows, [])
        st = state(self.engine, lose.thread_id)
        self.assertEqual((st.last_stage, st.needs_human), ("SCHEDULING", True))
        self.assertEqual(jobs(self.engine), [("HANDOFF", "TEXT_UNCERTAIN", 1, 0)])

    def test_a07_definite_then_success_is_one_sent_text_and_one_flow(self):
        win, lose, wm, lm = race(self.engine, loser_meta=Meta(text_modes=("4xx", "ok")))
        out = outbound(self.engine, lose.thread_id)
        self.assertEqual([(o.message_type, o.status) for o in out],
                         [("text", "failed"), ("text", "sent"), ("flow", "sent")])

    def test_a08_definite_flow_failure_withdraws_the_new_offer_and_hands_off(self):
        win, lose, wm, lm = race(self.engine, loser_meta=Meta(flow_modes=("4xx",)))
        st = state(self.engine, lose.thread_id)
        self.assertIsNone(st.flow_booking_token)
        self.assertEqual((st.last_stage, st.needs_human), ("SCHEDULING", True))
        self.assertEqual([x.status for x in offers(self.engine, lose.thread_id)],
                         ["SUPERSEDED", "WITHDRAWN"])
        self.assertEqual(len(lm.flows), 1)                     # never retried

    def test_a09_kill_switch_blocks_the_text_definitely_and_nothing_leaves(self):
        win, lose = H.seed(self.engine), H.seed(self.engine)
        with Meta():
            confirm(self.engine, win)
        with H.kill_switch(), mock.patch("app.ui.whatsapp_ui._send_whatsapp_cloud_text",
                                         side_effect=AssertionError("provider called")):
            with self.assertRaises(bfs.BookingSlotConflictError):
                confirm(self.engine, lose)
            work(self.engine)                   # R1: the worker meets the kill switch
        self.assertEqual([o.status for o in outbound(self.engine, lose.thread_id)],
                         ["blocked", "blocked"])
        st = state(self.engine, lose.thread_id)
        self.assertEqual((st.last_stage, st.needs_human), ("SCHEDULING", True))

    def test_a10_copy_is_plain_spanish_with_the_three_required_facts_and_no_internals(self):
        t = R.RECOVERY_TEXT.lower()
        self.assertIn("dejar de estar disponible", t)
        self.assertIn("no se confirmó ningún turno", t)
        self.assertIn("podés elegir otro horario", t)
        for forbidden in ("slot_conflict", "overlap", "error", "exception", "sql",
                          "constraint", "reason", "_"):
            self.assertNotIn(forbidden, t)

    def test_a11_only_a_genuine_slot_conflict_recovers(self):
        """Smart Booking off (a refusal that is not SLOT_CONFLICT) never sends the copy."""
        w = H.seed(self.engine)
        with Meta() as m, Session(self.engine) as db, \
                mock.patch("app.settings.get_settings", lambda: H.settings(flag=False)):
            with self.assertRaises(bfs.BookingSlotConflictError):
                bfs.BookingFlowService(db).handle_confirm_booking(w.token, {
                    "date": w.day.isoformat(), "time": w.at.strftime("%H:%M"),
                    "name": "Ana", "inspection_address": "x"})
            work(self.engine)
        self.assertNotIn(R.RECOVERY_TEXT, [t for _w, t in m.sent])
        self.assertEqual(events(self.engine, "SLOT_RECOVERY"), [])
        self.assertEqual(jobs(self.engine), [])

    def test_a12_identities_are_per_losing_offer_and_bounded(self):
        a, b = R.recovery_identity(5), R.recovery_identity(6)
        self.assertNotEqual(a, b)
        self.assertEqual(len({R.delivery_identity(a, "text", 1), R.delivery_identity(a, "text", 2),
                              R.delivery_identity(a, "flow")}), 3)
        with self.assertRaises(ValueError):
            R.delivery_identity(a, "text", 3)
        with self.assertRaises(ValueError):
            R.delivery_identity(a, "flow", 2)

    def test_a13_the_meta_refusal_screen_is_unchanged_and_not_the_mechanism(self):
        win, lose = H.seed(self.engine), H.seed(self.engine)
        with Meta():
            confirm(self.engine, win)
        with Meta() as m:
            try:
                confirm(self.engine, lose)
            except bfs.BookingSlotConflictError as exc:
                screen = exc.refreshed_data
            in_request = list(m.sent)
            work(self.engine)
        self.assertEqual(screen["screen"], "APPOINTMENT")
        self.assertTrue(screen["data"]["has_refusal"])
        # R1: nothing in the request; the customer-facing recovery went out of band, from the
        # worker, in the conversation
        self.assertEqual(in_request, [])
        self.assertEqual([t for _w, t in m.sent], [R.RECOVERY_TEXT])

    def test_a14_published_flow_json_is_not_part_of_this_change(self):
        src = (ROOT / "backend" / "app" / "services" / "booking_slot_recovery.py").read_text()
        self.assertNotIn("meta/flows", src)
        self.assertNotIn("routing_model", src)


# ══ B — DETAILS preserved; the CRM read model ═══════════════════════════════════════════

class B_Details(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()

    def rows(self, thread_id):
        with Session(self.engine) as db:
            return db.execute(select(WhatsAppThreadEvidence).where(
                WhatsAppThreadEvidence.thread_id == thread_id,
                WhatsAppThreadEvidence.producer == PRODUCER)
                .order_by(WhatsAppThreadEvidence.id)).scalars().all()

    def test_b01_prepare_summary_persists_every_value_bound_and_draft(self):
        w = H.seed(self.engine)
        with Meta() as m:
            self.assertEqual(prepare(self.engine, w)["screen"], "SUMMARY")
        rows = self.rows(w.thread_id)
        self.assertEqual(sorted(r.claim_type for r in rows), sorted(ALL_CLAIM_TYPES))
        for r in rows:
            self.assertEqual((r.outcome, r.status, r.bound_to_candidate_id, r.role),
                             ("HOLD", "DRAFT", w.candidate_id, None))
            self.assertTrue(r.turn_id.startswith("offer:"))
        self.assertEqual(live_details(Session(self.engine), [w.thread_id])[w.thread_id]
                         ["FLOW_REQUESTED_SLOT"],
                         f"{w.day.isoformat()} {w.at.strftime('%H:%M')}")
        # no booking, revision, offer change or message
        with Session(self.engine) as db:
            self.assertEqual(db.query(Revision).count(), 0)
            self.assertEqual(db.query(BookingCommandRecord).count(), 0)
        self.assertEqual([x.status for x in offers(self.engine, w.thread_id)], ["ISSUED"])
        self.assertEqual((m.sent, m.flows, outbound(self.engine, w.thread_id)), ([], [], []))

    def test_b02_replay_is_idempotent(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        n = len(self.rows(w.thread_id))
        prepare(self.engine, w)
        prepare(self.engine, w)
        self.assertEqual(len(self.rows(w.thread_id)), n)

    def test_b03_a_correction_supersedes_and_keeps_history(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        prepare(self.engine, w, inspection_address="Otra Calle 99")
        rows = [r for r in self.rows(w.thread_id) if r.claim_type == "FLOW_INSPECTION_ADDRESS"]
        self.assertEqual([r.value for r in rows], ["Avenida Siempreviva 742", "Otra Calle 99"])
        self.assertEqual(rows[0].superseded_by_id, rows[1].id)
        self.assertIsNone(rows[1].superseded_by_id)
        self.assertEqual(live_details(Session(self.engine), [w.thread_id])[w.thread_id]
                         ["FLOW_INSPECTION_ADDRESS"], "Otra Calle 99")
        # A -> B -> A again is a new live row, not a resurrection of the superseded one
        prepare(self.engine, w)
        live = live_details(Session(self.engine), [w.thread_id])[w.thread_id]
        self.assertEqual(live["FLOW_INSPECTION_ADDRESS"], "Avenida Siempreviva 742")
        self.assertEqual(len([r for r in self.rows(w.thread_id)
                              if r.claim_type == "FLOW_INSPECTION_ADDRESS"]), 3)
        # an optional field left empty is not a correction
        prepare(self.engine, w, seller_name="")
        self.assertEqual(live_details(Session(self.engine), [w.thread_id])[w.thread_id]
                         ["FLOW_SELLER_NAME"], "Vendedor Fixture")

    def test_b04_losers_details_survive_the_slot_conflict(self):
        win, lose = H.seed(self.engine), H.seed(self.engine)
        prepare(self.engine, win, name="Ana Ganadora")
        prepare(self.engine, lose)
        # R1 adaptation: confirm_booking now persists the submitted details AGAIN (and a
        # differing value supersedes), so each customer confirms what they reviewed — as the
        # real Flow does — instead of the historical minimal payload.
        with Meta():
            confirm(self.engine, win, name="Ana Ganadora", same_as_prepare=True)
        with Meta():
            with self.assertRaises(bfs.BookingSlotConflictError):
                confirm(self.engine, lose, name="Bruno Fixture", same_as_prepare=True)
        live = live_details(Session(self.engine), [lose.thread_id])[lose.thread_id]
        self.assertEqual(live["FLOW_CUSTOMER_NAME"], "Bruno Fixture")
        self.assertEqual(live["FLOW_SELLER_PHONE"], "1100000000")
        self.assertEqual(live_details(Session(self.engine), [win.thread_id])[win.thread_id]
                         ["FLOW_CUSTOMER_NAME"], "Ana Ganadora")

    def test_b05_crm_card_shows_winner_and_loser_details(self):
        from app.services.lead_booking_details import lead_booking_details
        from app.ui.kanban_view import render_lead_card
        win, lose = H.seed(self.engine), H.seed(self.engine)
        prepare(self.engine, win, name="Ana Ganadora")
        prepare(self.engine, lose)
        # R1 adaptation (as b04): each customer confirms what they reviewed on SUMMARY.
        with Meta():
            confirm(self.engine, win, name="Ana Ganadora", same_as_prepare=True)
        with Meta():
            with self.assertRaises(bfs.BookingSlotConflictError):
                confirm(self.engine, lose, name="Bruno Fixture", same_as_prepare=True)
        with Session(self.engine) as db:
            details = lead_booking_details(db, [win.lead_id, lose.lead_id])
            self.assertEqual(set(details), {win.lead_id, lose.lead_id})
            loser = details[lose.lead_id]
            self.assertEqual((loser.customer_name, loser.vehicle, loser.locality,
                              loser.inspection_address, loser.seller_name),
                             ("Bruno Fixture", "Peugeot 208 (Auto)", "Benavidez, Norte",
                              "Avenida Siempreviva 742", "Vendedor Fixture"))
            self.assertEqual(loser.requested_slot,
                             f"{lose.day.isoformat()} {lose.at.strftime('%H:%M')}")
            for lead_id, who in ((lose.lead_id, "Bruno Fixture"), (win.lead_id, "Ana Ganadora")):
                html = render_lead_card(db.get(Lead, lead_id), booking_details=details[lead_id])
                for fragment in (who, "Peugeot 208", "Benavidez", "Avenida Siempreviva 742",
                                 "Vendedor Fixture", "1100000000", lose.day.isoformat()):
                    self.assertIn(fragment, html)
            # nothing was copied into the lead
            self.assertIsNone(db.get(Lead, lose.lead_id).nombre)

    def test_b06_card_escapes_customer_text(self):
        from app.services.lead_booking_details import LeadBookingDetails
        from app.ui.kanban_view import render_booking_details_block
        html = render_booking_details_block(LeadBookingDetails(
            thread_id=1, inspection_address="<script>x</script>"))
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_b07_no_customer_value_reaches_the_logs(self):
        w = H.seed(self.engine)
        # Other suites in the same process call `logging.disable(...)`; measured in the full
        # run: with logging globally disabled this assertion saw no record at all. It is
        # re-enabled for this test only and restored afterwards.
        previous = logging.root.manager.disable
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, previous)
        # ...and a `logging.config.dictConfig(...)` elsewhere marks every existing logger
        # `disabled` (measured: still no record after the line above). The app's loggers are
        # re-enabled for this test and their exact state restored afterwards.
        import app.services.booking_flow_details  # noqa: F401 — its logger must exist
        saved = []
        for name, lg in list(logging.root.manager.loggerDict.items()):
            if isinstance(lg, logging.Logger) and (name == "app" or name.startswith("app.")):
                saved.append((lg, lg.disabled, lg.propagate))
                lg.disabled, lg.propagate = False, True

        def _restore():
            for lg, disabled, propagate in saved:
                lg.disabled, lg.propagate = disabled, propagate
        self.addCleanup(_restore)
        with self.assertLogs(level=logging.DEBUG) as logs:
            logging.getLogger("app").info("probe")
            prepare(self.engine, w)
        self.assertTrue(any("BOOKING_FLOW_DETAILS persisted" in line for line in logs.output),
                        logs.output)
        text = "\n".join(logs.output)
        for value in DETAILS.values():
            self.assertNotIn(value, text)

    def test_b08_a_fresh_flow_prefills_the_name_the_customer_already_gave(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        with Session(self.engine) as db, dispatch_on():
            ctx = bfs.BookingFlowService(db).resolve_context(w.token)
            self.assertEqual(bfs.BookingFlowService(db)._identity_fields(ctx)["customer_name"],
                             "Bruno Fixture")

    def test_b09_an_unresolvable_token_persists_nothing(self):
        w = H.seed(self.engine)
        with self.assertRaises(bfs.BookingTokenError):
            prepare(self.engine, w, token=w.token + "x")
        self.assertEqual(self.rows(w.thread_id), [])

    def test_b10_new_rows_never_reach_the_locality_binder(self):
        from app.services.thread_evidence import bindable_locality
        w = H.seed(self.engine)
        prepare(self.engine, w)
        with Session(self.engine) as db:
            self.assertIsNone(bindable_locality(db, thread_id=w.thread_id, cycle_key=0))


# ══ C — operator notification ═══════════════════════════════════════════════════════════

class C_OperatorNotice(unittest.TestCase):

    def setUp(self):
        from test_w1f_operator_signal import set_floor
        self.engine = H.sqlite_engine()
        set_floor(self.engine, "0")

    def run_pass(self, cfg=None, template=None):
        from app.services import operator_notification as ON
        from test_w1f_operator_signal import notifier
        with notifier(cfg=cfg, template=template) as tpl:
            db = H.session_factory(self.engine)()
            try:
                ON.run_once(db)
            finally:
                db.close()
        return tpl

    def notices(self):
        with Session(self.engine) as db:
            return db.execute(select(OperatorNotification.booking_command_id,
                                     OperatorNotification.status,
                                     OperatorNotification.attempts,
                                     OperatorNotification.last_error)
                              .order_by(OperatorNotification.id)).all()

    def test_c01_one_notice_per_committed_booking_none_for_the_refusal(self):
        win, lose, wm, lm = race(self.engine)
        tpl = self.run_pass()
        n = self.notices()
        self.assertEqual([(x.status, x.attempts) for x in n], [("SENT", 1)])
        self.assertEqual(len(tpl.sent), 1)
        with Session(self.engine) as db:
            only = db.query(BookingCommandRecord).one()
            self.assertEqual(only.thread_id, win.thread_id)
        # the message carries the operational facts, from canonical rows
        params = dict(zip(__import__("app.services.operator_notification",
                                     fromlist=["x"]).TEMPLATE_PARAMETERS, tpl.sent[0]["params"]))
        self.assertIn("Peugeot 208", params["vehiculo"])
        self.assertIn("Benavidez", params["lugar"])
        self.assertIn(win.day.strftime("%d/%m/%Y"), params["fecha_hora"])
        self.assertEqual(params["whatsapp_cliente"], win.wa_id)

    def test_c02_replay_and_a_second_pass_create_and_send_nothing_more(self):
        win = H.seed(self.engine)
        with Meta():
            confirm(self.engine, win)
        self.run_pass()
        with Meta() as m:
            self.assertEqual(confirm(self.engine, win)["screen"], "SUCCESS")   # replay
        tpl = self.run_pass()
        self.assertEqual([(x.status, x.attempts) for x in self.notices()], [("SENT", 1)])
        self.assertEqual(tpl.sent, [])
        self.assertEqual(m.sent, [])                                         # no 2nd ack

    def test_c03_a_definite_failure_then_retry_is_still_exactly_one_success(self):
        from test_w1f_operator_signal import Template
        win = H.seed(self.engine)
        with Meta():
            confirm(self.engine, win)
        from datetime import datetime, timezone
        from app.services import operator_notification as ON
        from test_w1f_operator_signal import notifier
        tpl = Template("4xx", "ok")
        with notifier(template=tpl):
            db = H.session_factory(self.engine)()
            now = datetime.now(timezone.utc)
            ON.run_once(db, now=now)
            ON.run_once(db, now=now + timedelta(seconds=ON.RETRY_AFTER_SECONDS + 1))
            ON.run_once(db, now=now + timedelta(seconds=3 * ON.RETRY_AFTER_SECONDS))
            db.close()
        self.assertEqual([(x.status, x.attempts) for x in self.notices()], [("SENT", 2)])
        self.assertEqual([s["mode"] for s in tpl.sent], ["4xx", "ok"])

    def test_c04_a_cancelled_booking_is_never_announced(self):
        win = H.seed(self.engine)
        with Meta():
            confirm(self.engine, win)
        with Session(self.engine) as db:
            rev = db.query(Revision).one()
            rev.estado_revision = "CANCELADO"
            db.commit()
        tpl = self.run_pass()
        self.assertEqual([(x.status, x.attempts, x.last_error) for x in self.notices()],
                         [("HELD", 1, "BOOKING_NOT_OCCUPYING")])
        self.assertEqual(tpl.sent, [])

    def test_c05_disabled_flag_holds_and_a_held_row_is_never_sent_later(self):
        from test_w1f_operator_signal import op_settings
        win = H.seed(self.engine)
        with Meta():
            confirm(self.engine, win)
        self.run_pass(cfg=op_settings(operator_whatsapp_notify_enabled=False))
        self.assertEqual([(x.status, x.attempts, x.last_error) for x in self.notices()],
                         [("HELD", 0, "NOT_CONFIGURED_DISABLED")])
        tpl = self.run_pass()                         # the flag is now on
        self.assertEqual(tpl.sent, [])
        self.assertEqual([x.status for x in self.notices()], ["HELD"])

    def test_c06_no_live_number_in_the_changed_sources(self):
        for rel in ("backend/app/services/booking_slot_recovery.py",
                    "backend/app/services/booking_flow_details.py",
                    "backend/app/services/lead_identity.py",
                    "backend/app/services/lead_booking_details.py"):
            src = (ROOT / rel).read_text()
            self.assertIsNone(re.search(r"549\d{8,}", src), rel)


# ══ D — one lead per canonical phone identity ═══════════════════════════════════════════

class D_LeadIdentity(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()

    def post(self, telefono, **extra):
        from app.api.leads import create_lead
        from app.schemas.leads import LeadCreate
        with H.session_factory(self.engine)() as db:
            return create_lead(LeadCreate(telefono=telefono, **extra), db).id

    def test_d01_plus_and_digits_forms_are_one_lead(self):
        a = self.post("+5491100000001", nombre="Primera")
        b = self.post("5491100000001", nombre="Segunda")
        c = self.post("+54 9 11 0000-0001")
        self.assertEqual(a, b)
        self.assertEqual(a, c)
        with Session(self.engine) as db:
            self.assertEqual(db.query(Lead).count(), 1)
            self.assertEqual(db.get(Lead, a).nombre, "Primera")    # never overwritten

    def test_d02_leads_without_a_phone_stay_legal_and_distinct(self):
        self.assertNotEqual(self.post(None), self.post(None))

    def test_d03_lookup_matches_the_canonical_identity(self):
        from app.api.leads import list_leads
        a = self.post("5491100000002")
        with H.session_factory(self.engine)() as db:
            self.assertEqual([l.id for l in list_leads("+5491100000002", db)], [a])
            self.assertEqual([l.id for l in list_leads("5491100000003", db)], [])

    def test_d04_ui_create_never_adds_a_second_lead_for_a_phone(self):
        from app.ui.kanban_actions import ui_lead_create
        self.post("5491100000004")
        with H.session_factory(self.engine)() as db:
            ui_lead_create(nombre="X", apellido=None, telefono="+5491100000004", tel=None,
                           email=None, canal=None, compro_el_auto=None, db=db)
        with Session(self.engine) as db:
            self.assertEqual(db.query(Lead).count(), 1)

    def test_d05_index_literals_are_the_migrations(self):
        import importlib.util
        from app.services import phone_utils as P
        spec = importlib.util.spec_from_file_location(
            "m_lead_identity", ROOT / "backend" / "migrations" / "versions"
            / "20261102_lead_phone_identity.py")
        src = spec.origin and pathlib.Path(spec.origin).read_text()
        self.assertIn(f'IDENTITY = "{P.PHONE_IDENTITY_SQL}"', src)
        self.assertIn(f'PREDICATE = "{P.PHONE_IDENTITY_PREDICATE}"', src)
        self.assertLessEqual(len("20261102_lead_phone_identity"), 32)
        idx = [i for i in Lead.__table__.indexes if i.name == "uq_leads_phone_identity"]
        self.assertEqual(len(idx), 1)
        self.assertTrue(idx[0].unique)
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.schema import CreateIndex
        ddl = str(CreateIndex(idx[0]).compile(dialect=postgresql.dialect()))
        self.assertIn(P.PHONE_IDENTITY_SQL, ddl)
        self.assertIn(P.PHONE_IDENTITY_PREDICATE, ddl)
        for forbidden in ("DELETE FROM", "UPDATE leads", "TRUNCATE"):
            self.assertNotIn(forbidden, src)
        # the only statements it executes: the index build and the index drop
        self.assertEqual(re.findall(r"op\.execute\(([^)]*)\)", src),
                         ["_DDL", 'f"DROP INDEX {INDEX}"'])

    def test_d06_canonical_identity_is_digits_only(self):
        from app.services.phone_utils import canonical_phone_identity
        self.assertEqual(canonical_phone_identity("+54 9 (11) 0000-0001"), "5491100000001")
        self.assertIsNone(canonical_phone_identity(""))
        self.assertIsNone(canonical_phone_identity(None))


# ══ F — payment fact across turns ═══════════════════════════════════════════════════════

class F_PaymentDedup(unittest.TestCase):

    def answered(self, *bodies):
        from app.services.conversation_engine import ConversationEngine
        eng = ConversationEngine.__new__(ConversationEngine)
        msgs = [SimpleNamespace(direction="out", id=i + 10, text=b) for i, b in enumerate(bodies)]
        ctx = SimpleNamespace(state=SimpleNamespace(current_cycle_start_message_db_id=None),
                              db_messages=msgs)
        return eng._topics_already_answered_this_cycle(ctx)

    def test_f01_payment_in_other_words_counts_as_answered(self):
        self.assertIn("payment", self.answered(
            "En cuanto al pago, aceptamos transferencia bancaria, Mercado Pago y efectivo, "
            "pero no cheque."))

    def test_f02_a_contradicting_statement_never_counts(self):
        self.assertNotIn("payment", self.answered(
            "Aceptamos efectivo, transferencia y también débito."))

    def test_f03_other_topics_keep_the_verbatim_rule(self):
        self.assertNotIn("report", self.answered("Te enviamos el informe al terminar."))

    def test_f04_the_canonical_answer_still_counts(self):
        from app.services.conversation_engine import _FAQ_PAYMENT_ANSWER
        self.assertIn("payment", self.answered(_FAQ_PAYMENT_ANSWER))


if __name__ == "__main__":
    unittest.main()
