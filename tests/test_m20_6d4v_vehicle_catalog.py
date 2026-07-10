"""RC25–RC36 — Deterministic vehicle catalog tests (M20.6D.4V.1).

Tests the new JSON-backed vehicle_catalog.py.  No database, no AI, no outbound.
Pure unit tests against lookup_vehicle().
"""
import sys
import pytest

# Insert backend path so relative imports resolve
sys.path.insert(0, "backend")

from app.services.vehicle_catalog import lookup_vehicle, VehicleMatch


# ─────────────────────────────────────────────────────────────────────────────
# RC25 — Brand + model normal text
# ─────────────────────────────────────────────────────────────────────────────
class TestRC25BrandModelNormalText:
    """Quiero revisar un Ford Focus 2019 → Ford Focus AUTO."""

    def test_ford_focus_recognized(self):
        r = lookup_vehicle("Quiero revisar un Ford Focus 2019 en Palermo")
        assert r is not None
        assert r.marca == "Ford"
        assert r.modelo == "Focus"

    def test_ford_focus_tipo(self):
        r = lookup_vehicle("Quiero revisar un Ford Focus 2019 en Palermo")
        assert r.tipo_vehiculo == "AUTO"

    def test_ford_focus_not_recognition_only(self):
        r = lookup_vehicle("Quiero revisar un Ford Focus 2019 en Palermo")
        assert not r.recognition_only

    def test_ford_focus_confidence_high(self):
        r = lookup_vehicle("Quiero revisar un Ford Focus 2019 en Palermo")
        assert r.confidence == "high"


# ─────────────────────────────────────────────────────────────────────────────
# RC26 — Case-insensitive Honda Fit
# ─────────────────────────────────────────────────────────────────────────────
class TestRC26CaseInsensitive:
    """Honda FIT (uppercase) must extract same as honda fit."""

    def test_honda_fit_uppercase(self):
        r = lookup_vehicle("Tengo un Honda FIT 2018 en Belgrano")
        assert r is not None
        assert r.marca == "Honda"
        assert r.modelo == "Fit"

    def test_honda_fit_tipo(self):
        r = lookup_vehicle("Tengo un Honda FIT 2018 en Belgrano")
        assert r.tipo_vehiculo == "AUTO"

    def test_honda_fit_mixed_case(self):
        r = lookup_vehicle("HONDA fit")
        assert r is not None
        assert r.modelo == "Fit"

    def test_honda_fit_all_lower(self):
        r = lookup_vehicle("honda fit")
        assert r is not None
        assert r.modelo == "Fit"


# ─────────────────────────────────────────────────────────────────────────────
# RC27 — Model-only Tracker
# ─────────────────────────────────────────────────────────────────────────────
class TestRC27ModelOnlyTracker:
    """Model-only alias 'tracker' must match Chevrolet Tracker SUV."""

    def test_tracker_model_only(self):
        r = lookup_vehicle("Quiero revisar una Tracker 2020 en Palermo")
        assert r is not None
        assert r.marca == "Chevrolet"
        assert r.modelo == "Tracker"

    def test_tracker_tipo_suv(self):
        r = lookup_vehicle("Quiero revisar una Tracker 2020 en Palermo")
        assert r.tipo_vehiculo == "SUV_4X4_DEPORTIVO"

    def test_tracker_not_classification_auto(self):
        r = lookup_vehicle("Quiero revisar una Tracker 2020 en Palermo")
        assert r.tipo_vehiculo != "AUTO"

    def test_chevrolet_tracker_full(self):
        r = lookup_vehicle("Chevrolet Tracker 2022")
        assert r is not None
        assert r.tipo_vehiculo == "SUV_4X4_DEPORTIVO"


