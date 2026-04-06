from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import ThreadRevision
from app.schemas.schedule import ScheduleCheckIn
from app.schemas.thread_revisions import ThreadRevisionCreateIn, ThreadRevisionPatch
from app.services.pricing import PricingService
from app.services.schedule import ScheduleService
from app.services.thread_revisions import ThreadRevisionService


@dataclass
class FakePriceRow:
    tipo_vehiculo: str
    precio_base: int


@dataclass
class FakeZone:
    zone_group: str
    zone_detail: str
    viaticos: int


class FakePricingRepository:
    def find_base_price(self, tipo_vehiculo: str):
        if tipo_vehiculo == "AUTO":
            return FakePriceRow(tipo_vehiculo="AUTO", precio_base=130000)
        return None

    def find_zone_by_group_and_detail(self, db, zone_group: str | None, zone_detail: str | None):
        if (zone_detail or "").strip().lower() == "palermo":
            return FakeZone(zone_group="CABA", zone_detail="Palermo", viaticos=15000)
        return None


class FakeThreadRevisionRepository:
    def __init__(self):
        self.db = self
        self.thread = type("Thread", (), {"id": 12})()
        self.candidate = type("Candidate", (), {"id": 34, "thread_id": 12})()
        self.revision = ThreadRevision(
            id=7,
            thread_id=12,
            candidate_id=34,
            status="collecting_data",
            buyer_name="Lara",
        )

    def rollback(self):
        return None

    def get_thread(self, thread_id: int):
        return self.thread if thread_id == 12 else None

    def get_candidate(self, candidate_id: int):
        return self.candidate if candidate_id == 34 else None

    def get_revision(self, revision_id: int):
        return self.revision if revision_id == 7 else None

    def add_revision(self, revision: ThreadRevision):
        revision.id = 99
        self.revision = revision
        return revision

    def save_revision(self, revision: ThreadRevision):
        self.revision = revision
        return revision


class BackendServiceTests(unittest.TestCase):
    def test_pricing_service_normalizes_zone_detail_and_maps_group(self):
        service = PricingService(repository=FakePricingRepository())

        quote = service.quote(
            db=None,
            tipo_vehiculo="auto",
            zone_group="caba",
            zone_detail="  PALERMO  ",
        )

        self.assertEqual(quote.tipo_vehiculo, "AUTO")
        self.assertEqual(quote.zone_group, "CABA")
        self.assertEqual(quote.zone_detail, "Palermo")
        self.assertEqual(quote.precio_base, 130000)
        self.assertEqual(quote.viaticos, 15000)
        self.assertEqual(quote.precio_total, 145000)

    def test_thread_revision_service_create_sets_collecting_data(self):
        repository = FakeThreadRevisionRepository()
        service = ThreadRevisionService(repository=repository)

        revision = service.create_revision(ThreadRevisionCreateIn(thread_id=12, candidate_id=34))

        self.assertEqual(revision.id, 99)
        self.assertEqual(revision.status, "collecting_data")
        self.assertEqual(revision.thread_id, 12)
        self.assertEqual(revision.candidate_id, 34)

    def test_thread_revision_service_patch_ignores_null_fields(self):
        repository = FakeThreadRevisionRepository()
        service = ThreadRevisionService(repository=repository)

        revision = service.patch_revision(
            revision_id=7,
            payload=ThreadRevisionPatch(status="booked", buyer_name=None, seller_name="Agencia Norte"),
        )

        self.assertEqual(revision.status, "booked")
        self.assertEqual(revision.buyer_name, "Lara")
        self.assertEqual(revision.seller_name, "Agencia Norte")

    def test_schedule_service_placeholder(self):
        service = ScheduleService()

        result = service.check(
            ScheduleCheckIn(
                address="Av. Santa Fe 1234",
                preferred_day="2026-04-08",
                preferred_time="10:00",
            )
        )

        self.assertTrue(result.valid)
        self.assertEqual(result.suggested_slots, [])


if __name__ == "__main__":
    unittest.main()
