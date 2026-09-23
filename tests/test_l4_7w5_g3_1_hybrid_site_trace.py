"""L4.7W5 G3-1 — the four hybrid decisions the Inspector could not see.

The 2026-09-23 audit established that semantic evidence already reaches production. It
reaches four decisions:

    scheduling.request.reconciliation   CE claims + semantic claims -> reconcile_scheduling
    quote.acceptance.authorization      CE claims + semantic claims -> authorize_quote_acceptance
    handoff.semantic.request            semantic NEEDS_HUMAN -> scheduling escalation
    locality.semantic.recovery          semantic fragment -> proposal + confirmation question

None of them emitted a reconciliation row. The three sites that DID emit rows are CE-only,
so the Inspector was truthful and blind at the same time: every row it showed said
"one producer", and every genuinely hybrid decision happened off-camera.

G3-1 records what those four decisions already consume and already decide. It is
observational in the strict sense the milestone requires — no re-parse, no second model
call, no manufactured participation, no policy change. The proof that matters is not that
rows appear; it is that **the business result is identical with tracing on and off**, and
that is what SITE-30..37 assert.

One thing this suite deliberately does NOT do: it does not implement P4's HOLD-and-clarify,
does not consolidate locality writers, and does not project ambiguities. Those are G3-2
through G3-6, and a test that asserted them would be asserting work nobody has reviewed.

SITE-01..09  scheduling reconciliation
SITE-10..16  quote-acceptance authorization
SITE-17..23  semantic human handoff
SITE-24..29  semantic locality recovery
SITE-30..37  cross-cutting: parity, single call, fail-safe, early return, identity
SITE-38..44  privacy, history, contract
"""
from __future__ import annotations

import hashlib
import json
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

from app.schemas.claims import ClaimType, EvidenceClass
from app.schemas.hybrid_trace import (CanonicalEffect, Classification, ComparisonVerdict,
                                      DECISION_PURPOSE, DecisionSite, EvidenceSource,
                                      TRACE_VERSION)
from app.services import conversation_engine as ce
from app.services import hybrid_trace as svc

FIXTURE = ROOT / "tests" / "fixtures" / "hybrid_trace_live_partial_reconciliation.json"
LIVE_BYTES = FIXTURE.read_bytes()

SEM = EvidenceSource.SEMANTIC
DET = EvidenceSource.DETERMINISTIC


# ── a bare engine, with no database and no provider ──────────────────────────

def engine(*, trace=True, scheduling=True, acceptance=True, vehicle=True, location=True):
    e = ce.ConversationEngine.__new__(ce.ConversationEngine)
    e.settings = types.SimpleNamespace(
        hybrid_trace_enabled=trace,
        reconciler_scheduling_authority_enabled=scheduling,
        reconciler_acceptance_authority_enabled=acceptance,
        reconciler_vehicle_authority_enabled=vehicle,
        reconciler_location_authority_enabled=location,
        semantic_same_turn_enabled=True,
        semantic_same_turn_timeout_seconds=6.0)
    e._turn_trace_reconciliations = []
    e._turn_semantic = None
    e._turn_semantic_texts = []
    e._turn_scheduling_requested = False
    return e


def state():
    return types.SimpleNamespace(current_cycle_start_message_db_id=1,
                                 current_revision_id=None,
                                 current_cycle_started_at=None)


def ctx(st):
    return types.SimpleNamespace(thread=types.SimpleNamespace(id=7), state=st, lead=None)


def rows(e):
    """The rows the turn collected, settled the way the writer settles them."""
    from app.schemas.hybrid_trace import SemanticEvidence
    return svc.finalize_rows(tuple(e._turn_trace_reconciliations),
                             SemanticEvidence(status="ABSENT"))


def row_for(e, site):
    return [r for r in rows(e) if r.decision_site_id == site]


# ── SITE-01..09: scheduling reconciliation ───────────────────────────────────

