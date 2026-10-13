"""G3-7R — the sealed oracles: one candidate, one lock order, one refusal, one surface.

Everything here runs in the DEFAULT regression gate, with no database and no network. That
placement is the point. The G3-7P-R3A independent review measured that the offer authority's
two guards could be deleted with the whole 6,000-test suite staying identity-identical,
because every oracle that could have seen it sat behind `G7P_PG_URL` and skipped. A safety
control whose only witness is an optional suite is a control with no witness.

So the behavioural proofs live on PostgreSQL in
`test_l4_7w5_g3_7r_booking_projection_pg.py` (P1–P8), and the STRUCTURAL ones live here:
the controls that cannot be removed without this file failing, in the gate that always runs.
"""
from __future__ import annotations

import ast
import inspect
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_BACKEND = str(ROOT / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _code(fn) -> str:
    """The function's CODE, with comments and its docstring dropped.

    Comments must be excluded or the explanations of what was removed would themselves
    trip the oracles that check it stays removed.
    """
    node = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return "\n".join(ast.unparse(s) for s in body)


# ══ S1 — the two application guards are VISIBLE to the default gate ══════════

class ApplicationGuardsAreVisible(unittest.TestCase):
    """MUTATION ORACLES for required mutations 2, 3 and 4.

    The R3A review's finding, in one sentence: both offer guards could be deleted and
    nothing in the suite noticed. These assertions are the minimum that fixes that — they
    are structural, and they are deliberately structural, because the BEHAVIOURAL finding
    measured there was that each guard is individually redundant while the pair is not. A
    behavioural oracle for one of them alone cannot exist as long as the other is present;
    a structural one can, and it is what makes the removal visible at all.

    The pair's behavioural oracle is P1/P2/P3 on PostgreSQL, where the partial unique index
    is what refuses the second booking.
    """

    def setUp(self):
        from app.services.offer_lifecycle import OfferLifecycle
        # `_claim_checks` holds the checks and `claim` holds the terminal transition —
        # G3-7R split them so the bindings could be evaluated BEFORE availability
        # revalidation (S5) without duplicating them. Both halves are read together here,
        # because the guards are a property of the acceptance path, not of one method.
        self.claim = (_code(OfferLifecycle._claim_checks)
                      + "\n" + _code(OfferLifecycle.claim))

    def test_g37r_guard_01_the_offer_row_is_locked_before_it_is_read(self):
        """Required mutation 2. `SELECT ... FOR UPDATE` is what makes a concurrent
        withdrawal or a duplicate delivery serialise instead of racing."""
        self.assertIn("with_for_update()", self.claim,
                      "the offer row must be locked before anything is read from it")

    def test_g37r_guard_02_the_transition_is_conditional_on_ISSUED(self):
        """Required mutation 3. `UPDATE ... WHERE status = 'ISSUED'` plus a rowcount check
        is what turns a lost race into a typed refusal rather than an exception."""
        self.assertIn("O.status == OfferStatus.ISSUED", self.claim,
                      "the acceptance transition must be conditional on ISSUED")
        self.assertIn("res.rowcount", self.claim,
                      "the conditional UPDATE's rowcount is what decides the race")

    def test_g37r_guard_03_the_DATABASE_guarantee_is_declared(self):
        """Required mutation 1, in the model. The index is the guarantee; P3 is its
        behavioural oracle."""
        from app.models import ThreadRevision
        from app.services.offer_lifecycle import BOOKING_PROJECTION_CONSTRAINT

        names = {i.name for i in ThreadRevision.__table__.indexes}
        self.assertIn(BOOKING_PROJECTION_CONSTRAINT, names,
                      "the one-offer/one-booking index must be declared on the model")
        index = next(i for i in ThreadRevision.__table__.indexes
                     if i.name == BOOKING_PROJECTION_CONSTRAINT)
        self.assertTrue(index.unique, "the index must be UNIQUE or it guarantees nothing")
        self.assertEqual([c.name for c in index.columns], ["offer_id"])
        # Partial, or every historical and operator-written booking would collide on NULL.
        dialect_options = index.dialect_options
        self.assertIn("offer_id IS NOT NULL",
                      str(dialect_options["postgresql"].get("where", "")))
        self.assertIn("offer_id IS NOT NULL",
                      str(dialect_options["sqlite"].get("where", "")))

    def test_g37r_guard_04_both_acceptance_writers_record_the_offer(self):
        """A constraint only one of two writers participates in guarantees nothing about
        the pair."""
        from app.services.booking_flow_service import BookingFlowService
        from app.services.conversation_engine import ConversationEngine

        ce = _code(ConversationEngine._process_flow_response)
        dx = _code(BookingFlowService.handle_confirm_booking)
        for label, src in (("ConversationEngine", ce), ("DataExchange", dx)):
            self.assertIn("offer_id=_claim.offer_id", src,
                          f"{label} must write the claimed offer onto the booking")

    def test_g37r_guard_05_the_violation_is_translated_and_never_leaked(self):
        """A raw `IntegrityError`, a SQLSTATE or a constraint name must not reach a
        customer, and this violation's honest meaning is a replay."""
        from app.services.booking_flow_service import BookingFlowService
        from app.services.conversation_engine import ConversationEngine
        from app.services.offer_lifecycle import OfferLifecycle

        for fn in (ConversationEngine._process_flow_response,
                   BookingFlowService.handle_confirm_booking):
            src = _code(fn)
            self.assertIn("is_booking_projection_violation", src, fn.__qualname__)
        translator = _code(OfferLifecycle.is_booking_projection_violation)
        self.assertIn("23505", translator)
        self.assertIn("BOOKING_PROJECTION_CONSTRAINT", translator,
                      "the constraint NAME must be matched, so an unrelated unique "
                      "violation is not reported as a duplicate booking")


# ══ S3 — one candidate authority, resolved once ══════════════════════════════

class OneCandidateAuthority(unittest.TestCase):

    def test_g37r_cand_01_CE_resolves_the_candidate_exactly_once(self):
        """MUTATION ORACLE for required mutation 7.

        This path used to resolve a focus candidate TWICE in one acceptance — once with
        `repair=False` to bind the claim, and again, REPAIRING, to fill the booking's
        vehicle fields. Two resolutions of one question inside one transaction can
        disagree, and the second was allowed to mutate state the first had read.
        """
        from app.services.conversation_engine import ConversationEngine
        src = _code(ConversationEngine._process_flow_response)
        self.assertEqual(src.count("self._offer_bound_candidate("), 1,
                         "the candidate must be resolved exactly once per acceptance")
        self.assertNotIn("self._focus_candidate(", src,
                         "the acceptance path must not resolve a focus of its own")

    def test_g37r_cand_02_the_authority_is_the_offers_own_binding(self):
        from app.services.conversation_engine import ConversationEngine
        src = _code(ConversationEngine._offer_bound_candidate)
        self.assertIn("admission", src)
        self.assertIn("candidate_id", src)
        # What is forbidden is a second way to SELECT a candidate — a scan, an ordering, a
        # tie-break, a status rule. Reading the focus pointer in order to REFUSE when it
        # contradicts the binding is not selection: it returns None, never a row, and it is
        # the boundary half of the same rule whose first half is the withdrawal. R2
        # established that shape for the other bindings and this follows it.
        for forbidden in ("order_by", "updated_at", "limit(", 'status =='):
            self.assertNotIn(forbidden, src,
                             f"no second way to SELECT a candidate: {forbidden}")
        self.assertIn("return None", src)
        # And the pointer may only ever narrow, never widen: every use of it must be
        # followed by a refusal.
        pointer_uses = src.count("current_focus_candidate_id")
        self.assertEqual(pointer_uses, 1,
                         "the focus pointer is read exactly once, to refuse on a mismatch")

    def test_g37r_cand_03_both_transports_use_the_same_rule(self):
        """CE/Data Exchange equivalence, asserted on the rule rather than on prose: both
        read the offer's `candidate_id`, both refuse a deleted row, a foreign row and a row
        outside the active cycle watermark."""
        from app.services.booking_flow_service import BookingFlowService
        from app.services.conversation_engine import ConversationEngine
        ce = _code(ConversationEngine._offer_bound_candidate)
        dx = _code(BookingFlowService._offer_bound_candidate)
        for clause in ("candidate_id", "current_cycle_started_at", "created_at",
                       "thread_id"):
            self.assertIn(clause, ce, f"CE: {clause}")
            self.assertIn(clause, dx, f"DX: {clause}")
        self.assertIn("return None", ce)
        self.assertIn("return None", dx)

    def test_g37r_cand_04_the_old_resolvers_are_deleted(self):
        from app.services.booking_flow_service import BookingFlowService
        self.assertFalse(hasattr(BookingFlowService, "_load_focus_candidate"))

    def test_g37r_cand_05_a_focus_move_withdraws_the_offer(self):
        """The withdrawal that the acceptance-time comparison used to stand in for. It is
        narrow by construction: no live offer, no candidate on the offer, or the same
        candidate re-confirmed all withdraw nothing, so the established VEHICLE_CHANGED and
        LOCATION_CHANGED withdrawals keep owning their own cases."""
        from app.services.offer_lifecycle import OfferLifecycle, WithdrawReason
        self.assertEqual(WithdrawReason.FOCUS_CHANGED, "FOCUS_CHANGED")
        src = _code(OfferLifecycle.withdraw_if_candidate_moved)
        self.assertIn("live_offer", src)
        self.assertIn("return 0", src)
        self.assertIn("WithdrawReason.FOCUS_CHANGED", src)

    def test_g37r_cand_06_every_focus_pointer_write_is_covered(self):
        """A withdrawal that only some of the pointer writes call is a withdrawal with a
        hole in it, which is review finding B-01's shape from G3-7N."""
        from app.services.conversation_engine import ConversationEngine
        for fn in (ConversationEngine._apply_candidate,
                   ConversationEngine._create_candidate_from_catalog):
            src = _code(fn)
            writes = src.count("state.current_focus_candidate_id = ")
            guards = src.count("_withdraw_offer_if_focus_moved(")
            self.assertEqual(
                writes, guards,
                f"{fn.__name__}: {writes} focus-pointer write(s) but {guards} withdrawal "
                "guard(s) — every move must be covered")


# ══ S5 — final availability revalidation, with its coordination ══════════════

class FinalRevalidation(unittest.TestCase):

    def test_g37r_reval_01_CE_revalidates_before_it_accepts(self):
        """MUTATION ORACLE for required mutation 8 (structural half; P7 is behavioural).

        Revalidation must precede the claim: a slot that has gone is a fact about the
        calendar, not about the offer, so the offer stays ISSUED and the customer keeps the
        ability to book another slot with the same token.
        """
        from app.services.conversation_engine import ConversationEngine
        src = _code(ConversationEngine._process_flow_response)
        self.assertIn("self._revalidate_booking_slot(", src)
        self.assertLess(src.index("self._revalidate_booking_slot("),
                        src.index("_offers.claim("),
                        "revalidation must run BEFORE acceptance, not be undone by a "
                        "rollback afterwards")

    def test_g37r_reval_02_the_per_date_advisory_coordination_is_taken(self):
        """MUTATION ORACLE for required mutation 9. Keyed exactly as the Data Exchange
        writer keys it, so the two writers contend on the SAME key rather than each
        coordinating with itself."""
        from app.services.booking_flow_service import BookingFlowService
        from app.services.conversation_engine import ConversationEngine
        ce = _code(ConversationEngine._revalidate_booking_slot)
        self.assertIn("pg_try_advisory_xact_lock", ce)
        self.assertIn("self._booking_dialect_is_postgres()", ce,
                      "the advisory lock must be taken on PostgreSQL, not disabled")
        self.assertIn("sha256", ce)
        dx = _code(BookingFlowService.handle_confirm_booking)
        self.assertIn("sha256", dx)
        self.assertIn("_acquire_advisory_lock", dx)
        # The same derivation, so the same date yields the same key in both writers.
        # `0x7FFFFFFF` is compared as its decimal value: `ast.unparse` normalises the
        # literal, and an oracle that only recognised the hex spelling would pass while
        # the two writers drifted onto different masks.
        for clause in ("hexdigest()[:8]", "2147483647"):
            self.assertIn(clause, ce, f"CE: {clause}")
            self.assertIn(clause, dx.replace("0x7FFFFFFF", "2147483647"), f"DX: {clause}")

    def test_g37r_reval_03_the_same_authority_decides_for_both_writers(self):
        from app.services.conversation_engine import ConversationEngine
        src = _code(ConversationEngine._revalidate_booking_slot)
        self.assertIn("self._schedule.check(", src,
                      "the one availability authority, not a second rule")
        self.assertIn("out.valid", src)

    def test_g37r_reval_04_an_unanswerable_check_refuses(self):
        """"We could not check" is never "it is free"."""
        from app.services.conversation_engine import ConversationEngine
        src = _code(ConversationEngine._revalidate_booking_slot)
        self.assertIn("except Exception", src)
        self.assertIn("SLOT_NO_LONGER_AVAILABLE", src)

    def test_g37r_reval_05_a_missing_slot_or_location_refuses(self):
        from app.services.conversation_engine import ConversationEngine
        from app.services.offer_lifecycle import OfferRefusal
        src = _code(ConversationEngine._revalidate_booking_slot)
        self.assertIn("NO_SUBMITTED_SLOT", src)
        self.assertIn("NO_CANONICAL_LOCATION", src)
        self.assertEqual(OfferRefusal.NO_SUBMITTED_SLOT, "NO_SUBMITTED_SLOT")
        self.assertEqual(OfferRefusal.NO_CANONICAL_LOCATION, "NO_CANONICAL_LOCATION")

    def test_g37r_reval_06_a_missing_half_of_the_slot_is_not_a_slot(self):
        """A booked ThreadRevision with a NULL scheduled_date is not an appointment, and
        this path used to create one."""
        import types
        from app.services.conversation_engine import ConversationEngine
        resolve = ConversationEngine._submitted_booking_slot
        for day, hhmm in (("2026-11-10", None), (None, "10:00"), (None, None),
                          ("not-a-date", "10:00"), ("2026-11-10", "not-a-time")):
            with self.subTest(day=day, time=hhmm):
                state = types.SimpleNamespace(preferred_day=day, preferred_time=hhmm)
                self.assertEqual(resolve(state), (None, None))
        state = types.SimpleNamespace(preferred_day="2026-11-10", preferred_time="10:00")
        day, hhmm = resolve(state)
        self.assertIsNotNone(day)
        self.assertIsNotNone(hhmm)


# ══ S1/S4 — every offer writer takes resource 1 first ════════════════════════

class OneLockOrderForEveryWriter(unittest.TestCase):

    def test_g37r_lock_01_every_transition_acquires_the_thread_state_first(self):
        """MUTATION ORACLE for required mutation 10 (structural; P6 is behavioural).

        R3 enforced the order on the two acceptance writers and declared it done. The
        architecture audit then measured `40P01` in 3/3 trials between ISSUE and
        acceptance, because `issue()` was never brought under the protocol.
        """
        from app.services.offer_lifecycle import OfferLifecycle
        # The writers REQUIRE it (they raise when it cannot be taken); the acceptance
        # boundary ACQUIRES it and answers with a typed refusal instead, because it owes
        # its caller a domain outcome rather than an exception.
        for name in ("issue", "_terminalise", "consume", "refine_terminal_reason"):
            src = _code(getattr(OfferLifecycle, name))
            self.assertIn("_require_thread_state(", src,
                          f"{name} must REQUIRE resource 1 before writing the offer table")
        self.assertIn("_acquire_thread_state(", _code(OfferLifecycle._claim_checks))

    def test_g37r_lock_01b_a_writer_that_cannot_enter_the_order_does_not_write(self):
        """The first draft let the terminal transitions ignore the contention signal and
        write anyway — breaking the order AND leaving the offer-table write unbounded,
        which is how a booking turn hangs instead of answering. Measured: a PostgreSQL
        suite stalled indefinitely."""
        from app.services.offer_lifecycle import (OfferLifecycle,
                                                  OfferProtocolContended, OfferRefusal)
        lifecycle = OfferLifecycle.__new__(OfferLifecycle)
        lifecycle._acquire_thread_state = lambda t: OfferRefusal.OFFER_CONTENDED
        with self.assertRaises(OfferProtocolContended) as caught:
            lifecycle._require_thread_state(1)
        self.assertEqual(caught.exception.refusal, OfferRefusal.OFFER_CONTENDED)

    def test_g37r_lock_01c_every_offer_table_write_is_bounded(self):
        """A bound is what turns "cannot wait" into "cannot hang"."""
        from app.services.offer_lifecycle import OfferLifecycle
        for name in ("_terminalise", "consume", "_claim_checks"):
            src = _code(getattr(OfferLifecycle, name))
            self.assertIn("_bounded_lock_wait(", src, name)

    def test_g37r_lock_02_ISSUE_takes_it_BEFORE_it_supersedes(self):
        """The order inside `issue()` is the whole fix: `supersede()` locks the offer row,
        and the mirror write that follows flushes as an UPDATE on the thread-state row at
        commit — so taking resource 1 afterwards is what closed the cycle."""
        from app.services.offer_lifecycle import OfferLifecycle
        src = _code(OfferLifecycle.issue)
        self.assertLess(src.index("_require_thread_state("), src.index("self.supersede("),
                        "ISSUE must take the thread-state row before it supersedes")

    def test_g37r_lock_03_the_declared_order_is_the_implemented_order(self):
        from app.services.offer_lifecycle import OFFER_LOCK_ORDER, OfferLifecycle
        self.assertEqual(OFFER_LOCK_ORDER,
                         ("whatsapp_thread_states", "whatsapp_booking_offers"))
        src = _code(OfferLifecycle._acquire_thread_state)
        self.assertIn("whatsapp_thread_states", src)
        self.assertIn("FOR NO KEY UPDATE", src)

    def test_g37r_lock_04_a_deadlock_is_classified_apart_from_contention(self):
        from app.services.offer_lifecycle import OfferLifecycle, OfferRefusal
        self.assertEqual(OfferLifecycle.refusal_for_sqlstate("40P01"),
                         OfferRefusal.OFFER_DEADLOCK)
        self.assertEqual(OfferLifecycle.refusal_for_sqlstate("55P03"),
                         OfferRefusal.OFFER_CONTENDED)
        self.assertNotEqual(OfferRefusal.OFFER_DEADLOCK, OfferRefusal.OFFER_CONTENDED)

    def test_g37r_lock_05_no_retry_loop_at_the_acceptance_boundary(self):
        """A retry at this boundary is how duplicate work gets created."""
        from app.services.booking_flow_service import BookingFlowService
        from app.services.conversation_engine import ConversationEngine
        for fn in (ConversationEngine._process_flow_response,
                   BookingFlowService.handle_confirm_booking):
            src = _code(fn)
            for forbidden in ("while True", "for _attempt", "range(3)", "retry"):
                self.assertNotIn(forbidden, src, f"{fn.__qualname__}: {forbidden}")

    def test_g37r_lock_06_external_io_stays_after_the_commit(self):
        from app.services.conversation_engine import ConversationEngine
        src = _code(ConversationEngine._process_flow_response)
        self.assertLess(src.rindex("self.db.commit()"),
                        src.index("self._send_booking_notification("),
                        "no external I/O may happen inside the booking transaction")


# ══ R2 — the bound covers every exit, and the writer list is complete ════════

class R2BoundsCoverEveryExit(unittest.TestCase):
    """The sealed half of the R2 correction. The bounds themselves are measured on real
    PostgreSQL in `test_l4_7w5_g3_7r_r2_refusal_bounds_pg.py`; these assert the STRUCTURE
    that made the measurement come out right, so a future edit that reopens the defect
    fails here even where no disposable database is available.
    """

    def test_r2_01_every_DECLARED_offer_writer_requires_resource_1(self):
        """MUTATION ORACLE for required mutations 6 and 7 (RV-02), structural half.

        G3-7R's docstring said "every writer". The independent review inventoried four that
        did not: `record_replay`, `_refuse`, `record_refusal` and
        `record_refusal_out_of_band`, and reproduced `40P01` on demand in the inverse order.
        `OFFER_WRITERS` now NAMES them all, and this walks the list — so a tenth writer
        added without the acquisition fails the gate instead of silently widening it.
        """
        from app.services.offer_lifecycle import OFFER_WRITERS, OfferLifecycle
        for name in OFFER_WRITERS:
            with self.subTest(writer=name):
                src = _code(getattr(OfferLifecycle, name))
                acquires = ("_require_thread_state(" in src
                            or "_acquire_thread_state(" in src
                            # `claim` enters through `_claim_checks`, which is itself in
                            # this list and is therefore checked directly. One delegation,
                            # named, and no other.
                            or (name == "claim" and "_claim_checks(" in src))
                self.assertTrue(
                    acquires,
                    f"{name} writes whatsapp_booking_offers without entering "
                    f"OFFER_LOCK_ORDER — the inverse order deadlocks 40P01")

    def test_r2_02_the_declared_writer_list_is_the_actual_writer_list(self):
        """A declaration that drifts from the code is worse than no declaration."""
        import re
        from app.services import offer_lifecycle as mod
        from app.services.offer_lifecycle import OFFER_WRITERS, OfferLifecycle
        source = _code(OfferLifecycle)
        # Every method that issues an UPDATE or INSERT against the offer table.
        found = set()
        for name, fn in vars(OfferLifecycle).items():
            if not callable(fn) or name.startswith("__"):
                continue
            try:
                body = _code(fn)
            except (OSError, TypeError):            # pragma: no cover - defensive
                continue
            if re.search(r"\b(update|insert)\(\s*O\b", body) or "db.add(" in body:
                found.add(name)
        self.assertEqual(found - set(OFFER_WRITERS), set(),
                         "an offer-table writer is missing from OFFER_WRITERS")
        self.assertEqual(len(OFFER_WRITERS), len(set(OFFER_WRITERS)))

    def test_r2_03_the_bounded_entry_precedes_EVERY_admission_outcome(self):
        """MUTATION ORACLE for required mutation 3 (RV-01).

        All three answers persist something — a refusal counter, a replay counter or a
        booking — so all three are writers. G3-7R entered the order only for the granted
        one, and the review measured the other two at 42s against a 12s hold.
        """
        from app.services.conversation_engine import ConversationEngine
        src = _code(ConversationEngine._process_flow_response)
        entry = src.index("begin_offer_transaction(")
        for branch in ("if _adm.replay:", "if not _adm.granted:"):
            with self.subTest(branch=branch):
                self.assertLess(entry, src.index(branch),
                                f"{branch} is reached before the bounded entry — its "
                                f"first write would wait with no bound in force")

    def test_r2_04_a_contended_turn_mutates_nothing(self):
        """Contention is not a refusal: no counter moves, and the cursor is not consumed."""
        from app.services.conversation_engine import ConversationEngine
        src = _code(ConversationEngine._process_flow_response)
        head, _sep, tail = src.partition("if _contended is not None:")
        contended_block = tail.split("if _adm.replay:")[0]
        self.assertTrue(_sep, "the contended branch disappeared")
        for forbidden in ("record_refusal", "record_replay", "finalise_admission_refusal",
                          "needs_human"):
            self.assertNotIn(forbidden, contended_block,
                             f"a contended turn must not {forbidden}")
        self.assertIn("ACTION_THREAD_CONTENDED", contended_block)

    def test_r2_05_the_data_exchange_command_enters_the_order_before_refusing(self):
        """MUTATION ORACLE for required mutation 4. Both transports, or only half a fix."""
        from app.services.booking_flow_service import BookingFlowService
        src = _code(BookingFlowService.handle_confirm_booking)
        self.assertLess(src.index("begin_offer_transaction("),
                        src.index("if not _adm.granted:"),
                        "the DX refusal path writes before the bound is in force")

    def test_r2_06_resolution_is_READ_ONLY(self):
        """MUTATION ORACLE for required mutation 9 (RV-07).

        `resolve_context` is reached from `handle_init`, `handle_date_selected` and
        `handle_prepare_summary`, none of which is a command. G3-7R had it count the replay
        and COMMIT, so resolving a context could commit whatever the caller had pending.
        """
        from app.services.booking_flow_service import BookingFlowService
        src = _code(BookingFlowService.resolve_context)
        for forbidden in ("self.db.commit()", "record_replay("):
            self.assertNotIn(forbidden, src,
                             f"resolve_context must not {forbidden} — it resolves, it does "
                             f"not command")
        self.assertIn("BookingAlreadyCompletedError", src, "it must still DETECT the replay")

    def test_r2_07_the_command_boundary_is_where_the_replay_is_counted(self):
        """The other half of RV-07: moved, not dropped."""
        from app.services.booking_flow_service import BookingFlowService
        self.assertIn("_count_replay_at_command_boundary(",
                      _code(BookingFlowService.handle_confirm_booking))
        src = _code(BookingFlowService._count_replay_at_command_boundary)
        self.assertIn("record_replay(", src)
        self.assertIn("begin_offer_transaction(", src,
                      "the counter must be written inside the canonical bounded order")
        self.assertIn("thread_id", src)

    def test_r2_08_the_replay_carries_its_thread_so_the_boundary_can_enter_the_order(self):
        from app.services.booking_flow_service import BookingAlreadyCompletedError
        exc = BookingAlreadyCompletedError(offer_id=7, thread_revision_id=8, revision_id=9,
                                          thread_id=11)
        self.assertEqual((exc.offer_id, exc.thread_id), (7, 11))
        # Defaults stay None so an older raiser cannot silently acquire a wrong thread.
        self.assertIsNone(BookingAlreadyCompletedError().thread_id)

    def test_r2_09_the_preparatory_statements_do_not_flush_the_callers_writes(self):
        """MUTATION ORACLES for required mutations 1 and 2, structural half.

        The whole RV-01 defect in one ordering: `SHOW lock_timeout` autoflushed the turn's
        pending dedup-cursor UPDATE before `SET LOCAL lock_timeout` existed.
        """
        from app.services.offer_lifecycle import OfferLifecycle
        src = _code(OfferLifecycle._bounded_lock_wait)
        self.assertIn("with self.db.no_autoflush:", src)
        self.assertLess(src.index("with self.db.no_autoflush:"),
                        src.index('SHOW lock_timeout'),
                        "the SHOW must sit INSIDE the autoflush suppression")
        self.assertLess(src.index("SET LOCAL lock_timeout = %d"),
                        src.index("self.db.flush()"),
                        "the caller's writes must flush AFTER the bound is set")
        # The LAST `yield` is the guarded one; the first belongs to the non-PostgreSQL
        # early return, which has no bound to be inside of.
        self.assertLess(src.index("self.db.flush()"), src.rindex("yield"),
                        "and before the guarded statement runs")


# ══ S6 — replay is not a refusal ═════════════════════════════════════════════

class ReplayIsNotARefusal(unittest.TestCase):

    def test_g37r_replay_01_a_replay_has_its_own_typed_outcome(self):
        from app.schemas.conversation import (ACTION_BOOKING_REPLAY, HANDLED_ACTIONS)
        from app.services.conversation_engine import (_NO_REPLY_REQUIRED_ACTIONS,
                                                      _REPLY_PRODUCED_ACTIONS, _out)
        self.assertIn(ACTION_BOOKING_REPLAY, HANDLED_ACTIONS,
                      "an unhandled replay would reach n8n's legacy booking chain")
        self.assertIn(ACTION_BOOKING_REPLAY, _NO_REPLY_REQUIRED_ACTIONS,
                      "the acknowledgement was already sent by the writer that booked")
        self.assertNotIn(ACTION_BOOKING_REPLAY, _REPLY_PRODUCED_ACTIONS)
        out = _out(ACTION_BOOKING_REPLAY, detail="55")
        self.assertTrue(out.handled)
        self.assertFalse(out.reply_required)
        self.assertFalse(out.alert_eligible,
                         "a replay owes nothing, so it must not alert as a missed reply")
        self.assertNotEqual(out.action, "error")

    def test_g37r_replay_02_the_replay_path_touches_no_refusal_counter(self):
        """MUTATION ORACLE for required mutation 11 (structural; P8 is behavioural)."""
        from app.services.conversation_engine import ConversationEngine
        src = _code(ConversationEngine._booking_replay_outcome)
        self.assertIn("record_replay(", src)
        for forbidden in ("record_refusal", "last_refusal", "refused_count",
                          "needs_human", "_FALLBACK_WARM_HANDOFF", "_send_text_to_wa"):
            self.assertNotIn(forbidden, src,
                             f"a replay must not {forbidden}")

    def test_g37r_replay_03_the_data_exchange_replay_is_not_a_token_error(self):
        """The false-expiry wording must be unreachable for a replay."""
        from app.services.booking_flow_service import (BookingAlreadyCompletedError,
                                                       BookingTokenError)
        self.assertFalse(issubclass(BookingAlreadyCompletedError, BookingTokenError),
                         "a replay caught as a token error renders the expiry screen")
        route = (ROOT / "backend" / "app" / "routes"
                 / "flow_data_exchange.py").read_text(encoding="utf-8-sig")
        self.assertLess(route.index("except BookingAlreadyCompletedError"),
                        route.index("except BookingTokenError"),
                        "the replay handler must be caught first")
        # The RESPONSE the replay handler builds, not its commentary: the comment explains
        # that the expiry wording was the defect, so it necessarily contains the word.
        handler = next(h for n in ast.walk(ast.parse(route))
                       if isinstance(n, ast.Try) for h in n.handlers
                       if "BookingAlreadyCompletedError" in ast.unparse(h.type or ast.Pass()))
        replay_code = ast.unparse(handler)
        self.assertIn("'screen': 'SUCCESS'", replay_code.replace('"', "'"))
        self.assertNotIn("expiró", replay_code,
                         "the false-expiry wording must be unreachable for a replay")

    def test_g37r_replay_04_admission_detects_it_before_the_mirror_can_lie(self):
        """A committed booking CLEARS the transport mirror, so the mirror comparison cannot
        tell a replay from a tampered token — it reported both as an expired session."""
        from app.services.booking_flow_service import BookingFlowService
        src = _code(BookingFlowService.resolve_context)
        self.assertLess(src.index("admit("),
                        src.index("flow_booking_token != booking_token"),
                        "the replay question must be asked before the mirror check")

    def test_g37r_replay_05_an_accepted_offer_with_no_booking_is_refused(self):
        """There is no authoritative booking to return, so inventing one would be worse
        than refusing."""
        from app.services.offer_lifecycle import OfferLifecycle
        src = _code(OfferLifecycle.admit)
        self.assertIn("accepted_thread_revision_id is not None", src)
        self.assertIn("OFFER_ALREADY_ACCEPTED", src)

    def test_g37r_replay_06_the_counters_are_separate_columns(self):
        from app.models import WhatsAppBookingOffer
        cols = {c.name for c in WhatsAppBookingOffer.__table__.columns}
        for name in ("refused_count", "last_refusal", "last_refused_at",
                     "replay_count", "last_replay_at"):
            self.assertIn(name, cols, name)


# ══ S9 — the operator surface ════════════════════════════════════════════════

class OperatorSurface(unittest.TestCase):

    def _row(self, **kw):
        row = dict(id=7, thread_id=3, cycle_key=0, candidate_id=11, tipo_vehiculo="AUTO",
                   zone_group="CABA", zone_detail="Palermo", offered_date="2026-11-10",
                   offered_time="10:00", flow_id="28104222025943520",
                   contract_version="offer/1.0",
                   # R2/RV-05: the accessor emits only the prefix, so the row the view
                   # receives never carries the full value in the first place.
                   token_fingerprint_prefix="f" * 12,
                   status="ISSUED", issued_at="2026-11-01T10:00:00Z", expires_at=None,
                   terminal_at=None, accepted_at=None, terminal_reason=None,
                   refused_count=0, last_refusal=None, last_refused_at=None,
                   replay_count=0, last_replay_at=None, accepted_revision_id=None,
                   accepted_thread_revision_id=None)
        row.update(kw)
        return row

    def test_g37r_view_01_every_required_field_is_rendered(self):
        from app.ui.offer_authority_view import render_offer_detail_page
        page = render_offer_detail_page(
            self._row(status="ACCEPTED", terminal_at="2026-11-02T09:00:00Z",
                      accepted_at="2026-11-02T09:00:00Z",
                      terminal_reason="CONSUMED_BY_BOOKING",
                      accepted_thread_revision_id=55, accepted_revision_id=66))
        for needed in ("#7", "conversación 3", "CABA", "Palermo", "AUTO", "2026-11-10",
                       "10:00", "55", "66", "2026-11-01", "2026-11-02"):
            self.assertIn(needed, page, needed)

    def test_g37r_view_02_no_raw_token_no_secret_no_full_fingerprint(self):
        from app.ui.offer_authority_view import (fingerprint_prefix,
                                                 render_offer_detail_page)
        page = render_offer_detail_page(self._row())
        self.assertNotIn("f" * 64, page, "the full fingerprint must not be rendered")
        self.assertIn("f" * 12, page, "a short prefix is still useful to an operator")
        # Defensive even when handed too much, because the page is the last line.
        self.assertEqual(fingerprint_prefix("a" * 64), "a" * 12 + "⋯")
        self.assertEqual(fingerprint_prefix(None), "—")
        for forbidden in ("wamid.", "-----BEGIN", "sk-", "549"):
            self.assertNotIn(forbidden, page, forbidden)

    def test_g37r_view_02b_the_ACCESSOR_minimises_so_the_API_cannot_leak(self):
        """MUTATION ORACLE for required mutation 8 (R2/RV-05).

        The independent review found the page truncating the fingerprint to twelve
        characters with a stated rationale while `/api/ops/offers`, which shares the same
        accessor, returned all sixty-four. Minimisation now happens in the accessor, so the
        field list itself no longer carries the full value and neither surface can emit it.
        """
        from app.routes.ops_dashboard import (OFFER_FINGERPRINT_PREFIX_LEN, _OFFER_FIELDS,
                                              _offer_dict)
        import types
        self.assertNotIn("token_fingerprint", _OFFER_FIELDS,
                         "the full fingerprint must not be a selectable operator field")
        full = "e" * 64
        row = types.SimpleNamespace(token_fingerprint=full)
        for name in _OFFER_FIELDS:
            setattr(row, name, None)
        row.token_fingerprint = full
        emitted = _offer_dict(row)
        self.assertNotIn("token_fingerprint", emitted)
        self.assertEqual(emitted["token_fingerprint_prefix"],
                         full[:OFFER_FINGERPRINT_PREFIX_LEN])
        self.assertNotIn(full, str(emitted), "no surface may carry the whole fingerprint")
        row.token_fingerprint = None
        self.assertIsNone(_offer_dict(row)["token_fingerprint_prefix"])

    def test_g37r_view_03_the_six_lifecycle_states_are_distinguishable(self):
        from app.ui.offer_authority_view import STATUS_LABELS, outcome_of
        for status in ("ISSUED", "ACCEPTED", "WITHDRAWN", "SUPERSEDED", "EXPIRED"):
            self.assertIn(status, STATUS_LABELS, status)
        labels = {outcome_of(self._row(status=s))[0]
                  for s in ("ISSUED", "WITHDRAWN", "SUPERSEDED", "EXPIRED")}
        self.assertEqual(len(labels), 4, f"each state must read differently: {labels}")

    def test_g37r_view_04_booking_replay_and_refusal_read_differently(self):
        from app.ui.offer_authority_view import outcome_of
        booked = outcome_of(self._row(status="ACCEPTED",
                                      accepted_thread_revision_id=55))[0]
        replayed = outcome_of(self._row(status="ACCEPTED",
                                        accepted_thread_revision_id=55,
                                        replay_count=2))[0]
        refused = outcome_of(self._row(status="WITHDRAWN", refused_count=1,
                                       last_refusal="OFFER_EXPIRED"))[0]
        orphan = outcome_of(self._row(status="ACCEPTED",
                                      accepted_thread_revision_id=None))[0]
        self.assertEqual(len({booked, replayed, refused, orphan}), 4,
                         f"{booked} / {replayed} / {refused} / {orphan}")
        self.assertIn("REENVIADA", replayed)
        self.assertIn("SIN RESERVA", orphan,
                      "an acceptance with no booking is the one state that must stand out")

    def test_g37r_view_05_a_booking_is_decided_by_the_booking_not_the_status(self):
        """An offer can be ACCEPTED while its booking write failed, and calling that
        'reservado' would be a false business fact."""
        from app.ui.offer_authority_view import outcome_of
        label, css, _why = outcome_of(self._row(status="ACCEPTED",
                                                accepted_thread_revision_id=None))
        self.assertEqual(css, "bad")
        self.assertNotIn("CONFIRMADA", label)

    def test_g37r_view_06_absence_is_rendered_as_absence(self):
        from app.ui.offer_authority_view import (render_offer_detail_page,
                                                 render_offer_list_page)
        missing = render_offer_detail_page(None, offer_id=99)
        self.assertIn("No existe una oferta", missing)
        empty = render_offer_list_page([])
        self.assertIn("No hay ofertas registradas", empty)
        self.assertNotIn("RECHAZADA", empty,
                         "no offers is not a refusal")

    def test_g37r_view_07_both_transports_are_nameable(self):
        from app.ui.offer_authority_view import transport_of
        booking_flow = "28104222025943520"
        self.assertIn("Data Exchange",
                      transport_of({"flow_id": booking_flow}, booking_flow))
        self.assertIn("ConversationEngine",
                      transport_of({"flow_id": "OTHER_FLOW"}, booking_flow))
        self.assertEqual(transport_of({"flow_id": None}, booking_flow), "—")

    def test_g37r_view_08_the_accessor_selects_declared_fields_only(self):
        """Explicit rather than reflective, so a column added later must be CHOSEN for this
        surface instead of inherited by it."""
        from app.routes.ops_dashboard import _OFFER_FIELDS
        self.assertNotIn("flow_booking_token", _OFFER_FIELDS)
        for required in ("id", "thread_id", "status", "terminal_reason", "candidate_id",
                         "tipo_vehiculo", "zone_group", "zone_detail", "offered_date",
                         "offered_time", "refused_count", "last_refusal", "replay_count",
                         "accepted_revision_id", "accepted_thread_revision_id",
                         "issued_at", "terminal_at", "accepted_at"):
            self.assertIn(required, _OFFER_FIELDS, required)

    def test_g37r_view_09_the_pages_are_behind_the_protected_prefix(self):
        from app.main import _is_protected_path
        for path in ("/control/offers", "/control/offer/7"):
            self.assertTrue(_is_protected_path(path),
                            f"{path} must require an authenticated CRM session")

    def test_g37r_view_10_every_refusal_the_authority_can_write_has_a_label(self):
        """An operator must never meet a bare enum. A value with no label is rendered
        verbatim rather than hidden, but the ones we emit should all be translated."""
        from app.services.offer_lifecycle import OfferRefusal
        from app.ui.offer_authority_view import REFUSAL_LABELS
        emitted = {v for k, v in vars(OfferRefusal).items()
                   if not k.startswith("_") and isinstance(v, str)}
        self.assertEqual(emitted - set(REFUSAL_LABELS), set())

    def test_g37r_view_11_every_withdraw_reason_has_a_label(self):
        from app.services.offer_lifecycle import WithdrawReason
        from app.ui.offer_authority_view import REASON_LABELS
        emitted = {v for k, v in vars(WithdrawReason).items()
                   if not k.startswith("_") and isinstance(v, str)}
        self.assertEqual(emitted - set(REASON_LABELS), set())


# ══ S8 — the claims that were corrected ══════════════════════════════════════

class CorrectedClaims(unittest.TestCase):

    def test_g37r_doc_01_the_fails_closed_to_True_claim_is_gone(self):
        """It documented a safety control whose fallback was to serialise traffic that
        could never book. The control is deleted, so the claim must be too."""
        ce = (ROOT / "backend" / "app" / "services"
              / "conversation_engine.py").read_text(encoding="utf-8-sig")
        self.assertNotIn("it answers True, so", ce)
        self.assertNotIn("Fail-closed in the direction that matters", ce)

    def test_g37r_doc_02_no_source_claims_application_code_alone_guarantees_one_acceptance(self):
        for path in ("backend/app/models.py",
                     "backend/app/services/offer_lifecycle.py",
                     "backend/migrations/versions/20261010_offer_lifecycle.py"):
            text = (ROOT / path).read_text(encoding="utf-8-sig")
            self.assertNotIn("at most one ACCEPTANCE per offer", text, path)

    def test_g37r_doc_03_the_lock_order_comment_no_longer_says_both_writers(self):
        offer = (ROOT / "backend" / "app" / "services"
                 / "offer_lifecycle.py").read_text(encoding="utf-8-sig")
        self.assertNotIn("enforced on BOTH writers", offer)
        self.assertIn("EVERY writer", offer)

    def test_g37r_doc_04_CLAUDE_md_no_longer_claims_one_booking_writer(self):
        claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8-sig")
        self.assertNotIn(
            'only path creating `ThreadRevision(status="booked")` is '
            "`_process_flow_response`", claude)
        # And the operator/machine authority is documented as the distinct thing it is.
        self.assertIn("PATCH /api/revisions/{id}", claude)
        self.assertIn("patch_revision", claude)

    def test_g37r_doc_05_the_booking_contract_names_three_writers(self):
        contract = (ROOT / "docs" / "architecture"
                    / "BOOKING_UX_CONTRACT.md").read_text(encoding="utf-8-sig")
        self.assertNotIn(
            "The only code path that creates `ThreadRevision(status=\"booked\")`\n"
            "  is `ConversationEngine._process_flow_response`", contract)
        for needed in ("handle_confirm_booking", "patch_revision", "offer_id"):
            self.assertIn(needed, contract, needed)

    def test_g37r_doc_06_the_operator_endpoint_is_not_routed_into_acceptance(self):
        """Documented as a distinct authority, and left as one: the operator endpoint must
        not acquire an offer claim in this milestone."""
        service = (ROOT / "backend" / "app" / "services"
                   / "thread_revisions.py").read_text(encoding="utf-8-sig")
        for forbidden in ("OfferLifecycle", "offer_lifecycle", "claim(", "admit("):
            self.assertNotIn(forbidden, service, forbidden)


# ══ S10 — compatibility with the authorized Smart Booking convergence ════════

class SmartBookingCompatibility(unittest.TestCase):
    """This milestone is a strict subset of the selected Option C: Gate 3 owns ADMISSION
    and Smart Booking's `BookingAuthority` will own EXECUTION. Nothing here may create a
    second booking authority or collide with Track B's typed vocabulary."""

    def test_g37r_sb_01_no_second_acceptance_service_writes_bookings(self):
        """The booking writers are the two transports and the authenticated operator
        endpoint. G3-7R added none."""
        import app.services.offer_lifecycle as OL
        src = (ROOT / "backend" / "app" / "services"
               / "offer_lifecycle.py").read_text(encoding="utf-8-sig")
        for forbidden in ("ThreadRevision(", 'status="booked"', "status='booked'"):
            self.assertNotIn(forbidden, src,
                             "the admission authority must not write bookings")
        self.assertTrue(hasattr(OL, "OfferAdmission"))

    def test_g37r_sb_02_no_name_collides_with_track_Bs_vocabulary(self):
        import app.services.offer_lifecycle as OL
        for reserved in ("BookingCommand", "BookingResult", "BookingOutcome",
                         "flow_idempotency_key", "AckClaim", "AvailabilityRevalidation",
                         "BookingAuthority"):
            self.assertFalse(hasattr(OL, reserved),
                             f"{reserved} belongs to Track B's BookingAuthority")

    def test_g37r_sb_03_admission_is_separable_from_execution(self):
        """The grant carries everything an executor needs and performs no write, so the
        convergence can hand it to `BookingAuthority` without re-deriving anything."""
        from app.services.offer_lifecycle import AdmissionOutcome, OfferAdmission
        grant = OfferAdmission(AdmissionOutcome.GRANTED, None, offer_id=1, candidate_id=2,
                               tipo_vehiculo="AUTO", zone_group="CABA",
                               zone_detail="Palermo", cycle_key=0)
        self.assertTrue(grant.granted)
        self.assertTrue(bool(grant))
        for field in ("offer_id", "candidate_id", "tipo_vehiculo", "zone_group",
                      "zone_detail", "cycle_key", "offered_date", "offered_time",
                      "thread_revision_id", "revision_id"):
            self.assertTrue(hasattr(grant, field), field)
        from dataclasses import FrozenInstanceError
        with self.assertRaises(FrozenInstanceError):
            grant.offer_id = 99          # immutable, like Track B's typed command

    def test_g37r_sb_04_the_bounded_wait_is_not_wrapped_round_a_foreign_transaction(self):
        """Track B's authority owns its own timeout policy; this helper must not govern it."""
        # Asserted on CODE: the module's prose names Track B's authority deliberately, to
        # record which layer will own execution. What must not exist is a CALL.
        from app.services.offer_lifecycle import OfferLifecycle
        for name in ("_bounded_lock_wait", "_acquire_thread_state",
                     "begin_offer_transaction", "claim", "_claim_checks", "admit",
                     "verify_bindings"):
            src = _code(getattr(OfferLifecycle, name))
            for forbidden in ("BookingAuthority", "booking_authority", "BookingCommand"):
                self.assertNotIn(forbidden, src, f"{name}: {forbidden}")


if __name__ == "__main__":
    unittest.main()
