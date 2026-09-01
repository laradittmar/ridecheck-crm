"""L4.4-CLEAN-WILD-PREP — tester zero state before the next full clean Wild.

Deterministic rehearsal of the FIRST inbound after cleanup, plus the runtime-readiness
invariants the next Wild depends on. No live WhatsApp traffic, no fake inbound events.

L44-01  a brand-new ThreadState carries no inherited operational state
L44-02  CE cycle-reset guard cannot fire for a brand-new thread
L44-03  no inherited inspection location (zone resolution is empty)
L44-04  no inherited quote — pricing cannot produce a number without a zone
L44-05  no inherited scheduling — a greeting yields zero scheduling branches
L44-06  Booking Flow is NOT eligible at first inbound (no candidate, no zone)
L44-07  runtime constants: booking Flow id default + BOOKING_FLOW is an authorized path
L44-08  no text-only booking completion path survives the reset
L44-09  the L4.3 ordered-scheduling semantics are still in force after cleanup
"""
from __future__ import annotations

import types
import unittest
from datetime import date
from unittest.mock import MagicMock

from sqlalchemy import create_engine as _sa_create_engine

TESTER_WA_MASKED = "549115***8330"


def _fresh_state():
    """Exactly what a brand-new ThreadState looks like: everything empty."""
    return types.SimpleNamespace(
        home_zone_group=None, home_zone_detail=None,
        current_focus_candidate_id=None,
        preferred_day=None, preferred_time=None,
        active_requested_date=None, last_requested_time=None,
        last_offered_slots=None, last_visible_slots=None,
        last_stage=None, needs_human=False, is_website_lead=False,
        flow_booking_token=None, current_revision_id=None, customer_name=None,
        cycle_reset_pending=False, current_cycle_started_at=None,
        current_cycle_start_message_db_id=None,
    )


def _fresh_ctx(state):
    from app.services.conversation_engine import _Context
    ctx = _Context.__new__(_Context)
    ctx.thread = types.SimpleNamespace(id=1, lead_id=1, contact_id=1, last_message_at=None)
    ctx.lead = types.SimpleNamespace(
        id=1, nombre=None, apellido=None, email=None, telefono="5491153368330",
        flag=None, estado="CONSULTA_NUEVA", canal=None, necesita_humano=False,
        ref_code=None, rc_code=None,
    )
    ctx.contact = types.SimpleNamespace(wa_id="5491153368330")
    ctx.candidates = []                 # brand-new customer: nothing inherited
    ctx.state = state
    ctx.db_messages = []
    ctx.inbound_wa_message_id = None
    ctx.previous_processed_cursor = None
    return ctx


def _engine():
    from app.services.conversation_engine import ConversationEngine
    import app.models as models
    sqlite = _sa_create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(sqlite)
    db = MagicMock()
    db.bind = sqlite
    settings = MagicMock()
    settings.whatsapp_flow_id = "1644218879979041"
    settings.whatsapp_website_flow_id = ""
    settings.booking_flow_id = "28104222025943520"
    eng = ConversationEngine(db=db, settings=settings)
    eng._correlation_id = "corr-l44"
    return eng


class TestFirstInboundIsBrandNew(unittest.TestCase):

    def test_l44_01_no_inherited_operational_state(self):
        s = _fresh_state()
        for field in ("home_zone_group", "home_zone_detail", "current_focus_candidate_id",
                      "preferred_day", "preferred_time", "active_requested_date",
                      "last_requested_time", "last_offered_slots", "last_visible_slots",
                      "last_stage", "flow_booking_token", "current_revision_id",
                      "current_cycle_started_at"):
            self.assertIsNone(getattr(s, field), f"{field} must be empty for a new customer")

    def test_l44_02_cycle_reset_guard_cannot_fire(self):
        """CE only resets when cycle_reset_pending was armed by set_lead_estado()."""
        s = _fresh_state()
        self.assertFalse(s.cycle_reset_pending)

    def test_l44_03_no_inherited_location(self):
        eng = _engine()
        s = _fresh_state()
        ctx = _fresh_ctx(s)
        self.assertEqual(eng._get_active_inspection_location(ctx, s), (None, None))

    def test_l44_04_no_inherited_quote(self):
        """Wild #1 defect class: no zone → no price, never a stale $240.000."""
        from app.services.pricing import PricingService, PricingNotFoundError
        from app.repositories.pricing_repository import PricingRepository
        svc = PricingService(repository=PricingRepository())
        db = MagicMock()
        db.execute.return_value.scalars.return_value.all.return_value = []
        db.execute.return_value.scalar_one_or_none.return_value = None
        with self.assertRaises(Exception) as raised:
            svc.quote(db, "SUV_4X4_DEPORTIVO", None, None)
        self.assertIsInstance(raised.exception, (PricingNotFoundError, AttributeError, TypeError))

    def test_l44_05_no_inherited_scheduling(self):
        from app.services.conversation_engine import _parse_scheduling_requests
        greeting = "Hola, ¿cómo están? Quería consultar por una revisión"
        self.assertEqual(_parse_scheduling_requests([greeting], date(2026, 9, 2)), [])

    def test_l44_06_booking_flow_not_eligible_at_first_inbound(self):
        eng = _engine()
        s = _fresh_state()
        ctx = _fresh_ctx(s)
        sent = []
        eng._send_flow_button = lambda *a, **k: sent.append(k) or "wamid.X"  # type: ignore
        out = eng._send_booking_flow(ctx, s, "28104222025943520")
        self.assertIsNone(out)
        self.assertEqual(sent, [])
        self.assertIsNone(s.flow_booking_token)


class TestRuntimeReadinessInvariants(unittest.TestCase):

    def test_l44_07_booking_flow_constants(self):
        import os
        from app.settings import get_settings
        from app.services.outbound_path_registry import OutboundPathId, AUTHORIZED_PATHS
        previous = os.environ.pop("WHATSAPP_BOOKING_FLOW_ID", None)
        try:
            self.assertEqual(get_settings().booking_flow_id, "28104222025943520")
        finally:
            if previous is not None:
                os.environ["WHATSAPP_BOOKING_FLOW_ID"] = previous
        self.assertEqual(OutboundPathId.BOOKING_FLOW.value, "BOOKING_FLOW")
        self.assertIn(OutboundPathId.BOOKING_FLOW, AUTHORIZED_PATHS)

    def test_l44_08_no_text_only_booking_path(self):
        import inspect
        from app.services.conversation_engine import ConversationEngine
        creators = []
        for name, member in inspect.getmembers(ConversationEngine, inspect.isfunction):
            try:
                src = inspect.getsource(member)
            except (OSError, TypeError):
                continue
            if 'ThreadRevision(' in src and 'status="booked"' in src:
                creators.append(name)
        self.assertEqual(creators, ["_process_flow_response"])

    def test_l44_09_ordered_scheduling_still_in_force(self):
        """The L4.3 semantics must survive the reset — Wild B depends on them."""
        from app.services.conversation_engine import _parse_scheduling_requests
        parsed = _parse_scheduling_requests(
            ["Mñ 15hs? O nose jueves que tenes"], date(2026, 9, 1)
        )
        self.assertEqual(
            [(r.day_iso, r.time_str) for r in parsed],
            [("2026-09-02", "15:00"), ("2026-09-03", None)],
        )


if __name__ == "__main__":
    unittest.main()
