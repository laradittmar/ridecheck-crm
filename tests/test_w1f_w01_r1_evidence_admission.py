"""W1F-W-01 R1 — the evidence admission boundary.

The Wild's failure, in the review's words: **authority is positional, not evidential.**
The bot asked where the vehicle was, the customer answered `en flores`, the interpreter
read `INSPECTION_LOCATION = Flores` CONFIRMED — and the turn exited at Layer F step 9,
eighteen lines before the only deterministic location writer and thirty-four before the
only semantic location consumer. The locality was never persisted and the same question was
asked three times in one conversation.

R1 changes an ORDER, not a routing rule: this turn's locality evidence is decided at a
typed boundary before the first gate can answer, and written through the chokepoint that
already owns candidate fields. This module proves the boundary, in two layers:

  * `TestR1A_Boundary` drives `evidence_admission.admit` directly — the eligibility rules,
    the refusal codes and the provenance, with no engine and no database;
  * every other group runs the COMPLETE turn through `ConversationEngine.handle`, so the
    gate chain, the routing gate, the one clarification decision, the attempt store and
    PricingService all execute. Only two things are simulated, and they are the two that
    must never be real in a test: the reply model's HTTP call (patched at
    `urllib.request.urlopen`) and transport (`_send_whatsapp_cloud_*` captured,
    `OutboundSafetyGate` mocked, so no `whatsapp_messages` row is written anywhere).

What R1 does NOT do is asserted here too, by `TestR1E_NotR2`: no outstanding question is
persisted, so Layer F step 9 can still answer an answer and the Wild's turn 2 still
receives the qualification greeting. The locality now survives that greeting, which is the
whole of R1. The greeting itself is R2, and a test that pretended otherwise would be the
falsified evidence the milestone forbids.

The semantic interpreter is injected by patching `_semantic_turn_evidence` — the one
accessor every consumer reads — so these tests state what the model said and then measure
only what the engine did with it. No model is called: `_turn_semantic_calls` is asserted to
stay at zero.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

#: The certified full-turn harness, loaded as a module rather than copied. It owns the
#: SQLite engine, the `app.db` stub, the catalogue fixture, the thread seeder, the engine
#: builder and the turn driver, and it arms `OUTBOUND_ENABLED` per turn with `patch.dict`
#: — never at module scope, which is the instrument fault the R3 owner review measured.
_HARNESS_PATH = Path(__file__).resolve().parent / "test_w1f_w01_r3_r1_fullturn_ambiguity.py"
_spec = importlib.util.spec_from_file_location("_r1_fullturn_harness", _HARNESS_PATH)
H = importlib.util.module_from_spec(_spec)
sys.modules["_r1_fullturn_harness"] = H
_spec.loader.exec_module(H)

from app.models import WhatsAppThreadCandidate, WhatsAppThreadState      # noqa: E402
from app.schemas.claims import (ClaimEvidence, ClaimType, EvidenceClass,  # noqa: E402
                                Explicitness, Polarity, ReconciliationOutcome)
from app.schemas.turn_evidence import (EvidenceStatus, LocationEvidence,  # noqa: E402
                                       LocationRole, TurnEvidence, TurnRef)
from app.services import evidence_admission as EA                         # noqa: E402
from app.services.conversation_engine import ConversationEngine           # noqa: E402

CE_SOURCE = (BACKEND_DIR / "app" / "services" / "conversation_engine.py").read_text(
    encoding="utf-8")

_GREETING_MARK = "nos especializamos en revisiones pre-compra"


# ── the leaf's inputs, built by hand so each rule is readable ───────────────────
def _claim(value="Flores", *, claim_type=ClaimType.INSPECTION_LOCATION,
           evidence_class=EvidenceClass.EXPLICIT_CUSTOMER, status=EvidenceStatus.CONFIRMED,
           polarity=Polarity.ASSERTED, cycle_id="7", producer="semantic:understand/1.20",
           message_ids=("wamid.1",)):
    return ClaimEvidence(
        claim_type=claim_type, value=value, evidence_class=evidence_class, status=status,
        polarity=polarity, producer=producer, producer_version="1.20",
        explicitness=Explicitness.STATED, cycle_id=cycle_id,
        source_message_ids=tuple(message_ids)).with_id()


def _ok_validator(value):
    return EA.VALIDATOR_OK, ("CABA", "Flores"), "catalogue locality"


def _admit(claims, **kw):
    kw.setdefault("cycle_id", "7")
    kw.setdefault("turn_id", "corr-1")
    kw.setdefault("burst_fingerprint", "bf-1")
    kw.setdefault("turn_message_ids", ("wamid.1",))
    kw.setdefault("locality_validator", _ok_validator)
    return EA.admit(claims, **kw)


# ── the complete turn ───────────────────────────────────────────────────────────
def _evidence(*mentions, interpreter="semantic:understand/1.20", message_ids=()):
    """One interpretation of one burst, with the location mentions the test states."""
    return TurnEvidence(
        interpreter=interpreter, model_version="1.20",
        turn=TurnRef(ordered_message_ids=tuple(message_ids)),
        location_mentions=tuple(mentions))


def _loc(locality, role=LocationRole.INSPECTION_LOCATION.value,
         status=EvidenceStatus.CONFIRMED):
    return LocationEvidence(locality=locality, role=role, status=status)


class _TurnCase:
    """One complete turn, with the admission record it produced."""

    def __init__(self, turn, admission, other_candidate=None):
        self.turn = turn
        self.admission = admission
        self.other_candidate = other_candidate

    @property
    def records(self):
        return tuple(self.admission.records) if self.admission is not None else ()

    def record_for(self, claim_type=ClaimType.INSPECTION_LOCATION):
        for record in self.records:
            if record.claim_type == claim_type:
                return record
        return None

    def comparable(self):
        """Everything a customer or an operator could observe, as one value.

        Used for the ON/OFF identity assertions: the reply, the Flow, both zones, stage,
        escalation, the route, every pricing call and every durable attempt. If any of
        these differed, the flag would not be byte-compatible in the sense the programme
        uses the word.
        """
        t = self.turn
        return (tuple(t.sent), tuple(f["screen"] for f in t.flows),
                t.thread_zone, t.cand_zone, t.stage, t.needs_human, t.lead_needs_human,
                t.route, t.reason, tuple(json.dumps(p, sort_keys=True) for p in t.pricing),
                tuple(t.attempts), t.revisions)


def _turn(text, *, admission, evidence=None, vehicle=False, zone=None,
          stage=None, flag=None, mentioned=False, safety=True):
    stage = stage or H.STAGE_QUALIFYING
    db = H._SessionLocal()
    try:
        H._seed_catalogue(db)
        thread_id, cand_id, wa = H._seed_thread(
            db, vehicle=vehicle, zone=zone, stage=stage, flag=flag)
        other_id = None
        if mentioned:
            other = WhatsAppThreadCandidate(
                thread_id=thread_id, marca="Honda", modelo="CB500", anio=2020,
                tipo_vehiculo="MOTO", status="mentioned")
            db.add(other)
            db.commit()
            other_id = other.id
        engine = H._engine(db, safety=safety)
        # OC-R2 CORRECTION 2: R1 behaviour is now behind the THREE-flag interlock
        # (`_integrated_features_armed`), because the R2 closeout measured that R1 ON with
        # R2 OFF admits `la boca` out of `me mordí la boca`. "Admission armed" therefore
        # means the integrated posture, and this module arms it as one switch. The R3 flag
        # is `safety`, which `_engine` already sets; `TestR1D_FlagOff` and the R2 module's
        # eight-row matrix are where the partial postures are proven inert.
        engine.settings.evidence_admission_enabled = admission
        engine.settings.outstanding_question_enabled = admission
        if evidence is not None:
            with patch.object(ConversationEngine, "_semantic_turn_evidence",
                              return_value=evidence):
                turn = H._run(db, engine, thread_id, wa, text, cand_id=cand_id)
        else:
            turn = H._run(db, engine, thread_id, wa, text, cand_id=cand_id)
        other_zone = None
        if other_id is not None:
            db.expire_all()
            other = db.query(WhatsAppThreadCandidate).filter_by(id=other_id).one()
            other_zone = (other.zone_group, other.zone_detail)
        return _TurnCase(turn, getattr(engine, "_turn_admission", None), other_zone)
    finally:
        db.close()


def _both(text, **kw):
    """The same input with the flag OFF and ON. Returns (off, on)."""
    kw.pop("admission", None)
    return _turn(text, admission=False, **kw), _turn(text, admission=True, **kw)


# ══════════════════════════════════════════════════════════════════════════════
class TestR1A_Boundary(unittest.TestCase):
    """The boundary itself: what may cross, what may not, and why — no engine, no DB."""

    def test_a01_the_admitted_family_is_the_locality_and_nothing_else(self):
        self.assertEqual(EA.ADMITTED_CLAIM_TYPES,
                         frozenset({ClaimType.INSPECTION_LOCATION}))

    def test_a02_no_business_family_can_be_admitted_here(self):
        """Invariant 6 of the review, as a set operation rather than a hope.

        There is no price, slot, offer or booking claim type — the claim space deliberately
        has none — so the strongest statement available is that every family that COULD
        advance commercial state is named in the refusal set and absent from the admitted
        one."""
        business = {ClaimType.QUOTE_ACCEPTED, ClaimType.QUOTE_REQUEST,
                    ClaimType.SCHEDULING_PREFERENCE, ClaimType.NEEDS_HUMAN,
                    ClaimType.SERVICE_INTENT, ClaimType.FUTURE_INTENT}
        for family in sorted(business):
            with self.subTest(family=family):
                self.assertIn(family, EA.REFUSED_CLAIM_TYPES)
                self.assertNotIn(family, EA.ADMITTED_CLAIM_TYPES)
        declared = {v for k, v in vars(ClaimType).items() if not k.startswith("_")
                    and isinstance(v, str)}
        self.assertFalse({c for c in declared if any(
            w in c for w in ("price", "precio", "slot", "offer", "booking"))},
            "a price/slot/offer/booking claim type would need its own authority, "
            "not this boundary")

    def test_a03_a_current_turn_locality_crosses_the_boundary(self):
        result = _admit([_claim("Flores")])
        self.assertEqual(len(result.admitted), 1)
        record = result.admitted[0]
        self.assertEqual(record.outcome, ReconciliationOutcome.ACCEPT.value)
        self.assertEqual(record.reason, EA.AdmissionReason.ADMITTED)
        self.assertEqual((record.zone_group, record.zone_detail), ("CABA", "Flores"))

    def test_a04_provenance_names_the_source_and_the_originating_turn(self):
        result = _admit([_claim("Flores", message_ids=("wamid.1", "wamid.2"))])
        prov = result.admitted[0].provenance
        self.assertEqual(prov.producer, "semantic:understand/1.20")
        self.assertEqual(prov.producer_version, "1.20")
        self.assertEqual(prov.evidence_class, EvidenceClass.EXPLICIT_CUSTOMER.value)
        self.assertEqual(prov.status, EvidenceStatus.CONFIRMED.value)
        self.assertEqual(prov.cycle_id, "7")
        self.assertEqual(prov.turn_id, "corr-1")
        self.assertEqual(prov.burst_fingerprint, "bf-1")
        self.assertEqual(prov.source_message_ids, ("wamid.1", "wamid.2"))
        self.assertTrue(prov.claim_id)
        self.assertTrue(prov.is_from_interpreter)

    def test_a05_a_claim_from_another_cycle_is_refused(self):
        result = _admit([_claim("Flores", cycle_id="6")])
        self.assertEqual(result.admitted, ())
        self.assertEqual(result.refused[0].reason, EA.AdmissionReason.STALE_CYCLE)

    def test_a06_a_reading_that_names_no_message_of_this_turn_is_refused(self):
        result = _admit([_claim("Flores", message_ids=("wamid.OLD",))])
        self.assertEqual(result.admitted, ())
        self.assertEqual(result.refused[0].reason, EA.AdmissionReason.STALE_TURN)

    def test_a07_an_interpretation_not_bound_to_this_burst_is_refused(self):
        """G3-5C fail-closed, applied to admission. Note the class is EXPLICIT_CUSTOMER:
        the rule follows the PRODUCER, because the projection promotes a verbatim mention
        and the strongest semantic claims would otherwise escape the binding."""
        result = _admit([_claim("Flores")], semantic_binding_ok=False)
        self.assertEqual(result.admitted, ())
        self.assertEqual(result.refused[0].reason, EA.AdmissionReason.STALE_BURST)

    def test_a08_a_denial_is_not_an_assertion(self):
        result = _admit([_claim("Flores", polarity=Polarity.NEGATED)])
        self.assertEqual(result.refused[0].reason, EA.AdmissionReason.NEGATED)

    def test_a09_only_a_confirmed_reading_may_become_truth(self):
        for status in (EvidenceStatus.PROPOSED, EvidenceStatus.AMBIGUOUS,
                       EvidenceStatus.CONFLICT):
            with self.subTest(status=status):
                result = _admit([_claim("Flores", status=status)])
                self.assertEqual(result.admitted, ())
                self.assertEqual(result.refused[0].reason,
                                 EA.AdmissionReason.UNCONFIRMED_STATUS)

    def test_a10_a_claim_with_no_value_is_refused(self):
        for empty in (None, "", [], {}):
            with self.subTest(value=empty):
                result = _admit([_claim(empty)])
                self.assertEqual(result.refused[0].reason, EA.AdmissionReason.NO_VALUE)

    def test_a11_an_ambiguous_locality_is_never_admitted_as_truth(self):
        result = _admit([_claim("San Nicolás")], locality_validator=lambda v: (
            EA.VALIDATOR_AMBIGUOUS, None, "CABA barrio vs the province city"))
        self.assertEqual(result.admitted, ())
        record = result.refused[0]
        self.assertEqual(record.outcome, ReconciliationOutcome.CLARIFY.value)
        self.assertEqual(record.reason, EA.AdmissionReason.LOCALITY_AMBIGUOUS)
        self.assertIsNone(record.normalized)

    def test_a12_an_unsupported_locality_is_never_admitted(self):
        result = _admit([_claim("Mar del Plata")], locality_validator=lambda v: (
            EA.VALIDATOR_UNSUPPORTED, None, "declared outside the coverage area"))
        self.assertEqual(result.admitted, ())
        self.assertEqual(result.refused[0].outcome, ReconciliationOutcome.CLARIFY.value)
        self.assertEqual(result.refused[0].reason, EA.AdmissionReason.LOCALITY_UNSUPPORTED)

    def test_a13_an_unresolvable_locality_is_held(self):
        result = _admit([_claim("Quilmesss")], locality_validator=lambda v: (
            EA.VALIDATOR_UNRESOLVED, None, "no catalogue locality"))
        self.assertEqual(result.refused[0].outcome, ReconciliationOutcome.HOLD.value)
        self.assertEqual(result.refused[0].reason, EA.AdmissionReason.LOCALITY_UNRESOLVED)

    def test_a14_a_broken_validator_refuses_instead_of_raising(self):
        def _boom(value):
            raise RuntimeError("catalogue unavailable")
        result = _admit([_claim("Flores")], locality_validator=_boom)
        self.assertEqual(result.admitted, ())
        self.assertEqual(result.refused[0].reason, EA.AdmissionReason.LOCALITY_UNRESOLVED)
        self.assertIn("RuntimeError", result.refused[0].detail)

    def test_a15_an_established_value_is_not_overwritten(self):
        result = _admit([_claim("Flores")], canonical_locality=("CABA", "Palermo"))
        self.assertEqual(result.admitted, ())
        self.assertEqual(result.refused[0].reason,
                         EA.AdmissionReason.CONFIRMED_VALUE_PRESENT)

    def test_a16_the_customers_own_words_outrank_a_reading_of_them(self):
        result = _admit([_claim("Palermo")], canonical_locality=("CABA", "Flores"),
                        canonical_from_this_turn=True,
                        locality_validator=lambda v: (EA.VALIDATOR_OK, ("CABA", v), "ok"))
        self.assertEqual(result.admitted, ())
        self.assertEqual(result.refused[0].reason,
                         EA.AdmissionReason.OUTRANKED_BY_EXPLICIT)
        self.assertIn("Flores", result.refused[0].detail)

    def test_a16b_an_inferred_locality_is_not_admitted_at_all(self):
        """Model prose is evidence, not authority. A locality whose words are nowhere in
        what the customer wrote stays a proposal for `_recover_locality` to offer."""
        for weak in (EvidenceClass.SEMANTIC_INFERRED, EvidenceClass.FUZZY_SUGGESTED,
                     EvidenceClass.SERVICE_COMPUTED):
            with self.subTest(evidence_class=weak):
                result = _admit([_claim("Flores", evidence_class=weak)])
                self.assertEqual(result.admitted, ())
                self.assertEqual(result.refused[0].reason,
                                 EA.AdmissionReason.INFERRED_NOT_EXPLICIT)

    def test_a16c_the_admissible_classes_are_the_customers_words_and_stronger(self):
        self.assertEqual(EA.ADMISSIBLE_EVIDENCE_CLASSES, frozenset({
            EvidenceClass.HUMAN_CONFIRMED.value, EvidenceClass.EXPLICIT_CUSTOMER.value,
            EvidenceClass.DETERMINISTIC_EXTRACTED.value,
            EvidenceClass.CATALOG_CONFIRMED.value}))
        self.assertNotIn(EvidenceClass.SEMANTIC_INFERRED.value,
                         EA.ADMISSIBLE_EVIDENCE_CLASSES)

    def test_a16d_the_text_decides_the_role(self):
        result = _admit([_claim("Flores")], role_decided_by_text=True)
        self.assertEqual(result.admitted, ())
        self.assertEqual(result.refused[0].reason,
                         EA.AdmissionReason.ROLE_DECIDED_BY_TEXT)

    def test_a17_an_agreeing_claim_is_still_admitted(self):
        result = _admit([_claim("Flores")], canonical_locality=("CABA", "Flores"),
                        canonical_from_this_turn=True)
        self.assertEqual(len(result.admitted), 1)

    def test_a18_two_readings_of_equal_strength_that_disagree_are_a_question(self):
        def _validate(value):
            return EA.VALIDATOR_OK, ("CABA", value), "catalogue locality"
        result = _admit([_claim("Flores"), _claim("Palermo")],
                        locality_validator=_validate)
        self.assertEqual(result.admitted, ())
        self.assertEqual({r.reason for r in result.refused},
                         {EA.AdmissionReason.COMPETING_LOCALITIES})
        self.assertEqual({r.outcome for r in result.refused},
                         {ReconciliationOutcome.CLARIFY.value})

    def test_a19_an_origin_is_never_an_inspection_location(self):
        for family in (ClaimType.CUSTOMER_ORIGIN, ClaimType.SELLER_LOCATION):
            with self.subTest(family=family):
                result = _admit([_claim("Tigre", claim_type=family)])
                self.assertEqual(result.admitted, ())
                self.assertEqual(result.refused[0].reason,
                                 EA.AdmissionReason.ROLE_NOT_INSPECTION)

    def test_a20_the_reconciler_receives_exactly_the_admitted_claims(self):
        good, stale = _claim("Flores"), _claim("Flores", cycle_id="6")
        result = _admit([stale, good])
        self.assertEqual([c.claim_id for c in result.admitted_claims], [good.claim_id])

    def test_a21_the_boundary_invents_nothing(self):
        """Every admitted value is a value some producer proposed."""
        claims = [_claim("Flores"), _claim("Tigre", claim_type=ClaimType.CUSTOMER_ORIGIN)]
        result = _admit(claims)
        proposed = {c.value for c in claims}
        for record in result.records:
            with self.subTest(claim=record.claim_type):
                self.assertIn(record.value, proposed)

    def test_a22_every_produced_claim_gets_a_decision(self):
        """A claim with no decision site produces no record of its own refusal — that is
        the invisibility the review named (G-11). Here the refusals are rows."""
        claims = [_claim("Flores"),
                  _claim("Tigre", claim_type=ClaimType.CUSTOMER_ORIGIN),
                  _claim(True, claim_type=ClaimType.QUOTE_ACCEPTED),
                  _claim("si", claim_type=ClaimType.SERVICE_INTENT)]
        result = _admit(claims)
        self.assertEqual(len(result.records), len(claims))
        self.assertEqual(len(result.admitted), 1)
        self.assertEqual(len(result.refused), 3)
        self.assertEqual(result.contract, "evidence-admission/1.0")

    def test_a23_an_empty_turn_decides_nothing_and_raises_nothing(self):
        result = _admit([])
        self.assertEqual(result.records, ())
        self.assertEqual(result.admitted_claims, ())


# ══════════════════════════════════════════════════════════════════════════════
class TestR1B_WholeTurn(unittest.TestCase):
    """The complete turn: `handle()`, the real gates, the real router, the real catalogue."""

    def test_b01_the_wild_turn_now_persists_the_locality_it_understood(self):
        """The measured failure, both postures, one assertion each.

        `en flores` exits at Layer F step 9 in both postures — that is R2's to fix — but
        with admission ON the locality the customer gave is on the thread when the turn
        ends instead of being destroyed by the early return."""
        off, on = _both("en flores")
        self.assertEqual(off.turn.thread_zone, (None, None))
        self.assertEqual(on.turn.thread_zone, ("CABA", "Flores"))

    def test_b02_the_admitted_record_names_its_source_and_its_turn(self):
        on = _turn("en flores", admission=True,
                   evidence=_evidence(_loc("Flores")))
        record = on.record_for()
        self.assertEqual(record.outcome, ReconciliationOutcome.ACCEPT.value)
        self.assertEqual(record.provenance.producer, "semantic:understand/1.20")
        self.assertTrue(record.provenance.turn_id)
        self.assertTrue(record.provenance.burst_fingerprint)
        self.assertEqual((record.zone_group, record.zone_detail), ("CABA", "Flores"))

    def test_b03_with_a_candidate_the_locality_lands_on_the_candidate(self):
        on = _turn("en flores", admission=True, vehicle=True,
                   evidence=_evidence(_loc("Flores")))
        self.assertEqual(on.turn.cand_zone, ("CABA", "Flores"))
        self.assertEqual(on.turn.attempts, [], "nothing may ask for an admitted fact")

    def test_b04_a_locality_only_the_interpreter_inferred_is_not_admitted(self):
        """The ASR case: `floress` resolves to nothing deterministically, and `Flores` is
        the interpreter's reading of a corrupted token — a confident match on a corrupted
        token is still a guess about what was said. Nothing is persisted in either posture.

        UPDATED BY OC-R2 CORRECTION 2, and the guarantee got STRONGER rather than weaker.
        R1 alone admitted these claims to the boundary and refused them there, by name
        (`INFERRED_NOT_EXPLICIT`). Under the integrated posture the R1-PL-01 predicate sees
        that the burst's own words contain no catalogue locality and no coverage city, so
        the interpreter is never consulted at all: there is no claim to refuse, and the
        assertion is now that the boundary produced NO record and wrote nothing. A reading
        that was never requested cannot be admitted by a future mistake either."""
        for mangled in ("en flore", "en florez", "en tegui"):
            with self.subTest(text=mangled):
                off, on = _both(mangled, evidence=_evidence(_loc("Flores")))
                self.assertEqual(off.turn.thread_zone, (None, None))
                self.assertEqual(on.turn.thread_zone, (None, None))
                self.assertEqual((), tuple(on.admission.records),
                                 "the interpreter was consulted on a burst with no "
                                 "resolvable locality in the customer's own words")
                self.assertTrue(
                    getattr(on.turn.engine, "_turn_semantic_wait_skipped", False))
        # `floress` is NOT one of them: the inherited matcher's compact/ASR pass reads it,
        # so that locality is deterministic evidence and admission is not what keeps it.
        _, on = _both("en floress")
        self.assertEqual(on.turn.thread_zone, ("CABA", "Flores"))

    def test_b05_explicit_words_beat_a_conflicting_reading(self):
        """The customer wrote `en flores`; the interpreter says Palermo. Two independent
        rules refuse it and the canonical value is the customer's: the reading is inferred
        (its words are not in the burst), and a value established from the customer's own
        words this turn is never overwritten."""
        on = _turn("en flores", admission=True, evidence=_evidence(_loc("Palermo")))
        self.assertEqual(on.turn.thread_zone, ("CABA", "Flores"))
        self.assertIn(on.record_for().reason,
                      {EA.AdmissionReason.INFERRED_NOT_EXPLICIT,
                       EA.AdmissionReason.OUTRANKED_BY_EXPLICIT})
        self.assertFalse(on.record_for().admitted)

    def test_b06_a_stale_reading_persists_nothing(self):
        """Two ways a stale reading is refused, and the integrated posture uses the cheaper.

        The boundary's `STALE_TURN` rule still exists and `TestR1A_Boundary::a06` proves it
        directly. Through the whole turn, `ya te lo dije` carries no locality in the
        customer's own words, so the R1-PL-01 predicate declines to consult the interpreter
        and the stale reading never reaches the boundary at all. Nothing persisted, and
        nothing asked."""
        on = _turn("ya te lo dije", admission=True,
                   evidence=_evidence(_loc("Flores"), message_ids=("wamid.OLD",)))
        self.assertEqual(on.turn.thread_zone, (None, None))
        self.assertEqual((), tuple(on.admission.records))
        self.assertTrue(getattr(on.turn.engine, "_turn_semantic_wait_skipped", False))

    def test_b07_evidence_cannot_leak_to_another_candidate(self):
        """Candidate-location isolation (M21.2.14): a `mentioned` MOTO from an earlier
        handoff is not this conversation's subject."""
        on = _turn("en flores", admission=True, vehicle=True, mentioned=True,
                   evidence=_evidence(_loc("Flores")))
        self.assertEqual(on.turn.cand_zone, ("CABA", "Flores"))
        self.assertEqual(on.other_candidate, (None, None))

    def test_b08_the_r3_ambiguity_protection_holds_through_admission(self):
        on = _turn("en san nicolas", admission=True,
                   evidence=_evidence(_loc("san nicolas")))
        self.assertEqual(on.turn.thread_zone, (None, None))
        self.assertIsNone(on.turn.cand_zone)
        record = on.record_for()
        self.assertEqual(record.outcome, ReconciliationOutcome.CLARIFY.value)
        self.assertEqual(record.reason, EA.AdmissionReason.LOCALITY_AMBIGUOUS)
        self.assertEqual(on.turn.pricing, [])

    def test_b09_an_unsupported_locality_is_not_admitted_and_is_not_priced(self):
        cases = {
            # declared outside the coverage area — R3's coverage list
            "mar del plata": (EA.AdmissionReason.LOCALITY_UNSUPPORTED,
                              ReconciliationOutcome.CLARIFY.value),
            "cordoba": (EA.AdmissionReason.LOCALITY_UNSUPPORTED,
                        ReconciliationOutcome.CLARIFY.value),
            "rosario": (EA.AdmissionReason.LOCALITY_UNSUPPORTED,
                        ReconciliationOutcome.CLARIFY.value),
            # only part of a longer connector-joined name — R3's composite rule
            "san miguel del monte": (EA.AdmissionReason.LOCALITY_UNRESOLVED,
                                     ReconciliationOutcome.HOLD.value),
        }
        for name, (reason, outcome) in cases.items():
            with self.subTest(locality=name):
                on = _turn(f"en {name}", admission=True, evidence=_evidence(_loc(name)))
                self.assertEqual(on.turn.thread_zone, (None, None))
                self.assertEqual(on.turn.pricing, [])
                # The exact code, not merely "not admitted": a refusal nobody can read is
                # the invisibility the review named, and a validator that collapsed
                # "outside the coverage area" into "unresolved" would pass a weaker test.
                self.assertEqual(on.record_for().reason, reason)
                self.assertEqual(on.record_for().outcome, outcome)

    def test_b10_an_origin_mention_writes_no_inspection_locality(self):
        on = _turn("soy de tigre", admission=True, evidence=_evidence(
            _loc("tigre", role=LocationRole.CUSTOMER_ORIGIN.value)))
        self.assertEqual(on.turn.thread_zone, (None, None))
        self.assertEqual(on.record_for(ClaimType.CUSTOMER_ORIGIN).reason,
                         EA.AdmissionReason.ROLE_NOT_INSPECTION)

    def test_b10b_a_model_role_label_cannot_overrule_the_text(self):
        """Measured while building the boundary, and the reason the rule exists.

        `soy de flores` and `estoy en flores` are turns where the role-aware reader
        correctly writes nothing: the words place the locality with the CUSTOMER. Before
        this rule, an interpreter that labelled the same mention `INSPECTION_LOCATION` had
        it written as the car's locality — the exact role collision the review calls RC-5,
        where a price and an appointment would be computed for the wrong side of the city.
        """
        for text in ("soy de flores", "estoy en flores",
                     "yo soy de flores pero el auto no"):
            with self.subTest(text=text):
                off, on = _both(text, evidence=_evidence(_loc("flores")))
                self.assertEqual(off.turn.thread_zone, (None, None))
                self.assertEqual(on.turn.thread_zone, (None, None))
                self.assertEqual(on.record_for().reason,
                                 EA.AdmissionReason.ROLE_DECIDED_BY_TEXT)

    def test_b10c_an_origin_clause_beside_a_vehicle_clause_still_admits(self):
        """The rule must not swallow the Wild's own turn-1 shape, where the customer gives
        both: the vehicle clause produces the locality and the origin clause suppresses
        only itself (LR-2/LR-3 preserved)."""
        on = _turn("soy de tigre pero el auto está en flores", admission=True,
                   evidence=_evidence(_loc("flores")))
        self.assertEqual(on.turn.thread_zone, ("CABA", "Flores"))
        self.assertEqual(on.record_for().reason, EA.AdmissionReason.ADMITTED)

    def test_b11_admission_sends_nothing_of_its_own(self):
        """The reply a customer gets is the gate chain's, unchanged in number and in text.

        Admission writes; it does not speak. If it ever sent anything, this turn would
        carry one more outbound body than the same turn without it."""
        for text, evidence in (("en flores", _evidence(_loc("Flores"))),
                               ("hola", _evidence(_loc("San Nicolás"))),
                               ("en floress", _evidence(_loc("Flores")))):
            with self.subTest(text=text):
                off, on = _both(text, evidence=evidence)
                self.assertEqual(len(on.turn.sent), len(off.turn.sent))
                self.assertEqual(on.turn.sent, off.turn.sent)
                self.assertEqual(len(on.turn.flows), len(off.turn.flows))

    def test_b12_admission_creates_no_quote_offer_or_booking(self):
        on = _turn("en flores", admission=True, evidence=_evidence(_loc("Flores")))
        self.assertEqual(on.turn.pricing, [])
        self.assertEqual(on.turn.revisions, 0)
        self.assertEqual(on.turn.stage, H.STAGE_QUALIFYING)

    def test_b13_admission_costs_no_model_call(self):
        on = _turn("en flores", admission=True, evidence=_evidence(_loc("Flores")))
        self.assertEqual(on.turn.semantic_calls, 0)

    def test_b14_a_turn_that_names_nothing_admits_nothing(self):
        on = _turn("buenas", admission=True)
        self.assertEqual(on.turn.thread_zone, (None, None))
        self.assertEqual(on.records, ())


