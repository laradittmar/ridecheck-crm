"""PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION — the PostgreSQL half.

Real PostgreSQL 16, real concurrency (threads on separate pooled connections meeting at a
barrier), production functions only; the network edge is the `_sb2b_harness` recorder plus a
Flow recorder (no socket is ever opened).

  MINLAUNCH_PG_URL        a disposable database at this tree's Alembic head
  MINLAUNCH_PG_MIG_URL    a second one, for the migration rehearsal (it is downgraded,
                          broken on purpose and re-upgraded)
  MINLAUNCH_PG_STAMP_URL  one built by `create_all` + `alembic stamp head`
  MINLAUNCH_PG_R1_MIG_URL a third one at head, for the R1 recovery-job migration rehearsal

Each skips cleanly without its variable, and says so.

  D  concurrent opening messages for one customer — including the "+549…" and "549…" forms,
     after both executions have looked the phone up and found nothing — produce ONE lead, both
     callers receive its id, both thread links agree, and no orphan lead exists. Repeated.
  A  concurrent recovery ADMISSIONS for one losing offer: exactly one job (R1: the
     `slot_recovery_jobs` UNIQUE identity, not an attempt-event row), raised with the human
     flags; a real two-customer race through the Data Exchange produces one booking, one
     acknowledgement, NO provider call in the request, then — from the worker — one recovery
     text and one fresh Flow.
  R1 real K1 contention at admission and at the worker; two concurrent workers on one job;
     the details kept at confirm when prepare_summary met a held K1; the recovery-job
     migration rehearsed (upgrade, repeat, adoption, malformed adoption, refusal with rows,
     clean downgrade, re-upgrade); lead-identity callers under the real index.
  I  the migration: upgrade, repeat upgrade, the VERIFIED adoption branch, refusal of a
     malformed index (twice), refusal of pre-existing duplicate groups (never resolved),
     downgrade changes no row, refusal to drop a foreign index, clean downgrade, re-upgrade,
     and `create_all` building the identical index.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import os
import pathlib
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
PG_URL = os.environ.get("MINLAUNCH_PG_URL", "")
MIG_URL = os.environ.get("MINLAUNCH_PG_MIG_URL", "")
STAMP_URL = os.environ.get("MINLAUNCH_PG_STAMP_URL", "")
R1_MIG_URL = os.environ.get("MINLAUNCH_PG_R1_MIG_URL", "")
if PG_URL:
    os.environ["DATABASE_URL"] = PG_URL

import _sb2b_harness as H  # noqa: E402

from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.models import (BookingAttemptEvent, BookingCommandRecord, Lead, Revision,  # noqa: E402
                        WhatsAppContact, WhatsAppMessage, WhatsAppThread)

BACKEND = pathlib.Path(__file__).resolve().parents[1] / "backend"
REV = "20261102_lead_phone_identity"
PARENT = "20261027_w1f_conversation_memory"
#: R1: the tree's head now sits one additive revision above REV.
HEAD = "20261103_slot_recovery_job"
INDEX = "uq_leads_phone_identity"


def _truncate(engine):
    with engine.begin() as c:
        names = set(c.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")).scalars())
        tables = [t.name for t in Lead.__table__.metadata.sorted_tables if t.name in names]
        c.execute(text("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY CASCADE"))


def _together(*fns, timeout=60):
    """Run `fns` in threads that start at one barrier; return their results (or exceptions)."""
    barrier = threading.Barrier(len(fns))
    out = [None] * len(fns)

    def run(i, fn):
        try:
            barrier.wait(timeout)
            out[i] = fn()
        except BaseException as exc:          # noqa: BLE001 — returned to the test
            out[i] = exc
    ts = [threading.Thread(target=run, args=(i, f)) for i, f in enumerate(fns)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout)
    return out


def _indexdef(conn):
    return conn.execute(text(
        "SELECT pg_get_indexdef(i.indexrelid) FROM pg_index i JOIN pg_class c "
        "ON c.oid = i.indexrelid WHERE c.relname = :n"), {"n": INDEX}).scalar()


def _canonical():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "m_lpi", BACKEND / "migrations" / "versions" / f"{REV}.py")
    src = pathlib.Path(spec.origin).read_text()
    start = src.index("CANONICAL_INDEXDEF = (")
    block = src[start:src.index(")\n", start) + 1]
    ns: dict = {}
    exec(block, ns)                                   # the literal only, not the module
    return ns["CANONICAL_INDEXDEF"].format(schema="public")


# ══ D — concurrent first messages ═══════════════════════════════════════════════════════

@unittest.skipUnless(PG_URL, "MINLAUNCH_PG_URL not set — no disposable PostgreSQL; nothing proven")
class PG_D_ConcurrentLeads(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_URL, pool_size=20, max_overflow=10)
        cls.factory = sessionmaker(bind=cls.engine, autoflush=False)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        _truncate(self.engine)

    def test_pgd_01_index_is_present_and_canonical_at_head(self):
        with self.engine.connect() as c:
            self.assertEqual(_indexdef(c), _canonical())

    def test_pgd_02_the_database_refuses_a_plus_variant_duplicate(self):
        from sqlalchemy.exc import IntegrityError
        with self.engine.begin() as c:
            c.execute(text("INSERT INTO leads (estado, telefono, necesita_humano, created_at) "
                           "VALUES ('CONSULTA_NUEVA', '5491100000010', false, now())"))
        with self.assertRaises(IntegrityError) as cm:
            with self.engine.begin() as c:
                c.execute(text("INSERT INTO leads (estado, telefono, necesita_humano, "
                               "created_at) VALUES ('CONSULTA_NUEVA', '+5491100000010', "
                               "false, now())"))
        self.assertEqual(getattr(cm.exception.orig, "sqlstate", None), "23505")
        with self.engine.begin() as c:              # NULL and '' stay legal, repeatedly
            # R1 (F-11): so do digit-free values — no identity, exactly like NULL
            for v in ("NULL", "NULL", "''", "''", "'-'", "'-'", "'s/n'", "'s/n'", "'(  )'"):
                c.execute(text("INSERT INTO leads (estado, telefono, necesita_humano, "
                               f"created_at) VALUES ('CONSULTA_NUEVA', {v}, false, now())"))

    def _race_once(self, n):
        """One opening burst: both executions look the phone up (empty), meet, then create
        and link — exactly the observed read-empty/read-empty/double-insert sequence."""
        from app.api.leads import create_lead, list_leads
        from app.schemas.leads import LeadCreate
        digits = f"54911000{n:05d}"
        with Session(self.engine) as db:
            contact = WhatsAppContact(wa_id=digits, display_name="Fixture")
            db.add(contact)
            db.flush()
            thread = WhatsAppThread(contact_id=contact.id, lead_id=None, unread_count=0)
            db.add(thread)
            db.commit()
            thread_id = thread.id
        looked = threading.Barrier(2)

        def execution(form):
            db = self.factory()
            try:
                found = list_leads(form, db)                 # n8n "Find Lead"
                db.rollback()
                looked.wait(30)                              # both saw nothing
                lead_id = found[0].id if found else create_lead(
                    LeadCreate(telefono=form), db).id        # n8n "Create Lead"
                t = db.get(WhatsAppThread, thread_id)        # n8n ".../link"
                t.lead_id = lead_id
                db.commit()
                return (bool(found), lead_id)
            finally:
                db.close()
        a, b = _together(lambda: execution(f"+{digits}"), lambda: execution(digits))
        for r in (a, b):
            if isinstance(r, BaseException):
                raise r
        self.assertEqual((a[0], b[0]), (False, False), "both lookups must have been empty")
        with Session(self.engine) as db:
            leads = db.execute(text(
                "SELECT id FROM leads WHERE regexp_replace(telefono, '[^0-9]', '', 'g') = :d"),
                {"d": digits}).scalars().all()
            linked = db.get(WhatsAppThread, thread_id).lead_id
            orphans = db.execute(text(
                "SELECT count(*) FROM leads l WHERE regexp_replace(l.telefono, '[^0-9]', '', "
                "'g') = :d AND NOT EXISTS (SELECT 1 FROM whatsapp_threads t WHERE "
                "t.lead_id = l.id)"), {"d": digits}).scalar()
        self.assertEqual(len(leads), 1, f"trial {n}: {leads}")
        self.assertEqual(a[1], b[1])
        self.assertEqual(linked, leads[0])
        self.assertEqual(orphans, 0)

    def test_pgd_03_concurrent_opening_messages_create_one_lead_six_of_six(self):
        for n in range(1, 7):
            with self.subTest(trial=n):
                self._race_once(n)

    def test_pgd_04_many_simultaneous_creates_one_lead(self):
        from app.services.lead_identity import find_or_create_lead

        def make(form):
            db = self.factory()
            try:
                lead, created = find_or_create_lead(
                    db, telefono=form, values=dict(estado="CONSULTA_NUEVA",
                                                   necesita_humano=False))
                return lead.id, created
            finally:
                db.close()
        forms = ["+5491100099999", "5491100099999"] * 4
        out = _together(*[(lambda f=f: make(f)) for f in forms])
        for r in out:
            if isinstance(r, BaseException):
                raise r
        self.assertEqual(len({r[0] for r in out}), 1)
        self.assertEqual(sum(1 for r in out if r[1]), 1)
        with Session(self.engine) as db:
            self.assertEqual(db.query(Lead).count(), 1)


# ══ A — the recovery claim and the race, on PostgreSQL ══════════════════════════════════

@unittest.skipUnless(PG_URL, "MINLAUNCH_PG_URL not set — no disposable PostgreSQL; nothing proven")
class PG_A_Recovery(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_URL, pool_size=20, max_overflow=10)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        _truncate(self.engine)

    def test_pga_01_concurrent_admissions_create_exactly_one_job(self):
        """R1 adaptation. OLD: six concurrent attempt-event CLAIMS per offer, one admitted.
        NEW: six concurrent ADMISSIONS per losing offer (real worlds, real K1 on separate
        connections) create exactly one `slot_recovery_jobs` row, raised with the human flags;
        no attempt-event row is written by the recovery at all."""
        from types import SimpleNamespace
        from app.models import SlotRecoveryJob, WhatsAppThreadState
        from app.services import booking_slot_recovery as R
        worlds = [H.seed(self.engine) for _ in range(3)]
        with Session(self.engine) as db:
            offer_ids = {w.thread_id: db.execute(text(
                "SELECT id FROM whatsapp_booking_offers WHERE thread_id = :t"),
                {"t": w.thread_id}).scalar_one() for w in worlds}
        for w in worlds:
            ctx = SimpleNamespace(thread=SimpleNamespace(id=w.thread_id),
                                  lead=SimpleNamespace(id=w.lead_id),
                                  contact=SimpleNamespace(id=w.contact_id),
                                  candidate=SimpleNamespace(id=w.candidate_id))

            def admit(i, ctx=ctx, w=w):
                with Session(self.engine) as db:
                    return R.admit_recovery(db, ctx, losing_offer_id=offer_ids[w.thread_id],
                                            attempt_ref=f"dx:{i:064d}")
            out = _together(*[(lambda i=i: admit(i)) for i in range(6)])
            with self.subTest(thread=w.thread_id):
                for r in out:
                    if isinstance(r, BaseException):
                        raise r
                self.assertEqual(sorted(r.admitted for r in out), [False] * 5 + [True])
                self.assertEqual({r.job_id for r in out} - {None}, {next(
                    r.job_id for r in out if r.admitted)})
        with Session(self.engine) as db:
            jobs = db.query(SlotRecoveryJob).order_by(SlotRecoveryJob.id).all()
            self.assertEqual(len(jobs), 3)
            self.assertTrue(all(j.state == "PENDING" and j.attention_applied
                                and j.owns_state_flag and j.owns_lead_flag for j in jobs))
            for w in worlds:
                st = db.execute(select(WhatsAppThreadState).where(
                    WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
                self.assertEqual((st.needs_human, st.last_stage), (True, "SCHEDULING"))
                self.assertTrue(db.get(Lead, w.lead_id).necesita_humano)
            self.assertEqual(db.query(BookingAttemptEvent).filter(
                BookingAttemptEvent.reason.like("SLOT_RECOVERY%")).count(), 0)

    def test_pga_02_two_customers_one_slot_concurrently(self):
        from test_minimum_launch import Meta
        from app.services import booking_flow_service as bfs
        from app.services import booking_slot_recovery as R
        win, lose = H.seed(self.engine), H.seed(self.engine)

        def go(w):
            # The settings patch is applied ONCE around both threads below: a per-thread
            # `mock.patch` of one module attribute is undone by whichever thread exits first.
            with Session(self.engine) as db:
                try:
                    return bfs.BookingFlowService(db).handle_confirm_booking(w.token, {
                        "date": w.day.isoformat(), "time": w.at.strftime("%H:%M"),
                        "name": "Ana Fixture", "inspection_address": "Calle Falsa 123"})
                except bfs.BookingSlotConflictError as exc:
                    return exc
        with Meta() as m, mock.patch("app.settings.get_settings",
                                     lambda: H.settings(flag=True)):
            a, b = _together(lambda: go(win), lambda: go(lose))
            # R1: the Data Exchange requests made no recovery send — only the winner's ack
            in_request = [t for _to, t in m.sent]
            self.assertEqual((in_request, m.flows), ([H.OWNER_TEXT], []))
            with Session(self.engine) as db:                     # the background worker
                R.run_once(db)
        outcomes = sorted(["SUCCESS" if isinstance(x, dict) and x.get("screen") == "SUCCESS"
                           else type(x).__name__ for x in (a, b)])
        self.assertEqual(outcomes, ["BookingSlotConflictError", "SUCCESS"])
        winner, loser = (win, lose) if isinstance(a, dict) else (lose, win)
        with Session(self.engine) as db:
            self.assertEqual(db.query(Revision).count(), 1)
            self.assertEqual(db.query(BookingCommandRecord).one().thread_id, winner.thread_id)
            outs = {w.thread_id: db.execute(select(WhatsAppMessage.message_type,
                                                   WhatsAppMessage.status,
                                                   WhatsAppMessage.text)
                                            .where(WhatsAppMessage.thread_id == w.thread_id,
                                                   WhatsAppMessage.direction == "out")
                                            .order_by(WhatsAppMessage.id)).all()
                    for w in (winner, loser)}
        self.assertEqual([(t, s, x) for t, s, x in outs[winner.thread_id]],
                         [("text", "sent", H.OWNER_TEXT)])
        self.assertEqual([(t, s) for t, s, _x in outs[loser.thread_id]],
                         [("text", "sent"), ("flow", "sent")])
        self.assertEqual(outs[loser.thread_id][0][2], R.RECOVERY_TEXT)
        self.assertEqual(len(m.flows), 1)
        self.assertEqual(m.flows[0][0], loser.wa_id)


# ══ I — the migration, rehearsed ════════════════════════════════════════════════════════

def _alembic(url, *argv):
    """Run Alembic in-process against `url`; returns (rc, combined output)."""
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
    except Exception as exc:                  # noqa: BLE001 — the refusal IS the result
        rc, _ = 1, buf.write(f"\nRAISED {type(exc).__name__}: {exc}")
    finally:
        if prev is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev
    return rc, buf.getvalue()


@unittest.skipUnless(MIG_URL, "MINLAUNCH_PG_MIG_URL not set — no disposable PostgreSQL")
class PG_I_Migration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(MIG_URL)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def version(self):
        with self.engine.connect() as c:
            return c.execute(text("SELECT string_agg(version_num, ',') FROM alembic_version")
                             ).scalar()

    def idx(self):
        with self.engine.connect() as c:
            return _indexdef(c)

    def leads_digest(self):
        with self.engine.connect() as c:
            rows = c.execute(text("SELECT id, telefono, nombre, estado FROM leads ORDER BY id")
                             ).all()
        return hashlib.sha256(repr(rows).encode()).hexdigest(), len(rows)

    def sql(self, statement):
        with self.engine.begin() as c:
            c.execute(text(statement))

    def test_pgi_01_full_rehearsal(self):
        self.sql("DELETE FROM leads")
        # at head, created (R1: the tree's head is one additive revision above REV)
        self.assertEqual(self.version(), HEAD)
        self.assertEqual(self.idx(), _canonical())
        # repeat upgrade: nothing to do
        self.assertEqual(_alembic(MIG_URL, "upgrade", "head")[0], 0)
        self.assertEqual((self.version(), self.idx()), (HEAD, _canonical()))
        # downgrade with protected data: rows are kept byte-for-byte, only the index goes
        self.sql("INSERT INTO leads (estado, telefono, nombre, necesita_humano, created_at) "
                 "VALUES ('CONSULTA_NUEVA','5491100000020','Uno',false,now()),"
                 "('AGENDADO','+5491100000021','Dos',false,now()),"
                 "('CONSULTA_NUEVA',NULL,'SinTel',false,now())")
        before = self.leads_digest()
        rc, out = _alembic(MIG_URL, "downgrade", PARENT)
        self.assertEqual(rc, 0, out)
        self.assertIn("DROPPED (no row changed)", out)
        self.assertEqual((self.version(), self.idx()), (PARENT, None))
        self.assertEqual(self.leads_digest(), before)
        # re-upgrade
        rc, out = _alembic(MIG_URL, "upgrade", "head")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"{INDEX}=CREATED", out)
        self.assertEqual((self.version(), self.idx()), (HEAD, _canonical()))
        self.assertEqual(self.leads_digest(), before)
        # the VERIFIED branch: the version rewound over an already-upgraded schema
        self.assertEqual(_alembic(MIG_URL, "stamp", PARENT)[0], 0)
        rc, out = _alembic(MIG_URL, "upgrade", "head")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"{INDEX}=VERIFIED", out)
        self.assertEqual(self.version(), HEAD)

    def test_pgi_02_malformed_index_adoption_refuses(self):
        self.sql("DELETE FROM leads")
        self.assertEqual(_alembic(MIG_URL, "downgrade", PARENT)[0], 0)
        for bad in (f"CREATE INDEX {INDEX} ON leads (regexp_replace(telefono, '[^0-9]', '', "
                    f"'g')) WHERE telefono IS NOT NULL AND telefono <> ''",       # not unique
                    f"CREATE UNIQUE INDEX {INDEX} ON leads (telefono)",           # wrong key
                    # R1: the PRE-R1 canonical definition (raw-column predicate) is no
                    # longer canonical — digit-free values collided under it (F-11)
                    f"CREATE UNIQUE INDEX {INDEX} ON leads (regexp_replace(telefono, "
                    f"'[^0-9]', '', 'g')) WHERE telefono IS NOT NULL AND telefono <> ''"):
            with self.subTest(bad=bad[:40]):
                self.sql(bad)
                found = self.idx()
                rc, out = _alembic(MIG_URL, "upgrade", "head")
                self.assertEqual(rc, 1)
                self.assertIn("is not the canonical one", out)
                self.assertIn("nothing was created, altered or dropped", out)
                self.assertEqual((self.version(), self.idx()), (PARENT, found))
                self.sql(f"DROP INDEX {INDEX}")
        self.assertEqual(_alembic(MIG_URL, "upgrade", "head")[0], 0)

    def test_pgi_03_duplicate_groups_refuse_and_are_never_resolved(self):
        self.sql("DELETE FROM leads")
        self.assertEqual(_alembic(MIG_URL, "downgrade", PARENT)[0], 0)
        self.sql("INSERT INTO leads (estado, telefono, nombre, necesita_humano, created_at) "
                 "VALUES ('CONSULTA_NUEVA','5491100000030',NULL,false,now()),"
                 "('COORDINAR_DISPONIBILIDAD','+5491100000030','Real',false,now()),"
                 "('CONSULTA_NUEVA','5491100000031',NULL,false,now())")
        before = self.leads_digest()
        rc, out = _alembic(MIG_URL, "upgrade", "head")
        self.assertEqual(rc, 1, out)
        self.assertIn("1 group(s) of leads share one phone identity", out)
        self.assertIn("never chooses a winner", out)
        # R1 (F-09): lead ids only — never the digits and never a digest of them (an unsalted
        # sha256 of a 13-digit phone is brute-forceable; it is not anonymisation)
        self.assertRegex(out, r"group 1: lead_ids=\[\d+, \d+\]")
        self.assertNotIn("5491100000030", out)
        self.assertNotIn(hashlib.sha256(b"5491100000030").hexdigest()[:16], out)
        self.assertNotRegex(out, r"sha256")
        self.assertEqual((self.version(), self.idx()), (PARENT, None))
        self.assertEqual(self.leads_digest(), before)     # nothing merged, deleted or edited
        self.sql("DELETE FROM leads")
        self.assertEqual(_alembic(MIG_URL, "upgrade", "head")[0], 0)

    def test_pgi_04_downgrade_refuses_a_foreign_index_under_the_name(self):
        self.sql("DELETE FROM leads")
        self.assertEqual(_alembic(MIG_URL, "downgrade", PARENT)[0], 0)
        self.sql(f"CREATE UNIQUE INDEX {INDEX} ON leads (telefono)")
        self.assertEqual(_alembic(MIG_URL, "stamp", REV)[0], 0)
        found = self.idx()
        rc, out = _alembic(MIG_URL, "downgrade", PARENT)
        self.assertEqual(rc, 1)
        self.assertIn("downgrade REFUSED", out)
        self.assertEqual((self.version(), self.idx()), (REV, found))
        self.sql(f"DROP INDEX {INDEX}")
        self.assertEqual(_alembic(MIG_URL, "stamp", PARENT)[0], 0)
        self.assertEqual(_alembic(MIG_URL, "upgrade", "head")[0], 0)
        self.assertEqual(self.idx(), _canonical())


@unittest.skipUnless(STAMP_URL, "MINLAUNCH_PG_STAMP_URL not set — no create_all database")
class PG_I_CreateAll(unittest.TestCase):

    def test_pgi_10_create_all_builds_the_identical_index(self):
        eng = create_engine(STAMP_URL)
        try:
            with eng.connect() as c:
                self.assertEqual(_indexdef(c), _canonical())
        finally:
            eng.dispose()


# ══ R1 — real K1 contention, concurrent workers, details, lead callers ═════════════════

def _hold_k1(engine, thread_id):
    """Hold K1 (the thread-state row) on its own connection until the returned event is set."""
    held, release = threading.Event(), threading.Event()

    def run():
        with engine.connect() as c:
            tx = c.begin()
            c.execute(text("SELECT 1 FROM whatsapp_thread_states WHERE thread_id = :t "
                           "FOR UPDATE"), {"t": thread_id})
            held.set()
            release.wait(60)
            tx.rollback()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert held.wait(10), "K1 was not taken"
    return release, t


def _ctx(w):
    from types import SimpleNamespace
    return SimpleNamespace(thread=SimpleNamespace(id=w.thread_id),
                           lead=SimpleNamespace(id=w.lead_id),
                           contact=SimpleNamespace(id=w.contact_id),
                           candidate=SimpleNamespace(id=w.candidate_id))


@unittest.skipUnless(PG_URL, "MINLAUNCH_PG_URL not set — no disposable PostgreSQL; nothing proven")
class PG_R1_Recovery(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_URL, pool_size=20, max_overflow=10)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        _truncate(self.engine)

    def offer_id(self, w):
        with Session(self.engine) as db:
            return db.execute(text("SELECT id FROM whatsapp_booking_offers WHERE thread_id = :t"),
                              {"t": w.thread_id}).scalar_one()

    def work(self, now=None):
        from app.services import booking_slot_recovery as R
        with Session(self.engine) as db, mock.patch("app.settings.get_settings",
                                                    lambda: H.settings(flag=True)):
            return R.run_once(db, now=now)

    def job(self, w):
        from app.models import SlotRecoveryJob
        with Session(self.engine) as db:
            j = db.query(SlotRecoveryJob).filter(SlotRecoveryJob.thread_id == w.thread_id).one()
            db.expunge(j)
            return j

    def test_pgr_01_k1_held_at_admission_and_at_the_worker(self):
        from datetime import datetime, timedelta, timezone
        from test_minimum_launch import Meta
        from app.services import booking_slot_recovery as R
        w = H.seed(self.engine)
        release, t = _hold_k1(self.engine, w.thread_id)
        try:
            with Session(self.engine) as db:
                adm = R.admit_recovery(db, _ctx(w), losing_offer_id=self.offer_id(w))
            self.assertEqual((adm.admitted, adm.attention_applied), (True, False))
            with Session(self.engine) as db:
                rows = R.recovery_attention(db)
            self.assertIn("SLOT_RECOVERY_FLAGS_NOT_RAISED", rows[0]["reasons"])
            with Meta() as m:
                self.work()
            self.assertEqual((m.sent, m.flows), ([], []))      # nothing before the flags
            self.assertEqual(self.job(w).last_error, "K1_CONTENDED")
            with Session(self.engine) as db:
                self.assertIn("SLOT_RECOVERY_LOCK_CONTENDED",
                              R.recovery_attention(db)[0]["reasons"])
        finally:
            release.set()
            t.join(10)
        with Meta() as m:
            self.work(now=datetime.now(timezone.utc) + timedelta(minutes=5))
        j = self.job(w)
        self.assertEqual((j.state, j.attention_applied), ("RECOVERED", True))
        self.assertEqual(([x for _to, x in m.sent], len(m.flows)), ([R.RECOVERY_TEXT], 1))

    def test_pgr_02_two_concurrent_workers_one_claimant_one_send(self):
        from test_minimum_launch import Meta
        from app.services import booking_slot_recovery as R
        w = H.seed(self.engine)
        with Session(self.engine) as db:
            R.admit_recovery(db, _ctx(w), losing_offer_id=self.offer_id(w))
        with Meta() as m, mock.patch("app.settings.get_settings", lambda: H.settings(flag=True)):
            def one():
                with Session(self.engine) as db:
                    return R.run_once(db)
            out = _together(one, one)
        for r in out:
            if isinstance(r, BaseException):
                raise r
        self.assertEqual(sorted(len(r) for r in out), [0, 1])  # exactly one claimant
        self.assertEqual(([x for _to, x in m.sent], len(m.flows)), ([R.RECOVERY_TEXT], 1))
        self.assertEqual(self.job(w).state, "RECOVERED")

    def test_pgr_03_prepare_meets_a_held_k1_then_confirm_keeps_the_details(self):
        from test_minimum_launch import DETAILS, Meta, confirm, prepare
        from app.models import BookingAttentionCondition
        from app.services.booking_flow_details import live_details
        w = H.seed(self.engine)
        release, t = _hold_k1(self.engine, w.thread_id)
        try:
            self.assertEqual(prepare(self.engine, w)["screen"], "SUMMARY")
        finally:
            release.set()
            t.join(10)
        with Session(self.engine) as db:
            self.assertEqual(live_details(db, [w.thread_id]), {})   # the K1 refusal, measured
        with Meta():
            self.assertEqual(confirm(self.engine, w, name=DETAILS["name"],
                                     same_as_prepare=True)["screen"], "SUCCESS")
        with Session(self.engine) as db:
            live = live_details(db, [w.thread_id])[w.thread_id]
            self.assertEqual(live["FLOW_SELLER_PHONE"], DETAILS["seller_phone"])
            self.assertEqual(db.query(BookingAttentionCondition).count(), 0)
            self.assertEqual(db.execute(text(
                "SELECT seller_phone FROM thread_revisions WHERE thread_id = :t"),
                {"t": w.thread_id}).scalar_one(), DETAILS["seller_phone"])

    def test_pgr_04_lead_identity_callers_under_the_real_index(self):
        from app.services.lead_identity import assign_phone_if_free
        from app.ui.kanban_actions import ui_lead_create, ui_lead_update
        factory = sessionmaker(bind=self.engine, autoflush=True)
        with Session(self.engine) as db:
            a = Lead(estado="CONSULTA_NUEVA", telefono="5491100000801", necesita_humano=False)
            b = Lead(estado="CONSULTA_NUEVA", telefono="5491100000802", necesita_humano=False)
            db.add_all([a, b])
            db.commit()
            a_id, b_id = a.id, b.id
        db = factory()
        try:
            r = ui_lead_update(lead_id=b_id, nombre=None, apellido=None, telefono="+5491100000801",
                               tel=None, email=None, canal=None, compro_el_auto=None,
                               necesita_humano=None, estado=None, db=db)
            self.assertEqual(r.status_code, 409)
            r = ui_lead_create(nombre="X", apellido=None, telefono="5491100000801", tel=None,
                               email=None, canal=None, compro_el_auto=None, db=db)
            self.assertEqual(r.status_code, 409)
            self.assertIn(f"lead #{a_id}", r.body.decode())
        finally:
            db.close()
        with Session(self.engine) as db:
            self.assertEqual(db.get(Lead, b_id).telefono, "5491100000802")
            self.assertEqual(db.query(Lead).count(), 2)
            c1 = Lead(estado="CONSULTA_NUEVA", telefono=None, necesita_humano=False)
            c2 = Lead(estado="CONSULTA_NUEVA", telefono=None, necesita_humano=False)
            db.add_all([c1, c2])
            db.commit()
            ids = (c1.id, c2.id)

        def assign(lead_id):
            with Session(self.engine) as db:
                lead = db.get(Lead, lead_id)
                out = assign_phone_if_free(db, lead, "+5491100000899")
                db.commit()
                return out
        out = _together(lambda: assign(ids[0]), lambda: assign(ids[1]))
        for r in out:
            if isinstance(r, BaseException):
                raise r
        self.assertEqual(sorted(out), ["ASSIGNED", "HELD_BY_OTHER"])


# ══ R1 — the recovery-job migration, rehearsed ══════════════════════════════════════════

class _PGDeath(BaseException):
    """A simulated process death (not an Exception: no handler catches it)."""


@unittest.skipUnless(PG_URL, "MINLAUNCH_PG_URL not set — no disposable PostgreSQL; nothing proven")
class PG_R2_DispatchOff(unittest.TestCase):
    """R2 (review R1-01 / R1-02) on PostgreSQL 16: Smart Booking OFF with OUTBOUND on sends
    nothing at any state, and the real daemon worker stops before the Flow."""

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_URL, pool_size=20, max_overflow=10)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        import threading
        from app.services import booking_slot_recovery as R
        _truncate(self.engine)
        R._STOP = threading.Event()

    def tearDown(self):
        import threading
        from app.services import booking_slot_recovery as R
        R._STAGE_HOOK = None
        R.stop_worker(timeout=2.0)
        R._STOP = threading.Event()
        R._WORKER = None

    def admitted(self):
        from app.services import booking_slot_recovery as R
        w = H.seed(self.engine)
        with Session(self.engine) as db:
            oid = db.execute(text("SELECT id FROM whatsapp_booking_offers WHERE thread_id = :t"),
                             {"t": w.thread_id}).scalar_one()
            R.admit_recovery(db, _ctx(w), losing_offer_id=oid)
        return w

    def work(self, sb, now=None):
        from app.services import booking_slot_recovery as R
        with Session(self.engine) as db, mock.patch("app.settings.get_settings",
                                                    lambda: H.settings(flag=sb)):
            return R.run_once(db, now=now)

    def row(self, w):
        with Session(self.engine) as db:
            return db.execute(text(
                "SELECT state, outcome, last_error FROM slot_recovery_jobs WHERE thread_id = :t"),
                {"t": w.thread_id}).one()

    def offers(self, w):
        with Session(self.engine) as db:
            return [r[0] for r in db.execute(text(
                "SELECT status FROM whatsapp_booking_offers WHERE thread_id = :t ORDER BY id"),
                {"t": w.thread_id})]

    def test_pgo_01_pending_and_lease_expired_sending_flow_send_nothing(self):
        from datetime import datetime, timedelta, timezone
        from test_minimum_launch import Meta
        from app.services import booking_slot_recovery as R
        later = lambda m: datetime.now(timezone.utc) + timedelta(minutes=m)   # noqa: E731
        # PENDING, Smart Booking OFF, OUTBOUND_ENABLED=true (Meta sets it)
        w = self.admitted()
        with Meta() as m:
            self.work(False)
            self.work(True, now=later(10))          # the flags return: nothing stale
        self.assertEqual((m.sent, m.flows), ([], []))
        self.assertEqual(tuple(self.row(w)), ("HANDOFF", "DISPATCH_OFF", "AT_PROCESS"))
        # SENDING_FLOW left by a dead claimant, resumed with Smart Booking OFF
        _truncate(self.engine)
        w = self.admitted()
        with Meta() as m, mock.patch.object(R, "_send_flow", side_effect=_PGDeath("died")):
            with self.assertRaises(_PGDeath):
                self.work(True)
        self.assertEqual(self.row(w)[0], "SENDING_FLOW")
        with Meta() as m:
            self.work(False, now=later(5))
            self.work(True, now=later(15))
        self.assertEqual((m.sent, m.flows), ([], []))
        self.assertEqual(tuple(self.row(w)), ("HANDOFF", "DISPATCH_OFF", "AT_PROCESS"))
        self.assertEqual(self.offers(w), ["SUPERSEDED", "WITHDRAWN"])
        with Session(self.engine) as db:
            reasons = R.recovery_attention(db)[0]["reasons"]
        self.assertEqual(reasons, ["SLOT_RECOVERY_HANDOFF", "SLOT_RECOVERY_DISPATCH_OFF"])

    def test_pgo_02_the_daemon_worker_stops_between_text_and_flow(self):
        import threading
        from sqlalchemy.orm import sessionmaker
        from test_minimum_launch import Meta
        from app.services import booking_slot_recovery as R
        w = self.admitted()
        opened, closed = [], []

        class Tracked(Session):
            def __init__(s, *a, **k):
                super().__init__(*a, **k)
                opened.append(id(s))

            def close(s):
                closed.append(id(s))
                super().close()
        passed = threading.Event()
        stages = []

        def hook(stage, _job):
            stages.append(stage)
            if stage == "after_text":
                R.stop_worker(timeout=0)
            if stage == "claim" and stages.count("claim") >= 2:
                passed.set()
        with Meta() as m, mock.patch("app.db.SessionLocal",
                                     sessionmaker(bind=self.engine, class_=Tracked)), \
                mock.patch.object(R, "CHECK_INTERVAL_SECONDS", 0.01), \
                mock.patch("app.settings.get_settings", lambda: H.settings(flag=True)):
            R._STAGE_HOOK = hook
            worker = R.start_worker()
            self.assertTrue(worker.daemon)
            self.assertTrue(passed.wait(20))
            worker.join(5)
            self.assertFalse(worker.is_alive())
        self.assertEqual(([x for _to, x in m.sent], m.flows), ([R.RECOVERY_TEXT], []))
        self.assertEqual(tuple(self.row(w)), ("HANDOFF", "FLOW_WORKER_STOPPING",
                                              "AT_AFTER_TEXT"))
        self.assertTrue(opened)
        self.assertEqual(sorted(opened), sorted(closed))


def _r1_migration():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "m_srj", BACKEND / "migrations" / "versions" / f"{HEAD}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(R1_MIG_URL, "MINLAUNCH_PG_R1_MIG_URL not set — no disposable PostgreSQL")
class PG_R1_Migration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(R1_MIG_URL)
        cls.m = _r1_migration()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def version(self):
        with self.engine.connect() as c:
            return c.execute(text("SELECT string_agg(version_num, ',') FROM alembic_version")
                             ).scalar()

    def sql(self, statement, **p):
        with self.engine.begin() as c:
            return c.execute(text(statement), p)

    def shape(self):
        from app.services.schema_fingerprint import (column_fingerprint, matching,
                                                     table_fingerprint)
        with self.engine.connect() as c:
            jobs = table_fingerprint(c, "slot_recovery_jobs")
            conds = table_fingerprint(c, "booking_attention_conditions")
            col = column_fingerprint(c, "thread_revisions", "seller_phone")
        return (None if jobs is None else matching(self.m.F_JOBS_PERMITTED, jobs) is not None,
                None if conds is None else matching(self.m.F_CONDITIONS_PERMITTED, conds)
                is not None,
                None if col["column"] is None else self.m._column_canonical(col))

    def test_pgm_01_full_rehearsal(self):
        _truncate(self.engine)
        self.assertEqual(self.version(), HEAD)
        self.assertEqual(self.shape(), (True, True, True))
        # repeat upgrade: a no-op
        self.assertEqual(_alembic(R1_MIG_URL, "upgrade", "head")[0], 0)
        self.assertEqual((self.version(), self.shape()), (HEAD, (True, True, True)))
        # downgrade protection: a NONTERMINAL job (a customer still owed a recovery)
        self.sql("INSERT INTO slot_recovery_jobs (recovery_identity, thread_id, "
                 "losing_offer_id, state) VALUES (:i, 1, 1, 'PENDING')", i="a" * 64)
        rc, out = _alembic(R1_MIG_URL, "downgrade", REV)
        self.assertEqual(rc, 1, out)
        self.assertIn("downgrade REFUSED", out)
        self.assertIn("nonterminal recovery jobs=1", out)
        self.assertEqual((self.version(), self.shape()), (HEAD, (True, True, True)))
        # …and a TERMINAL one (forensic record) — still refused
        self.sql("UPDATE slot_recovery_jobs SET state = 'HANDOFF', outcome = 'TEXT_UNCERTAIN', "
                 "completed_at = now()")
        rc, out = _alembic(R1_MIG_URL, "downgrade", REV)
        self.assertEqual(rc, 1, out)
        self.assertIn("nonterminal recovery jobs=0", out)
        self.sql("DELETE FROM slot_recovery_jobs")
        # an attention condition — refused
        self.sql("INSERT INTO booking_attention_conditions (condition_uid, kind, reason, "
                 "thread_id) VALUES (:u, 'DETAILS_NOT_PERSISTED', 'X', 1)", u="b" * 64)
        self.assertEqual(_alembic(R1_MIG_URL, "downgrade", REV)[0], 1)
        self.sql("DELETE FROM booking_attention_conditions")
        # a booking seller phone — refused
        w = H.seed(self.engine)
        self.sql("INSERT INTO thread_revisions (thread_id, status, seller_phone) "
                 "VALUES (:t, 'draft', '1100000000')", t=w.thread_id)
        rc, out = _alembic(R1_MIG_URL, "downgrade", REV)
        self.assertEqual(rc, 1, out)
        self.assertIn("thread_revisions.seller_phone=1", out)
        self.assertEqual(self.sql("SELECT count(*) FROM thread_revisions").scalar(), 1)
        self.sql("UPDATE thread_revisions SET seller_phone = NULL")
        # clean downgrade: every row elsewhere preserved
        before = self.sql("SELECT count(*) FROM thread_revisions").scalar(), \
            self.sql("SELECT count(*) FROM leads").scalar()
        rc, out = _alembic(R1_MIG_URL, "downgrade", REV)
        self.assertEqual(rc, 0, out)
        self.assertIn("DROPPED", out)
        self.assertEqual((self.version(), self.shape()), (REV, (None, None, None)))
        self.assertEqual((self.sql("SELECT count(*) FROM thread_revisions").scalar(),
                          self.sql("SELECT count(*) FROM leads").scalar()), before)
        # re-upgrade
        rc, out = _alembic(R1_MIG_URL, "upgrade", "head")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"{HEAD}: CREATED", out)
        self.assertEqual((self.version(), self.shape()), (HEAD, (True, True, True)))
        # adoption: the version rewound over the upgraded schema (empty) → adopted
        self.assertEqual(_alembic(R1_MIG_URL, "stamp", REV)[0], 0)
        rc, out = _alembic(R1_MIG_URL, "upgrade", "head")
        self.assertEqual(rc, 0, out)
        self.assertIn("ADOPTED_EMPTY_CANONICAL", out)
        # …and with rows: VERIFIED, rows kept
        self.sql("INSERT INTO slot_recovery_jobs (recovery_identity, thread_id, "
                 "losing_offer_id, state) VALUES (:i, 1, 1, 'PENDING')", i="c" * 64)
        self.assertEqual(_alembic(R1_MIG_URL, "stamp", REV)[0], 0)
        rc, out = _alembic(R1_MIG_URL, "upgrade", "head")
        self.assertEqual(rc, 0, out)
        self.assertIn("VERIFIED_CANONICAL", out)
        self.assertEqual(self.sql("SELECT count(*) FROM slot_recovery_jobs").scalar(), 1)
        self.sql("DELETE FROM slot_recovery_jobs")

    def test_pgm_02_malformed_and_partial_adoption_refuse(self):
        _truncate(self.engine)
        self.assertEqual(_alembic(R1_MIG_URL, "downgrade", REV)[0], 0)
        # a same-named table of another shape
        self.sql("CREATE TABLE slot_recovery_jobs (id bigserial PRIMARY KEY, state text)")
        rc, out = _alembic(R1_MIG_URL, "upgrade", "head")
        self.assertEqual(rc, 1, out)
        self.assertIn("slot_recovery_jobs exists and is not exactly the canonical shape", out)
        self.assertIn("nothing was created, altered or dropped", out)
        self.assertEqual(self.version(), REV)
        self.sql("DROP TABLE slot_recovery_jobs")
        # a seller_phone column of another type
        self.sql("ALTER TABLE thread_revisions ADD COLUMN seller_phone text")
        rc, out = _alembic(R1_MIG_URL, "upgrade", "head")
        self.assertEqual(rc, 1, out)
        self.assertIn("seller_phone exists and is not exactly the canonical column", out)
        self.sql("ALTER TABLE thread_revisions DROP COLUMN seller_phone")
        # part of the schema present, holding data, the rest missing — unknown provenance
        self.sql("ALTER TABLE thread_revisions ADD COLUMN seller_phone varchar(40)")
        w = H.seed(self.engine)
        self.sql("INSERT INTO thread_revisions (thread_id, status, seller_phone) "
                 "VALUES (:t, 'draft', '1')", t=w.thread_id)
        rc, out = _alembic(R1_MIG_URL, "upgrade", "head")
        self.assertEqual(rc, 1, out)
        self.assertIn("provenance is unknown", out)
        self.assertEqual(self.version(), REV)
        self.sql("DELETE FROM thread_revisions")
        self.sql("ALTER TABLE thread_revisions DROP COLUMN seller_phone")
        self.assertEqual(_alembic(R1_MIG_URL, "upgrade", "head")[0], 0)
        self.assertEqual((self.version(), self.shape()), (HEAD, (True, True, True)))


if __name__ == "__main__":
    unittest.main()
