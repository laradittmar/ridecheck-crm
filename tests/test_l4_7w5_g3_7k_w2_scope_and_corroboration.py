"""G3-7K W2 — truthful attribute-conflict trace, target scope before legacy admission,
and tolerant-but-bounded vehicle corroboration.

MEASURED CONSTRAINT THIS IS BUILT AROUND. The W2 bounded evaluation spent its full
authorized budget (45 calls, temperature 0) on the frozen `gpt-4o-mini`. Across 17
attempts on three correction families and TWO prompt versions (`understand/1.20` and the
refined `1.21`, whose every correction example now carries `target_scope`), the model
returned `UNRESOLVED_REFERENT` for BOTH "No, el otro es del 2016." and "No, es del 2022."
— 10/10 then 7/7. Scope therefore cannot be taken from the model today.

What the model DOES give reliably is its hedge: the alternate-referent correction came
back `PROPOSED` on 8 of 8 attempts, while current-referent corrections came back
`CONFIRMED` on 8 of 11. That is G3-7D's recommended narrow rule — "PROPOSED may not
select a target by IMPLICIT focus" — and it is what the admission floor below rests on,
together with `from_value` checked against canonical state. No phrase, alias or spelling
variant is consulted anywhere.

B15's duplicate-candidate/year-loss defect is explicitly OUT of scope and remains open
(W3). Nothing here rewrites its expectation.
"""
from __future__ import annotations

import inspect
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.schemas.hybrid_trace import (CandidateAction as A, CandidateGroundingBasis as G,
                                      CandidateHoldReason as H, CANDIDATE_RULE_VERSION)
from app.schemas.turn_evidence import (CorrectionEvidence, CorrectionRelation as R,
                                       CorrectionTargetScope as S, EvidenceStatus as E,
                                       TurnEvidence, TurnRef, VehicleEvidence)
from app.services.conversation_engine import ConversationEngine as CE
from app.services.vehicle_catalog import corroborate_vehicle_surface


def V(make=None, model=None, year=None, st="CONFIRMED", idx=0, sup=False):
    return VehicleEvidence(field="vehicle", value=(make or model or year), status=E(st),
                           make=make, model=model, year=year, mention_index=idx,
                           is_superseded=sup)


def C(rel="CORRECT_EXISTING", to=None, frm=None, st="CONFIRMED", ref=None,
      scope="UNRESOLVED_REFERENT"):
    return CorrectionEvidence(field="correction", value=True, status=E(st), relation=R(rel),
                              target_scope=S(scope), target_ref=ref, from_value=frm,
                              to_value=to)


def TE(veh=(), corr=()):
    return TurnEvidence(schema_version="turn-evidence/1.4", interpreter="w2",
                        model_version="stored", turn=TurnRef(thread_id=1),
                        vehicle_mentions=tuple(veh), corrections=tuple(corr))


class Cand:
    def __init__(self, i, marca, modelo, anio, zd=None, status="current_focus"):
        self.id, self.marca, self.modelo, self.anio = i, marca, modelo, anio
        self.tipo_vehiculo, self.status = "AUTO", status
        self.zone_group, self.zone_detail = None, zd
        self.version_text = self.direccion_texto = None


#: The three postures §4.1 requires. `None` is the deployed value — the flag is unset.
POSTURES = (True, False, None)


def resolve(cands, ev, burst, grounding=True):
    eng = CE.__new__(CE)
    eng._correlation_id = "w2"
    settings = types.SimpleNamespace(reconciler_location_authority_enabled=True,
                                     provisional_correction_enabled=False)
    if grounding is not None:                 # unset == attribute absent entirely
        settings.correction_target_grounding_enabled = grounding
    eng.settings = settings
    eng._canonical_turn_burst = types.SimpleNamespace(messages=list(burst))
    state = types.SimpleNamespace(current_focus_candidate_id=next(
        (c.id for c in cands if c.status == "current_focus"), None))
    ctx = types.SimpleNamespace(thread=types.SimpleNamespace(id=1), candidates=cands,
                                state=state)
    eng._focus_candidate = lambda c, repair=False: next(
        (x for x in cands if x.status == "current_focus"), None)
    r = eng._resolve_candidate(ctx, state, ev)
    return r, eng._resolution_to_candidate_data(r, ev)


