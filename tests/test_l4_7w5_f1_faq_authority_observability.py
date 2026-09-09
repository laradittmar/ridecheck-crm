"""L4.7W5-F1 — canonical business truth outranks generated prose.

A customer asked "¿Te va a estar presente?" and was told "no estaremos presentes durante el
proceso" — that RideCheck would not attend its own inspection. Two independent failures
produced it, and both are general, not phrasing accidents:

  1. `_PRESENCE_FAQ_DETECTION` held three literal sentences, so an ordinary way of asking
     matched none and the topic was never raised;
  2. de-duplication compared one substring per topic, and "presente" appears in the *wrong*
     sentence as readily as the right one — so the canonical correction was withheld as a
     duplicate of the falsehood it should have replaced.

A substring cannot tell agreement from contradiction. Each topic now carries SATISFIED and
CONTRADICTS predicates, and canonical truth wins outright.

FAQ-PRES-01..06   presence detection, role distinction, canonical authority
FAQ-DEDUP-01..04  emit once, but override wrong prose
PAY-01..05        payment policy
DEBOUNCE-01..05   burst-aware stall detection
RESET-WAMID-01..04 reset preserves callback attribution
BURST-01          the exact failed session, end to end
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
import types
import unittest
from datetime import datetime, timezone
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
from app.models import (AiEvent, Lead, WhatsAppContact, WhatsAppMessage, WhatsAppThread,
                        WhatsAppThreadState)
from app.services.conversation_engine import (
    _FAQ_PRESENCE_ANSWER,
    _FAQ_PAYMENT_ANSWER,
    _FAQ_REPORT_ANSWER,
    _FAQ_SCOPE_ANSWER,
    _FAQ_TOPIC_CONTRADICTS,
    _payment_contradicts_policy,
    _FAQ_TOPIC_SATISFIED,
    ConversationEngine,
    _strip_contradicting_sentences,
)
from app.services.tester_reset import ARCHIVE_WA_ID, reset_tester_to_zero_state

ALERT_SOURCE = (ROOT / "backend" / "app" / "services"
                / "unanswered_alert.py").read_text(encoding="utf-8-sig")

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})


@event.listens_for(_engine, "connect")
def _pragmas(conn, _rec):
    conn.execute("PRAGMA foreign_keys=OFF")


app.models.Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
_NOW = datetime.now(timezone.utc)

# The three real transcripts from the failed session.
BURST_1 = "Hola, ¿cómo están? Bueno, quería saber si hacían una revisión de un 2008 del 2014, ¿hacen eso ustedes?"
BURST_2 = "¿Mandan informe? ¿Cómo es? ¿Qué tiene el informe? ¿Te va a estar presente?"
BURST_3 = "Se paga con débito."

# The reply that actually went out (blocked by the kill switch before delivery).
BAD_REPLY = (
    "¡Hola! Sí, hacemos la revisión del Peugeot 2008 del 2014. Al finalizar la revisión, "
    "te enviamos un informe detallado que incluye la verificación de más de 250 puntos del "
    "vehículo, como carrocería, motor, frenos, y más. La revisión se realiza en el lugar "
    "donde está el auto y no estaremos presentes durante el proceso, pero recibirás el "
    "informe por correo. En cuanto a los pagos, aceptamos transferencia bancaria, Mercado "
    "Pago y efectivo, pero no se acepta débito. ¿En qué zona o ciudad está el auto?"
)


def _engine_stub() -> ConversationEngine:
    eng = ConversationEngine.__new__(ConversationEngine)
    eng._turn_ctx = None
    eng._contributing_sources = None
    eng._semantic_faq_topics = lambda: set()      # semantic layer silent: detection alone
    eng._turn_scheduling_requested = False
    return eng


def _topics(text: str) -> set:
    return _engine_stub()._explicit_faq_topics(text)


def _compose(primary: str, burst: str) -> str:
    return _engine_stub()._compose_secondary_answers(primary, burst)


class TestPresenceDetection(unittest.TestCase):

    def test_faq_pres_01_the_exact_failing_phrasing_is_detected(self):
        """FAQ-PRES-01 — '¿Te va a estar presente?' raised nothing before."""
        self.assertIn("presence", _topics(BURST_2))

    def test_faq_pres_02_and_03_natural_variants(self):
        """FAQ-PRES-02/03 — meaning, not a growing list of sentences."""
        for phrasing in (
            "¿Tengo que estar?",
            "¿Tengo que estar presente?",
            "¿Necesito estar durante la revisión?",
            "¿Puede ir el inspector solo?",
            "¿Hace falta que esté ahí?",
            "¿Ustedes están presentes?",
            "¿Quién hace la revisión?",
            "¿Va alguien de ustedes?",
            "¿Tenés que estar vos también?",
        ):
            with self.subTest(phrasing=phrasing):
                self.assertIn("presence", _topics(phrasing))

    def test_faq_pres_04_customer_and_inspector_roles_are_the_same_question(self):
        """FAQ-PRES-04 — asked from either side, one canonical answer resolves it."""
        customer_side = _topics("¿tengo que estar presente?")
        inspector_side = _topics("¿puede ir el inspector solo?")
        self.assertIn("presence", customer_side)
        self.assertIn("presence", inspector_side)

    def test_unrelated_text_does_not_trigger_presence(self):
        for phrasing in ("¿Cuánto sale la revisión?", "Quiero coordinar para el jueves",
                         "El auto está en Berazategui"):
            with self.subTest(phrasing=phrasing):
                self.assertNotIn("presence", _topics(phrasing))


class TestCanonicalAuthority(unittest.TestCase):

    def test_faq_pres_05_the_canonical_answer_is_the_business_truth(self):
        """FAQ-PRES-05 — the customer need not attend; nothing says we don't."""
        self.assertEqual(_FAQ_PRESENCE_ANSWER,
                         "No es necesario que estés presente durante la inspección.")

    def test_faq_pres_06_a_contradicting_reply_is_corrected_not_supplemented(self):
        """FAQ-PRES-06 — the whole defect, end to end.

        The false clause must be gone AND the canonical answer present. Leaving both would
        ship a message that contradicts itself.
        """
        out = _compose(BAD_REPLY, BURST_2)
        self.assertNotIn("no estaremos presentes", out.lower())
        self.assertIn(_FAQ_PRESENCE_ANSWER, out)

    def test_the_rest_of_the_reply_survives_the_correction(self):
        """Only the false sentence is removed — surrounding prose is fine."""
        out = _compose(BAD_REPLY, BURST_2)
        self.assertIn("informe detallado", out)
        self.assertIn("250 puntos", out)
        self.assertIn("¿En qué zona o ciudad está el auto?", out)

    def test_contradiction_patterns_cover_the_ways_of_saying_it(self):
        for wrong in ("no estaremos presentes durante el proceso.",
                      "no vamos a estar presentes.",
                      "la revisión se hace sin nuestra presencia."):
            with self.subTest(wrong=wrong):
                self.assertTrue(
                    any(re.search(p, wrong) for p in _FAQ_TOPIC_CONTRADICTS["presence"]),
                    wrong)

    def test_a_correct_presence_statement_is_not_flagged_as_contradiction(self):
        correct = "no es necesario que estés presente durante la inspección."
        self.assertFalse(any(re.search(p, correct)
                             for p in _FAQ_TOPIC_CONTRADICTS["presence"]))
        self.assertTrue(any(re.search(p, correct)
                            for p in _FAQ_TOPIC_SATISFIED["presence"]))

    def test_stripping_never_yields_a_message_that_still_contradicts(self):
        cleaned = _strip_contradicting_sentences(BAD_REPLY, "presence")
        self.assertNotIn("no estaremos presentes", cleaned.lower())


