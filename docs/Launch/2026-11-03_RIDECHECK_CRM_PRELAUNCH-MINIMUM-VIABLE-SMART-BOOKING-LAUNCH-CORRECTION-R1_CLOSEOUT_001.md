PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION-R1

Date: 2026-11-03. Implementation only. Nothing staged, committed, pushed, built, deployed or
migrated live. No live flag, allowlist, Compose file, environment or live `crm_test` row was
changed. No Meta or other provider was contacted. Lead 169 and operator notification 2 were
read, never written. `/opt/ridecheck-crm-integration` was not modified (read-only fingerprints
before and after, §19). Smart Booking remains NO-GO.

================================================================================
0. VERDICT
================================================================================
READY FOR FRESH INDEPENDENT REVIEW. Smart Booking remains NO-GO; nothing is enabled, migrated
or deployed, and the deployment preconditions of the 2026-11-02 closeout §13 still apply
(P1 lead 169 · P2 the §9 procedure · P3 a compatibility image, now at EXPECTED_HEAD
20261103_slot_recovery_job).

  C-1 … C-7 implemented: a durable, UNIQUE slot-recovery JOB is admitted by the Data Exchange
  request together with the human flags and is executed by a dedicated background worker
  under leases and a total per-call transport deadline; every incomplete recovery is listed
  for an operator; the Flow details are persisted again at confirm and a failure is a typed,
  listed condition; seller_phone is booking-canonical; the CRM card escapes every
  customer-derived value; the notification-2 SQL is self-enforcing; the OFF kit has race
  thresholds, a KILL deadline and a watcher that runs before ENABLE.

  The nine readiness conditions of the prompt:
    1  no recovery claim can remain silent ............................ MET (§3, §5)
    2  recovery transport does not execute inside Data Exchange ......... MET (§4, §6)
    3  Data Exchange is not blocked by synchronous provider calls ....... MET (§6)
    4  detail-persistence failure is operator-visible ................... MET (§7)
    5  booking-required customer details remain canonical ............... MET (§7)
    6  CRM card renders all customer-derived values safely .............. MET (§8)
    7  emergency kit passes every test and rehearsal .................... MET — 28/28, 38/38, rehearsal 89/0 (§10)
    8  no unexplained candidate-only failure identity remains ........... MET — SQLite and PostgreSQL NEW 0 / GONE 0 (§14)
    9  live isolation is unchanged ...................................... MET — identical before/after (§19)

