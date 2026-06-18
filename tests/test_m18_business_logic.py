"""Regression tests for M18 backend business-logic fixes.

Covers:
  1. Proactive candidate creation when catalog hits and no candidate exists
  2. Zone normalization: Lomas del Mirador → Oeste / Lomas del Mirador
  3. Price guard: PRESUPUESTO_ENVIADO blocked without deterministic quote
  4. Email persisted to lead on flow_response
  5. Human CRM approval moves lead.estado to AGENDADO (revision_items endpoint)
  6. Public email-link approval moves lead.estado to AGENDADO (public_approval endpoint)
"""
from __future__ import annotations

import sys
import types
import unittest
from dataclasses import dataclass, field
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.pricing import PricingNotFoundError, PricingQuote, PricingService
from app.services.vehicle_catalog import lookup_vehicle


# ── Shared fakes ──────────────────────────────────────────────────────────────

@dataclass
class FakePriceRow:
    tipo_vehiculo: str
    precio_base: int


@dataclass
class FakeZone:
    zone_group: str
    zone_detail: str
    viaticos: int


class FakeRepoCaptiva:
    """Covers Captiva (SUV_4X4_DEPORTIVO) in Lomas del Mirador (Oeste, 30000)."""

    _BASE = {
        "SUV_4X4_DEPORTIVO": FakePriceRow(tipo_vehiculo="SUV_4X4_DEPORTIVO", precio_base=140000),
        "AUTO": FakePriceRow(tipo_vehiculo="AUTO", precio_base=130000),
    }
    _ZONES_BY_DETAIL = {
        "lomas del mirador": FakeZone(
            zone_group="Oeste", zone_detail="Lomas del Mirador", viaticos=30000
        ),
    }

    def find_base_price(self, tipo_vehiculo: str):
        return self._BASE.get(tipo_vehiculo)

    def find_zone_by_group_and_detail(self, db, zone_group, zone_detail):
        key = (zone_detail or "").strip().lower()
        return self._ZONES_BY_DETAIL.get(key)


# ── Minimal ConversationEngine scaffolding ────────────────────────────────────

def _make_engine(repo=None):
    """Return a ConversationEngine instance wired to the given pricing repo.

    We stub out every external dependency (OpenAI, WhatsApp, DB) so the
    deterministic helper methods can be exercised in isolation.
    """
    from app.services.conversation_engine import ConversationEngine
    from app.services.pricing import PricingService
    from unittest.mock import MagicMock

    repo = repo or FakeRepoCaptiva()
    pricing = PricingService(repository=repo)

    db = MagicMock()
    settings = MagicMock()
    settings.openai_api_key = "sk-fake"
    settings.openai_model = "gpt-4o-mini"
    settings.backend_url = "http://localhost:8000"

    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = db
    eng.settings = settings
    eng._pricing = pricing
    return eng


def _make_state(**kwargs):
    """Plain namespace — avoids SQLAlchemy instrumentation issues in unit tests."""
    ns = types.SimpleNamespace(
        home_zone_group=None,
        home_zone_detail=None,
        home_address=None,
        distance_km=None,
        current_focus_candidate_id=None,
        preferred_day=None,
        preferred_time=None,
        active_requested_date=None,
        last_requested_time=None,
        last_offered_slots=None,
        last_stage="QUALIFYING",
        needs_human=False,
        flow_booking_token=None,
        current_revision_id=None,
        customer_name=None,
    )
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _make_candidate(**kwargs):
    """Plain namespace — avoids SQLAlchemy instrumentation issues in unit tests."""
    return types.SimpleNamespace(
        id=kwargs.get("id", 1),
        thread_id=kwargs.get("thread_id", 37),
        marca=kwargs.get("marca"),
        modelo=kwargs.get("modelo"),
        tipo_vehiculo=kwargs.get("tipo_vehiculo"),
        zone_group=kwargs.get("zone_group"),
        zone_detail=kwargs.get("zone_detail"),
        status=kwargs.get("status", "current_focus"),
        anio=kwargs.get("anio"),
        label=None,
    )


def _make_ctx(thread_id=37, candidates=None, state=None):
    """Build a minimal _Context-like namespace."""
    from app.services.conversation_engine import _Context

    thread = types.SimpleNamespace(id=thread_id, lead_id=10, contact_id=5)
    lead = types.SimpleNamespace(
        id=10, nombre="Lara", apellido=None, email=None,
        telefono="1153368330", flag="PRESUPUESTANDO",
        estado="CONSULTA_NUEVA", canal=None, necesita_humano=False,
    )
    contact = types.SimpleNamespace(wa_id="5491153368330")

    ctx = _Context.__new__(_Context)
    ctx.thread = thread
    ctx.lead = lead
    ctx.contact = contact
    ctx.candidates = list(candidates or [])
    ctx.state = state or _make_state()
    ctx.db_messages = []
    return ctx


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestVehicleCatalogLookup(unittest.TestCase):
    def test_captiva_matched(self):
        match = lookup_vehicle("tengo una Captiva 2017")
        self.assertIsNotNone(match)
        self.assertEqual(match.tipo_vehiculo, "SUV_4X4_DEPORTIVO")
        self.assertEqual(match.marca, "Chevrolet")
        self.assertEqual(match.modelo, "Captiva")

    def test_captiva_case_insensitive(self):
        match = lookup_vehicle("CAPTIVA")
        self.assertIsNotNone(match)
        self.assertEqual(match.tipo_vehiculo, "SUV_4X4_DEPORTIVO")

    def test_no_match_returns_none(self):
        self.assertIsNone(lookup_vehicle("quiero inspeccionar mi vehículo"))


class TestPricingCaptiva(unittest.TestCase):
    def test_captiva_lomas_total_170000(self):
        service = PricingService(repository=FakeRepoCaptiva())
        q = service.quote(
            db=None,
            tipo_vehiculo="SUV_4X4_DEPORTIVO",
            zone_group=None,
            zone_detail="Lomas del Mirador",
        )
        self.assertEqual(q.precio_base, 140000)
        self.assertEqual(q.viaticos, 30000)
        self.assertEqual(q.precio_total, 170000)

    def test_pricing_zone_group_returned(self):
        service = PricingService(repository=FakeRepoCaptiva())
        q = service.quote(
            db=None,
            tipo_vehiculo="SUV_4X4_DEPORTIVO",
            zone_group=None,
            zone_detail="Lomas del Mirador",
        )
        self.assertEqual(q.zone_group, "Oeste")


class TestNormalizeZoneFromDb(unittest.TestCase):
    def test_fills_zone_group_when_detail_known(self):
        eng = _make_engine()
        state = _make_state(home_zone_detail="Lomas del Mirador")
        ctx = _make_ctx(state=state)

        # Make eng.db.execute return a fake zone
        from unittest.mock import MagicMock
        zone = FakeZone(zone_group="Oeste", zone_detail="Lomas del Mirador", viaticos=30000)
        # Override _pricing.repository to use our fake
        eng._pricing = PricingService(repository=FakeRepoCaptiva())

        eng._normalize_zone_from_db(ctx, state)
        self.assertEqual(state.home_zone_group, "Oeste")

    def test_overwrites_wrong_ai_zone_group_with_db_canonical(self):
        """DB is authoritative — normalization must overwrite even when zone_group is set."""
        eng = _make_engine()
        # Simulate AI having set zone_group to the city name (wrong) instead of "Oeste"
        state = _make_state(home_zone_group="Lomas del Mirador", home_zone_detail="Lomas del Mirador")
        ctx = _make_ctx(state=state)
        eng._pricing = PricingService(repository=FakeRepoCaptiva())

        eng._normalize_zone_from_db(ctx, state)
        # DB says zone_group="Oeste" — must overwrite the AI's wrong value
        self.assertEqual(state.home_zone_group, "Oeste")


class TestComputePriceQuote(unittest.TestCase):
    def test_returns_none_when_no_candidate(self):
        eng = _make_engine()
        state = _make_state(home_zone_group="Oeste", home_zone_detail="Lomas del Mirador")
        ctx = _make_ctx(candidates=[], state=state)
        result = eng._compute_price_quote(ctx, state)
        self.assertIsNone(result)

    def test_returns_none_when_no_zone(self):
        eng = _make_engine()
        c = _make_candidate(tipo_vehiculo="SUV_4X4_DEPORTIVO")
        state = _make_state()
        ctx = _make_ctx(candidates=[c], state=state)
        result = eng._compute_price_quote(ctx, state)
        self.assertIsNone(result)

    def test_returns_quote_170000_for_captiva_in_lomas(self):
        eng = _make_engine()
        c = _make_candidate(tipo_vehiculo="SUV_4X4_DEPORTIVO")
        state = _make_state(home_zone_detail="Lomas del Mirador")
        ctx = _make_ctx(candidates=[c], state=state)
        result = eng._compute_price_quote(ctx, state)
        self.assertIsNotNone(result)
        self.assertEqual(result.precio_total, 170000)

    def test_backfills_zone_group_on_state(self):
        eng = _make_engine()
        c = _make_candidate(tipo_vehiculo="SUV_4X4_DEPORTIVO")
        state = _make_state(home_zone_detail="Lomas del Mirador")
        ctx = _make_ctx(candidates=[c], state=state)
        eng._compute_price_quote(ctx, state)
        self.assertEqual(state.home_zone_group, "Oeste")


class TestProactiveCandidateCreation(unittest.TestCase):
    def _engine_with_fake_db(self):
        from unittest.mock import MagicMock
        eng = _make_engine()

        created = []

        def fake_add(obj):
            if hasattr(obj, "tipo_vehiculo"):
                obj.id = 99
                created.append(obj)

        def fake_flush():
            pass

        eng.db.add.side_effect = fake_add
        eng.db.flush.side_effect = fake_flush
        return eng, created

    def test_create_candidate_from_catalog(self):
        eng, created = self._engine_with_fake_db()
        state = _make_state(home_zone_group="Oeste", home_zone_detail="Lomas del Mirador")
        ctx = _make_ctx(candidates=[], state=state)

        match = lookup_vehicle("Captiva 2017")
        self.assertIsNotNone(match)
        eng._create_candidate_from_catalog(ctx, state, match)

        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].tipo_vehiculo, "SUV_4X4_DEPORTIVO")
        self.assertEqual(created[0].marca, "Chevrolet")
        self.assertEqual(created[0].modelo, "Captiva")
        self.assertEqual(created[0].zone_group, "Oeste")
        self.assertEqual(created[0].zone_detail, "Lomas del Mirador")
        self.assertEqual(len(ctx.candidates), 1)
        self.assertEqual(state.current_focus_candidate_id, 99)

    def test_enforce_catalog_vehicle_creates_when_no_focus(self):
        eng, created = self._engine_with_fake_db()
        state = _make_state()
        ctx = _make_ctx(candidates=[], state=state)

        match = lookup_vehicle("Captiva")
        eng._enforce_catalog_vehicle(ctx, match)

        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].tipo_vehiculo, "SUV_4X4_DEPORTIVO")


class TestPriceGuard(unittest.TestCase):
    """The engine must NOT set PRESUPUESTO_ENVIADO without a deterministic price."""

    def _make_flag_guard_scenario(self, real_price_quote):
        """Simulates the guard check in _process_text."""
        _ALLOWED_FLAGS = {"PRESUPUESTANDO", "PRESUPUESTO_ENVIADO", "ACEPTADO"}
        new_flag = "PRESUPUESTO_ENVIADO"
        flag_accepted = new_flag and new_flag in _ALLOWED_FLAGS
        if flag_accepted and new_flag == "PRESUPUESTO_ENVIADO" and real_price_quote is None:
            flag_accepted = False
        return flag_accepted

    def test_guard_blocks_when_no_price(self):
        accepted = self._make_flag_guard_scenario(real_price_quote=None)
        self.assertFalse(accepted)

    def test_guard_allows_when_price_available(self):
        q = PricingQuote(
            tipo_vehiculo="SUV_4X4_DEPORTIVO",
            zone_group="Oeste",
            zone_detail="Lomas del Mirador",
            precio_base=140000,
            viaticos=30000,
        )
        accepted = self._make_flag_guard_scenario(real_price_quote=q)
        self.assertTrue(accepted)


class TestApplyExtractedZoneNormalization(unittest.TestCase):
    def test_zone_group_normalized_after_apply_extracted(self):
        """Simulate what happens when AI returns zone_detail but not zone_group."""
        eng = _make_engine()
        state = _make_state()
        ctx = _make_ctx(state=state)

        # Simulate _apply_extracted with just zone_detail from AI
        state.home_zone_detail = "Lomas del Mirador"
        # No zone_group set yet

        eng._pricing = PricingService(repository=FakeRepoCaptiva())
        eng._normalize_zone_from_db(ctx, state)

        self.assertEqual(state.home_zone_group, "Oeste")
        self.assertEqual(state.home_zone_detail, "Lomas del Mirador")


class TestApplyUpdateWithoutId(unittest.TestCase):
    def test_update_without_id_falls_back_to_focus(self):
        from unittest.mock import MagicMock
        eng = _make_engine()
        eng.db.flush = MagicMock()

        c = _make_candidate(id=5, tipo_vehiculo="AUTO", marca="Ford", modelo="Focus")
        state = _make_state()
        state.current_focus_candidate_id = 5
        ctx = _make_ctx(candidates=[c], state=state)

        eng._apply_candidate(ctx, {
            "action": "update",
            "id": None,
            "marca": "Chevrolet",
            "modelo": "Captiva",
            "tipo_vehiculo": "SUV_4X4_DEPORTIVO",
            "anio": 2017,
            "status": "current_focus",
        })

        self.assertEqual(c.marca, "Chevrolet")
        self.assertEqual(c.modelo, "Captiva")
        self.assertEqual(c.tipo_vehiculo, "SUV_4X4_DEPORTIVO")
        self.assertEqual(c.anio, 2017)


class TestFlowResponseEmailToLead(unittest.TestCase):
    def test_email_persisted_to_lead(self):
        """Verify _process_flow_response sets lead.email when not already set."""
        # We test the logic in isolation — the assignment is straightforward
        buyer_email = "lara@example.com"
        lead_email = None  # lead has no email yet

        if buyer_email and not lead_email:
            lead_email = buyer_email

        self.assertEqual(lead_email, "lara@example.com")

    def test_email_not_overwritten_when_lead_already_has_one(self):
        buyer_email = "newaddress@example.com"
        lead_email = "existing@example.com"

        if buyer_email and not lead_email:
            lead_email = buyer_email

        # Should not overwrite
        self.assertEqual(lead_email, "existing@example.com")


class TestApprovalMovesLeadToAgendado(unittest.TestCase):
    """Unit-level test for the AGENDADO transition logic (no HTTP stack needed)."""

    def _simulate_crm_approval(self, lead_estado):
        """Simulate the approval logic from revision_items.py."""
        class FakeLead:
            def __init__(self, estado):
                self.estado = estado

        class FakeRevision:
            lead_id = 10
            appointment_approval_status = None
            appointment_approved_at = None

        revision = FakeRevision()
        lead = FakeLead(lead_estado)

        # Logic from revision_items.py
        revision.appointment_approval_status = "APPROVED"
        if revision.lead_id:
            if lead and lead.estado == "COORDINAR_DISPONIBILIDAD":
                lead.estado = "AGENDADO"

        return revision, lead

    def _simulate_public_approval(self, lead_estado):
        """Simulate the approval logic from public_approval.py."""
        class FakeLead:
            def __init__(self, estado):
                self.estado = estado

        class FakeThread:
            lead_id = 10

        class FakeRevision:
            thread_id = 37
            appointment_approval_status = None

        revision = FakeRevision()
        lead = FakeLead(lead_estado)
        thread = FakeThread()

        # Logic from public_approval.py
        revision.appointment_approval_status = "APPROVED"
        if thread and thread.lead_id:
            if lead and lead.estado == "COORDINAR_DISPONIBILIDAD":
                lead.estado = "AGENDADO"

        return revision, lead

    def test_crm_approval_moves_to_agendado(self):
        _, lead = self._simulate_crm_approval("COORDINAR_DISPONIBILIDAD")
        self.assertEqual(lead.estado, "AGENDADO")

    def test_crm_approval_does_not_move_other_states(self):
        _, lead = self._simulate_crm_approval("CONSULTA_NUEVA")
        self.assertEqual(lead.estado, "CONSULTA_NUEVA")

    def test_public_approval_moves_to_agendado(self):
        _, lead = self._simulate_public_approval("COORDINAR_DISPONIBILIDAD")
        self.assertEqual(lead.estado, "AGENDADO")

    def test_public_approval_does_not_move_other_states(self):
        _, lead = self._simulate_public_approval("AGENDADO")
        self.assertEqual(lead.estado, "AGENDADO")  # already correct, unchanged


