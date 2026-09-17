"""L4.7W5-F7C — asking for a person is an act, not a verb from a list.

The scenario-correction audit drove the three live detectors against the two sentences the
owner intended to use in the Human-Rescue Wild:

    "Ninguno de esos horarios me sirve."                       → no detector fires
    "¿Me puede ayudar una persona para coordinar otro horario?" → no detector fires

The second one misses for a reason worth naming precisely: `_HUMAN_REQUEST_PATTERNS`
enumerates *hablar, comunicar, contactar, pasar, derivar, atender* — and "ayudar" is not
there. Adding it would make that sentence pass and leave the next ordinary phrasing
failing, which is the patch the owner's standing rule forbids.

The evidence already existed. `SemanticTurnInterpreter` emits `handoff{requested,status}`,
`claim_projection` already turns it into `ClaimType.NEEDS_HUMAN`, and until now only
`shadow_reconciler` read that claim — it reached no routing decision. F7C consumes the
existing projection in the existing escalation block. One producer, one authority, no new
model call, no new prompt, no new schema, no phrase list.

Verified against the real interpreter with a realistic SCHEDULING context before writing a
line of this: "¿Me puede ayudar una persona…?" returns `handoff=(requested=True,
CONFIRMED)`. The rejection sentence returns *empty* evidence — which is why it is NOT
claimed fixed here and is recorded as a producer-side blocker instead.
"""
from __future__ import annotations

import ast
import pathlib
import sys
import unittest
from unittest.mock import MagicMock

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import app.services.conversation_engine as ce                      # noqa: E402
from app.schemas.claims import ClaimType                           # noqa: E402
from app.schemas.turn_evidence import (                            # noqa: E402
    EvidenceStatus, HandoffEvidence, TurnEvidence)
from app.services.claim_projection import claims_from_turn_evidence  # noqa: E402

from test_l4_7w5_f7a_rescue_flow_guard import _LiveTurn            # noqa: E402

CE_SRC = pathlib.Path(ce.__file__).read_text(encoding="utf-8")


