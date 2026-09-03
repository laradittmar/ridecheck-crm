"""L4.7C.1 — reconciler primitives: claims, states, records. Shadow only.

Phase C1 of the L4.7C design builds the vocabulary in which the two evidence producers can
be compared, and the append-only record in which a decision can be re-explained. It moves
**no authority**: these tests exist as much to prove what the new layer *cannot* do as to
prove what it computes.

CLAIM-01  explicit customer evidence class          CLAIM-10  cycle scope preserved
CLAIM-02  semantic inferred evidence class          CLAIM-11  correction supersession
CLAIM-03  catalog-confirmed is distinct             CLAIM-12  rule id / version recorded
CLAIM-04  absence → NEITHER                         CLAIM-13  append-only log
CLAIM-05  positive → TRUE_ONLY                      CLAIM-14  TurnEvidence unchanged
CLAIM-06  negative → FALSE_ONLY                     CLAIM-15  FieldEvidence unchanged
CLAIM-07  conflict → BOTH                           CLAIM-16  no canonical state mutation
CLAIM-08  confidence cannot alter the state         CLAIM-17  no outbound effect
CLAIM-09  future/conditional preserved              CLAIM-18  critical Wild examples project
"""
from __future__ import annotations

import ast
import copy
import json
import pathlib
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
    InformationState,
    Modality,
    Polarity,
    RiskTier,
    Temporality,
    information_state,
    risk_tier_for,
)
from app.schemas.turn_evidence import (  # noqa: E402
    AcceptanceEvidence,
    AcceptanceSignal,
    Alternative,
    CorrectionEvidence,
    CorrectionRelation,
    EvidenceStatus,
    LocationEvidence,
    LocationRole,
    ReconciliationLog,
    ReconciliationRecord,
    ReconciliationStatus,
    SchedulingPriority,
    SchedulingRequestEvidence,
    ServiceIntentEvidence,
    ServiceIntentKind,
    TurnEvidence,
    TurnRef,
    VehicleEvidence,
)
from app.services.claim_projection import (  # noqa: E402
    claims_from_field_evidence,
    claims_from_turn_evidence,
    in_cycle,
    project_all,
    turn_modality,
)
from app.services.shadow_reconciler import reconcile, summarise  # noqa: E402


def claim(claim_type=ClaimType.VEHICLE_MODEL, **kw) -> ClaimEvidence:
    return ClaimEvidence(claim_type=claim_type, **kw).with_id()


def field(value, source="CURRENT_TURN_EXACT", confirmed=True, current_turn=True):
    return SimpleNamespace(value=value, source=source, confidence="HIGH",
                           confirmed=confirmed, current_turn=current_turn)


def snapshot(**kw):
    base = dict(service_intent=field(None, "NONE", False, False),
                vehicle=field(None, "NONE", False, False),
                vehicle_year=field(None, "NONE", False, False),
                vehicle_category=field(None, "NONE", False, False),
                inspection_location=field(None, "NONE", False, False),
                customer_origin=field(None, "NONE", False, False),
                inspectability=field(None, "NONE", False, False),
                scheduling=field(None, "NONE", False, False))
    base.update(kw)
    return SimpleNamespace(**base)


# ── evidence classes ──────────────────────────────────────────────────────────

