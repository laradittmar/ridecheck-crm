"""M21.2-DATA Phase 6+7: Pricing and full 204-locality quote tests.

Phase 6: Representative quote matrix (fixed examples from directive).
Phase 7: Exhaustive 204-locality AUTO quote + one 150k category check.
"""
from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock

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

from app.services.pricing import PricingService, PricingNotFoundError
from app.repositories.pricing_repository import PricingRepository


def _make_service(db_zones: list[tuple[str, str | None, int]]) -> tuple[PricingService, object]:
    """Build a PricingService backed by mock zone data."""
    from app.models import ViaticosZone

    repo = PricingRepository()
    db = MagicMock()

    def fake_find_zone(db, zone_group, zone_detail):
        needle_g = (zone_group or "").lower().strip()
        needle_d = (zone_detail or "").lower().strip()
        # exact group+detail
        for g, d, v in db_zones:
            if (g or "").lower().strip() == needle_g and (d or "").lower().strip() == needle_d:
                z = ViaticosZone(); z.zone_group = g; z.zone_detail = d; z.viaticos = v
                return z
        # detail only
        for g, d, v in db_zones:
            if d and (d or "").lower().strip() == needle_d:
                z = ViaticosZone(); z.zone_group = g; z.zone_detail = d; z.viaticos = v
                return z
        # group fallback (zone_detail IS NULL)
        for g, d, v in db_zones:
            if d is None and (g or "").lower().strip() == needle_g:
                z = ViaticosZone(); z.zone_group = g; z.zone_detail = None; z.viaticos = v
                return z
        return None

    repo.find_zone_by_group_and_detail = fake_find_zone
    svc = PricingService(repo)
    return svc, db


