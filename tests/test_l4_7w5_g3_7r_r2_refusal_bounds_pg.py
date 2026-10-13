"""G3-7R-R2 — EVERY booking-shaped turn answers inside its bound (R1–R9), on real PostgreSQL.

Disposable database only (`G37R_PG_URL`); skips cleanly without it and says so, because a
bound that was never measured is not a weaker claim, it is no claim at all.

WHY THIS FILE EXISTS, STATED AS A MEASUREMENT. G3-7R deleted the turn-entry lock and gave
the offer authority one canonical, bounded acquisition order. Its own suite passed, its PG
suite passed 132 tests, and the sealed regression was identity-identical. The independent
review then held `whatsapp_thread_states` for twelve seconds and submitted a Flow response
whose offer had been withdrawn — the normal outcome of a customer correcting their vehicle
or their address — and measured the turn at **42 seconds and still waiting**, where the
PARENT commit answered `thread_contended` in 4.06s. This milestone measured it again at
**72 seconds** against the same hold, and found two more cases the review never tried:

    before this fix          unknown token 72.0s   withdrawn 72.0s   superseded 72.0s
                             granted        4.0s   replay    72.0s
    cursor consumed anyway   yes            yes     yes              yes

The granted path was bounded only because `SHOW lock_timeout` happened not to autoflush the
caller's pending dedup-cursor write; nothing in the code said so. The refusal and replay
paths reached their first write — a counter UPDATE — with no bound in scope at all, because
they returned before `begin_offer_transaction()` was ever called.

So this file does not test the helper. It holds resource 1 for real and drives the REAL
`ConversationEngine.handle()` and the REAL Data Exchange command through every booking-shaped
submission class, and asserts the whole contention contract on persisted STATE: bounded
elapsed time, a typed outcome, cursor NOT consumed, offer NOT mutated, nothing booked,
nothing sent — and the turn still works once the contention clears.

    A path that cannot answer is not safe merely because it cannot answer wrongly.
"""
from __future__ import annotations

import os
import sys
import threading
import time as _t
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

PG_URL = os.environ.get("G37R_PG_URL", "")

WA = "549110000781"
BOOKING_PAYLOAD = {
    "nombre_apellido": "Bound Probe", "email": "b@example.test", "telefono": WA,
    "como_llego": "Instagram", "direccion": "Palermo 123", "tipo_vendedor": "particular",
}
BOOKING_FLOW_ID = "28104222025943520"

#: How long the competing writer holds resource 1. Three times the declared bound, so a turn
#: that waits for the holder and a turn that respects its bound cannot be confused.
HOLD_SECONDS = 12.0

#: `DEFAULT_OFFER_LOCK_TIMEOUT_MS` is 4000. The allowance covers scheduler reads and the
#: engine's own work either side of the acquisition; it is far below HOLD_SECONDS, which is
#: what makes the two outcomes distinguishable.
BOUND_ALLOWANCE_S = 8.0


