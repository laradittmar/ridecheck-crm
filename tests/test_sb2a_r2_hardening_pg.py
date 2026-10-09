"""SB2A-R2 — PostgreSQL evidence for the authority hardening. Disposable databases only.

  R2_PG_URL      behaviour database (upgraded to the script head here if older)
  R2_PG_MIG_URL  database at 20261008_sb2a_booking_authority or later; the lease migration
                 is downgraded, re-upgraded and checked for reversibility

Real sessions on separate connections, real transactions, real advisory locks. Every test
skips cleanly when the variables are unset.
"""
from __future__ import annotations

import os
import secrets
import sys
import threading
import unittest
from datetime import date, datetime, time, timedelta, timezone

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

PG_URL = os.environ.get("R2_PG_URL", "")
PG_MIG_URL = os.environ.get("R2_PG_MIG_URL", "")
SB2A = "20261008_sb2a_booking_authority"
R2 = "20261009_sb2a_r2_ack_lease"
FIXTURE = "SB2A-R2-PG-FIXTURE"
TRIGGERS = ("trg_revisions_sb1_attribute_professional", "trg_revisions_sb2a_booking_version")


def _alembic(url: str, action: str, target: str) -> None:
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


def _script_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(Config(os.path.join(_BACKEND, "alembic.ini"))).get_current_head()