class TestFaqDedup(unittest.TestCase):

    def test_faq_dedup_01_scope_expressed_in_other_words_is_not_repeated(self):
        """FAQ-DEDUP-01 — the W3 redundancy class. The bad reply already said we go to
        where the car is, so the canonical scope paragraph must not follow it."""
        out = _compose(BAD_REPLY, "¿en qué consiste el servicio?")
        self.assertNotIn(_FAQ_SCOPE_ANSWER, out)

    def test_faq_dedup_02_report_already_answered_is_not_repeated(self):
        out = _compose("Al finalizar te enviamos un informe detallado.", BURST_2)
        self.assertEqual(out.count("informe detallado"), 1)

    def test_faq_dedup_03_a_missing_canonical_fact_is_still_appended(self):
        """FAQ-DEDUP-03 — dedup must not become silence."""
        out = _compose("Hola, ¿en qué zona está el auto?", "¿tengo que estar presente?")
        self.assertIn(_FAQ_PRESENCE_ANSWER, out)

    def test_faq_dedup_04_wrong_prose_loses_to_canonical_truth(self):
        """FAQ-DEDUP-04 — priority is canonical > generated, not first-writer-wins."""
        wrong = "Aceptamos débito sin problema."
        out = _compose(wrong, "¿aceptan débito?")
        self.assertNotIn("Aceptamos débito sin problema", out)
        self.assertIn(_FAQ_PAYMENT_ANSWER, out)


