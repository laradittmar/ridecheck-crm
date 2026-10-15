"""G37R-RV-03 — the tester reset, against the booking projection, on real PostgreSQL.

Disposable database only (`RV03_PG_URL`, or `G37R_PG_URL`); skips cleanly without one and
says so, because a reset that was never executed against the real foreign keys is not a
weaker claim, it is no claim at all. SQLite cannot stand in: the whole defect is a
`NO ACTION` foreign-key cycle, and SQLite does not enforce these constraints the same way.

WHAT WAS WRONG, AS MEASURED BY THE G3-7R-R3 INDEPENDENT REVIEW.
`20261013_booking_projection` added `thread_revisions.offer_id`, and
`whatsapp_booking_offers` already pointed the other way through
`accepted_thread_revision_id`. Once a tester completed one booking,
`reset_tester_to_zero_state` failed `23503` and **deleted nothing at all** — so the tester
could never be returned to zero state and no booking Wild could be re-run.

WHAT THIS SUITE FOUND THAT THE REVIEW DID NOT.
Asking `pg_constraint` instead of reading the code shows the offer row blocks the reset at
FOUR points, not one — every `NO ACTION` foreign key leaving the offer table:

    DELETE thread_revisions   -> whatsapp_booking_offers_accepted_thread_revision_id_fkey
    DELETE candidates         -> whatsapp_booking_offers_candidate_id_fkey
    DELETE revisions          -> whatsapp_booking_offers_accepted_revision_id_fkey
    DELETE whatsapp_threads   -> whatsapp_booking_offers_thread_id_fkey
    DELETE offers first       -> fk_thread_revisions_offer_id

Clearing the two accepted pointers is therefore necessary and NOT sufficient: the helper
never deleted the tester's offers at all. And the same query turned up a second blocking
referent that predates the booking projection entirely —
`whatsapp_clarification_attempts`, which references both the thread and the outbound
message it dispatched, and which one row of is enough to make the whole reset fail with the
offer table absent as well. `test_rv03_pg_13` derives that list from the catalogue so a
third referent added by a future migration fails this gate instead of silently breaking the
reset a third time.

WHAT IS DELIBERATELY NOT DONE. The production foreign keys are not changed to
`ON DELETE SET NULL`. A test-support reset must not weaken the referential integrity of the
booking authority to make its own job easier; `test_rv03_fk_behaviour_unchanged` asserts
that it did not.
"""
from __future__ import annotations

