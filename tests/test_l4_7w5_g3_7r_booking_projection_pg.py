"""G3-7R — one offer, at most one booking, PROVEN ON REAL POSTGRESQL (P1–P8).

Disposable database only (`G37R_PG_URL`); skips cleanly without it and says so, because a
concurrency claim that did not run is not a weaker claim, it is no claim at all.

WHY THIS FILE EXISTS, STATED AS A MEASUREMENT. The G3-7P-R3A independent review removed both
of the application guards the offer authority relied on — the `SELECT ... FOR UPDATE` on the
offer row and the conditional `UPDATE ... WHERE status = 'ISSUED'` — and then ran the whole
suite. It stayed identity-identical: 6,000-odd tests, same failures, nothing new. With the
guards gone, two concurrent confirmations of ONE offer produced TWO bookings in 6 of 6
trials, at two different slots that were both genuinely free, so no calendar conflict caught
them either. The milestone's central safety property had no oracle at all.

So every test here drives the REAL writers against real persisted rows, and asserts on
STATE — bookings, offer status, refusal and replay counters — never on source text. And the
guard the milestone adds has a DIRECT oracle: P3 removes the index in a disposable mutation
and requires the duplicate to appear.

    The advisory lock is coordination. The exclusion constraint is the guarantee.
"""
from __future__ import annotations

import os
import sys
import threading
from unittest import mock
import time as _t
import unittest
from datetime import date, time, timedelta
from pathlib import Path

_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

PG_URL = os.environ.get("G37R_PG_URL", "")
# ── Convergence Phase 3 (adaptation, whole module) ─────────────────────────────────────────
# The Conversation Engine and the Data Exchange no longer book in their own sessions: both
# delegate to `BookingAuthority`, which (a) is reachable only with SMART_BOOKING_CE_ENABLED on
# — the OFF switch for both Flow transports, now read from `get_settings()` — and (b) books
# against the launch professional. These suites prove the offer authority's and the booking
# projection's guarantees, so they run the converged writer: the flag is ON for this module
# and every fixture world has a (synthetic) launch professional. Nothing else changes.
_P3_PATCHES = []


def setUpModule():
    from offer_fixture import start_converged_booking
    _P3_PATCHES.extend(start_converged_booking())


def tearDownModule():
    from offer_fixture import stop_converged_booking
    stop_converged_booking(_P3_PATCHES)


WA = "549110000778"
BOOKING_PAYLOAD = {
    "nombre_apellido": "Probe Cliente", "email": "p@example.test", "telefono": WA,
    "como_llego": "Instagram", "direccion": "Palermo 123", "tipo_vendedor": "particular",
}
BOOKING_FLOW_ID = "28104222025943520"

#: Threads named here are held inside the bypassed `claim()`, between accepting the offer
#: and inserting their booking. See `_Bypass` item (4): it is what makes the index reachable
#: deterministically instead of depending on which writer wins a race.
CLAIM_GATES: dict = {}


def _gate(name: str) -> dict:
    gate = {"arrived": threading.Event(), "release": threading.Event()}
    CLAIM_GATES[name] = gate
    return gate


def _clear_gates() -> None:
    CLAIM_GATES.clear()