# ══════════════════════════════════════════════════════════════════════════════
class TestR1C_Boundaries(unittest.TestCase):
    """The decisions R1 must not touch, compared turn against turn."""

    CASES = (
        ("no_intent", "hola", {}),
        ("faq_only", "¿de qué consta la revisión?", {}),
        ("faq_hours", "¿qué horarios tienen?", {}),
        ("opt_in", "sí, quiero revisar un auto", {}),
        ("vehicle_named", "es un corolla del 2024", {}),
        ("quote", "es un corolla 2024, está en Flores", {}),
        ("scheduling", "puede ser el jueves a las 10?",
         dict(vehicle=True, zone=("CABA", "Flores"), stage="QUOTED", flag="ACEPTADO")),
        ("handoff", "quiero hablar con una persona", {}),
        ("motorcycle", "tengo una moto, la revisan?", {}),
        ("unsupported_service", "hacen transferencias?", {}),
        ("coverage", "en mar del plata", {}),
        ("ambiguous", "en san nicolas", {}),
    )

    def test_c01_every_boundary_is_identical_with_the_flag_on(self):
        for name, text, kw in self.CASES:
            with self.subTest(case=name):
                off, on = _both(text, **kw)
                self.assertEqual(on.comparable(), off.comparable())

    def test_c02_the_same_boundaries_hold_when_the_interpreter_also_spoke(self):
        """Admission consumes a real interpretation on each of these turns; the outcome a
        customer sees must still be the committed one. The three localities are the R3
        cases: a supported one, an ambiguous one and an unsupported one."""
        for name, text, kw in self.CASES:
            for locality in ("Flores", "San Nicolás", "Mar del Plata"):
                with self.subTest(case=name, locality=locality):
                    evidence = _evidence(_loc(locality))
                    off, on = _both(text, evidence=evidence, **kw)
                    if locality == "Flores" and on.turn.thread_zone != off.turn.thread_zone:
                        # The one intended difference: a locality the turn understood is
                        # now kept. Everything the customer sees must still match.
                        self.assertEqual(on.turn.sent, off.turn.sent)
                        self.assertEqual(
                            tuple(f["screen"] for f in on.turn.flows),
                            tuple(f["screen"] for f in off.turn.flows))
                        continue
                    self.assertEqual(on.comparable(), off.comparable())


