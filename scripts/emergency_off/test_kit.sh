#!/usr/bin/env bash
# Sealed unit tests for the emergency-OFF kit. No containers, no database, no network: the
# breach used is the deadline (WINDOW_SECONDS=0), which fires before any instrument is read,
# so the REAL WATCH.sh → KILL path is exercised with KILL replaced in a temporary copy.
# Run: bash scripts/emergency_off/test_kit.sh
set -uo pipefail
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  PASS  %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; }
chk() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (got '$2', want '$3')"; fi; }
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

mkkit() {                      # $1 = name, $2 = KILL.sh body ("" = no KILL.sh at all)
  local d="$TMP/$1"; mkdir -p "$d/kit" "$d/ev"; chmod 700 "$d/ev"
  cp "$SRC/common.sh" "$SRC/watcher_pid.sh" "$SRC/WATCH.sh" "$d/kit/"
  [ -n "$2" ] && printf '%s\n' "$2" > "$d/kit/KILL.sh"
  chmod 600 "$d/kit/"*                      # the real posture: nothing is executable
  cat > "$d/kit.env" <<EOF
EV_ROOT=$d/ev
BACKEND=unit-backend PGC=unit-pg DB=unit COMPOSE_PROJECT=unit COMPOSE_DIR=$d COMPOSE_FILES=x
ON_OVERLAY=x OFF_OVERLAY=x EXPECT_IMAGE=x EXPECT_HEAD=x TESTER_WA_IDS=1
OFF_ALLOWLIST_COUNT=1 OFF_ALLOWLIST_SHA16=x ON_TRUE_FLAGS=OUTBOUND_ENABLED KIT_MODE=rehearsal
WINDOW_SECONDS=0
EOF
  chmod 600 "$d/kit.env"; echo "$d"
}
watch() { bash "$1/kit/WATCH.sh" "$1/kit.env" > "$1/watch.out" 2>&1; echo $?; }

echo "K1  breach -> KILL invoked although every kit file is mode 0600 (the NP-03 defect)"
d=$(mkkit k1 'echo "KILL ran args=$*" > "$(dirname "$0")/../killed"; exit 0')
chk "KILL.sh is not executable" "$([ -x "$d/kit/KILL.sh" ] && echo x || echo noexec)" "noexec"
rc=$(watch "$d")
chk "watcher exit 3 (OFF verified)" "$rc" "3"
chk "KILL really ran, with config and reason" "$(grep -c 'kit.env watcher:deadline' "$d/killed" 2>/dev/null)" "1"
chk "no Permission denied" "$(grep -c 'Permission denied' "$d/watch.out")" "0"
chk "pidfile removed after a VERIFIED stop" "$([ -f "$d/ev/evidence/watch/watcher.pid" ] && echo yes || echo no)" "no"
chk "no incident file" "$([ -f "$d/ev/evidence/INCIDENT_UNRECOVERED" ] && echo yes || echo no)" "no"

echo "K2  KILL cannot be started -> loud BLOCKER, unrecovered incident, pidfile retained"
d=$(mkkit k2 "")
rc=$(watch "$d")
chk "watcher exit 4" "$rc" "4"
chk "BLOCKER line" "$(grep -c 'BLOCKER' "$d/ev/evidence/timeline.log")" "1"
chk "incident names exit 127" "$(grep -c 'KILL exit status: 127' "$d/ev/evidence/INCIDENT_UNRECOVERED")" "1"
chk "pidfile retained" "$([ -f "$d/ev/evidence/watch/watcher.pid" ] && echo yes || echo no)" "yes"
chk "incident file is 0600" "$(stat -c %a "$d/ev/evidence/INCIDENT_UNRECOVERED")" "600"

echo "K3  KILL runs and FAILS -> the watcher reports it, never a clean stop"
d=$(mkkit k3 'echo "restoration failed" >&2; exit 1')
rc=$(watch "$d")
chk "watcher exit 4" "$rc" "4"
chk "incident names exit 1" "$(grep -c 'KILL exit status: 1 ' "$d/ev/evidence/INCIDENT_UNRECOVERED")" "1"
chk "pidfile retained" "$([ -f "$d/ev/evidence/watch/watcher.pid" ] && echo yes || echo no)" "yes"

