"""Provisional correction authority — L4.7W5-GATE3-G3-7F Commit 3.

A grounded correction is not automatically a safe correction. G3-7C and G3-7D measured the
interpreter mis-assigning a correction's referent while reporting `CONFIRMED`, so the owner
declined unconditional immediate mutation: anything that moves a price, an identity or a
quote's validity must be confirmed by the customer before canonical state moves.

Three tiers, each decided from source-proven business impact rather than from field names:

    Tier 1  immediate + echo    `anio` only. `PricingService.quote()` reads tipo and zone
                                and nothing else, and `_catalog_tipo_for` takes no year, so
                                a year moves no price and no category. It does reach the
                                quote prose and `ThreadRevision.anio`, which is why the echo
                                is a correctness requirement and not a courtesy.
    Tier 2  provisional         locality, zone, make, model, category, version, candidate —
                                every pricing input, every identity field, anything
                                quote-invalidating, and anything whose impact is unknown.
    Tier 3  HOLD                owned by the Grounded-Target Invariant upstream.

Everything here is PURE: it classifies, fingerprints and decides. It never writes.
`_apply_candidate` remains the only candidate writer, and a confirmed patch reaches it
exactly once. `pending_correction` is PROPOSED state — nothing reads it as canonical truth.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field as _field
from datetime import datetime, timezone
from typing import Any, Optional


# ── tiers ─────────────────────────────────────────────────────────────────────

class CorrectionTier:
    IMMEDIATE = "IMMEDIATE"
    PROVISIONAL = "PROVISIONAL"
    HOLD = "HOLD"

    ALL = (IMMEDIATE, PROVISIONAL, HOLD)


#: The only canonical field a correction may move without confirmation. Derived, not chosen:
#: `quote()` reads `tipo_vehiculo` and the zone pair; `quote_inputs_unchanged()` compares
#: candidate id, tipo and zone; `_catalog_tipo_for(marca, modelo)` takes no year.
PRICE_NEUTRAL_FIELDS = frozenset({"anio"})

#: Fields that move a price, directly or through the catalogue's category.
PRICE_AFFECTING_FIELDS = frozenset({"tipo_vehiculo", "zone_group", "zone_detail",
                                    "marca", "modelo"})

#: Fields that define which vehicle this is — the writer's own dedup threshold.
IDENTITY_FIELDS = frozenset({"marca", "modelo"})

#: Fields `quote_inputs_unchanged()` compares, so moving one invalidates a delivered quote.
QUOTE_INVALIDATING_FIELDS = frozenset({"tipo_vehiculo", "zone_group", "zone_detail"})


class ImpactClass:
    NONE = "NONE"
    AFFECTS_PRICE = "AFFECTS_PRICE"
    AFFECTS_IDENTITY = "AFFECTS_IDENTITY"
    UNKNOWN = "UNKNOWN"


class PendingStatus:
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"

    ALL = (PENDING, CONFIRMED, REJECTED, SUPERSEDED, EXPIRED)
    TERMINAL = (CONFIRMED, REJECTED, SUPERSEDED, EXPIRED)


class InvalidationReason:
    """Why a patch stopped being confirmable. Enumerated so the trace never says "other"."""
    NEWER_CORRECTION = "NEWER_CORRECTION"
    BEFORE_VALUES_MOVED = "BEFORE_VALUES_MOVED"
    TARGET_LOST = "TARGET_LOST"
    FOCUS_MOVED = "FOCUS_MOVED"
    CYCLE_ENDED = "CYCLE_ENDED"
    HUMAN_HANDOFF = "HUMAN_HANDOFF"
    CUSTOMER_REJECTED = "CUSTOMER_REJECTED"
    NOT_LATEST_QUESTION = "NOT_LATEST_QUESTION"
    LEGACY_PROPOSAL_ACTIVE = "LEGACY_PROPOSAL_ACTIVE"
    FINGERPRINT_MISMATCH = "FINGERPRINT_MISMATCH"
    #: G3-7F-R2 (review G37FRR-05). Provisional authority was armed without the grounding
    #: floor that chooses the target. A patch whose basis is NONE was selected by the v1
    #: resolver, and v1 is exactly what the Grounded-Target Invariant exists to replace.
    GROUNDING_NOT_ENABLED = "GROUNDING_NOT_ENABLED"
    TARGET_NOT_GROUNDED = "TARGET_NOT_GROUNDED"
    #: G3-7F-R2 (review G37FRR-08). The confirmation question's own outbound row is not in a
    #: state that proves dispatch was accepted — it is gone, on another thread, still
    #: pending, suppressed, blocked, failed, or carries no WAMID.
    CONFIRMATION_NOT_DISPATCHED = "CONFIRMATION_NOT_DISPATCHED"
    #: G3-7F-R2 (review G37FRR-04). The clarification budget for this cycle is spent and a
    #: legacy proposal is still outstanding and still sound. Terminates instead of repeating.
    CLARIFICATION_BUDGET_SPENT = "CLARIFICATION_BUDGET_SPENT"

    ALL = (NEWER_CORRECTION, BEFORE_VALUES_MOVED, TARGET_LOST, FOCUS_MOVED, CYCLE_ENDED,
           HUMAN_HANDOFF, CUSTOMER_REJECTED, NOT_LATEST_QUESTION,
           LEGACY_PROPOSAL_ACTIVE, FINGERPRINT_MISMATCH, GROUNDING_NOT_ENABLED,
           TARGET_NOT_GROUNDED, CONFIRMATION_NOT_DISPATCHED, CLARIFICATION_BUDGET_SPENT)


#: G3-7F-R2 (review G37FRR-08). The canonical outbound lifecycle is
#: `routes/whatsapp.py:STATUS_PRECEDENCE` (pending < sent < delivered < read < failed) over
#: the column's own CHECK constraint
#: `('received','pending','sent','delivered','read','failed','blocked')`.
#: This is a POSITIVE allow-list, not a deny-list: a status nobody has thought of yet — a new
#: Meta state, a migration default, a typo — must not confer confirmation authority, and a
#: deny-list would silently admit it.
OUTBOUND_DISPATCH_ACCEPTED = frozenset({"sent", "delivered", "read"})


#: G3-7F-R2 (review G37FRR-03). Every thread-state field that represents an OUTSTANDING
#: proposal the customer could answer. G3-7F listed nine and missed three; the omissions were
#: not arbitrary — they were fields whose twins were already listed, which is how the gap
#: survived review. Each entry below was checked against its actual lifecycle in
#: `conversation_engine.py`: where it is set, where it is cleared, and what a customer "si"
#: would mean while it stands.
#:
#:   field                              set at             cleared at       family
#:   pending_fuzzy_catalog_key          12772              3629, 5513       VEHICLE
#:   pending_turn_evidence_text         12773              (with the pair)  VEHICLE
#:   vehicle_clarification_sent         (ask)              (answer)         VEHICLE
#:   vehicle_fallback_flow_sent         5798, 5906         5513, 11701      VEHICLE
#:   pending_location_proposal          _propose_locality  _clear_...       LOCALITY
#:   location_clarification_sent        (ask)              (answer)         LOCALITY
#:   location_fallback_flow_sent        5830, 5955         5605, 11702      LOCALITY
#:   last_offered_slots                 (offer)            (accept/reset)   SCHEDULING
#:   last_visible_slots                 10199, 10452       10093, 10227     SCHEDULING
#:   active_requested_date              (negotiate)        (resolve)        SCHEDULING
#:   last_requested_time                10188, 10450       10091            SCHEDULING
#:   flow_booking_token                 (Flow dispatch)    3629, 7549       BOOKING_FLOW
#:   inspectability_clarification_sent  (ask)              (answer)         INSPECTABILITY
#:
#: `last_visible_slots` earns its place from `conversation_engine.py:1784`, which reads it
#: BESIDE `last_offered_slots` as "the canonical slot representation": acceptance machinery
#: consults both, so a thread holding only the former still has an answerable offer.

class ProposalFamily:
    """What kind of question is outstanding. Decides what a correction does to it."""
    VEHICLE = "VEHICLE"
    LOCALITY = "LOCALITY"
    SCHEDULING = "SCHEDULING"
    BOOKING_FLOW = "BOOKING_FLOW"
    INSPECTABILITY = "INSPECTABILITY"

    ALL = (VEHICLE, LOCALITY, SCHEDULING, BOOKING_FLOW, INSPECTABILITY)


#: field -> family, in audit order, stable for the trace.
LEGACY_PROPOSALS: tuple = (
    ("pending_fuzzy_catalog_key", ProposalFamily.VEHICLE),
    ("pending_turn_evidence_text", ProposalFamily.VEHICLE),
    ("vehicle_clarification_sent", ProposalFamily.VEHICLE),
    ("vehicle_fallback_flow_sent", ProposalFamily.VEHICLE),
    ("pending_location_proposal", ProposalFamily.LOCALITY),
    ("location_clarification_sent", ProposalFamily.LOCALITY),
    ("location_fallback_flow_sent", ProposalFamily.LOCALITY),
    ("last_offered_slots", ProposalFamily.SCHEDULING),
    ("last_visible_slots", ProposalFamily.SCHEDULING),
    ("active_requested_date", ProposalFamily.SCHEDULING),
    ("last_requested_time", ProposalFamily.SCHEDULING),
    ("flow_booking_token", ProposalFamily.BOOKING_FLOW),
    ("inspectability_clarification_sent", ProposalFamily.INSPECTABILITY),
)

#: Kept as a flat tuple because callers and tests pin it. Derived, never hand-maintained.
LEGACY_PROPOSAL_FIELDS = tuple(f for f, _family in LEGACY_PROPOSALS)

#: A correction supplying one of these ANSWERS the family's outstanding question, so the
#: question is resolved rather than merely cleared: the customer did reply, in their own
#: words, to the thing we asked.
PROPOSAL_RESOLVED_BY: dict = {
    ProposalFamily.VEHICLE: frozenset({"marca", "modelo", "tipo_vehiculo", "version_text"}),
    ProposalFamily.LOCALITY: frozenset({"zone_detail", "zone_group"}),
    ProposalFamily.INSPECTABILITY: frozenset({"marca", "modelo", "tipo_vehiculo"}),
    ProposalFamily.SCHEDULING: frozenset(),
    ProposalFamily.BOOKING_FLOW: frozenset(),
}

#: A correction moving one of these makes the family's proposal UNSOUND — the offer was
#: computed from a value that is no longer true. Derived from executable source, not judged:
#: `PricingService.quote()` takes `tipo_vehiculo` and the zone pair;
#: `AcceptanceContext.quote_inputs_unchanged()` compares candidate id, tipo and the zone
#: pair; `_catalog_tipo_for(marca, modelo)` takes no year. `anio` appears in none of them,
#: which is why a year-only correction leaves a booking proposal standing (Part D.4) — a
#: source fact, not a tolerance.
PROPOSAL_INVALIDATED_BY: dict = {
    ProposalFamily.SCHEDULING: PRICE_AFFECTING_FIELDS,
    ProposalFamily.BOOKING_FLOW: PRICE_AFFECTING_FIELDS,
    ProposalFamily.VEHICLE: frozenset(),
    ProposalFamily.LOCALITY: frozenset(),
    ProposalFamily.INSPECTABILITY: frozenset(),
}


def proposal_is_active(state, field: str) -> bool:
    """Is THIS proposal outstanding right now?

    The canonical model carries no expiry column for any of these, so "active" is exactly
    "the field holds a value the legacy workflow treats as outstanding" — a non-blank string
    or a real `True`. Nothing here guesses from a timestamp: Part D.7 forbids a TTL, and a
    clock cannot distinguish an abandoned Flow from a slow customer.
    """
    value = getattr(state, field, None)
    if value is True:
        return True
    if isinstance(value, str) and value.strip():
        return True
    return False


def active_proposals(state) -> tuple:
    """`((field, family), ...)` for every outstanding legacy proposal, in audit order."""
    if state is None:
        return ()
    return tuple((f, fam) for f, fam in LEGACY_PROPOSALS if proposal_is_active(state, f))


def legacy_proposal_active(state) -> Optional[str]:
    """The first outstanding legacy proposal, or None. For callers that want one name."""
    found = active_proposals(state)
    return found[0][0] if found else None


def corrected_field_names(corrected_fields) -> frozenset:
    """The canonical field names a patch moves."""
    out = set()
    for entry in (corrected_fields or ()):
        try:
            out.add(str(entry[0]))
        except (TypeError, IndexError):
            continue
    return frozenset(out)


def proposal_disposition(state, corrected_fields) -> dict:
    """What this correction does to every outstanding legacy proposal.

    Returns `{"resolved": (...), "invalidated": (...), "conflicting": (...)}` over FIELD
    names. This is the whole of Part D's policy, in one pure place:

      resolved     the correction answers the question the proposal asked
      invalidated  the correction moves a value the proposal was computed from
      conflicting  the proposal is outstanding, unanswered and still sound — the only case
                   where a correction and a legacy proposal genuinely compete for one "si"

    A field in `resolved` or `invalidated` is no longer a mutual-exclusion obstacle, because
    the caller withdraws it canonically in the same turn. Only `conflicting` may refuse.
    """
    moved = corrected_field_names(corrected_fields)
    resolved, invalidated, conflicting = [], [], []
    for field, family in active_proposals(state):
        if moved & PROPOSAL_RESOLVED_BY.get(family, frozenset()):
            resolved.append(field)
        elif moved & PROPOSAL_INVALIDATED_BY.get(family, frozenset()):
            invalidated.append(field)
        else:
            conflicting.append(field)
    return {"resolved": tuple(resolved), "invalidated": tuple(invalidated),
            "conflicting": tuple(conflicting)}


# -- Part D: a FINITE clarification budget, in state that already exists --------------
#
# Review finding G37FRR-04: the "one bounded clarification" wrote no state, so an abandoned
# `flow_booking_token` refused every future correction and re-sent one identical sentence
# forever. The budget below lives in the `pending_correction` JSONB column the G3-7F
# migration already added - no second migration, no new column - and `from_payload` keeps
# reading a marker as "no pending correction" because a marker carries no `pending_id`.

#: One clarification per cycle. A second unresolved conflict terminates instead of repeating.
CLARIFICATION_BUDGET = 1
_MARKER_KIND = "correction_clarification"


def clarification_marker(count: int, cycle_id: Any) -> dict:
    return {"kind": _MARKER_KIND, "count": int(count), "cycle_id": str(cycle_id or "")}


def clarifications_used(state, cycle_id: Any) -> int:
    """How many correction clarifications this cycle has already spent."""
    raw = getattr(state, "pending_correction", None)
    if not isinstance(raw, dict) or raw.get("kind") != _MARKER_KIND:
        return 0
    if str(raw.get("cycle_id") or "") != str(cycle_id or ""):
        return 0            # a new cycle is a new budget; the old marker is spent state
    try:
        return int(raw.get("count") or 0)
    except (TypeError, ValueError):
        return 0


def clarification_budget_remains(state, cycle_id: Any) -> bool:
    return clarifications_used(state, cycle_id) < CLARIFICATION_BUDGET


def classify_field(name: str) -> tuple[str, bool, bool]:
    """`(impact, identity_affecting, quote_invalidating)` for one canonical field."""
    if name in IDENTITY_FIELDS:
        return ImpactClass.AFFECTS_IDENTITY, True, name in QUOTE_INVALIDATING_FIELDS
    if name in PRICE_AFFECTING_FIELDS:
        return ImpactClass.AFFECTS_PRICE, False, name in QUOTE_INVALIDATING_FIELDS
    if name in PRICE_NEUTRAL_FIELDS:
        return ImpactClass.NONE, False, False
    return ImpactClass.UNKNOWN, False, True       # unknown impact is treated as the worst


def classify_patch(corrected_fields) -> tuple[str, str, bool, bool]:
    """`(tier, price_impact, identity_impact, quote_invalidating)` for a whole patch.

    A patch is atomic: one Tier 2 entry makes the whole patch Tier 2. Splitting Wild Case B
    would leave a candidate at 2018/Palermo — a combination the customer never described,
    which `_build_quote_reply` would then assert with fresh authority.
    """
    if not corrected_fields:
        return CorrectionTier.HOLD, ImpactClass.NONE, False, False
    price = ImpactClass.NONE
    identity = False
    invalidating = False
    for name, _before, _after in corrected_fields:
        impact, is_identity, invalidates = classify_field(name)
        identity = identity or is_identity
        invalidating = invalidating or invalidates
        if impact == ImpactClass.UNKNOWN:
            price = ImpactClass.UNKNOWN
        elif impact == ImpactClass.AFFECTS_PRICE and price != ImpactClass.UNKNOWN:
            price = ImpactClass.AFFECTS_PRICE
        elif impact == ImpactClass.AFFECTS_IDENTITY and price == ImpactClass.NONE:
            price = ImpactClass.AFFECTS_PRICE   # identity reaches price via the catalogue
    tier = (CorrectionTier.IMMEDIATE
            if price == ImpactClass.NONE and not identity and not invalidating
            else CorrectionTier.PROVISIONAL)
    return tier, price, identity, invalidating


def fingerprint(target_candidate_id, corrected_fields, cycle_id) -> str:
    """A stable identity for exactly this proposal against exactly this row.

    Confirmation must match it, so a re-delivered affirmation cannot apply a patch twice and
    an affirmation cannot apply a patch the customer never saw.
    """
    payload = json.dumps(
        {"candidate": target_candidate_id, "cycle": cycle_id,
         "fields": sorted((str(f), _jsonable(b), _jsonable(a))
                          for f, b, a in (corrected_fields or ()))},
        sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


@dataclass(frozen=True)
class PendingCorrection:
    """A provisional canonical patch. PROPOSED state — never canonical truth.

    Built by the reconciler, never by the semantic layer: the interpreter supplies evidence,
    and `target_candidate_id` is chosen here from canonical state.
    """
    pending_id: str
    thread_id: Optional[int] = None
    turn_id: Optional[str] = None
    target_candidate_id: Optional[int] = None
    grounding_basis: str = "NONE"
    relation: Optional[str] = None
    target_scope: Optional[str] = None
    corrected_fields: tuple = ()
    tier: str = CorrectionTier.PROVISIONAL
    price_impact: str = ImpactClass.UNKNOWN
    identity_impact: bool = False
    quote_invalidating: bool = True
    evidence_refs: tuple = ()
    cycle_id: Optional[str] = None
    origin_message_ids: tuple = ()
    confirmation_message_id: Optional[int] = None
    created_at: Optional[str] = None
    status: str = PendingStatus.PENDING
    invalidation_reason: Optional[str] = None
    fingerprint: str = ""

    # ── construction ─────────────────────────────────────────────────────────
    @classmethod
    def build(cls, *, thread_id, turn_id, target_candidate_id, grounding_basis,
              relation, target_scope, corrected_fields, evidence_refs=(),
              cycle_id=None, origin_message_ids=()) -> "PendingCorrection":
        tier, price, identity, invalidating = classify_patch(corrected_fields)
        fields = tuple((str(f), _jsonable(b), _jsonable(a)) for f, b, a in corrected_fields)
        return cls(
            pending_id=str(uuid.uuid4()), thread_id=thread_id, turn_id=turn_id,
            target_candidate_id=target_candidate_id, grounding_basis=grounding_basis,
            relation=relation, target_scope=target_scope, corrected_fields=fields,
            tier=tier, price_impact=price, identity_impact=identity,
            quote_invalidating=invalidating,
            evidence_refs=tuple(r for r in evidence_refs if r),
            cycle_id=(str(cycle_id) if cycle_id is not None else None),
            origin_message_ids=tuple(str(m) for m in origin_message_ids if m),
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            status=PendingStatus.PENDING,
            fingerprint=fingerprint(target_candidate_id, fields, cycle_id))

    # ── persistence ──────────────────────────────────────────────────────────
    def to_payload(self) -> dict:
        return {
            "pending_id": self.pending_id, "thread_id": self.thread_id,
            "turn_id": self.turn_id, "target_candidate_id": self.target_candidate_id,
            "grounding_basis": self.grounding_basis, "relation": self.relation,
            "target_scope": self.target_scope,
            "corrected_fields": [{"field": f, "before": b, "proposed": a}
                                 for f, b, a in self.corrected_fields],
            "tier": self.tier, "price_impact": self.price_impact,
            "identity_impact": self.identity_impact,
            "quote_invalidating": self.quote_invalidating,
            "evidence_refs": list(self.evidence_refs), "cycle_id": self.cycle_id,
            "origin_message_ids": list(self.origin_message_ids),
            "confirmation_message_id": self.confirmation_message_id,
            "created_at": self.created_at, "status": self.status,
            "invalidation_reason": self.invalidation_reason,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_payload(cls, raw: Any) -> "Optional[PendingCorrection]":
        """Strict: a payload that is not a well-formed pending correction is not one.

        A malformed stored patch must not become a half-understood proposal, so anything
        that fails to parse reads as "no pending correction" and the turn holds.
        """
        if not isinstance(raw, dict):
            return None
        pid = raw.get("pending_id")
        fp = raw.get("fingerprint")
        status = raw.get("status")
        if not isinstance(pid, str) or not pid or not isinstance(fp, str) or not fp:
            return None
        if status not in PendingStatus.ALL:
            return None
        fields = []
        for entry in (raw.get("corrected_fields") or []):
            if not isinstance(entry, dict) or not isinstance(entry.get("field"), str):
                return None
            fields.append((entry["field"], entry.get("before"), entry.get("proposed")))
        tier = raw.get("tier")
        if tier not in CorrectionTier.ALL:
            return None
        reason = raw.get("invalidation_reason")
        if reason is not None and reason not in InvalidationReason.ALL:
            return None
        tid = raw.get("target_candidate_id")
        if tid is not None and not isinstance(tid, int):
            return None
        return cls(
            pending_id=pid, thread_id=raw.get("thread_id"), turn_id=raw.get("turn_id"),
            target_candidate_id=tid, grounding_basis=str(raw.get("grounding_basis")
                                                          or "NONE"),
            relation=raw.get("relation"), target_scope=raw.get("target_scope"),
            corrected_fields=tuple(fields), tier=tier,
            price_impact=str(raw.get("price_impact") or ImpactClass.UNKNOWN),
            identity_impact=bool(raw.get("identity_impact")),
            quote_invalidating=bool(raw.get("quote_invalidating", True)),
            evidence_refs=tuple(str(r) for r in (raw.get("evidence_refs") or [])),
            cycle_id=raw.get("cycle_id"),
            origin_message_ids=tuple(str(m) for m in (raw.get("origin_message_ids") or [])),
            confirmation_message_id=raw.get("confirmation_message_id"),
            created_at=raw.get("created_at"), status=status,
            invalidation_reason=reason, fingerprint=fp)

    # ── transitions (pure: they return a new object) ──────────────────────────
    def with_status(self, status: str,
                    reason: Optional[str] = None) -> "PendingCorrection":
        import dataclasses
        return dataclasses.replace(self, status=status, invalidation_reason=reason)

    def with_confirmation_message(self, message_id) -> "PendingCorrection":
        import dataclasses
        return dataclasses.replace(self, confirmation_message_id=message_id)

    @property
    def blocking(self) -> bool:
        """Does this patch block commercial progression while it is pending?"""
        return (self.status == PendingStatus.PENDING
                and (self.price_impact != ImpactClass.NONE
                     or self.identity_impact
                     or self.quote_invalidating))

    @property
    def writer_patch(self) -> dict:
        """The one `_apply_candidate` call a confirmed patch authorizes.

        No `status` key: a correction never moves focus, and the omission is what makes the
        writer's demotion loop structurally unreachable.
        """
        data = {"action": "update", "id": self.target_candidate_id}
        for name, _before, proposed in self.corrected_fields:
            data[name] = proposed
        return data


# ── the predicates the engine and G3-8 consult ────────────────────────────────

def load(state) -> "Optional[PendingCorrection]":
    """The thread's pending correction, or None. Never raises."""
    return PendingCorrection.from_payload(getattr(state, "pending_correction", None))


