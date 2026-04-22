"""
Phase 3 – Image Message Tests

Since real WhatsApp webhooks cannot be triggered locally, these tests submit
payloads in the exact format Meta sends for image messages directly to the
route handler.  Media download (which requires Meta's CDN) is out of scope;
we test that the webhook layer stores the message correctly, captures the
caption as text, fires the AI event, and handles edge cases.

Edge cases covered:
  - Image with caption → caption stored as text
  - Image without caption → text is None
  - Missing media_id (malformed) → message not stored
  - Oversized / unsupported format: the webhook doesn't validate MIME or size
    (that's Meta's responsibility before sending); we verify the handler is
    resilient to any mime_type string.
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
    whatsapp_app_secret="",
    n8n_webhook_url="",
    openai_api_key="",
)

TS = "1776710118"


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------

def _image_payload(
    wa_id: str,
    msg_id: str,
    media_id: str,
    caption: str | None = None,
    name: str = "",
    mime_type: str = "image/jpeg",
) -> dict:
    """Meta image webhook payload format."""
    img_block: dict = {
        "id": media_id,
        "mime_type": mime_type,
        "sha256": "abc123fakehash",
    }
    if caption is not None:
        img_block["caption"] = caption

    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA_TEST", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "15551234568", "phone_number_id": "123"},
            "contacts": [{"profile": {"name": name}, "wa_id": wa_id}],
            "messages": [{
                "from": wa_id,
                "id": msg_id,
                "timestamp": TS,
                "type": "image",
                "image": img_block,
            }],
        }}]}],
    }


def _image_payload_no_media_id(wa_id: str, msg_id: str) -> dict:
    """Malformed payload: image block present but id is empty string."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA_TEST", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "15551234568", "phone_number_id": "123"},
            "contacts": [{"profile": {"name": ""}, "wa_id": wa_id}],
            "messages": [{
                "from": wa_id,
                "id": msg_id,
                "timestamp": TS,
                "type": "image",
                "image": {"id": "", "mime_type": "image/jpeg"},
            }],
        }}]}],
    }


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
# FakeDB (identical to Phase 1 & 2)
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
        self._contacts: dict = {}
        self._threads: dict = {}
        self._messages: dict = {}
        self._ai_events: dict = {}
        self._id = 0

    def _next_id(self) -> int:
        self._id += 1
        return self._id

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
        froms = list(stmt.froms)
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

    @property
    def all_messages(self) -> list:
        return list(self._messages.values())

    @property
    def all_threads(self) -> list:
        return list(self._threads.values())


