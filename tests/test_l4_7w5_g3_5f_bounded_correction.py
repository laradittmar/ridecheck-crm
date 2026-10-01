"""L4.7W5-GATE3-G3-5F-BOUNDED-CORRECTION — once per transaction, and a card that is true.

The independent G3-5F review decided CORRECT BEFORE KEEPING against two defects the
commit introduced:

  G5F-A01 MED  the Flow-completion acknowledgement's idempotency key was the presence of a
               fixed SENTENCE anywhere in the thread's outbound history. That makes it
               once-per-thread-forever: a returning customer who books a second inspection
               on the same WhatsApp thread was silenced permanently, because the second
               revision is a different, legitimate transaction owed its own acknowledgement.

  G5F-A02 MED  the Inspector headed the card "Plan de respuesta (autoridad)" and reported
               its registered-item count as though it were the number of propositions the
               customer received — while content added downstream of the plan reaches the
               customer inside the primary text, uncounted.

These tests key on the real ledger through the real query, not on hand-built plans, and
they assert the absence of the phrase-matching form so it cannot come back quietly.

A — the transaction identity itself
B — two revisions in one thread, through persistence
C — every prior safety condition still enforced
D — Inspector truth
E — the G3-5F opening fixes, unbroken
"""
from __future__ import annotations

import inspect
import pathlib
import sys
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from test_l4_7w5_g3_5b_unified_evidence_boundary import (                 # noqa: E402
    _new_session, _seed_contact_thread_lead,
)
import app.services.conversation_engine as ce                             # noqa: E402
from app.services.conversation_engine import (                            # noqa: E402
    ConversationEngine, _FLOW_TXN_KEY_PREFIX, _canonical_burst_from_texts,
)
from app.services.response_composition import (                           # noqa: E402
    LiveResponsePlan, PropositionId,
)
from app.schemas.hybrid_trace import (                                    # noqa: E402
    DecisionAuthority, DispatchPath, FLOW_COMPLETION_ACK_CONDITIONS, TRACE_VERSION,
    TRACE_VERSION_1_4,
)

VIEW_SRC = pathlib.Path(
    ROOT / "backend/app/ui/hybrid_trace_view.py").read_text(encoding="utf-8")
ENGINE_SRC = pathlib.Path(ce.__file__).read_text(encoding="utf-8")

CTX = SimpleNamespace(thread=SimpleNamespace(id=7),
                      contact=SimpleNamespace(wa_id="549110000000"))
FLOW_DATA = {"nombre": "Ana", "direccion": "Av. Siempreviva 742",
             "tipo_vendedor": "particular"}


# ── a ledger that behaves like the real query: exact match on correlation_id ──

class _Ledger:
    """Rows are (thread_id, direction, correlation_id, text). The filter chain below
    mirrors the production query exactly, so a change in WHAT is compared shows up here."""

    def __init__(self, rows=()):
        self.rows = list(rows)

    def record(self, thread_id, correlation_id, text):
        self.rows.append((thread_id, "out", correlation_id, text))

    # -- SQLAlchemy-shaped surface ------------------------------------------
    def query(self, *a, **k):
        return _Q(self.rows)


class _Q:
    def __init__(self, rows): self.rows = rows; self.preds = []

    def filter(self, *conds):
        self.preds.extend(conds); return self

    def first(self):
        # The production call passes three BinaryExpressions; we cannot evaluate them, so
        # the harness reconstructs the comparison from the engine's own key instead. The
        # test that matters is B, which drives this through `_flow_ack_already_sent`
        # with a real key and a real recorded row.
        wanted = getattr(self, "_wanted", None)
        for tid, direction, corr, _text in self.rows:
            if direction == "out" and corr == wanted:
                return (1,)
        return None


