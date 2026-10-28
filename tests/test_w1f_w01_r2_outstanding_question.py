"""W1F-W-01 R2 — the outstanding-question contract and cross-turn evidence retention.

The Wild, in three turns: the bot answered FAQ questions and its generated reply asked where
the vehicle was; the customer replied `en flores`; the interpreter read
`INSPECTION_LOCATION = Flores, CONFIRMED` — and the turn exited at Layer F step 9 and sent
the generic qualification greeting, because **nothing in the system knew a question had been
asked**. R1 made the answer survive. R2 makes it an *answer*.

Two rules govern this module, and every test below is one of them:

    A question becomes ACTIVE because Meta confirmed DELIVERED or READ.
    A question closes because ADMITTED EVIDENCE answered it.

(`delivered` means the recipient's device has the message. It is not proof a human looked
at it — only `read` would be — and nothing here claims the customer *saw* a delivered-only
message.)

So the delivery half is tested by taking delivery away (blocked gate, Meta failure, empty
composition, duplicate callback) and the closing half by offering everything that is NOT
admitted evidence for that question — the wrong field, the wrong candidate, a stale cycle,
an ambiguous, unsupported, negated or inferred locality, and a model that simply says the
customer answered.

Groups:
  * `TestR2A_Contract`   the two services as units — identity, states, matching, expiry;
  * `TestR2B_Wild`       the exact three-turn regression, turn by turn, flags off and on;
  * `TestR2C_Delivery`   a question is recorded only when the customer received it;
  * `TestR2D_Closure`    what closes a question, and the longer list of what must not;
  * `TestR2E_CrossTurn`  evidence given before the candidate existed (W1F-C-05/C-07/C-22);
  * `TestR2F_Form`       W1FRV-04: the Location Flow goes through the reconciler;
  * `TestR2G_Wait`       R1-PL-01, by the PASS-B method: `buenas` does not wait;
  * `TestR2H_Flags`      the R1/R2/R3 matrix, and the fail-closed partial combination;
  * `TestR2I_NoAuthority` answering a locality question authorises nothing commercial;
  * `TestR2J_Structural` the rules a reviewer should not have to take on trust;
  * `TestR2K_Migration` the additive migration and the two-connection races, on PostgreSQL;
  * `TestR2L_Boundaries` crashes, retries and concurrent closures;
  * `TestR2M_Delivery`  OC-R2: acceptance is not delivery;
  * `TestR2N_Ordering`  OC-R2-R1: a late delivery never replaces a newer question;
  * `TestR2P_Incumbent` OC-R2-R3: only a real activation deactivates an incumbent.

Only two things are simulated, and they are the two that must never be real in a test: the
reply model's HTTP call (patched at `urllib.request.urlopen`) and transport
(`_send_whatsapp_cloud_*` captured, `OutboundSafetyGate` mocked), so no `whatsapp_messages`
row is written and no provider is contacted anywhere.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

#: The certified R3 full-turn harness, loaded as a module rather than copied — the same
#: discipline the R1 module uses. It owns the SQLite engine, the `app.db` stub, the
#: catalogue fixture, the thread seeder, the engine builder and the turn driver, and it arms
#: `OUTBOUND_ENABLED` per turn with `patch.dict`.
_HARNESS_PATH = Path(__file__).resolve().parent / "test_w1f_w01_r3_r1_fullturn_ambiguity.py"
_spec = importlib.util.spec_from_file_location("_r2_fullturn_harness", _HARNESS_PATH)
H = importlib.util.module_from_spec(_spec)
sys.modules["_r2_fullturn_harness"] = H
_spec.loader.exec_module(H)

from app.models import (OQ_ABANDONED, OQ_ANSWERED, OQ_ASKED_BY_DETERMINISTIC_TEMPLATE,  # noqa: E402
                        OQ_ASKED_BY_REPLY_MODEL, OQ_KIND_INSPECTION_LOCATION,
                        OQ_KIND_VEHICLE_IDENTITY, OQ_OUTSTANDING, OQ_PENDING_DELIVERY,
                        OQ_SUPERSEDED, OQ_UNDELIVERED, WhatsAppMessage,
                        WhatsAppOutstandingQuestion, WhatsAppThreadCandidate,
                        WhatsAppThreadEvidence, WhatsAppThreadState)
from app.schemas.claims import ClaimType, ReconciliationOutcome                    # noqa: E402
from app.schemas.turn_evidence import (EvidenceStatus, LocationEvidence,           # noqa: E402
                                       LocationRole, TurnEvidence, TurnRef)
from app.services import evidence_admission as EA                                  # noqa: E402
from app.services import outstanding_question as OQ                                # noqa: E402
from app.services import thread_evidence as TE                                     # noqa: E402
from app.services.conversation_engine import ConversationEngine                    # noqa: E402

CE_SOURCE = (BACKEND_DIR / "app" / "services" / "conversation_engine.py").read_text(
    encoding="utf-8")
_GREETING_MARK = "nos especializamos en revisiones pre-compra"

#: A model reply that asks where the vehicle is — the Wild's turn 1, whose question came
#: from the reply model's own prose and from no proposition at all.
_ASKS_LOCATION = json.dumps({
    "intent": "QUALIFYING",
    "reply": "La revisión incluye más de 100 puntos de control. ¿En qué zona está el auto?",
    "deferred_interest": False, "candidate": {"action": "none"}, "extracted": {},
    "lead_flag": None, "needs_human": False})
_NEUTRAL = H._AI_PAYLOAD
_FAQ_TEXT = "¿de qué consta la revisión?"


# ── helpers ─────────────────────────────────────────────────────────────────────
def _evidence(*mentions, interpreter="semantic:understand/1.20", message_ids=()):
    return TurnEvidence(
        interpreter=interpreter, model_version="1.20",
        turn=TurnRef(ordered_message_ids=tuple(message_ids)),
        location_mentions=tuple(mentions))


def _loc(locality, role=LocationRole.INSPECTION_LOCATION.value,
         status=EvidenceStatus.CONFIRMED):
    return LocationEvidence(locality=locality, role=role, status=status)


class _Thread:
    """One conversation, driven turn by turn, with the R2 rows it produced."""

    def __init__(self, *, r1=True, r2=True, safety=True, vehicle=False, zone=None,
                 stage=None, flag=None):
        self.db = H._SessionLocal()
        H._seed_catalogue(self.db)
        self.thread_id, self.cand_id, self.wa = H._seed_thread(
            self.db, vehicle=vehicle, zone=zone,
            stage=stage or H.STAGE_QUALIFYING, flag=flag)
        self.engine = H._engine(self.db, safety=safety)
        self.engine.settings.evidence_admission_enabled = r1
        self.engine.settings.outstanding_question_enabled = r2
        self.r1, self.r2, self.safety = r1, r2, safety

    # ── OC-R2 CORRECTION 1: delivery is simulated the way Meta reports it ─────
    def deliver(self, *, status="delivered", wamid=None):
        """Replay a Meta status webhook for this thread's pending question(s).

        The real path is `routes/whatsapp.py` → `activate_on_status`, behind the signed
        webhook; this calls the same function with the same arguments, so the tests exercise
        the production transition and not a test-only shortcut."""
        rows = ([r for r in self.questions() if r.wa_message_id == wamid] if wamid
                else [r for r in self.questions() if r.status == OQ_PENDING_DELIVERY])
        changed = 0
        for row in rows:
            changed += OQ.activate_on_status(self.db, wa_message_id=row.wa_message_id,
                                             status=status)
        # COMMITTED, because the real activation happens in the webhook's own transaction,
        # not in the turn's. The crash tests roll the TURN back and the question must
        # survive that, exactly as it would in production.
        self.db.commit()
        self.db.expire_all()
        return changed

    def ask_and_deliver(self, text=_FAQ_TEXT, *, status="delivered"):
        """The common fixture: ask the location question and have Meta confirm it."""
        turn = self.turn(text, payload=_ASKS_LOCATION)
        self.deliver(status=status)
        return turn

    def turn(self, text, *, evidence=None, payload=_NEUTRAL):
        previous, H._AI_PAYLOAD = H._AI_PAYLOAD, payload
        try:
            with patch.object(ConversationEngine, "_semantic_turn_evidence",
                              return_value=evidence):
                turn = H._run(self.db, self.engine, self.thread_id, self.wa, text,
                              cand_id=self.cand_id)
        finally:
            H._AI_PAYLOAD = previous
        self._uniquify_wamids()
        return turn

    def _uniquify_wamids(self) -> None:
        """Give this fixture's accepted questions globally unique WAMIDs.

        A fixture detail with a real reason. The certified R3 harness's fake transport
        returns `fake-text-N` numbered per TURN, so the first send of every turn on every
        thread gets `fake-text-1`. Real WAMIDs are globally unique and `activate_on_status`
        correctly looks a question up by WAMID alone — so without this, one thread's
        delivery callback would activate another thread's question **in the test only**,
        and `test_m09` could not tell a genuine cross-thread leak from a harness artefact.
        The committed harness is not modified; the collision is repaired here, where it is
        created.
        """
        self.db.flush()
        for row in self.db.query(WhatsAppOutstandingQuestion).filter_by(
                thread_id=self.thread_id).all():
            if row.wa_message_id and not row.wa_message_id.startswith("wamid."):
                row.wa_message_id = f"wamid.T{self.thread_id}M{row.id}"
                # The identity is derived FROM the WAMID, so it has to follow it. Without
                # this, a second turn that asks the same field recomputes
                # `uuid5(..., 'fake-text-1')`, matches the first turn's stored identity and
                # is treated as the same ask — so the fixture, not the engine, would be
                # deciding that a re-ask produces no second row. Repaired here, where the
                # collision is created, exactly as the WAMID itself is.
                row.identity = OQ.identity(row.thread_id, row.cycle_key,
                                           row.question_kind, row.wa_message_id)
        self.db.flush()

    # ── what the conversation now holds ──────────────────────────────────────
    def questions(self, **kw):
        self.db.expire_all()
        q = self.db.query(WhatsAppOutstandingQuestion).filter_by(
            thread_id=self.thread_id, **kw)
        return q.order_by(WhatsAppOutstandingQuestion.id).all()

    def evidence_rows(self):
        self.db.expire_all()
        return self.db.query(WhatsAppThreadEvidence).filter_by(
            thread_id=self.thread_id).order_by(WhatsAppThreadEvidence.id).all()

    def candidates(self):
        self.db.expire_all()
        return self.db.query(WhatsAppThreadCandidate).filter_by(
            thread_id=self.thread_id).order_by(WhatsAppThreadCandidate.id).all()

    def state(self):
        self.db.expire_all()
        return self.db.query(WhatsAppThreadState).filter_by(
            thread_id=self.thread_id).one()

    def zone(self):
        st = self.state()
        return (st.home_zone_group, st.home_zone_detail)

    def cycle(self):
        return self.engine._r2_cycle_key(self.state())

    def close(self):
        self.db.close()


def _ledger(t, wamid: str, status: str) -> WhatsAppMessage:
    """The outbound ledger row the safety gate would have written, at a given status.

    Any existing row for that WAMID is removed first. `whatsapp_messages.wa_message_id` is
    globally UNIQUE — correctly, since a WAMID identifies one Meta message — and the
    certified harness's fake transport returns `fake-text-1` for the first send of every
    turn, so two tests that both need to pre-seed "the ledger already holds a status for the
    message this turn will send" would otherwise collide on it.
    """
    existing = t.db.query(WhatsAppMessage).filter_by(wa_message_id=wamid).all()
    for row in existing:
        t.db.delete(row)
    if existing:
        t.db.flush()
    return WhatsAppMessage(
        thread_id=t.thread_id, direction="out", text="¿En qué zona está el auto?",
        wa_message_id=wamid, status=status, timestamp=datetime.now(timezone.utc),
        automated=True)


def _drop_ledger(t, wamid: str) -> None:
    """Remove a seeded ledger row so it cannot reach a later test.

    Necessary because the certified harness's fake transport returns `fake-text-1` for the
    first send of EVERY turn, and the engine's reconciliation correctly consults the ledger
    by WAMID — so a row left behind would silently pre-deliver (or pre-fail) another test's
    question. The leak is the fixture's, not the engine's, and it is cleaned up where it is
    created.
    """
    try:
        for row in t.db.query(WhatsAppMessage).filter_by(wa_message_id=wamid).all():
            t.db.delete(row)
        t.db.commit()
    except Exception:
        t.db.rollback()


def _record(db, thread_id, *, kind=OQ_KIND_INSPECTION_LOCATION, cycle=0,
            asked_by=OQ_ASKED_BY_REPLY_MODEL, wamid="wamid.OUT1", **kw):
    """Record an ACCEPTED question. It is `PENDING_DELIVERY` and owns nothing."""
    return OQ.record_asked(db, thread_id=thread_id, cycle_key=cycle, kind=kind,
                           asked_by=asked_by, wa_message_id=wamid, **kw)


def _record_live(db, thread_id, *, status="delivered", **kw):
    """Record a question AND have Meta confirm delivery — an ACTIVE question.

    OC-R2 CORRECTION 1: these are two separate facts now, so a test that needs a live
    question says so explicitly. `status` lets a test use `read` instead of `delivered`.
    """
    row, created = _record(db, thread_id, **kw)
    if row is not None:
        OQ.activate_on_status(db, wa_message_id=row.wa_message_id, status=status)
        db.flush()
        db.expire_all()
    return row, created


def _admitted(claim_type=ClaimType.INSPECTION_LOCATION, *, value="Flores",
              normalized=("CABA", "Flores"), claim_id="claim-1"):
    """One ACCEPTED `AdmissionRecord`, built by hand so the matching rules are readable."""
    return EA.AdmissionRecord(
        claim_type=claim_type, value=value,
        outcome=ReconciliationOutcome.ACCEPT.value, reason=EA.AdmissionReason.ADMITTED,
        normalized=normalized,
        provenance=EA.Provenance(producer="semantic:understand/1.20",
                                 claim_id=claim_id, cycle_id="0"))


def _refused(claim_type=ClaimType.INSPECTION_LOCATION, *,
             reason=EA.AdmissionReason.LOCALITY_AMBIGUOUS, outcome=None):
    return EA.AdmissionRecord(
        claim_type=claim_type, value="san nicolas",
        outcome=outcome or ReconciliationOutcome.CLARIFY.value, reason=reason,
        provenance=EA.Provenance(producer="semantic:understand/1.20", claim_id="claim-r"))


# ══════════════════════════════════════════════════════════════════════════════
class TestR2A_Contract(unittest.TestCase):
    """The two services as units: identity, the state machine, matching, expiry."""

    def setUp(self):
        self.db = H._SessionLocal()
        H._seed_catalogue(self.db)
        self.thread_id, _, _ = H._seed_thread(self.db, vehicle=False)

    def tearDown(self):
        self.db.close()

    def test_a01_identity_is_deterministic_and_carries_no_customer_data(self):
        """The same ask always yields the same identity; a different one never collides."""
        a = OQ.identity(7, 0, OQ_KIND_INSPECTION_LOCATION, "wamid.A")
        self.assertEqual(a, OQ.identity(7, 0, OQ_KIND_INSPECTION_LOCATION, "wamid.A"))
        self.assertEqual(36, len(a))
        for other in (OQ.identity(8, 0, OQ_KIND_INSPECTION_LOCATION, "wamid.A"),
                      OQ.identity(7, 9, OQ_KIND_INSPECTION_LOCATION, "wamid.A"),
                      OQ.identity(7, 0, OQ_KIND_VEHICLE_IDENTITY, "wamid.A"),
                      OQ.identity(7, 0, OQ_KIND_INSPECTION_LOCATION, "wamid.B")):
            with self.subTest(other=other):
                self.assertNotEqual(a, other)

    def test_a02_no_delivery_evidence_means_no_row(self):
        """The single guard behind "a question the customer never saw is not a question"."""
        for wamid in (None, "", 0):
            with self.subTest(wa_message_id=wamid):
                row, created = _record(self.db, self.thread_id, wamid=wamid)
                self.assertIsNone(row)
                self.assertFalse(created)
        self.assertEqual(0, self.db.query(WhatsAppOutstandingQuestion).count())

    def test_a03_the_same_delivery_seen_twice_opens_one_question(self):
        first, created_1 = _record(self.db, self.thread_id)
        second, created_2 = _record(self.db, self.thread_id)
        self.assertEqual(OQ_PENDING_DELIVERY, first.status,
                         "acceptance created an ACTIVE question")
        self.assertTrue(created_1)
        self.assertFalse(created_2, "a duplicate callback opened a second question")
        self.assertEqual(first.id, second.id)
        self.assertEqual(1, self.db.query(WhatsAppOutstandingQuestion).count())

    def test_a04_a_re_ask_supersedes_explicitly_and_leaves_one_live_row(self):
        """Supersession is a WRITTEN state, never inferred from a missing row.

        OC-R2 CORRECTION 1 moved WHEN it happens: supersession is applied at ACTIVATION,
        not at acceptance. A re-ask that Meta has not yet delivered must not displace a
        question the customer demonstrably received — otherwise a message that never
        arrives could silently retire a live contract."""
        first, _ = _record(self.db, self.thread_id, wamid="wamid.OUT1")
        OQ.activate_on_status(self.db, wa_message_id="wamid.OUT1", status="delivered")
        second, created = _record(self.db, self.thread_id, wamid="wamid.OUT2")
        self.assertTrue(created)
        self.db.expire_all()
        self.assertEqual(OQ_OUTSTANDING, first.status,
                         "an undelivered re-ask displaced a delivered question")
        OQ.activate_on_status(self.db, wa_message_id="wamid.OUT2", status="delivered")
        self.db.expire_all()
        self.assertEqual(OQ_SUPERSEDED, first.status)
        self.assertEqual(OQ.CloseReason.SUPERSEDED_BY_REASK, first.closed_reason)
        self.assertEqual(second.id, first.superseded_by_id)
        self.assertIsNotNone(first.closed_at)
        live = OQ.live(self.db, thread_id=self.thread_id, cycle_key=0)
        self.assertEqual([second.id], [q.id for q in live],
                         "two questions owned the same field at once")

    def test_a05_two_kinds_may_be_outstanding_at_the_same_time(self):
        """The reason §8.1 is a table and not a column: the Wild had the location question
        open while the vehicle was still unknown."""
        loc, _ = _record(self.db, self.thread_id, kind=OQ_KIND_INSPECTION_LOCATION,
                         wamid="wamid.L")
        veh, _ = _record(self.db, self.thread_id, kind=OQ_KIND_VEHICLE_IDENTITY,
                         wamid="wamid.V")
        for wamid in ("wamid.L", "wamid.V"):
            OQ.activate_on_status(self.db, wa_message_id=wamid, status="delivered")
        live = OQ.live(self.db, thread_id=self.thread_id, cycle_key=0)
        self.assertEqual({loc.id, veh.id}, {q.id for q in live})
        self.assertEqual([veh.id], [q.id for q in OQ.live(
            self.db, thread_id=self.thread_id, cycle_key=0,
            kind=OQ_KIND_VEHICLE_IDENTITY)])

    def test_a06_expected_slots_are_stored_on_the_row(self):
        row, _ = _record(self.db, self.thread_id)
        self.assertIn(ClaimType.INSPECTION_LOCATION, row.expected_slots)
        veh, _ = _record(self.db, self.thread_id, kind=OQ_KIND_VEHICLE_IDENTITY,
                         wamid="wamid.V")
        self.assertIn(ClaimType.VEHICLE_MAKE, veh.expected_slots)
        self.assertNotIn(ClaimType.INSPECTION_LOCATION, veh.expected_slots)

    def test_a07_matching_accepts_only_admitted_evidence_for_this_field(self):
        row, _ = _record(self.db, self.thread_id)
        OQ.activate_on_status(self.db, wa_message_id=row.wa_message_id,
                              status="delivered")
        self.db.expire_all()
        record, why = OQ.match(row, [_admitted()], cycle_key=0)
        self.assertIsNotNone(record)
        self.assertIsNone(why)

    def test_a08_every_non_match_is_a_named_reason(self):
        """Each row here is one of the cases the milestone enumerates."""
        row, _ = _record_live(self.db, self.thread_id, candidate_id=None)
        cases = (
            ("no admitted evidence at all", [], 0, None, OQ.NoMatch.NO_ADMITTED_EVIDENCE),
            ("a refusal is never a match", [_refused()], 0, None, OQ.NoMatch.NOT_ADMITTED),
            ("wrong field", [_admitted(ClaimType.VEHICLE_MAKE)], 0, None,
             OQ.NoMatch.WRONG_FIELD),
            ("stale cycle", [_admitted()], 99, None, OQ.NoMatch.STALE_CYCLE),
        )
        for label, records, cycle, cand, expected in cases:
            with self.subTest(case=label):
                record, why = OQ.match(row, records, cycle_key=cycle, candidate_id=cand)
                self.assertIsNone(record)
                self.assertEqual(expected, why)

    def test_a09_a_question_about_one_candidate_is_not_answered_for_another(self):
        row, _ = _record_live(self.db, self.thread_id, candidate_id=11)
        record, why = OQ.match(row, [_admitted()], cycle_key=0, candidate_id=22)
        self.assertIsNone(record)
        self.assertEqual(OQ.NoMatch.WRONG_CANDIDATE, why)
        record, why = OQ.match(row, [_admitted()], cycle_key=0, candidate_id=11)
        self.assertIsNotNone(record)

    def test_a10_an_already_closed_question_cannot_be_matched_again(self):
        row, _ = _record_live(self.db, self.thread_id)
        self.assertTrue(OQ.close_answered(self.db, row, claim_id="claim-1"))
        record, why = OQ.match(row, [_admitted()], cycle_key=0)
        self.assertIsNone(record)
        self.assertEqual(OQ.NoMatch.ALREADY_CLOSED, why)

    def test_a11_closure_is_idempotent_and_the_second_caller_loses(self):
        """Two workers answering the same question: one close, one refusal, one record of
        who closed it. The pattern `offer_lifecycle` uses for acceptance."""
        row, _ = _record_live(self.db, self.thread_id)
        self.assertTrue(OQ.close_answered(self.db, row, claim_id="winner"))
        self.assertFalse(OQ.close_answered(self.db, row, claim_id="loser"))
        self.db.expire_all()
        self.assertEqual(OQ_ANSWERED, row.status)
        self.assertEqual("winner", row.answered_by_claim_id,
                         "the loser overwrote the winner's provenance")

    def test_a12_expiry_is_counted_in_turns_and_is_written(self):
        row, _ = _record_live(self.db, self.thread_id)
        self.assertEqual(3, row.expires_after_turns)
        for turn in (1, 2):
            expired = OQ.observe_turn(self.db, thread_id=self.thread_id, cycle_key=0)
            self.assertEqual([], expired, f"expired too early on turn {turn}")
            self.assertEqual(OQ_OUTSTANDING, row.status)
        expired = OQ.observe_turn(self.db, thread_id=self.thread_id, cycle_key=0)
        self.assertEqual([row.id], [q.id for q in expired])
        self.db.expire_all()
        self.assertEqual(OQ_ABANDONED, row.status)
        self.assertEqual(OQ.CloseReason.EXPIRED_TURNS, row.closed_reason)

    def test_a13_a_question_survives_one_off_topic_turn(self):
        """`W1F-C-11`: bot asks location → `cuánto sale?` → `en flores`. The floor on the
        lifetime exists so this cannot regress to a one-turn window."""
        row, _ = _record_live(self.db, self.thread_id, expires_after_turns=1)
        self.assertGreaterEqual(row.expires_after_turns, 2)
        OQ.observe_turn(self.db, thread_id=self.thread_id, cycle_key=0)
        self.assertEqual(OQ_OUTSTANDING, row.status)
        record, why = OQ.match(row, [_admitted()], cycle_key=0)
        self.assertIsNotNone(record, f"the question did not survive one turn ({why})")

    def test_a14_a_question_from_another_cycle_is_not_live(self):
        row, _ = _record_live(self.db, self.thread_id, cycle=0)
        self.assertEqual([], OQ.live(self.db, thread_id=self.thread_id, cycle_key=5))
        self.assertEqual([row.id], [q.id for q in OQ.live(
            self.db, thread_id=self.thread_id, cycle_key=0)])

    def test_a15_a_question_from_another_thread_is_not_live(self):
        other_id, _, _ = H._seed_thread(self.db, vehicle=False)
        _record_live(self.db, self.thread_id)
        self.assertEqual([], OQ.live(self.db, thread_id=other_id, cycle_key=0))
        self.assertEqual(1, len(OQ.live(self.db, thread_id=self.thread_id, cycle_key=0)))

    def test_a16_an_unknown_kind_or_provenance_is_refused(self):
        for kw in ({"kind": "WHATEVER"}, {"asked_by": "THE_INTERN"}):
            with self.subTest(**kw):
                row, created = _record(self.db, self.thread_id, **kw)
                self.assertIsNone(row)
                self.assertFalse(created)


# ══════════════════════════════════════════════════════════════════════════════
class TestR2B_Wild(unittest.TestCase):
    """The exact three-turn Wild regression. This is the milestone's reason to exist."""

    def _run_wild(self, *, r1, r2):
        t = _Thread(r1=r1, r2=r2)
        out = {}
        out["t1"] = t.turn(_FAQ_TEXT, payload=_ASKS_LOCATION)
        out["q_accepted"] = [(q.question_kind, q.asked_by, q.status)
                             for q in t.questions()]
        # OC-R2 CORRECTION 1: Meta confirms the customer received it. Until this, the
        # question is PENDING_DELIVERY and owns nothing.
        t.deliver(status="delivered")
        out["q_after_1"] = [(q.question_kind, q.asked_by, q.status) for q in t.questions()]
        out["t2"] = t.turn("en flores", evidence=_evidence(_loc("Flores")))
        out["zone_after_2"] = t.zone()
        out["q_after_2"] = [(q.question_kind, q.status, q.closed_reason)
                            for q in t.questions()]
        out["t3"] = t.turn("es un toyota corolla 2024")
        out["cands"] = [(c.marca, c.modelo, c.zone_group, c.zone_detail,
                         c.zone_evidence_id) for c in t.candidates()]
        out["thread"] = t
        return out

    def test_b01_turn_one_records_the_question_the_model_actually_asked(self):
        """`asked_by = REPLY_MODEL`. There is no proposition to include on this turn: the
        clarification producer needs a focus candidate with a `tipo_vehiculo`, and turn 1
        has no candidate at all — the location question is the model's own sentence."""
        r = self._run_wild(r1=True, r2=True)
        self.addCleanup(r["thread"].close)
        self.assertIn("¿En qué zona está el auto?", " ".join(r["t1"].sent))
        self.assertEqual([(OQ_KIND_INSPECTION_LOCATION, OQ_ASKED_BY_REPLY_MODEL,
                           OQ_OUTSTANDING)], r["q_after_1"])

    def test_b02_turn_two_answers_it_and_the_greeting_is_not_sent(self):
        """The defect, closed. `en flores` used to receive the generic qualification
        greeting — three times in one conversation."""
        r = self._run_wild(r1=True, r2=True)
        self.addCleanup(r["thread"].close)
        self.assertFalse(any(_GREETING_MARK in s for s in r["t2"].sent),
                         "the qualification greeting was still sent")
        self.assertEqual(("CABA", "Flores"), r["zone_after_2"])
        self.assertEqual([(OQ_KIND_INSPECTION_LOCATION, OQ_ANSWERED,
                           OQ.CloseReason.ANSWERED_BY_EVIDENCE)], r["q_after_2"])

    def test_b03_turn_two_does_not_ask_for_the_location_again(self):
        r = self._run_wild(r1=True, r2=True)
        self.addCleanup(r["thread"].close)
        text = " ".join(r["t2"].sent + [f["body"] or "" for f in r["t2"].flows]).lower()
        for probe in ("en qué zona", "en que zona", "localidad", "barrio", "dónde está"):
            with self.subTest(probe=probe):
                self.assertNotIn(probe, text)

    def test_b04_turn_three_inherits_the_locality_through_typed_evidence(self):
        """`W1F-C-07`: the candidate created on turn 3 carries the locality given on turn 2,
        and `zone_evidence_id` says which row put it there."""
        r = self._run_wild(r1=True, r2=True)
        self.addCleanup(r["thread"].close)
        self.assertEqual(1, len(r["cands"]))
        marca, modelo, group, detail, evidence_id = r["cands"][0]
        self.assertEqual(("Toyota", "Corolla", "CABA", "Flores"),
                         (marca, modelo, group, detail))
        self.assertIsNotNone(evidence_id, "the candidate carries no evidence provenance")

    def test_b05_turn_three_quotes_and_never_asks_for_flores_again(self):
        r = self._run_wild(r1=True, r2=True)
        self.addCleanup(r["thread"].close)
        self.assertTrue(any("Flores" in s for s in r["t3"].sent))
        self.assertTrue(r["t3"].pricing, "the quote journey did not continue")
        self.assertEqual([], r["t3"].attempts,
                         "something asked for a fact already admitted")

    def test_b06_the_committed_system_still_fails_the_same_way(self):
        """The regression oracle. With the flags off, every defect is reproduced exactly —
        which is what makes the comparison above evidence rather than assertion."""
        r = self._run_wild(r1=False, r2=False)
        self.addCleanup(r["thread"].close)
        self.assertEqual([], r["q_after_1"])
        self.assertTrue(any(_GREETING_MARK in s for s in r["t2"].sent))
        self.assertEqual((None, None), r["zone_after_2"])
        self.assertEqual([(None, None)], [(c[2], c[3]) for c in r["cands"]])

    def test_b07_r1_alone_is_inert_under_the_interlock(self):
        """OC-R2 CORRECTION 2, measured on the Wild itself.

        This test used to assert the honest half of R1 — "R1 alone keeps the locality and
        still greets" — and that was true of the posture R1 shipped with. The owner then
        showed the posture itself is unsafe: R1 ON with R2 OFF admits `la boca` out of
        `me mordí la boca`. So R1 and R2 are now one interlocked switch, and R1 alone does
        **nothing**: the locality is not kept, no question is recorded, and the turn is the
        committed pre-R1 turn. The integrated posture is where the Wild is fixed
        (`b01`–`b05`), and the eight-row matrix in `TestR2H_Flags` is where every partial
        posture is proven inert."""
        r = self._run_wild(r1=True, r2=False)
        self.addCleanup(r["thread"].close)
        self.assertEqual((None, None), r["zone_after_2"],
                         "a partial posture admitted a locality")
        self.assertTrue(any(_GREETING_MARK in s for s in r["t2"].sent))
        self.assertEqual([], r["q_after_1"])
        self.assertEqual([], r["thread"].evidence_rows())

    def test_b08_the_interruption_case_survives_one_off_topic_turn(self):
        """`W1F-C-11`: bot asks location → `cuánto sale?` → `en flores`."""
        t = _Thread()
        self.addCleanup(t.close)
        t.ask_and_deliver()
        t.turn("cuánto sale?")
        self.assertEqual([OQ_OUTSTANDING], [q.status for q in t.questions()])
        t2 = t.turn("en flores", evidence=_evidence(_loc("Flores")))
        self.assertEqual([OQ_ANSWERED], [q.status for q in t.questions()])
        self.assertEqual(("CABA", "Flores"), t.zone())
        self.assertFalse(any(_GREETING_MARK in s for s in t2.sent))


