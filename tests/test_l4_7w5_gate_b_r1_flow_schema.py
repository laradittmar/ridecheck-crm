"""L4.7W5 Gate B R1 — the component-schema checks that Meta actually enforces.

Gate B preparation called the v7.4 prefill candidate contract-PASS. Its evidence was that
the file parsed and every `${data.*}` reference resolved against its screen's declared
data. Both were true. Both were irrelevant to what Meta checks: a property can be
well-formed JSON, reference perfectly declared data, and still be disallowed on that
component type. Meta's Ejecutar returned four component-schema errors.

These tests encode those four errors so the same class of miss cannot recur, and they pin
the one finding that is NOT ours — `on-select-action.name = "data_exchange"` on the date
Dropdown is present in the LIVE PUBLISHED asset, so Meta's current validator rejects a
Flow that is deployed and working today. That is an owner decision, not a silent fix.

Local PASS is never Meta validation.
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_flow_json import check                                   # noqa: E402

FLOWS = ROOT / "meta" / "flows" / "booking"
PUBLISHED = FLOWS / "PUBLISHED_28104222025943520_v7.3.json"
REJECTED = FLOWS / "CANDIDATE_v7.4-prefill.json"
R1 = FLOWS / "CANDIDATE_v7.4-prefill-r1.json"


def load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def errors(p, precedent=None):
    return [f for f in check(load(p), load(precedent) if precedent else None) if f[0] == "ERROR"]


class MetaErrorsAreReproduced(unittest.TestCase):
    """All four authoritative errors, at the paths Meta named."""

    def setUp(self):
        self.found = {where: msg for _, where, msg in errors(REJECTED)}

    def test_gbr1_01_init_value_on_the_date_dropdown(self):
        w = ".screens[0].layout.children[3].children[0].init-value"
        self.assertIn(w, self.found)
        self.assertIn("not allowed in 'Dropdown'", self.found[w])

    def test_gbr1_02_init_value_on_the_time_dropdown(self):
        w = ".screens[0].layout.children[3].children[1].init-value"
        self.assertIn(w, self.found)
        self.assertIn("not allowed in 'Dropdown'", self.found[w])

    def test_gbr1_03_dropdown_on_select_action_name(self):
        w = ".screens[0].layout.children[3].children[0]['on-select-action'].name"
        self.assertIn(w, self.found)
        self.assertIn("Expected 'update_data'", self.found[w])

    def test_gbr1_04_init_value_on_the_details_text_input(self):
        w = ".screens[1].layout.children[1].children[0].init-value"
        self.assertIn(w, self.found)
        self.assertIn("not allowed in 'TextInput'", self.found[w])

    def test_gbr1_05_exactly_four_errors_no_more_no_less(self):
        self.assertEqual(len(self.found), 4, "the validator must mirror Meta's result exactly")


class R1RemovesWhatWeIntroduced(unittest.TestCase):

    def test_gbr1_06_no_init_value_anywhere_in_r1(self):
        self.assertNotIn("init-value", R1.read_text(encoding="utf-8"))

    def test_gbr1_07_r1_clears_three_of_the_four_errors(self):
        remaining = {w for _, w, _ in errors(R1)}
        for cleared in (".screens[0].layout.children[3].children[0].init-value",
                        ".screens[0].layout.children[3].children[1].init-value",
                        ".screens[1].layout.children[1].children[0].init-value"):
            self.assertNotIn(cleared, remaining)

    def test_gbr1_08_the_rejected_artifact_is_preserved_as_evidence(self):
        self.assertTrue(REJECTED.exists(), "the rejected candidate must remain for forensics")
        self.assertNotEqual(REJECTED.read_bytes(), R1.read_bytes())


class TheRemainingErrorIsNotOurs(unittest.TestCase):
    """The single remaining finding is inherited from the live published Flow."""

    ACTION = ".screens[0].layout.children[3].children[0]['on-select-action'].name"

    def test_gbr1_09_the_published_live_asset_fails_the_same_check(self):
        published = {w for _, w, _ in errors(PUBLISHED)}
        self.assertIn(self.ACTION, published,
                      "if this ever passes, Meta changed its validator — re-derive the plan")

    def test_gbr1_10_r1_inherits_exactly_that_one_finding(self):
        self.assertEqual({w for _, w, _ in errors(R1)}, {self.ACTION})

    def test_gbr1_11_r1_does_not_silently_change_the_action(self):
        """Changing it to update_data would break handle_date_selected — an owner call."""
        d = load(R1)
        dd = d["screens"][0]["layout"]["children"][3]["children"][0]
        self.assertEqual(dd["on-select-action"]["name"], "data_exchange")
        self.assertEqual(dd["on-select-action"]["payload"]["trigger"], "date_selected")


class ContractPreserved(unittest.TestCase):

    def setUp(self):
        self.r1, self.pub = load(R1), load(PUBLISHED)

    def test_gbr1_12_routing_and_screens_unchanged(self):
        self.assertEqual([s["id"] for s in self.r1["screens"]],
                         [s["id"] for s in self.pub["screens"]])
        self.assertEqual(self.r1.get("routing_model"), self.pub.get("routing_model"))
        self.assertEqual(self.r1.get("version"), self.pub.get("version"))
        self.assertEqual(self.r1.get("data_api_version"), self.pub.get("data_api_version"))
        self.assertEqual([s["id"] for s in self.r1["screens"] if s.get("terminal")], ["SUMMARY"])

    def test_gbr1_13_every_data_reference_resolves(self):
        import re
        for s in self.r1["screens"]:
            declared = set((s.get("data") or {}).keys())
            refs = set(re.findall(r"\$\{data\.([A-Za-z0-9_]+)\}", json.dumps(s)))
            self.assertEqual(refs - declared, set(), f"{s['id']} references undeclared data")

    def test_gbr1_14_booking_token_and_summaries_survive(self):
        ap = self.r1["screens"][0]["data"]
        for k in ("booking_token", "vehicle_summary", "location_summary", "date", "time"):
            self.assertIn(k, ap)

    def test_gbr1_15_no_component_type_without_meta_precedent(self):
        def types(d):
            out = set()
            def w(n):
                if isinstance(n, dict):
                    if isinstance(n.get("type"), str): out.add(n["type"])
                    for v in n.values(): w(v)
                elif isinstance(n, list):
                    for v in n: w(v)
            w(d); return out
        self.assertEqual(types(self.r1) - types(self.pub), set())

    def test_gbr1_16_no_secret_or_customer_pii(self):
        txt = R1.read_text(encoding="utf-8")
        for marker in ("EA" + "A", "AIz" + "a", "sk" + "-", "-----BEG" + "IN"):
            self.assertNotIn(marker, txt)
        import re
        real = [n for n in re.findall(r"\b549\d{10}\b", txt) if n != "5491100000000"]
        self.assertEqual(real, [], "a real-looking phone number is embedded")

    def test_gbr1_17_submit_payload_keys_match_the_backend(self):
        """The backend consumes these at prepare_summary / confirm_booking."""
        payloads = []
        def w(n):
            if isinstance(n, dict):
                if n.get("name") == "data_exchange" and isinstance(n.get("payload"), dict):
                    payloads.append(set(n["payload"]))
                for v in n.values(): w(v)
            elif isinstance(n, list):
                for v in n: w(v)
        w(self.r1)
        final = max(payloads, key=len)
        for k in ("booking_token", "date", "time", "name", "email",
                  "inspection_address", "seller_name", "seller_phone", "listing_url"):
            self.assertIn(k, final)


class LocalIsNotMeta(unittest.TestCase):

    def test_gbr1_18_the_validator_says_so_out_loud(self):
        src = (ROOT / "scripts" / "validate_flow_json.py").read_text(encoding="utf-8")
        self.assertIn("Local PASS is necessary and never sufficient", src)
        self.assertIn("Meta Ejecutar remains the authority", src)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
