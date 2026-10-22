"""W1-F2 on PostgreSQL — the operator notice's guarantees where SQLite cannot prove them, and the
migration's upgrade / downgrade / refusal round trip.

  W1F_NOTIFY_PG_URL  a disposable PostgreSQL 16 database at the Alembic head
  W1F_MIG_URL        a disposable PostgreSQL 16 database at 20261016_notification_sent_at
                     (the parent head), for the migration round trip
Never crm or crm_test. Meta is a recorder; `urlopen` raises.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
import threading
import unittest
from datetime import datetime, time, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _sb2b_harness as H  # noqa: E402
from test_w1f_operator_signal import (DEST, Template, book, notifier,  # noqa: E402
                                      operator_outbound)

from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import BookingCommandRecord, OperatorNotification  # noqa: E402
from app.services import operator_notification as ON  # noqa: E402

NOTIFY_URL = os.environ.get("W1F_NOTIFY_PG_URL", "")
MIG_URL = os.environ.get("W1F_MIG_URL", "")
BACKEND = pathlib.Path(__file__).resolve().parents[1] / "backend"
PARENT = "20261016_notification_sent_at"
HEAD = "20261022_operator_notifications"


def _slots():
    tue = H.slot_day()
    mon = tue - timedelta(days=1)
    yield from ((tue, time(10, 0)), (tue, time(12, 0)), (mon, time(14, 0)), (mon, time(16, 0)))


@unittest.skipUnless(NOTIFY_URL, "W1F_NOTIFY_PG_URL not set — no disposable PostgreSQL")
class Q_NoticePg(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(NOTIFY_URL)
        cls.slots = _slots()
        with cls.engine.connect() as c:
            cls.floor = int(c.execute(text("SELECT value FROM system_settings WHERE key = :k"),
                                      {"k": ON.FLOOR_KEY}).scalar())

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def booked(self):
        day, at = next(self.slots)
        w = book(self.engine, day=day, at=at)
        with Session(self.engine) as db:
            led = db.execute(select(BookingCommandRecord).where(
                BookingCommandRecord.thread_id == w.thread_id)).scalar_one()
        self.assertGreater(led.id, self.floor)
        return w, led

    def race(self, fn, n=2):
        barrier, results, errors = threading.Barrier(n), [None] * n, []

        def worker(i):
            db = H.session_factory(self.engine)()
            try:
                barrier.wait(timeout=10)
                results[i] = fn(db)
            except Exception as exc:          # pragma: no cover - reported below
                errors.append(exc)
            finally:
                db.close()
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        return results

    def test_q01_concurrent_enqueue_and_claim_one_row_one_claim(self):
        _w, led = self.booked()
        t0 = datetime.now(timezone.utc)
        with notifier():
            enq = self.race(lambda db: ON.enqueue_committed_bookings(db, now=t0), n=4)
            self.assertEqual(sorted(x for r in enq for x in r if x == led.id), [led.id])
            claims = [c for c in self.race(lambda db: ON.claim_next(db, now=t0), n=4) if c]
        self.assertEqual(len(claims), 1, claims)
        with Session(self.engine) as db:
            rows = db.execute(select(OperatorNotification).where(
                OperatorNotification.booking_command_id == led.id)).scalars().all()
        self.assertEqual([(r.status, r.attempts) for r in rows], [("SENDING", 1)])

    def test_q02_end_to_end_one_notice_replays_add_nothing(self):
        w, led = self.booked()
        with notifier() as tpl:
            db = H.session_factory(self.engine)()
            ON.run_once(db)
            db.close()
            with H.Meta() as meta:
                self.assertEqual(H.dx_confirm(self.engine, w, day=w.day, at=w.at)["screen"],
                                 "SUCCESS")
                H.run_turn(self.engine, H.flow_event(w))
            self.assertEqual(meta.sent, [])
            for k in range(3):
                db = H.session_factory(self.engine)()
                ON.run_once(db, now=datetime.now(timezone.utc) + timedelta(minutes=k + 1))
                db.close()
        mine = [s for s in tpl.sent if f"Reserva {led.id} " in s["params"][8]]
        self.assertEqual(len(mine), 1)
        with Session(self.engine) as db:
            n = db.execute(select(OperatorNotification).where(
                OperatorNotification.booking_command_id == led.id)).scalar_one()
        self.assertEqual((n.status, n.attempts), ("SENT", 1))
        self.assertEqual([o.wa_message_id for o in operator_outbound(self.engine)
                          if o.id == n.outbound_message_id], [n.provider_message_id])

    def test_q03_unique_constraints_are_the_duplicate_authority(self):
        _w, led = self.booked()
        with Session(self.engine) as db, notifier():
            ON.enqueue_committed_bookings(db)
        from sqlalchemy.exc import IntegrityError
        ident = ON.notification_identity(led.idempotency_key)
        for name, booking_command_id, identity in (
                ("same booking command", led.id, "a" * 64),
                ("same identity", self.free_ledger_id(), ident)):
            with self.subTest(name), self.assertRaises(IntegrityError), self.engine.begin() as c:
                c.execute(text(
                    "INSERT INTO operator_notifications (booking_command_id, identity, kind, "
                    "status, provider) VALUES (:b, :i, 'BOOKING_CREATED', 'PENDING', 'P')"),
                    {"b": booking_command_id, "i": identity})

    def free_ledger_id(self):
        """A ledger row with no notice yet (only the identity can then collide)."""
        with self.engine.begin() as c:
            return c.execute(text(
                "INSERT INTO booking_commands (idempotency_key, source, operation, "
                "request_fingerprint, occupying) VALUES (:k, 'CRM', 'CREATE', 'fp', false) "
                "RETURNING id"), {"k": f"w1f-q03-{datetime.now().timestamp()}"}).scalar()

    def test_q04_checks_refuse_impossible_states(self):
        _w, led = self.booked()
        with Session(self.engine) as db, notifier():
            ON.enqueue_committed_bookings(db)
        from sqlalchemy.exc import IntegrityError
        for sql in ("UPDATE operator_notifications SET attempts = 3 WHERE booking_command_id = :i",
                    "UPDATE operator_notifications SET status = 'DONE' WHERE booking_command_id = :i",
                    "UPDATE operator_notifications SET kind = 'X' WHERE booking_command_id = :i"):
            with self.subTest(sql=sql[29:55]), self.assertRaises(IntegrityError), \
                    self.engine.begin() as c:
                c.execute(text(sql), {"i": led.id})


def _alembic(*args):
    env = {**os.environ, "DATABASE_URL": MIG_URL}
    return subprocess.run([sys.executable, "-m", "alembic", *args], env=env, cwd=str(BACKEND),
                          capture_output=True, text=True, timeout=300)


def _migration():
    spec = importlib.util.spec_from_file_location(
        "m20261022", BACKEND / "migrations" / "versions" / "20261022_operator_notifications.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@unittest.skipUnless(MIG_URL, "W1F_MIG_URL not set — no migration database; nothing proven")
class M_MigrationPg(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine(MIG_URL)

    def tearDown(self):
        self.engine.dispose()

    def q(self, sql, **p):
        with self.engine.connect() as c:
            return c.execute(text(sql), p).all()

    def x(self, sql, **p):
        with self.engine.begin() as c:
            c.execute(text(sql), p)

    def version(self):
        return [r[0] for r in self.q("SELECT version_num FROM alembic_version")]

    def floor(self):
        return [r[0] for r in self.q("SELECT value FROM system_settings WHERE key = :k",
                                     k=ON.FLOOR_KEY)]

    def other_columns(self):
        return self.q("SELECT table_name, column_name, data_type, is_nullable, column_default "
                      "FROM information_schema.columns WHERE table_schema = current_schema() "
                      "AND table_name <> 'operator_notifications' ORDER BY 1, 2")

    def test_m01_round_trip_refusals_and_adoption(self):
        from app.services.schema_fingerprint import table_fingerprint
        from app.services.schema_preflight import missing_declared_booking_schema
        m = _migration()
        self.assertEqual(self.version(), [PARENT])
        # two committed ledger rows exist before the upgrade: the floor must be the max
        for k in ("w1f-mig-a", "w1f-mig-b"):
            self.x("INSERT INTO booking_commands (idempotency_key, source, operation, "
                   "request_fingerprint, occupying) VALUES (:k, 'CRM', 'CREATE', 'fp', false) "
                   "ON CONFLICT DO NOTHING", k=k)
        top = self.q("SELECT max(id) FROM booking_commands")[0][0]
        before = self.other_columns()

        up = _alembic("upgrade", "head")
        self.assertEqual(up.returncode, 0, up.stderr[-1500:])
        self.assertIn(f"{HEAD}: CREATED floor={top}", up.stdout + up.stderr)
        self.assertEqual((self.version(), self.floor()), ([HEAD], [str(top)]))
        with self.engine.connect() as c:
            self.assertEqual(table_fingerprint(c, "operator_notifications"), m._F_CANONICAL)
        self.assertEqual(missing_declared_booking_schema(self.engine), [])
        self.assertEqual(self.other_columns(), before, "the upgrade changed another table")

        # a row is evidence: the downgrade refuses and changes nothing
        self.x("INSERT INTO operator_notifications (booking_command_id, identity, kind, status, "
               "provider) VALUES (:b, :i, 'BOOKING_CREATED', 'SENT', 'META_WHATSAPP_TEMPLATE')",
               b=top, i="f" * 64)
        down = _alembic("downgrade", PARENT)
        self.assertNotEqual(down.returncode, 0)
        self.assertIn("Downgrade refused", down.stdout + down.stderr)
        self.assertEqual((self.version(), self.floor()), ([HEAD], [str(top)]))
        self.x("DELETE FROM operator_notifications")

        down = _alembic("downgrade", PARENT)
        self.assertEqual(down.returncode, 0, down.stderr[-1500:])
        self.assertEqual(self.version(), [PARENT])
        self.assertEqual(self.q("SELECT to_regclass('operator_notifications')")[0][0], None)
        self.assertEqual(self.floor(), [], "the downgrade left its floor behind")
        self.assertEqual(self.other_columns(), before, "the downgrade changed another table")

        # the application's create_all shape, EMPTY, is adopted exactly
        import app.models as models
        models.OperatorNotification.__table__.create(self.engine)
        up = _alembic("upgrade", "head")
        self.assertEqual(up.returncode, 0, up.stderr[-1500:])
        self.assertIn(f"{HEAD}: ADOPTED_EMPTY_CANONICAL", up.stdout + up.stderr)
        self.assertEqual(self.floor(), [str(top)])

        # this revision's own finished state, version rewound: verified, nothing changed
        self.x("INSERT INTO operator_notifications (booking_command_id, identity, kind, status, "
               "provider) VALUES (:b, :i, 'BOOKING_CREATED', 'SENT', 'META_WHATSAPP_TEMPLATE')",
               b=top, i="d" * 64)
        self.x("UPDATE alembic_version SET version_num = :v", v=PARENT)
        up = _alembic("upgrade", "head")
        self.assertEqual(up.returncode, 0, up.stderr[-1500:])
        self.assertIn(f"{HEAD}: VERIFIED_CANONICAL floor={top}", up.stdout + up.stderr)
        self.assertEqual(self.q("SELECT count(*) FROM operator_notifications")[0][0], 1)
        self.x("DELETE FROM operator_notifications")

        # refusals, each with nothing changed: a row in the found table; a widened CHECK;
        # a floor key of unknown provenance
        self.assertEqual(_alembic("downgrade", PARENT).returncode, 0)
        for name, setup in (
                ("row", ["CREATE_ORM",
                         f"INSERT INTO operator_notifications (booking_command_id, identity, "
                         f"kind, status, provider) VALUES ({top}, '{'e' * 64}', "
                         f"'BOOKING_CREATED', 'PENDING', 'P')"]),
                ("widened", ["CREATE_ORM",
                             "ALTER TABLE operator_notifications DROP CONSTRAINT "
                             "ck_operator_notifications_attempts",
                             "ALTER TABLE operator_notifications ADD CONSTRAINT "
                             "ck_operator_notifications_attempts CHECK (attempts >= 0)"]),
                ("floor", [f"INSERT INTO system_settings (key, value, updated_at) VALUES "
                           f"('{ON.FLOOR_KEY}', '0', now())"])):
            with self.subTest(refusal=name):
                for sql in setup:
                    if sql == "CREATE_ORM":
                        models.OperatorNotification.__table__.create(self.engine)
                    else:
                        self.x(sql)
                up = _alembic("upgrade", "head")
                self.assertNotEqual(up.returncode, 0, f"{name}: upgrade accepted")
                self.assertIn("Refusing before any change", up.stdout + up.stderr)
                self.assertEqual(self.version(), [PARENT])
                # undo the fixture only
                self.x("DROP TABLE IF EXISTS operator_notifications")
                self.x("DELETE FROM system_settings WHERE key = :k", k=ON.FLOOR_KEY)

        up = _alembic("upgrade", "head")
        self.assertEqual(up.returncode, 0, up.stderr[-1500:])
        self.assertEqual(self.version(), [HEAD])
        self.assertEqual(self.other_columns(), before)


if __name__ == "__main__":
    unittest.main()