import ast
import os
import sys
import unittest
from datetime import date, datetime, time as _time, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = str(_ROOT / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

PG_URL = os.environ.get("RV03_PG_URL", "") or os.environ.get("G37R_PG_URL", "")

# ── Convergence Phase 3 (adaptation, whole module) ─────────────────────────────────────────
# The tester's REAL acceptance path is now the converged one: the Conversation Engine and the
# Data Exchange delegate to `BookingAuthority`, reachable only with SMART_BOOKING_CE_ENABLED on
# (read from `get_settings()`) and booking against the launch professional. The reset under
# test is unchanged; the bookings it resets are made by the authority.
_P3_PATCHES = []


def setUpModule():
    from offer_fixture import start_converged_booking
    _P3_PATCHES.extend(start_converged_booking())


def tearDownModule():
    from offer_fixture import stop_converged_booking
    stop_converged_booking(_P3_PATCHES)


BOOKING_FLOW_ID = "28104222025943520"
PROJECTION_REVISION = "20261013_booking_projection"
PROJECTION_FK = "fk_thread_revisions_offer_id"
PROJECTION_INDEX = "uq_thread_revision_one_booking_per_offer"

#: Every table the reset reads or writes, in an order safe to TRUNCATE CASCADE.
_TABLES = (
    "whatsapp_booking_offers", "whatsapp_clarification_attempts",
    "whatsapp_outbound_dedup", "security_events", "ai_events", "whatsapp_messages",
    "thread_revisions", "whatsapp_thread_candidates", "whatsapp_thread_states",
    "revisions", "whatsapp_threads", "leads", "whatsapp_contacts",
)


def _payload(wa_id: str) -> dict:
    return {
        "nombre_apellido": "Reset Tester %s" % wa_id[-4:],
        "email": "reset%s@example.test" % wa_id[-4:],
        "telefono": wa_id,
        "como_llego": "Instagram",
        "direccion": "Palermo 123",
        "tipo_vendedor": "particular",
    }


class _ResetHarness(unittest.TestCase):
    """Shared world-building. Every booking is created through the REAL acceptance path."""

    engine = None
    Session = None
    _slots: list = []

    @classmethod
    def setUpClass(cls):
        if not PG_URL:
            raise unittest.SkipTest(
                "RV03_PG_URL (or G37R_PG_URL) required — the G37R-RV-03 defect IS a "
                "PostgreSQL foreign-key cycle and cannot be observed anywhere else")
        cls.engine = create_engine(PG_URL, pool_size=10, max_overflow=5)
        # Production fidelity: `app/db.py` builds sessions with autoflush=False and the
        # default expire_on_commit=True. A probe session with autoflush=True can flush the
        # pointer clear at a moment production would not.
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False,
                                   expire_on_commit=True)
        cls._ensure_schema()

    @classmethod
    def tearDownClass(cls):
        if cls.engine is not None:
            cls.engine.dispose()

    # ── schema ───────────────────────────────────────────────────────────────
    @classmethod
    def _ensure_schema(cls):
        """Build the schema through the migration, and record how.

        A from-scratch `alembic upgrade head` is impossible in this project and that is a
        property of its history, not a shortcut taken here: the earliest migration already
        assumes `revisions` exists, because the base tables predate Alembic. Attempting it
        raises `NoSuchTableError: revisions`. So the schema is built the only way that
        exercises this migration's own code: `create_all()`, `stamp head`,
        `downgrade -1` — which must be able to drop what `create_all` made — and then
        `upgrade head`, which rebuilds the projection foreign key and index through
        `20261013_booking_projection.upgrade()`.
        """
        import app.models  # noqa: F401  — registers the tables on the metadata
        from app.db import Base

        with cls.engine.begin() as c:
            present = bool(c.execute(text(
                "SELECT to_regclass('whatsapp_booking_offers')")).scalar())
        if not present:
            from alembic import command
            from alembic.config import Config

            Base.metadata.create_all(cls.engine)
            cfg = Config(str(Path(_BACKEND) / "alembic.ini"))
            cfg.set_main_option("script_location", str(Path(_BACKEND) / "migrations"))
            prior = os.environ.get("DATABASE_URL")
            os.environ["DATABASE_URL"] = PG_URL
            try:
                command.stamp(cfg, "head")
                # Convergence Phase 1: the head is now a MERGE revision, from which a relative
                # `-1` is ambiguous. Name the parent `-1` meant on Track A, so exactly the booking
                # projection migration (and the no-op merge) is undone, as before.
                command.downgrade(cfg, "20261010_offer_lifecycle")
                command.upgrade(cfg, "head")
            finally:
                if prior is None:
                    os.environ.pop("DATABASE_URL", None)
                else:
                    os.environ["DATABASE_URL"] = prior

    # ── small helpers ────────────────────────────────────────────────────────
    def scalar(self, sql, **p):
        with self.engine.begin() as c:
            return c.execute(text(sql), p).scalar()

    def rows(self, sql, **p):
        with self.engine.begin() as c:
            return [tuple(r) for r in c.execute(text(sql), p)]

    def truncate(self):
        self.engine.dispose()
        with self.engine.begin() as c:
            c.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"))
        self.engine.dispose()
        with self.engine.begin() as c:
            for t in _TABLES:
                c.execute(text("TRUNCATE %s RESTART IDENTITY CASCADE" % t))

    def setUp(self):
        self.truncate()
        from offer_fixture import ensure_launch_professional
        db = self.Session()
        try:
            ensure_launch_professional(db)   # Convergence Phase 3: the authority books against it
            db.commit()
        finally:
            db.close()

    def slots(self, n=2):
        if len(type(self)._slots) >= n:
            return type(self)._slots[:n]
        from app.schemas.schedule import ScheduleCheckIn
        from app.services.schedule import ScheduleService
        db = self.Session()
        try:
            svc = ScheduleService(db)
            for off in range(2, 45):
                d = date.today() + timedelta(days=off)
                out = svc.list_slots(ScheduleCheckIn(
                    address="Palermo, CABA", preferred_day=d, preferred_time=_time(11, 0),
                    zone_group="CABA", zone_detail="Palermo"))
                if out.slots:
                    type(self)._slots.append((d, str(out.slots[0])[:5]))
                if len(type(self)._slots) >= n:
                    break
        finally:
            db.close()
        self.assertGreaterEqual(len(type(self)._slots), n,
                                "the schedule offered fewer free slots than this test needs")
        return type(self)._slots[:n]

    def engine_for(self, db):
        """A production ConversationEngine with the TRANSPORT stubbed and nothing else.

        The kill switch refuses a Flow dispatch, so with a real transport no offer could be
        issued at all and there would be nothing to reset. Nothing about the reset itself,
        or about the acceptance decision, is stubbed.
        """
        from app.services.conversation_engine import ConversationEngine
        from app.settings import get_settings
        e = ConversationEngine(db=db, settings=get_settings())
        e._send_flow_button = lambda *a, **k: "wamid.stub.flow"
        e._send_text_to_wa = lambda *a, **k: "wamid.stub.text"
        e._send_booking_notification = lambda *a, **k: None
        e._send_fallback_human_review_notification = lambda *a, **k: None
        return e

    # ── world ────────────────────────────────────────────────────────────────
    def make_tester(self, wa_id, *, slot_index=0, candidates=1):
        import app.models as M
        d, hhmm = self.slots(2)[slot_index]
        db = self.Session()
        try:
            ct = M.WhatsAppContact(wa_id=wa_id, display_name="TESTER %s" % wa_id[-4:])
            db.add(ct); db.flush()
            ld = M.Lead(estado="CONSULTA_NUEVA", flag="ACEPTADO", necesita_humano=False)
            db.add(ld); db.flush()
            th = M.WhatsAppThread(contact_id=ct.id, lead_id=ld.id)
            db.add(th); db.flush()
            st = M.WhatsAppThreadState(thread_id=th.id)
            db.add(st); db.flush()
            cids = []
            for i in range(candidates):
                cand = M.WhatsAppThreadCandidate(
                    thread_id=th.id, marca="Ford", modelo="Focus", anio=2017 + i,
                    tipo_vehiculo="AUTO",
                    status="current_focus" if i == 0 else "archived",
                    source_text="ford focus %d" % (2017 + i))
                db.add(cand); db.flush()
                cids.append(cand.id)
            st.current_focus_candidate_id = cids[0]
            st.home_zone_group, st.home_zone_detail = "CABA", "Palermo"
            st.last_stage = "SCHEDULING"
            st.preferred_day, st.preferred_time = d, hhmm
            st.active_requested_date, st.last_requested_time = d, hhmm
            db.commit()
            return {"wa_id": wa_id, "contact_id": ct.id, "lead_id": ld.id,
                    "thread_id": th.id, "candidate_ids": cids, "date": d, "time": hhmm}
        finally:
            db.close()

    def issue_offer(self, thread_id):
        """A real offer, minted by `_send_booking_flow` through `OfferLifecycle.issue`."""
        db = self.Session()
        try:
            e = self.engine_for(db)
            ctx = e._load_context(thread_id)
            ctx.state.needs_human = False
            db.commit()
            e._send_booking_flow(ctx, ctx.state, booking_flow_id=BOOKING_FLOW_ID)
            db.refresh(ctx.state)
            token = ctx.state.flow_booking_token
        finally:
            db.close()
        offer_id = self.scalar(
            "SELECT id FROM whatsapp_booking_offers WHERE thread_id = :t "
            "AND status = 'ISSUED'", t=thread_id)
        self.assertIsNotNone(token, "no offer token was minted")
        self.assertIsNotNone(offer_id, "no ISSUED offer row was created")
        return token, offer_id

    def inbound(self, thread_id, wamid, *, kind="flow_response", body=None):
        import app.models as M
        db = self.Session()
        try:
            db.add(M.WhatsAppMessage(
                thread_id=thread_id, wa_message_id=wamid, direction="in",
                timestamp=datetime.now(timezone.utc), message_type=kind, text=body,
                status="received"))
            db.commit()
        finally:
            db.close()

    def accept_via_ce(self, ids, token, *, tag="accept"):
        """Accept through the ConversationEngine — a real production transport."""
        from app.schemas.conversation import ConversationHandleIn
        wamid = "wamid.%s.%s" % (tag, ids["thread_id"])
        self.inbound(ids["thread_id"], wamid)
        db = self.Session()
        try:
            e = self.engine_for(db)
            out = e.handle(ConversationHandleIn(
                thread_id=ids["thread_id"], wa_message_id=wamid, wa_id=ids["wa_id"],
                message_type="flow_response", flow_response=_payload(ids["wa_id"]),
                flow_token=token))
            return out.action
        finally:
            db.close()

    def replay_via_dx(self, ids, token):
        """Resubmit the same token through the Data Exchange writer — the other transport.

        Returns the typed outcome, whatever it is: the point of the scenario is that the
        offer and booking carry CROSS-TRANSPORT state when the reset runs, not that the
        replay succeeds.
        """
        from app.services.booking_flow_service import BookingFlowService
        db = self.Session()
        try:
            svc = BookingFlowService(db)
            try:
                svc.handle_confirm_booking(_payload(ids["wa_id"]), token)
                return "completed"
            except Exception as exc:                # a typed domain outcome is expected
                return type(exc).__name__
        finally:
            try:
                db.rollback()
            except Exception:
                pass
            db.close()

    def reset(self, wa_id, *, expect_ok=True):
        from app.services.tester_reset import reset_tester_to_zero_state
        db = self.Session()
        try:
            result = reset_tester_to_zero_state(db, wa_id)
            db.commit()
            return result
        except Exception:
            db.rollback()
            if expect_ok:
                raise
            return None
        finally:
            db.close()

    # ── assertions ───────────────────────────────────────────────────────────
    def database_census(self):
        """Every row of every table the reset can touch, as comparable values."""
        out = {}
        for table in _TABLES:
            out[table] = self.scalar("SELECT count(*) FROM %s" % table)
        out["contacts_by_wa_id"] = self.rows(
            "SELECT wa_id, display_name FROM whatsapp_contacts ORDER BY wa_id")
        out["threads"] = self.rows(
            "SELECT id, contact_id, lead_id FROM whatsapp_threads ORDER BY id")
        out["outbound_ledger"] = self.rows(
            "SELECT wa_message_id, thread_id, status, path_id FROM whatsapp_messages "
            "WHERE direction = 'out' ORDER BY id")
        return out

    def census_of_tester(self, wa_id):
        tids = [r[0] for r in self.rows(
            "SELECT t.id FROM whatsapp_threads t JOIN whatsapp_contacts c "
            "ON c.id = t.contact_id WHERE c.wa_id = :w", w=wa_id)]
        lids = [r[0] for r in self.rows(
            "SELECT t.lead_id FROM whatsapp_threads t JOIN whatsapp_contacts c "
            "ON c.id = t.contact_id WHERE c.wa_id = :w AND t.lead_id IS NOT NULL", w=wa_id)]
        t = tids or [0]
        lids = lids or [0]
        return {
            "contact": self.scalar(
                "SELECT count(*) FROM whatsapp_contacts WHERE wa_id = :w", w=wa_id),
            "threads": len(tids),
            "offers": self.scalar("SELECT count(*) FROM whatsapp_booking_offers "
                                  "WHERE thread_id = ANY(:t)", t=t),
            "thread_revisions": self.scalar("SELECT count(*) FROM thread_revisions "
                                            "WHERE thread_id = ANY(:t)", t=t),
            "candidates": self.scalar("SELECT count(*) FROM whatsapp_thread_candidates "
                                      "WHERE thread_id = ANY(:t)", t=t),
            "thread_states": self.scalar("SELECT count(*) FROM whatsapp_thread_states "
                                         "WHERE thread_id = ANY(:t)", t=t),
            "messages": self.scalar("SELECT count(*) FROM whatsapp_messages "
                                    "WHERE thread_id = ANY(:t)", t=t),
            "clarifications": self.scalar(
                "SELECT count(*) FROM whatsapp_clarification_attempts "
                "WHERE thread_id = ANY(:t)", t=t),
            "leads": self.scalar("SELECT count(*) FROM leads WHERE id = ANY(:l)", l=lids),
            "revisions": self.scalar("SELECT count(*) FROM revisions "
                                     "WHERE lead_id = ANY(:l)", l=lids),
            "dedup": self.scalar("SELECT count(*) FROM whatsapp_outbound_dedup "
                                 "WHERE wa_id = :w", w=wa_id),
        }

    def assert_zero_state(self, wa_id, *, where=""):
        census = self.census_of_tester(wa_id)
        self.assertEqual(
            census, {k: 0 for k in census},
            "%s: the tester is not at zero state — %r" % (where or wa_id, census))

    def assert_no_orphans(self):
        """Nothing may point at a row that is gone. Asked of the data, not of the code."""
        checks = {
            "thread_revisions -> offers": (
                "SELECT count(*) FROM thread_revisions tr LEFT JOIN "
                "whatsapp_booking_offers o ON o.id = tr.offer_id "
                "WHERE tr.offer_id IS NOT NULL AND o.id IS NULL"),
            "offers -> thread_revisions": (
                "SELECT count(*) FROM whatsapp_booking_offers o LEFT JOIN "
                "thread_revisions tr ON tr.id = o.accepted_thread_revision_id "
                "WHERE o.accepted_thread_revision_id IS NOT NULL AND tr.id IS NULL"),
            "offers -> revisions": (
                "SELECT count(*) FROM whatsapp_booking_offers o LEFT JOIN revisions r "
                "ON r.id = o.accepted_revision_id "
                "WHERE o.accepted_revision_id IS NOT NULL AND r.id IS NULL"),
            "offers -> threads": (
                "SELECT count(*) FROM whatsapp_booking_offers o LEFT JOIN "
                "whatsapp_threads t ON t.id = o.thread_id WHERE t.id IS NULL"),
            "clarifications -> threads": (
                "SELECT count(*) FROM whatsapp_clarification_attempts a LEFT JOIN "
                "whatsapp_threads t ON t.id = a.thread_id WHERE t.id IS NULL"),
        }
        for label, sql in checks.items():
            self.assertEqual(self.scalar(sql), 0, "orphan rows: %s" % label)

    def offer_rows(self, thread_ids=None):
        if thread_ids is None:
            return self.rows(
                "SELECT id, thread_id, status, accepted_revision_id, "
                "accepted_thread_revision_id, candidate_id "
                "FROM whatsapp_booking_offers ORDER BY id")
        return self.rows(
            "SELECT id, thread_id, status, accepted_revision_id, "
            "accepted_thread_revision_id, candidate_id FROM whatsapp_booking_offers "
            "WHERE thread_id = ANY(:t) ORDER BY id", t=thread_ids)

    def clarification(self, thread_id, *, family="VEHICLE", key="probe-1",
                      outbound_message_id=None):
        import app.models as M
        now = datetime.now(timezone.utc)
        db = self.Session()
        try:
            db.add(M.WhatsAppClarificationAttempt(
                thread_id=thread_id, cycle_key=0, family=family, idempotency_key=key,
                status="RESERVED", reserved_at=now, updated_at=now,
                outbound_message_id=outbound_message_id))
            db.commit()
        finally:
            db.close()


