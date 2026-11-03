#!/usr/bin/env bash
# Sealed unit tests for the R1 kit corrections (PRELAUNCH-MINIMUM-LAUNCH-CORRECTION-R1).
# No containers, no database, no network. test_kit.sh keeps its 28 historical checks; these
# are the new ones:
#   R1  a KILL that hangs past KILL_DEADLINE_SECONDS → BLOCKER, INCIDENT_UNRECOVERED naming
#       KILL's pid (left running, not signalled), pidfile retained, OFF never claimed
#   R2  WATCH starts PRE-ARMED before ENABLE; ENABLE refuses without it; a watcher whose
#       ENABLE never starts, or refuses before any change, exits 6 without running KILL
#   R3  race-window thresholds: the measured two-customer race does not breach; one more does
#   R4  an unreadable counter is a breach
#   R5  K6's command filter: real pattern kills are found, the sentinel's quoted argv is not
# Run: bash scripts/emergency_off/test_kit_r1.sh
set -uo pipefail
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  PASS  %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; }
chk() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (got '$2', want '$3')"; fi; }
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

mkkit() {                      # $1 = name, $2 = KILL.sh body, $3 = extra config lines
  local d="$TMP/$1"; mkdir -p "$d/kit" "$d/ev"; chmod 700 "$d/ev"
  cp "$SRC/common.sh" "$SRC/watcher_pid.sh" "$SRC/WATCH.sh" "$SRC/ENABLE.sh" "$d/kit/"
  [ -n "$2" ] && printf '%s\n' "$2" > "$d/kit/KILL.sh"
  chmod 600 "$d/kit/"*
  cat > "$d/kit.env" <<EOF
EV_ROOT=$d/ev
BACKEND=unit-backend PGC=unit-pg DB=unit COMPOSE_PROJECT=unit COMPOSE_DIR=$d COMPOSE_FILES=x
ON_OVERLAY=x OFF_OVERLAY=x EXPECT_IMAGE=x EXPECT_HEAD=x TESTER_WA_IDS=1
OFF_ALLOWLIST_COUNT=1 OFF_ALLOWLIST_SHA16=x ON_TRUE_FLAGS=OUTBOUND_ENABLED KIT_MODE=rehearsal
POLL_SECONDS=1
$3
EOF
  chmod 600 "$d/kit.env"; echo "$d"
}

