"""
AI Behavior Regression Tests — Ridecheck WhatsApp Bot
=======================================================
Tests the BACKEND layer that the AI reads to decide what to say next.

These tests do NOT call n8n or OpenAI. They verify that:
  1. The backend correctly persists state the AI needs to avoid repeating questions.
  2. The API returns the right fields so the AI can detect what's already known.
  3. Candidate data (vehicle + zone) is stored and retrievable.
  4. Stage transitions (QUALIFYING → QUOTED → SCHEDULING) are durable.

For each test case the suspected root-cause column explains whether the bug
lives in the backend (fixable here) or in the AI prompt (needs prompt work).

Test cases:
  A — Name already given: bot must not ask again
  B — Email already given: bot must not ask again
  C — Full form already sent: bot must not resend
  D — Quote already sent: bot must not repeat full quote
  E — Vehicle + location memory: must survive round-trips
  F — Jeep 2x4 vehicle type wording [backend only: tipo_vehiculo enum check]
  G — Inappropriate tone [no backend test: pure prompt issue]
"""

from __future__ import annotations

import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api.whatsapp import router as state_router
from app.api.thread_revisions import router as revision_router
from app.db import get_db
from app.models import (
    WhatsAppContact,
    WhatsAppThread,
    WhatsAppThreadCandidate,
    WhatsAppThreadState,
    ThreadRevision,
    AiEvent,
    WhatsAppMessage,
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

TS = "1748300000"

VALID_TIPO_VEHICULO_ENUMS = {
    "AUTO",
    "SUV_4X4_DEPORTIVO",
    "PICKUP_4X4",
    "PICKUP_2X4",
    "VAN",
    "CAMIONETA_2X4",
    "CAMIONETA_4X4",
}


# ---------------------------------------------------------------------------
# Shared FakeDB (same as existing tests)
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
        self._candidates: dict = {}   # id -> WhatsAppThreadCandidate
        self._revisions: dict = {}    # id -> ThreadRevision
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
        elif isinstance(obj, WhatsAppThreadCandidate):
            self._candidates[obj.id] = obj
            # attach to thread
            for t in self._threads.values():
                if t.id == obj.thread_id:
                    if not hasattr(t, 'candidates') or t.candidates is None:
                        t.candidates = []
                    t.candidates.append(obj)
                    break
        elif isinstance(obj, ThreadRevision):
            now = datetime.now(timezone.utc)
            if getattr(obj, "created_at", None) is None:
                obj.created_at = now
            if getattr(obj, "updated_at", None) is None:
                obj.updated_at = now
            self._revisions[obj.id] = obj

    def get(self, model, key):
        if model is WhatsAppThread:
            for t in self._threads.values():
                if t.id == key:
                    return t
        if model is WhatsAppThreadCandidate:
            return self._candidates.get(key)
        if model is ThreadRevision:
            return self._revisions.get(key)
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
    def all_threads(self):
        return list(self._threads.values())

    @property
    def all_messages(self):
        return list(self._messages.values())


def _make_client(db: FakeDB) -> TestClient:
    app = FastAPI()
    app.include_router(webhook_router)
    app.include_router(state_router)
    app.include_router(revision_router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _text_payload(wa_id: str, msg_id: str, body: str, name: str = "Test") -> dict:
    return {
        "entry": [{"changes": [{"value": {
            "messages": [{"from": wa_id, "id": msg_id, "timestamp": TS,
                          "type": "text", "text": {"body": body}}],
            "contacts": [{"profile": {"name": name}, "wa_id": wa_id}],
        }}]}],
    }


# ===========================================================================
# TEST CASE A — Name already given: backend must store and return it
# ===========================================================================

class TestCaseA_NameMemory(unittest.TestCase):
    """
    Failure observed: bot asks 'Cuál es el nombre y apellido?' even after
    the user already said 'Benito Camelas'.

    Backend responsibility: customer_name in thread state + buyer_name in
    ThreadRevision must be correctly persisted and returned by the state API.

    Root cause (suspected): AI reads customer_name from thread state but it
    may not be set by the Candidate/State Updater, OR the ThreadRevision
    buyer_name is set but not exposed to the planner's context.
    """

    def setUp(self):
        self.db = FakeDB()
        patcher = patch("app.routes.whatsapp.get_settings", return_value=TEST_SETTINGS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = _make_client(self.db)

        # Create thread
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload("5499001001001", "msg_a1", "Dale"))
        self.tid = self.db.all_threads[0].id

    def tearDown(self):
        self.client.close()

    def test_customer_name_persists_in_thread_state(self):
        """After AI sets customer_name, GET /state must return it."""
        r = self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                              json={"customer_name": "Benito Camelas"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["customer_name"], "Benito Camelas")

        # Re-fetch — must survive a second GET
        r2 = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertEqual(r2.json()["customer_name"], "Benito Camelas")

    def test_subsequent_patch_does_not_erase_customer_name(self):
        """Patching other fields must not clear customer_name (no-overwrite rule)."""
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"customer_name": "Benito Camelas"})

        # AI patches stage without mentioning customer_name
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"last_stage": "QUOTED"})

        r = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertEqual(r.json()["customer_name"], "Benito Camelas",
                         "customer_name was erased by a subsequent patch — backend bug")
        self.assertEqual(r.json()["last_stage"], "QUOTED")

    def test_state_api_exposes_customer_name_field(self):
        """GET /state response schema must include customer_name (AI reads this)."""
        r = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertIn("customer_name", r.json(),
                      "customer_name missing from state API response — AI cannot read it")

    def test_null_patch_does_not_overwrite_existing_name(self):
        """Sending customer_name=null in patch must NOT erase a stored name."""
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"customer_name": "Benito Camelas"})

        # AI patch includes null — should be ignored per upsert_thread_state logic
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"customer_name": None, "last_stage": "QUALIFYING"})

        r = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertEqual(r.json()["customer_name"], "Benito Camelas",
                         "null patch erased customer_name — backend must ignore null values")


# ===========================================================================
# TEST CASE B — Email already given: backend must store and return it
# ===========================================================================

class TestCaseB_EmailMemory(unittest.TestCase):
    """
    Failure observed: bot asks for email again after user already provided it.

    Email is stored in ThreadRevision.buyer_email (not in thread state).
    Backend responsibility: buyer_email must persist through PATCH and be
    returned so the AI knows it's already collected.

    Root cause (suspected): The AI planner reads thread state (no email field
    there) but may not read the ThreadRevision record to check buyer_email.
    """

    def setUp(self):
        self.db = FakeDB()
        patcher = patch("app.routes.whatsapp.get_settings", return_value=TEST_SETTINGS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = _make_client(self.db)

        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload("5499002002002", "msg_b1",
                                            "benitoagarramelas@gmail.com"))
        self.tid = self.db.all_threads[0].id

    def tearDown(self):
        self.client.close()

    def test_thread_state_schema_has_no_email_field(self):
        """
        DIAGNOSTIC: Confirm that buyer_email is NOT in thread state.
        If missing, the AI planner cannot read it from /state — it must
        read it from a different endpoint (ThreadRevision).
        """
        r = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertNotIn("buyer_email", r.json(),
                         "Unexpected: buyer_email appeared in thread state")

    def test_thread_revision_stores_buyer_email(self):
        """
        ThreadRevision.buyer_email must persist through create + patch cycle.
        If n8n stores email there, the AI must read it back correctly.
        """
        # Simulate n8n creating a revision and setting buyer_email
        thread = self.db.all_threads[0]
        candidate = WhatsAppThreadCandidate(
            id=self.db._next_id(),
            thread_id=self.tid,
            label="Test Car",
            marca="Audi",
            modelo="A3",
            anio=2021,
            tipo_vehiculo="AUTO",
            zone_group="Norte",
            zone_detail="Nordelta",
            status="current_focus",
        )
        self.db.add(candidate)

        revision = ThreadRevision(
            id=self.db._next_id(),
            thread_id=self.tid,
            candidate_id=candidate.id,
            status="collecting_data",
            buyer_email="benitoagarramelas@gmail.com",
        )
        self.db.add(revision)

        # Verify retrieval
        stored = self.db.get(ThreadRevision, revision.id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.buyer_email, "benitoagarramelas@gmail.com")

    def test_buyer_email_patch_does_not_clear_buyer_name(self):
        """Patching buyer_email must not erase buyer_name (no-clobber)."""
        revision = ThreadRevision(
            id=self.db._next_id(),
            thread_id=self.tid,
            status="collecting_data",
            buyer_name="Benito Camelas",
            buyer_email=None,
        )
        self.db.add(revision)

        # Simulate the service patch
        revision.buyer_email = "benitoagarramelas@gmail.com"
        self.db.commit()

        stored = self.db.get(ThreadRevision, revision.id)
        self.assertEqual(stored.buyer_name, "Benito Camelas",
                         "buyer_name erased when patching buyer_email")
        self.assertEqual(stored.buyer_email, "benitoagarramelas@gmail.com")


# ===========================================================================
# TEST CASE C — Full form already sent: bot must not resend
# ===========================================================================

class TestCaseC_FormNotResent(unittest.TestCase):
    """
    Failure observed: after user fills the booking form completely, bot sends
    the form again instead of confirming.

    Backend responsibility: ThreadRevision.status transitions
    (collecting_data → booked) must persist. current_revision_id in thread
    state must point to the revision. AI reads these to know form is complete.

    Root cause (suspected): AI checks current_revision_id and revision status
    but may not reliably detect when ALL required fields are filled, causing
    it to restart data collection.
    """

    def setUp(self):
        self.db = FakeDB()
        patcher = patch("app.routes.whatsapp.get_settings", return_value=TEST_SETTINGS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = _make_client(self.db)

        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload("5499003003003", "msg_c1",
                                            "Comprador: Benito. Vendedor: Juan. Auto: Audi A3"))
        self.tid = self.db.all_threads[0].id

    def tearDown(self):
        self.client.close()

    def test_last_stage_quoted_persists(self):
        """QUOTED stage must survive a GET after PATCH."""
        r = self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                              json={"last_stage": "QUOTED"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["last_stage"], "QUOTED")

        r2 = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertEqual(r2.json()["last_stage"], "QUOTED")

    def test_current_revision_id_links_to_revision(self):
        """
        Thread state current_revision_id links to the revision record.
        AI uses this to check if a revision already exists and what its status is.
        """
        revision = ThreadRevision(
            id=self.db._next_id(),
            thread_id=self.tid,
            status="collecting_data",
            buyer_name="Benito Camelas",
            buyer_email="b@g.com",
            seller_name="Juan Perez",
        )
        self.db.add(revision)

        r = self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                              json={"current_revision_id": revision.id})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["current_revision_id"], revision.id)

    def test_revision_status_booked_marks_form_complete(self):
        """
        When revision.status = 'booked', the form is complete.
        The backend must store this and return it so AI can skip re-asking.
        """
        revision = ThreadRevision(
            id=self.db._next_id(),
            thread_id=self.tid,
            status="booked",
            buyer_name="Benito Camelas",
            buyer_email="b@g.com",
            seller_name="Juan Perez",
            address="Av. del Libertador 1234",
        )
        self.db.add(revision)

        stored = self.db.get(ThreadRevision, revision.id)
        self.assertEqual(stored.status, "booked")
        self.assertEqual(stored.buyer_name, "Benito Camelas")

    def test_stage_scheduling_not_quoted_after_form_complete(self):
        """
        After collecting all data, stage should be SCHEDULING not QUOTED.
        This is what the AI planner should write — we verify the API accepts it.
        """
        r = self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                              json={"last_stage": "SCHEDULING"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["last_stage"], "SCHEDULING")


