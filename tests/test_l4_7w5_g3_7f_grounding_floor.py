"""L4.7W5-GATE3-G3-7F Commit 1 — the deterministic grounding floor.

G3-7C and G3-7D measured the thing this floor exists to stop: for "No, el otro es del
2016." `gpt-4o-mini` emitted CORRECT_EXISTING/PROPOSED and `gpt-5.4-mini` emitted
CORRECT_EXISTING/CONFIRMED, and the reconciler wrote the year onto the candidate the
customer had just excluded. A status gate would have caught the first and not the second.

So target selection no longer reads the interpreter's confidence at all. The Grounded-Target
Invariant admits three grounds the reconciler checks for itself:

  G1  make AND model said literally in the burst, matching exactly one row
  G2  exactly one candidate, strict canonical focus, nothing excluding it
  G3  a typed in-burst reference resolving to one row — superseded mentions included

Anything else holds. `status` and `target_scope` may narrow a decision; neither is a ground.

A  the flag, and flag-off fidelity
B  G1 / G2 / G3 in isolation
C  the invariant under a LYING model
D  creation needs literal make+model
E  the rung-4 superseded-reference defect
F  the MOTO / category floor
G  the catalog_candidate type guard
H  contract 1.7 and rule v2
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
from app.schemas.turn_evidence import BurstReconstruction, EvidenceStatus as S
from app.schemas.hybrid_trace import (CandidateAction, CandidateGroundingBasis as G,
                                      CandidateHoldReason as H, CANDIDATE_RULE_VERSION,
                                      TRACE_VERSION)
from app.services.semantic_interpreter import SemanticTurnInterpreter

C = S.CONFIRMED
R = te.CorrectionRelation
INSP = te.LocationRole.INSPECTION_LOCATION.value

_PARSER = SemanticTurnInterpreter.__new__(SemanticTurnInterpreter)


def parse(payload, texts):
    """Evidence as the REAL parser produces it, so fixtures cannot drift from production."""
    return _PARSER._to_turn_evidence(
        payload, model="offline", thread_id=1, burst_id="b", message_ids=("m",),
        reconstruction=BurstReconstruction.CORPUS_FIXTURE, texts=list(texts))


def row(cid, marca, modelo, *, anio=2019, status="mentioned", tipo="AUTO",
        zg="CABA", zd="Palermo"):
    return SimpleNamespace(id=cid, marca=marca, modelo=modelo, anio=anio, status=status,
                           tipo_vehiculo=tipo, zone_group=zg, zone_detail=zd,
                           version_text=None, direccion_texto=None)


ZONE = lambda t: (SimpleNamespace(zone_group="Norte", zone_detail="Pilar")
                  if "ilar" in str(t) else None)


def engine(candidates, texts, *, grounding=True, pointer="auto", zone=ZONE):
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
    e._extract_zone_from_text = zone
    e._canonical_turn_burst = SimpleNamespace(messages=tuple(texts))
    e._catalog_tipo_for = lambda a, b: ("AUTO" if (a, b) == ("Ford", "Focus") else None)
    return e, ctx, st


def resolve(candidates, evidence, texts, **kw):
    e, ctx, st = engine(candidates, texts, **kw)
    r = e._resolve_candidate(ctx, st, evidence)
    return r, e._resolution_to_candidate_data(r, evidence)


FOCUS = [row(1, "Ford", "Focus", status="current_focus")]
TWO = [row(1, "Ford", "Focus", status="current_focus"), row(2, "Toyota", "Corolla")]

# the exact G3-7C/G3-7D measured output for "No, el otro es del 2016."
EL_OTRO = ["No, el otro es del 2016."]
EL_OTRO_4O = {"corrections": [{"relation": "CORRECT_EXISTING", "to_value": 2016,
                               "status": "PROPOSED",
                               "target_scope": "CURRENT_REFERENT"}]}
EL_OTRO_54 = {"corrections": [{"relation": "CORRECT_EXISTING", "to_value": 2016,
                               "status": "CONFIRMED",
                               "target_scope": "CURRENT_REFERENT"}]}
WILD_B = ["Perdón, es 2018 y está en Pilar, no Palermo."]
# Commit 2 added `target_scope`, and its default is UNRESOLVED_REFERENT — so from Commit 2
# onward a correction that does not say which referent it means HOLDS. That is the designed
# fail-closed behaviour, so the grounding tests state the referent explicitly and
# `test_g37f_09b` pins the scope-less case to the HOLD it must now produce.
WILD_B_PAYLOAD = {"corrections": [{"relation": "CORRECT_EXISTING", "from_value": 2019,
                                   "to_value": 2018, "status": "CONFIRMED",
                                   "target_scope": "CURRENT_REFERENT"}],
                  "locations": [{"value": "Pilar", "locality": "Pilar",
                                 "role": "INSPECTION_LOCATION", "status": "CONFIRMED"}]}


# ══ A — the flag, and flag-off fidelity ════════════════════════════════════

class A_Flag(unittest.TestCase):

    def test_g37f_01_the_floor_is_off_by_default(self):
        import dataclasses
        from app.settings import Settings
        field = next(f for f in dataclasses.fields(Settings)
                     if f.name == "correction_target_grounding_enabled")
        self.assertIs(field.default, False)

    def test_g37f_02_only_a_real_true_arms_it(self):
        for value, expected in ((True, True), (False, False), ("true", False),
                                (1, False), (None, False)):
            with self.subTest(value=value):
                e = E.__new__(E)
                e.settings = SimpleNamespace(correction_target_grounding_enabled=value)
                self.assertIs(e._correction_grounding_on(), expected)
        bare = E.__new__(E)
        bare.settings = SimpleNamespace()
        self.assertFalse(bare._correction_grounding_on())

    def test_g37f_03_flag_off_keeps_the_certified_v1_outcome(self):
        """The v1 defect is preserved exactly when the floor is off — that is what makes
        the rollback meaningful, and it is measured rather than assumed."""
        r, d = resolve(TWO, parse(EL_OTRO_54, EL_OTRO), EL_OTRO, grounding=False)
        self.assertEqual(r.action, CandidateAction.CORRECT_EXISTING)
        self.assertEqual(r.target_candidate_id, 1)
        self.assertEqual(d["anio"], 2016)
        self.assertEqual(r.grounding_basis, G.NONE)


# ══ B — the three grounds ══════════════════════════════════════════════════

class B_Grounds(unittest.TestCase):

    def test_g37f_04_g1_literal_identity_matching_one_row(self):
        texts = ["El Ford Focus es 2018."]
        ev = parse({"vehicles": [{"value": "Ford Focus", "make": "Ford",
                                  "model": "Focus", "status": "CONFIRMED"}],
                    "corrections": [{"relation": "CORRECT_EXISTING", "to_value": 2018,
                                     "status": "CONFIRMED"}]}, texts)
        r, d = resolve(TWO, ev, texts)
        self.assertEqual(r.action, CandidateAction.CORRECT_EXISTING)
        self.assertEqual(r.grounding_basis, G.G1_IDENTITY)
        self.assertEqual(d["id"], 1)

    def test_g37f_05_g1_needs_the_tokens_actually_said(self):
        """"Toyota" is never uttered; the parser caps such an inference at PROPOSED and the
        floor refuses to ground a target on it."""
        texts = ["El Corolla es 2018."]
        ev = parse({"vehicles": [{"value": "Corolla", "make": "Toyota",
                                  "model": "Corolla", "status": "CONFIRMED"}],
                    "corrections": [{"relation": "CORRECT_EXISTING", "to_value": 2018,
                                     "status": "CONFIRMED"}]}, texts)
        e, ctx, _ = engine(TWO, texts)
        self.assertFalse(e._identity_literally_grounded(ev.vehicle_mentions[0],
                                                        e._burst_haystack()))

    def test_g37f_06_g2_singleton_with_a_valid_focus(self):
        r, d = resolve(FOCUS, parse(WILD_B_PAYLOAD, WILD_B), WILD_B)
        self.assertEqual(r.action, CandidateAction.CORRECT_EXISTING)
        self.assertEqual(r.grounding_basis, G.G2_SINGLETON)
        self.assertEqual(d["id"], 1)

    def test_g37f_07_g2_requires_a_singleton(self):
        r, _ = resolve(TWO, parse(WILD_B_PAYLOAD, WILD_B), WILD_B)
        self.assertEqual(r.action, CandidateAction.HOLD)
        self.assertEqual(r.hold_reason, H.TARGET_NOT_GROUNDED)

    def test_g37f_08_g2_requires_a_valid_strict_focus(self):
        for label, pointer, reason in (("pointer outside the cycle", 99,
                                        H.FOCUS_POINTER_INVALID),
                                       ("no pointer at all", None,
                                        H.FOCUS_POINTER_MISSING)):
            with self.subTest(case=label):
                r, _ = resolve(FOCUS, parse(WILD_B_PAYLOAD, WILD_B), WILD_B,
                               pointer=pointer)
                self.assertEqual(r.action, CandidateAction.HOLD)
                self.assertEqual(r.hold_reason, reason)

    def test_g37f_09_g2_will_not_spend_an_unconfirmed_commitment(self):
        """Implicit focus is the one ground with no independent evidence behind it."""
        r, _ = resolve(FOCUS, parse(EL_OTRO_4O, EL_OTRO), EL_OTRO)
        self.assertEqual(r.action, CandidateAction.HOLD)
        self.assertEqual(r.hold_reason, H.IMPLICIT_FOCUS_UNCONFIRMED)

    def test_g37f_09b_a_correction_that_names_no_referent_fails_closed(self):
        """From Commit 2 a scope-less correction — every `turn-evidence/1.3` payload — may
        not spend implicit focus, even in a singleton thread. Missing is not CURRENT."""
        texts = ["Perdón, es 2018."]
        ev = parse({"corrections": [{"relation": "CORRECT_EXISTING", "to_value": 2018,
                                     "status": "CONFIRMED"}]}, texts)
        self.assertEqual(ev.corrections[0].target_scope.value, "UNRESOLVED_REFERENT")
        r, d = resolve(FOCUS, ev, texts)
        self.assertEqual(r.action, CandidateAction.HOLD)
        self.assertEqual(d, {"action": "none"})

    def test_g37f_10_g3_a_typed_in_burst_reference(self):
        texts = ["El Focus no, el Corolla: ese es del 2017."]
        ev = parse({"vehicles": [{"value": "Ford Focus", "make": "Ford", "model": "Focus",
                                  "is_superseded": True, "status": "CONFIRMED"}],
                    "corrections": [{"relation": "CORRECT_EXISTING", "to_value": 2017,
                                     "target_ref": "vehicle_mentions[0]",
                                     "status": "CONFIRMED"}]}, texts)
        r, d = resolve(TWO, ev, texts)
        self.assertEqual(r.action, CandidateAction.CORRECT_EXISTING)
        self.assertEqual(r.grounding_basis, G.G3_REFERENCE)
        self.assertEqual(d["id"], 1)

    def test_g37f_11_a_dangling_reference_holds(self):
        texts = ["Perdón, es 2018."]
        ev = parse({"corrections": [{"relation": "CORRECT_EXISTING", "to_value": 2018,
                                     "target_ref": "vehicle_mentions[7]",
                                     "status": "CONFIRMED"}]}, texts)
        r, _ = resolve(FOCUS, ev, texts)
        self.assertEqual(r.hold_reason, H.TARGET_UNRESOLVED)

    def test_g37f_12_a_reference_to_a_car_this_thread_lacks_conflicts(self):
        texts = ["La Amarok no, es 2018."]
        ev = parse({"vehicles": [{"value": "Amarok", "make": "Volkswagen",
                                  "model": "Amarok", "is_superseded": True,
                                  "status": "CONFIRMED"}],
                    "corrections": [{"relation": "CORRECT_EXISTING", "to_value": 2018,
                                     "target_ref": "vehicle_mentions[0]",
                                     "status": "CONFIRMED"}]}, texts)
        r, _ = resolve(FOCUS, ev, texts)
        self.assertEqual(r.hold_reason, H.TARGET_CONFLICT)


# ══ C — the invariant under a LYING model ══════════════════════════════════

class C_HostileModel(unittest.TestCase):
    """The point of the floor: no label the model can emit selects a row."""

    def test_g37f_13_the_measured_4o_mini_output_cannot_mutate(self):
        r, d = resolve(TWO, parse(EL_OTRO_4O, EL_OTRO), EL_OTRO)
        self.assertEqual(r.action, CandidateAction.HOLD)
        self.assertEqual(d, {"action": "none"})

    def test_g37f_14_the_measured_54_mini_output_cannot_mutate_either(self):
        """CONFIRMED defeated the proposed status gate. It does not defeat the floor."""
        r, d = resolve(TWO, parse(EL_OTRO_54, EL_OTRO), EL_OTRO)
        self.assertEqual(r.action, CandidateAction.HOLD)
        self.assertEqual(r.hold_reason, H.TARGET_NOT_GROUNDED)
        self.assertEqual(d, {"action": "none"})

    def test_g37f_15_no_status_at_all_can_ground_a_multi_candidate_target(self):
        for status in ("CONFIRMED", "PROPOSED", "AMBIGUOUS", "CONFLICT"):
            with self.subTest(status=status):
                ev = parse({"corrections": [{"relation": "CORRECT_EXISTING",
                                             "to_value": 2016, "status": status}]},
                           EL_OTRO)
                r, d = resolve(TWO, ev, EL_OTRO)
                self.assertNotEqual(r.action, CandidateAction.CORRECT_EXISTING)
                self.assertEqual(d, {"action": "none"})

    def test_g37f_16_no_relation_at_all_can_ground_it_either(self):
        for relation in ("CORRECT_EXISTING", "REPLACE_CANDIDATE",
                         "SWITCH_TO_PRIOR_CANDIDATE", "ADD_SECOND_CANDIDATE"):
            with self.subTest(relation=relation):
                ev = parse({"corrections": [{"relation": relation, "to_value": 2016,
                                             "status": "CONFIRMED"}]}, EL_OTRO)
                r, d = resolve(TWO, ev, EL_OTRO)
                self.assertEqual(d, {"action": "none"})

    def test_g37f_17_the_target_is_never_read_from_the_models_labels(self):
        """Source-level: the grounds consult canonical state and the burst, and the only
        status read is the narrowing veto on implicit focus."""
        import inspect
        body = inspect.getsource(E._grounded_target)
        self.assertIn("self._correction_focus(ctx)", body)
        self.assertIn("self._identity_literally_grounded", body)
        self.assertIn("evidence.item_at", body)
        self.assertEqual(body.count("EvidenceStatus.CONFIRMED.value"), 1,
                         "status may narrow exactly once — the implicit-focus veto")


# ══ D — creation needs literal identity ════════════════════════════════════

class D_Creation(unittest.TestCase):

    def test_g37f_18_a_literally_named_new_car_creates(self):
        texts = ["Tengo un Toyota Corolla."]
        ev = parse({"vehicles": [{"value": "Toyota Corolla", "make": "Toyota",
                                  "model": "Corolla", "status": "CONFIRMED"}]}, texts)
        r, d = resolve(FOCUS, ev, texts)
        self.assertEqual(r.action, CandidateAction.CREATE)
        self.assertEqual(r.grounding_basis, G.G1_IDENTITY)
        self.assertTrue(r.literally_grounded)

    def test_g37f_19_an_inferred_identity_may_not_create(self):
        """"Toyota" was never said. Under v1 this created a row; the floor holds."""
        texts = ["Tengo un Corolla."]
        ev = parse({"vehicles": [{"value": "Corolla", "make": "Toyota",
                                  "model": "Corolla", "status": "CONFIRMED"}]}, texts)
        r, d = resolve(FOCUS, ev, texts)
        self.assertEqual(r.action, CandidateAction.HOLD)
        self.assertEqual(r.hold_reason, H.CREATE_UNCONFIRMED)
        self.assertEqual(d, {"action": "none"})

    def test_g37f_20_incomplete_identity_never_creates(self):
        for label, payload, texts in (
            ("year only", {"vehicles": [{"value": "2018", "year": 2018,
                                          "status": "CONFIRMED"}]}, ["Es 2018."]),
            ("make only", {"vehicles": [{"value": "un Fiat", "make": "Fiat",
                                          "status": "CONFIRMED"}]}, ["Un Fiat."]),
            ("category only", {"vehicles": [{"value": "un auto",
                                              "category_suggestion": "AUTO",
                                              "status": "CONFIRMED"}]}, ["Un auto."]),
        ):
            with self.subTest(case=label):
                r, d = resolve(FOCUS, parse(payload, texts), texts)
                self.assertNotEqual(r.action, CandidateAction.CREATE)
                self.assertNotIn("create", str(d.get("action")))


# ══ E — the rung-4 superseded defect ═══════════════════════════════════════

class E_SupersededReference(unittest.TestCase):

    def test_g37f_21_the_parser_really_does_rename_a_superseded_mention(self):
        ev = parse({"vehicles": [{"value": "Ford Focus", "make": "Ford", "model": "Focus",
                                  "is_superseded": True, "status": "CONFIRMED"}]},
                   ["El Ford Focus no."])
        self.assertEqual(ev.vehicle_mentions[0].field, "vehicle_superseded")

    def test_g37f_22_a_superseded_target_is_now_accepted(self):
        """G3-7B's gate was `field != "vehicle"`, which refused the one case the reference
        exists for and fell through to CREATE."""
        texts = ["El Ford Focus no, es 2017."]
        ev = parse({"vehicles": [{"value": "Ford Focus", "make": "Ford", "model": "Focus",
                                  "is_superseded": True, "status": "CONFIRMED"}],
                    "corrections": [{"relation": "CORRECT_EXISTING", "to_value": 2017,
                                     "target_ref": "vehicle_mentions[0]",
                                     "status": "CONFIRMED"}]}, texts)
        r, d = resolve(FOCUS, ev, texts)
        self.assertEqual(r.action, CandidateAction.CORRECT_EXISTING)
        self.assertEqual(r.grounding_basis, G.G3_REFERENCE)
        self.assertEqual(d["id"], 1)


# ══ F — the MOTO / category floor ══════════════════════════════════════════

class F_CategoryFloor(unittest.TestCase):

    def _engine(self, grounding=True):
        e = E.__new__(E)
        e.settings = SimpleNamespace(correction_target_grounding_enabled=grounding)
        e._catalog_tipo_for = lambda a, b: ("AUTO" if (a, b) == ("Ford", "Focus") else None)
        return e

    def test_g37f_23_a_catalogued_vehicle_has_an_established_category(self):
        e = self._engine()
        self.assertTrue(e._category_established(
            None, {"action": "create", "marca": "Ford", "modelo": "Focus"}))

    def test_g37f_24_an_uncatalogued_identity_without_a_category_is_not(self):
        """G37DR-04: a Honda Titan proposal created a candidate and continued
        commercially because no category ever reached the guard."""
        e = self._engine()
        self.assertFalse(e._category_established(
            None, {"action": "create", "marca": "Honda", "modelo": "Titan"}))

    def test_g37f_25_an_explicit_category_establishes_it(self):
        e = self._engine()
        self.assertTrue(e._category_established(
            None, {"action": "create", "marca": "Honda", "modelo": "Titan",
                   "tipo_vehiculo": "MOTO"}))

    def test_g37f_26_the_floor_is_wired_before_the_writer(self):
        import inspect
        src = inspect.getsource(ce.ConversationEngine._process_text)
        self.assertIn("self._category_established(ctx, _cand_data)", src)
        self.assertLess(src.index("self._category_established"),
                        src.index("_outcome = self._apply_candidate("),
                        "the floor must run BEFORE the writer")


# ══ G — the catalog_candidate type guard ═══════════════════════════════════

class G_CatalogGuard(unittest.TestCase):

    def test_g37f_27_an_object_valued_suggestion_no_longer_kills_the_turn(self):
        """G37DR-03: a candidate model returned an object here and pydantic rejected the
        item, discarding every other piece of evidence in the turn."""
        ev = parse({"vehicles": [{"value": "Corolla 2016", "make": "Toyota",
                                  "model": "Corolla", "year": 2016,
                                  "catalog_candidate": {"make": "Toyota",
                                                        "model": "Corolla"},
                                  "status": "CONFIRMED"}]}, ["Es un Corolla 2016."])
        self.assertEqual(len(ev.vehicle_mentions), 1, "the turn survived")
        self.assertEqual(ev.vehicle_mentions[0].model, "Corolla")

    def test_g37f_28_a_string_suggestion_is_still_honoured(self):
        ev = parse({"vehicles": [{"value": "Corolla", "make": "Toyota", "model": "Corolla",
                                  "catalog_candidate": "Toyota Corolla",
                                  "status": "CONFIRMED"}]}, ["Es un Corolla."])
        self.assertEqual(ev.vehicle_mentions[0].catalog_candidate, "Toyota Corolla")


# ══ H — contract and rule versions ═════════════════════════════════════════

class H_Contracts(unittest.TestCase):

    def test_g37f_29_the_trace_contract_is_1_7(self):
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.10")

    def test_g37f_30_the_rule_version_moved_to_v2(self):
        self.assertEqual(CANDIDATE_RULE_VERSION, "v2")

    def test_g37f_31_historical_rows_render_by_what_their_contract_carried(self):
        """1.6 introduced the section, so on a 1.6 row an absent decision is a fact worth
        stating. On 1.5 and earlier the section never existed, so the card stays silent
        rather than inventing a silence nobody recorded. G3-7B pinned one literal version
        here, which inverted the moment 1.7 shipped."""
        from app.ui.hybrid_trace_view import (_candidate_resolution_card,
                                              CONTRACTS_WITH_CANDIDATE_SECTION)
        for old in ("hybrid-decision-trace/1.0", "hybrid-decision-trace/1.4",
                    "hybrid-decision-trace/1.5"):
            with self.subTest(contract=old, carries=False):
                self.assertEqual(_candidate_resolution_card({"trace_version": old}), "")
        for carried in ("hybrid-decision-trace/1.6", TRACE_VERSION):
            with self.subTest(contract=carried, carries=True):
                self.assertIn(carried, CONTRACTS_WITH_CANDIDATE_SECTION)
                self.assertIn("sin decisión de candidato",
                              _candidate_resolution_card({"trace_version": carried}))

    def test_g37f_32_the_inspector_names_the_ground(self):
        from app.ui.hybrid_trace_view import _candidate_resolution_card
        r, _ = resolve(FOCUS, parse(WILD_B_PAYLOAD, WILD_B), WILD_B)
        html_out = _candidate_resolution_card(
            {"trace_version": TRACE_VERSION, "candidate_resolution": r.to_payload()})
        self.assertIn("Fundamento del objetivo", html_out)
        self.assertIn("único candidato", html_out)

    def test_g37f_33_the_inspector_names_every_new_hold_reason(self):
        from app.ui.hybrid_trace_view import _candidate_resolution_card
        for reason, fragment in (
                (H.TARGET_NOT_GROUNDED, "ningún fundamento verificable"),
                (H.EXCLUDED_REFERENT, "excluye el vehículo"),
                (H.IMPLICIT_FOCUS_UNCONFIRMED, "foco implícito"),
                (H.CREATE_UNCONFIRMED, "textualmente"),
                (H.CATEGORY_NOT_ESTABLISHED, "revisión humana")):
            with self.subTest(reason=reason):
                html_out = _candidate_resolution_card(
                    {"trace_version": TRACE_VERSION,
                     "candidate_resolution": {"action": "HOLD", "hold_reason": reason}})
                self.assertIn(fragment, html_out)

    def test_g37f_34_no_phrase_catalogue_was_introduced(self):
        """The floor must contain no customer wording whatsoever."""
        import inspect
        for fn in (E._grounded_target, E._identity_literally_grounded,
                   E._burst_haystack, E._value_said_literally,
                   E._evidence_excludes_current, E._category_established):
            body = inspect.getsource(fn)
            with self.subTest(fn=fn.__name__):
                self.assertNotIn("re.compile", body)
                for word in ("sí", "dale", "correcto", "exacto", "el otro", "no ese"):
                    self.assertNotIn(word, body.lower())


if __name__ == "__main__":        # pragma: no cover
    unittest.main()
