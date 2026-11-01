"""WILD1-R3 — Meta's forward-only routing graph, and the server recovery return beside it.

WHAT THIS MODULE IS FOR
    On 2026-10-30, owner-present, Meta's own validator rejected the artifact this project
    had certified locally four times. Two errors, verbatim:

        "Backward route [DETAILS->APPOINTMENT] corresponding to forward route
         [APPOINTMENT->DETAILS] is not allowed in the routing model. Only forward routes
         can be specified."
        "No entry screen found in the routing model. Expected a screen with no inbound
         edges as the entry screen."

    The local validators passed that artifact without comment. They were a deny-list of
    the three errors Meta gave on 2026-09-16 plus one render rule, and routing was not
    among them. Meta was authoritative and Meta found this; nothing local did. The first
    class below is that gap closed — the two findings as assertions that run every time.

    The deeper error was conceptual, and it is the one worth not repeating. The old
    contract treated `routing_model` as an exhaustive list of every screen a Data API
    *response* may name. From that it followed that a refusal returning APPOINTMENT needed
    `DETAILS -> APPOINTMENT` and `SUMMARY -> APPOINTMENT` declared. There is no artifact in
    which those edges exist, so the conclusion was not merely unproven, it was impossible.

    Two concepts, kept apart from here on:

      A  FORWARD ROUTES    Meta's `routing_model`: a forward-only DAG, one entry screen.
                           Ordinary navigation. Exactly what Meta validated.
      B  RECOVERY RETURNS  A server-selected return to APPOINTMENT carried by a recognised
                           typed refusal. Not an edge, not declared to Meta, not reachable
                           by navigation, and not requestable by a client.

WHAT THIS MODULE DOES NOT CLAIM
    That the refusal renders on a handset. The 2026-10-29 Wild returned
    `SUMMARY -> APPOINTMENT` ten times and the handset showed Meta's generic error every
    time. The old explanation for that — a missing edge — is now impossible, so F3a is
    REOPENED, not closed. What remains is either that the runtime refuses a backward
    response outright, or that something else broke those ten responses. Only a handset
    separates them. Every assertion here is about the contract this backend keeps; none is
    about what Meta's runtime does with it.

R3-M  meta        Meta's two real findings, as local gates
R3-F  forward     the published graph: forward-only, one entry, acyclic
R3-N  navigation  ordinary forward progression allowed, arbitrary backward refused
R3-R  recovery    the narrow allowlist, and everything it refuses
R3-C  client      a payload cannot choose the screen, by inspection and by behaviour
R3-P  producers   the inventory this allowlist was derived from, re-derived by AST
R3-W  writes      no refusal writes a booking; replay and idempotency intact
R3-7  mf07        the expired-token path, fail-closed and separately classified
R3-X  mutants     nine mutations, each of which must fail at least one test above
"""
from __future__ import annotations

import ast
import copy
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

# The fixture world is reused rather than rebuilt: a second copy of a 60-line booking
# fixture is a second thing to drift.
from test_wild1_r3_aligned_flow_contract import (  # noqa: E402
    FUTURE, TARGET, R3, _Converged, _World)

from app.services import flow_contract as FC  # noqa: E402
from app.services.booking_flow_service import (  # noqa: E402
    BookingSlotConflictError, BookingTokenError)

DX_PATH = ROOT / "backend" / "app" / "routes" / "flow_data_exchange.py"
BFS_PATH = ROOT / "backend" / "app" / "services" / "booking_flow_service.py"

PUBLISHED_GRAPH = {"APPOINTMENT": ["DETAILS"], "DETAILS": ["SUMMARY"], "SUMMARY": []}


def _graph(path):
    return json.load(open(path, encoding="utf-8"))["routing_model"]


# ══════════════════════════════════════════════════════════════════════════════
# R3-M — Meta's two real findings, as local gates
# ══════════════════════════════════════════════════════════════════════════════
class TestMetaFindings(unittest.TestCase):
    """The regression. Had this existed on 2026-10-29, Meta would not have been the first
    thing to catch the defect."""

    def test_r3_m01_a_backward_edge_is_refused_with_metas_own_words(self):
        bad = copy.deepcopy(PUBLISHED_GRAPH)
        bad["DETAILS"] = ["SUMMARY", "APPOINTMENT"]
        with self.assertRaises(ValueError) as cm:
            FC.assert_forward_only(bad)
        msg = str(cm.exception)
        self.assertIn("Backward route [DETAILS->APPOINTMENT]", msg)
        self.assertIn("Only forward routes can be specified", msg)

    def test_r3_m02_the_summary_backward_edge_is_refused_too(self):
        """`SUMMARY -> APPOINTMENT` on its own is NOT a mirrored backward edge: the forward
        route out of APPOINTMENT is to DETAILS, not to SUMMARY. It is a three-screen cycle,
        and what catches it is the entry-screen rule — every screen gains an inbound edge,
        so none qualifies as the entry. That is Meta's second error, and it is why the
        second error exists: the first rule alone would let this through.
        """
        bad = copy.deepcopy(PUBLISHED_GRAPH)
        bad["SUMMARY"] = ["APPOINTMENT"]
        inbound = {s: 0 for s in bad}
        for origin, targets in bad.items():
            for t in targets:
                inbound[t] += 1
        self.assertEqual([s for s in bad if inbound[s] == 0], [],
                         "this mutant must leave no entry screen")
        with self.assertRaises(ValueError) as cm:
            FC.assert_forward_only(bad)
        self.assertIn("No entry screen found", str(cm.exception))

    def test_r3_m03_the_rejected_artifact_shape_loses_its_entry_screen(self):
        """Meta's second error. Both backward edges at once give every screen an inbound
        edge, so no screen qualifies as the entry."""
        bad = copy.deepcopy(PUBLISHED_GRAPH)
        bad["DETAILS"] = ["SUMMARY", "APPOINTMENT"]
        bad["SUMMARY"] = ["APPOINTMENT"]
        inbound = {s: 0 for s in bad}
        for origin, targets in bad.items():
            for t in targets:
                inbound[t] += 1
        self.assertEqual([s for s in bad if inbound[s] == 0], [],
                         "the rejected shape must have no entry screen at all")
        with self.assertRaises(ValueError) as cm:
            FC.assert_forward_only(bad)
        self.assertRegex(str(cm.exception), "Backward route|No entry screen found")

    def test_r3_m04_a_cycle_with_no_mirror_is_still_refused(self):
        """A three-screen cycle mirrors no single forward route, so the backward-edge rule
        alone would miss it. The entry-screen rule and the colour walk catch it."""
        cyc = {"A": ["B"], "B": ["C"], "C": ["A"]}
        with self.assertRaises(ValueError):
            FC.assert_forward_only(cyc)

    def test_r3_m05_two_entry_screens_are_refused(self):
        """CORRECTED 2026-11-01 (WILD1-R3-C1). This test used to require Meta's
        *"No entry screen found"* wording for a graph with TWO entry screens. That conflated
        two different defects and put words in Meta's mouth: Meta gave us that error for a
        graph with **no** entry screen, and never showed us the many-entries case at all.
        The validator now reports zero entries in Meta's words and many entries in ours, so
        a reader can tell which rule fired and which authority it came from.
        """
        with self.assertRaises(ValueError) as cm:
            FC.assert_forward_only({"A": ["C"], "B": ["C"], "C": []})
        msg = str(cm.exception)
        self.assertIn("more than one entry screen", msg)
        self.assertNotIn("No entry screen found", msg)

    def test_r3_m06_the_published_graph_passes(self):
        FC.assert_forward_only(PUBLISHED_GRAPH)
        FC.assert_forward_only(_graph(TARGET))
        FC.assert_forward_only(_graph(R3))

    def test_r3_m07_the_rejected_hashes_are_recorded_and_are_not_the_target(self):
        self.assertEqual(FC.REJECTED_CYCLIC_SHA256,
                         "98981193109ce426af1298dbfca5dd2f24cae2aee0a7af08b20dde56001bc36f")
        self.assertEqual(FC.REJECTED_CYCLIC_CANONICAL_SHA256,
                         "53dfe8fef7ca66c478421462a15e8992bb8839712e1fb7c8790f90c410e7f965")
        self.assertNotEqual(FC.TARGET_FLOW_SHA256, FC.REJECTED_CYCLIC_SHA256)
        self.assertNotEqual(FC.TARGET_FLOW_CANONICAL_SHA256,
                            FC.REJECTED_CYCLIC_CANONICAL_SHA256)
        self.assertEqual(len(FC.REJECTED_CYCLIC_META_ERRORS), 2)
        joined = " ".join(FC.REJECTED_CYCLIC_META_ERRORS)
        self.assertIn("Only forward routes can be specified", joined)
        self.assertIn("No entry screen found in the routing model", joined)


