"""L4.6-EVIDENCE-CAPTURE — evidence-driven vehicle + location capture.

Closes the L4-WILD-B findings:
  VEH-A  deterministic vehicle evidence was gated behind an intent-phrase whitelist
  VEH-B  a reply asserted a vehicle canonical state did not hold
  LOC-A  a customer-origin clause suppressed the inspection location in the same message
  LOC-B  no pending state was armed, so a later "sí" could not replay the evidence

VEH-01  "para revisar un 2008 del 2014"      → Peugeot 2008 / 2014 candidate persisted
VEH-02  "Quería revisar un 2008 del 2014"    → identical result (wording is not authority)
VEH-03  "quiero revisar un 2008"             → Peugeot 2008 resolved + confirmation armed
VEH-04  "una 2008 del 2014"                  → Peugeot 2008 / 2014
VEH-05  "una Taos 2020"                      → VW Taos / 2020
VEH-06  "un Focus 2017"                      → Ford Focus / 2017
VEH-07  genuinely ambiguous ("2008 o 2014")  → no arbitrary candidate
VEH-08  intent detectors False + unique evidence → candidate still persisted
STATE-01 reply may name the vehicle when a candidate exists
STATE-02 reply may not assert certainty when no candidate exists
LOC-01  "El auto está en Berazategui"                     → Sur/Berazategui
LOC-02  "Está en Berazategui"                             → Sur/Berazategui
LOC-03  "Está en Berazategui, pero yo soy de Tigre"       → Berazategui; Tigre is origin
LOC-04  "Yo soy de Tigre pero el auto está en Berazategui"→ Berazategui authoritative
LOC-05  no candidate + valid location                     → buffered in state.home_zone_*
LOC-06  candidate created later                           → buffered location attached
CONF-01 deterministic clarification arms pending_fuzzy_catalog_key
CONF-02 confirmation stores the originating turn evidence
CONF-03 "sí" → candidate created and a previously given Berazategui preserved
CONF-04 a reply that names a vehicle with no candidate cannot leave pending state unarmed
WILD-B  full reproduction of the live Wild B turns, through to the $240.000 quote
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT_DIR = __import__("pathlib").Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

for _mod_name in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = sqlalchemy.JSON      # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON         # type: ignore[attr-defined]

os.environ["OUTBOUND_ENABLED"] = "true"

from sqlalchemy import create_engine                      # noqa: E402
from sqlalchemy.orm import Session                        # noqa: E402
from sqlalchemy.pool import StaticPool                    # noqa: E402

from app.services.conversation_engine import (            # noqa: E402
    ConversationEngine,
    _AWAITING_QUALIFICATION,
    _INTENT_PREPURCHASE,
    _strip_customer_origin_clauses,
    _has_customer_origin_clause,
)
from app.schemas.conversation import ConversationHandleIn  # noqa: E402

WILD_B_T1 = "Hola, para revisar un 2008 del 2014, ¿ustedes hacen ese servicio?"
WILD_B_LOC = "Está en Berazategui, pero yo soy de Tigre."


# ── harness ───────────────────────────────────────────────────────────────────

def _zone_db() -> Session:
    """Real SQLite session with the viáticos zones the tests resolve against."""
    import app.models as models
    engine = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    models.Base.metadata.create_all(engine)
    db = Session(engine)
    db.add_all([
        models.ViaticosZone(zone_group="Sur", zone_detail="Berazategui", viaticos=90000),
        models.ViaticosZone(zone_group="Norte", zone_detail="Tigre", viaticos=70000),
        models.ViaticosZone(zone_group="CABA", zone_detail="Palermo", viaticos=0),
    ])
    # Real rows so candidate persistence exercises the actual FK path.
    db.add_all([
        models.WhatsAppContact(id=2044, wa_id="5491153368330", display_name="Tester"),
        models.Lead(id=123, estado="CONSULTA_NUEVA", necesita_humano=False,
                    telefono="5491153368330"),
    ])
    db.flush()
    db.add(models.WhatsAppThread(id=2037, contact_id=2044, lead_id=123))
    db.commit()
    return db


def _make_engine(zone_db: Session | None = None):
    """CE with real vehicle/zone resolution; outbound, AI and pricing stubbed."""
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = zone_db if zone_db is not None else MagicMock()
    eng.settings = MagicMock()
    eng.settings.openai_api_key = "sk-fake"
    eng.settings.whatsapp_flow_id = ""
    eng.settings.whatsapp_location_fallback_flow_id = ""
    eng.settings.whatsapp_vehicle_fallback_flow_id = ""
    eng._send_text_to_wa = MagicMock(return_value="mock-wa-id")
    eng._send_fallback_human_review_notification = MagicMock()
    eng._call_openai = MagicMock(return_value=json.dumps({
        "intent": "QUALIFYING", "reply": "Seguimos.", "deferred_interest": False,
        "candidate": {"action": "none"}, "extracted": {}, "lead_flag": None,
        "needs_human": False,
    }))
    eng._build_ai_messages = MagicMock(return_value=[])
    eng._compute_price_quote = MagicMock(return_value=None)
    eng._pricing = MagicMock()
    eng._scrub_invented_price = MagicMock(side_effect=lambda r, q: r)
    eng._try_schedule_and_flow = MagicMock(return_value=None)
    eng._handle_day_only_request = MagicMock(return_value=None)
    eng._handle_period_request = MagicMock(return_value=None)
    eng._build_quote_reply = MagicMock(return_value="Cotización: $999.")
    eng._apply_extracted = MagicMock()
    eng._apply_candidate = MagicMock()
    eng._apply_narrative_interpretation = MagicMock()
    eng._routing_gate = MagicMock(return_value=(None, True))
    eng._check_fallback_flow_triggers = MagicMock(return_value=None)
    eng._enforce_catalog_vehicle = MagicMock()
    eng._focus_candidate = MagicMock(return_value=None)
    # zone normalisation depends on the pricing repository, which is stubbed here.
    eng._normalize_zone_from_db = MagicMock()
    if zone_db is None:
        eng._extract_zone_from_text = MagicMock(return_value=None)
    return eng


def _make_state(**kw):
    ns = SimpleNamespace(
        last_stage="QUALIFYING", needs_human=False, last_intent=None,
        home_zone_group=None, home_zone_detail=None, home_address=None,
        distance_km=None, current_focus_candidate_id=None, preferred_day=None,
        preferred_time=None, active_requested_date=None, last_requested_time=None,
        last_offered_slots=None, last_visible_slots=None, is_website_lead=False,
        flow_booking_token=None, current_revision_id=None, customer_name=None,
        vehicle_clarification_sent=False, location_clarification_sent=False,
        vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False,
        inspectability_clarification_sent=False,
        last_processed_inbound_wa_message_id=None,
        pending_fuzzy_catalog_key=None, pending_turn_evidence_text=None,
        cycle_reset_pending=False, current_cycle_started_at=None,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _make_ctx(state, candidates=None):
    ctx = SimpleNamespace()
    ctx.thread = SimpleNamespace(id=2037, lead_id=123, contact_id=2044)
    ctx.contact = SimpleNamespace(wa_id="5491153368330")
    ctx.lead = SimpleNamespace(id=123, flag="PRESUPUESTANDO", estado="CONSULTA_NUEVA",
                               nombre=None, telefono="5491153368330", necesita_humano=False)
    ctx.state = state
    ctx.candidates = list(candidates or [])
    ctx.db_messages = []
    ctx.inbound_wa_message_id = None
    return ctx


def _make_event(text):
    return ConversationHandleIn(
        thread_id=2037, wa_message_id=f"msg-{abs(hash(text)) % 100000}",
        wa_id="5491153368330", text=text,
        recent_user_messages=[text], unanswered_recent_user_messages=[text],
    )


def _sent(eng):
    return [c[0][1] for c in eng._send_text_to_wa.call_args_list]


def _candidate(marca=None, modelo=None, anio=None, tipo="SUV_4X4_DEPORTIVO",
               zone_group=None, zone_detail=None, status="current_focus"):
    return SimpleNamespace(id=1, thread_id=2037, marca=marca, modelo=modelo, anio=anio,
                           tipo_vehiculo=tipo, zone_group=zone_group, zone_detail=zone_detail,
                           status=status, label=None, direccion_texto=None)


# ── PHASE A — evidence-driven vehicle capture ────────────────────────────────

class TestVehicleEvidenceCapture(unittest.TestCase):

    def _run(self, text, state=None):
        eng = _make_engine()
        state = state or _make_state()
        ctx = _make_ctx(state)
        eng._process_text(ctx, _make_event(text))
        return eng, state, ctx

    def test_veh_01_purpose_clause_persists_candidate(self):
        """VEH-01 the exact Wild B turn-1 wording persists Peugeot 2008 / 2014."""
        _eng, _state, ctx = self._run(WILD_B_T1)
        self.assertEqual(len(ctx.candidates), 1, "candidate must be persisted")
        cand = ctx.candidates[0]
        self.assertEqual((cand.marca, cand.modelo, cand.anio), ("Peugeot", "2008", 2014))
        self.assertEqual(cand.tipo_vehiculo, "SUV_4X4_DEPORTIVO")

    def test_veh_02_modal_wording_gives_identical_result(self):
        """VEH-02 wording is not authority — the certified Wild A phrasing matches."""
        _eng, _state, ctx = self._run("Quería revisar un 2008 del 2014. ¿Ustedes hacen eso?")
        self.assertEqual(len(ctx.candidates), 1)
        cand = ctx.candidates[0]
        self.assertEqual((cand.marca, cand.modelo, cand.anio), ("Peugeot", "2008", 2014))

    def test_veh_03_bare_numeric_model_resolves_and_arms_confirmation(self):
        """VEH-03 'quiero revisar un 2008' resolves Peugeot 2008 deterministically.

        A bare numeric model with no companion year is the genuinely ambiguous case
        (model vs year), so the certified WILD-02-B owner rule applies: confirm rather
        than create.  The resolution itself is deterministic and the pending state is
        armed, so a following "sí" creates the candidate without further questions.
        """
        eng, state, ctx = self._run("quiero revisar un 2008")
        self.assertTrue(any("Peugeot 2008" in t for t in _sent(eng)))
        self.assertEqual(state.pending_fuzzy_catalog_key, "Peugeot||2008")
        self.assertIsNotNone(state.pending_turn_evidence_text)
        self.assertEqual(len(ctx.candidates), 0)

    def test_veh_04_feminine_article_model_del_year(self):
        """VEH-04 'una 2008 del 2014' → Peugeot 2008 / 2014."""
        _eng, _state, ctx = self._run("Hola, una 2008 del 2014, ¿la revisan?")
        self.assertEqual(len(ctx.candidates), 1)
        self.assertEqual((ctx.candidates[0].marca, ctx.candidates[0].anio), ("Peugeot", 2014))

    def test_veh_05_taos_model_only(self):
        """VEH-05 'una Taos 2020' → Volkswagen Taos / 2020."""
        _eng, _state, ctx = self._run("Hola, para revisar una Taos 2020")
        self.assertEqual(len(ctx.candidates), 1)
        cand = ctx.candidates[0]
        self.assertEqual((cand.marca, cand.modelo, cand.anio), ("Volkswagen", "Taos", 2020))

    def test_veh_06_focus_model_only(self):
        """VEH-06 'un Focus 2017' → Ford Focus / 2017."""
        _eng, _state, ctx = self._run("Hola, para revisar un Focus 2017")
        self.assertEqual(len(ctx.candidates), 1)
        cand = ctx.candidates[0]
        self.assertEqual((cand.marca, cand.modelo, cand.anio), ("Ford", "Focus", 2017))

    def test_veh_07_ambiguous_years_create_no_candidate(self):
        """VEH-07 '2008 o 2014' is a year comparison — no arbitrary candidate."""
        _eng, _state, ctx = self._run("Quiero revisar algo, 2008 o 2014")
        self.assertEqual(len(ctx.candidates), 0)

    def test_veh_08_intent_detectors_false_but_evidence_unique(self):
        """VEH-08 the intent whitelist no longer decides whether evidence is captured."""
        eng = _make_engine()
        norm = eng._norm_text(WILD_B_T1)
        self.assertFalse(eng._detect_prepurchase_signal(norm))
        self.assertFalse(eng._detect_explicit_inspection_request(norm))
        state = _make_state()
        ctx = _make_ctx(state)
        eng._process_text(ctx, _make_event(WILD_B_T1))
        self.assertEqual(len(ctx.candidates), 1,
                         "evidence must be captured despite both intent detectors being False")


# ── PHASE B — response / canonical state consistency ─────────────────────────

class TestResponseStateConsistency(unittest.TestCase):

    def test_state_01_reply_may_name_vehicle_when_candidate_exists(self):
        """STATE-01 with a persisted candidate the reply is left untouched."""
        eng = _make_engine()
        state = _make_state(current_focus_candidate_id=1)
        ctx = _make_ctx(state, candidates=[_candidate("Peugeot", "2008", 2014)])
        reply = "Sí, hacemos la revisión del Peugeot 2008 2014."
        self.assertEqual(eng._enforce_canonical_vehicle_claim(reply, ctx, state), reply)

    def test_state_02_reply_cannot_assert_vehicle_without_candidate(self):
        """STATE-02 the exact Wild B reply is reconciled with canonical state."""
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state)
        eng._turn_text = WILD_B_T1
        reply = "¡Hola! Sí, hacemos el servicio de revisión para un 2008 del 2014."
        out = eng._enforce_canonical_vehicle_claim(reply, ctx, state)
        self.assertNotEqual(out, reply)
        self.assertIn("¿Es un Peugeot 2008?", out)
        self.assertIn("Todavía no tengo confirmado el vehículo", out)

    def test_state_02b_reconciliation_arms_pending_state(self):
        """STATE-02 / CONF-04 the reconciled reply also arms the pending confirmation."""
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state)
        eng._turn_text = WILD_B_T1
        eng._enforce_canonical_vehicle_claim(
            "Sí, hacemos el servicio para un 2008 del 2014.", ctx, state)
        self.assertEqual(state.pending_fuzzy_catalog_key, "Peugeot||2008")
        self.assertEqual(state.pending_turn_evidence_text, WILD_B_T1)

    def test_state_02c_reply_without_vehicle_is_untouched(self):
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state)
        reply = "¡Hola! Contanos qué necesitás y te ayudamos."
        self.assertEqual(eng._enforce_canonical_vehicle_claim(reply, ctx, state), reply)


# ── PHASE C — location evidence with an origin clause ────────────────────────

class TestLocationEvidence(unittest.TestCase):

    def setUp(self):
        self.db = _zone_db()
        self.eng = _make_engine(self.db)

    def tearDown(self):
        self.db.close()

    def _apply(self, text, candidates=None, state=None):
        state = state or _make_state()
        ctx = _make_ctx(state, candidates=candidates)
        early, written = self.eng._apply_zone_from_text(ctx, state, text)
        return state, ctx, early, written

    def test_loc_01_explicit_subject(self):
        """LOC-01 'El auto está en Berazategui' → Sur/Berazategui."""
        state, _ctx, _e, _w = self._apply("El auto está en Berazategui.")
        self.assertEqual((state.home_zone_group, state.home_zone_detail), ("Sur", "Berazategui"))

    def test_loc_02_subjectless(self):
        """LOC-02 'Está en Berazategui' → Sur/Berazategui."""
        state, _ctx, _e, _w = self._apply("Está en Berazategui.")
        self.assertEqual((state.home_zone_group, state.home_zone_detail), ("Sur", "Berazategui"))

    def test_loc_03_subjectless_plus_origin_clause(self):
        """LOC-03 the exact Wild B location turn keeps Berazategui and ignores Tigre."""
        state, _ctx, _e, _w = self._apply(WILD_B_LOC)
        self.assertEqual((state.home_zone_group, state.home_zone_detail), ("Sur", "Berazategui"))
        self.assertNotEqual(state.home_zone_detail, "Tigre")

    def test_loc_04_origin_first_then_vehicle_location(self):
        """LOC-04 'Yo soy de Tigre pero el auto está en Berazategui' → Berazategui."""
        state, _ctx, _e, _w = self._apply("Yo soy de Tigre pero el auto está en Berazategui.")
        self.assertEqual((state.home_zone_group, state.home_zone_detail), ("Sur", "Berazategui"))

    def test_loc_05_buffers_when_no_candidate(self):
        """LOC-05 with no candidate the inspection location is buffered on thread state."""
        state, ctx, _e, _w = self._apply(WILD_B_LOC)
        self.assertEqual(ctx.candidates, [])
        self.assertEqual(state.home_zone_detail, "Berazategui")

    def test_loc_06_buffered_location_attached_to_new_candidate(self):
        """LOC-06 a candidate created afterwards inherits the buffered location."""
        state, ctx, _e, _w = self._apply(WILD_B_LOC)
        cand = _candidate("Peugeot", "2008", 2014)
        ctx.candidates = [cand]
        self.eng._attach_buffered_location(ctx, state)
        self.assertEqual((cand.zone_group, cand.zone_detail), ("Sur", "Berazategui"))

    def test_loc_06b_explicit_candidate_zone_is_never_overwritten(self):
        """The buffer must not override per-candidate evidence."""
        state = _make_state(home_zone_group="Sur", home_zone_detail="Berazategui")
        ctx = _make_ctx(state, candidates=[_candidate("Peugeot", "2008", 2014,
                                                     zone_group="CABA", zone_detail="Palermo")])
        self.eng._attach_buffered_location(ctx, state)
        self.assertEqual(ctx.candidates[0].zone_detail, "Palermo")

    def test_origin_clause_stripper(self):
        self.assertEqual(_strip_customer_origin_clauses(WILD_B_LOC), "Está en Berazategui")
        self.assertTrue(_has_customer_origin_clause(WILD_B_LOC))
        self.assertFalse(_has_customer_origin_clause(
            _strip_customer_origin_clauses(WILD_B_LOC)))


# ── PHASE D/E — deterministic confirmation + replay ──────────────────────────

class TestConfirmationContract(unittest.TestCase):

    def setUp(self):
        self.db = _zone_db()
        self.eng = _make_engine(self.db)

    def tearDown(self):
        self.db.close()

    def test_conf_01_clarification_arms_pending_key(self):
        """CONF-01 the deterministic clarification always arms pending state."""
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        ctx = _make_ctx(state)
        self.eng._process_text(ctx, _make_event("Sí, un 2008 en Berazategui"))
        self.assertEqual(state.pending_fuzzy_catalog_key, "Peugeot||2008")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)

    def test_conf_02_pending_turn_evidence_stored(self):
        """CONF-02 the originating turn text is stored for replay."""
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        ctx = _make_ctx(state)
        self.eng._process_text(ctx, _make_event("Sí, un 2008 en Berazategui"))
        self.assertIn("2008", state.pending_turn_evidence_text or "")

    def test_conf_03_yes_creates_candidate_and_keeps_location(self):
        """CONF-03 after a location was already given, "sí" must not lose it."""
        state = _make_state(
            last_intent=_INTENT_PREPURCHASE,
            pending_fuzzy_catalog_key="Peugeot||2008",
            pending_turn_evidence_text="para revisar un 2008 del 2014",
            home_zone_group="Sur", home_zone_detail="Berazategui",
        )
        ctx = _make_ctx(state)
        self.eng._process_text(ctx, _make_event("Sí"))
        self.assertEqual(len(ctx.candidates), 1, "confirmation must create the candidate")
        cand = ctx.candidates[0]
        self.assertEqual((cand.marca, cand.modelo), ("Peugeot", "2008"))
        self.assertEqual((cand.zone_group, cand.zone_detail), ("Sur", "Berazategui"))
        self.assertIsNone(state.pending_fuzzy_catalog_key, "pending state consumed")

    def test_conf_04_unsupported_confirmation_cannot_stay_unarmed(self):
        """CONF-04 an AI-style confirmation with no pending state is reconciled."""
        state = _make_state()
        ctx = _make_ctx(state)
        self.eng._turn_text = WILD_B_T1
        ai_reply = ("Genial! Para poder cotizar, ¿podrías confirmarme el tipo de vehículo "
                    "que querés revisar? En este caso, un 2008 del 2014. ¿Es correcto?")
        out = self.eng._enforce_canonical_vehicle_claim(ai_reply, ctx, state)
        self.assertEqual(state.pending_fuzzy_catalog_key, "Peugeot||2008")
        self.assertIn("¿Es un Peugeot 2008?", out)


# ── FULL WILD B REPRODUCTION ─────────────────────────────────────────────────

class TestWildBReproduction(unittest.TestCase):
    """The exact live Wild B sequence, from zero state through to the quote."""

    def setUp(self):
        self.db = _zone_db()
        self.eng = _make_engine(self.db)
        self.state = _make_state()
        self.ctx = _make_ctx(self.state)

    def tearDown(self):
        self.db.close()

    def test_wild_b_turn1_then_location_then_quote(self):
        # Turn 1 — vehicle evidence inside a purpose clause + FAQ burst
        burst = (WILD_B_T1 + " ¿Entregan informes? ¿Qué contenido tienen los informes? "
                 "¿Tengo que estar yo presente? ¿Se puede pagar con débito?")
        self.eng._process_text(self.ctx, _make_event(burst))

        self.assertEqual(len(self.ctx.candidates), 1, "exactly one candidate")
        cand = self.ctx.candidates[0]
        self.assertEqual(cand.marca, "Peugeot")
        self.assertEqual(cand.modelo, "2008")
        self.assertEqual(cand.anio, 2014)
        self.assertEqual(cand.tipo_vehiculo, "SUV_4X4_DEPORTIVO")

        # Turn 2 — subjectless inspection location plus a customer-origin clause
        self.eng._focus_candidate = MagicMock(return_value=cand)
        self.state.current_focus_candidate_id = cand.id
        self.eng._apply_zone_from_text(self.ctx, self.state, WILD_B_LOC)

        zone_group = cand.zone_group or self.state.home_zone_group
        zone_detail = cand.zone_detail or self.state.home_zone_detail
        self.assertEqual((zone_group, zone_detail), ("Sur", "Berazategui"))
        self.assertNotEqual(zone_detail, "Tigre", "customer origin must never be the location")

        # No redundant vehicle confirmation was needed
        self.assertIsNone(self.state.pending_fuzzy_catalog_key)

        # Pricing on the canonical state → 150000 + 90000 = 240000
        from app.services.pricing import PricingService
        from app.repositories.pricing_repository import PricingRepository
        repo = PricingRepository()
        repo.find_base_price = lambda tipo: SimpleNamespace(  # type: ignore[assignment]
            tipo_vehiculo="SUV_4X4_DEPORTIVO", precio_base=150000)
        quote = PricingService(repository=repo).quote(
            self.db, cand.tipo_vehiculo, zone_group, zone_detail)
        self.assertEqual(quote.precio_base, 150000)
        self.assertEqual(quote.viaticos, 90000)
        self.assertEqual(quote.precio_total, 240000)


# ── PHASE F — decision logging ───────────────────────────────────────────────

class TestDecisionLogging(unittest.TestCase):

    def test_decision_log_emits_structured_record(self):
        import logging
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state)
        with self.assertLogs("app.services.conversation_engine", level="INFO") as captured:
            eng._decision_log(ctx, "unit_probe", marca="Peugeot", modelo="2008")
        joined = " ".join(captured.output)
        self.assertIn("CE_DECISION", joined)
        self.assertIn("event=unit_probe", joined)
        self.assertIn("thread_id=2037", joined)
        self.assertIn("marca=Peugeot", joined)

    def test_capture_path_emits_decision_records(self):
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state)
        with self.assertLogs("app.services.conversation_engine", level="INFO") as captured:
            eng._process_text(ctx, _make_event(WILD_B_T1))
        joined = " ".join(captured.output)
        self.assertIn("event=intent_gate", joined)
        self.assertIn("event=vehicle_candidate_persisted", joined)

    def test_decision_log_never_raises(self):
        eng = _make_engine()
        broken_ctx = SimpleNamespace(thread=None)
        eng._decision_log(broken_ctx, "probe", x=1)   # must not raise


if __name__ == "__main__":
    unittest.main()
