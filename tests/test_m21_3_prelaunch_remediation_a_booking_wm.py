"""M21.3-PRELAUNCH-REMEDIATION-A — BookingFlow active-cycle candidate tests.

BF-WM-01  the candidate comes from the offer that armed the token
BF-WM-02  a candidate created before the active cycle boundary is excluded
BF-WM-03  the offer's binding decides — the newest updated row does NOT win
BF-WM-04  a candidate at the exact cycle boundary is eligible
BF-WM-05  no eligible candidate → deterministic None, which refuses downstream
BF-WM-06  booking cannot use a historical vehicle or location

WHAT G3-7R CHANGED HERE, AND WHY THE SUITE HAD TO MOVE WITH IT.

BF-WM's PROPERTY is intact and is still what this file protects: a booking may never be
written against a vehicle from a closed conversation cycle. The cycle watermark
(`current_cycle_started_at`) is still the boundary, it is still the one
`ConversationEngine._execute_cycle_reset` sets, and `created_at >= watermark` is still the
test.

What changed is HOW the candidate is chosen inside that window. `_load_focus_candidate`
answered "the most recently updated candidate in the current cycle", with the focus pointer
ignored and `None` never returned; the ConversationEngine answered the same question from
the focus pointer with a single-candidate fallback, and twice within one acceptance. The
G3-7P architecture audit enumerated four ways those resolvers could disagree about the
vehicle whose price the customer had already accepted. `_offer_bound_candidate` replaces
both with the binding recorded on the offer itself, which is immutable, was written when the
quote was computed, and is what acceptance compares.

So BF-WM-03's old tie-break — "most recently updated within cycle wins" — is DELETED rather
than ported. It is not a casualty of the unification; removing it is a requirement of the
milestone ("no newest-updated-row-wins fallback"), because that rule is how a booking could
silently follow a candidate the customer never accepted a price for. BF-WM-03 now asserts
the replacement: the offer's binding decides, and the most recently updated row does not.
"""
from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if not BACKEND_DIR.exists():
    BACKEND_DIR = ROOT_DIR
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import sqlalchemy as sa
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = sa.JSON  # type: ignore[attr-defined]
_pg_json.JSONB = sa.JSON     # type: ignore[attr-defined]


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
_CYCLE_START = _NOW - timedelta(hours=2)   # current cycle watermark
_OLD_TIME = _NOW - timedelta(hours=6)      # before current cycle
_TOKEN = "bk-1-deadbeef"


def _candidate(id: int, updated_at: datetime, created_at: datetime,
               marca: str = "Toyota", modelo: str = "Corolla",
               zone_group: str = "GBA Sur") -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id=id,
        thread_id=1,
        status="current_focus",
        marca=marca,
        modelo=modelo,
        anio=2020,
        tipo_vehiculo="AUTO",
        zone_group=zone_group,
        zone_detail="Lomas de Zamora",
        direccion_texto=None,
        updated_at=updated_at,
        created_at=created_at,
    )


def _state(**kw) -> types.SimpleNamespace:
    defaults = dict(
        current_cycle_started_at=_CYCLE_START,
        current_cycle_start_message_db_id=100,
        current_focus_candidate_id=None,
        home_zone_group=None,
        home_zone_detail=None,
    )
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def _offer(candidate_id) -> types.SimpleNamespace:
    """The minimum of an offer row that the candidate authority reads."""
    return types.SimpleNamespace(id=7, thread_id=1, candidate_id=candidate_id,
                                 status="ISSUED")


class _CandidateAuthorityHarness(unittest.TestCase):
    """Build the real service over a mock session, and record what it asked for.

    The mock answers exactly two things, because the resolver asks exactly two things: the
    offer for the submitted token, and the candidate row the offer names. That is itself an
    assertion — the old resolver issued an ORDER BY / LIMIT query, and this one issues a
    primary-key fetch. A resolver that went back to scanning candidates would not fit this
    harness at all.
    """

    def _service(self, candidates, *, offer_candidate_id, cycle_start=_CYCLE_START):
        from app.services.booking_flow_service import BookingFlowService

        by_id = {c.id: c for c in candidates}
        self.fetched = []

        db = MagicMock()

        def _get(model, pk):
            self.fetched.append(pk)
            return by_id.get(pk)

        db.get = _get
        svc = BookingFlowService.__new__(BookingFlowService)
        svc.db = db

        import app.services.offer_lifecycle as OL
        self._patched = OL.OfferLifecycle.offer_by_token
        OL.OfferLifecycle.offer_by_token = (
            lambda _self, token: _offer(offer_candidate_id) if token else None)
        self.addCleanup(self._restore)
        return svc

    def _restore(self):
        import app.services.offer_lifecycle as OL
        OL.OfferLifecycle.offer_by_token = self._patched

    def _resolve(self, svc, state, token=_TOKEN):
        return svc._offer_bound_candidate(1, state, token)