class SchedulingSite(unittest.TestCase):

    SITE = DecisionSite.SCHEDULING_REQUEST_RECONCILIATION

    def run_site(self, *, ce_claims, sem_claims, trace=True):
        from datetime import date
        e = engine(trace=trace)
        st = state()
        with mock.patch.object(e, "_scheduling_claims_from_texts",
                               return_value=list(ce_claims)), \
             mock.patch.object(e, "_semantic_scheduling_claims",
                               return_value=list(sem_claims)):
            out = e._reconciled_scheduling_requests(ctx(st), st, ["irrelevant"], date(2026, 9, 23))
        return e, out

    @staticmethod
    def claim(producer, branches, klass):
        from app.schemas.claims import ClaimEvidence, Explicitness
        return ClaimEvidence(claim_type=ClaimType.SCHEDULING_PREFERENCE, value=branches,
                             evidence_class=klass, producer=producer,
                             explicitness=Explicitness.STATED).with_id()

    def det(self, branches):
        return self.claim("ce:_parse_scheduling_requests", branches,
                          EvidenceClass.DETERMINISTIC_EXTRACTED)

    def sem(self, branches):
        return self.claim("semantic:understand", branches, EvidenceClass.SEMANTIC_INFERRED)

    # `resolved_date` is the key the reconciler reads; a fixture using "date" resolves to
    # nothing and would have tested an empty decision while appearing to test a full one.
    BR_A = ({"resolved_date": "2026-09-24", "time": "10:30"},)
    BR_A_RICH = ({"resolved_date": "2026-09-24", "time": "10:30"},
                 {"resolved_date": "2026-09-25", "time": "15:00"})
    BR_B = ({"resolved_date": "2026-09-30", "time": "18:00"},)

    def test_site_01_compatible_two_producers_is_agree(self):
        e, out = self.run_site(ce_claims=[self.det(self.BR_A)],
                               sem_claims=[self.sem(self.BR_A_RICH)])
        row = row_for(e, self.SITE)[0]
        self.assertEqual(row.classification, Classification.AGREE)
        self.assertEqual(sorted(row.participating_sources), sorted([SEM, DET]))
        self.assertEqual(row.domain_verdict, ComparisonVerdict.COMPATIBLE)
        self.assertEqual(row.authority_result, "semantic")

    def test_site_02_incompatible_two_producers_is_conflict(self):
        e, out = self.run_site(ce_claims=[self.det(self.BR_A)],
                               sem_claims=[self.sem(self.BR_B)])
        row = row_for(e, self.SITE)[0]
        self.assertEqual(row.classification, Classification.CONFLICT)
        self.assertEqual(row.domain_verdict, ComparisonVerdict.INCOMPATIBLE)
        self.assertEqual(row.authority_result, "deterministic_conflict")

    def test_site_03_ce_only_is_single_producer(self):
        e, out = self.run_site(ce_claims=[self.det(self.BR_A)], sem_claims=[])
        row = row_for(e, self.SITE)[0]
        self.assertEqual(row.classification, Classification.SINGLE_PRODUCER)
        self.assertEqual(row.participating_sources, (DET,))
        self.assertIsNone(row.domain_verdict, "one producer compared nothing")

    def test_site_04_semantic_only_is_single_producer_semantic(self):
        e, out = self.run_site(ce_claims=[], sem_claims=[self.sem(self.BR_A)])
        row = row_for(e, self.SITE)[0]
        self.assertEqual(row.classification, Classification.SINGLE_PRODUCER)
        self.assertEqual(row.participating_sources, (SEM,))

    def test_site_05_no_evidence_is_no_evidence(self):
        e, out = self.run_site(ce_claims=[], sem_claims=[])
        row = row_for(e, self.SITE)[0]
        self.assertEqual(row.classification, Classification.NO_EVIDENCE)
        self.assertEqual(out, [])

    def test_site_06_absence_never_becomes_agreement(self):
        for ce_c, sem_c in (([], []), ([self.det(self.BR_A)], []), ([], [self.sem(self.BR_A)])):
            with self.subTest(n=len(ce_c) + len(sem_c)):
                e, _ = self.run_site(ce_claims=ce_c, sem_claims=sem_c)
                self.assertNotEqual(row_for(e, self.SITE)[0].classification,
                                    Classification.AGREE)

    def test_site_07_the_traced_result_matches_the_business_result(self):
        e, out = self.run_site(ce_claims=[self.det(self.BR_A)],
                               sem_claims=[self.sem(self.BR_A_RICH)])
        row = row_for(e, self.SITE)[0]
        self.assertEqual(row.business_outcome, f"{len(out)} scheduling request(s) resolved")
        self.assertEqual(row.permitted_action, "check_availability")
        self.assertEqual(len(out), 2, "the reconciler kept the richer semantic reading")

    def test_site_08_the_conflict_result_is_the_deterministic_reading(self):
        """P4 is NOT implemented here. The existing precedence must be untouched."""
        e, out = self.run_site(ce_claims=[self.det(self.BR_A)],
                               sem_claims=[self.sem(self.BR_B)])
        self.assertEqual([(r.day_iso, r.time_str) for r in out], [("2026-09-24", "10:30")])

    def test_site_09_no_canonical_write_from_this_site(self):
        e, _ = self.run_site(ce_claims=[self.det(self.BR_A)],
                             sem_claims=[self.sem(self.BR_A_RICH)])
        self.assertEqual(row_for(e, self.SITE)[0].canonical_effect, CanonicalEffect.NONE)


# ── SITE-10..16: quote-acceptance authorization ──────────────────────────────

class AcceptanceSite(unittest.TestCase):

    SITE = DecisionSite.QUOTE_ACCEPTANCE_AUTHORIZATION

    def run_site(self, claims, *, allows=False, result="HOLD", trace=True):
        e = engine(trace=trace)
        st = state()
        decision = types.SimpleNamespace(
            result=result, reason="fixture", rule_id="authorize.quote_acceptance",
            rule_version="v1", stance="ACCEPT" if allows else None,
            stance_state="TRUE_ONLY", quote_identity=None, satisfied=(), failed=(),
            blockers=(), evidence_ids=tuple(c.claim_id for c in claims),
            risk_tier="HIGH", allows=allows)
        with mock.patch("app.services.claim_projection.acceptance_claims",
                        return_value=list(claims)), \
             mock.patch("app.services.acceptance_authorizer.authorize_quote_acceptance",
                        return_value=decision), \
             mock.patch.object(e, "_semantic_turn_evidence", return_value=None), \
             mock.patch.object(e, "_turn_has_scheduling_evidence", return_value=False), \
             mock.patch.object(e, "_commercial_state", return_value=object()), \
             mock.patch.object(e, "_record_authorization"):
            out = e._authorize_acceptance(ctx(st), st, ["irrelevant"])
        return e, out

    @staticmethod
    def claim(producer, klass, value=True):
        from app.schemas.claims import ClaimEvidence, Explicitness
        return ClaimEvidence(claim_type=ClaimType.QUOTE_ACCEPTED, value=value,
                             evidence_class=klass, producer=producer,
                             explicitness=Explicitness.STATED).with_id()

    def det(self, value=True):
        return self.claim("canonical:deterministic_acceptance",
                          EvidenceClass.DETERMINISTIC_EXTRACTED, value)

    def sem(self, value=True):
        return self.claim("semantic:understand", EvidenceClass.SEMANTIC_INFERRED, value)

    def test_site_10_both_producers_agree(self):
        e, _ = self.run_site([self.det(), self.sem()], allows=True, result="ALLOW")
        row = row_for(e, self.SITE)[0]
        self.assertEqual(row.classification, Classification.AGREE)
        self.assertEqual(row.authority_result, "ALLOW")
        self.assertEqual(row.permitted_action, "advance_commercial_state")

    def test_site_11_genuine_conflict_is_conflict(self):
        e, _ = self.run_site([self.det(True), self.sem(False)])
        row = row_for(e, self.SITE)[0]
        self.assertEqual(row.classification, Classification.CONFLICT)

    def test_site_12_ce_only(self):
        e, _ = self.run_site([self.det()])
        row = row_for(e, self.SITE)[0]
        self.assertEqual(row.classification, Classification.SINGLE_PRODUCER)
        self.assertEqual(row.participating_sources, (EvidenceSource.CANONICAL_STATE,))

    def test_site_13_semantic_only(self):
        e, _ = self.run_site([self.sem()])
        row = row_for(e, self.SITE)[0]
        self.assertEqual(row.classification, Classification.SINGLE_PRODUCER)
        self.assertEqual(row.participating_sources, (SEM,))

    def test_site_14_absence_does_not_become_agreement(self):
        e, _ = self.run_site([])
        self.assertEqual(row_for(e, self.SITE)[0].classification, Classification.NO_EVIDENCE)

    def test_site_15_authorization_behaviour_is_unchanged(self):
        """The trace observes; it must return the authorizer's own decision untouched."""
        for allows, result in ((True, "ALLOW"), (False, "HOLD"), (False, "DENY")):
            with self.subTest(result=result):
                e, out = self.run_site([self.det()], allows=allows, result=result)
                self.assertEqual(out.result, result)
                self.assertEqual(out.allows, allows)

    def test_site_16_no_hold_and_clarify_implemented_here(self):
        """P4 is a G3-6 obligation. This milestone must not pre-empt it."""
        e, out = self.run_site([self.det(True), self.sem(False)])
        row = row_for(e, self.SITE)[0]
        self.assertNotIn("HOLD_AND_CLARIFY", str(row.authority_result))
        self.assertEqual(row.canonical_effect, CanonicalEffect.NONE)


