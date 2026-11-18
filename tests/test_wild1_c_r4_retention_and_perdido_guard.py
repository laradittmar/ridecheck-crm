"""WILD1-C-R4 — route-independent question retention, one turn deadline, PERDIDO entry guard.

Focused tests for the R4 correction of the WILD1-C-R3 focused independent review:

* R3RV-01 — retention no longer depends on Layer D's route classifier: the review's seven
  supported inputs, in QUALIFYING and QUOTED, under an immediate, a ~3.5 s, a late (8 s) and a
  failing reading, are never silently lost.
* R3RV-03 — ONE monotonic semantic deadline per turn, shared by the current reading and the
  pending re-reading (concurrent, not sequential); a thread-start failure never runs the
  re-reading inline; the re-reading is bounded by rows and characters, and recovery drains
  every row over bounded batches.
* R3RV-02 — a vehicle/location fallback Flow on a PERDIDO lead is held on the real Flow route.
* R3RV-04 — the flush guard is removed on every exit of the request lifecycle, and never
  overwrites a concurrent operator commit.
* §3.5 — a non-dataclass decision HOLDs instead of raising.
* Owner copy — the new acknowledgement (alone, never beside the generic closing), WC-08
  (operator-held PERDIDO + acceptance) and WC-09 (price concern without refusal).
* Review test gaps R3-19 (any total-as-revision wording on the website form), R3-23b (pending
  write failure inside its savepoint) and R3-25 (accumulated preamble > 1024).

Everything runs the real `/api/conversation/handle` route (`_wild1c_harness`); only model and
network seams are scripted. Operator actions are the real kanban/API functions.
"""
from __future__ import annotations

import re
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import _wild1c_harness as W
import test_wild1_c_conversation_correction as T

from sqlalchemy import event as sa_event
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Lead, WhatsAppMessage, WhatsAppThreadEvidence, WhatsAppThreadState
from app.schemas.conversation import ConversationHandleIn
from app.services import claim_coverage as COV
from app.services import semantic_interpreter as SI
from app.services.shadow_worker import get_worker

CE = W.CE
SCOPE, REPORT, PAYMENT = CE._FAQ_SCOPE_LEAD, CE._FAQ_REPORT_ANSWER, CE._FAQ_PAYMENT_ANSWER
ACK = CE._PENDING_QUESTION_ACK
HOLD = CE._PERDIDO_HOLD_REPLY
CONCERN = CE._PRICE_CONCERN_LEAD
GENERIC_CLOSING = "Gracias por tu mensaje"
BUDGET = CE._TURN_SEMANTIC_BUDGET_SECONDS
#: scheduling, composition and SQLite overhead on top of the semantic budget
OVERHEAD = 1.5
LATE = 8.0                                  # a reading between 6 and 12 s

#: the review's R3RV-01 inputs (c1–c7) and the claim each one asks
INPUTS = [
    ("c1", "¿Cuándo me mandan el informe?", "report", {}),
    ("c2", "¿Y el informe cuándo llega?", "report", {}),
    ("c3", "Hola, tengo un auto para revisar. ¿Qué me entregan después?", "report",
     dict(inspection=True)),
    ("c4", "necesito revisar una camioneta que le revisan", "service_scope",
     dict(inspection=True)),
    ("c5", "Tengo un Corolla 2018 en Palermo, ¿qué revisan?", "service_scope",
     dict(inspection=True, vehicle=("Toyota", "Corolla", 2018),
          locations=(("Palermo", "INSPECTION_LOCATION"),))),
    ("c6", "Quiero revisar un auto antes de comprarlo, ¿qué incluye?", "service_scope",
     dict(inspection=True)),
    ("c7", "¿Qué miran en la revisión?", "service_scope", {}),
]
ANSWER = {"report": REPORT, "service_scope": SCOPE}
MODES = {
    "immediate": dict(delay=0.0, raises=False),
    "after_3_5s": dict(delay=3.5, raises=False),
    "late_8s": dict(delay=LATE, raises=False),
    "unavailable": dict(delay=0.0, raises=True),
}


class SlowFirst(W.Interpreter):
    """The scripted interpreter with a delay / failure on the FIRST reading of a burst that
    contains `slow_key` (the re-reading is normal). Thread-safe: called from the shadow
    worker and from the pending-resolution thread."""

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


class Switchable(W.Interpreter):
    """Every call fails (or sleeps) while `mode` says so — a provider outage."""

    def __init__(self, script):
        super().__init__(script)
        self.mode = "ok"
        self.delay = 0.0

    def __call__(self, messages, model):
        if self.mode == "raise":
            raise RuntimeError("provider outage (scripted)")
        if self.mode == "slow":
            time.sleep(self.delay)
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


def inbound_ids(eng, t):
    with Session(eng) as db:
        return {m.wa_message_id for m in db.execute(select(WhatsAppMessage).where(
            WhatsAppMessage.thread_id == t.tid, WhatsAppMessage.direction == "in")).scalars()}


def drain():
    get_worker().drain(timeout=30.0)
    W.join_pending_resolution()


def unresolved(eng, t):
    return COV.unresolved_pending(evidence(eng, t, "pending_question"))


def no_duplicates(case, eng, t):
    rows = evidence(eng, t, "pending_question") + evidence(eng, t, "required_claim")
    keys = [(r.claim_type, r.status, r.value, tuple(sorted(r.source_message_ids or ())))
            for r in rows]
    case.assertEqual(len(keys), len(set(keys)), keys)
    ids = [r.claim_id for r in rows]
    case.assertEqual(len(ids), len(set(ids)))


# ══ A. R3RV-01 — route-independent retention: 7 inputs × 2 stages × 4 reading modes ═════

