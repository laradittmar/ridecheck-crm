#!/usr/bin/env bash
# RideCheck emergency-OFF kit — WATCH.sh.   bash WATCH.sh CONFIG
#
# START IT FIRST (R1, review F-13): the watcher is started BEFORE ENABLE, in PRE-ARMED mode,
# and ENABLE refuses to arm a window without a live pre-armed watcher — so there is no
# unwatched interval between ENABLE and WATCH. Pre-armed, it waits at most
# PREARM_GRACE_SECONDS for ENABLE's watermarks (written before ENABLE changes anything):
#   no watermarks within the grace → nothing was enabled → it says so and exits 6;
#   watermarks present            → every counter below is live AT ONCE (before the
#                                   recreate finishes), the window deadline runs, and the
#                                   explicit ARMED transition happens when the flags read
#                                   exactly ON_TRUE_FLAGS; drift after that is a breach,
#                                   and never reaching it within 300 s is a breach.
#
# A breach runs KILL and the watcher waits for it — at most KILL_DEADLINE_SECONDS (R1):
#   KILL exit 0           → "OFF restored and verified", pidfile removed, exit 3
#   KILL cannot start, or
#   KILL exit non-zero    → BLOCKER, $EVID/INCIDENT_UNRECOVERED (0600), pidfile KEPT, exit 4
#   KILL past its deadline→ BLOCKER, INCIDENT_UNRECOVERED naming KILL's pid (left running,
#                           NOT signalled — it may be mid-recreate), pidfile KEPT, exit 4.
#                           OFF is never claimed.
# It never exits quietly after a breach (2026-11-01: KILL failed with "Permission denied",
# the watcher fell through to `exit 3`, its trap removed the pidfile, and the window stayed
# open while looking like a clean stop).
#
# Counted precisely (NP-08; thresholds per WINDOW_PROFILE, see common.sh):
#   a Flow dispatch is `direction='out' AND message_type='flow'` — a BOOKING_FLOW text such
#   as the booking acknowledgement or the slot-recovery explanation is NOT one;
#   an operator notification is a PROVIDER ATTEMPT — a row SENDING/SENT, any attempt, or an
#   OPERATOR_NOTIFICATION outbound that was not blocked. A HELD row with attempts=0 is a
#   withheld obligation, not a send: it is recorded, never a breach.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
mkdir -p "$EVID/watch"; chmod 700 "$EVID/watch"
guard_target || exit 65
watcher_claim "$$" || exit 2
trap 'rm -f "$(watcher_pidfile)"' EXIT
STARTED=$(date -u +%s)
W="$EVID/enable/watermarks.txt"

kill_now() {
  local why="$1" krc kpid waited=0
  log "WATCH: BREACH -- $why"
  log "WATCH: invoking KILL: bash $KIT_DIR/KILL.sh (waiting for it, at most ${KILL_DEADLINE_SECONDS}s)"
  KILL_INVOKED_BY_WATCHER=$$ bash "$KIT_DIR/KILL.sh" "$KIT_CONFIG" "watcher:$why" &
  kpid=$!
  while kill -0 "$kpid" 2>/dev/null && [ "$waited" -lt "$KILL_DEADLINE_SECONDS" ]; do
    sleep 1; waited=$((waited+1))
  done
  if kill -0 "$kpid" 2>/dev/null; then
    krc="DEADLINE"
  else
    wait "$kpid"; krc=$?
  fi
  if [ "$krc" = "0" ]; then
    log "WATCH: KILL completed -- OFF restored and verified; watcher exiting"
    exit 3
  fi
  trap - EXIT                                     # the pidfile stays: this is not a clean stop
  {
    echo "UNRECOVERED INCIDENT at $(ts)"
    echo "breach: $why"
    if [ "$krc" = "DEADLINE" ]; then
      echo "KILL exit status: none -- still running after the ${KILL_DEADLINE_SECONDS}s deadline"
      echo "KILL pid: $kpid (left running, NOT signalled: it may be mid-recreate)"
    else
      echo "KILL exit status: $krc  (126/127 = KILL could not be started)"
    fi
    echo "watcher pid: $$ (pidfile retained: $(watcher_pidfile))"
    echo "OFF IS NOT VERIFIED."
    echo "ACTION: restore OFF by hand now: bash $KIT_DIR/KILL.sh $KIT_CONFIG manual"
  } > "$EVID/INCIDENT_UNRECOVERED"
  chmod 600 "$EVID/INCIDENT_UNRECOVERED"
  if [ "$krc" = "DEADLINE" ]; then
    log "WATCH: *** BLOCKER *** KILL exceeded its ${KILL_DEADLINE_SECONDS}s deadline (pid $kpid retained) -- OFF NOT VERIFIED. Unrecovered incident recorded at $EVID/INCIDENT_UNRECOVERED; pidfile retained."
  else
    log "WATCH: *** BLOCKER *** KILL exited $krc -- OFF NOT VERIFIED. Unrecovered incident recorded at $EVID/INCIDENT_UNRECOVERED; pidfile retained."
  fi
  exit 4
}

