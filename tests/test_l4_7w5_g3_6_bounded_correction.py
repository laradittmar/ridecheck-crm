"""L4.7W5-GATE3-G3-6-BOUNDED-CORRECTION — a price that survives, and a card that admits its limits.

The independent G3-6 review found the migration real and the claim overstated:

  G36-01 HIGH  `turn.primary_reply` is still ONE opaque item, and a model-authored reply can
               carry several business claims inside it — "Sale $85.000 y podés pagar con
               transferencia" is one plan item and two claims. Complete coverage is not
               achieved, and cannot be until the reply model stops authoring consequential
               facts (G3-7).
  G36-02 MED   the Inspector's coverage warning was removed anyway, so the card implies its
               item count describes the message.
  G36-03 MED   a canonical VERIFIED price disappears when the same opaque reply also carries
               false opening hours and is therefore rejected. The parent kept it.
  G36-07 LOW   no test failed for either.

This suite is the missing evidence. It does NOT decompose model prose — G36-01 stays open
and is asserted as open, deliberately, so the limitation is visible rather than implied.

A — the opaque primary, asserted as still opaque (the blind spot, named)
B — the Inspector tells the truth about what it counts
C — the canonical price proposition: authorization, provenance, purity
D — price plus conflicting hours (the blind spot, reproduced)
E — nothing else moved
"""
from __future__ import annotations

import inspect
import json
import pathlib
import sys
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from test_l4_7w5_g3_5b_unified_evidence_boundary import HOURS_Q, Turn         # noqa: E402
import app.services.conversation_engine as ce                                 # noqa: E402
from app.services.conversation_engine import ConversationEngine               # noqa: E402
from app.services.response_composition import (                               # noqa: E402
    LiveResponsePlan, PropositionId, SuppressionReason,
)
from app.schemas.hybrid_trace import (                                        # noqa: E402
    DecisionAuthority, FactSource, RendererSource, ResponseItemKind, ResponsePolicy,
    TRACE_VERSION,
)
from app.services.schedule import business_hours_summary_es                   # noqa: E402

VIEW_SRC = pathlib.Path(
    ROOT / "backend/app/ui/hybrid_trace_view.py").read_text(encoding="utf-8")

FALSE_SATURDAY = "Trabajamos sábados de 9 a 13 hs."       # canonical Saturday is 9 a 15
PRICE_TOTAL = 85000


def bench(primary, *, topics=(), quote=PRICE_TOTAL, answer_source="PRICING_SERVICE",
          zone=("CABA", "Palermo"), marca="Ford", modelo="Focus", anio=2019,
          candidates=("focus",)):
    """The production producers and the production plan, at the point they really run."""
    state = SimpleNamespace(last_visible_slots=None, last_offered_slots=None,
                            needs_human=False, last_stage=ce.STAGE_QUALIFYING,
                            current_focus_candidate_id=1,
                            home_zone_group=zone[0], home_zone_detail=zone[1],
                            pending_fuzzy_catalog_key=None, pending_turn_evidence_text=None)
    focus = SimpleNamespace(id=1, tipo_vehiculo="AUTO", marca=marca, modelo=modelo,
                            anio=anio, zone_group=zone[0], zone_detail=zone[1])
    ctx = SimpleNamespace(state=state, thread=SimpleNamespace(id=1),
                          candidates=list(candidates),
                          contact=SimpleNamespace(wa_id="x"))
    e = ConversationEngine.__new__(ConversationEngine)
    e._turn_ctx = ctx
    e._contributing_sources = None
    e._turn_fact_sources = []
    e._turn_faq_burst = "b"
    e._turn_clarification_burst = None
    e._turn_text = "t"
    e._live_plan = LiveResponsePlan(turn_id="t")
    e._turn_price_quote = (SimpleNamespace(precio_total=quote) if quote else None)
    e._answer_source_value = answer_source
    e._turn_decision_authority = None
    e._faq_topics_for_burst = lambda t, ctx=None, st=None: set(topics)
    e._focus_candidate = lambda c: focus
    e._get_active_inspection_location = lambda c, s: zone
    e._detect_explicit_inspection_request = lambda t: True
    e._decision_log = lambda *a, **k: None
    e._register_response_propositions(ctx, primary)
    out = e._live_plan.compose(primary)
    return SimpleNamespace(
        text=out, engine=e,
        props={p.proposition_id: p for p in e._live_plan.propositions},
        plan=e._live_plan.to_plan(plan_id="p",
                                  renderer_source=RendererSource.DETERMINISTIC_TEMPLATE,
                                  outcome="replied"))


