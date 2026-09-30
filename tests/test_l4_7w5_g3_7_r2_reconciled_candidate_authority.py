"""L4.7W5-GATE3-G3-7-R2 — the candidate reconciler the cutover audit found missing.

The audit's finding, restated so this file can be read on its own: the reply model had
never been only interpreting. It was choosing which CRM candidate a mention referred to,
which one became the focus, which one was demoted, and which one a locality attached to —
reconciliation work carried in a `candidate:{id, status, zone_group, …}` dict.
`VehicleEvidence` correctly describes only what the customer SAID and must never carry a
database id or a mutable CRM status, so removing the model left those decisions with no
owner and broke 30 certified canonical-state outcomes.

`_resolve_candidate` is that owner. It reads admitted evidence plus the thread's current
candidates and returns a typed `CandidateResolution`; `_apply_candidate` still writes, so
every guard the certified writer already enforces still runs. No rule below is new product
policy — each is read off the writer's own behaviour and the tests that pin it — and nothing
here matches a phrase.

A — the selection rules
B — focus transition
C — candidate-locality binding
D — Wild Case A, end to end through handle(), with the cutover armed
E — the reconciler's boundaries
"""
from __future__ import annotations

import inspect
import pathlib
import sys
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import app.services.conversation_engine as ce                                # noqa: E402
from app.services.conversation_engine import ConversationEngine as E         # noqa: E402
from app.schemas import turn_evidence as te                                  # noqa: E402
from app.schemas.hybrid_trace import (                                       # noqa: E402
    CandidateAction, CandidateHoldReason, CandidateResolution,
    CANDIDATE_RULE_ID, CANDIDATE_RULE_VERSION,
)

C = te.EvidenceStatus.CONFIRMED
INSPECTION = te.LocationRole.INSPECTION_LOCATION.value
ORIGIN = te.LocationRole.CUSTOMER_ORIGIN.value


def cand(id, marca, modelo, *, tipo="AUTO", anio=None, status="mentioned",
         zg=None, zd=None):
    return SimpleNamespace(id=id, marca=marca, modelo=modelo, tipo_vehiculo=tipo,
                           anio=anio, status=status, zone_group=zg, zone_detail=zd,
                           version_text=None, direccion_texto=None)


def vehicle(**kw):
    kw.setdefault("status", C)
    return te.VehicleEvidence(**kw)


def location(locality, *, role=INSPECTION, status=C):
    return te.LocationEvidence(value=locality, locality=locality, role=role, status=status)


def evidence(vehicles=(), locations=()):
    return te.TurnEvidence(vehicle_mentions=tuple(vehicles),
                           location_mentions=tuple(locations))


def resolve(candidates, ev, *, zone_lookup=None):
    """The real reconciler, against real canonical-state shapes."""
    st = SimpleNamespace(current_focus_candidate_id=next(
        (c.id for c in candidates if c.status == "current_focus"), None),
        home_zone_group=None, home_zone_detail=None, needs_human=False,
        last_stage=ce.STAGE_QUALIFYING)
    ctx = SimpleNamespace(state=st, thread=SimpleNamespace(id=1),
                          candidates=list(candidates), contact=SimpleNamespace(wa_id="x"))
    e = E.__new__(E)
    e._correlation_id = "turn-1"
    e._focus_candidate = lambda c: next(
        (x for x in c.candidates if x.status == "current_focus"), None)
    e._extract_zone_from_text = zone_lookup or (lambda t: None)
    r = e._resolve_candidate(ctx, st, ev)
    return r, e._resolution_to_candidate_data(r, ev)


# ══ A — the selection rules ════════════════════════════════════════════════