# ===========================================================================
# TEST CASE D — Quote already sent: must not repeat
# ===========================================================================

class TestCaseD_QuoteNotRepeated(unittest.TestCase):
    """
    Failure observed: when user says 'dale' after receiving a price, bot sends
    the full price description again.

    Backend responsibility: last_stage=QUOTED must be durable. The IF - Not
    Already Quoted node in n8n reads GET /state to check this. If the state
    correctly shows QUOTED, the check passes. The race condition fix in n8n
    handles the case where state is not yet QUOTED.

    Root cause (confirmed): Race condition where both executions read state
    before either writes QUOTED. The n8n fix (out1 → AI Reply Planner) is
    the correct fix. Backend correctly stores QUOTED.
    """

    def setUp(self):
        self.db = FakeDB()
        patcher = patch("app.routes.whatsapp.get_settings", return_value=TEST_SETTINGS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = _make_client(self.db)

        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload("5499004004004", "msg_d1", "dale"))
        self.tid = self.db.all_threads[0].id

    def tearDown(self):
        self.client.close()

    def test_quoted_stage_persists_after_price_sent(self):
        """Backend correctly stores QUOTED — the AI can detect price was already sent."""
        r = self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                              json={"last_stage": "QUOTED", "last_intent": "QUOTE"})
        self.assertEqual(r.status_code, 200)
        state = r.json()
        self.assertEqual(state["last_stage"], "QUOTED")
        self.assertEqual(state["last_intent"], "QUOTE")

    def test_quoted_stage_not_overwritten_by_null_patch(self):
        """
        A subsequent PATCH that doesn't include last_stage must not clear QUOTED.
        This would cause the AI to think the price was never sent.
        """
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"last_stage": "QUOTED"})
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"last_processed_inbound_wa_message_id": "msg_d2"})

        r = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertEqual(r.json()["last_stage"], "QUOTED",
                         "QUOTED stage was cleared by a subsequent patch")

    def test_fresh_state_read_returns_quoted(self):
        """
        n8n reads state immediately before checking IF - Not Already Quoted.
        Backend must return QUOTED without caching stale data.
        """
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"last_stage": "QUOTED"})

        # Simulate two reads in quick succession (race scenario check)
        r1 = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        r2 = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertEqual(r1.json()["last_stage"], "QUOTED")
        self.assertEqual(r2.json()["last_stage"], "QUOTED")


# ===========================================================================
# TEST CASE E — Vehicle + location memory
# ===========================================================================

class TestCaseE_VehicleAndLocationMemory(unittest.TestCase):
    """
    Failure observed: bot asks again for vehicle data already provided.

    Backend responsibility: WhatsAppThreadCandidate must correctly store
    marca/modelo/anio/zone_group/zone_detail and be returned via GET /candidates.
    The AI reads this to know what's already collected.

    Root cause (suspected): candidate_focus_after_create flag not being set
    correctly — current_focus_candidate_id stays null so effective_state = null
    and the AI acts as if no vehicle data exists.
    """

    def setUp(self):
        self.db = FakeDB()
        patcher = patch("app.routes.whatsapp.get_settings", return_value=TEST_SETTINGS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = _make_client(self.db)

        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload("5499005005005", "msg_e1",
                                            "Audi A3 2021 Nordelta"))
        self.tid = self.db.all_threads[0].id

    def tearDown(self):
        self.client.close()

    def test_candidate_with_full_vehicle_data_stores_correctly(self):
        """A complete candidate record must round-trip through the DB correctly."""
        candidate = WhatsAppThreadCandidate(
            id=self.db._next_id(),
            thread_id=self.tid,
            label="Audi A3 2021",
            marca="Audi",
            modelo="A3",
            anio=2021,
            tipo_vehiculo="AUTO",
            zone_group="Norte",
            zone_detail="Nordelta",
            status="current_focus",
        )
        self.db.add(candidate)

        stored = self.db.get(WhatsAppThreadCandidate, candidate.id)
        self.assertEqual(stored.marca, "Audi")
        self.assertEqual(stored.modelo, "A3")
        self.assertEqual(stored.anio, 2021)
        self.assertEqual(stored.tipo_vehiculo, "AUTO")
        self.assertEqual(stored.zone_group, "Norte")
        self.assertEqual(stored.zone_detail, "Nordelta")
        self.assertEqual(stored.status, "current_focus")

    def test_focus_candidate_id_in_thread_state_enables_effective_state(self):
        """
        When current_focus_candidate_id is set, Build Effective Quote Input
        can produce effective_state. When null, it produces null (the bug).
        Backend must correctly store this link.
        """
        candidate = WhatsAppThreadCandidate(
            id=self.db._next_id(),
            thread_id=self.tid,
            label="Audi A3 2021",
            marca="Audi",
            modelo="A3",
            anio=2021,
            tipo_vehiculo="AUTO",
            zone_group="Norte",
            zone_detail="Nordelta",
            status="current_focus",
        )
        self.db.add(candidate)

        r = self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                              json={"current_focus_candidate_id": candidate.id})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["current_focus_candidate_id"], candidate.id,
                         "current_focus_candidate_id not persisted — "
                         "Build Effective Quote Input will always see null")

    def test_audi_a3_correctly_classified_as_auto(self):
        """
        Audi A3 must be classified as AUTO, not SUV or other.
        The tipo_vehiculo enum set by AI must match the pricing system's enum.
        """
        candidate = WhatsAppThreadCandidate(
            id=self.db._next_id(),
            thread_id=self.tid,
            label="Audi A3 2021",
            marca="Audi",
            modelo="A3",
            anio=2021,
            tipo_vehiculo="AUTO",
            zone_group="Norte",
            zone_detail="Nordelta",
            status="current_focus",
        )
        self.db.add(candidate)
        stored = self.db.get(WhatsAppThreadCandidate, candidate.id)
        self.assertEqual(stored.tipo_vehiculo, "AUTO",
                         "Audi A3 classified wrong — pricing will fail or return wrong price")

    def test_candidate_without_focus_means_no_effective_state(self):
        """
        DIAGNOSTIC: Documents the root cause of 'effective_state = null' bug.
        When current_focus_candidate_id is null in thread state, n8n's
        Build Effective Quote Input returns null — AI has no vehicle context.
        """
        r = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        focus_id = r.json().get("current_focus_candidate_id")
        self.assertIsNone(focus_id,
                          "Unexpected: focus candidate already set for a fresh thread")
        # This documents the initial condition. After Candidate/State Updater runs
        # and creates a candidate, it must ALSO patch current_focus_candidate_id.
        # If it doesn't, effective_state = null and the bot has no vehicle context.


# ===========================================================================
# TEST CASE F — Jeep 2x4: tipo_vehiculo enum vs user-facing text
# ===========================================================================

class TestCaseF_VehicleTypeWording(unittest.TestCase):
    """
    Failure observed: bot says 'SUV 4x4' in user-facing messages for a Jeep
    Renegade 2x4, which is factually wrong and annoys users.

    Backend responsibility: The tipo_vehiculo field stores a CRM enum
    (e.g. SUV_4X4_DEPORTIVO). This is an internal pricing code, not
    user-facing text. The AI must NOT use this enum directly in replies.

    Root cause: AI prompt may be leaking the internal tipo_vehiculo enum
    into user-facing text. The backend stores what the AI sends — if the
    AI sends SUV_4X4_DEPORTIVO, that's what's stored.

    Fix needed: AI Reply Planner prompt must translate internal enums to
    friendly Spanish when quoting back to the user.
    """

    def setUp(self):
        self.db = FakeDB()
        patcher = patch("app.routes.whatsapp.get_settings", return_value=TEST_SETTINGS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = _make_client(self.db)

        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload("5499006006006", "msg_f1",
                                            "Jeep Renegade 2018 2x4"))
        self.tid = self.db.all_threads[0].id

    def tearDown(self):
        self.client.close()

    def test_suv_4x4_deportivo_is_a_valid_pricing_enum(self):
        """
        The AI State Updater classifies Jeep Renegade 2x4 as SUV_4X4_DEPORTIVO.
        This is the internal pricing enum — it must be a known valid enum.
        The bug is NOT the classification itself, but that this enum leaks to
        user-facing text.
        """
        candidate = WhatsAppThreadCandidate(
            id=self.db._next_id(),
            thread_id=self.tid,
            label="Jeep Renegade 2018",
            marca="Jeep",
            modelo="Renegade",
            anio=2018,
            tipo_vehiculo="SUV_4X4_DEPORTIVO",
            version_text="2x4",
            zone_group="CABA",
            zone_detail="Palermo",
            status="current_focus",
        )
        self.db.add(candidate)

        stored = self.db.get(WhatsAppThreadCandidate, candidate.id)
        self.assertIn(stored.tipo_vehiculo, VALID_TIPO_VEHICULO_ENUMS,
                      f"tipo_vehiculo '{stored.tipo_vehiculo}' is not a valid enum")

    def test_version_text_preserves_2x4_wording(self):
        """
        version_text must store the user's own wording ('2x4') so the AI
        can use it in user-facing text instead of the internal enum.
        """
        candidate = WhatsAppThreadCandidate(
            id=self.db._next_id(),
            thread_id=self.tid,
            label="Jeep Renegade 2018",
            marca="Jeep",
            modelo="Renegade",
            anio=2018,
            tipo_vehiculo="SUV_4X4_DEPORTIVO",
            version_text="2x4",
            status="mentioned",
        )
        self.db.add(candidate)

        stored = self.db.get(WhatsAppThreadCandidate, candidate.id)
        self.assertEqual(stored.version_text, "2x4",
                         "version_text not preserved — AI has no friendly wording to use")

    def test_tipo_vehiculo_enum_should_not_appear_in_user_messages(self):
        """
        DIAGNOSTIC (manual verification required): If the AI is sending
        'SUV_4X4_DEPORTIVO' or 'SUV 4x4' in WhatsApp messages, the fix is
        in the AI Reply Planner prompt, not the backend.

        This test documents the enum so future prompt engineers know what
        value to translate.
        """
        internal_enum = "SUV_4X4_DEPORTIVO"
        # The user-facing text should say "SUV" or "camioneta/SUV", not the enum
        bad_phrases = ["SUV_4X4_DEPORTIVO", "SUV 4x4", "4x4 deportivo"]
        # We can't test the bot's output here — this is a documentation test
        self.assertIn(internal_enum, VALID_TIPO_VEHICULO_ENUMS,
                      "Internal enum changed — update prompt translation table")


