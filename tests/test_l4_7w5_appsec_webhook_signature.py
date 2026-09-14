"""L4.7W5-APPSEC — webhook signature verification must fail closed.

`_verify_signature` returned True when WHATSAPP_APP_SECRET was unset — a "dev mode" skip.
The intent was convenience; the effect was that forgetting one .env line silently turned
webhook authentication off, announced by a single INFO line. That is the OPS-CRM-500 shape:
a security control disabled because a variable never reached the container and no gate
noticed.

No real secret is used or needed here — every case signs with a local test value.

SIG-01 valid signature over the raw body     SIG-04 wrong secret rejected
SIG-02 missing header rejected               SIG-05 body tampering rejected
SIG-03 malformed header rejected             SIG-06 absent secret FAILS CLOSED
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import pathlib
import sys
import types
import unittest
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

from app.routes.whatsapp import _verify_signature

TEST_SECRET = "local-test-secret-not-a-real-app-secret"
BODY = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()


def sign(body: bytes, secret: str = TEST_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestSignatureVerification(unittest.TestCase):

    def test_sig_01_a_valid_signature_over_the_raw_body_is_accepted(self):
        self.assertTrue(_verify_signature(BODY, sign(BODY), TEST_SECRET))

    def test_sig_01b_the_signature_covers_the_raw_bytes_not_reparsed_json(self):
        """Whitespace changes the bytes, so it must change the verdict — proof the digest is
        over the body as received, not over a re-serialised object."""
        reserialised = json.dumps(json.loads(BODY), indent=2).encode()
        self.assertNotEqual(reserialised, BODY)
        self.assertFalse(_verify_signature(reserialised, sign(BODY), TEST_SECRET))
        self.assertTrue(_verify_signature(reserialised, sign(reserialised), TEST_SECRET))

    def test_sig_02_a_missing_header_is_rejected(self):
        self.assertFalse(_verify_signature(BODY, None, TEST_SECRET))
        self.assertFalse(_verify_signature(BODY, "", TEST_SECRET))

    def test_sig_03_a_malformed_header_is_rejected(self):
        for header in ("garbage", "sha256=", "=abc", "sha1=" + "0" * 40,
                       "sha256", "SHA256"):
            with self.subTest(header=header):
                self.assertFalse(_verify_signature(BODY, header, TEST_SECRET))

    def test_sha256_prefix_is_case_insensitive_as_meta_sends_it(self):
        valid = sign(BODY)
        self.assertTrue(_verify_signature(BODY, valid.replace("sha256=", "SHA256="), TEST_SECRET))

    def test_sig_04_a_signature_from_the_wrong_secret_is_rejected(self):
        self.assertFalse(_verify_signature(BODY, sign(BODY, "a-different-secret"), TEST_SECRET))

    def test_sig_05_tampering_with_the_body_is_rejected(self):
        tampered = BODY.replace(b"[]", b'[{"injected": true}]')
        self.assertFalse(_verify_signature(tampered, sign(BODY), TEST_SECRET))

    def test_a_constant_time_comparison_is_used(self):
        src = (ROOT / "backend" / "app" / "routes"
               / "whatsapp.py").read_text(encoding="utf-8-sig")
        self.assertIn("hmac.compare_digest", src)
        self.assertNotIn("expected_sig == provided_sig", src)


class TestFailsClosed(unittest.TestCase):

    def test_sig_06_an_absent_secret_rejects_every_request(self):
        """SIG-06 — the defect. An omitted .env line used to disable authentication."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WHATSAPP_WEBHOOK_ALLOW_UNSIGNED", None)
            self.assertFalse(_verify_signature(BODY, sign(BODY), ""))
            self.assertFalse(_verify_signature(BODY, None, ""))
            self.assertFalse(_verify_signature(BODY, sign(BODY), "   "))

    def test_the_escape_hatch_must_be_set_deliberately_and_is_loud(self):
        """Unsigned acceptance is possible only by explicit opt-in, and never quietly."""
        with patch.dict(os.environ, {"WHATSAPP_WEBHOOK_ALLOW_UNSIGNED": "true"}):
            with self.assertLogs("app.routes.whatsapp", level="WARNING") as logs:
                self.assertTrue(_verify_signature(BODY, None, ""))
            self.assertTrue(any("NOT authenticated" in m for m in logs.output))

    def test_any_value_other_than_true_still_fails_closed(self):
        for value in ("false", "1", "yes", "TRUE ", ""):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"WHATSAPP_WEBHOOK_ALLOW_UNSIGNED": value}):
                    expected = value.strip().lower() == "true"
                    self.assertEqual(_verify_signature(BODY, None, ""), expected)

    def test_the_rejection_is_logged_as_an_error_not_an_info(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WHATSAPP_WEBHOOK_ALLOW_UNSIGNED", None)
            with self.assertLogs("app.routes.whatsapp", level="ERROR") as logs:
                _verify_signature(BODY, sign(BODY), "")
            self.assertTrue(any("FAIL_CLOSED" in m for m in logs.output))


class TestDeploymentGating(unittest.TestCase):

    def test_preflight_requires_the_app_secret(self):
        """Fail-closed means a deploy without the secret takes inbound down. The gate must
        stop that before the container is replaced, not after."""
        # line-scan, not index-slicing: a ")" inside a comment ends the slice early.
        # That exact parser bug bit an earlier milestone reading this same block.
        lines = (ROOT / "scripts" / "preflight_deploy.sh").read_text(
            encoding="utf-8").splitlines()
        start = next(i for i, l in enumerate(lines) if l.startswith("REQUIRED=("))
        end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == ")")
        names = [l.split("#")[0].strip() for l in lines[start + 1:end]]
        self.assertIn("WHATSAPP_APP_SECRET", [n for n in names if n])

    def test_compose_interpolates_from_the_env_file(self):
        """An empty literal in compose overrides .env and silently disables verification —
        which is exactly how this ended up unset in production.

        Both tracked compose files are asserted, the override included. The deployed stack
        is an overlay of a base compose and docker-compose.beta.yml; for a while the
        declaration existed ONLY as an uncommitted edit to the base file in another
        worktree, so any checkout there would have restored the empty literal with nothing
        to catch it. Declaring it in the override that ships with this branch is what makes
        the guarantee survive a checkout, and this test is what keeps it declared.
        """
        for name in ("docker-compose.yml", "docker-compose.beta.yml"):
            compose = ROOT / name
            self.assertTrue(compose.exists(), f"{name} missing from the repository")
            lines = [l for l in compose.read_text(encoding="utf-8").splitlines()
                     if "WHATSAPP_APP_SECRET" in l and not l.strip().startswith("#")]
            self.assertEqual(len(lines), 1, f"{name}: expected exactly one declaration")
            self.assertIn("${WHATSAPP_APP_SECRET}", lines[0], f"{name}: not interpolated")
            self.assertNotIn('WHATSAPP_APP_SECRET: ""', lines[0], f"{name}: empty literal")

    def test_preflight_checks_the_secret_is_declared_in_compose(self):
        """Presence in .env is not presence in the container: compose only injects a
        variable the service declares. Preflight checked .env presence for this key but
        verified compose declaration only for AUTH_SECRET_KEY/ADMIN_PASSWORD — the precise
        gap its own comment warns about."""
        text = (ROOT / "scripts" / "preflight_deploy.sh").read_text(encoding="utf-8")
        # The literal-name loop, not one of the "${array[@]}" expansions.
        loop = [l for l in text.splitlines() if "for key in" in l and "${" not in l]
        self.assertEqual(len(loop), 1, "compose-declaration loop not found")
        self.assertIn("WHATSAPP_APP_SECRET", loop[0])

    def test_no_real_secret_appears_in_this_suite(self):
        text = pathlib.Path(__file__).read_text(encoding="utf-8")
        self.assertIn("not-a-real-app-secret", text)
        # Credential shapes that must never appear in a fixture. Assembled from parts so
        # the literals do not occur in this file — otherwise the check matches its own
        # marker list and fails on itself.
        markers = ["EA" + "A", "AIz" + "a", "sk" + "-", "-----BEG" + "IN"]
        for marker in markers:
            self.assertNotIn(marker, text, f"credential-shaped literal {marker!r}")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
