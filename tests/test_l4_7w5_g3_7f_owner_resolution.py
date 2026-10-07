"""L4.7W5-GATE3-G3-7F-OWNER-RESOLUTION — Flow-first clarification and MOTO isolation.

The owner's resolution after the G3-7F-R2 independent review. Three things changed shape:

  * R2's free-text clarification budget is gone. The project already owns two published,
    structured clarification Flows; those are the one clarification attempt, and a typed
    router decides which (if either) a turn needs — from canonical evidence, never phrases.
  * `_motorcycle_human_handoff` is reserved for proven motorcycles. R2 routed an
    unresolvable category, an exhausted clarification and a correction conflict through it,
    so a Chery Tiggo owner was told "las motos las revisamos de forma manual", the operator
    dashboard recorded `motorcycle_enquiry`, and a `tipo_vehiculo="MOTO"` candidate was
    inserted. A generic handoff now owns those.
  * Confirmation authority requires that the question was in the DELIVERED response. A
    degraded plan sends a human-follow-up line with a perfectly good WAMID; R2 bound the
    patch to it.

Classes map to the prompt's test groups; each test names the requirement it discharges.
"""
from __future__ import annotations

import inspect
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import app.services.conversation_engine as ce                              # noqa: E402
from app.services import pending_correction as pc                          # noqa: E402
from app.services.conversation_engine import ConversationEngine as E       # noqa: E402
from app.services.response_composition import (LiveResponsePlan,           # noqa: E402
                                               PropositionId)
import _r2_regression_accounting as ra                                     # noqa: E402

# ── G3-7F sequencing migration: the clarification authority moved ─────────────────
# `_route_clarification` + `_dispatch_clarification_route` have been replaced by ONE
# memoized decision (`_turn_clarification_decision`) placed after the turn's reconciled
# proposal exists, and ONE executor (`_execute_clarification`) that reserves a durable
# attempt before transport. These adapters keep every assertion below pointed at the same
# behavioural claim: the route and reason this turn chose, and what executing it does.
def _route(eng, ctx, state, resolution=None, disposition=None, **kw):
    """`(route, reason)` from the current authority. Resets the per-turn memo first.

    Current-turn relevance is stated explicitly. The retired router proved the domain from
    STORED state (`vehicle_domain_proven=focus is not None`); the replacement requires the
    turn itself to be about the family, because owner policy 2.8 forbids an old half-filled
    candidate turning a payment question into a vehicle form. Every scenario in this file
    describes a turn that IS about the vehicle and its locality, so that is what is passed.
    """
    import types as _t
    eng._turn_clarification_decision_obj = None
    kw.setdefault("ce_vehicle", _t.SimpleNamespace(marca="", modelo="", tipo_vehiculo=""))
    kw.setdefault("ce_location", True)
    d = eng._turn_clarification_decision(
        ctx, state, resolution=resolution, disposition=disposition, **kw)
    return d.route, d.reason


def _spend(eng, *families):
    """Mark a family's one attempt as already made — the ATTEMPT, not the flag.

    The retired design kept "already asked" in `*_fallback_flow_sent`, which completion,
    handoff and the withdrawal loop could all clear. Availability now lives in
    `whatsapp_clarification_attempts`, so a test that means "this family has had its turn"
    says so against the store.
    """
    from _clarification_store_double import install
    return install(eng, spent=list(families))


def _dispatch(eng, ctx, state, route, reason):
    """Execute a route through the single reserving authority."""
    from app.services.clarification_router import TurnClarificationDecision as _TCD
    fam = ("VEHICLE" if route == "VEHICLE_FLOW_REQUIRED"
           else "LOCATION" if route == "LOCATION_FLOW_REQUIRED" else None)
    return eng._execute_clarification(ctx, state, _TCD(route, reason, family=fam))


ENGINE_SRC = pathlib.Path(inspect.getsourcefile(E)).read_text(encoding="utf-8")
PLAN_SRC = pathlib.Path(
    inspect.getsourcefile(LiveResponsePlan)).read_text(encoding="utf-8")