def _make_client(db: FakeDB) -> TestClient:
    app = FastAPI()
    app.include_router(webhook_router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


# ===========================================================================
# Phase 3  –  Image message tests
# ===========================================================================

class ImageWebhookTests(unittest.TestCase):
    """
    Tests that the webhook handler correctly ingests Meta-format image payloads.
    Field updates to lead/revision records are triggered by N8N after the AiEvent
    is received; the webhook layer's job is to store and route the image correctly.
    """

    def setUp(self):
        self.db = FakeDB()
        patcher = patch("app.routes.whatsapp.get_settings", return_value=TEST_SETTINGS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = _make_client(self.db)

    def tearDown(self):
        self.client.close()

    def test_image_message_stored_with_correct_type_and_media_id(self):
        """message_type must be 'image' and media_id must match the payload."""
        r = self.client.post(
            "/integrations/whatsapp/webhook",
            json=_image_payload("5491100000010", "wamid.img001", "MEDIA_ID_IMG_001"),
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.db.all_messages), 1)

        msg = self.db.all_messages[0]
        self.assertEqual(msg.message_type, "image")
        self.assertEqual(msg.media_id, "MEDIA_ID_IMG_001")
        self.assertEqual(msg.direction, "in")
        self.assertEqual(msg.status, "received")

    def test_image_with_caption_stores_caption_as_text(self):
        """
        When a client sends an image with a caption (e.g. "Fotos del Honda Civic"),
        the caption must be stored in the text field so N8N can read it.
        """
        caption = "Fotos del auto - Honda Civic 2019"
        r = self.client.post(
            "/integrations/whatsapp/webhook",
            json=_image_payload("5491100000011", "wamid.img002", "MEDIA_ID_IMG_002",
                                caption=caption),
        )
        self.assertEqual(r.status_code, 200)
        msg = self.db.all_messages[0]
        self.assertEqual(msg.text, caption)

    def test_image_without_caption_has_null_text(self):
        """Image with no caption → text must be None (not empty string)."""
        self.client.post(
            "/integrations/whatsapp/webhook",
            json=_image_payload("5491100000012", "wamid.img003", "MEDIA_ID_IMG_003",
                                caption=None),
        )
        msg = self.db.all_messages[0]
        self.assertIsNone(msg.text)

    def test_image_creates_ai_event_with_pending_status(self):
        """
        AiEvent must be created so N8N can process the image and update
        the lead/revision record (e.g. vehicle photo uploaded by the client).
        """
        self.client.post(
            "/integrations/whatsapp/webhook",
            json=_image_payload("5491100000013", "wamid.img004", "MEDIA_ID_IMG_004",
                                name="Cliente Leo"),
        )
        self.assertEqual(len(self.db._ai_events), 1)
        event = list(self.db._ai_events.values())[0]
        self.assertEqual(event.status, "pending")
        self.assertEqual(event.wa_message_id, "wamid.img004")

    def test_image_missing_media_id_not_stored(self):
        """
        Edge case: malformed payload with empty image.id.
        Nothing must be stored — no message, no thread, no AI event.
        """
        r = self.client.post(
            "/integrations/whatsapp/webhook",
            json=_image_payload_no_media_id("5491100000014", "wamid.img_bad"),
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.db.all_messages), 0)
        self.assertEqual(len(self.db._ai_events), 0)

    def test_image_deduplication_same_message_id_stored_once(self):
        """Retried delivery must not create a second row."""
        payload = _image_payload("5491100000015", "wamid.img_dup", "MEDIA_ID_DUP")
        self.client.post("/integrations/whatsapp/webhook", json=payload)
        self.client.post("/integrations/whatsapp/webhook", json=payload)

        self.assertEqual(len(self.db.all_messages), 1)
        self.assertEqual(self.db.all_threads[0].unread_count, 1)

    def test_image_increments_unread_count(self):
        """Image messages must increment unread_count just like text messages."""
        self.client.post("/integrations/whatsapp/webhook",
                         json=_image_payload("5491100000016", "wamid.img_unr", "MEDIA_ID_UNR"))
        self.assertEqual(self.db.all_threads[0].unread_count, 1)

    def test_image_after_text_messages_belongs_to_same_thread(self):
        """
        Typical flow: client sends text inquiry, then sends a photo of the vehicle.
        Both messages must be in the same thread; unread_count reflects both.
        """
        wa_id = "5491152478834"
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload(wa_id, "wamid.mix_t1",
                                            "Hola quiero que revisen un vehículo"))
        self.client.post("/integrations/whatsapp/webhook",
                         json=_image_payload(wa_id, "wamid.mix_i1", "MEDIA_ID_VEH_PHOTO",
                                             caption="Foto del auto"))

        self.assertEqual(len(self.db.all_threads), 1)
        self.assertEqual(len(self.db.all_messages), 2)
        self.assertEqual(self.db.all_threads[0].unread_count, 2)

        thread_ids = {m.thread_id for m in self.db.all_messages}
        self.assertEqual(len(thread_ids), 1, "Both messages must share the same thread_id")

    def test_image_with_unsupported_mime_type_still_stored(self):
        """
        Edge case: Meta should only send valid image types, but if an unusual
        mime_type arrives the handler must not crash — it stores whatever comes.
        MIME validation is Meta's responsibility, not the webhook's.
        """
        r = self.client.post(
            "/integrations/whatsapp/webhook",
            json=_image_payload("5491100000017", "wamid.img_webp", "MEDIA_ID_WEBP",
                                mime_type="image/webp"),
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.db.all_messages), 1)
        self.assertEqual(self.db.all_messages[0].message_type, "image")


if __name__ == "__main__":
    unittest.main()
