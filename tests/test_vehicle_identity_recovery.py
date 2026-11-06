"""PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE — SQLite half.

Production functions throughout; the network edge is the `_sb2b_harness` recorder. The Flow
round trip is driven by the CANDIDATE ARTIFACT itself: `Flow` below resolves the real `${data.*}`
and `${form.*}` bindings of `CANDIDATE_v7.8-…json` screen by screen, so a key the backend does
not supply, or a binding the artifact does not declare, fails here. It proves the contract
locally and nothing about Meta: Ejecutar and a handset remain the only authorities on those.
All plates, phones and addresses are synthetic.
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import unittest
from datetime import time
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _sb2b_harness as H  # noqa: E402
import test_minimum_launch as ML  # noqa: E402
import test_minimum_launch_r1 as R1  # noqa: E402

from pydantic import ValidationError  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (Lead, Revision, SlotRecoveryJob, ThreadRevision, WhatsAppBookingOffer,  # noqa: E402
                        WhatsAppContact, WhatsAppThread, WhatsAppThreadCandidate,
                        WhatsAppThreadEvidence, WhatsAppThreadState)
from app.schemas.revisions import RevisionCreate, RevisionOut, RevisionUpdate  # noqa: E402
from app.services import booking_flow_details as D  # noqa: E402
from app.services import booking_flow_service as bfs  # noqa: E402
from app.services import booking_slot_recovery as R  # noqa: E402
from app.services import flow_contract as FC  # noqa: E402
from app.services.lead_booking_details import LeadBookingDetails, lead_booking_details  # noqa: E402
from app.services.vehicle_identity import (DOMINIO_MAX, KILOMETRAJE_MAX, format_kilometraje,  # noqa: E402
                                           normalize_dominio, parse_kilometraje)

ROOT = pathlib.Path(__file__).resolve().parents[1]
FLOWS = ROOT / "meta" / "flows" / "booking"
PUBLISHED = FLOWS / "TARGET_v7.7-r3-prefill-refusal.json"
CANDIDATE = FLOWS / "CANDIDATE_v7.8-vehicle-identity-prefill.json"
GENERATOR = FLOWS / "make_v78_vehicle_prefill_candidate.py"

TYPED = {**ML.DETAILS, "vehicle_mileage": "85000", "vehicle_plate": "ab-123 cd"}
WINNER = {"name": "Winnie Fixture", "email": "winnie@example.invalid",
          "inspection_address": "Calle Ganadora 1", "seller_name": "Otro Vendedor",
          "seller_phone": "1122223333", "listing_url": "https://example.invalid/aviso/2",
          "vehicle_mileage": "12000", "vehicle_plate": "ZZZ999"}
EXPECTED_PREFILL = {**ML.DETAILS, "vehicle_mileage": "85000", "vehicle_plate": "AB123CD"}


def canonical(doc) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def on(flag=True):
    """The settings with the v7.8 switch on (or, `flag=False`, as they are today)."""
    return dataclasses.replace(H.settings(flag=True),
                               booking_flow_details_prefill_enabled=flag)


def svc_call(engine, fn, *, prefill=True):
    with Session(engine) as db, mock.patch("app.settings.get_settings", lambda: on(prefill)):
        return fn(bfs.BookingFlowService(db))


def init(engine, token, *, prefill=True):
    return svc_call(engine, lambda s: s.handle_init(token), prefill=prefill)


def prepare(engine, w, *, token=None, at=None, typed=None, prefill=True):
    data = {"date": w.day.isoformat(), "time": (at or w.at).strftime("%H:%M"),
            **(TYPED if typed is None else typed)}
    return svc_call(engine, lambda s: s.handle_prepare_summary(token or w.token, data),
                    prefill=prefill)


def confirm(engine, w, *, token=None, at=None, typed=None, attempt_ref=None, prefill=True):
    data = {"date": w.day.isoformat(), "time": (at or w.at).strftime("%H:%M"),
            **(TYPED if typed is None else typed)}
    return svc_call(engine, lambda s: s.handle_confirm_booking(token or w.token, data,
                                                               attempt_ref=attempt_ref),
                    prefill=prefill)


def work(engine, now=None):
    with Session(engine) as db, mock.patch("app.settings.get_settings", on):
        return R.run_once(db, now=now)


def race(engine, *, at=time(10, 0), typed=None, attempt_ref=None):
    """Two customers, one slot; both reached SUMMARY. The loser is refused for real."""
    win, lose = H.seed(engine, at=at), H.seed(engine, at=at)
    with ML.Meta():
        prepare(engine, win, typed=WINNER)
        confirm(engine, win, typed=WINNER)
    with ML.Meta() as m:
        prepare(engine, lose, typed=typed)
        try:
            confirm(engine, lose, typed=typed, attempt_ref=attempt_ref)
            raise AssertionError("the loser booked")
        except bfs.BookingSlotConflictError:
            pass
        assert (m.sent, m.flows) == ([], []), "a provider call inside Data Exchange"
    return win, lose


def fresh_token(engine, w):
    return ML.state(engine, w.thread_id).flow_booking_token


def live(engine, thread_id):
    with Session(engine) as db:
        return D.live_details(db, [thread_id]).get(thread_id, {})


def card(engine, lead_id):
    from app.ui.kanban_view import render_lead_card
    with Session(engine) as db:
        details = lead_booking_details(db, [lead_id]).get(lead_id)
        return details, render_lead_card(db.get(Lead, lead_id), booking_details=details)


class Flow:
    """Walks the candidate artifact: every value crosses a screen only through a binding the
    artifact really declares, and a reference to anything undeclared raises."""

    def __init__(self, path=CANDIDATE):
        self.doc = json.loads(path.read_text(encoding="utf-8"))
        self.screens = {s["id"]: s for s in self.doc["screens"]}

    @staticmethod
    def form_of(screen):
        return next(c for c in screen["layout"]["children"] if c.get("type") == "Form")

    def inputs(self, name):
        return [c for c in self.form_of(self.screens[name])["children"]
                if c.get("type") in ("TextInput", "Dropdown")]

    def resolve(self, value, data, form):
        if not isinstance(value, str):
            return value
        m = re.fullmatch(r"\$\{(data|form)\.([a-z_]+)\}", value)
        if m is None:
            return value
        source = data if m.group(1) == "data" else form
        return source[m.group(2)]

    def action(self, screen_id, data, form):
        screen = self.screens[screen_id]
        holder = self.form_of(screen)["children"] if screen_id != "SUMMARY" \
            else screen["layout"]["children"]
        footer = next(c for c in holder if c.get("type") == "Footer")
        act = footer["on-click-action"]
        return act, {k: self.resolve(v, data, form) for k, v in act["payload"].items()}

    def details_from(self, appointment_data, *, date, at):
        """APPOINTMENT → DETAILS: the navigate payload, then what the Form starts with."""
        act, payload = self.action("APPOINTMENT", appointment_data, {"date": date, "time": at})
        assert act["name"] == "navigate" and act["next"]["name"] == "DETAILS"
        declared = set(self.screens["DETAILS"]["data"])
        assert set(payload) == declared, (set(payload) ^ declared)
        form = self.form_of(self.screens["DETAILS"])
        initial = self.resolve(form["init-values"], payload, {})
        names = {c["name"] for c in self.inputs("DETAILS")}
        assert set(initial) <= names, set(initial) - names
        return payload, {n: initial.get(n, "") for n in names}

    def submit_details(self, details_data, form_values):
        act, payload = self.action("DETAILS", details_data, form_values)
        assert payload["trigger"] == "prepare_summary"
        return payload

    def submit_summary(self, summary_data):
        declared = set(self.screens["SUMMARY"]["data"])
        assert set(summary_data) == declared, set(summary_data) ^ declared
        act, payload = self.action("SUMMARY", summary_data, {})
        assert payload["trigger"] == "confirm_booking"
        return payload


# ── Kilometraje ──────────────────────────────────────────────────────────────────────────

class K_Kilometraje(unittest.TestCase):

    def test_k01_blank_is_null_never_zero(self):
        for blank in (None, "", "   ", "\t\n"):
            self.assertIsNone(parse_kilometraje(blank), repr(blank))

    def test_k02_present_and_explicit_zero(self):
        self.assertEqual(parse_kilometraje("85000"), 85000)
        self.assertEqual(parse_kilometraje(" 85000 "), 85000)
        self.assertEqual(parse_kilometraje(85000), 85000)
        self.assertEqual(parse_kilometraje("0"), 0)
        self.assertEqual(parse_kilometraje(0), 0)

    def test_k03_negative_rejected(self):
        for bad in ("-1", -1, " -85000 "):
            with self.assertRaises(ValueError, msg=repr(bad)):
                parse_kilometraje(bad)

    def test_k04_malformed_rejected(self):
        for bad in ("abc", "85,000", "1e5", "85000km", "85 000", "١٢٣", 85.5, 85000.0,
                    True, False, [], {}, "9" * 5000):
            with self.assertRaises(ValueError, msg=repr(bad)):
                parse_kilometraje(bad)

    def test_k08_argentine_grouping_is_accepted(self):
        for raw, want in (("85.000", 85000), (" 85.000 ", 85000), ("1.000", 1000),
                          ("1.234.567", 1234567), ("999.999.999", 999999999),
                          ("12.345", 12345), ("100.000", 100000)):
            self.assertEqual(parse_kilometraje(raw), want, raw)
            self.assertIs(type(parse_kilometraje(raw)), int, raw)

    def test_k09_grouping_that_needs_a_guess_is_refused(self):
        for bad in ("85.00", "85..000", "85,000", "85,5", "85.0000", "8.5", "1.23.456",
                    "1.234.56", "1234.567", ".000", "85.", ".85.000", "85.000.", "0.000",
                    "085.000", "00.000", "+85000", "+85.000", "-85.000", "-85000", "85.000,5",
                    "85,000.5", "1.234,567", "1,234.567", "85.000km", "85.000 km", "85 .000",
                    "1e5", "1.5e3", "٨٥.٠٠٠", "８５.０００", "85\u00a0000"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                parse_kilometraje(bad)

    def test_k10_grouped_ceiling_is_the_same_as_plain(self):
        self.assertEqual(parse_kilometraje("2.147.483.647"), KILOMETRAJE_MAX)
        for bad in ("2.147.483.648", "2147483648", "999.999.999.999", "9.999.999.999"):
            with self.assertRaises(ValueError, msg=bad):
                parse_kilometraje(bad)

    def test_k11_canonical_form_is_a_fixpoint(self):
        for raw in ("85.000", "85000", "1.234.567", "0"):
            once = parse_kilometraje(raw)
            self.assertEqual(parse_kilometraje(str(once)), once)
            self.assertEqual(parse_kilometraje(format_kilometraje(once).removesuffix(" km")), once)

    def test_k05_the_ceiling_is_the_column_not_an_opinion(self):
        self.assertEqual(parse_kilometraje(str(KILOMETRAJE_MAX)), KILOMETRAJE_MAX)
        self.assertEqual(parse_kilometraje("1500000"), 1500000)
        with self.assertRaises(ValueError):
            parse_kilometraje(KILOMETRAJE_MAX + 1)

    def test_k06_display(self):
        self.assertEqual(format_kilometraje(85000), "85.000 km")
        self.assertEqual(format_kilometraje("85000"), "85.000 km")
        self.assertEqual(format_kilometraje(1234567), "1.234.567 km")
        self.assertEqual(format_kilometraje(0), "0 km")
        self.assertEqual(format_kilometraje(None), "")

    def test_k07_errors_never_quote_the_value(self):
        with self.assertRaises(ValueError) as caught:
            parse_kilometraje("SECRET-VALUE")
        self.assertNotIn("SECRET", str(caught.exception))


class P_Dominio(unittest.TestCase):

    def test_p01_blank_is_null(self):
        for blank in (None, "", "   ", " - ", "..."):
            self.assertIsNone(normalize_dominio(blank), repr(blank))

    def test_p02_old_and_new_argentine_formats(self):
        for raw, want in (("ABC123", "ABC123"), ("abc 123", "ABC123"), ("abc-123", "ABC123"),
                          ("AB123CD", "AB123CD"), ("ab 123 cd", "AB123CD"),
                          ("  ab-123-cd  ", "AB123CD"), ("AB.123.CD", "AB123CD"),
                          ("ab–123–cd", "AB123CD")):
            self.assertEqual(normalize_dominio(raw), want, raw)

    def test_p03_a_permissive_foreign_value_is_accepted(self):
        for raw, want in (("1234-XYZ", "1234XYZ"), ("B 1234 CD", "B1234CD"), ("X9", "X9"),
                          ("7", "7"), ("A" * DOMINIO_MAX, "A" * DOMINIO_MAX)):
            self.assertEqual(normalize_dominio(raw), want, raw)

    def test_p04_not_alphanumeric_or_too_long_is_refused(self):
        for bad in ("<b>ABC</b>", "ABC_123", "ABC/123", "ÑANDÚ", "A" * (DOMINIO_MAX + 1), 123,
                    True, ["ABC123"]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                normalize_dominio(bad)

    def test_p05_normalization_is_idempotent(self):
        once = normalize_dominio(" ab-123 cd ")
        self.assertEqual(normalize_dominio(once), once)

    def test_p06_one_implementation(self):
        """The separator rule exists once; every entry point calls it."""
        app = ROOT / "backend" / "app"
        holders = sorted(p.name for p in app.rglob("*.py")
                         if re.search(r"def (normalize_dominio|parse_kilometraje)\b",
                                      p.read_text(encoding="utf-8")))
        self.assertEqual(holders, ["vehicle_identity.py"])
        for rel in ("schemas/revisions.py", "ui/kanban_actions.py",
                    "services/booking_flow_details.py"):
            text = (app / rel).read_text(encoding="utf-8")
            self.assertIn("normalize_dominio", text, rel)
            self.assertIn("parse_kilometraje", text, rel)


# ── API ──────────────────────────────────────────────────────────────────────────────────

class A_Api(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()

    def lead_with_revision(self, **fields):
        with Session(self.engine) as db:
            lead = Lead(estado="CONSULTA_NUEVA")
            db.add(lead); db.flush()
            db.add(Revision(lead_id=lead.id, marca="Fiat", **fields))
            db.commit()
            return int(lead.id)

    def patch(self, lead_id, **body):
        from app.api.revisions import update_latest_revision
        with Session(self.engine) as db, \
                mock.patch("app.api.revisions._recalc_quote_if_possible"):
            out = update_latest_revision(lead_id, RevisionUpdate(**body), db=db, if_match=None,
                                         idempotency_key=None)
            return (out.kilometraje, out.dominio, out.marca)

    def test_a01_old_payload_stays_valid_and_means_nothing(self):
        payload = RevisionCreate(marca="Fiat")
        self.assertIsNone(payload.kilometraje)
        self.assertIsNone(payload.dominio)

    def test_a02_schema_normalizes_and_refuses_through_the_shared_rule(self):
        payload = RevisionCreate(kilometraje="85000", dominio=" ab-123 cd ")
        self.assertEqual((payload.kilometraje, payload.dominio), (85000, "AB123CD"))
        self.assertIsNone(RevisionCreate(kilometraje="", dominio=" ").kilometraje)
        self.assertEqual(RevisionCreate(kilometraje=0).kilometraje, 0)
        self.assertEqual(RevisionCreate(kilometraje="85.000").kilometraje, 85000)
        self.assertEqual(RevisionCreate(kilometraje="1.234.567").kilometraje, 1234567)
        self.assertEqual(RevisionUpdate(kilometraje="85.000").kilometraje, 85000)
        for body in ({"kilometraje": -1}, {"kilometraje": "abc"}, {"kilometraje": "85.00"},
                     {"kilometraje": "85,000"}, {"kilometraje": True}, {"dominio": "<x>"}):
            with self.assertRaises(ValidationError, msg=str(body)):
                RevisionCreate(**body)

    def test_a03_omitted_fields_survive_an_unrelated_edit(self):
        lead = self.lead_with_revision(kilometraje=85000, dominio="ABC123")
        self.assertEqual(self.patch(lead, marca="Renault"), (85000, "ABC123", "Renault"))

    def test_a04_explicit_null_clears_the_way_patch_always_has(self):
        lead = self.lead_with_revision(kilometraje=85000, dominio="ABC123")
        self.assertEqual(self.patch(lead, kilometraje=None), (None, "ABC123", "Fiat"))
        self.assertEqual(self.patch(lead, dominio=""), (None, None, "Fiat"))

    def test_a05_patch_writes_the_canonical_values(self):
        lead = self.lead_with_revision()
        self.assertEqual(self.patch(lead, kilometraje="0", dominio="ab-123-cd"),
                         (0, "AB123CD", "Fiat"))

    def test_a05b_patch_with_grouped_mileage_stores_the_integer(self):
        lead = self.lead_with_revision()
        self.assertEqual(self.patch(lead, kilometraje="1.234.567"), (1234567, None, "Fiat"))

    def test_a06_out_schema_exposes_both_and_tolerates_their_absence(self):
        lead = self.lead_with_revision(kilometraje=0, dominio="ABC123")
        bare = self.lead_with_revision()
        with Session(self.engine) as db:
            def out(lead_id):
                row = db.query(Revision).filter(Revision.lead_id == lead_id).one()
                return RevisionOut.model_validate(row)
            self.assertEqual((out(lead).kilometraje, out(lead).dominio), (0, "ABC123"))
            self.assertEqual((out(bare).kilometraje, out(bare).dominio), (None, None))

    def test_a07_plate_is_not_unique_across_inspections(self):
        with Session(self.engine) as db:
            lead = Lead(estado="CONSULTA_NUEVA")
            other = Lead(estado="CONSULTA_NUEVA")
            db.add_all([lead, other]); db.flush()
            db.add_all([Revision(lead_id=lead.id, dominio="ABC123"),
                        Revision(lead_id=lead.id, dominio="ABC123"),
                        Revision(lead_id=other.id, dominio="ABC123")])
            db.commit()
            self.assertEqual(db.query(Revision).filter(Revision.dominio == "ABC123").count(), 3)
        column = Revision.__table__.c.dominio
        self.assertFalse(column.unique)
        self.assertFalse(any("dominio" in [c.name for c in idx.columns]
                             for idx in Revision.__table__.indexes))

    def test_a08_a_create_without_them_keeps_its_fingerprint(self):
        """An idempotent API retry spanning a deployment must still match its own key: the
        booking command built by the real handler carries neither key unless it was given."""
        from datetime import date
        from types import SimpleNamespace
        from app.api import revisions as api
        lead = self.lead_with_revision()

        def command_for(**body):
            seen = []

            class Authority:
                def __init__(self, db):
                    pass

                def execute(self, command, **_):
                    seen.append(command)
                    return SimpleNamespace(ok=True, revision_id=1)
            with Session(self.engine) as db, mock.patch.object(api, "BookingAuthority", Authority):
                api.create_revision(lead, RevisionCreate(
                    turno_fecha=date(2030, 1, 7), turno_hora=time(10, 0),
                    estado_revision="CONFIRMADO", **body), db=db, idempotency_key="k")
            return seen[0]
        self.assertNotIn("kilometraje", command_for().revision_fields)
        self.assertNotIn("dominio", command_for().revision_fields)
        given = command_for(kilometraje=0, dominio="abc 123").revision_fields
        self.assertEqual((given["kilometraje"], given["dominio"]), (0, "ABC123"))


# ── CRM form and rendering ───────────────────────────────────────────────────────────────

class C_Crm(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import inspect
        from app.ui.kanban_actions import ui_revision_latest_update
        # The dependency the handler really declares: other suites replace `app.db` in
        # `sys.modules`, so importing `get_db` here can name a different function.
        get_db = inspect.signature(ui_revision_latest_update).parameters["db"].default.dependency
        app = FastAPI()
        app.post("/ui/revision")(ui_revision_latest_update)

        def session():
            with Session(self.engine) as db:
                yield db
        app.dependency_overrides[get_db] = session
        self.client = TestClient(app, raise_server_exceptions=False)

    def seed(self, **fields):
        with Session(self.engine) as db:
            lead = Lead(estado="CONSULTA_NUEVA", nombre="Ana")
            db.add(lead); db.flush()
            db.add(Revision(lead_id=lead.id, marca="Fiat", modelo="Uno", anio=2008, **fields))
            db.commit()
            return int(lead.id)

    def post(self, lead_id, **form):
        body = {"lead_id": str(lead_id), "marca": "Fiat", "modelo": "Uno", "anio": "2008",
                "recalcular_presupuesto": "false", **form}
        return self.client.post("/ui/revision", data=body, follow_redirects=False)

    def values(self, lead_id):
        with Session(self.engine) as db:
            r = db.query(Revision).filter(Revision.lead_id == lead_id).one()
            return (r.kilometraje, r.dominio, r.marca)

    def test_c01_edit_persists_canonical_values_after_reload(self):
        lead = self.seed()
        resp = self.post(lead, vehicle_identity_form="1", kilometraje="85000",
                         dominio="ab-123 cd")
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(self.values(lead), (85000, "AB123CD", "Fiat"))

    def test_c01b_grouped_mileage_is_stored_and_reopens_as_digits(self):
        lead = self.seed()
        resp = self.post(lead, vehicle_identity_form="1", kilometraje="1.234.567", dominio="")
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(self.values(lead)[0], 1234567)
        _, html = card(self.engine, lead)
        self.assertIn('autocomplete="off" value="1234567"', html)
        self.assertIn("Kilometraje: 1.234.567 km", html)

    def test_c01c_the_browser_cannot_turn_grouped_input_into_a_blank(self):
        """`type="number"` reports `1.234.567` as an empty value, which this form reads as a
        clearing. The input must leave the value for the server's normalizer to judge."""
        lead = self.seed(kilometraje=85000)
        _, html = card(self.engine, lead)
        self.assertNotIn('name="kilometraje" type="number"', html)

    def test_c02_blank_inputs_clear_to_null_not_zero(self):
        lead = self.seed(kilometraje=85000, dominio="ABC123")
        self.assertEqual(self.post(lead, vehicle_identity_form="1", kilometraje="",
                                   dominio="").status_code, 303)
        self.assertEqual(self.values(lead), (None, None, "Fiat"))

    def test_c03_a_post_from_a_form_without_them_erases_nothing(self):
        lead = self.seed(kilometraje=85000, dominio="ABC123")
        self.assertEqual(self.post(lead, marca="Renault").status_code, 303)
        self.assertEqual(self.values(lead), (85000, "ABC123", "Renault"))

    def test_c04_a_refused_value_changes_nothing(self):
        lead = self.seed(kilometraje=85000, dominio="ABC123")
        for bad in ({"kilometraje": "-1"}, {"kilometraje": "abc"}, {"kilometraje": "85.00"},
                    {"kilometraje": "85,000"}, {"dominio": "<script>"}):
            resp = self.post(lead, vehicle_identity_form="1", marca="Renault",
                             **{"kilometraje": "1", "dominio": "X1", **bad})
            self.assertEqual(resp.status_code, 422, bad)
            self.assertEqual(self.values(lead), (85000, "ABC123", "Fiat"), bad)

    def test_c05_explicit_zero_is_kept_and_shown(self):
        from app.ui.kanban_view import _vehicle_identity_line
        lead = self.seed()
        self.post(lead, vehicle_identity_form="1", kilometraje="0", dominio="")
        self.assertEqual(self.values(lead)[0], 0)
        _, html = card(self.engine, lead)
        self.assertEqual(html.count("Kilometraje: 0 km"), 2)     # summary line and revision card
        with Session(self.engine) as db:
            rev = db.query(Revision).filter(Revision.lead_id == lead).one()
            self.assertIn("Kilometraje: 0 km", _vehicle_identity_line(rev))
        self.assertIn('name="kilometraje" type="text" inputmode="decimal" autocomplete="off" '
                      'value="0"', html)

    def test_c06_modal_places_both_inputs_with_the_vehicle(self):
        lead = self.seed(kilometraje=85000, dominio="ABC123")
        _, html = card(self.engine, lead)
        order = [html.index(s) for s in ('name="marca"', 'name="modelo"', 'name="anio"',
                                         'name="kilometraje"', 'name="dominio"',
                                         'name="link_compra"')]
        self.assertEqual(order, sorted(order))
        self.assertIn('<div class="label">Kilometraje</div>', html)
        self.assertIn('<div class="label">Dominio / patente</div>', html)
        self.assertIn('name="vehicle_identity_form" value="1"', html)

    def test_c07_display_uses_kilometres_and_the_normalized_plate(self):
        lead = self.seed(kilometraje=85000, dominio="AB123CD")
        _, html = card(self.engine, lead)
        self.assertIn("Kilometraje: 85.000 km", html)
        self.assertIn("Dominio / patente: AB123CD", html)

    def test_c08_existing_revisions_render_with_no_misleading_label(self):
        lead = self.seed()
        _, html = card(self.engine, lead)
        for wrong in ("Kilometraje:", "Dominio / patente:", "0 km", ">None<", 'value="None"'):
            self.assertNotIn(wrong, html, wrong)

    def test_c09_stored_markup_is_escaped_on_every_surface(self):
        from app.ui.kanban_view import render_booking_details_block
        evil = '"><script>alert(1)</script>'
        lead = self.seed(dominio=evil[:16])
        _, html = card(self.engine, lead)
        # the card carries its own structural <script>; what must not appear is the VALUE raw
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;alert", html)
        block = render_booking_details_block(LeadBookingDetails(
            thread_id=1, kilometraje=evil, dominio=evil))
        self.assertNotIn("<script>", block)
        self.assertIn("&lt;script&gt;", block)

    def test_c10_resultado_tecnico_field_is_preserved(self):
        lead = self.seed(resultado_link="https://example.invalid/informe")
        _, html = card(self.engine, lead)
        self.assertIn('name="resultado_link"', html)
        self.assertIn("https://example.invalid/informe", html)
        self.assertNotIn('type="file"', html[html.index('name="resultado_link"') - 200:
                                             html.index('name="resultado_link"') + 200])

    def agenda(self, **fields):
        from datetime import date, timedelta
        from app.ui.kanban_view import render_calendar_page
        today = date.today()
        lead_id = self.seed(turno_fecha=today, turno_hora=time(10, 0),
                            estado_revision="PENDIENTE", **fields)
        with Session(self.engine) as db:
            lead = db.get(Lead, lead_id)
            lead.revisions                               # loaded while the session is open
            return render_calendar_page([lead], profesionales=[],
                                        week=(today - timedelta(days=today.weekday())).isoformat())

    def test_c11_agenda_vehicle_line_carries_them_only_when_present(self):
        html = self.agenda(kilometraje=85000, dominio="AB123CD")
        self.assertIn("Fiat / Uno / 2008 / AB123CD / 85.000 km", html)
        self.assertNotIn(" km", self.agenda().replace("85.000 km", ""))

    def test_c12_agenda_escapes_the_vehicle_line_on_every_view(self):
        html = self.agenda(dominio='"><script>alert(')
        self.assertNotIn("<script>alert", html)
        self.assertGreaterEqual(html.count("&lt;script&gt;alert"), 3)


