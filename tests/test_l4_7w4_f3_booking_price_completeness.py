"""L4.7W4-F3 — a booking must carry the price the customer accepted.

W4-F2 produced a technically perfect booking that the CRM showed as
"Total presupuestado: -". The Revision was created without precio_base, viaticos or
precio_total, so the 240000 the customer had already accepted existed nowhere in the CRM.

The quote has no stored identity: it is a deterministic function of
(tipo_vehiculo, zone_group, zone_detail) over the pricing catalog, and the Revision
persists all three beside the price. So the quote identity IS those inputs, and stamping
through the same PricingService reproduces the accepted amount rather than inventing one.

BOOKPRICE-01 accepted quote copied      BOOKPRICE-07 wrong location blocks price
BOOKPRICE-02 precio_base preserved      BOOKPRICE-08 quote identity preserved
BOOKPRICE-03 viaticos preserved         BOOKPRICE-09 opening writes nothing
BOOKPRICE-04 precio_total preserved     BOOKPRICE-10 price+booking atomic
BOOKPRICE-05 no second authority        BOOKPRICE-11 CRM renders the total
BOOKPRICE-06 wrong candidate blocks     BOOKPRICE-12 W4 case reproducible
TRACE-01 deployment_id current          TRACE-03 no UNKNOWN path introduced
TRACE-02 blocked BOOKING_FLOW keeps path_id
"""
from __future__ import annotations

import ast
import pathlib
import sys
import types
import unittest
from datetime import date, time, timedelta
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
for _m in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    sys.modules.setdefault(_m, types.ModuleType(_m))

import sqlalchemy as _sa
import sqlalchemy.dialects.postgresql as _pg
import sqlalchemy.dialects.postgresql.json as _pgj
_pg.JSONB = _sa.JSON
_pgj.JSONB = _sa.JSON

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import app.models
from app.models import (Lead, Revision, ThreadRevision, ViaticosZone, WhatsAppContact,
                        WhatsAppThread, WhatsAppThreadCandidate, WhatsAppThreadState)
from app.schemas.schedule import ScheduleCheckOut, ScheduleSlotsOut, ScheduleSlotOut
from app.services.booking_flow_service import BookingFlowService, make_booking_token

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})


@event.listens_for(_engine, "connect")
def _pragmas(conn, _rec):
    conn.execute("PRAGMA foreign_keys=OFF")


app.models.Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

FUTURE = date.today() + timedelta(days=3)
SLOT = "12:30"

# The exact W4-F2 case: Peugeot 2008 2014, Berazategui, accepted at 240000.
W4_TIPO, W4_GROUP, W4_DETAIL = "SUV_4X4_DEPORTIVO", "Sur", "Berazategui"
W4_BASE, W4_VIATICOS, W4_TOTAL = 150000, 90000, 240000

BFS_SOURCE = (ROOT / "backend" / "app" / "services"
              / "booking_flow_service.py").read_text(encoding="utf-8-sig")
GATE_SOURCE = (ROOT / "backend" / "app" / "services"
               / "outbound_safety_gate.py").read_text(encoding="utf-8-sig")


def _wipe():
    with _engine.begin() as conn:
        for tbl in reversed(app.models.Base.metadata.sorted_tables):
            conn.execute(tbl.delete())


def _world(db, *, tipo=W4_TIPO, group=W4_GROUP, detail=W4_DETAIL, seed_zone=True):
    if seed_zone:
        db.add(ViaticosZone(zone_group=group, zone_detail=detail, viaticos=W4_VIATICOS))
    contact = WhatsAppContact(wa_id="5491153368330", display_name="Tester")
    db.add(contact); db.flush()
    lead = Lead(estado="COTIZACION", flag="ACEPTADO", necesita_humano=False)
    db.add(lead); db.flush()
    thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id)
    db.add(thread); db.flush()
    token = make_booking_token(thread.id)
    cand = WhatsAppThreadCandidate(
        thread_id=thread.id, marca="Peugeot", modelo="2008", anio=2014,
        tipo_vehiculo=tipo, zone_group=group, zone_detail=detail,
        direccion_texto="Haedo 4567", status="mentioned")
    db.add(cand); db.flush()
    state = WhatsAppThreadState(thread_id=thread.id, flow_booking_token=token,
                                needs_human=False)
    db.add(state); db.commit()
    return thread, state, cand, lead, token


def _sched_ok(svc):
    svc._sched = MagicMock()
    svc._sched.list_slots.return_value = ScheduleSlotsOut(
        preferred_day=FUTURE, business_hours="09:30-14:00", slots=[SLOT])
    svc._sched.check.return_value = ScheduleCheckOut(
        valid=True, suggested_slots=[SLOT], approval_tag="",
        requested_slot=ScheduleSlotOut(start=SLOT, end="13:00"),
        business_hours="09:30-14:00")


