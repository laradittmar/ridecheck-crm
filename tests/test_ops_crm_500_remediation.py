"""OPS-CRM-500 — an unconfigured server must say so, not crash.

The CRM login page signs its own CAPTCHA token with the session key. When
SEC-PRELAUNCH-SOURCE-HARDENING made that key mandatory, `verify_session` and the POST
handler were guarded and the GET *render* path was not — so a missing key took the whole
operator UI down with a stack trace, and it stayed down for three days.

Failing closed is correct and stays. Failing incomprehensibly was the defect.

OPS500-01 login renders with a key      OPS500-04 old default secret cannot authenticate
OPS500-02 missing key -> 503, not 500   OPS500-05 configured admin password works
OPS500-03 POST fails closed             OPS500-06 wrong admin password fails
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
for _mod in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = sqlalchemy.JSON      # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON         # type: ignore[attr-defined]

from fastapi.testclient import TestClient  # noqa: E402

from app.auth import (  # noqa: E402
    AuthConfigurationError,
    login_ok,
    sign_session,
    verify_session,
)
from app.main import app  # noqa: E402

CONFIGURED = {"AUTH_SECRET_KEY": "a-real-configured-session-key",
              "ADMIN_EMAIL": "admin@ridecheck.local",
              "ADMIN_PASSWORD": "a-real-configured-admin-password"}


class TestLoginRender(unittest.TestCase):

    def test_ops500_01_login_renders_when_the_key_is_configured(self):
        """OPS500-01 — the normal path still works."""
        # TestClient is NOT used as a context manager on purpose: the app's startup event
        # issues PostgreSQL-specific DDL, which is irrelevant to rendering a login page.
        with patch.dict(os.environ, CONFIGURED, clear=False):
            response = TestClient(app).get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn("captcha", response.text.lower())

    def test_ops500_02_missing_key_returns_503_not_500(self):
        """OPS500-02 — the exact failure that took the CRM down."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("AUTH_SECRET_KEY", "SECRET_KEY")}
        with patch.dict(os.environ, env, clear=True):
            response = TestClient(app, raise_server_exceptions=False).get("/login")
        self.assertNotEqual(response.status_code, 500, "a 500 is what we are fixing")
        self.assertEqual(response.status_code, 503)
        self.assertIn("AUTH_SECRET_KEY", response.text)

    def test_ops500_03_post_login_fails_closed_without_a_key(self):
        """OPS500-03 — no key, no session, whatever the credentials."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("AUTH_SECRET_KEY", "SECRET_KEY")}
        env["ADMIN_PASSWORD"] = "a-real-configured-admin-password"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(AuthConfigurationError):
                sign_session({"email": "admin@ridecheck.local"})
            self.assertIsNone(verify_session("anything.deadbeef"))


class TestCredentialSafety(unittest.TestCase):

    def test_ops500_04_the_removed_default_secret_cannot_authenticate(self):
        """OPS500-04 — a cookie signed with the old literal is rejected."""
        import base64
        import hashlib
        import hmac as _hmac
        body = base64.urlsafe_b64encode(
            b'{"email":"admin@ridecheck.local"}').decode().rstrip("=")
        forged = body + "." + _hmac.new(b"dev-only-change-me", body.encode(),
                                        hashlib.sha256).hexdigest()
        with patch.dict(os.environ, CONFIGURED, clear=False):
            self.assertIsNone(verify_session(forged))

    def test_ops500_05_and_06_configured_admin_password_behaviour(self):
        """OPS500-05/06 — the configured value works; anything else does not."""
        with patch.dict(os.environ, CONFIGURED, clear=False):
            self.assertTrue(login_ok("admin@ridecheck.local",
                                     "a-real-configured-admin-password"))
            self.assertFalse(login_ok("admin@ridecheck.local", "wrong"))
            self.assertFalse(login_ok("admin@ridecheck.local", "admin123"),
                             "the removed default must never authenticate")
            self.assertFalse(login_ok("someone@else.com",
                                      "a-real-configured-admin-password"))


class TestDeploymentPreflight(unittest.TestCase):

    SCRIPT = ROOT / "scripts" / "preflight_deploy.sh"

    def test_the_preflight_gate_exists_and_is_executable(self):
        self.assertTrue(self.SCRIPT.exists())
        self.assertTrue(os.access(self.SCRIPT, os.X_OK))
        body = self.SCRIPT.read_text(encoding="utf-8")
        for key in ("AUTH_SECRET_KEY", "ADMIN_PASSWORD", "POSTGRES_PASSWORD"):
            self.assertIn(key, body)

    def test_the_preflight_gate_stops_a_deploy_when_a_value_is_missing(self):
        """The mechanism that a sentence in a closeout was not."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bad = pathlib.Path(tmp) / "bad.env"
            bad.write_text("AUTH_SECRET_KEY=x\nADMIN_PASSWORD=\n", encoding="utf-8")
            result = subprocess.run(["bash", str(self.SCRIPT), str(bad)],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0, "a missing value must stop the deploy")
            self.assertIn("ADMIN_PASSWORD", result.stderr)
            self.assertNotIn("AUTH_SECRET_KEY=x", result.stderr, "values are never printed")

            # Derive the required list FROM THE SCRIPT rather than restating it: the
            # first version of this test hardcoded six names, the script later grew to
            # eight, and the fixture silently went stale.
            lines = self.SCRIPT.read_text(encoding="utf-8").splitlines()
            start = next(i for i, l in enumerate(lines) if l.startswith("REQUIRED=("))
            required = []
            for line in lines[start + 1:]:
                if line.strip() == ")":
                    break
                name = line.split("#")[0].strip()
                if name:
                    required.append(name)
            self.assertGreaterEqual(len(required), 6)
            good = pathlib.Path(tmp) / "good.env"
            good.write_text("\n".join(f"{k}=value" for k in required), encoding="utf-8")
            ok = subprocess.run(["bash", str(self.SCRIPT), str(good)],
                                capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stderr)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
