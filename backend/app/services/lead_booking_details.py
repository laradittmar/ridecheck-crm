"""PRELAUNCH-MINIMUM-LAUNCH (B) — what the CRM lead card shows about a booking attempt.

The 2026-11-01 Wild: the customer who lost the slot race had an EMPTY card although the
database held their vehicle (current-focus candidate) all along — the card had no path to the
candidate — and their DETAILS had been discarded. This is a READ MODEL ONLY: it joins the
canonical rows at render time and copies nothing into `leads`, so there is no second source of
truth to drift when the customer changes vehicle or corrects an address.

    lead ──< whatsapp_threads (lead_id; the most recent thread)
               ├─ whatsapp_thread_states.current_focus_candidate_id
               │     └─ whatsapp_thread_candidates   vehicle, locality / zone
               └─ whatsapp_thread_evidence           the live BOOKING_FLOW_DETAILS rows:
                                                     name, e-mail, address, seller, URL,
                                                     requested date/time

The same view serves the winner (whose booking also has its own revision) and the loser
(who has none). Seller data is a third party's and is shown only when the customer supplied it.

Two kinds of fact, and they follow different rules:

PERSON (`PERSON_CLAIMS`: name, e-mail). They belong to the customer, so they follow the thread:
the displayed attempt's own value, else the thread's most recent, else the committed booking's
(review F-03, F-14). Only what is listed there is treated this way.

CAR, everything else: plate, mileage, listing URL, seller, inspection address, requested slot.
They describe one vehicle and one inspection, and an operator acts on them, so they appear only
beside that vehicle and never fall back to the thread:
* A COMMITTED card (the displayed candidate has a booking; or, with no candidate to display, the
  thread's latest booking) takes them from that booking alone: `thread_revisions`, written by the
  booking's own transaction, and `revisions` for `kilometraje` / `dominio`, which the operator
  edits and the card's revision lines show. A blank there stays blank. No draft fills it, so an
  old draft cannot contradict a correction.
* An UNCOMMITTED card takes them from the attempt it displays, the focus candidate in the current
  cycle (`attempt_details`, the scope `prefill_details` uses).
* Otherwise they are not shown. An unknown is better than another car's seller.
Vehicle and locality are read from the displayed candidate itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select


@dataclass(frozen=True)
class LeadBookingDetails:
    thread_id: int
    customer_name: Optional[str] = None
    email: Optional[str] = None
    vehicle: Optional[str] = None
    locality: Optional[str] = None
    inspection_address: Optional[str] = None
    requested_slot: Optional[str] = None
    seller_name: Optional[str] = None
    seller_phone: Optional[str] = None
    listing_url: Optional[str] = None
    #: Digits as stored; `vehicle_identity.format_kilometraje` is what displays it.
    kilometraje: Optional[str] = None
    dominio: Optional[str] = None

    def has_content(self) -> bool:
        return any((self.customer_name, self.email, self.vehicle, self.locality,
                    self.inspection_address, self.requested_slot, self.seller_name,
                    self.seller_phone, self.listing_url, self.kilometraje, self.dominio))


#: Facts about the customer. Every other claim is about one car, including any added later:
#: being scoped is the default, and following the thread has to be asked for here.
PERSON_CLAIMS = frozenset({"FLOW_CUSTOMER_NAME", "FLOW_CUSTOMER_EMAIL"})


def _vehicle(c) -> Optional[str]:
    if c is None:
        return None
    text = " ".join(str(x) for x in (c.marca, c.modelo, c.anio) if x)
    if c.tipo_vehiculo:
        text = f"{text} ({c.tipo_vehiculo})" if text else str(c.tipo_vehiculo)
    return text or None


def _locality(c) -> Optional[str]:
    if c is None:
        return None
    return ", ".join(x for x in (c.zone_detail, c.zone_group) if x) or None


def lead_booking_details(db, lead_ids) -> dict:
    """{lead_id: LeadBookingDetails} for the leads that have a WhatsApp thread. Read only."""
    from ..models import WhatsAppThread, WhatsAppThreadCandidate, WhatsAppThreadState
    from .booking_flow_details import attempt_details, live_details
    from .clarification_attempt import cycle_key
    ids = sorted({int(x) for x in (lead_ids or []) if x is not None})
    if not ids:
        return {}
    threads = db.execute(
        select(WhatsAppThread.id, WhatsAppThread.lead_id).where(
            WhatsAppThread.lead_id.in_(ids))
        .order_by(WhatsAppThread.lead_id, WhatsAppThread.last_message_at.desc().nullslast(),
                  WhatsAppThread.id.desc())).all()
    thread_for: dict = {}
    for thread_id, lead_id in threads:
        thread_for.setdefault(int(lead_id), int(thread_id))
    if not thread_for:
        return {}
    tids = list(thread_for.values())
    states = {int(row.thread_id): row for row in db.execute(
        select(WhatsAppThreadState.thread_id, WhatsAppThreadState.current_focus_candidate_id,
               WhatsAppThreadState.current_cycle_start_message_db_id)
        .where(WhatsAppThreadState.thread_id.in_(tids))).all()}
    focus = {t: row.current_focus_candidate_id for t, row in states.items()}
    cand_ids = [int(c) for c in focus.values() if c is not None]
    candidates = {}
    if cand_ids:
        for c in db.execute(select(WhatsAppThreadCandidate).where(
                WhatsAppThreadCandidate.id.in_(cand_ids))).scalars().all():
            candidates[int(c.id)] = c
    details = live_details(db, tids)
    # The attempt each card displays: only a focus candidate of that very thread has one.
    attempts = {t: (int(focus[t]), cycle_key(states[t])) for t in tids
                if focus.get(t) is not None and int(focus[t]) in candidates
                and int(candidates[int(focus[t])].thread_id) == t}
    displayed = attempt_details(db, attempts)
    # R1: the committed booking's own copy (latest booked/completed row), per thread for the
    # person and per candidate for the car: a thread can hold bookings of two cars.
    from ..models import ThreadRevision as TR
    booked: dict = {}
    booked_car: dict = {}
    for tr in db.execute(select(TR).where(TR.thread_id.in_(tids),
                                          TR.status.in_(("booked", "completed")))
                         .order_by(TR.thread_id, TR.id)).scalars().all():
        booked[int(tr.thread_id)] = tr
        booked_car[(int(tr.thread_id), tr.candidate_id)] = tr
    out = {}
    for lead_id, thread_id in thread_for.items():
        cid = focus.get(thread_id)
        c = candidates.get(int(cid)) if cid is not None else None
        if c is not None and int(c.thread_id) != int(thread_id):
            c = None                            # never another thread's vehicle
        own = displayed.get(thread_id, {})
        d = {k: v for k, v in details.get(thread_id, {}).items() if k in PERSON_CLAIMS}
        for claim_type in PERSON_CLAIMS & set(own):
            if own[claim_type]:
                d[claim_type] = own[claim_type]
            else:
                d.pop(claim_type, None)           # this attempt emptied it
        tr = booked.get(thread_id)
        if tr is not None:
            for claim_type, value in (("FLOW_CUSTOMER_NAME", tr.buyer_name),
                                      ("FLOW_CUSTOMER_EMAIL", tr.buyer_email)):
                if not d.get(claim_type) and value:
                    d[claim_type] = value
        committed = tr if c is None else booked_car.get((thread_id, int(c.id)))
        if committed is not None:
            slot = (f"{committed.scheduled_date.isoformat()} "
                    f"{committed.scheduled_time.strftime('%H:%M')}"
                    if committed.scheduled_date is not None
                    and committed.scheduled_time is not None else None)
            d.update({"FLOW_INSPECTION_ADDRESS": committed.address,
                      "FLOW_SELLER_NAME": committed.seller_name,
                      "FLOW_SELLER_PHONE": getattr(committed, "seller_phone", None),
                      "FLOW_LISTING_URL": committed.publication_url,
                      "FLOW_REQUESTED_SLOT": slot})
        else:
            d.update({k: v for k, v in own.items() if k not in PERSON_CLAIMS and v})
        out[lead_id] = LeadBookingDetails(
            thread_id=thread_id, customer_name=d.get("FLOW_CUSTOMER_NAME"),
            email=d.get("FLOW_CUSTOMER_EMAIL"), vehicle=_vehicle(c), locality=_locality(c),
            inspection_address=d.get("FLOW_INSPECTION_ADDRESS"),
            requested_slot=d.get("FLOW_REQUESTED_SLOT"),
            seller_name=d.get("FLOW_SELLER_NAME"), seller_phone=d.get("FLOW_SELLER_PHONE"),
            listing_url=d.get("FLOW_LISTING_URL"),
            kilometraje=d.get("FLOW_VEHICLE_MILEAGE"), dominio=d.get("FLOW_VEHICLE_PLATE"))
    return out
