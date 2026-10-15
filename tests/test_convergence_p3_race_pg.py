"""Convergence Phase 3 — the CE / Data Exchange cutover, RACED ON REAL POSTGRESQL.

The design audit's race matrix (§14), races 1–14 and 16–18, driven through the production
route behind `POST /api/conversation/handle` and the production Data Exchange service, both
delegating to `BookingAuthority`. Race 15 (tester reset after success) is Phase 2's
`test_convergence_p2_authority_pg::ResetLedger*`, re-run unchanged.

No race passes by running sequentially: every concurrent case either holds the first writer
INSIDE its lock until a sampler on a third connection has SEEN the second one waiting
(`pg_locks.granted = false`), or meets at a barrier inside `BookingAuthority.execute` and then
asserts the observed wait. Every assertion is on persisted rows, outbound-ledger rows, typed
outcomes, SQLSTATEs or timings — never on source text.

Where the design expected a durable attempt event (`booking_attempt_events`, Phase 4), the
assertion here is the honest one for Phase 3: NO durable refusal record is claimed — there
is no such table — and the authority wrote nothing.

  CONV_P3_PG_URL   a disposable PostgreSQL 16 database at the Alembic head, built the
                   project's way (pre-branch `create_all` + stamp, then `alembic upgrade head`
                   with this tree). Skips cleanly without it, and says so.

The network edge is the `_sb2b_harness` recorder (`OUTBOUND_ENABLED` is set only inside that
recorder, whose Meta transport never opens a socket; `urllib.request.urlopen` raises).
"""
from __future__ import annotations

import os
import pathlib
import sys
import threading
import time as _t
import unittest
from datetime import time
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
PG_URL = os.environ.get("CONV_P3_PG_URL", "")
if PG_URL:
    os.environ["DATABASE_URL"] = PG_URL

import _sb2b_harness as H  # noqa: E402

from sqlalchemy import create_engine, select, text, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (BookingCommandRecord, Lead, Revision, ThreadRevision,  # noqa: E402
                        WhatsAppBookingOffer, WhatsAppMessage, WhatsAppThreadCandidate,
                        WhatsAppThreadState)
from app.services import booking_ce_adapter as adapter  # noqa: E402
from app.services.booking_authority import (BookingAuthority, BookingCommand,  # noqa: E402
                                            BookingOperation, BookingSource,
                                            flow_idempotency_key)
from app.services.booking_flow_service import BookingSlotConflictError  # noqa: E402
from app.services.conversation_engine import ConversationEngine  # noqa: E402


class Crash(BaseException):
    """Process death: not an Exception, so nothing catches it; the session closes uncommitted."""