# ══════════════════════════════════════════════════════════════════════════════
# R3-F — the published artifact is forward-only, and is the published one
# ══════════════════════════════════════════════════════════════════════════════
class TestPublishedForwardOnly(unittest.TestCase):

    def test_r3_f01_the_target_hashes_are_the_published_ones(self):
        import hashlib
        raw = hashlib.sha256(TARGET.read_bytes()).hexdigest()
        doc = json.load(open(TARGET, encoding="utf-8"))
        can = hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"),
                                        ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(raw, FC.TARGET_FLOW_SHA256)
        self.assertEqual(raw,
                         "4ce27122a55f3efd4fbae117d620abf34024c5a86343bb23ce878182ba2bbd9d")
        self.assertEqual(can, FC.TARGET_FLOW_CANONICAL_SHA256)
        self.assertEqual(can,
                         "dccd3bc973b1b51526c5064027b5d8f61ee885293dfa9815a272609092aca17d")

    def test_r3_f02_the_routing_model_is_identical_to_the_published_r3_graph(self):
        self.assertEqual(_graph(TARGET), _graph(R3))
        self.assertEqual(_graph(TARGET), PUBLISHED_GRAPH)

    def test_r3_f03_forward_routes_agree_with_the_artifact(self):
        """The constant and the file are two places one fact lives. They must agree, or
        the guard is validating against a graph Meta never saw."""
        from_const = {k: list(v) for k, v in FC.FORWARD_ROUTES.items()}
        self.assertEqual(from_const, _graph(TARGET))
        self.assertEqual(FC.ENTRY_SCREEN, "APPOINTMENT")

    def test_r3_f04_summary_has_no_forward_successor(self):
        self.assertEqual(_graph(TARGET)["SUMMARY"], [])
        self.assertEqual(tuple(FC.FORWARD_ROUTES["SUMMARY"]), ())

    def test_r3_f05_summary_is_still_terminal(self):
        screens = {s["id"]: s for s in json.load(open(TARGET, encoding="utf-8"))["screens"]}
        self.assertIs(screens["SUMMARY"].get("terminal"), True)

    def test_r3_f06_the_artifact_still_carries_the_refusal_contract(self):
        doc = json.load(open(TARGET, encoding="utf-8"))
        screens = {s["id"]: s for s in doc["screens"]}
        self.assertEqual(len(screens["APPOINTMENT"]["data"]), 16)
        for key in ("slot_conflict_message", "has_refusal", "error_message", "has_error"):
            self.assertIn(key, screens["APPOINTMENT"]["data"])
        kids = screens["APPOINTMENT"]["layout"]["children"]
        self.assertEqual(kids[1].get("visible"), "${data.has_refusal}")
        self.assertEqual(kids[2].get("visible"), "${data.has_error}")
        self.assertEqual(json.dumps(doc).count('"visible"'), 2)

    def test_r3_f07_no_customer_phone_input_and_no_init_value(self):
        doc = json.load(open(TARGET, encoding="utf-8"))
        blob = json.dumps(doc, ensure_ascii=False)
        self.assertNotIn("init-value", blob)
        names = set()

        def walk(node):
            if isinstance(node, dict):
                if isinstance(node.get("type"), str) and "Input" in node["type"]:
                    names.add(node.get("name"))
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(doc)
        self.assertNotIn("phone", names, "a customer phone input came back")
        self.assertIn("seller_phone", names, "the seller's phone is r3's and must stay")

    def test_r3_f08_the_local_validator_now_runs_on_the_published_file(self):
        """"Published forward-only Flow JSON passes local structural validation" — and the
        routing check is part of it now, which is the gap Meta exposed."""
        import subprocess
        script = ROOT / "scripts" / "validate_flow_json.py"
        res = subprocess.run([sys.executable, "-I", str(script), str(TARGET)],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("0 error(s)", res.stdout)
        FC.assert_forward_only(_graph(TARGET))


# ══════════════════════════════════════════════════════════════════════════════
# R3-N — ordinary navigation
# ══════════════════════════════════════════════════════════════════════════════
class TestForwardNavigation(unittest.TestCase):
    RM = PUBLISHED_GRAPH

    def test_r3_n01_appointment_to_details_is_allowed(self):
        FC.validate_forward_route("APPOINTMENT", "DETAILS", self.RM)
        FC.validate_response_screen("date_selected", "APPOINTMENT", self.RM)

    def test_r3_n02_details_to_summary_is_allowed(self):
        FC.validate_forward_route("DETAILS", "SUMMARY", self.RM)
        FC.validate_response_screen("prepare_summary", "SUMMARY", self.RM)

    def test_r3_n03_init_has_no_origin_and_opens_on_appointment(self):
        FC.validate_forward_route(None, "APPOINTMENT", self.RM)
        FC.validate_response_screen("INIT", "APPOINTMENT", self.RM)

    def test_r3_n04_a_self_refresh_needs_no_edge(self):
        self.assertNotIn("APPOINTMENT", self.RM["APPOINTMENT"])
        FC.validate_forward_route("APPOINTMENT", "APPOINTMENT", self.RM)

    def test_r3_n05_arbitrary_details_to_appointment_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            FC.validate_forward_route("DETAILS", "APPOINTMENT", self.RM)
        self.assertIn("not a declared forward route", str(cm.exception))
        with self.assertRaises(ValueError):
            FC.validate_response_screen("prepare_summary", "APPOINTMENT", self.RM)

    def test_r3_n06_arbitrary_summary_to_appointment_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            FC.validate_forward_route("SUMMARY", "APPOINTMENT", self.RM)
        self.assertIn("not a declared forward route", str(cm.exception))
        with self.assertRaises(ValueError):
            FC.validate_response_screen("confirm_booking", "APPOINTMENT", self.RM)

    def test_r3_n07_backward_summary_to_details_is_refused(self):
        with self.assertRaises(ValueError):
            FC.validate_forward_route("SUMMARY", "DETAILS", self.RM)

    def test_r3_n08_skipping_a_screen_forward_is_refused(self):
        with self.assertRaises(ValueError):
            FC.validate_forward_route("APPOINTMENT", "SUMMARY", self.RM)

    def test_r3_n09_an_unknown_origin_is_not_a_pass(self):
        with self.assertRaises(ValueError):
            FC.validate_forward_route("NOWHERE", "APPOINTMENT", self.RM)

    def test_r3_n10_a_successful_confirm_cannot_return_backward(self):
        """The success path closes the Flow. It may not name a screen, and it may not be
        dressed up as a recovery."""
        term = {"extension_message_response": {"params": {"flow_token": "t"}}}
        FC.validate_response_screen("confirm_booking", "SUCCESS", self.RM,
                                    terminal_payload=term)
        for screen in ("APPOINTMENT", "DETAILS"):
            with self.assertRaises(ValueError):
                FC.validate_response_screen("confirm_booking", screen, self.RM)
        # and a refusal may never report success
        with self.assertRaises(ValueError) as cm:
            FC.validate_response_screen("confirm_booking", "SUCCESS", self.RM,
                                        outcome=FC.RECOVERY_SLOT_CONFLICT,
                                        terminal_payload=term)
        self.assertIn("may not return", str(cm.exception))

    def test_r3_n11_the_terminal_name_alone_does_not_escape_the_graph(self):
        with self.assertRaises(ValueError) as cm:
            FC.validate_response_screen("confirm_booking", "SUCCESS", self.RM)
        self.assertIn("extension_message_response", str(cm.exception))
        for junk in ({}, {"extension_message_response": "yes"},
                     {"extension_message_response": {}}, "SUCCESS", None):
            with self.assertRaises(ValueError):
                FC.validate_response_screen("confirm_booking", "SUCCESS", self.RM,
                                            terminal_payload=junk)

    def test_r3_n12_success_is_not_a_screen_in_the_artifact(self):
        doc = json.load(open(TARGET, encoding="utf-8"))
        self.assertNotIn("SUCCESS", [s["id"] for s in doc["screens"]])
        self.assertNotIn("SUCCESS", json.dumps(doc))
        self.assertNotIn(FC.TERMINAL_PROTOCOL_SCREEN, _graph(TARGET))


# ══════════════════════════════════════════════════════════════════════════════
# R3-R — the recovery allowlist
# ══════════════════════════════════════════════════════════════════════════════
class TestRecoveryReturns(unittest.TestCase):
    RM = PUBLISHED_GRAPH

    def test_r3_r01_a_genuine_slot_conflict_may_return_from_summary(self):
        FC.validate_recovery_return("SUMMARY", "confirm_booking",
                                    FC.RECOVERY_SLOT_CONFLICT, "APPOINTMENT")
        FC.validate_response_screen("confirm_booking", "APPOINTMENT", self.RM,
                                    outcome=FC.RECOVERY_SLOT_CONFLICT)

    def test_r3_r02_there_is_no_slot_conflict_from_details_because_none_is_produced(self):
        """The milestone allows `DETAILS -> APPOINTMENT` for a slot conflict "if that path
        is actually produced". It is not: all four `BookingSlotConflictError` raises are in
        `_confirm_booking`, which only `confirm_booking` reaches. An allowlist entry for a
        path nothing produces is a permission granted for nothing."""
        with self.assertRaises(ValueError) as cm:
            FC.validate_recovery_return("DETAILS", "prepare_summary",
                                        FC.RECOVERY_SLOT_CONFLICT, "APPOINTMENT")
        self.assertIn("no recovery return is permitted", str(cm.exception))
        self.assertNotIn(("DETAILS", "prepare_summary", FC.RECOVERY_SLOT_CONFLICT),
                         FC.RECOVERY_RETURNS)

    def test_r3_r03_a_token_error_may_return_from_every_real_trigger(self):
        for trigger in ("INIT", "date_selected", "prepare_summary", "confirm_booking"):
            FC.validate_response_screen(trigger, "APPOINTMENT", self.RM,
                                        outcome=FC.RECOVERY_TOKEN_INVALID)

    def test_r3_r04_an_unknown_trigger_cannot_authorise_a_return(self):
        for trigger in ("", "   ", "go_back", "navigate", "APPOINTMENT", "confirm", None):
            with self.assertRaises(ValueError):
                FC.validate_recovery_return("SUMMARY", trigger,
                                            FC.RECOVERY_SLOT_CONFLICT, "APPOINTMENT")
            with self.assertRaises(ValueError):
                FC.validate_response_screen(trigger, "APPOINTMENT", self.RM,
                                            outcome=FC.RECOVERY_SLOT_CONFLICT)

    def test_r3_r05_an_unrecognised_outcome_cannot_authorise_a_return(self):
        for outcome in ("", "OK", "SLOT_CONFLICTED", "slot_conflict", "TOKEN",
                        "Exception", "ValueError", "BOOM", "True", "*"):
            with self.assertRaises(ValueError) as cm:
                FC.validate_recovery_return("SUMMARY", "confirm_booking",
                                            outcome, "APPOINTMENT")
            self.assertIn("not a recognised typed refusal outcome", str(cm.exception))

    def test_r3_r06_a_generic_exception_cannot_authorise_a_return(self):
        """The route's `except Exception` arm must not be able to produce a recovery. It
        has no typed outcome to supply, and a class name is not one."""
        for outcome in (Exception.__name__, RuntimeError.__name__, ValueError.__name__,
                        KeyError.__name__, "BaseException"):
            with self.assertRaises(ValueError):
                FC.validate_recovery_return("SUMMARY", "confirm_booking",
                                            outcome, "APPOINTMENT")

    def test_r3_r07_a_wrong_origin_for_a_real_trigger_is_refused(self):
        """`origin` is derived from `TRIGGER_ORIGIN`; a caller that supplies a different
        one is claiming the customer is somewhere they cannot be."""
        with self.assertRaises(ValueError) as cm:
            FC.validate_recovery_return("APPOINTMENT", "confirm_booking",
                                        FC.RECOVERY_SLOT_CONFLICT, "APPOINTMENT")
        self.assertIn("is not where trigger", str(cm.exception))
        with self.assertRaises(ValueError):
            FC.validate_response_screen("confirm_booking", "APPOINTMENT", self.RM,
                                        outcome=FC.RECOVERY_SLOT_CONFLICT,
                                        origin="DETAILS")

    def test_r3_r08_a_recovery_may_not_name_an_arbitrary_screen(self):
        for screen in ("DETAILS", "SUMMARY", "SUCCESS", "", "ANY"):
            with self.assertRaises(ValueError):
                FC.validate_recovery_return("SUMMARY", "confirm_booking",
                                            FC.RECOVERY_SLOT_CONFLICT, screen)

    def test_r3_r09_the_allowlist_is_narrow_and_fully_enumerated(self):
        """Every entry is named here. Widening the allowlist fails this test, which is the
        point: it cannot grow quietly."""
        self.assertEqual(set(FC.RECOVERY_RETURNS), {
            ("SUMMARY", "confirm_booking", FC.RECOVERY_SLOT_CONFLICT),
            (None, "INIT", FC.RECOVERY_TOKEN_INVALID),
            ("APPOINTMENT", "date_selected", FC.RECOVERY_TOKEN_INVALID),
            ("DETAILS", "prepare_summary", FC.RECOVERY_TOKEN_INVALID),
            ("SUMMARY", "confirm_booking", FC.RECOVERY_TOKEN_INVALID),
        })
        self.assertEqual(set(FC.RECOVERY_RETURNS.values()), {"APPOINTMENT"})
        self.assertEqual(FC.RECOVERY_OUTCOMES,
                         frozenset({FC.RECOVERY_SLOT_CONFLICT, FC.RECOVERY_TOKEN_INVALID}))
        # every origin in the allowlist is the one TRIGGER_ORIGIN derives
        for origin, trigger, _ in FC.RECOVERY_RETURNS:
            self.assertEqual(FC.TRIGGER_ORIGIN[trigger], origin)

    def test_r3_r10_no_routing_model_can_authorise_a_recovery(self):
        """The two halves do not leak into each other. Even handed the rejected cyclic
        graph, an ordinary response may not reach APPOINTMENT from SUMMARY through the
        recovery path, and a recovery does not consult the graph at all."""
        cyclic = {"APPOINTMENT": ["DETAILS"], "DETAILS": ["SUMMARY", "APPOINTMENT"],
                  "SUMMARY": ["APPOINTMENT"]}
        # a recovery is authorised identically whichever graph is passed: it ignores it
        FC.validate_response_screen("confirm_booking", "APPOINTMENT", {},
                                    outcome=FC.RECOVERY_SLOT_CONFLICT)
        FC.validate_response_screen("confirm_booking", "APPOINTMENT", cyclic,
                                    outcome=FC.RECOVERY_SLOT_CONFLICT)
        # and a non-recovery is NOT authorised by claiming no outcome on the real graph
        with self.assertRaises(ValueError):
            FC.validate_response_screen("confirm_booking", "APPOINTMENT", self.RM)

    def test_r3_r11_fail_closed_and_recoverable_are_disjoint_and_complete(self):
        self.assertEqual(FC.FAIL_CLOSED_OUTCOMES | FC.RECOVERABLE_OUTCOMES,
                         FC.RECOVERY_OUTCOMES)
        self.assertEqual(FC.FAIL_CLOSED_OUTCOMES & FC.RECOVERABLE_OUTCOMES, frozenset())
        self.assertIn(FC.RECOVERY_SLOT_CONFLICT, FC.RECOVERABLE_OUTCOMES)
        self.assertIn(FC.RECOVERY_TOKEN_INVALID, FC.FAIL_CLOSED_OUTCOMES)


# ══════════════════════════════════════════════════════════════════════════════
# R3-C — the client cannot choose the screen
# ══════════════════════════════════════════════════════════════════════════════
class TestClientCannotChooseScreen(unittest.TestCase):

    def test_r3_c01_the_route_reads_no_screen_or_target_from_request_data(self):
        """By AST: no `data.get("screen")`, `data["target"]` or equivalent anywhere in the
        route or the service. The screen is a literal in backend-built dicts."""
        for path in (DX_PATH, BFS_PATH):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                key = None
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "get" and node.args
                        and isinstance(node.args[0], ast.Constant)):
                    key = node.args[0].value
                elif (isinstance(node, ast.Subscript)
                      and isinstance(node.slice, ast.Constant)):
                    key = node.slice.value
                if isinstance(key, str) and key in ("screen", "target", "target_screen",
                                                    "next_screen", "return_screen",
                                                    "next", "destination", "dest", "goto",
                                                    "to_screen", "response_screen"):
                    src = ast.unparse(node)
                    base = src.split("[")[0].split(".")[0]
                    # TIGHTENED 2026-11-01 (WILD1-R3-C1, R-F06). The old assertion only
                    # rejected a base containing "data", so `payload.get("target")` or
                    # `body["target"]` would have slipped past it. Now the ONLY base
                    # permitted to carry such a key is the server-built response dict.
                    self.assertIn(base, ("response_dict", "result", "doc", "screens",
                                         "comp", "n", "node", "self"),
                                  f"{path.name} reads {key!r} from {base!r}, which is not "
                                  f"a server-built response: {src}")

    def test_r3_c02_a_payload_claiming_a_screen_changes_nothing(self):
        """Behavioural, not structural: the guard's answer for a real trigger is the same
        whatever the payload says, because the payload is not an input to it."""
        RM = PUBLISHED_GRAPH
        with self.assertRaises(ValueError):
            FC.validate_response_screen("prepare_summary", "APPOINTMENT", RM)
        # there is no argument through which a client value could reach the decision:
        # `validate_response_screen` takes the trigger, the screen the backend chose, the
        # published graph and the typed outcome. None of those is client-supplied.
        import inspect
        params = set(inspect.signature(FC.validate_response_screen).parameters)
        self.assertEqual(params, {"trigger", "response_screen", "routing_model",
                                  "outcome", "origin", "terminal_payload"})

    def test_r3_c03_the_guard_is_the_only_way_a_screen_bearing_response_leaves(self):
        """Every encrypted return that names a screen goes through `_guarded_return`. Only
        the health check may use the raw encryptor, and it names no screen."""
        tree = ast.parse(DX_PATH.read_text(encoding="utf-8-sig"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.name == "booking_flow_data_exchange")
        raw, guarded = [], []
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "_encrypt_and_return":
                    raw.append(ast.unparse(node))
                elif node.func.id == "_guarded_return":
                    guarded.append(ast.unparse(node))
        self.assertEqual(len(raw), 1, f"raw encryptor used {len(raw)} times: {raw}")
        self.assertIn("health_response()", raw[0])
        self.assertGreaterEqual(len(guarded), 4, guarded)

    def test_r3_c04_the_guard_refuses_rather_than_emits(self):
        tree = ast.parse(DX_PATH.read_text(encoding="utf-8-sig"))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                  and n.name == "_guarded_return")
        body = ast.unparse(fn)
        self.assertIn("validate_response_screen", body)
        self.assertIn("BOOKING_FLOW_RESPONSE_REFUSED", body)
        self.assertIn("HTTPException", body)
        # the validate call must come before the encrypt call IN THE SOURCE. `ast.walk`
        # is breadth-first and does not yield source order, so the nodes are sorted by
        # position — an earlier version of this assertion read the two in walk order and
        # "failed" on correct code.
        order = sorted((n for n in ast.walk(fn) if isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Name)
                        and n.func.id in ("validate_response_screen",
                                          "_encrypt_and_return")),
                       key=lambda n: (n.lineno, n.col_offset))
        self.assertEqual([n.func.id for n in order],
                         ["validate_response_screen", "_encrypt_and_return"])

    def test_r3_c05_the_guard_validates_against_the_published_graph(self):
        from app.routes import flow_data_exchange as DX
        self.assertEqual(DX._ROUTING_MODEL, _graph(TARGET))
        self.assertEqual(DX._ROUTING_MODEL, PUBLISHED_GRAPH)


