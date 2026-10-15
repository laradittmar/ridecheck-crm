"""PRELAUNCH-SB2B-OPTION-B-CE-INTEGRATION — CE booking paths on the booking authority.

D1 Option B, binding rules B-1..B-8: adapter first, authoritative commit first, CE
synchronization second. Every scenario runs the production `ConversationEngine.handle()` on a
production-shaped session; the booking authority, the CE adapter, the outbound gate and the
Data Exchange confirmation are the production code. Only the Meta edge is a recorder (see
`_sb2b_harness`). The cases in `OptionBCases` run here on SQLite and again, unchanged, on a
disposable PostgreSQL in `test_sb2b_option_b_pg.py`, where transaction behaviour is real.

SQLite is NOT concurrency or lock evidence; it proves wiring, ordering and truthfulness.
"""
from __future__ import annotations

import inspect
import json
import os
import pathlib
import sys
import unittest
from datetime import datetime, time, timedelta, timezone
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _sb2b_harness as H  # noqa: E402

from sqlalchemy import select, text, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (BookingCommandRecord, HybridDecisionTraceRow, Lead, Profesional,  # noqa: E402
                        Revision, WhatsAppThreadState)
from app.schemas.conversation import HANDLED_ACTIONS  # noqa: E402
from app.services import booking_ce_adapter as adapter  # noqa: E402
from app.services import conversation_engine as ce_mod  # noqa: E402
from app.services.booking_authority import (BookingAuthority, BookingOutcome as Out,  # noqa: E402
                                            create_calendar_exception, flow_idempotency_key)
from app.services.booking_recovery import recover_acknowledgement, stored_inbound_event  # noqa: E402
from app.services.conversation_engine import ConversationEngine  # noqa: E402

NEW_ACTIONS = ("booking_replayed", "booking_retry_required", "booking_refused_handoff")


class Crash(BaseException):
    """Process death: not an Exception, so nothing in CE catches it; the session closes
    uncommitted, exactly like a killed worker."""


class _Spy:
    """Counts calls to a module-level function (or, with `owner`, a method) while
    delegating to the real one."""

    def __init__(self, target: str, owner=None):
        self.target, self.owner, self.calls = target, owner, 0
        if owner is not None:
            self.real = getattr(owner, target)
        else:
            mod, name = target.rsplit(".", 1)
            self.real = getattr(__import__(mod, fromlist=[name]), name)

    def __call__(self, *a, **k):
        self.calls += 1
        return self.real(*a, **k)

    def __enter__(self):
        if self.owner is not None:
            spy = self

            def method(inner, *a, **k):
                spy.calls += 1
                return spy.real(inner, *a, **k)
            self._p = mock.patch.object(self.owner, self.target, method)
        else:
            self._p = mock.patch(self.target, self)
        self._p.start()
        return self

    def __exit__(self, *e):
        self._p.stop()


