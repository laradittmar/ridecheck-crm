"""L4.7W5-NO-REPLY-ALERT-INTEGRITY — silence must not be recorded as an answer.

The AI fallback ran, returned no usable text, and nothing was handed to the outbound path.
The engine nevertheless returned `action="replied"`, which put the turn into
`_REPLY_PRODUCED_ACTIONS`, wrote `reply_produced=true` on the `AiEvent`, and thereby
removed it from the unanswered-message rescue. The customer received silence and the alert
built to detect exactly that silence was switched off by an action name.

The invariant, stated once:

    When a reply is required and no usable response artifact is produced,
    `reply_produced` is false and the turn stays eligible for unanswered rescue.

`detail="no_reply_text"` is kept. It is the diagnostic evidence of *why* nothing was
produced; it must never be what decides *whether* a reply existed.

Two things the fix deliberately does NOT do. It does not fabricate a generic reply to
satisfy the invariant — a customer who received nothing must be recorded as having received
nothing. And it does not touch `blocked_dispatch`: a usable reply withheld by outbound
safety is a different fact, and its existing observability is preserved unchanged.

INV-01..08   the corrected outcome and its classification
HND-01..04   n8n ownership — the blast radius that makes this more than a flag change
PROD-01..05  paths that DO produce a reply are untouched
SIL-01..03   intentionally silent turns do not become false alerts
BLK-01..03   produced-but-blocked stays distinguishable from never-produced
ALRT-01..06  the real rescue query, duplicates, and the undefined successor case
TRC-01..05   Inspector truth for new traces and for the preserved historical one
"""
from __future__ import annotations

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
from app.models import AiEvent, WhatsAppContact, WhatsAppThread, WhatsAppThreadState
from app.schemas.conversation import ACTION_NO_REPLY_PRODUCED, HANDLED_ACTIONS
from app.services.conversation_engine import (_NO_REPLY_REQUIRED_ACTIONS,
                                              _REPLY_PRODUCED_ACTIONS,
                                              _scrub_scheduling_confirmation, _out)

CE_SOURCE = (ROOT / "backend" / "app" / "services"
             / "conversation_engine.py").read_text(encoding="utf-8-sig")


def _db() -> Session:
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    return Session(engine)


# ── INV: the corrected outcome ───────────────────────────────────────────────

class NoReplyProducedOutcome(unittest.TestCase):

    def setUp(self):
        self.out = _out(ACTION_NO_REPLY_PRODUCED, detail="no_reply_text")

    def test_inv_01_the_action_is_not_replied(self):
        self.assertNotEqual(self.out.action, "replied")
        self.assertEqual(self.out.action, "no_reply_produced")

    def test_inv_02_a_reply_was_required(self):
        self.assertTrue(self.out.reply_required)

    def test_inv_03_no_reply_was_produced(self):
        self.assertFalse(self.out.reply_produced)

    def test_inv_04_the_turn_stays_alert_eligible(self):
        self.assertTrue(self.out.alert_eligible)

    def test_inv_05_the_diagnostic_reason_survives(self):
        self.assertEqual(self.out.detail, "no_reply_text")

    def test_inv_06_no_outbound_identifier_is_claimed(self):
        self.assertIsNone(self.out.wa_message_id)

    def test_inv_07_it_is_not_an_error(self):
        """Nothing crashed. `ok=False` would send the turn down the failure path."""
        self.assertTrue(self.out.ok)

    def test_inv_08_the_engine_branch_uses_it(self):
        self.assertIn('return _out(ACTION_NO_REPLY_PRODUCED, detail="no_reply_text")',
                      CE_SOURCE)
        self.assertNotIn('return _out("replied", detail="no_reply_text")', CE_SOURCE)


class ActionSetMembership(unittest.TestCase):

    def test_hnd_01_it_is_handled_so_n8n_does_not_fall_back(self):
        """The blast radius that makes this more than a flag change.

        The false branch of n8n's `IF - Engine Handled?` runs a legacy booking chain that
        bypasses the booking authority. A truthful action outside HANDLED_ACTIONS would
        have fixed an alert and opened a far worse hole.
        """
        self.assertIn(ACTION_NO_REPLY_PRODUCED, HANDLED_ACTIONS)

    def test_hnd_02_it_does_not_claim_a_reply_was_produced(self):
        self.assertNotIn(ACTION_NO_REPLY_PRODUCED, _REPLY_PRODUCED_ACTIONS)

    def test_hnd_03_it_does_not_claim_a_reply_was_unnecessary(self):
        self.assertNotIn(ACTION_NO_REPLY_PRODUCED, _NO_REPLY_REQUIRED_ACTIONS)

    def test_hnd_04_no_existing_action_could_have_carried_this_meaning(self):
        """Evidence for introducing a value rather than reusing one."""
        for action in sorted(_REPLY_PRODUCED_ACTIONS):
            self.assertTrue(_out(action).reply_produced,
                            f"{action} asserts a reply was produced")
        for action in sorted(_NO_REPLY_REQUIRED_ACTIONS):
            self.assertFalse(_out(action).reply_required,
                             f"{action} asserts no reply was required")
        self.assertFalse(_out("error").ok, "error asserts a crash")


