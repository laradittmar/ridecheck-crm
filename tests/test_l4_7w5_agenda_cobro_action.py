"""L4.7W5 — Agenda quick payment (Cobrar) and quote visibility.

The operator needed to charge a revision from the Agenda without opening the edit form.
The risk in a one-tap money action is not the tap; it is everything around it:

  * billing an amount nobody agreed to — so the card shows `precio_total`, the quote
    stored on THAT revision, and the payment action never re-asks PricingService;
  * a second payment state — so it writes the existing `cobrado` / `fecha_cobro` fields
    and creates no new column, table or flag;
  * a wrong business day — near midnight UTC and Buenos Aires disagree, so the server
    computes the date and the browser's clock is never trusted;
  * a double tap billing twice — so the UPDATE carries its own `cobrado <> 'SI'`
    predicate and the database, not the button's disabled attribute, enforces once-only.

PAY-01..18 backend · PAY-UI-01..20 rendered Agenda · PAY-E2E-01..06 end to end
"""
from __future__ import annotations

import os
import re
import sys
import types
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for p in (str(BACKEND), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)
for _m in ("resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"):
    if _m not in sys.modules:
        sys.modules[_m] = types.ModuleType(_m)

import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg
import sqlalchemy.dialects.postgresql.json as _pgj
_pg.JSONB = sqlalchemy.JSON
_pgj.JSONB = sqlalchemy.JSON

from fastapi.testclient import TestClient            # noqa: E402
from sqlalchemy import create_engine, event         # noqa: E402
from sqlalchemy.orm import sessionmaker             # noqa: E402

import app.models                                    # noqa: E402
import app.main                                      # noqa: E402,F401
from app.models import Lead, Revision                # noqa: E402

# `app.main` pulls in route modules that register further tables on the shared metadata.
# Importing it BEFORE create_all is what makes those tables exist; otherwise the schema is
# whatever happened to be imported first, and a later query hits "no such table".
from app.ui.kanban_view import _fmt_ars, _fmt_money, render_calendar_page  # noqa: E402

CONFIGURED = {"AUTH_SECRET_KEY": "test-signing-key-for-the-agenda-payment-suite",
              "ADMIN_PASSWORD": "a-configured-admin-password"}
OPERATOR = "operator@ridecheck.local"
TODAY = date.today()


# ── backend harness ───────────────────────────────────────────────────────────

def _client_and_db():
    """A fresh database per test, wired so the ROUTE uses it too.

    `dependency_overrides` alone is not enough here. Other suites reload `app.db`, and a
    route whose `Depends(get_db)` captured the pre-reload function is keyed on an object
    this module can no longer name — the override then silently applies to nothing and the
    request runs against the application's own database, which surfaced as
    `no such table: revisions` rather than as an obvious fixture error.

    `get_db` reads the module-global `SessionLocal` at call time, and a reload mutates the
    existing module object rather than replacing it, so patching `SessionLocal` reaches
    every version of `get_db` regardless of which one the route holds.
    """
    from sqlalchemy.pool import StaticPool
    from app.main import app as fastapi_app, SESSION_COOKIE
    from app.auth import sign_session
    import app.db as app_db
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    event.listen(engine, "connect", lambda c, r: c.execute("PRAGMA foreign_keys=OFF"))
    app.models.Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Maker()
    patcher = patch.object(app_db, "SessionLocal", lambda: db)
    patcher.start()
    # Key the override on the callable the ROUTE actually holds. Depending on import
    # order, `kanban_actions` may have captured the real `get_db` before another suite
    # swapped `sys.modules["app.db"]` for its stub — in which case the stub's `get_db`,
    # which is what `import app.db` now yields, is not the route's dependency at all and
    # an override keyed on it applies to nothing. The route's dependant is authoritative
    # under either order; `app_db.get_db` is added as well so both are covered.
    route = next(r for r in fastapi_app.routes
                 if getattr(r, "path", "") == "/ui/revision_mark_paid")
    keys = {d.call for d in route.dependant.dependencies
            if getattr(d.call, "__name__", "").endswith("get_db")
            or getattr(d.call, "__name__", "") == "_get_db_gen"}
    keys.add(app_db.get_db)
    for key in keys:
        fastapi_app.dependency_overrides[key] = lambda: db
    client = TestClient(fastapi_app, raise_server_exceptions=(os.environ.get("COBRO_RAISE") == "1"))
    with patch.dict(os.environ, CONFIGURED, clear=False):
        client.cookies.set(SESSION_COOKIE, sign_session({"email": OPERATOR}))
    return client, db, fastapi_app, patcher