================================================================================
1. SOURCE, BRANCH AND PATH SET
================================================================================
  worktree      /opt/ridecheck-crm-minimum-launch
  branch        integration/prelaunch-minimum-launch
  HEAD          6f20587da9af0d49fbe0b20078cff555521c293b   (unchanged; nothing committed)
  staged 0 · stash 0 · origin/integration/prelaunch-minimum-launch absent (never pushed)
  origin/integration/prelaunch-booking-convergence  6f20587…  (unchanged)

  Path set = 41 paths: the 32 of the 2026-11-02 closeout, MINUS 2 restored to pristine,
  PLUS 9 new R1 code/test paths, PLUS the preserved independent-review audit, PLUS this
  closeout (32 − 2 + 9 + 1 + 1 = 41).

  Restored to pristine (no longer in the candidate) — the in-request recovery adaptations
  are obsolete because the Data Exchange request no longer supersedes the losing offer, so
  the PRISTINE assertions (offer stays ISSUED; a fresh IV is a new, counted attempt; nothing
  sent) hold again (§12, §14.2):
    tests/test_convergence_p3_race_pg.py
    tests/test_convergence_p4_r1_pg.py

  New R1 paths and why each is required:
    M backend/app/main.py                          starts the slot-recovery worker loop (C-2)
    M backend/app/routes/flow_data_exchange.py     sync dispatch off the event loop (C-2)
    M backend/app/routes/ops_dashboard.py          recoveries + conditions on booking-attention (C-1)
    M backend/app/services/booking_authority.py    seller_phone in the booking's own transaction (C-3)
    M backend/app/ui/control_view.py               /control lists them, with thread links (C-1)
    A backend/migrations/versions/20261103_slot_recovery_job.py   job + condition tables,
                                                   thread_revisions.seller_phone (C-1/C-2/C-3/C-7)
    M tests/test_sb2a_booking_authority_pg.py      pg17's inline cleanup also deletes the
                                                   Flow-detail evidence rows confirm now
                                                   writes (FK to the thread) (§14.2)
    A tests/test_minimum_launch_r1.py              the committed R1 SQLite proofs (§5–§12)
    A scripts/emergency_off/test_kit_r1.sh         the new sealed kit checks (§10)
    ? docs/Launch/2026-11-03_…-CORRECTION-R1_CLOSEOUT_001.md      this file
  Preserved, untracked, not edited:
    ? docs/Launch/2026-11-03_…-CORRECTION-INDEPENDENT-REVIEW_AUDIT_001.md

  Carried paths edited again in R1: models.py, booking_acknowledgement.py,
  booking_flow_details.py, booking_flow_service.py, booking_slot_recovery.py (rewritten),
  conversation_engine.py, lead_booking_details.py, lead_identity.py, phone_utils.py,
  schema_preflight.py, kanban_actions.py, kanban_view.py, 20261102_lead_phone_identity.py,
  scripts/emergency_off/{common,ENABLE,WATCH,README,rehearse,test_kit}, the four
  test_minimum_launch* suites, test_convergence_p4_r1.py, test_w1f_w01_r2_outstanding_question.py.
  Untouched: Meta Flow JSON, Compose / env files, docs/operations/RECOVERY_MANIFEST.md,
  /opt/ridecheck-crm-integration.

  Overlap with the deferred return-cutoff candidate (F-12, corrected): the 2026-11-02 tree
  shared EIGHT filenames with it, not seven — the closeout omitted
  tests/test_convergence_p3_race_pg.py. In THIS tree that file is pristine again and
  backend/app/services/booking_authority.py and tests/test_sb2a_booking_authority_pg.py
  join, so the count is NINE: models.py, booking_authority.py, conversation_engine.py,
  schema_preflight.py, kanban_actions.py, kanban_view.py, test_convergence_p4_r1.py,
  test_sb2a_booking_authority_pg.py, test_w1f_w01_r2_outstanding_question.py (computed from
  the deferred candidate's read-only all_dirty_paths.txt). Both candidates also add an Alembic
  revision on the same chain: expect textual conflicts there and in EXPECTED_HEAD.

================================================================================
2. FINDINGS F-01 … F-14 — DISPOSITIONS
================================================================================
  F-01 HIGH  silent claimed recovery        FIXED. Durable job admitted with the human flags
                                            in one transaction; every nonterminal job is
                                            listed; lease expiry resumes or hands off; INTERNAL
                                            and lock contention are listed and end FAILED
                                            after bounded retries (§3–§5).
  F-02 HIGH  provider time inside DX        FIXED. DX only admits; a dedicated worker thread
                                            sends under a TOTAL per-call deadline; the DX body
                                            runs on the threadpool (§4, §6).
  F-03 HIGH  silent detail loss             FIXED. Persisted again at confirm_booking before
                                            the authority; a failure is a typed, listed
                                            DETAILS_NOT_PERSISTED condition with the flags;
                                            seller_phone is booking-canonical; the card falls
                                            back to the committed thread_revision (§7).
  F-04 HIGH  stored HTML injection          FIXED. Every customer/operator value on the card
                                            (incl. the edit modal and the revision block) is
                                            escaped exactly once; only http(s) URLs link (§8).
  F-05 MED   attempt events as admission    FIXED. The recovery neither writes nor reads
                                            booking_attempt_events; admission and replay
                                            recognition use slot_recovery_jobs. The model's
                                            "never authority" contract is true again and is
                                            unchanged.
  F-06 MED   notification-2 SQL manual      FIXED. Self-enforcing procedure (§9). Not run.
  F-07 MED   kit thresholds below the race  FIXED. WINDOW_PROFILE=race with measured,
                                            narrow maxima; recovery-job counter (§10).
  F-08 MED   test_kit 27/28 (K6)            FIXED. K6 matches commands only; 28/28 (§10).
  F-09 LOW   unsalted phone digest          FIXED. Migration and logs carry lead ids only (§11).
  F-10 LOW   lost fresh-IV coverage         FIXED. SQLite m1 + the pristine PG p4r1_12 (§12).
  F-11 LOW   unique-index callers           FIXED. Typed 409s, deterministic CE assignment,
                                            digit-free = no identity (§11).
  F-12 LOW   seven vs eight filenames       FIXED in §1.
  F-13 LOW   kit hygiene                    FIXED. KILL deadline, pre-armed WATCH that ENABLE
                                            requires, accurate credential wording (§10).
  F-14 INFO  details not cycle-scoped       UNCHANGED, recorded as accepted debt (§16).

================================================================================
3. RECOVERY-JOB SCHEMA AND STATE MACHINE (A, B, C-7)
================================================================================
  Table slot_recovery_jobs (migration 20261103_slot_recovery_job, 26 characters):
    id bigint · recovery_identity varchar(64) UNIQUE (sha256 of the losing offer id: one job
    per losing offer) · thread_id, lead_id, contact_id, candidate_id, losing_offer_id,
    new_offer_id (scalar history, no FK — a tester reset is never blocked) ·
    origin_delivery_hash varchar(16) (the losing Data Exchange delivery) · state ·
    outcome varchar(48) (typed terminal outcome) · attempts 0..6, text_attempts 0..2,
    flow_attempts 0..1 (CHECKed) · claim_token, lease_expires_at, next_attempt_at ·
    last_error (typed) · text_/flow_delivery_identity varchar(64) ·
    text_/flow_provider_message_id · attention_applied, owns_state_flag, owns_lead_flag ·
    inbound_marker varchar(16) · created_at, updated_at, completed_at.
    CHECKs: state vocabulary; bounds; terminal ⇔ (outcome AND completed_at). No PII.

  Table booking_attention_conditions: condition_uid UNIQUE, kind CHECK
  ('DETAILS_NOT_PERSISTED'), reason, thread_id, lead_id, offer_id, flags_applied, created_at.
  Column thread_revisions.seller_phone varchar(40) NULL, no default.

    PENDING ──claim──▶ SENDING_TEXT ──WAMID──▶ TEXT_SENT ──K1: fresh offer + state, ONE tx──▶
      ▲  definite #1        │ ambiguous / deadline / definite #2 ──▶ HANDOFF
      └──────────────────────┘
    SENDING_FLOW ──WAMID──▶ RECOVERED
         │ definite (offer withdrawn) / ambiguous / deadline / superseded ──▶ HANDOFF
    any state: INTERNAL error or K1 contention ──▶ released, retried; claims exhausted ──▶ FAILED
    lease expired in SENDING_*: the gate's own row for that delivery identity decides —
      none → resume the same identity; `sent`+WAMID → record and continue;
      anything else → HANDOFF (*_UNCERTAIN_LOST_SENDER). Never re-sent.

  Admission (the Data Exchange request, after the typed SLOT_CONFLICT refusal is durable and
  the ledger re-read missed; no provider call): one isolated transaction — K1 (bounded, two
  tries) → INSERT … ON CONFLICT (recovery_identity) DO NOTHING RETURNING id → stage
  SCHEDULING, state.needs_human = true, lead.necesita_humano = true, recording which flags
  THIS job turned false → true, and the thread's inbound marker → commit. Concurrent
  admissions for one losing offer: exactly one job (PG pga_01, 3 offers × 6 racers). K1
  contended: the job is still inserted (listed at once, FLAGS_NOT_RAISED) and the worker
  raises the flags before anything is sent (PG pgr_01, SQLite w8a).

  Flag ownership:
    raised at admission; never cleared on HANDOFF / FAILED;
    cleared on RECOVERED only (a) the flag this job owns, (b) in the same K1 transaction,
    (c) when no turn ran since admission (inbound marker unchanged), (d) no other job or
    attention condition of the thread / lead is active, (e) for the lead, no other thread of
    that lead needs a person. Otherwise it is kept and last_error says why
    (FLAGS_KEPT_OTHER_ATTENTION / FLAGS_KEPT_CONTENDED). Proven f1–f6.

================================================================================
4. WORKER LIFECYCLE, LEASE AND TIMEOUT (C-2)
================================================================================
  slot_recovery_loop (startup task, shaped like operator_notification_loop): sleep 5 s, then
  one pass on a DEDICATED single-thread executor (never the event loop, never FastAPI's
  threadpool, never the executor the notification loop uses).
  claim_next: one conditional UPDATE — unclaimed and due, or lease expired; attempts + 1;
  lease 60 s. Every later write is conditional on the claim token (a lost claim stops at
  once, _LostClaim). One active claimant per job (PG pgr_02: two concurrent workers → one
  claimant, one text, one Flow).

  Bounded transport (bounded_call): the provider call runs on its own daemon thread; the
  worker waits at most TRANSPORT_DEADLINE_SECONDS = 8.0 s per call, whatever it is blocked on
  (DNS, connect, TLS, read). Past it the step is UNCERTAIN → HANDOFF
  (TEXT_/FLOW_TRANSPORT_DEADLINE, last_error TRANSPORT_DEADLINE_EXCEEDED).
  WHAT IS BOUNDED: the worker's wait and therefore the job, the pass and every other job.
  WHAT REMAINS ALIVE: the abandoned network call itself — Python cannot cancel a blocking
  socket operation; it lives on its daemon thread until urllib's own 15 s per-operation
  timeout (or DNS) ends it, and its result is discarded (that thread never touches the
  database). At most MAX_INFLIGHT_TRANSPORTS = 4 may be alive; with none free the worker
  claims nothing into a transport and the jobs stay listed (l4).
  Retry policy (unchanged and proven): text attempt 1; attempt 2 only after a DEFINITE
  non-delivery; never after ambiguity; a Flow only after a text WAMID; ≤ 1 Flow; the gate
  admits each delivery identity once, ever, which also covers a lease expiry.

================================================================================
5. CRASH-WINDOW CONTRACT (F) — committed tests, tests/test_minimum_launch_r1.py
================================================================================
  Process death is a BaseException raised at the boundary (no handler catches it); the
  resume is the production worker after the lease expires (now + 5 min).

  window                                     after death               resume → result
  1 before admission (w1)                    no job, refusal durable   Meta retry admits →
                                                                       RECOVERED (self-heals)
  2 admitted, before text (w2)               PENDING, flags up, listed RECOVERED; LEASE_EXPIRED
                                             (LEASE_EXPIRED shown)     listed meanwhile
  3a Meta accepted text, nothing recorded    SENDING_TEXT, gate row    HANDOFF TEXT_UNCERTAIN_
     (w3a)                                   pending                   LOST_SENDER; 0 re-sends
  3b gate recorded SENT, job not (w3b)       SENDING_TEXT              → TEXT_SENT → Flow →
                                                                       RECOVERED; 1 text total
  4 text recorded, before fresh offer (w4)   TEXT_SENT, offer ISSUED   Flow → RECOVERED
  5 offer committed, before Flow (w5)        SENDING_FLOW, no gate row same offer, same identity
                                                                       → RECOVERED; no 3rd offer
  6a Flow accepted+recorded, before          SENDING_FLOW, gate sent   RECOVERED, 0 re-sends
     completion (w6a)
  6b Meta accepted Flow, nothing recorded    SENDING_FLOW, pending     HANDOFF FLOW_UNCERTAIN_
     (w6b)                                                             LOST_SENDER; 0 re-sends
  7 unexpected Exception after admission     listed INTERNAL, retried  FAILED INTERNAL_ERROR,
     (w7)                                                              listed; nothing sent
  8a K1 contended at admission and after     listed FLAGS_NOT_RAISED,  FAILED HANDOFF_LOCK_
     (w8a)                                   LOCK_CONTENDED            CONTENDED, lead flag
                                                                       raised, listed
  8b K1 contended once at the fresh offer    TEXT_SENT, LOCK_CONTENDED RECOVERED
  replay after completion (w9)               —                         same screen, 0 sends
  For windows 2–8 every test asserts: no loser booking (Revision, ThreadRevision), exactly one
  job, the thread or lead flagged, the ops surface lists the thread with its link, the
  resumed job is terminal, at most one SENT row per delivery identity, and a degraded end is
  still listed (never silent). PG: pgr_01 (real K1 held), pgr_02 (two workers).

================================================================================
6. DATA EXCHANGE LATENCY PROOF (D, E)
================================================================================
  l1  slow provider fakes (text 1.5 s, Flow 1.5 s):
        DX_RESPONSE_SECONDS = 0.060     provider calls at the DX return: 0
        WORKER_SECONDS      = 3.086     (both slow sends happen there, after the response)
  l2  a provider call that never returns, deadline 0.5 s: the worker returned in 0.532 s,
      HANDOFF TEXT_TRANSPORT_DEADLINE, no retry, no Flow, flags up, listed; the orphan
      finished later and nothing was recorded from it.
  l3  one stuck job + one healthy job in one pass: the healthy one RECOVERED; the operator
      notification pass ran meanwhile.
  l5  booking_flow_data_exchange whose synchronous service call takes 1 s: a concurrent
      ticker on the SAME event loop kept running (≥ 10 ticks in that second). The route's
      dispatch chain — unchanged, line for line — is a nested sync function awaited through
      run_in_threadpool; the try/except handlers, the guard and the single raw-encryptor use
      stay in the route itself (the first candidate moved the whole body into a helper and
      the full SQLite run measured 5 NEW structural failures; corrected, §14.1).
  l6  source: the DX recovery hook contains no transport call and the route awaits
      `result = await run_in_threadpool(_dispatch)`.
  race() in the A suite asserts no provider call before the DX response in every recovery
  test; PG pga_02 asserts the same on a real concurrent race (only the winner's
  acknowledgement during the requests; the recovery comes from the worker).

================================================================================
7. DETAIL PERSISTENCE AT CONFIRMATION (G)
================================================================================
  prepare_summary persists as before. confirm_booking now persists the submitted values AGAIN
  through the same persist_flow_details (producer version confirm_booking/1), bound to the
  thread the token resolved, the admitted offer and the offer-bound candidate, BEFORE the
  authority. Replay writes nothing; a correction supersedes (g5, g6).
  K1 is only TRIED there (CONFIRM_LOCK_TIMEOUT_MS = 50): a contended confirmation must reach
  the authority and its typed retry screen exactly as before, writing nothing (the
  pre-existing Phase 3 contract p3r_10b — the first R1 freeze waited 1.5 s + 1.5 s, the
  6-second K1 hold of that test expired, the booking committed, and the full PG comparison
  measured it NEW; corrected). A write that could not happen is SETTLED once the outcome is
  known:
    COMMITTED      retried after the commit with the normal bounded wait (g8); failing again
                   → the typed condition (g2)
    SLOT_CONFLICT  written inside the recovery admission transaction, which holds K1, in a
                   savepoint (a failure never costs the admission) (g7); failing → condition
    anything else  (contention, offer refusals) nothing is written; the customer's retry
                   or Meta's carries the same values

  Booking may still complete when the draft write fails — decided and proven (g2): the
  booking's OWN transaction writes the canonical copy in thread_revisions (buyer name,
  e-mail, address, seller name, seller_phone — new — and listing URL), so a completed
  booking never depends on the draft rows. The failure is never silent:
  record_details_condition writes one typed DETAILS_NOT_PERSISTED row per (thread, delivery)
  with reason CONFIRM_<cause>, raises lead.necesita_humano and (K1 permitting) the thread
  flag, and it is listed on booking-attention (attention_conditions) with the thread link and
  no PII. The CRM card falls back to the committed thread_revision for every field the
  evidence lacks.
  A losing customer's details remain recoverable: persisted at prepare_summary AND at
  confirm_booking; if both fail the thread is flagged DETAILS_NOT_PERSISTED so a person
  collects them, and the recovery never clears those flags (g3).

  tests: g1 prepare DB failure → confirm keeps them · g2 confirm DB failure → booking,
  canonical seller phone, condition, flags, card fallback · g3 every write failing (prepare,
  confirm, admission) → condition, flags, recovery never clears them · g4 winner + loser +
  seller phone with no prepare at all · g5 retry idempotent, one condition per delivery ·
  g6 corrected submission · g7 confirm met K1, the admission kept the loser's details · g8
  winner's confirm write failed, retried after the commit · PG pgr_03 real K1 held at
  prepare · PG p3r_10b (pristine) a 6 s K1 hold: retry screen, nothing written.

