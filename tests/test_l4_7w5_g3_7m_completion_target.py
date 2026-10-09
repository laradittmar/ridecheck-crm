"""G3-7M — the Vehicle-Flow completion must resolve its target before it writes.

F1, the defect this closes. `_process_vehicle_fallback_response` wrote the customer's
confirmed identity onto `focus` unconditionally. In a multi-candidate thread that destroyed
the focused car: a customer clarifying "no, el otro es del 2016" and correctly selecting
their Peugeot had their Ford Focus overwritten into a second Peugeot 208, with the real
Peugeot left untouched beside it. Reproduced on real PostgreSQL before the fix.

The Flow hands back customer-confirmed STRUCTURED fields, so the target was always
resolvable — it was simply never asked for. `_resolve_completion_target` now answers it,
and ordering is the safety property: resolve, then write, never the reverse and never
"focus by default".

F2: the engine-side eligibility set had no behavioural test. F3: `_clarification_disposition`
raised `NameError` whenever both correction flags were armed.

Candidate-row tests here use the resolver directly against canonical-shaped rows; the
end-to-end writes, idempotency, stale-cycle and concurrency proofs run against real
PostgreSQL and are recorded in the closeout.
"""
from __future__ import annotations

import inspect
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import _clarification_store_double as DBL
from app.schemas.hybrid_trace import (CandidateAction as A, CandidateHoldReason as H,
                                      ClarificationCompletionTarget as T,
                                      CompletionTargetDecision)
from app.services.clarification_attempt import VEHICLE_CLARIFIABLE_HOLDS
from app.services.clarification_router import ClarificationRoute, ClarificationReason
from app.services.conversation_engine import ConversationEngine as CE

import test_l4_7w5_g3_7l_clarification_activation as G7L


class Row:
    """A canonical candidate row, as the resolver reads one."""

    def __init__(self, i, marca, modelo, anio=None, tipo="AUTO", status="mentioned"):
        self.id, self.marca, self.modelo, self.anio = i, marca, modelo, anio
        self.tipo_vehiculo, self.status = tipo, status


def resolve(rows, *, marca, modelo, anio=None, tipo="AUTO"):
    eng = CE.__new__(CE)
    ctx = types.SimpleNamespace(candidates=list(rows))
    return eng._resolve_completion_target(ctx, marca=marca, modelo=modelo,
                                          anio=anio, tipo=tipo)


# ══ A · F1 — the defect, as a permanent guard ═════════════════════════════════════════
class G7M_F1(unittest.TestCase):

    #: The exact shape reproduced on PostgreSQL at the parent commit.
    FOCUS_FORD = lambda self: [Row(1, "Ford", "Focus", 2019, status="current_focus"),
                               Row(2, "Peugeot", "208", None)]

    def test_g7m_01_the_other_car_is_selected_not_the_focused_one(self):
        d = resolve(self.FOCUS_FORD(), marca="Peugeot", modelo="208", anio=2016)
        self.assertEqual(d.outcome, T.MATCH_EXISTING_CANDIDATE)
        self.assertEqual(d.target_candidate_id, 2, "must select the Peugeot, not focus")
        self.assertEqual(d.action, "update")

    def test_g7m_02_the_focused_vehicle_is_never_the_default_target(self):
        d = resolve(self.FOCUS_FORD(), marca="Peugeot", modelo="208", anio=2016)
        self.assertNotEqual(d.target_candidate_id, 1,
                            "writing to focus by default is the F1 defect")

    def test_g7m_03_resolution_precedes_mutation_in_the_handler(self):
        """Ordering is the safety property, asserted on the source order of the two
        operations rather than on their effects, because an effect test cannot show
        that the resolve happens FIRST."""
        src = inspect.getsource(CE._process_vehicle_fallback_response)
        self.assertLess(src.index("_resolve_completion_target"),
                        src.index("_apply_candidate"),
                        "the target must be resolved before any write")

    def test_g7m_04_the_handler_has_no_write_to_focus_shortcut(self):
        src = inspect.getsource(CE._process_vehicle_fallback_response)
        self.assertNotIn("_apply_vehicle_identity(ctx, focus", src)
        self.assertNotIn("self.db.add(cand)", src, "no ad hoc ORM create remains")


