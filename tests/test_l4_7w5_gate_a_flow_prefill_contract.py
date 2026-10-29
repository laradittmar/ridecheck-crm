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

    # ── WILD1-F3-R1, 2026-10-29: the six DATE-TIME selection-echo assertions ────────
    #
    # BEFORE  each asserted `data["selected_date"]` / `data["selected_time"]` in the
    #         APPOINTMENT payload.
    # AFTER   each asserts the key is ABSENT, and keeps the guarantee it was really
    #         protecting: the option LIST.
    # WHY     the two keys were init-values for `CANDIDATE_v7.4-prefill-r3.json`, which has
    #         never been published. Published v7.3 declares both Dropdowns without
    #         `init-value`, so no value the backend sent could preselect anything — the
    #         module's own comment said so while the payload kept sending them. The owner
    #         has excluded the prefill rollout from the November 1 correction, so the keys
    #         are gone from the payload and the assertions follow the behaviour.
    # NOT WEAKENED  every test below still asserts the same observable outcome through the
    #         mechanism that actually works: `date` and `time` carry exactly the options the
    #         customer may pick, narrowed to one when a slot is agreed and still free, and
    #         restored to the full picker when it is not.

    ECHO = ("selected_date", "selected_time")

    def assertNoEcho(self, data):
        for key in self.ECHO:
            self.assertNotIn(key, data,
                             f"{key} is back in the payload — the prefill rollout is "
                             f"deferred (flow_contract.DEFERRED_PREFILL_KEYS)")

    def test_date_time_01_an_agreed_valid_slot_is_narrowed_to_one_option(self):
        """DATE-TIME-01 — the agreed slot is the ONLY option offered, which is how the
        published Flow can show a chosen slot at all."""
        self.agree()
        data = self.svc.handle_init(self.token)["data"]
        self.assertNoEcho(data)
        self.assertEqual([i["id"] for i in data["date"]], [FUTURE.isoformat()])
        self.assertEqual([i["id"] for i in data["time"]], ["11:00"])
        self.assertTrue(data["is_date_enabled"])
        self.assertTrue(data["is_time_enabled"])

    def test_date_time_02_day_only_never_preselects_a_time(self):
        """DATE-TIME-02 — choosing the earliest for them would be us deciding."""
        data = self.svc.handle_date_selected(self.token, FUTURE.isoformat())["data"]
        self.assertNoEcho(data)
        self.assertTrue(data["time"], "real times are still offered")
        self.assertGreater(len(data["time"]), 1,
                           "a day-only request must leave the time choice open")

    def test_date_time_03_open_picker_selects_nothing(self):
        data = self.svc.handle_init(self.token)["data"]
        self.assertNoEcho(data)
        self.assertEqual(data["time"], [], "no times before a day is chosen")
        self.assertFalse(data["is_time_enabled"])
        self.assertTrue(data["date"])

    def test_date_time_04_an_agreed_time_that_went_stale_is_not_offered(self):
        """DATE-TIME-04 — the agreement is honoured only while it is still true."""
        self.agree(hhmm="11:00")
        self._mock_schedule(slots=["15:00", "16:00"])      # 11:00 is gone
        data = self.svc.handle_init(self.token)["data"]
        self.assertNoEcho(data)
        # the narrowed one-option list is abandoned: INIT falls back to the normal picker,
        # where times load only after a date is chosen
        self.assertEqual([i["id"] for i in data["time"]], [])
        self.assertNotIn("11:00", [i["id"] for i in data["time"]],
                         "a slot that vanished must not come back offered")
        self.assertTrue(data["date"], "dates are still offered")
        later = self.svc.handle_date_selected(self.token, FUTURE.isoformat())["data"]
        self.assertEqual([i["id"] for i in later["time"]], ["15:00", "16:00"])
        self.assertNotIn("11:00", [i["id"] for i in later["time"]])

    def test_date_time_05_date_selected_refresh_offers_that_day_s_times(self):
        data = self.svc.handle_date_selected(self.token, FUTURE.isoformat())["data"]
        self.assertNoEcho(data)
        self.assertEqual([i["id"] for i in data["time"]], SLOTS)
        self.assertTrue(data["is_time_enabled"])

    def test_date_time_06_a_slot_conflict_carries_no_stale_selection(self):
        """DATE-TIME-06 — the time that just vanished must not come back offered."""
        self._mock_schedule(valid=False)
        with self.assertRaises(BookingSlotConflictError) as ctx:
            self.svc.handle_confirm_booking(self.token, self.confirm_payload())
        data = ctx.exception.refreshed_data["data"]
        self.assertNoEcho(data)
        self.assertTrue(data["has_refusal"], "the refusal must be visible")
        self.assertTrue(data["slot_conflict_message"])

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

    def test_phone_01_canonical_identity_is_not_offered_to_the_published_flow(self):
        """PHONE-01 — WILD1-F3-R1.

        BEFORE  asserted `contact_phone` == WA_ID and `contact_phone_display` == "…0001"
                in the APPOINTMENT payload.
        AFTER   asserts both are absent.
        WHY     published v7.3's DETAILS screen declares neither key and its navigate
                payload carries neither, so the two keys went to a Flow that ignored them.
                The screen that would render them is in the unpublished prefill candidate,
                whose rollout the owner has deferred past November 1.
        NOT WEAKENED  the guarantee this test was named for — that the phone is SUPPLIED by
                us and never taken from the customer's form — is server-side and is
                asserted whole by PHONE-02 and PHONE-03, which are unchanged and still
                pass. The masking helper keeps its own unit assertions in PHONE-05.
        """
        data = self.svc.handle_init(self.token)["data"]
        self.assertNotIn("contact_phone", data)
        self.assertNotIn("contact_phone_display", data)
        # the helper is retained for the deferred rollout and must still be correct
        self.assertEqual(_mask_phone(WA_ID), "…0001")

    def test_phone_02_a_tampered_payload_phone_cannot_replace_wa_id(self):
        """PHONE-02 — enforced server-side, not by the absence of a UI field."""
        out = self.svc.handle_prepare_summary(
            self.token, self.confirm_payload(phone="5491199999999"))
        self.assertNotIn("99999999", json.dumps(out))
        self.assertEqual(out["data"]["phone"], WA_ID)

    def test_phone_03_the_booking_records_canonical_identity(self):
        self.svc.handle_confirm_booking(
            self.token, self.confirm_payload(phone="5491199999999"))
        rev = self.db.query(ThreadRevision).one()
        self.assertEqual(rev.buyer_phone, WA_ID)

    def test_phone_05_no_full_number_reaches_the_flow_at_all(self):
        """PHONE-05 — WILD1-F3-R1. Strictly stronger than before.

        BEFORE  the full number travelled as data and only the masked copy was checked for
                not containing it.
        AFTER   the number appears NOWHERE in the payload. A value we do not send cannot
                leak from a screen, a log or a Meta-side error report.
        """
        data = self.svc.handle_init(self.token)["data"]
        self.assertNotIn(WA_ID, json.dumps(data))
        self.assertTrue(_mask_phone(WA_ID).startswith("…"))
        self.assertEqual(_mask_phone(None), "")

    def test_the_candidate_flow_has_no_editable_phone_input(self):
        det = _screens(CANDIDATE)["DETAILS"]
        phone_input = _find(det["layout"],
                            lambda c: c.get("type") == "TextInput" and c.get("name") == "phone")
        self.assertIsNone(phone_input, "the editable phone field must be gone")
        footer = _find(det["layout"], lambda c: c.get("type") == "Footer")
        self.assertEqual(footer["on-click-action"]["payload"]["phone"], "${data.contact_phone}")