================================================================================
8. HTML ESCAPING PROOF (H)
================================================================================
  render_lead_card: every interpolated value a customer, the Flow, the CE or an operator form
  can supply is escaped exactly once at its sink — name/surname (title and the Flow-name
  fallback), telephone, e-mail, attribution fields, flag label, vehicle badge and pill,
  professional label, revision summary lines, motivo de pérdida, estado; the edit modal's
  value="…" attributes (quote-safe). render_revisions_block: every _txt sink (brand, model,
  address, zone, notes, result, …). render_edit_latest_revision_form (rendered inside the
  card): every value="…" and the notes <textarea>. _url_link: the href is attribute-escaped
  and only http(s) becomes a link; anything else (javascript:) is shown as escaped text.
  Structural HTML generated by the renderer is not escaped (e3).
  e1 puts `<img src=x onerror=alert(1)>"'<script>alert(2)</script>` into nombre, apellido,
  telefono, email, revision marca/modelo/dirección/zona/notas, a javascript: link and all
  nine Flow detail fields: no `<img`, `<script>alert`, `onerror=alert(1)>` or
  `href="javascript:` in the card; the escaped form is present for every field; no
  double-escaping (`&amp;lt;` absent). The first e1 run FOUND the edit-form sinks the
  review had not listed (value="M<img …"): fixed and re-run.

