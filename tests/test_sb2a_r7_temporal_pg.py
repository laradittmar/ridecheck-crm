"""SB2A-R7 on real PostgreSQL — temporal values at the driver and in storage.

Disposable database only (R7_PG_URL, at the migration head); skips cleanly without it.
Proves on the real driver what SQLite cannot: a refused aware value never reaches parameter
adaptation (where psycopg would call `utcoffset`), caller code never runs inside the booking
transaction, and an accepted value round-trips to exactly the instant that was fingerprinted.
"""
from __future__ import annotations

import os
import pathlib
import sys
import unittest
from datetime import date, datetime, time, timedelta, timezone, tzinfo

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_BACKEND = str(_ROOT / "backend")
for _p in (_BACKEND, str(_ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PG_URL = os.environ.get("R7_PG_URL", "")
if PG_URL:
    os.environ["DATABASE_URL"] = PG_URL
else:
    os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine, event, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import BookingCommandRecord, Lead, Profesional, Revision  # noqa: E402
from app.services import booking_authority as ba  # noqa: E402
from app.services.booking_authority import (BookingAuthority, BookingCommand, BookingOperation as Op,  # noqa: E402
                                            BookingOutcome as Out, BookingSource as Src)

D = date(2027, 3, 1)
CALLS: list = []


def _head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(Config(os.path.join(_BACKEND, "alembic.ini"))).get_current_head()


class ShiftingTZ(tzinfo):
    """Offset can be changed by the caller at any moment; every call is recorded."""

    def __init__(self):
        self.offset = timedelta(hours=-3)

    def utcoffset(self, dt):
        CALLS.append(("utcoffset", ba.booking_transaction_active() is not None))
        return self.offset

    def dst(self, dt):
        CALLS.append(("dst", ba.booking_transaction_active() is not None))
        return timedelta(0)


class RaisingTZ(ShiftingTZ):
    def utcoffset(self, dt):
        CALLS.append(("utcoffset", True))
        raise RuntimeError("caller code ran")


@unittest.skipUnless(PG_URL, "R7_PG_URL not set — no disposable PostgreSQL; nothing proven")
class R7Postgres(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_URL)
        with cls.engine.connect() as c:
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == _head()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        CALLS.clear()
        with self.engine.begin() as c:
            c.execute(text("TRUNCATE booking_commands, revisions, leads, profesionales RESTART IDENTITY CASCADE"))
        with Session(self.engine) as s:
            s.add(Profesional(nombre="Fixture", apellido="SB2A-R7-PG", email="r7@example.invalid",
                              resource_key="fixture.r7pg", is_launch_default=True, is_active=True))
            lead = Lead()
            s.add(lead)
            s.commit()
            self.lead_id = lead.id
        self.side_sql = []

    def _cmd(self, **kw):
        base = dict(operation=Op.CREATE, source=Src.OTHER, lead_id=self.lead_id, requested_date=D,
                    requested_time=time(10))
        base.update(kw)
        return BookingCommand(**base)

    def _run(self, cmd):
        """Execute; return (result, statements sent to PostgreSQL)."""
        stmts = []

        def hit(conn, cursor, statement, *a):
            stmts.append(statement)
        event.listen(self.engine, "before_cursor_execute", hit)
        try:
            with Session(self.engine) as s:
                r = BookingAuthority(s).execute(cmd)
        finally:
            event.remove(self.engine, "before_cursor_execute", hit)
        return r, stmts

    def _rows(self):
        with self.engine.connect() as c:
            return (c.execute(text("SELECT count(*) FROM revisions")).scalar(),
                    c.execute(text("SELECT count(*) FROM booking_commands")).scalar())

    def _assert_refused(self, cmd, reason):
        r, stmts = self._run(cmd)
        self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, reason))
        self.assertEqual(stmts, [], "a refused value reached SQL / driver adaptation")
        self.assertEqual(CALLS, [], "caller tzinfo code ran")
        self.assertEqual(self._rows(), (0, 0))

    # 1 + 4: custom tzinfo whose offset changes after capture, on a time
    def test_r7pg_01_shifting_tz_time_refused(self):
        tz = ShiftingTZ()
        cmd = self._cmd(requested_time=time(10, tzinfo=tz), idempotency_key="pg-1")
        call = ba._capture_call(cmd, None, None)
        tz.offset = timedelta(hours=9)                       # after capture
        with Session(self.engine) as s:
            r = BookingAuthority(s)._execute_snapshot(call)
        self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, "TEMPORAL_NOT_NAIVE:requested_time"))
        self.assertEqual((CALLS, self._rows()), ([], (0, 0)))

    # 1 + 5: custom tzinfo on a datetime, both admissible DateTime column kinds
    def test_r7pg_02_shifting_tz_datetime_refused(self):
        for col in ("created_at", "appointment_approved_at"):
            self._assert_refused(self._cmd(revision_fields={col: datetime(2026, 11, 1, 12, tzinfo=ShiftingTZ())}),
                                 f"TEMPORAL_NOT_NAIVE:revision_fields.{col}")

    # 2: a tzinfo whose methods attempt SQL
    def test_r7pg_03_tz_attempting_sql_never_runs(self):
        engine, side = self.engine, self.side_sql

        class SqlTZ(ShiftingTZ):
            def utcoffset(self, dt):
                with engine.connect() as c:
                    side.append(c.execute(text("SELECT count(*) FROM revisions")).scalar())
                return timedelta(0)
        self._assert_refused(self._cmd(revision_fields={"created_at": datetime(2026, 11, 1, tzinfo=SqlTZ())}),
                             "TEMPORAL_NOT_NAIVE:revision_fields.created_at")
        self.assertEqual(side, [])

    # 3: a tzinfo whose methods raise -> typed validation failure, never INTERNAL_FAILURE
    def test_r7pg_04_raising_tz_is_validation_not_internal(self):
        self._assert_refused(self._cmd(requested_time=time(10, tzinfo=RaisingTZ())),
                             "TEMPORAL_NOT_NAIVE:requested_time")

    # 6: standard fixed-offset aware values, per the selected policy (refused)
    def test_r7pg_05_stdlib_fixed_offset_values_refused(self):
        self._assert_refused(self._cmd(requested_time=time(11, tzinfo=timezone(timedelta(hours=-3)))),
                             "TEMPORAL_NOT_NAIVE:requested_time")
        for col in ("created_at", "appointment_approval_sent_at"):
            self._assert_refused(self._cmd(revision_fields={col: datetime(2026, 11, 1, 12, tzinfo=timezone.utc)}),
                                 f"TEMPORAL_NOT_NAIVE:revision_fields.{col}")

    def test_r7pg_06_naive_value_for_timestamptz_refused_without_sql(self):
        r, stmts = self._run(self._cmd(revision_fields={"appointment_approved_at": datetime(2026, 11, 1, 12)}))
        self.assertEqual((r.reason, stmts), ("COMMAND_NOT_DATA:revision_fields.appointment_approved_at", []))

    # 7: aware API time through the real handler on PostgreSQL
    def test_r7pg_07_api_offset_time_is_422_not_500(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api import revisions as api
        from app.db import get_db
        s = Session(self.engine)
        try:
            app = FastAPI(); app.include_router(api.router)
            app.dependency_overrides[get_db] = lambda: s
            app.dependency_overrides[api.get_db] = lambda: s
            c = TestClient(app, raise_server_exceptions=False)
            # Convergence Phase 4 adaptation (CP1-02): both requests carry a durable key, so the
            # offset-time refusal under test is reached (an unkeyed booking command is a 428).
            bad = c.post(f"/leads/{self.lead_id}/revisions",
                         json={"turno_fecha": D.isoformat(), "turno_hora": "11:00:00-03:00"},
                         headers={"Idempotency-Key": "r7pg-07-bad"})
            self.assertEqual(bad.status_code, 422, bad.text)
            self.assertNotIn("DB_42883", bad.text)
            ok = c.post(f"/leads/{self.lead_id}/revisions",
                        json={"turno_fecha": D.isoformat(), "turno_hora": "11:00:00"},
                        headers={"Idempotency-Key": "r7pg-07-ok"})
            self.assertEqual(ok.status_code, 200, ok.text)
        finally:
            s.close()
        with self.engine.connect() as c:
            self.assertEqual(c.execute(text("SELECT turno_hora FROM revisions")).scalars().all(), [time(11)])

    # 8 + 9 + 10: naive control; fingerprint after caller mutation; round trip
    def test_r7pg_08_naive_control_fingerprint_and_round_trip(self):
        created, cobro = datetime(2026, 10, 1, 9, 5, 7, 123456), date(2026, 10, 2)
        rf = {"created_at": created, "fecha_cobro": cobro, "marca": "Fiat"}
        cmd = self._cmd(requested_time=time(10, 30, 15), idempotency_key="pg-naive", revision_fields=rf)
        call = ba._capture_call(cmd, None, None)
        rf["created_at"] = datetime(1999, 1, 1)               # caller mutation after capture
        rf["marca"] = "POISON"
        with Session(self.engine) as s:
            r = BookingAuthority(s)._execute_snapshot(call)
        self.assertEqual(r.outcome, Out.COMMITTED, r.reason)
        canonical = self._cmd(requested_time=time(10, 30, 15), idempotency_key="pg-naive",
                              revision_fields={"created_at": created, "fecha_cobro": cobro, "marca": "Fiat"})
        with Session(self.engine) as s:
            rec = s.query(BookingCommandRecord).filter_by(idempotency_key="pg-naive").one()
            rev = s.get(Revision, r.revision_id)
            self.assertEqual(rec.request_fingerprint, ba.request_fingerprint(canonical))
            self.assertEqual((rev.turno_fecha, rev.turno_hora, rev.created_at, rev.fecha_cobro, rev.marca),
                             (D, time(10, 30, 15), created, cobro, "Fiat"))
            self.assertIsNone(rev.created_at.tzinfo)
        with self.engine.connect() as c:                      # the stored text is the instant itself
            self.assertEqual(c.execute(text("SELECT created_at::text, turno_hora::text FROM revisions")).one(),
                             ("2026-10-01 09:05:07.123456", "10:30:15"))
        replay, _ = self._run(canonical)
        self.assertEqual((replay.outcome, replay.revision_id), (Out.IDEMPOTENT_REPLAY, r.revision_id))
        self.assertEqual(CALLS, [])


if __name__ == "__main__":
    unittest.main()