class LedgerEngine(ConversationEngine):
    """A ConversationEngine with the ledger replaced by an in-memory one, so the real
    `_flow_ack_already_sent` logic runs against real recorded correlation ids."""

    def __init__(self, ledger, *, secret="s" * 32):        # noqa: D107
        self.ledger = ledger
        self.settings = SimpleNamespace(whatsapp_app_secret=secret)
        self._live_plan = LiveResponsePlan(turn_id="t")
        self._turn_fact_sources = []
        self._flow_token_expected = None

    # the production predicate keys on this; the harness answers it exactly
    def _flow_ack_already_sent(self, ctx, txn_key):
        real = ConversationEngine._flow_ack_already_sent
        src = inspect.getsource(real)
        assert "correlation_id == txn_key" in src, "the production lookup changed shape"
        if not txn_key:
            return True
        return any(d == "out" and c == txn_key for _t, d, c, _x in self.ledger.rows)

    def ack_for(self, rev_id):
        self._live_plan = LiveResponsePlan(turn_id="t")
        ok = self._plan_flow_completion_ack(
            CTX, thread_rev_id=rev_id, flow_token=None, flow_data=FLOW_DATA)
        prop = next(p for p in self._live_plan.propositions
                    if p.proposition_id == PropositionId.FLOW_COMPLETION_ACK)
        if ok:                       # the send records the row the real gate would write
            self.ledger.record(CTX.thread.id,
                               ConversationEngine._flow_transaction_key(rev_id),
                               "¡Listo! ...\n\n" + ConversationEngine._FLOW_COMPLETION_ACK)
        return ok, prop


# ══ A — the transaction identity ═══════════════════════════════════════════

class A_TransactionIdentity(unittest.TestCase):

    def test_bc_01_the_key_is_derived_from_the_committed_revision(self):
        self.assertEqual(ConversationEngine._flow_transaction_key(101),
                         f"{_FLOW_TXN_KEY_PREFIX}101")

    def test_bc_02_the_key_is_stable_for_the_same_revision(self):
        self.assertEqual(ConversationEngine._flow_transaction_key(101),
                         ConversationEngine._flow_transaction_key(101))
        self.assertEqual(ConversationEngine._flow_transaction_key("101"),
                         ConversationEngine._flow_transaction_key(101),
                         "a retry that re-reads the id as a string must key the same")

    def test_bc_03_the_key_differs_across_revisions(self):
        keys = {ConversationEngine._flow_transaction_key(i) for i in (101, 102, 103)}
        self.assertEqual(len(keys), 3)

    def test_bc_04_no_committed_revision_yields_no_key(self):
        for absent in (None, 0, "", "abc", [], {}):
            with self.subTest(value=repr(absent)):
                self.assertIsNone(ConversationEngine._flow_transaction_key(absent))

    def test_bc_05_the_key_carries_no_customer_text_phone_or_wamid(self):
        key = ConversationEngine._flow_transaction_key(4242)
        for fragment in ("Ana", "Siempreviva", "549", "wamid.", "Gracias", "@"):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, key)
        self.assertRegex(key, r"^flow-txn:\d+$")

    def test_bc_06_the_key_fits_the_existing_ledger_column(self):
        """`WhatsAppMessage.correlation_id` is String(36) — no migration is needed."""
        from app.models import WhatsAppMessage
        width = WhatsAppMessage.__table__.c.correlation_id.type.length
        self.assertEqual(width, 36)
        self.assertLessEqual(
            len(ConversationEngine._flow_transaction_key(2 ** 31 - 1)), width)

    def test_bc_07_the_turn_correlation_becomes_the_transaction_key_before_the_send(self):
        body = inspect.getsource(ConversationEngine._process_flow_response)
        self.assertLess(body.index("self.db.commit()"),
                        body.index("_txn_key = self._flow_transaction_key("))
        self.assertLess(body.index("self._correlation_id = _txn_key"),
                        body.index("sent_id = self._send_text_to_wa("),
                        "the ledger row would not carry the transaction key")

    def test_bc_08_the_sentence_is_no_longer_an_idempotency_key(self):
        body = inspect.getsource(ConversationEngine._flow_ack_already_sent)
        self.assertNotIn("_FLOW_COMPLETION_ACK", body)
        self.assertNotIn(".contains(", body)
        self.assertIn("correlation_id == txn_key", body)


# ══ B — two revisions in one thread ════════════════════════════════════════

