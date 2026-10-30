"""WILD1-F1/F3/F4/F5 — the booking-path cluster the 2026-10-29 natural Wild exposed.

Four defects, one suite, each with a regression that fails against the parent behaviour:

  F1  `SMART_BOOKING_CE_ENABLED=false` did not stop a booking offer being issued or the
      Booking Flow being dispatched. The flag was tested at the CE *writer* and at the
      Data Exchange *confirm* step; the dispatch sat in front of both. The Wild's
      "okay, podes hoy?" produced offer 3 and Flow message 6248.
  F3a Every refusal and every token error returns `screen: "APPOINTMENT"`, and all of them
      are raised from `_confirm_booking`/`handle_prepare_summary` — reached from SUMMARY and
      DETAILS. Published v7.3's `routing_model` is
      `{"APPOINTMENT": ["DETAILS"], "DETAILS": ["SUMMARY"], "SUMMARY": []}`: neither screen
      may reach APPOINTMENT. The response names a transition the Flow forbids, which is
      what the handset reported as "something went wrong" ten times.
  F3b APPOINTMENT binds no component to a refusal message, so a delivered refusal would
      still have been silent.

      R1 note — the original F3 read "the Flow runtime rejects a payload carrying undeclared
      keys". The Wild refutes that: the deployed backend sent APPOINTMENT five keys v7.3
      declares nowhere and the screen rendered twice (INIT 08:53:31, date_selected
      08:55:53) before the owner reached SUMMARY. Undeclared extras were tolerated; the
      absent routing edge was not.
  F4  `HTTPException(422)` was raised inside the `try` and swallowed by the bare
      `except Exception`, which re-raised 500. Four real handset requests with an empty
      trigger were reported as internal server errors.
  F5  The contract test asserted one direction and subtracted the two keys that break the
      live Flow, while claiming "both directions" in its docstring.

R2 BASELINE CORRECTION — 2026-10-30
    Everything above is unchanged except WHICH artifact "published v7.3" names. R1 read
    `meta/flows/booking/README.md`, which calls `PUBLISHED_28104222025943520_v7.3.json` "a
    byte-exact copy of what is PUBLISHED right now" two lines after recording that r1 was
    "PUBLISHED live on 2026-09-16". The 2026-09-16 Gate B Final Certification settles it:
    it downloaded the live definition from the Flows API, canonicalised it to
    `5c4c68d1…` and proved deep structural equality with `CANDIDATE_v7.4-prefill-r3.json`.
    On 2026-10-30 the owner exported the published JSON for asset 28104222025943520 from
    Meta and confirmed r3's structure field by field.

    The F3a diagnosis survives untouched, because v7.3 and r3 declare the IDENTICAL routing
    model — `{"APPOINTMENT": ["DETAILS"], "DETAILS": ["SUMMARY"], "SUMMARY": []}` — which
    CONTRACT-09b asserts. What changes is the SIZE of the published-to-target delta: four
    keys, not nine. R1 had read five published prefill fields as part of the gap and removed
    them from the payload, withdrawing a shipped, handset-certified feature. CONTRACT-08b
    now asserts their restoration where it asserted their absence, and CONTRACT-08d keeps
    the wrongly-derived `TARGET_v7.6` artifact out of publication without deleting it.

GATE-01..08 · CONTRACT-01..09b · ROUTING-01..06 · HTTP-01..06 · REFUSAL-01..08
"""
from __future__ import annotations

import ast
import hashlib
import json
import pathlib
import re
import sys
import types
import unittest
import unittest.mock
from unittest.mock import patch

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

from app.services import flow_contract as FC
from app.services.booking_dispatch_gate import (
    BookingDispatchDisabled,
    SMART_BOOKING_DISABLED,
    assert_booking_dispatch_allowed,
    booking_dispatch_allowed,
)

FLOW_DIR = ROOT / "meta" / "flows" / "booking"
TARGET = ROOT / FC.TARGET_FLOW_PATH
PUBLISHED = ROOT / FC.PUBLISHED_FLOW_PATH
PUBLISHED_SHA = FC.PUBLISHED_FLOW_SHA256
#: The pre-16-September baseline, still pinned so it cannot drift, and still asserted to be
#: refusal-incapable. It is NOT what is live (see the R2 note in the module docstring).
PRESERVED = ROOT / FC.PRESERVED_BASELINE_PATH
PRESERVED_SHA = FC.PRESERVED_BASELINE_SHA256
SUPERSEDED = ROOT / FC.SUPERSEDED_TARGET_PATH

BFS_PATH = ROOT / "backend" / "app" / "services" / "booking_flow_service.py"
DX_PATH = ROOT / "backend" / "app" / "routes" / "flow_data_exchange.py"
CE_PATH = ROOT / "backend" / "app" / "services" / "conversation_engine.py"
OL_PATH = ROOT / "backend" / "app" / "services" / "offer_lifecycle.py"


def _sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def _screens(path):
    return {s["id"]: s for s in json.load(open(path))["screens"]}


def _declared(path):
    return {sid: set(s.get("data", {})) for sid, s in _screens(path).items()}


def _input_names(layout):
    """Every `TextInput` name in a layout, in document order.

    A module-level reader rather than a closure inside each test: the two baselines are
    distinguished by whether DETAILS asks the customer for a telephone number, and a check
    that important should be one function that several tests call.
    """
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


def _rendered(path):
    out = {}
    for sid, s in _screens(path).items():
        out[sid] = set(re.findall(r"\$\{data\.([A-Za-z0-9_]+)",
                                  json.dumps(s.get("layout", {}))))
    return out


class _Settings:
    def __init__(self, on):
        self.smart_booking_ce_enabled = on


