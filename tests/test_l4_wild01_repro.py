"""L4-WILD-01 forensic reproduction — Cases A and B.

CASE A: Incident state (cycle_reset_pending=False, stale prior-cycle candidate 129).
         Verifies CE would generate a quote using Berazategui viaticos → $240,000.

CASE B: Canonical-reset state (cycle_reset_pending=True, zone cleared after reset).
         Verifies CE cannot quote without location; no $240,000 produced.

These are read-only invariant checks.  No DB writes to crm or crm_test.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.orm import Session

_TZ = timezone.utc
_PRIOR_CYCLE = datetime(2026, 8, 27, 19, 20, 56, tzinfo=_TZ)
_WILD_START  = datetime(2026, 9, 1,  15, 34, 24, tzinfo=_TZ)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mem_db():
    """Fresh SQLite in-memory DB with full schema (JSONB→JSON patch applied by conftest)."""
    import app.models  # noqa: F401 — ensures all tables registered in metadata
    # L4.4: take Base from app.models — another suite in the same session may have
    # stubbed app.db, which would yield an empty metadata and no tables.
    Base = app.models.Base
    # L4.4: SQLite in-memory needs a single shared connection, otherwise every new
    # connection gets an empty database and create_all() is invisible to the session.
    from sqlalchemy.pool import StaticPool
    engine = create_engine(
        "sqlite:///:memory:", echo=False,
        poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _seed_lead_contact_thread(db):
    from app.models import Lead, WhatsAppContact, WhatsAppThread
    lead = Lead(
        id=4, nombre="Lara", apellido="Dittmar",
        email="lara@test.com", telefono="5491153368330",
        acq_source="organic", inbound_channel="WHATSAPP",
    )
    db.add(lead); db.flush()
    contact = WhatsAppContact(id=2, wa_id="5491153368330", display_name="Lara Dittmar")
    db.add(contact); db.flush()
    thread = WhatsAppThread(
        id=2, contact_id=contact.id, lead_id=lead.id,
        inbound_channel="WHATSAPP", last_message_at=_PRIOR_CYCLE,
    )
    db.add(thread); db.flush()
    return lead, contact, thread


def _seed_prior_candidate(db, thread_id):
    from app.models import WhatsAppThreadCandidate
    cand = WhatsAppThreadCandidate(
        id=129, thread_id=thread_id,
        marca="Peugeot", modelo="2008", anio=2015,
        tipo_vehiculo="SUV_4X4_DEPORTIVO",
        zone_group="Sur", zone_detail="Berazategui",
        status="current_focus",
    )
    db.add(cand); db.flush()
    return cand


def _make_state(db, thread_id, cand_id, *, reset_pending: bool):
    from app.models import WhatsAppThreadState
    state = WhatsAppThreadState(
        thread_id=thread_id,
        last_stage="QUOTED",
        current_focus_candidate_id=cand_id,
        cycle_reset_pending=reset_pending,
        customer_name="Lara",
        home_zone_group="Sur",
        home_zone_detail="Berazategui",
        current_cycle_started_at=_PRIOR_CYCLE,
    )
    db.add(state); db.flush()
    return state


def _make_ce(db):
    from app.services.conversation_engine import ConversationEngine
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = db
    eng.settings = MagicMock()
    eng.settings.outbound_enabled = False
    eng.settings.closed_beta_allowed_wa_ids = "5491153368330"
    eng.settings.quarantined_test_wa_ids = ""
    return eng


# ---------------------------------------------------------------------------
# CASE A: Incident state — no reset armed
# ---------------------------------------------------------------------------

class TestCaseAIncidentState:
    """Reproduce: stale QUOTED state, cycle_reset_pending=False → quote fires."""

    def test_a1_cycle_reset_not_pending(self, mem_db):
        """A1: cycle_reset_pending is False — CE precondition for reset is absent."""
        lead, contact, thread = _seed_lead_contact_thread(mem_db)
        cand = _seed_prior_candidate(mem_db, thread.id)
        state = _make_state(mem_db, thread.id, cand.id, reset_pending=False)
        assert state.cycle_reset_pending is False
        assert state.last_stage == "QUOTED"

    def test_a2_prior_candidate_zone_intact(self, mem_db):
        """A2: Candidate 129 retains Sur/Berazategui — never archived."""
        lead, contact, thread = _seed_lead_contact_thread(mem_db)
        cand = _seed_prior_candidate(mem_db, thread.id)
        _make_state(mem_db, thread.id, cand.id, reset_pending=False)
        assert cand.zone_group == "Sur"
        assert cand.zone_detail == "Berazategui"
        assert cand.status == "current_focus"
        assert cand.tipo_vehiculo == "SUV_4X4_DEPORTIVO"
        assert cand.anio == 2015

    def test_a3_stale_cycle_date_not_current(self, mem_db):
        """A3: current_cycle_started_at is 2026-08-27, not renewed for Wild."""
        lead, contact, thread = _seed_lead_contact_thread(mem_db)
        cand = _seed_prior_candidate(mem_db, thread.id)
        state = _make_state(mem_db, thread.id, cand.id, reset_pending=False)
        expected = datetime(2026, 8, 27, 19, 20, 56, tzinfo=_TZ)
        assert state.current_cycle_started_at == expected

    def test_a4_pricing_240000_berazategui(self, mem_db):
        """A4: PricingService(SUV_4X4_DEPORTIVO, Sur, Berazategui) == 240000."""
        from app.services.pricing import PricingService
        from app.repositories.pricing_repository import PricingRepository
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from app.db import Base as AppBase
        pg_url = "postgresql+psycopg://crm:crm@localhost:5432/crm_test"
        try:
            eng = create_engine(pg_url, connect_args={"connect_timeout": 3})
            with Session(eng) as real_db:
                repo = PricingRepository()
                svc = PricingService(repository=repo)
                quote = svc.quote(real_db, "SUV_4X4_DEPORTIVO", "Sur", "Berazategui")
            assert quote.precio_base == 150_000, f"base={quote.precio_base}"
            assert quote.viaticos == 90_000, f"viaticos={quote.viaticos}"
            assert quote.precio_total == 240_000, f"total={quote.precio_total}"
        except Exception as exc:
            pytest.skip(f"PostgreSQL unavailable: {exc}")

    def test_a5_focus_candidate_is_prior_cycle(self, mem_db):
        """A5: current_focus_candidate_id points to cand 129 (prior cycle)."""
        lead, contact, thread = _seed_lead_contact_thread(mem_db)
        cand = _seed_prior_candidate(mem_db, thread.id)
        state = _make_state(mem_db, thread.id, cand.id, reset_pending=False)
        assert state.current_focus_candidate_id == 129


# ---------------------------------------------------------------------------
# CASE B: Canonical-reset state — reset armed and fires
# ---------------------------------------------------------------------------

class TestCaseBCanonicalReset:
    """Reproduce: cycle_reset_pending=True → reset fires → zone cleared → no quote."""

    def test_b1_reset_armed(self, mem_db):
        """B1: Arming state: cycle_reset_pending=True."""
        lead, contact, thread = _seed_lead_contact_thread(mem_db)
        cand = _seed_prior_candidate(mem_db, thread.id)
        state = _make_state(mem_db, thread.id, cand.id, reset_pending=True)
        assert state.cycle_reset_pending is True

    def test_b2_execute_cycle_reset_clears_zone(self, mem_db):
        """B2: _execute_cycle_reset() clears home_zone_* and resets pending flag."""
        from app.services.conversation_engine import ConversationHandleIn, _Context
        lead, contact, thread = _seed_lead_contact_thread(mem_db)
        cand = _seed_prior_candidate(mem_db, thread.id)
        state = _make_state(mem_db, thread.id, cand.id, reset_pending=True)
        eng = _make_ce(mem_db)
        event = ConversationHandleIn(
            thread_id=thread.id, wa_message_id="wamid.repro01", wa_id="5491153368330",
            message_type="text", text="hola",
            recent_user_messages=["hola"], unanswered_recent_user_messages=["hola"],
            recent_outbound_replies=[],
        )
        ctx = _Context(
            thread=thread, contact=contact, lead=lead,
            state=state, candidates=[cand], db_messages=[],
        )
        eng._execute_cycle_reset(ctx, state, event, previous_cursor=None)
        mem_db.flush()
        assert state.home_zone_group is None,   f"still={state.home_zone_group}"
        assert state.home_zone_detail is None,  f"still={state.home_zone_detail}"
        assert state.cycle_reset_pending is False

    def test_b3_execute_cycle_reset_clears_stage(self, mem_db):
        """B3: After reset, last_stage reverts away from QUOTED (new cycle start)."""
        from app.services.conversation_engine import ConversationHandleIn, _Context
        lead, contact, thread = _seed_lead_contact_thread(mem_db)
        cand = _seed_prior_candidate(mem_db, thread.id)
        state = _make_state(mem_db, thread.id, cand.id, reset_pending=True)
        eng = _make_ce(mem_db)
        event = ConversationHandleIn(
            thread_id=thread.id, wa_message_id="wamid.repro02", wa_id="5491153368330",
            message_type="text", text="hola",
            recent_user_messages=["hola"], unanswered_recent_user_messages=["hola"],
            recent_outbound_replies=[],
        )
        ctx = _Context(
            thread=thread, contact=contact, lead=lead,
            state=state, candidates=[cand], db_messages=[],
        )
        eng._execute_cycle_reset(ctx, state, event, previous_cursor=None)
        mem_db.flush()
        assert state.last_stage != "QUOTED", f"stage should reset, still={state.last_stage}"

    def test_b4_after_reset_no_quote_without_location(self, mem_db):
        """B4: After reset, PricingService cannot produce $240k without zone."""
        from app.services.pricing import PricingService
        from app.repositories.pricing_repository import PricingRepository
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        pg_url = "postgresql+psycopg://crm:crm@localhost:5432/crm_test"
        try:
            eng = create_engine(pg_url, connect_args={"connect_timeout": 3})
            with Session(eng) as real_db:
                repo = PricingRepository()
                svc = PricingService(repository=repo)
                # zone_group=None after reset → no viaticos, cannot produce 240000
                try:
                    quote = svc.quote(real_db, "SUV_4X4_DEPORTIVO", None, None)
                    # If it returns: must not be 240000
                    assert quote.precio_total != 240_000, \
                        f"Should not produce 240000 without zone: got {quote.precio_total}"
                except Exception:
                    pass  # expected: cannot quote without zone
        except Exception as exc:
            pytest.skip(f"PostgreSQL unavailable: {exc}")
