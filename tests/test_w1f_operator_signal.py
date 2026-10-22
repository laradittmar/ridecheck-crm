"""PRELAUNCH-FIRST-WILD-FINDINGS-W1-F1-F2-OPERATOR-SIGNAL-CORRECTION — the SQLite gate.

W1-F1  a committed, acknowledged Flow booking is not an unanswered customer: the predicate
       the human-handoff alert now consults, over the EXACT Wild ordering produced by the
       production code (Data Exchange booking → acknowledgement → stored `flow_response` →
       CE replay), and every near miss that must still alert.
W1-F2  the operator's WhatsApp notice: configuration fails closed, the gate pins the path to
       the configured operator, one record per committed booking, replays and provider
       callbacks add nothing, one bounded retry, a visible OPERATOR_NOTIFICATION_REQUIRED, the
       customer's acknowledgement unchanged, no e-mail in its place.

SQLite, no network: the booking authority, the Data Exchange confirmation, the CE turn, the
outbound gate and the notifier are production code; only the Meta senders are recorders and
`urlopen` raises. The PostgreSQL proofs (the real alert job, races, the migration) are in
`test_w1f_unanswered_alert_pg.py` and `test_w1f_operator_notification_pg.py`.
Synthetic data only.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import dataclasses
import json
import os
import pathlib
import re
import sys
import unittest
from datetime import datetime, time, timedelta, timezone
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _sb2b_harness as H  # noqa: E402

from sqlalchemy import select, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (BookingCommandRecord, OperatorNotification, SecurityEvent,  # noqa: E402
                        SystemSetting, WhatsAppContact, WhatsAppMessage, WhatsAppThread,
                        WhatsAppThreadState)
from app.services import operator_notification as ON  # noqa: E402
from app.services import unanswered_alert as UA  # noqa: E402
from app.services.outbound_safety_gate import GateOutcome, OutboundSafetyGate  # noqa: E402

DEST = "5491100990001"                      # synthetic operator destination
TEMPLATE = "ridecheck_operador_reserva"
LANG = "es_AR"
ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "backend" / "app" / "services" / "operator_notification.py").read_text()


def op_settings(**over):
    base = dict(operator_whatsapp_notify_enabled=True, operator_whatsapp_to=DEST,
                operator_booking_template_name=TEMPLATE,
                operator_booking_template_language=LANG,
                whatsapp_token="fixture-token-not-real",
                whatsapp_phone_number_id="PNID_FIXTURE")
    base.update(over)
    return dataclasses.replace(H.settings(), **base)


class Template:
    """The template sender, recorded. `modes` is consumed one per call:
    ok | 4xx (Graph-documented rejection) | 408 | 5xx | timeout | nowamid | runtime."""

    def __init__(self, *modes):
        self.modes = list(modes) or ["ok"]
        self.sent: list[dict] = []

    def __call__(self, *, to_wa_id, template_name, language_code, body_parameters):
        from app.ui.whatsapp_ui import MetaSendError
        mode = self.modes.pop(0) if len(self.modes) > 1 else self.modes[0]
        self.sent.append({"to": to_wa_id, "name": template_name, "lang": language_code,
                          "params": list(body_parameters), "mode": mode})
        if mode == "4xx":
            raise MetaSendError(400, '{"error": {"code": 131026, "message": "undeliverable"}}')
        if mode == "408":
            raise MetaSendError(408, "<html>timeout</html>")
        if mode == "5xx":
            raise MetaSendError(503, '{"error": {"code": 2, "message": "unavailable"}}')
        if mode == "timeout":
            raise MetaSendError(None, "timed out")
        if mode == "nowamid":
            raise MetaSendError(200, "{}", error_message="Unexpected response: no messages[0].id")
        if mode == "runtime":
            raise RuntimeError("WHATSAPP_TOKEN missing — a template is never simulated")
        return f"wamid.W1F.OP.{len(self.sent):04d}", 200


@contextlib.contextmanager
def notifier(cfg=None, template: Template | None = None, *, outbound="true"):
    cfg = cfg or op_settings()
    tpl = template or Template()
    env = {"OUTBOUND_ENABLED": outbound} if outbound is not None else {}
    with mock.patch.dict(os.environ, env), \
            mock.patch.object(ON, "get_settings", lambda: cfg), \
            mock.patch("app.ui.whatsapp_ui._send_whatsapp_cloud_template", tpl), \
            mock.patch("app.services.resend_email.send_booking_notification",
                       side_effect=AssertionError("an e-mail was sent in place of WhatsApp")), \
            mock.patch("urllib.request.urlopen", side_effect=AssertionError("egress")):
        if outbound is None:
            os.environ.pop("OUTBOUND_ENABLED", None)
        yield tpl


def set_floor(engine, value="0"):
    with Session(engine) as db:
        row = db.get(SystemSetting, ON.FLOOR_KEY)
        if row is None:
            db.add(SystemSetting(key=ON.FLOOR_KEY, value=value))
        else:
            row.value = value
        db.commit()


def book(engine, **seed):
    """A real Data Exchange booking, acknowledged — exactly what the Wild did. A second
    booking in the same engine needs its own slot (`at=`)."""
    w = H.seed(engine, **seed)
    with H.Meta() as meta:
        screen = H.dx_confirm(engine, w)
    assert screen["screen"] == "SUCCESS", screen
    assert [t for _w, t in meta.sent] == [H.OWNER_TEXT], meta.sent
    return w


def run(engine, now=None):
    db = H.session_factory(engine)()
    try:
        return ON.run_once(db, now=now)
    finally:
        db.close()


def notices(engine):
    with Session(engine) as db:
        return db.execute(select(OperatorNotification).order_by(OperatorNotification.id)
                          ).scalars().all()


def operator_outbound(engine):
    with Session(engine) as db:
        return db.execute(select(WhatsAppMessage).where(
            WhatsAppMessage.path_id == "OPERATOR_NOTIFICATION").order_by(WhatsAppMessage.id)
        ).scalars().all()


def ledger(engine, w):
    with Session(engine) as db:
        return db.execute(select(BookingCommandRecord).where(
            BookingCommandRecord.thread_id == w.thread_id)).scalar_one()


def attention(engine, **kw):
    from app.routes.ops_dashboard import booking_attention
    with Session(engine) as db:
        return {r["id"]: r for r in booking_attention(db, **kw)}


# ══ configuration ═════════════════════════════════════════════════════════════════════════

class C_Configuration(unittest.TestCase):

    def test_c01_disabled_by_default_and_every_gap_fails_closed(self):
        self.assertEqual(ON.config_problem(H.settings()), "NOT_CONFIGURED_DISABLED")
        cases = {
            "NOT_CONFIGURED_DISABLED": dict(operator_whatsapp_notify_enabled=False),
            "NOT_CONFIGURED_DESTINATION_MISSING": dict(operator_whatsapp_to=""),
            "NOT_CONFIGURED_DESTINATION_MALFORMED": dict(operator_whatsapp_to="+5491100990001"),
            "NOT_CONFIGURED_TEMPLATE_MISSING": dict(operator_booking_template_name=""),
            "NOT_CONFIGURED_TEMPLATE_MALFORMED": dict(operator_booking_template_name="Reserva!"),
            "NOT_CONFIGURED_LANGUAGE_MISSING": dict(operator_booking_template_language=""),
            "NOT_CONFIGURED_LANGUAGE_MALFORMED": dict(operator_booking_template_language="es-AR"),
            "NOT_CONFIGURED_TRANSPORT": dict(whatsapp_token="dummy"),
        }
        for code, over in cases.items():
            with self.subTest(code=code):
                self.assertEqual(ON.config_problem(op_settings(**over)), code)
                self.assertIsNone(ON.operator_config(op_settings(**over)))
        for bad in ("5491100", "0491100990001", "54911009900011234", "5491100990001 ",
                    "54911OO990001"):
            with self.subTest(destination=bad):
                self.assertIsNone(ON.operator_config(op_settings(operator_whatsapp_to=bad)))
        cfg = ON.operator_config(op_settings())
        self.assertEqual((cfg.destination, cfg.template_name, cfg.language), (DEST, TEMPLATE, LANG))

    def test_c02_flag_is_exact_true_and_values_come_from_the_environment_only(self):
        from app.settings import get_settings
        env = {"OPERATOR_WHATSAPP_NOTIFY_ENABLED": "yes", "OPERATOR_WHATSAPP_TO": DEST,
               "OPERATOR_BOOKING_TEMPLATE_NAME": TEMPLATE,
               "OPERATOR_BOOKING_TEMPLATE_LANGUAGE": LANG}
        get_settings.cache_clear()
        try:
            with mock.patch.dict(os.environ, env):
                self.assertFalse(get_settings().operator_whatsapp_notify_enabled)
                get_settings.cache_clear()
                with mock.patch.dict(os.environ, {"OPERATOR_WHATSAPP_NOTIFY_ENABLED": "true"}):
                    s = get_settings()
                    self.assertTrue(s.operator_whatsapp_notify_enabled)
                    self.assertEqual((s.operator_whatsapp_to, s.operator_booking_template_name,
                                      s.operator_booking_template_language),
                                     (DEST, TEMPLATE, LANG))
        finally:
            get_settings.cache_clear()

    def test_c03_no_destination_template_or_email_in_source(self):
        settings_src = (ROOT / "backend" / "app" / "settings.py").read_text()
        for src in (SOURCE, settings_src):
            self.assertIsNone(re.search(r"\b\d{10,15}\b", src), "a phone-like literal in source")
        self.assertNotIn("resend_email", SOURCE)
        self.assertNotIn("send_booking_notification", SOURCE)
        self.assertNotIn("smtp", SOURCE.lower())
        # the notifier takes no destination from any caller
        for fn in (ON.run_once, ON.send_claimed, ON.claim_next, ON.enqueue_committed_bookings):
            self.assertNotIn("destination", fn.__code__.co_varnames[:fn.__code__.co_argcount])


# ══ the gate: OPERATOR_NOTIFICATION is not a send-to-any-phone path ═══════════════════════

class G_Gate(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()
        with Session(self.engine) as db:
            c = WhatsAppContact(wa_id="5491100990077")
            db.add(c); db.flush()
            t = WhatsAppThread(contact_id=c.id, unread_count=0)
            db.add(t); db.commit()
            self.thread_id = t.id

    def attempt(self, wa_id, cfg):
        with mock.patch.object(ON, "get_settings", lambda: cfg), \
                mock.patch.dict(os.environ, {"OUTBOUND_ENABLED": "true"}), Session(self.engine) as db:
            return OutboundSafetyGate(db).attempt(
                wa_id=wa_id, thread_id=self.thread_id, text="probe", message_type="template",
                path_id="OPERATOR_NOTIFICATION")

    def test_g01_any_other_recipient_is_refused_with_a_blocker_event(self):
        for wa_id, cfg in (("5491100990077", op_settings()),        # a customer
                           (DEST, H.settings()),                     # unconfigured: anyone
                           (DEST, op_settings(whatsapp_token="dummy"))):
            with self.subTest(wa_id=wa_id[-4:]):
                r = self.attempt(wa_id, cfg)
                self.assertEqual(r.outcome, GateOutcome.BLOCKED_UNAUTHORIZED_PATH)
        with Session(self.engine) as db:
            events = db.execute(select(SecurityEvent.event_type, SecurityEvent.severity)).all()
        self.assertEqual(events, [("OPERATOR_NOTIFICATION_UNCONFIGURED_DESTINATION", "BLOCKER")] * 3)

    def test_g02_the_configured_operator_passes_and_the_kill_switch_still_rules(self):
        self.assertEqual(self.attempt(DEST, op_settings()).outcome, GateOutcome.ALLOWED)
        with mock.patch.object(ON, "get_settings", lambda: op_settings()), \
                mock.patch.dict(os.environ, {}), Session(self.engine) as db:
            os.environ.pop("OUTBOUND_ENABLED", None)
            r = OutboundSafetyGate(db).attempt(
                wa_id=DEST, thread_id=self.thread_id, text="probe2", message_type="template",
                path_id="OPERATOR_NOTIFICATION")
        self.assertEqual(r.outcome, GateOutcome.BLOCKED_KILL_SWITCH)

    def test_g03_path_registered_and_shown(self):
        from app.routes.ops_dashboard import get_path_registry
        from app.services.outbound_path_registry import AUTHORIZED_PATHS, OutboundPathId
        self.assertIn(OutboundPathId.OPERATOR_NOTIFICATION, AUTHORIZED_PATHS)
        entry = {e["path_id"]: e for e in get_path_registry()["paths"]}["OPERATOR_NOTIFICATION"]
        self.assertTrue(entry["authorized"])
        self.assertEqual(entry["kind"], "AUTOMATED")


# ══ the template sender ═══════════════════════════════════════════════════════════════════

class S_TemplateSender(unittest.TestCase):

    def call(self, cfg, env, urlopen):
        from app.ui import whatsapp_ui as ui
        with mock.patch.object(ui, "get_settings", lambda: cfg), \
                mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(ui.urlrequest, "urlopen", urlopen):
            if "OUTBOUND_ENABLED" not in env:
                os.environ.pop("OUTBOUND_ENABLED", None)
            return ui._send_whatsapp_cloud_template(DEST, TEMPLATE, LANG, ["a", "b"])

    def test_s01_kill_switch_is_the_first_statement(self):
        from app.services.outbound_guard import OutboundBlockedError
        opened = mock.Mock(side_effect=AssertionError("egress"))
        with self.assertRaises(OutboundBlockedError):
            self.call(op_settings(), {}, opened)
        opened.assert_not_called()

    def test_s02_no_simulated_success_without_a_real_token(self):
        opened = mock.Mock(side_effect=AssertionError("egress"))
        for token in ("", "dummy"):
            with self.subTest(token=token or "empty"), self.assertRaises(RuntimeError):
                self.call(op_settings(whatsapp_token=token), {"OUTBOUND_ENABLED": "true"}, opened)
        opened.assert_not_called()

    def test_s03_payload_is_a_body_parameter_template(self):
        seen = {}

        class Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"messages": [{"id": "wamid.W1F.TPL"}]}'

        def opened(req, timeout):
            seen["url"], seen["body"] = req.full_url, json.loads(req.data)
            seen["auth"] = req.get_header("Authorization")
            return Resp()
        wamid, status = self.call(op_settings(), {"OUTBOUND_ENABLED": "true"}, opened)
        self.assertEqual((wamid, status), ("wamid.W1F.TPL", 200))
        self.assertTrue(seen["url"].endswith("/PNID_FIXTURE/messages"))
        self.assertEqual(seen["body"], {
            "messaging_product": "whatsapp", "to": DEST, "type": "template",
            "template": {"name": TEMPLATE, "language": {"code": LANG}, "components": [
                {"type": "body", "parameters": [{"type": "text", "text": "a"},
                                                {"type": "text", "text": "b"}]}]}})
        self.assertEqual(seen["auth"], "Bearer fixture-token-not-real")


# ══ W1-F2: the notice ════════════════════════════════════════════════════════════════════

class N_Notice(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()
        set_floor(self.engine)

    def test_n01_one_committed_booking_one_notice_sent_with_the_operational_summary(self):
        w = book(self.engine)
        with Session(self.engine) as db:      # a private customer message must never travel
            db.add(WhatsAppMessage(thread_id=w.thread_id, direction="in", status="received",
                                   wa_message_id="wamid.W1F.PRIVATE", message_type="text",
                                   text="mensaje privado del cliente XYZ",
                                   timestamp=datetime.now(timezone.utc) - timedelta(hours=1)))
            db.commit()
        before = H.facts(self.engine, w)
        with notifier() as tpl:
            result = run(self.engine)
        led = ledger(self.engine, w)
        self.assertEqual(result["enqueued"], [led.id])
        self.assertEqual(len(tpl.sent), 1)
        sent = tpl.sent[0]
        self.assertEqual((sent["to"], sent["name"], sent["lang"]), (DEST, TEMPLATE, LANG))
        params = dict(zip(ON.TEMPLATE_PARAMETERS, sent["params"]))
        self.assertEqual(len(sent["params"]), 10)
        self.assertEqual(params["evento"], "Nueva reserva confirmada")
        self.assertEqual(params["whatsapp_cliente"], f"+{w.wa_id}")
        self.assertIn("Peugeot 208", params["vehiculo"])
        self.assertIn("Calle Falsa 123", params["lugar"])
        self.assertIn(w.day.strftime("%d/%m/%Y"), params["fecha_hora"])
        self.assertIn("10:00", params["fecha_hora"])
        self.assertIn("SB2B-FIXTURE", params["profesional"])
        self.assertIn("SENT", params["estado"])
        self.assertIn(f"Lead {w.lead_id}", params["identificadores"])
        self.assertIn(f"Conversación {w.thread_id}", params["identificadores"])
        self.assertEqual(params["crm"], f"/whatsapp/thread/{w.thread_id}")
        for p in sent["params"]:
            self.assertTrue(p.strip() and "\n" not in p and "\t" not in p and "     " not in p)
            self.assertNotIn("privado", p)
        (n,) = notices(self.engine)
        self.assertEqual((n.status, n.attempts, n.provider, n.provider_message_id),
                         ("SENT", 1, "META_WHATSAPP_TEMPLATE", "wamid.W1F.OP.0001"))
        self.assertEqual((n.booking_command_id, n.kind, n.template_name, n.template_language),
                         (led.id, "BOOKING_CREATED", TEMPLATE, LANG))
        self.assertEqual(n.identity, ON.notification_identity(led.idempotency_key))
        self.assertEqual(len(n.destination_hash), 16)
        self.assertNotIn(DEST, json.dumps([n.destination_hash, n.last_error, n.identity]))
        (out,) = operator_outbound(self.engine)
        self.assertEqual((out.status, out.wa_message_id, out.message_type, out.id),
                         ("sent", "wamid.W1F.OP.0001", "template", n.outbound_message_id))
        self.assertNotEqual(out.thread_id, w.thread_id, "the customer's thread was reused")
        self.assertEqual(out.content_fingerprint, ON.attempt_fingerprint(n.identity, 1))
        # the customer's conversation is untouched and got exactly its one acknowledgement
        self.assertEqual(H.facts(self.engine, w), before)
        self.assertEqual(before["outbound"], [("BOOKING_FLOW", "sent", H.OWNER_TEXT)])
        self.assertFalse(attention(self.engine, pending_older_than_seconds=0).get(led.id, {})
                         .get("reasons", []))

    def test_n02_replays_resubmissions_and_more_passes_add_nothing(self):
        w = book(self.engine)
        with notifier() as tpl:
            run(self.engine)
            with H.Meta() as meta:
                self.assertEqual(H.dx_confirm(self.engine, w)["screen"], "SUCCESS")   # replay
                out, _eng = H.run_turn(self.engine, H.flow_event(w))                 # CE replay
                self.assertEqual(meta.sent, [], "a second acknowledgement")
            for _ in range(3):
                run(self.engine, now=datetime.now(timezone.utc) + timedelta(minutes=5))
        self.assertEqual(len(tpl.sent), 1)
        self.assertEqual(len(notices(self.engine)), 1)
        self.assertEqual(len(operator_outbound(self.engine)), 1)
        with Session(self.engine) as db:
            self.assertEqual(db.query(BookingCommandRecord).count(), 1)

    def test_n03_provider_callbacks_never_create_a_notice(self):
        from app.routes import whatsapp as route
        from starlette.requests import Request
        w = book(self.engine)
        with notifier() as tpl:
            run(self.engine)
        (n,) = notices(self.engine)
        for status in ("sent", "delivered", "read", "read", "failed"):
            body = json.dumps({"entry": [{"changes": [{"value": {"statuses": [
                {"id": n.provider_message_id, "status": status}]}}]}]}).encode()

            async def receive(body=body):
                return {"type": "http.request", "body": body, "more_body": False}
            secret = "w1f-fixture-app-secret-not-real"
            sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            req = Request({"type": "http", "method": "POST", "path": "/webhook",
                           "headers": [(b"content-type", b"application/json"),
                                       (b"x-hub-signature-256", f"sha256={sig}".encode())],
                           "query_string": b""}, receive)
            cfg = dataclasses.replace(op_settings(), whatsapp_app_secret=secret,
                                      whatsapp_verify_token="v", n8n_webhook_url="")
            with mock.patch.object(route, "get_settings", lambda: cfg), \
                    mock.patch.object(route, "db_engine", self.engine), \
                    H.session_factory(self.engine)() as db:
                resp = asyncio.run(route.inbound_webhook(req, db))
            self.assertEqual(resp.status_code, 200)
            with notifier() as tpl2:
                run(self.engine, now=datetime.now(timezone.utc) + timedelta(minutes=10))
            self.assertEqual(tpl2.sent, [])
        self.assertEqual(len(tpl.sent), 1)
        self.assertEqual(len(notices(self.engine)), 1)
        (out,) = operator_outbound(self.engine)
        self.assertEqual(out.status, "failed")            # read < failed by precedence
        row = attention(self.engine)[ledger(self.engine, w).id]
        self.assertEqual(row["notification_state"], "DELIVERY_FAILED")
        self.assertIn("OPERATOR_NOTIFICATION_REQUIRED", row["reasons"])

    def test_n04_first_failure_one_bounded_retry_then_sent(self):
        w = book(self.engine)
        t0 = datetime.now(timezone.utc)
        tpl = Template("4xx", "ok")
        with notifier(template=tpl):
            run(self.engine, now=t0)
            (n,) = notices(self.engine)
            self.assertEqual((n.status, n.attempts, n.last_error),
                             ("PENDING", 1, "META_REJECTED_400"))
            run(self.engine, now=t0 + timedelta(seconds=30))     # not due yet
            self.assertEqual(len(tpl.sent), 1)
            run(self.engine, now=t0 + timedelta(seconds=ON.RETRY_AFTER_SECONDS + 1))
        (n,) = notices(self.engine)
        self.assertEqual((n.status, n.attempts, n.provider_message_id),
                         ("SENT", 2, "wamid.W1F.OP.0002"))
        outs = operator_outbound(self.engine)
        self.assertEqual([o.status for o in outs], ["failed", "sent"])
        self.assertEqual([o.content_fingerprint for o in outs],
                         [ON.attempt_fingerprint(n.identity, 1),
                          ON.attempt_fingerprint(n.identity, 2)])
        self.assertNotIn(ledger(self.engine, w).id,
                         attention(self.engine, pending_older_than_seconds=0))

    def test_n05_second_failure_is_terminal_and_visible(self):
        w = book(self.engine)
        t0 = datetime.now(timezone.utc)
        tpl = Template("4xx", "4xx", "ok")
        with notifier(template=tpl):
            for k in range(6):
                run(self.engine, now=t0 + timedelta(seconds=k * (ON.RETRY_AFTER_SECONDS + 1)))
        self.assertEqual(len(tpl.sent), 2, "a third attempt")
        (n,) = notices(self.engine)
        self.assertEqual((n.status, n.attempts, n.next_attempt_at), ("FAILED", 2, None))
        row = attention(self.engine)[ledger(self.engine, w).id]  # no settle window needed
        self.assertEqual(row["notification_state"], "FAILED")
        self.assertEqual(row["notification_channel"], "WHATSAPP")
        self.assertEqual(row["notification_attempts"], 2)
        self.assertEqual(row["reasons"], ["OPERATOR_NOTIFICATION_REQUIRED"])
        self.assertEqual(row["recovery"], "NOTIFY_TEAM_MANUALLY")
        self.assertEqual(row["booking"], "COMMITTED")

    def test_n06_uncertain_outcomes_are_held_and_never_repeated(self):
        for mode in ("timeout", "5xx", "408", "nowamid"):
            with self.subTest(mode=mode):
                engine = H.sqlite_engine()
                set_floor(engine)
                w = book(engine)
                tpl = Template(mode, "ok")
                t0 = datetime.now(timezone.utc)
                with notifier(template=tpl):
                    for k in range(4):
                        run(engine, now=t0 + timedelta(seconds=k * (ON.RETRY_AFTER_SECONDS + 1)))
                self.assertEqual(len(tpl.sent), 1)
                (n,) = notices(engine)
                self.assertEqual((n.status, n.attempts), ("HELD", 1))
                self.assertTrue(n.last_error.startswith("UNCERTAIN_"), n.last_error)
                row = attention(engine)[ledger(engine, w).id]
                self.assertEqual(row["reasons"], ["OPERATOR_NOTIFICATION_REQUIRED"])

    def test_n07_a_lost_sender_is_held_not_reclaimed(self):
        book(self.engine)
        t0 = datetime.now(timezone.utc)
        with notifier() as tpl:
            db = H.session_factory(self.engine)()
            ON.enqueue_committed_bookings(db, now=t0)
            claim = ON.claim_next(db, now=t0)          # the sender dies here
            db.close()
            self.assertIsNotNone(claim)
            run(self.engine, now=t0 + timedelta(seconds=30))
            self.assertEqual(notices(self.engine)[0].status, "SENDING")
            run(self.engine, now=t0 + timedelta(seconds=ON.LEASE_SECONDS + 1))
            run(self.engine, now=t0 + timedelta(seconds=ON.LEASE_SECONDS + 200))
        self.assertEqual(tpl.sent, [])
        (n,) = notices(self.engine)
        self.assertEqual((n.status, n.attempts, n.last_error), ("HELD", 1, "UNCERTAIN_LOST_SENDER"))
        # the dead claim can no longer write
        db = H.session_factory(self.engine)()
        self.assertFalse(ON.record(db, claim, ON.Outcome(True, False, None, wamid="x"), None))
        db.close()
        self.assertEqual(notices(self.engine)[0].status, "HELD")

    def test_n08_unconfigured_records_held_and_sends_nothing(self):
        w = book(self.engine)
        with notifier(cfg=H.settings()) as tpl:
            run(self.engine)
            run(self.engine, now=datetime.now(timezone.utc) + timedelta(minutes=5))
        self.assertEqual(tpl.sent, [])
        self.assertEqual(operator_outbound(self.engine), [])
        (n,) = notices(self.engine)
        self.assertEqual((n.status, n.attempts, n.last_error),
                         ("HELD", 0, "NOT_CONFIGURED_DISABLED"))
        row = attention(self.engine)[ledger(self.engine, w).id]
        self.assertEqual(row["reasons"], ["OPERATOR_NOTIFICATION_REQUIRED"])
        self.assertEqual(row["notification_last_reason"], "NOT_CONFIGURED_DISABLED")

    def test_n09_no_backfill_floor_and_no_floor(self):
        w1 = book(self.engine)
        set_floor(self.engine, str(ledger(self.engine, w1).id))
        w2 = book(self.engine, at=time(12, 0))
        with notifier() as tpl:
            run(self.engine)
        self.assertEqual([n.booking_command_id for n in notices(self.engine)],
                         [ledger(self.engine, w2).id])
        self.assertEqual(len(tpl.sent), 1)
        engine = H.sqlite_engine()                       # no floor at all: nothing, ever
        book(engine)
        with notifier() as tpl:
            self.assertEqual(run(engine)["enqueued"], [])
        self.assertEqual((notices(engine), tpl.sent), ([], []))

    def test_n10_kill_switch_blocks_twice_then_failed_and_nothing_leaves(self):
        book(self.engine)
        t0 = datetime.now(timezone.utc)
        with notifier(outbound=None) as tpl:
            for k in range(4):
                run(self.engine, now=t0 + timedelta(seconds=k * (ON.RETRY_AFTER_SECONDS + 1)))
        self.assertEqual(tpl.sent, [])
        (n,) = notices(self.engine)
        self.assertEqual((n.status, n.attempts, n.last_error),
                         ("FAILED", 2, "GATE_blocked_kill_switch"))
        self.assertEqual([o.status for o in operator_outbound(self.engine)], ["blocked", "blocked"])

    def test_n11_destination_equal_to_the_customer_is_refused(self):
        w = book(self.engine)
        with notifier(cfg=op_settings(operator_whatsapp_to=w.wa_id)) as tpl:
            run(self.engine)
        self.assertEqual(tpl.sent, [])
        (n,) = notices(self.engine)
        self.assertEqual((n.status, n.last_error), ("HELD", "DESTINATION_IS_CUSTOMER"))
        self.assertEqual(operator_outbound(self.engine), [])

    def test_n12_an_operator_number_that_is_a_customer_thread_is_refused(self):
        book(self.engine)
        with Session(self.engine) as db:
            c = WhatsAppContact(wa_id=DEST)
            db.add(c); db.flush()
            db.add(WhatsAppThread(contact_id=c.id, lead_id=999, unread_count=0))
            db.commit()
        with notifier() as tpl:
            run(self.engine)
        self.assertEqual(tpl.sent, [])
        self.assertEqual(notices(self.engine)[0].last_error, "DESTINATION_IS_CUSTOMER_THREAD")

    def test_n13_operator_and_detached_commands_are_not_customer_bookings(self):
        w = book(self.engine)
        with Session(self.engine) as db:
            db.execute(update(BookingCommandRecord).values(source="CRM"))
            db.commit()
        with notifier() as tpl:
            self.assertEqual(run(self.engine)["enqueued"], [])
        self.assertEqual(tpl.sent, [])
        self.assertIsNotNone(w)

    def test_n14_the_concurrent_claim_loses_cleanly(self):
        book(self.engine)
        t0 = datetime.now(timezone.utc)
        with notifier():
            db = H.session_factory(self.engine)()
            ON.enqueue_committed_bookings(db, now=t0)
            first = ON.claim_next(db, now=t0)
            second = ON.claim_next(db, now=t0)
            db.close()
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_n15_identity_is_the_ledger_keys_and_attempts_are_bounded(self):
        a = ON.notification_identity("flow:abc")
        self.assertEqual(a, ON.notification_identity("flow:abc"))
        self.assertNotEqual(a, ON.notification_identity("flow:abd"))
        self.assertRegex(a, r"^[0-9a-f]{64}$")
        self.assertNotEqual(ON.attempt_fingerprint(a, 1), ON.attempt_fingerprint(a, 2))
        for bad in (0, 3):
            with self.assertRaises(ValueError):
                ON.attempt_fingerprint(a, bad)
        with self.assertRaises(ValueError):
            ON.notification_identity("")


# ══ W1-F1: the alert predicate ═══════════════════════════════════════════════════════════

#: The production thread-level query (`unanswered_alert._run_check`), with the time threshold
#: dropped (fixtures are old enough) — what selected the Wild thread.
_THREAD_SQL = """
    SELECT wt.id FROM whatsapp_threads wt
    JOIN whatsapp_contacts wc ON wc.id = wt.contact_id
    LEFT JOIN whatsapp_thread_states wts ON wts.thread_id = wt.id
    WHERE (SELECT direction FROM whatsapp_messages wm WHERE wm.thread_id = wt.id
           AND wm.status NOT IN ('blocked', 'failed') ORDER BY wm.timestamp DESC LIMIT 1) = 'in'
      AND (wts.unanswered_alert_sent_at IS NULL) AND wts.needs_human = 1
