"""Convergence Phase 2 — the authority foundation, default (SQLite) gate.

What runs here needs no PostgreSQL: the typed `OfferAcceptance` contract and its pure
validation, the unified bound, the K-order shape of `_execute_snapshot`, the K1 exemption for
CE-W2, the detached ledger end to end through the real tester reset, and the flag-off
neutrality guard — Track A's own Conversation Engine and Data Exchange booking tests, run under
a spy that fails if any Smart Booking authority entry point is reached.

Concurrency, lock order and the partial-unique race are NOT claimed here: SQLite serializes
writers. Those are proved in `test_convergence_p2_authority_pg.py`.
"""
from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import os
import sys
import unittest
from datetime import date, time
from pathlib import Path
from unittest import mock

_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
_TESTS = str(Path(__file__).resolve().parent)
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.models as M  # noqa: E402
from app.services import booking_authority as ba  # noqa: E402
from app.services import booking_ce_adapter as ce  # noqa: E402
from app.services.booking_authority import (BookingAttachmentPlan, BookingAuthority,  # noqa: E402
                                            BookingCommand, BookingOperation as Op,
                                            BookingOutcome as Out, BookingSource as Src,
                                            OfferAcceptance, ThreadRevisionAttachment,
                                            ThreadStateUpdate)

D = date(2027, 3, 2)
WA = "549110000931"