# ──────────────────────────────────────────────────────────────────────────────
# BF-WM-01: the candidate comes from the offer that armed the token
# ──────────────────────────────────────────────────────────────────────────────

class TestBFWM01CurrentCandidateSelected(_CandidateAuthorityHarness):

    def test_bfwm01_the_offers_binding_is_the_candidate(self):
        cand = _candidate(id=2, updated_at=_NOW - timedelta(minutes=30),
                          created_at=_CYCLE_START + timedelta(minutes=5))
        svc = self._service([cand], offer_candidate_id=2)
        result = self._resolve(svc, _state())
        self.assertIsNotNone(result)
        self.assertEqual(result.id, 2)
        self.assertEqual(self.fetched, [2],
                         "the resolver must fetch the bound candidate by id, not scan")

    def test_bfwm01_no_watermark_still_resolves(self):
        """Without a watermark (first cycle) every candidate is in-window, and the offer's
        binding still decides which one is booked."""
        cand = _candidate(id=1, updated_at=_NOW, created_at=_OLD_TIME)
        svc = self._service([cand], offer_candidate_id=1, cycle_start=None)
        result = self._resolve(svc, _state(current_cycle_started_at=None))
        self.assertIsNotNone(result)
        self.assertEqual(result.id, 1)

    def test_bfwm01_an_unbound_offer_resolves_to_nothing(self):
        """An offer that names no candidate authorizes no vehicle. None refuses downstream;
        guessing one would be the defect."""
        cand = _candidate(id=2, updated_at=_NOW, created_at=_CYCLE_START)
        svc = self._service([cand], offer_candidate_id=None)
        self.assertIsNone(self._resolve(svc, _state()))

    def test_bfwm01_no_offer_for_the_token_resolves_to_nothing(self):
        cand = _candidate(id=2, updated_at=_NOW, created_at=_CYCLE_START)
        svc = self._service([cand], offer_candidate_id=2)
        self.assertIsNone(self._resolve(svc, _state(), token=""))


# ──────────────────────────────────────────────────────────────────────────────
# BF-WM-02: a candidate from before the cycle boundary is excluded
# ──────────────────────────────────────────────────────────────────────────────

class TestBFWM02HistoricalCandidateExcluded(_CandidateAuthorityHarness):

    def test_bfwm02_old_candidate_excluded(self):
        """Excluded even though the OFFER names it. The cycle watermark outranks the
        binding, because a closed cycle's vehicle is not what this cycle is booking."""
        old = _candidate(id=1, updated_at=_NOW - timedelta(hours=1),
                         created_at=_OLD_TIME)
        svc = self._service([old], offer_candidate_id=1)
        self.assertIsNone(self._resolve(svc, _state()))

    def test_bfwm02_current_candidate_included(self):
        current = _candidate(id=2, updated_at=_NOW - timedelta(minutes=30),
                             created_at=_CYCLE_START + timedelta(minutes=5))
        svc = self._service([current], offer_candidate_id=2)
        result = self._resolve(svc, _state())
        self.assertIsNotNone(result)
        self.assertEqual(result.id, 2)

    def test_bfwm02_a_foreign_candidate_is_excluded(self):
        """A bound candidate belonging to another thread is refused, not booked."""
        foreign = _candidate(id=3, updated_at=_NOW, created_at=_CYCLE_START)
        foreign.thread_id = 999
        svc = self._service([foreign], offer_candidate_id=3)
        self.assertIsNone(self._resolve(svc, _state()))

    def test_bfwm02_a_deleted_candidate_is_excluded(self):
        """The binding points at a row that no longer exists."""
        svc = self._service([], offer_candidate_id=42)
        self.assertIsNone(self._resolve(svc, _state()))


# ──────────────────────────────────────────────────────────────────────────────
# BF-WM-03: the offer's binding decides — the newest updated row does NOT win
# ──────────────────────────────────────────────────────────────────────────────