# ══════════════════════════════════════════════════════════════════════════════
class TestR2C_Delivery(unittest.TestCase):
    """A question exists because Meta ACCEPTED the message. Every way that can fail."""

    def test_c01_a_blocked_dispatch_leaves_no_question(self):
        """The gate refused to send. Nothing reached the customer, so nothing is
        outstanding — and the next turn must not have a greeting suppressed by a question
        that was never asked.

        Driven at the transport boundary itself, because that is where the decision lives:
        the gate returns a non-ALLOWED outcome, `_send_text_to_wa` raises
        `OutboundBlockedError` before the Meta call, and the recorder is never reached."""
        from app.services.conversation_engine import GateOutcome, OutboundBlockedError
        t = _Thread()
        self.addCleanup(t.close)
        ctx = t.engine._load_context(t.thread_id)
        t.engine._live_plan = None
        with patch.dict(os.environ, H._ARMED):
            with patch("app.services.conversation_engine.OutboundSafetyGate") as MG:
                gate, blocked = MagicMock(), MagicMock()
                blocked.outcome = GateOutcome.BLOCKED_DUPLICATE
                blocked.message_id = 1
                gate.attempt.return_value = blocked
                MG.return_value = gate
                with self.assertRaises(OutboundBlockedError):
                    t.engine._send_text_to_wa(
                        ctx, "¿En qué localidad o barrio está el auto?")
        self.assertEqual([], t.questions(), "a blocked send left a false question")

    def test_c02_a_meta_failure_leaves_no_question(self):
        """`_send_whatsapp_cloud_text` raised after the gate allowed it: the ledger row is
        marked failed and the customer received nothing."""
        from app.services.conversation_engine import MetaSendError
        t = _Thread()
        self.addCleanup(t.close)
        previous, H._AI_PAYLOAD = H._AI_PAYLOAD, _ASKS_LOCATION
        try:
            with patch.dict(os.environ, H._ARMED), patch("urllib.request.urlopen") as mu:
                mu.return_value.__enter__ = lambda s: s
                mu.return_value.__exit__ = MagicMock()
                mu.return_value.read = lambda: json.dumps(
                    {"choices": [{"message": {"content": _ASKS_LOCATION}}]}).encode()
                with patch("app.services.conversation_engine.OutboundSafetyGate") as MG:
                    gate, allowed = MagicMock(), MagicMock()
                    allowed.outcome = "allowed"
                    allowed.message_id = 1
                    gate.attempt.return_value = allowed
                    MG.return_value = gate
                    with patch("app.services.conversation_engine."
                               "_send_whatsapp_cloud_text",
                               side_effect=MetaSendError(500, "", "boom")):
                        with patch("app.services.conversation_engine."
                                   "reset_unanswered_alert"):
                            from app.schemas.conversation import ConversationHandleIn
                            H._seq[0] += 1
                            event = ConversationHandleIn(
                                thread_id=t.thread_id,
                                wa_message_id=f"wamid.R2F{H._seq[0]:05d}",
                                wa_id=t.wa, text="¿de qué consta la revisión?",
                                unanswered_recent_user_messages=[
                                    "¿de qué consta la revisión?"],
                                recent_user_messages=["¿de qué consta la revisión?"])
                            t.engine.handle(event)
        finally:
            H._AI_PAYLOAD = previous
        self.assertEqual([], t.questions(), "a failed Meta send left a false question")

    def test_c03_a_turn_that_asks_nothing_records_nothing(self):
        """The reply did not ask for a location, so no question is outstanding. This is the
        guard against recording a question on every delivered message."""
        t = _Thread()
        self.addCleanup(t.close)
        t.turn("¿de qué consta la revisión?", payload=_NEUTRAL)
        self.assertEqual([], t.questions())

    def test_c04_the_deterministic_template_is_recorded_as_such(self):
        """`asked_by = DETERMINISTIC_TEMPLATE`, proved STRUCTURALLY from the plan's own
        `included()` set and not by searching the customer's prose — the G3-7F owner
        resolution forbids the latter."""
        t = _Thread(vehicle=True)
        self.addCleanup(t.close)
        # Measured: on a thread whose focus candidate has a `tipo_vehiculo` and no zone,
        # this turn is the one that composes the canonical location ask
        # (`¿En qué localidad o barrio está el auto?`) as a typed proposition.
        t.turn("hola")
        rows = t.questions()
        self.assertTrue(rows, "the clarification proposition asked and nothing recorded it")
        self.assertEqual(OQ_PENDING_DELIVERY, rows[0].status)
        self.assertEqual(OQ_ASKED_BY_DETERMINISTIC_TEMPLATE, rows[0].asked_by)
        self.assertEqual(OQ_KIND_INSPECTION_LOCATION, rows[0].question_kind)

    def test_c05_the_row_carries_the_delivery_evidence_and_the_originating_turn(self):
        t = _Thread()
        self.addCleanup(t.close)
        t.turn(_FAQ_TEXT, payload=_ASKS_LOCATION)
        row = t.questions()[0]
        self.assertEqual(OQ_PENDING_DELIVERY, row.status)
        self.assertTrue(row.wa_message_id, "no acceptance evidence on the row")
        self.assertTrue(row.asked_turn_id, "no originating turn on the row")
        self.assertIsNotNone(row.asked_at)
        self.assertEqual(0, row.turns_observed)

    def test_c06_a_replayed_inbound_message_does_not_open_a_second_question(self):
        """Duplicate webhook delivery of the same customer message."""
        t = _Thread()
        self.addCleanup(t.close)
        from app.schemas.conversation import ConversationHandleIn
        H._seq[0] += 1
        wamid = f"wamid.R2D{H._seq[0]:05d}"
        previous, H._AI_PAYLOAD = H._AI_PAYLOAD, _ASKS_LOCATION
        try:
            for _ in range(3):
                event = ConversationHandleIn(
                    thread_id=t.thread_id, wa_message_id=wamid, wa_id=t.wa,
                    text="¿de qué consta la revisión?",
                    unanswered_recent_user_messages=["¿de qué consta la revisión?"],
                    recent_user_messages=["¿de qué consta la revisión?"])
                with patch.dict(os.environ, H._ARMED), \
                        patch("urllib.request.urlopen") as mu:
                    mu.return_value.__enter__ = lambda s: s
                    mu.return_value.__exit__ = MagicMock()
                    mu.return_value.read = lambda: json.dumps(
                        {"choices": [{"message": {"content": _ASKS_LOCATION}}]}).encode()
                    with patch("app.services.conversation_engine.OutboundSafetyGate") as MG:
                        gate, allowed = MagicMock(), MagicMock()
                        allowed.outcome = "allowed"
                        allowed.message_id = 1
                        gate.attempt.return_value = allowed
                        MG.return_value = gate
                        with patch("app.services.conversation_engine."
                                   "_send_whatsapp_cloud_text",
                                   return_value=("wamid.FIXED", {})):
                            with patch("app.services.conversation_engine."
                                       "reset_unanswered_alert"):
                                t.engine.handle(event)
        finally:
            H._AI_PAYLOAD = previous
        self.assertEqual(1, len(t.questions()),
                         "a replayed delivery opened more than one question")


