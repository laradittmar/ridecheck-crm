"""PRELAUNCH-P1 — the outbound recipient lock, and the operator notice that crosses it, on
PostgreSQL. A hard prerequisite of enabling OUTBOUND_ENABLED for customers.

The lock is `whatsapp_recipient_locks`, taken inside `OutboundSafetyGate.attempt()` for every
automated send. The operator notice crosses it on the OPERATOR_NOTIFICATION path, to the one
configured destination. Neither SQLite nor a timing race can prove this, so nothing here sleeps
to win a race: a gate is paused INSIDE the critical section, and `pg_stat_activity` is what says
the contender really is waiting on a lock.

  L  the lock itself: it serialises, every wait is bounded, it fails closed
  N  the notice: one delivery under a race, the retry's recipient, cancellation, rollback, flags
  B  the owner-reviewed production bounds, which the L cases patch down and so cannot see

  P1_LOCK_PG_URL  a disposable PostgreSQL 16 database at the Alembic head. Never crm or crm_test.

Meta is a recorder; `urlopen` raises. Synthetic data only.
"""
from __future__ import annotations

import contextlib
import itertools
import os
import pathlib
import sys
import threading
import time as clock
import unittest
from datetime import datetime, time, timedelta, timezone
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _sb2b_harness as H  # noqa: E402
from test_w1f_operator_signal import (Template, book, notifier, op_settings,  # noqa: E402
                                      set_floor)

from sqlalchemy import create_engine, event, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (BookingCommandRecord, OperatorNotification,  # noqa: E402
                        WhatsAppContact, WhatsAppMessage, WhatsAppOutboundDedup, WhatsAppThread)
from app.services import operator_notification as ON  # noqa: E402
from app.services.booking_flow_service import BookingSlotConflictError  # noqa: E402
from app.services import outbound_safety_gate as G  # noqa: E402
from app.services.outbound_path_registry import OutboundPathId  # noqa: E402
from app.services.outbound_safety_gate import OutboundSafetyGate  # noqa: E402

URL = os.environ.get("P1_LOCK_PG_URL", "")
CE_TEXT = OutboundPathId.CE_TEXT.value
OPERATOR = OutboundPathId.OPERATOR_NOTIFICATION.value
#: The outcome of a lock that could not be held. Compared by value so that a tree without it
#: fails on what the gate actually did, not on a missing name.
LOCK_REFUSED = "blocked_recipient_lock"
BOUND_MS = 300            # the bound under test; production's is `GATE_LOCK_TIMEOUT_MS`
JOIN = 15                 # far above any bound here: reaching it IS the unbounded-wait failure
_serial = itertools.count(1)


def _destination() -> str:
    """A fresh operator number per test: the flood window is per recipient and real-time."""
    return f"54911{os.getpid() % 1000:03d}{next(_serial):05d}"


def _recipient(engine, wa_id=None):
    wa_id = wa_id or f"P1LOCK{next(_serial):06d}"
    with engine.begin() as c:
        cid = c.execute(text(
            "INSERT INTO whatsapp_contacts (wa_id, display_name, phone, created_at) "
            "VALUES (:w, 'P1', '5491100000001', now()) RETURNING id"), {"w": wa_id}).scalar()
        tid = c.execute(text("INSERT INTO whatsapp_threads (contact_id, unread_count, created_at) "
                             "VALUES (:c, 0, now()) RETURNING id"), {"c": cid}).scalar()
        c.execute(text("INSERT INTO whatsapp_thread_states "
                       "(thread_id, needs_human, created_at, updated_at) "
                       "VALUES (:t, false, now(), now())"), {"t": tid})
    return wa_id, tid


class Bg:
    """`fn` in a thread; `done(timeout)` says whether it ended, `.value` what it returned."""

    def __init__(self, fn):
        self.value = self.error = None
        self.started = clock.monotonic()
        self.elapsed = None

        def run():
            try:
                self.value = fn()
            except Exception as exc:
                self.error = exc
            self.elapsed = clock.monotonic() - self.started
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def done(self, timeout=JOIN):
        self.thread.join(timeout)
        return not self.thread.is_alive()


def attempt(engine, wa_id, tid, body="Mensaje P1", *, path=CE_TEXT, **kw):
    with Session(engine) as db:
        return OutboundSafetyGate(db).attempt(wa_id=wa_id, thread_id=tid, text=body, path_id=path,
                                              **kw)


def waiting_on_lock(engine, needle, timeout=10.0) -> bool:
    """True once a backend of this database is really waiting on a heavyweight lock in a
    statement matching `needle`. A condition, polled; never a guess at timing."""
    deadline = clock.monotonic() + timeout
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        while clock.monotonic() < deadline:
            if c.execute(text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND wait_event_type = 'Lock' AND query ILIKE :q"),
                    {"q": f"%{needle}%"}).scalar():
                return True
            clock.sleep(0.02)
    return False


