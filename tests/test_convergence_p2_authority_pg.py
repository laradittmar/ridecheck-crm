"""Convergence Phase 2 — the authority foundation, PROVEN ON REAL POSTGRESQL.

K1-first locking, the four-second bound, the typed offer acceptance (K2) and its every
binding, partial-unique-index winner replay, and the tester-reset terminal ledger state.

Disposable PostgreSQL 16 only:

  CONV_P2_PG_URL      behaviour database at the Alembic head, built the project's way — the
                      pre-branch models (`create_all` + `stamp 20261006_clarification_attempts`)
                      then `alembic upgrade head` with THIS tree, so every branch migration's
                      own DDL (SB1 trigger and exclusion constraint, the offer projection, the
                      detached-state trigger) really ran.
  CONV_P2_PG_MIG_URL  a second database built the same way, which the migration tests
                      downgrade and upgrade in place.

Both skip cleanly without their URL and say so: a concurrency claim that did not run is no
claim at all. Every assertion is on persisted STATE, timings or SQLSTATEs — never on source.

No external service is reachable from here: the harness runs on an internal Docker network,
OUTBOUND_ENABLED is never set, and the one outbound-gate call below is the probe proving the
gate refuses to run inside the booking transaction at all.
"""
from __future__ import annotations

import json
import os
import random
import socket
import sys
import threading
import time as _t
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest import mock

_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import create_engine, event, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

PG_URL = os.environ.get("CONV_P2_PG_URL", "")
PG_MIG_URL = os.environ.get("CONV_P2_PG_MIG_URL", "")

HEAD = "20261014_ledger_detached_state"
PARENT = "20261014_converge_track_heads"
#: Convergence Phase 4 adaptation: the script head moved to the one additive Phase 4 migration
#: (booking_attempt_events), a child of HEAD. The Phase 2 assertions below now name it where
#: they mean "the head", and still prove the Phase 2 revision itself.
P4_HEAD = "20261015_booking_attempt_events"
#: Convergence Phase 4-R1 adaptation: one more additive migration on top (the declared
#: `thread_revisions.notification_sent_at`, P4RV-04), a child of P4_HEAD. Where these tests
#: mean "the head" they now name it, and the chain HEAD ← P4_HEAD ← R1_HEAD is asserted.
R1_HEAD = "20261016_notification_sent_at"
#: W1-F2 adaptation: one more additive migration on top (`operator_notifications`), a child of
#: R1_HEAD. Where these tests mean "the head" they now name it; the chain is still asserted.
W1F_HEAD = "20261022_operator_notifications"
DAY = date(2027, 3, 2)                       # a Tuesday; ScheduleService is not consulted here
DAY2 = date(2027, 3, 3)
WA_A, WA_B = "549110000901", "549110000902"
TABLES = ("booking_commands", "whatsapp_booking_offers", "whatsapp_clarification_attempts",
          "whatsapp_messages", "thread_revisions", "whatsapp_thread_candidates",
          "whatsapp_thread_states", "calendar_exceptions", "revisions", "whatsapp_threads",
          "leads", "whatsapp_contacts", "profesionales", "ai_events")


def _ba():
    from app.services import booking_authority as ba
    return ba


def _sqlstate(exc):
    return getattr(getattr(exc, "orig", None), "sqlstate", None)


class _World:
    """One synthetic tester: contact, lead, thread, state (home zone CABA/Palermo, cycle 0),
    an AUTO candidate in focus, and helpers to issue offers and build acceptance commands."""

    def __init__(self, SF, wa):
        import app.models as M
        self.SF, self.wa = SF, wa
        db = SF()
        ct = M.WhatsAppContact(wa_id=wa, display_name="P2 Fixture"); db.add(ct); db.flush()
        lead = M.Lead(nombre="Probe", apellido="Fixture", telefono=wa,
                      estado="CONSULTA_NUEVA", flag="ACEPTADO"); db.add(lead); db.flush()
        th = M.WhatsAppThread(contact_id=ct.id, lead_id=lead.id); db.add(th); db.flush()
        st = M.WhatsAppThreadState(thread_id=th.id, last_stage="SCHEDULING",
                                   home_zone_group="CABA", home_zone_detail="Palermo")
        db.add(st); db.flush()
        cand = M.WhatsAppThreadCandidate(thread_id=th.id, marca="Ford", modelo="Focus",
                                         anio=2017, tipo_vehiculo="AUTO",
                                         status="current_focus", source_text="fixture")
        db.add(cand); db.flush()
        st.current_focus_candidate_id = cand.id
        db.commit()
        self.lead_id, self.thread_id, self.cand_id = lead.id, th.id, cand.id
        db.close()

    def issue(self, token, **bind):
        """A REAL offer through `OfferLifecycle.issue` (K1 → K2), committed."""
        import app.models as M
        from app.services.offer_lifecycle import OfferLifecycle
        db = self.SF()
        st = db.query(M.WhatsAppThreadState).filter_by(thread_id=self.thread_id).one()
        kw = dict(cycle_key=0, candidate_id=self.cand_id, tipo_vehiculo="AUTO",
                  zone_group="CABA", zone_detail="Palermo", offered_date=DAY,
                  offered_time=time(10), flow_id="28104222025943520")
        kw.update(bind)
        offer = OfferLifecycle(db).issue(st, thread_id=self.thread_id, token=token, **kw)
        db.commit()
        oid = offer.id
        db.close()
        return oid

    def acceptance(self, token, **over):
        ba = _ba()
        from app.services.offer_lifecycle import token_fingerprint
        kw = dict(token_fingerprint=token_fingerprint(token), thread_id=self.thread_id,
                  cycle_key=0, candidate_id=self.cand_id, tipo_vehiculo="AUTO",
                  zone_group="CABA", zone_detail="Palermo")
        kw.update(over)
        return ba.OfferAcceptance(**kw)

    def command(self, token, *, at=time(10), day=DAY, key=None, **cmd_over):
        ba = _ba()
        kw = dict(operation=ba.BookingOperation.CREATE,
                  source=ba.BookingSource.CONVERSATION_ENGINE,
                  idempotency_key=key or ba.flow_idempotency_key(token),
                  lead_id=self.lead_id, thread_id=self.thread_id, requested_date=day,
                  requested_time=at,
                  revision_fields={"tipo_vehiculo": "AUTO", "zone_group": "CABA",
                                   "zone_detail": "Palermo"},
                  tracks_acknowledgement=True)
        kw.update(cmd_over)
        return ba.BookingCommand(**kw)

    def plan(self, token, *, acceptance=None, candidate_id="same", buyer_phone=None,
             accept=True):
        ba = _ba()
        cid = self.cand_id if candidate_id == "same" else candidate_id
        acc = acceptance if acceptance is not None else (
            self.acceptance(token, candidate_id=cid) if accept else None)
        return ba.BookingAttachmentPlan(
            thread_revision=ba.ThreadRevisionAttachment(
                thread_id=self.thread_id, candidate_id=cid, tipo_vehiculo="AUTO",
                zone_group="CABA", buyer_phone=buyer_phone, request_approval=True),
            lead=ba.LeadUpdate(estado="COORDINAR_DISPONIBILIDAD", flag="ACEPTADO",
                               necesita_humano=True),
            thread_state=ba.ThreadStateUpdate(link_current_revision=True, last_stage="BOOKED",
                                              needs_human=True, consume_flow_token=True),
            offer_acceptance=acc)