# ══ B · §5 compatibility, example by example ══════════════════════════════════════════
class G7M_Compatibility(unittest.TestCase):

    def test_g7m_05_partial_candidate_is_completed_not_duplicated(self):
        d = resolve([Row(1, "Peugeot", "208", None, status="current_focus")],
                    marca="Peugeot", modelo="208", anio=2016)
        self.assertEqual((d.outcome, d.target_candidate_id),
                         (T.MATCH_EXISTING_CANDIDATE, 1))

    def test_g7m_06_an_exact_existing_candidate_is_selected(self):
        d = resolve([Row(1, "Peugeot", "208", 2016, status="current_focus")],
                    marca="Peugeot", modelo="208", anio=2016)
        self.assertEqual((d.outcome, d.target_candidate_id),
                         (T.MATCH_EXISTING_CANDIDATE, 1))

    def test_g7m_07_the_year_discriminates_between_two_same_model_rows(self):
        d = resolve([Row(1, "Peugeot", "208", 2016, status="current_focus"),
                     Row(2, "Peugeot", "208", 2020)],
                    marca="Peugeot", modelo="208", anio=2016)
        self.assertEqual((d.outcome, d.target_candidate_id),
                         (T.MATCH_EXISTING_CANDIDATE, 1))

    def test_g7m_08_two_yearless_same_model_rows_cannot_be_guessed(self):
        d = resolve([Row(1, "Peugeot", "208", None, status="current_focus"),
                     Row(2, "Peugeot", "208", None)],
                    marca="Peugeot", modelo="208", anio=2016)
        self.assertEqual(d.outcome, T.UNRESOLVED_TARGET)
        self.assertIsNone(d.target_candidate_id)
        self.assertEqual(d.reason, "MULTIPLE_COMPATIBLE_CANDIDATES")

    def test_g7m_09_a_genuinely_different_vehicle_creates(self):
        d = resolve([Row(1, "Ford", "Focus", 2019, status="current_focus")],
                    marca="Peugeot", modelo="208", anio=2016)
        self.assertEqual(d.outcome, T.CREATE_NEW_CANDIDATE)
        self.assertEqual(d.action, "create")

    def test_g7m_10_a_non_focused_partial_candidate_wins_over_focus(self):
        d = resolve([Row(1, "Ford", "Focus", 2019, status="current_focus"),
                     Row(2, "Peugeot", "208", None)],
                    marca="Peugeot", modelo="208", anio=2016)
        self.assertEqual(d.target_candidate_id, 2)

    def test_g7m_11_duplicate_representations_are_not_silently_chosen(self):
        d = resolve([Row(1, "Peugeot", "208", 2016, status="current_focus"),
                     Row(2, "Peugeot", "208", 2016)],
                    marca="Peugeot", modelo="208", anio=2016)
        self.assertEqual(d.outcome, T.UNRESOLVED_TARGET)

    def test_g7m_12_a_year_correction_on_the_only_car_is_not_a_new_car(self):
        """The year is a DISCRIMINATOR among several rows, never evidence of a different
        vehicle — otherwise "no, it's a 2016" duplicates the customer's only car."""
        d = resolve([Row(1, "Ford", "Focus", 2019, status="current_focus")],
                    marca="Ford", modelo="Focus", anio=2016)
        self.assertEqual((d.outcome, d.target_candidate_id),
                         (T.MATCH_EXISTING_CANDIDATE, 1))

    def test_g7m_13_a_moto_row_is_never_repurposed_for_a_car(self):
        d = resolve([Row(1, "Honda", "Titan", 2020, tipo="MOTO", status="current_focus")],
                    marca="Honda", modelo="Titan", anio=2020, tipo="AUTO")
        self.assertEqual(d.outcome, T.CREATE_NEW_CANDIDATE,
                         "category incompatibility must not update the MOTO row")

    def test_g7m_14_an_empty_placeholder_row_is_filled_not_duplicated(self):
        """§4.3: an identity is not "new" merely because the existing candidate is
        incomplete. A row carrying no make and no model is an empty slot, and the Flow is
        exactly what fills it — the first draft of this rule excluded such rows and
        duplicated the customer's own candidate."""
        d = resolve([Row(1, None, None, 2019, status="current_focus")],
                    marca="Peugeot", modelo="208", anio=2016)
        self.assertEqual((d.outcome, d.target_candidate_id),
                         (T.MATCH_EXISTING_CANDIDATE, 1))

    def test_g7m_14b_a_named_row_outranks_a_placeholder(self):
        d = resolve([Row(1, None, None, None, status="current_focus"),
                     Row(2, "Peugeot", "208", None)],
                    marca="Peugeot", modelo="208", anio=2016)
        self.assertEqual(d.target_candidate_id, 2, "the named Peugeot wins")

    def test_g7m_14c_two_placeholders_are_not_guessed_between(self):
        d = resolve([Row(1, None, None, None, status="current_focus"),
                     Row(2, None, None, None)],
                    marca="Peugeot", modelo="208", anio=2016)
        self.assertEqual(d.outcome, T.CREATE_NEW_CANDIDATE,
                         "ambiguous placeholders must not be chosen between")

    def test_g7m_15_selection_never_depends_on_order_or_recency(self):
        rows = [Row(1, "Ford", "Focus", 2019, status="current_focus"),
                Row(2, "Peugeot", "208", None)]
        a = resolve(rows, marca="Peugeot", modelo="208", anio=2016)
        b = resolve(list(reversed(rows)), marca="Peugeot", modelo="208", anio=2016)
        self.assertEqual(a.target_candidate_id, b.target_candidate_id)
        self.assertEqual(a.outcome, b.outcome)