# Full authoritative zone table (204 localities + 3 legacy-review + 4 fallbacks)
_ZONES: list[tuple[str, str | None, int]] = [
    # ── CABA — 51 authoritative + alias rows ──────────────────────────────
    ("CABA","Agronomía",0),("CABA","Almagro",0),("CABA","Balvanera",0),
    ("CABA","Barracas",0),("CABA","Belgrano",0),("CABA","Boedo",0),
    ("CABA","Caballito",0),("CABA","Chacarita",0),("CABA","Coghlan",0),
    ("CABA","Colegiales",0),("CABA","Constitución",0),("CABA","Flores",0),
    ("CABA","Floresta",0),("CABA","La Boca",0),("CABA","La Paternal",0),
    ("CABA","Liniers",0),("CABA","Mataderos",0),("CABA","Monte Castro",0),
    ("CABA","Monserrat",0),("CABA","Nueva Pompeya",0),("CABA","Núñez",0),
    ("CABA","Palermo",0),("CABA","Parque Avellaneda",0),("CABA","Parque Chacabuco",0),
    ("CABA","Parque Chas",0),("CABA","Parque Patricios",0),("CABA","Puerto Madero",0),
    ("CABA","Recoleta",0),("CABA","Retiro",0),("CABA","Saavedra",0),
    ("CABA","San Cristóbal",0),("CABA","San Nicolás",0),("CABA","San Telmo",0),
    ("CABA","Versalles",0),("CABA","Villa Crespo",0),("CABA","Villa del Parque",0),
    ("CABA","Villa Devoto",0),("CABA","Villa General Mitre",0),("CABA","Villa Lugano",0),
    ("CABA","Villa Luro",0),("CABA","Villa Ortúzar",0),("CABA","Villa Pueyrredón",0),
    ("CABA","Villa Real",0),("CABA","Villa Riachuelo",0),("CABA","Villa Santa Rita",0),
    ("CABA","Villa Soldati",0),("CABA","Villa Urquiza",0),("CABA","Vélez Sarsfield",0),
    ("CABA","CABA",0),("CABA","Capital Federal",0),("CABA","Capital",0),
    # ── Norte — 40 authoritative ──────────────────────────────────────────
    ("Norte","Tigre",0),("Norte","San Fernando",0),("Norte","San Isidro",0),
    ("Norte","Vicente Lopez",0),("Norte","Villa Adelina",0),("Norte","Boulogne Sur Mer",0),
    ("Norte","Benavídez",20000),("Norte","Escobar",100000),("Norte","Pilar",90000),
    ("Norte","Campana",190000),("Norte","Zárate",250000),("Norte","Don Torcuato",0),
    ("Norte","Tortuguitas",80000),("Norte","Acassuso",0),("Norte","Beccar",0),
    ("Norte","Belén de Escobar",100000),("Norte","Boulogne",0),("Norte","Carapachay",0),
    ("Norte","Del Viso",90000),("Norte","El Talar",0),("Norte","Florida",0),
    ("Norte","Garín",100000),("Norte","General Pacheco",0),("Norte","Ingeniero Maschwitz",100000),
    ("Norte","La Lucila",0),("Norte","Manuel Alberti",90000),("Norte","Maquinista Savio",100000),
    ("Norte","Martínez",0),("Norte","Munro",0),("Norte","Nordelta",0),
    ("Norte","Olivos",0),("Norte","Pacheco",0),("Norte","Presidente Derqui",90000),
    ("Norte","Ricardo Rojas",0),("Norte","Rincón de Milberg",0),("Norte","Santa Catalina",20000),
    ("Norte","Victoria",0),("Norte","Villa Martelli",0),("Norte","Villa Rosa",90000),
    ("Norte","Virreyes",0),
    # legacy-review (preserved, not authoritative)
    ("Norte","Malvinas Argentinas",30000),
    # ── Oeste — 58 authoritative ──────────────────────────────────────────
    ("Oeste","San Martin",30000),("Oeste","3 de Febrero",40000),("Oeste","Hurlingham",40000),
    ("Oeste","Ituzaingó",50000),("Oeste","Morón",50000),("Oeste","La Matanza Oeste",50000),
    ("Oeste","Moreno",90000),("Oeste","General Rodriguez",160000),("Oeste","Marcos Paz",160000),
    ("Oeste","Merlo",100000),("Oeste","Cañuelas",170000),("Oeste","General Las Heras",190000),
    ("Oeste","Luján",200000),("Oeste","Exaltación de la Cruz",200000),("Oeste","Castelar",50000),
    ("Oeste","Padua",90000),("Oeste","San Justo",40000),("Oeste","Ciudad Jardín",40000),
    ("Oeste","Bella Vista",50000),("Oeste","Caseros",40000),("Oeste","Ciudadela",40000),
    ("Oeste","Cuartel V",90000),("Oeste","El Palomar",50000),("Oeste","Francisco Álvarez",90000),
    ("Oeste","González Catán",90000),("Oeste","Grand Bourg",90000),
    ("Oeste","Gregorio de Laferrère",90000),("Oeste","Haedo",50000),
    ("Oeste","Isidro Casanova",90000),("Oeste","José C. Paz",90000),
    ("Oeste","José León Suárez",30000),("Oeste","La Reja",90000),("Oeste","La Tablada",50000),
    ("Oeste","Libertad",100000),("Oeste","Lomas del Mirador",50000),
    ("Oeste","Los Polvorines",90000),("Oeste","Mariano Acosta",100000),
    ("Oeste","Martín Coronado",40000),("Oeste","Muñiz",50000),("Oeste","Pablo Nogués",90000),
    ("Oeste","Paso del Rey",90000),("Oeste","Pontevedra",100000),("Oeste","Rafael Castillo",90000),
    ("Oeste","Ramos Mejía",50000),("Oeste","Sáenz Peña",40000),("Oeste","San Andrés",30000),
    ("Oeste","San Antonio de Padua",90000),("Oeste","San Miguel",50000),
    ("Oeste","Santos Lugares",40000),("Oeste","Trujui",90000),("Oeste","Villa Ballester",30000),
    ("Oeste","Villa Bosch",40000),("Oeste","Villa Luzuriaga",50000),("Oeste","Villa Lynch",30000),
    ("Oeste","Villa Sarmiento",50000),("Oeste","Villa Tesei",40000),("Oeste","Villa Udaondo",50000),
    ("Oeste","William Morris",40000),
    # legacy-review (preserved)
    ("Oeste","Ciudad Evita",30000),("Oeste","Tapiales",30000),
    # ── Sur — 55 authoritative ────────────────────────────────────────────
    ("Sur","Lanús",30000),("Sur","Avellaneda",30000),("Sur","Lomas de Zamora",80000),
    ("Sur","Almirante Brown",80000),("Sur","Quilmes",50000),("Sur","La Matanza Este",90000),
    ("Sur","Ezeiza",100000),("Sur","Esteban Echeverría",90000),("Sur","Presidente Perón",110000),
    ("Sur","Florencio Varela",100000),("Sur","Berazategui",90000),("Sur","Coronel Brandsen",200000),
    ("Sur","La Plata",180000),("Sur","Berisso",190000),("Sur","Ensenada",190000),
    ("Sur","Gonnet",170000),("Sur","Bernal",80000),("Sur","Villa Dominico",30000),
    ("Sur","Adrogué",80000),("Sur","Banfield",80000),("Sur","Bosques",100000),
    ("Sur","Burzaco",80000),("Sur","Canning",100000),("Sur","City Bell",180000),
    ("Sur","Claypole",80000),("Sur","Dock Sud",30000),("Sur","Don Bosco",50000),
    ("Sur","El Jagüel",90000),("Sur","Ezpeleta",50000),("Sur","Gerli",30000),
    ("Sur","Glew",80000),("Sur","Guernica",110000),("Sur","Hudson",90000),
    ("Sur","José Mármol",80000),("Sur","Llavallol",80000),("Sur","Longchamps",80000),
    ("Sur","Los Hornos",180000),("Sur","Luis Guillón",90000),("Sur","Monte Chingolo",30000),
    ("Sur","Monte Grande",90000),("Sur","Piñeyro",30000),("Sur","Plátanos",90000),
    ("Sur","Rafael Calzada",80000),("Sur","Ranelagh",90000),("Sur","Remedios de Escalada",30000),
    ("Sur","Ringuelet",180000),("Sur","San Francisco Solano",50000),("Sur","Sarandí",30000),
    ("Sur","Temperley",80000),("Sur","Tolosa",180000),("Sur","Tristán Suárez",100000),
    ("Sur","Turdera",80000),("Sur","Valentín Alsina",30000),
    ("Sur","Villa Elisa",180000),("Sur","Wilde",30000),
    # ── fallback rows (zone_detail=None) ──────────────────────────────────
    ("CABA", None, 0),
    ("Norte", None, 0),
    ("Oeste", None, 0),
    ("Sur", None, 0),
]

