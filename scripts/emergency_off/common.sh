#!/usr/bin/env bash
# RideCheck emergency-OFF kit — shared definitions (PRELAUNCH-MINIMUM-LAUNCH E).
#
# The kit is VERSIONED here and INSTANTIATED per window by a config file that lives OUTSIDE
# Git (0600, in a 0700 evidence directory): no phone number, credential or live identifier is
# in this directory. Every script is run as `bash /exact/path/SCRIPT.sh CONFIG` — the kit
# never relies on an execute bit, which is what failed on 2026-11-01 (NP-03: kill_now invoked
# KILL.sh as a command, every kit file was 0600, execve said "Permission denied", and
# containment never ran).
#
# No pkill, no `pgrep -f`, no killall, and no process-pattern matching of any kind anywhere in
# this kit: a pattern matched the INVOKING shell once (2026-11-01) and killed the command
# before KILL could run. Processes are addressed by exact, verified PID only (watcher_pid.sh).
set -uo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT_CONFIG="${1:-${KIT_CONFIG:-}}"
if [ -z "$KIT_CONFIG" ] || [ ! -r "$KIT_CONFIG" ]; then
  echo "emergency_off: a readable config file is required (bash SCRIPT.sh CONFIG)" >&2
  exit 64
fi
KIT_CONFIG="$(cd "$(dirname "$KIT_CONFIG")" && pwd)/$(basename "$KIT_CONFIG")"
# shellcheck disable=SC1090
source "$KIT_CONFIG"

# ── required configuration (see README.md) ───────────────────────────────────────────────
: "${EV_ROOT:?}" "${BACKEND:?}" "${PGC:?}" "${DB:?}" "${COMPOSE_PROJECT:?}" "${COMPOSE_DIR:?}"
: "${COMPOSE_FILES:?}" "${ON_OVERLAY:?}" "${OFF_OVERLAY:?}" "${EXPECT_IMAGE:?}" "${EXPECT_HEAD:?}"
: "${TESTER_WA_IDS:?}" "${OFF_ALLOWLIST_COUNT:?}" "${OFF_ALLOWLIST_SHA16:?}"
: "${ON_TRUE_FLAGS:?}" "${KIT_MODE:?}"
WINDOW_SECONDS="${WINDOW_SECONDS:-2700}"
# ── thresholds (R1, review F-07) ─────────────────────────────────────────────────────────
# WINDOW_PROFILE=single (default): one tester books; the historical limits.
# WINDOW_PROFILE=race: two testers race for one slot. Expected, measured on this branch
# (tests/test_minimum_launch_r1.py + scripts/emergency_off/test_kit_r1.sh R3):
#   Flow dispatches   2 originals + 1 recovery Flow          = 3   → max 3
#   booking offers    2 originals + 1 recovery offer          = 3   → max 3
#   bookings          1 winner (+1 if the loser rebooks)      ≤ 2   → max 2
#   attempt events    1 FINALISE per distinct losing delivery = 1–2 → max 4
#   recovery jobs     1 per losing offer                      = 1   → max 1
#   operator notices  1 per committed booking — only when OPERATOR_WHATSAPP_NOTIFY_ENABLED
#                     is in ON_TRUE_FLAGS                     ≤ 2   → max 2 (else 0)
#   (texts are not a breach counter: the acknowledgement and the recovery explanation are
#    BOOKING_FLOW texts and are bounded by the recipient check)
# A value set explicitly in the config always wins; nothing is disabled.
WINDOW_PROFILE="${WINDOW_PROFILE:-single}"
case "$WINDOW_PROFILE" in
  single) _d_flow=2; _d_offers=4; _d_book=2; _d_att=6; _d_jobs=1 ;;
  race)   _d_flow=3; _d_offers=3; _d_book=2; _d_att=4; _d_jobs=1 ;;
  *) echo "emergency_off: WINDOW_PROFILE must be single or race" >&2; exit 64 ;;
