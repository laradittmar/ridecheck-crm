"""M21.1.5 — Central Field-Evidence Resolver.

Read-only. Does not mutate ctx, state, or any DB object.
Does not call external services, create candidates, or write to state.
ER-13: No new persistence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.vehicle_catalog import lookup_vehicle, fuzzy_lookup_vehicle

# ── Source labels (ER-2) ───────────────────────────────────────────────────────
SRC_CURRENT_TURN_EXACT = "CURRENT_TURN_EXACT"
SRC_CURRENT_TURN_FUZZY_HIGH = "CURRENT_TURN_FUZZY_HIGH"
SRC_CURRENT_TURN_CONFIRMED_FUZZY = "CURRENT_TURN_CONFIRMED_FUZZY"
SRC_FLOW = "FLOW"
SRC_WEBSITE_FORM = "WEBSITE_FORM"
SRC_CANDIDATE = "CANDIDATE"
SRC_THREAD_STATE = "THREAD_STATE"
SRC_REVISION = "REVISION"
SRC_AI_EXTRACTED = "AI_EXTRACTED"
SRC_DERIVED = "DERIVED"
SRC_NONE = "NONE"

# ── Inspectability status values (ER-9) ───────────────────────────────────────
INSP_DISASSEMBLED_BLOCKED = "DISASSEMBLED_BLOCKED"
INSP_ASSEMBLED_ACCESSIBLE = "ASSEMBLED_ACCESSIBLE"
INSP_UNRESOLVED_NON_RUNNING = "UNRESOLVED_NON_RUNNING"
INSP_HUMAN_REVIEW = "HUMAN_REVIEW"
INSP_UNKNOWN = "UNKNOWN"

_INTENT_PREPURCHASE = "PREPURCHASE_INSPECTION"

# ── Year regex ────────────────────────────────────────────────────────────────
_YEAR_RE = re.compile(r"\b(19[89]\d|20[0-2]\d)\b")

# ── Detection patterns ────────────────────────────────────────────────────────
# These are replicated from conversation_engine.py to avoid a circular import.
# The CE imports from this module; this module must not import from CE.

_CUSTOMER_ORIGIN_CAPTURE_RE = re.compile(
    r"\b(?:soy\s+de|vivo\s+en|estoy\s+en|vengo\s+de|me\s+encuentro\s+en)"
    r"\s+((?:\w+\s*){1,3})",
    re.IGNORECASE,
)

# Explicit vehicle-location clauses — each captures a locality (1-4 words).
# Pattern index 6 (alt-location "o puede ser X") is the contradiction signal.
_VEHICLE_LOCATION_CLAUSE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\bel\s+(?:auto|veh[ií]culo|coche|m[oó]vil)\s+(?:ahora\s+)?"
        r"est[aá](?:\s+ubicad[ao])?\s+en\s+((?:\w+\s*){1,4})",
        re.IGNORECASE,
    ),
    re.compile(r"\best[aá]\s+para\s+revisar\s+en\s+((?:\w+\s*){1,4})", re.IGNORECASE),
    re.compile(r"\bla\s+revisi[oó]n\s+ser[ií]a\s+en\s+((?:\w+\s*){1,4})", re.IGNORECASE),
    re.compile(r"\bhay\s+que\s+verlo\s+en\s+((?:\w+\s*){1,4})", re.IGNORECASE),
    re.compile(r"\blo\s+tienen\s+en\s+((?:\w+\s*){1,4})", re.IGNORECASE),
    re.compile(r"\best[aá]\s+ubicad[ao]\s+en\s+((?:\w+\s*){1,4})", re.IGNORECASE),
    re.compile(r"\bo\s+puede\s+(?:ser|estar)\s+(?:en\s+)?((?:\w+\s*){1,3})", re.IGNORECASE),
]

_LOCATION_UNCERTAINTY_RE = re.compile(
    r"\b(?:o\s+puede\s+(?:ser|estar)|no\s+s[eé]|no\s+estoy\s+segur[ao]|"
    r"tal\s+vez|quiz[aá]s)\b",
    re.IGNORECASE,
)

_INSP_DISASSEMBLED: list[re.Pattern[str]] = [
    re.compile(r"\bdesarmad[oa]\b", re.IGNORECASE),
    re.compile(r"\bdesmontad[oa]\b", re.IGNORECASE),
    re.compile(r"\bsin\s+motor\b", re.IGNORECASE),
    re.compile(r"\bmotor\s+(?:est[aá]\s+)?afuera\b", re.IGNORECASE),
    re.compile(r"\bmotor\s+desmontado\b", re.IGNORECASE),
    re.compile(r"\bsin\s+ruedas?\b", re.IGNORECASE),
    re.compile(r"\bpartes?\s+sueltas?\b", re.IGNORECASE),
    re.compile(r"\bpiezas?\s+desmontadas?\b", re.IGNORECASE),
]

_INSP_ASSEMBLED: list[re.Pattern[str]] = [
    re.compile(r"\best[aá]\s+armad[oa]\b", re.IGNORECASE),
    re.compile(r"\best[aá]\s+complet[oa]\b", re.IGNORECASE),
    re.compile(r"\bse\s+puede\s+revisar\b", re.IGNORECASE),
    re.compile(r"\bse\s+puede\s+acceder\b", re.IGNORECASE),
    re.compile(r"\bse\s+puede\s+abrir\b", re.IGNORECASE),
    re.compile(r"\bse\s+puede\s+levantar\b", re.IGNORECASE),
    re.compile(r"\btiene\s+todas?\s+las\s+ruedas?\b", re.IGNORECASE),
    re.compile(r"\bel\s+motor\s+est[aá]\s+puest[oa]\b", re.IGNORECASE),
    re.compile(r"\best[aá]\s+accesible\b", re.IGNORECASE),
]

_INSP_NONRUNNING: list[re.Pattern[str]] = [
    re.compile(r"\bno\s+arranca\b", re.IGNORECASE),
    re.compile(r"\bno\s+enciende\b", re.IGNORECASE),
    re.compile(r"\bno\s+prende\b", re.IGNORECASE),
    re.compile(r"\bbater[ií]a\s+muerta\b", re.IGNORECASE),
    re.compile(r"\best[aá]\s+parad[oa]\b", re.IGNORECASE),
    re.compile(r"\bno\s+funciona\b", re.IGNORECASE),
    re.compile(r"\bno\s+est[aá]\s+andando\b", re.IGNORECASE),
    re.compile(r"\bhay\s+que\s+empujarlo\b", re.IGNORECASE),
    re.compile(r"\bhay\s+que\s+remolcarlo\b", re.IGNORECASE),
]

_INSP_HISTORICAL: list[re.Pattern[str]] = [
    re.compile(r"\bestaba\b", re.IGNORECASE),
    re.compile(r"\bestuvo\b", re.IGNORECASE),
    re.compile(r"\bantes\b", re.IGNORECASE),
    re.compile(r"\bsi\s+estuviera\b", re.IGNORECASE),
    re.compile(r"\bhipot[eé]ticamente\b", re.IGNORECASE),
]


# ── Core dataclasses ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FieldEvidence:
    value: Any
    source: str
    confidence: str  # "HIGH" | "MEDIUM" | "LOW" | "NONE"
    confirmed: bool
    current_turn: bool


@dataclass(frozen=True)
class FieldEvidenceSnapshot:
    service_intent: FieldEvidence
    vehicle: FieldEvidence
    vehicle_year: FieldEvidence
    vehicle_category: FieldEvidence
    inspection_location: FieldEvidence
    customer_origin: FieldEvidence
    inspectability: FieldEvidence
    scheduling: FieldEvidence

    # ── Readiness helpers (Phase 6) ───────────────────────────────────────────

    def has_confirmed_vehicle(self) -> bool:
        """True when vehicle make/model is confirmed from any source."""
        return self.vehicle.confirmed and self.vehicle.value is not None

    def has_confirmed_location(self) -> bool:
        """True when inspection location is confirmed."""
        return (
            self.inspection_location.confirmed
            and self.inspection_location.value is not None
        )

    def vehicle_known(self) -> bool:
        """True iff tipo_vehiculo is confirmed and non-empty.

        Mirrors CE vehicle_known: requires tipo to gate flows/pricing.
        A brand-only hit (tipo_vehiculo='') does not satisfy this.
        """
        return (
            self.vehicle_category.confirmed
            and bool(self.vehicle_category.value)
        )

    def location_known(self) -> bool:
        """True when inspection location is confirmed."""
        return self.has_confirmed_location()

    def needs_vehicle(self) -> bool:
        """True when vehicle type is not yet confirmed."""
        return not self.vehicle_known()

    def needs_location(self) -> bool:
        """True when inspection location is not yet confirmed."""
        return not self.has_confirmed_location()

    def inspectability_allows_progress(self) -> bool:
        """True unless vehicle is actively blocked (assembled or unknown = allow)."""
        return self.inspectability.value in (INSP_ASSEMBLED_ACCESSIBLE, INSP_UNKNOWN)

    def service_intent_known(self) -> bool:
        """True when PREPURCHASE_INSPECTION intent is established (BR-1)."""
        return self.service_intent.confirmed and self.service_intent.value is not None

    def pricing_ready(self) -> bool:
        """All required evidence exists. Does NOT call PricingService."""
        return (
            self.vehicle_known()
            and self.location_known()
            and self.inspectability_allows_progress()
            and self.service_intent_known()
        )


# ── Internal helpers ──────────────────────────────────────────────────────────

_NONE_EVIDENCE = FieldEvidence(
    value=None, source=SRC_NONE, confidence="NONE", confirmed=False, current_turn=False
)


def _focus_from_ctx(ctx: Any, state: Any) -> Any:
    """Pure-function equivalent of ConversationEngine._focus_candidate."""
    candidates = getattr(ctx, "candidates", None) or []
    focus_id = getattr(state, "current_focus_candidate_id", None)
    if focus_id:
        for c in candidates:
            if getattr(c, "id", None) == focus_id:
                return c
    for c in candidates:
        if getattr(c, "status", None) == "current_focus":
            return c
    return candidates[0] if candidates else None


def _resolve_service_intent(state: Any) -> FieldEvidence:
    """ER-6: Respect BR-1. Intent established iff last_intent == PREPURCHASE_INSPECTION."""
    if getattr(state, "last_intent", None) == _INTENT_PREPURCHASE:
        return FieldEvidence(
            value=_INTENT_PREPURCHASE,
            source=SRC_THREAD_STATE,
            confidence="HIGH",
            confirmed=True,
            current_turn=False,
        )
    return _NONE_EVIDENCE


def _resolve_vehicle(ctx: Any, state: Any, turn: str) -> FieldEvidence:
    """ER-7: exact current-turn → fuzzy AUTO_ACCEPT → candidate → pending/none."""
    if turn:
        exact = lookup_vehicle(turn)
        if exact and getattr(exact, "marca", None):
            return FieldEvidence(
                value=(exact.marca, exact.modelo),
                source=SRC_CURRENT_TURN_EXACT,
                confidence="HIGH",
                confirmed=True,
                current_turn=True,
            )
        try:
            fz = fuzzy_lookup_vehicle(turn)
        except Exception:
            fz = None
        if fz and fz.outcome == "AUTO_ACCEPT" and fz.hit:
            return FieldEvidence(
                value=(fz.hit.marca, fz.hit.modelo),
                source=SRC_CURRENT_TURN_FUZZY_HIGH,
                confidence="HIGH",
                confirmed=True,
                current_turn=True,
            )

    focus = _focus_from_ctx(ctx, state)
    if focus and getattr(focus, "marca", None):
        return FieldEvidence(
            value=(focus.marca, focus.modelo),
            source=SRC_CANDIDATE,
            confidence="HIGH",
            confirmed=True,
            current_turn=False,
        )

    # Pending fuzzy proposal — unconfirmed (ER-5)
    pending = getattr(state, "pending_fuzzy_catalog_key", None)
    if pending:
        parts = pending.split("||", 1)
        marca = parts[0] if parts else None
        modelo = parts[1] if len(parts) > 1 else None
        return FieldEvidence(
            value=(marca, modelo),
            source=SRC_THREAD_STATE,
            confidence="MEDIUM",
            confirmed=False,
            current_turn=False,
        )

    return _NONE_EVIDENCE


def _resolve_vehicle_category(ctx: Any, state: Any, turn: str) -> FieldEvidence:
    """tipo_vehiculo: exact catalog authoritative; candidate fallback."""
    if turn:
        exact = lookup_vehicle(turn)
        if exact and getattr(exact, "tipo_vehiculo", None):
            return FieldEvidence(
                value=exact.tipo_vehiculo,
                source=SRC_CURRENT_TURN_EXACT,
                confidence="HIGH",
                confirmed=True,
                current_turn=True,
            )
        try:
            fz = fuzzy_lookup_vehicle(turn)
        except Exception:
            fz = None
        if fz and fz.outcome == "AUTO_ACCEPT" and fz.hit and fz.hit.tipo_vehiculo:
            return FieldEvidence(
                value=fz.hit.tipo_vehiculo,
                source=SRC_CURRENT_TURN_FUZZY_HIGH,
                confidence="HIGH",
                confirmed=True,
                current_turn=True,
            )

    focus = _focus_from_ctx(ctx, state)
    if focus and getattr(focus, "tipo_vehiculo", None):
        return FieldEvidence(
            value=focus.tipo_vehiculo,
            source=SRC_CANDIDATE,
            confidence="HIGH",
            confirmed=True,
            current_turn=False,
        )

    return _NONE_EVIDENCE


def _resolve_vehicle_year(ctx: Any, state: Any, turn: str) -> FieldEvidence:
    """Year: candidate is authoritative; current-turn hit + year text fills if missing."""
    focus = _focus_from_ctx(ctx, state)
    if focus and getattr(focus, "anio", None):
        return FieldEvidence(
            value=focus.anio,
            source=SRC_CANDIDATE,
            confidence="HIGH",
            confirmed=True,
            current_turn=False,
        )
    if turn:
        exact = lookup_vehicle(turn)
        if exact:
            m = _YEAR_RE.search(turn)
            if m:
                return FieldEvidence(
                    value=int(m.group(1)),
                    source=SRC_CURRENT_TURN_EXACT,
                    confidence="HIGH",
                    confirmed=True,
                    current_turn=True,
                )
    return _NONE_EVIDENCE


def _extract_vehicle_location_from_turn(turn: str) -> tuple[list[str], bool]:
    """Return (localities, has_contradiction) from explicit vehicle-location clauses."""
    localities: list[str] = []
    for pat in _VEHICLE_LOCATION_CLAUSE_PATTERNS[:-1]:
        m = pat.search(turn)
        if m:
            loc = m.group(1).strip()
            if loc and loc not in localities:
                localities.append(loc)
    alt_m = _VEHICLE_LOCATION_CLAUSE_PATTERNS[-1].search(turn)
    contradiction = bool(alt_m and _LOCATION_UNCERTAINTY_RE.search(turn))
    return localities, contradiction


def _resolve_inspection_location(ctx: Any, state: Any, turn: str) -> FieldEvidence:
    """ER-8: explicit vehicle clause > candidate zone > thread zone fallback."""
    if turn:
        localities, contradiction = _extract_vehicle_location_from_turn(turn)
        if contradiction or len(localities) >= 2:
            # SC17 / LR-8: same-turn contradiction — unresolved
            return FieldEvidence(
                value=None,
                source=SRC_NONE,
                confidence="LOW",
                confirmed=False,
                current_turn=True,
            )
        if localities:
            return FieldEvidence(
                value=localities[0],
                source=SRC_CURRENT_TURN_EXACT,
                confidence="HIGH",
                confirmed=True,
                current_turn=True,
            )

    # Focused candidate zone is authoritative (LR-3, ER-4)
    focus = _focus_from_ctx(ctx, state)
    if focus and getattr(focus, "zone_group", None):
        loc = getattr(focus, "zone_detail", None) or focus.zone_group
        return FieldEvidence(
            value=loc,
            source=SRC_CANDIDATE,
            confidence="HIGH",
            confirmed=True,
            current_turn=False,
        )

    # Thread state zone — legacy fallback (ER-4: only when no candidate/current evidence)
    home_group = getattr(state, "home_zone_group", None)
    home_detail = getattr(state, "home_zone_detail", None)
    if home_group or home_detail:
        loc = home_detail or home_group
        return FieldEvidence(
            value=loc,
            source=SRC_THREAD_STATE,
            confidence="MEDIUM",
            confirmed=bool(home_group),
            current_turn=False,
        )

    return _NONE_EVIDENCE


def _resolve_customer_origin(turn: str) -> FieldEvidence:
    """ER-8: customer origin is separated from inspection location."""
    if turn:
        m = _CUSTOMER_ORIGIN_CAPTURE_RE.search(turn)
        if m:
            locality = m.group(1).strip()
            return FieldEvidence(
                value=locality or None,
                source=SRC_CURRENT_TURN_EXACT,
                confidence="HIGH",
                confirmed=True,
                current_turn=True,
            )
    return _NONE_EVIDENCE


def _resolve_inspectability(state: Any, turn: str) -> FieldEvidence:
    """ER-9: current-turn explicit evidence beats persisted flag."""
    if turn:
        is_historical = any(p.search(turn) for p in _INSP_HISTORICAL)
        if not is_historical:
            if any(p.search(turn) for p in _INSP_ASSEMBLED):
                return FieldEvidence(
                    value=INSP_ASSEMBLED_ACCESSIBLE,
                    source=SRC_CURRENT_TURN_EXACT,
                    confidence="HIGH",
                    confirmed=True,
                    current_turn=True,
                )
            if any(p.search(turn) for p in _INSP_DISASSEMBLED):
                return FieldEvidence(
                    value=INSP_DISASSEMBLED_BLOCKED,
                    source=SRC_CURRENT_TURN_EXACT,
                    confidence="HIGH",
                    confirmed=True,
                    current_turn=True,
                )
            if any(p.search(turn) for p in _INSP_NONRUNNING):
                return FieldEvidence(
                    value=INSP_UNRESOLVED_NON_RUNNING,
                    source=SRC_CURRENT_TURN_EXACT,
                    confidence="MEDIUM",
                    confirmed=False,
                    current_turn=True,
                )

    # State flag: pending clarification means unresolved (R1-A / R1-B)
    if getattr(state, "inspectability_clarification_sent", False):
        return FieldEvidence(
            value=INSP_UNRESOLVED_NON_RUNNING,
            source=SRC_THREAD_STATE,
            confidence="LOW",
            confirmed=False,
            current_turn=False,
        )

    return FieldEvidence(
        value=INSP_UNKNOWN,
        source=SRC_NONE,
        confidence="NONE",
        confirmed=False,
        current_turn=False,
    )


def _resolve_scheduling(state: Any) -> FieldEvidence:
    """Scheduling is a read model from thread state (no current-turn detection)."""
    day = getattr(state, "preferred_day", None)
    time_ = getattr(state, "preferred_time", None)
    if day:
        return FieldEvidence(
            value={"day": day, "time": time_},
            source=SRC_THREAD_STATE,
            confidence="HIGH",
            confirmed=bool(time_),
            current_turn=False,
        )
    active = getattr(state, "active_requested_date", None)
    if active:
        return FieldEvidence(
            value={"active_requested_date": active},
            source=SRC_THREAD_STATE,
            confidence="MEDIUM",
            confirmed=False,
            current_turn=False,
        )
    return _NONE_EVIDENCE


# ── Public API ────────────────────────────────────────────────────────────────

def resolve_field_evidence(
    ctx: Any,
    state: Any,
    current_turn_text: str = "",
) -> FieldEvidenceSnapshot:
    """Assemble a read-only evidence snapshot from all available sources.

    ER-10: Does not mutate ctx, state, or any DB object.
    ER-13: No new persistence.

    Args:
        ctx: Conversation context with .candidates list and .thread.
        state: WhatsAppThreadState or compatible SimpleNamespace.
        current_turn_text: Raw current-turn text for real-time detection.
            Omit (or pass "") when calling from CE after candidates are set.
    """
    turn = current_turn_text or ""
    return FieldEvidenceSnapshot(
        service_intent=_resolve_service_intent(state),
        vehicle=_resolve_vehicle(ctx, state, turn),
        vehicle_year=_resolve_vehicle_year(ctx, state, turn),
        vehicle_category=_resolve_vehicle_category(ctx, state, turn),
        inspection_location=_resolve_inspection_location(ctx, state, turn),
        customer_origin=_resolve_customer_origin(turn),
        inspectability=_resolve_inspectability(state, turn),
        scheduling=_resolve_scheduling(state),
    )
