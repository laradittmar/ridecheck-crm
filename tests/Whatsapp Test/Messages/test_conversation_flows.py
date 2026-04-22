"""
Phase 1 – Conversation Flow Tests

Simulates complete WhatsApp conversation flows by posting webhook payloads
directly into the route handler and driving state changes through the state API,
mirroring what N8N/AI would do in production.

Conversation types covered (based on screenshots in this folder):
  - Conversation_type_1  : new client, single vehicle inquiry
  - Conversation_type_4  : client mentions two vehicles (Hyundai + Ford Ranger)
  - Conversation_type_7  : full booking data collected, CONSULTA_NUEVA → AGENDADO
  - Conversation_type_9/10: two simultaneous clients, zone + vehicle info
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[3]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api.whatsapp import router as state_router
from app.db import get_db
from app.models import (
    AiEvent,
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppThread,
    WhatsAppThreadState,
)
from app.routes.whatsapp import router as webhook_router
from app.settings import Settings

TEST_SETTINGS = Settings(
    whatsapp_token="test_token",
    whatsapp_verify_token="test_verify",
    whatsapp_phone_number_id="test_phone_id",
    whatsapp_app_secret="",   # empty → signature check skipped (dev mode)
    n8n_webhook_url="",       # empty → N8N call skipped
    openai_api_key="",
)

TS = "1776710118"  # fixed Unix timestamp for all test messages


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------

def _text_payload(wa_id: str, msg_id: str, body: str, name: str = "") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA_TEST", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "15551234568", "phone_number_id": "123"},
            "contacts": [{"profile": {"name": name}, "wa_id": wa_id}],
            "messages": [{"from": wa_id, "id": msg_id, "timestamp": TS,
                          "type": "text", "text": {"body": body}}],
        }}]}],
    }


# ---------------------------------------------------------------------------
# FakeDB – in-memory store compatible with both webhook and state API handlers
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, value=None):
        self._v = value

    def scalar_one_or_none(self):
        return self._v

    def scalars(self):
        return self

    def first(self):
        return self._v


class FakeDB:
    def __init__(self):
        self._contacts: dict = {}   # wa_id  -> WhatsAppContact
        self._threads: dict = {}    # contact_id -> WhatsAppThread
        self._messages: dict = {}   # wa_message_id -> WhatsAppMessage
        self._ai_events: dict = {}  # wa_message_id -> AiEvent
        self._id = 0

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    # --- ORM operations -------------------------------------------------------

    def add(self, obj) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = self._next_id()
        if isinstance(obj, WhatsAppContact):
            self._contacts[obj.wa_id] = obj
        elif isinstance(obj, WhatsAppThread):
            self._threads[obj.contact_id] = obj
        elif isinstance(obj, WhatsAppMessage):
            if obj.wa_message_id:
                self._messages[obj.wa_message_id] = obj
        elif isinstance(obj, AiEvent):
            if obj.wa_message_id:
                self._ai_events[obj.wa_message_id] = obj
        elif isinstance(obj, WhatsAppThreadState):
            # Link state to thread in-memory so thread.state reads it back
            for t in self._threads.values():
                if t.id == obj.thread_id:
                    t.state = obj
                    break

    def get(self, model, key):
        if model is WhatsAppThread:
            for t in self._threads.values():
                if t.id == key:
                    return t
        return None

    def flush(self): pass
    def commit(self): pass
    def rollback(self): pass
    def refresh(self, obj): pass

    def execute(self, stmt):
        try:
            return self._dispatch(stmt)
        except Exception:
            return _FakeResult(None)

    def _dispatch(self, stmt):
        froms = list(stmt.get_final_froms())
        if not froms:
            return _FakeResult(None)
        table = froms[0].name

        cond: dict = {}
        w = stmt.whereclause
        if w is not None and hasattr(w, "left") and hasattr(w, "right"):
            col_key = getattr(w.left, "key", None)
            val = getattr(w.right, "value", None)
            if col_key is not None:
                cond[col_key] = val

        if table == "whatsapp_messages":
            msg = self._messages.get(cond.get("wa_message_id")) if "wa_message_id" in cond else None
            try:
                exported = [c.key for c in stmt.exported_columns if hasattr(c, "key")]
                if exported == ["id"]:
                    return _FakeResult(msg.id if msg else None)
            except Exception:
                pass
            return _FakeResult(msg)

        if table == "whatsapp_contacts":
            return _FakeResult(self._contacts.get(cond.get("wa_id")))

        if table == "whatsapp_threads":
            return _FakeResult(self._threads.get(cond.get("contact_id")))

        if table == "ai_events":
            return _FakeResult(self._ai_events.get(cond.get("wa_message_id")))

        return _FakeResult(None)

    # --- Convenience accessors for assertions ---------------------------------

    @property
    def all_threads(self) -> list:
        return list(self._threads.values())

    @property
    def all_messages(self) -> list:
        return list(self._messages.values())


# ---------------------------------------------------------------------------
# Helper: build a TestClient with both routers and settings mocked
# ---------------------------------------------------------------------------

def _make_client(db: FakeDB) -> TestClient:
    app = FastAPI()
    app.include_router(webhook_router)
    app.include_router(state_router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


# ===========================================================================
# Phase 1-A  –  Webhook message ingestion & storage
# ===========================================================================

class WebhookTextIngestTests(unittest.TestCase):
    """
    Verifies that the webhook handler correctly persists contacts, threads,
    messages and AiEvents.  No N8N call is made (n8n_webhook_url is empty).
    """

    def setUp(self):
        self.db = FakeDB()
        patcher = patch("app.routes.whatsapp.get_settings", return_value=TEST_SETTINGS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = _make_client(self.db)

    def tearDown(self):
        self.client.close()

    def test_first_message_creates_contact_thread_and_message(self):
        """Conversation_type_1: brand-new client sends initial inquiry."""
        payload = _text_payload("5491131776798", "wamid.msg001",
                                "Hola! Quiero más información.", name="kavita")
        r = self.client.post("/integrations/whatsapp/webhook", json=payload)

        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.db._contacts), 1)
        self.assertEqual(len(self.db.all_threads), 1)
        self.assertEqual(len(self.db.all_messages), 1)

        msg = self.db.all_messages[0]
        self.assertEqual(msg.direction, "in")
        self.assertEqual(msg.message_type, "text")
        self.assertEqual(msg.status, "received")
        self.assertEqual(msg.text, "Hola! Quiero más información.")

    def test_ai_event_created_with_pending_status(self):
        payload = _text_payload("5491131776798", "wamid.msg002",
                                "Cuánto cuesta revisar un Ecosport 2011?")
        self.client.post("/integrations/whatsapp/webhook", json=payload)

        self.assertEqual(len(self.db._ai_events), 1)
        event = list(self.db._ai_events.values())[0]
        self.assertEqual(event.status, "pending")
        self.assertEqual(event.wa_message_id, "wamid.msg002")

    def test_duplicate_message_is_stored_only_once(self):
        """Sending the same wa_message_id twice must not create a second row."""
        payload = _text_payload("5491131776798", "wamid.dup001", "si")
        self.client.post("/integrations/whatsapp/webhook", json=payload)
        self.client.post("/integrations/whatsapp/webhook", json=payload)

        self.assertEqual(len(self.db.all_messages), 1)
        self.assertEqual(len(self.db.all_threads), 1)
        self.assertEqual(self.db.all_threads[0].unread_count, 1)

    def test_sequential_messages_increment_unread_count(self):
        """Conversation_type_7: multiple messages from client mid-booking flow."""
        for i, body in enumerate([
            "Necesito que revisen un auto",
            "Es un Ford Escort 1997",
            "La dirección es Amenábar 3230",
        ], start=1):
            r = self.client.post("/integrations/whatsapp/webhook",
                                 json=_text_payload("5491161460064", f"wamid.seq{i:03}", body))
            self.assertEqual(r.status_code, 200)

        thread = self.db.all_threads[0]
        self.assertEqual(thread.unread_count, 3)
        self.assertEqual(len(self.db.all_messages), 3)

    def test_second_message_reuses_existing_thread(self):
        """Client sends two messages; both end up in the same thread."""
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload("5491161460064", "wamid.a1", "Hola"))
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload("5491161460064", "wamid.a2", "Cuanto sale?"))

        self.assertEqual(len(self.db.all_threads), 1)
        thread_ids = {m.thread_id for m in self.db.all_messages
                      if hasattr(m, "thread_id")}
        self.assertEqual(len(thread_ids), 1)

    def test_second_message_reuses_existing_thread_v2(self):
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload("5491161460064", "wamid.b1", "Hola"))
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload("5491161460064", "wamid.b2", "Cuanto sale?"))

        msgs = self.db.all_messages
        self.assertEqual(msgs[0].thread_id, msgs[1].thread_id)


# ===========================================================================
# Phase 1-B  –  Multi-client thread isolation
# ===========================================================================

class ThreadIsolationTests(unittest.TestCase):
    """
    Two simultaneous clients (Conversation_type_9 + Conversation_type_10 style).
    Their threads must remain completely independent.
    """

    def setUp(self):
        self.db = FakeDB()
        patcher = patch("app.routes.whatsapp.get_settings", return_value=TEST_SETTINGS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = _make_client(self.db)

    def tearDown(self):
        self.client.close()

    def test_two_clients_get_separate_threads(self):
        """Conversation_type_9 (Karina) + Conversation_type_10 (Julian) don't interfere."""
        karina_id = "5491166067298"
        julian_id = "5491145284129"

        for seq, (wa, name, body) in enumerate([
            (karina_id, "Karina", "Hola quiero que revisen un vehículo"),
            (julian_id, "Julian", "Te comento, nos dedicamos a revisar el auto de segunda mano"),
            (karina_id, "Karina", "Me pasas info?"),
            (julian_id, "Julian", "Ford Focus SE Plus manual 2017"),
            (karina_id, "Karina", "Zona de facultad de agronomía"),
        ], start=1):
            self.client.post(
                "/integrations/whatsapp/webhook",
                json=_text_payload(wa, f"wamid.iso{seq:03}", body, name=name),
            )

        self.assertEqual(len(self.db.all_threads), 2)
        self.assertEqual(len(self.db._contacts), 2)

        karina_contact = self.db._contacts[karina_id]
        julian_contact = self.db._contacts[julian_id]
        karina_thread = self.db._threads[karina_contact.id]
        julian_thread = self.db._threads[julian_contact.id]

        self.assertEqual(karina_thread.unread_count, 3)
        self.assertEqual(julian_thread.unread_count, 2)
        self.assertNotEqual(karina_thread.id, julian_thread.id)

    def test_messages_are_stored_in_correct_thread(self):
        """Each message's thread_id matches only its sender's thread."""
        wa_a, wa_b = "5491111111111", "5491122222222"
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload(wa_a, "wamid.c1", "Mensaje A1"))
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload(wa_b, "wamid.c2", "Mensaje B1"))
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload(wa_a, "wamid.c3", "Mensaje A2"))

        thread_a = self.db._threads[self.db._contacts[wa_a].id]
        thread_b = self.db._threads[self.db._contacts[wa_b].id]

        msgs_a = [m for m in self.db.all_messages if m.thread_id == thread_a.id]
        msgs_b = [m for m in self.db.all_messages if m.thread_id == thread_b.id]

        self.assertEqual(len(msgs_a), 2)
        self.assertEqual(len(msgs_b), 1)
        self.assertEqual(msgs_b[0].text, "Mensaje B1")


