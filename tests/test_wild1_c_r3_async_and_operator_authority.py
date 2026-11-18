"""WILD1-C-R3 — asynchronous question coverage, operator authority, website-form quote.

Focused tests for the R3 correction of the WILD1-C focused independent review:

* WC-01 — under the LIVE posture (SHADOW_UNDERSTAND_ASYNC=true, 6.0 s same-turn timeout) an
  explicit question on the general-information route is never silently lost: the reading is
  waited for within a bounded budget, and when it does not arrive the question is
  acknowledged, durably retained (`pending_question`) and answered on the next turn.
* WC-02 — an operator-written PERDIDO (any operator intervention after an engine decline)
  is never automatically reactivated; provenance is written by the REAL operator actions in
  the same transaction as the mutation.
* WC-03 — the website-form quote carries one canonical breakdown and one CTA.
* WC-04 — tests that kill the review's surviving mutants MI-3b, MI-4, MI-4b, MI-5b, MI-7.

Everything runs the real `/api/conversation/handle` route (`_wild1c_harness`); only the
model seams are scripted. Operator actions are the real `app.ui.kanban_actions` / API
functions on a real Session. No provider, Meta or network is reached.
"""
from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

import _wild1c_harness as W
import test_wild1_c_conversation_correction as T

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Lead, WhatsAppMessage, WhatsAppThreadEvidence
from app.services import claim_coverage as COV
from app.services.shadow_worker import get_worker

CE = W.CE
SCOPE, REPORT, PAYMENT = CE._FAQ_SCOPE_LEAD, CE._FAQ_REPORT_ANSWER, CE._FAQ_PAYMENT_ANSWER
ACK = CE._PENDING_QUESTION_ACK
GENERIC_CLOSING = "Gracias por tu mensaje"

#: the five inputs of the independent review's L-2 probe (q1–q5), with the topics the
#: interpreter labels for each.
INPUTS = [
    ("q1", "¿Qué revisan?", ("service_scope",)),
    ("q2", "¿Qué incluye la revisión?", ("service_scope",)),
    ("q3", "hola queria saber que es lo que revisan cuando van a ver un auto", ("service_scope",)),
    ("q4", "¿Qué revisan y qué me mandan en el informe?", ("service_scope", "report")),
    ("q5", "Hola, buenas tardes! ¿Qué revisan?", ("service_scope",)),
]
#: interpreter behaviour on the FIRST reading of a burst (the re-reading is always normal)
MODES = {
    "immediate": dict(delay=0.0, raises=False),
    "after_3_5s": dict(delay=3.5, raises=False),
    "timeout": dict(delay=W.LIVE_TIMEOUT_SECONDS + 1.0, raises=False),   # > 6.0 s and > 4.5 s
    "unavailable": dict(delay=0.0, raises=True),
}


class FirstReadingInterpreter(W.Interpreter):
    """The scripted interpreter, with a delay or a failure on the FIRST call for a burst
    containing `slow_key` (thread-safe: it is called from the worker and from the pending
    re-reading thread)."""

    def __init__(self, script, *, slow_key, delay=0.0, raises=False):
        super().__init__(script)
        self.slow_key, self.delay, self.raises = slow_key, delay, raises
        self._lock = threading.Lock()
        self._slowed = False

    def __call__(self, messages, model):
        burst = messages[-1]["content"].split("\n\nCONTEXTO")[0]
        first = False
        with self._lock:
            if self.slow_key in burst and not self._slowed:
                self._slowed = first = True
        if first and self.delay:
            time.sleep(self.delay)
        if first and self.raises:
            raise RuntimeError("provider unavailable (scripted)")
        return super().__call__(messages, model)


def text_of(r):
    return "\n".join(list(r.sent) + [f for f in r.flows if f])


def outbound_texts(eng, t):
    with Session(eng) as db:
        return [m.text or "" for m in db.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.thread_id == t.tid,
                                          WhatsAppMessage.direction == "out")
            .order_by(WhatsAppMessage.id)).scalars()]


def evidence(eng, t, claim_type):
    with Session(eng) as db:
        return list(db.execute(select(WhatsAppThreadEvidence).where(
            WhatsAppThreadEvidence.thread_id == t.tid,
            WhatsAppThreadEvidence.claim_type == claim_type)
            .order_by(WhatsAppThreadEvidence.id)).scalars())


def drain():
    get_worker().drain(timeout=30.0)


# ══ WC-01 — live asynchronous posture, the review's five inputs ═══════════════════════

