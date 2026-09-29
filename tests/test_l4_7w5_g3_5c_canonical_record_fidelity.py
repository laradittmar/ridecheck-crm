"""L4.7W5-GATE3-G3-5C — the canonical collection is faithful to the real messages.

The independent G3-5B review found the unified boundary architecturally sound and the
records it distributes unfaithful in three ways, plus one test that hid the first:

  G5B-01 MEDIUM  the presentation-to-persisted match used raw string equality, so one real
                 message became two canonical records whenever the transport's copy differed
                 by whitespace, case or Unicode form — a phantom duplicate carrying a
                 synthetic identity beside the real WAMID.
  G5B-02 MEDIUM  every untimestamped record was forced behind every timestamped one, so a
                 message the customer spoke FIRST could be placed last.
  G5B-03 MEDIUM  the fallback identity embedded the absolute list position, so the same
                 message changed identity when the transport reported a different subset.
  G5B-04 MEDIUM  `test_g35b_11` varied a value production precedence never reads, so it
                 passed without exercising the matching step. Rewritten in its own file.
  G5B-05 LOW     a missing semantic binding was accepted rather than refused.

Nothing here adds vocabulary, a phrase rule or a heuristic. The matching normalization is the
project's own `normalize_burst` — the same function the fingerprint uses, so identity and
digest cannot disagree.
"""
from __future__ import annotations

import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from test_l4_7w5_g3_5b_unified_evidence_boundary import (                # noqa: E402
    ADMIT, AMBIGUOUS, BY_AVAILABILITY, BY_DETERMINISTIC, CLARIFY, CONFIRMED, HOLD, LOOK_UP,
    NO_CANDIDATE, PREREQ, UNCORROBORATED, HOURS_Q, LIVE_FIRST, LIVE_SECOND, Turn,
    availability_evidence,
)
from app.schemas import turn_evidence as te                              # noqa: E402
from app.schemas.hybrid_trace import HoursDisposition                    # noqa: E402
from app.services.conversation_engine import (                           # noqa: E402
    ConversationEngine, _canonical_messages, _canonical_burst_from_texts,
    STAGE_QUALIFYING, STAGE_SCHEDULING,
)
import app.services.conversation_engine as _ce                           # noqa: E402

#: Resolved at call time, and deliberately tolerant of its absence: this suite must be able
#: to COLLECT against the parent commit so its behavioural assertions can fail there, which
#: is the evidence that they are real. A hard import would turn parent-failure proof into a
#: collection error and prove nothing.
def _norm_message(text):
    fn = getattr(_ce, "_norm_message", None)
    if fn is None:                       # parent commit: no normalization step existed
        from app.schemas.hybrid_trace import normalize_burst
        return normalize_burst([text])
    return fn(text)

BASE = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)
Q = HOURS_Q


def row(wamid, text, *, rid=1, offset=None):
    """One persisted `whatsapp_messages` row, as the merge reads it."""
    return SimpleNamespace(wa_message_id=wamid, id=rid, text=text,
                           timestamp=(BASE + timedelta(seconds=offset)
                                      if offset is not None else None))


# ══ A — identity and deduplication ═════════════════════════════════════════