# ===========================================================================
# Phase 1-C  –  Full flow: CONSULTA_NUEVA → AGENDADO (webhook + state API)
# ===========================================================================

class FullFlowStateTests(unittest.TestCase):
    """
    Simulates the complete CONSULTA_NUEVA → AGENDADO flow.
    Webhook calls represent client messages; PATCH calls represent
    N8N/AI updating conversation state after each turn.

    Based on Conversation_type_7 (Eduardo Barrios / Ford Escort) and
    Conversation_type_10 (Ford Focus SE Plus).
    """

    def setUp(self):
        self.db = FakeDB()
        patcher = patch("app.routes.whatsapp.get_settings", return_value=TEST_SETTINGS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = _make_client(self.db)

    def tearDown(self):
        self.client.close()

    def _thread_id(self) -> int:
        return self.db.all_threads[0].id

    def test_full_consulta_to_agendado_flow(self):
        """
        Step-by-step flow:
          1. Client sends initial inquiry → thread created
          2. N8N updates state: collecting info, customer name extracted
          3. Client provides vehicle + zone details
          4. N8N updates state: zone captured, candidate set, stage = quoting
          5. Client confirms → N8N sets stage = booked, revision_id linked
          6. GET state → verify all fields reflect the completed booking
        """
        wa_id = "5491161460064"

        # Step 1: Initial inquiry
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload(wa_id, "wamid.flow01",
                                            "Hola, quiero revisar un Ford Escort 1997"))
        tid = self._thread_id()

        # Step 2: N8N sets initial state
        r = self.client.patch(f"/api/whatsapp/thread/{tid}/state", json={
            "last_intent": "inspection_inquiry",
            "last_stage": "collecting_info",
            "customer_name": "Eduardo Barrios",
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["customer_name"], "Eduardo Barrios")
        self.assertEqual(r.json()["last_stage"], "collecting_info")

        # Step 3: Client provides address + zone
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload(wa_id, "wamid.flow02",
                                            "Está en Amenábar 3230, 9E"))

        # Step 4: N8N updates zone + quoting stage
        r = self.client.patch(f"/api/whatsapp/thread/{tid}/state", json={
            "home_zone_group": "CABA",
            "home_zone_detail": "Colegiales",
            "last_stage": "quoting",
            "current_focus_candidate_id": 1,
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["home_zone_group"], "CABA")
        self.assertEqual(r.json()["current_focus_candidate_id"], 1)

        # Step 5: Client confirms booking
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload(wa_id, "wamid.flow03", "Perfecto, lo reservamos"))

        # N8N finalizes booking
        r = self.client.patch(f"/api/whatsapp/thread/{tid}/state", json={
            "last_stage": "booked",
            "current_revision_id": 42,
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["last_stage"], "booked")
        self.assertEqual(r.json()["current_revision_id"], 42)

        # Step 6: Verify final state preserves all accumulated data
        r = self.client.get(f"/api/whatsapp/thread/{tid}/state")
        self.assertEqual(r.status_code, 200)
        state = r.json()
        self.assertEqual(state["customer_name"], "Eduardo Barrios")
        self.assertEqual(state["home_zone_group"], "CABA")
        self.assertEqual(state["last_stage"], "booked")
        self.assertEqual(state["current_revision_id"], 42)
        self.assertEqual(state["current_focus_candidate_id"], 1)

        # Also verify 3 messages were stored
        self.assertEqual(len(self.db.all_messages), 3)

    def test_client_changes_mind_switches_candidate(self):
        """
        Conversation_type_4: client has Hyundai + Ford Ranger XLS.
        N8N first focuses on Hyundai (candidate 1), client then switches to
        Ford Ranger (candidate 2).  Final state must point to Ford Ranger.
        """
        wa_id = "5491161460064"
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload(wa_id, "wamid.cm01",
                                            "Tengo una Hyundai y una Ford Ranger XLS"))
        tid = self._thread_id()

        # Initially focused on Hyundai
        self.client.patch(f"/api/whatsapp/thread/{tid}/state",
                          json={"current_focus_candidate_id": 1, "last_stage": "quoting"})

        # Client switches to Ford Ranger
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload(wa_id, "wamid.cm02",
                                            "La Ford Ranger podría ser mañana mismo"))
        self.client.patch(f"/api/whatsapp/thread/{tid}/state",
                          json={"current_focus_candidate_id": 2})

        r = self.client.get(f"/api/whatsapp/thread/{tid}/state")
        self.assertEqual(r.json()["current_focus_candidate_id"], 2)
        self.assertEqual(r.json()["last_stage"], "quoting")  # unchanged

    def test_client_silent_then_returns_thread_continues(self):
        """
        Client sends a message, goes quiet, then comes back.
        All messages must belong to the same thread; state is preserved.
        """
        wa_id = "5491159537052"
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload(wa_id, "wamid.sr01", "Quiero revisar un vehículo"))
        tid = self._thread_id()

        # N8N sets initial state
        self.client.patch(f"/api/whatsapp/thread/{tid}/state",
                          json={"customer_name": "Diego Senén", "last_stage": "collecting_info"})

        # Client goes quiet (no messages) – no assertion needed for the silence itself

        # Client returns
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload(wa_id, "wamid.sr02", "Los tendré en cuenta sin algo útil"))
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload(wa_id, "wamid.sr03", "Igual me interesa seguir"))

        # Same thread, 3 messages total
        self.assertEqual(len(self.db.all_threads), 1)
        self.assertEqual(len(self.db.all_messages), 3)

        # State preserved
        r = self.client.get(f"/api/whatsapp/thread/{tid}/state")
        self.assertEqual(r.json()["customer_name"], "Diego Senén")
        self.assertEqual(r.json()["last_stage"], "collecting_info")

    def test_needs_human_flag_escalation(self):
        """N8N can set needs_human=True to escalate to a human agent."""
        wa_id = "5491129204834620"  # "Navarro Inmobiliaria" screenshot
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload(wa_id, "wamid.nh01",
                                            "Hola con cuánto tiempo de anticipo te aviso?"))
        tid = self._thread_id()

        r = self.client.patch(f"/api/whatsapp/thread/{tid}/state",
                              json={"needs_human": True})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["needs_human"])

        # Verify it persists on GET
        r = self.client.get(f"/api/whatsapp/thread/{tid}/state")
        self.assertTrue(r.json()["needs_human"])


if __name__ == "__main__":
    unittest.main()