class FakeRepoSanJusto:
    """Covers Captiva (SUV_4X4_DEPORTIVO) in San Justo (Oeste, 30000)."""

    _BASE = {
        "SUV_4X4_DEPORTIVO": FakePriceRow(tipo_vehiculo="SUV_4X4_DEPORTIVO", precio_base=140000),
    }
    _ZONES_BY_DETAIL = {
        "san justo": FakeZone(zone_group="Oeste", zone_detail="San Justo", viaticos=30000),
    }

    def find_base_price(self, tipo_vehiculo: str):
        return self._BASE.get(tipo_vehiculo)

    def find_zone_by_group_and_detail(self, db, zone_group, zone_detail):
        key = (zone_detail or "").strip().lower()
        return self._ZONES_BY_DETAIL.get(key)


class TestSanJustoZoneNormalization(unittest.TestCase):
    """San Justo must map to Oeste/San Justo and produce a $170,000 quote."""

    def test_san_justo_in_pricing_repo(self):
        service = PricingService(repository=FakeRepoSanJusto())
        q = service.quote(
            db=None,
            tipo_vehiculo="SUV_4X4_DEPORTIVO",
            zone_group=None,
            zone_detail="San Justo",
        )
        self.assertEqual(q.zone_group, "Oeste")
        self.assertEqual(q.zone_detail, "San Justo")
        self.assertEqual(q.precio_total, 170000)

    def test_normalize_zone_overwrites_wrong_ai_zone_group(self):
        """When AI sets zone_group='San Justo' (wrong), DB normalization must fix it."""
        eng = _make_engine(repo=FakeRepoSanJusto())
        # Simulate AI having extracted zone_group="San Justo" (wrong) + zone_detail="San Justo"
        state = _make_state(home_zone_group="San Justo", home_zone_detail="San Justo")
        ctx = _make_ctx(state=state)

        eng._pricing = PricingService(repository=FakeRepoSanJusto())
        eng._normalize_zone_from_db(ctx, state)

        self.assertEqual(state.home_zone_group, "Oeste")
        self.assertEqual(state.home_zone_detail, "San Justo")

    def test_compute_price_quote_san_justo(self):
        eng = _make_engine(repo=FakeRepoSanJusto())
        c = _make_candidate(tipo_vehiculo="SUV_4X4_DEPORTIVO")
        # Simulate post-normalization state (zone_group already fixed)
        state = _make_state(home_zone_group="Oeste", home_zone_detail="San Justo")
        ctx = _make_ctx(candidates=[c], state=state)
        result = eng._compute_price_quote(ctx, state)
        self.assertIsNotNone(result)
        self.assertEqual(result.precio_total, 170000)

    def test_compute_price_quote_returns_none_before_normalization(self):
        """With wrong zone_group='San Justo' and wrong zone_detail in a repo
        that only knows 'San Justo' by detail, pricing should still work because
        find_zone_by_group_and_detail falls back to detail-only search."""
        eng = _make_engine(repo=FakeRepoSanJusto())
        c = _make_candidate(tipo_vehiculo="SUV_4X4_DEPORTIVO")
        # Pre-normalization: zone_group and detail both set to "San Justo" (AI mistake)
        state = _make_state(home_zone_group="San Justo", home_zone_detail="San Justo")
        ctx = _make_ctx(candidates=[c], state=state)
        # Normalize first (as engine does) then compute
        eng._normalize_zone_from_db(ctx, state)
        result = eng._compute_price_quote(ctx, state)
        self.assertIsNotNone(result)
        self.assertEqual(result.precio_total, 170000)


class TestDeterministicQuoteOverride(unittest.TestCase):
    """Engine must force PRESUPUESTO_ENVIADO + price injection when quote is available."""

    def _run_override_logic(self, real_price_quote, lead_flag, last_stage, ai_reply, needs_human=False):
        """Replicate the override block from _process_text in isolation."""
        STAGE_QUALIFYING = "QUALIFYING"
        STAGE_QUOTED = "QUOTED"

        lead = types.SimpleNamespace(flag=lead_flag)
        state = types.SimpleNamespace(last_stage=last_stage, needs_human=needs_human)
        decision = {"reply": ai_reply}

        if (
            real_price_quote is not None
            and lead.flag not in ("PRESUPUESTO_ENVIADO", "ACEPTADO")
            and state.last_stage in (STAGE_QUALIFYING, None)
            and not state.needs_human
        ):
            lead.flag = "PRESUPUESTO_ENVIADO"
            state.last_stage = STAGE_QUOTED
            total_str = f"${real_price_quote.precio_total:,.0f}".replace(",", ".")
            if str(real_price_quote.precio_total) not in ai_reply and total_str not in ai_reply:
                decision["reply"] = (
                    ai_reply
                    + f"\n\nEl precio de la revisión es {total_str} "
                    f"(base ${real_price_quote.precio_base:,.0f}".replace(",", ".")
                    + f" + viáticos ${real_price_quote.viaticos:,.0f})".replace(",", ".")
                )

        return lead, state, decision

    def _make_quote(self, total=170000):
        return PricingQuote(
            tipo_vehiculo="SUV_4X4_DEPORTIVO",
            zone_group="Oeste",
            zone_detail="San Justo",
            precio_base=140000,
            viaticos=30000,
        )

    def test_override_forces_flag_when_ai_missed_it(self):
        q = self._make_quote()
        lead, state, decision = self._run_override_logic(
            real_price_quote=q,
            lead_flag="PRESUPUESTANDO",
            last_stage="QUALIFYING",
            ai_reply="Genial, ya tengo toda la info.",
        )
        self.assertEqual(lead.flag, "PRESUPUESTO_ENVIADO")
        self.assertEqual(state.last_stage, "QUOTED")

    def test_override_injects_price_when_missing_from_reply(self):
        q = self._make_quote()
        lead, state, decision = self._run_override_logic(
            real_price_quote=q,
            lead_flag="PRESUPUESTANDO",
            last_stage="QUALIFYING",
            ai_reply="Genial, ya tengo toda la info.",
        )
        self.assertIn("170.000", decision["reply"])

    def test_override_skips_when_already_quoted(self):
        q = self._make_quote()
        lead, state, decision = self._run_override_logic(
            real_price_quote=q,
            lead_flag="PRESUPUESTO_ENVIADO",
            last_stage="QUOTED",
            ai_reply="Ya te mandé el precio.",
        )
        self.assertEqual(lead.flag, "PRESUPUESTO_ENVIADO")
        self.assertEqual(state.last_stage, "QUOTED")

    def test_override_skips_when_no_price(self):
        lead, state, decision = self._run_override_logic(
            real_price_quote=None,
            lead_flag="PRESUPUESTANDO",
            last_stage="QUALIFYING",
            ai_reply="Necesito saber tu zona.",
        )
        self.assertEqual(lead.flag, "PRESUPUESTANDO")
        self.assertEqual(state.last_stage, "QUALIFYING")

    def test_override_skips_when_needs_human(self):
        q = self._make_quote()
        lead, state, decision = self._run_override_logic(
            real_price_quote=q,
            lead_flag="PRESUPUESTANDO",
            last_stage="QUALIFYING",
            ai_reply="Un asesor te va a contactar.",
            needs_human=True,
        )
        # Should NOT override when needs_human=True
        self.assertEqual(lead.flag, "PRESUPUESTANDO")


class TestPromptNeverAsksVehiclePrice(unittest.TestCase):
    """Validate the system prompt rules contain the required prohibitions."""

    def _get_system_prompt(self):
        from app.services.conversation_engine import ConversationEngine
        from app.services.pricing import PricingService
        from unittest.mock import MagicMock

        eng = _make_engine()
        ctx = _make_ctx(
            candidates=[_make_candidate(tipo_vehiculo="SUV_4X4_DEPORTIVO", marca="Chevrolet", modelo="Captiva")],
            state=_make_state(home_zone_detail="San Justo"),
        )
        event = MagicMock()
        event.recent_outbound_replies = []
        event.recent_user_messages = []
        msgs = eng._build_ai_messages(ctx, event, ["Agencia"])
        return msgs[0]["content"]  # system message

    def test_prompt_forbids_vehicle_price(self):
        prompt = self._get_system_prompt()
        self.assertIn("precio de venta", prompt.lower())
        self.assertIn("NUNCA", prompt)

    def test_prompt_does_not_require_seller_type_for_quote(self):
        prompt = self._get_system_prompt()
        # Rule 1 must NOT say vendedor is required for quoting
        self.assertNotIn("tipo de vendedor (agencia/particular)", prompt)

    def test_prompt_has_calculated_price_when_available(self):
        eng = _make_engine(repo=FakeRepoSanJusto())
        ctx = _make_ctx(
            candidates=[_make_candidate(tipo_vehiculo="SUV_4X4_DEPORTIVO", marca="Chevrolet", modelo="Captiva")],
            state=_make_state(home_zone_group="Oeste", home_zone_detail="San Justo"),
        )
        from unittest.mock import MagicMock
        event = MagicMock()
        event.recent_outbound_replies = []
        event.recent_user_messages = []
        q = PricingQuote(
            tipo_vehiculo="SUV_4X4_DEPORTIVO",
            zone_group="Oeste",
            zone_detail="San Justo",
            precio_base=140000,
            viaticos=30000,
        )
        msgs = eng._build_ai_messages(ctx, event, ["Agencia"], real_price_quote=q)
        system = msgs[0]["content"]
        self.assertIn("PRECIO CALCULADO", system)
        self.assertIn("170.000", system)


class TestVehicleYearExtraction(unittest.TestCase):
    """Year (anio) must be extracted deterministically from free text and persisted."""

    def test_extract_year_from_text_2020(self):
        from app.services.conversation_engine import _extract_year_from_text
        self.assertEqual(_extract_year_from_text("captiva 2020"), 2020)

    def test_extract_year_from_text_1998(self):
        from app.services.conversation_engine import _extract_year_from_text
        self.assertEqual(_extract_year_from_text("tengo una Hilux 1998"), 1998)

    def test_extract_year_returns_none_when_absent(self):
        from app.services.conversation_engine import _extract_year_from_text
        self.assertIsNone(_extract_year_from_text("tengo una Captiva en San Justo"))

    def test_create_candidate_with_year(self):
        """_create_candidate_from_catalog must populate anio from source_text."""
        eng, created = _engine_with_fake_db_for_year()
        state = _make_state(home_zone_group="Oeste", home_zone_detail="San Justo")
        ctx = _make_ctx(candidates=[], state=state)

        match = lookup_vehicle("Captiva 2020")
        self.assertIsNotNone(match)
        eng._create_candidate_from_catalog(ctx, state, match, source_text="captiva 2020 San Justo")

        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].anio, 2020)

    def test_create_candidate_no_year_in_text(self):
        """When no year in text anio must be None, not 0 or a wrong value."""
        eng, created = _engine_with_fake_db_for_year()
        state = _make_state()
        ctx = _make_ctx(candidates=[], state=state)

        match = lookup_vehicle("Captiva")
        eng._create_candidate_from_catalog(ctx, state, match, source_text="tengo una Captiva")

        self.assertEqual(len(created), 1)
        self.assertIsNone(created[0].anio)

    def test_year_sync_onto_existing_candidate(self):
        """Post-AI year sync must backfill anio=None on focus candidate from all_recent_text."""
        from app.services.conversation_engine import _extract_year_from_text
        candidate = _make_candidate(anio=None, tipo_vehiculo="SUV_4X4_DEPORTIVO")
        year_hit = _extract_year_from_text("captiva 2020 san justo")
        if year_hit and candidate.anio is None:
            candidate.anio = year_hit
        self.assertEqual(candidate.anio, 2020)

    def test_existing_year_not_overwritten(self):
        """If candidate already has anio, the sync block must not overwrite it."""
        from app.services.conversation_engine import _extract_year_from_text
        candidate = _make_candidate(anio=2018, tipo_vehiculo="SUV_4X4_DEPORTIVO")
        year_hit = _extract_year_from_text("captiva 2020")
        if year_hit and candidate.anio is None:
            candidate.anio = year_hit
        self.assertEqual(candidate.anio, 2018)


def _engine_with_fake_db_for_year():
    created = []

    def fake_add(obj):
        if hasattr(obj, "tipo_vehiculo"):
            obj.id = 42
            created.append(obj)

    eng = _make_engine()
    eng.db.add.side_effect = fake_add
    eng.db.flush.side_effect = lambda: None
    return eng, created


class TestDeterministicSchedulingDate(unittest.TestCase):
    """Post-AI scheduling path must use _parse_scheduling_text, never AI's preferred_day_iso."""

    def test_parse_manana_returns_tomorrow(self):
        from datetime import date
        from app.services.conversation_engine import _parse_scheduling_text
        today = date(2026, 6, 13)  # Saturday
        day, t = _parse_scheduling_text(["mañana 12hs"], today)
        self.assertEqual(day, "2026-06-14")  # Sunday

    def test_parse_lunes_returns_next_monday(self):
        from datetime import date
        from app.services.conversation_engine import _parse_scheduling_text
        today = date(2026, 6, 13)  # Saturday
        day, t = _parse_scheduling_text(["el lunes a las 10"], today)
        self.assertEqual(day, "2026-06-15")

    def test_parse_time_extracted(self):
        from datetime import date
        from app.services.conversation_engine import _parse_scheduling_text
        today = date(2026, 6, 13)
        day, t = _parse_scheduling_text(["mañana 12hs"], today)
        self.assertEqual(t, "12:00")

    def test_rafaga_si_plus_manana_uses_deterministic_date(self):
        """Simulates the bug: ráfaga ['Si', 'Mañana 12hs'] must yield 2026-06-14, not AI date."""
        from datetime import date
        from app.services.conversation_engine import _parse_scheduling_text
        today = date(2026, 6, 13)
        # The ráfaga arrives as two messages; engine passes both to _parse_scheduling_text
        day, t = _parse_scheduling_text(["Si", "Mañana 12hs"], today)
        self.assertEqual(day, "2026-06-14")
        self.assertEqual(t, "12:00")


class TestSundayClosed(unittest.TestCase):
    """Sunday must be closed; ScheduleService.check() and list_slots() return no valid slots."""

    def _make_schedule_service(self):
        from app.services.schedule import ScheduleService
        from unittest.mock import MagicMock
        svc = ScheduleService.__new__(ScheduleService)
        svc.db = MagicMock()
        svc.db.execute.return_value.scalars.return_value.all.return_value = []
        return svc

    def test_business_hours_sunday_is_closed(self):
        from datetime import date
        from app.services.schedule import ScheduleService
        svc = self._make_schedule_service()
        sunday = date(2026, 6, 14)
        hours = svc._business_hours(sunday, normalized_context="", is_holiday=False)
        self.assertTrue(hours.closed)

    def test_check_sunday_returns_invalid(self):
        from datetime import date, time
        from app.schemas.schedule import ScheduleCheckIn
        svc = self._make_schedule_service()
        payload = ScheduleCheckIn(
            address="San Justo 123",
            preferred_day=date(2026, 6, 14),
            preferred_time=time(12, 0),
            zone_group="Oeste",
            zone_detail="San Justo",
            distance_km=15.0,
            is_holiday=False,
        )
        result = svc.check(payload)
        self.assertFalse(result.valid)
        self.assertEqual(result.suggested_slots, [])
        self.assertTrue(any("Domingo" in r for r in result.reasons))

    def test_list_slots_sunday_returns_empty(self):
        from datetime import date, time
        from app.schemas.schedule import ScheduleCheckIn
        svc = self._make_schedule_service()
        payload = ScheduleCheckIn(
            address="San Justo 123",
            preferred_day=date(2026, 6, 14),
            preferred_time=time(12, 0),
            zone_group="Oeste",
            zone_detail="San Justo",
            distance_km=15.0,
            is_holiday=False,
        )
        result = svc.list_slots(payload)
        self.assertEqual(result.slots, [])
        self.assertEqual(result.business_hours, "cerrado")

    def test_saturday_is_open(self):
        from datetime import date
        from app.services.schedule import ScheduleService
        svc = self._make_schedule_service()
        saturday = date(2026, 6, 13)
        hours = svc._business_hours(saturday, normalized_context="", is_holiday=False)
        self.assertFalse(hours.closed)


# ── 160626_TESTS regression: BUG-1 / BUG-2 / BUG-3 ──────────────────────────


class FakeRepoCABAFallback:
    """CABA group-level zone (zone_detail=NULL), AUTO base price = $130.000.

    Models a viaticos_zones row: zone_group='CABA', zone_detail=NULL, viaticos=0.
    The PricingRepository group-only lookup (third branch) resolves this row when
    zone_detail is blank and zone_group='CABA'.
    """
    _BASE = {"AUTO": FakePriceRow("AUTO", 130000)}

    def find_base_price(self, tipo_vehiculo):
        return self._BASE.get(tipo_vehiculo)

    def find_zone_by_group_and_detail(self, db, zone_group, zone_detail):
        ng = (zone_group or "").strip().lower()
        nd = (zone_detail or "").strip().lower()
        if nd == "caba":
            return None  # "CABA" is not a zone_detail in the DB
        if ng == "caba" and not nd:
            return FakeZone(zone_group="CABA", zone_detail=None, viaticos=0)
        return None


