"""WILD1-C — focused tests: price composition, claim coverage, quote decline, Flow copy.

Full-engine tests run the real `/api/conversation/handle` route on synthetic SQLite with the
live H-1 posture (`_wild1c_harness`). The interpreter, bridge generator and verifier are
scripted; nothing reaches a provider, Meta or a network.
"""
from __future__ import annotations

import re
import unittest
from unittest import mock

import _wild1c_harness as W

from sqlalchemy.orm import Session  # noqa: E402

from app.services import claim_coverage as COV  # noqa: E402
from app.services import response_validator as RV  # noqa: E402
from app.services.pricing import PricingQuote  # noqa: E402

CE = W.CE
SCOPE, REPORT, PAYMENT = CE._FAQ_SCOPE_LEAD, CE._FAQ_REPORT_ANSWER, CE._FAQ_PAYMENT_ANSWER
RENEGADE = ("Jeep", "Renegade", 2024, "SUV_4X4_DEPORTIVO")
COROLLA = ("Toyota", "Corolla", 2018, "AUTO")
BERAZATEGUI = ("Sur", "Berazategui")
PALERMO = ("CABA", "Palermo")                       # viáticos 0 in the zone fixture
BLOCK_240 = W.block(150_000, 90_000, 240_000)
BLOCK_140 = W.block(140_000, 0, 140_000)


def quote(base, viaticos):
    return PricingQuote(tipo_vehiculo="AUTO", zone_group="CABA", zone_detail="Palermo",
                        precio_base=base, viaticos=viaticos)


def facts_for(base, viaticos, total=None):
    return RV.CanonicalFacts(vehicle_marca="Toyota", vehicle_modelo="Corolla",
                             inspection_zone_detail="Palermo", inspection_zone_group="CABA",
                             quote_total=(base + viaticos if total is None else total),
                             quote_base=base, quote_viaticos=viaticos)


def quoted_world(eng, *, vehicle=RENEGADE, zone=BERAZATEGUI, block=BLOCK_240, flag=None,
                 motivo=None):
    """A thread whose quote was DELIVERED (the outbound ledger carries it)."""
    t = W.new_thread(eng, stage="QUOTED", flag=flag or "PRESUPUESTO_ENVIADO",
                     vehicle=vehicle, zone=zone, motivo=motivo)
    W.add_outbound(eng, t, f"Genial! Te paso la cotización para la revisión:\n\n{block}\n\n"
                           "Si te parece bien, podemos avanzar.")
    return t


def text_of(r):
    return "\n".join(list(r.sent) + [f for f in r.flows if f])


# ══ 5–8. price composition ═══════════════════════════════════════════════════

class PriceBreakdownUnit(unittest.TestCase):
    def test_nonzero_viaticos_block(self):
        self.assertEqual(RV.render_price_breakdown(150_000, 90_000, 240_000),
                         "Revisión: $150.000\nViáticos: $90.000\nTotal: $240.000")

    def test_zero_viaticos_is_shown(self):
        self.assertEqual(RV.render_price_breakdown(140_000, 0, 140_000),
                         "Revisión: $140.000\nViáticos: $0\nTotal: $140.000")

    def test_arithmetic_must_reconcile(self):
        for bad in ((150_000, 90_000, 230_000), (150_000, 90_000, 250_000),
                    (150_000, -1, 149_999), (150_000.0, 0, 150_000), (True, 0, 1),
                    ("150000", 0, 150_000), (None, 0, 0)):
            with self.subTest(bad=bad), self.assertRaises(RV.PriceBreakdownError):
                RV.render_price_breakdown(*bad)

    def test_quote_copy_is_built_from_the_structured_quote_only(self):
        text = CE.ConversationEngine._build_quote_reply("Toyota", "Corolla", "Palermo",
                                                        quote(140_000, 0), 2018)
        self.assertIn(BLOCK_140, text)
        self.assertIn("del Toyota Corolla 2018 en Palermo", text)
        with self.assertRaises(TypeError):        # a bare total is not a quote any more
            CE.ConversationEngine._build_quote_reply(None, None, None, 140_000)

    def test_pricing_service_quote_reconciles(self):
        q = quote(150_000, 90_000)
        self.assertEqual(q.precio_base + q.viaticos, q.precio_total)
        self.assertEqual(CE._quote_breakdown(q), (150_000, 90_000, 240_000))


