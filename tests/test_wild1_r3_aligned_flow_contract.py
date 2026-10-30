"""WILD1-F3-R2 — the refusal contract, re-derived from the Flow that is actually published.

WHY THIS MODULE EXISTS BESIDE `test_wild1_booking_flow_gate_and_contract.py`
    That suite proves the correction. This one proves the correction was derived from the
    right baseline, which is the thing R1 got wrong and no test could have caught, because
    every assertion R1 made was consistent with R1's false premise.

    R1 believed `meta/flows/booking/PUBLISHED_28104222025943520_v7.3.json` was the live
    definition of asset 28104222025943520. On 2026-10-30 the owner exported the published
    JSON from Meta: it is `CANDIDATE_v7.4-prefill-r3.json`'s structure. The 2026-09-16 Gate
    B Final Certification had already proved that by downloading the definition from the
    Flows API and canonicalising it to `5c4c68d1…`. So R1's target removed five fields a
    shipped screen declares, and would have re-added a customer telephone input that live
    customers stopped seeing on 2026-09-16.

    The lesson, written as tests rather than as a comment: a Flow target is only as good as
    the artifact it was derived FROM, and the derivation must be mechanical and checkable.

R2-B  baseline        the target derives from r3, by canonical fingerprint, deterministically
R2-D  delta           exactly four fields, two components, two edges; nothing else, anywhere
R2-C  contract        declared == supplied == 16, in both directions, no exclusion set
R2-S  server authority the five restored values come from server state, never from the Flow
R2-O  OFF behaviour   Smart Booking off creates nothing, on any surface
R2-N  ON behaviour    one offer, one token, one sixteen-key payload, no early booking
R2-R  refusal         both response paths carry their own flag and not the other's
"""
from __future__ import annotations

import ast
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import date, timedelta
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
from app.models import (Lead, ThreadRevision, ViaticosZone, WhatsAppContact,
                        WhatsAppThread, WhatsAppThreadCandidate, WhatsAppThreadState,
                        WhatsAppMessage)
from app.schemas.schedule import ScheduleCheckOut, ScheduleSlotOut, ScheduleSlotsOut
from app.services import flow_contract as FC
from app.services.booking_dispatch_gate import booking_dispatch_allowed
from app.services.booking_flow_service import (BookingFlowService,
                                               BookingSlotConflictError,
                                               BookingTokenError,
                                               make_booking_token)
from offer_fixture import (ensure_launch_professional, route_authority_availability,
                           seed_live_offer, start_converged_booking,
                           stop_converged_booking)

FLOW_DIR = ROOT / "meta" / "flows" / "booking"
R3 = FLOW_DIR / "CANDIDATE_v7.4-prefill-r3.json"
TARGET = ROOT / FC.TARGET_FLOW_PATH
GENERATOR = FLOW_DIR / "make_r3_refusal_target.py"

#: The twelve APPOINTMENT declarations of the published Flow, in the owner's 2026-10-30
#: export order. Written out rather than read from the artifact: the point of the test is to
#: compare the artifact against the owner's evidence, and a set read from the artifact would
#: agree with the artifact by construction.
OWNER_EXPORT_APPOINTMENT = [
    "booking_token", "vehicle_summary", "location_summary",
    "date", "is_date_enabled", "time", "is_time_enabled",
    "selected_date", "selected_time",
    "customer_name", "contact_phone", "contact_phone_display",
]
#: The eight values the owner's export shows APPOINTMENT -> DETAILS carrying.
OWNER_EXPORT_NAVIGATE = [
    "booking_token", "vehicle_summary", "location_summary", "date", "time",
    "customer_name", "contact_phone", "contact_phone_display",
]

WA_ID = "5491100000001"
SLOTS = ["10:00", "11:00", "12:00"]
FUTURE = date.today() + timedelta(days=3)


def _sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def _canonical(doc):
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def _screens(path):
    return {s["id"]: s for s in json.load(open(path))["screens"]}


def _components(layout):
    """Each component's own properties, without its children.

    A container's raw dump contains everything below it, so inserting one component would
    read as a rewrite of every ancestor. Comparing bodies without `children` is what makes
    "nothing was altered" a measurable claim rather than a diff of the whole tree.
    """
    out = []

    def walk(n):
        if isinstance(n, dict):
            if n.get("type"):
                out.append(json.dumps({k: v for k, v in n.items() if k != "children"},
                                      sort_keys=True, ensure_ascii=False))
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(layout)
    return out


def _input_names(layout):
    out = []

    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "TextInput":
                out.append(n.get("name"))
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(layout)
    return out


def _actions(screen):
    """Every action in a screen, keyed by its own payload trigger or destination."""
    out = []

    def walk(n):
        if isinstance(n, dict):
            for k in ("on-click-action", "on-select-action"):
                a = n.get(k)
                if isinstance(a, dict):
                    out.append(json.dumps({
                        "hook": k,
                        "name": a.get("name"),
                        "next": (a.get("next") or {}).get("name"),
                        "payload": dict(sorted((a.get("payload") or {}).items())),
                    }, sort_keys=True, ensure_ascii=False))
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(screen.get("layout", {}))
    return out