# ══ C · trace truth ══════════════════════════════════════════════════════════════════
class G7M_Trace(unittest.TestCase):

    def test_g7m_16_the_decision_records_what_it_considered(self):
        d = resolve([Row(1, "Ford", "Focus", 2019, status="current_focus"),
                     Row(2, "Peugeot", "208", None)],
                    marca="Peugeot", modelo="208", anio=2016)
        p = d.to_payload()
        self.assertEqual(p["outcome"], T.MATCH_EXISTING_CANDIDATE)
        self.assertEqual(p["target_candidate_id"], 2)
        self.assertEqual(sorted(p["considered_candidate_ids"]), [1, 2])
        self.assertEqual(p["compatible_candidate_ids"], [2])
        self.assertEqual(p["action"], "update")

    def test_g7m_17_an_unresolved_target_records_its_reason(self):
        d = resolve([Row(1, "Peugeot", "208", None, status="current_focus"),
                     Row(2, "Peugeot", "208", None)],
                    marca="Peugeot", modelo="208", anio=2016)
        p = d.to_payload()
        self.assertEqual(p["reason"], "MULTIPLE_COMPATIBLE_CANDIDATES")
        self.assertIsNone(p["target_candidate_id"])
        self.assertIsNone(p["action"])

    def test_g7m_18_the_trace_carries_ids_and_canonical_values_only(self):
        d = resolve([Row(1, "Ford", "Focus", 2019, status="current_focus")],
                    marca="Ford", modelo="Focus", anio=2016)
        blob = repr(d.to_payload()).lower()
        for leak in ("el otro", "perdon", "no, es", "tengo un"):
            self.assertNotIn(leak, blob)

    def test_g7m_19_all_three_outcomes_are_declared_contract_members(self):
        self.assertEqual(len(T.ALL), 3)
        for v in (T.MATCH_EXISTING_CANDIDATE, T.CREATE_NEW_CANDIDATE, T.UNRESOLVED_TARGET):
            self.assertIn(v, T.ALL)