class A_Identity(unittest.TestCase):
    """Identity decides. Normalized text only ever MATCHES; it never erases."""

    def test_g35c_01_same_wamid_twice_identical_text(self):
        m = _canonical_messages(db_rows=[row("w1", "Hola", offset=0)], event_wamid="w1",
                                event_text="Hola", presentation_texts=["Hola"])
        self.assertEqual([(x.message_id, x.text) for x in m], [("w1", "Hola")])

    def test_g35c_02_03_04_formatting_variations_are_one_record(self):
        """G5B-01. Against 2b5c344 each of these produced TWO records."""
        for label, reported in (("trailing space", Q + " "),
                                ("leading space", " " + Q),
                                ("case", Q.lower()),
                                ("non-breaking space", Q.replace(" ", " ")),
                                ("collapsed double space", Q.replace(" ", "  ")),
                                ("accent stripped", "¿Que horarios tienen?")):
            with self.subTest(variation=label):
                m = _canonical_messages(db_rows=[row("w1", Q, offset=0)], event_wamid="w1",
                                        event_text=Q, presentation_texts=[reported])
                self.assertEqual(len(m), 1, f"{label} produced a phantom duplicate")
                self.assertEqual(m[0].message_id, "w1")
                self.assertEqual(m[0].text, Q, "the persisted text is authoritative")
        self.assertEqual(_norm_message(Q + " "), _norm_message(Q),
                         "the matching normalization is the project's own")

    def test_g35c_05_same_identity_materially_different_payload(self):
        m = _canonical_messages(
            db_rows=[row("w1", "Hola", offset=0)], event_wamid="w1",
            event_text="En realidad quiero un turno el martes", presentation_texts=["Hola"])
        self.assertEqual(len(m), 1, "a payload conflict must not create a second message")
        self.assertEqual(m[0].text, "Hola", "the persisted payload wins")
        self.assertEqual(len(m.conflicts), 1)
        c = m.conflicts[0]
        self.assertEqual((c["selected"], c["discarded"]), ("persisted", "event_payload"))
        self.assertEqual(len(c["selected_digest"]), 12)
        long_id = "wamid.HBgLNTQ5MTEyMzQ1Njc4FQIAEhggQkxNVFdaWDdK"
        m2 = _canonical_messages(
            db_rows=[row(long_id, "Hola", offset=0)], event_wamid=long_id,
            event_text="otra cosa entera", presentation_texts=["Hola"])
        tail = m2.conflicts[0]["identity_tail"]
        self.assertEqual(len(tail), 6, "the tail is bounded")
        self.assertTrue(long_id.endswith(tail), "and it is the tail")
        self.assertLess(len(tail), len(long_id), "never the full WAMID")

    def test_g35c_05b_the_conflict_record_carries_no_customer_text(self):
        m = _canonical_messages(
            db_rows=[row("w1", "Hola", offset=0)], event_wamid="w1",
            event_text="quiero un turno el martes", presentation_texts=["Hola"])
        blob = repr(m.conflicts)
        for fragment in ("Hola", "turno", "martes"):
            self.assertNotIn(fragment, blob, f"{fragment!r} leaked into the conflict record")

    def test_g35c_06_different_wamids_identical_text(self):
        m = _canonical_messages(
            db_rows=[row("w1", "Hola", rid=1, offset=0), row("w2", "Hola", rid=2, offset=5)],
            event_wamid="w2", event_text="Hola", presentation_texts=["Hola"])
        self.assertEqual([x.message_id for x in m], ["w1", "w2"])
        self.assertEqual([x.text for x in m], ["Hola", "Hola"])

    def test_g35c_07_three_wamids_two_identical_texts(self):
        m = _canonical_messages(
            db_rows=[row("w0", "Hola.", rid=1, offset=0), row("w1", "Hola.", rid=2, offset=5),
                     row("w2", Q, rid=3, offset=10)],
            event_wamid="w2", event_text=Q, presentation_texts=["Hola.", Q])
        self.assertEqual([x.message_id for x in m], ["w0", "w1", "w2"])
        self.assertEqual([x.text for x in m], ["Hola.", "Hola.", Q])

    def test_g35c_08_two_repeated_messages_without_wamids(self):
        m = _canonical_messages(presentation_texts=["igual", "igual"])
        self.assertEqual(len(m), 2, "a repeated message must not be collapsed")
        self.assertEqual(len({x.message_id for x in m}), 2)
        self.assertEqual([x.text for x in m], ["igual", "igual"])

    def test_g35c_09_null_persisted_text_plus_transcript(self):
        m = _canonical_messages(db_rows=[row("w1", None, offset=0)], event_wamid="w1",
                                event_text="transcripción del audio",
                                presentation_texts=["transcripción del audio"])
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0].message_id, "w1", "the persisted identity is retained")
        self.assertEqual(m[0].text, "transcripción del audio")
        self.assertEqual(m[0].order_basis, "timestamp", "and so is its clock")

    def test_g35c_10_db_row_plus_n8n_copy_without_an_id(self):
        m = _canonical_messages(db_rows=[row("w1", Q, offset=0)], presentation_texts=[Q])
        self.assertEqual([x.message_id for x in m], ["w1"])

    def test_g35c_11_db_row_plus_a_distinct_message_with_identical_text(self):
        """The transport reported the sentence TWICE; only one is persisted, so there are two."""
        m = _canonical_messages(db_rows=[row("w1", Q, offset=0)], presentation_texts=[Q, Q])
        self.assertEqual(len(m), 2)
        self.assertEqual(m[0].message_id, "w1")
        self.assertTrue(m[1].message_id.startswith("n8n:"), m[1].message_id)

    def test_g35c_11b_a_persisted_row_without_a_wamid_uses_its_database_id(self):
        m = _canonical_messages(db_rows=[row(None, "Hola", rid=77, offset=0)])
        self.assertEqual([x.message_id for x in m], ["db:77"])


