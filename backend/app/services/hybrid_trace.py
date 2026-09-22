"""L4.7W5 Gate 1/2 — assemble and persist one Hybrid Decision Trace per turn.

Observability only. Nothing here decides anything, mutates canonical state, calls a model or
invokes a business action, and every path is fail-open: a trace that cannot be built or
written is silently dropped and the customer turn is unaffected.

**Why CE evidence is re-evaluated rather than instrumented.** The deterministic predicates
this adapter reports are pure functions of the burst text and an allowlisted state snapshot.
Instrumenting the router would mean touching the very code path this milestone must not
disturb; re-evaluating the same pure functions against the same inputs yields the same
values with zero routing risk. The snapshot is taken BEFORE the turn mutates anything, so a
rescue that withdraws a Flow token is still reported against the offer that existed when the
rule ran. `test_hti_*` pins the equivalence.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from ..schemas.hybrid_trace import (
    CanonicalSnapshot, Classification, DECISION_PURPOSE, DecisionSite, EvidenceSource,
    HybridDecisionTrace, ReconciliationEvidence, ResultKind, RuleEvidence,
    SemanticEvidence, SourceContribution, TRACE_VERSION, TRACE_VERSION_1_0,
    burst_hash, inputs_digest, source_of_producer, token_fingerprint,
)

logger = logging.getLogger(__name__)

CE_RULE_VERSION = "1.0"

# Business-facing rule identifiers. The dashboard binds to THESE, never to Python symbol
# names, so an internal rename can never break an operator's saved filter.
RULE_OFFER_OUTSTANDING = "scheduling.offer_outstanding"
RULE_OPTION_TAKEN = "scheduling.option_taken_this_turn"
RULE_EARLIEST_REJECTED = "scheduling.earliest_option_rejected"
RULE_REJECTION_SCOPE = "scheduling.rejection_is_about_scheduling"
RULE_UNIVERSAL_REJECTION = "scheduling.all_offered_options_rejected"
RULE_COMPETING_OBJECT = "scheduling.competing_object_named"
RULE_HUMAN_REQUEST = "handoff.human_requested"
RULE_PHONE_CALL_REQUEST = "handoff.phone_call_requested"
RULE_RESCUE_DECISION = "handoff.canonical_rescue"

#: The engine outcome meaning "a reply was required and none was produced".
#: Imported rather than repeated so the trace and the engine cannot drift apart.
ACTION_NO_REPLY_PRODUCED = "no_reply_produced"

ACTIONS_HANDOFF = frozenset({"skipped_human", "human_handoff_blocked"})
ACTIONS_BLOCKED = frozenset({"blocked_dispatch", "service_gate_blocked",
                             "inspectability_gate_blocked", "location_contradiction_blocked",
                             "vehicle_fuzzy_blocked", "location_proposal_blocked"})


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def snapshot_state(state, lead) -> CanonicalSnapshot:
    """Allowlisted canonical fields. Never serializes the ORM object."""
    def g(obj, name, default=None):
        return getattr(obj, name, default) if obj is not None else default

    raw_slots = g(state, "last_offered_slots")
    slots_count = None
    if raw_slots:
        try:
            parsed = json.loads(str(raw_slots))
            slots_count = len(parsed) if isinstance(parsed, list) else None
        except (ValueError, TypeError):
            slots_count = None
    token = g(state, "flow_booking_token")
    active_date = g(state, "active_requested_date")
    return CanonicalSnapshot(
        stage=g(state, "last_stage"),
        needs_human=bool(g(state, "needs_human", False)) if state is not None else None,
        lead_estado=g(lead, "estado"),
        lead_necesita_humano=(bool(g(lead, "necesita_humano", False))
                              if lead is not None else None),
        candidate_id=g(state, "current_focus_candidate_id"),
        revision_id=g(state, "current_revision_id"),
        zone_group=g(state, "home_zone_group"),
        zone_detail=g(state, "home_zone_detail"),
        offer_outstanding=bool(token or active_date or slots_count),
        active_requested_date=str(active_date) if active_date else None,
        offered_slots_count=slots_count,
        booking_token_present=bool(token),
        booking_token_fingerprint=token_fingerprint(token),
    )


def ce_evidence_for(texts, snapshot: CanonicalSnapshot, final_action: Optional[str]) -> tuple:
    """Re-evaluate the deterministic rules that explain a scheduling/rescue decision.

    Only the rules an operator needs to read the outcome — not every internal boolean.
    Import is local and defensive: an evidence adapter must never be able to break the
    module that imports it.
    """
    from . import conversation_engine as ce

    burst = [t for t in (texts or []) if isinstance(t, str)]
    digest = inputs_digest(*burst)
    offer = bool(snapshot.offer_outstanding)
    out: list[RuleEvidence] = []

    def add(rule_id: str, value: Any, note: Optional[str] = None,
            extra_digest: Optional[str] = None) -> None:
        out.append(RuleEvidence(rule_id=rule_id, rule_version=CE_RULE_VERSION, value=value,
                                inputs_digest=extra_digest or digest, note=note))

    try:
        add(RULE_OFFER_OUTSTANDING, offer,
            note="a day, visible slots or a live Flow token",
            extra_digest=inputs_digest(snapshot.active_requested_date,
                                       snapshot.offered_slots_count,
                                       snapshot.booking_token_present))
        add(RULE_OPTION_TAKEN, bool(ce.ConversationEngine._turn_took_an_option(burst)),
            note="a day or time named this turn")
        add(RULE_EARLIEST_REJECTED,
            bool(ce.ConversationEngine._earliest_option_rejected(burst)),
            note="an unsuitability or alternative-request predicate matched")
        add(RULE_REJECTION_SCOPE,
            bool(ce._rejection_is_about_scheduling(burst, offer)),
            note="the rejection is about the calendar, not another object")
        add(RULE_UNIVERSAL_REJECTION,
            bool(ce._rejects_every_offered_option(burst, offer)),
            note="a universal negative quantifier scoped to a scheduling object")
        from .scheduling_lexicon import names_competing_object
        add(RULE_COMPETING_OBJECT,
            bool(names_competing_object(ce._norm_lower(" ".join(burst)))),
            note="a non-scheduling object was named and wins")
        add(RULE_HUMAN_REQUEST, bool(ce._is_human_request(burst)),
            note="an affirmative request to speak to a person")
        add(RULE_PHONE_CALL_REQUEST, bool(ce._is_phone_call_request(burst)),
            note="an affirmative request to be phoned")
        add(RULE_RESCUE_DECISION, final_action in ACTIONS_HANDOFF or bool(final_action == "replied"
            and snapshot.needs_human),
            note="canonical rescue outcome for this turn")
    except Exception as exc:                       # evidence is never worth a failed turn
        logger.warning("HYBRID_TRACE ce_evidence degraded: %s", exc)
    return tuple(out)


# ── adapters from the live producers ─────────────────────────────────────────
#
# A claim's SOURCE comes from its `producer` namespace, which every producer sets as an
# explicit literal (`semantic:understand`, `ce:zone`, `canonical:deterministic_acceptance`).
# It does NOT come from `evidence_class`: a class says how strong a claim is, not who made
# it. Reading the class was a concrete falsehood, not a stylistic one — CE's own fuzzy
# catalog lookup produces `FUZZY_SUGGESTED` claims, and `FUZZY_SUGGESTED` was being counted
# as semantic participation in a decision the semantic engine never saw.

#: Claim families whose string values the authenticated Inspector is already permitted to
#: display. Locality is on this list because `CanonicalSnapshot.zone_detail` already renders
#: it on the same page for the same operator. Vehicle make, model and category are NOT: the
#: Inspector's current contract does not show them, and widening a privacy surface is not
#: this milestone's to do. Their values are withheld behind a typed reference instead.
DISPLAYABLE_STRING_FAMILIES = frozenset({
    "inspection_location", "customer_origin", "seller_location",
})

#: Shapes that must never be printed even inside an allowlisted family.
_UNSAFE_VALUE = re.compile(r"\d{7,}|@|https?://", re.IGNORECASE)
_MAX_DISPLAY_CHARS = 40


def value_key(claim_type: Optional[str], value: Any) -> str:
    """A short, non-reversible reference to one claim's value.

    Always present, displayed or not, so a reader can still see that two sources named the
    same thing — or different things — without the value ever leaving the database.
    """
    return inputs_digest("claim-value", claim_type, repr(value))


def display_value(claim_type: Optional[str], value: Any) -> tuple:
    """`(display, withheld)` for one claim value under the Inspector allowlist.

    Booleans and integers are printable whatever the family: neither can carry a name, an
    address, a phone number or a customer's words. Strings are printable only inside an
    allowlisted family, only when short, and only when they carry no digit run, no `@` and
    no URL — a locality never does, and a value that does is not a locality.
    """
    if value is None or value == "" or value == [] or value == {}:
        return None, False
    if isinstance(value, bool):
        return ("sí" if value else "no"), False
    if isinstance(value, int):
        return str(value), False
    if isinstance(value, str) and claim_type in DISPLAYABLE_STRING_FAMILIES:
        text = value.strip()
        if 0 < len(text) <= _MAX_DISPLAY_CHARS and not _UNSAFE_VALUE.search(text):
            return text, False
    return None, True


def contributions_from_claims(claims) -> tuple:
    """Group the reconciler's input set by proven source. One entry per source."""
    grouped: dict = {}
    for claim in claims or ():
        producer = getattr(claim, "producer", None)
        source = source_of_producer(producer)
        bucket = grouped.setdefault(source, {
            "producer": [], "producer_version": [], "evidence_classes": [],
            "claim_types": [], "claim_ids": [], "value_keys": [], "polarities": [],
            "values": [], "withheld": False,
        })
        claim_type = getattr(claim, "claim_type", None)
        value = getattr(claim, "value", None)
        shown, withheld = display_value(claim_type, value)
        polarity = getattr(getattr(claim, "polarity", None), "value", None)
        modality = getattr(getattr(claim, "modality", None), "value", None)
        evidence_class = getattr(getattr(claim, "evidence_class", None), "value", None)
        if producer and producer not in bucket["producer"]:
            bucket["producer"].append(str(producer))
        version = getattr(claim, "producer_version", None)
        if version and version not in bucket["producer_version"]:
            bucket["producer_version"].append(str(version))
        if evidence_class and evidence_class not in bucket["evidence_classes"]:
            bucket["evidence_classes"].append(str(evidence_class))
        bucket["claim_types"].append(str(claim_type or ""))
        bucket["claim_ids"].append(str(getattr(claim, "claim_id", "") or ""))
        bucket["value_keys"].append(value_key(claim_type, value))
        bucket["polarities"].append("NEGATED" if (polarity == "NEGATED"
                                                  or modality == "NEGATED") else "ASSERTED")
        bucket["values"].append(shown)
        bucket["withheld"] = bucket["withheld"] or withheld

    out = []
    for source in EvidenceSource.ALL:
        bucket = grouped.get(source)
        if bucket is None:
            continue
        out.append(SourceContribution(
            source=source,
            producer=", ".join(bucket["producer"]) or None,
            producer_version=", ".join(bucket["producer_version"]) or None,
            evidence_classes=tuple(bucket["evidence_classes"]),
            claim_types=tuple(bucket["claim_types"]),
            claim_ids=tuple(bucket["claim_ids"]),
            value_keys=tuple(bucket["value_keys"]),
            polarities=tuple(bucket["polarities"]),
            values=tuple(bucket["values"]),
            withheld=bucket["withheld"]))
    return tuple(out)


