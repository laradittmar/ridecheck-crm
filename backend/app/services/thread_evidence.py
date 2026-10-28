"""W1F-W-01 R2 — the persistence of `ClaimEvidence`, and the binding rules over it.

The architectural review's §8.2 in one sentence: *"This table is the persistence of an
existing in-memory type, not a new model."* R1 built the typed admission boundary and every
decision it makes — acceptance and refusal alike — but those decisions lived only on the
engine object for the length of one turn. So a locality the customer gave before the vehicle
existed still could not reach the candidate created on a later turn, and `W1F-C-05` /
`W1F-C-07` stayed open. This module is the durable half.

It does three things and nothing else:

* **persist** every `AdmissionRecord` R1 produced, acceptance and refusal, with provenance;
* **offer** the rows that are legitimately bindable to a candidate, by the certified rules;
* **bind** a row to the candidate that consumed it.

What it deliberately does NOT do:

* it never writes a candidate field. `_apply_inspection_zone` is the C2 chokepoint and stays
  the only writer; this module hands the engine a row and the engine goes through the
  reconciler. A test asserts that no zone field is assigned anywhere in this file.
* it never decides precedence. `reconcile_inspection_location`'s ladder ranks producers, and
  a copy of that ladder here would be exactly finding W1FRV-07.
* it never resolves a locality. The validator's output arrives already computed, in
  `AdmissionRecord.normalized`, from R3's certified resolver.

**Why `RISK-03` can finally be retired for typed rows, and only for those.**
`_apply_candidate` refuses to inherit `state.home_zone_*` at candidate creation because an
untyped buffer value cannot say which cycle, which turn or which ROLE it came from — so a
locality from a previous session could contaminate a new candidate, which is the defect
RISK-03 was written for. A row here carries `cycle_key`, `turn_id` and `role`, so the three
unknowables become columns. The guard therefore stays exactly where it is for the buffer, and
binding is offered only for rows that answer all three questions.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import select

from ..models import WhatsAppThreadEvidence
from ..schemas.claims import ClaimType, ReconciliationOutcome

logger = logging.getLogger(__name__)

CONTRACT = "thread-evidence/1.0"

#: The claim types whose role is "where the vehicle is to be inspected". One entry today;
#: a frozenset because the binding query filters on it and a second location-shaped family
#: must be an explicit addition, never an accident of string comparison.
LOCALITY_CLAIM_TYPES = frozenset({ClaimType.INSPECTION_LOCATION})

#: `role` is the LR-2 fact: whose place this is. Derived from the claim type, which is where
#: the producer already recorded it — not re-derived from text.
_ROLE_FOR = {
    ClaimType.INSPECTION_LOCATION: "INSPECTION_LOCATION",
    ClaimType.CUSTOMER_ORIGIN: "CUSTOMER_ORIGIN",
    ClaimType.SELLER_LOCATION: "SELLER_LOCATION",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_value(enum_or_str: Any) -> str:
    return getattr(enum_or_str, "value", enum_or_str)


def role_for(claim_type: Any) -> Optional[str]:
    """The LR-2 role this claim type carries, or None when it carries none."""
    return _ROLE_FOR.get(_as_value(claim_type), _ROLE_FOR.get(claim_type))


def _claim_identity(record, *, thread_id: int, cycle_key: int) -> str:
    """The row's identity: the claim's own content hash where it has one.

    `ClaimEvidence.with_id()` computes a content hash and R1 carries it on the provenance,
    so the same claim read from the same burst produces the same identity and the UNIQUE
    `(thread_id, claim_id)` makes a replayed turn idempotent at the database rather than at a
    read-then-write a second worker can also pass.

    A claim that arrives without one is hashed here from the same components the content hash
    uses, so the fallback is deterministic too — never a uuid4, never a timestamp, because
    either would make a replay insert a second row.
    """
    existing = getattr(getattr(record, "provenance", None), "claim_id", None)
    if existing:
        return str(existing)[:64]
    prov = getattr(record, "provenance", None)
    parts = (
        "thread-evidence:v1",
        str(int(thread_id)), str(int(cycle_key)),
        str(_as_value(record.claim_type)), str(record.value),
        str(getattr(prov, "producer", "")), str(getattr(prov, "turn_id", "")),
        ",".join(getattr(prov, "source_message_ids", ()) or ()),
    )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:64]


def _normalized_text(record) -> Optional[str]:
    group, detail = record.zone_group, record.zone_detail
    if not group and not detail:
        return None
    return f"{group or ''}|{detail or ''}"[:160]


# ── persistence ───────────────────────────────────────────────────────────────

def persist_admission(db, *, thread_id: int, cycle_key: int, result,
                      now: Optional[datetime] = None) -> list:
    """One row per admission decision. Idempotent, additive, and never a mutation.

    Returns the rows for this turn, existing ones included, in the order R1 decided them —
    so a caller can hand the acceptance straight to the binding path without a second query.

    Refusals are persisted with the same fidelity as acceptances. That is finding G-11: R1
    already produced a typed reason for every refused claim, and a reason nobody can read
    afterwards is indistinguishable from no decision at all.
    """
    if result is None:
        return []
    stamp = now or _now()
    rows: list = []
    for record in getattr(result, "records", ()) or ():
        claim_id = _claim_identity(record, thread_id=thread_id, cycle_key=cycle_key)
        existing = db.execute(
            select(WhatsAppThreadEvidence).where(
                WhatsAppThreadEvidence.thread_id == thread_id,
                WhatsAppThreadEvidence.claim_id == claim_id)).scalars().first()
        if existing is not None:
            # A replayed burst decides the same claims again. The first decision stands;
            # nothing is overwritten, because the row is the record of what was decided
            # THEN and a second identical decision adds no information.
            rows.append(existing)
            continue
        prov = getattr(record, "provenance", None)
        row = WhatsAppThreadEvidence(
            thread_id=thread_id,
            cycle_key=int(cycle_key),
            turn_id=(str(getattr(prov, "turn_id", "") or "")[:64] or None),
            claim_type=str(_as_value(record.claim_type))[:48],
            role=role_for(record.claim_type),
            value=(None if record.value is None else str(record.value)),
            normalized_value=_normalized_text(record),
            evidence_class=(str(getattr(prov, "evidence_class", "") or "")[:32] or None),
            producer=(str(getattr(prov, "producer", "") or "")[:64] or None),
            producer_version=(str(getattr(prov, "producer_version", "") or "")[:32] or None),
            status=(str(getattr(prov, "status", "") or "")[:16] or None),
            polarity=(str(getattr(prov, "polarity", "") or "")[:16] or None),
            explicitness=(str(getattr(prov, "explicitness", "") or "")[:16] or None),
            outcome=str(_as_value(record.outcome))[:16],
            reason=(str(record.reason or "")[:48] or None),
            source_message_ids=list(getattr(prov, "source_message_ids", ()) or ()),
            validated_as=({"zone_group": record.zone_group,
                           "zone_detail": record.zone_detail}
                          if record.normalized else None),
            claim_id=claim_id,
            created_at=stamp,
        )
        db.add(row)
        rows.append(row)
    if rows:
        db.flush()
    return rows


# ── binding ───────────────────────────────────────────────────────────────────

def bindable_locality(db, *, thread_id: int, cycle_key: int) -> Optional[
        WhatsAppThreadEvidence]:
    """The newest locality row this cycle that may legitimately reach a candidate.

    Five conditions, each one a column rather than an assumption — which is the whole reason
    the table exists:

    1. `thread_id` matches. Evidence never crosses threads or customers.
    2. `cycle_key` matches. `W1F-C-22`: a locality given before a cycle reset must NOT bind
       to a candidate created after it. The review's own words: "the new evidence table must
       carry `cycle_key`".
    3. `role = INSPECTION_LOCATION`. LR-2's separation survives persistence: the customer's
       own origin was never a claim about where the car is, and it is not one here either.
    4. `outcome = ACCEPT`. A refusal is history, never a value. An ambiguous or unsupported
       locality is stored precisely so that it can be read and NOT used.
    5. not already bound, and not superseded. One locality row binds to one candidate, and a
       correction's replacement row is what binds instead of the value it replaced.

    Newest first, because a later in-cycle statement by the same customer is a correction of
    an earlier one, and `superseded_by_id` records that relationship explicitly.
    """
    stmt = (select(WhatsAppThreadEvidence)
            .where(WhatsAppThreadEvidence.thread_id == thread_id,
                   WhatsAppThreadEvidence.cycle_key == int(cycle_key),
                   WhatsAppThreadEvidence.role == "INSPECTION_LOCATION",
                   WhatsAppThreadEvidence.outcome
                   == ReconciliationOutcome.ACCEPT.value,
                   WhatsAppThreadEvidence.bound_to_candidate_id.is_(None),
                   WhatsAppThreadEvidence.superseded_by_id.is_(None))
            .order_by(WhatsAppThreadEvidence.id.desc()))
    for row in db.execute(stmt).scalars().all():
        if row.zone_pair() != (None, None):
            return row
    return None


def bind(db, row, candidate_id: int, *, now: Optional[datetime] = None) -> bool:
    """Mark a row consumed by a candidate. Refuses a second binding.

    Returns whether THIS call bound it. A row already bound — by a concurrent turn, or by a
    replay of this one — returns False and is left exactly as it was, so "which evidence set
    this candidate's location" keeps one answer.
    """
    if row is None:
        return False
    if row.bound_to_candidate_id is not None:
        return row.bound_to_candidate_id == candidate_id and False
    row.bound_to_candidate_id = int(candidate_id)
    row.bound_at = now or _now()
    db.flush()
    return True


def supersede(db, old_row, new_row, *, now: Optional[datetime] = None) -> bool:
    """Record that `new_row` replaced `old_row`. Provenance of the old value is kept.

    The owner's invariant 4 and review §8.4: a correction is a new row that supersedes an
    old one, and nothing is deleted or overwritten — so "the customer first said Flores and
    then corrected it to Palermo" stays answerable after the fact.
    """
    if old_row is None or new_row is None or old_row.id == new_row.id:
        return False
    if old_row.superseded_by_id is not None:
        return False
    old_row.superseded_by_id = int(new_row.id)
    db.flush()
    return True


def for_thread(db, *, thread_id: int, cycle_key: Optional[int] = None,
               claim_type: Optional[Any] = None) -> list:
    """Read-only accessor for the tests, the Inspector and the trace."""
    stmt = select(WhatsAppThreadEvidence).where(
        WhatsAppThreadEvidence.thread_id == thread_id)
    if cycle_key is not None:
        stmt = stmt.where(WhatsAppThreadEvidence.cycle_key == int(cycle_key))
    if claim_type is not None:
        stmt = stmt.where(
            WhatsAppThreadEvidence.claim_type == str(_as_value(claim_type)))
    return list(db.execute(stmt.order_by(WhatsAppThreadEvidence.id)).scalars().all())