# ══ D · F2 — eligibility, asserted through the ENGINE ════════════════════════════════
class G7M_F2_Eligibility(unittest.TestCase):
    """The W2/G3-7L reviews both found this unprotected: mutating the eligibility set
    admitted every HOLD reason and no test failed. These go through the real engine."""

    INELIGIBLE = (H.CATEGORY_NOT_ESTABLISHED, H.FOCUS_POINTER_INVALID,
                  H.FOCUS_NOT_UNIQUE, H.FOCUS_POINTER_MISSING, H.FOCUS_NOT_ESTABLISHED,
                  H.FOCUS_INCONSISTENT)

    def _route_for(self, hold_reason):
        import dataclasses
        eng, ctx, state = G7L.engine(G7L.FOCUS_2019(), G7L.N01_BURST)
        eng._semantic_turn_evidence = lambda: G7L.N01_EV
        real = eng._resolve_candidate(ctx, state, G7L.N01_EV)
        forged = dataclasses.replace(real, hold_reason=hold_reason)
        eng._turn_candidate_resolution = forged
        eng._turn_clarification_decision_obj = None
        eng._turn_clarification_decision_count = 0
        return eng._turn_clarification_decision(
            ctx, state, reconciled={"action": "none"}, resolution=forged,
            disposition=None, ce_vehicle=None, ce_location=False,
            current_turn_text=" ".join(G7L.N01_BURST))

    def test_g7m_20_ineligible_hold_reasons_never_reach_the_vehicle_flow(self):
        for reason in self.INELIGIBLE:
            with self.subTest(hold=reason):
                self.assertNotIn(reason, VEHICLE_CLARIFIABLE_HOLDS)
                dec = self._route_for(reason)
                self.assertNotEqual(dec.route, ClarificationRoute.VEHICLE_FLOW_REQUIRED,
                                    f"{reason} must not ask the customer")

    def test_g7m_21_an_ineligible_hold_mutates_nothing(self):
        for reason in self.INELIGIBLE:
            with self.subTest(hold=reason):
                dec = self._route_for(reason)
                self.assertIsNone(dec.family)

    def test_g7m_22_the_nine_intended_reasons_remain_eligible(self):
        expected = {H.TARGET_SCOPE_UNRESOLVED, H.TARGET_SCOPE_CONTRADICTED,
                    H.EXCLUDED_REFERENT, H.CONFLICTING_VEHICLE_ATTRIBUTES,
                    H.UNMARKED_MULTI_IDENTITY, H.AMBIGUOUS_MATCH, H.INCOMPLETE_IDENTITY,
                    H.CREATE_UNCONFIRMED, H.TARGET_NOT_GROUNDED}
        self.assertEqual(set(VEHICLE_CLARIFIABLE_HOLDS), expected)
        for reason in expected:
            with self.subTest(hold=reason):
                dec = self._route_for(reason)
                self.assertEqual(dec.route, ClarificationRoute.VEHICLE_FLOW_REQUIRED)
                self.assertEqual(dec.family, "VEHICLE")

    def test_g7m_23_making_every_reason_eligible_is_caught_behaviourally(self):
        """The hostile mutation the prior audits found uncaught. It must fail on the
        ROUTE the real engine selects, not on an import or a source string."""
        import app.services.conversation_engine as CEM

        def mutant():
            original = CEM.ConversationEngine._turn_clarification_decision
            # widen eligibility the way a careless refactor would: accept any hold
            import app.services.clarification_attempt as CA
            saved = CA.VEHICLE_CLARIFIABLE_HOLDS
            CA.VEHICLE_CLARIFIABLE_HOLDS = frozenset(
                v for k, v in vars(H).items() if isinstance(v, str) and k.isupper())
            try:
                dec = self._route_for(H.CATEGORY_NOT_ESTABLISHED)
                assert dec.route != ClarificationRoute.VEHICLE_FLOW_REQUIRED, \
                    "an ineligible category fault reached the Vehicle Flow"
            finally:
                CA.VEHICLE_CLARIFIABLE_HOLDS = saved
                CEM.ConversationEngine._turn_clarification_decision = original
        with self.assertRaises(AssertionError):
            mutant()