class TestPaymentPolicy(unittest.TestCase):

    def test_pay_01_debit_is_rejected(self):
        """PAY-01 — a declarative "se paga con débito" is a policy claim, not truth."""
        self.assertIn("payment", _topics("se paga con debito"))
        self.assertIn("Con débito no estamos trabajando", _FAQ_PAYMENT_ANSWER)

    def test_pay_02_03_04_accepted_methods_are_named(self):
        for method in ("efectivo", "transferencia", "Mercado Pago"):
            with self.subTest(method=method):
                self.assertIn(method, _FAQ_PAYMENT_ANSWER)

    def test_pay_05_credit_is_not_advertised_as_accepted(self):
        self.assertNotIn("crédito", _FAQ_PAYMENT_ANSWER.lower())
        self.assertTrue(_payment_contradicts_policy("también aceptamos crédito."))
        self.assertTrue(_payment_contradicts_policy("aceptamos débito sin problema."))
        self.assertTrue(_payment_contradicts_policy(
            "aceptamos efectivo, transferencia y débito."))

    def test_the_correct_payment_answer_is_not_self_flagged(self):
        self.assertFalse(_payment_contradicts_policy(_FAQ_PAYMENT_ANSWER.lower()),
                         "the canonical answer must never look like a contradiction")
        for correct in ("no se acepta débito.",
                        "con débito no estamos trabajando por el momento.",
                        "no aceptamos crédito ni débito."):
            with self.subTest(correct=correct):
                self.assertFalse(_payment_contradicts_policy(correct))


class TestFailedBurst(unittest.TestCase):
    """BURST-01 — the exact session, not hardcoded: composed from the real transcripts."""

    def test_burst_01_one_coherent_correct_response(self):
        burst = f"{BURST_1} {BURST_2} {BURST_3}"
        out = _compose(BAD_REPLY, burst)
        low = out.lower()

        self.assertNotIn("no estaremos presentes", low)          # falsehood gone
        self.assertIn(_FAQ_PRESENCE_ANSWER, out)                 # truth present
        self.assertIn("no se acepta débito", low)                # debit still rejected
        self.assertIn("informe", low)                            # report answered
        self.assertIn("¿en qué zona o ciudad está el auto?", low)  # location still requested
        self.assertNotIn(_FAQ_SCOPE_ANSWER, out)                 # no duplicated scope
        self.assertEqual(low.count("no es necesario que estés presente"), 1)

    def test_the_burst_contains_no_self_contradiction(self):
        out = _compose(BAD_REPLY, f"{BURST_1} {BURST_2} {BURST_3}").lower()
        says_absent = "no estaremos presentes" in out or "no vamos a estar presentes" in out
        says_customer_optional = "no es necesario que estés presente" in out
        self.assertFalse(says_absent and says_customer_optional)
        self.assertTrue(says_customer_optional)


class TestDebounceAwareAlert(unittest.TestCase):
    """DEBOUNCE-01..05 — asserted on the SQL contract, which is where the rule lives."""

    def setUp(self):
        self.fn = next(ast.unparse(n) for n in ast.walk(ast.parse(ALERT_SOURCE))
                       if isinstance(n, ast.FunctionDef)
                       and n.name == "_check_forwarded_but_unfinished")

    def test_debounce_02_03_a_successor_that_reached_ce_explains_earlier_events(self):
        """DEBOUNCE-02/03 — the false positives that fired on events 141 and 142."""
        self.assertIn("NOT EXISTS", self.fn)
        self.assertIn("later.status = 'processed'", self.fn.replace('"', "'"))
        self.assertIn("later.thread_id = ae.thread_id", self.fn)
        self.assertIn("later.id > ae.id", self.fn)

    def test_debounce_01_04_a_genuine_stall_still_alerts(self):
        """DEBOUNCE-01/04 — the suppression is conditional, never blanket."""
        self.assertIn("ae.status = 'triggered'", self.fn.replace('"', "'"))
        self.assertIn("STALLED_TRANSPORT_THRESHOLD_SECONDS", self.fn)
        self.assertIn("unanswered_alert_sent_at IS NULL", self.fn)

    def test_debounce_05_the_successor_window_is_bounded(self):
        """DEBOUNCE-05 — an unrelated message an hour later must not excuse a real stall."""
        from app.services.unanswered_alert import DEBOUNCE_SUCCESSOR_WINDOW_SECONDS
        self.assertEqual(DEBOUNCE_SUCCESSOR_WINDOW_SECONDS, 120)
        self.assertIn("DEBOUNCE_SUCCESSOR_WINDOW_SECONDS", self.fn)
        self.assertIn("later.created_at >= ae.created_at", self.fn)

    def test_the_threshold_and_the_sla_check_are_unchanged(self):
        from app.services.unanswered_alert import (STALLED_TRANSPORT_THRESHOLD_SECONDS,
                                                   _ALERT_THRESHOLD_SECONDS)
        self.assertEqual(STALLED_TRANSPORT_THRESHOLD_SECONDS, 180)
        self.assertEqual(_ALERT_THRESHOLD_SECONDS, 120)