# ══════════════════════════════════════════════════════════════════════════════
# R2-B — the baseline
# ══════════════════════════════════════════════════════════════════════════════
class TestBaseline(unittest.TestCase):

    def test_r2_b01_the_source_artifact_is_the_published_structure(self):
        """R2-B01 — r3 canonicalises to the fingerprint the Gate B certification recorded
        for the definition it downloaded from the Flows API."""
        self.assertEqual(_canonical(json.load(open(R3))),
                         FC.PUBLISHED_FLOW_CANONICAL_SHA256)
        self.assertEqual(_sha(R3), FC.PUBLISHED_FLOW_SHA256)
        self.assertEqual(ROOT / FC.PUBLISHED_FLOW_PATH, R3)

    def test_r2_b02_the_artifact_matches_the_owner_export_field_by_field(self):
        """R2-B02 — the owner's 2026-10-30 Meta export, compared against the artifact.

        This is the only check in the suite whose expected values are transcribed from
        outside the repository. If Meta's published definition and this artifact ever
        disagree, this is the test that says so.
        """
        appt, details = _screens(R3)["APPOINTMENT"], _screens(R3)["DETAILS"]
        self.assertEqual(list(appt["data"]), OWNER_EXPORT_APPOINTMENT)
        nav = [a for a in _actions(appt) if json.loads(a)["name"] == "navigate"]
        self.assertEqual(len(nav), 1, nav)
        self.assertEqual(sorted(json.loads(nav[0])["payload"]),
                         sorted(OWNER_EXPORT_NAVIGATE))
        self.assertEqual(json.loads(nav[0])["next"], "DETAILS")
        blob = json.dumps(details["layout"], ensure_ascii=False)
        self.assertIn("Te contactamos a este WhatsApp:", blob)
        self.assertIn("${data.contact_phone_display}", blob)
        self.assertNotIn("phone", _input_names(details["layout"]),
                         "the published DETAILS screen asks for no telephone number")
        prep = [json.loads(a) for a in _actions(details)
                if (json.loads(a)["payload"] or {}).get("trigger") == "prepare_summary"]
        self.assertEqual(len(prep), 1, prep)
        self.assertEqual(prep[0]["payload"]["phone"], "${data.contact_phone}")
        rm = json.load(open(R3))["routing_model"]
        self.assertEqual(rm, {"APPOINTMENT": ["DETAILS"], "DETAILS": ["SUMMARY"],
                              "SUMMARY": []})
        self.assertIs(_screens(R3)["SUMMARY"].get("terminal"), True)

    def test_r2_b03_the_target_regenerates_deterministically_from_r3(self):
        """R2-B03 — the delta is what the generator does, and nothing else.

        The generator is run twice into fresh temporary files and both are compared, byte
        for byte, with the committed target. A hand edit to the artifact fails here.
        """
        outs = []
        with tempfile.TemporaryDirectory() as tmp:
            for i in (1, 2):
                dst = pathlib.Path(tmp) / f"regen{i}.json"
                r = subprocess.run([sys.executable, "-I", str(GENERATOR), str(R3), str(dst)],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                outs.append(dst.read_bytes())
        self.assertEqual(outs[0], outs[1], "the generator is not deterministic")
        self.assertEqual(hashlib.sha256(outs[0]).hexdigest(), FC.TARGET_FLOW_SHA256)
        self.assertEqual(outs[0], TARGET.read_bytes(),
                         "the committed target is not what the generator produces")

    def test_r2_b04_the_generator_refuses_every_other_baseline(self):
        """R2-B04 — R1's mistake, made impossible to repeat silently.

        The generator pins its source by canonical fingerprint. Fed the pre-16-September
        baseline, or any earlier candidate, it exits non-zero and writes nothing.
        """
        others = sorted(p for p in FLOW_DIR.glob("*.json")
                        if p.name not in (R3.name, TARGET.name)
                        and "DELTA" not in p.name)
        self.assertGreaterEqual(len(others), 4, others)
        with tempfile.TemporaryDirectory() as tmp:
            for src in others:
                dst = pathlib.Path(tmp) / (src.stem + ".out")
                r = subprocess.run([sys.executable, "-I", str(GENERATOR), str(src), str(dst)],
                                   capture_output=True, text=True)
                self.assertNotEqual(r.returncode, 0, f"{src.name} was accepted as baseline")
                self.assertIn("STOP", r.stderr)
                self.assertFalse(dst.exists(), f"{src.name} produced output anyway")


# ══════════════════════════════════════════════════════════════════════════════
# R2-D — the exact semantic delta
# ══════════════════════════════════════════════════════════════════════════════
class TestExactDelta(unittest.TestCase):

    def setUp(self):
        self.base, self.var = _screens(R3), _screens(TARGET)

    def test_r2_d01_exactly_four_fields_are_added_and_none_removed_or_retyped(self):
        added, removed, retyped = {}, {}, {}
        for sid in set(self.base) | set(self.var):
            b = dict((self.base[sid].get("data") or {}))
            v = dict((self.var[sid].get("data") or {}))
            for k in set(v) - set(b):
                added[f"{sid}.{k}"] = v[k]
            for k in set(b) - set(v):
                removed[f"{sid}.{k}"] = b[k]
            for k in set(b) & set(v):
                if b[k] != v[k]:
                    retyped[f"{sid}.{k}"] = (b[k], v[k])
        self.assertEqual(sorted(added), [
            "APPOINTMENT.error_message", "APPOINTMENT.has_error",
            "APPOINTMENT.has_refusal", "APPOINTMENT.slot_conflict_message"])
        self.assertEqual(removed, {}, f"fields were removed: {sorted(removed)}")
        self.assertEqual(retyped, {}, f"fields changed: {sorted(retyped)}")
        self.assertEqual({v["type"] for k, v in added.items() if "message" in k}, {"string"})
        self.assertEqual({v["type"] for k, v in added.items() if "has_" in k}, {"boolean"})

    def test_r2_d02_exactly_two_textbody_components_are_added(self):
        added, removed = [], []
        for sid in self.base:
            b, v = _components(self.base[sid]["layout"]), _components(self.var[sid]["layout"])
            rest = list(b)
            for c in v:
                if c in rest:
                    rest.remove(c)
                else:
                    added.append((sid, c))
            removed += [(sid, c) for c in rest]
        self.assertEqual(removed, [], f"components were altered or removed: {removed}")
        self.assertEqual(len(added), 2, added)
        for sid, c in added:
            self.assertEqual(sid, "APPOINTMENT")
            body = json.loads(c)
            self.assertEqual(body["type"], "TextBody")
        bodies = {json.loads(c)["text"]: json.loads(c)["visible"] for _, c in added}
        self.assertEqual(bodies, {"${data.slot_conflict_message}": "${data.has_refusal}",
                                  "${data.error_message}": "${data.has_error}"})

    def test_r2_d03_the_two_messages_sit_directly_under_the_heading(self):
        kids = self.var["APPOINTMENT"]["layout"]["children"]
        self.assertEqual(kids[0]["text"], "Reservá tu revisión")
        self.assertEqual(kids[1]["text"], "${data.slot_conflict_message}")
        self.assertEqual(kids[2]["text"], "${data.error_message}")
        self.assertEqual(kids[3]["text"], "${data.vehicle_summary}")
        self.assertEqual(kids[4]["text"], "${data.location_summary}")
        self.assertEqual(kids[5]["type"], "Form")
        self.assertEqual(len(kids), 6)

    def test_r2_d04_exactly_two_routing_edges_are_added(self):
        b = json.load(open(R3))["routing_model"]
        v = json.load(open(TARGET))["routing_model"]
        self.assertEqual(set(b), set(v), "a screen appeared in or left the routing model")
        added = {(k, d) for k in v for d in v[k] if d not in b[k]}
        removed = {(k, d) for k in b for d in b[k] if d not in v[k]}
        self.assertEqual(added, set(FC.REQUIRED_ROUTING_EDGES))
        self.assertEqual(added, {("DETAILS", "APPOINTMENT"), ("SUMMARY", "APPOINTMENT")})
        self.assertEqual(removed, set(), f"edges were removed: {removed}")
        # the forward journey is untouched
        self.assertEqual(v["APPOINTMENT"], ["DETAILS"])
        self.assertIn("SUMMARY", v["DETAILS"])

    def test_r2_d05_no_action_or_payload_anywhere_is_changed(self):
        for sid in self.base:
            self.assertEqual(_actions(self.base[sid]), _actions(self.var[sid]),
                             f"{sid}: an action or its payload changed")

    def test_r2_d06_no_screen_is_added_removed_or_un_terminalled(self):
        self.assertEqual(list(self.base), list(self.var))
        for sid in self.base:
            self.assertEqual(self.base[sid].get("terminal"), self.var[sid].get("terminal"),
                             f"{sid}: terminal changed")
            self.assertEqual(self.base[sid].get("title"), self.var[sid].get("title"))
        self.assertIs(self.var["SUMMARY"].get("terminal"), True,
                      "SUMMARY stopped being terminal — that is a Meta-side question, not "
                      "a local edit")

    def test_r2_d07_details_and_summary_are_structurally_identical(self):
        """R2-D07 — the whole screen, not a chosen subset.

        DETAILS must be identical; SUMMARY must be identical too, since its only change is
        in the top-level `routing_model`, not in the screen object.
        """
        for sid in ("DETAILS", "SUMMARY"):
            self.assertEqual(
                json.dumps(self.base[sid], sort_keys=True, ensure_ascii=False),
                json.dumps(self.var[sid], sort_keys=True, ensure_ascii=False),
                f"{sid} is not structurally identical to the published screen")

    def test_r2_d08_no_customer_phone_input_comes_back(self):
        """R2-D08 — the regression `TARGET_v7.6` would have shipped."""
        for sid, s in self.var.items():
            names = _input_names(s["layout"])
            self.assertNotIn("phone", names, f"{sid} asks the customer for a phone number")
            self.assertNotIn("contact_phone", names)
        self.assertEqual(_input_names(self.var["DETAILS"]["layout"]),
                         _input_names(self.base["DETAILS"]["layout"]))
        self.assertIn("${data.contact_phone_display}",
                      json.dumps(self.var["DETAILS"]["layout"], ensure_ascii=False))

    def test_r2_d09_all_eight_navigate_values_survive(self):
        nav = [json.loads(a) for a in _actions(self.var["APPOINTMENT"])
               if json.loads(a)["name"] == "navigate"]
        self.assertEqual(len(nav), 1)
        payload = nav[0]["payload"]
        self.assertEqual(sorted(payload), sorted(OWNER_EXPORT_NAVIGATE))
        for key in OWNER_EXPORT_NAVIGATE:
            src = "form" if key in ("date", "time") else "data"
            self.assertEqual(payload[key], "${%s.%s}" % (src, key))
            self.assertIn(key, self.var["DETAILS"]["data"],
                          f"DETAILS no longer declares {key}")

    def test_r2_d10_no_init_value_is_introduced(self):
        self.assertEqual(json.dumps(json.load(open(TARGET))).count("init-value"), 0)
        doc = json.load(open(TARGET))
        self.assertEqual(doc["version"], "7.3")
        self.assertEqual(doc["data_api_version"], "3.0")


# ══════════════════════════════════════════════════════════════════════════════
# R2-C — contract equality
# ══════════════════════════════════════════════════════════════════════════════
class TestContractEquality(unittest.TestCase):

    def test_r2_c01_declared_equals_supplied_at_exactly_sixteen(self):
        declared = set(_screens(TARGET)["APPOINTMENT"]["data"])
        supplied = set(FC.appointment_payload())
        self.assertEqual(len(declared), 16, sorted(declared))
        self.assertEqual(len(supplied), 16, sorted(supplied))
        self.assertEqual(declared - supplied, set(),
                         f"declared, never supplied: {sorted(declared - supplied)}")
        self.assertEqual(supplied - declared, set(),
                         f"supplied, never declared: {sorted(supplied - declared)}")
        self.assertEqual(declared, set(FC.APPOINTMENT_KEYS))

    def test_r2_c02_the_sixteen_are_the_published_twelve_plus_the_refusal_four(self):
        published = set(_screens(R3)["APPOINTMENT"]["data"])
        self.assertEqual(len(published), 12)
        self.assertEqual(published | set(FC.REFUSAL_KEYS), set(FC.APPOINTMENT_KEYS))
        self.assertEqual(published & set(FC.REFUSAL_KEYS), set(),
                         "a refusal key was already published — re-derive the delta")
        # DERIVED, not compared. An earlier version asserted
        # `RESTORED_PREFILL_KEYS <= published`, which a mutant satisfied by emptying the
        # constant — the same vacuity that let a WILD1 mutant survive. The restored set IS
        # what the live Flow declares and the pre-16-September baseline does not, so it is
        # computed from the two artifacts and the constant must equal it.
        preserved = set(_screens(ROOT / FC.PRESERVED_BASELINE_PATH)["APPOINTMENT"]["data"])
        derived = published - preserved
        self.assertEqual(set(FC.RESTORED_PREFILL_KEYS), derived,
                         f"RESTORED_PREFILL_KEYS disagrees with the artifacts: {sorted(derived)}")
        self.assertEqual(len(derived), 5, sorted(derived))
        self.assertTrue(derived <= set(FC.APPOINTMENT_KEYS))
        self.assertEqual(preserved, published - derived,
                         "the baselines no longer differ by exactly the prefill set")

    def test_r2_c03_the_payload_is_shaped_the_same_on_every_branch(self):
        """R2-C03 — the whole point of routing every payload through one builder."""
        shapes = {
            "default": FC.appointment_payload(),
            "refusal": FC.appointment_payload(slot_conflict_message="x", has_refusal=True),
            "error": FC.appointment_payload(error_message="x", has_error=True),
            "full": FC.appointment_payload(
                **{k: ("" if isinstance(FC.APPOINTMENT_DEFAULTS[k], str) else
                       FC.APPOINTMENT_DEFAULTS[k]) for k in FC.APPOINTMENT_KEYS}),
        }
        for name, payload in shapes.items():
            self.assertEqual(set(payload), set(FC.APPOINTMENT_KEYS), name)
            FC.validate_screen_payload("APPOINTMENT", payload)

    def test_r2_c04_an_undeclared_key_is_refused_by_the_builder(self):
        with self.assertRaises(ValueError) as cm:
            FC.appointment_payload(contact_phone_full="5491100000001")
        self.assertIn("contact_phone_full", str(cm.exception))
        self.assertIn(FC.TARGET_FLOW_PATH, str(cm.exception))

    def test_r2_c05_details_and_summary_contracts_match_the_target(self):
        declared = {sid: set(s.get("data") or {}) for sid, s in _screens(TARGET).items()}
        self.assertEqual(declared["DETAILS"], set(FC.DETAILS_KEYS))
        self.assertEqual(declared["SUMMARY"], set(FC.SUMMARY_KEYS))
        self.assertEqual(len(FC.DETAILS_KEYS), 8, sorted(FC.DETAILS_KEYS))


# ══════════════════════════════════════════════════════════════════════════════
# the live-ish world, on SQLite, for the behavioural groups
# ══════════════════════════════════════════════════════════════════════════════
_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})