def _usable(contribution) -> bool:
    """True when this source supplied at least one claim carrying a value.

    A claim with no value supports nothing — `information_state` already ignores it, and a
    participant that contributed nothing usable must not be counted as having participated.
    """
    for key, claim_type in zip(getattr(contribution, "value_keys", ()) or (),
                               getattr(contribution, "claim_types", ()) or ()):
        if key != value_key(claim_type, None):
            return True
    return False


def participating_sources(contributions) -> tuple:
    """The distinct, attributed sources that actually supplied usable evidence."""
    return tuple(c.source for c in contributions or ()
                 if c.source in EvidenceSource.PARTICIPATING and _usable(c))


def sources_conflict(contributions) -> bool:
    """True only for EXPLICIT incompatibility between two distinct participating sources.

    Compared per claim type, never across types: one call site hands the reconciler make,
    model, year and category together, and "make=Peugeot" does not contradict "year=2020".
    Two sources are incompatible when one asserts what the other denies, or when both
    assert values for the same claim type and share none.
    """
    parts = [c for c in contributions or ()
             if c.source in EvidenceSource.PARTICIPATING and _usable(c)]
    for index, left in enumerate(parts):
        for right in parts[index + 1:]:
            shared = set(left.claim_types) & set(right.claim_types)
            for claim_type in shared:
                def split(contribution):
                    asserted, negated = set(), set()
                    for key, kind, polarity in zip(contribution.value_keys,
                                                   contribution.claim_types,
                                                   contribution.polarities):
                        if kind != claim_type or key == value_key(kind, None):
                            continue
                        (negated if polarity == "NEGATED" else asserted).add(key)
                    return asserted, negated
                left_asserted, left_negated = split(left)
                right_asserted, right_negated = split(right)
                if (left_asserted & right_negated) or (right_asserted & left_negated):
                    return True
                if left_asserted and right_asserted and not (left_asserted & right_asserted):
                    return True
    return False


