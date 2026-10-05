"""L4.7W5-GATE3-G3-7F Commit 2 — the semantic representation.

Two measured failures drive this commit.

`CorrectionRelation` was carrying two questions at once. "No, el otro es del 2016." is a
CORRECT_EXISTING-kind change aimed at an ALTERNATE referent, and with nowhere to put the
referent both evaluated models reported the kind correctly and the target wrongly.
`CorrectionTargetScope` separates them.

And `corrections[]` was loose `(from, to)` pairs with no field identifier, so a two-field
turn collapsed into the one salient change — both models dropped the year from Wild Case B.
`CorrectedFieldPatch` makes multiplicity structural.

A  the types, and their defaults failing closed
B  parsing scope and patch
C  family mirroring, so existing consumers keep working
D  Wild Case B carries BOTH corrections
E  exclusion closes the single-candidate residual
F  the boundary: evidence describes, it does not choose or authorize
G  prompt and schema versions, and the prompt's shape
H  corpus expectations
"""
from __future__ import annotations

import json
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
from app.schemas.turn_evidence import (BurstReconstruction, CorrectedField,
                                       CorrectedFieldPatch, CorrectionTargetScope as Scope,
                                       EvidenceStatus as S, SCHEMA_VERSION)
from app.schemas.hybrid_trace import (CandidateAction, CandidateGroundingBasis as G,
                                      CandidateHoldReason as H)
from app.services.semantic_interpreter import SemanticTurnInterpreter, PROMPT_VERSION

_PARSER = SemanticTurnInterpreter.__new__(SemanticTurnInterpreter)


def parse(payload, texts):
    return _PARSER._to_turn_evidence(
        payload, model="offline", thread_id=1, burst_id="b", message_ids=("m",),
        reconstruction=BurstReconstruction.CORPUS_FIXTURE, texts=list(texts))


def correction(*, scope=None, patch=None, relation="CORRECT_EXISTING",
               status="CONFIRMED", ref=None, to=None, frm=None):
    item = {"relation": relation, "status": status}
    if scope is not None:
        item["target_scope"] = scope
    if patch is not None:
        item["patch"] = patch
    if ref is not None:
        item["target_ref"] = ref
    if to is not None:
        item["to_value"] = to
    if frm is not None:
        item["from_value"] = frm
    return item


def row(cid, marca, modelo, *, anio=2019, status="mentioned", tipo="AUTO",
        zg="CABA", zd="Palermo"):
    return SimpleNamespace(id=cid, marca=marca, modelo=modelo, anio=anio, status=status,
                           tipo_vehiculo=tipo, zone_group=zg, zone_detail=zd,
                           version_text=None, direccion_texto=None)


def resolve(candidates, evidence, texts, *, grounding=True, pointer="auto"):
    if pointer == "auto":
        pointer = next((c.id for c in candidates if c.status == "current_focus"), None)
    st = SimpleNamespace(current_focus_candidate_id=pointer, home_zone_group="CABA",
                         home_zone_detail="Palermo", needs_human=False,
                         last_stage=ce.STAGE_QUOTED)
    ctx = SimpleNamespace(state=st, thread=SimpleNamespace(id=1),
                          candidates=list(candidates),
                          contact=SimpleNamespace(wa_id="x"))
    e = E.__new__(E)
    e._correlation_id = "turn-1"
    e.settings = SimpleNamespace(correction_target_grounding_enabled=grounding)
    e._extract_zone_from_text = lambda t: (
        SimpleNamespace(zone_group="Norte", zone_detail="Pilar") if "ilar" in str(t)
        else SimpleNamespace(zone_group="Oeste", zone_detail="San Miguel")
        if "Miguel" in str(t) else None)
    e._canonical_turn_burst = SimpleNamespace(messages=tuple(texts))
    e._catalog_tipo_for = lambda a, b: "AUTO"
    r = e._resolve_candidate(ctx, st, evidence)
    return r, e._resolution_to_candidate_data(r, evidence)


