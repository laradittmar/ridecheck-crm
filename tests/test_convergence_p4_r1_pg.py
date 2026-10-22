"""Convergence Phase 4-R1 — operator delete and attempt identity, PROVEN ON REAL POSTGRESQL
through production boundaries (the Kanban delete handler, the encrypted Data Exchange ROUTE,
the CE route, the refusal finaliser, the attempt-event writer, Alembic itself).

  CONV_P3_PG_URL           a disposable PostgreSQL 16 database at the Alembic head (the
                           behavioural classes truncate it per test).
  CONV_P4R1_PARENT_URL     disposable, at the Phase 4 PARENT schema (20261014_ledger_detached_state)
  CONV_P4R1_APPFIRST_URL   disposable, the same parent schema (the app's create_all runs first)
  CONV_P4R1_LEGACY_URL     disposable, the same parent schema (an old start-up added the column)
  CONV_P4R1_EMPTY_URL      disposable and EMPTY (the inherited base-chain limitation)

The schema classes run `alembic` as a subprocess against those databases; each skips without
its URL. No fixture here creates a column, trigger or table the migrations are the subject of —
except CONV_P4R1_LEGACY_URL's, which reproduces the OLD start-up DDL on purpose, because the
old start-up is the legacy state under test.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import timedelta
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_convergence_p3_race_pg import PG_URL, Crash, _Race, _measure  # noqa: E402

import _sb2b_harness as H  # noqa: E402

from sqlalchemy import create_engine, text, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import Revision, WhatsAppThreadCandidate  # noqa: E402
from app.services.conversation_engine import ConversationEngine  # noqa: E402

_BACKEND = pathlib.Path(__file__).resolve().parents[1] / "backend"
PARENT_URL = os.environ.get("CONV_P4R1_PARENT_URL", "")
APPFIRST_URL = os.environ.get("CONV_P4R1_APPFIRST_URL", "")
LEGACY_URL = os.environ.get("CONV_P4R1_LEGACY_URL", "")
EMPTY_URL = os.environ.get("CONV_P4R1_EMPTY_URL", "")
P4_PARENT = "20261014_ledger_detached_state"
P4_REV = "20261015_booking_attempt_events"
R1_HEAD = "20261016_notification_sent_at"
#: W1-F2 adaptation: the Alembic head is now the additive 20261022_operator_notifications, a
#: child of R1_HEAD. Assertions that a database sits AT THE HEAD (after `upgrade head`, or
#: unchanged after a refused downgrade) name it; R1_HEAD stays the 4-R1 revision under test.
ALEMBIC_HEAD = "20261022_operator_notifications"


def _rows(engine, sql, **p):
    with engine.connect() as c:
        return [r[0] for r in c.execute(text(sql), p).all()]


class _R1(_Race):

    # ── canonical facts, as full row images (the zero-delta proofs compare these) ──────

    def image(self, w):
        e = self.engine
        return {
            "revisions": _rows(e, "SELECT r::text FROM revisions r WHERE lead_id = :l ORDER BY id",
                               l=w.lead_id),
            "thread_revisions": _rows(e, "SELECT t::text FROM thread_revisions t "
                                         "WHERE thread_id = :t ORDER BY id", t=w.thread_id),
            "offers": _rows(e, "SELECT o::text FROM whatsapp_booking_offers o "
                               "WHERE thread_id = :t ORDER BY id", t=w.thread_id),
            "ledger": _rows(e, "SELECT b::text FROM booking_commands b ORDER BY id"),
            "events": _rows(e, "SELECT x::text FROM booking_attempt_events x ORDER BY id"),
            "state": _rows(e, "SELECT s::text FROM whatsapp_thread_states s "
                              "WHERE thread_id = :t", t=w.thread_id),
            "lead": _rows(e, "SELECT l::text FROM leads l WHERE id = :l", l=w.lead_id),
            "agenda": self.occupying_at(w.day, w.at),
        }

    def offer(self, w):
        with self.engine.connect() as c:
            return [tuple(r) for r in c.execute(text(
                "SELECT status, terminal_reason, last_refusal, refused_count "
                "FROM whatsapp_booking_offers WHERE thread_id = :t ORDER BY id"),
                {"t": w.thread_id}).all()]

    def events(self, w=None, **where):
        sql = ("SELECT source, stage, outcome, reason, event_uid, attempt_ref_hash "
               "FROM booking_attempt_events")
        conds, p = [], {}
        if w is not None:
            conds.append("hist_thread_id = :tid"); p["tid"] = w.thread_id
        for k, v in where.items():
            conds.append(f"{k} = :{k}"); p[k] = v
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        with self.engine.connect() as c:
            return [tuple(r) for r in c.execute(text(sql + " ORDER BY id"), p).all()]

    def mirror(self, w):
        return _rows(self.engine, "SELECT coalesce(flow_booking_token, '') FROM "
                                  "whatsapp_thread_states WHERE thread_id = :t", t=w.thread_id)[0]

    def change_category(self, w, to="Camioneta"):
        with Session(self.engine) as db:
            db.execute(update(WhatsAppThreadCandidate).where(
                WhatsAppThreadCandidate.id == w.candidate_id).values(tipo_vehiculo=to))
            db.commit()

    def booked_customer(self):
        """A REAL customer booking through the CE route: occupying revision, booked
        ThreadRevision, ACCEPTED offer pointing at both, COMMITTED ledger row."""
        w = H.seed(self.engine)
        with H.Meta():
            out = self.ce(w)()
        self.assertEqual(out.action, "booking_created", out)
        img = self.image(w)
        self.assertEqual(img["agenda"], 1)
        with self.engine.connect() as c:
            row = c.execute(text(
                "SELECT o.status, o.accepted_revision_id IS NOT NULL, "
                "o.accepted_thread_revision_id IS NOT NULL FROM whatsapp_booking_offers o "
                "WHERE o.thread_id = :t"), {"t": w.thread_id}).one()
            self.assertEqual(tuple(row), ("ACCEPTED", True, True))
            self.assertEqual(c.execute(text(
                "SELECT count(*) FROM booking_commands WHERE thread_id = :t AND "
                "command_state = 'COMMITTED' AND occupying"), {"t": w.thread_id}).scalar(), 1)
        rev_id = _rows(self.engine, "SELECT id FROM revisions WHERE lead_id = :l",
                       l=w.lead_id)[0]
        return w, rev_id


# ══ 3.1 the customer-booking deletion matrix (P4RV-01) ═════════════════════════════════════

@unittest.skipUnless(PG_URL, "CONV_P3_PG_URL not set — no disposable PostgreSQL; nothing proven")
class P4R1_KanbanDelete(_R1):

    def pending(self, **payload):
        from app.ui import kanban_actions as ka
        item = ka._pending_store(payload)
        item["deadline"] = ka._utcnow() - timedelta(seconds=1)
        return item["token"]

    def commit_delete(self, token):
        from app.ui import kanban_actions as ka
        db = Session(self.engine)
        try:
            resp = ka.ui_commit_delete(token=token, db=db)
            return resp.status_code, json.loads(resp.body)
        finally:
            db.close()

    def assert_refused(self, got, reason, revision_ids):
        status, body = got
        self.assertEqual((status, body["code"], body["reason"], body["committed"], body["ok"]),
                         (409, "DELETE_REFUSED", reason, False, False), body)
        self.assertEqual(body["revision_ids"], revision_ids)
        self.assertTrue(body["message"])

    def no_cancel(self):
        from app.services.booking_authority import BookingAuthority
        return mock.patch.object(BookingAuthority, "execute",
                                 side_effect=AssertionError("a delete must never cancel"))

    def test_p4r1_01_revision_delete_of_a_customer_booking_is_refused_unchanged(self):
        w, rev = self.booked_customer()
        before = self.image(w)
        token = self.pending(type="revision", lead_id=w.lead_id, revision_id=rev)
        with self.no_cancel():
            got = self.commit_delete(token)
        self.assert_refused(got, "BOOKING_MUST_BE_CANCELLED_FIRST", [rev])
        self.assertEqual(self.image(w), before, "a refused delete changed booking state")

    def test_p4r1_02_lead_delete_and_its_retry_are_refused_unchanged(self):
        w, rev = self.booked_customer()
        before = self.image(w)
        token = self.pending(type="lead", lead_id=w.lead_id)
        with self.no_cancel():
            first = self.commit_delete(token)
            again = self.commit_delete(token)            # §3.1 case 3: the same request again
        for got in (first, again):
            self.assert_refused(got, "BOOKING_MUST_BE_CANCELLED_FIRST", [rev])
        self.assertEqual(self.image(w), before)
        from app.ui import kanban_actions as ka
        self.assertIn(token, ka._PENDING_DELETES, "the refused delete consumed its token")

    def test_p4r1_03_a_mixed_lead_target_set_is_refused_whole(self):
        w, rev = self.booked_customer()
        with Session(self.engine) as db:
            loose = Revision(lead_id=w.lead_id)            # deletable on its own
            db.add(loose); db.commit()
            loose_id = loose.id
        before = self.image(w)
        token = self.pending(type="lead", lead_id=w.lead_id)
        with self.no_cancel():
            got = self.commit_delete(token)
        self.assert_refused(got, "BOOKING_MUST_BE_CANCELLED_FIRST", [rev])
        self.assertEqual(self.image(w), before, "partial lead delete")
        self.assertIn(f"({loose_id},", " ".join(self.image(w)["revisions"]))

    def test_p4r1_04_non_occupying_unreferenced_revisions_delete_as_before(self):
        w, rev = self.booked_customer()
        with Session(self.engine) as db:
            loose = Revision(lead_id=w.lead_id)
            db.add(loose); db.commit()
            loose_id = loose.id
        booking_before = {k: v for k, v in self.image(w).items() if k != "revisions"}
        got = self.commit_delete(self.pending(type="revision", lead_id=w.lead_id,
                                              revision_id=loose_id))
        self.assertEqual(got, (200, {"ok": True, "committed": True}))
        after = self.image(w)
        self.assertNotIn(f"({loose_id},", " ".join(after["revisions"]))
        self.assertEqual({k: v for k, v in after.items() if k != "revisions"}, booking_before)
        # a lead with only a non-occupying, unreferenced revision: the lead-level delete too
        w2 = H.seed(self.engine, with_token=False)
        with Session(self.engine) as db:
            db.add(Revision(lead_id=w2.lead_id)); db.commit()
        got = self.commit_delete(self.pending(type="lead", lead_id=w2.lead_id))
        self.assertEqual(got, (200, {"ok": True, "committed": True}))
        self.assertEqual(_rows(self.engine, "SELECT count(*) FROM leads WHERE id = :l",
                               l=w2.lead_id), [0])

    def test_p4r1_05_after_a_canonical_cancel_the_accepted_offer_still_protects_history(self):
        """The booking is cancelled through the authority first; the revision no longer
        occupies, but it is still the accepted revision of the customer's offer — the offer
        pointer is never cleared to make a hard delete possible, so the delete is refused
        (typed), and nothing moves."""
        from app.services.booking_authority import (BookingAuthority, BookingOutcome,
                                                    BookingSource, command_for_edit)
        w, rev = self.booked_customer()
        with Session(self.engine) as db:
            r = db.get(Revision, rev)
            res = BookingAuthority(db).execute(command_for_edit(
                r, source=BookingSource.CRM, new_date=r.turno_fecha, new_time=r.turno_hora,
                new_status="CANCELADO", new_profesional_id=None,
                expected_version=int(r.booking_version), actor="p4r1-cancel"))
            self.assertEqual(res.outcome, BookingOutcome.COMMITTED, res.reason)
        self.assertEqual(self.occupying_at(w.day, w.at), 0)
        before = self.image(w)
        for payload in ({"type": "revision", "lead_id": w.lead_id, "revision_id": rev},
                        {"type": "lead", "lead_id": w.lead_id}):
            with self.subTest(kind=payload["type"]):
                self.assert_refused(self.commit_delete(self.pending(**payload)),
                                    "REVISION_DELETE_PROTECTED", [rev])
                self.assertEqual(self.image(w), before)


# ══ 3.2 the Data Exchange replay matrix, through the ENCRYPTED ROUTE (P4RV-02) ═════════════

class _Envelope:
    """One Meta Data Exchange delivery: a fresh AES key and IV, RSA-OAEP over the key."""

    def __init__(self, public_key, payload: dict):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self.aes, self.iv = os.urandom(16), os.urandom(16)
        ct = AESGCM(self.aes).encrypt(self.iv, json.dumps(payload).encode(), None)
        ek = public_key.encrypt(self.aes, padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        b64 = lambda b: base64.b64encode(b).decode()       # noqa: E731
        self.body = {"encrypted_aes_key": b64(ek), "encrypted_flow_data": b64(ct),
                     "initial_vector": b64(self.iv)}

    def open(self, resp) -> dict:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        flipped = bytes(b ^ 0xFF for b in self.iv)
        return json.loads(AESGCM(self.aes).decrypt(flipped, base64.b64decode(resp.content),
                                                   None))

    def expected_attempt_hash(self) -> str:
        """Independently computed: the replay identity of this envelope and its stored
        16-hex prefix (booking_attempt_events.dx_attempt_ref → short_hash)."""
        d = hashlib.sha256(b"dxe1\x00" + self.body["initial_vector"].encode() + b"\x00"
                           + self.body["encrypted_flow_data"].encode()).hexdigest()
        return hashlib.sha256(("dx:" + d).encode()).hexdigest()[:16]


@unittest.skipUnless(PG_URL, "CONV_P3_PG_URL not set — no disposable PostgreSQL; nothing proven")
class P4R1_DataExchangeReplay(_R1):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls._keydir = tempfile.TemporaryDirectory()
        path = pathlib.Path(cls._keydir.name) / "flow_test_key.pem"
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
        cls.public_key = key.public_key()
        cls._env = mock.patch.dict(os.environ, {"FLOW_BOOKING_PRIVATE_KEY_PATH": str(path)})
        cls._env.start()

    @classmethod
    def tearDownClass(cls):
        cls._env.stop()
        cls._keydir.cleanup()
        super().tearDownClass()

    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.db import get_db
        from app.routes import flow_data_exchange as route

        def _db():
            s = Session(self.engine)
            try:
                yield s
            finally:
                s.close()
        app = FastAPI(); app.include_router(route.router)
        for dep in {get_db, route.get_db}:
            app.dependency_overrides[dep] = _db
        return TestClient(app, raise_server_exceptions=False)

    def envelope(self, w, **over):
        data = {"trigger": "confirm_booking", "booking_token": w.token,
                "date": w.day.isoformat(), "time": w.at.strftime("%H:%M"), "name": "Ana Fixture",
                "email": "ana@example.invalid", "inspection_address": "Calle Falsa 123"}
        data.update(over)
        return _Envelope(self.public_key, {"version": "3.0", "action": "data_exchange",
                                           "flow_token": w.token, "data": data})

    def deliver(self, env, times=3):
        """POST the SAME request body `times` times — a transport retry is byte-identical."""
        c, out = self.client(), []
        with mock.patch("app.settings.get_settings", lambda: H.settings(flag=True)):
            for _ in range(times):
                r = c.post("/integrations/whatsapp/flows/booking/data-exchange", json=env.body)
                self.assertEqual(r.status_code, 200, r.text[:300])
                out.append(env.open(r))
        return out

    def counts(self, w):
        e = self.engine
        return {
            "bookings": self.occupying_at(w.day, w.at),
            "commands": _rows(e, "SELECT count(*) FROM booking_commands WHERE thread_id = :t",
                              t=w.thread_id)[0],
            "events": len(self.events(w)),
            "event_uids": len({x[4] for x in self.events(w)}),
            "offer": self.offer(w),
            "mirror_set": bool(self.mirror(w)),
        }

    def test_p4r1_10_a_valid_booking_delivered_three_times_is_one_booking(self):
        w = H.seed(self.engine)
        with H.Meta() as meta:
            screens = self.deliver(self.envelope(w))
        self.assertEqual([s["screen"] for s in screens], ["SUCCESS"] * 3)
        got = self.counts(w)
        self.assertEqual((got["bookings"], got["commands"], got["events"]), (1, 1, 0))
        self.assertEqual(got["offer"], [("ACCEPTED", "CONSUMED_BY_BOOKING", None, 0)])
        self.assertEqual(len([t for _to, t in meta.sent if t == H.OWNER_TEXT]), 1,
                         "one booking, one acknowledgement")

    def test_p4r1_11_a_terminal_refusal_delivered_three_times_counts_once(self):
        w = H.seed(self.engine)
        self.change_category(w)
        with H.Meta() as meta:
            screens = self.deliver(self.envelope(w))
        self.assertTrue(all(s["screen"] == "APPOINTMENT" for s in screens), screens)
        got = self.counts(w)
        self.assertEqual((got["bookings"], got["commands"]), (0, 0))
        self.assertEqual(got["offer"], [("WITHDRAWN", "OFFER_VEHICLE_CATEGORY_CHANGED",
                                         "OFFER_VEHICLE_CATEGORY_CHANGED", 1)])
        self.assertFalse(got["mirror_set"])
        self.assertEqual([x[:4] for x in self.events(w)],
                         [("WHATSAPP_FLOW", "FINALISE", "REFUSED",
                           "OFFER_VEHICLE_CATEGORY_CHANGED")])
        self.assertEqual(meta.sent, [])

    def test_p4r1_12_a_slot_refusal_delivered_three_times_counts_once_new_iv_counts_again(self):
        owner = H.seed(self.engine)
        with H.Meta():
            self.assertEqual(self.ce(owner)().action, "booking_created")
        w = H.seed(self.engine, day=owner.day, at=owner.at)
        env = self.envelope(w)
        with H.Meta() as meta:
            screens = self.deliver(env)
        self.assertEqual(len({json.dumps(s, sort_keys=True) for s in screens}), 1,
                         "the same delivery must get the same answer")
        self.assertEqual(screens[0]["screen"], "APPOINTMENT")
        got = self.counts(w)
        self.assertEqual(got["offer"], [("ISSUED", None, "SLOT_NO_LONGER_AVAILABLE", 1)])
        self.assertTrue(got["mirror_set"])
        evs = self.events(w)
        self.assertEqual([x[:4] for x in evs],
                         [("WHATSAPP_FLOW", "FINALISE", "REFUSED", "SLOT_NO_LONGER_AVAILABLE")])
        self.assertEqual(evs[0][5], env.expected_attempt_hash(),
                         "the attempt identity is not the envelope digest")
        self.assertEqual((self.occupying_at(owner.day, owner.at), meta.sent), (1, []))
        # §3.2 case 5: a genuinely NEW submission (fresh IV) is a new attempt
        fresh = self.envelope(w)
        with H.Meta():
            self.deliver(fresh, times=1)
        got = self.counts(w)
        self.assertEqual(got["offer"], [("ISSUED", None, "SLOT_NO_LONGER_AVAILABLE", 2)])
        self.assertEqual((got["events"], got["event_uids"]), (2, 2))
        self.assertEqual(self.events(w)[1][5], fresh.expected_attempt_hash())

    def test_p4r1_13_contention_then_the_same_delivery_books_once(self):
        w = H.seed(self.engine)
        env = self.envelope(w)
        holder = self.engine.connect()
        tx = holder.begin()
        holder.execute(text("SELECT 1 FROM whatsapp_thread_states WHERE thread_id = :t "
                            "FOR UPDATE"), {"t": w.thread_id})
        try:
            with H.Meta():
                first = self.deliver(env, times=1)
        finally:
            tx.rollback(); holder.close()
        self.assertEqual(first[0]["screen"], "APPOINTMENT")
        got = self.counts(w)
        self.assertEqual((got["bookings"], got["commands"]), (0, 0))
        self.assertEqual(got["offer"], [("ISSUED", None, None, 0)], "contention is no refusal")
        with H.Meta() as meta:
            rest = self.deliver(env, times=2)
        self.assertEqual([s["screen"] for s in rest], ["SUCCESS", "SUCCESS"])
        got = self.counts(w)
        self.assertEqual((got["bookings"], got["commands"]), (1, 1))
        self.assertEqual(got["offer"], [("ACCEPTED", "CONSUMED_BY_BOOKING", None, 0)])
        self.assertEqual([x[:3] for x in self.events(w)],
                         [("WHATSAPP_FLOW", "AUTHORITY", "RETRYABLE")])
        self.assertEqual(len([t for _to, t in meta.sent if t == H.OWNER_TEXT]), 1)

    def test_p4r1_14_privacy_of_real_rows_is_hashing_not_truncation(self):
        """R1-E on PostgreSQL: real CE and Data Exchange refusals; every stored hash EQUALS an
        independently computed SHA-256 prefix, and no raw identifier or prose is stored."""
        w = H.seed(self.engine)
        self.change_category(w)
        env = self.envelope(w)
        with H.Meta():
            self.deliver(env, times=1)
        w2 = H.seed(self.engine)
        self.change_category(w2)
        with H.Meta():
            self.ce(w2)()
        with self.engine.connect() as c:
            rows = [dict(r._mapping) for r in c.execute(text(
                "SELECT * FROM booking_attempt_events ORDER BY id")).all()]
        self.assertEqual(len(rows), 2)
        blob = json.dumps(rows, default=str)
        for raw in (w.token, w2.token, w.wa_id, w2.wa_id, w2.wamid, "ana@example.invalid",
                    "Calle Falsa 123", "Ana Fixture", env.body["initial_vector"],
                    env.body["encrypted_flow_data"][:24], env.body["encrypted_aes_key"][:24]):
            self.assertNotIn(raw, blob)
        dx, ce = rows
        self.assertEqual(dx["attempt_ref_hash"], env.expected_attempt_hash())
        self.assertEqual(ce["attempt_ref_hash"],
                         hashlib.sha256(w2.wamid.encode()).hexdigest()[:16])
        from app.services.booking_authority import flow_idempotency_key
        self.assertEqual(dx["command_key_hash"], hashlib.sha256(
            flow_idempotency_key(w.token).encode()).hexdigest()[:16])


# ══ 3.3 the CE crash-redelivery matrix (P4RV-02, CE side) ═════════════════════════════════

@unittest.skipUnless(PG_URL, "CONV_P3_PG_URL not set — no disposable PostgreSQL; nothing proven")
class P4R1_CeRedelivery(_R1):

    def crash_then_redeliver(self, w):
        with H.Meta() as m1, mock.patch.object(ConversationEngine, "_sb_handoff",
                                               side_effect=Crash("after finalisation")):
            with self.assertRaises(Crash):
                self.ce(w)()
        crashed = (self.offer(w), self.events(w), H.facts(self.engine, w)["cursor"])
        with H.Meta() as m2:
            out = self.ce(w)()
        return crashed, out, m1.sent + m2.sent

    def test_p4r1_20_terminal_refusal_crash_redelivery_counts_once_keeps_the_cause(self):
        w = H.seed(self.engine)
        self.change_category(w)
        (offer, evs, cursor), out, sent = self.crash_then_redeliver(w)
        self.assertIsNone(cursor, "the crash must leave the cursor unwritten")
        truth = [("WITHDRAWN", "OFFER_VEHICLE_CATEGORY_CHANGED",
                  "OFFER_VEHICLE_CATEGORY_CHANGED", 1)]
        self.assertEqual(offer, truth)
        self.assertEqual(self.offer(w), truth, "the redelivery moved refusal truth")
        self.assertEqual(self.events(w), evs, "the redelivery wrote another attempt event")
        self.assertEqual(len(evs), 1)
        self.assertEqual(out.action, "booking_refused_handoff")
        self.assertEqual(self.occupying_at(w.day, w.at), 0)
        self.assertLessEqual(len(sent), 1, "duplicate customer message")
        self.assertNotIn(H.OWNER_TEXT, [t for _to, t in sent])

    def test_p4r1_21_slot_refusal_crash_redelivery_counts_once(self):
        owner = H.seed(self.engine)
        with H.Meta():
            self.assertEqual(self.ce(owner)().action, "booking_created")
        w = H.seed(self.engine, day=owner.day, at=owner.at)
        (offer, evs, _cursor), out, sent = self.crash_then_redeliver(w)
        truth = [("ISSUED", None, "SLOT_NO_LONGER_AVAILABLE", 1)]
        self.assertEqual(offer, truth)
        self.assertEqual(self.offer(w), truth)
        self.assertEqual(self.events(w), evs)
        self.assertEqual([x[:4] for x in evs], [("CONVERSATION_ENGINE", "FINALISE", "REFUSED",
                                                  "SLOT_NO_LONGER_AVAILABLE")])
        self.assertEqual(self.occupying_at(owner.day, owner.at), 1)
        self.assertNotIn(H.OWNER_TEXT, [t for _to, t in sent])

    def test_p4r1_22_a_new_inbound_is_a_new_attempt(self):
        owner = H.seed(self.engine)
        with H.Meta():
            self.ce(owner)()
        w = H.seed(self.engine, day=owner.day, at=owner.at)
        with H.Meta():
            self.assertEqual(self.ce(w)().action, "booking_refused_handoff")
        # the refusal handed the thread to a person; the operator hands it back, and the
        # customer submits again — a NEW inbound, so a new logical attempt
        with self.engine.begin() as c:
            c.execute(text("UPDATE whatsapp_thread_states SET needs_human = false, "
                           "last_stage = 'SCHEDULING' WHERE thread_id = :t"), {"t": w.thread_id})
        with H.Meta():
            self.assertEqual(self.ce(w, wamid=w.wamid + ".2")().action,
                             "booking_refused_handoff")
        self.assertEqual(self.offer(w), [("ISSUED", None, "SLOT_NO_LONGER_AVAILABLE", 2)])
        evs = self.events(w)
        self.assertEqual((len(evs), len({x[5] for x in evs})), (2, 2))


# ══ 3.4 PostgreSQL decides admission (P4RV-03) ════════════════════════════════════════════

@unittest.skipUnless(PG_URL, "CONV_P3_PG_URL not set — no disposable PostgreSQL; nothing proven")
class P4R1_Admission(_R1):

    E = dict(source="WHATSAPP_FLOW", stage="FINALISE", outcome="REFUSED", once_per_attempt=True)

    def test_p4r1_30_first_admitted_then_replay_one_row(self):
        from app.services.booking_attempt_events import insert_attempt_event
        self.assertEqual(self.engine.dialect.name, "postgresql")
        self.assertEqual(self.engine.dialect.driver, "psycopg")
        with Session(self.engine) as s:
            first = insert_attempt_event(s, reason="R_ONE", attempt_ref="dx:one", **self.E)
            second = insert_attempt_event(s, reason="R_TWO", attempt_ref="dx:one", **self.E)
            s.commit()
        with Session(self.engine) as s:
            third = insert_attempt_event(s, reason="R_ONE", attempt_ref="dx:one", **self.E)
            s.commit()
        self.assertEqual((first, second, third), (True, False, False))
        self.assertEqual(len(self.events()), 1)
        # why `bool(rowcount)` could not decide it: psycopg3 reports no count here
        from sqlalchemy.dialects.postgresql import insert
        from app.models import BookingAttemptEvent as E
        with Session(self.engine) as s:
            rc = s.execute(insert(E.__table__).values(
                event_uid=self.events()[0][4], source="X", stage="FINALISE", outcome="REFUSED",
                reason="R").on_conflict_do_nothing(index_elements=["event_uid"])).rowcount
            s.rollback()
        _measure(conflict_rowcount=rc)

    def test_p4r1_31_concurrent_insertors_exactly_one_admitted(self):
        from app.services.booking_attempt_events import insert_attempt_event
        n, barrier, out = 8, threading.Barrier(8), []
        lock = threading.Lock()

        def one(i):
            with Session(self.engine) as s:
                barrier.wait(10)
                got = insert_attempt_event(s, reason=f"R{i}", attempt_ref="dx:race", **self.E)
                s.commit()
            with lock:
                out.append(got)
        ts = [threading.Thread(target=one, args=(i,)) for i in range(n)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        self.assertEqual(sorted(out), [False] * (n - 1) + [True])
        self.assertEqual(len(self.events()), 1)


# ══ P4RV-04 at runtime: the declared column on a migrated database, no fixture ════════════

@unittest.skipUnless(PG_URL, "CONV_P3_PG_URL not set — no disposable PostgreSQL; nothing proven")
class P4R1_Notification(_R1):

    def test_p4r1_40_m18_on_the_migrated_schema_sends_once_with_prices_and_reads_sent(self):
        import dataclasses
        from app.routes.ops_dashboard import booking_attention
        self.assertEqual(_rows(self.engine, "SELECT count(*) FROM information_schema.columns "
                                            "WHERE table_name = 'thread_revisions' AND "
                                            "column_name = 'notification_sent_at'"), [1])
        w = H.seed(self.engine)
        cfg = dataclasses.replace(H.settings(), resend_api_key="probe-not-a-credential",
                                  internal_booking_email_from="from@example.invalid",
                                  internal_booking_email_to="to@example.invalid")
        import logging
        mails, logs = [], []

        class _Grab(logging.Handler):
            def emit(self, record):
                logs.append(record.getMessage())
        grab = _Grab(level=logging.WARNING)
        logging.getLogger().addHandler(grab)
        try:
            with H.Meta(), mock.patch("app.services.resend_email.send_booking_notification",
                                      lambda **k: mails.append(k) or True):
                out, _eng = H.run_turn(self.engine, H.flow_event(w), cfg=cfg)
                again, _ = H.run_turn(self.engine, H.flow_event(w), cfg=cfg)   # redelivery
        finally:
            logging.getLogger().removeHandler(grab)
        self.assertEqual(out.action, "booking_created")
        self.assertEqual(len(mails), 1, "the team was mailed twice for one booking")
        bad = [m for m in logs if "M18" in m or "telemetry writeback" in m
               or "InFailedSqlTransaction" in m]
        self.assertEqual(bad, [])
        with self.engine.connect() as c:
            sent_at, total = c.execute(text(
                "SELECT t.notification_sent_at, r.precio_total FROM thread_revisions t "
                "JOIN revisions r ON r.lead_id = :l WHERE t.thread_id = :t"),
                {"t": w.thread_id, "l": w.lead_id}).one()
            status = c.execute(text("SELECT status FROM ai_events WHERE wa_message_id = :m"),
                               {"m": w.wamid}).scalar()
        self.assertIsNotNone(sent_at, "the e-mail was never marked")
        self.assertEqual(mails[0]["precio_total"],
                         str(total) if total is not None else "Sin dato")
        self.assertEqual(status, "processed", "telemetry write-back failed")
        with Session(self.engine) as db:
            row = [r for r in booking_attention(db) if r["thread_id"] == w.thread_id]
        self.assertEqual([r["notification_state"] for r in row] or ["SENT"], ["SENT"])
        for r in row:
            self.assertNotIn("OPERATOR_NOTIFICATION_PENDING", r["reasons"])
            self.assertNotIn("CE_SYNC_INCOMPLETE", r["reasons"])


# ══ 3.5 the schema-order matrix (P4RV-04/05/06) ═══════════════════════════════════════════

_FP_SQL = [
    ("columns", "SELECT table_name||'.'||column_name||':'||data_type||':'||is_nullable||':'||"
                "coalesce(column_default,'') FROM information_schema.columns "
                "WHERE table_schema = current_schema() ORDER BY 1"),
    ("constraints", "SELECT conrelid::regclass||'.'||conname||':'||pg_get_constraintdef(oid) "
                    "FROM pg_constraint WHERE connamespace = current_schema()::regnamespace "
                    "ORDER BY 1"),
    ("indexes", "SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema() ORDER BY 1"),
    ("triggers", "SELECT pg_get_triggerdef(oid) FROM pg_trigger WHERE NOT tgisinternal "
                 "ORDER BY 1"),
    ("functions", "SELECT p.proname||':'||md5(p.prosrc) FROM pg_proc p WHERE p.pronamespace = "
                  "current_schema()::regnamespace AND NOT EXISTS (SELECT 1 FROM pg_depend d "
                  "WHERE d.objid = p.oid AND d.deptype = 'e') ORDER BY 1"),
]


def _fingerprint(url) -> dict:
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            return {k: [r[0] for r in c.execute(text(q)).all()] for k, q in _FP_SQL}
    finally:
        eng.dispose()


def _alembic(url, *args):
    env = {**os.environ, "DATABASE_URL": url}
    return subprocess.run([sys.executable, "-m", "alembic", *args], env=env, cwd=str(_BACKEND),
                          capture_output=True, text=True, timeout=600)


def _version(url):
    return _rows(_eng(url), "SELECT version_num FROM alembic_version")


_ENGINES: dict = {}


def _eng(url):
    if url not in _ENGINES:
        _ENGINES[url] = create_engine(url)
    return _ENGINES[url]


class _Schema(unittest.TestCase):

    @classmethod
    def tearDownClass(cls):
        for e in _ENGINES.values():
            e.dispose()
        _ENGINES.clear()

    def upgrade(self, url, target="head"):
        r = _alembic(url, "upgrade", target)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])

    def assert_enforced(self, url):
        """Enforced, not merely present: each object is exercised and must refuse."""
        e = _eng(url)
        with e.begin() as c:
            self.assertEqual(c.execute(text(
                "SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND conrelid = "
                "'booking_attempt_events'::regclass")).scalar(), 0)
            c.execute(text("INSERT INTO booking_attempt_events (event_uid, source, stage, "
                           "outcome, reason) VALUES ('enf-1', 'X', 'AUTHORITY', 'REFUSED', 'R')"))
        refusals = {
            "update": "UPDATE booking_attempt_events SET reason = 'Y'",
            "delete": "DELETE FROM booking_attempt_events",
            "stage": "INSERT INTO booking_attempt_events (event_uid, source, stage, outcome, "
                     "reason) VALUES ('enf-2', 'X', 'WHENEVER', 'REFUSED', 'R')",
            "outcome": "INSERT INTO booking_attempt_events (event_uid, source, stage, outcome, "
                       "reason) VALUES ('enf-3', 'X', 'AUTHORITY', 'OK', 'R')",
            "unique": "INSERT INTO booking_attempt_events (event_uid, source, stage, outcome, "
                      "reason) VALUES ('enf-1', 'X', 'AUTHORITY', 'REFUSED', 'R')",
        }
        expect = {"update": "append-only", "delete": "append-only",
                  "stage": "ck_booking_attempt_events_stage",
                  "outcome": "ck_booking_attempt_events_outcome",
                  "unique": "booking_attempt_events_event_uid_key"}
        for name, stmt in refusals.items():
            with self.subTest(enforcement=name), e.connect() as c:
                with self.assertRaises(Exception) as cm:
                    c.execute(text(stmt)); c.commit()
                self.assertIn(expect[name], str(cm.exception))
        with e.begin() as c:
            self.assertEqual(c.execute(text("SELECT reason FROM booking_attempt_events")
                                       ).scalars().all(), ["R"])
            c.execute(text("TRUNCATE booking_attempt_events"))
        idx = set(_rows(e, "SELECT indexname FROM pg_indexes WHERE tablename = "
                           "'booking_attempt_events'"))
        self.assertTrue({"ix_booking_attempt_events_occurred", "ix_booking_attempt_events_thread",
                         "ix_booking_attempt_events_thread_revision"} <= idx, idx)

    def startup_missing(self, url):
        from app.services.schema_preflight import missing_declared_booking_schema
        return missing_declared_booking_schema(_eng(url))


@unittest.skipUnless(PARENT_URL and APPFIRST_URL,
                     "CONV_P4R1_PARENT_URL / CONV_P4R1_APPFIRST_URL not set — nothing proven")
class P4R1_SchemaOrder(_Schema):
    """Ordered: 49 → 55 share the two databases and run in name order."""

    fp: dict = {}

    def test_p4r1_49_the_application_refuses_to_start_on_an_unmigrated_schema(self):
        """Deployment preflight: the image started first (its `create_all` ran, creating the
        evidence table WITHOUT its trigger, and adding no column to an existing table), and
        the application's start-up check refuses to run on that schema."""
        from app.db import Base
        import app.models  # noqa: F401
        self.assertEqual(_version(APPFIRST_URL), [P4_PARENT])
        type(self).fp["parent"] = _fingerprint(PARENT_URL)
        Base.metadata.create_all(_eng(APPFIRST_URL))       # the image started first
        with _eng(APPFIRST_URL).connect() as c:
            self.assertIsNotNone(c.execute(text(
                "SELECT to_regclass('booking_attempt_events')")).scalar())
            self.assertEqual(c.execute(text(
                "SELECT count(*) FROM pg_trigger WHERE tgrelid = "
                "'booking_attempt_events'::regclass AND NOT tgisinternal")).scalar(), 0,
                "create_all is not expected to install the trigger")
        from app.services.schema_preflight import (DeclaredSchemaMissing,
                                                   require_declared_booking_schema)
        # STRICT-SCHEMA-ADOPTION adaptation. OLD: exactly 2 problems (the column + the
        # trigger, by name). NEW: the preflight also requires the exact Alembic head and the
        # canonical fingerprint, so each schema reports its three problems — the version, the
        # evidence table (absent / not canonical) and the absent column — asserted by content.
        for url in (PARENT_URL, APPFIRST_URL):
            with self.subTest(db=url.rsplit("/", 1)[-1]):
                missing = self.startup_missing(url)
                self.assertEqual(len(missing), 3, missing)
                self.assertIn(f"Alembic version is ['{P4_PARENT}']", missing[0])
                self.assertIn("booking_attempt_events is not the canonical shape", missing[1])
                self.assertIn("notification_sent_at", missing[2])
                with self.assertRaises(DeclaredSchemaMissing):
                    require_declared_booking_schema(_eng(url))

    def test_p4r1_50_parent_bootstrap_then_upgrade_to_the_new_head(self):
        self.upgrade(PARENT_URL)
        self.assertEqual(_version(PARENT_URL), [ALEMBIC_HEAD])
        self.assert_enforced(PARENT_URL)
        self.assertEqual(self.startup_missing(PARENT_URL), [])
        type(self).fp["upgraded"] = _fingerprint(PARENT_URL)

    def test_p4r1_51_app_first_create_all_then_upgrade_converges(self):
        with _eng(APPFIRST_URL).connect() as c:          # the state p4r1_49 left
            self.assertIsNotNone(c.execute(text(
                "SELECT to_regclass('booking_attempt_events')")).scalar())
        self.upgrade(APPFIRST_URL)
        self.assertEqual(_version(APPFIRST_URL), [ALEMBIC_HEAD])
        self.assert_enforced(APPFIRST_URL)
        self.assertEqual(self.startup_missing(APPFIRST_URL), [])
        type(self).fp["appfirst"] = _fingerprint(APPFIRST_URL)

    def test_p4r1_52_both_supported_orders_reach_the_same_schema(self):
        self.assertEqual(sorted(k for k in ("upgraded", "appfirst") if k in self.fp),
                         ["appfirst", "upgraded"], "a supported upgrade order did not complete")
        a, b = self.fp["upgraded"], self.fp["appfirst"]
        for k in a:
            with self.subTest(part=k):
                self.assertEqual(a[k], b[k])

    def test_p4r1_53_downgrade_is_refused_while_attempt_evidence_exists(self):
        with _eng(PARENT_URL).begin() as c:
            c.execute(text("INSERT INTO booking_attempt_events (event_uid, source, stage, "
                           "outcome, reason) VALUES ('ev-1', 'X', 'AUTHORITY', 'REFUSED', 'R')"))
        try:
            r = _alembic(PARENT_URL, "downgrade", P4_PARENT)
            self.assertNotEqual(r.returncode, 0, "an evidence-bearing downgrade ran")
            self.assertIn("audit-data loss", r.stderr + r.stdout)
            self.assertEqual(_version(PARENT_URL), [ALEMBIC_HEAD], "a partial downgrade was recorded")
            self.assertEqual(_fingerprint(PARENT_URL), self.fp.get("upgraded"),
                             "partial downgrade")
            self.assertEqual(_rows(_eng(PARENT_URL),
                                   "SELECT count(*) FROM booking_attempt_events"), [1])
        finally:
            # the documented owner checkpoint: export + hash, then a DELIBERATE truncation
            with _eng(PARENT_URL).begin() as c:
                c.execute(text("TRUNCATE booking_attempt_events"))

    def test_p4r1_54_downgrade_is_refused_while_a_notification_timestamp_exists(self):
        with _eng(PARENT_URL).begin() as c:
            tr = c.execute(text("SELECT id FROM thread_revisions LIMIT 1")).scalar()
            if tr is None:
                th = c.execute(text("SELECT id FROM whatsapp_threads LIMIT 1")).scalar()
                if th is None:
                    ct = c.execute(text("INSERT INTO whatsapp_contacts (wa_id) "
                                        "VALUES ('549110009999') RETURNING id")).scalar()
                    th = c.execute(text("INSERT INTO whatsapp_threads (contact_id) "
                                        "VALUES (:c) RETURNING id"),
                                   {"c": ct}).scalar()
                tr = c.execute(text("INSERT INTO thread_revisions (thread_id, status) "
                                    "VALUES (:t, 'booked') RETURNING id"), {"t": th}).scalar()
            c.execute(text("UPDATE thread_revisions SET notification_sent_at = now() "
                           "WHERE id = :i"), {"i": tr})
        r = _alembic(PARENT_URL, "downgrade", P4_REV)
        self.assertNotEqual(r.returncode, 0, "a timestamp-bearing downgrade ran")
        self.assertIn("notification-evidence loss", r.stderr + r.stdout)
        self.assertEqual(_version(PARENT_URL), [ALEMBIC_HEAD])
        self.assertEqual(_rows(_eng(PARENT_URL), "SELECT count(*) FROM thread_revisions WHERE "
                                                 "notification_sent_at IS NOT NULL"), [1])
        with _eng(PARENT_URL).begin() as c:
            c.execute(text("DELETE FROM thread_revisions WHERE id = :i"), {"i": tr})

    def test_p4r1_55_clean_upgrade_downgrade_reupgrade_is_exact(self):
        r = _alembic(PARENT_URL, "downgrade", P4_PARENT)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertEqual(_version(PARENT_URL), [P4_PARENT])
        self.assertEqual(_fingerprint(PARENT_URL), self.fp["parent"])
        self.upgrade(PARENT_URL)
        self.assertEqual(_fingerprint(PARENT_URL), self.fp["upgraded"])
        self.assert_enforced(PARENT_URL)


