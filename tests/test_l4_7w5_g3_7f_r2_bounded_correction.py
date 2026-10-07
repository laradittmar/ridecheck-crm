"""L4.7W5-GATE3-G3-7F-R2 — the bounded correction to the G3-7F foundation.

Every test here exists because the G3-7F independent review proved something specific was
wrong, and each class names the finding it closes:

    A  G37FRR-01  the Tier 1 echo was write-only state; a year applied silently
    B  G37FRR-02  the typed proposition had no composer consumer; prose lived in the engine
    C  G37FRR-03  three outstanding proposals did not block arming
    D  G37FRR-04  a stale proposal refused corrections forever, one sentence per turn
    E  G37FRR-05  provisional ON / grounding OFF armed on the ungrounded v1 target
    F  G37FRR-08  a post-acceptance delivery failure kept confirmation authority
       G37FRR-10  a failed Meta send was reported as `replied`
    G  G37FRR-06  an identity correction kept the old catalogue category
       G37FRR-07  the same turn then spawned a phantom candidate
    H  G37FRR-09  SUBFAIL was invisible to the regression method
    I             invariants that must survive all of the above
    J             mutation controls: each reintroduces one defect and must be caught

The review's own probes are not reused: where a fact matters it is re-derived here from the
production function, so a shared helper cannot make both the code and its test agree on
something false.
"""
from __future__ import annotations

import inspect
import pathlib
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from app.services import pending_correction as pc                        # noqa: E402
from app.services import response_composition as rc                      # noqa: E402
from app.services.conversation_engine import ConversationEngine as E     # noqa: E402
from app.services.conversation_engine import _tipo_compatible            # noqa: E402
from app.services.response_composition import (LiveResponsePlan,         # noqa: E402
                                               PropositionId,
                                               RequiredPropositionUnavailable)

import _r2_regression_accounting as ra                                   # noqa: E402

# ── G3-7F sequencing migration: the clarification authority moved ─────────────────
# `_route_clarification` + `_dispatch_clarification_route` have been replaced by ONE
# memoized decision (`_turn_clarification_decision`) placed after the turn's reconciled
# proposal exists, and ONE executor (`_execute_clarification`) that reserves a durable
# attempt before transport. These adapters keep every assertion below pointed at the same
# behavioural claim: the route and reason this turn chose, and what executing it does.
def _route(eng, ctx, state, resolution=None, disposition=None, **kw):
    """`(route, reason)` from the current authority. Resets the per-turn memo first."""
    eng._turn_clarification_decision_obj = None
    d = eng._turn_clarification_decision(
        ctx, state, resolution=resolution, disposition=disposition, **kw)
    return d.route, d.reason


def _dispatch(eng, ctx, state, route, reason):
    """Execute a route through the single reserving authority."""
    from app.services.clarification_router import TurnClarificationDecision as _TCD
    fam = ("VEHICLE" if route == "VEHICLE_FLOW_REQUIRED"
           else "LOCATION" if route == "LOCATION_FLOW_REQUIRED" else None)
    return eng._execute_clarification(ctx, state, _TCD(route, reason, family=fam))


ENGINE_SRC = pathlib.Path(
    inspect.getsourcefile(E)).read_text(encoding="utf-8")                # noqa: E501


