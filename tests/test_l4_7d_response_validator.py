"""L4.7D — canonical response validator.

The AI composes natural language; it may not assert business facts canonical state does not
support. Validation runs between composition and OutboundSafetyGate on every CE text path.

VAL-VEH-01   vehicle named, candidate exists            → allowed
VAL-VEH-02   vehicle named, no candidate                → certainty blocked
VAL-LOC-01   candidate zone Berazategui                 → allowed
VAL-LOC-02   only customer origin Tigre                 → inspection-location claim blocked
VAL-PRICE-01 canonical quote 240000 stated              → allowed
VAL-PRICE-02 220000 stated with canonical 240000        → rewritten to canonical
VAL-PRICE-03 amount stated with no quote                → blocked
VAL-AVAIL-01 slots produced by ScheduleService          → allowed
VAL-AVAIL-02 availability asserted, never evaluated     → blocked
VAL-BOOK-01  Flow sent but not completed                → "turno confirmado" blocked
VAL-BOOK-02  booked ThreadRevision                      → allowed
VAL-ACC-01   quote not accepted                         → "presupuesto aceptado" blocked
VAL-ACC-02   canonical acceptance                       → allowed
VAL-MIX-01   valid FAQ + invalid booking claim          → FAQ preserved, claim removed
VAL-PATH-01  every CE text send path runs the validator
"""
from __future__ import annotations

import json
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT_DIR = __import__("pathlib").Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

for _mod in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = sqlalchemy.JSON     # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON        # type: ignore[attr-defined]

from sqlalchemy import create_engine                    # noqa: E402
from sqlalchemy.pool import StaticPool                  # noqa: E402

from app.services.response_validator import (           # noqa: E402
    CanonicalFacts,
    validate_response,
    CLAIM_VEHICLE, CLAIM_LOCATION, CLAIM_PRICE,
    CLAIM_AVAILABILITY, CLAIM_BOOKING, CLAIM_ACCEPTANCE,
    ACTION_ALLOWED, ACTION_REMOVED, ACTION_REWRITTEN,
)

ZONES = ("Berazategui", "Tigre", "Palermo", "Sur", "Norte", "CABA")


def _facts(**kw) -> CanonicalFacts:
    base = dict(known_zone_names=ZONES)
    base.update(kw)
    return CanonicalFacts(**base)


def _resolver(fragment: str):
    from app.services.vehicle_catalog import (
        lookup_vehicle, extract_model_del_year, _contextual_numeric_model_lookup,
    )
    hit = lookup_vehicle(fragment)
    if hit is not None:
        return hit
    mdy = extract_model_del_year(fragment)
    if mdy is not None:
        return mdy[0]
    return _contextual_numeric_model_lookup(fragment)


def _claims(result, claim):
    return [f for f in result.findings if f.claim == claim]


# ── VEHICLE ───────────────────────────────────────────────────────────────────

class TestVehicleClaim(unittest.TestCase):

    def test_val_veh_01_allowed_with_candidate(self):
        facts = _facts(vehicle_marca="Peugeot", vehicle_modelo="2008")
        text = "Sí, hacemos la revisión del Peugeot 2008."
        out = validate_response(text, facts, vehicle_resolver=_resolver)
        self.assertEqual(out.text, text)
        self.assertTrue(_claims(out, CLAIM_VEHICLE)[0].allowed)

    def test_val_veh_02_blocked_without_candidate(self):
        facts = _facts()
        out = validate_response(
            "Sí, hacemos el servicio de revisión para un Peugeot 2008.",
            facts, vehicle_resolver=_resolver)
        self.assertNotIn("Peugeot 2008", out.text)
        finding = _claims(out, CLAIM_VEHICLE)[0]
        self.assertFalse(finding.allowed)
        self.assertEqual(finding.proof, "no current-focus candidate")

    def test_val_veh_02b_wrong_vehicle_blocked(self):
        facts = _facts(vehicle_marca="Peugeot", vehicle_modelo="2008")
        out = validate_response("Coordinamos la revisión del Ford Focus.",
                                facts, vehicle_resolver=_resolver)
        self.assertNotIn("Ford Focus", out.text)
        self.assertFalse(_claims(out, CLAIM_VEHICLE)[0].allowed)

    def test_question_is_never_a_claim(self):
        facts = _facts()
        text = "¿Es un Peugeot 2008?"
        out = validate_response(text, facts, vehicle_resolver=_resolver)
        self.assertEqual(out.text, text)


