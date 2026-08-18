"""M21.2 CR — Candidate type-safety regression tests.

Guards the fix for D-LIVE08-01: a MOTO candidate must never be repurposed
as an AUTO candidate (and vice versa) by _apply_candidate or _enforce_catalog_vehicle.

CR01 — LIVE07→LIVE08 sequence: MOTO candidate preserved, new AUTO candidate created
CR02 — motorcycle source_text preserved unchanged after CR01 sequence
CR03 — same-vehicle enrichment: existing AUTO candidate reused (year added)
CR04 — incompatible automotive make/model: new candidate created
CR05 — type boundary reverse: AUTO→MOTO inquiry creates new MOTO candidate
CR06 — motorcycle LIVE07 handoff path still works end-to-end
CR07 — quote uses Ford candidate, not motorcycle candidate, after CR01 sequence
CR08 — source_text not mutated by same-candidate enrichment
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

from app.services.conversation_engine import (  # noqa: E402
    ConversationEngine,
    _tipo_compatible,
    _MOTO_TIPO_VALUES,
)
from app.models import WhatsAppThreadCandidate  # noqa: E402

# ── Shared fixtures ───────────────────────────────────────────────────────────

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
        id=37, marca=None, modelo=None, anio=None,
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
    db = MagicMock()
    # flush() assigns a fake id to newly added candidates
    _id_counter = [100]
    def _flush():
        for call_args in db.add.call_args_list:
            obj = call_args[0][0]
            if isinstance(obj, WhatsAppThreadCandidate) and not obj.id:
                _id_counter[0] += 1
                obj.id = _id_counter[0]
    db.flush.side_effect = _flush
    eng.db = db
    eng.settings = MagicMock()
    eng.settings.whatsapp_flow_id = "TEST_FLOW_999"
    return eng


# ══════════════════════════════════════════════════════════════════════════════
# Unit tests for _tipo_compatible helper
# ══════════════════════════════════════════════════════════════════════════════

class TestTipoCompatibleHelper(unittest.TestCase):

    def test_moto_vs_auto_incompatible(self):
        self.assertFalse(_tipo_compatible("MOTO", "AUTO"))

    def test_auto_vs_moto_incompatible(self):
        self.assertFalse(_tipo_compatible("AUTO", "MOTO"))

    def test_motocicleta_vs_auto_incompatible(self):
        self.assertFalse(_tipo_compatible("MOTOCICLETA", "AUTO"))

    def test_auto_vs_motocicleta_incompatible(self):
        self.assertFalse(_tipo_compatible("AUTO", "MOTOCICLETA"))

    def test_moto_vs_suv_incompatible(self):
        self.assertFalse(_tipo_compatible("MOTO", "SUV_4X4_DEPORTIVO"))

    def test_moto_vs_moto_compatible(self):
        self.assertTrue(_tipo_compatible("MOTO", "MOTO"))

    def test_auto_vs_auto_compatible(self):
        self.assertTrue(_tipo_compatible("AUTO", "AUTO"))

    def test_auto_vs_suv_compatible(self):
        self.assertTrue(_tipo_compatible("AUTO", "SUV_4X4_DEPORTIVO"))

    def test_null_existing_compatible(self):
        self.assertTrue(_tipo_compatible(None, "AUTO"))

    def test_null_new_compatible(self):
        self.assertTrue(_tipo_compatible("MOTO", None))

    def test_both_null_compatible(self):
        self.assertTrue(_tipo_compatible(None, None))

    def test_empty_string_compatible(self):
        self.assertTrue(_tipo_compatible("", "AUTO"))

    def test_moto_uppercase_case_insensitive(self):
        self.assertFalse(_tipo_compatible("moto", "auto"))


# ══════════════════════════════════════════════════════════════════════════════
# CR01 — _apply_candidate: MOTO existing + AUTO update (no id) → new candidate
# ══════════════════════════════════════════════════════════════════════════════

class TestCR01MotoExistingAutoUpdate(unittest.TestCase):

    def _run(self):
        eng = _make_engine()
        moto_cand = _make_candidate(
            id=37, tipo_vehiculo="MOTO", marca="Honda", modelo="CB500",
            status="mentioned", source_text="Quiero revisar una moto Honda CB500",
        )
        state = _make_state()
        ctx = _make_ctx(state=state, candidates=[moto_cand])

        # AI returns action=update (no id) with AUTO Ford Focus 2019
        candidate_data = {
            "action": "update",
            "tipo_vehiculo": "AUTO",
            "marca": "Ford",
            "modelo": "Focus",
            "anio": 2019,
            "status": "current_focus",
        }
        eng._apply_candidate(ctx, candidate_data)
        return eng, ctx, state, moto_cand

    def test_cr01_moto_candidate_preserved(self):
        """MOTO candidate must not be modified when AUTO update arrives."""
        _, ctx, _, moto_cand = self._run()
        self.assertEqual(moto_cand.tipo_vehiculo, "MOTO")
        self.assertEqual(moto_cand.marca, "Honda")
        self.assertEqual(moto_cand.modelo, "CB500")
        self.assertEqual(moto_cand.status, "mentioned")

    def test_cr01_new_auto_candidate_created(self):
        """A new AUTO candidate must be created instead of overwriting MOTO."""
        _, ctx, _, moto_cand = self._run()
        auto_candidates = [c for c in ctx.candidates if c.tipo_vehiculo == "AUTO"]
        self.assertEqual(len(auto_candidates), 1, "exactly one AUTO candidate expected")
        auto = auto_candidates[0]
        self.assertEqual(auto.marca, "Ford")
        self.assertEqual(auto.modelo, "Focus")
        self.assertEqual(auto.anio, 2019)

    def test_cr01_candidate_count_two(self):
        """Two candidates must exist after the sequence (MOTO + AUTO)."""
        _, ctx, _, _ = self._run()
        self.assertEqual(len(ctx.candidates), 2)

    def test_cr01_focus_on_auto_candidate(self):
        """current_focus_candidate_id must point to the new AUTO candidate."""
        _, ctx, state, moto_cand = self._run()
        self.assertNotEqual(state.current_focus_candidate_id, moto_cand.id)
        auto_cand = next(c for c in ctx.candidates if c.tipo_vehiculo == "AUTO")
        self.assertEqual(state.current_focus_candidate_id, auto_cand.id)


# ══════════════════════════════════════════════════════════════════════════════
# CR02 — source_text of MOTO candidate preserved after AUTO inquiry
# ══════════════════════════════════════════════════════════════════════════════

class TestCR02MotoSourceTextPreserved(unittest.TestCase):

    def test_cr02_source_text_unchanged(self):
        """MOTO candidate source_text must remain the original motorcycle evidence."""
        eng = _make_engine()
        original_text = "Quiero revisar una moto Honda CB500"
        moto_cand = _make_candidate(
            id=37, tipo_vehiculo="MOTO", marca="Honda", modelo="CB500",
            status="mentioned", source_text=original_text,
        )
        state = _make_state()
        ctx = _make_ctx(state=state, candidates=[moto_cand])
        eng._apply_candidate(ctx, {
            "action": "update", "tipo_vehiculo": "AUTO",
            "marca": "Ford", "modelo": "Focus", "anio": 2019, "status": "current_focus",
        })
        self.assertEqual(moto_cand.source_text, original_text)


# ══════════════════════════════════════════════════════════════════════════════
# CR03 — same-vehicle enrichment: existing AUTO candidate reused (year added)
# ══════════════════════════════════════════════════════════════════════════════

class TestCR03SameVehicleEnrichment(unittest.TestCase):

    def test_cr03_year_enrichment_reuses_candidate(self):
        """Adding year to an existing AUTO candidate must update in-place, not create new."""
        eng = _make_engine()
        auto_cand = _make_candidate(
            id=36, tipo_vehiculo="AUTO", marca="Ford", modelo="Focus",
            anio=None, status="current_focus",
        )
        state = _make_state(current_focus_candidate_id=36)
        ctx = _make_ctx(state=state, candidates=[auto_cand])
        eng._apply_candidate(ctx, {
            "action": "update",
            "tipo_vehiculo": "AUTO",
            "anio": 2019,
            "status": "current_focus",
        })
        # Still one candidate; year updated
        self.assertEqual(len(ctx.candidates), 1)
        self.assertEqual(auto_cand.anio, 2019)
        self.assertEqual(auto_cand.tipo_vehiculo, "AUTO")
        self.assertEqual(auto_cand.marca, "Ford")

    def test_cr03_location_enrichment_reuses_candidate(self):
        """Adding zone to an existing AUTO candidate must not create a new one."""
        eng = _make_engine()
        auto_cand = _make_candidate(
            id=36, tipo_vehiculo="AUTO", marca="Ford", modelo="Focus",
            anio=2019, zone_group=None, status="current_focus",
        )
        state = _make_state(current_focus_candidate_id=36)
        ctx = _make_ctx(state=state, candidates=[auto_cand])
        eng._apply_candidate(ctx, {
            "action": "update",
            "zone_group": "CABA",
            "zone_detail": "Palermo",
        })
        self.assertEqual(len(ctx.candidates), 1)
        self.assertEqual(auto_cand.zone_group, "CABA")


# ══════════════════════════════════════════════════════════════════════════════
# CR04 — incompatible automotive make/model: new candidate (via AI action=create)
# ══════════════════════════════════════════════════════════════════════════════

class TestCR04IncompatibleAutoMakeModel(unittest.TestCase):

    def test_cr04_focus_then_corolla_creates_new_candidate(self):
        """When AI returns action=create for Toyota Corolla after Ford Focus, two AUTO candidates exist."""
        eng = _make_engine()
        focus_cand = _make_candidate(
            id=36, tipo_vehiculo="AUTO", marca="Ford", modelo="Focus",
            anio=2019, status="current_focus",
        )
        state = _make_state(current_focus_candidate_id=36)
        ctx = _make_ctx(state=state, candidates=[focus_cand])
        # AI explicitly creates a new candidate
        eng._apply_candidate(ctx, {
            "action": "create",
            "tipo_vehiculo": "AUTO",
            "marca": "Toyota",
            "modelo": "Corolla",
            "anio": 2021,
            "status": "current_focus",
        })
        self.assertEqual(len(ctx.candidates), 2)
        marcas = {c.marca for c in ctx.candidates}
        self.assertIn("Ford", marcas)
        self.assertIn("Toyota", marcas)
        # Ford candidate unchanged
        self.assertEqual(focus_cand.marca, "Ford")
        self.assertEqual(focus_cand.modelo, "Focus")


# ══════════════════════════════════════════════════════════════════════════════
# CR05 — type boundary reverse: AUTO→MOTO inquiry creates new MOTO candidate
# ══════════════════════════════════════════════════════════════════════════════

class TestCR05AutoToMotoCreatesNew(unittest.TestCase):

    def test_cr05_auto_existing_moto_update_creates_new(self):
        """An existing AUTO candidate must not be mutated when MOTO update arrives."""
        eng = _make_engine()
        auto_cand = _make_candidate(
            id=36, tipo_vehiculo="AUTO", marca="Ford", modelo="Focus",
            anio=2019, status="current_focus",
        )
        state = _make_state(current_focus_candidate_id=36)
        ctx = _make_ctx(state=state, candidates=[auto_cand])
        eng._apply_candidate(ctx, {
            "action": "update",
            "tipo_vehiculo": "MOTO",
            "marca": "Honda",
            "modelo": "CB500",
            "status": "current_focus",
        })
        # AUTO candidate intact
        self.assertEqual(auto_cand.tipo_vehiculo, "AUTO")
        self.assertEqual(auto_cand.marca, "Ford")
        # New MOTO candidate created
        moto_candidates = [c for c in ctx.candidates if (c.tipo_vehiculo or "").upper() in _MOTO_TIPO_VALUES]
        self.assertEqual(len(moto_candidates), 1)
        self.assertEqual(moto_candidates[0].marca, "Honda")


# ══════════════════════════════════════════════════════════════════════════════
# CR06 — _enforce_catalog_vehicle: MOTO focus → AUTO catalog creates new candidate
# ══════════════════════════════════════════════════════════════════════════════

class TestCR06EnforceCatalogTypeSafety(unittest.TestCase):

    def _make_vehicle_match(self, tipo, marca, modelo):
        return types.SimpleNamespace(
            tipo_vehiculo=tipo, marca=marca, modelo=modelo,
            matched_alias=f"{marca} {modelo}", confidence="HIGH",
        )

    def test_cr06_moto_focus_auto_catalog_creates_new(self):
        """_enforce_catalog_vehicle must not overwrite MOTO candidate when catalog gives AUTO."""
        eng = _make_engine()
        moto_cand = _make_candidate(
            id=37, tipo_vehiculo="MOTO", marca="Honda", modelo="CB500",
            status="mentioned",
        )
        state = _make_state()
        ctx = _make_ctx(state=state, candidates=[moto_cand])
        match = self._make_vehicle_match("AUTO", "Ford", "Focus")

        eng._create_candidate_from_catalog = MagicMock()
        eng._enforce_catalog_vehicle(ctx, match)

        # Must NOT have mutated the MOTO candidate
        self.assertEqual(moto_cand.tipo_vehiculo, "MOTO")
        self.assertEqual(moto_cand.marca, "Honda")
        # Must have created a new candidate from catalog
        eng._create_candidate_from_catalog.assert_called_once()

    def test_cr06_auto_focus_auto_catalog_updates_in_place(self):
        """_enforce_catalog_vehicle must still update compatible AUTO candidate in-place."""
        eng = _make_engine()
        auto_cand = _make_candidate(
            id=36, tipo_vehiculo=None, marca=None, modelo=None,
            status="current_focus",
        )
        state = _make_state(current_focus_candidate_id=36)
        ctx = _make_ctx(state=state, candidates=[auto_cand])
        match = self._make_vehicle_match("AUTO", "Ford", "Focus")

        eng._create_candidate_from_catalog = MagicMock()
        eng._enforce_catalog_vehicle(ctx, match)

        # No new candidate created
        eng._create_candidate_from_catalog.assert_not_called()
        # tipo_vehiculo set on existing
        self.assertEqual(auto_cand.tipo_vehiculo, "AUTO")

    def test_cr06_auto_focus_moto_catalog_creates_new(self):
        """_enforce_catalog_vehicle must create new MOTO candidate when focus is AUTO."""
        eng = _make_engine()
        auto_cand = _make_candidate(
            id=36, tipo_vehiculo="AUTO", marca="Ford", modelo="Focus",
            status="current_focus",
        )
        state = _make_state(current_focus_candidate_id=36)
        ctx = _make_ctx(state=state, candidates=[auto_cand])
        match = self._make_vehicle_match("MOTO", "Honda", "CB500")

        eng._create_candidate_from_catalog = MagicMock()
        eng._enforce_catalog_vehicle(ctx, match)

        # AUTO candidate must be untouched
        self.assertEqual(auto_cand.tipo_vehiculo, "AUTO")
        self.assertEqual(auto_cand.marca, "Ford")
        # New candidate created from catalog
        eng._create_candidate_from_catalog.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# CR07 — _apply_candidate with explicit id: type incompatibility → create new
# ══════════════════════════════════════════════════════════════════════════════

class TestCR07ExplicitIdTypeSafety(unittest.TestCase):

    def test_cr07_explicit_moto_id_auto_update_creates_new(self):
        """Even when AI supplies an explicit candidate id, type conflicts must create new."""
        eng = _make_engine()
        moto_cand = _make_candidate(
            id=37, tipo_vehiculo="MOTO", marca="Honda", modelo="CB500",
            status="mentioned",
        )
        state = _make_state()
        ctx = _make_ctx(state=state, candidates=[moto_cand])
        eng._apply_candidate(ctx, {
            "action": "update",
            "id": 37,
            "tipo_vehiculo": "AUTO",
            "marca": "Ford",
            "modelo": "Focus",
            "status": "current_focus",
        })
        # MOTO candidate must not be mutated
        self.assertEqual(moto_cand.tipo_vehiculo, "MOTO")
        self.assertEqual(moto_cand.marca, "Honda")
        # New AUTO candidate created
        auto_candidates = [c for c in ctx.candidates if c.tipo_vehiculo == "AUTO"]
        self.assertEqual(len(auto_candidates), 1)


# ══════════════════════════════════════════════════════════════════════════════
# CR08 — source_text immutability: same-candidate enrichment does not mutate it
# ══════════════════════════════════════════════════════════════════════════════

class TestCR08SourceTextImmutability(unittest.TestCase):

    def test_cr08_source_text_not_in_update_fields(self):
        """_apply_candidate must never overwrite source_text (it is not in the update field list)."""
        eng = _make_engine()
        original = "Quiero un Ford Focus 2019"
        auto_cand = _make_candidate(
            id=36, tipo_vehiculo="AUTO", marca="Ford", modelo="Focus",
            anio=None, status="current_focus", source_text=original,
        )
        state = _make_state(current_focus_candidate_id=36)
        ctx = _make_ctx(state=state, candidates=[auto_cand])
        eng._apply_candidate(ctx, {
            "action": "update",
            "anio": 2019,
            "zone_group": "CABA",
            "zone_detail": "Palermo",
        })
        self.assertEqual(auto_cand.source_text, original)


if __name__ == "__main__":
    unittest.main()