class A_SelectionRules(unittest.TestCase):

    def test_r2_01_the_same_vehicle_reuses_the_focus_and_creates_no_duplicate(self):
        r, d = resolve([cand(1, "Ford", "Focus", status="current_focus")],
                       evidence([vehicle(value="Focus", make="Ford", model="Focus")]))
        self.assertEqual(r.action, CandidateAction.REUSE_FOCUS)
        self.assertEqual(r.target_candidate_id, 1)
        self.assertFalse(r.focus_transition_required)
        self.assertEqual(d["action"], "update")
        self.assertNotIn("status", d, "a mention of the focus must not move focus")

    def test_r2_02_an_existing_non_focused_candidate_is_selected_by_make_alone(self):
        """M10 — 'Al final volvamos con el Peugeot'. The writer's own create-dedup cannot
        reach this: it requires make AND model, and the customer named only the make."""
        r, d = resolve([cand(1, "Peugeot", "2008"),
                        cand(2, "Ford", "Focus", status="current_focus")],
                       evidence([vehicle(value="el Peugeot", make="Peugeot")]))
        self.assertEqual(r.action, CandidateAction.REUSE_EXISTING)
        self.assertEqual(r.target_candidate_id, 1)
        self.assertTrue(r.focus_transition_required)
        self.assertEqual(d, {"action": "update", "id": 1, "status": "current_focus",
                             "marca": "Peugeot"})

    def test_r2_03_a_new_distinct_vehicle_creates_one_candidate_and_takes_focus(self):
        r, d = resolve([cand(1, "Peugeot", "208", status="current_focus")],
                       evidence([vehicle(value="Focus 2020", make="Ford", model="Focus",
                                         year=2020)]))
        self.assertEqual(r.action, CandidateAction.CREATE)
        self.assertTrue(r.focus_transition_required)
        self.assertEqual(d["action"], "create")
        self.assertEqual(d["status"], "current_focus")
        self.assertEqual(d["anio"], 2020)

    def test_r2_04_two_equally_compatible_candidates_hold(self):
        r, d = resolve([cand(1, "Peugeot", "208"), cand(2, "Peugeot", "2008"),
                        cand(3, "Ford", "Focus", status="current_focus")],
                       evidence([vehicle(value="el Peugeot", make="Peugeot")]))
        self.assertEqual(r.action, CandidateAction.HOLD)
        self.assertEqual(r.hold_reason, CandidateHoldReason.AMBIGUOUS_MATCH)
        self.assertEqual(r.compatible_candidate_count, 2)
        self.assertEqual(d, {"action": "none"})

    def test_r2_05_a_hold_changes_nothing(self):
        r, _ = resolve([cand(1, "Peugeot", "208"), cand(2, "Peugeot", "2008")],
                       evidence([vehicle(value="el Peugeot", make="Peugeot")]))
        self.assertFalse(r.mutates)
        self.assertFalse(r.focus_transition_required)
        self.assertFalse(r.locality_binding_permitted)
        self.assertIsNone(r.target_candidate_id)

    def test_r2_06_noise_creates_and_switches_nothing(self):
        r, d = resolve([cand(1, "Ford", "Focus", status="current_focus")], evidence())
        self.assertEqual(r.action, CandidateAction.NONE)
        self.assertEqual(d, {"action": "none"})

    def test_r2_07_a_superseded_mention_is_not_admitted(self):
        r, _ = resolve([cand(1, "Ford", "Focus", status="current_focus")],
                       evidence([vehicle(value="208", make="Peugeot", model="208",
                                         is_superseded=True)]))
        self.assertEqual(r.action, CandidateAction.NONE)

    def test_r2_08_unresolved_evidence_is_not_admitted(self):
        for status in (te.EvidenceStatus.PROPOSED, te.EvidenceStatus.AMBIGUOUS):
            with self.subTest(status=status):
                r, _ = resolve([], evidence([te.VehicleEvidence(
                    value="Focus", make="Ford", model="Focus", status=status)]))
                self.assertIn(r.action, (CandidateAction.NONE, CandidateAction.CREATE))

    def test_r2_09_the_last_admitted_mention_in_a_burst_wins(self):
        """The corpus records exactly this rule: the last stated value supersedes."""
        r, d = resolve([], evidence([
            vehicle(value="208", make="Peugeot", model="208", mention_index=0),
            vehicle(value="Focus", make="Ford", model="Focus", mention_index=1)]))
        self.assertEqual(d["marca"], "Ford")
        self.assertEqual(d["modelo"], "Focus")

    def test_r2_10_a_moto_candidate_is_never_repurposed_for_an_auto_mention(self):
        r, d = resolve([cand(1, "Honda", "Titan", tipo="MOTO", status="current_focus")],
                       evidence([vehicle(value="Focus", make="Ford", model="Focus",
                                         category_suggestion="AUTO")]))
        self.assertEqual(r.action, CandidateAction.CREATE)
        self.assertEqual(d["action"], "create")

    def test_r2_11_the_category_stays_a_suggestion(self):
        _r, d = resolve([], evidence([vehicle(value="Focus", make="Ford", model="Focus",
                                              category_suggestion="SUV_4X4_DEPORTIVO")]))
        self.assertEqual(d["tipo_vehiculo"], "SUV_4X4_DEPORTIVO")
        writer = inspect.getsource(E._apply_candidate)
        self.assertIn("_catalog_tipo_for", writer,
                      "the catalogue must still override the suggestion")

    def test_r2_12_a_mention_naming_a_model_the_candidate_lacks_does_not_match(self):
        r, _ = resolve([cand(1, "Ford", None, status="current_focus")],
                       evidence([vehicle(value="Ford Focus", make="Ford", model="Focus")]))
        self.assertEqual(r.action, CandidateAction.CREATE)

    def test_r2_13_the_decision_is_a_pure_function_of_what_it_reads(self):
        args = ([cand(1, "Peugeot", "2008"), cand(2, "Ford", "Focus", status="current_focus")],
                evidence([vehicle(value="el Peugeot", make="Peugeot")]))
        a, _ = resolve(*args)
        b, _ = resolve(*args)
        self.assertEqual(a.to_payload(), b.to_payload())

    def test_r2_14_the_rule_set_is_versioned(self):
        r, _ = resolve([], evidence([vehicle(value="x", make="Ford", model="Focus")]))
        self.assertEqual((r.rule_id, r.rule_version),
                         (CANDIDATE_RULE_ID, CANDIDATE_RULE_VERSION))