def _code_only(fn) -> str:
    """A function's source with docstrings removed.

    Prose explaining that a word must never be matched would otherwise fail a test looking
    for that word. The check is about executable code, so that is what it reads.
    """
    import ast as _ast
    import textwrap
    tree = _ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef,
                             _ast.Module)):
            body = node.body
            if (body and isinstance(body[0], _ast.Expr)
                    and isinstance(body[0].value, _ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [_ast.Pass()]
    return _ast.unparse(tree)


# ── harness ──────────────────────────────────────────────────────────────────────

def row(cid=1, marca="Honda", modelo="Titan", anio=2019, tipo="MOTO",
        status="current_focus", **over):
    base = dict(id=cid, marca=marca, modelo=modelo, anio=anio, tipo_vehiculo=tipo,
                status=status, zone_group="CABA", zone_detail="Palermo",
                version_text=None, direccion_texto=None, source_text=None)
    base.update(over)
    return SimpleNamespace(**base)


def state(**over):
    base = dict(pending_correction=None, current_focus_candidate_id=1,
                home_zone_group="CABA", home_zone_detail="Palermo", needs_human=False)
    for f in pc.LEGACY_PROPOSAL_FIELDS:
        base.setdefault(f, None)
    base.update(over)
    return SimpleNamespace(**base)


def resolution(fields, *, basis="G2_SINGLETON", cid=1):
    return SimpleNamespace(
        target_candidate_id=cid, grounding_basis=basis,
        target_relation="CORRECT_EXISTING", target_scope_value="CURRENT_REFERENT",
        corrected_fields=tuple(fields), correction_evidence_ref="corrections[0]",
        vehicle_evidence_ref=None, location_evidence_ref=None)


def engine(*, grounding=True, provisional=True, rows=None, catalog=None):
    e = E.__new__(E)
    e.settings = SimpleNamespace(correction_target_grounding_enabled=grounding,
                                 provisional_correction_enabled=provisional)
    e._correlation_id = "turn-1"
    e._canonical_turn_burst = SimpleNamespace(ordered_message_ids=("m1",))
    e._reconciler_cycle_id = lambda _st: "c1"
    e._live_plan = LiveResponsePlan(turn_id="turn-1")
    e._turn_correction_echo = ()
    e._turn_correction_proposition = None
    e._turn_correction_moto = False
    e._turn_correction_category = None
    e._decision_log = lambda *a, **k: None
    table = catalog if catalog is not None else {("Ford", "Focus"): "AUTO"}
    e._catalog_tipo_for = lambda a, b: table.get((a, b))
    rows = rows if rows is not None else [row()]
    # `created` records what the writer INSERTED. `ctx.candidates` also receives the new
    # object, so counting that list alone cannot distinguish an insert from a patch.
    e.created = []

    def _add(obj):
        e.created.append(obj)
        rows.append(obj)

    e.db = SimpleNamespace(add=_add, flush=lambda: None, commit=lambda: None)
    return e, rows


def ctx_for(rows, st):
    return SimpleNamespace(state=st, candidates=rows, thread=SimpleNamespace(id=1),
                           contact=SimpleNamespace(wa_id="x"))


def out_row(mid=11, *, status="sent", wamid="wamid.C1", thread_id=1, direction="out"):
    return SimpleNamespace(id=mid, status=status, wa_message_id=wamid,
                           thread_id=thread_id, direction=direction)


def pending(fields=(("anio", 2019, 2018),), *, cid=1, cycle="c1", basis="G2_SINGLETON"):
    return pc.PendingCorrection.build(
        thread_id=1, turn_id="t1", target_candidate_id=cid, grounding_basis=basis,
        relation="CORRECT_EXISTING", target_scope="CURRENT_REFERENT",
        corrected_fields=list(fields), evidence_refs=("corrections[0]",),
        cycle_id=cycle, origin_message_ids=("m1",))


def admit(p, **kw):
    kw.setdefault("state", state())
    kw.setdefault("candidates", [row(marca="Ford", modelo="Focus", anio=2019, tipo="AUTO")])
    kw.setdefault("cycle_id", "c1")
    kw.setdefault("latest_outbound_message_id", p.confirmation_message_id)
    kw.setdefault("newer_correction_this_turn", False)
    kw.setdefault("confirmation_row", out_row(p.confirmation_message_id or 11))
    kw.setdefault("thread_id", 1)
    return pc.confirmation_admissible(p, **kw)


# ══ A — the Tier 1 echo is authorized by the WRITE (G37FRR-01) ════════════════════

class A_TierOneEcho(unittest.TestCase):

    def test_r2_01_the_echo_is_assigned_after_the_writer_runs(self):
        """Requirement 3. Source order, not a comment: the assignment must appear after the
        `_apply_candidate` call in the gate, or a failed write could still echo."""
        gate = ENGINE_SRC[ENGINE_SRC.index("G3-7F Commit 3 + G3-7F-R2"):]
        gate = gate[:gate.index("Catalog enforcement (post-AI)")]
        write_at = gate.index("_outcome = self._apply_candidate(")
        echo_at = gate.index("self._turn_correction_echo = tuple(")
        self.assertLess(write_at, echo_at,
                        "the echo must be authorized by the write, not precede it")

    def test_r2_02_a_writer_that_reports_nothing_authorizes_no_echo(self):
        """Requirement 2. `_writer_applied` is the one predicate that gates the echo."""
        for outcome in (None, (False, None), (), "ok", (True,), object(),
                        SimpleNamespace(), (None, None)):
            with self.subTest(outcome=type(outcome).__name__ + str(outcome)[:18]):
                self.assertFalse(E._writer_applied(outcome))
        self.assertTrue(E._writer_applied((True, None)))
        self.assertTrue(E._writer_applied((True, 7)))

    def test_r2_03_a_mock_writer_never_authorizes_an_echo(self):
        """A MagicMock stands in for the writer all over this suite; it must read as
        "reported nothing" rather than as a successful write."""
        from unittest.mock import MagicMock
        self.assertFalse(E._writer_applied(MagicMock()))

    def test_r2_04_the_echo_carries_only_the_corrected_fields(self):
        """Requirements 5 and 6: nothing unrelated may travel with it."""
        e, rows = engine()
        res = resolution([("anio", 2019, 2018)])
        e._turn_correction_echo = tuple((f, a) for f, _b, a in res.corrected_fields)
        self.assertEqual(e._turn_correction_echo, (("anio", 2018),))


# ══ B — the composer owns the words (G37FRR-02) ═══════════════════════════════════

class B_ComposerAuthority(unittest.TestCase):

    def test_r2_05_the_engine_holds_no_correction_prose(self):
        """Requirement 6 / Part A.10: the interim renderer is gone, not merely unused."""
        self.assertFalse(hasattr(E, "_render_correction_confirmation"))
        self.assertNotIn("¿Es correcto?", ENGINE_SRC)
        self.assertNotIn("Perfecto, entonces es", ENGINE_SRC)

    def test_r2_06_neither_typed_value_is_write_only_any_more(self):
        """Part A.10. Both were assigned and read by nothing; each must now have a reader."""
        producer = inspect.getsource(E._register_correction_propositions)
        for name in ("_turn_correction_echo", "_turn_correction_proposition"):
            with self.subTest(field=name):
                self.assertIn(f'getattr(self, "{name}"', producer,
                              f"{name} has no consumer that reads it off the turn")

    def test_r2_07_the_producer_is_registered_and_required(self):
        """Requirement 4 and Part B's fail-closed clause."""
        entry = [p for p in E._RESPONSE_PRODUCERS if p[0] == "correction"]
        self.assertEqual(len(entry), 1, "exactly one correction producer")
        self.assertEqual(entry[0][1], "_register_correction_propositions")
        self.assertTrue(entry[0][2], "a dropped correction must degrade the turn")

    def test_r2_08_the_applied_echo_reaches_the_plan(self):
        """Requirement 1."""
        e, rows = engine()
        st = state()
        e._turn_correction_echo = (("anio", 2018),)
        e._register_correction_propositions(ctx_for(rows, st), "")
        ids = [p.proposition_id for p in e._live_plan.propositions]
        self.assertIn(PropositionId.CORRECTION_APPLIED, ids)
        text = e._live_plan.compose("")
        self.assertIn("2018", text)
        self.assertEqual(text, "Perfecto, entonces es modelo 2018.",
                         "the conformance example from Part A")

    def test_r2_09_the_confirmation_proposition_reaches_the_plan(self):
        """Requirement 4."""
        e, rows = engine()
        st = state()
        p = pending()
        e._turn_correction_proposition = e._correction_confirmation_proposition(p)
        e._register_correction_propositions(ctx_for(rows, st), "")
        ids = [q.proposition_id for q in e._live_plan.propositions]
        self.assertIn(PropositionId.CORRECTION_CONFIRMATION_REQUIRED, ids)
        self.assertIn("2018", e._live_plan.compose(""))

    def test_r2_10_faq_plus_confirmation_is_one_message(self):
        """Requirement 5 / Part B. Two propositions, one outbound body."""
        from app.schemas.hybrid_trace import (DecisionAuthority, FactSource,
                                              RendererSource, ResponseItemKind,
                                              ResponsePolicy)
        from app.services.response_composition import Proposition
        e, rows = engine()
        st = state()
        e._live_plan.register(Proposition(
            proposition_id=PropositionId.faq("hours"), kind=ResponseItemKind.FAQ_ANSWER,
            claim_family="faq", render=lambda: "Atendemos de 9 a 18 hs.",
            decision_authority=DecisionAuthority.DETERMINISTIC_RULE,
            fact_sources=(FactSource.FAQ_CANONICAL,),
            renderer_source=RendererSource.DETERMINISTIC_TEMPLATE,
            policy=ResponsePolicy.P1_HOLD_PLUS_SAFE_FAQ, priority=40))
        e._turn_correction_proposition = e._correction_confirmation_proposition(pending())
        e._register_correction_propositions(ctx_for(rows, st), "")
        text = e._live_plan.compose("")
        self.assertIn("9 a 18", text)
        self.assertIn("2018", text)
        self.assertEqual(text.count("¿es correcto?"), 1, "asked exactly once")

    def test_r2_11_an_unrenderable_proposition_fails_closed(self):
        """Part B's last clause: a missing renderer must not silently drop a confirmation."""
        e, rows = engine()
        st = state()
        e._turn_correction_proposition = {"proposition_id": "x",
                                          "fields": [{"field": "zone_group",
                                                      "proposed": "Norte"}]}
        with self.assertRaises(RequiredPropositionUnavailable):
            e._register_correction_propositions(ctx_for(rows, st), "")

    def test_r2_12_the_confirmation_carries_no_price_or_slot(self):
        """Part B: the confirmation must not contain a quote or provisional price."""
        text = rc.render_correction_confirmation(
            [("anio", 2018), ("zone_detail", "Pilar")])
        for forbidden in ("$", "precio", "sale", "hs", "turno", "revisión sale"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, text)

    def test_r2_13_zone_group_is_never_spoken(self):
        """It is an administrative grouping the customer never named."""
        self.assertEqual(rc.correction_fragments([("zone_group", "Norte")]), [])
        self.assertEqual(rc.render_correction_applied([("zone_group", "Norte")]), "")

    def test_r2_14_no_phrase_catalogue_entered_the_composer(self):
        """Requirement 38. The phrase table maps CANONICAL FIELDS to wording; it must not
        become a table of customer utterances."""
        self.assertEqual(set(rc._CORRECTION_PHRASE),
                         {"anio", "marca", "modelo", "version_text", "tipo_vehiculo",
                          "zone_detail"})
        for template in rc._CORRECTION_PHRASE.values():
            with self.subTest(template=template):
                self.assertIn("{value}", template)


# ══ C — legacy-proposal completeness (G37FRR-03) ══════════════════════════════════

class C_LegacyProposals(unittest.TestCase):

    def test_r2_15_the_three_proven_omissions_are_present(self):
        """Requirements 8, 9, 10."""
        for field in ("vehicle_fallback_flow_sent", "location_fallback_flow_sent",
                      "last_visible_slots"):
            with self.subTest(field=field):
                self.assertIn(field, pc.LEGACY_PROPOSAL_FIELDS)

    def test_r2_16_every_listed_field_is_a_real_thread_state_column(self):
        """Part C: prove each listed field exists in the canonical model, so the list cannot
        drift into naming something that is never set."""
        from app.models import WhatsAppThreadState
        columns = set(WhatsAppThreadState.__table__.c.keys())
        for field in pc.LEGACY_PROPOSAL_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, columns)

    def test_r2_17_every_listed_field_has_a_lifecycle_in_the_engine(self):
        """Part C: a field nothing ever sets is not a proposal."""
        for field in pc.LEGACY_PROPOSAL_FIELDS:
            with self.subTest(field=field):
                self.assertIn(f"state.{field}", ENGINE_SRC,
                              f"{field} is never written by the engine")

    def test_r2_18_each_field_has_exactly_one_family(self):
        seen = {}
        for field, family in pc.LEGACY_PROPOSALS:
            self.assertNotIn(field, seen, f"{field} listed twice")
            self.assertIn(family, pc.ProposalFamily.ALL)
            seen[field] = family
        self.assertEqual(len(seen), len(pc.LEGACY_PROPOSAL_FIELDS))

    def test_r2_19_activity_is_a_value_not_a_clock(self):
        """Part D.7: no TTL may decide staleness."""
        st = state()
        self.assertEqual(pc.active_proposals(st), ())
        for value, active in ((True, True), (False, False), (None, False), ("", False),
                              ("   ", False), ("tok", True), (0, False), (1, False)):
            with self.subTest(value=repr(value)):
                st.flow_booking_token = value
                self.assertEqual(pc.proposal_is_active(st, "flow_booking_token"), active)
        body = inspect.getsource(pc.proposal_is_active)
        body = body[body.index('"""', body.index('"""') + 3) + 3:]   # code only
        for forbidden in ("datetime", "utcnow", "timedelta", "time."):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, body)

    def test_r2_20_a_sound_unanswered_proposal_still_refuses_arming(self):
        """Requirement 7. The bounded guarantee is unchanged for genuine competition."""
        e, rows = engine()
        st = state(inspectability_clarification_sent=True)
        p, why = e._arm_pending_correction(ctx_for(rows, st), st,
                                           resolution([("anio", 2019, 2018)]))
        self.assertIsNone(p)
        self.assertEqual(why, pc.InvalidationReason.LEGACY_PROPOSAL_ACTIVE)


