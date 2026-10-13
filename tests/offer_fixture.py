"""Make a hand-set `flow_booking_token` into a real, claimable offer — G3-7P.

Before G3-7P a fixture could simulate "a booking Flow was dispatched" by assigning
`WhatsAppThreadState.flow_booking_token`. Authority now lives in `whatsapp_booking_offers`
and the ABSENCE of a row is a refusal, not a permission (that permissiveness is the defect
G3-7P closes: with `expected IS NULL` the booking path used to accept the old consumed
token, a fabricated token, an empty token and no token at all). So a fixture that wants a
bookable offer has to issue one.

This is the single place that does it, so every suite keeps the same shape as the authority
evolves — and the bindings default to the state's own canonical values, because an offer
whose candidate or zone disagrees with the thread is refused by design.
"""
from __future__ import annotations

#: The stub leaves the candidate unpinned so `_offer_bound_candidate` falls through to the
#: thread's own focus — which is what these field-mapping suites set up and assert on.
_STUB_CANDIDATE = None


def seed_live_offer(db, state, *, thread_id=None, token=None, candidate_id=None,
                    zone_group=None, zone_detail=None, tipo_vehiculo=None,
                    offered_date=None, offered_time=None, flow_id=None, commit=True):
    """Issue one ISSUED offer for `state`, bound to the thread's canonical values.

    Returns the offer row, or None when the state carries no token to bind to.
    """
    from app.services.offer_lifecycle import OfferLifecycle

    token = token or getattr(state, "flow_booking_token", None)
    if not token:
        return None
    thread_id = thread_id if thread_id is not None else getattr(state, "thread_id", None)
    if thread_id is None:
        return None

    # One token -> at most one offer is a DATABASE constraint
    # (UNIQUE(token_fingerprint)), and several fixtures use a FIXED token string while
    # sharing one module-level database across tests. Production never does that:
    # `make_booking_token` carries a timestamp and a nonce. So when the token has already
    # been issued, mint a distinct one and tell the state about it — the fixture keeps
    # working and the invariant keeps holding, instead of the helper weakening it.
    from app.services.offer_lifecycle import OfferLifecycle as _OL
    if _OL(db).offer_by_token(token) is not None:
        import secrets as _secrets
        token = f"{token}-{_secrets.token_hex(8)}"
        state.flow_booking_token = token
    # Mirror `_get_active_inspection_location`'s precedence exactly: the focused
    # candidate's zone is authoritative and `state.home_zone_*` is only the fallback. An
    # offer bound to the other one would be refused at acceptance for a location change
    # that never happened.
    _resolved = _active_candidate_and_zone(db, state)
    _zone = _resolved[1]
    offer = OfferLifecycle(db).issue(
        state, thread_id=thread_id, token=token,
        cycle_key=getattr(state, "current_cycle_start_message_db_id", None) or 0,
        candidate_id=candidate_id if candidate_id is not None else _resolved[0],
        # R2: the offer carries the pricing context, and acceptance compares it against
        # the SAME resolved candidate. A fixture that issued an offer with no category
        # would be issuing one that can never be accepted.
        tipo_vehiculo=(tipo_vehiculo if tipo_vehiculo is not None else _resolved[2]),
        zone_group=zone_group if zone_group is not None else _zone[0],
        zone_detail=zone_detail if zone_detail is not None else _zone[1],
        offered_date=(offered_date if offered_date is not None
                      else getattr(state, "preferred_day", None)),
        offered_time=(offered_time if offered_time is not None
                      else getattr(state, "preferred_time", None)),
        flow_id=flow_id)
    if commit:
        db.commit()
    return offer


def _active_candidate_and_zone(db, state):
    """(candidate_id, (zone_group, zone_detail), tipo_vehiculo) as ACCEPTANCE resolves them.

    Both consumers now share one rule (`_get_active_inspection_location` /
    `_location_from_candidate`): the focused candidate is authoritative only when it
    actually HAS a location, and a MISSING HALF is filled from the thread state. Getting
    that fallback wrong here would bind the offer to a zone the claim then reads
    differently, and the booking would be refused for a location change that never
    happened — which is how this helper was wrong on its first draft.
    """
    from sqlalchemy import select
    from app.models import WhatsAppThreadCandidate as C
    home_group = getattr(state, "home_zone_group", None)
    home_detail = getattr(state, "home_zone_detail", None)
    focus = None
    focus_id = getattr(state, "current_focus_candidate_id", None)
    thread_id = getattr(state, "thread_id", None)
    rows = []
    if thread_id is not None:
        rows = list(db.execute(select(C).where(C.thread_id == thread_id)
                               .order_by(C.id)).scalars())
    if focus_id is not None:
        focus = next((c for c in rows if c.id == focus_id), None)
    if focus is None:
        focus = next((c for c in rows if c.status == "current_focus"), None)
    if focus is None and len(rows) == 1:
        focus = rows[0]            # the documented single-candidate fallback
    zone = (home_group, home_detail)
    if focus is not None:
        cand_group = focus.zone_group or None
        cand_detail = focus.zone_detail or None
        if cand_group or cand_detail:
            zone = (cand_group or home_group, cand_detail or home_detail)
    return (getattr(focus, "id", None), zone,
            getattr(focus, "tipo_vehiculo", None))


