"""L4.7W5-TRACE-ROW-SEMANTICS-R2 — absence of conflict is not agreement either.

R1 stopped deriving a row from `InformationState` and derived it from participation
instead. That removed the falsehood the audit found and left its mirror standing:

    if len(set(present)) >= 2:
        return CONFLICT if sources_conflict(contributions) else AGREE

`sources_conflict` only ever looked for an EXPLICIT incompatibility on a claim type both
sources had spoken to. Two producers who shared no claim type at all could not trip it, so
semantic supplying `vehicle.model` while CE supplied `vehicle.year` returned `AGREE` — two
engines agreeing about nothing in particular.

`hybrid-decision-trace/1.2` requires a shared canonical proposition, proven compatible.

The four things this suite keeps apart, because collapsing any two of them is how the
previous two labels went wrong:

* **raw language** — what the customer typed or said. The classifier never sees it, and no
  test here compares sentences. There is no phrase list, no synonym table and no regex over
  customer words anywhere in this milestone.
* **canonical proposition** — the business field two producers may be said to speak about.
  Two claims address the same proposition when they carry the same claim type, never when
  their wording resembles each other.
* **evidence compatibility** — whether their canonical values mean the same thing, decided
  by resolvers the system already has. Where none can decide, the answer is UNPROVEN.
* **business authority** — who is allowed to act. Nothing here grants it. Semantic-only
  evidence stays valid evidence; deterministic-only evidence stays valid evidence; the
  reconciler's `outcome` is recorded in its own field and never read as agreement.

R2-01..11   the §6 examples, one per outcome
R2-12..20   comparison identity, and what must never change it
R2-21..30   the owner visibility policy and WAMID masking
R2-31..40   the fuzzy-vehicle repair, proved as behaviour rather than as a diff
R2-41..50   the remaining §11 matrix, the preserved trace, and the surfaces
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import pathlib
import re
import sys
import types
import unittest
from unittest import mock

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

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models as models
from app.models import HybridDecisionTraceRow
from app.schemas.claims import (ClaimEvidence, ClaimType, EvidenceClass, Explicitness,
                                Polarity)
from app.schemas.hybrid_trace import (CANONICAL_PROPOSITIONS, Classification,
                                      ComparisonVerdict, DecisionSite, EvidenceSource,
                                      SemanticEvidence, TRACE_VERSION)
from app.services import hybrid_trace as svc
from app.ui import hybrid_trace_view as view

FIXTURE = ROOT / "tests" / "fixtures" / "hybrid_trace_live_partial_reconciliation.json"
LIVE_BYTES = FIXTURE.read_bytes()
LIVE = json.loads(LIVE_BYTES.decode("utf-8"))

SEM = EvidenceSource.SEMANTIC
DET = EvidenceSource.DETERMINISTIC
CAN = EvidenceSource.CANONICAL_STATE

SEM_ABSENT = SemanticEvidence(status="ABSENT", produced_claims=())


def sem_ok(claims=("vehicle_mentions",)):
    return SemanticEvidence(ok=True, status="OK", produced_claims=tuple(claims))


def claim(producer, claim_type, value, *, polarity=Polarity.ASSERTED,
          evidence_class=EvidenceClass.DETERMINISTIC_EXTRACTED, confidence=None):
    return ClaimEvidence(claim_type=claim_type, value=value, polarity=polarity,
                         evidence_class=evidence_class, producer=producer,
                         explicitness=Explicitness.STATED,
                         confidence=confidence).with_id()


def semantic_claim(claim_type, value, **kw):
    kw.setdefault("evidence_class", EvidenceClass.SEMANTIC_INFERRED)
    return claim("semantic:understand", claim_type, value, **kw)


def ce_claim(claim_type, value, **kw):
    return claim("ce:catalog", claim_type, value, **kw)


class Record:
    def __init__(self, claim_type=ClaimType.VEHICLE_MODEL, information_state="TRUE_ONLY",
                 outcome="ACCEPT", reason="why", rule_id="reconcile.vehicle_identity",
                 rule_version="v1", evidence_ids=()):
        self.claim_type = claim_type
        self.information_state = information_state
        self.outcome = outcome
        self.reason = reason
        self.rule_id = rule_id
        self.rule_version = rule_version
        self.evidence_ids = tuple(evidence_ids)


def row(claims=(), *, record=None, site=DecisionSite.VEHICLE_IDENTITY_APPLY, semantic=None):
    built = svc.reconciliation_from(record or Record(), claims, decision_site_id=site)
    return svc.finalize_rows((built,), semantic or SEM_ABSENT)[0]


def _db() -> Session:
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    return Session(engine)


# ── R2-01..11: the §6 examples ───────────────────────────────────────────────

class RequiredExamples(unittest.TestCase):

    def test_r2_01_same_canonical_identity_agrees_whatever_the_method(self):
        """The interpreter extracted `208`; the catalogue produced `Peugeot 208`.

        Different producers, different methods, different strings — one car. Agreement is
        established through `vehicle_catalog.lookup_vehicle`, the resolver the reconciler
        already treats as the authority, and never by comparing the two strings.
        """
        built = row((semantic_claim(ClaimType.VEHICLE_MODEL, "208"),
                     ce_claim(ClaimType.VEHICLE_MODEL, "Peugeot 208")))
        self.assertEqual(built.classification, Classification.AGREE)
        self.assertEqual(len(built.compared_propositions), 1)
        proposition = built.compared_propositions[0]
        self.assertEqual(proposition.claim_type, ClaimType.VEHICLE_MODEL)
        self.assertEqual(proposition.verdict, ComparisonVerdict.COMPATIBLE)
        self.assertEqual(proposition.basis, "same canonical identity")
        self.assertEqual(sorted(proposition.sources), sorted([SEM, DET]))

    def test_r2_02_different_canonical_identities_conflict(self):
        built = row((semantic_claim(ClaimType.VEHICLE_MODEL, "Peugeot 208"),
                     ce_claim(ClaimType.VEHICLE_MODEL, "Toyota Corolla")))
        self.assertEqual(built.classification, Classification.CONFLICT)
        self.assertEqual(built.compared_propositions[0].verdict,
                         ComparisonVerdict.INCOMPATIBLE)

    def test_r2_03_model_against_year_is_not_comparable(self):
        """The R1 defect, exactly. Two producers, no shared proposition, never AGREE."""
        built = row((semantic_claim(ClaimType.VEHICLE_MODEL, "Peugeot 208"),
                     ce_claim(ClaimType.VEHICLE_YEAR, 2020)))
        self.assertEqual(built.classification, Classification.PARALLEL_EVIDENCE)
        self.assertEqual(built.compared_propositions, ())
        self.assertEqual(sorted(built.participating_sources), sorted([SEM, DET]))
        self.assertNotEqual(built.classification, Classification.AGREE)

    def test_r2_04_model_against_locality_is_parallel_evidence(self):
        built = row((semantic_claim(ClaimType.VEHICLE_MODEL, "Peugeot 208"),
                     ce_claim(ClaimType.INSPECTION_LOCATION, "Palermo")))
        self.assertEqual(built.classification, Classification.PARALLEL_EVIDENCE)
        self.assertNotEqual(built.classification, Classification.AGREE)

    def test_r2_05_semantic_alone_is_single_producer_semantic(self):
        built = row((semantic_claim(ClaimType.VEHICLE_MODEL, "Peugeot 208"),))
        self.assertEqual(built.classification, Classification.SINGLE_PRODUCER)
        self.assertEqual(built.participating_sources, (SEM,))

    def test_r2_06_ce_alone_is_single_producer_deterministic(self):
        built = row((ce_claim(ClaimType.VEHICLE_MODEL, "Peugeot 208"),))
        self.assertEqual(built.classification, Classification.SINGLE_PRODUCER)
        self.assertEqual(built.participating_sources, (DET,))

    def test_r2_07_canonical_state_alone_is_single_producer_canonical(self):
        built = row((claim("canonical:deterministic_acceptance",
                           ClaimType.QUOTE_ACCEPTED, True),))
        self.assertEqual(built.classification, Classification.SINGLE_PRODUCER)
        self.assertEqual(built.participating_sources, (CAN,))

    def test_r2_08_shared_proposition_without_canonical_equivalence_is_unproven(self):
        """Two localities that no resolver reachable from an observer can place.

        The trace does not guess in either direction: it cannot prove they are the same
        place, and it cannot prove they are different. `COMPARISON_UNPROVEN` says so.
        """
        built = row((semantic_claim(ClaimType.INSPECTION_LOCATION, "Villa Crespo"),
                     ce_claim(ClaimType.INSPECTION_LOCATION, "V. Crespo")),
                    record=Record(claim_type=ClaimType.INSPECTION_LOCATION))
        self.assertEqual(built.classification, Classification.COMPARISON_UNPROVEN)
        self.assertEqual(built.compared_propositions[0].verdict, ComparisonVerdict.UNPROVEN)
        self.assertNotEqual(built.classification, Classification.AGREE)
        self.assertNotEqual(built.classification, Classification.CONFLICT)

    def test_r2_09_self_contradiction_is_not_cross_engine_conflict(self):
        built = row((ce_claim(ClaimType.VEHICLE_MODEL, "Peugeot 208"),
                     ce_claim(ClaimType.VEHICLE_MODEL, "Ford Ka")),
                    record=Record(information_state="BOTH"))
        self.assertEqual(built.classification, Classification.AMBIGUOUS_EVIDENCE)
        self.assertEqual(built.self_contradiction_sources, (DET,))
        self.assertEqual(built.information_state, "BOTH",
                         "polarity is retained separately, not replaced by the label")
        self.assertNotEqual(built.classification, Classification.CONFLICT)

    def test_r2_10_missing_routing_is_neither_conflict_nor_agreement(self):
        built = row((), semantic=sem_ok())
        self.assertEqual(built.classification, Classification.NOT_ROUTED)
        self.assertEqual(built.not_routed_sources, (SEM,))
        self.assertNotIn(built.classification, Classification.COMPARED)

    def test_r2_11_zero_evidence_is_no_evidence(self):
        built = row(())
        self.assertEqual(built.classification, Classification.NO_EVIDENCE)
        self.assertEqual(built.participating_sources, ())

    def test_r2_11b_no_test_in_this_suite_compares_raw_sentences(self):
        """The classifier must never be reachable with customer text."""
        source = (ROOT / "backend" / "app" / "services"
                  / "hybrid_trace.py").read_text(encoding="utf-8-sig")
        classifier = source[source.index("def _verdict_for("):source.index("def classify_row(")]
        for forbidden in ("SequenceMatcher", "difflib", "startswith(", "in text",
                          "burst", "message", "lower()"):
            self.assertNotIn(forbidden, classifier,
                             f"{forbidden!r} would put language back into the classifier")

    def test_r2_11c_evidence_classification_grants_no_authority(self):
        """A single producer's evidence stays valid evidence; the outcome is its own field."""
        for outcome in ("ACCEPT", "CLARIFY", "HOLD", "NEEDS_HUMAN"):
            with self.subTest(outcome=outcome):
                built = row((semantic_claim(ClaimType.VEHICLE_MODEL, "Peugeot 208"),),
                            record=Record(outcome=outcome))
                self.assertEqual(built.classification, Classification.SINGLE_PRODUCER)
                self.assertEqual(built.outcome, outcome)
                self.assertEqual(built.participating_sources, (SEM,),
                                 "absence of CE corroboration must not erase the evidence")