def legacy_proposal_active(state) -> Optional[str]:
    """The name of an active legacy confirmable proposal, or None.

    Mutual exclusion is enforced by refusing to arm, not by superseding: a bounded answer
    that leaves every legacy workflow exactly as it is.
    """
    for name in LEGACY_PROPOSAL_FIELDS:
        value = getattr(state, name, None)
        if isinstance(value, str) and value.strip():
            return name
        if value is True:
            return name
    return None


def has_blocking_pending_correction(state) -> tuple[bool, Optional[str]]:
    """`(blocking, reason)` — the contract G3-8 must enforce at every early-return gate.

    G3-7F supplies the state and this predicate. Enforcement at the deterministic gates is
    G3-8's work (`G37BR-02`), so a True here does not yet mean a gate respects it.
    """
    pending = load(state)
    if pending is None or not pending.blocking:
        return False, None
    return True, (f"pending_correction:{pending.tier}:"
                  f"price={pending.price_impact}:identity={pending.identity_impact}:"
                  f"quote_invalidating={pending.quote_invalidating}")


def confirmation_dispatch_proven(pending, confirmation_row, *,
                                 thread_id=None) -> tuple[bool, Optional[str]]:
    """Did the confirmation question actually reach Meta, and is it STILL accepted?

    G3-7F-R2, review finding G37FRR-08. G3-7F compared `confirmation_message_id` against the
    id of the latest outbound row and never looked at the row itself. That fails closed for a
    BLOCKED or FAILED send — the id is absent or stale — but not for the case that matters
    most: a row accepted by Meta and *later* marked `failed` by a status webhook. The id
    still matched, so authority survived a message the customer never received.

    Every clause reads the canonical outbound record. The status test is a POSITIVE
    allow-list (`OUTBOUND_DISPATCH_ACCEPTED`), so `pending`, `blocked`, `failed`, `received`
    and anything unforeseen all deny authority rather than slipping through a deny-list.

    This proves DISPATCH ACCEPTANCE, which is the strongest fact the transport offers. It is
    not proof of human receipt and this function does not claim to be.
    """
    message_id = getattr(pending, "confirmation_message_id", None)
    if message_id is None:
        return False, InvalidationReason.CONFIRMATION_NOT_DISPATCHED
    if confirmation_row is None:
        return False, InvalidationReason.CONFIRMATION_NOT_DISPATCHED
    if getattr(confirmation_row, "id", None) != message_id:
        return False, InvalidationReason.CONFIRMATION_NOT_DISPATCHED
    if thread_id is not None and getattr(confirmation_row, "thread_id", None) != thread_id:
        return False, InvalidationReason.CONFIRMATION_NOT_DISPATCHED
    if getattr(confirmation_row, "direction", None) != "out":
        return False, InvalidationReason.CONFIRMATION_NOT_DISPATCHED
    wamid = getattr(confirmation_row, "wa_message_id", None)
    if not (isinstance(wamid, str) and wamid.strip()):
        return False, InvalidationReason.CONFIRMATION_NOT_DISPATCHED
    status = getattr(confirmation_row, "status", None)
    if not (isinstance(status, str) and status in OUTBOUND_DISPATCH_ACCEPTED):
        return False, InvalidationReason.CONFIRMATION_NOT_DISPATCHED
    return True, None


