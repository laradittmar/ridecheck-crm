"""L4.7W5-GATE3-G3-7F clarification lifecycle — the database-enforced one-attempt invariant.

The clarification-bounding family failed three times as application logic. Each time the
fact "this family already had its attempt" shared a boolean with "a Flow is outstanding",
was written from several places, and was cleared by a completion or a handoff. The final
independent review reproduced four Vehicle Flows in one cycle from a single partial
submission.

This suite proves the replacement. The classes that matter most are `C_RealPostgres` and
`D_Concurrency`: a unique constraint and a two-session race cannot be demonstrated on
SQLite, so those tests require a real PostgreSQL instance and SKIP rather than pretend when
one is not configured (`CLARIFICATION_PG_URL`). Nothing here claims concurrency proof from
SQLite.
"""
from __future__ import annotations

import inspect
import os
import pathlib
import sys
import unittest
import uuid
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import app.services.conversation_engine as ce                            # noqa: E402
from app.services import clarification_attempt as ca                     # noqa: E402
from app.services import clarification_router as cr                      # noqa: E402
from app.services.conversation_engine import ConversationEngine as E     # noqa: E402

ENGINE_SRC = pathlib.Path(inspect.getsourcefile(E)).read_text(encoding="utf-8")


def code_only(fn) -> str:
    """A function's source with docstrings and comments removed.

    Several of these docstrings quote the defect being fixed, so they legitimately contain
    the very symbols the checks forbid. A check about executable behaviour reads code.
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
PG_URL = os.environ.get("CLARIFICATION_PG_URL", "")
Route, Why = cr.ClarificationRoute, cr.ClarificationReason
Fam, St = ca.ClarificationFamily, ca.AttemptStatus


# ══ A — identity: cycle key and idempotency key ═══════════════════════════════════

class A_Identity(unittest.TestCase):

    def test_cl_01_cycle_key_is_the_integer_column_or_zero(self):
        """The live database has this column NULL on every thread, so the COALESCE is what
        makes the key usable in a uniqueness constraint at all."""
        self.assertEqual(ca.cycle_key(SimpleNamespace()), 0)
        self.assertEqual(
            ca.cycle_key(SimpleNamespace(current_cycle_start_message_db_id=None)), 0)
        self.assertEqual(
            ca.cycle_key(SimpleNamespace(current_cycle_start_message_db_id=4217)), 4217)

    def test_cl_02_a_timestamp_can_never_become_the_cycle_key(self):
        """The old `_reconciler_cycle_id()` fell back to `current_cycle_started_at`, mixing a
        message id and a timestamp into one string. This reads the integer only."""
        from datetime import datetime, timezone
        st = SimpleNamespace(current_cycle_start_message_db_id=None,
                             current_cycle_started_at=datetime.now(timezone.utc))
        self.assertEqual(ca.cycle_key(st), 0)
        body = code_only(ca.cycle_key)
        self.assertNotIn("current_cycle_started_at", body)
        self.assertNotIn("_reconciler_cycle_id", body)

    def test_cl_03_the_idempotency_key_is_deterministic_and_36_chars(self):
        a = ca.idempotency_key(7, 0, Fam.VEHICLE)
        b = ca.idempotency_key(7, 0, Fam.VEHICLE)
        self.assertEqual(a, b, "same identity must give the same key")
        self.assertEqual(len(a), 36, "must fit correlation_id String(36)")
        uuid.UUID(a)                                   # parses as a canonical UUID

    def test_cl_04_it_differs_per_family_thread_and_cycle(self):
        base = ca.idempotency_key(7, 0, Fam.VEHICLE)
        for other in (ca.idempotency_key(7, 0, Fam.LOCATION),
                      ca.idempotency_key(8, 0, Fam.VEHICLE),
                      ca.idempotency_key(7, 1, Fam.VEHICLE)):
            with self.subTest(other=other):
                self.assertNotEqual(base, other)

    def test_cl_05_it_fits_every_destination_width(self):
        """The architecture review proposed a 48-character digest; the widths were checked
        rather than assumed, and `correlation_id` is String(36) in two tables."""
        from app.models import WhatsAppMessage, SecurityEvent
        for model in (WhatsAppMessage, SecurityEvent):
            col = model.__table__.c.get("correlation_id")
            if col is None:
                continue
            with self.subTest(table=model.__tablename__):
                self.assertEqual(col.type.length, 36)
                self.assertLessEqual(len(ca.idempotency_key(999999, 123456, Fam.LOCATION)),
                                     col.type.length)

    def test_cl_06_the_key_carries_no_time_randomness_or_customer_data(self):
        body = code_only(ca.idempotency_key)
        for token in ("datetime", "time", "random", "token_", "uuid4", "wa_id"):
            with self.subTest(token=token):
                self.assertNotIn(token, body)


# ══ B — the state machine ═════════════════════════════════════════════════════════

class B_StateMachine(unittest.TestCase):

    def test_cl_07_every_status_has_a_transition_rule(self):
        self.assertEqual(set(ca.ALLOWED_TRANSITIONS), set(St.ALL))

    def test_cl_08_reserved_is_terminal_for_automation(self):
        """A row still RESERVED on a later turn means the process died between the
        reservation and the dispatch. We may already have sent a Flow we cannot account
        for, so the honest reading is that the attempt is spent."""
        self.assertIn(St.RESERVED, St.TERMINAL_FOR_AUTOMATION)

    def test_cl_09_every_failure_and_non_resolving_outcome_is_terminal(self):
        for status in (St.COMPLETED_UNRESOLVED, St.INVALID_COMPLETION,
                       St.DISPATCH_BLOCKED, St.DISPATCH_FAILED, St.HANDED_OFF):
            with self.subTest(status=status):
                self.assertIn(status, St.TERMINAL_FOR_AUTOMATION)

    def test_cl_10_only_a_resolving_completion_is_not_terminal(self):
        self.assertNotIn(St.COMPLETED_RESOLVED, St.TERMINAL_FOR_AUTOMATION)
        self.assertIn(St.COMPLETED_RESOLVED, St.RESOLVING)

    def test_cl_11_no_transition_leads_back_to_an_available_attempt(self):
        """Nothing may return a family to "not attempted" — there is no such status, and no
        transition deletes the row."""
        for target in ca.ALLOWED_TRANSITIONS.values():
            for status in target:
                with self.subTest(status=status):
                    self.assertIn(status, St.ALL)
        body = code_only(ca.ClarificationAttemptStore).lower()
        self.assertNotIn("delete", body)

    def test_cl_12_terminal_states_allow_no_further_automation(self):
        self.assertEqual(ca.ALLOWED_TRANSITIONS[St.COMPLETED_RESOLVED], ())
        self.assertEqual(ca.ALLOWED_TRANSITIONS[St.HANDED_OFF], ())

    def test_cl_13_is_terminal_for_automation_reads_the_row(self):
        self.assertFalse(ca.is_terminal_for_automation(None))
        self.assertTrue(ca.is_terminal_for_automation(
            SimpleNamespace(status=St.COMPLETED_UNRESOLVED)))
        self.assertFalse(ca.is_terminal_for_automation(
            SimpleNamespace(status=St.DISPATCHED)))


# ══ C — real PostgreSQL: the constraint IS the invariant ══════════════════════════

@unittest.skipUnless(PG_URL, "CLARIFICATION_PG_URL not set — no real PostgreSQL")
class C_RealPostgres(unittest.TestCase):
    """A unique constraint cannot be demonstrated against an in-memory SQLite stub."""

    @classmethod
    def setUpClass(cls):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        cls.engine = create_engine(PG_URL)
        cls.Session = sessionmaker(bind=cls.engine)

    def setUp(self):
        from sqlalchemy import text
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM whatsapp_clarification_attempts"))
            # the FK to whatsapp_threads is real, so the parent rows must be too
            for tid in range(1, 20):
                conn.execute(text(
                    "INSERT INTO whatsapp_contacts (id, wa_id) VALUES (:i, :w) "
                    "ON CONFLICT (id) DO NOTHING"),
                    {"i": tid, "w": f"c{tid}"})
                conn.execute(text(
                    "INSERT INTO whatsapp_threads (id, contact_id) VALUES (:i, :i) "
                    "ON CONFLICT (id) DO NOTHING"), {"i": tid})

    def _store(self):
        return ca.ClarificationAttemptStore(self.Session())

    def test_cl_14_the_composite_unique_constraint_exists(self):
        from sqlalchemy import text
        with self.engine.connect() as conn:
            names = {r[0] for r in conn.execute(text(
                "select conname from pg_constraint where "
                "conrelid='whatsapp_clarification_attempts'::regclass"))}
        self.assertIn("uq_clarification_attempt_thread_cycle_family", names)
        self.assertIn("uq_clarification_attempt_idempotency_key", names)
        self.assertIn("ck_clarification_attempt_family", names)
        self.assertIn("ck_clarification_attempt_status", names)

    def test_cl_15_first_vehicle_reservation_wins(self):
        _rr1 = self._store().reserve(thread_id=1, cycle=0, family=Fam.VEHICLE)
        attempt = _rr1.attempt
        won = _rr1.won
        self.assertTrue(won)
        self.assertEqual(attempt.status, St.RESERVED)

    def test_cl_16_second_vehicle_reservation_loses(self):
        s = self._store()
        s.reserve(thread_id=1, cycle=0, family=Fam.VEHICLE)
        _rr2 = s.reserve(thread_id=1, cycle=0, family=Fam.VEHICLE)
        attempt = _rr2.attempt
        won = _rr2.won
        self.assertFalse(won, "the constraint must refuse the second")
        self.assertIsNotNone(attempt, "and must hand back the existing row")

    def test_cl_17_location_remains_independently_available(self):
        s = self._store()
        s.reserve(thread_id=1, cycle=0, family=Fam.VEHICLE)
        _rr3 = s.reserve(thread_id=1, cycle=0, family=Fam.LOCATION)
        won = _rr3.won
        self.assertTrue(won, "the families are separate rows")

    def test_cl_18_a_new_cycle_permits_one_new_attempt_per_family(self):
        s = self._store()
        s.reserve(thread_id=1, cycle=0, family=Fam.VEHICLE)
        _rr4 = s.reserve(thread_id=1, cycle=4217, family=Fam.VEHICLE)
        won_new_cycle = _rr4.won
        self.assertTrue(won_new_cycle)
        _rr5 = s.reserve(thread_id=1, cycle=4217, family=Fam.VEHICLE)
        won_again = _rr5.won
        self.assertFalse(won_again)

    def test_cl_19_the_reservation_survives_an_outer_rollback(self):
        """The whole point of the independent transaction. A savepoint would not do."""
        outer = self.Session()
        store = ca.ClarificationAttemptStore(outer)
        _rr6 = store.reserve(thread_id=2, cycle=0, family=Fam.VEHICLE)
        attempt = _rr6.attempt
        won = _rr6.won
        self.assertTrue(won)
        outer.rollback()                       # the business transaction fails
        outer.close()
        _rr7 = self._store().reserve(
            thread_id=2, cycle=0, family=Fam.VEHICLE)
        again = _rr7.attempt
        won_again = _rr7.won
        self.assertFalse(won_again, "the rollback must not reopen the attempt")
        self.assertEqual(again.id, attempt.id)

    def test_cl_20_a_crash_after_reservation_does_not_reopen_it(self):
        """Simulated by abandoning the session entirely, which is what a crash looks like
        from the database's side."""
        store = self._store()
        store.reserve(thread_id=3, cycle=0, family=Fam.LOCATION)
        del store                               # the process is gone
        _rr8 = self._store().reserve(
            thread_id=3, cycle=0, family=Fam.LOCATION)
        attempt = _rr8.attempt
        won = _rr8.won
        self.assertFalse(won)
        self.assertEqual(attempt.status, St.RESERVED)
        self.assertTrue(ca.is_terminal_for_automation(attempt),
                        "a stale RESERVED row is spent, not retryable")

    def test_cl_21_transitions_persist_and_the_row_is_never_deleted(self):
        s = self._store()
        _rr9 = s.reserve(thread_id=4, cycle=0, family=Fam.VEHICLE)
        attempt = _rr9.attempt
        s.transition(attempt.id, St.DISPATCHED, wa_message_id="wamid.F1",
                     dispatch_result="ACCEPTED")
        s.transition(attempt.id, St.COMPLETED_UNRESOLVED,
                     completion_result="UNRESOLVED", resolved_family=False)
        row = s.load(thread_id=4, cycle=0, family=Fam.VEHICLE)
        self.assertEqual(row.status, St.COMPLETED_UNRESOLVED)
        self.assertEqual(row.wa_message_id, "wamid.F1")
        self.assertIs(row.resolved_family, False)
        _rr10 = s.reserve(thread_id=4, cycle=0, family=Fam.VEHICLE)
        won = _rr10.won
        self.assertFalse(won, "a completed-unresolved attempt is still spent")

    def test_cl_22_an_illegal_transition_raises(self):
        s = self._store()
        _rr11 = s.reserve(thread_id=5, cycle=0, family=Fam.VEHICLE)
        attempt = _rr11.attempt
        with self.assertRaises(ValueError):
            s.transition(attempt.id, St.COMPLETED_RESOLVED)   # RESERVED -> completed

    def test_cl_23_spent_families_reports_both(self):
        s = self._store()
        s.reserve(thread_id=6, cycle=0, family=Fam.VEHICLE)
        first = s.spent_families(thread_id=6, cycle=0)
        self.assertEqual(first.families, frozenset({Fam.VEHICLE}))
        self.assertTrue(first.available, "a successful read reports itself available")
        s.reserve(thread_id=6, cycle=0, family=Fam.LOCATION)
        self.assertEqual(s.spent_families(thread_id=6, cycle=0).families,
                         frozenset({Fam.VEHICLE, Fam.LOCATION}))
        other = s.spent_families(thread_id=6, cycle=99)
        self.assertEqual(other.families, frozenset())
        self.assertTrue(other.available,
                        "an EMPTY answer and an UNAVAILABLE answer are different facts")

    def test_cl_23b_a_non_conflict_integrity_error_is_not_reported_as_spent(self):
        """A foreign-key violation must NOT be read as "already attempted".

        SEQ-02. An FK, CHECK or NOT NULL failure arrives on the same path as a uniqueness
        conflict, so the conflict is confirmed by finding the row that caused it. Reporting
        a non-conflict as spent would tell the caller the customer had been asked when
        nothing was written — and letting it escape would destroy the customer's turn.
        Neither happens: the store reports a typed unavailability and raises nothing.
        """
        res = self._store().reserve(thread_id=987654, cycle=0, family=Fam.VEHICLE)
        self.assertEqual(res.status, ca.AttemptStoreStatus.STORE_UNAVAILABLE)
        self.assertFalse(res.won)
        self.assertFalse(res.already_attempted,
                         "a foreign-key violation is NOT 'already attempted'")
        self.assertFalse(res.available)
        self.assertEqual(res.fault, ca.StoreFault.INTEGRITY_NOT_CONFLICT)
        self.assertIsNone(res.attempt)

    def test_cl_24_the_family_check_constraint_rejects_junk(self):
        with self.assertRaises(ValueError):
            self._store().reserve(thread_id=7, cycle=0, family="SOMETHING_ELSE")


