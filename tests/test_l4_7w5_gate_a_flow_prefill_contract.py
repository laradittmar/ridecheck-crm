"""L4.7W5-GATE-A — the backend contract a prefilling Flow would need, validated locally.

The published Flow declares no `init-value`, so nothing the back end sends can render as
selected or prefilled. F4 narrowed the option lists, which removed the wrong choice but not
the choosing. Closing it needs a Flow republish — a Gate B decision — so Gate A builds and
proves the whole change without touching Meta.

One fact shapes everything: **APPOINTMENT → DETAILS is client-side `navigate`.** The back end
is never called between those screens, so anything DETAILS needs must leave from APPOINTMENT's
data through the navigate payload. Name and phone therefore become APPOINTMENT keys.

DATE-TIME-01..08 · PHONE-01..05 · NAME-01..04 · FLOW-01..07
"""
from __future__ import annotations

import ast
import hashlib
import json
import pathlib
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

from offer_fixture import (ensure_launch_professional, route_authority_availability,  # noqa: E402
                           seed_live_offer, start_converged_booking, stop_converged_booking)
from app.models import (Lead, ThreadRevision, ViaticosZone, WhatsAppContact, WhatsAppThread,
                        WhatsAppThreadCandidate, WhatsAppThreadState)
from app.schemas.schedule import ScheduleCheckOut, ScheduleSlotOut, ScheduleSlotsOut
from app.services.booking_flow_service import (BookingFlowService, BookingSlotConflictError,
                                               _lead_display_name, _mask_phone,
                                               make_booking_token)

FLOW_DIR = ROOT / "meta" / "flows" / "booking"
PUBLISHED = FLOW_DIR / "PUBLISHED_28104222025943520_v7.3.json"
CANDIDATE = FLOW_DIR / "CANDIDATE_v7.4-prefill.json"
PUBLISHED_SHA = "274038ba234a49fa4f99bae47c00f6cac640e248d56a373d40ae8fba6c1afb15"

#: WILD1-F3-R2, 2026-10-30. `PUBLISHED` above is the PRE-16-SEPTEMBER baseline: the 2026-09-16
#: Gate B Final Certification downloaded the live definition from the Flows API and proved deep
#: structural equality with r3, and the owner re-exported the published JSON from Meta on
#: 2026-10-30 and confirmed r3's structure field by field. `PUBLISHED` keeps its name and its
#: drift check because it is cited by name throughout the certification record; this is the
#: artifact that matches what is live.
PUBLISHED_LIVE = FLOW_DIR / "CANDIDATE_v7.4-prefill-r3.json"
PUBLISHED_LIVE_SHA = "53a3d88f3dd57c9bf3089c97c9edfe83bc907afc428e7ef19e3c1538efb1bc04"

BFS_SOURCE = (ROOT / "backend" / "app" / "services"
              / "booking_flow_service.py").read_text(encoding="utf-8-sig")

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})


@event.listens_for(_engine, "connect")
def _pragmas(conn, _rec):
    conn.execute("PRAGMA foreign_keys=OFF")


app.models.Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

# Convergence Phase 3 (adaptation, whole module): confirm_booking books through
# `BookingAuthority` only — flag ON, a launch professional, and the authority's own
# availability check (routed to `_mock_schedule`'s double). See `offer_fixture`.
_MODULE_PATCHES = []


def setUpModule():
    _MODULE_PATCHES.extend(start_converged_booking())


def tearDownModule():
    stop_converged_booking(_MODULE_PATCHES)


WA_ID = "5491100000001"
FUTURE = date.today() + timedelta(days=3)
SLOTS = ["10:00", "11:00", "12:00"]


def _screens(path):
    return {s["id"]: s for s in json.loads(path.read_text())["screens"]}


def _find(node, pred):
    for c in node.get("children", []) or []:
        if pred(c):
            return c
        hit = _find(c, pred)
        if hit:
            return hit
    return None


