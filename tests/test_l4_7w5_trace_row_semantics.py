"""L4.7W5-TRACE-ROW-SEMANTICS — a comparison is made by producers, not by polarity.

`hybrid-decision-trace/1.0` classified every reconciliation row from `InformationState`:

    BOTH → CONFLICT, TRUE_ONLY → AGREE, FALSE_ONLY → AGREE, NEITHER → SEMANTIC_MISSING

`InformationState` folds the claims about one claim type into a four-valued POLARITY. It is
computed from values and negations. It does not know who supplied them, and it is unchanged
whether one producer spoke or five did. Reading agreement out of it produced two false
statements that reached a deployed Inspector:

* the surviving row of the first live trace read `AGREE` — "both engines agreed" — on a
  decision exactly one deterministic parser had informed;
* its other row read `SEMANTIC_MISSING` — "the interpreter said nothing" — on a call that
  received nothing from anybody, deterministic parsers included.

1.1 records per row which sources participated, what each supplied, and which decision site
asked; the classification is derived from that. Polarity stays on the row as evidence.

A third defect surfaced while auditing it, and it was not observational. The `claims`
argument added to `_record_reconciliation` in Gate 1 named a variable that does not exist in
`_fuzzy_identity_accepted`. Python raised `NameError` at the observation point, the method's
own `except Exception` swallowed it, and the method returned False for every fuzzy identity
regardless of what the reconciler had decided. ROW-30..33 pin the repair.

ROW-01..08   the row contract, one case per outcome
ROW-09..13   polarity and acceptance are not agreement
ROW-14..16   identity and counting
ROW-17..19   the preserved 1.0 trace
ROW-20..23   turn-level aggregation
ROW-24..25   API and renderer tell the same story
ROW-26       privacy
ROW-27..29   nothing outside this milestone moved
ROW-30..33   the unbound name, and the decision sites now carried

**Amended by L4.7W5-TRACE-ROW-SEMANTICS-R2 (`hybrid-decision-trace/1.2`).** Two of the
expectations below pinned behaviour R2 corrected, and both changed in the safe direction:

* a row whose only participant contradicts itself now reads `AMBIGUOUS_EVIDENCE` rather
  than `SINGLE_PRODUCER` — one producer disagreeing with itself is ambiguity of that
  producer's evidence, and `SINGLE_PRODUCER` implied usable single-source evidence;
* `ordinal` is gone from comparison identity, so the helpers no longer pass one; identity
  now comes from `logical_comparison_id`, and repeats are numbered separately.

R2's own matrix lives in `test_l4_7w5_trace_row_semantics_r2.py`.
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
from app.schemas.hybrid_trace import (CanonicalSnapshot, Classification, DECISION_PURPOSE,
                                      DecisionSite, EvidenceSource, ReconciliationEvidence,
                                      RuleEvidence, SemanticEvidence, TRACE_VERSION,
                                      source_of_producer)
from app.services import hybrid_trace as svc

FIXTURE = ROOT / "tests" / "fixtures" / "hybrid_trace_live_partial_reconciliation.json"
LIVE_BYTES = FIXTURE.read_bytes()
LIVE = json.loads(LIVE_BYTES.decode("utf-8"))

SEM = EvidenceSource.SEMANTIC
DET = EvidenceSource.DETERMINISTIC
CAN = EvidenceSource.CANONICAL_STATE


# ── builders: real claims, so nothing is asserted into place ─────────────────

def claim(producer, claim_type, value, *, polarity=Polarity.ASSERTED,
          evidence_class=EvidenceClass.DETERMINISTIC_EXTRACTED):
    return ClaimEvidence(claim_type=claim_type, value=value, polarity=polarity,
                         evidence_class=evidence_class, producer=producer,
                         explicitness=Explicitness.STATED).with_id()


class Record:
    """The reconciliation record the reconciler hands to the observer."""

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


def built(claims=(), *, record=None, site=DecisionSite.VEHICLE_IDENTITY_APPLY,
          semantic=None):
    """One row, captured and then settled exactly as the engine settles it."""
    row = svc.reconciliation_from(record or Record(), claims, decision_site_id=site)
    return svc.finalize_rows((row,), semantic or SemanticEvidence(status="ABSENT"))[0]


def sem_ok(claims=("vehicle_mentions",)):
    return SemanticEvidence(ok=True, status="OK", produced_claims=tuple(claims))


SEM_ABSENT = SemanticEvidence(status="ABSENT", produced_claims=())


def _db() -> Session:
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    return Session(engine)


# ── ROW-01..08: one case per row outcome ─────────────────────────────────────

class RowContract(unittest.TestCase):

    def test_row_01_two_compatible_sources_agree(self):
        row = built((claim("semantic:understand", ClaimType.VEHICLE_MODEL, "208",
                           evidence_class=EvidenceClass.SEMANTIC_INFERRED),
                     claim("ce:catalog", ClaimType.VEHICLE_MODEL, "208")))
        self.assertEqual(row.classification, Classification.AGREE)
        self.assertEqual(sorted(row.participating_sources), sorted([SEM, DET]))
        self.assertEqual(row.semantic_input, "PRESENT")
        self.assertEqual(row.ce_input, "PRESENT")

    def test_row_02_two_incompatible_sources_conflict(self):
        row = built((claim("semantic:understand", ClaimType.VEHICLE_MODEL, "208",
                           evidence_class=EvidenceClass.SEMANTIC_INFERRED),
                     claim("ce:catalog", ClaimType.VEHICLE_MODEL, "Ka")))
        self.assertEqual(row.classification, Classification.CONFLICT)
        self.assertEqual(len(set(row.participating_sources)), 2)

    def test_row_03_semantic_only_is_single_producer(self):
        row = built((claim("semantic:understand", ClaimType.VEHICLE_MODEL, "208",
                           evidence_class=EvidenceClass.SEMANTIC_INFERRED),))
        self.assertEqual(row.classification, Classification.SINGLE_PRODUCER)
        self.assertEqual(row.participating_sources, (SEM,))
        self.assertEqual(row.ce_input, "ABSENT")

    def test_row_04_deterministic_only_is_single_producer(self):
        row = built((claim("ce:zone", ClaimType.INSPECTION_LOCATION, "Palermo"),))
        self.assertEqual(row.classification, Classification.SINGLE_PRODUCER)
        self.assertEqual(row.participating_sources, (DET,))
        self.assertEqual(row.semantic_input, "ABSENT")

    def test_row_05_canonical_only_is_single_producer(self):
        row = built((claim("canonical:deterministic_acceptance",
                           ClaimType.QUOTE_ACCEPTED, True),))
        self.assertEqual(row.classification, Classification.SINGLE_PRODUCER)
        self.assertEqual(row.participating_sources, (CAN,))
        self.assertEqual(row.ce_input, "PRESENT",
                         "the 1.0 compatibility field groups canonical with the CE side")

    def test_row_06_no_claims_is_no_evidence(self):
        row = built(())
        self.assertEqual(row.classification, Classification.NO_EVIDENCE)
        self.assertEqual(row.participating_sources, ())
        self.assertEqual(row.not_routed_sources, ())

    def test_row_07_evidence_elsewhere_in_the_turn_is_not_routed(self):
        row = built((), semantic=sem_ok())
        self.assertEqual(row.classification, Classification.NOT_ROUTED)
        self.assertEqual(row.not_routed_sources, (SEM,))

    def test_row_07b_a_single_producer_row_still_names_the_unrouted_source(self):
        """Participation and routing are different facts and are reported separately."""
        row = built((claim("ce:zone", ClaimType.INSPECTION_LOCATION, "Palermo"),),
                    semantic=sem_ok())
        self.assertEqual(row.classification, Classification.SINGLE_PRODUCER)
        self.assertEqual(row.not_routed_sources, (SEM,))

    def test_row_08_a_producer_error_is_error(self):
        row = svc.reconciliation_from(Record(), (), decision_site_id="x")
        row.error_category = "ProducerTimeout"
        settled = svc.finalize_rows((row,), sem_ok())[0]
        self.assertEqual(settled.classification, Classification.ERROR)

    def test_row_08b_an_unattributed_claim_never_makes_a_participant(self):
        row = built((claim("unknown", ClaimType.VEHICLE_MODEL, "208"),
                     claim("", ClaimType.VEHICLE_MODEL, "Ka")))
        self.assertEqual(row.classification, Classification.NO_EVIDENCE)
        self.assertEqual(row.participating_sources, ())
        self.assertIn(EvidenceSource.UNATTRIBUTED,
                      [c.source for c in row.source_evidence])

    def test_row_08c_fuzzy_is_deterministic_because_ce_produces_it(self):
        """`FUZZY_SUGGESTED` used to be read as semantic participation. CE makes it."""
        self.assertEqual(source_of_producer("ce:fuzzy_lookup_vehicle"), DET)
        row = built((claim("ce:fuzzy_lookup_vehicle", ClaimType.VEHICLE_MODEL, "208",
                           evidence_class=EvidenceClass.FUZZY_SUGGESTED),))
        self.assertEqual(row.semantic_input, "ABSENT")
        self.assertEqual(row.participating_sources, (DET,))


# ── ROW-09..13: polarity and acceptance are not agreement ────────────────────

class PolarityIsNotAgreement(unittest.TestCase):

    def test_row_09_true_only_with_one_producer_is_not_agree(self):
        row = built((claim("ce:catalog", ClaimType.VEHICLE_MODEL, "208"),),
                    record=Record(information_state="TRUE_ONLY"))
        self.assertEqual(row.information_state, "TRUE_ONLY")
        self.assertNotEqual(row.classification, Classification.AGREE)
        self.assertEqual(row.classification, Classification.SINGLE_PRODUCER)

    def test_row_10_false_only_with_one_producer_is_not_agree(self):
        row = built((claim("ce:catalog", ClaimType.VEHICLE_MODEL, "208",
                           polarity=Polarity.NEGATED),),
                    record=Record(information_state="FALSE_ONLY"))
        self.assertEqual(row.information_state, "FALSE_ONLY")
        self.assertNotEqual(row.classification, Classification.AGREE)

    def test_row_11_neither_is_not_blamed_on_semantic(self):
        row = built((), record=Record(information_state="NEITHER"))
        self.assertEqual(row.information_state, "NEITHER")
        self.assertEqual(row.classification, Classification.NO_EVIDENCE)
        self.assertNotIn("SEMANTIC", row.classification)

    def test_row_11b_both_with_one_producer_is_not_a_conflict(self):
        """One producer asserting two values contradicts itself, not another engine.

        R2 refined the label from `SINGLE_PRODUCER` to `AMBIGUOUS_EVIDENCE`: the producer
        did speak, but what it produced cannot be used as one source's position. The
        invariant this test exists for is unchanged — it is never `CONFLICT`.
        """
        row = built((claim("ce:catalog", ClaimType.VEHICLE_MODEL, "208"),
                     claim("ce:catalog", ClaimType.VEHICLE_MODEL, "Ka")),
                    record=Record(information_state="BOTH"))
        self.assertEqual(row.information_state, "BOTH")
        self.assertEqual(row.classification, Classification.AMBIGUOUS_EVIDENCE)
        self.assertEqual(row.self_contradiction_sources, (DET,))
        self.assertNotEqual(row.classification, Classification.CONFLICT)

    def test_row_12_one_producer_plus_accept_is_still_not_agreement(self):
        row = built((claim("ce:catalog", ClaimType.VEHICLE_MODEL, "208"),),
                    record=Record(outcome="ACCEPT", information_state="TRUE_ONLY"))
        self.assertEqual(row.outcome, "ACCEPT")
        self.assertEqual(row.classification, Classification.SINGLE_PRODUCER)

    def test_row_13_a_deterministic_floor_is_not_agreement(self):
        fired = (RuleEvidence("scheduling.earliest_option_rejected", "1.0", True),)
        trace = svc.build_trace(
            turn_id="floor-1", thread_id=1, lead_id=None, deployment_sha="d",
            started_at="s", completed_at="c", duration_ms=1,
            ordered_message_ids=("w",), message_timestamps=("t",), burst_texts=["x"],
            semantic=SemanticEvidence(status="PENDING"), ce_rules=fired,
            reconciliations=(), before=CanonicalSnapshot(needs_human=False),
            after=CanonicalSnapshot(needs_human=True), action="skipped_human",
            detail=None, answer_source=None, outbound={})
        self.assertNotIn("AGREE", trace.badges)
        self.assertNotIn("CONFLICT", trace.badges)
        self.assertIn("DETERMINISTIC FLOOR", trace.badges)


# ── ROW-14..16: identity and counting ────────────────────────────────────────

class IdentityAndCounting(unittest.TestCase):

    def two_vehicle_model_rows(self):
        apply_row = svc.reconciliation_from(
            Record(), (claim("ce:catalog", ClaimType.VEHICLE_MODEL, "208"),),
            decision_site_id=DecisionSite.VEHICLE_IDENTITY_APPLY)
        fuzzy_row = svc.reconciliation_from(
            Record(outcome="HOLD"),
            (claim("ce:fuzzy_lookup_vehicle", ClaimType.VEHICLE_MODEL, "208",
                   evidence_class=EvidenceClass.FUZZY_SUGGESTED),),
            decision_site_id=DecisionSite.VEHICLE_FUZZY_ADMISSIBILITY)
        return svc.finalize_rows((apply_row, fuzzy_row), SEM_ABSENT)

    def test_row_14_two_same_family_rows_stay_individually_identifiable(self):
        first, second = self.two_vehicle_model_rows()
        self.assertEqual(first.claim_family, second.claim_family)
        self.assertNotEqual(first.decision_site_id, second.decision_site_id)
        self.assertNotEqual(first.logical_comparison_id, second.logical_comparison_id)
        self.assertEqual(first.decision_purpose,
                         DECISION_PURPOSE[DecisionSite.VEHICLE_IDENTITY_APPLY])
        self.assertEqual(second.decision_purpose,
                         DECISION_PURPOSE[DecisionSite.VEHICLE_FUZZY_ADMISSIBILITY])

    def test_row_14b_identity_survives_a_rename_and_never_reads_the_stack(self):
        source = (ROOT / "backend" / "app" / "services"
                  / "hybrid_trace.py").read_text(encoding="utf-8-sig")
        engine = (ROOT / "backend" / "app" / "services"
                  / "conversation_engine.py").read_text(encoding="utf-8-sig")
        for forbidden in ("inspect.stack", "sys._getframe", "traceback.extract",
                          "inspect.currentframe"):
            self.assertNotIn(forbidden, source)
            self.assertNotIn(forbidden, engine)
        for site in DecisionSite.ALL:
            self.assertIn(f'"{site}"',
                          (ROOT / "backend" / "app" / "schemas"
                           / "hybrid_trace.py").read_text(encoding="utf-8-sig"))

    def test_row_15_distinct_family_counts_deduplicate(self):
        counts = svc.family_counts(self.two_vehicle_model_rows())
        self.assertEqual(counts["distinct_families"], 1)

    def test_row_16_comparison_row_counts_are_row_counts(self):
        counts = svc.family_counts(self.two_vehicle_model_rows())
        self.assertEqual(counts["comparison_rows"], 2)
        self.assertEqual(counts["single_producer"], 2)
        self.assertEqual(counts["compared"], 0)
        self.assertEqual(counts["agreed"], 0)


# ── ROW-17..19: the preserved 1.0 trace ──────────────────────────────────────

class PreservedLegacyTrace(unittest.TestCase):

    def test_row_17_the_fixture_is_byte_identical(self):
        self.assertEqual(FIXTURE.read_bytes(), LIVE_BYTES)
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
                         hashlib.sha256(LIVE_BYTES).hexdigest())
        svc.effective_from_payload(LIVE)
        svc.row_summaries_from_payload(LIVE)
        svc.counts_from_payload(LIVE)
        self.assertEqual(FIXTURE.read_bytes(), LIVE_BYTES, "a read modified the fixture")

    def test_row_18_a_legacy_agree_is_not_presented_as_proven_agreement(self):
        second = svc.row_summaries_from_payload(LIVE)[1]
        self.assertEqual(second["captured_classification"], "AGREE")
        self.assertEqual(second["effective_classification"],
                         Classification.LEGACY_PROVENANCE_UNAVAILABLE)
        self.assertTrue(second["legacy"])
        self.assertIsNone(second["participating_sources"],
                          "an absent 1.0 field is not an empty 1.1 field")
        self.assertIsNone(second["source_evidence"])

    def test_row_19_a_legacy_absence_is_not_blamed_on_semantic(self):
        first = svc.row_summaries_from_payload(LIVE)[0]
        self.assertEqual(first["captured_classification"], "SEMANTIC_MISSING")
        self.assertIn(first["effective_classification"],
                      (Classification.NOT_ROUTED, Classification.NO_EVIDENCE))
        self.assertNotEqual(first["effective_classification"],
                            Classification.SEMANTIC_MISSING)

    def test_row_19b_the_preserved_turn_no_longer_displays_conflict(self):
        headline, _ = svc.effective_from_payload(LIVE)
        self.assertNotEqual(headline, Classification.CONFLICT)
        self.assertEqual(headline, Classification.TRACE_INCOMPLETE)
        self.assertEqual(LIVE["badges"][0], "CONFLICT",
                         "the captured record keeps the claim it made")

    def test_row_19c_a_1_0_row_is_detected_by_the_missing_key_not_an_empty_value(self):
        self.assertTrue(svc.is_legacy_row({"classification": "AGREE"}))
        self.assertFalse(svc.is_legacy_row({"participating_sources": []}))
        self.assertFalse(svc.is_legacy_row({"participating_sources": ["SEMANTIC"]}))


# ── ROW-20..23: turn-level aggregation ───────────────────────────────────────

class TurnAggregation(unittest.TestCase):

    def head(self, rows, semantic=SEM_ABSENT):
        return svc.classify(semantic, (), svc.finalize_rows(rows, semantic))

    def agree_row(self, ordinal=0):
        return svc.reconciliation_from(
            Record(), (claim("semantic:understand", ClaimType.VEHICLE_MODEL, "Peugeot 208",
                             evidence_class=EvidenceClass.SEMANTIC_INFERRED),
                       claim("ce:catalog", ClaimType.VEHICLE_MODEL, "Peugeot 208")),
            decision_site_id=DecisionSite.VEHICLE_IDENTITY_APPLY)

    def conflict_row(self, ordinal=0):
        return svc.reconciliation_from(
            Record(), (claim("semantic:understand", ClaimType.VEHICLE_MODEL, "Peugeot 208",
                             evidence_class=EvidenceClass.SEMANTIC_INFERRED),
                       claim("ce:catalog", ClaimType.VEHICLE_MODEL, "Ford Ka")),
            decision_site_id=DecisionSite.VEHICLE_IDENTITY_APPLY)

    def single_row(self, ordinal=0):
        return svc.reconciliation_from(
            Record(), (claim("ce:zone", ClaimType.INSPECTION_LOCATION, "Palermo"),),
            decision_site_id=DecisionSite.LOCATION_INSPECTION_APPLY)

    def empty_row(self, ordinal=0):
        return svc.reconciliation_from(Record(), (),
                                       decision_site_id=DecisionSite.VEHICLE_IDENTITY_APPLY)

    def test_row_20_a_real_modern_conflict_still_reads_conflict(self):
        self.assertEqual(self.head((self.agree_row(0), self.conflict_row(1))),
                         Classification.CONFLICT)

    def test_row_21_all_agree_reads_agree(self):
        self.assertEqual(self.head((self.agree_row(0), self.agree_row(1))),
                         Classification.AGREE)

    def test_row_22_agreement_beside_a_single_source_is_partial(self):
        self.assertEqual(self.head((self.agree_row(0), self.single_row(1))),
                         Classification.PARTIAL_RECONCILIATION)

    def test_row_22b_agreement_beside_an_empty_row_is_partial(self):
        self.assertEqual(self.head((self.agree_row(0), self.empty_row(1))),
                         Classification.PARTIAL_RECONCILIATION)

    def test_row_23_no_usable_evidence_anywhere_is_no_comparison(self):
        self.assertEqual(self.head((self.empty_row(0), self.empty_row(1))),
                         Classification.NO_COMPARISON)

    def test_row_23b_only_single_source_rows_is_a_single_source_decision(self):
        self.assertEqual(self.head((self.single_row(0), self.single_row(1))),
                         Classification.SINGLE_SOURCE_DECISION)

    def test_row_23c_one_producer_can_never_produce_agree_at_turn_level(self):
        for rows in ((self.single_row(0),),
                     (self.single_row(0), self.single_row(1)),
                     (self.single_row(0), self.empty_row(1))):
            with self.subTest(n=len(rows)):
                self.assertNotEqual(self.head(rows), Classification.AGREE)

    def test_row_23d_zero_producers_can_never_produce_agree_or_conflict(self):
        for semantic in (SEM_ABSENT, sem_ok()):
            with self.subTest(semantic=semantic.status):
                headline = self.head((self.empty_row(0),), semantic)
                self.assertNotIn(headline, Classification.COMPARED)


# ── ROW-24..25: one story, two surfaces ──────────────────────────────────────

class ApiAndRenderer(unittest.TestCase):

    KEY = "trace-row-semantics-test-signing-key"

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("DATABASE_URL", "sqlite://")
        import sqlalchemy
        from fastapi.testclient import TestClient
        from app.auth import SESSION_COOKIE, build_session, sign_session
        from app.main import app
        cls.TURN = "row-semantics-0001"
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=sqlalchemy.pool.StaticPool)
        models.Base.metadata.create_all(engine)
        cls.session = Session(engine)
        cls.app = app
        cls.TestClient = TestClient
        cls.SESSION_COOKIE = SESSION_COOKIE
        cls._build_session = staticmethod(build_session)
        cls._sign_session = staticmethod(sign_session)
        cls.anon = TestClient(app, follow_redirects=False)

    def _install_override(self):
        import app.db as app_db
        keys = {app_db.get_db}
        for route in self.app.routes:
            if getattr(route, "path", "") in ("/api/ops/turns", "/api/ops/turn/{turn_id}",
                                              "/control/turn/{turn_id}"):
                keys.update(d.call for d in route.dependant.dependencies
                            if getattr(d.call, "__name__", "").endswith("get_db")
                            or getattr(d.call, "__name__", "") == "_get_db_gen")
        for key in keys:
            self.app.dependency_overrides[key] = lambda: self.session
        patcher = mock.patch.object(app_db, "SessionLocal", lambda: self.session)
        patcher.start()
        self.addCleanup(patcher.stop)

    def setUp(self):
        self._install_override()
        HybridDecisionTraceRow.__table__.create(self.session.get_bind(), checkfirst=True)
        self.session.query(HybridDecisionTraceRow).delete()
        self.session.add(HybridDecisionTraceRow(
            turn_id=self.TURN, thread_id=9, message_count=1, classification="CONFLICT",
            result_kind="FALLBACK", semantic_status="OK", payload=LIVE))
        self.session.commit()

    @classmethod
    def tearDownClass(cls):
        from app.db import get_db
        cls.app.dependency_overrides.pop(get_db, None)

    @contextlib.contextmanager
    def authed(self):
        with mock.patch.dict(os.environ,
                             {"AUTH_SECRET_KEY": self.KEY, "SECRET_KEY": self.KEY}):
            cookie = self._sign_session(self._build_session("operator@example.com"))
            yield self.TestClient(self.app, follow_redirects=False,
                                  cookies={self.SESSION_COOKIE: cookie})

    def test_row_24_the_page_and_the_api_agree_row_by_row(self):
        with self.authed() as client:
            body = client.get(f"/api/ops/turn/{self.TURN}").json()
            page = client.get(f"/control/turn/{self.TURN}").text
        self.assertEqual(len(body["reconciliation_rows"]), 2)
        for row in body["reconciliation_rows"]:
            label = str(row["effective_classification"]).replace("_", " ")
            self.assertIn(label, page,
                          "the renderer must show the API's effective label, not its own")
        self.assertIn(str(body["reconciliation_counts"]["comparison_rows"]), page)
        self.assertIn(body["effective_classification"].replace("_", " "), page)

    def test_row_24b_the_dashboard_row_carries_the_same_effective_value(self):
        with self.authed() as client:
            listing = client.get("/api/ops/turns").json()["turns"][0]
            detail = client.get(f"/api/ops/turn/{self.TURN}").json()
        self.assertEqual(listing["effective_classification"],
                         detail["effective_classification"])
        self.assertEqual(listing["captured_classification"],
                         detail["captured_classification"])

    def test_row_24c_the_counts_are_not_families_printed_as_rows(self):
        with self.authed() as client:
            counts = client.get(f"/api/ops/turn/{self.TURN}").json()["reconciliation_counts"]
        self.assertEqual(counts["comparison_rows"], 2)
        self.assertEqual(counts["distinct_families"], 1,
                         "both preserved rows are vehicle.model")
        self.assertNotEqual(counts["comparison_rows"], counts["distinct_families"])

    def test_row_25_captured_and_effective_stay_separately_available(self):
        with self.authed() as client:
            body = client.get(f"/api/ops/turn/{self.TURN}").json()
        self.assertEqual(body["captured_classification"], "CONFLICT")
        self.assertEqual(body["effective_classification"], Classification.TRACE_INCOMPLETE)
        self.assertEqual(body["classification"], body["captured_classification"])
        self.assertTrue(body["reclassified"])
        self.assertEqual(body["contract_version"], LIVE.get("trace_version"))
        for row in body["reconciliation_rows"]:
            self.assertIn("captured_classification", row)
            self.assertIn("effective_classification", row)

    def test_row_25b_the_stored_row_is_untouched_by_reading_it(self):
        before = json.dumps(self.session.query(HybridDecisionTraceRow).one().payload,
                            sort_keys=True)
        with self.authed() as client:
            client.get(f"/api/ops/turn/{self.TURN}")
            client.get(f"/control/turn/{self.TURN}")
        row = self.session.query(HybridDecisionTraceRow).one()
        self.assertEqual(json.dumps(row.payload, sort_keys=True), before)
        self.assertEqual(row.classification, "CONFLICT")

    def test_row_28_anonymous_trace_access_is_still_denied(self):
        self.assertEqual(self.anon.get("/api/ops/turns").status_code, 401)
        self.assertEqual(self.anon.get(f"/api/ops/turn/{self.TURN}").status_code, 401)
        self.assertEqual(self.anon.get(f"/control/turn/{self.TURN}").status_code, 303)


# ── ROW-26: privacy ──────────────────────────────────────────────────────────

class PrivacyOfTheNewFields(unittest.TestCase):

    def row_with(self, value, claim_type=ClaimType.VEHICLE_MODEL):
        return built((claim("ce:parser", claim_type, value),))

    def test_row_26_customer_shaped_values_never_reach_a_new_field(self):
        # Every value here is a SHAPE, chosen so the repository never has to contain a
        # plausible phone number or a WAMID to prove that neither can be printed. A WAMID
        # base64-encodes the sender's number, so writing a realistic one to assert its
        # absence would put the thing in the repository to say it is not there.
        #
        # R2 note: these now arrive inside families the owner AUTHORIZED for display, which
        # is the point. The family allowlist can no longer carry the refusal on its own;
        # the shape must, and that is what this asserts.
        cases = [
            (ClaimType.INSPECTION_LOCATION, "0000000"),           # a seven-digit run
            (ClaimType.INSPECTION_LOCATION, "cliente@example.com"),
            (ClaimType.INSPECTION_LOCATION, "https://example.com/aviso"),
            (ClaimType.INSPECTION_LOCATION, "Av. Santa Fe 1234, piso 3, depto B, Palermo, "
                                            "Ciudad de Buenos Aires"),
            (ClaimType.VEHICLE_MODEL, "wamid.SANITISED-SHAPE-ONLY"),
            (ClaimType.VEHICLE_MODEL, "bk_tok_DO_NOT_LEAK_0001"),
            (ClaimType.VEHICLE_MODEL, "sk-ABCDEFGHIJKLMNOP"),
            (ClaimType.SERVICE_INTENT, "-----BEGIN PRIVATE KEY-----"),
        ]
        for claim_type, value in cases:
            with self.subTest(value=value[:18]):
                row = self.row_with(value, claim_type)
                blob = json.dumps(_as_dict(row), ensure_ascii=False)
                self.assertNotIn(value, blob)
                self.assertTrue(row.source_evidence[0].withheld)
                self.assertEqual(row.source_evidence[0].values, (None,))
                self.assertTrue(row.source_evidence[0].value_keys[0])

    def test_row_26b_an_allowlisted_locality_is_shown(self):
        row = self.row_with("Palermo", ClaimType.INSPECTION_LOCATION)
        self.assertEqual(row.source_evidence[0].values, ("Palermo",))
        self.assertFalse(row.source_evidence[0].withheld)

    def test_row_26c_vehicle_identity_is_now_authorized_for_display(self):
        """Owner decision of 2026-09-22: the Inspector is an authenticated audit surface,
        and an operator cannot read a vehicle decision without seeing the vehicle."""
        for claim_type, value, shown in (
                (ClaimType.VEHICLE_MAKE, "Peugeot", "Peugeot"),
                (ClaimType.VEHICLE_MODEL, "Peugeot 208", "Peugeot 208"),
                (ClaimType.VEHICLE_YEAR, 2020, "2020"),
                (ClaimType.VEHICLE_CATEGORY, "AUTO", "AUTO"),
                (ClaimType.SERVICE_INTENT, "revision_pre_compra", "revision_pre_compra"),
                (ClaimType.INSPECTION_LOCATION, "Palermo", "Palermo")):
            with self.subTest(claim_type=claim_type):
                row = self.row_with(value, claim_type)
                self.assertEqual(row.source_evidence[0].values, (shown,))
                self.assertFalse(row.source_evidence[0].withheld)

    def test_row_26d_two_values_stay_distinguishable_while_withheld(self):
        a = self.row_with("Peugeot 208").source_evidence[0].value_keys[0]
        b = self.row_with("Ford Ka").source_evidence[0].value_keys[0]
        c = self.row_with("Peugeot 208").source_evidence[0].value_keys[0]
        self.assertNotEqual(a, b)
        self.assertEqual(a, c)
        self.assertNotIn("208", a)

    def test_row_26e_the_rendered_page_leaks_nothing_new(self):
        from app.ui.hybrid_trace_view import render_turn_trace_page
        headline, conditions = svc.effective_from_payload(LIVE)
        page = render_turn_trace_page({
            "captured": True, "trace": LIVE, "effective_classification": headline,
            "supporting_conditions": list(conditions),
            "reconciliation_rows": list(svc.row_summaries_from_payload(LIVE)),
            "reconciliation_counts": svc.counts_from_payload(LIVE)})
        self.assertEqual(re.findall(r"\b549\d{8,12}\b", page), [])
        self.assertEqual([w for w in re.findall(r"wamid\.[A-Za-z0-9+/=]{12,}", page)
                          if not w.startswith("wamid.SANITISED")], [])
        self.assertNotIn("chain_of_thought", page)
        self.assertNotIn("bk_tok_", page)


def _as_dict(row):
    import dataclasses
    return dataclasses.asdict(row)


# ── ROW-27, 29: nothing outside this milestone moved ─────────────────────────

class NothingElseMoved(unittest.TestCase):

    def test_row_27_the_no_reply_outcome_is_intact(self):
        from app.schemas.conversation import ACTION_NO_REPLY_PRODUCED, HANDLED_ACTIONS
        from app.services.hybrid_trace import ACTION_NO_REPLY_PRODUCED as TRACE_ACTION
        self.assertEqual(ACTION_NO_REPLY_PRODUCED, "no_reply_produced")
        self.assertEqual(TRACE_ACTION, ACTION_NO_REPLY_PRODUCED)
        self.assertIn(ACTION_NO_REPLY_PRODUCED, HANDLED_ACTIONS)
        self.assertEqual(
            svc.result_kind_for(ACTION_NO_REPLY_PRODUCED, CanonicalSnapshot(),
                                CanonicalSnapshot(), "CE_AI", ()),
            "NO_ACTION")

    def test_row_27b_the_successor_suppression_clause_is_intact(self):
        source = (ROOT / "backend" / "app" / "services"
                  / "unanswered_alert.py").read_text(encoding="utf-8-sig")
        start = source.index("NOT EXISTS")
        clause = source[start:source.index("ae.created_at", start) + len("ae.created_at")]
        for term in ("ob.thread_id = ae.thread_id", "ob.direction = 'out'",
                     "ob.wa_message_id IS NOT NULL", "ob.timestamp > ae.created_at"):
            self.assertIn(term, clause)
        for forbidden in ("blocked", "failed", "pending", "wa_id"):
            self.assertNotIn(forbidden, clause)

    def test_row_29_no_forbidden_module_was_touched(self):
        """This milestone may not change what the system decides, only what it records."""
        import shutil
        import subprocess
        if shutil.which("git") is None:                     # pragma: no cover
            self.skipTest("git is not installed in the test image; "
                          "the same check runs on the host and is reported in the closeout")
        changed = subprocess.run(
            ["git", "diff", "--name-only", "d5cb8ae", "--"],
            cwd=str(ROOT), capture_output=True, text=True).stdout.split()
        forbidden = (
            "semantic_interpreter.py", "turn_evidence.py", "claims.py",
            "claim_projection.py", "field_reconciler.py", "shadow_reconciler.py",
            "scheduling_lexicon.py", "acceptance_lexicon.py", "pricing.py",
            "schedule.py", "booking_flow_service.py", "flow_data_exchange.py",
            "outbound_safety_gate.py", "outbound_guard.py", "whatsapp.py",
            "models.py", "settings.py", "main.py",
        )
        touched = [p for p in changed
                   if pathlib.Path(p).name in forbidden
                   or p.startswith("backend/migrations/")
                   or p.startswith("meta/")
                   or p.endswith("docker-compose.beta.yml")]
        self.assertEqual(touched, [], f"forbidden paths changed: {touched}")

    def test_row_29b_no_migration_and_no_schema_change(self):
        migrations = sorted((ROOT / "backend" / "migrations" / "versions").glob("*.py"))
        self.assertEqual(len(migrations), 45)
        source = (ROOT / "backend" / "app" / "services"
                  / "hybrid_trace.py").read_text(encoding="utf-8-sig")
        for forbidden in ("UPDATE hybrid_decision_traces", "update(HybridDecisionTraceRow",
                          "def backfill", "ALTER TABLE"):
            self.assertNotIn(forbidden, source)


# ── ROW-30..33: the unbound name, and the sites now carried ──────────────────

class FuzzyAdmissibilityRepair(unittest.TestCase):
    """The observational argument that changed a customer-facing outcome."""

    def engine(self, authority=True):
        from app.services import conversation_engine as ce
        instance = ce.ConversationEngine.__new__(ce.ConversationEngine)
        instance.settings = types.SimpleNamespace(
            reconciler_vehicle_authority_enabled=authority, hybrid_trace_enabled=False)
        instance._turn_trace_reconciliations = []
        return instance

    def ctx_state(self):
        state = types.SimpleNamespace(current_cycle_start_message_db_id=1,
                                      current_revision_id=None)
        ctx = types.SimpleNamespace(thread=types.SimpleNamespace(id=1), state=state,
                                    lead=None)
        return ctx, state

    def test_row_30_the_scope_no_longer_references_an_unbound_name(self):
        import ast
        source = (ROOT / "backend" / "app" / "services"
                  / "conversation_engine.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        target = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef)
                      and n.name == "_fuzzy_identity_accepted")
        bound = {a.arg for a in target.args.args}
        for node in ast.walk(target):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bound.add(node.id)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound.add((alias.asname or alias.name).split(".")[0])
        self.assertIn("claims", bound,
                      "`claims` must be bound in this scope before it is passed")

    def test_row_31_the_reconcilers_decision_is_what_is_returned(self):
        """Before the repair this returned False for every input, decision or not."""
        from app.services import conversation_engine as ce
        from app.services.field_reconciler import reconcile_vehicle_identity
        from app.services.vehicle_catalog import lookup_vehicle
        instance = self.engine()
        ctx, state = self.ctx_state()
        for marca, modelo in (("Peugeot", "208"), ("Ford", "Ka"), ("Nada", "Nada")):
            with self.subTest(vehicle=f"{marca} {modelo}"):
                hit = types.SimpleNamespace(marca=marca, modelo=modelo)
                result = types.SimpleNamespace(hit=hit)
                expected = bool(reconcile_vehicle_identity(
                    instance._fuzzy_claim(state, hit),
                    catalog_lookup=lookup_vehicle).accepted)
                self.assertEqual(instance._fuzzy_identity_accepted(ctx, state, result),
                                 expected)

    def test_row_32_every_reconciliation_call_site_names_its_decision(self):
        import ast
        source = (ROOT / "backend" / "app" / "services"
                  / "conversation_engine.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "_record_reconciliation"]
        self.assertEqual(len(calls), 3)
        sites = []
        for call in calls:
            names = [k.arg for k in call.keywords]
            self.assertIn("decision_site_id", names,
                          "a reconciliation the Inspector cannot name is one it cannot read")
            value = next(k.value for k in call.keywords if k.arg == "decision_site_id")
            self.assertIsInstance(value, ast.Attribute)
            sites.append(value.attr)
        self.assertEqual(sorted(sites),
                         sorted(["VEHICLE_IDENTITY_APPLY", "LOCATION_INSPECTION_APPLY",
                                 "VEHICLE_FUZZY_ADMISSIBILITY"]))

    def test_row_33_the_observer_still_cannot_break_the_turn(self):
        """A trace failure must stay invisible to the customer decision."""
        instance = self.engine()
        ctx, state = self.ctx_state()
        with mock.patch.object(instance, "_record_reconciliation",
                               side_effect=RuntimeError("observer exploded")):
            hit = types.SimpleNamespace(marca="Peugeot", modelo="208")
            result = types.SimpleNamespace(hit=hit)
            self.assertFalse(instance._fuzzy_identity_accepted(ctx, state, result),
                             "a raising observer is caught, and acceptance is never assumed")

    def test_row_33b_the_contract_version_is_current(self):
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.2")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