def _code_only(name: str) -> str:
    """A function's source with its docstring removed — prose is not implementation."""
    fn = next(n for n in ast.walk(ast.parse(CE_SRC))
              if isinstance(n, ast.FunctionDef) and n.name == name)
    body = list(fn.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return "\n".join(ast.unparse(stmt) for stmt in body)


def evidence_with_handoff(status=EvidenceStatus.CONFIRMED, requested=True):
    return TurnEvidence(handoff=HandoffEvidence(value=requested, requested=requested,
                                                status=status))


class _Result:
    def __init__(self, evidence, ok=True):
        self.ok, self.evidence = ok, evidence


class _Provider:
    """Stands in for the model, not for the routing under test.

    `TurnSemanticEvidence` is the one dispatch the burst already made; this returns what it
    would have returned. Everything downstream — the projection, the status gate, the
    router branch, `_handle_scheduling_escalation` — runs for real.
    """
    def __init__(self, evidence, ok=True):
        self._r = _Result(evidence, ok) if evidence is not None else None
        self.calls, self.timed_out, self.gets = 1, False, 0

    def get(self, timeout=None):
        self.gets += 1
        return self._r


class _SemanticTurn(_LiveTurn):
    """The F7A live-turn harness, with the semantic provider under our control."""

    def arm(self, evidence, ok=True):
        self.eng.settings = MagicMock()
        self.eng.settings.semantic_same_turn_enabled = True
        self.eng.settings.semantic_same_turn_timeout_seconds = 1.0
        prov = _Provider(evidence, ok)
        self.eng._turn_semantic = prov
        self.eng._turn_semantic_texts = ["(burst)"]
        # `_run_shadow_understand` resets and rebinds the provider on every turn; neutralise
        # that one dispatch so ours is what the router reads. The router branch itself,
        # the projection and the escalation handler all run for real.
        self.eng._run_shadow_understand = lambda *a, **k: None
        return prov

    def neutral(self):
        """Wording no deterministic detector recognises, so only semantics can escalate."""
        return "Buenas, ¿cómo seguimos con esto?"


class TestTheMissedHumanRequestNowEscalates(_SemanticTurn):

    def test_f7c_01_semantic_handoff_escalates_on_neutral_wording(self):
        self.arm(evidence_with_handoff())
        self.turn(self.neutral())
        self.assert_rescued()

    def test_f7c_02_the_owners_sentence_reaches_the_same_path(self):
        self.arm(evidence_with_handoff())
        self.turn("¿Me puede ayudar una persona para coordinar otro horario?")
        self.assert_rescued()

    def test_f7c_03_a_paraphrase_not_taken_from_the_finding(self):
        self.arm(evidence_with_handoff())
        self.turn("Prefiero que lo vea alguien del equipo y me proponga otro día.")
        self.assert_rescued()

    def test_f7c_04_deterministic_forms_still_escalate_without_semantics(self):
        for phrase in ("No me sirve ninguno de esos horarios.",
                       "Esos horarios no me sirven.",
                       "¿Puedo hablar con una persona para coordinar otro horario?",
                       "Necesito que me atienda una persona.",
                       "Pasame con alguien por favor."):
            with self.subTest(phrase=phrase):
                self.setUp()
                prov = self.arm(None)          # no semantic evidence at all
                self.turn(phrase)
                self.assert_rescued()
                self.assertEqual(prov.gets, 0,
                                 "the deterministic floor must not wait on the model")


class TestUncertaintyInventsNothing(_SemanticTurn):

    def test_f7c_05_ambiguous_handoff_is_not_evidence(self):
        self.arm(evidence_with_handoff(status=EvidenceStatus.AMBIGUOUS))
        self.turn(self.neutral())
        self.assert_not_rescued()

    def test_f7c_06_conflicting_handoff_is_not_evidence(self):
        self.arm(evidence_with_handoff(status=EvidenceStatus.CONFLICT))
        self.turn(self.neutral())
        self.assert_not_rescued()

    def test_f7c_07_handoff_not_requested_is_not_evidence(self):
        self.arm(TurnEvidence(handoff=HandoffEvidence(value=False, requested=False)))
        self.turn(self.neutral())
        self.assert_not_rescued()

    def test_f7c_08_a_model_timeout_leaves_the_floor_in_charge(self):
        self.arm(None, ok=False)               # provider returns nothing
        self.turn(self.neutral())
        self.assert_not_rescued()

    def test_f7c_09_empty_evidence_escalates_nothing(self):
        self.arm(TurnEvidence())
        self.turn(self.neutral())
        self.assert_not_rescued()

    def test_f7c_10_an_ordinary_scheduling_question_does_not_escalate(self):
        self.arm(TurnEvidence())
        self.turn("¿El viernes a la tarde tenés algo?")
        self.assert_not_rescued()

    def test_f7c_11_an_acceptance_is_not_a_handoff(self):
        self.arm(TurnEvidence())
        self.turn("Perfecto, el viernes a las 14 me sirve.")
        self.assert_not_rescued()


class TestAuthorityBoundary(_SemanticTurn):

    def test_f7c_12_no_escalation_outside_scheduling(self):
        self.state.last_stage = "QUOTED"
        self.db.commit()
        self.arm(evidence_with_handoff())
        self.turn(self.neutral())
        self.assert_not_rescued()

    def test_f7c_13_an_already_human_owned_thread_is_not_re_escalated(self):
        self.state.needs_human = True
        self.db.commit()
        prov = self.arm(evidence_with_handoff())
        out = self.turn(self.neutral())
        self.assertEqual(out.action, "skipped_human")
        self.assertEqual(len(self.sent), 0, "no reply on a human-owned thread")
        self.assertEqual(len(self.emails), 0)
        self.assertEqual(prov.gets, 0, "the guard returns before any semantic consult")

    def test_f7c_14_escalating_withdraws_the_outstanding_flow_token(self):
        self.assertTrue(self.state.flow_booking_token)
        self.arm(evidence_with_handoff())
        self.turn(self.neutral())
        self.db.expire_all()
        self.assertIsNone(self.state.flow_booking_token,
                          "the rejected offer must not remain bookable")

    def test_f7c_15_a_second_message_after_handoff_changes_nothing(self):
        self.arm(evidence_with_handoff())
        self.turn(self.neutral(), wa_msg_id="wamid.A")
        self.assert_rescued()
        self.arm(evidence_with_handoff())
        self.turn("¿Hola? ¿Alguna novedad?", wa_msg_id="wamid.B")
        self.db.expire_all()
        self.assertEqual(len(self.sent), 1, "still exactly one customer reply")
        self.assertEqual(len(self.emails), 1, "still exactly one operator alert")
        self.assertEqual(len(self.flows), 0)


class TestEvidenceConvergence(_SemanticTurn):
    """Deterministic-only, semantic-only and both-present reach ONE escalation."""

    def _side_effects(self):
        return (len(self.sent), len(self.emails), len(self.flows))

    def test_f7c_16_deterministic_only(self):
        self.arm(None)
        self.turn("Pasame con alguien por favor.")
        self.assert_rescued()
        self.assertEqual(self._side_effects(), (1, 1, 0))

    def test_f7c_17_semantic_only(self):
        self.arm(evidence_with_handoff())
        self.turn(self.neutral())
        self.assert_rescued()
        self.assertEqual(self._side_effects(), (1, 1, 0))

    def test_f7c_18_both_in_the_same_turn_escalate_once(self):
        self.arm(evidence_with_handoff())
        self.turn("Pasame con alguien por favor.")
        self.assert_rescued()
        self.assertEqual(self._side_effects(), (1, 1, 0),
                         "two agreeing sources must not double the side effects")


class TestAdversarial(_SemanticTurn):

    def test_f7c_27_disagreement_deterministic_fires_semantic_unresolved(self):
        """The floor is consulted first, so an unresolved model reading cannot veto it."""
        self.arm(evidence_with_handoff(status=EvidenceStatus.CONFLICT))
        self.turn("Pasame con alguien por favor.")
        self.assert_rescued()
        self.assertEqual((len(self.sent), len(self.emails)), (1, 1))

    def test_f7c_28_a_deliberate_cycle_reset_still_reclaims_the_thread(self):
        """F7C changes who escalates, never who may undo it."""
        self.arm(evidence_with_handoff())
        self.turn(self.neutral())
        self.assert_rescued()
        self.state.cycle_reset_pending = True
        self.db.commit()
        self.arm(TurnEvidence())
        self.turn("Hola, quiero revisar otro auto.", wa_msg_id="wamid.C")
        self.db.expire_all()
        self.assertFalse(self.state.needs_human,
                         "the CRM cycle reset remains the one way back to automation")


class TestGovernance(unittest.TestCase):

    def test_f7c_19_the_router_consumes_the_existing_projection(self):
        src = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SRC))
                   if isinstance(n, ast.FunctionDef) and n.name == "_semantic_handoff_requested")
        self.assertIn("claims_from_turn_evidence", src)
        self.assertIn("NEEDS_HUMAN", src)
        self.assertIn("UNRESOLVED_STATUSES", src)

    def test_f7c_20_no_second_needs_human_producer(self):
        """Only claim_projection may mint a NEEDS_HUMAN claim."""
        hits = [n for n in ast.walk(ast.parse(CE_SRC))
                if isinstance(n, ast.Attribute) and n.attr == "NEEDS_HUMAN"]
        self.assertEqual(len(hits), 1,
                         "conversation_engine references NEEDS_HUMAN once, to read it")

    def test_f7c_21_projection_remains_the_only_mapping(self):
        ev = evidence_with_handoff()
        claims = claims_from_turn_evidence(ev, texts=["x"], cycle_id="c")
        self.assertEqual([c.value for c in claims if c.claim_type == ClaimType.NEEDS_HUMAN],
                         [True])

    def test_f7c_22_escalation_stays_the_sole_side_effect_authority(self):
        body = _code_only("_semantic_handoff_requested")
        for forbidden in ("needs_human =", "necesita_humano", "ATENCION_HUMANA",
                          "flow_booking_token", "_send_text_to_wa", "db.commit"):
            self.assertNotIn(forbidden, body,
                             "the reader must not mutate; escalation owns every effect")

    def test_f7c_23_no_new_phrase_lexicon(self):
        """Code only — a docstring explaining the fix is not a lexicon."""
        body = _code_only("_semantic_handoff_requested")
        self.assertNotIn("re.search", body)
        self.assertNotIn("re.match", body)
        self.assertNotIn("frozenset", body)
        customer_words = [n.value for n in ast.walk(ast.parse(body))
                          if isinstance(n, ast.Constant) and isinstance(n.value, str)
                          and " " in n.value and "%s" not in n.value]
        self.assertEqual(customer_words, [], "no customer-language literals may appear")

    def test_f7c_24_no_extra_model_call_is_requested(self):
        body = _code_only("_semantic_handoff_requested")
        self.assertIn("_semantic_turn_evidence", body)
        for forbidden in ("SemanticTurnInterpreter", "interpret(", "TurnSemanticEvidence"):
            self.assertNotIn(forbidden, body)


class TestSameTurnEvidenceOnly(unittest.TestCase):
    """The claim read by the router belongs to the burst being routed."""

    def test_f7c_25_the_provider_is_reset_every_turn(self):
        fn = next(n for n in ast.walk(ast.parse(CE_SRC))
                  if isinstance(n, ast.FunctionDef) and n.name == "_run_shadow_understand")
        body = ast.unparse(fn)
        self.assertIn("self._turn_semantic = None", body,
                      "a prior turn's provider must not survive into this one")
        self.assertIn("burst_id", body, "the provider is bound to this burst")

    def test_f7c_26_the_reader_reads_only_that_provider(self):
        fn = next(n for n in ast.walk(ast.parse(CE_SRC))
                  if isinstance(n, ast.FunctionDef) and n.name == "_semantic_turn_evidence")
        self.assertIn("self, '_turn_semantic'", ast.unparse(fn).replace('"', "'"))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