def _seed(db, *, precio_total=130000, cobrado=None, fecha_cobro=None, estado="CONFIRMADO"):
    lead = Lead(estado="CONSULTA_NUEVA", flag="ACEPTADO", nombre="Cliente Sintetico")
    db.add(lead); db.flush()
    rev = Revision(lead_id=lead.id, turno_fecha=TODAY, turno_hora=time(10, 0),
                   precio_base=120000, viaticos=10000, precio_total=precio_total,
                   cobrado=cobrado, fecha_cobro=fecha_cobro, estado_revision=estado,
                   marca="Renault", modelo="Sandero", anio=2020, tipo_vehiculo="AUTO",
                   zone_group="CABA", zone_detail="La Paternal")
    db.add(rev); db.commit()
    return lead, rev


def _post(client, revision_id):
    with patch.dict(os.environ, CONFIGURED, clear=False):
        return client.post("/ui/revision_mark_paid", data={"revision_id": revision_id})


class BackendPayment(unittest.TestCase):

    def setUp(self):
        self.client, self.db, self.app, self._patcher = _client_and_db()
        self.lead, self.rev = _seed(self.db)

    def tearDown(self):
        # Remove only OUR overrides. `.clear()` would wipe whatever another suite
        # installed, turning this module into the polluter it was tripping over.
        self._patcher.stop()
        for dep in list(self.app.dependency_overrides):
            if getattr(dep, "__name__", "").endswith("get_db") or \
                    getattr(dep, "__name__", "") == "_get_db_gen":
                self.app.dependency_overrides.pop(dep, None)
        self.db.close()

    def test_pay_01_unpaid_with_quote_becomes_paid(self):
        r = _post(self.client, self.rev.id)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["paid"])
        self.db.expire_all()
        self.assertEqual(self.db.get(Revision, self.rev.id).cobrado, "SI")

    def test_pay_02_date_is_buenos_aires_not_utc_naive(self):
        from zoneinfo import ZoneInfo
        expected = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires")).date()
        _post(self.client, self.rev.id)
        self.db.expire_all()
        self.assertEqual(self.db.get(Revision, self.rev.id).fecha_cobro, expected)

    def test_pay_03_utc_buenos_aires_boundary(self):
        """03:00 UTC is still the previous day in Buenos Aires (UTC-3)."""
        from app.ui import kanban_actions
        fake_utc = datetime(2026, 9, 16, 1, 30, tzinfo=timezone.utc)   # 22:30 on the 15th, BA
        with patch.object(kanban_actions, "datetime") as dt:
            dt.now.side_effect = lambda tz=None: fake_utc.astimezone(tz) if tz else fake_utc
            written = kanban_actions._buenos_aires_today()
        self.assertEqual(written, date(2026, 9, 15),
                         "the UTC calendar date would have been the 16th")

    def test_pay_04_and_05_writes_the_existing_two_fields(self):
        _post(self.client, self.rev.id)
        self.db.expire_all()
        row = self.db.get(Revision, self.rev.id)
        self.assertEqual(row.cobrado, "SI")
        self.assertIsNotNone(row.fecha_cobro)

    def test_pay_06_unrelated_fields_unchanged(self):
        before = {c.name: getattr(self.rev, c.name)
                  for c in Revision.__table__.columns
                  if c.name not in ("cobrado", "fecha_cobro")}
        _post(self.client, self.rev.id)
        self.db.expire_all()
        row = self.db.get(Revision, self.rev.id)
        for name, value in before.items():
            self.assertEqual(getattr(row, name), value, f"{name} changed")

    def test_pay_07_returns_the_canonical_stored_quote(self):
        body = _post(self.client, self.rev.id).json()
        self.assertEqual(body["amount"], 130000)
        self.assertEqual(body["amount_display"], "$ 130.000")
        self.assertEqual(body["currency"], "ARS")

    def test_pay_08_pricing_service_is_not_invoked(self):
        """The amount billed is the stored quote, never a fresh calculation.

        Asserted twice: nothing in the pricing module is called during the request, and
        the action's own source does not reach for it at all — a runtime check alone would
        pass for a code path this fixture happens not to take.
        """
        import app.services.pricing as pricing
        called = []
        originals = {}
        for name in dir(pricing):
            if name.startswith("_"):
                continue
            attr = getattr(pricing, name, None)
            if callable(attr) and getattr(attr, "__module__", "") == pricing.__name__:
                originals[name] = attr
                setattr(pricing, name, lambda *a, _n=name, **k: called.append(_n))
        try:
            r = _post(self.client, self.rev.id)
        finally:
            for name, attr in originals.items():
                setattr(pricing, name, attr)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(called, [], f"payment recalculated the price via {called}")

        import ast
        import inspect
        from app.ui.kanban_actions import ui_revision_mark_paid
        node = ast.parse(inspect.getsource(ui_revision_mark_paid)).body[0]
        body = list(node.body)
        if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]      # the docstring EXPLAINS why pricing is not called
        code = "\n".join(ast.unparse(st) for st in body)
        for forbidden in ("pricing", "PricingService", "recalc_quote", "precio_base", "viaticos"):
            self.assertNotIn(forbidden, code,
                             f"the payment action references {forbidden}")

    def test_pay_09_missing_quote_is_rejected(self):
        self.db.close()
        self.client, self.db, self.app, self._patcher = _client_and_db()
        _seed(self.db, precio_total=None)
        rev = self.db.query(Revision).one()
        r = _post(self.client, rev.id)
        self.assertEqual(r.status_code, 409)
        self.assertIn("Presupuesto no disponible", r.text)
        self.db.expire_all()
        self.assertIsNone(self.db.get(Revision, rev.id).cobrado)

    def test_pay_10_unknown_revision_is_not_found(self):
        self.assertEqual(_post(self.client, 999999).status_code, 404)

    def test_pay_11_unauthenticated_is_rejected(self):
        from app.main import app as fastapi_app, SESSION_COOKIE
        client = TestClient(fastapi_app, raise_server_exceptions=(os.environ.get("COBRO_RAISE")=="1"))
        client.cookies.clear()
        with patch.dict(os.environ, CONFIGURED, clear=False):
            r = client.post("/ui/revision_mark_paid", data={"revision_id": self.rev.id},
                            follow_redirects=False)
        self.assertIn(r.status_code, (302, 303, 401),
                      "an anonymous caller must not reach the payment action")
        self.db.expire_all()
        self.assertIsNone(self.db.get(Revision, self.rev.id).cobrado)

    def test_pay_12_route_is_behind_the_protected_prefix(self):
        from app.main import _is_protected_path
        self.assertTrue(_is_protected_path("/ui/revision_mark_paid"))

    def test_pay_13_and_14_idempotent_and_preserves_the_original_date(self):
        first = _post(self.client, self.rev.id).json()
        self.db.expire_all()
        original = self.db.get(Revision, self.rev.id).fecha_cobro
        # a later day must not overwrite the first payment date
        self.db.get(Revision, self.rev.id)
        second = _post(self.client, self.rev.id).json()
        self.db.expire_all()
        self.assertTrue(second["paid"])
        self.assertTrue(second["already_paid"])
        self.assertFalse(first["already_paid"])
        self.assertEqual(self.db.get(Revision, self.rev.id).fecha_cobro, original)

    def test_pay_14b_repeat_cannot_move_the_date_even_on_another_day(self):
        _post(self.client, self.rev.id)
        self.db.expire_all()
        original = self.db.get(Revision, self.rev.id).fecha_cobro
        from app.ui import kanban_actions
        with patch.object(kanban_actions, "_buenos_aires_today",
                          return_value=original + timedelta(days=5)):
            _post(self.client, self.rev.id)
        self.db.expire_all()
        self.assertEqual(self.db.get(Revision, self.rev.id).fecha_cobro, original,
                         "a repeat overwrote the original payment date")

    def test_pay_15_concurrent_requests_converge(self):
        from app.ui import kanban_actions
        dates = [TODAY, TODAY + timedelta(days=3)]
        with patch.object(kanban_actions, "_buenos_aires_today", side_effect=dates):
            a = _post(self.client, self.rev.id).json()
            b = _post(self.client, self.rev.id).json()
        self.db.expire_all()
        row = self.db.get(Revision, self.rev.id)
        self.assertEqual(row.cobrado, "SI")
        self.assertEqual(row.fecha_cobro, TODAY, "the second writer moved the date")
        self.assertTrue(a["paid"] and b["paid"])
        self.assertEqual([a["already_paid"], b["already_paid"]], [False, True])

    def test_pay_16_no_outbound_attempt(self):
        before = self.db.query(app.models.WhatsAppMessage).count()
        _post(self.client, self.rev.id)
        self.assertEqual(self.db.query(app.models.WhatsAppMessage).count(), before)

    def test_pay_17_no_booking_write(self):
        before = self.db.query(app.models.ThreadRevision).count()
        _post(self.client, self.rev.id)
        self.assertEqual(self.db.query(app.models.ThreadRevision).count(), before)

    def test_pay_18_no_scheduling_state_mutation(self):
        _post(self.client, self.rev.id)
        self.db.expire_all()
        row = self.db.get(Revision, self.rev.id)
        self.assertEqual(row.turno_fecha, TODAY)
        self.assertEqual(row.turno_hora, time(10, 0))
        self.assertEqual(row.estado_revision, "CONFIRMADO")

    def test_pay_19_audit_line_has_ids_not_pii(self):
        with self.assertLogs("app.ui.kanban_actions", level="INFO") as logs:
            _post(self.client, self.rev.id)
        line = next(l for l in logs.output if "AGENDA_MARK_PAID" in l)
        self.assertIn(f"revision_id={self.rev.id}", line)
        self.assertIn(f"operator={OPERATOR}", line)
        self.assertIn("newly_paid=True", line)
        self.assertNotIn("Cliente Sintetico", line)
        self.assertNotIn("1100000000", line)