def code_only(fn) -> str:
    """A function's source with docstrings removed.

    The new docstrings quote what R2 did wrong, which means they legitimately contain the
    word MOTO. A check about executable behaviour must read executable code.
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


Route = pc.ClarificationRoute
Why = pc.ClarificationReason


# ── harness ──────────────────────────────────────────────────────────────────────

def row(cid=1, marca="Ford", modelo="Focus", tipo="AUTO", status="current_focus", **over):
    base = dict(id=cid, marca=marca, modelo=modelo, anio=2019, tipo_vehiculo=tipo,
                status=status, zone_group="CABA", zone_detail="Palermo",
                version_text=None, direccion_texto=None, source_text=None)
    base.update(over)
    return SimpleNamespace(**base)


def state(**over):
    base = dict(pending_correction=None, current_focus_candidate_id=1, needs_human=False,
                home_zone_group="CABA", home_zone_detail="Palermo", last_stage=None)
    for f in pc.LEGACY_PROPOSAL_FIELDS:
        base.setdefault(f, None)
    base.update(over)
    return SimpleNamespace(**base)


def ctx_for(rows, st, lead=None):
    return SimpleNamespace(state=st, candidates=rows, thread=SimpleNamespace(id=1),
                           contact=SimpleNamespace(wa_id="x"),
                           lead=lead if lead is not None else SimpleNamespace(
                               necesita_humano=False, flag=None),
                           db_messages=[], inbound_wa_message_id="in1")


SENT: list = []
FLOWS: list = []


class _Recorder:
    """Stands in for transport. Records what would be dispatched; never calls Meta."""

    def text(self, engine, ctx, primary):
        plan = getattr(engine, "_live_plan", None)
        if plan is not None:
            plan.degraded_fallback = ce._DEGRADED_RESPONSE_FALLBACK
            engine._register_response_propositions(ctx, primary)
            primary = plan.compose(primary)
            if not primary:
                return None
        if not (primary or "").strip():
            return None
        SENT.append(primary)
        return f"wamid.T{len(SENT)}"

    def flow(self, engine, ctx, body, token, **kw):
        FLOWS.append({"body": body, "screen": kw.get("initial_screen"),
                      "flow_id": kw.get("flow_id")})
        return f"wamid.F{len(FLOWS)}"


def engine(*, grounding=True, provisional=True, rows=None, catalog=None, flow_ids=True,
           outbound=True, flow_fails=False, text_fails=False):
    e = E.__new__(E)
    e.settings = SimpleNamespace(
        correction_target_grounding_enabled=grounding,
        provisional_correction_enabled=provisional,
        whatsapp_vehicle_fallback_flow_id=("vflow" if flow_ids else ""),
        whatsapp_location_fallback_flow_id=("lflow" if flow_ids else ""),
        whatsapp_flow_id=("cflow" if flow_ids else ""),
        resend_api_key="", internal_booking_email_to="", internal_booking_email_from="")
    e._correlation_id = "turn-1"
    e._turn_correlation_id = lambda: "turn-1"
    e._canonical_turn_burst = SimpleNamespace(ordered_message_ids=("m1",))
    e._reconciler_cycle_id = lambda _s: "c1"
    e._live_plan = LiveResponsePlan(turn_id="turn-1")
    for name, value in (("_turn_correction_echo", ()),
                        ("_turn_correction_proposition", None),
                        ("_turn_correction_moto", False),
                        ("_turn_correction_category", None),
                        ("_turn_clarification_route", None),
                        ("_turn_clarification_reason", None),
                        ("_turn_clarification_flow", None),
                        ("_turn_clarification_intro", None),
                        ("_turn_handoff_reason", None),
                        ("_turn_generic_handoff", False),
                        ("_turn_confirmation_bound", None),
                        ("_turn_faq_burst", None), ("_turn_price_quote", None),
                        ("_answer_source", None)):
        setattr(e, name, value)
    e._decision_log = lambda *a, **k: None
    e._validate_outbound_text = lambda t, c, s: t
    table = catalog if catalog is not None else {("Ford", "Focus"): "AUTO",
                                                 ("Honda", "Titan"): "MOTO"}
    e._catalog_tipo_for = lambda a, b: table.get((a, b))
    rows = rows if rows is not None else [row()]
    e.created = []

    def _add(o):
        e.created.append(o)
        rows.append(o)

    e.db = SimpleNamespace(add=_add, flush=lambda: None, commit=lambda: None,
                           query=lambda *a: SimpleNamespace(
                               filter=lambda *b: SimpleNamespace(one_or_none=lambda: None)))
    rec = _Recorder()
    e._outbound_armed = staticmethod(lambda: outbound)
    if text_fails:
        e._send_text_to_wa = lambda ctx, t: None
    else:
        e._send_text_to_wa = lambda ctx, t: rec.text(e, ctx, t)
    if flow_fails:
        def _boom(ctx, body, token, **kw):
            raise ce.OutboundBlockedError(
                sender_path="t", kind="flow", to_wa_id="x", thread_id=1,
                gate_outcome="BLOCKED_TEST")
        e._send_flow_button = _boom
    else:
        e._send_flow_button = lambda ctx, body, token, **kw: rec.flow(
            e, ctx, body, token, **kw)
    e._send_fallback_human_review_notification = lambda *a, **k: ALERTS.append(
        k.get("reason"))
    e._get_active_inspection_location = lambda c, s: (
        getattr(s, "home_zone_group", None), getattr(s, "home_zone_detail", None))
    e._focus_candidate = lambda c, **kw: next(
        (r for r in rows if getattr(r, "status", None) == "current_focus"), None)
    e._pending_correction = lambda c: pc.load(getattr(c, "state", None))
    e._store_pending_correction = lambda st, p: setattr(
        st, "pending_correction", p.to_payload() if p is not None else None)
    SENT.clear()
    FLOWS.clear()
    ALERTS.clear()
    # G3-7F sequencing (§6): an EXPLICIT attempt store, never a mock that happens to
    # answer. Clarification now depends on durable attempt history, and a harness that let
    # a MagicMock stand in for it would exercise the fail-closed unavailable path on every
    # unresolved turn — a property of the harness, not of the engine under test.
    from _clarification_store_double import install as _install_store
    _install_store(e)
    return e, rows


ALERTS: list = []


def resolution(fields, *, basis="G2_SINGLETON", cid=1):
    return SimpleNamespace(
        target_candidate_id=cid, grounding_basis=basis,
        target_relation="CORRECT_EXISTING", target_scope_value="CURRENT_REFERENT",
        corrected_fields=tuple(fields), correction_evidence_ref="corrections[0]",
        vehicle_evidence_ref=None, location_evidence_ref=None,
        action=None)


def pending(fields=(("anio", 2019, 2018),), *, cycle="c1", basis="G2_SINGLETON"):
    return pc.PendingCorrection.build(
        thread_id=1, turn_id="t1", target_candidate_id=1, grounding_basis=basis,
        relation="CORRECT_EXISTING", target_scope="CURRENT_REFERENT",
        corrected_fields=list(fields), cycle_id=cycle)


# ══ A — semantic-first: a resolved turn asks nothing (requirements 1-5) ═══════════

class A_SemanticFirst(unittest.TestCase):

    def test_or_01_a_resolved_noisy_turn_sends_no_flow(self):
        """Requirement 1. `qquieor reviisar un autoo 208 del 2022 en ppalermo` resolves to
        Peugeot 208 / 2022 / Palermo through semantic evidence; the router sees only the
        resolved facts and must ask for nothing."""
        self.assertEqual(
            pc.route_clarification(vehicle_domain_proven=True,
                                   vehicle_identity_resolved=True,
                                   vehicle_category_resolved=True,
                                   location_domain_proven=True, location_resolved=True),
            (Route.NO_CLARIFICATION_REQUIRED, Why.RESOLVED))

    def test_or_02_semantic_only_vehicle_identity_avoids_the_vehicle_flow(self):
        """Requirement 2 and owner policy 2: a deterministic parser's silence is not a veto.
        The router is given resolution, not provenance, so it cannot prefer CE."""
        self.assertEqual(
            pc.route_clarification(vehicle_domain_proven=True,
                                   vehicle_identity_resolved=True)[0],
            Route.NO_CLARIFICATION_REQUIRED)
        for arg in inspect.signature(pc.route_clarification).parameters:
            with self.subTest(arg=arg):
                self.assertNotIn("ce_", arg)
                self.assertNotIn("semantic", arg)

    def test_or_03_semantic_only_locality_avoids_the_location_flow(self):
        """Requirement 3."""
        self.assertEqual(
            pc.route_clarification(location_domain_proven=True, location_resolved=True)[0],
            Route.NO_CLARIFICATION_REQUIRED)

    def test_or_04_the_router_reads_no_customer_text(self):
        """Requirement 4 / Part D: canonical evidence only, no phrase matching."""
        src = inspect.getsource(pc.route_clarification)
        for token in ("re.", "combined", "_wants_price_quote", "_is_general_faq",
                      "text", "lower()"):
            with self.subTest(token=token):
                self.assertNotIn(token, src)
        params = inspect.signature(pc.route_clarification).parameters
        for name, param in params.items():
            with self.subTest(param=name):
                self.assertIn(param.annotation, (bool, "bool"),
                              "every router input is a boolean fact, never text")

    def test_or_05_an_unproven_domain_invents_no_flow(self):
        """Requirement 5 / Part C: do not infer a domain merely to send a Flow."""
        self.assertEqual(pc.route_clarification(),
                         (Route.NO_CLARIFICATION_REQUIRED, Why.DOMAIN_NOT_PROVEN))
        self.assertEqual(
            pc.route_clarification(vehicle_identity_resolved=False,
                                   location_resolved=False),
            (Route.NO_CLARIFICATION_REQUIRED, Why.DOMAIN_NOT_PROVEN),
            "unresolved values without a proven domain must not route to a Flow")

    def test_or_06_defaults_are_the_safe_reading(self):
        """A caller that omits an argument must not get an unasked-for Flow."""
        sig = inspect.signature(pc.route_clarification).parameters
        for name in ("vehicle_identity_resolved", "vehicle_category_resolved",
                     "location_resolved"):
            with self.subTest(arg=name):
                self.assertIs(sig[name].default, True)
        for name in ("vehicle_domain_proven", "location_domain_proven",
                     "human_owns_turn", "other_confirmable_proposal"):
            with self.subTest(arg=name):
                self.assertIs(sig[name].default, False)


# ══ B — the Vehicle Clarification Flow (requirements 6-15) ════════════════════════

class B_VehicleFlow(unittest.TestCase):

    def test_or_07_proven_domain_plus_unresolved_identity_routes_to_the_vehicle_flow(self):
        """Requirement 6."""
        self.assertEqual(
            pc.route_clarification(vehicle_domain_proven=True,
                                   vehicle_identity_resolved=False),
            (Route.VEHICLE_FLOW_REQUIRED, Why.VEHICLE_IDENTITY_UNRESOLVED))

    def test_or_08_one_explanatory_message_accompanies_it(self):
        """Requirements 7 and owner policy 12/13: one short sentence, carried as the Flow
        body so the text and the Flow are one response with no duplicate."""
        e, rows = engine(rows=[row(marca="", modelo="")])
        st = state()
        out = e._dispatch_vehicle_clarification_flow(ctx_for(rows, st), st)
        self.assertIsNotNone(out)
        self.assertEqual(len(FLOWS), 1, "exactly one Flow")
        self.assertEqual(len(SENT), 0, "no separate text message")
        self.assertEqual(FLOWS[0]["body"], ce._VEHICLE_CLARIFICATION_INTRO)
        self.assertEqual(FLOWS[0]["screen"], "VEHICLE_DETAILS")

    def test_or_09_the_intro_is_a_module_constant_not_reconciler_prose(self):
        """Part E: no customer-facing Flow prose in the reconciler, and G3-5F's own
        invariant keeps it out of the response plan too."""
        for fn in (E._turn_clarification_decision, E._execute_clarification,
                   E._arm_pending_correction):
            with self.subTest(fn=fn.__name__):
                src = inspect.getsource(fn)
                self.assertNotIn("formulario", src)
                self.assertNotIn("Para asegurarme", src)
        self.assertNotIn("formulario", PLAN_SRC)
        self.assertIn("formulario", ce._VEHICLE_CLARIFICATION_INTRO)

    def test_or_10_the_flow_is_sent_once_per_cycle(self):
        """Requirement 8: the second unresolved turn does not resend it."""
        self.assertEqual(
            pc.route_clarification(vehicle_domain_proven=True,
                                   vehicle_identity_resolved=False,
                                   vehicle_flow_attempted=True),
            (Route.GENERIC_HUMAN_REQUIRED, Why.VEHICLE_FLOW_ALREADY_ATTEMPTED))

    def test_or_11_duplicate_inbound_does_not_resend_it(self):
        """Requirement 9. The flag is canonical state, so a replayed burst sees it set."""
        e, rows = engine(rows=[row(marca="", modelo="")])
        st = state()
        ctx = ctx_for(rows, st)
        # MIGRATED (G3-7F sequencing): availability lives in the ATTEMPT row now, not
        # in `*_fallback_flow_sent`. The flag means only "a proposal is outstanding"
        # and clearing it can never make the attempt available again.
        _spend(e, "VEHICLE")
        e._dispatch_vehicle_clarification_flow(ctx, st)
        self.assertTrue(st.vehicle_fallback_flow_sent)
        st.vehicle_fallback_flow_sent = False      # the flag may be cleared freely
        route, why = _route(e, ctx, st)
        self.assertEqual((route, why),
                         (Route.GENERIC_HUMAN_REQUIRED,
                          "VEHICLE_ATTEMPT_SPENT"))

    def test_or_12_a_process_restart_does_not_resend_it(self):
        """Requirement 10: the flag is persisted, not in-memory."""
        from app.models import WhatsAppThreadState
        self.assertIn("vehicle_fallback_flow_sent", WhatsAppThreadState.__table__.c)
        self.assertIn("location_fallback_flow_sent", WhatsAppThreadState.__table__.c)
        e2, rows2 = engine(rows=[row(marca="", modelo="")])
        # MIGRATED (G3-7F sequencing): availability lives in the ATTEMPT row now, not
        # in `*_fallback_flow_sent`. The flag means only "a proposal is outstanding"
        # and clearing it can never make the attempt available again.
        _spend(e2, "VEHICLE")
        st = state(vehicle_fallback_flow_sent=False)      # as loaded after a restart
        self.assertEqual(_route(e2, ctx_for(rows2, st), st)[0],
                         Route.GENERIC_HUMAN_REQUIRED)

    def test_or_13_the_next_unresolved_turn_routes_to_a_human(self):
        """Requirement 11 / owner policy 10."""
        e, rows = engine(rows=[row(marca="", modelo="")])
        # MIGRATED (G3-7F sequencing): availability lives in the ATTEMPT row now, not
        # in `*_fallback_flow_sent`. The flag means only "a proposal is outstanding"
        # and clearing it can never make the attempt available again.
        _spend(e, "VEHICLE")
        st = state(vehicle_fallback_flow_sent=False)
        ctx = ctx_for(rows, st)
        route, why = _route(e, ctx, st)
        out = _dispatch(e, ctx, st, route, why)
        self.assertTrue(st.needs_human)
        self.assertEqual(e._turn_handoff_reason, "VEHICLE_ATTEMPT_SPENT")
        self.assertEqual(len(FLOWS), 0, "no second Flow")
        self.assertIsNotNone(out)

    def test_or_14_completed_vehicle_evidence_prevents_another_flow(self):
        """Requirements 12 and 13: completion resumes from canonical evidence."""
        src = inspect.getsource(E._process_vehicle_fallback_response)
        self.assertIn("state.vehicle_fallback_flow_sent = False", src,
                      "completion consumes the attempt")
        self.assertEqual(
            pc.route_clarification(vehicle_domain_proven=True,
                                   vehicle_identity_resolved=True)[0],
            Route.NO_CLARIFICATION_REQUIRED)

    def test_or_15_known_identity_unknown_category_uses_a_human_not_the_flow(self):
        """Requirement 14 / owner policy 18: never re-ask an identity we already hold."""
        self.assertEqual(
            pc.route_clarification(vehicle_domain_proven=True,
                                   vehicle_identity_resolved=True,
                                   vehicle_category_resolved=False),
            (Route.GENERIC_HUMAN_REQUIRED, Why.VEHICLE_CATEGORY_UNRESOLVED))

    def test_or_16_the_vehicle_flow_creates_no_moto_candidate(self):
        """Requirement 15."""
        e, rows = engine(rows=[row(marca="", modelo="")])
        st = state()
        e._dispatch_vehicle_clarification_flow(ctx_for(rows, st), st)
        self.assertEqual(e.created, [])
        self.assertEqual(len(rows), 1)


# ══ C — the Location Clarification Flow (requirements 16-25) ══════════════════════

class C_LocationFlow(unittest.TestCase):

    def test_or_17_proven_domain_plus_unresolved_locality_routes_to_the_location_flow(self):
        """Requirement 16."""
        self.assertEqual(
            pc.route_clarification(location_domain_proven=True, location_resolved=False),
            (Route.LOCATION_FLOW_REQUIRED, Why.LOCATION_UNRESOLVED))

    def test_or_18_one_explanatory_message_accompanies_it(self):
        """Requirement 17."""
        e, rows = engine()
        st = state(home_zone_group=None, home_zone_detail=None)
        out = e._dispatch_location_clarification_flow(ctx_for(rows, st), st)
        self.assertIsNotNone(out)
        self.assertEqual(len(FLOWS), 1)
        self.assertEqual(len(SENT), 0)
        self.assertEqual(FLOWS[0]["body"], ce._LOCATION_CLARIFICATION_INTRO)
        self.assertEqual(FLOWS[0]["screen"], "LOCATION_DETAILS")

    def test_or_19_the_location_flow_is_sent_once(self):
        """Requirements 18, 19, 20."""
        self.assertEqual(
            pc.route_clarification(location_domain_proven=True, location_resolved=False,
                                   location_flow_attempted=True),
            (Route.GENERIC_HUMAN_REQUIRED, Why.LOCATION_FLOW_ALREADY_ATTEMPTED))
        e, rows = engine()
        st = state(home_zone_group=None, home_zone_detail=None)
        e._dispatch_location_clarification_flow(ctx_for(rows, st), st)
        self.assertTrue(st.location_fallback_flow_sent)

    def test_or_20_the_next_unresolved_turn_routes_to_a_human(self):
        """Requirement 21."""
        # The focused candidate carries no zone either — the default fixture has Palermo,
        # which would make the location RESOLVED and the question moot.
        e, rows = engine(rows=[row(zone_group=None, zone_detail=None)])
        # MIGRATED (G3-7F sequencing): the LOCATION attempt is spent — recorded durably —
        # and the outstanding flag is cleared, proving that clearing it cannot make the
        # family available again. The vehicle must be resolved or it would outrank location.
        _spend(e, "LOCATION")
        st = state(home_zone_group=None, home_zone_detail=None,
                   location_fallback_flow_sent=False)
        ctx = ctx_for(rows, st)
        route, why = _route(e, ctx, st,
                            ce_vehicle=SimpleNamespace(marca="Ford", modelo="Ka",
                                                       tipo_vehiculo="AUTO"))
        self.assertEqual((route, why), (Route.GENERIC_HUMAN_REQUIRED,
                                        "LOCATION_ATTEMPT_SPENT"))
        _dispatch(e, ctx, st, route, why)
        self.assertEqual(len(FLOWS), 0)
        self.assertTrue(st.needs_human)

    def test_or_21_completion_resumes_canonical_pricing(self):
        """Requirement 22."""
        src = inspect.getsource(E._process_location_fallback_response)
        self.assertIn("state.location_fallback_flow_sent = False", src)

    def test_or_22_a_resolved_locality_prevents_an_unnecessary_flow(self):
        """Requirement 23 / Part D: do not send the Flow merely because a text lookup
        failed when canonical coverage already resolves a safe CABA/GBA location."""
        e, rows = engine()
        st = state(home_zone_group="CABA", home_zone_detail="Palermo")
        self.assertEqual(_route(e, ctx_for(rows, st), st)[0],
                         Route.NO_CLARIFICATION_REQUIRED)

    def test_or_23_an_unavailable_location_flow_fails_safely(self):
        """Requirement 24 / owner policy 11: a Flow that cannot be dispatched hands off."""
        e, rows = engine(flow_ids=False)
        st = state(home_zone_group=None, home_zone_detail=None)
        ctx = ctx_for(rows, st)
        out = _dispatch(e, 
            ctx, st, Route.LOCATION_FLOW_REQUIRED, Why.LOCATION_UNRESOLVED)
        self.assertEqual(len(FLOWS), 0)
        self.assertTrue(st.needs_human)
        self.assertEqual(e._turn_handoff_reason,
                         ce._HANDOFF_LOCATION_FLOW_UNAVAILABLE)
        self.assertFalse(st.location_fallback_flow_sent,
                         "an undispatched Flow must not consume the attempt")
        self.assertIsNotNone(out)

    def test_or_24_the_location_flow_mutates_no_vehicle_or_category(self):
        """Requirement 25."""
        e, rows = engine()
        st = state(home_zone_group=None, home_zone_detail=None)
        before = (rows[0].marca, rows[0].modelo, rows[0].tipo_vehiculo)
        e._dispatch_location_clarification_flow(ctx_for(rows, st), st)
        self.assertEqual((rows[0].marca, rows[0].modelo, rows[0].tipo_vehiculo), before)
        self.assertEqual(e.created, [])


# ══ D — ordering (requirements 26-30) ═════════════════════════════════════════════

class D_Ordering(unittest.TestCase):

    def test_or_25_both_unresolved_offers_the_vehicle_flow_only(self):
        """Requirements 26 and 28 / owner policies 7 and 8."""
        route, why = pc.route_clarification(
            vehicle_domain_proven=True, vehicle_identity_resolved=False,
            location_domain_proven=True, location_resolved=False)
        self.assertEqual((route, why), (Route.VEHICLE_FLOW_REQUIRED,
                                        Why.VEHICLE_IDENTITY_UNRESOLVED))

    def test_or_26_after_vehicle_completion_the_location_flow_may_follow(self):
        """Requirement 27: re-run from canonical evidence, then offer Location."""
        route, why = pc.route_clarification(
            vehicle_domain_proven=True, vehicle_identity_resolved=True,
            vehicle_category_resolved=True,
            location_domain_proven=True, location_resolved=False)
        self.assertEqual((route, why), (Route.LOCATION_FLOW_REQUIRED,
                                        Why.LOCATION_UNRESOLVED))

    def test_or_27_the_two_flows_are_never_dispatched_together(self):
        """Requirement 28, structurally: one route is returned, and the executor dispatches
        exactly the one it names."""
        self.assertEqual(len(Route.ALL), 5)
        src = inspect.getsource(E._execute_clarification)
        self.assertEqual(src.count("_dispatch_vehicle_clarification_flow"), 1)
        self.assertEqual(src.count("_dispatch_location_clarification_flow"), 1)
        e, rows = engine(rows=[row(marca="", modelo="")])
        st = state(home_zone_group=None, home_zone_detail=None)
        ctx = ctx_for(rows, st)
        route, why = _route(e, ctx, st)
        _dispatch(e, ctx, st, route, why)
        self.assertEqual(len(FLOWS), 1, "one Flow in a both-unresolved turn")
        self.assertEqual(FLOWS[0]["screen"], "VEHICLE_DETAILS")

    def test_or_28_a_flow_never_coexists_with_another_confirmable_proposal(self):
        """Requirement 29 / owner policy 8."""
        for kw in (dict(vehicle_domain_proven=True, vehicle_identity_resolved=False),
                   dict(location_domain_proven=True, location_resolved=False)):
            with self.subTest(**kw):
                self.assertEqual(
                    pc.route_clarification(other_confirmable_proposal=True, **kw),
                    (Route.GENERIC_HUMAN_REQUIRED, Why.PROPOSAL_WOULD_COEXIST))

    def test_or_29_a_human_owned_thread_sends_no_flow(self):
        """Requirement 30."""
        self.assertEqual(
            pc.route_clarification(human_owns_turn=True, vehicle_domain_proven=True,
                                   vehicle_identity_resolved=False),
            (Route.NO_CLARIFICATION_REQUIRED, Why.HUMAN_OWNS_TURN))
        e, rows = engine(rows=[row(marca="", modelo="")])
        st = state(needs_human=True)
        self.assertEqual(_route(e, ctx_for(rows, st), st)[0],
                         Route.NO_CLARIFICATION_REQUIRED)


# ══ E — confirmation versus clarification (requirements 31-35) ════════════════════

class E_ConfirmationVersusClarification(unittest.TestCase):

    def test_or_30_a_known_tier_2_correction_uses_the_typed_confirmation(self):
        """Requirement 31 / owner policy 17."""
        self.assertEqual(
            pc.route_clarification(correction_target_known=True,
                                   correction_values_known=True,
                                   vehicle_domain_proven=True,
                                   vehicle_identity_resolved=False),
            (Route.CORRECTION_CONFIRMATION_REQUIRED, Why.CORRECTION_VALUES_KNOWN))

    def test_or_31_unknown_identity_uses_the_flow_not_a_confirmation(self):
        """Requirement 32: no corrected values means nothing to confirm."""
        self.assertEqual(
            pc.route_clarification(correction_target_known=True,
                                   correction_values_known=False,
                                   vehicle_domain_proven=True,
                                   vehicle_identity_resolved=False)[0],
            Route.VEHICLE_FLOW_REQUIRED)

    def test_or_32_unknown_locality_uses_the_flow_not_a_confirmation(self):
        """Requirement 33."""
        self.assertEqual(
            pc.route_clarification(correction_target_known=True,
                                   correction_values_known=False,
                                   location_domain_proven=True,
                                   location_resolved=False)[0],
            Route.LOCATION_FLOW_REQUIRED)

    def test_or_33_a_degraded_response_creates_no_confirmation_authority(self):
        """Requirement 34 and Part A — review finding R2R-01, end to end.

        A REQUIRED producer fails, the plan degrades, the bounded human-follow-up line is
        dispatched with a real WAMID, and no patch may survive it.
        """
        e, rows = engine()
        st = state()
        ctx = ctx_for(rows, st)
        # zone_group alone has no renderable fragment, so the correction producer raises
        p = pending(fields=[("zone_group", "CABA", "Norte")])
        out = e._request_correction_confirmation(ctx, st, p)
        self.assertTrue(e._live_plan.degraded, "the plan degraded")
        self.assertEqual(SENT[-1], ce._DEGRADED_RESPONSE_FALLBACK)
        self.assertIsNone(pc.load(st), "no pending correction may survive")
        self.assertEqual(e._turn_confirmation_bound, "DEGRADED")
        self.assertTrue(st.needs_human, "a person owns the turn")
        self.assertEqual(e._turn_handoff_reason,
                         ce._HANDOFF_RESPONSE_COMPOSITION_FAILED)
        self.assertNotIn("moto", SENT[-1].lower())
        self.assertEqual(e.created, [], "no candidate, least of all a MOTO one")

    def test_or_34_a_later_accept_after_a_degraded_response_is_inadmissible(self):
        """Requirement 35. Even if a caller reconstructed the patch, admission refuses."""
        p = pending()
        ok, why = pc.confirmation_admissible(
            p, state=state(), candidates=[row()], cycle_id="c1",
            latest_outbound_message_id=None, newer_correction_this_turn=False,
            confirmation_row=None, thread_id=1, grounding_enabled=True)
        self.assertFalse(ok)
        self.assertEqual(why, pc.InvalidationReason.NOT_LATEST_QUESTION)

    def test_or_35_inclusion_not_registration_is_what_binds(self):
        """Part A: the proof is structural and never reads rendered prose."""
        src = inspect.getsource(E._request_correction_confirmation)
        self.assertIn("plan.included(", src)
        self.assertIn("degraded", src)
        self.assertIn("confirmation_dispatch_proven", src)
        for prose_probe in ("in text", "startswith", "find(", ".lower()"):
            with self.subTest(probe=prose_probe):
                self.assertNotIn(prose_probe, src)

    def test_or_36_included_is_false_before_composition_and_when_degraded(self):
        plan = LiveResponsePlan(turn_id="t")
        self.assertFalse(plan.included(PropositionId.CORRECTION_CONFIRMATION_REQUIRED))
        plan.composed = True
        plan.degraded = True
        self.assertFalse(plan.included(PropositionId.CORRECTION_CONFIRMATION_REQUIRED))

    def test_or_37_a_clean_confirmation_still_binds(self):
        """The fix must not make the ordinary path impossible."""
        e, rows = engine()
        st = state()
        ctx = ctx_for(rows, st)
        p = pending()
        row_obj = SimpleNamespace(id=7, status="sent", wa_message_id="wamid.T1",
                                  thread_id=1, direction="out")
        e.db = SimpleNamespace(
            commit=lambda: None, add=lambda o: None, flush=lambda: None,
            query=lambda *a: SimpleNamespace(
                filter=lambda *b: SimpleNamespace(one_or_none=lambda: row_obj)))
        out = e._request_correction_confirmation(ctx, st, p)
        self.assertFalse(e._live_plan.degraded)
        self.assertTrue(e._live_plan.included(
            PropositionId.CORRECTION_CONFIRMATION_REQUIRED))
        self.assertEqual(e._turn_confirmation_bound, "BOUND")
        stored = pc.load(st)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.confirmation_message_id, 7)


# ══ F — no false MOTO (requirements 36-44) ════════════════════════════════════════

class F_NoFalseMoto(unittest.TestCase):

    UNKNOWN = (("Chery", "Tiggo"), ("Baic", "X35"), ("Jetour", "X70"), ("Lada", "Niva"))

    def test_or_38_unknown_market_vehicles_never_become_moto(self):
        """Requirements 36-39. Each of these resolves to None in the 311-entry catalogue."""
        e = E.__new__(E)
        for marca, modelo in self.UNKNOWN:
            with self.subTest(vehicle=f"{marca} {modelo}"):
                self.assertIsNone(E._catalog_tipo_for(e, marca, modelo),
                                  "catalogue coverage changed; re-examine this test")
                self.assertEqual(
                    pc.route_clarification(vehicle_domain_proven=True,
                                           vehicle_identity_resolved=True,
                                           vehicle_category_resolved=False),
                    (Route.GENERIC_HUMAN_REQUIRED, Why.VEHICLE_CATEGORY_UNRESOLVED))

    def test_or_39_an_unresolvable_category_uses_the_generic_handoff(self):
        """Requirement 40, through the real gate helper."""
        e, rows = engine(catalog={})
        st = state()
        ctx = ctx_for(rows, st)
        out = e._correction_category(
            ctx, {"action": "update", "id": 1, "marca": "Chery", "modelo": "Tiggo"},
            resolution([("marca", "Ford", "Chery"), ("modelo", "Focus", "Tiggo")]))
        self.assertIsNone(out, "an unplaceable vehicle fails closed")
        res = e._generic_human_handoff(ctx, st,
                                       reason=ce._HANDOFF_CATEGORY_UNRESOLVED)
        self.assertTrue(st.needs_human)
        self.assertEqual(ALERTS, [ce._HANDOFF_CATEGORY_UNRESOLVED])
        self.assertEqual(SENT[-1], ce._GENERIC_HANDOFF_REPLY)
        self.assertNotIn("moto", SENT[-1].lower())
        self.assertIsNotNone(res)

    def test_or_40_an_ordinary_correction_conflict_uses_the_generic_handoff(self):
        """Requirement 41 — R2 sent this through the motorcycle helper."""
        e, rows = engine()
        st = state(flow_booking_token="tok-abandoned", vehicle_fallback_flow_sent=True)
        ctx = ctx_for(rows, st)
        route, why = _route(e, 
            ctx, st, resolution([("anio", 2019, 2018)]),
            {"conflicting": ("flow_booking_token",)})
        _dispatch(e, ctx, st, route, why)
        self.assertTrue(st.needs_human)
        self.assertNotIn("moto", (SENT[-1] if SENT else "").lower())
        self.assertEqual(e.created, [])

    def test_or_41_the_generic_handoff_creates_no_candidate(self):
        """Requirement 42."""
        e, rows = engine()
        st = state()
        e._generic_human_handoff(ctx_for(rows, st), st, reason="x")
        self.assertEqual(e.created, [])
        self.assertEqual(len(rows), 1)
        src = code_only(E._generic_human_handoff)
        self.assertNotIn("WhatsAppThreadCandidate", src)
        self.assertNotIn("MOTO", src)

    def test_or_42_a_genuine_moto_still_uses_motorcycle_handling(self):
        """Requirement 43: the resolution isolates the helper, it does not disable it."""
        e, rows = engine(rows=[row(marca="Ford", modelo="Focus", tipo="AUTO")])
        st = state()
        out = e._correction_category(
            ctx_for(rows, st),
            {"action": "update", "id": 1, "marca": "Honda", "modelo": "Titan"},
            resolution([("marca", "Ford", "Honda"), ("modelo", "Focus", "Titan")]))
        self.assertEqual(out["tipo_vehiculo"], "MOTO")
        self.assertTrue(e._turn_correction_moto)
        gate = ENGINE_SRC[ENGINE_SRC.index("a corrected vehicle that is a motorcycle"):]
        self.assertIn("_motorcycle_human_handoff", gate[:900])

    def test_or_43_only_proven_moto_callers_reach_the_motorcycle_helper(self):
        """Requirement 44 / Part C: every call site is gated on typed MOTO or the
        catalogue, never on absence, ambiguity or a catalogue miss."""
        lines = ENGINE_SRC.splitlines()
        sites = [i for i, l in enumerate(lines)
                 if "_motorcycle_human_handoff(" in l and "def " not in l]
        self.assertEqual(len(sites), 5, f"call sites changed: {len(sites)}")
        for i in sites:
            context = "\n".join(lines[max(0, i - 10):i + 1])
            with self.subTest(line=i + 1):
                self.assertTrue(
                    ('== "MOTO"' in context or "_is_motorcycle_enquiry" in context
                     or "_turn_correction_moto" in context),
                    f"ungated motorcycle handoff near line {i + 1}")

    def test_or_44_the_non_moto_reasons_are_their_own(self):
        """The operator alert must not say `motorcycle_enquiry` for a car."""
        for reason in (ce._HANDOFF_CATEGORY_UNRESOLVED,
                       ce._HANDOFF_CORRECTION_UNRESOLVED,
                       ce._HANDOFF_RESPONSE_COMPOSITION_FAILED,
                       ce._HANDOFF_VEHICLE_FLOW_UNAVAILABLE,
                       ce._HANDOFF_LOCATION_FLOW_UNAVAILABLE,
                       ce._HANDOFF_FLOW_DISPATCH_FAILED):
            with self.subTest(reason=reason):
                self.assertNotIn("motorcycle", reason)
                self.assertNotIn("moto", reason)


# ══ G — transport (requirements 45-50) ════════════════════════════════════════════

class G_Transport(unittest.TestCase):

    def test_or_45_flow_success_records_one_response_plan(self):
        """Requirement 45 / Part E.6: the explanatory claim and the Flow are one plan."""
        e, rows = engine(rows=[row(marca="", modelo="")])
        st = state()
        ctx = ctx_for(rows, st)
        e._dispatch_vehicle_clarification_flow(ctx, st)
        self.assertEqual(e._turn_clarification_intro[0], "VEHICLE")
        e._register_correction_propositions(ctx, "")
        self.assertTrue(e._live_plan.registered(
            PropositionId.VEHICLE_CLARIFICATION_INTRO))
        text = e._live_plan.compose("")
        self.assertEqual(text, "", "the Flow body is the message; no duplicate text")

    def test_or_46_flow_dispatch_failure_hands_off_without_marking_it_sent(self):
        """Requirement 46 / Part E."""
        e, rows = engine(rows=[row(marca="", modelo="")], flow_fails=True)
        st = state()
        ctx = ctx_for(rows, st)
        with self.assertRaises(ce.OutboundBlockedError):
            e._dispatch_vehicle_clarification_flow(ctx, st)
        self.assertFalse(st.vehicle_fallback_flow_sent,
                         "a failed dispatch must not consume the one attempt")

    def test_or_47_the_flag_is_set_only_after_a_successful_dispatch(self):
        """Part E.5 / F. R2 set it before the send, so a refused Flow burned the attempt."""
        for fn in (E._dispatch_vehicle_flow_direct, E._dispatch_location_flow_direct):
            with self.subTest(fn=fn.__name__):
                src = inspect.getsource(fn)
                send_at = src.index("self._send_flow_button(")
                flag_at = max(src.rfind("_fallback_flow_sent = True"), 0)
                self.assertGreater(flag_at, send_at,
                                   "the flag must follow the dispatch")
                self.assertIn("if not sent_id:", src)

    def test_or_48_outbound_disabled_dispatches_nothing_and_sets_no_flag(self):
        """Requirement 48."""
        e, rows = engine(rows=[row(marca="", modelo="")], outbound=False)
        st = state()
        blocked = []
        e._blocked_outbound = lambda ctx, text, path_id: (
            blocked.append((text, path_id)) or ce._out(
                "blocked_dispatch", detail="OUTBOUND_GATE_BLOCKED_KILL_SWITCH"))
        out = e._dispatch_vehicle_clarification_flow(ctx_for(rows, st), st)
        self.assertEqual(len(FLOWS), 0, "no Flow reaches transport")
        self.assertFalse(st.vehicle_fallback_flow_sent,
                         "a kill-switched Flow must not consume the attempt")
        self.assertEqual(out.action, "blocked_dispatch")
        self.assertEqual(blocked[0][0], ce._VEHICLE_CLARIFICATION_INTRO)

    def test_or_49_failed_transport_is_not_reported_as_replied(self):
        """Requirement 49."""
        e, rows = engine(text_fails=True)
        st = state()
        out = e._generic_human_handoff(ctx_for(rows, st), st, reason="x")
        self.assertNotEqual(out.action, "replied")
        self.assertEqual(out.action, "human_handoff_blocked")

    def test_or_50_handled_actions_prevents_n8n_fallthrough(self):
        """Requirement 50."""
        from app.schemas.conversation import HANDLED_ACTIONS
        for action in ("replied", "correction_confirmation_blocked",
                       "correction_hold_blocked", "human_handoff_blocked",
                       "blocked_dispatch"):
            with self.subTest(action=action):
                self.assertIn(action, HANDLED_ACTIONS)


# ══ H — state (requirements 51-56) ════════════════════════════════════════════════

class H_State(unittest.TestCase):

    def test_or_51_the_marker_and_budget_are_removed(self):
        """Requirements 51 and 52."""
        for gone in ("clarification_marker", "clarifications_used",
                     "clarification_budget_remains", "CLARIFICATION_BUDGET"):
            with self.subTest(symbol=gone):
                self.assertFalse(hasattr(pc, gone))
        self.assertFalse(hasattr(E, "_spend_correction_clarification"))

    def test_or_52_an_existing_pending_payload_remains_compatible(self):
        """Requirement 53: the resolution must not orphan a proposal already in the column."""
        p = pending()
        st = state()
        st.pending_correction = p.to_payload()
        loaded = pc.load(st)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.pending_id, p.pending_id)
        self.assertEqual(loaded.corrected_fields, p.corrected_fields)

    def test_or_53_a_stale_r2_marker_degrades_to_no_proposal(self):
        """Requirement 52: a marker left by the previous build must not half-parse."""
        st = state()
        st.pending_correction = {"kind": "correction_clarification", "count": 1,
                                 "cycle_id": "c1"}
        self.assertIsNone(pc.load(st))
        self.assertEqual(pc.has_blocking_pending_correction(st), (False, None))

    def test_or_54_flow_flags_reset_only_through_the_canonical_cycle_lifecycle(self):
        """Requirements 55 and 56."""
        reset = inspect.getsource(E._execute_cycle_reset)
        for field in ("vehicle_fallback_flow_sent", "location_fallback_flow_sent"):
            with self.subTest(field=field):
                self.assertIn(f"state.{field} = False", reset)
        # arming, rejecting or expiring a patch must not touch them
        for fn in (E._arm_pending_correction, E._withdraw_dependent_proposals,
                   E._store_pending_correction):
            with self.subTest(fn=fn.__name__):
                src = inspect.getsource(fn)
                self.assertNotIn("vehicle_fallback_flow_sent = False", src)
                self.assertNotIn("location_fallback_flow_sent = False", src)

    def test_or_55_a_correction_does_not_reset_a_flow_attempt(self):
        """Requirement 56, behaviourally: withdrawal clears proposals, not attempts.

        A vehicle correction RESOLVES the vehicle fallback, which is a canonical
        consumption, so that one is expected to clear. A YEAR correction must not.
        """
        e, rows = engine()
        st = state(vehicle_fallback_flow_sent=True, location_fallback_flow_sent=True)
        d = pc.proposal_disposition(st, [("anio", 2019, 2018)])
        e._withdraw_dependent_proposals(ctx_for(rows, st), st, d)
        self.assertTrue(st.vehicle_fallback_flow_sent,
                        "a year correction answers no vehicle question")
        self.assertTrue(st.location_fallback_flow_sent)

    def test_or_56_no_new_migration_was_added(self):
        """Part F: the canonical persisted Flow state already exists."""
        versions = pathlib.Path(__file__).resolve().parents[1] / "backend/migrations/versions"
        # SB1 (Smart Booking Track B) later added its own, separately reviewed migration.
        self.assertEqual(sorted(p.name for p in versions.glob("2026100*.py")),
                         ["20261005_pending_correction.py",
                          "20261006_clarification_attempts.py",
                          "20261007_sb1_booking_representation.py"])


# ══ I — observability (requirements 57-63) ════════════════════════════════════════

class I_Observability(unittest.TestCase):

    def test_or_57_the_turn_records_the_routing_decision(self):
        """Requirements 57-60."""
        e, rows = engine(rows=[row(marca="", modelo="")])
        st = state()
        ctx = ctx_for(rows, st)
        route, why = _route(e, ctx, st)
        _dispatch(e, ctx, st, route, why)
        self.assertEqual(e._turn_clarification_route, Route.VEHICLE_FLOW_REQUIRED)
        self.assertEqual(e._turn_clarification_reason, Why.VEHICLE_IDENTITY_UNRESOLVED)
        self.assertEqual(e._turn_clarification_flow, "VEHICLE")
        self.assertIsNotNone(e._turn_clarification_intro)

    def test_or_58_semantic_resolution_without_a_flow_is_recorded_as_such(self):
        """Requirement 57: the no-Flow case is observable, not merely silent."""
        e, rows = engine()
        st = state()
        route, why = _route(e, ctx_for(rows, st), st)
        self.assertEqual(route, Route.NO_CLARIFICATION_REQUIRED)
        self.assertIn(why, Why.ALL)

    def test_or_59_the_unresolved_after_flow_handoff_is_recorded(self):
        """Requirement 60."""
        e, rows = engine(rows=[row(marca="", modelo="")])
        # MIGRATED (G3-7F sequencing): availability lives in the ATTEMPT row now, not
        # in `*_fallback_flow_sent`. The flag means only "a proposal is outstanding"
        # and clearing it can never make the attempt available again.
        _spend(e, "VEHICLE")
        st = state(vehicle_fallback_flow_sent=False)
        ctx = ctx_for(rows, st)
        route, why = _route(e, ctx, st)
        _dispatch(e, ctx, st, route, why)
        self.assertEqual(e._turn_handoff_reason, "VEHICLE_ATTEMPT_SPENT")
        self.assertTrue(e._turn_generic_handoff)

    def test_or_60_no_reason_code_can_label_a_car_a_motorcycle(self):
        """Requirement 61."""
        for reason in Why.ALL + Route.ALL:
            with self.subTest(code=reason):
                self.assertNotIn("MOTO", reason)
                self.assertNotIn("moto", reason.lower().replace("promotion", ""))

    def test_or_61_the_metrics_fields_distinguish_resolution_from_fallback(self):
        """Requirement 62 / Part H: the route and reason are the measurement."""
        self.assertEqual(len(Route.ALL), 5)
        self.assertIn(Route.NO_CLARIFICATION_REQUIRED, Route.ALL)
        self.assertIn(Route.VEHICLE_FLOW_REQUIRED, Route.ALL)
        self.assertIn(Route.LOCATION_FLOW_REQUIRED, Route.ALL)
        self.assertGreaterEqual(len(Why.ALL), 9)
        # every route is reachable with a distinct reason, so counts are meaningful
        seen = set()
        for kw in (dict(),
                   dict(correction_target_known=True, correction_values_known=True),
                   dict(vehicle_domain_proven=True, vehicle_identity_resolved=False),
                   dict(location_domain_proven=True, location_resolved=False),
                   dict(vehicle_domain_proven=True, vehicle_identity_resolved=True,
                        vehicle_category_resolved=False)):
            seen.add(pc.route_clarification(**kw))
        self.assertEqual(len(seen), 5, "each route/reason pair must be distinguishable")

    def test_or_62b_the_trace_carries_the_clarification_record(self):
        """Part H: the record exists on every turn, including the ones that asked nothing."""
        e, rows = engine()
        st = state()
        _route(e, ctx_for(rows, st), st)
        rec = e._clarification_for_trace()
        for key in ("route", "reason", "flow_dispatched", "explanatory_planned",
                    "generic_handoff", "handoff_reason", "confirmation_bound",
                    "corrected_category", "moto_proven", "resolved_without_flow",
                    "g3_8_enforced"):
            with self.subTest(key=key):
                self.assertIn(key, rec)
        self.assertIs(rec["g3_8_enforced"], False,
                      "no trace may imply G3-8 enforcement is live")
        self.assertIs(rec["resolved_without_flow"], True)

    def test_or_62c_the_trace_contract_was_bumped(self):
        from app.schemas.hybrid_trace import TRACE_VERSION
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.9")

    def test_or_62d_the_inspector_renders_the_clarification_card(self):
        """Requirements 58-61 at the rendering boundary."""
        from app.ui.hybrid_trace_view import (_clarification_card,
                                              CONTRACTS_WITH_CANDIDATE_SECTION)
        from app.schemas.hybrid_trace import TRACE_VERSION
        self.assertIn(TRACE_VERSION, CONTRACTS_WITH_CANDIDATE_SECTION)
        e, rows = engine(rows=[row(marca="", modelo="")])
        # MIGRATED (G3-7F sequencing): availability lives in the ATTEMPT row now, not
        # in `*_fallback_flow_sent`. The flag means only "a proposal is outstanding"
        # and clearing it can never make the attempt available again.
        _spend(e, "VEHICLE")
        st = state(vehicle_fallback_flow_sent=False)
        ctx = ctx_for(rows, st)
        route, why = _route(e, ctx, st)
        _dispatch(e, ctx, st, route, why)
        html_out = _clarification_card({"trace_version": TRACE_VERSION,
                                        "clarification": e._clarification_for_trace()})
        self.assertIn("Aclaración y derivación", html_out)
        self.assertIn("asesor", html_out, "the escalation is visible")
        self.assertIn("G3-8", html_out, "the gate status is stated")
        for forbidden in ("MOTO", "moto"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, html_out.replace("MOTO probada", ""))

    def test_or_62e_the_inspector_says_a_flow_is_the_fallback_not_the_target(self):
        """Part H's measurement intent, stated to the operator."""
        from app.ui.hybrid_trace_view import CLARIFICATION_SCOPE_NOTE
        self.assertIn("recurso de seguridad", CLARIFICATION_SCOPE_NOTE)
        self.assertIn("G3-8", CLARIFICATION_SCOPE_NOTE)

    def test_or_62f_a_backward_trace_renders_safely(self):
        """A 1.7 payload has no `clarification` key; the card must render nothing."""
        from app.ui.hybrid_trace_view import _clarification_card
        for payload in ({}, {"clarification": None}, {"clarification": "junk"},
                        {"clarification": []}):
            with self.subTest(payload=payload):
                self.assertEqual(_clarification_card(payload), "")

    def test_or_62g_the_deferral_to_g3_8_is_recorded(self):
        """Part J: R2R-07 must be written down where the next milestone will read it."""
        src = pathlib.Path(inspect.getsourcefile(pc)).read_text(encoding="utf-8")
        self.assertIn("DEFERRED TO G3-8", src)
        self.assertIn("R2R-07", src)
        self.assertIn("ThreadRevision", src)

    def test_or_62_privacy_remains_clean(self):
        """Requirement 63: no phone, WAMID, secret or raw customer prose in the new state."""
        e, rows = engine(rows=[row(marca="", modelo="")])
        st = state()
        ctx = ctx_for(rows, st)
        route, why = _route(e, ctx, st)
        _dispatch(e, ctx, st, route, why)
        observable = (e._turn_clarification_route, e._turn_clarification_reason,
                      e._turn_clarification_flow, e._turn_handoff_reason)
        for value in observable:
            with self.subTest(value=value):
                if value is None:
                    continue
                self.assertNotIn("wamid", str(value).lower())
                self.assertFalse(any(ch.isdigit() for ch in str(value)))


