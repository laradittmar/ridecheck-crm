"""SB2A-R5 — PostgreSQL lock-wait mutation proof (R3A-T1). Disposable database only
(R5_PG_URL); skips cleanly without it.

A booking is held in a GENUINE lock wait (a parked same-professional, same-day booking holds
the (professional, date) advisory lock). While it waits — after the authority captured its
snapshot and before any trusted write — another thread holding the caller's ORIGINAL
references mutates every input. The committed result must use only the captured values.
"""
from __future__ import annotations

import os
import secrets
import sys
import threading
import time as _t
import unittest
from datetime import date, time, timedelta

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

PG_URL = os.environ.get("R5_PG_URL", "")


def _head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(Config(os.path.join(_BACKEND, "alembic.ini"))).get_current_head()


@unittest.skipUnless(PG_URL, "R5_PG_URL not set — no disposable PostgreSQL; nothing proven")
class R5LockWait(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_URL, pool_size=16, max_overflow=4)
        with cls.engine.connect() as c:
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == _head()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        from app.services import booking_authority as ba
        self.ba = ba
        self.day = date(2035, 1, 1) + timedelta(days=7 * (abs(hash(self.id())) % 400))
        with self.engine.begin() as c:
            self.lead = c.execute(text("INSERT INTO leads (created_at, estado, necesita_humano) VALUES (now(),'R5',false) RETURNING id")).scalar()
            self.lead2 = c.execute(text("INSERT INTO leads (created_at, estado, necesita_humano) VALUES (now(),'R5-2',false) RETURNING id")).scalar()
            if not c.execute(text("SELECT count(*) FROM profesionales WHERE resource_key='r5.p1'")).scalar():
                c.execute(text("UPDATE profesionales SET is_launch_default=false"))
                c.execute(text("INSERT INTO profesionales (nombre, apellido, email, created_at, resource_key, is_launch_default, is_active) "
                               "VALUES ('P1','R5','p1@example.invalid',now(),'r5.p1',true,true), ('P2','R5','p2@example.invalid',now(),'r5.p2',false,true)"))
            self.p1 = c.execute(text("SELECT id FROM profesionales WHERE resource_key='r5.p1'")).scalar()
            self.p2 = c.execute(text("SELECT id FROM profesionales WHERE resource_key='r5.p2'")).scalar()

    def tearDown(self):
        with self.engine.begin() as c:
            L = {"a": self.lead, "b": self.lead2}
            c.execute(text("DELETE FROM booking_commands WHERE lead_id IN (:a,:b)"), L)
            c.execute(text("DELETE FROM thread_revisions WHERE thread_id IN (SELECT id FROM whatsapp_threads WHERE lead_id IN (:a,:b))"), L)
            c.execute(text("DELETE FROM whatsapp_thread_states WHERE thread_id IN (SELECT id FROM whatsapp_threads WHERE lead_id IN (:a,:b))"), L)
            c.execute(text("DELETE FROM whatsapp_threads WHERE lead_id IN (:a,:b)"), L)
            c.execute(text("DELETE FROM revisions WHERE lead_id IN (:a,:b)"), L)
            c.execute(text("DELETE FROM leads WHERE id IN (:a,:b)"), L)

    # ── helpers ───────────────────────────────────────────────────────────

    def thread(self, lead=None):
        with self.engine.begin() as c:
            ct = c.execute(text("INSERT INTO whatsapp_contacts (wa_id, display_name, created_at) VALUES (:w,'R5F',now()) RETURNING id"),
                           {"w": "5491" + str(secrets.randbelow(10**9)).zfill(9)}).scalar()
            tid = c.execute(text("INSERT INTO whatsapp_threads (contact_id, lead_id, created_at) VALUES (:c,:l,now()) RETURNING id"),
                            {"c": ct, "l": lead or self.lead}).scalar()
            c.execute(text("INSERT INTO whatsapp_thread_states (thread_id, needs_human, flow_booking_token) VALUES (:t,false,'tok')"), {"t": tid})
        return tid

    def book(self, c, auth=None, **kw):
        with Session(self.engine) as s:
            return (auth or self.ba.BookingAuthority)(s, lock_timeout_ms=10000).execute(c, **kw)

    def q(self, sql, **p):
        with self.engine.connect() as c:
            return c.execute(text(sql), p).all()

    def holder(self, at=time(8)):
        """Park a same-professional booking on self.day while holding L1/L2."""
        parked, release, out = threading.Event(), threading.Event(), {}
        base = self.ba.BookingAuthority

        class P(base):
            def _verify_before_commit(inner, *a, **k):
                parked.set(); release.wait(20)
                return base._verify_before_commit(inner, *a, **k)
        B = self.ba
        c = B.BookingCommand(operation=B.BookingOperation.CREATE, source=B.BookingSource.OTHER, lead_id=self.lead2,
                             requested_date=self.day, requested_time=at)
        th = threading.Thread(target=lambda: out.setdefault("r", self.book(c, auth=P)))
        th.start(); self.assertTrue(parked.wait(10))
        return release, th, out

    def contender_waiting(self, fn):
        """Start fn in a thread; return once it is provably blocked on an advisory lock."""
        res = {}
        th = threading.Thread(target=lambda: res.setdefault("r", fn()))
        th.start()
        for _ in range(100):
            waiting = self.q("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND NOT granted")[0][0]
            if waiting:
                return th, res
            _t.sleep(0.05)
        self.fail("contender never waited on the lock")

    # ══ 1. the full lock-wait mutation matrix ═══════════════════════════════

    def test_r5pg_01_every_original_mutated_during_the_wait(self):
        B = self.ba
        tid, foreign = self.thread(), self.thread(lead=self.lead2)
        rf = {"marca": "Fiat"}
        ident = B.LeadIdentity(nombre="Carlos", apellido="Pérez")
        tr = B.ThreadRevisionAttachment(thread_id=tid, request_approval=False, buyer_name="Carlos")
        st = B.ThreadStateUpdate(link_current_revision=True, consume_flow_token=False)
        lu = B.LeadUpdate(identity=ident)
        plan = B.BookingAttachmentPlan(thread_revision=tr, lead=lu, thread_state=st)
        key = f"r5-{self.lead}"
        c = B.BookingCommand(operation=B.BookingOperation.CREATE, source=B.BookingSource.OTHER, lead_id=self.lead,
                             thread_id=tid, requested_date=self.day, requested_time=time(15), idempotency_key=key,
                             correlation_id="corr-ok", actor="actor-ok", tracks_acknowledgement=False,
                             revision_fields=rf)
        release, hth, hout = self.holder()
        th, res = self.contender_waiting(lambda: self.book(c, attachments=plan))
        # ── the caller mutates EVERYTHING it still holds, mid-wait ──
        rf["marca"] = "MUTATED"; rf["lead_id"] = self.lead2; rf["estado_revision"] = "CANCELADO"
        object.__setattr__(tr, "request_approval", "no")
        object.__setattr__(tr, "thread_id", foreign)
        object.__setattr__(tr, "buyer_name", "Mallory")
        object.__setattr__(ident, "nombre", "Mallory")
        object.__setattr__(lu, "identity", B.LeadIdentity(nombre="Eve", apellido="X"))
        object.__setattr__(st, "consume_flow_token", True)
        object.__setattr__(plan, "lead", B.LeadUpdate(estado="HIJACKED"))
        object.__setattr__(c, "lead_id", self.lead2)
        object.__setattr__(c, "thread_id", foreign)
        object.__setattr__(c, "profesional_id", self.p2)
        object.__setattr__(c, "requested_date", self.day + timedelta(days=9))
        object.__setattr__(c, "requested_time", time(17))
        object.__setattr__(c, "idempotency_key", "HIJACKED")
        object.__setattr__(c, "correlation_id", "HIJACKED"); object.__setattr__(c, "actor", "HIJACKED")
        object.__setattr__(c, "tracks_acknowledgement", True)
        release.set(); hth.join(15); th.join(20)
        r = res["r"]
        self.assertEqual(r.outcome.value, "COMMITTED", r.reason)
        rev = self.q("SELECT lead_id, marca, estado_revision, profesional_id, turno_fecha, turno_hora FROM revisions WHERE id=:i",
                     i=r.revision_id)[0]
        self.assertEqual(tuple(rev), (self.lead, "Fiat", "PENDIENTE", self.p1, self.day, time(15)))
        t = self.q("SELECT thread_id, buyer_name, appointment_approval_status FROM thread_revisions WHERE id=:i",
                   i=r.thread_revision_id)[0]
        self.assertEqual(tuple(t), (tid, "Carlos", None))
        lead = self.q("SELECT nombre, apellido, estado FROM leads WHERE id=:l", l=self.lead)[0]
        self.assertEqual(tuple(lead), ("Carlos", "Pérez", "R5"))
        self.assertEqual(r.identity_outcome, "ADMITTED")
        self.assertEqual(self.q("SELECT flow_booking_token FROM whatsapp_thread_states WHERE thread_id=:t", t=tid)[0][0], "tok")
        self.assertEqual(self.q("SELECT flow_booking_token, current_revision_id FROM whatsapp_thread_states WHERE thread_id=:t",
                                t=foreign)[0], ("tok", None))
        led = self.q("SELECT idempotency_key, lead_id, thread_id, correlation_id, actor, ack_status FROM booking_commands "
                     "WHERE revision_id=:i", i=r.revision_id)[0]
        self.assertEqual(tuple(led), (key, self.lead, tid, "corr-ok", "actor-ok", "NOT_APPLICABLE"))
        self.assertEqual((r.idempotency_key, r.profesional_id, r.ack_status), (key, self.p1, "NOT_APPLICABLE"))
        self.assertEqual(self.q("SELECT count(*) FROM revisions WHERE lead_id=:l", l=self.lead2)[0][0], 1)  # only the holder's

    def test_r5pg_02_expected_version_mutated_during_the_wait(self):
        B = self.ba
        target = self.book(B.BookingCommand(operation=B.BookingOperation.CREATE, source=B.BookingSource.OTHER,
                                            lead_id=self.lead, requested_date=self.day, requested_time=time(12)))
        c = B.BookingCommand(operation=B.BookingOperation.RESCHEDULE, source=B.BookingSource.OTHER,
                             revision_id=target.revision_id, requested_date=self.day, requested_time=time(14),
                             expected_version=target.booking_version)
        release, hth, _ = self.holder()
        th, res = self.contender_waiting(lambda: self.book(c))
        object.__setattr__(c, "expected_version", 99)                 # would be STALE if re-read
        object.__setattr__(c, "revision_id", 1)
        release.set(); hth.join(15); th.join(20)
        self.assertEqual((res["r"].outcome.value, res["r"].revision_id), ("COMMITTED", target.revision_id))
        self.assertEqual(self.q("SELECT turno_hora FROM revisions WHERE id=:i", i=target.revision_id)[0][0], time(14))

    def test_r5pg_05_thread_state_only_plan_uses_the_captured_command_thread(self):
        """No ThreadRevision attachment: the state update targets the COMMAND's thread, which
        must be the captured one even if the caller retargets the original mid-wait."""
        B = self.ba
        tid, other = self.thread(), self.thread()          # both threads of this lead
        plan = B.BookingAttachmentPlan(thread_state=B.ThreadStateUpdate(consume_flow_token=True,
                                                                         last_stage="BOOKED"))
        c = B.BookingCommand(operation=B.BookingOperation.CREATE, source=B.BookingSource.OTHER, lead_id=self.lead,
                             thread_id=tid, requested_date=self.day, requested_time=time(15))
        release, hth, _ = self.holder()
        th, res = self.contender_waiting(lambda: self.book(c, attachments=plan))
        object.__setattr__(c, "thread_id", other)
        release.set(); hth.join(15); th.join(20)
        self.assertEqual(res["r"].outcome.value, "COMMITTED", res["r"].reason)
        st = dict(self.q("SELECT thread_id, flow_booking_token FROM whatsapp_thread_states WHERE thread_id IN (:a,:b)",
                         a=tid, b=other))
        self.assertEqual((st[tid], st[other]), (None, "tok"))

    # ══ 2. lock and key stability: mutation BEFORE locks are derived ════════

    def test_r5pg_03_lock_keys_come_from_the_snapshot(self):
        B = self.ba
        seen, gate, go = {}, threading.Event(), threading.Event()
        base = B.BookingAuthority

        class Pause(base):
            def _load_revision(inner, cmd):          # after capture+validation, before L1/L2
                gate.set(); go.wait(10)
                return base._load_revision(inner, cmd)

            def _acquire_locks(inner, keys):
                seen["keys"] = keys
                return base._acquire_locks(inner, keys)
        c = B.BookingCommand(operation=B.BookingOperation.CREATE, source=B.BookingSource.OTHER, lead_id=self.lead,
                             requested_date=self.day, requested_time=time(10), profesional_id=self.p1)
        res = {}
        th = threading.Thread(target=lambda: res.setdefault("r", self.book(c, auth=Pause)))
        th.start(); self.assertTrue(gate.wait(10))
        object.__setattr__(c, "profesional_id", self.p2)
        object.__setattr__(c, "requested_date", self.day + timedelta(days=3))
        object.__setattr__(c, "requested_time", time(16))
        go.set(); th.join(20)
        ymd = self.day.year * 10000 + self.day.month * 100 + self.day.day
        self.assertEqual(seen["keys"], ((self.p1, self.day),))
        self.assertEqual((res["r"].outcome.value, res["r"].profesional_id), ("COMMITTED", self.p1))
        self.assertEqual(self.q("SELECT turno_fecha, turno_hora, profesional_id FROM revisions WHERE id=:i",
                                i=res["r"].revision_id)[0], (self.day, time(10), self.p1))
        del ymd

    # ══ 3. the CE adapter under a genuine lock wait ══════════════════════════

    def test_r5pg_04_adapter_request_mutated_during_the_wait(self):
        from app.services import booking_ce_adapter as ce
        B = self.ba
        tid, foreign = self.thread(), self.thread(lead=self.lead2)
        rf = {"marca": "Fiat"}
        plan = B.BookingAttachmentPlan(thread_revision=B.ThreadRevisionAttachment(thread_id=tid))
        req = ce.CeFlowBookingRequest(thread_id=tid, lead_id=self.lead, inbound_event_id="wamid.R5", flow_token=None,
                                      requested_date=self.day, requested_time=time(15), revision_fields=rf,
                                      attachments=plan)
        expected_key = ce.ce_flow_booking_key(req)
        release, hth, _ = self.holder()
        th, res = self.contender_waiting(lambda: ce.book_from_flow_response(Session(self.engine), req, lock_timeout_ms=10000))
        rf["lead_id"] = self.lead2; rf["marca"] = "MUTATED"
        object.__setattr__(req, "thread_id", foreign); object.__setattr__(req, "lead_id", self.lead2)
        object.__setattr__(req, "inbound_event_id", "wamid.HIJACK")
        object.__setattr__(plan, "thread_revision", B.ThreadRevisionAttachment(thread_id=foreign, request_approval=True))
        release.set(); hth.join(15); th.join(20)
        r = res["r"]
        self.assertEqual(r.outcome.value, "COMMITTED", r.reason)
        self.assertEqual(tuple(self.q("SELECT lead_id, marca FROM revisions WHERE id=:i", i=r.revision_id)[0]), (self.lead, "Fiat"))
        self.assertEqual(tuple(self.q("SELECT thread_id, appointment_approval_status FROM thread_revisions WHERE id=:i",
                                      i=r.thread_revision_id)[0]), (tid, None))
        self.assertEqual(self.q("SELECT idempotency_key FROM booking_commands WHERE revision_id=:i", i=r.revision_id)[0][0],
                         expected_key)


if __name__ == "__main__":
    unittest.main()