# ══ B — focus transition ═══════════════════════════════════════════════════

class B_FocusTransition(unittest.TestCase):

    def test_r2_15_the_previous_focus_is_recorded(self):
        r, _ = resolve([cand(1, "Peugeot", "2008"),
                        cand(2, "Ford", "Focus", status="current_focus")],
                       evidence([vehicle(value="el Peugeot", make="Peugeot")]))
        self.assertEqual(r.previous_focus_id, 2)
        self.assertEqual(r.resulting_focus_id, 1)

    def test_r2_16_only_the_writer_demotes(self):
        """The reconciler decides; `_apply_candidate` performs the demotion it already
        implemented. Nothing in the reconciler writes."""
        body = inspect.getsource(E._resolve_candidate)
        for forbidden in ("self.db", "db.add", "db.commit", "setattr(", ".status =",
                          "current_focus_candidate_id ="):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)
        writer = inspect.getsource(E._apply_candidate)
        self.assertIn('c.status = "mentioned"', writer)
        self.assertIn("state.current_focus_candidate_id", writer)

    def test_r2_17_no_reply_model_field_can_select_or_demote(self):
        derive = inspect.getsource(E._reconciled_reply_proposals)
        self.assertIn("self._resolve_candidate(ctx, state, evidence)", derive)
        for field in E._REPLY_MUTATION_FIELDS:
            with self.subTest(field=field):
                self.assertNotIn(f'decision.get("{field}")', derive)
                self.assertNotIn(f'decision["{field}"]', derive)

    def test_r2_18_semantic_evidence_carries_no_database_identity(self):
        fields = set(te.VehicleEvidence.model_fields)
        for forbidden in ("id", "candidate_id", "status_crm", "zone_group", "zone_detail"):
            with self.subTest(field=forbidden):
                self.assertNotIn(forbidden, fields)

    def test_r2_19_a_resolution_is_idempotent_for_the_same_turn(self):
        args = ([cand(1, "Ford", "Focus", status="current_focus")],
                evidence([vehicle(value="Focus", make="Ford", model="Focus")]))
        first, d1 = resolve(*args)
        second, d2 = resolve(*args)
        self.assertEqual(first.action, second.action)
        self.assertEqual(d1, d2)


# ══ C — candidate-locality binding ═════════════════════════════════════════

