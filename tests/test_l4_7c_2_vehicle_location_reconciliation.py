"""L4.7C.2 — the first authority cutover: vehicle identity and inspection location.

Two claim families stop being written by whichever code path ran first, and start being
written by a named rule that records why. Everything else — pricing, acceptance, scheduling,
booking, lead lifecycle — is untouched, and these tests assert that too.

The cutover is reversible: with the flags off, the write path is the legacy assignment,
byte for byte.

VL-01 explicit vehicle + catalog confirmation → ACCEPT
VL-02 a semantic-inferred make alone cannot canonicalise
VL-03 numeric model / year pair
VL-04 ambiguous model → CLARIFY, no candidate
VL-05 vehicle correction / supersession
VL-06 single canonical vehicle write path
VL-07 inspection role + zone validation → ACCEPT
VL-08 customer origin can never become the inspection location
VL-09 two inspection locations → BOTH → CLARIFY
VL-10 pre-candidate location buffer preserved
VL-11 candidate location authoritative once a candidate exists
VL-12 single canonical location write path
VL-13 cycle scoping blocks stale evidence
VL-14 justification recorded
VL-15 the response validator consumes reconciled state
VL-16 feature flags roll back to legacy behaviour
VL-17 the Wild-B sequence passes
VL-18 no pricing / scheduling / booking authority changed
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import re
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

for _mod in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = sqlalchemy.JSON      # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON         # type: ignore[attr-defined]

from app.schemas.claims import (  # noqa: E402
    ClaimEvidence,
    ClaimType,
    EvidenceClass,
    Explicitness,
    ReconciliationOutcome,
)
from app.services.field_reconciler import (  # noqa: E402
    LOCATION_RULE_ID,
    VEHICLE_RULE_ID,
    reconcile_inspection_location,
    reconcile_vehicle_identity,
)

CE_SOURCE = (ROOT / "backend" / "app" / "services" / "conversation_engine.py").read_text()


def claim(claim_type, value, *, evidence_class=EvidenceClass.EXPLICIT_CUSTOMER,
          cycle_id=None, **kw):
    return ClaimEvidence(claim_type=claim_type, value=value, evidence_class=evidence_class,
                         explicitness=Explicitness.STATED, cycle_id=cycle_id, **kw).with_id()


def catalog(marca, modelo, tipo="SUV_4X4_DEPORTIVO"):
    return lambda text: SimpleNamespace(marca=marca, modelo=modelo, tipo_vehiculo=tipo)


def zone(group="Sur", detail="Berazategui"):
    return lambda locality: SimpleNamespace(zone_group=group, zone_detail=detail)


def engine(*, vehicle=False, location=False):
    from app.services.conversation_engine import ConversationEngine
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock()
    eng.settings = SimpleNamespace(reconciler_vehicle_authority_enabled=vehicle,
                                   reconciler_location_authority_enabled=location)
    eng._correlation_id = "corr-c2"
    return eng


def ctx_with(state=None):
    ctx = SimpleNamespace()
    ctx.thread = SimpleNamespace(id=77)
    ctx.state = state or SimpleNamespace(current_cycle_start_message_db_id=9,
                                         current_revision_id=None)
    return ctx


def candidate(**kw):
    base = dict(marca=None, modelo=None, anio=None, tipo_vehiculo=None,
                zone_group=None, zone_detail=None)
    base.update(kw)
    return SimpleNamespace(**base)


# ── vehicle authority ─────────────────────────────────────────────────────────

class TestVehicleAuthority(unittest.TestCase):

    def test_vl_01_explicit_model_plus_catalog_accepts(self):
        decision = reconcile_vehicle_identity(
            [claim(ClaimType.VEHICLE_MODEL, "Fox"),
             claim(ClaimType.VEHICLE_YEAR, 2013)],
            catalog_lookup=catalog("Volkswagen", "Fox", "AUTO"))
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.value.marca, "Volkswagen")
        self.assertEqual(decision.value.modelo, "Fox")
        self.assertEqual(decision.value.tipo_vehiculo, "AUTO")
        self.assertEqual(decision.value.anio, 2013)
        self.assertEqual(decision.rule_id, VEHICLE_RULE_ID)

    def test_vl_02_semantic_make_alone_cannot_canonicalise(self):
        """"Volkswagen" invented beside a model the catalog cannot resolve stays a suggestion."""
        decision = reconcile_vehicle_identity(
            [claim(ClaimType.VEHICLE_MAKE, "Volkswagen",
                   evidence_class=EvidenceClass.SEMANTIC_INFERRED)],
            catalog_lookup=lambda text: None)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.outcome, ReconciliationOutcome.CLARIFY)
        self.assertIsNone(decision.value)
        self.assertIn("Volkswagen", decision.candidate_values)

    def test_the_canonical_make_comes_from_the_catalog_not_the_model(self):
        decision = reconcile_vehicle_identity(
            [claim(ClaimType.VEHICLE_MODEL, "Fox"),
             claim(ClaimType.VEHICLE_MAKE, "Ford",           # a wrong suggestion
                   evidence_class=EvidenceClass.SEMANTIC_INFERRED)],
            catalog_lookup=catalog("Volkswagen", "Fox", "AUTO"))
        self.assertEqual(decision.value.marca, "Volkswagen",
                         "the catalog overrules an inferred make")

    def test_vl_03_numeric_model_and_year_both_survive(self):
        decision = reconcile_vehicle_identity(
            [claim(ClaimType.VEHICLE_MODEL, "2008"),
             claim(ClaimType.VEHICLE_YEAR, 2014)],
            catalog_lookup=catalog("Peugeot", "2008", "SUV_4X4_DEPORTIVO"))
        self.assertTrue(decision.accepted)
        self.assertEqual((decision.value.marca, decision.value.modelo, decision.value.anio),
                         ("Peugeot", "2008", 2014))
        self.assertEqual(decision.value.tipo_vehiculo, "SUV_4X4_DEPORTIVO")

    def test_vl_04_ambiguous_model_clarifies_without_a_candidate(self):
        decision = reconcile_vehicle_identity(
            [claim(ClaimType.VEHICLE_MODEL, "2008"),
             claim(ClaimType.VEHICLE_MODEL, "208")],
            catalog_lookup=catalog("Peugeot", "2008"))
        self.assertEqual(decision.outcome, ReconciliationOutcome.CLARIFY)
        self.assertIsNone(decision.value, "no arbitrary candidate is chosen")
        self.assertEqual(decision.information_state, "BOTH")
        self.assertIn("2008", decision.candidate_values)
        self.assertIn("208", decision.candidate_values)

    def test_unresolvable_model_clarifies(self):
        decision = reconcile_vehicle_identity(
            [claim(ClaimType.VEHICLE_MODEL, "Cosita")], catalog_lookup=lambda t: None)
        self.assertEqual(decision.outcome, ReconciliationOutcome.CLARIFY)
        self.assertIsNone(decision.value)

    def test_vl_05_correction_supersession_is_carried(self):
        superseding = claim(ClaimType.VEHICLE_MODEL, "Kuga", supersedes=("claim-ka",))
        decision = reconcile_vehicle_identity([superseding],
                                              catalog_lookup=catalog("Ford", "Kuga", "SUV"))
        record = decision.to_record(claim_type=ClaimType.VEHICLE_MODEL,
                                    supersedes=("claim-ka",))
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.value.modelo, "Kuga")
        self.assertIn("claim-ka", record.supersedes)
        self.assertFalse(record.shadow, "C2 records a real decision, not a shadow one")


# ── location authority ────────────────────────────────────────────────────────

class TestLocationAuthority(unittest.TestCase):

    def test_vl_07_inspection_role_plus_zone_accepts(self):
        decision = reconcile_inspection_location(
            [claim(ClaimType.INSPECTION_LOCATION, "Berazategui")],
            zone_validator=zone("Sur", "Berazategui"))
        self.assertTrue(decision.accepted)
        self.assertEqual((decision.value.zone_group, decision.value.zone_detail),
                         ("Sur", "Berazategui"))
        self.assertEqual(decision.rule_id, LOCATION_RULE_ID)

    def test_vl_08_customer_origin_never_becomes_inspection_location(self):
        """Tigre is a perfectly valid locality. That is not the question being asked."""
        decision = reconcile_inspection_location(
            [claim(ClaimType.CUSTOMER_ORIGIN, "Tigre")],
            zone_validator=zone("Norte", "Tigre"))
        self.assertFalse(decision.accepted)
        self.assertIsNone(decision.value)
        self.assertIn("origin", decision.reason)

    def test_vl_09_two_inspection_locations_clarify(self):
        decision = reconcile_inspection_location(
            [claim(ClaimType.INSPECTION_LOCATION, "Berazategui"),
             claim(ClaimType.INSPECTION_LOCATION, "Quilmes")],
            zone_validator=zone())
        self.assertEqual(decision.outcome, ReconciliationOutcome.CLARIFY)
        self.assertEqual(decision.information_state, "BOTH")
        self.assertIsNone(decision.value)

    def test_unvalidated_locality_clarifies(self):
        decision = reconcile_inspection_location(
            [claim(ClaimType.INSPECTION_LOCATION, "Narnia")],
            zone_validator=lambda locality: None)
        self.assertEqual(decision.outcome, ReconciliationOutcome.CLARIFY)
        self.assertIsNone(decision.value)

    def test_origin_and_inspection_coexist_without_conflict(self):
        decision = reconcile_inspection_location(
            [claim(ClaimType.INSPECTION_LOCATION, "Berazategui"),
             claim(ClaimType.CUSTOMER_ORIGIN, "Tigre")],
            zone_validator=zone("Sur", "Berazategui"))
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.value.zone_detail, "Berazategui")


# ── the write path ────────────────────────────────────────────────────────────

class TestWritePath(unittest.TestCase):

    VEHICLE_FIELDS = ("marca", "modelo", "anio", "tipo_vehiculo")

    def test_vl_06_single_canonical_vehicle_write_path(self):
        """No candidate vehicle field is assigned outside the chokepoint."""
        tree = ast.parse(CE_SOURCE)
        chokepoint = None
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_apply_vehicle_identity":
                chokepoint = node
        self.assertIsNotNone(chokepoint, "the chokepoint exists")
        allowed = {id(n) for n in ast.walk(chokepoint)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or id(node) in allowed:
                continue
            for target in node.targets:
                if (isinstance(target, ast.Attribute) and target.attr in self.VEHICLE_FIELDS
                        and isinstance(target.value, ast.Name)
                        and target.value.id in ("focus", "focus_after", "candidate", "cand",
                                                "_fc", "_fc2", "_fc3")):
                    offenders.append((target.value.id, target.attr, node.lineno))
        self.assertEqual(offenders, [], f"vehicle writes outside the chokepoint: {offenders}")

    def test_vl_12_single_canonical_location_write_path(self):
        tree = ast.parse(CE_SOURCE)
        chokepoint = next((n for n in ast.walk(tree)
                           if isinstance(n, ast.FunctionDef)
                           and n.name == "_apply_inspection_zone"), None)
        self.assertIsNotNone(chokepoint)
        allowed = {id(n) for n in ast.walk(chokepoint)}
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or id(node) in allowed:
                continue
            for target in node.targets:
                if (isinstance(target, ast.Attribute)
                        and target.attr in ("zone_group", "zone_detail")
                        and isinstance(target.value, ast.Name)
                        and target.value.id in ("focus", "focus_after", "candidate", "cand",
                                                "_fc", "_fc2", "_fc3")):
                    offenders.append((target.value.id, target.attr, node.lineno))
        self.assertEqual(offenders, [], f"location writes outside the chokepoint: {offenders}")

    def test_vl_16_flags_roll_back_to_legacy_behaviour(self):
        """With the flags off the chokepoint is a plain assignment — the cutover reverses."""
        legacy = engine(vehicle=False, location=False)
        target = candidate()
        legacy._apply_vehicle_identity(ctx_with(), target, marca="Ford", modelo="Focus",
                                       tipo="AUTO", anio=2017)
        self.assertEqual((target.marca, target.modelo, target.tipo_vehiculo, target.anio),
                         ("Ford", "Focus", "AUTO", 2017))
        spot = candidate()
        legacy._apply_inspection_zone(ctx_with(), spot, zone_group="Sur",
                                      zone_detail="Berazategui")
        self.assertEqual((spot.zone_group, spot.zone_detail), ("Sur", "Berazategui"))

    def test_authority_on_writes_only_on_accept(self):
        live = engine(vehicle=True)
        target = candidate()
        with unittest.mock.patch("app.services.vehicle_catalog.lookup_vehicle",
                                 return_value=None):
            wrote = live._apply_vehicle_identity(ctx_with(), target, modelo="Cosita")
        self.assertFalse(wrote)
        self.assertIsNone(target.modelo, "a CLARIFY writes nothing")

        with unittest.mock.patch("app.services.vehicle_catalog.lookup_vehicle",
                                 return_value=SimpleNamespace(marca="Peugeot", modelo="2008",
                                                              tipo_vehiculo="SUV_4X4_DEPORTIVO")):
            wrote = live._apply_vehicle_identity(ctx_with(), target, modelo="2008", anio=2014)
        self.assertTrue(wrote)
        self.assertEqual((target.marca, target.modelo, target.anio, target.tipo_vehiculo),
                         ("Peugeot", "2008", 2014, "SUV_4X4_DEPORTIVO"))

    def test_location_authority_on_refuses_an_origin(self):
        live = engine(location=True)
        target = candidate()
        live._extract_zone_from_text = lambda text: SimpleNamespace(zone_group="Norte",
                                                                    zone_detail="Tigre")
        wrote = live._apply_inspection_zone(ctx_with(), target, zone_group="Norte",
                                            zone_detail="Tigre", role="CUSTOMER_ORIGIN")
        self.assertFalse(wrote)
        self.assertIsNone(target.zone_detail, "an origin never populates the car's location")


# ── scope, cycle, justification ───────────────────────────────────────────────

class TestScopeAndJustification(unittest.TestCase):

    def test_vl_10_pre_candidate_buffer_preserved(self):
        """`state.home_zone_*` remains the pre-candidate buffer; C2 did not repurpose it."""
        self.assertIn("state.home_zone_group = db_zone.zone_group", CE_SOURCE)
        self.assertIn("_get_active_inspection_location", CE_SOURCE)

    def test_vl_11_candidate_location_authoritative_after_candidate(self):
        from app.services.conversation_engine import ConversationEngine
        source = inspect.getsource(ConversationEngine._get_active_inspection_location)
        self.assertIn("current_focus candidate zone is authoritative", source)
        self.assertIn("state.home_zone_* is a revision-scoped", source,
                      "the buffer remains a fallback, not the authority")

    def test_vl_13_cycle_scoping_blocks_stale_evidence(self):
        from app.services.claim_projection import in_cycle
        stale = claim(ClaimType.INSPECTION_LOCATION, "Quilmes", cycle_id="cycle-1")
        current = claim(ClaimType.INSPECTION_LOCATION, "Berazategui", cycle_id="cycle-2")
        scoped = in_cycle([stale, current], "cycle-2")
        self.assertEqual([c.value for c in scoped], ["Berazategui"])
        decision = reconcile_inspection_location(scoped, zone_validator=zone())
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.value.zone_detail, "Berazategui")

    def test_vl_14_justification_recorded(self):
        decision = reconcile_inspection_location(
            [claim(ClaimType.INSPECTION_LOCATION, "Berazategui")],
            zone_validator=zone("Sur", "Berazategui"))
        record = decision.to_record(claim_type=ClaimType.INSPECTION_LOCATION,
                                    cycle_id="cycle-9", revision_id=4)
        self.assertEqual(record.rule_id, "reconcile.inspection_location")
        self.assertEqual(record.rule_version, "v1")
        self.assertEqual(record.information_state, "TRUE_ONLY")
        self.assertEqual(record.outcome, "ACCEPT")
        self.assertEqual(record.risk_tier, "MEDIUM")
        self.assertEqual(record.cycle_id, "cycle-9")
        self.assertEqual(record.revision_id, 4)
        self.assertTrue(record.evidence_ids)
        self.assertIn(ClaimType.INSPECTION_LOCATION, record.depends_on)


# ── downstream and blast radius ───────────────────────────────────────────────

class TestDownstreamUnchanged(unittest.TestCase):

    def test_vl_15_response_validator_consumes_canonical_state(self):
        """L4.7D still validates against CanonicalFacts, which C2 fills from the candidate."""
        from app.services.response_validator import CanonicalFacts, validate_response
        facts = CanonicalFacts(vehicle_marca="Peugeot", vehicle_modelo="2008",
                               inspection_zone_detail="Berazategui",
                               known_zone_names=("Berazategui", "Tigre", "Quilmes"))
        supported = validate_response("Coordinamos la revisión del Peugeot 2008 en "
                                      "Berazategui.", facts)
        self.assertEqual(supported.blocked, [], "reconciled state supports the claim")
        invented = validate_response("Coordinamos la revisión del Ford Focus en Quilmes.",
                                     facts)
        self.assertTrue(invented.blocked,
                        "a location canonical state does not support is still blocked")
        self.assertEqual([f.claim for f in invented.blocked], ["LOCATION"])

    def test_vl_18_no_pricing_scheduling_or_booking_authority_changed(self):
        from app.services import field_reconciler
        source = pathlib.Path(field_reconciler.__file__).read_text()
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        for forbidden in ("pricing", "schedule", "booking_flow_service",
                          "outbound_safety_gate", "models", "thread_revisions"):
            self.assertFalse(any(forbidden in m for m in imported),
                             f"C2 must not reach {forbidden}: {sorted(imported)}")
        # and the chokepoints touch no pricing/scheduling/booking field
        for method in ("_apply_vehicle_identity", "_apply_inspection_zone"):
            body = CE_SOURCE.split(f"def {method}")[1].split("\n    def ")[0]
            for field in ("precio", "quote", "slot", "booking", "estado", "flag",
                          "needs_human", "revision"):
                self.assertNotIn(f".{field} =", body,
                                 f"{method} must not write {field}")

    def test_vl_17_wild_b_sequence(self):
        """"para revisar un 2008 del 2014" then "Está en Berazategui, pero yo soy de Tigre"."""
        vehicle = reconcile_vehicle_identity(
            [claim(ClaimType.VEHICLE_MODEL, "2008"), claim(ClaimType.VEHICLE_YEAR, 2014)],
            catalog_lookup=catalog("Peugeot", "2008", "SUV_4X4_DEPORTIVO"))
        self.assertTrue(vehicle.accepted)
        self.assertEqual((vehicle.value.marca, vehicle.value.modelo, vehicle.value.anio,
                          vehicle.value.tipo_vehiculo),
                         ("Peugeot", "2008", 2014, "SUV_4X4_DEPORTIVO"))

        location = reconcile_inspection_location(
            [claim(ClaimType.INSPECTION_LOCATION, "Berazategui"),
             claim(ClaimType.CUSTOMER_ORIGIN, "Tigre")],
            zone_validator=zone("Sur", "Berazategui"))
        self.assertTrue(location.accepted)
        self.assertEqual((location.value.zone_group, location.value.zone_detail),
                         ("Sur", "Berazategui"))

        origin_only = reconcile_inspection_location(
            [claim(ClaimType.CUSTOMER_ORIGIN, "Tigre")], zone_validator=zone("Norte", "Tigre"))
        self.assertFalse(origin_only.accepted, "Tigre never becomes the inspection location")


if __name__ == "__main__":
    unittest.main()