# ══════════════════════════════════════════════════════════════════════════════
# R3-P — the producer inventory the allowlist was derived from
# ══════════════════════════════════════════════════════════════════════════════
class TestProducerInventory(unittest.TestCase):
    """Re-derived by AST on every run. If a new producer appears, or an existing one moves
    to another handler, this fails — which is what stops the allowlist drifting away from
    the code it describes."""

    @staticmethod
    def _raises_by_function(path):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        owner = {}

        def walk(node, chain):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chain = chain + [node.name]
                for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                    owner[ln] = chain
            for child in ast.iter_child_nodes(node):
                walk(child, chain)
        for child in ast.iter_child_nodes(tree):
            walk(child, [])
        out = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc is not None:
                func = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
                name = (func.id if isinstance(func, ast.Name)
                        else getattr(func, "attr", None))
                if name in ("BookingSlotConflictError", "BookingTokenError",
                            "BookingAlreadyCompletedError"):
                    out.setdefault((name, owner.get(node.lineno, ["?"])[-1]),
                                   []).append(node.lineno)
        return out

    def test_r3_p01_slot_conflict_is_raised_only_inside_confirm_booking(self):
        found = self._raises_by_function(BFS_PATH)
        sites = {fn: lines for (exc, fn), lines in found.items()
                 if exc == "BookingSlotConflictError"}
        self.assertEqual(set(sites), {"_confirm_booking"}, sites)
        self.assertEqual(len(sites["_confirm_booking"]), 4,
                         f"four slot-conflict sites expected, found {sites}")

    def test_r3_p02_confirm_booking_is_reached_only_from_summary(self):
        self.assertEqual(FC.TRIGGER_ORIGIN["confirm_booking"], "SUMMARY")
        src = BFS_PATH.read_text(encoding="utf-8-sig")
        tree = ast.parse(src)
        callers = set()
        owner = {}

        def walk(node, chain):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chain = chain + [node.name]
                for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                    owner[ln] = chain
            for child in ast.iter_child_nodes(node):
                walk(child, chain)
        for child in ast.iter_child_nodes(tree):
            walk(child, [])
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_confirm_booking"):
                callers.add(owner.get(node.lineno, ["?"])[-1])
        self.assertEqual(callers, {"handle_confirm_booking"}, callers)

    def test_r3_p03_token_errors_originate_where_the_allowlist_says(self):
        found = self._raises_by_function(BFS_PATH)
        sites = {fn: len(lines) for (exc, fn), lines in found.items()
                 if exc == "BookingTokenError"}
        self.assertEqual(set(sites),
                         {"parse_booking_token", "resolve_context", "_confirm_booking"},
                         sites)
        # resolve_context is the first statement of every handler, so TOKEN_INVALID is
        # reachable from every trigger — which is exactly what the allowlist grants.
        for trigger in ("INIT", "date_selected", "prepare_summary", "confirm_booking"):
            self.assertIn((FC.TRIGGER_ORIGIN[trigger], trigger, FC.RECOVERY_TOKEN_INVALID),
                          FC.RECOVERY_RETURNS)

    def test_r3_p04_every_handler_resolves_context_before_reading_the_payload(self):
        """Why the expired-token screen carries no identity: `resolve_context` raises
        before any handler touches `data`."""
        tree = ast.parse(BFS_PATH.read_text(encoding="utf-8-sig"))
        for name in ("handle_init", "handle_date_selected", "handle_prepare_summary"):
            fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                      and n.name == name)
            calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute)
                     and n.func.attr == "resolve_context"]
            self.assertTrue(calls, f"{name} does not resolve a context")
            first = fn.body[0] if not isinstance(fn.body[0], ast.Expr) else fn.body[1]
            self.assertIn("resolve_context", ast.unparse(first),
                          f"{name} reads something before resolving the context")

    def test_r3_p05_the_only_appointment_responses_are_the_known_four(self):
        """Enumerated from source: two success paths that need no edge, and the two
        refusal builders."""
        tree = ast.parse(BFS_PATH.read_text(encoding="utf-8-sig"))
        owner = {}

        def walk(node, chain):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chain = chain + [node.name]
                for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                    owner[ln] = chain
            for child in ast.iter_child_nodes(node):
                walk(child, chain)
        for child in ast.iter_child_nodes(tree):
            walk(child, [])
        producers = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if (isinstance(k, ast.Constant) and k.value == "screen"
                            and isinstance(v, ast.Constant)):
                        producers.setdefault(v.value, set()).add(
                            owner.get(node.lineno, ["?"])[-1])
        self.assertEqual(producers.get("APPOINTMENT"),
                         {"handle_init", "handle_date_selected", "_refusal_screen"},
                         producers)
        self.assertEqual(producers.get("SUMMARY"), {"handle_prepare_summary"}, producers)
        self.assertEqual(producers.get("SUCCESS"), {"_success_response"}, producers)