# ══ D — finite stale-proposal recovery (G37FRR-04) ════════════════════════════════

class D_FiniteRecovery(unittest.TestCase):

    def test_r2_21_an_identity_correction_invalidates_a_booking_flow(self):
        """Requirements 11 and 12. The Flow was computed from a category that just moved."""
        d = pc.proposal_disposition(state(flow_booking_token="tok-abandoned"),
                                    [("marca", "Honda", "Ford")])
        self.assertIn("flow_booking_token", d["invalidated"])
        self.assertEqual(d["conflicting"], ())

    def test_r2_22_withdrawal_uses_the_canonical_field_values(self):
        """Part D.2: the same fields and the same cleared values as `_execute_cycle_reset`."""
        e, rows = engine()
        st = state(flow_booking_token="tok", last_visible_slots='[{"d":"x"}]',
                   vehicle_fallback_flow_sent=True)
        d = pc.proposal_disposition(st, [("marca", "Honda", "Ford")])
        e._withdraw_dependent_proposals(ctx_for(rows, st), st, d)
        self.assertIsNone(st.flow_booking_token)
        self.assertIsNone(st.last_visible_slots)
        self.assertIs(st.vehicle_fallback_flow_sent, False,
                      "a boolean proposal clears to False, never to None")

    def test_r2_23_a_vehicle_correction_resolves_a_vehicle_fallback(self):
        """Requirement 13."""
        d = pc.proposal_disposition(state(vehicle_fallback_flow_sent=True),
                                    [("modelo", "Titan", "Focus")])
        self.assertIn("vehicle_fallback_flow_sent", d["resolved"])

    def test_r2_24_a_locality_correction_resolves_a_locality_fallback(self):
        """Requirement 14."""
        d = pc.proposal_disposition(state(location_fallback_flow_sent=True,
                                          pending_location_proposal="CABA||Palermo"),
                                    [("zone_detail", "Palermo", "Pilar")])
        self.assertEqual(set(d["resolved"]),
                         {"location_fallback_flow_sent", "pending_location_proposal"})

    def test_r2_25_the_fuzzy_pair_is_withdrawn_together(self):
        e, rows = engine()
        st = state(pending_fuzzy_catalog_key="Ford||Focus",
                   pending_turn_evidence_text="el focus")
        d = pc.proposal_disposition(st, [("modelo", "Titan", "Focus")])
        e._withdraw_dependent_proposals(ctx_for(rows, st), st, d)
        self.assertIsNone(st.pending_fuzzy_catalog_key)
        self.assertIsNone(st.pending_turn_evidence_text)

    def test_r2_26_a_year_only_correction_preserves_a_booking_proposal(self):
        """Part D.4, and the source fact behind it: `anio` is in no quote input."""
        d = pc.proposal_disposition(state(flow_booking_token="tok"),
                                    [("anio", 2019, 2018)])
        self.assertEqual(d["invalidated"], ())
        self.assertEqual(d["conflicting"], ("flow_booking_token",))
        from app.services.acceptance_authorizer import CommercialState
        self.assertNotIn("anio", inspect.getsource(
            CommercialState.quote_inputs_unchanged))
        self.assertNotIn("anio", str(inspect.signature(
            __import__("app.services.pricing", fromlist=["PricingService"])
            .PricingService.quote)))

    def test_r2_27_the_free_text_clarification_budget_is_gone(self):
        """OWNER-RESOLUTION Part D supersedes R2's budget entirely.

        R2 metered a free-text clarification with a marker stored inside
        `pending_correction`; the independent review proved the bound was not per-cycle and
        had no atomic guard. The owner's resolution removes the mechanism rather than
        repairing it, because the project already owns two structured Flows that are the one
        clarification attempt. There is no counter left to overwrite.
        """
        for gone in ("clarification_marker", "clarifications_used",
                     "clarification_budget_remains", "CLARIFICATION_BUDGET",
                     "CLARIFICATION_BUDGET_SPENT"):
            with self.subTest(symbol=gone):
                self.assertFalse(hasattr(pc, gone), f"{gone} must not come back")
        self.assertFalse(hasattr(E, "_spend_correction_clarification"))

    def test_r2_28_pending_correction_has_one_typed_meaning_again(self):
        """The column carries a proposal or nothing — never a second record kind."""
        st = state()
        self.assertIsNone(pc.load(st))
        st.pending_correction = {"kind": "correction_clarification", "count": 1,
                                 "cycle_id": "c1"}
        self.assertIsNone(pc.load(st), "a stale R2 marker still reads as no proposal")
        self.assertEqual(pc.has_blocking_pending_correction(st), (False, None))
        p = pending()
        st.pending_correction = p.to_payload()
        self.assertIsNotNone(pc.load(st))

    def test_r2_29_the_flow_sent_flags_are_what_bounds_clarification(self):
        """Part F: boundedness is the canonical Flow lifecycle, not a counter."""
        for family, field, attempted_reason in (
            ("vehicle", "vehicle_flow_attempted",
             pc.ClarificationReason.VEHICLE_FLOW_ALREADY_ATTEMPTED),
            ("location", "location_flow_attempted",
             pc.ClarificationReason.LOCATION_FLOW_ALREADY_ATTEMPTED),
        ):
            with self.subTest(family=family):
                kw = dict(vehicle_domain_proven=True, vehicle_identity_resolved=False)
                if family == "location":
                    kw = dict(location_domain_proven=True, location_resolved=False)
                first = pc.route_clarification(**kw)
                second = pc.route_clarification(**{**kw, field: True})
                self.assertIn(first[0], (pc.ClarificationRoute.VEHICLE_FLOW_REQUIRED,
                                         pc.ClarificationRoute.LOCATION_FLOW_REQUIRED))
                self.assertEqual(second,
                                 (pc.ClarificationRoute.GENERIC_HUMAN_REQUIRED,
                                  attempted_reason))

    def test_r2_30_no_time_based_guess_decides_staleness(self):
        """Part D.7, over the whole recovery path."""
        for fn in (pc.proposal_disposition, pc.route_clarification,
                   pc.proposal_is_active, E._withdraw_dependent_proposals,
                   E._turn_clarification_decision):
            with self.subTest(fn=fn.__name__):
                src = inspect.getsource(fn)
                for token in ("utcnow", "datetime.now", "time.time", "timedelta"):
                    self.assertNotIn(token, src)

    def test_r2_31_withdrawal_never_touches_identity_focus_or_zone(self):
        """The bounded subset: a correction must not reset the conversation."""
        e, rows = engine()
        st = state(flow_booking_token="tok")
        before = (st.current_focus_candidate_id, st.home_zone_group, st.home_zone_detail)
        d = pc.proposal_disposition(st, [("marca", "Honda", "Ford")])
        e._withdraw_dependent_proposals(ctx_for(rows, st), st, d)
        self.assertEqual((st.current_focus_candidate_id, st.home_zone_group,
                          st.home_zone_detail), before)
        self.assertEqual(rows[0].marca, "Honda", "withdrawal writes no candidate field")