@unittest.skipUnless(LEGACY_URL, "CONV_P4R1_LEGACY_URL not set — nothing proven")
class P4R1_LegacyStartupColumn(_Schema):

    def test_p4r1_56_a_startup_created_column_is_adopted_with_its_values(self):
        e = _eng(LEGACY_URL)
        self.assertEqual(_version(LEGACY_URL), [P4_PARENT])
        with e.begin() as c:                       # what the OLD start-up did, verbatim
            c.execute(text("ALTER TABLE thread_revisions "
                           "ADD COLUMN IF NOT EXISTS notification_sent_at TIMESTAMPTZ"))
            ct = c.execute(text("INSERT INTO whatsapp_contacts (wa_id) VALUES "
                                "('549110008888') RETURNING id")).scalar()
            th = c.execute(text("INSERT INTO whatsapp_threads (contact_id) "
                                "VALUES (:c) RETURNING id"),
                           {"c": ct}).scalar()
            c.execute(text("INSERT INTO thread_revisions (thread_id, status, notification_sent_at) "
                           "VALUES (:t, 'booked', '2026-10-01T12:00:00Z')"), {"t": th})
        before = _rows(e, "SELECT notification_sent_at::text FROM thread_revisions")
        self.upgrade(LEGACY_URL)
        self.assertEqual(_version(LEGACY_URL), [ALEMBIC_HEAD])
        self.assertEqual(_rows(e, "SELECT notification_sent_at::text FROM thread_revisions"),
                         before, "the upgrade rewrote existing evidence")
        self.assertEqual(_rows(e, "SELECT coalesce(col_description('thread_revisions'::regclass,"
                                  " attnum), '') FROM pg_attribute WHERE attrelid = "
                                  "'thread_revisions'::regclass AND attname = "
                                  "'notification_sent_at'"), [""], "adopted, not re-created")
        self.assert_enforced(LEGACY_URL)
        r = _alembic(LEGACY_URL, "downgrade", P4_REV)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("notification-evidence loss", r.stderr + r.stdout)
        self.assertEqual(_rows(e, "SELECT notification_sent_at::text FROM thread_revisions"),
                         before)
        # null-only: the adopted column is LEFT in place (it pre-dated the revision)
        with e.begin() as c:
            c.execute(text("UPDATE thread_revisions SET notification_sent_at = NULL"))
        r = _alembic(LEGACY_URL, "downgrade", P4_REV)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertEqual(_rows(e, "SELECT count(*) FROM information_schema.columns WHERE "
                                  "table_name = 'thread_revisions' AND column_name = "
                                  "'notification_sent_at'"), [1])


@unittest.skipUnless(EMPTY_URL, "CONV_P4R1_EMPTY_URL not set — nothing proven")
class P4R1_InheritedBaseChain(_Schema):

    def test_p4r1_57_alembic_cannot_build_from_empty_inherited_limitation(self):
        """Stated, not hidden: the base revision of the chain presupposes tables that only
        `create_all` makes. This remediation's supported paths are the bootstrap + upgrade
        and app-first + upgrade orders proven above."""
        r = _alembic(EMPTY_URL, "upgrade", "head")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("NoSuchTableError", r.stderr + r.stdout)


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