class PriceClaimValidation(unittest.TestCase):
    """The validator owns the equality and the rendering, independently of the composer."""

    def envelope(self, text, value, facts):
        return RV.validate_envelope(RV.composed_text(text, claims=[
            RV.StructuredClaim(RV.DomainClaim.PRICE, value)]), facts)

    def test_declared_breakdown_passes_and_keeps_its_lines(self):
        env = self.envelope(f"Te paso la cotización:\n\n{BLOCK_140}\n\nSi te parece bien.",
                            (140_000, 0, 140_000), facts_for(140_000, 0))
        self.assertFalse(env.fell_back, env.findings)
        self.assertIn(BLOCK_140, env.text)          # newlines preserved, not flattened

    def test_total_only_declaration_is_refused(self):
        env = self.envelope("La cotización es de $140.000.", (140_000,), facts_for(140_000, 0))
        self.assertTrue(env.fell_back)
        # refused by the structured contract itself, not only by a later layer
        self.assertTrue(any("must declare the breakdown" in f.detail for f in env.findings),
                        env.findings)

    def test_mismatched_component_is_refused(self):
        for value in ((130_000, 10_000, 140_000), (140_000, 0, 150_000),
                      (0, 140_000, 140_000)):
            with self.subTest(value=value):
                text = f"x:\n\n{W.block(*value) if sum(value[:2]) == value[2] else ''}"
                self.assertTrue(self.envelope(text, value, facts_for(140_000, 0)).fell_back)

    def test_declared_but_not_rendered_is_refused(self):
        env = self.envelope("La cotización total es de $140.000.", (140_000, 0, 140_000),
                            facts_for(140_000, 0))
        self.assertTrue(env.fell_back)
        self.assertTrue(any("not rendered" in f.detail for f in env.findings))

    def test_unedited_text_is_returned_as_written(self):
        txt = "Hola.\nTodo bien."
        self.assertEqual(RV.validate_response(txt, RV.CanonicalFacts()).text, txt)


class QuoteBreakdownFullEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()

    def first_quote(self, vehicle, zone, texts):
        t = W.new_thread(self.eng, vehicle=vehicle, zone=None)
        interp = W.Interpreter([(texts[-1][:12], W.payload(
            locations=((zone[1], "INSPECTION_LOCATION"),)))])
        return t, W.run(self.eng, W.burst(self.eng, t, texts), interp=interp)

    def test_first_quote_nonzero_viaticos(self):
        t, r = self.first_quote(RENEGADE, BERAZATEGUI, ["el auto está en Berazategui"])
        self.assertIn(BLOCK_240, text_of(r))
        self.assertEqual(W.snap(self.eng, t)["flag"], "PRESUPUESTO_ENVIADO")

    def test_first_quote_zero_viaticos(self):
        t, r = self.first_quote(COROLLA, PALERMO, ["está en Palermo"])
        self.assertIn(BLOCK_140, text_of(r))
        self.assertIn("Viáticos: $0", text_of(r))

    def test_repeated_quote_retains_breakdown(self):
        t = quoted_world(self.eng)
        interp = W.Interpreter([("cuánto", W.payload(quote=True))])
        for ask in (["¿cuánto salía al final?"], ["perdón, ¿me repetís cuánto era?"]):
            r = W.run(self.eng, W.burst(self.eng, t, ask), interp=interp)
            self.assertIn(BLOCK_240, text_of(r), ask)
            self.assertNotRegex(text_of(r), r"es de \$240\.000")


# ══ 2, 3, 13, 14, 15. coverage ════════════════════════════════════════════════

class CoverageFullEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()

    def test_multi_question_first_message_scope_and_payment(self):
        t = W.new_thread(self.eng)
        interp = W.Interpreter([("incluye la", W.payload(faq=("service_scope", "payment"),
                                                          inspection=True))])
        r = W.run(self.eng, W.burst(self.eng, t, [
            "Buenas! ¿Qué incluye la revisión y cómo se paga?"]), interp=interp)
        for answer in (SCOPE, REPORT, PAYMENT):
            self.assertIn(answer, text_of(r))
        self.assertTrue(r.coverage.complete, r.coverage.summary())

    def test_reordered_and_paraphrased_questions(self):
        cases = [
            ("che y si pago con la tarjeta de débito anda? y qué es lo que miran del auto",
             ("payment", "service_scope")),
            ("¿qué me entregan después? digo, el informe qué trae",  ("report",)),
            ("hola queria saber que revisan exactamente y si se puede abonar en efectivo",
             ("service_scope", "payment")),            # voice-note style, no punctuation
        ]
        for words, topics in cases:
            with self.subTest(words=words):
                t = W.new_thread(self.eng)
                interp = W.Interpreter([(words[:15], W.payload(faq=topics))])
                r = W.run(self.eng, W.burst(self.eng, t, [words], message_type="audio"),
                          interp=interp)
                want = {COV.TOPIC_CLAIMS[x] for x in topics}
                self.assertTrue(want <= r.coverage.required, r.coverage.summary())
                self.assertTrue(r.coverage.complete, r.coverage.summary())

    def test_paraphrased_viaticos_reaches_breakdown_semantically(self):
        # No floor lexeme in the words: only the interpreter's QUOTE_REQUEST carries it.
        t = quoted_world(self.eng)
        words = "y el traslado hasta allá lo cobran aparte?"
        self.assertFalse(CE.ConversationEngine._detect_price_question(None, words))
        interp = W.Interpreter([("traslado", W.payload(quote=True))])
        r = W.run(self.eng, W.burst(self.eng, t, [words]), interp=interp)
        self.assertIn(BLOCK_240, text_of(r))

    def test_scope_asked_three_times_is_answered_each_time(self):
        t = quoted_world(self.eng)
        interp = W.Interpreter([("consta", W.payload(faq=("service_scope",)))])
        for n in range(3):
            r = W.run(self.eng, W.burst(self.eng, t, [f"de qué consta la revisión? ({n})"]),
                      interp=interp)
            self.assertIn(SCOPE, text_of(r), n)

    def test_bridge_omitting_or_mentioning_a_claim_never_covers_it(self):
        """14. The generator's bridge mentions the report without answering it; the
        canonical report answer is still delivered (coverage is by canonical identity)."""
        t = quoted_world(self.eng)                      # Layer D: a bridge-carrying route
        bridges = W.Bridges(["¡Hola! Te cuento lo del informe."])
        interp = W.Interpreter([("informe", W.payload(faq=("report",)))])
        r = W.run(self.eng, W.burst(self.eng, t, ["¿Qué trae el informe?"]), interp=interp,
                  bridges=bridges, reply_body="Claro, te cuento.")
        t_ = text_of(r)
        self.assertIn(REPORT, t_)
        self.assertTrue(t_.startswith("¡Hola! Te cuento lo del informe."), t_)  # natural
        self.assertTrue(r.coverage.complete)

    def test_bridge_that_looks_like_an_answer_does_not_suppress_it(self):
        """The FAQ redundancy test reads the CANONICAL segment only: a bridge whose words
        happen to match an answer's shape (presence) cannot withhold the canonical answer."""
        t = quoted_world(self.eng)
        bridge = "¡Claro! No hace falta que estés ahí, igual te cuento."
        interp = W.Interpreter([("presente", W.payload(faq=("presence",)))])
        r = W.run(self.eng, W.burst(self.eng, t, ["¿tengo que estar presente?"]), interp=interp,
                  bridges=W.Bridges([bridge]), reply_body="Claro.")
        self.assertIn(CE._FAQ_PRESENCE_ANSWER, text_of(r))

    def test_layer_d_answers_an_untracked_faq(self):
        """F1: Layer D arms the FAQ burst — an hours/presence question asked on a qualifying
        thread is answered canonically (before R6-C4B-era arming it was dropped)."""
        t = quoted_world(self.eng)
        interp = W.Interpreter([("presente", W.payload(faq=("presence",)))])
        r = W.run(self.eng, W.burst(self.eng, t, ["¿hay que estar presente?"]), interp=interp,
                  reply_body="Claro.")
        self.assertIn(CE._FAQ_PRESENCE_ANSWER, text_of(r))

    def test_bridge_with_a_false_payment_claim_is_rejected(self):
        t = quoted_world(self.eng)
        lie = "¡Claro! Aceptamos débito sin problema."
        bridges = W.Bridges([lie, lie], verdicts={lie: json_verdict("PAYMENT", "débito")})
        interp = W.Interpreter([("débito", W.payload(faq=("payment",)))])
        r = W.run(self.eng, W.burst(self.eng, t, ["¿Aceptan débito?"]), interp=interp,
                  bridges=bridges, reply_body="Claro, te cuento.")
        self.assertNotIn(lie, text_of(r))
        self.assertIn(PAYMENT, text_of(r))

    def test_unanswered_question_is_owed_on_the_next_turn(self):
        """A claim the final message could not carry is persisted as owed, and the NEXT
        reply carries it (cross-turn ledger)."""
        t = quoted_world(self.eng)
        interp = W.Interpreter([("informe", W.payload(faq=("report",), day="TOMORROW",
                                                       availability=True)),
                                ("dale", W.payload(accept="ACCEPT"))])
        with mock.patch.object(CE, "_FLOW_BODY_MAX", 10):    # the preamble cannot fit
            r1 = W.run(self.eng, W.burst(self.eng, t, [
                "¿para mañana tenés? y el informe qué trae"]), interp=interp)
        self.assertEqual(len(r1.flows), 1)
        self.assertNotIn(REPORT, r1.flows[0])
        self.assertIn("report", W.snap(self.eng, t)["owed"])
        r2 = W.run(self.eng, W.burst(self.eng, t, ["dale"]), interp=interp)
        self.assertIn(REPORT, text_of(r2))

    def test_coverage_unit(self):
        canon = {COV.RequiredClaim.PAYMENT: PAYMENT}
        rep = COV.coverage({COV.RequiredClaim.PAYMENT}, "Hola. " + PAYMENT, canon)
        self.assertTrue(rep.complete)
        rep = COV.coverage({COV.RequiredClaim.PAYMENT}, "Aceptamos efectivo.", canon)
        self.assertEqual(rep.missing, {COV.RequiredClaim.PAYMENT})
        led = COV.Ledger(asks=[COV.Ask(COV.RequiredClaim.PAYMENT, 5)],
                         outbound=[(4, PAYMENT), (6, "otra cosa")])
        self.assertEqual(led.outstanding(canon), {COV.RequiredClaim.PAYMENT})  # 4 < 5
        led.outbound.append((7, PAYMENT))
        self.assertEqual(led.outstanding(canon), frozenset())


