"""G3-7R-R3 — the cycle-reset bound, the replay race, and the FK name, on real PostgreSQL.

Disposable database only (`G37R_PG_URL`); skips cleanly without it and says so, because a
bound that was never measured is not a weaker claim, it is no claim at all.

WHY THIS FILE EXISTS, STATED AS A MEASUREMENT. G3-7R-R2 moved the canonical bounded
acquisition above all three of admission's outcomes and measured five ordinary
booking-shaped submission classes answering `thread_contended` in ~4.0s against a 12s hold.
Its own suite passed, its PG suite passed, the sealed regression was identity-identical.

The R2 independent review then tried a SIXTH shape, which R2 had never enumerated: the same
booking submission, on a turn that also carries `cycle_reset_pending`. That flag is set by
`lead_lifecycle.set_lead_estado()` whenever a human moves a lead backwards in the CRM, and
the L4 Wild preflight requires it. Measured on `72fbb627`:

    hold 14s → turn 14.033s     hold 20s → turn 20.045s     action `replied`
    cursor CONSUMED             offer → WITHDRAWN/CYCLE_RESET, refused_count=2
    candidate ARCHIVED          needs_human WRITTEN
    warm-handoff text SENT to the customer, plus an internal notification

The whole pre-fix behaviour, tracking the holder rather than any bound. The cause was not a
missing branch: `_handle` assigns the dedup cursor and the reset block runs BEFORE
`_process_flow_response`, so `_execute_cycle_reset`, `_reload_active_candidates` and
`_query_active_messages` autoflush the pending cursor UPDATE — on resource 1 — with no bound
in scope at all.

So R2's remedy was scoped to a code path when the fact it had to answer was about an
ORDERING: from the instant the cursor write is pending, every later statement of the turn can
block on resource 1. R3 therefore enters the bound BEFORE the cursor is assigned, for every
turn `_is_booking_shaped_submission` admits, and the cycle reset — which archives candidates,
clears state and withdraws the live offer — enters it in its own right.

    A fix that enumerates turn shapes is only as good as the enumeration.
    This file tests the ordering instead.

Three further things the review found untested, and which are tested here for the first time:
`_booking_projection_replay`'s bounded re-entry (R2's mutation `rv3` survived every shipped
oracle), the RV-04 foreign-key NAME (mutation `rv10` had no test at all), and the deliberate
flush inside `_bounded_lock_wait`'s protected region (RVR2-02).
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

#: A SECOND disposable database, built by Alembic rather than by `create_all()`. Only the
#: foreign-key name agreement test needs it, and it skips without it — the two schemas have
#: to be built two different ways or the test proves nothing.
ALEMBIC_URL = os.environ.get("G37R_PG_ALEMBIC_URL", "")

WA = "549110000793"
BOOKING_PAYLOAD = {
    "nombre_apellido": "Reset Probe", "email": "r@example.test", "telefono": WA,
    "como_llego": "Instagram", "direccion": "Palermo 123", "tipo_vendedor": "particular",
}
BOOKING_FLOW_ID = "28104222025943520"

#: The declared bound is `DEFAULT_OFFER_LOCK_TIMEOUT_MS` = 4000ms.
DECLARED_BOUND_S = 4.0

#: Covers the engine's own work either side of the acquisition. Far below every holder
#: duration used here, which is what makes "respected its bound" and "waited for the
#: holder" two distinguishable outcomes rather than one number.
BOUND_ALLOWANCE_S = 8.0

#: TWO holder durations, both well above the bound and well apart from each other. One
#: number cannot separate "the code waits 4s" from "the code waits as long as the lock is
#: held"; two can, and the R2 review showed that every single-number claim made about the
#: unbounded case (42s, 72s) was a property of the instrument, not of the code.
HOLDS = (10.0, 18.0)


class _PGBase(unittest.TestCase):
    """The world, the real engine, and the competing writer. Observations on real rows."""

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_URL, pool_size=16, max_overflow=8)
        cls.SF = sessionmaker(bind=cls.engine, expire_on_commit=False)
        with cls.engine.begin() as c:
            assert c.execute(text("SELECT to_regclass('whatsapp_booking_offers')")).scalar()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        import app.models as M
        self.M = M
        # These tests deliberately hold resource 1 in worker threads. A worker that
        # outlived its join would block the next test's writes with no diagnosis, so
        # pooled connections are released and strays terminated before TRUNCATE.
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
        ct = M.WhatsAppContact(wa_id=WA, display_name="Reset"); db.add(ct); db.flush()
        lead = M.Lead(nombre="Reset", apellido="Probe", telefono=WA,
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
        # TWO open slots. The second is for the booking-projection race: the winner must
        # book a DIFFERENT slot, or the loser's final availability revalidation correctly
        # refuses the submission and the turn never reaches its insert at all. The index is
        # declared on `offer_id` alone, so a different date still violates it — which is
        # the point: one offer authorises one booking, whatever slot it names.
        _slots = self._open_slots(2)
        self.day, self.hhmm = _slots[0]
        self.day2, self.hhmm2 = _slots[1]
        self._arm(self.day, self.hhmm)

    # ── the real engine, with ONLY the external transport stubbed ────────────

    def _engine_for(self, db=None):
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
        """Dispatch the Flow through the real path, which is what ISSUES the offer."""
        db, eng = self._engine_for()
        ctx = eng._load_context(self.thread_id)
        ctx.state.needs_human = False
        db.commit()
        eng._send_booking_flow(ctx, ctx.state, booking_flow_id=BOOKING_FLOW_ID)
        db.refresh(ctx.state)
        token = ctx.state.flow_booking_token
        db.close()
        self.assertTrue(token, "the dispatch path issued no offer — nothing to prove")
        return token

    def _arm_cycle_reset(self):
        """The one-shot signal `set_lead_estado()` writes when a human moves a lead back."""
        db = self.SF()
        db.execute(text("UPDATE whatsapp_thread_states SET cycle_reset_pending=true, "
                        "needs_human=false WHERE thread_id=:t"), {"t": self.thread_id})
        db.commit(); db.close()

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
            "lead_needs_human": bool(db.execute(text(
                "SELECT coalesce(necesita_humano,false) FROM leads WHERE id=:i"),
                {"i": self.lead_id}).scalar()),
            "lead_estado": str(db.execute(text(
                "SELECT coalesce(estado,'') FROM leads WHERE id=:i"),
                {"i": self.lead_id}).scalar()),
            "cycle_reset_pending": bool(db.execute(text(
                "SELECT cycle_reset_pending FROM whatsapp_thread_states "
                "WHERE thread_id=:t"), {"t": self.thread_id}).scalar()),
            "cycle_watermark": str(db.execute(text(
                "SELECT coalesce(current_cycle_start_message_db_id::text,'<none>') "
                "FROM whatsapp_thread_states WHERE thread_id=:t"),
                {"t": self.thread_id}).scalar()),
            "offers": [tuple(map(str, r)) for r in db.execute(text(
                "SELECT id, status, coalesce(terminal_reason,''), refused_count, "
                "coalesce(last_refusal,''), replay_count FROM whatsapp_booking_offers "
                "ORDER BY id"))],
            "candidates": [tuple(map(str, r)) for r in db.execute(text(
                "SELECT id, status FROM whatsapp_thread_candidates ORDER BY id"))],
            "bookings": int(db.execute(text(
                "SELECT count(*) FROM thread_revisions WHERE status='booked'")).scalar()),
            "revisions": int(db.execute(text("SELECT count(*) FROM revisions")).scalar()),
            "out_rows": int(db.execute(text(
                "SELECT count(*) FROM whatsapp_messages WHERE direction='out'")).scalar()),
        }
        db.close()
        return s

    # ── the competing writer ─────────────────────────────────────────────────

    def _holder_releasing_after(self, seconds):
        """Holds resource 1 and lets go by ITSELF after `seconds`. No release signal.

        Deliberate: a bounded turn returns at its bound whatever this is, and an unbounded
        turn returns when this does. Running the same case at two durations is therefore
        what separates the two, and no single number can.
        """
        holding = threading.Event()
        released = {}

        def holder():
            db = self.SF()
            try:
                db.execute(text("SELECT 1 FROM whatsapp_thread_states WHERE thread_id=:t "
                                "FOR NO KEY UPDATE"), {"t": self.thread_id}).fetchall()
                holding.set()
                _t.sleep(seconds)
            finally:
                released["at"] = _t.perf_counter()
                db.rollback(); db.close()

        th = threading.Thread(target=holder, name="R3-HOLDER", daemon=True)
        th.start()
        self.assertTrue(holding.wait(timeout=30),
                        "the competing writer never acquired resource 1 — nothing held")
        return th, released

    def _persist_inbound(self, wamid):
        """n8n persists the inbound row BEFORE it dispatches to CE, so the fixture must.

        `_execute_cycle_reset` resolves the new cycle's watermark from this row — its own
        comment says "always exists — persisted before CE dispatch" — so a fixture that
        skips it cannot observe whether the reset established a watermark at all.
        """
        db = self.SF()
        db.add(self.M.WhatsAppMessage(
            thread_id=self.thread_id, wa_message_id=wamid, direction="in",
            timestamp=datetime.now(timezone.utc), message_type="text",
            text="probe", status="received"))
        db.commit(); db.close()

    def _submit(self, token, tag, message_type="flow_response", text_body=None):
        """One delivery in its OWN session — the shape of one n8n HTTP call."""
        from app.schemas.conversation import ConversationHandleIn
        db, eng = self._engine_for()
        wamid = "wamid.%s.%s" % (tag, _t.time())
        self._persist_inbound(wamid)
        kwargs = dict(thread_id=self.thread_id, wa_message_id=wamid, wa_id=WA,
                      message_type=message_type)
        if message_type == "flow_response":
            kwargs.update(flow_response=dict(BOOKING_PAYLOAD), flow_token=token)
        else:
            kwargs.update(text=text_body or "hola")
        started = _t.perf_counter()
        usable = None
        try:
            out = eng.handle(ConversationHandleIn(**kwargs))
            action, detail = out.action, (getattr(out, "detail", "") or "")
        except Exception as exc:                    # a raised exception is also an answer
            action, detail = "EXCEPTION:%s" % type(exc).__name__, str(exc)[:160]
        finally:
            elapsed = _t.perf_counter() - started
            try:
                db.execute(text("SELECT 1")).scalar()
                usable = True
            except Exception as exc:
                usable = "NO:%s" % type(exc).__name__
            db.close()
        return action, detail, elapsed, usable

    def _submit_in_thread(self, token, tag, join_after):
        """Run the turn in a worker so an UNBOUNDED turn can be observed, not waited on."""
        box = {}

        def run():
            box["result"] = self._submit(token, tag)

        th = threading.Thread(target=run, name="R3-VICTIM", daemon=True)
        th.start()
        th.join(timeout=join_after)
        self.assertIn("result", box,
                      "the turn had not returned after %.0fs — it is not bounded at all"
                      % join_after)
        return box["result"]


@unittest.skipUnless(PG_URL, "G37R_PG_URL not set — no disposable PostgreSQL; nothing proven")
class G37RR3CycleResetBoundPG(_PGBase):
    """§3 + §6A. The whole contention contract for a `cycle_reset_pending` booking turn."""

    def _assert_full_contention_contract(self, hold_seconds):
        """Every clause of §3, measured on one real turn against one real holder."""
        from app.services.conversation_engine import ACTION_THREAD_CONTENDED
        token = self._issue()
        self._arm_cycle_reset()
        before = self._snapshot()
        self.assertTrue(before["cycle_reset_pending"],
                        "the fixture did not arm the flag — the case under test is absent")
        self.assertEqual(before["offers"][0][1], "ISSUED")
        self.assertEqual(before["candidates"], [(str(self.cand_a), "current_focus")])
        self.sends.clear()

        holder, released = self._holder_releasing_after(hold_seconds)
        action, detail, elapsed, usable = self._submit_in_thread(
            token, "cyclereset", join_after=hold_seconds + 60)
        during = self._snapshot()
        sent_during = list(self.sends)
        holder.join(timeout=30)

        # 1. BOUNDED — and bounded by the DECLARED bound, not by the holder. This is the
        #    clause `72fbb627` failed: 14.033s against 14s, 20.045s against 20s.
        self.assertLess(elapsed, BOUND_ALLOWANCE_S,
                        "the turn took %.3fs against a %.0fs hold — it waited for the "
                        "holder, not for its bound" % (elapsed, hold_seconds))
        self.assertLess(elapsed, hold_seconds - 1.0,
                        "the turn returned only when the holder let go (%.3fs vs %.0fs)"
                        % (elapsed, hold_seconds))
        # 2. TYPED. `replied` is what the pre-fix code returned — a turn that told the
        #    customer something while having been unable to judge their submission.
        self.assertEqual(action, ACTION_THREAD_CONTENDED,
                         "contention answered %r with detail %r" % (action, detail))
        # 3. THE DEDUP CURSOR IS PRESERVED, so a redelivery may try again.
        self.assertEqual(during["cursor"], before["cursor"],
                         "the contended turn consumed the dedup cursor — a redelivery of "
                         "this message can no longer be processed")
        # 4. THE CYCLE RESET DID NOT RUN, not even partially.
        self.assertTrue(during["cycle_reset_pending"],
                        "the one-shot cycle-reset signal was consumed by a turn that "
                        "never executed the reset — the reset is now lost")
        self.assertEqual(during["cycle_watermark"], before["cycle_watermark"],
                         "a cycle watermark was established by a contended turn")
        # 5. THE CANDIDATE IS NOT ARCHIVED.
        self.assertEqual(during["candidates"], before["candidates"],
                         "the contended turn archived the customer's vehicle candidate")
        # 6. THE OFFER IS UNTOUCHED — status, terminal reason and both counters.
        self.assertEqual(during["offers"], before["offers"],
                         "the contended turn mutated the offer")
        # 7. NO BOOKING, NO REVISION.
        self.assertEqual(during["bookings"], 0)
        self.assertEqual(during["revisions"], before["revisions"])
        # 8. NO STAGE OR needs_human CHANGE, on the thread OR on the lead.
        self.assertEqual(during["needs_human"], before["needs_human"])
        self.assertEqual(during["lead_needs_human"], before["lead_needs_human"])
        self.assertEqual(during["lead_estado"], before["lead_estado"])
        # 9. NOTHING SENT — no customer text, and no internal handoff notification.
        self.assertEqual(sent_during, [],
                         "the contended turn sent %r; the pre-fix code sent the customer "
                         "a warm handoff plus an internal notify" % (sent_during,))
        self.assertEqual(during["out_rows"], before["out_rows"],
                         "an outbound ledger row was written by a contended turn")
        # 10. THE SESSION IS USABLE AFTER THE ROLLBACK.
        self.assertIs(usable, True,
                      "the session was left unusable after contention: %r" % (usable,))
        return token, elapsed

    def test_r3_pg_01_cycle_reset_booking_turn_is_bounded_at_a_10s_hold(self):
        """MUTATION ORACLE for required mutations 1–7. The defect, at the first duration."""
        self._assert_full_contention_contract(HOLDS[0])

    def test_r3_pg_02_cycle_reset_booking_turn_is_bounded_at_an_18s_hold(self):
        """The SAME case at a second duration — which is what proves it is a bound.

        One measurement cannot distinguish "waits 4s" from "waits as long as the lock is
        held". Two can: an unbounded turn moves with the holder, a bounded one does not.
        """
        self._assert_full_contention_contract(HOLDS[1])

    def test_r3_pg_03_the_elapsed_time_tracks_the_bound_and_not_the_holder(self):
        """The two durations, compared to each other rather than to a threshold."""
        _, short = self._assert_full_contention_contract(HOLDS[0])
        self.setUp()
        _, long_ = self._assert_full_contention_contract(HOLDS[1])
        self.assertLess(
            abs(long_ - short), 2.5,
            "the turn took %.3fs against a %.0fs hold and %.3fs against a %.0fs hold — it "
            "is tracking the holder, so it has no bound of its own"
            % (short, HOLDS[0], long_, HOLDS[1]))

    def test_r3_pg_04_the_same_submission_completes_once_the_contention_clears(self):
        """§3 "behavior after contention clears" — and the reset runs EXACTLY once."""
        token = self._issue()
        self._arm_cycle_reset()
        holder, _ = self._holder_releasing_after(HOLDS[0])
        self._submit_in_thread(token, "contended", join_after=HOLDS[0] + 60)
        holder.join(timeout=30)
        contended_state = self._snapshot()

        self.sends.clear()
        action, detail, elapsed, usable = self._submit(token, "retry")
        after = self._snapshot()

        self.assertLess(elapsed, BOUND_ALLOWANCE_S)
        self.assertNotEqual(action, "thread_contended",
                            "the retry was still contended after the holder let go")
        # The reset executed, exactly once: the flag is consumed, a watermark exists, the
        # prior-cycle candidate is archived once, and the stale offer is withdrawn by the
        # cycle-reset policy rather than accepted.
        self.assertFalse(after["cycle_reset_pending"], "the retry did not run the reset")
        self.assertNotEqual(after["cycle_watermark"], "<none>")
        self.assertEqual([c[1] for c in after["candidates"]], ["archived"],
                         "the candidate was archived %d times, not once"
                         % sum(1 for c in after["candidates"] if c[1] == "archived"))
        self.assertEqual(len(after["offers"]), 1)
        offer = after["offers"][0]
        self.assertEqual(offer[1], "WITHDRAWN")
        self.assertEqual(offer[2], "CYCLE_RESET")
        # NEVER a booking from an offer the reset invalidated.
        self.assertEqual(after["bookings"], 0,
                         "a booking was created from an offer the cycle reset withdrew")
        self.assertEqual(after["revisions"], contended_state["revisions"])
        # The cursor advances with the canonical transaction, once.
        self.assertNotEqual(after["cursor"], contended_state["cursor"],
                            "the completed retry did not advance the dedup cursor")
        # Exactly one refusal is recorded — the retry's. The pre-fix code recorded two,
        # because the contended turn had refused as well.
        self.assertEqual(offer[3], "1",
                         "refused_count is %s — a contended turn moved a counter" % offer[3])

    def test_r3_pg_05_an_uncontended_cycle_reset_turn_is_unchanged(self):
        """REGRESSION GUARD. No contention: the authorised reset policy must be identical.

        The correction must not change the business meaning of `cycle_reset_pending`, so the
        uncontended case is asserted on the same observations as the contended one.
        """
        token = self._issue()
        self._arm_cycle_reset()
        before = self._snapshot()
        self.sends.clear()
        action, detail, elapsed, usable = self._submit(token, "clean")
        after = self._snapshot()

        self.assertLess(elapsed, BOUND_ALLOWANCE_S)
        self.assertNotEqual(action, "thread_contended")
        self.assertIs(usable, True)
        self.assertFalse(after["cycle_reset_pending"])
        self.assertNotEqual(after["cycle_watermark"], "<none>")
        self.assertEqual([c[1] for c in after["candidates"]], ["archived"])
        self.assertEqual(after["offers"][0][1], "WITHDRAWN")
        self.assertEqual(after["offers"][0][2], "CYCLE_RESET")
        self.assertEqual(after["bookings"], 0)
        self.assertNotEqual(after["cursor"], before["cursor"],
                            "an uncontended turn must advance the dedup cursor")

    def test_r3_pg_06_a_TEXT_turn_carrying_the_flag_is_bounded_too(self):
        """MUTATION ORACLE for the defence-in-depth half of the correction.

        `_is_booking_shaped_submission` is False for a text turn, so the turn-entry
        acquisition does not apply to it — correctly, because an ordinary conversational
        turn must not take the booking lock, which is the whole reason the general
        turn-entry gate was deleted. But a turn carrying `cycle_reset_pending` WRITES: it
        archives candidates, clears eighteen state fields and withdraws the live offer. So
        the reset enters the order in its own right, and this measures that a text turn
        which happens to carry the flag gets a bounded typed answer instead of the
        open-ended wait that shape had after R3's entry acquisition alone.
        """
        from app.services.conversation_engine import ACTION_THREAD_CONTENDED
        self._issue()
        self._arm_cycle_reset()
        before = self._snapshot()
        self.sends.clear()
        holder, _ = self._holder_releasing_after(HOLDS[1])
        box = {}

        def run():
            box["r"] = self._submit(None, "textreset", message_type="text",
                                    text_body="hola, seguimos?")

        th = threading.Thread(target=run, daemon=True); th.start()
        th.join(timeout=HOLDS[1] + 60)
        self.assertIn("r", box, "the text turn never returned — it is not bounded")
        action, detail, elapsed, usable = box["r"]
        after = self._snapshot()
        holder.join(timeout=30)

        self.assertLess(elapsed, BOUND_ALLOWANCE_S,
                        "a text turn carrying cycle_reset_pending waited %.3fs against an "
                        "%.0fs hold" % (elapsed, HOLDS[1]))
        self.assertEqual(action, ACTION_THREAD_CONTENDED)
        self.assertEqual(after["cursor"], before["cursor"])
        self.assertTrue(after["cycle_reset_pending"])
        self.assertEqual(after["offers"], before["offers"])
        self.assertEqual(after["candidates"], before["candidates"])
        self.assertEqual(self.sends, [])

    def test_r3_pg_06b_a_booking_turn_on_a_human_owned_thread_is_bounded(self):
        """MUTATION ORACLE for the turn-entry acquisition, which nothing else covers.

        This is the case the entry bound exists for and the reset bound cannot reach. A
        booking-shaped submission arrives on a thread a human has taken over, so `_handle`
        answers `skipped_human` — and it COMMITS on the way out, which flushes the pending
        dedup-cursor UPDATE on resource 1. No cycle reset is involved, and
        `_process_flow_response` is never reached, so neither of the other two acquisitions
        is in scope. Before the entry bound that commit was an open-ended wait.
        """
        from app.services.conversation_engine import ACTION_THREAD_CONTENDED
        token = self._issue()
        db = self.SF()
        db.execute(text("UPDATE whatsapp_thread_states SET needs_human=true "
                        "WHERE thread_id=:t"), {"t": self.thread_id})
        db.commit(); db.close()
        before = self._snapshot()
        self.sends.clear()

        holder, _ = self._holder_releasing_after(HOLDS[1])
        action, detail, elapsed, usable = self._submit_in_thread(
            token, "humanowned", join_after=HOLDS[1] + 60)
        after = self._snapshot()
        holder.join(timeout=30)

        self.assertLess(elapsed, BOUND_ALLOWANCE_S,
                        "a booking submission on a human-owned thread waited %.3fs against "
                        "an %.0fs hold — it committed the cursor outside any bound"
                        % (elapsed, HOLDS[1]))
        self.assertEqual(action, ACTION_THREAD_CONTENDED,
                         "answered %r / %r" % (action, detail))
        self.assertEqual(after["cursor"], before["cursor"],
                         "the contended turn consumed the dedup cursor")
        self.assertEqual(after["offers"], before["offers"])
        self.assertEqual(self.sends, [])
        self.assertIs(usable, True)

    def test_r3_pg_07_an_ordinary_text_turn_takes_no_booking_lock(self):
        """THE OTHER HALF OF THE SAME GUARANTEE — the deleted gate stays deleted.

        A text turn with NO cycle reset pending must run to completion while another
        session holds resource 1, because it is not a booking writer and must not be
        serialised behind one. The R3 architecture review measured the cost of the old
        unconditional gate at 4.05s on threads the parent commit served in 0.05s.
        """
        holder, _ = self._holder_releasing_after(HOLDS[1])
        box = {}

        def run():
            box["r"] = self._submit(None, "plaintext", message_type="text",
                                    text_body="hola")

        th = threading.Thread(target=run, daemon=True); th.start()
        th.join(timeout=HOLDS[1] + 30)
        holder.join(timeout=40)
        self.assertIn("r", box, "an ordinary text turn did not return at all")
        action, detail, elapsed, usable = box["r"]
        self.assertNotEqual(
            action, "thread_contended",
            "an ordinary text turn was refused for booking contention — the general "
            "turn-entry gate has been reintroduced")

    def test_r3_pg_08_the_predicate_admits_exactly_the_submissions_that_reach_admission(self):
        """The gate's predicate, stated and checked against the router it mirrors.

        Structural, and deliberately so: this is a declaration, not a concurrency claim.
        """
        from app.schemas.conversation import ConversationHandleIn
        from app.services.conversation_engine import ConversationEngine

        class _St:
            flow_booking_token = None

        def ev(**kw):
            base = dict(thread_id=self.thread_id, wa_message_id="w", wa_id=WA)
            base.update(kw)
            return ConversationHandleIn(**base)

        P = ConversationEngine._is_booking_shaped_submission
        st = _St()
        self.assertTrue(P(ev(message_type="flow_response",
                             flow_response=dict(BOOKING_PAYLOAD), flow_token="t"), st))
        # not a Flow response at all
        self.assertFalse(P(ev(message_type="text", text="hola"), st))
        # a Flow response with no payload
        self.assertFalse(P(ev(message_type="flow_response", flow_response={}), st))
        # the two qualification fallback forms, keyed on their distinctive payload keys
        self.assertFalse(P(ev(message_type="flow_response",
                              flow_response={"tipo_vehiculo": "AUTO"}), st))
        self.assertFalse(P(ev(message_type="flow_response",
                              flow_response={"zona_general": "CABA"}), st))
        # the motorcycle handoff, keyed on the mirror token prefix
        st_moto = _St(); st_moto.flow_booking_token = "moto-handoff-7-1"
        self.assertFalse(P(ev(message_type="flow_response",
                              flow_response=dict(BOOKING_PAYLOAD)), st_moto))


@unittest.skipUnless(PG_URL, "G37R_PG_URL not set — no disposable PostgreSQL; nothing proven")
class G37RR3BookingProjectionReplayPG(_PGBase):
    """§6B. The real race that reaches `_booking_projection_replay`.

    WHAT THE INTERLEAVING HAS TO BE, AND WHY. Building this race taught something the R2
    review did not state and R3 measured here: the window is narrower than
    `_booking_projection_replay`'s docstring implies, and the FIRST construction attempted
    deadlocks rather than racing.

      * The loser cannot be paused between its CLAIM and its insert. `_claim_checks` holds
        the offer row `FOR UPDATE`, and a concurrent `INSERT INTO thread_revisions` carrying
        the same `offer_id` must take `FOR KEY SHARE` on that row for its foreign key. So a
        winner released at that point simply BLOCKS on the loser — measured here at the
        full 60s coordination timeout, with the loser then answering `error`.
      * The window is therefore between ADMISSION and the claim, which is exactly what the
        replay handler's docstring says: "a concurrent writer committed the booking between
        this turn's admission and its insert". The winner commits there, releasing its
        foreign-key lock; the loser's claim then succeeds — the offer is still ISSUED,
        because committing a booking ROW is not the status transition — and its insert hits
        the partial unique index for real.

    So the loser is the REAL `ConversationEngine.handle()`, paused after the real `admit()`
    by a hook on the real method, and the winner is a real second transaction committing a
    real booking row for the same offer. Nothing is stubbed and no handler is called
    directly.

    THIS IS ALSO WHY `rv3` SURVIVED EVERY ORACLE R2 SHIPPED. Under the shipped coordination
    two concurrent customer acceptances cannot BOTH reach an insert: whichever claims first
    holds the offer row, and the other is refused inside its own bound. The index is the
    last line of defence against a writer that commits a booking without passing through
    the status transition — which the G3-7P-R3A review proved possible by removing both
    guards and measuring two bookings in 6 of 6 trials. That is the writer modelled here.
    The reachability is stated in the closeout as DEFENCE IN DEPTH, not as a live path.
    """

    def _winner_books(self, offer_id, when_ready):
        """A second transaction that commits a booking for the SAME offer.

        Built through the real models, so the row it commits is the shape the authority
        writes — including the `offer_id` the partial unique index is declared on. Any
        failure is RECORDED rather than swallowed: a winner that silently did not commit
        would leave the loser measuring nothing, which is how a race test lies.
        """
        done = threading.Event()
        box = {}

        def run():
            when_ready.wait(timeout=60)
            db = self.SF()
            try:
                rev = self.M.Revision(lead_id=self.lead_id,
                                      created_at=datetime.now())
                db.add(rev); db.flush()
                db.add(self.M.ThreadRevision(
                    thread_id=self.thread_id, candidate_id=self.cand_a,
                    offer_id=offer_id, status="booked",
                    scheduled_date=date.fromisoformat(self.day2),
                    scheduled_time=time.fromisoformat(self.hhmm2)))
                db.commit()
                box["thread_revision_id"] = int(db.execute(text(
                    "SELECT id FROM thread_revisions WHERE offer_id=:o"),
                    {"o": offer_id}).scalar())
                done.set()
            except Exception as exc:                # pragma: no cover - diagnosed below
                box["error"] = "%s: %s" % (type(exc).__name__, exc)
                db.rollback()
                done.set()
            finally:
                db.close()

        th = threading.Thread(target=run, name="R3-WINNER", daemon=True)
        th.start()
        return th, done, box

    def _loser_turn(self, token, *, hold_resource_1_after_claim):
        """Drive the real turn, releasing the winner between its ADMISSION and its claim."""
        from app.schemas.conversation import ConversationHandleIn
        from app.services.offer_lifecycle import OfferLifecycle

        db, eng = self._engine_for()
        admitted = threading.Event()
        holder_state = {"acquired": False}
        original = OfferLifecycle.admit
        offer_id_box = {}

        db_probe = self.SF()
        offer_id_box["id"] = int(db_probe.execute(text(
            "SELECT id FROM whatsapp_booking_offers ORDER BY id DESC LIMIT 1")).scalar())
        db_probe.close()

        winner, go, winner_box = self._winner_books(offer_id_box["id"], admitted)

        holder_thread = None
        if hold_resource_1_after_claim:
            # Enqueue BEHIND the loser on resource 1. The loser holds it from its turn
            # entry, so this blocks; the instant `_booking_projection_replay` rolls back
            # and releases it, this acquires — which is precisely the window the replay's
            # bounded re-entry has to survive.
            def holder():
                hdb = self.SF()
                try:
                    admitted.wait(timeout=60)
                    hdb.execute(text(
                        "SELECT 1 FROM whatsapp_thread_states WHERE thread_id=:t "
                        "FOR NO KEY UPDATE"), {"t": self.thread_id}).fetchall()
                    holder_state["acquired"] = True
                    _t.sleep(HOLDS[1])
                finally:
                    hdb.rollback(); hdb.close()

            holder_thread = threading.Thread(target=holder, name="R3-REPLAY-HOLDER",
                                             daemon=True)
            holder_thread.start()

        def patched(self_lc, *a, **kw):
            out = original(self_lc, *a, **kw)
            if getattr(out, "granted", False):
                admitted.set()
                # Let the winner COMMIT, and release its foreign-key lock, before this
                # turn claims and inserts.
                self.assertTrue(winner_done.wait(timeout=60),
                                "the winning transaction never committed")
                _t.sleep(0.4)
            return out

        winner_done = go
        OfferLifecycle.admit = patched
        started = _t.perf_counter()
        try:
            out = eng.handle(ConversationHandleIn(
                thread_id=self.thread_id, wa_message_id="wamid.loser.%s" % _t.time(),
                wa_id=WA, message_type="flow_response",
                flow_response=dict(BOOKING_PAYLOAD), flow_token=token))
            action, detail = out.action, (getattr(out, "detail", "") or "")
        except Exception as exc:
            action, detail = "EXCEPTION:%s" % type(exc).__name__, str(exc)[:200]
        finally:
            elapsed = _t.perf_counter() - started
            OfferLifecycle.admit = original
            db.close()
        winner.join(timeout=30)
        if holder_thread is not None:
            holder_thread.join(timeout=HOLDS[1] + 30)
        self.assertNotIn("error", winner_box,
                         "the WINNING transaction failed, so this run measured nothing: %s"
                         % winner_box.get("error"))
        self.assertIn("thread_revision_id", winner_box,
                      "the winning transaction committed no booking")
        return (action, detail, elapsed, offer_id_box["id"], holder_state["acquired"],
                winner_box["thread_revision_id"])

    def test_r3_pg_09_the_index_rejects_the_loser_and_the_loser_returns_the_winners_booking(self):
        """§6B, uncontended: one booking, one revision, the authoritative result returned."""
        from app.services.conversation_engine import ACTION_BOOKING_REPLAY
        token = self._issue()
        before = self._snapshot()
        self.sends.clear()
        action, detail, elapsed, offer_id, _, winner_id = self._loser_turn(
            token, hold_resource_1_after_claim=False)
        after = self._snapshot()

        db = self.SF()
        rows = db.execute(text(
            "SELECT id FROM thread_revisions WHERE offer_id=:o AND status='booked' "
            "ORDER BY id"), {"o": offer_id}).scalars().all()
        db.close()
        self.assertEqual(len(rows), 1,
                         "the partial unique index allowed %d bookings for one offer"
                         % len(rows))
        self.assertEqual(action, ACTION_BOOKING_REPLAY,
                         "the loser answered %r / %r instead of returning the "
                         "authoritative booking" % (action, detail))
        self.assertEqual(detail, str(rows[0]),
                         "the loser returned %r, not the committed booking %s"
                         % (detail, rows[0]))
        # The winner's revision, and NOT a second one from the loser.
        self.assertEqual(after["revisions"], before["revisions"] + 1,
                         "the loser wrote a second canonical revision")
        # No second acknowledgement, and no human handoff: the winner already acknowledged.
        self.assertEqual([k for k, _v in self.sends], [],
                         "the replaying turn sent %r — the customer is acknowledged twice"
                         % (self.sends,))
        self.assertFalse(after["needs_human"],
                         "the replaying turn raised a human handoff")
        self.assertEqual(after["offers"][0][5], "1",
                         "the replay was not counted: replay_count=%s"
                         % after["offers"][0][5])

    def test_r3_pg_10_the_replay_re_entry_is_bounded(self):
        """MUTATION ORACLE for required mutation 9 (RVR2-03 / `rv3`).

        `_booking_projection_replay` rolls back — which RELEASES resource 1 — and then
        re-enters the bound before it touches the replay counter. A competing writer that
        takes the row in that window must not be able to stall the turn: the booking is the
        fact the customer depends on, the counter is bookkeeping, and the authoritative
        booking must still be returned inside the bound with the counter SKIPPED.
        """
        from app.services.conversation_engine import ACTION_BOOKING_REPLAY
        token = self._issue()
        action, detail, elapsed, offer_id, acquired, winner_id = self._loser_turn(
            token, hold_resource_1_after_claim=True)
        after = self._snapshot()

        self.assertTrue(acquired,
                        "the competing writer never took resource 1 in the replay window "
                        "— this run measured nothing; re-run")
        self.assertLess(elapsed, HOLDS[1] - 1.0,
                        "the replaying turn took %.3fs against an %.0fs hold — the "
                        "bounded re-entry is gone" % (elapsed, HOLDS[1]))
        self.assertEqual(action, ACTION_BOOKING_REPLAY,
                         "a contended replay answered %r / %r instead of the booking"
                         % (action, detail))
        db = self.SF()
        rows = db.execute(text(
            "SELECT id FROM thread_revisions WHERE offer_id=:o AND status='booked'"),
            {"o": offer_id}).scalars().all()
        db.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(detail, str(rows[0]))
        self.assertEqual(after["offers"][0][5], "0",
                         "the counter was moved while resource 1 was held by another "
                         "writer — the re-entry is not bounded, it is absent")


@unittest.skipUnless(PG_URL, "G37R_PG_URL not set — no disposable PostgreSQL; nothing proven")
class G37RR3BoundedWaitTimeoutRestoration(_PGBase):
    """§5 / RVR2-02. The caller's `lock_timeout` survives this helper, including a raise."""

    def _lock_timeout(self, db):
        return str(db.execute(text("SHOW lock_timeout")).scalar()).strip()

    def test_r3_pg_11_the_callers_default_is_restored_after_success(self):
        from app.services.offer_lifecycle import OfferLifecycle
        db = self.SF()
        try:
            self.assertEqual(self._lock_timeout(db), "0")
            with OfferLifecycle(db)._bounded_lock_wait(4000):
                self.assertEqual(self._lock_timeout(db), "4s")
            self.assertEqual(self._lock_timeout(db), "0")
        finally:
            db.rollback(); db.close()

    def test_r3_pg_12_a_stricter_caller_bound_is_restored_after_success(self):
        from app.services.offer_lifecycle import OfferLifecycle
        db = self.SF()
        try:
            db.execute(text("SET LOCAL lock_timeout = '250ms'"))
            with OfferLifecycle(db)._bounded_lock_wait(4000):
                self.assertEqual(self._lock_timeout(db), "4s")
            self.assertEqual(self._lock_timeout(db), "250ms",
                             "the helper widened the caller's own stricter bound")
        finally:
            db.rollback(); db.close()

    def test_r3_pg_13_a_stricter_caller_bound_is_restored_when_the_FLUSH_RAISES(self):
        """MUTATION ORACLE for required mutation 8 (RVR2-02). The audit's hostile case.

        R2 put the deliberate `self.db.flush()` BETWEEN the `SET LOCAL` and the `try`, so a
        flush that raised skipped the restore entirely. The review measured a caller's
        777ms bound left installed at 4s — the same invisible-widening defect the helper
        exists to close, moved from its exit to its entry.
        """
        from app.services.offer_lifecycle import OfferLifecycle
        db = self.SF()
        try:
            db.execute(text("SET LOCAL lock_timeout = '777ms'"))
            self.assertEqual(self._lock_timeout(db), "777ms")
            lc = OfferLifecycle(db)
            boom = RuntimeError("the deliberate flush failed")

            def exploding_flush(*a, **k):
                raise boom

            db.flush = exploding_flush
            entered = False
            try:
                with lc._bounded_lock_wait(4000):
                    entered = True                  # pragma: no cover - must not happen
            except RuntimeError as exc:
                self.assertIs(exc, boom,
                              "the original exception was replaced by the helper's own")
            else:                                   # pragma: no cover - must not happen
                self.fail("the raising flush was swallowed")
            finally:
                del db.flush
            self.assertFalse(entered, "the body ran although the flush had failed")
            self.assertEqual(
                self._lock_timeout(db), "777ms",
                "the caller's 777ms bound was left at %s after the flush raised — the "
                "deliberate flush is outside the protected region"
                % self._lock_timeout(db))
        finally:
            db.rollback(); db.close()

    def test_r3_pg_14_nested_bounds_restore_their_IMMEDIATE_outer_value(self):
        from app.services.offer_lifecycle import OfferLifecycle
        db = self.SF()
        try:
            db.execute(text("SET LOCAL lock_timeout = '900ms'"))
            lc = OfferLifecycle(db)
            seen = [self._lock_timeout(db)]
            with lc._bounded_lock_wait(4000):
                seen.append(self._lock_timeout(db))
                with lc._bounded_lock_wait(1000):
                    seen.append(self._lock_timeout(db))
                seen.append(self._lock_timeout(db))
            seen.append(self._lock_timeout(db))
            self.assertEqual(seen, ["900ms", "4s", "1s", "4s", "900ms"],
                             "nested bounds do not compose: %r" % (seen,))
        finally:
            db.rollback(); db.close()

    def test_r3_pg_15_a_database_error_leaves_the_session_reusable_after_rollback(self):
        from app.services.offer_lifecycle import OfferLifecycle
        db = self.SF()
        try:
            lc = OfferLifecycle(db)
            with self.assertRaises(Exception):
                with lc._bounded_lock_wait(4000):
                    db.execute(text("SELECT * FROM a_table_that_does_not_exist"))
            db.rollback()
            self.assertEqual(db.execute(text("SELECT 1")).scalar(), 1)
            self.assertEqual(self._lock_timeout(db), "0",
                             "the rollback did not clear the transaction-local bound")
        finally:
            db.rollback(); db.close()

    def test_r3_pg_16_a_pooled_connection_does_not_inherit_a_widened_bound(self):
        """`SET LOCAL` is transaction-scoped; a later session must start clean."""
        from app.services.offer_lifecycle import OfferLifecycle
        db = self.SF()
        try:
            with OfferLifecycle(db)._bounded_lock_wait(4000):
                self.assertEqual(self._lock_timeout(db), "4s")
            db.commit()
        finally:
            db.close()
        db2 = self.SF()
        try:
            self.assertEqual(self._lock_timeout(db2), "0",
                             "a pooled connection carried a widened lock_timeout into a "
                             "new session")
        finally:
            db2.rollback(); db2.close()