# ══ E — the flag dependency (G37FRR-05) ═══════════════════════════════════════════

class E_FlagDependency(unittest.TestCase):

    def test_r2_32_grounding_off_provisional_on_refuses_to_arm(self):
        """Requirement 15 — the configuration the review singled out."""
        e, rows = engine(grounding=False, provisional=True)
        st = state()
        p, why = e._arm_pending_correction(ctx_for(rows, st), st,
                                           resolution([("marca", "Honda", "Ford")]))
        self.assertIsNone(p)
        self.assertEqual(why, pc.InvalidationReason.GROUNDING_NOT_ENABLED)

    def test_r2_33_a_basis_of_none_refuses_to_arm(self):
        """Requirement 17. This is what a v1-selected target looks like."""
        e, rows = engine()
        st = state()
        for basis in ("NONE", "", None):
            with self.subTest(basis=basis):
                p, why = e._arm_pending_correction(
                    ctx_for(rows, st), st,
                    resolution([("marca", "Honda", "Ford")], basis=basis))
                self.assertIsNone(p)
                self.assertEqual(why, pc.InvalidationReason.TARGET_NOT_GROUNDED)

    def test_r2_34_a_missing_target_refuses_to_arm(self):
        e, rows = engine()
        st = state()
        p, why = e._arm_pending_correction(
            ctx_for(rows, st), st, resolution([("marca", "H", "F")], cid=999))
        self.assertIsNone(p)
        self.assertEqual(why, pc.InvalidationReason.TARGET_LOST)

    def test_r2_35_all_four_configurations_are_exercised(self):
        """Requirement 16."""
        expected = {(False, False): pc.InvalidationReason.GROUNDING_NOT_ENABLED,
                    (True, False): None,
                    (True, True): None,
                    (False, True): pc.InvalidationReason.GROUNDING_NOT_ENABLED}
        for (grounding, provisional), why_expected in expected.items():
            with self.subTest(grounding=grounding, provisional=provisional):
                e, rows = engine(grounding=grounding, provisional=provisional)
                st = state()
                p, why = e._arm_pending_correction(
                    ctx_for(rows, st), st, resolution([("marca", "Honda", "Ford")]))
                self.assertEqual(why, why_expected)
                self.assertEqual(p is not None, why_expected is None)

    def test_r2_36_the_gate_itself_requires_both_flags(self):
        gate = ENGINE_SRC[ENGINE_SRC.index("G3-7F Commit 3 + G3-7F-R2"):]
        gate = gate[:gate.index("the category travels WITH the correction")]
        self.assertIn("self._provisional_correction_on()", gate)
        self.assertIn("self._correction_grounding_on()", gate)

    def test_r2_37_only_a_real_true_arms_either_feature(self):
        """Requirement 36's companion: no truthy, mocked or string value may activate one."""
        from unittest.mock import MagicMock
        for value in (False, "true", "True", 1, 0, None, "", [], MagicMock(), object()):
            with self.subTest(value=repr(value)[:24]):
                e = E.__new__(E)
                e.settings = SimpleNamespace(
                    correction_target_grounding_enabled=value,
                    provisional_correction_enabled=value)
                self.assertFalse(e._correction_grounding_on())
                self.assertFalse(e._provisional_correction_on())

    def test_r2_38_both_flags_still_default_false(self):
        """Requirement 36."""
        import dataclasses
        from app.settings import Settings
        fields = {f.name: f for f in dataclasses.fields(Settings)} \
            if dataclasses.is_dataclass(Settings) else None
        if fields is None:
            fields = Settings.model_fields
            for name in ("correction_target_grounding_enabled",
                         "provisional_correction_enabled"):
                with self.subTest(flag=name):
                    self.assertIs(fields[name].default, False)
        else:
            for name in ("correction_target_grounding_enabled",
                         "provisional_correction_enabled"):
                with self.subTest(flag=name):
                    self.assertIs(fields[name].default, False)

    def test_r2_39_admission_also_enforces_the_dependency(self):
        """A patch can outlive the configuration that armed it."""
        p = pending().with_confirmation_message(11)
        ok, why = admit(p, grounding_enabled=False)
        self.assertFalse(ok)
        self.assertEqual(why, pc.InvalidationReason.GROUNDING_NOT_ENABLED)
        ok2, why2 = admit(pending(basis="NONE").with_confirmation_message(11))
        self.assertFalse(ok2)
        self.assertEqual(why2, pc.InvalidationReason.TARGET_NOT_GROUNDED)