# ══ B — stable fallback identity ═══════════════════════════════════════════

class B_FallbackIdentity(unittest.TestCase):
    """G5B-03. Identity must not move because the transport reported a different slice."""

    @staticmethod
    def _id_of(text, presentation):
        m = _canonical_messages(presentation_texts=presentation)
        return next(x.message_id for x in m if x.text == text)

    def test_g35c_12_same_message_in_different_subsets(self):
        ids = {self._id_of("dos", p) for p in (["uno", "dos"], ["dos"], ["dos", "tres"],
                                               ["uno", "dos", "tres"])}
        self.assertEqual(len(ids), 1, f"identity moved with the subset: {ids}")

    def test_g35c_13_an_unrelated_earlier_message_is_inserted(self):
        self.assertEqual(self._id_of("dos", ["uno", "dos"]),
                         self._id_of("dos", ["cero", "uno", "dos"]))

    def test_g35c_14_an_unrelated_message_is_removed(self):
        self.assertEqual(self._id_of("dos", ["cero", "uno", "dos"]),
                         self._id_of("dos", ["dos"]))

    def test_g35c_15_two_identical_messages_keep_distinct_stable_identities(self):
        a = _canonical_messages(presentation_texts=["x", "igual", "igual"])
        b = _canonical_messages(presentation_texts=["igual", "igual"])
        ids_a = [m.message_id for m in a if m.text == "igual"]
        ids_b = [m.message_id for m in b if m.text == "igual"]
        self.assertEqual(ids_a, ids_b, "occurrence identity moved with the subset")
        self.assertEqual(len(set(ids_a)), 2, "the repeat must stay distinct")

    def test_g35c_16_the_fingerprint_survives_a_presentation_position_change(self):
        """The same two real messages, reported with and without the persisted one."""
        from app.services.conversation_engine import _canonical_burst

        def burst(presentation):
            msgs = _canonical_messages(db_rows=[row("w9", "ancla", offset=0)],
                                       presentation_texts=presentation)
            return _canonical_burst(thread_id=1, turn_id=None, messages=msgs,
                                    provenance=(), presentation_texts=presentation)

        with_anchor = burst(["ancla", "suelto"])
        without = burst(["suelto"])
        self.assertEqual(with_anchor.ordered_message_ids, without.ordered_message_ids,
                         "the synthetic identity moved because the subset did")
        self.assertEqual(with_anchor.fingerprint, without.fingerprint,
                         "the canonical fingerprint moved because the subset did")

    def test_g35c_16b_no_identity_carries_customer_text(self):
        m = _canonical_messages(presentation_texts=["quiero un turno el martes"])
        self.assertTrue(m[0].message_id.startswith("n8n:"))
        for fragment in ("turno", "martes", "quiero"):
            self.assertNotIn(fragment, m[0].message_id)


# ══ C — ordering ═══════════════════════════════════════════════════════════

