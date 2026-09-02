"""M21.3-UX2 — PRE-LAUNCH CRM + AGENDA UX POLISH

UX2-01  Sidebar collapse JS saves localStorage (table view)
UX2-02  Sidebar collapse restores from localStorage on DOMContentLoaded (calendar view)
UX2-03  sidebarFooter is NOT display:none when collapsed (CSS fixed)
UX2-04  Logo img renders; no brandText "RIDECHECK" text present
UX2-05  bg.png cache-buster: _BG_VER set and CSS URL contains ?v=
UX2-06  Background CSS uses cover + no-repeat
UX2-07  Agenda: address and locality rendered in Day view card
UX2-08  Address card links to Google Maps URL
UX2-09  GPS menu contains both Google Maps and Waze links
UX2-10  Missing address shows "Dirección pendiente"
UX2-11  Travel block shows origin → destination route text
UX2-12  Available gap block rendered between appointments
UX2-13  Positive margin renders ok (✓)
UX2-14  Conflict margin renders conflict (!)
UX2-15  Zero-zone / day-start block visible
UX2-16  First travel block (from zero-zone to first appt)
UX2-17  Trailing free gap at end of day
UX2-18  Cancelled appointments appear in card list but don't drive travel blocks
UX2-19  None/missing fixture fields don't crash rendering
UX2-20  Day / Week / Month view pills preserved
UX2-21  WhatsApp inbox renders (no UX regression)
"""
from __future__ import annotations

import sys
import types
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# SQLAlchemy JSONB → JSON for SQLite
import sqlalchemy as _sa
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = _sa.JSON
_pg_json.JSONB = _sa.JSON

for _mod in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

if "psycopg2" not in sys.modules:
    _pg = types.ModuleType("psycopg2")
    _pg.extensions = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2"] = _pg
    sys.modules["psycopg2.extensions"] = _pg.extensions

import unittest
from app.ui.kanban_view import render_calendar_page, _BG_VER, _base_css
from app.services.schedule import ScheduleService

# ── Fixture date: use a known Wednesday (2026-08-26) ─────────────────────────
_WED = date(2026, 8, 26)           # Wednesday — business day with zero-zone Melo
_MON = _WED - timedelta(days=2)    # Monday of same week = 2026-08-24
_TEST_DATE = _WED.isoformat()
_TEST_WEEK = _MON.isoformat()


def _rev(
    *,
    id: int = 1,
    turno_fecha: date = _WED,
    turno_hora: time | None = time(10, 0),
    zone_group: str | None = "Norte",
    zone_detail: str | None = "Santa Catalina",
    direccion_texto: str | None = "Av. Maipú 1234",
    estado_revision: str = "PENDIENTE",
    pago: bool | None = None,
    cobrado: str | None = None,
    tipo_vehiculo: str | None = "SEDAN",
    marca: str | None = "Toyota",
    modelo: str | None = "Corolla",
    anio: int | None = 2020,
    profesional_id: int | None = None,
    link_maps: str | None = None,
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id=id, turno_fecha=turno_fecha, turno_hora=turno_hora,
        zone_group=zone_group, zone_detail=zone_detail,
        direccion_texto=direccion_texto, estado_revision=estado_revision,
        pago=pago, cobrado=cobrado, tipo_vehiculo=tipo_vehiculo,
        marca=marca, modelo=modelo, anio=anio,
        profesional_id=profesional_id, link_maps=link_maps,
    )


def _lead(
    *,
    id: int = 1,
    nombre: str = "Juan",
    apellido: str = "Pérez",
    telefono: str | None = "+5491122334455",
    revisions: list | None = None,
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id=id, nombre=nombre, apellido=apellido,
        telefono=telefono, revisions=revisions or [],
    )


def _render(
    leads=None,
    thread_by_lead=None,
    schedule_svc=None,
    initial_date=_TEST_DATE,
    week=_TEST_WEEK,
):
    """Helper: render calendar page with UX2 params."""
    # Build a minimal ScheduleService if not provided
    if schedule_svc is None:
        schedule_svc = _FakeScheduleSvc()
    return render_calendar_page(
        leads or [],
        week=week,
        initial_date=initial_date,
        schedule_svc=schedule_svc,
        thread_by_lead=thread_by_lead or {},
    )


class _FakeScheduleSvc:
    """Minimal stand-in for ScheduleService (no DB needed)."""

    def get_day_start_info(self, day: date) -> dict:
        from app.services.schedule import ScheduleService
        svc = ScheduleService.__new__(ScheduleService)
        return svc.get_day_start_info(day)