# ══════════════════════════════════════════════════════════════════════════════
class TestR2M_Delivery(unittest.TestCase):
    """OC-R2 CORRECTION 1 — acceptance is not delivery. The eleven cases the owner named.

    The integration path, measured before any of this was designed rather than assumed:
    `routes/whatsapp.py::inbound_webhook` already receives Meta's `statuses` array, ranks
    it by `STATUS_PRECEDENCE` (pending < sent < delivered < read < failed) and writes
    `whatsapp_messages.status`. It sits behind the `X-Hub-Signature-256` HMAC check against
    `WHATSAPP_APP_SECRET`, which is set in the deployed container and fails closed when it
    is not. And the statuses genuinely arrive: on `crm_test`, 43 of 45 outbound rows reached
    `read`, 1 `delivered`, 1 `failed`, and **none** is stranded at `sent` or `pending`.

    So no new route, no new transport and no new authenticity boundary — the activation hook
    is called from that existing handler, and these tests call the same function it calls.
    """

    def _accepted(self, **kw):
        t = _Thread(**kw)
        t.turn(_FAQ_TEXT, payload=_ASKS_LOCATION)
        rows = t.questions()
        self.assertEqual(1, len(rows), "the question was not even accepted")
        self.assertEqual(OQ_PENDING_DELIVERY, rows[0].status)
        return t, rows[0]

    def test_m01_an_accepted_send_with_no_callback_is_not_active(self):
        """A WAMID came back and nothing else ever did. The question exists as evidence of
        what we tried, and owns nothing."""
        t, row = self._accepted()
        self.addCleanup(t.close)
        self.assertEqual([], OQ.live(t.db, thread_id=t.thread_id, cycle_key=t.cycle()))
        self.assertIsNone(row.activated_at)
        self.assertIsNone(row.delivery_status)

    def test_m02_sent_alone_does_not_activate(self):
        """`sent` means Meta accepted it for delivery — which the WAMID already said. It is
        explicitly not sufficient."""
        t, row = self._accepted()
        self.addCleanup(t.close)
        self.assertEqual(0, t.deliver(status="sent"))
        self.assertEqual(OQ_PENDING_DELIVERY, t.questions()[0].status)
        self.assertEqual([], OQ.live(t.db, thread_id=t.thread_id, cycle_key=t.cycle()))

    def test_m03_delivered_activates_it(self):
        t, row = self._accepted()
        self.addCleanup(t.close)
        self.assertEqual(1, t.deliver(status="delivered"))
        fresh = t.questions()[0]
        self.assertEqual(OQ_OUTSTANDING, fresh.status)
        self.assertEqual("delivered", fresh.delivery_status)
        self.assertIsNotNone(fresh.activated_at)
        self.assertEqual([fresh.id], [q.id for q in OQ.live(
            t.db, thread_id=t.thread_id, cycle_key=t.cycle())])

    def test_m04_read_without_a_prior_delivered_still_activates(self):
        """Meta does not guarantee the intermediate callback arrives, and you cannot read a
        message that was not delivered."""
        t, row = self._accepted()
        self.addCleanup(t.close)
        self.assertEqual(1, t.deliver(status="read"))
        fresh = t.questions()[0]
        self.assertEqual(OQ_OUTSTANDING, fresh.status)
        self.assertEqual("read", fresh.delivery_status)

    def test_m05_failed_is_terminal_and_never_active(self):
        t, row = self._accepted()
        self.addCleanup(t.close)
        self.assertEqual(1, t.deliver(status="failed"))
        fresh = t.questions()[0]
        self.assertEqual(OQ_UNDELIVERED, fresh.status)
        self.assertEqual(OQ.CloseReason.DELIVERY_FAILED, fresh.closed_reason)
        self.assertIsNotNone(fresh.closed_at)
        self.assertEqual([], OQ.live(t.db, thread_id=t.thread_id, cycle_key=t.cycle()))

    def test_m06_duplicate_and_out_of_order_callbacks_are_idempotent(self):
        t, row = self._accepted()
        self.addCleanup(t.close)
        first = OQ.activate_on_status(t.db, wa_message_id=row.wa_message_id,
                                      status="delivered")
        self.assertEqual(1, first)
        for repeat in ("delivered", "read", "sent", "delivered"):
            with self.subTest(callback=repeat):
                self.assertEqual(0, OQ.activate_on_status(
                    t.db, wa_message_id=row.wa_message_id, status=repeat),
                    "a duplicate or out-of-order callback changed the row")
        t.db.expire_all()
        fresh = t.questions()[0]
        self.assertEqual(OQ_OUTSTANDING, fresh.status)
        self.assertEqual("delivered", fresh.delivery_status,
                         "a later callback overwrote the activating provenance")

    def test_m07_a_late_delivered_does_not_resurrect_a_failed_question(self):
        """The ordering case that matters most: Meta reported failure, then a stale
        `delivered` arrives. The conditional update only fires from PENDING_DELIVERY, so
        the terminal state holds."""
        t, row = self._accepted()
        self.addCleanup(t.close)
        self.assertEqual(1, t.deliver(status="failed"))
        self.assertEqual(0, OQ.activate_on_status(
            t.db, wa_message_id=row.wa_message_id, status="delivered"))
        self.assertEqual(0, OQ.activate_on_status(
            t.db, wa_message_id=row.wa_message_id, status="read"))
        t.db.expire_all()
        self.assertEqual(OQ_UNDELIVERED, t.questions()[0].status)
        self.assertEqual([], OQ.live(t.db, thread_id=t.thread_id, cycle_key=t.cycle()))

    def test_m08_a_callback_arriving_before_the_row_exists_is_not_lost(self):
        """The race the owner named. The outbound ledger is the rendezvous: the callback
        updates `whatsapp_messages.status` in its own transaction, and `reconcile_delivery`
        reads that status when the question row is created. Nothing is lost."""
        t = _Thread()
        self.addCleanup(t.close)
        # the ledger row the gate would have written, already advanced by an early callback
        t.db.add(_ledger(t, "wamid.EARLY", "read"))
        t.db.flush()
        row, created = _record(t.db, t.thread_id, wamid="wamid.EARLY")
        self.assertTrue(created)
        self.assertEqual(OQ_PENDING_DELIVERY, row.status)
        self.assertTrue(OQ.reconcile_delivery(t.db, row),
                        "the early callback's status was not applied")
        t.db.expire_all()
        self.assertEqual(OQ_OUTSTANDING, row.status)
        self.assertEqual("read", row.delivery_status)

    def test_m08b_reconciliation_also_applies_an_early_failure(self):
        t = _Thread()
        self.addCleanup(t.close)
        t.db.add(_ledger(t, "wamid.EARLYFAIL", "failed"))
        t.db.flush()
        row, _ = _record(t.db, t.thread_id, wamid="wamid.EARLYFAIL")
        self.assertTrue(OQ.reconcile_delivery(t.db, row))
        t.db.expire_all()
        self.assertEqual(OQ_UNDELIVERED, row.status)

    def test_m08c_reconciliation_does_nothing_when_only_sent_is_recorded(self):
        t = _Thread()
        self.addCleanup(t.close)
        t.db.add(_ledger(t, "wamid.ONLYSENT", "sent"))
        t.db.flush()
        row, _ = _record(t.db, t.thread_id, wamid="wamid.ONLYSENT")
        self.assertFalse(OQ.reconcile_delivery(t.db, row))
        self.assertEqual(OQ_PENDING_DELIVERY, row.status)

    def test_m08d_the_engine_applies_an_early_callback_during_the_turn(self):
        """The same race, driven through a COMPLETE TURN rather than through the service.

        MUTATION M27 is why this test exists. It replaces the engine's
        `OQ.reconcile_delivery(...)` call with `pass` and **survived** the first run,
        because `m08`–`m08c` exercise the function directly and nothing asserted that the
        engine actually calls it. A reconciliation nobody invokes is not a reconciliation.

        The setup is the real ordering: the outbound ledger already carries `read` for the
        WAMID this turn's transport will return — which is what an early callback leaves
        behind — and the question must come out of the turn already ACTIVE, with no
        explicit delivery step."""
        t = _Thread()
        self.addCleanup(t.close)
        # the harness's fake transport returns `fake-text-1` for the first send of a turn;
        # an early callback would already have advanced that ledger row.
        t.db.add(_ledger(t, "fake-text-1", "read"))
        t.db.commit()
        self.addCleanup(_drop_ledger, t, "fake-text-1")
        t.turn(_FAQ_TEXT, payload=_ASKS_LOCATION)
        rows = t.questions()
        self.assertEqual(1, len(rows))
        self.assertEqual(OQ_OUTSTANDING, rows[0].status,
                         "the engine did not apply the status the ledger already held")
        self.assertEqual("read", rows[0].delivery_status)
        self.assertIsNotNone(rows[0].activated_at)
        # and it is genuinely live, so the next turn can answer it
        self.assertEqual([rows[0].id], [q.id for q in OQ.live(
            t.db, thread_id=t.thread_id, cycle_key=t.cycle())])

    def test_m08e_the_engine_applies_an_early_failure_during_the_turn(self):
        t = _Thread()
        self.addCleanup(t.close)
        t.db.add(_ledger(t, "fake-text-1", "failed"))
        t.db.commit()
        self.addCleanup(_drop_ledger, t, "fake-text-1")
        t.turn(_FAQ_TEXT, payload=_ASKS_LOCATION)
        rows = t.questions()
        self.assertEqual(1, len(rows))
        self.assertEqual(OQ_UNDELIVERED, rows[0].status)
        self.assertEqual([], OQ.live(t.db, thread_id=t.thread_id, cycle_key=t.cycle()))

    def test_m09_a_status_for_another_message_or_thread_changes_nothing(self):
        t, row = self._accepted()
        other = _Thread()
        self.addCleanup(t.close)
        self.addCleanup(other.close)
        other_turn = other.turn(_FAQ_TEXT, payload=_ASKS_LOCATION)
        other_row = other.questions()[0]
        self.assertNotEqual(row.wa_message_id, other_row.wa_message_id)
        for wamid in ("wamid.SOMEONE_ELSE", "", "wamid.UNKNOWN"):
            with self.subTest(wamid=wamid):
                self.assertEqual(0, OQ.activate_on_status(
                    t.db, wa_message_id=wamid, status="delivered"))
        # the correct WAMID activates only ITS own thread's question
        self.assertEqual(1, OQ.activate_on_status(
            t.db, wa_message_id=other_row.wa_message_id, status="delivered"))
        t.db.expire_all()
        other.db.expire_all()
        self.assertEqual(OQ_PENDING_DELIVERY, t.questions()[0].status,
                         "another thread's delivery activated this thread's question")

    def test_m10_a_pending_question_cannot_suppress_layer_f(self):
        """The whole reason the correction matters. A question Meta accepted but never
        delivered must not buy silence: the greeting still goes out, because as far as the
        customer is concerned nothing was asked."""
        t, row = self._accepted()
        self.addCleanup(t.close)
        turn = t.turn("en flores", evidence=_evidence(_loc("Flores")))
        self.assertTrue(any(_GREETING_MARK in s for s in turn.sent),
                        "an undelivered question suppressed the greeting")
        self.assertEqual(OQ_PENDING_DELIVERY, t.questions()[0].status,
                         "an undelivered question was answered")
        self.assertEqual(("CABA", "Flores"), t.zone(),
                         "R1's evidence admission was affected by the delivery state")

    def test_m11_only_a_delivered_question_can_be_answered(self):
        """The same turn, the same evidence, the only difference being delivery."""
        for status, expect_answered in (("sent", False), ("delivered", True),
                                        ("read", True), ("failed", False)):
            with self.subTest(status=status):
                t, row = self._accepted()
                try:
                    t.deliver(status=status)
                    turn = t.turn("en flores", evidence=_evidence(_loc("Flores")))
                    fresh = t.questions()[0]
                    if expect_answered:
                        self.assertEqual(OQ_ANSWERED, fresh.status)
                        self.assertFalse(any(_GREETING_MARK in s for s in turn.sent))
                    else:
                        self.assertNotEqual(OQ_ANSWERED, fresh.status)
                        self.assertTrue(any(_GREETING_MARK in s for s in turn.sent))
                finally:
                    t.close()

    def test_m12_the_webhook_handler_is_the_activation_site_and_is_authenticated(self):
        """No new route, and no unauthenticated status mutation — the owner's constraint.

        Read off the source: the activation call lives inside `inbound_webhook`'s status
        loop, and that function verifies `X-Hub-Signature-256` against
        `WHATSAPP_APP_SECRET` before it parses anything. `_verify_signature` fails closed
        when the secret is unset."""
        route = (BACKEND_DIR / "app" / "routes" / "whatsapp.py").read_text(
            encoding="utf-8")
        self.assertEqual(1, route.count("activate_on_status("),
                         "the activation site is not unique")
        verify = route.index("_verify_signature(")
        activate = route.index("activate_on_status(")
        self.assertLess(verify, activate,
                        "the status path can mutate a question before authentication")
        self.assertIn("X-Hub-Signature-256", route)
        # and no second route was added for statuses
        self.assertEqual(1, route.count('@router.post("/webhook")'))
        for added in ('@router.post("/status', '@router.post("/delivery'):
            self.assertNotIn(added, route)

    def test_m13_the_state_vocabulary_is_one_definition(self):
        from app.models import (OQ_ACTIVE_STATUSES, OQ_DELIVERING_STATUSES,
                                OQ_FAILED_STATUS, OQ_STATUSES)
        self.assertEqual((OQ_OUTSTANDING,), OQ_ACTIVE_STATUSES,
                         "more than one state is treated as active")
        self.assertEqual(("delivered", "read"), tuple(OQ_DELIVERING_STATUSES))
        self.assertNotIn("sent", OQ_DELIVERING_STATUSES)
        self.assertEqual("failed", OQ_FAILED_STATUS)
        for state in (OQ_PENDING_DELIVERY, OQ_UNDELIVERED):
            self.assertIn(state, OQ_STATUSES)
        # the migration's CHECK carries exactly the ORM's vocabulary
        migration = (BACKEND_DIR / "migrations" / "versions"
                     / "20261027_w1f_conversation_memory.py").read_text(encoding="utf-8")
        for state in OQ_STATUSES:
            with self.subTest(state=state):
                self.assertIn(state, migration,
                              "the migration's CHECK vocabulary is behind the ORM's")
        # and the CHECK is built from that one tuple rather than a second literal list
        self.assertIn("_in_list('status', OQ_STATUSES)", migration)