def classify_row(contributions, *, error_category: Optional[str] = None,
                 semantic_available: bool = False) -> str:
    """What ONE reconciliation row proves. Derived from participation, never from polarity.

    The whole correction is here. `InformationState` describes the polarity of the evidence
    about a claim type; it cannot say who supplied it, so it can never decide whether two
    engines agreed. It stays on the row as evidence, next to this label, not behind it.
    """
    if error_category:
        return Classification.ERROR
    present = participating_sources(contributions)
    if len(set(present)) >= 2:
        return (Classification.CONFLICT if sources_conflict(contributions)
                else Classification.AGREE)
    if len(set(present)) == 1:
        return Classification.SINGLE_PRODUCER
    # Nothing usable reached this call. Blaming a named producer here is exactly the
    # falsehood 1.0 shipped: `NEITHER` became SEMANTIC_MISSING on rows where CE had said
    # nothing either. NOT_ROUTED is claimed only when the evidence is provably elsewhere.
    return Classification.NOT_ROUTED if semantic_available else Classification.NO_EVIDENCE


def not_routed_for(contributions, semantic_available: bool) -> tuple:
    """Sources that produced evidence in this turn and did not reach this call.

    Only SEMANTIC can be established: the trace records whether the interpreter produced
    claims for the turn. Nothing in the record proves that a deterministic parser produced
    evidence it then failed to route, so nothing here claims it did.
    """
    if not semantic_available:
        return ()
    if EvidenceSource.SEMANTIC in participating_sources(contributions):
        return ()
    return (EvidenceSource.SEMANTIC,)


