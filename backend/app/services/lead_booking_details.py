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

R1 (review F-03): for a COMMITTED booking the card no longer depends on the draft evidence
rows. Any field they do not provide is taken from the thread's latest committed booking
`thread_revisions` row (status booked/completed — written by the booking's own transaction,
including `seller_phone`). Evidence first (it is the customer's latest submission, and the
loser's only record), the committed booking second. Still read only.

Accepted debt (review F-14, unchanged): the live Flow details are per THREAD, not per
conversation cycle, so a later conversation on the same thread shows the earlier details
until the customer submits new ones.
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

    def has_content(self) -> bool:
        return any((self.customer_name, self.email, self.vehicle, self.locality,
                    self.inspection_address, self.requested_slot, self.seller_name,
                    self.seller_phone, self.listing_url))


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
    from .booking_flow_details import live_details
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
    focus = dict(db.execute(select(WhatsAppThreadState.thread_id,
                                   WhatsAppThreadState.current_focus_candidate_id)
                            .where(WhatsAppThreadState.thread_id.in_(tids))).all())
    cand_ids = [int(c) for c in focus.values() if c is not None]
    candidates = {}
    if cand_ids:
        for c in db.execute(select(WhatsAppThreadCandidate).where(
                WhatsAppThreadCandidate.id.in_(cand_ids))).scalars().all():
            candidates[int(c.id)] = c
    details = live_details(db, tids)
    # R1: the committed booking's own copy, per thread (latest booked/completed row).
    from ..models import ThreadRevision as TR
    booked: dict = {}
    for tr in db.execute(select(TR).where(TR.thread_id.in_(tids),
                                          TR.status.in_(("booked", "completed")))
                         .order_by(TR.thread_id, TR.id)).scalars().all():
        booked[int(tr.thread_id)] = tr
    out = {}
    for lead_id, thread_id in thread_for.items():
        cid = focus.get(thread_id)
        c = candidates.get(int(cid)) if cid is not None else None
        if c is not None and int(c.thread_id) != int(thread_id):
            c = None                            # never another thread's vehicle
        d = dict(details.get(thread_id, {}))
        tr = booked.get(thread_id)
        if tr is not None:
            slot = (f"{tr.scheduled_date.isoformat()} {tr.scheduled_time.strftime('%H:%M')}"
                    if tr.scheduled_date is not None and tr.scheduled_time is not None else None)
            for claim_type, value in (("FLOW_CUSTOMER_NAME", tr.buyer_name),
                                      ("FLOW_CUSTOMER_EMAIL", tr.buyer_email),
                                      ("FLOW_INSPECTION_ADDRESS", tr.address),
                                      ("FLOW_SELLER_NAME", tr.seller_name),
                                      ("FLOW_SELLER_PHONE", getattr(tr, "seller_phone", None)),
                                      ("FLOW_LISTING_URL", tr.publication_url),
                                      ("FLOW_REQUESTED_SLOT", slot)):
                if not d.get(claim_type) and value:
                    d[claim_type] = value
        out[lead_id] = LeadBookingDetails(
            thread_id=thread_id, customer_name=d.get("FLOW_CUSTOMER_NAME"),
            email=d.get("FLOW_CUSTOMER_EMAIL"), vehicle=_vehicle(c), locality=_locality(c),
            inspection_address=d.get("FLOW_INSPECTION_ADDRESS"),
            requested_slot=d.get("FLOW_REQUESTED_SLOT"),
            seller_name=d.get("FLOW_SELLER_NAME"), seller_phone=d.get("FLOW_SELLER_PHONE"),
            listing_url=d.get("FLOW_LISTING_URL"))
    return out
