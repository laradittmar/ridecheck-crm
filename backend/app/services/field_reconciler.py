"""L4.7C.2 — vehicle and location authority.

The first authority cutover. Two claim families move from "whichever code path ran first
writes the field" to "a named rule decides, and records why":

    vehicle.make / model / year / category   →  reconcile.vehicle_identity.v1
    inspection_location (+ role)             →  reconcile.inspection_location.v1

Authority is claim-specific, never producer-specific. Neither the semantic interpreter nor
the deterministic parsers "win":

* **vehicle identity** — the customer's words say WHICH car; the **catalog** says what that
  car is called and what category it belongs to. A make the interpreter inferred is a
  suggestion until the catalog resolves it (L4.7C §5).
* **location** — the semantic layer reads the ROLE (is this where the car is, or where the
  customer lives?); the **zone resolver** says whether the locality exists and which zone it
  belongs to. A valid locality with the wrong role is not an inspection location.

Both authorities are injected as callables, so this module imports no ORM and no engine:
`catalog_lookup(text) -> VehicleMatch | None` and `zone_validator(locality) -> zone | None`.
It computes decisions; the caller decides whether it is allowed to act on them.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from ..schemas.claims import (
    ClaimEvidence,
    ClaimType,
    EvidenceClass,
    InformationState,
    ReconciliationOutcome,
    alternatives_for,
    information_state,
    risk_tier_for,
)
from ..schemas import turn_evidence as _te

logger = logging.getLogger(__name__)

VEHICLE_RULE_ID = "reconcile.vehicle_identity"
LOCATION_RULE_ID = "reconcile.inspection_location"
RULE_VERSION = "v1"

# Roles that can never become an inspection location, no matter how valid the locality is.
NON_INSPECTION_ROLES = ("CUSTOMER_ORIGIN", "SELLER_LOCATION", "UNKNOWN_LOCATION_ROLE")


@dataclass(frozen=True)
class FieldDecision:
    """What the reconciler decided about one canonical field. Carries no write authority."""
    outcome: ReconciliationOutcome
    value: Any = None                       # the canonical value, only when ACCEPT
    reason: str = ""
    rule_id: str = ""
    rule_version: str = RULE_VERSION
    information_state: str = InformationState.NEITHER.value
    evidence_ids: tuple[str, ...] = ()
    candidate_values: tuple[Any, ...] = ()
    depends_on: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.outcome is ReconciliationOutcome.ACCEPT

    def to_record(self, *, claim_type: str, cycle_id: Optional[str] = None,
                  revision_id: Optional[int] = None,
                  supersedes: tuple[str, ...] = ()) -> Any:
        """An append-only reconciliation record for this decision."""
        status_enum = _te.ReconciliationStatus
        status = (status_enum.ACCEPTED if self.outcome is ReconciliationOutcome.ACCEPT else
                  status_enum.NEEDS_CLARIFICATION
                  if self.outcome is ReconciliationOutcome.CLARIFY else
                  status_enum.CONFLICT_UNRESOLVED
                  if self.outcome is ReconciliationOutcome.NEEDS_HUMAN else
                  status_enum.DEFERRED)
        return _te.ReconciliationRecord(
            evidence_ref=claim_type, status=status, reason=self.reason,
            decided_by=f"reconciler:{self.rule_id}", canonical_value=self.value,
            claim_type=claim_type, evidence_ids=self.evidence_ids,
            candidate_values=self.candidate_values, rule_id=self.rule_id,
            rule_version=self.rule_version, information_state=self.information_state,
            outcome=self.outcome.value, risk_tier=risk_tier_for(claim_type).value,
            cycle_id=cycle_id, revision_id=revision_id, depends_on=self.depends_on,
            supersedes=supersedes, shadow=False)


def _of_type(claims: Iterable[ClaimEvidence], claim_type: str) -> list[ClaimEvidence]:
    return [c for c in claims if c.claim_type == claim_type]


def _first_value(claims: Iterable[ClaimEvidence], *classes: EvidenceClass) -> Optional[Any]:
    for evidence_class in classes:
        for claim in claims:
            if claim.evidence_class is evidence_class and claim.value not in (None, "", [], {}):
                return claim.value
    return None


# ── vehicle identity ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VehicleIdentity:
    marca: Optional[str] = None
    modelo: Optional[str] = None
    anio: Optional[int] = None
    tipo_vehiculo: Optional[str] = None


def _refine_without_identity(year_claims: list, category_claims: list,
                             evidence_ids: tuple) -> Optional[FieldDecision]:
    """Accept a lone year or category for a car whose identity is already established."""
    for claims_, field_name in ((year_claims, "anio"), (category_claims, "tipo_vehiculo")):
        if not claims_:
            continue
        state = information_state(claims_)
        if state is InformationState.TRUE_ONLY:
            value = _first_value(claims_, EvidenceClass.HUMAN_CONFIRMED,
                                 EvidenceClass.CATALOG_CONFIRMED,
                                 EvidenceClass.EXPLICIT_CUSTOMER,
                                 EvidenceClass.DETERMINISTIC_EXTRACTED,
                                 EvidenceClass.SEMANTIC_INFERRED)
            identity = VehicleIdentity(**{field_name: value})
            return FieldDecision(
                outcome=ReconciliationOutcome.ACCEPT, value=identity,
                reason=f"{field_name} refinement on an established identity",
                rule_id=VEHICLE_RULE_ID, information_state=state.value,
                evidence_ids=evidence_ids, candidate_values=(value,),
                depends_on=(ClaimType.VEHICLE_MODEL,))
        if state is InformationState.BOTH:
            return FieldDecision(
                outcome=ReconciliationOutcome.CLARIFY,
                reason=f"contradictory {field_name} evidence",
                rule_id=VEHICLE_RULE_ID, information_state=state.value,
                evidence_ids=evidence_ids,
                candidate_values=tuple(c.value for c in claims_ if c.value))
    return None


def reconcile_vehicle_identity(
    claims: Iterable[ClaimEvidence],
    *,
    catalog_lookup: Optional[Callable[[str], Any]] = None,
) -> FieldDecision:
    """Decide the canonical vehicle identity, with the catalog as the naming authority.

    A. explicit model + a unique catalog resolution → ACCEPT the catalog's normalised
       identity (make, model and category all come from the catalog, not from the model
       that suggested them);
    B. a make the interpreter inferred, with no catalog confirmation → cannot canonicalise;
    C. model and year are independent claims and both survive;
    D. an ambiguous catalog match → CLARIFY, never an arbitrary pick;
    E. two incompatible identities → BOTH → CLARIFY.
    """
    claims = list(claims)
    model_claims = _of_type(claims, ClaimType.VEHICLE_MODEL)
    make_claims = _of_type(claims, ClaimType.VEHICLE_MAKE)
    year_claims = _of_type(claims, ClaimType.VEHICLE_YEAR)
    identity_claims = model_claims + make_claims
    evidence_ids = tuple(c.claim_id for c in identity_claims + year_claims if c.claim_id)

    model_state = information_state(model_claims)
    if model_state is InformationState.BOTH:
        return FieldDecision(
            outcome=ReconciliationOutcome.CLARIFY,
            reason="two incompatible vehicle identities in scope",
            rule_id=VEHICLE_RULE_ID, information_state=model_state.value,
            evidence_ids=evidence_ids,
            candidate_values=tuple(c.value for c in model_claims if c.value)
            + alternatives_for(model_claims))

    stated_model = _first_value(model_claims, EvidenceClass.HUMAN_CONFIRMED,
                                EvidenceClass.EXPLICIT_CUSTOMER,
                                EvidenceClass.DETERMINISTIC_EXTRACTED,
                                EvidenceClass.CATALOG_CONFIRMED,
                                EvidenceClass.SEMANTIC_INFERRED)
    stated_make = _first_value(make_claims, EvidenceClass.HUMAN_CONFIRMED,
                               EvidenceClass.EXPLICIT_CUSTOMER,
                               EvidenceClass.DETERMINISTIC_EXTRACTED,
                               EvidenceClass.CATALOG_CONFIRMED)
    inferred_make = _first_value(make_claims, EvidenceClass.SEMANTIC_INFERRED)

    if stated_model is None and stated_make is None and inferred_make is None:
        # A refinement, not an identity decision: a year or a category arriving for a car
        # whose identity is already canonical. The identity is not re-litigated; the single
        # field is accepted on its own evidence, or held when it is contradictory.
        refinement = _refine_without_identity(year_claims,
                                              _of_type(claims, ClaimType.VEHICLE_CATEGORY),
                                              evidence_ids)
        if refinement is not None:
            return refinement
        return FieldDecision(outcome=ReconciliationOutcome.HOLD, reason="no vehicle evidence",
                             rule_id=VEHICLE_RULE_ID,
                             information_state=InformationState.NEITHER.value,
                             evidence_ids=evidence_ids)

    # The catalog is asked with the customer's own words, never with the model's suggestion.
    match = None
    if catalog_lookup is not None and stated_model:
        # Ask the catalog the way a customer names a car: make+model when both were said,
        # the model alone otherwise. A numeric model ("2008") only resolves in the second
        # form, so both are tried before concluding the catalog cannot place it.
        # An inferred make is not authority, but it IS a legitimate search hint: the catalog
        # is asked with it, and whatever the catalog answers is what becomes canonical. The
        # suggestion never creates the make; it only helps find the row (L4.7C §5).
        probes = []
        if stated_make:
            probes.append(f"{stated_make} {stated_model}")
        if inferred_make and inferred_make != stated_make:
            probes.append(f"{inferred_make} {stated_model}")
        probes.append(str(stated_model))
        for probe in probes:
            try:
                match = catalog_lookup(probe)
            except Exception as exc:                   # a catalog failure is not an identity
                logger.warning("L4.7C.2 catalog lookup failed for %r: %s", probe, exc)
                match = None
            if match is not None:
                break

    year_state = information_state(year_claims)
    year = None
    if year_state is InformationState.TRUE_ONLY:
        year = _first_value(year_claims, EvidenceClass.HUMAN_CONFIRMED,
                            EvidenceClass.EXPLICIT_CUSTOMER,
                            EvidenceClass.DETERMINISTIC_EXTRACTED,
                            EvidenceClass.SEMANTIC_INFERRED)
    elif year_state is InformationState.BOTH:
        # The year is ambiguous but the identity may still be decidable: keep the identity,
        # leave the year unset, and let the ambiguity be clarified on its own.
        year = None

    if match is not None:
        identity = VehicleIdentity(
            marca=getattr(match, "marca", None) or stated_make,
            modelo=getattr(match, "modelo", None) or stated_model,
            anio=(int(year) if isinstance(year, int) else None),
            tipo_vehiculo=getattr(match, "tipo_vehiculo", None))
        return FieldDecision(
            outcome=ReconciliationOutcome.ACCEPT, value=identity,
            reason="explicit model resolved uniquely by the catalog",
            rule_id=VEHICLE_RULE_ID, information_state=InformationState.TRUE_ONLY.value,
            evidence_ids=evidence_ids,
            candidate_values=(identity.modelo,),
            depends_on=(ClaimType.VEHICLE_MODEL, ClaimType.VEHICLE_MAKE,
                        ClaimType.VEHICLE_YEAR))

    # No catalog confirmation. An inferred make alone can never canonicalise (rule B).
    if stated_model is None:
        return FieldDecision(
            outcome=ReconciliationOutcome.CLARIFY,
            reason="only an inferred make is available; the catalog has not confirmed it",
            rule_id=VEHICLE_RULE_ID, information_state=InformationState.NEITHER.value,
            evidence_ids=evidence_ids,
            candidate_values=(inferred_make,) if inferred_make else ())

    return FieldDecision(
        outcome=ReconciliationOutcome.CLARIFY,
        reason="vehicle not resolvable by the catalog",
        rule_id=VEHICLE_RULE_ID, information_state=model_state.value,
        evidence_ids=evidence_ids,
        candidate_values=(stated_model,) + alternatives_for(model_claims))


# ── inspection location ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class InspectionLocation:
    zone_group: Optional[str] = None
    zone_detail: Optional[str] = None
    locality: Optional[str] = None


def reconcile_inspection_location(
    claims: Iterable[ClaimEvidence],
    *,
    zone_validator: Optional[Callable[[str], Any]] = None,
) -> FieldDecision:
    """Decide the canonical inspection location: role from language, zone from the resolver.

    A. a valid locality with no usable role → HOLD (nothing to write yet);
    B. a claimed inspection locality the resolver cannot validate → CLARIFY;
    C. two competing inspection localities → BOTH → CLARIFY;
    D. customer origin coexists and is never a conflict;
    E. an origin claim can never populate the inspection location.
    """
    claims = list(claims)
    inspection = _of_type(claims, ClaimType.INSPECTION_LOCATION)
    origin = _of_type(claims, ClaimType.CUSTOMER_ORIGIN)
    evidence_ids = tuple(c.claim_id for c in inspection if c.claim_id)

    if not inspection:
        # Rule E, made structural: an origin claim is not evidence about where the car is.
        return FieldDecision(
            outcome=ReconciliationOutcome.HOLD,
            reason=("customer origin only — an origin is never an inspection location"
                    if origin else "no inspection-location evidence"),
            rule_id=LOCATION_RULE_ID,
            information_state=InformationState.NEITHER.value)

    state = information_state(inspection)
    if state is InformationState.BOTH:
        return FieldDecision(
            outcome=ReconciliationOutcome.CLARIFY,
            reason="two competing inspection locations",
            rule_id=LOCATION_RULE_ID, information_state=state.value,
            evidence_ids=evidence_ids,
            candidate_values=tuple(c.value for c in inspection if c.value)
            + alternatives_for(inspection))
    if state is not InformationState.TRUE_ONLY:
        return FieldDecision(
            outcome=ReconciliationOutcome.HOLD,
            reason="no resolvable inspection locality",
            rule_id=LOCATION_RULE_ID, information_state=state.value,
            evidence_ids=evidence_ids)

    locality = _first_value(inspection, EvidenceClass.HUMAN_CONFIRMED,
                            EvidenceClass.EXPLICIT_CUSTOMER,
                            EvidenceClass.DETERMINISTIC_EXTRACTED,
                            EvidenceClass.CATALOG_CONFIRMED,
                            EvidenceClass.SEMANTIC_INFERRED)
    zone = None
    if zone_validator is not None and locality:
        try:
            zone = zone_validator(str(locality))
        except Exception as exc:
            logger.warning("L4.7C.2 zone validation failed for %r: %s", locality, exc)
            zone = None

    if zone is None:
        return FieldDecision(
            outcome=ReconciliationOutcome.CLARIFY,
            reason="locality not validated by the zone resolver",
            rule_id=LOCATION_RULE_ID, information_state=state.value,
            evidence_ids=evidence_ids, candidate_values=(locality,))

    value = InspectionLocation(zone_group=getattr(zone, "zone_group", None),
                               zone_detail=getattr(zone, "zone_detail", None) or locality,
                               locality=locality)
    return FieldDecision(
        outcome=ReconciliationOutcome.ACCEPT, value=value,
        reason="inspection role supported and locality validated by the zone resolver",
        rule_id=LOCATION_RULE_ID, information_state=state.value,
        evidence_ids=evidence_ids, candidate_values=(value.zone_detail,),
        depends_on=(ClaimType.INSPECTION_LOCATION,))
