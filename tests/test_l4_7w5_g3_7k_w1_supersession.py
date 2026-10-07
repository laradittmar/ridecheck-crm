"""G3-7K W1 — survivorship must be proven, never inferred from emission order.

ROOT CAUSE. `_admitted_vehicle_evidence` returned `sorted(mentions, key=mention_index)[-1]`
— last mention wins. That is sound only while `understand/1.20` rule V4 holds ("el que fue
descartado va con is_superseded=true. No los inviertas"), because `_evidence_admitted` then
excludes the discarded car. G3-7J-R2 proved the model breaks V4: for "Dejá el 208, tengo un
Focus 2020." it emitted BOTH cars with is_superseded=false and the DISCARDED 208 LAST, on
3/3 attempts. Last-wins therefore selected the car the customer had just rejected, carrying
a year the burst never states for it. In the deployed posture (grounding flag unset) that
reached `REUSE_FOCUS` on the Peugeot — a live wrong-vehicle mutation.

W1 adds two things and nothing else:
  A  a survivorship guard at the single admission authority: two or more genuinely distinct
     admitted vehicle identities in one burst, with no supersession marking that leaves
     exactly one standing, is `HOLD / UNMARKED_MULTI_IDENTITY`;
  B  `G4_NAMED_REPLACEMENT` — an explicit CONFIRMED REPLACE_CANDIDATE may ground the single
     incoming identity it names, when that car is absent from the thread, said literally by
     the customer and confirmed by the vehicle catalogue.

W2 (CURRENT/ALTERNATE referent production, B02 singleton collapse) and W3 (B06 resumption,
B09 coexistence, B05/B15 relation production) are deliberately NOT implemented here.

All evidence below is the stored typed payload captured in G3-7J and re-verified in
G3-7J-R2. No model call is made by this suite.
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


# ── builders: the exact shapes the interpreter emitted, nothing invented ──────────────
def V(make=None, model=None, year=None, st="CONFIRMED", idx=0, sup=False):
    return VehicleEvidence(field="vehicle", value=(make or model or year), status=E(st),
                           make=make, model=model, year=year, mention_index=idx,
                           is_superseded=sup)


def C(rel, to=None, frm=None, st="CONFIRMED", ref=None, scope="UNRESOLVED_REFERENT"):
    return CorrectionEvidence(field="correction", value=True, status=E(st), relation=R(rel),
                              target_scope=S(scope), target_ref=ref, from_value=frm,
                              to_value=to)


def TE(veh=(), corr=()):
    return TurnEvidence(schema_version="turn-evidence/1.4", interpreter="w1",
                        model_version="stored", turn=TurnRef(thread_id=1),
                        vehicle_mentions=tuple(veh), corrections=tuple(corr))


class Cand:
    """A canonical candidate row, as the resolver reads one."""

    def __init__(self, i, marca, modelo, anio, tipo="AUTO"):
        self.id, self.marca, self.modelo, self.anio = i, marca, modelo, anio
        self.tipo_vehiculo, self.status = tipo, "current_focus"
        self.zone_group = self.zone_detail = None


def engine(grounding=True):
    eng = CE.__new__(CE)
    eng._correlation_id = "w1"
    eng.settings = types.SimpleNamespace(
        reconciler_location_authority_enabled=True,
        correction_target_grounding_enabled=grounding,
        provisional_correction_enabled=False)
    return eng


def resolve(eng, cands, ev, burst):
    """The real resolver, over a real canonical burst reconstruction."""
    eng._canonical_turn_burst = types.SimpleNamespace(messages=list(burst))
    ctx = types.SimpleNamespace(thread=types.SimpleNamespace(id=1), candidates=cands)
    eng._focus_candidate = lambda c, repair=False: (cands[0] if cands else None)
    return eng._resolve_candidate(ctx, None, ev)


# ── the stored G3-7J bursts and payloads ─────────────────────────────────────────────
B01_BURST = ["Al final ese auto se cayó. Encontré un Focus 2019 en Palermo."]
B03_BURST = ["Al final ese auto se cayó. Tengo un Ford Focus 2019."]
B04_BURST = ["Dejá el 208, tengo un Focus 2020."]
B05_BURST = ["Al final el Focus se cayó. Tengo un Toyota Corolla 2020.",
             "¿Qué horarios tienen?"]
B15_BURST = ["En realidad el auto es un Ford Focus."]
N01_BURST = ["No, el otro es del 2016."]
N05_BURST = ["Ese no.", "El de Pilar."]
B02_BURST = ["Perdón, es 2018 y está en Pilar, no Palermo."]

B01_EV = TE([V("Ford", "Focus", 2019, "PROPOSED", 0)], [C("REPLACE_CANDIDATE", to="Focus 2019")])
B03_EV = TE([V("Ford", "Focus", 2019, "CONFIRMED", 0)],
            [C("REPLACE_CANDIDATE", to="Ford Focus 2019")])
#: B04 exactly as emitted on attempts 1 and 2 — the DISCARDED 208 last, neither superseded.
B04_EV_A12 = TE([V("Ford", "Focus", 2020, "PROPOSED", 0), V("Peugeot", "208", 2020, "PROPOSED", 1)],
                [C("REPLACE_CANDIDATE", to="Ford Focus", frm="Peugeot 208", st="PROPOSED")])
#: B04 attempt 3 — the discarded 208 is CONFIRMED while the surviving Focus is only PROPOSED.
B04_EV_A3 = TE([V("Ford", "Focus", 2020, "PROPOSED", 0), V(None, "208", 2020, "CONFIRMED", 1)],
               [C("REPLACE_CANDIDATE", to="Ford Focus", frm="208")])
B05_EV = TE([V("Toyota", "Corolla", 2020, "CONFIRMED", 0)], [])
B15_EV = TE([V("Ford", "Focus", None, "CONFIRMED", 0)], [])
N01_EV = TE([V(None, None, 2016, "PROPOSED", 0)], [C("CORRECT_EXISTING", to="2016", st="PROPOSED")])
B02_EV = TE([V(None, None, 2018, "CONFIRMED", 0)], [C("CORRECT_EXISTING", to="2018", frm="2014")])
N05_EV = TE([], [C("SWITCH_TO_PRIOR_CANDIDATE", to="El de Pilar", st="PROPOSED")])

ONE_208 = lambda: [Cand(1, "Peugeot", "208", 2018)]          # noqa: E731
ONE_2008 = lambda: [Cand(1, "Peugeot", "2008", 2014)]        # noqa: E731


# ═══ A · identity-set evaluation, in isolation ════════════════════════════════════════
class W1_IdentitySet(unittest.TestCase):
    """`_vehicle_identity_survivorship` on its own terms, with no resolver around it."""

    def test_w1_01_two_distinct_identities_prove_no_survivor(self):
        _s, n, _c = CE._vehicle_identity_survivorship(B04_EV_A12)
        self.assertEqual(n, 2, "B04 as emitted carries two distinct identities")

    def test_w1_02_duplicate_mentions_of_one_identity_collapse(self):
        ev = TE([V("Ford", "Focus", 2019, idx=0), V("Ford", "Focus", 2019, idx=1)])
        s, n, _c = CE._vehicle_identity_survivorship(ev)
        self.assertEqual(n, 1, "the same car mentioned twice is one identity, not two")
        self.assertEqual((s.make, s.model), ("Ford", "Focus"))

    def test_w1_03_explicit_supersession_leaves_exactly_one(self):
        ev = TE([V("Ford", "Focus", 2020, idx=0), V("Peugeot", "208", None, idx=1, sup=True)])
        s, n, _c = CE._vehicle_identity_survivorship(ev)
        self.assertEqual(n, 1, "V4 obeyed: the marked car is excluded and one survives")
        self.assertEqual((s.make, s.model), ("Ford", "Focus"))

    def test_w1_04_partial_identity_is_not_assumed_equivalent(self):
        ev = TE([V("Ford", "Focus", 2020, idx=0), V(None, "Focus", None, idx=1)])
        _s, n, _c = CE._vehicle_identity_survivorship(ev)
        self.assertEqual(n, 2, "nothing proves a bare model names the same car")

    def test_w1_05_bare_year_carries_no_identity(self):
        _s, n, _c = CE._vehicle_identity_survivorship(N01_EV)
        self.assertEqual(n, 0, "a year is an attribute, never an identity contest")

    def test_w1_06_two_years_for_one_identity_is_unproven(self):
        ev = TE([V("Ford", "Focus", 2019, idx=0), V("Ford", "Focus", 2020, idx=1)])
        _s, n, conflict = CE._vehicle_identity_survivorship(ev)
        self.assertEqual((n, conflict), (1, True))

    def test_w1_07_survivorship_ignores_emission_order(self):
        """The same two mentions in either order give the same answer."""
        a = TE([V("Ford", "Focus", 2020, idx=0), V("Peugeot", "208", 2020, idx=1)])
        b = TE([V("Peugeot", "208", 2020, idx=0), V("Ford", "Focus", 2020, idx=1)])
        self.assertEqual(CE._vehicle_identity_survivorship(a)[1],
                         CE._vehicle_identity_survivorship(b)[1])

    def test_w1_08_admission_authority_returns_none_when_contested(self):
        """The canonical admission helper — every handler inherits this."""
        self.assertIsNone(CE._admitted_vehicle_evidence(B04_EV_A12))
        self.assertIsNone(CE._admitted_vehicle_evidence(B04_EV_A3))

    def test_w1_09_admission_authority_still_serves_single_identity(self):
        v = CE._admitted_vehicle_evidence(B03_EV)
        self.assertEqual((v.make, v.model, v.year), ("Ford", "Focus", 2019))


# ═══ B · the real resolver ════════════════════════════════════════════════════════════
class W1_Resolver(unittest.TestCase):

    def test_w1_10_b04_holds_on_attempt_1_2_evidence(self):
        r = resolve(engine(), ONE_208(), B04_EV_A12, B04_BURST)
        self.assertEqual(r.action, A.HOLD)
        self.assertEqual(r.hold_reason, H.UNMARKED_MULTI_IDENTITY)
        self.assertIsNone(r.target_candidate_id, "no vehicle may be selected")

    def test_w1_11_b04_holds_on_attempt_3_evidence(self):
        r = resolve(engine(), ONE_208(), B04_EV_A3, B04_BURST)
        self.assertEqual((r.action, r.hold_reason), (A.HOLD, H.UNMARKED_MULTI_IDENTITY))

    def test_w1_12_b04_never_mutates_either_vehicle(self):
        for ev in (B04_EV_A12, B04_EV_A3):
            r = resolve(engine(), ONE_208(), ev, B04_BURST)
            self.assertFalse(r.mutates, "neither the Peugeot nor the Focus may be touched")
            self.assertIsNone(r.created_candidate_id)

    def test_w1_13_b01_progresses_to_the_named_focus(self):
        r = resolve(engine(), ONE_2008(), B01_EV, B01_BURST)
        self.assertEqual(r.action, A.CREATE)
        self.assertEqual(r.grounding_basis, G.G4_NAMED_REPLACEMENT)

    def test_w1_14_b03_progresses_to_the_named_focus(self):
        r = resolve(engine(), ONE_2008(), B03_EV, B03_BURST)
        self.assertEqual((r.action, r.grounding_basis), (A.CREATE, G.G4_NAMED_REPLACEMENT))

    def test_w1_15_n01_still_holds_and_never_takes_the_focus(self):
        cands = [Cand(1, "Ford", "Focus", 2019), Cand(2, "Peugeot", "208", 2016)]
        r = resolve(engine(), cands, N01_EV, N01_BURST)
        self.assertEqual(r.action, A.HOLD)
        self.assertNotEqual(r.target_candidate_id, 1, "the focused car must not be chosen")
        self.assertIsNone(r.target_candidate_id)

    def test_w1_16_b02_remains_held_in_w1(self):
        """W2 owns B02. The invented from-value is never authority."""
        r = resolve(engine(), [Cand(1, "Ford", "Focus", 2019)], B02_EV, B02_BURST)
        self.assertEqual(r.action, A.HOLD)

    def test_w1_17_n05_switch_behaviour_unchanged(self):
        cands = [Cand(1, "Ford", "Focus", 2019), Cand(2, "Peugeot", "208", 2016)]
        r = resolve(engine(), cands, N05_EV, N05_BURST)
        self.assertEqual(r.action, A.HOLD)

    def test_w1_18_b14_existing_candidate_reuse_still_correct(self):
        cands = [Cand(1, "Ford", "Focus", 2019), Cand(2, "Peugeot", "2008", None)]
        ev = TE([V("Peugeot", "2008", None, "CONFIRMED", 0)], [])
        r = resolve(engine(), cands, ev,
                    ["Al final volvamos con el Peugeot 2008 y está en San Miguel."])
        self.assertEqual(r.action, A.REUSE_EXISTING)
        self.assertEqual(r.target_candidate_id, 2)

    def test_w1_19_b05_unchanged_no_relation_no_new_authority(self):
        """W1 grants no authority to a bare mention: B05 behaves exactly as at baseline."""
        r = resolve(engine(), [Cand(1, "Ford", "Focus", 2019)], B05_EV, B05_BURST)
        self.assertEqual(r.grounding_basis, G.G1_IDENTITY,
                         "pre-existing literal-identity path, never G4")
        self.assertNotEqual(r.grounding_basis, G.G4_NAMED_REPLACEMENT)

    def test_w1_20_b15_unchanged_no_relation_no_new_authority(self):
        r = resolve(engine(), ONE_2008(), B15_EV, B15_BURST)
        self.assertNotEqual(r.grounding_basis, G.G4_NAMED_REPLACEMENT)

    def test_w1_21_valid_supersession_progresses(self):
        ev = TE([V("Ford", "Focus", 2020, "CONFIRMED", 0),
                 V("Peugeot", "208", None, "CONFIRMED", 1, sup=True)],
                [C("REPLACE_CANDIDATE", to="Ford Focus 2020")])
        r = resolve(engine(), ONE_208(), ev, ["Dejá el 208, tengo un Ford Focus 2020."])
        self.assertEqual((r.action, r.grounding_basis), (A.CREATE, G.G4_NAMED_REPLACEMENT))

    def test_w1_22_duplicate_same_identity_is_not_two_cars(self):
        ev = TE([V("Ford", "Focus", 2019, "CONFIRMED", 0),
                 V("Ford", "Focus", 2019, "CONFIRMED", 1)],
                [C("REPLACE_CANDIDATE", to="Ford Focus 2019")])
        r = resolve(engine(), ONE_2008(), ev, B03_BURST)
        self.assertEqual(r.action, A.CREATE)

    def test_w1_23_partial_identity_collision_holds(self):
        ev = TE([V("Ford", "Focus", 2020, "CONFIRMED", 0), V(None, "Focus", None, "CONFIRMED", 1)],
                [C("REPLACE_CANDIDATE", to="Ford Focus 2020")])
        r = resolve(engine(), ONE_208(), ev, ["Dejá el 208, tengo un Ford Focus 2020."])
        self.assertEqual((r.action, r.hold_reason), (A.HOLD, H.UNMARKED_MULTI_IDENTITY))

    def test_w1_24_guard_holds_in_the_deployed_grounding_off_posture(self):
        """The exposure W1 actually closes. At baseline this reached REUSE_FOCUS on the
        Peugeot — the discarded car — because the guard did not exist in this posture."""
        for ev in (B04_EV_A12, B04_EV_A3):
            r = resolve(engine(grounding=False), ONE_208(), ev, B04_BURST)
            self.assertEqual(r.action, A.HOLD)
            self.assertEqual(r.hold_reason, H.UNMARKED_MULTI_IDENTITY)
            self.assertIsNone(r.target_candidate_id)

    def test_w1_25_split_burst_and_single_message_decide_identically(self):
        one = ["Dejá el 208, tengo un Focus 2020."]
        two = ["Dejá el 208,", "tengo un Focus 2020."]
        a = resolve(engine(), ONE_208(), B04_EV_A12, one)
        b = resolve(engine(), ONE_208(), B04_EV_A12, two)
        self.assertEqual((a.action, a.hold_reason), (b.action, b.hold_reason))
        c = resolve(engine(), ONE_2008(), B03_EV, B03_BURST)
        d = resolve(engine(), ONE_2008(), B03_EV,
                    ["Al final ese auto se cayó.", "Tengo un Ford Focus 2019."])
        self.assertEqual((c.action, c.grounding_basis), (d.action, d.grounding_basis))


# ═══ C · handler boundary: what the canonical writer is actually handed ═══════════════
class W1_HandlerBoundary(unittest.TestCase):
    """`_resolution_to_candidate_data` is the writer's only input. The writer stays the
    sole mutation authority; W1 changes what it is told, never who writes."""

    def _data(self, cands, ev, burst, grounding=True):
        eng = engine(grounding)
        return eng._resolution_to_candidate_data(resolve(eng, cands, ev, burst), ev)

    def test_w1_26_b01_writer_input_is_the_named_ford_focus_2019(self):
        d = self._data(ONE_2008(), B01_EV, B01_BURST)
        self.assertEqual(d["action"], "create")
        self.assertEqual((d.get("marca"), d.get("modelo"), d.get("anio")),
                         ("Ford", "Focus", 2019))

    def test_w1_27_b03_writer_input_is_the_named_ford_focus_2019(self):
        d = self._data(ONE_2008(), B03_EV, B03_BURST)
        self.assertEqual((d["action"], d.get("marca"), d.get("modelo"), d.get("anio")),
                         ("create", "Ford", "Focus", 2019))

    def test_w1_28_b04_hands_the_writer_nothing_at_all(self):
        for ev in (B04_EV_A12, B04_EV_A3):
            for grounding in (True, False):
                d = self._data(ONE_208(), ev, B04_BURST, grounding)
                self.assertEqual(d, {"action": "none"},
                                 "no Peugeot mutation, no Focus mutation, no creation")

    def test_w1_29_b04_writer_input_never_names_the_discarded_car(self):
        for ev in (B04_EV_A12, B04_EV_A3):
            d = self._data(ONE_208(), ev, B04_BURST)
            self.assertNotIn("marca", d)
            self.assertNotIn("id", d)

    def test_w1_30_n01_and_b02_hand_the_writer_nothing(self):
        cands = [Cand(1, "Ford", "Focus", 2019), Cand(2, "Peugeot", "208", 2016)]
        self.assertEqual(self._data(cands, N01_EV, N01_BURST), {"action": "none"})
        self.assertEqual(self._data([Cand(1, "Ford", "Focus", 2019)], B02_EV, B02_BURST),
                         {"action": "none"})

    def test_w1_31_writer_remains_the_sole_mutation_authority(self):
        """W1 adds no write. The resolver is still side-effect free."""
        src = inspect.getsource(CE._resolve_candidate)
        for forbidden in ("self.db.add", "self.db.commit", "self.db.flush"):
            self.assertNotIn(forbidden, src)
        for fn in (CE._vehicle_identity_survivorship, CE._named_replacement_identity):
            s = inspect.getsource(fn)
            for forbidden in ("self.db", "commit(", "add("):
                self.assertNotIn(forbidden, s)


# ═══ D · trace / Inspector ════════════════════════════════════════════════════════════
class W1_Trace(unittest.TestCase):

    def test_w1_32_hold_reason_is_visible_in_the_payload(self):
        r = resolve(engine(), ONE_208(), B04_EV_A12, B04_BURST)
        p = r.to_payload()
        self.assertEqual(p["hold_reason"], "UNMARKED_MULTI_IDENTITY")
        self.assertEqual(p["surviving_identity_count"], 2)

    def test_w1_33_grounding_basis_is_visible_for_a_named_replacement(self):
        r = resolve(engine(), ONE_2008(), B03_EV, B03_BURST)
        p = r.to_payload()
        self.assertEqual(p["grounding_basis"], "G4_NAMED_REPLACEMENT")
        self.assertEqual(p["surviving_identity_count"], 1)

    def test_w1_34_trace_exposes_no_customer_text(self):
        """The whole point of a count: the Inspector learns WHY without the burst."""
        for ev, burst in ((B04_EV_A12, B04_BURST), (B03_EV, B03_BURST)):
            p = resolve(engine(), ONE_208(), ev, burst).to_payload()
            blob = repr(p).lower()
            for leak in ("dejá", "deja el", "tengo un", "al final", "encontré", "perdón"):
                self.assertNotIn(leak, blob)

    def test_w1_35_new_reason_and_basis_are_declared_contract_members(self):
        self.assertIn(G.G4_NAMED_REPLACEMENT, G.ALL)
        self.assertEqual(H.UNMARKED_MULTI_IDENTITY, "UNMARKED_MULTI_IDENTITY")

    def test_w1_36_rule_version_records_the_rule_change(self):
        """A replayed decision must be able to tell which rule set produced it."""
        self.assertEqual(CANDIDATE_RULE_VERSION, "v3")


# ═══ E · mutation proofs — does this suite actually catch the regressions? ════════════
class W1_MutationProofs(unittest.TestCase):
    """Each test installs the defect and asserts a W1 guarantee breaks. If a mutation
    passes silently, the suite is not protecting the invariant it claims to protect."""

    def _assert_mutation_detected(self, fn, label):
        with self.assertRaises(AssertionError, msg=f"MUTATION NOT CAUGHT: {label}"):
            fn()

    @staticmethod
    def _patched(name, replacement):
        """Swap a class attribute and restore the EXACT descriptor afterwards.

        `getattr(CE, name)` unwraps `staticmethod`/`classmethod`, so restoring that would
        leave a plain function on the class — and `self._name(...)` would then bind `self`
        as the first argument. Snapshot `__dict__` instead.
        """
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

    def test_w1_37_restoring_last_mention_wins_is_caught(self):
        def mutant():
            last_wins = classmethod(lambda cls, ev: sorted(
                [v for v in (getattr(ev, "vehicle_mentions", ()) or ())
                 if cls._evidence_admitted(v)],
                key=lambda v: getattr(v, "mention_index", 0))[-1]
                if (getattr(ev, "vehicle_mentions", ()) or ()) else None)
            with self._patched("_admitted_vehicle_evidence", last_wins):
                v = CE._admitted_vehicle_evidence(B04_EV_A12)
                assert v is None, "last-wins returned the discarded Peugeot"
        self._assert_mutation_detected(mutant, "last-mention-wins restored")

    def test_w1_38_removing_the_multi_identity_guard_is_caught(self):
        def mutant():
            always_one = classmethod(lambda cls, ev: (None, 1, False))
            with self._patched("_vehicle_identity_survivorship", always_one):
                r = resolve(engine(), ONE_208(), B04_EV_A12, B04_BURST)
                assert r.hold_reason == H.UNMARKED_MULTI_IDENTITY, "guard removed"
        self._assert_mutation_detected(mutant, "multi-identity guard removed")

    def test_w1_39_treating_proposed_as_replacement_authority_is_caught(self):
        """B04 attempt 1/2's correction is PROPOSED. If PROPOSED were accepted as
        replacement authority, a merely-proposed replacement could create."""
        ev = TE([V("Ford", "Focus", 2020, "CONFIRMED", 0)],
                [C("REPLACE_CANDIDATE", to="Ford Focus 2020", st="PROPOSED")])
        r = resolve(engine(), ONE_208(), ev, ["Dejá el 208, tengo un Ford Focus 2020."])
        self.assertNotEqual(r.grounding_basis, G.G4_NAMED_REPLACEMENT,
                            "PROPOSED is not replacement authority")

    def test_w1_40_mention_without_a_relation_never_self_grounds(self):
        ev = TE([V("Ford", "Focus", 2020, "CONFIRMED", 0)], [])
        eng = engine()
        self.assertIsNone(eng._named_replacement_identity(None, V("Ford", "Focus", 2020), "ford focus"))
        r = resolve(eng, ONE_208(), ev, ["Tengo un Ford Focus 2020."])
        self.assertNotEqual(r.grounding_basis, G.G4_NAMED_REPLACEMENT)

    def test_w1_41_disabling_grounding_does_not_recover_b04(self):
        """The stop condition: progression must never come from turning the flag off."""
        r_on = resolve(engine(True), ONE_208(), B04_EV_A12, B04_BURST)
        r_off = resolve(engine(False), ONE_208(), B04_EV_A12, B04_BURST)
        self.assertEqual(r_on.action, A.HOLD)
        self.assertEqual(r_off.action, A.HOLD, "flag posture must not unlock B04")

    def test_w1_42_treating_partial_identities_as_equivalent_is_caught(self):
        def mutant():
            # collapse every identity to its model, so ("ford","focus") == ("","focus")
            model_only = staticmethod(
                lambda item: ("", (getattr(item, "model", None) or "").strip().lower()))
            with self._patched("_evidence_identity", model_only):
                ev = TE([V("Ford", "Focus", 2020, idx=0), V(None, "Focus", None, idx=1)])
                _s, n, _c = CE._vehicle_identity_survivorship(ev)
                assert n == 2, "partial identity silently treated as equivalent"
        self._assert_mutation_detected(mutant, "partial identities auto-equivalent")

    def test_w1_43_catalogue_disagreement_blocks_self_grounding(self):
        """The make must be canonical. A model-asserted make the catalogue denies is not."""
        eng = engine()
        bogus = V("Fiat", "Focus", 2019)        # catalogue says Focus is a Ford
        self.assertIsNone(eng._named_replacement_identity(
            C("REPLACE_CANDIDATE", to="Fiat Focus 2019"), bogus, "tengo un fiat focus 2019"))

    def test_w1_44_unsaid_model_blocks_self_grounding(self):
        eng = engine()
        self.assertIsNone(eng._named_replacement_identity(
            C("REPLACE_CANDIDATE", to="Ford Focus"), V("Ford", "Focus", 2019),
            "al final ese auto se cayo."), "the model token was never said")

    def test_w1_45_to_value_must_name_the_surviving_identity(self):
        eng = engine()
        self.assertIsNone(eng._named_replacement_identity(
            C("REPLACE_CANDIDATE", to="Toyota Corolla"), V("Ford", "Focus", 2019),
            "tengo un ford focus 2019"), "relation and mention must concern one car")

    def test_w1_46_incomplete_identity_never_self_grounds(self):
        eng = engine()
        for inc in (V(None, "Focus", 2019), V("Ford", None, 2019), V(None, None, 2019)):
            self.assertIsNone(eng._named_replacement_identity(
                C("REPLACE_CANDIDATE", to="Ford Focus 2019"), inc, "ford focus 2019"))

    def test_w1_47_explicit_target_ref_is_left_to_the_reference_path(self):
        eng = engine()
        self.assertIsNone(eng._named_replacement_identity(
            C("REPLACE_CANDIDATE", to="Ford Focus 2019", ref="v1"), V("Ford", "Focus", 2019),
            "tengo un ford focus 2019"))


# ═══ F · W2 and W3 remain unimplemented, on purpose ══════════════════════════════════
class W1_ScopeFence(unittest.TestCase):

    def test_w1_48_no_target_scope_production_was_added(self):
        """W2. The reconciler still reads scope; it does not teach the model to emit it."""
        src = inspect.getsource(CE._vehicle_identity_survivorship) + \
            inspect.getsource(CE._named_replacement_identity)
        self.assertNotIn("CURRENT_REFERENT", src)
        self.assertNotIn("ALTERNATE_REFERENT", src)

    def test_w1_49_no_phrase_alias_or_exception_catalogue_was_added(self):
        src = (inspect.getsource(CE._vehicle_identity_survivorship)
               + inspect.getsource(CE._named_replacement_identity))
        for token in ("volvamos", "al final", "en realidad", "mejor el", "deja el",
                      "ALIASES", "SYNONYM", "_PHRASES"):
            self.assertNotIn(token, src)

    def test_w1_50_b02_singleton_collapse_was_not_implemented(self):
        r = resolve(engine(), [Cand(1, "Ford", "Focus", 2019)], B02_EV, B02_BURST)
        self.assertEqual(r.action, A.HOLD, "singleton collapse belongs to W2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