class OptionBCases:
    """Shared by SQLite (here) and PostgreSQL (test_sb2b_option_b_pg)."""

    engine = None

    # -- helpers -----------------------------------------------------------
    def turn(self, w, *, cfg=None, event=None, meta="ok", hook=None):
        with H.Meta(meta) as m:
            out, eng = H.run_turn(self.engine, event or H.flow_event(w), cfg=cfg,
                                  engine_hook=hook)
        return out, eng, m

    def replay(self, w, *, cfg=None, meta="ok", hook=None):
        """The approved recovery entry: the STORED inbound event, rebuilt and re-POSTed."""
        with Session(self.engine) as db:
            event = stored_inbound_event(db, w.wamid)
        self.assertIsNotNone(event)
        return self.turn(w, cfg=cfg, event=event, meta=meta, hook=hook)

    def f(self, w):
        return H.facts(self.engine, w)

    def owner_sends(self, m):
        return [t for _w, t in m.sent if t == H.OWNER_TEXT]

    def attention(self):
        """The /control rows as an operator would see them once the approved age has
        passed (the age itself is a parameter, so no clock is faked)."""
        from app.routes.ops_dashboard import booking_attention
        with Session(self.engine) as db:
            return booking_attention(db, pending_older_than_seconds=0)

    # ══ 1. CE-W1 steady state ═══════════════════════════════════════════════
    def test_sb2b_01_steady_state_books_once_and_synchronizes(self):
        w = H.seed(self.engine)
        with _Spy("app.services.booking_ce_adapter.book_from_flow_response") as spy:
            out, eng, m = self.turn(w, cfg=H.settings(trace=True))
        f = self.f(w)
        self.assertEqual((out.action, out.handled, out.detail), ("booking_created", True, "ack:SENT"))
        self.assertEqual(spy.calls, 1)
        self.assertEqual([k for k, *_ in f["ledger"]], [flow_idempotency_key(w.token)])   # B-1
        self.assertEqual(f["ledger"][0][1:4], ("CONVERSATION_ENGINE", "CREATE", "SENT"))
        self.assertEqual(f["occupying"], 1)
        self.assertEqual([(d, t) for _i, d, t, _s in f["revisions"]], [(w.day, w.at)])
        self.assertEqual([s for _i, s in f["thread_revisions"]], ["booked"])
        self.assertEqual((f["cursor"], f["needs_human"], f["last_stage"], f["token"]),
                         (w.wamid, True, "BOOKED", None))
        self.assertEqual(f["current_revision_id"], f["thread_revisions"][0][0])
        self.assertEqual(m.sent, [(w.wa_id, H.OWNER_TEXT)])                             # B-5
        self.assertEqual(f["outbound"], [("BOOKING_FLOW", "sent", H.OWNER_TEXT)])
        self.assertNotIn("LOCK_TIMEOUT", json.dumps(eng._sb_trace_payload()))
        self.assertEqual(m.egress, [])

    def test_sb2b_02_availability_is_revalidated_under_the_authority(self):
        # free in the agenda, outside business hours: only ScheduleService can refuse it
        w = H.seed(self.engine, at=time(21, 30))
        out, eng, m = self.turn(w)
        f = self.f(w)
        self.assertEqual((out.action, out.detail), ("booking_refused_handoff", "SCHEDULE_REVALIDATION"))
        self.assertEqual(f["occupying"], 0)
        self.assertTrue(eng._sb_trace_payload()["availability_revalidated"])
        self.assertNotIn(H.OWNER_TEXT, [t for _w, t in m.sent])
        self.assertEqual([t for _w, t in m.sent], [H.HANDOFF_TEXT])

    def test_sb2b_03_adapter_refuses_an_occupying_ce_booking_without_revalidation(self):
        w = H.seed(self.engine)
        with Session(self.engine) as db:
            r = adapter.book_from_flow_response(db, adapter.CeFlowBookingRequest(
                thread_id=w.thread_id, lead_id=w.lead_id, inbound_event_id=w.wamid,
                flow_token=w.token, requested_date=w.day, requested_time=w.at))
        self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, "AVAILABILITY_REVALIDATION_REQUIRED"))
        self.assertEqual(self.f(w)["ledger"], [])

    # ══ 2. dirty-transaction guard ══════════════════════════════════════════
    def test_sb2b_04_cycle_reset_flush_is_refused_before_the_authority(self):
        w = H.seed(self.engine, cycle_reset=True)          # focus candidate → reset flushes
        with _Spy("app.services.booking_ce_adapter.book_from_flow_response") as spy:
            out, eng, m = self.turn(w)
        f = self.f(w)
        # Convergence Phase 3 adaptation. OLD: detail == CE_TRANSACTION_NOT_CLEAN (the txid
        # guard caught the reset's flush). NEW: the turn is refused EARLIER, as a read — the
        # reset withdrew the offer through the offer authority and cleared its mirror, so the
        # submitted token is no longer the thread's (design §12 stage 3: a reset turn never
        # delegates). WHY: on the converged branch the token is checked before the guard. The
        # invariant is stronger, not weaker: the authority is not called (spy 0), nothing is
        # booked, the offer is WITHDRAWN for CYCLE_RESET, and the guard itself is still
        # proven by sb2b_05 and pg_02. Under the old order (guard first) this assertion fails.
        self.assertEqual((out.action, out.detail), ("booking_refused_handoff", "FLOW_TOKEN_NOT_ACTIVE"))
        if self.engine.dialect.name == "postgresql":
            # SQLite (StaticPool) shares ONE connection between CE and every "isolated"
            # session, so the ledger re-read before the handoff rolls the reset's Core-level
            # offer UPDATE back there — an artefact no real pool has. The durable withdrawal
            # is asserted where sessions are real connections (the PostgreSQL twin).
            with Session(self.engine) as db:
                from app.models import WhatsAppBookingOffer
                self.assertEqual(db.execute(select(WhatsAppBookingOffer.status,
                                                   WhatsAppBookingOffer.terminal_reason).where(
                    WhatsAppBookingOffer.thread_id == w.thread_id)).all(),
                    [("WITHDRAWN", "CYCLE_RESET")])
        self.assertEqual(spy.calls, 0)
        self.assertEqual((f["ledger"], f["revisions"], f["occupying"]), ([], [], 0))
        self.assertEqual((f["cursor"], f["needs_human"], f["last_stage"]), (w.wamid, True, "HUMAN_REQUIRED"))
        self.assertEqual(self.owner_sends(m), [])
        self.assertFalse(eng._sb_trace_payload()["authority_called"])

    def test_sb2b_05_guard_reports_written_transactions_only(self):
        w = H.seed(self.engine)
        db = H.session_factory(self.engine)()
        try:
            eng = ConversationEngine(db=db, settings=H.settings())
            db.execute(select(Lead).where(Lead.id == w.lead_id)).all()
            self.assertTrue(eng._sb_transaction_clean())            # reads assign nothing
            db.get(Lead, w.lead_id).flag = "X"
            self.assertTrue(eng._sb_transaction_clean())            # dirty but unflushed
            db.flush()
            self.assertFalse(eng._sb_transaction_clean())           # written
        finally:
            db.rollback(); db.close()

    # ══ 3. crash matrix + stored-inbound replay ═════════════════════════════
    def _crash_then_replay(self, w, patcher):
        with patcher:
            with self.assertRaises(Crash):
                self.turn(w)
        before = self.f(w)
        # what /control shows between the crash and the operator's replay
        before["attention"] = [x for r in self.attention_rows_before(w) for x in r]
        with _Spy("execute", BookingAuthority) as ex:
            out, eng, m = self.replay(w)
        return before, out, eng, m, ex

    def _assert_recovered(self, w, out, ex):
        f = self.f(w)
        self.assertEqual(out.action, "booking_replayed")           # never skipped_human
        self.assertEqual(ex.calls, 0)                              # no execute after a hit
        self.assertEqual((len(f["ledger"]), f["occupying"]), (1, 1))
        self.assertEqual(f["cursor"], w.wamid)
        self.assertEqual(f["ledger"][0][3], "SENT")
        return f

    def test_sb2b_06_crash_after_authority_commit(self):
        w = H.seed(self.engine)
        before, out, eng, m, ex = self._crash_then_replay(w, mock.patch.object(
            ConversationEngine, "_sb_acknowledge", side_effect=Crash()))
        self.assertEqual((len(before["ledger"]), before["ledger"][0][3], before["cursor"]),
                         (1, "PENDING", None))
        self.assertTrue(before["needs_human"])                     # the D1-02 trap is set
        # Convergence Phase 4 adaptation (SB2B-RV-04): a crash right after the authority commit
        # also means the operator e-mail was never sent — now listed (design §13: "/control
        # must list it"). The exact list grows by that one truthful reason.
        self.assertEqual(before["attention"], ["ACK_PENDING_STALE", "CE_SYNC_INCOMPLETE",
                                               "OPERATOR_NOTIFICATION_PENDING"])
        self._assert_recovered(w, out, ex)
        # Convergence Phase 4 adaptation (SB2B-RV-04). OLD: after the replay nothing was listed.
        # NEW: the acknowledgement and the CE synchronisation are resolved, and exactly one
        # reason remains — the operator e-mail, which a ledger replay never sends. WHY: that
        # e-mail really was lost with the crash; hiding it was the RV-04 gap. PROOF: on the
        # parent the operator surface knows nothing about notifications, so the list is [].
        self.assertEqual(self.attention_rows_before(w), [["OPERATOR_NOTIFICATION_PENDING"]])
        self.assertEqual(len(self.owner_sends(m)), 1)

    def attention_rows_before(self, w):
        return [r["reasons"] for r in self.attention() if r["thread_id"] == w.thread_id]

    def test_sb2b_07_crash_after_acknowledgement_claim(self):
        w = H.seed(self.engine)
        real = BookingAuthority.claim_acknowledgement

        def claim_then_die(auth, key, **k):
            real(auth, key, **k)
            raise Crash()
        before, out, eng, m, ex = self._crash_then_replay(
            w, mock.patch.object(BookingAuthority, "claim_acknowledgement", claim_then_die))
        self.assertEqual(before["ledger"][0][3], "SENDING")
        # inside the lease the replay is SILENT: the claimant may still be sending
        f = self.f(w)
        self.assertEqual((out.action, f["ledger"][0][3], self.owner_sends(m)), ("booking_replayed", "SENDING", []))
        self.assertEqual(f["cursor"], w.wamid)
        self._expire_lease(w)
        self.assertIn("ACK_LEASE_GONE", [x for r in self.attention_rows_before(w) for x in r])
        # Convergence Phase 3-R1 adaptation. OLD: after expiry, one bounded reclaim sent the
        # acknowledgement (duplicate risk) and recorded SENT. NEW: the lost sender is HELD as
        # uncertain — recovery sends nothing, the row is FAILED with the uncertainty mark and
        # listed as ACK_HELD. WHY: owner policy; the claimant may have died after Meta accepted
        # the message, so a resend is the duplicate P3R-01/02 measured. This crash happened
        # before the gate, so this acknowledgement is lost — the owner's accepted trade-off
        # (a rare missing acknowledgement, never a duplicate). INVARIANT kept: the booking
        # stands and an operator sees the row. PROOF: the old claim recovers with "SENT".
        with H.Meta() as m:
            with Session(self.engine) as db:
                key = db.execute(select(BookingCommandRecord.idempotency_key).where(
                    BookingCommandRecord.thread_id == w.thread_id)).scalar_one()
                status = recover_acknowledgement(db, key)
        f = self.f(w)
        self.assertEqual((status, f["ledger"][0][3], f["ledger"][0][5]), ("FAILED", "FAILED", True))
        self.assertEqual(self.owner_sends(m), [])
        self.assertIn("ACK_HELD", [x for r in self.attention_rows_before(w) for x in r])

    def _expire_lease(self, w):
        with Session(self.engine) as db:
            db.execute(update(BookingCommandRecord)
                       .where(BookingCommandRecord.thread_id == w.thread_id)
                       .values(ack_lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5)))
            db.commit()
        return True

    def _recover(self, w):
        with H.Meta() as m:
            with Session(self.engine) as db:
                key = db.execute(select(BookingCommandRecord.idempotency_key).where(
                    BookingCommandRecord.thread_id == w.thread_id)).scalar_one()
                status = recover_acknowledgement(db, key)
        self.assertLessEqual(len(self.owner_sends(m)), 1)
        return status

    def test_sb2b_08_crash_after_meta_success_before_ledger_completion(self):
        w = H.seed(self.engine)
        before, out, eng, m, ex = self._crash_then_replay(w, mock.patch.object(
            BookingAuthority, "record_acknowledgement", side_effect=Crash()))
        self.assertEqual(before["ledger"][0][3], "SENDING")
        self.assertEqual([o for o in before["outbound"] if o[1] == "sent"],
                         [("BOOKING_FLOW", "sent", H.OWNER_TEXT)])     # it DID reach Meta
        f = self.f(w)
        self.assertEqual((out.action, self.owner_sends(m)), ("booking_replayed", []))  # lease
        self.assertEqual(f["ledger"][0][3], "SENDING")
        self._expire_lease(w)
        reasons = [x for r in self.attention_rows_before(w) for x in r]
        self.assertIn("ACK_LEASE_GONE", reasons)
        # The reclaim is flagged duplicate-risk, and the outbound gate — the second line of
        # defence — refuses to send the same text to the same customer again: the customer
        # keeps exactly ONE acknowledgement and the row stays visible for a person.
        with H.Meta() as again:
            with Session(self.engine) as db:
                status = recover_acknowledgement(db, f["ledger"][0][0])
        f = self.f(w)
        self.assertEqual((status, again.sent), ("FAILED", []))
        self.assertTrue(f["ledger"][0][5])                          # duplicate risk recorded
        self.assertEqual([o for o in f["outbound"] if o[1] == "sent"],
                         [("BOOKING_FLOW", "sent", H.OWNER_TEXT)])
        reasons = [x for r in self.attention_rows_before(w) for x in r]
        self.assertIn("ACK_DUPLICATE_RISK", reasons)

    def test_sb2b_09_crash_after_ledger_completion_before_ce_sync(self):
        w = H.seed(self.engine)
        before, out, eng, m, ex = self._crash_then_replay(w, mock.patch.object(
            ConversationEngine, "_sb_synchronize", side_effect=Crash()))
        self.assertEqual((before["ledger"][0][3], before["cursor"]), ("SENT", None))
        # Convergence Phase 4 adaptation (SB2B-RV-04): as sb2b_06 — the operator e-mail was never
        # sent before the crash, and the operator surface now says so.
        self.assertEqual(before["attention"], ["CE_SYNC_INCOMPLETE",
                                               "OPERATOR_NOTIFICATION_PENDING"])
        self._assert_recovered(w, out, ex)
        self.assertEqual(self.owner_sends(m), [])                  # SENT ⇒ silent
        self.assertNotIn("CE_SYNC_INCOMPLETE",
                         [x for r in self.attention_rows_before(w) for x in r])

    def test_sb2b_10_crash_after_ce_sync_before_trace(self):
        w = H.seed(self.engine)
        with mock.patch.object(ConversationEngine, "_write_hybrid_trace", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.turn(w, cfg=H.settings(trace=True))
        f = self.f(w)
        self.assertEqual((f["cursor"], f["ledger"][0][3], f["occupying"]), (w.wamid, "SENT", 1))
        out, eng, m = self.replay(w)
        self.assertEqual((out.action, m.sent), ("skipped_dedup", []))   # fully processed

    def test_sb2b_11_trace_failure_never_costs_the_booking(self):
        w = H.seed(self.engine)
        with mock.patch("app.services.hybrid_trace.persist", side_effect=RuntimeError("db")):
            out, eng, m = self.turn(w, cfg=H.settings(trace=True))
        f = self.f(w)
        self.assertEqual((out.action, f["ledger"][0][3], f["occupying"], f["cursor"]),
                         ("booking_created", "SENT", 1, w.wamid))

    # ══ 4. cross-writer identity ════════════════════════════════════════════
    def _dx_then_ce(self, *, other_slot):
        w = H.seed(self.engine)
        with H.Meta() as dxm:
            resp = H.dx_confirm(self.engine, w, at=time(11, 0) if other_slot else None)
        self.assertEqual(resp["screen"], "SUCCESS")
        self.assertEqual(len(self.owner_sends(dxm)), 1)
        with _Spy("execute", BookingAuthority) as ex:
            out, eng, m = self.turn(w)
        f = self.f(w)
        self.assertEqual(out.action, "booking_replayed")
        self.assertTrue(eng._sb_trace_payload()["lookup_hit"])
        self.assertEqual(ex.calls, 0)
        self.assertEqual(([k for k, *_ in f["ledger"]], f["occupying"]),
                         ([flow_idempotency_key(w.token)], 1))
        self.assertEqual(f["ledger"][0][1], "WHATSAPP_FLOW")
        self.assertEqual(self.owner_sends(m), [])
        self.assertEqual(f["cursor"], w.wamid)
        self.assertEqual(f["revisions"][0][2], time(11, 0) if other_slot else w.at)
        return f

    def test_sb2b_12_dx_first_then_ce_same_slot(self):
        self._dx_then_ce(other_slot=False)

    def test_sb2b_13_dx_first_then_ce_different_slot(self):
        self._dx_then_ce(other_slot=True)

    def _ce_then_dx(self, *, other_slot):
        w = H.seed(self.engine)
        out, eng, m = self.turn(w)
        self.assertEqual(out.action, "booking_created")
        with H.Meta() as dxm:
            resp = H.dx_confirm(self.engine, w, at=time(11, 0) if other_slot else None)
        self.assertEqual(resp["screen"], "SUCCESS")
        self.assertEqual(dxm.sent, [])
        f = self.f(w)
        self.assertEqual(([k for k, *_ in f["ledger"]], f["occupying"], f["ledger"][0][3]),
                         ([flow_idempotency_key(w.token)], 1, "SENT"))
        self.assertEqual(f["revisions"][0][2], w.at)

    def test_sb2b_14_ce_first_then_dx_same_slot(self):
        self._ce_then_dx(other_slot=False)

    def test_sb2b_15_ce_first_then_dx_different_slot(self):
        self._ce_then_dx(other_slot=True)

    def test_sb2b_16_key_reused_from_execute_resolves_through_lookup(self):
        # The Data Exchange endpoint commits AFTER CE's entry lookup and BEFORE CE's execute
        # (a real race window): execute answers IDEMPOTENCY_KEY_REUSED; CE must look up.
        w = H.seed(self.engine)
        real = adapter.book_from_flow_response

        def dx_wins_the_race(db, req, **k):
            with H.Meta():
                H.dx_confirm(self.engine, w, at=time(11, 0))
            return real(db, req, **k)
        with mock.patch.object(adapter, "book_from_flow_response", dx_wins_the_race):
            out, eng, m = self.turn(w)
        f = self.f(w)
        self.assertEqual(out.action, "booking_replayed")
        self.assertEqual(eng._sb_trace_payload()["outcome"], "IDEMPOTENT_REPLAY")
        self.assertEqual((len(f["ledger"]), f["occupying"]), (1, 1))
        self.assertEqual(self.owner_sends(m), [])

    # ══ 5. state change before replay ═══════════════════════════════════════
    def test_sb2b_17_state_edit_before_replay_never_rebooks_or_overwrites(self):
        w = H.seed(self.engine)
        with mock.patch.object(ConversationEngine, "_sb_acknowledge", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.turn(w)
        committed = self.f(w)
        with Session(self.engine) as db:                            # the fixture moves on
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
            st.preferred_time = "15:00"; st.last_stage = "SCHEDULING"
            db.commit()
        out, eng, m = self.replay(w)
        f = self.f(w)
        self.assertEqual(out.action, "booking_replayed")
        self.assertNotIn("IDEMPOTENCY_KEY_REUSED", json.dumps(eng._sb_trace_payload()))
        self.assertEqual((f["revisions"], f["thread_revisions"]),
                         (committed["revisions"], committed["thread_revisions"]))
        self.assertEqual(len(f["ledger"]), 1)
        self.assertEqual(f["cursor"], w.wamid)

    # ══ 6. acknowledgement lifecycle ════════════════════════════════════════
    def test_sb2b_18_kill_switch_records_failed_and_bounded_recovery_sends_once(self):
        w = H.seed(self.engine)
        with H.kill_switch():
            out, _eng = H.run_turn(self.engine, H.flow_event(w))
        f = self.f(w)
        self.assertEqual((out.action, out.detail), ("booking_created", "ack:FAILED"))
        self.assertEqual(f["ledger"][0][3:5], ("FAILED", 1))
        self.assertFalse(out.reply_produced)
        self.assertIn("ACK_FAILED", [x for r in self.attention_rows_before(w) for x in r])
        self.assertEqual(self._recover(w), "SENT")
        self.assertEqual(self._recover(w), "SENT")                 # SENT ⇒ silent
        self.assertEqual(self.f(w)["ledger"][0][3:5], ("SENT", 2))

    def test_sb2b_19_exhausted_failures_are_terminal_and_visible(self):
        w = H.seed(self.engine)
        with H.kill_switch():
            H.run_turn(self.engine, H.flow_event(w))
        with Session(self.engine) as db:
            db.execute(update(BookingCommandRecord).where(
                BookingCommandRecord.thread_id == w.thread_id).values(ack_attempts=5))
            db.commit()
        with H.Meta() as m:
            with Session(self.engine) as db:
                key = db.execute(select(BookingCommandRecord.idempotency_key)).scalar_one()
                self.assertEqual(recover_acknowledgement(db, key), "FAILED")
        self.assertEqual(m.sent, [])
        self.assertIn("ACK_FAILED", [x for r in self.attention_rows_before(w) for x in r])

    def test_sb2b_20_ambiguous_transport_is_recorded_and_visible(self):
        w = H.seed(self.engine)
        out, eng, m = self.turn(w, meta="timeout")
        f = self.f(w)
        self.assertEqual(f["ledger"][0][3], "FAILED")
        self.assertTrue(f["ledger"][0][5])                          # duplicate risk
        row = next(r for r in self.attention() if r["thread_id"] == w.thread_id)
        self.assertIn("ACK_AMBIGUOUS", row["reasons"])
        self.assertEqual(row["recovery"], "OPERATOR_REVIEW")

    def test_sb2b_21_owner_text_is_the_only_w1_acknowledgement(self):
        w = H.seed(self.engine)
        out, eng, m = self.turn(w)
        self.assertEqual([t for _w, t in m.sent], [H.OWNER_TEXT])
        # Convergence Phase 3 adaptation (CP1-07). OLD: `_FLOW_COMPLETION_ACK` (Track A's
        # copy of the same sentence) was NOT among the sends. NEW: it IS the owner text — one
        # object, `booking_acknowledgement.BOOKING_FLOW_RECEIPT_TEXT` — and it is sent exactly
        # once. WHY: the two literals were unified so no second producer can drift; the old
        # assertion could only pass while two copies existed. The invariant (one success text,
        # the owner's, never the date receipt, never a `flow-txn:` correlation) is kept below.
        from app.services.booking_acknowledgement import BOOKING_FLOW_RECEIPT_TEXT
        self.assertIs(ConversationEngine._FLOW_COMPLETION_ACK, BOOKING_FLOW_RECEIPT_TEXT)
        self.assertEqual([t for _w, t in m.sent].count(H.OWNER_TEXT), 1)
        from app.services.booking_flow_service import build_booking_receipt_message
        receipt = build_booking_receipt_message("Ana", "martes", "10:00")
        self.assertNotIn(receipt, [t for _w, t in m.sent])
        with Session(self.engine) as db:
            from app.models import WhatsAppMessage
            corr = db.execute(select(WhatsAppMessage.correlation_id).where(
                WhatsAppMessage.thread_id == w.thread_id,
                WhatsAppMessage.direction == "out")).scalars().all()
        self.assertFalse(any((c or "").startswith("flow-txn:") for c in corr))

    def test_sb2b_22_acknowledgement_only_after_the_authority_commit(self):
        w = H.seed(self.engine)
        seen = []
        real = H.Meta._send

        def observing(meta, *, to_wa_id, text):
            with Session(self.engine) as other:                      # outside CE's session
                seen.append(other.execute(select(BookingCommandRecord.ack_status).where(
                    BookingCommandRecord.thread_id == w.thread_id)).scalars().all())
            return real(meta, to_wa_id=to_wa_id, text=text)
        with mock.patch.object(H.Meta, "_send", observing):
            out, eng, m = self.turn(w)
        self.assertEqual(seen, [["SENDING"]])                        # committed + claimed

    # ══ 7. failure outcomes are never confirmations ═════════════════════════
    def _failure(self, w, *, expect_action, expect_outcome, manual, patches=()):
        stack = [p for p in patches]
        for p in stack:
            p.start()
        try:
            notified = []
            out, eng, m = self.turn(w, hook=lambda e: setattr(
                e, "_send_booking_notification", lambda *a, **k: notified.append(1)))
        finally:
            for p in stack:
                p.stop()
        f = self.f(w)
        trace = eng._sb_trace_payload()
        self.assertEqual((out.action, trace["outcome"]), (expect_action, expect_outcome))
        self.assertIn(out.action, HANDLED_ACTIONS)
        self.assertEqual(f["occupying"], 0)
        # Convergence Phase 3 adaptation. OLD: token None — SB2B cleared the transport
        # mirror by hand at handoff while the offer row stayed ISSUED. NEW: the mirror is
        # left exactly as the authority left it and AGREES with the offer row (ISSUED, this
        # token). WHY: the by-hand clear split the mirror from the offer authority (G3-7P-R2,
        # "the mirror never lies"); offer terminalisation at refusal is Phase 4 policy
        # (P2R-10). Still asserted: the handoff, the cursor, no booking, no owner text.
        self.assertEqual((f["needs_human"], f["last_stage"], f["token"], f["cursor"]),
                         (True, "HUMAN_REQUIRED", w.token, w.wamid))
        with Session(self.engine) as db:
            from app.models import WhatsAppBookingOffer
            self.assertEqual(db.execute(select(WhatsAppBookingOffer.status).where(
                WhatsAppBookingOffer.thread_id == w.thread_id)).scalars().all(), ["ISSUED"])
        self.assertEqual((f["lead_necesita_humano"], f["lead_estado"]), (True, "ATENCION_HUMANA"))
        self.assertEqual([t for _w, t in m.sent], [H.HANDOFF_TEXT])   # never the owner text
        self.assertEqual(notified, [])                                # no booking e-mail
        mans = [r for r in f["ledger"] if r[2] == "REQUEST_MANUAL_SCHEDULING"]
        self.assertEqual(len(mans), 1 if manual else 0)
        self.assertEqual([r for r in f["ledger"] if r[2] == "CREATE"], [])
        if manual:
            self.assertFalse(mans[0][7])                               # non-occupying
            self.assertEqual(f["current_revision_id"], f["thread_revisions"][0][0])
            self.assertEqual([s for _i, s in f["thread_revisions"]], ["provisional"])
        else:
            self.assertEqual((f["revisions"], f["thread_revisions"]), ([], []))
        return f, trace

    def test_sb2b_23_slot_conflict_falls_back_to_manual_scheduling(self):
        w = H.seed(self.engine)
        other = H.seed(self.engine, day=w.day, at=w.at)
        self.turn(other)                                              # takes the slot
        self._failure(w, expect_action="booking_refused_handoff", expect_outcome="SLOT_CONFLICT",
                      manual=True)

    def test_sb2b_24_calendar_exception_falls_back_to_manual_scheduling(self):
        w = H.seed(self.engine)
        with Session(self.engine) as db:
            create_calendar_exception(db, kind="CLOSED_DATE", exception_date=w.day, reason="fixture")
        self._failure(w, expect_action="booking_refused_handoff",
                      expect_outcome="CALENDAR_EXCEPTION", manual=True)

    def test_sb2b_25_missing_professional(self):
        w = H.seed(self.engine)
        with Session(self.engine) as db:
            db.execute(update(Profesional).values(is_active=False)); db.commit()
        try:
            self._failure(w, expect_action="booking_refused_handoff",
                          expect_outcome="MISSING_PROFESSIONAL", manual=False)
        finally:
            with Session(self.engine) as db:
                db.execute(update(Profesional).values(is_active=True)); db.commit()

    def test_sb2b_26_validation_failed(self):
        # a customer-typed seller type longer than `revisions.vendedor_tipo` (String(20))
        w = H.seed(self.engine)
        ev = H.flow_event(w, data=H.flow_payload(w.token, nombre_apellido="Ana",
                                                 tipo_vendedor="x" * 40))
        with H.Meta() as m:
            out, eng = H.run_turn(self.engine, ev)
        f = self.f(w)
        self.assertEqual((out.action, eng._sb_trace_payload()["outcome"]),
                         ("booking_refused_handoff", "VALIDATION_FAILED"))
        self.assertEqual((f["ledger"], f["occupying"], [t for _w, t in m.sent]),
                         ([], 0, [H.HANDOFF_TEXT]))

    def test_sb2b_27_retryable_failure_is_never_confirmed(self):
        from sqlalchemy.exc import OperationalError

        class _Orig(Exception):
            sqlstate = "55P03"
        w = H.seed(self.engine)
        boom = OperationalError("SELECT 1", {}, _Orig("lock timeout"))
        # Convergence Phase 3 adaptation (CD-04). OLD: `booking_retry_required` committed a
        # TERMINAL handoff (cursor, needs_human, lead ATENCION_HUMANA, handoff text) for a
        # transient lock wait. NEW: Track A's contention contract — `thread_contended`,
        # reply_required, NOTHING written (no cursor, no needs_human, no offer change, no
        # ledger), nothing sent — and a redelivery after the holder releases books once.
        # WHY: the ratified design (§12 stage 12f) makes transient contention non-terminal.
        # Under the old mapping the cursor/needs_human assertions below fail.
        with mock.patch.object(BookingAuthority, "_check_calendar", side_effect=boom):
            out, eng, m = self.turn(w)
        f = self.f(w)
        trace = eng._sb_trace_payload()
        self.assertEqual((out.action, trace["outcome"], trace["reason"]),
                         ("thread_contended", "RETRYABLE_FAILURE", "LOCK_TIMEOUT"))
        self.assertIn(out.action, HANDLED_ACTIONS)
        self.assertTrue(out.reply_required)
        self.assertEqual((f["cursor"], f["needs_human"], f["token"], f["lead_estado"]),
                         (None, False, w.token, "CONSULTA_NUEVA"))
        self.assertEqual((f["ledger"], f["revisions"], f["thread_revisions"], m.sent),
                         ([], [], [], []))
        out2, eng2, m2 = self.turn(w)                       # redelivery after release
        f2 = self.f(w)
        self.assertEqual((out2.action, f2["occupying"], len(f2["ledger"])),
                         ("booking_created", 1, 1))
        self.assertEqual([t for _w, t in m2.sent], [H.OWNER_TEXT])

    def test_sb2b_28_internal_failure(self):
        w = H.seed(self.engine)
        self._failure(w, expect_action="booking_refused_handoff", expect_outcome="INTERNAL_FAILURE",
                      manual=False, patches=[mock.patch.object(
                          BookingAuthority, "_write", side_effect=RuntimeError("fixture"))])

    def test_sb2b_29_token_not_active_books_nothing(self):
        w = H.seed(self.engine)
        with Session(self.engine) as db:
            # Convergence Phase 3 adaptation: the offer is withdrawn THROUGH the offer
            # authority (which also clears the mirror), the only way production withdraws
            # one; SB2B's by-hand mirror clear is gone (see `_failure`). Same refusal.
            from app.services.offer_lifecycle import OfferLifecycle, WithdrawReason
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
            OfferLifecycle(db).withdraw(st, thread_id=w.thread_id,
                                        reason=WithdrawReason.HUMAN_HANDOFF)
            db.commit()
        with _Spy("app.services.booking_ce_adapter.book_from_flow_response") as spy:
            out, eng, m = self.turn(w)
        self.assertEqual((out.action, out.detail, spy.calls),
                         ("booking_refused_handoff", "FLOW_TOKEN_NOT_ACTIVE", 0))
        self.assertEqual(self.f(w)["occupying"], 0)

    # ══ 9. identity conflict ════════════════════════════════════════════════
    def test_sb2b_30_identity_conflict_is_persisted_preserved_and_visible(self):
        w = H.seed(self.engine, lead_nombre="Beatriz")
        out, eng, m = self.turn(w)
        f = self.f(w)
        self.assertEqual(out.action, "booking_created")            # booking stands (R4 policy)
        self.assertEqual((f["ledger"][0][6], f["lead_nombre"]), ("CONFLICT", "Beatriz"))
        self.assertEqual(eng._sb_trace_payload()["identity_outcome"], "CONFLICT")
        row = next(r for r in self.attention() if r["thread_id"] == w.thread_id)
        self.assertIn("IDENTITY_CONFLICT", row["reasons"])
        self.assertEqual(row["recovery"], "OPERATOR_REVIEW")

    # ══ 10. metadata ownership ══════════════════════════════════════════════
    FORBIDDEN = ("created_at", "appointment_approval_status", "appointment_approval_token",
                 "appointment_approval_sent_at", "appointment_approved_at", "id", "lead_id",
                 "booking_version", "turno_fecha", "turno_hora", "estado_revision",
                 "profesional_id", "service_minutes", "buffer_minutes", "turno_timezone",
                 "agenda_occupying", "occupied_during")

    def test_sb2b_31_ce_cannot_write_metadata_or_approval_state(self):
        from sqlalchemy import event as sa_event
        w = H.seed(self.engine)
        values = {"created_at": datetime(2026, 1, 1), "appointment_approved_at": datetime(2026, 1, 1),
                  "appointment_approval_sent_at": datetime(2026, 1, 1)}
        stmts = []
        hook = lambda *a: stmts.append(a[2])                       # noqa: E731
        sa_event.listen(self.engine, "before_cursor_execute", hook)
        try:
            for name in self.FORBIDDEN:
                v = values.get(name, "PENDING" if "status" in name else 1 if "id" in name else "x")
                with Session(self.engine) as db:
                    r1 = adapter.book_from_flow_response(db, adapter.CeFlowBookingRequest(
                        thread_id=w.thread_id, lead_id=w.lead_id, inbound_event_id=w.wamid,
                        flow_token=w.token, requested_date=w.day, requested_time=w.at,
                        revision_fields={name: v},
                        availability=adapter.AvailabilityRevalidation()))
                    r2 = adapter.request_manual_scheduling(db, adapter.CeSchedulingEscalationRequest(
                        thread_id=w.thread_id, lead_id=w.lead_id, cycle_key=1,
                        revision_fields={name: v}))
                for r in (r1, r2):
                    self.assertEqual((r.outcome, r.reason),
                                     (Out.VALIDATION_FAILED, f"CE_FIELD_NOT_ALLOWED:{name}"), name)
        finally:
            sa_event.remove(self.engine, "before_cursor_execute", hook)
        self.assertEqual(stmts, [])                                 # refused before any SQL
        self.assertEqual(self.f(w)["ledger"], [])

    def test_sb2b_32_ce_w1_supplies_only_allow_listed_fields(self):
        w = H.seed(self.engine)
        seen = []
        real = adapter.book_from_flow_response

        def spy(db, req, **k):
            seen.append(dict(req.revision_fields)); return real(db, req, **k)
        with mock.patch.object(adapter, "book_from_flow_response", spy):
            self.turn(w)
        self.assertTrue(seen and set(seen[0]) <= adapter.CE_REVISION_FIELDS)

    # ══ 8. CE-W2 (structure; lock behaviour is proven on PostgreSQL) ═══════
    def _w2(self, w, *, flush_first=True, hook=None):
        db = H.session_factory(self.engine)()
        order = []
        try:
            eng = ConversationEngine(db=db, settings=H.settings())
            eng._sb_trace = None
            ctx = eng._load_context(w.thread_id)
            ctx.inbound_wa_message_id = w.wamid + ".w2"
            state = ctx.state
            state.active_requested_date = w.day.isoformat()
            state.last_requested_time = "19:45"
            if flush_first:                     # CE's text path flushed before reaching W2
                ctx.lead.flag = "ACEPTADO"
                state.last_intent = "schedule"
                db.flush()

            def email(**k):
                with Session(self.engine) as other:
                    order.append(("email", other.execute(select(BookingCommandRecord.operation).where(
                        BookingCommandRecord.thread_id == w.thread_id)).scalars().all(),
                        other.execute(select(WhatsAppThreadState.needs_human).where(
                            WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()))
            eng._send_scheduling_handoff_email = email
            if hook:
                hook(eng)
            with H.Meta() as m:
                out = eng._handle_scheduling_escalation(ctx, state, "quiero ese horario")
            return out, eng, m, order
        finally:
            db.close()

    def test_sb2b_33_ce_w2_records_a_non_occupying_request_and_keeps_the_handoff(self):
        w = H.seed(self.engine)
        out, eng, m, order = self._w2(w)
        f = self.f(w)
        self.assertEqual(out.action, "replied")
        self.assertEqual([(r[2], r[7]) for r in f["ledger"]], [("REQUEST_MANUAL_SCHEDULING", False)])
        self.assertEqual(f["occupying"], 0)
        self.assertEqual([s for _i, s in f["thread_revisions"]], ["provisional"])
        self.assertEqual((f["needs_human"], f["current_revision_id"], f["token"]),
                         (True, f["thread_revisions"][0][0], None))
        self.assertEqual(f["revisions"][0][3], "REPROGRAMAR")
        # B-8: the e-mail ran after the authority committed AND after CE committed the handoff
        self.assertEqual(order, [("email", ["REQUEST_MANUAL_SCHEDULING"], True)])
        self.assertEqual(eng._sb_trace_payload()["writer"], "CE_W2")

    def test_sb2b_34_ce_w2_plan_is_booking_owned_only(self):
        from app.services.booking_authority import (BookingAttachmentPlan, LeadUpdate,
                                                    ThreadRevisionAttachment, ThreadStateUpdate)
        w = H.seed(self.engine)
        for plan in (BookingAttachmentPlan(lead=LeadUpdate(necesita_humano=True)),
                     BookingAttachmentPlan(thread_state=ThreadStateUpdate(needs_human=True)),
                     BookingAttachmentPlan(thread_revision=ThreadRevisionAttachment(
                         thread_id=w.thread_id, request_approval=True))):
            with Session(self.engine) as db:
                r = adapter.request_manual_scheduling(db, adapter.CeSchedulingEscalationRequest(
                    thread_id=w.thread_id, lead_id=w.lead_id, cycle_key=3, attachments=plan))
            self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, "CE_W2_PLAN_NOT_BOOKING_OWNED"))
        self.assertEqual(self.f(w)["ledger"], [])

    def test_sb2b_35_ce_w2_handoff_survives_an_authority_refusal(self):
        w = H.seed(self.engine)
        with mock.patch.object(BookingAuthority, "_write", side_effect=RuntimeError("fixture")):
            out, eng, m, order = self._w2(w)
        f = self.f(w)
        self.assertEqual((f["ledger"], f["needs_human"], f["token"]), ([], True, None))
        self.assertEqual(eng._sb_trace_payload()["outcome"], "INTERNAL_FAILURE")
        self.assertEqual(len(order), 1)

    # ══ 4b. B-2: a committed booking is never swallowed by needs_human ══════
    def test_sb2b_36_needs_human_never_swallows_a_committed_booking(self):
        w = H.seed(self.engine)
        with H.Meta():
            H.dx_confirm(self.engine, w)                            # sets needs_human=True
        self.assertTrue(self.f(w)["needs_human"])
        out, eng, m = self.turn(w)
        self.assertEqual(out.action, "booking_replayed")
        self.assertNotEqual(out.action, "skipped_human")

    def test_sb2b_37_needs_human_without_a_booking_still_belongs_to_the_human(self):
        w = H.seed(self.engine, needs_human=True)
        with _Spy("app.services.booking_ce_adapter.book_from_flow_response") as spy:
            out, eng, m = self.turn(w)
        self.assertEqual((out.action, spy.calls, m.sent), ("skipped_human", 0, []))

    # ══ 12. HANDLED_ACTIONS ═════════════════════════════════════════════════
    def test_sb2b_38_every_booking_action_is_handled(self):
        for a in NEW_ACTIONS + ("booking_created",):
            self.assertIn(a, HANDLED_ACTIONS, a)
            self.assertTrue(ce_mod._out(a).handled, a)
        src = inspect.getsource(ce_mod.ConversationEngine)
        produced = {getattr(ConversationEngine, n) for n in dir(ConversationEngine)
                    if n.startswith("SB_ACTION_")}
        self.assertEqual(produced, set(NEW_ACTIONS) | {"booking_created"})
        self.assertIn("SB_ACTION_", src)

    # ══ 19. trace truth ═════════════════════════════════════════════════════
    def test_sb2b_39_trace_carries_the_booking_block_and_never_the_token(self):
        w = H.seed(self.engine)
        out, eng, m = self.turn(w, cfg=H.settings(trace=True))
        with Session(self.engine) as db:
            row = db.execute(select(HybridDecisionTraceRow).where(
                HybridDecisionTraceRow.thread_id == w.thread_id)).scalar_one()
        p = row.payload
        self.assertEqual(p["trace_version"], "hybrid-decision-trace/1.10")
        b = p["booking"]
        key = flow_idempotency_key(w.token)
        self.assertEqual((b["key_prefix"], b["key_hash_prefix"]), ("flow:", key[5:17]))
        self.assertEqual((b["outcome"], b["ack_status_before"], b["ack_status_after"],
                          b["ce_synchronized"], b["availability_revalidated"]),
                         ("COMMITTED", "PENDING", "SENT", True, True))
        dump = json.dumps(p)
        self.assertNotIn(w.token, dump)
        self.assertNotIn(key, dump)                                 # only a prefix
        from app.ui.hybrid_trace_view import render_turn_trace_page
        html = render_turn_trace_page({"captured": True, "trace": p, "turn_id": "t"}, "t")
        self.assertIn("Reserva — autoridad", html)
        self.assertNotIn(w.token, html)

    # ══ 20. dashboard ═══════════════════════════════════════════════════════
    def test_sb2b_40_dashboard_rows_carry_no_token_wamid_or_customer_prose(self):
        w = H.seed(self.engine, lead_nombre="Beatriz")
        out, eng, m = self.turn(w, meta="timeout")
        rows = [r for r in self.attention() if r["thread_id"] == w.thread_id]
        self.assertEqual(len(rows), 1)
        dump = json.dumps(rows)
        for secret in (w.token, w.wamid, flow_idempotency_key(w.token), "Beatriz", "Ana",
                       w.wa_id, "example.invalid"):
            self.assertNotIn(secret, dump)
        self.assertEqual((rows[0]["key_kind"], rows[0]["thread_url"]),
                         ("flow", f"/whatsapp/thread/{w.thread_id}"))


class SQLiteOptionB(OptionBCases, unittest.TestCase):
    def setUp(self):
        self.engine = H.sqlite_engine()            # one disposable database per case


# ══ flag OFF: legacy CE-W1 / CE-W2 untouched ═══════════════════════════════

class FlagOff(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()

    # Convergence Phase 3 adaptation of sb2b_41 / 42 / 44. OLD: with the flag off CE-W1 and
    # CE-W2 WERE Track A's legacy in-session writers (a booked Revision, the legacy copy; a
    # provisional Revision written outside the authority). NEW: the flag is an emergency
    # OFF switch — no automatic booking writer is reachable, a booking-shaped submission is a
    # truthful handoff, and a booking that already exists is still answered from the ledger.
    # WHY: Phase 3 deletes the legacy writers outright (design §16, owner ruling: the flag
    # never restores them). Each assertion below fails against the parent's legacy writer.
    def test_sb2b_41_flag_off_w1_is_a_truthful_handoff_never_a_writer(self):
        w = H.seed(self.engine)
        with _Spy("app.services.booking_ce_adapter.book_from_flow_response") as spy, \
                _Spy("execute", BookingAuthority) as ex:
            with H.Meta() as m:
                out, eng = H.run_turn(self.engine, H.flow_event(w), cfg=H.settings(flag=False))
        f = H.facts(self.engine, w)
        self.assertEqual((out.action, out.detail, spy.calls, ex.calls, f["ledger"]),
                         ("booking_refused_handoff", "SMART_BOOKING_DISABLED", 0, 0, []))
        self.assertEqual((f["revisions"], f["thread_revisions"], f["occupying"]), ([], [], 0))
        self.assertEqual((f["cursor"], f["needs_human"], f["token"]), (w.wamid, True, w.token))
        self.assertEqual([t for _w, t in m.sent], [H.HANDOFF_TEXT])       # never a success
        self.assertNotIn(H.OWNER_TEXT, [t for _w, t in m.sent])

    def test_sb2b_42_flag_off_a_committed_booking_is_replayed_not_swallowed(self):
        w = H.seed(self.engine)
        with H.Meta():
            H.dx_confirm(self.engine, w)                    # booked while the flag was ON
        with _Spy("execute", BookingAuthority) as ex, H.Meta() as m:
            out, _ = H.run_turn(self.engine, H.flow_event(w), cfg=H.settings(flag=False))
        f = H.facts(self.engine, w)
        # the ledger read is not a writer: it answers truthfully under either flag
        self.assertEqual((out.action, ex.calls, m.sent), ("booking_replayed", 0, []))
        self.assertEqual((f["occupying"], len(f["ledger"]), f["cursor"]), (1, 1, w.wamid))

    def test_sb2b_43_flag_is_read_by_identity_not_truthiness(self):
        eng = ConversationEngine.__new__(ConversationEngine)
        eng.settings = mock.MagicMock()                     # a MagicMock attribute is truthy
        self.assertFalse(eng._smart_booking_ce_on())
        eng.settings = H.settings(flag=True)
        self.assertTrue(eng._smart_booking_ce_on())
        self.assertFalse(H.get_settings().smart_booking_ce_enabled)        # default OFF

    def test_sb2b_44_flag_off_w2_writes_no_revision(self):
        w = H.seed(self.engine)
        db = H.session_factory(self.engine)()
        order = []
        try:
            eng = ConversationEngine(db=db, settings=H.settings(flag=False))
            ctx = eng._load_context(w.thread_id)
            ctx.inbound_wa_message_id = "w2.legacy"

            def email(**k):
                with Session(self.engine) as other:          # committed before the e-mail?
                    order.append(other.execute(select(WhatsAppThreadState.needs_human).where(
                        WhatsAppThreadState.thread_id == w.thread_id)).scalar_one())
            eng._send_scheduling_handoff_email = email
            with _Spy("app.services.booking_ce_adapter.request_manual_scheduling") as spy, \
                    H.Meta() as m:
                out = eng._handle_scheduling_escalation(ctx, ctx.state, "x")
        finally:
            db.close()
        f = H.facts(self.engine, w)
        self.assertEqual((spy.calls, f["ledger"], f["thread_revisions"], f["revisions"]),
                         (0, [], [], []))
        self.assertEqual((out.action, f["needs_human"], order), ("replied", True, [True]))
        self.assertEqual(len(m.sent), 1)


class Endpoint(unittest.TestCase):

    def test_sb2b_45_booking_attention_requires_the_crm_session(self):
        from fastapi import HTTPException
        from starlette.requests import Request
        from app.routes.ops_dashboard import require_crm_session
        req = Request({"type": "http", "method": "GET", "path": "/api/ops/booking-attention",
                       "headers": [], "query_string": b""})
        with self.assertRaises(HTTPException) as cm:
            require_crm_session(req)
        self.assertEqual(cm.exception.status_code, 401)
        from app.routes import ops_dashboard
        route = next(r for r in ops_dashboard.router.routes
                     if getattr(r, "path", "") == "/api/ops/booking-attention")
        deps = [d.call for d in route.dependant.dependencies]
        self.assertIn(require_crm_session, deps)

    def test_sb2b_46_control_page_renders_the_attention_panel(self):
        from app.ui.control_view import render_control_page
        html = render_control_page("operator@example.invalid")
        self.assertIn('id="panel-bookings"', html)
        self.assertIn("/api/ops/booking-attention", html)
        self.assertIn("fetchBookings();", html)

    def test_sb2b_47_older_traces_still_render(self):
        from app.ui.hybrid_trace_view import render_turn_trace_page
        for version in ("hybrid-decision-trace/1.9", "hybrid-decision-trace/1.3"):
            html = render_turn_trace_page({"captured": True, "turn_id": "t", "trace": {
                "trace_version": version, "turn_id": "t"}}, "t")
            self.assertNotIn("Reserva — autoridad", html, version)
        html = render_turn_trace_page({"captured": True, "turn_id": "t", "trace": {
            "trace_version": "hybrid-decision-trace/1.10", "turn_id": "t", "booking": None}}, "t")
        self.assertIn("no llamó a la autoridad de reservas", html)


if __name__ == "__main__":
    unittest.main()