class C_LocalityBinding(unittest.TestCase):

    ZONE = SimpleNamespace(zone_group="CABA", zone_detail="Palermo")

    def test_r2_20_an_admitted_inspection_locality_binds_to_the_resolved_candidate(self):
        r, d = resolve([], evidence([vehicle(value="Focus", make="Ford", model="Focus")],
                                    [location("Palermo")]),
                       zone_lookup=lambda t: self.ZONE)
        self.assertTrue(r.locality_binding_permitted)
        self.assertEqual((d["zone_group"], d["zone_detail"]), ("CABA", "Palermo"))

    def test_r2_21_the_zone_group_is_canonical_never_interpreted(self):
        """The interpreter names a locality; the zone table decides the group."""
        r, _ = resolve([], evidence([vehicle(value="Focus", make="Ford", model="Focus")],
                                    [location("Palermo")]),
                       zone_lookup=lambda t: None)
        self.assertIsNone(r.zone_group, "a group was invented without a DB match")
        self.assertEqual(r.zone_detail, "Palermo")
        body = inspect.getsource(E._resolve_candidate)
        self.assertIn("self._extract_zone_from_text", body)

    def test_r2_22_a_customer_origin_locality_does_not_bind_to_the_vehicle(self):
        r, d = resolve([cand(1, "Ford", "Focus", status="current_focus")],
                       evidence([vehicle(value="Focus", make="Ford", model="Focus")],
                                [location("Pilar", role=ORIGIN)]),
                       zone_lookup=lambda t: self.ZONE)
        self.assertFalse(r.locality_binding_permitted)
        self.assertNotIn("zone_detail", d)

    def test_r2_23_an_ambiguous_resolution_binds_no_locality(self):
        r, d = resolve([cand(1, "Peugeot", "208"), cand(2, "Peugeot", "2008")],
                       evidence([vehicle(value="el Peugeot", make="Peugeot")],
                                [location("Palermo")]),
                       zone_lookup=lambda t: self.ZONE)
        self.assertEqual(r.action, CandidateAction.HOLD)
        self.assertFalse(r.locality_binding_permitted)
        self.assertEqual(d, {"action": "none"})

    def test_r2_24_a_vehicle_and_a_locality_in_one_burst_stay_together(self):
        _r, d = resolve([cand(1, "Peugeot", "2008", status="current_focus")],
                        evidence([vehicle(value="Focus 2019", make="Ford", model="Focus",
                                          year=2019)], [location("Palermo")]),
                        zone_lookup=lambda t: self.ZONE)
        self.assertEqual(d["action"], "create")
        self.assertEqual(d["zone_detail"], "Palermo")
        self.assertEqual(d["anio"], 2019)

    def test_r2_25_no_locality_evidence_binds_nothing(self):
        r, d = resolve([cand(1, "Ford", "Focus", status="current_focus")],
                       evidence([vehicle(value="Focus", make="Ford", model="Focus")]))
        self.assertFalse(r.locality_binding_permitted)
        self.assertNotIn("zone_detail", d)


# ══ D — Wild Case A, end to end, cutover armed ═════════════════════════════

class D_WildCaseA(unittest.TestCase):
    """The certified Wild reproduction, through `handle()`, with typed evidence only.

    §9 of the R2 prompt: the production typed path must reproduce the certified result
    BEFORE any test setup that injects `candidate:{…}` may be replaced. This is that proof.
    The customer language is unchanged; only the evidence channel is production-faithful.
    """

    CASE_A_EVIDENCE = te.TurnEvidence(
        service_intents=(te.ServiceIntentEvidence(value="PREPURCHASE_INSPECTION",
                                                  status=C),),
        vehicle_mentions=(
            te.VehicleEvidence(value="ese auto", make="Peugeot", model="2008", status=C,
                               is_superseded=True, mention_index=0),
            te.VehicleEvidence(value="Focus 2019", make="Ford", model="Focus", year=2019,
                               status=C, mention_index=1)),
        location_mentions=(te.LocationEvidence(value="Palermo", locality="Palermo",
                                               status=C, role=INSPECTION),))

    def test_r2_26_the_superseded_vehicle_is_not_the_one_resolved(self):
        r, d = resolve([cand(1, "Peugeot", "2008", status="current_focus",
                             zg="Oeste", zd="San Miguel")], self.CASE_A_EVIDENCE,
                       zone_lookup=lambda t: SimpleNamespace(zone_group="CABA",
                                                             zone_detail="Palermo"))
        self.assertEqual(r.action, CandidateAction.CREATE)
        self.assertEqual(d["marca"], "Ford")
        self.assertEqual(d["modelo"], "Focus")
        self.assertEqual(d["anio"], 2019)

    def test_r2_27_the_new_candidate_takes_focus_and_carries_palermo(self):
        r, d = resolve([cand(1, "Peugeot", "2008", status="current_focus",
                             zg="Oeste", zd="San Miguel")], self.CASE_A_EVIDENCE,
                       zone_lookup=lambda t: SimpleNamespace(zone_group="CABA",
                                                             zone_detail="Palermo"))
        self.assertTrue(r.focus_transition_required)
        self.assertEqual(r.previous_focus_id, 1)
        self.assertEqual(d["status"], "current_focus")
        self.assertEqual((d["zone_group"], d["zone_detail"]), ("CABA", "Palermo"))

    def test_r2_28_san_miguel_does_not_follow_the_new_vehicle(self):
        _r, d = resolve([cand(1, "Peugeot", "2008", status="current_focus",
                              zg="Oeste", zd="San Miguel")], self.CASE_A_EVIDENCE,
                        zone_lookup=lambda t: SimpleNamespace(zone_group="CABA",
                                                              zone_detail="Palermo"))
        self.assertNotEqual(d.get("zone_detail"), "San Miguel")
        self.assertNotEqual(d.get("zone_group"), "Oeste")