class TestNamePrefill(_World):

    # ── WILD1-F3-R1: NAME-01/02/04 ─────────────────────────────────────────────────
    #
    # BEFORE  each read `data["customer_name"]` from the APPOINTMENT payload.
    # AFTER   each asserts the key is absent, and tests `_lead_display_name` directly.
    # WHY     published v7.3's DETAILS `name` TextInput declares no `init-value`, so the
    #         name could not be prefilled; only the unpublished candidate binds it. The
    #         helper is retained for that deferred milestone, so its behaviour — the actual
    #         subject of all three tests — is still asserted, now at its source.
    # NOT WEAKENED  NAME-03, which proves a typed correction survives to the booking row,
    #         is unchanged and still passes.

    def test_name_01_a_lead_name_is_computed_but_not_offered(self):
        self.lead.nombre, self.lead.apellido = "Juan", "Pérez"
        self.db.commit()
        self.assertEqual(_lead_display_name(self.lead), "Juan Pérez")
        self.assertNotIn("customer_name", self.svc.handle_init(self.token)["data"])

    def test_name_02_no_lead_name_invents_nothing(self):
        self.assertEqual(_lead_display_name(self.lead), "")
        self.assertNotIn("customer_name", self.svc.handle_init(self.token)["data"])

    def test_name_04_whatsapp_display_name_is_not_verified_identity(self):
        """NAME-04 — display_name is a profile label the customer sets; it is not a name we
        may put on a booking."""
        data = self.svc.handle_init(self.token)["data"]
        self.assertNotIn("customer_name", data)
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
        """FLOW-07 — the rollback artifact must not drift."""
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