def money(total=PRICE_TOTAL):
    return f"${total:,.0f}".replace(",", ".")


# ══ A — the opaque primary, named rather than implied ══════════════════════

class A_OpaquePrimaryIsStillOpaque(unittest.TestCase):
    """The audit's first blind spot. G36-01 is OPEN and this suite says so out loud.

    Decomposing model prose into claims is the reply-model demotion, which this correction
    is forbidden to attempt. What it must not do is let the limitation go unrecorded again.
    """

    MULTI_CLAIM = (
        ("price plus payment method", "Sale $85.000 y podés pagar con transferencia."),
        ("availability offer",        "Te puedo dar el martes a las 14. ¿Te sirve?"),
        ("report plus hours",         "Te mandamos un informe de 250 puntos. "
                                      "Trabajamos lunes de 13 a 18 hs."),
    )

    def test_bc36_01_a_multi_claim_primary_is_still_one_opaque_item(self):
        for label, prose in self.MULTI_CLAIM:
            r = bench(prose, quote=None, answer_source=None)
            with self.subTest(case=label):
                primary = [p for p in r.props.values()
                           if p.proposition_id == PropositionId.PRIMARY_REPLY]
                self.assertEqual(len(primary), 1)
                self.assertEqual(primary[0].kind, ResponseItemKind.TEXT,
                                 "the primary was decomposed — out of scope for G3-6-BC")
                self.assertIn(prose, r.text)

    def test_bc36_02_no_prose_decomposition_was_attempted(self):
        """G36-01 is owned by G3-7. Nothing here may start classifying model sentences."""
        producers = "\n".join(inspect.getsource(getattr(ConversationEngine, n)) for n in (
            "_register_response_propositions", "_register_price_proposition",
            "_register_faq_propositions", "_register_clarification_proposition",
            "_register_canonical_vehicle_proposition"))
        for surgery in ("re.split(", "sentences", "split('.')", 'split(".")'):
            with self.subTest(op=surgery):
                self.assertNotIn(surgery, producers)


# ══ B — the Inspector tells the truth ══════════════════════════════════════

class B_InspectorCoverage(unittest.TestCase):

    LIVE = {"trace_version": TRACE_VERSION, "final_message_count": 1,
            "response_plan": {"plan_id": "response-plan:abc", "shadow": False,
                              "item_count": 1, "planned_text_items": 1,
                              "planned_flow_items": 0, "suppressed_count": 0,
                              "dispatch_path": "TEXT", "dispatch_result": "ALLOWED",
                              "items": [{"kind": "TEXT",
                                         "proposition_id": "turn.primary_reply",
                                         "included": True,
                                         "decision_authority": "DETERMINISTIC_RULE",
                                         "renderer_source": "REPLY_MODEL"}]}}

    def card(self):
        from app.ui.hybrid_trace_view import _response_plan_card
        return _response_plan_card(self.LIVE)

    def test_bc36_03_the_card_warns_that_opaque_primary_claims_are_uncounted(self):
        from app.ui.hybrid_trace_view import PLAN_SCOPE_NOTE
        card = self.card()
        self.assertIn("Alcance", card)
        self.assertIn("turn.primary_reply", PLAN_SCOPE_NOTE,
                      "the note does not name the opaque item")
        self.assertIn("opaco", PLAN_SCOPE_NOTE)
        self.assertIn("varias", PLAN_SCOPE_NOTE)

    def test_bc36_04_the_card_discloses_the_outbound_validator(self):
        """G36-04. Its removals are not plan suppression records, and the card says so."""
        from app.ui.hybrid_trace_view import PLAN_SCOPE_NOTE
        self.assertIn("validador de salida", PLAN_SCOPE_NOTE)
        self.assertIn("supresiones del plan", PLAN_SCOPE_NOTE)

    def test_bc36_05_the_card_says_the_count_is_not_the_total(self):
        from app.ui.hybrid_trace_view import PLAN_SCOPE_NOTE
        self.assertIn("no es el total", PLAN_SCOPE_NOTE)

    def test_bc36_06_the_note_does_not_erase_what_WAS_migrated(self):
        """The correction must not swing into the opposite falsehood: the registered
        families are real, countable and traceable, and the note has to say that too."""
        from app.ui.hybrid_trace_view import PLAN_SCOPE_NOTE
        for present in ("REGISTRADAS", "FAQ", "vehículo canónico", "precio canónico",
                        "supresiones"):
            with self.subTest(term=present):
                self.assertIn(present, PLAN_SCOPE_NOTE)

    def test_bc36_07_the_heading_is_unchanged_and_claims_no_new_authority(self):
        from app.ui.hybrid_trace_view import PLAN_SCOPE_NOTE
        card = self.card()
        self.assertIn("Plan de respuesta (composición)", card)
        self.assertNotIn("Plan de respuesta (autoridad)", card)
        for refused in ("acción principal", "mutaciones canónicas", "transporte",
                        "resultado transaccional"):
            with self.subTest(refused=refused):
                self.assertIn(refused, PLAN_SCOPE_NOTE)

    def test_bc36_08_the_contract_was_not_bumped_for_wording(self):
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.5")


