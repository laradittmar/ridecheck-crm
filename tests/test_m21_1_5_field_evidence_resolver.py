"""M21.1.5 — Central Field-Evidence Resolver — Executable Tests.

Source of truth: docs/M21_1_5_field_evidence_resolver.md (approved 2026-08-11).

FE01  Confirmed candidate vehicle
FE02  Pending fuzzy proposal — unconfirmed, not pricing-ready
FE03  Exact current-turn vehicle wins over empty state
FE04  Candidate location beats thread zone
FE05  Current explicit location beats stale candidate zone
FE06  Customer origin separated from inspection location
FE07  Bare locality in location clarification context (state already extracted)
FE08  Inspectability pending — not pricing-ready
FE09  Inspectability resolved from current-turn text
FE10  BR-1 service intent established from state
FE11  FAQ-only fresh thread — no commercial field invented
FE12  Website/Flow evidence — candidate authority, no downgrade
FE13  Contradictory current locations — unresolved
FE14  No redundant vehicle question when candidate confirmed
FE15  No redundant location question when candidate has zone
FE16  No redundant inspectability question when resolved
FE17  Pending fuzzy is not pricing-ready
FE18  Full qualification completeness
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── Stub heavy deps before any app import ─────────────────────────────────────
for _mod_name in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

# ── Stub PostgreSQL JSONB ──────────────────────────────────────────────────────
import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = sqlalchemy.JSON   # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON      # type: ignore[attr-defined]

from app.services.field_evidence import (   # noqa: E402
    resolve_field_evidence,
    FieldEvidenceSnapshot,
    FieldEvidence,
    SRC_CURRENT_TURN_EXACT,
    SRC_CURRENT_TURN_FUZZY_HIGH,
    SRC_CANDIDATE,
    SRC_THREAD_STATE,
    SRC_NONE,
    INSP_DISASSEMBLED_BLOCKED,
    INSP_ASSEMBLED_ACCESSIBLE,
    INSP_UNRESOLVED_NON_RUNNING,
    INSP_UNKNOWN,
)
from app.services.vehicle_catalog import VehicleMatch, FuzzyLookupResult  # noqa: E402


# ── Fixture helpers ────────────────────────────────────────────────────────────

def _make_state(**kw) -> SimpleNamespace:
    ns = SimpleNamespace(
        last_stage="QUALIFYING",
        needs_human=False,
        last_intent=None,
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
        flow_booking_token=None,
        current_revision_id=None,
        customer_name=None,
        vehicle_clarification_sent=False,
        location_clarification_sent=False,
        vehicle_fallback_flow_sent=False,
        location_fallback_flow_sent=False,
        inspectability_clarification_sent=False,
        last_processed_inbound_wa_message_id=None,
        pending_fuzzy_catalog_key=None,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _make_candidate(**kw) -> SimpleNamespace:
    ns = SimpleNamespace(
        id=10, thread_id=42, status="current_focus",
        tipo_vehiculo="AUTO", marca="Toyota", modelo="Corolla",
        anio=2020, zone_group=None, zone_detail=None,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _make_ctx(state=None, candidates=None) -> SimpleNamespace:
    ctx = SimpleNamespace()
    ctx.thread = SimpleNamespace(id=42)
    ctx.contact = SimpleNamespace(wa_id="5491199999999")
    ctx.lead = SimpleNamespace(id=1, nombre="Test")
    ctx.state = state if state is not None else _make_state()
    ctx.candidates = candidates if candidates is not None else []
    return ctx


def _veh_match(marca="Ford", modelo="Kuga", tipo="SUV_4X4_DEPORTIVO") -> VehicleMatch:
    return VehicleMatch(
        marca=marca,
        modelo=modelo,
        tipo_vehiculo=tipo,
        confidence="high",
        matched_alias=f"{marca.lower()} {modelo.lower()}",
    )


def _fuzzy_hit(outcome, marca="Ford", modelo="Ka", tipo="AUTO",
               score=0.9, second_score=0.5, gap=0.4) -> FuzzyLookupResult:
    hit = VehicleMatch(marca=marca, modelo=modelo, tipo_vehiculo=tipo,
                       confidence="high", matched_alias=f"{marca.lower()} {modelo.lower()}")
    return FuzzyLookupResult(
        outcome=outcome, hit=hit, score=score,
        second_hit=None, second_score=second_score, gap=gap,
        make_constrained=False,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# FE01 — Confirmed candidate vehicle
# ═══════════════════════════════════════════════════════════════════════════════
class TestFE01ConfirmedCandidateVehicle(unittest.TestCase):

    def setUp(self):
        self.cand = _make_candidate(
            id=10, marca="Ford", modelo="Focus", anio=2019,
            tipo_vehiculo="AUTO", status="current_focus",
        )
        self.state = _make_state(current_focus_candidate_id=10)
        self.ctx = _make_ctx(state=self.state, candidates=[self.cand])

    def test_vehicle_source_is_candidate(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertEqual(snap.vehicle.source, SRC_CANDIDATE)

    def test_vehicle_confirmed(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertTrue(snap.vehicle.confirmed)

    def test_vehicle_value(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertEqual(snap.vehicle.value, ("Ford", "Focus"))

    def test_vehicle_category_is_auto(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertEqual(snap.vehicle_category.value, "AUTO")

    def test_needs_vehicle_false(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertFalse(snap.needs_vehicle())

    def test_vehicle_known_true(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertTrue(snap.vehicle_known())


# ═══════════════════════════════════════════════════════════════════════════════
# FE02 — Pending fuzzy proposal — unconfirmed
# ═══════════════════════════════════════════════════════════════════════════════
class TestFE02PendingFuzzyUnconfirmed(unittest.TestCase):

    def setUp(self):
        self.state = _make_state(pending_fuzzy_catalog_key="Ford||Ka")
        self.ctx = _make_ctx(state=self.state, candidates=[])

    def _snap(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None), \
             patch("app.services.field_evidence.fuzzy_lookup_vehicle",
                   return_value=_fuzzy_hit("UNRESOLVED")):
            return resolve_field_evidence(self.ctx, self.state)

    def test_vehicle_source_is_thread_state(self):
        snap = self._snap()
        self.assertEqual(snap.vehicle.source, SRC_THREAD_STATE)

    def test_vehicle_not_confirmed(self):
        snap = self._snap()
        self.assertFalse(snap.vehicle.confirmed)

    def test_vehicle_value_contains_brand(self):
        snap = self._snap()
        self.assertIsNotNone(snap.vehicle.value)
        self.assertEqual(snap.vehicle.value[0], "Ford")

    def test_pricing_ready_false(self):
        snap = self._snap()
        self.assertFalse(snap.pricing_ready())

    def test_needs_vehicle_true(self):
        snap = self._snap()
        self.assertTrue(snap.needs_vehicle())


# ═══════════════════════════════════════════════════════════════════════════════
# FE03 — Exact current-turn vehicle wins over empty state
# ═══════════════════════════════════════════════════════════════════════════════
class TestFE03ExactCurrentTurnVehicle(unittest.TestCase):

    def setUp(self):
        self.state = _make_state()
        self.ctx = _make_ctx(state=self.state, candidates=[])

    def test_current_turn_exact_source(self):
        with patch("app.services.field_evidence.lookup_vehicle",
                   return_value=_veh_match("Ford", "Kuga", "SUV_4X4_DEPORTIVO")):
            snap = resolve_field_evidence(self.ctx, self.state, "Ford Kuga 2020")
        self.assertEqual(snap.vehicle.source, SRC_CURRENT_TURN_EXACT)

    def test_vehicle_confirmed(self):
        with patch("app.services.field_evidence.lookup_vehicle",
                   return_value=_veh_match("Ford", "Kuga", "SUV_4X4_DEPORTIVO")):
            snap = resolve_field_evidence(self.ctx, self.state, "Ford Kuga 2020")
        self.assertTrue(snap.vehicle.confirmed)

    def test_vehicle_value(self):
        with patch("app.services.field_evidence.lookup_vehicle",
                   return_value=_veh_match("Ford", "Kuga", "SUV_4X4_DEPORTIVO")):
            snap = resolve_field_evidence(self.ctx, self.state, "Ford Kuga 2020")
        self.assertEqual(snap.vehicle.value, ("Ford", "Kuga"))

    def test_current_turn_flag(self):
        with patch("app.services.field_evidence.lookup_vehicle",
                   return_value=_veh_match("Ford", "Kuga", "SUV_4X4_DEPORTIVO")):
            snap = resolve_field_evidence(self.ctx, self.state, "Ford Kuga 2020")
        self.assertTrue(snap.vehicle.current_turn)


# ═══════════════════════════════════════════════════════════════════════════════
# FE04 — Candidate location beats thread zone
# ═══════════════════════════════════════════════════════════════════════════════
class TestFE04CandidateLocationBeatsThread(unittest.TestCase):

    def setUp(self):
        self.cand = _make_candidate(
            id=10, marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
            zone_group="CABA", zone_detail="Palermo",
        )
        self.state = _make_state(
            current_focus_candidate_id=10,
            home_zone_group="GBA_NORTE",
            home_zone_detail="Tigre",
        )
        self.ctx = _make_ctx(state=self.state, candidates=[self.cand])

    def test_location_source_is_candidate(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertEqual(snap.inspection_location.source, SRC_CANDIDATE)

    def test_location_value_is_palermo(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertIn("Palermo", str(snap.inspection_location.value))

    def test_location_confirmed(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertTrue(snap.inspection_location.confirmed)

    def test_location_known_true(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertTrue(snap.location_known())


# ═══════════════════════════════════════════════════════════════════════════════
# FE05 — Current explicit location beats stale candidate zone
# ═══════════════════════════════════════════════════════════════════════════════
class TestFE05CurrentLocationBeatsCandidate(unittest.TestCase):

    def setUp(self):
        self.cand = _make_candidate(
            id=10, marca="Toyota", modelo="Corolla", tipo_vehiculo="AUTO",
            zone_group="GBA_NORTE", zone_detail="Tigre",
        )
        self.state = _make_state(current_focus_candidate_id=10)
        self.ctx = _make_ctx(state=self.state, candidates=[self.cand])

    def test_current_turn_location_wins(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            snap = resolve_field_evidence(
                self.ctx, self.state,
                "El auto ahora está en Villa Urquiza.",
            )
        self.assertEqual(snap.inspection_location.source, SRC_CURRENT_TURN_EXACT)

    def test_location_value_contains_villa_urquiza(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            snap = resolve_field_evidence(
                self.ctx, self.state,
                "El auto ahora está en Villa Urquiza.",
            )
        self.assertIn("Villa Urquiza", str(snap.inspection_location.value))

    def test_resolver_does_not_mutate_candidate(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            resolve_field_evidence(
                self.ctx, self.state,
                "El auto ahora está en Villa Urquiza.",
            )
        self.assertEqual(self.cand.zone_group, "GBA_NORTE")
        self.assertEqual(self.cand.zone_detail, "Tigre")


# ═══════════════════════════════════════════════════════════════════════════════
# FE06 — Customer origin separated from inspection location
# ═══════════════════════════════════════════════════════════════════════════════
class TestFE06CustomerOriginSeparated(unittest.TestCase):

    def setUp(self):
        self.state = _make_state()
        self.ctx = _make_ctx(state=self.state, candidates=[])

    def test_customer_origin_detected(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            snap = resolve_field_evidence(
                self.ctx, self.state,
                "Yo vivo en Tigre, pero el auto está en Palermo.",
            )
        self.assertEqual(snap.customer_origin.source, SRC_CURRENT_TURN_EXACT)
        self.assertIsNotNone(snap.customer_origin.value)
        self.assertIn("Tigre", str(snap.customer_origin.value))

    def test_inspection_location_is_palermo(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            snap = resolve_field_evidence(
                self.ctx, self.state,
                "Yo vivo en Tigre, pero el auto está en Palermo.",
            )
        self.assertEqual(snap.inspection_location.source, SRC_CURRENT_TURN_EXACT)
        self.assertIn("Palermo", str(snap.inspection_location.value))

    def test_customer_origin_does_not_satisfy_location(self):
        # Customer origin must not bleed into inspection_location
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            snap = resolve_field_evidence(
                self.ctx, self.state,
                "Yo vivo en Tigre, pero el auto está en Palermo.",
            )
        self.assertNotIn("Tigre", str(snap.inspection_location.value))


# ═══════════════════════════════════════════════════════════════════════════════
# FE07 — Bare locality in location clarification context
# ═══════════════════════════════════════════════════════════════════════════════
class TestFE07BareLocalityClarificationContext(unittest.TestCase):
    """State models CE having already extracted and stored the zone from the reply."""

    def setUp(self):
        # CE's _extract_zone_from_text + _normalize_zone_from_db already ran;
        # result is stored in state. Resolver reads from state.
        self.state = _make_state(
            location_clarification_sent=True,
            home_zone_group="CABA",
            home_zone_detail="Palermo",
        )
        self.ctx = _make_ctx(state=self.state, candidates=[])

    def test_location_value_is_palermo(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            snap = resolve_field_evidence(self.ctx, self.state, "Palermo")
        self.assertIsNotNone(snap.inspection_location.value)
        self.assertIn("Palermo", str(snap.inspection_location.value))

    def test_location_confirmed(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            snap = resolve_field_evidence(self.ctx, self.state, "Palermo")
        self.assertTrue(snap.inspection_location.confirmed)

    def test_location_known_true(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            snap = resolve_field_evidence(self.ctx, self.state, "Palermo")
        self.assertTrue(snap.location_known())


# ═══════════════════════════════════════════════════════════════════════════════
# FE08 — Inspectability pending (clarification sent, no confirmation)
# ═══════════════════════════════════════════════════════════════════════════════
class TestFE08InspectabilityPending(unittest.TestCase):

    def setUp(self):
        self.state = _make_state(inspectability_clarification_sent=True)
        self.ctx = _make_ctx(state=self.state, candidates=[])

    def test_inspectability_is_unresolved(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertEqual(snap.inspectability.value, INSP_UNRESOLVED_NON_RUNNING)

    def test_inspectability_not_confirmed(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertFalse(snap.inspectability.confirmed)

    def test_pricing_ready_false(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertFalse(snap.pricing_ready())

    def test_inspectability_blocks_progress(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertFalse(snap.inspectability_allows_progress())


# ═══════════════════════════════════════════════════════════════════════════════
# FE09 — Inspectability resolved from current-turn text
# ═══════════════════════════════════════════════════════════════════════════════
class TestFE09InspectabilityResolvedCurrentTurn(unittest.TestCase):

    def setUp(self):
        self.state = _make_state()
        self.ctx = _make_ctx(state=self.state, candidates=[])

    def test_assembled_accessible_detected(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            snap = resolve_field_evidence(
                self.ctx, self.state,
                "Sí, está armado y accesible.",
            )
        self.assertEqual(snap.inspectability.value, INSP_ASSEMBLED_ACCESSIBLE)

    def test_inspectability_confirmed(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            snap = resolve_field_evidence(
                self.ctx, self.state,
                "Sí, está armado y accesible.",
            )
        self.assertTrue(snap.inspectability.confirmed)

    def test_current_turn_flag(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            snap = resolve_field_evidence(
                self.ctx, self.state,
                "Sí, está armado y accesible.",
            )
        self.assertTrue(snap.inspectability.current_turn)

    def test_allows_progress(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            snap = resolve_field_evidence(
                self.ctx, self.state,
                "Sí, está armado y accesible.",
            )
        self.assertTrue(snap.inspectability_allows_progress())


# ═══════════════════════════════════════════════════════════════════════════════
# FE10 — BR-1 service intent established from state
# ═══════════════════════════════════════════════════════════════════════════════
class TestFE10ServiceIntentEstablished(unittest.TestCase):

    def setUp(self):
        self.state = _make_state(last_intent="PREPURCHASE_INSPECTION")
        self.ctx = _make_ctx(state=self.state, candidates=[])

    def test_intent_value(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertEqual(snap.service_intent.value, "PREPURCHASE_INSPECTION")

    def test_intent_confirmed(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertTrue(snap.service_intent.confirmed)

    def test_intent_source_is_thread_state(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertEqual(snap.service_intent.source, SRC_THREAD_STATE)

    def test_service_intent_known_true(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertTrue(snap.service_intent_known())


# ═══════════════════════════════════════════════════════════════════════════════
# FE11 — FAQ-only fresh thread — no commercial field invented
# ═══════════════════════════════════════════════════════════════════════════════
class TestFE11FaqFreshThread(unittest.TestCase):

    def setUp(self):
        self.state = _make_state()
        self.ctx = _make_ctx(state=self.state, candidates=[])

    def test_no_service_intent(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None), \
             patch("app.services.field_evidence.fuzzy_lookup_vehicle",
                   return_value=_fuzzy_hit("UNRESOLVED")):
            snap = resolve_field_evidence(self.ctx, self.state, "¿Qué revisan?")
        self.assertEqual(snap.service_intent.source, SRC_NONE)
        self.assertFalse(snap.service_intent.confirmed)

    def test_no_vehicle(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None), \
             patch("app.services.field_evidence.fuzzy_lookup_vehicle",
                   return_value=_fuzzy_hit("UNRESOLVED")):
            snap = resolve_field_evidence(self.ctx, self.state, "¿Qué revisan?")
        self.assertEqual(snap.vehicle.source, SRC_NONE)

    def test_no_inspection_location(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None), \
             patch("app.services.field_evidence.fuzzy_lookup_vehicle",
                   return_value=_fuzzy_hit("UNRESOLVED")):
            snap = resolve_field_evidence(self.ctx, self.state, "¿Qué revisan?")
        self.assertEqual(snap.inspection_location.source, SRC_NONE)

    def test_inspectability_unknown(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None), \
             patch("app.services.field_evidence.fuzzy_lookup_vehicle",
                   return_value=_fuzzy_hit("UNRESOLVED")):
            snap = resolve_field_evidence(self.ctx, self.state, "¿Qué revisan?")
        self.assertEqual(snap.inspectability.value, INSP_UNKNOWN)

    def test_pricing_ready_false(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None), \
             patch("app.services.field_evidence.fuzzy_lookup_vehicle",
                   return_value=_fuzzy_hit("UNRESOLVED")):
            snap = resolve_field_evidence(self.ctx, self.state, "¿Qué revisan?")
        self.assertFalse(snap.pricing_ready())


# ═══════════════════════════════════════════════════════════════════════════════
# FE12 — Website/Flow evidence — candidate authority, no downgrade from stale state
# ═══════════════════════════════════════════════════════════════════════════════
class TestFE12WebsiteFlowEvidence(unittest.TestCase):
    """Candidate created via fallback Flow. Resolver reports CANDIDATE, not stale state."""

    def setUp(self):
        self.cand = _make_candidate(
            id=10, marca="Volkswagen", modelo="Gol", tipo_vehiculo="AUTO",
            zone_group="GBA_SUR", zone_detail="Quilmes",
        )
        self.state = _make_state(
            current_focus_candidate_id=10,
            vehicle_fallback_flow_sent=True,
            home_zone_group="GBA_SUR",
            home_zone_detail="Quilmes",
        )
        self.ctx = _make_ctx(state=self.state, candidates=[self.cand])

    def test_vehicle_source_is_candidate(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertEqual(snap.vehicle.source, SRC_CANDIDATE)

    def test_vehicle_confirmed(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertTrue(snap.vehicle.confirmed)

    def test_location_source_is_candidate(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertEqual(snap.inspection_location.source, SRC_CANDIDATE)

    def test_location_confirmed(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertTrue(snap.inspection_location.confirmed)

    def test_stale_state_does_not_downgrade(self):
        # Candidate is authoritative; state echo is not a separate claim
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertEqual(snap.inspection_location.source, SRC_CANDIDATE)


# ═══════════════════════════════════════════════════════════════════════════════
# FE13 — Contradictory current locations — unresolved
# ═══════════════════════════════════════════════════════════════════════════════
class TestFE13ContradictoryLocations(unittest.TestCase):

    def setUp(self):
        self.state = _make_state()
        self.ctx = _make_ctx(state=self.state, candidates=[])

    def test_location_unresolved(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            snap = resolve_field_evidence(
                self.ctx, self.state,
                "El auto está en Tigre o puede ser Villa Urquiza, no sé.",
            )
        self.assertFalse(snap.inspection_location.confirmed)

    def test_location_value_is_none(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            snap = resolve_field_evidence(
                self.ctx, self.state,
                "El auto está en Tigre o puede ser Villa Urquiza, no sé.",
            )
        self.assertIsNone(snap.inspection_location.value)

    def test_location_known_false(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            snap = resolve_field_evidence(
                self.ctx, self.state,
                "El auto está en Tigre o puede ser Villa Urquiza, no sé.",
            )
        self.assertFalse(snap.location_known())


# ═══════════════════════════════════════════════════════════════════════════════
# FE14 — No redundant vehicle question when candidate confirmed
# ═══════════════════════════════════════════════════════════════════════════════
class TestFE14NoRedundantVehicleQuestion(unittest.TestCase):

    def setUp(self):
        self.cand = _make_candidate(
            id=10, marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
        )
        self.state = _make_state(
            last_intent="PREPURCHASE_INSPECTION",
            current_focus_candidate_id=10,
        )
        self.ctx = _make_ctx(state=self.state, candidates=[self.cand])

    def test_needs_vehicle_false(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            snap = resolve_field_evidence(self.ctx, self.state, "¿Cuánto sale?")
        self.assertFalse(snap.needs_vehicle())

    def test_vehicle_known_true(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            snap = resolve_field_evidence(self.ctx, self.state, "¿Cuánto sale?")
        self.assertTrue(snap.vehicle_known())

    def test_vehicle_confirmed(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            snap = resolve_field_evidence(self.ctx, self.state, "¿Cuánto sale?")
        self.assertTrue(snap.vehicle.confirmed)


# ═══════════════════════════════════════════════════════════════════════════════
# FE15 — No redundant location question when candidate has zone
# ═══════════════════════════════════════════════════════════════════════════════
class TestFE15NoRedundantLocationQuestion(unittest.TestCase):

    def setUp(self):
        self.cand = _make_candidate(
            id=10, marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
            zone_group="CABA", zone_detail="Palermo",
        )
        self.state = _make_state(current_focus_candidate_id=10)
        self.ctx = _make_ctx(state=self.state, candidates=[self.cand])

    def test_needs_location_false(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertFalse(snap.needs_location())

    def test_location_known_true(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertTrue(snap.location_known())

    def test_location_source_is_candidate(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertEqual(snap.inspection_location.source, SRC_CANDIDATE)


# ═══════════════════════════════════════════════════════════════════════════════
# FE16 — No redundant inspectability question when resolved
# ═══════════════════════════════════════════════════════════════════════════════
class TestFE16NoRedundantInspectabilityQuestion(unittest.TestCase):

    def setUp(self):
        # Flag was reset after positive confirmation (R1-C: inspectability_clarification_sent=False)
        self.state = _make_state(inspectability_clarification_sent=False)
        self.ctx = _make_ctx(state=self.state, candidates=[])

    def test_unknown_allows_progress(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertTrue(snap.inspectability_allows_progress())

    def test_assembled_current_turn_allows_progress(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            snap = resolve_field_evidence(
                self.ctx, self.state,
                "Sí está armado y se puede revisar.",
            )
        self.assertEqual(snap.inspectability.value, INSP_ASSEMBLED_ACCESSIBLE)
        self.assertTrue(snap.inspectability_allows_progress())

    def test_assembled_confirmed(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None):
            snap = resolve_field_evidence(
                self.ctx, self.state,
                "Sí está armado y se puede revisar.",
            )
        self.assertTrue(snap.inspectability.confirmed)


# ═══════════════════════════════════════════════════════════════════════════════
# FE17 — Pending fuzzy is not pricing-ready
# ═══════════════════════════════════════════════════════════════════════════════
class TestFE17PendingFuzzyNotPricingReady(unittest.TestCase):

    def setUp(self):
        self.state = _make_state(pending_fuzzy_catalog_key="Ford||Ka")
        self.ctx = _make_ctx(state=self.state, candidates=[])

    def test_pricing_ready_false(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None), \
             patch("app.services.field_evidence.fuzzy_lookup_vehicle",
                   return_value=_fuzzy_hit("UNRESOLVED")):
            snap = resolve_field_evidence(self.ctx, self.state)
        self.assertFalse(snap.pricing_ready())

    def test_vehicle_not_confirmed(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None), \
             patch("app.services.field_evidence.fuzzy_lookup_vehicle",
                   return_value=_fuzzy_hit("UNRESOLVED")):
            snap = resolve_field_evidence(self.ctx, self.state)
        self.assertFalse(snap.vehicle.confirmed)

    def test_needs_vehicle_true(self):
        with patch("app.services.field_evidence.lookup_vehicle", return_value=None), \
             patch("app.services.field_evidence.fuzzy_lookup_vehicle",
                   return_value=_fuzzy_hit("UNRESOLVED")):
            snap = resolve_field_evidence(self.ctx, self.state)
        self.assertTrue(snap.needs_vehicle())


# ═══════════════════════════════════════════════════════════════════════════════
# FE18 — Full qualification completeness
# ═══════════════════════════════════════════════════════════════════════════════
class TestFE18QualificationCompleteness(unittest.TestCase):

    def setUp(self):
        self.cand = _make_candidate(
            id=10, marca="Ford", modelo="Focus", anio=2019,
            tipo_vehiculo="AUTO",
            zone_group="CABA", zone_detail="Palermo",
        )
        self.state = _make_state(
            last_intent="PREPURCHASE_INSPECTION",
            current_focus_candidate_id=10,
        )
        self.ctx = _make_ctx(state=self.state, candidates=[self.cand])

    def test_service_intent_known(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertTrue(snap.service_intent_known())

    def test_vehicle_known(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertTrue(snap.vehicle_known())

    def test_location_known(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertTrue(snap.location_known())

    def test_inspectability_allows_progress(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertTrue(snap.inspectability_allows_progress())

    def test_pricing_ready(self):
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertTrue(snap.pricing_ready())

    def test_no_price_amount_asserted(self):
        # Resolver must not call PricingService
        snap = resolve_field_evidence(self.ctx, self.state)
        self.assertIsInstance(snap, FieldEvidenceSnapshot)


if __name__ == "__main__":
    unittest.main()