@event.listens_for(_engine, "connect")
def _pragmas(conn, _rec):
    # OFF, as in `test_l4_7w5_gate_a_flow_prefill_contract.py` and for the same reason: the
    # per-test wipe deletes in `sorted_tables` order and two tables are mutually dependent,
    # so a committed booking makes the teardown itself unorderable. Referential integrity is
    # proven against PostgreSQL by the migration and race suites, not here.
    conn.execute("PRAGMA foreign_keys=OFF")


app.models.Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


class _Converged:
    """Smart Booking ON, for the classes that must reach the booking authority.

    Deliberately per-class and not per-module: `TestOffBehaviour` must run with the flag in
    its deployed posture, and a module-wide patch would have made its OFF assertions
    assertions about a patch rather than about the gate. Convergence Phase 3: confirm books
    through `BookingAuthority` only — flag ON, a launch professional, and the authority's
    own availability check routed to `_mock_schedule`'s double.
    """

    @classmethod
    def setUpClass(cls):
        cls._converged = start_converged_booking()

    @classmethod
    def tearDownClass(cls):
        stop_converged_booking(cls._converged)


class _World(unittest.TestCase):
    def setUp(self):
        with _engine.begin() as conn:
            for tbl in reversed(app.models.Base.metadata.sorted_tables):
                conn.execute(tbl.delete())
        self.db = _Session()
        self.db.add(ViaticosZone(zone_group="CABA", zone_detail="Palermo", viaticos=0))
        self.contact = WhatsAppContact(wa_id=WA_ID, display_name="perfil_de_whatsapp")
        self.db.add(self.contact); self.db.flush()
        self.lead = Lead(estado="COTIZACION", flag="ACEPTADO")
        self.db.add(self.lead); self.db.flush()
        self.thread = WhatsAppThread(contact_id=self.contact.id, lead_id=self.lead.id)
        self.db.add(self.thread); self.db.flush()
        self.token = make_booking_token(self.thread.id)
        self.db.add(WhatsAppThreadCandidate(
            thread_id=self.thread.id, marca="Peugeot", modelo="2008", anio=2020,
            tipo_vehiculo="AUTO", zone_group="CABA", zone_detail="Palermo",
            direccion_texto="Av. Santa Fe 1234", status="current_focus"))
        self.db.flush()
        self.state = WhatsAppThreadState(thread_id=self.thread.id,
                                         flow_booking_token=self.token, needs_human=False)
        self.db.add(self.state)
        ensure_launch_professional(self.db)
        self.db.commit()
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
        """An exact slot agreed in conversation, which is what narrows the picker."""
        self.state.preferred_day = (day or FUTURE).isoformat()
        self.state.preferred_time = hhmm
        self.db.commit()

    def payload(self, **over):
        base = {"booking_token": self.token, "date": FUTURE.isoformat(), "time": "11:00",
                "name": "Juan Pérez", "phone": WA_ID, "email": "",
                "inspection_address": "Av. 1", "seller_name": "", "seller_phone": "",
                "listing_url": ""}
        base.update(over)
        return base