esac
case " ${ON_TRUE_FLAGS:-} " in
  *" OPERATOR_WHATSAPP_NOTIFY_ENABLED "*) _d_opn=$([ "$WINDOW_PROFILE" = race ] && echo 2 || echo 1) ;;
  *) _d_opn=0 ;;
esac
MAX_FLOW_DISPATCHES="${MAX_FLOW_DISPATCHES:-$_d_flow}"
MAX_NEW_BOOKINGS="${MAX_NEW_BOOKINGS:-$_d_book}"
MAX_NEW_OFFERS="${MAX_NEW_OFFERS:-$_d_offers}"
MAX_NEW_ATTEMPTS="${MAX_NEW_ATTEMPTS:-$_d_att}"
MAX_NEW_RECOVERY_JOBS="${MAX_NEW_RECOVERY_JOBS:-$_d_jobs}"
MAX_OPERATOR_NOTIFICATIONS="${MAX_OPERATOR_NOTIFICATIONS:-$_d_opn}"
# R1 (review F-13): the watcher waits at most this long for KILL (a hung `docker compose`
# must not hang the watcher); past it the incident is UNRECOVERED, KILL's pid is retained.
KILL_DEADLINE_SECONDS="${KILL_DEADLINE_SECONDS:-$(( ${SENDER_CHECK_SECONDS:-65} + 240 ))}"
# R1 (review F-13): WATCH starts BEFORE ENABLE (pre-armed) and waits at most this long for
# ENABLE's watermarks; ENABLE refuses to arm without a live pre-armed watcher.
PREARM_GRACE_SECONDS="${PREARM_GRACE_SECONDS:-300}"
POLL_SECONDS="${POLL_SECONDS:-2}"
READY_MODE="${READY_MODE:-host}"                 # host | exec
READY_URL="${READY_URL:-http://127.0.0.1:8000/login}"
SENDER_CHECK_SECONDS="${SENDER_CHECK_SECONDS:-65}"
DB_USER="${DB_USER:-crm}"
EVID="$EV_ROOT/evidence"

SEVEN=(OUTBOUND_ENABLED SMART_BOOKING_CE_ENABLED OPERATOR_WHATSAPP_NOTIFY_ENABLED
       CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED EVIDENCE_ADMISSION_ENABLED
       OUTSTANDING_QUESTION_ENABLED LOCALITY_RESOLUTION_SAFETY_ENABLED)

mkdir -p "$EVID"; chmod 700 "$EV_ROOT" "$EVID" 2>/dev/null

ts()  { date -u +%Y-%m-%dT%H:%M:%S.%3NZ; }
log() { printf '%s  %s\n' "$(ts)" "$*" | tee -a "$EVID/timeline.log"; }

# ── refuse a rehearsal that names a live object ──────────────────────────────────────────
# A rehearsal config must never be able to stop, recreate or read the live stack. The live
# names are refused by value, so a copy-pasted live config fails closed in rehearsal mode.
LIVE_PROJECT_NAMES="ridecheck-crm"
LIVE_CONTAINERS="ridecheck-crm-backend-1 ridecheck-crm-postgres-1 ridecheck-crm-n8n-1"
guard_target() {
  case "$KIT_MODE" in
    live) return 0 ;;
    rehearsal)
      for n in $LIVE_PROJECT_NAMES; do
        [ "$COMPOSE_PROJECT" != "$n" ] || { log "GUARD: *** rehearsal names the live project"; return 1; }
      done
      for c in $LIVE_CONTAINERS; do
        [ "$BACKEND" != "$c" ] && [ "$PGC" != "$c" ] \
          || { log "GUARD: *** rehearsal names live container $c"; return 1; }
      done
      return 0 ;;
    *) log "GUARD: *** KIT_MODE must be live or rehearsal"; return 1 ;;
  esac
}