# ══════════════════════════════════════════════════════════════════════════════
# R3-W — no refusal writes a booking; replay and identity intact
# ══════════════════════════════════════════════════════════════════════════════
class TestRefusalWritesNothing(_Converged, _World):

    def _booking_rows(self):
        from app.models import ThreadRevision
        return self.db.query(ThreadRevision).filter(
            ThreadRevision.status == "booked").count()

    def test_r3_w01_a_slot_conflict_writes_no_booking(self):
        self.agree()
        self._mock_schedule(valid=False)
        before = self._booking_rows()
        with self.assertRaises(BookingSlotConflictError) as cm:
            self.svc.handle_confirm_booking(self.token, self.payload())
        self.assertEqual(self._booking_rows(), before, "a refusal created a booking")
        data = cm.exception.refreshed_data
        self.assertEqual(data["screen"], "APPOINTMENT")
        self.assertIs(data["data"]["has_refusal"], True)
        self.assertTrue(data["data"]["slot_conflict_message"])
        # and the response the route would emit is authorised
        FC.validate_response_screen("confirm_booking", data["screen"], PUBLISHED_GRAPH,
                                    outcome=FC.RECOVERY_SLOT_CONFLICT)

    def test_r3_w02_the_refusal_payload_is_exactly_sixteen_keys_both_ways(self):
        self.agree()
        self._mock_schedule(valid=False)
        with self.assertRaises(BookingSlotConflictError) as cm:
            self.svc.handle_confirm_booking(self.token, self.payload())
        payload = cm.exception.refreshed_data["data"]
        declared = set(json.load(open(TARGET, encoding="utf-8"))["screens"][0]["data"])
        self.assertEqual(len(declared), 16)
        self.assertEqual(set(payload) - declared, set(), "supplied but not declared")
        self.assertEqual(declared - set(payload), set(), "declared but not supplied")
        FC.validate_screen_payload("APPOINTMENT", payload)

    def test_r3_w03_the_refusal_identity_comes_from_the_server(self):
        self.contact.wa_id = "+54 9 11 5555-1234"
        self.db.commit()
        self.agree()
        self._mock_schedule(valid=False)
        with self.assertRaises(BookingSlotConflictError) as cm:
            self.svc.handle_confirm_booking(
                self.token, self.payload(phone="+1 555 000 0000", name="Impostor"))
        data = cm.exception.refreshed_data["data"]
        self.assertEqual(data["contact_phone"], "5491155551234",
                         "the Flow's phone became the identity")
        self.assertNotIn("5550000000", json.dumps(data))
        self.assertNotIn("1234", data["contact_phone_display"].replace("1234", "", 1)
                         .replace("*", ""))
        self.assertTrue(data["contact_phone_display"])

    def test_r3_w04_a_refusal_never_preselects_the_time_that_failed(self):
        self.agree()
        self._mock_schedule(valid=False)
        with self.assertRaises(BookingSlotConflictError) as cm:
            self.svc.handle_confirm_booking(self.token, self.payload())
        self.assertEqual(cm.exception.refreshed_data["data"]["selected_time"], "")

    def test_r3_w05_an_expired_token_writes_nothing_and_is_fail_closed(self):
        before = self._booking_rows()
        with self.assertRaises(BookingTokenError):
            self.svc.handle_confirm_booking("not-a-real-token", self.payload())
        self.assertEqual(self._booking_rows(), before)

    def test_r3_w06_the_successful_confirm_still_books_exactly_once(self):
        """Replay and idempotency: the path the correction must not have disturbed."""
        self.agree()
        before = self._booking_rows()
        first = self.svc.handle_confirm_booking(self.token, self.payload())
        self.assertEqual(first["screen"], "SUCCESS")
        self.assertEqual(self._booking_rows(), before + 1)
        again = self.svc.handle_confirm_booking(self.token, self.payload())
        self.assertEqual(again["screen"], "SUCCESS")
        self.assertEqual(self._booking_rows(), before + 1, "a replay booked twice")
        term = first["data"]
        FC.validate_response_screen("confirm_booking", "SUCCESS", PUBLISHED_GRAPH,
                                    terminal_payload=term)

    def test_r3_w07_the_ordinary_forward_responses_are_authorised(self):
        self.agree()
        init = self.svc.handle_init(self.token)
        FC.validate_response_screen("INIT", init["screen"], PUBLISHED_GRAPH)
        refresh = self.svc.handle_date_selected(self.token, FUTURE.isoformat())
        FC.validate_response_screen("date_selected", refresh["screen"], PUBLISHED_GRAPH)
        summary = self.svc.handle_prepare_summary(self.token, self.payload())
        self.assertEqual(summary["screen"], "SUMMARY")
        FC.validate_response_screen("prepare_summary", summary["screen"], PUBLISHED_GRAPH)


