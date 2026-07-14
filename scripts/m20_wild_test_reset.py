#!/usr/bin/env python3
"""
m20_wild_test_reset.py — Tester clean-state reset for M20 wild phone conversations.

Resets ALL test data for the single allowlisted tester (suffix ...8330) in crm_test:
  - all threads (and their messages / states / candidates / thread_revisions / dedup / ai_events)
  - all revisions and feedback rows for tester leads
  - the tester lead(s) themselves

Preserves:
  - the tester WhatsApp contact record (n8n needs it for identity lookup)
  - all non-tester data (real customers, other leads, other threads)
  - production crm database (never touched)

Safety guards:
  - Refuses to run against any DATABASE_URL that does not end in 'crm_test'
  - Refuses if more than one contact matches the allowlisted suffix
  - Refuses if target contact cannot be identified uniquely
  - Creates a pg_dump backup before any mutation
  - Runs mutations inside a single DB transaction
  - Supports --dry-run (default) and --execute
  - Prints before/after counts for every affected table

Usage:
  python3 scripts/m20_wild_test_reset.py --dry-run    # inspect only (default)
  python3 scripts/m20_wild_test_reset.py --execute    # perform reset with backup
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone

# ── Constants ──────────────────────────────────────────────────────────────────
ALLOWED_SUFFIX = "8330"
REQUIRED_DB_SUFFIX = "crm_test"

# ── Argument parsing ───────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="M20 wild test clean-state reset")
mode = parser.add_mutually_exclusive_group()
mode.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                  help="Inspect state only — do not modify data (default)")
mode.add_argument("--execute", dest="dry_run", action="store_false",
                  help="Perform the reset (creates backup first unless --skip-backup)")
parser.add_argument("--skip-backup", dest="skip_backup", action="store_true", default=False,
                    help="Skip pg_dump backup (use when backup was taken externally)")
args = parser.parse_args()
DRY_RUN = args.dry_run
SKIP_BACKUP = args.skip_backup

# ── Import sqlalchemy after arg parse so --help works without deps ─────────────
try:
    from sqlalchemy import create_engine, text
except ImportError:
    print("ERROR: sqlalchemy not installed. Run: pip3 install sqlalchemy psycopg")
    sys.exit(1)

# ── Database URL guard ─────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    # Fall back to reading from .env in /opt/ridecheck-crm
    env_path = "/opt/ridecheck-crm/.env"
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    DATABASE_URL = line.split("=", 1)[1].strip()
                    break

if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set and not found in /opt/ridecheck-crm/.env")
    sys.exit(1)

if not DATABASE_URL.endswith(REQUIRED_DB_SUFFIX):
    print(f"ERROR: DATABASE_URL does not target '{REQUIRED_DB_SUFFIX}' — refusing to run.")
    print(f"  DATABASE_URL ends with: ...{DATABASE_URL[-20:]}")
    sys.exit(1)


def _psql_url(db_url: str) -> str:
    """Convert sqlalchemy URL to psql-compatible URL for pg_dump."""
    return db_url.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


def take_backup(db_url: str, tag: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = f"/tmp/crm_test_backup_{tag}_{ts}.sql"
    psql_url = _psql_url(db_url)
    result = subprocess.run(
        ["pg_dump", psql_url, "-f", path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ERROR: pg_dump failed: {result.stderr.strip()}")
        sys.exit(1)
    size = os.path.getsize(path)
    print(f"  Backup written: {path} ({size // 1024}K)")
    return path


def count(conn, table: str, where: str, params: dict) -> int:
    row = conn.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"), params).fetchone()
    return row[0]


print("═" * 60)
print(f"  M20 WILD TEST RESET — {'DRY RUN' if DRY_RUN else 'EXECUTE'}")
print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
print("═" * 60)
print(f"  DB: ...{DATABASE_URL[-20:]}")
print(f"  Tester suffix: ...{ALLOWED_SUFFIX}")
print()

engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    # ── 1. Identify tester contact ─────────────────────────────────────────────
    rows = conn.execute(
        text("SELECT id, wa_id FROM whatsapp_contacts WHERE wa_id LIKE :suffix"),
        {"suffix": f"%{ALLOWED_SUFFIX}"}
    ).fetchall()

    if len(rows) == 0:
        print(f"INFO: No contact found with suffix ...{ALLOWED_SUFFIX}. Nothing to reset.")
        sys.exit(0)
    if len(rows) > 1:
        print(f"ERROR: {len(rows)} contacts match suffix ...{ALLOWED_SUFFIX} — refusing to run.")
        for r in rows:
            print(f"  contact_id={r[0]} wa_id=...{r[1][-4:]}")
        sys.exit(1)

    contact = rows[0]
    contact_id = contact[0]
    print(f"  Tester contact: contact_id={contact_id} wa_id=...{contact[1][-4:]}")

    # ── 2. Find all tester threads ─────────────────────────────────────────────
    thread_rows = conn.execute(
        text("SELECT id, lead_id FROM whatsapp_threads WHERE contact_id=:cid"),
        {"cid": contact_id}
    ).fetchall()
    thread_ids = [r[0] for r in thread_rows]
    lead_ids = list({r[1] for r in thread_rows if r[1] is not None})

    print(f"  Tester threads: {thread_ids or 'none'}")
    print(f"  Tester leads:   {lead_ids or 'none'}")
    print()

    # ── 3. Before-counts ──────────────────────────────────────────────────────
    print("── BEFORE STATE ──────────────────────────────────────")

    def count_in(conn, table, col, ids):
        if not ids:
            return 0
        return conn.execute(
            text(f"SELECT count(*) FROM {table} WHERE {col} = ANY(:ids)"),
            {"ids": ids}
        ).fetchone()[0]

    bc = {}
    if thread_ids:
        bc["thread_revisions"]        = count_in(conn, "thread_revisions", "thread_id", thread_ids)
        bc["whatsapp_thread_states"]  = count_in(conn, "whatsapp_thread_states", "thread_id", thread_ids)
        bc["whatsapp_messages"]       = count_in(conn, "whatsapp_messages", "thread_id", thread_ids)
        bc["whatsapp_outbound_dedup"] = count_in(conn, "whatsapp_outbound_dedup", "thread_id", thread_ids)
        bc["ai_events"]               = count_in(conn, "ai_events", "thread_id", thread_ids)
        bc["whatsapp_thread_candidates"] = count_in(conn, "whatsapp_thread_candidates", "thread_id", thread_ids)
        bc["whatsapp_threads"]        = len(thread_ids)
    if lead_ids:
        bc["feedback_post_revision"]  = count_in(conn, "feedback_post_revision", "lead_id", lead_ids)
        bc["revisions"]               = count_in(conn, "revisions", "lead_id", lead_ids)
        bc["leads"]                   = len(lead_ids)
        lead_detail = conn.execute(
            text("SELECT id, estado, flag, necesita_humano, nombre FROM leads WHERE id = ANY(:ids)"),
            {"ids": lead_ids}
        ).fetchall()
        for ld in lead_detail:
            print(f"  lead {ld[0]}: estado={ld[1]} flag={ld[2]} necesita_humano={ld[3]} nombre={ld[4]}")

    for tbl, cnt in bc.items():
        print(f"  {tbl}: {cnt}")
    print()

    if DRY_RUN:
        print("── DRY RUN — no data modified ────────────────────────")
        print()
        print("Re-run with --execute to perform the reset.")
        sys.exit(0)

    # ── 4. Backup ──────────────────────────────────────────────────────────────
    print("── BACKUP ────────────────────────────────────────────")
    if SKIP_BACKUP:
        backup_path = "(skipped — taken externally)"
        print(f"  Backup: {backup_path}")
    else:
        backup_path = take_backup(DATABASE_URL, "wild_reset")
    print()

    # ── 5. Execute reset in transaction ───────────────────────────────────────
    print("── DELETING TESTER DATA ──────────────────────────────")
    deleted = {}

    def del_in(table, col, ids):
        if not ids:
            deleted[table] = 0
            return
        r = conn.execute(
            text(f"DELETE FROM {table} WHERE {col} = ANY(:ids)"),
            {"ids": ids}
        )
        deleted[table] = r.rowcount

    try:
        if thread_ids:
            del_in("thread_revisions", "thread_id", thread_ids)
            del_in("whatsapp_thread_states", "thread_id", thread_ids)
            del_in("whatsapp_messages", "thread_id", thread_ids)
            del_in("whatsapp_outbound_dedup", "thread_id", thread_ids)
            del_in("ai_events", "thread_id", thread_ids)
            del_in("whatsapp_thread_candidates", "thread_id", thread_ids)
            del_in("whatsapp_threads", "id", thread_ids)

        if lead_ids:
            del_in("feedback_post_revision", "lead_id", lead_ids)
            del_in("revisions", "lead_id", lead_ids)
            del_in("leads", "id", lead_ids)

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    for tbl, cnt in deleted.items():
        print(f"  deleted from {tbl}: {cnt}")
    print()

    # ── 6. After-counts ────────────────────────────────────────────────────────
    print("── AFTER STATE ───────────────────────────────────────")
    any_remaining = 0

    remaining_threads = conn.execute(
        text("SELECT id FROM whatsapp_threads WHERE contact_id=:cid"),
        {"cid": contact_id}
    ).fetchall()
    remaining_leads_via_threads = conn.execute(
        text("SELECT id FROM leads WHERE id = ANY(:ids)"),
        {"ids": lead_ids}
    ).fetchall() if lead_ids else []

    print(f"  threads for tester contact: {len(remaining_threads)}")
    print(f"  leads remaining (ids {lead_ids}): {len(remaining_leads_via_threads)}")

    # Tables keyed by thread_id
    thread_keyed = [
        "thread_revisions", "whatsapp_thread_states", "whatsapp_messages",
        "whatsapp_outbound_dedup", "ai_events", "whatsapp_thread_candidates",
    ]
    # Tables keyed by lead_id
    lead_keyed = ["feedback_post_revision", "revisions"]

    for tbl in thread_keyed:
        remaining = count_in(conn, tbl, "thread_id", thread_ids)
        if remaining > 0:
            print(f"  WARNING: {remaining} rows remain in {tbl}")
            any_remaining += 1
        else:
            print(f"  {tbl}: 0 ✓")

    for tbl in lead_keyed:
        remaining = count_in(conn, tbl, "lead_id", lead_ids)
        if remaining > 0:
            print(f"  WARNING: {remaining} rows remain in {tbl}")
            any_remaining += 1
        else:
            print(f"  {tbl}: 0 ✓")

    print()
    print("── CONTACT PRESERVED ─────────────────────────────────")
    contact_check = conn.execute(
        text("SELECT id, wa_id FROM whatsapp_contacts WHERE id=:cid"),
        {"cid": contact_id}
    ).fetchone()
    print(f"  contact_id={contact_check[0]} wa_id=...{contact_check[1][-4:]} ✓")
    print()

    if any_remaining == 0:
        print("═" * 60)
        print("  RESET COMPLETE — tester state is clean")
        print(f"  Backup: {backup_path}")
        print("  Next message from tester → n8n creates fresh thread + lead")
        print("═" * 60)
    else:
        print("═" * 60)
        print(f"  WARNING: {any_remaining} table(s) have unexpected remaining rows")
        print("═" * 60)
        sys.exit(1)
