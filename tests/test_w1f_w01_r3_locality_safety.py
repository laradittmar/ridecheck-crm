"""W1F-W-01 R3 — locality resolution safety.

The W1F-W-01 architectural review (2026-10-26) measured three wrong-location defects on the
deployed resolver, all independent of the conversational defect that caused the Wild to fail:

  W1FRV-02  "en san nicolas"           -> ('CABA','San Nicolás'), silently, 0 viáticos
                                          — the name is also San Nicolás de los Arroyos,
                                            ~240 km away and outside coverage.
  W1FRV-03  "en mar del plata"         -> ('Sur','La Plata'), 180 000 viáticos, and the
                                          out-of-coverage response was produced only AFTER
                                          the zone had been written.
  (sibling) "en san miguel del monte"  -> ('Oeste','San Miguel'), 50 000 viáticos.
  W1FRV-05  "los autos están en Flores" matched no vehicle-location clause at all.

Everything here runs against the REAL resolver and the REAL viáticos catalogue, loaded from
`tests/fixtures/viaticos_zones_20261026.tsv` (211 rows, exported from crm_test on
2026-10-26; configuration data, no customer data). No network, no live database, no model
call: `SEMANTIC` never runs in these tests and `_turn_semantic_calls` stays 0.

Groups
------
R3-A  the shared description cannot diverge (structural, no flag)
R3-B  the resolver, flag OFF — the inherited answers, byte for byte
R3-C  the resolver, flag ON  — unsupported / ambiguous / composite names refused
R3-D  the write path — nothing is persisted for a refused locality, and coverage is
      decided BEFORE the write
R3-E  the Flow form — a form answer is evidence, not an authority
R3-F  pricing and booking isolation — a refused locality cannot reach PricingService
R3-G  plural vehicle-location clauses
R3-H  supported localities are unchanged, across the whole catalogue
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

for _mod in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg.JSONB = sqlalchemy.JSON          # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON     # type: ignore[attr-defined]

from sqlalchemy import create_engine                              # noqa: E402
from sqlalchemy.orm import sessionmaker                           # noqa: E402

from app.models import ViaticosZone                               # noqa: E402
from app.services import field_evidence as FE                     # noqa: E402
from app.services import locality_text as LOC                     # noqa: E402
from app.services import conversation_engine as CEM               # noqa: E402
from app.services.conversation_engine import ConversationEngine   # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "viaticos_zones_20261026.tsv"
V = LOC.LocalityVerdict


# ── the real catalogue, in memory ────────────────────────────────────────────
def _catalogue_session():
    engine = create_engine("sqlite://")
    ViaticosZone.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    rows = []
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        zid, group, detail, viaticos = line.split("\t")
        rows.append(ViaticosZone(id=int(zid), zone_group=group,
                                 zone_detail=detail or None, viaticos=int(viaticos or 0)))
    db.add_all(rows)
    db.commit()
    return db, rows


_DB, _ROWS = _catalogue_session()
CATALOGUE = [(r.zone_group, r.zone_detail) for r in _ROWS]


def _engine(*, safety: bool, db=None) -> ConversationEngine:
    """A real engine with the real resolver and no external call of any kind."""
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = db if db is not None else _DB
    eng.settings = SimpleNamespace(locality_resolution_safety_enabled=safety)
    eng._pricing = MagicMock()
    eng._send_text_to_wa = MagicMock(return_value="mock-wa-id")
    eng._send_fallback_human_review_notification = MagicMock()
    eng._outbound_armed = MagicMock(return_value=True)
    eng._turn_semantic_calls = 0
    return eng


def _z(zone):
    return None if zone is None else (zone.zone_group, zone.zone_detail)


# ── minimal conversation fixtures (no DB rows: CE writes attributes) ─────────
def _state(**kw):
    ns = SimpleNamespace(
        last_stage="QUALIFYING", needs_human=False, last_intent=None,
        home_zone_group=None, home_zone_detail=None,
        current_focus_candidate_id=None, location_fallback_flow_sent=True,
        flow_booking_token=None, pending_location_proposal=None,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _cand(**kw):
    ns = SimpleNamespace(id=10, thread_id=42, status="current_focus",
                         tipo_vehiculo="AUTO", marca="Toyota", modelo="Corolla",
                         anio=2024, zone_group=None, zone_detail=None,
                         direccion_texto=None, source_text=None)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _ctx(state=None, cands=None):
    c = SimpleNamespace()
    c.thread = SimpleNamespace(id=42)
    c.contact = SimpleNamespace(wa_id="5491100000000")
    c.lead = SimpleNamespace(id=1, flag=None, estado="CONSULTA_NUEVA",
                             necesita_humano=False, nombre="T", apellido=None)
    c.state = state if state is not None else _state()
    c.candidates = cands or []
    return c


# ── R3-A: the shared description cannot diverge ──────────────────────────────
class TestR3A_NoDuplication(unittest.TestCase):
    """W1FRV-07: the two consumers must hold the SAME objects, not equal copies."""

    def test_clause_patterns_are_the_same_object(self):
        self.assertIs(CEM._VEHICLE_LOCATION_CLAUSE_PATTERNS,
                      FE._VEHICLE_LOCATION_CLAUSE_PATTERNS)
        self.assertIs(CEM._VEHICLE_LOCATION_CLAUSE_PATTERNS,
                      LOC.VEHICLE_LOCATION_CLAUSE_PATTERNS)

    def test_uncertainty_and_origin_are_the_same_object(self):
        self.assertIs(CEM._LOCATION_UNCERTAINTY_RE, FE._LOCATION_UNCERTAINTY_RE)
        self.assertIs(CEM._LOCATION_UNCERTAINTY_RE, LOC.LOCATION_UNCERTAINTY_RE)
        self.assertIs(CEM._CUSTOMER_ORIGIN_RE, LOC.CUSTOMER_ORIGIN_RE)
        self.assertIs(FE._CUSTOMER_ORIGIN_CAPTURE_RE, LOC.CUSTOMER_ORIGIN_CAPTURE_RE)

    def test_coverage_list_and_normalisers_are_the_same_object(self):
        self.assertIs(CEM._OUTSIDE_COVERAGE_CITIES, LOC.OUTSIDE_COVERAGE_CITIES)
        self.assertIs(CEM._is_outside_coverage, LOC.is_outside_coverage)
        self.assertIs(CEM._strip_accents, LOC.strip_accents)
        self.assertIs(CEM._locality_in_context, LOC.locality_in_context)
        self.assertIs(CEM._ARTICLE_PREFIX_RE, LOC.ARTICLE_PREFIX_RE)

    def test_neither_consumer_defines_its_own_clause_patterns(self):
        """A regex literal for a vehicle-location clause may exist in ONE place only."""
        for path in (BACKEND_DIR / "app" / "services" / "conversation_engine.py",
                     BACKEND_DIR / "app" / "services" / "field_evidence.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("est[aá](?:\\s+ubicad[ao])?\\s+en", source,
                             f"{path.name} re-defines a vehicle-location clause pattern")

    def test_alt_location_pattern_is_always_last(self):
        """field_evidence reads [:-1] and [-1]; plural clauses must not break that."""
        for plural in (False, True):
            patterns = LOC.clause_patterns(plural=plural)
            self.assertIs(patterns[-1], LOC._ALT_LOCATION_PATTERN)

    def test_plural_patterns_are_absent_with_safety_off(self):
        self.assertEqual(len(LOC.clause_patterns(plural=False)), 7)
        self.assertGreater(len(LOC.clause_patterns(plural=True)), 7)


# ── R3-B: flag OFF is the inherited behaviour ────────────────────────────────
class TestR3B_FlagOffIsInherited(unittest.TestCase):
    """The measured deployed answers, including the three wrong ones. OFF changes nothing."""

    OFF_CASES = {
        "en flores": ("CABA", "Flores"),
        "en floresta": ("CABA", "Floresta"),
        "en la paternal": ("CABA", "La Paternal"),
        "en paternal": ("CABA", "La Paternal"),
        "me mordi la boca": None,
        "en cordoba": None,
        "en rosario": None,
        "en neuquen": None,
        # the three defects, still present with the flag off — this is what makes the
        # rollback real rather than asserted
        "en mar del plata": ("Sur", "La Plata"),
        "en san miguel del monte": ("Oeste", "San Miguel"),
        "en san nicolas": ("CABA", "San Nicolás"),
    }

    def test_resolver_answers_are_unchanged(self):
        eng = _engine(safety=False)
        for text, expected in self.OFF_CASES.items():
            with self.subTest(text=text):
                self.assertEqual(_z(eng._extract_zone_from_text(text)), expected)

    def test_plural_clause_still_matches_nothing(self):
        eng = _engine(safety=False)
        self.assertEqual(eng._extract_vehicle_location_zones("los autos estan en flores"), [])

    def test_field_evidence_default_is_unchanged(self):
        locs, contra = FE._extract_vehicle_location_from_turn(
            "el auto esta en mar del plata")
        self.assertEqual(locs, ["mar del plata"])
        self.assertFalse(contra)


# ── R3-C: flag ON — the three validators ─────────────────────────────────────
class TestR3C_ResolverSafety(unittest.TestCase):

    def setUp(self):
        self.eng = _engine(safety=True)

    def _verdict(self, text):
        return self.eng._locality_verdict(text)[0]

    # Mar del Plata — required by the owner's scope item 3
    def test_mar_del_plata_never_resolves_to_la_plata(self):
        for text in ("mar del plata", "en mar del plata", "EN MAR DEL PLATA",
                     "el auto esta en mar del plata", "el auto está en Mar del Plata"):
            with self.subTest(text=text):
                self.assertIsNone(self.eng._extract_zone_from_text(text))
                self.assertEqual(self._verdict(text), V.UNSUPPORTED)

    def test_explicit_clause_for_mar_del_plata_yields_no_zone(self):
        self.assertEqual(
            self.eng._extract_vehicle_location_zones("el auto esta en mar del plata"), [])

    # the generic rule behind it, proven on a locality the coverage list does NOT name
    def test_composite_name_is_refused_without_any_city_list(self):
        self.assertFalse(LOC.is_outside_coverage("san miguel del monte"))
        self.assertIsNone(self.eng._extract_zone_from_text("en san miguel del monte"))
        self.assertEqual(self._verdict("en san miguel del monte"), V.UNRESOLVED)
        self.assertEqual(
            self.eng._extract_vehicle_location_zones("el auto esta en san miguel del monte"),
            [])

    def test_catalogue_connector_names_still_resolve(self):
        """"Lomas de Zamora" is a connector name too — the rule must not refuse it."""
        for text, expected in (
            ("en lomas de zamora", ("Sur", "Lomas de Zamora")),
            ("en villa del parque", ("CABA", "Villa del Parque")),
            ("en paso del rey", ("Oeste", "Paso del Rey")),
            ("en belen de escobar", ("Norte", "Belén de Escobar")),
            ("en 3 de febrero", ("Oeste", "3 de Febrero")),
            ("en lomas del mirador", ("Oeste", "Lomas del Mirador")),
            ("en remedios de escalada", ("Sur", "Remedios de Escalada")),
            ("en exaltacion de la cruz", ("Oeste", "Exaltación de la Cruz")),
        ):
            with self.subTest(text=text):
                self.assertEqual(_z(self.eng._extract_zone_from_text(text)), expected)

    def test_customer_origin_phrasing_still_resolves(self):
        """"soy de Tigre" must stay Tigre: the connector rule reads function words."""
        for text in ("soy de Tigre", "vivo en Tigre", "vengo de la plata"):
            with self.subTest(text=text):
                self.assertIsNotNone(self.eng._extract_zone_from_text(text))

    def test_la_plata_itself_is_untouched(self):
        for text in ("en la plata", "la plata", "en plata", "el auto esta en la plata"):
            with self.subTest(text=text):
                self.assertEqual(_z(self.eng._extract_zone_from_text(text)),
                                 ("Sur", "La Plata"))

    # San Nicolás — required by the owner's scope item 3
    def test_san_nicolas_is_ambiguous_not_caba(self):
        for text in ("en san nicolas", "en San Nicolás", "el auto esta en san nicolas"):
            with self.subTest(text=text):
                self.assertIsNone(self.eng._extract_zone_from_text(text))
                self.assertEqual(self._verdict(text), V.AMBIGUOUS)

    def test_san_nicolas_resolves_once_the_customer_names_the_area(self):
        for text in ("en san nicolas, caba", "el auto esta en san nicolas caba"):
            with self.subTest(text=text):
                self.assertEqual(_z(self.eng._extract_zone_from_text(text)),
                                 ("CABA", "San Nicolás"))

    def test_capital_federal_qualifier_resolves_inside_caba(self):
        """"capital federal" hits the inherited CABA-synonym fast-path, which runs before
        the locality loop, so the answer is the CABA sentinel rather than the barrio. That
        is pre-existing behaviour and it is SAFE here: the group is CABA either way, and
        every CABA row carries the same viáticos (0), so the quote is identical. R3 asserts
        only that the ambiguity is settled and the coverage is right."""
        zone = self.eng._extract_zone_from_text("san nicolas capital federal")
        self.assertIsNotNone(zone)
        self.assertEqual(zone.zone_group, "CABA")

    def test_the_province_homonym_is_refused_outright(self):
        self.assertIsNone(self.eng._extract_zone_from_text(
            "en san nicolas de los arroyos"))

    def test_ambiguity_set_names_a_reason_for_every_entry(self):
        self.assertTrue(LOC.CROSS_COVERAGE_AMBIGUOUS)
        for name, reason in LOC.CROSS_COVERAGE_AMBIGUOUS.items():
            with self.subTest(name=name):
                self.assertEqual(name, LOC.norm_locality_text(name))
                self.assertGreater(len(reason), 20, "an entry must justify itself")

    def test_no_catalogue_name_is_also_a_declared_out_of_coverage_city(self):
        """A name in both lists would be ambiguous by construction and must be declared."""
        overlap = {LOC.norm_locality_text(d) for _, d in CATALOGUE if d} & \
                  LOC.OUTSIDE_COVERAGE_CITIES
        self.assertEqual(overlap - set(LOC.CROSS_COVERAGE_AMBIGUOUS), set())

    # unsupported localities — required by the owner's scope item 3
    def test_unsupported_cities_resolve_to_nothing(self):
        for text in ("en cordoba", "en rosario", "en neuquen", "en bahia blanca",
                     "en mendoza", "en salta"):
            with self.subTest(text=text):
                self.assertIsNone(self.eng._extract_zone_from_text(text))
                self.assertEqual(self._verdict(text), V.UNSUPPORTED)

    def test_unknown_places_are_unresolved_not_unsupported(self):
        for text in ("en montevideo", "en chile", "en el centro", "no se"):
            with self.subTest(text=text):
                self.assertIsNone(self.eng._extract_zone_from_text(text))
                self.assertEqual(self._verdict(text), V.UNRESOLVED)

    # the negative control from the Wild programme
    def test_la_boca_negative_control_language(self):
        self.assertIsNone(self.eng._extract_zone_from_text("me mordi la boca"))
        self.assertEqual(_z(self.eng._extract_zone_from_text("en la boca")),
                         ("CABA", "La Boca"))
        self.assertEqual(_z(self.eng._extract_zone_from_text("en boca")),
                         ("CABA", "La Boca"))

    def test_flores_versus_floresta(self):
        self.assertEqual(_z(self.eng._extract_zone_from_text("en flores")),
                         ("CABA", "Flores"))
        self.assertEqual(_z(self.eng._extract_zone_from_text("en floresta")),
                         ("CABA", "Floresta"))

    def test_la_paternal_article_handling_is_preserved(self):
        self.assertEqual(_z(self.eng._extract_zone_from_text("en paternal")),
                         ("CABA", "La Paternal"))
        self.assertEqual(_z(self.eng._extract_zone_from_text("en la paternal")),
                         ("CABA", "La Paternal"))

    def test_no_model_call_is_made(self):
        for text in ("en mar del plata", "en san nicolas", "en flores"):
            self.eng._extract_zone_from_text(text)
        self.assertEqual(self.eng._turn_semantic_calls, 0)


# ── R3-D: the write path ─────────────────────────────────────────────────────
class TestR3D_NothingIsPersisted(unittest.TestCase):
    """Scope item 2: coverage and ambiguity are evaluated BEFORE any zone write."""

    def _apply(self, text, *, safety=True, candidate=None, state=None):
        eng = _engine(safety=safety)
        st = state if state is not None else _state()
        cands = [candidate] if candidate is not None else []
        ctx = _ctx(state=st, cands=cands)
        eng._focus_candidate = MagicMock(return_value=candidate)
        early, wrote = eng._apply_zone_from_text(ctx, st, text)
        return eng, st, candidate, early, wrote

    def test_unsupported_locality_writes_no_thread_zone(self):
        _, st, _, early, wrote = self._apply("el auto esta en mar del plata")
        self.assertIsNone(st.home_zone_group)
        self.assertIsNone(st.home_zone_detail)
        self.assertFalse(wrote)
        self.assertIsNotNone(early, "the coverage response must own the turn")

    def test_unsupported_locality_writes_no_candidate_zone(self):
        cand = _cand()
        _, st, cand, _, _ = self._apply("el auto esta en mar del plata", candidate=cand)
        self.assertIsNone(cand.zone_group)
        self.assertIsNone(cand.zone_detail)
        self.assertIsNone(st.home_zone_detail)

    def test_coverage_outcome_is_the_existing_one(self):
        eng, st, _, early, _ = self._apply("el auto esta en mar del plata")
        self.assertTrue(st.needs_human, "the existing coverage outcome sets needs_human")
        eng._send_text_to_wa.assert_called_once()
        self.assertIn("CABA y GBA", eng._send_text_to_wa.call_args[0][1])

    def test_ambiguous_locality_writes_nothing_and_lets_the_turn_continue(self):
        eng, st, _, early, wrote = self._apply("el auto esta en san nicolas")
        self.assertIsNone(st.home_zone_group)
        self.assertIsNone(st.home_zone_detail)
        self.assertFalse(wrote)
        self.assertIsNone(early, "ambiguity asks through the existing clarification route")
        eng._send_text_to_wa.assert_not_called()

    def test_replay_path_never_sends_a_message(self):
        """allow_contradiction_return=False is the stored-turn replay: silent refusal."""
        eng = _engine(safety=True)
        st = _state()
        ctx = _ctx(state=st)
        eng._focus_candidate = MagicMock(return_value=None)
        early, wrote = eng._apply_zone_from_text(
            ctx, st, "el auto esta en mar del plata", allow_contradiction_return=False)
        self.assertIsNone(early)
        self.assertFalse(wrote)
        self.assertIsNone(st.home_zone_detail)
        eng._send_text_to_wa.assert_not_called()

    def test_supported_locality_still_writes(self):
        _, st, _, early, wrote = self._apply("el auto esta en flores")
        self.assertEqual((st.home_zone_group, st.home_zone_detail), ("CABA", "Flores"))
        self.assertTrue(wrote)
        self.assertIsNone(early)

    def test_flag_off_write_path_is_the_inherited_one(self):
        _, st, _, early, wrote = self._apply("el auto esta en mar del plata", safety=False)
        self.assertEqual((st.home_zone_group, st.home_zone_detail), ("Sur", "La Plata"))
        self.assertTrue(wrote)
        self.assertIsNone(early)


# ── R3-E: the Flow form ──────────────────────────────────────────────────────
class TestR3E_FormAnswer(unittest.TestCase):

    def _submit(self, localidad, *, safety=True, zona_general="CABA"):
        eng = _engine(safety=safety)
        st = _state()
        ctx = _ctx(state=st, cands=[_cand()])
        eng._focus_candidate = MagicMock(return_value=ctx.candidates[0])
        eng._complete_clarification_attempt = MagicMock(return_value=(object(), False))
        eng._compute_price_quote = MagicMock(return_value=None)
        eng._withdraw_booking_offer = MagicMock()
        eng.db = MagicMock()
        eng.db.execute = _DB.execute          # the real catalogue, read-only
        out = eng._process_location_fallback_response(
            ctx, st, {"zona_general": zona_general, "localidad": localidad})
        return eng, st, ctx, out

    def test_unsupported_form_answer_persists_no_zone(self):
        eng, st, ctx, out = self._submit("Mar del Plata")
        self.assertIsNone(st.home_zone_group)
        self.assertIsNone(st.home_zone_detail)
        self.assertTrue(st.needs_human)
        self.assertIn("CABA y GBA", eng._send_text_to_wa.call_args[0][1])

    def test_ambiguous_form_answer_persists_no_zone(self):
        """CORRECTED by OWNER-REVIEW-R1 (R1RV-01).

        This test used to assert that an ambiguous `localidad` persisted nothing WHATEVER
        the customer selected in `zona_general`, and it passed. The owner review showed
        what that cost: the form's area dropdown is part of the customer's answer, so a
        customer who answered the very question we had just asked — area CABA, locality
        San Nicolás — was refused and handed to a person. The ambiguity rule is unchanged;
        what changed is that an EXPLICIT area selection may settle it, and only when it
        agrees with the locality's own area."""
        # A selection that disagrees with the locality's area settles nothing.
        eng, st, ctx, out = self._submit("San Nicolás", zona_general="SUR")
        self.assertIsNone(st.home_zone_group)
        self.assertIsNone(st.home_zone_detail)
        self.assertTrue(st.needs_human)
        self.assertTrue(ctx.lead.necesita_humano)

    def test_an_agreeing_area_selection_settles_the_ambiguity(self):
        """R1RV-01: the dropdown is evidence, and an answer that settles it is served."""
        eng, st, ctx, out = self._submit("San Nicolás", zona_general="CABA")
        self.assertEqual((st.home_zone_group, st.home_zone_detail),
                         ("CABA", "San Nicolás"))
        # Whether the thread stays automated is asserted on the COMPLETE turn, in
        # `test_w1f_w01_r3_r1_fullturn_ambiguity.py::test_j09_*`: this harness stubs
        # `_compute_price_quote` to None and a quote-less form completion legitimately
        # escalates, so a `needs_human` assertion here would measure the stub.

    def test_the_area_selection_cannot_rescue_an_unsupported_locality(self):
        """Coverage is still decided before ambiguity, so no selection admits a city that
        is declared outside the coverage area."""
        for area in ("CABA", "SUR", "NORTE", "OESTE"):
            with self.subTest(area=area):
                eng, st, ctx, out = self._submit("Mar del Plata", zona_general=area)
                self.assertIsNone(st.home_zone_group)
                self.assertIsNone(st.home_zone_detail)
                self.assertTrue(st.needs_human)

    def test_supported_form_answer_still_resolves(self):
        eng, st, ctx, out = self._submit("Flores")
        self.assertEqual((st.home_zone_group, st.home_zone_detail), ("CABA", "Flores"))

    def test_flag_off_form_behaviour_is_inherited(self):
        eng, st, ctx, out = self._submit("Mar del Plata", safety=False)
        self.assertEqual((st.home_zone_group, st.home_zone_detail), ("Sur", "La Plata"))