def _fp(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _db() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    # the models' own metadata: another test module may reload `app.db`, after which its
    # `Base` is no longer the registry these model classes were declared on
    M.Lead.__table__.metadata.create_all(engine)
    return Session(engine)


def _world(db, wa=WA, *, with_state=True):
    db.add(M.Profesional(nombre="Fixture", apellido="P2-FIXTURE", email="fixture@example.invalid",
                         resource_key=f"fixture.p2.{wa}", is_launch_default=not db.query(
                             M.Profesional).filter_by(is_launch_default=True).count(),
                         is_active=True))
    ct = M.WhatsAppContact(wa_id=wa, display_name="P2"); db.add(ct); db.flush()
    lead = M.Lead(nombre="Probe", estado="CONSULTA_NUEVA"); db.add(lead); db.flush()
    th = M.WhatsAppThread(contact_id=ct.id, lead_id=lead.id); db.add(th); db.flush()
    if with_state:
        db.add(M.WhatsAppThreadState(thread_id=th.id, home_zone_group="CABA",
                                     home_zone_detail="Palermo"))
    cand = M.WhatsAppThreadCandidate(thread_id=th.id, tipo_vehiculo="AUTO", status="current_focus")
    db.add(cand); db.commit()
    return lead.id, th.id, cand.id


def _acc(tid, cid, token="tok", **kw):
    base = dict(token_fingerprint=_fp(token), thread_id=tid, cycle_key=0, candidate_id=cid,
                tipo_vehiculo="AUTO", zone_group="CABA", zone_detail="Palermo")
    base.update(kw)
    return OfferAcceptance(**base)


def _cmd(lead_id, tid, token="tok", **kw):
    base = dict(operation=Op.CREATE, source=Src.CONVERSATION_ENGINE,
                idempotency_key=ba.flow_idempotency_key(token), lead_id=lead_id, thread_id=tid,
                requested_date=D, requested_time=time(10))
    base.update(kw)
    return BookingCommand(**base)


def _plan(tid, cid, acc=None, **tr):
    return BookingAttachmentPlan(
        thread_revision=ThreadRevisionAttachment(thread_id=tid, candidate_id=cid, **tr),
        thread_state=ThreadStateUpdate(link_current_revision=True), offer_acceptance=acc)


def _counts(db):
    db.expire_all()
    return (db.query(M.Revision).count(), db.query(M.ThreadRevision).count(),
            db.query(M.BookingCommandRecord).count())


# ══ 1. the typed OfferAcceptance ═══════════════════════════════════════════════

class P2U_OfferAcceptanceType(unittest.TestCase):

    def test_p2u_01_the_attachment_is_frozen_data_with_exactly_the_bindings(self):
        names = [f.name for f in dataclasses.fields(OfferAcceptance)]
        self.assertEqual(names, ["token_fingerprint", "thread_id", "cycle_key", "candidate_id",
                                 "tipo_vehiculo", "zone_group", "zone_detail"])
        self.assertTrue(OfferAcceptance.__dataclass_params__.frozen)
        a = _acc(1, 2)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            a.thread_id = 3
        # declared kinds only — no callable, session, mapping or prose field exists
        kinds = {k for k, _n, _e in ba._PLAN_SCHEMA["OfferAcceptance"].values()}
        self.assertEqual(kinds, {"str", "int"})
        self.assertEqual(ba._PLAN_SCHEMA["BookingAttachmentPlan"]["offer_acceptance"],
                         ("plan", True, "OfferAcceptance"))
        self.assertNotIn("offered_date", names)            # metadata, never a binding

    def test_p2u_02_the_acceptance_is_captured_as_a_new_object(self):
        acc = _acc(1, 2)
        plan = _plan(1, 2, acc)
        call = ba._capture_call(_cmd(1, 1), None, plan)
        got = call.attachments.offer_acceptance
        self.assertIsNot(got, acc)
        self.assertEqual(got, acc)

    def test_p2u_03_a_non_data_acceptance_is_refused_before_any_database_access(self):
        db = _db(); lead, tid, cid = _world(db)

        class Sub(OfferAcceptance):
            pass
        cases = [
            (Sub(**dataclasses.asdict(_acc(tid, cid))), "PLAN_NOT_DATA:attachments"),
            (_acc(tid, cid, thread_id=True), "PLAN_NOT_DATA:attachments"),         # bool, not int
            (_acc(tid, cid, tipo_vehiculo=lambda: "AUTO"), "PLAN_NOT_DATA:attachments"),
            (_acc(tid, cid, token_fingerprint="Z" * 64), "OFFER_ACCEPTANCE_FINGERPRINT_NOT_SHA256"),
            (_acc(tid, cid, token_fingerprint=_fp("tok")[:63]), "OFFER_ACCEPTANCE_FINGERPRINT_NOT_SHA256"),
        ]
        for acc, reason in cases:
            with mock.patch.object(BookingAuthority, "_lock_thread_state",
                                   side_effect=AssertionError("reached K1")):
                r = BookingAuthority(db).execute(_cmd(lead, tid), attachments=_plan(tid, cid, acc))
            self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, reason))
        self.assertEqual(_counts(db), (0, 0, 0))

    def test_p2u_04_the_acceptance_binds_one_thread_bound_create_under_its_own_key(self):
        db = _db(); lead, tid, cid = _world(db)
        other = M.WhatsAppThreadCandidate(thread_id=tid, tipo_vehiculo="AUTO"); db.add(other)
        db.commit()
        cases = [
            (_cmd(lead, tid, operation=Op.REQUEST_MANUAL_SCHEDULING), _plan(tid, cid, _acc(tid, cid)),
             "OFFER_ACCEPTANCE_ONLY_FOR_CREATE"),
            (_cmd(lead, tid, thread_id=None), _plan(tid, cid, _acc(tid, cid)),
             "OFFER_ACCEPTANCE_THREAD_MISMATCH"),
            (_cmd(lead, tid), BookingAttachmentPlan(offer_acceptance=_acc(tid, cid)),
             "OFFER_ACCEPTANCE_REQUIRES_THREAD_REVISION"),
            (_cmd(lead, tid), _plan(tid, other.id, _acc(tid, cid)),
             "OFFER_ACCEPTANCE_CANDIDATE_MISMATCH"),
            (_cmd(lead, tid, idempotency_key="api:not-the-offer-key"),
             _plan(tid, cid, _acc(tid, cid)), "OFFER_ACCEPTANCE_KEY_MISMATCH"),
        ]
        for cmd, plan, reason in cases:
            r = BookingAuthority(db).execute(cmd, attachments=plan)
            self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, reason))
        self.assertEqual(_counts(db), (0, 0, 0))
        # the key derivation is exactly the Flow key: one token, one ledger row
        self.assertEqual(ba.flow_idempotency_key_for_fingerprint(_fp("abc")),
                         ba.flow_idempotency_key("abc"))


# ══ 2. bound, order, K1 scope ═════════════════════════════════════════════════