# ── SITE-17..23: semantic human handoff ──────────────────────────────────────

class HandoffSite(unittest.TestCase):

    SITE = DecisionSite.HANDOFF_SEMANTIC_REQUEST

    def run_site(self, claims, *, trace=True):
        e = engine(trace=trace)
        st = state()
        with mock.patch.object(e, "_semantic_turn_evidence", return_value=object()), \
             mock.patch("app.services.claim_projection.claims_from_turn_evidence",
                        return_value=list(claims)):
            out = e._semantic_handoff_requested(st)
        return e, out

    @staticmethod
    def claim(value=True, status=None, producer="semantic:understand"):
        from app.schemas.claims import ClaimEvidence, Explicitness
        from app.schemas.turn_evidence import EvidenceStatus
        return ClaimEvidence(claim_type=ClaimType.NEEDS_HUMAN, value=value,
                             status=status or EvidenceStatus.CONFIRMED,
                             evidence_class=EvidenceClass.SEMANTIC_INFERRED,
                             producer=producer,
                             explicitness=Explicitness.STATED).with_id()

    def test_site_17_affirmative_semantic_only_escalates(self):
        e, out = self.run_site([self.claim(True)])
        self.assertTrue(out)
        row = row_for(e, self.SITE)[0]
        self.assertEqual(row.classification, Classification.SINGLE_PRODUCER)
        self.assertEqual(row.participating_sources, (SEM,))
        self.assertEqual(row.authority_result, "ACCEPT")
        self.assertEqual(row.permitted_action, "human_escalation")
        self.assertEqual(row.canonical_effect, CanonicalEffect.WROTE)

    def test_site_18_negated_request_does_not_escalate(self):
        e, out = self.run_site([self.claim(False)])
        self.assertFalse(out)
        row = row_for(e, self.SITE)[0]
        self.assertEqual(row.authority_result, "HOLD")
        self.assertIsNone(row.permitted_action)
        self.assertEqual(row.canonical_effect, CanonicalEffect.NONE)

    def test_site_19_ambiguous_evidence_does_not_escalate(self):
        from app.schemas.turn_evidence import EvidenceStatus
        for status in (EvidenceStatus.AMBIGUOUS, EvidenceStatus.CONFLICT):
            with self.subTest(status=status):
                e, out = self.run_site([self.claim(True, status=status)])
                self.assertFalse(out, "unresolved evidence is not an affirmative request")
                self.assertEqual(row_for(e, self.SITE)[0].authority_result, "HOLD")

    def test_site_20_no_fabricated_ce_participation(self):
        e, _ = self.run_site([self.claim(True)])
        row = row_for(e, self.SITE)[0]
        self.assertNotIn(DET, row.participating_sources)
        self.assertEqual(row.ce_input, "ABSENT")
        self.assertEqual(row.compared_propositions, (),
                         "one producer compared nothing")

    def test_site_21_a_refusal_is_still_recorded(self):
        """The reader must tell 'looked and declined' from 'never looked'."""
        e, _ = self.run_site([self.claim(False)])
        self.assertEqual(len(row_for(e, self.SITE)), 1)

    def test_site_22_no_evidence_at_all_still_records(self):
        e, out = self.run_site([])
        self.assertFalse(out)
        row = row_for(e, self.SITE)[0]
        self.assertEqual(row.classification, Classification.NO_EVIDENCE)

    def test_site_23_business_outcome_matches_the_decision(self):
        for claims, expected in (([self.claim(True)], True), ([self.claim(False)], False)):
            with self.subTest(expected=expected):
                e, out = self.run_site(claims)
                self.assertEqual(out, expected)


# ── SITE-24..29: semantic locality recovery ──────────────────────────────────