# 204 authoritative rows only (no legacy-review, no fallbacks)
_AUTHORITATIVE_204: list[tuple[str, str, int]] = [
    (g, d, v) for g, d, v in _ZONES
    if d is not None
    and (g, d) not in {
        ("Norte", "Malvinas Argentinas"),
        ("Oeste", "Ciudad Evita"),
        ("Oeste", "Tapiales"),
    }
]


class TestPricingBaseUpdated(unittest.TestCase):
    """Phase 2: verify CSV prices loaded correctly."""

    def setUp(self):
        from app.repositories.pricing_repository import PricingRepository as R
        self.repo = R()  # fresh instance → fresh CSV read (lru_cache is per-instance)

    def test_auto_is_140000(self):
        row = self.repo.find_base_price("AUTO")
        self.assertIsNotNone(row)
        self.assertEqual(row.precio_base, 140000)

    def test_suv_4x4_is_150000(self):
        row = self.repo.find_base_price("SUV/4x4")
        self.assertIsNotNone(row)
        self.assertEqual(row.precio_base, 150000)

    def test_suv_4x4_deportivo_is_150000(self):
        row = self.repo.find_base_price("SUV_4X4_DEPORTIVO")
        self.assertIsNotNone(row)
        self.assertEqual(row.precio_base, 150000)

    def test_clasico_is_150000(self):
        row = self.repo.find_base_price("CLASICO")
        self.assertIsNotNone(row)
        self.assertEqual(row.precio_base, 150000)

    def test_utilitario_is_150000(self):
        row = self.repo.find_base_price("UTILITARIO")
        self.assertIsNotNone(row)
        self.assertEqual(row.precio_base, 150000)

    def test_camioneta_is_150000(self):
        row = self.repo.find_base_price("CAMIONETA")
        self.assertIsNotNone(row)
        self.assertEqual(row.precio_base, 150000)

    def test_moto_unchanged_120000(self):
        row = self.repo.find_base_price("MOTO")
        self.assertIsNotNone(row)
        self.assertEqual(row.precio_base, 120000)

    def test_escaneo_motor_unchanged_80000(self):
        row = self.repo.find_base_price("ESCANEO_MOTOR")
        self.assertIsNotNone(row)
        self.assertEqual(row.precio_base, 80000)


