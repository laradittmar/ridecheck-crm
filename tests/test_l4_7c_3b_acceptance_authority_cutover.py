"""L4.7C.3B — quote-acceptance authority cutover.

The predicate proven in C3A now decides. A language match no longer advances commercial
state on its own; with the flag off, the legacy behaviour returns exactly.

CUT-01 affirmative "Sí" → ALLOW           CUT-11 prior-cycle acceptance blocked
CUT-02 unaccented affirmative "Si" → ALLOW CUT-12 computed-not-delivered blocked
CUT-03 conditional "si" → HOLD/DENY        CUT-13 FUTURE_INTENT blocked
CUT-04 legacy "Bueno…" false positive safe  CUT-14 HESITATE blocked
CUT-05 single canonical acceptance path     CUT-15 QUESTION_ONLY blocked
CUT-06 legacy helper cannot progress alone  CUT-16 confidence irrelevant
CUT-07 flag OFF rollback                    CUT-17 acceptance enters scheduling only
CUT-08 stale candidate blocked              CUT-18 acceptance cannot book
CUT-09 stale location blocked               CUT-19 justification recorded
CUT-10 recomputed quote blocked             CUT-20 L4.7D uses authorized state
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

for _mod in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = sqlalchemy.JSON      # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON         # type: ignore[attr-defined]

from app.schemas.claims import (  # noqa: E402
    ClaimEvidence,
    ClaimType,
    EvidenceClass,
    Explicitness,
    Modality,
    Polarity,
    Temporality,
)
from app.schemas.turn_evidence import (  # noqa: E402
    AcceptanceEvidence,
    AcceptanceSignal,
    EvidenceStatus,
    TurnEvidence,
)
from app.services.acceptance_authorizer import (  # noqa: E402
    ALLOW,
    CLARIFY,
    DENY,
    HOLD,
    CommercialState,
    authorize_quote_acceptance,
    authorize_scheduling_progression,
)
from app.services.claim_projection import claims_from_turn_evidence, turn_modality  # noqa: E402
from app.services.conversation_engine import _has_acceptance_word, _is_acceptance  # noqa: E402

CE_SOURCE = (ROOT / "backend" / "app" / "services" / "conversation_engine.py").read_text()
CYCLE = "cycle-live"


def state(**kw):
    base = dict(cycle_id=CYCLE, revision_id=5, candidate_id=11, quote_total=95000,
                quote_tipo_vehiculo="AUTO", quote_zone_group="Sur",
                quote_zone_detail="Berazategui", quote_candidate_id=11, quote_cycle_id=CYCLE,
                current_tipo_vehiculo="AUTO", current_zone_group="Sur",
                current_zone_detail="Berazategui", delivered_amounts=(95000,),
                quote_delivered=True, lead_flag="PRESUPUESTO_ENVIADO", stage="QUOTED")
    base.update(kw)
    return CommercialState(**base)


def claims_for(text, signal=AcceptanceSignal.ACCEPT, cycle_id=CYCLE):
    """Project a real turn: the interpreter's stance plus this turn's grammar."""
    evidence = TurnEvidence(acceptance=AcceptanceEvidence(
        signal=signal, value=(signal is AcceptanceSignal.ACCEPT),
        status=EvidenceStatus.CONFIRMED)) if signal else TurnEvidence()
    return claims_from_turn_evidence(evidence, texts=[text], cycle_id=cycle_id)


# ── the unaccented-si invariant ───────────────────────────────────────────────

class TestSiInvariant(unittest.TestCase):

    def test_cut_01_accented_si_allows(self):
        decision = authorize_quote_acceptance(claims_for("Sí, avancemos"), state())
        self.assertEqual(decision.result, ALLOW, decision.reason)

    def test_cut_02_unaccented_affirmative_si_allows(self):
        """"Si avancemos" is agreement written without the accent, not a condition."""
        for text in ("Si avancemos", "si dale", "si coordinemos"):
            temporality, modality = turn_modality([text])
            self.assertEqual(modality, Modality.FACTUAL, text)
            self.assertEqual(temporality, Temporality.PRESENT, text)
            self.assertEqual(authorize_quote_acceptance(claims_for(text), state()).result,
                             ALLOW, text)

    def test_cut_03_conditional_si_never_allows(self):
        """A conditional needs a consequence — and a consequence never authorises."""
        for text in ("si me cierra te hablo", "si puedo te aviso",
                     "si consigo el auto avanzamos"):
            _, modality = turn_modality([text])
            self.assertEqual(modality, Modality.CONDITIONAL, text)
            self.assertNotEqual(authorize_quote_acceptance(claims_for(text), state()).result,
                                ALLOW, text)

    def test_the_invariant_is_grammatical_not_a_phrase_list(self):
        """Naming an example in prose is documentation; a phrase in code is a patch."""
        source = (ROOT / "backend" / "app" / "services" / "claim_projection.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    body.pop(0)
        code = ast.unparse(tree).lower()
        for phrase in ("si avancemos", "si dale", "si coordinemos", "avancemos", "bueno"):
            self.assertNotIn(phrase, code,
                             f"phrase patch {phrase!r} must not appear in executable code")


# ── the legacy false positive ─────────────────────────────────────────────────

class TestLegacyFalsePositive(unittest.TestCase):

    GREETING = "Hola, ¿cómo están? Bueno, quería revisar una 2008 del 2014"

    def test_cut_04_bueno_greeting_is_not_acceptance(self):
        """The legacy word guard fires on the discourse marker; the authorizer does not."""
        self.assertTrue(_has_acceptance_word([self.GREETING]),
                        "this is the legacy behaviour being corrected")
        decision = authorize_quote_acceptance(
            claims_for(self.GREETING, signal=None), state())
        self.assertNotEqual(decision.result, ALLOW)
        self.assertIsNone(decision.stance, "a greeting carries no stance at all")

    def test_the_fix_is_structural_not_a_phrase_block(self):
        authorizer = (ROOT / "backend" / "app" / "services"
                      / "acceptance_authorizer.py").read_text().lower()
        for phrase in ("bueno", "buenísimo", "gracias", "dale"):
            self.assertNotIn(f'"{phrase}"', authorizer,
                             "the authorizer must not carry a phrase blocklist")


# ── the write path ────────────────────────────────────────────────────────────

class TestWritePath(unittest.TestCase):

    def test_cut_05_single_canonical_acceptance_path(self):
        """Every QUOTED progression site is behind the authority check."""
        tree = ast.parse(CE_SOURCE)
        gated = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (isinstance(target, ast.Attribute) and target.attr == "flag"
                        and isinstance(node.value, ast.Constant)
                        and node.value.value == "ACEPTADO"):
                    gated += 1
        self.assertGreaterEqual(gated, 4, "the ACEPTADO write sites are still enumerable")
        # the QUOTED branch progression sites all consult the authorization result
        self.assertIn("_progression_allowed = (_sched_auth is None or _sched_auth.allows)",
                      CE_SOURCE)
        for guarded in ("if len(sched_requests) >= 2 and _progression_allowed:",
                        "if sched_day_iso and sched_time_str and _progression_allowed:",
                        "elif sched_day_iso and _progression_allowed:"):
            self.assertIn(guarded, CE_SOURCE)

    def test_cut_06_legacy_helper_cannot_progress_alone(self):
        """`_is_acceptance` matching is necessary but no longer sufficient."""
        self.assertIn("if not self._acceptance_authority_on():\n"
                      "                return self._handle_quoted_acceptance(ctx, state)",
                      CE_SOURCE)
        self.assertIn("_auth = self._authorize_acceptance(ctx, state, ai_input_messages)",
                      CE_SOURCE)
        self.assertIn("if _auth.allows:", CE_SOURCE)
        # and the weaker word guard is gated too
        self.assertIn("and _has_acceptance_word(ai_input_messages)\n"
                      "                     and (not self._acceptance_authority_on()", CE_SOURCE)

    def test_cut_07_flag_off_restores_legacy_behaviour(self):
        from app.services.conversation_engine import ConversationEngine
        engine = ConversationEngine.__new__(ConversationEngine)
        engine.settings = SimpleNamespace(reconciler_acceptance_authority_enabled=False)
        self.assertFalse(engine._acceptance_authority_on())
        engine.settings = SimpleNamespace(reconciler_acceptance_authority_enabled=True)
        self.assertTrue(engine._acceptance_authority_on())
        engine.settings = MagicMock()
        self.assertFalse(engine._acceptance_authority_on(),
                         "a test double must never enable authority")

    def test_no_mixed_mode(self):
        """With authority ON there is exactly one decision point per progression site."""
        self.assertNotIn("if state.last_stage == STAGE_QUOTED and _is_acceptance(ai_input_messages):\n"
                         "            return self._handle_quoted_acceptance(ctx, state)",
                         CE_SOURCE, "the ungated legacy call must not survive")


# ── adversarial safety, unchanged from C3A ────────────────────────────────────

class TestAdversarialSafety(unittest.TestCase):

    def assert_denied(self, decision, case):
        self.assertNotEqual(decision.result, ALLOW, f"{case}: {decision.reason}")

    def test_cut_08_stale_candidate(self):
        self.assert_denied(authorize_quote_acceptance(claims_for("Sí, avancemos"),
                                                      state(candidate_id=99)), "stale candidate")

    def test_cut_09_stale_location(self):
        self.assert_denied(authorize_quote_acceptance(claims_for("Sí, avancemos"),
                                                      state(current_zone_detail="Quilmes")),
                           "stale location")

    def test_cut_10_recomputed_quote(self):
        self.assert_denied(
            authorize_quote_acceptance(claims_for("Sí, avancemos"),
                                       state(quote_total=112000, delivered_amounts=(95000,),
                                             quote_delivered=False)),
            "recomputed quote")

    def test_cut_11_prior_cycle(self):
        self.assert_denied(authorize_quote_acceptance(claims_for("Sí, avancemos"),
                                                      state(quote_cycle_id="cycle-old")),
                           "prior-cycle quote")
        self.assert_denied(
            authorize_quote_acceptance(claims_for("Sí, avancemos", cycle_id="cycle-old"),
                                       state()), "prior-cycle acceptance")

    def test_cut_12_computed_not_delivered(self):
        self.assert_denied(
            authorize_quote_acceptance(claims_for("Sí, avancemos"),
                                       state(delivered_amounts=(), quote_delivered=False)),
            "computed but not delivered")

    def test_cut_13_future_intent(self):
        self.assert_denied(
            authorize_quote_acceptance(claims_for("después te aviso",
                                                  signal=AcceptanceSignal.FUTURE_INTENT),
                                       state()), "future intent")

    def test_cut_14_hesitate(self):
        self.assert_denied(
            authorize_quote_acceptance(claims_for("lo voy a pensar",
                                                  signal=AcceptanceSignal.HESITATE),
                                       state()), "hesitate")

    def test_cut_15_question_only(self):
        self.assert_denied(
            authorize_quote_acceptance(claims_for("¿qué incluye?",
                                                  signal=AcceptanceSignal.QUESTION_ONLY),
                                       state()), "question only")

    def test_cut_16_confidence_is_irrelevant(self):
        base = claims_for("Sí, avancemos")[0]
        low = [base.model_copy(update={"confidence": 0.01})]
        high = [base.model_copy(update={"confidence": 0.99})]
        self.assertEqual(authorize_quote_acceptance(low, state()).result,
                         authorize_quote_acceptance(high, state()).result)
        conditional = claims_for("si me cierra te hablo")[0].model_copy(
            update={"confidence": 0.99})
        self.assertNotEqual(authorize_quote_acceptance([conditional], state()).result, ALLOW)

    def test_courtesy_and_bare_ok_do_not_progress(self):
        for text in ("gracias!", "buenísimo", "ok", "dale, después veo"):
            decision = authorize_quote_acceptance(claims_for(text, signal=None), state())
            self.assert_denied(decision, text)

    def test_scheduling_progression_shares_the_prerequisites(self):
        self.assertEqual(authorize_scheduling_progression(state()).result, ALLOW)
        for kw, case in ((dict(delivered_amounts=(), quote_delivered=False), "undelivered"),
                         (dict(quote_cycle_id="cycle-old"), "prior cycle"),
                         (dict(current_zone_detail="Quilmes"), "stale zone"),
                         (dict(quote_total=None), "no quote")):
            self.assertEqual(authorize_scheduling_progression(state(**kw)).result, DENY, case)


# ── boundaries: scheduling and booking ────────────────────────────────────────

class TestBoundaries(unittest.TestCase):

    def test_cut_17_acceptance_enters_scheduling_only(self):
        """The authorized transition is flag=ACEPTADO + stage=SCHEDULING. Nothing else."""
        handler = CE_SOURCE.split("def _handle_quoted_acceptance")[1].split("\n    def ")[0]
        self.assertIn('lead.flag = "ACEPTADO"', handler)
        self.assertIn("state.last_stage = STAGE_SCHEDULING", handler)
        for forbidden in ("ThreadRevision(", "STAGE_BOOKED", "scheduled_date",
                          "_send_flow_button", "precio_total ="):
            self.assertNotIn(forbidden, handler,
                             f"acceptance must not reach {forbidden}")

    def test_cut_18_acceptance_cannot_book(self):
        authorizer = (ROOT / "backend" / "app" / "services"
                      / "acceptance_authorizer.py").read_text()
        for forbidden in ("ThreadRevision", "booking", "scheduled_date", "STAGE_BOOKED"):
            self.assertNotIn(forbidden, authorizer)
        self.assertIn("status=\"booked\"", CE_SOURCE,
                      "booking still happens only on the Flow path")

    def test_scheduling_interpretation_unchanged(self):
        """C3B gates progression; it does not read days or times differently."""
        self.assertIn("sched_day_iso, sched_time_str = _parse_scheduling_text(", CE_SOURCE)
        self.assertIn("sched_requests = _parse_scheduling_requests(", CE_SOURCE)


# ── justification and downstream ──────────────────────────────────────────────

class TestJustificationAndValidator(unittest.TestCase):

    def test_cut_19_justification_recorded(self):
        decision = authorize_quote_acceptance(claims_for("Sí, avancemos"), state())
        self.assertEqual(decision.rule_id, "authorize.quote_acceptance")
        self.assertEqual(decision.rule_version, "v1")
        self.assertEqual(decision.risk_tier, "HIGH")
        self.assertTrue(decision.quote_identity)
        self.assertIn("quote_delivered", decision.satisfied)
        self.assertIn("quote_inputs_unchanged", decision.satisfied)
        self.assertTrue(decision.evidence_ids)
        self.assertIn("_record_authorization", CE_SOURCE)
        self.assertIn("authorization_records.jsonl", CE_SOURCE)

    def test_cut_20_validator_reads_authorized_acceptance_state(self):
        """L4.7D still grounds acceptance claims in canonical state, not in the text."""
        from app.services.response_validator import CanonicalFacts, validate_response
        not_accepted = CanonicalFacts(acceptance_confirmed=False, availability_checked=False)
        blocked = validate_response("Tenemos disponibilidad el jueves a las 15.", not_accepted)
        self.assertTrue(blocked.blocked,
                        "a progression claim canonical state does not support is blocked")
        self.assertEqual([f.claim for f in blocked.blocked], ["AVAILABILITY"])

        # …and once the state supports it, the same sentence passes. `acceptance_confirmed`
        # is derived from the canonical transition the authorizer now gates, so the
        # validator consumes authorized state rather than re-reading the conversation.
        accepted = CanonicalFacts(acceptance_confirmed=True, availability_checked=True,
                                  offered_slots=("jueves 15:00",))
        self.assertEqual(validate_response("Tenemos disponibilidad el jueves a las 15.",
                                           accepted).blocked, [])
        self.assertIn("acceptance_confirmed", CE_SOURCE)

    def test_authorizer_still_writes_nothing(self):
        module = (ROOT / "backend" / "app" / "services" / "acceptance_authorizer.py").read_text()
        for writer in (".add(", ".commit(", ".flush(", "_send_", "lead.flag"):
            self.assertNotIn(writer, module)


if __name__ == "__main__":
    unittest.main()
