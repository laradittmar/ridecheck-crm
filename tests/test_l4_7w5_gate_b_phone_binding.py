"""L4.7W5 Gate B — the masked phone must render, not print its own reference.

R1 published clean and reached the tester's handset. Every screen worked: the vehicle and
location lines, both dropdowns, the date→times round trip, DETAILS with no editable phone
field. One line was wrong. The DETAILS caption showed

    Te contactamos a este WhatsApp:
    ${data.contact_phone_display}

— the reference itself, as characters. The backend was never at fault: `_identity_fields`
supplies `contact_phone_display` on every APPOINTMENT payload, the navigate payload carries
it to DETAILS, and DETAILS declares it. All three boundaries held.

What separated that one line from the 39 references that did resolve is that it was the
only property in the asset mixing a static sentence with a dynamic reference. Meta's Flow
JSON reference says the two variants cannot be combined; Meta's component reference lists
`text` as dynamic on TextCaption as well as TextBody, so the component type was never the
problem. Ejecutar did not object to r1 — which is the point worth keeping: Meta's validator
passes assets that are broken on a handset, so this class of defect has to be caught here.

R3 therefore keeps the wording and splits it in two: a static caption, then the masked
value alone on a component and in a placement that the handset has already proven —
`SingleColumnLayout > TextBody` with the whole property as the reference, exactly how
`vehicle_summary` and `location_summary` render on APPOINTMENT.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_flow_json import check, mixed_static_and_dynamic          # noqa: E402

FLOWS = ROOT / "meta" / "flows" / "booking"
PUBLISHED = FLOWS / "PUBLISHED_28104222025943520_v7.3.json"
REJECTED = FLOWS / "CANDIDATE_v7.4-prefill.json"
R1 = FLOWS / "CANDIDATE_v7.4-prefill-r1.json"
R2 = FLOWS / "CANDIDATE_v7.4-prefill-r2.json"
R3 = FLOWS / "CANDIDATE_v7.4-prefill-r3.json"

LABEL = "Te contactamos a este WhatsApp:"
REF = "${data.contact_phone_display}"


def load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def findings(p, level):
    return [f for f in check(load(p)) if f[0] == level]


def strings(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from strings(v)
    elif isinstance(node, str):
        yield node


def details_children(doc):
    return [s for s in doc["screens"] if s["id"] == "DETAILS"][0]["layout"]["children"]


class TheDefectIsDetected(unittest.TestCase):
    """The rule must catch r1 and must not touch anything Meta has accepted in use."""

    def test_pb_01_r1_carries_exactly_one_mixed_reference(self):
        f = findings(R1, "RENDER")
        self.assertEqual(len(f), 1, "r1's only render defect was the phone caption")
        self.assertIn("TextCaption", f[0][2])

    def test_pb_02_the_published_flow_is_not_condemned(self):
        """A rule that fails the Flow running in production is the rule that is wrong."""
        self.assertEqual(findings(PUBLISHED, "RENDER"), [])
        self.assertEqual(findings(PUBLISHED, "ERROR"), [])

    def test_pb_03_r3_has_no_mixed_reference(self):
        self.assertEqual(findings(R3, "RENDER"), [])
        self.assertEqual(findings(R3, "ERROR"), [])

    def test_pb_04_every_reference_in_r3_is_the_whole_property(self):
        for s in strings(load(R3)):
            if "${" in s:
                self.assertFalse(mixed_static_and_dynamic(s), f"{s!r} would render literally")

    def test_pb_05_the_rule_recognises_the_shapes_it_must(self):
        self.assertTrue(mixed_static_and_dynamic("Hola ${data.x}"))
        self.assertTrue(mixed_static_and_dynamic("${data.x} y ${data.y}"))
        self.assertFalse(mixed_static_and_dynamic("${data.x}"))
        self.assertFalse(mixed_static_and_dynamic("Hola"))


class TheCorrectionUsesOnlyProvenPatterns(unittest.TestCase):

    def setUp(self):
        self.children = details_children(load(R3))
        self.texts = [c for c in self.children if c.get("type", "").startswith("Text")]

    def test_pb_06_the_wording_is_retained(self):
        self.assertIn(LABEL, [c.get("text") for c in self.texts])

    def test_pb_07_the_masked_value_is_a_whole_property_reference(self):
        value = [c for c in self.texts if c.get("text") == REF]
        self.assertEqual(len(value), 1, "the masked number must appear exactly once")
        self.assertEqual(value[0]["type"], "TextBody",
                         "TextBody is the component the handset has proven interpolates")

    def test_pb_08_both_components_sit_where_dynamic_text_has_rendered_live(self):
        """Outside the Form, as a direct child of SingleColumnLayout — the proven placement.

        Dynamic text has never been proven to render inside a Form; on APPOINTMENT, where
        it demonstrably works, both bindings are siblings of the Form, not children of it.
        """
        for c in self.children:
            if c.get("type") == "Form":
                self.assertNotIn(REF, list(strings(c)))
                self.assertNotIn(LABEL, [x.get("text") for x in c["children"]])
        self.assertIn(REF, [c.get("text") for c in self.texts])

    def test_pb_09_the_pattern_has_a_precedent_in_the_published_asset(self):
        appointment = [s for s in load(PUBLISHED)["screens"] if s["id"] == "APPOINTMENT"][0]
        proven = [c for c in appointment["layout"]["children"]
                  if c.get("type") == "TextBody" and re.fullmatch(r"\$\{data\.\w+\}", c.get("text", ""))]
        self.assertGreaterEqual(len(proven), 2, "SingleColumnLayout > TextBody > ${data.*}")

    def test_pb_10_no_component_type_without_meta_precedent(self):
        def types(d):
            return {n["type"] for n in _nodes(d) if isinstance(n.get("type"), str)}
        self.assertEqual(types(load(R3)) - types(load(PUBLISHED)), set())


def _nodes(node):
    if isinstance(node, dict):
        if "type" in node:
            yield node
        for v in node.values():
            yield from _nodes(v)
    elif isinstance(node, list):
        for v in node:
            yield from _nodes(v)


class NothingElseMoved(unittest.TestCase):
    """R3 differs from R1 in the DETAILS display components and nowhere else."""

    def test_pb_11_version_routing_and_screens_unchanged(self):
        r3, r1 = load(R3), load(R1)
        self.assertEqual(r3["version"], "7.3")
        self.assertEqual(r3["data_api_version"], r1["data_api_version"])
        self.assertEqual(r3["routing_model"], r1["routing_model"])
        self.assertEqual([s["id"] for s in r3["screens"]], [s["id"] for s in r1["screens"]])

    def test_pb_12_screen_data_declarations_unchanged(self):
        for a, b in zip(load(R1)["screens"], load(R3)["screens"]):
            self.assertEqual(a.get("data"), b.get("data"), f"{a['id']} data declaration moved")

    def test_pb_13_actions_and_payloads_identical(self):
        def actions(d):
            out = []
            for n in _nodes(d):
                for k in ("on-click-action", "on-select-action"):
                    if isinstance(n.get(k), dict):
                        out.append((k, json.dumps(n[k], sort_keys=True, ensure_ascii=False)))
            return sorted(out)
        self.assertEqual(actions(load(R3)), actions(load(R1)))

    def test_pb_14_only_the_details_layout_differs(self):
        r1, r3 = load(R1), load(R3)
        for a, b in zip(r1["screens"], r3["screens"]):
            if a["id"] != "DETAILS":
                self.assertEqual(a, b, f"{a['id']} must be untouched")

    def test_pb_15_the_form_lost_only_the_broken_caption(self):
        def form(doc):
            return [c for c in details_children(doc) if c.get("type") == "Form"][0]
        before, after = form(load(R1)), form(load(R3))
        self.assertEqual(before["name"], after["name"])
        removed = [c for c in before["children"] if c not in after["children"]]
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]["type"], "TextCaption")
        self.assertIn(REF, removed[0]["text"])
        self.assertEqual([c for c in after["children"] if c not in before["children"]], [])

    def test_pb_16_no_init_value(self):
        self.assertEqual(R3.read_text(encoding="utf-8").count("init-value"), 0)

    def test_pb_17_every_data_reference_resolves(self):
        for s in load(R3)["screens"]:
            declared = set((s.get("data") or {}).keys())
            refs = set(re.findall(r"\$\{data\.([A-Za-z0-9_]+)\}", json.dumps(s)))
            self.assertEqual(refs - declared, set(), f"{s['id']} references undeclared data")


class PhoneAuthorityUnchanged(unittest.TestCase):

    def test_pb_18_no_editable_customer_phone_field(self):
        form = [c for c in details_children(load(R3)) if c.get("type") == "Form"][0]
        names = [c.get("name") for c in form["children"] if c.get("type") == "TextInput"]
        self.assertNotIn("phone", names)
        self.assertEqual(names, ["name", "email", "inspection_address",
                                 "seller_name", "seller_phone", "listing_url"])

    def test_pb_19_the_submitted_phone_stays_backend_canonical(self):
        form = [c for c in details_children(load(R3)) if c.get("type") == "Form"][0]
        footer = [c for c in form["children"] if c.get("type") == "Footer"][0]
        self.assertEqual(footer["on-click-action"]["payload"]["phone"], "${data.contact_phone}")

    def test_pb_20_no_real_number_secret_or_pii_in_the_asset(self):
        txt = R3.read_text(encoding="utf-8")
        for marker in ("EA" + "A", "AIz" + "a", "sk" + "-", "-----BEG" + "IN"):
            self.assertNotIn(marker, txt)
        self.assertEqual([n for n in re.findall(r"\b549\d{10}\b", txt) if n != "5491100000000"], [])
        self.assertNotIn("contact_phone_display\": \"5", txt)


class EvidenceKept(unittest.TestCase):

    def test_pb_21_every_prior_candidate_survives(self):
        for p in (PUBLISHED, REJECTED, R1, R2):
            self.assertTrue(p.exists(), f"{p.name} is evidence and must remain")
        self.assertNotEqual(R1.read_bytes(), R3.read_bytes())

    def test_pb_22_the_validator_records_what_proved_the_rule(self):
        src = (ROOT / "scripts" / "validate_flow_json.py").read_text(encoding="utf-8")
        self.assertIn("handset", src, "the rule's authority is the handset, not a guess")
        self.assertIn("Local PASS is necessary and never sufficient", src)
        self.assertIn("RENDER, not ERROR", src,
                      "the file must not claim Ejecutar rejects what it accepted")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