# ══ C — the canonical price proposition ════════════════════════════════════

class C_PriceProposition(unittest.TestCase):

    QUOTE_REPLY_FRAGMENT = "La cotización para la revisión"

    def test_bc36_09_an_authorized_quote_registers_a_typed_proposition(self):
        r = bench("Genial! " + self.QUOTE_REPLY_FRAGMENT + f" es de {money()}.")
        item = r.props[PropositionId.CANONICAL_PRICE]
        self.assertEqual(item.kind, ResponseItemKind.CANONICAL_FACT)
        self.assertEqual(item.claim_family, "pricing")
        self.assertEqual(item.decision_authority, DecisionAuthority.PRICING_SERVICE)
        self.assertEqual(item.fact_sources,
                         (FactSource.PRICING_SERVICE, FactSource.CANONICAL_STATE))
        self.assertEqual(item.renderer_source, RendererSource.DETERMINISTIC_TEMPLATE)
        self.assertEqual(item.locked_values, (("pricing.precio_total", "85000"),))

    def test_bc36_10_authorization_is_typed_and_has_exactly_two_conditions(self):
        """Neither condition reads the words. No quote, or a turn the pricing service is
        not answering, registers nothing."""
        self.assertNotIn(PropositionId.CANONICAL_PRICE,
                         bench("Sale $85.000.", quote=None).props)
        self.assertNotIn(PropositionId.CANONICAL_PRICE,
                         bench("Sale $85.000.", answer_source="CE_AI").props)
        self.assertNotIn(PropositionId.CANONICAL_PRICE,
                         bench("Sale $85.000.", answer_source=None).props)
        self.assertIn(PropositionId.CANONICAL_PRICE, bench("Genial!").props)

    def test_bc36_11_the_price_is_never_read_out_of_the_prose(self):
        """A model sentence naming a DIFFERENT number does not change what is rendered."""
        r = bench("Sale $999.999 por la revisión.")
        self.assertEqual(r.props[PropositionId.CANONICAL_PRICE].text().count(money()), 1)
        self.assertNotIn("999.999", r.props[PropositionId.CANONICAL_PRICE].text())

    def test_bc36_12_the_renderer_computes_nothing(self):
        body = inspect.getsource(ConversationEngine._register_price_proposition)
        rendered_at = body.index("rendered = self._build_quote_reply(")
        registered_at = body.index("plan.register(")
        self.assertLess(rendered_at, registered_at,
                        "the price sentence is built inside the renderer")
        self.assertIn("render=lambda: rendered", body)
        for forbidden in ("self._pricing", ".quote(", "precio_total *", "int(total) *",
                          "self.db", "db.commit", "_call_openai", "_send_text_to_wa"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)

    def test_bc36_13_no_phrase_matcher_authorizes_the_price(self):
        """Scanned over the EXECUTABLE body only.

        The docstring quotes the audit's scenario and names the canonical field
        `precio_total`, so a whole-source scan would fail on the explanation rather than on
        the code. What must contain no lexical work is the code.
        """
        import ast, textwrap
        src = textwrap.dedent(
            inspect.getsource(ConversationEngine._register_price_proposition))
        tree = ast.parse(src)
        fn = tree.body[0]
        if (fn.body and isinstance(fn.body[0], ast.Expr)
                and isinstance(fn.body[0].value, ast.Constant)):
            fn.body = fn.body[1:]                      # drop the docstring
        body = ast.unparse(fn).lower()
        for lexical in ("cuánto", "cuanto", "cotiza", "sale ", "re.search", "re.match",
                        "re.findall", " in text", "in primary", ".lower()"):
            with self.subTest(token=lexical):
                self.assertNotIn(lexical, body)
        # The only two authorization reads, and neither touches the words.
        self.assertIn("_turn_price_quote", body)
        self.assertIn("pricing_service", body)
        self.assertNotIn("text", body.replace("text: str", ""),
                         "the producer reads the prose")

    def test_bc36_14_an_ordinary_quote_turn_does_not_say_the_price_twice(self):
        primary = ("Genial! La cotización para la revisión del Ford Focus 2019 en Palermo "
                   f"es de {money()}. Si te parece bien, podemos avanzar.")
        r = bench(primary)
        self.assertEqual(r.text.count(money()), 1, "the customer was told the price twice")
        self.assertIn(primary, r.text)

    def test_bc36_15_the_redundant_price_stays_traceable(self):
        r = bench("Genial! " + self.QUOTE_REPLY_FRAGMENT + f" es de {money()}.")
        item = next(i for i in r.plan.items
                    if i.proposition_id == PropositionId.CANONICAL_PRICE)
        self.assertFalse(item.included)
        self.assertEqual(item.suppression_reason, SuppressionReason.ALREADY_STATED)
        self.assertEqual(item.policy, ResponsePolicy.P6_NO_ROUTINE_FACT_REPETITION)
        self.assertIn(PropositionId.CANONICAL_PRICE,
                      [i["proposition_id"] for i in r.plan.to_payload()["items"]])

    def test_bc36_16_it_serializes_and_round_trips(self):
        r = bench("Genial!")
        payload = r.plan.to_payload()
        self.assertEqual(json.loads(json.dumps(payload)), payload)
        item = next(i for i in payload["items"]
                    if i["proposition_id"] == PropositionId.CANONICAL_PRICE)
        self.assertEqual(item["kind"], ResponseItemKind.CANONICAL_FACT)
        self.assertEqual(item["decision_authority"], DecisionAuthority.PRICING_SERVICE)
        self.assertEqual(item["locked_values"], [["pricing.precio_total", "85000"]])

    def test_bc36_17_the_locked_value_carries_no_customer_data(self):
        r = bench("Genial!")
        blob = json.dumps(r.plan.to_payload())
        for fragment in ("wamid.", "5491", "@", "Palermo, CABA,"):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, blob)