FOCUS = [row(1, "Ford", "Focus", status="current_focus")]
TWO = [row(1, "Ford", "Focus", status="current_focus"), row(2, "Toyota", "Corolla")]
WILD_B = ["Perdón, es 2018 y está en Pilar, no Palermo."]
WILD_B_PATCH = [{"field": "VEHICLE_YEAR", "from_value": 2019, "to_value": 2018},
                {"field": "INSPECTION_LOCALITY", "from_value": "Palermo",
                 "to_value": "Pilar"}]
PILAR = [{"value": "Pilar", "locality": "Pilar", "role": "INSPECTION_LOCATION",
          "status": "CONFIRMED"}]


# ══ A — the types ══════════════════════════════════════════════════════════

class A_Types(unittest.TestCase):

    def test_g37f_35_the_scope_covers_the_four_required_meanings(self):
        self.assertEqual({s.value for s in Scope},
                         {"CURRENT_REFERENT", "ALTERNATE_REFERENT",
                          "NAMED_REFERENT", "UNRESOLVED_REFERENT"})

    def test_g37f_36_an_absent_scope_fails_closed(self):
        """Silence, and every turn-evidence/1.3 payload, must read as unresolved."""
        # Compared by value: other suites reload the schema module, and an enum member
        # that survived a reload is still the same scope.
        self.assertEqual(
            getattr(te.CorrectionEvidence(value=True).target_scope, "value", None),
            "UNRESOLVED_REFERENT")

    def test_g37f_37_scope_is_orthogonal_to_relation(self):
        """The defect was one field answering two questions. Every pair must be expressible."""
        for relation in te.CorrectionRelation:
            for scope in Scope:
                with self.subTest(relation=relation.value, scope=scope.value):
                    c = te.CorrectionEvidence(value=True, relation=relation,
                                              target_scope=scope)
                    self.assertEqual(c.relation, relation)
                    self.assertEqual(c.target_scope, scope)

    def test_g37f_38_a_patch_entry_identifies_its_canonical_field(self):
        p = CorrectedFieldPatch(field=CorrectedField.VEHICLE_YEAR, from_value=2019,
                                to_value=2018)
        self.assertEqual(p.field, CorrectedField.VEHICLE_YEAR)
        self.assertEqual((p.from_value, p.to_value), (2019, 2018))

    def test_g37f_39_a_scope_or_patch_only_correction_survives_sanitation(self):
        """Either is evidence on the same footing as the relation; dropping one loses the
        only record of what changed, or of which car was meant.

        G3-7F-R2 (review G37FRR-09). The `patch only` case used to construct a
        `CorrectedFieldPatch` INSTANCE and nest it. Another suite
        (`test_l4_7a_turn_evidence_schema.py:498`) reloads `app.schemas.turn_evidence`, so in
        full-suite order the class this module imported was no longer the class the reloaded
        `CorrectionEvidence` validates against, and pydantic raised `model_type` — a SUBFAIL
        that the summary-identity diff could not see.

        The nested entry is built from a DICT instead. That is also the production-faithful
        boundary: `semantic_interpreter._patch()` builds these from the model's JSON, never
        from a held class object. Validation is not weakened — pydantic still constructs and
        validates a real `CorrectedFieldPatch`, as the type assertion below proves.
        """
        for label, kwargs in (
            ("scope only", {"target_scope": Scope.ALTERNATE_REFERENT}),
            ("patch only", {"patch": ({"field": "VEHICLE_YEAR", "to_value": 2018},)}),
        ):
            with self.subTest(case=label):
                c = te.CorrectionEvidence(**kwargs)
                self.assertFalse(c.is_semantically_empty())
                if label == "patch only":
                    # the dict really did become the schema's own validated model
                    self.assertEqual(len(c.patch), 1)
                    self.assertIsInstance(c.patch[0], te.CorrectedFieldPatch)
                    self.assertEqual(getattr(c.patch[0].field, "value",
                                             c.patch[0].field), "VEHICLE_YEAR")
                    self.assertEqual(c.patch[0].to_value, 2018)
                ev = te.TurnEvidence(corrections=(c,))
                self.assertEqual(len(ev.without_empty_items().corrections), 1)

    def test_g37f_40_a_truly_empty_correction_is_still_dropped(self):
        c = te.CorrectionEvidence()
        self.assertTrue(c.is_semantically_empty())


