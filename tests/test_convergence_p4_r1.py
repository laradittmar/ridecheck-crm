"""Convergence Phase 4-R1 — operator delete and attempt identity, in the DEFAULT regression
gate (SQLite). The PostgreSQL proofs — real customer bookings, the encrypted Data Exchange
route, crash-redelivery, the database's admission result, the schema-order matrix — are in
`test_convergence_p4_r1_pg.py`; the Kanban refusal contract on SQLite is p4u_17/p4u_18 in
`test_convergence_p4.py`.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import os
import pathlib
import re
import unittest

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.services import booking_attempt_events as bae

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "backend" / "app"
VERSIONS = ROOT / "backend" / "migrations" / "versions"


def _b64(n: int) -> str:
    return base64.b64encode(os.urandom(n)).decode()


class P4R1_AttemptIdentity(unittest.TestCase):

    def test_p4r1u_01_one_delivery_one_identity_a_new_iv_a_new_one(self):
        iv, ct = _b64(16), _b64(300)
        a, b = bae.dx_attempt_ref(iv, ct), bae.dx_attempt_ref(iv, ct)
        self.assertEqual(a, b, "a byte-identical retry must be the same attempt")
        self.assertNotEqual(a, bae.dx_attempt_ref(_b64(16), ct), "a fresh IV is a new attempt")
        self.assertNotEqual(a, bae.dx_attempt_ref(iv, _b64(300)))
        self.assertRegex(a, r"^dx:[0-9a-f]{64}$")
        self.assertNotIn(iv, a); self.assertNotIn(ct[:24], a)
        # independently computed — a digest of the envelope, nothing else
        want = hashlib.sha256(b"dxe1\x00" + iv.encode() + b"\x00" + ct.encode()).hexdigest()
        self.assertEqual(a, "dx:" + want)

    def test_p4r1u_02_no_envelope_no_identity(self):
        for iv, ct in ((None, "x"), ("x", None), ("", "x"), ("x", ""), (1, "x"), ("x", b"y")):
            with self.subTest(iv=iv, ct=ct):
                self.assertIsNone(bae.dx_attempt_ref(iv, ct))

    def test_p4r1u_03_the_route_passes_the_envelope_digest_to_the_service(self):
        src = (APP / "routes" / "flow_data_exchange.py").read_text(encoding="utf-8")
        call = re.search(r"svc\.handle_confirm_booking\((.*?)\)\)\n", src, re.S)
        self.assertIsNotNone(call, "confirm_booking dispatch not found")
        self.assertIn("attempt_ref=dx_attempt_ref(", call.group(1))
        self.assertIn('"initial_vector"', call.group(1))
        self.assertIn('"encrypted_flow_data"', call.group(1))

    def test_p4r1u_04_admission_is_the_insert_returning_not_rowcount(self):
        src = (APP / "services" / "booking_attempt_events.py").read_text(encoding="utf-8")
        fn = src[src.index("def insert_attempt_event("):src.index("def record_attempt_event(")]
        code = "\n".join(l.split("#", 1)[0] for l in fn.split('"""', 2)[2].splitlines())
        self.assertNotIn(".rowcount", code)
        self.assertIn(".returning(", code)

    def test_p4r1u_05_sqlite_admission_sequence(self):
        from sqlalchemy.orm import Session
        from app.models import BookingAttemptEvent
        eng = create_engine("sqlite://", poolclass=StaticPool)
        BookingAttemptEvent.__table__.create(eng)
        e = dict(source="WHATSAPP_FLOW", stage="FINALISE", outcome="REFUSED",
                 attempt_ref="dx:one", once_per_attempt=True)
        with Session(eng) as s:
            got = (bae.insert_attempt_event(s, reason="A", **e),
                   bae.insert_attempt_event(s, reason="B", **e))
            s.commit()
            self.assertEqual(got, (True, False))
            self.assertEqual(s.execute(text("SELECT count(*) FROM booking_attempt_events"))
                             .scalar(), 1)


class P4R1_DeclaredSchema(unittest.TestCase):

    def test_p4r1u_10_the_column_is_declared_by_the_model(self):
        from app.models import ThreadRevision
        col = ThreadRevision.__table__.c.notification_sent_at
        self.assertTrue(col.nullable)
        self.assertTrue(getattr(col.type, "timezone", False))

    def test_p4r1u_11_no_application_code_creates_schema_any_more(self):
        hits = []
        for f in APP.rglob("*.py"):
            src = f.read_text(encoding="utf-8")
            if re.search(r"ADD COLUMN IF NOT EXISTS\s+notification_sent_at", src):
                hits.append(f.name)
        self.assertEqual(hits, [], "a second, silent owner of the column")
        main = (APP / "main.py").read_text(encoding="utf-8")
        self.assertIn("require_declared_booking_schema", main)

    def test_p4r1u_12_one_head_and_the_chain(self):
        revs = {}
        for f in VERSIONS.glob("*.py"):
            tree = ast.parse(f.read_text(encoding="utf-8"))
            vals = {}
            for node in tree.body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    tgt = node.targets[0] if isinstance(node, ast.Assign) else node.target
                    if isinstance(tgt, ast.Name) and tgt.id in ("revision", "down_revision"):
                        vals[tgt.id] = ast.literal_eval(node.value)
            if "revision" in vals:
                revs[vals["revision"]] = vals.get("down_revision")
        downs = set()
        for d in revs.values():
            downs.update(d if isinstance(d, (tuple, list)) else [d])
        heads = sorted(r for r in revs if r not in downs)
        self.assertEqual(heads, ["20261016_notification_sent_at"])
        self.assertEqual(revs["20261016_notification_sent_at"], "20261015_booking_attempt_events")
        self.assertEqual(revs["20261015_booking_attempt_events"], "20261014_ledger_detached_state")

    def test_p4r1u_13_the_preflight_refuses_a_schema_without_the_column(self):
        from app.services.schema_preflight import (DeclaredSchemaMissing,
                                                   missing_declared_booking_schema,
                                                   require_declared_booking_schema)
        eng = create_engine("sqlite://", poolclass=StaticPool)
        with eng.begin() as c:
            c.execute(text("CREATE TABLE thread_revisions (id INTEGER PRIMARY KEY)"))
        self.assertEqual(len(missing_declared_booking_schema(eng)), 1)
        with self.assertRaises(DeclaredSchemaMissing):
            require_declared_booking_schema(eng)
        with eng.begin() as c:
            c.execute(text("ALTER TABLE thread_revisions ADD COLUMN notification_sent_at "
                           "TIMESTAMP"))
        self.assertEqual(missing_declared_booking_schema(eng), [])
        require_declared_booking_schema(eng)

    def test_p4r1u_14_the_rollback_runbook_is_in_the_deployment_documents(self):
        manifest = (ROOT / "docs" / "operations" / "RECOVERY_MANIFEST.md").read_text(
            encoding="utf-8")
        section = manifest[manifest.index("## 6."):]
        for needle in ("booking_attempt_events", "notification_sent_at", "sha256sum",
                       "STOP", "owner checkpoint", "before the replacement application",
                       "alembic upgrade head"):
            self.assertIn(needle, section)
        preflight = (ROOT / "scripts" / "preflight_deploy.sh").read_text(encoding="utf-8")
        self.assertIn("RECOVERY_MANIFEST.md", preflight)


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
