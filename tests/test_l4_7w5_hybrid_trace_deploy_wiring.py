"""L4.7W5-HYBRID-TRACE-DEPLOY-WIRING — the deployment path itself, under test.

The controlled-deployment preflight stopped because `HYBRID_TRACE_ENABLED` reached no
container: it was in neither compose file, there was no `env_file`, and it was not in
`.env`. Because `Settings` defaults it to `false`, that gap fails *silently* — the backend
comes up healthy, the checklist goes green, and the trace table stays empty. The same class
of gap cost this project an outage once already, which is why the compose file itself
carries the warning:

    # OPS-CRM-500: the hardened auth code requires these, and .env presence is NOT the
    # same thing as container environment — compose only injects what is declared here.

These tests keep the declaration declared, and keep it safe: default false, only the
backend, and never coupled to outbound.

WIRE-01..04  the reviewed image pin
WIRE-05..10  the trace mapping: declared, defaulted false, backend-only
WIRE-11..13  independence from outbound
"""
from __future__ import annotations

import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
BETA = ROOT / "docker-compose.beta.yml"
BASE = ROOT / "docker-compose.yml"

REVIEWED_IMAGE = "ridecheck-crm-backend:w5g3-1-840bce0"
#: Superseded pins. Every one stays on disk as a rollback target; none may be what deploys.
#: Keeping the whole chain here means a revert to any older pin also fails the test.
SUPERSEDED_IMAGES = ("ridecheck-crm-backend:w5tracerows-355c0ae",
                     "ridecheck-crm-backend:w5noreply-a7a6413",
                     "ridecheck-crm-backend:w5labeltruth-0407bab",
                     "ridecheck-crm-backend:w5hybridtrace-2f8acc8",
                     "ridecheck-crm-backend:w5f7g-ce978a2")
TRACE_KEY = "HYBRID_TRACE_ENABLED"
TRACE_MAPPING = '"${HYBRID_TRACE_ENABLED:-false}"'


def compose(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def declarations(path: pathlib.Path, key: str) -> list[str]:
    """Non-comment lines declaring `key` — a commented example is not a declaration."""
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if key in line and not line.strip().startswith("#")]


class ReviewedImagePin(unittest.TestCase):

    def test_wire_01_the_override_pins_the_reviewed_image(self):
        self.assertEqual(compose(BETA)["services"]["backend"]["image"], REVIEWED_IMAGE)

    def test_wire_02_the_tag_names_the_reviewed_commit(self):
        """The tag carries the short SHA it was built from, so a deployed container can be
        traced back to a commit without trusting a mutable label."""
        self.assertTrue(REVIEWED_IMAGE.endswith("-840bce0"), REVIEWED_IMAGE)

    def test_wire_03_exactly_one_backend_image_declaration(self):
        lines = declarations(BETA, "image: ridecheck-crm-backend")
        self.assertEqual(len(lines), 1, f"expected one backend image pin, got {lines}")

    def test_wire_04_no_superseded_image_is_still_pinned(self):
        """They remain rollback targets on disk; none may still be what deploys."""
        pinned = [l.split("image:")[-1].strip() for l in declarations(BETA, "image:")]
        for image in SUPERSEDED_IMAGES:
            with self.subTest(image=image):
                self.assertNotIn(image, pinned)


class TraceMapping(unittest.TestCase):

    def setUp(self):
        self.beta = compose(BETA)
        self.backend_env = self.beta["services"]["backend"]["environment"]

    def test_wire_05_the_backend_declares_the_flag(self):
        self.assertIn(TRACE_KEY, self.backend_env,
                      "compose only injects what the service declares")

    def test_wire_06_it_is_interpolated_with_a_false_default(self):
        self.assertEqual(self.backend_env[TRACE_KEY], "${HYBRID_TRACE_ENABLED:-false}")

    def test_wire_07_it_is_not_committed_as_true(self):
        """Activation belongs to the deployment checkpoint, not to version control."""
        value = str(self.backend_env[TRACE_KEY]).strip().strip('"').lower()
        self.assertNotEqual(value, "true")
        for line in declarations(BETA, TRACE_KEY):
            self.assertNotIn('"true"', line)

    def test_wire_08_exactly_one_declaration(self):
        self.assertEqual(len(declarations(BETA, TRACE_KEY)), 1)

    def test_wire_09_postgres_and_n8n_never_receive_it(self):
        for service in ("postgres", "n8n"):
            env = (self.beta["services"].get(service) or {}).get("environment") or {}
            with self.subTest(service=service):
                self.assertNotIn(TRACE_KEY, env)

    def test_wire_10_the_production_shaped_base_does_not_declare_it(self):
        """Every crm_test-only flag lives in the override alone; this one is no different.

        The base compose points `DATABASE_URL` at `crm`. Declaring an observability flag
        there would wire capture into the production-shaped layer.
        """
        self.assertEqual(declarations(BASE, TRACE_KEY), [])
        for companion in ("SHADOW_UNDERSTAND_ENABLED", "SEMANTIC_SAME_TURN_ENABLED",
                          "RECONCILER_VEHICLE_AUTHORITY_ENABLED"):
            with self.subTest(flag=companion):
                self.assertEqual(declarations(BASE, companion), [],
                                 "the convention this follows has changed; re-examine")
                self.assertEqual(len(declarations(BETA, companion)), 1)

    def test_wire_11_no_env_file_was_introduced(self):
        """An `env_file` would inject whatever the host happens to hold, unreviewed."""
        for path in (BETA, BASE):
            for service in compose(path).get("services", {}).values():
                with self.subTest(path=path.name):
                    self.assertNotIn("env_file", service)


class OutboundIndependence(unittest.TestCase):

    def setUp(self):
        self.backend_env = compose(BETA)["services"]["backend"]["environment"]

    def test_wire_12_outbound_is_unchanged_and_defaults_false(self):
        self.assertEqual(self.backend_env["OUTBOUND_ENABLED"],
                         "${BETA_OUTBOUND_ENABLED:-false}")

    def test_wire_13_the_two_flags_share_no_input_variable(self):
        """Arming capture must be incapable of arming a send, by construction."""
        trace = str(self.backend_env[TRACE_KEY])
        outbound = str(self.backend_env["OUTBOUND_ENABLED"])
        self.assertIn("HYBRID_TRACE_ENABLED", trace)
        self.assertIn("BETA_OUTBOUND_ENABLED", outbound)
        self.assertNotIn("HYBRID_TRACE_ENABLED", outbound)
        self.assertNotIn("BETA_OUTBOUND_ENABLED", trace)


class SettingsFailSafe(unittest.TestCase):
    """The second half of the guarantee: what the application does with the value."""

    def test_wire_14_only_true_arms_capture(self):
        import os
        from app.settings import get_settings
        armed = []
        for raw in ("true", "TRUE", "True", "  true  ", "false", "FALSE", "0", "1",
                    "yes", "on", "si", "truthy", "", "tru e", "None", "null"):
            os.environ[TRACE_KEY] = raw
            get_settings.cache_clear()
            if get_settings().hybrid_trace_enabled is True:
                armed.append(raw)
        os.environ.pop(TRACE_KEY, None)
        get_settings.cache_clear()
        self.assertEqual(armed, ["true", "TRUE", "True", "  true  "],
                         "an unexpected value arms capture")
        self.assertFalse(get_settings().hybrid_trace_enabled, "unset must be off")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