================================================================================
9. OPERATOR-NOTIFICATION PROCEDURE (I) — replaces the 2026-11-02 §7 procedure. NOT RUN.
================================================================================
  Run once, in the deployment window, BEFORE OPERATOR_WHATSAPP_NOTIFY_ENABLED is ever set,
  with `psql -X -v ON_ERROR_STOP=1 … -f <file>`. Any mismatch raises; ON_ERROR_STOP aborts;
  the open transaction is rolled back; nothing changes. Measured read-only today:
  notification 2 = HELD · attempts 0 · sent_at NULL · claim_token NULL · booking_command_id 9
  · hist_revision_id 82; revision 82 = CANCELADO · agenda_occupying false.

    \set ON_ERROR_STOP on
    BEGIN;
    SET LOCAL lock_timeout = '5s';
    DO $$
    DECLARE n int;
    BEGIN
      PERFORM 1 FROM revisions WHERE id = 82 FOR SHARE;
      PERFORM 1 FROM operator_notifications WHERE id = 2 FOR UPDATE;
      IF NOT EXISTS (SELECT 1 FROM operator_notifications
                     WHERE id = 2 AND status = 'HELD' AND attempts = 0 AND sent_at IS NULL
                       AND booking_command_id = 9 AND hist_revision_id = 82
                       AND claim_token IS NULL) THEN
        RAISE EXCEPTION 'notification 2 is not in the expected state — nothing changed';
      END IF;
      IF NOT EXISTS (SELECT 1 FROM revisions
                     WHERE id = 82 AND estado_revision = 'CANCELADO'
                       AND agenda_occupying IS FALSE) THEN
        RAISE EXCEPTION 'revision 82 is not cancelled/non-occupying — nothing changed';
      END IF;
      UPDATE operator_notifications
         SET status = 'FAILED', last_error = 'SUPERSEDED_BOOKING_CANCELLED',
             next_attempt_at = NULL, claim_token = NULL, lease_expires_at = NULL,
             updated_at = now()
       WHERE id = 2 AND status = 'HELD' AND attempts = 0;
      GET DIAGNOSTICS n = ROW_COUNT;
      IF n <> 1 THEN RAISE EXCEPTION 'expected 1 row, got %', n; END IF;
    END $$;
    COMMIT;

  The row is preserved as evidence (no DELETE); FAILED is terminal and non-sendable
  (claim_next selects PENDING only; nothing moves FAILED or HELD back to PENDING) and the
  send-time BOOKING_NOT_OCCUPYING guard would refuse it anyway. It then shows once on
  booking-attention as OPERATOR_NOTIFICATION_REQUIRED (expected).