# ══════════════════════════════════════════════════════════════════════════════
# CONTRACT — bidirectional, against the exact target artifact by path AND hash
# ══════════════════════════════════════════════════════════════════════════════
class TestFlowContract(unittest.TestCase):

    def test_contract_01_the_target_artifact_is_pinned_by_path_and_hash(self):
        """CONTRACT-01 — the suite consumes the exact artifact, not whatever is on disk."""
        self.assertTrue(TARGET.exists(), f"missing target Flow: {TARGET}")
        self.assertEqual(_sha(TARGET), FC.TARGET_FLOW_SHA256,
                         "the target Flow changed but flow_contract.TARGET_FLOW_SHA256 did not")

    def test_contract_02_declared_equals_contract_for_every_screen(self):
        """CONTRACT-02 — the module's key sets ARE the target's declarations."""
        declared = _declared(TARGET)
        for sid, keys in FC.SCREEN_KEYS.items():
            self.assertIn(sid, declared, f"target declares no screen {sid}")
            self.assertEqual(
                declared[sid], set(keys),
                f"{sid}: contract {sorted(set(keys))} != declared {sorted(declared[sid])}")

    def test_contract_03_supplied_minus_declared_is_empty(self):
        """CONTRACT-03 — the reverse direction the old test never checked.

        This is F5's exact hole: `supplied - declared`. Protocol keys (`version`,
        `screen`, `data`) are the envelope, not screen data, and are the only exemption —
        named here, provable, and not a list of inconvenient field names.
        """
        declared = _declared(TARGET)
        payload = FC.appointment_payload()
        extra = set(payload) - declared["APPOINTMENT"] - FC.PROTOCOL_KEYS
        self.assertEqual(extra, set(),
                         f"backend supplies keys APPOINTMENT does not declare: {sorted(extra)}")
        # and the same comparison, through the shared validator the runtime uses
        FC.validate_screen_payload("APPOINTMENT", payload)

    def test_contract_04_declared_minus_supplied_is_empty(self):
        """CONTRACT-04 — the forward direction, with no exclusion set."""
        declared = _declared(TARGET)
        missing = declared["APPOINTMENT"] - set(FC.appointment_payload())
        self.assertEqual(missing, set(),
                         f"APPOINTMENT declares keys the backend never supplies: {sorted(missing)}")

    def test_contract_05_rendered_and_carried_partition_the_declarations(self):
        """CONTRACT-05 — conditional/optional keys are modelled, not excluded.

        RENDERED keys are bound with `${data.x}`; CARRIED keys are declared and bound
        nowhere. Both are in the contract. The partition is asserted against the artifact,
        so a key cannot quietly migrate between the two.
        """
        rendered = _rendered(TARGET)
        for sid in FC.SCREEN_KEYS:
            self.assertEqual(
                FC.SCREEN_RENDERED[sid] | FC.SCREEN_CARRIED[sid], set(FC.SCREEN_KEYS[sid]),
                f"{sid}: RENDERED|CARRIED does not cover the declared set")
            self.assertEqual(
                FC.SCREEN_RENDERED[sid] & FC.SCREEN_CARRIED[sid], set(),
                f"{sid}: a key is both RENDERED and CARRIED")
            self.assertEqual(
                FC.SCREEN_RENDERED[sid], rendered[sid] & set(FC.SCREEN_KEYS[sid]),
                f"{sid}: RENDERED does not match what the layout binds")

    def test_contract_06_no_layout_references_an_undeclared_key(self):
        """CONTRACT-06 — the artifact is internally consistent."""
        declared, rendered = _declared(TARGET), _rendered(TARGET)
        for sid in declared:
            undeclared = rendered[sid] - declared[sid]
            self.assertEqual(undeclared, set(),
                             f"{sid} layout binds undeclared {sorted(undeclared)}")

    def test_contract_07_navigate_payloads_only_carry_keys_the_next_screen_declares(self):
        """CONTRACT-07 — the same defect class, one screen further on.

        APPOINTMENT -> DETAILS is client-side navigate, so its payload is DETAILS' input.
        An undeclared key there fails exactly like an undeclared key in a data response.
        """
        declared = _declared(TARGET)
        found = 0

        def walk(node, sid):
            nonlocal found
            if isinstance(node, dict):
                for key in ("on-click-action", "on-select-action"):
                    act = node.get(key)
                    if isinstance(act, dict) and act.get("name") == "navigate":
                        nxt = (act.get("next") or {}).get("name")
                        if nxt in declared:
                            found += 1
                            bad = set(act.get("payload") or {}) - declared[nxt]
                            self.assertEqual(
                                bad, set(),
                                f"navigate {sid}->{nxt} carries undeclared {sorted(bad)}")
                for v in node.values():
                    walk(v, sid)
            elif isinstance(node, list):
                for v in node:
                    walk(v, sid)

        for sid, s in _screens(TARGET).items():
            walk(s.get("layout", {}), sid)
        self.assertGreater(found, 0, "no navigate action found — the check proved nothing")

    def test_contract_08_the_named_keys_are_never_excluded(self):
        """CONTRACT-08 — F5's exclusion set may not come back, in any file.

        The old assertion subtracted `{slot_conflict_message, error_message}` from a set
        difference. This scans the contract suite's own source for that shape so the fix
        cannot be undone by a future edit that merely looks tidy.

        R1: the protected set is the four REFUSAL keys. The five prefill keys are no longer
        "protected" — they are deliberately gone, which CONTRACT-08b asserts separately.
        Keeping them here would have meant the same mistake twice: a name on a list, with
        nobody checking which side of the contract it was supposed to be on.
        """
        protected = set(FC.REFUSAL_KEYS)
        for key in protected:
            self.assertIn(key, set(FC.APPOINTMENT_KEYS),
                          f"{key} is in no screen contract — it has been dropped, not modelled")
        src = pathlib.Path(__file__).read_text()
        for m in re.finditer(r"-\s*\{([^}]*)\}", src):
            names = set(re.findall(r"[\"\']([A-Za-z_][A-Za-z0-9_]*)[\"\']", m.group(1)))
            leaked = names & protected
            self.assertEqual(leaked, set(),
                             f"a set-difference exclusion masks {sorted(leaked)}")

    def test_contract_08b_the_published_prefill_is_supplied_on_every_surface(self):
        """CONTRACT-08b — WILD1-F3-R2. The inverse of what this test asserted yesterday.

        R1 asserted that five keys — `selected_date`, `selected_time`, `customer_name`,
        `contact_phone`, `contact_phone_display` — were ABSENT from the contract, the
        payload builder and the target artifact, on the reading that they belonged to an
        unpublished candidate and the live Flow was `PUBLISHED_…v7.3.json`.

        The owner exported the published definition for asset 28104222025943520 from Meta
        on 2026-10-30. It declares all five on APPOINTMENT, carries three of them in the
        APPOINTMENT -> DETAILS navigate payload, and renders the masked number on DETAILS
        under "Te contactamos a este WhatsApp:" — r3's structure, which the 2026-09-16 Gate
        B Final Certification had already proved live by downloading the definition from the
        Flows API. R1's removal was therefore the withdrawal of a shipped feature.

        So the assertion is reversed, in the same places and in both directions: declared by
        the published Flow, declared by the target, in the contract, and supplied by the
        builder. The reversal is deliberate and recorded, not a weakening — a test that
        asserts absence and a test that asserts presence cannot both be satisfied, and the
        artifact decides which is right.
        """
        self.assertEqual(
            FC.RESTORED_PREFILL_KEYS,
            {"selected_date", "selected_time", "customer_name",
             "contact_phone", "contact_phone_display"})
        self.assertFalse(hasattr(FC, "DEFERRED_PREFILL_KEYS"),
                         "the deferral constant is back — its premise was refuted by the "
                         "owner's 2026-10-30 Meta export")
        payload = FC.appointment_payload()
        declared_live = _declared(PUBLISHED)["APPOINTMENT"]
        declared_target = _declared(TARGET)["APPOINTMENT"]
        for key in FC.RESTORED_PREFILL_KEYS:
            self.assertIn(key, declared_live,
                          f"the published Flow does not declare {key} — re-derive R2")
            self.assertIn(key, declared_target, f"the target dropped {key}")
            self.assertIn(key, FC.APPOINTMENT_KEYS, f"{key} is in no screen contract")
            self.assertIn(key, payload, f"the backend does not supply {key}")
            self.assertIn(key, FC.APPOINTMENT_DEFAULTS, f"{key} has no absent-value")
        # and the service builds them, from the identity helper R1 deleted
        bfs = (ROOT / "backend" / "app" / "services" / "booking_flow_service.py").read_text()
        tree = ast.parse(bfs)
        fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        self.assertIn("_identity_fields", fns,
                      "the identity helper is gone again — DETAILS renders a blank mask")
        identity = ast.unparse(fns["_identity_fields"])
        for key in ("customer_name", "contact_phone", "contact_phone_display"):
            self.assertIn(key, identity, f"_identity_fields no longer supplies {key}")
        # the number is derived from the proven transport identity, never from Flow input
        self.assertIn("ctx.contact.wa_id", identity)
        self.assertIn("_mask_phone", identity)
        self.assertIn("_lead_display_name", identity)
        appt = ast.unparse(fns["_appointment_screen_data"])
        self.assertIn("selected_date", appt)
        self.assertIn("selected_time", appt)
        self.assertIn("self._identity_fields(ctx)", appt)

    def test_contract_08d_the_superseded_target_is_preserved_and_never_published(self):
        """CONTRACT-08d — WILD1-F3-R2.

        `TARGET_v7.6-minimal-refusal.json` was derived from the pre-16-September baseline.
        Publishing it would remove five APPOINTMENT fields and three DETAILS fields,
        re-add a required customer telephone input and revert two action payloads. It is
        kept as audit evidence and must not be the artifact the backend points at.
        """
        self.assertTrue(SUPERSEDED.exists(), "the superseded target was deleted")
        self.assertEqual(_sha(SUPERSEDED), FC.SUPERSEDED_TARGET_SHA256)
        self.assertNotEqual(FC.TARGET_FLOW_PATH, FC.SUPERSEDED_TARGET_PATH)
        self.assertNotEqual(FC.TARGET_FLOW_SHA256, FC.SUPERSEDED_TARGET_SHA256)
        # the concrete regression it would have shipped, measured rather than asserted
        sup, live = _screens(SUPERSEDED), _screens(PUBLISHED)
        for sid in ("APPOINTMENT", "DETAILS"):
            lost = set(live[sid]["data"]) - set(sup[sid]["data"])
            self.assertTrue(lost, f"{sid}: the superseded target lost nothing — re-derive")
        self.assertIn("phone", _input_names(sup["DETAILS"]["layout"]),
                      "the superseded target no longer re-adds the phone input — its "
                      "preserved content changed")
        # and the artifact actually in use adds no input at all
        self.assertNotIn("phone", _input_names(_screens(TARGET)["DETAILS"]["layout"]),
                         "the live target asks the customer for a phone number")
        self.assertEqual(_input_names(_screens(TARGET)["DETAILS"]["layout"]),
                         _input_names(live["DETAILS"]["layout"]),
                         "the target changed which questions DETAILS asks")

    def test_contract_08c_the_published_journey_is_untouched(self):
        """CONTRACT-08c — the target may add; it may not alter or remove.

        The superseded candidate-derived target deleted the `phone` TextInput from DETAILS
        and rewrote two action payloads, which would have changed what live customers were
        asked. The minimal target must leave every published screen, component and payload
        exactly as it is and only add.
        """
        pub, tgt = _screens(PUBLISHED), _screens(TARGET)
        self.assertEqual(set(pub), set(tgt))

        def components(layout):
            """Each component's own properties, without its children — a container's dump
            would otherwise contain everything below it and every insertion would read as a
            rewrite of the whole tree."""
            out = []

            def walk(n):
                if isinstance(n, dict):
                    if n.get("type"):
                        out.append(json.dumps(
                            {k: v for k, v in n.items() if k != "children"},
                            sort_keys=True, ensure_ascii=False))
                    for v in n.values():
                        walk(v)
                elif isinstance(n, list):
                    for v in n:
                        walk(v)
            walk(layout)
            return out

        for sid in pub:
            # every published declaration survives, unchanged in type
            for key, spec in (pub[sid].get("data") or {}).items():
                self.assertIn(key, tgt[sid].get("data") or {},
                              f"{sid}.{key} was removed from the target")
                self.assertEqual(spec.get("type"),
                                 (tgt[sid]["data"])[key].get("type"),
                                 f"{sid}.{key} changed type")
            # every published component survives byte-identically
            missing = [c for c in components(pub[sid]["layout"])
                       if c not in components(tgt[sid]["layout"])]
            self.assertEqual(missing, [],
                             f"{sid}: the target altered or removed published components: "
                             f"{missing[:1]}")
        # the only additions anywhere are the two refusal TextBody components
        added = [c for sid in pub for c in components(tgt[sid]["layout"])
                 if c not in components(pub[sid]["layout"])]
        self.assertEqual(len(added), 2, added)
        for c in added:
            self.assertIn("TextBody", c)
            self.assertTrue("has_refusal" in c or "has_error" in c, c)

    def test_contract_09_the_published_flow_fails_the_target_contract(self):
        """CONTRACT-09 — the live mismatch is preserved as a regression.

        This is the defect, asserted as a fact rather than a memory: the PUBLISHED Flow
        cannot carry a refusal. If someone "fixes" this by pointing the contract at the
        published artifact, this test fails and says why. It is also the proof that
        publication is required.

        WILD1-F3-R2: the gap is now exactly four keys, not nine. R1 measured it against the
        pre-16-September baseline and read five published prefill fields as part of the
        gap; against what is actually live the gap is the refusal contract and nothing
        else — which is the whole claim this correction rests on.
        """
        self.assertEqual(_sha(PUBLISHED), PUBLISHED_SHA, "the published artifact changed")
        pub = _declared(PUBLISHED)["APPOINTMENT"]
        supplied = set(FC.appointment_payload())
        undeclared = supplied - pub
        self.assertIn("slot_conflict_message", undeclared,
                      "the published Flow now declares slot_conflict_message — re-derive")
        self.assertIn("error_message", undeclared)
        self.assertEqual(
            undeclared, set(FC.REFUSAL_KEYS),
            "the published-vs-target gap moved; the publication boundary must be re-stated")
        self.assertEqual(len(undeclared), 4, sorted(undeclared))
        # the published Flow supplies no key the backend withholds: the delta is additive
        self.assertEqual(pub - supplied, set(),
                         f"the backend withholds published keys: {sorted(pub - supplied)}")
        # and it binds no component capable of showing a refusal
        self.assertNotIn("slot_conflict_message", _rendered(PUBLISHED)["APPOINTMENT"])

    def test_contract_09b_the_preserved_baseline_is_pinned_and_is_not_live(self):
        """CONTRACT-09b — WILD1-F3-R2.

        `PUBLISHED_28104222025943520_v7.3.json` must not drift and must not be mistaken for
        the live asset again. The distinguishing facts are structural and are asserted here
        so the next reader does not have to trust a README: v7.3 asks the customer for their
        own telephone number and declares none of the five prefill keys; the live structure
        does the opposite.
        """
        self.assertEqual(_sha(PRESERVED), PRESERVED_SHA, "the preserved baseline drifted")
        self.assertNotEqual(_sha(PRESERVED), PUBLISHED_SHA)
        pre, live = _screens(PRESERVED), _screens(PUBLISHED)
        for key in FC.RESTORED_PREFILL_KEYS:
            self.assertNotIn(key, pre["APPOINTMENT"]["data"],
                             f"v7.3 declares {key} — the baselines are no longer distinct")
            self.assertIn(key, live["APPOINTMENT"]["data"])

        self.assertIn("phone", _input_names(pre["DETAILS"]["layout"]),
                      "v7.3 no longer asks for a phone — re-derive the distinction")
        self.assertNotIn("phone", _input_names(live["DETAILS"]["layout"]),
                         "the live DETAILS screen asks the customer for a phone number")
        self.assertIn("${data.contact_phone_display}",
                      json.dumps(live["DETAILS"]["layout"], ensure_ascii=False))
        # both baselines share the routing model that broke the refusal, so the F3a
        # diagnosis and the deployed backend correction stand whichever one is live
        self.assertEqual(json.load(open(PRESERVED))["routing_model"],
                         json.load(open(PUBLISHED))["routing_model"])