# ── LOCATION ──────────────────────────────────────────────────────────────────

class TestLocationClaim(unittest.TestCase):

    def test_val_loc_01_canonical_zone_allowed(self):
        facts = _facts(inspection_zone_detail="Berazategui", inspection_zone_group="Sur")
        text = "El auto está en Berazategui, así que la revisión es a domicilio."
        out = validate_response(text, facts, vehicle_resolver=_resolver)
        self.assertIn("Berazategui", out.text)
        self.assertTrue(_claims(out, CLAIM_LOCATION)[0].allowed)

    def test_val_loc_02_customer_origin_never_inspection_location(self):
        facts = _facts(customer_origin_zones=("Tigre",))
        out = validate_response("El auto está en Tigre.", facts, vehicle_resolver=_resolver)
        self.assertNotIn("Tigre", out.text)
        finding = _claims(out, CLAIM_LOCATION)[0]
        self.assertFalse(finding.allowed)
        self.assertIn("no canonical inspection location", finding.proof)

    def test_val_loc_02b_origin_zone_with_canonical_elsewhere_blocked(self):
        facts = _facts(inspection_zone_detail="Berazategui", inspection_zone_group="Sur",
                       customer_origin_zones=("Tigre",))
        out = validate_response("La revisión del auto en Tigre queda coordinada.",
                                facts, vehicle_resolver=_resolver)
        self.assertNotIn("Tigre", out.text)
        self.assertIn("customer origin stated as inspection location",
                      _claims(out, CLAIM_LOCATION)[0].detail)

    def test_customer_origin_statement_allowed(self):
        facts = _facts(inspection_zone_detail="Berazategui", customer_origin_zones=("Tigre",))
        text = "Entiendo que vos vivís en Tigre, pero la revisión del auto es donde está."
        out = validate_response(text, facts, vehicle_resolver=_resolver)
        self.assertIn("Tigre", out.text)


# ── PRICE ─────────────────────────────────────────────────────────────────────

class TestPriceClaim(unittest.TestCase):

    def test_val_price_01_canonical_amount_allowed(self):
        facts = _facts(vehicle_marca="Peugeot", vehicle_modelo="2008",
                       inspection_zone_detail="Berazategui",
                       quote_total=240000, quote_base=150000, quote_viaticos=90000)
        text = "La cotización para la revisión del Peugeot 2008 en Berazategui es de $240.000."
        out = validate_response(text, facts, vehicle_resolver=_resolver)
        self.assertEqual(out.text, text)
        self.assertTrue(_claims(out, CLAIM_PRICE)[0].allowed)

    def test_val_price_02_wrong_amount_rewritten(self):
        facts = _facts(quote_total=240000, quote_base=150000, quote_viaticos=90000)
        out = validate_response("La revisión sale $220.000.", facts, vehicle_resolver=_resolver)
        self.assertIn("$240.000", out.text)
        self.assertNotIn("$220.000", out.text)
        self.assertEqual(_claims(out, CLAIM_PRICE)[0].action, ACTION_REWRITTEN)

    def test_val_price_03_no_quote_blocks_amount(self):
        facts = _facts()
        out = validate_response("La revisión sale $240.000.", facts, vehicle_resolver=_resolver)
        self.assertNotIn("240", out.text)
        finding = _claims(out, CLAIM_PRICE)[0]
        self.assertFalse(finding.allowed)
        self.assertEqual(finding.proof, "no PricingService quote")

    def test_price_components_allowed(self):
        facts = _facts(quote_total=240000, quote_base=150000, quote_viaticos=90000)
        text = "Son $240.000 en total: $150.000 de base más $90.000 de viáticos."
        out = validate_response(text, facts, vehicle_resolver=_resolver)
        self.assertEqual(out.text, text)


