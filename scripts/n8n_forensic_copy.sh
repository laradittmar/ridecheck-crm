#!/usr/bin/env bash
# OPS-AUDIO-TRANSCRIPTION-RESILIENCE — copy n8n's SQLite safely for forensics.
#
# n8n's database is WAL-mode. Copying database.sqlite alone yields a stale checkpoint that
# can misreport execution state by days — on 2026-09-08 exactly that produced a confident
# "n8n is wedged" conclusion when every execution had in fact run. See
# docs/operations/N8N_FORENSIC_READ_RULE.md.
#
#   ./scripts/n8n_forensic_copy.sh [dest_dir] [container]
set -euo pipefail

DEST="${1:-./n8n-forensic-$(date -u +%Y%m%dT%H%M%SZ)}"
CONTAINER="${2:-ridecheck-crm-n8n-1}"
SRC=/home/node/.n8n

mkdir -p "$DEST"
missing=0
for f in database.sqlite database.sqlite-wal database.sqlite-shm; do
  if docker cp "$CONTAINER:$SRC/$f" "$DEST/$f" 2>/dev/null; then
    echo "  copied $f ($(stat -c%s "$DEST/$f") bytes)"
  else
    echo "  MISSING $f" >&2
    [[ "$f" == "database.sqlite" ]] && { echo "FATAL: main database not found" >&2; exit 2; }
    missing=1
  fi
done

# The append-only event log needs no WAL reasoning and is the better first read.
docker cp "$CONTAINER:$SRC/n8nEventLog.log" "$DEST/n8nEventLog.log" 2>/dev/null \
  && echo "  copied n8nEventLog.log" || echo "  (no n8nEventLog.log)"

if (( missing )); then
  echo
  echo "WARNING: a companion file was absent. If -wal is missing the database may still be"
  echo "         mid-checkpoint; treat execution status as UNVERIFIED and cross-check the"
  echo "         event log before drawing any conclusion."
fi
echo "Forensic copy in: $DEST"