def json_verdict(category, span):
    import json
    return json.dumps({"purely_conversational": False, "contains_operational_claim": True,
                       "findings": [{"category": category, "span": span}], "uncertain": []})


# ══ 4, 9–12. quote decline and reconsideration ═══════════════════════════════

class QuoteDecline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()

    def decline(self, words, payload):
        t = quoted_world(self.eng)
        interp = W.Interpreter([(words[:10], payload)])
        r = W.run(self.eng, W.burst(self.eng, t, [words]), interp=interp)
        return t, r, W.snap(self.eng, t)

    # ambiguous concern vs explicit refusal (owner ruling) ─────────────────────
    def test_price_concern_with_question_is_not_a_decline(self):
        t, r, s = self.decline("me parece caro, ¿cuánto es el viático?",
                               W.payload(quote=True, accept="HESITATE"))
        self.assertEqual((s["flag"], s["motivo"], s["decisions"]),
                         ("PRESUPUESTO_ENVIADO", None, []))
        self.assertIn(BLOCK_240, text_of(r))

    def test_breakdown_request_is_not_a_decline(self):
        t, r, s = self.decline("¿me pasás el detalle de cuánto es cada cosa?",
                               W.payload(quote=True, accept="QUESTION_ONLY"))
        self.assertEqual(s["flag"], "PRESUPUESTO_ENVIADO")
        self.assertEqual(s["decisions"], [])

    def test_concern_without_refusal_is_not_a_decline(self):
        t, r, s = self.decline("uff está caro eh", W.payload(accept="HESITATE"))
        self.assertEqual(s["flag"], "PRESUPUESTO_ENVIADO")
        self.assertEqual(s["decisions"], [])

    def test_ambiguous_reject_is_not_a_decline(self):
        p = W.payload(accept="REJECT", decline_reason="PRICE")
        p["acceptance"]["status"] = "AMBIGUOUS"
        t, r, s = self.decline("no sé, capaz que no", p)
        self.assertEqual(s["flag"], "PRESUPUESTO_ENVIADO")

    def test_reject_beside_a_day_request_is_not_a_decline(self):
        t, r, s = self.decline("no, el lunes no, ¿el martes?",
                               W.payload(accept="REJECT", day="TUESDAY"))
        self.assertNotEqual(s["flag"], "PERDIDO")

    def test_no_semantic_reading_never_declines(self):
        t = quoted_world(self.eng)
        cfg = W.cfg(semantic_same_turn_enabled=False)
        r = W.run(self.eng, W.burst(self.eng, t, ["no gracias, es muy caro, lo dejo"]),
                  interp=W.Interpreter([]), config=cfg)
        self.assertEqual(W.snap(self.eng, t)["flag"], "PRESUPUESTO_ENVIADO")

    # explicit refusal ─────────────────────────────────────────────────────────
    def test_explicit_price_refusal_marks_perdido_precio(self):
        t, r, s = self.decline("no gracias, es muy caro, no la voy a hacer",
                               W.payload(accept="REJECT", decline_reason="PRICE"))
        self.assertEqual((s["flag"], s["motivo"]), ("PERDIDO", "PRECIO"))
        self.assertEqual(s["decisions"], [("DECLINED", "PRECIO", "ENGINE")])
        self.assertIn(CE._QUOTE_DECLINE_ACK, text_of(r))
        # 10: not a complaint; 12: no Flow, no offer, no booking, no notification
        self.assertFalse(s["needs_human"]); self.assertFalse(s["necesita_humano"])
        self.assertEqual((r.flows, s["offers"], s["booked"], s["ledger"], s["token"]),
                         ([], 0, 0, 0, None))
        self.assertEqual(s["stage"], "QUOTED")
        self.assertNotRegex(text_of(r).lower(), r"descuento|promo|reserv|avanzar")

    def test_refusal_beside_an_faq_question_is_still_recorded(self):
        """Layer D must not swallow an explicit decline that also asks an FAQ."""
        # "¿de qué consta…?" is the general-information shape Layer D intercepts (H-1 6270).
        t, r, s = self.decline("no, gracias, la dejo. ¿igual de qué consta la revisión?",
                               W.payload(accept="REJECT", decline_reason="PRICE",
                                         faq=("service_scope",)))
        self.assertEqual((s["flag"], s["motivo"]), ("PERDIDO", "PRECIO"))
        self.assertIn(SCOPE, text_of(r))

    def test_explicit_other_refusal_marks_perdido_otro(self):
        t, r, s = self.decline("no, ya conseguí otro auto, gracias",
                               W.payload(accept="REJECT", decline_reason="OTHER"))
        self.assertEqual((s["flag"], s["motivo"]), ("PERDIDO", "OTRO"))

    def test_refusal_with_viaticos_question_answers_it(self):
        """4. Viáticos asked together with the rejection: the breakdown is given, no CTA."""
        t, r, s = self.decline("no la hago, es caro. ¿cuánto era el viático igual?",
                               W.payload(accept="REJECT", decline_reason="PRICE", quote=True))
        self.assertEqual(s["flag"], "PERDIDO")
        out = text_of(r)
        self.assertIn(BLOCK_240, out)
        self.assertIn(CE._QUOTE_DECLINE_ACK, out)
        self.assertNotIn("podemos avanzar", out)

    def test_decline_stops_the_quote_followup(self):
        """The 24 h quote follow-up selects ONLY flag = PRESUPUESTO_ENVIADO (its SQL is
        PostgreSQL-only, so it is read, not run); a decline leaves the lead PERDIDO."""
        from app.services import quote_followup as QF
        src = open(QF.__file__, encoding="utf-8").read()
        self.assertEqual(re.findall(r"l\.flag\s*=\s*'(\w+)'", src), ["PRESUPUESTO_ENVIADO"])
        t, r, s = self.decline("no gracias, lo dejo", W.payload(accept="REJECT"))
        self.assertEqual(s["flag"], "PERDIDO")

    def test_retry_of_the_same_inbound_records_once(self):
        t = quoted_world(self.eng)
        interp = W.Interpreter([("lo dejo", W.payload(accept="REJECT", decline_reason="PRICE"))])
        ev = W.burst(self.eng, t, ["no gracias, lo dejo"])
        W.run(self.eng, ev, interp=interp)
        W.run(self.eng, ev, interp=interp)
        self.assertEqual(len(W.snap(self.eng, t)["decisions"]), 1)

    # reconsideration ──────────────────────────────────────────────────────────
    def declined(self):
        t, r, s = self.decline("no gracias, es caro", W.payload(accept="REJECT",
                                                                decline_reason="PRICE"))
        self.assertEqual(s["flag"], "PERDIDO")
        return t

    def test_explicit_acceptance_reactivates_engine_decline(self):
        t = self.declined()
        interp = W.Interpreter([("lo pensé", W.payload(accept="ACCEPT"))])
        W.run(self.eng, W.burst(self.eng, t, ["bueno, lo pensé mejor, dale avancemos"]),
              interp=interp)
        s = W.snap(self.eng, t)
        self.assertEqual((s["flag"], s["motivo"], s["stage"]), ("ACEPTADO", None, "SCHEDULING"))
        self.assertEqual(s["decisions"], [("DECLINED", "PRECIO", "ENGINE"),
                                          ("RECONSIDERED", "PRECIO", "ENGINE")])

    def test_day_request_after_decline_reactivates_and_opens_the_flow(self):
        t = self.declined()
        interp = W.Interpreter([("están en precio", W.payload(day="TOMORROW",
                                                               availability=True))])
        r = W.run(self.eng, W.burst(self.eng, t, [
            "estuve averiguando y están en precio, ¿para mañana tenés?"]), interp=interp)
        s = W.snap(self.eng, t)
        self.assertEqual(len(r.flows), 1)
        self.assertEqual(s["flag"], "ACEPTADO")
        self.assertEqual([d[0] for d in s["decisions"]], ["DECLINED", "RECONSIDERED"])
        self.assertEqual(s["booked"], 0)

    def test_operator_perdido_is_never_reactivated(self):
        t = quoted_world(self.eng, flag="PERDIDO", motivo="PRECIO")   # operator wrote it
        interp = W.Interpreter([("dale", W.payload(accept="ACCEPT")),
                                ("mañana", W.payload(day="TOMORROW", availability=True))])
        for words in (["dale, avancemos"], ["¿para mañana tenés?"]):
            r = W.run(self.eng, W.burst(self.eng, t, words), interp=interp)
            s = W.snap(self.eng, t)
            self.assertEqual((s["flag"], s["motivo"]), ("PERDIDO", "PRECIO"), words)
            self.assertEqual((r.flows, s["decisions"], s["token"]), ([], [], None), words)

    def test_operator_changed_motivo_is_not_reversed(self):
        t = self.declined()
        with Session(self.eng) as db:
            from app.models import Lead
            db.get(Lead, t.lead_id).motivo_perdida = "OTRO"      # operator re-marked it
            db.commit()
        interp = W.Interpreter([("avancemos", W.payload(accept="ACCEPT"))])
        W.run(self.eng, W.burst(self.eng, t, ["dale avancemos"]), interp=interp)
        s = W.snap(self.eng, t)
        self.assertEqual((s["flag"], s["motivo"]), ("PERDIDO", "OTRO"))
        self.assertEqual([d[0] for d in s["decisions"]], ["DECLINED"])

    # operator visibility ───────────────────────────────────────────────────────
    def test_kanban_card_shows_state_and_history(self):
        t = self.declined()
        interp = W.Interpreter([("avancemos", W.payload(accept="ACCEPT"))])
        W.run(self.eng, W.burst(self.eng, t, ["dale avancemos"]), interp=interp)
        from app.models import Lead
        from app.services.quote_decision_history import lead_quote_decisions
        from app.ui.kanban_view import render_lead_card
        with Session(self.eng) as db:
            hist = lead_quote_decisions(db, [t.lead_id])[t.lead_id]
            html = render_lead_card(db.get(Lead, t.lead_id), quote_history=hist)
        self.assertIn("Presupuesto rechazado por el cliente (PRECIO, automático)", html)
        self.assertIn("Presupuesto retomado por el cliente", html)

    def test_kanban_card_perdido_motivo_visible(self):
        t = self.declined()
        from app.models import Lead
        from app.services.quote_decision_history import lead_quote_decisions
        from app.ui.kanban_view import render_lead_card
        with Session(self.eng) as db:
            lead = db.get(Lead, t.lead_id)
            html = render_lead_card(lead, quote_history=lead_quote_decisions(
                db, [t.lead_id]).get(t.lead_id))
        self.assertIn("flag-PERDIDO", html)
        self.assertIn("Motivo pérdida: PRECIO", html)