# ─────────────────────────────────────────────────────────────────────────────
# RC28 — Ecosport spelling variants
# ─────────────────────────────────────────────────────────────────────────────
class TestRC28EcosportVariants:
    """All common Ecosport spellings must extract the same vehicle."""

    @pytest.mark.parametrize("text", [
        "Ecosport 2011",
        "EcoSport 2011",
        "Eco Sport 2011",
        "Ford Ecosport 2011",
        "Ford EcoSport 2011",
        "ford eco sport 2011",
    ])
    def test_ecosport_variant(self, text):
        r = lookup_vehicle(text)
        assert r is not None, f"No match for: {text!r}"
        assert r.marca == "Ford", f"Expected Ford, got {r.marca!r} for {text!r}"
        assert r.modelo == "Ecosport", f"Expected Ecosport, got {r.modelo!r} for {text!r}"
        assert r.tipo_vehiculo == "SUV_4X4_DEPORTIVO", f"Wrong tipo for {text!r}"


# ─────────────────────────────────────────────────────────────────────────────
# RC29 — VW / Volkswagen aliases
# ─────────────────────────────────────────────────────────────────────────────
class TestRC29VwAliases:
    """VW / vw / Volkswagen prefix all resolve to Volkswagen Taos."""

    @pytest.mark.parametrize("text", [
        "VW Taos 2021",
        "Volkswagen Taos 2021",
        "Taos 2021",
        "vw taos",
        "VOLKSWAGEN TAOS",
    ])
    def test_taos_alias(self, text):
        r = lookup_vehicle(text)
        assert r is not None, f"No match for: {text!r}"
        assert r.marca == "Volkswagen"
        assert r.modelo == "Taos"
        assert r.tipo_vehiculo == "SUV_4X4_DEPORTIVO"

    def test_typo_volkswaguen(self):
        """Typo 'volkswaguen' must return medium confidence."""
        r = lookup_vehicle("Volkswaguen Taos 2021")
        assert r is not None
        assert r.modelo == "Taos"
        assert r.confidence == "medium"
        assert r.needs_confirmation is True


# ─────────────────────────────────────────────────────────────────────────────
# RC30 — Brand-only must not invent model
# ─────────────────────────────────────────────────────────────────────────────
class TestRC30BrandOnlyNoInvention:
    """Brand-only input (+ year) must return None — no model invented."""

    @pytest.mark.parametrize("text", [
        "Quiero revisar un Ford 2019 en Córdoba Capital",
        "Toyota 2020",
        "Honda 2018",
        "Volkswagen 2021",
        "Chevrolet",
        "ford",
        "peugeot",
        "toyota",
    ])
    def test_brand_only_returns_none(self, text):
        r = lookup_vehicle(text)
        assert r is None, (
            f"Expected None for brand-only {text!r}, got {r}"
        )

    def test_ford_2019_no_focus_invented(self):
        """Specifically validate the Scenario 4R case."""
        r = lookup_vehicle("Quiero revisar un Ford 2019 en Córdoba capital")
        assert r is None

    def test_ford_2019_no_marca(self):
        r = lookup_vehicle("Ford 2019")
        assert r is None


# ─────────────────────────────────────────────────────────────────────────────
# RC31 — Direct text quote path (vehicle + location in same message)
# ─────────────────────────────────────────────────────────────────────────────
class TestRC31DirectTextQuotePath:
    """Vehicle extracted from text with zone info present."""

    def test_ford_focus_palermo_recognized(self):
        r = lookup_vehicle("Tengo un Ford Focus 2019 en Palermo, cuánto sale?")
        assert r is not None
        assert r.marca == "Ford"
        assert r.modelo == "Focus"
        assert r.tipo_vehiculo == "AUTO"

    def test_no_missing_vehicle_fallback_needed(self):
        """If vehicle is known, vehicle_known=True so no fallback flow needed."""
        r = lookup_vehicle("Tengo un Ford Focus 2019 en Palermo, cuánto sale?")
        assert r is not None
        assert bool(r.tipo_vehiculo)  # non-empty tipo → vehicle_known=True

    def test_year_extraction_does_not_block(self):
        """Year '2019' in text must not consume the model alias."""
        r = lookup_vehicle("Ford Focus 2019")
        assert r is not None
        assert r.modelo == "Focus"


