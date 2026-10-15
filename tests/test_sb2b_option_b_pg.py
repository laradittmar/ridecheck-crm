"""PRELAUNCH-SB2B-OPTION-B — the same cases on real PostgreSQL, plus what only PostgreSQL shows.

Disposable databases only; every class skips cleanly without its URL:

  SB2B_PG_URL        behaviour database at the Alembic head (built from a pre-SB1 tree by
                     create_all + stamp, then `alembic upgrade head` on this tree).
  SB2B_PG_MIG_URL    a database at 20261009_sb2a_r2_ack_lease holding ledger rows; this
                     milestone's migration is applied, reverted and re-applied on it.
  SB2B_PG_CLEAN_URL  a fresh database brought to head by the migrations alone.

What only PostgreSQL proves: the CE turn really holds no transaction id when the authority is
called (`txid_current_if_assigned()`), CE-W1 never waits on its own locks, CE-W2 after real
CE flushes does not self-wait, a real advisory-lock timeout is RETRYABLE and never confirmed,
and CE and the Data Exchange endpoint racing on one Flow submission produce exactly one
booking.
"""
from __future__ import annotations

import os
import pathlib
import sys
import threading
import time as _time
import unittest
from datetime import time
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
PG_URL = os.environ.get("SB2B_PG_URL", "")
PG_MIG_URL = os.environ.get("SB2B_PG_MIG_URL", "")
PG_CLEAN_URL = os.environ.get("SB2B_PG_CLEAN_URL", "")
if PG_URL:
    os.environ["DATABASE_URL"] = PG_URL

import _sb2b_harness as H  # noqa: E402
from test_sb2b_option_b import OptionBCases, _Spy  # noqa: E402

from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import BookingCommandRecord, Lead  # noqa: E402
from app.services.booking_authority import (DATE_LOCK_NAMESPACE, BookingAuthority,  # noqa: E402
                                            flow_idempotency_key)
from app.services.conversation_engine import ConversationEngine  # noqa: E402

_BACKEND = str(pathlib.Path(__file__).resolve().parents[1] / "backend")
PRE = "20261009_sb2a_r2_ack_lease"
SB2B = "20261013_sb2b_identity_outcome"


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


def _head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(Config(os.path.join(_BACKEND, "alembic.ini"))).get_current_head()


