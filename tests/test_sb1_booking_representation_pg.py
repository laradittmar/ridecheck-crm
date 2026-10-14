"""SB1 — PostgreSQL certification of the booking representation.

These are the only SB1 tests that may be quoted as proof of overlap prevention, attribution
or migration safety. SQLite cannot evaluate a generated tsrange, an exclusion constraint or a
trigger, and nothing here falls back to it.

Two disposable databases, never shared `crm_test`:

  SB1_PG_URL       behaviour database. Pre-SB1 schema built from the parent commit's models
                   and stamped `20261006_clarification_attempts`; upgraded to head here if
                   it is not already. Every test runs in a transaction that is rolled back
                   (the concurrency test commits two rows and deletes them).
  SB1_PG_BASE_URL  migration database at `20261006_clarification_attempts`, built from the
                   parent commit's models — an oracle independent of this migration. The
                   migration class inserts representative legacy rows, upgrades, downgrades
                   and re-upgrades it, in order.
  SB1_PG_EXT_URL   a second database built the same way, in which btree_gist is installed
                   BEFORE the upgrade; the downgrade must leave it in place (SB1-R3).

All skip cleanly when unset, and say so.
"""
from __future__ import annotations

import os
import sys
import threading
import time as _time
import unittest
from datetime import date, datetime, time, timedelta

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import IntegrityError, DBAPIError  # noqa: E402

PG_URL = os.environ.get("SB1_PG_URL", "")
PG_BASE_URL = os.environ.get("SB1_PG_BASE_URL", "")

SB1_REVISION = "20261007_sb1_booking_repr"