class TestCanonicalVehicleType(unittest.TestCase):
    """Phase 3: _canonical_vehicle_type covers new categories."""

    def _canon(self, t):
        from app.services.pricing import PricingService
        return PricingService._canonical_vehicle_type(t)

    def test_utilitario_roundtrips(self):
        self.assertEqual(self._canon("UTILITARIO"), "UTILITARIO")

    def test_utilitario_lower(self):
        self.assertEqual(self._canon("utilitario"), "UTILITARIO")

    def test_camioneta_roundtrips(self):
        self.assertEqual(self._canon("CAMIONETA"), "CAMIONETA")

    def test_camioneta_lower(self):
        self.assertEqual(self._canon("camioneta"), "CAMIONETA")

    def test_suv_4x4_still_works(self):
        self.assertEqual(self._canon("SUV/4x4"), "SUV/4x4")

    def test_auto_still_works(self):
        self.assertEqual(self._canon("AUTO"), "AUTO")


class TestSubmittedTipoMap(unittest.TestCase):
    """Phase 3: website form tipo mapping."""

    def _normalize(self, s):
        from app.services.conversation_engine import _normalize_submitted_tipo
        return _normalize_submitted_tipo(s)

    def test_utilitario_maps_to_UTILITARIO(self):
        self.assertEqual(self._normalize("Utilitario"), "UTILITARIO")

    def test_utilitario_lower(self):
        self.assertEqual(self._normalize("utilitario"), "UTILITARIO")

    def test_camioneta_maps_to_CAMIONETA(self):
        self.assertEqual(self._normalize("Camioneta"), "CAMIONETA")

    def test_camioneta_lower(self):
        self.assertEqual(self._normalize("camioneta"), "CAMIONETA")

    def test_suv_still_maps_to_suv_4x4(self):
        self.assertEqual(self._normalize("SUV"), "SUV/4x4")

    def test_deportivo_maps_to_deportivo(self):
        self.assertEqual(self._normalize("Deportivo"), "SUV_4X4_DEPORTIVO")

    def test_clasico_maps_to_clasico(self):
        self.assertEqual(self._normalize("Clásico"), "CLASICO")


class TestRepresentativeQuoteMatrix(unittest.TestCase):
    """Phase 6: fixed quote matrix from directive."""

    def setUp(self):
        self.svc, self.db = _make_service(_ZONES)

    def _quote(self, tipo, group, detail):
        return self.svc.quote(self.db, tipo, group, detail)

    def test_auto_palermo(self):
        q = self._quote("AUTO", "CABA", "Palermo")
        self.assertEqual(q.precio_base, 140000)
        self.assertEqual(q.viaticos, 0)
        self.assertEqual(q.precio_total, 140000)

    def test_auto_balvanera(self):
        q = self._quote("AUTO", "CABA", "Balvanera")
        self.assertEqual(q.precio_base, 140000)
        self.assertEqual(q.viaticos, 0)
        self.assertEqual(q.precio_total, 140000)

    def test_auto_berazategui(self):
        q = self._quote("AUTO", "Sur", "Berazategui")
        self.assertEqual(q.precio_base, 140000)
        self.assertEqual(q.viaticos, 90000)
        self.assertEqual(q.precio_total, 230000)

    def test_suv_berazategui(self):
        q = self._quote("SUV/4x4", "Sur", "Berazategui")
        self.assertEqual(q.precio_base, 150000)
        self.assertEqual(q.viaticos, 90000)
        self.assertEqual(q.precio_total, 240000)

    def test_auto_pilar(self):
        q = self._quote("AUTO", "Norte", "Pilar")
        self.assertEqual(q.precio_base, 140000)
        self.assertEqual(q.viaticos, 90000)
        self.assertEqual(q.precio_total, 230000)

    def test_suv_zarate(self):
        q = self._quote("SUV/4x4", "Norte", "Zárate")
        self.assertEqual(q.precio_base, 150000)
        self.assertEqual(q.viaticos, 250000)
        self.assertEqual(q.precio_total, 400000)

    def test_utilitario_palermo(self):
        q = self._quote("UTILITARIO", "CABA", "Palermo")
        self.assertEqual(q.precio_base, 150000)
        self.assertEqual(q.viaticos, 0)
        self.assertEqual(q.precio_total, 150000)

    def test_camioneta_palermo(self):
        q = self._quote("CAMIONETA", "CABA", "Palermo")
        self.assertEqual(q.precio_base, 150000)
        self.assertEqual(q.viaticos, 0)
        self.assertEqual(q.precio_total, 150000)

    def test_clasico_la_plata(self):
        q = self._quote("CLASICO", "Sur", "La Plata")
        self.assertEqual(q.precio_base, 150000)
        self.assertEqual(q.viaticos, 180000)
        self.assertEqual(q.precio_total, 330000)

    # One locality per distinct viático value in authoritative dataset
    def test_viatico_0_palermo(self):
        q = self._quote("AUTO", "CABA", "Palermo")
        self.assertEqual(q.viaticos, 0)

    def test_viatico_20000_benavidez(self):
        q = self._quote("AUTO", "Norte", "Benavídez")
        self.assertEqual(q.viaticos, 20000)

    def test_viatico_30000_lanus(self):
        q = self._quote("AUTO", "Sur", "Lanús")
        self.assertEqual(q.viaticos, 30000)

    def test_viatico_40000_tres_de_febrero(self):
        q = self._quote("AUTO", "Oeste", "3 de Febrero")
        self.assertEqual(q.viaticos, 40000)

    def test_viatico_50000_quilmes(self):
        q = self._quote("AUTO", "Sur", "Quilmes")
        self.assertEqual(q.viaticos, 50000)

    def test_viatico_80000_tortuguitas(self):
        q = self._quote("AUTO", "Norte", "Tortuguitas")
        self.assertEqual(q.viaticos, 80000)

    def test_viatico_90000_pilar(self):
        q = self._quote("AUTO", "Norte", "Pilar")
        self.assertEqual(q.viaticos, 90000)

    def test_viatico_100000_escobar(self):
        q = self._quote("AUTO", "Norte", "Escobar")
        self.assertEqual(q.viaticos, 100000)

    def test_viatico_110000_presidente_peron(self):
        q = self._quote("AUTO", "Sur", "Presidente Perón")
        self.assertEqual(q.viaticos, 110000)

    def test_viatico_160000_general_rodriguez(self):
        q = self._quote("AUTO", "Oeste", "General Rodriguez")
        self.assertEqual(q.viaticos, 160000)

    def test_viatico_170000_canuela(self):
        q = self._quote("AUTO", "Oeste", "Cañuelas")
        self.assertEqual(q.viaticos, 170000)

    def test_viatico_180000_la_plata(self):
        q = self._quote("AUTO", "Sur", "La Plata")
        self.assertEqual(q.viaticos, 180000)

    def test_viatico_190000_campana(self):
        q = self._quote("AUTO", "Norte", "Campana")
        self.assertEqual(q.viaticos, 190000)

    def test_viatico_200000_lujan(self):
        q = self._quote("AUTO", "Oeste", "Luján")
        self.assertEqual(q.viaticos, 200000)

    def test_viatico_250000_zarate(self):
        q = self._quote("AUTO", "Norte", "Zárate")
        self.assertEqual(q.viaticos, 250000)


