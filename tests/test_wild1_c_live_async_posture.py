"""WILD1-C-R3 (review WC-04) — both WILD1-C suites, re-run in the LIVE asynchronous posture.

The independent review found that every WILD1-C test ran the interpreter synchronously, while
the live H-1 window runs SHADOW_UNDERSTAND_ASYNC=true (6.0 s same-turn timeout), so no
WILD1-C test could see WC-01. The synchronous suites are unchanged; this module subclasses
every test class of `test_wild1_c_conversation_correction` and `test_wild1_c_h1_regression`
and runs each test again with `_wild1c_harness.LIVE_ASYNC = True` — `cfg()` is then the live
posture, the interpreter runs on the shadow worker, and each turn waits for it (bounded)
exactly as production does. Interpreter latency here is the scripted seam's (immediate);
the delayed / timed-out / unavailable readings are `test_wild1_c_r3_async_and_operator_authority`.
"""
from __future__ import annotations

import unittest

import _wild1c_harness as W
import test_wild1_c_conversation_correction as _CC
import test_wild1_c_h1_regression as _H1


def _live(cls):
    class Live(cls):
        # A booking twin confirms a different day than its synchronous original: on a
        # shared PostgreSQL database both would otherwise ask for the same single-resource
        # slot (SQLite gives every test a fresh database, so it never showed).
        CONFIRM_DAYS_AHEAD = getattr(cls, "CONFIRM_DAYS_AHEAD", 1) + 1

        @classmethod
        def setUpClass(klass):
            W.LIVE_ASYNC = True
            try:
                super().setUpClass()
            except BaseException:
                W.LIVE_ASYNC = False
                raise

        @classmethod
        def tearDownClass(klass):
            try:
                super().tearDownClass()
            finally:
                W.LIVE_ASYNC = False

        def setUp(self):
            W.LIVE_ASYNC = True          # per test too: a sibling class may have reset it
            super().setUp()

    Live.__name__ = Live.__qualname__ = f"{cls.__name__}LiveAsync"
    Live.__module__ = __name__
    return Live


for _mod in (_CC, _H1):
    for _name, _obj in sorted(vars(_mod).items()):
        if (isinstance(_obj, type) and issubclass(_obj, unittest.TestCase)
                and _obj.__module__ == _mod.__name__):
            globals()[f"{_name}LiveAsync"] = _live(_obj)

del _mod, _name, _obj


class LivePostureIsReallyAsync(unittest.TestCase):
    def test_the_variant_runs_the_live_settings(self):
        W.LIVE_ASYNC = True
        try:
            c = W.cfg()
            self.assertIs(c.shadow_understand_async, True)
            self.assertEqual(c.semantic_same_turn_timeout_seconds, W.LIVE_TIMEOUT_SECONDS)
        finally:
            W.LIVE_ASYNC = False
        self.assertIs(W.cfg().shadow_understand_async, False)


if __name__ == "__main__":
    unittest.main()
