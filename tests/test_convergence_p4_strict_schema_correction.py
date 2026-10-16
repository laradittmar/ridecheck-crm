"""Convergence Phase 4 STRICT-SCHEMA-ADOPTION-CORRECTION — the parts that need no PostgreSQL.

The accepted CHECK forms are a finite, frozen set: exactly two exact deparses per constraint
(as created; as `pg_dump` → restore re-reads it), equal to hand-written literals here, varying
in nothing but those two deparse fields; the preflight's copy is the migrations'; `matching`
is exact equality. Behaviour on real catalogues is in
`test_convergence_p4_strict_schema_correction_pg.py`.
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

_STAGE, _OUTCOME = "ck_booking_attempt_events_stage", "ck_booking_attempt_events_outcome"
#: Hand-written: what PostgreSQL 16.13 printed for the restored constraints (closeout §4).
_RESTORED = {
    _OUTCOME: "CHECK (((outcome)::text = ANY (ARRAY[('REFUSED'::character varying)::text, "
              "('RETRYABLE'::character varying)::text, ('CONTENDED'::character varying)::text, "
              "('INTERNAL'::character varying)::text, ('FAILED'::character varying)::text, "
              "('HELD'::character varying)::text])))",
    _STAGE: "CHECK (((stage)::text = ANY (ARRAY[('PRE_EXECUTE'::character varying)::text, "
            "('AUTHORITY'::character varying)::text, ('FINALISE'::character varying)::text, "
            "('NOTIFICATION'::character varying)::text])))",
}
_CREATED = {
    _OUTCOME: "CHECK (((outcome)::text = ANY ((ARRAY['REFUSED'::character varying, "
              "'RETRYABLE'::character varying, 'CONTENDED'::character varying, "
              "'INTERNAL'::character varying, 'FAILED'::character varying, "
              "'HELD'::character varying])::text[])))",
    _STAGE: "CHECK (((stage)::text = ANY ((ARRAY['PRE_EXECUTE'::character varying, "
            "'AUTHORITY'::character varying, 'FINALISE'::character varying, "
            "'NOTIFICATION'::character varying])::text[])))",
}


def _migration(name):
    spec = importlib.util.spec_from_file_location(
        f"_sscu_{name}", BACKEND / "migrations" / "versions" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _deparses(shape):
    return {c[0]: c[-1] for c in shape["constraints"] if c[1] == "c"}


def _without_check_deparses(shape):
    return {**shape, "constraints": [c[:-1] if c[1] == "c" else c
                                     for c in shape["constraints"]]}


class AcceptedCheckForms(unittest.TestCase):

    def setUp(self):
        self.m15 = _migration("20261015_booking_attempt_events")

    def test_sscu_01_exactly_two_hand_checked_forms_per_constraint_and_nothing_else(self):
        m = self.m15
        self.assertEqual(_deparses(m.F_ORM), _CREATED)
        self.assertEqual(_deparses(m.F_CANONICAL), _CREATED)
        for base, permitted in ((m.F_ORM, m.F_ORM_PERMITTED),
                                (m.F_CANONICAL, m.F_CANONICAL_PERMITTED)):
            self.assertEqual(len(permitted), 4)
            self.assertEqual(permitted[0], base, "the creation form must come first")
            self.assertEqual(len({repr(p) for p in permitted}), 4)
            for p in permitted:
                # varies ONLY in the two CHECK deparse fields; every other field is exact
                self.assertEqual(_without_check_deparses(p), _without_check_deparses(base))
                for name, form in _deparses(p).items():
                    self.assertIn(form, (_CREATED[name], _RESTORED[name]))
            self.assertEqual({tuple(sorted(_deparses(p).items())) for p in permitted},
                             {((_OUTCOME, o), (_STAGE, s))
                              for o in (_CREATED[_OUTCOME], _RESTORED[_OUTCOME])
                              for s in (_CREATED[_STAGE], _RESTORED[_STAGE])})

    def test_sscu_02_the_preflight_permits_exactly_the_migrations_set(self):
        from app.services import schema_preflight as P
        m16 = _migration("20261016_notification_sent_at")
        self.assertEqual(P._EVENTS_PERMITTED, self.m15.F_CANONICAL_PERMITTED)
        self.assertEqual(P._NOTIFICATION_TABLE_LEVEL, m16._TABLE_LEVEL)
        self.assertEqual(m16._TABLE_LEVEL["column_constraints"], [])
        self.assertEqual(m16._TABLE_LEVEL["column_indexes"], [])
        self.assertEqual(dict(m16._TABLE_LEVEL["table_inheritance"]),
                         {"relkind": "r", "partition": False, "inherits_from": 0,
                          "inherited_by": 0})

    def test_sscu_03_matching_is_whole_fingerprint_equality(self):
        from app.services.schema_fingerprint import closest_differences, matching
        m = self.m15
        restored = {**m.F_CANONICAL, "constraints": [
            c[:-1] + (_RESTORED[c[0]],) if c[1] == "c" else c
            for c in m.F_CANONICAL["constraints"]]}
        self.assertEqual(matching(m.F_CANONICAL_PERMITTED, restored), restored)
        self.assertIsNone(matching(m.F_ORM_PERMITTED, restored))
        self.assertIsNone(matching(m.F_CANONICAL_PERMITTED, None))
        # a restored deparse under the OTHER constraint's name is not a permitted form
        swapped = {**m.F_CANONICAL, "constraints": [
            c[:-1] + (_RESTORED[_STAGE if c[0] == _OUTCOME else _OUTCOME],) if c[1] == "c"
            else c for c in m.F_CANONICAL["constraints"]]}
        self.assertIsNone(matching(m.F_CANONICAL_PERMITTED, swapped))
        # one character of difference anywhere else refuses too
        for part in ("columns", "indexes", "triggers"):
            with self.subTest(part=part):
                changed = {**restored, part: restored[part][:-1]}
                self.assertIsNone(matching(m.F_CANONICAL_PERMITTED, changed))
                self.assertTrue(closest_differences(m.F_CANONICAL_PERMITTED, changed))
        self.assertEqual(closest_differences(m.F_CANONICAL_PERMITTED, restored), [])


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
