"""L4.7W5 Gate B — Meta Flow component-schema checks, corrected by Meta's own results.

Gate B preparation called the v7.4 prefill candidate contract-PASS. Its evidence was that
the file parsed and every `${data.*}` reference resolved. Both were true; neither says
anything about which properties a component type accepts. Meta rejected the candidate.

Then this test file made the opposite mistake. Meta had reported an error on the date
Dropdown's `on-select-action` alongside the `init-value` errors, so a rule was written
requiring `update_data` — and, because the published asset carries `data_exchange`, the
suite concluded that the LIVE Flow was invalid and that a backend-contract milestone was
needed to fix it. The owner then ran Ejecutar on all three assets:

    PUBLISHED v7.3  274038ba…1afb15  PASS, zero errors
    CANDIDATE v7.4  1aa60623…07ac54  FAIL — three disallowed `init-value` properties
    CANDIDATE r1    dc5f204d…1cd55   PASS, zero errors

r1 carries that exact `data_exchange` action and passed clean. The rule was a false
positive; the action was never the defect, and no backend change is required. Meta emits
the action error only while the invalid `init-value` properties are present. Why is not
asserted here — we do not know.

The lesson these tests now carry: a local checker may only encode what Meta demonstrably
did, and a rule that condemns a Flow known to be working in production is far more likely
to be wrong than the production Flow is.
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
R2 = FLOWS / "CANDIDATE_v7.4-prefill-r2.json"

# Owner-attested Ejecutar results — the authority this suite answers to.
META_ACCEPTED = (PUBLISHED, R1)


def load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def errors(p, precedent=None):
    return [f for f in check(load(p), load(precedent) if precedent else None) if f[0] == "ERROR"]


class ValidatorMatchesMeta(unittest.TestCase):
    """The validator must agree with every Ejecutar result we hold."""

    def test_gbr2_01_published_asset_passes(self):
        self.assertEqual(errors(PUBLISHED), [],
                         "Meta accepted the published asset with zero errors")

    def test_gbr2_02_r1_passes(self):
        self.assertEqual(errors(R1), [], "Meta accepted r1 with zero errors")

    def test_gbr2_03_r2_passes(self):
        self.assertEqual(errors(R2), [])

    def test_gbr2_04_rejected_candidate_fails_for_exactly_three_init_values(self):
        found = errors(REJECTED)
        self.assertEqual(len(found), 3, "the real defect was three init-value properties")
        self.assertEqual(
            sorted(w for _, w, _ in found),
            sorted([".screens[0].layout.children[3].children[0].init-value",
                    ".screens[0].layout.children[3].children[1].init-value",
                    ".screens[1].layout.children[1].children[0].init-value"]))
        for _, _, msg in found:
            self.assertIn("init-value", msg)

    def test_gbr2_05_no_false_rejection_of_the_dropdown_action(self):
        """The regression that cost a fabricated blocker and a proposed backend milestone."""
        for asset in META_ACCEPTED + (R2,):
            with self.subTest(asset=asset.name):
                for _, where, msg in errors(asset):
                    self.assertNotIn("on-select-action", where)
                    self.assertNotIn("update_data", msg)

    def test_gbr2_06_the_action_is_still_data_exchange_everywhere(self):
        for asset in (PUBLISHED, R1, R2):
            with self.subTest(asset=asset.name):
                dd = load(asset)["screens"][0]["layout"]["children"][3]["children"][0]
                self.assertEqual(dd["on-select-action"]["name"], "data_exchange")
                self.assertEqual(dd["on-select-action"]["payload"]["trigger"], "date_selected")


class PreviewFixture(unittest.TestCase):
    """R2 exists so the static preview can be walked end to end."""

    def setUp(self):
        self.ap = load(R2)["screens"][0]["data"]

    def test_gbr2_07_time_selector_is_enabled_in_preview(self):
        self.assertIs(self.ap["is_time_enabled"]["__example__"], True,
                      "a disabled Horario dropdown is what blocked traversal in R1")

    def test_gbr2_08_date_selector_is_enabled_in_preview(self):
        self.assertIs(self.ap["is_date_enabled"]["__example__"], True)

    def test_gbr2_09_preview_offers_real_choices(self):
        self.assertGreater(len(self.ap["date"]["__example__"]), 0)
        self.assertGreater(len(self.ap["time"]["__example__"]), 0)
        for item in self.ap["date"]["__example__"] + self.ap["time"]["__example__"]:
            self.assertIn("id", item)
            self.assertIn("title", item)

    def test_gbr2_10_appointment_footer_navigates_client_side(self):
        """Nothing about traversal to DETAILS depends on a backend round trip."""
        foot = [c for c in load(R2)["screens"][0]["layout"]["children"][3]["children"]
                if c.get("type") == "Footer"][0]
        self.assertEqual(foot["on-click-action"]["name"], "navigate")
        self.assertEqual(foot["on-click-action"]["next"], {"type": "screen", "name": "DETAILS"})


class RuntimeSemanticsUnchanged(unittest.TestCase):
    """R2 may differ from R1 in preview fixtures and nothing else."""

    @staticmethod
    def flat(o, p=""):
        out = {}
        if isinstance(o, dict):
            for k, v in o.items():
                out.update(RuntimeSemanticsUnchanged.flat(v, f"{p}.{k}"))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                out.update(RuntimeSemanticsUnchanged.flat(v, f"{p}[{i}]"))
        else:
            out[p] = o
        return out

    def test_gbr2_11_only_example_fixtures_differ(self):
        a, b = self.flat(load(R1)), self.flat(load(R2))
        self.assertEqual(set(a), set(b), "R2 added or removed a key")
        changed = [k for k in a if a[k] != b[k]]
        self.assertEqual(changed, [".screens[0].data.is_time_enabled.__example__"])

    def test_gbr2_12_routing_screens_and_version_unchanged(self):
        r2, pub = load(R2), load(PUBLISHED)
        self.assertEqual(r2.get("version"), "7.3")
        self.assertEqual(r2.get("data_api_version"), pub.get("data_api_version"))
        self.assertEqual(r2.get("routing_model"), pub.get("routing_model"))
        self.assertEqual([s["id"] for s in r2["screens"]], [s["id"] for s in pub["screens"]])
        self.assertEqual([s["id"] for s in r2["screens"] if s.get("terminal")], ["SUMMARY"])

    def test_gbr2_13_no_init_value_anywhere(self):
        for asset in (R1, R2):
            with self.subTest(asset=asset.name):
                self.assertEqual(asset.read_text(encoding="utf-8").count("init-value"), 0)

    def test_gbr2_14_submission_contract_unchanged(self):
        def payloads(d):
            out = []
            def w(n):
                if isinstance(n, dict):
                    if n.get("name") == "data_exchange" and isinstance(n.get("payload"), dict):
                        out.append(tuple(sorted(n["payload"])))
                    for v in n.values(): w(v)
                elif isinstance(n, list):
                    for v in n: w(v)
            w(d); return sorted(out)
        self.assertEqual(payloads(load(R2)), payloads(load(R1)))

    def test_gbr2_15_every_data_reference_resolves(self):
        import re
        for s in load(R2)["screens"]:
            declared = set((s.get("data") or {}).keys())
            refs = set(re.findall(r"\$\{data\.([A-Za-z0-9_]+)\}", json.dumps(s)))
            self.assertEqual(refs - declared, set(), f"{s['id']} references undeclared data")

    def test_gbr2_16_backend_authority_preserved(self):
        r2 = load(R2)
        ap = r2["screens"][0]["data"]
        for k in ("booking_token", "vehicle_summary", "location_summary",
                  "date", "time", "is_date_enabled", "is_time_enabled", "contact_phone"):
            self.assertIn(k, ap, "availability and identity must still come from the backend")
        det = r2["screens"][1]["layout"]["children"][1]["children"]
        self.assertNotIn("phone", [c.get("name") for c in det if c.get("type") == "TextInput"],
                         "the phone must stay read-only; wa_id is the canonical identity")

    def test_gbr2_17_no_component_type_without_meta_precedent(self):
        def types(d):
            out = set()
            def w(n):
                if isinstance(n, dict):
                    if isinstance(n.get("type"), str): out.add(n["type"])
                    for v in n.values(): w(v)
                elif isinstance(n, list):
                    for v in n: w(v)
            w(d); return out
        self.assertEqual(types(load(R2)) - types(load(PUBLISHED)), set())

    def test_gbr2_18_no_secret_or_customer_pii(self):
        txt = R2.read_text(encoding="utf-8")
        for marker in ("EA" + "A", "AIz" + "a", "sk" + "-", "-----BEG" + "IN"):
            self.assertNotIn(marker, txt)
        import re
        real = [n for n in re.findall(r"\b549\d{10}\b", txt) if n != "5491100000000"]
        self.assertEqual(real, [])

    def test_gbr2_19_evidence_assets_are_preserved(self):
        for p in (PUBLISHED, REJECTED, R1):
            self.assertTrue(p.exists(), f"{p.name} must remain as evidence")
        self.assertNotEqual(R1.read_bytes(), R2.read_bytes())


class LocalIsNotMeta(unittest.TestCase):

    def test_gbr2_20_the_validator_still_says_so_out_loud(self):
        src = (ROOT / "scripts" / "validate_flow_json.py").read_text(encoding="utf-8")
        self.assertIn("Local PASS is necessary and never sufficient", src)
        self.assertIn("Meta Ejecutar remains the authority", src)
        self.assertIn("FALSE POSITIVE", src, "the corrected rule must stay documented")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