class AsyncQuestionCoverage(unittest.TestCase):
    """5 inputs × {QUALIFYING, QUOTED} × {immediate, 3.5 s, timeout, unavailable}."""

    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()
        cls.results = []

    @classmethod
    def tearDownClass(cls):
        # Zero silent losses across the matrix: every case that ran answered its question,
        # on the same turn or the next one (a failing case also fails its own test).
        lost = [r for r in cls.results if not r[3]]
        print(f"\nWILD1-C-R3 ASYNC MATRIX ran={len(cls.results)} answered="
              f"{sum(r[3] for r in cls.results)} silently_lost={len(lost)}")
        assert not lost, lost

    def world(self, stage):
        if stage == "QUALIFYING":
            return W.new_thread(self.eng)
        return T.quoted_world(self.eng)

    def first_turn(self, stage, key, words, topics, mode):
        t = self.world(stage)
        interp = FirstReadingInterpreter(
            [(words[:12], W.payload(faq=topics)),
             ("Corolla", W.payload(vehicle=("Toyota", "Corolla", 2018),
                                   locations=(("Palermo", "INSPECTION_LOCATION"),))),
             ("transferencia", W.payload(faq=("payment",)))],
            slow_key=words[:12], **MODES[mode])
        r = W.run(self.eng, W.burst(self.eng, t, [words]), interp=interp,
                  config=W.cfg_live_async())
        drain()
        return t, interp, r

    def follow_up(self, t, interp, stage):
        # QUALIFYING: the next turn is COMMERCIAL (vehicle + locality → the quote), so the
        # owed answer must travel with the commercial progression, not be displaced by it.
        # (A vehicle-only turn hands off in this harness: no location Flow is configured.)
        words = ("Es un Toyota Corolla 2018 y está en Palermo" if stage == "QUALIFYING"
                 else "Otra cosa: ¿aceptan transferencia?")
        return W.run(self.eng, W.burst(self.eng, t, [words]), interp=interp,
                     config=W.cfg_live_async())

    def check(self, stage, key, words, topics, mode):
        t, interp, r1 = self.first_turn(stage, key, words, topics, mode)
        out1 = text_of(r1)
        s1 = W.snap(self.eng, t)
        wants = [SCOPE] + ([REPORT] if "report" in topics else [])
        completed = mode in ("immediate", "after_3_5s")
        if completed:
            for want in wants:
                self.assertIn(want, out1)
            self.assertNotIn(ACK, out1)
            self.assertTrue(r1.coverage.complete, r1.coverage.summary())
            self.assertIn(COV.RequiredClaim.SERVICE_SCOPE, r1.coverage.covered)
            self.assertEqual(s1["pending"], [])
        else:
            # acknowledged, never implied answered, durably retained, not "complete"
            self.assertIn(ACK, out1)
            self.assertNotIn(SCOPE, out1)
            self.assertFalse(r1.coverage.complete, r1.coverage.summary())
            self.assertEqual(len(r1.coverage.pending), 1)
            self.assertNotIn(COV.RequiredClaim.SERVICE_SCOPE, r1.coverage.covered)
            self.assertEqual([p[0] for p in s1["pending"]], ["PENDING"])
            self.assertEqual(s1["pending"][0][2],
                             "SEMANTIC_TIMEOUT" if mode == "timeout" else "SEMANTIC_UNAVAILABLE")
            with Session(self.eng) as db:
                ids = {m.wa_message_id for m in db.execute(select(WhatsAppMessage).where(
                    WhatsAppMessage.thread_id == t.tid,
                    WhatsAppMessage.direction == "in")).scalars()}
            self.assertTrue(set(s1["pending"][0][1]) <= ids and s1["pending"][0][1])
        # a generic closing is never counted as the FAQ answer
        if GENERIC_CLOSING in out1 and SCOPE not in out1:
            self.assertNotIn(COV.RequiredClaim.SERVICE_SCOPE, r1.coverage.covered)

        r2 = self.follow_up(t, interp, stage)
        out2 = text_of(r2)
        s2 = W.snap(self.eng, t)
        if stage == "QUALIFYING":
            self.assertIn(T.BLOCK_140, out2)              # the commercial step happened
        if not completed:
            for want in wants:
                self.assertIn(want, out2, f"{stage}/{key}/{mode}: owed answer not delivered")
            self.assertEqual([p[0] for p in s2["pending"]], ["PENDING", "RESOLVED"])
            self.assertTrue(r2.coverage is None or not r2.coverage.pending)
        self.assertNotIn(ACK, out2)
        # zero duplicate answers, zero duplicate evidence rows
        everything = "\n".join(outbound_texts(self.eng, t))
        self.assertEqual(everything.count(SCOPE), 1, f"{stage}/{key}/{mode}")
        owed = [(r.value, tuple(sorted(r.source_message_ids or ())))
                for r in evidence(self.eng, t, "required_claim")]
        self.assertEqual(len(owed), len(set(owed)))
        self.results.append((stage, key, mode, SCOPE in out1 or SCOPE in out2))
        return t, r1, r2


def _make(stage, key, words, topics, mode):
    def test(self):
        self.check(stage, key, words, topics, mode)
    test.__name__ = f"test_{stage.lower()}_{key}_{mode}"
    return test


for _stage in ("QUALIFYING", "QUOTED"):
    for _key, _words, _topics in INPUTS:
        for _mode in MODES:
            _t = _make(_stage, _key, _words, _topics, _mode)
            setattr(AsyncQuestionCoverage, _t.__name__, _t)


class AsyncBoundaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()

    def test_a_pending_question_is_never_complete_coverage(self):
        """R3-10: answered / routed / pending / missing are four distinct states; a burst
        whose question is still pending is an open debt, never complete coverage."""
        canon = {COV.RequiredClaim.PAYMENT: "Aceptamos efectivo."}
        rep = COV.coverage((), "", canon, pending=[("wamid.A", "wamid.B")])
        self.assertEqual((rep.missing, rep.pending), (frozenset(), (("wamid.A", "wamid.B"),)))
        self.assertFalse(rep.complete, rep.summary())
        self.assertEqual(rep.summary()["pending"], [["wamid.A", "wamid.B"]])
        self.assertTrue(COV.coverage([COV.RequiredClaim.PAYMENT], "Aceptamos efectivo.",
                                     canon).complete)

    def test_bare_greeting_never_waits(self):
        """W1F-R2: 'hola' / 'buenas' / 'buenas tardes' never wait on a slow reading, and
        nothing is pending — there is nothing to owe."""
        for words in ("hola", "buenas", "Buenas tardes!", "hola, buenas tardes"):
            with self.subTest(words=words):
                t = W.new_thread(self.eng)
                interp = FirstReadingInterpreter([], slow_key=words[:4], delay=3.0)
                r = W.run(self.eng, W.burst(self.eng, t, [words]), interp=interp,
                          config=W.cfg_live_async())
                self.assertLess(r.elapsed, 2.0, f"{words!r} waited {r.elapsed:.2f}s")
                self.assertNotIn(ACK, text_of(r))
                self.assertEqual(W.snap(self.eng, t)["pending"], [])
                drain()

    def test_question_potential_is_typed_not_a_phrase_list(self):
        eng = object.__new__(CE.ConversationEngine)
        eng._turn_question_potential = None
        P = COV.QuestionPotential

        def pot(*texts):
            eng._turn_question_potential = None
            return eng._question_potential(list(texts))
        self.assertIs(pot("hola"), P.GREETING_ONLY)
        self.assertIs(pot("Hola, buenas tardes!"), P.GREETING_ONLY)     # any route
        self.assertIs(pot("buenas", "hola"), P.GREETING_ONLY)
        for words in ("¿Qué revisan?", "hola, ¿qué incluye?", "quería hacer una consulta"):
            self.assertIs(pot(words), P.MAY_ASK, words)
        self.assertIs(pot("Es un Toyota Corolla 2018"), P.NOT_INFORMATIONAL)

    def test_wait_is_bounded_by_the_route_budget(self):
        """A reading slower than every budget: the general-information turn waits at most
        min(4.5 s, configured timeout) + composition, never the provider's 30 s."""
        t = W.new_thread(self.eng)
        interp = FirstReadingInterpreter([("¿Qué revisan", W.payload(faq=("service_scope",)))],
                                         slow_key="¿Qué revisan", delay=12.0)
        r = W.run(self.eng, W.burst(self.eng, t, ["¿Qué revisan?"]), interp=interp,
                  config=W.cfg_live_async())
        self.assertLess(r.elapsed, CE._LAYER_D_SEMANTIC_WAIT_SECONDS + 2.5)
        self.assertGreaterEqual(r.elapsed, CE._LAYER_D_SEMANTIC_WAIT_SECONDS - 0.2)
        self.assertIn(ACK, text_of(r))
        drain()

    def test_commercial_path_question_is_retained_too(self):
        """A burst that names a vehicle (commercial path) AND asks what is inspected: when the
        reading fails, the question is retained as pending — never silently dropped."""
        t = W.new_thread(self.eng)
        words = "Es un Toyota Corolla 2018, ¿qué revisan?"
        interp = FirstReadingInterpreter(
            [(words[:12], W.payload(faq=("service_scope",), vehicle=("Toyota", "Corolla", 2018))),
             ("Palermo", W.payload(locations=(("Palermo", "INSPECTION_LOCATION"),)))],
            slow_key=words[:12], raises=True)
        r1 = W.run(self.eng, W.burst(self.eng, t, [words]), interp=interp,
                   config=W.cfg_live_async())
        self.assertIn(ACK, text_of(r1))
        self.assertEqual([p[0] for p in W.snap(self.eng, t)["pending"]], ["PENDING"])
        r2 = W.run(self.eng, W.burst(self.eng, t, ["está en Palermo"]), interp=interp,
                   config=W.cfg_live_async())
        self.assertIn(SCOPE, text_of(r2))

    def test_resolved_question_never_repeats_what_was_already_answered(self):
        """The payment floor answers beside the acknowledgement; on resolution only the
        still-owed scope is answered — payment is not repeated (one answer per claim)."""
        t = W.new_thread(self.eng)
        words = "¿Aceptan débito? ¿Y qué revisan?"
        interp = FirstReadingInterpreter(
            [(words[:10], W.payload(faq=("payment", "service_scope"))),
             ("Corolla", W.payload(vehicle=("Toyota", "Corolla", 2018),
                                   locations=(("Palermo", "INSPECTION_LOCATION"),)))],
            slow_key=words[:10], raises=True)
        r1 = W.run(self.eng, W.burst(self.eng, t, [words]), interp=interp,
                   config=W.cfg_live_async())
        out1 = text_of(r1)
        self.assertIn(PAYMENT, out1)
        self.assertIn(ACK, out1)
        self.assertNotIn(SCOPE, out1)
        r2 = W.run(self.eng, W.burst(self.eng, t, ["Es un Toyota Corolla 2018 y está en Palermo"]),
                   interp=interp, config=W.cfg_live_async())
        out2 = text_of(r2)
        self.assertIn(SCOPE, out2)
        self.assertNotIn(PAYMENT, out2)
        everything = "\n".join(outbound_texts(self.eng, t))
        self.assertEqual((everything.count(PAYMENT), everything.count(SCOPE)), (1, 1))

    def test_blocked_outbound_never_answers_an_owed_claim(self):
        """WC-07: an outbound row that was blocked or failed never proves an answer."""
        led = COV.Ledger(asks=[COV.Ask(COV.RequiredClaim.PAYMENT, 5)],
                         outbound=[(6, PAYMENT, "blocked"), (7, PAYMENT, "failed")])
        canon = {COV.RequiredClaim.PAYMENT: PAYMENT}
        self.assertEqual(led.outstanding(canon), {COV.RequiredClaim.PAYMENT})
        led.outbound.append((8, PAYMENT, "sent"))
        self.assertEqual(led.outstanding(canon), frozenset())

    def test_owed_claim_duplicate_rolls_back_savepoint_only(self):
        """WC-07: a duplicate owed row (unique (thread_id, claim_id) — the concurrent
        same-burst race) rolls back its own SAVEPOINT; the turn's transaction stays usable,
        its other writes commit, and nothing half-written survives."""
        t = T.quoted_world(self.eng)
        with Session(self.eng) as db:
            db.add(WhatsAppThreadEvidence(thread_id=t.tid, cycle_key=0, claim_type="r3_marker",
                                          value="x", outcome="HOLD", claim_id="r3-dup-0001"))
            db.commit()
        interp = W.Interpreter([("informe", W.payload(faq=("report",), day="TOMORROW",
                                                       availability=True))])

        def colliding(ce, ctx, claims, ids, *, reason):
            ce.db.add(WhatsAppThreadEvidence(
                thread_id=ctx.thread.id, cycle_key=0, claim_type="required_claim",
                value="report", outcome="HOLD", claim_id="r3-dup-0001",
                source_message_ids=list(ids)))
            return 1
        with mock.patch.object(CE, "_FLOW_BODY_MAX", 10), \
                mock.patch.object(CE.ConversationEngine, "_add_owed_rows", colliding):
            r = W.run(self.eng, W.burst(self.eng, t, ["¿para mañana tenés? y el informe qué trae"]),
                      interp=interp)
        s = W.snap(self.eng, t)
        self.assertEqual(len(r.flows), 1)           # the turn completed
        self.assertEqual(s["flag"], "ACEPTADO")     # and its other writes committed
        self.assertEqual(s["owed"], [])             # the colliding savepoint rolled back whole
        self.assertEqual(len(evidence(self.eng, t, "r3_marker")), 1)