def _script_head() -> str:
    """The current Alembic head. SB2A rewrite: the ORM models always describe the HEAD schema,
    so the ORM-using tests here must run at head; every SB1 property they assert still holds
    there, and the downgrade target (PARENT) is unchanged."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(Config(os.path.join(_BACKEND, "alembic.ini"))).get_current_head()


HEAD = _script_head()
PARENT = "20261006_clarification_attempts"
EXCLUSION = "ex_revisions_professional_occupied_overlap"


def _sqlstate(exc: BaseException) -> str | None:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", None)


def _alembic(url: str, action: str, target: str) -> None:
    """Run alembic against `url` only. env.py reads DATABASE_URL; restore it afterwards."""
    from alembic import command
    from alembic.config import Config
    previous = os.environ.get("DATABASE_URL")
    cwd = os.getcwd()
    os.environ["DATABASE_URL"] = url
    try:
        os.chdir(_BACKEND)
        cfg = Config(os.path.join(_BACKEND, "alembic.ini"))
        getattr(command, action)(cfg, target)
    finally:
        os.chdir(cwd)
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def _version(conn) -> str:
    return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _lead(conn) -> int:
    return conn.execute(text(
        "INSERT INTO leads (created_at, estado, necesita_humano) "
        "VALUES (now(), 'CONSULTA_NUEVA', false) RETURNING id")).scalar()


def _prof(conn, *, key: str | None = None, default: bool = False, name: str = "Fixture") -> int:
    # Synthetic fixture identity — disposable database only, never a real person.
    return conn.execute(text(
        "INSERT INTO profesionales (nombre, apellido, email, created_at, resource_key, "
        "is_launch_default) VALUES (:n, 'SB1-FIXTURE', 'fixture@example.invalid', now(), "
        ":k, :d) RETURNING id"), {"n": name, "k": key, "d": default}).scalar()


def _rev(conn, lead_id: int, day: date | None, at: time | None, *, prof: int | None = None,
         estado: str = "PENDIENTE", service: int | None = None,
         buffer: int | None = None) -> int:
    cols = ["lead_id", "created_at", "estado_revision", "turno_fecha", "turno_hora",
            "profesional_id"]
    vals = {"lead_id": lead_id, "estado_revision": estado, "turno_fecha": day,
            "turno_hora": at, "profesional_id": prof}
    if service is not None:
        cols.append("service_minutes"); vals["service_minutes"] = service
    if buffer is not None:
        cols.append("buffer_minutes"); vals["buffer_minutes"] = buffer
    placeholders = ", ".join("now()" if c == "created_at" else f":{c}" for c in cols)
    return conn.execute(text(
        f"INSERT INTO revisions ({', '.join(cols)}) VALUES ({placeholders}) RETURNING id"),
        vals).scalar()


D = date(2026, 11, 3)  # a Tuesday


# ═════════════════════════════════════════════════════════════════════════════
# Behaviour at head
# ═════════════════════════════════════════════════════════════════════════════

@unittest.skipUnless(PG_URL, "SB1_PG_URL not set — no disposable PostgreSQL; nothing proven")
class SB1PostgresBehaviour(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_URL)
        with cls.engine.connect() as conn:
            current = _version(conn)
        if current != HEAD:
            assert current == PARENT, f"behaviour database at unexpected revision {current}"
            _alembic(PG_URL, "upgrade", HEAD)
        with cls.engine.connect() as conn:
            assert _version(conn) == HEAD

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        self.conn = self.engine.connect()
        self.tx = self.conn.begin()
        self.lead = _lead(self.conn)

    def tearDown(self):
        self.tx.rollback()
        self.conn.close()

    def _expect_failure(self, sqlstate: str, fn):
        sp = self.conn.begin_nested()
        with self.assertRaises((IntegrityError, DBAPIError)) as ctx:
            fn()
        sp.rollback()
        self.assertEqual(_sqlstate(ctx.exception), sqlstate, str(ctx.exception))
        return ctx.exception

    def _interval(self, rev_id: int):
        return self.conn.execute(text(
            "SELECT lower(occupied_during), upper(occupied_during), lower_inc(occupied_during),"
            " upper_inc(occupied_during), agenda_occupying, service_minutes, buffer_minutes,"
            " turno_timezone, profesional_id FROM revisions WHERE id = :i"),
            {"i": rev_id}).one()

    # ── structure ──────────────────────────────────────────────────────────

    def test_pg01_structure_present(self):
        c = self.conn
        self.assertEqual(c.execute(text(
            "SELECT count(*) FROM pg_extension WHERE extname='btree_gist'")).scalar(), 1)
        condef = c.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname=:n"),
            {"n": EXCLUSION}).scalar()
        self.assertIn("EXCLUDE USING gist (profesional_id WITH =, occupied_during WITH &&)",
                      condef)
        self.assertIn("agenda_occupying", condef)
        self.assertIn("profesional_id IS NOT NULL", condef)
        gen = dict(c.execute(text(
            "SELECT column_name, is_generated FROM information_schema.columns "
            "WHERE table_name='revisions' AND column_name IN "
            "('agenda_occupying','occupied_during')")).all())
        self.assertEqual(gen, {"agenda_occupying": "ALWAYS", "occupied_during": "ALWAYS"})
        defaults = dict(c.execute(text(
            "SELECT column_name, column_default FROM information_schema.columns "
            "WHERE table_name='revisions' AND column_name IN "
            "('service_minutes','buffer_minutes')")).all())
        self.assertEqual(defaults, {"service_minutes": "30", "buffer_minutes": "15"})
        self.assertEqual(c.execute(text(
            "SELECT count(*) FROM pg_trigger WHERE tgname='trg_revisions_sb1_attribute_professional'"
            " AND NOT tgisinternal")).scalar(), 1)

    # ── interval ───────────────────────────────────────────────────────────

    def test_pg02_default_interval_is_30_plus_15_half_open(self):
        p = _prof(self.conn)
        r = _rev(self.conn, self.lead, D, time(10, 0), prof=p)
        lo, hi, lo_inc, hi_inc, occ, svc, buf, tz, _ = self._interval(r)
        self.assertEqual((svc, buf), (30, 15))
        self.assertEqual(lo, datetime(2026, 11, 3, 10, 0))
        self.assertEqual(hi, datetime(2026, 11, 3, 10, 45))
        self.assertEqual(hi - lo, timedelta(minutes=45))
        self.assertEqual((lo_inc, hi_inc), (True, False))
        self.assertTrue(occ)
        self.assertEqual(tz, "America/Argentina/Buenos_Aires")

    def test_pg03_service_and_buffer_are_independent_columns(self):
        p = _prof(self.conn)
        r = _rev(self.conn, self.lead, D, time(10, 0), prof=p, service=30, buffer=0)
        self.assertEqual(self._interval(r)[1], datetime(2026, 11, 3, 10, 30))
        self.conn.execute(text("UPDATE revisions SET buffer_minutes = 15 WHERE id=:i"), {"i": r})
        lo, hi, *_ , svc, buf, _tz, _p = self._interval(r)
        self.assertEqual((svc, buf, hi), (30, 15, datetime(2026, 11, 3, 10, 45)))
        self._expect_failure("23514", lambda: self.conn.execute(text(
            "UPDATE revisions SET service_minutes = 0 WHERE id=:i"), {"i": r}))
        self._expect_failure("23514", lambda: self.conn.execute(text(
            "UPDATE revisions SET buffer_minutes = -1 WHERE id=:i"), {"i": r}))

    def test_pg04_adjacent_intervals_coexist(self):
        p = _prof(self.conn)
        _rev(self.conn, self.lead, D, time(10, 0), prof=p)
        _rev(self.conn, self.lead, D, time(10, 45), prof=p)
        _rev(self.conn, self.lead, D, time(9, 15), prof=p)   # ends exactly at 10:00

    def test_pg05_true_overlap_rejected(self):
        p = _prof(self.conn)
        _rev(self.conn, self.lead, D, time(10, 0), prof=p)
        for at in (time(10, 30), time(10, 0), time(9, 30), time(10, 44)):
            exc = self._expect_failure("23P01", lambda at=at: _rev(
                self.conn, self.lead, D, at, prof=p))
            self.assertIn(EXCLUSION, str(exc))

    def test_pg06_buffer_extension_into_next_rejected(self):
        p = _prof(self.conn)
        r = _rev(self.conn, self.lead, D, time(10, 0), prof=p)
        _rev(self.conn, self.lead, D, time(10, 45), prof=p)
        self._expect_failure("23P01", lambda: self.conn.execute(text(
            "UPDATE revisions SET buffer_minutes = 16 WHERE id=:i"), {"i": r}))

    def test_pg07_different_professionals_may_share_an_interval(self):
        a, b = _prof(self.conn), _prof(self.conn)
        _rev(self.conn, self.lead, D, time(10, 0), prof=a)
        _rev(self.conn, self.lead, D, time(10, 0), prof=b)
        # the constraint is still per professional: b cannot overlap b
        self._expect_failure("23P01", lambda: _rev(self.conn, self.lead, D, time(10, 30), prof=b))

    def test_pg08_unscheduled_revision_valid_and_unattributed(self):
        r = _rev(self.conn, self.lead, None, None)
        lo, hi, _li, _ui, occ, svc, buf, _tz, prof = self._interval(r)
        self.assertIsNone(lo); self.assertIsNone(hi)
        self.assertFalse(occ)
        self.assertIsNone(prof)
        self.assertEqual((svc, buf), (30, 15))

    def test_pg09_occupying_without_professional_refused_when_none_designated(self):
        exc = self._expect_failure("23514", lambda: _rev(self.conn, self.lead, D, time(10, 0)))
        self.assertIn("no launch-default professional", str(exc))
        # moving an unscheduled row onto the agenda is refused the same way
        r = _rev(self.conn, self.lead, None, None)
        self._expect_failure("23514", lambda: self.conn.execute(text(
            "UPDATE revisions SET turno_fecha=:d, turno_hora='10:00' WHERE id=:i"),
            {"d": D, "i": r}))

    def test_pg10_writer_naming_no_professional_attributed_to_launch_default(self):
        """The unchanged Flow/CE/CRM writers set no professional: they get the default."""
        from sqlalchemy.orm import Session
        from app.models import Revision
        p = _prof(self.conn, key="inspector.julian", default=True)
        with Session(bind=self.conn, join_transaction_mode="create_savepoint") as s:
            # Same field set booking_flow_service.handle_confirm_booking writes.
            rev = Revision(lead_id=self.lead, zone_group="Norte", zone_detail="X",
                           turno_fecha=D, turno_hora=time(11, 0))
            s.add(rev)
            s.flush()
            rid = rev.id
            s.commit()          # releases the savepoint into the test's outer transaction
        self.assertEqual(self._interval(rid)[-1], p)
        # and an explicit blank (CRM form) on an attributed row re-attributes, never NULLs
        self.conn.execute(text("UPDATE revisions SET profesional_id=NULL WHERE id=:i"),
                          {"i": rid})
        self.assertEqual(self._interval(rid)[-1], p)
        # unscheduled rows are never attributed
        u = _rev(self.conn, self.lead, None, None)
        self.assertIsNone(self._interval(u)[-1])

    def test_pg11_non_occupying_estados_do_not_block(self):
        p = _prof(self.conn)
        _rev(self.conn, self.lead, D, time(10, 0), prof=p)
        for estado in ("CANCELADO", "REPROGRAMAR", "cancelado", "Reprogramar"):
            r = _rev(self.conn, self.lead, D, time(10, 15), prof=p, estado=estado)
            self.assertFalse(self._interval(r)[4], estado)
        # every other estado occupies
        for estado in ("PENDIENTE", "CONFIRMADO", "EN_PROCESO", "COMPLETADO"):
            self._expect_failure("23P01", lambda e=estado: _rev(
                self.conn, self.lead, D, time(10, 15), prof=p, estado=e))

    def test_pg12_cancel_frees_slot_and_reactivation_is_checked(self):
        p = _prof(self.conn)
        first = _rev(self.conn, self.lead, D, time(10, 0), prof=p)
        self.conn.execute(text("UPDATE revisions SET estado_revision='CANCELADO' WHERE id=:i"),
                          {"i": first})
        _rev(self.conn, self.lead, D, time(10, 0), prof=p)        # slot is free again
        self._expect_failure("23P01", lambda: self.conn.execute(text(
            "UPDATE revisions SET estado_revision='CONFIRMADO' WHERE id=:i"), {"i": first}))

    def test_pg13_bypassing_writer_cannot_move_onto_an_occupied_slot(self):
        """The CRM edit path (kanban_actions) writes turno_fecha/turno_hora directly."""
        p = _prof(self.conn)
        _rev(self.conn, self.lead, D, time(10, 0), prof=p)
        other = _rev(self.conn, self.lead, D, time(14, 0), prof=p)
        self._expect_failure("23P01", lambda: self.conn.execute(text(
            "UPDATE revisions SET turno_hora='10:15' WHERE id=:i"), {"i": other}))

    def test_pg14_single_launch_default(self):
        _prof(self.conn, key="inspector.julian", default=True)
        self._expect_failure("23505", lambda: _prof(self.conn, key="other", default=True))
        self._expect_failure("23514", lambda: _prof(self.conn, key=None, default=True))
        self._expect_failure("23505", lambda: _prof(self.conn, key="inspector.julian"))

    def test_pg15_buenos_aires_interpretation_explicit(self):
        p = _prof(self.conn)
        r = _rev(self.conn, self.lead, D, time(10, 0), prof=p)
        utc = self.conn.execute(text(
            "SELECT (lower(occupied_during) AT TIME ZONE turno_timezone) AT TIME ZONE 'UTC' "
            "FROM revisions WHERE id=:i"), {"i": r}).scalar()
        self.assertEqual(utc, datetime(2026, 11, 3, 13, 0))   # UTC-3, no DST
        self._expect_failure("23514", lambda: self.conn.execute(text(
            "UPDATE revisions SET turno_timezone='UTC' WHERE id=:i"), {"i": r}))
        # The stored interval is the wall-clock the writers wrote. PostgreSQL WOULD accept an
        # instant-valued generated range (timezone(text, timestamp) is declared immutable),
        # but its stored value would freeze whatever tzdata said at write time; the
        # wall-clock range cannot drift, and turno_timezone states how to read it.
        self.assertEqual(self.conn.execute(text(
            "SELECT pg_typeof(occupied_during)::text FROM revisions WHERE id=:i"),
            {"i": r}).scalar(), "tsrange")

    def test_pg16_calendar_exceptions(self):
        c = self.conn
        ins = ("INSERT INTO calendar_exceptions (kind, exception_date, start_time, end_time, "
               "reason, created_by) VALUES (:k, :d, :s, :e, :r, :by) RETURNING id, created_at, "
               "timezone")
        row = c.execute(text(ins), {"k": "CLOSED_DATE", "d": date(2026, 12, 25), "s": None,
                                    "e": None, "r": "cierre", "by": "operator"}).one()
        self.assertIsNotNone(row.created_at)
        self.assertEqual(row.timezone, "America/Argentina/Buenos_Aires")
        c.execute(text(ins), {"k": "UNAVAILABLE_INTERVAL", "d": D, "s": time(12, 0),
                              "e": time(13, 0), "r": None, "by": None})
        for bad in (
            {"k": "CLOSED_DATE", "d": D, "s": time(9, 0), "e": time(10, 0)},
            {"k": "UNAVAILABLE_INTERVAL", "d": D, "s": time(13, 0), "e": time(12, 0)},
            {"k": "UNAVAILABLE_INTERVAL", "d": D, "s": time(13, 0), "e": None},
            {"k": "HOLIDAY_API", "d": D, "s": None, "e": None},
        ):
            self._expect_failure("23514", lambda b=bad: c.execute(text(ins), {
                **b, "r": None, "by": None}))

    def test_pg17_designation_and_backfill_helpers(self):
        from sqlalchemy.orm import Session
        from app.services import booking_representation as br
        with Session(bind=self.conn, join_transaction_mode="create_savepoint") as s:
            with self.assertRaises(br.LaunchProfessionalError):
                br.backfill_legacy_attribution(s)
            pid = _prof(self.conn)
            br.designate_launch_professional(s, pid)
            other = _prof(self.conn, name="Other")
            with self.assertRaises(br.LaunchProfessionalError):
                br.designate_launch_professional(s, other)
            key = s.execute(text("SELECT resource_key FROM profesionales WHERE id=:i"),
                            {"i": pid}).scalar()
            self.assertEqual(key, br.LAUNCH_PROFESSIONAL_KEY)
            self.assertEqual(br.backfill_legacy_attribution(s).eligible, 0)

    def test_pg18_python_mirror_agrees_with_generated_columns(self):
        from app.services import booking_representation as br
        p = _prof(self.conn)
        cases = [(D, time(9, 0), "PENDIENTE", 30, 15), (D, time(11, 0), "cancelado", 30, 15),
                 (None, None, "PENDIENTE", 30, 15), (D, time(12, 0), "CONFIRMADO", 45, 0),
                 (date(2026, 11, 4), time(9, 30), "REPROGRAMAR", 20, 10)]
        for day, at, estado, svc, buf in cases:
            sp = self.conn.begin_nested()
            r = _rev(self.conn, self.lead, day, at, prof=p if day else None, estado=estado,
                     service=svc, buffer=buf)
            lo, hi, *_rest = self._interval(r)
            occ = self._interval(r)[4]
            mirror = br.occupied_interval(day, at, svc, buf)
            self.assertEqual(occ, br.is_occupying(day, at, estado))
            self.assertEqual((lo, hi), (mirror.start, mirror.end) if mirror else (None, None))
            sp.rollback()


@unittest.skipUnless(PG_URL, "SB1_PG_URL not set — no disposable PostgreSQL; nothing proven")
class SB1PostgresConcurrency(unittest.TestCase):
    """Two sessions race for one slot. Real PostgreSQL, real commits, cleaned up after."""

    def test_pg19_concurrent_overlap_second_committer_loses(self):
        engine = create_engine(PG_URL)
        a, b = engine.connect(), engine.connect()
        ids: list[int] = []
        try:
            setup = engine.connect()
            with setup.begin():
                lead = _lead(setup)
                prof = _prof(setup)
            setup.close()
            ids.append(("lead", lead)); ids.append(("prof", prof))

            ta = a.begin()
            first = _rev(a, lead, D, time(15, 0), prof=prof)          # uncommitted
            outcome: dict = {}

            def contender():
                tb = b.begin()
                started = _time.monotonic()
                try:
                    _rev(b, lead, D, time(15, 30), prof=prof)          # overlaps
                    tb.commit()
                    outcome["result"] = "committed"
                except IntegrityError as exc:
                    tb.rollback()
                    outcome["result"] = _sqlstate(exc)
                outcome["waited"] = _time.monotonic() - started

            t = threading.Thread(target=contender)
            t.start()
            _time.sleep(1.0)
            self.assertTrue(t.is_alive(), "contender must block on the uncommitted holder")
            ta.commit()
            t.join(10)
            self.assertEqual(outcome.get("result"), "23P01")
            self.assertGreaterEqual(outcome["waited"], 0.9)
            ids.append(("rev", first))
        finally:
            a.close(); b.close()
            with engine.begin() as c:
                for kind, i in reversed(ids):
                    table = {"rev": "revisions", "lead": "leads", "prof": "profesionales"}[kind]
                    c.execute(text(f"DELETE FROM {table} WHERE id=:i"), {"i": i})
            engine.dispose()


# ═════════════════════════════════════════════════════════════════════════════
# Migration: legacy rows, upgrade, downgrade, re-upgrade
# ═════════════════════════════════════════════════════════════════════════════

_FINGERPRINT_SQL = [
    ("columns",
     "SELECT table_name||'.'||column_name||':'||data_type||':'||is_nullable||':'||"
     "coalesce(column_default,'')||':'||is_generated||':'||coalesce(generation_expression,'')"
     " FROM information_schema.columns WHERE table_schema='public' ORDER BY 1"),
    ("constraints",
     "SELECT conrelid::regclass||'.'||conname||':'||pg_get_constraintdef(oid) "
     "FROM pg_constraint WHERE connamespace='public'::regnamespace ORDER BY 1"),
    ("indexes",
     "SELECT indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY 1"),
    ("triggers",
     "SELECT tgrelid::regclass||'.'||tgname FROM pg_trigger WHERE NOT tgisinternal ORDER BY 1"),
    ("functions",
     "SELECT p.proname FROM pg_proc p WHERE p.pronamespace='public'::regnamespace "
     "AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid=p.oid AND d.deptype='e') "
     "ORDER BY 1"),
    ("extensions", "SELECT extname FROM pg_extension ORDER BY 1"),
]


def _fingerprint(conn) -> dict[str, list[str]]:
    return {k: [r[0] for r in conn.execute(text(q)).all()] for k, q in _FINGERPRINT_SQL}


_LEGACY = [
    # (key, estado, date, time, attributed)
    ("occ_a", "PENDIENTE", date(2026, 9, 1), time(10, 0), False),
    ("occ_b_adjacent", "CONFIRMADO", date(2026, 9, 1), time(10, 45), False),
    ("occ_c", "COMPLETADO", date(2026, 9, 2), time(9, 0), False),
    ("cancel_overlapping", "CANCELADO", date(2026, 9, 1), time(10, 15), False),
    ("resched_overlapping", "REPROGRAMAR", date(2026, 9, 1), time(10, 30), False),
    ("cancel_lower", "cancelado", date(2026, 9, 2), time(9, 15), False),
    ("unscheduled", "PENDIENTE", None, None, False),
    ("date_only", "PENDIENTE", date(2026, 9, 5), None, False),
    ("time_only", "PENDIENTE", None, time(9, 0), False),
    ("pair_1", "CONFIRMADO", date(2026, 9, 3), time(11, 0), False),
    ("pair_2_overlaps_1", "PENDIENTE", date(2026, 9, 3), time(11, 30), False),
    ("attributed", "CONFIRMADO", date(2026, 9, 4), time(12, 0), True),
]
_EXPECTED_CLASSES = {
    "OCCUPYING_UNATTRIBUTED": 5, "OCCUPYING_ATTRIBUTED": 1, "NON_OCCUPYING": 3,
    "UNSCHEDULED": 1, "INCOMPLETE": 2,
}
_BASE_COLUMNS = ("id, lead_id, profesional_id, estado_revision, turno_fecha, turno_hora, "
                 "turno_notas, zone_group, created_at")


@unittest.skipUnless(PG_BASE_URL,
                     "SB1_PG_BASE_URL not set — no disposable pre-SB1 PostgreSQL; nothing proven")
class SB1Migration(unittest.TestCase):
    """Ordered: m1 → m6 share one database and run in name order."""

    state: dict = {}

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_BASE_URL)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_m1_base_with_legacy_rows(self):
        from app.services import booking_representation as br
        with self.engine.begin() as c:
            self.assertEqual(_version(c), PARENT)
            self.assertEqual(c.execute(text(
                "SELECT count(*) FROM pg_extension WHERE extname='btree_gist'")).scalar(), 0)
            self.assertEqual(c.execute(text("SELECT count(*) FROM revisions")).scalar(), 0)
            lead = _lead(c)
            prof = c.execute(text(
                "INSERT INTO profesionales (nombre, apellido, email, created_at) VALUES "
                "('Fixture','SB1-FIXTURE','fixture@example.invalid', now()) RETURNING id")).scalar()
            ids = {}
            for key, estado, day, at, attributed in _LEGACY:
                ids[key] = c.execute(text(
                    "INSERT INTO revisions (lead_id, created_at, estado_revision, turno_fecha,"
                    " turno_hora, profesional_id, zone_group) VALUES (:l, now(), :e, :d, :t,"
                    " :p, 'Norte') RETURNING id"),
                    {"l": lead, "e": estado, "d": day, "t": at,
                     "p": prof if attributed else None}).scalar()
            got = {r.cls: r.n for r in c.execute(text(br.LEGACY_CLASSIFICATION_SQL))}
            self.assertEqual(got, _EXPECTED_CLASSES)
            self.assertEqual(c.execute(text(br.LEGACY_OVERLAP_SQL)).scalar(), 1)
            snapshot = [tuple(r) for r in c.execute(text(
                f"SELECT {_BASE_COLUMNS} FROM revisions ORDER BY id")).all()]
            prof_snapshot = [tuple(r) for r in c.execute(text(
                "SELECT * FROM profesionales ORDER BY id")).all()]
        type(self).state.update(ids=ids, prof=prof, snapshot=snapshot,
                                prof_snapshot=prof_snapshot)
        with self.engine.connect() as c:
            type(self).state["fp_base"] = _fingerprint(c)

    def test_m2_upgrade_classifies_without_inventing(self):
        from app.services import booking_representation as br
        st = type(self).state
        _alembic(PG_BASE_URL, "upgrade", HEAD)
        with self.engine.connect() as c:
            self.assertEqual(_version(c), HEAD)
            st["fp_head"] = _fingerprint(c)
            self.assertEqual([tuple(r) for r in c.execute(text(
                f"SELECT {_BASE_COLUMNS} FROM revisions ORDER BY id")).all()], st["snapshot"])
            rows = c.execute(text(
                "SELECT id, estado_revision, turno_fecha, turno_hora, profesional_id,"
                " service_minutes, buffer_minutes, agenda_occupying, lower(occupied_during),"
                " upper(occupied_during) FROM revisions ORDER BY id")).all()
            for r in rows:
                self.assertEqual((r.service_minutes, r.buffer_minutes), (30, 15))
                self.assertEqual(r.agenda_occupying,
                                 br.is_occupying(r.turno_fecha, r.turno_hora, r.estado_revision))
                mirror = br.occupied_interval(r.turno_fecha, r.turno_hora, 30, 15)
                self.assertEqual((r.lower, r.upper),
                                 (mirror.start, mirror.end) if mirror else (None, None))
            # no professional invented, no row attributed, no professional created
            self.assertEqual(sum(1 for r in rows if r.profesional_id is not None), 1)
            self.assertEqual(c.execute(text("SELECT count(*) FROM profesionales")).scalar(), 1)
            self.assertEqual(c.execute(text(
                "SELECT count(*) FROM profesionales WHERE is_launch_default")).scalar(), 0)
            got = {r.cls: r.n for r in c.execute(text(br.LEGACY_CLASSIFICATION_SQL))}
            self.assertEqual(got, _EXPECTED_CLASSES)

    def test_m3_legacy_backlog_is_frozen_not_reowned(self):
        st = type(self).state
        legacy = st["ids"]["occ_a"]
        with self.engine.connect() as c:
            tx = c.begin()
            # untouched interval: editable, stays unattributed
            c.execute(text("UPDATE revisions SET turno_notas='nota' WHERE id=:i"), {"i": legacy})
            self.assertIsNone(c.execute(text(
                "SELECT profesional_id FROM revisions WHERE id=:i"), {"i": legacy}).scalar())
            # moving it is a new booking: refused while no default is designated
            sp = c.begin_nested()
            with self.assertRaises(IntegrityError) as ctx:
                c.execute(text("UPDATE revisions SET turno_hora='16:00' WHERE id=:i"),
                          {"i": legacy})
            sp.rollback()
            self.assertEqual(_sqlstate(ctx.exception), "23514")
            tx.rollback()

    def _legacy_change_refused(self, sql: str) -> None:
        """A legacy occupying unattributed row; the change must be refused (23514) while no
        launch default exists, and the row must be left exactly as it was."""
        st = type(self).state
        legacy = st["ids"]["occ_a"]
        cols = ("turno_fecha, turno_hora, service_minutes, buffer_minutes, profesional_id, "
                "agenda_occupying, lower(occupied_during), upper(occupied_during)")
        with self.engine.connect() as c:
            tx = c.begin()
            self.assertEqual(c.execute(text(
                "SELECT count(*) FROM profesionales WHERE is_launch_default")).scalar(), 0)
            before = tuple(c.execute(text(f"SELECT {cols} FROM revisions WHERE id=:i"),
                                     {"i": legacy}).one())
            self.assertIsNone(before[4])            # unattributed
            self.assertTrue(before[5])              # occupying
            sp = c.begin_nested()
            with self.assertRaises(IntegrityError) as ctx:
                c.execute(text(sql), {"i": legacy})
            sp.rollback()
            self.assertEqual(_sqlstate(ctx.exception), "23514")
            self.assertIn("no launch-default professional", str(ctx.exception))
            after = tuple(c.execute(text(f"SELECT {cols} FROM revisions WHERE id=:i"),
                                    {"i": legacy}).one())
            self.assertEqual(after, before)
            tx.rollback()

    def test_m3b_legacy_exemption_refuses_a_date_only_move(self):
        """SB1-R2 blind spot M8: same time, different date is a NEW booking."""
        self._legacy_change_refused(
            "UPDATE revisions SET turno_fecha = turno_fecha + 7 WHERE id=:i")

    def test_m3c_legacy_exemption_refuses_a_service_only_change(self):
        """SB1-R2 blind spot M9 (service): same start, different interval."""
        self._legacy_change_refused(
            "UPDATE revisions SET service_minutes = service_minutes + 30 WHERE id=:i")

    def test_m3d_legacy_exemption_refuses_a_buffer_only_change(self):
        """SB1-R2 blind spot M9 (buffer): same start, different interval."""
        self._legacy_change_refused("UPDATE revisions SET buffer_minutes = 0 WHERE id=:i")

    def test_m4_owner_checkpoint_backfill_is_atomic(self):
        from sqlalchemy.orm import Session
        from app.services import booking_representation as br
        st = type(self).state
        with self.engine.connect() as c:
            tx = c.begin()
            with Session(bind=c, join_transaction_mode="create_savepoint") as s:
                br.designate_launch_professional(s, st["prof"])
                dry = br.backfill_legacy_attribution(s)
                self.assertEqual((dry.eligible, dry.attributed, dry.dry_run), (5, 0, True))
                # the legacy overlapping pair makes the whole backfill fail; nothing attributed
                sp = c.begin_nested()
                with self.assertRaises(IntegrityError) as ctx:
                    br.backfill_legacy_attribution(s, dry_run=False)
                sp.rollback()
                self.assertEqual(_sqlstate(ctx.exception), "23P01")
                self.assertEqual(c.execute(text(
                    "SELECT count(*) FROM revisions WHERE agenda_occupying AND "
                    "profesional_id IS NULL")).scalar(), 5)
                # operator resolves the pair; then the backfill completes
                c.execute(text("UPDATE revisions SET estado_revision='REPROGRAMAR' WHERE id=:i"),
                          {"i": st["ids"]["pair_2_overlaps_1"]})
                done = br.backfill_legacy_attribution(s, dry_run=False)
                self.assertEqual((done.eligible, done.attributed), (4, 4))
                self.assertEqual(c.execute(text(
                    "SELECT count(*) FROM revisions WHERE agenda_occupying AND "
                    "profesional_id IS NULL")).scalar(), 0)
            tx.rollback()

    def test_m5_downgrade_restores_original_schema_and_values(self):
        st = type(self).state
        _alembic(PG_BASE_URL, "downgrade", PARENT)
        with self.engine.connect() as c:
            self.assertEqual(_version(c), PARENT)
            self.assertEqual(_fingerprint(c), st["fp_base"])
            self.assertEqual([tuple(r) for r in c.execute(text(
                f"SELECT {_BASE_COLUMNS} FROM revisions ORDER BY id")).all()], st["snapshot"])
            self.assertEqual([tuple(r) for r in c.execute(text(
                "SELECT * FROM profesionales ORDER BY id")).all()], st["prof_snapshot"])

    def test_m6_reupgrade_is_identical(self):
        st = type(self).state
        _alembic(PG_BASE_URL, "upgrade", HEAD)
        with self.engine.connect() as c:
            self.assertEqual(_fingerprint(c), st["fp_head"])


# ═════════════════════════════════════════════════════════════════════════════
# Extension lifecycle: a btree_gist that SB1 did not create must survive downgrade
# ═════════════════════════════════════════════════════════════════════════════

PG_EXT_URL = os.environ.get("SB1_PG_EXT_URL", "")


@unittest.skipUnless(PG_EXT_URL,
                     "SB1_PG_EXT_URL not set — no disposable pre-SB1 PostgreSQL; nothing proven")
class SB1PreexistingExtension(unittest.TestCase):
    """SB1-R2 blind spot M10. A third disposable database at the parent revision; btree_gist
    is installed BEFORE the upgrade, as another owner of the database might have. Ordered."""

    state: dict = {}
    EXT_SQL = "SELECT oid, extversion FROM pg_extension WHERE extname='btree_gist'"

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_EXT_URL)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_e1_extension_exists_before_sb1(self):
        with self.engine.begin() as c:
            self.assertEqual(_version(c), PARENT)
            self.assertIsNone(c.execute(text(self.EXT_SQL)).first())
            c.execute(text("CREATE EXTENSION btree_gist"))
            type(self).state["ext"] = tuple(c.execute(text(self.EXT_SQL)).one())
        with self.engine.connect() as c:
            type(self).state["fp_base"] = _fingerprint(c)

    def test_e2_upgrade_records_no_creation_marker(self):
        _alembic(PG_EXT_URL, "upgrade", HEAD)
        with self.engine.connect() as c:
            self.assertEqual(_version(c), HEAD)
            self.assertIsNone(c.execute(text(
                "SELECT obj_description(oid, 'pg_constraint') FROM pg_constraint "
                "WHERE conname=:n"), {"n": EXCLUSION}).scalar())
            self.assertEqual(tuple(c.execute(text(self.EXT_SQL)).one()), self.state["ext"])

    def test_e3_downgrade_keeps_the_preexisting_extension(self):
        _alembic(PG_EXT_URL, "downgrade", PARENT)
        with self.engine.connect() as c:
            self.assertEqual(_version(c), PARENT)
            # same extension object (oid), not dropped and re-created
            self.assertEqual(c.execute(text(self.EXT_SQL)).first(), self.state["ext"])
            self.assertEqual(_fingerprint(c), self.state["fp_base"])
            # and still usable: an integer equality under gist needs btree_gist
            c.execute(text("CREATE TEMP TABLE sb1_probe (k int, r tsrange, "
                           "EXCLUDE USING gist (k WITH =, r WITH &&))"))
            c.execute(text("INSERT INTO sb1_probe VALUES (1, '[2026-01-01 10:00, 2026-01-01 11:00)')"))
            with self.assertRaises(IntegrityError) as ctx:
                c.execute(text("INSERT INTO sb1_probe VALUES (1, '[2026-01-01 10:30, 2026-01-01 11:30)')"))
            self.assertEqual(_sqlstate(ctx.exception), "23P01")


if __name__ == "__main__":
    unittest.main()