# ══════════════════════════════════════════════════════════════════════════════
class TestR1D_FlagOff(unittest.TestCase):
    """With the flag OFF the boundary does not exist."""

    def test_d01_no_admission_record_is_produced(self):
        for text in ("en flores", "en floress", "hola"):
            with self.subTest(text=text):
                off = _turn(text, admission=False, evidence=_evidence(_loc("Flores")))
                self.assertIsNone(off.admission)

    def test_d02_the_committed_locality_behaviour_is_unchanged(self):
        off = _turn("en flores", admission=False, evidence=_evidence(_loc("Flores")))
        self.assertEqual(off.turn.thread_zone, (None, None))
        self.assertTrue(any(_GREETING_MARK in s for s in off.turn.sent))

    def test_d03_the_flag_defaults_to_off(self):
        from app.settings import Settings, get_settings
        self.assertIs(Settings().evidence_admission_enabled, False)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EVIDENCE_ADMISSION_ENABLED", None)
            get_settings.cache_clear()
            self.assertIs(get_settings().evidence_admission_enabled, False)
        get_settings.cache_clear()

    def test_d04_only_the_literal_true_arms_it(self):
        from app.settings import get_settings
        for value, expected in (("true", True), ("True", True), ("TRUE", True),
                                ("false", False), ("1", False), ("yes", False),
                                ("", False)):
            with self.subTest(value=value):
                with patch.dict(os.environ,
                                {"EVIDENCE_ADMISSION_ENABLED": value}):
                    get_settings.cache_clear()
                    self.assertIs(get_settings().evidence_admission_enabled, expected)
        get_settings.cache_clear()

    def test_d05_a_magicmock_settings_object_never_arms_it(self):
        """Strict identity, the house rule: an attribute that is merely truthy must not
        arm a behaviour change, or every test with a mock settings object would run a
        different engine than production."""
        engine = ConversationEngine.__new__(ConversationEngine)
        engine.settings = MagicMock()
        self.assertFalse(engine._admission_on())
        self.assertFalse(engine._integrated_features_armed())