# ══ B — parsing ════════════════════════════════════════════════════════════

class B_Parsing(unittest.TestCase):

    def test_g37f_41_scope_and_patch_round_trip(self):
        ev = parse({"corrections": [correction(scope="CURRENT_REFERENT",
                                               patch=WILD_B_PATCH)],
                    "locations": PILAR}, WILD_B)
        c = ev.corrections[0]
        self.assertEqual(c.target_scope, Scope.CURRENT_REFERENT)
        self.assertEqual([(p.field.value, p.from_value, p.to_value) for p in c.patch],
                         [("VEHICLE_YEAR", 2019, 2018),
                          ("INSPECTION_LOCALITY", "Palermo", "Pilar")])

    def test_g37f_42_an_unknown_scope_is_unresolved_not_a_guess(self):
        for raw in ("THE_OTHER_ONE", "", None, 7, "current"):
            with self.subTest(raw=raw):
                ev = parse({"corrections": [correction(scope=raw, to=2018)]}, ["x"])
                self.assertEqual(getattr(ev.corrections[0].target_scope, "value", None),
                                 "UNRESOLVED_REFERENT")

    def test_g37f_43_malformed_patch_entries_are_dropped_not_invented(self):
        ev = parse({"corrections": [correction(scope="CURRENT_REFERENT", patch=[
            {"field": "VEHICLE_YEAR", "to_value": 2018},        # keep
            {"field": "NOT_A_FIELD", "to_value": "x"},          # unknown field
            {"field": "UNKNOWN_FIELD", "to_value": "x"},        # explicit unknown
            {"field": "VEHICLE_MAKE"},                          # no value at all
            "not a dict",
        ])]}, ["x"])
        self.assertEqual([p.field.value for p in ev.corrections[0].patch],
                         ["VEHICLE_YEAR"])

    def test_g37f_44_a_patch_entry_may_carry_its_own_status_and_scope(self):
        ev = parse({"corrections": [correction(scope="CURRENT_REFERENT", patch=[
            {"field": "VEHICLE_YEAR", "to_value": 2018, "status": "PROPOSED",
             "scope": "NAMED_REFERENT", "evidence_ref": "vehicle_mentions[0]"}])]}, ["x"])
        p = ev.corrections[0].patch[0]
        self.assertEqual(getattr(p.status, "value", None), "PROPOSED")
        self.assertEqual(getattr(p.scope, "value", None), "NAMED_REFERENT")
        self.assertEqual(p.evidence_ref, "vehicle_mentions[0]")

    def test_g37f_45_a_1_3_shaped_payload_still_parses(self):
        """Backward compatibility is the default value, not a dual-reading path."""
        ev = parse({"corrections": [correction(to=2018, frm=2019)]}, ["Perdón, es 2018."])
        c = ev.corrections[0]
        self.assertEqual(getattr(c.target_scope, "value", None),
                         "UNRESOLVED_REFERENT")
        self.assertEqual(c.patch, ())
        self.assertEqual((c.from_value, c.to_value), (2019, 2018))


# ══ C — family mirroring ═══════════════════════════════════════════════════