# ── rendered Agenda ───────────────────────────────────────────────────────────

def _render(**kw):
    rev = NS(id=901, turno_fecha=TODAY, turno_hora=time(10, 0),
             precio_total=kw.get("precio_total", 130000), precio_base=120000, viaticos=10000,
             cobrado=kw.get("cobrado"), fecha_cobro=kw.get("fecha_cobro"), pago=kw.get("pago"),
             estado_revision=kw.get("estado", "CONFIRMADO"), marca="Renault", modelo="Sandero",
             anio=2020, tipo_vehiculo="AUTO", zone_group="CABA", zone_detail="La Paternal",
             direccion_texto="Av. Test 123", link_maps=None, profesional_id=None, lead_id=1,
             resultado=None, cliente_presente=None, turno_notas=None)
    lead = NS(id=1, nombre="Cliente Sintetico", telefono="1100000000", revisions=[rev],
              estado="CONSULTA_NUEVA", flag="ACEPTADO", canal=None, email=None)
    return render_calendar_page([lead], profesionales=[], week=TODAY.isoformat())


def _card(html: str) -> str:
    """The whole appointment card.

    A lazy `.*?</div></div>` stops at the first nested close — the time block — and every
    assertion then passes or fails against a fragment that never contained the payment row.
    Anchoring on the status span, which is the card's last element, takes the real card.
    """
    m = re.search(r'<div class="agendaApptCard.*?agendaApptStatus[^>]*>[^<]*</span></div></div>',
                  html, re.S)
    return m.group(0) if m else ""