# ══ WC-02 — operator authority over PERDIDO (real operator actions) ═══════════════════

class OperatorAuthority(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()
        from app.ui import kanban_actions as KA
        from app.api import leads as API
        cls.KA, cls.API = KA, API

    def op(self, fn, **kw):
        with Session(self.eng) as db:
            return getattr(fn(db=db, **kw), "status_code", None)

    def engine_decline(self):
        t = T.quoted_world(self.eng)
        W.run(self.eng, W.burst(self.eng, t, ["no gracias, es caro"]), interp=W.Interpreter(
            [("no gracias", W.payload(accept="REJECT", decline_reason="PRICE"))]))
        s = W.snap(self.eng, t)
        self.assertEqual((s["flag"], s["motivo"]), ("PERDIDO", "PRECIO"))
        return t

    def accept(self, t, *, config=None):
        r = W.run(self.eng, W.burst(self.eng, t, ["dale, avancemos"]),
                  interp=W.Interpreter([("dale", W.payload(accept="ACCEPT"))]), config=config)
        return r, W.snap(self.eng, t)

    def assertHeld(self, s, motivo="PRECIO"):
        self.assertEqual((s["flag"], s["motivo"]), ("PERDIDO", motivo))
        self.assertNotIn("RECONSIDERED", [d[0] for d in s["decisions"]])
        self.assertEqual((s["token"], s["offers"], s["booked"]), (None, 0, 0))

    def test_engine_decline_then_customer_reconsideration_reactivates(self):
        t = self.engine_decline()
        r, s = self.accept(t)
        self.assertEqual((s["flag"], s["stage"]), ("ACEPTADO", "SCHEDULING"))
        self.assertEqual([d[0] for d in s["decisions"]], ["DECLINED", "RECONSIDERED"])

    def test_operator_perdido_without_engine_decline_never_reactivates(self):
        t = T.quoted_world(self.eng)
        self.assertEqual(self.op(self.KA.ui_perdido, lead_id=t.lead_id,
                                 motivo_perdida="PRECIO"), 303)
        r, s = self.accept(t)
        self.assertHeld(s)
        self.assertEqual([d[2] for d in s["decisions"]], ["OPERATOR_UI"])

    def test_decline_clear_then_operator_perdido_same_motivo_never_reactivates(self):
        t = self.engine_decline()
        self.op(self.KA.ui_lead_flag_clear, lead_id=t.lead_id)
        self.op(self.KA.ui_perdido, lead_id=t.lead_id, motivo_perdida="PRECIO")
        r, s = self.accept(t)
        self.assertHeld(s)
        self.assertEqual([d[:1] + d[2:] for d in s["decisions"]],
                         [("DECLINED", "ENGINE"), ("OPERATOR_FLAG_ACTION", "OPERATOR_UI"),
                          ("OPERATOR_FLAG_ACTION", "OPERATOR_UI")])

    def test_decline_flag_change_then_operator_perdido_same_motivo_never_reactivates(self):
        t = self.engine_decline()
        self.op(self.KA.ui_lead_flag_set, lead_id=t.lead_id, flag="PRESUPUESTO_ENVIADO")
        self.op(self.KA.ui_perdido, lead_id=t.lead_id, motivo_perdida="PRECIO")
        r, s = self.accept(t)
        self.assertHeld(s)

    def test_decline_then_operator_reconfirms_perdido_never_reactivates(self):
        """MI-4 / review S3: re-confirming the SAME PERDIDO/PRECIO is an intervention."""
        t = self.engine_decline()
        self.op(self.KA.ui_perdido, lead_id=t.lead_id, motivo_perdida="PRECIO")
        r, s = self.accept(t)
        self.assertHeld(s)

    def test_decline_then_operator_flag_set_perdido_never_reactivates(self):
        t = self.engine_decline()
        self.op(self.KA.ui_lead_flag_set, lead_id=t.lead_id, flag="PERDIDO")
        with Session(self.eng) as db:
            self.assertEqual(db.get(Lead, t.lead_id).motivo_perdida, "PRECIO")  # kept
        r, s = self.accept(t)
        self.assertHeld(s)

    def test_different_operator_motivo_never_reactivates(self):
        t = self.engine_decline()
        self.op(self.KA.ui_perdido, lead_id=t.lead_id, motivo_perdida="DISPONIBILIDAD")
        r, s = self.accept(t)
        self.assertHeld(s, motivo="DISPONIBILIDAD")

    def test_api_patch_reconfirmation_never_reactivates(self):
        from app.schemas.leads import LeadUpdate
        t = self.engine_decline()
        with Session(self.eng) as db:
            self.API.update_lead(t.lead_id, LeadUpdate(flag="PERDIDO", motivo_perdida="PRECIO"),
                                 db=db)
        r, s = self.accept(t)
        self.assertHeld(s)
        self.assertEqual(s["decisions"][-1][2], "OPERATOR_API")

    def test_estado_move_does_not_touch_the_flag_and_is_not_an_intervention(self):
        """An estado move that changes neither flag nor motivo records nothing and leaves
        the engine's own decline reversible (owner rule: FLAG interventions revoke)."""
        t = self.engine_decline()
        self.op(self.KA.ui_move, lead_id=t.lead_id, estado="COORDINAR_DISPONIBILIDAD")
        self.assertEqual([d[0] for d in W.snap(self.eng, t)["decisions"]], ["DECLINED"])

    def test_engine_redecline_after_reconsideration_is_reversible_again(self):
        """MI-4 (newest row, not oldest): DECLINED(PRECIO) → RECONSIDERED → DECLINED(OTRO);
        the CURRENT decline is the engine's OTRO, so an acceptance reactivates it."""
        t = self.engine_decline()
        self.accept(t)
        with Session(self.eng) as db:                 # back to a delivered quote, QUOTED
            from app.models import WhatsAppThreadState
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == t.tid)).scalar_one()
            st.last_stage = "QUOTED"
            db.get(Lead, t.lead_id).flag = "PRESUPUESTO_ENVIADO"
            db.commit()
        W.run(self.eng, W.burst(self.eng, t, ["no, ya conseguí otro"]), interp=W.Interpreter(
            [("ya conseguí", W.payload(accept="REJECT", decline_reason="OTHER"))]))
        self.assertEqual(W.snap(self.eng, t)["motivo"], "OTRO")
        r, s = self.accept(t)
        self.assertEqual(s["flag"], "ACEPTADO")
        self.assertEqual([d[0] for d in s["decisions"]],
                         ["DECLINED", "RECONSIDERED", "DECLINED", "RECONSIDERED"])

    def test_legacy_authority_off_holds_operator_perdido(self):
        """MI-4b: the authority-off (legacy) branch obeys the same rule."""
        legacy = W.cfg(reconciler_acceptance_authority_enabled=False)
        t = T.quoted_world(self.eng)
        self.op(self.KA.ui_perdido, lead_id=t.lead_id, motivo_perdida="PRECIO")
        r, s = self.accept(t, config=legacy)
        self.assertHeld(s)
        self.assertLegacyHoldAnswered(r, s)
        t2 = self.engine_decline()
        self.op(self.KA.ui_perdido, lead_id=t2.lead_id, motivo_perdida="PRECIO")
        r, s2 = self.accept(t2, config=legacy)
        self.assertHeld(s2)
        self.assertLegacyHoldAnswered(r, s2)

    def assertLegacyHoldAnswered(self, r, s):
        """MI-4b / R3-12: the hold is the legacy branch's own decision, not a crash and not
        the flush guard undoing an acceptance — the turn is handled, the customer gets one
        delivered reply, the stage stays QUOTED and no Booking Flow is dispatched."""
        self.assertNotEqual(r.out.action, "error", r.out)
        self.assertEqual(s["stage"], "QUOTED")
        self.assertEqual(r.flows, [])
        self.assertEqual(len(r.sent), 1, r.sent)

    def test_legacy_authority_off_reactivates_engine_decline(self):
        legacy = W.cfg(reconciler_acceptance_authority_enabled=False)
        t = self.engine_decline()
        r, s = self.accept(t, config=legacy)
        self.assertEqual(s["flag"], "ACEPTADO")
        self.assertEqual([d[0] for d in s["decisions"]], ["DECLINED", "RECONSIDERED"])

    def test_side_door_flag_write_on_operator_perdido_is_blocked(self):
        """The flush guard: a vehicle correction on a QUOTED lead resets the flag to
        PRESUPUESTANDO for a re-quote — on an operator-held PERDIDO that write is restored."""
        t = T.quoted_world(self.eng)
        self.op(self.KA.ui_perdido, lead_id=t.lead_id, motivo_perdida="PRECIO")
        words = "perdón, me equivoqué, en realidad es un Toyota Corolla 2018"
        W.run(self.eng, W.burst(self.eng, t, [words]), interp=W.Interpreter(
            [("me equivoqué", W.payload(vehicle=("Toyota", "Corolla", 2018)))]))
        s = W.snap(self.eng, t)
        self.assertEqual((s["flag"], s["motivo"]), ("PERDIDO", "PRECIO"))
        self.assertNotIn("RECONSIDERED", [d[0] for d in s["decisions"]])

    def test_side_door_is_blocked_under_the_kill_switch_too(self):
        """OUTBOUND off: the blocked-dispatch branch commits CE state after the turn — the
        guard is still armed then."""
        t = T.quoted_world(self.eng)
        self.op(self.KA.ui_perdido, lead_id=t.lead_id, motivo_perdida="PRECIO")
        words = "perdón, me equivoqué, en realidad es un Toyota Corolla 2018"
        import os

        def kill_switch_off(_ce):          # inside the harness's env patch; it is restored
            os.environ["OUTBOUND_ENABLED"] = "false"
        r = W.run(self.eng, W.burst(self.eng, t, [words]), hook=kill_switch_off,
                  interp=W.Interpreter([("me equivoqué",
                                         W.payload(vehicle=("Toyota", "Corolla", 2018)))]))
        self.assertEqual(r.out.action, "blocked_dispatch")
        s = W.snap(self.eng, t)
        self.assertEqual((s["flag"], s["motivo"]), ("PERDIDO", "PRECIO"))

    def test_provenance_and_mutation_roll_back_together(self):
        """Provenance failure → no flag change committed; mutation (commit) failure → no
        provenance row committed. One transaction, both or neither."""
        from app.services import quote_decision_history as QDH
        t = self.engine_decline()
        boom = RuntimeError("provenance write failed (scripted)")
        with mock.patch.object(self.KA, "record_operator_flag_action", side_effect=boom):
            with self.assertRaises(RuntimeError):
                self.op(self.KA.ui_lead_flag_clear, lead_id=t.lead_id)
        s = W.snap(self.eng, t)
        self.assertEqual((s["flag"], s["motivo"]), ("PERDIDO", "PRECIO"))
        self.assertEqual([d[0] for d in s["decisions"]], ["DECLINED"])
        # commit fails after both were staged
        with Session(self.eng) as db:
            with mock.patch.object(db, "commit", side_effect=RuntimeError("commit failed")):
                with self.assertRaises(RuntimeError):
                    self.KA.ui_lead_flag_clear(lead_id=t.lead_id, db=db)
            db.rollback()
        s = W.snap(self.eng, t)
        self.assertEqual((s["flag"], s["motivo"]), ("PERDIDO", "PRECIO"))
        self.assertEqual([d[0] for d in s["decisions"]], ["DECLINED"])
        self.assertTrue(callable(QDH.record_operator_flag_action))

    def test_provenance_writer_never_commits_on_its_own(self):
        """The writer stages its row in the CALLER's transaction: a rollback of that
        transaction leaves neither the flag change nor the provenance row."""
        from app.services.quote_decision_history import (PRODUCER_OPERATOR_UI,
                                                          record_operator_flag_action)
        t = self.engine_decline()
        with Session(self.eng) as db:
            lead = db.get(Lead, t.lead_id)
            lead.flag = None
            self.assertEqual(record_operator_flag_action(
                db, lead, action="flag_clear", route="test", producer=PRODUCER_OPERATOR_UI,
                flag_before="PERDIDO", motivo_before="PRECIO"), 1)
            db.rollback()
        s = W.snap(self.eng, t)
        self.assertEqual((s["flag"], [d[0] for d in s["decisions"]]), ("PERDIDO", ["DECLINED"]))

    def test_every_operator_write_path_records_provenance(self):
        """Static inventory: each production function that assigns lead.flag or
        lead.motivo_perdida outside the engine records operator provenance."""
        import ast
        import inspect
        modules = [self.KA, self.API]
        writers = []
        for mod in modules:
            tree = ast.parse(inspect.getsource(mod))
            for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
                assigns = any(isinstance(n, ast.Assign) and any(
                    isinstance(tg, ast.Attribute) and tg.attr in ("flag", "motivo_perdida")
                    and isinstance(tg.value, ast.Name) and tg.value.id == "lead"
                    for tg in n.targets) for n in ast.walk(fn))
                if assigns:
                    src = ast.get_source_segment(inspect.getsource(mod), fn) or ""
                    writers.append((mod.__name__, fn.name,
                                    "_operator_provenance(" in src
                                    or "record_operator_flag_action(" in src))
        self.assertTrue(writers)
        self.assertEqual([w for w in writers if not w[2]], [], writers)
        self.assertEqual({w[1] for w in writers if w[0].endswith("kanban_actions")},
                         {"ui_move", "ui_move_lead", "ui_lead_move", "ui_perdido",
                          "ui_lead_flag_set", "ui_lead_flag_clear"})

    def test_kanban_history_shows_operator_action(self):
        from app.services.quote_decision_history import history_lines, lead_quote_decisions
        t = self.engine_decline()
        self.op(self.KA.ui_perdido, lead_id=t.lead_id, motivo_perdida="PRECIO")
        with Session(self.eng) as db:
            lines = history_lines(lead_quote_decisions(db, [t.lead_id])[t.lead_id])
        self.assertTrue(lines[0].startswith("Presupuesto rechazado por el cliente (PRECIO, automático)"))
        self.assertTrue(lines[1].startswith("Flag modificado por un operador (PRECIO)"))