class TestEvidenceClasses(unittest.TestCase):

    def test_claim_01_explicit_customer(self):
        """A value the customer actually wrote is EXPLICIT_CUSTOMER and STATED."""
        evidence = TurnEvidence(vehicle_mentions=(
            VehicleEvidence(value="Volkswagen Fox", make="Volkswagen", model="Fox",
                            status=EvidenceStatus.PROPOSED),))
        claims = claims_from_turn_evidence(evidence, texts=["quiero comprar un fox"])
        model = next(c for c in claims if c.claim_type == ClaimType.VEHICLE_MODEL)
        self.assertEqual(model.evidence_class, EvidenceClass.EXPLICIT_CUSTOMER)
        self.assertEqual(model.explicitness, Explicitness.STATED)

    def test_claim_02_semantic_inferred(self):
        """The make the interpreter added is a suggestion, not the customer's word."""
        evidence = TurnEvidence(vehicle_mentions=(
            VehicleEvidence(value="Volkswagen Fox", make="Volkswagen", model="Fox",
                            status=EvidenceStatus.PROPOSED),))
        claims = claims_from_turn_evidence(evidence, texts=["quiero comprar un fox"])
        make = next(c for c in claims if c.claim_type == ClaimType.VEHICLE_MAKE)
        self.assertEqual(make.evidence_class, EvidenceClass.SEMANTIC_INFERRED)
        self.assertEqual(make.explicitness, Explicitness.IMPLIED)

    def test_claim_03_catalog_confirmed_is_distinct(self):
        """The catalog's word arrives through the deterministic snapshot, not the model."""
        claims = claims_from_field_evidence(
            snapshot(vehicle=field("Volkswagen Fox", "CANDIDATE")),
            texts=["quiero comprar un fox"])
        self.assertEqual(claims[0].evidence_class, EvidenceClass.CATALOG_CONFIRMED)
        self.assertNotEqual(claims[0].evidence_class, EvidenceClass.SEMANTIC_INFERRED)
        self.assertTrue(claims[0].producer.startswith("ce:field_evidence"))


# ── information states ────────────────────────────────────────────────────────

class TestInformationStates(unittest.TestCase):

    def test_claim_04_absence_is_neither(self):
        self.assertEqual(information_state([]), InformationState.NEITHER)
        # a claim with no value supports nothing either
        self.assertEqual(information_state([claim(value=None)]), InformationState.NEITHER)

    def test_claim_05_positive_is_true_only(self):
        self.assertEqual(information_state([claim(value="Fox")]), InformationState.TRUE_ONLY)

    def test_claim_06_negative_is_false_only(self):
        state = information_state([claim(value="Tigre", polarity=Polarity.NEGATED)])
        self.assertEqual(state, InformationState.FALSE_ONLY)
        self.assertNotEqual(state, InformationState.NEITHER)

    def test_claim_07_conflict_is_both(self):
        opposed = information_state([claim(value="Fox"),
                                     claim(value="Fox", polarity=Polarity.NEGATED)])
        incompatible = information_state([claim(value="Fox"), claim(value="Gol")])
        self.assertEqual(opposed, InformationState.BOTH)
        self.assertEqual(incompatible, InformationState.BOTH)

    def test_claim_08_confidence_cannot_alter_the_state(self):
        low = information_state([claim(value="Fox", confidence=0.01)])
        high = information_state([claim(value="Fox", confidence=0.99)])
        self.assertEqual(low, high)
        conflict_low = information_state([claim(value="Fox", confidence=0.99),
                                          claim(value="Gol", confidence=0.01)])
        self.assertEqual(conflict_low, InformationState.BOTH,
                         "a confident claim does not win a disagreement")

    def test_ambiguity_is_uncertainty_not_support(self):
        state = information_state([claim(value="2008", status=EvidenceStatus.AMBIGUOUS)])
        self.assertEqual(state, InformationState.NEITHER)


# ── temporality, modality, cycle, supersession ────────────────────────────────