@unittest.skipUnless(PG_URL and ALEMBIC_URL,
                     "G37R_PG_URL and G37R_PG_ALEMBIC_URL both required — the two schemas "
                     "must be built two different ways or nothing is compared")
class G37RR3ForeignKeyNameAgreement(unittest.TestCase):
    """§6C / RVR2-03. `create_all()` and Alembic must name the FK the SAME thing.

    R2's mutation `rv10` — delete the explicit `name=` from the model's `ForeignKey` — had NO
    test at all, sealed or behavioural. Left implicit, SQLAlchemy generates
    `thread_revisions_offer_id_fkey` while the migration creates
    `fk_thread_revisions_offer_id`, so a database built by `create_all` and one built by
    Alembic disagree about the identifier — and `downgrade()` DROPS BY THE MIGRATION'S NAME,
    which is how the drift becomes a failed downgrade rather than a cosmetic difference.

    Both builds are performed here, in one disposable database, because that is the only way
    the comparison is real: `metadata.create_all()` first, then the migration's own
    `downgrade()` — which must be able to drop what `create_all` made — and then its
    `upgrade()`, which rebuilds the constraint through Alembic. Agreement is asserted as an
    equality of the two names, not as two separate expectations.
    """

    FK = "fk_thread_revisions_offer_id"
    INDEX = "uq_thread_revision_one_booking_per_offer"

    _FK_SQL = ("SELECT con.conname FROM pg_constraint con "
               "JOIN pg_class rel ON rel.oid = con.conrelid "
               "JOIN pg_attribute att ON att.attrelid = rel.oid "
               "                     AND att.attnum = con.conkey[1] "
               "WHERE con.contype = 'f' AND rel.relname = 'thread_revisions' "
               "  AND att.attname = 'offer_id'")

    @classmethod
    def setUpClass(cls):
        """Build the schema BOTH ways, in order, and record what each build named."""
        import app.models  # noqa: F401  — registers the tables on the metadata
        from alembic import command
        from alembic.config import Config
        from app.db import Base

        cls.engine = create_engine(ALEMBIC_URL)
        cls.stages = {}
        Base.metadata.create_all(cls.engine)
        cls.stages["create_all"] = cls._fk_names()

        cfg = Config(str(Path(_BACKEND) / "alembic.ini"))
        cfg.set_main_option("script_location", str(Path(_BACKEND) / "migrations"))
        prior = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = ALEMBIC_URL
        try:
            command.stamp(cfg, "head")
            # Convergence Phase 1: the head is now a MERGE revision, from which a relative
            # `-1` is ambiguous. Name the parent `-1` meant on Track A, so exactly the booking
            # projection migration (and the no-op merge) is undone, as before.
            command.downgrade(cfg, "20261010_offer_lifecycle")
            cls.stages["downgrade"] = cls._fk_names()
            cls.stages["downgrade_index"] = cls._index_present()
            command.upgrade(cfg, "head")
            cls.stages["alembic"] = cls._fk_names()
            cls.stages["alembic_index"] = cls._index_present()
        finally:
            if prior is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prior

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    @classmethod
    def _fk_names(cls):
        with cls.engine.begin() as c:
            return sorted(r[0] for r in c.execute(text(cls._FK_SQL)))

    @classmethod
    def _index_present(cls):
        with cls.engine.begin() as c:
            return bool(c.execute(text("SELECT 1 FROM pg_indexes WHERE indexname=:n"),
                                  {"n": cls.INDEX}).scalar())

    def test_r3_pg_17_create_all_names_the_projection_foreign_key(self):
        from app.models import BOOKING_PROJECTION_FK
        self.assertEqual(BOOKING_PROJECTION_FK, self.FK)
        self.assertEqual(self.stages["create_all"], [self.FK],
                         "a schema built by metadata.create_all() names the booking "
                         "projection foreign key %r, not %r"
                         % (self.stages["create_all"], self.FK))

    def test_r3_pg_18_the_migration_can_drop_what_create_all_made(self):
        """The drift's actual consequence: `downgrade()` drops by the migration's name."""
        self.assertEqual(self.stages["downgrade"], [],
                         "downgrade() left %r behind — it could not drop the constraint "
                         "create_all() had made, which is the drift defect itself"
                         % (self.stages["downgrade"],))
        self.assertFalse(self.stages["downgrade_index"],
                         "downgrade() left the projection index behind")

    def test_r3_pg_19_alembic_names_the_projection_foreign_key_the_same_thing(self):
        self.assertEqual(self.stages["alembic"], [self.FK],
                         "a schema rebuilt by Alembic names the booking projection "
                         "foreign key %r, not %r" % (self.stages["alembic"], self.FK))
        self.assertTrue(self.stages["alembic_index"],
                        "the re-upgrade did not restore the one-booking-per-offer index")
        # The property that actually matters, as an equality of the two builds.
        self.assertEqual(self.stages["create_all"], self.stages["alembic"],
                         "create_all() and Alembic disagree about the booking projection "
                         "foreign key's name: %r vs %r"
                         % (self.stages["create_all"], self.stages["alembic"]))


