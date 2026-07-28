"""M21.0.1 — Live Path Contract Tests.

Proves that the payload currently emitted by n8n is accepted by
/api/conversation/handle and that CE returns the response shape
expected by n8n's IF - Engine Handled? nodes.

Test index:
  RC41 — Normal n8n payload validates against ConversationHandleIn
  RC42 — Flow n8n payload validates against ConversationHandleIn
  RC43 — CE returns the response contract expected by n8n (handled/action/ok)
  RC44 — Backward-compatible minimal caller still validates

All tests are fully offline: no containers, no network, no production DB.
No production code was modified to enable these tests.
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
FIXTURES_DIR = ROOT_DIR / "tests" / "fixtures" / "m21"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── SQLAlchemy / SQLite in-memory ─────────────────────────────────────────────
import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json

_pg_dialect.JSONB = sqlalchemy.JSON           # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON              # type: ignore[attr-defined]

from sqlalchemy import create_engine, event, text as sql_text
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

# ── Import models ─────────────────────────────────────────────────────────────
import app.models  # noqa: F401
from app.models import (
    Lead,
    WhatsAppContact,
    WhatsAppThread,
    WhatsAppThreadState,
)

# Use the models' registered MetaData rather than this file's Base, so tables
# are always created correctly regardless of which test file was imported first.
Lead.__table__.metadata.create_all(_engine)

# ── Import units under test ───────────────────────────────────────────────────
from app.schemas.conversation import (
    HANDLED_ACTIONS,
    ConversationHandleIn,
    ConversationHandleOut,
)
from app.services.conversation_engine import ConversationEngine
from app.services.pricing import PricingService

_NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)
_WA_ID = "5491100000099"


def _new_session() -> Session:
    return _SessionLocal()


def _make_settings():
    s = MagicMock()
    s.openai_api_key = "sk-test-fake"
    s.openai_chat_model = "gpt-4o-mini"
    s.backend_url = "http://localhost:8000"
    s.whatsapp_flow_id = ""
    s.whatsapp_vehicle_fallback_flow_id = ""
    s.whatsapp_location_fallback_flow_id = ""
    s.whatsapp_website_flow_id = ""
    return s


def _make_engine(db: Session) -> ConversationEngine:
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = db
    eng.settings = _make_settings()
    eng._pricing = PricingService(repository=MagicMock())
    from app.services.schedule import ScheduleService
    eng._schedule = ScheduleService(db=db)
    return eng


def _seed_thread_with_human_takeover(db: Session):
    """Seed a thread whose lead has needs_human=True.
    CE returns skipped_human (handled=True) without any outbound or AI call.
    This is the cleanest deterministic path for contract testing.
    """
    for tbl in [
        "whatsapp_outbound_dedup", "whatsapp_messages",
        "whatsapp_thread_candidates", "whatsapp_thread_states",
        "whatsapp_threads", "whatsapp_contacts", "leads",
    ]:
        try:
            db.execute(sql_text(f"DELETE FROM {tbl}"))
        except Exception:
            pass
    db.commit()

    contact = WhatsAppContact(wa_id=_WA_ID, display_name="Test", phone=None)
    db.add(contact)
    db.flush()

    lead = Lead(flag="PRESUPUESTANDO", estado="CONSULTA_NUEVA",
                nombre="Test", necesita_humano=True)
    db.add(lead)
    db.flush()

    thread = WhatsAppThread(
        contact_id=contact.id, lead_id=lead.id,
        unread_count=0, created_at=_NOW,
    )
    db.add(thread)
    db.flush()

    state = WhatsAppThreadState(
        thread_id=thread.id,
        needs_human=True,
        last_stage="QUALIFYING",
        vehicle_clarification_sent=False,
        location_clarification_sent=False,
        vehicle_fallback_flow_sent=False,
        location_fallback_flow_sent=False,
        created_at=_NOW,
        updated_at=_NOW,
    )
    db.add(state)
    db.commit()
    return thread


# ══════════════════════════════════════════════════════════════════════════════
# RC41 — Normal n8n payload validates
# ══════════════════════════════════════════════════════════════════════════════

class TestRC41NormalPayloadValidates(unittest.TestCase):
    """Load the sanitized fixture for Call Backend Engine (M18) and assert it
    validates against ConversationHandleIn without validation errors."""

    def _load(self) -> dict:
        path = FIXTURES_DIR / "n8n_ce_text_payload.json"
        self.assertTrue(path.exists(), f"Fixture not found: {path}")
        return json.loads(path.read_text())

    def test_rc41a_fixture_validates_against_schema(self):
        """ConversationHandleIn accepts the exact n8n normal-message payload."""
        raw = self._load()
        handle_in = ConversationHandleIn(**raw)
        self.assertEqual(handle_in.thread_id, raw["thread_id"])
        self.assertEqual(handle_in.wa_message_id, raw["wa_message_id"])
        self.assertEqual(handle_in.wa_id, raw["wa_id"])

    def test_rc41b_required_fields_present(self):
        """All three required fields (thread_id, wa_message_id, wa_id) are supplied."""
        raw = self._load()
        handle_in = ConversationHandleIn(**raw)
        self.assertIsInstance(handle_in.thread_id, int)
        self.assertIsInstance(handle_in.wa_message_id, str)
        self.assertIsInstance(handle_in.wa_id, str)
        self.assertGreater(len(handle_in.wa_message_id), 0)
        self.assertGreater(len(handle_in.wa_id), 0)

    def test_rc41c_context_arrays_are_lists(self):
        """Context arrays remain lists after deserialization."""
        raw = self._load()
        handle_in = ConversationHandleIn(**raw)
        self.assertIsInstance(handle_in.recent_user_messages, list)
        self.assertIsInstance(handle_in.unanswered_recent_user_messages, list)
        self.assertIsInstance(handle_in.recent_outbound_replies, list)

    def test_rc41d_message_type_is_text_normalized(self):
        """message_type from normal path is accepted as text/audio/image (not flow_response)."""
        raw = self._load()
        handle_in = ConversationHandleIn(**raw)
        self.assertNotEqual(handle_in.message_type, "flow_response",
                            "Normal path must not be routed as flow_response")

    def test_rc41e_no_unknown_field_rejection(self):
        """Extra fields from n8n are silently ignored (Pydantic default)."""
        raw = self._load()
        raw["_n8n_extra_field"] = "should_be_ignored"
        try:
            ConversationHandleIn(**raw)
        except Exception as exc:
            self.fail(f"Extra field caused validation error: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# RC42 — Flow n8n payload validates
# ══════════════════════════════════════════════════════════════════════════════

class TestRC42FlowPayloadValidates(unittest.TestCase):
    """Load the sanitized fixture for Call Backend Engine (Flow M18) and assert
    it validates against ConversationHandleIn."""

    def _load(self) -> dict:
        path = FIXTURES_DIR / "n8n_ce_flow_payload.json"
        self.assertTrue(path.exists(), f"Fixture not found: {path}")
        return json.loads(path.read_text())

    def test_rc42a_fixture_validates_against_schema(self):
        """ConversationHandleIn accepts the exact n8n flow-response payload."""
        raw = self._load()
        handle_in = ConversationHandleIn(**raw)
        self.assertEqual(handle_in.thread_id, raw["thread_id"])
        self.assertEqual(handle_in.wa_message_id, raw["wa_message_id"])

    def test_rc42b_message_type_is_flow_response(self):
        """message_type must be 'flow_response' for the flow path."""
        raw = self._load()
        handle_in = ConversationHandleIn(**raw)
        self.assertEqual(handle_in.message_type, "flow_response")

    def test_rc42c_flow_response_is_dict(self):
        """flow_response field must be accepted as a dict."""
        raw = self._load()
        handle_in = ConversationHandleIn(**raw)
        self.assertIsInstance(handle_in.flow_response, dict)
        self.assertGreater(len(handle_in.flow_response), 0)

    def test_rc42d_flow_token_accepted(self):
        """flow_token is accepted as an optional string."""
        raw = self._load()
        handle_in = ConversationHandleIn(**raw)
        self.assertIsInstance(handle_in.flow_token, str)

    def test_rc42e_flow_response_branch_selected(self):
        """CE's _handle() would route this to _process_flow_response, not _process_text.

        This is a structural proof: the condition in _handle() is
        message_type == 'flow_response' and flow_response is not None.
        """
        raw = self._load()
        handle_in = ConversationHandleIn(**raw)
        self.assertEqual(handle_in.message_type, "flow_response")
        self.assertIsNotNone(handle_in.flow_response)
        # Both conditions for flow routing satisfied.
        self.assertTrue(
            handle_in.message_type == "flow_response" and handle_in.flow_response,
            "Payload must satisfy _handle() flow-response routing condition",
        )


# ══════════════════════════════════════════════════════════════════════════════
# RC43 — CE returns the response contract expected by n8n
# ══════════════════════════════════════════════════════════════════════════════

class TestRC43CEResponseContract(unittest.TestCase):
    """POST a realistic n8n-shaped request to CE.

    Scenario: thread has needs_human=True — CE returns skipped_human (handled=True)
    without any outbound or OpenAI call. This is the cleanest deterministic path
    that produces handled=True and proves the response contract.

    The response is validated against:
      - ConversationHandleOut field presence
      - handled=True → IF - Engine Handled? (M18) evaluates true
      - action in HANDLED_ACTIONS → consistent with handled=True
      - No real outbound occurred
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        self.thread = _seed_thread_with_human_takeover(self.db)
        self.eng = _make_engine(self.db)

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    def test_rc43a_response_has_required_fields(self):
        """CE response contains handled, action, ok — the fields n8n's IF node reads."""
        raw = json.loads((FIXTURES_DIR / "n8n_ce_text_payload.json").read_text())
        raw["thread_id"] = self.thread.id
        raw["wa_id"] = _WA_ID
        raw["wa_message_id"] = "wamid.rc43.contract.001"

        result = self.eng.handle(ConversationHandleIn(**raw))

        self.assertIsInstance(result, ConversationHandleOut)
        self.assertIsNotNone(result.handled,  "handled field must be present")
        self.assertIsNotNone(result.action,   "action field must be present")
        self.assertIsNotNone(result.ok,       "ok field must be present")

    def test_rc43b_handled_true_for_valid_turn(self):
        """CE returns handled=True for skipped_human — n8n's IF node stops the fallback."""
        raw = json.loads((FIXTURES_DIR / "n8n_ce_text_payload.json").read_text())
        raw["thread_id"] = self.thread.id
        raw["wa_id"] = _WA_ID
        raw["wa_message_id"] = "wamid.rc43.contract.002"

        result = self.eng.handle(ConversationHandleIn(**raw))

        self.assertTrue(result.handled,
                        "handled must be True so n8n's IF - Engine Handled? stops")

    def test_rc43c_action_is_in_handled_actions(self):
        """action value is consistent with HANDLED_ACTIONS when handled=True."""
        raw = json.loads((FIXTURES_DIR / "n8n_ce_text_payload.json").read_text())
        raw["thread_id"] = self.thread.id
        raw["wa_id"] = _WA_ID
        raw["wa_message_id"] = "wamid.rc43.contract.003"

        result = self.eng.handle(ConversationHandleIn(**raw))

        if result.handled:
            self.assertIn(
                result.action, HANDLED_ACTIONS,
                f"action={result.action!r} not in HANDLED_ACTIONS when handled=True",
            )

    def test_rc43d_if_engine_handled_evaluates_correctly(self):
        """n8n's IF condition ($json.handled == true) evaluates True for this response.

        The IF - Engine Handled? node checks: $json.handled == true (boolean).
        Python bool True serializes to JSON true, which n8n's boolean comparison accepts.
        """
        raw = json.loads((FIXTURES_DIR / "n8n_ce_text_payload.json").read_text())
        raw["thread_id"] = self.thread.id
        raw["wa_id"] = _WA_ID
        raw["wa_message_id"] = "wamid.rc43.contract.004"

        result = self.eng.handle(ConversationHandleIn(**raw))
        response_json = result.model_dump()

        # n8n evaluates: $json.handled == true
        self.assertIn("handled", response_json)
        self.assertIs(response_json["handled"], True,
                      "Serialized handled must be Python True (→ JSON true)")

    def test_rc43e_no_real_outbound_occurred(self):
        """No WhatsApp message with status=sent exists after the handled turn."""
        from sqlalchemy import select
        from app.models import WhatsAppMessage

        raw = json.loads((FIXTURES_DIR / "n8n_ce_text_payload.json").read_text())
        raw["thread_id"] = self.thread.id
        raw["wa_id"] = _WA_ID
        raw["wa_message_id"] = "wamid.rc43.contract.005"

        self.eng.handle(ConversationHandleIn(**raw))

        sent = self.db.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.status == "sent")
        ).scalars().all()
        self.assertEqual(len(sent), 0, "No Meta message may be marked sent")