def semantic_evidence_from(provider, dispatch: Optional[str]) -> SemanticEvidence:
    """Read the turn's one interpretation WITHOUT waiting for it.

    The interpreter runs async in the deployed runtime, so at the moment the decision was
    taken the result may genuinely not exist yet. `PENDING` records that truthfully.
    Blocking here to obtain a nicer-looking trace would change the timing of the customer
    turn to improve its own observation, which is exactly the thing an inspector may not do.
    """
    if provider is None:
        return SemanticEvidence(status="ABSENT", dispatch=dispatch)
    try:
        if not provider.ready:
            return SemanticEvidence(status="PENDING", dispatch=dispatch)
        result = provider.get(0.0)
    except Exception as exc:
        return SemanticEvidence(status="ERROR", dispatch=dispatch,
                                error_category=type(exc).__name__)
    if result is None:
        return SemanticEvidence(status="ABSENT", dispatch=dispatch)
    ok = bool(getattr(result, "ok", False))
    ev, families = None, ()
    if ok and getattr(result, "evidence", None) is not None:
        try:
            ev = result.evidence.model_dump(mode="json")
        except Exception:
            ev = None
        try:
            families = tuple(dict.fromkeys(
                ref.split("[")[0] for ref, _ in result.evidence.iter_items()))
        except Exception:
            families = ()
    return SemanticEvidence(
        ok=ok, status=("OK" if ok else "ERROR"), dispatch=dispatch,
        model=getattr(result, "model", None),
        prompt_version=getattr(result, "prompt_version", None),
        schema_version=getattr(result, "schema_version", None),
        latency_ms=getattr(result, "latency_ms", None),
        total_tokens=getattr(result, "total_tokens", None),
        error_category=(None if ok else (getattr(result, "error", None) or "UNKNOWN")),
        evidence=ev, produced_claims=families)


def reconciliation_from(record, claims=(), *, decision_site_id=None,
                        ordinal: int = 0) -> ReconciliationEvidence:
    """One live reconciliation, told as WHICH source supplied WHAT to WHICH decision.

    `claims` is the input set the reconciler was handed; it carries each producer's own
    namespaced literal, which is the only durable proof of who contributed. `record` carries
    the rule, the outcome and the reason. Neither is read back into the turn.

    The row is classified here with `semantic_available=False`, because at this moment the
    interpreter may not have answered yet. `finalize_rows()` revisits that one question once
    the turn's semantic evidence is known, and nothing else about the row changes.
    """
    contributions = contributions_from_claims(claims)
    present = participating_sources(contributions)
    site = decision_site_id or None
    return ReconciliationEvidence(
        claim_family=str(getattr(record, "claim_type", "") or ""),
        semantic_input=("PRESENT" if EvidenceSource.SEMANTIC in present else "ABSENT"),
        ce_input=("PRESENT" if (EvidenceSource.DETERMINISTIC in present
                                or EvidenceSource.CANONICAL_STATE in present) else "ABSENT"),
        classification=classify_row(contributions),
        rule_id=getattr(record, "rule_id", None),
        rule_version=getattr(record, "rule_version", None),
        outcome=getattr(record, "outcome", None),
        accepted=tuple(getattr(record, "evidence_ids", ()) or ()),
        rejected=(),
        reason_code=getattr(record, "reason", None),
        contract_version=TRACE_VERSION,
        comparison_id=inputs_digest("comparison", site,
                                    getattr(record, "claim_type", None), ordinal,
                                    ",".join(sorted(
                                        cid for c in contributions
                                        for cid in c.claim_ids))),
        decision_site_id=site,
        decision_purpose=DECISION_PURPOSE.get(site),
        participating_sources=tuple(dict.fromkeys(present)),
        not_routed_sources=(),
        source_evidence=contributions,
        information_state=getattr(record, "information_state", None),
        error_category=None)


def _semantic_produced_evidence(semantic) -> bool:
    """True when the interpreter ran and returned usable structured evidence."""
    status = getattr(semantic, "status", None) if semantic is not None else None
    claims = getattr(semantic, "produced_claims", ()) if semantic is not None else ()
    return status == "OK" and bool(claims)


