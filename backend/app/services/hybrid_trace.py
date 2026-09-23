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
    CANONICAL_PROPOSITIONS, CanonicalEffect, CanonicalSnapshot, Classification,
    ComparisonVerdict, DECISION_PURPOSE, DecisionSite, DomainVerdictAdmission,
    EvidenceSource,
    HybridDecisionTrace, PropositionComparison, ReconciliationEvidence, ResultKind,
    RuleEvidence, SemanticEvidence, SourceContribution, TRACE_VERSION,
    TRACE_VERSION_1_0, TRACE_VERSION_1_1, TRACE_VERSION_1_2,
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

#: Claim families whose string values the authenticated Inspector may display, under the
#: owner's visibility decision of 2026-09-22. The Inspector is an operational audit surface
#: for an authenticated operator: what a customer wants inspected, which car, and which
#: locality are the facts an operator needs in order to read a decision at all.
#:
#: What stays off this list is what identifies a person or opens a door: a phone number, an
#: email, an exact street address, a booking token, a secret, a raw model response. The
#: shape scrub below is the second line of that defence — a street address that arrives
#: inside an `inspection_location` claim is still withheld, because it is long and because
#: a locality name is not a street with a number.
DISPLAYABLE_STRING_FAMILIES = frozenset({
    "vehicle.make", "vehicle.model", "vehicle.category",
    "inspection_location", "customer_origin", "seller_location",
    "service_intent",
})

#: Shapes that must never be printed, whatever family they arrive in.
#:
#: Widening the family allowlist made this load-bearing rather than belt-and-braces. A
#: WAMID or a booking token pasted into a `vehicle.model` claim would previously have been
#: withheld because the family was not displayable; now the family is, so the shape has to
#: carry the refusal. Each alternative is a declared identifier or secret shape, not a
#: guess about language: a phone-length digit run, an email, a URL, a WhatsApp message id,
#: a booking token, a Meta or OpenAI key, or a PEM block. No vehicle, service or locality
#: name contains any of them.
_UNSAFE_VALUE = re.compile(
    r"\d{7,}"
    r"|@"
    r"|https?://"
    r"|wamid\."
    r"|bk_tok"
    r"|-----BEGIN"
    r"|EAA[A-Za-z0-9]{10,}"
    r"|sk-[A-Za-z0-9]{10,}",
    re.IGNORECASE)
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


# ── canonical equivalence, from resolvers this system already has ────────────
#
# The rule this whole section serves: the trace NEVER inspects raw customer wording to
# decide that two producers agree. It compares canonical identities, and where it cannot
# obtain one it says the comparison is unproven. Deliberately absent: phrase catalogues,
# synonym lists, fuzzy string matching, and any normalization table invented here.