def _truncate(engine):
    with engine.begin() as c:
        names = set(c.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")).scalars())
        tables = [t.name for t in Lead.__table__.metadata.sorted_tables if t.name in names]
        c.execute(text("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY CASCADE"))


class Sampler(threading.Thread):
    """A third connection that watches `pg_locks` for an ungranted lock in this database.
    `seen` is set the first time a waiter exists; `kinds` records what was waited on."""

    def __init__(self, engine):
        super().__init__(daemon=True)
        self.engine, self.seen, self.stop_ = engine, threading.Event(), threading.Event()
        self.kinds, self.max_waiters = set(), 0

    def run(self):
        with self.engine.connect() as c:
            c.execute(text("SET application_name = 'p3-sampler'"))
            while not self.stop_.is_set():
                rows = c.execute(text(
                    "SELECT l.locktype FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid "
                    "WHERE NOT l.granted AND a.datname = current_database()")).scalars().all()
                c.commit()
                if rows:
                    self.kinds.update(rows)
                    self.max_waiters = max(self.max_waiters, len(rows))
                    self.seen.set()
                _t.sleep(0.01)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop_.set()
        self.join(timeout=5)
        return False


class HoldFirst:
    """Patch `BookingAuthority.<method>` so the FIRST caller to return from it pauses — still
    holding everything it has locked — until the sampler sees a second writer waiting (or
    `timeout`). That is what makes the race window real rather than hoped for."""

    def __init__(self, method, sampler, timeout=3.0):
        self.method, self.sampler, self.timeout = method, sampler, timeout
        self.first, self.held, self.waited_seen = threading.Lock(), [], None

    def __enter__(self):
        real = getattr(BookingAuthority, self.method)
        me = self

        def wrapper(auth, *a, **k):
            out = real(auth, *a, **k)
            if me.first.acquire(blocking=False):
                me.held.append(threading.get_ident())
                me.waited_seen = me.sampler.seen.wait(me.timeout)
            return out
        self._p = mock.patch.object(BookingAuthority, self.method, wrapper)
        self._p.start()
        return self

    def __exit__(self, *exc):
        self._p.stop()
        return False


def _measure(**kv):
    """One line of evidence on stderr (`-s`): what the race window measured."""
    sys.stderr.write("P3R-MEASURE " + " ".join(f"{k}={v}" for k, v in kv.items()) + "\n")


def _threads(*fns, timeout=30):
    out, errs = [None] * len(fns), []

    def runner(i, fn):
        try:
            out[i] = fn()
        except BaseException as exc:                      # noqa: BLE001 — reported below
            out[i] = exc
            errs.append(exc)
    ts = [threading.Thread(target=runner, args=(i, fn)) for i, fn in enumerate(fns)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout)
    return out


@unittest.skipUnless(PG_URL, "CONV_P3_PG_URL not set — no disposable PostgreSQL; nothing proven")
class _Race(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_URL, pool_size=20, max_overflow=10)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        with self.engine.begin() as c:
            c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE "
                           "datname = current_database() AND pid <> pg_backend_pid() "
                           "AND application_name <> 'p3-keep'"))
        self.engine.dispose()
        _truncate(self.engine)

    # ── facts ────────────────────────────────────────────────────────────────

    def facts(self, w):
        f = H.facts(self.engine, w)
        with Session(self.engine) as db:
            f["offers"] = [tuple(r) for r in db.execute(select(
                WhatsAppBookingOffer.status, WhatsAppBookingOffer.accepted_revision_id,
                WhatsAppBookingOffer.accepted_thread_revision_id,
                WhatsAppBookingOffer.terminal_reason).where(
                WhatsAppBookingOffer.thread_id == w.thread_id).order_by(
                WhatsAppBookingOffer.id)).all()]
            f["tr_offer"] = [tuple(r) for r in db.execute(select(
                ThreadRevision.id, ThreadRevision.status, ThreadRevision.offer_id).where(
                ThreadRevision.thread_id == w.thread_id)).all()]
            f["ledger_full"] = [tuple(r) for r in db.execute(select(
                BookingCommandRecord.idempotency_key, BookingCommandRecord.operation,
                BookingCommandRecord.ack_status, BookingCommandRecord.replay_count,
                BookingCommandRecord.revision_id, BookingCommandRecord.command_state).where(
                BookingCommandRecord.thread_id == w.thread_id)).all()]
        return f

    def occupying_at(self, day, at):
        with Session(self.engine) as db:
            return db.execute(text(
                "SELECT count(*) FROM revisions WHERE turno_fecha = :d AND turno_hora = :t "
                "AND estado_revision IN ('PENDIENTE','CONFIRMADO','EN_PROCESO','COMPLETADO')"),
                {"d": day, "t": at}).scalar()

    def owner_texts(self, w):
        with Session(self.engine) as db:
            return db.execute(select(WhatsAppMessage.status).where(
                WhatsAppMessage.thread_id == w.thread_id, WhatsAppMessage.direction == "out",
                WhatsAppMessage.text == H.OWNER_TEXT)).scalars().all()

    def ce(self, w, *, wamid=None, cfg=None):
        return lambda: H.run_turn(self.engine, H.flow_event(w, wamid=wamid), cfg=cfg)[0]

    def dx(self, w, **kw):
        def call():
            try:
                return H.dx_confirm(self.engine, w, **kw)
            except BookingSlotConflictError as exc:
                return exc
        return call

    def assert_booked_once(self, w, f=None):
        f = f or self.facts(w)
        key = flow_idempotency_key(w.token)
        self.assertEqual(f["occupying"], 1)
        self.assertEqual(len(f["tr_offer"]), 1)
        self.assertEqual(f["tr_offer"][0][1:], ("booked", 1))
        self.assertEqual([r[0] for r in f["ledger_full"]], [key])
        self.assertEqual(f["offers"][0][0], "ACCEPTED")
        self.assertEqual(f["offers"][0][2], f["tr_offer"][0][0])
        self.assertEqual((f["last_stage"], f["token"], f["needs_human"]), ("BOOKED", None, True))
        return f


# ══ race 1, 5, 6: one token, two writers ═══════════════════════════════════════════════