@contextlib.contextmanager
def first_gate_paused_inside(*, after_dedup=False):
    """The first gate to pass the recipient lock stops there, holding it, until released.
    `after_dedup`: it stops later, having found no duplicate and not yet committed its claim —
    the exact window the lock exists to protect."""
    entered, resume, seen = threading.Event(), threading.Event(), []
    step = "_check_dedup" if after_dedup else "_check_flood"
    original = getattr(OutboundSafetyGate, step)

    def paused(self, db, **kw):
        first = not seen
        seen.append(1)
        result = original(self, db, **kw) if after_dedup else None
        if first:
            entered.set()
            resume.wait(JOIN)
        return result if after_dedup else original(self, db, **kw)
    with mock.patch.object(OutboundSafetyGate, step, paused):
        try:
            yield entered, resume
        finally:
            resume.set()


@contextlib.contextmanager
def lock_row_held(engine, wa_id, *, committed_row=True):
    """Another transaction holding `wa_id`'s lock: an existing row FOR UPDATE, or a row it has
    inserted and not committed."""
    if committed_row:
        with engine.begin() as c:
            c.execute(text("INSERT INTO whatsapp_recipient_locks (wa_id, created_at) "
                           "VALUES (:w, now()) ON CONFLICT (wa_id) DO NOTHING"), {"w": wa_id})
    conn = engine.connect()
    tx = conn.begin()
    try:
        conn.execute(text("SELECT id FROM whatsapp_recipient_locks WHERE wa_id = :w FOR UPDATE")
                     if committed_row else
                     text("INSERT INTO whatsapp_recipient_locks (wa_id, created_at) "
                          "VALUES (:w, now())"), {"w": wa_id})
        yield
    finally:
        tx.rollback()
        conn.close()


def rows(engine, tid):
    with Session(engine) as db:
        return db.execute(select(WhatsAppMessage.status, WhatsAppMessage.blocked_reason).where(
            WhatsAppMessage.thread_id == tid).order_by(WhatsAppMessage.id)).all()


def statuses(engine, tid):
    return [s for s, _r in rows(engine, tid)]


def dedups(engine, wa_id):
    with Session(engine) as db:
        return db.execute(select(WhatsAppOutboundDedup.id).where(
            WhatsAppOutboundDedup.wa_id == wa_id)).scalars().all()


class B_ReviewedBounds(unittest.TestCase):
    """The L cases patch the bound down to stay fast, so none of them notices the production
    value moving. These need no database and run everywhere; `test_l15` proves on PostgreSQL
    that the same values are the ones the gate transaction really applies."""

    def test_b01_the_lock_bound_is_the_reviewed_4000_ms(self):
        self.assertEqual(G.GATE_LOCK_TIMEOUT_MS, 4000)

    def test_b02_the_statement_margin_is_the_reviewed_6000_ms(self):
        self.assertEqual(G.GATE_STATEMENT_TIMEOUT_MARGIN_MS, 6000)

    def test_b03_the_statement_bound_is_10000_ms_and_outlasts_the_lock_bound(self):
        effective = G.GATE_LOCK_TIMEOUT_MS + G.GATE_STATEMENT_TIMEOUT_MARGIN_MS
        self.assertEqual(effective, 10000)
        self.assertGreater(effective, G.GATE_LOCK_TIMEOUT_MS)