# ══ D — the audit's reproduction: price plus conflicting hours ═════════════

class D_PriceSurvivesTheRejection(unittest.TestCase):

    PRIMARY = f"La revisión sale {money()}. {FALSE_SATURDAY}"

    def result(self, **kw):
        return bench(self.PRIMARY, topics=("business_hours",), **kw)

    def test_bc36_18_the_primary_is_still_rejected_whole(self):
        r = self.result()
        primary = r.props[PropositionId.PRIMARY_REPLY]
        self.assertFalse(primary.included)
        self.assertEqual(primary.suppression_reason,
                         SuppressionReason.CONFLICTS_WITH_CANONICAL)

    def test_bc36_19_the_false_hours_never_reach_the_customer(self):
        r = self.result()
        self.assertNotIn("sábados de 9 a 13", r.text)
        self.assertFalse(ce._hours_truth_violations(ce._norm_lower(r.text)))

    def test_bc36_20_THE_PRICE_SURVIVES(self):
        """G36-03. At 9a6ca45 this text contained no price at all."""
        r = self.result()
        self.assertIn(money(), r.text, "the authorized canonical price was lost")
        self.assertTrue(r.props[PropositionId.CANONICAL_PRICE].included)

    def test_bc36_21_the_surviving_price_came_from_the_canonical_renderer(self):
        r = self.result()
        self.assertIn("La cotización para la revisión", r.text)
        self.assertIn("Ford Focus 2019", r.text)
        self.assertIn("Palermo", r.text)
        self.assertNotIn("La revisión sale", r.text,
                         "the model's own sentence was resurrected")

    def test_bc36_22_the_price_appears_exactly_once(self):
        self.assertEqual(self.result().text.count(money()), 1)

    def test_bc36_23_the_canonical_hours_answer_is_unchanged(self):
        r = self.result()
        self.assertEqual(r.text.count(business_hours_summary_es()), 1)
        self.assertIn("sábados de 9 a 15 hs", r.text)

    def test_bc36_24_without_a_canonical_quote_no_price_is_invented(self):
        """A model-authored price rejected with the primary is never resurrected."""
        r = self.result(quote=None)
        self.assertNotIn(PropositionId.CANONICAL_PRICE, r.props)
        self.assertNotIn(money(), r.text)
        self.assertNotIn("85.000", r.text)
        self.assertIn(business_hours_summary_es(), r.text)

    def test_bc36_25_a_wrong_model_price_is_replaced_not_preserved(self):
        r = bench(f"La revisión sale $999.999. {FALSE_SATURDAY}",
                  topics=("business_hours",))
        self.assertNotIn("999.999", r.text, "an unverified price survived")
        self.assertIn(money(), r.text, "the canonical price is missing")

    def test_bc36_26_the_plan_records_the_price_as_included(self):
        r = self.result()
        item = next(i for i in r.plan.items
                    if i.proposition_id == PropositionId.CANONICAL_PRICE)
        self.assertTrue(item.included)
        self.assertIsNone(item.suppression_reason)
        self.assertIn(ResponseItemKind.CANONICAL_FACT, r.plan.to_payload()["item_kinds"])

    def test_bc36_27_other_authorized_propositions_still_survive_beside_it(self):
        r = bench(f"La revisión sale {money()}. {FALSE_SATURDAY}",
                  topics=("business_hours", "payment"))
        for pid in (PropositionId.CANONICAL_PRICE, PropositionId.faq("business_hours"),
                    PropositionId.faq("payment")):
            with self.subTest(item=pid):
                self.assertTrue(r.props[pid].included)