class P3R_SameToken(_Race):

    def _race(self, a, b, hold="_lock_thread_state"):
        with Sampler(self.engine) as smp, HoldFirst(hold, smp) as hold_, H.Meta() as m:
            t0 = _t.monotonic()
            out = _threads(a, b)
            elapsed = _t.monotonic() - t0
        self.assertTrue(hold_.waited_seen, "the second writer was never seen waiting — the "
                                           "race window was not reached")
        _measure(test=self.id().rsplit(".", 1)[-1], elapsed=f"{elapsed:.3f}",
                 waited_on=sorted(smp.kinds), max_waiters=smp.max_waiters)
        return out, smp, elapsed, m

    def test_p3r_01_ce_vs_dx_same_token_one_booking_one_text(self):
        w = H.seed(self.engine)
        (ce, dx), smp, elapsed, m = self._race(self.ce(w), self.dx(w))
        f = self.assert_booked_once(w)
        self.assertIn(ce.action, ("booking_created", "booking_replayed"))
        self.assertEqual(dx["screen"], "SUCCESS")
        self.assertEqual([t for _w, t in m.sent].count(H.OWNER_TEXT), 1, "exactly one text")
        self.assertEqual(self.owner_texts(w), ["sent"])
        self.assertEqual(f["ledger_full"][0][2], "SENT")
        self.assertEqual(f["cursor"], w.wamid)
        self.assertLess(elapsed, 12.0)
        # a retry of either transport is a replay: no booking, no text
        with H.Meta() as m2:
            again_ce = self.ce(w, wamid=w.wamid + ".r")()
            again_dx = self.dx(w)()
        self.assertEqual((again_ce.action, again_dx["screen"], m2.sent),
                         ("booking_replayed", "SUCCESS", []))
        self.assertEqual(self.facts(w)["occupying"], 1)

    def test_p3r_05_two_ce_deliveries_one_booking_one_text(self):
        w = H.seed(self.engine)
        (a, b), smp, elapsed, m = self._race(self.ce(w), self.ce(w, wamid=w.wamid + ".dup"))
        self.assert_booked_once(w)
        self.assertEqual(sorted([a.action, b.action]), ["booking_created", "booking_replayed"])
        self.assertEqual([t for _w, t in m.sent].count(H.OWNER_TEXT), 1)
        self.assertEqual(self.owner_texts(w), ["sent"])

    def test_p3r_06_two_dx_submissions_both_succeed_one_booking_one_text(self):
        w = H.seed(self.engine)
        (a, b), smp, elapsed, m = self._race(self.dx(w), self.dx(w))
        self.assert_booked_once(w)
        self.assertEqual((a["screen"], b["screen"]), ("SUCCESS", "SUCCESS"))
        self.assertEqual([t for _w, t in m.sent].count(H.OWNER_TEXT), 1)
        self.assertEqual(self.owner_texts(w), ["sent"])


# ══ race 2, 3, 4, 12: one slot, two bookings ════════════════════════════════════════════