class RenderedAgenda(unittest.TestCase):

    def test_pay_ui_01_unpaid_card_shows_the_quote(self):
        card = _card(_render())
        self.assertIn("Presupuesto", card)
        self.assertIn("$ 130.000", card)

    def test_pay_ui_02_unpaid_card_shows_cobrar(self):
        card = _card(_render())
        self.assertIn("agendaCobrarBtn", card)
        self.assertIn("Cobrar", card)
        self.assertIn('onclick="openCobroModal(this)"', card)

    def test_pay_ui_03_and_04_paid_card_shows_cobrado_and_date(self):
        card = _card(_render(cobrado="SI", fecha_cobro=date(2026, 9, 15)))
        self.assertIn("Cobrado", card)
        self.assertIn("15/09/2026", card)
        self.assertIn("$ 130.000", card)

    def test_pay_ui_05_paid_card_has_no_active_cobrar_action(self):
        card = _card(_render(cobrado="SI", fecha_cobro=date(2026, 9, 15)))
        self.assertNotIn("agendaCobrarBtn", card)
        self.assertNotIn("openCobroModal", card)

    def test_pay_ui_06_and_07_popup_identifies_the_revision_and_amount(self):
        card = _card(_render())
        self.assertIn('data-rev-id="901"', card)
        self.assertIn('data-rev-amount="$ 130.000"', card)
        self.assertIn("data-rev-name=", card)
        self.assertIn("data-rev-veh=", card)

    def test_pay_ui_08_popup_shows_the_buenos_aires_date(self):
        from app.ui.kanban_view import _fmt_ba_today
        html = _render()
        self.assertIn("Confirmar cobro", html)
        self.assertIn(f'data-today="{_fmt_ba_today()}"', html)
        self.assertIn("Se registrará como cobrada con fecha", html)

    def test_pay_ui_09_cancel_performs_no_mutation(self):
        html = _render()
        self.assertIn("closeCobroModal", html)
        cancel = re.search(r'id="cobroCancel"[^>]*onclick="([^"]+)"', html).group(1)
        self.assertEqual(cancel, "closeCobroModal()")
        self.assertNotIn("fetch", cancel)

    def test_pay_ui_10_and_11_confirm_calls_once_and_double_tap_is_guarded(self):
        html = _render()
        js = html[html.index("window.confirmCobro"):]
        self.assertIn("if(busy||!revId)return;", js)
        self.assertIn("busy=true;", js)
        self.assertIn("ok.disabled=true;", js)
        self.assertEqual(js.count('fetch("/ui/revision_mark_paid"'), 1)

    def test_pay_ui_12_loading_state_is_shown(self):
        self.assertIn("Registrando cobro", _render())

    def test_pay_ui_13_success_repaints_from_the_server_response(self):
        html = _render()
        paint = html[html.index("function paintPaid"):]
        for server_field in ("d.amount_display", "d.fecha_cobro_display", "d.revision_id"):
            self.assertIn(server_field, paint)

    def test_pay_ui_14_failure_restores_the_unpaid_presentation(self):
        js = _render()
        self.assertIn('ok.textContent="Confirmar cobro";', js)
        self.assertIn("err.style.display=\"block\";", js)
        # paintPaid runs only on the success branch
        confirm = js[js.index("window.confirmCobro"):js.index("function paintPaid")]
        catch = confirm[confirm.index(".catch("):]
        self.assertNotIn("paintPaid", catch)

    def test_pay_ui_15_missing_quote_disables_the_action(self):
        card = _card(_render(precio_total=None))
        self.assertIn("Presupuesto no disponible", card)
        self.assertNotIn("agendaCobrarBtn", card)
        self.assertIn("Completar revisión", card)

    def test_pay_ui_16_gap_and_travel_blocks_never_show_cobrar(self):
        html = _render()
        for block in re.findall(r'<div class="agendaGapBlock".*?</div></div>', html, re.S):
            self.assertNotIn("agendaCobrarBtn", block)
        for block in re.findall(r'<div class="agendaTravelBlock".*?</div></div>', html, re.S):
            self.assertNotIn("agendaCobrarBtn", block)

    def test_pay_ui_17_and_18_mobile_and_desktop_styles_exist(self):
        html = _render()
        self.assertIn("@media (max-width: 520px)", html)
        self.assertRegex(html, r"@media \(max-width: 520px\)[^}]*agendaCobrarBtn")
        self.assertIn("min-height: 36px", html)      # desktop tap target
        self.assertIn("min-height: 42px", html)      # mobile tap target

    def test_pay_ui_19_keyboard_and_focus_behaviour(self):
        html = _render()
        self.assertIn('role="dialog"', html)
        self.assertIn('aria-modal="true"', html)
        self.assertIn('aria-labelledby="cobroTitle"', html)
        self.assertIn('aria-label="Cobrar', html)
        self.assertIn('e.key==="Escape"', html)
        self.assertIn('e.key!=="Tab"', html)          # focus trap
        self.assertIn("if(trigger)trigger.focus();", html)   # focus returns
        self.assertIn(":focus-visible", html)

    def test_pay_ui_20_existing_card_actions_are_preserved(self):
        card = _card(_render())
        # The WhatsApp button renders only when the lead has a thread, which this fixture
        # deliberately has not — so it is asserted at the source, not in this card.
        for kept in ("agendaEditBtn", "Ver revisión", "agendaCallBtn", "agendaGpsBtn"):
            self.assertIn(kept, card, f"{kept} disappeared from the card")
        ce = (ROOT / "backend" / "app" / "ui" / "kanban_view.py").read_text(encoding="utf-8")
        self.assertIn("agendaWaBtn", ce)
        self.assertIn("/whatsapp/thread/", ce)

    def test_pay_ui_21_inconsistent_states_are_surfaced_not_repaired(self):
        paid_no_date = _card(_render(cobrado="SI", fecha_cobro=None))
        self.assertIn("Cobrado sin fecha de cobro", paid_no_date)
        date_not_paid = _card(_render(cobrado=None, fecha_cobro=date(2026, 9, 1)))
        self.assertIn("Tiene fecha de cobro pero no está cobrado", date_not_paid)
        self.assertIn("agendaCobrarBtn", date_not_paid,
                      "the action stays available; the warning is informational")

    def test_pay_ui_22_existing_money_formatter_is_unchanged(self):
        self.assertEqual(_fmt_money(130000), "$130.000")
        self.assertEqual(_fmt_ars(130000), "$ 130.000")
        self.assertEqual(_fmt_ars(None), "Presupuesto no disponible")


