"""SB2A — PostgreSQL certification of the booking authority. Disposable databases only.

  SB2A_PG_URL        behaviour database; upgraded to head here if at an older revision.
  SB2A_PG_MIG_URL    database at 20261007_sb1_booking_repr with representative rows; the
                     migration class upgrades, refuses/permits downgrade and re-upgrades it.
  SB2A_PG_STAMP_URL  a database built by create_all + `alembic stamp head` — the bootstrap
                     path that silently lacks every SB1/SB2A database object.

Races use real sessions on separate connections. The attach hook can PARK a transaction
after its locks and its write and before its commit, so the contender provably runs while
the holder is open — timing is asserted, not assumed. All skip cleanly when unset.
"""
from __future__ import annotations

import os
import secrets
import sys
import threading
import time as _t
import unittest
from datetime import date, time, timedelta
from unittest import mock

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

PG_URL = os.environ.get("SB2A_PG_URL", "")
PG_MIG_URL = os.environ.get("SB2A_PG_MIG_URL", "")
PG_STAMP_URL = os.environ.get("SB2A_PG_STAMP_URL", "")
HEAD = "20261008_sb2a_booking_authority"
_DEFAULT = object()
SB1 = "20261007_sb1_booking_repr"
FIXTURE = "SB2A-PG-FIXTURE"


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


def _version(conn) -> str:
    return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _state(exc):
    return getattr(getattr(exc, "orig", None), "sqlstate", None)


# ═════════════════════════════════════════════════════════════════════════════

