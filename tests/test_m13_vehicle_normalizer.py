"""
M13 Vehicle Normalizer — Regression Tests
==========================================
Tests verify the deterministic catalog lookup logic that lives in the
n8n 'Vehicle Normalizer' Code node. We extract and test the pure lookup
function in Python to keep tests fast and independent.

The catalog matches the JS node exactly (same aliases, same typos).
"""
from __future__ import annotations

import re
import sys
import unicodedata
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ── Python replica of the Vehicle Normalizer catalog ─────────────────────────

def _norm(s: str) -> str:
    n = unicodedata.normalize("NFD", s or "")
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    n = re.sub(r"[^a-z0-9\s]", " ", n.lower())
    return re.sub(r"\s+", " ", n).strip()


CAT = {
    "suran":             {"marca": "Volkswagen",    "modelo": "Suran",    "tipo_vehiculo": "AUTO",             "typo": None},
    "siran":             {"marca": "Volkswagen",    "modelo": "Suran",    "tipo_vehiculo": "AUTO",             "typo": "suran"},
    "wv suran":          {"marca": "Volkswagen",    "modelo": "Suran",    "tipo_vehiculo": "AUTO",             "typo": None},
    "vw suran":          {"marca": "Volkswagen",    "modelo": "Suran",    "tipo_vehiculo": "AUTO",             "typo": None},
    "volkswagen suran":  {"marca": "Volkswagen",    "modelo": "Suran",    "tipo_vehiculo": "AUTO",             "typo": None},
    "peugeot 208":       {"marca": "Peugeot",       "modelo": "208",      "tipo_vehiculo": "AUTO",             "typo": None},
    "etios":             {"marca": "Toyota",        "modelo": "Etios",    "tipo_vehiculo": "AUTO",             "typo": None},
    "toyota etios":      {"marca": "Toyota",        "modelo": "Etios",    "tipo_vehiculo": "AUTO",             "typo": None},
    "polo":              {"marca": "Volkswagen",    "modelo": "Polo",     "tipo_vehiculo": "AUTO",             "typo": None},
    "vw polo":           {"marca": "Volkswagen",    "modelo": "Polo",     "tipo_vehiculo": "AUTO",             "typo": None},
    "volkswagen polo":   {"marca": "Volkswagen",    "modelo": "Polo",     "tipo_vehiculo": "AUTO",             "typo": None},
    "gol":               {"marca": "Volkswagen",    "modelo": "Gol",      "tipo_vehiculo": "AUTO",             "typo": None},
    "vw gol":            {"marca": "Volkswagen",    "modelo": "Gol",      "tipo_vehiculo": "AUTO",             "typo": None},
    "volkswagen gol":    {"marca": "Volkswagen",    "modelo": "Gol",      "tipo_vehiculo": "AUTO",             "typo": None},
    "onix":              {"marca": "Chevrolet",     "modelo": "Onix",     "tipo_vehiculo": "AUTO",             "typo": None},
    "chevrolet onix":    {"marca": "Chevrolet",     "modelo": "Onix",     "tipo_vehiculo": "AUTO",             "typo": None},
    "focus":             {"marca": "Ford",          "modelo": "Focus",    "tipo_vehiculo": "AUTO",             "typo": None},
    "ford focus":        {"marca": "Ford",          "modelo": "Focus",    "tipo_vehiculo": "AUTO",             "typo": None},
    "fit":               {"marca": "Honda",         "modelo": "Fit",      "tipo_vehiculo": "AUTO",             "typo": None},
    "honda fit":         {"marca": "Honda",         "modelo": "Fit",      "tipo_vehiculo": "AUTO",             "typo": None},
    "corolla":           {"marca": "Toyota",        "modelo": "Corolla",  "tipo_vehiculo": "AUTO",             "typo": None},
    "toyota corolla":    {"marca": "Toyota",        "modelo": "Corolla",  "tipo_vehiculo": "AUTO",             "typo": None},
    "civic":             {"marca": "Honda",         "modelo": "Civic",    "tipo_vehiculo": "AUTO",             "typo": None},
    "honda civic":       {"marca": "Honda",         "modelo": "Civic",    "tipo_vehiculo": "AUTO",             "typo": None},
    "sandero":           {"marca": "Renault",       "modelo": "Sandero",  "tipo_vehiculo": "AUTO",             "typo": None},
    "renault sandero":   {"marca": "Renault",       "modelo": "Sandero",  "tipo_vehiculo": "AUTO",             "typo": None},
    "cronos":            {"marca": "Fiat",          "modelo": "Cronos",   "tipo_vehiculo": "AUTO",             "typo": None},
    "fiat cronos":       {"marca": "Fiat",          "modelo": "Cronos",   "tipo_vehiculo": "AUTO",             "typo": None},
    "208":               {"marca": "Peugeot",       "modelo": "208",      "tipo_vehiculo": "AUTO",             "typo": None},
    "taos":              {"marca": "Volkswagen",    "modelo": "Taos",     "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "vw taos":           {"marca": "Volkswagen",    "modelo": "Taos",     "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "volkswagen taos":   {"marca": "Volkswagen",    "modelo": "Taos",     "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "volkswaguen taos":  {"marca": "Volkswagen",    "modelo": "Taos",     "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "glb":               {"marca": "Mercedes-Benz", "modelo": "GLB",      "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "mercedes glb":      {"marca": "Mercedes-Benz", "modelo": "GLB",      "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "mercedes-benz glb": {"marca": "Mercedes-Benz", "modelo": "GLB",      "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "renegade":          {"marca": "Jeep",          "modelo": "Renegade", "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "jeep renegade":     {"marca": "Jeep",          "modelo": "Renegade", "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "compass":           {"marca": "Jeep",          "modelo": "Compass",  "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "jeep compass":      {"marca": "Jeep",          "modelo": "Compass",  "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "ecosport":          {"marca": "Ford",          "modelo": "Ecosport", "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "ford ecosport":     {"marca": "Ford",          "modelo": "Ecosport", "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "hilux":             {"marca": "Toyota",        "modelo": "Hilux",    "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "toyota hilux":      {"marca": "Toyota",        "modelo": "Hilux",    "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "ranger":            {"marca": "Ford",          "modelo": "Ranger",   "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "ford ranger":       {"marca": "Ford",          "modelo": "Ranger",   "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "amarok":            {"marca": "Volkswagen",    "modelo": "Amarok",   "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "vw amarok":         {"marca": "Volkswagen",    "modelo": "Amarok",   "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "volkswagen amarok": {"marca": "Volkswagen",    "modelo": "Amarok",   "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "toro":              {"marca": "Fiat",          "modelo": "Toro",     "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "fiat toro":         {"marca": "Fiat",          "modelo": "Toro",     "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "qashqai":           {"marca": "Nissan",        "modelo": "Qashqai",  "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "nissan qashqai":    {"marca": "Nissan",        "modelo": "Qashqai",  "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "tracker":           {"marca": "Chevrolet",     "modelo": "Tracker",  "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "chevrolet tracker": {"marca": "Chevrolet",     "modelo": "Tracker",  "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "duster":            {"marca": "Renault",       "modelo": "Duster",   "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
    "renault duster":    {"marca": "Renault",       "modelo": "Duster",   "tipo_vehiculo": "SUV_4X4_DEPORTIVO","typo": None},
}

_SORTED_KEYS = sorted(CAT.keys(), key=len, reverse=True)


def normalize_vehicle(text: str) -> dict | None:
    """Python replica of the JS Vehicle Normalizer lookup function."""
    n = _norm(text)
    for key in _SORTED_KEYS:
        pattern = r"(?:^|\s)" + re.escape(key) + r"(?:\s|$)"
        if re.search(pattern, n) or n == key:
            entry = CAT[key]
            is_typo = bool(entry["typo"])
            return {
                "found": True,
                "marca": entry["marca"],
                "modelo": entry["modelo"],
                "tipo_vehiculo": entry["tipo_vehiculo"],
                "confidence": "medium" if is_typo else "high",
                "matched_alias": key,
                "needs_confirmation": is_typo,
                "canonical": entry["typo"] or key,
            }
    return None


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestSuranNormalization(unittest.TestCase):
    """CASE A/B — Suran alias family."""

    def test_suran_clean_maps_to_auto(self):
        """CASE B: 'Suran 2015 en Avellaneda' → Volkswagen Suran AUTO."""
        r = normalize_vehicle("Suran 2015 en Avellaneda")
        self.assertIsNotNone(r)
        self.assertEqual(r["marca"], "Volkswagen")
        self.assertEqual(r["modelo"], "Suran")
        self.assertEqual(r["tipo_vehiculo"], "AUTO")
        self.assertEqual(r["confidence"], "high")
        self.assertFalse(r["needs_confirmation"])

    def test_siran_typo_detected_medium_confidence(self):
        """CASE A: 'siran 2015' → medium confidence, needs confirmation."""
        r = normalize_vehicle("siran 2015")
        self.assertIsNotNone(r)
        self.assertEqual(r["modelo"], "Suran")
        self.assertEqual(r["tipo_vehiculo"], "AUTO")
        self.assertEqual(r["confidence"], "medium")
        self.assertTrue(r["needs_confirmation"])

    def test_siran_with_location(self):
        """Full live message: 'Avellaneda, siran 2015'."""
        r = normalize_vehicle("Avellaneda, siran 2015")
        self.assertIsNotNone(r)
        self.assertEqual(r["modelo"], "Suran")
        self.assertEqual(r["confidence"], "medium")
        self.assertTrue(r["needs_confirmation"])

    def test_vw_suran_alias_high_confidence(self):
        r = normalize_vehicle("vw suran 2017 Palermo")
        self.assertIsNotNone(r)
        self.assertEqual(r["confidence"], "high")
        self.assertFalse(r["needs_confirmation"])

    def test_suran_confirmed_message(self):
        """'Sí, Suran 2015' — after confirmation, Suran should be high confidence."""
        r = normalize_vehicle("Sí, Suran 2015")
        self.assertIsNotNone(r)
        self.assertEqual(r["confidence"], "high")
        self.assertFalse(r["needs_confirmation"])


class TestSuvNormalization(unittest.TestCase):
    """CASE C — Known SUV models."""

    def test_taos_clean(self):
        r = normalize_vehicle("Taos 2020 en Beccar")
        self.assertIsNotNone(r)
        self.assertEqual(r["marca"], "Volkswagen")
        self.assertEqual(r["modelo"], "Taos")
        self.assertEqual(r["tipo_vehiculo"], "SUV_4X4_DEPORTIVO")
        self.assertEqual(r["confidence"], "high")

    def test_volkswaguen_taos_typo(self):
        """'volkswaguen taos' is a common typo for Volkswagen Taos."""
        r = normalize_vehicle("volkswaguen taos 2022")
        self.assertIsNotNone(r)
        self.assertEqual(r["modelo"], "Taos")
        self.assertEqual(r["tipo_vehiculo"], "SUV_4X4_DEPORTIVO")

    def test_hilux_auto(self):
        r = normalize_vehicle("hilux 2019 en Quilmes")
        self.assertIsNotNone(r)
        self.assertEqual(r["marca"], "Toyota")
        self.assertEqual(r["modelo"], "Hilux")
        self.assertEqual(r["tipo_vehiculo"], "SUV_4X4_DEPORTIVO")

    def test_glb_mercedes(self):
        r = normalize_vehicle("GLB 2023 Mercedes")
        self.assertIsNotNone(r)
        self.assertEqual(r["marca"], "Mercedes-Benz")
        self.assertEqual(r["tipo_vehiculo"], "SUV_4X4_DEPORTIVO")


class TestAutoNormalization(unittest.TestCase):
    """Other AUTO models."""

    def test_etios(self):
        r = normalize_vehicle("Toyota Etios 2018")
        self.assertIsNotNone(r)
        self.assertEqual(r["tipo_vehiculo"], "AUTO")

    def test_polo(self):
        r = normalize_vehicle("VW Polo 2020 Belgrano")
        self.assertIsNotNone(r)
        self.assertEqual(r["modelo"], "Polo")
        self.assertEqual(r["tipo_vehiculo"], "AUTO")

    def test_gol(self):
        r = normalize_vehicle("Gol 2015 en Lomas de Zamora")
        self.assertIsNotNone(r)
        self.assertEqual(r["modelo"], "Gol")
        self.assertEqual(r["tipo_vehiculo"], "AUTO")

    def test_focus(self):
        r = normalize_vehicle("Ford Focus 2016")
        self.assertIsNotNone(r)
        self.assertEqual(r["tipo_vehiculo"], "AUTO")

    def test_208(self):
        r = normalize_vehicle("Peugeot 208 2019 en San Isidro")
        self.assertIsNotNone(r)
        self.assertEqual(r["tipo_vehiculo"], "AUTO")


class TestUnknownModel(unittest.TestCase):
    """CASE D — Unknown models must NOT produce a result."""

    def test_unknown_model_returns_none(self):
        self.assertIsNone(normalize_vehicle("modelo raro 2015 en Palermo"))

    def test_generic_auto_returns_none(self):
        """'quiero revisar un auto' should not match any catalog entry."""
        self.assertIsNone(normalize_vehicle("quiero revisar un auto"))

    def test_empty_message_returns_none(self):
        self.assertIsNone(normalize_vehicle(""))

    def test_location_only_returns_none(self):
        self.assertIsNone(normalize_vehicle("Avellaneda"))

    def test_year_only_returns_none(self):
        self.assertIsNone(normalize_vehicle("2020"))


class TestNormalizerOutputStructure(unittest.TestCase):
    """Output structure matches what the n8n Candidate/State Updater expects."""

    def test_high_confidence_has_required_fields(self):
        r = normalize_vehicle("Toyota Corolla 2021")
        self.assertIsNotNone(r)
        for field in ("found", "marca", "modelo", "tipo_vehiculo", "confidence", "matched_alias", "needs_confirmation"):
            self.assertIn(field, r)
        self.assertTrue(r["found"])
        self.assertFalse(r["needs_confirmation"])

    def test_medium_confidence_marks_needs_confirmation(self):
        r = normalize_vehicle("siran 2016 Palermo")
        self.assertIsNotNone(r)
        self.assertEqual(r["confidence"], "medium")
        self.assertTrue(r["needs_confirmation"])
        self.assertEqual(r["canonical"], "suran")

    def test_tipo_vehiculo_enum_values_valid(self):
        """All tipos in catalog must be valid enum values."""
        valid = {"AUTO", "SUV_4X4_DEPORTIVO", "CLASICO", "MOTO", "ESCANEO_MOTOR"}
        for alias, entry in CAT.items():
            self.assertIn(entry["tipo_vehiculo"], valid, f"Bad tipo for alias {alias!r}")


class TestRegressionExistingModels(unittest.TestCase):
    """Ensure M9 models still work (regression for prior milestones)."""

    def test_hilux_still_suv(self):
        r = normalize_vehicle("Toyota Hilux 2020 Tigre")
        self.assertIsNotNone(r)
        self.assertEqual(r["tipo_vehiculo"], "SUV_4X4_DEPORTIVO")

    def test_renegade_still_suv(self):
        r = normalize_vehicle("Jeep Renegade 2021 San Telmo")
        self.assertIsNotNone(r)
        self.assertEqual(r["tipo_vehiculo"], "SUV_4X4_DEPORTIVO")

    def test_onix_still_auto(self):
        r = normalize_vehicle("Chevrolet Onix 2019 Flores")
        self.assertIsNotNone(r)
        self.assertEqual(r["tipo_vehiculo"], "AUTO")

    def test_tracker_suv(self):
        r = normalize_vehicle("Chevrolet Tracker 2022 Recoleta")
        self.assertIsNotNone(r)
        self.assertEqual(r["tipo_vehiculo"], "SUV_4X4_DEPORTIVO")


if __name__ == "__main__":
    unittest.main(verbosity=2)