# ── R3-F: pricing and booking isolation ──────────────────────────────────────
class TestR3F_PricingAndBookingIsolation(unittest.TestCase):
    """A refused locality must never reach PricingService — and so never a booking."""

    def _after_refusal(self, text):
        eng = _engine(safety=True)
        st = _state()
        cand = _cand()
        ctx = _ctx(state=st, cands=[cand])
        eng._focus_candidate = MagicMock(return_value=cand)
        eng._apply_zone_from_text(ctx, st, text)
        return eng, ctx, st, cand

    def test_no_quote_is_possible_after_a_refusal(self):
        for text in ("el auto esta en mar del plata",
                     "el auto esta en san nicolas",
                     "el auto esta en san miguel del monte"):
            with self.subTest(text=text):
                eng, ctx, st, cand = self._after_refusal(text)
                quote = eng._compute_price_quote(ctx, st)
                self.assertIsNone(quote, "no zone, so no quote")
                eng._pricing.quote.assert_not_called()

    def test_pricing_is_never_called_with_a_misresolved_zone(self):
        eng, ctx, st, cand = self._after_refusal("el auto esta en mar del plata")
        eng._compute_price_quote(ctx, st)
        for call in eng._pricing.quote.call_args_list:
            self.assertNotIn("La Plata", str(call))

    def test_the_booking_precondition_cannot_be_met_after_a_refusal(self):
        """BookingAuthority is reachable only through an ISSUED offer, and an offer is only
        issued after a quote has been delivered in this cycle (BOOKING_UX_CONTRACT §2-§3).
        This test asserts the link R3 owns: after a refusal there is no zone anywhere, so
        no quote exists and the stage never becomes QUOTED. It does not re-test the offer
        authority, which R3 does not touch."""
        eng, ctx, st, cand = self._after_refusal("el auto esta en mar del plata")
        self.assertIsNone(eng._compute_price_quote(ctx, st))
        self.assertIsNone(st.home_zone_group)
        self.assertIsNone(cand.zone_group)
        self.assertNotEqual(st.last_stage, "QUOTED")
        self.assertIsNone(st.flow_booking_token)

    def test_a_supported_locality_does_reach_pricing(self):
        eng = _engine(safety=True)
        st, cand = _state(), _cand()
        ctx = _ctx(state=st, cands=[cand])
        eng._focus_candidate = MagicMock(return_value=cand)
        eng._apply_zone_from_text(ctx, st, "el auto esta en flores")
        eng._pricing.quote.return_value = SimpleNamespace(precio_total=140000,
                                                          zone_group="CABA")
        eng._compute_price_quote(ctx, st)
        eng._pricing.quote.assert_called_once()
        kwargs = eng._pricing.quote.call_args.kwargs
        self.assertEqual(kwargs["zone_group"], "CABA")
        self.assertEqual(kwargs["zone_detail"], "Flores")


