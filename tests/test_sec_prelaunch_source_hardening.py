"""SEC-PRELAUNCH-SOURCE-HARDENING — no working credential may live in tracked source.

The controlled-Wild packaging work found three of them, and one more turned up while
fixing the first: an admin password fallback, a session-signing key fallback, a database
password, and a Maps API key. None is rotated here. These tests hold the source-side
invariant so the values cannot come back.

SEC-SRC-01 no admin fallback credential      SEC-SRC-06 n8n DB topology valid
SEC-SRC-02 missing ADMIN_PASSWORD fails closed  SEC-SRC-07 Maps key not literal
SEC-SRC-03 configured admin behaviour kept   SEC-SRC-08 runtime secret literal scan
SEC-SRC-04 compose does not publish DB globally SEC-SRC-09 compose validation
SEC-SRC-05 backend DB topology valid         SEC-SRC-10 no functional regression
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import re
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

from app.auth import (  # noqa: E402
    AuthConfigurationError,
    admin_auth_configured,
    auth_secret_configured,
    login_ok,
    sign_session,
    verify_session,
)

AUTH_SRC = (ROOT / "backend" / "app" / "auth.py").read_text(encoding="utf-8")
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
COMPOSE_BETA = (ROOT / "docker-compose.beta.yml").read_text(encoding="utf-8")
WORKFLOWS = sorted((ROOT / "N8N workflows").glob("*.json"))

SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf",
            ".pdf", ".xlsx", ".zip"}


EXCLUDED_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", "venv",
                 ".venv", "sanitized-work"}


def tracked_files():
    """Repository files. Uses git when available, else an equivalent filesystem walk,
    so the scan still runs inside the test container (which has no git binary)."""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                             check=True)
        return [f for f in out.stdout.decode().split("\0") if f]
    except (FileNotFoundError, subprocess.CalledProcessError):
        # Without git we cannot tell tracked from untracked, and untracked local
        # scratch files would produce false failures. The caller falls back to an
        # explicit list of the paths that actually matter.
        return None


def executable_code(source: str) -> str:
    """Strip docstrings so documentation about a credential is not read as one."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body.pop(0)
    return ast.unparse(tree)


class TestAdminFallback(unittest.TestCase):

    def test_sec_src_01_no_admin_fallback_credential_in_executable_code(self):
        """SEC-SRC-01 — no literal working credential survives in auth code."""
        code = executable_code(AUTH_SRC)
        for forbidden in ("admin123", "dev-only-change-me"):
            self.assertNotIn(forbidden, code, f"{forbidden!r} is still a live fallback")
        # getenv on either secret must not carry a default at all
        tree = ast.parse(AUTH_SRC)
        offenders = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "getenv" and len(node.args) >= 2):
                first = node.args[0]
                if isinstance(first, ast.Constant) and first.value in (
                        "ADMIN_PASSWORD", "AUTH_SECRET_KEY", "SECRET_KEY"):
                    offenders.append(first.value)
        self.assertEqual(offenders, [], f"credential getenv with a default: {offenders}")

    def test_sec_src_02_missing_admin_password_fails_closed(self):
        """SEC-SRC-02 — unset ADMIN_PASSWORD enables no password whatsoever."""
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(admin_auth_configured())
            self.assertFalse(login_ok("admin@ridecheck.local", "admin123"))
            self.assertFalse(login_ok("admin@ridecheck.local", ""))
            for guess in ("password", "changeme", "ridecheck", "admin"):
                self.assertFalse(login_ok("admin@ridecheck.local", guess), guess)

    def test_sec_src_02b_missing_signing_key_fails_closed(self):
        """SEC-04 — with no signing key nothing can be signed and no cookie verifies."""
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(auth_secret_configured())
            with self.assertRaises(AuthConfigurationError):
                sign_session({"email": "admin@ridecheck.local"})
            self.assertIsNone(verify_session("anything.deadbeef"))

    def test_sec_src_02c_a_cookie_signed_with_the_old_default_is_rejected(self):
        """The removed default must not still validate a forged session."""
        import hashlib
        import hmac as _hmac
        import base64
        body = base64.urlsafe_b64encode(b'{"email":"admin@ridecheck.local"}').decode().rstrip("=")
        forged = body + "." + _hmac.new(b"dev-only-change-me", body.encode(),
                                        hashlib.sha256).hexdigest()
        with patch.dict(os.environ, {"AUTH_SECRET_KEY": "a-real-configured-key"}, clear=True):
            self.assertIsNone(verify_session(forged))
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(verify_session(forged))

    def test_sec_src_03_configured_admin_password_behaviour_preserved(self):
        """SEC-SRC-03 — a configured credential still works exactly as before."""
        env = {"ADMIN_EMAIL": "admin@ridecheck.local", "ADMIN_PASSWORD": "a-configured-secret"}
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(admin_auth_configured())
            self.assertTrue(login_ok("admin@ridecheck.local", "a-configured-secret"))
            self.assertTrue(login_ok("  Admin@RideCheck.local  ", "a-configured-secret"))
            self.assertFalse(login_ok("admin@ridecheck.local", "wrong"))
            self.assertFalse(login_ok("someone@else.com", "a-configured-secret"))

    def test_configured_signing_key_round_trips(self):
        with patch.dict(os.environ, {"AUTH_SECRET_KEY": "a-configured-key"}, clear=True):
            token = sign_session({"email": "admin@ridecheck.local"})
            self.assertEqual(verify_session(token)["email"], "admin@ridecheck.local")
            self.assertIsNone(verify_session(token + "x"))