def confirmation_admissible(pending, *, state, candidates, cycle_id,
                            latest_outbound_message_id,
                            newer_correction_this_turn: bool,
                            confirmation_row=None, thread_id=None,
                            grounding_enabled: bool = True) -> tuple[bool, Optional[str]]:
    """May a typed ACCEPT apply this patch? `(admissible, invalidation_reason)`.

    Every clause is canonical state or the outbound ledger. None of them is a word: the
    affirmation itself arrives as `AcceptanceSignal.ACCEPT`, and what it refers to is
    decided here by what is actually outstanding.

    G3-7F-R2 adds three admission clauses the independent review found missing:

      * `grounding_enabled` / `grounding_basis`  (G37FRR-05) — a patch armed on the v1
        resolver's target has no proven referent, so it may not be applied even if every
        other clause holds. Enforced here as well as at arming, because a patch can outlive
        the configuration that armed it.
      * `confirmation_row`                       (G37FRR-08) — the question's own outbound
        record is re-read and must still prove dispatch acceptance. Passing `None` therefore
        DENIES authority; a caller that cannot supply the row gets no confirmation.
      * `thread_id`                              — the row must belong to this conversation.

    Still unwired by design: G3-8 owns the commercial gates. This function is the authority
    primitive, certified in isolation.
    """
    if pending is None or pending.status != PendingStatus.PENDING:
        return False, None
    if newer_correction_this_turn:
        # A newer grounded correction supersedes; it may not also be confirmed in the turn
        # that armed it, or one message would both propose and accept.
        return False, InvalidationReason.NEWER_CORRECTION
    legacy = legacy_proposal_active(state)
    if legacy is not None:
        return False, InvalidationReason.LEGACY_PROPOSAL_ACTIVE
    if not grounding_enabled:
        return False, InvalidationReason.GROUNDING_NOT_ENABLED
    basis = str(getattr(pending, "grounding_basis", "") or "")
    if basis in ("", "NONE"):
        return False, InvalidationReason.TARGET_NOT_GROUNDED
    # The question must be the latest thing we said. If the bot has spoken since - a quote, a
    # slot, a Flow prompt, another question - an affirmation is ambiguous, and ambiguity here
    # is a wrong-car mutation. A turn that re-attaches the confirmation updates
    # `confirmation_message_id`, so re-asking inside one message keeps it admissible.
    if (pending.confirmation_message_id is None
            or latest_outbound_message_id is None
            or pending.confirmation_message_id != latest_outbound_message_id):
        return False, InvalidationReason.NOT_LATEST_QUESTION
    dispatched, dispatch_reason = confirmation_dispatch_proven(
        pending, confirmation_row, thread_id=thread_id)
    if not dispatched:
        return False, dispatch_reason
    if str(pending.cycle_id or "") != str(cycle_id or ""):
        return False, InvalidationReason.CYCLE_ENDED
    row = next((c for c in (candidates or [])
                if getattr(c, "id", None) == pending.target_candidate_id), None)
    if row is None:
        return False, InvalidationReason.TARGET_LOST
    for name, before, _proposed in pending.corrected_fields:
        current = _jsonable(getattr(row, name, None))
        if current != before:
            return False, InvalidationReason.BEFORE_VALUES_MOVED
    if pending.fingerprint != fingerprint(pending.target_candidate_id,
                                          pending.corrected_fields, pending.cycle_id):
        return False, InvalidationReason.FINGERPRINT_MISMATCH
    return True, None