def finalize_rows(reconciliations, semantic) -> tuple:
    """Settle the one row question that needs the whole turn: did evidence exist elsewhere?

    A row that received nothing is `NO_EVIDENCE` on its own account. It becomes `NOT_ROUTED`
    only when the interpreter provably produced claims for this turn that did not reach it —
    "evidence existed and was not routed" is a stronger statement than "nothing arrived",
    and it may only be made when both halves are proven.

    A row that DID have a participant keeps its classification; the unrouted source is still
    named, because "one producer decided this while the other's evidence went elsewhere" is
    exactly what an operator needs to see and is not the same as agreement.
    """
    available = _semantic_produced_evidence(semantic)
    out = []
    for row in reconciliations or ():
        contributions = tuple(getattr(row, "source_evidence", ()) or ())
        row.not_routed_sources = not_routed_for(contributions, available)
        row.classification = classify_row(
            contributions,
            error_category=getattr(row, "error_category", None),
            semantic_available=available)
        out.append(row)
    return tuple(out)


def supporting_conditions(semantic, reconciliations) -> tuple:
    """Every reason a row could not be a proven comparison, in stable order, no duplicates.

    A compared row contributes nothing: there is no condition to report about a comparison
    that happened. Everything else names itself, so `PARTIAL_RECONCILIATION` is never
    printed without the reason beside it — printing it without one is how a reader ends up
    guessing, and guessing is how CONFLICT got onto the first deployed trace.
    """
    out = []
    for row in reconciliations or ():
        classification = getattr(row, "classification", None)
        if classification in Classification.COMPARED or classification is None:
            continue
        if classification not in out:
            out.append(classification)
        for source in getattr(row, "not_routed_sources", ()) or ():
            label = f"{source}_NOT_ROUTED"
            if classification != Classification.NOT_ROUTED and label not in out:
                out.append(label)
    return tuple(out)


def classify(semantic: SemanticEvidence, ce_rules: tuple, reconciliations: tuple) -> str:
    """One headline for the turn, derived from corrected row semantics.

    Precedence, and why each step is where it is:

    1. `CONFLICT` if and only if some row is a proven multi-source conflict. Nothing else
       may produce this word.
    2. `AGREE` only when at least one row is a proven multi-source agreement and no row is
       single-source, unrouted, empty, erroneous or legacy-unknown.
    3. `PARTIAL_RECONCILIATION` when something was proven and something was not.
    4. `TRACE_INCOMPLETE` when a row's truth cannot be established from the record at all.
       It sits ahead of `SINGLE_SOURCE_DECISION` deliberately: with an unreadable row on
       the turn, "one source decided this" is itself an unproven claim, and §9's last rule
       — do not state a stronger conclusion than the trace can carry — outranks its own
       ordering. Saying less is the only safe direction to be wrong in.
    5. `SINGLE_SOURCE_DECISION` when usable evidence from exactly one source drove every
       decision and none was ever compared.
    6. `NO_COMPARISON` when rows exist and none of them carried usable evidence at all.
    7. With no reconciliation recorded, the producer-level reading is all there is, and
       `NO_RULE` remains the honest answer when evidence existed and no authority owned it —
       the exact condition that made the failed Wild of 2026-09-17 unreadable.
    """
    rows = tuple(reconciliations or ())
    kinds = [getattr(r, "classification", None) for r in rows]

    if Classification.CONFLICT in kinds:
        return Classification.CONFLICT

    if rows:
        agreed = [k for k in kinds if k == Classification.AGREE]
        # An unrecognised row label is unknown, not "no comparison". A reader that meets a
        # vocabulary it does not have must say so, never resolve it to a cleaner answer.
        unknown = [k for k in kinds
                   if k in Classification.UNKNOWN or k not in Classification.ROW]
        single = [k for k in kinds if k == Classification.SINGLE_PRODUCER]
        uncompared = [k for k in kinds
                      if k in (Classification.NO_EVIDENCE, Classification.NOT_ROUTED)]
        if agreed and not (unknown or single or uncompared):
            return Classification.AGREE
        if agreed:
            return Classification.PARTIAL_RECONCILIATION
        if unknown:
            return Classification.TRACE_INCOMPLETE
        if single:
            return Classification.SINGLE_SOURCE_DECISION
        return Classification.NO_COMPARISON

    if semantic.status in ("ERROR", "TIMEOUT", "MALFORMED"):
        return Classification.SEMANTIC_ERROR
    ce_fired = any(bool(r.value) for r in ce_rules)
    if ce_fired and not semantic.produced_claims:
        return Classification.NO_RULE
    if not semantic.produced_claims:
        return Classification.SEMANTIC_MISSING
    if not ce_rules:
        return Classification.CE_MISSING
    return Classification.NO_RULE