# ══ WILD1-C-R1. report timing (owner ruling 2026-11-16) ═══════════════════════

class ReportTimingRuling(unittest.TestCase):
    OWNER = ("Al terminar la revisión, el inspector te comenta los principales hallazgos. "
             "El informe detallado se envía durante el mismo día, con los datos del vehículo, "
             "los hallazgos por sistema, fotos, una conclusión sobre el estado general y "
             "recomendaciones técnicas.")

    def test_report_answer_is_the_owner_wording(self):
        self.assertEqual(REPORT, self.OWNER)

    def test_no_other_timing_is_promised(self):
        low = REPORT.lower()
        for forbidden in ("24", "48", "hora", "inmediat", "en el momento", "pdf"):
            self.assertNotIn(forbidden, low, forbidden)
        self.assertIn("durante el mismo día", low)

    def test_scope_facts_unchanged_and_carry_the_report(self):
        self.assertEqual(SCOPE, (
            "Vamos hasta donde está el vehículo y hacemos la revisión pre-compra en el lugar: "
            "inspección mecánica, eléctrica, de estructura y solidez, prueba de marcha, escaneo "
            "electrónico profesional y verificación del kilometraje real."))
        self.assertEqual(CE._FAQ_SCOPE_ANSWER, f"{SCOPE} {REPORT}")


