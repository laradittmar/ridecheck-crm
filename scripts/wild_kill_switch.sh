#!/bin/bash
# W1-F7 — the Wild kill switch, with the backend's logs captured BEFORE the recreate removes them.
#
# The first controlled Wild's KILL.sh recreated the backend with `--force-recreate`, which
# removed the Wild-time container and every stdout line it had written (provider-call evidence
# for 21:21–21:28 was lost). This wrapper:
#   1. captures `docker logs --timestamps` of the CURRENT backend container into the protected
#      evidence directory (must exist, be a directory owned by root with mode 700; file 0600);
#   2. proves the capture carries no credential (log_credential_scan.py: the container's own
#      secret values + credential shapes; anything found is redacted in place; only labels,
#      counts and the SHA-256 are printed);
#   3. recreates ONLY the backend from the given overlay, exactly as KILL.sh did (no deps, no
#      build, no pull).
# A capture or scan failure NEVER blocks or delays step 3 beyond the 30-second capture bound:
# turning outbound off is the safety action, evidence is secondary.
# Never prunes, never removes anything else; no secret value is ever printed or passed in argv.
#
# Usage: wild_kill_switch.sh --evidence-dir DIR --overlay COMPOSE_FILE [--capture-only]
#        [--container ID] [--project ridecheck-crm] [--compose-dir /opt/ridecheck-crm]
set -u
umask 077
EV="" OVERLAY="" CAPTURE_ONLY=0 CID="" PROJECT="ridecheck-crm" COMPOSE_DIR="/opt/ridecheck-crm"
while [ $# -gt 0 ]; do
  case "$1" in
    --evidence-dir) EV="$2"; shift 2 ;;
    --overlay) OVERLAY="$2"; shift 2 ;;
    --capture-only) CAPTURE_ONLY=1; shift ;;
    --container) CID="$2"; shift 2 ;;
    --project) PROJECT="$2"; shift 2 ;;
    --compose-dir) COMPOSE_DIR="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done
HERE="$(cd "$(dirname "$0")" && pwd)"

capture() {
  if [ -z "$EV" ] || [ ! -d "$EV" ] || [ "$(stat -c '%a %U' "$EV")" != "700 root" ]; then
    echo "LOG_CAPTURE refused: evidence dir must exist, be owned by root, mode 700" >&2
    return 1
  fi
  if [ -z "$CID" ]; then
    CID="$(cd "$COMPOSE_DIR" && docker compose -p "$PROJECT" ps -q backend 2>/dev/null | head -1)"
  fi
  if [ -z "$CID" ]; then
    echo "LOG_CAPTURE: no running backend container found — nothing to capture" >&2
    return 1
  fi
  local ts log
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  log="$EV/backend_logs_${CID:0:12}_${ts}.log"
  if ! timeout 30 docker logs --timestamps "$CID" > "$log" 2>&1; then
    echo "LOG_CAPTURE: docker logs failed or timed out for ${CID:0:12} (partial file kept)" >&2
  fi
  chmod 600 "$log"
  if timeout 30 python3 "$HERE/log_credential_scan.py" --log "$log" --container "$CID" \
       > "$log.scan" 2>&1; then
    chmod 600 "$log.scan"
    echo "LOG_CAPTURE ok: $log ($(cat "$log.scan"))"
  else
    chmod 600 "$log.scan"
    echo "LOG_CAPTURE: credential scan did NOT come back clean — see $log.scan" >&2
    return 1
  fi
}

capture || echo "LOG_CAPTURE incomplete — proceeding with the kill switch regardless" >&2

if [ "$CAPTURE_ONLY" = 1 ]; then
  exit 0
fi
if [ -z "$OVERLAY" ] || [ ! -f "$OVERLAY" ]; then
  echo "KILL refused: --overlay must name the existing flags-OFF compose overlay" >&2
  exit 64
fi
cd "$COMPOSE_DIR" && env -u BETA_OUTBOUND_ENABLED docker compose -p "$PROJECT" \
  -f docker-compose.yml -f "$OVERLAY" up -d --no-deps --no-build --pull never --force-recreate backend
