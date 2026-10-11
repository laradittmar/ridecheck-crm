"""G3-7P — the booking-offer acceptance authority, proven on real PostgreSQL.

Disposable database only (`G7P_PG_URL`); skips cleanly without it and says so, because a
concurrency claim that did not run is not a weaker claim, it is no claim at all.

Why these tests exist. The G3-7N independent review measured that its central guards —
OFFER_WITHDRAWN, FOREIGN_OFFER and SUPERSEDED_OFFER — were asserted only by
`inspect.getsource` plus `assertIn`, so disabling each one left the entire 6,098-test suite
identity-identical (104 -> 104, NEW=[], GONE=[]). The suite could not see the milestone's
own protections being removed. Every test here therefore drives the real
`ConversationEngine.handle()` against real persisted rows and asserts on STATE — revisions,
offer status, candidate and zone bindings — never on source text.

SQLite proves nothing here: the live-offer invariant is a PostgreSQL partial unique index
and acceptance is a conditional UPDATE under FOR UPDATE, so the concurrency tests run
against PostgreSQL with independent sessions or they do not run.
"""
from __future__ import annotations

import os
import sys
import threading
import time as _t
import unittest
from datetime import date, time
from pathlib import Path

_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

PG_URL = os.environ.get("G7P_PG_URL", "")

WA = "549110000777"
BOOKING_PAYLOAD = {
    "nombre_apellido": "Probe Cliente", "email": "p@example.test", "telefono": WA,
    "como_llego": "Instagram", "direccion": "Calle Falsa 123", "tipo_vendedor": "particular",
}