class _World(unittest.TestCase):
    def setUp(self):
        with _engine.begin() as conn:
            for tbl in reversed(app.models.Base.metadata.sorted_tables):
                conn.execute(tbl.delete())
        self.db = _Session()
        self.db.add(ViaticosZone(zone_group="CABA", zone_detail="Palermo", viaticos=0))
        contact = WhatsAppContact(wa_id=WA_ID, display_name="perfil_de_whatsapp")
        self.db.add(contact); self.db.flush()
        self.contact = contact
        self.lead = Lead(estado="COTIZACION", flag="ACEPTADO")
        self.db.add(self.lead); self.db.flush()
        self.thread = WhatsAppThread(contact_id=contact.id, lead_id=self.lead.id)
        self.db.add(self.thread); self.db.flush()
        self.token = make_booking_token(self.thread.id)
        self.cand = WhatsAppThreadCandidate(
            thread_id=self.thread.id, marca="Peugeot", modelo="2008", anio=2020,
            tipo_vehiculo="AUTO", zone_group="CABA", zone_detail="Palermo",
            direccion_texto="Av. Santa Fe 1234", status="current_focus")
        self.db.add(self.cand); self.db.flush()
        self.state = WhatsAppThreadState(thread_id=self.thread.id,
                                         flow_booking_token=self.token, needs_human=False)
        self.db.add(self.state)
        ensure_launch_professional(self.db)
        self.db.commit()
        # G3-7P: a hand-set token is no longer an offer. Authority is
        # `whatsapp_booking_offers` and an absent row is a refusal, so a fixture that
        # wants a bookable offer issues one through the authority.
        seed_live_offer(self.db, self.state)
        self.svc = BookingFlowService(self.db)
        self._mock_schedule()

    def tearDown(self):
        self.db.close()

    def _mock_schedule(self, slots=None, valid=True):
        slots = SLOTS if slots is None else slots
        self.svc._sched = MagicMock()
        self.svc._sched.list_slots.return_value = ScheduleSlotsOut(
            preferred_day=FUTURE, business_hours="09:00-18:00", slots=slots)
        self.svc._sched.check.return_value = ScheduleCheckOut(
            valid=valid, suggested_slots=slots, approval_tag="",
            requested_slot=ScheduleSlotOut(start=slots[0] if slots else "10:00", end="11:00"),
            business_hours="09:00-18:00", reasons=[] if valid else ["OCCUPIED"])
        route_authority_availability(self.svc._sched)

    def agree(self, day=None, hhmm="11:00"):
        self.state.preferred_day = (day or FUTURE).isoformat()
        self.state.preferred_time = hhmm
        self.db.commit()

    def confirm_payload(self, **over):
        base = {"booking_token": self.token, "date": FUTURE.isoformat(), "time": "11:00",
                "name": "Juan Pérez", "phone": WA_ID, "email": "", "inspection_address": "Av. 1",
                "seller_name": "", "seller_phone": "", "listing_url": ""}
        base.update(over)
        return base