# ══════════════════════════════════════════════════════════════════════════════
# R3-7 — MF-07, the expired/tampered-token path, enumerated honestly
# ══════════════════════════════════════════════════════════════════════════════
class TestMf07FailClosed(_Converged, _World):

    def test_r3_mf07_01_the_error_screen_carries_no_identity_and_no_availability(self):
        from app.services.flow_contract import appointment_payload
        screen = appointment_payload(
            error_message="Tu sesión de reserva expiró. Por favor, volvé a iniciar el "
                          "proceso.",
            has_error=True)
        self.assertIs(screen["has_error"], True)
        self.assertIs(screen["has_refusal"], False)
        self.assertEqual(screen["slot_conflict_message"], "")
        for key in ("booking_token", "selected_date", "selected_time", "customer_name",
                    "contact_phone", "contact_phone_display", "vehicle_summary",
                    "location_summary"):
            self.assertEqual(screen[key], "", f"{key} is not empty on the expired screen")
        self.assertEqual(screen["date"], [])
        self.assertEqual(screen["time"], [])
        self.assertIs(screen["is_date_enabled"], False)
        self.assertIs(screen["is_time_enabled"], False)

    def test_r3_mf07_02_it_is_classified_fail_closed_not_recoverable(self):
        self.assertIn(FC.RECOVERY_TOKEN_INVALID, FC.FAIL_CLOSED_OUTCOMES)
        self.assertNotIn(FC.RECOVERY_TOKEN_INVALID, FC.RECOVERABLE_OUTCOMES)
        self.assertIn(FC.RECOVERY_SLOT_CONFLICT, FC.RECOVERABLE_OUTCOMES)
        self.assertNotIn(FC.RECOVERY_SLOT_CONFLICT, FC.FAIL_CLOSED_OUTCOMES)

    def test_r3_mf07_03_the_copy_tells_the_customer_to_restart(self):
        """It must not claim retry is supported on that screen."""
        src = DX_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("volvé a iniciar el proceso", src)
        doc = json.load(open(TARGET, encoding="utf-8"))
        example = doc["screens"][0]["data"]["error_message"]["__example__"]
        self.assertIn("volvé a iniciar", example)

    def test_r3_mf07_04_continuing_from_it_reaches_the_typed_422_not_a_booking(self):
        """The route the review traced: Continue navigates to DETAILS with an empty token
        and empty date/time. Either the envelope token fails validation again — the same
        fail-closed screen — or the empty date is rejected by the typed `ValueError` path,
        which the route turns into HTTP 422. Neither writes a row."""
        from app.models import ThreadRevision
        before = self.db.query(ThreadRevision).filter(
            ThreadRevision.status == "booked").count()
        # (a) empty token -> BookingTokenError, the same fail-closed screen
        with self.assertRaises(BookingTokenError):
            self.svc.handle_prepare_summary("", {"date": "", "time": ""})
        # (b) valid token, empty date -> ValueError, which the route maps to 422
        with self.assertRaises(ValueError) as cm:
            self.svc.handle_prepare_summary(self.token, {"date": "", "time": ""})
        self.assertNotIsInstance(cm.exception, BookingTokenError)
        self.assertIn("Invalid date", str(cm.exception))
        self.assertEqual(self.db.query(ThreadRevision).filter(
            ThreadRevision.status == "booked").count(), before)

    def test_r3_mf07_05_the_guard_order_is_recorded_correctly(self):
        """The first closeout said `handle_prepare_summary` refuses because it requires a
        non-empty phone. It does not: `resolve_context` is the first statement and the
        phone check is the LAST of four. Asserted so the wrong mechanism cannot come back.
        """
        tree = ast.parse(BFS_PATH.read_text(encoding="utf-8-sig"))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                  and n.name == "handle_prepare_summary")
        body = [n for n in fn.body if not isinstance(n, ast.Expr)]
        self.assertIn("resolve_context", ast.unparse(body[0]))
        text = ast.unparse(fn)
        self.assertLess(text.index("resolve_context"), text.index("Invalid date"))
        self.assertLess(text.index("Invalid date"), text.index("phone is required"))