class C_Ordering(unittest.TestCase):

    def test_g35c_17_two_persisted_messages_order_chronologically(self):
        m = _canonical_messages(db_rows=[row("wB", "segundo", rid=2, offset=30),
                                         row("wA", "primero", rid=1, offset=10)])
        self.assertEqual([x.message_id for x in m], ["wA", "wB"])
        self.assertEqual([x.order_basis for x in m], ["timestamp", "timestamp"])

    def test_g35c_18_equal_timestamps_are_stable(self):
        rows = [row("wB", "b", rid=9, offset=0), row("wA", "a", rid=4, offset=0)]
        first = [x.message_id for x in _canonical_messages(db_rows=rows)]
        second = [x.message_id for x in _canonical_messages(db_rows=list(reversed(rows)))]
        self.assertEqual(first, ["wA", "wB"], "row id breaks the tie")
        self.assertEqual(first, second, "the tie-break must not depend on input order")

    def test_g35c_19_an_untimestamped_message_reported_first_stays_first(self):
        """G5B-02. Against 2b5c344 this landed LAST, contradicting the customer."""
        m = _canonical_messages(
            db_rows=[row("w1", Q, offset=30)], event_wamid="w1", event_text=Q,
            presentation_texts=["Primero: quiero un turno mañana.", Q])
        self.assertEqual([x.text for x in m], ["Primero: quiero un turno mañana.", Q])
        self.assertEqual([x.order_basis for x in m], ["upstream_position", "timestamp"])

    def test_g35c_20_an_untimestamped_message_reported_last_stays_last(self):
        m = _canonical_messages(
            db_rows=[row("w0", "Hola.", offset=0)], event_wamid="w0", event_text="Hola.",
            presentation_texts=["Hola.", "Y después quiero un turno."])
        self.assertEqual([x.text for x in m], ["Hola.", "Y después quiero un turno."])

    def test_g35c_20b_an_untimestamped_message_between_two_persisted_stays_between(self):
        m = _canonical_messages(
            db_rows=[row("w0", "uno", rid=1, offset=0), row("w2", "tres", rid=2, offset=60)],
            presentation_texts=["uno", "dos", "tres"])
        self.assertEqual([x.text for x in m], ["uno", "dos", "tres"])
        self.assertEqual([x.order_basis for x in m],
                         ["timestamp", "upstream_position", "timestamp"])

    def test_g35c_21_reversing_the_row_container_changes_nothing(self):
        rows = [row("w0", "uno", rid=1, offset=0), row("w1", "dos", rid=2, offset=30)]
        a = _canonical_messages(db_rows=rows, presentation_texts=["uno", "dos"])
        b = _canonical_messages(db_rows=list(reversed(rows)),
                                presentation_texts=["uno", "dos"])
        self.assertEqual([x.message_id for x in a], [x.message_id for x in b])
        self.assertEqual([x.text for x in a], [x.text for x in b])

    def test_g35c_22_the_interpreter_receives_the_supported_order(self):
        t = Turn((("w1", Q, 30),), unanswered=["¿Hay algo mañana?", Q], trigger="w1",
                 evidence=availability_evidence()).run()
        try:
            self.assertEqual(list(t.burst.texts), ["¿Hay algo mañana?", Q])
            self.assertEqual(t.interpreter_saw[0]["messages"], ["¿Hay algo mañana?", Q])
            self.assertEqual(t.interpreter_saw[0]["message_ids"], t.burst.ordered_message_ids)
        finally:
            t.close()

    def test_g35c_23_uncertainty_is_named_never_fabricated(self):
        m = _canonical_messages(db_rows=[row("w1", Q, offset=30)],
                                presentation_texts=["suelto", Q])
        loose = next(x for x in m if x.text == "suelto")
        self.assertIsNone(loose.timestamp, "no timestamp may be invented")
        self.assertEqual(loose.order_basis, "upstream_position")
        proven = next(x for x in m if x.text == Q)
        self.assertIsNotNone(proven.timestamp)
        self.assertEqual(proven.order_basis, "timestamp")


# ══ D — boundary enforcement ═══════════════════════════════════════════════

