"""The booking-offer lifecycle — G3-7P.

ONE authority decides whether a submitted booking Flow may become a booking, and ONE
authority invalidates an offer when the thing being booked changes.

Why this module exists. Until G3-7P the offer and the authority over it were the same
nullable string, `whatsapp_thread_states.flow_booking_token`, and the booking path asked

    if expected and flow_token and flow_token != expected:

A NULL `expected` short-circuits that, and NULL is the RESTING STATE of the column after six
production writes: booking consumption, cycle reset, the rejected-offer handoff, the
motorcycle handoff, the text-path correction withdrawal, and `booking_flow_service`'s own
consumption. The G3-7N review measured the consequence on real PostgreSQL — with `expected
IS NULL` the path accepted the old consumed token, a fabricated token, an empty token and NO
TOKEN AT ALL, each creating a ThreadRevision and a CRM Revision. G3-7N replaced one of those
six NULLs with a truthy tombstone, which closed one door and re-armed L4.7W5-F7A through the
eight places that read the column for truthiness.

So authority moved out of the column entirely:

    `whatsapp_booking_offers`  is the authority (row EXISTENCE + `status`)
    `flow_booking_token`       is a transport mirror, written only through here

ABSENCE IS A REFUSAL. `claim()` starts from "there is no offer" and only an ISSUED row,
bound to this thread, this cycle, this candidate, this candidate's CATEGORY and this
location, matching the submitted token's fingerprint, un-expired and never previously
accepted, can move it. Missing evidence is never permission.

THE BINDING CONTRACT (R2). Every stored field is exactly one of two things, and the
distinction is enforced rather than described — the R2 audit found `tipo_vehiculo` and
`cycle_key` recorded as if they were guards and never compared, which is a false assurance:

    ACCEPTANCE BINDINGS — compared on every claim, a mismatch refuses
        token_fingerprint   the lookup key, and UNIQUE in the schema
        thread_id           an offer belongs to one conversation
        cycle_key           a closed cycle's offer cannot book into the next one
        candidate_id        resolved through the SAME authority the caller books with
        tipo_vehiculo       a `PricingService.quote()` input — see A2 below
        zone_group          a `PricingService.quote()` input
        zone_detail         a `PricingService.quote()` input
        expires_at          pre-existing token-age policy
        status              only ISSUED may move

    HISTORICAL METADATA — recorded for audit, never an acceptance guard, and deliberately
    NOT compared, because the customer legitimately chooses inside the Flow:
        offered_date        the slot the customer was SHOWN. The submitted date/time is
        offered_time        what gets booked, revalidated by `ScheduleService` at
                            confirmation; comparing these would refuse a customer who
                            picked a different offered slot on the APPOINTMENT screen.
        flow_id             which Flow asset was dispatched
        contract_version    the payload version this row was written at
        refused_count       audit counters
        last_refusal
        last_refused_at
        accepted_revision_id        what the acceptance produced
        accepted_thread_revision_id
        terminal_at / accepted_at / terminal_reason   lifecycle timestamps and cause

A2 (R2). The G3-7P review measured the cost of storing a binding and not comparing it:
with the candidate's category changed in place from AUTO to PICKUP — same candidate id,
same zone, offer still ISSUED — the submission was ACCEPTED and produced a CRM Revision
carrying `tipo_vehiculo='PICKUP'` with `precio_total=NULL`. An unpriced booking, from an
offer issued for a different vehicle class. A changed pricing basis now requires a NEW
quote and a NEW offer; confirmation never silently recomputes one.

The raw token is never persisted: `token_fingerprint` is sha256(token).

Concurrency is the database's, not the application's. `needs_human` is not idempotency.

    * at most one LIVE offer per thread  — UNIQUE (thread_id) WHERE status = 'ISSUED'
    * at most one offer per token        — UNIQUE (token_fingerprint)
    * at most one ACCEPTANCE per offer   — SELECT ... FOR UPDATE, then a conditional
                                           UPDATE ... WHERE status = 'ISSUED'

`claim()` deliberately does NOT commit. The caller's existing single commit couples the
acceptance to the booking it authorizes, so either both become authoritative or neither
does; a caller that fails after claiming rolls the acceptance back with its booking.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Bump when the stored payload's meaning or structure changes.
OFFER_CONTRACT_VERSION = "offer/1.0"

#: Expiry is PRE-EXISTING policy, not a new one: `booking_flow_service.TOKEN_MAX_AGE_SECONDS`
#: already refused a token older than this on the data-exchange path. Imported lazily to
#: keep this module free of a circular import.
def _max_age_seconds() -> int:
    from .booking_flow_service import TOKEN_MAX_AGE_SECONDS
    return int(TOKEN_MAX_AGE_SECONDS)


class OfferStatus:
    """The complete lifecycle vocabulary. Anything else is refused by a CHECK constraint."""
    ISSUED = "ISSUED"
    ACCEPTED = "ACCEPTED"
    WITHDRAWN = "WITHDRAWN"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"

    TERMINAL = frozenset({ACCEPTED, WITHDRAWN, SUPERSEDED, EXPIRED})
    ALL = frozenset({ISSUED}) | TERMINAL


class OfferRefusal:
    """Why a submission may not book. Every one of these is an honest terminal answer and
    none of them is reported to an operator as a successful state change."""
    NO_TOKEN = "NO_TOKEN"                        # nothing submitted to match
    UNKNOWN_TOKEN = "UNKNOWN_TOKEN"              # fabricated, tampered, or never issued
    NO_OFFER_RECORD = "NO_OFFER_RECORD"          # a token mirror with no authority row
    FOREIGN_OFFER = "FOREIGN_OFFER"              # issued to a different thread
    OFFER_WITHDRAWN = "OFFER_WITHDRAWN"
    SUPERSEDED_OFFER = "SUPERSEDED_OFFER"
    OFFER_ALREADY_ACCEPTED = "OFFER_ALREADY_ACCEPTED"
    OFFER_EXPIRED = "OFFER_EXPIRED"
    OFFER_CANDIDATE_CHANGED = "OFFER_CANDIDATE_CHANGED"
    OFFER_LOCATION_CHANGED = "OFFER_LOCATION_CHANGED"
    #: R2/A2. The candidate's category moved after the offer was issued. Category is a
    #: `PricingService.quote()` input, so the quote the customer accepted no longer
    #: describes what would be booked — a new quote is required, never a silent recompute.
    OFFER_VEHICLE_CATEGORY_CHANGED = "OFFER_VEHICLE_CATEGORY_CHANGED"
    #: R2/A2. A category we cannot resolve cannot be priced, and an unpriced booking is
    #: the harm this refusal exists to prevent.
    OFFER_VEHICLE_CATEGORY_UNRESOLVED = "OFFER_VEHICLE_CATEGORY_UNRESOLVED"
    #: R2/A3. The offer belongs to a conversation cycle that has closed.
    OFFER_CYCLE_CHANGED = "OFFER_CYCLE_CHANGED"


#: Why an offer was invalidated. Stored in `terminal_reason`, so an operator can tell a
#: vehicle change from a zone change from a cycle reset without reading container logs.
class WithdrawReason:
    VEHICLE_CHANGED = "VEHICLE_CHANGED"
    LOCATION_CHANGED = "LOCATION_CHANGED"
    TEXT_CORRECTION = "TEXT_CORRECTION"
    CYCLE_RESET = "CYCLE_RESET"
    OFFER_REJECTED = "OFFER_REJECTED"
    HUMAN_HANDOFF = "HUMAN_HANDOFF"
    MOTO_HANDOFF = "MOTO_HANDOFF"
    REPLACED_BY_NEW_OFFER = "REPLACED_BY_NEW_OFFER"
    CONSUMED_BY_BOOKING = "CONSUMED_BY_BOOKING"


#: The ONE list of scheduling facts whose validity depended on the withdrawn offer. It lives
#: here and nowhere else — G3-7N had it inline in one handler, which is why the location
#: handler never got it (G3-7N review B-01).
VEHICLE_DEPENDENT_SCHEDULING_FIELDS = (
    "last_offered_slots", "last_visible_slots", "active_requested_date",
    "last_requested_time", "preferred_day", "preferred_time",
)


def token_fingerprint(token: Optional[str]) -> Optional[str]:
    """sha256(token) hex, or None. Never reversible, never the raw token."""
    if not token or not isinstance(token, str):
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_date(value):
    """Coerce `state.preferred_day` (an ISO string) to a date. None when unusable — the
    offered slot is evidence, never a guess."""
    from datetime import date as _d
    if isinstance(value, _d) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return _d.fromisoformat(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def _as_time(value):
    """Coerce `state.preferred_time` ("HH:MM" or "HH:MM:SS") to a time. None when unusable."""
    from datetime import time as _t
    if isinstance(value, _t):
        return value
    if not value:
        return None
    raw = str(value).strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class OfferClaim:
    """The typed answer to "may this submission book?". `ok` is the only thing that
    authorizes a booking; `refusal` is always populated when it is False."""
    ok: bool
    refusal: Optional[str] = None
    offer_id: Optional[int] = None
    fingerprint: Optional[str] = None
    status: Optional[str] = None
    offered_date: Optional[object] = None
    offered_time: Optional[object] = None

    def __bool__(self) -> bool:          # a claim is used as a permission, so make it read like one
        return bool(self.ok)


class OfferLifecycle:
    """Issue, supersede, withdraw, expire and accept booking offers. Never commits."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── clock ────────────────────────────────────────────────────────────────

    def _postgres(self) -> bool:
        try:
            return self.db.get_bind().dialect.name == "postgresql"
        except Exception:
            return False

    def _now(self) -> datetime:
        """Database time where available — an offer's expiry must not depend on one
        container's clock."""
        if self._postgres():
            try:
                now = self.db.execute(select(func.now())).scalar()
                if isinstance(now, datetime):
                    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
            except Exception:                       # pragma: no cover - defensive
                logger.warning("OFFER clock fell back to process time")
        return datetime.now(timezone.utc)

    @staticmethod
    def _aware(value) -> Optional[datetime]:
        if not isinstance(value, datetime):
            return None
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    # ── reads ────────────────────────────────────────────────────────────────

    def live_offer(self, thread_id: int):
        """The thread's one ISSUED offer, or None. The database guarantees "one"."""
        from ..models import WhatsAppBookingOffer as O
        return self.db.execute(
            select(O).where(O.thread_id == thread_id)
            .where(O.status == OfferStatus.ISSUED)
            .execution_options(populate_existing=True)).scalars().first()

    def offer_by_token(self, token: Optional[str]):
        from ..models import WhatsAppBookingOffer as O
        fp = token_fingerprint(token)
        if fp is None:
            return None
        return self.db.execute(
            select(O).where(O.token_fingerprint == fp)).scalars().first()

    def latest_offer(self, thread_id: int):
        """Most recent offer of any status — for the trace, so a refusal can name what it
        refused rather than reporting nothing at all."""
        from ..models import WhatsAppBookingOffer as O
        return self.db.execute(
            select(O).where(O.thread_id == thread_id)
            .order_by(O.id.desc())
            .execution_options(populate_existing=True)).scalars().first()

    # ── transitions ──────────────────────────────────────────────────────────

    def _terminalise(self, thread_id: int, *, status: str, reason: str) -> int:
        """Move every LIVE offer of this thread to a terminal status. Conditional on
        'ISSUED', so it is idempotent and cannot re-terminalise history."""
        from ..models import WhatsAppBookingOffer as O
        now = self._now()
        res = self.db.execute(
            update(O).where(O.thread_id == thread_id)
            .where(O.status == OfferStatus.ISSUED)
            .values(status=status, terminal_at=now, terminal_reason=reason[:64])
            .execution_options(synchronize_session="fetch"))
        return int(res.rowcount or 0)

    def withdraw(self, state, *, thread_id: int, reason: str,
                 clear_scheduling: bool = True) -> int:
        """Invalidate the thread's live offer because what it was computed from has moved.

        Clears the transport mirror and, by default, the scheduling facts the offer produced
        — the single field list lives in this module so no handler can own a partial copy of
        it. Returns how many offers were withdrawn (0 when there was nothing live, which is
        not an error: resubmitting an unchanged identity withdraws nothing).
        """
        n = self._terminalise(thread_id, status=OfferStatus.WITHDRAWN, reason=reason)
        self._clear_mirror(state)
        if clear_scheduling:
            self.clear_scheduling_fields(state)
        if n:
            logger.info("OFFER_WITHDRAWN thread_id=%s reason=%s offers=%s",
                        thread_id, reason, n)
        return n

    def supersede(self, state, *, thread_id: int, reason: str) -> int:
        """Invalidate the live offer because a newer one replaces it. Scheduling facts are
        NOT cleared: the caller is about to set its own."""
        n = self._terminalise(thread_id, status=OfferStatus.SUPERSEDED, reason=reason)
        if n:
            logger.info("OFFER_SUPERSEDED thread_id=%s reason=%s offers=%s",
                        thread_id, reason, n)
        return n

    def consume(self, state, *, thread_id: int, revision_id=None,
                thread_revision_id=None) -> None:
        """A booking was created from this thread's accepted offer: record what it produced.

        Acceptance already happened in `claim()`; this only attaches the artifacts, so a
        successful booking's offer row says WHICH revision it became.
        """
        from ..models import WhatsAppBookingOffer as O
        self.db.execute(
            update(O).where(O.thread_id == thread_id)
            .where(O.status == OfferStatus.ACCEPTED)
            .where(O.accepted_revision_id.is_(None))
            .values(accepted_revision_id=revision_id,
                    accepted_thread_revision_id=thread_revision_id)
            .execution_options(synchronize_session="fetch"))
        self._clear_mirror(state)

    def issue(self, state, *, thread_id: int, token: str, cycle_key: int,
              candidate_id=None, tipo_vehiculo=None, zone_group=None, zone_detail=None,
              offered_date=None, offered_time=None, flow_id=None):
        """Create the thread's one live offer, superseding whatever it replaces.

        The bindings are recorded here and compared at acceptance, which is what makes a
        booking refuse when the vehicle or the zone has moved — rather than relying on every
        handler to remember to withdraw.
        """
        from ..models import WhatsAppBookingOffer as O
        fp = token_fingerprint(token)
        if fp is None:
            raise ValueError("an offer cannot be issued without a token")
        self.supersede(state, thread_id=thread_id,
                       reason=WithdrawReason.REPLACED_BY_NEW_OFFER)
        now = self._now()
        offer = O(thread_id=thread_id, cycle_key=int(cycle_key or 0),
                  candidate_id=candidate_id, tipo_vehiculo=tipo_vehiculo,
                  zone_group=zone_group, zone_detail=zone_detail,
                  offered_date=_as_date(offered_date), offered_time=_as_time(offered_time),
                  flow_id=(str(flow_id)[:40] if flow_id else None),
                  contract_version=OFFER_CONTRACT_VERSION, token_fingerprint=fp,
                  status=OfferStatus.ISSUED, issued_at=now,
                  expires_at=now + timedelta(seconds=_max_age_seconds()))
        self.db.add(offer)
        self.db.flush()
        if state is not None:
            state.flow_booking_token = token      # transport mirror, never the authority
        logger.info("OFFER_ISSUED thread_id=%s offer_id=%s candidate=%s zone=%s/%s",
                    thread_id, offer.id, candidate_id, zone_group, zone_detail)
        return offer

    # ── acceptance ───────────────────────────────────────────────────────────

    def claim(self, state, *, thread_id: int, token: Optional[str],
              candidate_id=None, tipo_vehiculo=None, zone_group=None, zone_detail=None,
              cycle_key=None) -> OfferClaim:
        """May this submission become a booking? Fails closed, and never commits.

        Every check is a reason to refuse; nothing here can widen authority. The row is
        locked FOR UPDATE before anything is read from it, so a concurrent withdrawal or a
        second submission of the same token serialises behind this one, and the final
        transition is a conditional UPDATE off 'ISSUED' — so exactly one caller can ever
        accept a given offer.
        """
        from ..models import WhatsAppBookingOffer as O

        if not token or not isinstance(token, str) or not token.strip():
            return OfferClaim(False, OfferRefusal.NO_TOKEN)
        fp = token_fingerprint(token)

        # `populate_existing` is not optional here. A session that already holds this offer
        # — because an earlier operation in the same turn superseded or withdrew it through
        # a bulk UPDATE — would otherwise hand back the STALE identity-mapped instance, and
        # a terminal offer would read as ISSUED and be accepted. The same reason
        # `_lock_revision` uses it in the booking authority.
        stmt = select(O).where(O.token_fingerprint == fp).execution_options(
            populate_existing=True)
        if self._postgres():
            stmt = stmt.with_for_update()
        offer = self.db.execute(stmt).scalars().first()
        if offer is None:
            # No authority row for this token. Either fabricated/tampered, or a mirror left
            # by an application older than this table. Both are refusals: the only thing a
            # missing record can honestly authorize is nothing.
            mirror = getattr(state, "flow_booking_token", None)
            refusal = (OfferRefusal.NO_OFFER_RECORD if mirror
                       else OfferRefusal.UNKNOWN_TOKEN)
            return OfferClaim(False, refusal, fingerprint=fp)

        if offer.thread_id != thread_id:
            return self._refuse(offer, OfferRefusal.FOREIGN_OFFER, fp)

        if offer.status != OfferStatus.ISSUED:
            refusal = {
                OfferStatus.WITHDRAWN: OfferRefusal.OFFER_WITHDRAWN,
                OfferStatus.SUPERSEDED: OfferRefusal.SUPERSEDED_OFFER,
                OfferStatus.ACCEPTED: OfferRefusal.OFFER_ALREADY_ACCEPTED,
                OfferStatus.EXPIRED: OfferRefusal.OFFER_EXPIRED,
            }.get(offer.status, OfferRefusal.UNKNOWN_TOKEN)
            return self._refuse(offer, refusal, fp)

        expires = self._aware(offer.expires_at)
        if expires is not None and self._now() > expires:
            self._terminalise(thread_id, status=OfferStatus.EXPIRED,
                              reason=OfferRefusal.OFFER_EXPIRED)
            self._clear_mirror(state)
            return self._refuse(offer, OfferRefusal.OFFER_EXPIRED, fp,
                                status=OfferStatus.EXPIRED)

        # The bindings. A live offer for a vehicle or a zone that has since moved is not a
        # permission to book the old one — this is the second line behind withdrawal, and
        # the one that holds when a path forgot to withdraw.
        if offer.candidate_id != candidate_id:
            return self._refuse(offer, OfferRefusal.OFFER_CANDIDATE_CHANGED, fp)

        # ── R2/A3: the cycle this offer belongs to ────────────────────────────────
        # The ordinary cycle reset already withdraws, so this is the guard for a cycle
        # that moved without it. Compared because it is declared a binding; a recorded
        # binding that is never evaluated is a false assurance.
        if cycle_key is not None and int(offer.cycle_key or 0) != int(cycle_key):
            self._terminalise(thread_id, status=OfferStatus.WITHDRAWN,
                              reason=OfferRefusal.OFFER_CYCLE_CHANGED)
            self._clear_mirror(state)
            return self._refuse(offer, OfferRefusal.OFFER_CYCLE_CHANGED, fp,
                                status=OfferStatus.WITHDRAWN)

        # ── R2/A2: the PRICING CONTEXT, not merely the candidate's identity ───────
        # `PricingService.quote(db, tipo_vehiculo, zone_group, zone_detail)` takes the
        # category, so an offer whose category has moved describes a quote that no longer
        # applies — even though the candidate id and the zone are untouched. The value
        # compared is the canonical column on the candidate the CALLER resolved through its
        # own authority; nothing here reads customer prose, model wording or an alias.
        if offer.tipo_vehiculo != tipo_vehiculo:
            self._terminalise(thread_id, status=OfferStatus.WITHDRAWN,
                              reason=OfferRefusal.OFFER_VEHICLE_CATEGORY_CHANGED)
            self._clear_mirror(state)
            return self._refuse(offer, OfferRefusal.OFFER_VEHICLE_CATEGORY_CHANGED, fp,
                                status=OfferStatus.WITHDRAWN)
        # A category we cannot resolve cannot be priced, and an unpriced booking is exactly
        # the harm A2 produced. Refuse rather than book something with no price basis.
        if tipo_vehiculo is None:
            self._terminalise(thread_id, status=OfferStatus.WITHDRAWN,
                              reason=OfferRefusal.OFFER_VEHICLE_CATEGORY_UNRESOLVED)
            self._clear_mirror(state)
            return self._refuse(offer, OfferRefusal.OFFER_VEHICLE_CATEGORY_UNRESOLVED, fp,
                                status=OfferStatus.WITHDRAWN)

        if (offer.zone_group, offer.zone_detail) != (zone_group, zone_detail):
            return self._refuse(offer, OfferRefusal.OFFER_LOCATION_CHANGED, fp)

        now = self._now()
        res = self.db.execute(
            update(O).where(O.id == offer.id)
            .where(O.status == OfferStatus.ISSUED)          # the race is decided here
            .values(status=OfferStatus.ACCEPTED, accepted_at=now, terminal_at=now,
                    terminal_reason=WithdrawReason.CONSUMED_BY_BOOKING)
            # `fetch`, not False: after a claim, anything in this session that reads the
            # offer must see ACCEPTED. A stale ISSUED instance is exactly how a second
            # acceptance would talk itself into existence.
            .execution_options(synchronize_session="fetch"))
        if not res.rowcount:
            return self._refuse(offer, OfferRefusal.OFFER_ALREADY_ACCEPTED, fp,
                                status=OfferStatus.ACCEPTED)

        logger.info("OFFER_ACCEPTED thread_id=%s offer_id=%s", thread_id, offer.id)
        return OfferClaim(True, None, offer_id=offer.id, fingerprint=fp,
                          status=OfferStatus.ACCEPTED,
                          offered_date=offer.offered_date, offered_time=offer.offered_time)

    def _refuse(self, offer, refusal: str, fingerprint: str,
                status: Optional[str] = None) -> OfferClaim:
        """Record the refusal against the offer it refused, then report it.

        G3-7P §9. A Flow turn persists no hybrid-trace row, so without this the
        classification would exist only in container logs — which is precisely the
        container-dependence CLAUDE.md forbids for operator forensics. The counters are on
        the offer because that is the thing the submission was refused against.
        """
        from ..models import WhatsAppBookingOffer as O
        try:
            self.db.execute(
                update(O).where(O.id == offer.id)
                .values(refused_count=(O.refused_count + 1), last_refusal=refusal[:64],
                        last_refused_at=self._now())
                .execution_options(synchronize_session="fetch"))
        except Exception:                      # auditing must never widen authority
            logger.warning("OFFER refusal not recorded offer_id=%s", offer.id, exc_info=True)
        return OfferClaim(False, refusal, offer_id=offer.id, fingerprint=fingerprint,
                          status=status or offer.status)

    # ── state mirror ─────────────────────────────────────────────────────────

    @staticmethod
    def _clear_mirror(state) -> None:
        """The transport mirror follows the authority. Clearing it is now SAFE — authority
        lives in the offer row — and it is what restores L4.7W5-F7A: the eight production
        readers of `flow_booking_token` truthiness see "no live offer" again, so a withdrawn
        offer no longer blocks the deterministic scheduling path or reads as outstanding in
        the trace.
        """
        if state is None:
            return
        if getattr(state, "flow_booking_token", None) is not None:
            state.flow_booking_token = None

    @staticmethod
    def clear_scheduling_fields(state) -> None:
        """Null every scheduling fact whose validity depended on the withdrawn offer."""
        if state is None:
            return
        for field in VEHICLE_DEPENDENT_SCHEDULING_FIELDS:
            if getattr(state, field, None) is not None:
                setattr(state, field, None)
