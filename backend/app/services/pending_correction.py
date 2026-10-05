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

    ALL = (NEWER_CORRECTION, BEFORE_VALUES_MOVED, TARGET_LOST, FOCUS_MOVED, CYCLE_ENDED,
           HUMAN_HANDOFF, CUSTOMER_REJECTED, NOT_LATEST_QUESTION,
           LEGACY_PROPOSAL_ACTIVE, FINGERPRINT_MISMATCH)


#: Thread-state fields that already represent a confirmable proposal. A new correction must
#: not coexist with any of them: an affirmation would then have two possible referents, and
#: the generic-affirmation invariant would be unprovable. Arming refuses instead, which is
#: bounded — it needs no change to the legacy workflows that own these.
LEGACY_PROPOSAL_FIELDS = (
    "pending_fuzzy_catalog_key",        # "¿Es un X?" — vehicle model confirmation
    "pending_location_proposal",        # "¿El auto está en X?" — locality confirmation
    "pending_turn_evidence_text",       # the stored burst a fuzzy acceptance replays
    "flow_booking_token",               # a live Meta Flow awaiting completion
    "last_offered_slots",               # slots the customer may accept
    "active_requested_date",            # a date under negotiation
    "vehicle_clarification_sent",
    "location_clarification_sent",
    "inspectability_clarification_sent",
)


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


def confirmation_admissible(pending, *, state, candidates, cycle_id,
                            latest_outbound_message_id,
                            newer_correction_this_turn: bool) -> tuple[bool, Optional[str]]:
    """May a typed ACCEPT apply this patch? `(admissible, invalidation_reason)`.

    Every clause is canonical state or the outbound ledger. None of them is a word: the
    affirmation itself arrives as `AcceptanceSignal.ACCEPT`, and what it refers to is
    decided here by what is actually outstanding.
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
    # The correction question must be the latest thing we said. If the bot has spoken since
    # — a quote, a slot, a Flow prompt, another question — an affirmation is ambiguous, and
    # ambiguity here is a wrong-car mutation. A turn that re-attaches the confirmation
    # updates `confirmation_message_id`, so re-asking keeps it admissible.
    if (pending.confirmation_message_id is None
            or latest_outbound_message_id is None
            or pending.confirmation_message_id != latest_outbound_message_id):
        return False, InvalidationReason.NOT_LATEST_QUESTION
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