class TestFull204LocalityAutoQuote(unittest.TestCase):
    """Phase 7: all 204 authoritative localities quote without error at correct viático."""

    def setUp(self):
        self.svc, self.db = _make_service(_ZONES)

    def test_all_204_auto_quotes_pass(self):
        failures = []
        for g, d, expected_viatico in _AUTHORITATIVE_204:
            try:
                q = self.svc.quote(self.db, "AUTO", g, d)
                if q.precio_base != 140000:
                    failures.append(f"{g}/{d}: wrong base {q.precio_base}")
                if q.viaticos != expected_viatico:
                    failures.append(f"{g}/{d}: wrong viatico {q.viaticos} (expected {expected_viatico})")
            except PricingNotFoundError as e:
                failures.append(f"{g}/{d}: PricingNotFoundError({e})")
            except Exception as e:
                failures.append(f"{g}/{d}: unexpected {type(e).__name__}({e})")
        self.assertEqual(len(failures), 0,
            f"{len(failures)} locality failures:\n" + "\n".join(failures[:20]))

    def test_all_204_utilitario_quotes_pass(self):
        """Phase 7: repeat with a 150k category to prove base independence."""
        failures = []
        for g, d, expected_viatico in _AUTHORITATIVE_204:
            try:
                q = self.svc.quote(self.db, "UTILITARIO", g, d)
                if q.precio_base != 150000:
                    failures.append(f"{g}/{d}: wrong base {q.precio_base}")
                if q.viaticos != expected_viatico:
                    failures.append(f"{g}/{d}: wrong viatico {q.viaticos}")
            except PricingNotFoundError as e:
                failures.append(f"{g}/{d}: PricingNotFoundError({e})")
        self.assertEqual(len(failures), 0,
            f"{len(failures)} failures:\n" + "\n".join(failures[:20]))

    def test_authoritative_row_count_is_204(self):
        self.assertEqual(len(_AUTHORITATIVE_204), 204)


if __name__ == "__main__":
    unittest.main()
