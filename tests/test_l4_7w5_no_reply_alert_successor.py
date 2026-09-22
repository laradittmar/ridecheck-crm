"""L4.7W5-NO-REPLY-ALERT-INTEGRITY-R2 — an answered conversation is not unanswered.

R1 made a required-but-unproduced reply alert-eligible, and disclosed the consequence: the
rescue query had no successor clause, so once 120 seconds elapsed the alert fired regardless
of what happened afterwards. A customer who was answered thirty seconds later would still
have been reported as ignored.

The owner's rule: a genuine successful outbound response on the SAME thread, sent after the
failed turn and before the alert is emitted, suppresses it. Blocked, failed, queued and
never-attempted sends do not, because none of them reached the customer.

The correlation is `thread_id`, never a phone number and never message text. "Successful" is
the transport contract's own vocabulary, not a guess about timing.

SRC-01..06   the authoritative record and its status vocabulary
SUP-01..06   what suppresses, and what must not
NEG-01..05   blocked / failed / pending / no-WAMID / cross-thread never suppress
PRE-01..05   R1 behaviour, historical alerts and dedup are preserved
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import types
import unittest

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

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

import app.models as models
from app.models import (AiEvent, WhatsAppContact, WhatsAppMessage, WhatsAppThread,
                        WhatsAppThreadState)
from app.schemas.conversation import ACTION_NO_REPLY_PRODUCED
from app.services.conversation_engine import _out

UTC = dt.timezone.utc
ALERT_SOURCE = (ROOT / "backend" / "app" / "services"
                / "unanswered_alert.py").read_text(encoding="utf-8-sig")

#: The production predicate, with PostgreSQL booleans written for SQLite and the time
#: threshold dropped (every fixture event is deliberately old enough). The successor clause
#: is copied verbatim so this suite fails if the real one is weakened.
_RESCUE_SQL = text("""
    SELECT ae.id FROM ai_events ae
    JOIN whatsapp_threads wt ON wt.id = ae.thread_id
    LEFT JOIN whatsapp_thread_states wts ON wts.thread_id = ae.thread_id
    WHERE ae.reply_required = 1
      AND ae.alert_eligible = 1
      AND (ae.reply_produced IS NULL OR ae.reply_produced = 0)
      AND ae.unanswered_alert_sent_at IS NULL
      AND (wts.needs_human IS NULL OR wts.needs_human = 0)
      AND NOT EXISTS (
          SELECT 1 FROM whatsapp_messages ob
          WHERE ob.thread_id = ae.thread_id
            AND ob.direction = 'out'
            AND ob.status IN ('sent', 'delivered', 'read')
            AND ob.wa_message_id IS NOT NULL
            AND ob.timestamp > ae.created_at
      )
    ORDER BY ae.id