class C_Mirroring(unittest.TestCase):

    def test_g37f_46_a_year_patch_is_mirrored_into_vehicle_evidence(self):
        """Every consumer that reads `VehicleEvidence.year` keeps working unchanged."""
        ev = parse({"corrections": [correction(scope="CURRENT_REFERENT",
                                               patch=WILD_B_PATCH)],
                    "locations": PILAR}, WILD_B)
        self.assertEqual([v.year for v in ev.vehicle_mentions], [2018])

    def test_g37f_47_mirroring_never_invents_an_identity(self):
        ev = parse({"corrections": [correction(scope="CURRENT_REFERENT",
                                               patch=WILD_B_PATCH)],
                    "locations": PILAR}, WILD_B)
        v = ev.vehicle_mentions[0]
        self.assertIsNone(v.make)
        self.assertIsNone(v.model)
        self.assertIsNone(v.value)

    def test_g37f_48_an_explicitly_named_year_still_wins(self):
        ev = parse({"vehicles": [{"value": "Ford Focus 2019", "make": "Ford",
                                  "model": "Focus", "year": 2019,
                                  "status": "CONFIRMED"}],
                    "corrections": [correction(scope="CURRENT_REFERENT", patch=[
                        {"field": "VEHICLE_YEAR", "to_value": 2018}])]},
                   ["El Ford Focus 2019... perdón, 2018."])
        self.assertEqual([v.year for v in ev.vehicle_mentions], [2019])


# ══ D — Wild Case B carries both ═══════════════════════════════════════════

class D_WildCaseB(unittest.TestCase):

    EV = None

    def setUp(self):
        self.EV = parse({"corrections": [correction(scope="CURRENT_REFERENT",
                                                    patch=WILD_B_PATCH)],
                         "locations": PILAR}, WILD_B)

    def test_g37f_49_both_corrections_reach_the_writer(self):
        r, d = resolve(FOCUS, self.EV, WILD_B)
        self.assertEqual(r.action, CandidateAction.CORRECT_EXISTING)
        self.assertEqual(d["anio"], 2018)
        self.assertEqual(d["zone_detail"], "Pilar")
        self.assertEqual(d["zone_group"], "Norte")

    def test_g37f_50_the_patch_is_the_authoritative_source(self):
        r, _ = resolve(FOCUS, self.EV, WILD_B)
        fields = {f for f, _b, _a in r.corrected_fields}
        self.assertEqual(fields, {"anio", "zone_detail", "zone_group"})

    def test_g37f_51_before_values_are_recorded_for_every_field(self):
        r, _ = resolve(FOCUS, self.EV, WILD_B)
        before = {f: b for f, b, _a in r.corrected_fields}
        self.assertEqual(before["anio"], 2019)
        self.assertEqual(before["zone_detail"], "Palermo")

    def test_g37f_52_it_works_with_the_floor_off_too(self):
        """Commit 2's representation is independent of Commit 1's flag."""
        r, d = resolve(FOCUS, self.EV, WILD_B, grounding=False)
        self.assertEqual(d["anio"], 2018)
        self.assertEqual(d["zone_detail"], "Pilar")

    def test_g37f_53_a_correction_still_never_moves_focus(self):
        r, d = resolve(FOCUS, self.EV, WILD_B)
        self.assertFalse(r.focus_transition_required)
        self.assertNotIn("status", d)


# ══ E — exclusion closes the residual ══════════════════════════════════════

class E_Exclusion(unittest.TestCase):
    """G37ER-04 was the residual the owner declined to accept: with ONE candidate, a
    mislabelled referent still wrote a wrong value to the only row. The scope field closes
    it — an excluded referent holds even in a singleton thread."""

    EL_OTRO = ["No, el otro es del 2016."]

    def _ev(self, scope):
        return parse({"corrections": [correction(scope=scope, patch=[
            {"field": "VEHICLE_YEAR", "to_value": 2016}])]}, self.EL_OTRO)

    def test_g37f_54_an_excluded_referent_holds_with_one_candidate(self):
        r, d = resolve(FOCUS, self._ev("ALTERNATE_REFERENT"), self.EL_OTRO)
        self.assertEqual(r.action, CandidateAction.HOLD)
        self.assertEqual(r.hold_reason, H.EXCLUDED_REFERENT)
        self.assertEqual(d, {"action": "none"})

    def test_g37f_55_an_excluded_referent_holds_with_several_candidates(self):
        r, d = resolve(TWO, self._ev("ALTERNATE_REFERENT"), self.EL_OTRO)
        self.assertEqual(r.action, CandidateAction.HOLD)
        self.assertEqual(d, {"action": "none"})

    def test_g37f_56_an_unresolved_referent_also_holds(self):
        r, _ = resolve(FOCUS, self._ev("UNRESOLVED_REFERENT"), self.EL_OTRO)
        self.assertEqual(r.action, CandidateAction.HOLD)
        self.assertEqual(r.hold_reason, H.EXCLUDED_REFERENT)

    def test_g37f_57_a_current_referent_still_applies(self):
        """The floor must not become a blanket refusal — capability is retained."""
        r, d = resolve(FOCUS, self._ev("CURRENT_REFERENT"), self.EL_OTRO)
        self.assertEqual(r.action, CandidateAction.CORRECT_EXISTING)
        self.assertEqual(d["anio"], 2016)