class _Base(unittest.TestCase):
    URL = PG_URL

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(cls.URL, pool_size=24, max_overflow=16)
        cls.SF = sessionmaker(bind=cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        self.engine.dispose()
        with self.engine.begin() as c:
            c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                           "WHERE datname = current_database() AND pid <> pg_backend_pid()"))
        self.engine.dispose()
        with self.engine.begin() as c:
            for t in TABLES:
                c.execute(text(f"TRUNCATE {t} RESTART IDENTITY CASCADE"))
        import app.models as M
        db = self.SF()
        db.add(M.Profesional(nombre="Fixture", apellido="P2-FIXTURE",
                             email="fixture@example.invalid", resource_key="fixture.p2",
                             is_launch_default=True, is_active=True))
        db.commit(); db.close()

    # ── helpers ──────────────────────────────────────────────────────────────

    def run_cmd(self, cmd, plan=None, *, lock_timeout_ms=None, auth=None, availability=None):
        ba = _ba()
        s = Session(self.engine)
        try:
            kw = {} if lock_timeout_ms is None else {"lock_timeout_ms": lock_timeout_ms}
            return (auth or ba.BookingAuthority)(s, **kw).execute(
                cmd, attachments=plan, availability=availability)
        finally:
            s.close()

    def q(self, sql, **kw):
        with self.engine.connect() as c:
            return c.execute(text(sql), kw).all()

    def one(self, sql, **kw):
        with self.engine.connect() as c:
            return c.execute(text(sql), kw).scalar()

    def snapshot(self, w):
        """Everything a booking could mutate for this world, as plain values."""
        return {
            "revisions": self.q("SELECT id, turno_fecha, turno_hora, estado_revision, "
                                "profesional_id FROM revisions ORDER BY id"),
            "thread_revisions": self.q("SELECT id, thread_id, status, offer_id, scheduled_date, "
                                       "scheduled_time FROM thread_revisions ORDER BY id"),
            "ledger": self.q("SELECT id, idempotency_key, command_state, revision_id, "
                             "thread_revision_id, lead_id, thread_id, occupying, ack_status "
                             "FROM booking_commands ORDER BY id"),
            "offers": self.q("SELECT id, status, accepted_revision_id, accepted_thread_revision_id, "
                             "accepted_at, terminal_at, terminal_reason, refused_count, "
                             "replay_count FROM whatsapp_booking_offers ORDER BY id"),
            "state": self.q("SELECT thread_id, current_revision_id, last_stage, needs_human, "
                            "flow_booking_token, last_processed_inbound_wa_message_id, "
                            "current_focus_candidate_id FROM whatsapp_thread_states "
                            "ORDER BY thread_id"),
            "leads": self.q("SELECT id, estado, flag, necesita_humano, nombre FROM leads ORDER BY id"),
        }

    def assertNothingMoved(self, before, w, why):
        after = self.snapshot(w)
        for k in before:
            self.assertEqual(before[k], after[k], f"{why}: {k} changed")


# ══════════════════════════════════════════════════════════════════════════════
# 0. the schema is the migrated one
# ══════════════════════════════════════════════════════════════════════════════

@unittest.skipUnless(PG_URL, "CONV_P2_PG_URL not set — no disposable PostgreSQL; nothing proven")
class P2_00_Schema(_Base):

    def test_p2_pg_00_the_schema_is_the_migrated_head(self):
        # Convergence Phase 4 / 4-R1 adaptation: the migrated head is now 4-R1's, whose
        # parent is Phase 4's, whose parent is HEAD (asserted in mg_01).
        self.assertEqual([r[0] for r in self.q("SELECT version_num FROM alembic_version")],
                         [W1F_HEAD])
        self.assertEqual(self.one("SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND "
                                  "tgname = 'trg_booking_commands_detached_terminal'"), 1)
        names = {r[0] for r in self.q("SELECT conname FROM pg_constraint WHERE conrelid = "
                                      "'booking_commands'::regclass")}
        self.assertTrue({"ck_booking_commands_command_state",
                         "ck_booking_commands_detached_shape"} <= names)
        self.assertEqual(self.one("SELECT count(*) FROM pg_indexes WHERE indexname = "
                                  "'uq_thread_revision_one_booking_per_offer'"), 1)
        self.assertEqual(self.one("SELECT count(*) FROM pg_constraint WHERE conname = "
                                  "'ex_revisions_professional_occupied_overlap'"), 1)
        s = Session(self.engine)
        try:
            self.assertIsNone(_ba().verify_booking_schema(s))
        finally:
            s.close()


# ══════════════════════════════════════════════════════════════════════════════
# 1. K1 first, the four-second bound, contention writes nothing
# ══════════════════════════════════════════════════════════════════════════════

_LOCK_MARKERS = (
    ("K1", "FROM whatsapp_thread_states", "FOR NO KEY UPDATE"),
    ("K2", "FROM whatsapp_booking_offers", "FOR UPDATE"),
    ("K3", "pg_advisory_xact_lock_shared", ""),
    ("K4", "pg_advisory_xact_lock(", ""),
    ("K6", "FROM revisions r WHERE r.agenda_occupying", "FOR SHARE"),
    ("K5w", "INSERT INTO revisions", ""),
    ("K7", "INSERT INTO thread_revisions", ""),
    ("K8", "FROM leads", "FOR NO KEY UPDATE"),
    ("K9", "INSERT INTO booking_commands", ""),
)


def _rank_of(sql: str):
    for name, a, b in _LOCK_MARKERS:
        if a in sql and b in sql:
            return name
    return None