class TestDateTimeSelection(_World):

    # ── WILD1-F3-R2, 2026-10-30: the six DATE-TIME selection-echo assertions, twice moved
    #
    # ORIGINAL (L4.7W5-GATE-A)  each asserted `data["selected_date"]` / `selected_time"]`.
    # R1 (2026-10-29)           each asserted the key was ABSENT, on the reading that the
    #                           two keys were init-values for an unpublished candidate and
    #                           the live Flow was `PUBLISHED_…v7.3.json`, which declares
    #                           neither.
    # R2 (2026-10-30)           the original assertions are restored, because R1's premise
    #                           was false. The owner exported the published definition for
    #                           asset 28104222025943520 from Meta and it declares both keys
    #                           on APPOINTMENT: it is r3's structure, not v7.3's. R1 had
    #                           stopped supplying five values a SHIPPED screen declares.
    #
    # WHAT R2 KEEPS FROM R1     the option-list assertions R1 added are strictly additional
    #                           and all still true, so they stay. Every test below now
    #                           asserts BOTH halves: the echoed selection AND the list it
    #                           must be a member of. That pairing is the actual guarantee —
    #                           an id that is not in its own data-source is an id the Flow
    #                           cannot render as chosen, which is what GATE A was for.
    #
    # NOT A ROLLBACK OF BEHAVIOUR  `init-value` is still absent from every Dropdown in every
    #                           artifact, and `scripts/validate_flow_json.py` still forbids
    #                           it. `selected_date`/`selected_time` are declared and bound
    #                           nowhere (flow_contract.APPOINTMENT_CARRIED). They are
    #                           supplied because the published Flow declares them, not
    #                           because they preselect anything.

    def test_date_time_01_an_agreed_valid_slot_is_selected(self):
        """DATE-TIME-01 — both selections must be IDs present in their own data-source,
        which is what makes the Flow render them chosen rather than silently ignore them."""
        self.agree()
        data = self.svc.handle_init(self.token)["data"]
        self.assertEqual(data["selected_date"], FUTURE.isoformat())
        self.assertEqual(data["selected_time"], "11:00")
        self.assertIn(data["selected_date"], [i["id"] for i in data["date"]])
        self.assertIn(data["selected_time"], [i["id"] for i in data["time"]])
        # R1's addition: the list is narrowed to exactly the agreed slot
        self.assertEqual([i["id"] for i in data["date"]], [FUTURE.isoformat()])
        self.assertEqual([i["id"] for i in data["time"]], ["11:00"])
        self.assertTrue(data["is_date_enabled"])
        self.assertTrue(data["is_time_enabled"])

    def test_date_time_02_day_only_never_preselects_a_time(self):
        """DATE-TIME-02 — choosing the earliest for them would be us deciding."""
        data = self.svc.handle_date_selected(self.token, FUTURE.isoformat())["data"]
        self.assertEqual(data["selected_date"], FUTURE.isoformat())
        self.assertEqual(data["selected_time"], "")
        self.assertTrue(data["time"], "real times are still offered")
        self.assertGreater(len(data["time"]), 1,
                           "a day-only request must leave the time choice open")

    def test_date_time_03_open_picker_selects_nothing(self):
        data = self.svc.handle_init(self.token)["data"]
        self.assertEqual(data["selected_date"], "")
        self.assertEqual(data["selected_time"], "")
        self.assertEqual(data["time"], [], "no times before a day is chosen")
        self.assertFalse(data["is_time_enabled"])
        self.assertTrue(data["date"])

    def test_date_time_04_an_agreed_time_that_went_stale_is_not_selected(self):
        """DATE-TIME-04 — the agreement is honoured only while it is still true."""
        self.agree(hhmm="11:00")
        self._mock_schedule(slots=["15:00", "16:00"])      # 11:00 is gone
        data = self.svc.handle_init(self.token)["data"]
        self.assertNotEqual(data["selected_time"], "11:00",
                            "a slot that vanished must not come back selected")
        self.assertEqual(data["selected_time"], "")
        # the narrowed one-option list is abandoned too: INIT falls back to the normal
        # picker, where times load only after a date is chosen
        self.assertEqual([i["id"] for i in data["time"]], [])
        self.assertTrue(data["date"], "dates are still offered")
        later = self.svc.handle_date_selected(self.token, FUTURE.isoformat())["data"]
        self.assertEqual([i["id"] for i in later["time"]], ["15:00", "16:00"])
        self.assertEqual(later["selected_time"], "", "no dead slot is reselected")
        self.assertNotIn("11:00", [i["id"] for i in later["time"]])

    def test_date_time_05_date_selected_refresh_keeps_the_day(self):
        data = self.svc.handle_date_selected(self.token, FUTURE.isoformat())["data"]
        self.assertEqual(data["selected_date"], FUTURE.isoformat())
        self.assertEqual([i["id"] for i in data["time"]], SLOTS)
        self.assertTrue(data["is_time_enabled"])

    def test_date_time_06_a_slot_conflict_carries_no_stale_selection(self):
        """DATE-TIME-06 — the time that just vanished must not come back selected."""
        self._mock_schedule(valid=False)
        with self.assertRaises(BookingSlotConflictError) as ctx:
            self.svc.handle_confirm_booking(self.token, self.confirm_payload())
        data = ctx.exception.refreshed_data["data"]
        self.assertEqual(data["selected_time"], "")
        self.assertIn("selected_date", data)
        # R1's addition, and the point of the whole correction: the reason is VISIBLE
        self.assertTrue(data["has_refusal"], "the refusal must be visible")
        self.assertTrue(data["slot_conflict_message"])

    def test_date_time_07_r2_a_day_the_backend_does_not_offer_is_never_echoed(self):
        """DATE-TIME-07 — WILD1-F3-R2. The echo is derived, not copied.

        R1's stated worry about restoring the echo was a stale selection from another offer
        or cycle coming back as a preselection. This asserts the guard that prevents it:
        `selected_date` is the request's value ONLY if the backend's own `date_items` — read
        from `ScheduleService` for this context — contains it. A day outside the horizon,
        a day with no slots, or a replayed day from a previous cycle collapses to "".
        """
        stale = (FUTURE + timedelta(days=400)).isoformat()      # far outside the horizon
        data = self.svc.handle_date_selected(self.token, stale)["data"]
        self.assertEqual(data["selected_date"], "",
                         "a day the backend never offered was echoed back as chosen")
        self.assertNotIn(stale, [i["id"] for i in data["date"]])
        self.assertEqual(data["selected_time"], "")

    def test_date_time_08_confirm_still_revalidates(self):
        """DATE-TIME-08 — prefill is UX; safety is unchanged.

        Convergence Phase 3 adaptation: OLD — a source scan for `self._sched.check(check_in)`
        in the Data Exchange writer. NEW — behaviour: the booking authority revalidates the
        SUBMITTED slot itself, and an invalid one books nothing. WHY: the in-session writer
        that called the check is deleted; revalidation is the authority's (B-7)."""
        self._mock_schedule(valid=False)
        with self.assertRaises(BookingSlotConflictError):
            self.svc.handle_confirm_booking(self.token, self.confirm_payload())
        self.svc._sched.check.assert_called_once()
        self.assertEqual(self.db.query(ThreadRevision).count(), 0)

