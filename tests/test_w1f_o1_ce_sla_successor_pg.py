"""W1F-O1 on PostgreSQL — the REAL per-turn CE-SLA job (`unanswered_alert._run_check`).

R2 (L4.7W5-NO-REPLY-ALERT-INTEGRITY-R2) put a successor clause in
`_FIND_UNANSWERED_EVENTS_SQL`: a turn that produced no reply is not unanswered once a later
outbound on the same thread was accepted by Meta. The constant was never executed. The job
ran its own inline copy without the clause, so an answered turn was e-mailed as
"sin respuesta" and stamped `performance_status='ALERT'`. The R2 suite certified a copy of
the SQL and the constant's text.

Every case here drives `_run_check` itself over rows written with the production models, so
the query under test is whatever the job executes. The e-mail sender is a recorder; nothing
leaves the process. This file imports nothing the correction added: run against the parent
(`c6e5ce9`) the S01/S02 cases reproduce the defect.

  W1F_PG_URL   a disposable PostgreSQL 16 database at the Alembic head (never crm/crm_test)

The `SourceInvariant` class needs no database: `_run_check` must execute the canonical
constant and carry no inline per-turn query of its own.
"""
from __future__ import annotations

import ast
import itertools
import os
import pathlib
import sys
import unittest
from datetime import datetime, time, timedelta, timezone
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

ROOT = pathlib.Path(__file__).resolve().parents[1]
ALERT_SOURCE = (ROOT / "backend" / "app" / "services"
                / "unanswered_alert.py").read_text(encoding="utf-8-sig")

PG_URL = os.environ.get("W1F_PG_URL", "")
_SEQ = itertools.count(1)


def _run_check_source() -> str:
    return next(ast.unparse(n) for n in ast.walk(ast.parse(ALERT_SOURCE))
                if isinstance(n, ast.FunctionDef) and n.name == "_run_check")


class SourceInvariant(unittest.TestCase):
    """Secondary to the PostgreSQL cases below, which are the authority."""

    def test_i01_run_check_executes_the_canonical_query(self):
        self.assertIn("db.execute(_FIND_UNANSWERED_EVENTS_SQL)", _run_check_source())

    def test_i02_no_inline_per_turn_query_remains(self):
        run = _run_check_source()
        for fragment in ("FROM ai_events", "ae.reply_required", "ae.alert_eligible",
                         "ae.reply_produced"):
            self.assertNotIn(fragment, run, f"_run_check carries its own per-turn SQL ({fragment})")
        self.assertEqual(ALERT_SOURCE.count("ae.reply_required = true"), 1,
                         "more than one per-turn query definition in the module")

    def test_i03_the_threshold_is_a_real_bind(self):
        from app.services import unanswered_alert as UA
        self.assertFalse("':threshold" in UA._FIND_UNANSWERED_EVENTS_SQL.text,
                         "a quoted placeholder is not a bind")
        compiled = UA._FIND_UNANSWERED_EVENTS_SQL.compile()
        self.assertEqual(compiled.params, {"threshold_seconds": UA._ALERT_THRESHOLD_SECONDS})
        self.assertIn("(:threshold_seconds * INTERVAL '1 second')", str(compiled))