# ── R3-G: plural vehicle-location clauses ────────────────────────────────────
class TestR3G_PluralClauses(unittest.TestCase):

    PLURAL = (
        "los autos estan en flores",
        "los autos están en Flores",
        "los vehiculos estan en Flores",
        "las camionetas estan en Flores",
        "mis autos están en Flores",
        "los autos están ubicados en Flores",
        "los tienen en Flores",
        "hay que verlos en Flores",
    )

    def test_plural_clauses_resolve_with_safety_on(self):
        eng = _engine(safety=True)
        for text in self.PLURAL:
            with self.subTest(text=text):
                self.assertEqual([_z(z) for z in eng._extract_vehicle_location_zones(text)],
                                 [("CABA", "Flores")])

    def test_singular_clauses_are_unchanged(self):
        eng = _engine(safety=True)
        for text in ("el auto esta en flores", "el auto está ubicado en Flores",
                     "esta para revisar en Flores", "lo tienen en Flores",
                     "hay que verlo en Flores", "la revision seria en Flores"):
            with self.subTest(text=text):
                self.assertEqual([_z(z) for z in eng._extract_vehicle_location_zones(text)],
                                 [("CABA", "Flores")])

    def test_plural_clause_writes_the_candidate_zone(self):
        eng = _engine(safety=True)
        st, cand = _state(current_focus_candidate_id=10), _cand()
        ctx = _ctx(state=st, cands=[cand])
        eng._focus_candidate = MagicMock(return_value=cand)
        early, wrote = eng._apply_zone_from_text(ctx, st, "los autos estan en flores")
        self.assertTrue(wrote)
        self.assertEqual((cand.zone_group, cand.zone_detail), ("CABA", "Flores"))

    def test_plural_clause_for_an_unsupported_place_still_writes_nothing(self):
        eng = _engine(safety=True)
        st = _state()
        ctx = _ctx(state=st)
        eng._focus_candidate = MagicMock(return_value=None)
        eng._apply_zone_from_text(ctx, st, "los autos estan en mar del plata")
        self.assertIsNone(st.home_zone_detail)

    def test_field_evidence_reads_plural_clauses_too(self):
        locs, _ = FE._extract_vehicle_location_from_turn(
            "los autos están en Flores", locality_safety=True)
        self.assertEqual(locs, ["Flores"])

    def test_field_evidence_refuses_what_the_text_disqualifies(self):
        for turn in ("el auto esta en mar del plata", "el auto esta en san nicolas"):
            with self.subTest(turn=turn):
                locs, _ = FE._extract_vehicle_location_from_turn(
                    turn, locality_safety=True)
                self.assertEqual(locs, [])