# ══════════════════════════════════════════════════════════════════════════════
# ROUTING — F3a. A screen name in a response is a navigation instruction.
# ══════════════════════════════════════════════════════════════════════════════
class TestRefusalRouting(unittest.TestCase):
    """The half of the contract the field sets cannot express, and the half that failed.

    `TRIGGER_ORIGIN` is not taken on trust: it is re-derived from the artifact by finding
    which screen's layout actually carries each `data_exchange` trigger.
    """

    def _origins_from_artifact(self, path):
        found = {}
        for sid, s in _screens(path).items():
            def walk(n):
                if isinstance(n, dict):
                    for k in ("on-click-action", "on-select-action"):
                        act = n.get(k)
                        if isinstance(act, dict) and act.get("name") == "data_exchange":
                            trig = (act.get("payload") or {}).get("trigger")
                            if trig:
                                found[trig] = sid
                    for v in n.values():
                        walk(v)
                elif isinstance(n, list):
                    for v in n:
                        walk(v)
            walk(s.get("layout", {}))
        return found

    def test_routing_01_trigger_origins_match_the_published_layouts(self):
        """ROUTING-01 — the contract's idea of "which screen is the customer on" is the
        artifact's, measured, not a comment."""
        found = self._origins_from_artifact(PUBLISHED)
        self.assertEqual(found.get("date_selected"), "APPOINTMENT")
        self.assertEqual(found.get("prepare_summary"), "DETAILS")
        self.assertEqual(found.get("confirm_booking"), "SUMMARY")
        for trig, origin in found.items():
            self.assertEqual(FC.TRIGGER_ORIGIN.get(trig), origin,
                             f"TRIGGER_ORIGIN[{trig!r}] disagrees with the artifact")
        # the target must not have moved a trigger to another screen
        self.assertEqual(self._origins_from_artifact(TARGET), found)

    def test_routing_02_the_target_declares_every_edge_the_backend_uses(self):
        """ROUTING-02 — the positive assertion: publishing the target makes the refusals
        legal transitions.

        The required set is DERIVED here, from the triggers that can refuse, and then
        compared with the constant. An earlier version only iterated the constant, so
        emptying it made the whole test vacuous and a mutant survived — the same shape of
        hole as F5. The derivation is what closes it: the set cannot be shrunk without
        disagreeing with `TRIGGER_ORIGIN`.
        """
        refusing = ("prepare_summary", "confirm_booking")
        derived = {(FC.TRIGGER_ORIGIN[t], FC.REFUSAL_RETURN_SCREEN) for t in refusing}
        derived = {e for e in derived if e[0] != e[1]}     # a self-refresh needs no edge
        self.assertEqual(set(FC.REQUIRED_ROUTING_EDGES), derived,
                         "REQUIRED_ROUTING_EDGES disagrees with the refusing triggers")
        self.assertEqual(len(derived), 2, derived)

        rm = json.load(open(TARGET))["routing_model"]
        for origin, dest in derived:
            self.assertIn(dest, rm.get(origin, []),
                          f"the target does not allow {origin} -> {dest}")
            FC.validate_transition(origin, dest, rm)

    def test_routing_03_published_v73_forbids_them_all(self):
        """ROUTING-03 — the live defect, preserved. This is the Wild's root cause asserted
        as a fact: ten handset attempts, every one of them this transition."""
        rm = json.load(open(PUBLISHED))["routing_model"]
        self.assertEqual(rm.get("SUMMARY"), [],
                         "v7.3 now routes out of SUMMARY — re-derive the publication boundary")
        for origin, dest in FC.REQUIRED_ROUTING_EDGES:
            with self.assertRaises(ValueError) as cm:
                FC.validate_transition(origin, dest, rm)
            self.assertIn("not a declared transition", str(cm.exception))

    def test_routing_04_a_self_refresh_needs_no_edge(self):
        """ROUTING-04 — why `date_selected` worked in the Wild while every refusal failed.

        APPOINTMENT -> APPOINTMENT is not a navigation. The published routing model has no
        self-edge anywhere and that path is the one the owner exercised successfully.
        """
        rm = json.load(open(PUBLISHED))["routing_model"]
        self.assertNotIn("APPOINTMENT", rm["APPOINTMENT"])
        FC.validate_transition("APPOINTMENT", "APPOINTMENT", rm)      # must not raise
        FC.validate_transition(None, "APPOINTMENT", rm)               # INIT has no origin

    def test_routing_05_every_backend_refusal_response_is_checked(self):
        """ROUTING-05 — the refusal screens the service actually returns, enumerated from
        the source, each validated against the target's routing model.

        An AST walk rather than a string search: the screen name is a literal in a dict
        the function returns, and a test that only grepped for "APPOINTMENT" would pass on
        a comment.
        """
        tree = ast.parse(BFS_PATH.read_text(encoding="utf-8-sig"))
        screens = set()
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                   and n.name == "_refusal_screen"):
            for node in ast.walk(fn):
                if isinstance(node, ast.Dict):
                    for k, v in zip(node.keys, node.values):
                        if (isinstance(k, ast.Constant) and k.value == "screen"
                                and isinstance(v, ast.Constant)):
                            screens.add(v.value)
        self.assertEqual(screens, {FC.REFUSAL_RETURN_SCREEN},
                         f"_refusal_screen returns {sorted(screens)}")
        # the DX route's token-error screen, same check
        dx = ast.parse(DX_PATH.read_text(encoding="utf-8-sig"))
        dx_screens = set()
        for node in ast.walk(dx):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if (isinstance(k, ast.Constant) and k.value == "screen"
                            and isinstance(v, ast.Constant)):
                        dx_screens.add(v.value)
        self.assertIn(FC.REFUSAL_RETURN_SCREEN, dx_screens)

        rm = json.load(open(TARGET))["routing_model"]
        for trig in ("prepare_summary", "confirm_booking"):
            FC.validate_transition(FC.TRIGGER_ORIGIN[trig],
                                   FC.REFUSAL_RETURN_SCREEN, rm)

    def test_routing_06_the_validator_refuses_an_unknown_origin(self):
        """ROUTING-06 — a routing model that does not mention the origin is not a pass."""
        with self.assertRaises(ValueError):
            FC.validate_transition("NOWHERE", "APPOINTMENT", {"SUMMARY": ["APPOINTMENT"]})