# ─────────────────────────────────────────────────────────────────────────────
class RV03SchemaIsTheMigratedOne(_ResetHarness):
    """Say out loud which schema everything below ran against."""

    def test_rv03_pg_00_the_schema_is_the_migrated_one(self):
        # Convergence Phase 1: the head is now the merge revision that joins this chain with
        # the Smart Booking chain, so "migrated through" means PROJECTION_REVISION is the
        # stamped revision or one of its ancestors in Alembic's own graph.
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        cfg = Config(str(Path(_BACKEND) / "alembic.ini"))
        cfg.set_main_option("script_location", str(Path(_BACKEND) / "migrations"))
        version = self.scalar("SELECT version_num FROM alembic_version")
        applied = {r.revision for r in
                   ScriptDirectory.from_config(cfg).iterate_revisions(version, "base")}
        self.assertIn(
            PROJECTION_REVISION, applied,
            "this suite must run against a schema migrated through %r" % PROJECTION_REVISION)
        self.assertTrue(self.scalar(
            "SELECT 1 FROM pg_constraint WHERE conname = :n", n=PROJECTION_FK),
            "the projection foreign key is absent — there would be no cycle to break")
        self.assertTrue(self.scalar(
            "SELECT 1 FROM pg_indexes WHERE indexname = :n", n=PROJECTION_INDEX),
            "the one-offer/one-booking index is absent")


