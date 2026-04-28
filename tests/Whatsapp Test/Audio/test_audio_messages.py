"""
Phase 2 – Audio Message Tests

Since real WhatsApp webhooks cannot be triggered locally, these tests submit
payloads in the exact format Meta sends for audio messages directly to the
route handler.  Media download / transcription (which requires Meta's CDN) is
out of scope here; we test that the *processing logic* stores the message
correctly and fires the AI event regardless of whether the audio can be fetched.

Test audio fixture: tests/Whatsapp Test/Audio/test_audio.ogg.ogg
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
# Payload helpers  (exact format Meta sends)
# ---------------------------------------------------------------------------

def _audio_payload(wa_id: str, msg_id: str, media_id: str, name: str = "") -> dict:
    """Meta audio webhook payload format."""
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
                "type": "audio",
                "audio": {
                    "id": media_id,
                    "mime_type": "audio/ogg; codecs=opus",
                    "sha256": "abc123fakehash",
                    "voice": True,
                },
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


def _audio_payload_no_media_id(wa_id: str, msg_id: str) -> dict:
    """Malformed payload: audio block present but id is empty."""
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
                "type": "audio",
                "audio": {"id": "", "mime_type": "audio/ogg; codecs=opus"},
            }],
        }}]}],
    }


# ---------------------------------------------------------------------------
# FakeDB (identical to Phase 1)
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

    def execute(self, stmt, params=None):
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
# Phase 2  –  Audio message tests
# ===========================================================================

class AudioWebhookTests(unittest.TestCase):
    """
    Tests that the webhook handler correctly ingests Meta-format audio payloads.
    Transcription is handled externally (N8N → OpenAI); these tests only cover
    what happens in the webhook layer: storage, deduplication, and AI event creation.
    """

    AUDIO_FIXTURE = ROOT_DIR / "tests" / "Whatsapp Test" / "Audio" / "test_audio.ogg.ogg"

    def setUp(self):
        self.db = FakeDB()
        patcher = patch("app.routes.whatsapp.get_settings", return_value=TEST_SETTINGS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = _make_client(self.db)

    def tearDown(self):
        self.client.close()

    def test_audio_fixture_file_exists(self):
        """Sanity check: the test audio file shipped with the repo is present."""
        self.assertTrue(
            self.AUDIO_FIXTURE.exists(),
            f"Audio fixture not found at {self.AUDIO_FIXTURE}",
        )

    def test_audio_message_stored_with_correct_type_and_media_id(self):
        """message_type must be 'audio' and media_id must match the payload."""
        r = self.client.post(
            "/integrations/whatsapp/webhook",
            json=_audio_payload("5491100000001", "wamid.aud001", "MEDIA_ID_AUDIO_001"),
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.db.all_messages), 1)

        msg = self.db.all_messages[0]
        self.assertEqual(msg.message_type, "audio")
        self.assertEqual(msg.media_id, "MEDIA_ID_AUDIO_001")
        self.assertEqual(msg.direction, "in")
        self.assertEqual(msg.status, "received")

    def test_audio_message_text_is_null(self):
        """Audio messages have no transcribed text at the webhook level."""
        self.client.post(
            "/integrations/whatsapp/webhook",
            json=_audio_payload("5491100000001", "wamid.aud002", "MEDIA_ID_AUDIO_002"),
        )
        msg = self.db.all_messages[0]
        self.assertIsNone(msg.text)

    def test_audio_creates_ai_event_with_correct_fields(self):
        """
        AiEvent must be created so N8N can pick it up for transcription + reply.
        text is None (N8N will fetch the audio and transcribe it).
        """
        self.client.post(
            "/integrations/whatsapp/webhook",
            json=_audio_payload("5491100000002", "wamid.aud003", "MEDIA_ID_AUDIO_003",
                                name="Cliente Voz"),
        )
        self.assertEqual(len(self.db._ai_events), 1)
        event = list(self.db._ai_events.values())[0]
        self.assertEqual(event.status, "pending")
        self.assertEqual(event.wa_message_id, "wamid.aud003")
        self.assertIsNone(event.text)

    def test_audio_missing_media_id_not_stored(self):
        """
        Edge case: malformed payload with an empty audio.id.
        The webhook handler must skip it — no message or AI event should be created.
        """
        r = self.client.post(
            "/integrations/whatsapp/webhook",
            json=_audio_payload_no_media_id("5491100000003", "wamid.aud_bad"),
        )
        self.assertEqual(r.status_code, 200)  # webhook always returns 200
        self.assertEqual(len(self.db.all_messages), 0)
        self.assertEqual(len(self.db._ai_events), 0)

    def test_audio_deduplication_same_message_id_stored_once(self):
        """Retried delivery of the same audio message must not create duplicate rows."""
        payload = _audio_payload("5491100000004", "wamid.aud_dup", "MEDIA_ID_DUP")
        self.client.post("/integrations/whatsapp/webhook", json=payload)
        self.client.post("/integrations/whatsapp/webhook", json=payload)

        self.assertEqual(len(self.db.all_messages), 1)
        self.assertEqual(self.db.all_threads[0].unread_count, 1)

    def test_audio_increments_unread_count(self):
        """Audio messages must increment unread_count just like text messages."""
        self.client.post("/integrations/whatsapp/webhook",
                         json=_audio_payload("5491100000005", "wamid.aud_unr", "MEDIA_ID_UNR"))
        self.assertEqual(self.db.all_threads[0].unread_count, 1)

    def test_audio_in_existing_thread_maintains_continuity(self):
        """
        Flow: client sends text → then sends audio (voice note).
        Both messages must be in the same thread.
        State maintained before and after audio (unread increments correctly).
        """
        wa_id = "5491100000006"
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload(wa_id, "wamid.mix_t1",
                                            "Hola buenas tardes, cuanto está el servicio?"))
        self.client.post("/integrations/whatsapp/webhook",
                         json=_audio_payload(wa_id, "wamid.mix_a1", "MEDIA_ID_MIX_001"))

        self.assertEqual(len(self.db.all_threads), 1)
        self.assertEqual(len(self.db.all_messages), 2)
        self.assertEqual(self.db.all_threads[0].unread_count, 2)

        thread_ids = {m.thread_id for m in self.db.all_messages}
        self.assertEqual(len(thread_ids), 1, "Both messages must share the same thread_id")

        types = {m.message_type for m in self.db.all_messages}
        self.assertIn("text", types)
        self.assertIn("audio", types)

    def test_very_short_audio_stored_identically_to_regular_audio(self):
        """
        Edge case: a very short voice note (e.g. 1 second, ~4 KB).
        The webhook handler does not inspect duration — it must store it exactly
        like any other audio message.  Transcription quality is N8N's concern.
        """
        self.client.post(
            "/integrations/whatsapp/webhook",
            json=_audio_payload("5491100000007", "wamid.aud_short", "MEDIA_ID_SHORT_001"),
        )
        msg = self.db.all_messages[0]
        self.assertEqual(msg.message_type, "audio")
        self.assertEqual(msg.media_id, "MEDIA_ID_SHORT_001")
        self.assertIsNone(msg.text)


if __name__ == "__main__":
    unittest.main()