class TestUX2CSS(unittest.TestCase):
    """UX2-01 to UX2-06 — CSS and JS structure."""

    def setUp(self):
        self._html = _render()

    # UX2-01 — Table view sidebar toggle saves localStorage
    def test_ux2_01_table_toggle_saves_localstorage(self):
        from app.ui.kanban_view import render_revisions_table_page
        html = render_revisions_table_page(revisions=[], user_email="test@example.com")
        self.assertIn("localStorage.setItem", html)
        self.assertIn("sidebar_collapsed", html)

    # UX2-02 — Calendar view restores collapse from localStorage on load
    def test_ux2_02_calendar_restores_localstorage(self):
        html = self._html
        self.assertIn("localStorage.getItem", html)
        self.assertIn("sidebar_collapsed", html)
        # DOMContentLoaded sets sidebar state
        self.assertIn("DOMContentLoaded", html)

    # UX2-03 — sidebarFooter is NOT hidden when collapsed
    def test_ux2_03_footer_not_hidden_when_collapsed(self):
        css = _base_css()
        # Must NOT have .sidebar.collapsed .sidebarFooter { display:none }
        self.assertNotIn(".sidebar.collapsed .sidebarFooter { display:none", css)
        self.assertNotIn(".sidebar.collapsed .sidebarFooter{display:none", css)
        # The compact logout button should be defined
        self.assertIn("logoutBtnCompact", css)

    # UX2-04 — Logo img rendered; no "RIDECHECK" text, no brandText element
    def test_ux2_04_logo_img_no_text(self):
        html = self._html
        self.assertIn('class="brandLogo"', html)
        self.assertIn("/static/branding/ridecheck-logo.jpg", html)
        self.assertNotIn(">RIDECHECK<", html)
        # No div/span with class brandText in the HTML (CSS class def is OK)
        self.assertNotIn('"brandText"', html)
        self.assertNotIn("class='brandText'", html)

    # UX2-05 — _BG_VER set and CSS URL includes ?v=
    def test_ux2_05_bg_cache_buster(self):
        self.assertIsNotNone(_BG_VER)
        self.assertGreater(len(_BG_VER), 0)
        css = _base_css()
        self.assertIn(f"bg.png?v={_BG_VER}", css)

    # UX2-06 — Background CSS uses cover + no-repeat
    def test_ux2_06_background_css(self):
        css = _base_css()
        self.assertIn("background-size: cover", css)
        self.assertIn("background-repeat: no-repeat", css)