class D_Boundary(unittest.TestCase):

    def turn(self, **kw):
        return Turn((("w0", LIVE_FIRST, 0), ("w1", LIVE_SECOND, 5)),
                    unanswered=[LIVE_FIRST], trigger="w1", **kw).run()

    def test_g35c_24_the_provider_receives_exactly_the_canonical_texts_and_ids(self):
        t = self.turn(evidence=availability_evidence())
        try:
            self.assertEqual(len(t.interpreter_saw), 1)
            self.assertEqual(t.interpreter_saw[0]["messages"], list(t.burst.texts))
            self.assertEqual(t.interpreter_saw[0]["message_ids"], t.burst.ordered_message_ids)
        finally:
            t.close()

    def test_g35c_25_a_correct_binding_admits_the_evidence(self):
        t = self.turn(evidence=availability_evidence())
        try:
            self.assertEqual(t.engine._turn_semantic_burst, t.burst.fingerprint)
            reading = t.engine._semantic_reading()
            self.assertEqual(reading.status, "OK")
            self.assertIsNotNone(reading.availability)
        finally:
            t.close()

    def test_g35c_26_a_foreign_binding_fails_closed(self):
        t = self.turn(evidence=availability_evidence())
        try:
            t.engine._turn_semantic_burst = "f" * 64
            reading = t.engine._semantic_reading()
            self.assertEqual(reading.status, "UNAVAILABLE")
            self.assertIsNone(reading.availability)
            self.assertEqual(reading.faq_topics, frozenset())
        finally:
            t.close()

    def test_g35c_27_a_missing_binding_fails_closed(self):
        """G5B-05. `None` used to be accepted; it is now the same answer as a foreign one."""
        t = self.turn(evidence=availability_evidence())
        try:
            self.assertTrue(getattr(t.engine, "_turn_semantic_dispatched", False))
            t.engine._turn_semantic_burst = None
            reading = t.engine._semantic_reading()
            self.assertEqual(reading.status, "UNAVAILABLE")
            self.assertIsNone(reading.availability)
        finally:
            t.close()

    def test_g35c_28_repeated_reads_cost_one_interpretation_and_one_model_call(self):
        t = self.turn(evidence=availability_evidence())
        try:
            before = t.model_calls
            for _ in range(4):
                t.engine._semantic_reading()
            self.assertEqual(t.model_calls, before, "an accessor read called the provider")
            self.assertEqual(t.model_calls, 1)
            self.assertGreater(t.engine._semantic_accessor_reads, 4)
        finally:
            t.close()

    def test_g35c_29_a_rejected_binding_costs_no_model_call(self):
        t = self.turn(evidence=availability_evidence())
        try:
            before = t.model_calls
            t.engine._turn_semantic_burst = "0" * 64
            for _ in range(3):
                self.assertEqual(t.engine._semantic_reading().status, "UNAVAILABLE")
            self.assertEqual(t.model_calls, before)
        finally:
            t.close()

    def test_g35c_30_engine_authority_and_trace_share_one_identity(self):
        t = Turn((("w0", Q, 0), ("w1", "¿Hay algo mañana?", 5)), evidence=None).run()
        try:
            ids = t.burst.ordered_message_ids
            self.assertEqual(tuple(t.decision.evidence_ids), ids)
            self.assertEqual(t.decision.burst_fingerprint, t.burst.fingerprint)
            for rule in ("scheduling.availability_admission", "faq.topic_authority"):
                rows_ = t.rows_for(rule)
                self.assertTrue(rows_, f"{rule} produced no row")
                for r in rows_:
                    self.assertEqual(tuple(r["evidence_ids"]), ids)
                    self.assertIn(f"burst={t.burst.fingerprint[:12]}",
                                  r["business_outcome"])
        finally:
            t.close()

    def test_g35c_30b_the_trace_names_the_merge_evidence(self):
        t = Turn((("w1", Q, 0),), unanswered=[Q + " "], trigger="w1", evidence=None).run()
        try:
            auth = t.rows_for("scheduling.availability_admission")[0]["business_outcome"]
            self.assertIn("payload_conflicts=0", auth)
            self.assertIn("format_variations=1", auth)
            self.assertIn("order_basis=timestamp", auth)
            self.assertEqual(t.burst.message_count, 1, "the variation stayed one message")
        finally:
            t.close()


# ══ E — the customer behaviour this must not disturb ═══════════════════════