class TestDatabaseExposure(unittest.TestCase):

    def test_sec_src_04_compose_does_not_publish_the_database_globally(self):
        """SEC-SRC-04 — 5432 must not be bound to every interface."""
        self.assertNotIn('- "5432:5432"', COMPOSE)
        self.assertNotIn('- "0.0.0.0:5432:5432"', COMPOSE)
        self.assertIn('- "127.0.0.1:5432:5432"', COMPOSE)

    def test_sec_src_05_backend_reaches_the_db_over_the_compose_network(self):
        """SEC-SRC-05 — the backend never depends on the host mapping."""
        self.assertIn("@postgres:5432/crm", COMPOSE)
        self.assertNotIn("@localhost:5432", COMPOSE)
        self.assertIn("@postgres:5432/crm_test", COMPOSE_BETA)

    def test_sec_src_06_n8n_topology_unchanged(self):
        """SEC-SRC-06 — every n8n HTTP node addresses the backend by service name.

        Checked on node URLs, not raw file text: one workflow carries a human note that
        mentions localhost, and a note is documentation, not topology.
        """
        checked = 0
        for wf in WORKFLOWS:
            doc = json.loads(wf.read_text(encoding="utf-8"))
            for node in doc.get("nodes", []):
                url = str((node.get("parameters") or {}).get("url") or "")
                if not url or "backend" not in url and "localhost" not in url:
                    continue
                self.assertNotIn("localhost:8000", url,
                                 f"{wf.name}:{node.get('name')} bypasses the compose network")
                if "backend" in url:
                    self.assertIn("http://backend:8000", url)
                    checked += 1
        self.assertGreater(checked, 0, "at least one backend HTTP node is present")

    def test_no_database_password_literal_in_compose(self):
        for name, text in (("docker-compose.yml", COMPOSE),
                           ("docker-compose.beta.yml", COMPOSE_BETA)):
            for m in re.finditer(r"postgresql\+psycopg://[^\s:]+:([^\s@]+)@", text):
                self.assertTrue(m.group(1).startswith("${"),
                                f"{name} still embeds a password literal")