# ══ E · F3 — both flags ON must execute ══════════════════════════════════════════════
class G7M_F3_FlagPostures(unittest.TestCase):

    POSTURES = ((False, False), (True, False), (False, True), (True, True), (None, False))

    def test_g7m_24_no_name_error_in_any_posture(self):
        for g, p in self.POSTURES:
            with self.subTest(grounding=g, provisional=p):
                r, d, dec, _s, _c = G7L.decide(G7L.FOCUS_2019(), G7L.N01_EV,
                                               G7L.N01_BURST, grounding=g, provisional=p)
                self.assertEqual(r.action, A.HOLD)
                self.assertEqual(d, {"action": "none"})
                self.assertEqual(dec.route, ClarificationRoute.VEHICLE_FLOW_REQUIRED)

    def test_g7m_25_the_disposition_imports_what_it_references(self):
        """F3 was a bare name with no import in scope and none at module level."""
        src = inspect.getsource(CE._clarification_disposition)
        self.assertIn("import CandidateAction as _CAction", src)
        self.assertIn("_CAction.CORRECT_EXISTING", src)
        self.assertNotIn("!= CandidateAction.CORRECT_EXISTING", src)

    def test_g7m_26_reintroducing_the_unresolved_reference_is_caught(self):
        src = inspect.getsource(CE._clarification_disposition)
        body = src.split('"""')[-1]
        bare = [ln for ln in body.splitlines()
                if "CandidateAction." in ln and "_CAction" not in ln
                and "import" not in ln]
        self.assertEqual(bare, [], f"unresolved CandidateAction reference: {bare}")

    def test_g7m_27_w2_safety_holds_in_every_posture(self):
        for g, p in self.POSTURES:
            with self.subTest(grounding=g, provisional=p):
                r, d, _dec, state, cands = G7L.decide(
                    G7L.TWO_CANDS(), G7L.N01_EV, G7L.N01_BURST, grounding=g, provisional=p)
                self.assertEqual(d, {"action": "none"})
                self.assertEqual(state.current_focus_candidate_id, 1)
                self.assertEqual([c.anio for c in cands], [2019, 2016],
                                 "2016 must never land on the focused Focus")