class P2U_BoundAndOrder(unittest.TestCase):

    def test_p2u_10_one_four_second_bound_everywhere(self):
        from app.services.offer_lifecycle import DEFAULT_OFFER_LOCK_TIMEOUT_MS
        self.assertEqual(ba.DEFAULT_LOCK_TIMEOUT_MS, 4000)
        self.assertEqual(ba.DEFAULT_LOCK_TIMEOUT_MS, DEFAULT_OFFER_LOCK_TIMEOUT_MS)
        self.assertEqual(BookingAuthority(None).lock_timeout_ms, 4000)
        for fn in (ce.book_from_flow_response, ce.request_manual_scheduling):
            self.assertEqual(inspect.signature(fn).parameters["lock_timeout_ms"].default, 4000)

    def test_p2u_11_the_snapshot_acquires_in_k_order(self):
        src = inspect.getsource(BookingAuthority._execute_snapshot)
        marks = ["self._lock_thread_state(", "self._claim_offer(", "self._acquire_locks(",
                 "self._lock_revision(", "self._lock_overlapping(", "self._write(",
                 "self._apply_attachments(", "self._sync_thread_revision(",
                 "self._apply_canonical_state(", "self._record(", "self.db.commit()"]
        at = [src.index(m) for m in marks]
        self.assertEqual(at, sorted(at), dict(zip(marks, at)))
        # the bound is set before K1, the unlocked ledger read before the bound
        self.assertLess(src.index("SET LOCAL lock_timeout"), src.index("self._lock_thread_state("))
        self.assertLess(src.index("self._check_replay("), src.index("SET LOCAL lock_timeout"))
        # K1 is an explicit row lock, never left to a flush
        k1 = inspect.getsource(BookingAuthority._lock_thread_state)
        self.assertIn("with_for_update(key_share=True)", k1)
        canon = inspect.getsource(BookingAuthority._apply_canonical_state)
        self.assertNotIn("select(", canon.split("from ..models")[1].split("if plan is not None and plan.thread_state")[1])

    def test_p2u_12_k1_creates_a_missing_state_row_but_ce_w2_takes_no_k1(self):
        db = _db(); lead, tid, cid = _world(db, with_state=False)
        r = ce.request_manual_scheduling(db, ce.CeSchedulingEscalationRequest(
            thread_id=tid, lead_id=lead, escalation_id="p2u-12", requested_date=D,
            requested_time=time(10)))
        self.assertEqual(r.outcome, Out.COMMITTED, r.reason)
        db.expire_all()
        self.assertEqual(db.query(M.WhatsAppThreadState).filter_by(thread_id=tid).count(), 0,
                         "CE-W2 (B-3) must not touch the thread-state row")
        b = BookingAuthority(db).execute(_cmd(lead, tid, idempotency_key="p2u-12b"),
                                         attachments=_plan(tid, cid))
        self.assertEqual(b.outcome, Out.COMMITTED, b.reason)
        db.expire_all()
        self.assertEqual(db.query(M.WhatsAppThreadState).filter_by(thread_id=tid).one()
                         .current_revision_id, b.thread_revision_id)

    def test_p2u_13_new_outcomes_are_typed_terminal_and_never_ok(self):
        for out in (Out.OFFER_REFUSED, Out.COMMAND_DETACHED):
            r = ba.refuse(out, "X")
            self.assertFalse(r.ok)
            self.assertFalse(r.retryable)
            self.assertTrue(ba._MESSAGES[out])


# ══ 3. the detached ledger, through the real tester reset ═════════════════════