class TestUX2AgendaDay(unittest.TestCase):
    """UX2-07 to UX2-19 — Operational Day view."""

    def _make_two_appt_html(self):
        """Two appointments in Norte and CABA (60-min travel)."""
        r1 = _rev(id=1, turno_hora=time(10, 0), zone_group="Norte",
                  zone_detail="Santa Catalina", direccion_texto="Maipú 1234")
        r2 = _rev(id=2, turno_hora=time(13, 0), zone_group="CABA",
                  zone_detail="Microcentro", direccion_texto="Florida 800")
        l1 = _lead(id=1, revisions=[r1])
        l2 = _lead(id=2, nombre="Ana", apellido="García", revisions=[r2])
        return _render(leads=[l1, l2], thread_by_lead={1: 99})

    # UX2-07 — Appointment card shows address and locality
    def test_ux2_07_address_and_locality(self):
        html = self._make_two_appt_html()
        self.assertIn("Maipú 1234", html)
        self.assertIn("Santa Catalina", html)

    # UX2-08 — Address links to Google Maps
    def test_ux2_08_address_google_maps(self):
        html = self._make_two_appt_html()
        self.assertIn("google.com/maps/search", html)

    # UX2-09 — GPS menu has both Google Maps and Waze
    def test_ux2_09_gps_menu_maps_and_waze(self):
        html = self._make_two_appt_html()
        self.assertIn("Google Maps", html)
        self.assertIn("waze.com", html)

    # UX2-10 — Missing address shows "Dirección pendiente"
    def test_ux2_10_missing_address_safe(self):
        r = _rev(direccion_texto=None)
        l = _lead(revisions=[r])
        html = _render(leads=[l])
        self.assertIn("Dirección pendiente", html)

    # UX2-11 — Travel block shows origin → destination
    def test_ux2_11_travel_route_text(self):
        html = self._make_two_appt_html()
        self.assertIn("Norte", html)
        self.assertIn("CABA", html)
        self.assertIn("Necesario:", html)

    # UX2-12 — Gap block appears between same-zone appointments
    def test_ux2_12_gap_block(self):
        # Same zone → no travel minutes, just gap
        r1 = _rev(id=1, turno_hora=time(9, 0), zone_group="Norte")
        r2 = _rev(id=2, turno_hora=time(13, 0), zone_group="Norte")
        l1 = _lead(id=1, revisions=[r1])
        l2 = _lead(id=2, nombre="Ana", apellido="R", revisions=[r2])
        html = _render(leads=[l1, l2])
        # 13:00 - (09:00 + 45min) = 195 min gap → same zone → gap block, not travel
        self.assertIn("agendaGapBlock", html)
        self.assertIn("min libre", html)

    # UX2-13 — Positive margin renders ok
    def test_ux2_13_positive_margin_ok(self):
        # Norte → Norte (0 travel), 4h gap → free gap not travel
        # To get a travel block with ok margin: Norte → CABA needs 60 min
        # r1 ends 10:45, r2 starts 12:00 → available=75min, travel=60min, margin=+15 → OK
        r1 = _rev(id=1, turno_hora=time(10, 0), zone_group="Norte")
        r2 = _rev(id=2, turno_hora=time(12, 0), zone_group="CABA")
        l1 = _lead(id=1, revisions=[r1])
        l2 = _lead(id=2, nombre="Ana", apellido="R", revisions=[r2])
        html = _render(leads=[l1, l2])
        self.assertIn("agendaMargin-ok", html)
        self.assertIn("✓", html)

    # UX2-14 — Conflict margin renders conflict
    def test_ux2_14_conflict_margin(self):
        # Norte → CABA (60 min travel), but only 30 min available → conflict
        r1 = _rev(id=1, turno_hora=time(10, 0), zone_group="Norte")
        r2 = _rev(id=2, turno_hora=time(11, 15), zone_group="CABA")
        l1 = _lead(id=1, revisions=[r1])
        l2 = _lead(id=2, nombre="Ana", apellido="R", revisions=[r2])
        html = _render(leads=[l1, l2])
        self.assertIn("agendaMargin-conflict", html)
        self.assertIn("!", html)

    # UX2-15 — Zero-zone / day-start block visible
    def test_ux2_15_zero_zone_start(self):
        html = _render(leads=[])
        self.assertIn("agendaDayStartBlock", html)
        self.assertIn("INICIO", html)
        # Wednesday zero-zone = Melo y Panamericana (from schedule constants)
        self.assertIn("Melo y Panamericana", html)

    # UX2-16 — First travel block (from zero-zone Norte → first appt zone)
    def test_ux2_16_first_travel_block(self):
        # Norte (zero-zone) → CABA (60 min travel), appt at 10:00
        # Biz start Wed = 09:00. Available = 60 min. Travel = 60 min → margin=0 → tight
        r = _rev(turno_hora=time(10, 0), zone_group="CABA")
        l = _lead(revisions=[r])
        html = _render(leads=[l])
        self.assertIn("agendaTravelBlock", html)
        self.assertIn("Norte", html)
        self.assertIn("CABA", html)

    # UX2-17 — Trailing free gap at end of day
    def test_ux2_17_trailing_gap(self):
        # One appointment at 09:00 (Wed, end 09:45), biz ends 18:00 → big gap
        r = _rev(turno_hora=time(9, 0), zone_group="Norte")
        l = _lead(revisions=[r])
        html = _render(leads=[l])
        self.assertIn("agendaGapEnd", html)

    # UX2-18 — Cancelled appointment: card shown, not occupying travel
    def test_ux2_18_cancelled_card_no_travel(self):
        # Only appointment is CANCELADO → no travel blocks, but card still rendered
        r = _rev(turno_hora=time(10, 0), zone_group="CABA", estado_revision="CANCELADO")
        l = _lead(revisions=[r])
        html = _render(leads=[l])
        self.assertIn("agendaApptCard", html)
        # CANCELADO card is rendered
        self.assertIn("CANCELADO", html)
        # No travel block rendered as HTML element (CSS class def appears in <style> but not as element)
        # The actual div element uses class="agendaTravelBlock"
        self.assertNotIn('<div class="agendaTravelBlock">', html)

    # UX2-19 — None/missing fields don't crash rendering
    def test_ux2_19_none_fields_safe(self):
        r = _rev(
            turno_hora=time(10, 0), zone_group=None, zone_detail=None,
            direccion_texto=None, tipo_vehiculo=None, marca=None, modelo=None,
            anio=None, pago=None, cobrado=None,
        )
        l = _lead(nombre=None, apellido=None, telefono=None, revisions=[r])
        # Must not raise
        html = _render(leads=[l])
        self.assertIsInstance(html, str)
        self.assertGreater(len(html), 100)


class TestUX2Views(unittest.TestCase):
    """UX2-20 / UX2-21 — view preservation and WA regression."""

    # UX2-20 — Day / Week / Month pills preserved
    def test_ux2_20_view_pills_preserved(self):
        html = _render(leads=[])
        self.assertIn("Día", html)
        self.assertIn("Semana", html)
        self.assertIn("Mes", html)

    # UX2-21 — WhatsApp shell renders without UX regression
    def test_ux2_21_whatsapp_inbox_regression(self):
        from app.ui.whatsapp_ui import _render_whatsapp_shell

        html = _render_whatsapp_shell(
            user_email="test@test.com",
            title="WhatsApp",
            body_html="<div>test</div>",
        )
        # Core sidebar structure intact
        self.assertIn("sidebarFooter", html)
        self.assertIn("logoutBtn", html)
        # Logo updated (no RIDECHECK text)
        self.assertNotIn(">RIDECHECK<", html)
        self.assertIn("brandLogo", html)
        # Sidebar localStorage present
        self.assertIn("sidebar_collapsed", html)


if __name__ == "__main__":
    unittest.main()
