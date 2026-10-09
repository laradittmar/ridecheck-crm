"""G3-7L — make W2's safe vehicle HOLDs reach the Vehicle clarification Flow, and close
the W2 review's F1 (the uniqueness gap had no behavioural protection).

ROOT CAUSE of the silence, which was NOT what either prior artifact said. The W2 closeout
and the W2 independent review both attributed it to `_clarification_disposition` requiring
two correction flags plus `cand_data["action"] == "update"`. That gate is real, but it only
feeds `other_confirmable_proposal`. The actual cause is that
`_turn_clarification_decision` asked exactly one question about the vehicle family — "is
the identity resolved?" — and answered it from CANONICAL COMPLETENESS. On
"No, el otro es del 2016." with one Ford Focus on file the answer is YES, the vehicle is
entirely resolved; what is unresolved is WHICH car the turn concerns. So the router was
told `RESOLVED` and routed `NO_CLARIFICATION_REQUIRED` in all three flag postures.

G3-7L adds a second, orthogonal question — `vehicle_decision_unresolved` — computed from
the turn's own `CandidateResolution` and from nothing else: no flag, no proposed database
mutation, no prose. Clarification authority and mutation authority stay separate: this
grants permission to ASK, never permission to write.
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import _clarification_store_double as DBL
from app.schemas.hybrid_trace import CandidateAction as A, CandidateHoldReason as H
from app.schemas.turn_evidence import (CorrectionEvidence, CorrectionRelation as R,
                                       CorrectionTargetScope as S, EvidenceStatus as E,
                                       TurnEvidence, TurnRef, VehicleEvidence)
from app.services.clarification_attempt import (ClarificationFamily,
                                                VEHICLE_CLARIFIABLE_HOLDS)
from app.services.clarification_router import (ClarificationReason, ClarificationRoute,
                                               clarification_decision)
from app.services.conversation_engine import ConversationEngine as CE
from app.services.vehicle_catalog import corroborate_vehicle_surface


def V(make=None, model=None, year=None, st="CONFIRMED", idx=0, sup=False):
    return VehicleEvidence(field="vehicle", value=(make or model or year), status=E(st),
                           make=make, model=model, year=year, mention_index=idx,
                           is_superseded=sup)


def C(rel="CORRECT_EXISTING", to=None, frm=None, st="CONFIRMED",
      scope="UNRESOLVED_REFERENT"):
    return CorrectionEvidence(field="correction", value=True, status=E(st), relation=R(rel),
                              target_scope=S(scope), target_ref=None, from_value=frm,
                              to_value=to)


def TE(veh=(), corr=()):
    return TurnEvidence(schema_version="turn-evidence/1.4", interpreter="g7l",
                        model_version="stored", turn=TurnRef(thread_id=1),
                        vehicle_mentions=tuple(veh), corrections=tuple(corr))


class Cand:
    def __init__(self, i, marca, modelo, anio, zd=None, status="current_focus"):
        self.id, self.marca, self.modelo, self.anio = i, marca, modelo, anio
        self.tipo_vehiculo, self.status = "AUTO", status
        self.zone_group, self.zone_detail = None, zd
        self.version_text = self.direccion_texto = None


#: The three postures. `None` means the attribute is absent entirely — the DEPLOYED value.
POSTURES = (True, False, None)

N01_BURST = ["No, el otro es del 2016."]
#: N01 exactly as the frozen model emits it: PROPOSED, no from_value, UNRESOLVED scope.
N01_EV = TE([V(None, None, 2016, "PROPOSED")], [C(to="2016", st="PROPOSED")])

FOCUS_2019 = lambda: [Cand(1, "Ford", "Focus", 2019, zd="Palermo")]      # noqa: E731
TWO_CANDS = lambda: [Cand(1, "Ford", "Focus", 2019),                     # noqa: E731
                     Cand(2, "Peugeot", "208", 2016, status="mentioned")]
P2008 = lambda: [Cand(1, "Peugeot", "2008", 2014)]                       # noqa: E731


def engine(cands, burst, grounding=None, provisional=False, needs_human=False,
           flow_outstanding=False, spent=frozenset()):
    eng = CE.__new__(CE)
    eng._correlation_id = "g7l"
    kw = dict(reconciler_location_authority_enabled=True,
              provisional_correction_enabled=provisional)
    if grounding is not None:
        kw["correction_target_grounding_enabled"] = grounding
    eng.settings = types.SimpleNamespace(**kw)
    eng._canonical_turn_burst = types.SimpleNamespace(messages=list(burst))
    state = types.SimpleNamespace(
        current_focus_candidate_id=next(
            (c.id for c in cands if c.status == "current_focus"), None),
        needs_human=needs_human, vehicle_fallback_flow_sent=flow_outstanding,
        location_fallback_flow_sent=False, home_zone_group=None, home_zone_detail=None,
        current_cycle_start_message_db_id=None, last_stage="QUOTED")
    ctx = types.SimpleNamespace(thread=types.SimpleNamespace(id=1), candidates=cands,
                                state=state, contact=types.SimpleNamespace(wa_id="x"))
    eng._focus_candidate = lambda c, repair=False: next(
        (x for x in cands if x.status == "current_focus"), None)
    DBL.install(eng)
    if spent:
        try:
            DBL.ask(eng, family=next(iter(spent)))
        except Exception:                      # the double's API varies; fall back below
            pass
    return eng, ctx, state


def decide(cands, ev, burst, **kw):
    """Resolver + clarification decision, through the real code on both sides."""
    eng, ctx, state = engine(cands, burst, **kw)
    eng._semantic_turn_evidence = lambda: ev
    r = eng._resolve_candidate(ctx, state, ev)
    data = eng._resolution_to_candidate_data(r, ev)
    eng._turn_candidate_resolution = r
    eng._turn_clarification_decision_obj = None
    eng._turn_clarification_decision_count = 0
    res, disp = eng._clarification_disposition(ctx, state, data)
    dec = eng._turn_clarification_decision(ctx, state, reconciled=data, resolution=res,
                                           disposition=disp, ce_vehicle=None,
                                           ce_location=False,
                                           current_turn_text=" ".join(burst))
    return r, data, dec, state, cands


# ══ A · F1 — behavioural protection for the uniqueness gap ════════════════════════════
class G7L_AmbiguityIsBehavioural(unittest.TestCase):
    """The W2 review proved the gap guard was protected only by `assertIn("_GAP_THRESHOLD")`.
    These assert the OUTCOME, using a real catalogue near-collision."""

    #: Independently reproduced: 40 catalogue model pairs sit within reach of the 0.87
    #: similarity threshold. This family is one of them.
    SURFACE = "tengo un c3 aircross"
    CLAIMED = "c5 aircross"

    def test_g7l_01_similarity_alone_would_admit_the_wrong_car(self):
        """The precondition that makes this test meaningful: without the gap test the
        score clears the threshold, so the guard is the only thing refusing."""
        from difflib import SequenceMatcher
        from app.services.vehicle_catalog import _HIGH_THRESHOLD, _norm
        score = SequenceMatcher(None, _norm("c3 aircross"), _norm(self.CLAIMED)).ratio()
        self.assertGreaterEqual(score, _HIGH_THRESHOLD,
                                "if this drops below threshold the test proves nothing")

    def test_g7l_02_the_uniqueness_gap_rejects_it(self):
        c = corroborate_vehicle_surface(self.CLAIMED, self.SURFACE)
        self.assertEqual(c.method, "NONE")
        self.assertEqual(c.reason, "AMBIGUOUS_SURFACE")
        self.assertLess(c.gap, 0.15)

    def test_g7l_03_an_ambiguous_surface_creates_and_updates_nothing(self):
        ev = TE([V("Citroen", "C5 Aircross", None)],
                [C("REPLACE_CANDIDATE", to="Citroen C5 Aircross")])
        for posture in POSTURES:
            with self.subTest(grounding=posture):
                r, d, _dec, state, cands = decide(P2008(), ev, [self.SURFACE],
                                                  grounding=posture)
                self.assertEqual(r.action, A.HOLD)
                self.assertEqual(d, {"action": "none"})
                self.assertIsNone(r.created_candidate_id)
                self.assertEqual(state.current_focus_candidate_id, 1, "focus must not move")
                self.assertEqual([(c.marca, c.modelo, c.anio) for c in cands],
                                 [("Peugeot", "2008", 2014)], "canonical state untouched")

    def test_g7l_04_no_quote_or_scheduling_progression_on_an_ambiguous_surface(self):
        ev = TE([V("Citroen", "C5 Aircross", None)],
                [C("REPLACE_CANDIDATE", to="Citroen C5 Aircross")])
        _r, _d, _dec, state, _c = decide(P2008(), ev, [self.SURFACE])
        self.assertEqual(state.last_stage, "QUOTED", "stage must not advance while unresolved")

    def test_g7l_05_a_unique_misspelling_still_corroborates(self):
        """The guard must not have become a blanket refusal."""
        c = corroborate_vehicle_surface("Focus", "tengo un foccus 2019")
        self.assertEqual(c.method, "FUZZY_UNIQUE")


# ══ B · clarification / mutation authority separation ═════════════════════════════════
class G7L_AuthoritySeparation(unittest.TestCase):

    def test_g7l_06_a_w2_hold_now_asks_the_vehicle_flow(self):
        for posture in POSTURES:
            with self.subTest(grounding=posture):
                r, d, dec, _s, _c = decide(FOCUS_2019(), N01_EV, N01_BURST,
                                           grounding=posture)
                self.assertEqual(r.action, A.HOLD)
                self.assertEqual(d, {"action": "none"}, "still no mutation authority")
                self.assertEqual(dec.route, ClarificationRoute.VEHICLE_FLOW_REQUIRED)
                self.assertEqual(dec.family, ClarificationFamily.VEHICLE)

    def test_g7l_07_the_reason_distinguishes_target_from_identity(self):
        _r, _d, dec, _s, _c = decide(FOCUS_2019(), N01_EV, N01_BURST)
        self.assertEqual(dec.reason, ClarificationReason.VEHICLE_TARGET_UNRESOLVED,
                         "the identity IS known; the target is not")

    def test_g7l_08_clarification_never_grants_mutation(self):
        for posture in POSTURES:
            with self.subTest(grounding=posture):
                r, d, dec, state, cands = decide(TWO_CANDS(), N01_EV, N01_BURST,
                                                 grounding=posture)
                self.assertEqual(dec.route, ClarificationRoute.VEHICLE_FLOW_REQUIRED)
                self.assertFalse(r.mutates)
                self.assertEqual(d, {"action": "none"})
                self.assertEqual([c.anio for c in cands], [2019, 2016],
                                 "2016 must never land on the focused Focus")

    def test_g7l_09_no_flag_is_required_to_see_a_w2_hold(self):
        """The decision must not read either correction flag to reach clarification."""
        import inspect
        src = inspect.getsource(CE._turn_clarification_decision)
        block = src[src.index("decision-level vehicle ambiguity"):
                    src.index("_vehicle_in_play = ")]
        self.assertNotIn("_correction_grounding_on", block)
        self.assertNotIn("_provisional_correction_on", block)
        # It may read the RESOLUTION's typed action; it must not read the proposed
        # database mutation (`reconciled` / `cand_data`), which is the gate W2's holds
        # could never satisfy because they correctly propose nothing.
        self.assertNotIn("reconciled", block)
        self.assertNotIn("cand_data", block)
        self.assertIn("_CA.HOLD", block, "eligibility is keyed on the typed decision")

    def test_g7l_10_eligibility_is_an_explicit_typed_set(self):
        self.assertIn(H.TARGET_SCOPE_UNRESOLVED, VEHICLE_CLARIFIABLE_HOLDS)
        self.assertIn(H.CONFLICTING_VEHICLE_ATTRIBUTES, VEHICLE_CLARIFIABLE_HOLDS)
        self.assertIn(H.UNMARKED_MULTI_IDENTITY, VEHICLE_CLARIFIABLE_HOLDS)
        self.assertIn(H.CREATE_UNCONFIRMED, VEHICLE_CLARIFIABLE_HOLDS)
        # deliberately NOT eligible: re-asking an identity we hold is not a question
        self.assertNotIn(H.CATEGORY_NOT_ESTABLISHED, VEHICLE_CLARIFIABLE_HOLDS)
        for focus_fault in (H.FOCUS_POINTER_MISSING, H.FOCUS_POINTER_INVALID,
                            H.FOCUS_NOT_UNIQUE, H.FOCUS_NOT_ESTABLISHED):
            self.assertNotIn(focus_fault, VEHICLE_CLARIFIABLE_HOLDS)

    def test_g7l_11_a_mutating_resolution_never_claims_decision_ambiguity(self):
        """A turn that WAS admitted must not also ask for clarification."""
        ev = TE([V(None, None, 2018)], [C(to="2018", frm=2019)])
        r, d, dec, _s, _c = decide(FOCUS_2019(), ev, ["Perdon, es 2018."], grounding=False)
        self.assertEqual(r.action, A.CORRECT_EXISTING)
        self.assertEqual(d["action"], "update")
        self.assertNotEqual(dec.route, ClarificationRoute.VEHICLE_FLOW_REQUIRED)

    def test_g7l_12_an_ineligible_hold_reason_does_not_ask(self):
        """Only the approved set routes. A category fault goes to a person."""
        route, reason = clarification_decision(
            vehicle_domain_relevant=True, vehicle_identity_resolved=True,
            vehicle_category_resolved=False, vehicle_decision_unresolved=False)
        self.assertEqual(route, ClarificationRoute.GENERIC_HUMAN_REQUIRED)
        self.assertEqual(reason, ClarificationReason.VEHICLE_CATEGORY_UNRESOLVED)

    def test_g7l_13_clarification_is_not_routed_merely_because_a_vehicle_was_mentioned(self):
        """A resolved, admitted vehicle mention asks nothing."""
        route, _reason = clarification_decision(
            vehicle_domain_relevant=True, vehicle_identity_resolved=True,
            vehicle_category_resolved=True, vehicle_decision_unresolved=False)
        self.assertEqual(route, ClarificationRoute.NO_CLARIFICATION_REQUIRED)


# ══ C · router ordering is preserved ══════════════════════════════════════════════════
class G7L_RouterOrdering(unittest.TestCase):

    BASE = dict(vehicle_domain_relevant=True, vehicle_identity_resolved=True,
                vehicle_category_resolved=True, vehicle_decision_unresolved=True)

    def test_g7l_14_human_ownership_still_stops_the_turn(self):
        """A person already owns the turn, so nothing is asked and nothing is re-handed
        off — the route is silence with the reason recorded, not a second escalation."""
        route, reason = clarification_decision(human_owns_turn=True, **self.BASE)
        self.assertNotEqual(route, ClarificationRoute.VEHICLE_FLOW_REQUIRED)
        self.assertEqual(reason, ClarificationReason.HUMAN_OWNS_TURN)

    def test_g7l_15_a_spent_attempt_escalates_instead_of_repeating(self):
        route, reason = clarification_decision(vehicle_attempt_spent=True, **self.BASE)
        self.assertEqual(route, ClarificationRoute.GENERIC_HUMAN_REQUIRED)
        self.assertEqual(reason, ClarificationReason.VEHICLE_ATTEMPT_SPENT)

    def test_g7l_16_an_outstanding_flow_waits_and_asks_nothing(self):
        route, reason = clarification_decision(vehicle_flow_outstanding=True, **self.BASE)
        self.assertEqual(route, ClarificationRoute.NO_CLARIFICATION_REQUIRED)
        self.assertEqual(reason, ClarificationReason.VEHICLE_FLOW_OUTSTANDING)

    def test_g7l_17_a_competing_confirmable_proposal_silences_the_flow(self):
        route, reason = clarification_decision(other_confirmable_proposal=True, **self.BASE)
        self.assertEqual(route, ClarificationRoute.GENERIC_HUMAN_REQUIRED)
        self.assertEqual(reason, ClarificationReason.PROPOSAL_WOULD_COEXIST)

    def test_g7l_18_an_irrelevant_domain_asks_nothing_even_when_unresolved(self):
        route, reason = clarification_decision(
            vehicle_domain_relevant=False, vehicle_decision_unresolved=True)
        self.assertEqual(route, ClarificationRoute.NO_CLARIFICATION_REQUIRED)
        self.assertEqual(reason, ClarificationReason.DOMAIN_NOT_RELEVANT)

    def test_g7l_19_vehicle_still_outranks_location(self):
        route, _r = clarification_decision(
            vehicle_domain_relevant=True, vehicle_decision_unresolved=True,
            location_domain_relevant=True, location_resolved=False)
        self.assertEqual(route, ClarificationRoute.VEHICLE_FLOW_REQUIRED)

    def test_g7l_20_location_behaviour_is_untouched(self):
        route, reason = clarification_decision(
            location_domain_relevant=True, location_resolved=False)
        self.assertEqual(route, ClarificationRoute.LOCATION_FLOW_REQUIRED)
        self.assertEqual(reason, ClarificationReason.LOCATION_UNRESOLVED)


# ══ D · the eligible families each reach the Flow ═════════════════════════════════════
class G7L_EligibleFamilies(unittest.TestCase):

    CASES = {
        "alternate/excluded target": (FOCUS_2019, N01_EV, N01_BURST),
        "conflicting attributes": (
            lambda: [Cand(1, "Peugeot", "208", 2018)],
            TE([V("Ford", "Focus", 2019, idx=0), V("Ford", "Focus", 2020, idx=1)],
               [C("REPLACE_CANDIDATE", to="Ford Focus")]), ["Tengo un Ford Focus."]),
        "two unmarked identities": (
            lambda: [Cand(1, "Peugeot", "208", 2018)],
            TE([V("Ford", "Focus", 2020, "PROPOSED", 0),
                V("Peugeot", "208", 2020, "PROPOSED", 1)],
               [C("REPLACE_CANDIDATE", to="Ford Focus", st="PROPOSED")]),
            ["Deja el 208, tengo un Focus 2020."]),
        "ungrounded identity": (
            P2008, TE([V("Ford", "Focus", 2019)],
                      [C("REPLACE_CANDIDATE", to="Ford Focus 2019")]),
            ["Quiero cambiar de auto."]),
    }

    def test_g7l_21_each_eligible_family_reaches_the_vehicle_flow(self):
        for label, (mk, ev, burst) in self.CASES.items():
            for posture in POSTURES:
                with self.subTest(case=label, grounding=posture):
                    r, d, dec, _s, _c = decide(mk(), ev, burst, grounding=posture)
                    self.assertEqual(r.action, A.HOLD)
                    self.assertIn(r.hold_reason, VEHICLE_CLARIFIABLE_HOLDS)
                    self.assertEqual(d, {"action": "none"})
                    self.assertEqual(dec.route, ClarificationRoute.VEHICLE_FLOW_REQUIRED)

    def test_g7l_22_human_request_authority_still_wins_over_every_family(self):
        for label, (mk, ev, burst) in self.CASES.items():
            with self.subTest(case=label):
                _r, _d, dec, _s, _c = decide(mk(), ev, burst, needs_human=True)
                self.assertNotEqual(dec.route, ClarificationRoute.VEHICLE_FLOW_REQUIRED)
                self.assertEqual(dec.reason, ClarificationReason.HUMAN_OWNS_TURN,
                                 "a person already owns the turn: ask nothing, and do "
                                 "not hand off a turn that is already handed off")


# ══ E · the flag matrix ═══════════════════════════════════════════════════════════════
class G7L_FlagMatrix(unittest.TestCase):
    """§9: the clarification path must not force a valid correction to HOLD just because
    broad grounding was activated — and must reach clarification without it."""

    MATRIX = ((False, False), (True, False), (False, True), (True, True), (None, False))

    def test_g7l_23_n01_holds_and_asks_in_every_flag_combination(self):
        for g, p in self.MATRIX:
            with self.subTest(grounding=g, provisional=p):
                r, d, dec, _s, cands = decide(FOCUS_2019(), N01_EV, N01_BURST,
                                              grounding=g, provisional=p)
                self.assertEqual(r.action, A.HOLD)
                self.assertEqual(d, {"action": "none"})
                self.assertEqual(dec.route, ClarificationRoute.VEHICLE_FLOW_REQUIRED)
                self.assertEqual([c.anio for c in cands], [2019])

    def test_g7l_24_a_valid_current_correction_is_not_forced_to_hold_by_clarification(self):
        """Grounding ON already held B02 before G3-7L (the G3-7F floor). What matters is
        that G3-7L did not ADD a hold where the deployed posture admits one."""
        ev = TE([V(None, None, 2018)], [C(to="2018", frm=2019)])
        for g, p in ((False, False), (None, False)):
            with self.subTest(grounding=g, provisional=p):
                r, d, _dec, _s, _c = decide(FOCUS_2019(), ev, ["Perdon, es 2018."],
                                            grounding=g, provisional=p)
                self.assertEqual(r.action, A.CORRECT_EXISTING)
                self.assertEqual(d["action"], "update")

    def test_g7l_25_a_named_current_vehicle_still_progresses(self):
        ev = TE([V("Ford", "Focus", 2022)], [C(to="2022")])
        for g, p in ((False, False), (None, False)):
            with self.subTest(grounding=g, provisional=p):
                r, d, _dec, _s, _c = decide(FOCUS_2019(), ev,
                                            ["El Ford Focus es del 2022."],
                                            grounding=g, provisional=p)
                self.assertIn(r.action, (A.REUSE_FOCUS, A.CORRECT_EXISTING))
                self.assertEqual(d["action"], "update")

    def test_g7l_26_a_fuzzy_unique_replacement_still_progresses_in_every_combination(self):
        ev = TE([V("Ford", "Focus", 2019)], [C("REPLACE_CANDIDATE", to="Ford Focus 2019")])
        for g, p in self.MATRIX:
            with self.subTest(grounding=g, provisional=p):
                r, d, _dec, _s, _c = decide(
                    P2008(), ev, ["Al final ese auto se cayo. Tengo un foccus 2019."],
                    grounding=g, provisional=p)
                self.assertEqual(r.action, A.CREATE)
                self.assertEqual(d["action"], "create")


# ══ F · hostile mutations (§11) ═══════════════════════════════════════════════════════
class G7L_MutationProofs(unittest.TestCase):

    @staticmethod
    def _patched(target, name, replacement):
        import contextlib

        @contextlib.contextmanager
        def _cm():
            original = target.__dict__.get(name, getattr(target, name))
            setattr(target, name, replacement)
            try:
                yield
            finally:
                setattr(target, name, original)
        return _cm()

    def _caught(self, fn, label):
        with self.assertRaises(AssertionError, msg=f"MUTATION NOT CAUGHT: {label}"):
            fn()

    def test_g7l_27_removing_the_uniqueness_gap_is_caught_behaviourally(self):
        """F1. This must fail on OUTCOME, not on a source scan."""
        from app.services import vehicle_catalog as VC

        def mutant():
            with self._patched(VC, "_GAP_THRESHOLD", -1.0):
                c = VC.corroborate_vehicle_surface("c5 aircross", "tengo un c3 aircross")
                assert c.method == "NONE", f"ambiguous surface admitted as {c.method}"
        self._caught(mutant, "uniqueness gap removed")

    def test_g7l_28_routing_every_semantic_vehicle_to_clarification_is_caught(self):
        def mutant():
            route, _r = clarification_decision(
                vehicle_domain_relevant=True, vehicle_identity_resolved=True,
                vehicle_category_resolved=True, vehicle_decision_unresolved=True)
            assert route == ClarificationRoute.NO_CLARIFICATION_REQUIRED, \
                "a resolved vehicle must not be clarified"
        self._caught(mutant, "every semantic vehicle routed to clarification")

    def test_g7l_29_restoring_action_update_as_the_only_gate_is_caught(self):
        def mutant():
            _r, d, dec, _s, _c = decide(FOCUS_2019(), N01_EV, N01_BURST)
            assert d.get("action") == "update" or \
                dec.route != ClarificationRoute.VEHICLE_FLOW_REQUIRED, \
                "a HOLD reached clarification without proposing an update"
        self._caught(mutant, "action=='update' restored as the only gate")

    def test_g7l_30_requiring_grounding_on_for_clarification_is_caught(self):
        for posture in (False, None):
            _r, _d, dec, _s, _c = decide(FOCUS_2019(), N01_EV, N01_BURST,
                                         grounding=posture)
            self.assertEqual(dec.route, ClarificationRoute.VEHICLE_FLOW_REQUIRED,
                             f"clarification must not need grounding ON (posture={posture})")

    def test_g7l_31_a_second_flow_in_the_same_cycle_is_caught(self):
        route, reason = clarification_decision(
            vehicle_domain_relevant=True, vehicle_decision_unresolved=True,
            vehicle_identity_resolved=True, vehicle_category_resolved=True,
            vehicle_attempt_spent=True)
        self.assertNotEqual(route, ClarificationRoute.VEHICLE_FLOW_REQUIRED)
        self.assertEqual(reason, ClarificationReason.VEHICLE_ATTEMPT_SPENT)

    def test_g7l_32_suppressing_escalation_after_a_spent_attempt_is_caught(self):
        route, _r = clarification_decision(
            vehicle_domain_relevant=True, vehicle_decision_unresolved=True,
            vehicle_identity_resolved=True, vehicle_category_resolved=True,
            vehicle_attempt_spent=True)
        self.assertEqual(route, ClarificationRoute.GENERIC_HUMAN_REQUIRED,
                         "a spent attempt must escalate, never go quiet")

    def test_g7l_33_mutating_the_candidate_before_flow_completion_is_caught(self):
        for posture in POSTURES:
            with self.subTest(grounding=posture):
                r, d, dec, _s, cands = decide(TWO_CANDS(), N01_EV, N01_BURST,
                                              grounding=posture)
                self.assertEqual(dec.route, ClarificationRoute.VEHICLE_FLOW_REQUIRED)
                self.assertEqual(d, {"action": "none"})
                self.assertEqual([(c.marca, c.anio) for c in cands],
                                 [("Ford", 2019), ("Peugeot", 2016)])

    def test_g7l_34_progressing_quote_while_clarification_pends_is_caught(self):
        _r, _d, dec, state, _c = decide(FOCUS_2019(), N01_EV, N01_BURST)
        self.assertEqual(dec.route, ClarificationRoute.VEHICLE_FLOW_REQUIRED)
        self.assertEqual(state.last_stage, "QUOTED", "no stage progression while unresolved")

    def test_g7l_35_an_unreadable_attempt_store_fails_closed_to_a_person(self):
        """Activation without the attempts table must not silently dispatch."""
        eng, ctx, state = engine(FOCUS_2019(), N01_BURST)
        eng._semantic_turn_evidence = lambda: N01_EV
        r = eng._resolve_candidate(ctx, state, N01_EV)
        eng._turn_candidate_resolution = r
        eng._turn_clarification_decision_obj = None
        eng._turn_clarification_decision_count = 0

        class _Broken:
            available = False
            fault = "OPERATIONAL"

            def spent_families(self, **_kw):
                from app.services.clarification_attempt import SpentFamiliesResult
                return SpentFamiliesResult(frozenset(), available=False,
                                           fault="OPERATIONAL")
        eng._attempt_store = lambda: _Broken()
        dec = eng._turn_clarification_decision(
            ctx, state, reconciled={"action": "none"}, resolution=r, disposition=None,
            ce_vehicle=None, ce_location=False, current_turn_text=" ".join(N01_BURST))
        self.assertEqual(dec.route, ClarificationRoute.GENERIC_HUMAN_REQUIRED)
        self.assertFalse(dec.store_available)


# ══ F2 · activation preflight (§8) ═══════════════════════════════════════════════════
class G7L_ActivationPreflight(unittest.TestCase):
    """The deployment gate: clarification must not be armed against a database that
    cannot guarantee one-attempt-per-cycle or duplicate-delivery idempotency."""

    def _ready(self, tables, uniques):
        from app.services.clarification_attempt import clarification_activation_ready

        class _Insp:
            def get_table_names(self):
                return list(tables)

            def get_unique_constraints(self, _t):
                return [{"column_names": list(c)} for c in uniques]

        class _DB:
            def get_bind(self):
                return object()

        import app.services.clarification_attempt as CA
        orig = CA._sa_inspect if hasattr(CA, "_sa_inspect") else None
        import sqlalchemy
        real = sqlalchemy.inspect
        sqlalchemy.inspect = lambda _b: _Insp()
        try:
            return clarification_activation_ready(_DB())
        finally:
            sqlalchemy.inspect = real
            if orig is not None:
                CA._sa_inspect = orig

    TABLE = "whatsapp_clarification_attempts"
    CYCLE = ("thread_id", "cycle_key", "family")
    IDEM = ("idempotency_key",)

    def test_g7l_39_an_absent_attempts_table_refuses_activation(self):
        r = self._ready(tables=["whatsapp_threads"], uniques=[])
        self.assertFalse(r.ready)
        self.assertEqual(r.reason, "ATTEMPTS_TABLE_ABSENT")

    def test_g7l_40_a_table_without_cycle_uniqueness_refuses(self):
        r = self._ready(tables=[self.TABLE], uniques=[self.IDEM])
        self.assertFalse(r.ready)
        self.assertEqual(r.reason, "CYCLE_UNIQUENESS_MISSING")

    def test_g7l_41_a_table_without_idempotency_uniqueness_refuses(self):
        r = self._ready(tables=[self.TABLE], uniques=[self.CYCLE])
        self.assertFalse(r.ready)
        self.assertEqual(r.reason, "IDEMPOTENCY_UNIQUENESS_MISSING")

    def test_g7l_42_a_fully_migrated_database_is_ready(self):
        r = self._ready(tables=[self.TABLE], uniques=[self.CYCLE, self.IDEM])
        self.assertTrue(r.ready)
        self.assertEqual(r.reason, "")

    def test_g7l_43_the_preflight_never_writes(self):
        import ast
        import inspect
        import textwrap
        from app.services.clarification_attempt import clarification_activation_ready
        code = textwrap.dedent(inspect.getsource(clarification_activation_ready))
        tree = ast.parse(code)
        calls = {n.func.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        for forbidden in ("add", "commit", "flush", "execute", "delete"):
            self.assertNotIn(forbidden, calls)


# ══ G · scope fence ═══════════════════════════════════════════════════════════════════
class G7L_ScopeFence(unittest.TestCase):

    def test_g7l_36_w2_protection_is_intact(self):
        """The whole point: 2016 never lands on the focused vehicle, any posture."""
        for mk in (FOCUS_2019, TWO_CANDS):
            for posture in POSTURES:
                with self.subTest(setup=mk.__name__, grounding=posture):
                    r, d, _dec, state, cands = decide(mk(), N01_EV, N01_BURST,
                                                      grounding=posture)
                    self.assertEqual(r.action, A.HOLD)
                    self.assertEqual(d, {"action": "none"})
                    self.assertEqual(state.current_focus_candidate_id, 1)
                    self.assertEqual(cands[0].anio, 2019)

    def test_g7l_37_b15_remains_an_open_w3_defect(self):
        ev = TE([V("Ford", "Focus", None)])
        r, d, _dec, _s, _c = decide(P2008(), ev, ["En realidad el auto es un Ford Focus."])
        self.assertEqual(r.action, A.CREATE, "unchanged: G3-7L did not fix B15")
        self.assertNotIn("anio", d, "the year loss is still open")

    def test_g7l_38_no_phrase_list_entered_the_clarification_decision(self):
        import ast
        import inspect
        import textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(CE._turn_clarification_decision)))
        node = tree.body[0]
        body = list(node.body)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)):
            body = body[1:]
        code = "\n".join(ast.unparse(st) for st in body).lower()
        for token in ("otro", "en realidad", "al final", "perdon", "deja el", "foccus"):
            self.assertNotIn(token, code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