# ── R3-H: supported localities are unchanged, across the whole catalogue ─────
class TestR3H_CatalogueRegression(unittest.TestCase):
    """211 real localities. Every one must resolve to itself with the flag ON, except the
    declared cross-coverage-ambiguous names, which must resolve to nothing."""

    def test_every_catalogue_locality_resolves_to_itself(self):
        on, off = _engine(safety=True), _engine(safety=False)
        changed, ambiguous = [], []
        for group, detail in CATALOGUE:
            name = detail or group
            before, after = off._extract_zone_from_text(f"en {name}"), \
                on._extract_zone_from_text(f"en {name}")
            if detail and LOC.is_cross_coverage_ambiguous(detail):
                ambiguous.append(name)
                self.assertIsNone(after, f"{name} is declared ambiguous")
                continue
            if _z(before) != _z(after):
                changed.append((name, _z(before), _z(after)))
        self.assertEqual(changed, [], "safety must not change a supported locality")
        self.assertEqual(sorted(ambiguous), ["San Nicolás"])

    def test_bare_and_clause_phrasings_are_also_unchanged(self):
        on, off = _engine(safety=True), _engine(safety=False)
        for group, detail in CATALOGUE:
            name = detail or group
            if detail and LOC.is_cross_coverage_ambiguous(detail):
                continue
            for phrasing in (name, f"el auto esta en {name}"):
                with self.subTest(phrasing=phrasing):
                    self.assertEqual(_z(off._extract_zone_from_text(phrasing)),
                                     _z(on._extract_zone_from_text(phrasing)))