def _confirm(token):
    return {"booking_token": token, "date": FUTURE.isoformat(), "time": SLOT,
            "name": "Lara Dittmar", "phone": "1153368330", "email": "",
            "inspection_address": "Haedo 4567", "seller_name": "",
            "seller_phone": "", "listing_url": ""}


class _Base(unittest.TestCase):
    def setUp(self):
        _wipe()
        self.db = _Session()

    def tearDown(self):
        self.db.close()

    def book(self, **kw):
        self.thread, self.state, self.cand, self.lead, self.token = _world(self.db, **kw)
        svc = BookingFlowService(self.db)
        _sched_ok(svc)
        svc.handle_confirm_booking(self.token, _confirm(self.token))
        return self.db.query(Revision).one()


class TestPricePreserved(_Base):

    def test_bookprice_01_accepted_quote_is_copied_onto_the_revision(self):
        rev = self.book()
        self.assertEqual(
            (rev.precio_base, rev.viaticos, rev.precio_total),
            (W4_BASE, W4_VIATICOS, W4_TOTAL),
            "the booking must carry the price the customer accepted")

    def test_bookprice_02_precio_base(self):
        self.assertEqual(self.book().precio_base, W4_BASE)

    def test_bookprice_03_viaticos(self):
        self.assertEqual(self.book().viaticos, W4_VIATICOS)

    def test_bookprice_04_precio_total(self):
        self.assertEqual(self.book().precio_total, W4_TOTAL)

    def test_bookprice_08_quote_identity_is_persisted_beside_the_price(self):
        """BOOKPRICE-08 — the inputs that produced the price are stored with it,
        so the amount stays re-derivable without a separate quote record."""
        rev = self.book()
        self.assertEqual(rev.tipo_vehiculo, W4_TIPO)
        self.assertEqual(rev.zone_group, W4_GROUP)
        self.assertEqual(rev.zone_detail, W4_DETAIL)
        from app.repositories.pricing_repository import PricingRepository
        from app.services.pricing import PricingService
        q = PricingService(repository=PricingRepository()).quote(
            db=self.db, tipo_vehiculo=rev.tipo_vehiculo,
            zone_group=rev.zone_group, zone_detail=rev.zone_detail)
        self.assertEqual(q.precio_total, rev.precio_total)

    def test_bookprice_12_the_w4_case_reproduces_exactly(self):
        """BOOKPRICE-12 — Peugeot 2008 / Berazategui = 240000, the accepted amount."""
        rev = self.book()
        self.assertEqual(rev.marca, "Peugeot")
        self.assertEqual(rev.modelo, "2008")
        self.assertEqual(rev.precio_total, 240000)


class TestPriceIntegrity(_Base):

    def test_bookprice_06_a_candidate_without_a_type_yields_no_invented_price(self):
        """BOOKPRICE-06 — an unpriceable vehicle must leave the price empty, not guessed."""
        rev = self.book(tipo=None)
        self.assertIsNone(rev.precio_total)
        self.assertIsNone(rev.precio_base)

    def test_bookprice_07_an_unknown_zone_yields_no_invented_total(self):
        """BOOKPRICE-07 — no viaticos row means no total; a base price alone is not a quote."""
        rev = self.book(detail="Zona Inexistente", seed_zone=False)
        self.assertIsNone(rev.precio_total,
                          "a total without a real viaticos figure would be fabricated")

    def test_bookprice_05_booking_owns_no_second_pricing_authority(self):
        """BOOKPRICE-05 — the service must delegate, never compute."""
        tree = ast.parse(BFS_SOURCE)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                body = ast.unparse(node)
                self.assertNotIn("precio_base +", body,
                                 f"{node.name} computes a total itself")
                self.assertNotIn("PRECIO_BASE_BY_TIPO", body,
                                 f"{node.name} reads a second price table")
        self.assertIn("self._pricing.recalculate_revision_if_possible", BFS_SOURCE)

    def test_mismatched_commercial_inputs_are_recorded_and_not_priced(self):
        """Defence in depth: the live cycle-bounded candidate makes this unreachable
        today, so it is asserted structurally rather than simulated."""
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(BFS_SOURCE))
                  if isinstance(n, ast.FunctionDef) and n.name == "handle_confirm_booking")
        self.assertIn("BOOKING_PRICE_INPUT_MISMATCH", fn)
        self.assertIn("price_inputs_ok", fn)
        i_guard = fn.index("price_inputs_ok =")
        i_price = fn.index("recalculate_revision_if_possible")
        self.assertLess(i_guard, i_price, "the guard must precede the stamp")