class LocalitySite(unittest.TestCase):

    SITE = DecisionSite.LOCALITY_SEMANTIC_RECOVERY

    def run_site(self, match, fragment_text="Tegui", *, trace=True):
        e = engine(trace=trace)
        st = state()
        e.db = types.SimpleNamespace(
            execute=lambda *a, **k: types.SimpleNamespace(
                scalars=lambda: types.SimpleNamespace(all=lambda: [])))
        fragment = types.SimpleNamespace(text=fragment_text, role="INSPECTION_LOCATION",
                                         producer="semantic:understand")
        with mock.patch.object(e, "_semantic_location_fragment", return_value=fragment), \
             mock.patch("app.services.locality_resolver.resolve_locality_fragment",
                        return_value=match):
            out = e._recover_locality(ctx(st), st, "burst")
        return e, out

    @staticmethod
    def match(status="APPROXIMATE", detail="Berazategui"):
        best = (types.SimpleNamespace(zone_group="SUR", zone_detail=detail, score=0.87)
                if detail else None)
        return types.SimpleNamespace(
            status=status, best=best, alternatives=(), reason="fixture",
            rule_id="resolve.locality_fragment", rule_version="v1",
            needs_confirmation=(status == "APPROXIMATE"),
            is_canonical=(status == "EXACT"))

    def test_site_24_semantic_only_proposal_is_single_producer(self):
        e, out = self.run_site(self.match())
        row = row_for(e, self.SITE)[0]
        self.assertEqual(row.classification, Classification.SINGLE_PRODUCER)
        self.assertEqual(row.participating_sources, (SEM,))

    def test_site_25_confirmation_action_is_recorded(self):
        e, _ = self.run_site(self.match("APPROXIMATE"))
        row = row_for(e, self.SITE)[0]
        self.assertEqual(row.permitted_action, "ask_locality_confirmation")
        self.assertEqual(row.authority_result, "APPROXIMATE")

    def test_site_26_no_canonical_locality_write_is_introduced(self):
        """P2: propose and ask. Never write. The row must say so."""
        for status in ("APPROXIMATE", "EXACT", "AMBIGUOUS", "NONE"):
            with self.subTest(status=status):
                e, _ = self.run_site(self.match(status, "Berazategui" if status != "NONE" else None))
                self.assertEqual(row_for(e, self.SITE)[0].canonical_effect,
                                 CanonicalEffect.NONE)

    def test_site_27_no_fabricated_ce_agreement(self):
        e, _ = self.run_site(self.match())
        row = row_for(e, self.SITE)[0]
        self.assertNotIn(DET, row.participating_sources)
        self.assertNotEqual(row.classification, Classification.AGREE)

    def test_site_28_non_proposing_statuses_permit_nothing(self):
        for status in ("NONE", "AMBIGUOUS"):
            with self.subTest(status=status):
                e, _ = self.run_site(self.match(status, None))
                self.assertIsNone(row_for(e, self.SITE)[0].permitted_action)

    def test_site_29_the_match_is_returned_untouched(self):
        m = self.match()
        e, out = self.run_site(m)
        self.assertIs(out, m, "the observer must not replace the decision")


# ── SITE-30..37: cross-cutting ───────────────────────────────────────────────

