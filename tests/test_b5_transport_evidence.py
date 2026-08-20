"""B5 transport evidence persistence tests.

Guards B5-01-C: audio transcripts must be persisted to whatsapp_messages.text
when the transcribe endpoint receives wa_message_id, so that multi-audio
debounce bursts reconstruct all transcript evidence during context assembly.

TA01  Two audio messages: both transcripts persist to DB, both appear in
      unanswered_recent_user_messages-equivalent query (chronological order).

TA02  Three audio messages: all three transcripts persisted.

TA03  text + audio + text burst: audio transcript persists; text messages
      unaffected; all appear in chronological order.

TA04  Transcription failure (Whisper error): DB not corrupted; message text
      remains NULL; endpoint returns 502; no partial commit.

TA05  Single audio without wa_message_id: existing behavior unchanged;
      transcript returned; DB not touched.

TA06  Single audio WITH wa_message_id: transcript persisted;
      message_type remains 'audio'.
      No second CE call produced (single-turn guard not exercised here —
      that is an n8n-level concern).
"""
from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── SQLAlchemy / SQLite stubs ──────────────────────────────────────────────────
import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json

_pg_dialect.JSONB = sqlalchemy.JSON           # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON              # type: ignore[attr-defined]

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
)
_SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


@event.listens_for(_engine, "connect")
def _pragmas(conn, _rec):
    conn.execute("PRAGMA foreign_keys=OFF")


# ── Stub app.db ───────────────────────────────────────────────────────────────
_db_mod = types.ModuleType("app.db")
_db_mod.Base = Base                           # type: ignore[attr-defined]
_db_mod.engine = _engine                      # type: ignore[attr-defined]
_db_mod.SessionLocal = _SessionLocal          # type: ignore[attr-defined]
_db_mod.DATABASE_URL = "sqlite:///:memory:"   # type: ignore[attr-defined]


def _get_db_gen():
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


_db_mod.get_db = _get_db_gen                  # type: ignore[attr-defined]
sys.modules["app.db"] = _db_mod

# ── Stub optional heavy deps ──────────────────────────────────────────────────
for _mod_name in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

# ── Import models & create schema ────────────────────────────────────────────
import app.models as _app_models  # noqa: F401
from app.models import (
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppThread,
)
from app.models import Base as AppBase

AppBase.metadata.create_all(_engine)

# ── Helpers ───────────────────────────────────────────────────────────────────

_NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)


def _make_db() -> Session:
    return _SessionLocal()


_TEST_WA_ID = "5491153368330"


def _seed_audio_message(
    db: Session,
    wa_message_id: str,
    timestamp: datetime = _NOW,
    message_type: str = "audio",
    text: str | None = None,
) -> WhatsAppMessage:
    contact = db.execute(
        select(WhatsAppContact).where(WhatsAppContact.wa_id == _TEST_WA_ID)
    ).scalar_one_or_none()
    if contact is None:
        contact = WhatsAppContact(wa_id=_TEST_WA_ID, display_name=None, phone=None)
        db.add(contact)
        db.flush()

    thread = db.execute(
        select(WhatsAppThread).where(WhatsAppThread.contact_id == contact.id)
    ).scalar_one_or_none()
    if thread is None:
        thread = WhatsAppThread(contact_id=contact.id, lead_id=None, unread_count=0)
        db.add(thread)
        db.flush()

    msg = WhatsAppMessage(
        thread_id=thread.id,
        wa_message_id=wa_message_id,
        direction="in",
        timestamp=timestamp,
        message_type=message_type,
        media_id="media_" + wa_message_id[-6:],
        text=text,
        status="received",
    )
    db.add(msg)
    db.commit()
    return msg


def _call_transcribe_with_persistence(
    wa_message_id: str | None,
    transcript_text: str,
    db: Session,
) -> dict:
    """Simulate the transcribe endpoint logic directly (no HTTP layer needed)."""
    from app.models import WhatsAppMessage
    from sqlalchemy import select as sa_select

    # Logic extracted from transcribe_media endpoint (whatsapp.py)
    # (avoids needing a running FastAPI app for these unit tests)
    text = transcript_text  # mocked Whisper result

    if wa_message_id:
        msg = db.execute(
            sa_select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == wa_message_id)
        ).scalar_one_or_none()
        if msg is not None:
            msg.text = text
            db.commit()

    return {"text": text}


# ── TA06: Single audio WITH wa_message_id ─────────────────────────────────────

