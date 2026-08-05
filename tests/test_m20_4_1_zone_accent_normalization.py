"""M20.4.1 — Regression tests for _extract_zone_from_text accent normalization.

Focused tests prove the fix to the M20.3 blocking beta defect:
  _extract_zone_from_text did not strip Unicode diacritics, so "Benavídez"
  (user input, with accent) failed to match "Benavidez" (stored DB value).

All tests are fully offline: no DB, no Meta API, no OpenAI, no containers.
"""
from __future__ import annotations

import sys
import types
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Stub third-party packages so the import chain works offline.
for _mod in [
    "resend", "anthropic", "openai",
    "boto3", "botocore", "botocore.exceptions",
    "psycopg2", "psycopg2.extensions",
]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

from app.services.pricing import PricingNotFoundError, PricingQuote, PricingService
from app.services.vehicle_catalog import lookup_vehicle


# ── Minimal fakes ──────────────────────────────────────────────────────────────

@dataclass
class FakePriceRow:
    tipo_vehiculo: str
    precio_base: int


@dataclass
class FakeZoneRow:
    zone_group: str
    zone_detail: str | None
    viaticos: int


class FakeRepoBenavidez:
    """Pricing repo stub: SUV/4x4 + Norte/Benavidez → $140,000, viáticos=0."""

    def find_base_price(self, tipo_vehiculo: str):
        if tipo_vehiculo == "SUV/4x4":
            return FakePriceRow("SUV/4x4", 140_000)
        return None

    def find_zone_by_group_and_detail(self, db, zone_group, zone_detail):
        key = (zone_detail or "").strip().lower()
        if key == "benavidez":
            return FakeZoneRow("Norte", "Benavidez", 0)
        return None


# ── CE scaffolding (follows the pattern from test_m18_business_logic.py) ──────

def _make_engine(repo=None):
    from app.services.conversation_engine import ConversationEngine

    repo = repo or FakeRepoBenavidez()
    pricing = PricingService(repository=repo)

    db = MagicMock()
    settings = MagicMock()
    settings.openai_api_key = "sk-fake"
    settings.openai_model = "gpt-4o-mini"
    settings.backend_url = "http://localhost:8000"

    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = db
    eng.settings = settings
    eng._pricing = pricing
    return eng


def _make_state(**kwargs):
    ns = types.SimpleNamespace(
        home_zone_group=None,
        home_zone_detail=None,
        home_address=None,
        distance_km=None,
        current_focus_candidate_id=None,
        preferred_day=None,
        preferred_time=None,
        active_requested_date=None,
        last_requested_time=None,
        last_offered_slots=None,
        last_visible_slots=None,
        is_website_lead=False,
        last_stage="QUALIFYING",
        needs_human=False,
        flow_booking_token=None,
        current_revision_id=None,
        customer_name=None,
        vehicle_clarification_sent=False,
        location_clarification_sent=False,
        vehicle_fallback_flow_sent=False,
        location_fallback_flow_sent=False,
        inspectability_clarification_sent=False,
    )
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _make_candidate(**kwargs):
    return types.SimpleNamespace(
        id=kwargs.get("id", 1),
        thread_id=kwargs.get("thread_id", 37),
        marca=kwargs.get("marca"),
        modelo=kwargs.get("modelo"),
        tipo_vehiculo=kwargs.get("tipo_vehiculo"),
        zone_group=kwargs.get("zone_group"),
        zone_detail=kwargs.get("zone_detail"),
        status=kwargs.get("status", "current_focus"),
        anio=kwargs.get("anio"),
        label=None,
    )


def _make_ctx(thread_id=37, candidates=None, state=None):
    from app.services.conversation_engine import _Context

    thread = types.SimpleNamespace(id=thread_id, lead_id=10, contact_id=5)
    lead = types.SimpleNamespace(
        id=10, nombre="Lara", apellido=None, email=None,
        telefono="1153368330", flag="PRESUPUESTANDO",
        estado="CONSULTA_NUEVA", canal=None, necesita_humano=False,
    )
    contact = types.SimpleNamespace(wa_id="5491153368330")

    ctx = _Context.__new__(_Context)
    ctx.thread = thread
    ctx.lead = lead
    ctx.contact = contact
    ctx.candidates = list(candidates or [])
    ctx.state = state or _make_state()
    ctx.db_messages = []
    return ctx


def _z(group: str, detail: str | None, viaticos: int) -> SimpleNamespace:
    return SimpleNamespace(zone_group=group, zone_detail=detail, viaticos=viaticos)


def _engine_with_zones(zones: list):
    """CE instance whose DB.execute returns the given zones list for zone detection."""
    eng = _make_engine()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = zones
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_mock
    eng.db.execute.return_value = execute_result
    return eng


# ── Tests: _extract_zone_from_text accent normalization ───────────────────────

