"""L4.7A — map corpus truth into the typed TurnEvidence schema.

This lives in the test corpus, not in the backend: it exists to prove that every case in
`real_world_turns.jsonl` is representable by `app.schemas.turn_evidence` without dropping
meaning. It is also the reference for how a future interpreter should shape its output.

Nothing here touches the database or the conversation engine.
"""
from __future__ import annotations

import pathlib
import sys
from typing import Any, Optional

BACKEND = pathlib.Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.turn_evidence import (  # noqa: E402
    AcceptanceEvidence,
    AcceptanceSignal,
    Alternative,
    AmbiguityNote,
    BurstReconstruction,
    CorrectionEvidence,
    CorrectionRelation,
    EvidenceStatus,
    FaqIntentEvidence,
    LocationEvidence,
    LocationRole,
    Provenance,
    SchedulingPriority,
    SchedulingRequestEvidence,
    ServiceIntentEvidence,
    ServiceIntentKind,
    SourceKind,
    TurnEvidence,
    TurnRef,
    VehicleEvidence,
)

# Corpus vehicle values are "<Make> <Model…>"; the first token is the make.
_KNOWN_MAKES = {
    "peugeot", "volkswagen", "ford", "toyota", "chevrolet", "fiat", "renault", "honda",
    "nissan", "citroen", "citroën", "jeep", "kia", "hyundai",
}