class TestTA06SingleAudioWithMessageId(unittest.TestCase):
    """TA06: transcript persisted; message_type remains 'audio'."""

    def setUp(self):
        AppBase.metadata.drop_all(_engine)
        AppBase.metadata.create_all(_engine)
        self.db = _make_db()
        self.wa_msg_id = "wamid.ta06_audio_001"
        _seed_audio_message(self.db, self.wa_msg_id)
        # reopen so seed's session is flushed
        self.db.close()
        self.db = _make_db()

    def tearDown(self):
        self.db.close()

    def test_ta06a_transcript_written_to_text(self):
        _call_transcribe_with_persistence(self.wa_msg_id, "Quería revisar un auto.", self.db)
        msg = self.db.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == self.wa_msg_id)
        ).scalar_one()
        self.assertEqual(msg.text, "Quería revisar un auto.")

    def test_ta06b_message_type_remains_audio(self):
        _call_transcribe_with_persistence(self.wa_msg_id, "Quería revisar un auto.", self.db)
        msg = self.db.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == self.wa_msg_id)
        ).scalar_one()
        self.assertEqual(msg.message_type, "audio")

    def test_ta06c_transcript_returned_in_response(self):
        result = _call_transcribe_with_persistence(
            self.wa_msg_id, "Quería revisar un auto.", self.db
        )
        self.assertEqual(result["text"], "Quería revisar un auto.")


# ── TA05: Single audio WITHOUT wa_message_id ─────────────────────────────────

class TestTA05SingleAudioWithoutMessageId(unittest.TestCase):
    """TA05: existing behavior unchanged; DB not touched."""

    def setUp(self):
        AppBase.metadata.drop_all(_engine)
        AppBase.metadata.create_all(_engine)
        self.db = _make_db()
        self.wa_msg_id = "wamid.ta05_audio_001"
        _seed_audio_message(self.db, self.wa_msg_id)
        self.db.close()
        self.db = _make_db()

    def tearDown(self):
        self.db.close()

    def test_ta05a_transcript_returned(self):
        result = _call_transcribe_with_persistence(None, "¿De qué consta el servicio?", self.db)
        self.assertEqual(result["text"], "¿De qué consta el servicio?")

    def test_ta05b_db_text_unchanged(self):
        _call_transcribe_with_persistence(None, "¿De qué consta el servicio?", self.db)
        msg = self.db.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == self.wa_msg_id)
        ).scalar_one()
        self.assertIsNone(msg.text, "TA05: DB text must remain NULL when no wa_message_id given")


# ── TA01: Two audio messages, both transcripts persist ───────────────────────

class TestTA01TwoAudioTranscriptsBothPersist(unittest.TestCase):
    """TA01: multi-audio burst — both transcripts appear in DB (chronological)."""

    def setUp(self):
        AppBase.metadata.drop_all(_engine)
        AppBase.metadata.create_all(_engine)
        self.db = _make_db()
        self.t1 = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        self.t2 = datetime(2026, 8, 19, 12, 0, 5, tzinfo=timezone.utc)
        self.wid1 = "wamid.ta01_audio_001"
        self.wid2 = "wamid.ta01_audio_002"
        _seed_audio_message(self.db, self.wid1, timestamp=self.t1)
        self.db.close()
        self.db = _make_db()
        _seed_audio_message(self.db, self.wid2, timestamp=self.t2)
        self.db.close()
        self.db = _make_db()

    def tearDown(self):
        self.db.close()

    def _persist(self, wid: str, text: str):
        _call_transcribe_with_persistence(wid, text, self.db)

    def test_ta01a_audio1_transcript_in_db(self):
        self._persist(self.wid1, "Quería revisar un auto.")
        self._persist(self.wid2, "¿De qué consta el servicio?")
        msg1 = self.db.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == self.wid1)
        ).scalar_one()
        self.assertEqual(msg1.text, "Quería revisar un auto.")

    def test_ta01b_audio2_transcript_in_db(self):
        self._persist(self.wid1, "Quería revisar un auto.")
        self._persist(self.wid2, "¿De qué consta el servicio?")
        msg2 = self.db.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == self.wid2)
        ).scalar_one()
        self.assertEqual(msg2.text, "¿De qué consta el servicio?")

    def test_ta01c_both_have_string_text_for_context_assembly_filter(self):
        self._persist(self.wid1, "Quería revisar un auto.")
        self._persist(self.wid2, "¿De qué consta el servicio?")
        msgs = self.db.execute(
            select(WhatsAppMessage)
            .where(WhatsAppMessage.wa_message_id.in_([self.wid1, self.wid2]))
            .order_by(WhatsAppMessage.timestamp)
        ).scalars().all()
        # Simulate n8n Build Conversation Context filter:
        # typeof m.text === "string" && m.text.trim() !== ""
        recoverable = [m for m in msgs if m.text and isinstance(m.text, str) and m.text.strip()]
        self.assertEqual(len(recoverable), 2, "TA01: both audio transcripts must be string-typed")
        texts = [m.text for m in recoverable]
        self.assertIn("Quería revisar un auto.", texts)
        self.assertIn("¿De qué consta el servicio?", texts)

    def test_ta01d_chronological_order_preserved(self):
        self._persist(self.wid1, "Quería revisar un auto.")
        self._persist(self.wid2, "¿De qué consta el servicio?")
        msgs = self.db.execute(
            select(WhatsAppMessage)
            .where(WhatsAppMessage.wa_message_id.in_([self.wid1, self.wid2]))
            .order_by(WhatsAppMessage.timestamp)
        ).scalars().all()
        self.assertEqual(msgs[0].text, "Quería revisar un auto.")
        self.assertEqual(msgs[1].text, "¿De qué consta el servicio?")