def _vehicle_canonical(value: Any) -> Optional[str]:
    """The catalog's own identity for a vehicle value, or None.

    `vehicle_catalog.lookup_vehicle` is the resolver `reconcile_vehicle_identity` already
    treats as the authority on what a car is called; reading it is how "un doscientos ocho"
    projected to `Peugeot 208` and a catalogue hit on `208` become the same identity without
    the trace ever seeing either sentence. Pure, in-memory, no database, no network.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        from .vehicle_catalog import lookup_vehicle
        match = lookup_vehicle(value)
    except Exception:                      # a resolver failure is not an equivalence
        return None
    if match is None:
        return None
    return f"{match.marca} {match.modelo}".strip()


_RESOLVERS = {"vehicle_catalog": _vehicle_canonical}


def canonical_identity(claim_type: Optional[str], value: Any) -> tuple:
    """`(identity, basis)` for one structured value, or `(None, why-not)`.

    `identity` is a canonical form two producers can be compared on. `basis` names the
    existing resolver that supplied it, so the Inspector can show WHY two values were
    treated as the same thing rather than asking the reader to trust it.
    """
    spec = CANONICAL_PROPOSITIONS.get(str(claim_type or ""))
    if spec is None:
        return None, "not a canonical proposition"
    if value is None or value == "" or value == [] or value == {}:
        return None, "no value"
    resolver = _RESOLVERS.get(spec.get("resolver") or "")
    if resolver is not None:
        identity = resolver(value)
        if identity is not None:
            return identity, spec["resolver"]
        return None, f"{spec['resolver']} did not resolve the value"
    if spec.get("self_canonical"):
        # A boolean proposition is its own canonical form: `True` and `False` are not two
        # spellings of one thing, they are contradictory answers to one question. Excluding
        # bools here was correct while every self-canonical proposition was numeric; with
        # `quote_accepted` and `needs_human` it would leave a real contradiction unproven.
        if isinstance(value, bool):
            return ("true" if value else "false"), "boolean proposition"
        if isinstance(value, (int, float)):
            return str(value), "structured value is already canonical"
    return None, "no canonical resolver for this proposition"


def contributions_from_claims(claims) -> tuple:
    """Group the reconciler's input set by proven source. One entry per source."""
    grouped: dict = {}
    for claim in claims or ():
        producer = getattr(claim, "producer", None)
        source = source_of_producer(producer)
        bucket = grouped.setdefault(source, {
            "producer": [], "producer_version": [], "evidence_classes": [],
            "claim_types": [], "claim_ids": [], "value_keys": [], "polarities": [],
            "values": [], "canonical_values": [], "confidences": [], "withheld": False,
        })
        claim_type = getattr(claim, "claim_type", None)
        value = getattr(claim, "value", None)
        shown, withheld = display_value(claim_type, value)
        identity, _basis = canonical_identity(claim_type, value)
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
        bucket["canonical_values"].append(identity)
        bucket["confidences"].append(getattr(claim, "confidence", None))
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
            canonical_values=tuple(bucket["canonical_values"]),
            confidences=tuple(bucket["confidences"]),
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


def _claims_of(contribution, claim_type: str) -> tuple:
    """(value_key, canonical, display, polarity) for one source's claims of one type."""
    out = []
    empty = value_key(claim_type, None)
    for key, kind, polarity, shown, identity in zip(
            contribution.value_keys, contribution.claim_types, contribution.polarities,
            (contribution.values or ()) + (None,) * len(contribution.claim_types),
            (contribution.canonical_values or ()) + (None,) * len(contribution.claim_types)):
        if kind != claim_type or key == empty:
            continue
        out.append((key, identity, shown, polarity))
    return tuple(out)


def _verdict_for(left_claims, right_claims) -> tuple:
    """`(verdict, basis)` for one proposition between exactly two sources.

    Compatible requires PROOF, in one of two forms, and nothing else counts:

      * both sides resolve through the same existing resolver to the same canonical
        identity — how a value the interpreter phrased one way and a value the catalogue
        phrased another are recognised as one car without reading either sentence; or
      * the two structured value keys are exactly equal, which needs no resolver at all.

    Incompatible likewise requires proof: one side asserting what the other denies, or two
    canonical identities that both resolved and differ.

    Everything else is UNPROVEN. Two unequal strings that no resolver could place may be
    two spellings of one locality, and the trace has no way to tell — so it does not guess
    in either direction. Under-claiming is the only safe direction here.
    """
    def split(claims):
        return ({c for c in claims if c[3] != "NEGATED"},
                {c for c in claims if c[3] == "NEGATED"})

    left_yes, left_no = split(left_claims)
    right_yes, right_no = split(right_claims)

    # assertion against denial of the same value — provable, resolver or not
    if ({c[0] for c in left_yes} & {c[0] for c in right_no}
            or {c[0] for c in right_yes} & {c[0] for c in left_no}):
        return ComparisonVerdict.INCOMPATIBLE, "one source denies what the other asserts"
    if not left_yes or not right_yes:
        return ComparisonVerdict.UNPROVEN, "one source asserted nothing about it"

    if {c[0] for c in left_yes} & {c[0] for c in right_yes}:
        return ComparisonVerdict.COMPATIBLE, "identical structured value"

    left_ids = {c[1] for c in left_yes if c[1]}
    right_ids = {c[1] for c in right_yes if c[1]}
    if left_ids and right_ids:
        if left_ids & right_ids:
            return ComparisonVerdict.COMPATIBLE, "same canonical identity"
        return ComparisonVerdict.INCOMPATIBLE, "different canonical identities"
    return (ComparisonVerdict.UNPROVEN,
            "no canonical identity could be established for both sides")