# ══ J — accounting and architecture (requirements 64-74) ══════════════════════════

class J_AccountingAndArchitecture(unittest.TestCase):

    def test_or_63_nested_parenthesis_subfail_is_retained(self):
        """Requirement 64 / Part I — review finding R2R-06."""
        out = ("=== short test summary info ===\n"
               "SUBFAILED(outcome='tuple(False, None)') ../tests/a.py::T::t\n"
               "1 failed, 0 passed in 1s\n")
        acc = ra.parse(out)
        self.assertEqual(len(acc.subfailed), 1)
        self.assertTrue(acc.reconciles, acc.explain())

    def test_or_64_labels_with_commas_quotes_and_tuples_are_retained(self):
        for label in ("(outcome='tuple(False, None)')", "(value=('a, b', 'c)d'))",
                      "(case='patch only')", "(i=1)", "(x=(1, 2))"):
            with self.subTest(label=label):
                out = ("=== short test summary info ===\n"
                       f"SUBFAILED{label} ../tests/a.py::T::t\n"
                       "1 failed, 0 passed in 1s\n")
                acc = ra.parse(out)
                self.assertEqual(len(acc.subfailed), 1, label)
                self.assertTrue(acc.reconciles)

    def test_or_65_paths_with_spaces_and_parametrized_ids_are_retained(self):
        """Requirements 65 and 66."""
        out = ("=== short test summary info ===\n"
               "FAILED ../tests/W T/M/test_c.py::T::ta - x\n"
               "FAILED ../tests/a.py::T::t[case-1] - y\n"
               "FAILED ../tests/a.py::T::t[case-2] - z\n"
               "3 failed, 0 passed in 1s\n")
        acc = ra.parse(out)
        self.assertEqual(len(acc.failed), 3)
        self.assertTrue(acc.reconciles)

    def test_or_66_a_recognized_outcome_is_never_discarded(self):
        """Part I: an unaddressable line is counted under its raw text, not dropped."""
        out = ("=== short test summary info ===\n"
               "SUBFAILED(weird) something-with-no-node-id\n"
               "1 failed, 0 passed in 1s\n")
        acc = ra.parse(out)
        self.assertEqual(len(acc.subfailed), 1)
        self.assertTrue(acc.reconciles)

    def test_or_67_arithmetic_mismatch_fails_certification(self):
        """Requirement 67."""
        bad = ("=== short test summary info ===\n"
               + "".join(f"FAILED ../tests/a.py::T::t{i}\n" for i in range(58))
               + "".join(f"ERROR ../tests/b.py::T::e{i}\n" for i in range(9))
               + "59 failed, 100 passed, 9 errors in 1s\n")
        acc = ra.parse(bad)
        self.assertFalse(acc.reconciles)
        self.assertFalse(acc.clean)

    def test_or_68_apply_candidate_remains_the_sole_correction_writer(self):
        """Requirement 68."""
        import ast
        tree = ast.parse(ENGINE_SRC)
        self.assertEqual(
            [n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name.startswith("_apply_candidate")],
            ["_apply_candidate"])
        # the new paths write no candidate at all
        for fn in (E._generic_human_handoff, E._turn_clarification_decision,
                   E._execute_clarification,
                   E._dispatch_vehicle_clarification_flow,
                   E._dispatch_location_clarification_flow):
            with self.subTest(fn=fn.__name__):
                src = inspect.getsource(fn)
                self.assertNotIn("WhatsAppThreadCandidate", src)
                self.assertNotIn("_apply_candidate", src)

    def test_or_69_no_phrase_catalogue_was_added(self):
        """Requirement 69."""
        for fn in (pc.route_clarification, E._turn_clarification_decision,
                   E._execute_clarification, E._generic_human_handoff):
            with self.subTest(fn=fn.__name__):
                src = inspect.getsource(fn)
                for token in ("re.search", "re.match", "re.compile",
                              '"si"', "'si'", '"dale"', '"ok"'):
                    self.assertNotIn(token, src)

    def test_or_70_both_flags_remain_false(self):
        """Requirement 70."""
        import dataclasses
        from app.settings import Settings
        if dataclasses.is_dataclass(Settings):
            defaults = {f.name: f.default for f in dataclasses.fields(Settings)}
        else:
            defaults = {k: v.default for k, v in Settings.model_fields.items()}
        for name in ("correction_target_grounding_enabled",
                     "provisional_correction_enabled"):
            with self.subTest(flag=name):
                self.assertIs(defaults[name], False)

    def test_or_71_confirmation_admissible_remains_unwired(self):
        """Requirement 71."""
        import subprocess
        hits = subprocess.run(
            ["grep", "-rn", "confirmation_admissible", "backend/app/"],
            capture_output=True, text=True,
            cwd=str(pathlib.Path(__file__).resolve().parents[1])).stdout.splitlines()
        callers = [h for h in hits if "def confirmation_admissible" not in h
                   and "pending_correction.py" not in h]
        self.assertEqual(callers, [], f"unexpected caller: {callers}")

    def test_or_72_no_g3_8_gate_was_modified(self):
        """Requirement 72: the blocking predicate still has one call site, the trace."""
        self.assertEqual(ENGINE_SRC.count("has_blocking_pending_correction"), 1)
        self.assertIn('"enforced_at_gates"',
                      inspect.getsource(E._pending_correction_for_trace))

    def test_or_73_no_booking_revision_is_modified(self):
        """Requirement 73 / Part J: R2R-07 is deferred, not touched."""
        for fn in (E._generic_human_handoff, E._withdraw_dependent_proposals,
                   E._turn_clarification_decision, E._execute_clarification):
            with self.subTest(fn=fn.__name__):
                src = inspect.getsource(fn)
                self.assertNotIn("ThreadRevision", src)
                self.assertNotIn("current_revision_id", src)

    def test_or_74_no_meta_flow_definition_is_modified(self):
        """Requirement 74. The published Flow is pinned by content, not by a git diff:
        a hash holds inside a sealed container and survives a rebase."""
        root = pathlib.Path(__file__).resolve().parents[1]
        published = root / "meta/flows/booking/PUBLISHED_28104222025943520_v7.3.json"
        self.assertTrue(published.exists(), "the published Flow definition moved")
        import hashlib
        self.assertEqual(
            hashlib.sha256(published.read_bytes()).hexdigest(),
            "274038ba234a49fa4f99bae47c00f6cac640e248d56a373d40ae8fba6c1afb15",
            "the published Meta Flow definition changed; this milestone may not touch it")

    def test_or_75_the_response_plan_still_holds_no_wording(self):
        """G3-5F's architectural invariant, which this milestone had to respect: the
        clarification and handoff sentences live beside the other Flow bodies in the engine,
        not in the plan."""
        code = "\n".join(l for l in PLAN_SRC.splitlines()
                         if not l.lstrip().startswith("#"))
        for word in ("formulario", "asesor", "cotiza", "traslado"):
            with self.subTest(word=word):
                self.assertNotIn(word, code)


