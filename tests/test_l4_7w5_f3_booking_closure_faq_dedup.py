"""L4.7W5-F3 — a successful booking must say so, and say it once.

The complete Wild booked correctly and then went silent: WhatsApp showed "Formulario
completado" and the conversation stopped dead. Nothing was broken in the booking — the
acknowledgement simply had no path out.

Root cause, proven not assumed: `handle_confirm_booking` sets `state.needs_human = True` as
part of the booking write, and CE's human-takeover guard runs BEFORE the flow_response is
routed. So the flow_response returns `skipped_human` and `_process_flow_response` — which
holds the receipt wording — is never reached for an endpoint-backed Flow.

The same Wild left one FAQ duplication: prose saying "Revisamos el auto en el lugar donde
está" did not match the scope SATISFIED predicate, which listed the verbs it expected.

BOOK-END-01..10   receipt, pending semantics, exactly-once, attribution, handoff distinction
SCOPE-DEDUP-01..05 scope satisfied by meaning, contradiction still overridden
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
import types
import unittest
from datetime import date, time
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

from app.services.booking_flow_service import build_booking_receipt_message
from app.services.conversation_engine import (_FAQ_SCOPE_ANSWER, _FAQ_TOPIC_CONTRADICTS,
                                              _FAQ_TOPIC_SATISFIED, ConversationEngine,
                                              _norm_lower)

BFS_SOURCE = (ROOT / "backend" / "app" / "services"
              / "booking_flow_service.py").read_text(encoding="utf-8-sig")
CE_SOURCE = (ROOT / "backend" / "app" / "services"
             / "conversation_engine.py").read_text(encoding="utf-8-sig")


def _fn(source: str, name: str) -> str:
    return next(ast.unparse(n) for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.FunctionDef) and n.name == name)


def _fn_code(source: str, name: str) -> str:
    """Function body with the docstring removed.

    A docstring that *names* a forbidden value — "can never appear as MANUAL_CRM" — is
    prose about the guarantee, not a violation of it. Asserting over unparsed source
    without stripping it produces a false failure; this project has hit that repeatedly.
    """
    node = next(n for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.FunctionDef) and n.name == name)
    body = node.body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return "\n".join(ast.unparse(stmt) for stmt in body)


class TestBookingReceipt(unittest.TestCase):

    def test_book_end_01_a_successful_booking_sends_one_receipt(self):
        """BOOK-END-01 — the send happens after the commit, inside the confirm handler."""
        fn = _fn(BFS_SOURCE, "handle_confirm_booking")
        self.assertIn("self._send_booking_receipt(", fn)
        i_commit = fn.index("self.db.commit()")
        i_send = fn.index("_send_booking_receipt")
        self.assertLess(i_commit, i_send,
                        "the booking must be durable before we acknowledge it")

    def test_book_end_02_it_never_claims_the_appointment_is_confirmed(self):
        """BOOK-END-02 — approval is PENDING; saying 'turno confirmado' would be false."""
        msg = build_booking_receipt_message("Lara", "sábado 12 de septiembre", "13:30")
        low = msg.lower()
        self.assertIn("recibimos tu solicitud", low)
        self.assertIn("te confirma el turno a la brevedad", low)
        for false_claim in ("turno confirmado", "queda confirmado", "ya está confirmado",
                            "confirmado para"):
            self.assertNotIn(false_claim, low, false_claim)

    def test_the_receipt_carries_the_booked_day_and_time(self):
        msg = build_booking_receipt_message("Lara", "sábado 12 de septiembre", "13:30")
        self.assertIn("sábado 12 de septiembre", msg)
        self.assertIn("13:30", msg)
        self.assertIn("Lara", msg)

    def test_it_degrades_without_a_name_or_slot(self):
        self.assertIn("Recibimos tu solicitud", build_booking_receipt_message(None, None, None))
        self.assertIn("Lara", build_booking_receipt_message("Lara", None, None))

    def test_book_end_03_04_a_failed_booking_sends_no_receipt(self):
        """BOOK-END-03/04 — revalidation failure raises before the write and the send."""
        fn = _fn(BFS_SOURCE, "handle_confirm_booking")
        i_fail = fn.index("BOOKING_REVALIDATION_FAIL")
        i_send = fn.index("_send_booking_receipt")
        self.assertLess(i_fail, i_send)
        self.assertIn("raise BookingSlotConflictError", fn)
        # the conflict raise sits between them, so the send is unreachable on failure
        self.assertLess(i_fail, fn.index("raise BookingSlotConflictError"))

    def test_book_end_05_06_exactly_once_comes_from_the_consumed_token(self):
        """BOOK-END-05/06 — a Meta retry or duplicate confirm cannot reach the send."""
        confirm = _fn(BFS_SOURCE, "handle_confirm_booking")
        self.assertIn("state.flow_booking_token = None", confirm)
        resolve = _fn(BFS_SOURCE, "resolve_context")
        self.assertIn("token invalid or already consumed", resolve)
        self.assertIn("raise BookingTokenError", resolve)
        # and the receipt itself documents that it relies on this, not on a flag
        receipt = _fn(BFS_SOURCE, "_send_booking_receipt")
        self.assertIn("BookingTokenError", receipt)

    def test_book_end_07_08_attribution_is_the_registered_booking_path(self):
        """BOOK-END-07/08 — never MANUAL_CRM, never unattributed."""
        receipt = _fn_code(BFS_SOURCE, "_send_booking_receipt")
        self.assertIn("OutboundPathId.BOOKING_FLOW.value", receipt)
        self.assertIn("OutboundSafetyGate", receipt)
        self.assertIn("get_deployment_id()", receipt)
        for wrong in ("MANUAL_CRM", "UNKNOWN", "UNATTRIBUTED"):
            self.assertNotIn(wrong, receipt)

    def test_a_delivery_failure_never_undoes_the_booking(self):
        receipt = _fn(BFS_SOURCE, "_send_booking_receipt")
        self.assertIn("except Exception", receipt)
        self.assertNotIn("rollback", receipt)
        self.assertNotIn("db.delete", receipt)

    def test_book_end_10_a_normal_booking_is_not_a_rescue(self):
        """BOOK-END-10 — needs_human on a booking means 'operator must approve', which is
        the existing booking semantics; the receipt must not add rescue wording."""
        msg = build_booking_receipt_message("Lara", "sábado 12 de septiembre", "13:30").lower()
        for rescue_phrase in ("alternativa que te sirva", "vamos a intentar", "julián"):
            self.assertNotIn(rescue_phrase, msg)

    def test_book_end_09_the_rescue_message_stays_distinct(self):
        """BOOK-END-09 — two different states, two different messages."""
        escalation = _fn(CE_SOURCE, "_handle_scheduling_escalation")
        self.assertIn("Julián", escalation)
        self.assertNotIn("Recibimos tu solicitud", escalation)
        receipt = build_booking_receipt_message("Lara", "sábado 12", "13:30")
        self.assertNotIn("Julián", receipt)


class TestOneCanonicalWording(unittest.TestCase):

    def test_ce_and_the_flow_service_share_a_single_builder(self):
        """The wording lived in CE and was stranded; both paths now call one function,
        so they cannot drift into competing copy."""
        self.assertIn("from .booking_flow_service import build_booking_receipt_message",
                      CE_SOURCE)
        self.assertIn("build_booking_receipt_message(", CE_SOURCE)
        # the literal opener must exist in exactly one place
        self.assertEqual(CE_SOURCE.count("Recibimos tu solicitud"), 0,
                         "CE must not carry its own copy of the wording")
        self.assertEqual(BFS_SOURCE.count("Un asesor va a revisar los datos"), 1)


class TestRootCauseIsRecorded(unittest.TestCase):
    """The guard that caused the silence is intentional and stays intact."""

    def test_the_human_guard_still_precedes_flow_response_routing(self):
        handler = CE_SOURCE[CE_SOURCE.index("# Human takeover check"):
                            CE_SOURCE.index("# Text path:")]
        self.assertIn("if state.needs_human:", handler)
        self.assertIn('return _out("skipped_human")', handler)
        i_guard = handler.index("skipped_human")
        i_flow = handler.index("_process_flow_response")
        self.assertLess(i_guard, i_flow,
                        "loosening this would let automation resume after a real handoff")


class TestScopeDedup(unittest.TestCase):

    def _satisfied(self, text: str) -> bool:
        return any(re.search(p, _norm_lower(text))
                   for p in _FAQ_TOPIC_SATISFIED["service_scope"])

    def test_scope_dedup_01_the_exact_live_wild_prose_satisfies_scope(self):
        """SCOPE-DEDUP-01 — the sentence that slipped through, verbatim."""
        self.assertTrue(self._satisfied(
            "Revisamos el auto en el lugar donde está y al finalizar, te enviamos un "
            "informe detallado con más de 250 puntos de control."))

    def test_scope_dedup_02_paraphrases_satisfy_it_too(self):
        """SCOPE-DEDUP-02 — meaning, not a vocabulary list. The previous attempt
        enumerated verbs and the model simply used a different one."""
        for prose in (
            "Vamos hasta donde está el vehículo y hacemos la revisión en el lugar.",
            "Nos acercamos a donde esté el auto para hacer la inspección.",
            "Hacemos la revisión en el lugar donde está el vehículo.",
            "La inspección se hace a domicilio.",
            "Vamos hasta donde tengas el auto.",
            "Inspeccionamos el vehículo en el lugar donde se encuentra.",
        ):
            with self.subTest(prose=prose):
                self.assertTrue(self._satisfied(prose), prose)

    def test_scope_dedup_03_unrelated_prose_does_not_satisfy_it(self):
        """SCOPE-DEDUP-03 — dedup must never become silence."""
        for prose in ("Hola, ¿en qué zona está el auto?",
                      "El informe tiene más de 250 puntos de control.",
                      "¿Tenés otro día preferido?",
                      "La cotización es de $240.000."):
            with self.subTest(prose=prose):
                self.assertFalse(self._satisfied(prose), prose)

    def test_scope_dedup_05_no_duplicate_paragraph_on_the_live_reply(self):
        """SCOPE-DEDUP-05 — end to end through the real composer."""
        eng = ConversationEngine.__new__(ConversationEngine)
        eng._turn_ctx = None
        eng._contributing_sources = None
        eng._semantic_faq_topics = lambda: {"service_scope"}
        eng._turn_scheduling_requested = False
        primary = ("¡Genial! Sí, hacemos revisiones de vehículos como el Peugeot 2008 del "
                   "2014. Revisamos el auto en el lugar donde está y al finalizar, te "
                   "enviamos un informe detallado con más de 250 puntos de control.")
        out = eng._compose_secondary_answers(primary, "¿en qué consiste el servicio?")
        self.assertNotIn(_FAQ_SCOPE_ANSWER, out)
        self.assertEqual(out.lower().count("en el lugar"), 1)

    def test_scope_dedup_04_a_wrong_scope_claim_is_still_overridden(self):
        """SCOPE-DEDUP-04 — satisfied must not swallow contradictions. Presence is the
        canonical example and must keep winning."""
        eng = ConversationEngine.__new__(ConversationEngine)
        eng._turn_ctx = None
        eng._contributing_sources = None
        eng._semantic_faq_topics = lambda: set()
        eng._turn_scheduling_requested = False
        wrong = ("Revisamos el auto en el lugar donde está y no estaremos presentes "
                 "durante el proceso.")
        out = eng._compose_secondary_answers(wrong, "¿tengo que estar presente?")
        self.assertNotIn("no estaremos presentes", out.lower())
        self.assertIn("No es necesario que estés presente", out)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
