# Emergency-OFF kit (ENABLE / WATCH / KILL)

Versioned containment for a bounded live window. Introduced by
PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION (E) after the 2026-11-01 probe
(NP-03): the watcher detected a breach, but `kill_now` invoked `KILL.sh` **as a command**,
every kit file was mode 0600, `execve` failed with *Permission denied*, the watcher fell
through to `exit 3`, its trap removed the pidfile, and containment never ran.
Corrected by …-CORRECTION-R1 (independent review F-07, F-08, F-13): race-window
thresholds, a bounded KILL deadline, and a watcher that is running before anything is
enabled.

## Rules this kit enforces

* **Always `bash /exact/path/SCRIPT.sh CONFIG`.** No script relies on an execute bit; the
  files may stay 0600.
* **WATCH first, then ENABLE (R1).** The watcher starts PRE-ARMED; ENABLE refuses to arm a
  window unless this window's watcher is alive. The watcher's database counters go live the
  moment ENABLE writes its watermarks — before the backend is recreated — so there is no
  unwatched interval. A watcher whose ENABLE never starts (within `PREARM_GRACE_SECONDS`,
  default 300) or refuses before changing anything exits 6 without running KILL. The ARMED
  transition is explicit: it happens when the flags read exactly `ON_TRUE_FLAGS`; drift
  after it is a breach, and never reaching it within 300 s is a breach.
* **KILL's exit status is its verdict.** 0 only when, read from inside the recreated
  container: all seven flags are `false`, booking dispatch is disallowed, the allowlist has the
  ORIGINAL count and digest, the Resend key and SMTP password are blank, readiness answers 200,
  and the image is the expected one. Anything else exits non-zero.
  **KILL verifies that dispatch is disabled; it does not verify that every WhatsApp
  credential is blank.** A live OFF posture keeps `WHATSAPP_TOKEN` / `WHATSAPP_PHONE_NUMBER_ID`
  set by design (the kill switch is `OUTBOUND_ENABLED=false` plus the dispatch gate). Only the
  sealed rehearsal runs with blank transport credentials, and its check is labelled as a
  rehearsal-only fact.
* **The watcher waits for KILL — for a bounded time (R1).** On a breach it runs KILL and
  waits at most `KILL_DEADLINE_SECONDS` (default `SENDER_CHECK_SECONDS + 240`):
  * exit 0 → "OFF restored and verified", exit 3;
  * KILL cannot start (126/127) or exits non-zero → `*** BLOCKER ***`,
    `evidence/INCIDENT_UNRECOVERED` (0600), the pidfile is **kept**, exit 4;
  * KILL still running at the deadline (e.g. a hung `docker compose`) → `*** BLOCKER ***`,
    `INCIDENT_UNRECOVERED` naming KILL's pid, which is **left running and not signalled**
    (it may be mid-recreate), the pidfile is kept, exit 4. OFF is never claimed.
* **Exact PIDs only.** No `pkill`, `pgrep -f`, `killall` or pattern match anywhere. A watcher
  is ours only if its exact pid is alive, its command line names this kit's `WATCH.sh` **and**
  this window's config, and it is not the caller or an ancestor. KILL never signals the
  watcher that invoked it. (`test_kit.sh` K6 checks commands only — comment lines and quoted
  strings, such as the rehearsal sentinel's deliberately misleading argv, are not commands.)
* **Counted precisely.** A Flow dispatch is `direction='out' AND message_type='flow'`; a
  BOOKING_FLOW *text* (the booking acknowledgement, the slot-recovery explanation) is not one.
  An operator notification counts only as a provider attempt (SENDING/SENT, any attempt, or a
  non-blocked OPERATOR_NOTIFICATION outbound); a HELD row with zero attempts is recorded,
  never a breach.
* **An unreadable instrument is a breach**, never a silent zero.
* **Rehearsal mode refuses live names** (`ridecheck-crm` project and its three containers).

## What `docker stop` does and does not stop (slot-recovery worker, R2 review R2R-01)

* KILL's `docker stop` sends SIGTERM; Docker gives the container approximately **10 s**
  (its default) before SIGKILL.
* **SIGTERM does not itself stop recovery activity.** The recovery worker's stop signal is
  set by the FastAPI/Uvicorn **shutdown handler**, and Uvicorn 0.30.6 invokes that handler
  only after it has closed its listeners and drained active connections and tasks. The
  container command sets no `--timeout-graceful-shutdown`, so that drain has no explicit
  Uvicorn timeout: Docker's SIGKILL is the only bound (and if SIGKILL comes first, the
  handler never runs).
* During the drain window the **old process keeps operating under its cached ON posture**
  (settings are read once per process): its worker can still claim jobs and begin recovery
  sends. Measured in review: with a six-second request in flight, the shutdown handler ran
  approximately **5.2 s** after SIGTERM. No bound shorter than Docker's 10 s stop window is
  claimed.
* Once the stop signal has crossed the worker's `_SEND_LOCK`, **no new recovery provider
  call can begin**. A provider request that had already started may still complete at Meta;
  its job is recorded as an uncertain HANDOFF and is never retried.
* **Emergency OFF is complete only when the old container has exited AND the replacement
  container is ready with the required flags verified OFF** — that is, when KILL exits 0.
  Before then, do not treat OFF as in effect.