FOCUS_2019 = lambda: [Cand(1, "Ford", "Focus", 2019, zd="Palermo")]       # noqa: E731
TWO_CANDS = lambda: [Cand(1, "Ford", "Focus", 2019),                      # noqa: E731
                     Cand(2, "Peugeot", "208", 2016, status="mentioned")]
P208 = lambda: [Cand(1, "Peugeot", "208", 2018)]                          # noqa: E731
P2008 = lambda: [Cand(1, "Peugeot", "2008", 2014)]                        # noqa: E731

# ── durable, provenance-backed burst language (already authorized corpus cases) ───────
N01 = ["No, el otro es del 2016."]
B02 = ["Perdon, es 2018 y esta en Pilar, no Palermo."]
B04 = ["Deja el 208, tengo un Focus 2020."]
B01 = ["Al final ese auto se cayo. Encontre un Focus 2019 en Palermo."]
FOCCUS = ["Al final ese auto se cayo. Tengo un foccus 2019."]
COROLA = ["Al final ese auto se cayo. Tengo un corola 2020."]
NO_SURFACE = ["Quiero cambiar de auto."]

#: N01 exactly as the model emits it — PROPOSED, no from_value, UNRESOLVED scope (8/8).
N01_EV = TE([V(None, None, 2016, "PROPOSED")], [C(to="2016", st="PROPOSED")])


# ══ A · attribute-conflict trace truth ════════════════════════════════════════════════
class W2_AttributeConflict(unittest.TestCase):

    EV = TE([V("Ford", "Focus", 2019, idx=0), V("Ford", "Focus", 2020, idx=1)],
            [C("REPLACE_CANDIDATE", to="Ford Focus")])

    def test_w2_01_one_identity_two_years_is_not_a_multi_vehicle_ambiguity(self):
        for posture in POSTURES:
            with self.subTest(grounding=posture):
                r, _d = resolve(P208(), self.EV, ["Tengo un Ford Focus."], posture)
                self.assertEqual(r.action, A.HOLD)
                self.assertEqual(r.hold_reason, H.CONFLICTING_VEHICLE_ATTRIBUTES)
                self.assertNotEqual(r.hold_reason, H.UNMARKED_MULTI_IDENTITY)

    def test_w2_02_surviving_identity_count_stays_truthful(self):
        r, _d = resolve(P208(), self.EV, ["Tengo un Ford Focus."])
        self.assertEqual(r.surviving_identity_count, 1,
                         "one identity was named; the count must say so")

    def test_w2_03_two_distinct_identities_keep_the_multi_identity_reason(self):
        ev = TE([V("Ford", "Focus", 2020, "PROPOSED", 0),
                 V("Peugeot", "208", 2020, "PROPOSED", 1)],
                [C("REPLACE_CANDIDATE", to="Ford Focus", st="PROPOSED")])
        r, _d = resolve(P208(), ev, B04)
        self.assertEqual(r.hold_reason, H.UNMARKED_MULTI_IDENTITY)
        self.assertEqual(r.surviving_identity_count, 2)

    def test_w2_04_the_hold_is_still_fail_closed(self):
        r, d = resolve(P208(), self.EV, ["Tengo un Ford Focus."])
        self.assertFalse(r.mutates)
        self.assertEqual(d, {"action": "none"})

    def test_w2_05_the_reason_survives_into_the_payload(self):
        p = resolve(P208(), self.EV, ["Tengo un Ford Focus."])[0].to_payload()
        self.assertEqual(p["hold_reason"], "CONFLICTING_VEHICLE_ATTRIBUTES")
        self.assertEqual(p["surviving_identity_count"], 1)

    def test_w2_06_the_inspector_does_not_claim_two_vehicles(self):
        from app.ui import hybrid_trace_view as HV
        src = inspect.getsource(HV)
        i = src.index('"CONFLICTING_VEHICLE_ATTRIBUTES"')
        label = src[i:i + 260]
        self.assertIn("un solo veh", label, "the label must say ONE vehicle")
        self.assertNotIn("dos veh", label.split("ALTERNATE")[0])

    def test_w2_07_older_traces_still_render_their_own_reason(self):
        """Backward compatibility: a stored v3 row keeps its recorded classification."""
        from app.ui import hybrid_trace_view as HV
        src = inspect.getsource(HV)
        self.assertIn('"UNMARKED_MULTI_IDENTITY"', src)
        self.assertIn('"CONFLICTING_VEHICLE_ATTRIBUTES"', src)

    def test_w2_08_rule_version_records_the_change(self):
        self.assertEqual(CANDIDATE_RULE_VERSION, "v4")


