"""L4.7C.3A — acceptance authorization, in shadow.

The highest-risk transition in the product gets a predicate before it gets authority. These
tests are the false-progression suite: each one is a way a customer could be treated as
having agreed when they did not.

Every prerequisite is positively proven; every blocker blocks when present and proves
nothing when absent. Confidence appears in no branch.

AUTH-01 ACCEPT + current delivered quote → ALLOW      AUTH-11 prior-cycle acceptance blocked
AUTH-02 ACCEPT without a quote → DENY                 AUTH-12 computed-but-undelivered blocked
AUTH-03 FUTURE_INTENT → no progression                AUTH-13 confidence cannot change it
AUTH-04 HESITATE → no progression                     AUTH-14 quote_request never accepts
AUTH-05 QUESTION_ONLY → no progression                AUTH-15 courtesy never accepts
AUTH-06 conditional acceptance → no progression       AUTH-16 valid acceptance coverage
AUTH-07 future acceptance → no progression            AUTH-17 CE state unchanged
AUTH-08 stale candidate → no progression              AUTH-18 no canonical acceptance write
AUTH-09 stale location → no progression               AUTH-19 no scheduling transition
AUTH-10 recomputed quote → prior acceptance invalid   AUTH-20 no outbound effect
"""
from __future__ import annotations

import ast
import pathlib
import sys
import types
import unittest
from types import SimpleNamespace

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
    Modality,
    Polarity,
    Temporality,
)
from app.schemas.turn_evidence import (  # noqa: E402
    AcceptanceEvidence,
    AcceptanceSignal,
    EvidenceStatus,
    TurnEvidence,
)
from app.services.acceptance_authorizer import (  # noqa: E402
    ALLOW,
    CLARIFY,
    DENY,
    HOLD,
    CommercialState,
    authorize_quote_acceptance,
    reconcile_stance,
    to_shadow_record,
)
from app.services.claim_projection import claims_from_turn_evidence  # noqa: E402

CYCLE = "cycle-7"


def accept_claim(*, temporality=Temporality.PRESENT, modality=Modality.FACTUAL,
                 evidence_class=EvidenceClass.SEMANTIC_INFERRED,
                 explicitness=Explicitness.STATED, cycle_id=CYCLE, confidence=None,
                 polarity=Polarity.ASSERTED):
    return ClaimEvidence(claim_type=ClaimType.QUOTE_ACCEPTED, value=True, polarity=polarity,
                         status=EvidenceStatus.CONFIRMED, evidence_class=evidence_class,
                         explicitness=explicitness, temporality=temporality,
                         modality=modality, confidence=confidence,
                         cycle_id=cycle_id).with_id()


def other_claim(claim_type, value=True, cycle_id=CYCLE):
    return ClaimEvidence(claim_type=claim_type, value=value,
                         status=EvidenceStatus.CONFIRMED,
                         evidence_class=EvidenceClass.SEMANTIC_INFERRED,
                         explicitness=Explicitness.STATED,
                         cycle_id=cycle_id).with_id()


def good_state(**kw):
    """A quote that exists, was delivered, and whose inputs have not moved."""
    base = dict(cycle_id=CYCLE, revision_id=5, candidate_id=11, quote_total=95000,
                quote_tipo_vehiculo="AUTO", quote_zone_group="Sur",
                quote_zone_detail="Berazategui", quote_candidate_id=11,
                quote_cycle_id=CYCLE, current_tipo_vehiculo="AUTO",
                current_zone_group="Sur", current_zone_detail="Berazategui",
                delivered_amounts=(95000,), quote_delivered=True,
                lead_flag="PRESUPUESTO_ENVIADO", stage="QUOTED")
    base.update(kw)
    return CommercialState(**base)


# ── the one path that may advance ─────────────────────────────────────────────