def family_counts(reconciliations) -> dict:
    """Truthful counts for the Inspector. Families deduplicate; rows never do."""
    rows = tuple(reconciliations or ())
    kinds = [getattr(r, "classification", None) for r in rows]

    def count(*values):
        return sum(1 for k in kinds if k in values)

    return {
        "distinct_families": len({str(getattr(r, "claim_family", "") or "") for r in rows}),
        "comparison_rows": len(rows),
        "compared": count(*Classification.COMPARED),
        "agreed": count(Classification.AGREE),
        "conflicting": count(Classification.CONFLICT),
        "single_producer": count(Classification.SINGLE_PRODUCER),
        "no_evidence": count(Classification.NO_EVIDENCE),
        "not_routed": count(Classification.NOT_ROUTED),
        "errored": count(Classification.ERROR),
        "legacy_unknown": count(Classification.LEGACY_PROVENANCE_UNAVAILABLE),
    }


def badges_for(trace: HybridDecisionTrace, headline: str) -> tuple:
    """The labels an operator reads first. Headline classification always comes first.

    `RECONCILED` is badged like every other result kind so that "a reconciler decided this"
    and "a deterministic floor decided this" are told apart at a glance rather than by
    reading the table below. `SEMANTIC PENDING` is added separately because it is not a
    classification — the interpreter had simply not answered yet — and leaving it implicit
    was the difference between "the engine said nothing" and "the engine was not asked".
    """
    out = [headline.replace("_", " ")]
    # The conditions are part of the headline's meaning: PARTIAL RECONCILIATION without
    # "why" invites the reader to guess, and guessing is how CONFLICT got here.
    for condition in getattr(trace, "supporting_conditions", ()) or ():
        if condition != headline:
            out.append(str(condition).replace("_", " "))
    if trace.result_kind == ResultKind.DETERMINISTIC_FLOOR:
        out.append("DETERMINISTIC FLOOR")
    elif trace.result_kind in (ResultKind.RECONCILED, ResultKind.HANDOFF, ResultKind.BLOCKED,
                               ResultKind.CLARIFICATION, ResultKind.FALLBACK):
        out.append(trace.result_kind.replace("_", " "))
    if getattr(trace.semantic, "status", None) == "PENDING":
        out.append("SEMANTIC PENDING")
    return tuple(dict.fromkeys(out))


def result_kind_for(action: Optional[str], snapshot_before: CanonicalSnapshot,
                    snapshot_after: CanonicalSnapshot, answer_source: Optional[str],
                    ce_rules: tuple) -> str:
    """Name how the outcome was produced — a floor must never read as reconciliation."""
    became_human = bool(snapshot_after.needs_human) and not bool(snapshot_before.needs_human)
    rescued_by_floor = any(
        r.rule_id in (RULE_EARLIEST_REJECTED, RULE_UNIVERSAL_REJECTION,
                      RULE_REJECTION_SCOPE, RULE_HUMAN_REQUEST,
                      RULE_PHONE_CALL_REQUEST) and bool(r.value) for r in ce_rules)
    if action in ACTIONS_BLOCKED:
        return ResultKind.BLOCKED
    # Order matters. A floor rescue and a stand-down both surface as `skipped_human`, so
    # testing the action first would hide every floor behind the word HANDOFF — and which
    # authority produced the handoff is precisely what the failed Wild could not be told.
    if became_human and rescued_by_floor:
        return ResultKind.DETERMINISTIC_FLOOR
    if action == "skipped_human" or became_human:
        return ResultKind.HANDOFF
    # L4.7W5-NO-REPLY-ALERT-INTEGRITY. A turn that produced nothing is NO_ACTION, not a
    # fallback that answered. Checked before the answer-source reading because the AI
    # fallback did run — it simply returned nothing usable, and "CE_AI ran" is not the same
    # claim as "CE_AI answered".
    if action == ACTION_NO_REPLY_PRODUCED:
        return ResultKind.NO_ACTION
    if answer_source == "CE_AI":
        return ResultKind.FALLBACK
    if answer_source == "DETERMINISTIC_RULE":
        return ResultKind.DETERMINISTIC_FLOOR
    if action in ("replied", "flow_button_sent"):
        return ResultKind.RECONCILED
    return ResultKind.NO_ACTION