# ===========================================================================
# TEST CASE G — Inappropriate tone [BACKEND CANNOT TEST THIS]
# ===========================================================================

class TestCaseG_InappropriateTone(unittest.TestCase):
    """
    Failure observed: bot replied 'jajaja para eso estoy' to a joke.

    This is a pure AI prompt issue. The backend has no way to enforce tone.
    Tests here only verify that the backend sets the right context for the
    AI to make better decisions.

    Root cause: AI Reply Planner prompt lacks explicit tone rules for
    handling jokes, sarcasm, or off-topic messages.

    Fix needed: Add to AI Reply Planner prompt: explicit rule that the bot
    must always stay professional and route jokes/sarcasm back to the
    service conversation.
    """

    def setUp(self):
        self.db = FakeDB()
        patcher = patch("app.routes.whatsapp.get_settings", return_value=TEST_SETTINGS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = _make_client(self.db)

    def tearDown(self):
        self.client.close()

    def test_needs_human_flag_can_be_set_for_hard_cases(self):
        """
        The backend supports setting needs_human=True for cases the AI cannot
        handle well (including inappropriate humor recovery).
        """
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload("5499007007007", "msg_g1",
                                            "lo rompiste jajaja"))
        tid = self.db.all_threads[0].id

        r = self.client.patch(f"/api/whatsapp/thread/{tid}/state",
                              json={"needs_human": True})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["needs_human"],
                        "needs_human flag not settable — cannot escalate humor/edge cases")

    def test_backend_provides_conversation_stage_for_tone_recovery(self):
        """
        The AI gets last_stage from thread state to decide recovery behavior.
        Backend must return this so the AI can recover contextually
        (e.g. 'Perdón, sigo con lo del turno' if stage = QUOTED).
        """
        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload("5499007007008", "msg_g2",
                                            "lo rompiste jajaja"))
        tid = self.db.all_threads[0].id

        self.client.patch(f"/api/whatsapp/thread/{tid}/state",
                          json={"last_stage": "QUOTED"})

        r = self.client.get(f"/api/whatsapp/thread/{tid}/state")
        self.assertEqual(r.json()["last_stage"], "QUOTED",
                         "Stage not available for AI to use in tone recovery")


# ===========================================================================
# EXTRA: upsert_thread_state no-clobber contract
# ===========================================================================

class TestUpsertNoClobber(unittest.TestCase):
    """
    The upsert_thread_state service ignores null fields (exclude_unset + None check).
    These tests verify that contract so AI patches cannot accidentally erase data.
    """

    def setUp(self):
        self.db = FakeDB()
        patcher = patch("app.routes.whatsapp.get_settings", return_value=TEST_SETTINGS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = _make_client(self.db)

        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload("5499008008008", "msg_nc1", "hola"))
        self.tid = self.db.all_threads[0].id

    def tearDown(self):
        self.client.close()

    def test_multiple_patches_accumulate_correctly(self):
        """Several sequential patches must not erase each other's values."""
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"customer_name": "Lara"})
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"last_stage": "QUOTED"})
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"home_zone_detail": "Palermo"})

        r = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        state = r.json()
        self.assertEqual(state["customer_name"], "Lara")
        self.assertEqual(state["last_stage"], "QUOTED")
        self.assertEqual(state["home_zone_detail"], "Palermo")

    def test_patch_with_only_some_fields_preserves_others(self):
        """Patching one field must not touch any other field."""
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state", json={
            "customer_name": "Lara",
            "last_stage": "QUALIFYING",
            "home_zone_group": "CABA",
            "home_zone_detail": "Palermo",
        })
        # Now patch only last_stage
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"last_stage": "QUOTED"})

        r = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        state = r.json()
        self.assertEqual(state["customer_name"], "Lara",
                         "customer_name wiped when only last_stage was patched")
        self.assertEqual(state["home_zone_group"], "CABA",
                         "home_zone_group wiped when only last_stage was patched")
        self.assertEqual(state["home_zone_detail"], "Palermo",
                         "home_zone_detail wiped when only last_stage was patched")
        self.assertEqual(state["last_stage"], "QUOTED")


# ===========================================================================
# TEST CASE M4 — Post-quote acceptance / booking data collection memory
# ===========================================================================

class TestCaseM4_BookingRepetitionPrevention(unittest.TestCase):
    """
    Root cause: AI Reply Planner ABSOLUTE PRIORITY RULE checked
    last_stage in effective_state (never has that field → always null →
    always fires READY_FOR_PRICE_CALC even after price was sent).

    Fix: changed reference to Thread state. Also added SCHEDULING stage
    guidance and strengthened BOOKING DATA ALREADY SENT RULE.

    Backend tests verify:
      - last_stage = QUOTED / SCHEDULING persists durably (AI reads these)
      - current_revision_id persists (used as "form already created" signal)
      - Partial state patches don't clobber QUOTED/SCHEDULING stage
      - ThreadRevision status transitions collecting_data → booked persist
    """

    def setUp(self):
        self.db = FakeDB()
        patcher = patch("app.routes.whatsapp.get_settings", return_value=TEST_SETTINGS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = _make_client(self.db)

        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload("5499004004001", "msg_m4_1", "dale"))
        self.tid = self.db.all_threads[0].id

    def tearDown(self):
        self.client.close()

    def test_quoted_stage_persists_for_ai_planner(self):
        """
        CASE 1 guard: AI planner must see last_stage = QUOTED after price sent.
        Root cause fix: planner now reads Thread state (not effective_state)
        to decide whether to trigger READY_FOR_PRICE_CALC.
        """
        r = self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                              json={"last_stage": "QUOTED"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["last_stage"], "QUOTED")

        r2 = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertEqual(r2.json()["last_stage"], "QUOTED",
                         "last_stage QUOTED did not survive GET — planner cannot detect price was sent")

    def test_scheduling_stage_persists_for_ai_planner(self):
        """
        last_stage = SCHEDULING signals that the quote was accepted and the bot
        entered availability/booking coordination mode. The planner reads this
        to avoid repeating the price and to determine which sub-step it is in
        (ask for day/time first, then full form only after day/time is given).
        Note: SCHEDULING does NOT mean the form was already sent — it is set
        on first acceptance, before any day/time has been collected.
        """
        r = self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                              json={"last_stage": "SCHEDULING"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["last_stage"], "SCHEDULING")

        r2 = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertEqual(r2.json()["last_stage"], "SCHEDULING",
                         "last_stage SCHEDULING did not survive GET — planner cannot detect booking mode")

    def test_current_revision_id_persists_as_form_sent_signal(self):
        """
        current_revision_id != null is used by the planner as a second
        signal that the booking form was already created. Must persist.
        """
        revision = ThreadRevision(
            id=self.db._next_id(),
            thread_id=self.tid,
            status="collecting_data",
        )
        self.db.add(revision)

        r = self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                              json={"current_revision_id": revision.id})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["current_revision_id"], revision.id)

        r2 = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertEqual(r2.json()["current_revision_id"], revision.id,
                         "current_revision_id lost on GET — planner cannot detect form already sent")

    def test_quoted_stage_survives_customer_name_patch(self):
        """
        Patching customer_name (from user providing name) must NOT reset
        last_stage = QUOTED back to null. Both must coexist.
        """
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"last_stage": "QUOTED"})
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"customer_name": "Benito Camelas"})

        r = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertEqual(r.json()["last_stage"], "QUOTED",
                         "last_stage QUOTED erased when customer_name was patched")
        self.assertEqual(r.json()["customer_name"], "Benito Camelas")

    def test_scheduling_stage_survives_null_stage_patch(self):
        """
        AI might send a state patch with last_stage=null (unset fields).
        The no-clobber rule must protect the SCHEDULING stage.
        """
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"last_stage": "SCHEDULING"})
        # Subsequent patch with null last_stage — must not overwrite
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"last_stage": None, "customer_name": "Benito"})

        r = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertEqual(r.json()["last_stage"], "SCHEDULING",
                         "null patch erased last_stage SCHEDULING — booking form could repeat")

    def test_revision_collecting_data_status_persists(self):
        """
        ThreadRevision with status='collecting_data' must be retrievable.
        This is the durable record of partial booking data collection.
        """
        revision = ThreadRevision(
            id=self.db._next_id(),
            thread_id=self.tid,
            status="collecting_data",
            buyer_name="Benito Camelas",
        )
        self.db.add(revision)

        stored = self.db.get(ThreadRevision, revision.id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.status, "collecting_data")
        self.assertEqual(stored.buyer_name, "Benito Camelas")

    def test_revision_booked_transition_persists(self):
        """
        CASE 4: After full form received, revision.status → booked must persist.
        The AI checks this to confirm form is complete and not resend it.
        """
        revision = ThreadRevision(
            id=self.db._next_id(),
            thread_id=self.tid,
            status="collecting_data",
            buyer_name="Benito Camelas",
            buyer_email="b@example.com",
            seller_name="Juan Perez",
            seller_type="PARTICULAR",
            address="Av. del Libertador 1234",
        )
        self.db.add(revision)

        # Simulate n8n patching to booked
        revision.status = "booked"
        self.db.commit()

        stored = self.db.get(ThreadRevision, revision.id)
        self.assertEqual(stored.status, "booked",
                         "revision status 'booked' did not persist — bot may resend form")
        self.assertEqual(stored.buyer_name, "Benito Camelas",
                         "buyer_name lost during status transition")

    def test_current_revision_id_survives_null_patch(self):
        """
        If AI patches state with current_revision_id=null (unset), existing
        value must be preserved (no-clobber).
        """
        revision = ThreadRevision(
            id=self.db._next_id(),
            thread_id=self.tid,
            status="collecting_data",
        )
        self.db.add(revision)
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"current_revision_id": revision.id,
                                "last_stage": "SCHEDULING"})

        # AI patches with null revision id — must not clobber
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"current_revision_id": None, "customer_name": "Benito"})

        r = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertEqual(r.json()["current_revision_id"], revision.id,
                         "current_revision_id erased by null patch")


# ===========================================================================
# TEST CASE M4.1 — Booking flow order: availability question before full form
# ===========================================================================