class TestCanonicalPhone(_World):

    def test_phone_01_canonical_identity_is_supplied_not_requested(self):
        """PHONE-01 — restored by WILD1-F3-R2 (see the note on TestDateTimeSelection).

        The published DETAILS screen renders `${data.contact_phone_display}` under
        "Te contactamos a este WhatsApp:" and asks the customer for no number of their own,
        so these two values are what that screen reads. R1 withheld them, which would have
        left a shipped caption with nothing under it.
        """
        data = self.svc.handle_init(self.token)["data"]
        self.assertEqual(data["contact_phone"], WA_ID)
        self.assertEqual(data["contact_phone_display"], "…0001")
        self.assertEqual(_mask_phone(WA_ID), "…0001")

    def test_phone_01b_r2_the_phone_is_digits_only_and_from_the_contact(self):
        """PHONE-01b — WILD1-F3-R2. The value is derived from `ctx.contact.wa_id`, digits
        only, so a formatted or punctuated identity cannot reach a payload the operator's
        screen later reads."""
        self.contact.wa_id = "+54 9 11 0000-0001"
        self.db.commit()
        data = self.svc.handle_init(self.token)["data"]
        self.assertEqual(data["contact_phone"], "5491100000001")
        self.assertTrue(data["contact_phone"].isdigit())
        self.assertEqual(data["contact_phone_display"], "…0001")

    def test_phone_02_a_tampered_payload_phone_cannot_replace_wa_id(self):
        """PHONE-02 — enforced server-side, not by the absence of a UI field."""
        out = self.svc.handle_prepare_summary(
            self.token, self.confirm_payload(phone="5491199999999"))
        self.assertNotIn("99999999", json.dumps(out))
        self.assertEqual(out["data"]["phone"], WA_ID)

    def test_phone_03_the_booking_records_canonical_identity(self):
        self.svc.handle_confirm_booking(self.token, self.confirm_payload(
            phone="5491199999999"))
        rev = self.db.query(ThreadRevision).one()
        self.assertEqual(rev.buyer_phone, WA_ID)

    def test_phone_05_no_full_number_is_rendered_on_screen(self):
        """PHONE-05 — the screen shows a mask; the full value travels as data only.

        WILD1-F3-R2: R1 replaced this with "the number appears nowhere in the payload",
        which was true only because R1 had stopped sending it. The published Flow carries
        `contact_phone` in two action payloads and renders ONLY the mask, so the original
        assertion is the one that matches the published asset: nothing drawn on a screen
        contains the full number, and that is asserted here against the artifact as well as
        against the payload.
        """
        data = self.svc.handle_init(self.token)["data"]
        self.assertNotIn(WA_ID, data["contact_phone_display"])
        self.assertTrue(_mask_phone(WA_ID).startswith("…"))
        self.assertEqual(_mask_phone(None), "")
        # and no COMPONENT in the published Flow binds the unmasked key — only actions do
        import re as _re
        for sid, s in _screens(PUBLISHED_LIVE).items():
            def visible_text(node, out):
                if isinstance(node, dict):
                    if node.get("type") in ("TextBody", "TextHeading", "TextCaption",
                                            "TextSubheading", "RichText"):
                        out.append(json.dumps(node.get("text"), ensure_ascii=False))
                    for k, v in node.items():
                        if k not in ("on-click-action", "on-select-action"):
                            visible_text(v, out)
                elif isinstance(node, list):
                    for v in node:
                        visible_text(v, out)
                return out
            drawn = " ".join(visible_text(s.get("layout", {}), []))
            self.assertNotIn("${data.contact_phone}", drawn,
                             f"{sid} draws the unmasked number on screen")

    def test_the_candidate_flow_has_no_editable_phone_input(self):
        for s in _screens(CANDIDATE).values():
            def check(node):
                if isinstance(node, dict):
                    if node.get("type") == "TextInput":
                        self.assertNotEqual(node.get("name"), "phone",
                                            "a phone input is back on a screen")
                    for v in node.values():
                        check(v)
                elif isinstance(node, list):
                    for v in node:
                        check(v)
            check(s.get("layout") or {})