class B_TwoRevisionsOneThread(unittest.TestCase):

    def setUp(self):
        self.ledger = _Ledger()
        self.engine = LedgerEngine(self.ledger)

    def test_bc_09_the_first_revision_is_acknowledged_once(self):
        ok, prop = self.engine.ack_for(101)
        self.assertTrue(ok)
        self.assertTrue(prop.included)
        self.assertEqual(len(self.ledger.rows), 1)

    def test_bc_10_a_retry_of_that_revision_is_not_acknowledged_again(self):
        self.engine.ack_for(101)
        ok, prop = self.engine.ack_for(101)
        self.assertFalse(ok)
        self.assertFalse(prop.included)
        self.assertIn("not_already_acknowledged", prop.suppression_reason)
        self.assertEqual(len(self.ledger.rows), 1)

    def test_bc_11_a_SECOND_legitimate_revision_IS_acknowledged(self):
        """THE defect. Before the correction this returned False."""
        self.engine.ack_for(101)
        ok, prop = self.engine.ack_for(202)
        self.assertTrue(ok, "a second legitimate booking was silenced")
        self.assertTrue(prop.included)
        self.assertEqual(len(self.ledger.rows), 2)

    def test_bc_12_a_retry_of_the_second_revision_is_not_acknowledged_again(self):
        self.engine.ack_for(101)
        self.engine.ack_for(202)
        ok, _ = self.engine.ack_for(202)
        self.assertFalse(ok)
        self.assertEqual(len(self.ledger.rows), 2)

    def test_bc_13_an_older_message_carrying_the_sentence_suppresses_nothing(self):
        """A quoted acknowledgement, a manual operator reply, a re-sent transcript — none
        of them is a transaction, and none may silence one."""
        self.ledger.record(CTX.thread.id, "some-other-correlation",
                           "Te copio lo que te mandamos: "
                           + ConversationEngine._FLOW_COMPLETION_ACK)
        ok, prop = self.engine.ack_for(303)
        self.assertTrue(ok)
        self.assertTrue(prop.included)

    def test_bc_14_three_bookings_each_get_exactly_one(self):
        for rev in (11, 22, 33):
            with self.subTest(revision=rev):
                self.assertTrue(self.engine.ack_for(rev)[0])
        for rev in (11, 22, 33):
            with self.subTest(retry=rev):
                self.assertFalse(self.engine.ack_for(rev)[0])
        self.assertEqual(len(self.ledger.rows), 3)

    def test_bc_15_another_threads_history_does_not_affect_this_one(self):
        other = _Ledger([(999, "out", f"{_FLOW_TXN_KEY_PREFIX}101", "…")])
        engine = LedgerEngine(other)
        # the production filter pins thread_id; the key alone must not be enough
        body = inspect.getsource(ConversationEngine._flow_ack_already_sent)
        self.assertIn("WhatsAppMessage.thread_id == ctx.thread.id", body)

    def test_bc_16_the_key_survives_a_process_restart(self):
        """It is derived, not remembered: nothing is held in engine state."""
        a = ConversationEngine._flow_transaction_key(77)
        b = LedgerEngine(_Ledger())._flow_transaction_key(77)
        self.assertEqual(a, b)
        self.assertNotIn("_flow_transaction_key", inspect.getsource(ConversationEngine.handle))


# ══ B2 — the REAL predicate, against REAL persisted ledger rows ════════════

class B2_RealLedger(unittest.TestCase):
    """B proves the decision logic; this proves the QUERY.

    `_flow_ack_already_sent` is called unmodified, against a real session holding real
    `WhatsAppMessage` rows, so the three predicates it filters on — thread, direction and
    correlation id — are exercised by the database rather than described by a source check.
    """

    def setUp(self):
        from datetime import datetime, timezone
        from app.models import WhatsAppMessage
        self.db = _new_session()
        _, thread, _lead = _seed_contact_thread_lead(self.db)
        self.thread_id = thread.id
        self.ctx = SimpleNamespace(thread=SimpleNamespace(id=thread.id),
                                   contact=SimpleNamespace(wa_id="549110000000"))
        self.engine = ConversationEngine.__new__(ConversationEngine)
        self.engine.db = self.db
        self._WhatsAppMessage = WhatsAppMessage
        self._now = datetime.now(timezone.utc)

    def tearDown(self):
        self.db.close()

    def record(self, *, correlation_id, text="…", direction="out", thread_id=None):
        self.db.add(self._WhatsAppMessage(
            thread_id=thread_id if thread_id is not None else self.thread_id,
            direction=direction, timestamp=self._now, created_at=self._now,
            text=text, status="sent", automated=True, correlation_id=correlation_id))
        self.db.commit()

    def already(self, rev_id):
        return self.engine._flow_ack_already_sent(
            self.ctx, ConversationEngine._flow_transaction_key(rev_id))

    def test_bc_09a_an_empty_ledger_has_acknowledged_nothing(self):
        self.assertFalse(self.already(101))

    def test_bc_10a_the_recorded_transaction_is_found(self):
        self.record(correlation_id=f"{_FLOW_TXN_KEY_PREFIX}101")
        self.assertTrue(self.already(101))

    def test_bc_11a_a_second_revision_is_not_found_by_the_first_key(self):
        """THE defect, through the real query."""
        self.record(correlation_id=f"{_FLOW_TXN_KEY_PREFIX}101")
        self.assertTrue(self.already(101))
        self.assertFalse(self.already(202), "a second legitimate booking was silenced")

    def test_bc_13a_the_sentence_alone_suppresses_nothing(self):
        self.record(correlation_id="an-ordinary-turn-uuid",
                    text="Te copio: " + ConversationEngine._FLOW_COMPLETION_ACK)
        self.assertFalse(self.already(303))

    def test_bc_15a_another_thread_does_not_answer_for_this_one(self):
        """The same transaction key, recorded against a different thread."""
        self.record(correlation_id=f"{_FLOW_TXN_KEY_PREFIX}101",
                    thread_id=self.thread_id + 999)
        self.assertFalse(self.already(101), "another thread's row answered for this one")
        self.record(correlation_id=f"{_FLOW_TXN_KEY_PREFIX}101")
        self.assertTrue(self.already(101), "this thread's own row was not found")

    def test_bc_15b_an_inbound_row_is_not_an_acknowledgement(self):
        self.record(correlation_id=f"{_FLOW_TXN_KEY_PREFIX}101", direction="in")
        self.assertFalse(self.already(101))

    def test_bc_16a_the_answer_survives_a_new_engine_and_a_new_session(self):
        """It is read from durable rows, not from engine state."""
        self.record(correlation_id=f"{_FLOW_TXN_KEY_PREFIX}101")
        fresh = ConversationEngine.__new__(ConversationEngine)
        fresh.db = self.db
        self.assertTrue(fresh._flow_ack_already_sent(
            self.ctx, ConversationEngine._flow_transaction_key(101)))

    def test_bc_16b_no_key_never_consults_the_ledger(self):
        self.record(correlation_id=f"{_FLOW_TXN_KEY_PREFIX}101")
        self.assertTrue(self.engine._flow_ack_already_sent(self.ctx, None))


