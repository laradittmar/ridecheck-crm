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
        self.assertEqual(row.authority_verdict, ComparisonVerdict.COMPATIBLE)
        self.assertEqual(row.authority_result, "semantic")

    def test_site_02_incompatible_two_producers_is_conflict(self):
        e, out = self.run_site(ce_claims=[self.det(self.BR_A)],
                               sem_claims=[self.sem(self.BR_B)])
        row = row_for(e, self.SITE)[0]
        self.assertEqual(row.classification, Classification.CONFLICT)
        self.assertEqual(row.authority_verdict, ComparisonVerdict.INCOMPATIBLE)
        self.assertEqual(row.authority_result, "deterministic_conflict")

    def test_site_03_ce_only_is_single_producer(self):
        e, out = self.run_site(ce_claims=[self.det(self.BR_A)], sem_claims=[])
        row = row_for(e, self.SITE)[0]
        self.assertEqual(row.classification, Classification.SINGLE_PRODUCER)
        self.assertEqual(row.participating_sources, (DET,))
        self.assertIsNone(row.authority_verdict, "one producer compared nothing")

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