# ── AVAILABILITY ──────────────────────────────────────────────────────────────

class TestAvailabilityClaim(unittest.TestCase):

    def test_val_avail_01_offered_slot_allowed(self):
        facts = _facts(availability_checked=True, offered_slots=("13:00",))
        text = "Para jueves 03/09 tengo 13:00."
        out = validate_response(text, facts, vehicle_resolver=_resolver)
        self.assertEqual(out.text, text)
        self.assertTrue(_claims(out, CLAIM_AVAILABILITY)[0].allowed)

    def test_val_avail_02_unevaluated_availability_blocked(self):
        facts = _facts()
        out = validate_response("Tenemos disponibilidad esta semana.",
                                facts, vehicle_resolver=_resolver)
        self.assertNotIn("disponibilidad", out.text)
        finding = _claims(out, CLAIM_AVAILABILITY)[0]
        self.assertFalse(finding.allowed)
        self.assertEqual(finding.proof, "no ScheduleService evaluation")

    def test_val_avail_02b_unoffered_slot_blocked(self):
        facts = _facts(availability_checked=True, offered_slots=("13:00",))
        out = validate_response("Te puedo ofrecer 16:30.", facts, vehicle_resolver=_resolver)
        self.assertNotIn("16:30", out.text)
        self.assertFalse(_claims(out, CLAIM_AVAILABILITY)[0].allowed)

    def test_negative_availability_allowed_after_evaluation(self):
        """Saying a slot is NOT available names a time precisely because it is unavailable."""
        facts = _facts(availability_checked=True, offered_slots=("13:00",))
        text = ("Para jueves 03/09 a las 15:00 no tenemos disponibilidad "
                "(ese día trabajamos de 9 a 14 hs).")
        out = validate_response(text, facts, vehicle_resolver=_resolver)
        self.assertEqual(out.text, text)


# ── BOOKING ───────────────────────────────────────────────────────────────────

class TestBookingClaim(unittest.TestCase):

    def test_val_book_01_flow_sent_is_not_booked(self):
        facts = _facts(booking_confirmed=False)
        out = validate_response("Listo, tu turno está confirmado para el jueves.",
                                facts, vehicle_resolver=_resolver)
        self.assertNotIn("confirmado", out.text)
        finding = _claims(out, CLAIM_BOOKING)[0]
        self.assertFalse(finding.allowed)
        self.assertEqual(finding.proof, "no booked ThreadRevision")

    def test_val_book_02_booked_allows_confirmation(self):
        facts = _facts(booking_confirmed=True)
        text = "Listo, tu turno está confirmado."
        out = validate_response(text, facts, vehicle_resolver=_resolver)
        self.assertEqual(out.text, text)
        self.assertTrue(_claims(out, CLAIM_BOOKING)[0].allowed)

    def test_flow_body_is_not_a_booking_claim(self):
        """The Booking Flow invitation must survive validation."""
        facts = _facts(availability_checked=True, offered_slots=("13:00",))
        text = ("Ese horario está disponible 🎉 "
                "Para confirmar el turno, elegí el horario y completá tus datos.")
        out = validate_response(text, facts, vehicle_resolver=_resolver)
        self.assertEqual(out.text, text)


# ── ACCEPTANCE ────────────────────────────────────────────────────────────────

class TestAcceptanceClaim(unittest.TestCase):

    def test_val_acc_01_unaccepted_blocked(self):
        facts = _facts(acceptance_confirmed=False)
        out = validate_response("Como el presupuesto aceptado ya está, seguimos.",
                                facts, vehicle_resolver=_resolver)
        self.assertNotIn("presupuesto aceptado", out.text)
        self.assertFalse(_claims(out, CLAIM_ACCEPTANCE)[0].allowed)

    def test_val_acc_02_accepted_allowed(self):
        facts = _facts(acceptance_confirmed=True)
        text = "Perfecto, avanzamos con la reserva entonces."
        out = validate_response(text, facts, vehicle_resolver=_resolver)
        self.assertEqual(out.text, text)
        self.assertTrue(_claims(out, CLAIM_ACCEPTANCE)[0].allowed)


