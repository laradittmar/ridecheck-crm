"""M21.2-DATA Phase 11: Tracking parser and persistence safety tests (TRACK-01–10)."""
from __future__ import annotations

import re
import sys
import types
import unittest

ROOT_DIR = __import__("pathlib").Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

for _mod in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg
import sqlalchemy.dialects.postgresql.json as _pgj
_pg.JSONB = sqlalchemy.JSON
_pgj.JSONB = sqlalchemy.JSON

_FORM_HEADER = "quiero solicitar una revision pre-compra"
_VALID_RC = re.compile(r"^RC-[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{4}$")
_VALID_REF_VALUES = {"ga", "ig", "fb", "org", "dir", "otro"}


def _parse(texts):
    from app.services.conversation_engine import _parse_website_form
    return _parse_website_form(texts)


def _form_message(*, tipo="Auto pequeño o mediano", localidad="Palermo",
                  ref=None, rc=None, extra=""):
    lines = [
        "Hola, quiero solicitar una revisión pre-compra.",
        "* Nombre: Test User",
        f"* Auto a revisar: Toyota Corolla 2020",
        f"* Tipo: {tipo}",
        f"* Localidad: {localidad}",
        "* Total estimado: $140.000",
    ]
    if ref and rc:
        lines.append(f"ref: {ref} · cod: {rc}")
    elif ref:
        lines.append(f"ref: {ref}")
    if extra:
        lines.append(extra)
    return "\n".join(lines)


class TestTrackingParser(unittest.TestCase):
    """TRACK-01 through TRACK-08: parsing and validation."""

    def test_track01_ref_ga_rc_parsed(self):
        """TRACK-01: ref=ga, cod=RC-ABCD → both extracted."""
        msg = _form_message(ref="ga", rc="RC-ABCD")
        result = _parse([msg])
        self.assertIsNotNone(result)
        self.assertEqual(result.get("ref"), "ga")
        self.assertEqual(result.get("rc_code"), "RC-ABCD")

    def test_track01_rc_is_uppercase(self):
        msg = _form_message(ref="ga", rc="RC-ABCD")
        result = _parse([msg])
        self.assertEqual(result["rc_code"], "RC-ABCD")

    def test_track02_ref_ig_and_numeric_rc(self):
        """TRACK-02: ref=ig, cod=RC-2345 → both extracted; RC validated against alphabet."""
        msg = _form_message(ref="ig", rc="RC-2345")
        result = _parse([msg])
        self.assertIsNotNone(result)
        self.assertEqual(result.get("ref"), "ig")
        self.assertEqual(result.get("rc_code"), "RC-2345")
        self.assertRegex(result["rc_code"], _VALID_RC)

    def test_track03_unknown_ref_preserved_not_mapped(self):
        """TRACK-03: unknown ref value is stored as-is, not remapped."""
        msg = _form_message(ref="unknown_campaign_xyz")
        result = _parse([msg])
        self.assertIsNotNone(result)
        # Stored (do not invent mapping), capped at 10 chars
        self.assertIsNotNone(result.get("ref"))
        stored = result["ref"]
        self.assertLessEqual(len(stored), 10)

    def test_track04_invalid_rc_with_forbidden_chars_rejected(self):
        """TRACK-04: RC with O/I/L/0/1 does not match regex and is not stored."""
        msg = _form_message(ref="ga", rc="RC-OIL1")
        result = _parse([msg])
        # rc_code should not appear (regex fails: O, I, L, 1 not in alphabet)
        self.assertIsNone(result.get("rc_code"),
            f"rc_code should be None for invalid RC-OIL1, got {result.get('rc_code')}")

    def test_track04_invalid_rc_wrong_prefix_rejected(self):
        """TRACK-04: non-RC prefix not stored."""
        msg = _form_message(ref="ga", rc="XX-ABCD")
        result = _parse([msg])
        self.assertIsNone(result.get("rc_code"))

    def test_track05_direct_whatsapp_no_tokens(self):
        """TRACK-05: no ref/rc in message → both None."""
        msg = _form_message()  # no ref, no rc
        result = _parse([msg])
        self.assertIsNotNone(result)
        self.assertIsNone(result.get("ref"))
        self.assertIsNone(result.get("rc_code"))

    def test_track06_tracking_line_does_not_become_vehicle_text(self):
        """TRACK-06: 'ref: ga · cod: RC-ABCD' must not appear in vehicle_text."""
        msg = _form_message(ref="ga", rc="RC-ABCD")
        result = _parse([msg])
        vehicle = result.get("vehicle_text", "")
        self.assertNotIn("ref:", vehicle.lower())
        self.assertNotIn("cod:", vehicle.lower())
        self.assertNotIn("RC-", vehicle)

    def test_track07_tracking_line_does_not_become_zone_detail(self):
        """TRACK-07: tracking line must not be parsed as zone_detail."""
        msg = _form_message(ref="ga", rc="RC-ABCD")
        result = _parse([msg])
        zone = result.get("zone_detail", "")
        self.assertNotIn("ref:", str(zone).lower())
        self.assertNotIn("RC-", str(zone))

    def test_track08_tracking_tokens_do_not_affect_tipo(self):
        """TRACK-08: tipo field must come from the * Tipo: line, not tracking."""
        msg = _form_message(tipo="Camioneta", ref="ga", rc="RC-ABCD")
        result = _parse([msg])
        # submitted_tipo should be 'Camioneta'
        self.assertEqual(result.get("submitted_tipo"), "Camioneta")

    def test_all_six_ref_values_pass(self):
        for ref in _VALID_REF_VALUES:
            with self.subTest(ref=ref):
                msg = _form_message(ref=ref, rc="RC-ABCD")
                result = _parse([msg])
                self.assertIsNotNone(result)
                self.assertIsNotNone(result.get("ref"))


