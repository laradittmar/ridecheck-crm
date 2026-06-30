"""Cleanup utility for SMOKETEST-prefixed fixture data in crm_test.

Deletes only rows whose identifying key (wa_id or lead name) starts with
'SMOKETEST_'. Never deletes by real phone number, arbitrary thread ID, or
production contact.

Usage:
    TEST_DATABASE_URL=postgresql+psycopg://crm:crm@localhost:5432/crm_test \
        python tests/cleanup_smoketest_fixtures.py

Guards (checked before any DB/app import):
  - TEST_DATABASE_URL must be set
  - database name in URL must be exactly 'crm_test'
  - OUTBOUND_ENABLED must not be 'true'
"""
import os
import sys
from urllib.parse import urlparse

# ── Fail-closed guards ────────────────────────────────────────────────────────
_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
if not _TEST_DB_URL:
    sys.exit("ABORT: TEST_DATABASE_URL is required. "
             "Set it to postgresql+psycopg://crm:crm@<host>:5432/crm_test")

_parsed_db = urlparse(_TEST_DB_URL)
_db_name = _parsed_db.path.lstrip("/")
if _db_name != "crm_test":
    sys.exit(f"ABORT: TEST_DATABASE_URL must target database 'crm_test', got '{_db_name}'. "
             "Refusing to clean any database other than crm_test.")

if os.environ.get("OUTBOUND_ENABLED") == "true":
    sys.exit("ABORT: OUTBOUND_ENABLED=true is set. "
             "This utility must never run with outbound enabled.")

from sqlalchemy import create_engine, text

engine = create_engine(_TEST_DB_URL)

SMOKETEST_PREFIX = "SMOKETEST_"

_CLEANUP_SQL = [
    ("whatsapp_messages",
     "thread_id IN (SELECT t.id FROM whatsapp_threads t "
     "JOIN whatsapp_contacts c ON c.id = t.contact_id "
     "WHERE c.wa_id LIKE :pfx)"),
    ("whatsapp_thread_candidates",
     "thread_id IN (SELECT t.id FROM whatsapp_threads t "
     "JOIN whatsapp_contacts c ON c.id = t.contact_id "
     "WHERE c.wa_id LIKE :pfx)"),
    ("whatsapp_thread_states",
     "thread_id IN (SELECT t.id FROM whatsapp_threads t "
     "JOIN whatsapp_contacts c ON c.id = t.contact_id "
     "WHERE c.wa_id LIKE :pfx)"),
    ("thread_revisions",
     "thread_id IN (SELECT t.id FROM whatsapp_threads t "
     "JOIN whatsapp_contacts c ON c.id = t.contact_id "
     "WHERE c.wa_id LIKE :pfx)"),
    ("whatsapp_threads",
     "contact_id IN (SELECT id FROM whatsapp_contacts WHERE wa_id LIKE :pfx)"),
    ("revisions",
     "lead_id IN (SELECT l.id FROM leads l WHERE l.nombre = 'Test' AND l.apellido IN ('Fallback', 'Smoke'))"),
    ("leads",
     "nombre = 'Test' AND apellido IN ('Fallback', 'Smoke')"),
    ("whatsapp_contacts",
     "wa_id LIKE :pfx"),
]


def main() -> None:
    prefix_pattern = SMOKETEST_PREFIX + "%"
    total_deleted = 0

    with engine.begin() as conn:
        for table, where_clause in _CLEANUP_SQL:
            result = conn.execute(
                text(f"DELETE FROM {table} WHERE {where_clause}"),
                {"pfx": prefix_pattern},
            )
            n = result.rowcount
            if n > 0:
                print(f"  deleted {n:4d} rows from {table}")
                total_deleted += n

    print(f"\nTotal deleted: {total_deleted} rows (SMOKETEST fixtures only, database={_db_name})")


if __name__ == "__main__":
    main()