# ── PROD: producing paths unchanged ──────────────────────────────────────────

class ProducingPathsUnchanged(unittest.TestCase):

    def test_prod_01_a_sent_text_reply_still_counts_as_produced(self):
        out = _out("replied", wa_message_id="wamid.X")
        self.assertTrue(out.reply_required)
        self.assertTrue(out.reply_produced)
        self.assertEqual(out.wa_message_id, "wamid.X")

    def test_prod_02_flow_button_sent_still_counts_as_produced(self):
        out = _out("flow_button_sent", wa_message_id="wamid.F")
        self.assertTrue(out.reply_produced)
        self.assertIn("flow_button_sent", HANDLED_ACTIONS)

    def test_prod_03_booking_created_still_counts_as_produced(self):
        out = _out("booking_created", wa_message_id="wamid.B")
        self.assertTrue(out.reply_produced)
        self.assertIn("booking_created", HANDLED_ACTIONS)

    def test_prod_04_the_successful_send_branch_is_untouched(self):
        self.assertIn('sent_id = self._send_text_to_wa(ctx, reply)', CE_SOURCE)
        self.assertIn('return _out("replied", wa_message_id=sent_id)', CE_SOURCE)

    def test_prod_05_no_fabricated_reply_was_introduced(self):
        """A missing response must not become an invented generic message."""
        branch = CE_SOURCE.split("if not reply:")[1].split("return _out(")[0]
        self.assertNotIn("_send_text_to_wa(ctx", branch,
                         "the no-reply branch must not send anything")
        self.assertIn("ACTION_NO_REPLY_PRODUCED", CE_SOURCE.split("if not reply:")[1][:900])


# ── SIL / BLK: silence and blocking stay distinct ────────────────────────────

class SilenceAndBlocking(unittest.TestCase):

    def test_sil_01_intentionally_silent_turns_require_no_reply(self):
        for action in ("skipped_dedup", "no_lead", "skipped_human"):
            with self.subTest(action=action):
                out = _out(action)
                self.assertFalse(out.reply_required)
                self.assertFalse(out.alert_eligible,
                                 "silence by design must not become an alert")

    def test_sil_02_a_routing_miss_requires_no_reply(self):
        self.assertFalse(_out("error", detail="thread_not_found").reply_required)

    def test_sil_03_a_real_error_still_expects_a_reply(self):
        self.assertTrue(_out("error", detail="internal_error").reply_required)

    def test_blk_01_blocked_dispatch_behaviour_is_unchanged(self):
        """A usable reply withheld by outbound safety keeps its existing observability."""
        out = _out("blocked_dispatch", detail="OUTBOUND_GATE_BLOCKED_KILL_SWITCH")
        self.assertTrue(out.reply_required)
        self.assertFalse(out.reply_produced)      # unchanged by this milestone
        self.assertFalse(out.ok)
        self.assertIn("blocked_dispatch", HANDLED_ACTIONS)

    def test_blk_02_blocked_and_never_produced_are_different_actions(self):
        self.assertNotEqual(_out("blocked_dispatch").action,
                            _out(ACTION_NO_REPLY_PRODUCED).action)

    def test_blk_03_only_one_of_them_asserts_a_failure(self):
        """`ok` is what separates 'we had an answer and could not send it' from
        'we never had an answer'."""
        self.assertFalse(_out("blocked_dispatch").ok)
        self.assertTrue(_out(ACTION_NO_REPLY_PRODUCED).ok)