@unittest.skipUnless(PG_URL, "G37R_PG_URL not set — no disposable PostgreSQL; nothing proven")
class G37RBookingProjectionPG(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_URL, pool_size=16, max_overflow=8)
        cls.SF = sessionmaker(bind=cls.engine, expire_on_commit=False)
        with cls.engine.begin() as c:
            # Both halves of the guarantee must exist, or every test here is vacuous.
            assert c.execute(text("SELECT to_regclass('whatsapp_booking_offers')")).scalar()
            assert c.execute(text(
                "SELECT 1 FROM pg_indexes WHERE indexname = "
                "'uq_thread_revision_one_booking_per_offer'")).scalar(), \
                "the booking projection index is absent — nothing below proves anything"

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    # ── fixture ──────────────────────────────────────────────────────────────

    def setUp(self):
        import app.models as M
        self.M = M
        # ── G3-7R: release every pooled connection before truncating ────────────
        # `thread_revisions.offer_id` is a new foreign key INTO
        # `whatsapp_booking_offers`, so `TRUNCATE whatsapp_booking_offers ... CASCADE` now
        # needs an ACCESS EXCLUSIVE lock on `thread_revisions` as well. Any pooled
        # connection still sitting `idle in transaction` — one a test left behind after
        # reading a ThreadRevision — therefore blocks the truncate, where before the FK
        # existed it did not. Measured: the suite stalled indefinitely at the same test.
        #
        # Disposing the pool here closes those connections and rolls their transactions
        # back, so the fixture no longer depends on every test having closed its session.
        # Safe at this point: no test thread is running.
        self.engine.dispose()
        # And terminate anything still attached to this DISPOSABLE database from outside
        # the pool. The concurrency tests in these suites deliberately hold locks in
        # worker threads and `join(timeout=...)`; a worker that outlives its join keeps
        # resource 1 of OFFER_LOCK_ORDER, which since G3-7R every offer writer takes — so
        # the next test's own writes would block behind it, forever, with no diagnosis.
        # Measured: the suite stalled indefinitely with `pg_stat_activity` showing exactly
        # that shape. This database exists only for this run, so terminating other
        # backends on it costs nothing and makes the suite deterministic rather than
        # order-dependent.
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
        from offer_fixture import ensure_launch_professional
        ensure_launch_professional(db)   # Convergence Phase 3: the authority books against it
        ct = M.WhatsAppContact(wa_id=WA, display_name="Probe"); db.add(ct); db.flush()
        lead = M.Lead(nombre="Probe", apellido="Cliente", telefono=WA,
                      estado="CONSULTA_NUEVA", flag="ACEPTADO"); db.add(lead); db.flush()
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

    # ── the real engine, with ONLY the external transport stubbed ────────────

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

    def _svc(self, db):
        from app.services.booking_flow_service import BookingFlowService
        svc = BookingFlowService(db)
        svc._send_booking_receipt = lambda *a, **k: None
        return svc

    # ── slots that are genuinely open, asked of the authority that decides ───

    def _open_slots(self, how_many=2):
        """`[(day_iso, 'HH:MM'), ...]` for real openings, from `ScheduleService` itself.

        Never literal dates. The suite already carries one Sunday-dependent family whose
        failures appear and disappear with the day of the week; a hard-coded day would add a
        second, and would start failing the moment it drifted into the past.

        The slots returned are on DIFFERENT days, deliberately. P1 requires that the second
        booking not be caught by a same-slot calendar conflict — that would prove the
        calendar works, not that the offer projection does.
        """
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

    def _issue(self, day_iso=None, hhmm=None):
        """Issue a real offer through the real dispatch path."""
        if day_iso:
            self._arm(day_iso, hhmm)
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

    # ── observations ─────────────────────────────────────────────────────────

    def _bookings(self):
        db = self.SF()
        rows = db.execute(text(
            "SELECT id, offer_id, scheduled_date, scheduled_time FROM thread_revisions "
            "WHERE thread_id=:t AND status='booked' ORDER BY id"),
            {"t": self.thread_id}).fetchall()
        db.close()
        return [tuple(r) for r in rows]

    def _revisions(self):
        db = self.SF()
        n = db.execute(text("SELECT count(*) FROM revisions")).scalar()
        db.close()
        return int(n)

    def _offers(self):
        db = self.SF()
        rows = db.execute(text(
            "SELECT id, status, terminal_reason, refused_count, last_refusal, "
            "replay_count, accepted_thread_revision_id FROM whatsapp_booking_offers "
            "WHERE thread_id=:t ORDER BY id"), {"t": self.thread_id}).fetchall()
        db.close()
        return [dict(zip(("id", "status", "terminal_reason", "refused_count",
                          "last_refusal", "replay_count", "accepted_tr"), r))
                for r in rows]

    def _dx_payload(self, day_iso, hhmm):
        return {"date": day_iso, "time": hhmm, "name": "Probe Cliente",
                "email": "p@example.test", "inspection_address": "Palermo, CABA",
                "seller_name": "Vendedor", "seller_phone": "1122334455"}

    def _dx_confirm(self, token, day_iso, hhmm, *, bypass=False):
        """The real Data Exchange writer. Returns ('ok', screen) or ('exc', type, text)."""
        db = self.SF()
        try:
            with _Bypass(enabled=bypass):
                out = self._svc(db).handle_confirm_booking(
                    token, self._dx_payload(day_iso, hhmm))
            return ("ok", out.get("screen"))
        except Exception as exc:
            return ("exc", type(exc).__name__, str(exc)[:220])
        finally:
            db.close()

    def _ce_submit(self, token, *, tag="x", bypass=False):
        from app.schemas.conversation import ConversationHandleIn
        db = self.SF()
        db.execute(text("UPDATE whatsapp_thread_states SET needs_human=false "
                        "WHERE thread_id=:t"), {"t": self.thread_id})
        db.commit(); db.close()
        db, eng = self._engine()
        try:
            with _Bypass(enabled=bypass):
                out = eng.handle(ConversationHandleIn(
                    thread_id=self.thread_id, wa_message_id=f"wamid.{tag}.{_t.time()}",
                    wa_id=WA, message_type="flow_response",
                    flow_response=dict(BOOKING_PAYLOAD), flow_token=token))
            return out
        finally:
            db.close()

    def _race_for_the_index(self, token, slot_a, slot_b):
        """Two bypassed confirmations that BOTH reach their insert. `{'a':…, 'b':…}`.

        The winner is held inside `claim()` until the loser has passed admission, the
        bindings and availability and is at its own insert — so both transactions are open
        when they insert, and the partial unique index is the only thing left that can
        refuse one of them. Without that ordering the loser is stopped by the transport's
        own mirror check, and the probe measures the application layer while appearing to
        measure the index.
        """
        results = {}
        gate = _gate("GATE_A")
        # Convergence Phase 3: ONE bypass around the whole race (never nested), and the
        # index's translation is observed so a loser's SUCCESS is attributable to it.
        import app.services.booking_authority as BA
        translated = []
        real_replay = BA.BookingAuthority._projection_winner_replay

        def observe(auth, offer_id, cmd):
            r = real_replay(auth, offer_id, cmd)
            translated.append((threading.current_thread().name, r.outcome.value, r.reason))
            return r
        try:
            with _Bypass(enabled=True), \
                    mock.patch.object(BA.BookingAuthority, "_projection_winner_replay", observe):
                def run_a():
                    results["a"] = self._dx_confirm(token, *slot_a)

                def run_b():
                    results["b"] = self._dx_confirm(token, *slot_b)

                a = threading.Thread(target=run_a, name="GATE_A")
                a.start()
                self.assertTrue(gate["arrived"].wait(timeout=60),
                                "the held writer never reached its claim")
                b = threading.Thread(target=run_b, name="GATE_B")
                b.start()
                b.join(timeout=60)
                gate["release"].set()
                a.join(timeout=60)
        finally:
            _clear_gates()
        results["translated"] = translated
        return results

    # ══ P1 — the database guarantee BENEATH the application locks ════════════

    def test_p1_two_concurrent_confirmations_two_valid_slots_one_booking(self):
        """P1. One offer, two concurrent confirmations, two DIFFERENT genuinely-free slots.

        The bypass is declared and named (`_Bypass`): it removes the thread-state
        acquisition, the offer row's `FOR UPDATE` and the conditional `UPDATE ... WHERE
        status = 'ISSUED'`. What remains between one offer and two bookings is the partial
        unique index and nothing else.

        The two slots are on different days ON PURPOSE. A same-slot race would be caught by
        the calendar's own overlap check, and a test that passes because of THAT has proved
        the calendar works, not the guarantee.
        """
        slots = self._open_slots(2)
        (d1, t1), (d2, t2) = slots[0], slots[1]
        token = self._issue(d1, t1)

        results = self._race_for_the_index(token, (d1, t1), (d2, t2))

        bookings = self._bookings()
        self.assertEqual(len(bookings), 1,
                         f"exactly one booking must exist; got {bookings} from {results}")
        self.assertEqual(len(set(b[0] for b in bookings)), 1)
        self.assertEqual(self._revisions(), 1, "exactly one CRM Revision")
        self.assertIsNotNone(bookings[0][1], "the booking must name the offer that authorized it")

        # Convergence Phase 3 adaptation. OLD: the loser raised `BookingTokenError` /
        # `BookingAlreadyCompletedError` (the in-session writer's translation). NEW: the
        # authority translates the index violation into a PROVEN replay of the winner
        # (`_projection_winner_replay`, Phase 2), so the held writer — the loser — answers the
        # winner's SUCCESS. The attribution is the same: it was the INDEX that stopped it,
        # never the calendar, and nothing internal reached the Flow.
        translated = results.pop("translated")
        self.assertEqual(translated, [("GATE_A", "IDEMPOTENT_REPLAY", "PROJECTION_WINNER_REPLAY")],
                         f"the index did not decide the loser: {results} {translated}")
        self.assertEqual((results["a"], results["b"]), (("ok", "SUCCESS"), ("ok", "SUCCESS")),
                         f"both answers are the one booking's SUCCESS: {results}")
        for forbidden in ("IntegrityError", "23505", "uq_thread_revision", "psycopg"):
            self.assertNotIn(forbidden, str(results),
                             f"the loser was shown database internals: {forbidden}")

    # ══ P2 — guard attribution ══════════════════════════════════════════════

    def test_p2_each_protection_is_separately_attributable(self):
        """P2. WHICH protection refused the loser, proved one protection at a time.

        Four distinguishable refusals, each produced by removing everything above it:

          app-conditional  both app guards present  -> OFFER_ALREADY_ACCEPTED, no index hit
          projection       both app guards removed  -> the index refuses
          schedule         same slot, guards present-> the calendar refuses
        """
        slots = self._open_slots(2)
        (d1, t1), (d2, t2) = slots[0], slots[1]

        # ── (a) with the application guards IN PLACE, the conditional UPDATE decides ──
        token = self._issue(d1, t1)
        first = self._dx_confirm(token, d1, t1)
        self.assertEqual(first[0], "ok", first)
        second = self._dx_confirm(token, d2, t2)
        # Convergence Phase 3 adaptation: the application layer that answers is now the
        # booking LEDGER (asked first), so the second confirmation returns the committed
        # booking's SUCCESS and the replay is counted on the ledger row, not the offer.
        self.assertEqual(second, ("ok", "SUCCESS"), second)
        self.assertEqual(len(self._bookings()), 1)
        offers = self._offers()
        self.assertEqual(offers[-1]["status"], "ACCEPTED")
        db = self.SF()
        ledger_replays = db.execute(text("SELECT replay_count FROM booking_commands")).scalars().all()
        db.close()
        self.assertEqual(ledger_replays, [1],
                         "the second confirmation was a replay of a committed booking")
        self.assertEqual(offers[-1]["refused_count"], 0,
                         "a replay must never be counted as a refusal")

        # ── (b) with them REMOVED, the index is the only thing left ──────────
        # Raced, not sequential, and through the same helper P1 and P3 use. Sequentially
        # the first confirmation commits and the second is stopped by the transport's own
        # mirror check, which is the application layer again — the index would never be
        # reached and this row would be attributing the refusal to the wrong protection,
        # which is the one thing P2 exists to get right.
        self.setUp()
        slots = self._open_slots(2)
        (d1, t1), (d2, t2) = slots[0], slots[1]
        token = self._issue(d1, t1)
        raced = self._race_for_the_index(token, (d1, t1), (d2, t2))
        self.assertEqual(len(self._bookings()), 1,
                         f"the projection index must stop the second booking: {raced}")
        # Convergence Phase 3 adaptation: translated by the authority into a proven replay.
        self.assertEqual(raced["translated"],
                         [("GATE_A", "IDEMPOTENT_REPLAY", "PROJECTION_WINNER_REPLAY")],
                         f"the index violation must be translated, not leaked: {raced}")

        # ── (c) the CALENDAR, for contrast: same slot, guards in place ───────
        self.setUp()
        (d1, t1), _ = self._open_slots(2)
        db = self.SF()
        M = self.M
        occupying = M.Revision(lead_id=self.lead_id, tipo_vehiculo="AUTO",
                               zone_group="CABA", zone_detail="Palermo",
                               turno_fecha=date.fromisoformat(d1),
                               turno_hora=time.fromisoformat(t1))
        db.add(occupying); db.commit(); db.close()
        token = self._issue(d1, t1)
        c = self._dx_confirm(token, d1, t1)
        self.assertEqual(c[0], "exc", c)
        self.assertEqual(c[1], "BookingSlotConflictError",
                         f"a taken slot must be refused by the calendar: {c}")
        self.assertEqual(len(self._bookings()), 0)
        # And — S5 — the offer stays ISSUED: the slot went, the offer did not.
        self.assertEqual(self._offers()[-1]["status"], "ISSUED",
                         "a vanished slot must not consume the offer")

    # ══ P3 — the structural constraint has a DIRECT oracle ══════════════════

    def test_p3_removing_the_index_produces_the_duplicate(self):
        """P3. MUTATION 1, applied to the disposable database and put back.

        With the application guards bypassed: the index present yields one booking, and the
        index DROPPED yields two. Without this test the new guarantee would have no oracle
        at all — which is precisely the finding that convened this milestone.
        """
        INDEX = "uq_thread_revision_one_booking_per_offer"

        def run_once():
            """Two confirmations CONCURRENTLY, which is what isolates the index.

            Sequentially they do not: the first one commits, and the second one's
            admission then finds an ACCEPTED offer that already names a booking and
            returns a REPLAY — so the second booking is stopped by the replay path and the
            index is never reached. That is correct production behaviour and it is proved
            in P8; it is simply not an oracle for THIS guarantee.

            Nor is a plain barrier enough: with both threads released at once, the winner
            can finish its whole transaction first and the loser is then stopped by the
            transport's mirror check. `_race_for_the_index` holds the winner inside
            `claim()` until the loser is at its own insert, which is the only arrangement
            in which the index is what decides.
            """
            self.setUp()
            slots = self._open_slots(2)
            (d1, t1), (d2, t2) = slots[0], slots[1]
            token = self._issue(d1, t1)
            self._race_for_the_index(token, (d1, t1), (d2, t2))
            return self._bookings()

        with_index = run_once()
        self.assertEqual(len(with_index), 1,
                         f"constraint present must yield ONE booking: {with_index}")

        with self.engine.begin() as c:
            ddl = c.execute(text(
                "SELECT indexdef FROM pg_indexes WHERE indexname = :n"),
                {"n": INDEX}).scalar()
            self.assertTrue(ddl, "the index must exist before it can be mutated away")
            c.execute(text(f"DROP INDEX {INDEX}"))
        try:
            without_index = run_once()
        finally:
            # The duplicate rows the mutation just PROVED are themselves what blocks the
            # index from being recreated — `CREATE UNIQUE INDEX` refuses a table that
            # already violates it, which is itself a second, independent confirmation that
            # the duplicate is real. So the measurement is taken first, the disposable
            # table is emptied, and only then is the index put back.
            with self.engine.begin() as c:
                c.execute(text("TRUNCATE thread_revisions RESTART IDENTITY CASCADE"))
                c.execute(text(ddl))
            with self.engine.begin() as c:
                restored = c.execute(text(
                    "SELECT 1 FROM pg_indexes WHERE indexname = :n"), {"n": INDEX}).scalar()
            self.assertTrue(restored, "the disposable mutation must be put back")

        # Convergence Phase 3 adaptation. OLD: with the index dropped, TWO bookings appeared.
        # NEW: the authority records every command in the booking LEDGER in the same
        # transaction, and both confirmations of one Flow token carry the same `flow:` key —
        # so the ledger's UNIQUE key is a SECOND database guarantee, and with only the
        # projection index dropped there is still ONE booking. Dropping BOTH is what produces
        # the duplicate, which proves each is sufficient on its own and that neither is
        # vacuous. (Strictly stronger than the parent: two independent oracles, not one.)
        self.assertEqual(len(without_index), 1,
                         f"the ledger key must still stop the duplicate: {without_index}")
        LEDGER = "uq_booking_commands_idempotency_key"
        with self.engine.begin() as c:
            c.execute(text(f"DROP INDEX {INDEX}"))
            c.execute(text(f"ALTER TABLE booking_commands DROP CONSTRAINT {LEDGER}"))
        try:
            without_both = run_once()
        finally:
            with self.engine.begin() as c:
                c.execute(text("TRUNCATE thread_revisions RESTART IDENTITY CASCADE"))
                c.execute(text("TRUNCATE booking_commands RESTART IDENTITY CASCADE"))
                c.execute(text(ddl))
                c.execute(text(f"ALTER TABLE booking_commands ADD CONSTRAINT {LEDGER} "
                               "UNIQUE (idempotency_key)"))
            with self.engine.begin() as c:
                restored = c.execute(text(
                    "SELECT count(*) FROM pg_constraint WHERE conname = :n"),
                    {"n": LEDGER}).scalar()
            self.assertEqual(restored, 1, "the disposable mutation must be put back")
        self.assertEqual(
            len(without_both), 2,
            "with BOTH database guarantees dropped and the application guards bypassed, TWO "
            f"bookings must appear — otherwise this suite cannot see them: {without_both}")
        self.assertNotEqual(without_both[0][2], without_both[1][2],
                            "the two bookings must be at two different dates, so no "
                            "calendar conflict could have been involved")

    # ══ P4 — the transport race ═════════════════════════════════════════════

    def test_p4_ce_against_data_exchange_on_one_offer(self):
        """P4. The two real writers, raced on one offer, in both orders and on both the same
        and different submitted slots."""
        for same_slot in (True, False):
            for ce_first in (True, False):
                with self.subTest(same_slot=same_slot, ce_first=ce_first):
                    self.setUp()
                    slots = self._open_slots(2)
                    (d1, t1), (d2, t2) = slots[0], slots[1]
                    token = self._issue(d1, t1)
                    dx_slot = (d1, t1) if same_slot else (d2, t2)

                    results = {}
                    barrier = threading.Barrier(2)

                    def run_ce():
                        barrier.wait(timeout=20)
                        if not ce_first:
                            _t.sleep(0.05)
                        try:
                            results["ce"] = self._ce_submit(token, tag="race").action
                        except Exception as exc:          # pragma: no cover
                            results["ce"] = f"exc:{type(exc).__name__}"

                    def run_dx():
                        barrier.wait(timeout=20)
                        if ce_first:
                            _t.sleep(0.05)
                        results["dx"] = self._dx_confirm(token, *dx_slot)

                    threads = [threading.Thread(target=run_ce),
                               threading.Thread(target=run_dx)]
                    for th in threads:
                        th.start()
                    for th in threads:
                        th.join(timeout=60)

                    bookings = self._bookings()
                    offers = self._offers()
                    accepted = [o for o in offers if o["status"] == "ACCEPTED"]
                    self.assertLessEqual(len(bookings), 1,
                                         f"at most one booking: {bookings} {results}")
                    self.assertLessEqual(len(accepted), 1,
                                         f"at most one accepted offer: {offers}")
                    self.assertLessEqual(self._revisions(), 1, "at most one CRM Revision")
                    if bookings:
                        self.assertEqual(len(accepted), 1,
                                         "a booking implies exactly one accepted offer")
                        self.assertIsNotNone(bookings[0][1],
                                             "the booking must name its offer")
                        self.assertEqual(accepted[0]["accepted_tr"], bookings[0][0])
                    # No deadlock, ever.
                    for value in results.values():
                        blob = str(value)
                        self.assertNotIn("40P01", blob, f"deadlock in {results}")
                        self.assertNotIn("OFFER_DEADLOCK", blob, f"deadlock in {results}")
                    # No contradictory refusal state: an accepted offer whose booking
                    # exists must not also be carrying a refusal from this race.
                    if bookings and accepted:
                        self.assertEqual(
                            accepted[0]["refused_count"], 0,
                            f"the winning offer must not also be refused: {offers}")

    # ══ P5 — ordinary-message isolation ═════════════════════════════════════

    def test_p5_ordinary_messages_are_not_serialised_by_a_booking(self):
        """P5. The property R3A protected with a predicate, re-proved without one.

        While a booking transaction holds its locks for 1, 6 and 12 seconds, ordinary
        conversation turns must complete independently and none may be lost. The R3 review
        measured the failure this replaces: the turn-entry acquisition DECLINED the second
        turn after 4.05s on a thread the parent commit served in 0.05–0.20s.
        """
        from app.schemas.conversation import ACTION_THREAD_CONTENDED
        from app.services.offer_lifecycle import OfferLifecycle

        for hold_s in (1, 6, 12):
            with self.subTest(hold_seconds=hold_s):
                self.setUp()
                (d1, t1), _ = self._open_slots(2)
                token = self._issue(d1, t1)

                holding = threading.Event()
                release = threading.Event()

                def hold():
                    """Take the booking locks for real and keep them."""
                    db = self.SF()
                    try:
                        offers = OfferLifecycle(db)
                        offers.begin_offer_transaction(self.thread_id)
                        db.execute(text(
                            "SELECT 1 FROM whatsapp_booking_offers WHERE thread_id=:t "
                            "FOR UPDATE"), {"t": self.thread_id}).fetchall()
                        holding.set()
                        release.wait(timeout=hold_s + 20)
                        db.rollback()
                    finally:
                        db.close()

                holder = threading.Thread(target=hold)
                holder.start()
                self.assertTrue(holding.wait(timeout=20), "the holder never acquired")

                # Ordinary traffic, while the booking locks are held.
                from app.schemas.conversation import ConversationHandleIn
                observed = []
                lock_sql = []
                for i in range(3):
                    db, eng = self._engine()
                    try:
                        # Observe whether this turn issues ANY booking lock statement.
                        original = db.execute

                        def spy(stmt, *a, **k):
                            sql = str(stmt)
                            if ("FOR NO KEY UPDATE" in sql or "FOR UPDATE" in sql
                                    or "pg_try_advisory" in sql):
                                lock_sql.append(sql)
                            return original(stmt, *a, **k)

                        db.execute = spy
                        started = _t.perf_counter()
                        out = eng.handle(ConversationHandleIn(
                            thread_id=self.thread_id,
                            wa_message_id=f"wamid.ord.{hold_s}.{i}.{_t.time()}",
                            wa_id=WA, message_type="text",
                            text="hola, una consulta sobre la revision"))
                        observed.append((out.action, _t.perf_counter() - started))
                    finally:
                        db.execute = original
                        db.close()

                release.set()
                holder.join(timeout=30)

                for action, elapsed in observed:
                    self.assertNotEqual(
                        action, ACTION_THREAD_CONTENDED,
                        f"an ordinary turn was declined while a booking held the thread "
                        f"({hold_s}s hold): {observed}")
                    self.assertLess(
                        elapsed, float(hold_s),
                        f"an ordinary turn waited for the booking ({hold_s}s hold): "
                        f"{observed}")
                self.assertEqual(
                    lock_sql, [],
                    f"an ordinary turn issued a booking lock statement: {lock_sql[:2]}")

                # ── and no message was silently lost ─────────────────────────
                # Asserted on OWNERSHIP and on an ANSWER, not on the dedup cursor.
                # Measured on this branch AND on its parent 98eff4d, with no booking
                # holder present at all: an ordinary text turn on the FAQ/service-gate
                # path returns `replied` and leaves
                # `last_processed_inbound_wa_message_id` NULL. That is pre-existing
                # behaviour of that path, identical before and after G3-7R (recorded as
                # observation G37R-O1), so a cursor assertion here would be testing
                # something this milestone neither owns nor changed — and would mask what
                # P5 is actually for.
                #
                # What P5 owns is that a booking in flight costs an ordinary turn nothing:
                # CE still owned every turn, every turn produced a reply, and none was
                # declined. A lost message on this path shows up as `thread_contended`,
                # as `error`, or as handled=False handing the turn to n8n's legacy
                # booking chain — and all three are excluded.
                for action, _elapsed in observed:
                    self.assertIn(action, ("replied", "flow_button_sent",
                                           "booking_created", "skipped_human"),
                                  f"an ordinary turn produced no answer: {observed}")
                    self.assertNotEqual(action, "error", f"{observed}")
                self.assertEqual(len(observed), 3,
                                 f"every ordinary turn must complete: {observed}")

    # ══ P6 — the ISSUE/acceptance race ══════════════════════════════════════

    def test_p6_issue_against_acceptance_never_deadlocks(self):
        """P6. The architecture audit measured `40P01 deadlock_detected` in 3/3 trials here,
        with the acceptance writer the victim every time and the event misreported as
        OFFER_CONTENDED. `issue()` held the offer row through `supersede()` and then wanted
        the thread-state row at commit, where the transport-mirror write flushes.

        S1 brought ISSUE under the protocol. Zero deadlocks, in either direction, over
        repeated trials.
        """
        TRIALS = 6
        seen = []
        for trial in range(TRIALS):
            self.setUp()
            slots = self._open_slots(2)
            (d1, t1), (d2, t2) = slots[0], slots[1]
            token = self._issue(d1, t1)

            results = {}
            barrier = threading.Barrier(2)

            def do_issue():
                barrier.wait(timeout=20)
                try:
                    results["issue"] = ("ok", self._issue(d2, t2))
                except Exception as exc:
                    results["issue"] = ("exc", type(exc).__name__, str(exc)[:200])

            def do_accept():
                barrier.wait(timeout=20)
                results["accept"] = self._dx_confirm(token, d1, t1)

            threads = [threading.Thread(target=do_issue),
                       threading.Thread(target=do_accept)]
            for th in threads:
                th.start()
            for th in threads:
                th.join(timeout=60)
            seen.append(dict(results))

            blob = str(results) + str(self._offers())
            self.assertNotIn("40P01", blob, f"trial {trial}: deadlock_detected — {results}")
            self.assertNotIn("OFFER_DEADLOCK", blob,
                             f"trial {trial}: a deadlock was classified — {results}")
            # Valid terminal states only, and at most one booking.
            for offer in self._offers():
                self.assertIn(offer["status"],
                              ("ISSUED", "ACCEPTED", "SUPERSEDED", "WITHDRAWN", "EXPIRED"))
            self.assertLessEqual(len(self._bookings()), 1,
                                 f"trial {trial}: more than one booking")
            # A committed booking is never lost: if the acceptance said ok, the rows exist.
            if results.get("accept", ("",))[0] == "ok":
                self.assertEqual(len(self._bookings()), 1,
                                 f"trial {trial}: the acceptance reported success and no "
                                 f"booking exists — {results}")
        self.assertEqual(len(seen), TRIALS)

    # ══ P7 — CE stale-slot rejection (invert SAA-F1) ════════════════════════

    def test_p7_ce_refuses_a_slot_an_occupying_revision_already_owns(self):
        """P7. The architecture audit's HIGH, inverted.

        Measured on the parent: with an occupying Revision already at the slot and
        `ScheduleService.check()` returning `valid=False` and NAMING the overlap,
        `ConversationEngine.handle()` answered `booking_created` and wrote a SECOND
        occupying Revision at the identical date and time. The Data Exchange writer refused
        the same submission, so the two transports disagreed about whether a slot was
        bookable.
        """
        (d1, t1), _ = self._open_slots(2)
        db = self.SF()
        M = self.M
        occupying = M.Revision(lead_id=self.lead_id, tipo_vehiculo="AUTO",
                               zone_group="CABA", zone_detail="Palermo",
                               turno_fecha=date.fromisoformat(d1),
                               turno_hora=time.fromisoformat(t1))
        db.add(occupying); db.commit()
        occupying_id = occupying.id
        db.close()
        before = self._revisions()

        # The availability authority must actually be saying no, or this proves nothing.
        from app.schemas.schedule import ScheduleCheckIn
        from app.services.schedule import ScheduleService
        db = self.SF()
        check = ScheduleService(db).check(ScheduleCheckIn(
            address="Palermo, CABA", preferred_day=date.fromisoformat(d1),
            preferred_time=time.fromisoformat(t1), zone_group="CABA",
            zone_detail="Palermo"))
        db.close()
        self.assertFalse(check.valid,
                         "the premise failed: ScheduleService considers the slot free")

        token = self._issue(d1, t1)
        out = self._ce_submit(token, tag="stale")

        self.assertNotEqual(out.action, "booking_created",
                            "CE created a booking the availability authority refused")
        self.assertEqual(len(self._bookings()), 0, "no occupying booking may be created")
        # Convergence Phase 3 adaptation: the refusal now also records the ratified B-6
        # NON-occupying manual-scheduling request (REPROGRAMAR), so the Revision count grows
        # by exactly that one non-occupying row; the occupying count at the slot is still 1.
        db = self.SF()
        rows = db.execute(text(
            "SELECT estado_revision, turno_fecha = :d AND turno_hora = :t FROM revisions "
            "ORDER BY id"), {"d": d1, "t": t1}).fetchall()
        db.close()
        self.assertEqual(self._revisions(), before + 1)
        self.assertEqual([tuple(r) for r in rows][1:], [("REPROGRAMAR", True)],
                         "the only new Revision is the non-occupying manual request")
        # The customer was not told it succeeded: a typed, truthful handoff.
        self.assertEqual(out.action, "booking_refused_handoff")
        self.assertIn(out.detail, ("OVERLAP", "SCHEDULE_REVALIDATION", "EXCLUSION_CONSTRAINT"),
                      "the authority's SLOT_CONFLICT reason")
        # And the offer was NOT accepted: the slot went, the offer did not.
        offers = self._offers()
        self.assertNotEqual(offers[-1]["status"], "ACCEPTED",
                            "a failed revalidation must not mark the offer ACCEPTED")
        self.assertEqual(offers[-1]["last_refusal"], "SLOT_NO_LONGER_AVAILABLE",
                         f"the refusal must be typed and auditable: {offers}")
        # It is still ISSUED, so the customer can pick another slot with the same token.
        self.assertEqual(offers[-1]["status"], "ISSUED")
        self.assertTrue(occupying_id)

    # ══ P8 — replay ═════════════════════════════════════════════════════════

    def test_p8_replay_across_transports_returns_the_original_booking(self):
        """P8. Commit through one transport, deliver the completion through the other.

        The architecture audit measured both halves of this going wrong. The Data Exchange
        side told the customer *"Tu sesión de reserva expiró"* about a booking that had just
        succeeded; the ConversationEngine side refused `OFFER_ALREADY_ACCEPTED`, set
        `needs_human=True`, sent the warm-handoff text, and left the offer reading
        `ACCEPTED, refused_count=1, last_refusal='OFFER_ALREADY_ACCEPTED'` — the shape of a
        rejected submission, on the happy path.
        """
        from app.schemas.conversation import ACTION_BOOKING_REPLAY
        from app.services.booking_flow_service import (BookingAlreadyCompletedError,
                                                       BookingTokenError)

        # ── (a) DX books, CE receives the completion ────────────────────────
        (d1, t1), _ = self._open_slots(2)
        token = self._issue(d1, t1)
        self.assertEqual(self._dx_confirm(token, d1, t1)[0], "ok")
        booking = self._bookings()
        self.assertEqual(len(booking), 1)
        offer_before = self._offers()[-1]

        out = self._ce_submit(token, tag="replay")
        # Convergence Phase 3 adaptation: the ledger answers first ("booking_replayed").
        self.assertEqual(out.action, "booking_replayed",
                         f"the completion of a committed booking is a replay: {out.action}")
        # Convergence Phase 3 adaptation: an acknowledgement is owed by the LEDGER, not
        # assumed sent by "the transport that created the booking". Here the Data Exchange's
        # attempt was blocked by the kill switch (FAILED, bounded retries), so the replay
        # truthfully still owes it and re-attempts it ONCE through the lease; were it SENT,
        # the replay would owe nothing (asserted in the Phase 3 race suite, p3r_09/p3r_13).
        db = self.SF()
        ack = db.execute(text("SELECT ack_status, ack_attempts FROM booking_commands")).one()
        db.close()
        self.assertEqual((ack[0], ack[1]), ("FAILED", 2),
                         "one blocked attempt by the Data Exchange, one by the replay")
        self.assertTrue(out.reply_required, "the owed acknowledgement was re-attempted")
        self.assertEqual(self._bookings(), booking, "no duplicate booking")
        self.assertEqual(self._revisions(), 1)
        after = self._offers()[-1]
        self.assertEqual(after["refused_count"], offer_before["refused_count"],
                         "a replay must not increment refused_count")
        self.assertIsNone(after["last_refusal"],
                          "a replay must not write last_refusal")
        db = self.SF()
        ledger_replays = db.execute(text("SELECT replay_count FROM booking_commands")).scalars().all()
        db.close()
        self.assertEqual(ledger_replays, [1],
                         "a replay must be distinguishable in operator evidence (the ledger)")
        # No contradictory human state written merely because a completion arrived twice.
        db = self.SF()
        needs_human = db.execute(text(
            "SELECT needs_human FROM whatsapp_thread_states WHERE thread_id=:t"),
            {"t": self.thread_id}).scalar()
        db.close()
        # The replay must not WRITE a handoff state. This harness clears `needs_human`
        # before submitting — it has to, or CE's human-takeover guard would return
        # `skipped_human` and the replay would never be reached — so the property under
        # test is the direction: the replay left it False and did not flip it to True.
        # That is exactly the contradiction the architecture audit measured, where
        # `OFFER_ALREADY_ACCEPTED` set `needs_human=True` and sent the warm-handoff text
        # for a booking that had already succeeded.
        self.assertFalse(needs_human,
                         "a replay must not set a human-handoff state merely because a "
                         "completion event arrived twice")

        # ── (b) CE books, DX receives the confirmation again ────────────────
        self.setUp()
        slots = self._open_slots(2)
        (d1, t1), (d2, t2) = slots[0], slots[1]
        token = self._issue(d1, t1)
        out = self._ce_submit(token, tag="ce_first")
        self.assertEqual(out.action, "booking_created", out.action)
        booking = self._bookings()
        self.assertEqual(len(booking), 1)

        again = self._dx_confirm(token, d2, t2)
        # Convergence Phase 3 adaptation: the ledger answers the replay with the committed
        # booking's SUCCESS screen — never an error, never the false-expiry wording.
        self.assertEqual(again, ("ok", "SUCCESS"),
                         f"a replay must not be reported as an expired session: {again}")
        self.assertEqual(self._bookings(), booking, "no duplicate booking")
        after = self._offers()[-1]
        self.assertEqual(after["refused_count"], 0)
        self.assertIsNone(after["last_refusal"])
        db = self.SF()
        self.assertGreaterEqual(db.execute(text(
            "SELECT replay_count FROM booking_commands")).scalar(), 1)
        db.close()
        self.assertTrue(issubclass(BookingAlreadyCompletedError, Exception))
        self.assertFalse(issubclass(BookingAlreadyCompletedError, BookingTokenError))