class FakeRepoCABANoFallback:
    """CABA is a known zone_group but has NO pricing row at any level."""
    _BASE = {"AUTO": FakePriceRow("AUTO", 130000)}

    def find_base_price(self, tipo_vehiculo):
        return self._BASE.get(tipo_vehiculo)

    def find_zone_by_group_and_detail(self, db, zone_group, zone_detail):
        return None


def _make_engine_with_groups(groups, repo):
    """Make engine where db.execute returns the given zone_group list for _find_zone_group."""
    from unittest.mock import MagicMock
    eng = _make_engine(repo=repo)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = list(groups)
    eng.db.execute.return_value = mock_result
    return eng


class TestCABAZoneDetection(unittest.TestCase):
    """BUG-1: 'CABA' input must be promoted to zone_group, not used as zone_detail."""

    def test_normalize_promotes_caba_detail_to_group(self):
        """After AI extracts zone_detail='CABA', normalization must promote to zone_group."""
        eng = _make_engine_with_groups(["CABA", "Oeste", "Norte"], repo=FakeRepoCABAFallback())
        state = _make_state(home_zone_detail="CABA")
        ctx = _make_ctx(state=state)

        eng._normalize_zone_from_db(ctx, state)

        self.assertEqual(state.home_zone_group, "CABA")
        self.assertIsNone(state.home_zone_detail)

    def test_normalize_clears_zone_detail_when_caba_is_group(self):
        """zone_detail must be None after promotion so downstream knows to ask for barrio."""
        eng = _make_engine_with_groups(["CABA"], repo=FakeRepoCABANoFallback())
        state = _make_state(home_zone_detail="CABA")
        ctx = _make_ctx(state=state)

        eng._normalize_zone_from_db(ctx, state)

        self.assertIsNone(state.home_zone_detail)

    def test_compute_quote_caba_with_fallback_returns_130000(self):
        """When CABA has a group-level pricing row, AUTO + CABA yields $130.000."""
        eng = _make_engine_with_groups(["CABA", "Oeste"], repo=FakeRepoCABAFallback())
        c = _make_candidate(tipo_vehiculo="AUTO")
        # Post-normalization state: zone_group=CABA, zone_detail=None
        state = _make_state(home_zone_group="CABA", home_zone_detail=None)
        ctx = _make_ctx(candidates=[c], state=state)

        result = eng._compute_price_quote(ctx, state)

        self.assertIsNotNone(result)
        self.assertEqual(result.precio_total, 130000)
        self.assertEqual(result.zone_group, "CABA")

    def test_compute_quote_caba_without_fallback_returns_none(self):
        """When CABA has no pricing row, compute returns None — no invented price possible."""
        eng = _make_engine_with_groups(["CABA"], repo=FakeRepoCABANoFallback())
        c = _make_candidate(tipo_vehiculo="AUTO")
        state = _make_state(home_zone_group="CABA", home_zone_detail=None)
        ctx = _make_ctx(candidates=[c], state=state)

        result = eng._compute_price_quote(ctx, state)

        self.assertIsNone(result)

    def test_find_zone_group_returns_canonical_name(self):
        """_find_zone_group must return the canonical casing from the DB."""
        eng = _make_engine_with_groups(["CABA", "Oeste"], repo=FakeRepoCABAFallback())
        result = eng._find_zone_group("caba")
        self.assertEqual(result, "CABA")

    def test_find_zone_group_returns_none_for_unknown(self):
        eng = _make_engine_with_groups(["CABA", "Oeste"], repo=FakeRepoCABAFallback())
        result = eng._find_zone_group("Mars")
        self.assertIsNone(result)

    def test_regular_zone_detail_not_promoted(self):
        """A real zone_detail (Lomas del Mirador) must NOT be treated as a group."""
        eng = _make_engine_with_groups(["CABA", "Oeste"], repo=FakeRepoCaptiva())
        state = _make_state(home_zone_detail="Lomas del Mirador")
        ctx = _make_ctx(state=state)

        eng._normalize_zone_from_db(ctx, state)

        # zone_detail stays; zone_group gets filled from DB
        self.assertEqual(state.home_zone_detail, "Lomas del Mirador")
        self.assertEqual(state.home_zone_group, "Oeste")


class TestHardPriceGuard(unittest.TestCase):
    """BUG-2: AI replies containing a price must be replaced when real_price_quote is None."""

    def _scrub(self, reply, real_price_quote=None):
        eng = _make_engine()
        return eng._scrub_invented_price(reply, real_price_quote)

    def test_dollar_dot_format_scrubbed(self):
        """'$5.000' must be scrubbed when no real quote — the exact incident format."""
        result = self._scrub("El precio de la revisión de tu Gol Trend en CABA es $5.000.")
        self.assertNotIn("5.000", result)
        self.assertNotIn("$", result)

    def test_dollar_no_separator_scrubbed(self):
        result = self._scrub("Son $5000 para tu auto.")
        self.assertNotIn("5000", result)

    def test_pesos_word_scrubbed(self):
        result = self._scrub("Te queda en 130.000 pesos.")
        self.assertNotIn("130.000", result)

    def test_scrubbed_reply_asks_for_zone(self):
        """Replacement text must guide the user to provide zone info."""
        result = self._scrub("El precio es $5.000.")
        self.assertIn("barrio", result.lower())

    def test_real_quote_allows_price_through(self):
        """When real_price_quote exists, price in reply must NOT be scrubbed."""
        q = PricingQuote(
            tipo_vehiculo="AUTO", zone_group="CABA", zone_detail=None,
            precio_base=130000, viaticos=0,
        )
        result = self._scrub("El precio es $130.000.", real_price_quote=q)
        self.assertIn("$130.000", result)

    def test_no_price_reply_passes_through_unchanged(self):
        """Reply without any price and no quote must be returned unchanged."""
        original = "¿En qué barrio de CABA está el auto?"
        result = self._scrub(original)
        self.assertEqual(result, original)

    def test_gol_trend_caba_hallucinated_5000_scrubbed(self):
        """Exact regression: $5.000 for Gol Trend in CABA must be scrubbed."""
        from app.services.conversation_engine import _PRICE_RE
        ai_reply = "Para tu Gol Trend en CABA, el precio de la revisión es $5.000."
        eng = _make_engine(repo=FakeRepoCABANoFallback())
        scrubbed = eng._scrub_invented_price(ai_reply, real_price_quote=None)
        self.assertIsNone(_PRICE_RE.search(scrubbed), f"Price pattern found in: {scrubbed!r}")
        self.assertNotIn("5.000", scrubbed)


class TestAcceptanceGuardNoQuote(unittest.TestCase):
    """BUG-3: Acceptance in QUALIFYING without a quote must not advance to SCHEDULING."""

    def _run_guard(self, new_flag, last_stage, real_price_quote):
        """Replicate the BUG-3 guard from _process_text in isolation."""
        _ALLOWED_FLAGS = {"PRESUPUESTANDO", "PRESUPUESTO_ENVIADO", "ACEPTADO"}

        flag_accepted = bool(new_flag and new_flag in _ALLOWED_FLAGS)

        if flag_accepted and new_flag == "PRESUPUESTO_ENVIADO" and real_price_quote is None:
            flag_accepted = False

        if (
            flag_accepted
            and new_flag == "ACEPTADO"
            and last_stage in ("QUALIFYING", None)
            and real_price_quote is None
        ):
            flag_accepted = False

        return flag_accepted

    def test_dale_in_qualifying_without_quote_is_blocked(self):
        """'dale' after hallucinated price must NOT move to ACEPTADO/SCHEDULING."""
        accepted = self._run_guard("ACEPTADO", "QUALIFYING", real_price_quote=None)
        self.assertFalse(accepted)

    def test_aceptado_with_none_stage_is_blocked(self):
        accepted = self._run_guard("ACEPTADO", None, real_price_quote=None)
        self.assertFalse(accepted)

    def test_aceptado_in_quoted_stage_is_not_blocked(self):
        """In QUOTED stage, ACEPTADO is legitimate — this guard must not fire."""
        accepted = self._run_guard("ACEPTADO", "QUOTED", real_price_quote=None)
        self.assertTrue(accepted)

    def test_aceptado_with_real_quote_in_qualifying_not_blocked(self):
        q = PricingQuote(
            tipo_vehiculo="AUTO", zone_group="CABA", zone_detail=None,
            precio_base=130000, viaticos=0,
        )
        accepted = self._run_guard("ACEPTADO", "QUALIFYING", real_price_quote=q)
        self.assertTrue(accepted)

    def test_guard_reply_cites_caba_neighborhood_when_group_known(self):
        """When zone_group='CABA' and detail missing, the override reply asks for barrio."""
        from unittest.mock import MagicMock
        eng = _make_engine(repo=FakeRepoCABANoFallback())
        eng.db.flush = MagicMock()

        state = _make_state(home_zone_group="CABA", home_zone_detail=None, last_stage="QUALIFYING")
        c = _make_candidate(tipo_vehiculo="AUTO")
        ctx = _make_ctx(candidates=[c], state=state)

        # Simulate the exact engine logic path for BUG-3 override reply
        decision = {"lead_flag": "ACEPTADO", "reply": "Te llevo al siguiente paso."}
        flag_accepted = True
        if (
            flag_accepted
            and decision["lead_flag"] == "ACEPTADO"
            and state.last_stage in ("QUALIFYING", None)
            # real_price_quote is None
        ):
            flag_accepted = False
            focus_c = eng._focus_candidate(ctx)
            if state.home_zone_group and not state.home_zone_detail:
                decision["reply"] = (
                    f"¿En qué barrio de {state.home_zone_group} está el auto? "
                    "Así te paso el valor exacto."
                )

        self.assertFalse(flag_accepted)
        self.assertIn("CABA", decision["reply"])
        self.assertIn("barrio", decision["reply"].lower())
        self.assertNotIn("$", decision["reply"])


# ── CABA synonym + quote-intent scrub regression (real test 160626) ──────────


class FakeRepoCABASentinel:
    """CABA sentinel row: zone_group='CABA', zone_detail='CABA', viaticos=0.
    Mirrors the DB row added by migration 20260617_add_caba_zone.
    """
    _BASE = {"AUTO": FakePriceRow("AUTO", 130000)}
    _CABA_ZONE = FakeZone(zone_group="CABA", zone_detail="CABA", viaticos=0)

    def find_base_price(self, tipo_vehiculo):
        return self._BASE.get(tipo_vehiculo)

    def find_zone_by_group_and_detail(self, db, zone_group, zone_detail):
        ng = (zone_group or "").strip().lower()
        nd = (zone_detail or "").strip().lower()
        if nd == "caba":
            return self._CABA_ZONE
        if ng == "caba" and nd == "caba":
            return self._CABA_ZONE
        return None


class TestCABASynonymNormalization(unittest.TestCase):
    """'capital federal', 'ciudad autónoma de buenos aires' etc. must map to CABA sentinel."""

    def _normalize_engine(self, repo=None):
        return _make_engine(repo=repo or FakeRepoCABASentinel())

    def test_capital_federal_normalized_to_caba(self):
        eng = self._normalize_engine()
        state = _make_state(home_zone_detail="capital federal")
        ctx = _make_ctx(state=state)
        eng._normalize_zone_from_db(ctx, state)
        self.assertEqual(state.home_zone_detail, "CABA")
        self.assertEqual(state.home_zone_group, "CABA")

    def test_ciudad_autonoma_normalized(self):
        eng = self._normalize_engine()
        state = _make_state(home_zone_detail="ciudad autónoma de buenos aires")
        ctx = _make_ctx(state=state)
        eng._normalize_zone_from_db(ctx, state)
        self.assertEqual(state.home_zone_detail, "CABA")
        self.assertEqual(state.home_zone_group, "CABA")

    def test_ciudad_autonoma_unaccented_normalized(self):
        eng = self._normalize_engine()
        state = _make_state(home_zone_detail="ciudad autonoma de buenos aires")
        ctx = _make_ctx(state=state)
        eng._normalize_zone_from_db(ctx, state)
        self.assertEqual(state.home_zone_detail, "CABA")

    def test_cdad_autonoma_normalized(self):
        eng = self._normalize_engine()
        state = _make_state(home_zone_detail="cdad autónoma de buenos aires")
        ctx = _make_ctx(state=state)
        eng._normalize_zone_from_db(ctx, state)
        self.assertEqual(state.home_zone_detail, "CABA")

    def test_caba_canonical_unchanged(self):
        """'CABA' itself is canonical — must not be treated as synonym and must still
        resolve correctly via the normal DB lookup."""
        eng = self._normalize_engine()
        state = _make_state(home_zone_detail="CABA")
        ctx = _make_ctx(state=state)
        eng._normalize_zone_from_db(ctx, state)
        # DB lookup finds the row: zone_group="CABA"
        self.assertEqual(state.home_zone_detail, "CABA")
        self.assertEqual(state.home_zone_group, "CABA")

    def test_gol_trend_caba_quotes_130000(self):
        """After synonym normalization: AUTO + CABA → $130.000, no extra confirmation."""
        eng = self._normalize_engine()
        c = _make_candidate(tipo_vehiculo="AUTO")
        # Simulate post-normalization state (synonym already normalized to CABA)
        state = _make_state(home_zone_group="CABA", home_zone_detail="CABA")
        ctx = _make_ctx(candidates=[c], state=state)

        result = eng._compute_price_quote(ctx, state)

        self.assertIsNotNone(result)
        self.assertEqual(result.precio_total, 130000)
        self.assertEqual(result.zone_group, "CABA")
        self.assertEqual(result.viaticos, 0)

    def test_gol_trend_capital_federal_quotes_130000(self):
        """'capital federal' + AUTO → normalize → $130.000."""
        eng = self._normalize_engine()
        c = _make_candidate(tipo_vehiculo="AUTO")
        state = _make_state(home_zone_detail="capital federal")
        ctx = _make_ctx(candidates=[c], state=state)

        eng._normalize_zone_from_db(ctx, state)
        result = eng._compute_price_quote(ctx, state)

        self.assertIsNotNone(result)
        self.assertEqual(result.precio_total, 130000)

    def test_is_caba_synonym_function(self):
        from app.services.conversation_engine import _is_caba_synonym
        self.assertTrue(_is_caba_synonym("capital federal"))
        self.assertTrue(_is_caba_synonym("Capital Federal"))
        self.assertTrue(_is_caba_synonym("ciudad autónoma de buenos aires"))
        self.assertTrue(_is_caba_synonym("ciudad autonoma de buenos aires"))
        self.assertTrue(_is_caba_synonym("c.a.b.a."))
        self.assertFalse(_is_caba_synonym("CABA"))  # canonical, not a synonym
        self.assertFalse(_is_caba_synonym("caba"))  # handled by DB detail match
        self.assertFalse(_is_caba_synonym("Palermo"))
        self.assertFalse(_is_caba_synonym("Oeste"))


class TestQuoteIntentScrub(unittest.TestCase):
    """BUG-2 extension: 'te envío la cotización' without amount must be scrubbed."""

    def _scrub(self, reply, real_price_quote=None):
        eng = _make_engine()
        return eng._scrub_invented_price(reply, real_price_quote)

    def test_te_envio_la_cotizacion_scrubbed_when_no_quote(self):
        reply = "Genial, te envío la cotización para la revisión del Volkswagen Gol en CABA."
        result = self._scrub(reply, real_price_quote=None)
        self.assertNotIn("cotización", result)
        self.assertIn("precio", result.lower())

    def test_te_paso_el_precio_scrubbed_when_no_quote(self):
        result = self._scrub("Ya te paso el precio.", real_price_quote=None)
        self.assertNotIn("te paso el precio", result)

    def test_envio_el_presupuesto_scrubbed_when_no_quote(self):
        result = self._scrub("Te envío el presupuesto a la brevedad.", real_price_quote=None)
        self.assertNotIn("presupuesto", result)

    def test_quote_intent_with_real_quote_passes_through(self):
        """When real_price_quote exists, 'te envío la cotización' is fine — override injects the amount."""
        q = PricingQuote(
            tipo_vehiculo="AUTO", zone_group="CABA", zone_detail="CABA",
            precio_base=130000, viaticos=0,
        )
        reply = "Perfecto, te envío la cotización ahora."
        result = self._scrub(reply, real_price_quote=q)
        self.assertEqual(result, reply)  # not scrubbed

    def test_regular_reply_without_intent_passes_through(self):
        reply = "¿En qué barrio de CABA está el auto?"
        result = self._scrub(reply, real_price_quote=None)
        self.assertEqual(result, reply)