* This is an operational limitation of the old process's drain window, **not a Smart
  Booking gate bypass**: the replacement process starts OFF and refuses every recovery send.

## Thresholds — `WINDOW_PROFILE` (R1, review F-07)

Counters are deltas over ENABLE's watermarks. A value set explicitly in the config always
wins; nothing is disabled.

| counter | `single` (default) | `race`: expected | `race`: max |
|---|---|---|---|
| Flow dispatches (`MAX_FLOW_DISPATCHES`) | 2 | 2 originals + 1 recovery Flow = **3** | **3** |
| booking offers (`MAX_NEW_OFFERS`) | 4 | 2 originals + 1 recovery offer = **3** | **3** |
| bookings / thread_revisions (`MAX_NEW_BOOKINGS`) | 2 | 1 winner (+1 if the loser rebooks) | **2** |
| booking_attempt_events (`MAX_NEW_ATTEMPTS`) | 6 | 1 FINALISE per distinct losing delivery = 1–2 | **4** |
| slot_recovery_jobs (`MAX_NEW_RECOVERY_JOBS`) | 1 | 1 per losing offer | **1** |
| operator-notice provider attempts (`MAX_OPERATOR_NOTIFICATIONS`) | 0 (1 if the notice flag is ON) | 1 per committed booking, only with `OPERATOR_WHATSAPP_NOTIFY_ENABLED` in `ON_TRUE_FLAGS` | **2** (else 0) |

The race shape was measured on this branch (two customers, one slot, the loser's delivery
retried once and re-tapped once, the worker run): +1 booking, +1 recovery offer, +1 recovery
Flow, 2 BOOKING_FLOW texts (acknowledgement + explanation), 2 FINALISE rows, 1 recovery job.
The recovery writes NO `booking_attempt_events` row (R1: its record is `slot_recovery_jobs`).
On a schema below `20261103_slot_recovery_job` the recovery-job counter reads `absent` (and the
table appearing mid-window is a breach). Use `WINDOW_PROFILE=race` for any window in which two
testers may race for one slot; the single profile breaches on the race's third Flow.

## Configuration (outside Git: 0600 file in a 0700 evidence directory)

```
EV_ROOT=/opt/ridecheck-builds/<window-dir>        # evidence root (created 0700)
BACKEND=<backend container>   PGC=<postgres container>   DB=<database>
COMPOSE_PROJECT=<project>     COMPOSE_DIR=<dir>         COMPOSE_FILES=<base.yml[:beta.yml]>
ON_OVERLAY=<on.yml>           OFF_OVERLAY=<off.yml>
EXPECT_IMAGE=<sha256:...>     EXPECT_HEAD=<alembic head>
TESTER_WA_IDS=<digits>,<digits>                   # the only permitted recipients
OFF_ALLOWLIST_COUNT=<n>       OFF_ALLOWLIST_SHA16=<sha256(sorted,comma-joined)[:16]>
ON_TRUE_FLAGS="OUTBOUND_ENABLED SMART_BOOKING_CE_ENABLED ..."
KIT_MODE=live|rehearsal
WINDOW_PROFILE=single|race
# optional: WINDOW_SECONDS POLL_SECONDS MAX_FLOW_DISPATCHES MAX_NEW_BOOKINGS MAX_NEW_OFFERS
#           MAX_NEW_ATTEMPTS MAX_NEW_RECOVERY_JOBS MAX_OPERATOR_NOTIFICATIONS
#           KILL_DEADLINE_SECONDS PREARM_GRACE_SECONDS READY_MODE=host|exec READY_URL
#           SENDER_CHECK_SECONDS DB_PASSWORD
```

`OFF_ALLOWLIST_SHA16` is a configuration fingerprint compared by equality, not anonymisation:
an unsalted digest of a phone list is brute-forceable. Keep the config 0600 and never paste it.

## Use

```
bash scripts/emergency_off/WATCH.sh  CONFIG &      # FIRST: pre-armed, its pid in evidence/watch/
bash scripts/emergency_off/ENABLE.sh CONFIG        # refuses without the pre-armed watcher
bash scripts/emergency_off/KILL.sh   CONFIG manual # at any time; idempotent
```

## Tests

* `bash scripts/emergency_off/test_kit.sh` — sealed, no containers, 28 checks: breach → KILL
  with every file 0600; KILL missing / failing → BLOCKER, incident, pidfile retained; the
  watcher outlives KILL; rehearsal guard; no pattern kill (commands only); exact-PID ownership.
* `bash scripts/emergency_off/test_kit_r1.sh` — sealed, no containers: KILL past its deadline
  (BLOCKER, pid retained, no OFF claim); the pre-armed watcher (ENABLE refuses without it; no
  ENABLE / an early refusal / stale watermarks → exit 6, no KILL); the race thresholds against
  the measured shape and one beyond it; unreadable counters; K6's filter both ways.
* `bash scripts/emergency_off/rehearse.sh SCHEMA_SQL HEAD IMAGE OUTDIR` — a real backend
  container on a disposable `--internal` network with a schema-only database and synthetic
  numbers: ENABLE refused without a watcher; watcher before ENABLE in every run; a real breach
  restored and verified independently; a failed restoration, a KILL that cannot start and a
  KILL that hangs each surfaced as an unrecovered BLOCKER and then restored by hand; the race
  profile absorbing the measured race and breaching one Flow beyond it; sentinels and the
  invoking shell alive; live containers unchanged.