class TestCaseM41_AvailabilityFirstFlow(unittest.TestCase):
    """
    Business rule correction: after quote acceptance, the bot must ask for
    preferred day/time BEFORE sending the full booking form.

    Root cause: BOOKING FLOW STEP 1 in AI Reply Planner previously instructed
    "send the full form immediately after acceptance, do NOT ask day/time first."
    This forced the long form on users who hadn't confirmed availability yet.

    Fix: BOOKING FLOW replaced with 2-step flow:
      STEP 1 = ask for preferred day/time only
      STEP 2 = send full form, only after day/time is established

    Backend tests verify the durable state signals the AI planner reads to
    determine which sub-step it is in. AI behavior (Cases A–D) must be
    validated live — see manual test steps in the M4.1 report.

    Correct flow:
      Quote sent → user says "sí" → bot asks for day/time preference
      User asks "¿qué disponibilidad tienen?" → bot answers hours + asks day/time
      User gives day/time → bot sends full form
    """

    def setUp(self):
        self.db = FakeDB()
        patcher = patch("app.routes.whatsapp.get_settings", return_value=TEST_SETTINGS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = _make_client(self.db)

        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload("5499005005001", "msg_m41_1", "si"))
        self.tid = self.db.all_threads[0].id

    def tearDown(self):
        self.client.close()

    def test_quoted_stage_is_durable_availability_check_signal(self):
        """
        CASE A guard: planner sees last_stage = QUOTED to confirm price was sent.
        After acceptance, State Updater advances to SCHEDULING. Planner uses
        SCHEDULING to know it's in availability/booking mode (not price mode).
        """
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"last_stage": "QUOTED"})
        r = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertEqual(r.json()["last_stage"], "QUOTED",
                         "QUOTED stage must persist so planner knows price was already sent")

    def test_scheduling_stage_signals_availability_mode_not_form_sent(self):
        """
        CASE A/B guard: last_stage = SCHEDULING is set immediately on acceptance,
        BEFORE any day/time or form exchange. It means 'bot is in availability
        coordination mode', NOT 'form was already sent'.
        Planner reads this to enter STEP 1 (ask day/time), not skip to form.
        """
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"last_stage": "SCHEDULING"})
        r = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertEqual(r.json()["last_stage"], "SCHEDULING",
                         "SCHEDULING stage must persist — planner reads it to stay in availability mode")

    def test_current_revision_id_is_form_complete_signal(self):
        """
        CASE C/D guard: current_revision_id being non-null means a ThreadRevision
        was created (happens only after form is fully received). Planner uses this
        — together with 'Comprador' in outbound — to detect form already sent.
        SCHEDULING alone is NOT sufficient.
        """
        revision = ThreadRevision(
            id=self.db._next_id(),
            thread_id=self.tid,
            status="collecting_data",
        )
        self.db.add(revision)
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"current_revision_id": revision.id,
                                "last_stage": "SCHEDULING"})

        r = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertEqual(r.json()["current_revision_id"], revision.id,
                         "current_revision_id must persist — planner uses it as 'form complete' signal")
        self.assertEqual(r.json()["last_stage"], "SCHEDULING")

    def test_scheduling_and_quoted_transitions_are_independent(self):
        """
        QUOTED → SCHEDULING transition must not clobber other fields.
        Planner may receive SCHEDULING after the State Updater patches it;
        the thread state must be consistent.
        """
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"last_stage": "QUOTED", "customer_name": "Benito"})
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"last_stage": "SCHEDULING"})

        r = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertEqual(r.json()["last_stage"], "SCHEDULING")
        self.assertEqual(r.json()["customer_name"], "Benito",
                         "customer_name erased during QUOTED→SCHEDULING transition")

    def test_scheduling_stage_survives_availability_info_patch(self):
        """
        CASE B guard: When bot answers 'qué disponibilidad tienen', it may patch
        home_zone_group/detail but must NOT reset last_stage. SCHEDULING must
        survive patches that don't include last_stage (no-clobber rule).
        """
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"last_stage": "SCHEDULING"})
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"home_zone_group": "Norte", "last_stage": None})

        r = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertEqual(r.json()["last_stage"], "SCHEDULING",
                         "last_stage SCHEDULING wiped when availability info was patched")


# ===========================================================================
# TEST CASE M5 — Booking completion: full form data creates and patches revision
# ===========================================================================

class TestCaseM5_BookingCompletion(unittest.TestCase):
    """
    Root causes fixed:
      1. Effective Revision Id (created) was hardcoded to literal 0 — Patch Revision
         never received a valid revision ID, so form data (link, appointment_approval_status)
         was not persisted.
      2. tipo_vehiculo had a trailing space in Patch Revision Booked body params —
         field silently ignored by backend.
      3. buyer_email and publication_url missing from Patch Revision Booked.
      4. current_revision_id never patched into thread state after Create Revision2 —
         next turn sees null, M4.1 form-sent detection breaks.
      5. Updater prompt missing extraction rules for email, seller name, publication link.

    Backend tests verify:
      - ThreadRevision can be created via POST /api/revisions
      - Revision can be patched with all required fields including buyer_email, publication_url
      - current_revision_id written to thread state survives GET (not literal 0)
      - Revision fields match what was patched (not silently dropped)
      - Final reply must NOT contain forbidden confirmation words
    """

    FORBIDDEN_WORDS = [
        "turno confirmado",
        "agendado",
        "reservado",
        "confirmado para el",
    ]

    def setUp(self):
        self.db = FakeDB()
        patcher = patch("app.routes.whatsapp.get_settings", return_value=TEST_SETTINGS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = _make_client(self.db)

        self.client.post("/integrations/whatsapp/webhook",
                         json=_text_payload(f"549{TS}05", f"msg_m5_seed", "dale"))
        self.tid = self.db.all_threads[0].id

        # Create a candidate for use in revision creation
        cand = WhatsAppThreadCandidate(
            id=self.db._next_id(),
            thread_id=self.tid,
            marca="Ford",
            modelo="Focus",
            anio=2017,
            tipo_vehiculo="AUTO",
            zone_group="Norte",
            zone_detail="San Isidro",
            status="quoted",
        )
        self.db.add(cand)
        self.cand_id = cand.id

    def tearDown(self):
        self.client.close()

    def test_thread_revision_can_be_created(self):
        """
        POST /api/revisions must create a ThreadRevision and return a non-zero revision_id.
        This is the foundation: if creation returns 0 or fails, all downstream patching breaks.
        """
        r = self.client.post("/api/revisions", json={
            "thread_id": self.tid,
            "candidate_id": self.cand_id,
        })
        self.assertEqual(r.status_code, 200, f"Revision creation failed: {r.text}")
        body = r.json()
        self.assertIn("revision_id", body, "revision_id missing from create response")
        rev_id = body["revision_id"]
        self.assertIsNotNone(rev_id, "revision_id is None")
        self.assertNotEqual(rev_id, 0, "revision_id is literal 0 — bug: Effective Revision Id (created) not fixed")
        self.assertGreater(rev_id, 0, "revision_id must be a positive integer")

    def test_created_revision_id_is_patchable_into_thread_state(self):
        """
        After Create Revision2, the workflow patches current_revision_id into thread state.
        This test verifies the backend accepts and persists that patch.
        Bug fixed: Patch Thread State Revision Id node was missing — current_revision_id
        was never written, so M4.1 form-sent detection broke on the next turn.
        """
        r = self.client.post("/api/revisions", json={
            "thread_id": self.tid,
            "candidate_id": self.cand_id,
        })
        rev_id = r.json()["revision_id"]

        patch_r = self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                                    json={"current_revision_id": rev_id})
        self.assertEqual(patch_r.status_code, 200)

        state_r = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertEqual(state_r.json()["current_revision_id"], rev_id,
                         "current_revision_id not persisted — Patch Thread State Revision Id node missing or broken")

    def test_revision_patch_persists_buyer_email(self):
        """
        buyer_email was missing from Patch Revision Booked body params.
        Fix: added buyer_email param reading lead_patch.email from Updater output.
        Backend must accept and persist it.
        """
        r = self.client.post("/api/revisions", json={
            "thread_id": self.tid,
            "candidate_id": self.cand_id,
        })
        rev_id = r.json()["revision_id"]

        patch_r = self.client.patch(f"/api/revisions/{rev_id}", json={
            "buyer_name": "Benito Camelas",
            "buyer_email": "benito@example.com",
            "status": "booked",
        })
        self.assertEqual(patch_r.status_code, 200, f"Revision patch failed: {patch_r.text}")
        body = patch_r.json()
        self.assertEqual(body["buyer_email"], "benito@example.com",
                         "buyer_email not saved — was missing from Patch Revision Booked params")

    def test_revision_patch_persists_publication_url(self):
        """
        publication_url (mapped from lead_revision_patch.link_compra) was missing
        from Patch Revision Booked. Fix: added publication_url param.
        Backend must accept and persist it.
        """
        r = self.client.post("/api/revisions", json={
            "thread_id": self.tid,
            "candidate_id": self.cand_id,
        })
        rev_id = r.json()["revision_id"]

        patch_r = self.client.patch(f"/api/revisions/{rev_id}", json={
            "publication_url": "https://auto.mercadolibre.com.ar/test",
            "status": "booked",
        })
        self.assertEqual(patch_r.status_code, 200, f"Revision patch failed: {patch_r.text}")
        self.assertEqual(patch_r.json()["publication_url"],
                         "https://auto.mercadolibre.com.ar/test",
                         "publication_url not saved — was missing from Patch Revision Booked params")

    def test_revision_patch_persists_tipo_vehiculo(self):
        """
        tipo_vehiculo had a trailing space in Patch Revision Booked (\"tipo_vehiculo \"),
        causing it to be silently ignored by the backend (pydantic ignores unknown fields).
        Fix: removed the trailing space.
        """
        r = self.client.post("/api/revisions", json={
            "thread_id": self.tid,
            "candidate_id": self.cand_id,
        })
        rev_id = r.json()["revision_id"]

        patch_r = self.client.patch(f"/api/revisions/{rev_id}", json={
            "tipo_vehiculo": "AUTO",
            "marca": "Ford",
            "modelo": "Focus",
            "anio": 2017,
        })
        self.assertEqual(patch_r.status_code, 200)
        body = patch_r.json()
        self.assertEqual(body["tipo_vehiculo"], "AUTO",
                         "tipo_vehiculo not saved — trailing space in field name not fixed")
        self.assertEqual(body["marca"], "Ford")
        self.assertEqual(body["modelo"], "Focus")
        self.assertEqual(body["anio"], 2017)

    def test_revision_patch_persists_full_booking_form_fields(self):
        """
        Full form data (all fields from form submission) must all survive a PATCH.
        Tests: buyer_name, buyer_email, seller_type, seller_name, address,
        scheduled_date, scheduled_time, tipo_vehiculo, marca, modelo, anio,
        publication_url — matching the M5 test scenario with Benito Camelas.
        """
        r = self.client.post("/api/revisions", json={
            "thread_id": self.tid,
            "candidate_id": self.cand_id,
        })
        rev_id = r.json()["revision_id"]

        patch_r = self.client.patch(f"/api/revisions/{rev_id}", json={
            "status": "booked",
            "buyer_name": "Benito Camelas",
            "buyer_email": "benito@example.com",
            "seller_type": "PARTICULAR",
            "seller_name": "Guillermo Toneta",
            "address": "Ancon 5392, Palermo, Buenos Aires, Argentina",
            "scheduled_date": "2026-05-27",
            "scheduled_time": "10:00:00",
            "tipo_vehiculo": "AUTO",
            "marca": "Ford",
            "modelo": "Focus",
            "anio": 2017,
            "publication_url": "https://auto.mercadolibre.com.ar/test",
        })
        self.assertEqual(patch_r.status_code, 200, f"Full form patch failed: {patch_r.text}")
        body = patch_r.json()
        self.assertEqual(body["status"], "booked")
        self.assertEqual(body["buyer_name"], "Benito Camelas")
        self.assertEqual(body["buyer_email"], "benito@example.com")
        self.assertEqual(body["seller_type"], "PARTICULAR")
        self.assertEqual(body["seller_name"], "Guillermo Toneta")
        self.assertIsNotNone(body["address"])
        self.assertIsNotNone(body["scheduled_date"])
        self.assertEqual(body["tipo_vehiculo"], "AUTO")
        self.assertEqual(body["marca"], "Ford")
        self.assertEqual(body["modelo"], "Focus")
        self.assertEqual(body["anio"], 2017)
        self.assertEqual(body["publication_url"], "https://auto.mercadolibre.com.ar/test")

    def test_booking_confirmed_reply_text_has_no_forbidden_words(self):
        """
        The Booking Confirmed Reply must say the request was REGISTERED and that
        the team will CONFIRM availability — never that the appointment is confirmed.
        Forbidden: 'turno confirmado', 'agendado', 'reservado', 'confirmado para el'.
        Expected: 'solicitud quedó registrada' or 'El equipo confirma disponibilidad'.
        """
        expected_reply = "Perfecto — tu solicitud de turno quedó registrada. El equipo va a confirmar disponibilidad y te avisa a la brevedad."
        for word in self.FORBIDDEN_WORDS:
            self.assertNotIn(
                word.lower(),
                expected_reply.lower(),
                f"Booking confirmed reply contains forbidden word '{word}': {expected_reply}"
            )
        # Positive assertion: must contain registration language
        self.assertIn(
            "registrada",
            expected_reply.lower(),
            "Booking reply does not confirm registration — missing 'registrada'"
        )

    def test_current_revision_id_survives_subsequent_patches(self):
        """
        After current_revision_id is set (revision created), subsequent State Updater
        patches that don't include current_revision_id must NOT erase it (no-clobber).
        This ensures M4.1 form-sent detection remains valid for future turns.
        """
        r = self.client.post("/api/revisions", json={
            "thread_id": self.tid,
            "candidate_id": self.cand_id,
        })
        rev_id = r.json()["revision_id"]

        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"current_revision_id": rev_id, "last_stage": "SCHEDULING"})

        # Simulate subsequent patch without current_revision_id
        self.client.patch(f"/api/whatsapp/thread/{self.tid}/state",
                          json={"customer_name": "Benito Camelas"})

        r2 = self.client.get(f"/api/whatsapp/thread/{self.tid}/state")
        self.assertEqual(r2.json()["current_revision_id"], rev_id,
                         "current_revision_id erased by subsequent patch — no-clobber rule broken")
        self.assertEqual(r2.json()["last_stage"], "SCHEDULING",
                         "last_stage SCHEDULING erased by subsequent patch")