echo "K4  the watcher does not exit before KILL completes"
d=$(mkkit k4 'sleep 2; date +%s%N > "$(dirname "$0")/../kill_done"; exit 0')
rc=$(watch "$d"); after=$(date +%s%N)
chk "watcher exit 3" "$rc" "3"
chk "KILL finished before the watcher returned" \
  "$([ "$(cat "$d/kill_done")" -le "$after" ] && echo yes || echo no)" "yes"

echo "K5  a rehearsal config naming a live object is refused before anything runs"
d=$(mkkit k5 'touch "$(dirname "$0")/../killed"; exit 0')
sed -i 's/^BACKEND=unit-backend/BACKEND=ridecheck-crm-backend-1/' "$d/kit.env"
rc=$(watch "$d")
chk "refused (65)" "$rc" "65"
chk "KILL not run" "$([ -f "$d/killed" ] && echo ran || echo no)" "no"

echo "K6  no process-pattern kill anywhere in the kit (commands, not comments or quoted text)"
# R1 (review F-08): a COMMAND is what is left of a line once comment lines and every quoted
# string are removed. rehearse.sh starts a sentinel whose fake argv (a quoted string) carries
# the words on purpose — that is data, not a kill; a bare `pkill …` is still a hit
# (test_kit_r1.sh R5 proves the same filter on synthetic files both ways).
pattern_kill_hits() {
  local f
  for f in "$@"; do
    sed -E -e 's/^[[:space:]]*#.*$//' -e 's/"([^"\\]|\\.)*"//g' -e "s/'[^']*'//g" "$f"
  done | grep -c -E '(^|[^[:alnum:]_-])(pkill|killall)([^[:alnum:]_-]|$)|pgrep +-f'
}
kit_scripts=(); for f in "$SRC"/*.sh; do case "$f" in */test_kit*.sh) ;; *) kit_scripts+=("$f") ;; esac; done
hits=$(pattern_kill_hits "${kit_scripts[@]}")
chk "zero hits" "$hits" "0"

echo "K7  exact-PID ownership (carried from the 2026-11-01 suite)"
d=$(mkkit k7 'exit 0')
KIT_CONFIG="$d/kit.env"; source "$d/kit/common.sh" "$d/kit.env" >/dev/null 2>&1
mkdir -p "$EVID/watch"
printf '999999\n' > "$(watcher_pidfile)"; watcher_stop >/dev/null
chk "stale pid: not killed, pidfile removed" "$([ -f "$(watcher_pidfile)" ] && echo yes || echo no)" "no"
printf 'garbage\n' > "$(watcher_pidfile)"; watcher_stop >/dev/null; chk "garbage pid rc 0" "$?" "0"
printf '%s\n' "$$" > "$(watcher_pidfile)"; watcher_stop >/dev/null
chk "this shell is refused and survives" "$(kill -0 $$ 2>/dev/null && echo alive)" "alive"
# an impostor whose command line names WATCH.sh AND a kit path, but not THIS config
( exec -a "bash $KIT_DIR/WATCH.sh /elsewhere/kit.env pkill killall" sleep 30 ) & IMP=$!
sleep 0.5; printf '%s\n' "$IMP" > "$(watcher_pidfile)"; watcher_stop >/dev/null
chk "impostor survives" "$(kill -0 $IMP 2>/dev/null && echo alive)" "alive"
kill -KILL $IMP 2>/dev/null
# the real one: our WATCH.sh path and our config, a live process
( exec -a "bash $KIT_DIR/WATCH.sh $KIT_CONFIG" sleep 30 ) & REAL=$!
sleep 0.5; rm -f "$(watcher_pidfile)"; watcher_claim "$REAL" >/dev/null; chk "claim" "$?" "0"
chk "recognised" "$(watcher_is_ours "$REAL" && echo yes || echo no)" "yes"
watcher_claim 12345 >/dev/null 2>&1; chk "second claim refused" "$?" "2"
KILL_INVOKED_BY_WATCHER=$REAL watcher_stop >/dev/null
chk "the invoking watcher is never signalled by its own KILL" "$(kill -0 $REAL 2>/dev/null && echo alive)" "alive"
watcher_stop >/dev/null; sleep 0.5
chk "otherwise terminated by exact pid" "$(kill -0 $REAL 2>/dev/null && echo alive || echo dead)" "dead"

printf '\n  %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