# ══ 16. Booking Flow intro copy ═══════════════════════════════════════════════

class FlowIntroCopy(unittest.TestCase):
    def test_body_without_a_verified_time_never_says_ese_horario(self):
        self.assertNotIn("Ese horario", CE._booking_flow_body(time_verified=False))
        self.assertTrue(CE._booking_flow_body(time_verified=True)
                        .startswith("Ese horario está disponible"))

    def test_specific_verified_time_keeps_the_line(self):
        """The line survives exactly where it is true: the customer named a time and
        ScheduleService confirmed THAT time (stubbed verdict: valid)."""
        from types import SimpleNamespace
        eng = W.engine()
        t = quoted_world(eng)
        interp = W.Interpreter([("a las 10", W.payload(accept="ACCEPT", day="TOMORROW"))])
        valid = SimpleNamespace(valid=True, reason=None, suggested_slots=[], slots=W.SLOTS)
        r = W.run(eng, W.burst(eng, t, ["dale, mañana a las 10"]), interp=interp,
                  hook=lambda ce: setattr(ce._schedule, "check", lambda _in: valid))
        self.assertEqual(len(r.flows), 1, r.sent)
        self.assertIn("Ese horario está disponible", r.flows[0])
        # WILD1-C-R2: the verified-time body, exactly
        self.assertTrue(r.flows[0].endswith(
            "Ese horario está disponible 🎉 Completá tus datos para solicitar el turno."),
            r.flows[0])
        self.assertIsNone(RV._check_booking(r.flows[0], RV.CanonicalFacts()), r.flows[0])

    # ── WILD1-C-R2: no pre-persistence Flow copy confirms the appointment ────────
    FORBIDDEN = ("confirmad", "confirmar el turno", "confirmá el turno", "confirmá ",
                 "confirmamos", "quedó agendado", "te esperamos", "reservado")

    def test_flow_body_variants_request_never_confirm(self):
        self.assertEqual(CE._booking_flow_body(time_verified=False),
                         "Elegí el horario que te sirva y completá tus datos para solicitar "
                         "el turno.")
        self.assertEqual(CE._booking_flow_body(time_verified=True),
                         "Ese horario está disponible 🎉 Completá tus datos para solicitar el turno.")
        for text in (CE._booking_flow_body(time_verified=False),
                     CE._booking_flow_body(time_verified=True),
                     CE._TX_FLOW_CTA_TEXT, CE._TX_FLOW_PENDING_TEXT):
            low = text.lower()
            for bad in self.FORBIDDEN + tuple(CE._SCHEDULING_FORBIDDEN_PHRASES):
                self.assertNotIn(bad, low, (bad, text))
            self.assertIsNone(RV._check_booking(text, RV.CanonicalFacts()), text)

    def test_source_guard_pre_persistence_flow_copy(self):
        """Every string literal the pre-persistence Flow paths can send (docstrings and
        log calls excluded) is free of confirmation wording."""
        import ast
        src = open(CE.__file__, encoding="utf-8").read()
        tree = ast.parse(src)
        names = {"_booking_flow_body", "_send_booking_flow", "_dispatch_booking_flow_for_day",
                 "_try_schedule_and_flow"}
        found = set()
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                   and n.name in names):
            found.add(fn.name)
            doc = ast.get_docstring(fn, clean=False)
            logs = {id(c) for call in ast.walk(fn) if isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr in ("info", "warning", "error", "debug", "exception")
                    for c in ast.walk(call)}
            for node in ast.walk(fn):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and node.value != doc and id(node) not in logs):
                    low = node.value.lower()
                    for bad in self.FORBIDDEN:
                        self.assertNotIn(bad, low, (fn.name, node.value))
        self.assertEqual(found, names)
        self.assertIn('cta_label="Reservar turno"', src)      # the Flow button is unchanged

    def test_h1_flow_first_body_is_the_required_sentence(self):
        eng = W.engine()
        t = quoted_world(eng)
        interp = W.Interpreter([("mañana", W.payload(day="TOMORROW", availability=True))])
        r = W.run(eng, W.burst(eng, t, ["¿para mañana tenés horarios?"]), interp=interp)
        self.assertEqual(len(r.flows), 1, r.sent)
        body = r.flows[0]
        self.assertRegex(body, r"Para mañana \w+ \d{2}/\d{2} tengo 5 horarios disponibles\. "
                               r"Elegí el horario que te sirva y completá tus datos para "
                               r"solicitar el turno\.$")
        self.assertNotIn("Ese horario", body)
        self.assertIsNone(RV._check_booking(body, RV.CanonicalFacts()), body)

    def test_no_other_ese_horario_literal_left_unconditioned(self):
        src = open(CE.__file__, encoding="utf-8").read()
        for m in re.finditer(r"Ese horario está disponible", src):
            line = src[src.rfind("\n", 0, m.start()) + 1: src.find("\n", m.start())]
            self.assertTrue(("if" in line) or ("lead =" in line) or line.strip().startswith(
                ('"""', '#', 'may the body', 'WILD1-C')) or "\"Ese horario" in line, line)


if __name__ == "__main__":
    unittest.main()