# ══════════════════════════════════════════════════════════════════════════════
class TestR2N_Ordering(unittest.TestCase):
    """OC-R2-R1 — a LATE delivery for an older question must never replace a newer one.

    The owner's sequence, which the first delivery contract got wrong:

        Q1 accepted (PENDING_DELIVERY)
        Q2 accepted for the same thread, cycle and kind
        Q2 delivered            → Q2 is OUTSTANDING, correctly
        Q1 delivered, LATE      → Q1 activated and SUPERSEDED Q2

    The last line was the defect: supersession asked "who is live?" and never "who is
    newer?", so a question the customer had been re-asked out of could take the field back
    on a callback that Meta happened to deliver out of order.

    The ordering is the **row id** — the only immutable, monotonic, locally assigned
    ordering this table has, and the one the owner named. Webhook arrival time is explicitly
    not an ordering: it is a property of Meta's retry schedule, not of the conversation, and
    using it is what makes the outcome depend on which callback happened to land first.

    Every test here is one of the owner's cases A–H, with case F on PostgreSQL in
    `TestR2K_Migration` (`k06a`, `k06b`, `k06c`) because two interleaved connections cannot
    be expressed in the sealed SQLite harness — the lesson mutation M25 taught this
    milestone.
    """

    def setUp(self):
        self.db = H._SessionLocal()
        H._seed_catalogue(self.db)
        self.thread_id, _, _ = H._seed_thread(self.db, vehicle=False)

    def tearDown(self):
        self.db.close()

    # ── fixtures ─────────────────────────────────────────────────────────────
    def _two_asks(self, **kw):
        """Two accepted asks for the same owner tuple, through the production path.

        Note what this already proves: accepting Q2 retires Q1 immediately, because
        `record_asked` applies the same ordering helper. So in the ordinary single-threaded
        sequence the obsolete row is terminal from the moment of the re-ask and the late
        callback has nothing left to change — which is the outcome the owner asked for, and
        the reason the guard inside `activate_on_status` needs `_unswept` below to be
        exercised at all.
        """
        q1, _ = _record(self.db, self.thread_id, wamid="wamid.Q1", **kw)
        q2, _ = _record(self.db, self.thread_id, wamid="wamid.Q2", **kw)
        self.db.expire_all()
        self.assertLess(q1.id, q2.id, "the fixture's own ordering is not what it claims")
        return q1, q2

    def _unswept(self, row):
        """Put `row` back to `PENDING_DELIVERY` with a newer sibling already live.

        A CONSTRUCTED state, and it is labelled as one. It is the state two PostgreSQL
        connections can genuinely produce — Q1's delivery callback being processed by a
        connection that had not yet seen Q2's acceptance, so the acceptance-time sweep never
        reached Q1 — and it is the only way to make the activation guard itself, rather than
        the eager sweep, the thing standing in the way. The real interleaving is proved on a
        real server in `TestR2K_Migration.test_k06a/b/c`; this makes the guard's own write
        observable in the sealed harness instead of claiming the guard is covered because
        something else happened to cover the outcome.
        """
        row.status = OQ_PENDING_DELIVERY
        row.closed_reason = None
        row.closed_at = None
        row.superseded_by_id = None
        row.delivery_status = None
        row.activated_at = None
        self.db.flush()
        self.db.expire_all()
        return row

    def _status(self, row, status):
        changed = OQ.activate_on_status(self.db, wa_message_id=row.wa_message_id,
                                        status=status)
        self.db.flush()
        self.db.expire_all()
        return changed

    def _assert_terminal_before_delivery(self, row, *, by):
        self.assertEqual(OQ_SUPERSEDED, row.status)
        self.assertEqual(OQ.CloseReason.SUPERSEDED_BEFORE_DELIVERY, row.closed_reason)
        self.assertEqual(by.id, row.superseded_by_id)
        self.assertIsNotNone(row.closed_at)
        self.assertIsNone(row.activated_at, "a terminalized question was activated")

    def _assert_owns_the_field(self, row, *, delivery, activated_at):
        """`row` is the live question AND its activation provenance is byte-for-byte intact."""
        self.assertEqual(OQ_OUTSTANDING, row.status)
        self.assertEqual(delivery, row.delivery_status,
                         "another question's callback overwrote the delivery provenance")
        self.assertEqual(activated_at, row.activated_at,
                         "another question's callback overwrote activated_at")
        self.assertIsNone(row.closed_reason)
        self.assertIsNone(row.superseded_by_id)
        self.assertEqual([row.id], [q.id for q in OQ.live(
            self.db, thread_id=self.thread_id, cycle_key=0)])

    # ── A. the owner's sequence, exactly ─────────────────────────────────────
    def test_n01_a_late_delivery_for_an_older_question_cannot_replace_a_newer_one(self):
        """Case A: Q1 pending → Q2 delivered → Q1 delivered LATE.

        Q2 keeps the field, Q1 is terminal, and Q2's activation provenance is untouched —
        the three things the owner requires, asserted separately so a failure says which
        one broke."""
        q1, q2 = self._two_asks()
        self.assertEqual(1, self._status(q2, "delivered"))
        activated, delivery = q2.activated_at, q2.delivery_status
        self.assertIsNotNone(activated)

        # the late callback for the REPLACED question
        self.assertEqual(0, self._status(q1, "delivered"),
                         "the late callback altered a row the re-ask had already retired")

        self._assert_terminal_before_delivery(q1, by=q2)
        self._assert_owns_the_field(q2, delivery=delivery, activated_at=activated)

    def test_n01d_the_activation_guard_alone_refuses_the_late_delivery(self):
        """Case A again, with the eager sweep taken out of the way.

        `_unswept` restores the state an interleaved acceptance leaves — Q1 still pending
        while Q2 is already live — so the only thing between the late callback and a
        reactivation is the guard in `activate_on_status`. It refuses, terminalizes, and
        records the delivery that did eventually happen so the provenance is not lost."""
        q1, q2 = self._two_asks()
        self._status(q2, "delivered")
        activated, delivery = q2.activated_at, q2.delivery_status
        self._unswept(q1)

        self.assertEqual(1, self._status(q1, "read"),
                         "the guard did not terminalize the replaced question")
        self._assert_terminal_before_delivery(q1, by=q2)
        self.assertEqual("read", q1.delivery_status,
                         "the delivery that did happen was not recorded on the row")
        self._assert_owns_the_field(q2, delivery=delivery, activated_at=activated)

    def test_n01b_the_late_callback_cannot_match_evidence_or_suppress_anything(self):
        """A terminal row is invisible to every reader that could give it authority:
        `live()` does not return it, `match()` refuses it by name, and the engine's
        routing fact is computed from `live()` alone."""
        q1, q2 = self._two_asks()
        self._status(q2, "delivered")
        self._status(q1, "delivered")
        self.assertNotIn(q1.id, [q.id for q in OQ.live(
            self.db, thread_id=self.thread_id, cycle_key=0)])
        record, why = OQ.match(q1, [_admitted()], cycle_key=0)
        self.assertIsNone(record)
        self.assertEqual(OQ.NoMatch.ALREADY_CLOSED, why)
        self.assertFalse(OQ.close_answered(self.db, q1, claim_id="claim-1"),
                         "a superseded question was closed as ANSWERED")

    def test_n01c_the_late_callback_is_idempotent(self):
        """The first late callback terminalizes; every repeat changes nothing and reports
        so, which is what keeps the webhook from committing an empty transaction."""
        q1, q2 = self._two_asks()
        self._status(q2, "delivered")
        activated, delivery = q2.activated_at, q2.delivery_status
        self._unswept(q1)
        self.assertEqual(1, self._status(q1, "delivered"))
        closed_at = q1.closed_at
        for repeat in ("delivered", "read", "delivered"):
            with self.subTest(repeat=repeat):
                self.assertEqual(0, self._status(q1, repeat))
                self.assertEqual(OQ_SUPERSEDED, q1.status)
                self.assertEqual(closed_at, q1.closed_at,
                                 "a duplicate callback rewrote the terminal row")
        self._assert_owns_the_field(q2, delivery=delivery, activated_at=activated)

    # ── B. a newer PENDING question does not displace a live one ─────────────
    def test_n02_a_newer_pending_question_leaves_the_outstanding_one_alone(self):
        """Case B. The OC-R2 correction-1 rule, re-proved under the ordering rule: the
        newer row has only been ACCEPTED, and a message that may never arrive must not
        retire a question the customer demonstrably has."""
        q1, _ = _record(self.db, self.thread_id, wamid="wamid.Q1")
        self._status(q1, "delivered")
        activated, delivery = q1.activated_at, q1.delivery_status
        q2, created = _record(self.db, self.thread_id, wamid="wamid.Q2")
        self.db.expire_all()
        self.assertTrue(created)
        self.assertEqual(OQ_PENDING_DELIVERY, q2.status)
        self._assert_owns_the_field(q1, delivery=delivery, activated_at=activated)

    # ── C. the newest delivered question takes the field ─────────────────────
    def test_n03_the_newest_delivered_question_supersedes_the_live_one(self):
        """Case C: Q1 outstanding → Q2 delivered. Q2 owns the field and Q1 records WHY it
        lost it — `SUPERSEDED_BY_REASK`, which is a different fact from a question that
        was never delivered at all."""
        q1, _ = _record(self.db, self.thread_id, wamid="wamid.Q1")
        self._status(q1, "delivered")
        q2, _ = _record(self.db, self.thread_id, wamid="wamid.Q2")
        self.assertEqual(1, self._status(q2, "read"))
        self.assertEqual(OQ_SUPERSEDED, q1.status)
        self.assertEqual(OQ.CloseReason.SUPERSEDED_BY_REASK, q1.closed_reason)
        self.assertEqual(q2.id, q1.superseded_by_id)
        self.assertEqual("delivered", q1.delivery_status,
                         "the superseded row lost its own delivery provenance")
        self._assert_owns_the_field(q2, delivery="read", activated_at=q2.activated_at)

    # ── D. two pending asks, the newer one delivered ─────────────────────────
    def test_n04_an_obsolete_pending_question_is_terminalized_not_merely_ignored(self):
        """Case D: Q1 pending → Q2 pending → Q2 delivered.

        Q1 is terminal, and it is terminal as a WRITTEN state rather than by being
        unreachable — the doctrine this whole milestone runs on. The write happens at Q2's
        acceptance, through the same ordering helper activation uses."""
        q1, q2 = self._two_asks()
        self._assert_terminal_before_delivery(q1, by=q2)      # already, at acceptance
        self.assertEqual(1, self._status(q2, "delivered"))
        self.db.expire_all()
        self._assert_terminal_before_delivery(q1, by=q2)      # and unchanged by it
        self._assert_owns_the_field(q2, delivery="delivered", activated_at=q2.activated_at)

    # ── E/G. late, duplicated and reversed callbacks after supersession ──────
    def test_n05_a_late_read_after_supersession_changes_nothing(self):
        """Case E. `read` is the strongest status Meta sends and it still cannot revive a
        superseded question: the conditional update fires only off `PENDING_DELIVERY`."""
        q1, _ = _record(self.db, self.thread_id, wamid="wamid.Q1")
        self._status(q1, "delivered")
        q2, _ = _record(self.db, self.thread_id, wamid="wamid.Q2")
        self._status(q2, "delivered")
        self.assertEqual(OQ_SUPERSEDED, q1.status)
        was_activated, was_delivery = q1.activated_at, q1.delivery_status
        self.assertEqual(0, self._status(q1, "read"))
        self.assertEqual(OQ_SUPERSEDED, q1.status)
        # it keeps its own history — it WAS delivered once — and gains nothing from the
        # late callback. A superseded question is not re-stamped, and is not re-activated.
        self.assertEqual(was_activated, q1.activated_at)
        self.assertEqual(was_delivery, q1.delivery_status)
        self.assertEqual([q2.id], [q.id for q in OQ.live(
            self.db, thread_id=self.thread_id, cycle_key=0)])

    def test_n06_reversed_and_duplicated_callbacks_reach_the_same_state(self):
        """Case G, as a matrix: whatever order the four callbacks arrive in, the newest
        accepted ask owns the field and the older one is terminal."""
        for order in (("Q1", "Q2"), ("Q2", "Q1"), ("Q2", "Q2", "Q1", "Q1")):
            with self.subTest(order=order):
                self.db.query(WhatsAppOutstandingQuestion).filter_by(
                    thread_id=self.thread_id).delete()
                self.db.flush()
                q1, q2 = self._two_asks()
                rows = {"Q1": q1, "Q2": q2}
                for i, which in enumerate(order):
                    self._status(rows[which], "read" if i % 2 else "delivered")
                self.db.expire_all()
                self.assertEqual(OQ_OUTSTANDING, q2.status,
                                 "the newest ask did not end up owning the field")
                self.assertEqual(OQ_SUPERSEDED, q1.status)
                self.assertEqual([q2.id], [q.id for q in OQ.live(
                    self.db, thread_id=self.thread_id, cycle_key=0)])

    # ── the ordering key itself ──────────────────────────────────────────────
    def test_n07_a_terminal_newer_sibling_still_blocks_the_older_one(self):
        """The rule counts EVERY newer row, terminal ones included — and that is the whole
        point. If a newer `UNDELIVERED` sibling handed the field back, the outcome would
        depend on whether Q2's `failed` arrived before or after Q1's `delivered`, which is
        the callback-order dependence this correction exists to remove.

        The cost is stated, not hidden: nothing is active for that field. The next turn
        asks again, which is the fail-closed direction."""
        q1, q2 = self._two_asks()
        self.assertEqual(1, self._status(q2, "failed"))
        self.assertEqual(OQ_UNDELIVERED, q2.status)
        self._status(q1, "delivered")
        self.assertEqual(OQ_SUPERSEDED, q1.status,
                         "a question replaced by a newer ask activated anyway")
        self.assertEqual([], OQ.live(self.db, thread_id=self.thread_id, cycle_key=0))

    def test_n08_the_ordering_is_the_row_id_and_not_the_clock(self):
        """`newest_question_id` is decided by the primary key. Backdating `asked_at` on the
        newer row — which a clock-based ordering would believe — changes nothing."""
        q1, q2 = self._two_asks()
        q2.asked_at = q1.asked_at - timedelta(hours=3)
        self.db.flush()
        self.db.expire_all()
        self.assertEqual(q2.id, OQ.newest_question_id(
            self.db, thread_id=self.thread_id, cycle_key=0,
            kind=OQ_KIND_INSPECTION_LOCATION))
        self._status(q1, "delivered")
        self.assertEqual(OQ_SUPERSEDED, q1.status)

    def test_n09_the_owner_tuple_is_thread_cycle_and_kind(self):
        """A newer question for a DIFFERENT field, cycle or thread is not newer *for this
        one* — the ordering is scoped to the same three columns the partial unique index
        guards, so the two cannot disagree about who owns what."""
        loc, _ = _record(self.db, self.thread_id, wamid="wamid.L",
                         kind=OQ_KIND_INSPECTION_LOCATION)
        veh, _ = _record(self.db, self.thread_id, wamid="wamid.V",
                         kind=OQ_KIND_VEHICLE_IDENTITY)
        other_cycle, _ = _record(self.db, self.thread_id, wamid="wamid.C", cycle=99)
        second_thread, _, _ = H._seed_thread(self.db, vehicle=False)
        elsewhere, _ = _record(self.db, second_thread, wamid="wamid.T2")
        self.db.expire_all()
        self.assertGreater(elsewhere.id, loc.id, "the fixture is not ordered as it claims")
        self.assertEqual(1, self._status(loc, "delivered"),
                         "a newer question for another field blocked this one")
        self.assertEqual(OQ_OUTSTANDING, loc.status)
        for row in (veh, other_cycle, elsewhere):
            with self.subTest(kind=row.question_kind, cycle=row.cycle_key,
                              thread=row.thread_id):
                self.assertEqual(OQ_PENDING_DELIVERY, row.status,
                                 "an unrelated question was swept up by the ordering rule")

    # ── H. the engine leaves no obsolete pending row behind ──────────────────
    def test_n10_a_re_ask_leaves_no_pending_row_capable_of_later_activation(self):
        """Case H, through the live engine rather than the service.

        Two turns ask the location question and no callback ever arrives for the first.
        The first row must not be left able to activate later — a real possibility, because
        Meta may deliver a message minutes after we have already asked again."""
        t = _Thread()
        self.addCleanup(t.close)
        t.turn(_FAQ_TEXT, payload=_ASKS_LOCATION)
        first = t.questions()
        self.assertEqual(1, len(first))
        self.assertEqual(OQ_PENDING_DELIVERY, first[0].status)
        first_wamid = first[0].wa_message_id

        t.turn(_FAQ_TEXT, payload=_ASKS_LOCATION)
        rows = t.questions()
        self.assertEqual(2, len(rows), "the second ask did not produce its own row")
        self.assertEqual(OQ_SUPERSEDED, rows[0].status,
                         "an obsolete pending row survived the re-ask")
        self.assertEqual(OQ.CloseReason.SUPERSEDED_BEFORE_DELIVERY, rows[0].closed_reason)

        # and the late callback for the first message still cannot activate it
        self.assertEqual(0, t.deliver(wamid=first_wamid, status="delivered"))
        self.assertEqual(OQ_SUPERSEDED, t.questions()[0].status)
        self.assertEqual([], OQ.live(t.db, thread_id=t.thread_id, cycle_key=t.cycle()))

    def test_n11_the_delivered_newest_question_still_suppresses_and_closes_normally(self):
        """The correction removes an unsafe activation and nothing else: on the Wild's own
        shape the newest delivered question still owns the field, suppresses the greeting
        and closes on admitted evidence."""
        t = _Thread()
        self.addCleanup(t.close)
        t.turn(_FAQ_TEXT, payload=_ASKS_LOCATION)
        t.turn(_FAQ_TEXT, payload=_ASKS_LOCATION)
        self.assertEqual(1, t.deliver())
        rows = t.questions()
        self.assertEqual([OQ_SUPERSEDED, OQ_OUTSTANDING], [r.status for r in rows])
        answer = t.turn("en flores", evidence=_evidence(_loc("flores")))
        self.assertFalse(any(_GREETING_MARK in sent for sent in answer.sent),
                         "the answered question did not suppress the greeting")
        self.assertEqual([OQ_SUPERSEDED, OQ_ANSWERED],
                         [r.status for r in t.questions()])
        self.assertEqual(("CABA", "Flores"), t.zone())

    # ── the rule has one home ────────────────────────────────────────────────
    def test_n12_the_ordering_rule_has_exactly_one_implementation(self):
        """M34's lesson, applied to this correction: an interlock with two copies is an
        interlock that will disagree with itself. Both callers reach the ordering through
        `_terminalize_older`, and the `id <` comparison that IS the rule appears in that one
        function and nowhere else in the service."""
        source = (BACKEND_DIR / "app" / "services"
                  / "outstanding_question.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        homes = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            body = ast.dump(node)
            if "WhatsAppOutstandingQuestion" in body and "attr='id'" in body \
                    and "Lt()" in body:
                homes.append(node.name)
        self.assertEqual(["_terminalize_older"], homes,
                         f"the ordering comparison lives in more than one place: {homes}")
        callers = sorted(
            node.name for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and "_terminalize_older" in {
                getattr(c.func, "id", None) for c in ast.walk(node)
                if isinstance(c, ast.Call)})
        self.assertEqual(["activate_on_status", "record_asked"], callers)


# ══════════════════════════════════════════════════════════════════════════════
class TestR2P_Incumbent(unittest.TestCase):
    """OC-R2-R3 — what happens to an ACTIVE question when a re-ask does not arrive.

    The owner found two incompatible contracts in the previous revision's prose: case B
    said an undelivered re-ask retires nothing, while three other passages said only the
    newest accepted ask may own the field, so an undelivered re-ask leaves the field
    unasked. Those cannot both be true, and the implementation can only be doing one of
    them. **It does case B**, measured here through the service for all six sequences the
    owner enumerated — the other passages were an over-generalisation of sequence E, where
    there was never an active incumbent at all.

    THE RULE, in the owner's own words, and now the only statement of it anywhere:

        Only the newest ELIGIBLE row may newly ACTIVATE. An existing OUTSTANDING incumbent
        remains active until a newer question actually activates. A newer terminal sibling
        prevents an older INACTIVE row from activating later, but does not retroactively
        deactivate an incumbent that was already OUTSTANDING.

    The distinction is the whole correction: the ordering rule governs **activation**, not
    **deactivation**. Nothing in `activate_on_status` or `record_asked` can take the field
    from a question the customer demonstrably received except another question the customer
    demonstrably received.
    """

    def setUp(self):
        self.db = H._SessionLocal()
        H._seed_catalogue(self.db)
        self.thread_id, _, _ = H._seed_thread(self.db, vehicle=False)

    def tearDown(self):
        self.db.close()

    def _ask(self, wamid):
        row, _ = _record(self.db, self.thread_id, wamid=wamid)
        self.db.flush()
        self.db.expire_all()
        return row

    def _status(self, row, state):
        n = OQ.activate_on_status(self.db, wa_message_id=row.wa_message_id, status=state)
        self.db.flush()
        self.db.expire_all()
        return n

    def _live(self):
        return [q.id for q in OQ.live(self.db, thread_id=self.thread_id, cycle_key=0)]

    def _incumbent(self):
        """Q1, delivered and OUTSTANDING: an incumbent the customer actually has."""
        q1 = self._ask("wamid.INC1")
        self.assertEqual(1, self._status(q1, "delivered"))
        self.assertEqual(OQ_OUTSTANDING, q1.status)
        return q1

    # ── A. accepting a re-ask ────────────────────────────────────────────────
    def test_p01_accepting_a_re_ask_does_not_retire_the_incumbent(self):
        """Sequence A. `record_asked` sweeps older `PENDING_DELIVERY` siblings and
        **nothing else** — `include_outstanding=False`. A message we have only handed to
        Meta must not retire a question the customer is holding."""
        q1 = self._incumbent()
        activated, delivery = q1.activated_at, q1.delivery_status
        q2 = self._ask("wamid.INC2")
        self.assertEqual(OQ_PENDING_DELIVERY, q2.status)
        self.assertEqual(OQ_OUTSTANDING, q1.status,
                         "accepting a re-ask retired the active question")
        self.assertIsNone(q1.closed_reason)
        self.assertEqual((activated, delivery), (q1.activated_at, q1.delivery_status))
        self.assertEqual([q1.id], self._live())

    # ── B. the re-ask then FAILS ─────────────────────────────────────────────
    def test_p02_a_failed_re_ask_leaves_the_incumbent_active(self):
        """Sequence B, and the case the owner singled out.

        Meta reported the re-ask `failed`: the customer never got it. The question they DID
        get is still the live one, and the field is not unasked — it is asked, by Q1. The
        `failed` branch touches exactly the row carrying that WAMID."""
        q1 = self._incumbent()
        q2 = self._ask("wamid.INC2")
        self.assertEqual(1, self._status(q2, "failed"))
        self.assertEqual(OQ_UNDELIVERED, q2.status)
        self.assertEqual(OQ.CloseReason.DELIVERY_FAILED, q2.closed_reason)
        self.assertEqual(OQ_OUTSTANDING, q1.status,
                         "a FAILED re-ask deactivated the incumbent — the field would be "
                         "silently unasked")
        self.assertEqual([q1.id], self._live())
        # and it can still be answered, which is what "active" has to mean
        record, why = OQ.match(q1, [_admitted()], cycle_key=0)
        self.assertIsNotNone(record)
        self.assertIsNone(why)
        self.assertTrue(OQ.close_answered(self.db, q1, claim_id="claim-1"))

    # ── C. the re-ask never resolves at all ──────────────────────────────────
    def test_p03_a_re_ask_that_never_resolves_leaves_the_incumbent_active(self):
        """Sequence C. `PENDING_DELIVERY` owns nothing, including the power to expire
        somebody else: Q1 stays live for its whole counted lifetime."""
        q1 = self._incumbent()
        q2 = self._ask("wamid.INC2")
        for turn in (1, 2):
            with self.subTest(turn=turn):
                OQ.observe_turn(self.db, thread_id=self.thread_id, cycle_key=0)
                self.db.flush()
                self.db.expire_all()
                self.assertEqual(OQ_OUTSTANDING, q1.status)
                self.assertEqual([q1.id], self._live())
        self.assertEqual(OQ_PENDING_DELIVERY, q2.status)

    def test_p03b_the_incumbent_still_expires_on_its_own_counted_clock(self):
        """Sequence C to its end, so "remains active" is not read as "forever".

        The incumbent leaves on the third observed turn — by `EXPIRED_TURNS`, the ordinary
        counted-expiry rule, and **not** because of the re-ask. The pending re-ask is still
        pending afterwards, which is the §18.6 residual risk and not this contract's."""
        q1 = self._incumbent()
        q2 = self._ask("wamid.INC2")
        self.assertEqual(3, q1.expires_after_turns)
        for _ in range(3):
            OQ.observe_turn(self.db, thread_id=self.thread_id, cycle_key=0)
            self.db.flush()
            self.db.expire_all()
        self.assertEqual(OQ_ABANDONED, q1.status)
        self.assertEqual(OQ.CloseReason.EXPIRED_TURNS, q1.closed_reason)
        self.assertEqual(OQ_PENDING_DELIVERY, q2.status)
        self.assertEqual([], self._live())

    # ── D. the re-ask is delivered ───────────────────────────────────────────
    def test_p04_a_delivered_re_ask_takes_the_field_and_says_so(self):
        """Sequence D — the ONLY way an incumbent loses the field to a newer ask: the
        customer received the newer one too."""
        q1 = self._incumbent()
        q2 = self._ask("wamid.INC2")
        self.assertEqual(1, self._status(q2, "delivered"))
        self.assertEqual(OQ_SUPERSEDED, q1.status)
        self.assertEqual(OQ.CloseReason.SUPERSEDED_BY_REASK, q1.closed_reason)
        self.assertEqual(q2.id, q1.superseded_by_id)
        self.assertEqual(OQ_OUTSTANDING, q2.status)
        self.assertEqual([q2.id], self._live())

    # ── E. there was never an incumbent ──────────────────────────────────────
    def test_p05_with_no_incumbent_a_failed_re_ask_does_leave_the_field_unasked(self):
        """Sequence E — and this is the case the previous revision over-generalised into a
        universal rule.

        Q1 was still only `PENDING_DELIVERY` when Q2 was accepted, so Q1 was retired as
        obsolete. Q2 then failed. Nothing is active, and Q1's own late `delivered` cannot
        revive it. **The field is genuinely unasked here** — but that required there to be
        no active incumbent at any point, which is exactly the condition the corrected
        §15/§18 state and the previous wording omitted."""
        q1 = self._ask("wamid.E1")
        q2 = self._ask("wamid.E2")
        self.assertEqual(OQ_SUPERSEDED, q1.status)
        self.assertEqual(OQ.CloseReason.SUPERSEDED_BEFORE_DELIVERY, q1.closed_reason)
        self.assertEqual(1, self._status(q2, "failed"))
        self.assertEqual(0, self._status(q1, "delivered"),
                         "a retired question was revived by its own late delivery")
        self.assertEqual(OQ_SUPERSEDED, q1.status)
        self.assertIsNone(q1.activated_at)
        self.assertEqual([], self._live())

    # ── F. a terminal newer sibling never hands the field back ───────────────
    def test_p06_an_inactive_question_never_reactivates_when_the_newer_one_ends(self):
        """Sequence F. Q1 was superseded by a delivered Q2; Q2 was then ANSWERED; Q1's own
        late `read` arrives.

        Q1 must not come back. Two independent reasons, and both are asserted: the write is
        conditional off `PENDING_DELIVERY` and Q1 is `SUPERSEDED`, and the ordering guard
        sees a newer sibling. Nothing anywhere moves a row back to `PENDING_DELIVERY`, which
        is what makes "terminal" mean terminal.

        Q1 here is a question that WAS delivered and activated before being superseded, so
        its `activated_at` and `delivery_status` are real history and must survive untouched
        — "not reactivated" is a statement about `status` and `live()`, not an instruction to
        erase what happened. `p05` covers the other shape, where Q1 never activated at
        all."""
        q1 = self._incumbent()
        q2 = self._ask("wamid.INC2")
        self._status(q2, "delivered")
        self.assertEqual(OQ_SUPERSEDED, q1.status)
        was_activated, was_delivery = q1.activated_at, q1.delivery_status
        self.assertIsNotNone(was_activated, "the fixture did not activate Q1 at all")
        self.assertTrue(OQ.close_answered(self.db, q2, claim_id="claim-1"))
        self.db.flush()
        self.db.expire_all()
        self.assertEqual(OQ_ANSWERED, q2.status)

        for late in ("read", "delivered", "read"):
            with self.subTest(late=late):
                self.assertEqual(0, self._status(q1, late),
                                 "a terminal question was reactivated by a late callback")
                self.assertEqual(OQ_SUPERSEDED, q1.status)
                self.assertEqual(OQ.CloseReason.SUPERSEDED_BY_REASK, q1.closed_reason)
                self.assertEqual((was_activated, was_delivery),
                                 (q1.activated_at, q1.delivery_status),
                                 "a late callback re-stamped a terminal row")
                self.assertNotIn(q1.id, self._live())
        self.assertEqual(OQ_ANSWERED, q2.status, "the answered question was touched")
        self.assertEqual([], self._live())

    # ── the rule, as a property rather than a sequence ───────────────────────
    def test_p07_only_an_activation_can_deactivate_an_incumbent(self):
        """The contract in one assertion: across every non-activating event that can reach
        the service, an OUTSTANDING incumbent is still OUTSTANDING afterwards.

        `sent`, `pending`, an unknown status, a status for another message, the acceptance
        of a re-ask, that re-ask failing, and a duplicate of any of them. The only event
        that moves the field is a `delivered`/`read` for a NEWER question."""
        q1 = self._incumbent()
        q2 = self._ask("wamid.INC2")
        events = [("Q2", "sent"), ("Q2", "pending"), ("Q2", "queued"), ("Q2", "failed"),
                  ("Q2", "failed"), ("other", "delivered"), ("Q2", "delivered")]
        for which, state in events:
            with self.subTest(event=f"{which}:{state}"):
                if which == "other":
                    OQ.activate_on_status(self.db, wa_message_id="wamid.NOBODY",
                                          status=state)
                    self.db.flush()
                    self.db.expire_all()
                else:
                    self._status(q2, state)
                self.assertEqual(OQ_OUTSTANDING, q1.status,
                                 f"`{state}` on {which} deactivated the incumbent")
                self.assertEqual([q1.id], self._live())
        # Q2 is UNDELIVERED by now, so even its own `delivered` cannot take the field
        self.assertEqual(OQ_UNDELIVERED, q2.status)


# ══════════════════════════════════════════════════════════════════════════════
class TestR2D_Closure(unittest.TestCase):
    """A question closes because ADMITTED evidence answered it — and for no other reason."""

    def _asked(self, **kw):
        """Ask the location question AND have Meta report it delivered — an ACTIVE
        question, which is the only kind that can be answered or can suppress anything."""
        t = _Thread(**kw)
        t.ask_and_deliver()
        self.assertEqual([OQ_OUTSTANDING], [q.status for q in t.questions()])
        return t

    def test_d01_an_ambiguous_locality_does_not_close_it(self):
        """`en san nicolas` — R3 refuses it, so R2 has nothing to close on. The customer is
        asked to disambiguate and the question stays open, which is the point."""
        t = self._asked()
        self.addCleanup(t.close)
        t.turn("en san nicolas", evidence=_evidence(_loc("san nicolas")))
        self.assertEqual([OQ_OUTSTANDING], [q.status for q in t.questions()])
        self.assertEqual((None, None), t.zone())

    def test_d02_an_unsupported_locality_does_not_close_it(self):
        t = self._asked()
        self.addCleanup(t.close)
        t.turn("en mar del plata", evidence=_evidence(_loc("Mar del Plata")))
        self.assertEqual([OQ_OUTSTANDING], [q.status for q in t.questions()])
        self.assertEqual((None, None), t.zone())

    def test_d03_an_inferred_locality_does_not_close_it(self):
        """R1's `INFERRED_NOT_EXPLICIT`: the words are not the customer's, so the claim is
        not admitted and a question cannot close on it."""
        t = self._asked()
        self.addCleanup(t.close)
        t.turn("en flore", evidence=_evidence(_loc("Flores")))
        self.assertEqual([OQ_OUTSTANDING], [q.status for q in t.questions()])
        self.assertEqual((None, None), t.zone())

    def test_d04_a_customer_origin_clause_does_not_close_it(self):
        """`soy de flores` is where the CUSTOMER lives (LR-2). R1 refuses it as
        `ROLE_DECIDED_BY_TEXT`; the question about the VEHICLE stays open."""
        t = self._asked()
        self.addCleanup(t.close)
        t.turn("soy de flores", evidence=_evidence(_loc("Flores")))
        self.assertEqual([OQ_OUTSTANDING], [q.status for q in t.questions()])
        self.assertEqual((None, None), t.zone())

    def test_d05_a_stale_reading_does_not_close_it(self):
        """Evidence naming a message from an earlier turn is `STALE_TURN` at the boundary
        and therefore cannot answer anything."""
        t = self._asked()
        self.addCleanup(t.close)
        t.turn("ya te lo dije", evidence=_evidence(_loc("Flores"),
                                                  message_ids=("wamid.OLD",)))
        self.assertEqual([OQ_OUTSTANDING], [q.status for q in t.questions()])

    def test_d06_the_model_saying_the_customer_answered_closes_nothing(self):
        """The authority the review refuses to give the model. The reply payload asserts the
        customer answered; the burst carries no locality; nothing closes."""
        t = self._asked()
        self.addCleanup(t.close)
        claims_answered = json.dumps({
            "intent": "QUALIFYING",
            "reply": "¡Perfecto, ya tengo la ubicación!",
            "deferred_interest": False, "candidate": {"action": "none"},
            "extracted": {"zone_detail": "Flores", "zone_group": "CABA"},
            "lead_flag": None, "needs_human": False})
        t.turn("gracias", payload=claims_answered)
        self.assertEqual([OQ_OUTSTANDING], [q.status for q in t.questions()])

    def test_d07_answering_the_wrong_slot_keeps_the_right_question_open(self):
        """`W1F-C-12`: the bot asked for the VEHICLE, the customer gave a locality. The
        locality is admitted and kept; the vehicle question is still outstanding."""
        t = _Thread()
        self.addCleanup(t.close)
        row, _ = _record_live(t.db, t.thread_id, kind=OQ_KIND_VEHICLE_IDENTITY,
                              wamid="wamid.VEH")
        t.turn("en flores", evidence=_evidence(_loc("Flores")))
        self.assertEqual(("CABA", "Flores"), t.zone(), "the locality was discarded")
        self.db_status = [q.status for q in t.questions()]
        self.assertEqual([OQ_OUTSTANDING], self.db_status,
                         "the vehicle question was closed by a locality")

    def test_d08_a_question_from_a_previous_cycle_is_not_closed(self):
        """`W1F-C-22`: prior-cycle evidence must not bind, and prior-cycle questions must
        not close. `cycle_key` is the column that makes both checkable."""
        t = _Thread()
        self.addCleanup(t.close)
        row, _ = _record_live(t.db, t.thread_id, cycle=999, wamid="wamid.OLDCYCLE")
        t.turn("en flores", evidence=_evidence(_loc("Flores")))
        t.db.expire_all()
        self.assertEqual(OQ_OUTSTANDING, row.status)
        self.assertEqual(999, row.cycle_key)

    def test_d08b_a_negated_locality_does_not_close_it(self):
        """Polarity. "el auto NO está en Flores" denies the value; R1 refuses it as
        `NEGATED` + HOLD, so there is nothing admitted to close the question with — and the
        denial is still persisted, because a refusal nobody can read is invisible (G-11)."""
        t = _Thread()
        self.addCleanup(t.close)
        row, _ = _record_live(t.db, t.thread_id)
        negated = EA.AdmissionRecord(
            claim_type=ClaimType.INSPECTION_LOCATION, value="Flores",
            outcome=ReconciliationOutcome.HOLD.value, reason=EA.AdmissionReason.NEGATED,
            normalized=None,
            provenance=EA.Provenance(producer="semantic:understand/1.20",
                                     polarity="NEGATED", claim_id="negated-1"))
        record, why = OQ.match(row, [negated], cycle_key=0)
        self.assertIsNone(record, "a negated locality closed the question")
        self.assertEqual(OQ.NoMatch.NOT_ADMITTED, why)
        rows = TE.persist_admission(
            t.db, thread_id=t.thread_id, cycle_key=0,
            result=EA.AdmissionResult(records=(negated,), claims=(None,)))
        self.assertEqual(EA.AdmissionReason.NEGATED, rows[0].reason)
        self.assertIsNone(TE.bindable_locality(t.db, thread_id=t.thread_id, cycle_key=0))

    def test_d09_the_closure_names_the_claim_and_the_evidence_row(self):
        t = self._asked()
        self.addCleanup(t.close)
        t.turn("en flores", evidence=_evidence(_loc("Flores")))
        row = t.questions()[0]
        self.assertEqual(OQ_ANSWERED, row.status)
        self.assertTrue(row.answered_by_claim_id)
        self.assertIsNotNone(row.answered_by_evidence_id)
        self.assertIsNotNone(row.closed_at)
        evidence = [e for e in t.evidence_rows() if e.id == row.answered_by_evidence_id]
        self.assertEqual(1, len(evidence))
        self.assertEqual("CABA|Flores", evidence[0].normalized_value)

    def test_d10_replaying_the_answer_closes_it_once(self):
        """A replay is the SAME `wa_message_id` delivered again — duplicate webhook
        delivery — not a second message with the same words. The engine's own inbound dedup
        is the first guard and the conditional close is the second; both are exercised."""
        t = self._asked()
        self.addCleanup(t.close)
        from app.schemas.conversation import ConversationHandleIn
        H._seq[0] += 1
        wamid = f"wamid.R2R{H._seq[0]:05d}"
        for _ in range(3):
            event = ConversationHandleIn(
                thread_id=t.thread_id, wa_message_id=wamid, wa_id=t.wa, text="en flores",
                unanswered_recent_user_messages=["en flores"],
                recent_user_messages=["en flores"])
            with patch.dict(os.environ, H._ARMED), patch("urllib.request.urlopen") as mu:
                mu.return_value.__enter__ = lambda s: s
                mu.return_value.__exit__ = MagicMock()
                mu.return_value.read = lambda: json.dumps(
                    {"choices": [{"message": {"content": _NEUTRAL}}]}).encode()
                with patch("app.services.conversation_engine.OutboundSafetyGate") as MG:
                    gate, allowed = MagicMock(), MagicMock()
                    allowed.outcome = "allowed"
                    allowed.message_id = 1
                    gate.attempt.return_value = allowed
                    MG.return_value = gate
                    with patch("app.services.conversation_engine."
                               "_send_whatsapp_cloud_text",
                               return_value=("wamid.REPLY", {})):
                        with patch("app.services.conversation_engine."
                                   "_send_whatsapp_cloud_flow",
                                   return_value=("wamid.FLOW", {})):
                            with patch("app.services.conversation_engine."
                                       "reset_unanswered_alert"):
                                with patch.object(ConversationEngine,
                                                  "_semantic_turn_evidence",
                                                  return_value=_evidence(_loc("Flores"))):
                                    t.engine.handle(event)
        rows = t.questions()
        self.assertEqual(1, len(rows))
        self.assertEqual(OQ_ANSWERED, rows[0].status)
        self.assertEqual(1, len([e for e in t.evidence_rows()
                                 if e.outcome == ReconciliationOutcome.ACCEPT.value]),
                         "a replayed delivery produced a second accepted evidence row")

    def test_d11_three_genuinely_distinct_turns_close_it_once(self):
        """`W1F-C-24`, the other reading of it: three SEPARATE inbound messages all saying
        `en flores`. Each is a real turn and each legitimately produces its own claim, so
        there are three evidence rows — but ONE closure, ONE zone, and the location is never
        asked for again.

        What this test deliberately does NOT assert, because R2 does not promise it: that
        the later turns escape the generic greeting. Only the FIRST of them answered an
        outstanding question; by the second there is nothing outstanding, and a bare
        locality with no service signal still reaches Layer F step 9 exactly as it does on
        the committed system. R2's contract is about the question it asked, not about every
        subsequent fragment, and a test that claimed otherwise would be asserting a
        behaviour nothing implements."""
        t = self._asked()
        self.addCleanup(t.close)
        turns = [t.turn("en flores", evidence=_evidence(_loc("Flores")))
                 for _ in range(3)]
        rows = t.questions()
        self.assertEqual(1, len(rows))
        self.assertEqual(OQ_ANSWERED, rows[0].status)
        self.assertEqual(("CABA", "Flores"), t.zone())
        self.assertFalse(any(_GREETING_MARK in s for s in turns[0].sent),
                         "the turn that answered the question was still greeted")
        for index, turn in enumerate(turns):
            with self.subTest(turn=index):
                self.assertEqual([], turn.attempts,
                                 "something asked for a fact already admitted")
                text = " ".join(turn.sent).lower()
                for probe in ("en qué zona", "en que zona", "localidad o barrio"):
                    self.assertNotIn(probe, text)


# ══════════════════════════════════════════════════════════════════════════════
class TestR2E_CrossTurn(unittest.TestCase):
    """Evidence persisted, and bound only by the certified rules."""

    def test_e01_refusals_are_persisted_with_their_reason(self):
        """G-11: a refusal nobody can read is indistinguishable from no decision."""
        t = _Thread()
        self.addCleanup(t.close)
        t.turn("en san nicolas", evidence=_evidence(_loc("san nicolas")))
        rows = t.evidence_rows()
        self.assertTrue(rows, "the refusal left no record")
        self.assertEqual(EA.AdmissionReason.LOCALITY_AMBIGUOUS, rows[0].reason)
        self.assertNotEqual(ReconciliationOutcome.ACCEPT.value, rows[0].outcome)
        self.assertEqual("INSPECTION_LOCATION", rows[0].role)
        self.assertTrue(rows[0].producer.startswith("semantic"))

    def test_e02_a_refused_row_is_never_bindable(self):
        """Two classes of refusal, and the second one is why this test is not one line.

        A coverage or ambiguity refusal carries NO normalized value, so it would be skipped
        even without the outcome filter. But `CONFIRMED_VALUE_PRESENT` and
        `OUTRANKED_BY_EXPLICIT` are refusals the validator RESOLVED first — they carry a
        real `(zone_group, zone_detail)` — and for those the `outcome = ACCEPT` filter is the
        only thing standing between a refused reading and a candidate's location.

        Mutation M06 removed that filter and SURVIVED the first run of this suite, because
        only the first class was covered. The assertion was weak, not the test missing — the
        same lesson R1's M10 left — and it is pinned per class now."""
        t = _Thread()
        self.addCleanup(t.close)
        t.turn("en mar del plata", evidence=_evidence(_loc("Mar del Plata")))
        self.assertIsNone(TE.bindable_locality(t.db, thread_id=t.thread_id,
                                               cycle_key=t.cycle()),
                          "an unsupported locality became bindable")

        # a refusal that DID resolve: the value is real, the decision was still no
        for reason, outcome in (
                (EA.AdmissionReason.CONFIRMED_VALUE_PRESENT,
                 ReconciliationOutcome.HOLD.value),
                (EA.AdmissionReason.OUTRANKED_BY_EXPLICIT,
                 ReconciliationOutcome.HOLD.value)):
            with self.subTest(reason=reason):
                other = _Thread()
                self.addCleanup(other.close)
                record = EA.AdmissionRecord(
                    claim_type=ClaimType.INSPECTION_LOCATION, value="Flores",
                    outcome=outcome, reason=reason, normalized=("CABA", "Flores"),
                    provenance=EA.Provenance(producer="semantic:understand/1.20",
                                             claim_id=f"refused-{reason}"))
                rows = TE.persist_admission(
                    other.db, thread_id=other.thread_id, cycle_key=0,
                    result=EA.AdmissionResult(records=(record,), claims=(None,)))
                self.assertEqual(("CABA", "Flores"), rows[0].zone_pair(),
                                 "the fixture does not exercise the case it is for")
                self.assertIsNone(
                    TE.bindable_locality(other.db, thread_id=other.thread_id,
                                         cycle_key=0),
                    f"a {reason} refusal became bindable")

    def test_e03_evidence_binds_once_and_only_to_the_focus_candidate(self):
        t = _Thread()
        self.addCleanup(t.close)
        t.turn("en flores", evidence=_evidence(_loc("Flores")))
        row = TE.bindable_locality(t.db, thread_id=t.thread_id, cycle_key=t.cycle())
        self.assertIsNotNone(row)
        self.assertTrue(TE.bind(t.db, row, 41))
        self.assertFalse(TE.bind(t.db, row, 42), "a bound row was bound again")
        self.assertEqual(41, row.bound_to_candidate_id)
        self.assertIsNone(TE.bindable_locality(t.db, thread_id=t.thread_id,
                                               cycle_key=t.cycle()))

    def test_e04_a_mentioned_candidate_never_inherits_the_locality(self):
        """M21.2.14 isolation, through persistence. A `mentioned` MOTO from an earlier
        handoff is not this conversation's subject."""
        t = _Thread()
        self.addCleanup(t.close)
        other = WhatsAppThreadCandidate(
            thread_id=t.thread_id, marca="Honda", modelo="CB500", anio=2020,
            tipo_vehiculo="MOTO", status="mentioned")
        t.db.add(other)
        t.db.commit()
        t.turn("en flores", evidence=_evidence(_loc("Flores")))
        t.turn("es un toyota corolla 2024")
        t.db.expire_all()
        moto = [c for c in t.candidates() if c.tipo_vehiculo == "MOTO"][0]
        focus = [c for c in t.candidates() if c.status == "current_focus"][0]
        self.assertEqual((None, None), (moto.zone_group, moto.zone_detail))
        self.assertIsNone(moto.zone_evidence_id)
        self.assertEqual(("CABA", "Flores"), (focus.zone_group, focus.zone_detail))

    def test_e03b_prior_cycle_evidence_never_binds_to_a_later_candidate(self):
        """`W1F-C-22`, the evidence half — and mutation M05 is why this test exists.

        M05 replaces the `cycle_key` filter in `bindable_locality` with a tautology and
        SURVIVED the clean run: the suite had a test for a prior-cycle QUESTION (`d08`) and
        none for prior-cycle EVIDENCE, so the one column that makes contamination checkable
        was unguarded. The closeout claimed `W1F-C-22` closed; the claim was right and the
        proof was missing.

        A cycle boundary is `_execute_cycle_reset` writing
        `current_cycle_start_message_db_id`, so that is what is simulated: the locality is
        admitted in the old cycle, the cycle moves, and the candidate created afterwards
        must start with no zone."""
        t = _Thread()
        self.addCleanup(t.close)
        t.turn("en flores", evidence=_evidence(_loc("Flores")))
        old_cycle = t.cycle()
        rows = [r for r in t.evidence_rows()
                if r.outcome == ReconciliationOutcome.ACCEPT.value]
        self.assertEqual(1, len(rows))
        self.assertEqual(old_cycle, rows[0].cycle_key)
        self.assertIsNotNone(TE.bindable_locality(t.db, thread_id=t.thread_id,
                                                  cycle_key=old_cycle))

        # the conversation cycle moves on
        state = t.state()
        state.current_cycle_start_message_db_id = 9999
        state.home_zone_group = None
        state.home_zone_detail = None
        t.db.flush()
        new_cycle = t.engine._r2_cycle_key(state)
        self.assertNotEqual(old_cycle, new_cycle)
        self.assertIsNone(
            TE.bindable_locality(t.db, thread_id=t.thread_id, cycle_key=new_cycle),
            "evidence from a previous cycle was offered to the new one")

        # and through the engine: the candidate created in the NEW cycle carries nothing
        t.turn("es un toyota corolla 2024")
        focus = [c for c in t.candidates() if c.status == "current_focus"]
        self.assertTrue(focus)
        self.assertEqual((None, None), (focus[0].zone_group, focus[0].zone_detail),
                         "a prior-cycle locality contaminated a new candidate")
        self.assertIsNone(focus[0].zone_evidence_id)

    def test_e04b_the_binding_method_itself_refuses_a_non_focus_candidate(self):
        """The rule at its enforcement point, not at the call site.

        Mutation M07 deleted the caller's `current_focus` condition and SURVIVED the first
        run of this suite: `test_e04` creates the `mentioned` MOTO before the focus
        candidate, so the mutated call site was never exercised with a non-focus status.
        The check moved into `_bind_evidence_to_candidate`, and this calls that method
        DIRECTLY with a mentioned candidate — which is the only way to prove a rule cannot
        be bypassed by a caller."""
        t = _Thread()
        self.addCleanup(t.close)
        t.turn("en flores", evidence=_evidence(_loc("Flores")))
        self.assertIsNotNone(TE.bindable_locality(t.db, thread_id=t.thread_id,
                                                  cycle_key=t.cycle()),
                             "the fixture has no bindable evidence to refuse")
        mentioned = WhatsAppThreadCandidate(
            thread_id=t.thread_id, marca="Honda", modelo="CB500", anio=2020,
            tipo_vehiculo="MOTO", status="mentioned")
        t.db.add(mentioned)
        t.db.flush()
        ctx = t.engine._load_context(t.thread_id)
        bound = t.engine._bind_evidence_to_candidate(ctx, t.state(), mentioned)
        self.assertFalse(bound, "a mentioned candidate was bound locality evidence")
        t.db.expire_all()
        self.assertEqual((None, None), (mentioned.zone_group, mentioned.zone_detail))
        self.assertIsNone(mentioned.zone_evidence_id)
        self.assertIsNotNone(TE.bindable_locality(t.db, thread_id=t.thread_id,
                                                  cycle_key=t.cycle()),
                             "the refused attempt consumed the evidence row")

    def test_e04c_switching_the_focus_does_not_move_the_bound_locality(self):
        """Candidate switching. The customer gives a locality, a candidate takes it, then a
        SECOND vehicle becomes the focus. The new focus must not silently inherit the first
        one's locality — `bind()` is once-only, so the row is no longer on offer — and the
        first candidate keeps what it was told rather than having it moved or erased.

        Driven at the binding method with a second focus candidate, because a correction
        text updates the existing candidate rather than creating one: measured, and asserted
        rather than assumed."""
        t = _Thread()
        self.addCleanup(t.close)
        t.turn("en flores", evidence=_evidence(_loc("Flores")))
        t.turn("es un toyota corolla 2024")
        first = [c for c in t.candidates() if c.status == "current_focus"][0]
        self.assertEqual(("CABA", "Flores"), (first.zone_group, first.zone_detail))
        self.assertIsNotNone(first.zone_evidence_id)
        self.assertIsNone(TE.bindable_locality(t.db, thread_id=t.thread_id,
                                               cycle_key=t.cycle()),
                          "the bound row is still on offer to the next candidate")

        # the focus moves to a second vehicle
        second = WhatsAppThreadCandidate(
            thread_id=t.thread_id, marca="Ford", modelo="Ranger", anio=2021,
            tipo_vehiculo="PICKUP", status="current_focus")
        first.status = "mentioned"
        t.db.add(second)
        t.db.flush()
        state = t.state()
        state.current_focus_candidate_id = second.id
        t.db.flush()
        ctx = t.engine._load_context(t.thread_id)
        bound = t.engine._bind_evidence_to_candidate(ctx, state, second)
        self.assertFalse(bound, "the new focus inherited an already-bound evidence row")
        t.db.expire_all()
        self.assertEqual((None, None), (second.zone_group, second.zone_detail))
        self.assertIsNone(second.zone_evidence_id)
        # the first candidate keeps what it was told; nothing was moved or erased
        kept = t.db.query(WhatsAppThreadCandidate).filter_by(id=first.id).one()
        self.assertEqual(("CABA", "Flores"), (kept.zone_group, kept.zone_detail))
        self.assertIsNotNone(kept.zone_evidence_id)

    def test_e04d_stale_rows_from_an_armed_period_cannot_bind_once_the_flags_are_off(self):
        """The rollback claim of §14, as a test — and MUTATION M34 is why it exists.

        M34 removes the interlock guard from the candidate-binding site and **survived** the
        first run, because with the flags off no evidence rows are produced, so
        `bindable_locality` finds nothing and the guard looks redundant. It is not redundant
        in the one situation that matters operationally: the flags were ON, evidence was
        persisted, and then the flags were turned OFF — the behavioural rollback §14 offers
        as the recovery path. Without the guard, a candidate created afterwards would consume
        a leftover row and write a zone, which would make "stale rows cannot change behaviour
        once the flags are OFF" false exactly when someone is relying on it.

        So: arm the posture, persist a locality, disarm, create a candidate, and require that
        nothing is bound."""
        t = _Thread()
        self.addCleanup(t.close)
        t.turn("en flores", evidence=_evidence(_loc("Flores")))
        rows = [r for r in t.evidence_rows()
                if r.outcome == ReconciliationOutcome.ACCEPT.value]
        self.assertEqual(1, len(rows), "the armed period produced no evidence to leave behind")
        self.assertIsNone(rows[0].bound_to_candidate_id)

        # the behavioural rollback: flags off, same image, same database, stale rows present
        t.engine.settings.evidence_admission_enabled = False
        t.engine.settings.outstanding_question_enabled = False
        self.assertFalse(t.engine._integrated_features_armed())
        state = t.state()
        state.home_zone_group = None
        state.home_zone_detail = None
        t.db.flush()

        t.turn("es un toyota corolla 2024")
        focus = [c for c in t.candidates() if c.status == "current_focus"]
        self.assertTrue(focus, "no candidate was created")
        self.assertEqual((None, None), (focus[0].zone_group, focus[0].zone_detail),
                         "a stale evidence row bound after the flags were turned off")
        self.assertIsNone(focus[0].zone_evidence_id)
        t.db.expire_all()
        self.assertIsNone(rows[0].bound_to_candidate_id,
                          "the stale row was consumed while the flags were off")

    def test_e05_evidence_does_not_leak_between_threads(self):
        a = _Thread()
        b = _Thread()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        a.turn("en flores", evidence=_evidence(_loc("Flores")))
        self.assertIsNone(TE.bindable_locality(b.db, thread_id=b.thread_id,
                                               cycle_key=b.cycle()))
        b.turn("es un toyota corolla 2024")
        focus = [c for c in b.candidates() if c.status == "current_focus"]
        if focus:
            self.assertEqual((None, None),
                             (focus[0].zone_group, focus[0].zone_detail))

    def test_e06_a_superseded_row_is_not_bindable(self):
        """Review §8.4: a correction is a new row that supersedes an old one, and the old
        value's provenance is retained rather than overwritten."""
        t = _Thread()
        self.addCleanup(t.close)
        t.turn("en flores", evidence=_evidence(_loc("Flores")))
        first = TE.bindable_locality(t.db, thread_id=t.thread_id, cycle_key=t.cycle())
        t.turn("no, en palermo", evidence=_evidence(_loc("Palermo")))
        rows = [r for r in t.evidence_rows()
                if r.outcome == ReconciliationOutcome.ACCEPT.value]
        if len(rows) >= 2:
            self.assertTrue(TE.supersede(t.db, rows[0], rows[1]))
            self.assertEqual(rows[1].id, rows[0].superseded_by_id)
            self.assertIsNotNone(rows[0].value, "the superseded value was erased")

    def test_e07_persistence_is_idempotent_on_the_claim_identity(self):
        """One claim, one row — and the identity is the claim's own content hash, so the
        idempotency is at the database (`UNIQUE (thread_id, claim_id)`) and not in a
        read-then-write a second worker could also pass.

        Driven at the service, because that is the level the guarantee lives at: the same
        `AdmissionResult` persisted three times must leave one row. Three genuinely
        different turns legitimately leave three — `test_d11` measures that case and the one
        closure it still produces."""
        t = _Thread()
        self.addCleanup(t.close)
        result = EA.AdmissionResult(records=(_admitted(claim_id="stable-1"),),
                                    claims=(None,))
        for _ in range(3):
            TE.persist_admission(t.db, thread_id=t.thread_id, cycle_key=0, result=result)
        accepted = [r for r in t.evidence_rows()
                    if r.outcome == ReconciliationOutcome.ACCEPT.value]
        self.assertEqual(1, len(accepted))
        self.assertEqual("CABA|Flores", accepted[0].normalized_value)

    def test_e08_with_the_interlock_incomplete_no_row_is_written_at_all(self):
        """And no zone either, which is the part OC-R2 CORRECTION 2 changed: an incomplete
        posture is inert, not partially active."""
        for r1, r2, safety in ((True, False, True), (False, True, True),
                               (True, True, False)):
            with self.subTest(r1=r1, r2=r2, r3=safety):
                t = _Thread(r1=r1, r2=r2, safety=safety)
                try:
                    t.turn("en flores", evidence=_evidence(_loc("Flores")))
                    self.assertEqual([], t.evidence_rows())
                    self.assertEqual([], t.questions())
                    self.assertEqual((None, None), t.zone())
                finally:
                    t.close()


# ══════════════════════════════════════════════════════════════════════════════
class TestR2F_Form(unittest.TestCase):
    """W1FRV-04: the Location Flow answer takes the same route as a locality from text."""

    def _submit(self, t, data, *, cand_id=None):
        return H._run_form(t.db, t.engine, t.thread_id, cand_id or t.cand_id, data)

    def test_f01_a_form_answer_reaches_the_candidate_through_the_reconciler(self):
        t = _Thread(vehicle=True)
        self.addCleanup(t.close)
        self._submit(t, {"zona_general": "CABA", "localidad": "Flores"})
        focus = [c for c in t.candidates() if c.status == "current_focus"][0]
        self.assertEqual(("CABA", "Flores"), (focus.zone_group, focus.zone_detail),
                         "the form still writes only state.home_zone_*")

    def test_f02_with_r2_off_the_form_keeps_its_committed_behaviour(self):
        t = _Thread(vehicle=True, r2=False)
        self.addCleanup(t.close)
        self._submit(t, {"zona_general": "CABA", "localidad": "Flores"})
        self.assertEqual(("CABA", "Flores"), t.zone())

    def test_f03_an_unsupported_form_locality_stays_fail_closed(self):
        t = _Thread(vehicle=True)
        self.addCleanup(t.close)
        self._submit(t, {"zona_general": "CABA", "localidad": "Mar del Plata"})
        self.assertEqual((None, None), t.zone())
        focus = [c for c in t.candidates() if c.status == "current_focus"][0]
        self.assertEqual((None, None), (focus.zone_group, focus.zone_detail))

    def test_f04_an_ambiguous_form_locality_stays_fail_closed(self):
        t = _Thread(vehicle=True)
        self.addCleanup(t.close)
        self._submit(t, {"zona_general": "NORTE", "localidad": "San Nicolás"})
        self.assertEqual((None, None), t.zone())


# ══════════════════════════════════════════════════════════════════════════════
class TestR2G_Wait(unittest.TestCase):
    """R1-PL-01, by the R1 §9.2 PASS-B method: a real provider and a sleeping interpreter.

    Dispatch is measured separately from waiting throughout. `_run_shadow_understand` sits
    above Layer A at the starting HEAD and R2 does not move it, so every turn still STARTS a
    background interpretation; what R2 removes is the WAIT, on a turn whose own words cannot
    produce an admissible locality. `_turn_semantic_calls == 0` would not prove any of this
    and is not used as if it did.
    """

    SLEEP = 1.00

    class _Slow:
        constructed = 0

        def __init__(self, settings):
            type(self).constructed += 1

        def interpret(self, *a, **kw):
            time.sleep(TestR2G_Wait.SLEEP)
            return None

    def _timed(self, text, *, r2, r1=True):
        from app.services import semantic_interpreter as SI
        type(self)._Slow.constructed = 0
        t = _Thread(r1=r1, r2=r2)
        try:
            t.engine.settings.shadow_understand_enabled = True
            t.engine.settings.shadow_understand_async = True
            t.engine.settings.semantic_same_turn_enabled = True
            t.engine.settings.semantic_same_turn_timeout_seconds = 5.0
            t.engine.settings.shadow_evidence_path = ""
            with patch.object(SI, "SemanticTurnInterpreter", self._Slow), \
                    patch("app.services.shadow_recorder.record_shadow"):
                started = time.monotonic()
                H._run(t.db, t.engine, t.thread_id, t.wa, text, cand_id=t.cand_id)
                elapsed = time.monotonic() - started
            return (elapsed, bool(t.engine._turn_semantic_dispatched),
                    bool(getattr(t.engine, "_turn_semantic_wait_skipped", False)),
                    type(self)._Slow.constructed)
        finally:
            t.close()
            time.sleep(self.SLEEP * 1.4)       # drain the single bounded shadow worker

    def test_g01_buenas_does_not_wait_for_the_interpreter(self):
        """R1-PL-01, as the owner accepted it: **R1/R2 introduce no new semantic wait on a
        locality-free turn.** `buenas` is the canonical case.

        Stated absolutely rather than as a before/after delta, and that is a consequence of
        OC-R2 CORRECTION 2: with the interlock, the "R1 only" posture is inert, so there is
        no half-armed configuration that pays a wait to compare against. The requirement is
        therefore the direct one — the committed posture does not wait, and the integrated
        posture does not wait either."""
        committed, _, _, _ = self._timed("buenas", r1=False, r2=False)
        integrated, dispatched, skipped, constructed = self._timed("buenas", r2=True)
        self.assertLess(committed, self.SLEEP * 0.5,
                        f"the committed posture waited ({committed:.3f}s) — the baseline "
                        f"for this claim is wrong")
        self.assertLess(integrated, self.SLEEP * 0.5,
                        f"the integrated posture introduced a wait on `buenas` "
                        f"({integrated:.3f}s)")
        self.assertTrue(skipped)
        # DISPATCH IS UNCHANGED, and this is the half that is easy to overstate.
        self.assertTrue(dispatched, "the background interpretation was not dispatched")
        self.assertEqual(1, constructed,
                         "R2 removed the dispatch, not just the wait — that would be a "
                         "change to inherited shadow behaviour")

    def test_g02_a_burst_with_a_locality_still_waits(self):
        """"Preserve the semantic wait where a potentially admissible locality mention
        genuinely requires the same-turn result."""
        elapsed, dispatched, skipped, _ = self._timed("en flores", r2=True)
        self.assertGreaterEqual(elapsed, self.SLEEP * 0.8,
                                f"a locality turn stopped waiting ({elapsed:.3f}s)")
        self.assertFalse(skipped)
        self.assertTrue(dispatched)

    def test_g03_admission_skips_its_read_on_every_locality_free_burst(self):
        """Stated as what R2 actually promises. Admission's own wait is gone on all of
        these; the TURN still waits where an inherited reader needs the interpretation for
        its own reasons, which R2 does not touch."""
        for text in ("buenas", "hola", "cuanto sale?", "es un corolla del 2024",
                     "de que consta la revision?", "me mordi la boca", "gracias"):
            with self.subTest(text=text):
                _, _, skipped, _ = self._timed(text, r2=True)
                self.assertTrue(skipped, "admission waited on a locality-free burst")

    def test_g04_the_terminal_pre_gates_remain_free_of_the_wait(self):
        """Layers A/B/C exit above the boundary — R1's OC-R1 placement — so they neither
        skip nor pay: they never reach admission at all."""
        for text in ("tengo una moto, la revisan?", "llamame por favor",
                     "necesito hacer la transferencia del auto"):
            with self.subTest(text=text):
                elapsed, dispatched, _, constructed = self._timed(text, r2=True)
                self.assertLess(elapsed, self.SLEEP,
                                f"a terminal pre-gate waited ({elapsed:.3f}s)")
                self.assertTrue(dispatched)
                self.assertEqual(1, constructed)

    def test_g05_the_predicate_is_the_one_shared_rule(self):
        """R1-PL-01's hard requirement: ONE predicate, composed of the existing verbatim and
        catalogue rules, not a second copy of either."""
        from app.services import claim_projection as CP
        self.assertTrue(hasattr(CP, "locality_mention_possible"))
        source = (BACKEND_DIR / "app" / "services" / "claim_projection.py").read_text(
            encoding="utf-8")
        self.assertEqual(1, source.count("def locality_mention_possible"))
        # it resolves through the caller's resolver, so it holds no catalogue of its own
        self.assertEqual(1, CE_SOURCE.count("def _locality_possible_in_burst"))
        self.assertEqual(1, CE_SOURCE.count("locality_mention_possible("))

    def test_g06_the_predicate_is_self_protecting_on_the_article_rule(self):
        """The predicate closes a latent R1 defect, and this pins the MECHANISM that does
        it — not just the outcome.

        `me mordí la boca` must not read La Boca (L4.7W5-F5, `W1F-C-18`). R1 validates a
        claim value in ISOLATION, where that article rule cannot apply, so an interpreter
        reading `INSPECTION_LOCATION = "la boca"` out of that sentence IS admitted by R1
        alone — measured below. R2 closes it, and the reason is worth asserting precisely:
        the one case where the matcher is non-monotone (`resolve(burst)` is None while
        `resolve("la boca")` is not) is **exactly** the case where the predicate skips the
        semantic read, so no claim is ever produced to admit. The predicate is
        self-protecting, which is why the explicit context check that once sat in the
        validator was dead code and was removed.

        So the assertions are: the wait was skipped, NO admission record exists, and no zone
        was written — and the protection is not a blanket refusal, because the same locality
        in a locative context still resolves."""
        t_on = _Thread(r2=True)
        self.addCleanup(t_on.close)
        t_on.turn("me mordi la boca", evidence=_evidence(_loc("la boca")))
        self.assertEqual((None, None), t_on.zone())
        self.assertTrue(getattr(t_on.engine, "_turn_semantic_wait_skipped", False),
                        "the predicate did not skip — the protection has moved")
        self.assertEqual((), tuple(t_on.engine._turn_admission.records),
                         "a claim reached the boundary out of an idiom")

        # The posture that MEASURED the defect, kept here as the record of why the
        # interlock exists. OC-R2 CORRECTION 2 made it unreachable through the flags — R1
        # ON with R2 OFF is now inert — so the defect is reproduced by arming admission
        # directly, which is the only way left to show what the interlock is protecting.
        t_r1 = _Thread(r1=False, r2=False)
        self.addCleanup(t_r1.close)
        t_r1.engine._admission_on = lambda: True          # the pre-interlock posture
        t_r1.turn("me mordi la boca", evidence=_evidence(_loc("la boca")))
        self.assertEqual(("CABA", "La Boca"), t_r1.zone(),
                         "the defect the interlock exists for no longer reproduces; "
                         "§9.2 of the closeout is stale")

        # and it is not a blanket refusal
        t_ctx = _Thread(r2=True)
        self.addCleanup(t_ctx.close)
        t_ctx.turn("el auto esta en la boca", evidence=_evidence(_loc("La Boca")))
        self.assertEqual(("CABA", "La Boca"), t_ctx.zone(),
                         "the predicate became a blanket refusal")


# ══════════════════════════════════════════════════════════════════════════════
class TestR2H_Flags(unittest.TestCase):
    """The R1/R2/R3 matrix. An unsafe partial combination must fail closed."""

    def test_h01_the_flag_defaults_to_off(self):
        from app.settings import Settings, get_settings
        self.assertIs(False, Settings().outstanding_question_enabled)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OUTSTANDING_QUESTION_ENABLED", None)
            get_settings.cache_clear()
            self.assertIs(False, get_settings().outstanding_question_enabled)
        get_settings.cache_clear()

    def test_h02_only_the_literal_true_arms_it(self):
        from app.settings import get_settings
        for value, expected in (("true", True), ("True", True), ("TRUE", True),
                                ("false", False), ("1", False), ("yes", False),
                                ("", False)):
            with self.subTest(value=value):
                with patch.dict(os.environ,
                                {"OUTSTANDING_QUESTION_ENABLED": value}):
                    get_settings.cache_clear()
                    self.assertIs(expected,
                                  get_settings().outstanding_question_enabled)
        get_settings.cache_clear()

    def test_h03_a_magicmock_settings_object_never_arms_it(self):
        engine = ConversationEngine.__new__(ConversationEngine)
        engine.settings = MagicMock()
        self.assertFalse(engine._r2_on())

    #: OC-R2 CORRECTION 2 — the COMPLETE matrix, all eight rows, as the contract.
    #: `interlocked` is whether R1/R2 behaviour may run at all. Only one row arms it.
    MATRIX = (
        # (R1, R2, R3, interlocked)
        (False, False, False, False),   # the starting behaviour
        (True,  False, False, False),
        (False, True,  False, False),
        (False, False, True,  False),   # R3 alone: its own certified safety, R1/R2 inert
        (True,  True,  False, False),   # the posture the owner refused: no R3 underneath
        (True,  False, True,  False),   # the posture that admitted `la boca`
        (False, True,  True,  False),
        (True,  True,  True,  True),    # integrated R1+R2+R3
    )

    def test_h04_the_complete_eight_row_matrix_is_the_contract(self):
        """ONE interlock decides, and it decides for every flag combination there is.

        The owner's correction in one table. Guarding only "R2 without R1" was not enough:
        the R2 closeout measured R1 ON + R2 OFF (with R3 ON) writing `('CABA','La Boca')`
        out of `me mordí la boca`, because R1's own safety rests on R2's predicate. So
        R1 and R2 behaviour requires all three, and every other row is inert."""
        engine = ConversationEngine.__new__(ConversationEngine)
        for r1, r2, r3, interlocked in self.MATRIX:
            with self.subTest(r1=r1, r2=r2, r3=r3):
                engine.settings = MagicMock()
                engine.settings.evidence_admission_enabled = r1
                engine.settings.outstanding_question_enabled = r2
                engine.settings.locality_resolution_safety_enabled = r3
                self.assertIs(interlocked, engine._integrated_features_armed())
                # the two named readers add NO condition of their own
                self.assertIs(interlocked, engine._admission_on())
                self.assertIs(interlocked, engine._r2_on())
                # R3 keeps its independence, exactly as the owner specified
                self.assertIs(r3, engine._locality_safety_on())

    def test_h04b_every_posture_is_safe_on_the_cases_that_matter(self):
        """The same eight rows driven through COMPLETE TURNS, on the inputs the owner
        named: the La Boca idiom, San Nicolás, an unsupported locality, stale evidence and
        candidate isolation. No partial posture may admit, persist, bind, activate, answer
        or suppress — and the integrated row must still do the right thing on each."""
        cases = (
            ("la_boca_idiom", "me mordi la boca", _evidence(_loc("la boca")), None),
            ("ambiguous", "en san nicolas", _evidence(_loc("san nicolas")), None),
            ("unsupported", "en mar del plata", _evidence(_loc("Mar del Plata")), None),
            ("stale", "ya te lo dije",
             _evidence(_loc("Flores"), message_ids=("wamid.OLD",)), None),
            ("admissible", "en flores", _evidence(_loc("Flores")), ("CABA", "Flores")),
        )
        for r1, r2, r3, interlocked in self.MATRIX:
            for name, text, evidence, integrated_zone in cases:
                with self.subTest(r1=r1, r2=r2, r3=r3, case=name):
                    t = _Thread(r1=r1, r2=r2, safety=r3)
                    try:
                        t.turn(text, evidence=evidence)
                        expected = integrated_zone if interlocked else None
                        self.assertEqual(expected or (None, None), t.zone())
                        if not interlocked:
                            self.assertEqual([], t.evidence_rows())
                            self.assertEqual([], t.questions())
                            self.assertIsNone(getattr(t.engine, "_turn_admission", None))
                    finally:
                        t.close()

    def test_h04c_a_mentioned_candidate_is_isolated_in_every_posture(self):
        for r1, r2, r3, interlocked in self.MATRIX:
            with self.subTest(r1=r1, r2=r2, r3=r3):
                t = _Thread(r1=r1, r2=r2, safety=r3)
                try:
                    other = WhatsAppThreadCandidate(
                        thread_id=t.thread_id, marca="Honda", modelo="CB500", anio=2020,
                        tipo_vehiculo="MOTO", status="mentioned")
                    t.db.add(other)
                    t.db.commit()
                    t.turn("en flores", evidence=_evidence(_loc("Flores")))
                    t.turn("es un toyota corolla 2024")
                    t.db.expire_all()
                    moto = [c for c in t.candidates() if c.tipo_vehiculo == "MOTO"][0]
                    self.assertEqual((None, None), (moto.zone_group, moto.zone_detail))
                    self.assertIsNone(moto.zone_evidence_id)
                finally:
                    t.close()

    def test_h05_r2_without_r1_writes_nothing_on_a_complete_turn(self):
        t = _Thread(r1=False, r2=True)
        self.addCleanup(t.close)
        t.turn(_FAQ_TEXT, payload=_ASKS_LOCATION)
        t2 = t.turn("en flores", evidence=_evidence(_loc("Flores")))
        self.assertEqual([], t.questions())
        self.assertEqual([], t.evidence_rows())
        self.assertEqual((None, None), t.zone())
        self.assertTrue(any(_GREETING_MARK in s for s in t2.sent),
                        "the committed behaviour changed with R1 off")

    def test_h06_r1_and_r2_without_r3_are_inert(self):
        """The owner's row: `R1+R2 without R3` must not run. R3 is what makes a resolved
        locality safe to price at all, so admitting one without it is the coverage and
        ambiguity exposure R3 exists to close — W1FRV-02 and W1FRV-03, through a new door.

        R3's own independence is asserted separately (`h04`): with only
        `LOCALITY_RESOLUTION_SAFETY_ENABLED` armed, it still delivers exactly its certified
        behaviour."""
        t = _Thread(safety=False)
        self.addCleanup(t.close)
        t.turn(_FAQ_TEXT, payload=_ASKS_LOCATION)
        t.turn("en flores", evidence=_evidence(_loc("Flores")))
        self.assertEqual((None, None), t.zone())
        self.assertEqual([], t.questions())
        self.assertEqual([], t.evidence_rows())

    def test_h07_the_full_matrix_never_crashes_and_never_sends_twice(self):
        for r1, r2, r3, _interlocked in self.MATRIX:
            with self.subTest(r1=r1, r2=r2, r3=r3):
                t = _Thread(r1=r1, r2=r2, safety=r3)
                try:
                    t.ask_and_deliver()
                    turn = t.turn("en flores", evidence=_evidence(_loc("Flores")))
                    self.assertLessEqual(len(turn.sent), 1,
                                         "more than one text in one turn")
                finally:
                    t.close()


# ══════════════════════════════════════════════════════════════════════════════
class TestR2I_NoAuthority(unittest.TestCase):
    """Answering a locality question authorises nothing commercial. The honest boundary."""

    def test_i01_no_offer_slot_or_booking_is_created(self):
        t = _Thread()
        self.addCleanup(t.close)
        t.turn("¿de qué consta la revisión?", payload=_ASKS_LOCATION)
        turn = t.turn("en flores", evidence=_evidence(_loc("Flores")))
        self.assertEqual(0, turn.revisions, "a booking appeared from answering a question")
        from sqlalchemy import text as sql_text
        offers = t.db.execute(sql_text(
            "SELECT count(*) FROM whatsapp_booking_offers")).scalar()
        self.assertEqual(0, offers)

    def test_i02_the_stage_and_the_lead_flag_are_not_advanced(self):
        t = _Thread()
        self.addCleanup(t.close)
        t.turn("¿de qué consta la revisión?", payload=_ASKS_LOCATION)
        turn = t.turn("en flores", evidence=_evidence(_loc("Flores")))
        self.assertNotEqual("QUOTED", turn.stage)
        self.assertFalse(turn.needs_human)

    def test_i03_no_intent_is_invented_by_the_suppression(self):
        """Step 9 returns "continue" and sets nothing. An answer about WHERE the car is is
        not a statement of commercial intent, and R2 does not make one up."""
        t = _Thread()
        self.addCleanup(t.close)
        t.ask_and_deliver()
        before = t.state().last_intent
        t.turn("en flores", evidence=_evidence(_loc("Flores")))
        self.assertEqual(before, t.state().last_intent,
                         "answering a locality question invented an intent")

    def test_i04_the_motorcycle_and_handoff_gates_are_untouched(self):
        for text in ("tengo una moto, la revisan?", "llamame por favor",
                     "necesito hacer la transferencia del auto"):
            with self.subTest(text=text):
                off = _Thread(r2=False)
                on = _Thread(r2=True)
                try:
                    a = off.turn(text, evidence=_evidence(_loc("Flores")))
                    b = on.turn(text, evidence=_evidence(_loc("Flores")))
                    self.assertEqual(a.sent, b.sent)
                    self.assertEqual(a.needs_human, b.needs_human)
                    self.assertEqual(a.stage, b.stage)
                    self.assertEqual([], on.questions())
                finally:
                    off.close()
                    on.close()

    def test_i05_no_provider_is_contacted_and_no_model_is_called(self):
        t = _Thread()
        self.addCleanup(t.close)
        t.turn("¿de qué consta la revisión?", payload=_ASKS_LOCATION)
        turn = t.turn("en flores", evidence=_evidence(_loc("Flores")))
        self.assertEqual(0, turn.semantic_calls)
        from sqlalchemy import text as sql_text
        rows = t.db.execute(sql_text(
            "SELECT count(*) FROM whatsapp_messages WHERE direction = 'out'")).scalar()
        self.assertEqual(0, rows, "an outbound ledger row was written by the harness")


# ══════════════════════════════════════════════════════════════════════════════
class TestR2J_Structural(unittest.TestCase):
    """Rules a reviewer should not have to take on trust."""

    @staticmethod
    def _function(name: str) -> ast.FunctionDef:
        tree = ast.parse(CE_SOURCE)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"{name} not found")

    def test_j01_the_question_is_recorded_only_after_mark_sent(self):
        """The delivery guard, read off the source: the recorder is called inside the `try`
        that performs the Meta send, AFTER `gate.mark_sent`."""
        body = CE_SOURCE[CE_SOURCE.index("def _send_text_to_wa"):]
        body = body[:body.index("def _send_flow_button")]
        send = body.index("_send_whatsapp_cloud_text(to_wa_id=wa_id, text=text)")
        mark = body.index("gate.mark_sent(")
        record = body.index("self._record_delivered_questions(")
        self.assertLess(send, mark)
        self.assertLess(mark, record,
                        "the question is recorded before delivery is confirmed")

    def test_j02_the_recorder_requires_delivery_evidence(self):
        node = self._function("_record_delivered_questions")
        source = ast.get_source_segment(CE_SOURCE, node) or ""
        self.assertIn("not wa_message_id", source)
        service = (BACKEND_DIR / "app" / "services" / "outstanding_question.py").read_text(
            encoding="utf-8")
        self.assertIn("if not wa_message_id:", service)

    def test_j03_closure_is_a_conditional_update(self):
        service = (BACKEND_DIR / "app" / "services" / "outstanding_question.py").read_text(
            encoding="utf-8")
        for required in ("update(WhatsAppOutstandingQuestion)",
                         "WhatsAppOutstandingQuestion.status == OQ_OUTSTANDING",
                         "result.rowcount"):
            with self.subTest(required=required):
                self.assertIn(required, service)

    def test_j04_the_services_write_no_candidate_field_themselves(self):
        """The C2 chokepoints stay the only writers. Scoped honestly: these two modules may
        not assign a zone field at all, and the engine methods that consume them write only
        through `_apply_inspection_zone`."""
        for name in ("outstanding_question.py", "thread_evidence.py"):
            source = (BACKEND_DIR / "app" / "services" / name).read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Attribute):
                            with self.subTest(module=name, attr=target.attr):
                                self.assertNotIn(target.attr,
                                                 {"zone_group", "zone_detail",
                                                  "home_zone_group", "home_zone_detail"})

    def test_j05_the_services_send_nothing(self):
        """Asserted over the AST's CALLS and IMPORTS, not over the file's text.

        It used to be a substring scan, and that broke on a docstring: OC-R2 CORRECTION 1
        documents the send ordering in `reconcile_delivery`, and the words `gate.attempt()`
        in that explanation tripped a search for `attempt(`. A rule that a comment can
        violate is not the rule anyone meant — what matters is that these modules never
        CALL transport, so that is what is checked."""
        forbidden_calls = {"_send_whatsapp_cloud_text", "_send_whatsapp_cloud_flow",
                           "attempt", "mark_sent", "urlopen", "post", "request"}
        forbidden_imports = {"urllib", "requests", "httpx", "outbound_safety_gate"}
        for name in ("outstanding_question.py", "thread_evidence.py"):
            source = (BACKEND_DIR / "app" / "services" / name).read_text(encoding="utf-8")
            tree = ast.parse(source)
            called, imported = set(), set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    called.add(getattr(fn, "attr", None) or getattr(fn, "id", None))
                elif isinstance(node, ast.ImportFrom):
                    imported.add(node.module or "")
                elif isinstance(node, ast.Import):
                    imported.update(a.name for a in node.names)
            with self.subTest(module=name, kind="calls"):
                self.assertEqual(set(), called & forbidden_calls)
            for bad in forbidden_imports:
                with self.subTest(module=name, imports=bad):
                    self.assertFalse(any(bad in m for m in imported))

    def test_j06_the_form_no_longer_assigns_home_zone_directly_when_r2_is_on(self):
        """W1FRV-04, as a source rule. The two direct assignments survive only as the
        flag-OFF branch, and the R2 branch goes through the helper."""
        node = self._function("_process_location_fallback_response")
        source = ast.get_source_segment(CE_SOURCE, node) or ""
        self.assertIn("self._apply_form_location(", source)
        self.assertIn("if self._r2_on():", source)
        helper = self._function("_apply_form_location")
        helper_source = ast.get_source_segment(CE_SOURCE, helper) or ""
        self.assertIn("_apply_inspection_zone(", helper_source)
        self.assertIn("HUMAN_CONFIRMED", helper_source)
        for child in ast.walk(helper):
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Attribute):
                        self.assertNotIn(target.attr,
                                         {"home_zone_group", "home_zone_detail",
                                          "zone_group", "zone_detail"})

    def test_j07_the_cycle_key_has_one_definition(self):
        """Three subsystems key on the cycle and must agree. W1FRV-07 is the finding about
        one rule in two copies; R2 imports the certified one."""
        self.assertEqual(1, CE_SOURCE.count("def _r2_cycle_key"))
        self.assertIn("from .clarification_attempt import cycle_key",
                      CE_SOURCE)
        for name in ("outstanding_question.py", "thread_evidence.py"):
            source = (BACKEND_DIR / "app" / "services" / name).read_text(encoding="utf-8")
            self.assertNotIn("current_cycle_start_message_db_id", source,
                             f"{name} re-derives the cycle key")

    def test_j08_both_candidate_creation_sites_bind(self):
        """Measured, not assumed: the Wild's turn 3 creates its candidate in
        `_create_candidate_from_catalog`, not in `_apply_candidate`, so a binding in only
        one place would never have run on the sequence this milestone closes."""
        self.assertEqual(1, CE_SOURCE.count("def _bind_evidence_to_candidate"))
        self.assertEqual(2, CE_SOURCE.count("self._bind_evidence_to_candidate("))

    def test_j09_the_routing_fact_is_read_by_exactly_one_gate(self):
        """"It may suppress a contradictory generic fallback or greeting ONLY for the
        matching answered question." One reader, and it is Layer F step 9."""
        self.assertEqual(1, CE_SOURCE.count("def _answered_question_kinds"))
        tree = ast.parse(CE_SOURCE)
        readers = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name == "_answered_question_kinds":
                continue
            for child in ast.walk(node):
                if (isinstance(child, ast.Call)
                        and getattr(child.func, "attr", None)
                        == "_answered_question_kinds"):
                    readers.add(node.name)
        self.assertEqual({"_handle_qualifying_intent"}, readers,
                         f"the routing fact is read outside Layer F: {sorted(readers)}")
        guard = CE_SOURCE.index("if self._answered_question_kinds():")
        step9 = CE_SOURCE.index("return self._send_service_boundary(ctx, "
                                "_UNCERTAIN_SERVICE_REPLY)")
        self.assertLess(guard, step9)

    def test_j10_the_outcome_vocabulary_is_unchanged(self):
        """Review §15.4: the nine `DecisionSite` identifiers keep their meanings and no site
        may change the outcome vocabulary — the Inspector's reading must not shift."""
        source = (BACKEND_DIR / "app" / "services" / "thread_evidence.py").read_text(
            encoding="utf-8")
        self.assertNotIn("class Outcome", source)
        self.assertIn("ReconciliationOutcome", source)

    def test_j11_the_tester_reset_deletes_both_new_tables(self):
        """G37R-RV-03's lesson applied in advance: the certified reset deletes threads last,
        so a table referencing the thread must be deleted before it or the whole reset
        fails `23503` and deletes nothing."""
        source = (BACKEND_DIR / "app" / "services" / "tester_reset.py").read_text(
            encoding="utf-8")
        questions = source.index("_delete(WhatsAppOutstandingQuestion,")
        evidence = source.index("_delete(WhatsAppThreadEvidence,")
        messages = source.index("_delete(WhatsAppMessage, WhatsAppMessage.thread_id")
        threads = source.index("_delete(WhatsAppThread, WhatsAppThread.id")
        self.assertLess(questions, messages)
        self.assertLess(evidence, messages)
        self.assertLess(messages, threads)

    def test_j12_the_migration_is_the_single_head_and_additive(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from app.services.schema_preflight import EXPECTED_HEAD
        cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
        self.assertEqual([EXPECTED_HEAD],
                         ScriptDirectory.from_config(cfg).get_heads())
        self.assertEqual("20261027_w1f_conversation_memory", EXPECTED_HEAD)
        source = (BACKEND_DIR / "migrations" / "versions"
                  / "20261027_w1f_conversation_memory.py").read_text(encoding="utf-8")
        for forbidden in ("DROP TABLE whatsapp_thread_states", "DELETE FROM", "TRUNCATE",
                          "UPDATE whatsapp_"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertIn("downgrade REFUSED", source)

    def test_j13_the_two_tables_are_declared_once_each(self):
        from app.models import Base
        for table in ("whatsapp_outstanding_questions", "whatsapp_thread_evidence"):
            with self.subTest(table=table):
                self.assertIn(table, Base.metadata.tables)
        questions = Base.metadata.tables["whatsapp_outstanding_questions"]
        partial = [i for i in questions.indexes
                   if i.name == "uq_outstanding_question_one_live_per_field"]
        self.assertEqual(1, len(partial))
        self.assertTrue(partial[0].unique,
                        "one live question per field is not enforced by the database")



# ══════════════════════════════════════════════════════════════════════════════
class TestR2L_Boundaries(unittest.TestCase):
    """Transaction rollback, crash boundaries and concurrent answers."""

    def test_l01_a_crash_inside_the_turn_leaves_nothing_half_written(self):
        """Both R2 writes live in the TURN's transaction, so a turn that dies takes them
        with it — there is no second session, no autonomous commit and no background write.

        Measured rather than assumed: `handle()` catches the failure and returns
        `action='error'`, and the thread is left exactly as it was — the question still
        OUTSTANDING, no evidence row, no zone. That is the safe boundary: the customer's
        answer has not been consumed, so a retry can still consume it."""
        t = _Thread()
        self.addCleanup(t.close)
        t.ask_and_deliver()
        self.assertEqual([OQ_OUTSTANDING], [q.status for q in t.questions()])
        before_evidence = len(t.evidence_rows())

        for target in ("_handle_qualifying_intent", "_apply_zone_from_text"):
            with self.subTest(crash_in=target):
                with patch.object(ConversationEngine, target,
                                  side_effect=RuntimeError("crash mid-turn")):
                    turn = t.turn("en flores", evidence=_evidence(_loc("Flores")))
                self.assertEqual("error", turn.out.action)
                t.db.rollback()
                self.assertEqual([OQ_OUTSTANDING], [q.status for q in t.questions()],
                                 "a crashed turn closed the question anyway")
                self.assertEqual(before_evidence, len(t.evidence_rows()),
                                 "a crashed turn left an orphan evidence row")
                self.assertEqual((None, None), t.zone())

    def test_l02_the_crashed_turn_can_be_retried_and_then_closes_once(self):
        """The boundary that matters operationally: after the crash the question is still
        OUTSTANDING, so the retry closes it — exactly once, and the locality lands."""
        t = _Thread()
        self.addCleanup(t.close)
        t.ask_and_deliver()
        with patch.object(ConversationEngine, "_handle_qualifying_intent",
                          side_effect=RuntimeError("crash")):
            turn = t.turn("en flores", evidence=_evidence(_loc("Flores")))
        self.assertEqual("error", turn.out.action)
        t.db.rollback()
        t.turn("en flores", evidence=_evidence(_loc("Flores")))
        rows = t.questions()
        self.assertEqual(1, len(rows))
        self.assertEqual(OQ_ANSWERED, rows[0].status)
        self.assertEqual(("CABA", "Flores"), t.zone())
        self.assertEqual(1, len([e for e in t.evidence_rows()
                                 if e.outcome == ReconciliationOutcome.ACCEPT.value]))

    def test_l02b_a_crash_after_the_turn_committed_keeps_the_committed_work(self):
        """The other side of the boundary, and it is equally deliberate. The hybrid trace is
        written AFTER the turn's decisions are committed (it is observability, not
        authority), so a trace defect must not be able to undo an answer the customer
        already received a reply to. Measured: the question is ANSWERED, the evidence row is
        there and the zone is set."""
        t = _Thread()
        self.addCleanup(t.close)
        t.ask_and_deliver()
        with patch.object(ConversationEngine, "_write_hybrid_trace",
                          side_effect=RuntimeError("trace defect")):
            with self.assertRaises(RuntimeError):
                t.turn("en flores", evidence=_evidence(_loc("Flores")))
        t.db.rollback()
        self.assertEqual([OQ_ANSWERED], [q.status for q in t.questions()])
        self.assertEqual(("CABA", "Flores"), t.zone())
        self.assertEqual(1, len([e for e in t.evidence_rows()
                                 if e.outcome == ReconciliationOutcome.ACCEPT.value]))

    def test_l03_concurrent_closures_produce_one_winner(self):
        """Two workers answering the same question. The conditional UPDATE decides, and the
        loser does not overwrite the winner's provenance — the pattern `offer_lifecycle`
        uses for acceptance, and the G3-7P-R3A review measured what removing it costs."""
        t = _Thread()
        self.addCleanup(t.close)
        row, _ = _record_live(t.db, t.thread_id)
        t.db.commit()
        other = H._SessionLocal()
        try:
            mirror = other.query(WhatsAppOutstandingQuestion).filter_by(id=row.id).one()
            self.assertEqual(OQ_OUTSTANDING, mirror.status)
            self.assertTrue(OQ.close_answered(t.db, row, claim_id="worker-A"))
            t.db.commit()
            self.assertFalse(OQ.close_answered(other, mirror, claim_id="worker-B"),
                             "both workers closed the same question")
            other.commit()
        finally:
            other.close()
        t.db.expire_all()
        reread = t.db.query(WhatsAppOutstandingQuestion).filter_by(id=row.id).one()
        self.assertEqual(OQ_ANSWERED, reread.status)
        self.assertEqual("worker-A", reread.answered_by_claim_id)

    def test_l03b_concurrent_activation_callbacks_produce_one_activation(self):
        """Two status callbacks for the same message, in flight at once.

        MUTATION M25 is why this test exists. It removes the
        `status == PENDING_DELIVERY` condition from the activation UPDATE and **survived**
        the first run — because the SELECT just above it already filters on that status, so
        in a single-threaded test the condition is redundant. It is not redundant across
        sessions: Meta can deliver `delivered` and `read` for one message close together,
        the webhook handles each in its own transaction, and without the condition the
        second one re-stamps a row that has already moved on — overwriting the activating
        provenance, and resurrecting a `failed` question if the callbacks arrive in that
        order. A guard that only a race can exercise needs a race to test it."""
        t = _Thread()
        self.addCleanup(t.close)
        row, _ = _record(t.db, t.thread_id, wamid="wamid.RACE")
        t.db.commit()
        other = H._SessionLocal()
        try:
            # both sessions see the row as PENDING_DELIVERY
            mirror = other.query(WhatsAppOutstandingQuestion).filter_by(id=row.id).one()
            self.assertEqual(OQ_PENDING_DELIVERY, mirror.status)

            self.assertEqual(1, OQ.activate_on_status(
                t.db, wa_message_id="wamid.RACE", status="delivered"))
            t.db.commit()

            # the second callback, from a session that read the row BEFORE the first won
            self.assertEqual(0, OQ.activate_on_status(
                other, wa_message_id="wamid.RACE", status="read"),
                "a concurrent callback activated the question twice")
            other.commit()
        finally:
            other.close()
        t.db.expire_all()
        fresh = t.db.query(WhatsAppOutstandingQuestion).filter_by(id=row.id).one()
        self.assertEqual(OQ_OUTSTANDING, fresh.status)
        self.assertEqual("delivered", fresh.delivery_status,
                         "the losing callback overwrote the activating provenance")

    def test_l03c_a_failed_then_delivered_race_keeps_the_terminal_state(self):
        """The same guard, in the order that matters most: the failure commits first and a
        stale `delivered` arrives from another session afterwards."""
        t = _Thread()
        self.addCleanup(t.close)
        row, _ = _record(t.db, t.thread_id, wamid="wamid.RACEFAIL")
        t.db.commit()
        other = H._SessionLocal()
        try:
            other.query(WhatsAppOutstandingQuestion).filter_by(id=row.id).one()
            self.assertEqual(1, OQ.activate_on_status(
                t.db, wa_message_id="wamid.RACEFAIL", status="failed"))
            t.db.commit()
            self.assertEqual(0, OQ.activate_on_status(
                other, wa_message_id="wamid.RACEFAIL", status="delivered"),
                "a stale delivered resurrected a failed question across sessions")
            other.commit()
        finally:
            other.close()
        t.db.expire_all()
        fresh = t.db.query(WhatsAppOutstandingQuestion).filter_by(id=row.id).one()
        self.assertEqual(OQ_UNDELIVERED, fresh.status)
        self.assertEqual([], OQ.live(t.db, thread_id=t.thread_id, cycle_key=t.cycle()))

    def test_l04_a_concurrent_second_ask_cannot_leave_two_live_rows(self):
        """On SQLite the partial unique index is declared with `sqlite_where`, so the same
        guarantee is checked here; `test_k03` proves it against PostgreSQL, which is the
        server that matters."""
        import sqlalchemy as sa
        t = _Thread()
        self.addCleanup(t.close)
        first, _ = _record_live(t.db, t.thread_id, wamid="wamid.A")
        t.db.commit()
        duplicate = WhatsAppOutstandingQuestion(
            thread_id=t.thread_id, cycle_key=0,
            question_kind=OQ_KIND_INSPECTION_LOCATION, asked_by=OQ_ASKED_BY_REPLY_MODEL,
            identity="hand-written-duplicate", status=OQ_OUTSTANDING,
            asked_at=datetime.now(timezone.utc), expires_after_turns=3,
            turns_observed=0)
        t.db.add(duplicate)
        with self.assertRaises(sa.exc.IntegrityError,
                               msg="two live questions owned the same field"):
            t.db.flush()
        t.db.rollback()


# ══════════════════════════════════════════════════════════════════════════════
#: A DISPOSABLE PostgreSQL for the migration rehearsal, by the convention
#: `test_convergence_p4_strict_schema_pg.py` established (`CONV_SSA_PARENT_URL`): the class
#: SKIPS when it is unset, because a migration test that silently passes without a server
#: proves nothing. The rehearsal that produced the measurements in the closeout ran against
#: `postgresql+psycopg://…@r2-rehearsal-pg/r2_rehearsal`, PostgreSQL 16.13, on its own
#: docker network, disposed by exact name afterwards.
_R2_PG_URL = os.environ.get("R2_PG_URL", "")


@unittest.skipUnless(_R2_PG_URL, "R2_PG_URL not set — no disposable PostgreSQL; "
                                 "the migration rehearsal proves nothing without one")
class TestR2K_Migration(unittest.TestCase):
    """The one additive migration, on the DEPLOYED schema-adoption path.

    Not a clean Alembic chain: this project's schema is created by
    `Base.metadata.create_all` at start-up and `alembic upgrade head` on an empty database
    does not work (the Phase 4 review's caution, repeated by this review's §15). So the
    rehearsal builds the pre-R2 schema the way the deployed database has it, stamps the
    parent revision, and only then runs this one.
    """

    PARENT = "20261022_operator_notifications"
    HEAD = "20261027_w1f_conversation_memory"
    TABLES = ("whatsapp_thread_evidence", "whatsapp_outstanding_questions")

    @classmethod
    def _engine(cls):
        import sqlalchemy as sa
        return sa.create_engine(_R2_PG_URL)

    def _sql(self, statement):
        import sqlalchemy as sa
        with self._engine().begin() as conn:
            result = conn.execute(sa.text(statement))
            return result.fetchall() if result.returns_rows else []

    def _tables_present(self):
        return self._sql(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ANY("
            f"ARRAY{list(self.TABLES)!r})")[0][0]

    def test_k01_the_revision_is_the_single_head_and_chains_to_the_parent(self):
        source = (BACKEND_DIR / "migrations" / "versions"
                  / f"{self.HEAD}.py").read_text(encoding="utf-8")
        self.assertIn(f'down_revision: str = "{self.PARENT}"', source)

    def test_k02_the_downgrade_refuses_rather_than_deletes(self):
        """The contract, read off the source and proven by the rehearsal: with any row in
        either table the downgrade raises and changes nothing. A CODE rollback does not
        need it — both tables are unread with the flags off."""
        source = (BACKEND_DIR / "migrations" / "versions"
                  / f"{self.HEAD}.py").read_text(encoding="utf-8")
        self.assertIn("downgrade REFUSED", source)
        self.assertNotIn("DELETE FROM whatsapp_outstanding_questions", source)
        self.assertNotIn("DELETE FROM whatsapp_thread_evidence", source)

    def test_k03_one_live_question_per_field_is_enforced_by_postgresql(self):
        """The partial unique index, against a real server. The service's supersede step is
        coordination; THIS is the guarantee, and two concurrent asks cannot both leave a
        live row behind."""
        import sqlalchemy as sa
        if self._tables_present() != 2:
            self.skipTest("the rehearsal database is not at this revision")
        self._sql("INSERT INTO whatsapp_contacts (wa_id) VALUES ('549000000998') "
                  "ON CONFLICT DO NOTHING")
        self._sql("INSERT INTO whatsapp_threads (contact_id) SELECT id FROM "
                  "whatsapp_contacts WHERE wa_id='549000000998'")
        thread_id = self._sql(
            "SELECT id FROM whatsapp_threads ORDER BY id DESC LIMIT 1")[0][0]
        row = ("INSERT INTO whatsapp_outstanding_questions (thread_id, cycle_key, "
               "question_kind, asked_by, identity, status, asked_at) VALUES "
               f"({thread_id}, 0, 'INSPECTION_LOCATION', 'REPLY_MODEL', '%s', '%s', now())")
        try:
            self._sql(row % (f"k03-a-{thread_id}", "OUTSTANDING"))
            with self.assertRaises(sa.exc.IntegrityError,
                                   msg="a second live question was accepted"):
                self._sql(row % (f"k03-b-{thread_id}", "OUTSTANDING"))
            # a second row for the same field that is NOT live is fine: the history must
            # stay, and only one of them may own the field
            self._sql(row % (f"k03-c-{thread_id}", "ANSWERED"))
        finally:
            self._sql("DELETE FROM whatsapp_outstanding_questions WHERE thread_id = "
                      f"{thread_id}")
            self._sql(f"DELETE FROM whatsapp_threads WHERE id = {thread_id}")
            self._sql("DELETE FROM whatsapp_contacts WHERE wa_id = '549000000998'")

    def test_k03b_the_activation_guard_holds_under_a_real_interleaving(self):
        """MUTATION M25, proved where it can be proved: on PostgreSQL, with two
        connections, interleaved so the guard is the only thing standing.

        M25 removes the `status = 'PENDING_DELIVERY'` condition from the activation UPDATE
        and survived every SQLite test, including the two-session ones — because the SELECT
        immediately above carries the same predicate, so a second caller's fresh SELECT
        already returns nothing and the UPDATE is never reached. In-process, the two
        statements cannot be interleaved.

        Two real connections can. Connection A reads the pending row and then pauses before
        its UPDATE; connection B activates and commits; A's UPDATE then runs against a row
        that is no longer pending. Only the condition stops it from re-stamping the row and
        overwriting the activating provenance — and, with `failed` as B's status, from
        resurrecting a terminal question.

        This is the honest home for this assertion: the sealed SQLite harness cannot express
        it, so claiming the mutant dead there would have been a false claim."""
        import sqlalchemy as sa
        if self._tables_present() != 2:
            self.skipTest("the rehearsal database is not at this revision")
        engine = self._engine()
        self._sql("INSERT INTO whatsapp_contacts (wa_id) VALUES ('549000000997') "
                  "ON CONFLICT DO NOTHING")
        self._sql("INSERT INTO whatsapp_threads (contact_id) SELECT id FROM "
                  "whatsapp_contacts WHERE wa_id='549000000997'")
        thread_id = self._sql(
            "SELECT id FROM whatsapp_threads ORDER BY id DESC LIMIT 1")[0][0]
        wamid = f"wamid.K03B{thread_id}"
        try:
            self._sql(
                "INSERT INTO whatsapp_outstanding_questions (thread_id, cycle_key, "
                "question_kind, asked_by, identity, wa_message_id, status, asked_at) "
                f"VALUES ({thread_id}, 0, 'INSPECTION_LOCATION', 'REPLY_MODEL', "
                f"'k03b-{thread_id}', '{wamid}', 'PENDING_DELIVERY', now())")

            update = sa.text(
                "UPDATE whatsapp_outstanding_questions SET status = 'OUTSTANDING', "
                "delivery_status = :s, activated_at = now() "
                "WHERE wa_message_id = :w AND status = 'PENDING_DELIVERY'")
            select = sa.text(
                "SELECT id, status FROM whatsapp_outstanding_questions "
                "WHERE wa_message_id = :w AND status = 'PENDING_DELIVERY'")

            with engine.connect() as conn_a, engine.connect() as conn_b:
                # A reads the pending row — the state the service's SELECT sees
                seen = conn_a.execute(select, {"w": wamid}).fetchall()
                self.assertEqual(1, len(seen), "A did not see a pending row")

                # B wins the race and commits
                b_rows = conn_b.execute(update, {"s": "read", "w": wamid}).rowcount
                conn_b.commit()
                self.assertEqual(1, b_rows)

                # A now runs its UPDATE against a row that has moved on
                a_rows = conn_a.execute(update, {"s": "delivered", "w": wamid}).rowcount
                conn_a.commit()
                self.assertEqual(0, a_rows,
                                 "the losing connection re-activated the question — the "
                                 "guard is not holding")

            final = self._sql("SELECT status, delivery_status FROM "
                              "whatsapp_outstanding_questions WHERE wa_message_id = "
                              f"'{wamid}'")[0]
            self.assertEqual(("OUTSTANDING", "read"), tuple(final),
                             "the losing connection overwrote the activating provenance")
        finally:
            self._sql("DELETE FROM whatsapp_outstanding_questions WHERE thread_id = "
                      f"{thread_id}")
            self._sql(f"DELETE FROM whatsapp_threads WHERE id = {thread_id}")
            self._sql("DELETE FROM whatsapp_contacts WHERE wa_id = '549000000997'")

    def test_k03c_a_terminal_question_is_not_resurrected_under_interleaving(self):
        """The same interleaving with `failed` as the winner: the stale `delivered` must
        not bring a terminal question back."""
        import sqlalchemy as sa
        if self._tables_present() != 2:
            self.skipTest("the rehearsal database is not at this revision")
        engine = self._engine()
        self._sql("INSERT INTO whatsapp_contacts (wa_id) VALUES ('549000000996') "
                  "ON CONFLICT DO NOTHING")
        self._sql("INSERT INTO whatsapp_threads (contact_id) SELECT id FROM "
                  "whatsapp_contacts WHERE wa_id='549000000996'")
        thread_id = self._sql(
            "SELECT id FROM whatsapp_threads ORDER BY id DESC LIMIT 1")[0][0]
        wamid = f"wamid.K03C{thread_id}"
        try:
            self._sql(
                "INSERT INTO whatsapp_outstanding_questions (thread_id, cycle_key, "
                "question_kind, asked_by, identity, wa_message_id, status, asked_at) "
                f"VALUES ({thread_id}, 0, 'INSPECTION_LOCATION', 'REPLY_MODEL', "
                f"'k03c-{thread_id}', '{wamid}', 'PENDING_DELIVERY', now())")
            with engine.connect() as conn_a, engine.connect() as conn_b:
                conn_a.execute(sa.text(
                    "SELECT id FROM whatsapp_outstanding_questions "
                    "WHERE wa_message_id = :w AND status = 'PENDING_DELIVERY'"),
                    {"w": wamid}).fetchall()
                conn_b.execute(sa.text(
                    "UPDATE whatsapp_outstanding_questions SET status = 'UNDELIVERED', "
                    "delivery_status = 'failed', closed_reason = 'DELIVERY_FAILED', "
                    "closed_at = now() WHERE wa_message_id = :w "
                    "AND status = 'PENDING_DELIVERY'"), {"w": wamid})
                conn_b.commit()
                late = conn_a.execute(sa.text(
                    "UPDATE whatsapp_outstanding_questions SET status = 'OUTSTANDING', "
                    "delivery_status = 'delivered', activated_at = now() "
                    "WHERE wa_message_id = :w AND status = 'PENDING_DELIVERY'"),
                    {"w": wamid}).rowcount
                conn_a.commit()
                self.assertEqual(0, late, "a stale delivered resurrected a failed question")
            self.assertEqual(("UNDELIVERED",), tuple(self._sql(
                "SELECT status FROM whatsapp_outstanding_questions WHERE "
                f"wa_message_id = '{wamid}'")[0]))
        finally:
            self._sql("DELETE FROM whatsapp_outstanding_questions WHERE thread_id = "
                      f"{thread_id}")
            self._sql(f"DELETE FROM whatsapp_threads WHERE id = {thread_id}")
            self._sql("DELETE FROM whatsapp_contacts WHERE wa_id = '549000000996'")

    # ── OC-R2-R1 case F: the ordering guard, on two real connections ─────────
    #
    # Three tests rather than one with three parts, so each is reported independently:
    # (a) and (b) are outcomes the partial unique index would also defend, and only (c)
    # isolates the ordering guard from the database. A single test that stopped at (a)
    # would not say whether (c) holds, and (c) is the one that matters.

    def _pg_thread(self, wa_id):
        self._sql(f"INSERT INTO whatsapp_contacts (wa_id) VALUES ('{wa_id}') "
                  "ON CONFLICT DO NOTHING")
        self._sql("INSERT INTO whatsapp_threads (contact_id) SELECT id FROM "
                  f"whatsapp_contacts WHERE wa_id='{wa_id}'")
        self.addCleanup(self._pg_drop, wa_id)
        return self._sql("SELECT id FROM whatsapp_threads ORDER BY id DESC LIMIT 1")[0][0]

    def _pg_drop(self, wa_id):
        self._sql("DELETE FROM whatsapp_outstanding_questions WHERE thread_id IN "
                  "(SELECT t.id FROM whatsapp_threads t JOIN whatsapp_contacts c ON "
                  f"c.id = t.contact_id WHERE c.wa_id = '{wa_id}')")
        self._sql("DELETE FROM whatsapp_threads WHERE contact_id IN (SELECT id FROM "
                  f"whatsapp_contacts WHERE wa_id = '{wa_id}')")
        self._sql(f"DELETE FROM whatsapp_contacts WHERE wa_id = '{wa_id}'")

    def _pg_question(self, thread_id, tag, wamid, status="PENDING_DELIVERY"):
        self._sql(
            "INSERT INTO whatsapp_outstanding_questions (thread_id, cycle_key, "
            "question_kind, asked_by, identity, wa_message_id, status, asked_at, "
            "expires_after_turns, turns_observed) VALUES "
            f"({thread_id}, 0, 'INSPECTION_LOCATION', 'REPLY_MODEL', '{tag}', "
            f"'{wamid}', '{status}', now(), 3, 0)")
        return self._sql("SELECT id FROM whatsapp_outstanding_questions WHERE identity = "
                         f"'{tag}'")[0][0]

    def _pg_rows(self, thread_id):
        return {r[0]: (r[1], r[2], r[3], r[4]) for r in self._sql(
            "SELECT id, status, closed_reason, delivery_status, superseded_by_id FROM "
            f"whatsapp_outstanding_questions WHERE thread_id = {thread_id} ORDER BY id")}

    def _pg_ready(self):
        if self._tables_present() != 2:
            self.skipTest("the rehearsal database is not at this revision")
        import sqlalchemy as sa
        return sa.create_engine(_R2_PG_URL)

    def test_k06a_a_callback_that_could_not_see_the_newer_ask_still_loses_the_field(self):
        """OC-R2-R1 case F, the interleaving that genuinely produces a live older row.

        Connection A processes Q1's `delivered` while Q2's acceptance is still uncommitted,
        so A's ordering check cannot see Q2 and Q1 legitimately activates — there is nothing
        wrong with that, and it is the state the sealed harness cannot reach. What must hold
        is the ending: once Q2's own callback is processed, the NEWEST accepted ask owns the
        field and Q1 is terminal, whichever connection got there first."""
        from sqlalchemy.orm import Session
        import sqlalchemy as sa
        engine = self._pg_ready()
        thread_id = self._pg_thread("549000000995")
        q1 = self._pg_question(thread_id, f"k06a1-{thread_id}", f"wamid.K6A1{thread_id}")
        with engine.connect() as conn_c:
            conn_c.execute(sa.text(
                "INSERT INTO whatsapp_outstanding_questions (thread_id, cycle_key, "
                "question_kind, asked_by, identity, wa_message_id, status, asked_at, "
                "expires_after_turns, turns_observed) VALUES "
                f"(:t, 0, 'INSPECTION_LOCATION', 'REPLY_MODEL', 'k06a2-{thread_id}', "
                f"'wamid.K6A2{thread_id}', 'PENDING_DELIVERY', now(), 3, 0)"),
                {"t": thread_id})
            with Session(engine) as sess_a:
                changed = OQ.activate_on_status(
                    sess_a, wa_message_id=f"wamid.K6A1{thread_id}", status="delivered")
                sess_a.commit()
            self.assertEqual(1, changed, "the only visible question did not activate")
            conn_c.commit()
        q2 = self._sql("SELECT id FROM whatsapp_outstanding_questions WHERE identity = "
                       f"'k06a2-{thread_id}'")[0][0]
        self.assertGreater(q2, q1, "the fixture is not ordered as it claims")
        with Session(engine) as sess_b:
            self.assertEqual(1, OQ.activate_on_status(
                sess_b, wa_message_id=f"wamid.K6A2{thread_id}", status="read"))
            sess_b.commit()
        rows = self._pg_rows(thread_id)
        self.assertEqual("OUTSTANDING", rows[q2][0],
                         "the newest accepted ask did not end up owning the field")
        self.assertEqual(("SUPERSEDED", "SUPERSEDED_BY_REASK", "delivered", q2), rows[q1])
        self.assertEqual(1, sum(1 for r in rows.values() if r[0] == "OUTSTANDING"))

    def test_k06b_the_older_callback_arriving_first_does_not_take_the_field(self):
        """Case F in the other order: Q1's `delivered` is processed on one connection
        before Q2's `read` on another, with both asks already committed. The guard refuses
        the older one, records the delivery that did happen, and the winner is the same."""
        from sqlalchemy.orm import Session
        engine = self._pg_ready()
        thread_id = self._pg_thread("549000000994")
        q1 = self._pg_question(thread_id, f"k06b1-{thread_id}", f"wamid.K6B1{thread_id}")
        q2 = self._pg_question(thread_id, f"k06b2-{thread_id}", f"wamid.K6B2{thread_id}")
        with Session(engine) as sess_a:
            self.assertEqual(1, OQ.activate_on_status(
                sess_a, wa_message_id=f"wamid.K6B1{thread_id}", status="delivered"),
                "the stale callback was not terminalized")
            sess_a.commit()
        with Session(engine) as sess_b:
            self.assertEqual(1, OQ.activate_on_status(
                sess_b, wa_message_id=f"wamid.K6B2{thread_id}", status="read"))
            sess_b.commit()
        rows = self._pg_rows(thread_id)
        self.assertEqual("OUTSTANDING", rows[q2][0])
        self.assertEqual(("SUPERSEDED", "SUPERSEDED_BEFORE_DELIVERY", "delivered", q2),
                         rows[q1],
                         "the older question activated, or lost its own provenance")

    def test_k06c_the_guard_alone_holds_where_the_unique_index_cannot_see(self):
        """Case F where the database is no help — and the kill the ordering mutant needs.

        The newer question is already `ANSWERED`, so no second live row ever arises and the
        partial unique index is satisfied either way. The ONLY thing standing between a
        late `read` and a stale question becoming live again is the ordering guard. That is
        a production state, not a contrivance: the customer answered the re-ask, and then
        Meta reported the earlier message delivered.

        Mutation M35 (the guard never refuses) is killed here on PostgreSQL, independently
        of its SQLite kills — which is what the owner asked to see proved."""
        from sqlalchemy.orm import Session
        engine = self._pg_ready()
        thread_id = self._pg_thread("549000000993")
        q1 = self._pg_question(thread_id, f"k06c1-{thread_id}", f"wamid.K6C1{thread_id}")
        q2 = self._pg_question(thread_id, f"k06c2-{thread_id}", f"wamid.K6C2{thread_id}",
                               status="ANSWERED")
        with Session(engine) as sess:
            OQ.activate_on_status(sess, wa_message_id=f"wamid.K6C1{thread_id}",
                                  status="read")
            sess.commit()
        rows = self._pg_rows(thread_id)
        self.assertNotEqual("OUTSTANDING", rows[q1][0],
                            "a question replaced by a newer, already answered ask became "
                            "live again — the ordering guard is absent and the partial "
                            "unique index cannot see this case")
        self.assertEqual(("SUPERSEDED", "SUPERSEDED_BEFORE_DELIVERY", "read", q2),
                         rows[q1])
        self.assertEqual("ANSWERED", rows[q2][0], "the answered question was touched")
        self.assertEqual(0, sum(1 for r in rows.values() if r[0] == "OUTSTANDING"))

    def test_k04_the_two_candidate_columns_are_nullable_integers(self):
        if self._tables_present() != 2:
            self.skipTest("the rehearsal database is not at this revision")
        rows = self._sql(
            "SELECT column_name, data_type, is_nullable, column_default FROM "
            "information_schema.columns WHERE table_name='whatsapp_thread_candidates' "
            "AND column_name IN ('zone_evidence_id','vehicle_evidence_id') "
            "ORDER BY column_name")
        self.assertEqual(2, len(rows))
        for name, kind, nullable, default in rows:
            with self.subTest(column=name):
                self.assertEqual("integer", kind)
                self.assertEqual("YES", nullable)
                self.assertIsNone(default)

    def test_k05_every_reference_that_could_block_the_tester_reset_is_set_null(self):
        """G37R-RV-03, enforced against the real catalogue rather than against the code.
        The certified reset deletes candidates and messages BEFORE the thread, so only the
        thread references may be NO ACTION; everything pointing at a candidate or an
        evidence row must be SET NULL, and the gate's ledger row must not be a key at all.
        """
        if self._tables_present() != 2:
            self.skipTest("the rehearsal database is not at this revision")
        rows = self._sql("""
            SELECT c.conname, c.confdeltype, t.relname AS src, f.relname AS tgt
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_class f ON f.oid = c.confrelid
            WHERE c.contype = 'f'
              AND (t.relname IN ('whatsapp_thread_evidence',
                                 'whatsapp_outstanding_questions')
                   OR (t.relname = 'whatsapp_thread_candidates'
                       AND f.relname = 'whatsapp_thread_evidence'))
            ORDER BY c.conname""")
        self.assertTrue(rows, "no foreign keys found — is the revision applied?")
        for name, deltype, src, tgt in rows:
            with self.subTest(constraint=name, src=src, tgt=tgt):
                if tgt == "whatsapp_threads":
                    continue                      # deleted last by the reset
                self.assertEqual("n", deltype,
                                 f"{name} is {deltype!r}, not SET NULL — it can block "
                                 "the certified tester reset")
        # and the gate's ledger row is a scalar, by design
        ledger_fk = [r for r in rows if r[3] == "whatsapp_messages"]
        self.assertEqual([], ledger_fk,
                         "outbound_message_id became a foreign key; G37R-RV-03 measured "
                         "what that costs")


if __name__ == "__main__":
    unittest.main()