class TestExtractZoneAccentNormalization(unittest.TestCase):
    """Focused offline regression tests for _extract_zone_from_text."""

    # Minimal representative zone set used across all cases.
    _ZONES = [
        _z("Norte", "Benavidez", 0),
        _z("Norte", "Tigre",     0),
        _z("Norte",  None,       0),   # group-default row
        _z("Sur",  "Dock Sud", 30_000),
    ]

    def _eng(self):
        return _engine_with_zones(self._ZONES)

    def _assert_benavidez(self, result, *, text: str):
        self.assertIsNotNone(result, f"Zone not detected from {text!r} — accent normalization bug")
        self.assertEqual(result.zone_group,  "Norte",     f"zone_group mismatch for {text!r}")
        self.assertEqual(result.zone_detail, "Benavidez", f"zone_detail mismatch for {text!r}")

    # Required regression cases ────────────────────────────────────────────────

    def test_benavidez_with_accent(self):
        self._assert_benavidez(
            self._eng()._extract_zone_from_text("Benavídez"),
            text="Benavídez",
        )

    def test_benavidez_without_accent(self):
        self._assert_benavidez(
            self._eng()._extract_zone_from_text("Benavidez"),
            text="Benavidez",
        )

    def test_benavidez_uppercase_with_accent(self):
        self._assert_benavidez(
            self._eng()._extract_zone_from_text("BENAVÍDEZ"),
            text="BENAVÍDEZ",
        )

    def test_benavidez_leading_trailing_whitespace(self):
        self._assert_benavidez(
            self._eng()._extract_zone_from_text("  Benavídez  "),
            text="  Benavídez  ",
        )

    def test_full_message_with_accent(self):
        self._assert_benavidez(
            self._eng()._extract_zone_from_text(
                "Hola, quiero revisar un 3008 en Benavídez"
            ),
            text="Hola, quiero revisar un 3008 en Benavídez",
        )

    def test_unknown_locality_returns_none(self):
        """A locality absent from the zone set must return None."""
        result = self._eng()._extract_zone_from_text("mi auto está en Capilla del Monte")
        self.assertIsNone(result, "Non-existent zone must return None")

    def test_norte_group_detection_unchanged(self):
        """Bare group name 'Norte' still resolves via the group-detection fallback."""
        result = self._eng()._extract_zone_from_text("mi auto está en la zona Norte")
        self.assertIsNotNone(result)
        self.assertEqual(result.zone_group, "Norte")
        self.assertIsNone(result.zone_detail, "Group-only match must leave zone_detail=None")

    # Canonical stored value is not mutated ───────────────────────────────────

    def test_stored_zone_detail_canonical_value_preserved(self):
        """zone_detail returned must be the original DB value, not the accent-stripped form."""
        result = self._eng()._extract_zone_from_text("Benavídez")
        self.assertIsNotNone(result)
        self.assertEqual(result.zone_detail, "Benavidez",
                         "Returned zone_detail must be the canonical stored value")


# ── End-to-end pipeline: vehicle → zone (accent) → pricing ───────────────────

class TestE2EBeta3008Benavidez(unittest.TestCase):
    """
    End-to-end pipeline test using the M20.3 beta reference case:
      Input: "Hola, quiero revisar un 3008 en Benavídez"

    Exercises the full path:
      vehicle catalog  →  zone detection (accent-normalized)  →  pricing

    urlopen is wired to a fail-sentinel: any Meta transport attempt
    will raise AssertionError rather than silently succeeding.
    """

    @patch("urllib.request.urlopen")
    def test_vehicle_zone_pricing_pipeline_accent(self, mock_urlopen):
        mock_urlopen.side_effect = AssertionError(
            "Meta transport reached — expected 0 transport calls"
        )

        text = "Hola, quiero revisar un 3008 en Benavídez"

        # A. Vehicle recognition ───────────────────────────────────────────
        vehicle = lookup_vehicle(text)
        self.assertIsNotNone(vehicle, "Peugeot 3008 not recognized by catalog")
        self.assertEqual(vehicle.tipo_vehiculo, "SUV/4x4")
        self.assertEqual(vehicle.marca, "Peugeot")
        self.assertEqual(vehicle.modelo, "3008")

        # B. Zone detection with accent ────────────────────────────────────
        eng = _engine_with_zones([
            _z("Norte", "Benavidez", 0),
            _z("Norte",  None,       0),
        ])
        zone = eng._extract_zone_from_text(text)
        self.assertIsNotNone(zone,
            "Zone detection returned None for 'Benavídez' — accent normalization bug")
        self.assertEqual(zone.zone_group,  "Norte")
        self.assertEqual(zone.zone_detail, "Benavidez")

        # Canonical stored value must not be altered by normalization.
        self.assertEqual(zone.zone_detail, "Benavidez",
            "Canonical zone_detail must be the stored value, not the normalized form")

        # C. Pricing: $140,000, viáticos=$0, no PricingNotFoundError ──────
        candidate = _make_candidate(tipo_vehiculo="SUV/4x4")
        state = _make_state(
            home_zone_group=zone.zone_group,
            home_zone_detail=zone.zone_detail,
        )
        ctx = _make_ctx(candidates=[candidate], state=state)

        quote = eng._compute_price_quote(ctx, state)
        self.assertIsNotNone(quote,
            "PricingNotFoundError — Norte/Benavidez + SUV/4x4 must resolve to a price")
        self.assertEqual(quote.precio_total, 140_000)
        self.assertEqual(quote.precio_base,  140_000)
        self.assertEqual(quote.viaticos,     0)

        # D. No Location Fallback Flow ─────────────────────────────────────
        # Zone detected → CE would not enter the location-clarification branch.
        self.assertIsNotNone(zone.zone_detail,
            "zone_detail must be set; location fallback must not trigger")

        # E. needs_human remains False ────────────────────────────────────
        self.assertFalse(state.needs_human)

        # F. Zero Meta transport calls ─────────────────────────────────────
        mock_urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
