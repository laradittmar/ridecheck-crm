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
        self.db.add(self.state); self.db.commit()
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

    def test_date_time_01_an_agreed_valid_slot_is_selected(self):
        """DATE-TIME-01 — both selections must be IDs present in their own data-source,
        which is what makes the Flow render them chosen rather than silently ignore them."""
        self.agree()
        data = self.svc.handle_init(self.token)["data"]
        self.assertEqual(data["selected_date"], FUTURE.isoformat())
        self.assertEqual(data["selected_time"], "11:00")
        self.assertIn(data["selected_date"], [i["id"] for i in data["date"]])
        self.assertIn(data["selected_time"], [i["id"] for i in data["time"]])

    def test_date_time_02_day_only_never_preselects_a_time(self):
        """DATE-TIME-02 — choosing the earliest for them would be us deciding."""
        data = self.svc.handle_date_selected(self.token, FUTURE.isoformat())["data"]
        self.assertEqual(data["selected_date"], FUTURE.isoformat())
        self.assertEqual(data["selected_time"], "")
        self.assertTrue(data["time"], "real times are still offered")

    def test_date_time_03_open_picker_selects_nothing(self):
        data = self.svc.handle_init(self.token)["data"]
        self.assertEqual(data["selected_date"], "")
        self.assertEqual(data["selected_time"], "")

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

    def test_date_time_05_date_selected_refresh_keeps_the_day(self):
        data = self.svc.handle_date_selected(self.token, FUTURE.isoformat())["data"]
        self.assertEqual(data["selected_date"], FUTURE.isoformat())
        self.assertEqual([i["id"] for i in data["time"]], SLOTS)

    def test_date_time_06_a_slot_conflict_carries_no_stale_selection(self):
        """DATE-TIME-06 — the time that just vanished must not come back selected."""
        self._mock_schedule(valid=False)
        with self.assertRaises(BookingSlotConflictError) as ctx:
            self.svc.handle_confirm_booking(self.token, self.confirm_payload())
        data = ctx.exception.refreshed_data["data"]
        self.assertEqual(data["selected_time"], "")
        self.assertIn("selected_date", data)

    def test_date_time_08_confirm_still_revalidates(self):
        """DATE-TIME-08 — prefill is UX; safety is unchanged."""
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(BFS_SOURCE))
                  if isinstance(n, ast.FunctionDef) and n.name == "handle_confirm_booking")
        self.assertIn("self._sched.check(check_in)", fn)
        self.assertIn("BOOKING_REVALIDATION_FAIL", fn)


class TestCanonicalPhone(_World):

    def test_phone_01_canonical_identity_is_supplied_not_requested(self):
        data = self.svc.handle_init(self.token)["data"]
        self.assertEqual(data["contact_phone"], WA_ID)
        self.assertEqual(data["contact_phone_display"], "…0001")

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

    def test_phone_05_no_full_number_is_rendered_on_screen(self):
        """PHONE-05 — the screen shows a mask; the full value travels as data only."""
        data = self.svc.handle_init(self.token)["data"]
        self.assertNotIn(WA_ID, data["contact_phone_display"])
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

    def test_name_01_a_lead_name_is_offered(self):
        self.lead.nombre, self.lead.apellido = "Juan", "Pérez"
        self.db.commit()
        self.assertEqual(self.svc.handle_init(self.token)["data"]["customer_name"], "Juan Pérez")

    def test_name_02_no_lead_name_invents_nothing(self):
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
        """FLOW-01 — screen data and response contract must agree, in both directions."""
        cand = _screens(CANDIDATE)
        appt_declared = set(cand["APPOINTMENT"]["data"].keys())
        supplied = set()
        for node in ast.walk(ast.parse(BFS_SOURCE)):
            if isinstance(node, ast.FunctionDef) and node.name in (
                    "_appointment_screen_data", "_identity_fields"):
                for d in ast.walk(node):
                    if isinstance(d, ast.Dict):
                        supplied |= {k.value for k in d.keys
                                     if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        missing = appt_declared - supplied - {"slot_conflict_message", "error_message"}
        self.assertEqual(missing, set(), f"backend never supplies: {missing}")

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
        prep = next(ast.unparse(n) for n in ast.walk(ast.parse(BFS_SOURCE))
                    if isinstance(n, ast.FunctionDef) and n.name == "handle_prepare_summary")
        self.assertNotIn('status="booked"', prep.replace("'", '"'))      # FLOW-04
        writers = {n.name for n in ast.walk(ast.parse(BFS_SOURCE))
                   if isinstance(n, ast.FunctionDef)
                   and 'status="booked"' in ast.unparse(n).replace("'", '"')}
        self.assertTrue(writers <= {"handle_confirm_booking", "_create_booking"}, writers)
        confirm = next(ast.unparse(n) for n in ast.walk(ast.parse(BFS_SOURCE))
                       if isinstance(n, ast.FunctionDef) and n.name == "handle_confirm_booking")
        self.assertIn("recalculate_revision_if_possible", confirm)       # FLOW-06
        self.assertIn("BOOKING_REFUSED_NO_CANONICAL_LOCATION", confirm)

    def test_the_candidate_declares_no_secret_bearing_field(self):
        text = CANDIDATE.read_text()
        for forbidden in ("EAA", "AIza", "sk-", "PRIVATE KEY", "download_url"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
