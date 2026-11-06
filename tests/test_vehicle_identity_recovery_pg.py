"""PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE — PostgreSQL half.

  VEHICLE_PG_URL      a disposable database at this tree's head
  VEHICLE_PG_MIG_URL  a second one; it is downgraded, broken on purpose and re-upgraded

Each skips cleanly without its variable, and says so. Real PostgreSQL 16, production functions;
the network edge is the harness recorder, so no socket is opened and nothing is sent.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import pathlib
import subprocess
import sys
import threading
import unittest
from datetime import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
PG_URL = os.environ.get("VEHICLE_PG_URL", "")
MIG_URL = os.environ.get("VEHICLE_PG_MIG_URL", "")
if PG_URL:
    os.environ["DATABASE_URL"] = PG_URL

import _sb2b_harness as H  # noqa: E402
import test_minimum_launch as ML  # noqa: E402
import test_vehicle_identity_recovery as V  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import Lead, Revision, SlotRecoveryJob  # noqa: E402
from app.services import booking_flow_service as bfs  # noqa: E402

BACKEND = pathlib.Path(__file__).resolve().parents[1] / "backend"
REV = "20261104_vehicle_identity"
PARENT = "20261103_slot_recovery_job"


def _truncate(engine):
    with engine.begin() as c:
        names = set(c.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")).scalars())
        tables = [t.name for t in Lead.__table__.metadata.sorted_tables if t.name in names]
        c.execute(text("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY CASCADE"))


def _alembic(url, *argv):
    """Alembic in-process against `url`; (rc, combined output). A refusal IS a result."""
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    prev = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    buf = io.StringIO()
    rc = 0
    try:
        with contextlib.redirect_stdout(buf):
            getattr(command, argv[0])(cfg, *argv[1:])
    except Exception as exc:                  # noqa: BLE001
        rc = 1
        buf.write(f"\nRAISED {type(exc).__name__}: {exc}")
    finally:
        if prev is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev
    return rc, buf.getvalue()


#: A NEW OS process: it shares nothing with the one that wrote the rows but the database.
_NEW_PROCESS = """
import json, os, sys
sys.path.insert(0, '.')
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.services.booking_flow_service import BookingFlowService
with Session(create_engine(os.environ['DATABASE_URL'])) as db:
    print(json.dumps(BookingFlowService(db).handle_init(sys.argv[1])['data']['details_prefill']))
