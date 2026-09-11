"""L4.7W5-F7A — a Flow being open must not make the customer inaudible.

Live Wild. Three valid Saturday slots were offered through the Booking Flow. The customer
answered "no me sirve mañana me lo venden" — rejection and commercial urgency — and nothing
happened. Every signal had fired:

    _earliest_option_rejected -> True
    _urgency_signalled        -> True
    "no me sirve"             -> in _ESCALATION_KEYWORDS

None could be READ. The deterministic scheduling block, which contains the rescue branches,
was gated on `not state.flow_booking_token`, and the Flow dispatched 37 seconds earlier had
set that token. **The offer made the rejection of the offer unreachable.**

The token is technical lifecycle state — it stops a duplicate Flow and binds a submission to
its offer. It is not evidence about what the customer wants. So rescue consumption is now
token-independent while Flow dispatch stays guarded.

Every test here enters through `ConversationEngine.handle()`, the boundary n8n calls.
Driving the edited helper would prove nothing: the helper was already correct.

RESCUE-01..07   rejection / urgency / human request / cancellation with a token set
SAFE-01..08     no false rescue, no duplicate side effects, booking authority intact
"""
from __future__ import annotations

import ast
import pathlib
import sys
import types
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
for _m in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    sys.modules.setdefault(_m, types.ModuleType(_m))

import sqlalchemy as _sa
import sqlalchemy.dialects.postgresql as _pg
import sqlalchemy.dialects.postgresql.json as _pgj
_pg.JSONB = _sa.JSON
_pgj.JSONB = _sa.JSON

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

import app.models
from app.models import (Lead, Revision, ThreadRevision, ViaticosZone, WhatsAppContact,
                        WhatsAppMessage, WhatsAppThread, WhatsAppThreadCandidate,
                        WhatsAppThreadState)
from app.schemas.conversation import ConversationHandleIn
from app.services.conversation_engine import ConversationEngine

CE_SOURCE = (ROOT / "backend" / "app" / "services"
             / "conversation_engine.py").read_text(encoding="utf-8-sig")

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})


@event.listens_for(_engine, "connect")
def _pragmas(conn, _rec):
    conn.execute("PRAGMA foreign_keys=OFF")


app.models.Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

WA_ID = "5491100000001"
OFFERED_DAY = (date.today() + timedelta(days=1)).isoformat()


def _fn(name: str) -> str:
    return next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                if isinstance(n, ast.FunctionDef) and n.name == name)


class _LiveTurn(unittest.TestCase):
    """The state the live conversation actually held when the rejection arrived."""

    def setUp(self):
        with _engine.begin() as conn:
            for tbl in reversed(app.models.Base.metadata.sorted_tables):
                conn.execute(tbl.delete())
        self.db = _Session()
        self.db.add(ViaticosZone(zone_group="CABA", zone_detail="La Paternal", viaticos=0))

        contact = WhatsAppContact(wa_id=WA_ID, display_name="Tester")
        self.db.add(contact); self.db.flush()
        self.lead = Lead(estado="CONSULTA_NUEVA", flag="ACEPTADO", necesita_humano=False)
        self.db.add(self.lead); self.db.flush()
        self.thread = WhatsAppThread(contact_id=contact.id, lead_id=self.lead.id)
        self.db.add(self.thread); self.db.flush()
        self.cand = WhatsAppThreadCandidate(
            thread_id=self.thread.id, marca="Renault", modelo="Sandero", anio=2020,
            tipo_vehiculo="AUTO", zone_group="CABA", zone_detail="La Paternal",
            status="current_focus")
        self.db.add(self.cand); self.db.flush()
        # post-offer state: SCHEDULING, a day on the table, and a live Flow token
        self.state = WhatsAppThreadState(
            thread_id=self.thread.id, last_stage="SCHEDULING", needs_human=False,
            home_zone_group="CABA", home_zone_detail="La Paternal",
            current_focus_candidate_id=self.cand.id,
            active_requested_date=OFFERED_DAY,
            last_offered_slots='["13:00", "13:30", "14:00"]',
            last_visible_slots='["13:00", "13:30", "14:00"]',
            flow_booking_token=f"{self.thread.id}-1788999999-abcdef0123456789")
        self.db.add(self.state); self.db.commit()

        self.eng = ConversationEngine(db=self.db, settings=MagicMock())
        self.sent: list[str] = []
        self.flows: list[dict] = []
        self.emails: list[dict] = []
        self.eng._send_text_to_wa = lambda ctx, text: (self.sent.append(text), "wamid.OUT")[1]
        self.eng._send_flow_button = lambda *a, **k: (self.flows.append(k), "wamid.FLOW")[1]
        self.eng._send_scheduling_handoff_email = lambda **k: self.emails.append(k)
        self.eng._send_booking_notification = lambda **k: None

    def tearDown(self):
        self.db.close()

    def turn(self, *texts, wa_msg_id="wamid.IN.1"):
        """Drive the live entry point exactly as n8n does."""
        for i, t in enumerate(texts):
            self.db.add(WhatsAppMessage(
                thread_id=self.thread.id, direction="in", message_type="text", status="received",
                timestamp=datetime.now(timezone.utc), text=t,
                wa_message_id=f"{wa_msg_id}.{i}"))
        self.db.commit()
        event = ConversationHandleIn(
            thread_id=self.thread.id, wa_message_id=f"{wa_msg_id}.{len(texts)-1}",
            wa_id=WA_ID, text=texts[-1],
            recent_user_messages=list(texts),
            unanswered_recent_user_messages=list(texts))
        return self.eng.handle(event)

    # ── shared assertions ────────────────────────────────────────────────────
    def assert_rescued(self):
        self.db.expire_all()
        self.assertTrue(self.state.needs_human, "needs_human must be set")
        self.assertTrue(self.lead.necesita_humano)
        self.assertEqual(self.lead.estado, "ATENCION_HUMANA")
        self.assertEqual(len(self.emails), 1, "exactly one operator alert")
        self.assertEqual(len(self.flows), 0, "no new Flow after rescue")
        self.assertEqual(self.db.execute(select(ThreadRevision).where(
            ThreadRevision.status == "booked")).scalars().all(), [], "no booking")
        self.assertEqual(len(self.sent), 1, "exactly one customer reply")
        self.assertIn("Julián", self.sent[0])

    def assert_not_rescued(self):
        self.db.expire_all()
        self.assertFalse(self.state.needs_human)
        self.assertEqual(len(self.emails), 0)