@unittest.skipUnless(PG_URL, "G7P_PG_URL not set — no disposable PostgreSQL; nothing proven")
class G7POfferAuthorityPG(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_URL, pool_size=12, max_overflow=4)
        cls.SF = sessionmaker(bind=cls.engine, expire_on_commit=False)
        with cls.engine.begin() as c:
            # the authority's table must exist, or every test here would be vacuous
            assert c.execute(text("SELECT to_regclass('whatsapp_booking_offers')")).scalar()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    # ── fixture ──────────────────────────────────────────────────────────────

    def setUp(self):
        import app.models as M
        self.M = M
        with self.engine.begin() as c:
            for t in ("whatsapp_booking_offers", "whatsapp_clarification_attempts",
                      "whatsapp_messages", "thread_revisions", "whatsapp_thread_candidates",
                      "whatsapp_thread_states", "revisions", "whatsapp_threads", "leads",
                      "whatsapp_contacts"):
                c.execute(text(f"TRUNCATE {t} RESTART IDENTITY CASCADE"))
        db = self.SF()
        ct = M.WhatsAppContact(wa_id=WA, display_name="Probe"); db.add(ct); db.flush()
        lead = M.Lead(nombre="Probe", apellido="Cliente", telefono=WA,
                      estado="CONSULTA_NUEVA", flag="ACEPTADO"); db.add(lead); db.flush()
        th = M.WhatsAppThread(contact_id=ct.id, lead_id=lead.id); db.add(th); db.flush()
        st = M.WhatsAppThreadState(thread_id=th.id); db.add(st); db.flush()
        cand = M.WhatsAppThreadCandidate(
            thread_id=th.id, marca="Ford", modelo="Focus", anio=2017, tipo_vehiculo="AUTO",
            status="confirmed", source_text="un ford focus 2017")
        db.add(cand); db.flush()
        st.current_focus_candidate_id = cand.id
        st.home_zone_group, st.home_zone_detail = "CABA", "Palermo"
        st.last_stage = "SCHEDULING"
        st.preferred_day, st.preferred_time = "2026-11-10", "10:00"
        st.active_requested_date, st.last_requested_time = "2026-11-10", "10:00"
        st.last_offered_slots = st.last_visible_slots = '["2026-11-10T10:00"]'
        db.commit()
        self.thread_id, self.lead_id, self.cand_a = th.id, lead.id, cand.id
        db.close()

    # ── engine with ONLY the external transport stubbed ──────────────────────

    def _engine(self, db=None):
        from app.services.conversation_engine import ConversationEngine
        from app.settings import get_settings
        db = db or self.SF()
        eng = ConversationEngine(db=db, settings=get_settings())
        eng._send_text_to_wa = lambda ctx, t: "wamid.text"
        eng._send_flow_button = lambda ctx, body, token, **kw: "wamid.flow"
        eng._send_fallback_human_review_notification = lambda *a, **k: None
        eng._send_booking_notification = lambda *a, **k: None
        return db, eng

    def _clear_human(self):
        db = self.SF()
        db.execute(text("UPDATE whatsapp_thread_states SET needs_human = false "
                        "WHERE thread_id = :t"), {"t": self.thread_id})
        db.commit(); db.close()

    def _issue(self):
        """Issue a real offer through the real dispatch path."""
        db, eng = self._engine()
        ctx = eng._load_context(self.thread_id)
        ctx.state.needs_human = False
        db.commit()
        eng._send_booking_flow(ctx, ctx.state, booking_flow_id="28104222025943520")
        db.refresh(ctx.state)
        token = ctx.state.flow_booking_token
        db.close()
        return token

    def _submit(self, token, *, tag="x"):
        from app.schemas.conversation import ConversationHandleIn
        self._clear_human()
        db, eng = self._engine()
        out = eng.handle(ConversationHandleIn(
            thread_id=self.thread_id, wa_message_id=f"wamid.{tag}.{_t.time()}", wa_id=WA,
            message_type="flow_response", flow_response=dict(BOOKING_PAYLOAD),
            flow_token=token))
        db.close()
        return out

    def _flow(self, payload, token):
        from app.schemas.conversation import ConversationHandleIn
        db, eng = self._engine()
        out = eng.handle(ConversationHandleIn(
            thread_id=self.thread_id, wa_message_id=f"wamid.f.{_t.time()}", wa_id=WA,
            message_type="flow_response", flow_response=payload, flow_token=token))
        db.close()
        return out

    def _counts(self):
        db = self.SF()
        tr = db.execute(text("SELECT count(*) FROM thread_revisions WHERE thread_id=:t"),
                        {"t": self.thread_id}).scalar()
        rv = db.execute(text("SELECT count(*) FROM revisions")).scalar()
        db.close()
        return tr, rv

    def _offers(self):
        db = self.SF()
        rows = db.execute(text(
            "SELECT id, status, terminal_reason, candidate_id, zone_group, zone_detail, "
            "refused_count, last_refusal FROM whatsapp_booking_offers "
            "WHERE thread_id=:t ORDER BY id"), {"t": self.thread_id}).fetchall()
        db.close()
        return [tuple(r) for r in rows]

    def _state(self):
        db = self.SF()
        row = db.execute(text(
            "SELECT flow_booking_token, current_focus_candidate_id, home_zone_group, "
            "home_zone_detail, preferred_day, preferred_time, active_requested_date, "
            "last_requested_time, last_offered_slots, last_visible_slots "
            "FROM whatsapp_thread_states WHERE thread_id=:t"), {"t": self.thread_id}).one()
        db.close()
        return dict(zip(("token", "focus", "zone_group", "zone_detail", "preferred_day",
                         "preferred_time", "active_requested_date", "last_requested_time",
                         "last_offered_slots", "last_visible_slots"), row))

    # ── 1. the happy path, exactly once ──────────────────────────────────────

    def test_g7p_pg_01_a_valid_offer_is_accepted_exactly_once(self):
        token = self._issue()
        self.assertTrue(token)
        self.assertEqual([o[1] for o in self._offers()], ["ISSUED"])
        out = self._submit(token, tag="ok")
        self.assertEqual(out.action, "booking_created")
        self.assertEqual(self._counts(), (1, 1))
        offer = self._offers()[0]
        self.assertEqual(offer[1], "ACCEPTED")
        self.assertEqual(offer[2], "CONSUMED_BY_BOOKING")
        # the mirror follows the authority, so nothing downstream reads a live offer
        self.assertIsNone(self._state()["token"])

    def test_g7p_pg_02_an_accepted_token_cannot_be_replayed(self):
        token = self._issue()
        self._submit(token, tag="first")
        before = self._counts()
        out = self._submit(token, tag="replay")
        self.assertNotEqual(out.action, "booking_created")
        self.assertEqual(self._counts(), before)
        self.assertEqual(self._offers()[0][7], "OFFER_ALREADY_ACCEPTED")

    # ── 2. every refusal creates nothing ─────────────────────────────────────

    def test_g7p_pg_03_no_offer_record_refuses(self):
        # nothing was ever issued: absence of evidence is never permission
        out = self._submit("1-1700000000-deadbeef", tag="norec")
        self.assertNotEqual(out.action, "booking_created")
        self.assertEqual(self._counts(), (0, 0))

    def test_g7p_pg_04_missing_and_empty_tokens_refuse(self):
        self._issue()
        for token in (None, "", "   "):
            with self.subTest(token=token):
                out = self._submit(token, tag="empty")
                self.assertNotEqual(out.action, "booking_created")
                self.assertEqual(self._counts(), (0, 0))

    def test_g7p_pg_05_fabricated_and_tampered_tokens_refuse(self):
        token = self._issue()
        for bad in ("zzz-not-a-token", token[:-3] + "zzz", token + "0"):
            with self.subTest(bad=bad):
                out = self._submit(bad, tag="fake")
                self.assertNotEqual(out.action, "booking_created")
                self.assertEqual(self._counts(), (0, 0))

    def test_g7p_pg_06_a_foreign_threads_token_refuses(self):
        from app.services.offer_lifecycle import OfferLifecycle
        mine = self._issue()
        # a second thread with its own live offer
        db = self.SF()
        ct = self.M.WhatsAppContact(wa_id="549110000888", display_name="Other")
        db.add(ct); db.flush()
        th = self.M.WhatsAppThread(contact_id=ct.id, lead_id=self.lead_id)
        db.add(th); db.flush()
        st = self.M.WhatsAppThreadState(thread_id=th.id); db.add(st); db.flush()
        other = OfferLifecycle(db).issue(
            st, thread_id=th.id, token=f"{th.id}-1700000000-other", cycle_key=0,
            candidate_id=None, zone_group="CABA", zone_detail="Palermo")
        db.commit()
        foreign_token = st.flow_booking_token
        db.close()
        out = self._submit(foreign_token, tag="foreign")
        self.assertNotEqual(out.action, "booking_created")
        self.assertEqual(self._counts(), (0, 0))
        db = self.SF()
        status = db.execute(text("SELECT status, last_refusal FROM whatsapp_booking_offers "
                                 "WHERE id=:i"), {"i": other.id}).one()
        db.close()
        self.assertEqual(tuple(status), ("ISSUED", "FOREIGN_OFFER"))
        self.assertTrue(mine)          # our own offer is untouched by someone else's tap

    def test_g7p_pg_07_a_withdrawn_offer_refuses(self):
        from app.services.offer_lifecycle import WithdrawReason
        token = self._issue()
        db, eng = self._engine()
        ctx = eng._load_context(self.thread_id)
        eng._withdraw_booking_offer(ctx, ctx.state, reason=WithdrawReason.VEHICLE_CHANGED)
        db.commit(); db.close()
        out = self._submit(token, tag="wd")
        self.assertNotEqual(out.action, "booking_created")
        self.assertEqual(self._counts(), (0, 0))
        offer = self._offers()[0]
        self.assertEqual((offer[1], offer[2], offer[7]),
                         ("WITHDRAWN", "VEHICLE_CHANGED", "OFFER_WITHDRAWN"))

    def test_g7p_pg_08_a_superseded_offer_refuses_and_the_new_one_books(self):
        old = self._issue()
        new = self._issue()
        self.assertNotEqual(old, new)
        statuses = [o[1] for o in self._offers()]
        self.assertEqual(statuses, ["SUPERSEDED", "ISSUED"])
        out_old = self._submit(old, tag="old")
        self.assertNotEqual(out_old.action, "booking_created")
        self.assertEqual(self._counts(), (0, 0))
        out_new = self._submit(new, tag="new")
        self.assertEqual(out_new.action, "booking_created")
        self.assertEqual(self._counts(), (1, 1))
        # ordering holds in both directions: the old token stays dead afterwards too
        out_old2 = self._submit(old, tag="old2")
        self.assertNotEqual(out_old2.action, "booking_created")
        self.assertEqual(self._counts(), (1, 1))

    # ── 3. the bindings refuse even when nothing withdrew ────────────────────

    def test_g7p_pg_09_a_candidate_change_alone_refuses_the_offer(self):
        """The binding is the second line: even with the offer still ISSUED, a submission
        for a vehicle that is no longer canonical may not book."""
        token = self._issue()
        db = self.SF()
        cand_b = self.M.WhatsAppThreadCandidate(
            thread_id=self.thread_id, marca="Peugeot", modelo="208", anio=2019,
            tipo_vehiculo="AUTO", status="confirmed", source_text="peugeot 208")
        db.add(cand_b); db.flush()
        db.execute(text("UPDATE whatsapp_thread_states SET current_focus_candidate_id=:c "
                        "WHERE thread_id=:t"), {"c": cand_b.id, "t": self.thread_id})
        db.commit(); db.close()
        self.assertEqual([o[1] for o in self._offers()], ["ISSUED"])     # nothing withdrew
        out = self._submit(token, tag="cand")
        self.assertNotEqual(out.action, "booking_created")
        self.assertEqual(self._counts(), (0, 0))
        self.assertEqual(self._offers()[0][7], "OFFER_CANDIDATE_CHANGED")

    def test_g7p_pg_10_a_zone_change_alone_refuses_the_offer(self):
        token = self._issue()
        db = self.SF()
        db.execute(text("UPDATE whatsapp_thread_states SET home_zone_group='Norte', "
                        "home_zone_detail='San Isidro' WHERE thread_id=:t"),
                   {"t": self.thread_id})
        db.commit(); db.close()
        self.assertEqual([o[1] for o in self._offers()], ["ISSUED"])
        out = self._submit(token, tag="zone")
        self.assertNotEqual(out.action, "booking_created")
        self.assertEqual(self._counts(), (0, 0))
        self.assertEqual(self._offers()[0][7], "OFFER_LOCATION_CHANGED")

    # ── 4. the two correction domains ────────────────────────────────────────

    def test_g7p_pg_11_a_vehicle_change_withdraws_the_offer(self):
        token = self._issue()
        out = self._flow({"tipo_vehiculo": "AUTO", "marca": "Peugeot", "modelo": "208",
                          "anio": "2019"}, f"vehicle-fallback-{self.thread_id}")
        self.assertTrue(out.handled)
        st = self._state()
        db = self.SF()
        rows = db.execute(text("SELECT id, marca, modelo FROM whatsapp_thread_candidates "
                               "WHERE thread_id=:t ORDER BY id"), {"t": self.thread_id}
                          ).fetchall()
        db.close()
        # the old candidate is history, not overwritten, and focus moved to the new one
        self.assertEqual([(r[1], r[2]) for r in rows], [("Ford", "Focus"), ("Peugeot", "208")])
        self.assertNotEqual(st["focus"], self.cand_a)
        offer = self._offers()[0]
        self.assertEqual((offer[1], offer[2]), ("WITHDRAWN", "VEHICLE_CHANGED"))
        # every scheduling fact the offer produced is gone, and the mirror with it
        self.assertIsNone(st["token"])
        for field in ("preferred_day", "preferred_time", "active_requested_date",
                      "last_requested_time", "last_offered_slots", "last_visible_slots"):
            self.assertIsNone(st[field], field)
        self.assertNotEqual(self._submit(token, tag="late").action, "booking_created")
        self.assertEqual(self._counts(), (0, 0))

    def test_g7p_pg_12_a_location_change_withdraws_the_offer(self):
        """B-01. G3-7N instrumented only the vehicle handler; moving CABA/Palermo ->
        Norte/San Isidro left the offer live and the late tap booked at the old zone."""
        token = self._issue()
        out = self._flow({"zona_general": "NORTE", "localidad": "San Isidro"},
                         f"location-fallback-{self.thread_id}")
        self.assertTrue(out.handled)
        st = self._state()
        self.assertEqual((st["zone_group"], st["zone_detail"]), ("Norte", "San Isidro"))
        offer = self._offers()[0]
        self.assertEqual((offer[1], offer[2]), ("WITHDRAWN", "LOCATION_CHANGED"))
        self.assertIsNone(st["token"])
        self.assertNotEqual(self._submit(token, tag="loclate").action, "booking_created")
        self.assertEqual(self._counts(), (0, 0))

    def test_g7p_pg_13_a_new_offer_after_a_change_books_the_new_vehicle(self):
        self._issue()
        self._flow({"tipo_vehiculo": "AUTO", "marca": "Peugeot", "modelo": "208",
                    "anio": "2019"}, f"vehicle-fallback-{self.thread_id}")
        db = self.SF()
        db.execute(text("UPDATE whatsapp_thread_states SET preferred_day='2026-11-20', "
                        "preferred_time='11:00', needs_human=false WHERE thread_id=:t"),
                   {"t": self.thread_id})
        db.commit(); db.close()
        token = self._issue()
        self.assertTrue(token)
        out = self._submit(token, tag="newveh")
        self.assertEqual(out.action, "booking_created")
        self.assertEqual(self._counts(), (1, 1))
        db = self.SF()
        booked = db.execute(text("SELECT marca, modelo FROM revisions")).fetchall()
        db.close()
        self.assertEqual([tuple(b) for b in booked], [("Peugeot", "208")])

    # ── 5. restart and a later correction ────────────────────────────────────

    def test_g7p_pg_14_refusal_survives_a_process_restart(self):
        from app.services.offer_lifecycle import WithdrawReason
        token = self._issue()
        db, eng = self._engine()
        ctx = eng._load_context(self.thread_id)
        eng._withdraw_booking_offer(ctx, ctx.state, reason=WithdrawReason.VEHICLE_CHANGED)
        db.commit(); db.close()
        # a brand-new engine on a brand-new session: the authority is the database
        for tag in ("r1", "r2"):
            out = self._submit(token, tag=tag)
            self.assertNotEqual(out.action, "booking_created")
        self.assertEqual(self._counts(), (0, 0))

    def test_g7p_pg_15_a_text_correction_after_withdrawal_cannot_revive_the_token(self):
        """The G3-7N review's compounding case: a later text-path correction wrote
        `flow_booking_token = None` over the tombstone, and the old token booked again.
        Authority is the offer row now, so a NULL mirror grants nothing."""
        from app.services import pending_correction as pc
        from app.services.offer_lifecycle import WithdrawReason
        token = self._issue()
        db, eng = self._engine()
        ctx = eng._load_context(self.thread_id)
        eng._withdraw_booking_offer(ctx, ctx.state, reason=WithdrawReason.VEHICLE_CHANGED)
        disp = pc.proposal_disposition(ctx.state, (("tipo_vehiculo", "AUTO", "PICKUP"),))
        eng._withdraw_dependent_proposals(ctx, ctx.state, disp)
        db.commit(); db.close()
        self.assertIsNone(self._state()["token"])
        out = self._submit(token, tag="revive")
        self.assertNotEqual(out.action, "booking_created")
        self.assertEqual(self._counts(), (0, 0))

    # ── 6. concurrency, which only PostgreSQL can answer ────────────────────

    def _race(self, f1, f2):
        out = {}
        t1 = threading.Thread(target=f1, args=(out,))
        t2 = threading.Thread(target=f2, args=(out,))
        t1.start(); t2.start(); t1.join(); t2.join()
        return out

    def test_g7p_pg_16_concurrent_duplicate_acceptance_books_once(self):
        """B-05. Before G3-7P two concurrent submissions of one valid token created two
        ThreadRevisions and two CRM Revisions in 9 of 10 trials; `needs_human` was the only
        thing standing between a double tap and a double booking, which is suppression,
        not idempotency."""
        for trial in range(4):
            with self.subTest(trial=trial):
                self.setUp()
                token = self._issue()
                self._clear_human()
                self._race(lambda o: o.setdefault("a", self._submit_raw(token, "ca")),
                           lambda o: o.setdefault("b", self._submit_raw(token, "cb")))
                self.assertEqual(self._counts(), (1, 1))
                self.assertEqual([o[1] for o in self._offers()], ["ACCEPTED"])

    def _submit_raw(self, token, tag):
        """A submission that does NOT reset needs_human — the race must be decided by the
        database, not by an application flag."""
        from app.schemas.conversation import ConversationHandleIn
        db, eng = self._engine()
        try:
            return eng.handle(ConversationHandleIn(
                thread_id=self.thread_id, wa_message_id=f"wamid.{tag}.{_t.time()}",
                wa_id=WA, message_type="flow_response",
                flow_response=dict(BOOKING_PAYLOAD), flow_token=token)).action
        except Exception as exc:                 # a loser may surface a serialisation error
            return f"EXC:{type(exc).__name__}"
        finally:
            db.close()

    def test_g7p_pg_17_withdrawal_versus_acceptance_never_books_a_stale_offer(self):
        """B-06. Either acceptance wins and books exactly what its offer was bound to, or
        withdrawal wins and nothing books. A booking whose vehicle or zone disagrees with
        its own offer is the forbidden outcome."""
        for trial in range(4):
            with self.subTest(trial=trial):
                self.setUp()
                token = self._issue()
                self._clear_human()

                def tap(o):
                    o["tap"] = self._submit_raw(token, "rt")

                def change(o):
                    try:
                        o["chg"] = self._flow(
                            {"tipo_vehiculo": "AUTO", "marca": "Peugeot", "modelo": "208",
                             "anio": "2019"}, f"vehicle-fallback-{self.thread_id}").action
                    except Exception as exc:
                        o["chg"] = f"EXC:{type(exc).__name__}"

                self._race(tap, change)
                tr, _rv = self._counts()
                self.assertLessEqual(tr, 1)
                db = self.SF()
                faults = db.execute(text("""
                    SELECT tr.id, tr.candidate_id, o.status, o.candidate_id,
                           r.zone_group, o.zone_group
                    FROM thread_revisions tr
                    LEFT JOIN whatsapp_booking_offers o
                           ON o.accepted_thread_revision_id = tr.id
                    LEFT JOIN revisions r ON r.id = o.accepted_revision_id
                    WHERE tr.thread_id = :t
                      AND (o.id IS NULL OR o.status <> 'ACCEPTED'
                           OR tr.candidate_id IS DISTINCT FROM o.candidate_id
                           OR r.zone_group IS DISTINCT FROM o.zone_group)"""),
                    {"t": self.thread_id}).fetchall()
                db.close()
                self.assertEqual([tuple(f) for f in faults], [],
                                 "a booking disagreed with the offer that authorized it")

    def test_g7p_pg_17b_acceptance_is_rolled_back_with_its_booking(self):
        """The acceptance and the booking are ONE transaction.

        If `claim()` committed on its own, a failure between the claim and the booking
        commit would leave the offer ACCEPTED with no booking — the customer's slot consumed
        and nothing scheduled, unrecoverable by retry because the offer is terminal. Here
        the commit is made to fail once: the offer must come back ISSUED, no booking may
        exist, and the retry must complete the operation.
        """
        token = self._issue()
        self._clear_human()
        db, eng = self._engine()
        real_commit = db.commit
        calls = {"n": 0}

        def fail_first(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("induced failure at the booking commit")
            return real_commit(*a, **kw)

        db.commit = fail_first
        from app.schemas.conversation import ConversationHandleIn
        try:
            eng.handle(ConversationHandleIn(
                thread_id=self.thread_id, wa_message_id="wamid.precommit", wa_id=WA,
                message_type="flow_response", flow_response=dict(BOOKING_PAYLOAD),
                flow_token=token))
        except Exception:
            pass
        db.close()
        self.assertEqual(self._counts(), (0, 0), "a booking survived a failed commit")
        self.assertEqual([o[1] for o in self._offers()], ["ISSUED"],
                         "the acceptance was not rolled back with the booking")
        out = self._submit(token, tag="afterfail")
        self.assertEqual(out.action, "booking_created")
        self.assertEqual(self._counts(), (1, 1))
        self.assertEqual([o[1] for o in self._offers()], ["ACCEPTED"])

    def test_g7p_pg_18_the_database_forbids_two_live_offers(self):
        """The live-offer invariant is a partial unique index, not a convention — so a
        caller that forgets to supersede cannot leave two live offers behind."""
        from sqlalchemy.exc import IntegrityError
        from app.services.offer_lifecycle import OFFER_CONTRACT_VERSION, token_fingerprint
        self._issue()
        db = self.SF()
        with self.assertRaises(IntegrityError):
            db.execute(text(
                "INSERT INTO whatsapp_booking_offers (thread_id, cycle_key, "
                "contract_version, token_fingerprint, status, issued_at) "
                "VALUES (:t, 0, :v, :fp, 'ISSUED', now())"),
                {"t": self.thread_id, "v": OFFER_CONTRACT_VERSION,
                 "fp": token_fingerprint("a-second-live-offer")})
            db.flush()
        db.rollback(); db.close()

    def test_g7p_pg_19_one_token_can_only_ever_be_one_offer(self):
        from sqlalchemy.exc import IntegrityError
        from app.services.offer_lifecycle import OFFER_CONTRACT_VERSION, token_fingerprint
        token = self._issue()
        db = self.SF()
        with self.assertRaises(IntegrityError):
            db.execute(text(
                "INSERT INTO whatsapp_booking_offers (thread_id, cycle_key, "
                "contract_version, token_fingerprint, status, issued_at, terminal_at, "
                "terminal_reason) VALUES (:t, 0, :v, :fp, 'WITHDRAWN', now(), now(), 'X')"),
                {"t": self.thread_id, "v": OFFER_CONTRACT_VERSION,
                 "fp": token_fingerprint(token)})
            db.flush()
        db.rollback(); db.close()

    # ── 7. trace truth and F7A ───────────────────────────────────────────────

    def test_g7p_pg_20_trace_tells_the_truth_at_every_lifecycle_point(self):
        from app.services.hybrid_trace import snapshot_state
        from app.services.offer_lifecycle import OfferLifecycle, WithdrawReason

        def snap_now():
            db = self.SF()
            st = db.execute(text("SELECT * FROM whatsapp_thread_states WHERE thread_id=:t"),
                            {"t": self.thread_id}).one()
            state = db.query(self.M.WhatsAppThreadState).filter_by(
                thread_id=self.thread_id).one()
            lead = db.get(self.M.Lead, self.lead_id)
            offer = OfferLifecycle(db).latest_offer(self.thread_id)
            out = snapshot_state(state, lead, offer=offer)
            db.close()
            return out

        token = self._issue()
        issued = snap_now()
        self.assertEqual(issued.offer_status, "ISSUED")
        self.assertIs(issued.offer_outstanding, True)
        self.assertTrue(issued.booking_token_fingerprint)
        self.assertNotIn(token, str(issued))          # never the raw token

        db, eng = self._engine()
        ctx = eng._load_context(self.thread_id)
        eng._withdraw_booking_offer(ctx, ctx.state, reason=WithdrawReason.LOCATION_CHANGED)
        db.commit(); db.close()
        withdrawn = snap_now()
        self.assertEqual(withdrawn.offer_status, "WITHDRAWN")
        self.assertEqual(withdrawn.offer_terminal_reason, "LOCATION_CHANGED")
        self.assertIs(withdrawn.offer_outstanding, False)
        self.assertIs(withdrawn.booking_token_present, False)

        self._submit(token, tag="traceref")
        refused = snap_now()
        self.assertIs(refused.offer_outstanding, False)
        self.assertEqual(self._offers()[0][7], "OFFER_WITHDRAWN")   # durable classification

        self.setUp()
        token2 = self._issue()
        self._submit(token2, tag="traceacc")
        accepted = snap_now()
        self.assertEqual(accepted.offer_status, "ACCEPTED")
        self.assertIs(accepted.offer_outstanding, False)

    def test_g7p_pg_21_f7a_is_preserved_after_a_withdrawal(self):
        """G3-7N's tombstone was truthy, and the deterministic scheduling block is gated on
        `not state.flow_booking_token` — the very gate L4.7W5-F7A exists to keep open. A
        withdrawn offer must leave that gate open and the customer able to continue."""
        from app.services import pending_correction as pc
        from app.services.offer_lifecycle import WithdrawReason
        self._issue()
        db, eng = self._engine()
        ctx = eng._load_context(self.thread_id)
        self.assertTrue(eng._offer_outstanding(ctx.state))           # live offer, as issued
        eng._withdraw_booking_offer(ctx, ctx.state, reason=WithdrawReason.OFFER_REJECTED,
                                   clear_scheduling=False)
        db.commit()
        state = ctx.state
        # THE F7A GATE. The deterministic scheduling block is gated on
        # `not state.flow_booking_token`; G3-7N's truthy tombstone closed it. It is open.
        self.assertIsNone(state.flow_booking_token)
        self.assertTrue(not state.flow_booking_token)
        # the withdrawn offer contributes NOTHING to any outstanding-proposal reader
        self.assertFalse(pc.proposal_is_active(state, "flow_booking_token"))
        self.assertNotIn("flow_booking_token", [f for f, _ in pc.active_proposals(state)])
        # F7A deliberately preserves what the customer told us about availability, so
        # `_offer_outstanding` stays True — but on the SLOTS, not on the dead offer. Proven
        # by clearing only the slots and watching it go false while the offer is unchanged.
        self.assertEqual(state.preferred_day, "2026-11-10")
        self.assertTrue(eng._offer_outstanding(state))
        state.active_requested_date = None
        state.last_offered_slots = None
        state.last_visible_slots = None
        self.assertFalse(eng._offer_outstanding(state),
                         "with the slots gone, a withdrawn offer must not read as live")
        db.rollback()
        db.close()

    def test_g7p_pg_22_a_vehicle_change_leaves_nothing_outstanding(self):
        """The other half of §7: when the withdrawal DOES clear the scheduling facts, every
        reader agrees there is no outstanding offer."""
        from app.services import pending_correction as pc
        from app.services.offer_lifecycle import WithdrawReason
        self._issue()
        db, eng = self._engine()
        ctx = eng._load_context(self.thread_id)
        eng._withdraw_booking_offer(ctx, ctx.state, reason=WithdrawReason.VEHICLE_CHANGED)
        db.commit()
        state = ctx.state
        self.assertIsNone(state.flow_booking_token)
        self.assertFalse(eng._offer_outstanding(state))
        self.assertFalse(pc.proposal_is_active(state, "flow_booking_token"))
        self.assertEqual([f for f, _ in pc.active_proposals(state)], [])
        db.close()




@unittest.skipUnless(PG_URL, "G7P_PG_URL not set — no disposable PostgreSQL; nothing proven")
class G7PR2BindingCompleteness(G7POfferAuthorityPG):
    """R2 — the bindings the offer RECORDS are the bindings it ENFORCES.

    The G3-7P review proved that storing a binding and not comparing it is a false
    assurance. With the candidate's category changed in place from AUTO to PICKUP — same
    candidate id, same zone, offer still ISSUED — the submission was ACCEPTED and produced
    a CRM Revision carrying `tipo_vehiculo='PICKUP'` with `precio_total=NULL`: an unpriced
    booking, from an offer issued for a different vehicle class. `cycle_key` had the same
    shape of hole.
    """

    # ── §3/§4 the pricing-context binding ────────────────────────────────────

    def _change_category(self, new="PICKUP"):
        db = self.SF()
        db.execute(text("UPDATE whatsapp_thread_candidates SET tipo_vehiculo=:v WHERE id=:c"),
                   {"v": new, "c": self.cand_a})
        db.commit(); db.close()

    def test_g7p_r2_01_a_category_change_refuses_the_offer(self):
        token = self._issue()
        self.assertEqual([o[1] for o in self._offers()], ["ISSUED"])
        self._change_category("PICKUP")
        out = self._submit(token, tag="cat")
        self.assertNotEqual(out.action, "booking_created")
        self.assertEqual(self._counts(), (0, 0), "a moved pricing basis must not book")
        offer = self._offers()[0]
        self.assertEqual(offer[1], "WITHDRAWN")
        self.assertEqual(offer[2], "OFFER_VEHICLE_CATEGORY_CHANGED")
        self.assertEqual(offer[7], "OFFER_VEHICLE_CATEGORY_CHANGED")
        self.assertIsNone(self._state()["token"], "the stale offer's mirror is cleared")

    def test_g7p_r2_02_no_unpriced_booking_survives_a_category_change(self):
        """The audit's exact artifact: a Revision with the NEW category and NULL price."""
        token = self._issue()
        self._change_category("PICKUP")
        self._submit(token, tag="unpriced")
        db = self.SF()
        rows = db.execute(text("SELECT tipo_vehiculo, precio_total FROM revisions")).fetchall()
        db.close()
        self.assertEqual([tuple(r) for r in rows], [],
                         "no Revision at all may be created — priced or not")

    def test_g7p_r2_03_an_unchanged_category_still_books_exactly_once(self):
        token = self._issue()
        out = self._submit(token, tag="same")
        self.assertEqual(out.action, "booking_created")
        self.assertEqual(self._counts(), (1, 1))
        self.assertEqual([o[1] for o in self._offers()], ["ACCEPTED"])

    def test_g7p_r2_04_the_refusal_holds_in_both_flag_postures(self):
        """The deployed posture has both correction flags UNSET, which is why the
        text-path withdrawal does not run and the offer stays live while the category
        moves. The binding must refuse in either posture."""
        from dataclasses import replace as _replace
        from app.settings import get_settings
        for grounding, provisional in ((False, False), (True, True)):
            with self.subTest(grounding=grounding, provisional=provisional):
                self.setUp()
                token = self._issue()
                self._change_category("PICKUP")
                self._clear_human()
                db, eng = self._engine()
                eng.settings = _replace(
                    get_settings(),
                    correction_target_grounding_enabled=grounding,
                    provisional_correction_enabled=provisional)
                from app.schemas.conversation import ConversationHandleIn
                out = eng.handle(ConversationHandleIn(
                    thread_id=self.thread_id, wa_message_id=f"wamid.fl.{_t.time()}",
                    wa_id=WA, message_type="flow_response",
                    flow_response=dict(BOOKING_PAYLOAD), flow_token=token))
                db.close()
                self.assertNotEqual(out.action, "booking_created")
                self.assertEqual(self._counts(), (0, 0))

    def test_g7p_r2_05_an_unresolvable_category_refuses(self):
        """A category we cannot resolve cannot be priced."""
        token = self._issue()
        db = self.SF()
        db.execute(text("UPDATE whatsapp_thread_candidates SET tipo_vehiculo=NULL WHERE id=:c"),
                   {"c": self.cand_a})
        db.commit(); db.close()
        out = self._submit(token, tag="nocat")
        self.assertNotEqual(out.action, "booking_created")
        self.assertEqual(self._counts(), (0, 0))
        self.assertEqual(self._offers()[0][7], "OFFER_VEHICLE_CATEGORY_CHANGED")

    def test_g7p_r2_06_the_data_exchange_writer_refuses_a_category_change_too(self):
        """Both booking writers share the authority, so both must refuse."""
        from app.services.booking_flow_service import BookingFlowService, BookingTokenError
        token = self._issue()
        self._change_category("PICKUP")
        db = self.SF()
        svc = BookingFlowService(db)
        svc._send_booking_receipt = lambda *a, **k: None
        with self.assertRaises(BookingTokenError) as caught:
            svc.handle_confirm_booking(token, {
                "date": "2026-11-10", "time": "10:00", "name": "P C", "email": "p@e.t",
                "inspection_address": "Palermo, CABA"})
        db.close()
        self.assertIn("OFFER_VEHICLE_CATEGORY_CHANGED", str(caught.exception))
        self.assertEqual(self._counts(), (0, 0))

    # ── §5 the cycle binding ─────────────────────────────────────────────────

    def test_g7p_r2_07_a_closed_cycle_refuses_even_without_the_reset(self):
        """The ordinary cycle reset withdraws correctly; this is the adversarial case where
        the cycle moves WITHOUT it. `cycle_key` is declared a binding, so it is compared."""
        token = self._issue()
        db = self.SF()
        db.execute(text("UPDATE whatsapp_thread_states SET "
                        "current_cycle_start_message_db_id=99999 WHERE thread_id=:t"),
                   {"t": self.thread_id})
        db.commit(); db.close()
        out = self._submit(token, tag="cycle")
        self.assertNotEqual(out.action, "booking_created")
        self.assertEqual(self._counts(), (0, 0))
        offer = self._offers()[0]
        self.assertEqual((offer[1], offer[2]), ("WITHDRAWN", "OFFER_CYCLE_CHANGED"))
        self.assertIsNone(self._state()["token"])

    # ── §6/§7 the dispatch-failure lifecycle ─────────────────────────────────

    def _issue_failing(self, exc):
        db, eng = self._engine()
        eng._send_flow_button = lambda *a, **k: (_ for _ in ()).throw(exc)
        ctx = eng._load_context(self.thread_id)
        ctx.state.needs_human = False
        db.commit()
        raised = None
        try:
            eng._send_booking_flow(ctx, ctx.state,
                                   booking_flow_id="28104222025943520")
        except Exception as e:                   # OutboundBlockedError propagates by design
            raised = type(e).__name__
        db.refresh(ctx.state)
        token = ctx.state.flow_booking_token
        db.close()
        return token, raised

    def _assert_no_phantom(self):
        """The R2 invariant: a truthy mirror only when ONE ISSUED offer exists."""
        from app.services import pending_correction as pc
        st = self._state()
        live = [o for o in self._offers() if o[1] == "ISSUED"]
        self.assertEqual(live, [], "no offer may be live after a failed dispatch")
        self.assertIsNone(st["token"], "no phantom mirror")
        db, eng = self._engine()
        state = eng._load_context(self.thread_id).state
        self.assertTrue(not state.flow_booking_token, "the F7A gate must stay open")
        self.assertFalse(pc.proposal_is_active(state, "flow_booking_token"))
        self.assertNotIn("flow_booking_token",
                         [f for f, _ in pc.active_proposals(state)])
        db.close()

    def test_g7p_r2_08_dispatch_failure_leaves_no_phantom_offer(self):
        """Every failure branch, with and without a previous live offer."""
        from app.services.outbound_guard import OutboundBlockedError
        branches = [
            ("transport exception", RuntimeError("transport exploded")),
            ("outbound blocked before transport",
             OutboundBlockedError(sender_path="x", kind="flow", to_wa_id="0")),
        ]
        for label, exc in branches:
            for previous in (False, True):
                with self.subTest(branch=label, previous_offer=previous):
                    self.setUp()
                    old_token = self._issue() if previous else None
                    failed_token, _raised = self._issue_failing(exc)
                    self._assert_no_phantom()
                    # neither token may ever book
                    for tok in [t for t in (old_token, failed_token) if t]:
                        self._submit(tok, tag="dead")
                        self.assertEqual(self._counts(), (0, 0))
                    # and a subsequent valid offer is still issuable
                    db = self.SF()
                    db.execute(text("UPDATE whatsapp_thread_states SET needs_human=false "
                                    "WHERE thread_id=:t"), {"t": self.thread_id})
                    db.commit(); db.close()
                    self.assertTrue(self._issue(), "a new offer must still be issuable")

    def test_g7p_r2_09_a_superseded_token_is_never_restored_to_the_mirror(self):
        """The exact A1 defect: the previous offer's token coming back after its successor
        failed to dispatch."""
        old_token = self._issue()
        self._issue_failing(RuntimeError("boom"))
        self.assertIsNone(self._state()["token"])
        self.assertNotEqual(self._state()["token"], old_token)
        statuses = [(o[1], o[2]) for o in self._offers()]
        self.assertEqual(statuses, [("SUPERSEDED", "REPLACED_BY_NEW_OFFER"),
                                    ("WITHDRAWN", "DISPATCH_FAILED")])

    def test_g7p_r2_10_a_motorcycle_mirror_is_a_different_family(self):
        """The moto handoff legitimately shares the column and is not a booking offer, so
        it alone survives a failed booking dispatch."""
        from app.services.conversation_engine import _MOTO_HANDOFF_TOKEN_PREFIX
        moto = f"{_MOTO_HANDOFF_TOKEN_PREFIX}{self.thread_id}-1790000000"
        db = self.SF()
        db.execute(text("UPDATE whatsapp_thread_states SET flow_booking_token=:k, "
                        "needs_human=false WHERE thread_id=:t"),
                   {"k": moto, "t": self.thread_id})
        db.commit(); db.close()
        self._issue_failing(RuntimeError("boom"))
        self.assertEqual(self._state()["token"], moto,
                         "a pending motorcycle Flow must not be destroyed by a failed "
                         "booking dispatch")
        self.assertEqual([o[1] for o in self._offers()], ["WITHDRAWN"])

    # ── §8 the summary screen never consumes ─────────────────────────────────

    def _svc(self, db):
        from app.services.booking_flow_service import BookingFlowService
        svc = BookingFlowService(db)
        svc._send_booking_receipt = lambda *a, **k: None
        return svc

    def _offer_full(self):
        db = self.SF()
        row = db.execute(text(
            "SELECT status, accepted_at, accepted_revision_id, accepted_thread_revision_id "
            "FROM whatsapp_booking_offers WHERE thread_id=:t ORDER BY id DESC LIMIT 1"),
            {"t": self.thread_id}).one()
        db.close()
        return tuple(row)

    def _assert_not_consumed(self, where, session=None):
        """Nothing before confirm_booking may claim the offer.

        `session` matters: a claim that is not committed is rolled back when the session
        closes, so reading only COMMITTED state cannot see it. Checking through the
        handler's OWN session catches a claim the moment it is made, which is what makes
        "the summary screen claimed the offer" a detectable defect rather than an invisible
        one.
        """
        if session is not None:
            row = session.execute(text(
                "SELECT status, accepted_at FROM whatsapp_booking_offers "
                "WHERE thread_id=:t ORDER BY id DESC LIMIT 1"),
                {"t": self.thread_id}).one()
            self.assertEqual(row[0], "ISSUED",
                             f"{where}: the offer was claimed inside this session")
            self.assertIsNone(row[1], f"{where}: accepted_at was set in this session")
        status, accepted_at, rev, tr = self._offer_full()
        self.assertEqual(status, "ISSUED", f"{where}: the offer must still be ISSUED")
        self.assertIsNone(accepted_at, f"{where}: accepted_at must be null")
        self.assertIsNone(rev, f"{where}: accepted Revision id must be null")
        self.assertIsNone(tr, f"{where}: accepted ThreadRevision id must be null")
        self.assertIsNotNone(self._state()["token"], f"{where}: the mirror must stay valid")
        self.assertEqual(self._counts(), (0, 0), f"{where}: zero booking revisions")

    def test_g7p_r2_11_only_final_confirmation_consumes_the_offer(self):
        """The whole Data Exchange sequence. Nothing before confirm_booking may claim."""
        db = self.SF()
        db.execute(text("UPDATE whatsapp_thread_states SET preferred_day='2026-11-10', "
                        "preferred_time='10:00' WHERE thread_id=:t"), {"t": self.thread_id})
        db.commit(); db.close()
        token = self._issue()
        self._assert_not_consumed("after issue")

        payload = {"date": "2026-11-10", "time": "10:00", "name": "P C",
                   "email": "p@e.t", "inspection_address": "Palermo, CABA"}

        db = self.SF(); svc = self._svc(db)
        svc.handle_init(token)
        self._assert_not_consumed("after INIT", session=db)

        svc.handle_date_selected(token, "2026-11-10")
        self._assert_not_consumed("after date_selected", session=db)

        svc.handle_prepare_summary(token, payload)
        self._assert_not_consumed("after prepare_summary", session=db)

        svc.handle_prepare_summary(token, payload)          # repeated
        self._assert_not_consumed("after a repeated prepare_summary", session=db)

        with self.assertRaises(ValueError):
            svc.handle_prepare_summary(token, {"date": "not-a-date", "time": "xx"})
        db.rollback()                 # the failed call left the session in a dead txn
        self._assert_not_consumed("after an invalid prepare_summary", session=db)
        db.close()

        # abandoned: a brand-new session, as if the customer closed the Flow and came back
        db = self.SF(); svc = self._svc(db)
        svc.handle_init(token)
        self._assert_not_consumed("after abandoning and re-opening")
        db.close()

    def test_g7p_r2_12_confirmation_is_what_claims_and_consumes(self):
        db = self.SF()
        db.execute(text("UPDATE whatsapp_thread_states SET preferred_day='2026-11-10', "
                        "preferred_time='10:00' WHERE thread_id=:t"), {"t": self.thread_id})
        db.commit(); db.close()
        token = self._issue()
        db = self.SF(); svc = self._svc(db)
        try:
            svc.handle_confirm_booking(token, {
                "date": "2026-11-10", "time": "10:00", "name": "P C", "email": "p@e.t",
                "inspection_address": "Palermo, CABA"})
            booked = True
        except Exception:
            booked = False                   # the slot may be unavailable in this fixture
        db.close()
        status, accepted_at, rev, tr = self._offer_full()
        if booked:
            self.assertEqual(status, "ACCEPTED")
            self.assertIsNotNone(accepted_at)
            self.assertIsNotNone(rev)
            self.assertIsNotNone(tr)
            self.assertEqual(self._counts(), (1, 1))
        else:
            # a refusal must leave the offer exactly as it was — never half-accepted
            self.assertEqual(status, "ISSUED")
            self.assertIsNone(accepted_at)
            self.assertEqual(self._counts(), (0, 0))


if __name__ == "__main__":                       # pragma: no cover
    unittest.main()
