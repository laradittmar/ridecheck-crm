#!/usr/bin/env bash
# SEALED, DISPOSABLE rehearsal of the emergency-OFF kit against a REAL backend container.
#
#   bash rehearse.sh SCHEMA_SQL ALEMBIC_VERSION IMAGE OUTDIR
#
#   SCHEMA_SQL       a schema-only dump (no rows) the disposable database is built from
#   ALEMBIC_VERSION  the head that schema is at (the image's EXPECTED_HEAD)
#   IMAGE            the backend image to run — used as is: no build, no pull
#   OUTDIR           a NEW directory for evidence (created 0700)
#
# Everything it creates is named rce-<ts>-*: one --internal network (no route out of the
# host), one PostgreSQL 16 on tmpfs, one backend with blank provider credentials. Every
# allowlisted number is synthetic. The kit runs in KIT_MODE=rehearsal, whose guard refuses the
# live project and the live container names. The live containers' ids, images and restart
# counts are read before and after and must be identical.
#
# Run 1 — a REAL breach: the watcher is armed against the ON posture; a BOOKING_FLOW text and
#   a HELD operator notification are injected and must NOT breach; then three Flow dispatches
#   are injected; the watcher must invoke KILL by itself, wait for it, and exit 3; the OFF
#   posture is then verified INDEPENDENTLY of KILL's own log. Two sentinel processes — one
#   whose command line carries "WATCH.sh", "KILL.sh", "pkill", the kit path and the config
#   path — and the rehearsal shell itself must survive.
# Run 2 — a FAILED restoration: the OFF overlay is unreadable; the watcher must record an
#   unrecovered BLOCKER incident, keep its pidfile and exit 4; a manual KILL then restores OFF.
#
# R1 additions (review F-07, F-13):
#   Run 0 — ENABLE refuses to arm without a pre-armed watcher; nothing is recreated.
#   Every run starts WATCH FIRST (pre-armed) and only then ENABLE: no unwatched interval.
#   Run 3 — WINDOW_PROFILE=race: the measured two-customer race shape (3 Flow dispatches, 2
#           FINALISE attempt rows, a HELD notice) does NOT breach; a 4th Flow does -> OFF.
#   Run 4 — KILL cannot start (a kit copy without KILL.sh) -> BLOCKER, exit 4; manual KILL.
#   Run 5 — KILL hangs (a kit copy whose KILL.sh never returns) -> the watcher's
#           KILL_DEADLINE_SECONDS fires: BLOCKER, INCIDENT_UNRECOVERED names KILL's pid
#           (left running, not signalled), exit 4, OFF never claimed; manual KILL.
#   The credential check is labelled for what it proves: the REHEARSAL base env has blank
#   WhatsApp transport credentials. A LIVE OFF posture keeps WHATSAPP_TOKEN by design, and
#   KILL verifies that dispatch is disabled -- not that every live credential is blank.
set -uo pipefail
SCHEMA="$1"; HEAD="$2"; IMAGE="$3"; OUT="$4"
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ ! -e "$OUT" ] || { echo "OUTDIR exists: $OUT" >&2; exit 64; }
umask 077; mkdir -p "$OUT"; chmod 700 "$OUT"
TS=$(date -u +%Y%m%d%H%M%S); P="rce-$TS"; NET="$P-net"; PGN="$P-pg"; BE="$P-backend"
PGIMG="${PGIMG:-postgres:16}"
R="$OUT/rehearsal.log"; PASS=0; FAIL=0
say()  { printf '%s  %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$R"; }
ok()   { PASS=$((PASS+1)); say "PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); say "FAIL  $1"; }
chk()  { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (got '$2', want '$3')"; fi; }
live() { for c in ridecheck-crm-backend-1 ridecheck-crm-postgres-1 ridecheck-crm-n8n-1; do
           docker inspect -f '{{.Name}} {{.Id}} {{.Image}} {{.RestartCount}} {{.State.StartedAt}}' "$c" 2>&1
         done; }
cleanup() {
  for c in "$BE" "$PGN"; do docker rm -f "$c" >/dev/null 2>&1; done
  docker network rm "$NET" >/dev/null 2>&1
}
trap cleanup EXIT

live > "$OUT/live_before.txt"
IMGID=$(docker image inspect -f '{{.Id}}' "$IMAGE") || { echo "no such image" >&2; exit 64; }
say "rehearsal $P image $IMGID head $HEAD"

# ── the disposable stack ────────────────────────────────────────────────────────────────
docker network create --internal "$NET" >/dev/null
docker run -d --name "$PGN" --network "$NET" --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER=crm -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=crm_rehearsal "$PGIMG" >/dev/null
for i in $(seq 1 60); do docker exec "$PGN" pg_isready -U crm -d crm_rehearsal >/dev/null 2>&1 && break; sleep 1; done
sleep 2
sqlw() { docker exec -i "$PGN" psql -X -q -v ON_ERROR_STOP=1 -U crm -d crm_rehearsal "$@"; }
sqlw -f - < "$SCHEMA" > "$OUT/restore.log" 2>&1 || { say "restore failed"; exit 1; }
sqlw -c "INSERT INTO alembic_version VALUES ('$HEAD')" >/dev/null

T1=5490000000001; T2=5490000000002              # synthetic: not a valid AR mobile number
SHA1=$(printf '%s' "$T1" | sha256sum | cut -c1-16)
AUTHKEY=$(head -c 32 /dev/urandom | sha256sum | cut -c1-64)   # per rehearsal, never committed
env_common() { cat <<EOF
      DATABASE_URL: postgresql+psycopg://crm:pw@$PGN:5432/crm_rehearsal
      TZ: "America/Argentina/Buenos_Aires"
      OUTBOUND_ENABLED: "\${BETA_OUTBOUND_ENABLED:-false}"
      OPERATOR_WHATSAPP_NOTIFY_ENABLED: "false"
      CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED: "false"
      WHATSAPP_TOKEN: ""
      WHATSAPP_PHONE_NUMBER_ID: ""
      N8N_WEBHOOK_URL: ""
      SMTP_PASSWORD: ""
      AUTH_SECRET_KEY: "$AUTHKEY"
EOF
}
cat > "$OUT/base.yml" <<EOF
services:
  backend:
    image: $IMAGE
    container_name: $BE
    networks: [rce]
    environment:
$(env_common)
      SMART_BOOKING_CE_ENABLED: "false"
      EVIDENCE_ADMISSION_ENABLED: "false"
      OUTSTANDING_QUESTION_ENABLED: "false"
      LOCALITY_RESOLUTION_SAFETY_ENABLED: "false"
      CLOSED_BETA_ALLOWED_WA_IDS: "$T1"
      RESEND_API_KEY: ""
networks:
  rce:
    external: true
    name: $NET
EOF
cat > "$OUT/on.yml" <<EOF
services:
  backend:
    environment:
      CLOSED_BETA_ALLOWED_WA_IDS: "$T1,$T2"
      SMART_BOOKING_CE_ENABLED: "true"
      EVIDENCE_ADMISSION_ENABLED: "true"
      OUTSTANDING_QUESTION_ENABLED: "true"
      LOCALITY_RESOLUTION_SAFETY_ENABLED: "true"
      RESEND_API_KEY: ""
EOF
cat > "$OUT/off.yml" <<EOF
services:
  backend:
    environment:
      CLOSED_BETA_ALLOWED_WA_IDS: "$T1"
      SMART_BOOKING_CE_ENABLED: "false"
      EVIDENCE_ADMISSION_ENABLED: "false"
      OUTSTANDING_QUESTION_ENABLED: "false"
      LOCALITY_RESOLUTION_SAFETY_ENABLED: "false"
      RESEND_API_KEY: ""
EOF
( cd "$OUT" && docker compose -p "$P" -f base.yml up -d --no-build --pull never backend ) > "$OUT/up.log" 2>&1
cfg() {   # $1 = run dir, $2 = OFF overlay path, $3 = extra config lines
  mkdir -p "$1"; chmod 700 "$1"
  cat > "$1/kit.env" <<EOF
EV_ROOT=$1
BACKEND=$BE
PGC=$PGN
DB=crm_rehearsal
DB_PASSWORD=pw
COMPOSE_PROJECT=$P
COMPOSE_DIR=$OUT
COMPOSE_FILES=$OUT/base.yml
ON_OVERLAY=$OUT/on.yml
OFF_OVERLAY=$2
EXPECT_IMAGE=$IMGID
EXPECT_HEAD=$HEAD
TESTER_WA_IDS=$T1,$T2
OFF_ALLOWLIST_COUNT=1
OFF_ALLOWLIST_SHA16=$SHA1
ON_TRUE_FLAGS="OUTBOUND_ENABLED SMART_BOOKING_CE_ENABLED EVIDENCE_ADMISSION_ENABLED OUTSTANDING_QUESTION_ENABLED LOCALITY_RESOLUTION_SAFETY_ENABLED"
KIT_MODE=rehearsal
READY_MODE=exec
READY_URL=http://127.0.0.1:8000/login
POLL_SECONDS=1
WINDOW_SECONDS=900
SENDER_CHECK_SECONDS=${SENDER_CHECK_SECONDS:-65}
${3:-}
EOF
  chmod 600 "$1/kit.env"; echo "$1/kit.env"
}
C1=$(cfg "$OUT/run1" "$OUT/off.yml")
source "$KIT/common.sh" "$C1" >/dev/null 2>&1          # for ready / seven_state / interlock
for i in $(seq 1 90); do ready && break; sleep 1; done
ready && ok "disposable backend ready (200 inside the container)" || { bad "backend never ready"; docker logs "$BE" > "$OUT/backend_never_ready.log" 2>&1; exit 1; }

# synthetic customer rows for the two testers
sqlw -c "INSERT INTO whatsapp_contacts (wa_id, display_name) VALUES ('$T1','Rehearsal A'),('$T2','Rehearsal B');
         INSERT INTO whatsapp_threads (contact_id, unread_count) SELECT id, 0 FROM whatsapp_contacts;" >/dev/null
inject_flows() {   # $1 = count, $2 = message_type, $3 = path
  sqlw -c "INSERT INTO whatsapp_messages (thread_id, direction, timestamp, message_type, status, automated, path_id, text)
           SELECT t.id, 'out', now(), '$2', 'sent', true, '$3', 'rehearsal injected'
           FROM whatsapp_threads t JOIN whatsapp_contacts c ON c.id=t.contact_id, generate_series(1, $1)
           WHERE c.wa_id='$T1';" >/dev/null
}
watch_wait() {     # $1 = pid, $2 = seconds, $3 = rc file; echoes the watcher's exit status
  local i
  for i in $(seq 1 "$2"); do kill -0 "$1" 2>/dev/null || break; sleep 1; done
  if kill -0 "$1" 2>/dev/null; then echo "STILL_RUNNING"; else cat "$3" 2>/dev/null || echo "NO_RC"; fi
}
verify_off() {     # $1 = label — read from INSIDE the container, independently of KILL
  local st il
  st="$(seven_state)"; il="$(interlock)"
  chk "$1: seven flags false" "$(printf '%s\n' "$st" | grep -c '=false$')" "7"
  chk "$1: original allowlist (count)" "$(printf '%s\n' "$il" | grep -c '^allowlist=1$')" "1"
  chk "$1: original allowlist (digest)" "$(printf '%s\n' "$il" | grep -c "^allowlist_sha16=$SHA1\$")" "1"
  chk "$1: Resend key blank" "$(printf '%s\n' "$il" | grep -c 'resend_key_set=False')" "1"
  chk "$1: SMTP password blank" "$(printf '%s\n' "$il" | grep -c 'smtp_password_set=False')" "1"
  chk "$1: booking dispatch disallowed" "$(printf '%s\n' "$il" | grep -c 'booking_dispatch_allowed=False')" "1"
  # F-13: true of the REHEARSAL base env only; a live OFF keeps WHATSAPP_TOKEN by design
  chk "$1: rehearsal base env has blank WhatsApp transport credentials (rehearsal-only fact)" \
      "$(docker exec "$BE" sh -c '[ -z "$WHATSAPP_TOKEN" ] && echo blank')" "blank"
  chk "$1: readiness 200" "$(ready && echo 200 || echo no)" "200"
}

prearm() {         # $1 = kit dir, $2 = config, $3 = run dir -> starts WATCH, echoes its pid
  # the subshell must not hold this function's stdout: W=$(prearm …) would otherwise block
  # until the watcher exits (measured on the first R1 rehearsal attempt)
  ( bash "$1/WATCH.sh" "$2" > "$3/watch.out" 2>&1; echo $? > "$3/watch.rc" ) \
    </dev/null >/dev/null 2>&1 &
  local w=$! i
  for i in $(seq 1 30); do grep -q 'WATCH: pre-armed' "$3/evidence/timeline.log" 2>/dev/null && break; sleep 1; done
  echo "$w"
}
order_ok() {       # the watcher was pre-armed BEFORE ENABLE began (timeline order)
  awk '/WATCH: pre-armed/{w=NR} /ENABLE: begin/{if(!e)e=NR} END{print (w && e && w<e) ? "yes" : "no"}' "$1"
}

# ── RUN 0: ENABLE refuses without a pre-armed watcher ───────────────────────────────────
say "RUN 0 — ENABLE without a watcher"
C0=$(cfg "$OUT/run0" "$OUT/off.yml")
id_before=$(docker inspect -f '{{.Id}}' "$BE")
bash "$KIT/ENABLE.sh" "$C0" > "$OUT/run0/enable.out" 2>&1; chk "ENABLE refused (2)" "$?" "2"
chk "refusal names the missing watcher" "$(grep -c 'no pre-armed watcher' "$OUT/run0/evidence/timeline.log")" "1"
chk "backend not recreated" "$(docker inspect -f '{{.Id}}' "$BE")" "$id_before"

# ── RUN 1: a real breach ────────────────────────────────────────────────────────────────
say "RUN 1 — real breach (watcher started first)"
( exec -a "bash $KIT/WATCH.sh $C1 KILL.sh pkill -f killall" sleep 1800 ) & S1=$!
( exec -a "sentinel-unrelated" sleep 1800 ) & S2=$!
W1=$(prearm "$KIT" "$C1" "$OUT/run1")
chk "watcher pre-armed before ENABLE" "$(grep -c 'WATCH: pre-armed' "$OUT/run1/evidence/timeline.log")" "1"
bash "$KIT/ENABLE.sh" "$C1" > "$OUT/run1/enable.out" 2>&1; chk "ENABLE armed the window" "$?" "0"
chk "timeline: pre-armed watcher precedes ENABLE" "$(order_ok "$OUT/run1/evidence/timeline.log")" "yes"
for i in $(seq 1 60); do grep -q 'posture armed' "$OUT/run1/evidence/timeline.log" 2>/dev/null && break; sleep 1; done
chk "watcher armed on the ON posture" "$(grep -c 'posture armed' "$OUT/run1/evidence/timeline.log")" "1"
# NOT breaches: a BOOKING_FLOW text (the acknowledgement shape) and a HELD notification
inject_flows 3 text BOOKING_FLOW
sqlw -c "INSERT INTO booking_commands (idempotency_key, source, operation, request_fingerprint, occupying)
         VALUES ('rehearsal:held', 'API', 'CANCEL', repeat('0', 64), false);
         INSERT INTO operator_notifications (booking_command_id, identity, kind, status, attempts, provider, last_error)
         SELECT id, repeat('a', 64), 'BOOKING_CREATED', 'HELD', 0, 'META_WHATSAPP_TEMPLATE', 'NOT_CONFIGURED_DISABLED'
         FROM booking_commands WHERE idempotency_key='rehearsal:held';" >/dev/null
sleep 6
chk "BOOKING_FLOW texts and a HELD notice did not breach" \
    "$(grep -c 'BREACH' "$OUT/run1/evidence/timeline.log")" "0"
chk "the HELD notice was recorded as withheld, not sent" \
    "$(grep -c 'HELD with no attempt' "$OUT/run1/evidence/timeline.log")" "1"
chk "watcher still running" "$(kill -0 $W1 2>/dev/null && echo yes)" "yes"
inject_flows 3 flow BOOKING_FLOW                       # the breach: 3 > MAX_FLOW_DISPATCHES=2
rc=$(watch_wait $W1 300 "$OUT/run1/watch.rc")
chk "watcher invoked KILL itself, waited, and exited 3" "$rc" "3"
chk "breach recorded" "$(grep -c 'BREACH -- Flow dispatches = 3' "$OUT/run1/evidence/timeline.log")" "1"
chk "KILL started by the watcher" "$(grep -c 'KILL: begin (reason: watcher:Flow dispatches' "$OUT/run1/evidence/timeline.log")" "1"
chk "KILL verified OFF" "$(grep -c 'KILL: COMPLETE -- OFF posture restored and verified' "$OUT/run1/evidence/timeline.log")" "1"
chk "watcher reported the verified stop" "$(grep -c 'OFF restored and verified; watcher exiting' "$OUT/run1/evidence/timeline.log")" "1"
chk "no Permission denied anywhere" "$(cat "$OUT/run1/watch.out" "$OUT/run1/evidence/timeline.log" | grep -c 'Permission denied')" "0"
verify_off "RUN 1"
chk "sentinel with a matching command line survived" "$(kill -0 $S1 2>/dev/null && echo alive)" "alive"
chk "unrelated sentinel survived" "$(kill -0 $S2 2>/dev/null && echo alive)" "alive"
chk "the rehearsal shell survived" "$(kill -0 $$ 2>/dev/null && echo alive)" "alive"

# ── RUN 2: the restoration itself fails ─────────────────────────────────────────────────
say "RUN 2 — failed restoration must be loud"
C2=$(cfg "$OUT/run2" "$OUT/does-not-exist.yml" "SENDER_CHECK_SECONDS=0")
W2=$(prearm "$KIT" "$C2" "$OUT/run2")
bash "$KIT/ENABLE.sh" "$C2" > "$OUT/run2/enable.out" 2>&1; chk "ENABLE re-armed" "$?" "0"
chk "run 2: pre-armed watcher precedes ENABLE" "$(order_ok "$OUT/run2/evidence/timeline.log")" "yes"
for i in $(seq 1 60); do grep -q 'posture armed' "$OUT/run2/evidence/timeline.log" 2>/dev/null && break; sleep 1; done
inject_flows 3 flow BOOKING_FLOW
rc=$(watch_wait $W2 300 "$OUT/run2/watch.rc")
chk "watcher exit 4 on a failed KILL" "$rc" "4"
chk "BLOCKER recorded" "$(grep -c '\*\*\* BLOCKER \*\*\*' "$OUT/run2/evidence/timeline.log")" "1"
chk "unrecovered incident file" "$([ -s "$OUT/run2/evidence/INCIDENT_UNRECOVERED" ] && echo yes)" "yes"
chk "pidfile retained" "$([ -f "$OUT/run2/evidence/watch/watcher.pid" ] && echo yes)" "yes"
bash "$KIT/KILL.sh" "$C1" manual-after-incident > "$OUT/run2/manual_kill.out" 2>&1
chk "manual KILL restored and verified OFF" "$?" "0"
verify_off "RUN 2 manual"

# ── RUN 3: the race profile absorbs the measured race, and only that ────────────────────
say "RUN 3 — WINDOW_PROFILE=race"
C3=$(cfg "$OUT/run3" "$OUT/off.yml" "WINDOW_PROFILE=race
SENDER_CHECK_SECONDS=0")
W3=$(prearm "$KIT" "$C3" "$OUT/run3")
bash "$KIT/ENABLE.sh" "$C3" > "$OUT/run3/enable.out" 2>&1; chk "race: ENABLE armed" "$?" "0"
for i in $(seq 1 60); do grep -q 'posture armed' "$OUT/run3/evidence/timeline.log" 2>/dev/null && break; sleep 1; done
chk "race: profile thresholds announced" \
    "$(grep -c 'profile race; flows<=3 offers<=+3 bookings<=+2 attempts<=+4 recovery_jobs<=+1' "$OUT/run3/evidence/timeline.log")" "1"
inject_flows 2 flow BOOKING_FLOW                       # the two customers' original Flows
inject_flows 2 text BOOKING_FLOW                       # the winner's ack + the explanation
inject_flows 1 flow BOOKING_FLOW                       # the loser's recovery Flow
sqlw -c "INSERT INTO booking_attempt_events (event_uid, source, stage, outcome, reason)
         VALUES ('rehearsal-r3-1', 'WHATSAPP_FLOW', 'FINALISE', 'REFUSED', 'SLOT_NO_LONGER_AVAILABLE'),
                ('rehearsal-r3-2', 'WHATSAPP_FLOW', 'FINALISE', 'REFUSED', 'SLOT_NO_LONGER_AVAILABLE');" >/dev/null
sleep 6
chk "race: the measured race shape did not breach" "$(grep -c 'BREACH' "$OUT/run3/evidence/timeline.log")" "0"
chk "race: watcher still running" "$(kill -0 $W3 2>/dev/null && echo yes)" "yes"
inject_flows 1 flow BOOKING_FLOW                       # one Flow beyond the race
rc=$(watch_wait $W3 300 "$OUT/run3/watch.rc")
chk "race: the 4th Flow breached; KILL ran; watcher exit 3" "$rc" "3"
chk "race: breach recorded" "$(grep -c 'BREACH -- Flow dispatches = 4 (max 3)' "$OUT/run3/evidence/timeline.log")" "1"
verify_off "RUN 3"

# ── RUN 4: KILL cannot start ────────────────────────────────────────────────────────────
say "RUN 4 — KILL cannot start"
K4="$OUT/kit_nokill"; mkdir -p "$K4"; cp "$KIT/common.sh" "$KIT/watcher_pid.sh" "$KIT/WATCH.sh" "$KIT/ENABLE.sh" "$K4/"
C4=$(cfg "$OUT/run4" "$OUT/off.yml" "SENDER_CHECK_SECONDS=0")
W4=$(prearm "$K4" "$C4" "$OUT/run4")
bash "$K4/ENABLE.sh" "$C4" > "$OUT/run4/enable.out" 2>&1; chk "run 4: ENABLE armed" "$?" "0"
for i in $(seq 1 60); do grep -q 'posture armed' "$OUT/run4/evidence/timeline.log" 2>/dev/null && break; sleep 1; done
inject_flows 3 flow BOOKING_FLOW
rc=$(watch_wait $W4 120 "$OUT/run4/watch.rc")
chk "run 4: watcher exit 4" "$rc" "4"
chk "run 4: incident names exit 127" "$(grep -c 'KILL exit status: 127' "$OUT/run4/evidence/INCIDENT_UNRECOVERED")" "1"
chk "run 4: BLOCKER" "$(grep -c '\*\*\* BLOCKER \*\*\*' "$OUT/run4/evidence/timeline.log")" "1"
bash "$KIT/KILL.sh" "$C4" manual-after-run4 > "$OUT/run4/manual_kill.out" 2>&1
chk "run 4: manual KILL restored and verified OFF" "$?" "0"
verify_off "RUN 4 manual"

# ── RUN 5: KILL hangs past its deadline ─────────────────────────────────────────────────
say "RUN 5 — KILL timeout"
K5="$OUT/kit_hang"; mkdir -p "$K5"; cp "$KIT/common.sh" "$KIT/watcher_pid.sh" "$KIT/WATCH.sh" "$KIT/ENABLE.sh" "$K5/"
printf '%s\n' '#!/usr/bin/env bash' 'echo $$ > "$(dirname "$0")/kill.pid"' 'sleep 1800' > "$K5/KILL.sh"
C5=$(cfg "$OUT/run5" "$OUT/off.yml" "SENDER_CHECK_SECONDS=0
KILL_DEADLINE_SECONDS=5")
W5=$(prearm "$K5" "$C5" "$OUT/run5")
bash "$K5/ENABLE.sh" "$C5" > "$OUT/run5/enable.out" 2>&1; chk "run 5: ENABLE armed" "$?" "0"
for i in $(seq 1 60); do grep -q 'posture armed' "$OUT/run5/evidence/timeline.log" 2>/dev/null && break; sleep 1; done
inject_flows 3 flow BOOKING_FLOW
rc=$(watch_wait $W5 60 "$OUT/run5/watch.rc")
chk "run 5: watcher exit 4 after the KILL deadline" "$rc" "4"
KP=$(cat "$K5/kill.pid" 2>/dev/null)
chk "run 5: incident: KILL still running after the deadline" \
    "$(grep -c 'still running after the 5s deadline' "$OUT/run5/evidence/INCIDENT_UNRECOVERED")" "1"
chk "run 5: incident names KILL's pid" "$(grep -c "KILL pid: $KP " "$OUT/run5/evidence/INCIDENT_UNRECOVERED")" "1"
chk "run 5: KILL left running (not signalled)" "$(kill -0 "$KP" 2>/dev/null && echo alive)" "alive"
chk "run 5: OFF never claimed" "$(grep -c 'OFF restored and verified' "$OUT/run5/evidence/timeline.log")" "0"
chk "run 5: pidfile retained" "$([ -f "$OUT/run5/evidence/watch/watcher.pid" ] && echo yes)" "yes"
[ -n "$KP" ] && kill -KILL "$KP" 2>/dev/null           # the exact pid of the hung stand-in
bash "$KIT/KILL.sh" "$C5" manual-after-run5 > "$OUT/run5/manual_kill.out" 2>&1
chk "run 5: manual KILL restored and verified OFF" "$?" "0"
verify_off "RUN 5 manual"

chk "sentinels still alive" "$(kill -0 $S1 2>/dev/null && kill -0 $S2 2>/dev/null && echo alive)" "alive"
chk "the rehearsal shell survived every run" "$(kill -0 $$ 2>/dev/null && echo alive)" "alive"
kill -TERM "$S1" "$S2" 2>/dev/null                    # exact pids we started

# ── live isolation ──────────────────────────────────────────────────────────────────────
live > "$OUT/live_after.txt"
chk "live containers identical (id, image, restarts, start time)" \
    "$(cmp -s "$OUT/live_before.txt" "$OUT/live_after.txt" && echo same)" "same"
docker logs "$BE" > "$OUT/backend_final.log" 2>&1
say "SUMMARY $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