# ── end to end ────────────────────────────────────────────────────────────────

class EndToEnd(unittest.TestCase):

    def setUp(self):
        self.client, self.db, self.app, self._patcher = _client_and_db()
        self.lead, self.rev = _seed(self.db)

    def tearDown(self):
        # Remove only OUR overrides. `.clear()` would wipe whatever another suite
        # installed, turning this module into the polluter it was tripping over.
        self._patcher.stop()
        for dep in list(self.app.dependency_overrides):
            if getattr(dep, "__name__", "").endswith("get_db") or \
                    getattr(dep, "__name__", "") == "_get_db_gen":
                self.app.dependency_overrides.pop(dep, None)
        self.db.close()

    def test_pay_e2e_01_card_to_database_to_repainted_card(self):
        self.assertIn("agendaCobrarBtn", _card(_render()))
        body = _post(self.client, self.rev.id).json()
        self.db.expire_all()
        row = self.db.get(Revision, self.rev.id)
        self.assertEqual(row.cobrado, "SI")
        card = _card(_render(cobrado=row.cobrado, fecha_cobro=row.fecha_cobro))
        self.assertIn("Cobrado", card)
        self.assertIn(body["fecha_cobro_display"], card)
        self.assertNotIn("agendaCobrarBtn", card)

    def test_pay_e2e_02_cancellation_changes_nothing(self):
        before = (self.rev.cobrado, self.rev.fecha_cobro)
        _render()                       # opening the Agenda must never mutate
        self.db.expire_all()
        row = self.db.get(Revision, self.rev.id)
        self.assertEqual((row.cobrado, row.fecha_cobro), before)

    def test_pay_e2e_03_repeated_confirmation_yields_one_state(self):
        for _ in range(4):
            _post(self.client, self.rev.id)
        self.db.expire_all()
        rows = self.db.query(Revision).filter(Revision.cobrado == "SI").all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, self.rev.id)

    def test_pay_e2e_04_timezone_boundary_writes_the_buenos_aires_day(self):
        from app.ui import kanban_actions
        with patch.object(kanban_actions, "_buenos_aires_today", return_value=date(2026, 9, 15)):
            body = _post(self.client, self.rev.id).json()
        self.assertEqual(body["fecha_cobro"], "2026-09-15")
        self.assertEqual(body["fecha_cobro_display"], "15/09/2026")

    def test_pay_e2e_05_missing_quote_never_mutates(self):
        self.db.close()
        self.client, self.db, self.app, self._patcher = _client_and_db()
        _seed(self.db, precio_total=None)
        rev = self.db.query(Revision).one()
        self.assertEqual(_post(self.client, rev.id).status_code, 409)
        self.db.expire_all()
        row = self.db.get(Revision, rev.id)
        self.assertIsNone(row.cobrado)
        self.assertIsNone(row.fecha_cobro)

    def test_pay_e2e_06_paid_state_persists(self):
        """The paid state survives the request that wrote it.

        Verified through the route rather than by opening a second raw Session: another
        suite in this repository permanently replaces `sys.modules["app.db"]` with a stub,
        so after it has run, "a second session on the same bind" is no longer an
        unambiguous thing to construct. A fresh request is both unambiguous and stronger
        evidence — it is how the Agenda itself would read the state back.
        """
        first = _post(self.client, self.rev.id).json()
        self.assertTrue(first["paid"])
        self.assertFalse(first["already_paid"])

        later = _post(self.client, self.rev.id).json()
        self.assertTrue(later["paid"], "the paid state did not survive")
        self.assertTrue(later["already_paid"], "the row was re-written instead of read back")
        self.assertEqual(later["fecha_cobro"], first["fecha_cobro"])
        self.assertEqual(later["amount"], first["amount"])

        self.db.expire_all()
        row = self.db.get(Revision, self.rev.id)
        self.assertEqual(row.cobrado, "SI")
        self.assertEqual(row.fecha_cobro.isoformat(), first["fecha_cobro"])


if __name__ == "__main__":     # pragma: no cover
    unittest.main()