# ══════════════════════════════════════════════════════════════════════════════
# REFUSAL MATRIX — every refusal payload must validate against the target
# ══════════════════════════════════════════════════════════════════════════════
class TestRefusalPayloads(unittest.TestCase):

    def _validate(self, data, screen="APPOINTMENT"):
        declared = _declared(TARGET)[screen]
        self.assertEqual(set(data) - declared, set(),
                         f"undeclared keys: {sorted(set(data) - declared)}")
        self.assertEqual(declared - set(data), set(),
                         f"missing keys: {sorted(declared - set(data))}")

    def test_refusal_01_smart_booking_disabled_payload_validates(self):
        """REFUSAL-01 — the OFF switch's screen renders."""
        d = FC.appointment_payload(slot_conflict_message="handoff", has_refusal=True)
        self._validate(d)
        self.assertTrue(d["has_refusal"])
        self.assertEqual(d["slot_conflict_message"], "handoff")

    def test_refusal_02_slot_conflict_payload_validates(self):
        """REFUSAL-02 — a genuine slot conflict, the production case."""
        d = FC.appointment_payload(slot_conflict_message="Justo nos tomaron ese horario.",
                                   has_refusal=True, date=[{"id": "2026-11-03", "title": "x"}],
                                   is_date_enabled=True)
        self._validate(d)

    def test_refusal_03_expired_session_error_payload_validates(self):
        """REFUSAL-03 — the BookingTokenError screen."""
        d = FC.appointment_payload(error_message="expiró", has_error=True)
        self._validate(d)
        self.assertTrue(d["has_error"])

    def test_refusal_04_a_refusal_without_its_flag_is_invisible(self):
        """REFUSAL-04 — the message alone is not enough.

        The target renders `slot_conflict_message` only when `has_refusal` is true. A
        payload carrying the text with the flag false validates structurally and still
        says nothing to the customer, so the pairing is asserted as a contract rule.
        """
        d = FC.appointment_payload(slot_conflict_message="something went wrong")
        self._validate(d)
        self.assertFalse(d["has_refusal"])
        self.assertTrue(d["slot_conflict_message"])
        # the rule the backend must honour
        self.assertTrue(
            (not d["slot_conflict_message"]) or d["has_refusal"] is False,
            "placeholder — the real pairing is enforced in test_refusal_05")

    def test_refusal_05_every_backend_refusal_sets_both(self):
        """REFUSAL-05 — the backend never ships a message without its flag.

        AST, per function. The first version of this test used a regex requiring `:` or
        `=` straight after the quoted key, which never matched the real
        `data["slot_conflict_message"] = error_message` at all — a test that read nothing
        and passed. Mutation M6/M12 caught it.
        """
        pairs = (("slot_conflict_message", "has_refusal"), ("error_message", "has_error"))
        for path in (BFS_PATH, DX_PATH):
            tree = ast.parse(path.read_text())
            for fn in ast.walk(tree):
                if not isinstance(fn, ast.FunctionDef):
                    continue
                # Only places the KEY is written: a dict literal key, a subscript
                # slice, or a keyword argument to the contract builder. A parameter
                # merely NAMED after the key is not a write — `_appointment_screen_data`
                # used to take an `error_message` argument and write
                # `slot_conflict_message`, and counting that as a write made this test
                # fail for the wrong reason.
                written = set()
                for n in ast.walk(fn):
                    if isinstance(n, ast.Dict):
                        written |= {k.value for k in n.keys
                                    if isinstance(k, ast.Constant)
                                    and isinstance(k.value, str)}
                    elif isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store) \
                            and isinstance(n.slice, ast.Constant) \
                            and isinstance(n.slice.value, str):
                        written.add(n.slice.value)
                    elif isinstance(n, ast.Call):
                        written |= {kw.arg for kw in n.keywords if kw.arg}
                for msg_key, flag in pairs:
                    if msg_key not in written:
                        continue
                    self.assertIn(
                        flag, written,
                        f"{path.name}:{fn.name} writes {msg_key} but never {flag}")

    def test_refusal_07_the_real_refusal_screen_validates_on_both_branches(self):
        """REFUSAL-07 — behavioural, through the real `_refusal_screen`.

        Mutation M7 reverted the `ctx is None` branch to the three-key dict it used to
        return and every structural test still passed, because none of them called the
        function. This one does, for both branches.
        """
        from app.services.booking_flow_service import BookingFlowService

        class _Stub(BookingFlowService):
            def __init__(self):
                pass

            def _appointment_screen_data(self, ctx, selected_date_str=None,
                                         refusal_message=None):
                return FC.appointment_payload(booking_token="tok",
                                              vehicle_summary="v",
                                              location_summary="l")

        stub = _Stub()
        for ctx in (None, object()):
            out = stub._refusal_screen(ctx, "2026-11-03", "no pudimos confirmar")
            self.assertEqual(out["screen"], "APPOINTMENT")
            FC.validate_screen_payload("APPOINTMENT", out["data"])
            self.assertTrue(out["data"]["has_refusal"],
                            f"ctx={ctx!r}: the refusal is transmitted but not visible")
            self.assertEqual(out["data"]["slot_conflict_message"], "no pudimos confirmar")

    def test_refusal_08_the_shared_validator_catches_both_directions(self):
        """REFUSAL-08 — the comparison lives in the contract module, not only in a test.

        Mutation M4 deleted the reverse comparison from this file. The validator is why
        that is no longer sufficient to hide an undeclared key.
        """
        good = FC.appointment_payload()
        FC.validate_screen_payload("APPOINTMENT", good)
        with self.assertRaises(ValueError) as e:
            FC.validate_screen_payload("APPOINTMENT", {**good, "undeclared_key": 1})
        self.assertIn("supplied but not declared", str(e.exception))
        with self.assertRaises(ValueError) as e:
            FC.validate_screen_payload(
                "APPOINTMENT", {k: v for k, v in good.items() if k != "booking_token"})
        self.assertIn("declared but not supplied", str(e.exception))
        with self.assertRaises(ValueError) as e:
            FC.validate_screen_payload("APPOINTMENT",
                                       {**good, "slot_conflict_message": "x"})
        self.assertIn("without has_refusal", str(e.exception))

    def test_refusal_06_unknown_key_is_refused_not_sent(self):
        """REFUSAL-06 — the builder fails loudly rather than shipping an undeclared key."""
        with self.assertRaises(ValueError) as e:
            FC.appointment_payload(slot_conflict_msg="typo")
        self.assertIn("slot_conflict_msg", str(e.exception))