@unittest.skipUnless(PG_URL, "W1F_PG_URL not set — no disposable PostgreSQL; nothing proven")
class CeSlaRuntimePg(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from sqlalchemy import create_engine
        cls.engine = create_engine(PG_URL)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    # ── fixture: production models only ──────────────────────────────────────

    def thread(self, *, needs_human: bool = False) -> int:
        from sqlalchemy.orm import Session
        from app.models import WhatsAppContact, WhatsAppThread, WhatsAppThreadState
        n = next(_SEQ)
        with Session(self.engine) as db:
            c = WhatsAppContact(wa_id=f"5491100994{n:03d}")
            db.add(c); db.flush()
            t = WhatsAppThread(contact_id=c.id, unread_count=0)
            db.add(t); db.flush()
            db.add(WhatsAppThreadState(thread_id=t.id, needs_human=needs_human))
            db.commit()
            return int(t.id)

    def turn(self, thread_id: int, *, age: timedelta = timedelta(minutes=10)) -> tuple[int, datetime]:
        """A turn CE marked reply_required / alert_eligible / reply_produced=false."""
        from sqlalchemy.orm import Session
        from app.models import AiEvent
        created = datetime.now(timezone.utc) - age
        with Session(self.engine) as db:
            ev = AiEvent(thread_id=thread_id, wa_message_id=f"wamid.O1.IN.{next(_SEQ)}",
                         event_type="inbound", wa_id="o1", created_at=created,
                         action="no_reply_produced", reply_required=True,
                         reply_produced=False, alert_eligible=True)
            db.add(ev); db.commit()
            return int(ev.id), created

    def outbound(self, thread_id: int, at: datetime, *, status: str = "sent",
                 wamid: bool = True) -> int:
        from sqlalchemy.orm import Session
        from app.models import WhatsAppMessage
        n = next(_SEQ)
        with Session(self.engine) as db:
            m = WhatsAppMessage(thread_id=thread_id, direction="out", status=status,
                                timestamp=at, message_type="text", text="o1",
                                automated=True, path_id="CE_TEXT",
                                wa_message_id=f"wamid.O1.OUT.{n}" if wamid else None)
            db.add(m); db.commit()
            return int(m.id)

    def run_check(self) -> list:
        from sqlalchemy.orm import sessionmaker
        from app.services import unanswered_alert as UA
        calls = []
        with mock.patch.object(UA, "SessionLocal", sessionmaker(bind=self.engine)), \
                mock.patch.object(UA, "_send_alert_email",
                                  lambda tid, name, reason="CE": calls.append((tid, reason))):
            UA._run_check()
        return calls

    def ce_sla(self, thread_id: int) -> list:
        return [c for c in self.run_check() if c == (thread_id, "CE-SLA")]

    def event(self, event_id: int):
        from sqlalchemy.orm import Session
        from app.models import AiEvent
        with Session(self.engine) as db:
            ev = db.get(AiEvent, event_id)
            return ev.performance_status, ev.unanswered_alert_sent_at

    # ── assertions ───────────────────────────────────────────────────────────

    def assert_suppressed(self, thread_id: int, event_id: int):
        self.assertEqual(self.ce_sla(thread_id), [],
                         "a turn answered by a later accepted reply was e-mailed as unanswered")
        self.assertEqual(self.event(event_id), (None, None),
                         "an answered turn was stamped performance_status=ALERT")
        self.assertEqual(self.ce_sla(thread_id), [])

    def assert_alerts_once(self, thread_id: int, event_id: int):
        self.assertEqual(self.ce_sla(thread_id), [(thread_id, "CE-SLA")])
        status, sent_at = self.event(event_id)
        self.assertEqual(status, "ALERT")
        self.assertIsNotNone(sent_at)
        self.assertEqual(self.ce_sla(thread_id), [], "alerted twice")
        self.assertEqual(self.event(event_id), (status, sent_at), "the mark moved")

    # ── 1-2: an accepted successor answers the turn ─────────────────────────

    def test_s01_a_later_sent_reply_suppresses(self):
        t = self.thread(); ev, at = self.turn(t)
        self.outbound(t, at + timedelta(seconds=30), status="sent")
        self.assert_suppressed(t, ev)

    def test_s02_a_later_delivered_reply_suppresses(self):
        t = self.thread(); ev, at = self.turn(t)
        self.outbound(t, at + timedelta(seconds=30), status="delivered")
        self.assert_suppressed(t, ev)

    def test_s03_a_later_read_reply_suppresses(self):
        t = self.thread(); ev, at = self.turn(t)
        self.outbound(t, at + timedelta(seconds=30), status="read")
        self.assert_suppressed(t, ev)

    # ── 3-5: everything else still alerts, exactly once ─────────────────────

    def test_n01_no_successor_alerts_exactly_once(self):
        t = self.thread(); ev, _ = self.turn(t)
        self.assert_alerts_once(t, ev)

    def test_n02_a_failed_successor_does_not_suppress(self):
        t = self.thread(); ev, at = self.turn(t)
        self.outbound(t, at + timedelta(seconds=30), status="failed")
        self.assert_alerts_once(t, ev)

    def test_n03_non_qualifying_successors_do_not_suppress(self):
        for status, wamid in (("blocked", False), ("pending", False), ("sent", False)):
            with self.subTest(status=status, wamid=wamid):
                t = self.thread(); ev, at = self.turn(t)
                self.outbound(t, at + timedelta(seconds=30), status=status, wamid=wamid)
                self.assert_alerts_once(t, ev)

    def test_n04_a_reply_on_another_thread_does_not_suppress(self):
        t = self.thread(); other = self.thread()
        ev, at = self.turn(t)
        self.outbound(other, at + timedelta(seconds=30), status="read")
        self.assert_alerts_once(t, ev)

    def test_n05_a_reply_to_an_earlier_turn_does_not_suppress(self):
        t = self.thread(); ev, at = self.turn(t)
        self.outbound(t, at - timedelta(seconds=30), status="read")
        self.assert_alerts_once(t, ev)

    def test_n06_a_later_inbound_does_not_suppress(self):
        """`read` too: the status webhook advances a row by WAMID whatever its direction, and
        the schema allows every status in both directions — only `direction='out'` is a reply."""
        from sqlalchemy.orm import Session
        from app.models import WhatsAppMessage
        for status in ("received", "read"):
            with self.subTest(status=status):
                t = self.thread(); ev, at = self.turn(t)
                with Session(self.engine) as db:
                    db.add(WhatsAppMessage(thread_id=t, direction="in", status=status,
                                           timestamp=at + timedelta(seconds=30),
                                           message_type="text", text="o1",
                                           wa_message_id=f"wamid.O1.IN.{next(_SEQ)}"))
                    db.commit()
                self.assert_alerts_once(t, ev)

    def test_n07_one_answered_turn_does_not_silence_a_later_unanswered_one(self):
        t = self.thread()
        first, at = self.turn(t, age=timedelta(minutes=10))
        self.outbound(t, at + timedelta(seconds=30), status="sent")
        second, _ = self.turn(t, age=timedelta(minutes=5))
        self.assertEqual(self.ce_sla(t), [(t, "CE-SLA")])
        self.assertEqual(self.event(first), (None, None))
        self.assertEqual(self.event(second)[0], "ALERT")

    # ── 6: the threshold ─────────────────────────────────────────────────────

    def test_t01_threshold_younger_waits_older_alerts(self):
        from sqlalchemy import update
        from sqlalchemy.orm import Session
        from app.models import AiEvent
        from app.services.unanswered_alert import _ALERT_THRESHOLD_SECONDS
        t = self.thread()
        ev, _ = self.turn(t, age=timedelta(seconds=_ALERT_THRESHOLD_SECONDS - 60))
        self.assertEqual(self.ce_sla(t), [], "alerted before the threshold")
        self.assertEqual(self.event(ev), (None, None))
        with Session(self.engine) as db:
            db.execute(update(AiEvent).where(AiEvent.id == ev).values(
                created_at=datetime.now(timezone.utc)
                - timedelta(seconds=_ALERT_THRESHOLD_SECONDS + 60)))
            db.commit()
        self.assert_alerts_once(t, ev)

    def test_t02_a_human_owned_thread_has_no_ce_sla_alert(self):
        t = self.thread(needs_human=True); ev, _ = self.turn(t)
        self.assertEqual(self.ce_sla(t), [])
        self.assertEqual(self.event(ev), (None, None))

    # ── 7: the thread-level path and W1-F1 are unchanged ────────────────────

    def test_w01_an_acknowledged_flow_completion_is_still_suppressed(self):
        import _sb2b_harness as H
        from sqlalchemy import select, text, update
        from sqlalchemy.orm import Session
        from app.models import WhatsAppMessage
        w = H.seed(self.engine, day=H.slot_day(), at=time(10, 0))
        with H.Meta(mode="ok"):
            screen = H.dx_confirm(self.engine, w)
        self.assertEqual(screen["screen"], "SUCCESS", screen)
        with Session(self.engine) as db:
            ack = db.execute(select(WhatsAppMessage).where(
                WhatsAppMessage.thread_id == w.thread_id, WhatsAppMessage.direction == "out",
                WhatsAppMessage.path_id == "BOOKING_FLOW").order_by(WhatsAppMessage.id.desc())
            ).scalars().first()
            db.execute(update(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == w.wamid)
                       .values(timestamp=ack.timestamp + timedelta(seconds=4)))
            db.commit()
        with H.Meta(mode="ok") as meta:
            out, _ = H.run_turn(self.engine, H.flow_event(w))
        self.assertEqual((meta.sent, out.action), ([], "booking_replayed"))
        with self.engine.begin() as c:
            c.execute(text("UPDATE whatsapp_threads SET last_message_at = now() - interval "
                           "'10 minutes' WHERE id = :t"), {"t": w.thread_id})
        self.assertEqual([c for c in self.run_check() if c[0] == w.thread_id], [])

    def test_w02_a_genuine_human_handoff_still_alerts_once(self):
        from sqlalchemy import text
        from sqlalchemy.orm import Session
        from app.models import WhatsAppMessage
        t = self.thread(needs_human=True)
        with Session(self.engine) as db:
            db.add(WhatsAppMessage(thread_id=t, direction="in", status="received",
                                   wa_message_id=f"wamid.O1.IN.{next(_SEQ)}",
                                   message_type="text", text="o1",
                                   timestamp=datetime.now(timezone.utc) - timedelta(minutes=9)))
            db.commit()
        with self.engine.begin() as c:
            c.execute(text("UPDATE whatsapp_threads SET last_message_at = now() - interval "
                           "'10 minutes' WHERE id = :t"), {"t": t})
        self.assertEqual([c for c in self.run_check() if c[0] == t], [(t, "HUMAN")])
        self.assertEqual([c for c in self.run_check() if c[0] == t], [], "alerted twice")


if __name__ == "__main__":
    unittest.main()