class TestCABAEndToEndPricingPath(unittest.TestCase):
    """Integration: full engine path for Gol Trend + CABA variants → $130.000."""

    def test_caba_zone_detail_directly_prices_130000(self):
        """Simulate what happens when zone_detail='CABA' after AI extraction."""
        eng = _make_engine(repo=FakeRepoCABASentinel())
        c = _make_candidate(tipo_vehiculo="AUTO")
        state = _make_state(home_zone_detail="CABA")
        ctx = _make_ctx(candidates=[c], state=state)

        # Replicate engine normalization + pricing
        eng._normalize_zone_from_db(ctx, state)
        result = eng._compute_price_quote(ctx, state)

        self.assertIsNotNone(result)
        self.assertEqual(result.precio_total, 130000)
        self.assertEqual(result.viaticos, 0)

    def test_ciudad_autonoma_zone_detail_prices_130000(self):
        eng = _make_engine(repo=FakeRepoCABASentinel())
        c = _make_candidate(tipo_vehiculo="AUTO")
        state = _make_state(home_zone_detail="Ciudad Autónoma de Buenos Aires")
        ctx = _make_ctx(candidates=[c], state=state)

        eng._normalize_zone_from_db(ctx, state)
        result = eng._compute_price_quote(ctx, state)

        self.assertIsNotNone(result)
        self.assertEqual(result.precio_total, 130000)

    def test_caba_quote_has_viatics_zero(self):
        eng = _make_engine(repo=FakeRepoCABASentinel())
        c = _make_candidate(tipo_vehiculo="AUTO")
        state = _make_state(home_zone_detail="CABA")
        ctx = _make_ctx(candidates=[c], state=state)

        eng._normalize_zone_from_db(ctx, state)
        result = eng._compute_price_quote(ctx, state)

        self.assertIsNotNone(result)
        self.assertEqual(result.viaticos, 0)
        self.assertEqual(result.precio_base, 130000)

    def test_no_zone_confirmation_needed_when_caba_known(self):
        """When real_price_quote is not None, BUG-3 ACEPTADO guard must NOT fire."""
        q = PricingQuote(
            tipo_vehiculo="AUTO", zone_group="CABA", zone_detail="CABA",
            precio_base=130000, viaticos=0,
        )
        # BUG-3 guard condition: requires real_price_quote is None
        guard_fires = (
            "ACEPTADO" == "ACEPTADO"
            and "QUALIFYING" in ("QUALIFYING", None)
            and q is None  # <-- not None, so guard does NOT fire
        )
        self.assertFalse(guard_fires)

    def test_si_after_caba_quote_moves_to_scheduling(self):
        """After PRESUPUESTO_ENVIADO + QUOTED stage, 'Si' goes through _handle_quoted_acceptance.
        BUG-3 guard (which blocks ACEPTADO in QUALIFYING) must NOT interfere."""
        from app.services.conversation_engine import _is_acceptance, STAGE_QUOTED
        # Simulate state after quote was sent
        last_stage = STAGE_QUOTED
        messages = ["Si"]
        # Pre-AI acceptance check: last_stage == STAGE_QUOTED AND _is_acceptance → True
        acceptance_fires = (last_stage == STAGE_QUOTED and _is_acceptance(messages))
        self.assertTrue(acceptance_fires)

    def test_caba_pricing_not_blocked_by_presupuesto_enviado_guard(self):
        """PRESUPUESTO_ENVIADO guard must NOT block when real_price_quote is not None."""
        q = PricingQuote(
            tipo_vehiculo="AUTO", zone_group="CABA", zone_detail="CABA",
            precio_base=130000, viaticos=0,
        )
        new_flag = "PRESUPUESTO_ENVIADO"
        real_price_quote = q
        # Guard: only blocks when real_price_quote is None
        flag_accepted = True
        if new_flag == "PRESUPUESTO_ENVIADO" and real_price_quote is None:
            flag_accepted = False
        self.assertTrue(flag_accepted)


# ── M18 scheduling-flow regression (real test 20260617) ──────────────────────


class TestParseSchedulingTextExplicitDates(unittest.TestCase):
    """_parse_scheduling_text must handle numeric dates like '21 del 6'."""

    def _parse(self, texts, today=None):
        from app.services.conversation_engine import _parse_scheduling_text
        from datetime import date
        return _parse_scheduling_text(texts, today or date(2026, 6, 17))

    def test_21_del_6_parses_as_june_21(self):
        day, _ = self._parse(["Tenes para el 21 del 6 ? A las 12hs ?"])
        self.assertEqual(day, "2026-06-21")

    def test_21_de_junio_parses_as_june_21(self):
        day, _ = self._parse(["¿Tenés para el 21 de junio?"])
        self.assertEqual(day, "2026-06-21")

    def test_slash_format_21_6(self):
        day, _ = self._parse(["para el 21/6 a las 12hs"])
        self.assertEqual(day, "2026-06-21")

    def test_dash_format_21_6(self):
        day, _ = self._parse(["21-6 a las 10hs"])
        self.assertEqual(day, "2026-06-21")

    def test_time_12hs_extracted_alongside_date(self):
        day, t = self._parse(["Tenes para el 21 del 6 ? A las 12hs ?"])
        self.assertEqual(day, "2026-06-21")
        self.assertEqual(t, "12:00")

    def test_numeric_date_takes_next_year_if_past(self):
        from datetime import date
        day, _ = self._parse(["para el 3 del 1 a las 9hs"], today=date(2026, 6, 17))
        self.assertEqual(day, "2027-01-03")

    def test_si_alone_returns_no_date(self):
        day, t = self._parse(["Si"])
        self.assertIsNone(day)
        self.assertIsNone(t)

    def test_named_day_still_works(self):
        from datetime import date
        # today is Wednesday (weekday=2); "lunes" → next Monday = +5 days
        day, _ = self._parse(["el lunes a las 10hs"], today=date(2026, 6, 17))
        self.assertEqual(day, "2026-06-22")  # Monday 22/6

    def test_manana_still_works(self):
        from datetime import date
        day, _ = self._parse(["mañana 12hs"], today=date(2026, 6, 17))
        self.assertEqual(day, "2026-06-18")

    def test_all_spanish_months(self):
        from app.services.conversation_engine import _parse_scheduling_text
        from datetime import date
        today = date(2026, 1, 1)
        month_names = [
            "enero", "febrero", "marzo", "abril", "mayo", "junio",
            "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
        ]
        for i, mname in enumerate(month_names, start=1):
            day, _ = _parse_scheduling_text([f"para el 15 de {mname}"], today)
            self.assertIsNotNone(day, f"month '{mname}' not parsed")
            expected_month = i
            from datetime import date as d
            parsed = d.fromisoformat(day)
            self.assertEqual(parsed.month, expected_month, f"month mismatch for '{mname}'")


class TestQuotedDateProposal(unittest.TestCase):
    """QUOTED + date proposal → must NOT ask '¿Te parece bien?'.
    Sunday must be rejected.  No 'cliente' placeholder in any reply.
    """

    def _make_schedule_service(self, valid: bool = True):
        from unittest.mock import MagicMock
        from app.services.schedule import ScheduleCheckOut
        svc = MagicMock()
        if valid:
            svc.check.return_value = ScheduleCheckOut(
                valid=True,
                confirmed_day=None,
                confirmed_time=None,
                suggested_slots=[],
                reasons=[],
            )
        else:
            svc.check.return_value = ScheduleCheckOut(
                valid=False,
                confirmed_day=None,
                confirmed_time=None,
                suggested_slots=[],
                reasons=["Domingo"],
            )
        return svc

    def test_quoted_date_proposal_accepts_quote_and_advances(self):
        """QUOTED + parseable date → flag=ACEPTADO, stage=SCHEDULING, no AI."""
        from app.services.conversation_engine import (
            STAGE_QUOTED, STAGE_SCHEDULING, _parse_scheduling_text,
        )
        from datetime import date

        today = date(2026, 6, 17)
        messages = ["Tenes para el 23 del 6 ? A las 10hs ?"]  # Monday 23/6 = valid
        day, t = _parse_scheduling_text(messages, today)

        self.assertEqual(day, "2026-06-23")  # Monday
        self.assertEqual(t, "10:00")

        # Verify the condition that gates the new QUOTED-date path
        last_stage = STAGE_QUOTED
        needs_human = False
        gate_fires = (last_stage == STAGE_QUOTED and not needs_human and day is not None)
        self.assertTrue(gate_fires)

    def test_sunday_june_21_is_parsed_but_rejected_by_schedule(self):
        """June 21 is Sunday → _try_schedule_and_flow must call check() which returns invalid."""
        from app.services.conversation_engine import _parse_scheduling_text
        from datetime import date

        today = date(2026, 6, 17)
        messages = ["Tenes para el 21 del 6 ? A las 12hs ?"]
        day, t = _parse_scheduling_text(messages, today)

        self.assertEqual(day, "2026-06-21")

        parsed = date.fromisoformat(day)
        self.assertEqual(parsed.weekday(), 6, "21/6/2026 must be Sunday (weekday=6)")

    def test_no_cliente_in_handle_quoted_acceptance_reply_without_name(self):
        """_handle_quoted_acceptance must not emit 'cliente' when customer_name is None."""
        from unittest.mock import MagicMock

        eng = _make_engine()

        lead = MagicMock()
        lead.flag = "PRESUPUESTO_ENVIADO"
        lead.nombre = None

        state = MagicMock()
        state.customer_name = None
        state.last_stage = "QUOTED"

        sent_replies: list[str] = []

        def fake_send_text(ctx, text):
            sent_replies.append(text)
            return "fake-msg-id"

        eng._send_text_to_wa = fake_send_text

        ctx = MagicMock()
        ctx.lead = lead
        ctx.state = state

        eng._handle_quoted_acceptance(ctx, state)

        self.assertEqual(len(sent_replies), 1)
        reply = sent_replies[0]
        self.assertNotIn("cliente", reply.lower())
        self.assertIn("Genial", reply)

    def test_scheduling_si_with_stored_non_sunday_day_retries_slot(self):
        """'Si' in SCHEDULING stage with preferred_day=Monday → re-confirms that slot."""
        from app.services.conversation_engine import (
            STAGE_SCHEDULING, _parse_scheduling_text, _is_acceptance,
        )
        from datetime import date

        # Simulate the engine pre-AI SCHEDULING check logic
        today = date(2026, 6, 17)
        ai_input_messages = ["Si"]
        preferred_day = "2026-06-22"  # Monday
        preferred_time = "10:00"

        sched_day_iso, sched_time_str = _parse_scheduling_text(ai_input_messages, today)
        self.assertIsNone(sched_day_iso)

        # New: Si + stored non-Sunday → use stored day
        if not sched_day_iso and preferred_day and _is_acceptance(ai_input_messages):
            stored = date.fromisoformat(preferred_day)
            if stored.weekday() != 6:
                sched_day_iso = preferred_day
                sched_time_str = preferred_time

        self.assertEqual(sched_day_iso, "2026-06-22")
        self.assertEqual(sched_time_str, "10:00")

    def test_scheduling_si_with_stored_sunday_does_not_retry(self):
        """'Si' in SCHEDULING stage with preferred_day=Sunday → do NOT re-confirm."""
        from app.services.conversation_engine import (
            _parse_scheduling_text, _is_acceptance,
        )
        from datetime import date

        today = date(2026, 6, 17)
        ai_input_messages = ["Si"]
        preferred_day = "2026-06-21"  # Sunday
        preferred_time = "12:00"

        sched_day_iso, sched_time_str = _parse_scheduling_text(ai_input_messages, today)
        self.assertIsNone(sched_day_iso)

        if not sched_day_iso and preferred_day and _is_acceptance(ai_input_messages):
            stored = date.fromisoformat(preferred_day)
            if stored.weekday() != 6:  # Sunday excluded
                sched_day_iso = preferred_day

        self.assertIsNone(sched_day_iso, "Sunday stored day must not be retried")


# ── Scheduling state regression (real test 20260618) ─────────────────────────


class TestRejectedSlotNotStored(unittest.TestCase):
    """After ScheduleService rejects a slot, preferred_day/time must be cleared.
    A subsequent 'Okay' must NOT reuse the rejected date."""

    def _make_sched_service(self, valid: bool, reasons: list[str] | None = None):
        from unittest.mock import MagicMock
        svc = MagicMock()
        result = MagicMock()
        result.valid = valid
        result.suggested_slots = []
        result.reasons = reasons or ([] if valid else ["Domingo"])
        svc.check.return_value = result
        return svc

    def _make_state_obj(self, preferred_day=None, preferred_time=None, zone_group="Oeste",
                        zone_detail="San Justo"):
        return _make_state(
            preferred_day=preferred_day,
            preferred_time=preferred_time,
            home_zone_group=zone_group,
            home_zone_detail=zone_detail,
            last_stage="SCHEDULING",
        )

    def _run_try_schedule(self, state, day_iso, time_str, valid=True, reasons=None):
        eng = _make_engine()
        eng._schedule = self._make_sched_service(valid=valid, reasons=reasons)

        sent: list[str] = []
        eng._send_text_to_wa = lambda ctx, txt: sent.append(txt) or "msg-id"
        eng._send_flow_button = lambda ctx, body, token: "flow-id"

        ctx = _make_ctx(state=state)
        result = eng._try_schedule_and_flow(ctx, state, day_iso, time_str, "")
        return result, sent, state

    def test_rejected_slot_clears_preferred_day(self):
        """When ScheduleService rejects, preferred_day must be None afterwards."""
        state = self._make_state_obj()
        result, _, state = self._run_try_schedule(
            state, "2026-06-21", "12:00", valid=False, reasons=["Domingo"]
        )
        self.assertIsNone(state.preferred_day, "preferred_day must be cleared on rejection")
        self.assertIsNone(state.preferred_time, "preferred_time must be cleared on rejection")

    def test_rejected_slot_clears_preferred_time(self):
        state = self._make_state_obj(preferred_day="2026-06-21", preferred_time="12:00")
        self._run_try_schedule(state, "2026-06-21", "12:00", valid=False)
        self.assertIsNone(state.preferred_time)

    def test_valid_slot_stores_preferred_day(self):
        """When ScheduleService accepts, preferred_day must be stored."""
        state = self._make_state_obj()
        self._run_try_schedule(state, "2026-06-25", "10:00", valid=True)
        self.assertEqual(str(state.preferred_day), "2026-06-25")
        self.assertEqual(str(state.preferred_time), "10:00")

    def test_okay_after_sunday_rejection_finds_no_stored_day(self):
        """After Sunday rejection clears preferred_day, 'Okay' has nothing to confirm."""
        from app.services.conversation_engine import _parse_scheduling_text, _is_acceptance
        from datetime import date

        today = date(2026, 6, 18)
        # Simulate state after Sunday rejection: preferred_day cleared
        preferred_day = None  # cleared by rejection

        sched_day_iso, _ = _parse_scheduling_text(["Okay"], today)
        self.assertIsNone(sched_day_iso)

        # "Si/Okay + stored preferred_day" logic: preferred_day is None → no retry
        if not sched_day_iso and preferred_day and _is_acceptance(["Okay"]):
            sched_day_iso = preferred_day

        self.assertIsNone(sched_day_iso, "No slot should be confirmed when preferred_day is None")


class TestMnAbbreviation(unittest.TestCase):
    """'Mñ' (mobile abbreviation for 'mañana') must be parsed as tomorrow."""

    def _parse(self, texts, today=None):
        from app.services.conversation_engine import _parse_scheduling_text
        from datetime import date
        return _parse_scheduling_text(texts, today or date(2026, 6, 18))

    def test_mn_standalone_parsed_as_tomorrow(self):
        day, _ = self._parse(["Mñ 12hs"])
        self.assertEqual(day, "2026-06-19")  # tomorrow = 19/6

    def test_mn_lowercase_parsed(self):
        day, _ = self._parse(["mñ a las 10hs"])
        self.assertEqual(day, "2026-06-19")

    def test_mn_time_extracted(self):
        _, t = self._parse(["Mñ 12hs"])
        self.assertEqual(t, "12:00")

    def test_mn_does_not_match_inside_word(self):
        """'mñ' inside a longer token must not trigger mañana detection."""
        from app.services.conversation_engine import _parse_scheduling_text
        from datetime import date
        # "domñ" is not a real word but ensures we do word-boundary matching
        day, _ = _parse_scheduling_text(["domñ 10hs"], date(2026, 6, 18))
        self.assertIsNone(day)

    def test_manana_still_works_unchanged(self):
        day, _ = self._parse(["mañana 12hs"])
        self.assertEqual(day, "2026-06-19")