class TestRescueAudibleWithTokenSet(_LiveTurn):

    def test_rescue_01_the_exact_wild_sentence(self):
        """RESCUE-01 — "no me sirve mañana me lo venden", with the token set."""
        self.assertTrue(self.state.flow_booking_token, "precondition: a Flow is open")
        self.turn("no me sirve mañana me lo venden")
        self.assert_rescued()

    def test_rescue_02_rejection_plus_urgency(self):
        self.turn("mañana no puedo y lo necesito antes")
        self.assert_rescued()

    def test_rescue_03_offer_rejected_without_inventing_anything(self):
        """RESCUE-03 — no booking, no invented replacement slot."""
        self.turn("ninguno de esos horarios me sirve")
        self.db.expire_all()
        self.assertEqual(len(self.flows), 0)
        self.assertEqual(self.db.execute(select(ThreadRevision)).scalars().all(), [])

    def test_rescue_04_explicit_request_for_a_person(self):
        self.turn("necesito hablar con una persona")
        self.db.expire_all()
        self.assertTrue(self.state.needs_human)
        self.assertEqual(len(self.emails), 1)

    def test_rescue_05_cancellation_creates_nothing(self):
        """RESCUE-05 — refusal must not book and must not re-offer."""
        self.turn("dejalo, no quiero reservar")
        self.db.expire_all()
        self.assertEqual(self.db.execute(select(ThreadRevision)).scalars().all(), [])
        self.assertEqual(len(self.flows), 0)

    def test_the_withdrawn_offer_token_is_consumed_exactly_once(self):
        """One explicit lifecycle transition: the rejected offer is withdrawn, so a late tap
        on the old Flow fails token validation instead of booking a rejected slot."""
        before = self.state.flow_booking_token
        self.assertTrue(before)
        self.turn("no me sirve mañana me lo venden")
        self.db.expire_all()
        self.assertIsNone(self.state.flow_booking_token, "the offer must be withdrawn")