class P3R_SameSlot(_Race):

    def _operator(self, lead_id, day, at):
        def call():
            with Session(self.engine) as s:
                return BookingAuthority(s).execute(BookingCommand(
                    operation=BookingOperation.CREATE, source=BookingSource.CRM,
                    idempotency_key=f"form:op-{lead_id}-{day}-{at}", lead_id=lead_id,
                    requested_date=day, requested_time=at,
                    revision_fields={"tipo_vehiculo": "Auto"}))
        return call

    def _other_lead(self):
        with Session(self.engine) as db:
            lead = Lead(estado="CONSULTA_NUEVA", nombre="Operador")
            db.add(lead); db.commit()
            return lead.id

    def _race(self, a, b):
        with Sampler(self.engine) as smp, HoldFirst("_acquire_locks", smp) as hold, \
                H.Meta() as m:
            out = _threads(a, b)
        self.assertTrue(hold.waited_seen, "the second booking never waited on the date lock")
        self.assertIn("advisory", smp.kinds)
        _measure(test=self.id().rsplit(".", 1)[-1], waited_on=sorted(smp.kinds))
        return out, m

    def test_p3r_02_ce_vs_dx_same_slot_two_threads(self):
        w1 = H.seed(self.engine)
        w2 = H.seed(self.engine, day=w1.day, at=w1.at)
        (ce, dx), m = self._race(self.ce(w1), self.dx(w2))
        self.assertEqual(self.occupying_at(w1.day, w1.at), 1)
        f1, f2 = self.facts(w1), self.facts(w2)
        winner_ce = ce.action == "booking_created"
        self.assertNotEqual(winner_ce, not isinstance(dx, BookingSlotConflictError))
        loser = f2 if winner_ce else f1
        self.assertEqual(loser["offers"][0][0], "ISSUED", "the loser's offer stays ISSUED")
        self.assertEqual([r[1] for r in loser["ledger_full"] if r[1] == "CREATE"], [])
        if winner_ce:
            self.assertEqual(dx.refreshed_data["data"]["slot_conflict_message"][:5], "Justo")
        else:
            # CE loser: B-6 non-occupying manual request + truthful handoff, never a success
            self.assertEqual(ce.action, "booking_refused_handoff")
            self.assertIn(ce.detail, ("OVERLAP", "SCHEDULE_REVALIDATION", "EXCLUSION_CONSTRAINT"))
            self.assertEqual([r[1] for r in f1["ledger_full"]], ["REQUEST_MANUAL_SCHEDULING"])
            self.assertEqual(f1["needs_human"], True)
        self.assertEqual([t for _w, t in m.sent].count(H.OWNER_TEXT), 1)

    def test_p3r_03_ce_vs_operator_same_slot(self):
        w = H.seed(self.engine)
        op_lead = self._other_lead()
        (ce, op), m = self._race(self.ce(w), self._operator(op_lead, w.day, w.at))
        self.assertEqual(self.occupying_at(w.day, w.at), 1)
        f = self.facts(w)
        if ce.action == "booking_created":
            self.assertEqual(op.outcome.value, "SLOT_CONFLICT")
            self.assert_booked_once(w, f)
        else:
            self.assertEqual(op.outcome.value, "COMMITTED")
            self.assertEqual(ce.action, "booking_refused_handoff")
            self.assertIn(ce.detail, ("OVERLAP", "SCHEDULE_REVALIDATION", "EXCLUSION_CONSTRAINT"))
            self.assertEqual(f["offers"][0][0], "ISSUED")
            self.assertNotIn(H.OWNER_TEXT, [t for _w, t in m.sent])

    def test_p3r_04_dx_vs_operator_same_slot(self):
        w = H.seed(self.engine)
        op_lead = self._other_lead()
        (dx, op), m = self._race(self.dx(w), self._operator(op_lead, w.day, w.at))
        self.assertEqual(self.occupying_at(w.day, w.at), 1)
        if isinstance(dx, dict):
            self.assertEqual((dx["screen"], op.outcome.value), ("SUCCESS", "SLOT_CONFLICT"))
        else:
            self.assertEqual(op.outcome.value, "COMMITTED")
            self.assertEqual(self.facts(w)["offers"][0][0], "ISSUED")
            self.assertNotIn(H.OWNER_TEXT, [t for _w, t in m.sent])

    def test_p3r_12_slot_taken_after_the_offer_before_acceptance(self):
        w = H.seed(self.engine)
        other = H.seed(self.engine, day=w.day, at=w.at)
        with H.Meta():
            self.assertEqual(self.dx(other)()["screen"], "SUCCESS")   # takes the slot
        with H.Meta() as m:
            out = self.ce(w)()
        f = self.facts(w)
        self.assertEqual(out.action, "booking_refused_handoff")
        self.assertIn(out.detail, ("OVERLAP", "SCHEDULE_REVALIDATION", "EXCLUSION_CONSTRAINT"))
        self.assertEqual(f["offers"][0][0], "ISSUED")
        self.assertEqual([r[1] for r in f["ledger_full"]], ["REQUEST_MANUAL_SCHEDULING"])
        self.assertEqual(f["occupying"], 0)
        self.assertEqual([t for _w, t in m.sent], [H.HANDOFF_TEXT])
        with H.Meta():
            retry = self.dx(w)()                                  # the same refusal
        self.assertIsInstance(retry, BookingSlotConflictError)
        self.assertEqual(self.occupying_at(w.day, w.at), 1)


# ══ race 7, 8, 9: crash and kill windows ═══════════════════════════════════════════════

class P3R_Crash(_Race):

    def test_p3r_07_crash_before_the_booking_commit_leaves_nothing(self):
        w = H.seed(self.engine)
        with mock.patch.object(BookingAuthority, "_verify_before_commit",
                               side_effect=Crash("worker died")), H.Meta() as m:
            with self.assertRaises(Crash):
                self.ce(w)()
        f = self.facts(w)
        self.assertEqual((f["revisions"], f["tr_offer"], f["ledger_full"], f["cursor"]),
                         ([], [], [], None))
        self.assertEqual(f["offers"][0][0], "ISSUED")
        self.assertEqual(m.sent, [])
        with H.Meta() as m2:
            out = self.ce(w)()                                 # redelivery books once
        self.assertEqual(out.action, "booking_created")
        self.assert_booked_once(w)
        self.assertEqual([t for _w, t in m2.sent], [H.OWNER_TEXT])

    def test_p3r_08_connection_killed_after_writes_before_commit(self):
        w = H.seed(self.engine)
        pids, ready, go = [], threading.Event(), threading.Event()
        real = BookingAuthority._verify_before_commit

        def pause(auth, *a, **k):
            pids.append(auth.db.execute(text("SELECT pg_backend_pid()")).scalar())
            written = auth.db.execute(text(
                "SELECT (SELECT count(*) FROM booking_commands), "
                "(SELECT count(*) FROM thread_revisions WHERE offer_id IS NOT NULL)")).one()
            pids.append(tuple(written))
            ready.set()
            go.wait(10)
            return real(auth, *a, **k)
        with mock.patch.object(BookingAuthority, "_verify_before_commit", pause), H.Meta() as m:
            th = threading.Thread(target=lambda: pids.append(self.ce(w)()))
            th.start()
            self.assertTrue(ready.wait(10))
            with self.engine.connect() as c:
                c.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pids[0]})
                c.commit()
            go.set()
            th.join(20)
        out = pids[-1]
        self.assertEqual(pids[1], (1, 1), "the kill landed after the ledger and the booking "
                                          "rows were written inside the transaction")
        f = self.facts(w)
        self.assertEqual((f["revisions"], f["tr_offer"], f["ledger_full"]), ([], [], []))
        self.assertEqual(f["offers"][0][:3], ("ISSUED", None, None))
        self.assertEqual((out.action, f["cursor"], f["needs_human"]),
                         ("thread_contended", None, False))
        self.assertEqual(m.sent, [])
        with H.Meta():
            self.assertEqual(self.ce(w)().action, "booking_created")
        self.assert_booked_once(w)

    def test_p3r_09_crash_after_commit_before_the_acknowledgement(self):
        w = H.seed(self.engine)
        with mock.patch.object(ConversationEngine, "_sb_acknowledge",
                               side_effect=Crash("died after commit")), H.Meta() as m:
            with self.assertRaises(Crash):
                self.ce(w)()
        f = self.assert_booked_once(w)
        self.assertEqual((f["ledger_full"][0][2], f["cursor"], m.sent), ("PENDING", None, []))
        with H.Meta() as m2:
            out = self.ce(w)()                                   # redelivery: ledger hit
        self.assertEqual(out.action, "booking_replayed")
        f2 = self.assert_booked_once(w)
        self.assertEqual((f2["ledger_full"][0][2], f2["cursor"]), ("SENT", w.wamid))
        self.assertEqual([t for _w, t in m2.sent], [H.OWNER_TEXT])
        with H.Meta() as m3:
            self.assertEqual(self.ce(w, wamid=w.wamid + ".3")().action, "booking_replayed")
        self.assertEqual(m3.sent, [], "SENT replay sends nothing")


