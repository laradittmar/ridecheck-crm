"""M21.3-UX2-RUNTIME — Runtime parity tests

RUNTIME-01  /calendar returned HTML contains operational-day marker
RUNTIME-02  /calendar Day HTML does NOT contain old hour-grid as primary day renderer
RUNTIME-03  shared shell contains brandLogo
RUNTIME-04  shared shell contains logout/account footer
RUNTIME-05  collapse JS/localStorage present
RUNTIME-06  /static/branding/ridecheck-logo.jpg returns 200
RUNTIME-07  /static/bg.png returns current owner asset (>1MB)
RUNTIME-08  map link rendered for appointment with address
RUNTIME-09  Waze option rendered
RUNTIME-10  travel block rendered for suitable appointments
"""
from __future__ import annotations

import sys
import types
import urllib.request
import unittest
from datetime import date, time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import sqlalchemy as _sa
import sqlalchemy.dialects.postgresql as _pgd
import sqlalchemy.dialects.postgresql.json as _pgj
_pgd.JSONB = _pgj.JSONB = _sa.JSON

for _m in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    sys.modules.setdefault(_m, types.ModuleType(_m))

_pg = types.ModuleType("psycopg2")
_pg.extensions = types.ModuleType("psycopg2.extensions")
sys.modules["psycopg2"] = _pg
sys.modules["psycopg2.extensions"] = _pg.extensions

from app.ui.kanban_view import render_calendar_page, _base_css
from app.services.schedule import ScheduleService

CONTAINER_URL = "http://172.18.0.2:8000"


def _rev(id=1, hora=time(10, 0), zg="Norte", zd="Santa Catalina", addr="Maipú 1234"):
    return types.SimpleNamespace(
        id=id, turno_fecha=date(2026, 8, 26), turno_hora=hora,
        zone_group=zg, zone_detail=zd, direccion_texto=addr,
        estado_revision="PENDIENTE", pago=None, cobrado=None,
        tipo_vehiculo="SEDAN", marca="Toyota", modelo="Corolla",
        anio=2020, profesional_id=None, link_maps=None,
    )


def _lead(id=1, n="Juan", a="Pérez", revs=None):
    return types.SimpleNamespace(id=id, nombre=n, apellido=a,
                                  telefono="+549", revisions=revs or [])


def _rendered_html():
    svc = ScheduleService.__new__(ScheduleService)
    r1 = _rev(1, time(10, 0), "Norte", "Santa Catalina", "Maipú 1234")
    r2 = _rev(2, time(13, 0), "CABA", "Microcentro", "Florida 800")
    l1 = _lead(1, "Juan", "Pérez", [r1])
    l2 = _lead(2, "Ana", "García", [r2])
    return render_calendar_page(
        [l1, l2], initial_date="2026-08-26", week="2026-08-24",
        schedule_svc=svc, thread_by_lead={1: 42},
    )


class TestRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _rendered_html()
        cls.css = _base_css()

    def _http_get(self, path):
        try:
            with urllib.request.urlopen(f"{CONTAINER_URL}{path}", timeout=5) as r:
                return r.status, r.read()
        except Exception as e:
            return 0, b""

    def test_runtime_01_calendar_has_operational_day_marker(self):
        self.assertIn("agendaDayWrap", self.html)

    def test_runtime_02_no_old_hour_grid_element(self):
        self.assertNotIn('<div class="calDaySlots">', self.html)

    def test_runtime_03_shared_shell_has_brandlogo(self):
        from app.ui.whatsapp_ui import _render_whatsapp_shell
        wa_html = _render_whatsapp_shell("test@test.com", "WA", "<div>body</div>")
        self.assertIn("brandLogo", wa_html)
        self.assertIn("ridecheck-logo.jpg", wa_html)

    def test_runtime_04_shared_shell_has_logout_footer(self):
        self.assertIn("logoutBtnCompact", self.html)
        self.assertIn('action="/logout"', self.html)

    def test_runtime_05_collapse_js_localstorage_present(self):
        self.assertIn("localStorage.setItem", self.html)
        self.assertIn("sidebar_collapsed", self.html)
        self.assertIn("DOMContentLoaded", self.html)

    def test_runtime_06_logo_asset_returns_200(self):
        status, body = self._http_get("/static/branding/ridecheck-logo.jpg")
        self.assertEqual(status, 200, f"Logo returned HTTP {status}")
        self.assertGreater(len(body), 5000, "Logo body too small")

    def test_runtime_07_bg_png_is_large_owner_asset(self):
        status, body = self._http_get("/static/bg.png")
        self.assertEqual(status, 200)
        self.assertGreater(len(body), 1_000_000, "bg.png should be >1MB (owner asset)")

    def test_runtime_08_map_link_in_rendered_html(self):
        self.assertIn("google.com/maps/search", self.html)
        self.assertIn("Maipú 1234", self.html)

    def test_runtime_09_waze_link_in_rendered_html(self):
        self.assertIn("waze.com", self.html)
        self.assertIn("agendaGpsDropdown", self.html)

    def test_runtime_10_travel_block_in_rendered_html(self):
        self.assertIn('<div class="agendaTravelBlock">', self.html)
        self.assertIn("Necesario:", self.html)
        self.assertIn("agendaMargin-", self.html)


if __name__ == "__main__":
    unittest.main()