# ══ F — the boundary ═══════════════════════════════════════════════════════

class F_Boundary(unittest.TestCase):

    def test_g37f_58_no_schema_field_can_carry_a_database_identity(self):
        forbidden = {"candidate_id", "target_candidate_id", "thread_id", "lead_id",
                     "revision_id"}
        for model in (te.CorrectionEvidence, CorrectedFieldPatch, te.VehicleEvidence,
                      te.LocationEvidence):
            with self.subTest(model=model.__name__):
                self.assertEqual(set(model.model_fields) & forbidden, set())

    def test_g37f_59_the_schema_still_forbids_extra_fields(self):
        with self.assertRaises(Exception):
            te.CorrectionEvidence(value=True, candidate_id=7)

    def test_g37f_60_evidence_cannot_authorize_a_correction_by_itself(self):
        """Scope CURRENT_REFERENT plus CONFIRMED is still not authority: the reconciler
        must still ground the target."""
        ev = parse({"corrections": [correction(scope="CURRENT_REFERENT", patch=[
            {"field": "VEHICLE_YEAR", "to_value": 2016}])]}, ["Es 2016."])
        r, d = resolve(TWO, ev, ["Es 2016."])          # two candidates — not grounded
        self.assertEqual(r.action, CandidateAction.HOLD)
        self.assertEqual(r.hold_reason, H.TARGET_NOT_GROUNDED)
        self.assertEqual(d, {"action": "none"})

    def test_g37f_61_the_writer_is_still_the_only_mutation_path(self):
        import inspect
        src = inspect.getsource(ce)
        self.assertEqual(src.count("def _apply_candidate("), 1)


# ══ G — versions and prompt shape ══════════════════════════════════════════

class G_Versions(unittest.TestCase):

    def test_g37f_62_the_versions_moved_together(self):
        self.assertEqual(PROMPT_VERSION, "understand/1.20")
        self.assertEqual(SCHEMA_VERSION, "turn-evidence/1.4")

    def test_g37f_63_the_model_did_not_move(self):
        src = (ROOT / "backend/app/services/semantic_interpreter.py").read_text(
            encoding="utf-8")
        self.assertIn('or "gpt-4o-mini"', src)

    def test_g37f_64_production_request_parameters_are_unchanged(self):
        import inspect
        body = inspect.getsource(SemanticTurnInterpreter._call_openai)
        for required in ('"response_format": {"type": "json_object"}', '"temperature": 0',
                         '"max_tokens": 1200'):
            with self.subTest(param=required):
                self.assertIn(required, body)

    def test_g37f_65_the_prompt_states_rules_not_customer_phrasings(self):
        """Examples may be conformance tests; they may not be the rule. The ten SYN-CT
        texts must not appear in the prompt."""
        src = (ROOT / "backend/app/services/semantic_interpreter.py").read_text(
            encoding="utf-8")
        for forbidden in ("el otro es del 2016", "está en Pilar, no Palermo",
                          "Mejor el otro auto", "una Honda Titan"):
            with self.subTest(text=forbidden):
                self.assertNotIn(forbidden, src)

    def test_g37f_66_the_prompt_declares_the_new_obligations(self):
        src = (ROOT / "backend/app/services/semantic_interpreter.py").read_text(
            encoding="utf-8")
        for required in ("target_scope", "CURRENT_REFERENT", "ALTERNATE_REFERENT",
                         "NAMED_REFERENT", "UNRESOLVED_REFERENT",
                         "corrections[].patch[].field"):
            with self.subTest(rule=required):
                self.assertIn(required, src)

    def test_g37f_67_the_prompt_forbids_choosing_the_focus_itself(self):
        src = (ROOT / "backend/app/services/semantic_interpreter.py").read_text(
            encoding="utf-8")
        self.assertIn("lo resuelve el reconciliador", src)
        self.assertIn("identificadores de base de datos", src)