# ══ B · target scope precedes legacy admission ════════════════════════════════════════
class W2_TargetScope(unittest.TestCase):

    def test_w2_09_n01_holds_in_all_three_postures(self):
        for posture in POSTURES:
            with self.subTest(grounding=posture):
                r, d = resolve(TWO_CANDS(), N01_EV, N01, posture)
                self.assertEqual(r.action, A.HOLD)
                self.assertEqual(d, {"action": "none"})

    def test_w2_10_n01_holds_even_as_the_only_stored_candidate(self):
        """§4.1: never updates the focused car merely because it is the only one."""
        for posture in POSTURES:
            with self.subTest(grounding=posture):
                r, d = resolve(FOCUS_2019(), N01_EV, N01, posture)
                self.assertEqual(r.action, A.HOLD)
                self.assertEqual(r.hold_reason, H.TARGET_SCOPE_UNRESOLVED)
                self.assertIsNone(r.target_candidate_id)
                self.assertEqual(d, {"action": "none"})

    def test_w2_11_n01_mutates_neither_candidate_nor_focus(self):
        for posture in POSTURES:
            with self.subTest(grounding=posture):
                r, _d = resolve(TWO_CANDS(), N01_EV, N01, posture)
                self.assertFalse(r.mutates)
                self.assertFalse(r.focus_transition_required)
                self.assertIsNone(r.created_candidate_id)

    def test_w2_12_an_explicit_alternate_scope_is_admitted_as_alternate(self):
        """Dormant against today's model, correct the moment it complies."""
        ev = TE([V(None, None, 2016, "PROPOSED")],
                [C(to="2016", st="PROPOSED", scope="ALTERNATE_REFERENT")])
        for posture in POSTURES:
            with self.subTest(grounding=posture):
                r, _d = resolve(FOCUS_2019(), ev, N01, posture)
                self.assertEqual(r.hold_reason, H.EXCLUDED_REFERENT,
                                 "G3-7F's established reason already means"
                                 " exactly this; W2 adds no synonym")

    def test_w2_13_an_alternate_scope_never_falls_back_to_focus(self):
        ev = TE([V(None, None, 2016)], [C(to="2016", scope="ALTERNATE_REFERENT")])
        r, _d = resolve(TWO_CANDS(), ev, N01)
        self.assertNotEqual(r.target_candidate_id, 1)
        self.assertIsNone(r.target_candidate_id)

    def test_w2_14_the_scope_guard_runs_before_the_flag_split(self):
        """Source-level: the guard must precede `_correction_grounding_on()`."""
        src = inspect.getsource(CE._resolve_candidate)
        self.assertLess(src.index("Target-Scope Invariant"),
                        src.index("if self._correction_grounding_on():"),
                        "scope safety must not depend on the flag")

    def test_w2_15_a_contradicting_from_value_holds_rather_than_being_ignored(self):
        """§4.2: an interpreter-invented `from_value` must not be silently dropped."""
        ev = TE([V(None, None, 2018)], [C(to="2018", frm=2014)])   # canonical anio is 2019
        for posture in POSTURES:
            with self.subTest(grounding=posture):
                r, d = resolve(FOCUS_2019(), ev, B02, posture)
                self.assertEqual(r.hold_reason, H.TARGET_SCOPE_CONTRADICTED)
                self.assertEqual(d, {"action": "none"})

    def test_w2_16_a_corroborating_from_value_establishes_the_current_referent(self):
        """The durable corpus caseB shape: `from_value` matches canonical state."""
        ev = TE([V(None, None, 2018)], [C(to="2018", frm=2019)])
        r, d = resolve(FOCUS_2019(), ev, B02, False)
        self.assertEqual(r.action, A.CORRECT_EXISTING)
        self.assertEqual(d["action"], "update")

    def test_w2_17_a_locality_from_value_also_corroborates(self):
        ev = TE([], [C(to="Pilar", frm="Palermo")])     # candidate zone_detail is Palermo
        r, _d = resolve(FOCUS_2019(), ev, B02, False)
        self.assertNotEqual(r.hold_reason, H.TARGET_SCOPE_CONTRADICTED)

    def test_w2_18_proposed_may_not_select_a_target_by_implicit_focus(self):
        """G3-7D's narrow rule, which is what separates N01 from a real correction."""
        hedged = TE([V(None, None, 2016, "PROPOSED")], [C(to="2016", st="PROPOSED")])
        firm = TE([V(None, None, 2022)], [C(to="2022")])
        self.assertEqual(resolve(FOCUS_2019(), hedged, N01, False)[0].action, A.HOLD)
        self.assertEqual(resolve(FOCUS_2019(), firm, ["No, es del 2022."], False)[0].action,
                         A.CORRECT_EXISTING)

    def test_w2_19_a_switch_relation_is_untouched_by_the_guard(self):
        """A switch SELECTS a referent and writes no attribute, so its rung still owns it."""
        ev = TE([], [C("SWITCH_TO_PRIOR_CANDIDATE", to="El de Pilar", st="PROPOSED")])
        r, _d = resolve(TWO_CANDS(), ev, ["Ese no.", "El de Pilar."], False)
        self.assertEqual(r.action, A.REUSE_EXISTING)

    def test_w2_20_an_explicit_target_ref_is_left_to_the_reference_path(self):
        self.assertIsNone(CE._unresolved_scope_hold(
            CE.__new__(CE), None, Cand(1, "Ford", "Focus", 2019),
            C(to="2018", ref="vehicle_mentions[0]"), None))

    def test_w2_21_scope_is_recorded_in_the_trace(self):
        p = resolve(FOCUS_2019(), N01_EV, N01)[0].to_payload()
        self.assertEqual(p["target_scope"], "UNRESOLVED_REFERENT")

    def test_w2_22_b04_explicit_replacement_still_holds_as_w1_left_it(self):
        ev = TE([V("Ford", "Focus", 2020, "PROPOSED", 0),
                 V("Peugeot", "208", 2020, "PROPOSED", 1)],
                [C("REPLACE_CANDIDATE", to="Ford Focus", st="PROPOSED")])
        for posture in POSTURES:
            with self.subTest(grounding=posture):
                r, d = resolve(P208(), ev, B04, posture)
                self.assertEqual(r.hold_reason, H.UNMARKED_MULTI_IDENTITY)
                self.assertEqual(d, {"action": "none"})

    def test_w2_23_a_valid_supersession_still_lets_the_incoming_car_win(self):
        ev = TE([V("Ford", "Focus", 2020, idx=0),
                 V("Peugeot", "208", None, idx=1, sup=True)],
                [C("REPLACE_CANDIDATE", to="Ford Focus 2020")])
        r, d = resolve(P208(), ev, ["Deja el 208, tengo un Ford Focus 2020."])
        self.assertEqual(r.action, A.CREATE)
        self.assertEqual((d["marca"], d["modelo"]), ("Ford", "Focus"))

    def test_w2_24_order_reversal_changes_nothing(self):
        a = TE([V("Ford", "Focus", 2020, "PROPOSED", 0),
                V("Peugeot", "208", 2020, "PROPOSED", 1)],
               [C("REPLACE_CANDIDATE", to="Ford Focus", st="PROPOSED")])
        b = TE([V("Peugeot", "208", 2020, "PROPOSED", 0),
                V("Ford", "Focus", 2020, "PROPOSED", 1)],
               [C("REPLACE_CANDIDATE", to="Ford Focus", st="PROPOSED")])
        ra, rb = resolve(P208(), a, B04)[0], resolve(P208(), b, B04)[0]
        self.assertEqual((ra.action, ra.hold_reason), (rb.action, rb.hold_reason))

    def test_w2_25_split_burst_matches_single_message(self):
        one = resolve(FOCUS_2019(), N01_EV, N01)[0]
        two = resolve(FOCUS_2019(), N01_EV, ["No,", "el otro es del 2016."])[0]
        self.assertEqual((one.action, one.hold_reason), (two.action, two.hold_reason))


