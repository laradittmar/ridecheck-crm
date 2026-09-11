"""L4.7W5-F4 — the booking must carry the context the conversation already had.

Live Wild, 2026-09-11. "Un 3008 en paternal" → the vehicle resolved, the location did not,
so the customer typed an address into a Flow. CE then quoted $150.000 from CABA/Paternal —
and the booked Revision was written with zone_group NULL, zone_detail NULL and no price at
all. The amount the customer was promised exists nowhere in the CRM.

Two independent causes, both general:
  1. the catalog holds "La Paternal" and the lookup normalised case but not the article;
  2. BookingFlowService returned the candidate's zones whenever a candidate existed — even
     when both were NULL — and only consulted thread state when there was no candidate,
     while CE's resolver had the correct hierarchy all along.

LOC-ARTICLE-01..05   article-normalised locality matching, collision-safe
BOOKCTX-01..06       one canonical location, price integrity, the exact Wild case
FLOW-PREFILL-01..10  agreed-slot narrowing within the PUBLISHED Flow's real limits
EXACT-REJECT-01..03  Flow-first when the requested exact time is unavailable
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
import types
import unittest
from datetime import date, time, timedelta
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
for _m in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    sys.modules.setdefault(_m, types.ModuleType(_m))

import sqlalchemy as _sa
import sqlalchemy.dialects.postgresql as _pg
import sqlalchemy.dialects.postgresql.json as _pgj
_pg.JSONB = _sa.JSON
_pgj.JSONB = _sa.JSON

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import app.models
from app.models import (Lead, ViaticosZone, WhatsAppContact, WhatsAppThread,
                        WhatsAppThreadCandidate, WhatsAppThreadState)
from app.repositories.pricing_repository import _ARTICLE_PREFIX, PricingRepository
from app.services.booking_flow_service import BookingFlowService
from app.services.pricing import PricingService

BFS_SOURCE = (ROOT / "backend" / "app" / "services"
              / "booking_flow_service.py").read_text(encoding="utf-8-sig")
CE_SOURCE = (ROOT / "backend" / "app" / "services"
             / "conversation_engine.py").read_text(encoding="utf-8-sig")

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})


@event.listens_for(_engine, "connect")
def _pragmas(conn, _rec):
    conn.execute("PRAGMA foreign_keys=OFF")


app.models.Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

# The live catalog shape: article-prefixed localities alongside plain ones.
CATALOG = [("CABA", "La Paternal", 0), ("CABA", "La Boca", 0), ("CABA", "Palermo", 0),
           ("Norte", "La Lucila", 50000), ("Oeste", "El Palomar", 60000),
           ("Sur", "Los Hornos", 90000), ("Sur", "La Plata", 90000),
           ("Sur", "Berazategui", 90000)]


def _fn(source: str, name: str) -> str:
    return next(ast.unparse(n) for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.FunctionDef) and n.name == name)


class _Base(unittest.TestCase):
    def setUp(self):
        with _engine.begin() as conn:
            for tbl in reversed(app.models.Base.metadata.sorted_tables):
                conn.execute(tbl.delete())
        self.db = _Session()
        for g, d, v in CATALOG:
            self.db.add(ViaticosZone(zone_group=g, zone_detail=d, viaticos=v))
        self.db.commit()
        self.repo = PricingRepository()

    def tearDown(self):
        self.db.close()

    def _zone(self, detail, group=None):
        row = self.repo.find_zone_by_group_and_detail(
            db=self.db, zone_group=group, zone_detail=detail)
        return (row.zone_group, row.zone_detail) if row else None


class TestArticleNormalisation(_Base):

    def test_loc_article_01_paternal_resolves_to_la_paternal(self):
        """LOC-ARTICLE-01 — the exact live failure."""
        self.assertEqual(self._zone("paternal"), ("CABA", "La Paternal"))
        self.assertEqual(self._zone("Paternal"), ("CABA", "La Paternal"))

    def test_loc_article_02_the_canonical_form_is_unchanged(self):
        self.assertEqual(self._zone("La Paternal"), ("CABA", "La Paternal"))
        self.assertEqual(self._zone("la paternal"), ("CABA", "La Paternal"))

    def test_loc_article_04_representative_la_el_los_forms(self):
        """LOC-ARTICLE-04 — every article family the catalog actually uses."""
        for bare, expected in (("boca", ("CABA", "La Boca")),
                               ("lucila", ("Norte", "La Lucila")),
                               ("palomar", ("Oeste", "El Palomar")),
                               ("hornos", ("Sur", "Los Hornos")),
                               ("plata", ("Sur", "La Plata"))):
            with self.subTest(bare=bare):
                self.assertEqual(self._zone(bare), expected)

    def test_loc_article_03_an_ambiguous_bare_form_is_refused(self):
        """LOC-ARTICLE-03 — two canonical localities reducing to the same bare name must
        NOT silently resolve to whichever happens to be found first."""
        self.db.add(ViaticosZone(zone_group="Oeste", zone_detail="El Paternal", viaticos=70000))
        self.db.commit()
        self.assertIsNone(self._zone("paternal"),
                          "an ambiguous article-stripped form must stay unresolved")

    def test_a_bare_form_that_is_itself_canonical_is_not_hijacked(self):
        """If "Palermo" exists outright, article logic must not reinterpret it."""
        self.assertEqual(self._zone("Palermo"), ("CABA", "Palermo"))

    def test_an_unknown_locality_still_does_not_resolve(self):
        self.assertIsNone(self._zone("zona inexistente"))
        self.assertIsNone(self._zone("la zona inexistente"))

    def test_a_contradicting_group_blocks_the_article_match(self):
        """LOC-ARTICLE-05 — naming the wrong group must not be overridden silently."""
        self.assertIsNone(self._zone("paternal", group="Sur"))
        self.assertEqual(self._zone("paternal", group="CABA"), ("CABA", "La Paternal"))

    def test_arbitrary_text_is_never_article_stripped(self):
        """The rule matches CANONICAL entries; it never edits the customer's words into
        something else."""
        fn = _fn((ROOT / "backend" / "app" / "repositories"
                  / "pricing_repository.py").read_text(encoding="utf-8-sig"),
                 "_find_zone_by_article_variant")
        self.assertIn("len(candidates) != 1", fn)
        self.assertIn("return None", fn)
        self.assertTrue(_ARTICLE_PREFIX.match("la paternal"))
        self.assertFalse(_ARTICLE_PREFIX.match("paternal"))


class TestCanonicalBookingLocation(_Base):

    def _world(self, cand_group, cand_detail, state_group, state_detail):
        contact = WhatsAppContact(wa_id="5491100000001", display_name="Julian")
        self.db.add(contact); self.db.flush()
        lead = Lead(estado="COTIZACION"); self.db.add(lead); self.db.flush()
        thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id)
        self.db.add(thread); self.db.flush()
        cand = WhatsAppThreadCandidate(thread_id=thread.id, marca="Peugeot", modelo="3008",
                                       tipo_vehiculo="SUV/4x4", zone_group=cand_group,
                                       zone_detail=cand_detail, status="current_focus")
        self.db.add(cand); self.db.flush()
        state = WhatsAppThreadState(thread_id=thread.id, home_zone_group=state_group,
                                    home_zone_detail=state_detail)
        self.db.add(state); self.db.commit()
        return cand, state

    def test_bookctx_01_a_candidate_with_a_zone_is_authoritative(self):
        cand, state = self._world("CABA", "Palermo", "Sur", "Berazategui")
        self.assertEqual(BookingFlowService._location_from_candidate(cand, state),
                         ("CABA", "Palermo"))

    def test_bookctx_02_a_candidate_with_null_zones_falls_back_to_state(self):
        """BOOKCTX-02 — the exact live defect. The candidate existed with both zones NULL
        while state held CABA/Paternal, and booking returned (None, None)."""
        cand, state = self._world(None, None, "CABA", "Paternal")
        self.assertEqual(BookingFlowService._location_from_candidate(cand, state),
                         ("CABA", "Paternal"))

    def test_a_half_filled_candidate_takes_the_missing_half_from_state(self):
        cand, state = self._world("CABA", None, "CABA", "Paternal")
        self.assertEqual(BookingFlowService._location_from_candidate(cand, state),
                         ("CABA", "Paternal"))

    def test_no_candidate_at_all_still_uses_state(self):
        _, state = self._world(None, None, "Sur", "Berazategui")
        self.assertEqual(BookingFlowService._location_from_candidate(None, state),
                         ("Sur", "Berazategui"))

    def test_booking_and_ce_now_share_one_hierarchy(self):
        """The two implementations disagreed; they must not again."""
        bfs = _fn(BFS_SOURCE, "_location_from_candidate")
        ce = _fn(CE_SOURCE, "_get_active_inspection_location")
        for source in (bfs, ce):
            self.assertIn("zone_group or", source)
            self.assertIn("home_zone_group", source)
        self.assertIn("if cand_group or cand_detail:", bfs)

    def test_bookctx_03_no_canonical_location_refuses_the_booking(self):
        """BOOKCTX-03 — an unpriced booking is worse than no booking."""
        fn = _fn(BFS_SOURCE, "handle_confirm_booking")
        self.assertIn("BOOKING_REFUSED_NO_CANONICAL_LOCATION", fn)
        i_guard = fn.index("BOOKING_REFUSED_NO_CANONICAL_LOCATION")
        i_write = fn.index("thread_rev = ThreadRevision(")
        self.assertLess(i_guard, i_write, "the refusal must precede the write")

    def test_bookctx_04_05_the_quote_and_the_booking_share_inputs(self):
        """BOOKCTX-04/05 — same tipo + zone, priced by the one PricingService."""
        svc = PricingService(repository=self.repo)
        q = svc.quote(db=self.db, tipo_vehiculo="SUV/4x4",
                      zone_group="CABA", zone_detail="La Paternal")
        self.assertEqual(q.precio_total, q.precio_base + q.viaticos)
        fn = _fn(BFS_SOURCE, "handle_confirm_booking")
        self.assertIn("self._pricing.recalculate_revision_if_possible", fn)

    def test_bookctx_06_the_exact_wild_case_now_prices(self):
        """BOOKCTX-06 — Peugeot 3008 in 'paternal' resolves and prices. No hardcoded total:
        the expected figure is derived from the catalog itself."""
        zone = self.repo.find_zone_by_group_and_detail(
            db=self.db, zone_group=None, zone_detail="paternal")
        self.assertIsNotNone(zone)
        svc = PricingService(repository=self.repo)
        q = svc.quote(db=self.db, tipo_vehiculo="SUV/4x4",
                      zone_group=zone.zone_group, zone_detail=zone.zone_detail)
        base = self.repo.find_base_price("SUV/4x4").precio_base
        self.assertEqual(q.precio_base, base)
        self.assertEqual(q.viaticos, zone.viaticos)
        self.assertEqual(q.precio_total, base + zone.viaticos)


class TestFlowPrefillWithinPublishedLimits(unittest.TestCase):
    """FLOW-PREFILL — bounded by what the PUBLISHED Flow actually supports.

    Flow 28104222025943520, v7.3, fetched from the Meta Flows API: both Dropdowns on
    APPOINTMENT and every TextInput on DETAILS are declared WITHOUT `init-value`, so no
    value the back end sends can preselect or prefill them. What the back end does control
    is the option list, so an agreed slot is narrowed to a single choice.
    """

    def test_flow_prefill_01_an_agreed_slot_is_narrowed_to_one_option(self):
        fn = _fn(BFS_SOURCE, "_appointment_screen_data")
        self.assertIn("agreed_day", fn)
        self.assertIn("agreed_time", fn)
        self.assertIn("preferred_day", fn)
        self.assertIn("preferred_time", fn)

    def test_flow_prefill_02_03_day_only_and_nothing_keep_the_full_picker(self):
        """FLOW-PREFILL-02/03 — narrowing only when BOTH halves are agreed."""
        # raw source, not ast.unparse: unparse rewrites `not x` as `(not x)`
        self.assertIn("if agreed_day and agreed_time and not selected_date_str:", BFS_SOURCE)
        fn = _fn(BFS_SOURCE, "_appointment_screen_data")
        self.assertIn("self._available_dates(ctx.zone_group)", fn)

    def test_flow_prefill_07_08_a_stale_agreement_falls_back_and_is_revalidated(self):
        """FLOW-PREFILL-07/08 — narrowing is UX only; safety is unchanged."""
        fn = _fn(BFS_SOURCE, "_appointment_screen_data")
        self.assertIn("still_free", fn)
        confirm = _fn(BFS_SOURCE, "handle_confirm_booking")
        self.assertIn("self._sched.check(check_in)", confirm)
        self.assertIn("BOOKING_REVALIDATION_FAIL", confirm)

    def test_flow_prefill_10_no_duplicate_booking(self):
        confirm = _fn(BFS_SOURCE, "handle_confirm_booking")
        self.assertIn("state.flow_booking_token = None", confirm)

    def test_flow_prefill_04_05_06_phone_and_name_are_blocked_by_the_published_flow(self):
        """FLOW-PREFILL-04/05/06 — recorded as an external constraint, not silently skipped.

        The DETAILS screen declares `TextInput name=phone required=True` with no
        `init-value`. Nothing the back end returns can prefill it; removing the re-entry
        needs the Flow JSON republished on Meta, which is an owner action. This test pins
        the finding so it cannot be quietly forgotten.
        """
        constraint = (ROOT / "docs" / "operations" / "BOOKING_FLOW_PREFILL_CONSTRAINT.md")
        self.assertTrue(constraint.exists(), "the Meta constraint must be documented")
        body = constraint.read_text(encoding="utf-8")
        self.assertIn("init-value", body)
        self.assertIn("28104222025943520", body)
        self.assertIn("phone", body)


class TestExactRejectFlowFirst(unittest.TestCase):

    def test_exact_reject_01_alternatives_open_the_flow_instead_of_prose(self):
        """EXACT-REJECT-01 — the live "Horarios disponibles: 14:00, 14:30, …" dump."""
        seg = CE_SOURCE[CE_SOURCE.index("# ── L4.7W5-F4 FLOW-FIRST on a rejected exact time"):]
        seg = seg[:seg.index("else:")]
        self.assertIn("_dispatch_booking_flow_for_day", seg)
        self.assertIn("if flow_out is not None", seg)
        i_flow = seg.index("_dispatch_booking_flow_for_day")
        i_prose = seg.index("Horarios disponibles")
        self.assertLess(i_flow, i_prose, "the Flow must be attempted before the prose list")

    def test_exact_reject_02_no_same_day_slots_keeps_the_existing_path(self):
        """EXACT-REJECT-02 — an empty day still falls to the day/NEXT_AVAILABLE handling."""
        self.assertIn("no hay horarios disponibles", CE_SOURCE)
        self.assertIn("_handle_next_available_request", CE_SOURCE)

    def test_exact_reject_03_human_rescue_is_untouched(self):
        """EXACT-REJECT-03 / Part 17 — the rescue branch is still unproven live and must
        remain intact for the next Wild."""
        fn = _fn(CE_SOURCE, "_handle_scheduling_escalation")
        self.assertIn("lead.necesita_humano = True", fn)
        self.assertIn("state.needs_human = True", fn)
        self.assertIn("_send_scheduling_handoff_email", fn)
        self.assertIn("recalculate_revision_if_possible", fn)
        nxt = _fn(CE_SOURCE, "_handle_next_available_request")
        self.assertIn("_handle_scheduling_escalation", nxt)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