class RouteIndependentRetention(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()
        cls.results = []

    @classmethod
    def tearDownClass(cls):
        lost = [r for r in cls.results if not r[3]]
        print(f"\nWILD1-C-R4 RETENTION MATRIX ran={len(cls.results)} "
              f"answered={sum(r[3] for r in cls.results)} silently_lost={len(lost)} "
              f"max_t1={max((r[4] for r in cls.results), default=0):.2f}s")
        assert not lost, lost

    def world(self, stage):
        return W.new_thread(self.eng) if stage == "QUALIFYING" else T.quoted_world(self.eng)

    def check(self, stage, key, words, claim, extra, mode):
        t = self.world(stage)
        interp = SlowFirst(
            [(words[:14], W.payload(faq=(claim,), **extra)),
             ("Corolla 2018 y", W.payload(vehicle=("Toyota", "Corolla", 2018),
                                          locations=(("Palermo", "INSPECTION_LOCATION"),))),
             ("transferencia", W.payload(faq=("payment",)))],
            slow_key=words[:14], **MODES[mode])
        r1 = W.run(self.eng, W.burst(self.eng, t, [words]), interp=interp,
                   config=W.cfg_live_async())
        out1 = text_of(r1)
        drain()
        want = ANSWER[claim]
        s1 = W.snap(self.eng, t)
        # one deadline: no first turn ever waits more than the budget
        self.assertLess(r1.elapsed, BUDGET + OVERHEAD, f"{stage}/{key}/{mode} T1")
        if mode in ("immediate", "after_3_5s"):
            self.assertIn(want, out1, f"{stage}/{key}/{mode}: answered on the same turn")
            self.assertNotIn(ACK, out1)
            self.assertEqual(s1["pending"], [])
        else:
            self.assertIn(ACK, out1, f"{stage}/{key}/{mode}: acknowledged")
            self.assertEqual([p[0] for p in s1["pending"]], ["PENDING"])
            self.assertEqual(s1["pending"][0][2],
                             "SEMANTIC_TIMEOUT" if mode == "late_8s" else "SEMANTIC_UNAVAILABLE")
            self.assertTrue(s1["pending"][0][1]
                            and set(s1["pending"][0][1]) <= inbound_ids(self.eng, t))
            self.assertFalse(r1.coverage is not None and r1.coverage.complete)
            self.assertNotIn(GENERIC_CLOSING, out1)          # owner copy: never beside the ack
        follow = ("Es un Toyota Corolla 2018 y está en Palermo" if stage == "QUALIFYING"
                  else "Otra cosa: ¿aceptan transferencia?")
        r2 = W.run(self.eng, W.burst(self.eng, t, [follow]), interp=interp,
                   config=W.cfg_live_async())
        out2 = text_of(r2)
        drain()
        s2 = W.snap(self.eng, t)
        self.assertLess(r2.elapsed, BUDGET + OVERHEAD, f"{stage}/{key}/{mode} T2")
        self.assertNotIn(ACK, out2)
        if mode not in ("immediate", "after_3_5s"):
            self.assertIn(want, out2, f"{stage}/{key}/{mode}: resolved on the next turn")
            self.assertEqual([p[0] for p in s2["pending"]], ["PENDING", "RESOLVED"])
        everything = "\n".join(outbound_texts(self.eng, t))
        self.assertEqual(everything.count(want), 1, f"{stage}/{key}/{mode}: answered once")
        no_duplicates(self, self.eng, t)
        self.results.append((stage, key, mode, want in out1 or want in out2, r1.elapsed))


def _make(stage, key, words, claim, extra, mode):
    def test(self):
        self.check(stage, key, words, claim, extra, mode)
    test.__name__ = f"test_{stage.lower()}_{key}_{mode}"
    return test


for _stage in ("QUALIFYING", "QUOTED"):
    for _key, _words, _claim, _extra in INPUTS:
        for _mode in MODES:
            _t = _make(_stage, _key, _words, _claim, _extra, _mode)
            setattr(RouteIndependentRetention, _t.__name__, _t)


class RetentionBoundaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()

    def test_semantic_state_is_typed(self):
        S = COV.SemanticTurnState
        self.assertEqual({s.name for s in S}, {
            "GREETING_ONLY", "SEMANTICALLY_RESOLVED", "SEMANTICALLY_UNRESOLVED_NONTERMINAL",
            "TERMINAL_OR_PRE_GATED", "NOT_DISPATCHED"})

    def test_unresolved_burst_with_no_supported_claim_resolves_without_an_answer(self):
        """The re-reading finds nothing supported: RESOLVED, and no irrelevant FAQ answer."""
        t = W.new_thread(self.eng)
        words = "Es para mi hermano, después te cuento"
        interp = SlowFirst([], slow_key=words[:12], raises=True)
        r1 = W.run(self.eng, W.burst(self.eng, t, [words]), interp=interp,
                   config=W.cfg_live_async())
        self.assertIn(ACK, text_of(r1))
        drain()
        r2 = W.run(self.eng, W.burst(self.eng, t, ["dale, gracias"]), interp=interp,
                   config=W.cfg_live_async())
        drain()
        out2 = text_of(r2)
        for faq in (SCOPE, REPORT, PAYMENT):
            self.assertNotIn(faq, out2)
        self.assertNotIn(ACK, out2)
        self.assertEqual([p[0] for p in W.snap(self.eng, t)["pending"]], ["PENDING", "RESOLVED"])
        self.assertEqual(evidence(self.eng, t, "required_claim"), [])

    def test_greeting_and_terminal_turns_stay_fast_and_owe_nothing(self):
        for words in ("hola", "Buenas tardes!", "hola, buenas tardes"):
            with self.subTest(words=words):
                t = W.new_thread(self.eng)
                r = W.run(self.eng, W.burst(self.eng, t, [words]),
                          interp=SlowFirst([], slow_key=words[:4], delay=3.0),
                          config=W.cfg_live_async())
                self.assertLess(r.elapsed, 2.0)
                self.assertNotIn(ACK, text_of(r))
                self.assertEqual(W.snap(self.eng, t)["pending"], [])
                drain()

    def test_motorcycle_handoff_never_carries_the_acknowledgement(self):
        for words in ("tengo una moto Honda CB 500, ¿qué revisan?",
                      "es un cuatriciclo, ¿cuándo me mandan el informe?"):
            with self.subTest(words=words):
                t = W.new_thread(self.eng)
                r = W.run(self.eng, W.burst(self.eng, t, [words]),
                          interp=SlowFirst([], slow_key=words[:8], raises=True),
                          config=W.cfg_live_async())
                drain()
                self.assertNotIn(ACK, text_of(r))
                self.assertEqual(W.snap(self.eng, t)["pending"], [])
                self.assertNotIn("Total:", text_of(r))

    def test_acknowledgement_copy_is_the_owner_authorized_text(self):
        self.assertEqual(ACK, "Recibí tu consulta, pero no pude procesarla en este momento. "
                              "Si me escribís nuevamente, la retomo y te respondo.")
        self.assertNotIn("próximo mensaje", ACK)

    def test_quoted_unavailable_sends_the_acknowledgement_alone(self):
        """No useful normal content (the generic closing only) → the ack alone."""
        t = T.quoted_world(self.eng)
        words = "¿Qué revisan?"
        r = W.run(self.eng, W.burst(self.eng, t, [words]),
                  interp=SlowFirst([(words, W.payload(faq=("service_scope",)))],
                                   slow_key=words, raises=True),
                  config=W.cfg_live_async())
        drain()
        self.assertEqual(r.sent, [ACK])

    def test_acknowledgement_accompanies_real_content_never_the_generic_closing(self):
        """A turn with real content (here the restated quote) keeps it; the ack goes with it."""
        t = T.quoted_world(self.eng)
        words = "¿Y el informe cuándo llega?"
        r = W.run(self.eng, W.burst(self.eng, t, [words]),
                  interp=SlowFirst([(words[:10], W.payload(faq=("report",)))],
                                   slow_key=words[:10], raises=True),
                  config=W.cfg_live_async())
        drain()
        self.assertEqual(len(r.sent), 1)
        self.assertTrue(r.sent[0].rstrip().endswith(ACK), r.sent)
        self.assertNotIn(GENERIC_CLOSING, r.sent[0])

    def test_clarification_flow_body_carries_the_acknowledgement(self):
        """A continuing text turn that ends in the Location clarification Flow (which never
        passes through the plan) still acknowledges a pending burst, in the Flow body."""
        t = W.new_thread(self.eng)
        words = "Es un Toyota Corolla 2018, ¿cuándo me mandan el informe?"
        config = W.cfg_live_async(whatsapp_location_fallback_flow_id="LOCFLOW_R4_FIXTURE")
        r = W.run(self.eng, W.burst(self.eng, t, [words]),
                  interp=SlowFirst([(words[:12], W.payload(faq=("report",),
                                                           vehicle=("Toyota", "Corolla", 2018)))],
                                   slow_key=words[:12], raises=True), config=config)
        drain()
        self.assertEqual(len(r.flows), 1, (r.sent, r.flows))
        self.assertTrue(r.flows[0].rstrip().endswith(ACK), r.flows[0])
        self.assertEqual([p[0] for p in W.snap(self.eng, t)["pending"]], ["PENDING"])

    def test_turn_boundary_retains_a_burst_no_send_path_recorded(self):
        """Defence in depth: even if no consumer and no send path consulted the reading, the
        turn boundary retains the unresolved burst (`_finalize_question_retention`)."""
        t = T.quoted_world(self.eng)
        words = "¿Cuándo me mandan el informe?"
        silent = mock.patch.multiple(
            CE.ConversationEngine,
            _turn_owes_pending_ack=lambda self, ctx: False,
            _coverage_evidence=lambda self: None,
            _bounded_question_reading=lambda self: None)
        with silent:
            W.run(self.eng, W.burst(self.eng, t, [words]),
                  interp=SlowFirst([(words[:10], W.payload(faq=("report",)))],
                                   slow_key=words[:10], raises=True),
                  config=W.cfg_live_async())
            drain()
        self.assertEqual([p[0] for p in W.snap(self.eng, t)["pending"]], ["PENDING"])

    def test_a_terminal_turn_is_never_retained_even_if_asked_directly(self):
        """Defence in depth: `_note_semantic_unavailable` itself refuses a terminal turn (a
        motorcycle / phone / service pre-gate), whichever caller reaches it."""
        eng = object.__new__(CE.ConversationEngine)
        eng.settings = SimpleNamespace(semantic_same_turn_enabled=True)
        eng._turn_semantic = SimpleNamespace(timed_out=False, ready=False, dispatched=True)
        eng._turn_question_potential = None
        eng._turn_pending_reason = None
        eng._turn_ctx = None
        eng._turn_continues = False
        eng._note_semantic_unavailable(["tengo una moto, ¿qué revisan?"])
        self.assertIsNone(eng._turn_pending_reason)
        eng._turn_continues = True                       # the same burst on a continuing turn
        eng._turn_question_potential = None
        eng._note_semantic_unavailable(["tengo una moto, ¿qué revisan?"])
        self.assertEqual(eng._turn_pending_reason, "SEMANTIC_UNAVAILABLE")

    def test_pending_persistence_failure_rolls_back_its_savepoint_only(self):
        """R3-23b: the pending write fails INSIDE its savepoint. Documented policy: the turn
        continues truthfully — the savepoint is rolled back whole (no partial row), the outer
        transaction stays usable (its other writes commit), and the acknowledgement is NOT
        sent because nothing was retained that could be resumed."""
        t = T.quoted_world(self.eng)
        words = "¿Cuándo me mandan el informe?"

        def failing_add(ce, ctx, *, status, ids, claim_id, reason, payload):
            ce.db.add(WhatsAppThreadEvidence(
                thread_id=ctx.thread.id, cycle_key=0, claim_type="pending_question",
                value="pending_question", status=status, outcome="HOLD",
                claim_id=claim_id, source_message_ids=list(ids), validated_as=dict(payload)))
            ce.db.flush()
            raise RuntimeError("pending write failed inside the savepoint (scripted)")
        with mock.patch.object(CE.ConversationEngine, "_add_pending_row", failing_add):
            r = W.run(self.eng, W.burst(self.eng, t, [words]),
                      interp=SlowFirst([(words[:10], W.payload(faq=("report",)))],
                                       slow_key=words[:10], raises=True),
                      config=W.cfg_live_async())
            drain()
        s = W.snap(self.eng, t)
        self.assertNotEqual(r.out.action, "error", r.out)
        self.assertEqual(s["pending"], [])                     # no partial pending row
        self.assertNotIn(ACK, text_of(r))                      # no promise that cannot be kept
        self.assertEqual(len(r.sent), 1)                       # the turn still answered
        with Session(self.eng) as db:                          # outer txn committed the cursor
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == t.tid)).scalar_one()
            self.assertTrue(st.last_processed_inbound_wa_message_id)