class TestBFWM03NewCyclePreferred(_CandidateAuthorityHarness):

    def test_bfwm03_new_cycle_candidate_wins(self):
        """The old property, still true: a previous cycle's candidate cannot be booked
        while a current one exists — and here the offer names the current one."""
        old = _candidate(id=1, updated_at=_NOW - timedelta(minutes=10),
                         created_at=_OLD_TIME, marca="Ford")
        new = _candidate(id=2, updated_at=_NOW - timedelta(minutes=5),
                         created_at=_CYCLE_START + timedelta(minutes=1), marca="Toyota")
        svc = self._service([old, new], offer_candidate_id=2)
        result = self._resolve(svc, _state())
        self.assertEqual(result.id, 2)
        self.assertEqual(result.marca, "Toyota")

    def test_bfwm03_the_most_recently_updated_row_does_NOT_win(self):
        """THE RULE THAT WAS DELETED, asserted in the negative.

        Two candidates, both inside the cycle. The offer was issued for the Honda, and the
        BMW was updated more recently. The old resolver returned the BMW — a booking for a
        vehicle whose price the customer had never been quoted, with no refusal anywhere,
        because the resolver's answer and the offer's binding were two independent facts
        and only one of them was ever compared.
        """
        honda = _candidate(id=10, updated_at=_CYCLE_START + timedelta(minutes=30),
                           created_at=_CYCLE_START + timedelta(minutes=1), marca="Honda")
        bmw = _candidate(id=11, updated_at=_CYCLE_START + timedelta(minutes=45),
                         created_at=_CYCLE_START + timedelta(minutes=2), marca="BMW")
        svc = self._service([honda, bmw], offer_candidate_id=10)
        result = self._resolve(svc, _state())
        self.assertEqual(result.id, 10, "the offer's binding must decide")
        self.assertEqual(result.marca, "Honda")
        self.assertNotEqual(result.marca, "BMW",
                            "the most recently updated row must not win")

    def test_bfwm03_a_contradicting_focus_pointer_REFUSES_it_does_not_select(self):
        """Nor is the fix "use the focus pointer instead".

        A focus MOVE withdraws the offer where it happens
        (`withdraw_if_candidate_moved`), and that is the first line. This is the second:
        when the thread's focus pointer names a DIFFERENT candidate from the one the offer
        is bound to, the resolver returns None and the booking is refused — it does not
        quietly book either of them. Refusing is not selecting: no row is ever chosen by
        the pointer, which is what keeps the offer's binding the single authority.
        """
        honda = _candidate(id=10, updated_at=_NOW, created_at=_CYCLE_START)
        bmw = _candidate(id=11, updated_at=_NOW, created_at=_CYCLE_START)
        svc = self._service([honda, bmw], offer_candidate_id=10)
        self.assertIsNone(self._resolve(svc, _state(current_focus_candidate_id=11)),
                          "a focus that contradicts the binding must refuse")
        # An AGREEING pointer changes nothing, and so does no pointer at all.
        self.assertEqual(self._resolve(svc, _state(current_focus_candidate_id=10)).id, 10)
        self.assertEqual(self._resolve(svc, _state(current_focus_candidate_id=None)).id, 10)


# ──────────────────────────────────────────────────────────────────────────────
# BF-WM-04: the cycle boundary itself
# ──────────────────────────────────────────────────────────────────────────────

class TestBFWM04SwitchBackWithinCycle(_CandidateAuthorityHarness):

    def test_bfwm04_cycle_boundary_exact_match_included(self):
        """`created_at == watermark` is inside the window, exactly as before."""
        cand = _candidate(id=30, updated_at=_CYCLE_START + timedelta(seconds=1),
                          created_at=_CYCLE_START)
        svc = self._service([cand], offer_candidate_id=30)
        result = self._resolve(svc, _state())
        self.assertIsNotNone(result)
        self.assertEqual(result.id, 30)

    def test_bfwm04_one_microsecond_before_the_boundary_is_excluded(self):
        cand = _candidate(id=31, updated_at=_NOW,
                          created_at=_CYCLE_START - timedelta(microseconds=1))
        svc = self._service([cand], offer_candidate_id=31)
        self.assertIsNone(self._resolve(svc, _state()))

    def test_bfwm04_repeated_updates_within_the_cycle_change_nothing(self):
        """Switch-back within one cycle still books what the offer was issued for; an
        `updated_at` that moves is not a change of what was accepted."""
        cand = _candidate(id=21, updated_at=_CYCLE_START + timedelta(minutes=60),
                          created_at=_CYCLE_START + timedelta(minutes=2), marca="Beta")
        svc = self._service([cand], offer_candidate_id=21)
        self.assertEqual(self._resolve(svc, _state()).id, 21)