# ══ race 10, 14: transient contention writes nothing and is retried once ═══════════════

class P3R_Contention(_Race):

    def _hold_k1(self, thread_id, seconds):
        def holder():
            with self.engine.connect() as c:
                c.execute(text("SELECT 1 FROM whatsapp_thread_states WHERE thread_id = :t "
                               "FOR NO KEY UPDATE"), {"t": thread_id})
                _t.sleep(seconds)
                c.rollback()
        th = threading.Thread(target=holder)
        th.start()
        _t.sleep(0.3)
        return th

    def test_p3r_10_k1_contention_writes_nothing_and_redelivery_books_once(self):
        w = H.seed(self.engine)
        holder = self._hold_k1(w.thread_id, 6.0)
        with Sampler(self.engine) as smp, H.Meta() as m:
            t0 = _t.monotonic()
            out = self.ce(w)()
            elapsed = _t.monotonic() - t0
        holder.join()
        self.assertTrue(smp.seen.is_set(), "the turn never waited on K1")
        self.assertEqual((out.action, out.detail), ("thread_contended", "THREAD_STATE_CONTENDED"))
        self.assertTrue(out.reply_required)
        self.assertGreater(elapsed, 3.8)
        self.assertLess(elapsed, 6.0, "bounded by the authority's 4 s lock_timeout")
        _measure(test="p3r_10", contended_elapsed=f"{elapsed:.3f}", waited_on=sorted(smp.kinds))
        f = self.facts(w)
        self.assertEqual((f["revisions"], f["tr_offer"], f["ledger_full"]), ([], [], []))
        self.assertEqual((f["cursor"], f["needs_human"], f["lead_estado"], f["token"]),
                         (None, False, "CONSULTA_NUEVA", w.token))
        self.assertEqual(f["offers"][0][0], "ISSUED")
        self.assertEqual(m.sent, [])
        # Convergence Phase 4 adaptation. OLD: "no durable attempt event is claimed — there is
        # no such table" (Phase 3 recorded this dimension as Phase 4). NEW: the contention IS
        # durable, as exactly one RETRYABLE authority-stage event — and still nothing else was
        # written (asserted above). WHY: Phase 4 built the table. PROOF: absent on the parent.
        with self.engine.connect() as c:
            evs = c.execute(text("SELECT source, stage, outcome, reason FROM "
                                 "booking_attempt_events WHERE hist_thread_id = :t"),
                            {"t": w.thread_id}).all()
        self.assertEqual([tuple(e) for e in evs], [("CONVERSATION_ENGINE", "AUTHORITY",
                                                    "RETRYABLE", "THREAD_STATE_CONTENDED")])
        with H.Meta() as m2:
            out2 = self.ce(w)()                                  # after release
        self.assertEqual(out2.action, "booking_created")
        self.assert_booked_once(w)
        self.assertEqual([t for _w, t in m2.sent], [H.OWNER_TEXT])

    def test_p3r_10b_dx_contention_is_a_retry_screen_and_writes_nothing(self):
        w = H.seed(self.engine)
        holder = self._hold_k1(w.thread_id, 6.0)
        with Sampler(self.engine) as smp, H.Meta() as m:
            out = self.dx(w)()
        holder.join()
        self.assertTrue(smp.seen.is_set())
        self.assertIsInstance(out, BookingSlotConflictError)
        self.assertIn("Volvé a intentarlo", out.refreshed_data["data"]["slot_conflict_message"])
        f = self.facts(w)
        self.assertEqual((f["revisions"], f["ledger_full"], f["needs_human"], m.sent),
                         ([], [], False, []))
        with H.Meta():
            self.assertEqual(self.dx(w)()["screen"], "SUCCESS")
        self.assert_booked_once(w)

    def test_p3r_14_retry_after_rollback_is_a_fresh_attempt(self):
        from sqlalchemy.exc import OperationalError

        class _Orig(Exception):
            sqlstate = "40P01"
        w = H.seed(self.engine)
        outcomes = []
        real = BookingAuthority.execute

        def spy(auth, *a, **k):
            r = real(auth, *a, **k)
            outcomes.append((r.outcome.value, r.reason))
            return r
        with mock.patch.object(BookingAuthority, "execute", spy):
            with mock.patch.object(BookingAuthority, "_check_calendar",
                                   side_effect=OperationalError("x", {}, _Orig("deadlock"))), \
                    H.Meta() as m:
                out1 = self.ce(w)()
            self.assertEqual(self.facts(w)["ledger_full"], [])
            with H.Meta() as m2:
                out2 = self.ce(w)()
        self.assertEqual(outcomes, [("RETRYABLE_FAILURE", "DB_40P01"), ("COMMITTED", "COMMITTED")],
                         "the retry EXECUTED again — a fresh attempt, not a replay")
        self.assertEqual((out1.action, out2.action), ("thread_contended", "booking_created"))
        self.assert_booked_once(w)
        self.assertEqual((m.sent, [t for _w, t in m2.sent]), ([], [H.OWNER_TEXT]))