# ══ B. R3RV-03 — one semantic deadline per turn ══════════════════════════════════════

class OneTurnDeadline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()

    def test_budget_is_documented_and_bounded_by_the_configured_timeout(self):
        self.assertEqual(BUDGET, 4.5)
        self.assertLessEqual(BUDGET, W.LIVE_TIMEOUT_SECONDS)
        eng = object.__new__(CE.ConversationEngine)
        eng.settings = SimpleNamespace(semantic_same_turn_timeout_seconds=2.0)
        self.assertEqual(eng._semantic_budget(), 2.0)

    def test_qualifying_and_quoted_timeout_turns_stay_inside_the_budget(self):
        for stage in ("QUALIFYING", "QUOTED"):
            with self.subTest(stage=stage):
                t = W.new_thread(self.eng) if stage == "QUALIFYING" else T.quoted_world(self.eng)
                words = "¿Qué revisan?"
                r = W.run(self.eng, W.burst(self.eng, t, [words]),
                          interp=SlowFirst([(words, W.payload(faq=("service_scope",)))],
                                           slow_key=words, delay=12.0),
                          config=W.cfg_live_async())
                self.assertLess(r.elapsed, BUDGET + OVERHEAD, f"{stage}: {r.elapsed:.2f}s")
                self.assertGreaterEqual(r.elapsed, BUDGET - 0.3)
                self.assertIn(ACK, text_of(r))
                drain()

    def test_pending_reread_and_slow_current_reading_do_not_double_the_turn(self):
        """T1 leaves a pending row. On T2 BOTH the current reading and the re-reading hang:
        they run concurrently and share one deadline (R3 measured 8.04 / 9.05 s)."""
        t = T.quoted_world(self.eng)
        interp = Switchable([("informe", W.payload(faq=("report",))),
                             ("transferencia", W.payload(faq=("payment",)))])
        interp.mode = "raise"
        W.run(self.eng, W.burst(self.eng, t, ["¿Cuándo me mandan el informe?"]), interp=interp,
              config=W.cfg_live_async())
        drain()
        interp.mode, interp.delay = "slow", LATE
        r2 = W.run(self.eng, W.burst(self.eng, t, ["¿Y aceptan transferencia?"]), interp=interp,
                   config=W.cfg_live_async())
        self.assertLess(r2.elapsed, BUDGET + OVERHEAD, f"T2 {r2.elapsed:.2f}s")
        self.assertIn(ACK, text_of(r2))
        drain()
        self.assertEqual(len(unresolved(self.eng, t)), 2)       # both kept, nothing lost
        interp.mode = "ok"
        r3 = W.run(self.eng, W.burst(self.eng, t, ["gracias"]), interp=interp,
                   config=W.cfg_live_async())
        drain()
        self.assertIn(REPORT, text_of(r3))
        self.assertIn(PAYMENT, "\n".join(outbound_texts(self.eng, t)))
        self.assertEqual(unresolved(self.eng, t), [])
        no_duplicates(self, self.eng, t)

    def test_thread_start_failure_is_bounded_and_never_runs_inline(self):
        t = T.quoted_world(self.eng)
        words = "¿Cuándo me mandan el informe?"
        interp = SlowFirst([(words[:10], W.payload(faq=("report",)))],
                           slow_key=words[:10], raises=True)
        W.run(self.eng, W.burst(self.eng, t, [words]), interp=interp, config=W.cfg_live_async())
        drain()
        calls_before = len(interp.calls)

        class NoStart(threading.Thread):
            def start(self):
                if self.name == "pending-question-resolution":
                    raise RuntimeError("can't start new thread (scripted)")
                return super().start()
        slow_now = SlowFirst([(words[:10], W.payload(faq=("report",)))],
                             slow_key="nunca-coincide")
        with mock.patch.object(CE.threading, "Thread", NoStart):
            r2 = W.run(self.eng, W.burst(self.eng, t, ["ok"]), interp=slow_now,
                       config=W.cfg_live_async())
        drain()
        self.assertLess(r2.elapsed, BUDGET + OVERHEAD)
        self.assertNotIn(REPORT, text_of(r2))                    # not resolved inline …
        self.assertTrue(all("informe" not in c for c in slow_now.calls))   # … not even called
        self.assertIn(ACK, text_of(r2))                           # still pending, still owned
        self.assertEqual(len(unresolved(self.eng, t)), 1)
        self.assertEqual(len(interp.calls), calls_before)
        r3 = W.run(self.eng, W.burst(self.eng, t, ["gracias"]), interp=interp,
                   config=W.cfg_live_async())
        drain()
        self.assertIn(REPORT, text_of(r3))
        self.assertEqual(unresolved(self.eng, t), [])

    def outage_then_recovery(self, bursts):
        """`bursts` fail during an outage; then the provider recovers. Returns (re-reading
        payloads, recovery turns, thread). Asserts no row is ever deleted and every row is
        resolved, and that each recovery turn acknowledges exactly while rows remain."""
        t = T.quoted_world(self.eng)
        interp = Switchable([])
        interp.mode = "raise"
        rereads = []
        original = SI.SemanticTurnInterpreter.interpret

        def recording(self_, messages, **kw):
            from app.schemas.turn_evidence import BurstReconstruction
            if kw.get("reconstruction") is BurstReconstruction.REPLAY_CAUSAL_MARKER:
                rereads.append(list(messages))
            return original(self_, messages, **kw)
        with mock.patch.object(SI.SemanticTurnInterpreter, "interpret", recording):
            for words in bursts:
                W.run(self.eng, W.burst(self.eng, t, [words]), interp=interp,
                      config=W.cfg_live_async())
                drain()
            self.assertEqual(len(unresolved(self.eng, t)), len(bursts))
            interp.mode = "ok"
            rereads.clear()                       # the recovery re-readings only
            turns = 0
            while unresolved(self.eng, t) and turns < 8:
                r = W.run(self.eng, W.burst(self.eng, t, [f"sigo acá {turns}"]), interp=interp,
                          config=W.cfg_live_async())
                drain()
                turns += 1
                # rows left outside this turn's batch are still owned: acknowledged again
                self.assertEqual(ACK in text_of(r), bool(unresolved(self.eng, t)), turns)
        self.assertEqual(unresolved(self.eng, t), [])
        pend = evidence(self.eng, t, "pending_question")
        self.assertEqual(sum(r.status == "PENDING" for r in pend), len(bursts))  # never deleted
        self.assertEqual(sum(r.status == "RESOLVED" for r in pend), len(bursts))
        no_duplicates(self, self.eng, t)
        return rereads, turns, t

    def test_reread_caps_are_the_documented_values(self):
        self.assertEqual((CE._PENDING_REREAD_MAX_ROWS, CE._PENDING_REREAD_MAX_CHARS), (3, 4000))

    def test_prolonged_outage_of_short_bursts_is_bounded_by_rows(self):
        """Seven short failing bursts: each recovery re-reading carries at most 3 rows (the
        character cap never binds here), so recovery takes 3 bounded batches (3+3+1)."""
        rereads, turns, _t = self.outage_then_recovery(
            [f"consulta corta {i}, ¿qué revisan?" for i in range(7)])
        self.assertTrue(rereads)
        self.assertEqual(max(len(x) for x in rereads), 3)
        self.assertEqual(turns, 3)

    def test_prolonged_outage_of_long_bursts_is_bounded_by_characters(self):
        """Three failing bursts of ~2,500 characters: the row cap never binds, the character
        cap does — one row per re-reading (2 rows would exceed 4,000), three batches."""
        filler = "detalle " * 310                       # ~2,480 characters
        rereads, turns, _t = self.outage_then_recovery(
            [f"consulta {i}: {filler}" for i in range(3)])
        self.assertTrue(rereads)
        for texts in rereads:
            self.assertLessEqual(sum(len(x) for x in texts), 4000)
            self.assertEqual(len(texts), 1)
        self.assertEqual(turns, 3)