# ══════════════════════════════════════════════════════════════════════════════
# R3-X — mutation
# ══════════════════════════════════════════════════════════════════════════════
class TestMutants(unittest.TestCase):
    """Nine mutations, each the shape of a real regression. Every one must be killed by at
    least one assertion above. The mutants are applied to in-memory copies of the data the
    tests read, never to a file on disk."""

    RM = PUBLISHED_GRAPH

    def _must_raise(self, fn, *a, **kw):
        with self.assertRaises(Exception):
            fn(*a, **kw)

    def test_r3_x01_reintroducing_details_to_appointment_is_caught(self):
        mutant = copy.deepcopy(PUBLISHED_GRAPH)
        mutant["DETAILS"] = ["SUMMARY", "APPOINTMENT"]
        self._must_raise(FC.assert_forward_only, mutant)

    def test_r3_x02_reintroducing_summary_to_appointment_is_caught(self):
        mutant = copy.deepcopy(PUBLISHED_GRAPH)
        mutant["SUMMARY"] = ["APPOINTMENT"]
        self._must_raise(FC.assert_forward_only, mutant)

    def test_r3_x03_the_rejected_artifacts_graph_is_caught(self):
        mutant = {"APPOINTMENT": ["DETAILS"], "DETAILS": ["SUMMARY", "APPOINTMENT"],
                  "SUMMARY": ["APPOINTMENT"]}
        self._must_raise(FC.assert_forward_only, mutant)

    def test_r3_x04_broadening_recovery_to_every_data_exchange_is_caught(self):
        """Mutant: every (origin, trigger) pair may recover. The enumeration test and the
        DETAILS-slot-conflict test both fail on it."""
        broad = {(FC.TRIGGER_ORIGIN[t], t, o): "APPOINTMENT"
                 for t in FC.TRIGGER_ORIGIN for o in FC.RECOVERY_OUTCOMES}
        self.assertNotEqual(set(broad), set(FC.RECOVERY_RETURNS),
                            "the allowlist is as broad as the mutant")
        self.assertIn(("DETAILS", "prepare_summary", FC.RECOVERY_SLOT_CONFLICT), broad)
        self.assertNotIn(("DETAILS", "prepare_summary", FC.RECOVERY_SLOT_CONFLICT),
                         FC.RECOVERY_RETURNS)

    def test_r3_x05_trusting_a_client_target_screen_is_caught(self):
        """Mutant: the guard takes the screen from request data. There is no parameter
        through which that could happen, and the AST test proves no such read exists."""
        import inspect
        params = set(inspect.signature(FC.validate_response_screen).parameters)
        self.assertNotIn("data", params)
        self.assertNotIn("payload", params)
        self.assertNotIn("request", params)
        mutant_data = {"screen": "APPOINTMENT", "target": "APPOINTMENT",
                       "trigger": "prepare_summary"}
        # the only thing the guard would accept from this is the trigger, and with it the
        # screen is still refused
        self._must_raise(FC.validate_response_screen,
                         mutant_data["trigger"], mutant_data["screen"], self.RM)

    def test_r3_x06_allowing_an_unknown_trigger_is_caught(self):
        self._must_raise(FC.validate_recovery_return, "SUMMARY", "whatever",
                         FC.RECOVERY_SLOT_CONFLICT, "APPOINTMENT")
        self._must_raise(FC.validate_response_screen, "whatever", "APPOINTMENT", self.RM,
                         outcome=FC.RECOVERY_SLOT_CONFLICT)

    def test_r3_x07_allowing_a_generic_exception_is_caught(self):
        for name in ("Exception", "RuntimeError", "", None, True):
            self._must_raise(FC.validate_recovery_return, "SUMMARY", "confirm_booking",
                             name, "APPOINTMENT")

    def test_r3_x08_removing_the_slot_conflict_permission_is_caught(self):
        """Mutant: the genuine recovery is deleted. The positive test for it fails, which
        is what stops this correction being "refuse everything"."""
        self.assertIn(("SUMMARY", "confirm_booking", FC.RECOVERY_SLOT_CONFLICT),
                      FC.RECOVERY_RETURNS)
        FC.validate_recovery_return("SUMMARY", "confirm_booking",
                                    FC.RECOVERY_SLOT_CONFLICT, "APPOINTMENT")

    def test_r3_x09_a_refusal_reporting_success_is_caught(self):
        """Mutant: the recovery return is widened to the terminal screen, so a refusal
        would tell the customer the booking was made."""
        term = {"extension_message_response": {"params": {"flow_token": "t"}}}
        self._must_raise(FC.validate_response_screen, "confirm_booking", "SUCCESS",
                         self.RM, outcome=FC.RECOVERY_SLOT_CONFLICT,
                         terminal_payload=term)

    def test_r3_x10_bypassing_token_validation_is_caught(self):
        """Mutant: `resolve_context` stops raising on a bad token. Asserted at the source:
        every handler resolves first, and the raises are still there."""
        tree = ast.parse(BFS_PATH.read_text(encoding="utf-8-sig"))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                  and n.name == "resolve_context")
        raises = [n for n in ast.walk(fn) if isinstance(n, ast.Raise)]
        self.assertGreaterEqual(len(raises), 6,
                                "resolve_context stopped refusing bad tokens")
        names = set()
        for node in raises:
            func = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            names.add(func.id if isinstance(func, ast.Name)
                      else getattr(func, "attr", None))
        self.assertIn("BookingTokenError", names)

    def test_r3_x11_writing_before_returning_the_refusal_is_caught(self):
        """Mutant: a refusal branch books first. Covered behaviourally by R3-W01/W05; here
        the structural half — no refusal branch calls the authority's execute."""
        tree = ast.parse(BFS_PATH.read_text(encoding="utf-8-sig"))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                  and n.name == "_refusal_screen")
        text = ast.unparse(fn)
        for forbidden in ("execute", "commit(", "BookingAuthority", "add(", "flush("):
            self.assertNotIn(forbidden, text,
                             f"_refusal_screen performs {forbidden!r}")