class TestPositiveAcceptance(unittest.TestCase):

    def test_auth_01_accept_with_current_delivered_quote_allows(self):
        decision = authorize_quote_acceptance([accept_claim()], good_state())
        self.assertEqual(decision.result, ALLOW, decision.reason)
        self.assertIn("quote_delivered", decision.satisfied)
        self.assertIn("quote_inputs_unchanged", decision.satisfied)
        self.assertIsNotNone(decision.quote_identity)

    def test_auth_16_valid_acceptance_coverage(self):
        """Four ways of saying yes to a delivered quote all authorise."""
        phrasings = ["Sí, avancemos", "Dale, avancemos", "Ok, quiero hacerlo",
                     "Sí, coordinemos"]
        allowed = 0
        for text in phrasings:
            evidence = TurnEvidence(acceptance=AcceptanceEvidence(
                signal=AcceptanceSignal.ACCEPT, value=True,
                status=EvidenceStatus.CONFIRMED))
            claims = claims_from_turn_evidence(evidence, texts=[text], cycle_id=CYCLE)
            if authorize_quote_acceptance(claims, good_state()).allows:
                allowed += 1
        self.assertEqual(allowed, len(phrasings),
                         "the authorizer must not be uselessly conservative")


# ── the false-progression suite ───────────────────────────────────────────────

