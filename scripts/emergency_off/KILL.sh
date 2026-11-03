#!/usr/bin/env bash
# RideCheck emergency-OFF kit — KILL.sh.   bash KILL.sh CONFIG [reason]
#
# Returns the backend to the certified OFF posture and the ORIGINAL allowlist. It never
# touches the database. EXIT STATUS IS THE VERDICT: 0 only when every check below passed,
# read from INSIDE the recreated container; anything else is non-zero, so a caller (the
# watcher) can tell a restoration from a failed one. The 2026-11-01 kit always exited 0.
#
# ORDER: the watcher first (exact PID, never blocking), the backend STOPPED before anything
# else, then logs captured, then the OFF overlay recreated, then verification.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
REASON="${2:-manual}"
mkdir -p "$EVID/kill"; chmod 700 "$EVID/kill"
log "KILL: begin (reason: $REASON, mode: $KIT_MODE)"
guard_target || { log "KILL: *** REFUSED by the target guard -- nothing was touched"; exit 65; }

watcher_stop || log "KILL/0: watcher stop reported a fault; restoring OFF regardless"

log "KILL/1: stopping $BACKEND"
docker stop "$BACKEND" >/dev/null 2>&1
log "KILL/1: stopped; running=$(docker inspect -f '{{.State.Running}}' "$BACKEND" 2>/dev/null)"
docker logs "$BACKEND" > "$EVID/kill/backend_full.log" 2>&1
chmod 600 "$EVID/kill/backend_full.log"
log "KILL/2: captured $(wc -l < "$EVID/kill/backend_full.log") log lines"

log "KILL/3: recreating backend with the OFF overlay"
( unset BETA_OUTBOUND_ENABLED
  compose -f "$OFF_OVERLAY" up -d --no-deps --no-build --pull never --force-recreate backend ) \
  >> "$EVID/kill/recreate.log" 2>&1
crc=$?
log "KILL/3: compose exit=$crc"
for i in $(seq 1 60); do [ "$(docker inspect -f '{{.State.Running}}' "$BACKEND" 2>/dev/null)" = "true" ] && break; sleep 1; done

bad=0
[ "$crc" = "0" ] || { log "KILL/3: *** compose failed"; bad=1; }
seven_state > "$EVID/kill/seven_after.txt"
[ "$(grep -c '=' "$EVID/kill/seven_after.txt")" = "7" ] || { log "KILL/4: *** could not read the seven flags"; bad=1; }
while IFS='=' read -r k v; do
  [ "$v" = "false" ] && log "KILL/4: $k = 'false'  OK" || { log "KILL/4: *** $k = '$v'"; bad=1; }
done < "$EVID/kill/seven_after.txt"

interlock > "$EVID/kill/interlock_after.txt"
grep -q 'booking_dispatch_allowed=False' "$EVID/kill/interlock_after.txt" || { log "KILL/5: *** dispatch still allowed"; bad=1; }
grep -q "^allowlist=$OFF_ALLOWLIST_COUNT\$" "$EVID/kill/interlock_after.txt" || { log "KILL/5: *** allowlist count is not the original"; bad=1; }
grep -q "^allowlist_sha16=$OFF_ALLOWLIST_SHA16\$" "$EVID/kill/interlock_after.txt" || { log "KILL/5: *** allowlist is not the original"; bad=1; }
grep -q 'resend_key_set=False' "$EVID/kill/interlock_after.txt" || { log "KILL/5: *** RESEND key set"; bad=1; }
grep -q 'smtp_password_set=False' "$EVID/kill/interlock_after.txt" || { log "KILL/5: *** SMTP password set"; bad=1; }
for i in $(seq 1 60); do ready && break; sleep 1; done
if ready; then log "KILL/5: readiness 200 (after ${i}s)"; else log "KILL/5: *** NOT READY after 60s"; bad=1; fi

log "KILL/6: pending outbound = $(roq "SELECT count(*) FROM whatsapp_messages WHERE direction='out' AND (status IS NULL OR status='pending');")"
log "KILL/6: alembic head = $(roq "SELECT version_num FROM alembic_version;") (expected $EXPECT_HEAD)"
img=$(docker inspect -f '{{.Image}}' "$BACKEND" 2>/dev/null)
[ "$img" = "$EXPECT_IMAGE" ] || { log "KILL/6: *** image is $img, expected $EXPECT_IMAGE"; bad=1; }
log "KILL/6: evidence RETAINED -- no row deleted here"
if [ "$SENDER_CHECK_SECONDS" -gt 0 ] 2>/dev/null; then
  sleep "$SENDER_CHECK_SECONDS"
  for s in unanswered_alert quote_followup buscando_followup; do
    rep=$(docker logs "$BACKEND" --since "$((SENDER_CHECK_SECONDS + 5))s" 2>&1 | grep "$s" | grep -c "candidates=")
    none=$(docker logs "$BACKEND" --since "$((SENDER_CHECK_SECONDS + 5))s" 2>&1 | grep "$s" | grep -c "candidates=none")
    if [ "${rep:-0}" -ne "${none:-0}" ]; then log "KILL/6: *** $s reported a CANDIDATE"; bad=1
    else log "KILL/6: $s -- $none/$rep reporting passes say candidates=none"; fi
  done
else
  log "KILL/6: sender-candidate check skipped (SENDER_CHECK_SECONDS=0)"
fi
if [ "$bad" = "0" ]; then
  log "KILL: COMPLETE -- OFF posture restored and verified"; exit 0
fi
log "KILL: COMPLETE WITH FAULTS -- see *** lines above"; exit 1
