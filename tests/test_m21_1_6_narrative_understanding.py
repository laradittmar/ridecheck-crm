"""M21.1.6 — Narrative understanding tests (NU01–NU21).

All tests use mocked AI outputs only (no real OpenAI calls).
Pure-function tests for parse_narrative_interpretation, narrative_needs_ai,
NarrativeInterpretation helpers, and resolver integration.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.narrative_schema import (
    DEFERRED_RESPONSE_ES,
    STATUS_ABSENT,
    STATUS_CONFIRMED,
    STATUS_HISTORICAL,
    STATUS_HYPOTHETICAL,
    STATUS_LIKELY,
    STATUS_SUPERSEDED,
    STATUS_UNCERTAIN,
    NarrativeFact,
    NarrativeInterpretation,
    parse_narrative_interpretation,
    narrative_needs_ai,
)
from app.services.field_evidence import (
    resolve_field_evidence,
    INSP_ASSEMBLED_ACCESSIBLE,
    INSP_DISASSEMBLED_BLOCKED,
    INSP_UNRESOLVED_NON_RUNNING,
)
from app.services.vehicle_catalog import VehicleMatch, FuzzyLookupResult


# ── Shared fixtures ───────────────────────────────────────────────────────────

def _make_state(**kw) -> SimpleNamespace:
    defaults = dict(
        home_zone_group=None,
        home_zone_detail=None,
        last_intent=None,
        pending_fuzzy_catalog_key=None,
        inspectability_clarification_sent=False,
        preferred_day=None,
        preferred_time=None,
        active_requested_date=None,
        customer_name=None,
        current_focus_candidate_id=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _make_candidate(**kw) -> SimpleNamespace:
    defaults = dict(
        id=10,
        status="current_focus",
        marca=None,
        modelo=None,
        anio=None,
        tipo_vehiculo=None,
        zone_group=None,
        zone_detail=None,
        label=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _make_ctx(state=None, candidates=None) -> SimpleNamespace:
    if state is None:
        state = _make_state()
    ctx = SimpleNamespace(
        thread=SimpleNamespace(id=42),
        candidates=candidates if candidates is not None else [],
        state=state,
    )
    return ctx


def _veh_match(marca, modelo, tipo) -> VehicleMatch:
    return VehicleMatch(marca=marca, modelo=modelo, tipo_vehiculo=tipo)


def _fuzzy_hit(outcome, marca, modelo, tipo, score=0.90, gap=0.20) -> FuzzyLookupResult:
    return FuzzyLookupResult(
        outcome=outcome,
        match=VehicleMatch(marca=marca, modelo=modelo, tipo_vehiculo=tipo),
        score=score,
        gap=gap,
        key=f"{marca}||{modelo}",
    )


def _snap_empty():
    ctx = _make_ctx()
    state = _make_state()
    return resolve_field_evidence(ctx, state)


def _snap_vehicle_and_location():
    cand = _make_candidate(
        marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
        zone_group="Palermo",
    )
    state = _make_state(current_focus_candidate_id=10)
    ctx = _make_ctx(state=state, candidates=[cand])
    return resolve_field_evidence(ctx, state)


# ── NU01 — Real deferred-interest message (corrupted) ────────────────────────

class TestNU01DeferredInterestCorrupted:
    """Corrupted real client message: overall meaning is deferred/not-ready."""

    _raw = {
        "intent": "OTHER",
        "deferred_interest": True,
        "vehicle_make_model": None,
        "vehicle_year": None,
        "vehicle_location": None,
        "customer_origin": None,
        "inspectability": None,
        "asks_price": False,
        "asks_faq": False,
        "asks_schedule": False,
    }

    def test_parse_succeeds(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr is not None

    def test_deferred_interest_flag(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.deferred_interest is True

    def test_no_active_vehicle(self):
        narr = parse_narrative_interpretation(self._raw)
        assert not narr.has_active_vehicle()

    def test_is_effectively_deferred(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.is_effectively_deferred()

    def test_deferred_response_constant_defined(self):
        assert DEFERRED_RESPONSE_ES
        assert "cuando tengas" in DEFERRED_RESPONSE_ES
        assert "algún auto en vista" in DEFERRED_RESPONSE_ES


# ── NU02 — Clean deferred version ────────────────────────────────────────────

class TestNU02DeferredInterestClean:
    """Clean phrasing of the same deferred intent."""

    _raw = {
        "intent": "OTHER",
        "deferred_interest": True,
        "vehicle_make_model": None,
    }

    def test_is_effectively_deferred(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr is not None
        assert narr.is_effectively_deferred()

    def test_no_active_commercial_facts(self):
        narr = parse_narrative_interpretation(self._raw)
        assert not narr.has_active_vehicle()
        assert not narr.has_active_location()


# ── NU03 — Deferred language but active vehicle ───────────────────────────────

class TestNU03DeferredWithActiveVehicle:
    """Deferred language present but specific vehicle mentioned → active flow."""

    _raw = {
        "intent": "QUALIFYING",
        "deferred_interest": True,
        "vehicle_make_model": {"value": "Ford Focus", "status": STATUS_CONFIRMED},
        "vehicle_year": {"value": 2019, "status": STATUS_CONFIRMED},
        "vehicle_location": {"value": "Palermo", "status": STATUS_CONFIRMED},
    }

    def test_not_effectively_deferred(self):
        narr = parse_narrative_interpretation(self._raw)
        assert not narr.is_effectively_deferred()

    def test_vehicle_is_active(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.has_active_vehicle()
        assert narr.vehicle_make_model.value == "Ford Focus"

    def test_location_is_active(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.has_active_location()
        assert narr.vehicle_location.value == "Palermo"


# ── NU04 — Historical vehicle correction ─────────────────────────────────────

class TestNU04HistoricalVehicleCorrection:
    """Focus 2018 was historical; Corolla 2020 is current."""

    _raw = {
        "intent": "QUALIFYING",
        "deferred_interest": False,
        "vehicle_make_model": {
            "value": "Toyota Corolla",
            "status": STATUS_CONFIRMED,
            "evidence": "al final es un Corolla 2020",
        },
        "vehicle_year": {"value": 2020, "status": STATUS_CONFIRMED},
    }

    def test_current_vehicle_is_corolla(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.vehicle_make_model.value == "Toyota Corolla"

    def test_year_2020(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.vehicle_year.value == 2020

    def test_vehicle_is_active(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.has_active_vehicle()

    def test_evidence_preserved(self):
        narr = parse_narrative_interpretation(self._raw)
        assert "Corolla" in narr.vehicle_make_model.evidence

    def test_not_deferred(self):
        narr = parse_narrative_interpretation(self._raw)
        assert not narr.is_effectively_deferred()


# ── NU05 — Location correction ────────────────────────────────────────────────

class TestNU05LocationCorrection:
    """'El auto estaba en Tigre pero ahora está en Palermo.' → Palermo current."""

    _raw = {
        "vehicle_location": {
            "value": "Palermo",
            "status": STATUS_CONFIRMED,
            "evidence": "ahora está en Palermo",
        },
    }

    def test_location_is_palermo(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.vehicle_location.value == "Palermo"

    def test_location_active(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.has_active_location()

    def test_tigre_not_in_result(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.vehicle_location.value != "Tigre"


# ── NU06 — Hypothetical inspectability ───────────────────────────────────────

class TestNU06HypotheticalInspectability:
    """'¿Qué pasa si el auto no arranca?' → informational; no state change."""

    _raw = {
        "inspectability": {
            "value": INSP_UNRESOLVED_NON_RUNNING,
            "status": STATUS_HYPOTHETICAL,
        },
    }

    def test_inspectability_parsed(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.inspectability is not None

    def test_inspectability_not_active(self):
        narr = parse_narrative_interpretation(self._raw)
        assert not narr.has_active_inspectability()

    def test_status_is_hypothetical(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.inspectability.status == STATUS_HYPOTHETICAL

    def test_not_deferred(self):
        narr = parse_narrative_interpretation(self._raw)
        assert not narr.is_effectively_deferred()


# ── NU07 — Actual non-running + accessible ────────────────────────────────────

class TestNU07ActualNonRunningAccessible:
    """'No arranca, pero está armado, completo y se puede revisar.' → resolved."""

    _raw = {
        "inspectability": {
            "value": INSP_ASSEMBLED_ACCESSIBLE,
            "status": STATUS_CONFIRMED,
            "evidence": "está armado, completo y se puede revisar",
        },
    }

    def test_inspectability_confirmed(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.has_active_inspectability()

    def test_value_is_assembled_accessible(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.inspectability.value == INSP_ASSEMBLED_ACCESSIBLE

    def test_status_confirmed(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.inspectability.status == STATUS_CONFIRMED


# ── NU08 — Multi-fact quote ───────────────────────────────────────────────────

class TestNU08MultiFact:
    """Full compound message: vehicle + year + origin + location + inspectability + price."""

    _raw = {
        "intent": "QUALIFYING",
        "deferred_interest": False,
        "vehicle_make_model": {"value": "Ford Focus", "status": STATUS_CONFIRMED},
        "vehicle_year": {"value": 2019, "status": STATUS_CONFIRMED},
        "vehicle_location": {"value": "Palermo", "status": STATUS_CONFIRMED},
        "customer_origin": {"value": "La Plata", "status": STATUS_CONFIRMED},
        "inspectability": {
            "value": INSP_ASSEMBLED_ACCESSIBLE,
            "status": STATUS_CONFIRMED,
            "evidence": "no arranca aunque está completo",
        },
        "asks_price": True,
        "asks_faq": False,
        "asks_schedule": False,
    }

    def test_all_facts_parsed(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr is not None
        assert narr.has_active_vehicle()
        assert narr.has_active_location()
        assert narr.has_active_inspectability()

    def test_vehicle_is_focus(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.vehicle_make_model.value == "Ford Focus"

    def test_year_is_2019(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.vehicle_year.value == 2019

    def test_location_is_palermo(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.vehicle_location.value == "Palermo"

    def test_origin_is_la_plata(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.customer_origin.value == "La Plata"

    def test_inspectability_assembled(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.inspectability.value == INSP_ASSEMBLED_ACCESSIBLE

    def test_asks_price(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.asks_price is True

    def test_not_deferred(self):
        narr = parse_narrative_interpretation(self._raw)
        assert not narr.is_effectively_deferred()


# ── NU09 — Vehicle ambiguity ─────────────────────────────────────────────────

class TestNU09VehicleAmbiguity:
    """'Creo que es un Focus o un Fiesta, no sé.' → vehicle unresolved."""

    _raw = {
        "vehicle_make_model": {"value": None, "status": STATUS_UNCERTAIN},
    }

    def test_vehicle_uncertain(self):
        narr = parse_narrative_interpretation(self._raw)
        assert not narr.has_active_vehicle()

    def test_status_uncertain(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.vehicle_make_model.status == STATUS_UNCERTAIN

    def test_value_none(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.vehicle_make_model.value is None


# ── NU10 — Location ambiguity ─────────────────────────────────────────────────

class TestNU10LocationAmbiguity:
    """'El auto está en Tigre o en Palermo, no sé.' → location unresolved."""

    _raw = {
        "vehicle_location": {"value": None, "status": STATUS_UNCERTAIN},
    }

    def test_location_not_active(self):
        narr = parse_narrative_interpretation(self._raw)
        assert not narr.has_active_location()

    def test_status_uncertain(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.vehicle_location.status == STATUS_UNCERTAIN


# ── NU11 — Explicit vehicle correction ────────────────────────────────────────

class TestNU11ExplicitVehicleCorrection:
    """'Es un Ford Ka... no, perdón, es un Ford Kuga.' → Kuga current."""

    _raw = {
        "vehicle_make_model": {
            "value": "Ford Kuga",
            "status": STATUS_CONFIRMED,
            "evidence": "no, perdón, es un Ford Kuga",
        },
    }

    def test_vehicle_is_kuga(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.vehicle_make_model.value == "Ford Kuga"

    def test_vehicle_is_active(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.has_active_vehicle()

    def test_ka_not_in_result(self):
        narr = parse_narrative_interpretation(self._raw)
        assert "Ka" not in (narr.vehicle_make_model.value or "")


# ── NU12 — Customer origin + vehicle location separation ─────────────────────

class TestNU12OriginLocationSeparation:
    """'Yo estoy en San Isidro pero el auto está en Villa Urquiza.' → separate roles."""

    _raw = {
        "customer_origin": {"value": "San Isidro", "status": STATUS_CONFIRMED},
        "vehicle_location": {"value": "Villa Urquiza", "status": STATUS_CONFIRMED},
    }

    def test_origin_is_san_isidro(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.customer_origin.value == "San Isidro"

    def test_location_is_villa_urquiza(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.vehicle_location.value == "Villa Urquiza"

    def test_roles_separate(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.customer_origin.value != narr.vehicle_location.value

    def test_both_active(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.customer_origin.is_active()
        assert narr.vehicle_location.is_active()


# ── NU13 — Deterministic easy-case bypass ────────────────────────────────────

class TestNU13DeterministicEasyCaseBypass:
    """When evidence is complete and text is simple, narrative AI is not needed."""

    def test_bypass_when_vehicle_and_location_known(self):
        snap = _snap_vehicle_and_location()
        assert snap.vehicle_known()
        assert snap.location_known()
        result = narrative_needs_ai(snap, "Ford Focus 2019 en Palermo, ¿cuánto sale?")
        assert result is False

    def test_bypass_simple_price_question(self):
        snap = _snap_vehicle_and_location()
        result = narrative_needs_ai(snap, "¿cuánto sale?")
        assert result is False

    def test_no_bypass_when_vehicle_unknown(self):
        snap = _snap_empty()
        assert not snap.vehicle_known()
        result = narrative_needs_ai(snap, "Ford Focus 2019 en Palermo, ¿cuánto sale?")
        assert result is True

    def test_no_bypass_when_location_unknown(self):
        cand = _make_candidate(marca="Ford", modelo="Focus", tipo_vehiculo="AUTO")
        state = _make_state(current_focus_candidate_id=10)
        ctx = _make_ctx(state=state, candidates=[cand])
        snap = resolve_field_evidence(ctx, state)
        assert snap.vehicle_known()
        assert not snap.location_known()
        result = narrative_needs_ai(snap, "Ford Focus, ¿cuánto sale?")
        assert result is True

    def test_no_bypass_when_correction_marker_present(self):
        snap = _snap_vehicle_and_location()
        result = narrative_needs_ai(snap, "En realidad el auto está en Tigre, no en Palermo")
        assert result is True

    def test_no_bypass_when_hypothetical_marker_present(self):
        snap = _snap_vehicle_and_location()
        result = narrative_needs_ai(snap, "¿Qué pasa si el auto no arranca?")
        assert result is True


# ── NU14 — Unsupported boundary bypass ───────────────────────────────────────

class TestNU14UnsupportedBoundaryBypass:
    """Unsupported service request: narrative does not override deterministic gate."""

    _raw = {
        "intent": "ESCALATE",
        "deferred_interest": False,
        "vehicle_make_model": None,
        "vehicle_location": None,
    }

    def test_narrative_parse_does_not_deferred(self):
        narr = parse_narrative_interpretation(self._raw)
        assert not narr.is_effectively_deferred()

    def test_no_active_vehicle_or_location(self):
        narr = parse_narrative_interpretation(self._raw)
        assert not narr.has_active_vehicle()
        assert not narr.has_active_location()

    def test_narrative_needs_ai_when_evidence_incomplete(self):
        snap = _snap_empty()
        result = narrative_needs_ai(
            snap,
            "Quiero revisar el auto pero también necesito que hagan la transferencia.",
        )
        assert result is True


# ── NU15 — Motorcycle bypass ──────────────────────────────────────────────────

class TestNU15MotorcycleBypass:
    """Motorcycle: deterministic gate fires before narrative; narrative not the decider."""

    _raw = {
        "intent": "QUALIFYING",
        "deferred_interest": False,
        "vehicle_make_model": {
            "value": "Honda CB 500",
            "status": STATUS_CONFIRMED,
        },
    }

    def test_narrative_captures_vehicle(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.has_active_vehicle()
        assert "Honda" in narr.vehicle_make_model.value

    def test_narrative_not_deferred(self):
        narr = parse_narrative_interpretation(self._raw)
        assert not narr.is_effectively_deferred()

    def test_narrative_needs_ai_when_incomplete(self):
        snap = _snap_empty()
        result = narrative_needs_ai(
            snap, "Estoy viendo una Honda CB 500 y quiero revisarla."
        )
        assert result is True


# ── NU16 — Malformed AI result ────────────────────────────────────────────────

class TestNU16MalformedAI:
    """Malformed AI output must produce safe fallback, never unsafe mutation."""

    def test_empty_dict_returns_interpretation(self):
        narr = parse_narrative_interpretation({})
        assert narr is not None
        assert not narr.deferred_interest
        assert not narr.has_active_vehicle()

    def test_bad_status_coerced_to_uncertain(self):
        raw = {"vehicle_make_model": {"value": "Ford", "status": "INVALID_STATUS"}}
        narr = parse_narrative_interpretation(raw)
        assert narr is not None
        assert narr.vehicle_make_model.status == STATUS_UNCERTAIN
        assert not narr.has_active_vehicle()

    def test_wrong_type_value_handled(self):
        raw = {"deferred_interest": "yes", "vehicle_make_model": "string_not_dict"}
        narr = parse_narrative_interpretation(raw)
        assert narr is not None
        assert narr.vehicle_make_model is None

    def test_nested_garbage_returns_safe(self):
        raw = {"vehicle_make_model": {"value": {"nested": "bad"}, "status": STATUS_CONFIRMED}}
        narr = parse_narrative_interpretation(raw)
        assert narr is not None
        assert narr.vehicle_make_model is not None


# ── NU17 — AI timeout / error ─────────────────────────────────────────────────

class TestNU17AIError:
    """None or non-dict input returns None — callers use deterministic fallback."""

    def test_none_returns_none(self):
        assert parse_narrative_interpretation(None) is None

    def test_string_returns_none(self):
        assert parse_narrative_interpretation("not a dict") is None

    def test_list_returns_none(self):
        assert parse_narrative_interpretation([]) is None

    def test_integer_returns_none(self):
        assert parse_narrative_interpretation(42) is None


# ── NU18 — Resolver refresh ───────────────────────────────────────────────────

class TestNU18ResolverRefresh:
    """After narrative facts applied to candidate, refreshed resolver reflects new state."""

    def test_vehicle_unknown_before_candidate(self):
        ctx = _make_ctx()
        state = _make_state()
        snap = resolve_field_evidence(ctx, state)
        assert not snap.vehicle_known()

    def test_vehicle_known_after_candidate_added(self):
        cand = _make_candidate(marca="Ford", modelo="Focus", tipo_vehiculo="AUTO")
        state = _make_state(current_focus_candidate_id=10)
        ctx = _make_ctx(state=state, candidates=[cand])
        snap = resolve_field_evidence(ctx, state)
        assert snap.vehicle_known()
        assert not snap.needs_vehicle()

    def test_location_unknown_before_zone(self):
        cand = _make_candidate(marca="Ford", modelo="Focus", tipo_vehiculo="AUTO")
        state = _make_state(current_focus_candidate_id=10)
        ctx = _make_ctx(state=state, candidates=[cand])
        snap = resolve_field_evidence(ctx, state)
        assert not snap.location_known()

    def test_location_known_after_zone_set(self):
        cand = _make_candidate(
            marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
            zone_group="Palermo",
        )
        state = _make_state(current_focus_candidate_id=10)
        ctx = _make_ctx(state=state, candidates=[cand])
        snap = resolve_field_evidence(ctx, state)
        assert snap.location_known()

    def test_pricing_ready_after_full_qualification(self):
        cand = _make_candidate(
            marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
            zone_group="Palermo",
        )
        state = _make_state(
            current_focus_candidate_id=10,
            last_intent="PREPURCHASE_INSPECTION",
        )
        ctx = _make_ctx(state=state, candidates=[cand])
        snap = resolve_field_evidence(ctx, state)
        assert snap.pricing_ready()


# ── NU19 — FAQ + facts ────────────────────────────────────────────────────────

class TestNU19FAQPlusFacts:
    """'Es un Corolla 2021 en Palermo, ¿qué revisan?' → FAQ flag + retained facts."""

    _raw = {
        "intent": "QUALIFYING",
        "deferred_interest": False,
        "vehicle_make_model": {"value": "Toyota Corolla", "status": STATUS_CONFIRMED},
        "vehicle_year": {"value": 2021, "status": STATUS_CONFIRMED},
        "vehicle_location": {"value": "Palermo", "status": STATUS_CONFIRMED},
        "asks_price": False,
        "asks_faq": True,
        "asks_schedule": False,
    }

    def test_asks_faq(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.asks_faq is True

    def test_vehicle_retained(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.vehicle_make_model.value == "Toyota Corolla"
        assert narr.vehicle_year.value == 2021

    def test_location_retained(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.vehicle_location.value == "Palermo"

    def test_not_deferred(self):
        narr = parse_narrative_interpretation(self._raw)
        assert not narr.is_effectively_deferred()


# ── NU20 — Soft close after prior inspection context ─────────────────────────

class TestNU20SoftCloseAfterContext:
    """'Gracias, todavía no decidí.' → deferred; no new candidate or Flow."""

    _raw = {
        "intent": "OTHER",
        "deferred_interest": True,
        "vehicle_make_model": None,
        "vehicle_year": None,
    }

    def test_is_effectively_deferred(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.is_effectively_deferred()

    def test_no_active_vehicle(self):
        narr = parse_narrative_interpretation(self._raw)
        assert not narr.has_active_vehicle()

    def test_deferred_response_copy(self):
        assert "cuando tengas" in DEFERRED_RESPONSE_ES
        assert "escribinos" in DEFERRED_RESPONSE_ES


# ── NU21 — Multi-message burst ────────────────────────────────────────────────

class TestNU21MultiMessageBurst:
    """Burst of short messages ('Es un Focus.' / '2019.' / 'Está en Palermo.' / '¿Cuánto sale?')
    produces one coherent interpretation."""

    _raw = {
        "intent": "QUALIFYING",
        "deferred_interest": False,
        "vehicle_make_model": {"value": "Ford Focus", "status": STATUS_CONFIRMED},
        "vehicle_year": {"value": 2019, "status": STATUS_CONFIRMED},
        "vehicle_location": {"value": "Palermo", "status": STATUS_CONFIRMED},
        "asks_price": True,
    }

    def test_all_facts_from_burst(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.has_active_vehicle()
        assert narr.has_active_location()
        assert narr.vehicle_year.value == 2019

    def test_no_redundant_asks_needed(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.has_active_vehicle()
        assert narr.has_active_location()

    def test_asks_price(self):
        narr = parse_narrative_interpretation(self._raw)
        assert narr.asks_price is True

    def test_not_deferred(self):
        narr = parse_narrative_interpretation(self._raw)
        assert not narr.is_effectively_deferred()

    def test_narrative_needs_ai_with_empty_evidence(self):
        snap = _snap_empty()
        burst_text = "Es un Focus. 2019. Está en Palermo. ¿Cuánto sale?"
        assert narrative_needs_ai(snap, burst_text) is True

    def test_bypass_when_evidence_already_complete(self):
        snap = _snap_vehicle_and_location()
        burst_text = "Es un Focus. 2019. Está en Palermo. ¿Cuánto sale?"
        assert narrative_needs_ai(snap, burst_text) is False