@unittest.skipUnless(PG_URL, "SB2A_PG_URL not set — no disposable PostgreSQL; nothing proven")
class SB2AAuthorityPostgres(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_URL, pool_size=10)
        with cls.engine.connect() as c:
            current = _version(c)
        if current != HEAD:
            _alembic(PG_URL, "upgrade", HEAD)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        from app.services import booking_authority as ba
        self.ba = ba
        self.day = date(2027, 1, 4) + timedelta(days=7 * (hash(self.id()) % 400))
        self.threads: list[int] = []
        with self.engine.begin() as c:
            self.lead = c.execute(text(
                "INSERT INTO leads (created_at, estado, necesita_humano) "
                "VALUES (now(), 'X', false) RETURNING id")).scalar()

    def make_thread(self) -> int:
        with self.engine.begin() as c:
            contact = c.execute(text(
                "INSERT INTO whatsapp_contacts (wa_id, display_name, created_at) "
                "VALUES (:w, 'Fixture', now()) RETURNING id"),
                {"w": "54911" + str(secrets.randbelow(10**8)).zfill(8)}).scalar()
            tid = c.execute(text(
                "INSERT INTO whatsapp_threads (contact_id, lead_id, created_at) "
                "VALUES (:c, :l, now()) RETURNING id"), {"c": contact, "l": self.lead}).scalar()
        self.threads.append(tid)
        return tid

    def tearDown(self):
        with self.engine.begin() as c:
            c.execute(text("DELETE FROM booking_commands"))
            if self.threads:
                c.execute(text("DELETE FROM thread_revisions WHERE thread_id = ANY(:t)"),
                          {"t": self.threads})
                c.execute(text("DELETE FROM whatsapp_threads WHERE id = ANY(:t) RETURNING contact_id"),
                          {"t": self.threads})
                c.execute(text("DELETE FROM whatsapp_contacts WHERE display_name='Fixture' AND id "
                               "NOT IN (SELECT contact_id FROM whatsapp_threads)"))
            c.execute(text("DELETE FROM revisions WHERE lead_id = :l"), {"l": self.lead})
            c.execute(text("DELETE FROM calendar_exceptions WHERE reason = 'sb2a-test'"))
            c.execute(text("DELETE FROM revisions WHERE profesional_id IN "
                           "(SELECT id FROM profesionales WHERE apellido = :a)"), {"a": FIXTURE})
            c.execute(text("DELETE FROM profesionales WHERE apellido = :a"), {"a": FIXTURE})
            c.execute(text("DELETE FROM leads WHERE id = :l"), {"l": self.lead})

    # ── helpers ───────────────────────────────────────────────────────────

    def prof(self, *, default=False, active=True):
        with self.engine.begin() as c:
            return c.execute(text(
                "INSERT INTO profesionales (nombre, apellido, email, created_at, resource_key, "
                "is_launch_default, is_active) VALUES ('Fixture', :a, 'fixture@example.invalid', "
                "now(), :k, :d, :act) RETURNING id"),
                {"a": FIXTURE, "k": f"fx.{secrets.token_hex(4)}" if default else None,
                 "d": default, "act": active}).scalar()

    def cmd(self, at, *, op="CREATE", key=None, prof=None, rev=None, ver=None, day=_DEFAULT, **kw):
        B = self.ba
        if day is _DEFAULT:
            day = self.day if at is not None else None
        return B.BookingCommand(operation=B.BookingOperation(op), source=B.BookingSource.OTHER,
                                idempotency_key=key, lead_id=self.lead, revision_id=rev,
                                profesional_id=prof, requested_date=day,
                                requested_time=at, expected_version=ver, **kw)

    def run_cmd(self, command, *, lock_timeout_ms=5000, **kw):
        with Session(self.engine) as s:
            return self.ba.BookingAuthority(s, lock_timeout_ms=lock_timeout_ms).execute(command, **kw)

    def race(self, holder_cmd, contender_cmd, *, contender_kw=None, park=1.0):
        """Run holder; park it after its locks+write, start contender, release, collect."""
        parked, release, out = threading.Event(), threading.Event(), {}
        stamps = {}

        def park_attach(db, rev):
            parked.set()
            release.wait(10)
            return None

        def holder():
            out["holder"] = self.run_cmd(holder_cmd, attach=park_attach)
            stamps["holder_done"] = _t.monotonic()

        def contender():
            stamps["contender_start"] = _t.monotonic()
            out["contender"] = self.run_cmd(contender_cmd, **(contender_kw or {}))
            stamps["contender_done"] = _t.monotonic()

        th = threading.Thread(target=holder); th.start()
        self.assertTrue(parked.wait(10), "holder never reached its write")
        tc = threading.Thread(target=contender); tc.start()
        _t.sleep(park)
        stamps["contender_alive_while_parked"] = tc.is_alive()
        stamps["released"] = _t.monotonic()
        release.set()
        th.join(15); tc.join(15)
        return out["holder"], out["contender"], stamps

    def rows(self, sql, **p):
        with self.engine.connect() as c:
            return c.execute(text(sql), p).all()

    # ── 1, 15: same professional, overlapping interval ─────────────────────

    def test_pg01_overlap_race_one_commit_one_typed_conflict(self):
        p = self.prof(default=True)
        revalidated_at = {}

        def revalidate(db, target):
            revalidated_at["t"] = _t.monotonic()
            return None

        h, c, st = self.race(self.cmd(time(10)), self.cmd(time(10, 30)),
                             contender_kw={"revalidate": revalidate})
        self.assertEqual(h.outcome.value, "COMMITTED")
        self.assertEqual((c.outcome.value, c.reason), ("SLOT_CONFLICT", "OVERLAP"))
        self.assertTrue(st["contender_alive_while_parked"], "contender must wait on the lock")
        self.assertGreaterEqual(st["contender_done"], st["released"])
        self.assertNotIn("t", revalidated_at)        # never got past the lock + pre-check
        self.assertEqual(h.lock_mode, "pg-advisory-xact")
        self.assertEqual(len(self.rows("SELECT id FROM revisions WHERE lead_id=:l", l=self.lead)), 1)

    def test_pg02_bypassing_writer_loses_to_the_exclusion_constraint(self):
        """A writer that skips the locks and the pre-check still cannot commit an overlap:
        it blocks on the holder's uncommitted row and then fails 23P01 — translated."""
        p = self.prof(default=True)
        B = self.ba.BookingAuthority
        with mock.patch.object(B, "_acquire_locks", lambda self_, p_, t_: "bypassed"), \
             mock.patch.object(B, "_check_overlap", lambda self_, *a, **k: None):
            h, c, st = self.race(self.cmd(time(10)), self.cmd(time(10, 15)))
        self.assertEqual(h.outcome.value, "COMMITTED")
        self.assertEqual((c.outcome.value, c.reason), ("SLOT_CONFLICT", "EXCLUSION_CONSTRAINT"))
        self.assertTrue(st["contender_alive_while_parked"])
        with self.engine.begin() as conn:            # and raw SQL, no application at all
            with self.assertRaises(DBAPIError) as ctx:
                conn.execute(text(
                    "INSERT INTO revisions (lead_id, created_at, estado_revision, turno_fecha, "
                    "turno_hora, profesional_id) VALUES (:l, now(), 'PENDIENTE', :d, '10:30', :p)"),
                    {"l": self.lead, "d": self.day, "p": p})
            self.assertEqual(_state(ctx.exception), "23P01")

    # ── 2, 3: adjacency and different professionals ───────────────────────

    def test_pg03_back_to_back_both_commit(self):
        self.prof(default=True)
        h, c, st = self.race(self.cmd(time(10)), self.cmd(time(10, 45)))
        self.assertEqual((h.outcome.value, c.outcome.value), ("COMMITTED", "COMMITTED"))

    def test_pg04_different_professionals_same_interval_do_not_block(self):
        a, b = self.prof(default=True), self.prof()
        h, c, st = self.race(self.cmd(time(10), prof=a), self.cmd(time(10), prof=b))
        self.assertEqual((h.outcome.value, c.outcome.value), ("COMMITTED", "COMMITTED"))
        self.assertFalse(st["contender_alive_while_parked"],
                         "another professional's lock must not serialize this booking")

    # ── 4: non-occupying statuses ──────────────────────────────────────────

    def test_pg05_cancelled_and_reschedule_states_do_not_occupy(self):
        self.prof(default=True)
        first = self.run_cmd(self.cmd(time(10)))
        self.assertEqual(self.run_cmd(self.cmd(time(10))).outcome.value, "SLOT_CONFLICT")
        res = self.run_cmd(self.cmd(None, op="CANCEL", rev=first.revision_id))
        self.assertEqual((res.outcome.value, res.occupying), ("COMMITTED", False), res.reason)
        second = self.run_cmd(self.cmd(time(10)))                 # CANCELADO freed the slot
        self.assertEqual(second.outcome.value, "COMMITTED")
        res = self.run_cmd(self.cmd(None, op="MARK_FOR_RESCHEDULE", rev=second.revision_id))
        self.assertEqual((res.outcome.value, res.occupying), ("COMMITTED", False), res.reason)
        third = self.run_cmd(self.cmd(time(10)))                  # REPROGRAMAR freed it too
        self.assertEqual(third.outcome.value, "COMMITTED")
        self.assertEqual(len(self.rows(
            "SELECT id FROM revisions WHERE lead_id=:l AND agenda_occupying", l=self.lead)), 1)

    # ── 5, 6: professional assignment ──────────────────────────────────────

    def test_pg06_missing_and_inactive_professionals_fail_closed(self):
        r = self.run_cmd(self.cmd(time(9)))
        self.assertEqual((r.outcome.value, r.reason), ("MISSING_PROFESSIONAL", "NO_LAUNCH_DEFAULT"))
        inactive_default = self.prof(default=True, active=False)
        r = self.run_cmd(self.cmd(time(9)))
        self.assertEqual(r.reason, "LAUNCH_DEFAULT_INACTIVE")
        r = self.run_cmd(self.cmd(time(9), prof=inactive_default))
        self.assertEqual(r.reason, "PROFESSIONAL_INACTIVE")
        self.assertEqual(self.rows("SELECT id FROM revisions WHERE lead_id=:l", l=self.lead), [])

    # ── 7, 8: calendar exceptions, including a racing exception ───────────

    def test_pg07_closed_date_and_partial_interval(self):
        p = self.prof(default=True)
        with Session(self.engine) as s:
            self.ba.create_calendar_exception(s, kind="CLOSED_DATE", exception_date=self.day,
                                              reason="sb2a-test")
            self.ba.create_calendar_exception(s, kind="UNAVAILABLE_INTERVAL",
                                              exception_date=self.day + timedelta(1),
                                              start_time=time(12), end_time=time(13),
                                              reason="sb2a-test")
        self.assertEqual(self.run_cmd(self.cmd(time(10))).reason, "CLOSED_DATE")
        nxt = self.day + timedelta(1)
        self.assertEqual(self.run_cmd(self.cmd(time(12, 30), day=nxt)).reason, "UNAVAILABLE_INTERVAL")
        self.assertEqual(self.run_cmd(self.cmd(time(11, 15), day=nxt)).outcome.value, "COMMITTED")
        self.assertEqual(self.run_cmd(self.cmd(time(13), day=nxt)).outcome.value, "COMMITTED")

    def test_pg08_an_exception_cannot_race_past_a_booking(self):
        """The exception's trigger takes the per-date lock EXCLUSIVE; a booking holds it SHARED.
        An exception written while a booking is mid-transaction waits for it; a booking
        attempted while an exception is uncommitted waits, then sees it."""
        self.prof(default=True)
        parked, release, out = threading.Event(), threading.Event(), {}

        def park(db, rev):
            parked.set(); release.wait(10)

        th = threading.Thread(target=lambda: out.setdefault(
            "b", self.run_cmd(self.cmd(time(10)), attach=park)))
        th.start(); self.assertTrue(parked.wait(10))

        def add_exception():
            with self.engine.begin() as c:
                c.execute(text("INSERT INTO calendar_exceptions (kind, exception_date, reason) "
                               "VALUES ('CLOSED_DATE', :d, 'sb2a-test')"), {"d": self.day})
            out["ex_done"] = _t.monotonic()

        te = threading.Thread(target=add_exception); te.start()
        _t.sleep(1.0)
        self.assertTrue(te.is_alive(), "exception insert must wait for the open booking")
        released = _t.monotonic(); release.set()
        th.join(10); te.join(10)
        self.assertEqual(out["b"].outcome.value, "COMMITTED")
        self.assertGreaterEqual(out["ex_done"], released)
        # now the date is closed: a later booking is refused; the existing one is untouched
        self.assertEqual(self.run_cmd(self.cmd(time(14))).reason, "CLOSED_DATE")
        self.assertEqual(len(self.rows(
            "SELECT id FROM revisions WHERE lead_id=:l AND agenda_occupying", l=self.lead)), 1)

    # ── 9, 10, 11: idempotency and acknowledgement ─────────────────────────

    def test_pg09_concurrent_duplicate_returns_the_original(self):
        self.prof(default=True)
        h, c, st = self.race(self.cmd(time(10), key="dup-1"), self.cmd(time(10), key="dup-1"))
        self.assertEqual(h.outcome.value, "COMMITTED")
        self.assertEqual((c.outcome.value, c.revision_id), ("IDEMPOTENT_REPLAY", h.revision_id))
        self.assertEqual(len(self.rows("SELECT id FROM revisions WHERE lead_id=:l", l=self.lead)), 1)

    def test_pg10_two_distinct_legitimate_bookings(self):
        self.prof(default=True)
        a = self.run_cmd(self.cmd(time(9), key="rev-a", tracks_acknowledgement=True))
        b = self.run_cmd(self.cmd(time(11), key="rev-b", tracks_acknowledgement=True))
        self.assertEqual((a.outcome.value, b.outcome.value), ("COMMITTED", "COMMITTED"))
        self.assertNotEqual(a.revision_id, b.revision_id)
        with Session(self.engine) as s:
            auth = self.ba.BookingAuthority(s)
            self.assertTrue(auth.claim_acknowledgement("rev-a"))
            self.assertTrue(auth.claim_acknowledgement("rev-b"))    # each its own ack

    def test_pg11_ack_failure_then_retry_creates_no_duplicate(self):
        self.prof(default=True)
        first = self.run_cmd(self.cmd(time(10), key="ack-x", tracks_acknowledgement=True))
        with Session(self.engine) as s:
            auth = self.ba.BookingAuthority(s)
            self.assertTrue(auth.claim_acknowledgement("ack-x"))
            auth.record_acknowledgement("ack-x", sent=False)        # transport failure
        retry = self.run_cmd(self.cmd(time(10), key="ack-x", tracks_acknowledgement=True))
        self.assertEqual((retry.outcome.value, retry.revision_id, retry.ack_status),
                         ("IDEMPOTENT_REPLAY", first.revision_id, "FAILED"))
        claims = []
        def claim():
            with Session(self.engine) as s:
                claims.append(self.ba.BookingAuthority(s).claim_acknowledgement("ack-x"))
        ts = [threading.Thread(target=claim) for _ in range(4)]
        [t.start() for t in ts]; [t.join(10) for t in ts]
        self.assertEqual(sorted(claims), [False, False, False, True])   # exactly one resend
        self.assertEqual(len(self.rows("SELECT id FROM revisions WHERE lead_id=:l", l=self.lead)), 1)

    # ── 12: stale reschedule ───────────────────────────────────────────────

    def test_pg12_concurrent_reschedules_one_wins_one_is_stale(self):
        self.prof(default=True)
        booked = self.run_cmd(self.cmd(time(9)))
        h, c, st = self.race(
            self.cmd(time(11), op="RESCHEDULE", rev=booked.revision_id, ver=0),
            self.cmd(time(15), op="RESCHEDULE", rev=booked.revision_id, ver=0))
        self.assertEqual(h.outcome.value, "COMMITTED")
        self.assertEqual((c.outcome.value, c.booking_version), ("STALE_VERSION", 1))
        self.assertEqual(self.rows("SELECT turno_hora, booking_version FROM revisions WHERE id=:i",
                                   i=booked.revision_id)[0], (time(11), 1))

    def test_pg12b_bypass_writer_still_bumps_the_version(self):
        self.prof(default=True)
        booked = self.run_cmd(self.cmd(time(9)))
        with self.engine.begin() as c:                   # a writer not yet cut over (CE)
            c.execute(text("UPDATE revisions SET turno_hora='09:45' WHERE id=:i"),
                      {"i": booked.revision_id})
        stale = self.run_cmd(self.cmd(time(16), op="RESCHEDULE", rev=booked.revision_id, ver=0))
        self.assertEqual((stale.outcome.value, stale.booking_version), ("STALE_VERSION", 1))

    # ── 13: rollback ───────────────────────────────────────────────────────

    def test_pg13_rollback_leaves_no_partial_combination(self):
        self.prof(default=True)
        tid = self.make_thread()
        with Session(self.engine) as s:
            def attach(db, rev):
                db.execute(text("INSERT INTO thread_revisions (thread_id, status, created_at) "
                                "VALUES (:t, 'booked', now())"), {"t": tid})
                raise RuntimeError("fail after the write")
            r = self.ba.BookingAuthority(s).execute(self.cmd(time(10), key="rb-1"), attach=attach)
            self.assertEqual(r.outcome.value, "INTERNAL_FAILURE")
            self.assertEqual(s.execute(text("SELECT count(*) FROM revisions WHERE lead_id=:l"),
                                       {"l": self.lead}).scalar(), 0)
        self.assertEqual(self.rows("SELECT id FROM revisions WHERE lead_id=:l", l=self.lead), [])
        self.assertEqual(self.rows("SELECT id FROM thread_revisions WHERE thread_id=:t", t=tid), [])
        self.assertEqual(self.rows("SELECT id FROM booking_commands WHERE idempotency_key='rb-1'"), [])

    # ── 14: advisory-lock failure ──────────────────────────────────────────

    def test_pg14_lock_failure_is_surfaced_never_swallowed(self):
        p = self.prof(default=True)
        ymd = self.day.year * 10000 + self.day.month * 100 + self.day.day
        with self.engine.connect() as holder:
            holder.execute(text("SELECT pg_advisory_lock(:p, :d)"), {"p": p, "d": ymd})
            try:
                r = self.run_cmd(self.cmd(time(10)), lock_timeout_ms=300)
            finally:
                holder.execute(text("SELECT pg_advisory_unlock(:p, :d)"), {"p": p, "d": ymd})
                holder.commit()
        self.assertEqual((r.outcome.value, r.reason, r.retryable),
                         ("RETRYABLE_FAILURE", "LOCK_TIMEOUT", True))
        self.assertEqual(self.rows("SELECT id FROM revisions WHERE lead_id=:l", l=self.lead), [])

    # ── role, replication and schema guards ────────────────────────────────

    def test_pg15_session_with_triggers_disabled_is_refused(self):
        self.prof(default=True)
        with Session(self.engine) as s:
            s.execute(text("SET session_replication_role = replica"))
            r = self.ba.BookingAuthority(s).execute(self.cmd(time(10)))
            s.execute(text("RESET session_replication_role"))
        self.assertEqual((r.outcome.value, r.reason),
                         ("INTERNAL_FAILURE", "TRIGGERS_DISABLED_FOR_SESSION"))

    def test_pg16_a_non_superuser_role_is_sufficient_and_cannot_bypass(self):
        """The bounded enforcement SB2A can prove locally: the authority works under a plain
        role that can neither disable triggers nor switch replication role."""
        self.prof(default=True)
        role, pw = f"sb2a_app_{secrets.token_hex(3)}", secrets.token_urlsafe(18)
        with self.engine.begin() as c:
            # utility statements take no bind parameters; pw is url-safe base64, quote-free
            c.execute(text(f"CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{pw}'"))
            c.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}"))
            c.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}"))
        url = make_url(PG_URL).set(username=role, password=pw)
        eng = create_engine(url)
        try:
            with Session(eng) as s:
                r = self.ba.BookingAuthority(s).execute(self.cmd(time(10)))
            self.assertEqual(r.outcome.value, "COMMITTED", r.reason)
            with eng.connect() as c:
                for stmt in ("SET session_replication_role = replica",
                             "ALTER TABLE revisions DISABLE TRIGGER ALL",
                             "ALTER TABLE revisions DROP CONSTRAINT ex_revisions_professional_occupied_overlap"):
                    with self.assertRaises(DBAPIError) as ctx:
                        c.execute(text(stmt))
                    self.assertEqual(_state(ctx.exception), "42501", stmt)
                    c.rollback()
                su = c.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar()
                self.assertFalse(su)
        finally:
            eng.dispose()
            with self.engine.begin() as c:
                c.execute(text(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}"))
                c.execute(text(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}"))
                c.execute(text(f"DROP ROLE {role}"))

    # ── Flow writer end to end on PostgreSQL ──────────────────────────────

    def _flow_world(self, conn_session, hour_suffix):
        from app.models import (Lead, WhatsAppContact, WhatsAppThread, WhatsAppThreadCandidate,
                                WhatsAppThreadState)
        from app.services.booking_flow_service import make_booking_token
        s = conn_session
        contact = WhatsAppContact(wa_id=f"549110000{hour_suffix:04d}", display_name="Fixture")
        s.add(contact); s.flush()
        thread = WhatsAppThread(contact_id=contact.id, lead_id=self.lead)
        s.add(thread); s.flush()
        s.add(WhatsAppThreadCandidate(thread_id=thread.id, marca="Toyota", modelo="Corolla",
                                      anio=2020, tipo_vehiculo="AUTO", zone_group="CABA",
                                      zone_detail="Palermo", status="current_focus"))
        token = make_booking_token(thread.id)
        s.add(WhatsAppThreadState(thread_id=thread.id, flow_booking_token=token,
                                  needs_human=False))
        s.commit()
        return thread.id, token

    def test_pg17_flow_confirm_replay_and_race_on_postgres(self):
        from app.schemas.schedule import ScheduleCheckOut, ScheduleSlotOut, ScheduleSlotsOut
        from app.services.booking_flow_service import BookingFlowService, BookingSlotConflictError
        self.prof(default=True)
        ok = ScheduleCheckOut(valid=True, suggested_slots=[], approval_tag="x",
                              requested_slot=ScheduleSlotOut(start="x", end="y"),
                              business_hours="09:00-18:00", service_minutes=45,
                              buffer_minutes=0, travel_minutes=0, total_slot_minutes=45,
                              conflicts=[], reasons=[], rules_applied=[])
        slots = ScheduleSlotsOut(preferred_day=self.day, business_hours="09:00-18:00",
                                 slots=[], rules_applied=[])
        with Session(self.engine) as s:
            t1, tok1 = self._flow_world(s, 1)
            t2, tok2 = self._flow_world(s, 2)
        data = lambda tok, at: {"date": self.day.isoformat(), "time": at, "name": "Fixture X",
                                "inspection_address": "-", "booking_token": tok}
        results = {}

        def confirm(tok, at, label):
            with Session(self.engine) as s:
                svc = BookingFlowService(s)
                svc._sched = mock.MagicMock()
                svc._sched.check.return_value = ok
                svc._sched.list_slots.return_value = slots
                with mock.patch.object(BookingFlowService, "_send_booking_receipt",
                                       return_value=False):
                    try:
                        results[label] = svc.handle_confirm_booking(tok, data(tok, at))["screen"]
                    except BookingSlotConflictError as exc:
                        results[label] = "CONFLICT:" + exc.refreshed_data["screen"]

        a = threading.Thread(target=confirm, args=(tok1, "10:00", "a"))
        b = threading.Thread(target=confirm, args=(tok2, "10:30", "b"))
        a.start(); b.start(); a.join(20); b.join(20)
        self.assertEqual(sorted(results.values()), ["CONFLICT:APPOINTMENT", "SUCCESS"])
        winner = tok1 if results["a"] == "SUCCESS" else tok2
        confirm(winner, "10:00" if winner == tok1 else "10:30", "replay")
        self.assertEqual(results["replay"], "SUCCESS")              # original result, no error
        self.assertEqual(len(self.rows(
            "SELECT id FROM revisions WHERE lead_id=:l AND agenda_occupying", l=self.lead)), 1)
        self.assertEqual(len(self.rows(
            "SELECT id FROM thread_revisions WHERE thread_id IN (:a, :b)", a=t1, b=t2)), 1)
        rec = self.rows("SELECT replay_count, ack_status FROM booking_commands "
                        "WHERE source='WHATSAPP_FLOW'")
        self.assertEqual(len(rec), 1)
        self.assertGreaterEqual(rec[0][0], 1)
        with self.engine.begin() as c:
            c.execute(text("DELETE FROM booking_commands"))
            c.execute(text("DELETE FROM thread_revisions WHERE thread_id IN (:a, :b)"), {"a": t1, "b": t2})
            c.execute(text("DELETE FROM whatsapp_thread_states WHERE thread_id IN (:a, :b)"), {"a": t1, "b": t2})
            c.execute(text("DELETE FROM whatsapp_thread_candidates WHERE thread_id IN (:a, :b)"), {"a": t1, "b": t2})
            c.execute(text("DELETE FROM whatsapp_threads WHERE id IN (:a, :b)"), {"a": t1, "b": t2})
            c.execute(text("DELETE FROM whatsapp_contacts WHERE display_name='Fixture'"))

    def test_pg18_linked_thread_revision_follows_cancel_and_reschedule(self):
        self.prof(default=True)
        tid = self.make_thread()
        with Session(self.engine) as s:
            def attach(db, rev):
                from app.models import ThreadRevision
                tr = ThreadRevision(thread_id=tid, status="booked",
                                    scheduled_date=rev.turno_fecha, scheduled_time=rev.turno_hora)
                db.add(tr); db.flush(); return tr
            booked = self.ba.BookingAuthority(s).execute(self.cmd(time(10), key="link-1"), attach=attach)
        moved = self.run_cmd(self.cmd(time(13), op="RESCHEDULE", rev=booked.revision_id))
        tr = self.rows("SELECT status, scheduled_time FROM thread_revisions WHERE id=:i",
                       i=booked.thread_revision_id)[0]
        self.assertEqual(tr, ("booked", time(13)))
        self.run_cmd(self.cmd(None, op="CANCEL", rev=booked.revision_id, day=None))
        self.assertEqual(self.rows("SELECT status FROM thread_revisions WHERE id=:i",
                                   i=booked.thread_revision_id)[0][0], "cancelled")
        self.assertEqual(moved.outcome.value, "COMMITTED")


# ═════════════════════════════════════════════════════════════════════════════

@unittest.skipUnless(PG_STAMP_URL, "SB2A_PG_STAMP_URL not set — nothing proven")
class SB2AStampBootstrapRefused(unittest.TestCase):

    def test_stamp_built_database_refuses_bookings(self):
        from app.services import booking_authority as ba
        eng = create_engine(PG_STAMP_URL)
        try:
            with eng.connect() as c:
                self.assertEqual(_version(c), HEAD)
                self.assertEqual(c.execute(text(
                    "SELECT count(*) FROM pg_constraint WHERE conname="
                    "'ex_revisions_professional_occupied_overlap'")).scalar(), 0)
            with Session(eng) as s:
                lead = s.execute(text("INSERT INTO leads (created_at, estado, necesita_humano) "
                                      "VALUES (now(), 'X', false) RETURNING id")).scalar()
                s.commit()
                r = ba.BookingAuthority(s).execute(ba.BookingCommand(
                    operation=ba.BookingOperation.CREATE, source=ba.BookingSource.OTHER,
                    lead_id=lead, requested_date=date(2027, 3, 1), requested_time=time(10)))
            self.assertEqual((r.outcome.value, r.reason), ("INTERNAL_FAILURE", "BOOKING_SCHEMA_INCOMPLETE"))
        finally:
            eng.dispose()


_FP = [
    ("columns", "SELECT table_name||'.'||column_name||':'||data_type||':'||is_nullable||':'||"
                "coalesce(column_default,'')||':'||is_generated FROM information_schema.columns "
                "WHERE table_schema='public' ORDER BY 1"),
    ("constraints", "SELECT conrelid::regclass||'.'||conname||':'||pg_get_constraintdef(oid) "
                    "FROM pg_constraint WHERE connamespace='public'::regnamespace ORDER BY 1"),
    ("indexes", "SELECT indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY 1"),
    ("triggers", "SELECT tgrelid::regclass||'.'||tgname FROM pg_trigger WHERE NOT tgisinternal ORDER BY 1"),
    ("functions", "SELECT p.proname FROM pg_proc p WHERE p.pronamespace='public'::regnamespace "
                  "AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid=p.oid AND d.deptype='e') ORDER BY 1"),
    ("extensions", "SELECT extname FROM pg_extension ORDER BY 1"),
]


def _fingerprint(conn):
    return {k: [r[0] for r in conn.execute(text(q)).all()] for k, q in _FP}


@unittest.skipUnless(PG_MIG_URL, "SB2A_PG_MIG_URL not set — nothing proven")
class SB2AMigration(unittest.TestCase):
    state: dict = {}

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_MIG_URL)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_m1_base_is_sb1(self):
        with self.engine.begin() as c:
            self.assertEqual(_version(c), SB1)
            lead = c.execute(text("INSERT INTO leads (created_at, estado, necesita_humano) "
                                  "VALUES (now(), 'X', false) RETURNING id")).scalar()
            c.execute(text("INSERT INTO revisions (lead_id, created_at, estado_revision, turno_fecha, "
                           "turno_hora) VALUES (:l, now(), 'PENDIENTE', NULL, NULL)"), {"l": lead})
            contact = c.execute(text("INSERT INTO whatsapp_contacts (wa_id, display_name, created_at) "
                                     "VALUES ('5491100000000', 'Fixture', now()) RETURNING id")).scalar()
            thread = c.execute(text("INSERT INTO whatsapp_threads (contact_id, lead_id, created_at) "
                                    "VALUES (:c, :l, now()) RETURNING id"), {"c": contact, "l": lead}).scalar()
            c.execute(text("INSERT INTO thread_revisions (thread_id, status, created_at) "
                           "VALUES (:t, 'booked', now())"), {"t": thread})
            type(self).state["rows"] = [tuple(r) for r in c.execute(text(
                "SELECT id, lead_id, estado_revision, turno_fecha FROM revisions ORDER BY id")).all()]
        with self.engine.connect() as c:
            type(self).state["fp_sb1"] = _fingerprint(c)

    def test_m2_upgrade(self):
        _alembic(PG_MIG_URL, "upgrade", HEAD)
        with self.engine.connect() as c:
            self.assertEqual(_version(c), HEAD)
            type(self).state["fp_head"] = _fingerprint(c)
            self.assertEqual(c.execute(text("SELECT DISTINCT booking_version FROM revisions")).all(), [(0,)])
            self.assertEqual(c.execute(text("SELECT count(*) FROM booking_commands")).scalar(), 0)
            self.assertEqual([tuple(r) for r in c.execute(text(
                "SELECT id, lead_id, estado_revision, turno_fecha FROM revisions ORDER BY id")).all()],
                self.state["rows"])

    def test_m3_downgrade_refuses_while_sb2a_statuses_exist(self):
        with self.engine.begin() as c:
            c.execute(text("UPDATE thread_revisions SET status='cancelled'"))
        with self.assertRaises(Exception) as ctx:
            _alembic(PG_MIG_URL, "downgrade", SB1)
        # the migration's own guard, not an incidental CHECK failure further down
        self.assertIn("SB2A downgrade refused", str(ctx.exception))
        with self.engine.connect() as c:
            self.assertEqual(_version(c), HEAD)                  # nothing changed
            self.assertEqual(_fingerprint(c), self.state["fp_head"])
        with self.engine.begin() as c:
            c.execute(text("UPDATE thread_revisions SET status='booked'"))

    def test_m4_downgrade_restores_sb1(self):
        _alembic(PG_MIG_URL, "downgrade", SB1)
        with self.engine.connect() as c:
            self.assertEqual(_version(c), SB1)
            self.assertEqual(_fingerprint(c), self.state["fp_sb1"])

    def test_m5_reupgrade_identical(self):
        _alembic(PG_MIG_URL, "upgrade", HEAD)
        with self.engine.connect() as c:
            self.assertEqual(_fingerprint(c), self.state["fp_head"])


if __name__ == "__main__":
    unittest.main()