@unittest.skipUnless(PG_URL, "R2_PG_URL not set — no disposable PostgreSQL; nothing proven")
class R2Postgres(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_URL, pool_size=12, max_overflow=4)
        with cls.engine.connect() as c:
            current = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
        if current != _script_head():
            _alembic(PG_URL, "upgrade", _script_head())

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        from app.services import booking_authority as ba
        from app.services import booking_ce_adapter as ce
        self.ba, self.ce = ba, ce
        self.day = date(2028, 1, 3) + timedelta(days=7 * (abs(hash(self.id())) % 300))
        self.threads: list[int] = []
        with self.engine.begin() as c:
            self.lead = c.execute(text(
                "INSERT INTO leads (created_at, estado, necesita_humano) "
                "VALUES (now(), 'R2', false) RETURNING id")).scalar()
            if not c.execute(text("SELECT count(*) FROM profesionales WHERE is_launch_default")).scalar():
                c.execute(text(
                    "INSERT INTO profesionales (nombre, apellido, email, created_at, resource_key, "
                    "is_launch_default, is_active) VALUES ('Fixture', :a, 'fixture@example.invalid', "
                    "now(), :k, true, true)"), {"a": FIXTURE, "k": f"r2.{secrets.token_hex(4)}"})
            self.prof = c.execute(text(
                "SELECT id FROM profesionales WHERE is_launch_default")).scalar()

    def tearDown(self):
        with self.engine.begin() as c:
            for trg in TRIGGERS:
                c.execute(text(f"ALTER TABLE revisions ENABLE TRIGGER {trg}"))
            c.execute(text("ALTER TABLE calendar_exceptions ENABLE TRIGGER trg_calendar_exceptions_sb2a_lock"))
            c.execute(text("DELETE FROM booking_commands WHERE lead_id = :l"),
                      {"l": self.lead})
            if self.threads:
                c.execute(text("DELETE FROM thread_revisions WHERE thread_id = ANY(:t)"),
                          {"t": self.threads})
                c.execute(text("DELETE FROM whatsapp_threads WHERE id = ANY(:t)"), {"t": self.threads})
                c.execute(text("DELETE FROM whatsapp_contacts WHERE display_name='R2Fixture'"))
            c.execute(text("DELETE FROM revisions WHERE lead_id = :l"), {"l": self.lead})
            c.execute(text("DELETE FROM leads WHERE id = :l OR estado = 'R2-CALLER'"), {"l": self.lead})

    # ── helpers ───────────────────────────────────────────────────────────

    def thread(self) -> int:
        with self.engine.begin() as c:
            contact = c.execute(text(
                "INSERT INTO whatsapp_contacts (wa_id, display_name, created_at) "
                "VALUES (:w, 'R2Fixture', now()) RETURNING id"),
                {"w": "54911" + str(secrets.randbelow(10**8)).zfill(8)}).scalar()
            tid = c.execute(text(
                "INSERT INTO whatsapp_threads (contact_id, lead_id, created_at) "
                "VALUES (:c, :l, now()) RETURNING id"), {"c": contact, "l": self.lead}).scalar()
        self.threads.append(tid)
        return tid

    def cmd(self, at=time(10), *, key=None, op="CREATE", day=None, **kw):
        B = self.ba
        return B.BookingCommand(operation=B.BookingOperation(op), source=B.BookingSource.OTHER,
                                idempotency_key=key, lead_id=self.lead,
                                requested_date=day or self.day, requested_time=at, **kw)

    def run_cmd(self, command, **kw):
        with Session(self.engine) as s:
            return self.ba.BookingAuthority(s, lock_timeout_ms=kw.pop("lock_timeout_ms", 3000)
                                            ).execute(command, **kw)

    def rows(self):
        with self.engine.connect() as c:
            return (c.execute(text("SELECT count(*) FROM revisions WHERE lead_id=:l"),
                              {"l": self.lead}).scalar(),
                    c.execute(text("SELECT count(*) FROM thread_revisions WHERE thread_id = ANY(:t)"),
                              {"t": self.threads or [0]}).scalar(),
                    c.execute(text("SELECT count(*) FROM booking_commands WHERE lead_id=:l"),
                              {"l": self.lead}).scalar())

    def caller_session(self):
        """A CE-like caller: a session with FLUSHED, uncommitted work of its own."""
        s = Session(self.engine)
        s.execute(text("INSERT INTO leads (created_at, estado, necesita_humano) "
                       "VALUES (now(), 'R2-CALLER', false)"))
        return s

    def caller_work_visible(self) -> int:
        with self.engine.connect() as c:
            return c.execute(text("SELECT count(*) FROM leads WHERE estado='R2-CALLER'")).scalar()

    # ══ R-01 on PostgreSQL ════════════════════════════════════════════════

    def test_pg_r2_01_partial_manual_scheduling_is_stored_non_occupying(self):
        for i, (day, at) in enumerate(((self.day, None), (None, time(10)), (None, None),
                                       (self.day, time(10)))):
            r = self.run_cmd(self.ba.BookingCommand(
                operation=self.ba.BookingOperation.REQUEST_MANUAL_SCHEDULING,
                source=self.ba.BookingSource.OTHER, idempotency_key=f"r2m{i}-{self.lead}",
                lead_id=self.lead, requested_date=day, requested_time=at))
            self.assertEqual(r.outcome.value, "COMMITTED", (day, at, r.reason))
            with self.engine.connect() as c:
                occ, during, fecha, hora, estado = c.execute(text(
                    "SELECT agenda_occupying, occupied_during, turno_fecha, turno_hora, "
                    "estado_revision FROM revisions WHERE id=:i"), {"i": r.revision_id}).one()
            self.assertEqual((occ, fecha, hora, estado), (False, day, at, "REPROGRAMAR"))
        # an occupying booking at that exact time still commits: nothing was reserved
        self.assertEqual(self.run_cmd(self.cmd()).outcome.value, "COMMITTED")

    # ══ R-02 — real isolation ═══════════════════════════════════════════════

    def test_pg_r2_02_success_does_not_commit_the_callers_pending_work(self):
        caller = self.caller_session()
        r = self.ce.request_manual_scheduling(caller, self.ce.CeSchedulingEscalationRequest(
            thread_id=self.thread(), lead_id=self.lead, cycle_key=1, requested_date=self.day))
        self.assertEqual(r.outcome.value, "COMMITTED", r.reason)
        self.assertEqual(self.caller_work_visible(), 0)           # not committed by the adapter
        caller.rollback(); caller.close()                         # later caller rollback…
        self.assertEqual(self.caller_work_visible(), 0)
        self.assertEqual(self.rows()[0], 1)                       # …is harmless to the booking

    def test_pg_r2_03_refusal_does_not_erase_the_callers_pending_work(self):
        self.assertEqual(self.run_cmd(self.cmd()).outcome.value, "COMMITTED")
        caller = self.caller_session()
        r = self.ce.book_from_flow_response(caller, self.ce.CeFlowBookingRequest(
            thread_id=self.thread(), lead_id=self.lead, inbound_event_id="wamid.R2-03",
            flow_token=None, requested_date=self.day, requested_time=time(10, 15)),
            create_thread_revision=lambda s, rev: None)
        self.assertEqual(r.outcome.value, "SLOT_CONFLICT")
        caller.commit(); caller.close()                           # the caller still owns its work
        self.assertEqual(self.caller_work_visible(), 1)

    def test_pg_r2_04_adapter_exception_leaves_the_caller_intact_and_leaks_nothing(self):
        caller = self.caller_session()
        before = self.engine.pool.checkedout()

        def boom(s, rev):
            raise RuntimeError("callback failure")
        r = self.ce.book_from_flow_response(caller, self.ce.CeFlowBookingRequest(
            thread_id=self.thread(), lead_id=self.lead, inbound_event_id="wamid.R2-04",
            flow_token=None, requested_date=self.day, requested_time=time(11)),
            create_thread_revision=boom)
        self.assertEqual(r.outcome.value, "INTERNAL_FAILURE")
        self.assertEqual(self.engine.pool.checkedout(), before)  # adapter connection returned
        caller.commit(); caller.close()
        self.assertEqual(self.caller_work_visible(), 1)
        self.assertEqual(self.rows()[0], 0)

    def test_pg_r2_05_concurrent_adapter_calls_one_event_one_record(self):
        tid = self.thread()
        barrier, out = threading.Barrier(6), []

        def go():
            with Session(self.engine) as caller:
                barrier.wait()
                out.append(self.ce.request_manual_scheduling(caller, self.ce.CeSchedulingEscalationRequest(
                    thread_id=tid, lead_id=self.lead, cycle_key=3)).outcome.value)
        ths = [threading.Thread(target=go) for _ in range(6)]
        [t.start() for t in ths]; [t.join(20) for t in ths]
        self.assertEqual(sorted(out).count("COMMITTED"), 1, out)
        self.assertTrue(set(out) <= {"COMMITTED", "IDEMPOTENT_REPLAY"}, out)
        self.assertEqual(self.rows()[0], 1)

    def test_pg_r2_06_caller_holding_a_row_lock_yields_retryable_not_a_hang(self):
        """SB2B rule evidence: if the caller has locked a row the attach callback writes, the
        adapter's own transaction cannot wait for its caller — lock_timeout turns that
        self-deadlock into RETRYABLE_FAILURE, and the caller's work is untouched."""
        caller = Session(self.engine)
        caller.execute(text("UPDATE leads SET estado='R2-LOCKED' WHERE id=:l"), {"l": self.lead})

        def attach(s, rev):
            s.execute(text("UPDATE leads SET estado='R2-ATTACH' WHERE id=:l"), {"l": self.lead})
            return None
        t0 = datetime.now()
        r = self.ce.book_from_flow_response(caller, self.ce.CeFlowBookingRequest(
            thread_id=self.thread(), lead_id=self.lead, inbound_event_id="wamid.R2-06",
            flow_token=None, requested_date=self.day, requested_time=time(12)),
            create_thread_revision=attach, lock_timeout_ms=400)
        self.assertEqual((r.outcome.value, r.reason), ("RETRYABLE_FAILURE", "LOCK_TIMEOUT"))
        self.assertLess((datetime.now() - t0).total_seconds(), 10)
        caller.commit(); caller.close()
        with self.engine.connect() as c:
            self.assertEqual(c.execute(text("SELECT estado FROM leads WHERE id=:l"),
                                       {"l": self.lead}).scalar(), "R2-LOCKED")
        self.assertEqual(self.rows()[0], 0)

    def test_pg_r2_07_advisory_lock_held_elsewhere_is_retryable(self):
        ymd = int(self.day.strftime("%Y%m%d"))
        with self.engine.connect() as holder:
            holder.begin()
            holder.execute(text("SELECT pg_advisory_xact_lock(:p, :d)"), {"p": self.prof, "d": ymd})
            caller = self.caller_session()
            r = self.ce.book_from_flow_response(caller, self.ce.CeFlowBookingRequest(
                thread_id=self.thread(), lead_id=self.lead, inbound_event_id="wamid.R2-07",
                flow_token=None, requested_date=self.day, requested_time=time(13)),
                create_thread_revision=lambda s, rev: None, lock_timeout_ms=300)
            holder.rollback()
        self.assertEqual(r.outcome.value, "RETRYABLE_FAILURE")
        caller.commit(); caller.close()
        self.assertEqual(self.caller_work_visible(), 1)

    # ══ R-04 — hostile callbacks against the real database ═════════════════

    def assertViolation(self, r, fragment):
        self.assertEqual(r.outcome.value, "INTERNAL_FAILURE")
        self.assertTrue(r.reason.startswith("CALLBACK_VIOLATION:"), r.reason)
        self.assertIn(fragment, r.reason)
        self.assertEqual(self.rows()[0::2], (0, 0))               # zero partial rows

    def test_pg_r2_08_c01_callback_commits_then_raises(self):
        def attach(s, rev):
            s.commit()
            raise RuntimeError("after commit")
        self.assertViolation(self.run_cmd(self.cmd(key=f"c01{self.lead}"), attach=attach),
                             "session.commit")

    def test_pg_r2_09_c02_callback_adds_a_revision_and_pads_the_status(self):
        from app.models import Revision

        def attach(s, rev):
            s.add(Revision(lead_id=self.lead, turno_fecha=self.day, turno_hora=time(15),
                           estado_revision=" confirmado "))
            rev.estado_revision = " cancelado "
            return None
        self.assertViolation(self.run_cmd(self.cmd(key=f"c02{self.lead}"), attach=attach), "attach:")

    def test_pg_r2_10_raw_sql_writes_are_caught_by_the_database_check(self):
        tid = self.thread()
        cases = {
            "wrote_another_revision": lambda s, rev: s.execute(text(
                "INSERT INTO revisions (lead_id, created_at, estado_revision, turno_fecha, turno_hora) "
                "VALUES (:l, now(), 'CONFIRMADO', :d, '16:00')"), {"l": self.lead, "d": self.day}),
            "booking_fields_changed": lambda s, rev: s.execute(text(
                "UPDATE revisions SET estado_revision=' cancelado ' WHERE id=:i"), {"i": rev.id}),
            "thread_revision_elsewhere": lambda s, rev: s.execute(text(
                "INSERT INTO thread_revisions (thread_id, status, scheduled_date, scheduled_time, "
                "created_at) VALUES (:t, 'booked', :d, '17:00', now())"), {"t": tid, "d": self.day}),
        }
        for kind, attach in cases.items():
            r = self.run_cmd(self.cmd(), attach=attach)
            self.assertViolation(r, kind)
            self.assertEqual(self.rows()[1], 0, kind)

    def test_pg_r2_11_trigger_and_transaction_sql_and_side_connections_are_refused(self):
        hostile = {
            "sql:SET": lambda s, rev: s.execute(text("SET session_replication_role = replica")),
            "sql:SAVEPOINT": lambda s, rev: s.execute(text("SAVEPOINT x")),
            "sql:COMMIT": lambda s, rev: s.execute(text("COMMIT")),
            "sql:ALTER": lambda s, rev: s.execute(text(
                "ALTER TABLE revisions DISABLE TRIGGER trg_revisions_sb2a_booking_version")),
            "engine.new_connection": lambda s, rev: self.engine.connect().close(),
            "session.begin_nested": lambda s, rev: s.begin_nested(),
        }
        for frag, attach in hostile.items():
            self.assertViolation(self.run_cmd(self.cmd(), attach=attach), frag)
        with self.engine.connect() as c:                          # nothing got disabled
            self.assertEqual(c.execute(text(
                "SELECT tgenabled FROM pg_trigger WHERE tgname='trg_revisions_sb2a_booking_version'"
            )).scalar(), "O")

    def test_pg_r2_12_guard_is_per_context_other_threads_book_normally(self):
        parked, release, out = threading.Event(), threading.Event(), {}

        def attach(s, rev):
            parked.set(); release.wait(10)
            return None

        def holder():
            out["h"] = self.run_cmd(self.cmd(time(10)), attach=attach).outcome.value

        th = threading.Thread(target=holder); th.start()
        self.assertTrue(parked.wait(10))
        other_day = self.day + timedelta(days=1)
        out["o"] = self.run_cmd(self.cmd(time(10), day=other_day)).outcome.value
        release.set(); th.join(15)
        self.assertEqual(out, {"h": "COMMITTED", "o": "COMMITTED"})
        self.assertIsNone(self.ba.booking_callback_active())

    def test_pg_r2_13_booked_thread_revision_at_the_slot_still_commits(self):
        tid = self.thread()
        from app.models import ThreadRevision

        def attach(s, rev):
            t = ThreadRevision(thread_id=tid, status="booked", scheduled_date=rev.turno_fecha,
                               scheduled_time=rev.turno_hora)
            s.add(t); s.flush()
            return t
        r = self.run_cmd(self.cmd(key=f"ok{self.lead}"), attach=attach)
        self.assertEqual(r.outcome.value, "COMMITTED", r.reason)
        self.assertEqual(self.rows(), (1, 1, 1))

    # ══ R-03 — lease under real concurrency ═══════════════════════════════════

    def _tracked(self, key):
        r = self.run_cmd(self.cmd(key=key, tracks_acknowledgement=True))
        self.assertEqual(r.ack_status, "PENDING")

    def _race_claims(self, key, n=8):
        barrier, won = threading.Barrier(n), []

        def go():
            with Session(self.engine) as s:
                barrier.wait()
                c = self.ba.BookingAuthority(s).claim_acknowledgement(key)
                if c is not None:
                    won.append(c)
        ths = [threading.Thread(target=go) for _ in range(n)]
        [t.start() for t in ths]; [t.join(20) for t in ths]
        return won

    def test_pg_r2_14_concurrent_pending_claimers_one_sender(self):
        key = f"ack-p-{self.lead}"
        self._tracked(key)
        won = self._race_claims(key)
        self.assertEqual(len(won), 1)
        self.assertFalse(won[0].duplicate_risk)

    def test_pg_r2_15_concurrent_reclaimers_of_an_expired_lease_one_sender(self):
        key = f"ack-x-{self.lead}"
        self._tracked(key)
        with Session(self.engine) as s:                          # a claimant that died
            dead = self.ba.BookingAuthority(s).claim_acknowledgement(
                key, now=datetime.now(timezone.utc) - timedelta(seconds=self.ba.ACK_LEASE_SECONDS + 5))
        self.assertIsNotNone(dead)
        won = self._race_claims(key)
        self.assertEqual(len(won), 1)
        self.assertTrue(won[0].duplicate_risk)
        with Session(self.engine) as s:
            auth = self.ba.BookingAuthority(s)
            self.assertFalse(auth.record_acknowledgement(dead, sent=True))
            self.assertTrue(auth.record_acknowledgement(won[0], sent=True, transport_id="wamid.T"))
        with self.engine.connect() as c:
            row = c.execute(text("SELECT ack_status, ack_attempts, ack_duplicate_risk, "
                                 "ack_transport_id FROM booking_commands WHERE idempotency_key=:k"),
                            {"k": key}).one()
        self.assertEqual(tuple(row), ("SENT", 2, True, "wamid.T"))

    def test_pg_r2_16_active_lease_is_not_stolen_and_sent_is_final(self):
        key = f"ack-a-{self.lead}"
        self._tracked(key)
        with Session(self.engine) as s:
            auth = self.ba.BookingAuthority(s)
            c = auth.claim_acknowledgement(key)
            self.assertIsNone(auth.claim_acknowledgement(key))
            auth.record_acknowledgement(c, sent=True, transport_id="wamid.S")
        self.assertEqual(self._race_claims(key), [])

    # ══ R-07 / H10 — per-transaction preflight ═════════════════════════════════

    def test_pg_r2_17_trigger_disabled_after_a_booking_stops_the_next_one_same_process(self):
        self.assertEqual(self.run_cmd(self.cmd(time(9))).outcome.value, "COMMITTED")
        for trg in TRIGGERS:
            for mode in ("DISABLE TRIGGER", "ENABLE REPLICA TRIGGER"):
                with self.engine.begin() as c:
                    c.execute(text(f"ALTER TABLE revisions {mode} {trg}"))
                r = self.run_cmd(self.cmd(time(14)))
                self.assertEqual((r.outcome.value, r.reason),
                                 ("INTERNAL_FAILURE", "CRITICAL_TRIGGER_DISABLED"), (trg, mode))
                with self.engine.begin() as c:
                    c.execute(text(f"ALTER TABLE revisions ENABLE TRIGGER {trg}"))
        with self.engine.begin() as c:
            c.execute(text("ALTER TABLE calendar_exceptions DISABLE TRIGGER trg_calendar_exceptions_sb2a_lock"))
        r = self.run_cmd(self.cmd(time(14)))
        self.assertEqual(r.reason, "CRITICAL_TRIGGER_DISABLED")
        with self.engine.begin() as c:
            c.execute(text("ALTER TABLE calendar_exceptions ENABLE TRIGGER trg_calendar_exceptions_sb2a_lock"))
        self.assertEqual(self.run_cmd(self.cmd(time(14))).outcome.value, "COMMITTED")
        self.assertEqual(self.rows()[0], 2)

    def test_pg_r2_18_replica_session_is_refused_every_time(self):
        # one fixed connection: a session-level SET belongs to the connection, not the pool
        with self.engine.connect() as conn:
            s = Session(bind=conn, join_transaction_mode="rollback_only")
            auth = self.ba.BookingAuthority(s)
            self.assertEqual(auth.execute(self.cmd(time(9))).outcome.value, "COMMITTED")
            conn.execute(text("SET session_replication_role = replica")); conn.commit()
            r = auth.execute(self.cmd(time(15)))
            self.assertEqual((r.outcome.value, r.reason),
                             ("INTERNAL_FAILURE", "TRIGGERS_DISABLED_FOR_SESSION"))
            conn.rollback()
            conn.execute(text("SET session_replication_role = origin")); conn.commit()
            s.close()

    # ══ R-05 — PATCH on the real schema; schedule.py stays blind to it ═════════

    def test_pg_r2_19_patch_cannot_create_schedule_visible_occupancy(self):
        from fastapi import HTTPException
        from app.repositories.thread_revisions import ThreadRevisionRepository
        from app.schemas.thread_revisions import ThreadRevisionPatch
        from app.services.thread_revisions import ThreadRevisionService
        tid = self.thread()
        with Session(self.engine) as s:
            tr = s.execute(text(
                "INSERT INTO thread_revisions (thread_id, status, scheduled_date, scheduled_time, "
                "created_at) VALUES (:t, 'collecting_data', :d, '10:00', now()) RETURNING id"),
                {"t": tid, "d": self.day}).scalar()
            s.commit()
            svc = ThreadRevisionService(repository=ThreadRevisionRepository(db=s))
            with self.assertRaises(HTTPException) as ctx:
                svc.patch_revision(tr, ThreadRevisionPatch(status="booked"))
            self.assertEqual(ctx.exception.status_code, 409)
            s.rollback()
            ok = svc.patch_revision(tr, ThreadRevisionPatch(buyer_email="x@example.invalid"))
            self.assertEqual(ok.status, "collecting_data")
        with self.engine.connect() as c:
            self.assertEqual(c.execute(text(
                "SELECT count(*) FROM thread_revisions WHERE thread_id=:t AND status IN "
                "('booked','completed')"), {"t": tid}).scalar(), 0)

    # ══ H1 — complete fingerprint on PostgreSQL ════════════════════════════════

    def test_pg_r2_21_key_reuse_with_any_different_semantic_field_is_refused(self):
        B = self.ba
        tid, tid2 = self.thread(), self.thread()
        with self.engine.begin() as c:
            lead2 = c.execute(text("INSERT INTO leads (created_at, estado, necesita_humano) "
                                   "VALUES (now(), 'R2-CALLER', false) RETURNING id")).scalar()
            prof2 = c.execute(text(
                "INSERT INTO profesionales (nombre, apellido, email, created_at, is_launch_default, "
                "is_active) VALUES ('Fixture', :a, 'fixture@example.invalid', now(), false, true) "
                "RETURNING id"), {"a": FIXTURE}).scalar()
        key = f"fp-{self.lead}"
        base = dict(operation=B.BookingOperation.CREATE, source=B.BookingSource.OTHER,
                    idempotency_key=key, lead_id=self.lead, thread_id=tid,
                    profesional_id=self.prof, requested_date=self.day, requested_time=time(10),
                    target_status="PENDIENTE")
        first = self.run_cmd(B.BookingCommand(**base))
        self.assertEqual(first.outcome.value, "COMMITTED", first.reason)
        variants = {"lead": {"lead_id": lead2}, "thread": {"thread_id": tid2},
                    "revision": {"revision_id": first.revision_id},
                    "operation": {"operation": B.BookingOperation.REQUEST_MANUAL_SCHEDULING},
                    "professional": {"profesional_id": prof2},
                    "slot": {"requested_time": time(11)},
                    "status": {"target_status": "CONFIRMADO"}}
        try:
            for name, change in variants.items():
                r = self.run_cmd(B.BookingCommand(**{**base, **change}))
                self.assertEqual((r.outcome.value, r.reason),
                                 ("VALIDATION_FAILED", "IDEMPOTENCY_KEY_REUSED"), name)
                self.assertIsNone(r.revision_id, name)         # never another request's result
            again = self.run_cmd(B.BookingCommand(**base))
            self.assertEqual((again.outcome.value, again.revision_id),
                             ("IDEMPOTENT_REPLAY", first.revision_id))
            self.assertEqual(self.rows()[0], 1)
        finally:
            with self.engine.begin() as c:
                c.execute(text("DELETE FROM profesionales WHERE id=:p"), {"p": prof2})

    # ══ R-09 — empty CE identity on PostgreSQL ══════════════════════════════════

    def test_pg_r2_22_empty_ce_identity_writes_nothing_and_distinct_events_stay_distinct(self):
        tid = self.thread()
        for blank in (None, "", "  "):
            r = self.ce.book_from_flow_response(Session(self.engine), self.ce.CeFlowBookingRequest(
                thread_id=tid, lead_id=self.lead, inbound_event_id=blank, flow_token=blank,
                requested_date=self.day, requested_time=time(10)),
                create_thread_revision=lambda s, rev: None)
            self.assertEqual((r.outcome.value, r.reason), ("VALIDATION_FAILED", "EVENT_IDENTITY_REQUIRED"))
            r = self.ce.request_manual_scheduling(Session(self.engine), self.ce.CeSchedulingEscalationRequest(
                thread_id=tid, lead_id=self.lead, escalation_id=blank, inbound_event_id=blank))
            self.assertEqual(r.reason, "EVENT_IDENTITY_REQUIRED")
        self.assertEqual(self.rows(), (0, 0, 0))
        a = self.ce.request_manual_scheduling(Session(self.engine), self.ce.CeSchedulingEscalationRequest(
            thread_id=tid, lead_id=self.lead, inbound_event_id="wamid.A"))
        b = self.ce.request_manual_scheduling(Session(self.engine), self.ce.CeSchedulingEscalationRequest(
            thread_id=tid, lead_id=self.lead, inbound_event_id="wamid.B"))
        self.assertEqual((a.outcome.value, b.outcome.value), ("COMMITTED", "COMMITTED"))
        self.assertNotEqual(a.revision_id, b.revision_id)


