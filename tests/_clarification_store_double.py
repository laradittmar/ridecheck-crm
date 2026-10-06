"""An EXPLICIT attempt-store double, for harnesses that drive CE with a mocked session.

The sequencing audit (SEQ-02) measured what happens when a test lets a `MagicMock` stand in
for a database: a flush against an unusable session raised `FlushError` out of `reserve()`
and destroyed the customer's turn, and — worse — an empty answer from a broken store looked
exactly like an empty answer from a thread that had never been asked.

So the store is now injected on purpose, with the outcome the test actually means:

    eng._clarification_attempt_store = FakeAttemptStore()                  # works
    eng._clarification_attempt_store = FakeAttemptStore(spent=["VEHICLE"]) # already asked
    eng._clarification_attempt_store = FakeAttemptStore(unavailable=True)  # store is down

Nothing here fakes the UNIQUE constraint's semantics loosely: `reserve` on a family that is
already present returns `ALREADY_ATTEMPTED` with the stored row, exactly as the database
does. The real constraint, the real race and the real transaction boundary are proven
against real PostgreSQL in `test_l4_7w5_g3_7f_clarification_lifecycle.py` — this double
exists for turn-level behaviour, never as concurrency evidence.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.clarification_attempt import (                   # noqa: E402
    AttemptStatus, AttemptStoreStatus, ReservationResult, SpentFamiliesResult,
    StoreFault, ALLOWED_TRANSITIONS, cycle_key, idempotency_key)


class FakeAttemptStore:
    """In-memory attempt store with explicit, inspectable outcomes."""

    def __init__(self, *, unavailable: bool = False, spent=(),
                 fault: str = StoreFault.OPERATIONAL) -> None:
        self.unavailable = unavailable
        self.fault = fault
        self.rows: dict[tuple, SimpleNamespace] = {}
        self._next_id = 1
        for family in spent:
            self._insert(thread_id=1, cycle=0, family=family,
                         status=AttemptStatus.DISPATCHED)

    # ── helpers a test can read ──────────────────────────────────────────────
    @property
    def reserved(self):
        return list(self.rows.values())

    def row_for(self, family):
        for row in self.rows.values():
            if row.family == family:
                return row
        return None

    def _insert(self, *, thread_id, cycle, family, status):
        row = SimpleNamespace(
            id=self._next_id, thread_id=thread_id, cycle_key=cycle, family=family,
            idempotency_key=idempotency_key(thread_id, cycle, family), status=status,
            reserved_at=None, outbound_message_id=None, wa_message_id=None,
            dispatch_result=None, completion_result=None, resolved_family=None,
            terminal_reason=None, updated_at=None)
        self._next_id += 1
        self.rows[(thread_id, cycle, family)] = row
        return row

    # ── the store interface CE uses ──────────────────────────────────────────
    def spent_families(self, *, thread_id, cycle):
        if self.unavailable:
            return SpentFamiliesResult(frozenset(), available=False, fault=self.fault)
        return SpentFamiliesResult(frozenset(
            r.family for r in self.rows.values() if r.cycle_key == cycle))

    def reserve(self, *, thread_id, cycle, family):
        if self.unavailable:
            return ReservationResult(AttemptStoreStatus.STORE_UNAVAILABLE,
                                     fault=self.fault)
        existing = next((r for r in self.rows.values()
                         if r.cycle_key == cycle and r.family == family), None)
        if existing is not None:
            return ReservationResult(AttemptStoreStatus.ALREADY_ATTEMPTED,
                                     attempt=existing)
        row = self._insert(thread_id=thread_id, cycle=cycle, family=family,
                           status=AttemptStatus.RESERVED)
        return ReservationResult(AttemptStoreStatus.WON, attempt=row)

    def load(self, *, thread_id, cycle, family):
        if self.unavailable:
            return None
        return next((r for r in self.rows.values()
                     if r.cycle_key == cycle and r.family == family), None)

    def transition(self, attempt_id, new_status, **fields):
        if self.unavailable:
            return None
        row = next((r for r in self.rows.values() if r.id == attempt_id), None)
        if row is None:
            return None
        allowed = ALLOWED_TRANSITIONS.get(row.status, ())
        if new_status != row.status and new_status not in allowed:
            raise ValueError(f"illegal attempt transition {row.status} -> {new_status}")
        row.status = new_status
        for name, value in fields.items():
            setattr(row, name, value)
        return row


def install(eng, **kw) -> FakeAttemptStore:
    """Attach a double to an engine and hand it back for assertions."""
    store = FakeAttemptStore(**kw)
    eng._clarification_attempt_store = store
    return store


def decide(eng, ctx, state, **kw):
    """One fresh clarification decision (resets the per-turn memo first)."""
    eng._turn_clarification_decision_obj = None
    return eng._turn_clarification_decision(ctx, state, **kw)


def ask(eng, ctx, state, **kw):
    """Decide and execute in one step — what a qualifying turn actually does."""
    return eng._execute_clarification(ctx, state, decide(eng, ctx, state, **kw))