class TestTrackingCanal(unittest.TestCase):
    """TRACK-09: canal semantics unchanged."""

    def test_track09_canal_is_not_ref_code(self):
        """Canal is the operational source surface, not the marketing attribution."""
        # We verify via the submitted_tipo → tipo_vehiculo path and canal behavior:
        # _parse_website_form does not touch canal (CE sets it separately).
        msg = _form_message(ref="ga", rc="RC-ABCD")
        result = _parse([msg])
        # canal is not a field in form_data — CE sets it on the lead
        self.assertNotIn("canal", result)


class TestExistingWebsiteFormIntegrity(unittest.TestCase):
    """TRACK-10: existing website form parsing still works correctly."""

    def test_track10_form_without_tracking_still_parses(self):
        lines = [
            "Hola, quiero solicitar una revisión pre-compra.",
            "* Nombre: Ana García",
            "* Auto a revisar: Honda Civic 2019",
            "* Tipo: Auto pequeño o mediano",
            "* Localidad: Almagro",
            "* Total estimado: $140.000",
        ]
        result = _parse(["\n".join(lines)])
        self.assertIsNotNone(result)
        self.assertEqual(result.get("zone_detail"), "Almagro")
        self.assertEqual(result.get("submitted_tipo"), "Auto pequeño o mediano")
        self.assertIsNone(result.get("ref"))
        self.assertIsNone(result.get("rc_code"))

    def test_track10_form_with_tracking_still_parses_vehicle(self):
        lines = [
            "Hola, quiero solicitar una revisión pre-compra.",
            "* Auto a revisar: Chevrolet Spin 2021",
            "* Tipo: Utilitario",
            "* Localidad: Berazategui",
            "* Total estimado: $230.000",
            "ref: ig · cod: RC-XY23",
        ]
        result = _parse(["\n".join(lines)])
        self.assertIsNotNone(result)
        self.assertEqual(result.get("submitted_tipo"), "Utilitario")
        self.assertEqual(result.get("zone_detail"), "Berazategui")
        self.assertEqual(result.get("ref"), "ig")
        self.assertEqual(result.get("rc_code"), "RC-XY23")

    def test_track10_utilitario_tipo_maps_correctly(self):
        from app.services.conversation_engine import _normalize_submitted_tipo
        self.assertEqual(_normalize_submitted_tipo("Utilitario"), "UTILITARIO")

    def test_track10_camioneta_tipo_maps_correctly(self):
        from app.services.conversation_engine import _normalize_submitted_tipo
        self.assertEqual(_normalize_submitted_tipo("Camioneta"), "CAMIONETA")


if __name__ == "__main__":
    unittest.main()