# ══════════════════════════════════════════════════════════════════════════════
# R2-S — the restored values are the server's, never the Flow's
# ══════════════════════════════════════════════════════════════════════════════
class TestServerAuthority(_Converged, _World):

    def test_r2_s01_the_phone_comes_from_the_contact_digits_only(self):
        self.contact.wa_id = "+54 9 11 0000-0001"
        self.db.commit()
        data = self.svc.handle_init(self.token)["data"]
        self.assertEqual(data["contact_phone"], "5491100000001")
        self.assertTrue(data["contact_phone"].isdigit())
        self.assertEqual(data["contact_phone_display"], "…0001")

    def test_r2_s02_the_mask_is_derived_from_that_same_value(self):
        data = self.svc.handle_init(self.token)["data"]
        self.assertTrue(data["contact_phone"].endswith(
            data["contact_phone_display"].lstrip("…")))
        self.assertNotIn(data["contact_phone"], data["contact_phone_display"])
        self.assertEqual(len(data["contact_phone_display"].lstrip("…")), 4)

    def test_r2_s03_no_complete_number_is_drawn_on_any_screen(self):
        """R2-S03 — `contact_phone` reaches no visible component, in either artifact."""
        for path in (R3, TARGET):
            for sid, s in _screens(path).items():
                drawn = []

                def walk(n):
                    if isinstance(n, dict):
                        if n.get("type", "").startswith(("Text", "RichText")):
                            drawn.append(json.dumps(n.get("text"), ensure_ascii=False))
                        for k, v in n.items():
                            if k not in ("on-click-action", "on-select-action"):
                                walk(v)
                    elif isinstance(n, list):
                        for v in n:
                            walk(v)
                walk(s.get("layout", {}))
                self.assertNotIn("${data.contact_phone}", " ".join(drawn),
                                 f"{path.name}:{sid} draws the unmasked number")

    def test_r2_s04_a_flow_supplied_phone_never_becomes_booking_authority(self):
        out = self.svc.handle_prepare_summary(self.token,
                                              self.payload(phone="5491199999999"))
        self.assertEqual(out["data"]["phone"], WA_ID)
        self.assertNotIn("99999999", json.dumps(out))
        self.svc.handle_confirm_booking(self.token, self.payload(phone="5491199999999"))
        rev = self.db.query(ThreadRevision).one()
        self.assertEqual(rev.buyer_phone, WA_ID)

    def test_r2_s05_the_name_is_never_invented(self):
        self.assertEqual(self.svc.handle_init(self.token)["data"]["customer_name"], "")
        self.assertNotIn("perfil_de_whatsapp",
                         json.dumps(self.svc.handle_init(self.token)["data"]))
        self.lead.nombre, self.lead.apellido = "Juan", "Pérez"
        self.db.commit()
        self.assertEqual(self.svc.handle_init(self.token)["data"]["customer_name"],
                         "Juan Pérez")

    def test_r2_s06_a_selection_outside_the_offered_lists_is_dropped(self):
        stale = (FUTURE + timedelta(days=400)).isoformat()
        data = self.svc.handle_date_selected(self.token, stale)["data"]
        self.assertEqual(data["selected_date"], "")
        self.assertEqual(data["selected_time"], "")
        good = self.svc.handle_date_selected(self.token, FUTURE.isoformat())["data"]
        self.assertEqual(good["selected_date"], FUTURE.isoformat())
        self.assertIn(good["selected_date"], [i["id"] for i in good["date"]])

    def test_r2_s07_the_identity_helper_reads_only_server_state(self):
        """R2-S07 — by AST, so a future edit cannot quietly take the value from `data`."""
        src = (ROOT / "backend" / "app" / "services" / "booking_flow_service.py").read_text()
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "_identity_fields")
        # the DOCSTRING is prose and names the very things the body must not do, so the
        # scan runs on the statements only. A scan that included it would pass or fail on
        # how the function is described rather than on what it does.
        stmts = [n for n in fn.body
                 if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                         and isinstance(n.value.value, str))]
        self.assertTrue(stmts, "_identity_fields has no body")
        body = "\n".join(ast.unparse(n) for n in stmts)
        self.assertIn("ctx.contact.wa_id", body)
        self.assertIn("ctx.lead", body)
        for forbidden in ("data.get", "data[", "payload", "form."):
            self.assertNotIn(forbidden, body,
                             f"_identity_fields reads {forbidden} — the Flow is not an "
                             f"authority for identity")
        self.assertEqual([a.arg for a in fn.args.args], ["self", "ctx"])