# ══════════════════════════════════════════════════════════════════════════════
# RC44 — Backward-compatible minimal caller
# ══════════════════════════════════════════════════════════════════════════════

class TestRC44BackwardCompatibleCaller(unittest.TestCase):
    """Prove that existing unit-test callers (which do not send n8n context arrays)
    remain schema-compatible. No n8n-only fields must be required."""

    def test_rc44a_minimal_payload_validates(self):
        """thread_id + wa_message_id + wa_id alone satisfy ConversationHandleIn."""
        handle_in = ConversationHandleIn(
            thread_id=1,
            wa_message_id="wamid.minimal.001",
            wa_id="5491100000001",
        )
        self.assertEqual(handle_in.thread_id, 1)
        self.assertEqual(handle_in.message_type, "text")  # default
        self.assertIsNone(handle_in.text)                  # default
        self.assertEqual(handle_in.recent_user_messages, [])
        self.assertEqual(handle_in.unanswered_recent_user_messages, [])
        self.assertEqual(handle_in.recent_outbound_replies, [])
        self.assertIsNone(handle_in.flow_response)
        self.assertIsNone(handle_in.flow_token)

    def test_rc44b_no_observability_fields_required(self):
        """n8n_execution_id or similar observability fields must NOT be required."""
        # If M21.0.1 incorrectly added a required observability field, this fails.
        try:
            ConversationHandleIn(
                thread_id=1,
                wa_message_id="wamid.compat.001",
                wa_id="5491100000001",
                text="Hola",
            )
        except Exception as exc:
            self.fail(f"Minimal payload with text failed validation: {exc}")

    def test_rc44c_context_arrays_default_to_empty(self):
        """n8n context arrays default to [] — callers that omit them still work."""
        handle_in = ConversationHandleIn(
            thread_id=1,
            wa_message_id="wamid.compat.002",
            wa_id="5491100000001",
            text="Hola",
        )
        self.assertEqual(handle_in.recent_user_messages, [])
        self.assertEqual(handle_in.unanswered_recent_user_messages, [])
        self.assertEqual(handle_in.recent_outbound_replies, [])

    def test_rc44d_n8n_and_minimal_payloads_produce_same_type(self):
        """Both n8n payload and minimal payload produce ConversationHandleIn instances."""
        n8n_raw = json.loads((FIXTURES_DIR / "n8n_ce_text_payload.json").read_text())
        n8n_in = ConversationHandleIn(**n8n_raw)
        minimal_in = ConversationHandleIn(
            thread_id=1,
            wa_message_id="wamid.compat.003",
            wa_id="5491100000001",
        )
        self.assertIsInstance(n8n_in, ConversationHandleIn)
        self.assertIsInstance(minimal_in, ConversationHandleIn)
        self.assertIs(type(n8n_in), type(minimal_in))