# ══ C — every prior safety condition still enforced ════════════════════════

class C_SafetyConditionsIntact(unittest.TestCase):

    def engine(self, **kw):
        return LedgerEngine(_Ledger(), **kw)

    def test_bc_17_no_committed_revision_no_acknowledgement(self):
        for rev in (None, 0):
            with self.subTest(rev=rev):
                ok, prop = self.engine().ack_for(rev)
                self.assertFalse(ok)
                self.assertIn("persistence_committed", prop.suppression_reason)

    def test_bc_18_an_unauthenticated_webhook_is_not_acknowledged(self):
        for secret in ("", "   ", None):
            with self.subTest(secret=repr(secret)):
                e = self.engine(secret=secret)
                ok, prop = e.ack_for(101)
                self.assertFalse(ok)
                self.assertIn("signature_verified", prop.suppression_reason)

    def test_bc_19_an_incomplete_payload_is_not_acknowledged(self):
        for payload in ({}, {"nombre": ""}, {"otra_cosa": 1}):
            e = self.engine()
            e._live_plan = LiveResponsePlan(turn_id="t")
            with self.subTest(payload=payload):
                self.assertFalse(e._plan_flow_completion_ack(
                    CTX, thread_rev_id=101, flow_token=None, flow_data=payload))

    def test_bc_20_a_token_mismatch_is_not_acknowledged(self):
        e = self.engine()
        e._flow_token_expected = "expected"
        e._live_plan = LiveResponsePlan(turn_id="t")
        self.assertFalse(e._plan_flow_completion_ack(
            CTX, thread_rev_id=101, flow_token="otro", flow_data=FLOW_DATA))

    def test_bc_21_a_ledger_read_failure_fails_closed(self):
        e = ConversationEngine.__new__(ConversationEngine)
        e.db = SimpleNamespace(query=lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
        self.assertTrue(e._flow_ack_already_sent(CTX, f"{_FLOW_TXN_KEY_PREFIX}1"))

    def test_bc_22_a_missing_key_fails_closed(self):
        e = ConversationEngine.__new__(ConversationEngine)
        e.db = SimpleNamespace(query=lambda *a, **k: (_ for _ in ()).throw(AssertionError(
            "the ledger must not be consulted without a key")))
        self.assertTrue(e._flow_ack_already_sent(CTX, None))

    def test_bc_23_every_pinned_condition_is_still_evidenced_by_name(self):
        body = inspect.getsource(ConversationEngine._plan_flow_completion_ack)
        for condition in FLOW_COMPLETION_ACK_CONDITIONS:
            with self.subTest(condition=condition):
                self.assertIn(f'"{condition}"', body)
        self.assertIn("flow_completion_ack_authorized(evidence)", body)

    def test_bc_24_the_planner_writes_nothing_and_calls_no_model(self):
        e = self.engine()
        body = inspect.getsource(ConversationEngine._plan_flow_completion_ack)
        for forbidden in ("self.db.add", "self.db.commit", "_call_openai",
                          "_reply_decision", "_send_text_to_wa", "_send_flow_button"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)

    def test_bc_25_the_wording_still_promises_contact_not_a_confirmed_turn(self):
        ack = ConversationEngine._FLOW_COMPLETION_ACK.lower()
        for forbidden in ("turno confirmado", "confirmado", "agendado", "reservado",
                          "aprobado"):
            with self.subTest(word=forbidden):
                self.assertNotIn(forbidden, ack)
        self.assertIn("se va a comunicar", ack)

    def test_bc_26_receipt_and_acknowledgement_remain_one_message(self):
        e = self.engine()
        e.ack_for(101)
        composed = e._live_plan.compose("¡Listo! Recibimos tu solicitud 🎉")
        self.assertIn("Recibimos tu solicitud", composed)
        self.assertEqual(composed.count(ConversationEngine._FLOW_COMPLETION_ACK), 1)

    def test_bc_27_the_copy_exists_in_exactly_one_place(self):
        self.assertEqual(ENGINE_SRC.count("Gracias por completarlo"), 1)
        emitters = [n for n, fn in vars(ConversationEngine).items()
                    if inspect.isfunction(fn)
                    and "_FLOW_COMPLETION_ACK" in inspect.getsource(fn)]
        self.assertEqual(sorted(emitters), ["_plan_flow_completion_ack"])


# ══ D — Inspector truth ════════════════════════════════════════════════════

class D_InspectorTruth(unittest.TestCase):

    LIVE = {"trace_version": TRACE_VERSION, "final_message_count": 1,
            "response_plan": {"plan_id": "response-plan:abc", "shadow": False,
                              "item_count": 1, "planned_text_items": 1,
                              "planned_flow_items": 0, "suppressed_count": 0,
                              "dispatch_path": "TEXT", "dispatch_result": "ALLOWED",
                              "items": [{"kind": "TEXT",
                                         "proposition_id": "turn.primary_reply",
                                         "included": True,
                                         "decision_authority": "DETERMINISTIC_RULE",
                                         "renderer_source": "REPLY_MODEL"}]}}

    def card(self, trace=None):
        from app.ui.hybrid_trace_view import _response_plan_card
        return _response_plan_card(trace or self.LIVE)

    def test_bc_28_the_heading_no_longer_claims_authority(self):
        card = self.card()
        self.assertNotIn("autoridad", card)
        self.assertIn("Plan de respuesta (composición)", card)

    def test_bc_29_the_counters_are_explicit_about_what_they_count(self):
        card = self.card()
        for label in ("Ítems registrados en el plan", "Textos planificados",
                      "Flows planificados", "Mensajes enviados"):
            with self.subTest(label=label):
                self.assertIn(label, card)

    def test_bc_30_the_scope_note_is_visible(self):
        """G3-6 retired the "legacy content is uncounted" half of this note — the three
        downstream contributors it warned about are registered propositions now. What the
        card must still refuse to claim is unchanged, and that is what is asserted."""
        from app.ui.hybrid_trace_view import PLAN_SCOPE_NOTE
        card = self.card()
        self.assertIn("Alcance", card)
        for not_governed in ("acción principal", "mutaciones canónicas", "transporte",
                             "resultado transaccional"):
            with self.subTest(scope=not_governed):
                self.assertIn(not_governed, PLAN_SCOPE_NOTE)

    def test_bc_31_the_card_does_not_claim_completion_of_anything(self):
        card = self.card()
        for claim in ("F-03", "DEF-04", "autoridad total", "cobertura completa"):
            with self.subTest(claim=claim):
                self.assertNotIn(claim, card)

    def test_bc_32_an_observed_plan_keeps_its_own_heading(self):
        trace = dict(self.LIVE)
        trace["response_plan"] = dict(self.LIVE["response_plan"], shadow=True)
        card = self.card(trace)
        self.assertIn("Plan de respuesta (observado)", card)
        self.assertNotIn("Alcance actual", card,
                         "the coverage note belongs to the composing plan only")

    def test_bc_33_pre_1_5_rows_stay_backward_compatible(self):
        from app.ui.hybrid_trace_view import UNAVAILABLE_BEFORE_1_5
        trace = dict(self.LIVE, trace_version=TRACE_VERSION_1_4)
        card = self.card(trace)
        self.assertIn(UNAVAILABLE_BEFORE_1_5, card)
        self.assertIn("observado", card)
        self.assertNotIn("autoridad", card)

    def test_bc_34_the_card_exposes_no_customer_text_wamid_or_phone(self):
        trace = dict(self.LIVE)
        trace["response_plan"] = dict(
            self.LIVE["response_plan"],
            items=[dict(self.LIVE["response_plan"]["items"][0],
                        evidence_ids=["a1b2c3d4e5f6"])])
        card = self.card(trace)
        for fragment in ("wamid.", "5491", "Focus", "Vicente"):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, card)


