"""OPS-CONTROL — the Wild observer needs to filter a trace and read a path.

Two things made W3 harder to audit than it should have been: the message trace could not be
narrowed to one conversation, and the CAMINO column rendered a bare dash for an inbound
message and for an unattributed outbound one alike — the second of which is a forensic
finding, not a blank.

CONTROL-01 customer filter        CONTROL-07 CE_FLOW / BOOKING_FLOW registered
CONTROL-02 direction + customer   CONTROL-08 unattributed outbound is visible
CONTROL-03 path filter            CONTROL-09 inbound gets no send path
CONTROL-04 legend from registry   CONTROL-10 counts match source data
CONTROL-05 MANUAL_CRM classified  CONTROL-11 no conversation behaviour change
CONTROL-06 CE_TEXT classified
"""
from __future__ import annotations

import ast
import pathlib
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

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

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.models import (  # noqa: E402
    Base,
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppThread,
)
from app.routes.ops_dashboard import (  # noqa: E402
    _path_class,
    _path_display,
    get_messages,
    get_path_registry,
    get_paths,
)
from app.services.outbound_path_registry import OutboundPathId  # noqa: E402

CONTROL_VIEW = (ROOT / "backend" / "app" / "ui" / "control_view.py").read_text(encoding="utf-8")
CE_SOURCE = (ROOT / "backend" / "app" / "services"
             / "conversation_engine.py").read_text(encoding="utf-8-sig")


def build_db():
    """Two customers, one conversation each, covering every path class."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)

    def contact(cid, wa, name):
        c = WhatsAppContact(id=cid, wa_id=wa, display_name=name)
        db.add(c)
        t = WhatsAppThread(id=cid + 100, contact_id=cid, last_message_at=now)
        db.add(t)
        return t

    t_a = contact(1, "5491100000001", "Cliente A")
    t_b = contact(2, "5491100000002", "Cliente B")
    db.flush()

    def msg(mid, thread, direction, path, automated, status="read", offset=1):
        db.add(WhatsAppMessage(
            id=mid, thread_id=thread.id, direction=direction, message_type="text",
            text="x", path_id=path, automated=automated, status=status,
            timestamp=now - timedelta(minutes=offset)))

    msg(1, t_a, "in", None, False)
    msg(2, t_a, "out", "CE_TEXT", True)
    msg(3, t_a, "out", "MANUAL_CRM", True)
    msg(4, t_a, "out", None, True)              # unattributed — the forensic case
    msg(5, t_b, "in", None, False)
    msg(6, t_b, "out", "CE_TEXT", True)
    msg(7, t_b, "out", "BOOKING_FLOW", True, status="blocked")
    db.commit()
    return db


class TestTraceFilters(unittest.TestCase):

    def setUp(self):
        self.db = build_db()

    def tearDown(self):
        self.db.close()

    def fetch(self, **kw):
        """Call the endpoint directly, resolving every FastAPI Query default explicitly."""
        params = {"window": "24h", "direction": None, "thread_id": None,
                  "contact_id": None, "path_id": None, "limit": 500, "db": self.db}
        params.update(kw)
        return get_messages(**params)

    def ids(self, **kw):
        return sorted(m["id"] for m in self.fetch(**kw)["messages"])

    def test_control_01_customer_filter(self):
        """CONTROL-01 — one customer's conversation only."""
        self.assertEqual(self.ids(contact_id=1), [1, 2, 3, 4])
        self.assertEqual(self.ids(contact_id=2), [5, 6, 7])
        self.assertEqual(len(self.ids()), 7, "default is every message")

    def test_control_02_direction_and_customer_combine(self):
        """CONTROL-02 — Cliente A + OUT, the example from the brief."""
        self.assertEqual(self.ids(contact_id=1, direction="out"), [2, 3, 4])
        self.assertEqual(self.ids(contact_id=1, direction="in"), [1])
        self.assertEqual(self.ids(contact_id=2, direction="out"), [6, 7])

    def test_control_03_path_filter_and_triple_combination(self):
        """CONTROL-03 — customer AND direction AND path all narrow together."""
        self.assertEqual(self.ids(path_id="CE_TEXT"), [2, 6])
        self.assertEqual(self.ids(path_id="MANUAL_CRM"), [3])
        self.assertEqual(self.ids(contact_id=1, path_id="CE_TEXT"), [2])
        self.assertEqual(self.ids(contact_id=1, direction="out", path_id="CE_TEXT"), [2])
        self.assertEqual(self.ids(contact_id=2, path_id="MANUAL_CRM"), [])

    def test_control_08_unattributed_outbound_is_findable_and_visible(self):
        """CONTROL-08 — an outbound row with no path must never read as a blank."""
        self.assertEqual(self.ids(path_id="UNKNOWN"), [4])
        row = next(m for m in self.fetch()["messages"] if m["id"] == 4)
        self.assertEqual(row["path_display"], "UNATTRIBUTED")
        self.assertEqual(row["path_class"], "unknown")

    def test_control_09_inbound_never_carries_a_send_path(self):
        """CONTROL-09 — inbound is labelled INBOUND, not dashed like a missing path."""
        rows = {m["id"]: m for m in self.fetch()["messages"]}
        for mid in (1, 5):
            self.assertEqual(rows[mid]["path_display"], "INBOUND")
            self.assertEqual(rows[mid]["path_class"], "inbound")
            self.assertIsNone(rows[mid]["path_id"])
        self.assertEqual(self.ids(path_id="UNKNOWN"), [4],
                         "inbound rows must not be swept up as unattributed outbound")

    def test_control_05_and_06_known_paths_are_classified(self):
        """CONTROL-05/06 — MANUAL_CRM and CE_TEXT render as themselves, authorized."""
        rows = {m["id"]: m for m in self.fetch()["messages"]}
        self.assertEqual(rows[3]["path_display"], "MANUAL_CRM")
        self.assertEqual(rows[3]["path_class"], "authorized")
        self.assertEqual(rows[2]["path_display"], "CE_TEXT")
        self.assertEqual(rows[2]["path_class"], "authorized")

    def test_an_unregistered_path_is_named_and_flagged(self):
        self.assertEqual(_path_display("out", "SOMETHING_NEW"), "UNKNOWN (SOMETHING_NEW)")
        self.assertEqual(_path_class("out", "SOMETHING_NEW"), "unknown")
        self.assertEqual(_path_class("out", "LEGACY_N8N_AI_PIPELINE"), "legacy")

    def test_control_10_counts_match_source_data(self):
        """CONTROL-10 — and TOTAL is actually emitted, which it never was."""
        paths = {p["path_id"]: p for p in get_paths(window="24h", db=self.db)["paths"]}
        self.assertEqual(paths["CE_TEXT"]["count"], 2)
        self.assertEqual(paths["CE_TEXT"]["total"], 2, "the UI reads `total`")
        self.assertEqual(paths["CE_TEXT"]["success_count"], 2)
        self.assertEqual(paths["BOOKING_FLOW"]["blocked_count"], 1)
        self.assertEqual(paths["BOOKING_FLOW"]["success_count"], 0)
        self.assertEqual(paths["UNKNOWN"]["count"], 1, "the unattributed row is counted")
        self.assertEqual(paths["UNKNOWN"]["is_critical"], True)
        for entry in paths.values():
            self.assertEqual(entry["total"], entry["count"])