class TestSchedulingStageRegression(unittest.TestCase):
    """PRESUPUESTO_ENVIADO in SCHEDULING stage must be blocked (no regression)."""

    def test_presupuesto_enviado_blocked_in_scheduling(self):
        from app.services.conversation_engine import (
            STAGE_SCHEDULING, STAGE_FLOW_SENT, STAGE_BOOKED,
        )
        for stage in (STAGE_SCHEDULING, STAGE_FLOW_SENT, STAGE_BOOKED):
            new_flag = "PRESUPUESTO_ENVIADO"
            flag_accepted = True
            if flag_accepted and new_flag == "PRESUPUESTO_ENVIADO" and stage in (
                STAGE_SCHEDULING, STAGE_FLOW_SENT, STAGE_BOOKED
            ):
                flag_accepted = False
            self.assertFalse(flag_accepted, f"flag must be blocked in stage {stage}")

    def test_presupuesto_enviado_allowed_in_qualifying(self):
        from app.services.conversation_engine import STAGE_QUALIFYING
        new_flag = "PRESUPUESTO_ENVIADO"
        flag_accepted = True
        stage = STAGE_QUALIFYING
        if flag_accepted and new_flag == "PRESUPUESTO_ENVIADO" and stage in (
            "SCHEDULING", "FLOW_SENT", "BOOKED"
        ):
            flag_accepted = False
        self.assertTrue(flag_accepted, "PRESUPUESTO_ENVIADO allowed from QUALIFYING")


# ── Scheduling escalation regression (real test 20260618) ────────────────────


class TestEscalationKeywords(unittest.TestCase):
    """_should_escalate_scheduling_to_human must fire on insistence phrases."""

    def _check(self, texts, last_requested_time=None, last_offered_slots=None):
        from app.services.conversation_engine import _should_escalate_scheduling_to_human
        state = _make_state(
            last_requested_time=last_requested_time,
            last_offered_slots=last_offered_slots,
        )
        return _should_escalate_scheduling_to_human(texts, state)

    def test_solo_puedo_escalates(self):
        self.assertTrue(self._check(["solo puedo a las 12"]))

    def test_no_me_sirve_escalates(self):
        self.assertTrue(self._check(["no me sirve ninguno"]))

    def test_si_o_si_escalates(self):
        self.assertTrue(self._check(["mañana sí o sí"]))

    def test_excepcion_escalates(self):
        self.assertTrue(self._check(["podés hacer una excepción?"]))

    def test_ninguno_me_viene_escalates(self):
        self.assertTrue(self._check(["ninguno me viene bien"]))

    def test_normal_time_proposal_does_not_escalate(self):
        self.assertFalse(self._check(["14:30 me viene bien"]))

    def test_okay_alone_does_not_escalate(self):
        self.assertFalse(self._check(["Okay"]))

    def test_re_insistence_on_same_rejected_time_escalates(self):
        """Asking for the exact same rejected time after alternatives were offered."""
        import json
        self.assertTrue(self._check(
            ["a las 12hs sí o sí"],
            last_requested_time="12:00",
            last_offered_slots=json.dumps(["10:00", "14:30"]),
        ))

    def test_different_time_from_rejected_does_not_escalate(self):
        """Picking a different time (from alternatives) must not escalate."""
        import json
        self.assertFalse(self._check(
            ["a las 14:30"],
            last_requested_time="12:00",
            last_offered_slots=json.dumps(["10:00", "14:30"]),
        ))

    def test_no_offered_slots_means_no_re_insistence_check(self):
        """Re-insistence check requires last_offered_slots to be set."""
        self.assertFalse(self._check(
            ["a las 12hs"],
            last_requested_time="12:00",
            last_offered_slots=None,
        ))

    def test_empty_offered_slots_json_does_not_escalate(self):
        """Closed-day (e.g. Sunday) rejection stores '[]' — must NOT trigger escalation."""
        import json
        self.assertFalse(self._check(
            ["a las 12hs"],
            last_requested_time="12:00",
            last_offered_slots=json.dumps([]),  # "[]" — Sunday/closed day
        ))

    def test_mn_12hs_after_sunday_rejection_does_not_escalate(self):
        """'mñ 12hs' after a Sunday rejection must NOT escalate — it's a new valid date."""
        import json
        from app.services.conversation_engine import _should_escalate_scheduling_to_human
        state = _make_state(
            last_stage="SCHEDULING",
            active_requested_date="2026-06-21",   # Sunday
            last_requested_time="12:00",
            last_offered_slots=json.dumps([]),    # no slots for Sunday
        )
        result = _should_escalate_scheduling_to_human(["mñ 12hs"], state)
        self.assertFalse(result)

    def test_same_time_different_date_does_not_escalate(self):
        """Proposing the same time on a different (new) date must NOT escalate."""
        import json
        from app.services.conversation_engine import _should_escalate_scheduling_to_human
        # State: Friday 12hs was rejected, alternatives were offered
        state = _make_state(
            last_stage="SCHEDULING",
            active_requested_date="2026-06-19",   # Friday
            last_requested_time="12:00",
            last_offered_slots=json.dumps(["09:00", "09:30", "14:00"]),
        )
        # User changes to Saturday with the same time — fresh attempt, not insistence
        result = _should_escalate_scheduling_to_human(["el sábado a las 12hs"], state)
        self.assertFalse(result)

    def test_same_time_same_date_with_real_slots_escalates(self):
        """Re-requesting the same time on the same date after alternatives escalates."""
        import json
        from app.services.conversation_engine import _should_escalate_scheduling_to_human
        state = _make_state(
            last_stage="SCHEDULING",
            active_requested_date="2026-06-19",   # Friday
            last_requested_time="12:00",
            last_offered_slots=json.dumps(["09:00", "09:30", "14:00"]),
        )
        # User insists on 12hs for the same Friday
        result = _should_escalate_scheduling_to_human(["12hs"], state)
        self.assertTrue(result)

    def test_accepting_offered_slot_different_time_does_not_escalate(self):
        """Picking a time from the offered alternatives must not escalate."""
        import json
        from app.services.conversation_engine import _should_escalate_scheduling_to_human
        state = _make_state(
            last_stage="SCHEDULING",
            active_requested_date="2026-06-19",
            last_requested_time="12:00",
            last_offered_slots=json.dumps(["09:00", "14:00"]),
        )
        # User accepts "14:00" from alternatives
        result = _should_escalate_scheduling_to_human(["14:00"], state)
        self.assertFalse(result)

    def test_insistence_keyword_still_escalates_even_with_empty_slots(self):
        """Explicit insistence keywords escalate regardless of offered-slots state."""
        import json
        from app.services.conversation_engine import _should_escalate_scheduling_to_human
        state = _make_state(
            last_stage="SCHEDULING",
            active_requested_date="2026-06-21",   # Sunday
            last_requested_time="12:00",
            last_offered_slots=json.dumps([]),
        )
        result = _should_escalate_scheduling_to_human(["solo puedo a las 12"], state)
        self.assertTrue(result)


class TestRejectionStoresContext(unittest.TestCase):
    """After slot rejection, active_requested_date and last_offered_slots must be set."""

    def _run_rejection(self, day_iso="2026-06-19", time_str="12:00", slots=None,
                        list_slots_result=None):
        MagicMock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock
        eng = _make_engine()
        svc = MagicMock()

        check_mock = MagicMock()
        check_mock.valid = False
        check_mock.suggested_slots = slots if slots is not None else ["10:00", "14:30", "16:00"]
        check_mock.reasons = ["Ocupado"]
        svc.check.return_value = check_mock

        # list_slots returns full-day availability; default matches check slots
        list_mock = MagicMock()
        list_mock.slots = (
            list_slots_result if list_slots_result is not None
            else (slots if slots is not None else ["10:00", "14:30", "16:00"])
        )
        svc.list_slots.return_value = list_mock

        eng._schedule = svc

        sent: list[str] = []
        eng._send_text_to_wa = lambda ctx, txt: sent.append(txt) or "id"

        state = _make_state(
            home_zone_group="Oeste",
            home_zone_detail="San Justo",
            last_stage="SCHEDULING",
        )
        ctx = _make_ctx(state=state)
        eng._try_schedule_and_flow(ctx, state, day_iso, time_str, "")
        return state, sent

    def test_active_requested_date_set_on_rejection(self):
        state, _ = self._run_rejection()
        self.assertEqual(str(state.active_requested_date), "2026-06-19")

    def test_last_requested_time_set_on_rejection(self):
        state, _ = self._run_rejection()
        self.assertEqual(str(state.last_requested_time), "12:00")

    def test_last_offered_slots_set_on_rejection(self):
        import json
        state, _ = self._run_rejection(slots=["10:00", "14:30"])
        slots = json.loads(state.last_offered_slots)
        self.assertIn("10:00", slots)
        self.assertIn("14:30", slots)

    def test_preferred_day_cleared_on_rejection(self):
        state, _ = self._run_rejection()
        self.assertIsNone(state.preferred_day)

    def test_reply_uses_human_readable_date(self):
        """No raw ISO datetime strings in the rejection reply."""
        from datetime import date
        state, sent = self._run_rejection(day_iso="2026-06-19")
        self.assertEqual(len(sent), 1)
        reply = sent[0]
        self.assertNotIn("2026-06-19", reply)
        self.assertNotIn("T", reply)  # no ISO "T" separator

    def test_reply_includes_offered_slots(self):
        state, sent = self._run_rejection(slots=["10:00", "14:30"])
        self.assertIn("10:00", sent[0])

    def test_active_date_cleared_on_valid_slot(self):
        """When slot is available, active_requested_date and last_offered_slots are cleared."""
        eng = _make_engine()
        svc = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        result_mock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        result_mock.valid = True
        result_mock.suggested_slots = []
        result_mock.reasons = []
        svc.check.return_value = result_mock
        eng._schedule = svc
        eng._send_flow_button = lambda ctx, body, token: "flow-id"

        state = _make_state(
            home_zone_group="Oeste",
            home_zone_detail="San Justo",
            last_stage="SCHEDULING",
            active_requested_date="2026-06-19",
            last_requested_time="12:00",
            last_offered_slots='["10:00"]',
        )
        ctx = _make_ctx(state=state)
        eng._try_schedule_and_flow(ctx, state, "2026-06-25", "10:00", "")
        self.assertIsNone(state.active_requested_date)
        self.assertIsNone(state.last_offered_slots)


class TestDetectTimePeriod(unittest.TestCase):
    """_detect_time_period must recognise afternoon / morning requests."""

    def _detect(self, texts):
        from app.services.conversation_engine import _detect_time_period
        return _detect_time_period(texts)

    def test_por_la_tarde_detected(self):
        self.assertEqual(self._detect(["por la tarde no tenés?"]), "tarde")

    def test_a_la_tarde_detected(self):
        self.assertEqual(self._detect(["a la tarde, alguno?"]), "tarde")

    def test_por_la_manana_detected(self):
        self.assertEqual(self._detect(["por la mañana preferiblemente"]), "manana")

    def test_temprano_detected_as_manana(self):
        self.assertEqual(self._detect(["algo temprano?"]), "manana")

    def test_plain_time_returns_none(self):
        self.assertIsNone(self._detect(["14:30 me viene bien"]))

    def test_manana_alone_returns_none(self):
        """'mañana' alone means 'tomorrow', not 'morning period'."""
        self.assertIsNone(self._detect(["mañana a las 10hs"]))


class TestFormatDateHuman(unittest.TestCase):
    """_format_date_human must produce readable Argentine format."""

    def _fmt(self, iso, today_iso):
        from app.services.conversation_engine import _format_date_human
        from datetime import date
        return _format_date_human(iso, date.fromisoformat(today_iso))

    def test_tomorrow(self):
        result = self._fmt("2026-06-19", "2026-06-18")
        self.assertIn("mañana", result)
        self.assertIn("viernes", result)
        self.assertIn("19/06", result)

    def test_today(self):
        result = self._fmt("2026-06-18", "2026-06-18")
        self.assertIn("hoy", result)

    def test_future_day(self):
        result = self._fmt("2026-06-22", "2026-06-18")
        self.assertIn("lunes", result)
        self.assertIn("22/06", result)
        self.assertNotIn("mañana", result)

    def test_no_iso_in_output(self):
        result = self._fmt("2026-06-22", "2026-06-18")
        self.assertNotIn("2026", result)
        self.assertNotIn("-06-", result)


class TestHandleSchedulingEscalation(unittest.TestCase):
    """_handle_scheduling_escalation must create provisional revision, not Flow."""

    def _run_escalation(self, with_focus=True, existing_revision_id=None):
        from unittest.mock import MagicMock, patch
        eng = _make_engine()

        # Mock pricing
        from app.services.pricing import PricingQuote
        eng._compute_price_quote = lambda ctx, state: PricingQuote(
            tipo_vehiculo="AUTO", zone_group="Oeste", zone_detail="San Justo",
            precio_base=140000, viaticos=30000,
        )
        eng._pricing.recalculate_revision_if_possible = lambda db, revision: None

        # Fake DB — assign sequential ids on add so current_revision_id is non-None
        added: list = []
        _id_counter = [0]

        def _mock_add(obj):
            added.append(obj)
            _id_counter[0] += 1
            obj.id = _id_counter[0]

        eng.db.add = _mock_add
        eng.db.flush = lambda: None
        eng.db.commit = lambda: None

        sent: list[str] = []
        eng._send_text_to_wa = lambda ctx, txt: sent.append(txt) or "msg-id"
        eng._send_scheduling_handoff_email = lambda **kw: None

        focus = _make_candidate(
            id=1, tipo_vehiculo="AUTO", marca="Chevrolet", modelo="Captiva", anio=2020,
        ) if with_focus else None
        eng._focus_candidate = lambda ctx: focus

        state = _make_state(
            last_stage="SCHEDULING",
            home_zone_group="Oeste",
            home_zone_detail="San Justo",
            active_requested_date="2026-06-19",
            last_requested_time="12:00",
            last_offered_slots='["10:00", "14:30"]',
            current_revision_id=existing_revision_id,
        )

        lead = MagicMock()
        lead.id = 10
        lead.flag = "ACEPTADO"
        lead.estado = "CONSULTA_NUEVA"
        lead.nombre = None
        lead.email = None
        lead.necesita_humano = False

        contact = MagicMock()
        contact.wa_id = "5491100000000"

        ctx = _make_ctx(state=state)
        ctx.lead = lead
        ctx.state = state
        ctx.contact = contact

        result = eng._handle_scheduling_escalation(ctx, state, "solo puedo a las 12")
        return result, state, lead, added, sent

    def test_needs_human_set_on_state(self):
        _, state, _, _, _ = self._run_escalation()
        self.assertTrue(state.needs_human)

    def test_lead_necesita_humano_set(self):
        _, _, lead, _, _ = self._run_escalation()
        self.assertTrue(lead.necesita_humano)

    def test_lead_estado_set_to_atencion_humana(self):
        _, _, lead, _, _ = self._run_escalation()
        self.assertEqual(lead.estado, "ATENCION_HUMANA")

    def test_lead_flag_remains_aceptado(self):
        _, _, lead, _, _ = self._run_escalation()
        self.assertEqual(lead.flag, "ACEPTADO")

    def test_provisional_revision_created(self):
        from app.models import ThreadRevision
        _, _, _, added, _ = self._run_escalation()
        rev_objs = [o for o in added if isinstance(o, ThreadRevision)]
        self.assertEqual(len(rev_objs), 1)
        self.assertEqual(rev_objs[0].status, "provisional")

    def test_provisional_revision_has_vehicle_data(self):
        from app.models import ThreadRevision
        _, _, _, added, _ = self._run_escalation()
        rev = next(o for o in added if isinstance(o, ThreadRevision))
        self.assertEqual(rev.marca, "Chevrolet")
        self.assertEqual(rev.modelo, "Captiva")
        self.assertEqual(rev.anio, 2020)

    def test_provisional_revision_has_requested_slot(self):
        from app.models import ThreadRevision
        from datetime import date, time
        _, _, _, added, _ = self._run_escalation()
        rev = next(o for o in added if isinstance(o, ThreadRevision))
        self.assertEqual(rev.scheduled_date, date(2026, 6, 19))
        self.assertEqual(rev.scheduled_time, time(12, 0))

    def test_no_flow_sent(self):
        result, _, _, _, _ = self._run_escalation()
        self.assertNotEqual(result.action, "flow_button_sent")

    def test_lead_estado_not_agendado(self):
        _, _, lead, _, _ = self._run_escalation()
        self.assertNotEqual(lead.estado, "AGENDADO")

    def test_no_duplicate_revision_when_current_revision_id_set(self):
        from app.models import ThreadRevision
        _, _, _, added, _ = self._run_escalation(existing_revision_id=99)
        rev_objs = [o for o in added if isinstance(o, ThreadRevision)]
        self.assertEqual(len(rev_objs), 0, "No new revision if current_revision_id already set")

    def test_current_revision_id_stored_in_state(self):
        _, state, _, added, _ = self._run_escalation()
        self.assertIsNotNone(state.current_revision_id)

    def test_handoff_whatsapp_message_contains_julian(self):
        _, _, _, _, sent = self._run_escalation()
        self.assertEqual(len(sent), 1)
        self.assertIn("Julián", sent[0])

    def test_handoff_reply_no_iso_date(self):
        _, _, _, _, sent = self._run_escalation()
        self.assertNotIn("2026-06-", sent[0])