# ── R2-12..20: comparison identity ───────────────────────────────────────────

class ComparisonIdentityIsLogical(unittest.TestCase):

    def apply_row(self):
        return svc.reconciliation_from(
            Record(), (ce_claim(ClaimType.VEHICLE_MODEL, "Peugeot 208"),),
            decision_site_id=DecisionSite.VEHICLE_IDENTITY_APPLY)

    def unrelated_row(self):
        return svc.reconciliation_from(
            Record(claim_type=ClaimType.INSPECTION_LOCATION,
                   rule_id="reconcile.inspection_location"),
            (ce_claim(ClaimType.INSPECTION_LOCATION, "Palermo"),),
            decision_site_id=DecisionSite.LOCATION_INSPECTION_APPLY)

    def test_r2_12_no_positional_component_in_the_identity(self):
        import inspect
        source = inspect.getsource(svc.logical_comparison_id)
        for forbidden in ("ordinal", "index", "position"):
            self.assertNotIn(forbidden, source.split('"""')[2],
                             "identity must not be derived from where the row sits")

    def test_r2_13_inserting_an_earlier_row_does_not_change_it(self):
        """The R1 defect: an unrelated insertion renamed every later comparison."""
        alone = svc.finalize_rows((self.apply_row(),), SEM_ABSENT)
        after = svc.finalize_rows((self.unrelated_row(), self.apply_row()), SEM_ABSENT)
        self.assertEqual(alone[0].logical_comparison_id, after[1].logical_comparison_id)
        self.assertEqual(alone[0].occurrence_index, after[1].occurrence_index)

    def test_r2_14_claim_ordering_does_not_change_it(self):
        left = ce_claim(ClaimType.VEHICLE_MODEL, "Peugeot 208")
        right = semantic_claim(ClaimType.VEHICLE_MODEL, "208")
        first = svc.reconciliation_from(Record(), (left, right),
                                        decision_site_id=DecisionSite.VEHICLE_IDENTITY_APPLY)
        second = svc.reconciliation_from(Record(), (right, left),
                                         decision_site_id=DecisionSite.VEHICLE_IDENTITY_APPLY)
        self.assertEqual(first.logical_comparison_id, second.logical_comparison_id)

    def test_r2_15_source_ordering_does_not_change_it(self):
        claims = (semantic_claim(ClaimType.VEHICLE_MODEL, "208"),
                  ce_claim(ClaimType.VEHICLE_MODEL, "Peugeot 208"))
        ids = {svc.reconciliation_from(
            Record(), tuple(reversed(claims)) if flip else claims,
            decision_site_id=DecisionSite.VEHICLE_IDENTITY_APPLY).logical_comparison_id
            for flip in (False, True)}
        self.assertEqual(len(ids), 1)

    def test_r2_16_different_logical_comparisons_stay_distinct(self):
        seen = {
            self.apply_row().logical_comparison_id,
            self.unrelated_row().logical_comparison_id,
            svc.reconciliation_from(
                Record(), (ce_claim(ClaimType.VEHICLE_MODEL, "Ford Ka"),),
                decision_site_id=DecisionSite.VEHICLE_IDENTITY_APPLY).logical_comparison_id,
            svc.reconciliation_from(
                Record(), (ce_claim(ClaimType.VEHICLE_MODEL, "Peugeot 208"),),
                decision_site_id=DecisionSite.VEHICLE_FUZZY_ADMISSIBILITY
            ).logical_comparison_id,
            svc.reconciliation_from(
                Record(rule_version="v2"), (ce_claim(ClaimType.VEHICLE_MODEL, "Peugeot 208"),),
                decision_site_id=DecisionSite.VEHICLE_IDENTITY_APPLY).logical_comparison_id,
        }
        self.assertEqual(len(seen), 5, "site, claims, rule version must each separate")

    def test_r2_17_repeats_share_the_identity_and_differ_by_occurrence(self):
        rows = svc.finalize_rows((self.apply_row(), self.apply_row()), SEM_ABSENT)
        self.assertEqual(rows[0].logical_comparison_id, rows[1].logical_comparison_id)
        self.assertEqual([r.occurrence_index for r in rows], [0, 1])

    def test_r2_18_the_identity_is_a_pure_function_of_its_inputs(self):
        """Stable across deployments: same inputs, same id, every time."""
        kwargs = dict(decision_site_id=DecisionSite.VEHICLE_IDENTITY_APPLY,
                      claim_family=ClaimType.VEHICLE_MODEL,
                      rule_id="reconcile.vehicle_identity", rule_version="v1",
                      contributions=svc.contributions_from_claims(
                          (ce_claim(ClaimType.VEHICLE_MODEL, "Peugeot 208"),)))
        self.assertEqual(svc.logical_comparison_id(**kwargs),
                         svc.logical_comparison_id(**kwargs))

    def test_r2_19_the_engine_passes_nothing_positional(self):
        """No caller may hand the row builder a positional ordinal.

        G3-1 added a second builder call (`_trace_decision`), so counting call sites no
        longer expresses the invariant. What must hold is that EVERY call names its
        arguments and none of them is positional: position is what 1.2 removed from
        comparison identity, and a keyword list is the thing that keeps it out.
        """
        import ast
        source = (ROOT / "backend" / "app" / "services"
                  / "conversation_engine.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "reconciliation_from"]
        self.assertGreaterEqual(len(calls), 1)
        positional = {"ordinal", "index", "position", "row_index"}
        for call in calls:
            names = {k.arg for k in call.keywords}
            self.assertIn("decision_site_id", names)
            self.assertEqual(names & positional, set())
            # record + claims are the only positional arguments any caller may pass
            self.assertLessEqual(len(call.args), 2)

    def test_r2_20_counts_separate_rows_from_logical_comparisons(self):
        rows = svc.finalize_rows((self.apply_row(), self.apply_row(), self.unrelated_row()),
                                 SEM_ABSENT)
        counts = svc.family_counts(rows)
        self.assertEqual(counts["comparison_rows"], 3)
        self.assertEqual(counts["distinct_comparisons"], 2)
        self.assertEqual(counts["distinct_families"], 2)


# ── R2-21..30: visibility and WAMID masking ──────────────────────────────────

class VisibilityPolicy(unittest.TestCase):

    def shown(self, claim_type, value):
        built = row((claim("ce:parser", claim_type, value),),
                    record=Record(claim_type=claim_type))
        return built.source_evidence[0]

    def test_r2_21_authorized_operational_values_appear(self):
        for claim_type, value in ((ClaimType.VEHICLE_MAKE, "Peugeot"),
                                  (ClaimType.VEHICLE_MODEL, "Peugeot 208"),
                                  (ClaimType.VEHICLE_YEAR, 2020),
                                  (ClaimType.VEHICLE_CATEGORY, "AUTO"),
                                  (ClaimType.SERVICE_INTENT, "revision_pre_compra"),
                                  (ClaimType.INSPECTION_LOCATION, "Palermo")):
            with self.subTest(claim_type=claim_type):
                contribution = self.shown(claim_type, value)
                self.assertIsNotNone(contribution.values[0])
                self.assertFalse(contribution.withheld)

    def test_r2_22_forbidden_pii_never_appears_even_in_authorized_families(self):
        for claim_type, value in (
                (ClaimType.INSPECTION_LOCATION, "0000000"),
                (ClaimType.INSPECTION_LOCATION, "cliente@example.com"),
                (ClaimType.INSPECTION_LOCATION, "Av. Santa Fe 1234, piso 3, depto B, "
                                                "Palermo, Ciudad de Buenos Aires"),
                (ClaimType.VEHICLE_MODEL, "wamid.SANITISED-SHAPE-ONLY"),
                (ClaimType.VEHICLE_MODEL, "bk_tok_DO_NOT_LEAK_0001"),
                (ClaimType.VEHICLE_MODEL, "sk-ABCDEFGHIJKLMNOP"),
                (ClaimType.SERVICE_INTENT, "-----BEGIN PRIVATE KEY-----"),
                (ClaimType.SERVICE_INTENT, "https://example.com/x")):
            with self.subTest(value=value[:22]):
                contribution = self.shown(claim_type, value)
                self.assertEqual(contribution.values, (None,))
                self.assertTrue(contribution.withheld)
                self.assertNotIn(value, json.dumps(_as_dict(contribution)))

    def test_r2_23_confidence_is_carried_when_the_producer_supplied_it(self):
        built = row((semantic_claim(ClaimType.VEHICLE_MODEL, "Peugeot 208", confidence=0.82),))
        self.assertEqual(built.source_evidence[0].confidences, (0.82,))

    def test_r2_24_a_wamid_is_masked_irreversibly(self):
        original = "wamid.HBgNABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789=="
        masked = view.mask_wamid(original)
        self.assertTrue(masked.startswith("wamid⋯"))
        self.assertNotIn(original.split(".", 1)[1], masked)
        body = original.split(".", 1)[1]
        for size in range(8, len(body) + 1):
            for start in range(0, len(body) - size + 1):
                self.assertNotIn(body[start:start + size], masked)

    def test_r2_25_masking_is_stable_and_distinguishing(self):
        a, b = "wamid.AAAABBBBCCCC", "wamid.AAAABBBBCCCD"
        self.assertEqual(view.mask_wamid(a), view.mask_wamid(a))
        self.assertNotEqual(view.mask_wamid(a), view.mask_wamid(b))
        self.assertEqual(view.mask_wamid("not-a-wamid"), "not-a-wamid")

    def test_r2_26_no_full_wamid_survives_anywhere_on_the_page(self):
        payload = json.loads(json.dumps(LIVE))
        secret = "wamid.HBgNSHOULDNEVERAPPEAR0123456789"
        payload["ordered_message_ids"] = [secret]
        page = _page(payload)
        self.assertNotIn(secret, page)
        self.assertNotIn(secret.split(".", 1)[1], page)
        self.assertIn(view.mask_wamid(secret), page,
                      "the masked form must be shown, so messages stay distinguishable")

    def test_r2_27_every_panel_is_masked_including_the_ones_nobody_remembered(self):
        """A WAMID travels in more places than the message list.

        It is also on `provenance.source_message_ids` of every semantic claim, which the
        semantic-evidence panel dumps verbatim. Masking panel by panel missed that one; the
        page-level sweep is what makes the guarantee hold for panels not yet written.
        """
        payload = json.loads(json.dumps(LIVE))
        secret = "wamid.HBgNRAWPANELLEAK0123456789"
        payload["ordered_message_ids"] = [secret]
        payload["semantic"]["evidence"]["vehicle_mentions"][0]["provenance"][
            "source_message_ids"] = [secret]
        page = _page(payload)
        self.assertNotIn(secret, page)
        for panel in ("Traza completa (JSON)", "Evidencia semántica completa"):
            with self.subTest(panel=panel):
                self.assertNotIn(secret, page.split(panel)[1])
        self.assertIn("wamid⋯", page)

    def test_r2_28_the_stored_payload_is_never_altered_by_masking(self):
        payload = json.loads(json.dumps(LIVE))
        before = json.dumps(payload, sort_keys=True)
        _page(payload)
        self.assertEqual(json.dumps(payload, sort_keys=True), before)

    def test_r2_29_the_join_key_survives_for_forensics(self):
        """Masking is a rendering policy. The durable id stays in the payload and the API."""
        self.assertTrue(all(isinstance(w, str) for w in LIVE["ordered_message_ids"]))
        db = _db()
        db.add(HybridDecisionTraceRow(turn_id="join-1", message_count=1,
                                      classification="CONFLICT", payload=LIVE))
        db.commit()
        from app.routes.ops_dashboard import read_turn
        body = read_turn("join-1", db)
        self.assertEqual(body["trace"]["ordered_message_ids"],
                         LIVE["ordered_message_ids"])

    def test_r2_30_the_page_explains_the_masking(self):
        self.assertIn(view.WAMID_MASK_NOTE, _page(LIVE))


def _as_dict(obj):
    import dataclasses
    return dataclasses.asdict(obj)


def _page(payload):
    headline, conditions = svc.effective_from_payload(payload)
    return view.render_turn_trace_page({
        "captured": True, "trace": payload,
        "captured_classification": payload["badges"][0],
        "effective_classification": headline,
        "supporting_conditions": list(conditions),
        "reconciliation_rows": list(svc.row_summaries_from_payload(payload)),
        "reconciliation_counts": svc.counts_from_payload(payload)})


# ── R2-31..40: the fuzzy-vehicle repair, as behaviour ────────────────────────

class FuzzyVehicleRepair(unittest.TestCase):
    """The one customer-facing correction in the R1+R2 chain, proved rather than asserted."""

    def engine(self, *, authority=True, trace=False):
        from app.services import conversation_engine as ce
        instance = ce.ConversationEngine.__new__(ce.ConversationEngine)
        instance.settings = types.SimpleNamespace(
            reconciler_vehicle_authority_enabled=authority, hybrid_trace_enabled=trace)
        instance._turn_trace_reconciliations = []
        return instance

    def ctx_state(self):
        state = types.SimpleNamespace(current_cycle_start_message_db_id=1,
                                      current_revision_id=None)
        return (types.SimpleNamespace(thread=types.SimpleNamespace(id=1), state=state,
                                      lead=None), state)

    def expected(self, instance, state, hit):
        from app.services.field_reconciler import reconcile_vehicle_identity
        from app.services.vehicle_catalog import lookup_vehicle
        return bool(reconcile_vehicle_identity(instance._fuzzy_claim(state, hit),
                                               catalog_lookup=lookup_vehicle).accepted)

    CASES = (("Peugeot", "208"), ("Ford", "Ka"), ("Toyota", "Corolla"),
             ("Nada", "Nada"), ("", ""))

    def test_r2_31_the_deployed_path_referenced_an_unbound_name(self):
        """What shipped, reconstructed from git rather than from memory."""
        import subprocess, shutil
        if shutil.which("git") is None:                          # pragma: no cover
            self.skipTest("git is not installed in the test image; checked on the host")
        blob = subprocess.run(
            ["git", "show", "a7a6413:backend/app/services/conversation_engine.py"],
            cwd=str(ROOT), capture_output=True, text=True).stdout
        self.assertTrue(blob, "the deployed revision must be readable")
        import ast
        target = next(n for n in ast.walk(ast.parse(blob))
                      if isinstance(n, ast.FunctionDef)
                      and n.name == "_fuzzy_identity_accepted")
        bound = {a.arg for a in target.args.args}
        for node in ast.walk(target):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bound.add(node.id)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound.add((alias.asname or alias.name).split(".")[0])
        self.assertNotIn("claims", bound,
                         "the deployed revision must still show the defect")

    def test_r2_32_the_exception_was_swallowed_and_everything_was_rejected(self):
        """Reproduced by restoring the defect's shape, not by trusting the narrative."""
        instance = self.engine()
        ctx, state = self.ctx_state()
        hit = types.SimpleNamespace(marca="Peugeot", modelo="208")
        with mock.patch.object(instance, "_fuzzy_claim",
                               side_effect=NameError("name 'claims' is not defined")):
            self.assertFalse(
                instance._fuzzy_identity_accepted(ctx, state,
                                                  types.SimpleNamespace(hit=hit)),
                "a NameError at the observation point returned False unconditionally")

    def test_r2_33_the_repair_binds_exactly_the_claims_that_were_reconciled(self):
        instance = self.engine()
        ctx, state = self.ctx_state()
        hit = types.SimpleNamespace(marca="Peugeot", modelo="208")
        seen = {}
        original = instance._fuzzy_claim

        def spy(state_, hit_):
            seen["claims"] = original(state_, hit_)
            return seen["claims"]

        recorded = {}
        with mock.patch.object(instance, "_fuzzy_claim", side_effect=spy), \
             mock.patch.object(instance, "_record_reconciliation",
                               side_effect=lambda *a, **k: recorded.update(
                                   claims=a[4] if len(a) > 4 else k.get("claims"))):
            instance._fuzzy_identity_accepted(ctx, state, types.SimpleNamespace(hit=hit))
        self.assertEqual(recorded["claims"], seen["claims"])
        self.assertEqual(len(seen["claims"]), 2, "make and model")

    def test_r2_34_the_method_returns_the_reconcilers_own_decision(self):
        instance = self.engine()
        ctx, state = self.ctx_state()
        for marca, modelo in self.CASES:
            with self.subTest(vehicle=f"{marca} {modelo}"):
                hit = types.SimpleNamespace(marca=marca, modelo=modelo)
                self.assertEqual(
                    instance._fuzzy_identity_accepted(ctx, state,
                                                      types.SimpleNamespace(hit=hit)),
                    self.expected(instance, state, hit))

    def test_r2_35_no_reconciliation_rule_or_authority_changed(self):
        import subprocess, shutil
        if shutil.which("git") is None:                          # pragma: no cover
            self.skipTest("git is not installed in the test image; checked on the host")
        changed = subprocess.run(
            ["git", "diff", "--name-only", "d5cb8ae", "--"],
            cwd=str(ROOT), capture_output=True, text=True).stdout.split()
        for forbidden in ("backend/app/services/field_reconciler.py",
                          "backend/app/services/vehicle_catalog.py",
                          "backend/app/schemas/claims.py",
                          "backend/app/services/claim_projection.py",
                          "backend/app/settings.py"):
            self.assertNotIn(forbidden, changed)

    def test_r2_36_rejected_identities_remain_rejected(self):
        instance = self.engine()
        ctx, state = self.ctx_state()
        hit = types.SimpleNamespace(marca="Nada", modelo="Nada")
        self.assertFalse(self.expected(instance, state, hit))
        self.assertFalse(instance._fuzzy_identity_accepted(
            ctx, state, types.SimpleNamespace(hit=hit)))

    def test_r2_37_no_fuzzy_identity_is_acceptable_under_the_current_policy(self):
        """The measurement that corrects the R1 closeout.

        R1 reported the `NameError` as a live customer-facing defect: "every fuzzy vehicle
        identity was rejected regardless of what the reconciler decided". The first half is
        true. The second half implied the reconciler would sometimes have decided otherwise,
        and it would not have.

        `_fuzzy_claim` builds `FUZZY_SUGGESTED` claims, and `reconcile_vehicle_identity`
        deliberately omits that class from the ones that can establish a model — the
        weakest class there is "may never write canonical state". Every fuzzy-only claim
        set therefore reaches HOLD, and `_fuzzy_identity_accepted` returned False before the
        repair and returns False after it. The customer-facing outcome never differed.
        """
        from app.services.field_reconciler import reconcile_vehicle_identity
        from app.services.vehicle_catalog import lookup_vehicle
        import inspect

        instance = self.engine()
        ctx, state = self.ctx_state()
        for marca, modelo in (("Peugeot", "208"), ("Ford", "Ka"), ("Toyota", "Corolla"),
                              ("Chevrolet", "Onix"), ("Volkswagen", "Gol")):
            with self.subTest(vehicle=f"{marca} {modelo}"):
                hit = types.SimpleNamespace(marca=marca, modelo=modelo)
                decision = reconcile_vehicle_identity(instance._fuzzy_claim(state, hit),
                                                      catalog_lookup=lookup_vehicle)
                self.assertFalse(bool(decision.accepted))
                self.assertFalse(instance._fuzzy_identity_accepted(
                    ctx, state, types.SimpleNamespace(hit=hit)))

        source = inspect.getsource(reconcile_vehicle_identity)
        classes = source[source.index("stated_model = _first_value"):
                         source.index("stated_make = _first_value")]
        self.assertNotIn("FUZZY_SUGGESTED", classes,
                         "the exclusion is the reason the defect was inert; if this ever "
                         "changes, the repair stops being a no-op and R2-37b is the proof")

    def test_r2_37b_the_repair_makes_the_return_track_the_reconciler(self):
        """What the repair actually fixed, shown without touching any rule.

        Feed the same method a claim set the reconciler DOES accept — by substituting the
        claim builder, not by changing the reconciler, the catalogue or a flag — and the
        method returns True. With the unbound name in place it returned False here too,
        because it never reached its own return statement. That is the defect: the result
        stopped being a function of the decision, which would have hidden any future change
        to the evidence-class policy.
        """
        from app.schemas.claims import ClaimEvidence, ClaimType, EvidenceClass, Explicitness
        instance = self.engine()
        ctx, state = self.ctx_state()

        def deterministic_claims(_state, hit):
            return [ClaimEvidence(claim_type=t, value=v,
                                  evidence_class=EvidenceClass.DETERMINISTIC_EXTRACTED,
                                  producer="ce:test_substitute",
                                  explicitness=Explicitness.STATED).with_id()
                    for t, v in ((ClaimType.VEHICLE_MAKE, hit.marca),
                                 (ClaimType.VEHICLE_MODEL, hit.modelo))]

        hit = types.SimpleNamespace(marca="Peugeot", modelo="208")
        with mock.patch.object(instance, "_fuzzy_claim", side_effect=deterministic_claims):
            self.assertTrue(instance._fuzzy_identity_accepted(
                ctx, state, types.SimpleNamespace(hit=hit)))

    def test_r2_37c_the_defect_also_hid_the_decision_from_every_record(self):
        """The effect that WAS real: the reconciliation was never recorded at all.

        The `NameError` fired before `_record_reconciliation`, so the fuzzy admissibility
        decision reached neither the append-only justification log nor the hybrid trace.
        An operator asking why a vehicle was not accepted had nothing to read.
        """
        instance = self.engine()
        ctx, state = self.ctx_state()
        hit = types.SimpleNamespace(marca="Peugeot", modelo="208")
        with mock.patch.object(instance, "_record_reconciliation") as recorder:
            instance._fuzzy_identity_accepted(ctx, state,
                                              types.SimpleNamespace(hit=hit))
        recorder.assert_called_once()
        self.assertEqual(recorder.call_args.kwargs["decision_site_id"],
                         DecisionSite.VEHICLE_FUZZY_ADMISSIBILITY)

    def test_r2_38_a_missing_hit_is_still_rejected_without_reconciling(self):
        instance = self.engine()
        ctx, state = self.ctx_state()
        with mock.patch.object(instance, "_record_reconciliation") as recorder:
            self.assertFalse(instance._fuzzy_identity_accepted(
                ctx, state, types.SimpleNamespace(hit=None)))
        recorder.assert_not_called()

    def test_r2_39_the_observer_cannot_change_the_business_result(self):
        instance = self.engine()
        ctx, state = self.ctx_state()
        hit = types.SimpleNamespace(marca="Peugeot", modelo="208")
        truth = self.expected(instance, state, hit)
        with mock.patch.object(instance, "_record_reconciliation",
                               side_effect=RuntimeError("observer exploded")):
            self.assertFalse(instance._fuzzy_identity_accepted(
                ctx, state, types.SimpleNamespace(hit=hit)),
                "a raising observer is caught and acceptance is never assumed")
        self.assertEqual(instance._fuzzy_identity_accepted(
            ctx, state, types.SimpleNamespace(hit=hit)), truth)

    def test_r2_40_trace_on_and_off_give_the_same_decision(self):
        for marca, modelo in self.CASES:
            hit = types.SimpleNamespace(marca=marca, modelo=modelo)
            results = []
            for trace in (False, True):
                instance = self.engine(trace=trace)
                ctx, state = self.ctx_state()
                results.append(instance._fuzzy_identity_accepted(
                    ctx, state, types.SimpleNamespace(hit=hit)))
            with self.subTest(vehicle=f"{marca} {modelo}"):
                self.assertEqual(results[0], results[1])

    def test_r2_40b_authority_off_is_untouched_legacy_behaviour(self):
        instance = self.engine(authority=False)
        ctx, state = self.ctx_state()
        self.assertTrue(instance._fuzzy_identity_accepted(
            ctx, state,
            types.SimpleNamespace(hit=types.SimpleNamespace(marca="Nada", modelo="Nada"))))


# ── R2-41..50: the rest of the §11 matrix ────────────────────────────────────

class RemainingMatrix(unittest.TestCase):

    def test_r2_41_mixed_overlapping_and_non_overlapping_claims(self):
        """A shared proposition decides the row; the unshared ones do not dilute it."""
        built = row((semantic_claim(ClaimType.VEHICLE_MODEL, "208"),
                     semantic_claim(ClaimType.VEHICLE_YEAR, 2020),
                     ce_claim(ClaimType.VEHICLE_MODEL, "Peugeot 208"),
                     ce_claim(ClaimType.INSPECTION_LOCATION, "Palermo")))
        self.assertEqual(built.classification, Classification.AGREE)
        self.assertEqual([p.claim_type for p in built.compared_propositions],
                         [ClaimType.VEHICLE_MODEL])

    def test_r2_42_one_attributed_plus_one_unattributed_is_single_producer(self):
        built = row((ce_claim(ClaimType.VEHICLE_MODEL, "Peugeot 208"),
                     claim("unknown", ClaimType.VEHICLE_MODEL, "Toyota Corolla")))
        self.assertEqual(built.classification, Classification.SINGLE_PRODUCER)
        self.assertEqual(built.participating_sources, (DET,))
        self.assertNotEqual(built.classification, Classification.CONFLICT)

    def test_r2_43_two_unattributed_claims_participate_in_nothing(self):
        built = row((claim("unknown", ClaimType.VEHICLE_MODEL, "Peugeot 208"),
                     claim("", ClaimType.VEHICLE_MODEL, "Ford Ka")))
        self.assertEqual(built.classification, Classification.NO_EVIDENCE)
        self.assertEqual(built.participating_sources, ())

    def test_r2_44_a_negation_against_an_assertion_is_a_provable_conflict(self):
        built = row((semantic_claim(ClaimType.VEHICLE_MODEL, "Peugeot 208"),
                     ce_claim(ClaimType.VEHICLE_MODEL, "Peugeot 208",
                              polarity=Polarity.NEGATED)))
        self.assertEqual(built.classification, Classification.CONFLICT)
        self.assertEqual(built.compared_propositions[0].basis,
                         "one source denies what the other asserts")

    def test_r2_45_interpreter_error_is_not_a_row_conflict(self):
        built = row((ce_claim(ClaimType.VEHICLE_MODEL, "Peugeot 208"),),
                    semantic=SemanticEvidence(status="ERROR", error_category="HTTPError"))
        self.assertEqual(built.classification, Classification.SINGLE_PRODUCER)
        self.assertNotIn(built.classification, Classification.COMPARED)

    def test_r2_46_reconciler_error_is_error(self):
        built = svc.reconciliation_from(Record(), (ce_claim(ClaimType.VEHICLE_MODEL, "208"),),
                                        decision_site_id=DecisionSite.VEHICLE_IDENTITY_APPLY)
        built.error_category = "ReconcilerTimeout"
        self.assertEqual(svc.finalize_rows((built,), sem_ok())[0].classification,
                         Classification.ERROR)

    def test_r2_47_legacy_absence_is_not_a_modern_empty_field(self):
        self.assertTrue(svc.is_legacy_row({"classification": "AGREE"}))
        self.assertFalse(svc.is_legacy_row({"participating_sources": []}))
        summary = svc.row_summaries_from_payload(LIVE)[1]
        self.assertIsNone(summary["participating_sources"])
        self.assertIsNone(summary["compared_propositions"])
        self.assertEqual(summary["effective_classification"],
                         Classification.LEGACY_PROVENANCE_UNAVAILABLE)

    def test_r2_48_the_preserved_trace_is_byte_identical(self):
        svc.effective_from_payload(LIVE)
        svc.row_summaries_from_payload(LIVE)
        svc.counts_from_payload(LIVE)
        _page(LIVE)
        self.assertEqual(FIXTURE.read_bytes(), LIVE_BYTES)
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
                         hashlib.sha256(LIVE_BYTES).hexdigest())
        headline, _ = svc.effective_from_payload(LIVE)
        self.assertEqual(headline, Classification.TRACE_INCOMPLETE)
        self.assertNotEqual(headline, Classification.CONFLICT)

    def test_r2_49_no_reply_behaviour_is_untouched(self):
        from app.schemas.conversation import ACTION_NO_REPLY_PRODUCED, HANDLED_ACTIONS
        from app.schemas.hybrid_trace import CanonicalSnapshot
        self.assertIn(ACTION_NO_REPLY_PRODUCED, HANDLED_ACTIONS)
        self.assertEqual(svc.result_kind_for(ACTION_NO_REPLY_PRODUCED, CanonicalSnapshot(),
                                             CanonicalSnapshot(), "CE_AI", ()), "NO_ACTION")
        clause_source = (ROOT / "backend" / "app" / "services"
                         / "unanswered_alert.py").read_text(encoding="utf-8-sig")
        start = clause_source.index("NOT EXISTS")
        clause = clause_source[start:clause_source.index("ae.created_at", start) + 13]
        for term in ("ob.thread_id = ae.thread_id", "ob.direction = 'out'",
                     "ob.wa_message_id IS NOT NULL"):
            self.assertIn(term, clause)

    def test_r2_50_every_canonical_proposition_is_a_real_claim_type(self):
        known = {v for k, v in vars(ClaimType).items() if not k.startswith("_")}
        self.assertTrue(set(CANONICAL_PROPOSITIONS) <= known,
                        "a proposition nothing can produce is a vocabulary nobody earned")
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.3")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
