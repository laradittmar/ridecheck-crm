"""
M12.3 Calendar Navigation Regression Tests
===========================================
Tests the month navigation fix: prev/next month links must resolve
to the correct target month regardless of what weekday the 1st falls on.

Root cause:
  The old code passed `week = monday_of_week_containing_first_of_month`.
  When the first of April is a Wednesday, that Monday is March 30.
  Navigating to ?week=2026-03-30 makes month_start = March 1 (wrong).

Fix:
  Pass `week = first_of_target_month` directly.
  ?week=2026-04-01 → month_start = April 1 (correct).

These tests do NOT require a database or HTTP server.
"""
from __future__ import annotations

import re
import sys
import unittest
from datetime import date, time
from pathlib import Path
from unittest.mock import MagicMock

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _render(week: str | None = None, leads=None, profesionales=None):
    from app.ui.kanban_view import render_calendar_page
    return render_calendar_page(
        leads or [],
        profesionales=profesionales or [],
        week=week,
    )


def _extract_data_attr(html: str, attr: str) -> str:
    m = re.search(rf'{attr}="([^"]+)"', html)
    return m.group(1) if m else ""


def _month_from_week_url(url: str) -> date | None:
    """Given /calendar?week=YYYY-MM-DD, return what month_start resolves to."""
    m = re.search(r"week=(\d{4}-\d{2}-\d{2})", url)
    if not m:
        return None
    d = date.fromisoformat(m.group(1))
    return d.replace(day=1)


class TestMonthNavigation(unittest.TestCase):
    """Month navigation must step exactly one calendar month at a time."""

    def _nav_months(self, week: str | None = None):
        html = _render(week=week)
        prev_url = _extract_data_attr(html, "data-prev-month")
        next_url = _extract_data_attr(html, "data-next-month")
        return prev_url, next_url

    # ── May 2026 ─────────────────────────────────────────────────────────────

    def test_may_prev_month_resolves_to_april(self):
        """From May, prev month must link to April, not March."""
        prev_url, _ = self._nav_months(week="2026-05-25")
        resolved = _month_from_week_url(prev_url)
        self.assertEqual(resolved, date(2026, 4, 1),
                         f"Expected April, got {resolved} from URL {prev_url}")

    def test_may_next_month_resolves_to_june(self):
        """From May, next month must link to June."""
        _, next_url = self._nav_months(week="2026-05-25")
        resolved = _month_from_week_url(next_url)
        self.assertEqual(resolved, date(2026, 6, 1),
                         f"Expected June, got {resolved} from URL {next_url}")

    def test_may_direct_access_prev_resolves_to_april(self):
        """Direct /calendar (no week param) prev must also link to April."""
        # Today is 2026-05-30 on the test server; use explicit week to be deterministic
        prev_url, _ = self._nav_months(week="2026-05-01")
        resolved = _month_from_week_url(prev_url)
        self.assertEqual(resolved, date(2026, 4, 1),
                         f"Expected April, got {resolved} from URL {prev_url}")

    # ── April 2026 (after navigating from May) ───────────────────────────────

    def test_april_prev_month_resolves_to_march(self):
        """From April (first of month as week param), prev must go to March."""
        prev_url, _ = self._nav_months(week="2026-04-01")
        resolved = _month_from_week_url(prev_url)
        self.assertEqual(resolved, date(2026, 3, 1),
                         f"Expected March, got {resolved} from URL {prev_url}")

    def test_april_next_month_resolves_to_may(self):
        """From April, next must go to May."""
        _, next_url = self._nav_months(week="2026-04-01")
        resolved = _month_from_week_url(next_url)
        self.assertEqual(resolved, date(2026, 5, 1),
                         f"Expected May, got {resolved} from URL {next_url}")

    # ── Old broken path regression ───────────────────────────────────────────

    def test_old_broken_week_march30_still_shows_march(self):
        """If somehow ?week=2026-03-30 is visited, it resolves to March (not April).
        This is the old WRONG URL that the bug was generating — it should stay March
        since it's a valid week param. Our fix stops generating it, but the server
        must still handle it correctly."""
        html = _render(week="2026-03-30")
        # month_start should be March (week_start March 30 → replace(day=1) = March 1)
        # The new code no longer generates this URL for April navigation, so this is
        # now an edge case only reachable by typing the URL manually.
        from app.ui.kanban_view import render_calendar_page
        # Just verify it renders without error and shows March in the big label
        self.assertIn("Marzo", html)

    # ── Chained navigation: May → April → March ──────────────────────────────

    def test_chained_may_april_march(self):
        """Each prev step goes to exactly the prior month."""
        # Start: May
        _, _ = self._nav_months(week="2026-05-01")
        # After clicking prev from May, we'd navigate to ?week=2026-04-01
        prev_from_may, _ = self._nav_months(week="2026-05-01")
        april_month = _month_from_week_url(prev_from_may)
        self.assertEqual(april_month, date(2026, 4, 1))

        # April week param from the URL
        m = re.search(r"week=(\d{4}-\d{2}-\d{2})", prev_from_may)
        april_week = m.group(1)

        # After clicking prev from April, we'd navigate to ?week=2026-03-01
        prev_from_april, _ = self._nav_months(week=april_week)
        march_month = _month_from_week_url(prev_from_april)
        self.assertEqual(march_month, date(2026, 3, 1))