# ══ C. owner copy — WC-08 and WC-09 ═══════════════════════════════════════════════════

class OwnerCopy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()
        from app.ui import kanban_actions as KA
        cls.KA = KA

    def op_perdido(self, t, motivo="PRECIO"):
        with Session(self.eng) as db:
            self.KA.ui_perdido(lead_id=t.lead_id, motivo_perdida=motivo, db=db)

    def assertNoProgression(self, r, s, motivo="PRECIO"):
        self.assertEqual((s["flag"], s["motivo"]), ("PERDIDO", motivo))
        self.assertEqual((s["token"], s["offers"], s["booked"], s["revisions"]), (None, 0, 0, 0))
        self.assertEqual(s["stage"], "QUOTED")
        self.assertEqual(r.flows, [])
        self.assertEqual(s["notices"], 0)
        self.assertNotIn("RECONSIDERED", [d[0] for d in s["decisions"]])

    def test_wc08_operator_perdido_acceptance_is_held_with_the_owner_copy(self):
        for name, config in (("authority_on", W.cfg()),
                             ("legacy", W.cfg(reconciler_acceptance_authority_enabled=False))):
            with self.subTest(path=name):
                t = T.quoted_world(self.eng)
                self.op_perdido(t)
                r = W.run(self.eng, W.burst(self.eng, t, ["dale, avancemos"]),
                          interp=W.Interpreter([("dale", W.payload(accept="ACCEPT"))]),
                          config=config)
                s = W.snap(self.eng, t)
                self.assertEqual(r.sent, [HOLD], r.sent)
                self.assertNotIn("podemos avanzar", text_of(r))
                self.assertNotIn("Total:", text_of(r))
                self.assertNoProgression(r, s)

    def test_legacy_caller_honours_a_hold_verdict(self):
        """Review §3.5 race: the legacy branch saw no operator PERDIDO, but the lost-lead rule
        (reading again) HOLDs — the turn must hold, never accept and never error."""
        t = T.quoted_world(self.eng)
        self.op_perdido(t)
        legacy = W.cfg(reconciler_acceptance_authority_enabled=False)
        with mock.patch.object(CE.ConversationEngine, "_operator_lost_lead",
                               lambda self, ctx: False):
            r = W.run(self.eng, W.burst(self.eng, t, ["dale, avancemos"]),
                      interp=W.Interpreter([("dale", W.payload(accept="ACCEPT"))]), config=legacy)
        s = W.snap(self.eng, t)
        self.assertNotEqual(r.out.action, "error")
        self.assertEqual(r.sent, [HOLD])
        self.assertNoProgression(r, s)

    def test_wc08_day_proposal_on_operator_perdido_is_held(self):
        t = T.quoted_world(self.eng)
        self.op_perdido(t)
        r = W.run(self.eng, W.burst(self.eng, t, ["¿el martes a las 10 puede ser?"]),
                  interp=W.Interpreter([("martes", W.payload(accept="ACCEPT", day="TUESDAY"))]))
        s = W.snap(self.eng, t)
        self.assertIn(HOLD, text_of(r))
        self.assertNoProgression(r, s)

    def test_wc08_copy_claims_no_notification_and_no_booking(self):
        low = HOLD.lower()
        for bad in ("notific", "avisamos", "avisé", "confirm", "agend", "turno", "reserv"):
            self.assertNotIn(bad, low)

    def test_engine_decline_reconsideration_through_text_still_works(self):
        t = T.quoted_world(self.eng)
        W.run(self.eng, W.burst(self.eng, t, ["no gracias, es caro"]), interp=W.Interpreter(
            [("no gracias", W.payload(accept="REJECT", decline_reason="PRICE"))]))
        r = W.run(self.eng, W.burst(self.eng, t, ["dale, avancemos"]),
                  interp=W.Interpreter([("dale", W.payload(accept="ACCEPT"))]))
        s = W.snap(self.eng, t)
        self.assertEqual((s["flag"], s["motivo"], s["stage"]), ("ACEPTADO", None, "SCHEDULING"))
        self.assertEqual([d[0] for d in s["decisions"]], ["DECLINED", "RECONSIDERED"])
        self.assertNotIn(HOLD, text_of(r))

    def test_wc09_price_concern_gets_the_neutral_breakdown(self):
        cases = [("Me parece caro, ¿cuánto hay de viático?", W.payload(quote=True, accept="HESITATE")),
                 ("uff, me parece caro eh", W.payload(accept="HESITATE")),
                 ("Me parece caro. ¿Y qué revisan?",
                  W.payload(faq=("service_scope",), accept="HESITATE"))]
        for words, p in cases:
            with self.subTest(words=words):
                t = T.quoted_world(self.eng)
                r = W.run(self.eng, W.burst(self.eng, t, [words]),
                          interp=W.Interpreter([(words[:8], p)]))
                out = text_of(r)
                s = W.snap(self.eng, t)
                # a claim-free natural bridge may precede it; nothing else, and no amount
                self.assertIn(CONCERN, out)
                self.assertNotIn("$", out.split(CONCERN)[0])
                self.assertEqual(out.count(T.BLOCK_240), 1)
                self.assertNotIn("Genial", out)
                self.assertNotIn("podemos avanzar", out)
                self.assertNotIn("¿Qué día", out)
                if "revisan" in words:
                    self.assertIn(SCOPE, out)                  # other owed FAQ answered too
                self.assertEqual((s["flag"], s["motivo"], s["decisions"]),
                                 ("PRESUPUESTO_ENVIADO", None, []))

    def test_wc09_explicit_reject_still_declines(self):
        t = T.quoted_world(self.eng)
        W.run(self.eng, W.burst(self.eng, t, ["no gracias, es muy caro, no la voy a hacer"]),
              interp=W.Interpreter([("no gracias", W.payload(accept="REJECT",
                                                              decline_reason="PRICE"))]))
        self.assertEqual(W.snap(self.eng, t)["flag"], "PERDIDO")


