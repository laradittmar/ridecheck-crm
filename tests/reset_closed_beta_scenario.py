"""Safe closed-beta scenario reset tool.

Clears conversation state for the approved closed-beta tester so the next
inbound message arrives as a fresh qualification. Outbound safety history
(whatsapp_outbound_dedup, whatsapp_recipient_locks) is never modified unless
the explicit --clear-beta-cooldown flag is also provided.

Usage — standard reset:
    CLOSED_BETA_TEST_WA_ID=<wa_id> \\
    TEST_DATABASE_URL=postgresql+psycopg://crm:${POSTGRES_PASSWORD}@localhost:5432/crm_test \\
    python tests/reset_closed_beta_scenario.py --confirm

Usage — cooldown reset (also clears tester's dedup records):
    CLOSED_BETA_TEST_WA_ID=<wa_id> \\
    TEST_DATABASE_URL=postgresql+psycopg://crm:${POSTGRES_PASSWORD}@localhost:5432/crm_test \\
    python tests/reset_closed_beta_scenario.py --confirm --clear-beta-cooldown

Mandatory guards (all evaluated before any DB connection):
  - --confirm flag must be present on the command line
  - OUTBOUND_ENABLED must not be "true"
  - TEST_DATABASE_URL must be set
  - database name in TEST_DATABASE_URL must be exactly "crm_test"
  - CLOSED_BETA_TEST_WA_ID must be set via environment variable
  - backend must not be listening on 127.0.0.1:8000
  - n8n must not be listening on 127.0.0.1:5678

Standard reset scope (for all threads belonging to the tester's contact):
  DELETE  whatsapp_messages         (conversation messages incl. blocked/sent audit)
  DELETE  thread_revisions          (in-progress booking revisions)
  DELETE  whatsapp_thread_candidates (vehicle/service candidates)
  DELETE  whatsapp_thread_states    (all AI conversation state)
  DELETE  ai_events                 (queued/processed AI dispatch events)
  RESET   leads                     flag=NULL, estado='CONSULTA_NUEVA',
                                    motivo_perdida=NULL, necesita_humano=FALSE,
                                    buscando_auto_set_at=NULL
  RESET   whatsapp_threads          unread_count=0, last_message_at=NULL,
                                    latest_inbound_wa_message_id=NULL

Preserved by standard reset (never touched):
  whatsapp_contacts         (tester identity kept)
  whatsapp_threads          (structure kept; fields above reset)
  whatsapp_outbound_dedup   (safety gate history — flood/dedup guards preserved)
  whatsapp_recipient_locks  (serialisation state preserved)

Additional deletions when --clear-beta-cooldown is also provided:
  DELETE  whatsapp_outbound_dedup   WHERE wa_id = CLOSED_BETA_TEST_WA_ID only
  (recipient locks are NOT deleted even in cooldown mode)

The --clear-beta-cooldown flag is a beta-only convenience that lets the tester
immediately re-send an identical test scenario without waiting for the 10-minute
dedup window. It is never available against a database other than crm_test.
"""
from __future__ import annotations

import os
import socket
import sys
from urllib.parse import urlparse

# ── Guard block — runs before any DB import ───────────────────────────────────

def _abort(reason: str) -> None:
    print(f"ABORT: {reason}", file=sys.stderr)
    sys.exit(1)


if "--confirm" not in sys.argv:
    _abort(
        "--confirm flag is required. Read the module docstring and "
        "understand the reset scope before running this tool."
    )

if os.environ.get("OUTBOUND_ENABLED", "").strip().lower() == "true":
    _abort("OUTBOUND_ENABLED=true detected. Reset is only safe when outbound is disabled.")

_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
if not _TEST_DB_URL:
    _abort(
        "TEST_DATABASE_URL is not set. "
        "This tool only runs against a test database (crm_test)."
    )

_parsed_url = urlparse(_TEST_DB_URL)
_db_name = _parsed_url.path.lstrip("/")
if _db_name != "crm_test":
    _abort(
        f"Database name in TEST_DATABASE_URL is '{_db_name}', not 'crm_test'. "
        "Refusing to modify any database other than crm_test."
    )

_TESTER_WA_ID = os.environ.get("CLOSED_BETA_TEST_WA_ID", "").strip()
if not _TESTER_WA_ID:
    _abort(
        "CLOSED_BETA_TEST_WA_ID is not set. "
        "Provide the tester wa_id via environment variable only — never as a CLI argument."
    )

# Cooldown mode: set only when explicitly requested.
_CLEAR_COOLDOWN = "--clear-beta-cooldown" in sys.argv


def _check_port_closed(host: str, port: int, service_name: str) -> None:
    try:
        sock = socket.create_connection((host, port), timeout=1.0)
        sock.close()
        _abort(
            f"{service_name} appears to be running at {host}:{port}. "
            "Stop all backend and n8n processes before running this tool."
        )
    except (ConnectionRefusedError, OSError):
        pass


_check_port_closed("127.0.0.1", 8000, "backend (port 8000)")
_check_port_closed("127.0.0.1", 5678, "n8n (port 5678)")