# ─────────────────────────────────────────────────────────────────────────────
# RC32 — Model-only pickup (Ranger)
# ─────────────────────────────────────────────────────────────────────────────
class TestRC32ModelOnlyPickup:
    """'ranger' model-only alias → Ford Ranger SUV_4X4_DEPORTIVO."""

    def test_ranger_model_only(self):
        r = lookup_vehicle("Quiero revisar una Ranger 2020 en San Francisco Solano")
        assert r is not None
        assert r.marca == "Ford"
        assert r.modelo == "Ranger"

    def test_ranger_not_auto(self):
        r = lookup_vehicle("Quiero revisar una Ranger 2020 en San Francisco Solano")
        assert r.tipo_vehiculo == "SUV_4X4_DEPORTIVO"
        assert r.tipo_vehiculo != "AUTO"

    def test_ford_ranger_full(self):
        r = lookup_vehicle("Ford Ranger 2022")
        assert r is not None
        assert r.tipo_vehiculo == "SUV_4X4_DEPORTIVO"


# ─────────────────────────────────────────────────────────────────────────────
# RC33 — New high-traffic vehicles recognized
# ─────────────────────────────────────────────────────────────────────────────
class TestRC33NewHighTrafficVehicles:
    """Previously missing vehicles are now recognized."""

    def test_chevrolet_cruze(self):
        r = lookup_vehicle("Chevrolet Cruze 2018")
        assert r is not None
        assert r.marca == "Chevrolet"
        assert r.modelo == "Cruze"
        assert r.tipo_vehiculo == "AUTO"

    def test_fiat_palio(self):
        r = lookup_vehicle("Fiat Palio 2017")
        assert r is not None
        assert r.modelo == "Palio"
        assert r.tipo_vehiculo == "AUTO"

    def test_vw_t_cross(self):
        r = lookup_vehicle("Volkswagen T-Cross 2021")
        assert r is not None
        assert r.modelo == "T-Cross"
        assert r.tipo_vehiculo == "SUV_4X4_DEPORTIVO"

    def test_vw_t_cross_hyphen_variant(self):
        r = lookup_vehicle("VW T Cross 2021")
        assert r is not None
        assert r.modelo == "T-Cross"
        assert r.tipo_vehiculo == "SUV_4X4_DEPORTIVO"

    def test_peugeot_2008(self):
        r = lookup_vehicle("Peugeot 2008 2020")
        assert r is not None
        assert r.modelo == "2008"
        assert r.tipo_vehiculo == "SUV_4X4_DEPORTIVO"

    def test_peugeot_2008_not_year_confusion(self):
        """'peugeot 2008' matches vehicle, not year-only."""
        r = lookup_vehicle("Peugeot 2008")
        assert r is not None
        assert r.modelo == "2008"

    def test_hyundai_tucson(self):
        r = lookup_vehicle("Hyundai Tucson 2019")
        assert r is not None
        assert r.marca == "Hyundai"
        assert r.modelo == "Tucson"
        assert r.tipo_vehiculo == "SUV_4X4_DEPORTIVO"

    def test_kia_sportage(self):
        r = lookup_vehicle("Kia Sportage 2018")
        assert r is not None
        assert r.marca == "Kia"
        assert r.modelo == "Sportage"
        assert r.tipo_vehiculo == "SUV_4X4_DEPORTIVO"

    def test_toyota_rav4(self):
        r = lookup_vehicle("Toyota RAV4 2020")
        assert r is not None
        assert r.modelo == "RAV4"
        assert r.tipo_vehiculo == "SUV_4X4_DEPORTIVO"

    def test_vw_golf(self):
        r = lookup_vehicle("tengo un golf 2018")
        assert r is not None
        assert r.modelo == "Golf"
        assert r.tipo_vehiculo == "AUTO"

    def test_renault_logan(self):
        r = lookup_vehicle("Renault Logan 2019")
        assert r is not None
        assert r.modelo == "Logan"
        assert r.tipo_vehiculo == "AUTO"

    def test_fiat_uno(self):
        r = lookup_vehicle("Fiat Uno 2005")
        assert r is not None
        assert r.modelo == "Uno"
        assert r.tipo_vehiculo == "AUTO"