# ── R3-I: the whole turn, through _process_text ──────────────────────────────
class TestR3I_WholeTurn(unittest.TestCase):
    """The router, not just the helper: a turn naming an unsupported locality must leave
    the thread with no zone, and the one that names a supported locality must not change."""

    def _turn(self, text, *, safety=True):
        from app.schemas.conversation import ConversationHandleIn
        eng = _engine(safety=safety)
        st = _state(location_fallback_flow_sent=False)
        cand = _cand()
        ctx = _ctx(state=st, cands=[cand])
        eng._focus_candidate = MagicMock(return_value=cand)
        eng._normalize_zone_from_db = MagicMock()
        eng._compute_price_quote = MagicMock(return_value=None)
        eng._routing_gate = MagicMock(return_value=(None, True))
        eng._check_fallback_flow_triggers = MagicMock(return_value=None)
        eng._handle_qualifying_intent = MagicMock(return_value=None)
        eng._handle_vehicle_inspectability_gate = MagicMock(return_value=None)
        eng._handle_explicit_service_gate = MagicMock(return_value=None)
        eng._detect_general_information = MagicMock(return_value=False)
        eng._ask_vehicle_clarification = MagicMock(return_value=None)
        from app.services.clarification_router import TurnClarificationDecision
        eng._turn_clarification_decision = MagicMock(
            return_value=TurnClarificationDecision(
                route="NO_CLARIFICATION_REQUIRED", reason="RESOLVED"))
        eng._execute_clarification = MagicMock(return_value=None)
        eng._reply_decision = MagicMock(return_value=({"reply": "ok"}, None))
        eng._build_ai_messages = MagicMock(return_value=[])
        event = ConversationHandleIn(
            thread_id=42, wa_message_id="r3-turn", wa_id="5491100000000", text=text,
            unanswered_recent_user_messages=[], recent_user_messages=[text])
        # No try/except: the turn must complete. A scaffolding gap here would otherwise
        # hide the stage under test behind an exception.
        out = eng._process_text(ctx, event)
        return eng, st, cand, out

    def test_unsupported_turn_leaves_no_zone_on_the_thread(self):
        eng, st, cand, out = self._turn("el auto esta en mar del plata")
        self.assertIsNone(st.home_zone_group)
        self.assertIsNone(st.home_zone_detail)
        self.assertIsNone(cand.zone_group)
        self.assertIsNone(cand.zone_detail)
        self.assertTrue(st.needs_human)

    def test_supported_turn_still_writes_the_zone(self):
        eng, st, cand, out = self._turn("el auto esta en flores")
        self.assertEqual((cand.zone_group, cand.zone_detail), ("CABA", "Flores"))

    def test_flag_off_turn_keeps_the_inherited_wrong_answer(self):
        """Proof that the rollback is real: with the flag off the defect is still there."""
        eng, st, cand, out = self._turn("el auto esta en mar del plata", safety=False)
        self.assertEqual((cand.zone_group, cand.zone_detail), ("Sur", "La Plata"))


if __name__ == "__main__":
    unittest.main()