# ══ F · hostile mutations of the target resolver ═════════════════════════════════════
class G7M_MutationProofs(unittest.TestCase):

    TWO = lambda self: [Row(1, "Ford", "Focus", 2019, status="current_focus"),
                        Row(2, "Peugeot", "208", None)]
    AMBIG = lambda self: [Row(1, "Peugeot", "208", None, status="current_focus"),
                          Row(2, "Peugeot", "208", None)]

    def test_g7m_28_unconditional_write_to_focus_is_caught(self):
        d = resolve(self.TWO(), marca="Peugeot", modelo="208", anio=2016)
        self.assertNotEqual(d.target_candidate_id, 1)

    def test_g7m_29_choosing_the_first_compatible_row_is_caught(self):
        d = resolve(self.AMBIG(), marca="Peugeot", modelo="208", anio=2016)
        self.assertEqual(d.outcome, T.UNRESOLVED_TARGET,
                         "two compatible rows must not resolve to the first")

    def test_g7m_30_always_creating_is_caught(self):
        d = resolve(self.TWO(), marca="Peugeot", modelo="208", anio=2016)
        self.assertNotEqual(d.outcome, T.CREATE_NEW_CANDIDATE)

    def test_g7m_31_always_reusing_focus_is_caught(self):
        d = resolve([Row(1, "Ford", "Focus", 2019, status="current_focus")],
                    marca="Peugeot", modelo="208", anio=2016)
        self.assertEqual(d.outcome, T.CREATE_NEW_CANDIDATE)
        self.assertIsNone(d.target_candidate_id)

    def test_g7m_32_treating_moto_as_auto_is_caught(self):
        d = resolve([Row(1, "Honda", "Titan", 2020, tipo="MOTO", status="current_focus")],
                    marca="Honda", modelo="Titan", anio=2020, tipo="AUTO")
        self.assertNotEqual(d.target_candidate_id, 1)

    def test_g7m_33_the_moto_route_precedes_target_resolution(self):
        src = inspect.getsource(CE._process_vehicle_fallback_response)
        self.assertLess(src.index("_motorcycle_human_handoff"),
                        src.index("_resolve_completion_target"),
                        "category safety must not be reachable only after resolution")

    def test_g7m_34_an_unresolved_target_writes_nothing_and_escalates(self):
        """Bounded to the branch itself: from the `if` to its own `return`, so the
        later writer call cannot drift into the window and make this vacuous."""
        src = inspect.getsource(CE._process_vehicle_fallback_response)
        start = src.index("if _target.outcome == _T.UNRESOLVED_TARGET:")
        end = src.index("return _out(", start) + len("return _out(")
        block = src[start:end]
        self.assertIn("needs_human = True", block)
        self.assertNotIn("_apply_candidate", block,
                         "an unresolved target must not reach the writer")
        # and the writer genuinely comes after the branch returns
        self.assertGreater(src.index("_apply_candidate(", start), end)

    def test_g7m_35_a_stale_cycle_completion_cannot_write(self):
        src = inspect.getsource(CE._process_vehicle_fallback_response)
        self.assertIn("attempt_in_other_cycle", src)
        i = src.index("attempt_in_other_cycle")
        self.assertLess(i, src.index("_apply_candidate"),
                        "the stale-cycle guard must precede the writer")

    def test_g7m_36_the_stale_probe_fails_closed_when_unreadable(self):
        from app.services import clarification_attempt as CA
        src = inspect.getsource(CA.ClarificationAttemptStore.attempt_in_other_cycle)
        self.assertIn("return True", src.split("except")[-1],
                      "an unreadable store must fail closed")


# ══ G · scope fence ══════════════════════════════════════════════════════════════════
class G7M_ScopeFence(unittest.TestCase):

    def test_g7m_37_one_writer_only(self):
        src = inspect.getsource(CE._process_vehicle_fallback_response)
        self.assertEqual(src.count("_apply_candidate("), 1,
                         "exactly one writer call on the completion path")

    def test_g7m_38_no_phrase_list_entered_the_resolver(self):
        import ast
        import textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(CE._resolve_completion_target)))
        node = tree.body[0]
        body = list(node.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]
        code = "\n".join(ast.unparse(s) for s in body).lower()
        for token in ("otro", "en realidad", "al final", "perdon", "foccus", "alias"):
            self.assertNotIn(token, code)

    def test_g7m_39_no_revision_or_booking_on_the_completion_path(self):
        """Executed statements only — the docstring legitimately promises "never sets
        AGENDADO", and scanning raw source would match its own promise."""
        import ast
        import textwrap
        tree = ast.parse(textwrap.dedent(
            inspect.getsource(CE._process_vehicle_fallback_response)))
        node = tree.body[0]
        body = list(node.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]
        code = "\n".join(ast.unparse(st) for st in body)
        for forbidden in ("ThreadRevision(", "_send_booking_flow", "AGENDADO"):
            self.assertNotIn(forbidden, code)

    def test_g7m_40_b15_remains_an_open_w3_defect(self):
        ev = G7L.TE([G7L.V("Ford", "Focus", None)])
        r, d, _dec, _s, _c = G7L.decide(
            [G7L.Cand(1, "Peugeot", "2008", 2014)], ev,
            ["En realidad el auto es un Ford Focus."])
        self.assertEqual(r.action, A.CREATE, "unchanged: G3-7M did not fix B15")
        self.assertNotIn("anio", d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