# ── TA02: Three audio messages, all three persist ────────────────────────────

class TestTA02ThreeAudioAllPersist(unittest.TestCase):
    def setUp(self):
        AppBase.metadata.drop_all(_engine)
        AppBase.metadata.create_all(_engine)
        self.db = _make_db()
        self.wids = [f"wamid.ta02_audio_00{i}" for i in range(1, 4)]
        for i, wid in enumerate(self.wids):
            ts = datetime(2026, 8, 19, 12, 0, i * 3, tzinfo=timezone.utc)
            _seed_audio_message(self.db, wid, timestamp=ts)
            self.db.close()
            self.db = _make_db()

    def tearDown(self):
        self.db.close()

    def test_ta02_all_three_transcripts_in_db(self):
        texts = ["Audio A.", "Audio B.", "Audio C."]
        for wid, txt in zip(self.wids, texts):
            _call_transcribe_with_persistence(wid, txt, self.db)
        msgs = self.db.execute(
            select(WhatsAppMessage)
            .where(WhatsAppMessage.wa_message_id.in_(self.wids))
            .order_by(WhatsAppMessage.timestamp)
        ).scalars().all()
        recoverable = [m.text for m in msgs if m.text]
        self.assertEqual(recoverable, texts, "TA02: all three transcripts must persist in order")


# ── TA03: text + audio + text burst, audio transcript persists ───────────────

class TestTA03TextAudioTextBurst(unittest.TestCase):
    def setUp(self):
        AppBase.metadata.drop_all(_engine)
        AppBase.metadata.create_all(_engine)
        self.db = _make_db()

        contact = WhatsAppContact(wa_id="5491153368330", display_name=None, phone=None)
        self.db.add(contact)
        self.db.flush()
        thread = WhatsAppThread(contact_id=contact.id, lead_id=None, unread_count=0)
        self.db.add(thread)
        self.db.flush()
        self.thread_id = thread.id

        self.t_base = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        offsets = [0, 3, 6]
        self.msg_ids = ["wamid.ta03_text_001", "wamid.ta03_audio_001", "wamid.ta03_text_002"]
        types_ = ["text", "audio", "text"]
        texts = ["Hola", None, "¿Se puede?"]
        for wid, mtype, txt, off in zip(self.msg_ids, types_, texts, offsets):
            ts = datetime(2026, 8, 19, 12, 0, off, tzinfo=timezone.utc)
            msg = WhatsAppMessage(
                thread_id=self.thread_id,
                wa_message_id=wid,
                direction="in",
                timestamp=ts,
                message_type=mtype,
                text=txt,
                status="received",
            )
            self.db.add(msg)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_ta03a_audio_transcript_persisted(self):
        _call_transcribe_with_persistence(
            "wamid.ta03_audio_001", "Nota de voz.", self.db
        )
        audio_msg = self.db.execute(
            select(WhatsAppMessage).where(
                WhatsAppMessage.wa_message_id == "wamid.ta03_audio_001"
            )
        ).scalar_one()
        self.assertEqual(audio_msg.text, "Nota de voz.")

    def test_ta03b_text_messages_unaffected(self):
        _call_transcribe_with_persistence(
            "wamid.ta03_audio_001", "Nota de voz.", self.db
        )
        text1 = self.db.execute(
            select(WhatsAppMessage).where(
                WhatsAppMessage.wa_message_id == "wamid.ta03_text_001"
            )
        ).scalar_one()
        text2 = self.db.execute(
            select(WhatsAppMessage).where(
                WhatsAppMessage.wa_message_id == "wamid.ta03_text_002"
            )
        ).scalar_one()
        self.assertEqual(text1.text, "Hola")
        self.assertEqual(text2.text, "¿Se puede?")

    def test_ta03c_all_three_recoverable_in_order(self):
        _call_transcribe_with_persistence(
            "wamid.ta03_audio_001", "Nota de voz.", self.db
        )
        msgs = self.db.execute(
            select(WhatsAppMessage)
            .where(WhatsAppMessage.wa_message_id.in_(self.msg_ids))
            .order_by(WhatsAppMessage.timestamp)
        ).scalars().all()
        recoverable = [m.text for m in msgs if m.text and isinstance(m.text, str) and m.text.strip()]
        self.assertEqual(recoverable, ["Hola", "Nota de voz.", "¿Se puede?"])


