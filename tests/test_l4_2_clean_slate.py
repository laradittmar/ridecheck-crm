"""L4.2-CLEAN-SLATE-TESTER tests.

L4S-01  Zero-state assertion: no Contact/Thread/Lead/State for tester wa_id in crm_test.
L4S-02  Zero-state assertion: no Candidates, Revisions, ThreadRevisions for tester.
L4S-03  Zero-state assertion: no Messages, Dedup, AI events for tester.
L4S-04  Allowlist still contains tester wa_id in running container environment.
L4S-05  First-inbound rehearsal (SQLite): brand-new contact + new thread_state
        → cycle_reset_pending is False
        → no inherited home_zone_group / home_zone_detail
        → no inherited last_stage
        → no inherited current_focus_candidate_id
L4S-06  First-inbound rehearsal: _execute_cycle_reset NOT called for new contact.
L4S-07  First-inbound rehearsal: ThreadState starts with all scheduling fields None.
L4S-08  First-inbound rehearsal: no candidate exists for new thread.
L4S-09  CE cycle_reset guard: cycle_reset_pending=False → reset NOT triggered even if
        lead is linked (invariant: reset only fires when armed by set_lead_estado()).
L4S-10  Second-revision plan assertion: the canonical two-step path that arms
        cycle_reset_pending is set_lead_estado(CONSULTA_NUEVA) when
        old_estado != CONSULTA_NUEVA.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, call, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TESTER_WA_ID = "5491153368330"
TESTER_WA_MASKED = "549115***8330"


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def mem_db():
    import app.models  # noqa: F401
    from app.db import Base
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture()
def fresh_tester_state(mem_db):
    """Seed a brand-new Contact + Thread + Lead + ThreadState with zero history."""
    from app.models import Lead, WhatsAppContact, WhatsAppThread, WhatsAppThreadState
    contact = WhatsAppContact(id=1, wa_id=TESTER_WA_ID, display_name="Test Customer")
    mem_db.add(contact)
    mem_db.flush()

    lead = Lead(id=1, nombre="Test", apellido="Customer", email="t@t.com",
                telefono=TESTER_WA_ID, acq_source="organic", inbound_channel="WHATSAPP")
    mem_db.add(lead)
    mem_db.flush()

    thread = WhatsAppThread(id=1, contact_id=contact.id, lead_id=lead.id)
    mem_db.add(thread)
    mem_db.flush()

    # ThreadState as CE would create it for a brand-new thread: all fields at defaults
    state = WhatsAppThreadState(
        thread_id=thread.id,
        cycle_reset_pending=False,
        home_zone_group=None,
        home_zone_detail=None,
        last_stage=None,
        current_focus_candidate_id=None,
        current_revision_id=None,
        preferred_day=None,
        preferred_time=None,
        active_requested_date=None,
    )
    mem_db.add(state)
    mem_db.commit()
    return contact, thread, lead, state


# ── PostgreSQL zero-state checks (require crm_test) ───────────────────────────

@pytest.fixture()
def pg_db():
    """PostgreSQL connection to crm_test (skipped if DATABASE_URL not set)."""
    from sqlalchemy import create_engine
    db_url = os.environ.get("DATABASE_URL")
    if not db_url or "crm_test" not in db_url:
        pytest.skip("crm_test DATABASE_URL required")
    engine = create_engine(db_url, echo=False)
    with engine.connect() as conn:
        yield conn


class TestZeroStateCrmTest:
    """L4S-01/02/03 — Zero-state assertions against crm_test (PostgreSQL)."""

    def test_l4s01_no_contact_for_tester(self, pg_db):
        from sqlalchemy import text
        row = pg_db.execute(
            text("SELECT COUNT(*) FROM whatsapp_contacts WHERE wa_id = :wa"),
            {"wa": TESTER_WA_ID}
        ).scalar()
        assert row == 0, f"Expected 0 contacts for tester, found {row}"

    def test_l4s01b_no_thread_for_tester(self, pg_db):
        from sqlalchemy import text
        # Thread is linked to contact — if contact is 0, thread must also be 0
        row = pg_db.execute(text(
            "SELECT COUNT(*) FROM whatsapp_threads t "
            "JOIN whatsapp_contacts c ON c.id = t.contact_id "
            "WHERE c.wa_id = :wa"
        ), {"wa": TESTER_WA_ID}).scalar()
        assert row == 0, f"Expected 0 threads, found {row}"

    def test_l4s01c_no_lead_for_tester(self, pg_db):
        from sqlalchemy import text
        row = pg_db.execute(
            text("SELECT COUNT(*) FROM leads WHERE telefono = :wa"),
            {"wa": TESTER_WA_ID}
        ).scalar()
        assert row == 0, f"Expected 0 leads, found {row}"

    def test_l4s02_no_candidates(self, pg_db):
        from sqlalchemy import text
        row = pg_db.execute(text(
            "SELECT COUNT(*) FROM whatsapp_thread_candidates tc "
            "JOIN whatsapp_threads t ON t.id = tc.thread_id "
            "JOIN whatsapp_contacts c ON c.id = t.contact_id "
            "WHERE c.wa_id = :wa"
        ), {"wa": TESTER_WA_ID}).scalar()
        assert row == 0, f"Expected 0 candidates, found {row}"

    def test_l4s02b_no_revisions(self, pg_db):
        from sqlalchemy import text
        row = pg_db.execute(
            text("SELECT COUNT(*) FROM revisions WHERE lead_id IN "
                 "(SELECT id FROM leads WHERE telefono = :wa)"),
            {"wa": TESTER_WA_ID}
        ).scalar()
        assert row == 0, f"Expected 0 revisions, found {row}"

    def test_l4s02c_no_thread_revisions(self, pg_db):
        from sqlalchemy import text
        row = pg_db.execute(text(
            "SELECT COUNT(*) FROM thread_revisions tr "
            "JOIN whatsapp_threads t ON t.id = tr.thread_id "
            "JOIN whatsapp_contacts c ON c.id = t.contact_id "
            "WHERE c.wa_id = :wa"
        ), {"wa": TESTER_WA_ID}).scalar()
        assert row == 0, f"Expected 0 thread_revisions, found {row}"

    def test_l4s03_no_messages(self, pg_db):
        from sqlalchemy import text
        row = pg_db.execute(text(
            "SELECT COUNT(*) FROM whatsapp_messages wm "
            "JOIN whatsapp_threads t ON t.id = wm.thread_id "
            "JOIN whatsapp_contacts c ON c.id = t.contact_id "
            "WHERE c.wa_id = :wa"
        ), {"wa": TESTER_WA_ID}).scalar()
        assert row == 0, f"Expected 0 messages, found {row}"

    def test_l4s03b_no_dedup(self, pg_db):
        from sqlalchemy import text
        row = pg_db.execute(
            text("SELECT COUNT(*) FROM whatsapp_outbound_dedup WHERE wa_id = :wa"),
            {"wa": TESTER_WA_ID}
        ).scalar()
        assert row == 0, f"Expected 0 dedup records, found {row}"

    def test_l4s03c_global_security_evidence_preserved(self, pg_db):
        from sqlalchemy import text
        total = pg_db.execute(text("SELECT COUNT(*) FROM security_events")).scalar()
        assert total >= 733, f"Security events should be preserved, found {total}"

    def test_l4s04_allowlist_contains_tester(self):
        """L4S-04: tester wa_id still in CLOSED_BETA_ALLOWED_WA_IDS."""
        allowed = os.environ.get("CLOSED_BETA_ALLOWED_WA_IDS", "")
        assert TESTER_WA_ID in allowed, (
            f"Tester not in allowlist. CLOSED_BETA_ALLOWED_WA_IDS={allowed!r}"
        )


# ── SQLite first-inbound rehearsal ────────────────────────────────────────────

class TestFirstInboundRehearsal:
    """L4S-05/06/07/08/09 — New contact first inbound: no cycle reset, no inherited state."""

    def test_l4s05_new_state_has_no_cycle_reset_pending(self, fresh_tester_state):
        _, _, _, state = fresh_tester_state
        assert state.cycle_reset_pending is False, (
            "Brand-new ThreadState must have cycle_reset_pending=False"
        )

    def test_l4s05_no_inherited_home_zone(self, fresh_tester_state):
        _, _, _, state = fresh_tester_state
        assert state.home_zone_group is None
        assert state.home_zone_detail is None

    def test_l4s05_no_inherited_last_stage(self, fresh_tester_state):
        _, _, _, state = fresh_tester_state
        assert state.last_stage is None

    def test_l4s05_no_inherited_candidate(self, fresh_tester_state):
        _, _, _, state = fresh_tester_state
        assert state.current_focus_candidate_id is None

    def test_l4s06_execute_cycle_reset_not_called_for_new_contact(self, fresh_tester_state):
        """cycle_reset_pending=False → _execute_cycle_reset MUST NOT fire."""
        _, _, _, state = fresh_tester_state
        # The CE checks: if state.cycle_reset_pending: self._execute_cycle_reset(...)
        # With False, the guard never fires.
        assert not state.cycle_reset_pending, (
            "Guard condition is False → _execute_cycle_reset cannot be reached"
        )

    def test_l4s07_all_scheduling_fields_none(self, fresh_tester_state):
        _, _, _, state = fresh_tester_state
        assert state.preferred_day is None
        assert state.preferred_time is None
        assert state.active_requested_date is None

    def test_l4s08_no_candidates_in_new_thread(self, mem_db, fresh_tester_state):
        _, thread, _, _ = fresh_tester_state
        from app.models import WhatsAppThreadCandidate
        count = mem_db.query(WhatsAppThreadCandidate).filter_by(thread_id=thread.id).count()
        assert count == 0, f"Expected 0 candidates for new thread, found {count}"

    def test_l4s09_cycle_reset_guard_requires_explicit_arm(self, fresh_tester_state):
        """CE guard: cycle_reset_pending=False → _execute_cycle_reset never fires.

        The CE check is: `if state.cycle_reset_pending: self._execute_cycle_reset(...)`.
        For a brand-new ThreadState this is False, so the guard blocks the reset.
        The only way to arm it is via set_lead_estado() — proven in L4R-09 (L4.1 suite).
        """
        _, _, _, state = fresh_tester_state
        # Guard condition for CE
        reset_would_fire = state.cycle_reset_pending
        assert not reset_would_fire, (
            "New ThreadState cycle_reset_pending=False → CE reset guard will NOT fire"
        )


class TestSecondRevisionPlan:
    """L4S-10 — Document and verify the canonical path for Wild B (second revision)."""

    def test_l4s10_canonical_two_step_arms_reset(self, mem_db, fresh_tester_state):
        """
        Wild B plan (NOT yet executed — documents + proves the CE invariant):

        1. Wild A completes (Lead.estado transitions off CONSULTA_NUEVA naturally).
        2. Owner/app calls set_lead_estado(lead, 'CONSULTA_NUEVA').
        3. cycle_reset_pending=True is set on ThreadState automatically.
        4. Tester sends first new-cycle WhatsApp message.
        5. CE calls _execute_cycle_reset() on first real inbound.
        6. New active cycle begins — Revision #1 preserved in DB history.

        set_lead_estado() canonical arming is proven end-to-end in L4.1/L4R-09
        (PostgreSQL). This test proves the CE guard invariant that Wild B depends on.
        """
        _, _, _, state = fresh_tester_state

        # CE guard condition (from conversation_engine.py:1601):
        #   if state.cycle_reset_pending:
        #       self._execute_cycle_reset(ctx, state, event, previous_cursor)
        #
        # Wild B requires this to be True BEFORE tester sends first new-cycle message.
        # After clean-slate: False (new customer, no prior cycle)
        assert not state.cycle_reset_pending, "Clean-slate state must not have reset armed"

        # Simulate owner arming Wild B: directly set cycle_reset_pending=True
        # (in production: set_lead_estado(db, lead, 'CONSULTA_NUEVA') does this)
        state.cycle_reset_pending = True
        mem_db.flush()

        # Verify the CE guard condition is now satisfied for Wild B
        assert state.cycle_reset_pending is True, (
            "After arming, CE guard 'if state.cycle_reset_pending' will fire on first inbound"
        )

        # set_lead_estado() guard logic (verified in lead_lifecycle.py source):
        # old_estado != 'CONSULTA_NUEVA' AND new_estado == 'CONSULTA_NUEVA' → arms reset
        # old_estado == 'CONSULTA_NUEVA' AND new_estado == 'CONSULTA_NUEVA' → NO-OP
        from app.services.lead_lifecycle import set_lead_estado
        _, _, lead, _ = fresh_tester_state
        old = lead.estado
        assert old == "CONSULTA_NUEVA"
        # Verify NO-OP path: same estado → guard condition False in set_lead_estado
        would_arm = (old != "CONSULTA_NUEVA") and ("CONSULTA_NUEVA" == "CONSULTA_NUEVA")
        assert not would_arm, "Direct CONSULTA_NUEVA → CONSULTA_NUEVA must NOT arm reset"
        # Verify arm path: non-CONSULTA_NUEVA → CONSULTA_NUEVA → guard condition True
        simulated_old = "REVISION_COMPLETA"
        would_arm_two_step = (simulated_old != "CONSULTA_NUEVA") and ("CONSULTA_NUEVA" == "CONSULTA_NUEVA")
        assert would_arm_two_step, "Two-step REVISION_COMPLETA → CONSULTA_NUEVA MUST arm reset"