class RV03TheCycleAndTheReset(_ResetHarness):
    """The twelve required scenarios."""

    # 1 ──────────────────────────────────────────────────────────────────────
    def test_rv03_pg_01_a_tester_with_no_offer_or_booking(self):
        wa = "5491190000101"
        self.make_tester(wa)
        result = self.reset(wa)
        self.assertEqual(result.cleared_offer_pointers, 0,
                         "nothing to clear, so nothing may be reported as cleared")
        self.assert_zero_state(wa, where="no offer")
        self.assert_no_orphans()

    # 2 ──────────────────────────────────────────────────────────────────────
    def test_rv03_pg_02_a_tester_with_an_ISSUED_offer(self):
        wa = "5491190000102"
        ids = self.make_tester(wa)
        _token, offer_id = self.issue_offer(ids["thread_id"])
        self.assertEqual(self.scalar("SELECT status FROM whatsapp_booking_offers "
                                     "WHERE id = :o", o=offer_id), "ISSUED")
        result = self.reset(wa)
        self.assertEqual(result.deleted.get("booking_offers"), 1,
                         "the tester's live offer must be removed with the tester")
        self.assertEqual(result.cleared_offer_pointers, 0,
                         "an ISSUED offer has no accepted pointers to clear")
        self.assert_zero_state(wa, where="issued offer")
        self.assert_no_orphans()

    # 3 ──────────────────────────────────────────────────────────────────────
    def test_rv03_pg_03_a_tester_with_a_withdrawn_offer(self):
        from app.services.offer_lifecycle import OfferLifecycle, WithdrawReason
        wa = "5491190000103"
        ids = self.make_tester(wa)
        self.issue_offer(ids["thread_id"])
        db = self.Session()
        try:
            e = self.engine_for(db)
            ctx = e._load_context(ids["thread_id"])
            OfferLifecycle(db).withdraw(ctx.state, thread_id=ids["thread_id"],
                                        reason=WithdrawReason.CYCLE_RESET)
            db.commit()
        finally:
            db.close()
        self.assertEqual(self.scalar("SELECT status FROM whatsapp_booking_offers LIMIT 1"),
                         "WITHDRAWN")
        result = self.reset(wa)
        self.assertEqual(result.deleted.get("booking_offers"), 1)
        self.assert_zero_state(wa, where="withdrawn offer")
        self.assert_no_orphans()

    # 4 + 5 ─────────────────────────────────────────────────────────────────
    def test_rv03_pg_04_an_accepted_offer_and_one_committed_booking(self):
        wa = "5491190000104"
        ids = self.make_tester(wa)
        token, offer_id = self.issue_offer(ids["thread_id"])
        self.assertEqual(self.accept_via_ce(ids, token), "booking_created")
        self.assertEqual(self.scalar("SELECT count(*) FROM thread_revisions "
                                     "WHERE status = 'booked'"), 1)
        self.assertEqual(self.scalar("SELECT status FROM whatsapp_booking_offers "
                                     "WHERE id = :o", o=offer_id), "ACCEPTED")
        result = self.reset(wa)
        self.assertEqual(result.cleared_offer_pointers, 1,
                         "the accepted offer's pointers had to be cleared")
        self.assertEqual(result.deleted.get("thread_revisions"), 1)
        self.assertEqual(result.deleted.get("booking_offers"), 1)
        self.assertEqual(result.deleted.get("revisions"), 1)
        self.assert_zero_state(wa, where="accepted booking")
        self.assert_no_orphans()

    def test_rv03_pg_05_both_accepted_pointer_fields_were_populated(self):
        """The scenario only means something if BOTH pointers were really set first."""
        wa = "5491190000105"
        ids = self.make_tester(wa)
        token, offer_id = self.issue_offer(ids["thread_id"])
        self.assertEqual(self.accept_via_ce(ids, token), "booking_created")
        before = self.rows("SELECT accepted_revision_id, accepted_thread_revision_id "
                           "FROM whatsapp_booking_offers WHERE id = :o", o=offer_id)[0]
        self.assertIsNotNone(before[0], "accepted_revision_id was not populated — the "
                                        "scenario would not exercise that foreign key")
        self.assertIsNotNone(before[1], "accepted_thread_revision_id was not populated")
        self.reset(wa)
        self.assert_zero_state(wa, where="both pointers")
        self.assert_no_orphans()

    # 6 ──────────────────────────────────────────────────────────────────────
    def test_rv03_pg_06_cross_transport_booking_and_replay_state(self):
        wa = "5491190000106"
        ids = self.make_tester(wa)
        token, offer_id = self.issue_offer(ids["thread_id"])
        self.assertEqual(self.accept_via_ce(ids, token), "booking_created")
        outcome = self.replay_via_dx(ids, token)
        self.assertNotEqual(outcome, "completed",
                            "a second booking from one offer must never complete")
        self.assertEqual(self.scalar("SELECT count(*) FROM thread_revisions "
                                     "WHERE offer_id = :o AND status = 'booked'",
                                     o=offer_id), 1,
                         "the one-offer/one-booking guarantee must still hold")
        result = self.reset(wa)
        self.assertEqual(result.cleared_offer_pointers, 1)
        self.assert_zero_state(wa, where="cross-transport")
        self.assert_no_orphans()

    # 7 ──────────────────────────────────────────────────────────────────────
    def test_rv03_pg_07_multiple_historical_and_superseded_offers(self):
        wa = "5491190000107"
        ids = self.make_tester(wa)
        self.issue_offer(ids["thread_id"])
        self.issue_offer(ids["thread_id"])
        token, offer_id = self.issue_offer(ids["thread_id"])
        statuses = sorted(r[0] for r in self.rows(
            "SELECT status FROM whatsapp_booking_offers WHERE thread_id = :t",
            t=ids["thread_id"]))
        self.assertEqual(len(statuses), 3, "three offers were issued: %r" % (statuses,))
        self.assertIn("SUPERSEDED", statuses,
                      "issuing supersedes the previous offer: %r" % (statuses,))
        self.assertEqual(self.accept_via_ce(ids, token), "booking_created")
        result = self.reset(wa)
        self.assertEqual(result.deleted.get("booking_offers"), 3,
                         "every offer the tester ever had must go, not only the live one")
        self.assertEqual(result.cleared_offer_pointers, 1,
                         "only the accepted one carried pointers")
        self.assert_zero_state(wa, where="three offers")
        self.assert_no_orphans()

    # 8 ──────────────────────────────────────────────────────────────────────
    def test_rv03_pg_08_multiple_candidates_and_cycles(self):
        wa = "5491190000108"
        ids = self.make_tester(wa, candidates=3)
        token, _offer = self.issue_offer(ids["thread_id"])
        self.assertEqual(self.accept_via_ce(ids, token), "booking_created")
        # a second cycle: the human moves the lead back, the next turn consumes the flag
        with self.engine.begin() as c:
            c.execute(text("UPDATE whatsapp_thread_states SET cycle_reset_pending = true, "
                           "needs_human = false WHERE thread_id = :t"),
                      {"t": ids["thread_id"]})
        self.assertEqual(self.scalar("SELECT count(*) FROM whatsapp_thread_candidates "
                                     "WHERE thread_id = :t", t=ids["thread_id"]), 3)
        result = self.reset(wa)
        self.assertEqual(result.deleted.get("candidates"), 3)
        self.assert_zero_state(wa, where="three candidates")
        self.assert_no_orphans()

    # 9 ──────────────────────────────────────────────────────────────────────
    def test_rv03_pg_09_two_testers_with_independent_accepted_bookings(self):
        """The other tester must come out of this field-for-field unchanged."""
        wa_a, wa_b = "5491190000109", "5491190000209"
        ids_a = self.make_tester(wa_a, slot_index=0)
        ids_b = self.make_tester(wa_b, slot_index=1)
        token_a, _oa = self.issue_offer(ids_a["thread_id"])
        token_b, offer_b = self.issue_offer(ids_b["thread_id"])
        self.assertEqual(self.accept_via_ce(ids_a, token_a, tag="a"), "booking_created")
        self.assertEqual(self.accept_via_ce(ids_b, token_b, tag="b"), "booking_created")

        before_b = {
            "offers": self.offer_rows([ids_b["thread_id"]]),
            "bookings": self.rows(
                "SELECT id, thread_id, offer_id, status, scheduled_date, scheduled_time "
                "FROM thread_revisions WHERE thread_id = :t ORDER BY id",
                t=ids_b["thread_id"]),
            "revisions": self.rows(
                "SELECT id, lead_id, estado_revision, turno_fecha, turno_hora "
                "FROM revisions "
                "WHERE lead_id = :l ORDER BY id", l=ids_b["lead_id"]),
            "lead": self.rows("SELECT id, estado, flag, necesita_humano FROM leads "
                              "WHERE id = :l", l=ids_b["lead_id"]),
            "state": self.rows("SELECT thread_id, flow_booking_token, "
                               "current_focus_candidate_id, last_stage "
                               "FROM whatsapp_thread_states WHERE thread_id = :t",
                               t=ids_b["thread_id"]),
            "candidates": self.rows("SELECT id, status, marca, modelo, anio FROM "
                                    "whatsapp_thread_candidates WHERE thread_id = :t "
                                    "ORDER BY id", t=ids_b["thread_id"]),
        }
        self.assertIsNotNone(before_b["offers"][0][4],
                             "tester B's offer must carry an accepted pointer, or the "
                             "isolation claim is vacuous")

        self.reset(wa_a)
        self.assert_zero_state(wa_a, where="tester A")
        after_b = {
            "offers": self.offer_rows([ids_b["thread_id"]]),
            "bookings": self.rows(
                "SELECT id, thread_id, offer_id, status, scheduled_date, scheduled_time "
                "FROM thread_revisions WHERE thread_id = :t ORDER BY id",
                t=ids_b["thread_id"]),
            "revisions": self.rows(
                "SELECT id, lead_id, estado_revision, turno_fecha, turno_hora "
                "FROM revisions "
                "WHERE lead_id = :l ORDER BY id", l=ids_b["lead_id"]),
            "lead": self.rows("SELECT id, estado, flag, necesita_humano FROM leads "
                              "WHERE id = :l", l=ids_b["lead_id"]),
            "state": self.rows("SELECT thread_id, flow_booking_token, "
                               "current_focus_candidate_id, last_stage "
                               "FROM whatsapp_thread_states WHERE thread_id = :t",
                               t=ids_b["thread_id"]),
            "candidates": self.rows("SELECT id, status, marca, modelo, anio FROM "
                                    "whatsapp_thread_candidates WHERE thread_id = :t "
                                    "ORDER BY id", t=ids_b["thread_id"]),
        }
        self.assertEqual(before_b, after_b,
                         "resetting tester A changed tester B's rows")
        self.assertEqual(self.scalar("SELECT accepted_thread_revision_id FROM "
                                     "whatsapp_booking_offers WHERE id = :o", o=offer_b),
                         before_b["offers"][0][4],
                         "tester B's accepted pointer was cleared by another tester's reset")
        self.assert_no_orphans()

    # 10 ─────────────────────────────────────────────────────────────────────
    def test_rv03_pg_10_the_reset_is_idempotent(self):
        wa = "5491190000110"
        ids = self.make_tester(wa)
        token, _o = self.issue_offer(ids["thread_id"])
        self.assertEqual(self.accept_via_ce(ids, token), "booking_created")
        first = self.reset(wa)
        self.assertEqual(first.cleared_offer_pointers, 1)
        self.assert_zero_state(wa, where="first reset")
        # Nothing anywhere may move on the second call — not the tester's rows, which are
        # already gone, and not the archive the first call created. A whole-database
        # census is what makes "idempotent" mean something stronger than "did not raise".
        before = self.database_census()
        second = self.reset(wa)
        self.assertEqual(second.cleared_offer_pointers, 0)
        self.assertEqual(second.deleted, {},
                         "a second reset of an absent tester must do nothing at all")
        self.assertIsNone(second.archive_thread_id,
                          "a no-op reset must not even create the archive thread")
        self.assertEqual(self.database_census(), before,
                         "the second reset changed the database")
        self.assert_zero_state(wa, where="second reset")
        self.assert_no_orphans()

    def test_rv03_pg_18_an_unknown_handset_is_a_complete_no_op(self):
        """A wa_id that was never seen must produce no rows at all — not even the archive.

        The reset is a destructive tool run by hand. "Nothing matched" has to mean nothing
        happened, or a mistyped handset leaves a trace in the database and the operator has
        no way to tell whether the real tester was reset.
        """
        self.make_tester("5491190000118")        # a bystander, to make the census real
        before = self.database_census()
        result = self.reset("5491190000999")
        self.assertEqual(result.deleted, {})
        self.assertEqual(result.archived_outbound, 0)
        self.assertEqual(result.cleared_offer_pointers, 0)
        self.assertIsNone(result.archive_thread_id,
                          "an unknown handset must not create the archive thread")
        self.assertEqual(self.database_census(), before,
                         "resetting an unknown handset changed the database")

    # 11 ─────────────────────────────────────────────────────────────────────
    def test_rv03_pg_11_failure_after_the_pointers_are_cleared_rolls_everything_back(self):
        """The clear and the deletes are ONE transaction, so a later failure undoes it."""
        import app.models as M
        from app.services.tester_reset import reset_tester_to_zero_state
        wa = "5491190000111"
        ids = self.make_tester(wa)
        token, offer_id = self.issue_offer(ids["thread_id"])
        self.assertEqual(self.accept_via_ce(ids, token), "booking_created")
        before = self.rows("SELECT accepted_revision_id, accepted_thread_revision_id "
                           "FROM whatsapp_booking_offers WHERE id = :o", o=offer_id)[0]
        self.assertIsNotNone(before[1])

        db = self.Session()
        original = db.delete
        seen = {"cleared_before_failure": None}

        def delete_but_fail_on_the_booking(obj):
            if isinstance(obj, M.ThreadRevision):
                # BOTH pointers must already be NULL *in this transaction*. Observing them
                # here is the only way to see the clear itself rather than its
                # consequences: `accepted_revision_id` never blocks a delete once the
                # offers are removed before the revisions, so a mutation that stopped
                # clearing it would otherwise survive every outcome-level assertion.
                seen["cleared_before_failure"] = db.execute(text(
                    "SELECT accepted_thread_revision_id, accepted_revision_id "
                    "FROM whatsapp_booking_offers WHERE id = :o"), {"o": offer_id}).one()
                raise RuntimeError("injected failure after the pointer clear")
            return original(obj)

        db.delete = delete_but_fail_on_the_booking
        with self.assertRaises(RuntimeError):
            reset_tester_to_zero_state(db, wa)
        db.rollback()
        self.assertEqual(db.execute(text("SELECT 1")).scalar(), 1,
                         "the session must be reusable after the rollback")
        db.close()

        self.assertEqual(
            seen["cleared_before_failure"], (None, None),
            "BOTH accepted pointers must be NULL before the first dependent deletion; "
            "observed %r (accepted_thread_revision_id, accepted_revision_id)"
            % (seen["cleared_before_failure"],))
        after = self.rows("SELECT accepted_revision_id, accepted_thread_revision_id "
                          "FROM whatsapp_booking_offers WHERE id = :o", o=offer_id)[0]
        self.assertEqual(after, before,
                         "the rollback did not restore the accepted pointers")
        self.assertEqual(self.scalar("SELECT count(*) FROM thread_revisions "
                                     "WHERE thread_id = :t", t=ids["thread_id"]), 1)
        self.assertEqual(self.scalar("SELECT status FROM whatsapp_booking_offers "
                                     "WHERE id = :o", o=offer_id), "ACCEPTED")
        self.assertEqual(self.scalar("SELECT count(*) FROM whatsapp_threads "
                                     "WHERE id = :t", t=ids["thread_id"]), 1)
        self.assertEqual(self.scalar("SELECT count(*) FROM leads WHERE id = :l",
                                     l=ids["lead_id"]), 1)
        self.assert_no_orphans()
        # and the tester can still be reset afterwards, in full
        self.reset(wa)
        self.assert_zero_state(wa, where="after the injected failure")

    # 12 ─────────────────────────────────────────────────────────────────────
    def test_rv03_pg_12_failure_during_the_deletion_rolls_everything_back(self):
        import app.models as M
        from app.services.tester_reset import reset_tester_to_zero_state
        wa = "5491190000112"
        ids = self.make_tester(wa)
        token, offer_id = self.issue_offer(ids["thread_id"])
        self.assertEqual(self.accept_via_ce(ids, token), "booking_created")
        before_offers = self.offer_rows([ids["thread_id"]])
        before_bookings = self.scalar("SELECT count(*) FROM thread_revisions "
                                      "WHERE thread_id = :t", t=ids["thread_id"])

        db = self.Session()
        original = db.delete

        def delete_but_fail_on_the_thread(obj):
            if isinstance(obj, M.WhatsAppThread):
                raise RuntimeError("injected failure during the deletion")
            return original(obj)

        db.delete = delete_but_fail_on_the_thread
        with self.assertRaises(RuntimeError):
            reset_tester_to_zero_state(db, wa)
        db.rollback()
        self.assertEqual(db.execute(text("SELECT 1")).scalar(), 1)
        db.close()

        self.assertEqual(self.offer_rows([ids["thread_id"]]), before_offers,
                         "the offer rows did not survive the rollback unchanged")
        self.assertEqual(self.scalar("SELECT count(*) FROM thread_revisions "
                                     "WHERE thread_id = :t", t=ids["thread_id"]),
                         before_bookings)
        self.assertEqual(self.scalar("SELECT count(*) FROM whatsapp_thread_candidates "
                                     "WHERE thread_id = :t", t=ids["thread_id"]), 1)
        self.assert_no_orphans()
        self.reset(wa)
        self.assert_zero_state(wa, where="after the deletion failure")

    # 13 ─────────────────────────────────────────────────────────────────────
    def test_rv03_pg_13_every_blocking_referent_is_handled(self):
        """Derived from `pg_constraint`, not from the code.

        This is the test that would have caught the defect before it shipped, and the one
        that will catch the next one: every `NO ACTION` foreign key pointing AT a table the
        reset deletes is a row that can block the reset, and each such referencing table
        must either be deleted by the reset or have its pointer cleared by it.
        """
        from app.services import tester_reset as T
        deleted_tables = {
            "whatsapp_threads", "leads", "revisions", "thread_revisions",
            "whatsapp_thread_candidates", "whatsapp_contacts", "whatsapp_messages",
            "whatsapp_thread_states", "ai_events",
        }
        blocking = self.rows(
            "SELECT frel.relname, rel.relname, att.attname, con.conname "
            "FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid "
            "JOIN pg_class frel ON frel.oid = con.confrelid "
            "JOIN pg_attribute att ON att.attrelid = rel.oid "
            "                     AND att.attnum = con.conkey[1] "
            "WHERE con.contype = 'f' AND con.confdeltype = 'a' "
            "  AND frel.relname = ANY(:tables)", tables=sorted(deleted_tables))
        self.assertTrue(blocking, "the catalogue returned no blocking referents at all — "
                                  "this test would then prove nothing")
        source = open(Path(T.__file__)).read()
        handled = {"whatsapp_booking_offers", "whatsapp_clarification_attempts"}
        unhandled = sorted({r[1] for r in blocking}
                           - handled - deleted_tables)
        self.assertEqual(
            unhandled, [],
            "a NO ACTION foreign key points at a table the reset deletes, from a table the "
            "reset neither deletes nor clears: %r. Every one of these makes the reset fail "
            "23503 and delete nothing. Blocking referents found: %r" % (unhandled, blocking))
        for table in handled:
            self.assertIn(table, source,
                          "%s is declared handled but the reset never mentions it" % table)

    # 14 ─────────────────────────────────────────────────────────────────────
    def test_rv03_pg_14_a_clarification_attempt_does_not_block_the_reset(self):
        """The second blocking referent, older than the booking projection."""
        wa = "5491190000114"
        ids = self.make_tester(wa)
        self.clarification(ids["thread_id"], family="VEHICLE", key="v-1")
        self.clarification(ids["thread_id"], family="LOCATION", key="l-1")
        self.assertEqual(self.scalar("SELECT count(*) FROM "
                                     "whatsapp_clarification_attempts WHERE thread_id = :t",
                                     t=ids["thread_id"]), 2)
        result = self.reset(wa)
        self.assertEqual(result.deleted.get("clarification_attempts"), 2)
        self.assert_zero_state(wa, where="clarification attempts")
        self.assert_no_orphans()

    def test_rv03_pg_15_a_clarification_pointing_at_a_blocked_outbound_row(self):
        """`outbound_message_id` is the clarification table's SECOND blocking foreign key.

        A dispatch the kill switch blocked has no WAMID, so it is deleted rather than
        archived — and the clarification row referencing it would block that delete.
        """
        import app.models as M
        wa = "5491190000115"
        ids = self.make_tester(wa)
        db = self.Session()
        try:
            msg = M.WhatsAppMessage(
                thread_id=ids["thread_id"], direction="out", status="blocked",
                timestamp=datetime.now(timezone.utc), message_type="flow",
                automated=True, text="clarification flow", wa_message_id=None)
            db.add(msg); db.flush()
            msg_id = msg.id
            db.commit()
        finally:
            db.close()
        self.clarification(ids["thread_id"], key="o-1", outbound_message_id=msg_id)
        result = self.reset(wa)
        self.assertEqual(result.deleted.get("clarification_attempts"), 1)
        self.assertEqual(result.archived_outbound, 0,
                         "a blocked row with no WAMID carries no callback risk and is "
                         "deleted, not archived")
        self.assert_zero_state(wa, where="clarification -> blocked outbound")
        self.assert_no_orphans()