class E_CustomerBehaviour(unittest.TestCase):
    """Captured at the transport sink with the real composer running."""

    @staticmethod
    def _rows(texts):
        return tuple((f"w{i}", t, i * 5) for i, t in enumerate(texts))

    def test_g35c_31_f01_remains_closed_at_the_payload(self):
        for label, texts in (("tomorrow", [Q, "¿Hay algo mañana?"]),
                             ("weekday", [Q, "¿Y el martes?"]),
                             ("exact date", [Q, "¿El 30 de septiembre?"]),
                             ("exact time", [Q, "¿A las 14?"])):
            with self.subTest(case=label):
                t = Turn(self._rows(texts), evidence=None).run()
                try:
                    self.assertEqual(t.decision.hours_disposition, BY_DETERMINISTIC)
                    self.assertFalse(t.names_the_weekday_table())
                    self.assertEqual(t.schedule_calls, [])
                finally:
                    t.close()

    def test_g35c_32_f02_remains_closed_with_real_recovery(self):
        t = Turn((("w0", "Disponibles para mañana.", 0), ("w1", Q, 5)),
                 unanswered=[Q], trigger="w1", evidence=None).run()
        try:
            self.assertEqual(t.burst.texts, ("Disponibles para mañana.", Q))
            self.assertEqual(t.decision.hours_disposition, UNCORROBORATED)
            self.assertFalse(t.names_the_weekday_table())
        finally:
            t.close()

    def test_g35c_33_the_split_form_matches_the_single_message_form(self):
        split = Turn((("w0", LIVE_FIRST, 0), ("w1", LIVE_SECOND, 5)),
                     unanswered=[LIVE_FIRST], trigger="w1", evidence=None).run()
        single = Turn((("w0", f"{LIVE_FIRST} {LIVE_SECOND}", 0),), evidence=None).run()
        try:
            self.assertEqual(split.decision.hours_disposition,
                             single.decision.hours_disposition)
            self.assertEqual(split.names_the_weekday_table(),
                             single.names_the_weekday_table())
            self.assertFalse(split.names_the_weekday_table())
        finally:
            split.close(); single.close()

    def test_g35c_34_an_unopposed_hours_question_is_the_positive_control(self):
        t = Turn((("w0", "¿Cuál es el horario de atención?", 0),), evidence=None).run()
        try:
            self.assertEqual(t.decision.hours_disposition, ADMIT)
            self.assertTrue(t.names_the_weekday_table(),
                            "the harness cannot see the table; every negative is vacuous")
        finally:
            t.close()

    def test_g35c_35_two_propositions_still_answer_hours_once(self):
        from app.services.schedule import business_hours_summary_es
        t = Turn((("w0", "¿Cuáles son sus horarios de atención?", 0),
                  ("w1", "¿Y tienen un turno mañana?", 5)),
                 evidence=availability_evidence(faq=("business_hours",))).run()
        try:
            self.assertEqual(t.decision.hours_disposition, ADMIT)
            self.assertEqual(t.customer_text.count(business_hours_summary_es()), 1)
        finally:
            t.close()

    def test_g35c_36_no_canonical_state_is_invented(self):
        for texts, ev in (([Q, "¿Hay algo mañana?"], None),
                          ([Q], availability_evidence()),
                          (["¿Cuál es el horario de atención?"], None)):
            with self.subTest(burst=texts[-1][:18]):
                t = Turn(self._rows(texts), evidence=ev).run()
                try:
                    self.assertEqual(t.schedule_calls, [], "availability was invented")
                    self.assertEqual(
                        t.db.execute(__import__("sqlalchemy").text(
                            "select count(*) from thread_revisions")).scalar(), 0,
                        "a revision was created")
                    self.assertEqual(
                        t.db.execute(__import__("sqlalchemy").text(
                            "select count(*) from whatsapp_messages "
                            "where direction='out'")).scalar(), 0,
                        "an outbound row was written outside the controlled sink")
                finally:
                    t.close()

    def test_g35c_37_every_disposition_is_still_reachable(self):
        cases = ((ADMIT, [("w0", "¿Cuál es el horario de atención?", 0)], None),
                 (BY_AVAILABILITY, [("w0", Q, 0)], availability_evidence()),
                 (BY_DETERMINISTIC, [("w0", Q, 0), ("w1", "¿Hay algo mañana?", 5)], None),
                 (AMBIGUOUS, [("w0", Q, 0)],
                  availability_evidence(status=te.EvidenceStatus.AMBIGUOUS)),
                 (UNCORROBORATED, [("w0", "Que horarios tienen disponiblees", 0)], None),
                 (NO_CANDIDATE, [("w0", "gracias, muy amables", 0)], None))
        reached = set()
        for expected, rows_, ev in cases:
            with self.subTest(disposition=expected):
                t = Turn(tuple(rows_), evidence=ev).run()
                try:
                    self.assertEqual(t.decision.hours_disposition, expected)
                    reached.add(expected)
                finally:
                    t.close()
        self.assertEqual(reached, set(HoursDisposition.ALL))


# ══ F — freeze ═════════════════════════════════════════════════════════════

class F_Freeze(unittest.TestCase):

    def test_g35c_38_no_vocabulary_was_added(self):
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

    def test_g35c_39_the_model_and_contracts_are_frozen(self):
        from app.schemas.hybrid_trace import TRACE_VERSION
        interp = (ROOT / "backend/app/services/semantic_interpreter.py").read_text(
            encoding="utf-8")
        self.assertIn('or "gpt-4o-mini"', interp)
        self.assertIn('PROMPT_VERSION = "understand/1.19"', interp)
        self.assertEqual(te.SCHEMA_VERSION, "turn-evidence/1.3")
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.5")

    def test_g35c_40_the_matching_normalization_is_the_projects_own(self):
        from app.schemas.hybrid_trace import normalize_burst
        for text in (Q, " " + Q + " ", Q.upper(), "quiero un turno"):
            self.assertEqual(_norm_message(text), normalize_burst([text]),
                             "a second normalization would let identity and digest diverge")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