# ══════════════════════════════════════════════════════════════════════════════
class TestR1E_NotR2(unittest.TestCase):
    """What R1 does not do. These assertions are the honest half of the milestone."""

    def test_e01_r1_alone_is_inert_under_the_interlock(self):
        """R1 ALONE DOES NOTHING NOW, and that is the owner's correction, not a regression.

        This assertion has moved twice and the history is the point. It began as "the
        `whatsapp_outstanding_questions` table does not exist", which stopped being the
        invariant when R2 added the table. It became "R1 with the R2 flag off records no
        question", which was true but hid something worse: the R2 closeout then MEASURED
        that R1 ON with R2 OFF admits `la boca` out of `me mordí la boca` and writes
        `('CABA','La Boca')`.

        So OC-R2 CORRECTION 2 put R1 behind the three-flag interlock, and the invariant is
        now the strongest of the three: with only `EVIDENCE_ADMISSION_ENABLED` armed,
        **nothing happens at all** — no admission, no evidence row, no question, and the
        committed pre-R1 behaviour of the turn."""
        from app.models import WhatsAppOutstandingQuestion, WhatsAppThreadEvidence
        db = H._SessionLocal()
        try:
            H._seed_catalogue(db)
            thread_id, cand_id, wa = H._seed_thread(db, vehicle=False,
                                                    stage=H.STAGE_QUALIFYING)
            engine = H._engine(db, safety=True)
            engine.settings.evidence_admission_enabled = True
            engine.settings.outstanding_question_enabled = False
            self.assertFalse(engine._integrated_features_armed())
            self.assertFalse(engine._admission_on(), "R1 armed itself without the interlock")
            with patch.object(ConversationEngine, "_semantic_turn_evidence",
                              return_value=_evidence(_loc("Flores"))):
                turn = H._run(db, engine, thread_id, wa, "en flores", cand_id=cand_id)
            self.assertEqual((None, None), turn.thread_zone,
                             "a partial posture admitted a locality")
            self.assertIsNone(getattr(engine, "_turn_admission", None))
            self.assertEqual(0, db.query(WhatsAppOutstandingQuestion)
                             .filter_by(thread_id=thread_id).count())
            self.assertEqual(0, db.query(WhatsAppThreadEvidence)
                             .filter_by(thread_id=thread_id).count())
            self.assertEqual([], turn.attempts)
        finally:
            db.close()

    def test_e02_layer_f_step_nine_still_answers_an_unprompted_locality(self):
        """What R1 contributes, with the greeting case stated exactly as it now stands.

        R1's closeout predicted this would invert when R2 landed, and it has — but not in
        the blunt way the prediction implied. The greeting is suppressed only when the turn
        ANSWERED a question the customer was shown to have received. Here no question was
        ever asked, so `en flores` is an unprompted fragment, step 9 finds no service signal
        and greets exactly as the committed system does.

        R1's contribution is unchanged and is the other assertion: the locality survives
        that greeting instead of being destroyed by it. The three-turn sequence where the
        greeting IS suppressed lives in the R2 suite, because it needs a delivered
        question."""
        on = _turn("en flores", admission=True, evidence=_evidence(_loc("Flores")))
        self.assertTrue(any(_GREETING_MARK in s for s in on.turn.sent),
                        "an UNPROMPTED locality must still reach the greeting")
        self.assertEqual(on.turn.thread_zone, ("CABA", "Flores"))

    def test_e03_the_admission_record_claims_nothing_about_questions(self):
        record = _admit([_claim("Flores")]).admitted[0]
        for forbidden in ("question", "asked", "answers", "expected_slots"):
            with self.subTest(field=forbidden):
                self.assertNotIn(forbidden, record.as_dict())

    def test_e04_cross_turn_retention_is_not_claimed(self):
        """Evidence given before a candidate exists still does not bind to the candidate
        created in a LATER turn: that needs the typed evidence table (review §8.2) and a
        migration, which this milestone does not make. Within one turn it binds; across
        turns `_apply_candidate`'s RISK-03 guard still — correctly — refuses to inherit an
        untyped buffer value."""
        source = (BACKEND_DIR / "app" / "services" / "conversation_engine.py").read_text(
            encoding="utf-8")
        self.assertIn("FIX 1 (RISK-03): Do NOT inherit stale state.home_zone_* here.",
                      source)