class TestPathLegend(unittest.TestCase):

    def test_control_04_legend_comes_from_the_live_registry(self):
        """CONTROL-04 — every registered path appears, and nothing invented does."""
        registry = get_path_registry()
        listed = {e["path_id"] for e in registry["paths"]}
        self.assertEqual(listed, {m.value for m in OutboundPathId},
                         "the legend is the registry, not a copy of it")
        self.assertEqual(registry["count"], len(listed))
        for entry in registry["paths"]:
            for field in ("label", "initiator", "purpose", "kind", "authority"):
                self.assertTrue(entry[field], f"{entry['path_id']} missing {field}")
            self.assertIn(entry["kind"], ("AUTOMATED", "HUMAN"))

    def test_control_05b_manual_crm_is_described_as_human(self):
        entry = next(e for e in get_path_registry()["paths"] if e["path_id"] == "MANUAL_CRM")
        self.assertEqual(entry["kind"], "HUMAN")
        self.assertTrue(entry["authorized"])
        self.assertIn("operador", entry["initiator"].lower())

    def test_control_07_ce_flow_and_booking_flow_are_registered_and_authorized(self):
        entries = {e["path_id"]: e for e in get_path_registry()["paths"]}
        for pid in ("CE_FLOW", "BOOKING_FLOW", "CE_INTERACTIVE", "CE_LIST",
                    "SYSTEM_NOTIFICATION"):
            self.assertIn(pid, entries)
            self.assertTrue(entries[pid]["authorized"], pid)
        self.assertIn("turno", entries["BOOKING_FLOW"]["purpose"].lower())

    def test_a_retired_path_is_shown_but_marked_blocked(self):
        entries = {e["path_id"]: e for e in get_path_registry()["paths"]}
        legacy = entries.get("LEGACY_N8N_AI_PIPELINE")
        self.assertIsNotNone(legacy, "a registered legacy path must still be shown")
        self.assertFalse(legacy["authorized"])
        self.assertTrue(legacy["legacy"])
        self.assertIn("BLOQUEADO", legacy["authority"])


class TestUiWiring(unittest.TestCase):

    def test_the_filters_and_legend_exist_in_the_page(self):
        for marker in ("customerFilter", "pathFilter", "clearTraceFilters",
                       "filterTraceByCustomer", "filterTraceByPath",
                       "pathLegend", "togglePathLegend", "/api/ops/path-registry"):
            self.assertIn(marker, CONTROL_VIEW, marker)

    def test_the_trace_request_carries_every_filter(self):
        self.assertIn("&contact_id=", CONTROL_VIEW)
        self.assertIn("&path_id=", CONTROL_VIEW)
        self.assertIn("&direction=", CONTROL_VIEW)

    def test_no_full_phone_number_is_rendered_in_the_customer_options(self):
        """Privacy: the dropdown uses display_name or the masked id, never wa_id."""
        start = CONTROL_VIEW.index("function fetchCustomerOptions")
        body = CONTROL_VIEW[start:CONTROL_VIEW.index("function fetchPathRegistry")]
        self.assertIn("wa_id_masked", body)
        self.assertNotIn("r.wa_id ", body)


class TestNoBehaviourChange(unittest.TestCase):

    def test_control_11_conversation_engine_untouched_by_this_milestone(self):
        """CONTROL-11 — an observability change must not reach the engine."""
        for marker in ("customerFilter", "pathFilter", "path_display", "path_class",
                       "path-registry"):
            self.assertNotIn(marker, CE_SOURCE, f"{marker} leaked into the engine")
        # the invariants the dashboard reports on are still the engine's
        self.assertIn("OutboundPathId.MANUAL_CRM.value", CE_SOURCE + (
            ROOT / "backend" / "app" / "ui" / "whatsapp_ui.py").read_text(encoding="utf-8-sig"))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