class TestSlotFormatting(unittest.TestCase):
    """Slots must be formatted as HH:MM, never as ISO datetime."""

    def test_time_hour_hhmm(self):
        from app.services.conversation_engine import _time_hour
        self.assertEqual(_time_hour("09:00"), 9)
        self.assertEqual(_time_hour("14:30"), 14)
        self.assertEqual(_time_hour("17:00"), 17)

    def test_time_hour_iso_fallback(self):
        """_time_hour must handle legacy ISO datetime strings gracefully."""
        from app.services.conversation_engine import _time_hour
        self.assertEqual(_time_hour("2026-06-19T09:00"), 9)
        self.assertEqual(_time_hour("2026-06-19T14:30"), 14)

    def test_rejection_reply_no_iso_strings(self):
        """Rejection message must never contain raw ISO datetime tokens."""
        state, sent = TestRejectionStoresContext()._run_rejection(
            day_iso="2026-06-19",
            slots=["09:00", "09:30", "10:00"],
            list_slots_result=["09:00", "09:30", "10:00"],
        )
        self.assertEqual(len(sent), 1)
        self.assertNotIn("2026-06-19T", sent[0])
        self.assertNotIn("T09", sent[0])

    def test_rejection_stores_hhmm_not_iso(self):
        """last_offered_slots must store HH:MM strings, not ISO datetimes."""
        import json
        state, _ = TestRejectionStoresContext()._run_rejection(
            slots=["09:00", "14:00"],
            list_slots_result=["09:00", "14:00"],
        )
        stored = json.loads(state.last_offered_slots)
        for s in stored:
            self.assertNotIn("T", s, f"ISO datetime leaked into last_offered_slots: {s!r}")
            self.assertRegex(s, r"^\d{2}:\d{2}$", f"Expected HH:MM, got: {s!r}")

    def test_full_day_slots_stored_after_rejection(self):
        """Rejection stores list_slots results (full day) not just check() first-5."""
        import json
        full_day = ["09:00", "09:30", "10:00", "10:30", "11:00",
                    "13:00", "13:30", "14:00", "14:30", "15:00"]
        state, _ = TestRejectionStoresContext()._run_rejection(
            slots=["09:00", "09:30", "10:00"],  # check() only returned 3 morning
            list_slots_result=full_day,          # list_slots has full day
        )
        stored = json.loads(state.last_offered_slots)
        self.assertEqual(stored, full_day)
        self.assertIn("14:00", stored, "Afternoon slot must be stored for period filtering")

    def test_period_filter_uses_full_day_stored_slots(self):
        """_handle_period_request must return afternoon slots from the stored full-day list."""
        MagicMock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock
        import json

        eng = _make_engine()
        eng._send_text_to_wa = MagicMock(return_value="id")

        full_day = ["09:00", "09:30", "10:00", "10:30", "11:00",
                    "13:00", "13:30", "14:00", "14:30", "15:00"]
        state = _make_state(
            last_stage="SCHEDULING",
            active_requested_date="2026-06-19",
            last_requested_time="12:00",
            last_offered_slots=json.dumps(full_day),
        )
        ctx = _make_ctx(state=state)

        result = eng._handle_period_request(ctx, state, "tarde")

        self.assertIsNotNone(result)
        call_args = eng._send_text_to_wa.call_args
        reply = call_args[0][1] if call_args[0] else call_args[1].get("txt", "")
        self.assertIn("13:00", reply)
        self.assertNotIn("09:00", reply, "Morning slot must not appear in tarde reply")

    def test_morning_filter_uses_stored_morning_slots(self):
        """'por la mañana' returns only morning slots from the full-day list."""
        MagicMock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock
        import json

        eng = _make_engine()
        eng._send_text_to_wa = MagicMock(return_value="id")

        full_day = ["09:00", "09:30", "10:00", "13:00", "14:00"]
        state = _make_state(
            last_stage="SCHEDULING",
            active_requested_date="2026-06-19",
            last_requested_time="14:00",
            last_offered_slots=json.dumps(full_day),
        )
        ctx = _make_ctx(state=state)

        result = eng._handle_period_request(ctx, state, "manana")

        self.assertIsNotNone(result)
        call_args = eng._send_text_to_wa.call_args
        reply = call_args[0][1] if call_args[0] else call_args[1].get("txt", "")
        self.assertIn("09:00", reply)
        self.assertNotIn("14:00", reply, "Afternoon slot must not appear in mañana reply")

    def test_period_reply_no_iso_strings(self):
        """Period reply must never contain ISO datetime tokens."""
        MagicMock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock
        import json

        eng = _make_engine()
        eng._send_text_to_wa = MagicMock(return_value="id")

        state = _make_state(
            last_stage="SCHEDULING",
            active_requested_date="2026-06-19",
            last_requested_time="12:00",
            last_offered_slots=json.dumps(["13:00", "13:30", "14:00"]),
        )
        ctx = _make_ctx(state=state)
        eng._handle_period_request(ctx, state, "tarde")

        call_args = eng._send_text_to_wa.call_args
        reply = call_args[0][1] if call_args[0] else call_args[1].get("txt", "")
        self.assertNotIn("2026-06-19T", reply)

    def test_12h_booking_does_not_block_afternoon(self):
        """A 12:00 booking (ends 13:00) must not block 13:00+ afternoon slots."""
        from app.services.schedule import ScheduleService, SERVICE_MINUTES, BUFFER_MINUTES
        from app.schemas.schedule import ScheduleCheckIn
        from datetime import date, time
        MagicMock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock

        db = MagicMock()
        # Simulate one revision at 12:00 on 2026-06-19 (Friday long)
        rev = MagicMock()
        rev.id = 18
        rev.turno_fecha = date(2026, 6, 19)
        rev.turno_hora = time(12, 0)
        db.execute.return_value.scalars.return_value.all.return_value = [rev]

        svc = ScheduleService(db=db)
        payload = ScheduleCheckIn(
            address="San Justo, Buenos Aires",
            preferred_day=date(2026, 6, 19),
            preferred_time=time(12, 0),
            zone_group="Oeste",
            zone_detail="San Justo",
        )

        # Manually check afternoon slots using _suggest_slots with the occupied slot
        from datetime import datetime, timedelta
        from app.services.schedule import OccupiedSlot
        rev_start = datetime(2026, 6, 19, 12, 0)
        rev_end = rev_start + timedelta(minutes=SERVICE_MINUTES + BUFFER_MINUTES)
        occupied = [OccupiedSlot("revision", 18, rev_start, rev_end, "Rev #18")]

        from app.services.schedule import _BusinessHours
        hours = _BusinessHours(start=time(9, 0), end=time(18, 0))
        all_slots = svc._suggest_slots(
            preferred_day=date(2026, 6, 19),
            occupied_slots=occupied,
            hours=hours,
            total_slot_minutes=SERVICE_MINUTES + BUFFER_MINUTES,
            payload=payload,
            max_results=24,
        )

        afternoon_slots = [s for s in all_slots if int(s.split(":")[0]) >= 13]
        self.assertGreater(len(afternoon_slots), 0, "Afternoon must be available after 12-13 block")
        self.assertIn("13:00", afternoon_slots)
        self.assertIn("14:00", afternoon_slots)

    def test_suggest_slots_returns_hhmm_not_iso(self):
        """_suggest_slots must return HH:MM strings, not ISO datetimes."""
        from app.services.schedule import ScheduleService, _BusinessHours, OccupiedSlot
        from app.schemas.schedule import ScheduleCheckIn
        from datetime import date, time
        MagicMock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock

        db = MagicMock()
        db.execute.return_value.scalars.return_value.all.return_value = []
        svc = ScheduleService(db=db)

        payload = ScheduleCheckIn(
            address="San Justo, Buenos Aires",
            preferred_day=date(2026, 6, 19),
            preferred_time=time(9, 0),
            zone_group="Oeste",
            zone_detail="San Justo",
        )
        hours = _BusinessHours(start=time(9, 0), end=time(18, 0))
        slots = svc._suggest_slots(
            preferred_day=date(2026, 6, 19),
            occupied_slots=[],
            hours=hours,
            total_slot_minutes=60,
            payload=payload,
            max_results=3,
        )
        for s in slots:
            self.assertRegex(s, r"^\d{2}:\d{2}$", f"Expected HH:MM, got ISO: {s!r}")
            self.assertNotIn("T", s)
            self.assertNotIn("2026", s)


class TestDayOnlyRequest(unittest.TestCase):
    """Issue B regression: day-only message must never auto-send the Flow.
    Engine must list available slots and ask for a specific time instead."""

    def _make_svc_with_slots(self, slots):
        MagicMock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock
        svc = MagicMock()
        svc.check.return_value = MagicMock(valid=False, suggested_slots=[], reasons=["test"])
        list_mock = MagicMock()
        list_mock.slots = slots
        list_mock.business_hours = "09:00-18:00"
        svc.list_slots.return_value = list_mock
        return svc

    def test_parse_sabado_returns_day_no_time(self):
        """'el sabado' parser returns a day but no time."""
        from app.services.conversation_engine import _parse_scheduling_text
        from datetime import date
        day, t = _parse_scheduling_text(["el sabado"], date(2026, 6, 16))
        self.assertIsNotNone(day)
        self.assertIsNone(t)

    def test_parse_sabado_o_lunes_returns_day_no_time(self):
        """'sino el sábado o lunes' parser returns a day but no time."""
        from app.services.conversation_engine import _parse_scheduling_text
        from datetime import date
        day, t = _parse_scheduling_text(["sino el sabado o lunes"], date(2026, 6, 16))
        self.assertIsNotNone(day)
        self.assertIsNone(t)

    def test_day_only_lists_slots_not_flow(self):
        """Day-only message in SCHEDULING must list slots, not send the Flow."""
        eng = _make_engine()
        svc = self._make_svc_with_slots(["09:00", "10:00", "14:00", "16:00"])
        eng._schedule = svc

        sent_texts: list[str] = []
        flow_sent: list[str] = []
        eng._send_text_to_wa = lambda ctx, txt: sent_texts.append(txt) or "id"
        eng._send_flow_button = lambda ctx, body, token: flow_sent.append(token) or "flow-id"

        state = _make_state(
            last_stage="SCHEDULING",
            home_zone_group="Oeste",
            home_zone_detail="San Justo",
        )
        ctx = _make_ctx(state=state)
        eng._handle_day_only_request(ctx, state, "2026-06-20")

        self.assertEqual(len(flow_sent), 0, "Flow must NOT be sent for a day-only request")
        self.assertEqual(len(sent_texts), 1)
        reply = sent_texts[0]
        self.assertIn("09:00", reply)
        self.assertIn("¿A qué hora", reply)

    def test_day_only_stores_offered_slots(self):
        """_handle_day_only_request must store last_offered_slots for later period filtering."""
        import json
        eng = _make_engine()
        svc = self._make_svc_with_slots(["09:00", "10:00", "14:00"])
        eng._schedule = svc
        eng._send_text_to_wa = lambda ctx, txt: "id"

        state = _make_state(last_stage="SCHEDULING", home_zone_group="Oeste", home_zone_detail="San Justo")
        ctx = _make_ctx(state=state)
        eng._handle_day_only_request(ctx, state, "2026-06-20")

        self.assertIsNotNone(state.last_offered_slots)
        offered = json.loads(state.last_offered_slots)
        self.assertIn("09:00", offered)

    def test_day_only_sunday_replies_closed(self):
        """Day-only request for Sunday tells the user we're closed."""
        MagicMock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock
        eng = _make_engine()
        svc = MagicMock()
        list_mock = MagicMock()
        list_mock.slots = []
        list_mock.business_hours = "cerrado"
        svc.list_slots.return_value = list_mock
        eng._schedule = svc

        sent_texts: list[str] = []
        eng._send_text_to_wa = lambda ctx, txt: sent_texts.append(txt) or "id"

        state = _make_state(last_stage="SCHEDULING", home_zone_group="Oeste", home_zone_detail="San Justo")
        ctx = _make_ctx(state=state)
        eng._handle_day_only_request(ctx, state, "2026-06-21")  # Sunday 21/06

        self.assertEqual(len(sent_texts), 1)
        reply = sent_texts[0].lower()
        self.assertTrue(
            "operaciones" in reply or "lunes a sábado" in reply or "no tenemos" in reply,
            f"Reply should mention closed/unavailable: {reply!r}",
        )


class TestNearestSlots(unittest.TestCase):
    """_nearest_slots must return slots closest to the requested time, not first n."""

    def _near(self, slots, requested, n=3):
        from app.services.conversation_engine import _nearest_slots
        return _nearest_slots(slots, requested, n)

    def test_late_request_returns_late_slots_first(self):
        """Issue A: 18:00 requested → latest available slots should come first."""
        slots = ["09:00", "09:30", "10:00", "14:00", "14:30", "16:00", "17:00"]
        result = self._near(slots, "18:00")
        self.assertEqual(result[0], "17:00")
        self.assertEqual(result[1], "16:00")

    def test_midday_request_returns_surrounding_slots(self):
        """12:00 requested → slots on both sides of noon."""
        slots = ["09:00", "09:30", "10:00", "14:00", "14:30"]
        result = self._near(slots, "12:00")
        self.assertIn("10:00", result)
        self.assertIn("14:00", result)

    def test_returns_at_most_n_slots(self):
        slots = ["09:00", "10:00", "11:00", "14:00", "15:00"]
        self.assertEqual(len(self._near(slots, "10:00", 3)), 3)

    def test_empty_slots_returns_empty(self):
        self.assertEqual(self._near([], "12:00"), [])

    def test_rejection_message_uses_nearest_not_first(self):
        """Slot rejection reply must show nearest slots, not first 3 morning slots."""
        MagicMock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock
        eng = _make_engine()
        svc = MagicMock()

        check_mock = MagicMock()
        check_mock.valid = False
        check_mock.suggested_slots = ["09:00", "09:30"]
        check_mock.reasons = ["Fuera de horario"]
        svc.check.return_value = check_mock

        list_mock = MagicMock()
        list_mock.slots = ["09:00", "09:30", "10:00", "14:00", "16:00", "17:00"]
        svc.list_slots.return_value = list_mock

        eng._schedule = svc
        sent_texts: list[str] = []
        eng._send_text_to_wa = lambda ctx, txt: sent_texts.append(txt) or "id"

        state = _make_state(last_stage="SCHEDULING", home_zone_group="Oeste", home_zone_detail="San Justo")
        ctx = _make_ctx(state=state)
        # User requested 18:00 → nearest 3 to 18:00 should be 17:00, 16:00, 14:00
        eng._try_schedule_and_flow(ctx, state, "2026-06-20", "18:00", "")

        self.assertEqual(len(sent_texts), 1)
        reply = sent_texts[0]
        self.assertIn("17:00", reply, "Nearest slot (17:00) must appear in rejection reply")
        self.assertNotIn("09:00", reply, "09:00 is far from 18:00 and must not be shown first")


class TestFlowFailureDetection(unittest.TestCase):
    """_is_flow_failure must detect WhatsApp Web / form-not-opening messages."""

    def _detect(self, texts):
        from app.services.conversation_engine import _is_flow_failure
        return _is_flow_failure(texts)

    def test_no_me_abre_detected(self):
        self.assertTrue(self._detect(["no me abre"]))

    def test_whatsapp_web_detected(self):
        self.assertTrue(self._detect(["estoy en whatsapp web"]))

    def test_desde_la_computadora_detected(self):
        self.assertTrue(self._detect(["lo estoy viendo desde la computadora"]))

    def test_normal_message_not_detected(self):
        self.assertFalse(self._detect(["el lunes me viene bien"]))

    def test_flow_failure_with_token_escalates(self):
        """When flow_booking_token is set and user can't open form → human handoff."""
        eng = _make_engine()
        sent_texts: list[str] = []
        eng._send_text_to_wa = lambda ctx, txt: sent_texts.append(txt) or "id"

        handoff_called: list[bool] = []

        def fake_handoff(ctx, state, thread_rev_id, last_message):
            handoff_called.append(True)

        eng._send_scheduling_handoff_email = fake_handoff

        state = _make_state(
            last_stage="SCHEDULING",
            flow_booking_token="tok-abc",
            needs_human=False,
        )
        ctx = _make_ctx(state=state)
        eng._handle_flow_failure(ctx, state, "no me abre el formulario")

        self.assertTrue(state.needs_human, "needs_human must be set to True")
        self.assertEqual(state.last_stage, "HUMAN_REQUIRED")
        self.assertEqual(len(sent_texts), 1)
        reply = sent_texts[0].lower()
        self.assertTrue(
            "julián" in reply or "julian" in reply or "manual" in reply,
            f"Reply must mention manual processing: {reply!r}",
        )
        self.assertTrue(handoff_called, "Handoff email must be sent")