def compare_propositions(contributions) -> tuple:
    """Every canonical proposition at least two attributed producers spoke to.

    An empty result is the finding, not a gap: two producers sharing no proposition have
    not agreed about anything, and 1.1 called that AGREE.
    """
    parts = [c for c in contributions or ()
             if c.source in EvidenceSource.PARTICIPATING and _usable(c)]
    if len(parts) < 2:
        return ()
    out = []
    for claim_type in sorted(CANONICAL_PROPOSITIONS):
        speakers = [c for c in parts if _claims_of(c, claim_type)]
        if len(speakers) < 2:
            continue
        verdicts, bases = set(), []
        for index, left in enumerate(speakers):
            for right in speakers[index + 1:]:
                verdict, basis = _verdict_for(_claims_of(left, claim_type),
                                              _claims_of(right, claim_type))
                verdicts.add(verdict)
                if basis not in bases:
                    bases.append(basis)
        # One incompatible pair makes the proposition incompatible; otherwise every pair
        # must be proven compatible, or the proposition stays unproven.
        if ComparisonVerdict.INCOMPATIBLE in verdicts:
            verdict = ComparisonVerdict.INCOMPATIBLE
        elif verdicts == {ComparisonVerdict.COMPATIBLE}:
            verdict = ComparisonVerdict.COMPATIBLE
        else:
            verdict = ComparisonVerdict.UNPROVEN
        rendered = []
        for speaker in speakers:
            for key, identity, shown, polarity in _claims_of(speaker, claim_type):
                rendered.append((speaker.source, identity or shown, key, polarity))
        out.append(PropositionComparison(
            claim_type=claim_type,
            sources=tuple(dict.fromkeys(s.source for s in speakers)),
            canonical_by_source=tuple(rendered),
            verdict=verdict,
            basis="; ".join(bases) or None))
    return tuple(out)


def self_contradicting_sources(contributions) -> tuple:
    """Sources whose OWN claims contradict each other, on any single proposition.

    Recorded apart from every cross-producer verdict. "It is a Ka, it is not a Ka" from one
    parser is that parser being uncertain; rendering it as a conflict between engines would
    invent a disagreement that never happened.
    """
    out = []
    for contribution in contributions or ():
        if contribution.source not in EvidenceSource.PARTICIPATING:
            continue
        for claim_type in dict.fromkeys(contribution.claim_types):
            claims = _claims_of(contribution, claim_type)
            asserted = {c[0] for c in claims if c[3] != "NEGATED"}
            negated = {c[0] for c in claims if c[3] == "NEGATED"}
            if (asserted & negated) or len(asserted) > 1:
                if contribution.source not in out:
                    out.append(contribution.source)
                break
    return tuple(out)


def has_cross_producer_comparison(contributions) -> bool:
    """True when two or more DISTINCT producers addressed at least one shared proposition.

    The single precondition for any cross-producer statement. Counting claims is not a
    substitute: five claims from one parser are one producer, and two producers who never
    named the same field have not compared anything. Both mistakes were live before
    G3-1-R2 — one in the classifier, one in the scheduling verdict gate.
    """
    if len(set(participating_sources(contributions))) < 2:
        return False
    return bool(compare_propositions(contributions))


