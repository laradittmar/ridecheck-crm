#!/usr/bin/env bash
# RideCheck emergency-OFF kit — ENABLE.sh.   bash ENABLE.sh CONFIG
#
# Arms a bounded window with the ON overlay and nothing else. It refuses rather than
# improvises: a wrong image or schema head, a pending outbound row, an unsent operator
# notification that is not HELD-without-attempt, or a posture other than exactly
# ON_TRUE_FLAGS stops it — and a refusal after the recreate runs KILL (with `bash`, never as
# a command) and reports KILL's verdict.
#
# R1 (review F-13): there is no unwatched interval. ENABLE refuses unless THIS window's
# watcher is already running PRE-ARMED (`bash WATCH.sh CONFIG &` first); the watcher's
# counters go live the moment the watermarks below exist, before anything is recreated. A
# refusal before any change leaves `refused_before_change`, and the watcher then exits 6.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
mkdir -p "$EVID/enable"; chmod 700 "$EVID/enable"
log "ENABLE: begin (mode: $KIT_MODE, profile: $WINDOW_PROFILE)"
guard_target || { log "ENABLE: REFUSED by the target guard"; exit 65; }
early_refuse() {
  log "ENABLE: REFUSED -- $1"
  : > "$EVID/enable/refused_before_change"; chmod 600 "$EVID/enable/refused_before_change"
  exit 2
}
if ! watcher_is_ours "$(cat "$(watcher_pidfile)" 2>/dev/null)"; then
  log "ENABLE: REFUSED -- no pre-armed watcher for this window. Start it first: bash $KIT_DIR/WATCH.sh $KIT_CONFIG &"
  exit 2
fi
rm -f "$EVID/enable/refused_before_change" "$EVID/enable/deadline_start_epoch"
log "ENABLE/0: pre-armed watcher $(cat "$(watcher_pidfile)") is running"

seven_state > "$EVID/enable/seven_before.txt"
interlock   > "$EVID/enable/interlock_before.txt"
{
  echo "alembic_head=$(roq "SELECT version_num FROM alembic_version;")"
  echo "max_outbound_id=$(roq "SELECT COALESCE(max(id),0) FROM whatsapp_messages WHERE direction='out';")"
  echo "max_security_event_id=$(roq "SELECT COALESCE(max(id),0) FROM security_events WHERE severity IN ('HIGH','BLOCKER');")"
  echo "booking_offers=$(roq "SELECT count(*) FROM whatsapp_booking_offers;")"
  echo "booking_attempt_events=$(roq "SELECT count(*) FROM booking_attempt_events;")"
  echo "thread_revisions=$(roq "SELECT count(*) FROM thread_revisions;")"
  echo "operator_notification_attempts=$(roq "SELECT (SELECT count(*) FROM operator_notifications WHERE status IN ('SENDING','SENT') OR attempts > 0) + 0;")"
  echo "users=$(roq "SELECT count(*) FROM users;")"
  echo "slot_recovery_jobs=$(recovery_jobs)"
} > "$EVID/enable/watermarks.tmp"
chmod 600 "$EVID/enable/watermarks.tmp"
if grep -q '=$' "$EVID/enable/watermarks.tmp"; then
  early_refuse "a watermark could not be read"
fi
mv "$EVID/enable/watermarks.tmp" "$EVID/enable/watermarks.txt"      # atomic for the watcher
log "ENABLE/1: watermarks written (the pre-armed watcher's counters are live from here)"

img=$(docker inspect -f '{{.Image}}' "$BACKEND")
[ "$img" = "$EXPECT_IMAGE" ] || early_refuse "image is $img"
head=$(roq "SELECT version_num FROM alembic_version;")
[ "$head" = "$EXPECT_HEAD" ] || early_refuse "schema head is '$head'"
[ "$(roq "SELECT count(*) FROM whatsapp_messages WHERE direction='out' AND (status IS NULL OR status='pending');")" = "0" ] \
  || early_refuse "a pending outbound row exists"
[ "$(roq "SELECT count(*) FROM operator_notifications WHERE status IN ('PENDING','SENDING');")" = "0" ] \
  || early_refuse "a sendable operator notification exists"
grep -q "^allowlist=$OFF_ALLOWLIST_COUNT\$" "$EVID/enable/interlock_before.txt" \
  && grep -q "^allowlist_sha16=$OFF_ALLOWLIST_SHA16\$" "$EVID/enable/interlock_before.txt" \
  || early_refuse "the current allowlist is not the recorded original"
watcher_is_ours "$(cat "$(watcher_pidfile)" 2>/dev/null)" || early_refuse "the pre-armed watcher is gone"
log "ENABLE/2: refusal checks passed"

log "ENABLE/3: recreating backend with the ON overlay"
( export BETA_OUTBOUND_ENABLED=true
  compose -f "$ON_OVERLAY" up -d --no-deps --no-build --pull never --force-recreate backend ) \
  >> "$EVID/enable/recreate.log" 2>&1
log "ENABLE/3: compose exit=$?"
for i in $(seq 1 60); do ready && break; sleep 1; done

seven_state > "$EVID/enable/seven_after.txt"
interlock   > "$EVID/enable/interlock_after.txt"
bad=0
ready || { log "ENABLE/4: *** not ready"; bad=1; }
for k in "${SEVEN[@]}"; do
  v=$(grep "^$k=" "$EVID/enable/seven_after.txt" | cut -d= -f2)
  case " $ON_TRUE_FLAGS " in *" $k "*) want=true ;; *) want=false ;; esac
  [ "$v" = "$want" ] || { log "ENABLE/4: *** $k = '$v', expected '$want'"; bad=1; }
done
grep -q 'resend_key_set=False' "$EVID/enable/interlock_after.txt" || { log "ENABLE/4: *** RESEND key is set"; bad=1; }
grep -q 'smtp_password_set=False' "$EVID/enable/interlock_after.txt" || { log "ENABLE/4: *** SMTP password is set"; bad=1; }
if [ "$bad" != "0" ]; then
  log "ENABLE: REFUSED after recreate -- running KILL"
  bash "$KIT_DIR/KILL.sh" "$KIT_CONFIG" enable-posture-wrong
  log "ENABLE: KILL exit=$?"
  exit 2
fi
date -u +%s > "$EVID/enable/deadline_start_epoch"
log "ENABLE: COMPLETE -- window enabled, hard deadline $WINDOW_SECONDS s; the pre-armed watcher arms on the ON posture"