@unittest.skipUnless(PG_URL, "CONV_P2_PG_URL not set — no disposable PostgreSQL; nothing proven")
class P2_10_K1(_Base):

    def _trace(self, fn):
        """Every SQL statement the authority's connection sends, in order (this thread only)."""
        seen, me = [], threading.get_ident()

        def listener(conn, cursor, statement, params, context, executemany):
            if threading.get_ident() == me:
                seen.append(" ".join(statement.split()))
        event.listen(self.engine, "before_cursor_execute", listener)
        try:
            out = fn()
        finally:
            event.remove(self.engine, "before_cursor_execute", listener)
        return out, seen

    def test_p2_k1_01_k1_is_the_first_business_lock_and_every_rank_increases(self):
        w = _World(self.SF, WA_A); w.issue("tok-k1-01")
        r, seen = self._trace(lambda: self.run_cmd(w.command("tok-k1-01"), w.plan("tok-k1-01")))
        self.assertEqual(r.outcome.value, "COMMITTED", r.reason)
        ranks = [(i, _rank_of(s)) for i, s in enumerate(seen) if _rank_of(s)]
        names = [n for _, n in ranks]
        self.assertEqual(names[0], "K1", seen[:12])
        order = ["K1", "K2", "K3", "K4", "K6", "K5w", "K7", "K8", "K9"]
        firsts = [names.index(k) for k in order if k in names]
        self.assertEqual(firsts, sorted(firsts), names)
        self.assertTrue({"K1", "K2", "K3", "K4", "K7", "K8", "K9"} <= set(names), names)
        # the bound is set BEFORE the first business lock, never after
        k1_at = ranks[0][0]
        bound = [i for i, s in enumerate(seen) if s.startswith("SET LOCAL lock_timeout = 4000")]
        self.assertTrue(bound and bound[0] < k1_at, "lock_timeout not set before K1")
        stmt = [i for i, s in enumerate(seen) if s.startswith("SET LOCAL statement_timeout")]
        self.assertTrue(stmt and stmt[0] < k1_at)
        # no thread-state write and no lock of any kind happens before K1 (no implicit K1)
        for s in seen[:k1_at]:
            self.assertFalse(s.startswith(("UPDATE whatsapp_thread_states",
                                           "INSERT INTO whatsapp_thread_states")), s[:120])
            self.assertFalse(("FOR UPDATE" in s or "FOR SHARE" in s or "FOR NO KEY" in s
                              or "advisory" in s), f"a lock precedes K1: {s[:120]}")
        # after K1, every thread-state statement is the already-held row
        upd = [i for i, s in enumerate(seen) if s.startswith("UPDATE whatsapp_thread_states")]
        self.assertTrue(upd and min(upd) > k1_at)

    def test_p2_k1_02_the_bound_is_four_seconds_inside_the_transaction(self):
        ba = _ba()
        self.assertEqual(ba.DEFAULT_LOCK_TIMEOUT_MS, 4000)
        w = _World(self.SF, WA_A); w.issue("tok-k1-02")
        seen = {}

        class Probe(ba.BookingAuthority):
            def _revalidate_availability(inner, plan, target, lock_mode):
                seen["lock"] = inner.db.execute(text("SHOW lock_timeout")).scalar()
                seen["stmt"] = inner.db.execute(text("SHOW statement_timeout")).scalar()
        r = self.run_cmd(w.command("tok-k1-02"), w.plan("tok-k1-02"), auth=Probe,
                         availability=ba.AvailabilityRevalidation())
        self.assertEqual(r.outcome.value, "COMMITTED", r.reason)
        self.assertEqual(seen, {"lock": "4s", "stmt": "10s"})
        # LOCAL: the setting does not outlive the authority's transaction
        s = Session(self.engine)
        try:
            self.assertEqual(s.execute(text("SHOW lock_timeout")).scalar(), "0")
        finally:
            s.close()

    def _hold_k1(self, thread_id, seconds, started):
        c = self.engine.connect()
        tx = c.begin()
        c.execute(text("SELECT 1 FROM whatsapp_thread_states WHERE thread_id = :t "
                       "FOR NO KEY UPDATE"), {"t": thread_id})
        started.set()
        _t.sleep(seconds)
        tx.rollback(); c.close()

    def test_p2_k1_03_a_held_k1_is_a_bounded_typed_refusal_that_writes_nothing(self):
        w = _World(self.SF, WA_A); w.issue("tok-k1-03")
        before = self.snapshot(w)
        started = threading.Event()
        holder = threading.Thread(target=self._hold_k1, args=(w.thread_id, 7.0, started))
        holder.start(); started.wait(5)
        t0 = _t.monotonic()
        r = self.run_cmd(w.command("tok-k1-03"), w.plan("tok-k1-03"))
        elapsed = _t.monotonic() - t0
        holder.join(15)
        self.assertEqual((r.outcome.value, r.reason), ("RETRYABLE_FAILURE", "THREAD_STATE_CONTENDED"))
        self.assertTrue(r.retryable)
        self.assertGreater(elapsed, 3.6, elapsed)
        self.assertLess(elapsed, 4.6, elapsed)          # 4 s, not 5 s, not unbounded
        self.assertNothingMoved(before, w, "K1 contention")

    def test_p2_k1_04_retry_after_release_commits_exactly_once(self):
        w = _World(self.SF, WA_A); w.issue("tok-k1-04")
        started = threading.Event()
        holder = threading.Thread(target=self._hold_k1, args=(w.thread_id, 4.6, started))
        holder.start(); started.wait(5)
        first = self.run_cmd(w.command("tok-k1-04"), w.plan("tok-k1-04"))
        holder.join(15)
        self.assertEqual(first.outcome.value, "RETRYABLE_FAILURE")
        again = self.run_cmd(w.command("tok-k1-04"), w.plan("tok-k1-04"))
        self.assertEqual(again.outcome.value, "COMMITTED", again.reason)
        third = self.run_cmd(w.command("tok-k1-04"), w.plan("tok-k1-04"))
        self.assertEqual((third.outcome.value, third.revision_id),
                         ("IDEMPOTENT_REPLAY", again.revision_id))
        self.assertEqual((self.one("SELECT count(*) FROM revisions"),
                          self.one("SELECT count(*) FROM thread_revisions"),
                          self.one("SELECT count(*) FROM booking_commands")), (1, 1, 1))

    def test_p2_k1_05_an_unrelated_thread_on_the_same_date_does_not_inherit_the_wait(self):
        a = _World(self.SF, WA_A); a.issue("tok-k1-05a")
        b = _World(self.SF, WA_B); b.issue("tok-k1-05b")
        started = threading.Event()
        holder = threading.Thread(target=self._hold_k1, args=(a.thread_id, 3.0, started))
        holder.start(); started.wait(5)
        out = {}

        def book(w, tok, at, name):
            t0 = _t.monotonic()
            out[name] = (self.run_cmd(w.command(tok, at=at), w.plan(tok)), _t.monotonic() - t0)
        ta = threading.Thread(target=book, args=(a, "tok-k1-05a", time(9), "A"))
        ta.start(); _t.sleep(0.3)
        book(b, "tok-k1-05b", time(15), "B")
        ta.join(15); holder.join(15)
        self.assertEqual(out["B"][0].outcome.value, "COMMITTED", out["B"][0].reason)
        self.assertLess(out["B"][1], 1.0, f"B waited {out['B'][1]:.3f}s behind A's thread")
        self.assertEqual(out["A"][0].outcome.value, "COMMITTED", out["A"][0].reason)
        self.assertGreater(out["A"][1], 2.0)              # A really waited on its own K1

    def test_p2_k1_06_issue_and_withdraw_race_acceptance_without_deadlock(self):
        import app.models as M
        from app.services.offer_lifecycle import OfferLifecycle
        outcomes, errors = [], []
        for i in range(24):
            self.setUp()
            w = _World(self.SF, WA_A)
            tok = f"tok-k1-06-{i}"; w.issue(tok)
            barrier = threading.Barrier(2)

            def accept():
                barrier.wait()
                outcomes.append(self.run_cmd(w.command(tok), w.plan(tok)))

            def churn():
                db = self.SF()
                try:
                    barrier.wait()
                    if i % 3 == 0:                      # let the acceptance win some races
                        _t.sleep(0.3)
                    st = db.query(M.WhatsAppThreadState).filter_by(thread_id=w.thread_id).one()
                    if i % 2:
                        OfferLifecycle(db).withdraw(st, thread_id=w.thread_id, reason="TEXT_CORRECTION")
                    else:
                        OfferLifecycle(db).issue(st, thread_id=w.thread_id, token=tok + "-new",
                                                 cycle_key=0, candidate_id=w.cand_id,
                                                 tipo_vehiculo="AUTO", zone_group="CABA",
                                                 zone_detail="Palermo")
                    db.commit()
                except Exception as exc:                # noqa: BLE001
                    errors.append(_sqlstate(exc) or type(exc).__name__)
                    db.rollback()
                finally:
                    db.close()
            th = [threading.Thread(target=accept), threading.Thread(target=churn)]
            [x.start() for x in th]; [x.join(20) for x in th]
        reasons = [r.reason for r in outcomes]
        self.assertNotIn("DB_40P01", reasons)
        self.assertNotIn("40P01", errors)
        self.assertEqual(errors, [])
        for r in outcomes:
            self.assertIn((r.outcome.value, r.reason), {
                ("COMMITTED", "COMMITTED"), ("OFFER_REFUSED", "OFFER_WITHDRAWN"),
                ("OFFER_REFUSED", "SUPERSEDED_OFFER")}, r)
        self.assertTrue(any(r.outcome.value == "COMMITTED" for r in outcomes))
        self.assertTrue(any(r.outcome.value == "OFFER_REFUSED" for r in outcomes))

    def test_p2_k1_07_no_external_io_while_the_transaction_is_open(self):
        ba = _ba()
        from app.services.outbound_safety_gate import OutboundSafetyGate
        w = _World(self.SF, WA_A); w.issue("tok-k1-07")
        before = self.snapshot(w)
        reached = {}

        class Leaky(ba.BookingAuthority):
            def _revalidate_availability(inner, plan, target, lock_mode):
                reached["gate"] = True
                OutboundSafetyGate(inner.db).attempt(
                    wa_id=w.wa, thread_id=w.thread_id, text="probe", path_id="BOOKING_FLOW")
        r = self.run_cmd(w.command("tok-k1-07"), w.plan("tok-k1-07"), auth=Leaky,
                         availability=ba.AvailabilityRevalidation())
        self.assertTrue(reached.get("gate"))
        self.assertEqual((r.outcome.value, r.reason), ("INTERNAL_FAILURE", "INTERNAL"))
        self.assertNothingMoved(before, w, "an outbound attempt inside the transaction")
        self.assertEqual(self.one("SELECT count(*) FROM whatsapp_messages"), 0)
        # and a whole booking opens no network connection of its own (pool pre-warmed)
        self.one("SELECT 1")
        opened = []
        real = socket.socket.connect

        def guard(sock, addr):
            opened.append(addr)
            return real(sock, addr)
        with mock.patch.object(socket.socket, "connect", guard):
            ok = self.run_cmd(w.command("tok-k1-07"), w.plan("tok-k1-07"))
        self.assertEqual(ok.outcome.value, "COMMITTED", ok.reason)
        self.assertEqual(opened, [])


# ══════════════════════════════════════════════════════════════════════════════
# 1b. E3 — mixed shapes, real authority, every shape an increasing subsequence of K
# ══════════════════════════════════════════════════════════════════════════════

E3_WORKERS = int(os.environ.get("CONV_P2_E3_WORKERS", "8"))
E3_OPS = int(os.environ.get("CONV_P2_E3_OPS", "60"))