# ══ D — concurrency: two sessions, one winner ═════════════════════════════════════

@unittest.skipUnless(PG_URL, "CLARIFICATION_PG_URL not set — no real PostgreSQL")
class D_Concurrency(unittest.TestCase):
    """Two real sessions racing. This is the test that SQLite cannot stand in for."""

    @classmethod
    def setUpClass(cls):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        cls.engine = create_engine(PG_URL)
        cls.Session = sessionmaker(bind=cls.engine)

    def setUp(self):
        from sqlalchemy import text
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM whatsapp_clarification_attempts"))
            # the FK to whatsapp_threads is real, so the parent rows must be too
            for tid in range(1, 20):
                conn.execute(text(
                    "INSERT INTO whatsapp_contacts (id, wa_id) VALUES (:i, :w) "
                    "ON CONFLICT (id) DO NOTHING"),
                    {"i": tid, "w": f"c{tid}"})
                conn.execute(text(
                    "INSERT INTO whatsapp_threads (id, contact_id) VALUES (:i, :i) "
                    "ON CONFLICT (id) DO NOTHING"), {"i": tid})

    def test_cl_25_two_concurrent_sessions_produce_exactly_one_winner(self):
        import threading
        results = []
        barrier = threading.Barrier(2)

        def race():
            store = ca.ClarificationAttemptStore(self.Session())
            barrier.wait()
            try:
                results.append(store.reserve(
                    thread_id=11, cycle=0, family=Fam.VEHICLE).won)
            except Exception as exc:            # a raised conflict is still "lost"
                results.append(False)

        threads = [threading.Thread(target=race) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(sorted(results), [False, True],
                         f"exactly one winner required, got {results}")

    def test_cl_26_ten_concurrent_sessions_still_produce_one_winner(self):
        import threading
        results = []
        barrier = threading.Barrier(10)

        def race():
            store = ca.ClarificationAttemptStore(self.Session())
            barrier.wait()
            try:
                results.append(store.reserve(
                    thread_id=12, cycle=0, family=Fam.LOCATION).won)
            except Exception:
                results.append(False)

        threads = [threading.Thread(target=race) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(results.count(True), 1, f"one winner required, got {results}")

    def test_cl_27_no_read_then_insert_window_exists_in_the_code(self):
        """A SELECT-then-INSERT would be raceable no matter what the tests observe."""
        body = code_only(ca.ClarificationAttemptStore.reserve)
        self.assertIn("sess.add(row)", body)
        self.assertIn("IntegrityError", body)
        add_at = body.index("sess.add(row)")
        self.assertNotIn("select(", body[:add_at],
                         "no read may precede the insert")


# ══ E — structural: the two facts stay separate ══════════════════════════════════

class E_Separation(unittest.TestCase):

    def test_cl_28_no_pre_dispatch_write_of_either_outstanding_flag_remains(self):
        """Mechanical: find every assignment and prove each `True` follows a dispatch."""
        import re
        lines = ENGINE_SRC.splitlines()
        offenders = []
        for i, line in enumerate(lines):
            if re.search(r"(vehicle|location)_fallback_flow_sent\s*=\s*True", line):
                window = "\n".join(lines[max(0, i - 12):i])
                if "if not sent_id:" not in window:
                    offenders.append(i + 1)
        self.assertEqual(offenders, [],
                         f"flag set without a confirmed dispatch above, at lines {offenders}")

    def test_cl_29_the_flags_no_longer_decide_availability(self):
        """They may still be read as "outstanding"; they must not be the attempt authority."""
        src = inspect.getsource(E._turn_clarification_decision)
        self.assertIn("vehicle_attempt_spent=", src)
        self.assertIn("vehicle_flow_outstanding=", src)
        self.assertIn("spent_families", src)

    def test_cl_30_the_router_separates_spent_from_outstanding(self):
        sig = inspect.signature(cr.clarification_decision).parameters
        for name in ("vehicle_attempt_spent", "vehicle_flow_outstanding",
                     "location_attempt_spent", "location_flow_outstanding"):
            with self.subTest(arg=name):
                self.assertIn(name, sig)

    def test_cl_31_an_outstanding_flow_waits_but_a_spent_attempt_escalates(self):
        waiting = cr.clarification_decision(
            vehicle_domain_relevant=True, vehicle_identity_resolved=False,
            vehicle_flow_outstanding=True)
        self.assertEqual(waiting,
                         (Route.NO_CLARIFICATION_REQUIRED, Why.VEHICLE_FLOW_OUTSTANDING))
        spent = cr.clarification_decision(
            vehicle_domain_relevant=True, vehicle_identity_resolved=False,
            vehicle_attempt_spent=True)
        self.assertEqual(spent,
                         (Route.GENERIC_HUMAN_REQUIRED, Why.VEHICLE_ATTEMPT_SPENT))

    def test_cl_32_the_handoff_never_touches_the_attempt_table(self):
        body = code_only(E._generic_human_handoff)
        self.assertNotIn("ClarificationAttempt", body)
        self.assertNotIn("_attempt_store", body)
        self.assertNotIn("clarification_attempt", body)

    def test_cl_33_the_completion_handlers_bind_the_attempt(self):
        for fn in (E._process_vehicle_fallback_response,
                   E._process_location_fallback_response):
            with self.subTest(fn=fn.__name__):
                src = inspect.getsource(fn)
                self.assertIn("_complete_clarification_attempt", src)
                self.assertIn("fallback_flow_sent = False", src,
                              "the outstanding proposal is still cleared")


# ══ F — router placement and legacy removal ══════════════════════════════════════

class F_Placement(unittest.TestCase):

    def test_cl_34_the_decision_runs_after_reconciliation_and_before_mutation(self):
        """The corrected sequence. The preserved placement put this BEFORE the reconciled
        proposal existed, which is the defect the sequencing audit measured: the decision
        re-derived identity itself and answered "Focus 2008" with a Peugeot 2008.
        """
        decision = ENGINE_SRC.index("_clar = self._turn_clarification_decision(")
        reconcile = ENGINE_SRC.index("_proposals = (self._reconciled_reply_proposals(")
        cand = ENGINE_SRC.index('_cand_data = _proposals.get("candidate")')
        write = ENGINE_SRC.index("_outcome = self._apply_candidate(", cand)
        self.assertLess(reconcile, decision,
                        "the reconciled proposal must exist before the decision")
        self.assertLess(cand, decision,
                        "the candidate proposal must be derived before the decision")
        self.assertLess(decision, write,
                        "the decision must precede canonical mutation")

    def test_cl_35_it_is_independent_of_both_correction_flags(self):
        i = ENGINE_SRC.index("_clar = self._turn_clarification_decision(")
        window = ENGINE_SRC[max(0, i - 1800):i]
        self.assertNotIn("_provisional_correction_on()", window.split(
            "if state.last_stage in (STAGE_QUALIFYING, None)")[-1])
        self.assertNotIn("_correction_grounding_on()", window.split(
            "if state.last_stage in (STAGE_QUALIFYING, None)")[-1])

    def test_cl_36_the_five_legacy_clarification_branches_are_gone(self):
        """Structural, not textual. A comment claiming a branch was removed is not evidence
        that it was; the absence of every dispatch call inside both legacy authorities is.
        """
        for fn in (E._routing_gate, E._check_fallback_flow_triggers):
            body = code_only(fn)
            for forbidden in ("_dispatch_vehicle_flow_direct",
                              "_dispatch_location_flow_direct",
                              "_dispatch_vehicle_clarification_flow",
                              "_dispatch_location_clarification_flow",
                              "_send_flow_button",
                              "vehicle_fallback_flow_sent = True",
                              "location_fallback_flow_sent = True",
                              "vehicle_clarification_sent = True",
                              "location_clarification_sent = True"):
                self.assertNotIn(
                    forbidden, body,
                    f"{fn.__name__} can still reach clarification via {forbidden}")
        # And the one authority that may: exactly one executor, reached from one decision.
        self.assertEqual(ENGINE_SRC.count("self._execute_clarification("), 1,
                         "exactly one executor call site")
        self.assertEqual(ENGINE_SRC.count("_clar = self._turn_clarification_decision("), 1,
                         "exactly one decision call site")

    def test_cl_37_the_legacy_fallback_trigger_dispatches_nothing(self):
        body = code_only(E._check_fallback_flow_triggers)
        self.assertNotIn("_send_flow_button", body)
        self.assertNotIn("fallback_flow_sent = True", body)
        self.assertNotIn("_send_text_to_wa", body)
        self.assertEqual(body.strip().splitlines()[-1].strip(), "return None")

    def test_cl_38_no_substring_predicate_can_dispatch_a_clarification_flow(self):
        gate = inspect.getsource(E._routing_gate)
        for fn in ("_dispatch_vehicle_flow_direct", "_dispatch_location_flow_direct"):
            with self.subTest(fn=fn):
                self.assertNotIn(fn, gate)

    def test_cl_39_only_one_execution_authority_reserves_and_dispatches(self):
        callers = [l for l in ENGINE_SRC.splitlines()
                   if "_dispatch_vehicle_clarification_flow(" in l
                   or "_dispatch_location_clarification_flow(" in l]
        defs = [l for l in callers if l.strip().startswith("def ")]
        calls = [l for l in callers if not l.strip().startswith("def ")]
        self.assertEqual(len(defs), 2)
        for line in calls:
            with self.subTest(line=line.strip()[:60]):
                self.assertIn("self._dispatch_", line)

    def test_cl_40_the_router_module_is_pure(self):
        src = pathlib.Path(inspect.getsourcefile(cr)).read_text(encoding="utf-8")
        for token in ("db.", "session", "commit", "_send_", "WhatsAppThreadCandidate",
                      "re.search", "re.match", "re.compile"):
            with self.subTest(token=token):
                self.assertNotIn(token, src)


# ══ G — domain relevance ═════════════════════════════════════════════════════════

class G_DomainRelevance(unittest.TestCase):

    def test_cl_41_stored_state_alone_never_triggers_a_flow(self):
        """Owner policy 2.8 — the payment-FAQ case with an old incomplete candidate."""
        self.assertEqual(
            cr.clarification_decision(vehicle_identity_resolved=False,
                                      location_resolved=False),
            (Route.NO_CLARIFICATION_REQUIRED, Why.DOMAIN_NOT_RELEVANT))

    def test_cl_42_the_collector_derives_relevance_from_typed_evidence(self):
        src = inspect.getsource(E._turn_clarification_decision)
        self.assertIn("_admitted_vehicle_evidence", src)
        self.assertIn("_admitted_location_evidence", src)
        self.assertNotIn("focus is not None", src)
        self.assertIn("_required_next_information_authorized", src)

    def test_cl_43_required_next_information_is_the_other_source(self):
        self.assertEqual(
            cr.clarification_decision(required_next_information_authorized=True,
                                      location_resolved=False)[0],
            Route.LOCATION_FLOW_REQUIRED)

    def test_cl_44_a_resolved_turn_asks_nothing(self):
        self.assertEqual(
            cr.clarification_decision(vehicle_domain_relevant=True,
                                      vehicle_identity_resolved=True,
                                      vehicle_category_resolved=True,
                                      location_domain_relevant=True,
                                      location_resolved=True),
            (Route.NO_CLARIFICATION_REQUIRED, Why.RESOLVED))

    def test_cl_45_known_identity_unknown_category_never_asks_again(self):
        self.assertEqual(
            cr.clarification_decision(vehicle_domain_relevant=True,
                                      vehicle_identity_resolved=True,
                                      vehicle_category_resolved=False),
            (Route.GENERIC_HUMAN_REQUIRED, Why.VEHICLE_CATEGORY_UNRESOLVED))

    def test_cl_46_vehicle_precedes_location_when_both_are_unresolved(self):
        self.assertEqual(
            cr.clarification_decision(vehicle_domain_relevant=True,
                                      vehicle_identity_resolved=False,
                                      location_domain_relevant=True,
                                      location_resolved=False)[0],
            Route.VEHICLE_FLOW_REQUIRED)

    def test_cl_47_location_follows_once_the_vehicle_resolves(self):
        self.assertEqual(
            cr.clarification_decision(vehicle_domain_relevant=True,
                                      vehicle_identity_resolved=True,
                                      vehicle_category_resolved=True,
                                      vehicle_attempt_spent=True,
                                      location_domain_relevant=True,
                                      location_resolved=False)[0],
            Route.LOCATION_FLOW_REQUIRED)

    def test_cl_48_a_correction_with_known_values_is_not_a_clarification(self):
        self.assertEqual(
            cr.clarification_decision(correction_target_known=True,
                                      correction_values_known=True,
                                      vehicle_domain_relevant=True,
                                      vehicle_identity_resolved=False),
            (Route.CORRECTION_CONFIRMATION_REQUIRED, Why.CORRECTION_VALUES_KNOWN))


# ══ H — structure and governance ═════════════════════════════════════════════════

class H_Governance(unittest.TestCase):

    def test_cl_49_exactly_one_additive_migration_was_added(self):
        versions = pathlib.Path(__file__).resolve().parents[1] / "backend/migrations/versions"
        # SB1, SB2A and SB2A-R2 (Smart Booking Track B) later added their own, separately
        # reviewed migrations.
        self.assertEqual(sorted(p.name for p in versions.glob("2026100*.py")),
                         ["20261005_pending_correction.py",
                          "20261006_clarification_attempts.py",
                          "20261007_sb1_booking_representation.py",
                          "20261008_sb2a_booking_authority.py",
                          "20261009_sb2a_r2_ack_lease.py"])

    def test_cl_50_the_migration_is_additive_and_reversible(self):
        src = (pathlib.Path(__file__).resolve().parents[1]
               / "backend/migrations/versions/20261006_clarification_attempts.py"
               ).read_text(encoding="utf-8")
        self.assertIn('down_revision: str = "20261005_pending_correction"', src)
        self.assertIn("op.create_table(", src)
        self.assertIn("op.drop_table(", src)
        for forbidden in ("op.alter_column", "op.drop_column", "op.execute(\"UPDATE",
                          "op.execute(\"DELETE"):
            with self.subTest(op=forbidden):
                self.assertNotIn(forbidden, src)

    def test_cl_51_one_candidate_writer_remains(self):
        import ast
        tree = ast.parse(ENGINE_SRC)
        self.assertEqual(
            [n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name.startswith("_apply_candidate")],
            ["_apply_candidate"])

    def test_cl_52_the_attempt_module_writes_no_candidate(self):
        src = pathlib.Path(inspect.getsourcefile(ca)).read_text(encoding="utf-8")
        self.assertNotIn("WhatsAppThreadCandidate", src)
        self.assertNotIn("_apply_candidate", src)

    def test_cl_53_the_published_meta_flow_is_unchanged(self):
        import hashlib
        root = pathlib.Path(__file__).resolve().parents[1]
        published = root / "meta/flows/booking/PUBLISHED_28104222025943520_v7.3.json"
        self.assertEqual(
            hashlib.sha256(published.read_bytes()).hexdigest(),
            "274038ba234a49fa4f99bae47c00f6cac640e248d56a373d40ae8fba6c1afb15")

    def test_cl_54_both_correction_flags_still_default_false(self):
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

    def test_cl_55_confirmation_admissible_remains_unwired(self):
        import subprocess
        hits = subprocess.run(
            ["grep", "-rn", "confirmation_admissible", "backend/app/"],
            capture_output=True, text=True,
            cwd=str(pathlib.Path(__file__).resolve().parents[1])).stdout.splitlines()
        callers = [h for h in hits if "def confirmation_admissible" not in h
                   and "pending_correction.py" not in h]
        self.assertEqual(callers, [])

    def test_cl_56_no_g3_8_gate_was_modified(self):
        self.assertEqual(ENGINE_SRC.count("has_blocking_pending_correction"), 1)

    def test_cl_57_trace_contract_is_1_9_with_the_lifecycle_fields(self):
        from app.schemas.hybrid_trace import TRACE_VERSION
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.10")
        src = inspect.getsource(E._clarification_for_trace)
        for field in ("attempt_id", "cycle_key", "family", "attempt_status",
                      "reservation", "already_attempted", "outbound_message_id",
                      "store_available", "store_fault", "dispatch_result",
                      "completion_result", "resolved_family", "prevented_second"):
            with self.subTest(field=field):
                self.assertIn(f'"{field}"', src)
        self.assertIn('"g3_8_enforced": False', src)

    def test_cl_58_the_inspector_renders_the_lifecycle_and_stays_backward_safe(self):
        from app.ui.hybrid_trace_view import (_clarification_card,
                                              CONTRACTS_WITH_CANDIDATE_SECTION)
        self.assertIn("hybrid-decision-trace/1.9", CONTRACTS_WITH_CANDIDATE_SECTION)
        self.assertIn("hybrid-decision-trace/1.8", CONTRACTS_WITH_CANDIDATE_SECTION)
        html_19 = _clarification_card({"clarification": {
            "route": "VEHICLE_FLOW_REQUIRED", "reason": "VEHICLE_IDENTITY_UNRESOLVED",
            "attempt_id": 7, "cycle_key": 0, "family": "VEHICLE",
            "attempt_status": "DISPATCHED", "reservation": "WON",
            "dispatch_result": "DISPATCHED", "store_available": True,
            "already_attempted": False, "outbound_message_id": 31,
            "completion_result": None, "resolved_family": None,
            "prevented_second": False, "g3_8_enforced": False}})
        self.assertIn("Estado del intento", html_19)
        self.assertIn("entregado por Meta", html_19)
        self.assertIn("Reserva", html_19)
        # a 1.8 payload has none of those keys and must still render
        html_18 = _clarification_card({"clarification": {
            "route": "NO_CLARIFICATION_REQUIRED", "reason": "RESOLVED",
            "g3_8_enforced": False}})
        self.assertIn("Aclaración y derivación", html_18)
        self.assertNotIn("Estado del intento", html_18)
        self.assertEqual(_clarification_card({}), "")

    def test_cl_59_the_trace_exposes_no_pii(self):
        src = inspect.getsource(E._clarification_for_trace)
        for token in ("wa_id", "phone", "text", "prose", "body"):
            with self.subTest(token=token):
                self.assertNotIn(f'"{token}"', src)

    def test_cl_60_no_phrase_catalogue_entered_the_new_modules(self):
        for mod in (ca, cr):
            src = pathlib.Path(inspect.getsourcefile(mod)).read_text(encoding="utf-8")
            code = "\n".join(l for l in src.splitlines()
                             if not l.lstrip().startswith("#"))
            with self.subTest(module=mod.__name__):
                for token in ("re.search", "re.compile", '"si"', '"dale"', '"auto"'):
                    self.assertNotIn(token, code)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