# ══ H — corpus ═════════════════════════════════════════════════════════════

class H_Corpus(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        path = ROOT / "tests" / "semantic_corpus" / "real_world_turns.jsonl"
        cls.cases = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                cls.cases[r["id"]] = r

    def _corr(self, cid):
        return next((e for e in self.cases[cid]["expected_turn_evidence"]
                     if e["field"] == "correction"), None)

    def test_g37f_68_every_correction_case_states_a_scope(self):
        missing = [cid for cid, c in self.cases.items()
                   for e in c["expected_turn_evidence"]
                   if e["field"] == "correction" and "target_scope" not in e]
        self.assertEqual(missing, [])

    def test_g37f_69_the_exclusive_families_expect_an_alternate_referent(self):
        for cid in ("SYN-CT-05", "SYN-CT-07", "SYN-CORR-08"):
            with self.subTest(case=cid):
                self.assertEqual(self._corr(cid)["target_scope"], "ALTERNATE_REFERENT")

    def test_g37f_70_wild_case_b_expects_both_patch_entries(self):
        patch = self._corr("SYN-CT-01")["patch"]
        self.assertEqual([p["field"] for p in patch],
                         ["VEHICLE_YEAR", "INSPECTION_LOCALITY"])
        self.assertEqual(patch[0]["from_value"], 2019)
        self.assertEqual(patch[1]["to_value"], "Pilar")

    def test_g37f_71_the_exclusion_guard_is_explicit(self):
        for cid in ("SYN-CT-05", "SYN-CT-07", "SYN-CORR-08"):
            with self.subTest(case=cid):
                guards = [m for m in self.cases[cid]["must_not_infer"]
                          if m["field"] == "correction.target_scope"]
                self.assertTrue(guards, "an excluded referent must be guarded explicitly")
                self.assertEqual(guards[0]["value"], "CURRENT_REFERENT")

    def test_g37f_72_the_negatives_still_forbid_a_correction(self):
        for cid in ("SYN-CT-08", "SYN-CT-09"):
            with self.subTest(case=cid):
                self.assertIsNone(self._corr(cid))
                self.assertTrue([m for m in self.cases[cid]["must_not_infer"]
                                 if m["field"].startswith("correction")])

    def test_g37f_73_customer_language_was_not_altered(self):
        verbatim = {
            "SYN-CT-01": "Perdón, es 2018 y está en Pilar, no Palermo.",
            "SYN-CT-05": "No, el otro es del 2016.",
            "SYN-CORR-07": "Es del 2015 no del 2014",
            "SYN-CORR-08": "Al final volvemos con el Peugeot",
        }
        for cid, text in verbatim.items():
            with self.subTest(case=cid):
                self.assertEqual(self.cases[cid]["raw"]["messages"][0], text)

    def test_g37f_74_provenance_was_not_touched(self):
        self.assertEqual(sum(1 for c in self.cases.values()
                             if c["provenance"]["kind"] == "REAL"), 12)
        self.assertEqual(self.cases["SYN-CT-01"]["provenance"]["kind"], "SYNTHETIC")

    def test_g37f_75_the_corpus_evidence_names_no_database_identity(self):
        blob = json.dumps([e for c in self.cases.values()
                           for e in c["expected_turn_evidence"]], ensure_ascii=False)
        for forbidden in ("candidate_id", "target_candidate_id", "current_focus"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, blob)


if __name__ == "__main__":        # pragma: no cover
    unittest.main()