================================================================================
10. EMERGENCY-OFF RESULTS (J)
================================================================================
  Race-window thresholds (F-07) — WINDOW_PROFILE=race, measured on this branch (two
  customers, one slot, the loser's delivery retried once and re-tapped once, worker run):
    Flow dispatches  2 originals + 1 recovery = 3 → max 3   (single profile: 2)
    booking offers   2 originals + 1 recovery = 3 → max 3   (single: 4)
    bookings         1 (+1 if the loser rebooks) → max 2    (single: 2)
    attempt events   2 FINALISE rows (one per distinct losing delivery) → max 4 (single: 6)
    recovery jobs    1 → max 1                               (single: 1)
    operator notices 1 per committed booking, only with the notice flag ON → max 2, else 0
  Nothing is disabled; explicit config values win; the recovery writes no attempt-event row.

  test_kit.sh (K6 corrected: commands only — comment lines and quoted strings removed before
  matching, so the rehearsal sentinel's deliberately misleading argv is data; a bare
  `pkill …` is still a hit, proven both ways in test_kit_r1 R5):
      28 passed, 0 failed   (final kit files; K6 = 'zero hits')
  test_kit_r1.sh: 38 passed, 0 failed
    R1 KILL past KILL_DEADLINE_SECONDS → exit 4 within the bound, BLOCKER naming the
       deadline, INCIDENT_UNRECOVERED naming KILL's pid, that pid alive (not signalled),
       pidfile retained, "OFF restored" never written
    R2 ENABLE refuses without a pre-armed watcher (no watermarks written); a watcher whose
       ENABLE never starts / refuses before any change / finds stale watermarks exits 6 and
       runs no KILL
    R3 race thresholds: the measured race breaches nothing; one more of each counter breaches
    R4 unreadable counter = breach · R5 K6's filter on synthetic files both ways
  Overall KILL deadline: KILL_DEADLINE_SECONDS (default SENDER_CHECK_SECONDS + 240). On it:
  INCIDENT_UNRECOVERED, KILL's pid retained, loud BLOCKER, exit 4, no OFF claim.
  Enable/watch gap: WATCH starts first (pre-armed, bounded PREARM_GRACE_SECONDS); ENABLE
  refuses without it; the counters go live when ENABLE writes its watermarks (before the
  recreate); the explicit ARMED transition is the flags reading exactly ON_TRUE_FLAGS.

  Sealed rehearsal (rehearse.sh; --internal network, disposable PostgreSQL 16 on tmpfs from
  the schema-only dump, the LIVE image ridecheck-crm-backend:wild1-r3align-b31020ac
  sha256:19b3cec2… unmodified, blank rehearsal transport credentials, synthetic numbers):
      /opt/ridecheck-builds/emergency-off-rehearsal-r1-20261103T142919Z — 89 passed, 0 failed
    RUN 0  ENABLE without a watcher → refused (2), names the missing watcher, backend id
           unchanged (not recreated)
    RUN 1  WATCH pre-armed BEFORE ENABLE (timeline order asserted); ENABLE armed; 3
           BOOKING_FLOW texts + a HELD notice → no breach (HELD noted as withheld); 3 Flow
           dispatches → BREACH "Flow dispatches = 3 (max 2)" → the watcher ran KILL, waited,
           exit 3; OFF verified from inside the container independently of KILL's log (seven
           false, original allowlist count + digest, Resend/SMTP blank, dispatch disallowed,
           readiness 200); sentinel whose argv carries "WATCH.sh … KILL.sh pkill -f killall"
           ALIVE, unrelated sentinel ALIVE, rehearsal shell ALIVE
    RUN 2  OFF overlay unreadable → KILL non-zero → exit 4, BLOCKER, INCIDENT_UNRECOVERED,
           pidfile retained; manual KILL restored and verified OFF
    RUN 3  WINDOW_PROFILE=race: thresholds announced; 2 original Flows + 2 texts + 1 recovery
           Flow + 2 FINALISE rows → NO breach; a 4th Flow → BREACH "Flow dispatches = 4
           (max 3)" → KILL → exit 3 → OFF verified
    RUN 4  kit copy without KILL.sh → exit 4, incident "KILL exit status: 127", BLOCKER;
           manual KILL restored and verified OFF
    RUN 5  kit copy whose KILL.sh hangs, KILL_DEADLINE_SECONDS=5 → exit 4, incident "still
           running after the 5s deadline" naming KILL's pid, that pid ALIVE (not signalled),
           pidfile retained, OFF never claimed; the stand-in killed by exact pid; manual KILL
           restored and verified OFF
    end    sentinels alive; rehearsal shell alive; live containers identical (id, image,
           restarts, start time) before/after; no rce-* object left.
  One earlier attempt (…-r1-20261103T142518Z) is kept as evidence of a HARNESS defect, not a
  kit defect: rehearse.sh started the pre-armed watcher inside `$(…)` without detaching its
  stdout, so the substitution blocked until the watcher's grace ran out. Stopped by exact pid
  after RUN 0 passed (its EXIT trap removed the disposable stack); fixed (prearm detaches);
  the run above is the full re-run.
  Wording (F-13): the check "WhatsApp transport credentials blank" proves only the
  rehearsal's own base env and is now labelled so. A live OFF keeps WHATSAPP_TOKEN set by
  design; KILL verifies that dispatch is disabled, not that every live credential is blank.

================================================================================
11. LEAD-IDENTITY CALLERS (K) AND THE PRIVACY HASH (L)
================================================================================
  One rule everywhere — index predicate, ORM declaration, ON CONFLICT arbiter and lookup:
      identity  regexp_replace(telefono, '[^0-9]', '', 'g')
      predicate regexp_replace(telefono, '[^0-9]', '', 'g') <> ''
  Digit-free values ("-", "s/n", "(  )") have NO identity and behave like NULL for uniqueness
  (chosen rule: no canonical identity; nothing is invented). 20261102_lead_phone_identity was
  never deployed, so its predicate and CANONICAL_INDEXDEF were changed in place and
  re-measured on PostgreSQL 16; the PRE-R1 definition is now refused as malformed (pgi_02).
    ui_lead_update → a phone held by another lead: HTTP 409 LEAD_PHONE_IDENTITY_CONFLICT
      naming that lead, nothing changed (also on a racing IntegrityError at commit) (k1, pgr_04)
    ui_lead_create → existing identity: HTTP 409 naming the existing lead, echoing what the
      operator typed (escaped), nothing created (k2, pgr_04)
    CE website-form path → assign_phone_if_free: PostgreSQL conditional UPDATE in its own
      short transaction (lock_timeout 1.5 s) — ASSIGNED / HELD_BY_OTHER / CONTENDED /
      NO_IDENTITY; never an IntegrityError in the turn (k4, k5; PG pgr_04: two concurrent
      assignments → exactly one ASSIGNED)
    POST /leads → unchanged (idempotent ON CONFLICT, existing lead returned)
    PG pgd_02: '-', 's/n', '(  )' repeat legally beside NULL and ''.
  Privacy (F-09): the migration's refusal reports lead ids and the group count only
  ("group 1: lead_ids=[…]"); LEAD_CREATED / LEAD_IDENTITY_REUSED log lead ids only; no
  phone-derived digest is produced anywhere in lead_identity or the migration (k7, pgi_03
  asserts neither the digits nor sha256(digits)[:16] appear). Identity and uniqueness are
  unchanged by this. The live-state instrument of this closeout also prints no unsalted
  phone digest: the allowlist and lead 169 are compared through an HMAC under a per-session
  random key (§19).

================================================================================
12. RESTORED TEST COVERAGE (M)
================================================================================
  SQLite m1: delivery A loses (offer ISSUED, refused_count 1, job admitted) → a NEW delivery
  B (fresh IV) on the still-ISSUED offer is a new attempt: refused_count 2; B's Meta retry
  adds nothing (still 2); one job; no message. After the worker supersedes the offer, a fresh
  delivery C fails closed (BookingTokenError; no count, job or message), and A's replay still
  gets the same refusal screen. PG: tests/test_convergence_p4_r1_pg.py is pristine again, so
  p4r1_12's ORIGINAL "§3.2 case 5: a fresh IV is a new attempt, counted once" runs
  unchanged (§14).