# ─────────────────────────────────────────────────────────────────────────────
# RC34 — Negative: brand-only ambiguous inputs
# ─────────────────────────────────────────────────────────────────────────────
class TestRC34NegativeBrandOnly:
    """Brand-only text must never return a vehicle match."""

    @pytest.mark.parametrize("text,expected_none", [
        ("Toyota 2020", True),
        ("Honda 2018", True),
        ("Volkswagen 2021", True),
        ("Renault", True),
        ("Fiat", True),
        ("Chevrolet 2019", True),
        ("BMW 2022", True),
        ("Audi 2021", True),
        ("Hyundai 2020", True),
        ("Kia 2019", True),
    ])
    def test_brand_only_none(self, text, expected_none):
        r = lookup_vehicle(text)
        if expected_none:
            assert r is None, (
                f"Expected None for brand-only {text!r}, "
                f"but got marca={r.marca!r} modelo={r.modelo!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# RC35 — Recognition-only vehicles: recognized but no quote category
# ─────────────────────────────────────────────────────────────────────────────
class TestRC35RecognitionOnly:
    """Recognition-only vehicles: brand+model returned, tipo_vehiculo empty."""

    def test_toyota_hiace_recognized(self):
        r = lookup_vehicle("Toyota Hiace 2020")
        assert r is not None
        assert r.marca == "Toyota"
        assert r.modelo == "Hiace"

    def test_toyota_hiace_no_tipo(self):
        r = lookup_vehicle("Toyota Hiace 2020")
        assert r.tipo_vehiculo == ""

    def test_toyota_hiace_recognition_only_flag(self):
        r = lookup_vehicle("Toyota Hiace 2020")
        assert r.recognition_only is True

    def test_ford_transit_recognized(self):
        r = lookup_vehicle("Ford Transit 2022")
        assert r is not None
        assert r.recognition_only is True
        assert r.tipo_vehiculo == ""

    def test_vehicle_known_guard_fails_for_recognition_only(self):
        """Simulates conversation_engine vehicle_known check."""
        r = lookup_vehicle("Toyota Hiace 2020")
        # vehicle_known = pre_detected is not None and bool(pre_detected.tipo_vehiculo)
        vehicle_known = r is not None and bool(r.tipo_vehiculo)
        assert vehicle_known is False  # triggers Vehicle Flow, not quote


# ─────────────────────────────────────────────────────────────────────────────
# RC36 — Catalog integrity: existing 61 vehicles unbroken
# ─────────────────────────────────────────────────────────────────────────────
class TestRC36ExistingCatalogIntegrity:
    """Every vehicle from the original 61-vehicle catalog still matches."""

    @pytest.mark.parametrize("text,marca,modelo,tipo", [
        # AUTO
        ("suran",             "Volkswagen", "Suran",    "AUTO"),
        ("vw suran",          "Volkswagen", "Suran",    "AUTO"),
        ("peugeot 208",       "Peugeot",    "208",      "AUTO"),
        ("208",               "Peugeot",    "208",      "AUTO"),
        ("onix",              "Chevrolet",  "Onix",     "AUTO"),
        ("ford focus",        "Ford",       "Focus",    "AUTO"),
        ("focus",             "Ford",       "Focus",    "AUTO"),
        ("honda fit",         "Honda",      "Fit",      "AUTO"),
        ("fit",               "Honda",      "Fit",      "AUTO"),
        ("toyota corolla",    "Toyota",     "Corolla",  "AUTO"),
        ("corolla",           "Toyota",     "Corolla",  "AUTO"),
        ("sandero",           "Renault",    "Sandero",  "AUTO"),
        ("cronos",            "Fiat",       "Cronos",   "AUTO"),
        ("gol",               "Volkswagen", "Gol",      "AUTO"),
        ("vw gol",            "Volkswagen", "Gol",      "AUTO"),
        ("ka",                "Ford",       "Ka",       "AUTO"),
        ("ford ka",           "Ford",       "Ka",       "AUTO"),
        ("207",               "Peugeot",    "207",      "AUTO"),
        ("308",               "Peugeot",    "308",      "AUTO"),
        ("c3",                "Citroën",    "C3",       "AUTO"),
        ("citroen c3",        "Citroën",    "C3",       "AUTO"),
        # SUV
        ("ecosport",          "Ford",       "Ecosport", "SUV_4X4_DEPORTIVO"),
        ("ford ecosport",     "Ford",       "Ecosport", "SUV_4X4_DEPORTIVO"),
        ("hilux",             "Toyota",     "Hilux",    "SUV_4X4_DEPORTIVO"),
        ("ranger",            "Ford",       "Ranger",   "SUV_4X4_DEPORTIVO"),
        ("amarok",            "Volkswagen", "Amarok",   "SUV_4X4_DEPORTIVO"),
        ("tracker",           "Chevrolet",  "Tracker",  "SUV_4X4_DEPORTIVO"),
        ("duster",            "Renault",    "Duster",   "SUV_4X4_DEPORTIVO"),
        ("captiva",           "Chevrolet",  "Captiva",  "SUV_4X4_DEPORTIVO"),
        ("captiva 2017",      "Chevrolet",  "Captiva",  "SUV_4X4_DEPORTIVO"),
        ("taos",              "Volkswagen", "Taos",     "SUV_4X4_DEPORTIVO"),
        ("hrv",               "Honda",      "HR-V",     "SUV_4X4_DEPORTIVO"),
        ("hr v",              "Honda",      "HR-V",     "SUV_4X4_DEPORTIVO"),
        ("honda hrv",         "Honda",      "HR-V",     "SUV_4X4_DEPORTIVO"),
        ("kicks",             "Nissan",     "Kicks",    "SUV_4X4_DEPORTIVO"),
        ("glb",               "Mercedes-Benz","GLB",   "SUV_4X4_DEPORTIVO"),
        ("mercedes glb",      "Mercedes-Benz","GLB",   "SUV_4X4_DEPORTIVO"),
        ("renegade",          "Jeep",       "Renegade", "SUV_4X4_DEPORTIVO"),
        ("compass",           "Jeep",       "Compass",  "SUV_4X4_DEPORTIVO"),
        ("kangoo",            "Renault",    "Kangoo",   "SUV_4X4_DEPORTIVO"),
        ("oroch",             "Renault",    "Oroch",    "SUV_4X4_DEPORTIVO"),
        ("3008",              "Peugeot",    "3008",     "SUV/4x4"),
        ("peugeot 3008",      "Peugeot",    "3008",     "SUV/4x4"),
        ("5008",              "Peugeot",    "5008",     "SUV/4x4"),
    ])
    def test_existing_alias(self, text, marca, modelo, tipo):
        r = lookup_vehicle(text)
        assert r is not None, f"Expected match for {text!r}"
        assert r.marca == marca,   f"{text!r}: marca {r.marca!r} ≠ {marca!r}"
        assert r.modelo == modelo, f"{text!r}: modelo {r.modelo!r} ≠ {modelo!r}"
        assert r.tipo_vehiculo == tipo, f"{text!r}: tipo {r.tipo_vehiculo!r} ≠ {tipo!r}"

    def test_typo_siran(self):
        r = lookup_vehicle("siran")
        assert r is not None
        assert r.modelo == "Suran"
        assert r.needs_confirmation is True
        assert r.confidence == "medium"

    def test_typo_volkswaguen_taos(self):
        r = lookup_vehicle("volkswaguen taos")
        assert r is not None
        assert r.modelo == "Taos"
        assert r.needs_confirmation is True