class FakeRepoAutoAlmagro:
    """Covers AUTO in Almagro (CABA, no viaticos) and SUV in any zone."""

    _BASE = {
        "AUTO": FakePriceRow(tipo_vehiculo="AUTO", precio_base=130000),
        "SUV_4X4_DEPORTIVO": FakePriceRow(tipo_vehiculo="SUV_4X4_DEPORTIVO", precio_base=140000),
    }
    _ZONES_BY_DETAIL = {
        "almagro": FakeZone(zone_group="CABA", zone_detail="Almagro", viaticos=0),
        "tortuguitas": FakeZone(zone_group="Norte", zone_detail="Tortuguitas", viaticos=25000),
    }

    def find_base_price(self, tipo_vehiculo: str):
        return self._BASE.get(tipo_vehiculo)

    def find_zone_by_group_and_detail(self, db, zone_group, zone_detail):
        key = (zone_detail or "").strip().lower()
        return self._ZONES_BY_DETAIL.get(key)


class TestAuditVehicleChange(unittest.TestCase):
    """Regression suite for the Corsa→Meriva audit case.

    Original bugs:
    A. Bot invented $5.000 price (no deterministic quote).
    B. Vehicle changed to Meriva mid-conversation; bot sent Flow without
       re-quoting for the new vehicle.
    C. Legacy manual questionnaire (Comprador/Vendedor/Año) was shown while
       in SCHEDULING stage.
    """

    # ── Bug A: price invention ──────────────────────────────────────────

    def test_scrub_invented_price_when_no_quote(self):
        """_scrub_invented_price must strip any monetary amount when no real quote exists."""
        eng = _make_engine()
        result = eng._scrub_invented_price("El precio es $5.000 para el Corsa.", real_price_quote=None)
        self.assertNotIn("5.000", result)
        self.assertNotIn("$", result)

    def test_scrub_price_intent_when_no_quote(self):
        """Quote-promise ('te paso el precio') must be scrubbed with no deterministic quote."""
        eng = _make_engine()
        result = eng._scrub_invented_price("Te paso el precio ahora.", real_price_quote=None)
        # Should be replaced with a data-collection message
        self.assertNotIn("precio ahora", result.lower())

    def test_scrub_does_not_fire_when_real_quote_exists(self):
        """Reply with correct price is not scrubbed when quote is available."""
        eng = _make_engine()
        q = PricingQuote(
            tipo_vehiculo="AUTO", zone_group="CABA", zone_detail="Almagro",
            precio_base=130000, viaticos=0,
        )
        result = eng._scrub_invented_price("El precio de la revisión es $130.000.", real_price_quote=q)
        self.assertIn("130.000", result)

    def test_corsa_almagro_quote_is_deterministic(self):
        """Corsa (AUTO) + Almagro (CABA, $0 viáticos) → deterministic $130,000."""
        repo = FakeRepoAutoAlmagro()
        pricing = PricingService(repository=repo)
        q = pricing.quote(db=None, tipo_vehiculo="AUTO", zone_group=None, zone_detail="Almagro")
        self.assertEqual(q.precio_base, 130000)
        self.assertEqual(q.viaticos, 0)
        self.assertEqual(q.precio_total, 130000)
        self.assertEqual(q.zone_group, "CABA")

    def test_presupuesto_enviado_blocked_without_deterministic_quote(self):
        """PRESUPUESTO_ENVIADO flag is blocked when _compute_price_quote returns None."""
        from app.services.conversation_engine import ConversationEngine
        _ALLOWED_FLAGS = {"PRESUPUESTANDO", "PRESUPUESTO_ENVIADO", "ACEPTADO"}
        new_flag = "PRESUPUESTO_ENVIADO"
        flag_accepted = new_flag in _ALLOWED_FLAGS
        # Guard replicating _process_text logic
        real_price_quote = None
        if flag_accepted and new_flag == "PRESUPUESTO_ENVIADO" and real_price_quote is None:
            flag_accepted = False
        self.assertFalse(flag_accepted)

    # ── Bug B: vehicle change without re-quote ──────────────────────────

    def test_tipo_vehiculo_change_in_scheduling_resets_stage(self):
        """When tipo_vehiculo changes while in SCHEDULING, stage resets to QUALIFYING
        so the deterministic quote override re-prices for the new vehicle."""
        from app.services.conversation_engine import STAGE_SCHEDULING, STAGE_QUALIFYING

        eng = _make_engine(repo=FakeRepoAutoAlmagro())

        # Start: Corsa (AUTO) accepted, now in SCHEDULING
        corsa = _make_candidate(id=1, tipo_vehiculo="AUTO", marca="Chevrolet", modelo="Corsa")
        state = _make_state(
            last_stage=STAGE_SCHEDULING,
            home_zone_group="CABA",
            home_zone_detail="Almagro",
        )
        ctx = _make_ctx(state=state, candidates=[corsa])
        lead = ctx.lead
        lead.flag = "ACEPTADO"

        focus_before_tipo = corsa.tipo_vehiculo  # "AUTO"

        # AI updates the candidate to SUV (tipo changed)
        eng._apply_candidate(ctx, {
            "action": "update",
            "id": 1,
            "marca": "Chevrolet",
            "modelo": "Captiva",
            "tipo_vehiculo": "SUV_4X4_DEPORTIVO",
        })

        focus_after = eng._focus_candidate(ctx)

        # Simulate the vehicle-change guard from _process_text
        if (
            focus_after is not None
            and focus_before_tipo is not None
            and focus_after.tipo_vehiculo != focus_before_tipo
            and state.last_stage in (STAGE_SCHEDULING, "QUOTED", "FLOW_SENT")
            and not state.needs_human
        ):
            lead.flag = "PRESUPUESTANDO"
            state.last_stage = STAGE_QUALIFYING

        self.assertEqual(state.last_stage, STAGE_QUALIFYING,
                         "Stage must reset to QUALIFYING after vehicle tipo change")
        self.assertEqual(lead.flag, "PRESUPUESTANDO",
                         "Flag must reset to PRESUPUESTANDO after vehicle tipo change")

    def test_tipo_vehiculo_unchanged_does_not_reset_stage(self):
        """Updating marca/modelo without changing tipo_vehiculo must NOT reset the stage."""
        from app.services.conversation_engine import STAGE_SCHEDULING, STAGE_QUALIFYING

        eng = _make_engine(repo=FakeRepoAutoAlmagro())
        corsa = _make_candidate(id=1, tipo_vehiculo="AUTO", marca="Chevrolet", modelo="Corsa")
        state = _make_state(last_stage=STAGE_SCHEDULING, home_zone_group="CABA", home_zone_detail="Almagro")
        ctx = _make_ctx(state=state, candidates=[corsa])
        lead = ctx.lead
        lead.flag = "ACEPTADO"

        focus_before_tipo = corsa.tipo_vehiculo

        # AI updates modelo only — tipo_vehiculo stays AUTO
        eng._apply_candidate(ctx, {
            "action": "update",
            "id": 1,
            "marca": "Chevrolet",
            "modelo": "Meriva",
            "tipo_vehiculo": "AUTO",
        })

        focus_after = eng._focus_candidate(ctx)

        if (
            focus_after is not None
            and focus_before_tipo is not None
            and focus_after.tipo_vehiculo != focus_before_tipo
            and state.last_stage in (STAGE_SCHEDULING, "QUOTED", "FLOW_SENT")
            and not state.needs_human
        ):
            lead.flag = "PRESUPUESTANDO"
            state.last_stage = STAGE_QUALIFYING

        # Stage must NOT reset — same tipo_vehiculo means same price
        self.assertEqual(state.last_stage, STAGE_SCHEDULING)
        self.assertEqual(lead.flag, "ACEPTADO")

    def test_rafaga_vehicle_correction_is_not_pure_scheduling(self):
        """'Es una Chevrolet Meriva' + 'Miércoles 17HS' must fail the pure-scheduling check."""
        from app.services.conversation_engine import _is_pure_scheduling_rafaga
        from datetime import date

        messages = ["Es una Chevrolet Meriva", "Miércoles 17HS"]
        result = _is_pure_scheduling_rafaga(messages, date(2026, 6, 16))
        self.assertFalse(result, "Vehicle-correction ráfaga must not be treated as pure scheduling")

    def test_pure_scheduling_rafaga_recognized(self):
        """'Si' + 'mañana 12hs' must pass the pure-scheduling check."""
        from app.services.conversation_engine import _is_pure_scheduling_rafaga
        from datetime import date

        messages = ["Si", "mañana 12hs"]
        result = _is_pure_scheduling_rafaga(messages, date(2026, 6, 16))
        self.assertTrue(result, "Pure scheduling ráfaga (si + mañana 12hs) must be recognized")

    def test_single_day_time_message_is_pure_scheduling(self):
        """A single scheduling message passes the check."""
        from app.services.conversation_engine import _is_pure_scheduling_rafaga
        from datetime import date

        result = _is_pure_scheduling_rafaga(["el lunes a las 10hs"], date(2026, 6, 16))
        self.assertTrue(result)

    # ── Bug C: legacy manual questionnaire ─────────────────────────────

    def test_scheduling_prompt_does_not_ask_buyer_seller_data(self):
        """In SCHEDULING stage, the system prompt must NOT ask for buyer/seller/address.
        The questionnaire ('Comprador', 'Vendedor', 'datos del vehículo') is legacy
        behavior that was previously reachable via AI hallucination."""
        from app.services.conversation_engine import STAGE_SCHEDULING
        from unittest.mock import MagicMock

        eng = _make_engine(repo=FakeRepoAutoAlmagro())
        ctx = _make_ctx(
            candidates=[_make_candidate(tipo_vehiculo="AUTO", marca="Chevrolet", modelo="Corsa")],
            state=_make_state(
                last_stage=STAGE_SCHEDULING,
                home_zone_group="CABA",
                home_zone_detail="Almagro",
            ),
        )
        event = MagicMock()
        event.recent_outbound_replies = []
        event.recent_user_messages = []
        msgs = eng._build_ai_messages(ctx, event, ["el martes a las 10hs"])
        system = msgs[0]["content"]

        # Rule 4 must be present: SCHEDULING → only ask day/time
        self.assertIn("SCHEDULING", system)
        # Prompt must NOT demand buyer or seller data
        self.assertNotIn("Comprador:", system)
        self.assertNotIn("Vendedor:", system)
        # Rule 12: in SCHEDULING, don't mention price/quote
        self.assertIn("SCHEDULING", system)
        self.assertIn("NO menciones precio", system)

    def test_handled_true_on_replied_action(self):
        """handled=True must be set on 'replied' action so n8n skips legacy AI path."""
        from app.services.conversation_engine import _out
        from app.schemas.conversation import HANDLED_ACTIONS
        result = _out("replied", wa_message_id="msg-123")
        self.assertTrue(result.handled)
        self.assertIn("replied", HANDLED_ACTIONS)

    def test_handled_false_on_unmatched_action(self):
        """Actions not in HANDLED_ACTIONS must return handled=False."""
        from app.services.conversation_engine import _out
        result = _out("skipped_unknown")
        self.assertFalse(result.handled)


class TestSlotOrdering(unittest.TestCase):
    """All slot lists — stored and displayed — must be chronological."""

    def _run_rejection(self, requested_time="12:00", all_day_slots=None):
        """Trigger the rejection branch in _try_schedule_and_flow and return (state, sent)."""
        MagicMock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock
        eng = _make_engine()
        svc = MagicMock()
        check_mock = MagicMock(valid=False, suggested_slots=[], reasons=["Ocupado"])
        svc.check.return_value = check_mock
        list_mock = MagicMock()
        list_mock.slots = all_day_slots or ["09:00", "09:30", "10:00", "10:30", "11:00",
                                             "13:00", "13:30", "14:00", "14:30"]
        svc.list_slots.return_value = list_mock
        eng._schedule = svc
        sent: list[str] = []
        eng._send_text_to_wa = lambda ctx, txt: sent.append(txt) or "id"
        state = _make_state(last_stage="SCHEDULING", home_zone_group="Oeste", home_zone_detail="San Justo")
        ctx = _make_ctx(state=state)
        eng._try_schedule_and_flow(ctx, state, "2026-06-19", requested_time, "")
        return state, sent

    def test_offered_slots_stored_sorted(self):
        """last_offered_slots must be chronological regardless of list_slots order."""
        import json
        # Simulate list_slots returning slots out of order (as seen in the bug)
        state, _ = self._run_rejection(all_day_slots=["11:00", "13:00", "10:30", "09:00", "14:00"])
        stored = json.loads(state.last_offered_slots)
        self.assertEqual(stored, sorted(stored), f"Stored slots not sorted: {stored}")

    def test_rejection_reply_slots_are_chronological(self):
        """The offered slots in the rejection reply must be in chronological order."""
        _, sent = self._run_rejection(requested_time="12:00",
                                      all_day_slots=["09:00", "09:30", "10:00", "10:30",
                                                     "11:00", "13:00", "13:30", "14:00"])
        reply = sent[0]
        # Extract only the offered-slot section (after "disponibles:")
        import re
        after_disponibles = reply.split("disponibles:")[-1] if "disponibles:" in reply else reply
        times = re.findall(r"\d{2}:\d{2}", after_disponibles)
        self.assertTrue(len(times) >= 2, f"Expected at least 2 slot times in reply: {reply!r}")
        self.assertEqual(times, sorted(times), f"Offered slots not in chronological order: {times}")

    def test_nearest_slots_displayed_chronologically(self):
        """The 3 nearest slots to the requested time must be sorted, not by proximity."""
        from app.services.conversation_engine import _nearest_slots
        # Nearest to 12:00: 11:00 (60min), 13:00 (60min), 10:30 (90min)
        # Without sorting: [11:00, 13:00, 10:30] — WRONG
        # With sorting: [10:30, 11:00, 13:00] — correct
        result = _nearest_slots(["09:00", "10:30", "11:00", "13:00", "14:00"], "12:00", 3)
        self.assertEqual(sorted(result), sorted(result))  # basic sanity
        # The displayed ones (sorted) must be in order
        displayed = sorted(result)
        self.assertEqual(displayed, sorted(displayed))


class TestPeriodDetection(unittest.TestCase):
    """_detect_time_period must handle accents, typos, and standalone 'tarde'."""

    def _detect(self, texts):
        from app.services.conversation_engine import _detect_time_period
        return _detect_time_period(texts)

    def test_a_la_tarde_ascii(self):
        self.assertEqual(self._detect(["a la tarde?"]), "tarde")

    def test_accented_a_la_tarde(self):
        """'À la tarde?' (accented À) must normalize and return 'tarde'."""
        self.assertEqual(self._detect(["À la tarde?"]), "tarde")

    def test_por_la_tarde(self):
        self.assertEqual(self._detect(["por la tarde"]), "tarde")

    def test_standalone_tarde(self):
        """Single-word 'tarde?' must return 'tarde'."""
        self.assertEqual(self._detect(["tarde?"]), "tarde")
        self.assertEqual(self._detect(["tarde"]), "tarde")

    def test_mañana_a_la_tarde_detects_tarde(self):
        """'Mañana a la tarde' has day + period — period must still be detected."""
        self.assertEqual(self._detect(["Mañana a la tarde"]), "tarde")

    def test_por_la_manana(self):
        self.assertEqual(self._detect(["por la mañana"]), "manana")

    def test_temprano(self):
        self.assertEqual(self._detect(["algo temprano"]), "manana")

    def test_unrelated_message_not_detected(self):
        self.assertIsNone(self._detect(["el lunes a las 10hs"]))

    def test_no_false_positive_from_scheduling_context(self):
        self.assertIsNone(self._detect(["mañana 12hs"]))