# ══ C · tolerant but bounded corroboration ════════════════════════════════════════════
class W2_Corroboration(unittest.TestCase):

    def test_w2_26_exact_still_corroborates(self):
        c = corroborate_vehicle_surface("Focus", "tengo un focus 2019")
        self.assertEqual(c.method, "EXACT")

    def test_w2_27_a_unique_misspelling_corroborates(self):
        for modelo, text, in (("Focus", "tengo un foccus 2019"),
                              ("Corolla", "tengo un corola 2020")):
            with self.subTest(modelo=modelo):
                c = corroborate_vehicle_surface(modelo, text)
                self.assertEqual(c.method, "FUZZY_UNIQUE")
                self.assertGreaterEqual(c.score, 0.87)
                self.assertGreaterEqual(c.gap, 0.15)

    def test_w2_28_no_related_surface_is_refused(self):
        c = corroborate_vehicle_surface("Focus", "quiero cambiar de auto")
        self.assertEqual((c.method, c.reason), ("NONE", "NO_SURFACE"))

    def test_w2_29_a_contradicting_surface_is_refused(self):
        c = corroborate_vehicle_surface("Focus", "tengo un fiesta 2019")
        self.assertEqual(c.method, "NONE")

    def test_w2_30_closest_does_not_simply_win(self):
        """A near miss below the threshold must not be accepted as corroboration."""
        for modelo, text in (("Clio", "tengo un clyo"), ("Ka", "tengo un kaa")):
            with self.subTest(modelo=modelo):
                self.assertEqual(corroborate_vehicle_surface(modelo, text).method, "NONE")

    def test_w2_31_a_fuzzy_replacement_progresses(self):
        for burst, veh, to in ((FOCCUS, V("Ford", "Focus", 2019), "Ford Focus 2019"),
                               (COROLA, V("Toyota", "Corolla", 2020), "Toyota Corolla 2020")):
            with self.subTest(burst=burst[0][-18:]):
                ev = TE([veh], [C("REPLACE_CANDIDATE", to=to)])
                r, d = resolve(P2008(), ev, burst)
                self.assertEqual(r.action, A.CREATE)
                self.assertEqual(r.grounding_basis, G.G4_NAMED_REPLACEMENT)
                self.assertEqual(r.corroboration_method, "FUZZY_UNIQUE")
                self.assertEqual(d["action"], "create")

    def test_w2_32_an_exact_replacement_records_exact(self):
        ev = TE([V("Ford", "Focus", 2019, "PROPOSED")],
                [C("REPLACE_CANDIDATE", to="Focus 2019")])
        r, _d = resolve(P2008(), ev, B01)
        self.assertEqual(r.corroboration_method, "EXACT")
        self.assertTrue(r.literally_grounded)

    def test_w2_33_an_ungrounded_semantic_vehicle_creates_nothing_in_any_posture(self):
        """The phantom case. W1 guarded only the G4 path, leaving the DEPLOYED posture
        creating candidates for a car the customer never mentioned."""
        ev = TE([V("Ford", "Focus", 2019)], [C("REPLACE_CANDIDATE", to="Ford Focus 2019")])
        for posture in POSTURES:
            with self.subTest(grounding=posture):
                r, d = resolve(P2008(), ev, NO_SURFACE, posture)
                self.assertEqual(r.action, A.HOLD)
                self.assertEqual(d, {"action": "none"})
                self.assertEqual(r.corroboration_method, "NONE")

    def test_w2_34_corroboration_is_recorded_in_the_trace(self):
        ev = TE([V("Ford", "Focus", 2019)], [C("REPLACE_CANDIDATE", to="Ford Focus 2019")])
        p = resolve(P2008(), ev, FOCCUS)[0].to_payload()
        self.assertEqual(p["corroboration_method"], "FUZZY_UNIQUE")
        self.assertEqual(p["corroborated_identity"], "ford focus")
        self.assertIsNotNone(p["corroboration_score"])
        self.assertIsNotNone(p["corroboration_gap"])

    def test_w2_35_a_rejection_reason_is_recorded(self):
        ev = TE([V("Ford", "Focus", 2019)], [C("REPLACE_CANDIDATE", to="Ford Focus 2019")])
        p = resolve(P2008(), ev, NO_SURFACE)[0].to_payload()
        self.assertEqual(p["corroboration_reason"], "NO_SURFACE")

    def test_w2_36_no_phrase_catalogue_was_introduced(self):
        src = inspect.getsource(corroborate_vehicle_surface)
        for token in ("foccus", "corola", "peyo", "el otro", "ALIAS", "SYNONYM", "_PHRASES"):
            self.assertNotIn(token, src)

    def test_w2_37_corroboration_uses_the_existing_matcher_thresholds(self):
        from app.services import vehicle_catalog as VC
        src = inspect.getsource(VC.corroborate_vehicle_surface)
        self.assertIn("_HIGH_THRESHOLD", src)
        self.assertIn("_GAP_THRESHOLD", src)