@unittest.skipUnless(PG_MIG_URL, "R2_PG_MIG_URL not set — nothing proven")
class R2LeaseMigration(unittest.TestCase):

    def test_pg_r2_20_lease_migration_is_additive_and_reversible(self):
        eng = create_engine(PG_MIG_URL)
        cols = ("ack_claim_token", "ack_claimed_at", "ack_lease_expires_at", "ack_transport_id",
                "ack_last_error", "ack_duplicate_risk")

        def present():
            with eng.connect() as c:
                return set(c.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='booking_commands'")).scalars())

        def ver():
            with eng.connect() as c:
                return c.execute(text("SELECT version_num FROM alembic_version")).scalar()
        try:
            _alembic(PG_MIG_URL, "upgrade", R2)
            self.assertEqual(ver(), R2)
            base = present()
            self.assertTrue(set(cols) <= base)
            _alembic(PG_MIG_URL, "downgrade", SB2A)
            self.assertEqual(ver(), SB2A)
            self.assertEqual(present(), base - set(cols))
            _alembic(PG_MIG_URL, "upgrade", R2)
            self.assertEqual(present(), base)
            with eng.connect() as c:                              # default is false, not null
                self.assertEqual(c.execute(text(
                    "SELECT is_nullable, column_default FROM information_schema.columns "
                    "WHERE table_name='booking_commands' AND column_name='ack_duplicate_risk'")).one(),
                    ("NO", "false"))
        finally:
            eng.dispose()


if __name__ == "__main__":
    unittest.main()