@unittest.skipUnless(PG_URL, "G37R_PG_URL not set — no disposable PostgreSQL; nothing proven")
class G37RR2RefusalBoundsPG(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_URL, pool_size=16, max_overflow=8)
        cls.SF = sessionmaker(bind=cls.engine, expire_on_commit=False)
        with cls.engine.begin() as c:
            assert c.execute(text("SELECT to_regclass('whatsapp_booking_offers')")).scalar()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    # ── fixture ──────────────────────────────────────────────────────────────

    def setUp(self):
        import app.models as M
        self.M = M
        # Release pooled connections and terminate strays before truncating: these tests
        # deliberately hold resource 1 in worker threads, and a worker that outlived its
        # join would block the next test's writes with no diagnosis. See the G3-7R PG
        # suite's fixture for the measured stall this prevents.
        self.engine.dispose()
        try:
            with self.engine.begin() as c:
                c.execute(text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid()"))
        except Exception:                           # pragma: no cover - defensive
            pass
        self.engine.dispose()
        with self.engine.begin() as c:
            for t in ("whatsapp_booking_offers", "whatsapp_clarification_attempts",
                      "whatsapp_messages", "thread_revisions", "whatsapp_thread_candidates",
                      "whatsapp_thread_states", "revisions", "whatsapp_threads", "leads",
                      "whatsapp_contacts"):
                c.execute(text(f"TRUNCATE {t} RESTART IDENTITY CASCADE"))
        db = self.SF()
        ct = M.WhatsAppContact(wa_id=WA, display_name="Bound"); db.add(ct); db.flush()
        lead = M.Lead(nombre="Bound", apellido="Probe", telefono=WA,
                      estado="CONSULTA_NUEVA", flag="ACEPTADO", necesita_humano=False)
        db.add(lead); db.flush()
        th = M.WhatsAppThread(contact_id=ct.id, lead_id=lead.id); db.add(th); db.flush()
        st = M.WhatsAppThreadState(thread_id=th.id); db.add(st); db.flush()
        cand = M.WhatsAppThreadCandidate(
            thread_id=th.id, marca="Ford", modelo="Focus", anio=2017, tipo_vehiculo="AUTO",
            status="current_focus", source_text="un ford focus 2017")
        db.add(cand); db.flush()
        st.current_focus_candidate_id = cand.id
        st.home_zone_group, st.home_zone_detail = "CABA", "Palermo"
        st.last_stage = "SCHEDULING"
        db.commit()
        self.thread_id, self.lead_id, self.cand_a = th.id, lead.id, cand.id
        db.close()
        self.sends = []
        day, hhmm = self._open_slots(1)[0]
        self._arm(day, hhmm)

    # ── the real engine, with ONLY the external transport stubbed ────────────

    def _engine(self, db=None):
        from app.services.conversation_engine import ConversationEngine
        from app.settings import get_settings
        db = db or self.SF()
        eng = ConversationEngine(db=db, settings=get_settings())
        eng._send_text_to_wa = lambda ctx, t: (
            self.sends.append(("text", (t or "")[:60])), "wamid.text")[1]
        eng._send_flow_button = lambda ctx, body, token, **kw: (
            self.sends.append(("flow", "")), "wamid.flow")[1]
        eng._send_fallback_human_review_notification = lambda *a, **k: self.sends.append(
            ("notify", ""))
        eng._send_booking_notification = lambda *a, **k: self.sends.append(("email", ""))
        return db, eng

    def _open_slots(self, how_many=2):
        from app.schemas.schedule import ScheduleCheckIn
        from app.services.schedule import ScheduleService
        db = self.SF()
        found = []
        try:
            svc = ScheduleService(db)
            for offset in range(2, 30):
                day = date.today() + timedelta(days=offset)
                out = svc.list_slots(ScheduleCheckIn(
                    address="Palermo, CABA", preferred_day=day, preferred_time=time(11, 0),
                    zone_group="CABA", zone_detail="Palermo"))
                if out.slots:
                    found.append((day.isoformat(), str(out.slots[0])[:5]))
                if len(found) >= how_many:
                    return found
        finally:
            db.close()
        self.fail(f"ScheduleService offered fewer than {how_many} open slots in 30 days")

    def _arm(self, day_iso, hhmm):
        db = self.SF()
        db.execute(text("UPDATE whatsapp_thread_states SET preferred_day=:d, "
                        "preferred_time=:t, active_requested_date=:d, "
                        "last_requested_time=:t, needs_human=false WHERE thread_id=:th"),
                   {"d": day_iso, "t": hhmm, "th": self.thread_id})
        db.commit(); db.close()

    def _issue(self):
        db, eng = self._engine()
        ctx = eng._load_context(self.thread_id)
        ctx.state.needs_human = False
        db.commit()
        eng._send_booking_flow(ctx, ctx.state, booking_flow_id=BOOKING_FLOW_ID)
        db.refresh(ctx.state)
        token = ctx.state.flow_booking_token
        db.close()
        self.assertTrue(token, "the dispatch path issued no offer — nothing to prove")
        return token

    def _seed_offer(self, status, reason):
        """A terminal offer whose token is real, so admission REFUSES rather than not finding."""
        from app.services.offer_lifecycle import token_fingerprint
        tok = "bk-%d-r2%s" % (self.thread_id, status.lower())
        now = datetime.now(timezone.utc)
        db = self.SF()
        db.add(self.M.WhatsAppBookingOffer(
            thread_id=self.thread_id, cycle_key=0, candidate_id=self.cand_a,
            tipo_vehiculo="AUTO", zone_group="CABA", zone_detail="Palermo",
            contract_version="offer/1.0", token_fingerprint=token_fingerprint(tok),
            status=status, issued_at=now, terminal_at=now, terminal_reason=reason))
        db.commit(); db.close()
        return tok

    # ── observations, all on persisted state ─────────────────────────────────

    def _snapshot(self):
        db = self.SF()
        s = {
            "cursor": str(db.execute(text(
                "SELECT coalesce(last_processed_inbound_wa_message_id,'<none>') "
                "FROM whatsapp_thread_states WHERE thread_id=:t"),
                {"t": self.thread_id}).scalar()),
            "needs_human": bool(db.execute(text(
                "SELECT needs_human FROM whatsapp_thread_states WHERE thread_id=:t"),
                {"t": self.thread_id}).scalar()),
            "offers": [tuple(map(str, r)) for r in db.execute(text(
                "SELECT id, status, coalesce(terminal_reason,''), refused_count, "
                "coalesce(last_refusal,''), replay_count FROM whatsapp_booking_offers "
                "ORDER BY id"))],
            "bookings": int(db.execute(text(
                "SELECT count(*) FROM thread_revisions WHERE status='booked'")).scalar()),
            "revisions": int(db.execute(text("SELECT count(*) FROM revisions")).scalar()),
            "out_rows": int(db.execute(text(
                "SELECT count(*) FROM whatsapp_messages WHERE direction='out'")).scalar()),
        }
        db.close()
        return s

    # ── the competing writer ─────────────────────────────────────────────────

    def _hold_resource_1(self):
        """Hold `whatsapp_thread_states FOR NO KEY UPDATE` — exactly what resource 1 is."""
        holding, release = threading.Event(), threading.Event()

        def holder():
            db = self.SF()
            try:
                db.execute(text("SELECT 1 FROM whatsapp_thread_states WHERE thread_id=:t "
                                "FOR NO KEY UPDATE"), {"t": self.thread_id}).fetchall()
                holding.set()
                release.wait(timeout=HOLD_SECONDS + 60)
            finally:
                db.rollback(); db.close()

        t = threading.Thread(target=holder, name="R2-HOLDER", daemon=True)
        t.start()
        self.assertTrue(holding.wait(timeout=30),
                        "the competing writer never acquired resource 1 — nothing held")
        return t, release

    def _submit(self, token, tag):
        """One delivery in its OWN session, which is the shape of one n8n HTTP call."""
        from app.schemas.conversation import ConversationHandleIn
        db, eng = self._engine()
        started = _t.perf_counter()
        try:
            out = eng.handle(ConversationHandleIn(
                thread_id=self.thread_id, wa_message_id="wamid.%s.%s" % (tag, _t.time()),
                wa_id=WA, message_type="flow_response",
                flow_response=dict(BOOKING_PAYLOAD), flow_token=token))
            action, detail = out.action, (getattr(out, "detail", "") or "")
        except Exception as exc:                    # a raised exception is also an answer
            action, detail = "EXCEPTION:%s" % type(exc).__name__, str(exc)[:120]
        finally:
            elapsed = _t.perf_counter() - started
            db.close()
        return action, detail, elapsed

    def _assert_contention_contract(self, case, token, *, expect_bookings=0):
        """The whole contract for one submission class, not just its timing."""
        from app.services.conversation_engine import ACTION_THREAD_CONTENDED
        before = self._snapshot()
        self.sends.clear()
        holder, release = self._hold_resource_1()
        try:
            action, detail, elapsed = self._submit(token, case)
            during = self._snapshot()
            sent_during = list(self.sends)
        finally:
            release.set(); holder.join(timeout=30)

        # 1. BOUNDED. The measurement that failed the review.
        self.assertLess(elapsed, BOUND_ALLOWANCE_S,
                        f"{case}: waited {elapsed:.3f}s against a {HOLD_SECONDS}s hold — "
                        f"the bound is not in force on this path")
        # 2. TYPED. Contention is its own outcome, never a refusal and never an error.
        self.assertEqual(action, ACTION_THREAD_CONTENDED,
                         f"{case}: answered {action!r}/{detail!r}")
        # 3. The dedup cursor is NOT consumed, so a redelivery may try again.
        self.assertEqual(during["cursor"], before["cursor"],
                         f"{case}: the cursor was consumed by a turn that did not complete")
        # 4. The offer is untouched — no counter moved, no status changed.
        self.assertEqual(during["offers"], before["offers"],
                         f"{case}: contention mutated the offer")
        # 5. Nothing was booked, and no canonical revision was written.
        self.assertEqual(during["bookings"], expect_bookings, case)
        self.assertEqual(during["revisions"], before["revisions"], case)
        # 6. No handoff was declared for a submission nobody judged.
        self.assertEqual(during["needs_human"], before["needs_human"],
                         f"{case}: needs_human was written by a contended turn")
        # 7. Nothing was sent, and no outbound ledger row was created.
        self.assertEqual(sent_during, [], f"{case}: sent {sent_during} while contended")
        self.assertEqual(during["out_rows"], before["out_rows"], case)
        return elapsed

    # ══ R1–R5: every booking-shaped submission class ════════════════════════
    # MUTATION ORACLES for required mutations 1–5.

    def test_r2_pg_01_an_unknown_token_answers_inside_the_bound(self):
        self._assert_contention_contract("unknown", "bk-%d-%s" % (self.thread_id, "f" * 16))

    def test_r2_pg_02_a_withdrawn_offer_answers_inside_the_bound(self):
        """The review's case, and the COMMON one: withdrawal follows any correction."""
        from app.services.offer_lifecycle import OfferStatus
        tok = self._seed_offer(OfferStatus.WITHDRAWN, "TEXT_CORRECTION")
        self._assert_contention_contract("withdrawn", tok)

    def test_r2_pg_03_a_superseded_offer_answers_inside_the_bound(self):
        from app.services.offer_lifecycle import OfferStatus
        tok = self._seed_offer(OfferStatus.SUPERSEDED, "REPLACED_BY_NEW_OFFER")
        self._assert_contention_contract("superseded", tok)

    def test_r2_pg_04_a_granted_acceptance_answers_inside_the_bound(self):
        """Bounded in G3-7R too — but only because `SHOW` happened not to autoflush.

        This is the REGRESSION guard for that accident: the ordering is now structural, and
        a change that lets the caller's pending writes escape the bound fails here as well
        as on the refusal paths.
        """
        self._assert_contention_contract("granted", self._issue())

    def test_r2_pg_05_a_replay_answers_inside_the_bound(self):
        """A redelivered completion whose booking already committed. Measured at 72s."""
        token = self._issue()
        action, _d, _e = self._submit(token, "prebook")
        self.assertEqual(action, "booking_created", "the first submission must book")
        db = self.SF()
        db.execute(text("UPDATE whatsapp_thread_states SET needs_human=false "
                        "WHERE thread_id=:t"), {"t": self.thread_id})
        db.commit(); db.close()
        self._assert_contention_contract("replay", token, expect_bookings=1)

    # ══ R6: the turn is declined, not lost ══════════════════════════════════

    def test_r2_pg_06_the_same_submission_succeeds_once_the_contention_clears(self):
        """A bounded decline is only correct if the work is still possible afterwards.

        Otherwise the fix would trade an unbounded wait for a silently dropped booking —
        which is the trade the review's R3 findings rejected on the live path.
        """
        token = self._issue()
        self._assert_contention_contract("retryable", token)
        db = self.SF()
        db.execute(text("UPDATE whatsapp_thread_states SET needs_human=false "
                        "WHERE thread_id=:t"), {"t": self.thread_id})
        db.commit(); db.close()
        action, detail, elapsed = self._submit(token, "afterrelease")
        self.assertEqual(action, "booking_created",
                         f"the retry answered {action!r}/{detail!r} — the turn was lost")
        self.assertEqual(len([b for b in self._snapshot()["offers"]]), 1)
        self.assertEqual(self._snapshot()["bookings"], 1,
                         "exactly one booking, written by the retry")

    # ══ R7: EVERY declared writer takes resource 1 ══════════════════════════

    def test_r2_pg_07_every_declared_offer_writer_acquires_resource_1(self):
        """MUTATION ORACLE for required mutations 6 and 7 (RV-02).

        Behavioural, not textual: resource 1 is held, each declared writer is called for
        real, and a writer that enters the order either refuses or raises inside the bound.
        A writer that does NOT take it would simply succeed — which is exactly what
        `record_replay`, `_refuse`, `record_refusal` and `record_refusal_out_of_band` did
        when the review inventoried them, while the authority's docstring claimed otherwise.
        """
        from app.services.offer_lifecycle import (OFFER_WRITERS, OfferLifecycle,
                                                  OfferProtocolContended)
        self.assertEqual(len(OFFER_WRITERS), 10,
                         "the declared writer list changed — re-derive this test from it")
        token = self._issue()
        offer_id = int(self._snapshot()["offers"][0][0])
        checked = {}
        for name in ("record_replay", "record_refusal", "record_refusal_out_of_band"):
            holder, release = self._hold_resource_1()
            db = self.SF()
            lc = OfferLifecycle(db)
            started = _t.perf_counter()
            try:
                if name == "record_replay":
                    lc.record_replay(offer_id, thread_id=self.thread_id)
                    outcome = "completed"
                elif name == "record_refusal":
                    lc.record_refusal(offer_id, "PROBE", thread_id=self.thread_id)
                    outcome = "completed"
                else:
                    outcome = "returned:%s" % lc.record_refusal_out_of_band(token, "PROBE")
            except OfferProtocolContended as exc:
                outcome = "contended:%s" % exc.refusal
            except Exception as exc:
                outcome = "EXC:%s" % type(exc).__name__
            elapsed = _t.perf_counter() - started
            try:
                db.rollback()
            except Exception:
                pass
            db.close()
            release.set(); holder.join(timeout=30)
            checked[name] = (outcome, elapsed)

        for name, (outcome, elapsed) in checked.items():
            with self.subTest(writer=name):
                self.assertLess(elapsed, BOUND_ALLOWANCE_S,
                                f"{name} waited {elapsed:.3f}s — it is outside the bound")
                self.assertNotEqual(outcome, "completed",
                                    f"{name} wrote the offer table while resource 1 was "
                                    f"held by another writer — it never took resource 1")
        # `record_refusal_out_of_band` reports its refusal rather than raising, and must
        # have written nothing.
        self.assertEqual(checked["record_refusal_out_of_band"][0], "returned:False")
        db = self.SF()
        counts = db.execute(text("SELECT refused_count, replay_count FROM "
                                 "whatsapp_booking_offers WHERE id=:i"),
                            {"i": offer_id}).first()
        db.close()
        self.assertEqual(tuple(counts), (0, 0),
                         "a contended bookkeeping writer moved a counter anyway")

    # ══ R8: the order the real refusal turn actually issues ═════════════════

    def test_r2_pg_08_a_refusal_turn_writes_thread_states_before_the_offer_table(self):
        """MUTATION ORACLE for required mutation 9.

        The inverse order is what the architecture audit measured as a reproducible `40P01`.
        This records the statement order the REAL refusal turn issues and requires resource 1
        to be acquired before the offer table is touched.
        """
        from app.schemas.conversation import ConversationHandleIn
        from app.services.offer_lifecycle import OfferStatus
        tok = self._seed_offer(OfferStatus.WITHDRAWN, "TEXT_CORRECTION")
        db, eng = self._engine()
        seen = []
        original = db.execute

        def spy(stmt, *a, **k):
            sql = " ".join(str(stmt).split())
            head = sql.split()[0].upper() if sql.split() else ""
            for table in ("whatsapp_thread_states", "whatsapp_booking_offers"):
                if table in sql and head in ("SELECT", "UPDATE", "INSERT"):
                    seen.append(f"{head} {table}")
            return original(stmt, *a, **k)

        db.execute = spy
        eng.handle(ConversationHandleIn(
            thread_id=self.thread_id, wa_message_id="wamid.order.%s" % _t.time(),
            wa_id=WA, message_type="flow_response",
            flow_response=dict(BOOKING_PAYLOAD), flow_token=tok))
        db.close()

        locks = [i for i, s in enumerate(seen) if s == "SELECT whatsapp_thread_states"]
        offer_writes = [i for i, s in enumerate(seen)
                        if s == "UPDATE whatsapp_booking_offers"]
        self.assertTrue(locks, f"resource 1 was never acquired on a refusal turn: {seen}")
        if offer_writes:
            self.assertLess(min(locks), min(offer_writes),
                            f"the offer table was written before resource 1: {seen}")

    # ══ R9: the Data Exchange command is bounded too ════════════════════════

    def test_r2_pg_09_the_data_exchange_command_answers_inside_the_bound(self):
        """The other transport. Both writers must answer, or only half the pair is fixed."""
        from app.services.booking_flow_service import (BookingFlowService,
                                                       BookingTokenError)
        token = self._issue()
        day, hhmm = self._open_slots(1)[0]
        before = self._snapshot()
        holder, release = self._hold_resource_1()
        db = self.SF()
        svc = BookingFlowService(db)
        svc._send_booking_receipt = lambda *a, **k: None
        started = _t.perf_counter()
        try:
            svc.handle_confirm_booking(token, {
                "date": day, "time": hhmm, "name": "Bound Probe",
                "email": "b@example.test", "address": "Palermo 123",
                "seller_type": "particular", "seller_name": "Vendedor"})
            outcome = "completed"
        except BookingTokenError as exc:
            outcome = "refused:%s" % str(exc)[:60]
        except Exception as exc:
            outcome = "EXC:%s" % type(exc).__name__
        elapsed = _t.perf_counter() - started
        try:
            db.rollback()
        except Exception:
            pass
        db.close()
        release.set(); holder.join(timeout=30)

        self.assertLess(elapsed, BOUND_ALLOWANCE_S,
                        f"the DX command waited {elapsed:.3f}s against a "
                        f"{HOLD_SECONDS}s hold — the bound is not in force")
        self.assertTrue(outcome.startswith("refused:"),
                        f"the DX command answered {outcome!r} instead of refusing")
        after = self._snapshot()
        self.assertEqual(after["offers"], before["offers"],
                         "contention mutated the offer on the DX path")
        self.assertEqual(after["bookings"], 0)


if __name__ == "__main__":                          # pragma: no cover
    unittest.main()