class _Bypass:
    """THE DECLARED TEST-ONLY BYPASS of the upper application serialization.

    Convergence Phase 3 adaptation. The booking writers this bypass used to open up — the
    Conversation Engine's and the Data Exchange's in-session writers, with `OfferLifecycle`'s
    `begin_offer_transaction` / `claim()` — are deleted; both transports now delegate to
    `BookingAuthority`. So the SAME four items are applied to the authority, by name, and
    nothing else:

        1. K1 — the thread-state row lock (`_lock_thread_state` reads the row, locks nothing)
        2. K2's `SELECT ... FOR UPDATE` on the offer row
        3. K2's conditional `UPDATE ... WHERE status = 'ISSUED'` (and with it the offer
           projection UPDATE and its pre-commit check, which require ACCEPTED), plus the
           ledger replay checks — under the authority a replay check on the shared `flow:` key
           would otherwise stop the second writer before its insert, which is an application
           guard above the index
        4. an optional gate that holds a named thread inside K2, after the bindings were
           checked and before anything is inserted

    What is deliberately NOT removed: the authority's binding comparisons against the offer
    row, `ScheduleService`, the date/professional advisory locks, the ledger's UNIQUE key and
    the booking projection index. The point is to isolate the database guarantees.

    One instance wraps a whole race (`_race_for_the_index`), so it is never nested, and it
    restores the real methods in `__exit__`.
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._saved = {}

    def __enter__(self):
        if not self.enabled:
            return self
        from sqlalchemy import select

        from app.services import booking_authority as BA
        A = BA.BookingAuthority
        for name in ("_lock_thread_state", "_claim_offer", "_accepted_offer_id",
                     "_apply_canonical_state", "_verify_offer_projection", "_check_replay"):
            self._saved[name] = getattr(A, name)
        saved = self._saved

        def no_k1(auth, thread_id, cmd, attachments):
            from app.models import WhatsAppThreadState as S
            return auth.db.execute(select(S).where(S.thread_id == thread_id)
                                   .execution_options(populate_existing=True)).scalar_one()

        def unguarded_claim(auth, acceptance, state):
            """K2 with guards 2 and 3 removed and every BINDING check intact."""
            from app.models import WhatsAppBookingOffer as O
            offer = auth.db.execute(select(O).where(
                O.token_fingerprint == acceptance.token_fingerprint)
                .execution_options(populate_existing=True)).scalar_one_or_none()
            refusal = auth._offer_refusal(offer, acceptance, state)
            if refusal is not None:
                raise BA._Refused(BA._result(BA.BookingOutcome.OFFER_REFUSED, refusal))
            gate = CLAIM_GATES.get(threading.current_thread().name)
            if gate is not None:
                gate["arrived"].set()
                gate["release"].wait(timeout=60)
            return int(offer.id)

        def accepted_any(auth, acceptance):
            if acceptance is None:
                return None
            from app.models import WhatsAppBookingOffer as O
            return int(auth.db.execute(select(O.id).where(
                O.token_fingerprint == acceptance.token_fingerprint)).scalar())

        def no_projection(auth, cmd, plan, revision, tr_id, state, offer_id):
            return saved["_apply_canonical_state"](auth, cmd, plan, revision, tr_id, state, None)

        A._lock_thread_state = no_k1
        A._claim_offer = unguarded_claim
        A._accepted_offer_id = accepted_any
        A._apply_canonical_state = no_projection
        A._verify_offer_projection = lambda auth, *a, **k: None
        A._check_replay = lambda auth, key, fingerprint: None
        return self

    def __exit__(self, *exc):
        if not self.enabled:
            return False
        from app.services import booking_authority as BA
        for name, fn in self._saved.items():
            setattr(BA.BookingAuthority, name, fn)
        return False

if __name__ == "__main__":        # pragma: no cover
    unittest.main(verbosity=2)