class CrossCutting(unittest.TestCase):

    def test_site_30_tracing_on_and_off_give_identical_business_results(self):
        """The whole milestone rests on this."""
        from datetime import date
        s = SchedulingSite()
        for ce_c, sem_c in ((["A"], ["B"]), (["A"], []), ([], ["B"]), ([], [])):
            det = [s.det(s.BR_A)] if ce_c else []
            sem = [s.sem(s.BR_B)] if sem_c else []
            on, out_on = s.run_site(ce_claims=det, sem_claims=sem, trace=True)
            off, out_off = s.run_site(ce_claims=det, sem_claims=sem, trace=False)
            with self.subTest(case=(bool(ce_c), bool(sem_c))):
                self.assertEqual([(r.day_iso, r.time_str) for r in out_on],
                                 [(r.day_iso, r.time_str) for r in out_off])
                self.assertEqual(len(off._turn_trace_reconciliations), 0,
                                 "tracing off must persist nothing")

        h = HandoffSite()
        for claims in ([h.claim(True)], [h.claim(False)], []):
            on, out_on = h.run_site(claims, trace=True)
            off, out_off = h.run_site(claims, trace=False)
            with self.subTest(handoff=len(claims)):
                self.assertEqual(out_on, out_off)
                self.assertEqual(len(off._turn_trace_reconciliations), 0)

        a = AcceptanceSite()
        for allows, result in ((True, "ALLOW"), (False, "HOLD")):
            on, out_on = a.run_site([a.det()], allows=allows, result=result, trace=True)
            off, out_off = a.run_site([a.det()], allows=allows, result=result, trace=False)
            with self.subTest(result=result):
                self.assertEqual((out_on.result, out_on.allows),
                                 (out_off.result, out_off.allows))
                self.assertEqual(len(off._turn_trace_reconciliations), 0)

        loc = LocalitySite()
        on, out_on = loc.run_site(loc.match(), trace=True)
        off, out_off = loc.run_site(loc.match(), trace=False)
        self.assertEqual(out_on.status, out_off.status)
        self.assertEqual(len(off._turn_trace_reconciliations), 0)

    def test_site_31_trace_failure_cannot_change_the_decision(self):
        from datetime import date
        s = SchedulingSite()
        with mock.patch("app.services.hybrid_trace.reconciliation_from",
                        side_effect=RuntimeError("observer exploded")):
            e, out = s.run_site(ce_claims=[s.det(s.BR_A)], sem_claims=[])
        self.assertEqual([(r.day_iso, r.time_str) for r in out], [("2026-09-24", "10:30")])
        self.assertEqual(len(e._turn_trace_reconciliations), 0)

        h = HandoffSite()
        with mock.patch("app.services.hybrid_trace.reconciliation_from",
                        side_effect=RuntimeError("observer exploded")):
            e2, out2 = h.run_site([h.claim(True)])
        self.assertTrue(out2, "the handoff decision survives a trace failure")

    def test_site_32_the_observer_makes_no_model_call(self):
        """No site may reach the provider. `_semantic_turn_evidence` is the only door."""
        import ast
        src = (ROOT / "backend" / "app" / "services"
               / "conversation_engine.py").read_text(encoding="utf-8-sig")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "_trace_decision")
        seg = ast.get_source_segment(src, fn)
        for forbidden in ("_semantic_turn_evidence", "provider.get", "interpret",
                          "SemanticTurnInterpreter", "_run_shadow_understand"):
            self.assertNotIn(forbidden, seg)

    def test_site_33_early_returns_still_finalize_the_trace(self):
        """`handle()` wraps `_handle()`; every early return is inside the inner call."""
        import ast
        src = (ROOT / "backend" / "app" / "services"
               / "conversation_engine.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(src)
        outer = next(n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == "handle")
        seg = ast.get_source_segment(src, outer)
        self.assertEqual(seg.count("return "), 1,
                         "handle() must have exactly one exit, after the finalizer")
        self.assertIn("self._write_hybrid_trace(", seg)
        self.assertIn("out = self._handle(event)", seg)
        body = seg[seg.index("out = self._handle(event)"):]
        self.assertLess(body.index("_write_hybrid_trace"), body.rindex("return "),
                        "the finalizer must run before the single return")

    def test_site_34_every_new_site_has_a_stable_non_positional_identity(self):
        from datetime import date
        s = SchedulingSite()
        a, _ = s.run_site(ce_claims=[s.det(s.BR_A)], sem_claims=[])
        b, _ = s.run_site(ce_claims=[s.det(s.BR_A)], sem_claims=[])
        ida = row_for(a, s.SITE)[0].logical_comparison_id
        idb = row_for(b, s.SITE)[0].logical_comparison_id
        self.assertEqual(ida, idb, "same inputs, same identity")
        c, _ = s.run_site(ce_claims=[s.det(s.BR_B)], sem_claims=[])
        self.assertNotEqual(ida, row_for(c, s.SITE)[0].logical_comparison_id)

    def test_site_35_all_four_sites_are_registered_with_purposes(self):
        for site in (DecisionSite.SCHEDULING_REQUEST_RECONCILIATION,
                     DecisionSite.QUOTE_ACCEPTANCE_AUTHORIZATION,
                     DecisionSite.HANDOFF_SEMANTIC_REQUEST,
                     DecisionSite.LOCALITY_SEMANTIC_RECOVERY):
            with self.subTest(site=site):
                self.assertIn(site, DecisionSite.ALL)
                self.assertIn(site, DECISION_PURPOSE)
                self.assertTrue(DECISION_PURPOSE[site])

    def test_site_36_site_identities_are_typed_literals_not_scattered_strings(self):
        import ast
        src = (ROOT / "backend" / "app" / "services"
               / "conversation_engine.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(src)
        # Only the CALL SITES are checked. `_trace_decision` and
        # `_collect_trace_reconciliation` forward the value they were handed, so their own
        # `decision_site_id=decision_site_id` is a parameter reference, not an identity.
        sites = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr in ("_trace_decision", "_record_reconciliation")]
        self.assertGreaterEqual(len(sites), 7, "three legacy sites plus the four new ones")
        named = 0
        for call in sites:
            for kw in call.keywords:
                if kw.arg == "decision_site_id":
                    named += 1
                    self.assertIsInstance(
                        kw.value, ast.Attribute,
                        "a decision site must be a DecisionSite attribute, not a literal")
                    self.assertEqual(kw.value.value.id, "DecisionSite")
        self.assertEqual(named, len(sites), "every site must name its identity")

    def test_site_37_no_policy_module_changed(self):
        """G3-1 is observational: the authorities themselves must be untouched."""
        import shutil, subprocess
        if shutil.which("git") is None:                      # pragma: no cover
            self.skipTest("git not installed in the test image; checked on the host")
        changed = subprocess.run(["git", "diff", "--name-only", "9b3b182", "--"],
                                 cwd=str(ROOT), capture_output=True, text=True).stdout.split()
        for forbidden in ("backend/app/services/scheduling_reconciler.py",
                          "backend/app/services/acceptance_authorizer.py",
                          "backend/app/services/field_reconciler.py",
                          "backend/app/services/locality_resolver.py",
                          "backend/app/services/claim_projection.py",
                          "backend/app/services/semantic_interpreter.py",
                          "backend/app/services/semantic_turn_evidence.py",
                          "backend/app/schemas/claims.py",
                          "backend/app/settings.py"):
            self.assertNotIn(forbidden, changed)


# ── SITE-38..44: privacy, history, contract ──────────────────────────────────

class PrivacyHistoryContract(unittest.TestCase):

    def test_site_38_contract_version_is_1_3_and_older_stay_readable(self):
        from app.schemas.hybrid_trace import (READABLE_VERSIONS, TRACE_VERSION_1_0,
                                              TRACE_VERSION_1_1, TRACE_VERSION_1_2)
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.3")
        for older in (TRACE_VERSION_1_0, TRACE_VERSION_1_1, TRACE_VERSION_1_2):
            self.assertIn(older, READABLE_VERSIONS)

    def test_site_39_the_preserved_trace_is_byte_identical(self):
        payload = json.loads(LIVE_BYTES.decode("utf-8"))
        svc.effective_from_payload(payload)
        svc.row_summaries_from_payload(payload)
        svc.counts_from_payload(payload)
        self.assertEqual(FIXTURE.read_bytes(), LIVE_BYTES)
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
                         hashlib.sha256(LIVE_BYTES).hexdigest())

    def test_site_40_legacy_rows_still_render_by_their_own_provenance(self):
        payload = json.loads(LIVE_BYTES.decode("utf-8"))
        first, second = svc.row_summaries_from_payload(payload)
        self.assertTrue(first["legacy"])
        self.assertEqual(second["captured_classification"], "AGREE")
        self.assertEqual(second["effective_classification"],
                         Classification.LEGACY_PROVENANCE_UNAVAILABLE)
        headline, _ = svc.effective_from_payload(payload)
        self.assertEqual(headline, Classification.TRACE_INCOMPLETE)

    def test_site_41_no_migration_was_introduced(self):
        self.assertEqual(
            len(list((ROOT / "backend" / "migrations" / "versions").glob("*.py"))), 45)

    def test_site_42_no_pii_in_the_new_row_fields(self):
        h = HandoffSite()
        e, _ = h.run_site([h.claim(True)])
        blob = json.dumps(_as_dict(row_for(e, h.SITE)[0]), ensure_ascii=False, default=str)
        self.assertEqual(re.findall(r"\b549\d{8,12}\b", blob), [])
        for marker in ("wamid.", "bk_tok", "-----BEGIN", "@example", "sk-"):
            self.assertNotIn(marker, blob)

    def test_site_43_the_rendered_page_exposes_no_pii(self):
        from app.ui.hybrid_trace_view import render_turn_trace_page
        payload = json.loads(LIVE_BYTES.decode("utf-8"))
        headline, conditions = svc.effective_from_payload(payload)
        page = render_turn_trace_page({
            "captured": True, "trace": payload,
            "captured_classification": payload["badges"][0],
            "effective_classification": headline,
            "supporting_conditions": list(conditions),
            "reconciliation_rows": list(svc.row_summaries_from_payload(payload)),
            "reconciliation_counts": svc.counts_from_payload(payload)})
        for wamid in payload["ordered_message_ids"]:
            self.assertNotIn(wamid, page)
        self.assertEqual(re.findall(r"\b549\d{8,12}\b", page), [])
        self.assertNotIn("chain_of_thought", page)

    def test_site_44_the_authority_column_is_rendered_and_documented(self):
        from app.ui.hybrid_trace_view import LABEL_GLOSSARY
        src = (ROOT / "backend" / "app" / "ui"
               / "hybrid_trace_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("Autoridad y acción", src)
        self.assertIn("_authority_cell", src)
        self.assertIn("Autoridad y acción", {label for label, _ in LABEL_GLOSSARY})


def _as_dict(row):
    import dataclasses
    return dataclasses.asdict(row)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()


# ── G3-1-R2: the verdict guard, cases A..N from the corrective milestone ─────

class DomainVerdictGuard(unittest.TestCase):
    """A domain comparator is heard last, never first.

    G3-1 let a supplied verdict short-circuit ahead of `compare_propositions`. The
    independent pre-push audit proved three consequences: parallel evidence could be
    promoted to AGREE, parallel evidence could be promoted to CONFLICT, and a verdict could
    override an incompatibility the generic comparator had already proven. None was
    reachable from the single site that supplies a verdict today, which is exactly why it
    had to be fixed before a second site exists.

    The rule these cases pin: preconditions first, generic comparison second, domain
    knowledge only where the generic comparison could not reach an answer.
    """

    @staticmethod
    def claim(producer, claim_type, value, klass=EvidenceClass.DETERMINISTIC_EXTRACTED,
              polarity=None):
        from app.schemas.claims import ClaimEvidence, Explicitness, Polarity
        return ClaimEvidence(claim_type=claim_type, value=value, evidence_class=klass,
                             producer=producer,
                             polarity=polarity or Polarity.ASSERTED,
                             explicitness=Explicitness.STATED).with_id()

    def sem(self, t, v, **k):
        return self.claim("semantic:understand", t, v,
                          klass=EvidenceClass.SEMANTIC_INFERRED, **k)

    def det(self, t, v, **k):
        return self.claim("ce:catalog", t, v, **k)

    def classify(self, claims, verdict=None):
        return svc.classify_row(svc.contributions_from_claims(claims),
                                domain_verdict=verdict)

    def admission(self, claims, verdict):
        contribs = svc.contributions_from_claims(claims)
        return svc.admit_domain_verdict(contribs, svc.compare_propositions(contribs),
                                        verdict)

    MODEL = ClaimType.VEHICLE_MODEL
    YEAR = ClaimType.VEHICLE_YEAR
    LOC = ClaimType.INSPECTION_LOCATION

    # A
    def test_r2_a_one_producer_plus_compatible_is_not_agree(self):
        claims = [self.sem(self.MODEL, "Peugeot 208")]
        self.assertEqual(self.classify(claims, ComparisonVerdict.COMPATIBLE),
                         Classification.SINGLE_PRODUCER)
        self.assertEqual(self.admission(claims, ComparisonVerdict.COMPATIBLE),
                         "REJECTED_SINGLE_PRODUCER")

    # B
    def test_r2_b_zero_producers_plus_incompatible_is_no_evidence(self):
        self.assertEqual(self.classify([], ComparisonVerdict.INCOMPATIBLE),
                         Classification.NO_EVIDENCE)

    # C
    def test_r2_c_zero_producers_plus_compatible_is_no_evidence(self):
        self.assertEqual(self.classify([], ComparisonVerdict.COMPATIBLE),
                         Classification.NO_EVIDENCE)

    # D
    def test_r2_d_parallel_plus_compatible_stays_parallel(self):
        claims = [self.sem(self.MODEL, "Peugeot 208"), self.det(self.YEAR, 2020)]
        self.assertEqual(self.classify(claims, ComparisonVerdict.COMPATIBLE),
                         Classification.PARALLEL_EVIDENCE)
        self.assertEqual(self.admission(claims, ComparisonVerdict.COMPATIBLE),
                         "REJECTED_NO_SHARED_PROPOSITION")

    # E
    def test_r2_e_parallel_plus_incompatible_stays_parallel(self):
        claims = [self.sem(self.MODEL, "Peugeot 208"), self.det(self.YEAR, 2020)]
        self.assertEqual(self.classify(claims, ComparisonVerdict.INCOMPATIBLE),
                         Classification.PARALLEL_EVIDENCE)

    # F
    def test_r2_f_verdict_cannot_override_proven_incompatibility(self):
        claims = [self.sem(self.MODEL, "Peugeot 208"), self.det(self.MODEL, "Ford Ka")]
        self.assertEqual(self.classify(claims), Classification.CONFLICT)
        self.assertEqual(self.classify(claims, ComparisonVerdict.COMPATIBLE),
                         Classification.CONFLICT)
        self.assertEqual(self.admission(claims, ComparisonVerdict.COMPATIBLE),
                         "REJECTED_EVIDENCE_ALREADY_PROVEN")

    def test_r2_f2_verdict_cannot_override_proven_compatibility(self):
        claims = [self.sem(self.MODEL, "208"), self.det(self.MODEL, "Peugeot 208")]
        self.assertEqual(self.classify(claims), Classification.AGREE)
        self.assertEqual(self.classify(claims, ComparisonVerdict.INCOMPATIBLE),
                         Classification.AGREE)

    # G
    def test_r2_g_many_claims_one_producer_is_single_producer(self):
        claims = [self.det(self.MODEL, "Peugeot 208"), self.det(self.YEAR, 2020),
                  self.det(ClaimType.VEHICLE_MAKE, "Peugeot")]
        for verdict in (None, ComparisonVerdict.COMPATIBLE, ComparisonVerdict.INCOMPATIBLE):
            with self.subTest(verdict=verdict):
                self.assertEqual(self.classify(claims, verdict),
                                 Classification.SINGLE_PRODUCER)

    # H / I
    def test_r2_h_two_producers_compatible_is_agree(self):
        self.assertEqual(
            self.classify([self.sem(self.MODEL, "208"), self.det(self.MODEL, "Peugeot 208")]),
            Classification.AGREE)

    def test_r2_i_two_producers_incompatible_is_conflict(self):
        self.assertEqual(
            self.classify([self.sem(self.MODEL, "Peugeot 208"),
                           self.det(self.MODEL, "Toyota Corolla")]),
            Classification.CONFLICT)

    def test_r2_i2_unprovable_is_where_domain_knowledge_may_help(self):
        """The one place a domain verdict is admissible — and the only one."""
        claims = [self.sem(self.LOC, "Villa Crespo"), self.det(self.LOC, "V. Crespo")]
        self.assertEqual(self.classify(claims), Classification.COMPARISON_UNPROVEN)
        self.assertEqual(self.admission(claims, ComparisonVerdict.COMPATIBLE), "ADMITTED")
        self.assertEqual(self.classify(claims, ComparisonVerdict.COMPATIBLE),
                         Classification.AGREE)
        self.assertEqual(self.classify(claims, ComparisonVerdict.INCOMPATIBLE),
                         Classification.CONFLICT)

    # J
    def test_r2_j_boolean_canonicalization_remains_exact(self):
        ci = svc.canonical_identity
        t, f = ci(ClaimType.QUOTE_ACCEPTED, True)[0], ci(ClaimType.QUOTE_ACCEPTED, False)[0]
        self.assertNotEqual(t, f)
        self.assertNotEqual(t, ci(ClaimType.QUOTE_ACCEPTED, 1)[0])
        self.assertNotEqual(f, ci(ClaimType.QUOTE_ACCEPTED, 0)[0])
        self.assertIsNone(ci(ClaimType.QUOTE_ACCEPTED, "true")[0])
        self.assertIsNone(ci(ClaimType.QUOTE_ACCEPTED, "false")[0])
        self.assertEqual(
            self.classify([self.sem(ClaimType.QUOTE_ACCEPTED, "true"),
                           self.claim("canonical:deterministic_acceptance",
                                      ClaimType.QUOTE_ACCEPTED, True)]),
            Classification.COMPARISON_UNPROVEN)
        self.assertEqual(ci(ClaimType.VEHICLE_YEAR, 2020)[0], "2020")

    # L
    def test_r2_l_locality_projection_is_trace_only(self):
        loc = LocalitySite()
        e, out = loc.run_site(loc.match())
        row = row_for(e, loc.SITE)[0]
        self.assertEqual(row.participating_sources, (SEM,),
                         "the projection preserves the real semantic producer")
        self.assertNotIn(DET, row.participating_sources, "no CE producer is created")
        self.assertEqual(len(row.participating_sources), 1,
                         "the projection is never counted as an additional producer")
        self.assertEqual(row.canonical_effect, CanonicalEffect.NONE)
        # SITE-29 already pins that `_recover_locality` returns the resolver's own match
        # object; the projection is never substituted for it.

    def test_r2_l2_the_projection_never_reaches_business_logic(self):
        """It is built inside the tracer's own scope and returned to nobody."""
        import ast
        src = (ROOT / "backend" / "app" / "services"
               / "conversation_engine.py").read_text(encoding="utf-8-sig")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "_recover_locality")
        seg = ast.get_source_segment(src, fn)
        self.assertIn("_claims = [ClaimEvidence(", seg)
        self.assertNotIn("return _claims", seg)
        self.assertNotIn("self._claims", seg)
        self.assertTrue(seg.rstrip().endswith("return match"),
                        "the method still returns the resolver's match and nothing else")

    # M
    def test_r2_m_instrumentation_adds_no_provider_call(self):
        import ast
        src = (ROOT / "backend" / "app" / "services"
               / "conversation_engine.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_trace_decision")
        seg = ast.get_source_segment(src, fn)
        # Code only. The docstring says "does not call the provider", which is the claim
        # being tested, not a violation of it.
        code = "\n".join(l for l in seg.splitlines()
                         if not l.strip().startswith(("#", '"""', "G3-", "records ", "turn",
                                                      "no trace", "afford", "class,", "claim")))
        code = code.split('"""')[0] + code.split('"""')[-1] if code.count('"""') >= 2 else code
        for forbidden in ("_semantic_turn_evidence", "provider.", "interpret(",
                          "SemanticTurnInterpreter", "_run_shadow_understand"):
            self.assertNotIn(forbidden, code)
        # the four sites gained no new call to the evidence accessor either
        calls = sum(1 for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "_semantic_turn_evidence")
        self.assertEqual(calls, 5, "the same five pre-existing consumers, no more")


