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
    """Let a MagicMock-session unit test reach the booking body (G3-7P).

    `claim()` is a DATABASE decision: it reads `whatsapp_booking_offers` under a row lock.
    A unit test whose session is a `MagicMock` cannot answer it — the mock returns a mock,
    which the claim correctly reads as "not this thread's offer" and refuses. These tests
    assert the booking body's FIELD MAPPING, not acceptance, so the authority is stubbed
    here and proven for real in `test_l4_7w5_g3_7p_offer_authority_pg.py`, which runs
    every refusal class against PostgreSQL.

    Registers its own cleanup on `test_case`.
    """
    from unittest import mock
    from app.services.offer_lifecycle import OfferClaim, OfferLifecycle

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
