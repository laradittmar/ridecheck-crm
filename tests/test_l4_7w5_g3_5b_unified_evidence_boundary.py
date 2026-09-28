"""L4.7W5-GATE3-G3-5B — one evidence boundary for every producer.

The independent G3-5A audit returned four findings this file closes:

  G5A-01 HIGH    `_run_shadow_understand(ctx, event, ai_input_messages)` dispatched the
                 interpretation from the PRESENTATION subset while authority decided on
                 `_current_evidence`. The producer holding semantic primacy could interpret
                 fewer messages than the reconciler evaluated.
  G5A-02 MEDIUM  message identity and text were assembled and deduplicated separately, and
                 the dedup key was TEXT — so three WAMIDs could carry two texts and a
                 customer who repeated themselves had one message erased.
  G5A-03 MEDIUM  `stage` was read live and the decision memoised, so the stage that decided
                 was whichever the FIRST consumer happened to observe — silently.
  G5A-05 MEDIUM  the customer-facing proof replaced `_send_text_to_wa`, inside which
                 `_compose_secondary_answers` runs, so it bypassed the composer and passed
                 on the parent defect.

Everything below drives the real handler. Only three boundaries are controlled: the semantic
provider, the reply provider and the WhatsApp transport sink. Burst reconstruction, canonical
context construction, semantic dispatch, deterministic parsing, authority, HoursDisposition,
FAQ authorization, secondary composition, final composition and trace production all execute.

No vocabulary is added. Every reader used already existed.
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from test_wild04r_cycle_boundary import (                              # noqa: E402
    _WA_ID, _add_state, _make_engine, _new_session, _seed_contact_thread_lead,
)
from app.models import WhatsAppMessage                                 # noqa: E402
from app.schemas.conversation import ConversationHandleIn              # noqa: E402
from app.schemas import turn_evidence as te                           # noqa: E402
from app.schemas.hybrid_trace import HoursDisposition, burst_hash      # noqa: E402
import app.services.conversation_engine as ce                         # noqa: E402
import app.services.semantic_interpreter as si                        # noqa: E402
from app.services.conversation_engine import (                         # noqa: E402
    CanonicalMessage, ConversationEngine, _canonical_messages,
    _canonical_burst_from_texts, STAGE_QUALIFYING, STAGE_QUOTED, STAGE_SCHEDULING,
)
from app.services.outbound_safety_gate import GateOutcome              # noqa: E402

ADMIT = "ADMIT"
BY_AVAILABILITY = "SUPPRESSED_BY_SEMANTIC_AVAILABILITY"
BY_DETERMINISTIC = "SUPPRESSED_BY_DETERMINISTIC_SCHEDULING"
AMBIGUOUS = "SUPPRESSED_AMBIGUOUS"
UNCORROBORATED = "SUPPRESSED_UNCORROBORATED"
NO_CANDIDATE = "NO_CANDIDATE"
CLARIFY, HOLD, LOOK_UP, PREREQ = ("CLARIFY", "HOLD", "LOOK_UP_AVAILABILITY",
                                  "REQUEST_PREREQUISITE")
CONFIRMED = te.EvidenceStatus.CONFIRMED

#: The live defect's own burst, split the way the handset actually sent it.
LIVE_FIRST, LIVE_SECOND = "Que horarios tienen", "disponiblees"
HOURS_Q = "¿Qué horarios tienen?"
BASE_TS = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)
HOURS_TABLE_MARKERS = ("lunes de", "sabados de", "sábados de", "domingos no")

NEUTRAL_REPLY = json.dumps({
    "intent": "QUALIFYING", "reply": "Gracias por escribirnos.",
    "deferred_interest": False, "candidate": {"action": "none"}, "extracted": {},
    "lead_flag": None, "needs_human": False,
})


def availability_evidence(status=CONFIRMED, faq=()):
    return te.TurnEvidence(
        scheduling_intents=((te.SchedulingIntentEvidence(
            kind=te.SchedulingIntentKind.AVAILABILITY_ENQUIRY,
            value="AVAILABILITY_ENQUIRY", status=status),) if status else ()),
        faq_intents=tuple(te.FaqIntentEvidence(topic=t, value=t) for t in faq))


class Turn:
    """One real turn: real rows, real reconstruction, real composition.

    `rows` is a list of (wamid, text, seconds_offset) — or (wamid, text, offset, ts_override).
    `unanswered` is what n8n claims to have seen; when it is a strict subset of `rows`, this
    is the production shape of the audit's boundary findings.
    """

    def __init__(self, rows, *, unanswered=None, trigger=None, stage=STAGE_QUALIFYING,
                 evidence=None, interpret_ok=True, arm_interpreter=True, state_extra=None):
        self.db = _new_session()
        _, thread, _lead = _seed_contact_thread_lead(self.db)
        _add_state(self.db, thread.id, last_stage=stage,
                   last_processed_inbound_wa_message_id="prev-id", **(state_extra or {}))
        self._persist("prev-id", "previo", -60)
        for row in rows:
            self._persist(row[0], row[1], row[2])
        self.thread_id = thread.id
        self.rows = list(rows)
        self.trigger = trigger if trigger is not None else rows[-1][0]
        self.trigger_text = next(r[1] for r in rows if r[0] == self.trigger)
        self.unanswered = (list(unanswered) if unanswered is not None
                           else [r[1] for r in rows])

        self.sent: list[str] = []
        self.traces: list[dict] = []
        self.interpreter_saw: list[dict] = []
        self.schedule_calls: list[dict] = []

        engine = _make_engine(self.db)
        engine.settings.shadow_understand_enabled = bool(arm_interpreter)
        engine.settings.shadow_evidence_path = ""
        engine.settings.semantic_same_turn_enabled = True
        engine._schedule = SimpleNamespace(
            find_next_available=lambda **kw: (self.schedule_calls.append(kw), None)[1])
        original_trace = engine._trace_decision
        engine._trace_decision = lambda **kw: (self.traces.append(kw),
                                               original_trace(**kw))[1]
        self.engine = engine
        self._evidence = evidence
        self._interpret_ok = interpret_ok

    def _persist(self, wamid, text, offset):
        ts = BASE_TS + timedelta(seconds=offset)
        self.db.add(WhatsAppMessage(
            thread_id=1 if not hasattr(self, "thread_id") else self.thread_id,
            wa_message_id=wamid, direction="in", timestamp=ts, text=text,
            status="received", created_at=ts))
        self.db.commit()

    def run(self):
        saw, sent = self.interpreter_saw, self.sent
        evidence, ok = self._evidence, self._interpret_ok

        class _Interpreter:
            """The external provider. Records its input; invents no plumbing."""
            def __init__(self, settings): pass

            def interpret(self, messages, *, thread_id=None, burst_id=None,
                          message_ids=(), reconstruction=None, context=None,
                          context_hint=None):
                saw.append({"messages": list(messages),
                            "message_ids": tuple(message_ids)})
                if ok is False:
                    raise TimeoutError("provider timeout")
                return si.InterpretationResult(
                    evidence=evidence, ok=evidence is not None, latency_ms=1,
                    model="gpt-4o-mini")

        class _Sink:
            """The transport. Records the fully composed customer text."""
            def __init__(self, db): self.db = db
            def attempt(self, *, wa_id, thread_id, text, **kw):
                sent.append(text)
                return SimpleNamespace(outcome=GateOutcome.ALLOWED, message_id=None,
                                       blocked_reason=None)
            def mark_sent(self, *a, **k): return None
            def mark_failed(self, *a, **k): return None

        class _Reply:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                return json.dumps(
                    {"choices": [{"message": {"content": NEUTRAL_REPLY}}]}).encode()

        event = ConversationHandleIn(
            thread_id=self.thread_id, wa_message_id=self.trigger, wa_id=_WA_ID,
            text=self.trigger_text, unanswered_recent_user_messages=self.unanswered,
            recent_user_messages=self.unanswered)
        with patch.object(si, "SemanticTurnInterpreter", _Interpreter), \
             patch.object(ce, "OutboundSafetyGate", _Sink), \
             patch.object(ce, "_send_whatsapp_cloud_text", lambda **kw: ("wamid.G35B", {})), \
             patch("urllib.request.urlopen", lambda *a, **k: _Reply()):
            try:
                self.out = self.engine.handle(event)
            except Exception as exc:        # SQLite has no NOW(); the text is already caught
                self.out = SimpleNamespace(action=f"<raised {type(exc).__name__}>")
        return self

    # ── views ────────────────────────────────────────────────────────────────
    @property
    def burst(self):
        return self.engine._canonical_turn_burst

    @property
    def decision(self):
        return self.engine._availability_authority_cache

    @property
    def customer_text(self) -> str:
        return " ".join(self.sent)

    def names_the_weekday_table(self) -> bool:
        low = self.customer_text.lower()
        return any(m in low for m in HOURS_TABLE_MARKERS)

    @property
    def model_calls(self) -> int:
        return getattr(self.engine, "_turn_semantic_calls", 0)

    @property
    def accessor_reads(self) -> int:
        return getattr(self.engine, "_semantic_accessor_reads", 0)

    def rows_for(self, rule_id):
        return [t for t in self.traces if t.get("rule_id") == rule_id]

    def close(self):
        self.db.close()


# ══ A — the required end-to-end case ════════════════════════════════════════

class A_UnifiedBoundary(unittest.TestCase):
    """The live burst, split, with n8n reporting only the first half."""

    ROWS = ((("wamid.L0", LIVE_FIRST, 0), ("wamid.L1", LIVE_SECOND, 5)))

    def turn(self, **kw):
        return Turn(self.ROWS, unanswered=[LIVE_FIRST], trigger="wamid.L1",
                    evidence=availability_evidence(), **kw).run()

    def test_g35b_01_canonical_records_carry_both_identities_and_both_texts(self):
        t = self.turn()
        try:
            self.assertEqual(t.burst.ordered_message_ids, ("wamid.L0", "wamid.L1"))
            self.assertEqual(t.burst.texts, (LIVE_FIRST, LIVE_SECOND))
            self.assertEqual(t.burst.message_count, 2)
            self.assertEqual(len(t.burst.ordered_message_ids), len(t.burst.texts))
        finally:
            t.close()

    def test_g35b_02_the_interpreter_received_the_whole_burst_in_order(self):
        """BEFORE G3-5B the interpreter received ['Que horarios tienen'] alone."""
        t = self.turn()
        try:
            self.assertEqual(len(t.interpreter_saw), 1, "exactly one interpretation")
            self.assertEqual(t.interpreter_saw[0]["messages"], [LIVE_FIRST, LIVE_SECOND])
            self.assertEqual(t.interpreter_saw[0]["message_ids"],
                             ("wamid.L0", "wamid.L1"))
        finally:
            t.close()

    def test_g35b_03_semantic_input_equals_the_canonical_evidence(self):
        t = self.turn()
        try:
            self.assertEqual(list(t.engine._turn_semantic_texts), list(t.burst.texts))
            self.assertEqual(t.engine._turn_semantic_burst, t.burst.fingerprint)
        finally:
            t.close()

    def test_g35b_04_the_presentation_subset_defines_nothing(self):
        t = self.turn()
        try:
            self.assertEqual(t.burst.presentation_texts, (LIVE_FIRST,))
            self.assertNotEqual(t.burst.presentation_texts, t.burst.texts)
            self.assertNotIn(LIVE_SECOND, t.burst.presentation_texts)
        finally:
            t.close()

    def test_g35b_05_semantic_primacy_decides_and_nothing_is_invented(self):
        t = self.turn()
        try:
            d = t.decision
            self.assertEqual(d.semantic_status, "OK")
            self.assertEqual(d.hours_disposition, BY_AVAILABILITY)
            self.assertIn(d.authority, (LOOK_UP, PREREQ))
            self.assertTrue(d.suppress_hours)
            self.assertFalse(t.names_the_weekday_table(),
                             "opening hours reached the customer over an availability ask")
            self.assertEqual(t.schedule_calls, [], "no availability was invented")
        finally:
            t.close()

    def test_g35b_06_one_interpretation_and_one_model_call(self):
        t = self.turn()
        try:
            self.assertEqual(t.model_calls, 1, "exactly one provider invocation")
            self.assertGreaterEqual(t.accessor_reads, 1)
        finally:
            t.close()

    def test_g35b_07_both_trace_rows_name_the_same_boundary(self):
        t = self.turn()
        try:
            auth = t.rows_for("scheduling.availability_admission")
            faq = t.rows_for("faq.topic_authority")
            self.assertTrue(auth, "the authority produced no trace row")
            self.assertEqual(tuple(auth[0]["evidence_ids"]), ("wamid.L0", "wamid.L1"))
            self.assertIn(f"burst={t.burst.fingerprint[:12]}", auth[0]["business_outcome"])
            self.assertIn(f"semantic_burst={t.burst.fingerprint[:12]}",
                          auth[0]["business_outcome"])
            for row in faq:
                self.assertEqual(tuple(row["evidence_ids"]), ("wamid.L0", "wamid.L1"))
                self.assertIn(f"burst={t.burst.fingerprint[:12]}", row["business_outcome"])
        finally:
            t.close()

    def test_g35b_08_the_persisted_timestamps_decide_the_order(self):
        """Rows arrive in insertion order; the canonical order follows the clock.

        Written in insertion order (so the DB range recovers both) but with the SECOND row
        timestamped earlier — the clock skew an out-of-order webhook produces. The canonical
        burst, the interpreter's input and the trace identifiers must all follow the
        timestamps, not the row ids.
        """
        t = Turn((("wamid.LA", LIVE_SECOND, 30), ("wamid.LB", LIVE_FIRST, 10)),
                 unanswered=[LIVE_FIRST], trigger="wamid.LB",
                 evidence=availability_evidence()).run()
        try:
            self.assertEqual(t.burst.ordered_message_ids, ("wamid.LB", "wamid.LA"))
            self.assertEqual(t.burst.texts, (LIVE_FIRST, LIVE_SECOND))
            self.assertEqual(t.interpreter_saw[0]["messages"], [LIVE_FIRST, LIVE_SECOND])
            self.assertEqual(t.interpreter_saw[0]["message_ids"],
                             ("wamid.LB", "wamid.LA"))
        finally:
            t.close()

    def test_g35b_09_a_mismatched_interpretation_cannot_hold_primacy(self):
        """Fail-safe: evidence bound to another burst is treated as unavailable."""
        t = self.turn()
        try:
            t.engine._turn_semantic_burst = "0" * 64        # some other burst
            t.engine._availability_authority_phases = {}
            t.engine._availability_authority_cache = None
            reading = t.engine._semantic_reading()
            self.assertEqual(reading.status, "UNAVAILABLE")
            self.assertIsNone(reading.availability)
        finally:
            t.close()


# ══ B — the duplicate-evidence matrix ══════════════════════════════════════

class B_DuplicateEvidence(unittest.TestCase):
    """Identity is the deduplication key. Text never is."""

    def test_g35b_10_same_identity_delivered_twice_is_one_message(self):
        msgs = _canonical_messages(
            db_rows=[SimpleNamespace(wa_message_id="w1", id=1, text="Hola", timestamp=None)],
            event_wamid="w1", event_text="Hola", presentation_texts=["Hola"])
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].message_id, "w1")

    def test_g35b_11_one_message_reported_with_different_formatting_is_one_record(self):
        """REWRITTEN by G3-5C (audit G5B-04). The old version tested nothing.

        It varied `event_text`, which production precedence never reads when the persisted
        row carries text, and it handed the presentation loop a byte-identical copy — so it
        passed without exercising the matching step at all, and the real defect (the
        transport's copy differing from the persisted copy) went undetected.

        The variation now sits where production actually compares: `presentation_texts`.
        Against 2b5c344 this produces TWO records — the real WAMID plus a synthetic one for
        the same sentence.
        """
        for label, reported in (("trailing space", "¿Qué horarios tienen? "),
                                ("case", "¿qué horarios tienen?"),
                                ("non-breaking space", "¿Qué horarios\u00a0tienen?")):
            with self.subTest(variation=label):
                msgs = _canonical_messages(
                    db_rows=[SimpleNamespace(wa_message_id="w1", id=1,
                                             text="¿Qué horarios tienen?", timestamp=None)],
                    event_wamid="w1", event_text="¿Qué horarios tienen?",
                    presentation_texts=[reported])
                self.assertEqual(len(msgs), 1, "one real message became two records")
                self.assertEqual(msgs[0].message_id, "w1", "the real identity must survive")
                self.assertEqual(msgs[0].text, "¿Qué horarios tienen?",
                                 "the persisted text is authoritative")

    def test_g35b_12_different_identities_with_identical_text_are_two_messages(self):
        msgs = _canonical_messages(
            db_rows=[SimpleNamespace(wa_message_id="w1", id=1, text="Hola", timestamp=None),
                     SimpleNamespace(wa_message_id="w2", id=2, text="Hola", timestamp=None)],
            event_wamid="w2", event_text="Hola", presentation_texts=["Hola"])
        self.assertEqual([m.message_id for m in msgs], ["w1", "w2"])
        self.assertEqual([m.text for m in msgs], ["Hola", "Hola"])

    def test_g35b_13_three_messages_two_identical_texts_keep_all_three(self):
        """G5A-02 reproduced: this produced three ids and TWO texts before G3-5B."""
        t = Turn((("w0", "Hola.", 0), ("w1", "Hola.", 5), ("w2", HOURS_Q, 10)),
                 unanswered=["Hola.", HOURS_Q], trigger="w2",
                 evidence=availability_evidence(status=None)).run()
        try:
            self.assertEqual(t.burst.ordered_message_ids, ("w0", "w1", "w2"))
            self.assertEqual(t.burst.texts, ("Hola.", "Hola.", HOURS_Q))
            self.assertEqual(len(t.burst.ordered_message_ids), len(t.burst.texts))
            self.assertEqual(t.burst.message_count, 3)
            self.assertEqual(t.interpreter_saw[0]["message_ids"], ("w0", "w1", "w2"))
        finally:
            t.close()

    def test_g35b_14_equal_timestamps_tie_break_deterministically(self):
        rows = [SimpleNamespace(wa_message_id="wB", id=9, text="segundo", timestamp=BASE_TS),
                SimpleNamespace(wa_message_id="wA", id=4, text="primero", timestamp=BASE_TS)]
        first = _canonical_messages(db_rows=rows, presentation_texts=[])
        second = _canonical_messages(db_rows=list(reversed(rows)), presentation_texts=[])
        self.assertEqual([m.message_id for m in first], ["wA", "wB"], "row id breaks the tie")
        self.assertEqual([m.message_id for m in second], [m.message_id for m in first])

    def test_g35b_15_a_recovered_row_and_the_event_copy_are_one_message(self):
        t = Turn((("w0", "Disponibles para mañana.", 0), ("w1", HOURS_Q, 5)),
                 unanswered=[HOURS_Q], trigger="w1",
                 evidence=availability_evidence(status=None)).run()
        try:
            self.assertEqual(t.burst.ordered_message_ids, ("w0", "w1"))
            self.assertEqual(t.burst.message_count, 2)
        finally:
            t.close()

    def test_g35b_16_a_recovered_row_and_a_distinct_identical_text_are_two(self):
        t = Turn((("w0", HOURS_Q, 0), ("w1", HOURS_Q, 5)),
                 unanswered=[HOURS_Q], trigger="w1",
                 evidence=availability_evidence(status=None)).run()
        try:
            self.assertEqual(t.burst.ordered_message_ids, ("w0", "w1"))
            self.assertEqual(t.burst.texts, (HOURS_Q, HOURS_Q))
        finally:
            t.close()

    def test_g35b_17_a_message_without_an_external_id_gets_a_stable_one(self):
        a = _canonical_messages(presentation_texts=["uno", "dos"], turn_id="t-1")
        b = _canonical_messages(presentation_texts=["uno", "dos"], turn_id="t-2")
        self.assertEqual([m.message_id for m in a], [m.message_id for m in b],
                         "identity must not depend on a per-turn random value")
        self.assertEqual(len({m.message_id for m in a}), 2)
        for m in a:
            self.assertTrue(m.message_id.startswith("n8n:"), m.message_id)
            self.assertEqual(m.source, "unanswered_recent_user_messages")

    def test_g35b_18_the_presentation_subset_may_omit_a_repeated_message(self):
        t = Turn((("w0", "Hola.", 0), ("w1", "Hola.", 5), ("w2", HOURS_Q, 10)),
                 unanswered=[HOURS_Q], trigger="w2",
                 evidence=availability_evidence(status=None)).run()
        try:
            self.assertEqual(t.burst.texts, ("Hola.", "Hola.", HOURS_Q))
            self.assertEqual(t.interpreter_saw[0]["messages"],
                             ["Hola.", "Hola.", HOURS_Q])
        finally:
            t.close()

    def test_g35b_19_repeated_delivery_after_the_decision_is_idempotent(self):
        t = Turn((("w0", LIVE_FIRST, 0), ("w1", LIVE_SECOND, 5)),
                 unanswered=[LIVE_FIRST], trigger="w1",
                 evidence=availability_evidence()).run()
        try:
            before = (t.burst.fingerprint, t.decision.decision_phase_id,
                      t.decision.hours_disposition, t.model_calls, len(t.sent))
            state = SimpleNamespace(last_stage=t.burst.stage_at_build, needs_human=False)
            again = [t.engine._availability_authority(None, state, [LIVE_FIRST])
                     for _ in range(3)]
            self.assertIs(again[0], again[1])
            self.assertIs(again[1], again[2])
            after = (t.burst.fingerprint, again[0].decision_phase_id,
                     again[0].hours_disposition, t.model_calls, len(t.sent))
            self.assertEqual(before, after, "a repeated consumer changed the turn")
        finally:
            t.close()

    def test_g35b_20_the_fingerprint_binds_identity_order_and_content(self):
        base = (CanonicalMessage("w0", "uno", 0, None, "db_recovery"),
                CanonicalMessage("w1", "dos", 1, None, "db_recovery"))
        same = _canonical_burst_from_texts(texts=["uno", "dos"])
        def fp(msgs):
            return ce._canonical_burst(
                thread_id=1, turn_id=None, messages=msgs, provenance=(),
                presentation_texts=()).fingerprint
        reordered = (base[1], base[0])
        renamed = (CanonicalMessage("wX", "uno", 0, None, "db_recovery"), base[1])
        retexted = (CanonicalMessage("w0", "UNO distinto", 0, None, "db_recovery"), base[1])
        self.assertNotEqual(fp(base), fp(reordered), "order must change the digest")
        self.assertNotEqual(fp(base), fp(renamed), "identity must change the digest")
        self.assertNotEqual(fp(base), fp(retexted), "content must change the digest")
        self.assertNotEqual(fp(base), same.fingerprint, "identity is part of the digest")
        self.assertEqual(fp(base), fp(base), "and the digest is stable")
        self.assertEqual(len(fp(base)), 64)


# ══ C — the state-timing matrix ════════════════════════════════════════════

class C_DecisionPhase(unittest.TestCase):
    """One snapshot per decision phase; consumer order cannot decide."""

    TEXTS = ["¿Tenés disponibilidad?"]

    def engine_at(self, stage, *, scheduling_requested=False):
        e = ConversationEngine.__new__(ConversationEngine)
        e._availability_authority_cache = None
        e._availability_authority_phases = {}
        e._hybrid_trace_on = lambda: False
        e._trace_decision = lambda **kw: None
        e._semantic_scheduling_claims = lambda st: []
        e._topics_already_answered_this_cycle = lambda c: set()
        e._turn_scheduling_requested = scheduling_requested
        e._decision_log = lambda *a, **k: None
        e._schedule = SimpleNamespace(find_next_available=lambda **kw: None)
        e.db = SimpleNamespace(commit=lambda: None)
        e._semantic_accessor_reads = 0
        ev = availability_evidence()
        e._semantic_turn_evidence = lambda: ev
        e._turn_semantic_burst = None
        e._canonical_turn_burst = _canonical_burst_from_texts(
            thread_id=1, turn_id="t", texts=self.TEXTS, provenance=("event_evidence",))
        state = SimpleNamespace(last_stage=stage, needs_human=False)
        return e, state

    def test_g35b_21_both_consumer_orders_agree_within_one_phase(self):
        results = []
        for first in ("faq", "scheduling"):
            e, state = self.engine_at(STAGE_SCHEDULING)
            if first == "faq":
                ConversationEngine._faq_topics_for_burst(
                    e, " ".join(self.TEXTS), ctx=None, state=state)
                d = e._availability_authority(None, state, self.TEXTS)
            else:
                d = e._availability_authority(None, state, self.TEXTS)
                ConversationEngine._faq_topics_for_burst(
                    e, " ".join(self.TEXTS), ctx=None, state=state)
            results.append((d.decision_phase_id, d.state_snapshot_digest, d.authority,
                            d.hours_disposition, d.state_snapshot.stage))
        self.assertEqual(results[0], results[1], "consumer order changed the decision")

    def test_g35b_22_a_repeated_consumer_in_one_phase_gets_one_decision(self):
        e, state = self.engine_at(STAGE_SCHEDULING)
        first = e._availability_authority(None, state, self.TEXTS)
        self.assertIs(first, e._availability_authority(None, state, self.TEXTS))
        self.assertEqual(e._semantic_accessor_reads, 1, "no second reading was taken")

    def test_g35b_23_a_legitimate_update_opens_a_new_explicit_phase(self):
        """G5A-03: the old memo froze whichever stage the first consumer saw, silently."""
        e, state = self.engine_at(STAGE_QUALIFYING)
        early = e._availability_authority(None, state, self.TEXTS)
        state.last_stage = STAGE_SCHEDULING          # a handler advances the turn
        late = e._availability_authority(None, state, self.TEXTS)
        self.assertNotEqual(early.decision_phase_id, late.decision_phase_id)
        self.assertNotEqual(early.state_snapshot_digest, late.state_snapshot_digest)
        self.assertEqual(early.authority, PREREQ)
        self.assertEqual(late.authority, LOOK_UP, "the new phase sees the new stage")
        self.assertEqual(e._availability_authority_phases[early.decision_phase_id].authority,
                         PREREQ, "the earlier phase is kept, not overwritten")

    def test_g35b_24_the_only_consumer_after_an_update_agrees_with_that_new_phase(self):
        """The defect was order-dependence: these two must now reach the same verdict."""
        e1, s1 = self.engine_at(STAGE_QUALIFYING)
        e1._availability_authority(None, s1, self.TEXTS)
        s1.last_stage = STAGE_SCHEDULING
        after_transition = e1._availability_authority(None, s1, self.TEXTS)
        e2, s2 = self.engine_at(STAGE_SCHEDULING)
        only_late = e2._availability_authority(None, s2, self.TEXTS)
        self.assertEqual(after_transition.authority, only_late.authority)
        self.assertEqual(after_transition.decision_phase_id, only_late.decision_phase_id)

    def test_g35b_25_the_scheduling_flag_is_part_of_the_phase(self):
        a, sa = self.engine_at(STAGE_SCHEDULING, scheduling_requested=False)
        b, sb = self.engine_at(STAGE_SCHEDULING, scheduling_requested=True)
        da = a._availability_authority(None, sa, self.TEXTS)
        db_ = b._availability_authority(None, sb, self.TEXTS)
        self.assertNotEqual(da.state_snapshot_digest, db_.state_snapshot_digest)
        self.assertFalse(da.state_snapshot.scheduling_requested)
        self.assertTrue(db_.state_snapshot.scheduling_requested)

    def test_g35b_26_stage_variants_are_each_their_own_phase(self):
        seen = {}
        for stage in (None, STAGE_QUALIFYING, STAGE_QUOTED, STAGE_SCHEDULING, "NONSENSE"):
            with self.subTest(stage=stage):
                e, state = self.engine_at(stage)
                d = e._availability_authority(None, state, self.TEXTS)
                self.assertEqual(d.state_snapshot.stage, stage)
                seen[stage] = d.state_snapshot_digest
        self.assertEqual(len(set(seen.values())), len(seen), "digests must distinguish stage")

    def test_g35b_27_human_ownership_does_not_change_the_decision_phase(self):
        """`needs_human` is not read by the decision, so it must not open a phase."""
        e, state = self.engine_at(STAGE_SCHEDULING)
        first = e._availability_authority(None, state, self.TEXTS)
        state.needs_human = True
        self.assertIs(e._availability_authority(None, state, self.TEXTS), first)

    def test_g35b_28_no_extra_interpretation_across_phases(self):
        e, state = self.engine_at(STAGE_QUALIFYING)
        e._availability_authority(None, state, self.TEXTS)
        state.last_stage = STAGE_SCHEDULING
        e._availability_authority(None, state, self.TEXTS)
        self.assertEqual(e._semantic_accessor_reads, 2, "two readings, one interpretation")
        self.assertEqual(getattr(e, "_turn_semantic_calls", 0), 0,
                         "no provider invocation happened in this harness")


# ══ D — the customer-facing proof, through the whole handler ════════════════

class D_CustomerFacing(unittest.TestCase):
    """Captured at the transport sink, so the composer really runs (audit G5A-05)."""

    DATED = (
        ("tomorrow",      [HOURS_Q, "¿Hay algo mañana?"]),
        ("weekday",       [HOURS_Q, "¿Y el martes?"]),
        ("exact date",    [HOURS_Q, "¿El 30 de septiembre?"]),
        ("exact time",    [HOURS_Q, "¿A las 14?"]),
        ("date and time", [HOURS_Q, "¿El martes a las 14?"]),
    )

    @staticmethod
    def _rows(texts):
        return tuple((f"w{i}", t, i * 5) for i, t in enumerate(texts))

    def test_g35b_29_a_named_date_never_yields_the_weekday_table(self):
        for label, texts in self.DATED:
            for ok, mode in ((None, "no evidence"), (False, "provider timeout")):
                with self.subTest(case=label, provider=mode):
                    t = Turn(self._rows(texts), evidence=None,
                             interpret_ok=(False if ok is False else True)).run()
                    try:
                        self.assertEqual(t.decision.hours_disposition, BY_DETERMINISTIC)
                        self.assertEqual(t.decision.authority, HOLD)
                        self.assertFalse(t.names_the_weekday_table(),
                                         f"hours reached the customer for {label}")
                        self.assertEqual(t.schedule_calls, [])
                    finally:
                        t.close()

    def test_g35b_30_the_split_availability_question_yields_no_hours(self):
        t = Turn(self._rows([LIVE_FIRST, LIVE_SECOND]), unanswered=[LIVE_FIRST],
                 evidence=None).run()
        try:
            self.assertEqual(t.decision.hours_disposition, UNCORROBORATED)
            self.assertFalse(t.names_the_weekday_table())
        finally:
            t.close()

    def test_g35b_31_an_unopposed_hours_question_still_gets_canonical_hours(self):
        """The control arm. Without it every assertion above would be vacuous."""
        t = Turn((("w0", "¿Cuál es el horario de atención?", 0),), evidence=None).run()
        try:
            self.assertEqual(t.decision.hours_disposition, ADMIT)
            self.assertTrue(t.names_the_weekday_table(),
                            "the harness cannot see the hours table; the proofs are vacuous")
        finally:
            t.close()

    def test_g35b_32_two_semantic_propositions_compose_hours_exactly_once(self):
        from app.services.schedule import business_hours_summary_es
        t = Turn((("w0", "¿Cuáles son sus horarios de atención?", 0),
                  ("w1", "¿Y tienen un turno mañana?", 5)),
                 evidence=availability_evidence(faq=("business_hours",))).run()
        try:
            self.assertEqual(t.decision.hours_disposition, ADMIT)
            self.assertEqual(t.customer_text.count(business_hours_summary_es()), 1)
        finally:
            t.close()

    def test_g35b_33_a_weekday_inside_the_hours_question_keeps_its_answer(self):
        t = Turn((("w0", "cuando laburan los sabados?", 0),), evidence=None).run()
        try:
            self.assertEqual(t.decision.hours_disposition, ADMIT)
            self.assertTrue(t.names_the_weekday_table())
        finally:
            t.close()

    def test_g35b_34_every_disposition_remains_reachable(self):
        cases = (
            (ADMIT, [("w0", "¿Cuál es el horario de atención?", 0)], None),
            (BY_AVAILABILITY, [("w0", HOURS_Q, 0)], availability_evidence()),
            (BY_DETERMINISTIC, [("w0", HOURS_Q, 0), ("w1", "¿Hay algo mañana?", 5)], None),
            (AMBIGUOUS, [("w0", HOURS_Q, 0)],
             availability_evidence(status=te.EvidenceStatus.AMBIGUOUS)),
            (UNCORROBORATED, [("w0", "Que horarios tienen disponiblees", 0)], None),
            (NO_CANDIDATE, [("w0", "gracias, muy amables", 0)], None),
        )
        reached = set()
        for expected, rows, evidence in cases:
            with self.subTest(disposition=expected):
                t = Turn(tuple(rows), evidence=evidence).run()
                try:
                    self.assertEqual(t.decision.hours_disposition, expected)
                    reached.add(t.decision.hours_disposition)
                finally:
                    t.close()
        self.assertEqual(reached, set(HoursDisposition.ALL))


# ══ E — freeze and no language expansion ═══════════════════════════════════

class E_FreezeAndVocabulary(unittest.TestCase):

    def test_g35b_35_no_vocabulary_was_added(self):
        from app.services import scheduling_lexicon as lex
        from app.services.conversation_engine import _HOURS_FAQ_DETECTION
        self.assertEqual(set(_HOURS_FAQ_DETECTION), {
            "horarios", "horario", "en que horarios", "que horarios",
            "cuando laburan", "cuando trabajan", "a que hora", "horario de atencion"})
        self.assertEqual(lex.OPERATION_PREDICATES, frozenset({
            "hacen", "hacian", "tienen", "trabajan", "laburan", "atienden",
            "abren", "cierran", "atencion"}))
        self.assertEqual(lex.AVAILABILITY_OBJECTS, frozenset({
            "disponible", "disponibles", "disponibilidad", "libre", "libres",
            "vacante", "vacantes", "cupo", "cupos"}))

    def test_g35b_36_the_model_and_contracts_are_frozen(self):
        from app.schemas.hybrid_trace import TRACE_VERSION
        interp = (ROOT / "backend/app/services/semantic_interpreter.py").read_text(
            encoding="utf-8")
        self.assertIn('or "gpt-4o-mini"', interp)
        self.assertIn('PROMPT_VERSION = "understand/1.19"', interp)
        self.assertEqual(te.SCHEMA_VERSION, "turn-evidence/1.3")
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.4")

    def test_g35b_37_no_customer_text_reaches_the_trace_metadata(self):
        t = Turn((("w0", HOURS_Q, 0), ("w1", "¿Hay algo mañana?", 5)), evidence=None).run()
        try:
            blob = json.dumps([{k: str(v) for k, v in row.items()} for row in t.traces])
            for fragment in ("horarios tienen", "Hay algo", "mañana?"):
                self.assertNotIn(fragment, blob, f"{fragment!r} leaked into the trace")
        finally:
            t.close()


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