# ══ D · mutation proofs (§8) ══════════════════════════════════════════════════════════
class W2_MutationProofs(unittest.TestCase):
    """Each installs the defect and asserts a W2 guarantee breaks."""

    @staticmethod
    def _patched(name, replacement):
        import contextlib

        @contextlib.contextmanager
        def _cm():
            original = CE.__dict__[name]
            setattr(CE, name, replacement)
            try:
                yield
            finally:
                setattr(CE, name, original)
        return _cm()

    def _caught(self, fn, label):
        with self.assertRaises(AssertionError, msg=f"MUTATION NOT CAUGHT: {label}"):
            fn()

    def test_w2_38_moving_the_target_guard_after_the_flag_is_caught(self):
        def mutant():
            src = inspect.getsource(CE._resolve_candidate)
            assert src.index("Target-Scope Invariant") < src.index(
                "if self._correction_grounding_on():"), "guard moved after the flag"
        src = inspect.getsource(CE._resolve_candidate)
        self.assertLess(src.index("Target-Scope Invariant"),
                        src.index("if self._correction_grounding_on():"))
        # and the behavioural consequence: holding N01 must not depend on the flag
        self.assertEqual(resolve(FOCUS_2019(), N01_EV, N01, False)[0].action, A.HOLD)

    def test_w2_39_admitting_an_alternate_referent_into_focus_is_caught(self):
        def mutant():
            with self._patched("_unresolved_scope_hold",
                               lambda self, ctx, focus, corr, veh: None):
                r, d = resolve(FOCUS_2019(), N01_EV, N01, False)
                assert r.action == A.HOLD, f"alternate admitted: {r.action} {d}"
        self._caught(mutant, "alternate referent admitted into focus")

    def test_w2_40_treating_an_attribute_conflict_as_multi_identity_is_caught(self):
        def mutant():
            # pretend a year conflict is an identity contest, as W1 did
            with self._patched("_vehicle_identity_survivorship",
                               classmethod(lambda cls, ev: (None, 2, False))):
                ev = TE([V("Ford", "Focus", 2019, idx=0), V("Ford", "Focus", 2020, idx=1)],
                        [C("REPLACE_CANDIDATE", to="Ford Focus")])
                r, _d = resolve(P208(), ev, ["Tengo un Ford Focus."])
                assert r.hold_reason == H.CONFLICTING_VEHICLE_ATTRIBUTES, "misclassified"
        self._caught(mutant, "attribute conflict reported as multi-identity")

    def test_w2_41_removing_fuzzy_corroboration_is_caught(self):
        def mutant():
            from app.services import vehicle_catalog as VC
            original = VC.corroborate_vehicle_surface
            try:
                VC.corroborate_vehicle_surface = lambda modelo, text: VC.SurfaceCorroboration(
                    "NONE", 0.0, None, None, 0.0, "NO_SURFACE")
                ev = TE([V("Ford", "Focus", 2019)],
                        [C("REPLACE_CANDIDATE", to="Ford Focus 2019")])
                r, _d = resolve(P2008(), ev, FOCCUS)
                assert r.action == A.CREATE, "misspelling no longer corroborates"
            finally:
                VC.corroborate_vehicle_surface = original
        self._caught(mutant, "fuzzy corroboration removed")

    def test_w2_42_accepting_an_ungrounded_semantic_vehicle_is_caught(self):
        def mutant():
            from app.services import vehicle_catalog as VC
            original = VC.corroborate_vehicle_surface
            try:
                VC.corroborate_vehicle_surface = lambda modelo, text: VC.SurfaceCorroboration(
                    "EXACT", 1.0, modelo, None, 1.0, "")
                ev = TE([V("Ford", "Focus", 2019)],
                        [C("REPLACE_CANDIDATE", to="Ford Focus 2019")])
                r, d = resolve(P2008(), ev, NO_SURFACE)
                assert r.action == A.HOLD, f"phantom created: {d}"
            finally:
                VC.corroborate_vehicle_surface = original
        self._caught(mutant, "ungrounded semantic vehicle accepted")

    def test_w2_43_accepting_an_ambiguous_fuzzy_match_is_caught(self):
        """A surface nearly as close to a second catalogue model must not corroborate."""
        from app.services import vehicle_catalog as VC
        tight = VC.corroborate_vehicle_surface("Corolla", "tengo un corola 2020")
        self.assertGreaterEqual(tight.gap, VC._GAP_THRESHOLD)

        def mutant():
            original = VC._GAP_THRESHOLD
            try:
                VC._GAP_THRESHOLD = 0.0       # "closest always wins"
                c = VC.corroborate_vehicle_surface("Clio", "tengo un clyo")
                assert c.method == "NONE", "ambiguous/weak surface accepted"
            finally:
                VC._GAP_THRESHOLD = original
        # a zero gap alone must not promote a sub-threshold score
        mutant()