""")

T0 = dt.datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)


class _Fixture(unittest.TestCase):
    """Two threads and two customers, so cross-thread leakage is detectable."""

    def setUp(self):
        engine = create_engine("sqlite://")
        models.Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.threads = {}
        for key, wa_id in (("A", "5490000000001"), ("B", "5490000000002")):
            contact = WhatsAppContact(wa_id=wa_id)
            self.db.add(contact)
            self.db.flush()
            thread = WhatsAppThread(contact_id=contact.id)
            self.db.add(thread)
            self.db.flush()
            self.db.add(WhatsAppThreadState(thread_id=thread.id, needs_human=False))
            self.threads[key] = thread
        self.db.commit()
        self._n = 0

    def no_reply_event(self, thread="A", at=T0):
        out = _out(ACTION_NO_REPLY_PRODUCED, detail="no_reply_text")
        return self._event(out, thread, at)

    def _event(self, out, thread, at):
        self._n += 1
        ev = AiEvent(thread_id=self.threads[thread].id,
                     wa_message_id=f"wamid.in.{self._n}", event_type="inbound",
                     wa_id="5490000000001", created_at=at, action=out.action,
                     reply_required=out.reply_required, reply_produced=out.reply_produced,
                     alert_eligible=out.alert_eligible)
        self.db.add(ev)
        self.db.commit()
        return ev

    def outbound(self, thread="A", status="sent", offset_seconds=30, wamid="auto",
                 automated=True, path_id="CE_TEXT"):
        self._n += 1
        row = WhatsAppMessage(
            thread_id=self.threads[thread].id, direction="out", status=status,
            timestamp=T0 + dt.timedelta(seconds=offset_seconds),
            message_type="text", text="x", automated=automated, path_id=path_id,
            wa_message_id=(f"wamid.out.{self._n}" if wamid == "auto" else wamid))
        self.db.add(row)
        self.db.commit()
        return row

    def selected(self):
        return [r[0] for r in self.db.execute(_RESCUE_SQL).fetchall()]


# ── SRC: the authoritative record ────────────────────────────────────────────

class AuthoritativeSource(_Fixture):

    def test_src_01_the_status_vocabulary_is_constrained_by_the_schema(self):
        constraints = [str(c.sqltext) for c in WhatsAppMessage.__table__.constraints
                       if hasattr(c, "sqltext")]
        joined = " ".join(constraints)
        for status in ("pending", "sent", "delivered", "read", "failed", "blocked"):
            self.assertIn(status, joined)

    def test_src_02_only_the_gate_writes_outbound_rows(self):
        """The invariant that makes one table an authoritative source."""
        gate = (ROOT / "backend" / "app" / "services"
                / "outbound_safety_gate.py").read_text(encoding="utf-8-sig")
        self.assertIn('direction="out"', gate)
        for other in ("services/conversation_engine.py", "api/whatsapp.py",
                      "routes/whatsapp.py", "ui/whatsapp_ui.py"):
            source = (ROOT / "backend" / "app" / other).read_text(encoding="utf-8-sig")
            # A construction site is a keyword argument on its own line; prose that
            # merely mentions the string (whatsapp_ui.py records that its own direct
            # write was removed in L4.7W1-F3) is not a write.
            inserts = [l.strip() for l in source.splitlines()
                       if l.strip().startswith('direction="out"')]
            self.assertEqual(inserts, [], f"{other} writes an outbound row directly")

    def test_src_03_success_statuses_are_the_ones_the_query_uses(self):
        self.assertIn("ob.status IN ('sent', 'delivered', 'read')", ALERT_SOURCE)

    def test_src_04_correlation_is_by_thread_not_by_phone(self):
        clause = ALERT_SOURCE.split("AND NOT EXISTS (")[1].split(")")[0]
        self.assertIn("ob.thread_id = ae.thread_id", clause)
        self.assertNotIn("wa_id", clause)
        self.assertNotIn("text", clause.replace("ob.", ""))

    def test_src_05_a_wamid_is_required_as_durable_success_evidence(self):
        self.assertIn("ob.wa_message_id IS NOT NULL", ALERT_SOURCE)

    def test_src_06_the_ordering_is_attempt_time_after_the_failed_event(self):
        self.assertIn("ob.timestamp > ae.created_at", ALERT_SOURCE)


# ── SUP: what suppresses ─────────────────────────────────────────────────────

class SuccessorSuppression(_Fixture):

    def test_sup_01_no_reply_and_no_later_outbound_is_selected(self):
        ev = self.no_reply_event()
        self.assertIn(ev.id, self.selected())

    def test_sup_02_a_later_successful_send_suppresses(self):
        ev = self.no_reply_event()
        self.assertIn(ev.id, self.selected())
        self.outbound(status="sent", offset_seconds=30)
        self.assertNotIn(ev.id, self.selected(),
                         "a conversation that was answered is not unanswered")

    def test_sup_03_delivered_and_read_also_suppress(self):
        for status in ("delivered", "read"):
            with self.subTest(status=status):
                self.setUp()
                ev = self.no_reply_event()
                self.outbound(status=status, offset_seconds=30)
                self.assertNotIn(ev.id, self.selected())

    def test_sup_04_an_earlier_success_does_not_excuse_a_later_failure(self):
        self.outbound(status="read", offset_seconds=-60)
        ev = self.no_reply_event()
        self.assertIn(ev.id, self.selected(),
                      "a reply sent before the failure answered a different turn")

    def test_sup_05_an_automated_reply_suppresses(self):
        ev = self.no_reply_event()
        self.outbound(status="sent", automated=True, path_id="CE_TEXT")
        self.assertNotIn(ev.id, self.selected())

    def test_sup_06_a_human_crm_reply_suppresses(self):
        """Human replies pass through the same gate, so they are the same evidence.

        `api/whatsapp.py` calls `OutboundSafetyGate.attempt()` then `mark_sent()` with
        `path_id=MANUAL_CRM`; there is no second write path to account for.
        """
        api = (ROOT / "backend" / "app" / "api" / "whatsapp.py").read_text(encoding="utf-8-sig")
        self.assertIn("OutboundSafetyGate", api)
        self.assertIn("gate.mark_sent(", api)
        self.assertIn("MANUAL_CRM", api)
        ev = self.no_reply_event()
        self.outbound(status="sent", automated=False, path_id="MANUAL_CRM")
        self.assertNotIn(ev.id, self.selected())

    def test_sup_07_a_flow_send_suppresses(self):
        ev = self.no_reply_event()
        self.outbound(status="sent", path_id="BOOKING_FLOW")
        self.assertNotIn(ev.id, self.selected())


# ── NEG: what must never suppress ────────────────────────────────────────────

class NonSuppressing(_Fixture):

    def test_neg_01_a_blocked_send_does_not_suppress(self):
        ev = self.no_reply_event()
        self.outbound(status="blocked", wamid=None)
        self.assertIn(ev.id, self.selected(),
                      "a blocked message never reached the customer")

    def test_neg_02_a_failed_send_does_not_suppress(self):
        ev = self.no_reply_event()
        self.outbound(status="failed")
        self.assertIn(ev.id, self.selected())

    def test_neg_03_a_pending_send_does_not_suppress(self):
        """`pending` is written BEFORE the Meta call — it is an attempt, not an outcome."""
        ev = self.no_reply_event()
        self.outbound(status="pending", wamid=None)
        self.assertIn(ev.id, self.selected())

    def test_neg_04_a_success_status_without_a_wamid_does_not_suppress(self):
        """Belt and braces: only `mark_sent` sets both together."""
        ev = self.no_reply_event()
        self.outbound(status="sent", wamid=None)
        self.assertIn(ev.id, self.selected())

    def test_neg_05_a_reply_on_another_thread_does_not_suppress(self):
        ev = self.no_reply_event(thread="A")
        self.outbound(thread="B", status="read", offset_seconds=30)
        self.assertIn(ev.id, self.selected(),
                      "answering one customer must never silence another")

    def test_neg_06_an_inbound_message_does_not_suppress(self):
        ev = self.no_reply_event()
        self.db.add(WhatsAppMessage(
            thread_id=self.threads["A"].id, direction="in", status="received",
            timestamp=T0 + dt.timedelta(seconds=30), message_type="text", text="x",
            wa_message_id="wamid.in.later"))
        self.db.commit()
        self.assertIn(ev.id, self.selected())


# ── PRE: R1 and the existing contract are preserved ──────────────────────────

class PreservedBehaviour(_Fixture):

    def test_pre_01_r1_flags_are_unchanged(self):
        out = _out(ACTION_NO_REPLY_PRODUCED, detail="no_reply_text")
        self.assertEqual(out.action, "no_reply_produced")
        self.assertTrue(out.reply_required)
        self.assertFalse(out.reply_produced)
        self.assertTrue(out.alert_eligible)
        self.assertEqual(out.detail, "no_reply_text")

    def test_pre_02_a_successful_reply_event_is_still_excluded(self):
        ev = self._event(_out("replied", wa_message_id="wamid.S"), "A", T0)
        self.assertNotIn(ev.id, self.selected())

    def test_pre_03_an_intentionally_silent_event_is_still_excluded(self):
        for action in ("skipped_human", "skipped_dedup", "no_lead"):
            with self.subTest(action=action):
                ev = self._event(_out(action), "A", T0)
                self.assertNotIn(ev.id, self.selected())

    def test_pre_04_an_already_alerted_event_is_preserved_and_not_repeated(self):
        """History is evidence: suppression must never erase an emitted alert."""
        ev = self.no_reply_event()
        ev.unanswered_alert_sent_at = T0 + dt.timedelta(seconds=200)
        ev.performance_status = "ALERT"
        self.db.commit()
        self.outbound(status="read", offset_seconds=300)   # answered afterwards
        self.assertNotIn(ev.id, self.selected())
        row = self.db.get(AiEvent, ev.id)
        self.assertIsNotNone(row.unanswered_alert_sent_at,
                             "the emitted alert record must survive")
        self.assertEqual(row.performance_status, "ALERT")
        self.assertEqual(row.action, "no_reply_produced")

    def test_pre_05_repeated_evaluation_is_stable(self):
        ev = self.no_reply_event()
        self.assertEqual(self.selected(), self.selected())
        self.outbound(status="sent")
        self.assertEqual(self.selected(), [])
        self.assertNotIn(ev.id, self.selected())

    def test_pre_06_a_duplicate_inbound_cannot_create_a_second_event(self):
        self.no_reply_event()
        with self.assertRaises(Exception):
            ev = AiEvent(thread_id=self.threads["A"].id, wa_message_id="wamid.in.1",
                         event_type="inbound", wa_id="5490000000001", created_at=T0)
            self.db.add(ev)
            self.db.commit()
        self.db.rollback()

    def test_pre_07_a_human_owned_thread_is_still_excluded(self):
        ev = self.no_reply_event()
        state = self.db.query(WhatsAppThreadState).filter_by(
            thread_id=self.threads["A"].id).one()
        state.needs_human = True
        self.db.commit()
        self.assertNotIn(ev.id, self.selected())

    def test_pre_08_the_120_second_threshold_is_unchanged(self):
        self.assertIn("_ALERT_THRESHOLD_SECONDS = 120", ALERT_SOURCE)
        self.assertIn("ae.created_at < NOW() - INTERVAL ':threshold seconds'", ALERT_SOURCE)

    def test_pre_09_the_operational_meaning_is_documented(self):
        self.assertIn("operational flag", ALERT_SOURCE)
        self.assertIn("NOT", ALERT_SOURCE.split("operational flag")[1][:200])

    def test_pre_10_blocked_send_alerting_was_not_redesigned(self):
        """R2 changes selection, not the meaning of blocked dispatch."""
        out = _out("blocked_dispatch", detail="OUTBOUND_GATE_BLOCKED_KILL_SWITCH")
        self.assertTrue(out.reply_required)
        self.assertFalse(out.reply_produced)
        self.assertFalse(out.ok)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
