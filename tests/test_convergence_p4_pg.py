"""Convergence Phase 4 — durable refusal evidence, the K-E flush guard and the operator paths,
RACED AND MEASURED ON REAL POSTGRESQL through production code (CE route, Data Exchange service,
operator API, CRM handlers, the refusal finaliser, `booking_attention`, tester reset).

Every concurrent case captures the wait it is about: a `Sampler` on a third connection sees
an ungranted lock (`pg_locks.granted = false`) while the first party is held at a barrier.

  CONV_P3_PG_URL   the Phase 3 disposable PostgreSQL 16 database at the Alembic head.
  CONV_P4_MIG_URL  (optional) a second disposable database at the head, for the migration
                   round trip; the class skips without it.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import threading
import unittest
from datetime import time
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_convergence_p3_race_pg import (PG_URL, HoldFirst, Sampler, _Race,  # noqa: E402
                                         _measure, _threads)

import _sb2b_harness as H  # noqa: E402

from sqlalchemy import create_engine, select, text, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (BookingCommandRecord, Lead, Revision,  # noqa: E402
                        WhatsAppThreadCandidate, WhatsAppThreadState)
from app.services.booking_authority import (BookingAuthority, BookingCommand,  # noqa: E402
                                            BookingOperation, BookingSource)
from app.services.booking_flow_service import (BookingSlotConflictError,  # noqa: E402
                                               BookingTokenError)
from app.services.offer_lifecycle import OfferLifecycle  # noqa: E402

MIG_URL = os.environ.get("CONV_P4_MIG_URL", "")


class _P4(_Race):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The production schema: `main.py` adds this column at start-up (M18 operator e-mail);
        # no migration declares it. Without it M18's raw SQL aborts the CE transaction and the
        # notification path under test is not the production one.
        with cls.engine.begin() as c:
            c.execute(text("ALTER TABLE thread_revisions "
                           "ADD COLUMN IF NOT EXISTS notification_sent_at TIMESTAMPTZ"))

    def offer(self, w):
        with self.engine.connect() as c:
            r = c.execute(text(
                "SELECT id, status, terminal_reason, last_refusal, refused_count "
                "FROM whatsapp_booking_offers WHERE thread_id=:t ORDER BY id"),
                {"t": w.thread_id}).all()
        return [tuple(x) for x in r]

    def mirror(self, w):
        with self.engine.connect() as c:
            return c.execute(text("SELECT flow_booking_token FROM whatsapp_thread_states "
                                  "WHERE thread_id=:t"), {"t": w.thread_id}).scalar()

    def events(self, **where):
        sql = "SELECT source, stage, outcome, reason FROM booking_attempt_events"
        if where:
            sql += " WHERE " + " AND ".join(f"{k} = :{k}" for k in where)
        with self.engine.connect() as c:
            return [tuple(r) for r in c.execute(text(sql + " ORDER BY id"), where).all()]

    def change_category(self, w, to="Camioneta"):
        with Session(self.engine) as db:
            db.execute(update(WhatsAppThreadCandidate).where(
                WhatsAppThreadCandidate.id == w.candidate_id).values(tipo_vehiculo=to))
            db.commit()

    def dx_quiet(self, w):
        try:
            return H.dx_confirm(self.engine, w)
        except (BookingSlotConflictError, BookingTokenError) as exc:
            return type(exc).__name__


def _booking_holding_k1(case, booking, other):
    """Run `booking` until the authority holds K1, then `other`; release the booking once a
    sampler on a third connection sees `other` WAIT. Returns (booking result, other result,
    sampler)."""
    took, release, res = threading.Event(), threading.Event(), {}
    real = BookingAuthority._lock_thread_state

    def held(auth, *a, **k):
        out = real(auth, *a, **k)
        if threading.current_thread().name == "BOOKING":
            took.set(); release.wait(10)
        return out
    with Sampler(case.engine) as smp, mock.patch.object(BookingAuthority, "_lock_thread_state",
                                                        held), H.Meta():
        b = threading.Thread(target=lambda: res.setdefault("booking", booking()), name="BOOKING")
        b.start()
        case.assertTrue(took.wait(15), "the booking never took K1")
        o = threading.Thread(target=lambda: res.setdefault("other", other()), name="OTHER")
        o.start()
        res["waited"] = smp.seen.wait(8)
        release.set()
        b.join(30); o.join(30)
    return res, smp


# ══ 1–4: the refusal finaliser ═══════════════════════════════════════════════════════════

class P4R_Refusal(_P4):

    def test_p4r_01_ce_and_dx_finalise_one_refusal_identically(self):
        """Two identical worlds, the vehicle category moved in both: CE refuses one, Data
        Exchange the other. The canonical terminal state is the same, field for field."""
        seen = {}
        for transport in ("CE", "DX"):
            w = H.seed(self.engine)
            self.change_category(w)
            with H.Meta() as m:
                if transport == "CE":
                    self.assertEqual(self.ce(w)().action, "booking_refused_handoff")
                else:
                    self.assertEqual(self.dx_quiet(w), "BookingTokenError")
            o = self.offer(w)[0]
            seen[transport] = (o[1:], self.mirror(w))
            self.assertEqual(self.occupying_at(w.day, w.at), 0)
            self.assertNotIn(H.OWNER_TEXT, [t for _w, t in m.sent])
        self.assertEqual(seen["CE"], seen["DX"], seen)
        self.assertEqual(seen["CE"], (("WITHDRAWN", "OFFER_VEHICLE_CATEGORY_CHANGED",
                                       "OFFER_VEHICLE_CATEGORY_CHANGED", 1), None))

    def test_p4r_02_concurrent_ce_and_dx_refusals_terminalise_once(self):
        w = H.seed(self.engine)
        self.change_category(w)
        first, hold = threading.Lock(), threading.Event()
        real = OfferLifecycle._claim_checks

        def held(ol, *a, **k):
            out = real(ol, *a, **k)
            if first.acquire(blocking=False):          # the first finaliser keeps K1 + K2
                hold.wait(8)
            return out
        with Sampler(self.engine) as smp, mock.patch.object(OfferLifecycle, "_claim_checks",
                                                            held), H.Meta():
            def release_when_seen():
                smp.seen.wait(8); hold.set()
            threading.Thread(target=release_when_seen, daemon=True).start()
            out = _threads(self.ce(w), lambda: self.dx_quiet(w))
        self.assertTrue(smp.seen.is_set(), "the second finaliser never waited")
        o = self.offer(w)[0]
        fin = [e for e in self.events(stage="FINALISE") if e[2] == "REFUSED"]
        self.assertEqual(o[1:3], ("WITHDRAWN", "OFFER_VEHICLE_CATEGORY_CHANGED"))
        self.assertEqual(o[4], len(fin), "one counter step per finalised attempt")
        self.assertLessEqual(len(fin), 2)
        self.assertIsNone(self.mirror(w))
        self.assertEqual(self.occupying_at(w.day, w.at), 0)
        _measure(test="p4r_02", waited_on=sorted(smp.kinds), outcomes=[str(x)[:30] for x in out])

    def _finaliser_vs(self, w, other, *, finaliser_first):
        """Race the production finaliser for `w`'s token against `other`, the first holding
        K1 until the sampler sees the second wait."""
        from app.services.booking_refusal import finalise_offer_refusal
        hold, entered = threading.Event(), threading.Event()
        real = OfferLifecycle._claim_checks

        def held(ol, *a, **k):
            out = real(ol, *a, **k)
            if threading.current_thread().name == "FIN" and finaliser_first:
                entered.set(); hold.wait(8)
            return out

        def finalise():
            with Session(self.engine) as db:
                return finalise_offer_refusal(
                    db, source="WHATSAPP_FLOW", thread_id=w.thread_id, token=w.token,
                    observed_reason="OFFER_VEHICLE_CATEGORY_CHANGED", attempt_ref="race",
                    candidate_id=w.candidate_id, tipo_vehiculo="Camioneta",
                    zone_group="Norte", zone_detail="Benavidez")
        res = {}
        with Sampler(self.engine) as smp, mock.patch.object(OfferLifecycle, "_claim_checks",
                                                            held):
            f = threading.Thread(target=lambda: res.setdefault("fin", finalise()), name="FIN")
            o = threading.Thread(target=lambda: res.setdefault("other", other()), name="OTHER")
            if finaliser_first:
                f.start(); self.assertTrue(entered.wait(8))
                o.start(); self.assertTrue(smp.seen.wait(8), "the other party never waited")
                hold.set()
            else:
                o.start(); o.join(20); f.start()
            f.join(20); o.join(20)
        return res, smp

    def _replacement(self, w):
        from app.services.booking_flow_service import make_booking_token
        tok = make_booking_token(w.thread_id) + "-replacement"

        def issue():
            with Session(self.engine) as db:
                st = db.execute(select(WhatsAppThreadState).where(
                    WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
                OfferLifecycle(db).issue(st, thread_id=w.thread_id, token=tok, cycle_key=0,
                                         candidate_id=w.candidate_id, tipo_vehiculo="Camioneta",
                                         zone_group="Norte", zone_detail="Benavidez")
                db.commit()
        return issue

    def test_p4r_03_a_refusal_never_terminalises_the_replacement_offer(self):
        for finaliser_first in (True, False):
            with self.subTest(finaliser_first=finaliser_first):
                self.setUp()
                w = H.seed(self.engine)
                self.change_category(w)
                res, smp = self._finaliser_vs(w, self._replacement(w),
                                              finaliser_first=finaliser_first)
                offers = self.offer(w)
                self.assertEqual(len(offers), 2)
                old, new = offers
                self.assertEqual(new[1], "ISSUED", "the refusal terminalised the replacement")
                self.assertIsNone(new[3], "the replacement carries a refusal it never had")
                if finaliser_first:
                    self.assertEqual(old[1:4], ("WITHDRAWN", "OFFER_VEHICLE_CATEGORY_CHANGED",
                                                "OFFER_VEHICLE_CATEGORY_CHANGED"))
                    _measure(test="p4r_03", waited_on=sorted(smp.kinds))
                else:
                    self.assertEqual((old[1], old[3]), ("SUPERSEDED", "SUPERSEDED_OFFER"))

    def test_p4r_04_a_refusal_racing_a_valid_acceptance_never_splits_state(self):
        """CE accepts the offer (held after K1) while a refusal of the same token is finalised.
        Whoever wins, exactly one consistent story: one booking with an ACCEPTED offer and its
        projection, or (had the refusal terminalised it) no booking — never a mix."""
        w = H.seed(self.engine)
        from app.services.booking_refusal import finalise_offer_refusal

        def finalise():
            with Session(self.engine) as db:
                return finalise_offer_refusal(
                    db, source="WHATSAPP_FLOW", thread_id=w.thread_id, token=w.token,
                    observed_reason="NO_CANONICAL_LOCATION", attempt_ref="race-accept")
        res, smp = _booking_holding_k1(self, self.ce(w), finalise)
        ce, fin = res["booking"], res["other"]
        self.assertTrue(res["waited"], "the finaliser never waited on the booking's K1")
        self.assertEqual(ce.action, "booking_created")
        o = self.offer(w)[0]
        with self.engine.connect() as c:
            proj = c.execute(text("SELECT accepted_thread_revision_id FROM "
                                  "whatsapp_booking_offers WHERE id=:o"), {"o": o[0]}).scalar()
            trs = c.execute(text("SELECT id FROM thread_revisions WHERE offer_id=:o"),
                            {"o": o[0]}).scalars().all()
        self.assertEqual((o[1], self.occupying_at(w.day, w.at), trs), ("ACCEPTED", 1, [proj]))
        self.assertNotEqual(o[2], "OFFER_VEHICLE_CATEGORY_CHANGED")
        self.assertNotIn("40P01", repr(fin))
        _measure(test="p4r_04", waited_on=sorted(smp.kinds), finaliser=fin.reason)

    def test_p4r_05_a_fresh_offer_books_exactly_once_after_a_terminal_refusal(self):
        w = H.seed(self.engine)
        self.change_category(w)
        with H.Meta():
            self.assertEqual(self.ce(w)().action, "booking_refused_handoff")
        self.assertEqual(self.offer(w)[0][1], "WITHDRAWN")
        from app.services.booking_flow_service import make_booking_token
        tok = make_booking_token(w.thread_id) + "-fresh"
        with Session(self.engine) as db:
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
            OfferLifecycle(db).issue(st, thread_id=w.thread_id, token=tok, cycle_key=0,
                                     candidate_id=w.candidate_id, tipo_vehiculo="Camioneta",
                                     zone_group="Norte", zone_detail="Benavidez")
            st.flow_booking_token = tok
            st.needs_human = False
            st.last_stage = "SCHEDULING"
            db.commit()
        w2 = H.World(w.thread_id, w.lead_id, w.contact_id, w.wa_id, tok, w.wamid + ".fresh",
                     w.candidate_id, w.day, w.at)
        with H.Meta():
            self.assertEqual(self.ce(w2, wamid=w2.wamid)().action, "booking_created")
            self.assertEqual(self.dx_quiet(w2)["screen"], "SUCCESS")       # replay, not a 2nd
        self.assertEqual(self.occupying_at(w.day, w.at), 1)
        # the stale token cannot revive the terminal offer
        with H.Meta():
            self.assertEqual(self.dx_quiet(w), "BookingTokenError")
        self.assertEqual(self.occupying_at(w.day, w.at), 1)


# ══ 5: E4 — operator lead+state flush versus a booking holding K1 ═════════════════════════

class P4R_FlushOrder(_P4):

    def test_p4r_06_e4_operator_lead_flush_waits_for_the_booking_never_deadlocks(self):
        from app.services.lead_lifecycle import set_lead_estado
        w = H.seed(self.engine)
        with Session(self.engine) as db:
            db.get(Lead, w.lead_id).estado = "AGENDADO"            # so CONSULTA_NUEVA re-enters
            db.commit()
        sqlstates = []

        def operator():
            try:
                with Session(self.engine) as db:
                    set_lead_estado(db, db.get(Lead, w.lead_id), "CONSULTA_NUEVA")
                    db.commit()                     # one flush: the lead AND its thread states
                return "COMMITTED"
            except Exception as exc:                # noqa: BLE001
                sqlstates.append(getattr(getattr(exc, "orig", None), "sqlstate", None))
                return repr(exc)[:80]
        res, smp = _booking_holding_k1(self, self.ce(w), operator)
        ce, op = res["booking"], res["other"]
        self.assertTrue(res["waited"], "the operator never waited on the booking's K1")
        self.assertNotIn("40P01", sqlstates, "E4: the operator flush deadlocked the booking")
        self.assertEqual((ce.action, op), ("booking_created", "COMMITTED"))
        self.assertEqual(self.occupying_at(w.day, w.at), 1)
        _measure(test="p4r_06_e4", waited_on=sorted(smp.kinds))


# ══ 6–7: operator commands ═══════════════════════════════════════════════════════════════

class P4R_Operator(_P4):

    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api import revision_items, revisions as api
        from app.db import get_db

        def _db():
            s = Session(self.engine)
            try:
                yield s
            finally:
                s.close()
        app = FastAPI(); app.include_router(api.router); app.include_router(revision_items.router)
        for dep in {get_db, api.get_db, revision_items.get_db}:
            app.dependency_overrides[dep] = _db
        return TestClient(app, raise_server_exceptions=False)

    def new_lead(self):
        with Session(self.engine) as db:
            H.ensure_professional(db)
            lead = Lead(estado="CONSULTA_NUEVA", nombre="Operador")
            db.add(lead); db.commit()
            return lead.id

    def test_p4r_07_two_concurrent_retries_with_one_key_are_one_booking(self):
        lead = self.new_lead()
        w = H.seed(self.engine)
        body = {"turno_fecha": w.day.isoformat(), "turno_hora": "15:00:00"}
        c = self.client()
        out = _threads(*[lambda: c.post(f"/leads/{lead}/revisions", json=body,
                                        headers={"Idempotency-Key": "op-retry-1"})
                         for _ in range(3)])
        codes = sorted(r.status_code for r in out)
        ids = {r.json()["id"] for r in out if r.status_code == 200}
        self.assertEqual(len(ids), 1, f"one key produced {ids} ({codes})")
        self.assertTrue(all(code in (200, 409, 503) for code in codes), codes)
        other = c.post(f"/leads/{lead}/revisions",
                       json={"turno_fecha": w.day.isoformat(), "turno_hora": "16:30:00"},
                       headers={"Idempotency-Key": "op-retry-2"})
        self.assertEqual(other.status_code, 200, other.text)
        self.assertNotIn(other.json()["id"], ids)
        with self.engine.connect() as cx:
            n = cx.execute(text("SELECT count(*) FROM booking_commands WHERE "
                                "idempotency_key LIKE 'api:%'")).scalar()
        self.assertEqual(n, 2)

    def test_p4r_08_overlapping_operator_edits_are_typed_never_500(self):
        lead_a, lead_b = self.new_lead(), self.new_lead()
        w = H.seed(self.engine)
        c = self.client()
        a = c.post(f"/leads/{lead_a}/revisions",
                   json={"turno_fecha": w.day.isoformat(), "turno_hora": "15:00:00"},
                   headers={"Idempotency-Key": "op-a"})
        self.assertEqual(a.status_code, 200, a.text)
        b = c.post(f"/leads/{lead_b}/revisions",
                   json={"turno_fecha": w.day.isoformat(), "turno_hora": "15:15:00"},
                   headers={"Idempotency-Key": "op-b"})
        self.assertEqual((b.status_code, b.json()["detail"]["code"]), (409, "SLOT_CONFLICT"))
        cd05 = c.patch(f"/revisions/{a.json()['id']}", json={"turno_hora": "15:15:00"})
        self.assertEqual((cd05.status_code, cd05.json()["detail"]["code"]),
                         (409, "BOOKING_CHANGE_REQUIRES_AUTHORITY"))
        # Flow conflict: the DX slot an operator now owns is a typed refreshed screen
        w2 = H.seed(self.engine, day=w.day, at=time(15, 0))
        with H.Meta():
            self.assertEqual(self.dx_quiet(w2), "BookingSlotConflictError")
        self.assertIn(("API", "AUTHORITY", "REFUSED", "OVERLAP"), self.events(source="API"))


# ══ 8–10: reset, notification, external I/O ═════════════════════════════════════════════

class P4R_Evidence(_P4):

    def test_p4r_09_events_are_fk_free_append_only_and_survive_a_tester_reset(self):
        from app.services.tester_reset import reset_tester_to_zero_state
        w = H.seed(self.engine)
        self.change_category(w)
        with H.Meta():
            self.ce(w)()
        before = self.events()
        self.assertTrue(before, "the refusal left no evidence")
        with self.engine.connect() as c:
            fks = c.execute(text("SELECT count(*) FROM pg_constraint WHERE contype='f' AND "
                                 "conrelid='booking_attempt_events'::regclass")).scalar()
        self.assertEqual(fks, 0)
        for stmt in ("UPDATE booking_attempt_events SET reason='X'",
                     "DELETE FROM booking_attempt_events"):
            with self.subTest(stmt=stmt), self.engine.connect() as c:
                with self.assertRaises(Exception) as e:
                    c.execute(text(stmt)); c.commit()
                self.assertIn("append-only", str(e.exception))
        w2 = H.seed(self.engine)                            # a booked command to detach
        with H.Meta():
            self.assertEqual(self.ce(w2)().action, "booking_created")
        with Session(self.engine) as db:
            reset_tester_to_zero_state(db, w2.wa_id)
            reset_tester_to_zero_state(db, w.wa_id)
        self.assertEqual(self.events()[:len(before)], before, "the reset touched the evidence")
        with self.engine.connect() as c:
            states = c.execute(text("SELECT command_state FROM booking_commands")).scalars().all()
        self.assertIn("DETACHED", states)

    def test_p4r_10_a_failed_operator_email_leaves_one_booking_and_visible_truth(self):
        import dataclasses
        from app.routes.ops_dashboard import booking_attention
        w = H.seed(self.engine)
        cfg = dataclasses.replace(H.settings(), resend_api_key="probe-not-a-credential",
                                  internal_booking_email_from="from@example.invalid",
                                  internal_booking_email_to="to@example.invalid")
        with H.Meta(), mock.patch("urllib.request.urlopen",
                                  side_effect=OSError("smtp gateway unreachable")):
            out, _eng = H.run_turn(self.engine, H.flow_event(w), cfg=cfg)
        self.assertEqual(out.action, "booking_created")
        self.assertEqual(self.occupying_at(w.day, w.at), 1)
        self.assertIn(("CONVERSATION_ENGINE", "NOTIFICATION", "FAILED", "NOTIFICATION_SEND_FAILED"),
                      self.events())
        with Session(self.engine) as db:
            rows = [r for r in booking_attention(db) if r["thread_id"] == w.thread_id]
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["booking"], r["notification_state"], r["notification_next_action"]),
                         ("COMMITTED", "FAILED", "NOTIFY_TEAM_MANUALLY"))
        self.assertIn("OPERATOR_NOTIFICATION_FAILED", r["reasons"])
        self.assertEqual(r["ack_state"], "SENT_CONFIRMED", "operator e-mail ≠ customer ack")

    def test_p4r_11_no_external_io_while_any_booking_lock_is_held(self):
        import dataclasses
        import io
        import json as _json
        w = H.seed(self.engine)
        cfg = dataclasses.replace(H.settings(), resend_api_key="probe-not-a-credential",
                                  internal_booking_email_from="from@example.invalid",
                                  internal_booking_email_to="to@example.invalid")
        held_at_io = []

        def snapshot(channel):
            with self.engine.connect() as c:
                c.execute(text("SET application_name = 'p3-keep'"))
                held_at_io.append((channel, [tuple(r) for r in c.execute(text(
                    "SELECT l.locktype, coalesce(cl.relname, ''), l.mode FROM pg_locks l "
                    "LEFT JOIN pg_class cl ON cl.oid = l.relation JOIN pg_stat_activity a "
                    "ON a.pid = l.pid WHERE a.datname = current_database() AND l.pid <> "
                    "pg_backend_pid() AND (l.locktype = 'advisory' OR (cl.relname IN "
                    "('revisions','thread_revisions','booking_commands','leads',"
                    "'whatsapp_booking_offers','whatsapp_thread_states','profesionales') "
                    "AND l.mode NOT IN ('AccessShareLock')))")).all()]))

        class _Resp(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            snapshot("resend" if "resend" in getattr(req, "full_url", "") else "other")
            return _Resp(_json.dumps({"id": "re_probe"}).encode())

        meta = H.Meta()
        real_send = meta._send

        def send_and_snapshot(**kw):
            snapshot("meta")
            return real_send(**kw)
        meta._send = send_and_snapshot
        with meta, mock.patch("urllib.request.urlopen", fake_urlopen):
            out, _eng = H.run_turn(self.engine, H.flow_event(w), cfg=cfg)
        self.assertEqual(out.action, "booking_created")
        self.assertTrue({c for c, _ in held_at_io} >= {"meta", "resend"}, held_at_io)
        for channel, locks in held_at_io:
            self.assertEqual(locks, [], f"a booking lock was held at the {channel} send: {locks}")
        _measure(test="p4r_11", io_points=[c for c, _ in held_at_io])


# ══ the migration round trip ═════════════════════════════════════════════════════════════

@unittest.skipUnless(MIG_URL, "CONV_P4_MIG_URL not set — no migration database; nothing proven")
class P4R_Migration(unittest.TestCase):

    def alembic(self, *args):
        env = {**os.environ, "DATABASE_URL": MIG_URL}
        return subprocess.run([sys.executable, "-m", "alembic", *args], env=env,
                              cwd=str(pathlib.Path(__file__).resolve().parents[1] / "backend"),
                              capture_output=True, text=True, timeout=300)

    def test_p4r_12_upgrade_downgrade_refusal_and_reupgrade(self):
        eng = create_engine(MIG_URL)
        try:
            heads = self.alembic("heads")
            self.assertEqual(heads.returncode, 0, heads.stderr)
            self.assertEqual([h for h in heads.stdout.split("\n") if h.strip()],
                             ["20261015_booking_attempt_events (head)"])
            def columns():
                with eng.connect() as c:
                    return c.execute(text(
                        "SELECT table_name, column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema = current_schema() AND table_name <> "
                        "'booking_attempt_events' ORDER BY 1, 2")).all()
            at_head = columns()
            with eng.begin() as c:
                c.execute(text("INSERT INTO booking_attempt_events (event_uid, source, stage, "
                               "outcome, reason) VALUES ('mig-probe','X','AUTHORITY','REFUSED',"
                               "'R')"))
            down = self.alembic("downgrade", "-1")
            self.assertNotEqual(down.returncode, 0, "a downgrade dropped evidence rows")
            self.assertIn("audit-data", down.stderr + down.stdout)
            with eng.begin() as c:
                c.execute(text("TRUNCATE booking_attempt_events"))
            down = self.alembic("downgrade", "-1")
            self.assertEqual(down.returncode, 0, down.stderr[-800:])
            with eng.connect() as c:
                self.assertIsNone(c.execute(text(
                    "SELECT to_regclass('booking_attempt_events')")).scalar())
                self.assertEqual(c.execute(text("SELECT version_num FROM alembic_version"))
                                 .scalar(), "20261014_ledger_detached_state")
            self.assertEqual(columns(), at_head, "the downgrade changed another table")
            up = self.alembic("upgrade", "head")
            self.assertEqual(up.returncode, 0, up.stderr[-800:])
            with eng.connect() as c:
                self.assertEqual(c.execute(text(
                    "SELECT count(*) FROM pg_trigger WHERE tgname = "
                    "'trg_booking_attempt_events_append_only'")).scalar(), 1)
            self.assertEqual(columns(), at_head, "the round trip changed another table")
        finally:
            eng.dispose()


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