class RV03RealAcceptancePathAndReseed(_ResetHarness):
    """§5 — the whole sequence, and then a clean second test on the same tester."""

    def test_rv03_pg_16_accept_reset_and_re_seed_the_same_tester(self):
        wa = "5491190000116"
        ids = self.make_tester(wa)
        token, offer_id = self.issue_offer(ids["thread_id"])
        self.assertEqual(self.accept_via_ce(ids, token), "booking_created")
        self.assertEqual(self.scalar("SELECT count(*) FROM thread_revisions "
                                     "WHERE offer_id = :o AND status = 'booked'",
                                     o=offer_id), 1)
        self.assertEqual(self.scalar("SELECT status FROM whatsapp_booking_offers "
                                     "WHERE id = :o", o=offer_id), "ACCEPTED")
        outcome = self.replay_via_dx(ids, token)
        self.assertNotEqual(outcome, "completed")

        self.reset(wa)
        self.assert_zero_state(wa, where="before the re-seed")
        self.assert_no_orphans()

        # a genuinely new test on the same handset: new lead, new thread, new booking
        ids2 = self.make_tester(wa, slot_index=1)
        self.assertNotEqual(ids2["thread_id"], ids["thread_id"],
                            "the re-seeded tester reused the old thread id")
        token2, offer2 = self.issue_offer(ids2["thread_id"])
        self.assertEqual(self.accept_via_ce(ids2, token2, tag="second"),
                         "booking_created")
        self.assertEqual(self.scalar("SELECT count(*) FROM thread_revisions "
                                     "WHERE offer_id = :o AND status = 'booked'",
                                     o=offer2), 1)
        self.assertEqual(self.scalar("SELECT count(*) FROM thread_revisions "
                                     "WHERE status = 'booked'"), 1,
                         "the second test must start from one booking, not two")
        self.reset(wa)
        self.assert_zero_state(wa, where="after the second test")
        self.assert_no_orphans()

    def test_rv03_pg_17_the_outbound_ledger_is_still_preserved(self):
        """The reset's original purpose must survive this correction (L4.7W5-F1)."""
        import app.models as M
        from app.services.tester_reset import ARCHIVE_WA_ID
        wa = "5491190000117"
        ids = self.make_tester(wa)
        db = self.Session()
        try:
            db.add(M.WhatsAppMessage(
                thread_id=ids["thread_id"], direction="out", status="sent",
                timestamp=datetime.now(timezone.utc), message_type="text",
                automated=True, text="hola", wa_message_id="wamid.keepme.1"))
            db.commit()
        finally:
            db.close()
        token, _o = self.issue_offer(ids["thread_id"])
        self.assertEqual(self.accept_via_ce(ids, token), "booking_created")
        result = self.reset(wa)
        self.assertEqual(result.archived_outbound, 1)
        archived_thread = self.scalar(
            "SELECT t.id FROM whatsapp_threads t JOIN whatsapp_contacts c "
            "ON c.id = t.contact_id WHERE c.wa_id = :w", w=ARCHIVE_WA_ID)
        self.assertEqual(
            self.scalar("SELECT thread_id FROM whatsapp_messages "
                        "WHERE wa_message_id = 'wamid.keepme.1'"), archived_thread,
            "the transmitted outbound row must be re-parented, not deleted")
        self.assert_zero_state(wa, where="ledger preserved")