# ══ E — nothing else moved ═════════════════════════════════════════════════

class E_NothingElseMoved(unittest.TestCase):

    def test_bc36_28_the_migrated_producers_are_unchanged(self):
        r = bench("Anotado.", topics=("business_hours", "report"),
                  zone=(None, None), quote=None, answer_source=None, candidates=())
        for pid in (PropositionId.faq("business_hours"), PropositionId.faq("report"),
                    PropositionId.clarification("inspection_location")):
            with self.subTest(item=pid):
                self.assertIn(pid, r.props)

    def test_bc36_29_def04_remains_closed(self):
        for wrong in ("Trabajamos sábados de 9 a 13 hs.", "Trabajamos lunes de 9 a 18 hs.",
                      "Los domingos también trabajamos."):
            r = bench(wrong, topics=("business_hours",), quote=None, answer_source=None)
            with self.subTest(prose=wrong[:30]):
                self.assertFalse(ce._hours_truth_violations(ce._norm_lower(r.text)))
                self.assertIn(business_hours_summary_es(), r.text)

    def test_bc36_30_the_deleted_repair_machinery_is_still_gone(self):
        for gone in ("_strip_unverified_hours", "_strip_contradicting_sentences"):
            with self.subTest(fn=gone):
                self.assertFalse(hasattr(ce, gone))
        for gone in ("_compose_secondary_answers", "_apply_required_next_question",
                     "_enforce_canonical_vehicle_claim"):
            with self.subTest(method=gone):
                self.assertFalse(hasattr(ConversationEngine, gone))

    def test_bc36_31_the_outbound_validator_was_not_touched(self):
        """G36-04 is owned by G3-7. This correction may not modify it."""
        self.assertTrue(hasattr(ConversationEngine, "_validate_outbound_text"))
        import app.services.response_validator as rv
        self.assertTrue(hasattr(rv, "validate_response"))

    def test_bc36_32_p1_and_the_composition_order_are_unchanged(self):
        send = inspect.getsource(ConversationEngine._send_text_to_wa)
        register = send.index("self._register_response_propositions(")
        compose = send.index("plan.compose(text)")
        gate = send.index("gate.attempt(")
        self.assertLess(register, compose)
        self.assertLess(compose, gate)
        self.assertNotIn("text +=", send[compose:gate])

    def test_bc36_32b_nothing_assigns_the_text_after_it_is_composed(self):
        """The G3-6 suite scanned for a few concatenation spellings; an adversarial
        mutation that appends ON the compose line itself slipped through all of them.

        This reads the statements instead. The composition statement must be exactly
        `text = plan.compose(text)`, and no statement after it — up to the gate — may
        rebind `text`. That is the property, and it does not depend on guessing spellings.
        """
        import ast, textwrap
        src = textwrap.dedent(inspect.getsource(ConversationEngine._send_text_to_wa))
        fn = ast.parse(src).body[0]
        composed_at = None
        for node in ast.walk(fn):
            if not isinstance(node, ast.Assign):
                continue
            rendered = ast.unparse(node)
            if "plan.compose(" in rendered:
                self.assertEqual(rendered, "text = plan.compose(text)",
                                 "the composition statement does more than compose")
                composed_at = node.lineno
        self.assertIsNotNone(composed_at, "no composition statement found")
        gate_at = min(n.lineno for n in ast.walk(fn)
                      if isinstance(n, ast.Call) and "gate.attempt" in ast.unparse(n))
        rebinds = [ast.unparse(n) for n in ast.walk(fn)
                   if isinstance(n, (ast.Assign, ast.AugAssign))
                   and composed_at < n.lineno < gate_at
                   and any(isinstance(t, ast.Name) and t.id == "text"
                           for t in ([n.target] if isinstance(n, ast.AugAssign)
                                     else n.targets))]
        self.assertEqual(rebinds, [], f"text is reassigned after composition: {rebinds}")

    def test_bc36_33_scheduling_meaning_is_unchanged(self):
        for texts in ([HOURS_Q, "¿Hay algo mañana?"], [HOURS_Q, "¿Y el martes?"]):
            t = Turn(tuple((f"w{i}", x, i * 5) for i, x in enumerate(texts)),
                     evidence=None).run()
            try:
                with self.subTest(burst=texts[-1]):
                    self.assertEqual(t.decision.hours_disposition,
                                     "SUPPRESSED_BY_DETERMINISTIC_SCHEDULING")
                    self.assertFalse(t.names_the_weekday_table())
            finally:
                t.close()

    def test_bc36_34_a_real_turn_still_composes_one_message(self):
        """At least one test passes through the real registration and composition path."""
        t = Turn(((("w0", "¿Cuál es el horario de atención?", 0),)), evidence=None).run()
        try:
            self.assertEqual(len(t.sent), 1)
            payload = t.engine._turn_response_plan.to_payload()
            self.assertEqual(payload["item_kinds"],
                             [ResponseItemKind.TEXT, ResponseItemKind.FAQ_ANSWER])
            self.assertNotIn(PropositionId.CANONICAL_PRICE,
                             [i["proposition_id"] for i in payload["items"]],
                             "a price was registered with no canonical quote")
        finally:
            t.close()

    def test_bc36_35_the_flow_completion_acknowledgement_is_intact(self):
        self.assertTrue(hasattr(ConversationEngine, "_flow_transaction_key"))
        self.assertEqual(ConversationEngine._flow_transaction_key(101), "flow-txn:101")

    def test_bc36_36_no_model_prompt_or_schema_moved(self):
        engine_src = pathlib.Path(ce.__file__).read_text(encoding="utf-8")
        interp = (ROOT / "backend/app/services/semantic_interpreter.py").read_text(
            encoding="utf-8")
        self.assertIn('REPLY_PROMPT_VERSION = "reply/1.0"', engine_src)
        self.assertIn('PROMPT_VERSION = "understand/1.19"', interp)
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.5")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