# ── All guards passed — connect and reset ─────────────────────────────────────

from sqlalchemy import create_engine, text  # noqa: E402

_engine = create_engine(_TEST_DB_URL, echo=False)

_WA_SUFFIX = _TESTER_WA_ID[-4:]

_RESET_SQL: list[tuple[str, str]] = [
    # Child tables first (FK order); AiEvents last (no FK from thread).
    (
        "whatsapp_messages",
        "thread_id IN (SELECT id FROM whatsapp_threads WHERE contact_id = :contact_id)",
    ),
    (
        "thread_revisions",
        "thread_id IN (SELECT id FROM whatsapp_threads WHERE contact_id = :contact_id)",
    ),
    (
        "whatsapp_thread_candidates",
        "thread_id IN (SELECT id FROM whatsapp_threads WHERE contact_id = :contact_id)",
    ),
    (
        "whatsapp_thread_states",
        "thread_id IN (SELECT id FROM whatsapp_threads WHERE contact_id = :contact_id)",
    ),
    (
        "ai_events",
        "thread_id IN (SELECT id FROM whatsapp_threads WHERE contact_id = :contact_id)",
    ),
]


def main() -> None:
    wa_suffix = _WA_SUFFIX
    mode = "cooldown-reset" if _CLEAR_COOLDOWN else "standard-reset"

    with _engine.begin() as conn:
        # Resolve contact_id — must exist.
        row = conn.execute(
            text("SELECT id FROM whatsapp_contacts WHERE wa_id = :wa_id"),
            {"wa_id": _TESTER_WA_ID},
        ).fetchone()
        if row is None:
            print(f"No contact found for wa_id=...{wa_suffix}. Nothing to reset.")
            return
        contact_id: int = row[0]

        # Count threads before touching anything.
        thread_rows = conn.execute(
            text("SELECT id, lead_id FROM whatsapp_threads WHERE contact_id = :cid"),
            {"cid": contact_id},
        ).fetchall()
        if not thread_rows:
            print(f"No threads found for contact_id={contact_id} (wa_id=...{wa_suffix}). Nothing to reset.")
            return

        thread_ids = [r[0] for r in thread_rows]
        lead_ids = [r[1] for r in thread_rows if r[1] is not None]

        print(f"[{mode}] Resetting {len(thread_ids)} thread(s) for wa_id=...{wa_suffix}")

        # Delete conversation data.
        for table, where_clause in _RESET_SQL:
            result = conn.execute(
                text(f"DELETE FROM {table} WHERE {where_clause}"),
                {"contact_id": contact_id},
            )
            if result.rowcount:
                print(f"  deleted {result.rowcount:4d} rows from {table}")

        # Reset Lead fields to fresh qualification state.
        for lead_id in lead_ids:
            result = conn.execute(
                text(
                    "UPDATE leads SET "
                    "  flag = NULL, "
                    "  estado = 'CONSULTA_NUEVA', "
                    "  motivo_perdida = NULL, "
                    "  necesita_humano = FALSE, "
                    "  buscando_auto_set_at = NULL "
                    "WHERE id = :lead_id"
                ),
                {"lead_id": lead_id},
            )
            if result.rowcount:
                print(f"  reset lead_id={lead_id} → CONSULTA_NUEVA")

        # Reset WhatsAppThread metadata (keep structure).
        conn.execute(
            text(
                "UPDATE whatsapp_threads SET "
                "  unread_count = 0, "
                "  last_message_at = NULL, "
                "  latest_inbound_wa_message_id = NULL "
                "WHERE contact_id = :contact_id"
            ),
            {"contact_id": contact_id},
        )
        print(f"  reset {len(thread_ids)} whatsapp_thread(s) metadata")

        # Cooldown mode: also delete tester's dedup records.
        if _CLEAR_COOLDOWN:
            result = conn.execute(
                text("DELETE FROM whatsapp_outbound_dedup WHERE wa_id = :wa_id"),
                {"wa_id": _TESTER_WA_ID},
            )
            print(
                f"  [cooldown] deleted {result.rowcount} dedup record(s) for wa_id=...{wa_suffix} "
                f"— duplicate cooldown lifted"
            )
            print("  [cooldown] recipient_locks NOT deleted — serialisation state preserved")

    if _CLEAR_COOLDOWN:
        print(
            f"\nDone. CRM is in fresh qualification state for wa_id=...{wa_suffix} "
            "(dedup cooldown cleared).\n"
            "Preserved: whatsapp_recipient_locks, whatsapp_contact, whatsapp_thread (structure).\n"
            "Removed:   whatsapp_outbound_dedup rows for this tester only."
        )
    else:
        print(
            f"\nDone. CRM is in fresh qualification state for wa_id=...{wa_suffix}.\n"
            "Preserved: whatsapp_outbound_dedup, whatsapp_recipient_locks, "
            "whatsapp_contact, whatsapp_thread (structure)."
        )


if __name__ == "__main__":
    main()