# ══ F — delivery truth (G37FRR-08, G37FRR-10) ═════════════════════════════════════

class F_DeliveryTruth(unittest.TestCase):

    def test_r2_40_an_accepted_dispatch_may_grant_authority(self):
        """Requirement 21, for every allowed status."""
        for status in sorted(pc.OUTBOUND_DISPATCH_ACCEPTED):
            with self.subTest(status=status):
                p = pending().with_confirmation_message(11)
                ok, why = admit(p, confirmation_row=out_row(11, status=status))
                self.assertTrue(ok, why)

    def test_r2_41_the_allow_list_is_positive_and_minimal(self):
        self.assertEqual(pc.OUTBOUND_DISPATCH_ACCEPTED, frozenset({"sent", "delivered",
                                                                   "read"}))
        from app.models import WhatsAppMessage
        constraint = " ".join(str(c.sqltext) for c in WhatsAppMessage.__table__.constraints
                             if hasattr(c, "sqltext"))
        for denied in ("pending", "failed", "blocked", "received"):
            with self.subTest(status=denied):
                self.assertIn(denied, constraint, "the lifecycle really has this state")
                self.assertNotIn(denied, pc.OUTBOUND_DISPATCH_ACCEPTED)

    def test_r2_42_a_non_accepted_status_grants_nothing(self):
        """Requirements 18, 19 and 20 — including the post-acceptance revocation."""
        for status in ("pending", "failed", "blocked", "received", "queued", "", None):
            with self.subTest(status=repr(status)):
                p = pending().with_confirmation_message(11)
                ok, why = admit(p, confirmation_row=out_row(11, status=status))
                self.assertFalse(ok)
                self.assertEqual(why, pc.InvalidationReason.CONFIRMATION_NOT_DISPATCHED)

    def test_r2_43_a_post_acceptance_failure_revokes_authority(self):
        """Requirement 20, stated as the review found it: the row was accepted, a status
        webhook later marked it failed, and the id still matched."""
        p = pending().with_confirmation_message(11)
        self.assertTrue(admit(p, confirmation_row=out_row(11, status="delivered"))[0])
        ok, why = admit(p, confirmation_row=out_row(11, status="failed"))
        self.assertFalse(ok, "authority must not survive a delivery failure")
        self.assertEqual(why, pc.InvalidationReason.CONFIRMATION_NOT_DISPATCHED)

    def test_r2_44_a_missing_row_grants_nothing(self):
        p = pending().with_confirmation_message(11)
        ok, why = admit(p, confirmation_row=None)
        self.assertFalse(ok)
        self.assertEqual(why, pc.InvalidationReason.CONFIRMATION_NOT_DISPATCHED)

    def test_r2_45_a_wamid_is_required(self):
        for wamid in (None, "", "   ", 0, 123):
            with self.subTest(wamid=repr(wamid)):
                p = pending().with_confirmation_message(11)
                ok, why = admit(p, confirmation_row=out_row(11, wamid=wamid))
                self.assertFalse(ok)
                self.assertEqual(why, pc.InvalidationReason.CONFIRMATION_NOT_DISPATCHED)

    def test_r2_46_wrong_thread_message_cycle_or_fingerprint_refuses(self):
        """Requirement 22."""
        base = pending().with_confirmation_message(11)
        cases = {
            "wrong thread": dict(confirmation_row=out_row(11, thread_id=99)),
            "wrong message": dict(confirmation_row=out_row(12),
                                  latest_outbound_message_id=11),
            "inbound row": dict(confirmation_row=out_row(11, direction="in")),
            "wrong cycle": dict(cycle_id="c2"),
            "lost target": dict(candidates=[]),
        }
        for label, kw in cases.items():
            with self.subTest(case=label):
                ok, why = admit(base, **kw)
                self.assertFalse(ok, label)
                self.assertIsNotNone(why)
        tampered = base.__class__(**{**base.__dict__, "fingerprint": "deadbeef"})
        ok, why = admit(tampered)
        self.assertFalse(ok)
        self.assertEqual(why, pc.InvalidationReason.FINGERPRINT_MISMATCH)

    def test_r2_47_a_failed_send_is_not_reported_as_replied(self):
        """Requirement 23. `_send_text_to_wa` returns None on a Meta failure and does not
        raise, which is how `replied` was being claimed for an unsent message."""
        for method in (E._request_correction_confirmation,
                       E._hold_for_correction_clarification):
            with self.subTest(method=method.__name__):
                src = inspect.getsource(method)
                self.assertIn("if not sent:", src)
                self.assertIn("dispatch_failed", src)
                blocked_at = src.index("dispatch_failed")
                replied_at = src.rindex('_out("replied"')
                self.assertLess(blocked_at, replied_at,
                                "the failure branch must return before `replied`")

    def test_r2_48_both_failure_outcomes_keep_the_turn_handled(self):
        """A kill-switched or failed correction turn must not fall through to n8n's legacy
        chain — the same omission the G3-7F foundation had to fix once already."""
        from app.schemas.conversation import HANDLED_ACTIONS
        for action in ("correction_confirmation_blocked", "correction_hold_blocked"):
            with self.subTest(action=action):
                self.assertIn(action, HANDLED_ACTIONS)

    def test_r2_49_dispatch_proof_is_its_own_predicate(self):
        """So G3-8 can reuse it without re-deriving the allow-list."""
        ok, why = pc.confirmation_dispatch_proven(
            pending().with_confirmation_message(11), out_row(11), thread_id=1)
        self.assertTrue(ok)
        self.assertIsNone(why)
        ok2, _ = pc.confirmation_dispatch_proven(pending(), None, thread_id=1)
        self.assertFalse(ok2, "a patch with no confirmation id proves nothing")