class ObserverFailureDiagnostics(unittest.TestCase):
    """Case K — fail-open, but no longer silent, and never chatty with customer data."""

    def test_r2_k_forced_failure_logs_safely_and_changes_nothing(self):
        import logging
        h = HandoffSite()
        with self.assertLogs("app.services.conversation_engine", level="WARNING") as caught:
            with mock.patch("app.services.hybrid_trace.reconciliation_from",
                            side_effect=RuntimeError("observer exploded")):
                e_fail, out_fail = h.run_site([h.claim(True)], trace=True)
        e_off, out_off = h.run_site([h.claim(True)], trace=False)
        e_ok, out_ok = h.run_site([h.claim(True)], trace=True)

        self.assertEqual(out_fail, out_off,
                         "a failed observer gives the same result as no observer")
        self.assertEqual(out_fail, out_ok,
                         "and the same result as a working observer")
        self.assertEqual(len(e_fail._turn_trace_reconciliations), 0)

        blob = "\n".join(caught.output)
        self.assertIn("HYBRID_TRACE_COLLECT_FAILED", blob)
        self.assertIn("operation=_trace_decision", blob)
        self.assertIn("handoff.semantic.request", blob)
        self.assertIn("RuntimeError", blob)
        self.assertIn("hybrid-decision-trace/", blob)

    def test_r2_k2_the_diagnostic_carries_no_customer_data(self):
        h = HandoffSite()
        with self.assertLogs("app.services.conversation_engine", level="WARNING") as caught:
            with mock.patch("app.services.hybrid_trace.reconciliation_from",
                            side_effect=RuntimeError("boom")):
                h.run_site([h.claim(True)], trace=True)
        blob = "\n".join(caught.output)
        self.assertEqual(re.findall(r"\b549\d{8,12}\b", blob), [])
        for marker in ("wamid.", "bk_tok", "@", "-----BEGIN", "sk-", "prompt"):
            self.assertNotIn(marker, blob)

    def test_r2_k3_the_diagnostic_fields_are_an_allowlist(self):
        """Read from source: only these five values may reach the log line."""
        import ast
        src = (ROOT / "backend" / "app" / "services"
               / "conversation_engine.py").read_text(encoding="utf-8-sig")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "_trace_decision")
        seg = ast.get_source_segment(src, fn)
        line = seg[seg.index("HYBRID_TRACE_COLLECT_FAILED"):seg.index("except Exception:\n                pass")]
        for allowed in ("decision_site_id", "TRACE_VERSION", "type(exc).__name__",
                        "thread", "get_deployment_id()"):
            self.assertIn(allowed, line)
        for forbidden in ("claims", "reason", "business_outcome", "value", "text",
                          "evidence_ids", "outcome"):
            self.assertNotIn(forbidden, line)