# ══════════════════════════════════════════════════════════════════════════════
# R2-O — Smart Booking OFF creates nothing
# ══════════════════════════════════════════════════════════════════════════════
class TestOffBehaviour(_World):

    def test_r2_o01_dispatch_is_refused_and_nothing_is_created(self):
        class _S:
            smart_booking_ce_enabled = False
        self.assertFalse(booking_dispatch_allowed(_S()))
        self.assertFalse(booking_dispatch_allowed(None))

        from app.services.conversation_engine import ConversationEngine
        minted, sent = [], []

        class _Eng(ConversationEngine):
            def __init__(inner):
                inner.settings = _S()
                inner.db = MagicMock()

            def _decision_log(inner, *a, **kw):
                pass

            def _send_text_to_wa(inner, ctx, text):
                sent.append(text)
                return "wamid.stub"

        before = {
            "revisions": self.db.query(ThreadRevision).count(),
            "messages": self.db.query(WhatsAppMessage).count(),
        }
        ctx = MagicMock()
        ctx.thread.id = self.thread.id
        state = MagicMock()
        with patch("app.services.booking_flow_service.make_booking_token",
                   side_effect=lambda tid: minted.append(tid) or "tok"):
            out = eng_out = _Eng()._send_booking_flow(ctx, state, FC.PUBLISHED_FLOW_ID)
        self.assertIsNotNone(eng_out)
        self.assertEqual(minted, [], "a Flow token was minted with Smart Booking OFF")
        self.assertEqual(len(sent), 1, "the customer must get exactly one reply")
        self.assertTrue(state.needs_human)
        self.db.expire_all()
        self.assertEqual(self.db.query(ThreadRevision).count(), before["revisions"])
        self.assertEqual(self.db.query(WhatsAppMessage).count(), before["messages"])
        self.assertEqual(out.detail, "SMART_BOOKING_DISABLED")

    def test_r2_o02_the_live_posture_is_off(self):
        """R2-O02 — the deployed default, read from the gate rather than from a comment."""
        self.assertFalse(booking_dispatch_allowed(MagicMock()),
                         "a MagicMock reads as ON — the gate must require literal True")