# ══ K — hostile controls: every mutation must be caught ═══════════════════════════

class K_HostileControls(unittest.TestCase):

    def test_or_76_mutation_force_vehicle_flow_despite_sufficient_evidence(self):
        self.assertEqual(
            pc.route_clarification(vehicle_domain_proven=True,
                                   vehicle_identity_resolved=True,
                                   vehicle_category_resolved=True)[0],
            Route.NO_CLARIFICATION_REQUIRED)

    def test_or_77_mutation_force_location_flow_despite_resolved_locality(self):
        self.assertEqual(
            pc.route_clarification(location_domain_proven=True,
                                   location_resolved=True)[0],
            Route.NO_CLARIFICATION_REQUIRED)

    def test_or_78_mutation_send_both_flows_together(self):
        route, _ = pc.route_clarification(
            vehicle_domain_proven=True, vehicle_identity_resolved=False,
            location_domain_proven=True, location_resolved=False)
        self.assertEqual(route, Route.VEHICLE_FLOW_REQUIRED)
        self.assertNotIn("and self._dispatch_location", 
                         inspect.getsource(E._execute_clarification))

    def test_or_79_mutation_resend_a_flow_after_its_sent_flag(self):
        for field, reason in (("vehicle_flow_attempted",
                               Why.VEHICLE_FLOW_ALREADY_ATTEMPTED),
                              ("location_flow_attempted",
                               Why.LOCATION_FLOW_ALREADY_ATTEMPTED)):
            kw = dict(vehicle_domain_proven=True, vehicle_identity_resolved=False)
            if field.startswith("location"):
                kw = dict(location_domain_proven=True, location_resolved=False)
            with self.subTest(field=field):
                self.assertEqual(pc.route_clarification(**{**kw, field: True}),
                                 (Route.GENERIC_HUMAN_REQUIRED, reason))

    def test_or_80_mutation_restore_the_free_text_clarification_marker(self):
        pc_src = pathlib.Path(inspect.getsourcefile(pc)).read_text(encoding="utf-8")
        code = "\n".join(l for l in pc_src.splitlines()
                         if not l.lstrip().startswith("#"))
        for token in ("def clarification_marker", "def clarifications_used",
                      "CLARIFICATION_BUDGET ="):
            with self.subTest(token=token):
                self.assertNotIn(token, code)

    def test_or_81_mutation_treat_an_unknown_category_as_moto(self):
        self.assertEqual(
            pc.route_clarification(vehicle_domain_proven=True,
                                   vehicle_identity_resolved=True,
                                   vehicle_category_resolved=False)[0],
            Route.GENERIC_HUMAN_REQUIRED)
        self.assertNotIn("MOTO", code_only(E._generic_human_handoff))
        cat = inspect.getsource(E._correction_category)
        self.assertIn("return None", cat, "an unplaceable vehicle fails closed")

    def test_or_82_mutation_bind_confirmation_after_degraded_composition(self):
        src = inspect.getsource(E._request_correction_confirmation)
        self.assertIn("if degraded or not included:", src)
        bind_at = src.index("self._store_pending_correction(state, candidate_patch)")
        guard_at = src.index("if degraded or not included:")
        self.assertLess(guard_at, bind_at, "the guard must precede the binding")

    def test_or_83_mutation_set_a_flow_flag_before_dispatch(self):
        for fn in (E._dispatch_vehicle_flow_direct, E._dispatch_location_flow_direct):
            with self.subTest(fn=fn.__name__):
                src = inspect.getsource(fn)
                self.assertGreater(src.rfind("_fallback_flow_sent = True"),
                                   src.index("self._send_flow_button("))

    def test_or_84_mutation_lose_a_nested_parenthesis_subfail(self):
        src = pathlib.Path(inspect.getsourcefile(ra)).read_text(encoding="utf-8")
        self.assertIn("_NODE_ID", src)
        self.assertNotIn(r'(\([^)]*\))?\s+(.+?)', src)
        acc = ra.parse("=== short test summary info ===\n"
                       "SUBFAILED(o='t(1, 2)') ../tests/a.py::T::t\n"
                       "1 failed, 0 passed in 1s\n")
        self.assertEqual(len(acc.subfailed), 1)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