================================================================================
13. MIGRATION REHEARSALS (O)
================================================================================
  20261103_slot_recovery_job (26 chars) ← 20261102_lead_phone_identity ← 20261027_….
  schema_preflight.EXPECTED_HEAD = 20261103_slot_recovery_job (the image refuses to start
  below it: migration before image deployment, enforced).
  Measured on PostgreSQL 16 (disposable, --internal):
    upgrade from the live schema-only dump                 CREATED (both revisions)
    create_all + stamp 20261027 + upgrade                  20261102 VERIFIED, 20261103
                                                           ADOPTED_EMPTY_CANONICAL
    pg_dump → restore (re-deparsed CHECKs) + upgrade       ADOPTED_EMPTY_CANONICAL
  test_minimum_launch_pg PG_R1_Migration:
    pgm_01 at head, exactly canonical · repeat upgrade no-op · downgrade REFUSED with a
           nonterminal job ("nonterminal recovery jobs=1"), with a terminal job, with an
           attention condition, with a booking seller phone — rows kept, version unchanged ·
           clean downgrade DROPPED, every other row preserved · re-upgrade CREATED ·
           rewound version → ADOPTED_EMPTY_CANONICAL, and with rows VERIFIED_CANONICAL
    pgm_02 malformed adoption REFUSED (same-named table of another shape; seller_phone of
           another type); a partial set holding data REFUSED ("provenance is unknown")
  Downgrade drops exactly what is PRESENT of this revision's objects (each canonical and
  empty) — the project's `create_all` + `stamp head` bootstrap on an older database creates
  the two new tables but cannot add the column to the existing thread_revisions, and that
  empty partial shape must downgrade (strict-schema ssa_08; the first R1 freeze refused it
  and the full PG comparison measured it NEW; corrected). Data in any of them still refuses.
  PG_I (20261102) re-run at the new head: full rehearsal, malformed adoption (×3 incl. the
  pre-R1 definition), duplicate groups refused and never resolved, foreign index refused,
  create_all builds the identical index. Nothing migrated live (live head unchanged, §19).

================================================================================
14. TEST RESULTS — SQLite / PostgreSQL, pristine vs final candidate
================================================================================
  Harness: ridecheck-crm-test:l4.7b2, --network none (SQLite) / --internal (PostgreSQL 16
  71e27bf60b70, tmpfs), TZ=America/Argentina/Buenos_Aires, -p no:cacheprovider, trees
  mounted read-only. Frozen trees:
    pristine   git archive 6f20587        tree digest f420b2eb6e93…3ad1 (= 2026-11-02 and
                                          the review's pristine, byte-identical)
    candidate  worktree snapshot (final, everything except this closeout)
                                          tree digest 49204628dc14…2964

  Milestone suites:
    tests/test_minimum_launch.py       41 passed (SQLite)
    tests/test_minimum_launch_e2e.py    1 passed (SQLite)
    tests/test_minimum_launch_r1.py    48 passed (SQLite) — W 12, L 7, F 6, O 3, G 8, E 4, K 7, M 1
    tests/test_minimum_launch_pg.py    17 passed + 12 subtests = 29 junit cases, 0 failed (PostgreSQL 16)
  Mutation / fault injection (each mutant on a scratch copy; R1 + minimum-launch suites):
    mutant                                   verdict  killed by
    M1  admission does not raise the flags    KILLED   f1 f4 o1 w1 w2 w8b
    M2  provider calls back inside DX         KILLED   38 tests (a01–a13, c01, f*, g3, g5,
                                                       g7, l1–l6, m1, o*, w*)
    M3  lost sender's pending text re-sent    KILLED   w3a
    M4  transport no longer deadline-bounded  KILLED   l2 l3
    M5  RECOVERED clears despite another      KILLED   f4 f5 g3
        active condition / a newer turn
    M6  confirm_booking stops persisting      KILLED   g1 g2 g3 g4 g5 g6 g7 g8
    M7  lead name unescaped                   KILLED   e1 e2
    M8  edit-modal value= unescaped           KILLED   e1
    M9  ops surface omits nonterminal jobs    KILLED   o1 o2 w1 w2 w3a w3b w4 w5 w6a w7 w8a …
    M10 replay recognised for any delivery    KILLED   m1
    M11 identity predicate = raw column       KILLED   d05 k6
    M12 CE website form assigns blindly       KILLED   k5
    M13 admission drops the pending details   KILLED   g7
    13 of 13 killed, on the FINAL snapshot (49204628…2964). Fault injection proper is §5 (process death at every boundary) and the
    real K1 holds of PG pgr_01 / pgr_03.

  14.1 SQLite — full suite, both frozen trees
    PRISTINE        62 failed, 7136 passed, 658 skipped, 9 errors, 7100 subtests (6:02)
    CANDIDATE       62 failed, 7226 passed, 675 skipped, 9 errors, 7129 subtests (7:11)
    (+90 passed = the 42 minimum-launch SQLite tests + the 48 R1 tests; +17 skipped = the
     11 + 6 PG-gated tests of test_minimum_launch_pg)
    failing identities  pristine 71 · candidate 71 · NEW 0 · GONE 0 · COMMON 71, all 71
    with identical (normalised) messages — the same 71 as the 2026-11-02 baseline.

    Measured and corrected on the way: the FIRST candidate freeze (tree bb6c5e30…c397) gave
    67 failed — 5 NEW, all structural tests that read booking_flow_data_exchange's own
    source (test_wild1_booking_flow_gate_and_contract http_01/02/03,
    test_wild1_forward_only_recovery_contract r3_c03, test_wild1_r3_aligned_flow_contract
    r2_r05: the HTTPException-before-Exception order, the single raw-encryptor use, the
    422 literal). That freeze had moved the whole route body into a helper. Corrected in
    production, not in the tests: the route keeps its body and handlers and only its
    synchronous dispatch is awaited through run_in_threadpool. That second freeze
    (e004dd9e…fd5a) gave NEW 0 / GONE 0 (62 F, 7224 P); the PG comparison then drove the two
    production corrections of §14.2, and the FINAL freeze (49204628…2964) is the run above.

  14.2 PostgreSQL — every PG-gated file, both trees, fresh template clones per file
    All PG-gated files (the 26 of 2026-11-02 + test_minimum_launch_pg), one file per
    process, fresh clones of R2-§5 templates built from the live schema-only dump (pristine
    head 20261027_w1f_conversation_memory; candidate head 20261103_slot_recovery_job).

    FILE                                          PRISTINE           CANDIDATE          =
    test_convergence_p2_authority_pg              3 F / 37           3 F / 37           =
    test_convergence_p3_r1_ack_pg                 0 F / 15           0 F / 15           =
    test_convergence_p3_race_pg                   0 F / 20           0 F / 20 (pristine file)
    test_convergence_p4_pg                        1 F / 16           1 F / 16           =
    test_convergence_p4_r1_pg                     9 F / 29           9 F / 29 (pristine file)
    test_convergence_p4_strict_schema_corr._pg    23 skipped         23 skipped         =
    test_convergence_p4_strict_schema_pg          23 F / 121         23 F / 121         =
    test_l4_7w5_g37r_rv03_tester_reset_pg         1 F / 27           1 F / 27           =
    test_l4_7w5_g3_7f_clarification_lifecycle     0 F / 133          0 F / 133          =
    test_l4_7w5_g3_7p_offer_authority_pg          0 F / 215          0 F / 215          =
    test_l4_7w5_g3_7r_booking_projection_pg       0 F / 15           0 F / 15           =
    test_l4_7w5_g3_7r_r2_refusal_bounds_pg        0 F / 12           0 F / 12           =
    test_l4_7w5_g3_7r_r3_cycle_reset_bound_pg     0 F / 21           0 F / 21           =
    test_prewild_pw1_bounded_runtime_pg           0 F / 41           0 F / 41           =
    test_sb1_booking_representation_pg            0 F / 31           0 F / 31           =
    test_sb2a_booking_authority_pg                0 F / 25           0 F / 25 (adapted, §15)
    test_sb2a_r2_hardening_pg                     0 F / 18           0 F / 18           =
    test_sb2a_r3_typed_plans_pg                   0 F / 12           0 F / 12           =
    test_sb2a_r4_window_scope_pg                  0 F / 14           0 F / 14           =
    test_sb2a_r5_lockwait_pg                      0 F / 5            0 F / 5            =
    test_sb2a_r7_temporal_pg                      0 F / 8            0 F / 8            =
    test_sb2b_option_b_pg                         0 F / 48           0 F / 48           =
    test_w1f_o1_ce_sla_successor_pg               0 F / 22           0 F / 22           =
    test_w1f_operator_notification_pg             1 F, 4 E / 5       1 F, 4 E / 5       =
    test_w1f_unanswered_alert_pg                  0 F / 4            0 F / 4            =
    test_w1f_w01_r2_outstanding_question          0 F / 346          0 F / 346 (adapted)
    test_minimum_launch_pg (new)                  —                  0 F / 29
    (junit cases incl. subtests; totals pristine 778 / candidate 795)

    failing identities  pristine 30 · candidate 30 · NEW 0 · GONE 0 · COMMON 30
    21 common messages differ, all explained:
      17 only in the head name the inherited stale literal is compared against
         (['20261027_w1f_conversation_memory'] vs ['20261103_slot_recovery_job'],
          expected ['20261022_operator_notifications']);
       4 (strict-schema ssa_02, ssa_05, ssa_41, ssa_53) only by the two new revisions'
         upgrade lines ("20261102_lead_phone_identity: …", "20261103_slot_recovery_job: …")
         appended to the same failing assertion's captured migration output (ssa_53 prints a
         tail window, which therefore shifts).
    These are R2's inherited "stale pinned head literal" / "harness provisioning" identities,
    failing identically on both sides — the same 30 as 2026-11-02 and the review.

    Measured and corrected on the way (first full PG comparison, candidate freeze
    e004dd9e…fd5a — 4 NEW):
      * p3_race p3r_10b — the pre-authority details write waited on K1 long enough for the
        test's 6 s K1 hold to expire and the booking to commit: production fix (§7, short
        K1 try, settle after the outcome);
      * strict-schema ssa_08 — the recovery-job downgrade refused the empty partial shape a
        create_all + stamp bootstrap produces: production fix (§13);
      * sb2a pg17 / pg18 — pg17's inline cleanup did not know the confirm-time evidence rows:
        test adaptation (§15); pg18 only inherited pg17's leftovers.
    Re-frozen (49204628…2964) and re-run on every file: the figures above.