# ══════════════════════════════════════════════════════════════════════════════
# R3-G — the guard, exercised for real (WILD1-R3-C1, review finding R-F06)
#
# The review found that three of the nine mutants died on signature pins, an incidental
# AttributeError and an error-message assertion rather than on behaviour. These tests call
# `_guarded_return` with real AES material, so "the guard refuses rather than emits" is a
# measured fact about the function and not an assertion about its source text.
# ══════════════════════════════════════════════════════════════════════════════
class TestGuardIsNotBypassable(unittest.TestCase):
    RM = dict(PUBLISHED_GRAPH)

    def _kv(self):
        import os
        return os.urandom(16), os.urandom(16)

    def _refusal_payload(self):
        return {"version": FC.FLOW_VERSION if hasattr(FC, "FLOW_VERSION") else "7.3",
                "screen": "APPOINTMENT",
                "data": FC.appointment_payload(slot_conflict_message="no pudimos",
                                               has_refusal=True)}

    def test_r3_g01_an_authorised_slot_conflict_is_emitted(self):
        from app.routes.flow_data_exchange import _guarded_return
        k, iv = self._kv()
        resp = _guarded_return(self._refusal_payload(), "confirm_booking", k, iv,
                               outcome=FC.RECOVERY_SLOT_CONFLICT)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.body, "an authorised refusal produced an empty body")

    def test_r3_g02_an_unauthorised_origin_trigger_pair_is_refused_not_emitted(self):
        from fastapi import HTTPException
        from app.routes.flow_data_exchange import _guarded_return
        k, iv = self._kv()
        # a slot conflict claimed on prepare_summary: nothing produces one, so it is not
        # on the allowlist, and the guard must refuse rather than encrypt it.
        with self.assertRaises(HTTPException) as cm:
            _guarded_return(self._refusal_payload(), "prepare_summary", k, iv,
                            outcome=FC.RECOVERY_SLOT_CONFLICT)
        self.assertEqual(cm.exception.status_code, 500)

    def test_r3_g03_an_unrecognised_outcome_is_refused(self):
        from fastapi import HTTPException
        from app.routes.flow_data_exchange import _guarded_return
        k, iv = self._kv()
        for bogus in ("BOOM", "Exception", "slot_conflict", ""):
            with self.assertRaises(HTTPException):
                _guarded_return(self._refusal_payload(), "confirm_booking", k, iv,
                                outcome=bogus)

    def test_r3_g04_an_unknown_trigger_is_refused(self):
        from fastapi import HTTPException
        from app.routes.flow_data_exchange import _guarded_return
        k, iv = self._kv()
        for bogus in ("", "navigate", "date_chosen", "confirm"):
            with self.assertRaises(HTTPException):
                _guarded_return(self._refusal_payload(), bogus, k, iv,
                                outcome=FC.RECOVERY_SLOT_CONFLICT)

    def test_r3_g05_a_forward_response_off_the_graph_is_refused(self):
        from fastapi import HTTPException
        from app.routes.flow_data_exchange import _guarded_return
        k, iv = self._kv()
        # DETAILS -> APPOINTMENT as an ORDINARY response: the edge Meta forbids.
        with self.assertRaises(HTTPException):
            _guarded_return({"version": "7.3", "screen": "APPOINTMENT", "data": {}},
                            "prepare_summary", k, iv)

    def test_r3_g06_success_without_an_extension_payload_is_refused(self):
        from fastapi import HTTPException
        from app.routes.flow_data_exchange import _guarded_return
        k, iv = self._kv()
        with self.assertRaises(HTTPException):
            _guarded_return({"version": "7.3", "screen": "SUCCESS", "data": {}},
                            "confirm_booking", k, iv)

    def test_r3_g07_success_under_a_refusal_outcome_is_refused(self):
        from fastapi import HTTPException
        from app.routes.flow_data_exchange import _guarded_return
        k, iv = self._kv()
        good = {"version": "7.3", "screen": "SUCCESS",
                "data": {"extension_message_response": {"params": {"flow_token": "t"}}}}
        with self.assertRaises(HTTPException):
            _guarded_return(dict(good), "confirm_booking", k, iv,
                            outcome=FC.RECOVERY_SLOT_CONFLICT)
        # and the same payload WITHOUT an outcome is fine, so the refusal above is about
        # the outcome and not about the payload
        self.assertEqual(_guarded_return(dict(good), "confirm_booking", k, iv).status_code,
                         200)

    def test_r3_g08_ping_uses_the_raw_encryptor_and_names_no_screen(self):
        from app.services.booking_flow_service import health_response
        from app.routes.flow_data_exchange import _encrypt_and_return
        self.assertNotIn("screen", health_response())
        k, iv = self._kv()
        self.assertEqual(_encrypt_and_return(health_response(), k, iv).status_code, 200)


