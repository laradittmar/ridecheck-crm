"""Finding T-1, PostgreSQL half: the SQLite classes as they stand, on the real server.

  VEHICLE_PG_URL  a disposable database at this tree's head

Skips cleanly without it, and says so.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_vehicle_identity_recovery_pg import PG_URL, _OnPostgres  # noqa: E402

import test_vehicle_identity_t1_regression as T  # noqa: E402

NO_SERVER = "VEHICLE_PG_URL not set — no disposable PostgreSQL; nothing proven"


@unittest.skipUnless(PG_URL, NO_SERVER)
class PG_T1_BookingSelection(_OnPostgres, T.T1_BookingSelection):
    pass


@unittest.skipUnless(PG_URL, NO_SERVER)
class PG_T1_CandidateLessBooking(_OnPostgres, T.T1_CandidateLessBooking):
    pass


@unittest.skipUnless(PG_URL, NO_SERVER)
class PG_T1_ForeignThreadFocus(_OnPostgres, T.T1_ForeignThreadFocus):
    pass


if __name__ == "__main__":
    unittest.main()
