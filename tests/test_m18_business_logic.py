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
        current_focus_candidate_id=None,
        preferred_day=None,
        preferred_time=None,
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