class TestAutomationStopsAfterHandoff(_LiveTurn):

    def test_safe_04_and_06_repeated_urgency_produces_no_duplicate_side_effects(self):
        """SAFE-04/06 — the existing handoff contract owns the thread afterwards."""
        self.turn("no me sirve mañana me lo venden", wa_msg_id="wamid.A")
        self.assert_rescued()
        out = self.turn("en serio lo necesito ya", wa_msg_id="wamid.B")
        self.db.expire_all()
        self.assertEqual(out.action, "skipped_human")
        self.assertEqual(len(self.emails), 1, "no second operator alert")
        self.assertEqual(len(self.sent), 1, "no further automated reply")
        self.assertEqual(len(self.flows), 0)

    def test_safe_06_a_redelivered_wamid_rescues_once(self):
        """SAFE-06 — Meta redelivers the same WAMID. The row already exists (the webhook
        stores it once), so the redelivery re-drives handle() with the same event."""
        text = "no me sirve mañana me lo venden"
        self.turn(text, wa_msg_id="wamid.DUP")
        event = ConversationHandleIn(
            thread_id=self.thread.id, wa_message_id="wamid.DUP.0", wa_id=WA_ID, text=text,
            recent_user_messages=[text], unanswered_recent_user_messages=[text])
        self.eng.handle(event)          # redelivery, no new row
        self.db.expire_all()
        self.assertEqual(len(self.emails), 1, "the operator is alerted once")
        self.assertEqual(len(self.sent), 1, "the customer is answered once")


class TestNoFalseRescue(_LiveTurn):

    def test_safe_02_an_acceptance_is_not_a_rejection(self):
        """SAFE-02 — "mañana me sirve" is the opposite of the Wild sentence."""
        self.turn("mañana me sirve")
        self.assert_not_rescued()

    def test_safe_01_an_unrelated_faq_does_not_escalate(self):
        self.turn("¿tengo que estar presente?")
        self.assert_not_rescued()

    def test_safe_03_an_ambiguous_message_invents_nothing(self):
        self.turn("mmm no sé")
        self.db.expire_all()
        self.assertFalse(self.state.needs_human)
        self.assertEqual(self.db.execute(select(ThreadRevision)).scalars().all(), [])
        self.assertEqual(len(self.flows), 0)

    def test_urgency_alone_before_any_offer_does_not_escalate(self):
        """The precondition is an offer on the table, not a mood."""
        self.state.active_requested_date = None
        self.db.commit()
        self.turn("lo necesito urgente")
        self.assert_not_rescued()


class TestAuthorityBoundaries(unittest.TestCase):
    """The responsibility split, asserted where the guards live."""

    def test_rescue_consumption_is_token_independent(self):
        seg = CE_SOURCE[CE_SOURCE.index("# ── L4.7W5-F7A"):]
        seg = seg[:seg.index("sched_day_iso")]
        rescue = seg[:seg.index("if (\n            state.last_stage == STAGE_SCHEDULING")]
        # strip comments: this block EXPLAINS the old guard in prose, and prose about a
        # guard is not the guard. Asserting over raw source would fail on its own commentary.
        code = "\n".join(l for l in rescue.splitlines() if not l.strip().startswith("#"))
        self.assertIn("if state.last_stage == STAGE_SCHEDULING and not state.needs_human:", code)
        self.assertIn("_earliest_option_rejected", code)
        self.assertIn("_should_escalate_scheduling_to_human", code)
        self.assertNotIn("not state.flow_booking_token", code,
                         "rescue must not be gated on the token")

    def test_flow_dispatch_is_still_token_guarded(self):
        """MAY_DISPATCH_NEW_FLOW keeps its guard — a duplicate Flow is still prevented."""
        self.assertIn("""            state.last_stage == STAGE_SCHEDULING
            and not state.needs_human
            and not state.flow_booking_token""", CE_SOURCE)

    def test_booking_write_authority_is_untouched(self):
        """MAY_CREATE_OR_CONFIRM_BOOKING stays with BookingFlowService."""
        bfs = (ROOT / "backend" / "app" / "services"
               / "booking_flow_service.py").read_text(encoding="utf-8-sig")
        self.assertIn("BOOKING_REVALIDATION_PASS", bfs)
        self.assertIn("self._sched.check(check_in)", bfs)
        writers = {n.name for n in ast.walk(ast.parse(bfs))
                   if isinstance(n, ast.FunctionDef)
                   and 'status="booked"' in ast.unparse(n).replace("'", '"')}
        self.assertTrue(writers <= {"handle_confirm_booking", "_create_booking"}, writers)

    def test_the_withdrawal_is_one_explicit_transition(self):
        fn = _fn("_handle_scheduling_escalation")
        self.assertIn("FLOW_OFFER_WITHDRAWN", fn)
        self.assertEqual(fn.count("state.flow_booking_token = None"), 1)

    def test_schedule_service_remains_the_availability_authority(self):
        nxt = _fn("_handle_next_available_request")
        self.assertIn("self._schedule.find_next_available", nxt)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