# ══ D. R3RV-02 — PERDIDO on every engine entry path (real Flow route) ═════════════════

VEHICLE_FORM = {"tipo_vehiculo": "AUTO", "marca": "Toyota", "modelo": "Corolla", "anio": "2018"}
LOCATION_FORM = {"zona_general": "CABA", "localidad": "Palermo"}
COROLLA = ("Toyota", "Corolla", 2018, "AUTO")
PALERMO = ("CABA", "Palermo")


def flow_event(eng, t, form, *, token=None):
    ev = W.burst(eng, t, ["[respuesta de formulario]"], message_type="flow_response")
    return ConversationHandleIn(thread_id=ev.thread_id, wa_message_id=ev.wa_message_id,
                                wa_id=ev.wa_id, message_type="flow_response", text=None,
                                flow_response=dict(form), flow_token=token)


class PerdidoEntryPaths(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()
        from app.ui import kanban_actions as KA
        cls.KA = KA

    def op(self, fn, **kw):
        with Session(self.eng) as db:
            fn(db=db, **kw)

    def qualifying_world(self, form, **kw):
        """QUALIFYING with the OTHER half known, so the form would produce a quote."""
        if form is VEHICLE_FORM:
            return W.new_thread(self.eng, zone=PALERMO, **kw)
        return W.new_thread(self.eng, vehicle=COROLLA, **kw)

    def submit(self, t, form, *, hook=None):
        r = W.run(self.eng, flow_event(self.eng, t, form), interp=W.Interpreter([]), hook=hook)
        return r, W.snap(self.eng, t)

    def assertHeld(self, r, s, before):
        self.assertEqual((s["flag"], s["motivo"]), (before["flag"], before["motivo"]))
        self.assertEqual(s["stage"], before["stage"])
        self.assertEqual((s["token"], s["offers"], s["booked"], s["revisions"]),
                         (before["token"], before["offers"], 0, 0))
        self.assertEqual(r.flows, [])                             # no Flow of any kind
        out = text_of(r)
        self.assertNotIn("Total:", out)                           # no quote …
        self.assertNotIn("podemos avanzar", out)                  # … no CTA
        self.assertNotIn("RECONSIDERED", [d[0] for d in s["decisions"]])
        self.assertNotEqual(s["flag"], "PRESUPUESTO_ENVIADO")     # no quote-follow-up eligibility
        self.assertEqual(s["notices"], before["notices"])

    def test_operator_perdido_plus_each_fallback_is_held(self):
        for form in (VEHICLE_FORM, LOCATION_FORM):
            with self.subTest(form=sorted(form)[0]):
                t = self.qualifying_world(form)
                self.op(self.KA.ui_perdido, lead_id=t.lead_id, motivo_perdida="OTRO")
                before = W.snap(self.eng, t)
                r, s = self.submit(t, form)
                self.assertHeld(r, s, before)
                self.assertEqual(r.sent, [HOLD])

    def test_operator_reconfirmed_perdido_plus_each_fallback_is_held(self):
        for form in (VEHICLE_FORM, LOCATION_FORM):
            with self.subTest(form=sorted(form)[0]):
                t = T.quoted_world(self.eng)
                W.run(self.eng, W.burst(self.eng, t, ["no gracias, es caro"]),
                      interp=W.Interpreter([("no gracias", W.payload(
                          accept="REJECT", decline_reason="PRICE"))]))
                self.op(self.KA.ui_perdido, lead_id=t.lead_id, motivo_perdida="PRECIO")
                before = W.snap(self.eng, t)
                r, s = self.submit(t, form)
                self.assertHeld(r, s, before)

    def test_engine_decline_plus_each_fallback_is_held_and_still_reversible(self):
        for form in (VEHICLE_FORM, LOCATION_FORM):
            with self.subTest(form=sorted(form)[0]):
                t = T.quoted_world(self.eng)
                W.run(self.eng, W.burst(self.eng, t, ["no gracias, es caro"]),
                      interp=W.Interpreter([("no gracias", W.payload(
                          accept="REJECT", decline_reason="PRICE"))]))
                before = W.snap(self.eng, t)
                self.assertEqual((before["flag"], before["motivo"]), ("PERDIDO", "PRECIO"))
                r, s = self.submit(t, form)
                self.assertHeld(r, s, before)
                self.assertEqual([d[0] for d in s["decisions"]], ["DECLINED"])
                # the engine's own decline is still the customer's to reconsider, by text
                W.run(self.eng, W.burst(self.eng, t, ["dale, avancemos"]),
                      interp=W.Interpreter([("dale", W.payload(accept="ACCEPT"))]))
                s2 = W.snap(self.eng, t)
                self.assertEqual((s2["flag"], s2["motivo"]), ("ACEPTADO", None))
                self.assertEqual([d[0] for d in s2["decisions"]], ["DECLINED", "RECONSIDERED"])

    def test_unsolicited_fallback_on_operator_perdido_is_held(self):
        for form in (VEHICLE_FORM, LOCATION_FORM):
            with self.subTest(form=sorted(form)[0]):
                t = self.qualifying_world(form)          # no fallback Flow was ever sent
                self.op(self.KA.ui_perdido, lead_id=t.lead_id, motivo_perdida="PRECIO")
                before = W.snap(self.eng, t)
                r, s = self.submit(t, form)
                self.assertHeld(r, s, before)

    def test_held_under_the_kill_switch(self):
        import os

        def kill_switch_off(_ce):
            os.environ["OUTBOUND_ENABLED"] = "false"
        for form in (VEHICLE_FORM, LOCATION_FORM):
            with self.subTest(form=sorted(form)[0]):
                t = self.qualifying_world(form)
                self.op(self.KA.ui_perdido, lead_id=t.lead_id, motivo_perdida="PRECIO")
                before = W.snap(self.eng, t)
                r, s = self.submit(t, form, hook=kill_switch_off)
                self.assertEqual(r.out.action, "blocked_dispatch")
                self.assertEqual(r.sent, [])
                self.assertHeld(r, s, before)

    def test_no_stale_motivo_on_a_non_perdido_lead(self):
        """A legacy lead carrying a loss reason without PERDIDO: the fallback quote leaves a
        coherent pair (no motivo outside PERDIDO)."""
        for form in (VEHICLE_FORM, LOCATION_FORM):
            with self.subTest(form=sorted(form)[0]):
                t = self.qualifying_world(form, flag="PRESUPUESTANDO", motivo="PRECIO")
                r, s = self.submit(t, form)
                self.assertEqual(s["flag"], "PRESUPUESTO_ENVIADO")
                self.assertIsNone(s["motivo"])
                self.assertIn(T.BLOCK_140, text_of(r))

    def test_non_perdido_fallback_still_quotes(self):
        for form in (VEHICLE_FORM, LOCATION_FORM):
            with self.subTest(form=sorted(form)[0]):
                t = self.qualifying_world(form)
                r, s = self.submit(t, form)
                self.assertEqual((s["flag"], s["stage"]), ("PRESUPUESTO_ENVIADO", "QUOTED"))
                self.assertIn(T.BLOCK_140, text_of(r))

    def test_every_engine_flag_writer_is_inventoried(self):
        """Independent inventory of the engine's writes to `flag` / `motivo_perdida`: each
        one is a known site, so a new writer must be reviewed against the PERDIDO guard."""
        import ast
        import inspect
        src = inspect.getsource(CE)
        tree = ast.parse(src)
        sites = []
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            for n in ast.walk(fn):
                if isinstance(n, ast.Assign):
                    for tg in n.targets:
                        tgs = tg.elts if isinstance(tg, ast.Tuple) else [tg]
                        for x in tgs:
                            if isinstance(x, ast.Attribute) and x.attr in ("flag", "motivo_perdida"):
                                sites.append((fn.name, x.attr))
        self.assertEqual(sorted(set(sites)), sorted(set(ENGINE_FLAG_WRITERS)), sorted(set(sites)))


#: (function, field) — every engine assignment, reviewed for WILD1-C-R4 (closeout §5).
ENGINE_FLAG_WRITERS = [
    ("_process_vehicle_fallback_response", "flag"),
    ("_process_vehicle_fallback_response", "motivo_perdida"),
    ("_process_location_fallback_response", "flag"),
    ("_process_location_fallback_response", "motivo_perdida"),
    ("_process_text", "flag"),
    ("_apply_lost_lead_rule", "flag"),
    ("_apply_lost_lead_rule", "motivo_perdida"),
    ("_handle_quote_decline", "flag"),
    ("_handle_quote_decline", "motivo_perdida"),
    ("_handle_quoted_acceptance", "flag"),
    ("_handle_website_form", "flag"),
    ("_handle_scheduling_escalation", "flag"),
    ("_execute_cycle_reset", "flag"),
    ("_execute_cycle_reset", "motivo_perdida"),
]


# ══ E. R3RV-04 — guard lifecycle ══════════════════════════════════════════════════════

class GuardLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()
        from app.ui import kanban_actions as KA
        cls.KA = KA

    def perdido_world(self):
        t = T.quoted_world(self.eng)
        with Session(self.eng) as db:
            self.KA.ui_perdido(lead_id=t.lead_id, motivo_perdida="PRECIO", db=db)
        return t

    def run_raising(self, method, *, flow=False):
        t = self.perdido_world()
        seen = {}

        def hook(ce):
            arm = ce._arm_perdido_guard

            def recording_arm(ctx):
                arm(ctx)
                if ce._turn_perdido_guard is not None:
                    seen["guard"] = ce._turn_perdido_guard
            ce._arm_perdido_guard = recording_arm

            def boom(*a, **k):
                seen["armed_at_failure"] = ce._turn_perdido_guard is not None
                raise RuntimeError(f"{method} failed (scripted)")
            setattr(ce, method, boom)
            seen["ce"] = ce
        ev = (flow_event(self.eng, t, LOCATION_FORM) if flow
              else W.burst(self.eng, t, ["perdón, en realidad es un Toyota Corolla 2018"]))
        interp = W.Interpreter([("en realidad", W.payload(vehicle=("Toyota", "Corolla", 2018)))])
        raised = None
        try:
            r = W.run(self.eng, ev, interp=interp, hook=hook)
        except RuntimeError as exc:
            raised, r = exc, None
        return t, seen, raised, r

    def assertDisarmed(self, seen):
        self.assertIn("guard", seen, "the guard was armed for the PERDIDO lead")
        session, fn = seen["guard"]
        self.assertFalse(sa_event.contains(session, "before_flush", fn))
        self.assertIsNone(seen["ce"]._turn_perdido_guard)

    def test_exceptions_after_dispatch_always_remove_the_listener(self):
        for method in ("_revoke_undelivered_slot_claim", "_turn_plan", "_write_hybrid_trace",
                       "_finalize_question_retention"):
            with self.subTest(method=method):
                t, seen, raised, _r = self.run_raising(method)
                self.assertIsNotNone(raised, "the post-processing failure propagates")
                self.assertTrue(seen.get("armed_at_failure"))
                self.assertDisarmed(seen)
                s = W.snap(self.eng, t)
                self.assertEqual((s["flag"], s["motivo"]), ("PERDIDO", "PRECIO"))

    def test_dispatch_exception_removes_the_listener(self):
        for method, flow in (("_process_text", False), ("_process_flow_response", True)):
            with self.subTest(method=method):
                t, seen, raised, r = self.run_raising(method, flow=flow)
                self.assertIsNone(raised)                       # caught: action=error
                self.assertEqual(r.out.action, "error")
                self.assertTrue(seen.get("armed_at_failure"))
                self.assertDisarmed(seen)

    def test_no_listener_leaks_to_another_session_or_lead(self):
        t, seen, raised, _r = self.run_raising("_turn_plan")
        self.assertDisarmed(seen)
        ce = seen["ce"]
        # the same engine instance, reused for another lead on a NEW session
        other = W.new_thread(self.eng, vehicle=COROLLA)
        fresh = W.H.session_factory(self.eng)()
        try:
            baseline = len(fresh.dispatch.before_flush)
            ce.db = fresh
            ce._disarm_perdido_guard()
            self.assertEqual(len(fresh.dispatch.before_flush), baseline)
        finally:
            fresh.close()
        r, s = PerdidoEntryPaths.submit(SimpleNamespace(eng=self.eng), other, LOCATION_FORM)
        self.assertEqual(s["flag"], "PRESUPUESTO_ENVIADO")      # not restored by a stray guard

    def test_guard_never_flushes_recursively_and_turn_completes(self):
        t = self.perdido_world()
        depth = {"now": 0, "max": 0}

        def hook(ce):
            def before(sess, *_a):
                depth["now"] += 1
                depth["max"] = max(depth["max"], depth["now"])

            def after(sess, *_a):
                depth["now"] -= 1
            sa_event.listen(ce.db, "before_flush", before)
            sa_event.listen(ce.db, "after_flush_postexec", after)
        words = "perdón, me equivoqué, en realidad es un Toyota Corolla 2018"
        r = W.run(self.eng, W.burst(self.eng, t, [words]), hook=hook, interp=W.Interpreter(
            [("me equivoqué", W.payload(vehicle=("Toyota", "Corolla", 2018)))]))
        self.assertNotEqual(r.out.action, "error")
        self.assertEqual(depth["max"], 1)
        s = W.snap(self.eng, t)
        self.assertEqual((s["flag"], s["motivo"]), ("PERDIDO", "PRECIO"))

    def test_guard_never_undoes_a_concurrent_operator_commit(self):
        """The turn has committed once (expiring the lead), an operator then changes the loss
        reason in its own transaction, and the engine's side door writes the flag: the guard
        keeps the DATABASE's current values — the operator's — and emits no UPDATE."""
        t = self.perdido_world()
        Sess = W.H.session_factory(self.eng)
        db = Sess()
        try:
            eng = CE.ConversationEngine(db=db, settings=W.cfg())
            ctx = eng._load_context(t.tid)
            eng._turn_perdido_left_by = None
            eng._arm_perdido_guard(ctx)
            self.assertIsNotNone(eng._turn_perdido_guard)
            db.commit()                                         # a mid-turn commit (expires)
            with Session(self.eng) as op_db:                     # the operator, concurrently
                self.KA.ui_perdido(lead_id=t.lead_id, motivo_perdida="DISPONIBILIDAD", db=op_db)
            ctx.lead.flag = "PRESUPUESTANDO"                      # the engine's side door
            db.commit()
            eng._disarm_perdido_guard()
        finally:
            db.close()
        s = W.snap(self.eng, t)
        self.assertEqual((s["flag"], s["motivo"]), ("PERDIDO", "DISPONIBILIDAD"))

    def test_non_dataclass_decision_holds_instead_of_raising(self):
        """Review §3.5: `_LEGACY_ALLOW` (a SimpleNamespace) on an operator-held PERDIDO."""
        t = self.perdido_world()
        with Session(self.eng) as db:
            eng = CE.ConversationEngine(db=db, settings=W.cfg())
            ctx = eng._load_context(t.tid)
            eng._turn_perdido_left_by = None
            out = eng._apply_lost_lead_rule(ctx, ctx.state, CE._LEGACY_ALLOW)
            self.assertFalse(out.allows)
            self.assertEqual(out.result, "HOLD")
            self.assertIn("operator_lost", out.blockers)
            self.assertEqual(ctx.lead.flag, "PERDIDO")
            self.assertIsNone(eng._turn_perdido_left_by)
            malformed = eng._apply_lost_lead_rule(ctx, ctx.state, SimpleNamespace(allows=True))
            self.assertFalse(malformed.allows)
            db.rollback()


# ══ F. review test gaps ═══════════════════════════════════════════════════════════════

FORM = ("Hola, quiero solicitar una revisión pre-compra.\n\n- Nombre: Prueba Sintetica\n"
        "- Teléfono: 1100000000\n- Auto a revisar: Jeep Renegade 2024\n"
        "- Tipo: SUV / 4x4 / Deportivo\n- Localidad: Berazategui\n"
        "- Total estimado: $240.000\n\nref: r4test")


class ReviewGapKillers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()

    def test_r3_19_website_form_states_amounts_only_inside_the_block(self):
        """Any sentence that labels the TOTAL as the revisión ("La revisión sale $240.000",
        "El precio de la revisión es $240.000", …) puts an amount outside the canonical block:
        the message may carry money only inside the block."""
        for name, config in (("sync", W.cfg()), ("live_async", W.cfg_live_async())):
            with self.subTest(posture=name):
                t = W.new_thread(self.eng)
                r = W.run(self.eng, W.burst(self.eng, t, [FORM]), interp=W.Interpreter([]),
                          config=config)
                out = text_of(r)
                self.assertEqual(out.count(T.BLOCK_240), 1, out)
                outside = out.replace(T.BLOCK_240, "")
                self.assertNotRegex(outside, r"\$\s*\d", outside)
                self.assertNotRegex(outside.lower(),
                                    r"revisi[oó]n\s+(sale|cuesta|vale|es|est[aá])\b")
                self.assertNotRegex(outside.lower(), r"precio\s+de\s+la\s+revisi[oó]n")
                get_worker().drain(timeout=30.0)

    def test_r3_25_accumulated_preamble_never_exceeds_the_meta_limit(self):
        """Several individually-valid segments whose SUM exceeds 1024 with the Flow body:
        whole segments only, omitted claims stay owed and are never marked delivered, and the
        Flow authority (token, booking Flow) is unchanged."""
        t = T.quoted_world(self.eng)
        interp = W.Interpreter([
            ("informe", W.payload(faq=("service_scope", "payment"), quote=True,
                                  day="TOMORROW", availability=True))])
        body = ("Elegí el horario que te sirva y completá tus datos para solicitar el turno. "
                * 5).strip()                               # ~390 chars, request-only wording
        parts_seen = {}
        original = CE.ConversationEngine._coverage_preamble_parts
        original_with = CE.ConversationEngine._with_coverage_preamble

        def recording(ce, ctx):
            parts = original(ce, ctx)
            parts_seen.setdefault("parts", list(parts))
            return parts

        def recording_with(ce, ctx, flow_body):
            parts_seen["body"] = flow_body                 # the real Flow body (slots + text)
            return original_with(ce, ctx, flow_body)
        with mock.patch.object(CE, "_booking_flow_body", lambda **_k: body), \
                mock.patch.object(CE.ConversationEngine, "_coverage_preamble_parts", recording), \
                mock.patch.object(CE.ConversationEngine, "_with_coverage_preamble",
                                  recording_with):
            r = W.run(self.eng, W.burst(self.eng, t, [
                "¿para mañana tenés? ¿qué revisan, qué trae el informe, cuánto es el viático "
                "y aceptan transferencia?"]), interp=interp)
        parts = parts_seen["parts"]
        body = parts_seen["body"]
        self.assertGreaterEqual(len(parts), 3)
        for p in parts:                                     # each one fits on its own …
            self.assertLessEqual(len(p + "\n\n" + body), CE._FLOW_BODY_MAX)
        self.assertGreater(len("\n\n".join(parts + [body])), CE._FLOW_BODY_MAX)   # … not all
        self.assertEqual(len(r.flows), 1)
        sent = r.flows[0]
        self.assertLessEqual(len(sent), CE._FLOW_BODY_MAX)
        self.assertTrue(sent.endswith(body))
        kept = [p for p in parts if p in sent]
        self.assertEqual(sent, "\n\n".join(kept + [body]))     # whole segments, in order
        self.assertTrue(len(kept) < len(parts))
        s = W.snap(self.eng, t)
        self.assertTrue(s["token"])
        self.assertEqual((s["booked"], s["revisions"]), (0, 0))
        for claim, txt in ((COV.RequiredClaim.SERVICE_SCOPE, SCOPE),
                           (COV.RequiredClaim.PAYMENT, PAYMENT),
                           (COV.RequiredClaim.PRICE_BREAKDOWN, T.BLOCK_240)):
            if txt not in sent:
                self.assertNotIn(claim, r.coverage.covered)
                self.assertIn(claim.value, s["owed"])
            else:
                self.assertIn(claim, r.coverage.covered)


if __name__ == "__main__":
    unittest.main()