class TestClaimQualifiers(unittest.TestCase):

    def test_claim_09_future_conditional_preserved(self):
        temporality, modality = turn_modality(["si me cierra te hablo"])
        self.assertEqual(temporality, Temporality.FUTURE)
        self.assertEqual(modality, Modality.CONDITIONAL)
        evidence = TurnEvidence(acceptance=AcceptanceEvidence(
            signal=AcceptanceSignal.ACCEPT, value=True, status=EvidenceStatus.CONFIRMED))
        accepted = claims_from_turn_evidence(evidence, texts=["si me cierra te hablo"])[0]
        self.assertEqual(accepted.claim_type, ClaimType.QUOTE_ACCEPTED)
        self.assertFalse(accepted.is_actionable_now,
                         "a conditional future acceptance can never satisfy a HIGH rule")
        self.assertEqual(accepted.risk_tier, RiskTier.HIGH)

    def test_present_factual_is_actionable_shape(self):
        evidence = TurnEvidence(acceptance=AcceptanceEvidence(
            signal=AcceptanceSignal.ACCEPT, value=True, status=EvidenceStatus.CONFIRMED))
        accepted = claims_from_turn_evidence(evidence, texts=["dale, avancemos"])[0]
        self.assertTrue(accepted.is_actionable_now)

    def test_claim_10_cycle_scope_preserved(self):
        evidence = TurnEvidence(vehicle_mentions=(
            VehicleEvidence(value="Ford Focus", model="Focus", make="Ford"),))
        current = claims_from_turn_evidence(evidence, texts=["un focus"], cycle_id="cycle-2")
        self.assertTrue(all(c.cycle_id == "cycle-2" for c in current))
        stale = [c.model_copy(update={"cycle_id": "cycle-1"}) for c in current]
        self.assertEqual(in_cycle(stale, "cycle-2"), [],
                         "a claim from a finished cycle is not evidence about this one")
        result = reconcile(stale, cycle_id="cycle-2")
        self.assertEqual(result.claim_count, 0)

    def test_claim_11_correction_supersession_preserved(self):
        evidence = TurnEvidence(
            corrections=(CorrectionEvidence(
                value=True, relation=CorrectionRelation.CORRECT_EXISTING,
                from_value=2014, to_value=2015, status=EvidenceStatus.CONFIRMED),),
            vehicle_mentions=(VehicleEvidence(value=None, year=2015,
                                              year_status=EvidenceStatus.CONFIRMED),))
        claims = claims_from_turn_evidence(evidence, texts=["Es del 2015 no del 2014"])
        correction = next(c for c in claims if c.claim_type == ClaimType.CORRECTION)
        self.assertEqual(correction.value["from"], 2014)
        self.assertEqual(correction.value["to"], 2015)
        year = next(c for c in claims if c.claim_type == ClaimType.VEHICLE_YEAR)
        self.assertEqual(year.value, 2015)
        # the old value is not erased by the projection — it survives inside the relation
        superseded = claim(ClaimType.VEHICLE_YEAR, value=2015,
                           supersedes=("claim-old-2014",))
        record = reconcile([superseded]).records[0]
        self.assertIn("claim-old-2014", record.supersedes)


# ── records, rules, log ───────────────────────────────────────────────────────

class TestReconciliationRecords(unittest.TestCase):

    def test_claim_12_rule_id_and_version_recorded(self):
        result = reconcile([claim(ClaimType.INSPECTION_LOCATION, value="Berazategui")])
        record = result.records[0]
        self.assertEqual(record.rule_id, "reconcile.location_role")
        self.assertEqual(record.rule_version, "v1")
        self.assertEqual(record.information_state, "TRUE_ONLY")
        self.assertEqual(record.outcome, "ACCEPT")
        self.assertEqual(record.risk_tier, "MEDIUM")
        self.assertTrue(record.evidence_ids, "the decision names the claims it rests on")

    def test_claim_13_log_is_append_only(self):
        first = reconcile([claim(ClaimType.INSPECTION_LOCATION, value="Berazategui")]).log
        second = reconcile([claim(ClaimType.INSPECTION_LOCATION, value="Quilmes")],
                           log=first).log
        self.assertEqual(len(first.records), 1)
        self.assertEqual(len(second.records), 2, "history grows, it is never rewritten")
        self.assertEqual(second.records[0], first.records[0])
        with self.assertRaises(Exception):
            second.records[0].canonical_value = "rewritten"   # frozen

    def test_high_risk_future_evidence_is_held_not_accepted(self):
        conditional = claim(ClaimType.QUOTE_ACCEPTED, value=True,
                            temporality=Temporality.FUTURE, modality=Modality.CONDITIONAL)
        record = reconcile([conditional]).records[0]
        self.assertEqual(record.outcome, "HOLD")
        self.assertIsNone(record.canonical_value)

    def test_contradiction_on_a_high_claim_escalates(self):
        records = reconcile([claim(ClaimType.QUOTE_ACCEPTED, value=True),
                             claim(ClaimType.QUOTE_ACCEPTED, value=True,
                                   polarity=Polarity.NEGATED)]).records
        self.assertEqual(records[0].outcome, "NEEDS_HUMAN")
        self.assertEqual(records[0].information_state, "BOTH")

    def test_dependencies_are_recorded(self):
        record = reconcile([claim(ClaimType.QUOTE_ACCEPTED, value=True)]).records[0]
        self.assertIn(ClaimType.INSPECTION_LOCATION, record.depends_on)