# ══════════════════════════════════════════════════════════════════════════════
# R2-N — ON, in isolation: one offer, one token, one payload
# ══════════════════════════════════════════════════════════════════════════════
class TestOnBehaviourIsolated(_Converged, _World):

    def test_r2_n01_init_returns_exactly_the_sixteen_declared_keys(self):
        out = self.svc.handle_init(self.token)
        self.assertEqual(out["screen"], "APPOINTMENT")
        self.assertEqual(set(out["data"]), set(FC.APPOINTMENT_KEYS))
        self.assertEqual(len(out["data"]), 16)
        FC.validate_screen_payload("APPOINTMENT", out["data"])
        self.assertEqual(set(out["data"]), set(_screens(TARGET)["APPOINTMENT"]["data"]))

    def test_r2_n02_every_appointment_response_is_the_same_sixteen(self):
        responses = [
            self.svc.handle_init(self.token),
            self.svc.handle_date_selected(self.token, FUTURE.isoformat()),
        ]
        self._mock_schedule(valid=False)
        with self.assertRaises(BookingSlotConflictError) as cm:
            self.svc.handle_confirm_booking(self.token, self.payload())
        responses.append(cm.exception.refreshed_data)
        for r in responses:
            self.assertEqual(r["screen"], "APPOINTMENT")
            self.assertEqual(set(r["data"]), set(FC.APPOINTMENT_KEYS))
            FC.validate_screen_payload("APPOINTMENT", r["data"])

    def test_r2_n03_one_offer_one_booking_and_nothing_before_confirmation(self):
        from app.models import WhatsAppBookingOffer
        offers = self.db.query(WhatsAppBookingOffer).count()
        self.svc.handle_init(self.token)
        self.svc.handle_date_selected(self.token, FUTURE.isoformat())
        self.svc.handle_prepare_summary(self.token, self.payload())
        self.assertEqual(self.db.query(ThreadRevision).count(), 0,
                         "a booking was created before confirm_booking")
        self.assertEqual(self.db.query(WhatsAppBookingOffer).count(), offers,
                         "a second offer was issued inside one Flow session")
        self.svc.handle_confirm_booking(self.token, self.payload())
        self.assertEqual(self.db.query(ThreadRevision).count(), 1)

    def test_r2_n04_a_replay_creates_no_second_booking_and_no_second_offer(self):
        from app.models import WhatsAppBookingOffer
        self.svc.handle_confirm_booking(self.token, self.payload())
        offers = self.db.query(WhatsAppBookingOffer).count()
        try:
            self.svc.handle_confirm_booking(self.token, self.payload())
        except Exception:
            pass
        self.assertEqual(self.db.query(ThreadRevision).count(), 1,
                         "a replay produced a second booking")
        self.assertEqual(self.db.query(WhatsAppBookingOffer).count(), offers,
                         "a replay produced a second offer")

    def test_r2_n05_the_summary_screen_carries_the_canonical_phone(self):
        out = self.svc.handle_prepare_summary(self.token, self.payload())
        self.assertEqual(out["screen"], "SUMMARY")
        self.assertEqual(set(out["data"]), set(FC.SUMMARY_KEYS))
        self.assertEqual(out["data"]["phone"], WA_ID)