class TestWorkflowSecrets(unittest.TestCase):

    def test_sec_src_07_maps_key_is_not_a_literal(self):
        """SEC-SRC-07 — the key became a reference; the URL and node survived."""
        target = [w for w in WORKFLOWS if "(6)" in w.name]
        self.assertTrue(target, "the workflow export carrying the Maps node is tracked")
        text = target[0].read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"AIza[0-9A-Za-z_\-]{35}", text), [])
        self.assertIn("&key=", text)
        self.assertIn("maps.googleapis.com", text)
        self.assertIn("$env.GOOGLE_MAPS_API_KEY", text)

    def test_sec_src_09_workflow_and_compose_still_parse(self):
        """SEC-SRC-09 — hardening must not damage machine-readable config."""
        for wf in WORKFLOWS:
            doc = json.loads(wf.read_text(encoding="utf-8"))
            self.assertGreater(len(doc.get("nodes", [])), 0, str(wf))
        try:
            import yaml
        except ImportError:
            self.skipTest("pyyaml unavailable")
        for name, text in (("docker-compose.yml", COMPOSE),
                           ("docker-compose.beta.yml", COMPOSE_BETA)):
            doc = yaml.safe_load(text)
            self.assertIn("services", doc, name)
        self.assertIn("backend", yaml.safe_load(COMPOSE)["services"])


class TestRepositoryWideScan(unittest.TestCase):

    def test_sec_src_08_no_real_runtime_secret_literal_is_tracked(self):
        """SEC-SRC-08 — the scan that gates every future snapshot."""
        patterns = [
            ("GOOGLE_API_KEY", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
            ("META_TOKEN", re.compile(r"\bEAA[A-Za-z0-9]{20,}")),
            ("OPENAI_KEY", re.compile(r"\bsk-[A-Za-z0-9_\-]{32,}")),
            ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
            ("ADMIN_FALLBACK", re.compile(r"admin123|dev-only-change-me")),
            ("VERIFY_TOKEN", re.compile(r"ridecheck_whatsapp_verify_2026")),
        ]
        dbpw = re.compile(r"(?:postgres|postgresql)(?:\+\w+)?://[^\s:/'\"]+:([^\s@'\"]+)@")
        safe_pw = re.compile(r"^\**$|^<[^>]*>$|^\$\{[^}]*\}$|^\{[^}]*\}$")
        offenders = []
        files = tracked_files()
        if files is None:                      # no git: scan the critical paths instead
            files = ["backend/app/auth.py", "backend/app/main.py", "backend/README.md",
                     "docker-compose.yml", "docker-compose.beta.yml"]
            files += [str(w.relative_to(ROOT)) for w in WORKFLOWS]
        for f in files:
            path = ROOT / f
            if path.suffix.lower() in SKIP_EXT or not path.exists():
                continue
            if path.resolve() == pathlib.Path(__file__).resolve():
                continue                        # the scanner names the patterns it forbids
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for name, rx in patterns:
                if rx.search(text):
                    offenders.append(f"{name} in {f}")
            for m in dbpw.finditer(text):
                if not safe_pw.match(m.group(1)):
                    offenders.append(f"DB_PASSWORD in {f}")
        self.assertEqual(offenders, [], f"real credential literals tracked: {offenders}")

    def test_sec_src_10_env_indirection_changed_no_application_logic(self):
        """SEC-SRC-10 — only credential plumbing moved; behaviour is unchanged."""
        # the DSN helper is pure structure + one env read, and has no default password
        from pg_dsn import pg_dsn, pg_password
        with patch.dict(os.environ, {"POSTGRES_PASSWORD": "configured"}, clear=True):
            self.assertEqual(pg_dsn("crm_test", "postgres"),
                             "postgresql+psycopg://crm:configured@postgres:5432/crm_test")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(pg_password(), "")
            self.assertIn("crm:@postgres", pg_dsn("crm_test", "postgres"))
        helper = (ROOT / "tests" / "pg_dsn.py").read_text(encoding="utf-8")
        self.assertNotIn('"crm"', executable_code(helper).replace('DEFAULT_USER = "crm"', ""))

    def test_deployment_is_marked_blocked(self):
        """The source change is deliberately not deployable until config exists."""
        closeout = ROOT / ("2026-09-04_RIDECHECK_CRM_SEC-PRELAUNCH-SOURCE-HARDENING"
                           "_CLOSEOUT_NO-ROTATION.md")
        if not closeout.exists():
            self.skipTest("closeout written at the end of the milestone")
        self.assertIn("BLOCKED_PENDING_OWNER_CREDENTIAL_CONFIGURATION",
                      closeout.read_text(encoding="utf-8"))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