# ══ E · scope fence — B15 and W3 stay open ════════════════════════════════════════════
class W2_ScopeFence(unittest.TestCase):

    def test_w2_44_b15_duplicate_candidate_defect_is_not_hidden(self):
        """B15 still CREATEs a second row for one car and still drops the year. W3 owns
        it; W2 must not paper over it."""
        ev = TE([V("Ford", "Focus", None)])
        r, d = resolve(P2008(), ev, ["En realidad el auto es un Ford Focus."])
        self.assertEqual(r.action, A.CREATE, "unchanged: W2 did not fix B15")
        self.assertEqual(d["action"], "create")
        self.assertNotIn("anio", d, "the year loss is still present and still open")

    @staticmethod
    def _code_only(fn):
        """Executable statements only — docstrings and comments stripped.

        A docstring may legitimately QUOTE the corpus example it explains; what must not
        appear is customer wording in code the reconciler executes. Scanning raw source
        conflates the two, so the scan is AST-based.
        """
        import ast
        import textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        node = tree.body[0]                   # the function itself, not the module
        body = list(getattr(node, "body", []))
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]                   # drop the docstring
        return "\n".join(ast.unparse(stmt) for stmt in body)

    def test_w2_45_no_target_scope_production_was_added_to_the_reconciler(self):
        """W2 admits scope; it does not infer it from wording."""
        code = (self._code_only(CE._unresolved_scope_hold)
                + self._code_only(CE._target_scope_value)
                + self._code_only(CE._from_value_matches_canonical)).lower()
        for token in ("otro", "no ese", "en realidad", "al final", "perdon", "deja el"):
            self.assertNotIn(token, code, f"{token!r} must not appear in executed code")


if __name__ == "__main__":
    unittest.main(verbosity=2)