# ── the inputs are not touched ────────────────────────────────────────────────

class TestNonMutation(unittest.TestCase):

    def test_claim_14_turn_evidence_unchanged(self):
        evidence = TurnEvidence(
            turn=TurnRef(thread_id=1),
            vehicle_mentions=(VehicleEvidence(value="Ford Focus", model="Focus",
                                              make="Ford", year=2017),),
            location_mentions=(LocationEvidence(
                value="Quilmes", locality="Quilmes",
                role=LocationRole.INSPECTION_LOCATION.value),))
        before = evidence.to_canonical_json()
        claims_from_turn_evidence(evidence, texts=["un focus 2017 en quilmes"])
        reconcile(claims_from_turn_evidence(evidence, texts=["un focus 2017 en quilmes"]))
        self.assertEqual(evidence.to_canonical_json(), before)

    def test_claim_15_field_evidence_unchanged(self):
        snap = snapshot(vehicle=field("Ford Focus", "CANDIDATE"),
                        inspection_location=field("Quilmes", "THREAD_STATE"))
        before = copy.deepcopy(vars(snap))
        claims_from_field_evidence(snap, texts=["un focus en quilmes"])
        self.assertEqual({k: vars(v) for k, v in vars(snap).items()},
                         {k: vars(v) for k, v in before.items()})


# ── no authority ──────────────────────────────────────────────────────────────