# ══════════════════════════════════════════════════════════════════════════════
# GATE — the Smart Booking OFF invariant
# ══════════════════════════════════════════════════════════════════════════════
class TestDispatchGate(unittest.TestCase):

    def test_gate_01_allowed_only_when_exactly_true(self):
        """GATE-01 — the `is True` test, same as the writer and the confirm step."""
        self.assertTrue(booking_dispatch_allowed(_Settings(True)))
        for v in (False, None, 0, 1, "true", "True", "", [], object()):
            self.assertFalse(booking_dispatch_allowed(_Settings(v)),
                             f"{v!r} must read as OFF")

    def test_gate_02_missing_attribute_reads_off(self):
        """GATE-02 — a settings object without the flag is OFF, never ON."""
        self.assertFalse(booking_dispatch_allowed(object()))

    def test_gate_03_assert_raises_with_the_typed_reason(self):
        """GATE-03 — the backstop's refusal is typed and observable."""
        with self.assertRaises(BookingDispatchDisabled) as e:
            assert_booking_dispatch_allowed(source="t", thread_id=2074,
                                            settings=_Settings(False))
        self.assertEqual(e.exception.reason, SMART_BOOKING_DISABLED)
        self.assertEqual(e.exception.thread_id, 2074)
        self.assertIn(SMART_BOOKING_DISABLED, str(e.exception))

    def test_gate_04_assert_is_silent_when_on(self):
        """GATE-04 — ON must not be disturbed."""
        assert_booking_dispatch_allowed(source="t", settings=_Settings(True))

    def test_gate_05_offer_lifecycle_issue_detects_a_bypass(self):
        """GATE-05 — a direct/internal caller is DETECTED, before any row is written.

        This is the honest scope of the current boundary. The two product callers refuse
        above `issue()`, so no customer path reaches it when dispatch is off; a caller
        outside them is recorded loudly instead of refused. The blocking variant is an
        owner decision — it makes issuing an offer a Smart-Booking-gated action and fails
        114 existing tests across five suites, the offer authority's own among them.
        """
        src = OL_PATH.read_text()
        i_def = src.index("def issue(self, state, *, thread_id")
        body = src[i_def: src.index("\n    def ", i_def + 10)]
        self.assertIn("note_offer_issued_while_dispatch_off", body,
                      "OfferLifecycle.issue has no bypass detector")
        self.assertLess(body.index("note_offer_issued_while_dispatch_off"),
                        body.index("token_fingerprint(token)"),
                        "the detector must run before the row is built")

    def test_gate_05b_the_detector_fires_only_when_dispatch_is_off(self):
        """GATE-05b — behavioural: silent when ON, loud when OFF."""
        from app.services.booking_dispatch_gate import (
            note_offer_issued_while_dispatch_off as note)
        self.assertFalse(note(source="t", thread_id=1, settings=_Settings(True)))
        self.assertTrue(note(source="t", thread_id=1, settings=_Settings(False)))

    @staticmethod
    def _guarded_mints(fn):
        """Every `make_booking_token`/`_mk_token` call in `fn`, and whether a REAL gate
        guard dominates it.

        A substring search is not enough and that is the whole lesson of F5: the import
        `from .booking_dispatch_gate import booking_dispatch_allowed` sits inside the same
        function body, so `"booking_dispatch_allowed" in source` stays true even after the
        guard itself is deleted. This walks the statement list and only counts an `if`
        whose TEST actually calls the gate and whose body returns.
        """
        gates = {"booking_dispatch_allowed", "_dispatch_ok"}

        def calls_gate(node):
            return any(isinstance(n, ast.Call) and
                       ((isinstance(n.func, ast.Name) and n.func.id in gates) or
                        (isinstance(n.func, ast.Attribute) and n.func.attr in gates))
                       for n in ast.walk(node))

        def mints(node):
            out = []
            for n in ast.walk(node):
                if isinstance(n, ast.Call):
                    f = n.func
                    name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
                    if name in ("make_booking_token", "_mk_token"):
                        out.append(n)
            return out

        found = []

        def scan(stmts, guarded):
            for st in stmts:
                if isinstance(st, ast.If):
                    g = guarded or (calls_gate(st.test)
                                    and any(isinstance(x, ast.Return)
                                            for x in ast.walk(st)))
                    # a guard that returns protects everything AFTER it in this block
                    if (calls_gate(st.test)
                            and any(isinstance(x, ast.Return) for x in ast.walk(st))):
                        guarded = True
                    scan(st.body, g)
                    scan(st.orelse, g)
                    continue
                for m in mints(st):
                    found.append(guarded)
                for sub in ("body", "orelse", "finalbody"):
                    inner = getattr(st, sub, None)
                    if isinstance(inner, list):
                        scan(inner, guarded)
        scan(fn.body, False)
        return found

    def _ce_fn(self, name):
        tree = ast.parse(CE_PATH.read_text())
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and n.name == name:
                return n
        raise AssertionError(f"{name} not found")

    def test_gate_06_send_booking_flow_gates_before_minting_a_token(self):
        """GATE-06 — no booking token or Flow context exists when OFF."""
        guarded = self._guarded_mints(self._ce_fn("_send_booking_flow"))
        self.assertTrue(guarded, "no token mint found — the test proved nothing")
        self.assertTrue(all(guarded),
                        "a booking token is minted without a dispatch guard dominating it")

    def test_gate_07_the_legacy_data_collection_flow_is_gated_too(self):
        """GATE-07 — not only the `flow_first_day_request` route.

        The legacy Flow's token is accepted by `_process_flow_response`, so it is a booking
        offer. The invariant must hold for every route that can mint one, or it is
        route-specific — which is precisely how the Wild escaped.
        """
        tree = ast.parse(CE_PATH.read_text())
        total = 0
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            guarded = self._guarded_mints(fn)
            if guarded:
                total += len(guarded)
                self.assertTrue(
                    all(guarded),
                    f"{fn.name} mints a booking token with no dispatch guard above it")
        self.assertGreaterEqual(total, 2,
                                "expected at least the published and the legacy Flow mints")

    def test_gate_08_no_email_is_attempted_by_the_dispatch_refusal(self):
        """GATE-08 — requirement A.8, asserted on source.

        `_sb_handoff` fires `_send_fallback_human_review_notification`, a Resend e-mail.
        The dispatch refusal must not route through it.
        """
        # Read EXECUTABLE code only. The docstring of the function under test names these
        # helpers in order to explain why it avoids them, and a substring scan over the raw
        # source would read that explanation as the defect. Comments are excluded for the
        # same reason: a promise in prose is not the thing being asserted.
        fn = self._refusal_fn()
        body = [n for n in fn.body if not (isinstance(n, ast.Expr)
                                           and isinstance(n.value, ast.Constant)
                                           and isinstance(n.value.value, str))]
        code = ast.dump(ast.Module(body=body, type_ignores=[]))
        for forbidden in ("_send_fallback_human_review_notification",
                          "send_human_review_notification", "resend", "_sb_handoff"):
            self.assertNotIn(forbidden, code,
                             f"the dispatch refusal must not reach {forbidden}")
        self.assertIn("needs_human", code, "the refusal must hand off to a person")
        self.assertIn("BOOKING_HANDOFF_TEXT", code,
                      "the customer must get the approved non-Flow text")

    @staticmethod
    def _refusal_fn():
        tree = ast.parse(CE_PATH.read_text())
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and n.name == "_booking_dispatch_refusal":
                return n
        raise AssertionError("_booking_dispatch_refusal not found")


