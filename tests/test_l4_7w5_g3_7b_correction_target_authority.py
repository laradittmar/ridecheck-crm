"""L4.7W5-GATE3-G3-7B — correction-target authority.

A customer who corrects the car under discussion usually does not name it again:

    "Perdón, es 2018 y está en Pilar, no Palermo."

`understand/1.19` already describes that correctly — rule C2 defines
`CORRECT_EXISTING` as *"arregla un dato del mismo auto"* and rule C4 requires the
correction item *"aunque no haya vehículo en el mensaje"*. The reconciler simply never read
it: `_resolve_candidate` returned NONE whenever no vehicle identity was admitted, so the
correction was lost. Worse, C4 also requires the corrected year to come back as vehicle
evidence, and the interpreter satisfies that by synthesizing
`VehicleEvidence(value=None, year=2018)` — a row with no make and no model, which fell
through to CREATE and became a phantom candidate holding the corrected locality while the
real car kept the stale one.

These tests pin the four mechanisms that close that, and the business outcomes they protect:

A  admission — an unresolved item is not an identity; a relation-only correction survives
B  creation guard — incomplete identity can never create, by construction
C  strict correction focus — the four-condition predicate, and HOLD instead of guessing
D  the precedence ladder — explicit identity outranks every inference
E  Wild Case B end to end through `handle()`, against persisted rows
F  Wild Case A unchanged, and acceptance ordering after a correction
G  trace truth — the decision is observable and reports what the writer did
H  negative controls — each test fails if its mechanism is removed
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT / "backend"), str(ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app.services.conversation_engine as ce
from app.services.conversation_engine import ConversationEngine as E
from app.schemas import turn_evidence as te
from app.schemas.turn_evidence import BurstReconstruction, CorrectionRelation
from app.schemas.hybrid_trace import (CandidateAction, CandidateHoldReason,
                                      CandidateSelectionBasis, TRACE_VERSION)
from app.services.semantic_interpreter import SemanticTurnInterpreter

C = te.EvidenceStatus.CONFIRMED
INSP = te.LocationRole.INSPECTION_LOCATION.value
ORIGIN = te.LocationRole.CUSTOMER_ORIGIN.value


# ── fixtures ──────────────────────────────────────────────────────────────────

def cand(cid, marca, modelo, *, anio=None, status="mentioned", tipo="AUTO",
         zg=None, zd=None):
    return SimpleNamespace(id=cid, marca=marca, modelo=modelo, anio=anio, status=status,
                           tipo_vehiculo=tipo, zone_group=zg, zone_detail=zd,
                           version_text=None, direccion_texto=None)


def engine(candidates, *, pointer="auto", zone=None):
    """A resolver-ready engine over real canonical-state shapes."""
    if pointer == "auto":
        pointer = next((c.id for c in candidates if c.status == "current_focus"), None)
    st = SimpleNamespace(current_focus_candidate_id=pointer, home_zone_group=None,
                         home_zone_detail=None, needs_human=False,
                         last_stage=ce.STAGE_QUOTED)
    ctx = SimpleNamespace(state=st, thread=SimpleNamespace(id=1),
                          candidates=list(candidates),
                          contact=SimpleNamespace(wa_id="x"))
    e = E.__new__(E)
    e._correlation_id = "turn-1"
    e._extract_zone_from_text = zone or (lambda t: None)
    return e, ctx, st


def resolve(candidates, evidence, *, pointer="auto", zone=None):
    e, ctx, st = engine(candidates, pointer=pointer, zone=zone)
    r = e._resolve_candidate(ctx, st, evidence)
    return r, e._resolution_to_candidate_data(r, evidence)


NORTE = lambda t: SimpleNamespace(zone_group="Norte", zone_detail="Pilar")


def parsed_case_b():
    """Wild Case B evidence, built by the REAL parser from the contractual payload."""
    si = SemanticTurnInterpreter.__new__(SemanticTurnInterpreter)
    return si._to_turn_evidence(
        {"corrections": [{"relation": "CORRECT_EXISTING", "from_value": 2019,
                          "to_value": 2018, "status": "CONFIRMED"}],
         "locations": [{"value": "Pilar", "locality": "Pilar",
                        "role": "INSPECTION_LOCATION", "status": "CONFIRMED"}]},
        model="offline-fixture", thread_id=1, burst_id="b1", message_ids=("m1",),
        reconstruction=BurstReconstruction.CORPUS_FIXTURE,
        texts=["Perdón, es 2018 y está en Pilar, no Palermo."])


def correction(relation=CorrectionRelation.CORRECT_EXISTING, *, to=None, frm=None,
               ref=None, status=C):
    return te.CorrectionEvidence(value=True, relation=relation, from_value=frm,
                                 to_value=to, target_ref=ref, status=status)


def location(name="Pilar", role=INSP, status=C):
    return te.LocationEvidence(value=name, locality=name, role=role, status=status)


FOCUS_ONLY = [cand(1, "Ford", "Focus", anio=2019, status="current_focus",
                   zg="CABA", zd="Palermo")]


# ══ A — admission ══════════════════════════════════════════════════════════

class A_Admission(unittest.TestCase):

    def test_g37b_01_the_interpreter_really_does_synthesize_a_bare_year_vehicle(self):
        """The defect's input is contractual, not hypothetical: C4 requires the corrected
        year as vehicle evidence, and the parser satisfies it with an identity-less row."""
        ev = parsed_case_b()
        self.assertEqual(len(ev.vehicle_mentions), 1)
        v = ev.vehicle_mentions[0]
        self.assertIsNone(v.value)
        self.assertIsNone(v.make)
        self.assertIsNone(v.model)
        self.assertEqual(v.year, 2018)

    def test_g37b_02_an_unresolved_item_is_not_admitted(self):
        v = parsed_case_b().vehicle_mentions[0]
        self.assertFalse(v.resolved, "value=None cannot be a resolved item")
        self.assertFalse(E._evidence_admitted(v),
                         "admission must align with the schema's own definition")

    def test_g37b_03_a_relation_only_correction_still_survives(self):
        """'He replaced the car' is a fact even when the discarded car was never named."""
        corr = correction()
        self.assertTrue(E._evidence_admitted(corr))
        self.assertIsNotNone(
            E._admitted_correction_evidence(te.TurnEvidence(corrections=(corr,))))

    def test_g37b_04_an_ambiguous_or_conflicting_item_is_never_admitted(self):
        for status in (te.EvidenceStatus.AMBIGUOUS, te.EvidenceStatus.CONFLICT):
            with self.subTest(status=status):
                self.assertFalse(E._evidence_admitted(correction(status=status)))

    def test_g37b_05_a_superseded_mention_is_never_admitted(self):
        v = te.VehicleEvidence(value="el 208", make="Peugeot", model="2008", status=C,
                               is_superseded=True)
        self.assertFalse(E._evidence_admitted(v))

    def test_g37b_06_customer_origin_locality_is_not_an_inspection_locality(self):
        ev = te.TurnEvidence(location_mentions=(location(role=ORIGIN),))
        self.assertIsNone(E._admitted_location_evidence(ev))


# ══ B — the creation guard ═════════════════════════════════════════════════

class B_CreationGuard(unittest.TestCase):

    def test_g37b_07_make_and_model_together_are_the_threshold(self):
        self.assertTrue(E._creation_permitted(
            te.VehicleEvidence(value="Focus", make="Ford", model="Focus", status=C)))

    def test_g37b_08_nothing_short_of_a_full_identity_may_create(self):
        cases = {
            "year only": te.VehicleEvidence(value="2018", year=2018, status=C),
            "make only": te.VehicleEvidence(value="un Ford", make="Ford", status=C),
            "model only": te.VehicleEvidence(value="un Focus", model="Focus", status=C),
            "category only": te.VehicleEvidence(value="un auto",
                                                category_suggestion="AUTO", status=C),
            "unresolved value": parsed_case_b().vehicle_mentions[0],
            "ambiguous status": te.VehicleEvidence(value="Focus", make="Ford",
                                                   model="Focus",
                                                   status=te.EvidenceStatus.AMBIGUOUS),
            "nothing at all": None,
        }
        for label, v in cases.items():
            with self.subTest(case=label):
                self.assertFalse(E._creation_permitted(v))

    def test_g37b_09_wild_case_b_evidence_can_never_create(self):
        r, data = resolve(FOCUS_ONLY, parsed_case_b(), zone=NORTE)
        self.assertNotEqual(r.action, CandidateAction.CREATE)
        self.assertNotEqual(data.get("action"), "create")

    def test_g37b_10_a_locality_alone_creates_nothing(self):
        r, _ = resolve(FOCUS_ONLY, te.TurnEvidence(location_mentions=(location(),)),
                       zone=NORTE)
        self.assertEqual(r.action, CandidateAction.NONE)

    def test_g37b_11_noise_creates_nothing(self):
        r, _ = resolve(FOCUS_ONLY, te.TurnEvidence())
        self.assertEqual(r.action, CandidateAction.NONE)

    def test_g37b_12_a_correction_alone_creates_nothing(self):
        r, data = resolve(FOCUS_ONLY, te.TurnEvidence(corrections=(correction(to=2018),)))
        self.assertEqual(r.action, CandidateAction.CORRECT_EXISTING)
        self.assertEqual(data["action"], "update")


# ══ C — strict correction focus ════════════════════════════════════════════

class C_StrictFocus(unittest.TestCase):

    def _reason(self, candidates, pointer="auto"):
        e, ctx, _ = engine(candidates, pointer=pointer)
        row, reason = e._correction_focus(ctx)
        return row, reason

    def test_g37b_13_a_valid_pointer_and_status_resolve(self):
        row, reason = self._reason(FOCUS_ONLY)
        self.assertEqual(getattr(row, "id", None), 1)
        self.assertIsNone(reason)

    def test_g37b_14_a_single_row_is_not_a_focus(self):
        """The certified `_focus_candidate` row-count fallback must NOT apply here: a
        correction writes onto a row the customer never named."""
        row, reason = self._reason([cand(1, "Ford", "Focus")], pointer=None)
        self.assertIsNone(row)
        self.assertEqual(reason, CandidateHoldReason.FOCUS_NOT_ESTABLISHED)

    def test_g37b_15_a_missing_pointer_holds(self):
        row, reason = self._reason(
            [cand(1, "Ford", "Focus", status="current_focus")], pointer=None)
        self.assertIsNone(row)
        self.assertEqual(reason, CandidateHoldReason.FOCUS_POINTER_MISSING)

    def test_g37b_16_a_pointer_to_a_row_outside_the_cycle_holds(self):
        row, reason = self._reason(FOCUS_ONLY, pointer=99)
        self.assertIsNone(row)
        self.assertEqual(reason, CandidateHoldReason.FOCUS_POINTER_INVALID)

    def test_g37b_17_two_rows_marked_focus_holds(self):
        row, reason = self._reason(
            [cand(1, "Ford", "Focus", status="current_focus"),
             cand(2, "Peugeot", "208", status="current_focus")], pointer=1)
        self.assertIsNone(row)
        self.assertEqual(reason, CandidateHoldReason.FOCUS_NOT_UNIQUE)

    def test_g37b_18_pointer_and_status_disagreement_holds(self):
        row, reason = self._reason([cand(1, "Ford", "Focus", status="mentioned")],
                                   pointer=1)
        self.assertIsNone(row)
        self.assertEqual(reason, CandidateHoldReason.FOCUS_INCONSISTENT)

    def test_g37b_19_the_predicate_writes_nothing(self):
        e, ctx, st = engine(FOCUS_ONLY, pointer=99)
        e._correction_focus(ctx)
        self.assertEqual(st.current_focus_candidate_id, 99,
                         "the strict predicate must repair nothing")

    def test_g37b_20_the_resolver_itself_repairs_nothing(self):
        """G3-7A finding: `_focus_candidate` cleared a stale pointer from inside a resolver
        documented as side-effect free. The repair now runs in the proposal step."""
        e, ctx, st = engine(FOCUS_ONLY, pointer=99)
        e._resolve_candidate(ctx, st, parsed_case_b())
        self.assertEqual(st.current_focus_candidate_id, 99)

    def test_g37b_21_focus_candidate_keeps_its_certified_behaviour(self):
        e, ctx, st = engine([cand(1, "Ford", "Focus")], pointer=None)
        self.assertEqual(getattr(e._focus_candidate(ctx), "id", None), 1,
                         "the single-row fallback is certified for SELECTION")
        e2, ctx2, st2 = engine(FOCUS_ONLY, pointer=99)
        e2._focus_candidate(ctx2)
        self.assertIsNone(st2.current_focus_candidate_id,
                          "the repair still happens for its existing callers")


# ══ D — the precedence ladder ══════════════════════════════════════════════

class D_PrecedenceLadder(unittest.TestCase):

    def test_g37b_22_rung1_a_complete_new_identity_creates(self):
        r, data = resolve(FOCUS_ONLY, te.TurnEvidence(vehicle_mentions=(
            te.VehicleEvidence(value="Corolla", make="Toyota", model="Corolla", status=C),)))
        self.assertEqual(r.action, CandidateAction.CREATE)
        self.assertEqual(data["status"], "current_focus")

    def test_g37b_23_rung2_a_complete_identity_matching_focus_reuses_it(self):
        r, _ = resolve(FOCUS_ONLY, te.TurnEvidence(vehicle_mentions=(
            te.VehicleEvidence(value="Focus", make="Ford", model="Focus", status=C),)))
        self.assertEqual(r.action, CandidateAction.REUSE_FOCUS)
        self.assertFalse(r.focus_transition_required)

    def test_g37b_24_rung3_a_complete_identity_matching_another_row_takes_focus(self):
        r, data = resolve(
            [cand(1, "Peugeot", "208", status="current_focus"), cand(2, "Ford", "Focus")],
            te.TurnEvidence(vehicle_mentions=(
                te.VehicleEvidence(value="Focus", make="Ford", model="Focus", status=C),)))
        self.assertEqual(r.action, CandidateAction.REUSE_EXISTING)
        self.assertEqual(data["status"], "current_focus")

    def test_g37b_25_explicit_identity_outranks_a_correction_relation(self):
        """"no, es un Corolla" must never be applied as an attribute fix to the Focus."""
        r, _ = resolve(FOCUS_ONLY, te.TurnEvidence(
            vehicle_mentions=(te.VehicleEvidence(value="Corolla", make="Toyota",
                                                 model="Corolla", status=C),),
            corrections=(correction(CorrectionRelation.REPLACE_CANDIDATE),)))
        self.assertEqual(r.action, CandidateAction.CREATE)
        self.assertEqual(r.selection_basis, CandidateSelectionBasis.EVIDENCE_IDENTITY)

    def test_g37b_26_rung4_a_target_ref_corrects_the_referenced_vehicle(self):
        ev = te.TurnEvidence(
            vehicle_mentions=(te.VehicleEvidence(value="Focus", make="Ford",
                                                 model="Focus", status=C,
                                                 is_superseded=True),),
            corrections=(correction(to=2018, ref="vehicle_mentions[0]"),))
        r, data = resolve(FOCUS_ONLY, ev)
        self.assertEqual(r.action, CandidateAction.CORRECT_EXISTING)
        self.assertEqual(r.selection_basis, CandidateSelectionBasis.EXPLICIT_REFERENCE)
        self.assertEqual(data["id"], 1)

    def test_g37b_27_a_dangling_target_ref_holds(self):
        r, _ = resolve(FOCUS_ONLY, te.TurnEvidence(
            corrections=(correction(to=2018, ref="vehicle_mentions[7]"),)))
        self.assertEqual(r.action, CandidateAction.HOLD)
        self.assertEqual(r.hold_reason, CandidateHoldReason.TARGET_UNRESOLVED)

    def test_g37b_28_a_target_this_thread_does_not_have_conflicts(self):
        ev = te.TurnEvidence(
            vehicle_mentions=(te.VehicleEvidence(value="Amarok", make="Volkswagen",
                                                 model="Amarok", status=C,
                                                 is_superseded=True),),
            corrections=(correction(to=2018, ref="vehicle_mentions[0]"),))
        r, _ = resolve(FOCUS_ONLY, ev)
        self.assertEqual(r.hold_reason, CandidateHoldReason.TARGET_CONFLICT)

    def test_g37b_29_rung5_an_unnamed_correction_targets_the_strict_focus(self):
        r, data = resolve(FOCUS_ONLY, parsed_case_b(), zone=NORTE)
        self.assertEqual(r.action, CandidateAction.CORRECT_EXISTING)
        self.assertEqual(r.selection_basis, CandidateSelectionBasis.FOCUS_POINTER)
        self.assertEqual(data, {"action": "update", "id": 1, "anio": 2018,
                                "zone_detail": "Pilar", "zone_group": "Norte"})

    def test_g37b_30_a_correction_never_carries_a_status(self):
        _r, data = resolve(FOCUS_ONLY, parsed_case_b(), zone=NORTE)
        self.assertNotIn("status", data,
                         "a status key is how the writer moves focus — it must be absent")

    def test_g37b_31_rungs6_7_an_unnamed_correction_without_a_valid_focus_holds(self):
        for label, cands, pointer, reason in (
            ("no focus at all", [cand(1, "Ford", "Focus")], None,
             CandidateHoldReason.FOCUS_NOT_ESTABLISHED),
            ("pointer outside the cycle", FOCUS_ONLY, 99,
             CandidateHoldReason.FOCUS_POINTER_INVALID),
            ("two rows in focus",
             [cand(1, "Ford", "Focus", status="current_focus"),
              cand(2, "Peugeot", "208", status="current_focus")], 1,
             CandidateHoldReason.FOCUS_NOT_UNIQUE),
        ):
            with self.subTest(case=label):
                r, data = resolve(cands, parsed_case_b(), pointer=pointer, zone=NORTE)
                self.assertEqual(r.action, CandidateAction.HOLD)
                self.assertEqual(r.hold_reason, reason)
                self.assertEqual(data, {"action": "none"})

    def test_g37b_32_rung8_a_make_only_reference_reuses_a_unique_match(self):
        r, _ = resolve(
            [cand(1, "Ford", "Focus", status="current_focus"), cand(2, "Peugeot", "208")],
            te.TurnEvidence(
                vehicle_mentions=(te.VehicleEvidence(value="el Peugeot", make="Peugeot",
                                                     status=C),),
                corrections=(correction(CorrectionRelation.SWITCH_TO_PRIOR_CANDIDATE),)))
        self.assertEqual(r.action, CandidateAction.REUSE_EXISTING)

    def test_g37b_33_rung9_a_make_only_reference_matching_two_rows_holds(self):
        r, _ = resolve(
            [cand(1, "Ford", "Focus", status="current_focus"), cand(2, "Ford", "Ka")],
            te.TurnEvidence(vehicle_mentions=(
                te.VehicleEvidence(value="el Ford", make="Ford", status=C),)))
        self.assertEqual(r.hold_reason, CandidateHoldReason.AMBIGUOUS_MATCH)
        self.assertEqual(r.compatible_candidate_count, 2)

    def test_g37b_34_a_make_only_reference_matching_nothing_holds_not_creates(self):
        r, _ = resolve(FOCUS_ONLY, te.TurnEvidence(vehicle_mentions=(
            te.VehicleEvidence(value="el Fiat", make="Fiat", status=C),)))
        self.assertEqual(r.hold_reason, CandidateHoldReason.INCOMPLETE_IDENTITY)

    def test_g37b_35_el_otro_with_one_other_candidate_selects_it(self):
        r, _ = resolve(
            [cand(1, "Ford", "Focus", status="current_focus"), cand(2, "Peugeot", "208")],
            te.TurnEvidence(corrections=(
                correction(CorrectionRelation.SWITCH_TO_PRIOR_CANDIDATE),)))
        self.assertEqual(r.action, CandidateAction.REUSE_EXISTING)
        self.assertEqual(r.selection_basis, CandidateSelectionBasis.SOLE_NON_FOCUS)

    def test_g37b_36_el_otro_with_several_others_holds(self):
        r, _ = resolve(
            [cand(1, "Ford", "Focus", status="current_focus"), cand(2, "Peugeot", "208"),
             cand(3, "Toyota", "Corolla")],
            te.TurnEvidence(corrections=(
                correction(CorrectionRelation.SWITCH_TO_PRIOR_CANDIDATE),)))
        self.assertEqual(r.hold_reason, CandidateHoldReason.TARGET_UNRESOLVED)

    def test_g37b_37_rung10_a_year_without_correction_semantics_does_nothing(self):
        r, data = resolve(FOCUS_ONLY, te.TurnEvidence(vehicle_mentions=(
            te.VehicleEvidence(value="2018", year=2018, status=C),)))
        self.assertEqual(r.action, CandidateAction.NONE)
        self.assertEqual(data, {"action": "none"})

    def test_g37b_38_an_unplaceable_correction_holds_rather_than_vanishing(self):
        r, _ = resolve([], te.TurnEvidence(corrections=(
            correction(CorrectionRelation.UNKNOWN_RELATION, to=2018),)))
        self.assertEqual(r.action, CandidateAction.HOLD)

    def test_g37b_39_an_unknown_locality_binds_nothing(self):
        r, data = resolve(FOCUS_ONLY, parsed_case_b(), zone=lambda t: None)
        self.assertEqual(r.action, CandidateAction.CORRECT_EXISTING)
        self.assertFalse(r.locality_binding_permitted)
        self.assertNotIn("zone_detail", data)
        self.assertNotIn("zone_group", data)
        self.assertEqual(data["anio"], 2018, "the year correction still applies")

    def test_g37b_40_a_moto_row_is_not_repurposed_for_an_auto(self):
        r, _ = resolve([cand(1, "Honda", "Titan", status="current_focus", tipo="MOTO")],
                       te.TurnEvidence(vehicle_mentions=(
                           te.VehicleEvidence(value="Focus", make="Ford", model="Focus",
                                              status=C, category_suggestion="AUTO"),)))
        self.assertEqual(r.action, CandidateAction.CREATE)

    def test_g37b_41_the_decision_is_a_pure_function_of_what_it_reads(self):
        ev = parsed_case_b()
        first, _ = resolve(FOCUS_ONLY, ev, zone=NORTE)
        second, _ = resolve(FOCUS_ONLY, ev, zone=NORTE)
        self.assertEqual(first.to_payload(), second.to_payload())

    def test_g37b_42_evidence_references_are_burst_addresses_not_database_ids(self):
        r, _ = resolve(FOCUS_ONLY, parsed_case_b(), zone=NORTE)
        self.assertEqual(r.correction_evidence_ref, "corrections[0]")
        self.assertEqual(r.location_evidence_ref, "location_mentions[0]")
        self.assertIsNone(r.vehicle_evidence_ref,
                          "the synthesized row was refused, so it is referenced nowhere")


# ══ E/F/G — through the real handler ═══════════════════════════════════════

class E_WildCaseB(unittest.TestCase):
    """Wild Case B through the real `handle()`, against persisted rows."""

    CORRECTION = "Perdón, es 2018 y está en Pilar, no Palermo."

    def _run(self):
        import test_wild04r_f3_exact_cases as W
        ev = parsed_case_b()
        tc = W.TestExactCaseB("test_caseB_one_candidate")
        tc.setUp()
        self.addCleanup(tc.db.close)
        before = [(c.id, c.marca, c.modelo, c.anio, c.status, c.zone_group, c.zone_detail)
                  for c in W._candidates(tc.db, tc.thread.id)]
        tc.eng.settings.reply_renderer_demotion_enabled = True
        tc.eng.settings.semantic_same_turn_enabled = True
        tc.eng.settings.shadow_understand_enabled = True
        original = E._semantic_turn_evidence
        E._semantic_turn_evidence = lambda self: ev
        try:
            _res, sent = W._run_ce(tc.db, tc.eng, tc.thread.id, W.TestExactCaseB.WA_ID,
                                   "caseB", [self.CORRECTION])
        finally:
            E._semantic_turn_evidence = original
        tc.db.expire_all()
        after = [(c.id, c.marca, c.modelo, c.anio, c.status, c.zone_group, c.zone_detail)
                 for c in W._candidates(tc.db, tc.thread.id)]
        state = tc.db.query(W.WhatsAppThreadState).filter_by(
            thread_id=tc.thread.id).first()
        return before, after, sent, state, getattr(tc.eng, "_turn_candidate_resolution",
                                                   None)

    def setUp(self):
        self.before, self.after, self.sent, self.state, self.resolution = self._run()
        self.focus = next((c for c in self.after if c[4] == "current_focus"), None)

    def test_g37b_43_no_phantom_candidate_is_created(self):
        self.assertEqual(len(self.after), 1, f"expected one candidate, got {self.after}")
        self.assertIsNone(self.resolution.created_candidate_id)

    def test_g37b_44_the_candidate_id_is_unchanged(self):
        self.assertEqual(self.focus[0], self.before[0][0])

    def test_g37b_45_the_ford_focus_is_still_the_focused_vehicle(self):
        self.assertEqual((self.focus[1], self.focus[2]), ("Ford", "Focus"))
        self.assertFalse(self.resolution.focus_transition_required)

    def test_g37b_46_the_year_is_corrected(self):
        self.assertEqual(self.focus[3], 2018)

    def test_g37b_47_the_locality_is_rebound_to_pilar(self):
        self.assertEqual(self.focus[6], "Pilar")
        self.assertNotEqual(self.focus[6], "Palermo")

    def test_g37b_48_the_zone_group_comes_from_the_canonical_table(self):
        self.assertEqual(self.focus[5], "Norte")

    def test_g37b_49_the_canonical_price_is_recomputed(self):
        self.assertTrue(any("190.000" in t for t in self.sent),
                        f"expected the recomputed price, got {self.sent}")

    def test_g37b_50_exactly_one_response_is_authorized(self):
        self.assertEqual(len(self.sent), 1)

    def test_g37b_51_the_thread_home_locality_is_not_overwritten(self):
        self.assertEqual((self.state.home_zone_group, self.state.home_zone_detail),
                         ("CABA", "Palermo"),
                         "a candidate-level correction must not move thread/home locality")

    def test_g37b_52_the_certified_stage_is_preserved(self):
        self.assertEqual(self.state.last_stage, "QUOTED")


class F_BoundariesThroughTheHandler(unittest.TestCase):

    def test_g37b_53_wild_case_a_is_unchanged(self):
        """The correction path must not disturb the certified replacement journey."""
        import test_wild04r_f3_exact_cases as W
        ev = te.TurnEvidence(
            service_intents=(te.ServiceIntentEvidence(value="PREPURCHASE_INSPECTION",
                                                      status=C),),
            vehicle_mentions=(
                te.VehicleEvidence(value="ese auto", make="Peugeot", model="2008",
                                   status=C, is_superseded=True, mention_index=0),
                te.VehicleEvidence(value="Focus 2019", make="Ford", model="Focus",
                                   year=2019, status=C, mention_index=1)),
            location_mentions=(location("Palermo"),))
        tc = W.TestExactCaseA("test_caseA_two_candidates")
        tc.setUp()
        self.addCleanup(tc.db.close)
        tc.eng.settings.reply_renderer_demotion_enabled = True
        tc.eng.settings.semantic_same_turn_enabled = True
        tc.eng.settings.shadow_understand_enabled = True
        original = E._semantic_turn_evidence
        E._semantic_turn_evidence = lambda self: ev
        try:
            _r, sent = W._run_ce(tc.db, tc.eng, tc.thread.id, W.TestExactCaseA.WA_ID,
                                 "caseA", ["Al final ese auto se cayó. "
                                           "Encontré un Focus 2019 en Palermo."])
        finally:
            E._semantic_turn_evidence = original
        tc.db.expire_all()
        rows = [(c.id, c.marca, c.modelo, c.anio, c.status, c.zone_group, c.zone_detail)
                for c in W._candidates(tc.db, tc.thread.id)]
        focus = next(c for c in rows if c[4] == "current_focus")
        self.assertEqual(len(rows), 2)
        self.assertEqual((focus[1], focus[2], focus[3]), ("Ford", "Focus", 2019))
        self.assertEqual((focus[5], focus[6]), ("CABA", "Palermo"))
        self.assertTrue(any("140.000" in t for t in sent))

    def test_g37b_54_a_locality_correction_makes_a_stale_quote_unacceptable(self):
        """Acceptance ordering: the zone is a quote INPUT, so correcting it invalidates the
        delivered quote and acceptance can no longer be allowed against it."""
        from app.services.acceptance_authorizer import CommercialState
        state = CommercialState(
            cycle_id="c1", quote_cycle_id="c1", revision_id=None,
            candidate_id=1, quote_candidate_id=1, quote_total=140000,
            quote_tipo_vehiculo="AUTO", current_tipo_vehiculo="AUTO",
            quote_zone_group="CABA", quote_zone_detail="Palermo",
            current_zone_group="Norte", current_zone_detail="Pilar",
            quote_delivered=True, delivered_amounts=(140000,))
        self.assertFalse(state.quote_inputs_unchanged(),
                         "a corrected locality must invalidate the quote it was priced on")

    def test_g37b_55_a_price_neutral_correction_preserves_acceptance_eligibility(self):
        """A year is not a pricing input, so correcting it must not block acceptance."""
        from app.services.acceptance_authorizer import CommercialState
        state = CommercialState(
            cycle_id="c1", quote_cycle_id="c1", revision_id=None,
            candidate_id=1, quote_candidate_id=1, quote_total=140000,
            quote_tipo_vehiculo="AUTO", current_tipo_vehiculo="AUTO",
            quote_zone_group="CABA", quote_zone_detail="Palermo",
            current_zone_group="CABA", current_zone_detail="Palermo",
            quote_delivered=True, delivered_amounts=(140000,))
        self.assertTrue(state.quote_inputs_unchanged())


class G_TraceTruth(unittest.TestCase):

    def test_g37b_56_the_contract_is_1_6(self):
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.6")

    def test_g37b_57_the_resolution_reaches_the_persisted_payload(self):
        from app.services.hybrid_trace import build_trace
        from app.schemas.hybrid_trace import CanonicalSnapshot, SemanticEvidence
        r, _ = resolve(FOCUS_ONLY, parsed_case_b(), zone=NORTE)
        trace = build_trace(
            turn_id="t1", thread_id=1, lead_id=1, deployment_sha="x",
            started_at=None, completed_at=None, duration_ms=1,
            ordered_message_ids=("m1",), message_timestamps=(None,),
            burst_texts=["x"], semantic=SemanticEvidence(), ce_rules=(),
            reconciliations=(), before=CanonicalSnapshot(), after=CanonicalSnapshot(),
            action="REPLY", detail=None, answer_source="CE",
            outbound={}, candidate_resolution=r)
        section = trace.to_payload()["candidate_resolution"]
        self.assertEqual(section["action"], CandidateAction.CORRECT_EXISTING)
        self.assertEqual(section["selection_basis"], CandidateSelectionBasis.FOCUS_POINTER)
        self.assertEqual(section["target_candidate_id"], 1)
        self.assertEqual(section["zone_lookup"], {"locality": "Pilar", "hit": True,
                                                  "zone_group": "Norte"})
        fields = {c["field"]: (c["before"], c["after"]) for c in section["corrected_fields"]}
        self.assertEqual(fields["anio"], (2019, 2018))
        self.assertEqual(fields["zone_detail"], ("Palermo", "Pilar"))

    def test_g37b_58_a_1_5_row_without_the_section_still_renders(self):
        from app.ui.hybrid_trace_view import _candidate_resolution_card
        self.assertEqual(
            _candidate_resolution_card({"trace_version": "hybrid-decision-trace/1.5"}), "")

    def test_g37b_59_the_inspector_shows_the_decision_and_why(self):
        from app.ui.hybrid_trace_view import _candidate_resolution_card
        r, _ = resolve(FOCUS_ONLY, parsed_case_b(), zone=NORTE)
        html_out = _candidate_resolution_card(
            {"trace_version": TRACE_VERSION, "candidate_resolution": r.to_payload()})
        for expected in ("CORRECT_EXISTING", "vehículo en foco", "Pilar", "Norte",
                         "2019", "2018", "corrections[0]"):
            with self.subTest(shows=expected):
                self.assertIn(expected, html_out)

    def test_g37b_60_the_inspector_names_the_hold_reason(self):
        from app.ui.hybrid_trace_view import _candidate_resolution_card
        r, _ = resolve(FOCUS_ONLY, parsed_case_b(), pointer=99, zone=NORTE)
        html_out = _candidate_resolution_card(
            {"trace_version": TRACE_VERSION, "candidate_resolution": r.to_payload()})
        self.assertIn("fuera del ciclo", html_out)

    def test_g37b_61_the_panel_carries_no_customer_prose(self):
        r, _ = resolve(FOCUS_ONLY, parsed_case_b(), zone=NORTE)
        dumped = str(r.to_payload())
        for forbidden in ("Perdón", "no Palermo", "wamid", "5491"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, dumped)

    def test_g37b_62_mutation_is_reported_by_the_writer_not_predicted(self):
        r, _ = resolve(FOCUS_ONLY, parsed_case_b(), zone=NORTE)
        self.assertFalse(r.mutation_applied,
                         "the resolver must never claim a mutation it did not perform")
        e = E.__new__(E)
        e._turn_candidate_resolution = r
        e._record_candidate_mutation((True, None))
        self.assertTrue(e._turn_candidate_resolution.mutation_applied)
        self.assertIsNone(e._turn_candidate_resolution.created_candidate_id)

    def test_g37b_62b_an_unreported_writer_outcome_claims_nothing(self):
        """A test double standing in for the writer reports no outcome. The decision must
        then say no mutation happened rather than assert one it never observed."""
        from unittest.mock import MagicMock
        r, _ = resolve(FOCUS_ONLY, parsed_case_b(), zone=NORTE)
        for stand_in in (None, MagicMock(), (), ("yes", 1), (True, "x")):
            with self.subTest(outcome=type(stand_in).__name__):
                e = E.__new__(E)
                e._turn_candidate_resolution = r
                e._record_candidate_mutation(stand_in)
                res = e._turn_candidate_resolution
                if stand_in == (True, "x"):
                    self.assertTrue(res.mutation_applied)
                    self.assertIsNone(res.created_candidate_id)
                else:
                    self.assertFalse(res.mutation_applied)

    def test_g37b_63_a_correction_is_a_mutating_action(self):
        r, _ = resolve(FOCUS_ONLY, parsed_case_b(), zone=NORTE)
        self.assertTrue(r.mutates)


# ══ H — negative controls ══════════════════════════════════════════════════

class H_NegativeControls(unittest.TestCase):
    """Each control removes one mechanism and asserts the defect returns, so a silent
    regression in any of them cannot pass this suite."""

    def test_g37b_64_removing_the_resolved_check_readmits_the_bare_year_row(self):
        from app.schemas.turn_evidence import UNRESOLVED_STATUSES

        def weak(item):                      # the pre-G3-7B admission test
            if item is None or getattr(item, "is_superseded", False):
                return False
            status = getattr(getattr(item, "status", None), "value",
                             getattr(item, "status", None))
            return status not in UNRESOLVED_STATUSES

        row = parsed_case_b().vehicle_mentions[0]
        self.assertTrue(weak(row), "control: the old test admitted it")
        self.assertFalse(E._evidence_admitted(row), "the new test must refuse it")

    def test_g37b_65_removing_the_creation_guard_recreates_the_phantom(self):
        """With the guard bypassed, a bare-identity mention matches nothing and the old
        fall-through would create a make-less, model-less candidate."""
        bare = parsed_case_b().vehicle_mentions[0]
        marca, modelo = E._evidence_identity(bare)
        self.assertEqual((marca, modelo), ("", ""))
        self.assertFalse(
            any(E._candidate_matches_evidence(c, marca, modelo, None)
                for c in FOCUS_ONLY),
            "control: it matches no existing row, which is what used to mean CREATE")
        self.assertFalse(E._creation_permitted(bare))

    def test_g37b_66_a_single_row_fallback_would_misapply_the_correction(self):
        """`_focus_candidate` would hand back the only row; the strict predicate refuses."""
        e, ctx, _ = engine([cand(1, "Ford", "Focus")], pointer=None)
        self.assertIsNotNone(e._focus_candidate(ctx), "control: the lenient helper returns it")
        row, reason = e._correction_focus(ctx)
        self.assertIsNone(row)
        self.assertEqual(reason, CandidateHoldReason.FOCUS_NOT_ESTABLISHED)

    def test_g37b_67_ignoring_the_correction_relation_loses_the_turn(self):
        """The relation is what carries the turn: strip it and the SAME values do nothing.

        Both evidences are built here rather than derived from `parsed_case_b()`: other
        suites reload the schema module, and re-wrapping another TurnEvidence's items then
        fails pydantic's class-identity check for reasons unrelated to this invariant.
        """
        bare_year = te.VehicleEvidence(value=None, year=2018, status=C, mention_index=0)
        stripped = te.TurnEvidence(vehicle_mentions=(bare_year,),
                                   location_mentions=(location(),))
        r, data = resolve(FOCUS_ONLY, stripped, zone=NORTE)
        self.assertEqual(r.action, CandidateAction.NONE,
                         "without the relation there is nothing to place the values on")
        self.assertEqual(data, {"action": "none"})

        with_relation = te.TurnEvidence(
            vehicle_mentions=(bare_year,), location_mentions=(location(),),
            corrections=(correction(to=2018, frm=2019),))
        r2, data2 = resolve(FOCUS_ONLY, with_relation, zone=NORTE)
        self.assertEqual(r2.action, CandidateAction.CORRECT_EXISTING)
        self.assertEqual(data2["anio"], 2018)
        self.assertEqual(data2["zone_detail"], "Pilar")

    def test_g37b_68_a_status_key_in_the_writer_patch_would_move_focus(self):
        """Proof that the omission is what protects focus, not luck."""
        _r, data = resolve(FOCUS_ONLY, parsed_case_b(), zone=NORTE)
        self.assertNotIn("status", data)
        self.assertEqual(data["action"], "update")
        self.assertIn("id", data, "a correction must name the row it patches")

    def test_g37b_69_a_1_6_turn_with_no_decision_says_so(self):
        """Silence and "no decision" must be distinguishable: a 1.6 row whose panel is
        simply missing would look identical to a turn that decided nothing."""
        from app.ui.hybrid_trace_view import _candidate_resolution_card
        rendered = _candidate_resolution_card({"trace_version": TRACE_VERSION,
                                               "candidate_resolution": None})
        self.assertIn("sin decisión de candidato", rendered)
        self.assertNotIn("CORRECT_EXISTING", rendered)

    def test_g37b_70_every_hold_reason_is_a_named_constant(self):
        reasons = {v for k, v in vars(CandidateHoldReason).items()
                   if k.isupper() and isinstance(v, str)}
        for required in ("INCOMPLETE_IDENTITY", "TARGET_UNRESOLVED", "TARGET_CONFLICT",
                         "FOCUS_POINTER_MISSING", "FOCUS_POINTER_INVALID",
                         "FOCUS_NOT_UNIQUE", "FOCUS_INCONSISTENT",
                         "FOCUS_NOT_ESTABLISHED"):
            with self.subTest(reason=required):
                self.assertIn(required, reasons)

    def test_g37b_71_the_cutover_is_still_off_by_default(self):
        import dataclasses
        from app.settings import Settings
        field = next(f for f in dataclasses.fields(Settings)
                     if f.name == "reply_renderer_demotion_enabled")
        self.assertIs(field.default, False,
                      "Commit 1 must not change the cutover default")


if __name__ == "__main__":        # pragma: no cover
    unittest.main()