"""


@unittest.skipUnless(PG_URL, "VEHICLE_PG_URL not set — no disposable PostgreSQL; nothing proven")
class PG_Recovery(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_URL, pool_size=10, max_overflow=5)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        _truncate(self.engine)

    def test_pgv_01_real_race_one_booking_and_the_loser_keeps_everything(self):
        at = time(10, 0)
        a, b = H.seed(self.engine, at=at), H.seed(self.engine, at=at)
        typed = {a.thread_id: V.WINNER, b.thread_id: V.TYPED}
        for w in (a, b):
            V.prepare(self.engine, w, typed=typed[w.thread_id])
        results, barrier = {}, threading.Barrier(2)

        def go(w):
            barrier.wait(30)
            try:
                results[w.thread_id] = V.confirm(self.engine, w, typed=typed[w.thread_id])
            except bfs.BookingSlotConflictError as exc:
                results[w.thread_id] = exc
        with ML.Meta() as request_side:
            threads = [threading.Thread(target=go, args=(w,)) for w in (a, b)]
            [t.start() for t in threads]
            [t.join(60) for t in threads]
        self.assertEqual(request_side.flows, [])
        losers = [w for w in (a, b) if isinstance(results[w.thread_id], Exception)]
        self.assertEqual(len(losers), 1)
        lose = losers[0]
        win = a if lose is b else b
        with Session(self.engine) as db:
            self.assertEqual(db.query(Revision).filter(Revision.lead_id == lose.lead_id).count(), 0)
            self.assertEqual(db.query(Revision).filter(Revision.lead_id == win.lead_id).count(), 1)
        with ML.Meta() as m:
            V.work(self.engine)
        self.assertEqual(len(m.flows), 1)
        token = V.fresh_token(self.engine, lose)
        want = V.EXPECTED_PREFILL if lose is b else \
            {k: v for k, v in V.WINNER.items()}
        self.assertEqual(V.init(self.engine, token)["data"]["details_prefill"], want)

    def test_pgv_02_a_new_process_reads_the_same_prefill_from_the_database_alone(self):
        win, lose = V.race(self.engine)
        with ML.Meta():
            V.work(self.engine)
        token = V.fresh_token(self.engine, lose)
        out = subprocess.run(
            [sys.executable, "-c", _NEW_PROCESS, token], cwd=str(BACKEND), text=True,
            capture_output=True, timeout=60,
            env={**os.environ, "DATABASE_URL": PG_URL,
                 "BOOKING_FLOW_DETAILS_PREFILL_ENABLED": "true"})
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        self.assertEqual(json.loads(out.stdout.strip().splitlines()[-1]), V.EXPECTED_PREFILL)

    def test_pgv_02b_grouped_mileage_through_refusal_new_process_and_fallback(self):
        win, lose = V.race(self.engine, typed={**V.TYPED, "vehicle_mileage": "1.234.567"})
        with ML.Meta():
            V.work(self.engine)
        token = V.fresh_token(self.engine, lose)
        out = subprocess.run(
            [sys.executable, "-c", _NEW_PROCESS, token], cwd=str(BACKEND), text=True,
            capture_output=True, timeout=60,
            env={**os.environ, "DATABASE_URL": PG_URL,
                 "BOOKING_FLOW_DETAILS_PREFILL_ENABLED": "true"})
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        self.assertEqual(json.loads(out.stdout.strip().splitlines()[-1])["vehicle_mileage"],
                         "1234567")
        with ML.Meta():
            V.confirm(self.engine, lose, token=token, at=time(11, 30),
                      typed={**V.TYPED, "vehicle_mileage": "1234567"})
        with Session(self.engine) as db:
            rev = db.query(Revision).filter(Revision.lead_id == lose.lead_id).one()
            self.assertEqual(rev.kilometraje, 1234567)

    def test_pgv_02c_the_grouped_ceiling_fits_the_real_column(self):
        w = H.seed(self.engine)
        with ML.Meta():
            V.confirm(self.engine, w, typed={**V.TYPED, "vehicle_mileage": "2.147.483.647"})
        with Session(self.engine) as db:
            rev = db.query(Revision).filter(Revision.lead_id == w.lead_id).one()
            self.assertEqual(rev.kilometraje, 2147483647)

    def test_pgv_03_fallback_booking_and_the_same_plate_on_two_inspections(self):
        win, lose = V.race(self.engine)
        with ML.Meta():
            V.work(self.engine)
        with ML.Meta():
            done = V.confirm(self.engine, lose, token=V.fresh_token(self.engine, lose),
                             at=time(11, 30))
            same_car = H.seed(self.engine, at=time(13, 0))
            V.confirm(self.engine, same_car)
        self.assertEqual(done["screen"], "SUCCESS")
        with Session(self.engine) as db:
            rows = db.query(Revision).filter(Revision.dominio == "AB123CD").all()
            self.assertEqual(len(rows), 2)
            self.assertEqual({r.kilometraje for r in rows}, {85000})
            self.assertEqual(db.query(SlotRecoveryJob).count(), 1)

    def test_pgv_04_details_kept_when_k1_was_held_through_prepare(self):
        """The R1 path: prepare_summary met a held K1 and wrote nothing; the confirm-time write
        is what keeps both values, and the recovery still offers them back."""
        at = time(10, 0)
        win, lose = H.seed(self.engine, at=at), H.seed(self.engine, at=at)
        with ML.Meta():
            V.confirm(self.engine, win, typed=V.WINNER)
        with self.engine.connect() as c:
            tx = c.begin()
            c.execute(text("SELECT 1 FROM whatsapp_thread_states WHERE thread_id = :t "
                           "FOR UPDATE"), {"t": lose.thread_id})
            V.prepare(self.engine, lose)
            tx.rollback()
        self.assertEqual(V.live(self.engine, lose.thread_id), {})
        with ML.Meta(), self.assertRaises(bfs.BookingSlotConflictError):
            V.confirm(self.engine, lose)
        with ML.Meta():
            V.work(self.engine)
        self.assertEqual(V.init(self.engine, V.fresh_token(self.engine, lose))
                         ["data"]["details_prefill"], V.EXPECTED_PREFILL)


class _OnPostgres:
    """The SQLite classes as they stand, on the real server: one engine, emptied per test."""

    @classmethod
    def setUpClass(cls):
        cls.pg = create_engine(PG_URL, pool_size=10, max_overflow=5)

    @classmethod
    def tearDownClass(cls):
        cls.pg.dispose()

    def setUp(self):
        _truncate(self.pg)
        self.engine = self.pg
        self.flow = V.Flow()


@unittest.skipUnless(PG_URL, "VEHICLE_PG_URL not set — no disposable PostgreSQL; nothing proven")
class PG_CardScope(_OnPostgres, V.Q_CardScope):
    pass


@unittest.skipUnless(PG_URL, "VEHICLE_PG_URL not set — no disposable PostgreSQL; nothing proven")
class PG_Summary(_OnPostgres, V.E2_Summary):
    pass


@unittest.skipUnless(PG_URL, "VEHICLE_PG_URL not set — no disposable PostgreSQL; nothing proven")
class PG_Rebinding(_OnPostgres, V.Q_Rebinding):
    pass


@unittest.skipUnless(PG_URL, "VEHICLE_PG_URL not set — no disposable PostgreSQL; nothing proven")
class PG_CarContext(_OnPostgres, V.Q_CarContext):
    pass


@unittest.skipUnless(MIG_URL, "VEHICLE_PG_MIG_URL not set — no disposable PostgreSQL")
class PG_Migration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(MIG_URL)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        self.restore()

    def restore(self):
        """Back to head with the two canonical empty columns, whatever a test broke."""
        with self.engine.begin() as c:
            c.execute(text("DROP INDEX IF EXISTS ix_vehicle_probe"))
            c.execute(text("DELETE FROM revisions"))
            c.execute(text("DELETE FROM leads"))
            c.execute(text("ALTER TABLE revisions DROP COLUMN IF EXISTS kilometraje"))
            c.execute(text("ALTER TABLE revisions DROP COLUMN IF EXISTS dominio"))
            c.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": PARENT})
        rc, out = _alembic(MIG_URL, "upgrade", "head")
        self.assertEqual(rc, 0, out)

    def sql(self, statement, **p):
        with self.engine.begin() as c:
            return c.execute(text(statement), p)

    def version(self):
        return self.sql("SELECT string_agg(version_num, ',') FROM alembic_version").scalar()

    def columns(self):
        return dict(self.sql(
            "SELECT column_name, data_type FROM information_schema.columns WHERE "
            "table_name = 'revisions' AND column_name IN ('kilometraje', 'dominio')").all())

    def seed_rows(self):
        lead = self.sql("INSERT INTO leads (estado, necesita_humano, created_at) VALUES "
                        "('CONSULTA_NUEVA', false, now()) RETURNING id").scalar()
        for marca in ("Fiat", "Renault"):
            self.sql("INSERT INTO revisions (lead_id, marca, created_at, estado_revision) "
                     "VALUES (:l, :m, now(), 'PENDIENTE')", l=lead, m=marca)
        return lead

    def digest(self):
        rows = self.sql("SELECT id, lead_id, marca, estado_revision, booking_version "
                        "FROM revisions ORDER BY id").all()
        return hashlib.sha256(repr(rows).encode()).hexdigest(), len(rows)

    def version_width(self):
        return self.sql(
            "SELECT character_maximum_length FROM information_schema.columns WHERE "
            "table_name = 'alembic_version' AND column_name = 'version_num'").scalar()

    def test_pgm_00_the_revision_id_is_stored_whole_on_both_column_shapes(self):
        """The product requires that the id fits, not a width. Operational crm_test has
        varchar(128); a schema Alembic created itself has varchar(32). The full upgrade runs
        on each, and the id must come back whole."""
        original = self.version_width()
        self.addCleanup(self.sql, f"ALTER TABLE alembic_version ALTER COLUMN version_num "
                                  f"TYPE varchar({original})")
        self.assertEqual(len(REV), 25)
        for width in (128, 32):
            self.sql(f"ALTER TABLE alembic_version ALTER COLUMN version_num TYPE varchar({width})")
            self.restore()
            self.assertGreaterEqual(self.version_width(), len(REV))
            self.assertEqual(self.version(), REV)

    def test_pgm_01_upgrade_creates_two_nullable_columns_and_backfills_nothing(self):
        self.assertEqual(self.version(), REV)
        self.assertEqual(self.columns(),
                         {"kilometraje": "integer", "dominio": "character varying"})
        lead = self.seed_rows()
        before = self.digest()
        self.assertEqual(_alembic(MIG_URL, "downgrade", PARENT)[0], 0)
        self.assertEqual(self.digest(), before)                      # rows survive down…
        rc, out = _alembic(MIG_URL, "upgrade", "head")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"{REV}: CREATED", out)
        self.assertEqual(self.digest(), before)                      # …and up
        self.assertEqual(self.sql("SELECT count(*) FROM revisions WHERE lead_id = :l AND "
                                  "kilometraje IS NULL AND dominio IS NULL", l=lead).scalar(), 2)

    def test_pgm_02_rerun_is_a_no_op(self):
        rc, out = _alembic(MIG_URL, "upgrade", "head")
        self.assertEqual((rc, self.version()), (0, REV), out)

    def test_pgm_03_adopts_the_empty_canonical_columns(self):
        self.assertEqual(_alembic(MIG_URL, "stamp", PARENT)[0], 0)
        rc, out = _alembic(MIG_URL, "upgrade", "head")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"{REV}: ADOPTED_EMPTY_CANONICAL", out)

    def test_pgm_04_verifies_its_own_finished_state_holding_data(self):
        lead = self.seed_rows()
        self.sql("UPDATE revisions SET kilometraje = 1, dominio = 'ABC123' WHERE lead_id = :l",
                 l=lead)
        self.assertEqual(_alembic(MIG_URL, "stamp", PARENT)[0], 0)
        rc, out = _alembic(MIG_URL, "upgrade", "head")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"{REV}: VERIFIED_CANONICAL", out)
        self.assertEqual(self.sql("SELECT count(*) FROM revisions WHERE dominio = 'ABC123'"
                                  ).scalar(), 2)

    def test_pgm_05_adopts_one_empty_column_and_adds_the_other(self):
        self.sql("ALTER TABLE revisions DROP COLUMN dominio")
        self.assertEqual(_alembic(MIG_URL, "stamp", PARENT)[0], 0)
        rc, out = _alembic(MIG_URL, "upgrade", "head")
        self.assertEqual(rc, 0, out)
        self.assertIn("adopting the empty canonical part", out)
        self.assertEqual(len(self.columns()), 2)

    def test_pgm_06_a_malformed_column_refuses_and_changes_nothing(self):
        for name, ddl in (("kilometraje", "ADD COLUMN kilometraje bigint"),
                          ("kilometraje", "ADD COLUMN kilometraje integer DEFAULT 0"),
                          ("kilometraje", "ADD COLUMN kilometraje integer NOT NULL DEFAULT 0"),
                          ("dominio", "ADD COLUMN dominio text"),
                          ("dominio", "ADD COLUMN dominio varchar(16) UNIQUE")):
            with self.subTest(ddl=ddl):
                self.sql(f"ALTER TABLE revisions DROP COLUMN {name}")
                self.sql(f"ALTER TABLE revisions {ddl}")
                self.assertEqual(_alembic(MIG_URL, "stamp", PARENT)[0], 0)
                rc, out = _alembic(MIG_URL, "upgrade", "head")
                self.assertEqual(rc, 1, out)
                self.assertIn("not exactly the canonical column", out)
                self.assertEqual(self.version(), PARENT)
                self.restore()

    def test_pgm_07_an_index_on_a_column_refuses(self):
        self.sql("CREATE INDEX ix_vehicle_probe ON revisions (dominio)")
        self.assertEqual(_alembic(MIG_URL, "stamp", PARENT)[0], 0)
        rc, out = _alembic(MIG_URL, "upgrade", "head")
        self.assertEqual(rc, 1, out)
        self.assertIn("ix_vehicle_probe", out)

    def test_pgm_08_partial_schema_holding_data_refuses(self):
        lead = self.seed_rows()
        self.sql("UPDATE revisions SET dominio = 'ABC123' WHERE lead_id = :l", l=lead)
        self.sql("ALTER TABLE revisions DROP COLUMN kilometraje")
        self.assertEqual(_alembic(MIG_URL, "stamp", PARENT)[0], 0)
        rc, out = _alembic(MIG_URL, "upgrade", "head")
        self.assertEqual(rc, 1, out)
        self.assertIn("provenance is unknown", out)

    def test_pgm_09_downgrade_refuses_while_either_value_exists(self):
        lead = self.seed_rows()
        for sql, shown in (("kilometraje = 0", "revisions.kilometraje=2"),
                           ("dominio = 'ABC123'", "revisions.dominio=2")):
            with self.subTest(sql=sql):
                self.sql(f"UPDATE revisions SET {sql} WHERE lead_id = :l", l=lead)
                rc, out = _alembic(MIG_URL, "downgrade", PARENT)
                self.assertEqual(rc, 1, out)
                self.assertIn("downgrade REFUSED", out)
                self.assertIn(shown, out)
                self.assertEqual(self.version(), REV)
                self.assertEqual(len(self.columns()), 2)
                self.sql("UPDATE revisions SET kilometraje = NULL, dominio = NULL")

    def test_pgm_10_downgrade_refuses_a_column_that_is_not_its_own(self):
        self.sql("ALTER TABLE revisions DROP COLUMN dominio")
        self.sql("ALTER TABLE revisions ADD COLUMN dominio text")
        rc, out = _alembic(MIG_URL, "downgrade", PARENT)
        self.assertEqual(rc, 1, out)
        self.assertIn("not exactly this revision's column", out)

    def test_pgm_11_clean_downgrade_then_re_upgrade(self):
        rc, out = _alembic(MIG_URL, "downgrade", PARENT)
        self.assertEqual(rc, 0, out)
        self.assertIn("DROPPED", out)
        self.assertEqual((self.version(), self.columns()), (PARENT, {}))
        rc, out = _alembic(MIG_URL, "upgrade", "head")
        self.assertEqual((rc, self.version(), len(self.columns())), (0, REV, 2), out)

    def test_pgm_12_nothing_else_changes_and_no_uniqueness_exists(self):
        self.assertEqual(self.sql(
            "SELECT count(*) FROM pg_indexes WHERE tablename = 'revisions' AND "
            "(indexdef ILIKE '%dominio%' OR indexdef ILIKE '%kilometraje%')").scalar(), 0)
        for table in ("thread_revisions", "whatsapp_thread_candidates", "slot_recovery_jobs",
                      "whatsapp_thread_evidence"):
            self.assertEqual(self.sql(
                "SELECT count(*) FROM information_schema.columns WHERE table_name = :t AND "
                "column_name IN ('kilometraje', 'dominio')", t=table).scalar(), 0, table)


if __name__ == "__main__":
    unittest.main()