def _truncate(engine):
    with engine.begin() as c:
        names = set(c.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")).scalars())
        tables = [t.name for t in Lead.__table__.metadata.sorted_tables if t.name in names]
        c.execute(text("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY CASCADE"))


@unittest.skipUnless(PG_URL, "SB2B_PG_URL not set — no disposable PostgreSQL; nothing proven")
class PostgresOptionB(OptionBCases, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_URL)
        with cls.engine.connect() as c:
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == _head()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        _truncate(self.engine)


@unittest.skipUnless(PG_URL, "SB2B_PG_URL not set — no disposable PostgreSQL; nothing proven")
class PostgresOnly(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_URL, pool_size=10)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        _truncate(self.engine)

    def test_sb2b_pg_01_steady_state_has_no_transaction_id_and_no_self_wait(self):
        w = H.seed(self.engine)
        seen = []
        real = ConversationEngine._sb_transaction_clean

        def observe(eng):
            seen.append(eng.db.execute(text("SELECT txid_current_if_assigned()")).scalar())
            return real(eng)
        t0 = _time.monotonic()
        with mock.patch.object(ConversationEngine, "_sb_transaction_clean", observe), H.Meta() as m:
            out, eng = H.run_turn(self.engine, H.flow_event(w))
        elapsed = _time.monotonic() - t0
        self.assertEqual(out.action, "booking_created")
        # Convergence Phase 3 adaptation: the guard runs ONCE, immediately before delegation
        # (design §12 stage 5) — the earlier "entry" call is gone with the code it guarded.
        self.assertEqual(seen, [None])                       # at the call site: no txid
        self.assertLess(elapsed, 3.0)                        # lock_timeout is 5 s: no wait
        self.assertNotIn("LOCK_TIMEOUT", str(eng._sb_trace_payload()))
        self.assertEqual(m.sent, [(w.wa_id, H.OWNER_TEXT)])

    def test_sb2b_pg_02_a_flushed_lead_write_is_refused_without_calling_the_authority(self):
        # An "equivalent path" to the cycle reset WITH a still-valid token: something CE did
        # earlier in the turn flushed the lead row the plan would lock (L6). The guard is the
        # only thing between this turn and a self-wait.
        w = H.seed(self.engine)
        txids = []

        # Convergence Phase 3 adaptation: attribution is now written AFTER the booking
        # decision on a booking-shaped turn, so it can no longer flush before delegation.
        # The "something earlier in the turn flushed the lead row" premise is reproduced at a
        # step that still precedes delegation (the offer-bound candidate resolution).
        real_bound = ConversationEngine._offer_bound_candidate

        def flushing_attribution(eng, ctx, adm):
            ctx.lead.flag = "ACEPTADO"
            ctx.lead.canal = "fixture"
            eng.db.flush()
            txids.append(eng.db.execute(text("SELECT txid_current_if_assigned()")).scalar())
            return real_bound(eng, ctx, adm)
        with mock.patch.object(ConversationEngine, "_offer_bound_candidate", flushing_attribution), \
                _Spy("execute", BookingAuthority) as ex, H.Meta() as m:
            t0 = _time.monotonic()
            out, eng = H.run_turn(self.engine, H.flow_event(w))
            elapsed = _time.monotonic() - t0
        f = H.facts(self.engine, w)
        self.assertIsNotNone(txids[0])
        self.assertEqual((out.action, out.detail), ("booking_refused_handoff", "CE_TRANSACTION_NOT_CLEAN"))
        self.assertEqual((ex.calls, f["ledger"], f["occupying"]), (0, [], 0))
        self.assertLess(elapsed, 3.0)
        self.assertNotIn(H.OWNER_TEXT, [t for _w, t in m.sent])
        self.assertEqual((f["needs_human"], f["cursor"]), (True, w.wamid))

    def test_sb2b_pg_03_ce_w2_after_real_flushes_does_not_self_wait(self):
        w = H.seed(self.engine)
        db = H.session_factory(self.engine)()
        try:
            eng = ConversationEngine(db=db, settings=H.settings())
            eng._sb_trace = None
            ctx = eng._load_context(w.thread_id)
            ctx.inbound_wa_message_id = w.wamid + ".w2"
            st = ctx.state
            st.active_requested_date = w.day.isoformat()
            st.last_requested_time = "19:45"
            ctx.lead.flag = "ACEPTADO"; st.last_intent = "schedule"
            for c in ctx.candidates:
                c.marca = "Peugeot"
            db.flush()                                         # lead, state, candidate rows
            self.assertIsNotNone(db.execute(text("SELECT txid_current_if_assigned()")).scalar())
            eng._send_scheduling_handoff_email = lambda **k: None
            t0 = _time.monotonic()
            with H.Meta():
                out = eng._handle_scheduling_escalation(ctx, st, "quiero ese horario")
            elapsed = _time.monotonic() - t0
            trace = eng._sb_trace_payload()
        finally:
            db.close()
        f = H.facts(self.engine, w)
        self.assertEqual((trace["outcome"], trace["occupying"]), ("COMMITTED", False))
        self.assertLess(elapsed, 3.0)
        self.assertEqual(f["occupying"], 0)
        self.assertEqual((f["needs_human"], f["current_revision_id"]),
                         (True, f["thread_revisions"][0][0]))

    def test_sb2b_pg_04_real_lock_timeout_is_retry_required_never_confirmed(self):
        w = H.seed(self.engine)
        with Session(self.engine) as db:
            prof = db.execute(text("SELECT id FROM profesionales WHERE is_launch_default")).scalar()
        ymd = w.day.year * 10000 + w.day.month * 100 + w.day.day
        holder = self.engine.connect()
        tx = holder.begin()
        try:
            holder.execute(text("SELECT pg_advisory_xact_lock(:p, :d)"), {"p": prof, "d": ymd})
            with H.Meta() as m:
                out, eng = H.run_turn(self.engine, H.flow_event(w))
        finally:
            tx.rollback(); holder.close()
        f = H.facts(self.engine, w)
        # Convergence Phase 3 adaptation (CD-04): a real lock timeout is TRANSIENT — Track A's
        # contention contract: `thread_contended`, reply_required, nothing written, nothing
        # sent, the cursor not consumed, no handoff — never a terminal handoff.
        self.assertEqual((out.action, eng._sb_trace_payload()["reason"]),
                         ("thread_contended", "LOCK_TIMEOUT"))
        self.assertTrue(out.reply_required)
        self.assertEqual((f["ledger"], f["occupying"]), ([], 0))
        self.assertEqual((m.sent, f["cursor"], f["needs_human"]), ([], None, False))

    def _race(self, *, other_day: bool):
        """CE-W1 and the Data Exchange confirmation of ONE Flow submission, released together.

        Same day: both writers need the same (professional, date) advisory lock, so they
        rendezvous at `execute` and the lock orders them; the second re-checks the ledger
        under the lock. Different days: no advisory lock is shared, so they rendezvous after
        BOTH have written an occupying Revision (two uncommitted bookings exist at that
        instant); the lead row lock (L6) then orders them and the ledger's UNIQUE key turns
        the second into IDEMPOTENCY_KEY_REUSED, which each writer resolves by lookup."""
        from datetime import timedelta
        w = H.seed(self.engine)
        gate = threading.Barrier(2, timeout=20)
        # Convergence Phase 3 adaptation: since Phase 2 the authority takes the thread-state
        # row FIRST (K1), so two writers of one thread serialise there before either writes —
        # the "both hold an uncommitted booking" interleaving no longer exists on any day.
        # Both cases therefore rendezvous at `execute` and K1 orders them.
        point = "execute"
        real = getattr(BookingAuthority, point)
        results, errors = {}, []

        def rendezvous(auth, *a, **k):
            try:
                gate.wait()
            except threading.BrokenBarrierError:
                errors.append("barrier")
            return real(auth, *a, **k)

        def ce():
            out, _ = H.run_turn(self.engine, H.flow_event(w))
            results["ce"] = out.action

        def dx():
            day = w.day + timedelta(days=1) if other_day else None
            results["dx"] = H.dx_confirm(self.engine, w, day=day)["screen"]
        with mock.patch.object(BookingAuthority, point, rendezvous), H.Meta() as m:
            ts = [threading.Thread(target=ce), threading.Thread(target=dx)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(60)
        f = H.facts(self.engine, w)
        self.assertEqual(errors, [])                            # both really were in flight
        self.assertEqual(([k for k, *_ in f["ledger"]], f["occupying"]),
                         ([flow_idempotency_key(w.token)], 1))
        self.assertEqual(results.get("dx"), "SUCCESS")
        self.assertIn(results.get("ce"), ("booking_created", "booking_replayed"))
        self.assertLessEqual(len([t for _w, t in m.sent if t == H.OWNER_TEXT]), 1)
        self.assertEqual(f["ledger"][0][3], "SENT")
        return results

    def test_sb2b_pg_05_concurrent_ce_and_dx_same_slot_book_once(self):
        self._race(other_day=False)

    def test_sb2b_pg_06_concurrent_ce_and_dx_different_slots_book_once(self):
        self._race(other_day=True)


@unittest.skipUnless(PG_MIG_URL, "SB2B_PG_MIG_URL not set — nothing proven")
class Migration(unittest.TestCase):
    """Upgrade, downgrade, re-upgrade on a database holding pre-SB2B ledger rows."""

    def _cols(self, eng):
        with eng.connect() as c:
            return {r[0]: r[1:] for r in c.execute(text(
                "SELECT column_name, data_type, character_maximum_length, is_nullable, "
                "column_default FROM information_schema.columns "
                "WHERE table_name = 'booking_commands'")).all()}

    def _ver(self, eng):
        with eng.connect() as c:
            return c.execute(text("SELECT version_num FROM alembic_version")).scalar()

    def test_sb2b_m_01_additive_nullable_reversible_and_deterministic(self):
        eng = create_engine(PG_MIG_URL)
        try:
            if self._ver(eng) != PRE:                           # re-runnable: start at PRE
                _alembic(PG_MIG_URL, "downgrade", PRE)
            with eng.begin() as c:
                c.execute(text("DELETE FROM booking_commands WHERE idempotency_key='flow:legacyrow'"))
            self.assertEqual(self._ver(eng), PRE)
            with eng.begin() as c:                              # an existing pre-SB2B row
                c.execute(text(
                    "INSERT INTO booking_commands (idempotency_key, source, operation, "
                    "request_fingerprint, occupying, ack_status) VALUES "
                    "('flow:legacyrow', 'WHATSAPP_FLOW', 'CREATE', 'f', true, 'SENT')"))
            before = self._cols(eng)
            self.assertNotIn("identity_outcome", before)
            _alembic(PG_MIG_URL, "upgrade", SB2B)
            up = self._cols(eng)
            self.assertEqual(up["identity_outcome"], ("character varying", 16, "YES", None))
            self.assertEqual(set(up) - set(before), {"identity_outcome"})
            # Convergence Phase 2 added LATER ledger columns to the ORM model, so the ORM
            # (and with it `BookingAuthority.lookup`) can no longer read this table at the
            # intermediate SB2B revision — the model is ahead of this schema, as it is for every
            # other table at a pre-head revision. The old row is read with SQL here; the
            # authority's own read of a pre-existing ledger row at the head schema is asserted
            # in `test_convergence_p2_authority_pg.P2_50_Migration` (mg_02).
            with eng.connect() as c:                            # old rows read as "not recorded"
                self.assertEqual(tuple(c.execute(text(
                    "SELECT ack_status, identity_outcome FROM booking_commands WHERE "
                    "idempotency_key = 'flow:legacyrow'")).one()), ("SENT", None))
            _alembic(PG_MIG_URL, "downgrade", PRE)
            self.assertEqual((self._ver(eng), self._cols(eng)), (PRE, before))
            _alembic(PG_MIG_URL, "upgrade", SB2B)
            self.assertEqual((self._ver(eng), self._cols(eng)), (SB2B, up))   # re-upgrade
            with eng.connect() as c:
                self.assertEqual(c.execute(text(
                    "SELECT count(*) FROM booking_commands WHERE idempotency_key='flow:legacyrow' "
                    "AND identity_outcome IS NULL")).scalar(), 1)
        finally:
            eng.dispose()


@unittest.skipUnless(PG_CLEAN_URL, "SB2B_PG_CLEAN_URL not set — nothing proven")
class CleanInstall(unittest.TestCase):

    def test_sb2b_m_02_clean_install_matches_the_model(self):
        eng = create_engine(PG_CLEAN_URL)
        try:
            with eng.connect() as c:
                self.assertEqual(c.execute(text("SELECT version_num FROM alembic_version")).scalar(),
                                 _head())
                cols = set(c.execute(text("SELECT column_name FROM information_schema.columns "
                                          "WHERE table_name = 'booking_commands'")).scalars())
            self.assertEqual(cols, {col.name for col in BookingCommandRecord.__table__.columns})
            with Session(eng) as db:                            # the preflight accepts it
                from app.services.booking_authority import verify_booking_schema
                self.assertIsNone(verify_booking_schema(db))
        finally:
            eng.dispose()


if __name__ == "__main__":
    unittest.main()