class TestNoAuthority(unittest.TestCase):

    MODULES = ("backend/app/schemas/claims.py",
               "backend/app/services/claim_projection.py",
               "backend/app/services/shadow_reconciler.py")

    def test_claim_16_no_canonical_state_mutation(self):
        """Static proof: the C1 modules cannot reach anything that writes."""
        forbidden = ("models", "db", "session", "conversation_engine", "pricing",
                     "schedule", "outbound_safety_gate", "booking_flow_service",
                     "thread_revisions", "whatsapp_threads", "lead_lifecycle", "requests")
        for relative in self.MODULES:
            tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
            imported: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
                elif isinstance(node, ast.Import):
                    imported.update(a.name for a in node.names)
            for name in forbidden:
                self.assertFalse(any(name in module for module in imported),
                                 f"{relative} must not import {name}: {sorted(imported)}")
            source = (ROOT / relative).read_text(encoding="utf-8")
            for writer in (".add(", ".commit(", ".flush(", ".delete(", ".execute("):
                self.assertNotIn(writer, source, f"{relative} contains {writer}")

    def test_every_record_is_marked_shadow(self):
        result = reconcile([claim(ClaimType.VEHICLE_MODEL, value="Focus"),
                            claim(ClaimType.QUOTE_ACCEPTED, value=True)])
        self.assertTrue(all(r.shadow for r in result.records))
        self.assertTrue(result.shadow)
        self.assertTrue(summarise(result)["shadow"])

    def test_claim_17_no_outbound_effect(self):
        """The CE hook that carries this layer sends nothing and writes nothing."""
        from app.services.conversation_engine import ConversationEngine, _Context
        engine = ConversationEngine.__new__(ConversationEngine)
        engine.db = MagicMock()
        engine.settings = SimpleNamespace(
            openai_api_key="sk-stub", openai_chat_model="gpt-4o-mini",
            shadow_understand_enabled=True, shadow_understand_async=False,
            shadow_evidence_path=None)
        engine._correlation_id = "corr-c1"
        engine._send_text_to_wa = MagicMock()
        engine._send_flow_button = MagicMock()

        ctx = _Context.__new__(_Context)
        ctx.thread = SimpleNamespace(id=11)
        ctx.lead = SimpleNamespace(id=1, flag=None, estado="CONSULTA_NUEVA")
        ctx.contact = SimpleNamespace(wa_id="549110000000")
        ctx.candidates = []
        ctx.state = SimpleNamespace(last_stage="QUALIFYING", last_offered_slots=None,
                                    current_cycle_start_message_db_id=7,
                                    current_revision_id=None)
        ctx.db_messages = []
        ctx.inbound_wa_message_id = "wamid.X"
        before_state, before_lead = dict(vars(ctx.state)), dict(vars(ctx.lead))

        payload = json.dumps({"vehicles": [{"make": "Ford", "model": "Focus", "year": 2017,
                                            "status": "CONFIRMED"}]})
        with patch("app.services.semantic_interpreter.SemanticTurnInterpreter._call_openai",
                   return_value=(payload, {})), \
             patch("app.services.conversation_engine.OutboundSafetyGate") as gate, \
             tempfile.TemporaryDirectory() as tmp:
            engine.settings.shadow_evidence_path = str(pathlib.Path(tmp) / "s.jsonl")
            engine._run_shadow_understand(ctx, SimpleNamespace(wa_message_id="w"),
                                          ["un focus 2017"])
            record = json.loads((pathlib.Path(tmp) / "s.jsonl").read_text().strip())

        self.assertEqual(dict(vars(ctx.state)), before_state)
        self.assertEqual(dict(vars(ctx.lead)), before_lead)
        self.assertEqual(ctx.candidates, [])
        engine.db.add.assert_not_called()
        engine.db.commit.assert_not_called()
        engine._send_text_to_wa.assert_not_called()
        engine._send_flow_button.assert_not_called()
        gate.assert_not_called()

        # …and the reconciliation summary reached the shadow record
        self.assertEqual(record["record_version"], "shadow-record/1.2")
        self.assertTrue(record["reconciliation"]["shadow"])
        self.assertIn(ClaimType.VEHICLE_MODEL, record["reconciliation"]["claim_types"])

    def test_shadow_record_carries_no_customer_text(self):
        result = reconcile(claims_from_turn_evidence(
            TurnEvidence(location_mentions=(LocationEvidence(
                value="Berazategui", locality="Berazategui",
                role=LocationRole.INSPECTION_LOCATION.value),)),
            texts=["el auto esta en Berazategui"]))
        blob = json.dumps(summarise(result), ensure_ascii=False)
        self.assertNotIn("Berazategui", blob, "the summary carries types, never values")


# ── critical scenarios ────────────────────────────────────────────────────────

