from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api.whatsapp import router
from app.db import get_db
from app.models import WhatsAppContact, WhatsAppThread, WhatsAppThreadState


class FakeSession:
    def __init__(self, thread: WhatsAppThread):
        self.thread = thread

    def get(self, model, key):
        if model is WhatsAppThread and key == self.thread.id:
            return self.thread
        return None

    def add(self, obj):
        if isinstance(obj, WhatsAppThreadState):
            self.thread.state = obj

    def commit(self):
        return None

    def rollback(self):
        return None

    def refresh(self, obj):
        return None


class WhatsAppThreadStateApiTests(unittest.TestCase):
    def setUp(self):
        self.thread = WhatsAppThread(id=12, contact_id=7)
        self.thread.contact = WhatsAppContact(id=7, wa_id="5491100000000")
        self.db = FakeSession(thread=self.thread)

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    def test_get_state_returns_new_field_when_state_does_not_exist(self):
        response = self.client.get("/api/whatsapp/thread/12/state")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "id": None,
                "thread_id": 12,
                "last_intent": None,
                "last_stage": None,
                "needs_human": False,
                "current_focus_candidate_id": None,
                "current_revision_id": None,
                "last_processed_inbound_wa_message_id": None,
                "customer_name": None,
                "home_zone_group": None,
                "home_zone_detail": None,
                "created_at": None,
                "updated_at": None,
            },
        )

    def test_patch_state_updates_last_processed_inbound_message_id(self):
        response = self.client.patch(
            "/api/whatsapp/thread/12/state",
            json={"last_processed_inbound_wa_message_id": "wamid.HBgABC123"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["last_processed_inbound_wa_message_id"], "wamid.HBgABC123")
        self.assertIsNotNone(self.thread.state)
        self.assertEqual(self.thread.state.last_processed_inbound_wa_message_id, "wamid.HBgABC123")

    def test_patch_state_updates_current_revision_id(self):
        response = self.client.patch(
            "/api/whatsapp/thread/12/state",
            json={"current_revision_id": 55},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["current_revision_id"], 55)
        self.assertIsNotNone(self.thread.state)
        self.assertEqual(self.thread.state.current_revision_id, 55)

    def test_patch_state_omitting_new_field_preserves_existing_value(self):
        self.thread.state = WhatsAppThreadState(
            thread_id=12,
            current_revision_id=44,
            last_processed_inbound_wa_message_id="wamid.HBgKEEP",
            customer_name="Lara",
        )

        response = self.client.patch(
            "/api/whatsapp/thread/12/state",
            json={"customer_name": "Lara Ridecheck"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["customer_name"], "Lara Ridecheck")
        self.assertEqual(response.json()["current_revision_id"], 44)
        self.assertEqual(response.json()["last_processed_inbound_wa_message_id"], "wamid.HBgKEEP")
        self.assertEqual(self.thread.state.current_revision_id, 44)
        self.assertEqual(self.thread.state.last_processed_inbound_wa_message_id, "wamid.HBgKEEP")

    def test_patch_state_validates_last_processed_inbound_message_id_length(self):
        response = self.client.patch(
            "/api/whatsapp/thread/12/state",
            json={"last_processed_inbound_wa_message_id": "x" * 192},
        )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