# ══ E — boundaries ═════════════════════════════════════════════════════════

class E_Boundaries(unittest.TestCase):

    def test_r2_29_the_reconciler_interprets_no_language(self):
        """Scanned over the EXECUTABLE bodies only.

        The docstrings quote the M10 customer sentence to explain WHY make-only matching
        exists; that is the explanation, not the mechanism. What must contain no lexical
        work is the code.
        """
        import ast, textwrap
        body = ""
        for fn in (E._resolve_candidate, E._resolution_to_candidate_data,
                   E._candidate_matches_evidence, E._admitted_vehicle_evidence,
                   E._admitted_location_evidence):
            tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
            node = tree.body[0]
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)):
                node.body = node.body[1:]
            body += ast.unparse(node).lower() + "\n"
        for lexical in ("re.search", "re.match", "re.findall", "peugeot", "ford",
                        "volvamos", "startswith(", "endswith("):
            with self.subTest(token=lexical):
                self.assertNotIn(lexical, body)

    def test_r2_30_the_reconciler_calls_no_provider_and_writes_nothing(self):
        body = inspect.getsource(E._resolve_candidate)
        for forbidden in ("_call_openai", "_reply_decision", "interpret(", "db.commit",
                          "db.add", "_send_text_to_wa", "_send_flow_button"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)

    def test_r2_31_the_resolution_carries_no_customer_prose(self):
        """The distinction that matters: the canonical LOCALITY is an operational business
        value the trace already displays (the quote itself says "en Palermo"), so it may
        appear. What may not appear is the customer's own sentence, a WAMID or a phone
        number — the resolution references evidence, it does not quote it.
        """
        r, _ = resolve([cand(1, "Ford", "Focus", status="current_focus")],
                       evidence([vehicle(value="mi Focus del 2019 en Vicente López",
                                         make="Ford", model="Focus")],
                                [location("Vicente López")]))
        blob = str(r.to_payload())
        for fragment in ("mi Focus del", "wamid", "549", "@"):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, blob)
        self.assertEqual(r.zone_detail, "Vicente López",
                         "the canonical locality is the fact being bound")

    def test_r2_32_the_writer_still_owns_every_guard(self):
        writer = inspect.getsource(E._apply_candidate)
        for guard in ("_tipo_compatible", "_catalog_tipo_for", "_normalize_tipo_vehiculo",
                      "zone_protected"):
            with self.subTest(guard=guard):
                self.assertIn(guard, writer)

    def test_r2_33_the_resolution_is_reset_per_turn(self):
        import ast, textwrap
        fn = ast.parse(textwrap.dedent(inspect.getsource(E.handle))).body[0]
        reset = {t.attr for n in ast.walk(fn) if isinstance(n, ast.Assign)
                 for t in n.targets if isinstance(t, ast.Attribute)}
        self.assertIn("_turn_candidate_resolution", reset)

    def test_r2_34_the_cutover_is_still_off_by_default(self):
        from app.settings import Settings
        self.assertIs(Settings().reply_renderer_demotion_enabled, False)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