# ══ G — identity and category consistency (G37FRR-06, G37FRR-07) ══════════════════

class G_CategoryConsistency(unittest.TestCase):

    def test_r2_50_an_identity_correction_carries_the_catalogue_category(self):
        """Requirement 24."""
        e, rows = engine()
        data = e._correction_category(
            ctx_for(rows, state()),
            {"action": "update", "id": 1, "marca": "Ford", "modelo": "Focus"},
            resolution([("marca", "Honda", "Ford"), ("modelo", "Titan", "Focus")]))
        self.assertEqual(data["tipo_vehiculo"], "AUTO")
        self.assertTrue(data["correction_in_place"])

    def test_r2_51_a_ford_focus_cannot_remain_a_moto(self):
        """Requirement 25 — the review's exact reproduction, now end to end."""
        e, rows = engine()
        st = state()
        ctx = ctx_for(rows, st)
        data = e._correction_category(
            ctx, {"action": "update", "id": 1, "marca": "Ford", "modelo": "Focus"},
            resolution([("marca", "Honda", "Ford"), ("modelo", "Titan", "Focus")]))
        applied = e._apply_candidate(ctx, data)
        self.assertTrue(E._writer_applied(applied))
        self.assertEqual((rows[0].marca, rows[0].modelo, rows[0].tipo_vehiculo),
                         ("Ford", "Focus", "AUTO"))

    def test_r2_52_a_correction_turn_creates_no_phantom_candidate(self):
        """Requirements 28 and 29. MOTO and AUTO are incompatible, which is exactly what
        used to redirect the update into a create."""
        self.assertFalse(_tipo_compatible("MOTO", "AUTO"))
        e, rows = engine()
        st = state()
        ctx = ctx_for(rows, st)
        data = e._correction_category(
            ctx, {"action": "update", "id": 1, "marca": "Ford", "modelo": "Focus"},
            resolution([("marca", "Honda", "Ford"), ("modelo", "Titan", "Focus")]))
        e._apply_candidate(ctx, data)
        self.assertEqual(e.created, [], "a correction must patch one row, never insert one")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, 1)
        self.assertEqual(rows[0].status, "current_focus", "focus must not move")
        self.assertEqual(st.current_focus_candidate_id, 1)

    def test_r2_53_without_the_marker_the_writer_still_redirects(self):
        """The guard must be narrow: ordinary AI updates keep the type-safety redirect."""
        e, rows = engine()
        ctx = ctx_for(rows, state())
        e._apply_candidate(ctx, {"action": "update", "id": 1, "marca": "Ford",
                                 "modelo": "Focus", "tipo_vehiculo": "AUTO"})
        self.assertEqual(len(e.created), 1,
                         "a non-correction update keeps the type-safety create redirect")
        self.assertEqual((rows[-1].marca, rows[-1].tipo_vehiculo), ("Ford", "AUTO"))

    def test_r2_54_an_unknown_category_fails_closed(self):
        """Requirement 27: an unknown category is not AUTO."""
        e, rows = engine(catalog={})
        out = e._correction_category(
            ctx_for(rows, state()),
            {"action": "update", "id": 1, "marca": "Lada", "modelo": "Niva"},
            resolution([("marca", "Honda", "Lada"), ("modelo", "Titan", "Niva")]))
        self.assertIsNone(out, "an unplaceable vehicle must not be written as AUTO")

    def test_r2_55_a_corrected_moto_is_flagged_for_human_routing(self):
        """Requirement 26."""
        e, rows = engine(catalog={("Honda", "Titan"): "MOTO"},
                         rows=[row(marca="Ford", modelo="Focus", tipo="AUTO")])
        data = e._correction_category(
            ctx_for(rows, state()),
            {"action": "update", "id": 1, "marca": "Honda", "modelo": "Titan"},
            resolution([("marca", "Ford", "Honda"), ("modelo", "Focus", "Titan")]))
        self.assertEqual(data["tipo_vehiculo"], "MOTO")
        self.assertTrue(e._turn_correction_moto)

    def test_r2_56_the_gate_routes_a_corrected_moto_to_a_human(self):
        start = ENGINE_SRC.index("Part G.6): a corrected vehicle")
        gate = ENGINE_SRC[start:start + 900]
        self.assertIn("_turn_correction_moto", gate)
        self.assertIn("_motorcycle_human_handoff", gate)

    def test_r2_57_a_year_only_correction_resolves_no_category(self):
        """The category travels only with an identity change."""
        e, rows = engine()
        data = {"action": "update", "id": 1, "anio": 2018}
        out = e._correction_category(ctx_for(rows, state()), data,
                                     resolution([("anio", 2019, 2018)]))
        self.assertNotIn("tipo_vehiculo", out)
        self.assertNotIn("correction_in_place", out)

    def test_r2_58_the_category_is_not_echoed_as_a_customer_statement(self):
        """Part G: it is derived, so it must not enter `corrected_fields`."""
        e, rows = engine()
        res = resolution([("marca", "Honda", "Ford"), ("modelo", "Titan", "Focus")])
        before = tuple(res.corrected_fields)
        e._correction_category(
            ctx_for(rows, state()),
            {"action": "update", "id": 1, "marca": "Ford", "modelo": "Focus"}, res)
        self.assertEqual(tuple(res.corrected_fields), before)
        self.assertNotIn("tipo_vehiculo", [f for f, _b, _a in res.corrected_fields])

    def test_r2_59_quote_validity_moves_with_the_category(self):
        """Requirement 30. The existing re-quote guard keys on the focus candidate's tipo,
        so a corrected category is what makes a delivered quote stale."""
        from app.services.acceptance_authorizer import CommercialState
        src = inspect.getsource(CommercialState.quote_inputs_unchanged)
        self.assertIn("tipo_vehiculo", src)
        self.assertIn("focus_after.tipo_vehiculo != _focus_before_tipo", ENGINE_SRC)
        self.assertIn("_record_quote_consequence(invalidated=True, recomputed=True)",
                      ENGINE_SRC)

    def test_r2_60_catalog_enforcement_is_skipped_only_on_a_correction_turn(self):
        """Requirement 28 / Part G.5, at the boundary the milestone authorizes."""
        self.assertIn("if pre_detected_vehicle and not _correction_wrote_identity:",
                      ENGINE_SRC)
        self.assertIn("correction_in_place", ENGINE_SRC)
        self.assertEqual(
            ENGINE_SRC.count("self._enforce_catalog_vehicle(ctx, pre_detected_vehicle)"), 1,
            "exactly one enforcement call site")


