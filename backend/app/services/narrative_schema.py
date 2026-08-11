"""M21.1.6 — Narrative interpretation schema and pure-function helpers.

Extends M21.1.5 field evidence by classifying facts in the current inbound burst
as current/historical/hypothetical/superseded so the CE can avoid redundant
questions and handle deferred-interest turns correctly.

NU-16, NU-17 safety contract: all parse functions return None on failure;
callers must fall back to existing deterministic behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# ── Status labels (NU-10) ─────────────────────────────────────────────────────
STATUS_CONFIRMED = "CONFIRMED"
STATUS_LIKELY = "LIKELY"
STATUS_UNCERTAIN = "UNCERTAIN"
STATUS_ABSENT = "ABSENT"
STATUS_SUPERSEDED = "SUPERSEDED"
STATUS_HYPOTHETICAL = "HYPOTHETICAL"
STATUS_HISTORICAL = "HISTORICAL"

_VALID_STATUSES = frozenset({
    STATUS_CONFIRMED, STATUS_LIKELY, STATUS_UNCERTAIN, STATUS_ABSENT,
    STATUS_SUPERSEDED, STATUS_HYPOTHETICAL, STATUS_HISTORICAL,
})
_ACTIVE_STATUSES = frozenset({STATUS_CONFIRMED, STATUS_LIKELY})

# ── Approved deferred-interest response copy (NU-6) ──────────────────────────
DEFERRED_RESPONSE_ES = (
    "Perfecto, cuando tengas algún auto en vista escribinos y "
    "te ayudamos con la revisión."
)

# ── Markers that signal complex narrative requiring AI even with complete evidence ─
_CORRECTION_MARKERS = [
    "en realidad", "me equivoqué", "quise decir", "no, es",
    "al final compré", "al final es", "corrijo", "perdón, es",
]
_HISTORICAL_MARKERS = [
    "pensaba comprar", "pensaba que", "antes era",
    "pero ahora", "ya no es", "estaba en tigre", "estaba en palermo",
    "estaba en villa", "estaba en san",
]
_HYPOTHETICAL_MARKERS = [
    "supongamos", "qué pasa si", "si estuviera", "si fuera",
    "y si el auto", "qué pasaría",
]

_COMPLEX_NARRATIVE_MARKERS = (
    _CORRECTION_MARKERS + _HISTORICAL_MARKERS + _HYPOTHETICAL_MARKERS
)


@dataclass(frozen=True)
class NarrativeFact:
    """Evidence for one narrative field with status and optional provenance."""
    value: Any
    status: str
    confidence: Optional[float] = None
    evidence: Optional[str] = None

    def is_active(self) -> bool:
        """True when the fact represents current, usable evidence (CONFIRMED or LIKELY)."""
        return self.status in _ACTIVE_STATUSES


@dataclass(frozen=True)
class NarrativeInterpretation:
    """AI-classified narrative understanding of the current inbound burst."""
    overall_intent: Optional[str] = None
    deferred_interest: bool = False
    vehicle_make_model: Optional[NarrativeFact] = None
    vehicle_year: Optional[NarrativeFact] = None
    vehicle_location: Optional[NarrativeFact] = None
    customer_origin: Optional[NarrativeFact] = None
    inspectability: Optional[NarrativeFact] = None
    asks_price: bool = False
    asks_faq: bool = False
    asks_schedule: bool = False

    def has_active_vehicle(self) -> bool:
        return self.vehicle_make_model is not None and self.vehicle_make_model.is_active()

    def has_active_location(self) -> bool:
        return self.vehicle_location is not None and self.vehicle_location.is_active()

    def has_active_inspectability(self) -> bool:
        return self.inspectability is not None and self.inspectability.is_active()

    def is_effectively_deferred(self) -> bool:
        """True when the burst overall means "not ready yet" and no active commercial
        vehicle evidence overrides it (NU-6, NU-7).

        A message like "Estoy buscando, pero ya tengo un Focus" is NOT deferred
        because has_active_vehicle() is True.
        """
        return self.deferred_interest and not self.has_active_vehicle()


def parse_narrative_interpretation(raw: Any) -> Optional[NarrativeInterpretation]:
    """Parse narrative fields from an AI response dict.

    Returns None on any parse failure — callers fall back to deterministic
    behavior (NU-16, NU-17). Never raises.
    """
    if not isinstance(raw, dict):
        return None
    try:
        return NarrativeInterpretation(
            overall_intent=raw.get("overall_intent") or raw.get("intent"),
            deferred_interest=bool(raw.get("deferred_interest", False)),
            vehicle_make_model=_parse_fact(raw.get("vehicle_make_model")),
            vehicle_year=_parse_fact(raw.get("vehicle_year")),
            vehicle_location=_parse_fact(raw.get("vehicle_location")),
            customer_origin=_parse_fact(raw.get("customer_origin")),
            inspectability=_parse_fact(raw.get("inspectability")),
            asks_price=bool(raw.get("asks_price", False)),
            asks_faq=bool(raw.get("asks_faq", False)),
            asks_schedule=bool(raw.get("asks_schedule", False)),
        )
    except Exception:
        return None


def narrative_needs_ai(snap: Any, current_turn_text: str) -> bool:
    """Return True when narrative AI interpretation adds value for this turn.

    Returns False (bypass) when both vehicle and location are already confirmed
    in the M21.1.5 resolver snapshot and no complex narrative markers are present
    in the text (NU-14).

    snap must support .vehicle_known() and .location_known()
    (FieldEvidenceSnapshot from field_evidence.py).
    """
    if snap.vehicle_known() and snap.location_known():
        lowered = current_turn_text.lower()
        for marker in _COMPLEX_NARRATIVE_MARKERS:
            if marker in lowered:
                return True
        return False
    return True


def _parse_fact(d: Any) -> Optional[NarrativeFact]:
    if not d or not isinstance(d, dict):
        return None
    status = d.get("status", STATUS_UNCERTAIN)
    if status not in _VALID_STATUSES:
        status = STATUS_UNCERTAIN
    return NarrativeFact(
        value=d.get("value"),
        status=status,
        confidence=_safe_float(d.get("confidence")),
        evidence=d.get("evidence") if isinstance(d.get("evidence"), str) else None,
    )


def _safe_float(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