================================================================================
15. HOW THE ADAPTED TESTS CHANGED (no skip / xfail added anywhere)
================================================================================
  test_minimum_launch.py  race() now runs the production worker after the DX request and
                          asserts no provider call inside it; a01/a03/a05/a06/a11 assert the
                          job (OLD: attempt-event rows) — every no-booking, one-explanation,
                          ≤2-text, Flow-after-WAMID and no-identity-crossing assertion is
                          unchanged; a13 also asserts nothing was sent in the request;
                          b04/b05 confirm what each customer reviewed (confirm now persists
                          and a differing value supersedes).
  test_minimum_launch_pg  pga_01 → concurrent ADMISSIONS (one job, flags raised, no attempt
                          rows); pga_02 runs the worker; PG_I at HEAD 20261103; pgi_02 adds the
                          pre-R1 definition as malformed; pgi_03 asserts lead ids only.
  test_convergence_p4_r1  head pin 20261103 → 20261102 → 20261027 (chain below unchanged).
  test_sb2a_booking_authority_pg pg17  its inline cleanup deletes the thread's
                          whatsapp_thread_evidence rows before the threads (confirm now
                          persists the Flow details there); every assertion unchanged. pg18
                          failed only on pg17's leftovers and needs no change.
  test_w1f_w01_r2 j12     head pin, same idiom.
  p3_race_pg, p4_r1_pg    restored to PRISTINE (see §1).