@unittest.skipUnless(URL, "P1_LOCK_PG_URL not set — no disposable PostgreSQL")
class L_RecipientLock(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(URL, pool_size=12)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        env = mock.patch.dict(os.environ, {"OUTBOUND_ENABLED": "true"})
        env.start()
        self.addCleanup(env.stop)

    def assert_refused_with_nothing_sendable(self, bg, wa_id, tid):
        self.assertTrue(bg.done(), "the gate was still waiting: the lock wait is unbounded")
        self.assertIsNone(bg.error, repr(bg.error))
        self.assertEqual(bg.value.outcome.value, LOCK_REFUSED, bg.value)
        self.assertEqual(statuses(self.engine, tid), ["blocked"])
        self.assertEqual(dedups(self.engine, wa_id), [])

    def _serialises(self, *, row_exists):
        wa_id, tid = _recipient(self.engine)
        if row_exists:
            with self.engine.begin() as c:
                c.execute(text("INSERT INTO whatsapp_recipient_locks (wa_id, created_at) "
                               "VALUES (:w, now())"), {"w": wa_id})
        with first_gate_paused_inside() as (entered, resume):
            first = Bg(lambda: attempt(self.engine, wa_id, tid))
            self.assertTrue(entered.wait(JOIN))
            second = Bg(lambda: attempt(self.engine, wa_id, tid))
            needle = "FOR UPDATE" if row_exists else "INSERT INTO whatsapp_recipient_locks"
            self.assertTrue(waiting_on_lock(self.engine, needle),
                            "the second attempt never waited on the recipient lock")
            self.assertEqual(statuses(self.engine, tid), [])     # neither has decided yet
            resume.set()
            self.assertTrue(first.done() and second.done())
        self.assertEqual(sorted(x.value.outcome.value for x in (first, second)),
                         ["allowed", "blocked_duplicate"])
        self.assertEqual(sorted(statuses(self.engine, tid)), ["blocked", "pending"])
        self.assertEqual(len(dedups(self.engine, wa_id)), 1)

    def test_l01_existing_row_the_contender_waits_on_for_update(self):
        self._serialises(row_exists=True)

    def test_l02_fresh_row_the_contender_waits_on_the_insert(self):
        self._serialises(row_exists=False)

    def test_l03_a_held_lock_is_a_bounded_refusal_not_a_hang(self):
        wa_id, tid = _recipient(self.engine)
        with mock.patch.object(G, "GATE_LOCK_TIMEOUT_MS", BOUND_MS, create=True), \
                lock_row_held(self.engine, wa_id):
            bg = Bg(lambda: attempt(self.engine, wa_id, tid))
            self.assert_refused_with_nothing_sendable(bg, wa_id, tid)
        self.assertLess(bg.elapsed, 5.0)

    def test_l04_a_held_uncommitted_insert_is_a_bounded_refusal_too(self):
        wa_id, tid = _recipient(self.engine)
        with mock.patch.object(G, "GATE_LOCK_TIMEOUT_MS", BOUND_MS, create=True), \
                lock_row_held(self.engine, wa_id, committed_row=False):
            bg = Bg(lambda: attempt(self.engine, wa_id, tid))
            self.assert_refused_with_nothing_sendable(bg, wa_id, tid)

    def test_l05_a_lock_timeout_never_proceeds_unlocked(self):
        """The fail-open: with any `lock_timeout` in force the failed FOR UPDATE used to be
        swallowed as SQLite's missing clause, and the gate went on to ALLOW with no lock."""
        engine = create_engine(URL, connect_args={"options": "-c lock_timeout=200"})
        self.addCleanup(engine.dispose)
        wa_id, tid = _recipient(self.engine)
        with mock.patch.object(G, "GATE_LOCK_TIMEOUT_MS", BOUND_MS, create=True), \
                lock_row_held(self.engine, wa_id):
            bg = Bg(lambda: attempt(engine, wa_id, tid))
            self.assert_refused_with_nothing_sendable(bg, wa_id, tid)

    def test_l06_a_lock_timeout_cannot_admit_a_duplicate(self):
        """The same fail-open, end to end: one gate inside the lock, a second that gives up
        waiting. Two ALLOWED for one message is the duplicate the lock exists to prevent."""
        engine = create_engine(URL, connect_args={"options": "-c lock_timeout=200"})
        self.addCleanup(engine.dispose)
        wa_id, tid = _recipient(self.engine)
        with self.engine.begin() as c:
            c.execute(text("INSERT INTO whatsapp_recipient_locks (wa_id, created_at) "
                           "VALUES (:w, now())"), {"w": wa_id})
        with mock.patch.object(G, "GATE_LOCK_TIMEOUT_MS", BOUND_MS, create=True), \
                first_gate_paused_inside(after_dedup=True) as (entered, resume):
            first = Bg(lambda: attempt(self.engine, wa_id, tid))
            self.assertTrue(entered.wait(JOIN))
            second = Bg(lambda: attempt(engine, wa_id, tid))
            self.assertTrue(second.done(), "the second attempt never gave up waiting")
            resume.set()
            self.assertTrue(first.done())
        self.assertEqual((first.error, second.error), (None, None))
        allowed = [x for x in (first, second) if x.value.outcome.value == "allowed"]
        self.assertEqual(len(allowed), 1, (first.value, second.value))

    def test_l07_a_caller_holding_the_thread_state_cannot_wedge_the_flood_path(self):
        """The gate's flood escalation writes the thread state from its own session. A caller
        holding that row while it waits for the gate is a wait PostgreSQL cannot see as a
        deadlock, and the recipient lock is held throughout: it must end, and release."""
        wa_id, tid = _recipient(self.engine)
        for n in range(G.FLOOD_MAX_MESSAGES):
            self.assertEqual(attempt(self.engine, wa_id, tid, f"m{n}").outcome.value, "allowed")
        caller = Session(self.engine)
        self.addCleanup(caller.close)
        caller.execute(text("UPDATE whatsapp_thread_states SET updated_at = now() "
                            "WHERE thread_id = :t"), {"t": tid})
        with mock.patch.object(G, "GATE_LOCK_TIMEOUT_MS", BOUND_MS, create=True):
            bg = Bg(lambda: OutboundSafetyGate(caller).attempt(
                wa_id=wa_id, thread_id=tid, text="m3", path_id=CE_TEXT))
            ended = bg.done()
            caller.rollback()
            self.assertTrue(ended, "the gate waited on its own caller without bound")
        self.assertIsNone(bg.error, repr(bg.error))
        self.assertEqual(bg.value.outcome.value, LOCK_REFUSED)
        self.assertEqual(attempt(self.engine, wa_id, tid, "m4").outcome.value, "blocked_flood")

    def test_l08_a_deadlock_victim_is_refused_with_nothing_sendable(self):
        """A true cycle: the gate holds the lock and needs the thread state; another
        transaction holds the state and wants the lock. PostgreSQL ends it; the gate reports
        the refusal, and only the short `deadlock_timeout` side can be chosen as the victim."""
        engine = create_engine(URL, connect_args={"options": "-c deadlock_timeout=100ms"})
        self.addCleanup(engine.dispose)
        wa_id, tid = _recipient(self.engine)
        for n in range(G.FLOOD_MAX_MESSAGES):
            self.assertEqual(attempt(self.engine, wa_id, tid, f"m{n}").outcome.value, "allowed")
        before = statuses(self.engine, tid)
        other = self.engine.connect()
        self.addCleanup(other.close)
        tx = other.begin()
        other.execute(text("SET LOCAL deadlock_timeout = '60s'"))
        other.execute(text("UPDATE whatsapp_thread_states SET updated_at = now() "
                           "WHERE thread_id = :t"), {"t": tid})
        with first_gate_paused_inside() as (entered, resume):
            gate = Bg(lambda: attempt(engine, wa_id, tid, "m3"))
            self.assertTrue(entered.wait(JOIN))
            rival = Bg(lambda: other.execute(text(
                "SELECT id FROM whatsapp_recipient_locks WHERE wa_id = :w FOR UPDATE"),
                {"w": wa_id}))
            self.assertTrue(waiting_on_lock(self.engine, "FOR UPDATE"))
            resume.set()
            self.assertTrue(gate.done(), "the deadlock was not resolved")
        self.assertTrue(rival.done())
        tx.rollback()
        self.assertIsNone(gate.error, repr(gate.error))
        self.assertEqual(gate.value.outcome.value, LOCK_REFUSED)
        self.assertEqual(statuses(self.engine, tid), before + ["blocked"])

    def test_l09_a_lock_row_removed_mid_acquisition_is_not_a_held_lock(self):
        """`SELECT ... FOR UPDATE` over zero rows succeeds and locks nothing. A reset deleting
        the row between the two statements must not let the gate proceed believing it holds."""
        engine = create_engine(URL)
        self.addCleanup(engine.dispose)
        wa_id, tid = _recipient(self.engine)
        with self.engine.begin() as c:
            c.execute(text("INSERT INTO whatsapp_recipient_locks (wa_id, created_at) "
                           "VALUES (:w, now())"), {"w": wa_id})

        @event.listens_for(engine, "before_cursor_execute")
        def remove_the_row(_conn, _cur, statement, _params, _ctx, _many):
            if statement.startswith("SELECT id FROM whatsapp_recipient_locks"):
                with self.engine.begin() as c:
                    c.execute(text("DELETE FROM whatsapp_recipient_locks WHERE wa_id = :w"),
                              {"w": wa_id})
        self.assertEqual(attempt(engine, wa_id, tid).outcome.value, LOCK_REFUSED)
        self.assertEqual(statuses(self.engine, tid), ["blocked"])

    def test_l10_a_gate_failure_inside_the_lock_releases_it_and_leaves_nothing(self):
        wa_id, tid = _recipient(self.engine)
        with mock.patch.object(OutboundSafetyGate, "_check_dedup",
                               side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                attempt(self.engine, wa_id, tid)
        self.assertEqual(statuses(self.engine, tid), [])
        self.assertEqual(dedups(self.engine, wa_id), [])
        with self.engine.connect() as c:
            self.assertEqual(c.execute(text(
                "SELECT count(*) FROM whatsapp_recipient_locks WHERE wa_id = :w"),
                {"w": wa_id}).scalar(), 0)
        self.assertEqual(attempt(self.engine, wa_id, tid).outcome.value, "allowed")

    def test_l11_the_bound_does_not_outlive_the_gate_transaction(self):
        """`SET LOCAL`: the bound dies with the gate's own transaction, and no caller or pooled
        connection inherits it."""
        engine = create_engine(URL, pool_size=1, max_overflow=0)
        self.addCleanup(engine.dispose)
        wa_id, tid = _recipient(self.engine)
        with engine.connect() as c:
            baseline = c.execute(text("SHOW lock_timeout")).scalar(), \
                c.execute(text("SHOW statement_timeout")).scalar()
        attempt(engine, wa_id, tid)
        with engine.connect() as c:
            self.assertEqual((c.execute(text("SHOW lock_timeout")).scalar(),
                              c.execute(text("SHOW statement_timeout")).scalar()), baseline)

    def test_l12_the_contended_refusal_reaches_callers_as_any_other_non_allowed(self):
        """Every caller treats `outcome != ALLOWED` as nothing sent: the refusal is one more
        such outcome, with a durable audit row and no dedup claim to block the next attempt."""
        wa_id, tid = _recipient(self.engine)
        with mock.patch.object(G, "GATE_LOCK_TIMEOUT_MS", BOUND_MS, create=True), \
                lock_row_held(self.engine, wa_id):
            bg = Bg(lambda: attempt(self.engine, wa_id, tid))
            self.assertTrue(bg.done(), "the gate was still waiting: the lock wait is unbounded")
        refused = bg.value
        self.assertEqual(refused.outcome.value, LOCK_REFUSED)
        self.assertIn("RECIPIENT_LOCK", refused.blocked_reason or "")
        self.assertIsNotNone(refused.message_id)
        self.assertEqual(attempt(self.engine, wa_id, tid).outcome.value, "allowed")


    def test_l13_the_refusals_own_audit_cannot_become_a_second_hang(self):
        """The audit row's FK needs the thread row. A transaction holding that row FOR UPDATE
        must cost the refusal its audit, not its bound."""
        wa_id, tid = _recipient(self.engine)
        holder = self.engine.connect()
        self.addCleanup(holder.close)
        tx = holder.begin()
        holder.execute(text("SELECT id FROM whatsapp_threads WHERE id = :t FOR UPDATE"), {"t": tid})
        with mock.patch.object(G, "GATE_LOCK_TIMEOUT_MS", BOUND_MS, create=True), \
                lock_row_held(self.engine, wa_id):
            bg = Bg(lambda: attempt(self.engine, wa_id, tid))
            ended = bg.done()
        tx.rollback()
        self.assertTrue(ended, "the refusal's audit waited without bound")
        self.assertIsNone(bg.error, repr(bg.error))
        self.assertEqual(bg.value.outcome.value, LOCK_REFUSED)
        self.assertIsNone(bg.value.message_id)
        self.assertEqual(statuses(self.engine, tid), [])
        self.assertEqual(dedups(self.engine, wa_id), [])


    def test_l14_the_statement_bound_is_a_refusal_too(self):
        """`query_canceled`: the second, independent bound. The lock bound is set out of reach
        so that only `statement_timeout` can end the wait."""
        wa_id, tid = _recipient(self.engine)
        with mock.patch.object(G, "GATE_LOCK_TIMEOUT_MS", 60000, create=True), \
                mock.patch.object(G, "GATE_STATEMENT_TIMEOUT_MARGIN_MS", BOUND_MS - 60000,
                                  create=True), \
                lock_row_held(self.engine, wa_id):
            bg = Bg(lambda: attempt(self.engine, wa_id, tid))
            self.assert_refused_with_nothing_sendable(bg, wa_id, tid)

    def test_l15_postgresql_applies_the_reviewed_bounds_inside_the_gate(self):
        """Nothing patched: what the server itself reports, read from the gate's own
        transaction while it holds the lock. It catches a wrong relationship between the two
        constants as well as a wrong constant."""
        wa_id, tid = _recipient(self.engine)
        seen = {}
        original = OutboundSafetyGate._check_flood

        def read_bounds(gate, db, **kw):
            seen.update(tuple(r) for r in db.execute(text(
                "SELECT name, setting FROM pg_settings "
                "WHERE name IN ('lock_timeout', 'statement_timeout')")))
            return original(gate, db, **kw)
        with mock.patch.object(OutboundSafetyGate, "_check_flood", read_bounds):
            result = attempt(self.engine, wa_id, tid)
        self.assertEqual(result.outcome.value, "allowed", result)
        self.assertEqual(seen, {"lock_timeout": "4000", "statement_timeout": "10000"})


class OwnWamid(Template):
    """`Template` numbers its wamids from 1 per instance and `wa_message_id` is unique, so a
    later test's `mark_sent` would fail on the fake's number, not on anything under test."""

    def __call__(self, **kw):
        wamid, status = super().__call__(**kw)
        return f"{wamid}.{next(_serial)}", status


def _slots():
    """Candidate slots, each tried once: the agenda decides which of them is bookable."""
    tue = H.slot_day()
    days = [tue + timedelta(days=k) for k in (-1, 0, 1, 2, 3, 7, 8, 9, 10)]
    return iter([(d, time(h, m)) for d in days for h in range(9, 18) for m in (0, 30)])


@unittest.skipUnless(URL, "P1_LOCK_PG_URL not set — no disposable PostgreSQL")
class N_OperatorNotice(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(URL, pool_size=12)
        cls.slots = _slots()
        set_floor(cls.engine)     # a schema-only restore has none, and then nothing is enqueued

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        self.dest = _destination()
        self.cfg = op_settings(operator_whatsapp_to=self.dest)
        self._settle_earlier_tests()
        for day, at in self.slots:
            try:
                self.w = book(self.engine, day=day, at=at)
                break
            except BookingSlotConflictError:
                continue
        with Session(self.engine) as db:
            self.led = db.execute(select(BookingCommandRecord).where(
                BookingCommandRecord.thread_id == self.w.thread_id)).scalar_one()
        self.t0 = datetime.now(timezone.utc)

    def _settle_earlier_tests(self):
        """No earlier test's booking may share this test's pass or its recorded sends."""
        db = H.session_factory(self.engine)()
        try:
            with notifier(self.cfg):
                ON.enqueue_committed_bookings(db)
        finally:
            db.close()
        with self.engine.begin() as c:
            c.execute(text("UPDATE operator_notifications SET status = 'HELD', claim_token = NULL,"
                           " lease_expires_at = NULL WHERE status IN ('PENDING', 'SENDING')"))

    def run_pass(self, now=None):
        db = H.session_factory(self.engine)()
        try:
            return ON.run_once(db, now=now or self.t0)
        finally:
            db.close()

    def notice(self):
        with Session(self.engine) as db:
            return db.execute(select(OperatorNotification).where(
                OperatorNotification.booking_command_id == self.led.id)).scalar_one()

    def mine(self, tpl):
        return [s for s in tpl.sent if f"Reserva {self.led.id} " in s["params"][8]]

    def gate_rows(self):
        with Session(self.engine) as db:
            return db.execute(
                select(WhatsAppMessage.status)
                .join(WhatsAppThread, WhatsAppThread.id == WhatsAppMessage.thread_id)
                .join(WhatsAppContact, WhatsAppContact.id == WhatsAppThread.contact_id)
                .where(WhatsAppContact.wa_id == self.dest, WhatsAppMessage.path_id == OPERATOR)
                .order_by(WhatsAppMessage.id)).scalars().all()

    def set_estado(self, estado):
        with self.engine.begin() as c:
            c.execute(text("UPDATE revisions SET estado_revision = :e WHERE id = :r"),
                      {"e": estado, "r": self.led.revision_id})

    # ── E.2 ──────────────────────────────────────────────────────────────────
    def test_n01_concurrent_passes_deliver_one_notice(self):
        barrier = threading.Barrier(4)

        def one_pass():
            barrier.wait(JOIN)
            return self.run_pass()
        with notifier(self.cfg) as tpl:
            passes = [Bg(one_pass) for _ in range(4)]
            self.assertTrue(all(p.done() for p in passes))
        self.assertEqual([p.error for p in passes], [None] * 4)
        self.assertEqual(len(self.mine(tpl)), 1)
        n = self.notice()
        self.assertEqual((n.status, n.attempts), ("SENT", 1))
        self.assertEqual(self.gate_rows(), ["sent"])

    def test_n02_a_second_claim_inside_the_first_send_gets_nothing(self):
        """Deterministic: the first sender is inside the gate, holding the recipient lock, and
        the notice is SENDING. A rival pass finds nothing to claim for this booking."""
        with notifier(self.cfg) as tpl, first_gate_paused_inside() as (entered, resume):
            first = Bg(self.run_pass)
            self.assertTrue(entered.wait(JOIN))
            self.assertEqual(self.notice().status, "SENDING")
            db = H.session_factory(self.engine)()
            self.addCleanup(db.close)
            claims = []
            while (c := ON.claim_next(db, now=self.t0)) is not None:   # other tests' leftovers
                claims.append(c)
            self.assertNotIn(self.led.id, [c.booking_command_id for c in claims])
            resume.set()
            self.assertTrue(first.done())
        self.assertEqual(len(self.mine(tpl)), 1)
        self.assertEqual(self.notice().status, "SENT")

    def test_n03_the_operator_path_is_serialised_by_the_recipient_lock(self):
        """The notice's own gate crossing takes the lock: a rival send to the operator waits."""
        wa_id, tid = _recipient(self.engine, self.dest)
        identity = "a" * 64
        with notifier(self.cfg), first_gate_paused_inside() as (entered, resume):
            first = Bg(lambda: attempt(self.engine, wa_id, tid, path=OPERATOR,
                                       message_type="template", delivery_identity=identity))
            self.assertTrue(entered.wait(JOIN))
            second = Bg(lambda: attempt(self.engine, wa_id, tid, path=OPERATOR,
                                        message_type="template", delivery_identity=identity))
            self.assertTrue(waiting_on_lock(self.engine, "whatsapp_recipient_locks"))
            resume.set()
            self.assertTrue(first.done() and second.done())
        self.assertEqual(sorted(x.value.outcome.value for x in (first, second)),
                         ["allowed", "blocked_duplicate"])

    def test_n04_the_operator_path_reaches_no_other_recipient(self):
        wa_id, tid = _recipient(self.engine)
        with notifier(self.cfg):
            r = attempt(self.engine, wa_id, tid, path=OPERATOR, message_type="template")
        self.assertEqual(r.outcome.value, "blocked_unauthorized_path")
        self.assertEqual(dedups(self.engine, wa_id), [])

    # ── E.3 ──────────────────────────────────────────────────────────────────
    def test_n05_a_retry_keeps_the_recipient_of_the_first_attempt(self):
        with notifier(self.cfg, Template("4xx", "ok")) as tpl:
            self.run_pass()
            first_hash = self.notice().destination_hash
            self.run_pass(self.t0 + timedelta(seconds=ON.RETRY_AFTER_SECONDS + 1))
        self.assertEqual([s["to"] for s in self.mine(tpl)], [self.dest, self.dest])
        n = self.notice()
        self.assertEqual((n.status, n.attempts), ("SENT", 2))
        self.assertEqual(n.destination_hash, first_hash)

    def test_n06_a_retry_cannot_be_redirected_by_a_changed_destination(self):
        other = _destination()
        with notifier(self.cfg, Template("4xx")) as tpl:
            self.run_pass()
        first_hash = self.notice().destination_hash
        self.assertEqual(self.notice().status, "PENDING")
        with notifier(op_settings(operator_whatsapp_to=other)) as moved:
            self.run_pass(self.t0 + timedelta(seconds=ON.RETRY_AFTER_SECONDS + 1))
            self.run_pass(self.t0 + timedelta(hours=1))
        self.assertEqual(self.mine(moved), [], "the retry went to a different recipient")
        n = self.notice()
        self.assertEqual((n.status, n.attempts, n.last_error),
                         ("HELD", 2, "RETRY_DESTINATION_CHANGED"))
        self.assertEqual(n.destination_hash, first_hash)      # the original decision survives
        self.assertEqual(len(self.mine(tpl)), 1)

    def test_n06b_a_retry_with_no_recorded_decision_is_held_not_guessed(self):
        """Fail closed: a retry that cannot show its first attempt's recipient does not send."""
        with notifier(self.cfg, Template("4xx")):
            self.run_pass()
        with self.engine.begin() as c:
            c.execute(text("UPDATE operator_notifications SET destination_hash = NULL "
                           "WHERE booking_command_id = :b"), {"b": self.led.id})
        with notifier(self.cfg) as tpl:
            self.run_pass(self.t0 + timedelta(seconds=ON.RETRY_AFTER_SECONDS + 1))
        self.assertEqual(self.mine(tpl), [])
        n = self.notice()
        self.assertEqual((n.status, n.last_error), ("HELD", "RETRY_DESTINATION_CHANGED"))

    # ── E.4 ──────────────────────────────────────────────────────────────────
    def _never_sent(self, estado):
        self.set_estado(estado)
        with notifier(self.cfg) as tpl:
            self.run_pass()
            self.run_pass(self.t0 + timedelta(hours=1))
        self.assertEqual(self.mine(tpl), [])
        n = self.notice()
        self.assertEqual((n.status, n.last_error), ("HELD", "BOOKING_NOT_OCCUPYING"))
        self.assertEqual(self.gate_rows(), [])

    def test_n07_a_cancelled_booking_is_never_sendable(self):
        self._never_sent("CANCELADO")

    def test_n08_a_booking_moved_off_the_agenda_is_never_sendable(self):
        self._never_sent("REPROGRAMAR")

    def test_n09_cancelled_between_attempts_the_retry_is_held(self):
        with notifier(self.cfg, Template("4xx", "ok")) as tpl:
            self.run_pass()
            self.set_estado("CANCELADO")
            self.run_pass(self.t0 + timedelta(seconds=ON.RETRY_AFTER_SECONDS + 1))
        self.assertEqual(len(self.mine(tpl)), 1)              # only the rejected first attempt
        n = self.notice()
        self.assertEqual((n.status, n.last_error), ("HELD", "BOOKING_NOT_OCCUPYING"))

    def test_n10_restoring_a_cancelled_booking_does_not_revive_a_held_notice(self):
        self._never_sent("CANCELADO")
        self.set_estado("PENDIENTE")
        with notifier(self.cfg) as tpl:
            self.run_pass(self.t0 + timedelta(hours=2))
        self.assertEqual(self.mine(tpl), [])
        self.assertEqual(self.notice().status, "HELD")

    # ── E.6 ──────────────────────────────────────────────────────────────────
    def test_n11_a_gate_commit_failure_leaves_no_sendable_row(self):
        """The failure lands after the gate has flushed its pending row and dedup claim, at the
        commit that would make them durable."""
        original = Session.commit

        def failing(session):
            if any(isinstance(o, WhatsAppMessage) and o.status == "pending"
                   and o.path_id == OPERATOR for o in session.identity_map.values()):
                session.rollback()
                raise RuntimeError("injected commit failure")
            return original(session)
        with notifier(self.cfg) as tpl, mock.patch.object(Session, "commit", failing):
            self.run_pass()
        self.assertEqual(self.mine(tpl), [])
        self.assertEqual(self.gate_rows(), [])
        self.assertEqual(dedups(self.engine, self.dest), [])
        n = self.notice()
        self.assertEqual((n.status, n.attempts, n.last_error), ("PENDING", 1, "PRE_SEND_FAILURE"))

    def test_n12_a_sender_lost_after_its_claim_is_held_never_reclaimed(self):
        with notifier(self.cfg) as tpl:
            ON.enqueue_committed_bookings(H.session_factory(self.engine)(), now=self.t0)
            db = H.session_factory(self.engine)()
            self.addCleanup(db.close)
            claims = []
            while (c := ON.claim_next(db, now=self.t0)) is not None:
                claims.append(c)
            self.assertIn(self.led.id, [c.booking_command_id for c in claims])
            self.run_pass(self.t0 + timedelta(seconds=ON.LEASE_SECONDS + 1))
        self.assertEqual(self.mine(tpl), [])
        n = self.notice()
        self.assertEqual((n.status, n.last_error), ("HELD", "UNCERTAIN_LOST_SENDER"))

    def test_n13_a_contended_lock_is_retryable_and_never_a_send(self):
        with mock.patch.object(G, "GATE_LOCK_TIMEOUT_MS", BOUND_MS, create=True), \
                notifier(self.cfg, Template("ok")) as tpl, \
                lock_row_held(self.engine, self.dest):
            bg = Bg(self.run_pass)
            self.assertTrue(bg.done(), "the notice's pass waited without bound")
        self.assertEqual(self.mine(tpl), [])
        n = self.notice()
        self.assertEqual((n.status, n.attempts, n.last_error),
                         ("PENDING", 1, "GATE_blocked_recipient_lock"))
        with notifier(self.cfg, Template("ok")) as tpl:
            self.run_pass(self.t0 + timedelta(seconds=ON.RETRY_AFTER_SECONDS + 1))
        self.assertEqual(len(self.mine(tpl)), 1)
        self.assertEqual((self.notice().status, self.notice().attempts), ("SENT", 2))

    # ── E.7 ──────────────────────────────────────────────────────────────────
    def test_n14_outbound_off_is_no_delivery(self):
        with notifier(self.cfg, outbound="false") as tpl:
            self.run_pass()
        self.assertEqual(self.mine(tpl), [])
        self.assertEqual(self.notice().last_error, "GATE_blocked_kill_switch")
        self.assertEqual(self.gate_rows(), ["blocked"])

    def test_n15_outbound_unset_is_no_delivery(self):
        with notifier(self.cfg, outbound=None) as tpl:
            self.run_pass()
        self.assertEqual(self.mine(tpl), [])
        self.assertEqual(self.gate_rows(), ["blocked"])

    def test_n16_the_notice_switched_off_is_no_delivery_and_no_gate_crossing(self):
        off = op_settings(operator_whatsapp_to=self.dest, operator_whatsapp_notify_enabled=False)
        with notifier(off) as tpl:
            self.run_pass()
        self.assertEqual(self.mine(tpl), [])
        n = self.notice()
        self.assertEqual((n.status, n.last_error), ("HELD", "NOT_CONFIGURED_DISABLED"))
        self.assertEqual(self.gate_rows(), [])

    # ── the identity is per attempt, and at most one crossing each ───────────
    def test_n17_each_attempt_identity_crosses_the_gate_once(self):
        with notifier(self.cfg, Template("4xx", "ok")):
            self.run_pass()
            self.run_pass(self.t0 + timedelta(seconds=ON.RETRY_AFTER_SECONDS + 1))
        n = self.notice()
        fps = [ON.attempt_fingerprint(n.identity, k) for k in (1, 2)]
        with Session(self.engine) as db:
            seen = db.execute(select(WhatsAppOutboundDedup.content_fingerprint).where(
                WhatsAppOutboundDedup.wa_id == self.dest)).scalars().all()
        self.assertEqual(sorted(seen), sorted(fps))

    # ── the conditional claim, in its exact window ───────────────────────────
    def test_n18_a_rival_claim_after_the_select_leaves_the_loser_with_nothing(self):
        """Claimant A has selected the notice. Immediately before A's conditional UPDATE goes
        to PostgreSQL, claimant B claims the same row and commits. Nothing is timed: a cursor
        hook runs B to completion, on A's own thread, at that statement. Only the UPDATE's own
        status condition can then stop A; N01 reaches this window only by chance."""
        rival_engine = create_engine(URL, pool_size=1,
                                     connect_args={"options": "-c lock_timeout=5000"})
        self.addCleanup(rival_engine.dispose)
        a_db, b_db = H.session_factory(self.engine)(), H.session_factory(rival_engine)()
        self.addCleanup(a_db.close)
        self.addCleanup(b_db.close)
        window = {}

        def rival_claims_first(conn, _cursor, statement, *_rest):
            if statement.startswith("UPDATE operator_notifications") and "rival" not in window:
                window["a_connection"] = conn
                window["rival"] = ON.claim_next(b_db, now=self.t0)

        def note_a_rowcount(conn, cursor, statement, *_rest):
            if conn is window.get("a_connection") and statement.startswith("UPDATE"):
                window["a_rowcount"] = cursor.rowcount
        event.listen(self.engine, "before_cursor_execute", rival_claims_first)
        self.addCleanup(event.remove, self.engine, "before_cursor_execute", rival_claims_first)
        event.listen(self.engine, "after_cursor_execute", note_a_rowcount)
        self.addCleanup(event.remove, self.engine, "after_cursor_execute", note_a_rowcount)

        with notifier(self.cfg, OwnWamid()) as tpl:
            ON.enqueue_committed_bookings(a_db, now=self.t0)
            before = self.notice()
            self.assertEqual((before.status, before.attempts), ("PENDING", 0))

            loser = ON.claim_next(a_db, now=self.t0)

            rival = window.get("rival")
            self.assertIsNotNone(rival, "the rival never ran inside A's window")
            self.assertEqual((rival.booking_command_id, rival.attempt), (self.led.id, 1))
            self.assertIsNone(loser, "A claimed a row the rival already held")
            self.assertEqual(window.get("a_rowcount"), 0, "A's UPDATE matched the rival's row")
            n = self.notice()      # scalar_one: exactly one row for this booking
            self.assertEqual((n.status, n.attempts, n.claim_token),
                             ("SENDING", 1, rival.token))
            self.assertEqual((self.mine(tpl), self.gate_rows()), ([], []))

            # The only delivery obligation is the rival's: it completes once, and nothing is left.
            config = ON.operator_config()
            ON.record(b_db, rival, ON.send_claimed(b_db, rival, config), config, now=self.t0)
            self.run_pass()
        self.assertEqual(len(self.mine(tpl)), 1)
        n = self.notice()
        self.assertEqual((n.status, n.attempts), ("SENT", 1))
        self.assertEqual(self.gate_rows(), ["sent"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
