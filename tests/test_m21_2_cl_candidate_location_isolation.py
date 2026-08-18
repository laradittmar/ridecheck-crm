"""M21.2 CL — Candidate location-isolation regression tests.

Guards the fix for D-LIVE08-02: a mentioned/unrelated candidate must not
inherit location evidence from a different vehicle's conversation.
state.home_zone_* is the pre-candidate buffer; only the established
current-focus candidate receives zone directly from _apply_zone_from_text.

CL01 — LIVE07→LIVE08: MOTO candidate zone stays NULL after Ford Focus/Palermo burst
CL02 — two automotive candidates: Ford/Olivos unchanged when Toyota/Palermo created
CL03 — pre-candidate Palermo fallback still works (no candidate → home_zone_*)
CL04 — existing current_focus enrichment still works (Ford + Palermo → Ford.zone=Palermo)
CL05 — old MOTO mentioned candidate not affected by new automotive location
CL06 — correction remains local (current_focus Ford zone updated; other candidates unchanged)
CL07 — home_zone broadcast prevention: creating one candidate doesn't update all candidates
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

for _mod_name in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json

if not isinstance(getattr(_pg_dialect, "JSONB", None), type(sqlalchemy.JSON)):
    _pg_dialect.JSONB = sqlalchemy.JSON
if not isinstance(getattr(_pg_json, "JSONB", None), type(sqlalchemy.JSON)):
    _pg_json.JSONB = sqlalchemy.JSON

from app.services.conversation_engine import ConversationEngine  # noqa: E402
from app.models import WhatsAppThreadCandidate                    # noqa: E402

STAGE_QUALIFYING = "QUALIFYING"


def _make_state(**kw) -> types.SimpleNamespace:
    ns = types.SimpleNamespace(
        last_stage=STAGE_QUALIFYING,
        needs_human=False,
        last_intent=None,
        home_zone_group=None,
        home_zone_detail=None,
        current_focus_candidate_id=None,
        preferred_day=None,
        preferred_time=None,
        active_requested_date=None,
        last_requested_time=None,
        last_offered_slots=None,
        last_visible_slots=None,
        is_website_lead=False,
        flow_booking_token=None,
        current_revision_id=None,
        customer_name=None,
        vehicle_clarification_sent=False,
        location_clarification_sent=False,
        vehicle_fallback_flow_sent=False,
        location_fallback_flow_sent=False,
        inspectability_clarification_sent=False,
        last_processed_inbound_wa_message_id=None,
        pending_fuzzy_catalog_key=None,
        pending_turn_evidence_text=None,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _make_candidate(**kw) -> types.SimpleNamespace:
    defaults = dict(
        id=None, marca=None, modelo=None, anio=None,
        tipo_vehiculo=None, zone_group=None, zone_detail=None,
        status="mentioned", source_text=None, version_text=None, direccion_texto=None,
    )
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def _make_ctx(state=None, candidates=None) -> types.SimpleNamespace:
    ctx = types.SimpleNamespace()
    ctx.thread = types.SimpleNamespace(id=415, last_message_at=None)
    ctx.contact = types.SimpleNamespace(wa_id="5491153368330")
    ctx.lead = types.SimpleNamespace(
        id=27, flag=None, estado="CONSULTA_NUEVA",
        nombre=None, apellido=None, email=None, canal=None,
        telefono="5491153368330", necesita_humano=False,
    )
    ctx.state = state if state is not None else _make_state()
    ctx.candidates = candidates if candidates is not None else []
    return ctx


def _make_engine() -> ConversationEngine:
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock()
    eng.settings = MagicMock()
    eng.settings.whatsapp_flow_id = "TEST_FLOW_999"
    return eng


def _palermo_zone():
    return types.SimpleNamespace(zone_group="CABA", zone_detail="Palermo")


def _olivos_zone():
    return types.SimpleNamespace(zone_group="GBA Norte", zone_detail="Olivos")


# ══════════════════════════════════════════════════════════════════════════════
# CL01 — LIVE07→LIVE08 exact sequence: MOTO zone stays NULL
# ══════════════════════════════════════════════════════════════════════════════

class TestCL01MotoZoneNotLeaked(unittest.TestCase):

    def test_cl01_moto_zone_remains_null_after_auto_location(self):
        """MOTO mentioned candidate must not receive zone from a Ford Focus/Palermo turn."""
        eng = _make_engine()
        moto_cand = _make_candidate(
            id=37, tipo_vehiculo="MOTO", marca="Honda", modelo="CB500",
            status="mentioned", zone_group=None, zone_detail=None,
        )
        state = _make_state()  # no current_focus_candidate_id, no home_zone
        ctx = _make_ctx(state=state, candidates=[moto_cand])

        with patch.object(eng, "_extract_vehicle_location_zones", return_value=[_palermo_zone()]):
            with patch.object(eng, "_extract_zone_from_text", return_value=None):
                eng._apply_zone_from_text(ctx, state, "esta en palermo")

        self.assertIsNone(moto_cand.zone_group, "MOTO zone_group must remain NULL")
        self.assertIsNone(moto_cand.zone_detail, "MOTO zone_detail must remain NULL")

    def test_cl01_palermo_buffered_to_home_zone(self):
        """When only a mentioned MOTO candidate exists, zone must buffer into home_zone_*."""
        eng = _make_engine()
        moto_cand = _make_candidate(
            id=37, tipo_vehiculo="MOTO", marca="Honda", modelo="CB500",
            status="mentioned", zone_group=None, zone_detail=None,
        )
        state = _make_state()
        ctx = _make_ctx(state=state, candidates=[moto_cand])

        with patch.object(eng, "_extract_vehicle_location_zones", return_value=[_palermo_zone()]):
            with patch.object(eng, "_extract_zone_from_text", return_value=None):
                eng._apply_zone_from_text(ctx, state, "esta en palermo")

        self.assertEqual(state.home_zone_group, "CABA")
        self.assertEqual(state.home_zone_detail, "Palermo")


# ══════════════════════════════════════════════════════════════════════════════
# CL02 — two automotive candidates: existing zone unchanged
# ══════════════════════════════════════════════════════════════════════════════

class TestCL02TwoAutoCandidateIsolation(unittest.TestCase):

    def test_cl02_old_candidate_zone_unchanged_when_new_candidate_gets_palermo(self):
        """A mentioned Ford/Olivos candidate must not receive Palermo from a new Toyota turn."""
        eng = _make_engine()
        ford_cand = _make_candidate(
            id=36, tipo_vehiculo="AUTO", marca="Ford", modelo="Focus",
            status="mentioned", zone_group="GBA Norte", zone_detail="Olivos",
        )
        # No current_focus_candidate_id — Ford is only "mentioned"
        state = _make_state()
        ctx = _make_ctx(state=state, candidates=[ford_cand])

        with patch.object(eng, "_extract_vehicle_location_zones", return_value=[_palermo_zone()]):
            with patch.object(eng, "_extract_zone_from_text", return_value=None):
                eng._apply_zone_from_text(ctx, state, "toyota corolla en palermo")

        # Ford zone must be unchanged
        self.assertEqual(ford_cand.zone_group, "GBA Norte")
        self.assertEqual(ford_cand.zone_detail, "Olivos")
        # Palermo buffered to home_zone_*
        self.assertEqual(state.home_zone_group, "CABA")
        self.assertEqual(state.home_zone_detail, "Palermo")


# ══════════════════════════════════════════════════════════════════════════════
# CL03 — pre-candidate Palermo fallback still works
# ══════════════════════════════════════════════════════════════════════════════

class TestCL03PreCandidateFallback(unittest.TestCase):

    def test_cl03_no_candidate_buffers_to_home_zone(self):
        """With no candidates at all, vehicle-location evidence must buffer into home_zone_*."""
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state=state, candidates=[])

        with patch.object(eng, "_extract_vehicle_location_zones", return_value=[_palermo_zone()]):
            with patch.object(eng, "_extract_zone_from_text", return_value=None):
                eng._apply_zone_from_text(ctx, state, "el auto esta en palermo")

        self.assertEqual(state.home_zone_group, "CABA")
        self.assertEqual(state.home_zone_detail, "Palermo")

    def test_cl03_new_candidate_inherits_home_zone_via_apply_candidate(self):
        """New candidate created after home_zone_* is set must inherit that zone."""
        from app.models import WhatsAppThreadCandidate

        eng = _make_engine()
        _id = [100]
        def _flush():
            for call_args in eng.db.add.call_args_list:
                obj = call_args[0][0]
                if isinstance(obj, WhatsAppThreadCandidate) and not obj.id:
                    _id[0] += 1
                    obj.id = _id[0]
        eng.db.flush.side_effect = _flush

        state = _make_state(home_zone_group="CABA", home_zone_detail="Palermo")
        ctx = _make_ctx(state=state, candidates=[])

        eng._apply_candidate(ctx, {
            "action": "create",
            "tipo_vehiculo": "AUTO",
            "marca": "Ford",
            "modelo": "Focus",
            "anio": 2019,
            "status": "current_focus",
        })
        new_cand = next(c for c in ctx.candidates if c.tipo_vehiculo == "AUTO")
        self.assertEqual(new_cand.zone_group, "CABA")
        self.assertEqual(new_cand.zone_detail, "Palermo")


# ══════════════════════════════════════════════════════════════════════════════
# CL04 — established current_focus candidate enriched directly
# ══════════════════════════════════════════════════════════════════════════════

class TestCL04CurrentFocusEnrichment(unittest.TestCase):

    def test_cl04_current_focus_by_status_receives_zone(self):
        """A candidate with status=current_focus must receive zone directly."""
        eng = _make_engine()
        ford_cand = _make_candidate(
            id=36, tipo_vehiculo="AUTO", marca="Ford", modelo="Focus",
            status="current_focus", zone_group=None, zone_detail=None,
        )
        state = _make_state()
        ctx = _make_ctx(state=state, candidates=[ford_cand])

        with patch.object(eng, "_extract_vehicle_location_zones", return_value=[_palermo_zone()]):
            with patch.object(eng, "_extract_zone_from_text", return_value=None):
                eng._apply_zone_from_text(ctx, state, "esta en palermo")

        self.assertEqual(ford_cand.zone_group, "CABA")
        self.assertEqual(ford_cand.zone_detail, "Palermo")
        # home_zone_* should NOT be set when written directly to candidate
        self.assertIsNone(state.home_zone_group)
        self.assertIsNone(state.home_zone_detail)

    def test_cl04_current_focus_by_state_id_receives_zone(self):
        """A candidate pointed to by state.current_focus_candidate_id must receive zone directly."""
        eng = _make_engine()
        ford_cand = _make_candidate(
            id=36, tipo_vehiculo="AUTO", marca="Ford", modelo="Focus",
            status="mentioned",  # status is mentioned but ID is current focus
            zone_group=None, zone_detail=None,
        )
        state = _make_state(current_focus_candidate_id=36)
        ctx = _make_ctx(state=state, candidates=[ford_cand])

        with patch.object(eng, "_extract_vehicle_location_zones", return_value=[_palermo_zone()]):
            with patch.object(eng, "_extract_zone_from_text", return_value=None):
                eng._apply_zone_from_text(ctx, state, "esta en palermo")

        self.assertEqual(ford_cand.zone_group, "CABA")
        self.assertEqual(ford_cand.zone_detail, "Palermo")


# ══════════════════════════════════════════════════════════════════════════════
# CL05 — MOTO mentioned candidate protected from any automotive location
# ══════════════════════════════════════════════════════════════════════════════

class TestCL05MotoProtectedFromAutoLocation(unittest.TestCase):

    def test_cl05_moto_mentioned_not_updated_by_explicit_vehicle_location(self):
        """A MOTO/mentioned candidate must never receive zone from an automotive message."""
        eng = _make_engine()
        moto_cand = _make_candidate(
            id=37, tipo_vehiculo="MOTO", marca="Honda", modelo="CB500",
            status="mentioned", zone_group=None, zone_detail=None,
        )
        state = _make_state()  # no current_focus
        ctx = _make_ctx(state=state, candidates=[moto_cand])

        with patch.object(eng, "_extract_vehicle_location_zones", return_value=[_palermo_zone()]):
            with patch.object(eng, "_extract_zone_from_text", return_value=None):
                eng._apply_zone_from_text(ctx, state, "esta en palermo")

        self.assertIsNone(moto_cand.zone_group)
        self.assertIsNone(moto_cand.zone_detail)

    def test_cl05_bare_locality_moto_mentioned_not_updated(self):
        """Bare locality detection must also not write to a MOTO/mentioned candidate."""
        eng = _make_engine()
        moto_cand = _make_candidate(
            id=37, tipo_vehiculo="MOTO", marca="Honda", modelo="CB500",
            status="mentioned", zone_group=None, zone_detail=None,
        )
        state = _make_state()
        ctx = _make_ctx(state=state, candidates=[moto_cand])

        # No explicit vehicle-location phrase; bare locality path
        with patch.object(eng, "_extract_vehicle_location_zones", return_value=[]):
            with patch.object(eng, "_extract_zone_from_text", return_value=_palermo_zone()):
                eng._apply_zone_from_text(ctx, state, "palermo")

        self.assertIsNone(moto_cand.zone_group)
        self.assertIsNone(moto_cand.zone_detail)
        # Zone should still be captured in home_zone_* for later use
        self.assertEqual(state.home_zone_detail, "Palermo")


# ══════════════════════════════════════════════════════════════════════════════
# CL06 — correction is local to the current_focus candidate
# ══════════════════════════════════════════════════════════════════════════════

class TestCL06CorrectionIsLocal(unittest.TestCase):

    def test_cl06_zone_correction_updates_current_focus_only(self):
        """Zone correction must update only the current_focus candidate, not others."""
        eng = _make_engine()
        ford_cand = _make_candidate(
            id=36, tipo_vehiculo="AUTO", marca="Ford", modelo="Focus",
            status="current_focus", zone_group="CABA", zone_detail="Palermo",
        )
        other_cand = _make_candidate(
            id=35, tipo_vehiculo="AUTO", marca="Toyota", modelo="Corolla",
            status="mentioned", zone_group="GBA Norte", zone_detail="Olivos",
        )
        state = _make_state(current_focus_candidate_id=36)
        ctx = _make_ctx(state=state, candidates=[ford_cand, other_cand])

        olivos_zone = _olivos_zone()
        with patch.object(eng, "_extract_vehicle_location_zones", return_value=[olivos_zone]):
            with patch.object(eng, "_extract_zone_from_text", return_value=None):
                eng._apply_zone_from_text(ctx, state, "al final el auto esta en olivos")

        # Ford corrected to Olivos
        self.assertEqual(ford_cand.zone_group, "GBA Norte")
        self.assertEqual(ford_cand.zone_detail, "Olivos")
        # Toyota unchanged
        self.assertEqual(other_cand.zone_group, "GBA Norte")
        self.assertEqual(other_cand.zone_detail, "Olivos")


# ══════════════════════════════════════════════════════════════════════════════
# CL07 — home_zone broadcast prevention
# ══════════════════════════════════════════════════════════════════════════════

class TestCL07HomZoneNoBroadcast(unittest.TestCase):

    def test_cl07_apply_candidate_create_sets_zone_only_on_new_candidate(self):
        """_apply_candidate create must set zone only on the new candidate, not on existing ones."""
        from app.models import WhatsAppThreadCandidate

        eng = _make_engine()
        _id = [100]
        def _flush():
            for call_args in eng.db.add.call_args_list:
                obj = call_args[0][0]
                if isinstance(obj, WhatsAppThreadCandidate) and not obj.id:
                    _id[0] += 1
                    obj.id = _id[0]
        eng.db.flush.side_effect = _flush

        existing_cand = _make_candidate(
            id=36, tipo_vehiculo="AUTO", marca="Ford", modelo="Focus",
            status="mentioned", zone_group=None, zone_detail=None,
        )
        state = _make_state(home_zone_group="CABA", home_zone_detail="Palermo")
        ctx = _make_ctx(state=state, candidates=[existing_cand])

        eng._apply_candidate(ctx, {
            "action": "create",
            "tipo_vehiculo": "AUTO",
            "marca": "Toyota",
            "modelo": "Corolla",
            "anio": 2021,
            "status": "current_focus",
        })

        # Existing Ford candidate must not receive Palermo
        self.assertIsNone(existing_cand.zone_group, "Existing candidate zone must not be set")
        # New Toyota candidate inherits Palermo from home_zone_*
        toyota_cand = next(c for c in ctx.candidates if (c.marca or "") == "Toyota")
        self.assertEqual(toyota_cand.zone_group, "CABA")
        self.assertEqual(toyota_cand.zone_detail, "Palermo")


if __name__ == "__main__":
    unittest.main()