class TestNamePrefill(_World):

    # ── WILD1-F3-R2: NAME-01/02/04 restored ────────────────────────────────────────
    #
    # R1 rewrote these to read `_lead_display_name(self.lead)` and assert
    # `customer_name` was absent from the payload. The published DETAILS screen declares
    # `customer_name`, so the key belongs in the payload; the helper's own behaviour stays
    # asserted alongside it rather than instead of it.

    def test_name_01_a_lead_name_is_offered(self):
        self.lead.nombre, self.lead.apellido = "Juan", "Pérez"
        self.db.commit()
        self.assertEqual(_lead_display_name(self.lead), "Juan Pérez")
        self.assertEqual(self.svc.handle_init(self.token)["data"]["customer_name"],
                         "Juan Pérez")

    def test_name_02_no_lead_name_invents_nothing(self):
        self.assertEqual(_lead_display_name(self.lead), "")
        self.assertEqual(self.svc.handle_init(self.token)["data"]["customer_name"], "")

    def test_name_04_whatsapp_display_name_is_not_verified_identity(self):
        """NAME-04 — display_name is a profile label the customer sets; it is not a name we
        may put on a booking."""
        data = self.svc.handle_init(self.token)["data"]
        self.assertEqual(data["customer_name"], "")
        self.assertNotIn("perfil_de_whatsapp", json.dumps(data))
        self.assertEqual(_lead_display_name(self.lead), "")

    def test_name_03_a_correction_survives_to_summary(self):
        self.lead.nombre = "Juan"; self.db.commit()
        out = self.svc.handle_prepare_summary(self.token, self.confirm_payload(name="Juan Carlos Pérez"))
        self.assertEqual(out["data"]["name"], "Juan Carlos Pérez")
        self.svc.handle_confirm_booking(self.token, self.confirm_payload(name="Juan Carlos Pérez"))
        self.assertEqual(self.db.query(ThreadRevision).one().buyer_name, "Juan Carlos Pérez")

    def test_the_candidate_binds_the_name_input(self):
        det = _screens(CANDIDATE)["DETAILS"]
        name_in = _find(det["layout"],
                        lambda c: c.get("type") == "TextInput" and c.get("name") == "name")
        self.assertEqual(name_in["init-value"], "${data.customer_name}")