def admit_domain_verdict(contributions, propositions, domain_verdict) -> str:
    """Whether a domain comparator's finding may affect the classification, and why not.

    A domain comparator knows something the generic one does not — for scheduling, that a
    richer reading which covers every deterministic branch on the same resolved dates is
    an enrichment rather than a disagreement. That knowledge is admissible, under exactly
    the preconditions every other comparison obeys, and never over the top of a generic
    comparison that already proved the answer.

    Returns a `DomainVerdictAdmission`. Rejections are returned rather than swallowed so
    the row can show that two comparators disagreed.
    """
    if domain_verdict not in (ComparisonVerdict.COMPATIBLE, ComparisonVerdict.INCOMPATIBLE):
        return DomainVerdictAdmission.NOT_SUPPLIED
    if len(set(participating_sources(contributions))) < 2:
        return DomainVerdictAdmission.REJECTED_SINGLE_PRODUCER
    if not propositions:
        return DomainVerdictAdmission.REJECTED_NO_SHARED_PROPOSITION
    verdicts = {p.verdict for p in propositions}
    if ComparisonVerdict.INCOMPATIBLE in verdicts:
        return DomainVerdictAdmission.REJECTED_EVIDENCE_ALREADY_PROVEN
    if ComparisonVerdict.COMPATIBLE in verdicts and ComparisonVerdict.UNPROVEN not in verdicts:
        return DomainVerdictAdmission.REJECTED_EVIDENCE_ALREADY_PROVEN
    return DomainVerdictAdmission.ADMITTED


def classify_row(contributions, *, error_category: Optional[str] = None,
                 semantic_available: bool = False,
                 domain_verdict: Optional[str] = None) -> str:
    """What ONE reconciliation row proves. Derived from participation and from PROOF.

    1.1 derived the row from participation, which removed the polarity falsehood and left
    a second one standing: with two producers present it returned `AGREE` whenever no
    explicit conflict was found. Absence of conflict is not agreement — two producers who
    spoke about different fields cannot have agreed, and two who spoke about the same field
    in values nothing can reconcile have not been shown to.

    `AGREE` requires a shared canonical proposition, proven compatible. Every weaker
    outcome has its own name, and none of them is agreement.

    **1.3-R2 — a domain comparator is heard last, never first.** G3-1 let a supplied
    verdict short-circuit ahead of `compare_propositions`, which meant it could promote
    parallel evidence to `AGREE` and could override a proven incompatibility. The
    pre-condition ladder below is now evaluated in full FIRST, and the domain verdict is
    consulted only in the one place where it can add knowledge: a shared proposition whose
    generic comparison came back `UNPROVEN`. `admit_domain_verdict` records every
    rejection so a disagreement between the two comparators stays visible.

    An action-authority result — `ALLOW`, `HOLD`, `ACCEPT` — is never an input here and
    cannot be: this function receives contributions and a comparison verdict, nothing else.
    """
    if error_category:
        return Classification.ERROR
    present = participating_sources(contributions)
    distinct = set(present)

    # 1. no usable evidence at all
    if not distinct:
        return Classification.NOT_ROUTED if semantic_available else Classification.NO_EVIDENCE

    # 2. exactly one producer — no verdict can invent the second one
    if len(distinct) == 1:
        if self_contradicting_sources(contributions):
            return Classification.AMBIGUOUS_EVIDENCE
        return Classification.SINGLE_PRODUCER

    # 3. two or more producers: what, if anything, did they both address?
    propositions = compare_propositions(contributions)
    if not propositions:
        return Classification.PARALLEL_EVIDENCE

    verdicts = {p.verdict for p in propositions}

    # 4. the generic comparison decides whenever it can prove an answer
    if ComparisonVerdict.INCOMPATIBLE in verdicts:
        return Classification.CONFLICT
    if ComparisonVerdict.COMPATIBLE in verdicts and ComparisonVerdict.UNPROVEN not in verdicts:
        return Classification.AGREE

    # 5. only an unprovable shared proposition may be resolved by domain knowledge
    if admit_domain_verdict(contributions, propositions,
                            domain_verdict) == DomainVerdictAdmission.ADMITTED:
        if domain_verdict == ComparisonVerdict.INCOMPATIBLE:
            return Classification.CONFLICT
        return Classification.AGREE
    return Classification.COMPARISON_UNPROVEN


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