# ══ H — atomicity, idempotency, accounting ════════════════════════════════════════

class H_AtomicityAndAccounting(unittest.TestCase):

    def test_r2_61_multi_field_corrections_stay_atomic(self):
        """Requirement 31."""
        tier, price, identity, invalidating = pc.classify_patch(
            [("anio", 2019, 2018), ("zone_detail", "Palermo", "Pilar")])
        self.assertEqual(tier, pc.CorrectionTier.PROVISIONAL)
        self.assertTrue(invalidating)
        self.assertEqual(pc.classify_patch([("anio", 2019, 2018)])[0],
                         pc.CorrectionTier.IMMEDIATE)

    def test_r2_62_confirming_is_idempotent(self):
        """Requirement 32."""
        p = pending().with_confirmation_message(11)
        self.assertTrue(admit(p)[0])
        applied = p.with_status(pc.PendingStatus.CONFIRMED)
        ok, why = admit(applied)
        self.assertFalse(ok)
        self.assertIsNone(why, "a settled patch is not an invalidation, it is done")

    def test_r2_63_a_newer_correction_is_not_confirmed_in_the_same_turn(self):
        """Requirement 33."""
        ok, why = admit(pending().with_confirmation_message(11),
                        newer_correction_this_turn=True)
        self.assertFalse(ok)
        self.assertEqual(why, pc.InvalidationReason.NEWER_CORRECTION)

    def test_r2_64_the_accounting_instrument_counts_subfail(self):
        """Requirement 34/35: the method that missed the G3-7F regression must now catch it."""
        clean = ("=== short test summary info ===\n"
                 "FAILED ../tests/a.py::T::test_x - boom\n"
                 "ERROR ../tests/b.py::T::test_y\n"
                 "1 failed, 10 passed, 1 errors, 5 subtests passed in 1.00s\n")
        acc = ra.parse(clean)
        self.assertTrue(acc.reconciles, acc.explain())
        self.assertEqual(acc.identities, 2)

        injected = ("=== short test summary info ===\n"
                    "FAILED ../tests/a.py::T::test_x - boom\n"
                    "SUBFAILED(case='patch only') ../tests/c.py::T::test_z\n"
                    "ERROR ../tests/b.py::T::test_y\n"
                    "2 failed, 10 passed, 1 errors, 5 subtests passed in 1.00s\n")
        acc2 = ra.parse(injected)
        self.assertEqual(len(acc2.subfailed), 1, "the SUBFAIL must be seen")
        self.assertTrue(acc2.reconciles, acc2.explain())
        delta = ra.compare(acc, acc2)
        self.assertEqual(len(delta["added_subfailed"]), 1)
        self.assertFalse(acc2.clean)

    def test_r2_65_an_unreconciled_run_is_not_certifiable(self):
        """The exact shape of the G3-7F closeout's mistake: 59 + 9 against 67 identities."""
        bad = ("=== short test summary info ===\n"
               + "".join(f"FAILED ../tests/a.py::T::test_{i}\n" for i in range(58))
               + "".join(f"ERROR ../tests/b.py::T::test_e{i}\n" for i in range(9))
               + "59 failed, 100 passed, 9 errors, 10 subtests passed in 1.00s\n")
        acc = ra.parse(bad)
        self.assertEqual(acc.claimed, 68)
        self.assertEqual(acc.identities, 67)
        self.assertFalse(acc.reconciles, "68 != 67 must refuse certification")

    def test_r2_66_identities_with_spaces_are_not_merged(self):
        """This repository has `tests/Whatsapp Test/`; a whitespace-delimited capture
        collapsed four failures into one and undercounted by three."""
        out = ("=== short test summary info ===\n"
               "FAILED ../tests/Whatsapp Test/Messages/test_c.py::T::test_a - x\n"
               "FAILED ../tests/Whatsapp Test/Messages/test_c.py::T::test_b - y\n"
               "2 failed, 0 passed in 1.00s\n")
        self.assertEqual(len(ra.parse(out).failed), 2)

    def test_r2_67_collection_errors_are_counted(self):
        out = ("ERROR collecting ../tests/broken.py\n"
               "=== short test summary info ===\n"
               "ERROR ../tests/broken.py\n"
               "1 errors in 1.00s\n")
        self.assertGreaterEqual(ra.parse(out).collection_errors, 1)


# ══ I — invariants that must survive ══════════════════════════════════════════════