class TracingOffParity(unittest.TestCase):
    """Case N — with tracing off the business path is the pre-G3-1 path."""

    def test_r2_n_all_four_sites_identical_with_tracing_off(self):
        s, a, h, l = SchedulingSite(), AcceptanceSite(), HandoffSite(), LocalitySite()
        cases = []
        for det, sem in ((True, True), (True, False), (False, True), (False, False)):
            d = [s.det(s.BR_A)] if det else []
            m = [s.sem(s.BR_B)] if sem else []
            _, on = s.run_site(ce_claims=d, sem_claims=m, trace=True)
            _, off = s.run_site(ce_claims=d, sem_claims=m, trace=False)
            cases.append((f"sched {det}/{sem}", [(r.day_iso, r.time_str) for r in on],
                                                 [(r.day_iso, r.time_str) for r in off]))
        for claims, label in (([h.claim(True)], "handoff+"), ([h.claim(False)], "handoff-"),
                              ([], "handoff0")):
            _, on = h.run_site(claims, trace=True); _, off = h.run_site(claims, trace=False)
            cases.append((label, on, off))
        for allows, result in ((True, "ALLOW"), (False, "HOLD"), (False, "DENY")):
            _, on = a.run_site([a.det()], allows=allows, result=result, trace=True)
            _, off = a.run_site([a.det()], allows=allows, result=result, trace=False)
            cases.append((result, (on.result, on.allows), (off.result, off.allows)))
        for st in ("APPROXIMATE", "EXACT", "AMBIGUOUS", "NONE"):
            best = "Berazategui" if st not in ("NONE",) else None
            _, on = l.run_site(l.match(st, best), trace=True)
            _, off = l.run_site(l.match(st, best), trace=False)
            cases.append((f"loc {st}", (on.status, on.reason), (off.status, off.reason)))
        for label, on, off in cases:
            with self.subTest(case=label):
                self.assertEqual(on, off)
        self.assertEqual(len(cases), 14)
