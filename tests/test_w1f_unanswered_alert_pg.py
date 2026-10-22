"""W1-F1 on PostgreSQL — the REAL alert job (`unanswered_alert._run_check`) over the EXACT
first-Wild ordering, produced by the production code.

  booking committed (Data Exchange `confirm_booking` → BookingAuthority)
  → acknowledgement sent (the one owner-approved text, BOOKING_FLOW)
  → Meta's `flow_response` completion stored 4 s later
  → the Conversation Engine replays it (`booking_replayed`, nothing sent)
  → 120 s pass with `needs_human=true`

On the parent (`10f6a35`) the job e-mails the operator a false "Thread #… (human handoff)
sin respuesta" for that thread (the Wild's 21:30:00 Resend e-mail). After the correction it
does not — and a genuine handoff, a customer writing again after the booking, and a booking
whose acknowledgement was never delivered all still alert exactly once.

This file imports nothing the correction added, so the same file runs against the parent to
reproduce the defect. The e-mail sender is a recorder; nothing leaves the process.

  W1F_PG_URL   a disposable PostgreSQL 16 database at the Alembic head (never crm/crm_test)
"""
from __future__ import annotations

import os
import pathlib
import sys
import unittest
from datetime import date, datetime, time, timedelta, timezone
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _sb2b_harness as H  # noqa: E402

from sqlalchemy import create_engine, select, text, update  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.models import (BookingCommandRecord, WhatsAppContact, WhatsAppMessage,  # noqa: E402
                        WhatsAppThread, WhatsAppThreadState)
from app.services import unanswered_alert as UA  # noqa: E402

PG_URL = os.environ.get("W1F_PG_URL", "")


def _slots():
    """Distinct bookable slots, one per booking: a database shared by the tests of this file
    must never see two bookings compete for one slot."""
    tue = H.slot_day()
    mon = tue - timedelta(days=1)
    yield from ((tue, time(10, 0)), (tue, time(12, 0)), (mon, time(14, 0)), (mon, time(16, 0)))


@unittest.skipUnless(PG_URL, "W1F_PG_URL not set — no disposable PostgreSQL; nothing proven")
class W1F1_AlertJobPg(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(PG_URL)
        cls.slots = _slots()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def run_check(self) -> list:
        calls = []
        with mock.patch.object(UA, "SessionLocal", sessionmaker(bind=self.engine)), \
                mock.patch.object(UA, "_send_alert_email",
                                  lambda tid, name, reason="CE": calls.append((tid, reason))):
            UA._run_check()
        return calls

    def age(self, thread_id: int):
        with self.engine.begin() as c:
            c.execute(text("UPDATE whatsapp_threads SET last_message_at = now() - interval "
                           "'10 minutes' WHERE id = :t"), {"t": thread_id})

    def alert_marked(self, thread_id: int):
        with Session(self.engine) as db:
            return db.execute(select(WhatsAppThreadState.unanswered_alert_sent_at).where(
                WhatsAppThreadState.thread_id == thread_id)).scalar_one()

    def needs_human(self, thread_id: int) -> bool:
        with Session(self.engine) as db:
            return db.execute(select(WhatsAppThreadState.needs_human).where(
                WhatsAppThreadState.thread_id == thread_id)).scalar_one()

    def wild(self, *, meta_mode: str = "ok"):
        """The first Wild's exact sequence, through production code."""
        day, at = next(self.slots)
        w = H.seed(self.engine, day=day, at=at)
        with H.Meta(mode=meta_mode):
            screen = H.dx_confirm(self.engine, w)
        self.assertEqual(screen["screen"], "SUCCESS", screen)
        with Session(self.engine) as db:
            ack = db.execute(select(WhatsAppMessage).where(
                WhatsAppMessage.thread_id == w.thread_id, WhatsAppMessage.direction == "out",
                WhatsAppMessage.path_id == "BOOKING_FLOW").order_by(WhatsAppMessage.id.desc())
            ).scalars().first()
            # Meta's completion is stored AFTER the acknowledgement (Wild: +4 s)
            db.execute(update(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == w.wamid)
                       .values(timestamp=ack.timestamp + timedelta(seconds=4)))
            db.commit()
        with H.Meta(mode=meta_mode) as meta:
            out, _ = H.run_turn(self.engine, H.flow_event(w))
        if meta_mode == "ok":
            self.assertEqual(meta.sent, [], "the replay sent a second message")
            self.assertEqual(out.action, "booking_replayed")
        self.assertTrue(self.needs_human(w.thread_id), "Wild precondition: needs_human")
        return w

    def test_p01_wild_ordering_no_false_handoff_alert(self):
        w = self.wild()
        with Session(self.engine) as db:
            led = db.execute(select(BookingCommandRecord).where(
                BookingCommandRecord.thread_id == w.thread_id)).scalar_one()
            self.assertEqual((led.source, led.operation, led.command_state, led.ack_status),
                             ("WHATSAPP_FLOW", "CREATE", "COMMITTED", "SENT"))
        self.age(w.thread_id)
        calls = [c for c in self.run_check() if c[0] == w.thread_id]
        self.assertEqual(calls, [], "false 'human handoff sin respuesta' alert after a "
                                    "committed, acknowledged booking (W1-F1)")
        self.assertIsNone(self.alert_marked(w.thread_id))
        self.assertEqual([c for c in self.run_check() if c[0] == w.thread_id], [])

    def test_p02_genuine_human_handoff_still_alerts_once(self):
        with Session(self.engine) as db:
            c = WhatsAppContact(wa_id="5491100990201")
            db.add(c); db.flush()
            t = WhatsAppThread(contact_id=c.id, unread_count=0)
            db.add(t); db.flush()
            db.add(WhatsAppThreadState(thread_id=t.id, needs_human=True))
            db.add(WhatsAppMessage(thread_id=t.id, direction="in", status="received",
                                   wa_message_id="wamid.W1F.PG.HUMAN", message_type="text",
                                   text="quiero hablar con una persona",
                                   timestamp=datetime.now(timezone.utc) - timedelta(minutes=9)))
            db.commit()
            tid = t.id
        self.age(tid)
        self.assertEqual([c for c in self.run_check() if c[0] == tid], [(tid, "HUMAN")])
        self.assertIsNotNone(self.alert_marked(tid))
        self.assertEqual([c for c in self.run_check() if c[0] == tid], [], "alerted twice")

    def test_p03_customer_writing_again_after_the_booking_alerts(self):
        w = self.wild()
        with Session(self.engine) as db:
            db.add(WhatsAppMessage(thread_id=w.thread_id, direction="in", status="received",
                                   wa_message_id="wamid.W1F.PG.AFTER", message_type="text",
                                   text="una consulta más",
                                   timestamp=datetime.now(timezone.utc) + timedelta(seconds=30)))
            db.commit()
        self.age(w.thread_id)
        self.assertEqual([c for c in self.run_check() if c[0] == w.thread_id],
                         [(w.thread_id, "HUMAN")])

    def test_p04_a_booking_whose_acknowledgement_was_not_delivered_alerts(self):
        w = self.wild(meta_mode="4xx")
        with Session(self.engine) as db:
            led = db.execute(select(BookingCommandRecord).where(
                BookingCommandRecord.thread_id == w.thread_id)).scalar_one()
            self.assertNotEqual(led.ack_status, "SENT")
        self.age(w.thread_id)
        self.assertEqual([c for c in self.run_check() if c[0] == w.thread_id],
                         [(w.thread_id, "HUMAN")])


if __name__ == "__main__":
    unittest.main()