# ══════════════════════════════════════════════════════════════════════════════
# HTTP — intended statuses survive
# ══════════════════════════════════════════════════════════════════════════════
class TestHttpStatusPreservation(unittest.TestCase):

    def _handlers(self):
        """The ordered except-clause types of the data-exchange handler."""
        tree = ast.parse(DX_PATH.read_text())
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                  and n.name == "booking_flow_data_exchange")
        # the big try is the one with the most handlers
        tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
        big = max(tries, key=lambda t: len(t.handlers))
        out = []
        for h in big.handlers:
            if h.type is None:
                out.append("BARE")
            elif isinstance(h.type, ast.Name):
                out.append(h.type.id)
            elif isinstance(h.type, ast.Attribute):
                out.append(h.type.attr)
            else:
                out.append(ast.dump(h.type))
        return out

    def test_http_01_httpexception_is_caught_before_the_generic_handler(self):
        """HTTP-01 — F4's exact fix, asserted structurally."""
        hs = self._handlers()
        self.assertIn("HTTPException", hs, "no HTTPException handler at all")
        self.assertIn("Exception", hs, "no generic handler — the test is reading the wrong try")
        self.assertLess(hs.index("HTTPException"), hs.index("Exception"),
                        f"HTTPException must precede Exception; order was {hs}")

    def test_http_02_the_httpexception_handler_only_re_raises(self):
        """HTTP-02 — it must not convert, log-as-internal, or swallow."""
        tree = ast.parse(DX_PATH.read_text())
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                  and n.name == "booking_flow_data_exchange")
        big = max((n for n in ast.walk(fn) if isinstance(n, ast.Try)),
                  key=lambda t: len(t.handlers))
        h = next(x for x in big.handlers
                 if isinstance(x.type, ast.Name) and x.type.id == "HTTPException")
        stmts = [s for s in h.body if not isinstance(s, ast.Pass)]
        self.assertEqual(len(stmts), 1, "the handler should do exactly one thing")
        self.assertIsInstance(stmts[0], ast.Raise)
        self.assertIsNone(stmts[0].exc, "it must be a bare `raise`, preserving the status")

    def test_http_03_slot_conflict_still_returns_a_rendered_response(self):
        """HTTP-03 — the refusal path must stay a Flow response, not an HTTP error."""
        hs = self._handlers()
        self.assertIn("BookingSlotConflictError", hs)
        self.assertLess(hs.index("BookingSlotConflictError"), hs.index("Exception"))
        tree = ast.parse(DX_PATH.read_text())
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                  and n.name == "booking_flow_data_exchange")
        big = max((n for n in ast.walk(fn) if isinstance(n, ast.Try)),
                  key=lambda t: len(t.handlers))
        h = next(x for x in big.handlers
                 if isinstance(x.type, ast.Name)
                 and x.type.id == "BookingSlotConflictError")
        body = ast.dump(ast.Module(body=h.body, type_ignores=[]))
        self.assertIn("_encrypt_and_return", body,
                      "a slot conflict must return an encrypted Flow response")
        self.assertNotIn("HTTPException", body)

    def test_http_04_unknown_trigger_raises_422_not_500(self):
        """HTTP-04 — the empty trigger the real handset sent, four times."""
        src = DX_PATH.read_text()
        m = re.search(r"Unknown trigger", src)
        self.assertIsNotNone(m)
        window = src[max(0, m.start() - 300): m.start() + 120]
        self.assertIn("status_code=422", window)
        self.assertNotIn("status_code=500", window)

    def test_http_05_unknown_action_raises_422_not_500(self):
        """HTTP-05 — same for the action dispatch."""
        src = DX_PATH.read_text()
        m = re.search(r"Unknown action", src)
        self.assertIsNotNone(m)
        window = src[max(0, m.start() - 300): m.start() + 120]
        self.assertIn("status_code=422", window)
        self.assertNotIn("status_code=500", window)

    def test_http_06_validation_refusals_log_distinctly_from_internal_failures(self):
        """HTTP-06 — a client fault must not be recorded as a server fault."""
        src = DX_PATH.read_text()
        self.assertIn("BOOKING_FLOW_VALIDATION_REFUSED", src)
        for m in re.finditer(r"BOOKING_FLOW_VALIDATION_REFUSED", src):
            window = src[m.start(): m.start() + 260]
            self.assertNotIn("BOOKING_FLOW_INTERNAL_ERROR", window)
        # and the payload itself is never logged beside the refusal
        for m in re.finditer(r"BOOKING_FLOW_VALIDATION_REFUSED", src):
            window = src[m.start(): m.start() + 260]
            self.assertNotIn("payload", window)
            self.assertNotIn("encrypted_flow_data", window)


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ══════════════════════════════════════════════════════════════════════════════
# THE WILD PROGRESSION — the exact sequence, asserted end to end
# ══════════════════════════════════════════════════════════════════════════════
class TestWildProgression(unittest.TestCase):
    """The 2026-10-29 conversation, to the turn that broke the boundary.

    The owner's own words, which this milestone may quote because they are now a
    regression fixture and not a script for anyone to reproduce:

        "okay, podes hoy?"

    With Smart Booking OFF that turn must produce zero offers and zero Flow messages.
    """

    def test_wild_01_the_dispatch_refusal_is_reachable_and_returns_a_reply(self):
        """WILD-01 — `_send_booking_flow` refuses and answers, with no token minted."""
        from app.services.conversation_engine import ConversationEngine

        minted = []
        sent = []

        class _Eng(ConversationEngine):
            def __init__(self):
                self.settings = _Settings(False)
                self.db = unittest.mock.MagicMock()

            def _decision_log(self, *a, **kw):
                pass

            def _send_text_to_wa(self, ctx, text):
                sent.append(text)
                return "wamid.stub"

        eng = _Eng()
        ctx = unittest.mock.MagicMock()
        ctx.thread.id = 2074
        ctx.lead = unittest.mock.MagicMock()
        state = unittest.mock.MagicMock()

        with unittest.mock.patch(
                "app.services.booking_flow_service.make_booking_token",
                side_effect=lambda tid: minted.append(tid) or "tok"):
            out = eng._send_booking_flow(ctx, state, "28104222025943520")

        self.assertIsNotNone(out, "the refusal must be a turn outcome, not None")
        self.assertEqual(minted, [], "a booking token was minted while dispatch is OFF")
        self.assertEqual(len(sent), 1, "the customer must receive exactly one reply")
        self.assertTrue(state.needs_human, "the thread must go to a person")
        self.assertEqual(out.detail, SMART_BOOKING_DISABLED)

    def test_wild_02_with_dispatch_on_the_refusal_does_not_fire(self):
        """WILD-02 — ON is undisturbed: the gate returns nothing and the body proceeds."""
        from app.services.conversation_engine import ConversationEngine
        import unittest.mock

        class _Eng(ConversationEngine):
            def __init__(self):
                self.settings = _Settings(True)
                self.db = unittest.mock.MagicMock()

            def _booking_dispatch_refusal(self, *a, **kw):
                raise AssertionError("the refusal fired while dispatch is ON")

            def _get_active_inspection_location(self, ctx, state):
                return (None, None)          # stop early, after the gate

        eng = _Eng()
        ctx = unittest.mock.MagicMock()
        ctx.thread.id = 2074
        ctx.candidates = []
        out = eng._send_booking_flow(ctx, unittest.mock.MagicMock(), "28104222025943520")
        self.assertIsNone(out, "no zone + no candidate must still fall through as before")
