from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api.pricing import get_pricing_service, router as pricing_router
from app.api.schedule import get_schedule_service, router as schedule_router
from app.api.thread_revisions import get_thread_revision_service, router as thread_revisions_router
from app.db import get_db


@dataclass
class FakeQuote:
    tipo_vehiculo: str
    zone_group: str
    zone_detail: str
    precio_base: int
    viaticos: int

    @property
    def precio_total(self) -> int:
        return self.precio_base + self.viaticos


class FakePricingService:
    def quote(self, db, tipo_vehiculo: str, zone_group: str, zone_detail: str):
        return FakeQuote(
            tipo_vehiculo="AUTO",
            zone_group="CABA",
            zone_detail="Palermo",
            precio_base=130000,
            viaticos=15000,
        )


class FakeScheduleService:
    def check(self, payload):
        return {
            "valid": True,
            "suggested_slots": ["2026-04-08T10:00"],
            "approval_tag": "Esperando aprobación",
            "requested_slot": {
                "start": "2026-04-08T10:00",
                "end": "2026-04-08T11:00",
            },
            "business_hours": "09:00-18:00",
            "service_minutes": 45,
            "buffer_minutes": 15,
            "travel_minutes": 0,
            "total_slot_minutes": 60,
            "conflicts": [],
            "reasons": [],
            "rules_applied": ["Duracion fija de revision: 45 minutos"],
        }


class FakeThreadRevision:
    def __init__(self):
        self.id = 77
        self.thread_id = 12
        self.candidate_id = 34
        self.status = "booked"
        self.buyer_name = "Lara"
        self.buyer_phone = None
        self.buyer_email = None
        self.seller_type = None
        self.seller_name = None
        self.address = None
        self.scheduled_date = None
        self.scheduled_time = None
        self.tipo_vehiculo = None
        self.marca = None
        self.modelo = None
        self.anio = None
        self.publication_url = None
        self.created_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)


class FakeThreadRevisionService:
    def create_revision(self, payload):
        rev = FakeThreadRevision()
        rev.status = "collecting_data"
        return rev

    def patch_revision(self, revision_id: int, payload):
        return FakeThreadRevision()


class NewApiEndpointTests(unittest.TestCase):
    def test_pricing_quote_endpoint(self):
        app = FastAPI()
        app.include_router(pricing_router)
        app.dependency_overrides[get_db] = lambda: object()
        app.dependency_overrides[get_pricing_service] = lambda: FakePricingService()

        with TestClient(app) as client:
            response = client.post(
                "/api/pricing/quote",
                json={"tipo_vehiculo": "auto", "zone_group": "caba", "zone_detail": "palermo"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["precio_total"], 145000)

    def test_schedule_check_endpoint(self):
        app = FastAPI()
        app.include_router(schedule_router)
        app.dependency_overrides[get_schedule_service] = lambda: FakeScheduleService()

        with TestClient(app) as client:
            response = client.post(
                "/api/schedule/check",
                json={"address": "Av. Santa Fe 1234", "preferred_day": "2026-04-08", "preferred_time": "10:00"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["valid"], True)
        self.assertEqual(response.json()["approval_tag"], "Esperando aprobación")
        self.assertEqual(response.json()["requested_slot"]["start"], "2026-04-08T10:00")

    def test_thread_revision_endpoints(self):
        app = FastAPI()
        app.include_router(thread_revisions_router)
        app.dependency_overrides[get_thread_revision_service] = lambda: FakeThreadRevisionService()

        with TestClient(app) as client:
            create_response = client.post("/api/revisions", json={"thread_id": 12, "candidate_id": 34})
            patch_response = client.patch("/api/revisions/77", json={"status": "booked", "buyer_name": None})

        self.assertEqual(create_response.status_code, 200)
        self.assertEqual(create_response.json(), {"revision_id": 77})
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()["status"], "booked")


if __name__ == "__main__":
    unittest.main()
