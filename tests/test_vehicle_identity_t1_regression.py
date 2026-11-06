"""PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-ESCAPING-CONTROLLED-MERGE: finding T-1.

The R2A focused review found behaviour of the card's "Datos de reserva" block that is correct in
the product but that no committed test protected. These tests carry its probes b01-b03, r1a, r1a2,
r1b, r1c and the foreign-thread case into the suite. They pin existing behaviour; production code
was not changed for them. The PostgreSQL half is test_vehicle_identity_t1_regression_pg.py.

All values are synthetic.
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest
from datetime import datetime, time, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _sb2b_harness as H  # noqa: E402
import test_minimum_launch as ML  # noqa: E402
import test_vehicle_identity_recovery as V  # noqa: E402

from sqlalchemy.orm import Session  # noqa: E402

from app.models import (ThreadRevision, WhatsAppThreadCandidate,  # noqa: E402
                        WhatsAppThreadEvidence, WhatsAppThreadState)
from app.services import booking_flow_details as D  # noqa: E402

CAR_ONE = {"name": "Tea Uno", "email": "t1@example.invalid",
           "inspection_address": "Pasaje Uno 11", "seller_name": "Selluno Alfa",
           "seller_phone": "1177770001", "listing_url": "https://example.invalid/uno",
           "vehicle_mileage": "71.000", "vehicle_plate": "AA111AA"}
CAR_ONE_VALUES = ("Pasaje Uno 11", "Selluno Alfa", "1177770001", "example.invalid/uno",
                  "71.000", "AA111AA")
CAR_TWO_SELLER = "Selldos Beta"
SCOPED = ("inspection_address", "requested_slot", "seller_name", "seller_phone", "listing_url",
          "kilometraje", "dominio")


def scoped(details):
    return {field: getattr(details, field) for field in SCOPED}


def split_block(html):
    """(the "Datos de reserva" block, the rest of the card)."""
    found = re.search(r'<div class="leadBookingDetails".*?</div></div>', html, re.S)
    return (found.group(0), html[:found.start()] + html[found.end():]) if found else ("", html)


class _Booked(unittest.TestCase):
    def setUp(self):
        self.engine = H.sqlite_engine()

    def book(self, at=None):
        w = H.seed(self.engine, **({"at": at} if at else {}))
        with ML.Meta():
            V.prepare(self.engine, w, typed=CAR_ONE)
            self.assertEqual(V.confirm(self.engine, w, typed=CAR_ONE)["screen"], "SUCCESS")
        return w

    def set_booking(self, w, **fields):
        with Session(self.engine) as db:
            db.query(ThreadRevision).filter_by(thread_id=w.thread_id).update(fields)
            db.commit()

    def set_focus(self, w, candidate_id):
        with Session(self.engine) as db:
            db.query(WhatsAppThreadState).filter_by(thread_id=w.thread_id).update(
                {"current_focus_candidate_id": candidate_id})
            db.commit()


class T1_BookingSelection(_Booked):
    """Which booking counts as committed for the DISPLAYED candidate (b01-b03)."""

    def add_booking(self, w, *, status, seller):
        booked = None
        with Session(self.engine) as db:
            booked = db.query(ThreadRevision).filter_by(thread_id=w.thread_id).first()
            db.add(ThreadRevision(thread_id=w.thread_id, candidate_id=booked.candidate_id,
                                  status=status, seller_name=seller,
                                  scheduled_date=booked.scheduled_date,
                                  scheduled_time=time(11, 30)))
            db.commit()

    def test_t1_01_latest_qualifying_booking_of_the_displayed_candidate_wins(self):
        w = self.book()
        self.add_booking(w, status="booked", seller="Later Booking")
        details, html = V.card(self.engine, w.lead_id)
        self.assertEqual(details.seller_name, "Later Booking")
        self.assertIsNone(details.inspection_address)      # the whole booking, not a blend of two
        self.assertEqual(details.requested_slot[-5:], "11:30")
        self.assertNotIn(CAR_ONE["seller_name"], split_block(html)[0])

    def test_t1_02_a_later_cancelled_booking_does_not_displace_the_qualifying_one(self):
        w = self.book()
        self.add_booking(w, status="cancelled", seller="Later Cancelled")
        details, html = V.card(self.engine, w.lead_id)
        self.assertEqual(details.seller_name, CAR_ONE["seller_name"])
        self.assertNotIn("Later Cancelled", html)

    def test_t1_03_cancelled_booking_does_not_count_as_committed(self):
        w = self.book()
        self.set_booking(w, status="cancelled", seller_name="Cancelled Value")
        details, html = V.card(self.engine, w.lead_id)
        self.assertEqual(details.seller_name, CAR_ONE["seller_name"])    # the attempt's own draft
        self.assertEqual(details.dominio, CAR_ONE["vehicle_plate"])      # an uncommitted card
        self.assertNotIn("Cancelled Value", html)

    def test_t1_04_completed_booking_counts_as_committed(self):
        w = self.book()
        self.set_booking(w, status="completed", seller_name="Completed Value")
        details, _ = V.card(self.engine, w.lead_id)
        self.assertEqual(details.seller_name, "Completed Value")
        self.assertIsNone(details.dominio)                 # committed: the revision lines own it


class T1_CandidateLessBooking(_Booked):
    """A booking with no candidate cannot be tied to a displayed car: R-1, r1a, r1a2, r1b, r1c."""

    def book_detached(self):
        w = self.book()
        self.set_booking(w, candidate_id=None)
        return w

    def drop_flow_drafts(self, w):
        with Session(self.engine) as db:
            db.query(WhatsAppThreadEvidence).filter_by(thread_id=w.thread_id,
                                                       producer=D.PRODUCER).delete()
            db.commit()

    def test_t1_05_fails_closed_when_a_focus_candidate_exists(self):
        w = self.book_detached()
        self.drop_flow_drafts(w)
        details, html = V.card(self.engine, w.lead_id)
        self.assertEqual(set(scoped(details).values()), {None})
        block, rest = split_block(html)
        for value in CAR_ONE_VALUES:
            self.assertNotIn(value, block, value)
        self.assertEqual(details.vehicle, "Peugeot 208 (Auto)")          # the displayed car only
        self.assertEqual((details.customer_name, details.email),
                         (CAR_ONE["name"], CAR_ONE["email"]))            # the person survives
        self.assertIn(CAR_ONE["vehicle_plate"], rest)                    # revision lines keep it
        with Session(self.engine) as db:                                 # hidden, never lost
            row = db.query(ThreadRevision).filter_by(thread_id=w.thread_id).one()
            self.assertEqual(row.address, CAR_ONE["inspection_address"])

    def test_t1_06_is_shown_when_no_focus_candidate_exists(self):
        w = self.book_detached()
        self.set_focus(w, None)
        details, _ = V.card(self.engine, w.lead_id)
        self.assertEqual(details.seller_name, CAR_ONE["seller_name"])
        self.assertEqual(details.inspection_address, CAR_ONE["inspection_address"])
        self.assertIsNone(details.vehicle)

    def test_t1_07_displayed_candidates_own_draft_beats_the_unrelated_booking(self):
        w = self.book_detached()
        self.set_booking(w, seller_name="Booking Only", address="Booking Only Calle 1")
        details, html = V.card(self.engine, w.lead_id)
        self.assertEqual(details.seller_name, CAR_ONE["seller_name"])
        self.assertEqual(details.inspection_address, CAR_ONE["inspection_address"])
        self.assertNotIn("Booking Only", html)

    def test_t1_08_another_cars_draft_shows_that_car_only(self):
        w = self.book_detached()
        with Session(self.engine) as db:
            other = WhatsAppThreadCandidate(thread_id=w.thread_id, marca="Fiat", modelo="Uno",
                                            tipo_vehiculo="Auto", status="current_focus")
            db.add(other)
            db.commit()
            other_id = int(other.id)
            db.query(WhatsAppThreadState).filter_by(thread_id=w.thread_id).update(
                {"current_focus_candidate_id": other_id})
            D.write_detail_rows(db, thread_id=w.thread_id, offer_id=None, candidate_id=other_id,
                                values={"FLOW_SELLER_NAME": CAR_TWO_SELLER},
                                stamp=datetime.now(timezone.utc))
            db.commit()
        details, html = V.card(self.engine, w.lead_id)
        block, _ = split_block(html)
        self.assertEqual(details.vehicle, "Fiat Uno (Auto)")
        self.assertIn(CAR_TWO_SELLER, block)
        for value in CAR_ONE_VALUES:
            self.assertNotIn(value, block, value)


class T1_ForeignThreadFocus(_Booked):
    """A focus id that points at another thread's candidate supplies nothing from that thread."""

    def test_t1_09_foreign_focus_supplies_no_vehicle_or_car_detail(self):
        owner = H.seed(self.engine)
        V.prepare(self.engine, owner, typed=CAR_ONE)
        victim = H.seed(self.engine, at=time(11, 30))
        self.set_focus(victim, V.bound_candidate(self.engine, owner))
        details, html = V.card(self.engine, victim.lead_id)
        self.assertIsNone(details.vehicle)
        self.assertIsNone(details.locality)
        self.assertEqual(set(scoped(details).values()), {None})
        for value in CAR_ONE_VALUES:
            self.assertNotIn(value, html, value)

    def test_t1_10_foreign_booked_focus_supplies_nothing_either(self):
        owner = self.book()
        victim = H.seed(self.engine, at=time(11, 30))
        self.set_focus(victim, V.bound_candidate(self.engine, owner))
        details, html = V.card(self.engine, victim.lead_id)
        self.assertIsNone(details.vehicle)
        self.assertEqual(set(scoped(details).values()), {None})
        for value in CAR_ONE_VALUES:
            self.assertNotIn(value, html, value)


if __name__ == "__main__":
    unittest.main()