class TestFalseProgression(unittest.TestCase):

    def assert_no_progression(self, decision, case):
        self.assertNotEqual(decision.result, ALLOW, f"{case}: {decision.reason}")

    def test_auth_02_accept_without_a_quote(self):
        decision = authorize_quote_acceptance(
            [accept_claim()], good_state(quote_total=None, delivered_amounts=(),
                                         quote_delivered=False))
        self.assertEqual(decision.result, DENY)
        self.assertIn("quote_exists", decision.failed)

    def test_auth_03_future_intent_does_not_progress(self):
        decision = authorize_quote_acceptance(
            [other_claim(ClaimType.FUTURE_INTENT)], good_state())
        self.assert_no_progression(decision, "future intent")
        self.assertEqual(decision.stance, "FUTURE_INTENT")

    def test_auth_04_hesitate_does_not_progress(self):
        # HESITATE produces no acceptance claim at all — the stance is simply absent.
        evidence = TurnEvidence(acceptance=AcceptanceEvidence(
            signal=AcceptanceSignal.HESITATE, status=EvidenceStatus.CONFIRMED))
        claims = claims_from_turn_evidence(evidence, texts=["lo voy a pensar"],
                                           cycle_id=CYCLE)
        self.assert_no_progression(authorize_quote_acceptance(claims, good_state()),
                                   "hesitation")

    def test_auth_05_question_only_does_not_progress(self):
        evidence = TurnEvidence(acceptance=AcceptanceEvidence(
            signal=AcceptanceSignal.QUESTION_ONLY, status=EvidenceStatus.CONFIRMED))
        claims = claims_from_turn_evidence(evidence, texts=["¿qué incluye?"], cycle_id=CYCLE)
        self.assert_no_progression(authorize_quote_acceptance(claims, good_state()),
                                   "question only")

    def test_auth_06_conditional_acceptance_does_not_progress(self):
        decision = authorize_quote_acceptance(
            [accept_claim(modality=Modality.CONDITIONAL, temporality=Temporality.FUTURE)],
            good_state())
        self.assert_no_progression(decision, "si me cierra te hablo")
        self.assertIn("acceptance_is_present_and_factual", decision.failed)

    def test_auth_07_future_acceptance_does_not_progress(self):
        decision = authorize_quote_acceptance(
            [accept_claim(temporality=Temporality.FUTURE)], good_state())
        self.assert_no_progression(decision, "después te aviso")

    def test_auth_08_stale_candidate_does_not_progress(self):
        decision = authorize_quote_acceptance(
            [accept_claim()], good_state(candidate_id=12))       # quoted candidate was 11
        self.assertEqual(decision.result, DENY)
        self.assertIn("quote_inputs_unchanged", decision.failed)

    def test_auth_09_stale_location_does_not_progress(self):
        decision = authorize_quote_acceptance(
            [accept_claim()], good_state(current_zone_detail="Quilmes",
                                         current_zone_group="Sur"))
        self.assertEqual(decision.result, DENY)
        self.assertIn("stale", decision.reason)

    def test_auth_10_recomputed_quote_invalidates_prior_acceptance(self):
        """A new amount is a new quote identity; the old acceptance does not carry over."""
        before = good_state().quote_identity()
        after = good_state(quote_total=112000, delivered_amounts=(95000,)).quote_identity()
        self.assertNotEqual(before, after)
        decision = authorize_quote_acceptance(
            [accept_claim()], good_state(quote_total=112000, delivered_amounts=(95000,),
                                         quote_delivered=False))
        self.assertEqual(decision.result, DENY)
        self.assertIn("quote_delivered", decision.failed)

    def test_auth_11_prior_cycle_acceptance_blocked(self):
        stale_quote = authorize_quote_acceptance(
            [accept_claim()], good_state(quote_cycle_id="cycle-6"))
        self.assertEqual(stale_quote.result, DENY)
        stale_accept = authorize_quote_acceptance(
            [accept_claim(cycle_id="cycle-6")], good_state())
        self.assertEqual(stale_accept.result, DENY)

    def test_auth_12_computed_but_not_delivered_blocked(self):
        decision = authorize_quote_acceptance(
            [accept_claim()], good_state(delivered_amounts=(), quote_delivered=False))
        self.assertEqual(decision.result, DENY)
        self.assertIn("never delivered", decision.reason)

    def test_auth_13_confidence_cannot_change_the_outcome(self):
        low = authorize_quote_acceptance([accept_claim(confidence=0.01)], good_state())
        high = authorize_quote_acceptance([accept_claim(confidence=0.99)], good_state())
        self.assertEqual(low.result, high.result)
        blocked_low = authorize_quote_acceptance(
            [accept_claim(confidence=0.99, modality=Modality.CONDITIONAL)], good_state())
        self.assertNotEqual(blocked_low.result, ALLOW,
                            "a confident conditional is still conditional")

    def test_auth_14_quote_request_alone_never_accepts(self):
        decision = authorize_quote_acceptance(
            [other_claim(ClaimType.QUOTE_REQUEST)], good_state())
        self.assert_no_progression(decision, "cuánto sale?")
        self.assertIsNone(decision.stance)

    def test_auth_15_courtesy_alone_never_accepts(self):
        """"gracias", "buenísimo" — the interpreter emits no acceptance, and neither do we."""
        for text in ["gracias!", "buenísimo", "ok"]:
            claims = claims_from_turn_evidence(TurnEvidence(), texts=[text], cycle_id=CYCLE)
            decision = authorize_quote_acceptance(claims, good_state())
            self.assert_no_progression(decision, text)
            self.assertEqual(decision.result, HOLD)

    def test_rejection_denies(self):
        decision = authorize_quote_acceptance(
            [accept_claim(polarity=Polarity.NEGATED)], good_state())
        self.assertEqual(decision.result, DENY)
        self.assertEqual(decision.stance, "REJECT")

    def test_searching_not_ready_blocks_even_with_an_acceptance(self):
        decision = authorize_quote_acceptance(
            [accept_claim(), other_claim(ClaimType.SEARCHING_NOT_READY,
                                         "SEARCHING_NOT_READY")], good_state())
        self.assertEqual(decision.result, HOLD)
        self.assertIn("searching_not_ready", decision.blockers)

    def test_absent_readiness_is_not_readiness(self):
        """The blocker being absent proves nothing; the positive prerequisites carry ALLOW."""
        decision = authorize_quote_acceptance([accept_claim()], good_state())
        self.assertEqual(decision.result, ALLOW)
        self.assertEqual(decision.blockers, ())
        self.assertIn("quote_delivered", decision.satisfied)

    def test_unresolved_conflicts_clarify(self):
        for kw, blocker in ((dict(candidate_conflict=True), "candidate_conflict"),
                            (dict(location_conflict=True), "location_conflict")):
            decision = authorize_quote_acceptance([accept_claim()], good_state(**kw))
            self.assertEqual(decision.result, CLARIFY)
            self.assertIn(blocker, decision.blockers)


# ── stance reconciliation ─────────────────────────────────────────────────────