# ── MIXED / FAILURE BEHAVIOUR ─────────────────────────────────────────────────

class TestMixedResponses(unittest.TestCase):

    def test_val_mix_01_faq_preserved_booking_removed(self):
        facts = _facts(booking_confirmed=False)
        out = validate_response(
            "Aceptamos efectivo, transferencia bancaria y Mercado Pago. "
            "Tu turno está confirmado para mañana. "
            "¿En qué zona está el auto?",
            facts, vehicle_resolver=_resolver)
        self.assertIn("Mercado Pago", out.text)
        self.assertIn("¿En qué zona está el auto?", out.text)
        self.assertNotIn("confirmado", out.text)

    def test_required_next_question_survives(self):
        facts = _facts()
        out = validate_response(
            "El auto está en Tigre. ¿En qué zona o barrio está el auto?",
            facts, vehicle_resolver=_resolver)
        self.assertIn("¿En qué zona o barrio está el auto?", out.text)

    def test_fallback_when_nothing_survives(self):
        facts = _facts()
        out = validate_response("Tu turno está confirmado.", facts, vehicle_resolver=_resolver)
        self.assertIn("necesito confirmar", out.text.lower())

    def test_clean_text_is_untouched(self):
        facts = _facts()
        text = "Hola! Contame qué necesitás y te ayudo."
        self.assertEqual(validate_response(text, facts, vehicle_resolver=_resolver).text, text)

    def test_empty_text_is_safe(self):
        self.assertEqual(validate_response("", _facts()).text, "")


# ── CE INTEGRATION: path coverage + canonical facts ──────────────────────────