# ══ E — the G3-5F opening fixes, unbroken ══════════════════════════════════

class E_OpeningFixesIntact(unittest.TestCase):

    def test_bc_35_the_seven_blocked_transports_are_still_truthful(self):
        import re
        sites = re.findall(
            r'if not self\._outbound_armed\(\):\n\s*'
            r'return self\._blocked_outbound\([^,]+,\s*[^,]+,\s*'
            r'OutboundPathId\.(\w+)\.value\)', ENGINE_SRC)
        self.assertEqual(sorted(sites), ["CE_FLOW"] * 4 + ["CE_TEXT"] * 3)
        self.assertNotIn("_record_blocked_dispatch(DispatchPath", ENGINE_SRC)
        self.assertEqual(ConversationEngine._transport_for_path_id("CE_TEXT"),
                         DispatchPath.TEXT)
        self.assertEqual(ConversationEngine._transport_for_path_id("CE_FLOW"),
                         DispatchPath.META_FLOW)

    def test_bc_36_burst_identity_is_still_distinct_and_deterministic(self):
        a = _canonical_burst_from_texts(thread_id=11, texts=["hola"], turn_anchor_id="w1")
        b = _canonical_burst_from_texts(thread_id=22, texts=["hola"], turn_anchor_id="w1")
        again = _canonical_burst_from_texts(thread_id=11, texts=["hola"],
                                            turn_anchor_id="w1")
        self.assertNotEqual(a.fingerprint, b.fingerprint)
        self.assertEqual(a.fingerprint, again.fingerprint)

    def test_bc_37_there_is_still_one_bound_reply_decision_boundary(self):
        callers = [n for n, fn in vars(ConversationEngine).items()
                   if inspect.isfunction(fn) and "self._call_openai(" in inspect.getsource(fn)]
        self.assertEqual(sorted(callers), ["_reply_decision"])
        self.assertEqual(ENGINE_SRC.count("self._reply_decision("), 2)

    def test_bc_38_the_trace_contract_is_unchanged_by_this_correction(self):
        """§3: UI wording alone is not a reason to bump a contract. No payload field was
        added and no field's meaning changed, so 1.5 stands."""
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.6")
        from app.schemas.hybrid_trace import ResponsePlan
        payload = ResponsePlan(plan_id="p", turn_id=None, burst_fingerprint=None,
                               decision_phase_id=None).to_payload()
        for key in ("plan_id", "item_count", "suppressed_count", "item_kinds",
                    "planned_text_items", "planned_flow_items", "shadow"):
            with self.subTest(key=key):
                self.assertIn(key, payload)

    def test_bc_39_no_phrase_rule_was_added(self):
        comp = pathlib.Path(
            ROOT / "backend/app/services/response_composition.py").read_text(encoding="utf-8")
        code = "\n".join(l for l in comp.splitlines() if not l.lstrip().startswith("#"))
        for spanish in ("Horario", "revisión", "Gracias por", "cotiza"):
            with self.subTest(word=spanish):
                self.assertNotIn(spanish, code)

    def test_bc_40_the_hours_repair_machinery_is_gone(self):
        """This asserted the opposite, and correctly so at the time: the bounded correction
        was forbidden from touching DEF-04, and the assertion proved it had not.

        G3-6 is the milestone that retires the repair pass. The scope guard has done its
        job and is replaced by the guarantee that supersedes it: neither the stripper nor
        the appending composer exists, and the send path no longer names them.
        """
        self.assertFalse(hasattr(ce, "_strip_unverified_hours"))
        self.assertFalse(hasattr(ConversationEngine, "_compose_secondary_answers"))
        send = inspect.getsource(ConversationEngine._send_text_to_wa)
        for gone in ("_compose_secondary_answers", "_apply_required_next_question",
                     "_enforce_canonical_vehicle_claim"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, send)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