"""


def wild_order(engine, **seed):
    """booking committed → acknowledgement sent → flow_response stored → CE replay."""
    w = book(engine, **seed)
    with Session(engine) as db:
        ack = db.execute(select(WhatsAppMessage).where(
            WhatsAppMessage.thread_id == w.thread_id, WhatsAppMessage.direction == "out")
        ).scalar_one()
        db.execute(update(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == w.wamid)
                   .values(timestamp=ack.timestamp + timedelta(seconds=4)))
        db.commit()
    with H.Meta() as meta:
        out, _eng = H.run_turn(engine, H.flow_event(w))
    assert meta.sent == [], meta.sent
    return w, out


class A_AlertPredicate(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()

    def fresh(self):
        """Each near miss in its own database: one slot, one booking."""
        self.engine = H.sqlite_engine()

    def selected(self):
        from sqlalchemy import text
        with Session(self.engine) as db:
            return set(db.execute(text(_THREAD_SQL)).scalars().all())

    def predicate(self, thread_id):
        with Session(self.engine) as db:
            return UA.latest_inbound_is_acknowledged_booking_completion(db, thread_id)

    def test_a01_the_wild_ordering_is_a_completed_acknowledged_booking(self):
        w, out = wild_order(self.engine)
        self.assertEqual(out.action, "booking_replayed")
        with Session(self.engine) as db:
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
            self.assertTrue(st.needs_human, "the Wild's precondition: needs_human after booking")
        self.assertIn(w.thread_id, self.selected(), "the parent query selects the Wild thread")
        self.assertEqual(self.predicate(w.thread_id), ledger(self.engine, w).id)

    def test_a02_every_near_miss_still_alerts(self):
        # (a) the customer writes again after the booking
        w, _ = wild_order(self.engine)
        with Session(self.engine) as db:
            db.add(WhatsAppMessage(thread_id=w.thread_id, direction="in", status="received",
                                   wa_message_id="wamid.W1F.AFTER", message_type="text",
                                   text="hola, una consulta",
                                   timestamp=datetime.now(timezone.utc) + timedelta(minutes=1)))
            db.commit()
        self.assertIsNone(self.predicate(w.thread_id))
        # (b) acknowledgement not SENT
        self.fresh()
        w, _ = wild_order(self.engine)
        with Session(self.engine) as db:
            db.execute(update(BookingCommandRecord).where(
                BookingCommandRecord.thread_id == w.thread_id).values(ack_status="FAILED"))
            db.commit()
        self.assertIsNone(self.predicate(w.thread_id))
        # (c) acknowledgement WAMID not an accepted outbound on this thread
        self.fresh()
        w, _ = wild_order(self.engine)
        with Session(self.engine) as db:
            db.execute(update(WhatsAppMessage).where(
                WhatsAppMessage.thread_id == w.thread_id, WhatsAppMessage.direction == "out")
                .values(status="failed"))
            db.commit()
        self.assertIsNone(self.predicate(w.thread_id))
        # (d) the ledger row belongs to another thread
        self.fresh()
        w, _ = wild_order(self.engine)
        with Session(self.engine) as db:
            db.execute(update(BookingCommandRecord).where(
                BookingCommandRecord.thread_id == w.thread_id).values(thread_id=w.thread_id + 1000))
            db.commit()
        self.assertIsNone(self.predicate(w.thread_id))
        # (e) a fallback / handoff Flow that booked nothing (no ledger row for its key)
        w = H.seed(self.engine, needs_human=True,
                   flow_data={"tipo_vehiculo": "Auto", "zona_general": "CABA"})
        self.assertIsNone(self.predicate(w.thread_id))
        # (f) a flow_response with no payload identity
        w = H.seed(self.engine, needs_human=True, with_token=False, flow_data={})
        self.assertIsNone(self.predicate(w.thread_id))
        # (g) a plain human handoff, no booking at all
        with Session(self.engine) as db:
            c = WhatsAppContact(wa_id="5491100990055")
            db.add(c); db.flush()
            t = WhatsAppThread(contact_id=c.id, unread_count=0)
            db.add(t); db.flush()
            db.add(WhatsAppThreadState(thread_id=t.id, needs_human=True))
            db.add(WhatsAppMessage(thread_id=t.id, direction="in", status="received",
                                   wa_message_id="wamid.W1F.HUMAN", message_type="text",
                                   text="necesito hablar con alguien",
                                   timestamp=datetime.now(timezone.utc)))
            db.commit()
            tid = t.id
        self.assertIn(tid, self.selected())
        self.assertIsNone(self.predicate(tid))

    def test_a03_a_detached_command_is_not_a_booking(self):
        w, _ = wild_order(self.engine)
        lid = ledger(self.engine, w).id
        with Session(self.engine) as db:      # the shape a tester reset leaves (CHECK-enforced)
            db.execute(update(BookingCommandRecord).where(BookingCommandRecord.id == lid).values(
                command_state="DETACHED", detached_at=datetime.now(timezone.utc),
                detached_reason="TESTER_RESET", revision_id=None, thread_revision_id=None,
                lead_id=None, thread_id=None, profesional_id=None, slot_start=None,
                slot_end=None, occupying=False))
            db.commit()
        self.assertIsNone(self.predicate(w.thread_id))

    def test_a04_the_decision_reads_no_prose_and_no_clock(self):
        import inspect
        src = inspect.getsource(UA.latest_inbound_is_acknowledged_booking_completion)
        body = src.split('"""', 2)[2]
        for forbidden in ("BOOKING_FLOW_RECEIPT_TEXT", "Gracias", ".text", "now(", "timedelta",
                          "seconds", "created_at"):
            self.assertNotIn(forbidden, body)


if __name__ == "__main__":
    unittest.main()