def start_offer_claim_stub(test_case):
    """Let a MagicMock-session unit test reach the booking body (G3-7P, extended by G3-7R).

    Every decision on the acceptance path is a DATABASE decision, and a unit test whose
    session is a `MagicMock` cannot answer any of them — the mock returns a mock, which each
    control correctly reads as "not this thread's" and refuses. These tests assert the
    booking body's FIELD MAPPING, not acceptance, so the controls are stubbed HERE, in one
    declared place, and proven for real on PostgreSQL.

    FOUR things are stubbed, and each one names where it is really proven:

      `OfferLifecycle.claim`              acceptance, and every refusal class
                                          -> test_l4_7w5_g3_7p_offer_authority_pg.py
      `OfferLifecycle.admit`   (G3-7R/S2) database-first admission, replay and fail-closed
                                          -> G37RAdmissionScope (sealed, no database) and
                                             test_l4_7w5_g3_7r_booking_projection_pg.py P8
      `_offer_bound_candidate` (G3-7R/S3) the candidate authority. The stub applies the REAL
                                          rule — resolve the admitted offer's `candidate_id`
                                          — against `ctx.candidates` instead of through
                                          `db.get()`, so the field-mapping assertions still
                                          mean something. Cycle-window and deleted-row
                                          refusals -> the PG suites.
      `_revalidate_booking_slot` (G3-7R/S5) final availability. Needs a real
                                          `ScheduleService` over real occupying rows
                                          -> test_l4_7w5_g3_7r_booking_projection_pg.py P7

    Registers its own cleanup on `test_case`.
    """
    from unittest import mock
    from app.services.conversation_engine import ConversationEngine
    from app.services.offer_lifecycle import (AdmissionOutcome, OfferAdmission, OfferClaim,
                                              OfferLifecycle)

    def _admit(self, *, thread_id, token):
        """GRANTED, bound to whatever this thread's canonical values are.

        Deliberately still fails closed on a missing token: that is pure logic with no
        database in it, and it is the single most important thing admission does — the
        G3-7N review measured NO TOKEN AT ALL booking successfully.
        """
        if not token or not isinstance(token, str) or not token.strip():
            from app.services.offer_lifecycle import OfferRefusal
            return OfferAdmission(AdmissionOutcome.REFUSED, OfferRefusal.NO_TOKEN)
        return OfferAdmission(AdmissionOutcome.GRANTED, None, offer_id=1,
                              fingerprint="0" * 64, status="ISSUED",
                              candidate_id=_STUB_CANDIDATE, tipo_vehiculo=None,
                              zone_group=None, zone_detail=None, cycle_key=0)

    def _bound_candidate(self, ctx, admission):
        """The real S3 rule, resolved in memory: the offer's candidate, or the thread's
        focus when the stub did not pin one."""
        candidate_id = getattr(admission, "candidate_id", None)
        candidates = list(getattr(ctx, "candidates", None) or ())
        if candidate_id is not None:
            for candidate in candidates:
                if getattr(candidate, "id", None) == candidate_id:
                    return candidate
        focus_id = getattr(getattr(ctx, "state", None), "current_focus_candidate_id", None)
        for candidate in candidates:
            if getattr(candidate, "id", None) == focus_id:
                return candidate
        return candidates[0] if len(candidates) == 1 else None

    admit = mock.patch.object(OfferLifecycle, "admit", autospec=True, side_effect=_admit)
    # `verify_bindings` is the same database decision `claim` is — G3-7R split it out so the
    # bindings could be checked BEFORE availability revalidation. Stubbed for the same
    # reason and proven in the same places.
    bindings = mock.patch.object(OfferLifecycle, "verify_bindings", autospec=True,
                                 return_value=None)
    bindings.start()
    test_case.addCleanup(bindings.stop)
    bound = mock.patch.object(ConversationEngine, "_offer_bound_candidate", autospec=True,
                              side_effect=_bound_candidate)
    revalidate = mock.patch.object(ConversationEngine, "_revalidate_booking_slot",
                                   autospec=True, return_value=None)
    admit.start(); bound.start(); revalidate.start()
    test_case.addCleanup(admit.stop)
    test_case.addCleanup(bound.stop)
    test_case.addCleanup(revalidate.stop)

    claim = mock.patch.object(
        OfferLifecycle, "claim",
        return_value=OfferClaim(True, None, offer_id=1, fingerprint="0" * 64,
                                status="ACCEPTED"))
    # `consume` is stubbed but NOT inert: clearing the transport mirror is its real,
    # observable effect, and suites legitimately assert it. A no-op here would quietly
    # hide a regression in the mirror's lifecycle.
    def _consume(self, state, *, thread_id=None, revision_id=None,
                 thread_revision_id=None):
        if state is not None and getattr(state, "flow_booking_token", None) is not None:
            state.flow_booking_token = None

    consume = mock.patch.object(OfferLifecycle, "consume", autospec=True,
                                side_effect=_consume)
    claim.start(); consume.start()
    test_case.addCleanup(claim.stop)
    test_case.addCleanup(consume.stop)