compose() {
  local args=(-p "$COMPOSE_PROJECT") f
  IFS=':' read -r -a _files <<<"$COMPOSE_FILES"
  for f in "${_files[@]}"; do args+=(-f "$f"); done
  ( cd "$COMPOSE_DIR" && docker compose "${args[@]}" "$@" )
}

# Every database read is read-only AT THE SERVER, so an instrument fault cannot write.
roq() { docker exec -e "PGOPTIONS=-c default_transaction_read_only=on" -e PGPASSWORD="${DB_PASSWORD:-crm}" \
          "$PGC" psql -X -q -A -t -U "$DB_USER" -d "$DB" -c "$1" 2>/dev/null; }

seven_state() {
  docker exec -i "$BACKEND" python - <<'PY' 2>/dev/null
import os
for k in ["OUTBOUND_ENABLED","SMART_BOOKING_CE_ENABLED","OPERATOR_WHATSAPP_NOTIFY_ENABLED",
          "CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED","EVIDENCE_ADMISSION_ENABLED",
          "OUTSTANDING_QUESTION_ENABLED","LOCALITY_RESOLUTION_SAFETY_ENABLED"]:
    print(f"{k}={os.environ.get(k,'<ABSENT>')}")
PY
}

# Counts and digests only — never an allowlisted number or a credential value.
interlock() {
  docker exec -i "$BACKEND" python - <<'PY' 2>/dev/null
import hashlib, os
from app.services.booking_dispatch_gate import booking_dispatch_allowed
al = sorted(x.strip() for x in (os.environ.get("CLOSED_BETA_ALLOWED_WA_IDS") or "").split(",") if x.strip())
print("booking_dispatch_allowed=%s" % booking_dispatch_allowed())
print("allowlist=%d" % len(al))
print("allowlist_sha16=%s" % hashlib.sha256(",".join(al).encode()).hexdigest()[:16])
print("resend_key_set=%s" % bool(os.environ.get("RESEND_API_KEY")))
print("smtp_password_set=%s" % bool(os.environ.get("SMTP_PASSWORD")))
print("operator_to_set=%s" % bool(os.environ.get("OPERATOR_WHATSAPP_TO")))
PY
}

ready() {
  local code
  if [ "$READY_MODE" = "exec" ]; then
    code=$(docker exec "$BACKEND" python -c "import urllib.request as u
try:
    print(u.urlopen('$READY_URL', timeout=5).status)
except Exception as e:
    print(getattr(e, 'code', 0))" 2>/dev/null)
  else
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$READY_URL")
  fi
  [ "$code" = "200" ]
}

tester_sql_list() {                 # 'a','b' for SQL IN (), from the config only
  local out="" x
  IFS=',' read -r -a _t <<<"$TESTER_WA_IDS"
  for x in "${_t[@]}"; do case "$x" in ''|*[!0-9]*) continue ;; esac; out="$out,'$x'"; done
  echo "${out#,}"
}

# over NAME VALUE BASE MAX — print a breach reason and succeed when VALUE exceeds BASE+MAX.
# A non-numeric VALUE is itself a breach (an unreadable instrument is never a zero).
over() {
  case "$2" in ''|*[!0-9]*) echo "$1 unreadable ('$2')"; return 0 ;; esac
  [ "$2" -gt "$(( ${3:-0} + $4 ))" ] && { echo "$1 ${3:-0} -> $2 (max +$4)"; return 0; }
  return 1
}

# The recovery-job instrument (R1). 'absent' on a schema below 20261103_slot_recovery_job.
recovery_jobs() {
  local t; t=$(roq "SELECT to_regclass('slot_recovery_jobs') IS NOT NULL;")
  case "$t" in
    t) roq "SELECT count(*) FROM slot_recovery_jobs;" ;;
    f) echo absent ;;
    *) echo "" ;;
  esac
}

source "$KIT_DIR/watcher_pid.sh"