# ─────────────────────────────────────────────────────────────────────────────
class RV03Structure(unittest.TestCase):
    """§6 — structural constraints. No database, so these run in the default gate too.

    A source-text assertion does not prove reset safety and is not offered as if it did;
    the PostgreSQL scenarios above are the proof. These exist so the SHAPE of the
    correction cannot drift: a table-wide clear, or a clear moved after the deletion, is
    what would make the behavioural tests pass while the helper became dangerous.
    """

    @classmethod
    def setUpClass(cls):
        from app.services import tester_reset as T
        cls.T = T
        cls.source = open(Path(T.__file__)).read()
        cls.tree = ast.parse(cls.source)
        cls.fn = next(n for n in ast.walk(cls.tree)
                      if isinstance(n, ast.FunctionDef)
                      and n.name == "reset_tester_to_zero_state")

    #: Statement nodes that merely CONTAIN other statements. `ast.unparse` renders such a
    #: node together with its whole body, so including them would make every ordering
    #: assertion below trivially true.
    _CONTAINERS = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith,
                   ast.Try, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

    def _statements(self):
        """Every LEAF statement of the function, in source order."""
        out = []

        def walk(body):
            for node in body:
                if not isinstance(node, self._CONTAINERS):
                    out.append((node.lineno, ast.unparse(node)))
                for attr in ("body", "orelse", "finalbody"):
                    inner = getattr(node, attr, None)
                    if isinstance(inner, list):
                        walk(inner)
                for handler in getattr(node, "handlers", []) or []:
                    walk(handler.body)

        walk(self.fn.body)
        return [text_ for _line, text_ in sorted(out)]

    def test_rv03_01_the_clear_is_scoped_by_thread_identity(self):
        """No pointer may be cleared without a `thread_id IN (this tester's threads)`."""
        assigns = [n for n in ast.walk(self.fn)
                   if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Attribute)
                           and t.attr in ("accepted_thread_revision_id",
                                          "accepted_revision_id")
                           for t in n.targets)]
        self.assertTrue(assigns, "the accepted pointers are never cleared")
        for node in assigns:
            self.assertEqual(ast.unparse(node.value), "None",
                             "an accepted pointer is set to something other than None")
        # the offers being cleared come from a thread-scoped select, and from nothing else
        reads = [s for s in self._statements()
                 if "WhatsAppBookingOffer" in s and "select(" in s]
        self.assertTrue(reads, "the offers are not selected through the model at all")
        for stmt in reads:
            self.assertIn("thread_id.in_(thread_ids)", stmt,
                          "an offer read is not scoped to this tester: %r" % stmt)

    def test_rv03_02_no_table_wide_nulling_exists(self):
        """A `WHERE`-less update of the offer table would be the dangerous mutation."""
        src = self.source
        for forbidden in (
                "UPDATE whatsapp_booking_offers SET",
                "update(WhatsAppBookingOffer)",
                "WhatsAppBookingOffer).values(",
        ):
            self.assertNotIn(forbidden, src,
                             "the reset issues a table-wide offer update: %r" % forbidden)
        for stmt in self._statements():
            if "WhatsAppBookingOffer" in stmt and "select(" in stmt:
                self.assertIn("where", stmt,
                              "an unfiltered offer select reaches the clear: %r" % stmt)

    def test_rv03_03_the_pointers_are_cleared_before_any_dependent_deletion(self):
        """Order, on the AST: the clear and its flush precede every `_delete` call."""
        statements = self._statements()
        clear_at = [i for i, s in enumerate(statements)
                    if "accepted_thread_revision_id = None" in s]
        self.assertTrue(clear_at, "the clear is not present")
        deletes = [i for i, s in enumerate(statements) if s.startswith("_delete(")]
        self.assertTrue(deletes, "no deletions are present")
        self.assertLess(max(clear_at), min(deletes),
                        "a pointer is cleared AFTER a dependent deletion has already run")
        flushes = [i for i, s in enumerate(statements) if s == "db.flush()"]
        self.assertTrue(any(max(clear_at) < f < min(deletes) for f in flushes),
                        "the cleared pointers are never flushed before the deletions")

    def test_rv03_04_the_offers_are_deleted_after_the_bookings_that_reference_them(self):
        statements = self._statements()
        def at(fragment):
            return [i for i, s in enumerate(statements) if fragment in s]
        tr = at("'thread_revisions')")
        offers = at("'booking_offers')")
        cands = at("'candidates')")
        threads = at("'threads')")
        msgs = at("'messages')")
        clar = at("'clarification_attempts')")
        for label, got in (("thread_revisions", tr), ("booking_offers", offers),
                           ("candidates", cands), ("threads", threads),
                           ("clarification_attempts", clar)):
            self.assertTrue(got, "the reset never deletes %s" % label)
        self.assertLess(tr[0], offers[0],
                        "the offers are deleted BEFORE the bookings whose offer_id "
                        "references them — fk_thread_revisions_offer_id would refuse")
        self.assertLess(offers[0], cands[0],
                        "the candidates are deleted before the offers that reference them")
        self.assertLess(offers[0], threads[0],
                        "the threads are deleted before the offers that reference them")
        self.assertLess(clar[0], msgs[0],
                        "the clarification rows are deleted after the outbound rows their "
                        "outbound_message_id references")

    def test_rv03_05_the_clear_and_the_deletes_share_one_transaction(self):
        """One commit, at the end, and no rollback or nested begin inside."""
        calls = [ast.unparse(n) for n in ast.walk(self.fn)
                 if isinstance(n, ast.Call)]
        self.assertEqual([c for c in calls if c == "db.commit()"], ["db.commit()"],
                         "the reset must commit exactly once, at the end")
        statements = self._statements()
        self.assertEqual(statements[-1] if statements[-1].startswith("return") else
                         statements[-2], "return result",
                         "the function's shape changed; re-read this assertion")
        commit_at = [i for i, s in enumerate(statements) if s == "db.commit()"][0]
        self.assertTrue(all(i < commit_at for i, s in enumerate(statements)
                            if s.startswith("_delete(")),
                        "a deletion happens after the commit")
        for forbidden in ("db.rollback()", "db.begin(", "begin_nested"):
            self.assertNotIn(forbidden, self.source,
                             "the reset manages its own transaction boundary (%r) — the "
                             "caller must own the rollback" % forbidden)

    def test_rv03_06_no_production_route_calls_the_reset_helper(self):
        """A reset reachable from a route is a way to delete a customer."""
        root = Path(self.T.__file__).resolve().parents[2]   # backend/
        callers = []
        for path in sorted((root / "app").rglob("*.py")):
            if path.name == "tester_reset.py":
                continue
            text_ = path.read_text()
            if "reset_tester_to_zero_state" in text_:
                callers.append(str(path.relative_to(root)))
        self.assertEqual(callers, [],
                         "reset_tester_to_zero_state is reachable from application code: "
                         "%r" % callers)

    def test_rv03_07_the_model_and_the_migration_did_not_change(self):
        """The correction is a reset-tool change. Nothing about the schema moved."""
        import app.models as M
        offer = M.WhatsAppBookingOffer.__table__
        for col, target in (("accepted_thread_revision_id", "thread_revisions.id"),
                            ("accepted_revision_id", "revisions.id"),
                            ("thread_id", "whatsapp_threads.id"),
                            ("candidate_id", "whatsapp_thread_candidates.id")):
            fks = list(offer.c[col].foreign_keys)
            self.assertEqual(len(fks), 1, "%s has %d foreign keys" % (col, len(fks)))
            fk = fks[0]
            self.assertEqual(str(fk.column), target)
            self.assertIsNone(
                fk.ondelete,
                "%s now carries ondelete=%r. A test-support reset must not weaken the "
                "booking authority's referential integrity: the scoped clear in "
                "tester_reset is what makes the reset work." % (col, fk.ondelete))
        tr = M.ThreadRevision.__table__
        offer_fk = list(tr.c.offer_id.foreign_keys)[0]
        self.assertIsNone(offer_fk.ondelete,
                          "thread_revisions.offer_id is the safety control; it must not "
                          "acquire a cascade")
        self.assertEqual(offer_fk.name, M.BOOKING_PROJECTION_FK)

    def test_rv03_08_the_result_reports_what_was_cleared(self):
        from app.services.tester_reset import ResetResult
        r = ResetResult(wa_id="x")
        self.assertEqual(r.cleared_offer_pointers, 0)
        self.assertIn("cleared_offer_pointers", self.source)


if __name__ == "__main__":                              # pragma: no cover
    unittest.main()