# An instrument that cannot read is a breach, never a zero: an empty count would silently
# disable the check it feeds.
need() { [ -n "$2" ] || kill_now "instrument read failed: $1"; }
check() { local r; r=$(over "$@") && kill_now "$r"; }

# ── pre-armed: wait for ENABLE's watermarks (bounded) ─────────────────────────────────────
log "WATCH: pre-armed; pid $$; waiting up to ${PREARM_GRACE_SECONDS}s for ENABLE"
# Only THIS window's artifacts count: anything older than the watcher is stale.
fresh() { [ -s "$1" ] && [ "$(stat -c %Y "$1" 2>/dev/null || echo 0)" -ge "$STARTED" ]; }
while ! fresh "$W"; do
  now=$(date -u +%s)
  [ "$WINDOW_SECONDS" -le 0 ] && kill_now "deadline reached (0s window)"
  if [ "$((now-STARTED))" -ge "$PREARM_GRACE_SECONDS" ]; then
    log "WATCH: ENABLE did not start within ${PREARM_GRACE_SECONDS}s -- nothing was armed; watcher exiting (6)"
    exit 6
  fi
  sleep "$POLL_SECONDS"
done
log "WATCH: watermarks present -- counters live; profile $WINDOW_PROFILE; flows<=$MAX_FLOW_DISPATCHES offers<=+$MAX_NEW_OFFERS bookings<=+$MAX_NEW_BOOKINGS attempts<=+$MAX_NEW_ATTEMPTS recovery_jobs<=+$MAX_NEW_RECOVERY_JOBS operator_notices<=+$MAX_OPERATOR_NOTIFICATIONS"
WM_AT=$(date -u +%s)

base() { grep "^$1=" "$W" 2>/dev/null | cut -d= -f2; }
B_OUT=$(base max_outbound_id); B_OUT=${B_OUT:-0}
B_REVS=$(base thread_revisions); B_SEC=$(base max_security_event_id)
B_OPN_SENT=$(base operator_notification_attempts); B_OFFERS=$(base booking_offers)
B_ATT=$(base booking_attempt_events); B_USERS=$(base users)
B_JOBS=$(base slot_recovery_jobs)
TESTERS="$(tester_sql_list)"