# ══════════════════════════════════════════════════════════════════════════════
# R2-R — the two refusal paths, each with its own flag
# ══════════════════════════════════════════════════════════════════════════════
class TestRefusalPaths(_Converged, _World):

    def test_r2_r01_a_slot_conflict_sets_has_refusal_only(self):
        self._mock_schedule(valid=False)
        with self.assertRaises(BookingSlotConflictError) as cm:
            self.svc.handle_confirm_booking(self.token, self.payload())
        data = cm.exception.refreshed_data["data"]
        self.assertEqual(cm.exception.refreshed_data["screen"], "APPOINTMENT")
        self.assertIs(data["has_refusal"], True)
        self.assertTrue(data["slot_conflict_message"])
        # the GENUINE slot-conflict message, not the OFF-switch handoff text. Without this
        # the test would pass with Smart Booking off, through a different branch, and would
        # prove nothing about the path a live customer takes.
        from app.services.booking_flow_service import BOOKING_HANDOFF_TEXT
        self.assertNotEqual(data["slot_conflict_message"], BOOKING_HANDOFF_TEXT)
        self.assertIn("horario", data["slot_conflict_message"].lower())
        self.assertIs(data["has_error"], False)
        self.assertEqual(data["error_message"], "")
        self.assertEqual(data["selected_time"], "",
                         "the time that was not booked came back preselected")
        FC.validate_screen_payload("APPOINTMENT", data)

    def test_r2_r01b_a_refusal_with_an_agreed_free_slot_still_preselects_no_time(self):
        """R2-R01b — the one line in `_refusal_screen` that the ordinary conflict never
        reaches, and the mutant that survived without it.

        `_refusal_screen` is called with the date the Flow submitted. Several refusals
        submit an EMPTY date — the OFF-switch branch passes `data.get("date", "")`, and a
        tampered or truncated payload can too. With no date, `_appointment_screen_data`
        takes the agreed-slot narrowing branch; if the agreed slot is still free that branch
        echoes `selected_time`, and the customer would be shown, preselected, the time that
        was just refused. The override is what prevents it, so it is asserted on the path
        where it is the only thing standing in the way.
        """
        self.agree(hhmm="11:00")                 # agreed AND still free in the schedule
        ctx = self.svc.resolve_context(self.token)
        narrowed = self.svc._appointment_screen_data(ctx)
        self.assertEqual(narrowed["selected_time"], "11:00",
                         "the narrowing branch no longer echoes a time — the override "
                         "below is now untested, not unnecessary")
        out = self.svc._refusal_screen(ctx, "", "Ese horario ya fue reservado.")
        self.assertEqual(out["screen"], "APPOINTMENT")
        self.assertEqual(out["data"]["selected_time"], "",
                         "a refusal preselected the time that was not booked")
        self.assertIs(out["data"]["has_refusal"], True)
        self.assertEqual(set(out["data"]), set(FC.APPOINTMENT_KEYS))
        FC.validate_screen_payload("APPOINTMENT", out["data"])

    def test_r2_r02_an_expired_token_sets_has_error_only(self):
        from app.routes import flow_data_exchange as DX
        src = pathlib.Path(DX.__file__).read_text()
        handler = src[src.index("except BookingTokenError"):]
        handler = handler[:handler.index("except HTTPException")]
        self.assertIn("has_error=True", handler)
        self.assertNotIn("has_refusal", handler)
        self.assertIn('screen": "APPOINTMENT"', handler)
        # and the payload that handler builds
        data = FC.appointment_payload(
            error_message="Tu sesión de reserva expiró. Por favor, volvé a iniciar el proceso.",
            has_error=True)
        self.assertIs(data["has_error"], True)
        self.assertTrue(data["error_message"])
        self.assertIs(data["has_refusal"], False)
        self.assertEqual(data["slot_conflict_message"], "")
        self.assertEqual(set(data), set(FC.APPOINTMENT_KEYS))
        FC.validate_screen_payload("APPOINTMENT", data)
        # an expired session has no authoritative identity, so the prefill is empty, not absent
        for key in FC.RESTORED_PREFILL_KEYS:
            self.assertIn(key, data)
            self.assertEqual(data[key], "")

    def test_r2_r03_a_token_error_is_raised_for_an_unknown_token(self):
        with self.assertRaises(BookingTokenError):
            self.svc.handle_prepare_summary("1-0-deadbeefdeadbeef", self.payload())

    def test_r2_r04_both_refusals_are_legal_transitions_on_the_target(self):
        rm = json.load(open(TARGET))["routing_model"]
        FC.validate_transition("SUMMARY", "APPOINTMENT", rm)
        FC.validate_transition("DETAILS", "APPOINTMENT", rm)
        # and illegal on the published Flow, which is why publication is required
        rm_live = json.load(open(R3))["routing_model"]
        for origin in ("SUMMARY", "DETAILS"):
            with self.assertRaises(ValueError):
                FC.validate_transition(origin, "APPOINTMENT", rm_live)

    def test_r2_r05_an_empty_or_unknown_trigger_is_a_422_not_a_500(self):
        from app.routes import flow_data_exchange as DX
        src = pathlib.Path(DX.__file__).read_text()
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.name == "booking_flow_data_exchange")
        body = ast.unparse(fn)
        self.assertIn("status_code=422", body)
        # the HTTPException handler must come BEFORE the generic one, or 422 becomes 500
        handlers = [h for n in ast.walk(fn) if isinstance(n, ast.Try) for h in n.handlers]
        names = [ast.unparse(h.type) if h.type else "bare" for h in handlers]
        self.assertIn("HTTPException", names)
        self.assertLess(names.index("HTTPException"),
                        max(i for i, n in enumerate(names) if n in ("Exception", "bare")),
                        "HTTPException is caught after the generic handler — 422 -> 500")


if __name__ == "__main__":
    unittest.main()