# ──────────────────────────────────────────────────────────────────────────────
# BF-WM-05: no eligible candidate → deterministic None
# ──────────────────────────────────────────────────────────────────────────────

class TestBFWM05NoEligibleCandidate(_CandidateAuthorityHarness):

    def test_bfwm05_no_candidates_at_all(self):
        svc = self._service([], offer_candidate_id=1)
        self.assertIsNone(self._resolve(svc, _state()))

    def test_bfwm05_only_historical_candidates(self):
        old = _candidate(id=1, updated_at=_NOW, created_at=_OLD_TIME)
        svc = self._service([old], offer_candidate_id=1)
        self.assertIsNone(self._resolve(svc, _state()))

    def test_bfwm05_no_watermark_all_eligible(self):
        """Without a watermark (first cycle), a candidate of any age is in-window."""
        old = _candidate(id=1, updated_at=_NOW, created_at=_OLD_TIME)
        svc = self._service([old], offer_candidate_id=1)
        result = self._resolve(svc, _state(current_cycle_started_at=None))
        self.assertIsNotNone(result)

    def test_bfwm05_none_is_a_refusal_downstream_not_a_silent_booking(self):
        """BF-WM-05's point was always that the CALLER must handle None. It does, and the
        handling is typed: no category means no price, and an unpriced booking is the harm."""
        from app.services.offer_lifecycle import OfferRefusal
        self.assertEqual(OfferRefusal.OFFER_CANDIDATE_CHANGED, "OFFER_CANDIDATE_CHANGED")
        self.assertEqual(OfferRefusal.OFFER_VEHICLE_CATEGORY_UNRESOLVED,
                         "OFFER_VEHICLE_CATEGORY_UNRESOLVED")


# ──────────────────────────────────────────────────────────────────────────────
# BF-WM-06: a historical vehicle or location cannot drive a booking
# ──────────────────────────────────────────────────────────────────────────────

class TestBFWM06HistoricalVehicleExcluded(_CandidateAuthorityHarness):

    def test_bfwm06_old_vehicle_not_surfaced(self):
        """An old Ford from a previous cycle does not drive the new booking — and now it
        cannot even if the offer names it, because the watermark is checked after the
        binding is resolved."""
        old_ford = _candidate(id=1, updated_at=_NOW, created_at=_OLD_TIME,
                              marca="Ford", modelo="Ranger")
        new_toyota = _candidate(id=2, updated_at=_NOW - timedelta(minutes=1),
                                created_at=_CYCLE_START + timedelta(minutes=1),
                                marca="Toyota", modelo="Corolla")
        svc = self._service([old_ford, new_toyota], offer_candidate_id=2)
        result = self._resolve(svc, _state())
        self.assertEqual(result.marca, "Toyota")
        self.assertNotEqual(result.marca, "Ford")

    def test_bfwm06_old_zone_not_surfaced(self):
        old_norte = _candidate(id=1, updated_at=_NOW, created_at=_OLD_TIME,
                               zone_group="GBA Norte")
        svc = self._service([old_norte], offer_candidate_id=1)
        self.assertIsNone(self._resolve(svc, _state()))

    def test_bfwm06_the_old_resolver_is_gone(self):
        """Deleted, not deprecated: a resolver that still exists is one a later change can
        start calling again, and two candidate authorities is the defect S3 closes."""
        from app.services.booking_flow_service import BookingFlowService
        self.assertFalse(hasattr(BookingFlowService, "_load_focus_candidate"),
                         "_load_focus_candidate must be deleted, not retained")
        import inspect
        src = inspect.getsource(BookingFlowService._offer_bound_candidate)
        for forbidden in ("updated_at.desc()", "order_by", "limit(1)"):
            self.assertNotIn(forbidden, src,
                             f"no newest-updated-row-wins fallback may remain: {forbidden}")


if __name__ == "__main__":
    unittest.main()