@unittest.skipUnless(PG_URL, "CONV_P2_PG_URL not set — no disposable PostgreSQL; nothing proven")
class P2_11_E3(_Base):
    """Thread, offer and date contention at once. Shapes:
      accept   real authority, K1 K2 K3 K4 K6 K5w K7 K8 K9 (offer acceptance)
      book     real authority, thread-bound CREATE without an offer (K1 K3 K4 ... K9)
      issue    OfferLifecycle.issue (K1 K2), committed
      withdraw OfferLifecycle.withdraw (K1 K2), committed
      turn     an ordinary CE turn: K1 then the lead (K8), held briefly
      operator real authority, threadless CREATE (K3 K4 K6 K5w K9)
      kshape   raw K1 → K2 → K3 → K4, held briefly — the converged Phase 3 writer's prefix
      ack      ledger acknowledgement lease (K9 only)
    `40P01` is a protocol violation under ONE order, so the test requires zero of them."""

    def test_p2_k1_08_e3_mixed_order_produces_no_deadlock(self):
        import app.models as M
        from app.services.offer_lifecycle import OfferLifecycle
        ba = _ba()
        worlds = [_World(self.SF, f"5491100009{i:02d}") for i in range(4)]
        for i, w in enumerate(worlds):
            w.issue(f"e3-init-{i}")
        prof = self.one("SELECT id FROM profesionales WHERE is_launch_default")
        db = self.SF(); op_lead = M.Lead(nombre="Operador", estado="CONSULTA_NUEVA"); db.add(op_lead)
        db.commit(); op_lead_id = op_lead.id; db.close()
        tally = {"40P01": 0, "55P03": 0, "ok": 0, "refused": 0, "other": []}
        lock = threading.Lock()
        days = (DAY, DAY2)
        slots = [time(h, m) for h in range(8, 20) for m in (0, 15, 30, 45)]

        def note(kind, detail=None):
            with lock:
                if kind == "other":
                    tally["other"].append(detail)
                else:
                    tally[kind] += 1

        def classify(r):
            if r.reason in ("DB_40P01",):
                note("40P01")
            elif r.reason in ("LOCK_TIMEOUT", "THREAD_STATE_CONTENDED", "OFFER_CONTENDED"):
                note("55P03")
            elif r.ok:
                note("ok")
            elif r.outcome.value in ("INTERNAL_FAILURE",):
                note("other", r.reason)
            else:
                note("refused")

        def raw(fn):
            s = self.SF()
            try:
                s.execute(text("SET LOCAL lock_timeout = '2s'"))
                fn(s)
                s.commit(); note("ok")
            except Exception as exc:                    # noqa: BLE001
                s.rollback()
                st = _sqlstate(exc) or getattr(exc, "refusal", None)
                if st == "40P01" or st == "OFFER_DEADLOCK":
                    note("40P01")
                elif st in ("55P03", "OFFER_CONTENDED"):
                    note("55P03")
                else:
                    note("other", f"{type(exc).__name__}:{st}")
            finally:
                s.close()

        def op_accept(rnd):
            w = rnd.choice(worlds)
            fp = self.one("SELECT token_fingerprint FROM whatsapp_booking_offers "
                          "WHERE thread_id = :t AND status = 'ISSUED'", t=w.thread_id)
            if fp is None:
                return
            acc = ba.OfferAcceptance(token_fingerprint=fp, thread_id=w.thread_id, cycle_key=0,
                                     candidate_id=w.cand_id, tipo_vehiculo="AUTO",
                                     zone_group="CABA", zone_detail="Palermo")
            cmd = w.command("x", key=ba.flow_idempotency_key_for_fingerprint(fp),
                            day=rnd.choice(days), at=rnd.choice(slots))
            classify(self.run_cmd(cmd, w.plan("x", acceptance=acc), lock_timeout_ms=2000))

        def op_book(rnd):
            w = rnd.choice(worlds)
            cmd = w.command("x", key=f"e3-book-{rnd.random()}", day=rnd.choice(days),
                            at=rnd.choice(slots))
            classify(self.run_cmd(cmd, w.plan("x", accept=False), lock_timeout_ms=2000))

        def op_issue(rnd):
            w = rnd.choice(worlds)

            def fn(s):
                st = s.query(M.WhatsAppThreadState).filter_by(thread_id=w.thread_id).one()
                OfferLifecycle(s).issue(st, thread_id=w.thread_id, token=f"e3-{rnd.random()}",
                                        cycle_key=0, candidate_id=w.cand_id, tipo_vehiculo="AUTO",
                                        zone_group="CABA", zone_detail="Palermo")
            raw(fn)

        def op_withdraw(rnd):
            w = rnd.choice(worlds)

            def fn(s):
                st = s.query(M.WhatsAppThreadState).filter_by(thread_id=w.thread_id).one()
                OfferLifecycle(s).withdraw(st, thread_id=w.thread_id, reason="TEXT_CORRECTION")
            raw(fn)

        def op_turn(rnd):
            w = rnd.choice(worlds)

            def fn(s):
                s.execute(text("SELECT 1 FROM whatsapp_thread_states WHERE thread_id = :t "
                               "FOR NO KEY UPDATE"), {"t": w.thread_id})
                s.execute(text("UPDATE leads SET flag = flag WHERE id = :l"), {"l": w.lead_id})
                _t.sleep(0.01)
            raw(fn)

        def op_operator(rnd):
            cmd = ba.BookingCommand(operation=ba.BookingOperation.CREATE,
                                    source=ba.BookingSource.CRM, lead_id=op_lead_id,
                                    requested_date=rnd.choice(days), requested_time=rnd.choice(slots),
                                    idempotency_key=f"e3-op-{rnd.random()}")
            classify(self.run_cmd(cmd, lock_timeout_ms=2000))

        def op_kshape(rnd):
            w = rnd.choice(worlds)
            d = rnd.choice(days)

            def fn(s):
                s.execute(text("SELECT 1 FROM whatsapp_thread_states WHERE thread_id = :t "
                               "FOR NO KEY UPDATE"), {"t": w.thread_id})
                s.execute(text("SELECT id FROM whatsapp_booking_offers WHERE thread_id = :t "
                               "AND status = 'ISSUED' FOR UPDATE"), {"t": w.thread_id})
                ymd = d.year * 10000 + d.month * 100 + d.day
                s.execute(text("SELECT pg_advisory_xact_lock_shared(-1, :d)"), {"d": ymd})
                s.execute(text("SELECT pg_advisory_xact_lock(:p, :d)"), {"p": prof, "d": ymd})
                _t.sleep(0.005)
            raw(fn)

        def op_ack(rnd):
            key = self.one("SELECT idempotency_key FROM booking_commands ORDER BY random() LIMIT 1")
            if key is None:
                return
            s = Session(self.engine)
            try:
                ba.BookingAuthority(s).claim_acknowledgement(key)
                note("ok")
            except Exception as exc:                    # noqa: BLE001
                note("40P01" if _sqlstate(exc) == "40P01" else "other", str(_sqlstate(exc)))
            finally:
                s.close()

        shapes = [op_accept, op_accept, op_book, op_issue, op_withdraw, op_turn, op_turn,
                  op_operator, op_kshape, op_kshape, op_ack]

        def worker(seed):
            rnd = random.Random(seed)
            for _ in range(E3_OPS):
                rnd.choice(shapes)(rnd)
        th = [threading.Thread(target=worker, args=(1000 + i,)) for i in range(E3_WORKERS)]
        t0 = _t.monotonic()
        [x.start() for x in th]; [x.join(600) for x in th]
        elapsed = _t.monotonic() - t0
        print(f"\nE3 workers={E3_WORKERS} ops={E3_OPS} elapsed={elapsed:.1f}s "
              f"ok={tally['ok']} refused={tally['refused']} 55P03={tally['55P03']} "
              f"40P01={tally['40P01']} other={tally['other'][:5]}")
        self.assertEqual(tally["40P01"], 0, tally)
        self.assertEqual(tally["other"], [], tally)
        self.assertGreater(tally["ok"], E3_WORKERS * E3_OPS // 3)
        # the window was really exercised: bookings committed on both dates and offers accepted
        self.assertGreater(self.one("SELECT count(*) FROM whatsapp_booking_offers "
                                    "WHERE status = 'ACCEPTED'"), 0)
        self.assertEqual(self.one("SELECT count(DISTINCT turno_fecha) FROM revisions"), 2)


# ══════════════════════════════════════════════════════════════════════════════
# 2. the typed offer acceptance: every binding against the LOCKED row
# ══════════════════════════════════════════════════════════════════════════════

@unittest.skipUnless(PG_URL, "CONV_P2_PG_URL not set — no disposable PostgreSQL; nothing proven")
class P2_20_Offer(_Base):

    def refused(self, w, tok, plan, reason, cmd=None):
        before = self.snapshot(w)
        r = self.run_cmd(cmd or w.command(tok), plan)
        self.assertEqual((r.outcome.value, r.reason), ("OFFER_REFUSED", reason))
        self.assertFalse(r.ok)
        self.assertNothingMoved(before, w, f"refusal {reason}")
        return r

    def test_p2_offer_01_a_correct_acceptance_commits_one_of_everything(self):
        w = _World(self.SF, WA_A); oid = w.issue("tok-o1")
        r = self.run_cmd(w.command("tok-o1"), w.plan("tok-o1"))
        self.assertEqual(r.outcome.value, "COMMITTED", r.reason)
        revs = self.q("SELECT id, turno_fecha, turno_hora, estado_revision FROM revisions")
        trs = self.q("SELECT id, offer_id, status, candidate_id FROM thread_revisions")
        self.assertEqual(len(revs), 1); self.assertEqual(len(trs), 1)
        self.assertEqual(trs[0][1:], (oid, "booked", w.cand_id))
        offer = self.q("SELECT status, accepted_revision_id, accepted_thread_revision_id, "
                       "accepted_at IS NOT NULL, terminal_reason FROM whatsapp_booking_offers "
                       "WHERE id = :o", o=oid)[0]
        self.assertEqual(tuple(offer), ("ACCEPTED", revs[0][0], trs[0][0], True,
                                        "CONSUMED_BY_BOOKING"))
        led = self.q("SELECT command_state, revision_id, thread_revision_id, thread_id, "
                     "occupying, ack_status FROM booking_commands")
        self.assertEqual([tuple(x) for x in led],
                         [("COMMITTED", revs[0][0], trs[0][0], w.thread_id, True, "PENDING")])
        st = self.q("SELECT current_revision_id, last_stage, flow_booking_token FROM "
                    "whatsapp_thread_states WHERE thread_id = :t", t=w.thread_id)[0]
        self.assertEqual(tuple(st), (trs[0][0], "BOOKED", None))
        self.assertEqual((r.revision_id, r.thread_revision_id), (revs[0][0], trs[0][0]))

    def test_p2_offer_02_wrong_token_fingerprint(self):
        w = _World(self.SF, WA_A); w.issue("tok-o2")
        # the thread HAS a live offer; the submission names another fingerprint
        self.refused(w, "tok-o2-forged", w.plan("tok-o2-forged"), "UNKNOWN_TOKEN")
        self.assertEqual(self.one("SELECT status FROM whatsapp_booking_offers"), "ISSUED")

    def test_p2_offer_03_wrong_thread(self):
        a = _World(self.SF, WA_A); b = _World(self.SF, WA_B)
        b.issue("tok-o3")                       # the token belongs to B's conversation
        self.refused(a, "tok-o3", a.plan("tok-o3"), "FOREIGN_OFFER")

    def test_p2_offer_04_wrong_cycle(self):
        w = _World(self.SF, WA_A); w.issue("tok-o4")
        self.refused(w, "tok-o4", w.plan("tok-o4", acceptance=w.acceptance("tok-o4", cycle_key=7)),
                     "OFFER_CYCLE_CHANGED")
        # and the caller cannot hide a moved cycle by repeating the offer's value
        with self.engine.begin() as c:
            c.execute(text("UPDATE whatsapp_thread_states SET current_cycle_start_message_db_id "
                           "= 41 WHERE thread_id = :t"), {"t": w.thread_id})
        self.refused(w, "tok-o4", w.plan("tok-o4"), "OFFER_CYCLE_CHANGED")

    def test_p2_offer_05_wrong_candidate(self):
        import app.models as M
        w = _World(self.SF, WA_A); w.issue("tok-o5")
        db = self.SF(); other = M.WhatsAppThreadCandidate(
            thread_id=w.thread_id, marca="Fiat", modelo="Uno", tipo_vehiculo="AUTO",
            status="mentioned", source_text="fixture"); db.add(other); db.commit()
        oc = other.id; db.close()
        self.refused(w, "tok-o5", w.plan("tok-o5", candidate_id=oc), "OFFER_CANDIDATE_CHANGED")
        # a focus pointer that contradicts the offer's binding refuses too
        with self.engine.begin() as c:
            c.execute(text("UPDATE whatsapp_thread_states SET current_focus_candidate_id = :c "
                           "WHERE thread_id = :t"), {"c": oc, "t": w.thread_id})
        self.refused(w, "tok-o5", w.plan("tok-o5"), "OFFER_CANDIDATE_CHANGED")

    def test_p2_offer_06_wrong_category(self):
        w = _World(self.SF, WA_A); w.issue("tok-o6")
        self.refused(w, "tok-o6", w.plan("tok-o6", acceptance=w.acceptance("tok-o6",
                     tipo_vehiculo="PICKUP")), "OFFER_VEHICLE_CATEGORY_CHANGED")
        # the candidate moved AUTO → PICKUP in place; the caller repeats the offer's AUTO
        with self.engine.begin() as c:
            c.execute(text("UPDATE whatsapp_thread_candidates SET tipo_vehiculo = 'PICKUP' "
                           "WHERE id = :c"), {"c": w.cand_id})
        self.refused(w, "tok-o6", w.plan("tok-o6"), "OFFER_VEHICLE_CATEGORY_CHANGED")

    def test_p2_offer_07_wrong_zone_group(self):
        w = _World(self.SF, WA_A); w.issue("tok-o7")
        self.refused(w, "tok-o7", w.plan("tok-o7", acceptance=w.acceptance("tok-o7",
                     zone_group="GBA_NORTE")), "OFFER_LOCATION_CHANGED")
        with self.engine.begin() as c:
            c.execute(text("UPDATE whatsapp_thread_states SET home_zone_group = 'GBA_NORTE' "
                           "WHERE thread_id = :t"), {"t": w.thread_id})
        self.refused(w, "tok-o7", w.plan("tok-o7"), "OFFER_LOCATION_CHANGED")

    def test_p2_offer_08_wrong_zone_detail(self):
        w = _World(self.SF, WA_A); w.issue("tok-o8")
        self.refused(w, "tok-o8", w.plan("tok-o8", acceptance=w.acceptance("tok-o8",
                     zone_detail="Belgrano")), "OFFER_LOCATION_CHANGED")
        with self.engine.begin() as c:
            c.execute(text("UPDATE whatsapp_thread_candidates SET zone_detail = 'Belgrano' "
                           "WHERE id = :c"), {"c": w.cand_id})
        self.refused(w, "tok-o8", w.plan("tok-o8"), "OFFER_LOCATION_CHANGED")

    def test_p2_offer_09_expired(self):
        w = _World(self.SF, WA_A); oid = w.issue("tok-o9")
        with self.engine.begin() as c:
            c.execute(text("UPDATE whatsapp_booking_offers SET expires_at = now() - "
                           "interval '1 minute' WHERE id = :o"), {"o": oid})
        self.refused(w, "tok-o9", w.plan("tok-o9"), "OFFER_EXPIRED")

    def test_p2_offer_10_withdrawn(self):
        import app.models as M
        from app.services.offer_lifecycle import OfferLifecycle
        w = _World(self.SF, WA_A); w.issue("tok-o10")
        db = self.SF(); st = db.query(M.WhatsAppThreadState).filter_by(thread_id=w.thread_id).one()
        OfferLifecycle(db).withdraw(st, thread_id=w.thread_id, reason="VEHICLE_CHANGED")
        db.commit(); db.close()
        self.refused(w, "tok-o10", w.plan("tok-o10"), "OFFER_WITHDRAWN")

    def test_p2_offer_11_already_accepted(self):
        w = _World(self.SF, WA_A); oid = w.issue("tok-o11")
        # accepted by a writer that keeps no ledger row (Track A's in-session claim)
        with self.engine.begin() as c:
            c.execute(text("UPDATE whatsapp_booking_offers SET status = 'ACCEPTED', "
                           "accepted_at = now(), terminal_at = now(), terminal_reason = "
                           "'CONSUMED_BY_BOOKING' WHERE id = :o"), {"o": oid})
        self.refused(w, "tok-o11", w.plan("tok-o11"), "OFFER_ALREADY_ACCEPTED")

    def test_p2_offer_12_missing_offer(self):
        w = _World(self.SF, WA_A)                 # no offer was ever issued on this thread
        self.refused(w, "tok-o12", w.plan("tok-o12"), "UNKNOWN_TOKEN")

    def test_p2_offer_13_concurrent_acceptances_of_one_offer_book_once(self):
        w = _World(self.SF, WA_A); oid = w.issue("tok-o13")
        n = 6
        barrier = threading.Barrier(n)
        results = []

        def go(i):
            barrier.wait()
            results.append(self.run_cmd(w.command("tok-o13"), w.plan("tok-o13")))
        th = [threading.Thread(target=go, args=(i,)) for i in range(n)]
        [x.start() for x in th]; [x.join(30) for x in th]
        kinds = sorted(r.outcome.value for r in results)
        self.assertEqual(kinds.count("COMMITTED"), 1, kinds)
        self.assertEqual(kinds.count("IDEMPOTENT_REPLAY"), n - 1, kinds)
        winner = next(r for r in results if r.outcome.value == "COMMITTED")
        self.assertTrue(all(r.revision_id == winner.revision_id for r in results))
        self.assertEqual((self.one("SELECT count(*) FROM revisions"),
                          self.one("SELECT count(*) FROM thread_revisions WHERE offer_id = :o", o=oid),
                          self.one("SELECT count(*) FROM booking_commands")), (1, 1, 1))
        self.assertEqual(self.one("SELECT status FROM whatsapp_booking_offers WHERE id = :o",
                                  o=oid), "ACCEPTED")
        self.assertEqual(self.one("SELECT replay_count FROM booking_commands"), n - 1)


# ══════════════════════════════════════════════════════════════════════════════
# 3. the partial-unique winner is replayed, never duplicated
# ══════════════════════════════════════════════════════════════════════════════

@unittest.skipUnless(PG_URL, "CONV_P2_PG_URL not set — no disposable PostgreSQL; nothing proven")
class P2_30_ProjectionReplay(_Base):
    """A winner that booked the offer WITHOUT moving its status (a writer outside the
    authority — exactly the stale-projection case R3RV-06 names). The authority's own K2
    claim therefore succeeds, and only `uq_thread_revision_one_booking_per_offer` can stop the
    second booking. The winner holds its INSERT uncommitted until the loser is waiting on the
    index entry, then commits."""

    def _winner(self, w, oid, *, with_ledger=True, ledger_slot=None, rev_time=time(11)):
        """Open a transaction that books `oid` (revision + ThreadRevision + optional ledger) and
        return (connection, transaction, ids) — still uncommitted."""
        c = self.engine.connect(); tx = c.begin()
        rid = c.execute(text(
            "INSERT INTO revisions (lead_id, turno_fecha, turno_hora, estado_revision, "
            "service_minutes, buffer_minutes, booking_version, created_at) VALUES (:l, :d, :h, "
            "'CONFIRMADO', 30, 15, 0, now()) RETURNING id"),
            {"l": w.lead_id, "d": DAY, "h": rev_time}).scalar()
        trid = c.execute(text(
            "INSERT INTO thread_revisions (thread_id, candidate_id, status, offer_id, "
            "scheduled_date, scheduled_time) VALUES (:t, :c, 'booked', :o, :d, :h) RETURNING id"),
            {"t": w.thread_id, "c": w.cand_id, "o": oid, "d": DAY, "h": rev_time}).scalar()
        if with_ledger:
            ss = ledger_slot or datetime.combine(DAY, rev_time)
            c.execute(text(
                "INSERT INTO booking_commands (idempotency_key, source, operation, "
                "request_fingerprint, revision_id, thread_revision_id, lead_id, thread_id, "
                "slot_start, slot_end, occupying, booking_version, ack_status) VALUES "
                "('winner-key', 'CRM', 'CREATE', 'fp', :r, :tr, :l, :t, :s, :e, true, 0, "
                "'NOT_APPLICABLE')"),
                {"r": rid, "tr": trid, "l": w.lead_id, "t": w.thread_id, "s": ss,
                 "e": ss + timedelta(minutes=45)})
        return c, tx, (rid, trid)

    def _race(self, w, tok, winner_kw):
        oid = w.issue(tok)
        c, tx, ids = self._winner(w, oid, **winner_kw)
        out = {}
        t = threading.Thread(target=lambda: out.setdefault(
            "r", self.run_cmd(w.command(tok, at=time(15)), w.plan(tok))))
        t.start()
        # The winner's INSERT holds the FK's KEY SHARE on the offer row, so the loser blocks
        # at K2 (FOR UPDATE) until the winner commits; it then finds the offer still ISSUED
        # (the winner never moved it), claims it, and its own ThreadRevision INSERT meets the
        # committed index entry: `23505 uq_thread_revision_one_booking_per_offer`, every time.
        waited = None
        for _ in range(100):
            _t.sleep(0.05)
            waited = self.one("SELECT query FROM pg_stat_activity WHERE datname = "
                              "current_database() AND wait_event_type = 'Lock' LIMIT 1")
            if waited:
                break
        self.assertTrue(waited, "the loser never waited on the winner — vacuous race")
        tx.commit(); c.close()
        t.join(30)
        return oid, ids, out["r"]

    def test_p2_pr_01_the_loser_replays_the_winner_and_writes_nothing(self):
        w = _World(self.SF, WA_A)
        oid, (rid, trid), r = self._race(w, "tok-pr1", {})
        self.assertEqual((r.outcome.value, r.reason), ("IDEMPOTENT_REPLAY", "PROJECTION_WINNER_REPLAY"))
        self.assertEqual((r.revision_id, r.thread_revision_id, r.idempotency_key),
                         (rid, trid, "winner-key"))
        self.assertEqual((self.one("SELECT count(*) FROM revisions"),
                          self.one("SELECT count(*) FROM thread_revisions"),
                          self.one("SELECT count(*) FROM booking_commands")), (1, 1, 1))
        # the loser's claim rolled back with everything else: the (stale) offer is untouched
        self.assertEqual(self.one("SELECT status FROM whatsapp_booking_offers WHERE id = :o",
                                  o=oid), "ISSUED")
        self.assertIsNone(self.one("SELECT ack_claim_token FROM booking_commands"))

    def test_p2_pr_02_current_booking_truth_comes_from_the_revision(self):
        w = _World(self.SF, WA_A)
        # the winner's ledger still says 09:00 (its committed slot); the booking row says
        # 11:00 (it was moved since); the offer projection names nothing at all
        oid, (rid, trid), r = self._race(w, "tok-pr2", {
            "ledger_slot": datetime.combine(DAY, time(9)), "rev_time": time(11)})
        self.assertEqual(r.outcome.value, "IDEMPOTENT_REPLAY")
        self.assertEqual(r.slot_start, datetime.combine(DAY, time(11)))
        self.assertIsNone(self.one("SELECT accepted_revision_id FROM whatsapp_booking_offers "
                                   "WHERE id = :o", o=oid))
        self.assertTrue(r.occupying)

    def test_p2_pr_03_an_unrelated_unique_violation_is_not_a_replay(self):
        w = _World(self.SF, WA_A); oid = w.issue("tok-pr3")
        with self.engine.begin() as c:            # a disposable, test-only unique index
            c.execute(text("CREATE UNIQUE INDEX p2_test_unrelated ON thread_revisions (buyer_phone)"))
            c.execute(text("INSERT INTO thread_revisions (thread_id, status, buyer_phone) "
                           "VALUES (:t, 'draft', '5491100000000')"), {"t": w.thread_id})
        try:
            before = self.snapshot(w)
            r = self.run_cmd(w.command("tok-pr3"), w.plan("tok-pr3", buyer_phone="5491100000000"))
            self.assertEqual((r.outcome.value, r.reason), ("INTERNAL_FAILURE", "INTEGRITY_23505"))
            self.assertNothingMoved(before, w, "an unrelated unique violation")
            self.assertEqual(self.one("SELECT status FROM whatsapp_booking_offers WHERE id = :o",
                                      o=oid), "ISSUED")
        finally:
            with self.engine.begin() as c:
                c.execute(text("DROP INDEX p2_test_unrelated"))

    def test_p2_pr_04_a_winner_that_cannot_be_proven_fails_closed(self):
        w = _World(self.SF, WA_A)
        oid, (rid, trid), r = self._race(w, "tok-pr4", {"with_ledger": False})
        self.assertEqual((r.outcome.value, r.reason), ("INTERNAL_FAILURE", "PROJECTION_WINNER_UNPROVEN"))
        self.assertFalse(r.ok)
        self.assertEqual((self.one("SELECT count(*) FROM revisions"),
                          self.one("SELECT count(*) FROM booking_commands")), (1, 0))


# ══════════════════════════════════════════════════════════════════════════════
# 4. tester reset: retained, terminally DETACHED, never replayed
# ══════════════════════════════════════════════════════════════════════════════

@unittest.skipUnless(PG_URL, "CONV_P2_PG_URL not set — no disposable PostgreSQL; nothing proven")
class P2_40_ResetLedger(_Base):

    def book(self, w, tok, at=time(10)):
        w.issue(tok)
        r = self.run_cmd(w.command(tok, at=at), w.plan(tok))
        self.assertEqual(r.outcome.value, "COMMITTED", r.reason)
        return r

    def reset(self, wa):
        from app.services.tester_reset import reset_tester_to_zero_state
        db = self.SF()
        try:
            return reset_tester_to_zero_state(db, wa)
        finally:
            db.close()

    def ledger(self, key):
        return self.q("SELECT command_state, detached_reason, detached_at IS NOT NULL, "
                      "revision_id, thread_revision_id, lead_id, thread_id, profesional_id, "
                      "slot_start, slot_end, occupying, detached_history, ack_status "
                      "FROM booking_commands WHERE idempotency_key = :k", k=key)

    def lookup(self, key):
        s = Session(self.engine)
        try:
            return _ba().BookingAuthority(s).lookup(key)
        finally:
            s.close()

    def test_p2_rl_01_committed_then_reset_is_retained_and_terminally_detached(self):
        w = _World(self.SF, WA_A)
        r = self.book(w, "tok-rl1")
        before = self.lookup(r.idempotency_key)
        self.assertEqual((before.outcome.value, before.reason), ("IDEMPOTENT_REPLAY", "ALREADY_COMMITTED"))
        res = self.reset(WA_A)
        self.assertEqual(res.detached_commands, 1)
        rows = self.ledger(r.idempotency_key)
        self.assertEqual(len(rows), 1)                         # retained, never deleted
        st, reason, at, rid, trid, lid, tid, pid, ss, se, occ, hist, ack = rows[0]
        self.assertEqual((st, reason, at), ("DETACHED", "TESTER_RESET", True))
        self.assertEqual((rid, trid, lid, tid, pid, ss, se, occ), (None,) * 7 + (False,))
        h = json.loads(hist)
        self.assertTrue(h["historical"])
        self.assertEqual((h["lead_id"], h["thread_id"], h["revision_id"]),
                         (w.lead_id, w.thread_id, r.revision_id))
        after = self.lookup(r.idempotency_key)
        self.assertEqual((after.outcome.value, after.reason),
                         ("COMMAND_DETACHED", "LEDGER_DETACHED:TESTER_RESET"))
        self.assertFalse(after.ok)
        self.assertEqual((after.revision_id, after.thread_revision_id, after.slot_start,
                          after.occupying), (None, None, None, False))
        # nothing owed, nothing listed: the acknowledgement lease and both operator views
        s = Session(self.engine)
        try:
            auth = _ba().BookingAuthority(s)
            self.assertIsNone(auth.claim_acknowledgement(r.idempotency_key))
            self.assertEqual(auth.ledger_rows_needing_attention(pending_older_than_seconds=0), [])
            self.assertEqual(auth.acknowledgements_needing_attention(), [])
        finally:
            s.close()
        self.assertEqual(self.ledger(r.idempotency_key)[0][12], "PENDING")   # history kept

    def test_p2_rl_02_the_old_key_can_never_book_again(self):
        w = _World(self.SF, WA_A)
        r = self.book(w, "tok-rl2")
        self.reset(WA_A)
        w2 = _World(self.SF, WA_A)                  # the same handset, reseeded
        w2.issue("tok-rl2-new")                      # a live offer exists on the new thread
        cmd = w2.command("tok-rl2")                  # but the submission reuses the OLD key
        again = self.run_cmd(cmd, w2.plan("tok-rl2", accept=False))
        self.assertEqual((again.outcome.value, again.reason),
                         ("COMMAND_DETACHED", "LEDGER_DETACHED:TESTER_RESET"))
        self.assertEqual((self.one("SELECT count(*) FROM revisions"),
                          self.one("SELECT count(*) FROM booking_commands")), (0, 1))
        # replaying the exact original command is just as terminal
        same = self.run_cmd(w.command("tok-rl2"), None)
        self.assertEqual(same.outcome.value, "COMMAND_DETACHED")

    def test_p2_rl_03_a_reseeded_tester_cannot_attach_to_the_historical_command(self):
        w = _World(self.SF, WA_A)
        r = self.book(w, "tok-rl3")
        self.reset(WA_A)
        w2 = _World(self.SF, WA_A)
        r2 = self.book(w2, "tok-rl3-new", at=time(10))       # the SAME slot is free again
        self.assertEqual(r2.outcome.value, "COMMITTED", r2.reason)
        live = self.q("SELECT idempotency_key FROM booking_commands WHERE thread_id = :t OR "
                      "lead_id = :l", t=w2.thread_id, l=w2.lead_id)
        self.assertEqual([x[0] for x in live], [r2.idempotency_key])
        old = self.ledger(r.idempotency_key)[0]
        self.assertEqual(old[:2], ("DETACHED", "TESTER_RESET"))
        self.assertEqual(old[3:11], (None,) * 7 + (False,))
        # no join from any live row reaches it
        self.assertEqual(self.one(
            "SELECT count(*) FROM booking_commands bc JOIN revisions r ON r.id = bc.revision_id "
            "WHERE bc.command_state = 'DETACHED'"), 0)

    def test_p2_rl_04_another_tester_is_unchanged(self):
        a = _World(self.SF, WA_A); b = _World(self.SF, WA_B)
        self.book(a, "tok-rl4a", at=time(9))
        rb = self.book(b, "tok-rl4b", at=time(14))
        cols = ("SELECT * FROM booking_commands WHERE idempotency_key = :k")
        b_before = (self.q(cols, k=rb.idempotency_key), self.q("SELECT * FROM whatsapp_booking_offers WHERE thread_id = :t", t=b.thread_id),
                    self.q("SELECT * FROM revisions WHERE lead_id = :l", l=b.lead_id),
                    self.q("SELECT * FROM thread_revisions WHERE thread_id = :t", t=b.thread_id),
                    self.q("SELECT * FROM whatsapp_thread_states WHERE thread_id = :t", t=b.thread_id))
        self.reset(WA_A)
        b_after = (self.q(cols, k=rb.idempotency_key), self.q("SELECT * FROM whatsapp_booking_offers WHERE thread_id = :t", t=b.thread_id),
                   self.q("SELECT * FROM revisions WHERE lead_id = :l", l=b.lead_id),
                   self.q("SELECT * FROM thread_revisions WHERE thread_id = :t", t=b.thread_id),
                   self.q("SELECT * FROM whatsapp_thread_states WHERE thread_id = :t", t=b.thread_id))
        self.assertEqual(b_before, b_after)
        self.assertEqual(self.lookup(rb.idempotency_key).outcome.value, "IDEMPOTENT_REPLAY")

    def test_p2_rl_05_retrying_the_reset_is_idempotent(self):
        w = _World(self.SF, WA_A)
        r = self.book(w, "tok-rl5")
        self.reset(WA_A)
        first = self.q("SELECT * FROM booking_commands")
        res = self.reset(WA_A)
        self.assertEqual((res.detached_commands, res.deleted), (0, {}))
        self.assertEqual(self.q("SELECT * FROM booking_commands"), first)

    def test_p2_rl_06_a_failed_reset_leaves_the_command_and_booking_intact(self):
        from app.services.tester_reset import reset_tester_to_zero_state
        w = _World(self.SF, WA_A)
        r = self.book(w, "tok-rl6")
        before = self.q("SELECT * FROM booking_commands")
        with self.engine.begin() as c:            # inject a failure AFTER the detach
            c.execute(text("CREATE FUNCTION p2_fail() RETURNS trigger LANGUAGE plpgsql AS "
                           "$$ BEGIN RAISE EXCEPTION 'p2 injected'; END $$"))
            c.execute(text("CREATE TRIGGER p2_fail BEFORE DELETE ON leads FOR EACH ROW "
                           "EXECUTE FUNCTION p2_fail()"))
        db = self.SF()
        try:
            detached_inside = {}
            real = __import__("app.services.tester_reset", fromlist=["x"])._detach_ledger

            def spy(*a, **k):
                n = real(*a, **k)
                detached_inside["n"] = n
                detached_inside["state"] = a[0].execute(text(
                    "SELECT command_state FROM booking_commands")).scalar()
                return n
            with mock.patch("app.services.tester_reset._detach_ledger", spy):
                with self.assertRaises(Exception):
                    reset_tester_to_zero_state(db, WA_A)
            db.rollback()
        finally:
            db.close()
            with self.engine.begin() as c:
                c.execute(text("DROP TRIGGER p2_fail ON leads"))
                c.execute(text("DROP FUNCTION p2_fail()"))
        self.assertEqual(detached_inside, {"n": 1, "state": "DETACHED"})   # it really ran
        self.assertEqual(self.q("SELECT * FROM booking_commands"), before)
        self.assertEqual(self.lookup(r.idempotency_key).outcome.value, "IDEMPOTENT_REPLAY")
        self.assertEqual(self.one("SELECT count(*) FROM revisions WHERE id = :r",
                                  r=r.revision_id), 1)

    def test_p2_rl_07_detached_is_terminal_in_the_database_itself(self):
        w = _World(self.SF, WA_A)
        r = self.book(w, "tok-rl7")
        self.reset(WA_A)
        for sql, why in (
                ("UPDATE booking_commands SET command_state = 'COMMITTED', detached_at = NULL, "
                 "detached_reason = NULL, detached_history = NULL", "reattach"),
                ("UPDATE booking_commands SET lead_id = 1", "re-point a reusable id"),
                ("UPDATE booking_commands SET occupying = true", "re-occupy")):
            with self.assertRaises(Exception) as ctx, self.engine.begin() as c:
                c.execute(text(sql))
            self.assertEqual(_sqlstate(ctx.exception), "23514", why)
        self.assertEqual(self.ledger(r.idempotency_key)[0][0], "DETACHED")

    def test_p2_rl_08_a_threadless_operator_command_of_the_tester_is_detached_too(self):
        ba = _ba()
        w = _World(self.SF, WA_A)
        op = self.run_cmd(ba.BookingCommand(
            operation=ba.BookingOperation.CREATE, source=ba.BookingSource.API,
            idempotency_key="api:p2-rl8", lead_id=w.lead_id, requested_date=DAY,
            requested_time=time(16)))
        self.assertEqual(op.outcome.value, "COMMITTED", op.reason)
        self.reset(WA_A)
        self.assertEqual(self.lookup("api:p2-rl8").outcome.value, "COMMAND_DETACHED")


# ══════════════════════════════════════════════════════════════════════════════
# 5. the migration: one head, reversible, refuses to re-arm a detached command
# ══════════════════════════════════════════════════════════════════════════════

@unittest.skipUnless(PG_MIG_URL, "CONV_P2_PG_MIG_URL not set — nothing proven")
class P2_50_Migration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_MIG_URL)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def alembic(self, verb, target):
        from alembic import command
        from alembic.config import Config
        saved = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = PG_MIG_URL
        cwd = os.getcwd()
        os.chdir(_BACKEND)
        try:
            getattr(command, verb)(Config("alembic.ini"), target)
        finally:
            os.chdir(cwd)
            if saved is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = saved
        self.engine.dispose()

    def catalog(self):
        with self.engine.connect() as c:
            cols = c.execute(text("SELECT column_name, data_type, is_nullable, column_default "
                                  "FROM information_schema.columns WHERE table_name = "
                                  "'booking_commands' ORDER BY column_name")).all()
            cons = c.execute(text("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                                  "WHERE conrelid = 'booking_commands'::regclass ORDER BY 1")).all()
            trg = c.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND "
                                 "tgrelid = 'booking_commands'::regclass")).all()
            fn = c.execute(text("SELECT count(*) FROM pg_proc WHERE proname = "
                                "'booking_commands_detached_is_terminal'")).scalar()
            ver = c.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        return cols, cons, trg, fn, ver

    def test_p2_mg_01_one_head_whose_parent_is_the_phase1_merge(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        cwd = os.getcwd(); os.chdir(_BACKEND)
        try:
            script = ScriptDirectory.from_config(Config("alembic.ini"))
            # Convergence Phase 4 adaptation: still ONE head — the Phase 4 migration — whose
            # parent is the Phase 2 revision, whose parent is the Phase 1 merge.
            # Phase 4-R1 adaptation: the one head is now 4-R1's, whose parent is Phase 4's.
            self.assertEqual(script.get_heads(), [W1F_HEAD])
            self.assertEqual(script.get_revision(W1F_HEAD).down_revision, R1_HEAD)
            self.assertEqual(script.get_revision(R1_HEAD).down_revision, P4_HEAD)
            self.assertEqual(script.get_revision(P4_HEAD).down_revision, HEAD)
            self.assertEqual(script.get_revision(HEAD).down_revision, PARENT)
        finally:
            os.chdir(cwd)

    def test_p2_mg_02_downgrade_and_upgrade_round_trip_byte_identically(self):
        top = self.catalog()
        # Convergence Phase 4 adaptation: the round trip starts at the Phase 4 head and passes
        # through the (empty) booking_attempt_events downgrade on its way to PARENT.
        # Phase 4-R1 adaptation: it now starts at the 4-R1 head (and passes through its
        # null-only, marker-owned column downgrade as well).
        # W1-F2 adaptation: it now starts at W1F_HEAD and passes through its empty-table
        # downgrade first.
        self.assertEqual(top[4], [W1F_HEAD]); self.assertEqual(top[3], 1)
        self.alembic("downgrade", PARENT)
        mid = self.catalog()
        self.assertEqual(mid[4], [PARENT])
        self.assertFalse({"command_state", "detached_at", "detached_reason", "detached_history"}
                         & {r[0] for r in mid[0]})
        self.assertEqual((mid[2], mid[3]), ([], 0))
        # a row written while downgraded is COMMITTED after the upgrade and still replays
        with self.engine.begin() as c:
            c.execute(text("DELETE FROM booking_commands WHERE idempotency_key = 'mg-02'"))
            c.execute(text("INSERT INTO booking_commands (idempotency_key, source, operation, "
                           "request_fingerprint, occupying, ack_status) VALUES ('mg-02', 'API', "
                           "'CREATE', 'fp', false, 'NOT_APPLICABLE')"))
        self.alembic("upgrade", "head")
        self.assertEqual(self.catalog(), top)
        with self.engine.begin() as c:
            self.assertEqual(c.execute(text("SELECT command_state FROM booking_commands WHERE "
                                            "idempotency_key = 'mg-02'")).scalar(), "COMMITTED")
        s = Session(self.engine)
        try:                                       # the authority reads the pre-existing row
            r = _ba().BookingAuthority(s).lookup("mg-02")
            self.assertEqual((r.outcome.value, r.reason, r.identity_outcome),
                             ("IDEMPOTENT_REPLAY", "ALREADY_COMMITTED", None))
        finally:
            s.close()
        with self.engine.begin() as c:
            c.execute(text("DELETE FROM booking_commands WHERE idempotency_key = 'mg-02'"))

    def test_p2_mg_03_a_detached_row_blocks_the_downgrade(self):
        with self.engine.begin() as c:
            c.execute(text("INSERT INTO booking_commands (idempotency_key, source, operation, "
                           "request_fingerprint, occupying, ack_status, command_state, "
                           "detached_at, detached_reason) VALUES ('mg-03', 'API', 'CREATE', 'fp', "
                           "false, 'NOT_APPLICABLE', 'DETACHED', now(), 'TESTER_RESET')"))
        top = self.catalog()
        try:
            with self.assertRaises(RuntimeError) as ctx:
                self.alembic("downgrade", PARENT)
            self.assertIn("DETACHED", str(ctx.exception))
            self.assertEqual(self.catalog(), top)            # nothing was dropped
        finally:
            with self.engine.begin() as c:
                c.execute(text("DELETE FROM booking_commands WHERE idempotency_key = 'mg-03'"))


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