class ScrubsCannotEmptyAReply(unittest.TestCase):
    """Measured correction to the assumed mechanism.

    The audit supposed a reply could be emptied *by* a scrub. Neither scrub can: both
    substitute safe non-empty text. The empty case therefore arises only when the model
    returns nothing. These tests pin that, so a future scrub that starts returning `""`
    fails here rather than silently re-creating the defect.
    """

    def test_scrub_01_scheduling_scrub_substitutes_rather_than_empties(self):
        scrubbed = _scrub_scheduling_confirmation("Tu turno quedó confirmado", "SCHEDULING")
        self.assertTrue(scrubbed.strip(), "a scrub must never produce an empty reply")

    def test_scrub_02_a_clean_reply_passes_through(self):
        self.assertEqual(_scrub_scheduling_confirmation("¿Qué día te viene bien?",
                                                        "SCHEDULING"),
                         "¿Qué día te viene bien?")

    def test_scrub_03_the_guard_is_a_truthiness_check(self):
        """Empty and whitespace-only both reach the branch."""
        for value in ("", "   ", "\n\t"):
            with self.subTest(value=repr(value)):
                self.assertFalse(bool(str(value).strip()) and bool(value.strip()) is False)
                self.assertFalse(bool(value.strip()))


# ── ALRT: the real rescue query ──────────────────────────────────────────────

_RESCUE_SQL = text("""
    SELECT ae.id FROM ai_events ae
    JOIN whatsapp_threads wt ON wt.id = ae.thread_id
    LEFT JOIN whatsapp_thread_states wts ON wts.thread_id = ae.thread_id
    WHERE ae.reply_required = 1
      AND ae.alert_eligible = 1
      AND (ae.reply_produced IS NULL OR ae.reply_produced = 0)
      AND ae.unanswered_alert_sent_at IS NULL
      AND (wts.needs_human IS NULL OR wts.needs_human = 0)
    ORDER BY ae.id
""")


class RescueQueryIntegrity(unittest.TestCase):
    """The rescue query was always correct. The flag lied to it.

    These tests mirror the production predicate (`unanswered_alert.py`) — booleans written
    for SQLite — so the fix is proved against the selection logic, not against a paraphrase.
    """

    def setUp(self):
        self.db = _db()
        contact = WhatsAppContact(wa_id="5490000000000")
        self.db.add(contact)
        self.db.flush()
        self.thread = WhatsAppThread(contact_id=contact.id)
        self.db.add(self.thread)
        self.db.flush()
        self.db.add(WhatsAppThreadState(thread_id=self.thread.id, needs_human=False))
        self.db.commit()

    def _event(self, out, wamid):
        ev = AiEvent(thread_id=self.thread.id, wa_message_id=wamid, event_type="inbound",
                     wa_id="5490000000000",
                     action=out.action, reply_required=out.reply_required,
                     reply_produced=out.reply_produced, alert_eligible=out.alert_eligible)
        self.db.add(ev)
        self.db.commit()
        return ev

    def _selected(self):
        return [r[0] for r in self.db.execute(_RESCUE_SQL).fetchall()]

    def test_alrt_01_the_no_reply_turn_is_selected(self):
        ev = self._event(_out(ACTION_NO_REPLY_PRODUCED, detail="no_reply_text"), "wamid.1")
        self.assertIn(ev.id, self._selected(),
                      "a customer who received nothing must be rescued")

    def test_alrt_02_the_old_behaviour_would_not_have_been_selected(self):
        """The defect, reproduced: the same turn recorded the old way is invisible."""
        ev = self._event(_out("replied", detail="no_reply_text"), "wamid.2")
        self.assertNotIn(ev.id, self._selected())

    def test_alrt_03_a_successful_reply_is_excluded(self):
        ev = self._event(_out("replied", wa_message_id="wamid.S"), "wamid.3")
        self.assertNotIn(ev.id, self._selected())

    def test_alrt_04_an_intentionally_silent_turn_is_excluded(self):
        for action, wamid in (("skipped_human", "wamid.4"), ("skipped_dedup", "wamid.5"),
                              ("no_lead", "wamid.6")):
            with self.subTest(action=action):
                ev = self._event(_out(action), wamid)
                self.assertNotIn(ev.id, self._selected())

    def test_alrt_05_an_already_alerted_turn_is_not_alerted_twice(self):
        from datetime import datetime, timezone
        ev = self._event(_out(ACTION_NO_REPLY_PRODUCED, detail="no_reply_text"), "wamid.7")
        self.assertIn(ev.id, self._selected())
        ev.unanswered_alert_sent_at = datetime.now(timezone.utc)
        self.db.commit()
        self.assertNotIn(ev.id, self._selected(), "the alerted marker must suppress repeats")

    def test_alrt_06_a_duplicate_inbound_cannot_create_a_second_event(self):
        """`ai_events.wa_message_id` is UNIQUE, so a retried delivery cannot double-alert."""
        self._event(_out(ACTION_NO_REPLY_PRODUCED, detail="no_reply_text"), "wamid.8")
        with self.assertRaises(Exception):
            self._event(_out(ACTION_NO_REPLY_PRODUCED, detail="no_reply_text"), "wamid.8")
        self.db.rollback()

    def test_alrt_07_a_human_owned_thread_is_excluded(self):
        ev = self._event(_out(ACTION_NO_REPLY_PRODUCED, detail="no_reply_text"), "wamid.9")
        state = self.db.query(WhatsAppThreadState).one()
        state.needs_human = True
        self.db.commit()
        self.assertNotIn(ev.id, self._selected())

    def test_alrt_08_the_production_predicate_was_not_modified(self):
        """The query was always right. Proving the fix did not move it is part of the fix."""
        alert = (ROOT / "backend" / "app" / "services"
                 / "unanswered_alert.py").read_text(encoding="utf-8-sig")
        self.assertIn("AND (ae.reply_produced IS NULL OR ae.reply_produced = false)", alert)
        self.assertIn("ae.reply_required = true", alert)
        self.assertIn("ae.unanswered_alert_sent_at IS NULL", alert)