def split_vehicle(value: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """'Peugeot 2008' → ('Peugeot', '2008'); 'Volkswagen Gol Trend' → ('Volkswagen', 'Gol Trend')."""
    if not value:
        return None, None
    parts = str(value).split()
    if parts and parts[0].lower() in _KNOWN_MAKES:
        return parts[0], " ".join(parts[1:]) or None
    return None, " ".join(parts) or None


def _prov(case: dict) -> Provenance:
    return Provenance(
        source_kind=SourceKind.SEMANTIC,
        interpreter="corpus:human-authored",
        model_version=None,                 # human truth has no model version
        source_message_ids=tuple(
            f"{case['id']}#{i}" for i in range(len(case["raw"]["messages"]))
        ),
    )


def _status(raw: str) -> EvidenceStatus:
    return EvidenceStatus(raw)


def corpus_case_to_turn_evidence(case: dict) -> TurnEvidence:
    """Represent one corpus case's expected meaning as typed TurnEvidence."""
    prov = _prov(case)
    items = {i["field"]: i for i in case["expected_turn_evidence"]}
    ordered = case["expected_turn_evidence"]

    service_intents: list[ServiceIntentEvidence] = []
    vehicles: list[VehicleEvidence] = []
    locations: list[LocationEvidence] = []
    faqs: list[FaqIntentEvidence] = []
    schedule: list[SchedulingRequestEvidence] = []
    corrections: list[CorrectionEvidence] = []
    ambiguities: list[AmbiguityNote] = []
    acceptance: Optional[AcceptanceEvidence] = None

    readiness_value = (items.get("readiness") or {}).get("value")

    for item in ordered:
        field = item["field"]
        value: Any = item.get("value")
        status = _status(item["status"])
        note = item.get("note")

        if status in (EvidenceStatus.AMBIGUOUS, EvidenceStatus.CONFLICT):
            ambiguities.append(AmbiguityNote(
                field=field, alternatives=(), reason=note, provenance=prov,
            ))

        if field == "service_intent":
            service_intents.append(ServiceIntentEvidence(
                kind=ServiceIntentKind.INSPECTION, value=value, status=status,
                reason=note, provenance=prov))
        elif field == "readiness":
            service_intents.append(ServiceIntentEvidence(
                field="readiness", kind=ServiceIntentKind.READINESS, value=value,
                status=status, reason=note, provenance=prov))
        elif field == "quote_request":
            service_intents.append(ServiceIntentEvidence(
                field="quote_request", kind=ServiceIntentKind.QUOTE_REQUEST, value=value,
                status=status, reason=note, provenance=prov))
        elif field == "customer_logistics_offer":
            service_intents.append(ServiceIntentEvidence(
                field="customer_logistics_offer", kind=ServiceIntentKind.LOGISTICS_OFFER,
                value=value, status=status, reason=note, provenance=prov))
        elif field == "vehicle":
            make, model = split_vehicle(value)
            year_item = items.get("vehicle_year") or {}
            vehicles.append(VehicleEvidence(
                value=value, make=make, model=model,
                year=(year_item.get("value") if isinstance(year_item.get("value"), int) else None),
                year_status=(_status(year_item["status"]) if year_item else None),
                status=status, reason=note, provenance=prov,
                mention_index=len(vehicles),
            ))
        elif field == "vehicle_superseded":
            make, model = split_vehicle(value)
            vehicles.append(VehicleEvidence(
                field="vehicle_superseded", value=value, make=make, model=model,
                is_superseded=True, status=status, reason=note, provenance=prov,
                mention_index=len(vehicles)))
        elif field == "vehicle_year":
            # Folded into the vehicle mention above — unless the case states a year with no
            # vehicle at all ("Es del 2015 no del 2014"), where the year IS the evidence.
            if not any(v.field == "vehicle" for v in vehicles) and isinstance(value, int):
                vehicles.append(VehicleEvidence(
                    value=None, year=value, year_status=status, status=status,
                    reason=note, provenance=prov, mention_index=len(vehicles)))
            continue
        elif field == "inspection_location":
            locations.append(LocationEvidence(
                value=value, locality=value, role=LocationRole.INSPECTION_LOCATION.value,
                status=status, reason=note, provenance=prov))
        elif field == "customer_origin":
            locations.append(LocationEvidence(
                value=value, locality=value, role=LocationRole.CUSTOMER_ORIGIN.value,
                status=status, reason=note, provenance=prov))
        elif field == "faq_topics":
            for topic in (value or []):
                faqs.append(FaqIntentEvidence(
                    value=topic, topic=topic, status=status, provenance=prov))
        elif field == "acceptance":
            # L4.7B.2B: the corpus states the stance itself. Booleans are still accepted so
            # that any older fixture or producer round-trips to the same meaning.
            if isinstance(value, str):
                try:
                    signal = AcceptanceSignal(value.strip().upper())
                except ValueError:
                    signal = AcceptanceSignal.UNKNOWN
            elif value is True:
                signal = AcceptanceSignal.ACCEPT
            elif value is False:
                signal = (AcceptanceSignal.HESITATE
                          if readiness_value == "HESITANT_OR_DEFERRED"
                          else AcceptanceSignal.REJECT)
            else:
                signal = AcceptanceSignal.UNKNOWN
            acceptance = AcceptanceEvidence(
                value={"ACCEPT": True, "REJECT": False, "HESITATE": False,
                       "FUTURE_INTENT": False}.get(signal.value),
                signal=signal, status=status, reason=note, provenance=prov)
        elif field == "scheduling_preference":
            for branch in (value or []):
                rank = int(branch.get("rank", len(schedule) + 1))
                schedule.append(SchedulingRequestEvidence(
                    value=branch,
                    priority=(SchedulingPriority.PRIMARY if rank == 1
                              else SchedulingPriority.FALLBACK),
                    day_expression=branch.get("day"),
                    time=branch.get("time"),
                    flexible_time=branch.get("time") is None,
                    rank=rank, status=status, reason=note, provenance=prov))
        elif field == "correction":
            relation = (CorrectionRelation.REPLACE_CANDIDATE
                        if "vehicle_superseded" in items
                        else CorrectionRelation.CORRECT_EXISTING)
            corrections.append(CorrectionEvidence(
                value=value, relation=relation,
                from_value=(items.get("vehicle_superseded") or {}).get("value"),
                to_value=(items.get("vehicle") or {}).get("value"),
                status=status, reason=note, provenance=prov))
        else:  # pragma: no cover — a new corpus field must fail loudly, not be dropped
            raise ValueError(f"{case['id']}: corpus field {field!r} has no schema mapping")

    return TurnEvidence(
        interpreter="corpus:human-authored",
        turn=TurnRef(
            corpus_case_id=case["id"],
            ordered_message_ids=tuple(
                f"{case['id']}#{i}" for i in range(len(case["raw"]["messages"]))
            ),
            reconstruction=BurstReconstruction.CORPUS_FIXTURE,
        ),
        service_intents=tuple(service_intents),
        vehicle_mentions=tuple(vehicles),
        location_mentions=tuple(locations),
        faq_intents=tuple(faqs),
        acceptance=acceptance,
        scheduling_requests=tuple(schedule),
        corrections=tuple(corrections),
        ambiguities=tuple(ambiguities),
    )


def turn_evidence_to_harness_items(evidence: TurnEvidence) -> list[dict]:
    """Flatten TurnEvidence back into the harness's `{field, value, status, role}` shape.

    Round-tripping through this function is how L4.7B will score a real interpreter with
    the L4.7E harness without either side knowing about the other.
    """
    out: list[dict] = []
    for intent in evidence.service_intents:
        out.append({"field": intent.field, "value": intent.value,
                    "status": intent.status.value})
    for vehicle in evidence.vehicle_mentions:
        out.append({"field": vehicle.field, "value": vehicle.value,
                    "status": vehicle.status.value,
                    "role": vehicle.role or "VEHICLE_OF_INTEREST"})
        if vehicle.year is not None or vehicle.year_status is not None:
            out.append({"field": "vehicle_year", "value": vehicle.year,
                        "status": (vehicle.year_status or vehicle.status).value})
    for location in evidence.location_mentions:
        field = ("inspection_location"
                 if location.role == LocationRole.INSPECTION_LOCATION.value
                 else "customer_origin")
        out.append({"field": field, "value": location.locality,
                    "status": location.status.value, "role": location.role})
    if evidence.faq_intents:
        out.append({"field": "faq_topics",
                    "value": [f.topic for f in evidence.faq_intents],
                    "status": evidence.faq_intents[0].status.value})
    if evidence.acceptance is not None:
        # L4.7B.2B: the harness scores the stance itself. Emitting the boolean here made
        # FUTURE_INTENT indistinguishable from REJECT.
        out.append({"field": "acceptance", "value": evidence.acceptance.signal.value,
                    "status": evidence.acceptance.status.value})
    if evidence.scheduling_requests:
        out.append({"field": "scheduling_preference",
                    "value": [{"day": s.day_expression, "time": s.time, "rank": s.rank}
                              for s in evidence.scheduling_requests],
                    "status": evidence.scheduling_requests[0].status.value})
    for correction in evidence.corrections:
        out.append({"field": "correction", "value": correction.value,
                    "status": correction.status.value})
    return out