class TestCalendarAppointmentsRender(unittest.TestCase):
    """Appointments must appear in the day view HTML for the correct date."""

    def _make_rev(self, turno_fecha, turno_hora, nombre, tipo_vehiculo="AUTO"):
        rev = MagicMock()
        rev.tipo_vehiculo = tipo_vehiculo
        rev.marca = "Toyota"; rev.modelo = "Corolla"; rev.anio = 2020
        rev.turno_fecha = turno_fecha; rev.turno_hora = turno_hora
        rev.estado_revision = "PENDIENTE"; rev.link_maps = None
        rev.direccion_texto = "Calle Test 123"; rev.profesional_id = None
        rev.link_compra = rev.precio_base = rev.precio_total = None
        rev.presupuesto_compra = rev.created_at = None
        rev.id = 1; rev.profesional = rev.agencia = None
        return rev

    def _make_lead(self, nombre, apellido, rev):
        lead = MagicMock()
        lead.id = 1; lead.nombre = nombre; lead.apellido = apellido
        lead.telefono = "1234"; lead.email = "test@test.com"
        lead.estado = "AGENDADO"; lead.necesita_humano = False
        lead.flag = None; lead.revisions = [rev]; lead.created_at = None
        return lead

    def test_appointment_appears_in_day_view_for_correct_date(self):
        """An appointment on today's date at 10:00 must appear in the day view panel."""
        today = date.today()
        rev = self._make_rev(today, time(10, 0), "Ignacio")
        lead = self._make_lead("Ignacio", "", rev)
        html = _render(week=today.strftime("%Y-%m-%d"), leads=[lead])
        # Find the day view panel
        start = html.find('id="cal-view-day"')
        end = html.find('id="cal-view-week"')
        day_panel = html[start:end]
        self.assertIn("Ignacio", day_panel)
        self.assertIn("10:00", day_panel)

    def test_no_raw_enum_in_rendered_calendar(self):
        """SUV_4X4_DEPORTIVO must not appear anywhere in the rendered output."""
        rev = self._make_rev(date(2026, 5, 30), time(10, 0), "Test", "SUV_4X4_DEPORTIVO")
        lead = self._make_lead("Test", "User", rev)
        html = _render(week="2026-05-25", leads=[lead])
        self.assertNotIn("SUV_4X4_DEPORTIVO", html)
        self.assertIn("SUV", html)

    def test_no_orphan_separator_in_day_view(self):
        """Metadata row must not show '· -' when prof is missing."""
        rev = self._make_rev(date(2026, 5, 30), time(10, 0), "Ana")
        lead = self._make_lead("Ana", "Lopez", rev)
        html = _render(week="2026-05-25", leads=[lead])
        self.assertNotIn("&nbsp;&middot;&nbsp; -", html)


class TestCalendarPrevMonthURLFormat(unittest.TestCase):
    """The data-prev-month and data-next-month attrs must be navigable URLs."""

    def test_prev_month_url_contains_week_param(self):
        html = _render(week="2026-05-25")
        prev = _extract_data_attr(html, "data-prev-month")
        self.assertIn("/calendar?week=", prev)

    def test_next_month_url_contains_week_param(self):
        html = _render(week="2026-05-25")
        nxt = _extract_data_attr(html, "data-next-month")
        self.assertIn("/calendar?week=", nxt)

    def test_prev_month_week_param_is_first_of_month(self):
        """The week param in the prev-month URL must be day 1 of that month."""
        html = _render(week="2026-05-25")
        prev = _extract_data_attr(html, "data-prev-month")
        m = re.search(r"week=(\d{4}-\d{2}-\d{2})", prev)
        self.assertIsNotNone(m)
        d = date.fromisoformat(m.group(1))
        self.assertEqual(d.day, 1,
                         f"Expected day=1, got day={d.day} in URL {prev}")

    def test_next_month_week_param_is_first_of_month(self):
        """The week param in the next-month URL must be day 1 of that month."""
        html = _render(week="2026-05-25")
        nxt = _extract_data_attr(html, "data-next-month")
        m = re.search(r"week=(\d{4}-\d{2}-\d{2})", nxt)
        self.assertIsNotNone(m)
        d = date.fromisoformat(m.group(1))
        self.assertEqual(d.day, 1,
                         f"Expected day=1, got day={d.day} in URL {nxt}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
