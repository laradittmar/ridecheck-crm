"""SB2A-R3 — PostgreSQL evidence for the typed-plan authority. Disposable databases only.

  R3_PG_URL      behaviour database (upgraded to the script head here if older)
  R3_PG_MIG_URL  a database at 20261008_sb2a_booking_authority (SB2A) holding real SB2A ledger
                 rows; it is upgraded to head to prove legacy NULL-lease handling

Primary control: callers have no interface to supply code — every R2-audit hostile payload
expressed through the typed interface is inert DATA. Defence in depth: the same hostile
operations injected into the authority's OWN steps (a test-only subclass) are detected and
reported truthfully. All tests skip cleanly without the variables.
"""
from __future__ import annotations

import os
import secrets
import sys
import threading
import unittest
from datetime import date, datetime, time, timedelta, timezone
from unittest import mock

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

PG_URL = os.environ.get("R3_PG_URL", "")
PG_MIG_URL = os.environ.get("R3_PG_MIG_URL", "")
SB2A = "20261008_sb2a_booking_authority"
FIXTURE = "SB2A-R3-PG-FIXTURE"
TRIG = "trg_revisions_sb2a_booking_version"


def _alembic(url, action, target):
    from alembic import command
    from alembic.config import Config
    prev, cwd = os.environ.get("DATABASE_URL"), os.getcwd()
    os.environ["DATABASE_URL"] = url
    try:
        os.chdir(_BACKEND)
        getattr(command, action)(Config(os.path.join(_BACKEND, "alembic.ini")), target)
    finally:
        os.chdir(cwd)
        if prev is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev


def _head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(Config(os.path.join(_BACKEND, "alembic.ini"))).get_current_head()


