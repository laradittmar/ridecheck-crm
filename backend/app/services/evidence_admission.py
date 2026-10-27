"""W1F-W-01 R1 — the evidence admission boundary.

The W1F-W-01 review measured the defect precisely: **authority in the deployed system is
positional, not evidential.** What decides a turn is where the turn exits the ordered gate
chain in `_process_text`. The interpreter read `en flores` correctly — `INSPECTION_LOCATION
= Flores`, CONFIRMED, in the very turn the customer answered the system's own question —
and the turn ended at Layer F step 9, eighteen lines before the only deterministic location
writer and thirty-four before the only semantic location consumer. There was no decision
site for the claim to arrive at, so there is not even a record of its refusal.

This module is the *boundary*, and only the boundary: it decides which of a turn's
projected claims are **eligible** to reach a field reconciler, and it says so in a typed
record that names the producer, the evidence class, the cycle, the turn and the messages the
reading came from. It is deliberately not a reconciler:

* **Eligibility is here.** Family support, staleness, polarity, status, the locality
  validator's verdict, an already-confirmed canonical value, and a same-class conflict.
* **Value selection stays in `field_reconciler.reconcile_inspection_location`.** That ladder
  (HUMAN_CONFIRMED ▸ EXPLICIT_CUSTOMER ▸ DETERMINISTIC_EXTRACTED ▸ CATALOG_CONFIRMED ▸
  SEMANTIC_INFERRED) is certified by C2 and is the one place that chooses between competing
  producers. Re-implementing it here would give one rule two homes — exactly the hazard
  W1FRV-07 names.

What the boundary may never do, stated as code below rather than as prose: admit a family
it does not support, admit a price, a slot, an offer or a booking (the claim space
deliberately has no such type, and `REFUSED_CLAIM_TYPES` names the rest explicitly), invent
a value no producer proposed, overwrite a confirmed canonical value, or treat a locality the
R3 resolver refused as truth. It sends nothing, writes nothing and calls no model: it is a
pure function over claims, and the caller does the writing through the existing chokepoint.

Contract `evidence-admission/1.0`. Outcome vocabulary is `ReconciliationOutcome` — ACCEPT,
CLARIFY, HOLD — unchanged, because the Inspector's reading of a stored decision must not
shift (review §15.4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from ..schemas.claims import (ClaimEvidence, ClaimType, EvidenceClass, Polarity,
                              ReconciliationOutcome)
from ..schemas.turn_evidence import EvidenceStatus

CONTRACT = "evidence-admission/1.0"

#: The only families this boundary admits. R1 is the location family and nothing else: it
#: is the family the Wild lost, it is the one with a certified validator (the R3 resolver)
#: and a certified writer (`_apply_inspection_zone`), and a boundary that admits one family
#: well is worth more than one that admits five by guesswork. Vehicle identity already
#: reaches its reconciler from the catalogue path; widening this set is a later decision
#: with its own evidence.
ADMITTED_CLAIM_TYPES = frozenset({ClaimType.INSPECTION_LOCATION})

#: Families that must never be admitted here, named one by one so the rule is readable and
#: testable rather than implied by the absence of a branch. Two different reasons are mixed
#: on purpose, and both are load-bearing:
#:
#: * a ROLE that is not the inspection location (`CUSTOMER_ORIGIN`, `SELLER_LOCATION`) —
#:   the Wild's turn 1 carried `CUSTOMER_ORIGIN=Tigre` beside `INSPECTION_LOCATION=Flores`,
#:   and had the origin reached the one locality slot the price and the appointment would
#:   have been computed for the wrong side of the city;
#: * a family whose admission would be a business decision (`QUOTE_ACCEPTED`,
#:   `SCHEDULING_PREFERENCE`, `NEEDS_HUMAN`, `SERVICE_INTENT`, …). Those have their own
#:   authorities — the acceptance authorizer, the scheduling interface, the handoff site —
#:   and this boundary must not become a second door into them.
REFUSED_CLAIM_TYPES = frozenset({
    ClaimType.CUSTOMER_ORIGIN, ClaimType.SELLER_LOCATION,
    ClaimType.SERVICE_INTENT, ClaimType.QUOTE_REQUEST, ClaimType.QUOTE_ACCEPTED,
    ClaimType.FUTURE_INTENT, ClaimType.SEARCHING_NOT_READY, ClaimType.CORRECTION,
    ClaimType.SCHEDULING_PREFERENCE, ClaimType.INSPECTABILITY, ClaimType.FAQ_TOPIC,
    ClaimType.NEEDS_HUMAN,
    ClaimType.VEHICLE_MAKE, ClaimType.VEHICLE_MODEL, ClaimType.VEHICLE_YEAR,
    ClaimType.VEHICLE_CATEGORY,
})

#: Only a reading the producer stands behind may become canonical truth. PROPOSED,
#: AMBIGUOUS and CONFLICT are all "not yet" — the four-valued information state's whole
#: point is that absence is not falsity, and a maybe is not a yes.
ADMISSIBLE_STATUSES = frozenset({EvidenceStatus.CONFIRMED.value})

#: The classes that may become canonical truth here. The omission is the rule: a locality
#: the interpreter INFERRED — one whose words are nowhere in what the customer wrote — is
#: not admitted, however confident the model is. That is not timidity, it is the policy the
#: system already has for exactly this evidence: `_recover_locality` turns an approximate
#: semantic locality into a PROPOSAL the customer confirms, and never into a write. The
#: hazard is concrete and was measured while building this boundary: with an inferred
#: locality admitted, a turn that said only "es un corolla del 2024" acquired a locality the
#: customer had not given and was answered with a PRICE for it. Model prose is evidence,
#: not authority.
#:
#: `EXPLICIT_CUSTOMER` is how the projection classifies a mention whose value appears
#: verbatim in the burst, which is the Wild's own case: the interpreter reading `Flores` out
#: of `en flores` is reporting the customer's words, not guessing.
ADMISSIBLE_EVIDENCE_CLASSES = frozenset({
    EvidenceClass.HUMAN_CONFIRMED.value,
    EvidenceClass.EXPLICIT_CUSTOMER.value,
    EvidenceClass.DETERMINISTIC_EXTRACTED.value,
    EvidenceClass.CATALOG_CONFIRMED.value,
})


class AdmissionReason:
    """Typed reason codes. A refusal with no code is a refusal nobody can audit."""

    ADMITTED = "ADMITTED"
    FAMILY_NOT_ADMITTED = "FAMILY_NOT_ADMITTED"
    ROLE_NOT_INSPECTION = "ROLE_NOT_INSPECTION"
    STALE_CYCLE = "STALE_CYCLE"
    STALE_TURN = "STALE_TURN"
    STALE_BURST = "STALE_BURST"
    NEGATED = "NEGATED"
    UNCONFIRMED_STATUS = "UNCONFIRMED_STATUS"
    INFERRED_NOT_EXPLICIT = "INFERRED_NOT_EXPLICIT"
    ROLE_DECIDED_BY_TEXT = "ROLE_DECIDED_BY_TEXT"
    NO_VALUE = "NO_VALUE"
    LOCALITY_UNSUPPORTED = "LOCALITY_UNSUPPORTED"
    LOCALITY_AMBIGUOUS = "LOCALITY_AMBIGUOUS"
    LOCALITY_UNRESOLVED = "LOCALITY_UNRESOLVED"
    CONFIRMED_VALUE_PRESENT = "CONFIRMED_VALUE_PRESENT"
    OUTRANKED_BY_EXPLICIT = "OUTRANKED_BY_EXPLICIT"
    COMPETING_LOCALITIES = "COMPETING_LOCALITIES"


#: What the locality validator may answer. `OK` carries a normalized (group, detail) pair;
#: the three refusals map 1:1 onto the R3 `LocalityVerdict` values, so the resolver stays
#: the single authority on what a locality is and this module only reads its verdict.
VALIDATOR_OK = "OK"
VALIDATOR_UNSUPPORTED = "UNSUPPORTED"
VALIDATOR_AMBIGUOUS = "AMBIGUOUS"
VALIDATOR_UNRESOLVED = "UNRESOLVED"

_VALIDATOR_REFUSALS = {
    VALIDATOR_UNSUPPORTED: (ReconciliationOutcome.CLARIFY, AdmissionReason.LOCALITY_UNSUPPORTED),
    VALIDATOR_AMBIGUOUS: (ReconciliationOutcome.CLARIFY, AdmissionReason.LOCALITY_AMBIGUOUS),
    VALIDATOR_UNRESOLVED: (ReconciliationOutcome.HOLD, AdmissionReason.LOCALITY_UNRESOLVED),
}


def _as_value(enum_or_str: Any) -> str:
    return str(getattr(enum_or_str, "value", enum_or_str) or "")


@dataclass(frozen=True)
class Provenance:
    """Where a claim came from — source and originating turn, not just a producer name.

    `source_message_ids` is the interpreter's own `turn.ordered_message_ids`, and
    `burst_fingerprint` is the G3-5B binding that says WHICH burst was interpreted. Both
    travel with the record because "this evidence is from this turn" is the claim the
    staleness rules below actually check, and a reader must be able to check it too.
    """

    producer: str = ""
    producer_version: Optional[str] = None
    evidence_class: str = ""
    explicitness: str = ""
    status: str = ""
    polarity: str = ""
    cycle_id: Optional[str] = None
    turn_id: Optional[str] = None
    burst_fingerprint: Optional[str] = None
    source_message_ids: tuple[str, ...] = ()
    claim_id: Optional[str] = None

    @property
    def is_semantic(self) -> bool:
        return self.evidence_class == EvidenceClass.SEMANTIC_INFERRED.value

    @property
    def is_from_interpreter(self) -> bool:
        """Did this claim come from the semantic producer?

        Not the same question as `is_semantic`. The projection promotes a mention whose
        words appear verbatim in the burst to `EXPLICIT_CUSTOMER` — correctly, because the
        customer did write them — so the interpreter's own reading of "en flores" is NOT in
        the SEMANTIC_INFERRED class. The burst-binding rule has to follow the producer, or
        the strongest semantic claims would be the ones that escape it.
        """
        return (self.producer.startswith("semantic")
                or self.evidence_class == EvidenceClass.SEMANTIC_INFERRED.value)

    def as_dict(self) -> dict:
        return {
            "producer": self.producer, "producer_version": self.producer_version,
            "evidence_class": self.evidence_class, "explicitness": self.explicitness,
            "status": self.status, "polarity": self.polarity, "cycle_id": self.cycle_id,
            "turn_id": self.turn_id, "burst_fingerprint": self.burst_fingerprint,
            "source_message_ids": list(self.source_message_ids), "claim_id": self.claim_id,
        }


@dataclass(frozen=True)
class AdmissionRecord:
    """One claim, one decision, with the reason and the provenance that produced it."""

    claim_type: str
    value: Any
    outcome: str
    reason: str
    detail: str = ""
    normalized: Optional[tuple] = None
    provenance: Provenance = field(default_factory=Provenance)

    @property
    def admitted(self) -> bool:
        return self.outcome == ReconciliationOutcome.ACCEPT.value

    @property
    def zone_group(self):
        return self.normalized[0] if self.normalized else None

    @property
    def zone_detail(self):
        return self.normalized[1] if self.normalized else None

    def as_dict(self) -> dict:
        return {
            "claim_type": self.claim_type, "value": self.value, "outcome": self.outcome,
            "reason": self.reason, "detail": self.detail,
            "zone_group": self.zone_group, "zone_detail": self.zone_detail,
            "provenance": self.provenance.as_dict(),
        }


@dataclass(frozen=True)
class AdmissionResult:
    """Every produced claim's decision. Refusals are first-class, not absences."""

    records: tuple[AdmissionRecord, ...] = ()
    claims: tuple[ClaimEvidence, ...] = ()
    contract: str = CONTRACT

    @property
    def admitted(self) -> tuple[AdmissionRecord, ...]:
        return tuple(r for r in self.records if r.admitted)

    @property
    def refused(self) -> tuple[AdmissionRecord, ...]:
        return tuple(r for r in self.records if not r.admitted)

    @property
    def admitted_claims(self) -> tuple[ClaimEvidence, ...]:
        """The eligible claims, for the reconciler to choose between.

        Selection is NOT done here: the C2 ladder in `reconcile_inspection_location` is the
        one place that ranks producers, and it receives every eligible claim so that an
        explicit customer statement outranks an inferred one by the certified rule rather
        than by a copy of it.
        """
        return tuple(claim for record, claim in zip(self.records, self.claims)
                     if record.admitted)

    def reason_for(self, claim_type: str) -> Optional[str]:
        for record in self.records:
            if record.claim_type == claim_type:
                return record.reason
        return None

    def as_dict(self) -> dict:
        return {"contract": self.contract,
                "records": [r.as_dict() for r in self.records]}