def build_trace(*, turn_id, thread_id, lead_id, deployment_sha, started_at, completed_at,
                duration_ms, ordered_message_ids, message_timestamps, burst_texts,
                semantic, ce_rules, reconciliations, before, after, action, detail,
                answer_source, outbound) -> HybridDecisionTrace:
    kind = result_kind_for(action, before, after, answer_source, ce_rules)
    # Rows are captured while the turn runs, when the interpreter may not have answered
    # yet. Settle the one question that needs the whole turn before anything reads them.
    reconciliations = finalize_rows(reconciliations, semantic)
    trace = HybridDecisionTrace(
        turn_id=turn_id, thread_id=thread_id, lead_id=lead_id,
        deployment_sha=deployment_sha, started_at=started_at, completed_at=completed_at,
        duration_ms=duration_ms,
        ordered_message_ids=tuple(ordered_message_ids or ()),
        message_timestamps=tuple(message_timestamps or ()),
        message_count=len(ordered_message_ids or ()),
        input_hash=burst_hash(burst_texts),
        semantic=semantic, ce_evidence=tuple(ce_rules),
        reconciliation=tuple(reconciliations),
        supporting_conditions=supporting_conditions(semantic, reconciliations),
        canonical_before=before, canonical_after=after,
        transition=(f"{before.stage or '-'} -> {after.stage or '-'}"
                    if before.stage != after.stage else None),
        allowed=action not in ACTIONS_BLOCKED,
        result_kind=kind, reason_code=detail,
        response_plan_kind=action, response_plan_source=answer_source,
        outbound_message_id=(outbound or {}).get("message_id"),
        outbound_path_id=(outbound or {}).get("path_id"),
        outbound_status=(outbound or {}).get("status"),
        outbound_wamid_tail=(outbound or {}).get("wamid_tail"),
    )
    trace.badges = badges_for(trace, classify(semantic, ce_rules, reconciliations))
    return trace


class _Sem:
    __slots__ = ("status", "produced_claims")

    def __init__(self, status, produced_claims):
        self.status = status
        self.produced_claims = tuple(produced_claims or ())


class _StoredRow:
    """A reconciliation row rehydrated from a stored payload.

    `source_evidence` is rebuilt into real contributions so the reader recomputes the row
    exactly as the writer did — from participation, not from a label it found lying there.
    """
    __slots__ = ("classification", "source_evidence", "error_category",
                 "not_routed_sources", "claim_family")

    def __init__(self, raw: dict):
        raw = raw if isinstance(raw, dict) else {}
        self.claim_family = raw.get("claim_family")
        self.error_category = raw.get("error_category")
        self.not_routed_sources = tuple(raw.get("not_routed_sources") or ())
        self.classification = raw.get("classification")
        self.source_evidence = tuple(
            SourceContribution(
                source=str(c.get("source") or EvidenceSource.UNATTRIBUTED),
                producer=c.get("producer"),
                producer_version=c.get("producer_version"),
                evidence_classes=tuple(c.get("evidence_classes") or ()),
                claim_types=tuple(c.get("claim_types") or ()),
                claim_ids=tuple(c.get("claim_ids") or ()),
                value_keys=tuple(c.get("value_keys") or ()),
                polarities=tuple(c.get("polarities") or ()),
                values=tuple(c.get("values") or ()),
                withheld=bool(c.get("withheld")))
            for c in (raw.get("source_evidence") or []) if isinstance(c, dict))


def _legacy_row_classification(raw: dict, semantic_available: bool) -> str:
    """The only honest reading of a 1.0 row.

    A 1.0 row records two booleans — did "semantic" contribute, did "CE" contribute — and
    nothing else about provenance. Two things follow.

    When BOTH read ABSENT, the row provably received nothing, and that is worth stating:
    `NO_EVIDENCE`, or `NOT_ROUTED` when the interpreter provably produced claims elsewhere
    in the same turn. Note what is NOT said — `SEMANTIC_MISSING`, the 1.0 label, blamed a
    producer for an absence that was nobody's in particular.

    When either reads PRESENT, the row is unreadable, and `LEGACY_PROVENANCE_UNAVAILABLE`
    says so. It cannot become `SINGLE_PRODUCER`, because 1.0's `semantic_input` is itself
    unreliable: CE's fuzzy catalog claims were classed as semantic participation. And it
    can never become `AGREE`, which is the label this correction exists to withdraw.
    """
    semantic_present = raw.get("semantic_input") == "PRESENT"
    ce_present = raw.get("ce_input") == "PRESENT"
    if semantic_present or ce_present:
        return Classification.LEGACY_PROVENANCE_UNAVAILABLE
    return (Classification.NOT_ROUTED if semantic_available
            else Classification.NO_EVIDENCE)


def is_legacy_row(raw) -> bool:
    """A row is 1.0 when it carries no participation record at all.

    Tested on the KEY, never on the value: an absent `participating_sources` is a row that
    never recorded participation, while an empty one is a row that recorded none. Reading
    the first as the second is precisely the silent reinterpretation 1.1 must not do.
    """
    return not (isinstance(raw, dict) and "participating_sources" in raw)


def rows_from_payload(payload) -> tuple:
    """Every reconciliation row of a stored trace, reclassified under the current rules.

    Returns real row objects whose `classification` is the EFFECTIVE one, so the API, the
    Inspector and the dashboard all read the same recomputation and cannot disagree.
    """
    if not isinstance(payload, dict):
        return ()
    sem_raw = payload.get("semantic") or {}
    available = _semantic_produced_evidence(
        _Sem(sem_raw.get("status"), sem_raw.get("produced_claims")))
    out = []
    for raw in payload.get("reconciliation") or ():
        if not isinstance(raw, dict):
            continue
        row = _StoredRow(raw)
        if is_legacy_row(raw):
            row.classification = _legacy_row_classification(raw, available)
            row.not_routed_sources = ()
        else:
            row.not_routed_sources = not_routed_for(row.source_evidence, available)
            row.classification = classify_row(row.source_evidence,
                                              error_category=row.error_category,
                                              semantic_available=available)
        out.append(row)
    return tuple(out)