@unittest.skipUnless(PG_URL, "R3_PG_URL not set — no disposable PostgreSQL; nothing proven")
class R3Postgres(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_URL, pool_size=16, max_overflow=4)
        with cls.engine.connect() as c:
            current = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
        if current != _head():
            _alembic(PG_URL, "upgrade", _head())

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        from app.services import booking_authority as ba
        self.ba = ba
        self.day = date(2031, 1, 6) + timedelta(days=7 * (abs(hash(self.id())) % 300))
        self.threads = []
        with self.engine.begin() as c:
            self.lead = c.execute(text("INSERT INTO leads (created_at, estado, necesita_humano) "
                                       "VALUES (now(), 'R3', false) RETURNING id")).scalar()
            if not c.execute(text("SELECT count(*) FROM profesionales WHERE is_launch_default")).scalar():
                c.execute(text("INSERT INTO profesionales (nombre, apellido, email, created_at, resource_key,"
                               " is_launch_default, is_active) VALUES ('Fixture', :a, 'f@example.invalid',"
                               " now(), :k, true, true)"), {"a": FIXTURE, "k": f"r3.{secrets.token_hex(4)}"})

    def tearDown(self):
        with self.engine.begin() as c:
            c.execute(text(f"ALTER TABLE revisions ENABLE TRIGGER {TRIG}"))
            c.execute(text("DELETE FROM booking_commands WHERE lead_id = :l"), {"l": self.lead})
            if self.threads:
                c.execute(text("DELETE FROM thread_revisions WHERE thread_id = ANY(:t)"), {"t": self.threads})
                c.execute(text("DELETE FROM whatsapp_thread_states WHERE thread_id = ANY(:t)"), {"t": self.threads})
                c.execute(text("DELETE FROM whatsapp_threads WHERE id = ANY(:t)"), {"t": self.threads})
                c.execute(text("DELETE FROM whatsapp_contacts WHERE display_name='R3Fixture'"))
            c.execute(text("DELETE FROM revisions WHERE lead_id = :l"), {"l": self.lead})
            c.execute(text("DELETE FROM leads WHERE id = :l"), {"l": self.lead})

    # ── helpers ───────────────────────────────────────────────────────────

    def thread(self, state=True):
        with self.engine.begin() as c:
            ct = c.execute(text("INSERT INTO whatsapp_contacts (wa_id, display_name, created_at) VALUES "
                                "(:w, 'R3Fixture', now()) RETURNING id"),
                           {"w": "5491" + str(secrets.randbelow(10**9)).zfill(9)}).scalar()
            tid = c.execute(text("INSERT INTO whatsapp_threads (contact_id, lead_id, created_at) "
                                 "VALUES (:c, :l, now()) RETURNING id"), {"c": ct, "l": self.lead}).scalar()
            if state:
                c.execute(text("INSERT INTO whatsapp_thread_states (thread_id, needs_human, "
                               "flow_booking_token) VALUES (:t, false, 'tok')"), {"t": tid})
        self.threads.append(tid)
        return tid

    def cmd(self, at=time(10), *, key=None, op="CREATE", day=None, **kw):
        B = self.ba
        return B.BookingCommand(operation=B.BookingOperation(op), source=B.BookingSource.OTHER,
                                idempotency_key=key, lead_id=self.lead,
                                requested_date=day or self.day, requested_time=at, **kw)

    def run_cmd(self, command, *, authority=None, **kw):
        with Session(self.engine) as s:
            return (authority or self.ba.BookingAuthority)(s).execute(command, **kw)

    def q(self, sql, **p):
        with self.engine.connect() as c:
            return c.execute(text(sql), p).all()

    def counts(self):
        return (self.q("SELECT count(*) FROM revisions WHERE lead_id=:l", l=self.lead)[0][0],
                self.q("SELECT count(*) FROM thread_revisions WHERE thread_id = ANY(:t)",
                       t=self.threads or [0])[0][0],
                self.q("SELECT count(*) FROM booking_commands WHERE lead_id=:l", l=self.lead)[0][0])

    def injected(self, fault):
        base = self.ba.BookingAuthority

        class _Inject(base):
            def _apply_attachments(inner, cmd, plan, revision, target):
                tr = base._apply_attachments(inner, cmd, plan, revision, target)
                fault(inner.db, revision)
                return tr
        return _Inject

    # ══ 1. Primary control: hostile payloads through the interface are inert data ═════

    def test_pg_r3_01_hostile_strings_in_plans_are_stored_never_executed(self):
        tid = self.thread()
        payloads = ["/* x */ COMMIT", "-- x\nROLLBACK",
                    "DO $$ BEGIN EXECUTE 'ALTER TABLE revisions DISABLE TRIGGER " + TRIG + "'; END $$",
                    "SELECT set_config('session_replication_role','replica',true)",
                    "DELETE FROM revisions"]
        for i, p in enumerate(payloads):
            plan = self.ba.BookingAttachmentPlan(
                thread_revision=self.ba.ThreadRevisionAttachment(thread_id=tid, buyer_name=p[:120],
                                                                 address=p),
                lead=self.ba.LeadUpdate(estado=p[:40]))
            r = self.run_cmd(self.cmd(time(8 + i), key=f"inert{i}-{self.lead}", thread_id=tid),
                             attachments=plan,
                             availability=None)
            self.assertEqual(r.outcome.value, "COMMITTED", (p, r.reason))
            self.assertEqual(self.q("SELECT address FROM thread_revisions WHERE id=:i",
                                    i=r.thread_revision_id)[0][0], p)
        self.assertEqual(self.q(f"SELECT tgenabled FROM pg_trigger WHERE tgname='{TRIG}'")[0][0], "O")
        self.assertEqual(self.counts(), (5, 5, 5))

    # ══ 2. Defence in depth: every R2-audit shape injected into authority code ═══════

    def _assert_detected(self, r, kind):
        self.assertEqual(r.outcome.value, "INTERNAL_FAILURE")
        self.assertEqual(r.reason, f"INVARIANT_BREACH:{kind}")

    def test_pg_r3_02_in_transaction_shapes_are_detected_and_fully_rolled_back(self):
        tid = self.thread()
        other = self.run_cmd(self.cmd(time(8)))
        with self.engine.begin() as c:
            other_tr = c.execute(text("INSERT INTO thread_revisions (thread_id, status, scheduled_date, "
                                      "scheduled_time, created_at) VALUES (:t,'booked',:d,'16:00',now()) "
                                      "RETURNING id"), {"t": tid, "d": self.day}).scalar()
        shapes = {
            "POST_OPERATION_CRITICAL_TRIGGER_DISABLED": lambda s, rev: s.execute(text(
                f"DO $$ BEGIN EXECUTE 'ALTER TABLE revisions DISABLE TRIGGER {TRIG}'; END $$")),
            "POST_OPERATION_TRIGGERS_DISABLED_FOR_SESSION": lambda s, rev: s.execute(text(
                "SELECT set_config('session_replication_role', 'replica', true)")),
            "OCCUPANCY_SET_CHANGED": lambda s, rev: s.execute(text(
                "DELETE FROM revisions WHERE id=:i"), {"i": other.revision_id}),
            "BOOKED_THREAD_REVISION_SET_CHANGED": lambda s, rev: s.execute(text(
                "UPDATE thread_revisions SET status='draft' WHERE id=:i"), {"i": other_tr}),
            "BOOKING_FIELDS_CHANGED": lambda s, rev: s.execute(text(
                "UPDATE revisions SET estado_revision=' cancelado ' WHERE id=:i"), {"i": rev.id}),
        }
        before = self.counts()
        for kind, fault in shapes.items():
            r = self.run_cmd(self.cmd(time(12), key=f"dd-{kind}-{self.lead}", thread_id=tid),
                             authority=self.injected(fault),
                             attachments=self.ba.BookingAttachmentPlan(
                                 thread_revision=self.ba.ThreadRevisionAttachment(thread_id=tid)))
            self._assert_detected(r, kind)
            self.assertEqual(self.counts(), before, kind)          # zero partial rows
        # every injected effect was inside the rolled-back transaction
        self.assertEqual(self.q(f"SELECT tgenabled FROM pg_trigger WHERE tgname='{TRIG}'")[0][0], "O")
        self.assertEqual(self.q("SELECT count(*) FROM revisions WHERE id=:i", i=other.revision_id)[0][0], 1)
        self.assertEqual(self.q("SELECT status FROM thread_revisions WHERE id=:i", i=other_tr)[0][0], "booked")
        with self.engine.connect() as c:
            self.assertEqual(c.execute(text("SELECT current_setting('session_replication_role')")).scalar(),
                             "origin")

    def test_pg_r3_03_transaction_ending_shapes_are_detected_before_the_ledger(self):
        tid = self.thread()
        for sql in ("-- x\nROLLBACK", "/* x */ ROLLBACK"):
            r = self.run_cmd(self.cmd(time(13), key=f"rb-{len(sql)}-{self.lead}", thread_id=tid),
                             authority=self.injected(lambda s, rev, q=sql: s.execute(text(q))),
                             attachments=self.ba.BookingAttachmentPlan(
                                 thread_revision=self.ba.ThreadRevisionAttachment(thread_id=tid)))
            self._assert_detected(r, "TRANSACTION_NOT_CONTINUOUS")
            self.assertEqual(self.counts(), (0, 0, 0))              # booking AND ledger absent
        # A stray COMMIT inside authority code is DETECTED and reported, but statements the
        # server already committed cannot be undone — this is why R3's primary control is that
        # no caller can supply code at all (test_pg_r3_01).
        r = self.run_cmd(self.cmd(time(14), key=f"cm-{self.lead}", thread_id=tid),
                         authority=self.injected(lambda s, rev: s.execute(text("/* x */ COMMIT"))),
                         attachments=self.ba.BookingAttachmentPlan(
                             thread_revision=self.ba.ThreadRevisionAttachment(thread_id=tid)))
        self._assert_detected(r, "TRANSACTION_NOT_CONTINUOUS")
        self.assertEqual(self.counts()[2], 0)                       # no ledger was written

    def test_pg_r3_04_side_channel_writes_are_legitimate_concurrency_unless_they_overlap(self):
        """SB2A-R4 rewrite (R3A-04): the booking no longer locks or diffs the whole day, so a
        committed write by ANOTHER transaction that does not overlap is ordinary concurrency
        and the booking commits; an OVERLAPPING side write still cannot coexist — the
        exclusion constraint stops it. In-transaction writes stay audited (pg_r3_02)."""
        def raw(at):
            def fault(s, rev):
                conn = self.engine.raw_connection()
                try:
                    cur = conn.cursor()
                    cur.execute("INSERT INTO revisions (lead_id, created_at, estado_revision, turno_fecha, "
                                "turno_hora) VALUES (%s, now(), 'CONFIRMADO', %s, %s)", (self.lead, self.day, at))
                    conn.commit(); return "committed"
                except Exception as exc:
                    conn.rollback(); return type(exc).__name__
                finally:
                    conn.close()
            return fault
        r = self.run_cmd(self.cmd(time(10), key=f"side-ok-{self.lead}"), authority=self.injected(raw("18:00")),
                         attachments=self.ba.BookingAttachmentPlan())
        self.assertEqual(r.outcome.value, "COMMITTED", r.reason)
        side = {}

        def overlapping(s, rev):
            # a bypass writer racing an uncommitted booking: it blocks on our row; give up fast
            conn = self.engine.raw_connection()
            try:
                cur = conn.cursor(); cur.execute("SET lock_timeout = '300ms'")
                cur.execute("INSERT INTO revisions (lead_id, created_at, estado_revision, turno_fecha, "
                            "turno_hora) VALUES (%s, now(), 'CONFIRMADO', %s, '12:15')", (self.lead, self.day))
                conn.commit(); side["r"] = "committed"
            except Exception as exc:
                conn.rollback(); side["r"] = type(exc).__name__
            finally:
                conn.close()
        r2 = self.run_cmd(self.cmd(time(12), key=f"side-ov-{self.lead}"), authority=self.injected(overlapping),
                          attachments=self.ba.BookingAttachmentPlan())
        self.assertEqual(r2.outcome.value, "COMMITTED", r2.reason)
        self.assertNotEqual(side["r"], "committed")              # the overlap never coexists
        self.assertEqual(sorted(t for (t,) in self.q(
            "SELECT turno_hora FROM revisions WHERE lead_id=:l", l=self.lead)), [time(10), time(12), time(18)])

    # ══ 3. Database clock authority ═══════════════════════════════════════════

    def tracked(self, key):
        r = self.run_cmd(self.cmd(key=key, tracks_acknowledgement=True))
        self.assertEqual(r.ack_status, "PENDING")

    def claim(self, key, app_now=None):
        with Session(self.engine) as s:
            if app_now is None:
                return self.ba.BookingAuthority(s).claim_acknowledgement(key)
            with mock.patch.object(self.ba, "_app_now", return_value=app_now):
                return self.ba.BookingAuthority(s).claim_acknowledgement(key)

    def age(self, key, seconds):
        with self.engine.begin() as c:
            c.execute(text(f"UPDATE booking_commands SET ack_lease_expires_at = clock_timestamp() + "
                           f"interval '{seconds} seconds' WHERE idempotency_key=:k"), {"k": key})

    def state(self, key):
        return tuple(self.q("SELECT ack_status, ack_last_error FROM booking_commands "
                            "WHERE idempotency_key=:k", k=key)[0])

    # Convergence Phase 3-R1 adaptation of r3_05..r3_07. OLD: the DATABASE clock decided when
    # an expired lease could be RECLAIMED (and resent). NEW: no lease is ever reclaimed; the
    # database clock decides when a lost sender is HELD as uncertain (`_hold_lost_senders`)
    # instead of left in flight. The invariant these tests own — an application host's clock,
    # however skewed, never decides the lease — is asserted on the hold. PROOF: under the old
    # claim the post-expiry claims return a claim.
    def test_pg_r3_05_application_clock_never_decides_the_lease(self):
        key = f"clk-{self.lead}"; self.tracked(key)
        ahead = datetime.now(timezone.utc) + timedelta(days=3)
        behind = datetime.now(timezone.utc) - timedelta(days=3)
        first = self.claim(key, app_now=behind)
        self.assertIsNotNone(first)
        self.assertIsNone(self.claim(key, app_now=ahead))           # host far ahead: no steal
        lease = self.q("SELECT ack_lease_expires_at - clock_timestamp() FROM booking_commands "
                       "WHERE idempotency_key=:k", k=key)[0][0]
        self.assertTrue(timedelta(seconds=100) < lease <= timedelta(seconds=120), lease)
        self.assertEqual(self.state(key)[0], "SENDING")            # …nor holds a live lease
        self.age(key, -1)                                           # expired by DATABASE time
        self.assertIsNone(self.claim(key, app_now=behind))          # host far behind: no resend
        self.assertEqual(self.state(key), ("FAILED", self.ba.ACK_SENDER_LOST))  # …but held

    def test_pg_r3_06_two_workers_with_skewed_clocks_no_sender(self):
        # Phase 3-R1 (renamed from `..._one_sender`): neither skewed worker sends; one holds.
        key = f"clk2-{self.lead}"; self.tracked(key)
        self.claim(key); self.age(key, -1)                          # a dead claimant
        barrier, won = threading.Barrier(2), []

        def worker(skew):
            barrier.wait()
            c = self.claim(key, app_now=datetime.now(timezone.utc) + skew)
            if c:
                won.append(c)
        ts = [threading.Thread(target=worker, args=(s,)) for s in
              (timedelta(hours=5), -timedelta(hours=5))]
        [t.start() for t in ts]; [t.join(20) for t in ts]
        self.assertEqual(won, [])
        self.assertEqual(self.state(key), ("FAILED", self.ba.ACK_SENDER_LOST))

    def test_pg_r3_07_expiry_boundary_is_database_time(self):
        key = f"bnd-{self.lead}"; self.tracked(key)
        self.claim(key)
        self.age(key, 2)
        self.assertIsNone(self.claim(key))                          # 2 s left by the DB clock
        self.assertEqual(self.state(key)[0], "SENDING")             # …still in flight
        self.age(key, 0)                                            # expires now (strict <)
        self.assertIsNone(self.claim(key))
        self.assertEqual(self.state(key), ("FAILED", self.ba.ACK_SENDER_LOST))

    # ══ 4. Exhaustion at attempts 1..5 ═══════════════════════════════════════

    def test_pg_r3_08_a_crash_is_terminal_at_once(self):
        # Convergence Phase 3-R1 adaptation (renamed from `..._crash_at_each_attempt_then_
        # terminal`). OLD: five crashes, each dead lease reclaimed, then terminal. NEW: the
        # first crash is terminal — held as uncertain, never claimed again. WHY: owner policy.
        # INVARIANT kept: terminal, booking untouched, listed for an operator. PROOF: the old
        # claim returns attempt 2 after the first crash.
        key = f"exh-{self.lead}"; self.tracked(key)
        c = self.claim(key)
        self.assertEqual((c.attempt, c.duplicate_risk), (1, False))
        self.age(key, -1)                                           # this claimant dies
        self.assertIsNone(self.claim(key))
        self.assertIsNone(self.claim(key))
        row = self.q("SELECT ack_status, ack_attempts, ack_duplicate_risk, ack_last_error "
                     "FROM booking_commands WHERE idempotency_key=:k", k=key)[0]
        self.assertEqual(tuple(row), ("FAILED", 1, True, self.ba.ACK_SENDER_LOST))
        self.assertEqual(self.counts()[0], 1)                       # booking untouched
        with Session(self.engine) as s:
            keys = [r["idempotency_key"] for r in
                    self.ba.BookingAuthority(s).acknowledgements_needing_attention()]
        self.assertIn(key, keys)

    # ══ 5. Preflight completeness ════════════════════════════════════════════

    def test_pg_r3_09_previously_unchecked_columns_now_stop_the_booking(self):
        for table, col, ddl in (("booking_commands", "ack_last_error", "varchar(64)"),
                                ("leads", "canal", "varchar(50)"),
                                ("whatsapp_thread_states", "flow_booking_token", "varchar(120)")):
            with self.engine.begin() as c:
                c.execute(text(f"ALTER TABLE {table} DROP COLUMN {col}"))
            try:
                r = self.run_cmd(self.cmd(time(11)))
                self.assertEqual((r.outcome.value, r.reason),
                                 ("INTERNAL_FAILURE", "BOOKING_SCHEMA_INCOMPLETE"), col)
            finally:
                with self.engine.begin() as c:
                    c.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
        self.assertEqual(self.run_cmd(self.cmd(time(11))).outcome.value, "COMMITTED")

    # ══ 6. Callers on the typed contract (real PostgreSQL) ═══════════════════

    def test_pg_r3_10_api_create_update_and_crm_edit(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.revisions import router
        from app.db import get_db
        app = FastAPI(); app.include_router(router)

        def _db():
            s = Session(self.engine)
            try:
                yield s
            finally:
                s.close()
        app.dependency_overrides[get_db] = _db
        c = TestClient(app, raise_server_exceptions=False)
        d = self.day.isoformat()
        r1 = c.post(f"/leads/{self.lead}/revisions", json={"turno_fecha": d, "turno_hora": "10:00:00",
                                                           "estado_revision": "PENDIENTE"},
                    headers={"Idempotency-Key": f"r3-api-{self.lead}"})
        self.assertEqual(r1.status_code, 200, r1.text)
        clash = c.post(f"/leads/{self.lead}/revisions", json={"turno_fecha": d, "turno_hora": "10:15:00",
                                                              "estado_revision": "PENDIENTE"})
        self.assertEqual(clash.status_code, 409, clash.text)
        up = c.patch(f"/leads/{self.lead}/revisions/latest", json={"turno_fecha": d, "turno_hora": "15:00:00",
                                                                     "estado_revision": "PENDIENTE"})
        self.assertEqual(up.status_code, 200, up.text)
        # the CRM form's command path (command_for_edit → authority)
        from app.models import Revision
        with Session(self.engine) as s:
            rev = s.get(Revision, up.json()["id"])
            cmd = self.ba.command_for_edit(rev, source=self.ba.BookingSource.CRM,
                                           new_date=self.day, new_time=time(16), new_status=None,
                                           new_profesional_id=None,
                                           expected_version=rev.booking_version)
            res = self.ba.BookingAuthority(s).execute(cmd)
        self.assertEqual(res.outcome.value, "COMMITTED", res.reason)

    def test_pg_r3_11_thread_revision_sync_replay_and_rollback(self):
        tid = self.thread()
        plan = self.ba.BookingAttachmentPlan(
            thread_revision=self.ba.ThreadRevisionAttachment(thread_id=tid, request_approval=True),
            thread_state=self.ba.ThreadStateUpdate(link_current_revision=True, consume_flow_token=True))
        key = f"sync-{self.lead}"
        r = self.run_cmd(self.cmd(time(10), key=key, thread_id=tid), attachments=plan)
        self.assertEqual(r.outcome.value, "COMMITTED", r.reason)
        again = self.run_cmd(self.cmd(time(10), key=key, thread_id=tid), attachments=plan)
        self.assertEqual((again.outcome.value, again.revision_id), ("IDEMPOTENT_REPLAY", r.revision_id))
        st = self.q("SELECT current_revision_id, flow_booking_token FROM whatsapp_thread_states "
                    "WHERE thread_id=:t", t=tid)[0]
        self.assertEqual(tuple(st), (r.thread_revision_id, None))
        tr = self.q("SELECT status, scheduled_date, scheduled_time, appointment_approval_status, "
                    "appointment_approval_token IS NOT NULL FROM thread_revisions WHERE id=:i",
                    i=r.thread_revision_id)[0]
        self.assertEqual(tuple(tr), ("booked", self.day, time(10), "PENDING", True))
        moved = self.run_cmd(self.cmd(time(13), op="RESCHEDULE", revision_id=r.revision_id))
        self.assertEqual(moved.outcome.value, "COMMITTED", moved.reason)
        self.assertEqual(self.q("SELECT status, scheduled_time FROM thread_revisions WHERE id=:i",
                                i=r.thread_revision_id)[0], ("booked", time(13)))


@unittest.skipUnless(PG_MIG_URL, "R3_PG_MIG_URL not set — nothing proven")
class R3LegacyLease(unittest.TestCase):

    def test_pg_r3_12_real_sb2a_sending_rows_after_upgrade(self):
        eng = create_engine(PG_MIG_URL)
        try:
            with eng.begin() as c:
                version = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
                self.assertEqual(version, SB2A, "R3_PG_MIG_URL must start at SB2A")
                lead = c.execute(text("INSERT INTO leads (created_at, estado, necesita_humano) "
                                      "VALUES (now(),'R3-MIG',false) RETURNING id")).scalar()
                for key, attempts in (("legacy-1", 1), ("legacy-5", 5)):
                    c.execute(text("INSERT INTO booking_commands (idempotency_key, source, operation, "
                                   "request_fingerprint, lead_id, occupying, booking_version, ack_status, "
                                   "ack_attempts, replay_count) VALUES (:k,'WHATSAPP_FLOW','CREATE','f',:l,"
                                   "true,0,'SENDING',:a,0)"), {"k": key, "l": lead, "a": attempts})
            _alembic(PG_MIG_URL, "upgrade", _head())
            from app.services import booking_authority as ba
            with Session(eng) as s:
                auth = ba.BookingAuthority(s)
                one = auth.claim_acknowledgement("legacy-1")
                again = auth.claim_acknowledgement("legacy-1")         # its new lease is active
                five = auth.claim_acknowledgement("legacy-5")
                attention = {r["idempotency_key"]: r for r in auth.acknowledgements_needing_attention()}
            # Convergence Phase 3-R1 adaptation. OLD: a real SB2A-era SENDING row with no lease
            # got one recovery send (attempt 2, duplicate risk); the exhausted one was terminal.
            # NEW: both are held as uncertain — their senders' fate is unknown — and neither is
            # ever claimed. INVARIANT kept: real upgraded rows are handled without error and are
            # listed for an operator. PROOF: the old claim returns a claim for "legacy-1".
            self.assertIsNone(one)
            self.assertIsNone(again)
            self.assertIsNone(five)
            rows = dict((k, tuple(v)) for k, *v in eng.connect().execute(text(
                "SELECT idempotency_key, ack_status, ack_attempts, ack_duplicate_risk, ack_last_error "
                "FROM booking_commands WHERE idempotency_key IN ('legacy-1','legacy-5')")).all())
            self.assertEqual(rows["legacy-1"], ("FAILED", 1, True, ba.ACK_SENDER_LOST))
            self.assertEqual(rows["legacy-5"], ("FAILED", 5, True, ba.ACK_SENDER_LOST))
            self.assertIn("legacy-5", attention)
            self.assertIn("legacy-1", attention)                         # duplicate risk is visible
        finally:
            eng.dispose()


if __name__ == "__main__":
    unittest.main()