# ── the Flow artifact ────────────────────────────────────────────────────────────────────

def run_generator(dst):
    return subprocess.run([sys.executable, str(GENERATOR), str(PUBLISHED), str(dst)],
                          capture_output=True, text=True)


class F_Artifact(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(CANDIDATE.read_text(encoding="utf-8"))
        cls.base = json.loads(PUBLISHED.read_text(encoding="utf-8"))
        cls.flow = Flow()

    def test_f01_published_artifact_is_byte_identical(self):
        self.assertEqual(hashlib.sha256(PUBLISHED.read_bytes()).hexdigest(),
                         FC.TARGET_FLOW_SHA256)
        self.assertEqual(canonical(self.base), FC.TARGET_FLOW_CANONICAL_SHA256)

    def test_f02_candidate_hashes_are_pinned(self):
        self.assertEqual(hashlib.sha256(CANDIDATE.read_bytes()).hexdigest(),
                         FC.CANDIDATE_FLOW_SHA256)
        self.assertEqual(canonical(self.doc), FC.CANDIDATE_FLOW_CANONICAL_SHA256)

    def test_f03_generator_reproduces_it_byte_for_byte_twice(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            one, two = pathlib.Path(tmp) / "1.json", pathlib.Path(tmp) / "2.json"
            self.assertEqual(run_generator(one).returncode, 0)
            self.assertEqual(run_generator(two).returncode, 0)
            self.assertEqual(one.read_bytes(), two.read_bytes())
            self.assertEqual(one.read_bytes(), CANDIDATE.read_bytes())

    def test_f04_generator_refuses_any_other_source(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            dst = pathlib.Path(tmp) / "x.json"
            out = subprocess.run([sys.executable, str(GENERATOR),
                                  str(FLOWS / "CANDIDATE_v7.4-prefill-r3.json"), str(dst)],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 2)
            self.assertFalse(dst.exists())

    def test_f05_forward_only_validator_passes_and_the_graph_is_unchanged(self):
        self.assertEqual(self.doc["routing_model"], self.base["routing_model"])
        FC.assert_required_forward_graph(self.doc["routing_model"],
                                         screens=[s["id"] for s in self.doc["screens"]])

    def test_f06_forward_route_rejections_stay_active_on_the_candidate(self):
        for origin, target in (("DETAILS", "APPOINTMENT"), ("SUMMARY", "APPOINTMENT"),
                               ("SUMMARY", "DETAILS")):
            graph = json.loads(json.dumps(self.doc["routing_model"]))
            graph[origin] = graph[origin] + [target]
            with self.assertRaises(ValueError, msg=f"{origin}->{target}"):
                FC.assert_forward_only(graph, screens=list(graph))

    def test_f07_repository_validator_passes_with_no_error_or_render_finding(self):
        spec = importlib.util.spec_from_file_location(
            "_validate_flow", ROOT / "scripts" / "validate_flow_json.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        findings = module.check(self.doc, self.base, pin_published_graph=True)
        self.assertEqual([f for f in findings if f[0] != "UNPROVEN"], [])
        # the honest part: what no Meta-accepted artifact has ever carried stays visible
        unproven = {f[1].rsplit(".", 1)[-1] for f in findings if f[0] == "UNPROVEN"}
        self.assertEqual(unproven, {"__example__", "init-values", "helper-text", "pattern"})

    def test_f08_nothing_was_removed_renamed_or_reordered(self):
        def strip(doc):
            doc = json.loads(json.dumps(doc))
            screens = {s["id"]: s for s in doc["screens"]}
            screens["APPOINTMENT"]["data"].pop("details_prefill")
            screens["DETAILS"]["data"].pop("details_prefill")
            screens["SUMMARY"]["data"].pop("vehicle_mileage")
            screens["SUMMARY"]["data"].pop("vehicle_plate")
            forms = {sid: next(c for c in s["layout"]["children"] if c.get("type") == "Form")
                     for sid, s in screens.items() if sid != "SUMMARY"}
            forms["APPOINTMENT"]["children"][-1]["on-click-action"]["payload"] \
                .pop("details_prefill")
            forms["DETAILS"].pop("init-values")
            kids = forms["DETAILS"]["children"]
            kids[:] = [c for c in kids if c.get("name") not in ("vehicle_mileage", "vehicle_plate")]
            for payload in (kids[-1]["on-click-action"]["payload"],
                            screens["SUMMARY"]["layout"]["children"][-1]["on-click-action"]["payload"]):
                payload.pop("vehicle_mileage")
                payload.pop("vehicle_plate")
            return doc
        self.assertEqual(strip(self.doc), self.base)

    def test_f09_both_inputs_are_optional_with_the_owner_labels_and_meta_limits(self):
        inputs = {c["name"]: c for c in self.flow.inputs("DETAILS")}
        self.assertEqual(inputs["vehicle_mileage"]["label"], "Kilometraje")
        self.assertEqual(inputs["vehicle_plate"]["label"], "Dominio / patente")
        for name in ("vehicle_mileage", "vehicle_plate"):
            self.assertIs(inputs[name]["required"], False)
            self.assertLessEqual(len(inputs[name]["label"]), 20)         # Meta: label limit
            self.assertLessEqual(len(inputs[name]["helper-text"]), 80)   # Meta: helper limit
        self.assertNotIn("input-type", inputs["vehicle_mileage"])   # `pattern` alone decides

    def test_f10_no_init_value_and_the_form_binds_init_values_whole(self):
        raw = CANDIDATE.read_text(encoding="utf-8")
        self.assertEqual(raw.count('"init-value"'), 0)
        form = Flow.form_of(self.flow.screens["DETAILS"])
        self.assertEqual(form["init-values"], "${data.details_prefill}")
        declared = set(self.flow.screens["DETAILS"]["data"]["details_prefill"]["properties"])
        self.assertEqual(declared, {c["name"] for c in self.flow.inputs("DETAILS")})
        example = self.flow.screens["DETAILS"]["data"]["details_prefill"]["__example__"]
        self.assertEqual(set(example), declared)

    def test_f11_every_value_the_handset_pattern_admits_the_server_admits(self):
        inputs = {c["name"]: c for c in self.flow.inputs("DETAILS")}
        mileage = re.compile(inputs["vehicle_mileage"]["pattern"])
        plate = re.compile(inputs["vehicle_plate"]["pattern"])
        for value in ("", "0", "999999999", "85000", "85.000", "1.234.567", "999.999.999"):
            self.assertTrue(mileage.fullmatch(value), value)
            parse_kilometraje(value)
        for value in ("", "ABC123", "ab 123 cd", "ab-123-cd", "A.B", "A" * 16, "- -",
                      "1234 XYZ"):
            self.assertTrue(plate.fullmatch(value), value)
            normalize_dominio(value)
        for value in ("-1", "85.00", "85..000", "85,000", "85,5", "abc", "1234567890", "0.000",
                      "085.000", "1.234.567.890", "1e5"):
            self.assertFalse(mileage.fullmatch(value), value)
        for value in ("<b>", "ABC_123", "A" * 17):
            self.assertFalse(plate.fullmatch(value), value)


class G_Contract(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()
        self.flow = Flow()

    def test_g01_with_the_switch_off_every_payload_is_the_published_contract(self):
        w = H.seed(self.engine)
        data = init(self.engine, w.token, prefill=False)["data"]
        FC.validate_screen_payload("APPOINTMENT", data)
        self.assertNotIn("details_prefill", data)
        with self.assertRaises(ValueError):
            FC.validate_screen_payload("APPOINTMENT", {**data, "details_prefill": {}})
        summary = prepare(self.engine, w, typed=ML.DETAILS, prefill=False)["data"]
        FC.validate_screen_payload("SUMMARY", summary)
        self.assertEqual(set(summary), FC.SUMMARY_KEYS)

    def test_g02_default_setting_is_off(self):
        from app.settings import Settings
        self.assertIs(Settings().booking_flow_details_prefill_enabled, False)

    def test_g03_candidate_keys_match_the_artifact_in_both_directions(self):
        for screen, keys in FC.CANDIDATE_SCREEN_KEYS.items():
            self.assertEqual(set(self.flow.screens[screen]["data"]), set(keys), screen)

    def test_g04_with_the_switch_on_every_path_supplies_the_declared_key(self):
        w = H.seed(self.engine)
        data = init(self.engine, w.token)["data"]
        FC.validate_screen_payload("APPOINTMENT", data, candidate=True)
        self.assertEqual(data["details_prefill"], {})
        refreshed = svc_call(self.engine,
                             lambda s: s.handle_date_selected(w.token, w.day.isoformat()))
        FC.validate_screen_payload("APPOINTMENT", refreshed["data"], candidate=True)
        with mock.patch("app.settings.get_settings", on):
            screen = bfs.BookingFlowService.__new__(bfs.BookingFlowService)._refusal_screen(
                None, "", "x")
        FC.validate_screen_payload("APPOINTMENT", screen["data"], candidate=True)

    def test_g05_an_undeclared_key_is_still_refused(self):
        with self.assertRaises(ValueError):
            FC.appointment_payload(not_a_key="x")

    def test_g06_old_flow_requests_stay_valid_on_a_new_backend(self):
        """A session opened on v7.7 sends neither key, whatever the switch says. Off, SUMMARY
        is the published contract. On, it is the candidate's (V-02): the two extra keys are
        empty, v7.7 does not read them, and booking is unaffected."""
        for prefill, at in ((False, time(10, 0)), (True, time(11, 30))):
            w = H.seed(self.engine, at=at)
            summary = prepare(self.engine, w, typed=ML.DETAILS, prefill=prefill)["data"]
            self.assertEqual(set(summary),
                             FC.CANDIDATE_SCREEN_KEYS["SUMMARY"] if prefill else FC.SUMMARY_KEYS)
            self.assertNotIn("Kilometraje", summary["customer_summary"])
            with ML.Meta():
                done = confirm(self.engine, w, typed=ML.DETAILS, prefill=prefill)
            self.assertEqual(done["screen"], "SUCCESS")
            with Session(self.engine) as db:
                rev = db.query(Revision).filter(Revision.lead_id == w.lead_id).one()
                self.assertEqual((rev.kilometraje, rev.dominio), (None, None))

    def test_g07_flow_payload_round_trip_through_the_artifact(self):
        w = H.seed(self.engine)
        appointment = init(self.engine, w.token)["data"]
        details, form = self.flow.details_from(appointment, date=w.day.isoformat(),
                                               at=w.at.strftime("%H:%M"))
        self.assertEqual(set(v for v in form.values() if v), set())    # nothing to prefill yet
        form.update({k: v for k, v in TYPED.items()})
        submitted = self.flow.submit_details(details, form)
        summary = svc_call(self.engine,
                           lambda s: s.handle_prepare_summary(w.token, submitted))["data"]
        self.assertEqual((summary["vehicle_mileage"], summary["vehicle_plate"]),
                         ("85000", "AB123CD"))
        self.assertIn("Kilometraje: 85.000 km", summary["customer_summary"])
        self.assertIn("Dominio / patente: AB123CD", summary["customer_summary"])
        final = self.flow.submit_summary(summary)
        with ML.Meta():
            done = svc_call(self.engine, lambda s: s.handle_confirm_booking(w.token, final))
        self.assertEqual(done["screen"], "SUCCESS")
        with Session(self.engine) as db:
            rev = db.query(Revision).filter(Revision.lead_id == w.lead_id).one()
            self.assertEqual((rev.kilometraje, rev.dominio), (85000, "AB123CD"))

    def test_g07b_grouped_mileage_round_trip_is_canonical_at_every_step(self):
        w = H.seed(self.engine)
        appointment = init(self.engine, w.token)["data"]
        details, form = self.flow.details_from(appointment, date=w.day.isoformat(),
                                               at=w.at.strftime("%H:%M"))
        form.update({**TYPED, "vehicle_mileage": "1.234.567"})
        submitted = self.flow.submit_details(details, form)
        self.assertEqual(submitted["vehicle_mileage"], "1.234.567")
        summary = svc_call(self.engine,
                           lambda s: s.handle_prepare_summary(w.token, submitted))["data"]
        self.assertEqual(summary["vehicle_mileage"], "1234567")
        self.assertIn("Kilometraje: 1.234.567 km", summary["customer_summary"])
        self.assertEqual(live(self.engine, w.thread_id)["FLOW_VEHICLE_MILEAGE"], "1234567")
        with ML.Meta():
            svc_call(self.engine, lambda s: s.handle_confirm_booking(
                w.token, self.flow.submit_summary(summary)))
        with Session(self.engine) as db:
            rev = db.query(Revision).filter(Revision.lead_id == w.lead_id).one()
            self.assertEqual(rev.kilometraje, 1234567)
            self.assertIs(type(rev.kilometraje), int)

    def test_g07c_resubmitting_the_same_reading_grouped_is_not_a_correction(self):
        w = H.seed(self.engine)
        prepare(self.engine, w, typed={**TYPED, "vehicle_mileage": "85000"})
        with Session(self.engine) as db:
            before = db.query(WhatsAppThreadEvidence).count()
        prepare(self.engine, w, typed={**TYPED, "vehicle_mileage": "85.000"})
        with Session(self.engine) as db:
            self.assertEqual(db.query(WhatsAppThreadEvidence).count(), before)

    def test_g08_both_omitted_round_trip(self):
        w = H.seed(self.engine)
        appointment = init(self.engine, w.token)["data"]
        details, form = self.flow.details_from(appointment, date=w.day.isoformat(),
                                               at=w.at.strftime("%H:%M"))
        form.update(ML.DETAILS)                      # both vehicle inputs left blank
        submitted = self.flow.submit_details(details, form)
        self.assertEqual((submitted["vehicle_mileage"], submitted["vehicle_plate"]), ("", ""))
        summary = svc_call(self.engine,
                           lambda s: s.handle_prepare_summary(w.token, submitted))["data"]
        self.assertNotIn("Kilometraje", summary["customer_summary"])
        final = self.flow.submit_summary(summary)
        with ML.Meta():
            svc_call(self.engine, lambda s: s.handle_confirm_booking(w.token, final))
        with Session(self.engine) as db:
            rev = db.query(Revision).filter(Revision.lead_id == w.lead_id).one()
            self.assertEqual((rev.kilometraje, rev.dominio), (None, None))

    def test_g09_malformed_flow_values_are_refused_and_nothing_is_kept(self):
        for bad in ({"vehicle_mileage": "-5"}, {"vehicle_mileage": "abc"},
                    {"vehicle_mileage": "85.00"}, {"vehicle_mileage": "85,000"},
                    {"vehicle_plate": "<b>"}):
            w = H.seed(self.engine)
            with self.assertRaises(ValueError):
                prepare(self.engine, w, typed={**ML.DETAILS, **bad})
            self.assertEqual(live(self.engine, w.thread_id), {}, bad)
            with ML.Meta() as m, self.assertRaises(ValueError):
                confirm(self.engine, w, typed={**ML.DETAILS, **bad})
            self.assertEqual(m.sent, [])
            with Session(self.engine) as db:
                self.assertEqual(db.query(Revision).filter(
                    Revision.lead_id == w.lead_id).count(), 0, bad)

    def test_g10_a_refusal_message_never_carries_the_value(self):
        w = H.seed(self.engine)
        with self.assertRaises(ValueError) as caught:
            prepare(self.engine, w, typed={**ML.DETAILS, "vehicle_plate": "SECRET<>"})
        self.assertNotIn("SECRET", str(caught.exception))


# ── persistence ──────────────────────────────────────────────────────────────────────────

class S_Persistence(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()

    def test_s01_prepare_summary_keeps_both_canonical_bound_and_draft(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        with Session(self.engine) as db:
            rows = {r.claim_type: r for r in db.query(WhatsAppThreadEvidence).filter(
                WhatsAppThreadEvidence.thread_id == w.thread_id)}
            for claim, value in (("FLOW_VEHICLE_MILEAGE", "85000"),
                                 ("FLOW_VEHICLE_PLATE", "AB123CD")):
                self.assertEqual(rows[claim].value, value)
                self.assertEqual(rows[claim].bound_to_candidate_id, w.candidate_id)
                self.assertEqual((rows[claim].status, rows[claim].outcome), ("DRAFT", "HOLD"))
            self.assertEqual(db.query(Revision).filter(Revision.lead_id == w.lead_id).count(), 0)

    def test_s02_replayed_prepare_writes_nothing(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        with Session(self.engine) as db:
            before = db.query(WhatsAppThreadEvidence).count()
        prepare(self.engine, w)
        with Session(self.engine) as db:
            self.assertEqual(db.query(WhatsAppThreadEvidence).count(), before)

    def test_s03_zero_survives_every_step(self):
        w = H.seed(self.engine)
        typed = {**ML.DETAILS, "vehicle_mileage": "0", "vehicle_plate": ""}
        summary = prepare(self.engine, w, typed=typed)["data"]
        self.assertEqual(summary["vehicle_mileage"], "0")
        self.assertIn("Kilometraje: 0 km", summary["customer_summary"])
        self.assertEqual(live(self.engine, w.thread_id)["FLOW_VEHICLE_MILEAGE"], "0")
        with ML.Meta():
            confirm(self.engine, w, typed=typed)
        with Session(self.engine) as db:
            rev = db.query(Revision).filter(Revision.lead_id == w.lead_id).one()
            self.assertEqual((rev.kilometraje, rev.dominio), (0, None))

    def test_s04_winner_persists_on_the_revision_and_only_there(self):
        w = H.seed(self.engine)
        with ML.Meta():
            prepare(self.engine, w)
            confirm(self.engine, w)
        with Session(self.engine) as db:
            rev = db.query(Revision).filter(Revision.lead_id == w.lead_id).one()
            self.assertEqual((rev.kilometraje, rev.dominio), (85000, "AB123CD"))
            self.assertFalse(hasattr(ThreadRevision, "kilometraje"))
            self.assertFalse(hasattr(ThreadRevision, "dominio"))

    def test_s05_neither_field_can_make_a_second_booking(self):
        w = H.seed(self.engine)
        with ML.Meta():
            confirm(self.engine, w, attempt_ref="d1")
            again = confirm(self.engine, w, attempt_ref="d1")
            changed = confirm(self.engine, w, attempt_ref="d2",
                              typed={**TYPED, "vehicle_plate": "OTHER1"})
        self.assertEqual((again["screen"], changed["screen"]), ("SUCCESS", "SUCCESS"))
        with Session(self.engine) as db:
            self.assertEqual(db.query(Revision).filter(Revision.lead_id == w.lead_id).count(), 1)
            self.assertEqual(db.query(ThreadRevision).filter(
                ThreadRevision.thread_id == w.thread_id).count(), 1)
            self.assertEqual(db.query(Lead).count(), 1)
            rev = db.query(Revision).filter(Revision.lead_id == w.lead_id).one()
            self.assertEqual(rev.dominio, "AB123CD")      # the booking is the first submission

    def test_s06_a_submission_without_them_keeps_its_booking_fingerprint(self):
        """The ledger's request fingerprint must not move for a booking that has neither."""
        from app.services.booking_authority import request_fingerprint
        real, seen = bfs._execute_isolated, []

        def capture(db, command, availability, attachments):
            seen.append(command)
            return real(db, command, availability, attachments)
        w = H.seed(self.engine)
        with ML.Meta(), mock.patch.object(bfs, "_execute_isolated", capture):
            confirm(self.engine, w, typed=ML.DETAILS)
        fields = dict(seen[0].revision_fields)
        self.assertNotIn("kilometraje", fields)
        self.assertNotIn("dominio", fields)
        moved = dataclasses.replace(seen[0], revision_fields={**fields, "kilometraje": None})
        self.assertNotEqual(request_fingerprint(seen[0]), request_fingerprint(moved))

    def test_s07_operator_notification_is_untouched(self):
        source = (ROOT / "backend/app/services/operator_notification.py").read_text(encoding="utf-8")
        self.assertNotIn("kilometraje", source)
        self.assertNotIn("dominio", source)

    def test_s08_no_logger_call_can_carry_a_value(self):
        for name in ("booking_flow_details.py", "vehicle_identity.py", "lead_booking_details.py"):
            source = (ROOT / "backend/app/services" / name).read_text(encoding="utf-8")
            for call in re.findall(r"logger\.\w+\([^\n]*(?:\n[^\n]*?){0,2}\)", source):
                for leak in ("values", "plate", "mileage", "dominio", "kilometraje"):
                    self.assertNotIn(leak, call.replace("details_prefill", ""), call)


# ── recovery ─────────────────────────────────────────────────────────────────────────────

class R_Recovery(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()
        self.flow = Flow()

    def recovered(self, **kw):
        win, lose = race(self.engine, **kw)
        with ML.Meta() as m:
            work(self.engine)
        return win, lose, m

    def assert_prefilled(self, lose, expected=EXPECTED_PREFILL):
        token = fresh_token(self.engine, lose)
        screen = init(self.engine, token)
        self.assertEqual(screen["screen"], "APPOINTMENT")
        FC.validate_screen_payload("APPOINTMENT", screen["data"], candidate=True)
        self.assertEqual(screen["data"]["details_prefill"], expected)
        return token, screen["data"]

    def test_r01_loser_persisted_before_rebooking_with_no_booking(self):
        win, lose = race(self.engine)
        got = live(self.engine, lose.thread_id)
        self.assertEqual(got["FLOW_VEHICLE_MILEAGE"], "85000")
        self.assertEqual(got["FLOW_VEHICLE_PLATE"], "AB123CD")
        with Session(self.engine) as db:
            self.assertEqual(db.query(Revision).filter(Revision.lead_id == lose.lead_id).count(), 0)
            self.assertEqual(db.query(ThreadRevision).filter(
                ThreadRevision.thread_id == lose.thread_id).count(), 0)

    def test_r02_one_text_one_same_flow_starting_at_appointment(self):
        win, lose, m = self.recovered()
        self.assertEqual([t for _w, t in m.sent], [R.RECOVERY_TEXT])
        self.assertEqual(len(m.flows), 1)
        source = (ROOT / "backend/app/services/booking_slot_recovery.py").read_text(encoding="utf-8")
        self.assertIn('initial_screen="APPOINTMENT", mode=FLOW_MODE_DATA_EXCHANGE', source)
        self.assertEqual(m.flows[0][1], fresh_token(self.engine, lose))

    def test_r03_every_value_comes_back_including_mileage_and_plate(self):
        win, lose, m = self.recovered()
        self.assert_prefilled(lose)

    def test_r04_recovery_prefill_through_the_artifact_then_fallback_booking(self):
        win, lose, m = self.recovered()
        token, appointment = self.assert_prefilled(lose)
        at = "11:30"
        details, form = self.flow.details_from(appointment, date=lose.day.isoformat(), at=at)
        self.assertEqual({k: v for k, v in form.items() if v}, EXPECTED_PREFILL)
        # the customer retypes NOTHING: the form is submitted exactly as it opened
        summary = svc_call(self.engine, lambda s: s.handle_prepare_summary(
            token, self.flow.submit_details(details, form)))["data"]
        final = self.flow.submit_summary(summary)
        with ML.Meta() as booked:
            done = svc_call(self.engine, lambda s: s.handle_confirm_booking(token, final))
        self.assertEqual(done["screen"], "SUCCESS")
        self.assertEqual([t for _w, t in booked.sent], [H.OWNER_TEXT])
        with Session(self.engine) as db:
            rev = db.query(Revision).filter(Revision.lead_id == lose.lead_id).one()
            self.assertEqual((rev.kilometraje, rev.dominio), (85000, "AB123CD"))
            self.assertEqual((rev.turno_fecha, rev.turno_hora), (lose.day, time(11, 30)))

    def test_r04b_grouped_mileage_survives_refusal_prefill_and_fallback(self):
        """Typed grouped by the loser: kept canonical, offered back as digits the handset's
        pattern admits, and booked as the integer without the customer retyping it."""
        typed = {**TYPED, "vehicle_mileage": "1.234.567"}
        win, lose, m = self.recovered(typed=typed)
        self.assertEqual(live(self.engine, lose.thread_id)["FLOW_VEHICLE_MILEAGE"], "1234567")
        token, appointment = self.assert_prefilled(
            lose, {**EXPECTED_PREFILL, "vehicle_mileage": "1234567"})
        details, form = self.flow.details_from(appointment, date=lose.day.isoformat(), at="11:30")
        pattern = next(c for c in self.flow.inputs("DETAILS")
                       if c["name"] == "vehicle_mileage")["pattern"]
        self.assertTrue(re.fullmatch(pattern, form["vehicle_mileage"]))
        summary = svc_call(self.engine, lambda s: s.handle_prepare_summary(
            token, self.flow.submit_details(details, form)))["data"]
        with Session(self.engine) as db:
            rows = db.query(WhatsAppThreadEvidence).filter(
                WhatsAppThreadEvidence.thread_id == lose.thread_id,
                WhatsAppThreadEvidence.claim_type == "FLOW_VEHICLE_MILEAGE").count()
            self.assertEqual(rows, 1)                    # the same reading: no new row
        with ML.Meta():
            done = svc_call(self.engine, lambda s: s.handle_confirm_booking(
                token, self.flow.submit_summary(summary)))
        self.assertEqual(done["screen"], "SUCCESS")
        with Session(self.engine) as db:
            rev = db.query(Revision).filter(Revision.lead_id == lose.lead_id).one()
            self.assertEqual((rev.kilometraje, rev.turno_hora), (1234567, time(11, 30)))
        _, html = card(self.engine, lose.lead_id)
        self.assertIn("Kilometraje: 1.234.567 km", html)

    def test_r04c_customer_regroups_a_prefilled_reading_before_confirming(self):
        win, lose, m = self.recovered()
        token, appointment = self.assert_prefilled(lose)
        details, form = self.flow.details_from(appointment, date=lose.day.isoformat(), at="11:30")
        form["vehicle_mileage"] = "86.500"
        summary = svc_call(self.engine, lambda s: s.handle_prepare_summary(
            token, self.flow.submit_details(details, form)))["data"]
        with ML.Meta():
            svc_call(self.engine, lambda s: s.handle_confirm_booking(
                token, self.flow.submit_summary(summary)))
        with Session(self.engine) as db:
            rev = db.query(Revision).filter(Revision.lead_id == lose.lead_id).one()
            self.assertEqual(rev.kilometraje, 86500)

    def test_r05_the_rejected_slot_never_creates_a_booking_and_is_not_offered(self):
        win, lose, m = self.recovered()
        token, appointment = self.assert_prefilled(lose)
        self.assertEqual(appointment["selected_time"], "")
        self.assertNotIn("slot", appointment["details_prefill"])
        with ML.Meta():
            confirm(self.engine, lose, token=token, at=time(11, 30))
        with Session(self.engine) as db:
            slots = [(r.lead_id, r.turno_hora) for r in db.query(Revision).filter(
                Revision.turno_hora == lose.at)]
            self.assertEqual(slots, [(win.lead_id, lose.at)])

    def test_r06_customer_edits_a_prefilled_value_before_confirming(self):
        win, lose, m = self.recovered()
        token, appointment = self.assert_prefilled(lose)
        details, form = self.flow.details_from(appointment, date=lose.day.isoformat(), at="11:30")
        form.update({"vehicle_mileage": "86000", "email": "nuevo@example.invalid"})
        summary = svc_call(self.engine, lambda s: s.handle_prepare_summary(
            token, self.flow.submit_details(details, form)))["data"]
        with ML.Meta():
            svc_call(self.engine, lambda s: s.handle_confirm_booking(
                token, self.flow.submit_summary(summary)))
        with Session(self.engine) as db:
            rev = db.query(Revision).filter(Revision.lead_id == lose.lead_id).one()
            self.assertEqual((rev.kilometraje, rev.dominio), (86000, "AB123CD"))
            history = [r.value for r in db.query(WhatsAppThreadEvidence).filter(
                WhatsAppThreadEvidence.thread_id == lose.thread_id,
                WhatsAppThreadEvidence.claim_type == "FLOW_VEHICLE_MILEAGE")
                .order_by(WhatsAppThreadEvidence.id)]
            self.assertEqual(history, ["85000", "86000"])

    def test_r07_customer_clears_a_prefilled_value_and_it_stays_cleared(self):
        win, lose, m = self.recovered()
        token, appointment = self.assert_prefilled(lose)
        details, form = self.flow.details_from(appointment, date=lose.day.isoformat(), at="11:30")
        form.update({"vehicle_plate": "", "seller_phone": ""})
        submitted = self.flow.submit_details(details, form)
        summary = svc_call(self.engine,
                           lambda s: s.handle_prepare_summary(token, submitted))["data"]
        got = live(self.engine, lose.thread_id)
        self.assertNotIn("FLOW_VEHICLE_PLATE", got)
        self.assertNotIn("FLOW_SELLER_PHONE", got)
        self.assertEqual(init(self.engine, token)["data"]["details_prefill"],
                         {k: v for k, v in EXPECTED_PREFILL.items()
                          if k not in ("vehicle_plate", "seller_phone")})
        with ML.Meta():
            svc_call(self.engine, lambda s: s.handle_confirm_booking(
                token, self.flow.submit_summary(summary)))
        with Session(self.engine) as db:
            rev = db.query(Revision).filter(Revision.lead_id == lose.lead_id).one()
            self.assertEqual((rev.kilometraje, rev.dominio), (85000, None))

    def test_r08_with_the_switch_off_blank_is_still_not_given(self):
        w = H.seed(self.engine)
        prepare(self.engine, w, typed=ML.DETAILS, prefill=False)
        prepare(self.engine, w, typed={**ML.DETAILS, "email": "", "seller_name": ""},
                prefill=False)
        got = live(self.engine, w.thread_id)
        self.assertEqual(got["FLOW_CUSTOMER_EMAIL"], ML.DETAILS["email"])
        self.assertEqual(got["FLOW_SELLER_NAME"], ML.DETAILS["seller_name"])

    def test_r09_an_absent_key_is_never_a_clearing(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        prepare(self.engine, w, typed=ML.DETAILS)        # an older Flow: neither key sent
        got = live(self.engine, w.thread_id)
        self.assertEqual(got["FLOW_VEHICLE_PLATE"], "AB123CD")

    def test_r09b_a_blank_cannot_clear_what_the_customer_never_saw(self):
        """A live value of another vehicle or cycle was not on this customer's screen, so an
        empty input there is "not given", not a correction of it."""
        w = H.seed(self.engine)
        prepare(self.engine, w)
        for change in ({"bound_to_candidate_id": None}, {"cycle_key": 999}):
            with Session(self.engine) as db:
                db.query(WhatsAppThreadEvidence).filter(
                    WhatsAppThreadEvidence.thread_id == w.thread_id).update(change)
                db.commit()
            prepare(self.engine, w, typed={**TYPED, "vehicle_plate": "", "seller_name": ""})
            got = live(self.engine, w.thread_id)
            self.assertEqual(got["FLOW_VEHICLE_PLATE"], "AB123CD", change)
            self.assertEqual(got["FLOW_SELLER_NAME"], ML.DETAILS["seller_name"], change)

    def test_r09c_a_superseded_value_never_comes_back(self):
        """R2: a later vehicle no longer supersedes this one's rows (q31), but rows written
        before R2 can have a successor bound to another vehicle. Such a row is still history."""
        w = H.seed(self.engine)
        prepare(self.engine, w)
        from app.models import WhatsAppThreadCandidate
        with Session(self.engine) as db:
            other = WhatsAppThreadCandidate(thread_id=w.thread_id, marca="Fiat", modelo="Uno")
            db.add(other); db.flush()
            other_id = int(other.id)
            D.write_detail_rows(db, thread_id=w.thread_id, offer_id=None, candidate_id=other_id,
                                values={"FLOW_VEHICLE_PLATE": "NEW123"},
                                stamp=H.datetime.now(H.timezone.utc))
            rows = {r.bound_to_candidate_id: r for r in db.query(WhatsAppThreadEvidence).filter(
                WhatsAppThreadEvidence.thread_id == w.thread_id,
                WhatsAppThreadEvidence.claim_type == "FLOW_VEHICLE_PLATE")}
            rows[w.candidate_id].superseded_by_id = rows[other_id].id
            db.commit()
            got = D.prefill_details(db, thread_id=w.thread_id, candidate_id=w.candidate_id,
                                    cycle_key=0)
            self.assertNotIn("vehicle_plate", got)
            self.assertEqual(got["vehicle_mileage"], "85000")

    def test_r10_nothing_crosses_into_another_vehicle(self):
        """Live rows are per THREAD (F-14). A second car on this thread must not be offered
        the first car's plate."""
        win, lose, m = self.recovered()
        token = fresh_token(self.engine, lose)
        from app.models import WhatsAppThreadCandidate, WhatsAppThreadState
        with Session(self.engine) as db:
            other = WhatsAppThreadCandidate(thread_id=lose.thread_id, marca="Fiat", modelo="Uno",
                                            tipo_vehiculo="Auto", status="current_focus",
                                            zone_group="Norte", zone_detail="Benavidez")
            db.add(other); db.flush()
            db.query(WhatsAppBookingOffer).filter(
                WhatsAppBookingOffer.thread_id == lose.thread_id,
                WhatsAppBookingOffer.status == "ISSUED").update({"candidate_id": other.id})
            db.query(WhatsAppThreadState).filter(
                WhatsAppThreadState.thread_id == lose.thread_id).update(
                    {"current_focus_candidate_id": other.id})
            db.commit()
        # The person's own name follows them on their thread, as `customer_name` always has.
        # Everything about the other car and its seller stays behind.
        self.assertEqual(init(self.engine, token)["data"]["details_prefill"],
                         {"name": ML.DETAILS["name"]})

    def test_r11_nothing_crosses_a_conversation_cycle(self):
        win, lose, m = self.recovered()
        token = fresh_token(self.engine, lose)
        with Session(self.engine) as db:
            db.query(WhatsAppThreadEvidence).filter(
                WhatsAppThreadEvidence.thread_id == lose.thread_id).update({"cycle_key": 999})
            db.commit()
        self.assertEqual(init(self.engine, token)["data"]["details_prefill"],
                         {"name": ML.DETAILS["name"]})

    def test_r12_nothing_crosses_customers(self):
        win, lose, m = self.recovered()
        prefill = init(self.engine, fresh_token(self.engine, lose))["data"]["details_prefill"]
        self.assertNotIn(WINNER["vehicle_plate"], prefill.values())
        self.assertNotIn(WINNER["name"], prefill.values())
        self.assertEqual(live(self.engine, win.thread_id)["FLOW_VEHICLE_PLATE"], "ZZZ999")

    def test_r13_name_falls_back_to_the_lead_for_a_first_time_form(self):
        w = H.seed(self.engine, lead_nombre="Lucía")
        self.assertEqual(init(self.engine, w.token)["data"]["details_prefill"], {"name": "Lucía"})

    def test_r14_a_failed_read_opens_an_empty_form_not_a_broken_flow(self):
        w = H.seed(self.engine)
        with mock.patch.object(D, "prefill_details", side_effect=RuntimeError("boom")):
            data = init(self.engine, w.token)["data"]
        self.assertEqual(data["details_prefill"], {})

    # the six failure windows of the milestone ──────────────────────────────────────────

    def test_r20_process_restart_prefill_needs_no_memory(self):
        """Nothing here survives but the database: fresh engine objects, no job read."""
        win, lose, m = self.recovered()
        token = fresh_token(self.engine, lose)
        with Session(self.engine) as db:
            db.query(SlotRecoveryJob).delete()           # not even the job is consulted
            db.commit()
        self.assertEqual(init(self.engine, token)["data"]["details_prefill"], EXPECTED_PREFILL)

    def test_r21_lease_expiry_after_the_new_offer_before_the_flow(self):
        win, lose = race(self.engine)
        with R1.Edge() as edge, mock.patch.object(R, "_send_flow", side_effect=R1._Crash("x")):
            with self.assertRaises(R1._Crash):
                work(self.engine)
        self.assertEqual([k for k, *_ in edge.calls], ["text"])
        with R1.Edge() as resumed:
            work(self.engine, now=R1.later())
        self.assertEqual([k for k, *_ in resumed.calls], ["flow"])     # the text is not re-sent
        self.assertEqual(R1.job(self.engine, lose.thread_id).state, "RECOVERED")
        self.assert_prefilled(lose)

    def test_r22_duplicate_meta_submission_recovers_once(self):
        win, lose = race(self.engine, attempt_ref="delivery-1")
        with ML.Meta() as m:
            with self.assertRaises(bfs.BookingSlotConflictError):
                confirm(self.engine, lose, attempt_ref="delivery-1")
            work(self.engine)
            work(self.engine)
        self.assertEqual([t for _w, t in m.sent], [R.RECOVERY_TEXT])
        self.assertEqual(len(m.flows), 1)
        self.assertEqual(len(ML.jobs(self.engine, lose.thread_id)), 1)
        self.assert_prefilled(lose)

    def test_r23_retry_of_the_refused_request_after_recovery_sends_nothing(self):
        win, lose = race(self.engine, attempt_ref="delivery-1")
        with ML.Meta():
            work(self.engine)
        with ML.Meta() as m:
            with self.assertRaises(bfs.BookingSlotConflictError) as caught:
                confirm(self.engine, lose, attempt_ref="delivery-1")
            work(self.engine)
        self.assertEqual((m.sent, m.flows), ([], []))
        FC.validate_screen_payload("APPOINTMENT", caught.exception.refreshed_data["data"],
                                   candidate=True)
        self.assertEqual(len(ML.jobs(self.engine, lose.thread_id)), 1)
        self.assert_prefilled(lose)

    def test_r24_text_sent_then_flow_fails_details_stay_for_a_person(self):
        win, lose = race(self.engine)
        with R1.Edge(flow=("4xx",)) as edge:
            work(self.engine)
        self.assertEqual([k for k, *_ in edge.calls], ["text", "flow"])
        j = R1.job(self.engine, lose.thread_id)
        self.assertEqual(j.state, "HANDOFF")
        self.assertEqual(R1.flags(self.engine, lose)[:2], (True, True))
        details, html = card(self.engine, lose.lead_id)
        self.assertEqual((details.kilometraje, details.dominio), ("85000", "AB123CD"))
        self.assertIn("85.000 km", html)
        with R1.Edge() as later:
            work(self.engine, now=R1.later())
        self.assertEqual(later.calls, [])                # handed off: never sent again

    def test_r25_flow_sent_then_worker_interrupted(self):
        win, lose = race(self.engine)
        with R1.Edge() as edge, mock.patch.object(R, "_recovered", side_effect=R1._Crash("x")):
            with self.assertRaises(R1._Crash):
                work(self.engine)
        self.assertEqual([k for k, *_ in edge.calls], ["text", "flow"])
        self.assert_prefilled(lose)                      # the Flow is out: it opens prefilled
        with R1.Edge() as resumed:
            work(self.engine, now=R1.later())
        self.assertEqual(resumed.calls, [])              # no second Flow
        self.assertEqual(R1.job(self.engine, lose.thread_id).state, "RECOVERED")

    def test_r26_dispatch_off_is_still_fail_closed(self):
        win, lose = race(self.engine)
        off = dataclasses.replace(on(), smart_booking_ce_enabled=False)
        with ML.Meta() as m, Session(self.engine) as db, \
                mock.patch("app.settings.get_settings", lambda: off):
            R.run_once(db)
        self.assertEqual((m.sent, m.flows), ([], []))
        self.assertEqual(R1.job(self.engine, lose.thread_id).state, "HANDOFF")
        self.assertEqual(live(self.engine, lose.thread_id)["FLOW_VEHICLE_PLATE"], "AB123CD")

    def test_r27_no_abridged_flow_exists(self):
        names = sorted(p.name for p in FLOWS.glob("*.json") if "v7.8" in p.name)
        self.assertEqual(names, [CANDIDATE.name])
        source = (ROOT / "backend/app/services/booking_slot_recovery.py").read_text(encoding="utf-8")
        self.assertIn('initial_screen="APPOINTMENT"', source)
        self.assertNotIn("details_prefill", source)      # the worker carries no details at all


class I_Identity(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()

    def counts(self, w):
        with Session(self.engine) as db:
            return {"contacts": db.query(WhatsAppContact).filter(
                        WhatsAppContact.wa_id == w.wa_id).count(),
                    "threads": db.query(WhatsAppThread).filter(
                        WhatsAppThread.contact_id == w.contact_id).count(),
                    "leads": db.execute(select(func.count(func.distinct(WhatsAppThread.lead_id)))
                                        .where(WhatsAppThread.contact_id == w.contact_id)).scalar(),
                    "revisions": db.query(Revision).filter(
                        Revision.lead_id == w.lead_id).count()}

    def test_i01_loser_before_rebooking_is_one_identity_with_everything_visible(self):
        win, lose = race(self.engine)
        with ML.Meta():
            work(self.engine)
        self.assertEqual(self.counts(lose),
                         {"contacts": 1, "threads": 1, "leads": 1, "revisions": 0})
        with Session(self.engine) as db:
            self.assertEqual(db.query(Lead).count(), 2)          # winner + loser, no orphan
        details, html = card(self.engine, lose.lead_id)
        self.assertEqual(details.thread_id, lose.thread_id)
        self.assertEqual(details.vehicle, "Peugeot 208 (Auto)")
        for value in (ML.DETAILS["name"], ML.DETAILS["email"], ML.DETAILS["inspection_address"],
                      ML.DETAILS["seller_name"], ML.DETAILS["seller_phone"],
                      ML.DETAILS["listing_url"], "85.000 km", "AB123CD"):
            self.assertIn(value, html, value)
        for value in (WINNER["name"], WINNER["vehicle_plate"], WINNER["inspection_address"]):
            self.assertNotIn(value, html, value)

    def test_i02_after_fallback_the_same_identity_owns_booking_and_details(self):
        win, lose = race(self.engine)
        with ML.Meta():
            work(self.engine)
        with ML.Meta():
            confirm(self.engine, lose, token=fresh_token(self.engine, lose), at=time(11, 30))
        self.assertEqual(self.counts(lose),
                         {"contacts": 1, "threads": 1, "leads": 1, "revisions": 1})
        with Session(self.engine) as db:
            tr = db.query(ThreadRevision).filter(ThreadRevision.thread_id == lose.thread_id).one()
            self.assertEqual((tr.buyer_name, tr.seller_phone),
                             (ML.DETAILS["name"], ML.DETAILS["seller_phone"]))
            self.assertEqual(db.query(Lead).count(), 2)
        details, html = card(self.engine, lose.lead_id)
        self.assertIn("Kilometraje: 85.000 km", html)
        self.assertIn("Dominio / patente: AB123CD", html)
        self.assertNotIn(WINNER["vehicle_plate"], html)

    def test_i03_winner_card_shows_its_own_values(self):
        win, lose = race(self.engine)
        _, html = card(self.engine, win.lead_id)
        self.assertIn("Kilometraje: 12.000 km", html)
        self.assertIn("Dominio / patente: ZZZ999", html)
        self.assertNotIn("AB123CD", html)

    def test_i04_an_operator_correction_is_not_contradicted_by_the_old_draft(self):
        w = H.seed(self.engine)
        with ML.Meta():
            prepare(self.engine, w)
            confirm(self.engine, w)
        with Session(self.engine) as db:
            rev = db.query(Revision).filter(Revision.lead_id == w.lead_id).one()
            rev.kilometraje, rev.dominio = None, "XY987ZW"
            db.commit()
        details, html = card(self.engine, w.lead_id)
        self.assertEqual((details.kilometraje, details.dominio), (None, None))
        self.assertIn("Dominio / patente: XY987ZW", html)
        self.assertNotIn("AB123CD", html)
        self.assertNotIn("85.000 km", html)

    def test_i05_a_lead_with_no_details_has_no_empty_labels(self):
        from app.ui.kanban_view import render_booking_details_block
        block = render_booking_details_block(LeadBookingDetails(thread_id=1, customer_name="Ana"))
        self.assertNotIn("Kilometraje", block)
        self.assertNotIn("Dominio", block)
        self.assertEqual(render_booking_details_block(LeadBookingDetails(thread_id=1)), "")


# ── R2: one attempt, one card. An attempt is one candidate in one cycle ────────────────────

SECOND = {**ML.DETAILS, "vehicle_mileage": "120000", "vehicle_plate": "CD456EF"}
SECOND_PREFILL = {**ML.DETAILS, "vehicle_mileage": "120000", "vehicle_plate": "CD456EF"}
BLANK = {**ML.DETAILS, "vehicle_mileage": "", "vehicle_plate": ""}
FIRST_VALUES = ("AB123CD", "85.000 km")


def bound_candidate(engine, w):
    with Session(engine) as db:
        return ML.state(engine, w.thread_id).current_focus_candidate_id


def focus_on(engine, w, candidate_id):
    """The customer moves to another vehicle: the issued offer and the focus follow it, as
    r10 does. The card and the evidence depend only on these rows."""
    with Session(engine) as db:
        db.query(WhatsAppBookingOffer).filter(
            WhatsAppBookingOffer.thread_id == w.thread_id,
            WhatsAppBookingOffer.status == "ISSUED").update({"candidate_id": candidate_id})
        db.query(WhatsAppThreadState).filter(
            WhatsAppThreadState.thread_id == w.thread_id).update(
                {"current_focus_candidate_id": candidate_id})
        db.commit()


def second_car(engine, w):
    with Session(engine) as db:
        other = WhatsAppThreadCandidate(thread_id=w.thread_id, marca="Fiat", modelo="Uno",
                                        tipo_vehiculo="Auto", status="current_focus",
                                        zone_group="Norte", zone_detail="Benavidez")
        db.add(other)
        db.commit()
        other_id = int(other.id)
    focus_on(engine, w, other_id)
    return other_id


def new_cycle(engine, w, cycle):
    with Session(engine) as db:
        db.query(WhatsAppThreadState).filter(WhatsAppThreadState.thread_id == w.thread_id).update(
            {"current_cycle_start_message_db_id": cycle})
        db.commit()


def evidence(engine, w):
    """Every detail row of the thread, as the fields that decide its meaning."""
    with Session(engine) as db:
        return [(r.id, r.claim_type, r.value, r.bound_to_candidate_id, r.cycle_key,
                 r.superseded_by_id)
                for r in db.query(WhatsAppThreadEvidence).filter(
                    WhatsAppThreadEvidence.thread_id == w.thread_id,
                    WhatsAppThreadEvidence.producer == D.PRODUCER)
                .order_by(WhatsAppThreadEvidence.id)]


def active_per_attempt(rows):
    """{(claim, candidate, cycle): live rows}. Two in one attempt is a duplicate."""
    out = {}
    for _id, claim, _value, candidate, cycle, superseded in rows:
        if superseded is None:
            out[(claim, candidate, cycle)] = out.get((claim, candidate, cycle), 0) + 1
    return out


def prefill_of(engine, w, candidate_id, cycle=0):
    with Session(engine) as db:
        return D.prefill_details(db, thread_id=w.thread_id, candidate_id=candidate_id,
                                 cycle_key=cycle)


class Q_CardScope(unittest.TestCase):
    """V-01. Plate and mileage appear only beside the car they were given for."""

    def setUp(self):
        self.engine = H.sqlite_engine()

    def assert_shows_no_first_car(self, w, vehicle="Fiat Uno (Auto)"):
        details, html = card(self.engine, w.lead_id)
        self.assertEqual(details.vehicle, vehicle)
        self.assertEqual((details.kilometraje, details.dominio), (None, None))
        for value in FIRST_VALUES:
            self.assertNotIn(value, html, value)
        return details, html

    def test_q03_focus_moves_to_a_second_car(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        self.assertEqual(card(self.engine, w.lead_id)[0].dominio, "AB123CD")   # its own car
        second_car(self.engine, w)
        self.assert_shows_no_first_car(w)

    def test_q03b_second_car_submitted_blank(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        first = bound_candidate(self.engine, w)
        second_car(self.engine, w)
        prepare(self.engine, w, typed=BLANK)
        self.assert_shows_no_first_car(w)
        self.assertEqual(prefill_of(self.engine, w, first), EXPECTED_PREFILL)  # kept, not erased

    def test_q10_first_car_loses_second_books_blank(self):
        win, lose = race(self.engine)
        with ML.Meta():
            work(self.engine)
        token = fresh_token(self.engine, lose)
        car = second_car(self.engine, lose)
        with ML.Meta():
            prepare(self.engine, lose, token=token, at=time(11, 30), typed=BLANK)
            confirm(self.engine, lose, token=token, at=time(11, 30), typed=BLANK)
        with Session(self.engine) as db:
            rev = db.query(Revision).filter(Revision.lead_id == lose.lead_id).one()
            tr = db.query(ThreadRevision).filter(ThreadRevision.thread_id == lose.thread_id).one()
            self.assertEqual((rev.kilometraje, rev.dominio), (None, None))
            self.assertEqual(tr.candidate_id, car)
        _details, html = self.assert_shows_no_first_car(lose)
        self.assertNotIn("Kilometraje:", html)              # the data lines; the edit form's
        self.assertNotIn("Dominio / patente:", html)        # own labels have no colon

    def test_q11_the_second_cars_own_draft_shows_on_its_card(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        second_car(self.engine, w)
        prepare(self.engine, w, typed=SECOND)
        details, html = card(self.engine, w.lead_id)
        self.assertEqual((details.vehicle, details.kilometraje, details.dominio),
                         ("Fiat Uno (Auto)", "120000", "CD456EF"))
        self.assertIn("120.000 km", html)
        for value in FIRST_VALUES:
            self.assertNotIn(value, html, value)

    def test_q12_the_revision_is_what_a_booked_card_shows(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        second_car(self.engine, w)
        with ML.Meta():
            prepare(self.engine, w, typed=SECOND)
            confirm(self.engine, w, typed=SECOND)
        details, html = card(self.engine, w.lead_id)
        self.assertEqual((details.kilometraje, details.dominio), (None, None))  # not twice
        self.assertIn("Kilometraje: 120.000 km", html)
        self.assertIn("Dominio / patente: CD456EF", html)
        for value in FIRST_VALUES:
            self.assertNotIn(value, html, value)

    def test_q13_a_committed_null_is_not_filled_by_its_own_old_draft(self):
        w = H.seed(self.engine)
        with ML.Meta():
            prepare(self.engine, w)
            confirm(self.engine, w)
        with Session(self.engine) as db:
            rev = db.query(Revision).filter(Revision.lead_id == w.lead_id).one()
            rev.kilometraje, rev.dominio = None, None
            db.commit()
        details, html = card(self.engine, w.lead_id)
        self.assertEqual((details.kilometraje, details.dominio), (None, None))
        for value in FIRST_VALUES:
            self.assertNotIn(value, html, value)

    def test_q14_a_later_car_after_a_booking_keeps_each_cars_values_apart(self):
        w = H.seed(self.engine)
        with ML.Meta():
            prepare(self.engine, w)
            confirm(self.engine, w)
        car = second_car(self.engine, w)
        details = card(self.engine, w.lead_id)[0]
        self.assertEqual((details.vehicle, details.kilometraje, details.dominio),
                         ("Fiat Uno (Auto)", None, None))
        # The booking is the first car's, so it says nothing about the second car's own draft.
        with Session(self.engine) as db:
            D.write_detail_rows(db, thread_id=w.thread_id, offer_id=None, candidate_id=car,
                                values={"FLOW_VEHICLE_MILEAGE": "120000",
                                        "FLOW_VEHICLE_PLATE": "CD456EF"},
                                stamp=H.datetime.now(H.timezone.utc))
            db.commit()
        details = card(self.engine, w.lead_id)[0]
        self.assertEqual((details.vehicle, details.kilometraje, details.dominio),
                         ("Fiat Uno (Auto)", "120000", "CD456EF"))

    def test_q15_returning_to_the_first_car_shows_its_values_again(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        first = bound_candidate(self.engine, w)
        second_car(self.engine, w)
        prepare(self.engine, w, typed=SECOND)
        focus_on(self.engine, w, first)
        details = card(self.engine, w.lead_id)[0]
        self.assertEqual((details.vehicle, details.kilometraje, details.dominio),
                         ("Peugeot 208 (Auto)", "85000", "AB123CD"))

    def test_q16_no_established_scope_shows_nothing(self):
        for name in ("no focus", "another cycle", "another thread's candidate"):
            with self.subTest(name):
                w, other = H.seed(self.engine), H.seed(self.engine)
                prepare(self.engine, w)
                if name == "no focus":
                    with Session(self.engine) as db:
                        db.query(WhatsAppThreadState).filter(
                            WhatsAppThreadState.thread_id == w.thread_id).update(
                                {"current_focus_candidate_id": None})
                        db.commit()
                elif name == "another cycle":
                    new_cycle(self.engine, w, 77)
                else:
                    with Session(self.engine) as db:
                        db.query(WhatsAppThreadState).filter(
                            WhatsAppThreadState.thread_id == w.thread_id).update(
                                {"current_focus_candidate_id":
                                 bound_candidate(self.engine, other)})
                        db.commit()
                details, html = card(self.engine, w.lead_id)
                self.assertEqual((details.kilometraje, details.dominio), (None, None))
                self.assertNotIn("AB123CD", html)

    def test_q17_two_threads_stay_apart(self):
        a, b = H.seed(self.engine), H.seed(self.engine)
        prepare(self.engine, a)
        prepare(self.engine, b, typed=WINNER)
        second_car(self.engine, a)
        second_car(self.engine, b)
        prepare(self.engine, a, typed=SECOND)
        with Session(self.engine) as db:
            got = lead_booking_details(db, [a.lead_id, b.lead_id])
        self.assertEqual((got[a.lead_id].kilometraje, got[a.lead_id].dominio),
                         ("120000", "CD456EF"))
        self.assertEqual((got[b.lead_id].kilometraje, got[b.lead_id].dominio), (None, None))
        for value in ("ZZZ999", "12.000 km"):
            self.assertNotIn(value, card(self.engine, a.lead_id)[1], value)

    def test_q18_the_read_changes_nothing(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        second_car(self.engine, w)
        before = evidence(self.engine, w)
        card(self.engine, w.lead_id)
        self.assertEqual(evidence(self.engine, w), before)
        with Session(self.engine) as db:
            self.assertFalse({"kilometraje", "dominio"} & set(Lead.__table__.columns.keys()))

    def test_q19_what_the_displayed_attempt_emptied_stays_empty(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        second_car(self.engine, w)
        other = {**SECOND, "email": "segundo@example.invalid"}
        prepare(self.engine, w, typed=other)
        prepare(self.engine, w, typed={**other, "email": ""})
        self.assertIsNone(card(self.engine, w.lead_id)[0].email)
        self.assertNotIn("FLOW_CUSTOMER_EMAIL", live(self.engine, w.thread_id))

    def test_q19b_a_newer_attempt_does_not_refill_what_this_one_emptied(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        first = bound_candidate(self.engine, w)
        second = second_car(self.engine, w)
        other = {**SECOND, "email": "segundo@example.invalid"}
        prepare(self.engine, w, typed=other)
        prepare(self.engine, w, typed={**other, "email": ""})
        focus_on(self.engine, w, first)
        prepare(self.engine, w, typed={**TYPED, "email": "corregido@example.invalid"})
        focus_on(self.engine, w, second)
        self.assertIsNone(card(self.engine, w.lead_id)[0].email)

    def test_q20_each_attempt_shows_what_it_submitted(self):
        """A resubmission that repeats itself writes nothing, so the newest row in the thread
        can be another car's. The displayed attempt still shows its own."""
        w = H.seed(self.engine)
        prepare(self.engine, w)
        first = bound_candidate(self.engine, w)
        second_car(self.engine, w)
        prepare(self.engine, w, typed={**SECOND, "email": "segundo@example.invalid"})
        focus_on(self.engine, w, first)
        prepare(self.engine, w)
        self.assertEqual(card(self.engine, w.lead_id)[0].email, ML.DETAILS["email"])


class E2_Summary(unittest.TestCase):
    """V-02. With the switch on, SUMMARY always supplies what the candidate declares."""

    CASES = (("both absent", {}, "", ""),
             ("only mileage", {"vehicle_mileage": "85.000"}, "85000", ""),
             ("only plate", {"vehicle_plate": "ab-123 cd"}, "", "AB123CD"),
             ("both present", {"vehicle_mileage": "85000", "vehicle_plate": "ab-123 cd"},
              "85000", "AB123CD"),
             ("both blank", {"vehicle_mileage": "", "vehicle_plate": ""}, "", ""),
             ("explicit zero", {"vehicle_mileage": "0"}, "0", ""))

    def setUp(self):
        self.engine = H.sqlite_engine()
        self.flow = Flow()

    def test_e01_every_case_is_complete_through_the_real_validator_and_artifact(self):
        for name, extra, mileage, plate in self.CASES:
            with self.subTest(name):
                w = H.seed(self.engine)
                summary = prepare(self.engine, w, typed={**ML.DETAILS, **extra})["data"]
                FC.validate_screen_payload("SUMMARY", summary, candidate=True)
                self.assertEqual((summary["vehicle_mileage"], summary["vehicle_plate"]),
                                 (mileage, plate))
                self.assertEqual("Kilometraje" in summary["customer_summary"], bool(mileage))
                self.assertEqual("Dominio" in summary["customer_summary"], bool(plate))
                self.flow.submit_summary(summary)    # raises on a declared key not supplied

    def test_e02_omitted_inputs_book_with_nothing_invented(self):
        w = H.seed(self.engine)
        summary = prepare(self.engine, w, typed=ML.DETAILS)["data"]
        final = self.flow.submit_summary(summary)
        self.assertEqual((final["vehicle_mileage"], final["vehicle_plate"]), ("", ""))
        with ML.Meta():
            done = svc_call(self.engine, lambda s: s.handle_confirm_booking(w.token, final))
        self.assertEqual(done["screen"], "SUCCESS")
        with Session(self.engine) as db:
            rev = db.query(Revision).filter(Revision.lead_id == w.lead_id).one()
            self.assertEqual((rev.kilometraje, rev.dominio), (None, None))

    def test_e03_switch_off_legacy_payload_is_the_published_contract(self):
        w = H.seed(self.engine)
        summary = prepare(self.engine, w, typed=ML.DETAILS, prefill=False)["data"]
        FC.validate_screen_payload("SUMMARY", summary)
        self.assertEqual(set(summary), FC.SUMMARY_KEYS)
        self.assertFalse(FC.VEHICLE_IDENTITY_KEYS & set(summary))
        with self.assertRaises(ValueError):
            FC.validate_screen_payload("SUMMARY", summary, candidate=True)

    def test_e04_an_omitted_key_clears_nothing(self):
        """Absent is all v7.7 can send. Neither the switch nor the two keys now always on
        SUMMARY make it a clearing."""
        for prefill, at in ((True, time(10, 0)), (False, time(11, 30))):
            with self.subTest(prefill=prefill):
                w = H.seed(self.engine, at=at)
                prepare(self.engine, w)
                before = evidence(self.engine, w)
                prepare(self.engine, w, typed=ML.DETAILS, prefill=prefill)
                self.assertEqual(evidence(self.engine, w), before)
                with ML.Meta():
                    confirm(self.engine, w, typed=ML.DETAILS, prefill=prefill)
                got = live(self.engine, w.thread_id)
                self.assertEqual((got["FLOW_VEHICLE_MILEAGE"], got["FLOW_VEHICLE_PLATE"]),
                                 ("85000", "AB123CD"))

    def test_e05_a_prefilled_value_the_customer_emptied_is_removed_at_confirm(self):
        """Meta omits the emptied input, so prepare_summary cannot tell. SUMMARY then returns ""
        and confirm_booking carries it, which is the customer's own decision reaching us."""
        w = H.seed(self.engine)
        prepare(self.engine, w)
        summary = prepare(self.engine, w, typed=ML.DETAILS)["data"]
        self.assertEqual(live(self.engine, w.thread_id)["FLOW_VEHICLE_PLATE"], "AB123CD")
        final = self.flow.submit_summary(summary)
        with ML.Meta():
            svc_call(self.engine, lambda s: s.handle_confirm_booking(w.token, final))
        got = live(self.engine, w.thread_id)
        self.assertNotIn("FLOW_VEHICLE_PLATE", got)
        self.assertNotIn("FLOW_VEHICLE_MILEAGE", got)
        with Session(self.engine) as db:
            rev = db.query(Revision).filter(Revision.lead_id == w.lead_id).one()
            self.assertEqual((rev.kilometraje, rev.dominio), (None, None))


class Q_Rebinding(unittest.TestCase):
    """V-03. The same value in another attempt is that attempt's fact too."""

    def setUp(self):
        self.engine = H.sqlite_engine()

    def two_cars(self, at=time(10, 0)):
        w = H.seed(self.engine, at=at)
        prepare(self.engine, w)
        first = bound_candidate(self.engine, w)
        second = second_car(self.engine, w)
        prepare(self.engine, w, typed=SECOND)
        return w, first, second

    def test_q30_the_repeated_values_are_evidenced_for_the_second_car(self):
        w, first, second = self.two_cars()
        self.assertEqual(prefill_of(self.engine, w, second), SECOND_PREFILL)

    def test_q31_the_first_cars_evidence_is_untouched(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        first = bound_candidate(self.engine, w)
        before = evidence(self.engine, w)
        second_car(self.engine, w)
        prepare(self.engine, w, typed=SECOND)
        self.assertEqual(evidence(self.engine, w)[:len(before)], before)
        self.assertEqual(prefill_of(self.engine, w, first), EXPECTED_PREFILL)

    def test_q32_second_car_loses_a_real_race_and_gets_all_of_it_back(self):
        at = time(10, 0)
        w, first, second = self.two_cars(at)
        rival = H.seed(self.engine, at=at)
        with ML.Meta():
            prepare(self.engine, rival, typed=WINNER)
            confirm(self.engine, rival, typed=WINNER)
        with ML.Meta():
            with self.assertRaises(bfs.BookingSlotConflictError):
                confirm(self.engine, w, typed=SECOND)
        with ML.Meta() as m:
            work(self.engine)
        self.assertEqual((len(m.flows), [t for _w, t in m.sent]), (1, [R.RECOVERY_TEXT]))
        token = fresh_token(self.engine, w)
        screen = init(self.engine, token)
        FC.validate_screen_payload("APPOINTMENT", screen["data"], candidate=True)
        self.assertEqual(screen["data"]["details_prefill"], SECOND_PREFILL)
        self.assertEqual(prefill_of(self.engine, w, first), EXPECTED_PREFILL)
        for value in screen["data"]["details_prefill"].values():
            self.assertNotIn(value, WINNER.values())
        with ML.Meta():
            confirm(self.engine, w, token=token, at=time(11, 30), typed=SECOND)
        with Session(self.engine) as db:
            rev = db.query(Revision).filter(Revision.lead_id == w.lead_id).one()
            self.assertEqual((rev.kilometraje, rev.dominio), (120000, "CD456EF"))

    def test_q33_a_retry_in_the_same_attempt_writes_nothing(self):
        w, first, second = self.two_cars()
        before = evidence(self.engine, w)
        for _ in range(3):
            prepare(self.engine, w, typed=SECOND)
        self.assertEqual(evidence(self.engine, w), before)
        self.assertEqual(set(active_per_attempt(before).values()), {1})
        with ML.Meta():
            confirm(self.engine, w, typed=SECOND)
            confirm(self.engine, w, typed=SECOND)
        rows = evidence(self.engine, w)
        self.assertEqual(rows, before)
        self.assertEqual(set(active_per_attempt(rows).values()), {1})

    def test_q34_going_back_repeats_nothing_and_steals_nothing(self):
        w, first, second = self.two_cars()
        before = evidence(self.engine, w)
        focus_on(self.engine, w, first)
        prepare(self.engine, w)
        self.assertEqual(evidence(self.engine, w), before)
        self.assertEqual(prefill_of(self.engine, w, second), SECOND_PREFILL)

    def test_q35_a_later_cycle_on_the_same_car_is_its_own_attempt(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        car = bound_candidate(self.engine, w)
        before = evidence(self.engine, w)
        new_cycle(self.engine, w, 77)
        self.assertEqual(prefill_of(self.engine, w, car, 77), {})
        prepare(self.engine, w)
        self.assertEqual(prefill_of(self.engine, w, car, 77), EXPECTED_PREFILL)
        self.assertEqual(prefill_of(self.engine, w, car, 0), EXPECTED_PREFILL)
        rows = evidence(self.engine, w)
        self.assertEqual(rows[:len(before)], before)
        prepare(self.engine, w)
        self.assertEqual(evidence(self.engine, w), rows)

    def test_q36_a_correction_supersedes_inside_its_attempt_only(self):
        w, first, second = self.two_cars()
        prepare(self.engine, w, typed={**SECOND, "vehicle_plate": "EF789GH"})
        rows = [r for r in evidence(self.engine, w) if r[1] == "FLOW_VEHICLE_PLATE"]
        self.assertEqual([(r[2], r[3], r[5] is None) for r in rows],
                         [("AB123CD", first, True), ("CD456EF", second, False),
                          ("EF789GH", second, True)])
        self.assertEqual(rows[1][5], rows[2][0])          # explicit, and it names its successor

    def test_q37_a_blank_clears_only_inside_its_attempt(self):
        w, first, second = self.two_cars()
        prepare(self.engine, w, typed=BLANK)
        self.assertEqual(prefill_of(self.engine, w, first), EXPECTED_PREFILL)
        got = prefill_of(self.engine, w, second)
        self.assertNotIn("vehicle_plate", got)
        self.assertNotIn("vehicle_mileage", got)

    def test_q37b_a_blank_cannot_clear_a_row_with_no_candidate(self):
        """`prefill_details` never offers such a row, so no customer can have seen it."""
        w = H.seed(self.engine)
        with Session(self.engine) as db:
            for value in ("AB123CD", ""):
                D.write_detail_rows(db, thread_id=w.thread_id, offer_id=None, candidate_id=None,
                                    values={"FLOW_VEHICLE_PLATE": value},
                                    stamp=H.datetime.now(H.timezone.utc))
            db.commit()
        self.assertEqual([r[2] for r in evidence(self.engine, w)], ["AB123CD"])

    def test_q38_same_values_on_two_customers_stay_on_their_own_threads(self):
        a, b = H.seed(self.engine), H.seed(self.engine)
        prepare(self.engine, a)
        prepare(self.engine, b)
        car_a, car_b = bound_candidate(self.engine, a), bound_candidate(self.engine, b)
        self.assertEqual(prefill_of(self.engine, a, car_a), EXPECTED_PREFILL)
        self.assertEqual(prefill_of(self.engine, b, car_b), EXPECTED_PREFILL)
        self.assertEqual(prefill_of(self.engine, a, car_b), {})
        self.assertEqual(prefill_of(self.engine, b, car_a), {})

    def test_q39_two_attempts_never_share_an_identity(self):
        args = ("FLOW_CUSTOMER_EMAIL", "ana@example.invalid", None)
        self.assertNotEqual(D._claim_id(1, *args, 10, 0), D._claim_id(1, *args, 11, 0))
        self.assertNotEqual(D._claim_id(1, *args, 10, 0), D._claim_id(1, *args, 10, 1))
        self.assertEqual(D._claim_id(1, *args, 10, 0), D._claim_id(1, *args, 10, 0))


# ── R2A: what describes a car stays with that car ──────────────────────────────────────────

CAR_TWO = {"inspection_address": "Calle Segunda 2", "seller_name": "Vendedora Dos",
           "seller_phone": "1133334444", "listing_url": "https://example.invalid/aviso/dos",
           "vehicle_mileage": "120000", "vehicle_plate": "CD456EF"}
CAR_TWO_TYPED = {"name": ML.DETAILS["name"], "email": ML.DETAILS["email"], **CAR_TWO}
NO_CAR_DETAILS = {"name": ML.DETAILS["name"], "email": ML.DETAILS["email"],
                  "inspection_address": "", "seller_name": "", "seller_phone": "",
                  "listing_url": "", "vehicle_mileage": "", "vehicle_plate": ""}
CAR_FIELDS = ("inspection_address", "seller_name", "seller_phone", "listing_url", "kilometraje",
              "dominio")
CAR_ONE_VALUES = (ML.DETAILS["inspection_address"], ML.DETAILS["seller_name"],
                  ML.DETAILS["seller_phone"], ML.DETAILS["listing_url"], "AB123CD", "85.000 km")


def car_fields(details):
    return {f: getattr(details, f) for f in CAR_FIELDS}


NOTHING = dict.fromkeys(CAR_FIELDS)
CAR_ONE_DRAFT = {"inspection_address": ML.DETAILS["inspection_address"],
                 "seller_name": ML.DETAILS["seller_name"],
                 "seller_phone": ML.DETAILS["seller_phone"],
                 "listing_url": ML.DETAILS["listing_url"],
                 "kilometraje": "85000", "dominio": "AB123CD"}
CAR_TWO_DRAFT = {"inspection_address": CAR_TWO["inspection_address"],
                 "seller_name": CAR_TWO["seller_name"], "seller_phone": CAR_TWO["seller_phone"],
                 "listing_url": CAR_TWO["listing_url"], "kilometraje": "120000",
                 "dominio": "CD456EF"}
#: A committed card: `revisions` owns these two, shown by the revision lines, not this block.
CAR_TWO_BOOKED = {**CAR_TWO_DRAFT, "kilometraje": None, "dominio": None}


class Q_CarContext(unittest.TestCase):
    """The owner's matrix. Numbers are the cases of milestone R2A."""

    def setUp(self):
        self.engine = H.sqlite_engine()

    def shown(self, w):
        return card(self.engine, w.lead_id)

    def assert_none_of_car_one(self, w):
        details, html = self.shown(w)
        self.assertEqual(car_fields(details), NOTHING)
        for value in CAR_ONE_VALUES:
            self.assertNotIn(value, html, value)
        return details, html

    def assert_only_car_two(self, w, expected=CAR_TWO_DRAFT):
        details, html = self.shown(w)
        self.assertEqual(car_fields(details), expected)
        for value in CAR_ONE_VALUES:
            self.assertNotIn(value, html, value)
        return details, html

    def test_x01_second_car_with_nothing_shows_nothing_of_the_first(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        self.assertEqual(car_fields(self.shown(w)[0]), CAR_ONE_DRAFT)
        second_car(self.engine, w)
        details, _ = self.assert_none_of_car_one(w)
        self.assertIsNone(details.requested_slot)
        self.assertEqual(details.vehicle, "Fiat Uno (Auto)")
        prepare(self.engine, w, typed=NO_CAR_DETAILS)        # and after submitting blank
        self.assert_none_of_car_one(w)

    def test_x02_second_car_with_its_own_values_shows_only_those(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        second_car(self.engine, w)
        prepare(self.engine, w, typed=CAR_TWO_TYPED)
        self.assert_only_car_two(w)

    def test_x03_returning_to_the_first_car_finds_it_intact(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        first = bound_candidate(self.engine, w)
        rows = evidence(self.engine, w)
        second_car(self.engine, w)
        prepare(self.engine, w, typed=CAR_TWO_TYPED)
        focus_on(self.engine, w, first)
        details = self.shown(w)[0]
        self.assertEqual(car_fields(details), CAR_ONE_DRAFT)
        self.assertEqual(details.vehicle, "Peugeot 208 (Auto)")
        self.assertEqual(evidence(self.engine, w)[:len(rows)], rows)

    def test_x04_second_car_commits_blank_and_falls_back_to_nothing(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        car = second_car(self.engine, w)
        with ML.Meta():
            prepare(self.engine, w, typed=NO_CAR_DETAILS)
            confirm(self.engine, w, typed=NO_CAR_DETAILS)
        with Session(self.engine) as db:
            tr = db.query(ThreadRevision).filter(ThreadRevision.thread_id == w.thread_id).one()
            self.assertEqual(tr.candidate_id, car)
            self.assertEqual((tr.seller_name, tr.seller_phone, tr.publication_url),
                             (None, None, None))
        self.assert_none_of_car_one(w)

    def test_x05_second_car_commits_its_own_and_booking_and_card_agree(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        second_car(self.engine, w)
        with ML.Meta():
            prepare(self.engine, w, typed=CAR_TWO_TYPED)
            confirm(self.engine, w, typed=CAR_TWO_TYPED)
        details, html = self.assert_only_car_two(w, CAR_TWO_BOOKED)
        with Session(self.engine) as db:
            tr = db.query(ThreadRevision).filter(ThreadRevision.thread_id == w.thread_id).one()
            rev = db.query(Revision).filter(Revision.lead_id == w.lead_id).one()
            self.assertEqual((details.inspection_address, details.seller_name,
                              details.seller_phone, details.listing_url),
                             (tr.address, tr.seller_name, tr.seller_phone, tr.publication_url))
            self.assertEqual(rev.direccion_texto, details.inspection_address)
            self.assertEqual((rev.kilometraje, rev.dominio), (120000, "CD456EF"))
        self.assertIn("Kilometraje: 120.000 km", html)
        self.assertIn("Dominio / patente: CD456EF", html)

    def test_x06_first_car_loses_a_race_second_books_nothing_mixes(self):
        win, lose = race(self.engine)
        first = bound_candidate(self.engine, lose)
        with ML.Meta():
            work(self.engine)
        token = fresh_token(self.engine, lose)
        self.assertEqual(init(self.engine, token)["data"]["details_prefill"], EXPECTED_PREFILL)
        self.assertEqual(car_fields(self.shown(lose)[0]), CAR_ONE_DRAFT)
        second_car(self.engine, lose)
        self.assertEqual(init(self.engine, token)["data"]["details_prefill"],
                         {"name": ML.DETAILS["name"]})        # nothing of car 1 is offered
        self.assert_none_of_car_one(lose)
        with ML.Meta():
            prepare(self.engine, lose, token=token, at=time(11, 30), typed=CAR_TWO_TYPED)
            confirm(self.engine, lose, token=token, at=time(11, 30), typed=CAR_TWO_TYPED)
        self.assert_only_car_two(lose, CAR_TWO_BOOKED)
        self.assertEqual(prefill_of(self.engine, lose, first), EXPECTED_PREFILL)
        for value in (WINNER["inspection_address"], WINNER["seller_name"],
                      WINNER["vehicle_plate"]):
            self.assertNotIn(value, self.shown(lose)[1], value)

    def test_x07_the_person_follows_the_thread_the_car_does_not(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        second_car(self.engine, w)
        details, _ = self.assert_none_of_car_one(w)
        self.assertEqual((details.customer_name, details.email),
                         (ML.DETAILS["name"], ML.DETAILS["email"]))
        prepare(self.engine, w, typed=CAR_TWO_TYPED)
        details = self.assert_only_car_two(w)[0]
        self.assertEqual((details.customer_name, details.email),
                         (ML.DETAILS["name"], ML.DETAILS["email"]))

    def test_x08_two_customers_share_nothing(self):
        a, b = H.seed(self.engine), H.seed(self.engine)
        prepare(self.engine, a)
        prepare(self.engine, b, typed=WINNER)
        second_car(self.engine, a)
        second_car(self.engine, b)
        prepare(self.engine, a, typed=CAR_TWO_TYPED)
        with Session(self.engine) as db:
            got = lead_booking_details(db, [a.lead_id, b.lead_id])
        self.assertEqual(car_fields(got[a.lead_id]), CAR_TWO_DRAFT)
        self.assertEqual(car_fields(got[b.lead_id]), NOTHING)
        self.assertEqual(got[b.lead_id].customer_name, WINNER["name"])
        self.assertEqual(got[a.lead_id].customer_name, ML.DETAILS["name"])

    def test_x09_only_the_current_cycle_of_the_same_car(self):
        w = H.seed(self.engine)
        prepare(self.engine, w)
        new_cycle(self.engine, w, 77)
        self.assert_none_of_car_one(w)
        prepare(self.engine, w, typed=CAR_TWO_TYPED)
        self.assert_only_car_two(w)
        new_cycle(self.engine, w, 0)
        self.assertEqual(car_fields(self.shown(w)[0]), CAR_ONE_DRAFT)

    def test_x10_rendering_writes_nothing(self):
        w = H.seed(self.engine)
        with ML.Meta():
            prepare(self.engine, w)
            confirm(self.engine, w)
        second_car(self.engine, w)

        def snapshot():
            with Session(self.engine) as db:
                return (evidence(self.engine, w),
                        [(r.id, r.kilometraje, r.dominio, r.direccion_texto)
                         for r in db.query(Revision).order_by(Revision.id)],
                        [(r.id, r.status, r.address, r.seller_name, r.seller_phone,
                          r.publication_url)
                         for r in db.query(ThreadRevision).order_by(ThreadRevision.id)],
                        [(r.id, r.nombre, r.email, r.estado)
                         for r in db.query(Lead).order_by(Lead.id)])
        before = snapshot()
        for _ in range(2):
            self.shown(w)
        self.assertEqual(snapshot(), before)

    def test_x11_a_committed_card_ignores_a_newer_draft_of_its_own_car(self):
        w = H.seed(self.engine)
        with ML.Meta():
            prepare(self.engine, w)
            confirm(self.engine, w)
        car = bound_candidate(self.engine, w)
        with Session(self.engine) as db:
            D.write_detail_rows(db, thread_id=w.thread_id, offer_id=None, candidate_id=car,
                                values={"FLOW_SELLER_NAME": "Otro Vendedor",
                                        "FLOW_REQUESTED_SLOT": "2099-01-01 09:00"},
                                stamp=H.datetime.now(H.timezone.utc))
            db.commit()
        details = self.shown(w)[0]
        self.assertEqual(details.seller_name, ML.DETAILS["seller_name"])
        self.assertEqual(details.requested_slot,
                         f"{w.day.isoformat()} {w.at.strftime('%H:%M')}")

    def test_x12_a_committed_blank_is_not_filled_by_its_own_draft(self):
        w = H.seed(self.engine)
        with ML.Meta():
            prepare(self.engine, w)
            confirm(self.engine, w)
        with Session(self.engine) as db:
            tr = db.query(ThreadRevision).filter(ThreadRevision.thread_id == w.thread_id).one()
            tr.seller_name = tr.publication_url = None
            db.commit()
        details = self.shown(w)[0]
        self.assertEqual((details.seller_name, details.listing_url), (None, None))

    def test_x13_each_booked_car_shows_its_own_booking(self):
        """The thread's latest booking is not the displayed car's."""
        w = H.seed(self.engine)
        with ML.Meta():
            prepare(self.engine, w)
            confirm(self.engine, w)
        first = bound_candidate(self.engine, w)
        second = second_car(self.engine, w)
        with Session(self.engine) as db:
            old = db.query(ThreadRevision).filter(ThreadRevision.thread_id == w.thread_id).one()
            db.add(ThreadRevision(thread_id=w.thread_id, candidate_id=second, status="booked",
                                  address=CAR_TWO["inspection_address"],
                                  seller_name=CAR_TWO["seller_name"],
                                  seller_phone=CAR_TWO["seller_phone"],
                                  publication_url=CAR_TWO["listing_url"],
                                  scheduled_date=old.scheduled_date,
                                  scheduled_time=time(11, 30)))
            db.commit()
        self.assertEqual(car_fields(self.shown(w)[0]), CAR_TWO_BOOKED)
        focus_on(self.engine, w, first)
        self.assertEqual(car_fields(self.shown(w)[0]),
                         {**CAR_ONE_DRAFT, "kilometraje": None, "dominio": None})

    def test_x14_a_later_car_does_not_borrow_an_earlier_booking(self):
        w = H.seed(self.engine)
        with ML.Meta():
            prepare(self.engine, w)
            confirm(self.engine, w)
        second_car(self.engine, w)
        details, _ = self.shown(w)
        self.assertEqual(car_fields(details), NOTHING)
        self.assertIsNone(details.requested_slot)
        self.assertEqual(details.customer_name, ML.DETAILS["name"])   # the person still shows

    def test_x15_no_displayed_car_but_a_booking_shows_that_booking(self):
        w = H.seed(self.engine)
        with ML.Meta():
            prepare(self.engine, w)
            confirm(self.engine, w)
        with Session(self.engine) as db:
            db.query(WhatsAppThreadState).filter(
                WhatsAppThreadState.thread_id == w.thread_id).update(
                    {"current_focus_candidate_id": None})
            db.commit()
        details = self.shown(w)[0]
        self.assertIsNone(details.vehicle)
        self.assertEqual(details.seller_name, ML.DETAILS["seller_name"])

    def test_x16_every_claim_is_classified_and_only_two_follow_the_person(self):
        from app.services.lead_booking_details import PERSON_CLAIMS
        self.assertEqual(PERSON_CLAIMS, {"FLOW_CUSTOMER_NAME", "FLOW_CUSTOMER_EMAIL"})
        self.assertLess(PERSON_CLAIMS, set(D.ALL_CLAIM_TYPES))


if __name__ == "__main__":
    unittest.main()
