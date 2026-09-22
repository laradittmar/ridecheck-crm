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
from datetime import datetime, timezone
from typing import Any, Optional

from ..schemas.hybrid_trace import (
    CanonicalSnapshot, Classification, HybridDecisionTrace, ReconciliationEvidence,
    ResultKind, RuleEvidence, SemanticEvidence, burst_hash, inputs_digest,
    token_fingerprint,
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
# Evidence classes, partitioned by which producer they attest to. A claim's class is the
# only honest way to say whether the semantic engine contributed to a reconciliation: the
# record's `evidence_ids` are opaque content hashes and cannot answer it.
SEMANTIC_CLASSES = frozenset({"SEMANTIC_INFERRED", "FUZZY_SUGGESTED"})
DETERMINISTIC_CLASSES = frozenset({"EXPLICIT_CUSTOMER", "DETERMINISTIC_EXTRACTED",
                                   "CATALOG_CONFIRMED", "SERVICE_COMPUTED",
                                   "HUMAN_CONFIRMED"})

# InformationState → how the two producers stood to each other.
_STATE_TO_CLASSIFICATION = {
    "BOTH": Classification.CONFLICT,
    "TRUE_ONLY": Classification.AGREE,
    "FALSE_ONLY": Classification.AGREE,
    "NEITHER": Classification.SEMANTIC_MISSING,
}


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


def reconciliation_from(record, claims=()) -> ReconciliationEvidence:
    """One live reconciliation, told as which producer said what.

    `claims` is the input set the reconciler was handed. Today every one of them is
    produced by CE, so `semantic_input` reads ABSENT — that is not a gap in the trace,
    it is the finding the inspector exists to make visible.
    """
    classes = set()
    for claim in claims or ():
        value = getattr(getattr(claim, "evidence_class", None), "value", None)
        if isinstance(value, str):
            classes.add(value)
    state = getattr(record, "information_state", None)
    return ReconciliationEvidence(
        claim_family=str(getattr(record, "claim_type", "") or ""),
        semantic_input=("PRESENT" if classes & SEMANTIC_CLASSES else "ABSENT"),
        ce_input=("PRESENT" if classes & DETERMINISTIC_CLASSES else "ABSENT"),
        classification=_STATE_TO_CLASSIFICATION.get(state, Classification.NO_RULE),
        rule_id=getattr(record, "rule_id", None),
        rule_version=getattr(record, "rule_version", None),
        outcome=getattr(record, "outcome", None),
        accepted=tuple(getattr(record, "evidence_ids", ()) or ()),
        rejected=(),
        reason_code=getattr(record, "reason", None))


def _semantic_produced_evidence(semantic) -> bool:
    """True when the interpreter ran and returned usable structured evidence."""
    status = getattr(semantic, "status", None) if semantic is not None else None
    claims = getattr(semantic, "produced_claims", ()) if semantic is not None else ()
    return status == "OK" and bool(claims)


def _row_condition(row, semantic) -> Optional[str]:
    """Why ONE reconciliation could not compare both producers — or None if it could.

    The correction this milestone exists for: a row classified `SEMANTIC_MISSING` on a turn
    whose interpreter succeeded and produced claims did NOT see a silent interpreter. Its
    claims went somewhere else. Saying "missing" there is false about the producer, and
    saying "conflict" about the turn is false about the comparison.
    """
    classification = getattr(row, "classification", None)
    if classification in Classification.COMPARED:
        return None
    if classification == Classification.SEMANTIC_MISSING:
        if _semantic_produced_evidence(semantic):
            return Classification.SEMANTIC_NOT_ROUTED
        return Classification.SEMANTIC_MISSING
    if classification in (Classification.SEMANTIC_ERROR, Classification.CE_MISSING,
                          Classification.NO_RULE, Classification.SEMANTIC_NOT_ROUTED):
        return classification
    return Classification.NO_RULE


def supporting_conditions(semantic, reconciliations) -> tuple:
    """Every reason a family could not be compared, in stable order, without duplicates."""
    out = []
    for row in reconciliations or ():
        condition = _row_condition(row, semantic)
        if condition is not None and condition not in out:
            out.append(condition)
    return tuple(out)


def classify(semantic: SemanticEvidence, ce_rules: tuple, reconciliations: tuple) -> str:
    """One headline classification for the turn.

    Precedence, and the reasoning behind it:

    1. `CONFLICT` **if and only if** some reconciliation row is itself `CONFLICT`. Nothing
       else may produce this word. The first deployed trace was headlined CONFLICT because
       the rule used to be "not all agreed"; a row that had nothing to compare was thereby
       reported as contradiction.
    2. `AGREE` only when at least one family was compared, every compared family agreed, and
       no family was left uncompared. Agreement is a claim about evidence that was actually
       weighed.
    3. `PARTIAL_RECONCILIATION` when at least one family agreed and at least one could not be
       compared. `supporting_conditions()` says which.
    4. When no family was compared at all, the single remaining reason IS the headline —
       `SEMANTIC_NOT_ROUTED`, `SEMANTIC_MISSING`, `SEMANTIC_ERROR` or `CE_MISSING`.
    5. With no reconciliation at all, fall back to the producer-level reading, where
       `NO_RULE` remains the honest answer when evidence existed and no authority owned it —
       the exact condition that made the failed Wild unreadable.
    """
    rows = tuple(reconciliations or ())
    if any(getattr(r, "classification", None) == Classification.CONFLICT for r in rows):
        return Classification.CONFLICT

    if rows:
        agreed = [r for r in rows if getattr(r, "classification", None) == Classification.AGREE]
        conditions = supporting_conditions(semantic, rows)
        if agreed and not conditions:
            return Classification.AGREE
        if agreed and conditions:
            return Classification.PARTIAL_RECONCILIATION
        # Nothing was compared. The one reason why is the truthful headline; when the rows
        # disagree about the reason, `PARTIAL_RECONCILIATION` is the honest summary and the
        # conditions list carries the detail.
        if len(conditions) == 1:
            return conditions[0]
        return Classification.PARTIAL_RECONCILIATION

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


class _Row:
    """A reconciliation row rehydrated from a stored payload, for read-time reclassification."""
    __slots__ = ("classification",)

    def __init__(self, classification):
        self.classification = classification


class _Sem:
    __slots__ = ("status", "produced_claims")

    def __init__(self, status, produced_claims):
        self.status = status
        self.produced_claims = tuple(produced_claims or ())


def effective_from_payload(payload) -> tuple:
    """Recompute (headline, conditions) from a STORED trace, without touching it.

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
        rows = tuple(_Row(r.get("classification")) for r in payload.get("reconciliation") or ())
        sem_raw = payload.get("semantic") or {}
        semantic = _Sem(sem_raw.get("status"), sem_raw.get("produced_claims"))
        ce_raw = payload.get("ce_evidence") or []
        ce_rules = tuple(RuleEvidence(rule_id=r.get("rule_id", ""),
                                      rule_version=r.get("rule_version", ""),
                                      value=r.get("value")) for r in ce_raw)
        return classify(semantic, ce_rules, rows), supporting_conditions(semantic, rows)
    except Exception:
        return None, ()


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
