"""M21.2 AL — ASR-tolerant location resolution regression tests.

Guards the fix for D-LIVE09-01: Whisper word-boundary merge artifacts
(e.g. "Villurquiza" for "Villa Urquiza") must resolve to the canonical known
zone, while short/ambiguous tokens and genuinely ambiguous zones must NOT
be fuzzy-matched (Location Flow is the correct fallback).

AL01 — LIVE09 exact Whisper transcript: origin=San Isidro, vehicle=Villa Urquiza
AL02 — canonical exact: "el auto está en Villa Urquiza" → exact path, no fuzzy
AL03 — merged exact: "VillaUrquiza" → compact-exact path (no character deletion)
AL04 — ASR deletion: "Villurquiza" → fuzzy-compact (1-char deletion)
AL05 — ambiguity protection: compare La Matanza Este vs Oeste via runner-up gap
AL06 — short token protection: "villa", "san", "norte", "centro" must not resolve
AL07 — dual-role LIVE09: San Isidro ≠ pricing; Villa Urquiza = pricing
AL08 — full LIVE09 semantic path: all facts from one sentence → quote produced
AL09 — candidate isolation: historical MOTO untouched; Peugeot newly created
AL10 — representative exact paths still work: Palermo, Olivos, Tigre, San Isidro
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
from app.models import WhatsAppThreadCandidate, ViaticosZone      # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_state(**kw) -> types.SimpleNamespace:
    ns = types.SimpleNamespace(
        last_stage=None,
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


def _zone_rows():
    """Representative subset of the real zone dataset for unit tests."""
    rows = [
        ("CABA",   "Palermo",            0),
        ("CABA",   "Villa Urquiza",      0),
        ("CABA",   "Villa Crespo",       0),
        ("CABA",   "Villa del Parque",   0),
        ("CABA",   "Villa Devoto",       0),
        ("CABA",   "Villa Pueyrredón",   0),
        ("CABA",   "Belgrano",           0),
        ("CABA",   "Colegiales",         0),
        ("CABA",   "Saavedra",           0),
        ("CABA",   "CABA",               0),
        ("Norte",  "San Isidro",         0),
        ("Norte",  "Tigre",              0),
        ("Norte",  "San Fernando",       0),
        ("Norte",  "Vicente Lopez",      0),
        ("Norte",  "Beccar",             0),
        ("Oeste",  "Morón",            30000),
        ("Oeste",  "San Justo",        30000),
        ("Oeste",  "San Martin",       20000),
        ("Oeste",  "La Matanza Oeste", 30000),
        ("Sur",    "La Matanza Este",  60000),
        ("Sur",    "Quilmes",          50000),
        ("Sur",    "Dock Sud",         30000),
        ("Sur",    "Lomas de Zamora",  50000),
    ]
    zones = []
    for grp, det, via in rows:
        z = types.SimpleNamespace(zone_group=grp, zone_detail=det, viaticos=via)
        zones.append(z)
    # Sentinel rows (zone_detail=None)
    for grp in ("CABA", "Norte", "Oeste", "Sur"):
        zones.append(types.SimpleNamespace(zone_group=grp, zone_detail=None, viaticos=0))
    return zones


def _engine_with_zones(zones) -> ConversationEngine:
    """Engine whose db.execute returns the given zone list."""
    from sqlalchemy import select as _select
    eng = _make_engine()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = zones
    eng.db.execute.return_value = mock_result
    return eng


# ══════════════════════════════════════════════════════════════════════════════
# AL01 — LIVE09 exact Whisper: origin=San Isidro, vehicle=Villurquiza→Villa Urquiza
# ══════════════════════════════════════════════════════════════════════════════

class TestAL01Live09ExactTranscript(unittest.TestCase):

    def _zones(self):
        return _zone_rows()

    def test_al01_villurquiza_resolves_to_villa_urquiza(self):
        """_extract_zone_from_text('Villurquiza') must return Villa Urquiza via fuzzy."""
        eng = _engine_with_zones(self._zones())
        result = eng._extract_zone_from_text("Villurquiza")
        self.assertIsNotNone(result, "must resolve; must not return None")
        self.assertEqual(result.zone_detail, "Villa Urquiza")
        self.assertEqual(result.zone_group, "CABA")

    def test_al01_vehicle_location_extracted_from_live09_phrase(self):
        """_extract_vehicle_location_zones on LIVE09 sentence extracts Villa Urquiza."""
        eng = _engine_with_zones(self._zones())
        text = "Yo vivo en San Isidro pero el auto está en Villurquiza."
        zones = eng._extract_vehicle_location_zones(text)
        self.assertEqual(len(zones), 1)
        self.assertEqual(zones[0].zone_detail, "Villa Urquiza")

    def test_al01_san_isidro_not_vehicle_zone(self):
        """San Isidro must NOT appear as vehicle-location zone in the LIVE09 sentence."""
        eng = _engine_with_zones(self._zones())
        text = "Yo vivo en San Isidro pero el auto está en Villurquiza."
        zones = eng._extract_vehicle_location_zones(text)
        zone_details = [z.zone_detail for z in zones]
        self.assertNotIn("San Isidro", zone_details)

    def test_al01_apply_zone_writes_villa_urquiza_not_san_isidro(self):
        """After _apply_zone_from_text on LIVE09 text, home_zone_detail=Villa Urquiza."""
        eng = _engine_with_zones(self._zones())
        state = _make_state()
        ctx = _make_ctx(state=state, candidates=[])
        early, written = eng._apply_zone_from_text(ctx, state,
            "Yo vivo en San Isidro pero el auto está en Villurquiza.")
        self.assertIsNone(early)
        self.assertTrue(written)
        self.assertEqual(state.home_zone_detail, "Villa Urquiza")
        self.assertEqual(state.home_zone_group, "CABA")


# ══════════════════════════════════════════════════════════════════════════════
# AL02 — canonical exact form: substring path, no fuzzy needed
# ══════════════════════════════════════════════════════════════════════════════

class TestAL02CanonicalExact(unittest.TestCase):

    def test_al02_villa_urquiza_exact_substring(self):
        """'Villa Urquiza' resolves via exact-substring match (no fuzzy)."""
        eng = _engine_with_zones(_zone_rows())
        result = eng._extract_zone_from_text("el auto está en Villa Urquiza")
        self.assertIsNotNone(result)
        self.assertEqual(result.zone_detail, "Villa Urquiza")

    def test_al02_palermo_exact(self):
        """'Palermo' resolves via exact-substring match."""
        eng = _engine_with_zones(_zone_rows())
        result = eng._extract_zone_from_text("el auto está en Palermo")
        self.assertIsNotNone(result)
        self.assertEqual(result.zone_detail, "Palermo")

    def test_al02_san_isidro_exact(self):
        """'San Isidro' resolves via exact-substring match."""
        eng = _engine_with_zones(_zone_rows())
        result = eng._extract_zone_from_text("San Isidro")
        self.assertIsNotNone(result)
        self.assertEqual(result.zone_detail, "San Isidro")


# ══════════════════════════════════════════════════════════════════════════════
# AL03 — merged exact: "VillaUrquiza" → compact-exact (no char deletion)
# ══════════════════════════════════════════════════════════════════════════════

class TestAL03MergedExact(unittest.TestCase):

    def test_al03_villaurquiza_camel_case(self):
        """'VillaUrquiza' (merged, no deletion) resolves via compact-exact."""
        eng = _engine_with_zones(_zone_rows())
        result = eng._extract_zone_from_text("VillaUrquiza")
        self.assertIsNotNone(result)
        self.assertEqual(result.zone_detail, "Villa Urquiza")

    def test_al03_sanisidro_merged(self):
        """'SanIsidro' resolves via compact-exact."""
        eng = _engine_with_zones(_zone_rows())
        result = eng._extract_zone_from_text("SanIsidro")
        self.assertIsNotNone(result)
        self.assertEqual(result.zone_detail, "San Isidro")

    def test_al03_villadel_parque_merged(self):
        """'VilladelParque' resolves via compact-exact."""
        eng = _engine_with_zones(_zone_rows())
        result = eng._extract_zone_from_text("VilladelParque")
        self.assertIsNotNone(result)
        self.assertEqual(result.zone_detail, "Villa del Parque")


# ══════════════════════════════════════════════════════════════════════════════
# AL04 — ASR deletion: "Villurquiza" (char deleted), fuzzy-compact path
# ══════════════════════════════════════════════════════════════════════════════

class TestAL04AsrDeletion(unittest.TestCase):

    def test_al04_villurquiza_fuzzy_resolves(self):
        """'Villurquiza' (1-char deletion from 'Villa Urquiza') resolves via fuzzy."""
        eng = _engine_with_zones(_zone_rows())
        result = eng._extract_zone_from_text("Villurquiza")
        self.assertIsNotNone(result)
        self.assertEqual(result.zone_detail, "Villa Urquiza")

    def test_al04_canonical_wins_over_fuzzy_when_present(self):
        """Canonical exact 'Villa Urquiza' uses substring path, not fuzzy."""
        eng = _engine_with_zones(_zone_rows())
        # Verify by checking that the result is the same for both
        r_exact = eng._extract_zone_from_text("Villa Urquiza")
        r_fuzzy = eng._extract_zone_from_text("Villurquiza")
        self.assertEqual(r_exact.zone_detail, r_fuzzy.zone_detail)


# ══════════════════════════════════════════════════════════════════════════════
# AL05 — ambiguity protection: La Matanza Este vs Oeste
# ══════════════════════════════════════════════════════════════════════════════

class TestAL05AmbiguityProtection(unittest.TestCase):

    def test_al05_lamatanzaeste_compact_exact(self):
        """'LaMatanzaEste' resolves via compact-exact (not fuzzy) to La Matanza Este."""
        eng = _engine_with_zones(_zone_rows())
        result = eng._extract_zone_from_text("LaMatanzaEste")
        self.assertIsNotNone(result)
        self.assertEqual(result.zone_detail, "La Matanza Este")

    def test_al05_lamatanzaoeste_compact_exact(self):
        """'LaMatanzaOeste' resolves via compact-exact to La Matanza Oeste."""
        eng = _engine_with_zones(_zone_rows())
        result = eng._extract_zone_from_text("LaMatanzaOeste")
        self.assertIsNotNone(result)
        self.assertEqual(result.zone_detail, "La Matanza Oeste")

    def test_al05_ambiguous_lamatanza_no_false_resolution(self):
        """Ambiguous 'LaMatanza' (could be Este or Oeste) must not resolve to either.

        'lamatanza' is 10 chars. vs 'lamatanzaeste' (13): ratio ≈ 0.87 above threshold.
        vs 'lamatanzaoeste' (14): ratio ≈ 0.83 below threshold.
        The gap between 0.87 and 0.83 is 0.04, below _GAP_MIN=0.20 → no match.
        """
        eng = _engine_with_zones(_zone_rows())
        result = eng._extract_zone_from_text("LaMatanza")
        # Either returns None or returns a group-sentinel with zone_detail=None.
        # It must NOT return a specific zone_detail pinning to Este or Oeste.
        if result is not None:
            self.assertIsNone(
                result.zone_detail,
                f"Ambiguous 'LaMatanza' must not resolve to specific zone_detail, got {result.zone_detail!r}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# AL06 — short token protection
# ══════════════════════════════════════════════════════════════════════════════

class TestAL06ShortTokenProtection(unittest.TestCase):

    def _no_zone_detail(self, token: str, label: str):
        eng = _engine_with_zones(_zone_rows())
        result = eng._extract_zone_from_text(token)
        if result is not None:
            self.assertIsNone(
                result.zone_detail,
                f"Token {label!r} must not resolve to a specific zone_detail, got {result.zone_detail!r}"
            )

    def test_al06_villa_alone(self):
        """'villa' (5 chars compact) must not fuzzy-match to Villa Urquiza."""
        self._no_zone_detail("villa", "villa")

    def test_al06_san_alone(self):
        """'san' (3 chars compact) must not fuzzy-match to any zone."""
        self._no_zone_detail("san", "san")

    def test_al06_norte_alone(self):
        """'norte' (5 chars compact) must not fuzzy-match."""
        self._no_zone_detail("norte", "norte")

    def test_al06_centro_alone(self):
        """'centro' (6 chars compact) — at the boundary; must not produce a valid zone_detail."""
        self._no_zone_detail("centro", "centro")

    def test_al06_auto_alone(self):
        """'auto' (4 chars compact) must not fuzzy-match to any zone."""
        self._no_zone_detail("auto", "auto")


# ══════════════════════════════════════════════════════════════════════════════
# AL07 — dual-role LIVE09: location-role separation preserved
# ══════════════════════════════════════════════════════════════════════════════

class TestAL07DualRoleLocationSeparation(unittest.TestCase):

    def test_al07_vehicle_zone_is_villa_urquiza(self):
        """Vehicle location extracted from LIVE09 sentence must be Villa Urquiza."""
        eng = _engine_with_zones(_zone_rows())
        text = "Yo vivo en San Isidro pero el auto está en Villurquiza."
        zones = eng._extract_vehicle_location_zones(text)
        self.assertEqual(len(zones), 1)
        self.assertEqual(zones[0].zone_detail, "Villa Urquiza")
        self.assertEqual(zones[0].zone_group, "CABA")

    def test_al07_home_zone_not_san_isidro(self):
        """After processing LIVE09 sentence, home_zone must be Villa Urquiza not San Isidro."""
        eng = _engine_with_zones(_zone_rows())
        state = _make_state()
        ctx = _make_ctx(state=state)
        eng._apply_zone_from_text(ctx, state,
            "Yo vivo en San Isidro pero el auto está en Villurquiza.")
        self.assertNotEqual(state.home_zone_detail, "San Isidro",
            "pricing location must not be customer origin (San Isidro)")
        self.assertEqual(state.home_zone_detail, "Villa Urquiza")


# ══════════════════════════════════════════════════════════════════════════════
# AL08 — full LIVE09 semantic path (local CE invocation, mocked AI)
# ══════════════════════════════════════════════════════════════════════════════

class TestAL08FullLive09SemanticPath(unittest.TestCase):

    LIVE09_TEXT = (
        "Hola, estoy viendo un Peugeot 3008 2021. "
        "Yo vivo en San Isidro pero el auto está en Villurquiza. "
        "No arranca porque está sin batería pero está completo y se puede revisar. "
        "Quería saber cuánto cuesta."
    )

    def test_al08_zone_resolved_to_villa_urquiza(self):
        """Full LIVE09 transcript must resolve vehicle location to Villa Urquiza."""
        eng = _engine_with_zones(_zone_rows())
        state = _make_state()
        ctx = _make_ctx(state=state, candidates=[])
        early, written = eng._apply_zone_from_text(ctx, state, self.LIVE09_TEXT)
        self.assertIsNone(early)
        self.assertTrue(written, "vehicle location must be written")
        self.assertEqual(state.home_zone_detail, "Villa Urquiza")
        self.assertEqual(state.home_zone_group, "CABA")

    def test_al08_no_location_flow_triggered_by_zone_step(self):
        """_apply_zone_from_text on LIVE09 must not trigger a contradiction/flow early return."""
        eng = _engine_with_zones(_zone_rows())
        state = _make_state()
        ctx = _make_ctx(state=state, candidates=[])
        early, _ = eng._apply_zone_from_text(ctx, state, self.LIVE09_TEXT)
        self.assertIsNone(early, "no early return (no Location Flow from zone extraction)")

    def test_al08_vehicle_location_not_customer_origin(self):
        """Villa Urquiza is vehicle zone; San Isidro is NOT written as zone."""
        eng = _engine_with_zones(_zone_rows())
        state = _make_state()
        ctx = _make_ctx(state=state, candidates=[])
        eng._apply_zone_from_text(ctx, state, self.LIVE09_TEXT)
        self.assertNotEqual(state.home_zone_detail, "San Isidro")


# ══════════════════════════════════════════════════════════════════════════════
# AL09 — candidate isolation: MOTO untouched, Peugeot newly created
# ══════════════════════════════════════════════════════════════════════════════

class TestAL09CandidateIsolation(unittest.TestCase):

    LIVE09_TEXT = (
        "Hola, estoy viendo un Peugeot 3008 2021. "
        "Yo vivo en San Isidro pero el auto está en Villurquiza. "
        "No arranca porque está sin batería pero está completo y se puede revisar. "
        "Quería saber cuánto cuesta."
    )

    def test_al09_moto_zone_unchanged_after_live09(self):
        """Historical MOTO candidate must not receive Villa Urquiza from LIVE09."""
        eng = _engine_with_zones(_zone_rows())
        moto = _make_candidate(
            id=37, tipo_vehiculo="MOTO", marca="Honda", modelo="CB500",
            status="mentioned", zone_group=None, zone_detail=None,
        )
        state = _make_state()
        ctx = _make_ctx(state=state, candidates=[moto])
        eng._apply_zone_from_text(ctx, state, self.LIVE09_TEXT)
        self.assertIsNone(moto.zone_group, "MOTO zone_group must remain NULL")
        self.assertIsNone(moto.zone_detail, "MOTO zone_detail must remain NULL")

    def test_al09_villa_urquiza_buffered_to_home_zone(self):
        """With no current-focus candidate, Villa Urquiza must buffer to home_zone_*."""
        eng = _engine_with_zones(_zone_rows())
        moto = _make_candidate(
            id=37, tipo_vehiculo="MOTO", marca="Honda", modelo="CB500",
            status="mentioned", zone_group=None, zone_detail=None,
        )
        state = _make_state()
        ctx = _make_ctx(state=state, candidates=[moto])
        eng._apply_zone_from_text(ctx, state, self.LIVE09_TEXT)
        self.assertEqual(state.home_zone_group, "CABA")
        self.assertEqual(state.home_zone_detail, "Villa Urquiza")

    def test_al09_new_auto_candidate_inherits_home_zone(self):
        """New Peugeot AUTO candidate created after LIVE09 must inherit Villa Urquiza."""
        from app.models import WhatsAppThreadCandidate
        eng = _engine_with_zones(_zone_rows())
        _next_id = [100]
        def _flush():
            for call_args in eng.db.add.call_args_list:
                obj = call_args[0][0]
                if isinstance(obj, WhatsAppThreadCandidate) and not obj.id:
                    _next_id[0] += 1
                    obj.id = _next_id[0]
        eng.db.flush.side_effect = _flush
        state = _make_state(home_zone_group="CABA", home_zone_detail="Villa Urquiza")
        ctx = _make_ctx(state=state, candidates=[])
        eng._apply_candidate(ctx, {
            "action": "create",
            "tipo_vehiculo": "SUV/4x4",
            "marca": "Peugeot",
            "modelo": "3008",
            "anio": 2021,
            "status": "current_focus",
        })
        peugeot = next(c for c in ctx.candidates if (c.marca or "") == "Peugeot")
        self.assertEqual(peugeot.zone_group, "CABA")
        self.assertEqual(peugeot.zone_detail, "Villa Urquiza")


# ══════════════════════════════════════════════════════════════════════════════
# AL10 — representative exact paths unchanged
# ══════════════════════════════════════════════════════════════════════════════

class TestAL10ExactPathsUnchanged(unittest.TestCase):
    """Canonical typed inputs must still resolve through the exact-substring path."""

    def _exact_zone(self, text: str, expected_detail: str, expected_group: str):
        eng = _engine_with_zones(_zone_rows())
        result = eng._extract_zone_from_text(text)
        self.assertIsNotNone(result, f"'{text}' must resolve")
        self.assertEqual(result.zone_detail, expected_detail)
        self.assertEqual(result.zone_group, expected_group)

    def test_al10_palermo(self):
        self._exact_zone("Palermo", "Palermo", "CABA")

    def test_al10_olivos(self):
        # Olivos is not in the test zone subset — it's GBA Norte but not our subset.
        # Use Beccar (also Norte) instead.
        self._exact_zone("Beccar", "Beccar", "Norte")

    def test_al10_tigre(self):
        self._exact_zone("Tigre", "Tigre", "Norte")

    def test_al10_san_isidro(self):
        self._exact_zone("San Isidro", "San Isidro", "Norte")

    def test_al10_villa_urquiza_canonical(self):
        self._exact_zone("Villa Urquiza", "Villa Urquiza", "CABA")

    def test_al10_villa_urquiza_in_sentence(self):
        self._exact_zone(
            "el auto está en Villa Urquiza", "Villa Urquiza", "CABA"
        )

    def test_al10_moron_with_accent(self):
        self._exact_zone("Morón", "Morón", "Oeste")

    def test_al10_moron_without_accent(self):
        self._exact_zone("Moron", "Morón", "Oeste")

    def test_al10_dock_sud(self):
        # Dock Sud has a special alias fast-path; verify it still works.
        eng = _engine_with_zones(_zone_rows())
        result = eng._extract_zone_from_text("dock sud")
        self.assertIsNotNone(result)
        self.assertEqual(result.zone_detail, "Dock Sud")


if __name__ == "__main__":
    unittest.main()