================================================================================
16. ACCEPTED LAUNCH DEBT
================================================================================
  * F3a: the loser still sees Meta's generic error first; the explanation now arrives from
    the worker (≤ CHECK_INTERVAL 5 s + the sends), or a person is flagged at admission.
  * Window 1 (death before admission) is not durable until Meta retries the delivery; the
    FINALISE refusal row exists, the customer saw Meta's error. A retry self-heals (w1).
  * An abandoned provider call is not cancelled (§4); a late success after a deadline
    HANDOFF means the customer may receive the text although the job says uncertain — a
    person is already flagged; nothing is ever re-sent automatically.
  * A Meta retry of a SECOND losing delivery (fresh IV while the offer was still ISSUED),
    arriving after the recovery superseded the offer, fails closed ("session expired")
    instead of repeating its refusal: replay recognition is the job's origin delivery only.
  * Flag clearing is conservative: any turn processed after admission, any other active
    job/condition, or K1 contention keeps the flags up after RECOVERED (a false positive
    for a person, never a lost one).
  * Attention conditions and degraded jobs are listed for 7 days (the dashboard's lookback);
    resolution is by a person on the thread / lead (no resolve action was added).
  * A CONTENDED confirmation (typed retry screen) writes no detail rows and records no
    condition — the pre-existing "contended attempt writes nothing" contract wins; the
    customer's or Meta's retry carries the same values, and prepare_summary's rows may
    already hold them. A customer who abandons after that screen is not flagged.
  * F-14: Flow details are per thread, not per conversation cycle — UNCHANGED.
  * Carried from 2026-11-02 §14: return-to-origin cutoff, single-resource availability,
    payment copy authority, NP-06/NP-07, the CE nfm_reply SLOT_CONFLICT handoff, the flood
    gate holding a recovery text (definite → retry/handoff), Instagram, multi-professional,
    three clean Wilds. Deployment preconditions P1 (lead 169), P2 (§9 procedure), P3
    (compatibility image) unchanged — the compatibility image now needs EXPECTED_HEAD
    20261103_slot_recovery_job.

================================================================================
17. PROPOSED COMMIT SUBJECT
================================================================================
  fix(launch-r1): durable slot-recovery job off the Data Exchange path, details kept at confirm, safe CRM card, kit deadlines

================================================================================
18. EXACT-PATH git add (NOT EXECUTED)
================================================================================
  git -C /opt/ridecheck-crm-minimum-launch add -- \
      backend/app/api/leads.py \
      backend/app/main.py \
      backend/app/models.py \
      backend/app/routes/flow_data_exchange.py \
      backend/app/routes/ops_dashboard.py \
      backend/app/services/booking_acknowledgement.py \
      backend/app/services/booking_authority.py \
      backend/app/services/booking_flow_details.py \
      backend/app/services/booking_flow_service.py \
      backend/app/services/booking_slot_recovery.py \
      backend/app/services/conversation_engine.py \
      backend/app/services/lead_booking_details.py \
      backend/app/services/lead_identity.py \
      backend/app/services/operator_notification.py \
      backend/app/services/phone_utils.py \
      backend/app/services/schema_preflight.py \
      backend/app/ui/control_view.py \
      backend/app/ui/kanban.py \
      backend/app/ui/kanban_actions.py \
      backend/app/ui/kanban_view.py \
      backend/migrations/versions/20261102_lead_phone_identity.py \
      backend/migrations/versions/20261103_slot_recovery_job.py \
      docs/Launch/2026-11-02_RIDECHECK_CRM_PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION_CLOSEOUT_001.md \
      docs/Launch/2026-11-03_RIDECHECK_CRM_PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION-INDEPENDENT-REVIEW_AUDIT_001.md \
      docs/Launch/2026-11-03_RIDECHECK_CRM_PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION-R1_CLOSEOUT_001.md \
      scripts/emergency_off/ENABLE.sh \
      scripts/emergency_off/KILL.sh \
      scripts/emergency_off/README.md \
      scripts/emergency_off/WATCH.sh \
      scripts/emergency_off/common.sh \
      scripts/emergency_off/rehearse.sh \
      scripts/emergency_off/test_kit.sh \
      scripts/emergency_off/test_kit_r1.sh \
      scripts/emergency_off/watcher_pid.sh \
      tests/test_convergence_p4_r1.py \
      tests/test_minimum_launch.py \
      tests/test_minimum_launch_e2e.py \
      tests/test_minimum_launch_pg.py \
      tests/test_minimum_launch_r1.py \
      tests/test_sb2a_booking_authority_pg.py \
      tests/test_w1f_w01_r2_outstanding_question.py

================================================================================
19. LIVE ISOLATION
================================================================================
  Nothing written to live. Reads only: docker inspect; docker exec reading environment
  variable NAMES / booleans / counts (an HMAC under a per-session random key, never a digit
  or an unsalted digest); read-only psql (default_transaction_read_only=on); /login
  readiness from inside the container; git ls-remote; read-only git on the deferred
  worktree (GIT_OPTIONAL_LOCKS=0). Every container created was disposable (r1m-*, r1h-*,
  rce-*), on --none or --internal networks, and removed.
  Session note: the first live read of this session was taken at 14:26:11Z, after the code
  work had begun (nothing in it writes live); it equals the independent review's 04:03:11Z
  read in every common field.
  Read 2026-11-03T14:26:11Z and 16:14:56Z — IDENTICAL in every field (diff empty):
    container /ridecheck-crm-backend-1 id=122d61f95493 image=sha256:19b3cec208b4d97d0618eec6035c7a9fb3cfd1dc9ebb2000c52e09e425eb44f8 restarts=0 started=2026-11-01T23:34:11.834969498Z
    container /ridecheck-crm-postgres-1 id=5246fd86d829 image=sha256:71e27bf60b70bded003791b5573f8b808365613f341df20ffcf0c1ed7bc13ddf restarts=0 started=2026-10-21T23:31:05.793593905Z
    container /ridecheck-crm-n8n-1 id=6ea80570b67b image=sha256:2d70fafea7c0c397f40df30daa31243c0bff590e9e87e0430398e7cb4d3d8d0b restarts=0 started=2026-10-21T23:31:12.210634184Z
    flag OUTBOUND_ENABLED=false
    flag SMART_BOOKING_CE_ENABLED=false
    flag OPERATOR_WHATSAPP_NOTIFY_ENABLED=false
    flag CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED=false
    flag EVIDENCE_ADMISSION_ENABLED=false
    flag OUTSTANDING_QUESTION_ENABLED=false
    flag LOCALITY_RESOLUTION_SAFETY_ENABLED=false
    allowlist_count=1
    operator_to_set=False
    allowlist_hmac16=cb953429eb1d3bd5
    readiness=200
    alembic=20261027_w1f_conversation_memory
    max_message_id=6268
    pending_outbound=0
    max_security_event_id=735
    leads=55/171
    whatsapp_contacts=36
    whatsapp_threads=36
    thread_revisions=1
    revisions=61
    whatsapp_booking_offers=2
    booking_attempt_events=7
    booking_commands=10
    whatsapp_thread_evidence=20
    users=0
    operator_notifications=1:SENT:1: 2:HELD:0:NOT_CONFIGURED_DISABLED
    notification_2=HELD:0:NULL:NULL:9:82
    revision_82=CANCELADO:false
    lead_169_row_hmac16=5c2b3e26394fc7e0
    dup_phone_groups=1
    recovery_table_present=f
    integration_worktree_fingerprint=a1985b38e079d797
    integration_head=6f20587da9af0d49fbe0b20078cff555521c293b
    return_cutoff_evidence_manifest=OK
    integration_dirty_paths_vs_evidence=OK
    integration_diff_vs_evidence=IDENTICAL
    origin_convergence=6f20587da9af0d49fbe0b20078cff555521c293b
    origin_minimum_launch=
    minimum_launch_head=6f20587da9af0d49fbe0b20078cff555521c293b branch=integration/prelaunch-minimum-launch staged=0 stash=0

================================================================================
20. STATEMENT
================================================================================
  Nothing was staged, committed, pushed, built, deployed or migrated live. STOP.