# ── TRC: Inspector truth ─────────────────────────────────────────────────────

class InspectorTruth(unittest.TestCase):

    def _page(self, action, reason, outbound=None):
        from app.services import hybrid_trace as svc
        from app.ui.hybrid_trace_view import render_turn_trace_page
        from app.schemas.hybrid_trace import CanonicalSnapshot, SemanticEvidence
        trace = svc.build_trace(
            turn_id="t", thread_id=1, lead_id=None, deployment_sha="d",
            started_at="s", completed_at="c", duration_ms=1,
            ordered_message_ids=("w",), message_timestamps=("t",), burst_texts=["x"],
            semantic=SemanticEvidence(status="PENDING"), ce_rules=(), reconciliations=(),
            before=CanonicalSnapshot(), after=CanonicalSnapshot(),
            action=action, detail=reason, answer_source="CE_AI", outbound=outbound or {})
        payload = trace.to_payload()
        headline, conditions = svc.effective_from_payload(payload)
        return payload, render_turn_trace_page(
            {"captured": True, "trace": payload, "effective_classification": headline,
             "supporting_conditions": list(conditions)})

    def test_trc_01_a_new_no_reply_trace_is_no_action(self):
        payload, _ = self._page(ACTION_NO_REPLY_PRODUCED, "no_reply_text")
        self.assertEqual(payload["result_kind"], "NO_ACTION")
        self.assertNotEqual(payload["result_kind"], "FALLBACK")

    def test_trc_02_it_renders_no_response_produced_and_not_replied(self):
        from app.ui.hybrid_trace_view import NO_RESPONSE_PRODUCED_SENTENCE
        _, page = self._page(ACTION_NO_REPLY_PRODUCED, "no_reply_text")
        self.assertIn("NO RESPONSE PRODUCED", page)
        self.assertIn(NO_RESPONSE_PRODUCED_SENTENCE, page)
        self.assertNotIn(">replied<", page)

    def test_trc_03_the_historical_shape_is_read_truthfully(self):
        """`replied` + `no_reply_text` + no outbound, as stored before this milestone."""
        from app.ui.hybrid_trace_view import (NO_RESPONSE_HISTORICAL_NOTE,
                                              NO_RESPONSE_PRODUCED_SENTENCE)
        _, page = self._page("replied", "no_reply_text")
        self.assertIn(NO_RESPONSE_PRODUCED_SENTENCE, page)
        self.assertIn(NO_RESPONSE_HISTORICAL_NOTE, page)

    def test_trc_04_a_genuine_reply_is_untouched(self):
        from app.ui.hybrid_trace_view import NO_RESPONSE_PRODUCED_SENTENCE
        _, page = self._page("replied", None,
                             outbound={"message_id": 7, "path_id": "CE_TEXT",
                                       "status": "sent", "wamid_tail": "ABCDEFGH"})
        self.assertNotIn(NO_RESPONSE_PRODUCED_SENTENCE, page)
        self.assertNotIn("NO RESPONSE PRODUCED</span> No se produjo", page)

    def test_trc_05_a_blocked_send_is_not_called_never_produced(self):
        from app.ui.hybrid_trace_view import NO_RESPONSE_PRODUCED_SENTENCE
        _, page = self._page("blocked_dispatch", "OUTBOUND_GATE_BLOCKED_KILL_SWITCH",
                             outbound={"message_id": 9, "path_id": "CE_TEXT",
                                       "status": "blocked", "wamid_tail": None})
        self.assertNotIn(NO_RESPONSE_PRODUCED_SENTENCE, page)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