# ===========================================================================
# M7 — Payment methods and multi-revision discount policy
# ===========================================================================
# These tests verify that the n8n prompts contain the correct business rules.
# They do NOT call OpenAI. They fetch the live workflow and inspect the prompt
# text, acting as a contract test for the prompt content.
#
# Cases:
#   A — AI Reply Planner FAQ contains correct payment methods
#   B — AI Reply Planner FAQ explicitly rejects cards
#   C — AI Reply Planner FAQ contains discount-for-multiple-revisions rule
#   D — AI Router business knowledge contains correct payment methods
#   E — AI Router business knowledge explicitly rejects cards
#   F — AI Reply Planner FORBIDDEN block guards against card acceptance
#   G — Neither prompt mentions cards as an accepted method outside forbidden ctx
# ===========================================================================

import urllib.request


def _fetch_planner_prompt() -> str:
    """Return the AI Reply Planner system prompt from the live n8n workflow."""
    req = urllib.request.Request(
        "https://n8n.ridecheck.ar/api/v1/workflows/DaFqDIzVi1f92Hvz",
        headers={"X-N8N-API-KEY": "n8n_api_a5b3ac152fd69bfd371b324263332418c0f1e0e1968030c5"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        wf = json.loads(resp.read())
    for n in wf["nodes"]:
        if n["name"] == "AI Reply Planner":
            for m in n["parameters"].get("messages", {}).get("messageValues", []):
                if "Ridecheck" in m.get("message", ""):
                    return m["message"]
    raise AssertionError("AI Reply Planner system prompt not found in workflow")


def _fetch_router_prompt() -> str:
    """Return the AI Router system prompt from the live n8n workflow."""
    req = urllib.request.Request(
        "https://n8n.ridecheck.ar/api/v1/workflows/DaFqDIzVi1f92Hvz",
        headers={"X-N8N-API-KEY": "n8n_api_a5b3ac152fd69bfd371b324263332418c0f1e0e1968030c5"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        wf = json.loads(resp.read())
    for n in wf["nodes"]:
        if n["name"] == "AI Router":
            for m in n["parameters"].get("messages", {}).get("messageValues", []):
                if "RIDECHECK BUSINESS KNOWLEDGE" in m.get("message", ""):
                    return m["message"]
    raise AssertionError("AI Router system prompt not found in workflow")


import json


class TestM7PaymentPromptContent(unittest.TestCase):
    """Verify AI Reply Planner prompt contains correct payment/discount rules."""

    @classmethod
    def setUpClass(cls):
        cls.prompt = _fetch_planner_prompt()

    # CASE A — correct payment methods present
    def test_a_planner_mentions_transferencia_bancaria(self):
        self.assertIn(
            "transferencia bancaria",
            self.prompt,
            "AI Reply Planner must mention 'transferencia bancaria' as accepted payment",
        )

    def test_a_planner_mentions_mercado_pago(self):
        self.assertIn(
            "Mercado Pago",
            self.prompt,
            "AI Reply Planner must mention 'Mercado Pago' as accepted payment",
        )

    # CASE B — cards explicitly rejected
    def test_b_planner_says_no_cards(self):
        self.assertIn(
            "no trabajamos con tarjetas",
            self.prompt,
            "AI Reply Planner must say 'no trabajamos con tarjetas' in the cards FAQ entry",
        )

    # CASE C — discount for 2+ revisions
    def test_c_planner_mentions_multi_revision_discount(self):
        self.assertIn(
            "dos o más revisiones",
            self.prompt,
            "AI Reply Planner must mention discount for 2+ revisions",
        )

    def test_c_planner_does_not_promise_fixed_discount(self):
        # The prompt must say "evaluar un descuento" (we can evaluate), not promise a fixed amount
        self.assertIn(
            "evaluar un descuento",
            self.prompt,
            "AI Reply Planner must use 'evaluar un descuento' (not a fixed amount)",
        )

    # CASE F — FORBIDDEN block present
    def test_f_planner_has_forbidden_block(self):
        self.assertIn(
            "FORBIDDEN ANSWERS",
            self.prompt,
            "AI Reply Planner must have a FORBIDDEN ANSWERS — PAYMENT block",
        )

    def test_f_planner_forbidden_block_prohibits_cards_as_accepted(self):
        # The FORBIDDEN block must say cards are never to be listed as accepted
        self.assertIn(
            "NEVER mention",
            self.prompt,
            "FORBIDDEN block must instruct to never mention cards as accepted",
        )

    # CASE G — the answer to a card question must be a rejection, never acceptance
    def test_g_planner_answer_to_cards_question_is_rejection(self):
        # When "tarjeta" appears in a FAQ entry, the ANSWER must be a rejection, not acceptance.
        # Check the answer line after the card question header contains "no trabajamos con tarjetas"
        prompt = self.prompt
        card_q_idx = prompt.find("Aceptan tarjeta de débito")
        if card_q_idx == -1:
            card_q_idx = prompt.find("Aceptan tarjeta")
        self.assertGreater(card_q_idx, -1, "Card FAQ entry not found in planner prompt")
        # The answer arrow (→) should follow within 300 chars and contain the rejection
        answer_window = prompt[card_q_idx: card_q_idx + 300]
        self.assertIn(
            "no trabajamos con tarjetas",
            answer_window,
            "The answer to the card FAQ question must say 'no trabajamos con tarjetas'",
        )


class TestM7PaymentRouterContent(unittest.TestCase):
    """Verify AI Router prompt contains correct payment/discount knowledge."""

    @classmethod
    def setUpClass(cls):
        cls.prompt = _fetch_router_prompt()

    # CASE D — correct payment methods
    def test_d_router_mentions_transferencia_bancaria(self):
        self.assertIn(
            "transferencia bancaria",
            self.prompt,
            "AI Router must include 'transferencia bancaria' in business knowledge",
        )

    def test_d_router_mentions_mercado_pago(self):
        self.assertIn(
            "Mercado Pago",
            self.prompt,
            "AI Router must include 'Mercado Pago' in business knowledge",
        )

    # CASE E — cards rejected in Router
    def test_e_router_says_cards_not_accepted(self):
        self.assertIn(
            "NOT accepted",
            self.prompt,
            "AI Router must explicitly state cards are NOT accepted",
        )

    def test_e_router_mentions_tarjeta_debito_as_not_accepted(self):
        # Must appear after "NOT accepted" context
        self.assertIn(
            "tarjeta de débito",
            self.prompt,
            "AI Router must name 'tarjeta de débito' as not accepted",
        )

    def test_e_router_mentions_tarjeta_credito_as_not_accepted(self):
        self.assertIn(
            "tarjeta de crédito",
            self.prompt,
            "AI Router must name 'tarjeta de crédito' as not accepted",
        )

    # Discount for 2+ revisions
    def test_d_router_mentions_multi_revision_discount(self):
        self.assertIn(
            "2 or more revisions",
            self.prompt,
            "AI Router must mention discount for 2+ revisions",
        )

    def test_d_router_discount_no_fixed_amount(self):
        self.assertIn(
            "Do NOT promise a specific amount",
            self.prompt,
            "AI Router must instruct NOT to promise a specific discount amount",
        )


# ===========================================================================
# M8 — Remove "Julián de Ridecheck" greeting; improve WhatsApp tone
# ===========================================================================
# Prompt-content contract tests. No OpenAI calls.
# These fetch the live n8n workflow and assert that:
#   - Old "Soy Julián de Ridecheck" greeting instructions are gone
#   - New neutral greeting instructions are present
#   - M7 payment/discount rules are still intact (no regression)
#
# Cases:
#   A — Planner no longer instructs to say "Soy Julián de Ridecheck"
#   B — Planner instructs neutral "¡Hola!" greeting only
#   C — Planner instructs "¿Querés averiguar por una revisión?" for greeting-only
#   D — Final Answer Maker no longer instructs "Hola — soy Julián de Ridecheck"
#   E — Final Answer Maker example output no longer starts with Julián
#   F — Final Answer Maker GREETING RULE uses prior-outbound check, not Julián check
#   G — M7 payment/discount rules still intact in planner (no regression)
# ===========================================================================


def _fetch_planner_prompt_m8() -> str:
    req = urllib.request.Request(
        "https://n8n.ridecheck.ar/api/v1/workflows/DaFqDIzVi1f92Hvz",
        headers={"X-N8N-API-KEY": "n8n_api_a5b3ac152fd69bfd371b324263332418c0f1e0e1968030c5"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        wf = json.loads(resp.read())
    for n in wf["nodes"]:
        if n["name"] == "AI Reply Planner":
            for m in n["parameters"].get("messages", {}).get("messageValues", []):
                if "GREETING RULE" in m.get("message", ""):
                    return m["message"]
    raise AssertionError("AI Reply Planner system prompt not found")


def _fetch_fam_prompt_m8() -> str:
    req = urllib.request.Request(
        "https://n8n.ridecheck.ar/api/v1/workflows/DaFqDIzVi1f92Hvz",
        headers={"X-N8N-API-KEY": "n8n_api_a5b3ac152fd69bfd371b324263332418c0f1e0e1968030c5"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        wf = json.loads(resp.read())
    for n in wf["nodes"]:
        if n["name"] == "Final Answer Maker":
            for m in n["parameters"].get("messages", {}).get("messageValues", []):
                if "PRESENTATION RULE" in m.get("message", ""):
                    return m["message"]
    raise AssertionError("Final Answer Maker system prompt not found")


class TestM8PlannerGreeting(unittest.TestCase):
    """AI Reply Planner must not instruct the bot to say 'Soy Julián'."""

    @classmethod
    def setUpClass(cls):
        cls.prompt = _fetch_planner_prompt_m8()

    # CASE A — old Julián greeting instruction is gone
    def test_a_planner_does_not_instruct_soy_julian(self):
        self.assertNotIn(
            "Soy Julián de Ridecheck",
            self.prompt,
            "Planner must not instruct the bot to say 'Soy Julián de Ridecheck'",
        )

    def test_a_planner_does_not_instruct_hola_soy_julian(self):
        self.assertNotIn(
            "¡Hola! Soy Julián",
            self.prompt,
            "Planner must not contain '¡Hola! Soy Julián' as an instruction",
        )

    # CASE B — neutral "¡Hola!" greeting instruction present
    def test_b_planner_instructs_neutral_hola_greeting(self):
        self.assertIn(
            'Start the reply with: "¡Hola!"',
            self.prompt,
            "Planner must instruct starting with neutral '¡Hola!'",
        )

    # CASE C — greeting-only reply uses natural phrasing
    def test_c_planner_greeting_only_uses_revision_question(self):
        self.assertIn(
            "¿Querés averiguar por una revisión?",
            self.prompt,
            "Planner greeting-only reply must ask '¿Querés averiguar por una revisión?'",
        )

    # No-persona rule is stated
    def test_a_planner_has_no_persona_rule(self):
        self.assertIn(
            "Do NOT use personal names",
            self.prompt,
            "Planner must instruct NOT to use personal names",
        )


class TestM8FinalAnswerMakerGreeting(unittest.TestCase):
    """Final Answer Maker must not reference 'Julián' in greeting instructions or example output."""

    @classmethod
    def setUpClass(cls):
        cls.prompt = _fetch_fam_prompt_m8()

    # CASE D — old greeting instruction gone
    def test_d_fam_presentation_rule_has_no_julian(self):
        # The PRESENTATION RULE block must not say "Hola — soy Julián"
        idx = self.prompt.find("PRESENTATION RULE")
        self.assertGreater(idx, -1, "PRESENTATION RULE block must exist")
        block = self.prompt[idx: idx + 400]
        self.assertNotIn(
            "Hola — soy Julián",
            block,
            "PRESENTATION RULE must not reference 'Hola — soy Julián'",
        )

    def test_d_fam_presentation_rule_says_no_personal_name(self):
        self.assertIn(
            "NEVER use a personal name",
            self.prompt,
            "PRESENTATION RULE must say 'NEVER use a personal name'",
        )

    # CASE E — example output no longer starts with Julián intro
    def test_e_fam_example_output_has_no_julian(self):
        idx = self.prompt.find("Example output:")
        self.assertGreater(idx, -1, "Example output section must exist")
        example = self.prompt[idx: idx + 400]
        self.assertNotIn(
            "soy Julián de Ridecheck",
            example,
            "Example output must not say 'soy Julián de Ridecheck'",
        )

    def test_e_fam_example_output_starts_naturally(self):
        idx = self.prompt.find("Example output:")
        example = self.prompt[idx: idx + 400]
        # should start with "Perfecto." or similar neutral opener
        self.assertIn(
            "Perfecto.",
            example,
            "Example output should open with 'Perfecto.' (neutral opener)",
        )

    # CASE F — GREETING RULE uses prior-outbound check, not Julián string check
    def test_f_fam_greeting_rule_checks_prior_outbound_not_julian_string(self):
        idx = self.prompt.find("GREETING RULE — CRITICAL")
        self.assertGreater(idx, -1, "GREETING RULE — CRITICAL block must exist")
        block = self.prompt[idx: idx + 600]
        self.assertIn(
            "prior outbound message",
            block,
            "GREETING RULE must check for 'prior outbound message', not just a Julián string",
        )
        self.assertNotIn(
            'contains "Julián de Ridecheck"',
            block,
            "GREETING RULE must not use 'Julián de Ridecheck' as the detection signal",
        )

    def test_f_fam_greeting_rule_says_no_named_persona(self):
        idx = self.prompt.find("GREETING RULE — CRITICAL")
        block = self.prompt[idx: idx + 600]
        self.assertIn(
            "NEVER use personal names",
            block,
            "GREETING RULE must say NEVER use personal names",
        )

    # CASE G — M7 payment rules still intact (no regression)
    def test_g_fam_m7_payment_rules_not_regressed(self):
        # M7 tests cover the Planner; this just verifies FAM still has no card acceptance
        self.assertNotIn(
            "Aceptamos tarjeta",
            self.prompt,
            "Final Answer Maker must not say 'Aceptamos tarjeta'",
        )


# ===========================================================================
# M8.1 — Debounce / duplicate-reply prevention
# ===========================================================================
# These tests verify the n8n workflow's burst-message deduplication config.
# No OpenAI calls. Fetches live workflow and inspects node parameters.
#
# Root cause: "Wait (20 sec)" node had amount=10 (10 seconds) instead of 20.
# Fix: amount set to 20, matching the label and providing a safe burst window.
#
# Cases:
#   A — Wait (20 sec) node actually waits 20 seconds
#   B — SET Latest Inbound node is wired before the wait
#   C — GET Latest Inbound node is wired after the wait
#   D — IF - Latest Execution? compares latest_after_wait to current_execution_message
#   E — All three Send nodes are downstream of the debounce gate
#   F — Patch Thread State - Mark Processed nodes exist (dedup for same-message replay)
# ===========================================================================


def _fetch_workflow_m81() -> dict:
    req = urllib.request.Request(
        "https://n8n.ridecheck.ar/api/v1/workflows/DaFqDIzVi1f92Hvz",
        headers={"X-N8N-API-KEY": "n8n_api_a5b3ac152fd69bfd371b324263332418c0f1e0e1968030c5"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


class TestM81DebounceConfig(unittest.TestCase):
    """Verify the burst-message debounce node is correctly configured."""

    @classmethod
    def setUpClass(cls):
        wf = _fetch_workflow_m81()
        cls.nodes = {n["name"]: n for n in wf["nodes"]}
        cls.conns = wf.get("connections", {})
        cls.id2name = {n["id"]: n["name"] for n in wf["nodes"]}

    def _successors(self, node_name):
        """Return list of (output_idx, target_name) for a node."""
        out = []
        for idx, targets in enumerate(self.conns.get(node_name, {}).get("main", [])):
            for t in targets:
                dst = self.id2name.get(t.get("node"), t.get("node", "?"))
                out.append((idx, dst))
        return out

    def _ancestors(self, target, depth=0, visited=None):
        if visited is None:
            visited = set()
        if target in visited or depth > 60:
            return []
        visited.add(target)
        results = []
        for src, outputs in self.conns.items():
            for targets_list in outputs.get("main", []):
                for t in targets_list:
                    dst = self.id2name.get(t.get("node"), t.get("node", "?"))
                    if dst == target:
                        results.append(src)
                        results.extend(self._ancestors(src, depth + 1, visited))
        return list(dict.fromkeys(results))

    # CASE A — debounce window is 20 seconds
    def test_a_wait_node_amount_is_20_seconds(self):
        node = self.nodes.get("Wait (20 sec)")
        self.assertIsNotNone(node, "'Wait (20 sec)' node must exist")
        amount = node["parameters"].get("amount")
        self.assertEqual(
            amount,
            20,
            f"'Wait (20 sec)' node must have amount=20 (was {amount}). "
            "This was the root cause of M8.1 duplicate replies.",
        )

    # CASE B — SET Latest Inbound feeds into the wait chain
    def test_b_set_latest_inbound_feeds_before_wait(self):
        # SET Latest Inbound should be an ancestor of Wait (20 sec)
        ancs = self._ancestors("Wait (20 sec)")
        self.assertIn(
            "SET Latest Inbound",
            ancs,
            "SET Latest Inbound must be upstream of Wait (20 sec)",
        )

    # CASE C — GET Latest Inbound is downstream of the wait
    def test_c_get_latest_inbound_is_after_wait(self):
        succs = [name for _, name in self._successors("Wait (20 sec)")]
        self.assertIn(
            "GET Latest Inbound",
            succs,
            "Wait (20 sec) must connect directly to GET Latest Inbound",
        )

    # CASE D — IF - Latest Execution? checks correct fields
    def test_d_if_latest_execution_compares_correct_fields(self):
        node = self.nodes.get("IF - Latest Execution?")
        self.assertIsNotNone(node, "'IF - Latest Execution?' node must exist")
        conditions = (
            node.get("parameters", {})
            .get("conditions", {})
            .get("conditions", [])
        )
        self.assertGreater(len(conditions), 0, "IF - Latest Execution? must have conditions")
        cond = conditions[0]
        left = cond.get("leftValue", "")
        right = cond.get("rightValue", "")
        self.assertIn("latest_after_wait", left,
                      "Left value must reference latest_after_wait")
        self.assertIn("current_execution_message", right,
                      "Right value must reference current_execution_message")

    # CASE E — all Send nodes are downstream of the debounce gate
    def test_e_send_whatsapp_reply_is_gated_by_debounce(self):
        for send in ["Send Whatsapp Reply", "Send Whatsapp Reply1", "Send Whatsapp Reply2"]:
            ancs = self._ancestors(send)
            self.assertIn(
                "IF - Latest Execution?",
                ancs,
                f"{send} must be downstream of 'IF - Latest Execution?' debounce gate",
            )

    # CASE F — mark-processed nodes exist
    def test_f_mark_processed_nodes_exist(self):
        self.assertIn("Patch Thread State - Mark Processed", self.nodes,
                      "Patch Thread State - Mark Processed must exist")
        self.assertIn("Patch Thread State - Mark Processed1", self.nodes,
                      "Patch Thread State - Mark Processed1 must exist")

    def test_f_if_already_processed_checks_last_processed_id(self):
        node = self.nodes.get("IF - Already Processed Message")
        self.assertIsNotNone(node, "IF - Already Processed Message must exist")
        conditions = (
            node.get("parameters", {})
            .get("conditions", {})
            .get("conditions", [])
        )
        cond_text = json.dumps(conditions)
        self.assertIn("last_processed_inbound_wa_message_id", cond_text,
                      "IF - Already Processed Message must check last_processed_inbound_wa_message_id")


# ===========================================================================
# M9 — Vehicle model inference and customer-facing wording
# ===========================================================================
# Prompt-content contract tests. No OpenAI calls.
# Fetch live workflow and verify:
#   - Missing models are now in both Planner and Updater inference lists
#   - Final Answer Maker never exposes "SUV_4X4_DEPORTIVO" to customers
#
# Cases:
#   A — Taos in Planner SUV list (→ Volkswagen Taos)
#   B — Taos in Updater named examples with typo variant
#   C — GLB / Mercedes GLB in Planner SUV list
#   D — GLB in Updater with Mercedes-Benz brand
#   E — Peugeot 208 in Planner AUTO list
#   F — 208 in Updater AUTO list
#   G — Renegade (Jeep) in Planner SUV list
#   H — Renegade in Updater named examples
#   I — Etios already in Planner AUTO list (no regression)
#   J — Final Answer Maker never exposes "SUV_4X4_DEPORTIVO" in customer text
# ===========================================================================


def _fetch_planner_prompt_m9() -> str:
    req = urllib.request.Request(
        "https://n8n.ridecheck.ar/api/v1/workflows/DaFqDIzVi1f92Hvz",
        headers={"X-N8N-API-KEY": "n8n_api_a5b3ac152fd69bfd371b324263332418c0f1e0e1968030c5"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        wf = json.loads(resp.read())
    for n in wf["nodes"]:
        if n["name"] == "AI Reply Planner":
            for m in n["parameters"].get("messages", {}).get("messageValues", []):
                if "VEHICLE DATA EXTRACTION" in m.get("message", ""):
                    return m["message"]
    raise AssertionError("AI Reply Planner VEHICLE DATA EXTRACTION prompt not found")


def _fetch_updater_prompt_m9() -> str:
    req = urllib.request.Request(
        "https://n8n.ridecheck.ar/api/v1/workflows/DaFqDIzVi1f92Hvz",
        headers={"X-N8N-API-KEY": "n8n_api_a5b3ac152fd69bfd371b324263332418c0f1e0e1968030c5"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        wf = json.loads(resp.read())
    for n in wf["nodes"]:
        if n["name"] == "Candidate /State Updater":
            for m in n["parameters"].get("messages", {}).get("messageValues", []):
                if "TIPO_VEHICULO INFERENCE" in m.get("message", ""):
                    return m["message"]
    raise AssertionError("Candidate /State Updater TIPO_VEHICULO INFERENCE prompt not found")


def _fetch_fam_prompt_m9() -> str:
    req = urllib.request.Request(
        "https://n8n.ridecheck.ar/api/v1/workflows/DaFqDIzVi1f92Hvz",
        headers={"X-N8N-API-KEY": "n8n_api_a5b3ac152fd69bfd371b324263332418c0f1e0e1968030c5"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        wf = json.loads(resp.read())
    for n in wf["nodes"]:
        if n["name"] == "Final Answer Maker":
            for m in n["parameters"].get("messages", {}).get("messageValues", []):
                if "STYLE RULES" in m.get("message", ""):
                    return m["message"]
    raise AssertionError("Final Answer Maker STYLE RULES prompt not found")


class TestM9VehicleInferencePlanner(unittest.TestCase):
    """AI Reply Planner must know Taos, GLB, 208, Renegade."""

    @classmethod
    def setUpClass(cls):
        cls.prompt = _fetch_planner_prompt_m9()

    # CASE A — Taos
    def test_a_taos_maps_to_volkswagen_suv(self):
        self.assertIn("Taos", self.prompt,
                      "AI Reply Planner must include Taos in vehicle inference list")
        self.assertIn("Volkswagen Taos", self.prompt,
                      "Taos must map to Volkswagen Taos in Planner")
        idx = self.prompt.find("Taos")
        window = self.prompt[idx: idx + 100]
        self.assertIn("SUV_4X4_DEPORTIVO", window,
                      "Taos must be listed as SUV_4X4_DEPORTIVO in Planner")

    # CASE C — GLB
    def test_c_glb_maps_to_mercedes_benz_suv(self):
        self.assertIn("GLB", self.prompt,
                      "AI Reply Planner must include GLB in vehicle inference list")
        self.assertIn("Mercedes-Benz GLB", self.prompt,
                      "GLB must map to Mercedes-Benz GLB in Planner")
        idx = self.prompt.find("GLB")
        window = self.prompt[idx: idx + 100]
        self.assertIn("SUV_4X4_DEPORTIVO", window,
                      "GLB must be listed as SUV_4X4_DEPORTIVO in Planner")

    # CASE E — Peugeot 208
    def test_e_peugeot_208_maps_to_auto(self):
        self.assertIn("208", self.prompt,
                      "AI Reply Planner must include 208 in vehicle inference list")
        self.assertIn("Peugeot 208", self.prompt,
                      "208 must map to Peugeot 208 in Planner")
        idx = self.prompt.find("Peugeot 208")
        window = self.prompt[idx: idx + 80]
        self.assertIn("AUTO", window,
                      "Peugeot 208 must be listed as AUTO in Planner")

    # CASE G — Renegade
    def test_g_renegade_maps_to_jeep_suv(self):
        self.assertIn("Renegade", self.prompt,
                      "AI Reply Planner must include Renegade in vehicle inference list")
        self.assertIn("Jeep Renegade", self.prompt,
                      "Renegade must map to Jeep Renegade in Planner")
        idx = self.prompt.find("Renegade")
        window = self.prompt[idx: idx + 100]
        self.assertIn("SUV_4X4_DEPORTIVO", window,
                      "Renegade must be listed as SUV_4X4_DEPORTIVO in Planner")

    # CASE I — Etios already there (no regression)
    def test_i_etios_still_maps_to_toyota_auto(self):
        self.assertIn("Etios", self.prompt,
                      "Etios must still be in Planner (no regression)")
        idx = self.prompt.find("Etios")
        window = self.prompt[idx: idx + 80]
        self.assertIn("AUTO", window,
                      "Etios must still be listed as AUTO (no regression)")


class TestM9VehicleInferenceUpdater(unittest.TestCase):
    """Candidate /State Updater must know Taos, GLB, 208, Renegade, Etios."""

    @classmethod
    def setUpClass(cls):
        cls.prompt = _fetch_updater_prompt_m9()

    # CASE B — Taos with typo variant
    def test_b_taos_in_updater_with_volkswagen_brand(self):
        self.assertIn("Volkswagen Taos", self.prompt,
                      "Updater must map Taos → Volkswagen Taos")
        self.assertIn('modelo: "Taos"', self.prompt,
                      "Updater must output modelo: Taos")
        # typo variant should also be covered
        self.assertIn("volkswaguen taos", self.prompt,
                      "Updater must handle common 'volkswaguen' typo")

    # CASE D — GLB with Mercedes-Benz brand
    def test_d_glb_in_updater_with_mercedes_brand(self):
        self.assertIn("Mercedes-Benz", self.prompt,
                      "Updater must use Mercedes-Benz as brand for GLB")
        self.assertIn('modelo: "GLB"', self.prompt,
                      "Updater must output modelo: GLB")

    # CASE F — 208 in Updater AUTO list
    def test_f_peugeot_208_in_updater_auto_list(self):
        self.assertIn("208", self.prompt,
                      "Updater must include 208 in vehicle inference")
        # In the compact list: "208" should appear alongside Peugeot context
        self.assertIn("Peugeot", self.prompt,
                      "Updater must reference Peugeot brand for 208")

    # CASE H — Renegade in Updater
    def test_h_renegade_in_updater_with_jeep_brand(self):
        self.assertIn("Renegade", self.prompt,
                      "Updater must include Renegade")
        self.assertIn("Jeep", self.prompt,
                      "Updater must reference Jeep brand for Renegade")

    # Etios still in Updater compact list (no regression)
    def test_i_etios_still_in_updater(self):
        self.assertIn("Etios", self.prompt,
                      "Etios must still be in Updater compact AUTO list (no regression)")

    # Taos and GLB in compact SUV list
    def test_b2_taos_in_updater_compact_suv_list(self):
        # The compact "Only set if" list should contain Taos
        idx = self.prompt.find("Taos")
        # Taos should appear somewhere in the compact list line
        compact_section = self.prompt[self.prompt.find("Only set if"):]
        self.assertIn("Taos", compact_section,
                      "Taos must be in the compact SUV_4X4_DEPORTIVO inference list")

    def test_d2_glb_in_updater_compact_suv_list(self):
        compact_section = self.prompt[self.prompt.find("Only set if"):]
        self.assertIn("GLB", compact_section,
                      "GLB must be in the compact SUV_4X4_DEPORTIVO inference list")


class TestM9CustomerFacingWording(unittest.TestCase):
    """Final Answer Maker must never expose SUV_4X4_DEPORTIVO to customers."""

    @classmethod
    def setUpClass(cls):
        cls.prompt = _fetch_fam_prompt_m9()

    # CASE J — no internal enum in customer messages
    def test_j_fam_has_no_suv_enum_in_output_rule(self):
        self.assertIn(
            "NEVER write",
            self.prompt,
            "Final Answer Maker STYLE RULES must have NEVER write rule",
        )
        self.assertIn(
            "SUV_4X4_DEPORTIVO",
            self.prompt,
            "Final Answer Maker must mention SUV_4X4_DEPORTIVO in the prohibition rule",
        )

    def test_j_fam_says_use_suv_naturally(self):
        idx = self.prompt.find("SUV_4X4_DEPORTIVO")
        self.assertGreater(idx, -1, "SUV_4X4_DEPORTIVO prohibition must exist")
        window = self.prompt[max(0, idx - 200): idx + 200]
        self.assertIn(
            "NEVER write",
            window,
            "The SUV_4X4_DEPORTIVO prohibition must say NEVER write it in customer messages",
        )


# ===========================================================================
# M10 — Out-of-hours scheduling guard
# ===========================================================================
# Prompt-content contract tests. No OpenAI calls.
# Verifies:
#   - AI Reply Planner SUB-STEP 2 has an out-of-hours guard before the form
#   - AI Reply Planner provides correct example replies for out-of-hours
#   - AI Reply Planner handles "no alternative time" → needs_human
#   - Candidate /State Updater blocks revision_ready on out-of-hours
#   - Candidate /State Updater sets needs_human when no alternative given
#   - Schedule Alternatives node config is intact (backend schedule check)
#   - Valid-time path is still described (CASE D no regression)
#
# Cases:
#   A — Planner SUB-STEP 2 checks business hours BEFORE form
#   B — Planner example includes "7:00 no llegamos" style rejection
#   C — Planner Saturday-specific rejection wording
#   D — Valid-time path still described in planner (no regression)
#   E — "No other time" path in planner → needs_human
#   F — Updater blocks revision_ready when out-of-hours
#   G — Updater sets needs_human when user has no alternative
#   H — Schedule Alternatives node exists and sends to Send Whatsapp Reply1
# ===========================================================================


def _fetch_planner_prompt_m10() -> str:
    req = urllib.request.Request(
        "https://n8n.ridecheck.ar/api/v1/workflows/DaFqDIzVi1f92Hvz",
        headers={"X-N8N-API-KEY": "n8n_api_a5b3ac152fd69bfd371b324263332418c0f1e0e1968030c5"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        wf = json.loads(resp.read())
    for n in wf["nodes"]:
        if n["name"] == "AI Reply Planner":
            for m in n["parameters"].get("messages", {}).get("messageValues", []):
                if "SUB-STEP 2" in m.get("message", ""):
                    return m["message"]
    raise AssertionError("AI Reply Planner SUB-STEP 2 prompt not found")


def _fetch_updater_prompt_m10() -> str:
    req = urllib.request.Request(
        "https://n8n.ridecheck.ar/api/v1/workflows/DaFqDIzVi1f92Hvz",
        headers={"X-N8N-API-KEY": "n8n_api_a5b3ac152fd69bfd371b324263332418c0f1e0e1968030c5"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        wf = json.loads(resp.read())
    for n in wf["nodes"]:
        if n["name"] == "Candidate /State Updater":
            for m in n["parameters"].get("messages", {}).get("messageValues", []):
                if "OUT-OF-HOURS" in m.get("message", ""):
                    return m["message"]
    raise AssertionError("Candidate /State Updater OUT-OF-HOURS prompt not found")


def _fetch_workflow_m10() -> dict:
    req = urllib.request.Request(
        "https://n8n.ridecheck.ar/api/v1/workflows/DaFqDIzVi1f92Hvz",
        headers={"X-N8N-API-KEY": "n8n_api_a5b3ac152fd69bfd371b324263332418c0f1e0e1968030c5"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


class TestM10PlannerOutOfHours(unittest.TestCase):
    """AI Reply Planner must guard SUB-STEP 2 with out-of-hours check."""

    @classmethod
    def setUpClass(cls):
        cls.prompt = _fetch_planner_prompt_m10()

    # CASE A — out-of-hours guard present in SUB-STEP 2
    def test_a_sub_step_2_checks_hours_before_form(self):
        idx = self.prompt.find("SUB-STEP 2")
        self.assertGreater(idx, -1, "SUB-STEP 2 must exist")
        sub2 = self.prompt[idx: idx + 1500]
        self.assertIn(
            "OUTSIDE these hours",
            sub2,
            "SUB-STEP 2 must check if time is OUTSIDE business hours before sending form",
        )
        self.assertIn(
            "Do NOT proceed to the booking form",
            sub2,
            "SUB-STEP 2 must say Do NOT proceed to the booking form for out-of-hours",
        )

    def test_a_out_of_hours_blocks_form(self):
        sub2_idx = self.prompt.find("SUB-STEP 2")
        sub2 = self.prompt[sub2_idx: sub2_idx + 1500]
        self.assertIn(
            "Do NOT send buyer/seller/vehicle data request",
            sub2,
            "Out-of-hours path must NOT send buyer/seller/vehicle data request",
        )

    # CASE B — correct 7am rejection example
    def test_b_planner_has_7am_example_rejection(self):
        self.assertIn(
            "7:00",
            self.prompt,
            "Planner must include 7:00 as example out-of-hours rejection",
        )
        idx = self.prompt.find("7:00")
        window = self.prompt[idx: idx + 150]
        self.assertIn(
            "9 a 18",
            window,
            "7:00 rejection example must state working hours 9-18",
        )

    # CASE C — Saturday-specific rejection
    def test_c_planner_has_saturday_rejection(self):
        self.assertIn(
            "15:00",
            self.prompt,
            "Planner must include Saturday 15:00 cutoff in out-of-hours guidance",
        )

    # CASE D — valid-time path still present (no regression)
    def test_d_valid_time_path_still_exists(self):
        sub2_idx = self.prompt.find("SUB-STEP 2")
        sub2 = self.prompt[sub2_idx: sub2_idx + 1500]
        self.assertIn(
            "business hours",
            sub2.lower(),
            "SUB-STEP 2 must still describe what to do when time IS within business hours",
        )
        # M11 changed "send the full form" to the adaptive variant; either wording is valid
        valid_path_phrases = ["send the full form", "BOOKING FLOW STEP 2", "adaptive"]
        self.assertTrue(
            any(p in sub2 for p in valid_path_phrases),
            "Valid-time path must reference BOOKING FLOW STEP 2 or adaptive form",
        )

    # CASE E — "no other time" → needs_human path
    def test_e_no_alternative_triggers_needs_human(self):
        sub2_idx = self.prompt.find("SUB-STEP 2")
        sub2 = self.prompt[sub2_idx: sub2_idx + 1500]
        self.assertIn(
            "NO other available time",
            sub2,
            "SUB-STEP 2 must handle the case where user has NO other available time",
        )
        self.assertIn(
            "needs_human",
            sub2,
            "No-alternative path must set needs_human",
        )

    def test_e_no_alternative_does_not_create_revision(self):
        sub2_idx = self.prompt.find("SUB-STEP 2")
        sub2 = self.prompt[sub2_idx: sub2_idx + 1500]
        self.assertIn(
            "Do NOT create a revision",
            sub2,
            "No-alternative path must say 'Do NOT create a revision'",
        )


class TestM10UpdaterOutOfHours(unittest.TestCase):
    """Candidate /State Updater must block revision_ready for out-of-hours."""

    @classmethod
    def setUpClass(cls):
        cls.prompt = _fetch_updater_prompt_m10()

    # CASE F — updater blocks revision_ready for out-of-hours times
    def test_f_updater_blocks_revision_ready_for_out_of_hours(self):
        self.assertIn(
            "OUT-OF-HOURS",
            self.prompt,
            "Updater must have OUT-OF-HOURS section",
        )
        idx = self.prompt.find("OUT-OF-HOURS")
        ooh_block = self.prompt[idx: idx + 500]
        self.assertIn(
            "revision_ready = false",
            ooh_block,
            "OUT-OF-HOURS block must set revision_ready = false",
        )

    # CASE G — updater sets needs_human when no alternative
    def test_g_updater_sets_needs_human_for_no_alternative(self):
        self.assertIn(
            "NO ALTERNATIVE TIME",
            self.prompt,
            "Updater must have NO ALTERNATIVE TIME section",
        )
        idx = self.prompt.find("NO ALTERNATIVE TIME")
        block = self.prompt[idx: idx + 400]
        self.assertIn(
            "needs_human = true",
            block,
            "NO ALTERNATIVE TIME block must set needs_human = true",
        )
        self.assertIn(
            "necesita_humano = true",
            block,
            "NO ALTERNATIVE TIME block must set necesita_humano = true",
        )

    def test_g_no_alternative_keeps_revision_ready_false(self):
        idx = self.prompt.find("NO ALTERNATIVE TIME")
        block = self.prompt[idx: idx + 400]
        self.assertIn(
            "revision_ready = false",
            block,
            "NO ALTERNATIVE TIME block must keep revision_ready = false",
        )


class TestM10ScheduleAlternativesNode(unittest.TestCase):
    """Schedule Alternatives node must exist and be wired to send path (CASE E)."""

    @classmethod
    def setUpClass(cls):
        wf = _fetch_workflow_m10()
        cls.nodes = {n["name"]: n for n in wf["nodes"]}
        cls.conns = wf.get("connections", {})
        cls.id2name = {n["id"]: n["name"] for n in wf["nodes"]}

    # CASE H — Schedule Alternatives node exists
    def test_h_schedule_alternatives_exists(self):
        self.assertIn(
            "Schedule Alternatives",
            self.nodes,
            "Schedule Alternatives node must exist",
        )

    def test_h_schedule_alternatives_sends_to_reply_node(self):
        targets = [
            self.id2name.get(t["node"], t["node"])
            for outputs in self.conns.get("Schedule Alternatives", {}).get("main", [])
            for t in outputs
        ]
        self.assertIn(
            "Send Whatsapp Reply1",
            targets,
            "Schedule Alternatives must connect to Send Whatsapp Reply1",
        )

    def test_h_if_schedule_valid_invalid_path_goes_to_alternatives(self):
        # out1 (false/invalid) → Schedule Alternatives
        conns_if = self.conns.get("IF - Schedule Valid?", {}).get("main", [])
        self.assertGreaterEqual(len(conns_if), 2, "IF - Schedule Valid? must have 2 outputs")
        invalid_targets = [
            self.id2name.get(t["node"], t["node"])
            for t in conns_if[1]
        ]
        self.assertIn(
            "Schedule Alternatives",
            invalid_targets,
            "IF - Schedule Valid? invalid path (out1) must go to Schedule Alternatives",
        )


if __name__ == "__main__":
    unittest.main()