ARMED=0; HELD_NOTED=0
while :; do
  now=$(date -u +%s)
  if [ -f "$EVID/enable/refused_before_change" ] && [ "$(stat -c %Y "$EVID/enable/refused_before_change")" -ge "$STARTED" ] \
     && ! fresh "$EVID/enable/deadline_start_epoch"; then
    log "WATCH: ENABLE refused before changing anything -- nothing was armed; watcher exiting (6)"
    exit 6
  fi
  START=$WM_AT; fresh "$EVID/enable/deadline_start_epoch" && START=$(cat "$EVID/enable/deadline_start_epoch")
  elapsed=$((now-START))
  [ "$elapsed" -ge "$WINDOW_SECONDS" ] && kill_now "deadline reached (${elapsed}s)"
  # While ENABLE is still recreating the backend (no deadline file yet) the container may
  # briefly not exist: the DATABASE counters are live, the container checks wait for it.
  ENABLED=0; fresh "$EVID/enable/deadline_start_epoch" && ENABLED=1

  revs=$(roq "SELECT count(*) FROM thread_revisions;"); need thread_revisions "$revs"
  check thread_revisions "$revs" "${B_REVS:-0}" "$MAX_NEW_BOOKINGS"
  disp=$(roq "SELECT count(*) FROM whatsapp_messages WHERE direction='out' AND message_type='flow' AND id > $B_OUT;"); need flow_dispatches "$disp"
  [ "${disp:-0}" -gt "$MAX_FLOW_DISPATCHES" ] && kill_now "Flow dispatches = $disp (max $MAX_FLOW_DISPATCHES)"
  if [ -n "$TESTERS" ]; then
    foreign=$(roq "SELECT count(*) FROM whatsapp_messages m JOIN whatsapp_threads t ON t.id=m.thread_id
                   JOIN whatsapp_contacts c ON c.id=t.contact_id
                   WHERE m.direction='out' AND m.id > $B_OUT AND c.wa_id NOT IN ($TESTERS)
                   AND COALESCE(m.path_id,'') <> 'OPERATOR_NOTIFICATION';"); need foreign "$foreign"
    [ "${foreign:-0}" -gt 0 ] && kill_now "outbound to a NON-TESTER recipient"
  fi
  opn=$(roq "SELECT (SELECT count(*) FROM operator_notifications WHERE status IN ('SENDING','SENT') OR attempts > 0)
             + (SELECT count(*) FROM whatsapp_messages WHERE path_id='OPERATOR_NOTIFICATION'
                AND direction='out' AND status <> 'blocked' AND id > $B_OUT);"); need operator_notifications "$opn"
  r=$(over "operator-notification provider attempts" "$opn" "${B_OPN_SENT:-0}" "$MAX_OPERATOR_NOTIFICATIONS") && kill_now "$r"
  held=$(roq "SELECT count(*) FROM operator_notifications WHERE status='HELD' AND attempts = 0;")
  if [ "${held:-0}" -gt 0 ] && [ "$HELD_NOTED" = "0" ]; then
    log "WATCH: note -- $held operator notification(s) HELD with no attempt: withheld, not sent (no breach)"
    HELD_NOTED=1
  fi
  sec=$(roq "SELECT COALESCE(max(id),0) FROM security_events WHERE severity IN ('HIGH','BLOCKER');"); need security_events "$sec"
  [ "${sec:-0}" -gt "${B_SEC:-0}" ] && kill_now "new HIGH/BLOCKER security_event id $sec > ${B_SEC:-0}"
  usr=$(roq "SELECT count(*) FROM users;")
  [ "${usr:-0}" -gt "${B_USERS:-0}" ] && kill_now "users ${B_USERS:-0} -> $usr"
  off=$(roq "SELECT count(*) FROM whatsapp_booking_offers;"); need offers "$off"
  check booking_offers "$off" "${B_OFFERS:-0}" "$MAX_NEW_OFFERS"
  att=$(roq "SELECT count(*) FROM booking_attempt_events;"); need attempts "$att"
  check booking_attempt_events "$att" "${B_ATT:-0}" "$MAX_NEW_ATTEMPTS"
  jobs=$(recovery_jobs); need slot_recovery_jobs "$jobs"
  if [ "${B_JOBS:-absent}" = "absent" ]; then
    [ "$jobs" = "absent" ] || kill_now "slot_recovery_jobs appeared during the window (schema changed)"
  else
    check slot_recovery_jobs "$jobs" "$B_JOBS" "$MAX_NEW_RECOVERY_JOBS"
  fi

  st="$(seven_state)"
  tf=0; ok=1
  for k in "${SEVEN[@]}"; do
    v=$(printf '%s\n' "$st" | grep "^$k=" | cut -d= -f2)
    case " $ON_TRUE_FLAGS " in *" $k "*) want=true ;; *) want=false ;; esac
    [ "$v" = "true" ] && tf=$((tf+1))
    [ "$v" = "$want" ] || ok=0
  done
  if [ "$ok" = "1" ]; then
    [ "$ARMED" = "0" ] && { ARMED=1; log "WATCH: posture armed ($tf true) -- drift is now a breach"; }
  elif [ "$ARMED" = "1" ]; then
    kill_now "flag posture drifted after enable ($tf true)"
  elif [ "$((now-WM_AT))" -gt 300 ]; then
    kill_now "posture never reached the armed state within 300s"
  fi
  if [ "$ENABLED" = "1" ] || [ "$ARMED" = "1" ]; then
    [ "$(docker inspect -f '{{.Image}}' "$BACKEND" 2>/dev/null)" = "$EXPECT_IMAGE" ] || kill_now "image changed or backend missing"
    [ "$(docker inspect -f '{{.RestartCount}}' "$BACKEND" 2>/dev/null)" = "0" ] || kill_now "backend restarted"
  fi
  for pat in "resend" "smtp" "maps.googleapis"; do
    n=$(docker logs "$BACKEND" --since "$((POLL_SECONDS + 1))s" 2>&1 | grep -ci "$pat")
    [ "${n:-0}" -gt 0 ] && kill_now "forbidden provider traffic: $pat"
  done
  printf '%s elapsed=%ss revs=%s flows=%s opn=%s held=%s offers=%s attempts=%s jobs=%s armed=%s\n' \
    "$(ts)" "$elapsed" "$revs" "$disp" "$opn" "$held" "$off" "$att" "$jobs" "$ARMED" >> "$EVID/watch/rolling.log"
  sleep "$POLL_SECONDS"
done
