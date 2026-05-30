"""
M12 Calendar UI Polish — Regression Tests
==========================================
Tests verify:
  A. Human-readable vehicle type labels (no raw enums in rendered HTML)
  B. Metadata row cleanliness (no orphan · separators for missing values)
  C. Desktop typography CSS (calDateBig reduced from 2.8rem)
  D. label mapping function correctness

These tests do NOT need a database or HTTP server.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, time
from pathlib import Path
from unittest.mock import MagicMock

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _make_rev(
    tipo_vehiculo="SUV_4X4_DEPORTIVO",
    marca="Toyota",
    modelo="Hilux",
    anio=2020,
    turno_fecha=date(2026, 6, 2),
    turno_hora=time(10, 0),
    direccion_texto="Gurruchaga 2098",
    profesional_id=None,
):
    rev = MagicMock()
    rev.tipo_vehiculo = tipo_vehiculo
    rev.marca = marca
    rev.modelo = modelo
    rev.anio = anio
    rev.turno_fecha = turno_fecha
    rev.turno_hora = turno_hora
    rev.estado_revision = "PENDIENTE"
    rev.link_maps = None
    rev.direccion_texto = direccion_texto
    rev.profesional_id = profesional_id
    rev.link_compra = None
    rev.precio_base = None
    rev.precio_total = None
    rev.presupuesto_compra = None
    rev.created_at = None
    rev.id = 1
    rev.profesional = None
    rev.agencia = None
    return rev


def _make_lead(rev, nombre="Juan", apellido="Perez"):
    lead = MagicMock()
    lead.id = 1
    lead.nombre = nombre
    lead.apellido = apellido
    lead.telefono = "1234"
    lead.email = "j@j.com"
    lead.estado = "AGENDADO"
    lead.necesita_humano = False
    lead.flag = None
    lead.revisions = [rev]
    lead.created_at = None
    return lead


def _render(leads, week="2026-06-02"):
    from app.ui.kanban_view import render_calendar_page
    return render_calendar_page(leads, profesionales=[], week=week)


class TestFriendlyTipoVehiculo(unittest.TestCase):
    """A. Label mapping function."""

    def setUp(self):
        from app.ui.kanban_view import _friendly_tipo_vehiculo
        self.fn = _friendly_tipo_vehiculo

    def test_suv_label(self):
        self.assertEqual(self.fn("SUV_4X4_DEPORTIVO"), "SUV")

    def test_auto_label(self):
        self.assertEqual(self.fn("AUTO"), "Auto")

    def test_clasico_label(self):
        self.assertEqual(self.fn("CLASICO"), "Clásico")

    def test_escaneo_label(self):
        self.assertEqual(self.fn("ESCANEO_MOTOR"), "Escaneo")

    def test_moto_label(self):
        self.assertEqual(self.fn("MOTO"), "Moto")

    def test_unknown_falls_back_to_title(self):
        self.assertEqual(self.fn("SOME_ENUM"), "Some Enum")

    def test_none_returns_empty(self):
        self.assertEqual(self.fn(None), "")

    def test_empty_string_returns_empty(self):
        self.assertEqual(self.fn(""), "")


class TestNoRawEnumInCalendar(unittest.TestCase):
    """A. No raw internal enum names leak into rendered calendar HTML."""

    def _html(self, tipo):
        rev = _make_rev(tipo_vehiculo=tipo)
        return _render([_make_lead(rev)])

    def test_suv_label_not_raw(self):
        html = self._html("SUV_4X4_DEPORTIVO")
        self.assertNotIn("SUV_4X4_DEPORTIVO", html)
        self.assertIn("SUV", html)

    def test_auto_label_not_raw(self):
        html = self._html("AUTO")
        # "AUTO" alone could appear in CSS class names — check the vehicle line
        # The friendly label "Auto" must appear
        self.assertIn("Auto", html)
        # Raw uppercase AUTO should not appear inside the vehicle meta text
        # (it's acceptable in CSS class names like .calApptStatus)
        # Check that it does not appear as a standalone word in a data context
        # by checking no "AUTO" between > and < in a meta div
        import re
        raw_in_meta = re.search(r'calApptMeta[^>]*>[^<]*AUTO', html)
        self.assertIsNone(raw_in_meta, "Raw AUTO found inside a meta element")

    def test_clasico_label_not_raw(self):
        html = self._html("CLASICO")
        self.assertNotIn("CLASICO", html)
        self.assertIn("Clásico", html)

    def test_moto_label_not_raw(self):
        html = self._html("MOTO")
        # MOTO could appear in class names; verify friendly label present
        self.assertIn("Moto", html)


class TestMetadataSeparatorClean(unittest.TestCase):
    """B. Metadata rows never show orphan · separators."""

    def test_no_dash_after_separator_when_prof_missing(self):
        """addr · - must not appear when prof is missing."""
        rev = _make_rev(profesional_id=None)
        html = _render([_make_lead(rev)])
        self.assertNotIn("Gurruchaga 2098 &nbsp;&middot;&nbsp; -", html)

    def test_address_still_renders_without_prof(self):
        """Address should still be visible even without a profesional."""
        rev = _make_rev(profesional_id=None)
        html = _render([_make_lead(rev)])
        self.assertIn("Gurruchaga 2098", html)

    def test_no_double_dash_separator_when_both_missing(self):
        """No separator at all when both address and prof are absent."""
        rev = _make_rev(direccion_texto=None, profesional_id=None)
        html = _render([_make_lead(rev)])
        self.assertNotIn("- &nbsp;&middot;&nbsp; -", html)
        self.assertNotIn("&nbsp;&middot;&nbsp; -", html)
        self.assertNotIn("- &nbsp;&middot;&nbsp;", html)

    def test_separator_renders_when_both_present(self):
        """When address AND prof are present, separator must appear."""
        from unittest.mock import MagicMock as MM
        prof = MM()
        prof.id = 42
        prof.nombre = "Ana"
        prof.apellido = "García"
        rev = _make_rev(profesional_id=42)
        rev.profesional = prof
        lead = _make_lead(rev)
        from app.ui.kanban_view import render_calendar_page, _profesional_label
        html = render_calendar_page([lead], profesionales=[prof], week="2026-06-02")
        self.assertIn("&nbsp;&middot;&nbsp;", html)


class TestCalendarTypographyCSS(unittest.TestCase):
    """C. Desktop typography: old oversized date font removed."""

    def _css_html(self):
        return _render([])

    def test_old_oversize_date_font_removed(self):
        html = self._css_html()
        self.assertNotIn("2.8rem", html)

    def test_new_date_font_present(self):
        html = self._css_html()
        # M12 set 1.65rem; M12.1 further refined to 1.25rem — either is acceptable
        self.assertTrue(
            "1.65rem" in html or "1.25rem" in html,
            "calDateBig must use a compact font size (1.65rem or 1.25rem)",
        )

    def test_desktop_breakpoint_present(self):
        """Desktop date header must be resized via @media breakpoint."""
        html = self._css_html()
        # M12: 1.45rem, M12.1: 1.1rem, M12.2: 1.75rem — any explicit override is valid
        self.assertTrue(
            any(v in html for v in ("1.45rem", "1.1rem", "1.75rem")),
            "Desktop date breakpoint font must be present",
        )
        self.assertIn("min-width: 769px", html)

    def test_calDateBig_line_height_improved(self):
        html = self._css_html()
        # M12 set 1.15; M12.1 set 1.2 — any improved value is acceptable
        self.assertTrue(
            "line-height: 1.15" in html or "line-height: 1.2" in html,
            "calDateBig line-height must be set to a compact value",
        )

    def test_week_appt_meta_font_size(self):
        html = self._css_html()
        # M12 used 11.5px; M12.1 refined to 11px or 10.5px — any compact value
        self.assertTrue(
            "11.5px" in html or "11px" in html or "10.5px" in html,
            "Meta font size must be compact (11px or similar)",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
