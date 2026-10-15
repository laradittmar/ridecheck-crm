"""SB2A-R4 — PostgreSQL evidence: narrowed lock scope, lock order, cross-professional
concurrency, the transaction write audit, concurrent identities and the read-only attention
query. Disposable database only (R4_PG_URL); skips cleanly without it.

Waiting is MEASURED (monotonic time while a holder transaction is parked) and row locks are
OBSERVED with pgrowlocks — concurrency is never inferred from final rows alone.
"""
from __future__ import annotations

import os
import secrets
import sys
import threading
import time as _t
import unittest
from datetime import date, datetime, time, timedelta, timezone
from unittest import mock

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

PG_URL = os.environ.get("R4_PG_URL", "")
PARK = 1.5          # seconds a holder transaction stays open
FAST = 0.6          # a contender finishing faster than this did not wait for the holder


def _head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(Config(os.path.join(_BACKEND, "alembic.ini"))).get_current_head()


@unittest.skipUnless(PG_URL, "R4_PG_URL not set — no disposable PostgreSQL; nothing proven")
class R4Postgres(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_URL, pool_size=20, max_overflow=5)
        with cls.engine.begin() as c:
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == _head()
            c.execute(text("CREATE EXTENSION IF NOT EXISTS pgrowlocks"))

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        from app.services import booking_authority as ba
        self.ba = ba
        self.day = date(2033, 1, 3) + timedelta(days=7 * (abs(hash(self.id())) % 400))
        with self.engine.begin() as c:
            self.lead = c.execute(text("INSERT INTO leads (created_at, estado, necesita_humano) "
                                       "VALUES (now(), 'R4', false) RETURNING id")).scalar()
            self.lead2 = c.execute(text("INSERT INTO leads (created_at, estado, necesita_humano) "
                                        "VALUES (now(), 'R4-OTHER', false) RETURNING id")).scalar()
            for key, dflt in (("r4.default", True), ("r4.second", False)):
                if not c.execute(text("SELECT count(*) FROM profesionales WHERE resource_key=:k"), {"k": key}).scalar():
                    if dflt:
                        c.execute(text("UPDATE profesionales SET is_launch_default=false"))
                    c.execute(text("INSERT INTO profesionales (nombre, apellido, email, created_at, resource_key, "
                                   "is_launch_default, is_active) VALUES ('Fx','R4','f@example.invalid',now(),:k,:d,true)"),
                              {"k": key, "d": dflt})
            self.p1 = c.execute(text("SELECT id FROM profesionales WHERE resource_key='r4.default'")).scalar()
            self.p2 = c.execute(text("SELECT id FROM profesionales WHERE resource_key='r4.second'")).scalar()

    def tearDown(self):
        with self.engine.begin() as c:
            leads = {"a": self.lead, "b": self.lead2}
            c.execute(text("DELETE FROM booking_commands WHERE lead_id IN (:a, :b)"), leads)
            c.execute(text("DELETE FROM thread_revisions WHERE thread_id IN (SELECT id FROM whatsapp_threads "
                           "WHERE lead_id IN (:a, :b))"), leads)
            c.execute(text("DELETE FROM whatsapp_threads WHERE lead_id IN (:a, :b)"), leads)
            c.execute(text("DELETE FROM revisions WHERE lead_id IN (:a, :b)"), leads)
            c.execute(text("DELETE FROM leads WHERE id IN (:a, :b)"), leads)

    # ── helpers ───────────────────────────────────────────────────────────

    def cmd(self, at=time(10), *, prof=None, op="CREATE", rev=None, day=None, lead=None, key=None, **kw):
        B = self.ba
        d = day or self.day
        if op in ("CANCEL",):
            return B.BookingCommand(operation=B.BookingOperation(op), source=B.BookingSource.OTHER,
                                    revision_id=rev, idempotency_key=key, **kw)
        return B.BookingCommand(operation=B.BookingOperation(op), source=B.BookingSource.OTHER,
                                lead_id=lead or self.lead, revision_id=rev, profesional_id=prof,
                                requested_date=d, requested_time=at, idempotency_key=key, **kw)

    def book(self, c, *, auth=None, lt=5000, **kw):
        with Session(self.engine) as s:
            return (auth or self.ba.BookingAuthority)(s, lock_timeout_ms=lt).execute(c, **kw)

    def parked_holder(self, c, **kw):
        """Start `c` in a thread, park it just before its commit (all its locks held)."""
        parked, release, out = threading.Event(), threading.Event(), {}
        base = self.ba.BookingAuthority

        class P(base):
            def _verify_before_commit(inner, *a, **k):
                out["pid"] = inner.db.execute(text("SELECT pg_backend_pid()")).scalar()
                parked.set(); release.wait(15)
                return base._verify_before_commit(inner, *a, **k)

        th = threading.Thread(target=lambda: out.setdefault("result", self.book(c, auth=P, **kw)))
        th.start()
        self.assertTrue(parked.wait(10), "holder never parked")
        return out, release, th

    def timed(self, fn):
        t0 = _t.monotonic()
        r = fn()
        return r, _t.monotonic() - t0

    def locked_rows(self, table, pid):
        with self.engine.connect() as c:
            return sorted(r[0] for r in c.execute(text(
                f"SELECT t.id FROM pgrowlocks('{table}') l JOIN {table} t ON t.ctid = l.locked_row "
                "WHERE :p = ANY(l.pids)"), {"p": pid}).all())

    def q(self, sql, **p):
        with self.engine.connect() as c:
            return c.execute(text(sql), p).all()

    # ══ 1. lock scope, observed ════════════════════════════════════════════

    def test_r4pg_01_holder_row_locks_are_only_its_own(self):
        other_p2 = self.book(self.cmd(time(9), prof=self.p2))
        other_p1 = self.book(self.cmd(time(15)))
        nonocc = self.book(self.cmd(None, op="REQUEST_MANUAL_SCHEDULING"))
        target = self.book(self.cmd(time(12)))
        out, release, th = self.parked_holder(self.cmd(time(13), op="RESCHEDULE", rev=target.revision_id))
        locked = self.locked_rows("revisions", out["pid"])
        release.set(); th.join(15)
        self.assertEqual(out["result"].outcome.value, "COMMITTED", out["result"].reason)
        self.assertEqual(locked, [target.revision_id])             # L3 only — nothing else
        self.assertNotIn(other_p2.revision_id, locked)
        self.assertNotIn(other_p1.revision_id, locked)
        self.assertNotIn(nonocc.revision_id, locked)

    def test_r4pg_02_create_locks_no_existing_row(self):
        self.book(self.cmd(time(9), prof=self.p2)); self.book(self.cmd(time(15)))
        out, release, th = self.parked_holder(self.cmd(time(12)))
        locked = self.locked_rows("revisions", out["pid"])
        trs = self.locked_rows("thread_revisions", out["pid"])
        release.set(); th.join(15)
        self.assertEqual((locked, trs), ([], []))

    # ══ 2. two-session races, waiting measured ═════════════════════════════

    def test_r4pg_03_same_professional_overlap_one_wins_one_loses_safely(self):
        out, release, th = self.parked_holder(self.cmd(time(10)))
        res = {}
        tc = threading.Thread(target=lambda: res.setdefault("c", self.timed(lambda: self.book(self.cmd(time(10, 30))))))
        tc.start(); _t.sleep(PARK); alive = tc.is_alive(); release.set(); th.join(15); tc.join(15)
        r, waited = res["c"]
        self.assertTrue(alive, "the contender must wait for the holder")
        self.assertGreaterEqual(waited, PARK)
        self.assertEqual((out["result"].outcome.value, r.outcome.value, r.reason),
                         ("COMMITTED", "SLOT_CONFLICT", "OVERLAP"))
        self.assertEqual(self.q("SELECT count(*) FROM revisions WHERE lead_id=:l", l=self.lead)[0][0], 1)

    def test_r4pg_04_same_professional_same_day_non_overlapping_serializes_then_both_commit(self):
        """By design: the (professional, date) lock serializes one inspector's day, because
        availability revalidation depends on travel from that day's OTHER appointments, not
        only on overlapping ones. The wait is bounded by the holder's transaction."""
        out, release, th = self.parked_holder(self.cmd(time(10)))
        res = {}
        tc = threading.Thread(target=lambda: res.setdefault("c", self.timed(lambda: self.book(self.cmd(time(15))))))
        tc.start(); _t.sleep(PARK); release.set(); th.join(15); tc.join(15)
        r, waited = res["c"]
        self.assertEqual((out["result"].outcome.value, r.outcome.value), ("COMMITTED", "COMMITTED"))
        self.assertLess(waited, PARK + 3)
        other_day = self.book(self.cmd(time(10), day=self.day + timedelta(days=1)))
        self.assertEqual(other_day.outcome.value, "COMMITTED")

    def test_r4pg_05_different_professionals_same_interval_do_not_wait(self):
        out, release, th = self.parked_holder(self.cmd(time(10)))
        r, waited = self.timed(lambda: self.book(self.cmd(time(10), prof=self.p2)))
        release.set(); th.join(15)
        self.assertEqual((r.outcome.value, out["result"].outcome.value), ("COMMITTED", "COMMITTED"))
        self.assertLess(waited, FAST)

    def test_r4pg_06_unrelated_cancel_lead_edit_and_non_occupying_rows_are_not_blocked(self):
        p2 = self.book(self.cmd(time(10), prof=self.p2))          # SAME interval, other professional
        nonocc = self.book(self.cmd(time(10), op="REQUEST_MANUAL_SCHEDULING"))   # overlaps, non-occupying
        out, release, th = self.parked_holder(self.cmd(time(10)))
        cancel, t_cancel = self.timed(lambda: self.book(self.cmd(op="CANCEL", rev=p2.revision_id), lt=500))

        def edit(sql, **p):
            with self.engine.begin() as c:
                c.execute(text("SET LOCAL lock_timeout = '500ms'"))
                c.execute(text(sql), p)
        _, t_lead = self.timed(lambda: edit("UPDATE leads SET estado='EDITED' WHERE id=:i", i=self.lead2))
        _, t_nonocc = self.timed(lambda: edit("UPDATE revisions SET marca='edit' WHERE id=:i", i=nonocc.revision_id))
        release.set(); th.join(15)
        self.assertEqual((cancel.outcome.value, out["result"].outcome.value), ("COMMITTED", "COMMITTED"))
        self.assertLess(max(t_cancel, t_lead, t_nonocc), FAST, (t_cancel, t_lead, t_nonocc))

    def test_r4pg_07_crossed_reschedules_different_professionals_no_deadlock(self):
        d = self.day + timedelta(days=2)
        a = self.book(self.cmd(time(9), day=d)); b = self.book(self.cmd(time(9), day=d, prof=self.p2))
        bar = threading.Barrier(2); res = {}
        base = self.ba.BookingAuthority

        class Sync(base):
            def _lock_revision(inner, *ar, **k):
                bar.wait(10); _t.sleep(0.2)
                return base._lock_revision(inner, *ar, **k)

        def go(name, rid, prof):
            res[name] = self.timed(lambda: self.book(self.cmd(time(11), day=d, op="RESCHEDULE", rev=rid, prof=prof),
                                                     auth=Sync))
        ts = [threading.Thread(target=go, args=("a", a.revision_id, self.p1)),
              threading.Thread(target=go, args=("b", b.revision_id, self.p2))]
        [t.start() for t in ts]; [t.join(30) for t in ts]
        self.assertEqual({k: v[0].outcome.value for k, v in res.items()}, {"a": "COMMITTED", "b": "COMMITTED"})
        self.assertLess(max(v[1] for v in res.values()), 2.0)

    def test_r4pg_08_crossed_same_professional_reschedules_serialize_without_deadlock(self):
        d = self.day + timedelta(days=3)
        a = self.book(self.cmd(time(9), day=d)); b = self.book(self.cmd(time(13), day=d))
        bar = threading.Barrier(2); res = {}
        base = self.ba.BookingAuthority

        class Sync(base):
            def _acquire_locks(inner, keys):
                bar.wait(10)
                return base._acquire_locks(inner, keys)

        def go(name, rid, at):
            res[name] = self.timed(lambda: self.book(self.cmd(at, day=d, op="RESCHEDULE", rev=rid), auth=Sync))
        ts = [threading.Thread(target=go, args=("a", a.revision_id, time(15))),
              threading.Thread(target=go, args=("b", b.revision_id, time(11)))]
        [t.start() for t in ts]; [t.join(30) for t in ts]
        reasons = {k: (v[0].outcome.value, v[0].reason) for k, v in res.items()}
        self.assertTrue(all(o in ("COMMITTED", "RETRYABLE_FAILURE", "STALE_VERSION") for o, _ in reasons.values()), reasons)
        self.assertNotIn("DB_40P01", [r for _, r in reasons.values()], reasons)
        self.assertLess(max(v[1] for v in res.values()), 5.0)

    def test_r4pg_09_genuine_lock_conflict_is_retryable_and_leaves_nothing(self):
        out, release, th = self.parked_holder(self.cmd(time(10)))
        r = self.book(self.cmd(time(16), key=f"lt-{self.lead}"), lt=300)
        release.set(); th.join(15)
        self.assertEqual((r.outcome.value, r.reason, r.retryable), ("RETRYABLE_FAILURE", "LOCK_TIMEOUT", True))
        self.assertEqual(self.q("SELECT count(*) FROM booking_commands WHERE idempotency_key=:k",
                                k=f"lt-{self.lead}")[0][0], 0)
        self.assertEqual(self.q("SELECT count(*) FROM revisions WHERE lead_id=:l AND turno_hora='16:00'",
                                l=self.lead)[0][0], 0)

    def test_r4pg_10_deadlock_victim_leaves_no_partial_mutation(self):
        bar = threading.Barrier(2); res = {}
        base = self.ba.BookingAuthority

        def make(first, second):
            class X(base):
                def _apply_attachments(inner, *a, **k):
                    out = base._apply_attachments(inner, *a, **k)
                    inner.db.execute(text("UPDATE leads SET estado='DL' WHERE id=:i"), {"i": first})
                    bar.wait(10); _t.sleep(0.3)
                    inner.db.execute(text("UPDATE leads SET estado='DL' WHERE id=:i"), {"i": second})
                    return out
            return X
        plan = self.ba.BookingAttachmentPlan()
        ta = threading.Thread(target=lambda: res.setdefault("a", self.book(
            self.cmd(time(10), key=f"dla-{self.lead}"), auth=make(self.lead, self.lead2), attachments=plan, lt=10000)))
        tb = threading.Thread(target=lambda: res.setdefault("b", self.book(
            self.cmd(time(10), prof=self.p2, key=f"dlb-{self.lead}"), auth=make(self.lead2, self.lead),
            attachments=plan, lt=10000)))
        ta.start(); tb.start(); ta.join(30); tb.join(30)
        out = sorted((r.outcome.value, r.reason) for r in res.values())
        self.assertEqual(out, [("COMMITTED", "COMMITTED"), ("RETRYABLE_FAILURE", "DB_40P01")])
        loser = next(k for k, r in res.items() if r.outcome.value != "COMMITTED")
        self.assertEqual(self.q("SELECT count(*) FROM booking_commands WHERE idempotency_key=:k",
                                k=f"dl{loser}-{self.lead}")[0][0], 0)
        self.assertEqual(self.q("SELECT count(*) FROM revisions WHERE lead_id=:l", l=self.lead)[0][0], 1)

    def test_r4pg_11_change_between_unlocked_read_and_row_lock(self):
        target = self.book(self.cmd(time(10)))
        hold, release, res = threading.Event(), threading.Event(), {}
        base = self.ba.BookingAuthority

        class Slow(base):
            def _acquire_locks(inner, keys):
                hold.set(); release.wait(10)
                return base._acquire_locks(inner, keys)
        for name, ver in (("versioned", 0), ("unversioned", None)):
            hold.clear(); release.clear()
            current = self.q("SELECT booking_version FROM revisions WHERE id=:i", i=target.revision_id)[0][0]
            c = self.cmd(time(12), op="RESCHEDULE", rev=target.revision_id,
                         expected_version=current if ver is not None else None)
            th = threading.Thread(target=lambda c=c, n=name: res.setdefault(n, self.book(c, auth=Slow)))
            th.start(); self.assertTrue(hold.wait(10))
            with self.engine.begin() as conn:      # a direct writer changes the row meanwhile
                conn.execute(text("UPDATE revisions SET turno_hora = turno_hora + interval '15 minutes' "
                                  "WHERE id=:i"), {"i": target.revision_id})
            release.set(); th.join(15)
        self.assertEqual((res["versioned"].outcome.value, res["versioned"].reason), ("STALE_VERSION", "VERSION_MISMATCH"))
        self.assertEqual((res["unversioned"].outcome.value, res["unversioned"].reason),
                         ("RETRYABLE_FAILURE", "CONCURRENT_CHANGE"))

    # ══ 3. transaction write audit (no locks needed) ═══════════════════════

    def test_r4pg_12_write_audit_catches_writes_on_any_day(self):
        far = self.book(self.cmd(time(9), day=self.day + timedelta(days=30)))
        tid = self._thread()
        with self.engine.begin() as c:
            far_tr = c.execute(text("INSERT INTO thread_revisions (thread_id, status, scheduled_date, scheduled_time, "
                                    "created_at) VALUES (:t,'booked',:d,'09:00',now()) RETURNING id"),
                               {"t": tid, "d": self.day + timedelta(days=30)}).scalar()
        base = self.ba.BookingAuthority

        def inject(sql, **p):
            class I(base):
                def _apply_attachments(inner, *a, **k):
                    out = base._apply_attachments(inner, *a, **k)
                    inner.db.execute(text(sql), p)
                    return out
            return I
        plan = self.ba.BookingAttachmentPlan()
        cases = {
            "OCCUPANCY_SET_CHANGED:delete": inject("DELETE FROM revisions WHERE id=:i", i=far.revision_id),
            "OCCUPANCY_SET_CHANGED:update": inject("UPDATE revisions SET marca='x' WHERE id=:i", i=far.revision_id),
            "OCCUPANCY_SET_CHANGED:insert": inject("INSERT INTO revisions (lead_id, created_at, estado_revision) "
                                                   "VALUES (:l, now(), 'REPROGRAMAR')", l=self.lead),
            "BOOKED_THREAD_REVISION_SET_CHANGED:unbook": inject("UPDATE thread_revisions SET status='draft' WHERE id=:i", i=far_tr),
            "OCCUPANCY_SET_CHANGED:tr_delete": inject("DELETE FROM thread_revisions WHERE id=:i", i=far_tr),
        }
        for name, auth in cases.items():
            r = self.book(self.cmd(time(12)), auth=auth, attachments=plan)
            self.assertEqual(r.reason, f"INVARIANT_BREACH:{name.split(':')[0]}", name)
        self.assertEqual(self.q("SELECT count(*) FROM revisions WHERE id=:i", i=far.revision_id)[0][0], 1)
        self.assertEqual(self.q("SELECT status FROM thread_revisions WHERE id=:i", i=far_tr)[0][0], "booked")

    # ══ 4. concurrent identities ═══════════════════════════════════════════

    def test_r4pg_13_two_concurrent_identities_never_mix(self):
        bar = threading.Barrier(2); res = {}
        B = self.ba
        people = {"a": B.LeadIdentity(nombre="Carlos", apellido="Pérez", email="c@example.invalid"),
                  "b": B.LeadIdentity(nombre="Ana", apellido="Gómez", email="a@example.invalid")}
        base = B.BookingAuthority

        class Sync(base):
            def _apply_attachments(inner, *a, **k):
                bar.wait(10)
                return base._apply_attachments(inner, *a, **k)

        def go(name, at, prof):
            res[name] = self.book(self.cmd(at, prof=prof), auth=Sync,
                                  attachments=B.BookingAttachmentPlan(lead=B.LeadUpdate(identity=people[name])))
        ts = [threading.Thread(target=go, args=("a", time(10), self.p1)),
              threading.Thread(target=go, args=("b", time(10), self.p2))]
        [t.start() for t in ts]; [t.join(30) for t in ts]
        outcomes = sorted(r.identity_outcome for r in res.values())
        lead = self.q("SELECT nombre, apellido, email FROM leads WHERE id=:l", l=self.lead)[0]
        self.assertEqual(outcomes, ["ADMITTED", "CONFLICT"])
        self.assertIn(tuple(lead), [("Carlos", "Pérez", "c@example.invalid"), ("Ana", "Gómez", "a@example.invalid")])
        self.assertTrue(all(r.outcome.value == "COMMITTED" for r in res.values()))

    # ══ 5. attention query ═════════════════════════════════════════════════

    def test_r4pg_14_attention_read_writes_nothing_and_terminalization_is_single(self):
        key = f"att-{self.lead}"
        self.book(self.cmd(time(10), key=key, tracks_acknowledgement=True))
        # Convergence Phase 3-R1 adaptation: ONE claim whose sender dies (formerly one per
        # allowed attempt, each reclaiming the last dead lease — no reclaim exists now; a
        # second claim would hold the row itself, before the read this test is about). The
        # invariants owned here are unchanged: the read writes nothing, and exactly one of four
        # concurrent terminalizations holds the row.
        with Session(self.engine) as s:
            self.ba.BookingAuthority(s).claim_acknowledgement(key)
        with self.engine.begin() as c:
            c.execute(text("UPDATE booking_commands SET ack_lease_expires_at = clock_timestamp() - "
                           "interval '1 second' WHERE idempotency_key=:k"), {"k": key})
        stat = text("SELECT coalesce(sum(n_tup_ins + n_tup_upd + n_tup_del), 0) FROM pg_stat_xact_user_tables")
        with Session(self.engine) as s:
            # pending counts of EARLIER transactions on this backend may be visible (PG 15+):
            # measure the delta inside one transaction
            before = s.execute(stat).scalar()
            rows = self.ba.BookingAuthority(s).acknowledgements_needing_attention()
            writes = s.execute(stat).scalar() - before
            assigned = s.execute(text("SELECT txid_current_if_assigned()")).scalar()
        self.assertEqual((writes, assigned), (0, None))          # no write, no xid ever assigned
        mine = [r for r in rows if r["idempotency_key"] == key]
        self.assertEqual([(r["ack_status"], r["pending_terminalization"]) for r in mine], [("SENDING", True)])
        bar = threading.Barrier(4); counts = []

        def go():
            with Session(self.engine) as s:
                bar.wait(10)
                counts.append(self.ba.BookingAuthority(s).terminalize_exhausted_acknowledgements())
        ts = [threading.Thread(target=go) for _ in range(4)]
        [t.start() for t in ts]; [t.join(20) for t in ts]
        self.assertEqual(sum(counts), 1)
        self.assertEqual(tuple(self.q("SELECT ack_status, ack_last_error FROM booking_commands WHERE idempotency_key=:k",
                                      k=key)[0]), ("FAILED", self.ba.ACK_SENDER_LOST))

    def _thread(self):
        with self.engine.begin() as c:
            ct = c.execute(text("INSERT INTO whatsapp_contacts (wa_id, display_name, created_at) VALUES "
                                "(:w,'R4F',now()) RETURNING id"),
                           {"w": "5491" + str(secrets.randbelow(10**9)).zfill(9)}).scalar()
            return c.execute(text("INSERT INTO whatsapp_threads (contact_id, lead_id, created_at) VALUES "
                                  "(:c,:l,now()) RETURNING id"), {"c": ct, "l": self.lead}).scalar()


if __name__ == "__main__":
    unittest.main()