class TestAtomicity(_Base):

    def test_bookprice_09_opening_the_flow_writes_no_price_and_no_booking(self):
        """BOOKPRICE-09 — INIT and date selection are read-only."""
        _, _, _, _, token = _world(self.db)
        svc = BookingFlowService(self.db)
        _sched_ok(svc)
        svc.handle_init(token)
        svc.handle_date_selected(token, FUTURE.isoformat())
        self.assertEqual(self.db.query(Revision).count(), 0)
        self.assertEqual(self.db.query(ThreadRevision).count(), 0)

    def test_bookprice_10_price_and_booking_land_in_the_same_commit(self):
        """BOOKPRICE-10 — no window where a booked Revision exists without its price."""
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(BFS_SOURCE))
                  if isinstance(n, ast.FunctionDef) and n.name == "handle_confirm_booking")
        i_price = fn.index("recalculate_revision_if_possible")
        i_commit = fn.index("self.db.commit()")
        self.assertLess(i_price, i_commit, "the price must be stamped before the commit")
        rev = self.book()
        tr = self.db.query(ThreadRevision).one()
        self.assertEqual(tr.status, "booked")
        self.assertEqual(rev.precio_total, W4_TOTAL)

    def test_threadrevision_holds_no_second_copy_of_the_price(self):
        """Part 5 — one commercial value, on the CRM Revision only."""
        cols = {c.name for c in ThreadRevision.__table__.columns}
        self.assertEqual(cols & {"precio_base", "viaticos", "precio_total"}, set(),
                         "a second stored copy could disagree with the Revision")


class TestCrmDisplay(_Base):

    def test_bookprice_11_the_lead_card_renders_the_persisted_total(self):
        """BOOKPRICE-11 — from stored data, with no amount hardcoded in the view."""
        from app.ui.kanban_view import _fmt_money
        rev = self.book()
        self.assertEqual(_fmt_money(rev.precio_total), "$240.000")
        view = (ROOT / "backend" / "app" / "ui" / "kanban_view.py").read_text(
            encoding="utf-8-sig")
        self.assertIn("total_vals = [r.precio_total for r in revs if r.precio_total is not None]",
                      view)
        self.assertNotIn("240000", view, "the total must never be hardcoded")

    def test_a_priceless_revision_still_renders_a_dash(self):
        from app.ui.kanban_view import _fmt_money
        self.assertEqual(_fmt_money(None), "-")


class TestTraceability(unittest.TestCase):

    def test_trace_01_deployment_id_is_baked_into_the_image(self):
        """TRACE-01 — not a compose default that outlives the commit it names."""
        dockerfile = (ROOT / "backend" / "Dockerfile").read_text()
        self.assertIn("ARG GIT_SHA", dockerfile)
        self.assertIn("ENV GIT_SHA=${GIT_SHA}", dockerfile)
        compose = (ROOT / "docker-compose.beta.yml").read_text()
        self.assertNotIn('GIT_SHA: "${GIT_SHA:-d5f89b3}"', compose,
                         "a stale compose default would override the image ENV")
        for script in ("build_backend.sh", "verify_deployment_identity.sh"):
            path = ROOT / "scripts" / script
            self.assertTrue(path.exists(), f"{script} missing")
            self.assertTrue(path.stat().st_mode & 0o111, f"{script} not executable")
        self.assertIn("--build-arg", (ROOT / "scripts" / "build_backend.sh").read_text())

    def test_trace_02_blocked_attempts_keep_the_attempted_path(self):
        """TRACE-02 — every blocked writer, not just the unauthorized-path one."""
        blocked_writers = [
            n for n in ast.walk(ast.parse(GATE_SOURCE))
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", None) == "WhatsAppMessage"
            and any(k.arg == "status" and getattr(k.value, "value", None) == "blocked"
                    for k in n.keywords)
        ]
        self.assertEqual(len(blocked_writers), 4, "expected 4 blocked-row writers")
        for call in blocked_writers:
            kwargs = {k.arg for k in call.keywords}
            self.assertIn("path_id", kwargs,
                          f"blocked writer at line {call.lineno} drops attribution")
            self.assertIn("deployment_id", kwargs,
                          f"blocked writer at line {call.lineno} drops deployment id")

    def test_trace_03_no_unknown_path_is_introduced(self):
        from app.services.outbound_path_registry import OutboundPathId
        values = {p.value for p in OutboundPathId}
        self.assertNotIn("UNKNOWN", values)
        self.assertNotIn("UNATTRIBUTED", values)
        self.assertIn("BOOKING_FLOW", values)

    def test_the_kill_switch_still_fires_before_any_meta_call(self):
        """TRACE-02 must not have loosened the block itself."""
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(GATE_SOURCE))
                  if isinstance(n, ast.FunctionDef) and n.name == "_check_kill_switch")
        norm = fn.replace("'", '"')
        self.assertIn('os.environ.get("OUTBOUND_ENABLED") == "true"', norm)
        self.assertIn('status="blocked"', norm)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