def _ce_engine(db=None):
    from app.services.conversation_engine import ConversationEngine
    import app.models as models
    sqlite = create_engine("sqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(sqlite)
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = db if db is not None else MagicMock(bind=sqlite)
    eng.settings = MagicMock()
    eng._correlation_id = "corr-l47d"
    eng._turn_price_quote = None
    eng._availability_checked = False
    eng._zone_names_cache = ZONES
    eng._pricing = MagicMock()
    eng._focus_candidate = MagicMock(return_value=None)
    eng._compute_price_quote = MagicMock(return_value=None)
    eng._extract_zone_from_text = MagicMock(return_value=None)
    return eng


def _ce_ctx(state=None):
    from app.services.conversation_engine import _Context
    ctx = _Context.__new__(_Context)
    ctx.thread = SimpleNamespace(id=2037, lead_id=123, contact_id=2044, last_message_at=None)
    ctx.lead = SimpleNamespace(id=123, flag="PRESUPUESTANDO", estado="CONSULTA_NUEVA",
                               nombre=None, telefono="5491153368330", necesita_humano=False)
    ctx.contact = SimpleNamespace(wa_id="5491153368330")
    ctx.candidates = []
    ctx.state = state
    ctx.db_messages = []
    ctx.inbound_wa_message_id = None
    return ctx


def _ce_state(**kw):
    ns = SimpleNamespace(
        last_stage="QUALIFYING", needs_human=False, last_intent=None,
        home_zone_group=None, home_zone_detail=None, current_focus_candidate_id=None,
        preferred_day=None, preferred_time=None, active_requested_date=None,
        last_requested_time=None, last_offered_slots=None, last_visible_slots=None,
        is_website_lead=False, flow_booking_token=None, current_revision_id=None,
        customer_name=None, pending_fuzzy_catalog_key=None, pending_turn_evidence_text=None,
        cycle_reset_pending=False, current_cycle_started_at=None,
        vehicle_clarification_sent=False, location_clarification_sent=False,
        vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False,
        inspectability_clarification_sent=False,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


class TestValidatorOnEverySendPath(unittest.TestCase):

    def test_val_path_01_text_and_flow_paths_both_validate(self):
        """VAL-PATH-01 — both CE senders call the validator before the gate."""
        import inspect
        from app.services.conversation_engine import ConversationEngine
        for name in ("_send_text_to_wa", "_send_flow_button"):
            src = inspect.getsource(getattr(ConversationEngine, name))
            self.assertIn("_validate_outbound_text", src, f"{name} must validate")
            self.assertLess(src.index("_validate_outbound_text"), src.index("gate.attempt"),
                            f"{name} must validate BEFORE OutboundSafetyGate")

    def test_val_path_01b_no_other_direct_sender_exists(self):
        """No CE code path may reach Meta without passing the validated senders."""
        import inspect
        from app.services import conversation_engine as ce
        src = inspect.getsource(ce)
        self.assertEqual(src.count("_send_whatsapp_cloud_text(to_wa_id="), 1)
        self.assertEqual(src.count("_send_whatsapp_cloud_flow("), 1)

    def test_validator_runs_in_text_path(self):
        from app.services.conversation_engine import GateOutcome
        eng = _ce_engine()
        state = _ce_state()
        ctx = _ce_ctx(state)
        gate = MagicMock()
        gate.attempt.return_value = SimpleNamespace(outcome=GateOutcome.ALLOWED, message_id=1)
        with patch("app.services.conversation_engine.OutboundSafetyGate", return_value=gate), \
             patch("app.services.conversation_engine._send_whatsapp_cloud_text",
                   return_value=("wamid.T", {})):
            eng._send_text_to_wa(ctx, "Tu turno está confirmado.")
        sent = gate.attempt.call_args.kwargs["text"]
        self.assertNotIn("confirmado", sent, "unsupported booking claim reached the gate")

    def test_canonical_facts_reflect_state(self):
        eng = _ce_engine()
        state = _ce_state(last_stage="SCHEDULING", current_revision_id=None,
                          last_visible_slots=json.dumps(["13:00"]))
        ctx = _ce_ctx(state)
        ctx.lead.flag = "ACEPTADO"
        eng._get_active_inspection_location = MagicMock(return_value=("Sur", "Berazategui"))
        facts = eng._build_canonical_facts(ctx, state)
        self.assertEqual(facts.inspection_zone_detail, "Berazategui")
        self.assertTrue(facts.acceptance_confirmed)
        self.assertFalse(facts.booking_confirmed)
        self.assertTrue(facts.availability_checked)
        self.assertEqual(facts.offered_slots, ("13:00",))

    def test_canonical_facts_booking_confirmed_when_revision_exists(self):
        eng = _ce_engine()
        state = _ce_state(last_stage="BOOKED", current_revision_id=77)
        ctx = _ce_ctx(state)
        eng._get_active_inspection_location = MagicMock(return_value=(None, None))
        facts = eng._build_canonical_facts(ctx, state)
        self.assertTrue(facts.booking_confirmed)

    def test_validation_failure_never_breaks_a_turn(self):
        eng = _ce_engine()
        eng._build_canonical_facts = MagicMock(side_effect=RuntimeError("boom"))
        ctx = _ce_ctx(_ce_state())
        self.assertEqual(eng._validate_outbound_text("hola", ctx, ctx.state), "hola")

    def test_ce_response_validation_logging(self):
        eng = _ce_engine()
        state = _ce_state()
        ctx = _ce_ctx(state)
        eng._get_active_inspection_location = MagicMock(return_value=(None, None))
        with self.assertLogs("app.services.conversation_engine", level="INFO") as captured:
            eng._validate_outbound_text("Tu turno está confirmado.", ctx, state)
        joined = " ".join(captured.output)
        self.assertIn("CE_RESPONSE_VALIDATION", joined)
        self.assertIn("claim=BOOKING", joined)
        self.assertIn("allowed=False", joined)


if __name__ == "__main__":
    unittest.main()