# ══════════════════════════════════════════════════════════════════════════════
class TestR1F_Structural(unittest.TestCase):
    """Rules a reviewer should not have to take on trust."""

    @staticmethod
    def _function(name: str) -> ast.FunctionDef:
        tree = ast.parse(CE_SOURCE)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"{name} not found")

    def test_f01_admission_assigns_no_zone_field_itself(self):
        """Scoped honestly: the assertion is that the boundary's OWN body assigns no
        candidate or buffer zone field. It calls two writers — the deterministic zone
        reader and `_apply_inspection_zone` — and those remain the C2 chokepoints that
        R1 does not change."""
        node = self._function("_admit_turn_evidence")
        for child in ast.walk(node):
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Attribute):
                        self.assertNotIn(target.attr,
                                         {"zone_group", "zone_detail",
                                          "home_zone_group", "home_zone_detail"})

    def test_f02_admission_sends_nothing(self):
        node = self._function("_admit_turn_evidence")
        called = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                func = child.func
                name = getattr(func, "attr", None) or getattr(func, "id", None)
                if name:
                    called.add(name)
        for forbidden in ("_send_text_to_wa", "_send_whatsapp_cloud_text",
                          "_send_whatsapp_cloud_flow", "_send_coverage_response",
                          "_send_service_boundary", "attempt",
                          "_dispatch_location_clarification_flow"):
            with self.subTest(call=forbidden):
                self.assertNotIn(forbidden, called)

    def test_f03_the_deterministic_reader_is_called_in_its_write_only_mode(self):
        """`allow_contradiction_return=False` is what makes the reader unable to speak.
        Without it the coverage response and the SC17 contradiction reply would be
        produced from inside admission, before the gate chain, and twice per turn."""
        node = self._function("_admit_turn_evidence")
        found = []
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and getattr(
                    child.func, "attr", None) == "_apply_zone_from_text":
                found.append({kw.arg: getattr(kw.value, "value", None)
                              for kw in child.keywords})
        self.assertEqual(len(found), 1)
        self.assertIs(found[0].get("allow_contradiction_return"), False)

    def test_f04_the_current_focus_predicate_has_one_implementation(self):
        """W1FRV-07 is the finding about one rule in two copies. The predicate used to be
        written out three times inside the zone reader; admission is the fourth reader."""
        self.assertEqual(CE_SOURCE.count("def _is_current_focus"), 1)
        self.assertEqual(CE_SOURCE.count('_fc.status == "current_focus"'), 0)
        self.assertGreaterEqual(CE_SOURCE.count("self._is_current_focus("), 4)

    def test_f05_the_burst_binding_rule_has_one_implementation(self):
        self.assertEqual(CE_SOURCE.count("def _semantic_binding_refused"), 1)
        self.assertEqual(CE_SOURCE.count("G3-5C SEMANTIC BINDING REFUSED"), 1)

    def test_f06_the_chokepoint_still_builds_its_own_claim_for_every_other_caller(self):
        """`claims=` is additive: a caller that passes nothing gets exactly the single
        claim it got before, so the eight existing call sites are unchanged."""
        node = self._function("_apply_inspection_zone")
        defaults = {a.arg: d for a, d in zip(node.args.kwonlyargs,
                                             node.args.kw_defaults)}
        self.assertIn("claims", defaults)
        self.assertIsNone(getattr(defaults["claims"], "value", "NOT_NONE"))
        callers = CE_SOURCE.count("_apply_inspection_zone(")
        with_claims = CE_SOURCE.count("claims=list(result.admitted_claims)")
        self.assertEqual(with_claims, 1)
        self.assertGreater(callers, 2)

    def test_f07_the_admission_call_site_sits_between_layer_c_and_layer_d(self):
        """The whole of R1 is this order, and owner correction OC-R1 fixes where in it.

        Layers A/B/C are terminal — handoff, handoff, boundary statement — so admission
        sits BELOW them: none of those three turns needs the locality persisted, and
        putting the semantic bounded wait above them would couple three safety replies to
        a model. Layer D is the first gate that ends the turn while the conversation
        CONTINUES, and it is one of the two the Wild measured dropping the locality, so
        admission sits ABOVE it. One expression, read off the source."""
        marks = [
            ("dispatch", CE_SOURCE.index("self._run_shadow_understand(")),
            ("layer_a", CE_SOURCE.index("Layer A: Motorcycle pre-gate")),
            ("layer_b", CE_SOURCE.index("Layer B: Phone-call/human-request pre-gate")),
            ("layer_c", CE_SOURCE.index("Layer C: Explicit unsupported-service pre-gate")),
            ("admission", CE_SOURCE.index("self._admit_turn_evidence(ctx, state")),
            ("layer_d", CE_SOURCE.index("Layer D: FAQ bypass (all stages)")),
            ("layer_f", CE_SOURCE.index("M21.1.1 Layer F: QUALIFYING intent gate")),
        ]
        self.assertEqual([n for n, _ in sorted(marks, key=lambda m: m[1])],
                         [n for n, _ in marks],
                         f"source order is not the certified order: {marks}")
        # and each gate's own early return is still above admission, not merely its comment
        for ret in ("return self._motorcycle_human_handoff(",
                    "return self._handle_phone_call_escalation(",
                    "return _svc_out"):
            with self.subTest(gate=ret):
                self.assertLess(CE_SOURCE.index(ret),
                                CE_SOURCE.index("self._admit_turn_evidence(ctx, state"))

    def test_f08_the_call_site_is_guarded_by_the_flag(self):
        head = CE_SOURCE[:CE_SOURCE.index("self._admit_turn_evidence(ctx, state")]
        self.assertTrue(head.rstrip().endswith("if self._admission_on():"))

    def test_f09_the_boundary_is_a_leaf(self):
        """It may not import the engine, the ORM or a provider: a boundary that could
        reach the database would be a second writer."""
        source = (BACKEND_DIR / "app" / "services" / "evidence_admission.py").read_text(
            encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        for forbidden in ("conversation_engine", "sqlalchemy", "app.models",
                          "..models", "openai", "requests", "urllib"):
            with self.subTest(module=forbidden):
                self.assertFalse(any(forbidden in m for m in imported),
                                 f"{forbidden} reachable from the boundary: {imported}")

    def test_f10_the_outcome_vocabulary_is_unchanged(self):
        """Review §15.4: the Inspector's reading of a stored decision must not shift, so
        admission reuses ReconciliationOutcome and defines no outcome of its own."""
        source = (BACKEND_DIR / "app" / "services" / "evidence_admission.py").read_text(
            encoding="utf-8")
        self.assertNotIn("class AdmissionOutcome", source)
        for record in _admit([_claim("Flores"), _claim("Flores", cycle_id="6")]).records:
            with self.subTest(reason=record.reason):
                self.assertIn(record.outcome,
                              {o.value for o in ReconciliationOutcome})


# ══════════════════════════════════════════════════════════════════════════════
class TestR1G_Placement(unittest.TestCase):
    """Owner correction OC-R1: admission runs below Layers A/B/C and above Layer D.

    The reason is latency, and it is specific. `SEMANTIC_SAME_TURN_ENABLED=true` on the
    candidate deployment, so admission's one read of `_semantic_turn_evidence()` is a
    bounded wait of `SEMANTIC_SAME_TURN_TIMEOUT_SECONDS` (6.0 s). Layers A, B and C are
    the three terminal pre-gates — motorcycle handoff, phone-call handoff, unsupported
    service — and they answer in milliseconds today. Below them, they still do.

    The distinction this group keeps precise is **dispatch** versus **wait**. The
    interpreter is dispatched by `_run_shadow_understand`, which sits above Layer A at the
    starting HEAD and which R1 does not move: an A/B/C turn therefore still starts a
    background interpretation, exactly as it does today, and `g06` measures that it does.
    What those turns no longer do is *wait* for it. Claiming "no provider activity" on the
    strength of `_turn_semantic_calls == 0` would be false, and `g06`/`g07` say so.
    """

    #: Texts that genuinely exit at each pre-gate, verified against the gate's own
    #: predicate rather than assumed from its name: `_is_motorcycle_enquiry`,
    #: `_is_phone_call_request` and `_handle_explicit_service_gate`'s three detectors.
    TERMINAL = (
        ("layer_a_motorcycle", "tengo una moto, la revisan?"),
        ("layer_a_motorcycle_with_locality", "tengo una moto, la revisan? en flores"),
        ("layer_b_phone_call", "llamame por favor"),
        ("layer_c_transfer", "necesito hacer la transferencia del auto"),
        ("layer_c_repair", "me podes reparar el motor?"),
    )
    #: Turns that reach admission. The locality is in the customer's own words in each,
    #: because an inferred one is refused by name (`INFERRED_NOT_EXPLICIT`).
    ADMITTING = (
        ("layer_d_faq", "¿de qué consta la revisión? en flores"),
        ("layer_f_step9", "en flores"),
        ("vehicle_named", "es un corolla 2024, está en flores"),
    )

    @staticmethod
    def _count_reads(text, *, admission, evidence=None, **kw):
        """Run one complete turn and count how many times the turn read the interpreter."""
        reads = []

        def _read(self_):
            reads.append(1)
            return evidence

        with patch.object(ConversationEngine, "_semantic_turn_evidence", _read):
            case = _turn(text, admission=admission, **kw)
        return case, len(reads)

    def test_g01_layers_a_b_and_c_never_read_the_interpreter(self):
        """The correction, stated as the thing a customer would feel. With the flag ON,
        a terminal safety turn does not reach admission at all, so there is no bounded
        wait to pay and no admission record to read."""
        for name, text in self.TERMINAL:
            with self.subTest(gate=name):
                case, reads = self._count_reads(
                    text, admission=True, evidence=_evidence(_loc("Flores")))
                self.assertEqual(reads, 0, "a terminal pre-gate waited on the model")
                self.assertIsNone(case.admission,
                                  "admission ran above a terminal pre-gate")
                self.assertTrue(case.turn.responded, "the gate must still answer")

    def test_g02_every_gate_from_layer_d_down_still_reads_it(self):
        """The other half of the same boundary: admission is above Layer D, so the gates
        that continue the conversation still decide their evidence first.

        Stated as a delta, which is the honest form. Those turns have their own inherited
        readers — a bare locality reads twice at the starting HEAD, a named vehicle three
        times — and the provider memoises, so whoever arrives first pays the one wait.
        Admission adds exactly one read, never two, and never a second model call."""
        for name, text in self.ADMITTING:
            with self.subTest(gate=name):
                evidence = _evidence(_loc("Flores"))
                off, off_reads = self._count_reads(text, admission=False,
                                                   evidence=evidence)
                on, on_reads = self._count_reads(text, admission=True, evidence=evidence)
                self.assertEqual(on_reads, off_reads + 1,
                                 "admission did not run, or ran twice")
                self.assertIsNone(off.admission)
                self.assertIsNotNone(on.admission)
                self.assertEqual(on.turn.semantic_calls, 0)

    def test_g03_the_terminal_gates_are_baseline_equivalent_with_the_flag_on(self):
        """Requirement 3 of the correction: reply, state, escalation, route, pricing,
        attempts and revisions are identical, with and without an interpretation."""
        for name, text in self.TERMINAL:
            for evidence in (None, _evidence(_loc("Flores"))):
                with self.subTest(gate=name, interpreted=evidence is not None):
                    off, on = _both(text, evidence=evidence)
                    self.assertEqual(on.comparable(), off.comparable())
                    # and the locality is deliberately NOT persisted on these turns
                    self.assertEqual(on.turn.thread_zone, (None, None))
                    self.assertIn(on.turn.cand_zone, (None, (None, None)))

    def test_g04_the_locality_still_survives_the_layer_f_greeting(self):
        """Requirement 4. The Wild's turn 2, at the corrected placement: CABA/Flores is
        on the thread when the turn ends, and the qualification greeting is still sent —
        the greeting is R2's to remove, and R1 does not pretend otherwise."""
        off, on = _both("en flores")
        self.assertEqual(off.turn.thread_zone, (None, None))
        self.assertEqual(on.turn.thread_zone, ("CABA", "Flores"))
        self.assertTrue(any(_GREETING_MARK in s for s in on.turn.sent))
        self.assertEqual(on.turn.sent, off.turn.sent)

    def test_g05_faq_locality_evidence_is_admitted_before_the_faq_response(self):
        """Requirement 5, and the measurement behind the placement.

        Layer D answers and returns — inherited behaviour, flag irrelevant — and this is
        one of the two places the Wild measured a locality dying. Two separate facts,
        asserted separately because they have different producers:

        1. with no interpretation at all, step A (the deterministic role-aware reader, run
           write-only) keeps the locality, because at the starting HEAD that reader lives
           at stage 14 and Layer D returns before reaching it;
        2. with an interpretation, the semantic claim crosses the boundary too and the
           record says so.

        In both the reply is byte-identical to the committed one."""
        text = "¿de qué consta la revisión? en flores"
        off, on = _both(text)
        self.assertEqual(on.turn.sent, off.turn.sent)
        self.assertTrue(on.turn.sent, "Layer D must still answer the FAQ")
        self.assertEqual(off.turn.thread_zone, (None, None))
        self.assertEqual(on.turn.thread_zone, ("CABA", "Flores"))
        self.assertEqual(on.records, (), "no interpretation was offered on this turn")

        off2, on2 = _both(text, evidence=_evidence(_loc("Flores")))
        self.assertEqual(on2.turn.sent, off2.turn.sent)
        self.assertEqual(on2.turn.thread_zone, ("CABA", "Flores"))
        record = on2.record_for()
        self.assertEqual(record.outcome, ReconciliationOutcome.ACCEPT.value)
        self.assertEqual((record.zone_group, record.zone_detail), ("CABA", "Flores"))

    def test_g06_dispatch_still_happens_above_layer_a_and_is_not_waited_on(self):
        """The precise claim, measured with a real provider and a slow interpreter.

        `_run_shadow_understand` is above Layer A at the starting HEAD and R1 leaves it
        there, so a motorcycle turn DOES start a background interpretation. What changes
        is that it no longer waits for one: the terminal turn returns long before the
        interpretation finishes, while a Layer-D turn pays the wait. Both numbers come
        from the same fake interpreter in the same process.
        """
        import time
        from app.services import semantic_interpreter as SI

        #: Long enough that "did this turn wait?" is unambiguous even on a loaded host:
        #: a terminal turn costs ~25 ms in this harness, so the margin is ~40x.
        SLEEP = 1.00

        class _SlowInterpreter:
            constructed = 0
            interpreted = 0

            def __init__(self, settings):
                type(self).constructed += 1

            def interpret(self, *a, **kw):
                type(self).interpreted += 1
                time.sleep(SLEEP)
                return None          # no evidence; the WAIT is what is being measured

        def _timed(text):
            _SlowInterpreter.constructed = 0
            _SlowInterpreter.interpreted = 0
            db = H._SessionLocal()
            try:
                H._seed_catalogue(db)
                thread_id, cand_id, wa = H._seed_thread(
                    db, vehicle=False, zone=None, stage=H.STAGE_QUALIFYING, flag=None)
                engine = H._engine(db, safety=True)
                engine.settings.evidence_admission_enabled = True
                engine.settings.outstanding_question_enabled = True   # OC-R2: the interlock
                engine.settings.shadow_understand_enabled = True
                engine.settings.shadow_understand_async = True
                engine.settings.semantic_same_turn_enabled = True
                engine.settings.semantic_same_turn_timeout_seconds = 5.0
                engine.settings.shadow_evidence_path = ""
                with patch.object(SI, "SemanticTurnInterpreter", _SlowInterpreter), \
                        patch("app.services.shadow_recorder.record_shadow"):
                    started = time.monotonic()
                    H._run(db, engine, thread_id, wa, text, cand_id=cand_id)
                    elapsed = time.monotonic() - started
                return elapsed, engine
            finally:
                db.close()

        moto_elapsed, moto_engine = _timed("tengo una moto, la revisan?")
        self.assertEqual(_SlowInterpreter.constructed, 1,
                         "the interpreter was not dispatched — that would be a change "
                         "to inherited shadow behaviour, not to R1")
        self.assertTrue(moto_engine._turn_semantic_dispatched)
        self.assertLess(moto_elapsed, SLEEP,
                        f"the motorcycle handoff waited on the model ({moto_elapsed:.3f}s)")

        faq_elapsed, _ = _timed("¿de qué consta la revisión? en flores")
        self.assertGreaterEqual(faq_elapsed, SLEEP * 0.8,
                                f"the Layer-D turn did not reach the wait "
                                f"({faq_elapsed:.3f}s) — admission did not run")
        # The discriminating statement, independent of absolute host speed: the turn that
        # reaches admission pays the interpretation, the terminal turn does not.
        self.assertGreater(faq_elapsed - moto_elapsed, SLEEP * 0.5,
                           f"moto={moto_elapsed:.3f}s faq={faq_elapsed:.3f}s")

    def test_g07_the_dispatch_point_is_not_what_r1_moved(self):
        """So that the previous test cannot be misread as R1 adding provider activity:
        the dispatch happens in both flag postures, and on a terminal turn too."""
        for name, text in self.TERMINAL + self.ADMITTING:
            for admission in (False, True):
                with self.subTest(gate=name, flag=admission):
                    case = _turn(text, admission=admission,
                                 evidence=_evidence(_loc("Flores")))
                    self.assertTrue(case.turn.engine._turn_semantic_dispatched)
                    self.assertEqual(case.turn.semantic_calls, 0,
                                     "no model call is made in this suite")

    def test_g08_the_r3_and_precedence_protections_are_unchanged_at_the_new_place(self):
        """Requirement 6, re-measured below Layer C rather than above Layer A: coverage,
        ambiguity, staleness, explicit precedence and candidate isolation."""
        checks = (
            ("unsupported", "en mar del plata", _evidence(_loc("Mar del Plata")),
             EA.AdmissionReason.LOCALITY_UNSUPPORTED),
            ("ambiguous", "en san nicolas", _evidence(_loc("san nicolas")),
             EA.AdmissionReason.LOCALITY_AMBIGUOUS),
            # OC-R2 CORRECTION 2: these two bursts carry no resolvable locality in the
            # customer's own words, so the predicate declines to consult the interpreter
            # and there is no claim to refuse. `None` means "no record, nothing written",
            # which is the stronger outcome; the boundary rules themselves are proven
            # directly by `TestR1A_Boundary`.
            ("stale", "ya te lo dije",
             _evidence(_loc("Flores"), message_ids=("wamid.OLD",)), None),
            ("inferred", "en flore", _evidence(_loc("Flores")), None),
            ("origin_role", "soy de flores", _evidence(_loc("Flores")),
             EA.AdmissionReason.ROLE_DECIDED_BY_TEXT),
        )
        for name, text, evidence, reason in checks:
            with self.subTest(case=name):
                on = _turn(text, admission=True, evidence=evidence)
                self.assertEqual(on.turn.thread_zone, (None, None))
                if reason is None:
                    self.assertEqual((), tuple(on.admission.records))
                else:
                    self.assertEqual(on.record_for().reason, reason)
        # candidate isolation, at the new placement
        on = _turn("en flores", admission=True, vehicle=True, mentioned=True,
                   evidence=_evidence(_loc("Flores")))
        self.assertEqual(on.turn.cand_zone, ("CABA", "Flores"))
        self.assertEqual(on.other_candidate, (None, None))


    def test_g09_the_terminal_gates_issue_the_same_sql_and_the_same_commits(self):
        """Requirement 3's last two words, as a test rather than a probe.

        A statement counter on the harness engine, ON against OFF, for each terminal
        pre-gate: the boundary must add no read, no write and no transaction. The turns
        that DO reach admission are asserted the other way round — they add statements but
        still no commit, because admission writes inside the turn's own transaction."""
        from sqlalchemy import event

        counts = {"stmts": 0, "commits": 0}

        def _stmt(conn, cursor, statement, params, ctx, many):
            counts["stmts"] += 1

        def _commit(conn):
            counts["commits"] += 1

        def _measure(text, admission, evidence):
            counts["stmts"] = counts["commits"] = 0
            event.listen(H._ENGINE, "before_cursor_execute", _stmt)
            event.listen(H._ENGINE, "commit", _commit)
            try:
                _turn(text, admission=admission, evidence=evidence)
            finally:
                event.remove(H._ENGINE, "before_cursor_execute", _stmt)
                event.remove(H._ENGINE, "commit", _commit)
            return counts["stmts"], counts["commits"]

        evidence = _evidence(_loc("Flores"))
        for name, text in self.TERMINAL:
            with self.subTest(gate=name):
                self.assertEqual(_measure(text, True, evidence),
                                 _measure(text, False, evidence),
                                 "the boundary touched the database above a terminal gate")
        for name, text in self.ADMITTING:
            with self.subTest(gate=name):
                on_stmts, on_commits = _measure(text, True, evidence)
                off_stmts, off_commits = _measure(text, False, evidence)
                self.assertGreater(on_stmts, off_stmts,
                                   "admission did not run on a turn that reaches it")
                self.assertEqual(on_commits, off_commits,
                                 "admission opened a transaction of its own")


if __name__ == "__main__":
    unittest.main()