def _provenance_of(claim: ClaimEvidence, *, turn_id, burst_fingerprint) -> Provenance:
    return Provenance(
        producer=str(getattr(claim, "producer", "") or ""),
        producer_version=getattr(claim, "producer_version", None),
        evidence_class=_as_value(getattr(claim, "evidence_class", "")),
        explicitness=_as_value(getattr(claim, "explicitness", "")),
        status=_as_value(getattr(claim, "status", "")),
        polarity=_as_value(getattr(claim, "polarity", "")),
        cycle_id=getattr(claim, "cycle_id", None),
        turn_id=turn_id,
        burst_fingerprint=burst_fingerprint,
        source_message_ids=tuple(str(m) for m in (getattr(claim, "source_message_ids", ()) or ())),
        claim_id=getattr(claim, "claim_id", None),
    )


def admit(
    claims: Iterable[ClaimEvidence],
    *,
    cycle_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    burst_fingerprint: Optional[str] = None,
    turn_message_ids: Iterable[str] = (),
    locality_validator: Optional[Callable[[str], tuple]] = None,
    canonical_locality: Optional[tuple] = None,
    canonical_from_this_turn: bool = False,
    role_decided_by_text: bool = False,
    semantic_binding_ok: bool = True,
) -> AdmissionResult:
    """Decide, for every projected claim, whether it may reach a field reconciler.

    `locality_validator(value)` returns `(status, normalized, detail)` where `status` is one
    of the four `VALIDATOR_*` constants and `normalized` is a `(zone_group, zone_detail)`
    pair for `OK`. It is injected rather than imported so this module stays a leaf: the
    validator in the engine is the certified R3 resolver, and in a test it is whatever the
    test wants to prove.

    `canonical_locality` is the `(group, detail)` already held for this cycle, if any. A
    value that is already established is never overwritten here — a correction is a
    different decision, with its own confirmation step, and R1 does not make it.
    `canonical_from_this_turn` says that value was read from the customer's own words THIS
    turn by the deterministic parser, which is the case the owner's precedence rule names:
    an explicit statement outranks an inferred one, and the refusal says so by name.

    `role_decided_by_text` says the deterministic role parser examined this turn and placed
    its locality with the CUSTOMER, not the vehicle (LR-2: an origin clause suppresses
    itself and nothing else). Measured while building this boundary: without this rule,
    "soy de flores" and "estoy en flores" — turns where the deterministic reader correctly
    writes nothing — had the inspection locality written anyway as soon as the interpreter
    labelled the mention `INSPECTION_LOCATION`. Role is the one thing the review says the
    schema cannot represent and the parser can; a model's role label does not get to
    overrule it, and the absence of a deterministic claim is not the absence of a
    deterministic decision.
    """
    turn_ids = tuple(str(m) for m in (turn_message_ids or ()))
    records: list[AdmissionRecord] = []
    kept: list[ClaimEvidence] = []
    claims = list(claims or ())

    for claim in claims:
        prov = _provenance_of(claim, turn_id=turn_id, burst_fingerprint=burst_fingerprint)
        claim_type = str(getattr(claim, "claim_type", "") or "")
        value = getattr(claim, "value", None)

        def _refuse(outcome, reason, detail=""):
            records.append(AdmissionRecord(
                claim_type=claim_type, value=value, outcome=_as_value(outcome),
                reason=reason, detail=detail, provenance=prov))
            kept.append(claim)

        if claim_type in REFUSED_CLAIM_TYPES or claim_type not in ADMITTED_CLAIM_TYPES:
            reason = (AdmissionReason.ROLE_NOT_INSPECTION
                      if claim_type in (ClaimType.CUSTOMER_ORIGIN, ClaimType.SELLER_LOCATION)
                      else AdmissionReason.FAMILY_NOT_ADMITTED)
            _refuse(ReconciliationOutcome.HOLD, reason,
                    "recorded, never admitted by this boundary")
            continue

        # ── staleness, three different ways evidence can fail to be about THIS turn ──
        if prov.cycle_id != cycle_id:
            _refuse(ReconciliationOutcome.HOLD, AdmissionReason.STALE_CYCLE,
                    f"claim cycle {prov.cycle_id!r} is not this cycle {cycle_id!r}")
            continue
        if prov.source_message_ids and turn_ids and not (
                set(prov.source_message_ids) & set(turn_ids)):
            _refuse(ReconciliationOutcome.HOLD, AdmissionReason.STALE_TURN,
                    "the reading names no message of this turn")
            continue
        if prov.is_from_interpreter and not semantic_binding_ok:
            # G3-5C fail-closed, applied to admission: an interpretation that is not
            # POSITIVELY bound to this turn's canonical burst is unavailable, not advisory.
            _refuse(ReconciliationOutcome.HOLD, AdmissionReason.STALE_BURST,
                    "semantic reading is not bound to this turn's canonical burst")
            continue

        if prov.polarity == Polarity.NEGATED.value:
            _refuse(ReconciliationOutcome.HOLD, AdmissionReason.NEGATED,
                    "a denial is not an assertion of something else")
            continue
        if prov.status not in ADMISSIBLE_STATUSES:
            _refuse(ReconciliationOutcome.HOLD, AdmissionReason.UNCONFIRMED_STATUS,
                    f"status {prov.status or 'unknown'} is not CONFIRMED")
            continue
        if prov.evidence_class not in ADMISSIBLE_EVIDENCE_CLASSES:
            # An inferred locality stays what the existing design already makes it: a
            # proposal for `_recover_locality` to offer and the customer to confirm.
            _refuse(ReconciliationOutcome.HOLD, AdmissionReason.INFERRED_NOT_EXPLICIT,
                    f"{prov.evidence_class or 'unknown'} is not the customer's own words")
            continue
        if role_decided_by_text:
            _refuse(ReconciliationOutcome.HOLD, AdmissionReason.ROLE_DECIDED_BY_TEXT,
                    "this turn's words place the locality with the customer, "
                    "not the vehicle")
            continue
        if value in (None, "", [], {}):
            _refuse(ReconciliationOutcome.HOLD, AdmissionReason.NO_VALUE, "no value")
            continue

        # ── the validator decides what the locality IS; this module never guesses ──
        status, normalized, detail = (VALIDATOR_UNRESOLVED, None, "no validator")
        if locality_validator is not None:
            try:
                status, normalized, detail = locality_validator(str(value))
            except Exception as exc:                      # a broken validator refuses
                status, normalized, detail = (VALIDATOR_UNRESOLVED, None,
                                              f"validator failed: {type(exc).__name__}")
        if status != VALIDATOR_OK or not normalized:
            outcome, reason = _VALIDATOR_REFUSALS.get(
                status, (ReconciliationOutcome.HOLD, AdmissionReason.LOCALITY_UNRESOLVED))
            _refuse(outcome, reason, detail or "")
            continue

        if canonical_locality and tuple(canonical_locality) != tuple(normalized):
            _refuse(ReconciliationOutcome.HOLD,
                    (AdmissionReason.OUTRANKED_BY_EXPLICIT if canonical_from_this_turn
                     else AdmissionReason.CONFIRMED_VALUE_PRESENT),
                    (f"this turn's explicit locality is {canonical_locality}"
                     if canonical_from_this_turn
                     else f"a value is already established for this cycle: {canonical_locality}"))
            continue

        records.append(AdmissionRecord(
            claim_type=claim_type, value=value,
            outcome=ReconciliationOutcome.ACCEPT.value, reason=AdmissionReason.ADMITTED,
            detail=detail or "", normalized=tuple(normalized), provenance=prov))
        kept.append(claim)

    records = _refuse_same_class_conflicts(records)
    return AdmissionResult(records=tuple(records), claims=tuple(kept))