def logical_comparison_id(*, decision_site_id, claim_family, rule_id, rule_version,
                          contributions) -> str:
    """What this comparison IS, independent of where it happened to run.

    Inputs are all semantic: the decision site the caller named, the canonical proposition,
    the rule and its version, and the SORTED content hashes of every claim weighed. Claim
    ids are content hashes, so the same claims produce the same id on any deployment, in
    any iteration order, however the rows are arranged on the page.

    Deliberately absent: the row's position in the turn. 1.1 mixed an ordinal in here, so
    inserting an unrelated reconciliation earlier in a turn silently renamed every later
    comparison — an identity that changes when something unrelated moves is not an identity.
    Repeated executions of one logical comparison are separated by `occurrence_index`.
    """
    claim_ids = sorted(cid for c in contributions or () for cid in (c.claim_ids or ()))
    return inputs_digest("logical-comparison", decision_site_id, claim_family,
                         rule_id, rule_version, ",".join(claim_ids))


def reconciliation_from(record, claims=(), *, decision_site_id=None,
                        authority_result=None, domain_verdict=None,
                        canonical_effect=None, permitted_action=None,
                        business_outcome=None) -> ReconciliationEvidence:
    """One live reconciliation, told as WHICH source supplied WHAT to WHICH decision.

    `claims` is the input set the reconciler was handed; it carries each producer's own
    namespaced literal, which is the only durable proof of who contributed. `record` carries
    the rule, the outcome and the reason. Neither is read back into the turn.

    The row is classified here with `semantic_available=False`, because at this moment the
    interpreter may not have answered yet, and with `occurrence_index=0`, because whether
    this is a repeat is a fact about the turn. `finalize_rows()` settles both.
    """
    contributions = contributions_from_claims(claims)
    present = participating_sources(contributions)
    site = decision_site_id or None
    claim_family = str(getattr(record, "claim_type", "") or "")
    rule_id = getattr(record, "rule_id", None)
    rule_version = getattr(record, "rule_version", None)
    return ReconciliationEvidence(
        claim_family=claim_family,
        semantic_input=("PRESENT" if EvidenceSource.SEMANTIC in present else "ABSENT"),
        ce_input=("PRESENT" if (EvidenceSource.DETERMINISTIC in present
                                or EvidenceSource.CANONICAL_STATE in present) else "ABSENT"),
        classification=classify_row(contributions, domain_verdict=domain_verdict),
        rule_id=rule_id,
        rule_version=rule_version,
        outcome=getattr(record, "outcome", None),
        accepted=tuple(getattr(record, "evidence_ids", ()) or ()),
        rejected=(),
        reason_code=getattr(record, "reason", None),
        contract_version=TRACE_VERSION,
        logical_comparison_id=logical_comparison_id(
            decision_site_id=site, claim_family=claim_family, rule_id=rule_id,
            rule_version=rule_version, contributions=contributions),
        occurrence_index=0,
        decision_site_id=site,
        decision_purpose=DECISION_PURPOSE.get(site),
        participating_sources=tuple(dict.fromkeys(present)),
        not_routed_sources=(),
        source_evidence=contributions,
        compared_propositions=compare_propositions(contributions),
        self_contradiction_sources=self_contradicting_sources(contributions),
        information_state=getattr(record, "information_state", None),
        error_category=None,
        authority_result=authority_result,
        domain_verdict=domain_verdict,
        domain_verdict_admission=admit_domain_verdict(
            contributions, compare_propositions(contributions), domain_verdict),
        canonical_effect=canonical_effect,
        permitted_action=permitted_action,
        business_outcome=business_outcome)


def _semantic_produced_evidence(semantic) -> bool:
    """True when the interpreter ran and returned usable structured evidence."""
    status = getattr(semantic, "status", None) if semantic is not None else None
    claims = getattr(semantic, "produced_claims", ()) if semantic is not None else ()
    return status == "OK" and bool(claims)