# ══════════════════════════════════════════════════════════════════════════════
# Fixture integrity
# ══════════════════════════════════════════════════════════════════════════════

class TestFixtureIntegrity(unittest.TestCase):
    """Verify the fixture files are valid JSON and contain no production data."""

    def test_fixtures_are_valid_json(self):
        for fname in ("n8n_ce_text_payload.json", "n8n_ce_flow_payload.json"):
            with self.subTest(file=fname):
                path = FIXTURES_DIR / fname
                self.assertTrue(path.exists())
                data = json.loads(path.read_text())
                self.assertIsInstance(data, dict)

    def test_no_real_wa_ids(self):
        """Fixture wa_id values are test numbers, not production phone numbers."""
        for fname in ("n8n_ce_text_payload.json", "n8n_ce_flow_payload.json"):
            data = json.loads((FIXTURES_DIR / fname).read_text())
            wa_id = data.get("wa_id", "")
            self.assertTrue(
                wa_id.startswith("549110000"),
                f"{fname}: wa_id {wa_id!r} must be a sanitized test number",
            )

    def test_no_real_tokens(self):
        """flow_token in flow fixture contains the word 'sanitized' to mark it as test data."""
        data = json.loads((FIXTURES_DIR / "n8n_ce_flow_payload.json").read_text())
        token = data.get("flow_token", "")
        self.assertIn("sanitized", token.lower(),
                      "flow_token must contain 'sanitized' to be clearly marked as test data")

    def test_readme_exists(self):
        self.assertTrue((FIXTURES_DIR / "README.md").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