# ══ race 11, 17, 18: the offer moves under the submission ══════════════════════════════

class P3R_OfferMoves(_Race):

    def test_p3r_11_category_change_between_admission_and_execute(self):
        """CE admitted the offer as a read; before the authority locks it, the candidate's
        category changes (committed by another session). The authority re-derives from the
        LOCKED rows and refuses; the ledger is re-read; a truthful handoff, no booking."""
        w = H.seed(self.engine)
        real = ConversationEngine._sb_transaction_clean

        def change_then_check(eng):
            with Session(self.engine) as other:
                other.execute(update(WhatsAppThreadCandidate).where(
                    WhatsAppThreadCandidate.id == w.candidate_id).values(tipo_vehiculo="SUV/4x4"))
                other.commit()
            return real(eng)
        lookups = []
        real_hit = ConversationEngine._sb_ledger_hit
        with mock.patch.object(ConversationEngine, "_sb_transaction_clean", change_then_check), \
                mock.patch.object(ConversationEngine, "_sb_ledger_hit",
                                  lambda eng, key: (lookups.append(key), real_hit(eng, key))[1]), \
                H.Meta() as m:
            out = self.ce(w)()
        f = self.facts(w)
        self.assertEqual((out.action, out.detail),
                         ("booking_refused_handoff", "OFFER_VEHICLE_CATEGORY_CHANGED"))
        self.assertEqual(len(lookups), 2, "entry lookup + the re-read before the handoff")
        self.assertEqual((f["revisions"], f["ledger_full"], f["occupying"]), ([], [], 0))
        self.assertEqual(f["offers"][0][0], "ISSUED")
        self.assertEqual([t for _w, t in m.sent], [H.HANDOFF_TEXT])

    def test_p3r_11b_a_vehicle_change_withdraws_the_offer_and_the_submission_is_refused(self):
        w = H.seed(self.engine)
        from app.services.offer_lifecycle import OfferLifecycle, WithdrawReason
        with Session(self.engine) as db:
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
            OfferLifecycle(db).withdraw(st, thread_id=w.thread_id,
                                        reason=WithdrawReason.VEHICLE_CHANGED)
            db.commit()
        with mock.patch.object(BookingAuthority, "execute",
                               side_effect=AssertionError("must not delegate")), H.Meta() as m:
            out = self.ce(w)()
        f = self.facts(w)
        self.assertEqual(out.action, "booking_refused_handoff")
        self.assertEqual(f["offers"][0][0::3], ("WITHDRAWN", "VEHICLE_CHANGED"))
        self.assertEqual((f["revisions"], f["ledger_full"]), ([], []))
        self.assertEqual([t for _w, t in m.sent], [H.HANDOFF_TEXT])

    def test_p3r_17_issue_vs_acceptance_never_deadlocks(self):
        from app.services.offer_lifecycle import OfferLifecycle
        from app.services.booking_flow_service import make_booking_token
        deadlocks, outcomes = 0, []
        for trial in range(8):
            _truncate(self.engine)
            w = H.seed(self.engine)
            barrier = threading.Barrier(2)

            def issue():
                barrier.wait(5)
                if trial % 2:
                    _t.sleep(0.05)
                with Session(self.engine) as db:
                    st = db.execute(select(WhatsAppThreadState).where(
                        WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
                    OfferLifecycle(db).issue(st, thread_id=w.thread_id,
                                             token=make_booking_token(w.thread_id),
                                             cycle_key=0, candidate_id=w.candidate_id,
                                             tipo_vehiculo="Auto", zone_group="Norte",
                                             zone_detail="Benavidez")
                    db.commit()
                return "issued"

            def accept():
                barrier.wait(5)
                if not trial % 2:
                    _t.sleep(0.05)
                return self.ce(w)()
            with H.Meta():
                res = _threads(accept, issue)
            for r in res:
                if "40P01" in repr(r) or "deadlock" in repr(r).lower():
                    deadlocks += 1
            ce = res[0]
            f = self.facts(w)
            statuses = [o[0] for o in f["offers"]]
            if ce.action == "booking_created":
                outcomes.append("booked")
                self.assertEqual(statuses, ["ACCEPTED", "ISSUED"])
                self.assertEqual(f["occupying"], 1)
            else:
                outcomes.append("superseded")
                self.assertEqual(statuses[0], "SUPERSEDED")
                self.assertEqual((f["occupying"], f["ledger_full"]), (0, []))
        self.assertEqual(deadlocks, 0)
        self.assertEqual(len(outcomes), 8)

    def test_p3r_18_cycle_reset_plus_booking_submission(self):
        w = H.seed(self.engine, cycle_reset=True)
        lookups = []
        real_hit = ConversationEngine._sb_ledger_hit
        with mock.patch.object(ConversationEngine, "_sb_ledger_hit",
                               lambda eng, key: (lookups.append(key), real_hit(eng, key))[1]), \
                mock.patch.object(BookingAuthority, "execute",
                                  side_effect=AssertionError("a reset turn never delegates")), \
                H.Meta() as m:
            out = self.ce(w)()
        f = self.facts(w)
        self.assertEqual(out.action, "booking_refused_handoff")
        self.assertEqual(len(lookups), 2, "ledger read before the reset AND before the handoff")
        self.assertEqual(f["offers"][0][0::3], ("WITHDRAWN", "CYCLE_RESET"))
        self.assertEqual((f["revisions"], f["ledger_full"], f["cursor"]), ([], [], w.wamid))
        with Session(self.engine) as db:
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
            self.assertFalse(st.cycle_reset_pending, "the reset executed and committed")
        self.assertEqual([t for _w, t in m.sent], [H.HANDOFF_TEXT])


# ══ race 13, 16: replay after success; an ordinary turn on the same thread ═════════════

class P3R_Replay(_Race):

    def test_p3r_13_replay_after_success_mutates_nothing_and_sends_nothing(self):
        w = H.seed(self.engine)
        with H.Meta():
            self.assertEqual(self.ce(w)().action, "booking_created")
        before = self.facts(w)
        with H.Meta() as m:
            out = self.ce(w, wamid=w.wamid + ".again")()
            dx = self.dx(w)()
        after = self.facts(w)
        self.assertEqual((out.action, dx["screen"], m.sent), ("booking_replayed", "SUCCESS", []))
        for k in ("revisions", "tr_offer", "offers", "occupying", "token", "last_stage"):
            self.assertEqual(before[k], after[k], k)
        self.assertEqual(after["ledger_full"][0][3], 2, "two replays counted on the ledger")
        self.assertEqual(after["ledger_full"][0][2], "SENT")

    def test_p3r_16_an_ordinary_turn_on_the_same_thread_waits_one_bound_at_most(self):
        """The authority holds K1 (paused after taking it); an ordinary CE text turn on the same
        thread commits its own state write meanwhile. It waits for the authority, never
        deadlocks, and finishes within one bound of the release; the booking commits."""
        from app.schemas.conversation import ConversationHandleIn
        w = H.seed(self.engine)
        text_ev = ConversationHandleIn(thread_id=w.thread_id, wa_message_id="wamid.P3.TEXT",
                                       wa_id=w.wa_id, message_type="text", text="hola")
        took, release = threading.Event(), threading.Event()
        real = BookingAuthority._lock_thread_state

        def hold(auth, *a, **k):
            out = real(auth, *a, **k)
            took.set()
            release.wait(10)
            return out
        times = {}

        def text_turn():
            took.wait(10)
            t0 = _t.monotonic()
            out = H.run_turn(self.engine, text_ev)[0]
            times["text"] = _t.monotonic() - t0
            return out
        with mock.patch.object(BookingAuthority, "_lock_thread_state", hold), \
                Sampler(self.engine) as smp, H.Meta():
            ts = threading.Thread(target=lambda: times.setdefault("book", self.ce(w)()))
            ts.start()
            tt_out = []
            tt = threading.Thread(target=lambda: tt_out.append(text_turn()))
            tt.start()
            self.assertTrue(smp.seen.wait(8), "the ordinary turn never waited on K1")
            _t.sleep(1.0)
            release.set()
            ts.join(20); tt.join(20)
        self.assertEqual(times["book"].action, "booking_created")
        self.assertNotIn("40P01", repr(tt_out))
        self.assertEqual(tt_out[0].action, "replied")
        self.assertLess(times["text"], 6.0)
        self.assert_booked_once(w)
        _measure(test="p3r_16", text_turn_elapsed=f"{times['text']:.3f}",
                 waited_on=sorted(smp.kinds))

    def test_p3r_19_the_ce_sync_takes_the_thread_state_before_the_lead(self):
        """CD-07 for the synchronisation Phase 3 owns. One flush writes `leads` before
        `whatsapp_thread_states` (E5), so a sync that dirtied the lead without taking K1
        first would hold the lead and wait for the state row while a second booking of the
        same thread holds the state row and waits for the lead: `40P01`. Here a second
        authority transaction on the same thread and lead is held after K1, the replayed
        first submission synchronises (its attribution writes the lead), and then the
        authority is released. Both must complete; nobody may be a deadlock victim."""
        from app.models import WhatsAppThread
        from app.services.booking_flow_service import make_booking_token
        from app.services.offer_lifecycle import OfferLifecycle
        w = H.seed(self.engine)
        with H.Meta():
            self.assertEqual(self.dx(w)()["screen"], "SUCCESS")         # booking 1 (+ SENT)
        with Session(self.engine) as db:                                 # attribution will write
            db.execute(update(WhatsAppThread).where(WhatsAppThread.id == w.thread_id)
                       .values(inbound_channel="whatsapp"))
            db.execute(update(Lead).where(Lead.id == w.lead_id).values(inbound_channel=None))
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
            token2 = make_booking_token(w.thread_id) + "-2"
            OfferLifecycle(db).issue(st, thread_id=w.thread_id, token=token2, cycle_key=0,
                                     candidate_id=w.candidate_id, tipo_vehiculo="Auto",
                                     zone_group="Norte", zone_detail="Benavidez")
            db.commit()
        w2 = H.World(w.thread_id, w.lead_id, w.contact_id, w.wa_id, token2, w.wamid,
                     w.candidate_id, w.day, time(11, 30))
        took, release, outcomes = threading.Event(), threading.Event(), {}
        real_claim = BookingAuthority._claim_offer

        def held(auth, *a, **k):
            if threading.current_thread().name == "P3-AUTH":
                took.set()
                release.wait(10)
            return real_claim(auth, *a, **k)

        def second_booking():
            try:
                outcomes["dx"] = H.dx_confirm(self.engine, w2)["screen"]
            except BookingSlotConflictError as exc:
                outcomes["dx"] = "CONFLICT:" + exc.refreshed_data["data"]["slot_conflict_message"]
            except Exception as exc:                          # noqa: BLE001
                outcomes["dx"] = repr(exc)

        def replay_sync():
            try:
                outcomes["ce"] = self.ce(w, wamid=w.wamid + ".sync")()
            except Exception as exc:                          # noqa: BLE001
                outcomes["ce"] = repr(exc)
        with mock.patch.object(BookingAuthority, "_claim_offer", held), \
                Sampler(self.engine) as smp, H.Meta():
            a = threading.Thread(target=second_booking, name="P3-AUTH")
            a.start()
            self.assertTrue(took.wait(10), "the second booking never took K1")
            b = threading.Thread(target=replay_sync, name="P3-SYNC")
            b.start()
            self.assertTrue(smp.seen.wait(8), "the synchronisation never waited on K1")
            release.set()
            a.join(30); b.join(30)
        self.assertEqual(outcomes.get("dx"), "SUCCESS", f"the second booking failed: {outcomes}")
        ce = outcomes.get("ce")
        self.assertEqual(getattr(ce, "action", ce), "booking_replayed", f"{outcomes}")
        self.assertNotIn("40P01", repr(outcomes))
        self.assertNotIn("deadlock", repr(outcomes).lower())
        with Session(self.engine) as db:
            lead = db.get(Lead, w.lead_id)
            self.assertEqual(lead.inbound_channel, "whatsapp", "the sync's lead write landed")
        _measure(test="p3r_19", waited_on=sorted(smp.kinds))


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