class TestCriticalScenarios(unittest.TestCase):

    def test_claim_18a_location_roles_split(self):
        evidence = TurnEvidence(location_mentions=(
            LocationEvidence(value="Berazategui", locality="Berazategui",
                             role=LocationRole.INSPECTION_LOCATION.value,
                             status=EvidenceStatus.CONFIRMED),
            LocationEvidence(value="Tigre", locality="Tigre",
                             role=LocationRole.CUSTOMER_ORIGIN.value,
                             status=EvidenceStatus.CONFIRMED)))
        claims = claims_from_turn_evidence(
            evidence, texts=["Está en Berazategui, pero yo soy de Tigre."])
        types = {c.claim_type: c.value for c in claims}
        self.assertEqual(types[ClaimType.INSPECTION_LOCATION], "Berazategui")
        self.assertEqual(types[ClaimType.CUSTOMER_ORIGIN], "Tigre")
        result = reconcile(claims)
        self.assertEqual(result.states[ClaimType.INSPECTION_LOCATION], "TRUE_ONLY")
        self.assertEqual(result.states[ClaimType.CUSTOMER_ORIGIN], "TRUE_ONLY")

    def test_claim_18b_conditional_acceptance_has_no_authority(self):
        evidence = TurnEvidence(acceptance=AcceptanceEvidence(
            signal=AcceptanceSignal.ACCEPT, value=True, status=EvidenceStatus.CONFIRMED))
        result = reconcile(claims_from_turn_evidence(
            evidence, texts=["si me cierra te hablo"]))
        record = result.records[0]
        self.assertEqual(record.claim_type, ClaimType.QUOTE_ACCEPTED)
        self.assertEqual(record.outcome, "HOLD")
        self.assertIsNone(record.canonical_value)

    def test_claim_18c_year_correction_carries_both_sides(self):
        evidence = TurnEvidence(
            corrections=(CorrectionEvidence(
                value=True, relation=CorrectionRelation.CORRECT_EXISTING,
                from_value=2014, to_value=2015, status=EvidenceStatus.CONFIRMED),),
            vehicle_mentions=(VehicleEvidence(value=None, year=2015,
                                              year_status=EvidenceStatus.CONFIRMED),))
        result = reconcile(claims_from_turn_evidence(
            evidence, texts=["Es del 2015, no del 2014"]))
        self.assertEqual(result.states[ClaimType.VEHICLE_YEAR], "TRUE_ONLY")
        self.assertIn(ClaimType.CORRECTION, result.states)

    def test_claim_18d_ordered_scheduling_survives_projection(self):
        evidence = TurnEvidence(scheduling_requests=(
            SchedulingRequestEvidence(priority=SchedulingPriority.PRIMARY,
                                      day_expression="TOMORROW", time="15:00", rank=1,
                                      status=EvidenceStatus.CONFIRMED),
            SchedulingRequestEvidence(priority=SchedulingPriority.FALLBACK,
                                      day_expression="THURSDAY", time=None,
                                      flexible_time=True, rank=2,
                                      status=EvidenceStatus.CONFIRMED)))
        branches = claims_from_turn_evidence(
            evidence, texts=["Mñ 15hs? O nose jueves que tenes"])[0].value
        self.assertEqual([b["day"] for b in branches], ["TOMORROW", "THURSDAY"])
        self.assertEqual(branches[0]["time"], "15:00")
        self.assertIsNone(branches[1]["time"], "a time never migrates between branches")

    def test_claim_18e_model_only_mention_needs_the_catalog(self):
        """"Quiero comprar un Fox": model explicit, make only a suggestion until the
        deterministic snapshot confirms it."""
        evidence = TurnEvidence(vehicle_mentions=(
            VehicleEvidence(value="Volkswagen Fox", make="Volkswagen", model="Fox",
                            catalog_candidate="Volkswagen Fox",
                            status=EvidenceStatus.PROPOSED),))
        semantic = claims_from_turn_evidence(evidence, texts=["Quiero comprar un Fox"])
        make = next(c for c in semantic if c.claim_type == ClaimType.VEHICLE_MAKE)
        self.assertEqual(make.evidence_class, EvidenceClass.SEMANTIC_INFERRED)

        combined = project_all(evidence, snapshot(vehicle=field("Volkswagen Fox",
                                                               "CANDIDATE")),
                               texts=["Quiero comprar un Fox"])
        classes = {c.evidence_class for c in combined
                   if c.claim_type in (ClaimType.VEHICLE_MODEL, ClaimType.VEHICLE_MAKE)}
        self.assertIn(EvidenceClass.CATALOG_CONFIRMED, classes,
                      "catalog confirmation arrives from the deterministic producer")

    def test_risk_tiers_are_assigned(self):
        self.assertEqual(risk_tier_for(ClaimType.CUSTOMER_ORIGIN), RiskTier.LOW)
        self.assertEqual(risk_tier_for(ClaimType.INSPECTION_LOCATION), RiskTier.MEDIUM)
        self.assertEqual(risk_tier_for(ClaimType.QUOTE_ACCEPTED), RiskTier.HIGH)
        self.assertEqual(risk_tier_for("something.unclassified"), RiskTier.HIGH,
                         "an unclassified consequence is not a small one")


if __name__ == "__main__":
    unittest.main()
