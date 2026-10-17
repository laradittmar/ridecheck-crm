"""Pre-Wild PW1-R1 (PW1RV-01) — the `/security/*` and `/api/ops/*` operations surfaces.

Every route registered under either prefix requires a valid CRM session or the existing
machine credential (the shared secret, or a direct peer container that is not the bridge
gateway and whose request carries no forwarding headers). Routes with their own stricter
dependency (`require_crm_session`) stay session-only. The guard is independent of
`INTERNAL_API_AUTH_ENABLED`, and its refusal is one generic 401.

The application under test is the real `app.main.app`, middleware included, driven through
`httpx.ASGITransport` with chosen socket peers. The database is a private SQLite engine
seeded with synthetic sentinels only; no value is printed.

  R1-01  census: every registered family route; the required routes are all registered
  R1-02  anonymous → 401 on every route
  R1-03  hostile machine callers → 401 (missing / wrong / malformed secret, malformed
         session, spoofed forwarding headers, gateway peer, proxy-rewritten peer); the
         shared peer check reads the socket, never a header
  R1-04  valid CRM session → admitted on every route, payload unchanged
  R1-05  valid shared secret and trusted direct peer → admitted, except session-only routes
  R1-06  the session-only routes stay session-only
  R1-07  INTERNAL_API_AUTH_ENABLED false / missing / malformed / truthy cannot disable it
  R1-08  refusals disclose nothing: no sentinel, identical bytes for real and unknown paths
  R1-09  a route registered later under either prefix inherits the guard; a root_path
         mount cannot hide the family
  R1-10  /control still works for a session and still redirects without one
  R1-11  webhook verification and Flow Data Exchange are not in the family
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import httpx
import sqlalchemy
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

os.environ.setdefault("DATABASE_URL", "sqlite://")

KEY = "pw1r1-operations-auth-test-signing-key"
SECRET = "pw1r1-machine-secret-0123456789abcdef"
CIDR = "172.18.0.0/16"
PEER = "172.18.0.3"          # n8n's measured bridge address
GATEWAY = "172.18.0.1"       # everything via nginx or the published port
INTERNET = "203.0.113.9"

PHONE = "5491100771177"
NAME = "PW1R1-SENTINEL-NAME"
TEXT = "PW1R1-SENTINEL-TEXT"
WAMID = "wamid.PW1R1SENTINEL0001"
DETAIL = "PW1R1-SENTINEL-DETAIL"
FINGERPRINT = "pw1r1sentinelfingerprint00000000"
SENTINELS = (PHONE, NAME, TEXT, WAMID, DETAIL, FINGERPRINT)

# The routes the audit enumerated (§4.1); each must be registered and guarded.
REQUIRED = (
    "/security/unauthorized-path-events",
    "/security/outbound-ledger",
    "/api/ops/summary",
    "/api/ops/messages",
    "/api/ops/threads",
    "/api/ops/paths",
    "/api/ops/path-registry",
    "/api/ops/critical-events",
    "/api/ops/booking-attention",
)
# Owner policy 7, written down here rather than read off the route table: production code
# must not be the oracle for which routes are stricter.
SESSION_ONLY = frozenset({
    "/api/ops/booking-attention",
    "/api/ops/turns",
    "/api/ops/turn/{turn_id}",
    "/api/ops/offers",
    "/api/ops/offer/{offer_id}",
})
FAMILY = ("/security", "/api/ops")


def _settings(**env):
    """A real Settings object built from a controlled environment."""
    import app.settings as settings_mod
    base = {"INTERNAL_API_AUTH_ENABLED": "true", "INTERNAL_API_SECRET": SECRET,
            "INTERNAL_API_TRUSTED_CIDR": CIDR}
    base.update(env)
    clean = {k: v for k, v in base.items() if v is not None}
    drop = [k for k, v in base.items() if v is None]
    with mock.patch.dict(os.environ, clean):
        for k in drop:
            os.environ.pop(k, None)
        return settings_mod.get_settings.__wrapped__()


class OperationsSurfaceAuth(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import app.main as main
        from app import models
        from app.auth import SESSION_COOKIE
        cls.main = main
        cls.app = main.app
        cls.SESSION_COOKIE = SESSION_COOKIE
        cls.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                   poolclass=sqlalchemy.pool.StaticPool)
        models.Base.metadata.create_all(cls.engine)
        cls.session = Session(cls.engine)
        cls._seed(cls.session, models)

    @staticmethod
    def _seed(s, models):
        now = datetime.now(timezone.utc) - timedelta(minutes=5)
        contact = models.WhatsAppContact(wa_id=PHONE, display_name=NAME, phone=PHONE)
        s.add(contact)
        s.flush()
        thread = models.WhatsAppThread(contact_id=contact.id, last_message_at=now,
                                       latest_inbound_wa_message_id=WAMID)
        s.add(thread)
        s.flush()
        s.add(models.WhatsAppMessage(thread_id=thread.id, wa_message_id=WAMID,
                                     direction="in", timestamp=now, text=TEXT,
                                     status="received"))
        s.add(models.WhatsAppMessage(thread_id=thread.id, wa_message_id=WAMID + "OUT",
                                     direction="out", timestamp=now, text=TEXT,
                                     status="sent", automated=True, path_id="CE_TEXT",
                                     content_fingerprint=FINGERPRINT))
        s.add(models.SecurityEvent(detected_at=now, event_type="PW1R1_SENTINEL_EVENT",
                                   severity="BLOCKER", wamid=WAMID, thread_id=thread.id,
                                   details={"note": DETAIL}))
        s.commit()

    def setUp(self):
        self._install_db_override()
        self.cfg = _settings()
        p = mock.patch.object(self.main, "get_settings", lambda: self.cfg)
        p.start()
        self.addCleanup(p.stop)
        k = mock.patch.dict(os.environ, {"AUTH_SECRET_KEY": KEY, "SECRET_KEY": KEY})
        k.start()
        self.addCleanup(k.stop)

    def _install_db_override(self):
        """Key the override on every `get_db` the family's routes captured (other suites
        swap `app.db` before import), and patch `SessionLocal` for any other reader."""
        import app.db as app_db
        keys = {app_db.get_db}
        for route in self.app.routes:
            if not getattr(route, "path", "").startswith(FAMILY + ("/control",)):
                continue
            for dep in getattr(getattr(route, "dependant", None), "dependencies", []):
                name = getattr(dep.call, "__name__", "")
                if name.endswith("get_db") or name == "_get_db_gen":
                    keys.add(dep.call)
        saved = dict(self.app.dependency_overrides)
        for key in keys:
            self.app.dependency_overrides[key] = lambda: self.session
        self.addCleanup(lambda: (self.app.dependency_overrides.clear(),
                                 self.app.dependency_overrides.update(saved)))
        p = mock.patch.object(app_db, "SessionLocal", lambda: self.session)
        p.start()
        self.addCleanup(p.stop)

    # ── helpers ─────────────────────────────────────────────────────────────

    def _cookie(self, email="operator@example.com", key=KEY):
        from app.auth import build_session, sign_session
        with mock.patch.dict(os.environ, {"AUTH_SECRET_KEY": key, "SECRET_KEY": key}):
            return sign_session(build_session(email))

    def _get(self, path, *, peer=INTERNET, headers=None, cookies=None, app=None,
             method="GET"):
        async def go():
            transport = httpx.ASGITransport(app=app or self.app, client=(peer, 40123))
            async with httpx.AsyncClient(transport=transport, base_url="http://backend",
                                         follow_redirects=False,
                                         cookies=cookies or {}) as client:
                return await client.request(method, path, headers=headers or {})
        return asyncio.run(go())

    def _family_routes(self):
        out = []
        for route in self.app.routes:
            path = getattr(route, "path", "")
            if path.startswith(FAMILY):
                for method in sorted(getattr(route, "methods", None) or {"GET"}):
                    out.append((method, path))
        return out

    @staticmethod
    def _url(path):
        return re.sub(r"\{[^}]+\}", "1", path)

    def _hostile(self):
        """(label, kwargs) for every caller class that must be refused on every route."""
        bad_key_cookie = self._cookie(key="not-the-signing-key")
        return [
            ("anonymous", {}),
            ("missing secret", {"headers": {}}),
            ("wrong secret", {"headers": {"x-internal-auth": SECRET[:-1] + "X"}}),
            ("secret prefix", {"headers": {"x-internal-auth": SECRET[:8]}}),
            ("blank secret", {"headers": {"x-internal-auth": "   "}}),
            ("bearer secret", {"headers": {"authorization": f"Bearer {SECRET}"}}),
            ("secret in other header", {"headers": {"x-internal-secret": SECRET}}),
            ("garbage cookie", {"cookies": {self.SESSION_COOKIE: "garbage"}}),
            ("dotted garbage cookie", {"cookies": {self.SESSION_COOKIE: "e30.deadbeef"}}),
            ("cookie signed with another key",
             {"cookies": {self.SESSION_COOKIE: bad_key_cookie}}),
            ("spoofed X-Forwarded-For", {"headers": {"x-forwarded-for": PEER}}),
            ("spoofed X-Real-IP", {"headers": {"x-real-ip": PEER}}),
            ("spoofed Forwarded", {"headers": {"forwarded": f"for={PEER}"}}),
            ("gateway peer", {"peer": GATEWAY}),
            ("gateway peer spoofing", {"peer": GATEWAY,
                                       "headers": {"x-forwarded-for": PEER}}),
            ("loopback peer", {"peer": "127.0.0.1"}),
            ("peer outside the CIDR", {"peer": "10.0.0.3"}),
            ("trusted peer behind a proxy hop",
             {"peer": PEER, "headers": {"x-forwarded-for": INTERNET}}),
        ]

    # ── R1-01 census ────────────────────────────────────────────────────────

    def test_r1_01_census_covers_every_registered_family_route(self):
        routes = self._family_routes()
        registered = {p for _, p in routes}
        for path in REQUIRED:
            with self.subTest(required=path):
                self.assertIn(path, registered)
        self.assertTrue(any(p.startswith("/security") for p in registered))
        self.assertTrue(any(p.startswith("/api/ops") for p in registered))
        for method, path in routes:
            with self.subTest(route=path, method=method):
                self.assertTrue(self.main._is_operations_path(path))
                r = self._get(self._url(path), method=method)
                self.assertEqual(r.status_code, 401)

    # ── R1-02 / R1-03 hostile callers ───────────────────────────────────────

    def test_r1_02_03_every_hostile_caller_is_refused_on_every_route(self):
        for label, kw in self._hostile():
            for method, path in self._family_routes():
                with self.subTest(caller=label, route=path, method=method):
                    r = self._get(self._url(path), method=method, **kw)
                    self.assertEqual(r.status_code, 401)
                    self.assertEqual(r.json(), {"detail": "Unauthorized"})

    def test_r1_03b_forwarding_headers_rewritten_by_the_proxy_layer_grant_nothing(self):
        """uvicorn runs with `--proxy-headers` and trusts 127.0.0.1 by default, so a loopback
        request with `X-Forwarded-For: <peer>` reaches the app with `client.host == peer`.
        That address was manufactured by a header, so it is not a direct peer."""
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
        proxied = ProxyHeadersMiddleware(self.app, trusted_hosts="127.0.0.1")
        for method, path in self._family_routes():
            with self.subTest(route=path):
                r = self._get(self._url(path), method=method, peer="127.0.0.1",
                              headers={"x-forwarded-for": PEER}, app=proxied)
                self.assertEqual(r.status_code, 401)
        # Control: the same proxy layer with the real secret is still a machine caller.
        r = self._get("/api/ops/summary", peer="127.0.0.1", app=proxied,
                      headers={"x-forwarded-for": PEER, "x-internal-auth": SECRET})
        self.assertEqual(r.status_code, 200)

    def test_r1_03c_the_shared_peer_check_reads_the_socket_not_a_header(self):
        """The guard reuses `_client_is_peer_container`. That function must derive the peer
        from the socket alone, or every class built on it (MACHINE as well) would accept a
        header-manufactured peer. Pinned on the MACHINE class, whose behaviour is unchanged."""
        url = "/api/whatsapp/thread/1/state"
        for label, kw in (("X-Forwarded-For", {"headers": {"x-forwarded-for": PEER}}),
                          ("X-Real-IP", {"headers": {"x-real-ip": PEER}}),
                          ("Forwarded", {"headers": {"forwarded": f"for={PEER}"}}),
                          ("gateway + XFF", {"peer": GATEWAY,
                                             "headers": {"x-forwarded-for": PEER}})):
            with self.subTest(header=label):
                self.assertEqual(self._get(url, **kw).status_code, 401)

    # ── R1-04 session ───────────────────────────────────────────────────────

    def test_r1_04_a_crm_session_is_admitted_on_every_route(self):
        cookies = {self.SESSION_COOKIE: self._cookie()}
        for method, path in self._family_routes():
            with self.subTest(route=path):
                r = self._get(self._url(path), method=method, cookies=cookies)
                self.assertNotEqual(r.status_code, 401)
                if "{" not in path:
                    self.assertEqual(r.status_code, 200)

    def test_r1_04b_authenticated_payloads_are_unchanged(self):
        """Access control only: an admitted operator still sees the seeded rows in full."""
        cookies = {self.SESSION_COOKIE: self._cookie()}
        expect = {
            "/api/ops/messages": (PHONE, TEXT, WAMID),
            "/security/unauthorized-path-events?since=2000-01-01T00:00:00": (DETAIL, WAMID),
            "/security/outbound-ledger?since=2000-01-01T00:00:00": (WAMID, FINGERPRINT),
            "/api/ops/critical-events": (WAMID,),
        }
        for url, needles in expect.items():
            with self.subTest(url=url):
                r = self._get(url, cookies=cookies)
                self.assertEqual(r.status_code, 200)
                for needle in needles:
                    self.assertIn(needle, r.text)

    # ── R1-05 / R1-06 machine callers and the stricter routes ───────────────

    def test_r1_05_06_machine_callers_admitted_except_on_session_only_routes(self):
        callers = (("shared secret", {"headers": {"x-internal-auth": SECRET}}),
                   ("shared secret, padded", {"headers": {"x-internal-auth": f" {SECRET} "}}),
                   ("trusted direct peer", {"peer": PEER}),
                   ("shared secret via gateway", {"peer": GATEWAY,
                                                  "headers": {"x-internal-auth": SECRET}}))
        seen_session_only = set()
        for label, kw in callers:
            for method, path in self._family_routes():
                with self.subTest(caller=label, route=path):
                    r = self._get(self._url(path), method=method, **kw)
                    if path in SESSION_ONLY:
                        seen_session_only.add(path)
                        self.assertEqual(r.status_code, 401)
                        self.assertEqual(r.json(), {"detail": "Unauthorized"})
                    else:
                        self.assertNotEqual(r.status_code, 401)
                        if "{" not in path:
                            self.assertEqual(r.status_code, 200)
        self.assertIn("/api/ops/booking-attention", seen_session_only)

    def test_r1_06b_booking_attention_is_session_only(self):
        url = "/api/ops/booking-attention"
        self.assertEqual(self._get(url, headers={"x-internal-auth": SECRET}).status_code, 401)
        self.assertEqual(self._get(url, peer=PEER).status_code, 401)
        self.assertEqual(self._get(url, peer=PEER,
                                   headers={"x-internal-auth": SECRET}).status_code, 401)
        r = self._get(url, cookies={self.SESSION_COOKIE: self._cookie()})
        self.assertEqual(r.status_code, 200)

    # ── R1-07 flag independence ─────────────────────────────────────────────

    def test_r1_07_the_internal_auth_flag_cannot_disable_the_guard(self):
        variants = {"false": "false", "missing": None, "empty": "", "malformed": "maybe",
                    "zero": "0", "one": "1", "yes": "yes", "TRUE": "TRUE", "true": "true"}
        configs = {"secret+cidr": {}, "cidr only": {"INTERNAL_API_SECRET": None},
                   "secret only": {"INTERNAL_API_TRUSTED_CIDR": None},
                   "nothing configured": {"INTERNAL_API_SECRET": None,
                                          "INTERNAL_API_TRUSTED_CIDR": None}}
        cookies = {self.SESSION_COOKIE: self._cookie()}
        for flag_label, flag in variants.items():
            for cfg_label, extra in configs.items():
                self.cfg = _settings(INTERNAL_API_AUTH_ENABLED=flag, **extra)
                for method, path in self._family_routes():
                    with self.subTest(flag=flag_label, config=cfg_label, route=path):
                        self.assertEqual(
                            self._get(self._url(path), method=method).status_code, 401)
                        self.assertEqual(self._get(self._url(path), method=method,
                                                   peer=GATEWAY).status_code, 401)
                        self.assertNotEqual(self._get(self._url(path), method=method,
                                                      cookies=cookies).status_code, 401)
                with self.subTest(flag=flag_label, config=cfg_label, check="no channel"):
                    if cfg_label == "nothing configured":
                        self.assertEqual(self._get("/api/ops/summary", peer=PEER,
                                                   headers={"x-internal-auth": SECRET}
                                                   ).status_code, 401)

    # ── R1-08 non-disclosure ────────────────────────────────────────────────

    def test_r1_08_refusals_disclose_nothing_and_are_indistinguishable(self):
        bodies = set()
        probes = [self._url(p) for _, p in self._family_routes()] + [
            "/security/unauthorized-path-events?since=2000-01-01T00:00:00",
            f"/security/outbound-ledger?wamid={WAMID}",
            f"/api/ops/messages?thread_id=1",
            "/api/ops/does-not-exist", "/security/does-not-exist", "/api/ops",
            "/security", "/api/ops/turn/%27%20OR%201%3D1--", "/api/ops/offer/999999"]
        for url in probes:
            for label, kw in self._hostile():
                with self.subTest(url=url, caller=label):
                    r = self._get(url, **kw)
                    self.assertEqual(r.status_code, 401)
                    for needle in SENTINELS:
                        self.assertNotIn(needle, r.text)
                    self.assertNotIn("set-cookie", {k.lower() for k in r.headers})
                    bodies.add((r.status_code, r.content, r.headers.get("content-type")))
        self.assertEqual(len(bodies), 1, "one refusal for every path: no existence oracle")

    def test_r1_08b_refusal_happens_before_any_database_access(self):
        def boom():
            raise AssertionError("database reached by an unauthenticated request")
        for key in list(self.app.dependency_overrides):
            self.app.dependency_overrides[key] = boom
        import app.db as app_db
        with mock.patch.object(app_db, "SessionLocal", boom):
            for method, path in self._family_routes():
                with self.subTest(route=path):
                    self.assertEqual(self._get(self._url(path), method=method).status_code,
                                     401)

    # ── R1-09 future routes ─────────────────────────────────────────────────

    def test_r1_09_a_route_registered_later_inherits_the_guard(self):
        from fastapi.routing import APIRoute
        added = []
        for path in ("/api/ops/pw1r1-future", "/security/pw1r1-future"):
            route = APIRoute(path, lambda: {"secret": DETAIL}, methods=["GET"])
            self.app.router.routes.append(route)
            added.append(route)
        self.addCleanup(lambda: [self.app.router.routes.remove(r) for r in added])
        cookies = {self.SESSION_COOKIE: self._cookie()}
        for route in added:
            with self.subTest(route=route.path):
                r = self._get(route.path)
                self.assertEqual(r.status_code, 401)
                self.assertNotIn(DETAIL, r.text)
                self.assertEqual(self._get(route.path, peer=GATEWAY).status_code, 401)
                self.assertEqual(self._get(route.path, cookies=cookies).status_code, 200)

    def test_r1_09b_a_root_path_prefix_cannot_hide_the_family(self):
        """Behind a sub-path mount (`root_path`), the router matches the path WITHOUT the
        prefix while the URL path carries it. The guard must see the routed spelling."""
        async def go(path, **client_kw):
            transport = httpx.ASGITransport(app=self.app, client=(INTERNET, 40123),
                                            root_path="/pw1r1-mount")
            async with httpx.AsyncClient(transport=transport, base_url="http://backend",
                                         follow_redirects=False, **client_kw) as client:
                return await client.get(path)
        cookies = {self.SESSION_COOKIE: self._cookie()}
        for url in ("/pw1r1-mount/api/ops/messages", "/pw1r1-mount/security/outbound-ledger",
                    "/api/ops/messages", "/security/outbound-ledger"):
            with self.subTest(url=url):
                r = asyncio.run(go(url))
                self.assertEqual(r.status_code, 401)
                for needle in SENTINELS:
                    self.assertNotIn(needle, r.text)
        reached = asyncio.run(go("/pw1r1-mount/api/ops/messages", cookies=cookies))
        self.assertEqual(reached.status_code, 200, "the mounted spelling does route")

    # ── R1-10 /control ──────────────────────────────────────────────────────

    def test_r1_10_control_works_for_a_session_and_redirects_without_one(self):
        r = self._get("/control", cookies={self.SESSION_COOKIE: self._cookie()})
        self.assertEqual(r.status_code, 200)
        self.assertIn("/api/ops/critical-events", r.text)
        self.assertIn("/api/ops/booking-attention", r.text)
        r = self._get("/control")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers.get("location"), "/login")
        # The same-origin calls /control makes carry the operator's cookie and are admitted.
        cookies = {self.SESSION_COOKIE: self._cookie()}
        for url in ("/api/ops/summary", "/api/ops/threads", "/api/ops/messages",
                    "/api/ops/critical-events", "/api/ops/paths", "/api/ops/path-registry",
                    "/api/ops/booking-attention"):
            with self.subTest(url=url):
                self.assertEqual(self._get(url, cookies=cookies).status_code, 200)

    # ── R1-11 public protocol endpoints ─────────────────────────────────────

    def test_r1_11_webhook_and_flow_endpoints_are_not_in_the_family(self):
        for path in ("/integrations/whatsapp/webhook",
                     "/integrations/whatsapp/flows/booking/data-exchange"):
            with self.subTest(path=path):
                self.assertTrue(self.main._is_public_path(path))
                guard = getattr(self.main, "_is_operations_path", None)
                if guard is not None:          # absent on the parent: nothing to compare
                    self.assertFalse(guard(path))
        import app.routes.whatsapp as wa
        cfg = mock.MagicMock(whatsapp_enabled=True, whatsapp_verify_token="pw1r1-verify")
        with mock.patch.object(wa, "get_settings", return_value=cfg):
            ok = self._get("/integrations/whatsapp/webhook?hub.mode=subscribe"
                           "&hub.challenge=4242&hub.verify_token=pw1r1-verify",
                           peer=GATEWAY)
            bad = self._get("/integrations/whatsapp/webhook?hub.mode=subscribe"
                            "&hub.challenge=4242&hub.verify_token=wrong", peer=GATEWAY)
        self.assertEqual((ok.status_code, ok.text), (200, "4242"))
        self.assertEqual((bad.status_code, bad.text), (403, "forbidden"))
        # Data Exchange: an anonymous gateway caller reaches the route's own handling, not
        # the operations refusal.
        r = self._get("/integrations/whatsapp/flows/booking/data-exchange", peer=GATEWAY,
                      method="POST")
        self.assertNotEqual((r.status_code, r.content),
                            (401, json.dumps({"detail": "Unauthorized"},
                                             separators=(",", ":")).encode()))


if __name__ == "__main__":
    unittest.main()