class TestFlowContract(unittest.TestCase):

    def test_flow_07_the_published_baseline_is_byte_identical(self):
        """FLOW-07 — neither preserved artifact may drift.

        WILD1-F3-R2: `PUBLISHED` is the preserved PRE-16-SEPTEMBER baseline and
        `PUBLISHED_LIVE` (r3) is the structure of what is published now. Both are pinned:
        the first because the record cites it, the second because it is the rollback
        artifact and the baseline every target is derived from.
        """
        self.assertEqual(hashlib.sha256(PUBLISHED_LIVE.read_bytes()).hexdigest(),
                         PUBLISHED_LIVE_SHA, "the live-structure artifact drifted")
        self.assertEqual(hashlib.sha256(PUBLISHED.read_bytes()).hexdigest(), PUBLISHED_SHA)
        pub = json.loads(PUBLISHED.read_text())
        self.assertEqual(pub["version"], "7.3")
        self.assertEqual(json.dumps(pub).count("init-value"), 0,
                         "the PUBLISHED copy must stay exactly as published")

    def test_flow_02_every_init_value_binds_a_declared_data_key(self):
        """FLOW-02 — a binding that references a key the screen does not declare would be
        rejected by Meta, and an empty string is the valid 'nothing selected'."""
        cand = json.loads(CANDIDATE.read_text())
        for s in cand["screens"]:
            declared = set((s.get("data") or {}).keys())
            def check(node):
                for c in node.get("children", []) or []:
                    iv = c.get("init-value")
                    if isinstance(iv, str) and iv.startswith("${data."):
                        key = iv[len("${data."):-1]
                        self.assertIn(key, declared,
                                      f"{s['id']}.{c.get('name')} binds undeclared {key}")
                    check(c)
            check(s.get("layout") or {})

    def test_flow_01_the_backend_supplies_every_key_the_candidate_declares(self):
        """FLOW-01 — screen data and response contract must agree, in both directions.

        WILD1-F5 — corrected 2026-10-29. This assertion claimed "both directions" and
        checked one: `declared - supplied`, then minus `{slot_conflict_message,
        error_message}`. Those two subtracted names were exactly the keys the backend
        SUPPLIED and no Flow version DECLARED, so the direction that mattered was never
        compared and the two fields that broke the live Flow were named as acceptable. The
        2026-10-29 Wild then showed every booking refusal reaching the handset as Meta's
        generic error.

        It now compares both directions with no exclusion set, against the TARGET artifact
        pinned by path and content hash. The exhaustive per-screen suite lives in
        `test_wild1_booking_flow_gate_and_contract.py`; this keeps Gate A's own check
        honest where it was written.
        """
        from app.services import flow_contract as FC
        target = ROOT / FC.TARGET_FLOW_PATH
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),
                         FC.TARGET_FLOW_SHA256, "target Flow drifted from the contract")
        declared = set(_screens(target)["APPOINTMENT"]["data"].keys())
        supplied = set(FC.appointment_payload())
        self.assertEqual(declared - supplied, set(),
                         f"declared but never supplied: {sorted(declared - supplied)}")
        self.assertEqual(supplied - declared, set(),
                         f"supplied but never declared: {sorted(supplied - declared)}")

    def test_the_navigate_payload_carries_identity_to_details(self):
        """The only bridge: the back end is not called between APPOINTMENT and DETAILS."""
        appt = _screens(CANDIDATE)["APPOINTMENT"]
        payload = _find(appt["layout"],
                        lambda c: c.get("type") == "Footer")["on-click-action"]["payload"]
        for key in ("customer_name", "contact_phone", "contact_phone_display"):
            self.assertEqual(payload[key], "${data.%s}" % key)
            self.assertIn(key, _screens(CANDIDATE)["DETAILS"]["data"])

    def test_flow_03_screen_routing_is_unchanged(self):
        pub, cand = _screens(PUBLISHED), _screens(CANDIDATE)
        self.assertEqual(set(pub), set(cand))
        for sid in pub:
            p_footer = _find(pub[sid]["layout"], lambda c: c.get("type") == "Footer")
            c_footer = _find(cand[sid]["layout"], lambda c: c.get("type") == "Footer")
            self.assertEqual(p_footer["on-click-action"]["name"],
                             c_footer["on-click-action"]["name"], sid)
        self.assertEqual(cand["SUMMARY"].get("terminal"), True)

    def test_flow_04_05_06_booking_authority_is_untouched(self):
        """Convergence Phase 3 adaptation: OLD — `handle_confirm_booking` was the only writer
        of `status="booked"` here and it priced the revision itself. NEW — NOTHING in this
        module writes a booked ThreadRevision: the booking authority does, from a plan the
        confirm path describes (the price included). The FLOW-04 and location guards hold."""
        prep = next(ast.unparse(n) for n in ast.walk(ast.parse(BFS_SOURCE))
                    if isinstance(n, ast.FunctionDef) and n.name == "handle_prepare_summary")
        self.assertNotIn('status="booked"', prep.replace("'", '"'))      # FLOW-04
        writers = {n.name for n in ast.walk(ast.parse(BFS_SOURCE))
                   if isinstance(n, ast.FunctionDef)
                   and 'status="booked"' in ast.unparse(n).replace("'", '"')}
        self.assertEqual(writers, set(), writers)
        self.assertNotIn("ThreadRevision(", BFS_SOURCE.split("def _recipient_for")[0])
        confirm = next(ast.unparse(n) for n in ast.walk(ast.parse(BFS_SOURCE))
                       if isinstance(n, ast.FunctionDef) and n.name == "_confirm_booking")
        self.assertIn("price=PriceRecalculation(", confirm)                # FLOW-06
        self.assertIn("BOOKING_REFUSED_NO_CANONICAL_LOCATION", confirm)

    def test_the_candidate_declares_no_secret_bearing_field(self):
        text = CANDIDATE.read_text()
        for forbidden in ("EAA", "AIza", "sk-", "PRIVATE KEY", "download_url"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
