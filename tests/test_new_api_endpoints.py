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

from app.api.leads import router as leads_router
from app.api.pricing import get_pricing_service, router as pricing_router
from app.api.routes.public_approval import router as public_approval_router
from app.api.revision_items import api_router as revision_items_api_router
from app.api.schedule import get_schedule_service, router as schedule_router
from app.api.thread_revisions import get_thread_revision_service, router as thread_revisions_router
from app.db import get_db
from app.models import Lead


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

    def list_slots(self, payload):
        return {
            "preferred_day": "2026-04-08",
            "business_hours": "09:00-18:00",
            "slots": ["2026-04-08T10:00", "2026-04-08T11:30"],
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
        self.appointment_approval_status = None
        self.appointment_approval_token = "approval-token"
        self.appointment_approval_sent_at = None
        self.appointment_approved_at = None
        self.created_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)


class FakeThreadRevisionService:
    def create_revision(self, payload):
        rev = FakeThreadRevision()
        rev.status = "collecting_data"
        return rev

    def patch_revision(self, revision_id: int, payload):
        return FakeThreadRevision()


class FakePublicDb:
    def __init__(self, revision=None):
        self.revision = revision
        self.committed = False

    def get(self, model, revision_id: int):
        if self.revision and revision_id == self.revision.id:
            return self.revision
        return None

    def add(self, revision):
        self.revision = revision

    def commit(self):
        self.committed = True


class FakeLegacyRevision:
    def __init__(self):
        self.id = 55
        self.lead_id = 9
        self.created_at = datetime.now(timezone.utc)
        self.tipo_vehiculo = None
        self.marca = None
        self.modelo = None
        self.anio = None
        self.link_compra = None
        self.presupuesto_compra = None
        self.vendedor_tipo = None
        self.tipo_vendedor = None
        self.agencia_id = None
        self.compro = None
        self.resultado_link = None
        self.comision = None
        self.cobrado = None
        self.fecha_cobro = None
        self.zone_group = None
        self.zone_detail = None
        self.direccion_texto = None
        self.link_maps = None
        self.direccion_estado = None
        self.precio_base = None
        self.viaticos = None
        self.precio_total = None
        self.pago = None
        self.medio_pago = None
        self.turno_fecha = None
        self.turno_hora = None
        self.cliente_presente = None
        self.turno_notas = None
        self.estado_revision = "PENDIENTE"
        self.resultado = None
        self.motivo_rechazo = None
        self.appointment_approval_status = "PENDING"
        self.appointment_approval_token = "legacy-token"
        self.appointment_approval_sent_at = None
        self.appointment_approved_at = None