class P2U_DetachedLedger(unittest.TestCase):

    def _booked(self):
        db = _db(); lead, tid, cid = _world(db)
        r = BookingAuthority(db).execute(_cmd(lead, tid, idempotency_key="p2u-key",
                                              tracks_acknowledgement=True),
                                         attachments=_plan(tid, cid))
        self.assertEqual(r.outcome, Out.COMMITTED, r.reason)
        return db, lead, tid, cid, r

    def test_p2u_20_reset_detaches_and_lookup_is_terminal(self):
        from app.services.tester_reset import reset_tester_to_zero_state
        db, lead, tid, cid, r = self._booked()
        self.assertEqual(BookingAuthority(db).lookup("p2u-key").outcome, Out.IDEMPOTENT_REPLAY)
        res = reset_tester_to_zero_state(db, WA)
        self.assertEqual(res.detached_commands, 1)
        db.expire_all()
        rec = db.query(M.BookingCommandRecord).one()
        self.assertEqual((rec.command_state, rec.detached_reason), ("DETACHED", "TESTER_RESET"))
        self.assertEqual((rec.revision_id, rec.thread_revision_id, rec.lead_id, rec.thread_id,
                          rec.profesional_id, rec.slot_start, rec.slot_end, rec.occupying),
                         (None,) * 7 + (False,))
        self.assertTrue(json.loads(rec.detached_history)["historical"])
        look = BookingAuthority(db).lookup("p2u-key")
        self.assertEqual((look.outcome, look.reason),
                         (Out.COMMAND_DETACHED, "LEDGER_DETACHED:TESTER_RESET"))
        self.assertFalse(look.ok)
        self.assertIsNone(ce.lookup_booking(db, "p2u-key"))          # CE: not a hit
        self.assertIsNone(BookingAuthority(db).claim_acknowledgement("p2u-key"))
        again = BookingAuthority(db).execute(_cmd(lead, tid, idempotency_key="p2u-key"))
        self.assertEqual(again.outcome, Out.COMMAND_DETACHED)
        self.assertEqual(_counts(db), (0, 0, 1))

    def test_p2u_21_the_reset_detaches_before_it_deletes(self):
        import ast
        from app.services import tester_reset as T
        fn = next(n for n in ast.walk(ast.parse(inspect.getsource(T)))
                  if isinstance(n, ast.FunctionDef) and n.name == "reset_tester_to_zero_state")
        src = ast.unparse(fn)
        self.assertLess(src.index("_detach_ledger("), src.index("_delete(AiEvent"))
        self.assertIn("thread_id.in_(thread_ids)", inspect.getsource(T._detach_ledger))


# ══ 4. flag-off neutrality guard ══════════════════════════════════════════════

#: Track A modules that drive the ACTIVE booking paths on SQLite: the Data Exchange
#: `confirm_booking` writer, CE's `_process_flow_response`, Flow dispatch and — the only two
#: SQLite modules that do it — CE `handle()` with a `flow_response` message, which is where
#: the flag-gated ledger lookup sits. A flag-off reachability mutation at that gate SURVIVED
#: this guard until `test_m20_6d2_customer_reality` / `test_m20_6c1_copy_polish` were added
#: (measured). The PostgreSQL Track A suites are instrumented separately, by the harness.
_NEUTRAL_MODULES = ("test_l4_7w5_g3_7r_unification", "test_m21_3_c_d_booking_flow",
                    "test_l4_7w4_f3_booking_price_completeness", "test_sched_flow_dispatch",
                    "test_m21_2_mf_motorcycle_flow", "test_m21_0_1_live_path_contract",
                    "test_l4_7w5_g3_7n_scheduling_withdrawal",
                    "test_l4_7w5_f3_booking_closure_faq_dedup",
                    "test_m20_6d2_customer_reality", "test_m20_6c1_copy_polish")


class P2U_FlagOffNeutrality(unittest.TestCase):
    """Track A's own CE and Data Exchange booking tests, with the flag at its default (off),
    must never reach the Smart Booking authority, its adapter or CE's SB2B methods."""

    def test_p2u_30_active_flag_off_paths_make_zero_authority_calls(self):
        from app.services import conversation_engine as CEm
        from app.settings import get_settings
        self.assertIs(get_settings().smart_booking_ce_enabled, False)
        calls = []

        def spy(name, real):
            def wrapper(*a, **k):
                calls.append(name)
                return real(*a, **k)
            wrapper.__wrapped__ = real
            return wrapper
        targets = [(BookingAuthority, n) for n in ("execute", "lookup", "claim_acknowledgement",
                                                   "record_acknowledgement", "note_replay")]
        targets += [(ce, n) for n in ("book_from_flow_response", "request_manual_scheduling",
                                      "lookup_booking")]
        targets += [(CEm.ConversationEngine, n) for n in ("_sb_flow_book", "_sb_flow_ledger_entry",
                                                          "_sb_escalation")]
        patches = [mock.patch.object(obj, n, spy(f"{getattr(obj, '__name__', obj)}.{n}",
                                                 getattr(obj, n))) for obj, n in targets]
        loader = unittest.TestLoader()
        suite = unittest.TestSuite(loader.loadTestsFromName(m) for m in _NEUTRAL_MODULES)
        result = unittest.TestResult()
        for p in patches:
            p.start()
        try:
            suite.run(result)
        finally:
            for p in patches:
                p.stop()
        self.assertGreater(result.testsRun, 100)
        self.assertEqual(calls, [], f"flag-off paths reached the authority: {sorted(set(calls))}")


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