class TestResetWamidAttribution(unittest.TestCase):

    def setUp(self):
        with _engine.begin() as conn:
            for tbl in reversed(app.models.Base.metadata.sorted_tables):
                conn.execute(tbl.delete())
        self.db = _Session()
        self.contact = WhatsAppContact(wa_id="5491153368330", display_name="Tester")
        self.db.add(self.contact); self.db.flush()
        self.lead = Lead(estado="COTIZACION")
        self.db.add(self.lead); self.db.flush()
        self.thread = WhatsAppThread(contact_id=self.contact.id, lead_id=self.lead.id)
        self.db.add(self.thread); self.db.flush()
        self.db.add(WhatsAppThreadState(thread_id=self.thread.id))
        self.db.add(AiEvent(event_type="inbound_message", thread_id=self.thread.id,
                            wa_id="5491153368330", wa_message_id="wamid.IN1", status="triggered"))
        self.db.add(WhatsAppMessage(thread_id=self.thread.id, direction="in",
                                    message_type="audio", status="received",
                                    timestamp=_NOW, wa_message_id="wamid.IN1"))
        self.sent = WhatsAppMessage(thread_id=self.thread.id, direction="out",
                                    message_type="flow", status="delivered", timestamp=_NOW,
                                    path_id="CE_FLOW", deployment_id="bb2546d",
                                    wa_message_id="wamid.SENT.LIVE")
        self.blocked = WhatsAppMessage(thread_id=self.thread.id, direction="out",
                                       message_type="text", status="blocked", timestamp=_NOW,
                                       path_id="CE_TEXT", wa_message_id="")
        self.db.add_all([self.sent, self.blocked])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_reset_wamid_01_a_sent_wamid_still_resolves_after_reset(self):
        """RESET-WAMID-01 — the callback Meta sends ten minutes later must still land."""
        reset_tester_to_zero_state(self.db, "5491153368330")
        found = self.db.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == "wamid.SENT.LIVE")
        ).scalar_one_or_none()
        self.assertIsNotNone(found, "outbound evidence was destroyed by the reset")
        self.assertEqual(found.path_id, "CE_FLOW")
        self.assertEqual(found.deployment_id, "bb2546d")

    def test_reset_wamid_02_that_row_is_no_longer_attached_to_the_tester(self):
        """RESET-WAMID-02 — evidence is retained, conversation is not."""
        reset_tester_to_zero_state(self.db, "5491153368330")
        archive = self.db.execute(
            select(WhatsAppContact).where(WhatsAppContact.wa_id == ARCHIVE_WA_ID)
        ).scalar_one_or_none()
        self.assertIsNotNone(archive)
        found = self.db.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == "wamid.SENT.LIVE")
        ).scalar_one()
        archive_threads = [t.id for t in self.db.execute(
            select(WhatsAppThread).where(WhatsAppThread.contact_id == archive.id)
        ).scalars().all()]
        self.assertIn(found.thread_id, archive_threads)

    def test_reset_wamid_03_a_wamid_we_never_sent_is_still_unknown(self):
        """RESET-WAMID-03 — detection must not be weakened, only stopped from firing
        on our own history."""
        reset_tester_to_zero_state(self.db, "5491153368330")
        unknown = self.db.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == "wamid.NEVER.SENT")
        ).scalar_one_or_none()
        self.assertIsNone(unknown)

    def test_reset_wamid_04_business_state_still_returns_to_zero(self):
        """RESET-WAMID-04 — the next inbound must still look like a new customer."""
        reset_tester_to_zero_state(self.db, "5491153368330")
        self.assertIsNone(self.db.execute(
            select(WhatsAppContact).where(WhatsAppContact.wa_id == "5491153368330")
        ).scalar_one_or_none())
        for model in (WhatsAppThread, WhatsAppThreadState, AiEvent, Lead):
            self.assertEqual(len(self.db.execute(select(model)).scalars().all()),
                             1 if model is WhatsAppThread else 0,
                             f"{model.__name__} not at zero (archive thread excepted)")
        inbound = self.db.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.direction == "in")
        ).scalars().all()
        self.assertEqual(inbound, [], "inbound conversation content must not survive")

    def test_a_blocked_row_with_no_wamid_is_not_archived(self):
        """Nothing was transmitted, so there is no callback to answer."""
        reset_tester_to_zero_state(self.db, "5491153368330")
        blocked = self.db.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.status == "blocked")
        ).scalars().all()
        self.assertEqual(blocked, [])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