def _refuse_same_class_conflicts(records: list[AdmissionRecord]) -> list[AdmissionRecord]:
    """Two readings of equal strength that disagree are a question, never a coin toss.

    The C2 ladder ranks DIFFERENT evidence classes, which is what makes an explicit
    customer statement outrank an inferred one. It does not rank two claims of the SAME
    class against each other — `_first_value` would simply take the first — so a boundary
    that admitted both would let list order choose the inspection locality. Here they are
    all refused: nothing is written and the turn asks.
    """
    out = list(records)
    by_class: dict[tuple, set] = {}
    for record in out:
        if not record.admitted:
            continue
        key = (record.claim_type, record.provenance.evidence_class)
        by_class.setdefault(key, set()).add(tuple(record.normalized or ()))
    conflicted = {key for key, values in by_class.items() if len(values) > 1}
    if not conflicted:
        return out
    for index, record in enumerate(out):
        key = (record.claim_type, record.provenance.evidence_class)
        if record.admitted and key in conflicted:
            out[index] = AdmissionRecord(
                claim_type=record.claim_type, value=record.value,
                outcome=ReconciliationOutcome.CLARIFY.value,
                reason=AdmissionReason.COMPETING_LOCALITIES,
                detail="two readings of equal strength disagree",
                normalized=None, provenance=record.provenance)
    return out