# ══ WC-03 — website-form quote ════════════════════════════════════════════════════════

FORM = ("Hola, quiero solicitar una revisión pre-compra.\n\n- Nombre: Prueba Sintetica\n"
        "- Teléfono: 1100000000\n- Auto a revisar: Jeep Renegade 2024\n"
        "- Tipo: SUV / 4x4 / Deportivo\n- Localidad: Berazategui\n"
        "- Total estimado: $240.000\n\nref: r3test")


class WebsiteFormQuote(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()

    def test_one_block_one_cta_no_total_called_revision(self):
        for name, config in (("sync", W.cfg()), ("live_async", W.cfg_live_async())):
            with self.subTest(posture=name):
                t = W.new_thread(self.eng)
                r = W.run(self.eng, W.burst(self.eng, t, [FORM]), interp=W.Interpreter([]),
                          config=config)
                out = text_of(r)
                self.assertEqual(out.count(T.BLOCK_240), 1, out)
                self.assertEqual(out.count("Revisión: $"), 1)
                self.assertNotRegex(out.lower(), r"precio de la revisi[oó]n es")
                self.assertNotIn("podemos avanzar", out)               # the second CTA
                self.assertEqual(out.count("?"), 1)                     # exactly one CTA
                self.assertTrue(out.rstrip().endswith("¿Qué día y horario te viene mejor?"))
                s = W.snap(self.eng, t)
                self.assertEqual((s["flag"], s["stage"]), ("PRESUPUESTO_ENVIADO", "SCHEDULING"))

    def test_website_form_declares_the_breakdown(self):
        """The same PricingQuote and the same 3-tuple PRICE contract as every quote path."""
        import inspect
        src = inspect.getsource(CE.ConversationEngine._handle_website_form)
        self.assertIn("_quote_breakdown(real_price_quote)", src)
        self.assertIn("StructuredClaim(DomainClaim.PRICE, (base, viaticos, total))", src)
        self.assertNotIn("precio_total:,.0f", src)


# ══ validator — the touched price path keeps the canonical block ═══════════════════════

class ValidatedPriceBlock(unittest.TestCase):
    def setUp(self):
        import types
        from app.services.response_validator import CanonicalFacts
        self.facts = CanonicalFacts(
            vehicle_marca="Jeep", vehicle_modelo="Renegade", inspection_zone_group="Sur",
            inspection_zone_detail="Berazategui", quote_total=240000, quote_base=150000,
            quote_viaticos=90000, known_zone_names=frozenset({"Berazategui"}))
        self.eng = object.__new__(CE.ConversationEngine)
        self.eng._build_canonical_facts = lambda ctx, state: self.facts
        self.ctx = types.SimpleNamespace(thread=types.SimpleNamespace(id=1))
        q = types.SimpleNamespace(precio_base=150000, viaticos=90000, precio_total=240000)
        self.quote = self.eng._composed_quote_reply("Jeep", "Renegade", "Berazategui", q, 2024)

    def test_unrelated_rewrite_does_not_flatten_the_block(self):
        out = self.eng._validate_outbound_text(self.quote + " La revisión sale $99.999.",
                                               self.ctx, None)
        self.assertIn(T.BLOCK_240, out)                                  # three lines intact
        self.assertIn(f"\n\n{T.BLOCK_240}\n\n", out)                      # paragraph kept
        self.assertNotIn("$99.999", out)                                 # the rewrite stands

    def test_untouched_quote_is_returned_as_composed(self):
        self.assertEqual(self.eng._validate_outbound_text(self.quote, self.ctx, None),
                         str(self.quote))

    def test_corrupted_block_is_never_sent(self):
        """A message-wide finding that removes a LINE of the block: no partial breakdown."""
        out = self.eng._validate_outbound_text(
            self.quote + " Tu turno quedó confirmado para mañana.", self.ctx, None)
        self.assertNotIn("Viáticos: $90.000", out)
        self.assertNotIn("Total: $240.000", out)
        self.assertNotIn("confirmado", out)


# ══ WC-04 — the review's surviving mutants ════════════════════════════════════════════

class ReviewMutantKillers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()

    def test_mi3b_no_decline_without_a_delivered_quote(self):
        """QUOTED stage, explicit REJECT, but no outbound ever carried the quote."""
        t = W.new_thread(self.eng, stage="QUOTED", flag="PRESUPUESTO_ENVIADO",
                         vehicle=T.RENEGADE, zone=T.BERAZATEGUI)
        W.run(self.eng, W.burst(self.eng, t, ["no gracias, es muy caro, lo dejo"]),
              interp=W.Interpreter([("no gracias", W.payload(accept="REJECT",
                                                              decline_reason="PRICE"))]))
        s = W.snap(self.eng, t)
        self.assertNotEqual(s["flag"], "PERDIDO")
        self.assertEqual(s["decisions"], [])

    def test_mi5b_ai_confirmation_scrub_requests_never_confirms(self):
        for stage in (CE.STAGE_SCHEDULING, CE.STAGE_QUOTED):
            out = CE._scrub_scheduling_confirmation("Listo, tu turno quedó confirmado.", stage)
            self.assertNotEqual(out, "Listo, tu turno quedó confirmado.")
            low = out.lower()
            for bad in ("confirmar el turno", "confirmá el turno", "confirmado", "confirmamos"):
                self.assertNotIn(bad, low)
            self.assertIn("para solicitar el turno", low)

    def test_mi7_flow_body_never_exceeds_meta_limit(self):
        """Owed content + a long (legitimate) Flow body > 1024: the Flow stays within Meta's
        limit, what does not fit stays durably owed and is answered next turn; nothing is
        marked delivered that was not in the body; the Flow is the existing booking Flow."""
        self.assertEqual(CE._FLOW_BODY_MAX, 1024)
        t = T.quoted_world(self.eng)
        interp = W.Interpreter([
            ("informe", W.payload(faq=("service_scope", "payment"), quote=True,
                                  day="TOMORROW", availability=True)),
            ("dale", W.payload(accept="ACCEPT"))])
        long_body = ("Elegí el horario que te sirva y completá tus datos para solicitar el "
                     "turno. ") * 7                      # ~530 chars, request-only wording
        with mock.patch.object(CE, "_booking_flow_body", lambda **_k: long_body.strip()):
            r1 = W.run(self.eng, W.burst(self.eng, t, [
                "¿para mañana tenés? ¿qué revisan, qué trae el informe, cuánto es el "
                "viático y aceptan transferencia?"]), interp=interp)
        self.assertEqual(len(r1.flows), 1)
        body = r1.flows[0]
        self.assertLessEqual(len(body), 1024)
        s1 = W.snap(self.eng, t)
        self.assertTrue(s1["token"])                           # the booking Flow's token
        self.assertEqual(s1["booked"], 0)
        omitted = [c for c, txt in ((COV.RequiredClaim.SERVICE_SCOPE, SCOPE),
                                    (COV.RequiredClaim.PAYMENT, PAYMENT),
                                    (COV.RequiredClaim.PRICE_BREAKDOWN, T.BLOCK_240))
                   if txt not in body]
        self.assertTrue(omitted, "the test must actually overflow")
        for c in omitted:
            self.assertNotIn(c, r1.coverage.covered)
            self.assertIn(c.value, s1["owed"])
        r2 = W.run(self.eng, W.burst(self.eng, t, ["dale"]), interp=interp)
        out2 = text_of(r2)
        for c, txt in ((COV.RequiredClaim.SERVICE_SCOPE, SCOPE),
                       (COV.RequiredClaim.PAYMENT, PAYMENT),
                       (COV.RequiredClaim.PRICE_BREAKDOWN, T.BLOCK_240)):
            if c in omitted:
                self.assertIn(txt, out2, c)


if __name__ == "__main__":
    unittest.main()