def finalize_rows(reconciliations, semantic) -> tuple:
    """Settle the two questions that need the whole turn, and only those two.

    **Routing.** A row that received nothing is `NO_EVIDENCE` on its own account. It becomes
    `NOT_ROUTED` only when the interpreter provably produced claims for this turn that did
    not reach it — "evidence existed and was not routed" is a stronger statement than
    "nothing arrived", and it may only be made when both halves are proven. A row that DID
    have a participant keeps its classification and still names the unrouted source.

    **Occurrence.** How many times this logical comparison has already run in this turn.
    Positional by nature, which is exactly why it lives here and not in the identity.
    """
    available = _semantic_produced_evidence(semantic)
    seen: dict = {}
    out = []
    for row in reconciliations or ():
        contributions = tuple(getattr(row, "source_evidence", ()) or ())
        row.not_routed_sources = not_routed_for(contributions, available)
        row.classification = classify_row(
            contributions,
            error_category=getattr(row, "error_category", None),
            semantic_available=available,
            domain_verdict=getattr(row, "domain_verdict", None))
        row.domain_verdict_admission = admit_domain_verdict(
            contributions, compare_propositions(contributions),
            getattr(row, "domain_verdict", None))
        key = getattr(row, "logical_comparison_id", None)
        row.occurrence_index = seen.get(key, 0)
        seen[key] = row.occurrence_index + 1
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
        # `PARALLEL_EVIDENCE` is a PROVEN non-comparison — both producers spoke, about
        # different things — so it belongs with the uncompared rows, not the unknown ones.
        uncompared = [k for k in kinds
                      if k in (Classification.NO_EVIDENCE, Classification.NOT_ROUTED,
                               Classification.PARALLEL_EVIDENCE)]
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
        "parallel_evidence": count(Classification.PARALLEL_EVIDENCE),
        "comparison_unproven": count(Classification.COMPARISON_UNPROVEN),
        "ambiguous_evidence": count(Classification.AMBIGUOUS_EVIDENCE),
        "no_evidence": count(Classification.NO_EVIDENCE),
        "not_routed": count(Classification.NOT_ROUTED),
        "errored": count(Classification.ERROR),
        "legacy_unknown": count(Classification.LEGACY_PROVENANCE_UNAVAILABLE),
        #: Distinct logical comparisons, which is NOT the row count when one of them ran
        #: more than once in a turn.
        "distinct_comparisons": len({getattr(r, "logical_comparison_id", None)
                                     for r in rows}),
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
                 "not_routed_sources", "claim_family", "logical_comparison_id",
                 "domain_verdict")

    def __init__(self, raw: dict):
        raw = raw if isinstance(raw, dict) else {}
        self.claim_family = raw.get("claim_family")
        self.logical_comparison_id = raw.get("logical_comparison_id")
        self.domain_verdict = raw.get("domain_verdict")
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
                canonical_values=tuple(c.get("canonical_values") or ()),
                confidences=tuple(c.get("confidences") or ()),
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
                                              semantic_available=available,
                                              domain_verdict=row.domain_verdict)
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
            "logical_comparison_id": raw.get("logical_comparison_id"),
            "occurrence_index": raw.get("occurrence_index"),
            "compared_propositions": (None if legacy
                                      else list(raw.get("compared_propositions") or [])),
            "self_contradiction_sources": list(raw.get("self_contradiction_sources") or []),
            "captured_classification": raw.get("classification"),
            "effective_classification": row.classification,
            "reclassified": bool(raw.get("classification") != row.classification),
            "participating_sources": (None if legacy
                                      else list(raw.get("participating_sources") or ())),
            "not_routed_sources": list(row.not_routed_sources),
            "source_evidence": (None if legacy else list(raw.get("source_evidence") or [])),
            "information_state": raw.get("information_state"),
            "authority_result": raw.get("authority_result"),
            "domain_verdict": raw.get("domain_verdict"),
            "domain_verdict_admission": raw.get("domain_verdict_admission"),
            "canonical_effect": raw.get("canonical_effect"),
            "permitted_action": raw.get("permitted_action"),
            "business_outcome": raw.get("business_outcome"),
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