echo "R1  KILL hangs past its deadline -> unrecovered BLOCKER; KILL pid retained; no OFF claim"
d=$(mkkit r1 'echo $$ > "$(dirname "$0")/../kill.pid"; sleep 120; exit 0' "WINDOW_SECONDS=0
KILL_DEADLINE_SECONDS=2")
t0=$(date +%s)
bash "$d/kit/WATCH.sh" "$d/kit.env" > "$d/watch.out" 2>&1; rc=$?
took=$(( $(date +%s) - t0 ))
chk "watcher exit 4" "$rc" "4"
chk "watcher returned within the deadline bound" "$([ "$took" -le 6 ] && echo yes || echo "no(${took}s)")" "yes"
kp=$(cat "$d/kill.pid" 2>/dev/null)
chk "incident: KILL still running after the deadline" \
    "$(grep -c 'still running after the 2s deadline' "$d/ev/evidence/INCIDENT_UNRECOVERED")" "1"
chk "incident names KILL's pid" "$(grep -c "KILL pid: $kp " "$d/ev/evidence/INCIDENT_UNRECOVERED")" "1"
chk "incident says OFF is not verified" "$(grep -c 'OFF IS NOT VERIFIED' "$d/ev/evidence/INCIDENT_UNRECOVERED")" "1"
chk "BLOCKER line names the deadline" "$(grep -c 'BLOCKER .* exceeded its 2s deadline' "$d/ev/evidence/timeline.log")" "1"
chk "KILL left running (not signalled)" "$(kill -0 "$kp" 2>/dev/null && echo alive)" "alive"
chk "pidfile retained" "$([ -f "$d/ev/evidence/watch/watcher.pid" ] && echo yes || echo no)" "yes"
chk "OFF never claimed" "$(grep -c 'OFF restored and verified' "$d/ev/evidence/timeline.log")" "0"
chk "incident file is 0600" "$(stat -c %a "$d/ev/evidence/INCIDENT_UNRECOVERED")" "600"
kill -KILL "$kp" 2>/dev/null                      # the exact pid this test started

echo "R2  pre-armed watcher: ENABLE requires it; no ENABLE / early refusal -> exit 6, no KILL"
d=$(mkkit r2a 'touch "$(dirname "$0")/../killed"; exit 0' "WINDOW_SECONDS=900
PREARM_GRACE_SECONDS=2")
bash "$d/kit/ENABLE.sh" "$d/kit.env" > "$d/enable.out" 2>&1; rc=$?
chk "ENABLE without a pre-armed watcher refused (2)" "$rc" "2"
chk "refusal names the missing watcher" "$(grep -c 'no pre-armed watcher' "$d/ev/evidence/timeline.log")" "1"
chk "ENABLE wrote no watermarks" "$([ -f "$d/ev/evidence/enable/watermarks.txt" ] && echo yes || echo no)" "no"
bash "$d/kit/WATCH.sh" "$d/kit.env" > "$d/watch.out" 2>&1; rc=$?
chk "watcher with no ENABLE exits 6 after the grace" "$rc" "6"
chk "it says nothing was armed" "$(grep -c 'nothing was armed' "$d/ev/evidence/timeline.log")" "1"
chk "KILL not run" "$([ -f "$d/killed" ] && echo ran || echo no)" "no"
chk "pidfile removed (clean stop, no breach)" "$([ -f "$d/ev/evidence/watch/watcher.pid" ] && echo yes || echo no)" "no"
d=$(mkkit r2b 'touch "$(dirname "$0")/../killed"; exit 0' "WINDOW_SECONDS=900
PREARM_GRACE_SECONDS=30")
( bash "$d/kit/WATCH.sh" "$d/kit.env" > "$d/watch.out" 2>&1; echo $? > "$d/watch.rc" ) & WP=$!
for i in $(seq 1 20); do grep -q 'pre-armed' "$d/ev/evidence/timeline.log" 2>/dev/null && break; sleep 0.2; done
chk "watcher reports pre-armed before ENABLE" "$(grep -c 'WATCH: pre-armed' "$d/ev/evidence/timeline.log")" "1"
mkdir -p "$d/ev/evidence/enable"
printf 'max_outbound_id=0\n' > "$d/ev/evidence/enable/watermarks.txt"   # ENABLE step 1
: > "$d/ev/evidence/enable/refused_before_change"                       # ENABLE refused
for i in $(seq 1 30); do kill -0 "$WP" 2>/dev/null || break; sleep 0.2; done
chk "early refusal -> watcher exits 6" "$(cat "$d/watch.rc" 2>/dev/null)" "6"
chk "KILL not run after an early refusal" "$([ -f "$d/killed" ] && echo ran || echo no)" "no"
d=$(mkkit r2c 'exit 0' "WINDOW_SECONDS=900
PREARM_GRACE_SECONDS=3")
mkdir -p "$d/ev/evidence/enable"
printf 'max_outbound_id=0\n' > "$d/ev/evidence/enable/watermarks.txt"
touch -d '-1 hour' "$d/ev/evidence/enable/watermarks.txt"              # a previous window's
bash "$d/kit/WATCH.sh" "$d/kit.env" > "$d/watch.out" 2>&1; rc=$?
chk "stale watermarks from an earlier window never arm the watcher" "$rc" "6"

echo "R3  race-window thresholds (measured shape) and the single-window defaults"
prof() {   # $1 = profile, $2 = ON_TRUE_FLAGS → prints the effective maxima
  local d; d=$(mkkit "p$RANDOM" 'exit 0' "WINDOW_PROFILE=$1
ON_TRUE_FLAGS=\"$2\"")
  ( source "$d/kit/common.sh" "$d/kit.env" >/dev/null 2>&1
    echo "$MAX_FLOW_DISPATCHES $MAX_NEW_OFFERS $MAX_NEW_BOOKINGS $MAX_NEW_ATTEMPTS $MAX_NEW_RECOVERY_JOBS $MAX_OPERATOR_NOTIFICATIONS" )
}
chk "single profile: historical limits" "$(prof single OUTBOUND_ENABLED)" "2 4 2 6 1 0"
chk "race profile: flows 3, offers 3, bookings 2, attempts 4, jobs 1, notices 0" \
    "$(prof race OUTBOUND_ENABLED)" "3 3 2 4 1 0"
chk "race profile with the operator notice enabled: notices 2" \
    "$(prof race 'OUTBOUND_ENABLED OPERATOR_WHATSAPP_NOTIFY_ENABLED')" "3 3 2 4 1 2"
d=$(mkkit r3o 'exit 0' "WINDOW_PROFILE=race
MAX_FLOW_DISPATCHES=5")
chk "an explicit value wins over the profile" \
    "$( (source "$d/kit/common.sh" "$d/kit.env" >/dev/null 2>&1; echo "$MAX_FLOW_DISPATCHES") )" "5"
d=$(mkkit r3x 'exit 0' "WINDOW_PROFILE=wide-open")
( source "$d/kit/common.sh" "$d/kit.env" >/dev/null 2>&1 ); chk "an unknown profile is refused (64)" "$?" "64"
d=$(mkkit r3e 'exit 0' "WINDOW_PROFILE=race")
(
  source "$d/kit/common.sh" "$d/kit.env" >/dev/null 2>&1
  # the measured race (R1 closeout, race-shape measurement): +2 original
  # offers +1 recovery offer, +1 booking, +2 FINALISE rows (two distinct losing deliveries),
  # +1 recovery job; flows 2 originals + 1 recovery
  n=0
  over offers 13 10 "$MAX_NEW_OFFERS" >/dev/null && n=$((n+1))
  over bookings 11 10 "$MAX_NEW_BOOKINGS" >/dev/null && n=$((n+1))
  over attempts 12 10 "$MAX_NEW_ATTEMPTS" >/dev/null && n=$((n+1))
  over jobs 1 0 "$MAX_NEW_RECOVERY_JOBS" >/dev/null && n=$((n+1))
  [ 3 -gt "$MAX_FLOW_DISPATCHES" ] && n=$((n+1))
  echo "$n" > "$TMP/r3_expected"
  m=0
  over offers 14 10 "$MAX_NEW_OFFERS" >/dev/null && m=$((m+1))
  over bookings 13 10 "$MAX_NEW_BOOKINGS" >/dev/null && m=$((m+1))
  over attempts 15 10 "$MAX_NEW_ATTEMPTS" >/dev/null && m=$((m+1))
  over jobs 2 0 "$MAX_NEW_RECOVERY_JOBS" >/dev/null && m=$((m+1))
  [ 4 -gt "$MAX_FLOW_DISPATCHES" ] && m=$((m+1))
  echo "$m" > "$TMP/r3_beyond"
)
chk "the measured race: no counter breaches" "$(cat "$TMP/r3_expected")" "0"
chk "one more of each: every counter breaches" "$(cat "$TMP/r3_beyond")" "5"
chk "the single profile still breaches on the race's third Flow" \
    "$(set -- $(prof single OUTBOUND_ENABLED); [ 3 -gt "$1" ] && echo breach)" "breach"

echo "R4  an unreadable counter is a breach, never a zero"
d=$(mkkit r4 'exit 0' "")
(
  source "$d/kit/common.sh" "$d/kit.env" >/dev/null 2>&1
  over x "" 0 9 >/dev/null && echo empty-breach > "$TMP/r4a"
  over x "ERROR" 0 9 >/dev/null && echo text-breach > "$TMP/r4b"
)
chk "empty value breaches" "$(cat "$TMP/r4a" 2>/dev/null)" "empty-breach"
chk "non-numeric value breaches" "$(cat "$TMP/r4b" 2>/dev/null)" "text-breach"

echo "R5  K6's filter: real pattern kills are hits; quoted argv and comments are not"
# the SAME function test_kit.sh K6 uses (extracted, never copied by hand)
eval "$(sed -n '/^pattern_kill_hits() {/,/^}/p' "$SRC/test_kit.sh")"
chk "K6 filter extracted" "$(type -t pattern_kill_hits)" "function"
printf 'pkill -f WATCH.sh\n' > "$TMP/a.sh";            chk "pkill command"   "$(pattern_kill_hits "$TMP/a.sh")" "1"
printf 'x=1; killall bash\n' > "$TMP/b.sh";            chk "killall command" "$(pattern_kill_hits "$TMP/b.sh")" "1"
printf 'pgrep  -f watch\n' > "$TMP/c.sh";              chk "pgrep -f"        "$(pattern_kill_hits "$TMP/c.sh")" "1"
printf '( exec -a "bash W.sh C KILL.sh pkill -f killall" sleep 1 ) &\n' > "$TMP/d.sh"
chk "sentinel argv (quoted) is not a kill" "$(pattern_kill_hits "$TMP/d.sh")" "0"
printf "echo 'pkill'\n  # pkill -f x\n" > "$TMP/e.sh"; chk "quoted word and comment" "$(pattern_kill_hits "$TMP/e.sh")" "0"
kit=(); for f in "$SRC"/*.sh; do case "$f" in */test_kit*.sh) ;; *) kit+=("$f") ;; esac; done
chk "the kit itself (incl. rehearse.sh's sentinel)" "$(pattern_kill_hits "${kit[@]}")" "0"

printf '\n  %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