class G37RR3ForeignKeyNameDeclaration(unittest.TestCase):
    """MUTATION ORACLE for required mutation 10 — and it needs no database.

    The behavioural agreement above runs only where a disposable PostgreSQL exists. R2's
    `rv10` survived because NOTHING failed on it; this fails in the default gate, which is
    where a control with no witness becomes a control.
    """

    def test_r3_21_the_name_comes_from_the_models_declaration(self):
        import ast
        import inspect
        import app.models as models
        tree = ast.parse(inspect.getsource(models))
        cls = next(n for n in tree.body
                   if isinstance(n, ast.ClassDef) and n.name == "ThreadRevision")
        found = []
        for node in ast.walk(cls):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "ForeignKey" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "whatsapp_booking_offers.id"):
                found.append(sorted(k.arg for k in node.keywords))
        self.assertEqual(len(found), 1,
                         "expected exactly one ForeignKey to the offer table")
        self.assertIn("name", found[0],
                      "ThreadRevision.offer_id's ForeignKey has no explicit name — "
                      "create_all() and Alembic will disagree again, and downgrade() "
                      "drops by the migration's name")
        self.assertEqual(models.BOOKING_PROJECTION_FK, "fk_thread_revisions_offer_id")
        # And the migration must IMPORT the declaration rather than repeat the string.
        mig = (Path(_BACKEND) / "migrations" / "versions"
               / "20261013_booking_projection.py").read_text(encoding="utf-8")
        self.assertIn("from app.models import BOOKING_PROJECTION_FK", mig,
                      "the migration does not import the one declared name")


if __name__ == "__main__":                          # pragma: no cover
    unittest.main(verbosity=2)