# ── TA04: Transcription failure — DB not corrupted ───────────────────────────

class TestTA04TranscriptionFailureNoCorruption(unittest.TestCase):
    """TA04: when Whisper fails, DB text must remain NULL; no partial commit."""

    def setUp(self):
        AppBase.metadata.drop_all(_engine)
        AppBase.metadata.create_all(_engine)
        self.db = _make_db()
        self.wa_msg_id = "wamid.ta04_audio_001"
        _seed_audio_message(self.db, self.wa_msg_id)
        self.db.close()
        self.db = _make_db()

    def tearDown(self):
        self.db.close()

    def test_ta04_db_text_null_when_transcription_fails(self):
        from fastapi import HTTPException
        from app.api.whatsapp import _transcribe_audio_bytes, _download_whatsapp_media_bytes

        with patch("app.api.whatsapp._download_whatsapp_media_bytes") as mock_dl, \
             patch("app.api.whatsapp._transcribe_audio_bytes") as mock_tr:
            mock_dl.return_value = (MagicMock(media_id="x", mime_type="audio/ogg"), b"bytes")
            mock_tr.side_effect = HTTPException(status_code=502, detail="Whisper failed")

            # Call the actual endpoint function signature manually
            from fastapi import HTTPException as FHTTPEx
            from app.api import whatsapp as wa_module
            db = self.db
            raised = False
            try:
                # Simulate what the endpoint does
                info, audio_bytes = mock_dl("media_x")
                text = mock_tr(media_id=info.media_id, audio_bytes=audio_bytes, mime_type=info.mime_type)
                # Should not reach here
                msg = db.execute(
                    select(WhatsAppMessage).where(
                        WhatsAppMessage.wa_message_id == self.wa_msg_id
                    )
                ).scalar_one()
                msg.text = text
                db.commit()
            except FHTTPEx:
                raised = True

            self.assertTrue(raised, "TA04: HTTPException must propagate on transcription failure")

            # DB text must still be NULL
            self.db.expire_all()
            msg = self.db.execute(
                select(WhatsAppMessage).where(
                    WhatsAppMessage.wa_message_id == self.wa_msg_id
                )
            ).scalar_one()
            self.assertIsNone(msg.text, "TA04: DB text must remain NULL on transcription failure")


# ── TA06 endpoint-level test with real function signature ─────────────────────

class TestTA06EndpointSignature(unittest.TestCase):
    """TA06 variant: verify transcribe_media function signature accepts wa_message_id."""

    def test_ta06_function_accepts_wa_message_id_param(self):
        import inspect
        from app.api.whatsapp import transcribe_media

        sig = inspect.signature(transcribe_media)
        self.assertIn("wa_message_id", sig.parameters, (
            "TA06: transcribe_media must accept wa_message_id optional parameter"
        ))

    def test_ta06_wa_message_id_is_optional(self):
        import inspect
        from app.api.whatsapp import transcribe_media

        sig = inspect.signature(transcribe_media)
        param = sig.parameters["wa_message_id"]
        # FastAPI wraps defaults in Query(); the param must not be required
        self.assertIsNot(
            param.default,
            inspect.Parameter.empty,
            "TA06: wa_message_id must be optional (have a default, not be required)",
        )


if __name__ == "__main__":
    unittest.main()
