"""Convergence Phase 4 STRICT-SCHEMA-ADOPTION — the parts that need no PostgreSQL.

The behaviour (every refusal, every adoption, the runtime preflight on real catalogues) is
proven in `test_convergence_p4_strict_schema_pg.py`; here: the preflight's head literal is the
script directory's single head, its fingerprint literals are the migrations' own, the SQLite
branch stays a presence check, and `differences` names what differs.
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def _migration(name):
    spec = importlib.util.spec_from_file_location(
        f"_ssau_{name}", BACKEND / "migrations" / "versions" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class StrictSchemaLiterals(unittest.TestCase):

    def test_ssau_01_the_preflight_head_is_the_single_script_head(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from app.services.schema_preflight import EXPECTED_HEAD
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND / "migrations"))
        self.assertEqual(ScriptDirectory.from_config(cfg).get_heads(), [EXPECTED_HEAD])

    def test_ssau_02_preflight_literals_are_the_migrations_literals(self):
        from app.services import schema_preflight as P
        m15 = _migration("20261015_booking_attempt_events")
        m16 = _migration("20261016_notification_sent_at")
        self.assertEqual(P._EVENTS_CANONICAL, m15.F_CANONICAL)
        self.assertEqual(P._EVENTS_FUNCTION, m15.F_FUNCTION)
        self.assertEqual(set(P._NOTIFICATION_PERMITTED), {m16._LEGACY, m16._ADDED})
        # the ORM shape is the canonical one minus the enforcement, nothing else
        self.assertEqual({k for k in m15.F_ORM if m15.F_ORM[k] != m15.F_CANONICAL[k]},
                         {"triggers"})
        self.assertEqual(m15.F_ORM["triggers"], [])
        # the legacy column has no default, is nullable, and differs from the added one
        # only by the marker
        legacy = dict(m16._LEGACY)
        self.assertEqual((legacy["default"], legacy["not_null"], legacy["identity"],
                          legacy["generated"], legacy["comment"]), (None, False, "", "", None))
        self.assertEqual(dict(m16._ADDED), {**legacy, "comment": m16.MARKER})

    def test_ssau_03_the_upgrade_chain_runs_in_one_transaction(self):
        env = (BACKEND / "migrations" / "env.py").read_text(encoding="utf-8")
        self.assertNotIn("transaction_per_migration", env)
        self.assertEqual(env.count("context.begin_transaction()"), 2)   # offline + online

    def test_ssau_04_sqlite_branch_is_a_presence_check_only(self):
        from app.services.schema_preflight import missing_declared_booking_schema
        eng = create_engine("sqlite://", poolclass=StaticPool)
        with eng.begin() as c:
            c.execute(text("CREATE TABLE thread_revisions (id INTEGER PRIMARY KEY)"))
        self.assertEqual(len(missing_declared_booking_schema(eng)), 1)
        with eng.begin() as c:
            c.execute(text("ALTER TABLE thread_revisions ADD COLUMN notification_sent_at "
                           "TIMESTAMP"))
        self.assertEqual(missing_declared_booking_schema(eng), [])

    def test_ssau_05_differences_names_missing_unexpected_and_changed_parts(self):
        from app.services.schema_fingerprint import differences
        self.assertEqual(differences({"a": [1, 2]}, {"a": [1, 2]}), [])
        self.assertEqual(differences({"a": [1]}, None), ["object absent"])
        self.assertEqual(differences({"a": [1, 2]}, {"a": [2, 3]}),
                         ["a: missing [1]; unexpected [3]"])
        self.assertEqual(differences({"a": [1, 2]}, {"a": [2, 1]}),
                         ["a: order differs; expected [1, 2]; found [2, 1]"])
        self.assertEqual(differences({"t": ("x",)}, {"t": ("y",), "u": 1}),
                         ["t: expected ('x',); found ('y',)",
                          "u: expected '<not expected>'; found 1"])

    def test_ssau_06_a_server_other_than_16_is_refused(self):
        from app.services.schema_fingerprint import (SchemaShapeRefused,
                                                     require_certified_server)

        class _Conn:
            def __init__(self, v):
                self.v = v

            def execute(self, _):
                return self

            def scalar(self):
                return self.v

        require_certified_server(_Conn("160013"), "probe")
        for v in ("150008", "170002"):
            with self.subTest(server=v), self.assertRaises(SchemaShapeRefused):
                require_certified_server(_Conn(v), "probe")


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