# ══════════════════════════════════════════════════════════════════════════════
# R3-V — the canonical routing validator and its call sites (WILD1-R3-C1, R-F05)
# ══════════════════════════════════════════════════════════════════════════════
class TestCanonicalRoutingValidator(unittest.TestCase):
    SCREENS = ["APPOINTMENT", "DETAILS", "SUMMARY"]
    GOOD = {"APPOINTMENT": ["DETAILS"], "DETAILS": ["SUMMARY"], "SUMMARY": []}

    def _must_raise(self, rm, needle=None):
        with self.assertRaises(ValueError) as cm:
            FC.assert_forward_only(rm, screens=self.SCREENS)
        if needle:
            self.assertIn(needle, str(cm.exception))

    def test_r3_v01_the_published_graph_is_accepted(self):
        FC.assert_forward_only(self.GOOD, screens=self.SCREENS)
        FC.assert_required_forward_graph(self.GOOD, screens=self.SCREENS)

    def test_r3_v02_mirrored_backward_route_is_rejected_in_metas_words(self):
        self._must_raise({"APPOINTMENT": ["DETAILS"],
                          "DETAILS": ["SUMMARY", "APPOINTMENT"], "SUMMARY": []},
                         "Backward route [DETAILS->APPOINTMENT]")

    def test_r3_v03_summary_to_appointment_is_rejected_for_no_entry_screen(self):
        self._must_raise({"APPOINTMENT": ["DETAILS"], "DETAILS": ["SUMMARY"],
                          "SUMMARY": ["APPOINTMENT"]}, "No entry screen found")

    def test_r3_v04_a_route_to_an_unknown_screen_is_rejected(self):
        self._must_raise({"APPOINTMENT": ["DETAILS"], "DETAILS": ["NOPE"], "SUMMARY": []},
                         "route TO unknown screen")

    def test_r3_v05_a_route_from_an_unknown_screen_is_rejected(self):
        self._must_raise({"APPOINTMENT": ["DETAILS"], "DETAILS": ["SUMMARY"],
                          "SUMMARY": [], "GHOST": []}, "route FROM unknown screen")

    def test_r3_v06_a_declared_screen_absent_from_the_graph_is_rejected(self):
        self._must_raise({"APPOINTMENT": ["DETAILS"], "DETAILS": ["SUMMARY"]},
                         "declared but absent")

    def test_r3_v07_a_duplicate_route_entry_is_rejected(self):
        self._must_raise({"APPOINTMENT": ["DETAILS", "DETAILS"],
                          "DETAILS": ["SUMMARY"], "SUMMARY": []}, "duplicate route")

    def test_r3_v08_multiple_entry_screens_are_rejected_in_our_own_words(self):
        with self.assertRaises(ValueError) as cm:
            FC.assert_forward_only({"APPOINTMENT": ["SUMMARY"], "DETAILS": ["SUMMARY"],
                                    "SUMMARY": []}, screens=self.SCREENS)
        msg = str(cm.exception)
        self.assertIn("more than one entry screen", msg)
        # Meta never gave us this error, so it must not be dressed up as a quotation
        self.assertNotIn("No entry screen found", msg)

    def test_r3_v09_a_malformed_or_empty_graph_is_rejected(self):
        self._must_raise({}, "missing, empty")
        self._must_raise({"APPOINTMENT": "DETAILS", "DETAILS": ["SUMMARY"], "SUMMARY": []},
                         "not a list of screens")

    def test_r3_v10_a_legal_but_wrong_dag_fails_the_published_pin(self):
        other = {"APPOINTMENT": ["SUMMARY"], "SUMMARY": ["DETAILS"], "DETAILS": []}
        FC.assert_forward_only(other, screens=self.SCREENS)          # legal...
        with self.assertRaises(ValueError) as cm:                     # ...but not ours
            FC.assert_required_forward_graph(other, screens=self.SCREENS)
        self.assertIn("not the published forward graph", str(cm.exception))

    def test_r3_v11_the_runtime_enforces_its_own_graph_at_import(self):
        """R-F05: the validator is CALLED, not merely defined."""
        import ast as _ast
        src = DX_PATH.read_text(encoding="utf-8-sig")
        tree = _ast.parse(src)
        module_level = [n for n in tree.body
                        if isinstance(n, _ast.Expr) and isinstance(n.value, _ast.Call)
                        and isinstance(n.value.func, _ast.Name)
                        and "forward_only" in n.value.func.id]
        self.assertEqual(len(module_level), 1,
                         "the route must assert its own routing model at import time")

    def test_r3_v13_the_script_actually_rejects_the_cyclic_artifact(self):
        """EXECUTION test, not a string check (WILD1-R3-C1).

        `test_r3_v12` only proves the script *mentions* the validator. Mutant m11 — which
        replaced the call with `findings = []` while leaving the definition in place —
        SURVIVED that check. So this test runs the script as a subprocess: the rejected
        cyclic artifact must exit non-zero with a routing error naming Meta's rule, and the
        published artifact must exit zero. That is the only form of proof that the
        general-purpose validator is wired up, which is review finding R-F05.
        """
        import copy
        import json as _json
        import subprocess
        import sys as _sys
        import tempfile

        root = TARGET.parents[3]
        script = root / "scripts" / "validate_flow_json.py"
        self.assertTrue(script.exists(), script)

        def run(path):
            return subprocess.run([_sys.executable, "-I", str(script), str(path)],
                                  capture_output=True, text=True, cwd=str(root))

        # the published artifact passes
        ok = run(TARGET)
        self.assertEqual(ok.returncode, 0,
                         f"the published artifact must pass:\n{ok.stdout}\n{ok.stderr}")
        self.assertIn("0 routing", ok.stdout)

        # the rejected cyclic artifact must NOT
        cyclic = copy.deepcopy(_json.loads(TARGET.read_text(encoding="utf-8")))
        cyclic["routing_model"] = {"APPOINTMENT": ["DETAILS"],
                                   "DETAILS": ["SUMMARY", "APPOINTMENT"],
                                   "SUMMARY": ["APPOINTMENT"]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as fh:
            _json.dump(cyclic, fh, indent=2, ensure_ascii=False)
            bad = fh.name
        out = run(bad)
        self.assertNotEqual(out.returncode, 0,
                            f"the cyclic artifact must be refused:\n{out.stdout}")
        self.assertIn("ROUTING", out.stdout)
        self.assertIn("Backward route [DETAILS->APPOINTMENT]", out.stdout)

    def test_r3_v12_the_generator_and_the_script_call_the_shared_validator(self):
        """No second copy of the graph rules anywhere."""
        gen = (TARGET.parent / "make_r3_refusal_target.py").read_text(encoding="utf-8-sig")
        self.assertIn("assert_required_forward_graph", gen)
        self.assertNotIn("def _assert_forward_only_dag", gen,
                         "the generator kept its own copy of the rules")
        script = (TARGET.parents[3] / "scripts" / "validate_flow_json.py").read_text(
            encoding="utf-8-sig")
        self.assertIn("assert_forward_only", script)
        self.assertIn("routing_findings", script)
        # and it must not reimplement them
        self.assertNotIn("inbound = {", script)


# ══════════════════════════════════════════════════════════════════════════════
# R3-N — the obsolete v7.6 generator is neutralised (WILD1-R3-C1)
#
# `make_minimal_target.py` used to exit 0 while writing a Meta-invalid cyclic artifact. The
# commit-manifest verification found it still live and unmarked. These tests pin the
# neutralisation behaviourally: it must fail, write nothing, and not be mistakable for an
# active generator.
# ══════════════════════════════════════════════════════════════════════════════
class TestObsoleteV76GeneratorIsNeutralised(unittest.TestCase):
    SCRIPT = TARGET.parent / "make_minimal_target.py"

    def test_r3_n01_the_script_exists_and_is_not_silently_deleted(self):
        """History is preserved, not erased: the path stays, with a superseded header."""
        self.assertTrue(self.SCRIPT.exists(), self.SCRIPT)
        head = self.SCRIPT.read_text(encoding="utf-8-sig")[:400]
        self.assertIn("SUPERSEDED", head)

    def test_r3_n02_it_exits_nonzero_and_writes_nothing_even_with_an_output_path(self):
        import subprocess, sys as _sys, tempfile, os
        out = os.path.join(tempfile.mkdtemp(), "must_not_exist.json")
        r = subprocess.run([_sys.executable, "-I", str(self.SCRIPT),
                            str(TARGET.parent / "PUBLISHED_28104222025943520_v7.3.json"),
                            out], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse(os.path.exists(out), "the neutralised generator wrote a file")
        self.assertEqual(r.stdout, "", "it emitted something on stdout")
        self.assertTrue(r.stderr.startswith(
            "STOP: TARGET_v7.6 is superseded and Meta-invalid."), r.stderr[:120])

    def test_r3_n03_it_exits_nonzero_with_no_arguments(self):
        import subprocess, sys as _sys
        r = subprocess.run([_sys.executable, "-I", str(self.SCRIPT)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_r3_n04_importing_it_fails_closed_too(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("_v76", self.SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        with self.assertRaises(SystemExit) as cm:
            spec.loader.exec_module(mod)
        self.assertEqual(cm.exception.code, 2)

    def test_r3_n05_it_contains_no_active_backward_edge_assignment(self):
        """By AST: no assignment anywhere introduces either prohibited edge."""
        tree = ast.parse(self.SCRIPT.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                src = ast.unparse(node)
                self.assertNotIn("routing_model", src,
                                 f"an active routing_model assignment survives: {src}")
                for edge in ('"APPOINTMENT"', "'APPOINTMENT'"):
                    if edge in src and ("DETAILS" in src or "SUMMARY" in src):
                        self.fail(f"an active backward-edge assignment survives: {src}")
        # and it builds no JSON at all
        self.assertNotIn("json.dumps", ast.unparse(tree))
        self.assertNotIn("json.loads", ast.unparse(tree))

    def test_r3_n06_it_names_the_authoritative_artifact_generator_and_hashes(self):
        text = self.SCRIPT.read_text(encoding="utf-8-sig")
        self.assertIn("make_r3_refusal_target.py", text)
        self.assertIn("TARGET_v7.7-r3-prefill-refusal.json", text)
        self.assertIn("4ce27122a55f3efd4fbae117d620abf34024c5a86343bb23ce878182ba2bbd9d", text)
        self.assertIn("dccd3bc973b1b51526c5064027b5d8f61ee885293dfa9815a272609092aca17d", text)
        self.assertIn("cyclic", text.lower())
        self.assertIn("git", text.lower())

    def test_r3_n07_no_active_reference_recommends_it(self):
        """Zero references presenting it as a way to produce an artifact.

        Scope: production code, scripts, the other Flow generators and the booking README —
        the places a reader would look for "how do I build the target". THIS module is
        excluded by name, because it references the path precisely in order to assert the
        script is dead, and so is the neutralised script itself.
        """
        root = TARGET.parents[3]
        offenders = []
        targets = [root / "backend", root / "scripts", root / "meta"]
        files = []
        for base in targets:
            if base.exists():
                files += [f for f in base.rglob("*.py") if "__pycache__" not in str(f)]
        files.append(TARGET.parent / "README.md")
        for f in files:
            if f.name == "make_minimal_target.py":
                continue
            for i, line in enumerate(f.read_text(encoding="utf-8-sig",
                                                 errors="replace").splitlines(), 1):
                if "make_minimal_target" not in line:
                    continue
                low = line.lower()
                # a mention is acceptable only if it marks the script as dead or historical
                marked = any(w in low for w in
                             ("supersed", "neutralis", "used to", "wrong baseline",
                              "audit evidence", "historical", "obsolete", "exits 2",
                              "is now neutralised"))
                if not marked:
                    offenders.append(f"{f.relative_to(root)}:{i}: {line.strip()}")
        self.assertEqual(offenders, [],
                         "a reference still presents the v7.6 generator as usable")

    def test_r3_n08_the_v76_artifact_is_retained_but_fails_routing_validation(self):
        """Historical evidence, and provably inadmissible."""
        import json as _json
        v76 = TARGET.parent / "TARGET_v7.6-minimal-refusal.json"
        self.assertTrue(v76.exists(), "v7.6 must be retained as historical evidence")
        doc = _json.loads(v76.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            FC.assert_forward_only(doc["routing_model"],
                                   screens=[s["id"] for s in doc["screens"]])

    def test_r3_n09_the_active_generator_still_reproduces_the_artifact(self):
        """The neutralisation must not have disturbed the real generator."""
        import hashlib, subprocess, sys as _sys, tempfile, os
        gen = TARGET.parent / "make_r3_refusal_target.py"
        out = os.path.join(tempfile.mkdtemp(), "regen.json")
        r = subprocess.run([_sys.executable, "-I", str(gen),
                            str(TARGET.parent / "CANDIDATE_v7.4-prefill-r3.json"), out],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(open(out, "rb").read(), TARGET.read_bytes(),
                         "the active generator no longer reproduces the artifact")
        self.assertEqual(
            hashlib.sha256(open(out, "rb").read()).hexdigest(),
            "4ce27122a55f3efd4fbae117d620abf34024c5a86343bb23ce878182ba2bbd9d")


if __name__ == "__main__":
    unittest.main()