class I_Invariants(unittest.TestCase):

    def test_r2_68_one_candidate_writer_remains(self):
        """Requirement 39."""
        import ast
        tree = ast.parse(ENGINE_SRC)
        defs = [n.name for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name.startswith("_apply_candidate")]
        self.assertEqual(defs, ["_apply_candidate"])
        ctors = sum(1 for n in ast.walk(tree) if isinstance(n, ast.Call)
                    and getattr(n.func, "id", getattr(n.func, "attr", None))
                    == "WhatsAppThreadCandidate")
        self.assertEqual(ctors, 4, "candidate construction sites changed; re-examine")

    def test_r2_69_the_pending_module_still_writes_nothing(self):
        src = pathlib.Path(inspect.getsourcefile(pc)).read_text(encoding="utf-8")
        for forbidden in ("db.add(", "db.commit(", "_apply_candidate(", "setattr("):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, src)

    def test_r2_70_no_phrase_or_affirmation_detector_entered_the_new_path(self):
        """Requirement 38 / Part C's last clause."""
        for fn in (E._arm_pending_correction, E._withdraw_dependent_proposals,
                   E._turn_clarification_decision, E._correction_category,
                   E._register_correction_propositions, E._generic_human_handoff,
                   E._execute_clarification, pc.proposal_disposition,
                   pc.route_clarification, pc.confirmation_admissible,
                   pc.confirmation_dispatch_proven):
            with self.subTest(fn=fn.__name__):
                src = _code_only(fn)
                for token in ("_FUZZY_ACCEPTANCE_RE", "re.search", "re.match", "re.compile",
                              '"si"', "'si'", '"dale"', '"correcto"', '"ok"'):
                    self.assertNotIn(token, src, f"{token} in {fn.__name__}")

    def test_r2_71_confirmation_admissible_has_no_commercial_gate_caller(self):
        """Requirement 37. G3-8 owns the gates; this milestone certifies the primitive."""
        import subprocess
        hits = subprocess.run(
            ["grep", "-rn", "confirmation_admissible", "backend/app/"],
            capture_output=True, text=True,
            cwd=str(pathlib.Path(__file__).resolve().parents[1])).stdout.splitlines()
        callers = [h for h in hits if "def confirmation_admissible" not in h
                   and "pending_correction.py" not in h]
        self.assertEqual(callers, [], f"unexpected caller: {callers}")

    def test_r2_72_blocking_is_still_honestly_unenforced(self):
        """Requirement 40: the trace must not claim a gate it does not have."""
        src = inspect.getsource(E._pending_correction_for_trace)
        self.assertIn('"enforced_at_gates"', src)
        self.assertIn("False", src)

    def test_r2_73_the_trace_carries_the_correction_attribution(self):
        """Requirement 40: response source and action source must be truthful."""
        from app.schemas.hybrid_trace import DecisionAuthority, RendererSource
        src = inspect.getsource(E._register_correction_propositions)
        self.assertIn("DecisionAuthority.RECONCILER", src)
        self.assertIn("RendererSource.DETERMINISTIC_TEMPLATE", src)
        self.assertIn(DecisionAuthority.RECONCILER, DecisionAuthority.ALL)
        self.assertIn(RendererSource.DETERMINISTIC_TEMPLATE, RendererSource.ALL)

    def test_r2_74_flag_off_leaves_the_correction_path_inert(self):
        """Requirement: flag-off compatibility. With grounding off nothing arms, nothing is
        withdrawn, and no correction proposition is produced."""
        e, rows = engine(grounding=False, provisional=False)
        st = state(flow_booking_token="tok")
        p, why = e._arm_pending_correction(ctx_for(rows, st), st,
                                          resolution([("marca", "Honda", "Ford")]))
        self.assertIsNone(p)
        self.assertEqual(st.flow_booking_token, "tok", "nothing withdrawn when flags are off")
        e._register_correction_propositions(ctx_for(rows, st), "hola")
        self.assertEqual(e._live_plan.propositions, [])

    def test_r2_75_no_second_migration_was_added(self):
        """Part D's stop condition: the budget had to fit in existing persisted state."""
        versions = pathlib.Path(
            pathlib.Path(__file__).resolve().parents[1] / "backend/migrations/versions")
        recent = sorted(p.name for p in versions.glob("2026100*.py"))
        # G3-7F sequencing adds exactly ONE additive migration, by design (§4).
        # SB1 (Smart Booking Track B) later added its own, separately reviewed migration.
        self.assertEqual(recent, ["20261005_pending_correction.py",
                                  "20261006_clarification_attempts.py",
                                  "20261007_sb1_booking_representation.py"],
                         f"an unexpected migration appeared: {recent}")


# ══ J — mutation controls: every reintroduced defect must be caught ═══════════════

class J_MutationControls(unittest.TestCase):
    """Each control states a mutation and proves THIS suite notices it.

    A control that cannot name the test which fails is not a control, so each one asserts
    against the same production predicate the mutation would break.
    """

    def test_r2_76_mutation_echo_before_the_writer(self):
        gate = ENGINE_SRC[ENGINE_SRC.index("G3-7F Commit 3 + G3-7F-R2"):]
        gate = gate[:gate.index("Catalog enforcement (post-AI)")]
        self.assertLess(gate.index("_outcome = self._apply_candidate("),
                        gate.index("self._turn_correction_echo = tuple("),
                        "r2_01 would catch a reordering; this is its guard")

    def test_r2_77_mutation_drop_the_composer_consumer(self):
        self.assertIn(("correction", "_register_correction_propositions", True),
                      tuple(E._RESPONSE_PRODUCERS))

    def test_r2_78_mutation_omit_one_legacy_proposal_field(self):
        """Dropping any single field must be visible, so the count is pinned too."""
        self.assertEqual(len(pc.LEGACY_PROPOSAL_FIELDS), 13)
        self.assertEqual(pc.LEGACY_PROPOSAL_FIELDS,
                         tuple(f for f, _fam in pc.LEGACY_PROPOSALS))

    def test_r2_79_mutation_restore_the_free_text_clarification_marker(self):
        """OWNER-RESOLUTION: reintroducing R2's budget marker must be caught.

        The marker was not merely unbounded — it gave `pending_correction` a second meaning.
        This control fails if any part of it returns, including a renamed counter.
        """
        pc_src = pathlib.Path(inspect.getsourcefile(pc)).read_text(encoding="utf-8")
        code = "\n".join(l for l in pc_src.splitlines()
                          if not l.lstrip().startswith("#"))
        for token in ("def clarification_marker", "def clarifications_used",
                      "def clarification_budget_remains", "CLARIFICATION_BUDGET ="):
            with self.subTest(token=token):
                self.assertNotIn(token, code)
        self.assertNotIn("_spend_correction_clarification", ENGINE_SRC)

    def test_r2_80_mutation_provisional_without_grounding(self):
        src = inspect.getsource(E._arm_pending_correction)
        self.assertIn("_correction_grounding_on()", src)
        self.assertIn("GROUNDING_NOT_ENABLED", src)

    def test_r2_81_mutation_accept_a_failed_outbound_row(self):
        self.assertNotIn("failed", pc.OUTBOUND_DISPATCH_ACCEPTED)
        self.assertNotIn("blocked", pc.OUTBOUND_DISPATCH_ACCEPTED)
        src = inspect.getsource(pc.confirmation_admissible)
        self.assertIn("confirmation_dispatch_proven", src)

    def test_r2_82_mutation_skip_category_re_derivation(self):
        gate = ENGINE_SRC[ENGINE_SRC.index("the category travels WITH the correction"):]
        gate = gate[:gate.index("_outcome = self._apply_candidate(")]
        self.assertIn("self._correction_category(", gate)
        # OWNER-RESOLUTION Parts B and C: the call is gated on the grounding flag, and the
        # failure routes to the GENERIC handoff — never the motorcycle one.
        self.assertIn("if self._correction_grounding_on():", gate)
        self.assertIn("self._generic_human_handoff(", gate)
        self.assertNotIn("_motorcycle_human_handoff", gate)

    def test_r2_83_mutation_allow_phantom_candidate_creation(self):
        src = inspect.getsource(E._apply_candidate)
        self.assertIn('not candidate_data.get("correction_in_place")', src)

    def test_r2_84_mutation_ignore_subfail_in_accounting(self):
        src = pathlib.Path(inspect.getsourcefile(ra)).read_text(encoding="utf-8")
        self.assertIn("SUBFAILED", src)
        self.assertIn("reconciles", src)
        out = ("=== short test summary info ===\n"
               "SUBFAILED(case='x') ../tests/a.py::T::t\n"
               "1 failed, 0 passed in 1.00s\n")
        self.assertEqual(len(ra.parse(out).subfailed), 1)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