class FakeLegacyRevisionDb:
    def __init__(self, revision=None):
        self.revision = revision
        self.committed = False
        self.refreshed = False

    def get(self, model, revision_id: int):
        if self.revision and revision_id == self.revision.id:
            return self.revision
        return None

    def commit(self):
        self.committed = True

    def rollback(self):
        return None

    def refresh(self, revision):
        self.refreshed = True


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

    def test_schedule_slots_endpoint(self):
        app = FastAPI()
        app.include_router(schedule_router)
        app.dependency_overrides[get_schedule_service] = lambda: FakeScheduleService()

        with TestClient(app) as client:
            response = client.get(
                "/api/schedule/slots",
                params={"preferred_day": "2026-04-08", "address": "Av. Santa Fe 1234"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["business_hours"], "09:00-18:00")
        self.assertEqual(response.json()["slots"][0], "2026-04-08T10:00")

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

    def test_public_approve_endpoint_updates_revision(self):
        app = FastAPI()
        app.include_router(public_approval_router)
        revision = FakeThreadRevision()
        db = FakePublicDb(revision=revision)
        app.dependency_overrides[get_db] = lambda: db

        with TestClient(app) as client:
            response = client.post("/public/revisions/77/approve", params={"token": "approval-token"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("Turno confirmado", response.text)
        self.assertEqual(revision.appointment_approval_status, "APPROVED")
        self.assertIsNotNone(revision.appointment_approved_at)
        self.assertTrue(db.committed)

    def test_public_reject_endpoint_validates_token(self):
        app = FastAPI()
        app.include_router(public_approval_router)
        revision = FakeThreadRevision()
        db = FakePublicDb(revision=revision)
        app.dependency_overrides[get_db] = lambda: db

        with TestClient(app) as client:
            response = client.post("/public/revisions/77/reject", params={"token": "wrong-token"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(revision.appointment_approval_status, None)
        self.assertFalse(db.committed)

    def test_revision_appointment_approval_endpoint_approves_without_touching_token(self):
        app = FastAPI()
        app.include_router(revision_items_api_router)
        revision = FakeLegacyRevision()
        db = FakeLegacyRevisionDb(revision=revision)
        app.dependency_overrides[get_db] = lambda: db

        with TestClient(app) as client:
            response = client.patch("/api/revisions/55/appointment-approval", json={"status": "APPROVED"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["appointment_approval_status"], "APPROVED")
        self.assertEqual(response.json()["appointment_approval_token"], "legacy-token")
        self.assertIsNotNone(response.json()["appointment_approved_at"])
        self.assertTrue(db.committed)

    def test_revision_appointment_approval_endpoint_returns_404_for_missing_revision(self):
        app = FastAPI()
        app.include_router(revision_items_api_router)
        db = FakeLegacyRevisionDb(revision=None)
        app.dependency_overrides[get_db] = lambda: db

        with TestClient(app) as client:
            response = client.patch("/api/revisions/999/appointment-approval", json={"status": "REJECTED"})

        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# M6 — Lead email / name persistence regression tests
# ---------------------------------------------------------------------------

class FakeLead:
    def __init__(self, lead_id=1, email=None, nombre=None, apellido=None):
        self.id = lead_id
        self.estado = "CONSULTA_NUEVA"
        self.flag = None
        self.necesita_humano = False
        self.motivo_perdida = None
        self.telefono = "5491155550000"
        self.nombre = nombre
        self.apellido = apellido
        self.email = email
        self.canal = None
        self.compro_el_auto = None
        self.buscando_auto_set_at = None
        self.feedback = None
        self.created_at = datetime.now(timezone.utc)


class FakeLeadDb:
    def __init__(self, lead: FakeLead):
        self._lead = lead
        self.committed = False

    def get(self, model, pk):
        if model is Lead and pk == self._lead.id:
            return self._lead
        return None

    def add(self, obj):
        pass

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def refresh(self, obj):
        pass


class LeadPatchM6Tests(unittest.TestCase):
    def _make_app(self, lead: FakeLead):
        app = FastAPI()
        app.include_router(leads_router)
        db = FakeLeadDb(lead)
        app.dependency_overrides[get_db] = lambda: db
        return app, db

    # TEST A — email is patched from full booking form
    def test_lead_patch_email_from_booking_form(self):
        lead = FakeLead(lead_id=10, email=None)
        app, db = self._make_app(lead)

        with TestClient(app) as client:
            response = client.patch("/leads/10", json={"email": "agomelsky@gmail.com"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(lead.email, "agomelsky@gmail.com")
        self.assertTrue(db.committed)

    # TEST B — nombre and apellido are patched from full booking form
    def test_lead_patch_nombre_apellido_from_booking_form(self):
        lead = FakeLead(lead_id=11, nombre=None, apellido=None)
        app, db = self._make_app(lead)

        with TestClient(app) as client:
            response = client.patch("/leads/11", json={"nombre": "Alejandro", "apellido": "Gomelsky"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(lead.nombre, "Alejandro")
        self.assertEqual(lead.apellido, "Gomelsky")

    # TEST C — full booking patch (nombre + apellido + email together)
    def test_lead_patch_full_contact_fields(self):
        lead = FakeLead(lead_id=12, nombre=None, apellido=None, email=None)
        app, db = self._make_app(lead)

        with TestClient(app) as client:
            response = client.patch(
                "/leads/12",
                json={"nombre": "Alejandro", "apellido": "Gomelsky", "email": "agomelsky@gmail.com"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(lead.nombre, "Alejandro")
        self.assertEqual(lead.apellido, "Gomelsky")
        self.assertEqual(lead.email, "agomelsky@gmail.com")

    # TEST D — do NOT overwrite existing email with null
    def test_lead_patch_null_email_does_not_overwrite_existing(self):
        lead = FakeLead(lead_id=13, email="existing@example.com")
        app, db = self._make_app(lead)

        with TestClient(app) as client:
            # Sending null email — backend must leave existing value intact
            response = client.patch("/leads/13", json={"nombre": "Alguien"})

        self.assertEqual(response.status_code, 200)
        # email was not in the payload, so it must remain unchanged
        self.assertEqual(lead.email, "existing@example.com")

    # TEST E — lead 404 still works (existing behavior preserved)
    def test_lead_patch_returns_404_for_missing_lead(self):
        lead = FakeLead(lead_id=99)
        app, db = self._make_app(lead)

        with TestClient(app) as client:
            response = client.patch("/leads/999", json={"email": "x@y.com"})

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