def effective_from_payload(payload) -> tuple:
    """Recompute `(headline, conditions)` from a STORED trace, without touching it.

    A trace written before this correction carries the old headline in its row and in its
    `badges`. That record is forensic evidence and is never rewritten; instead the reader
    derives the truthful label from the same reconciliation rows the writer saw. A trace
    written after the correction recomputes to exactly what it already stored.

    Returns `(None, ())` when the payload cannot be read, so a caller falls back to the
    captured value rather than inventing one.
    """
    if not isinstance(payload, dict):
        return None, ()
    try:
        rows = rows_from_payload(payload)
        sem_raw = payload.get("semantic") or {}
        semantic = _Sem(sem_raw.get("status"), sem_raw.get("produced_claims"))
        ce_raw = payload.get("ce_evidence") or []
        ce_rules = tuple(RuleEvidence(rule_id=r.get("rule_id", ""),
                                      rule_version=r.get("rule_version", ""),
                                      value=r.get("value")) for r in ce_raw)
        return classify(semantic, ce_rules, rows), supporting_conditions(semantic, rows)
    except Exception:
        return None, ()


def row_summaries_from_payload(payload) -> tuple:
    """The reconciliation table the Inspector renders: stored facts + effective reading.

    Captured and effective are carried side by side on every row, never merged. A 1.0 row
    says so in `legacy`, and its stored label stays visible as `captured_classification`,
    because withdrawing a claim is not the same as deleting the record of having made it.
    """
    if not isinstance(payload, dict):
        return ()
    raw_rows = [r for r in (payload.get("reconciliation") or []) if isinstance(r, dict)]
    out = []
    for raw, row in zip(raw_rows, rows_from_payload(payload)):
        legacy = is_legacy_row(raw)
        out.append({
            "claim_family": raw.get("claim_family"),
            "decision_site_id": raw.get("decision_site_id"),
            "decision_purpose": raw.get("decision_purpose"),
            "comparison_id": raw.get("comparison_id"),
            "captured_classification": raw.get("classification"),
            "effective_classification": row.classification,
            "reclassified": bool(raw.get("classification") != row.classification),
            "participating_sources": (None if legacy
                                      else list(raw.get("participating_sources") or ())),
            "not_routed_sources": list(row.not_routed_sources),
            "source_evidence": (None if legacy else list(raw.get("source_evidence") or [])),
            "information_state": raw.get("information_state"),
            "outcome": raw.get("outcome"),
            "rule_id": raw.get("rule_id"),
            "rule_version": raw.get("rule_version"),
            "reason_code": raw.get("reason_code"),
            "error_category": raw.get("error_category"),
            "legacy": legacy,
            # 1.0's own two booleans, kept verbatim so the record stays inspectable — and
            # marked legacy, because `semantic_input` was not reliably about the semantic
            # engine at the time it was written.
            "legacy_semantic_input": raw.get("semantic_input") if legacy else None,
            "legacy_ce_input": raw.get("ce_input") if legacy else None,
        })
    return tuple(out)


def counts_from_payload(payload) -> dict:
    """Truthful row and family counts for a stored trace."""
    return family_counts(rows_from_payload(payload))


def persist(db, trace: HybridDecisionTrace) -> bool:
    """Write one trace. Returns False on any failure and never raises.

    Uses its own transaction boundary so a trace failure cannot roll back the customer turn.
    """
    try:
        from ..models import HybridDecisionTraceRow
        row = HybridDecisionTraceRow(
            turn_id=trace.turn_id, thread_id=trace.thread_id, lead_id=trace.lead_id,
            deployment_sha=trace.deployment_sha, input_hash=trace.input_hash,
            message_count=trace.message_count, result_kind=trace.result_kind,
            # The CAPTURED classification: the corrected headline as computed at write
            # time. A trace written before L4.7W5-HYBRID-TRACE-LABEL-TRUTH holds the old
            # aggregation here and is never rewritten — readers derive the effective value
            # with `effective_from_payload()` instead.
            classification=(trace.badges[0].replace(" ", "_") if trace.badges else None),
            semantic_status=trace.semantic.status, payload=trace.to_payload(),
        )
        db.add(row)
        db.commit()
        return True
    except Exception as exc:
        logger.warning("HYBRID_TRACE_WRITE_FAILED turn_id=%s category=%s detail=%s",
                       getattr(trace, "turn_id", None), type(exc).__name__, exc)
        try:
            db.rollback()
        except Exception:
            pass
        return False