class TestStance(unittest.TestCase):

    def test_a_derived_acceptance_never_authorises(self):
        """A stance concluded from other facts, rather than read from the turn, clarifies."""
        derived = accept_claim()
        derived = derived.model_copy(update={"explicitness": Explicitness.DERIVED})
        decision = authorize_quote_acceptance([derived], good_state())
        self.assertEqual(decision.result, CLARIFY)
        self.assertIn("acceptance_read_not_derived", decision.failed)

    def test_stance_values_are_distinct(self):
        self.assertEqual(reconcile_stance([accept_claim()])[0], "ACCEPT")
        self.assertEqual(reconcile_stance([accept_claim(polarity=Polarity.NEGATED)])[0],
                         "REJECT")
        self.assertEqual(reconcile_stance([other_claim(ClaimType.FUTURE_INTENT)])[0],
                         "FUTURE_INTENT")
        self.assertIsNone(reconcile_stance([])[0])

    def test_dale_without_a_proposal_does_not_authorise(self):
        """The stance may be ACCEPT; with no delivered quote it authorises nothing."""
        decision = authorize_quote_acceptance(
            [accept_claim()], good_state(quote_total=None, quote_delivered=False,
                                         delivered_amounts=(), stage="QUALIFYING",
                                         lead_flag=None))
        self.assertEqual(decision.result, DENY)


# ── shadow only: nothing is written, nothing is sent ──────────────────────────

class TestShadowOnly(unittest.TestCase):

    MODULE = ROOT / "backend" / "app" / "services" / "acceptance_authorizer.py"

    def test_auth_17_18_19_20_no_writes_no_transitions_no_outbound(self):
        tree = ast.parse(self.MODULE.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        for forbidden in ("models", "db", "session", "conversation_engine", "pricing",
                          "schedule", "outbound_safety_gate", "booking_flow_service",
                          "lead_lifecycle", "thread_revisions", "requests"):
            self.assertFalse(any(forbidden in module for module in imported),
                             f"the authorizer must not import {forbidden}: {sorted(imported)}")
        source = self.MODULE.read_text(encoding="utf-8")
        for writer in (".add(", ".commit(", ".flush(", ".delete(", ".execute(",
                       "last_stage =", "lead.flag", "ACEPTADO", "_send_"):
            self.assertNotIn(writer, source, f"the authorizer contains {writer}")

    def test_every_record_is_marked_shadow(self):
        decision = authorize_quote_acceptance([accept_claim()], good_state())
        record = to_shadow_record(decision, good_state(), legacy_decision="ALLOW",
                                  comparison="AGREE_ALLOW")
        self.assertTrue(record["shadow"])
        self.assertTrue(decision.shadow)
        self.assertEqual(record["risk_tier"], "HIGH")
        self.assertEqual(record["rule_id"], "authorize.quote_acceptance")
        self.assertEqual(record["rule_version"], "v1")

    def test_shadow_record_carries_no_customer_text(self):
        import json
        evidence = TurnEvidence(acceptance=AcceptanceEvidence(
            signal=AcceptanceSignal.ACCEPT, value=True, status=EvidenceStatus.CONFIRMED))
        claims = claims_from_turn_evidence(
            evidence, texts=["Dale, avancemos con el Peugeot de Berazategui"], cycle_id=CYCLE)
        record = to_shadow_record(authorize_quote_acceptance(claims, good_state()),
                                  good_state())
        blob = json.dumps(record, ensure_ascii=False)
        self.assertNotIn("Berazategui", blob)
        self.assertNotIn("Peugeot", blob)

    def test_the_legacy_acceptance_branch_still_exists_and_is_flag_guarded(self):
        """C3A wired nothing. L4.7C.3B then wired the gate, deliberately and behind a flag.

        The assertion moved with the cutover: what must remain true is that the legacy
        branch is still there and still reachable, so turning the flag off restores it.
        """
        ce = (ROOT / "backend" / "app" / "services" / "conversation_engine.py").read_text()
        self.assertIn("if state.last_stage == STAGE_QUOTED and _is_acceptance(", ce)
        self.assertIn("return self._handle_quoted_acceptance(ctx, state)", ce)
        self.assertIn("if not self._acceptance_authority_on():", ce,
                      "the legacy path must remain reachable with the flag off")


if __name__ == "__main__":
    unittest.main()