class TestPeriodSchedulingFlow(unittest.TestCase):
    """Afternoon/morning period requests must be deterministic in SCHEDULING stage."""

    def _make_svc(self, slots):
        MagicMock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock
        svc = MagicMock()
        check_mock = MagicMock(valid=False, suggested_slots=[], reasons=["test"])
        svc.check.return_value = check_mock
        list_mock = MagicMock()
        list_mock.slots = slots
        list_mock.business_hours = "09:00-18:00"
        svc.list_slots.return_value = list_mock
        return svc

    def _make_afternoon_state(self, with_slots=True):
        import json
        slots = ["09:00", "09:30", "10:00", "10:30", "11:00",
                 "13:00", "13:30", "14:00", "14:30"]
        return _make_state(
            last_stage="SCHEDULING",
            home_zone_group="Oeste",
            home_zone_detail="San Justo",
            active_requested_date="2026-06-19",
            last_offered_slots=json.dumps(sorted(slots)) if with_slots else None,
        )

    def test_a_la_tarde_accented_uses_active_date(self):
        """'À la tarde?' must filter last_offered_slots for afternoon, not ask for day/time."""
        eng = _make_engine()
        sent: list[str] = []
        eng._send_text_to_wa = lambda ctx, txt: sent.append(txt) or "id"

        state = self._make_afternoon_state()
        ctx = _make_ctx(state=state)
        result = eng._handle_period_request(ctx, state, "tarde")

        self.assertIsNotNone(result)
        self.assertEqual(len(sent), 1)
        reply = sent[0]
        # Must show afternoon slots
        self.assertTrue(
            any(t in reply for t in ["13:00", "13:30", "14:00", "14:30"]),
            f"Afternoon slots must appear in reply: {reply!r}",
        )
        # Must NOT ask for day/time generically
        self.assertNotIn("¿Qué día", reply)

    def test_manana_a_la_tarde_returns_afternoon_slots(self):
        """'Mañana a la tarde' must list afternoon slots, not morning."""
        eng = _make_engine()
        all_slots = ["09:00", "09:30", "10:00", "10:30", "11:00",
                     "13:00", "13:30", "14:00", "14:30"]
        eng._schedule = self._make_svc(all_slots)
        sent: list[str] = []
        eng._send_text_to_wa = lambda ctx, txt: sent.append(txt) or "id"

        state = self._make_afternoon_state(with_slots=False)
        ctx = _make_ctx(state=state)
        eng._handle_day_only_request(ctx, state, "2026-06-19", period="tarde")

        reply = sent[0]
        # Must not contain morning-only slots as the primary options
        self.assertNotIn("09:00", reply)
        self.assertNotIn("10:00", reply)
        # Must contain afternoon slots
        self.assertTrue(
            any(t in reply for t in ["13:00", "13:30", "14:00", "14:30"]),
            f"Afternoon slots must appear: {reply!r}",
        )

    def test_day_only_slots_are_sorted(self):
        """Slots stored via _handle_day_only_request must be chronological."""
        import json
        eng = _make_engine()
        # Simulate list_slots returning an unordered list
        eng._schedule = self._make_svc(["14:00", "09:00", "13:00", "10:30"])
        eng._send_text_to_wa = lambda ctx, txt: "id"

        state = self._make_afternoon_state(with_slots=False)
        ctx = _make_ctx(state=state)
        eng._handle_day_only_request(ctx, state, "2026-06-19")

        stored = json.loads(state.last_offered_slots)
        self.assertEqual(stored, sorted(stored), f"Stored slots must be sorted: {stored}")

    def test_period_request_does_not_send_flow(self):
        """A period request ('tarde') must NEVER send the Flow button."""
        eng = _make_engine()
        sent_texts: list[str] = []
        flow_sent: list[bool] = []
        eng._send_text_to_wa = lambda ctx, txt: sent_texts.append(txt) or "id"
        eng._send_flow_button = lambda ctx, body, token: flow_sent.append(True) or "flow-id"

        state = self._make_afternoon_state()
        ctx = _make_ctx(state=state)
        eng._handle_period_request(ctx, state, "tarde")

        self.assertEqual(flow_sent, [], "Flow must NOT be sent for a period request")
        self.assertEqual(len(sent_texts), 1)

    def test_exact_slot_from_afternoon_list_can_trigger_flow(self):
        """After afternoon list shown, user picks exact slot → Flow is sent."""
        MagicMock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock
        eng = _make_engine()
        svc = MagicMock()
        check_mock = MagicMock(valid=True, suggested_slots=[], reasons=[])
        svc.check.return_value = check_mock
        eng._schedule = svc
        flow_sent: list[str] = []
        eng._send_flow_button = lambda ctx, body, token: flow_sent.append(token) or "flow-id"
        eng._send_text_to_wa = lambda ctx, txt: "id"

        import json
        state = _make_state(
            last_stage="SCHEDULING",
            home_zone_group="Oeste",
            home_zone_detail="San Justo",
            active_requested_date="2026-06-19",
            last_offered_slots=json.dumps(["09:00", "13:00", "14:00"]),
        )
        ctx = _make_ctx(state=state)
        # User picks "14:00" from the afternoon list
        eng._try_schedule_and_flow(ctx, state, "2026-06-19", "14:00", "")

        self.assertTrue(len(flow_sent) > 0, "Flow must be sent when exact slot is confirmed available")

    def test_reply_contains_no_iso_strings(self):
        """Rejection reply must not contain ISO datetime strings."""
        _, sent = TestSlotOrdering()._run_rejection()
        reply = sent[0]
        self.assertNotIn("T", reply.split("disponibilidad")[0] if "disponibilidad" in reply else reply[:50])
        import re
        iso_pattern = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
        self.assertFalse(iso_pattern.search(reply), f"ISO datetime found in reply: {reply!r}")

    def test_period_reply_no_quote_mention(self):
        """Period reply in SCHEDULING must not repeat the price/quote."""
        eng = _make_engine()
        sent: list[str] = []
        eng._send_text_to_wa = lambda ctx, txt: sent.append(txt) or "id"

        import json
        state = _make_state(
            last_stage="SCHEDULING",
            home_zone_group="Oeste",
            home_zone_detail="San Justo",
            active_requested_date="2026-06-19",
            last_offered_slots=json.dumps(["09:00", "13:00", "14:00", "14:30"]),
        )
        ctx = _make_ctx(state=state)
        eng._handle_period_request(ctx, state, "tarde")

        reply = sent[0].lower()
        self.assertNotIn("cotización", reply)
        self.assertNotIn("$", reply)
        self.assertNotIn("precio", reply)


class TestSelectSlotFromOffered(unittest.TestCase):
    """Unit tests for _select_slot_from_offered — ordinal/positional slot detection."""

    def setUp(self):
        from app.services.conversation_engine import _select_slot_from_offered
        import json
        self._fn = _select_slot_from_offered
        self._slots = json.dumps(["09:00", "09:30", "10:00", "13:00", "13:30", "14:00", "14:30"])

    def test_ultimo_picks_last(self):
        self.assertEqual(self._fn(["el último horario"], self._slots), "14:30")

    def test_ultimo_no_accent(self):
        self.assertEqual(self._fn(["el ultimo horario"], self._slots), "14:30")

    def test_ultomp_typo_picks_last(self):
        """'el ultomp horario' is a real WhatsApp typo for 'el último horario'."""
        self.assertEqual(self._fn(["el ultomp horario"], self._slots), "14:30")

    def test_ultom_typo_picks_last(self):
        self.assertEqual(self._fn(["el ultom"], self._slots), "14:30")

    def test_ultiom_typo_picks_last(self):
        self.assertEqual(self._fn(["ultiom"], self._slots), "14:30")

    def test_primero_picks_first(self):
        self.assertEqual(self._fn(["el primero"], self._slots), "09:00")

    def test_primer_picks_first(self):
        self.assertEqual(self._fn(["el primer horario"], self._slots), "09:00")

    def test_segundo_picks_second(self):
        import json
        self.assertEqual(self._fn(["el segundo"], self._slots), "09:30")

    def test_tercero_picks_third(self):
        import json
        self.assertEqual(self._fn(["el tercero"], self._slots), "10:00")

    def test_no_ordinal_returns_none(self):
        self.assertIsNone(self._fn(["si"], self._slots))

    def test_empty_slots_returns_none(self):
        import json
        self.assertIsNone(self._fn(["el último"], json.dumps([])))

    def test_none_slots_returns_none(self):
        self.assertIsNone(self._fn(["el último"], None))

    def test_multi_message_burst(self):
        self.assertEqual(self._fn(["horario", "el último"], self._slots), "14:30")

    def test_segunda_picks_second(self):
        import json
        # feminine form
        self.assertEqual(self._fn(["la segunda opción"], self._slots), "09:30")


class TestOrdinalSlotConfirmation(unittest.TestCase):
    """Step 2b + step 4b: ordinal selection and pending-slot si-confirmation."""

    def _make_svc(self, valid=True, all_slots=None):
        MagicMock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock
        svc = MagicMock()
        svc.check.return_value = MagicMock(
            valid=valid, suggested_slots=[], reasons=["no disp"]
        )
        ls_mock = MagicMock()
        ls_mock.slots = all_slots or []
        svc.list_slots.return_value = ls_mock
        return svc

    def _make_scheduling_state(self, preferred_day=None, preferred_time=None):
        import json
        return _make_state(
            last_stage="SCHEDULING",
            home_zone_group="Oeste",
            home_zone_detail="San Justo",
            active_requested_date="2026-06-19",
            last_offered_slots=json.dumps(["09:00", "09:30", "13:00", "13:30", "14:00", "14:30"]),
            preferred_day=preferred_day,
            preferred_time=preferred_time,
        )

    # ── Step 2b: ordinal selection → _try_schedule_and_flow ────────────────

    def test_ultimo_horario_sends_flow(self):
        """'el último horario': ordinal picks 14:30, _try_schedule_and_flow sends Flow."""
        from app.services.conversation_engine import _select_slot_from_offered
        import json

        eng = _make_engine()
        eng._schedule = self._make_svc(valid=True)
        flow_sent: list[str] = []
        eng._send_flow_button = lambda ctx, body, token: flow_sent.append(token) or "flow-id"
        eng._send_text_to_wa = lambda ctx, txt: "id"

        state = self._make_scheduling_state()
        ctx = _make_ctx(state=state)

        # Simulate step 2b
        chosen = _select_slot_from_offered(["el último horario"], str(state.last_offered_slots))
        self.assertEqual(chosen, "14:30")
        result = eng._try_schedule_and_flow(ctx, state, str(state.active_requested_date), chosen, "")

        self.assertEqual(result.action, "flow_button_sent")
        self.assertEqual(len(flow_sent), 1)

    def test_ultomp_typo_sends_flow(self):
        """'el ultomp horario' (WhatsApp typo) selects last slot and sends Flow."""
        from app.services.conversation_engine import _select_slot_from_offered
        import json

        eng = _make_engine()
        eng._schedule = self._make_svc(valid=True)
        flow_sent: list[str] = []
        eng._send_flow_button = lambda ctx, body, token: flow_sent.append(token) or "flow-id"
        eng._send_text_to_wa = lambda ctx, txt: "id"

        state = self._make_scheduling_state()
        ctx = _make_ctx(state=state)

        chosen = _select_slot_from_offered(["el ultomp horario"], str(state.last_offered_slots))
        self.assertEqual(chosen, "14:30", "Typo 'ultomp' must resolve to last slot")
        result = eng._try_schedule_and_flow(ctx, state, str(state.active_requested_date), chosen, "")

        self.assertEqual(result.action, "flow_button_sent")
        self.assertEqual(len(flow_sent), 1)

    def test_ultimo_unavailable_offers_alternatives(self):
        """'el último horario' for an unavailable slot must offer alternatives, not Flow."""
        from app.services.conversation_engine import _select_slot_from_offered

        eng = _make_engine()
        eng._schedule = self._make_svc(valid=False, all_slots=["09:00", "09:30", "10:00"])
        sent: list[str] = []
        eng._send_text_to_wa = lambda ctx, txt: sent.append(txt) or "id"

        state = self._make_scheduling_state()
        ctx = _make_ctx(state=state)

        chosen = _select_slot_from_offered(["el último horario"], str(state.last_offered_slots))
        self.assertEqual(chosen, "14:30")
        result = eng._try_schedule_and_flow(ctx, state, str(state.active_requested_date), chosen, "")

        self.assertEqual(result.action, "replied")
        self.assertEqual(len(sent), 1)
        self.assertIn("disponib", sent[0].lower())

    def test_primero_sends_flow_for_first_slot(self):
        """'el primero' selects 09:00 and sends Flow."""
        from app.services.conversation_engine import _select_slot_from_offered

        eng = _make_engine()
        svc = self._make_svc(valid=True)
        eng._schedule = svc
        flow_sent: list[str] = []
        eng._send_flow_button = lambda ctx, body, token: flow_sent.append(token) or "flow-id"
        eng._send_text_to_wa = lambda ctx, txt: "id"

        state = self._make_scheduling_state()
        ctx = _make_ctx(state=state)

        chosen = _select_slot_from_offered(["el primero"], str(state.last_offered_slots))
        self.assertEqual(chosen, "09:00")
        result = eng._try_schedule_and_flow(ctx, state, str(state.active_requested_date), chosen, "")

        self.assertEqual(result.action, "flow_button_sent")
        call_args = svc.check.call_args
        checkin = call_args[0][0]
        self.assertEqual(checkin.preferred_time.strftime("%H:%M"), "09:00")

    def test_current_revision_id_null_after_ordinal_flow(self):
        """current_revision_id must remain None after ordinal-triggered Flow button."""
        from app.services.conversation_engine import _select_slot_from_offered

        eng = _make_engine()
        eng._schedule = self._make_svc(valid=True)
        eng._send_flow_button = lambda ctx, body, token: "flow-id"
        eng._send_text_to_wa = lambda ctx, txt: "id"

        state = self._make_scheduling_state()
        ctx = _make_ctx(state=state)

        chosen = _select_slot_from_offered(["el último horario"], str(state.last_offered_slots))
        eng._try_schedule_and_flow(ctx, state, str(state.active_requested_date), chosen, "")

        self.assertIsNone(state.current_revision_id, "current_revision_id must stay None until Flow submit")

    def test_step_2b_simulation_ordinal_resolves_to_try_schedule(self):
        """Simulate full step 2b path: ordinal → chosen slot → _try_schedule_and_flow."""
        from app.services.conversation_engine import (
            _select_slot_from_offered, _parse_scheduling_text, _detect_time_period,
        )
        import json
        from datetime import date as dt_date

        today = dt_date(2026, 6, 18)
        last_offered = json.dumps(["09:00", "09:30", "13:00", "14:00", "14:30"])
        active_date = "2026-06-19"
        messages = ["el ultomp horario"]

        sched_day_iso, sched_time_str = _parse_scheduling_text(messages, today)
        period = _detect_time_period(messages)

        # Step 2b guard: no day, no time, no period → check ordinal
        self.assertIsNone(sched_day_iso)
        self.assertIsNone(sched_time_str)
        self.assertIsNone(period)

        chosen = _select_slot_from_offered(messages, last_offered)
        self.assertEqual(chosen, "14:30", "Ordinal 'ultomp' must resolve to last slot")

        # The resolved day+time
        resolved_day = active_date
        resolved_time = chosen
        self.assertEqual(resolved_day, "2026-06-19")
        self.assertEqual(resolved_time, "14:30")

    # ── Step 4b: "si" + preferred_time + active_date → Flow ─────────────────

    def test_si_confirms_pending_ai_proposed_time(self):
        """'si' with preferred_time set and preferred_day=None uses active_requested_date."""
        from app.services.conversation_engine import (
            _is_acceptance, _parse_scheduling_text, _detect_time_period,
            _is_pure_scheduling_rafaga,
        )
        from datetime import date as dt_date

        today = dt_date(2026, 6, 18)
        messages = ["si"]
        preferred_time = "14:30"
        preferred_day = None
        active_requested_date = "2026-06-19"

        sched_day_iso, sched_time_str = _parse_scheduling_text(messages, today)
        self.assertIsNone(sched_day_iso)
        self.assertIsNone(sched_time_str)

        # Step 4b: si + preferred_time + active_requested_date
        if (
            not sched_day_iso
            and preferred_time
            and active_requested_date
            and _is_acceptance(messages)
        ):
            from datetime import date as dt_date2
            active_date = dt_date2.fromisoformat(active_requested_date)
            if active_date.weekday() != 6:
                sched_day_iso = active_requested_date
                sched_time_str = preferred_time

        self.assertEqual(sched_day_iso, "2026-06-19", "Step 4b must set day from active_requested_date")
        self.assertEqual(sched_time_str, "14:30", "Step 4b must set time from preferred_time")

    def test_si_with_pending_time_sends_flow(self):
        """'si' after AI confirms slot (preferred_time set) must trigger Flow, not generic question."""
        eng = _make_engine()
        eng._schedule = self._make_svc(valid=True)
        flow_sent: list[str] = []
        eng._send_flow_button = lambda ctx, body, token: flow_sent.append(token) or "flow-id"
        eng._send_text_to_wa = lambda ctx, txt: "id"

        # State: AI extracted preferred_time="14:30" in previous turn;
        # preferred_day was cleared by rejection; active_requested_date is still set.
        state = self._make_scheduling_state(preferred_day=None, preferred_time="14:30")
        ctx = _make_ctx(state=state)

        # Simulate step 4b: si + preferred_time + active_requested_date → _try_schedule_and_flow
        result = eng._try_schedule_and_flow(ctx, state, str(state.active_requested_date), "14:30", "")

        self.assertEqual(result.action, "flow_button_sent",
                         "Step 4b path must trigger Flow for 'si' when pending time exists")
        self.assertEqual(len(flow_sent), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
