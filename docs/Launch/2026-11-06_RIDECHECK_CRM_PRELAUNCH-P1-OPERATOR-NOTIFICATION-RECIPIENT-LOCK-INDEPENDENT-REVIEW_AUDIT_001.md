PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-P1-OPERATOR-NOTIFICATION-RECIPIENT-LOCK-INDEPENDENT-REVIEW

Date: 2026-11-06
Base: 163b37ecf3a70064f7b3e0b4715fd2ecf92843ca
Candidate: /opt/ridecheck-crm-p1-notification-lock, branch
fix/prelaunch-p1-operator-notification-recipient-lock, uncommitted, index empty.
Candidate tree hash, excluding this audit: b0913d7f72c01b6fb589d2b34d21c1c598e6635fce3e676b5625f6f30ca6eb2d

================================================================================
0. VERDICT
================================================================================

  CORRECTION REQUIRED

  No production-code defect was found. The lock fails closed on PostgreSQL, no send proceeded
  without it, the retry policy matches the owner decision, every gate caller handles the new
  outcome safely, and no candidate-only regression exists. The candidate does not meet
  READY FOR CONTROLLED COMMIT because the verdict requires correct roadmap wording, and the
  wording is incomplete. Bounded test and closeout corrections also remain.

  Required before controlled commit (documentation and tests only; no production change):

  C1  Roadmap. The P-1 entry never says "committed". It must state that customer outbound
      enablement may not occur until P-1 is reviewed, committed and included in the deployed
      image. Lines 1257-1258 currently say only "until P-1 passes PostgreSQL verification".

  C2  Test. O05 (claim UPDATE loses its status condition) is killed only by a race in N01. It
      survived 2 of my 7 runs. Add a deterministic test for a rival claim between the candidate
      SELECT and the UPDATE. The scratch probe in section 10 shows the shape.

  C3  Test. X05 and X06 survive: GATE_LOCK_TIMEOUT_MS = 400000 and a 600000 statement margin
      pass all 32 cases, because the tests patch the constant. Pin the production values
      (4000 and 6000), which are the owner-reviewed bounds.

  C4  Closeout. Correct the statements listed in section 14, including "22 killed", which holds
      for O05 only intermittently.

  Required before OUTBOUND_ENABLED or the operator flag is changed (not before commit):

  C5  Enablement runbook. None exists for flag order. It must enforce the sequence in section 9.
      Recording the requirement in the same roadmap edit as C1 is the cheapest way to hold it.

  C1 to C4 change test and document bytes, so section 8 of the closeout, its hashes and the
  final-tree identity must be regenerated. A focused re-check of those edits is enough; this
  review does not need repeating in full unless production code changes.

  P-1 is NOT marked passed by this audit.

================================================================================
1. SCOPE AND PROVENANCE
================================================================================

  Base and ancestry
    HEAD and origin/integration/prelaunch-minimum-launch-merge both 163b37e. Index empty. All
    changes are uncommitted on top of the base, so ancestry is direct.

  Changed paths: exactly the expected scope.
    8 tracked files modified (226 insertions, 24 deletions): operator_notification.py,
    outbound_safety_gate.py, LAUNCH_TRUTH_ROADMAP.md, test_l4_7w4_f3, test_m19_r1_2_durability,
    test_m19_r1_2_pg_concurrency, test_m19_r1_2_pg_integration, test_w1f_operator_signal.
    2 untracked: tests/test_p1_recipient_lock_pg.py and the closeout. No other path.

  Closeout hashes and tree identity
    All 9 sha16 values and the numstat match. The worktree equals the closeout's cand3 snapshot
    except the closeout itself. cand4 differs only in test_m19_r1_2_outbound_gate.py, a trial the
    closeout reverted. The base snapshot matches git archive. My entry fingerprint equals the
    closeout's exit fingerprint.

  Secrets and generated evidence
    13 live secret values searched for in scope: 0 hits. Phone literals are synthetic only
    (5491100000001, 5491100990002). No generated file in scope.

  Other worktrees: unchanged (section 16).

================================================================================
2. ORIGINAL DEFECTS, REPRODUCED ON BASE
================================================================================

  All on a disposable postgres:16 (16.13), internal network, tmpfs. Same probes on both sides.

  M19 stops before the lock
    Base, three suites: 30 failed, 6 passed; 86 UNKNOWN_SOURCE security events; 0 rows in
    whatsapp_recipient_locks. The gate refuses at step -1, so the lock is never reached. (The
    closeout recorded 94 events. The count depends on how many attempts run before failure; the
    finding is unaffected.)

  Adding only path_id reaches the real lock
    Base production code plus path_id only, base head pin kept: 35 of 36 pass. The one failure
    is the stale head pin. With the candidate's fixtures entire: 36 of 36.

  Unbounded wait
    Live role crm on crm_test: lock_timeout 0, statement_timeout 0, idle_in_transaction 0, no
    role config (read-only). With a stuck holder, base was still waiting at 25 s in a
    transactionid Lock wait, and returned ALLOWED only after the release.

  Swallowed failure produces ALLOWED without the lock
    lock_timeout=200 as a connect option, holder present: base ALLOWED in 0.23 s while the other
    transaction still held the row. pg_cancel_backend with zero timeouts: base ALLOWED. Lock row
    deleted: base ALLOWED.

  Two identical attempts both allowed
    Both ALLOWED on base: 2 pending rows, 2 dedup rows.

  Retry redirected by a configuration change
    Base: retry delivered to the other number, destination_hash overwritten, row SENT.

  Deadlock on base surfaced as raw 40P01, and pg_terminate_backend as raw PendingRollbackError.

================================================================================
3. PRODUCTION CORRECTION
================================================================================

  Transaction-local and applied first
    Statement order observed on PostgreSQL: SET LOCAL lock_timeout, SET LOCAL statement_timeout,
    INSERT ... ON CONFLICT, SAVEPOINT, SELECT ... FOR UPDATE. Afterwards the pooled connection
    reads 0/0, so nothing leaks. Mutants G08 and X08 (session-scoped) are killed by L11, and X01
    (bound after the INSERT) by L04. SET LOCAL also overrides the lock_timeout=200 connect
    option: refused at 4.03 s.

  ALLOWED only with the lock
    Candidate, same fault set as section 2:
      Stuck holder          blocked_recipient_lock, 4.04 s, blocked row, dedup 0
      Connect option 200    refused, 4.03 s
      Two identical         one allowed, one refused, dedup 1
      pg_cancel_backend     refused
      Lock row deleted      refused (_RecipientLockNotHeld)
      Deadlock victim       refused, 0.16 s, blocked row
      pg_terminate_backend  raw OperationalError (sqlstate None), no rows; next attempt allowed

    pg_terminate_backend is not a typed refusal. It is still fail-closed, because every caller
    turns the raw exception into a non-send (section 6). The always-refuse tripwire does not
    cover this exception path; caller handling of raw exceptions was probed separately.

  Session state after failure
    The caller's session is intact (7 rows visible), the pool is clean and the next attempt is
    allowed. The gate uses dedicated sessions, so the caller's transaction is untouched.

  SQLite
    Intentionally unchanged: bounds and the row guard apply only to postgresql. Full-suite
    comparison in section 11.

  New outcome is not delivery
    Section 6. No caller treats BLOCKED_RECIPIENT_LOCK as delivered.

  Attribution
    The blocked row carries path_id and deployment_id. X04 (deployment_id dropped) is killed by
    the trace suite, not the P-1 suite. The audit write is best effort: with the recipient and
    thread rows both stuck, the call refuses in 8.02 s with message_id None and no row, so only
    the log line remains (case J). It emits no SecurityEvent and has no correlation_id.
    Acceptable: it stays a refusal, and the case needs two independent stuck rows.

  No provider call under the lock
    The gate commits and closes before returning. Inside a held provider call, FOR UPDATE NOWAIT
    on the lock row succeeded and no session was idle in transaction (I3). 11 transport call
    sites read: all follow a gate result.

================================================================================
4. RETRY-RECIPIENT POLICY
================================================================================

  The implementation matches the owner decision. One literal point differs from the brief.

  First attempt establishes the recipient
    MATCHES THE OWNER WORDING, NOT THE BRIEF'S. destination_hash is NULL at claim (status
    SENDING) and record() writes it after the first attempt. So "the first eligible claim
    records" is literally false, while "the first notification attempt establishes" holds. No
    retry path exists in the gap: PENDING is reachable only through record(), and a lost sender
    goes to HELD. A NULL hash on a retry is refused (O04, N06b).

  Cannot be overwritten
    record() skips the hash on RETRY_DESTINATION_CHANGED. Killed mutants: O02, X10. Probe: hash
    kept after the refused retry.

  Same recipient may retry: SENT at attempt 2.

  Changed recipient
    0 sends, HELD, attempts 2, RETRY_DESTINATION_CHANGED, hash kept. The attention surface shows
    OPERATOR_NOTIFICATION_REQUIRED, HELD, NOTIFY_TEAM_MANUALLY.

  Missing, disabled or malformed on retry
    HELD with attempts 2, NOT_CONFIGURED_*, 0 sends, hash kept. Same on base.

  HELD is not reclaimable
    Restoring the original configuration: still HELD, 0 sends. claim_next selects PENDING only.

  No raw phone in new evidence
    destination_hash is a 16-character hash. The new blocked_reason and log line keep only the
    last 4 digits. The gate's blocked row stores the message text like the four existing
    blocked writers do.

  Concurrent retry and configuration change
    The guard compares the hash with the same config object the send then uses, so a change
    cannot land between check and send. get_settings is lru_cached, so a real change needs a
    restart. A stale-candidate early retry with a changed destination went HELD with 0 sends.

================================================================================
5. NOTIFICATION INVARIANTS (POSTGRESQL)
================================================================================

  One booking, one notice   8-way enqueue: 1 row. A forced second row is refused by the unique
                            constraint.
  Concurrent claims         8-way claim: 1 claim. Deterministic rival-in-window probe: the
                            second claim returns None; row SENDING, attempts 1.
  Cancelled before send     HELD BOOKING_NOT_OCCUPYING, 0 sends.
  Rollback                  No partial sendable row (L10, N11, N12, re-run green).
  Bounded contention        Section 7.
  Deadlock                  Refused in 0.16 s, not hung.
  Flags off                 Nothing delivered, either order (section 9).
  Retries                   Cannot duplicate or redirect (section 4).
  Crash                     SIGKILL inside the provider call: SENDING, gate row pending, no open
                            transaction. Before lease expiry nothing moves; after it HELD
                            UNCERTAIN_LOST_SENDER, 0 sends. SIGKILL holding the lock: lock
                            released, HELD after lease. Obligation stays visible.

================================================================================
6. ALL-SEND-PATH IMPACT
================================================================================

  12 attempt() call sites, read and classified. 11 transport call sites; none can be reached
  after a refusal. Probed with a real held lock unless marked static.

  Caller                                    BLOCKED_RECIPIENT_LOCK becomes           Raw exception
  ----------------------------------------------------------------------------------------------
  CE text _send_text_to_wa :16767           Typed block: action error                internal_error
                                            OUTBOUND_GATE_BLOCKED_RECIPIENT_LOCK,
                                            0 sent, cursor not consumed, ai_event
                                            reply_required/not produced, alert
                                            eligible, stays PENDING
  CE phone handoff                          Handoff: human_handoff_blocked,          internal_error
                                            needs_human true, cursor consumed,
                                            0 sent, alert eligible. Detail is
                                            mislabelled phone_call_kill_switch
  CE _blocked_outbound :3305                Result discarded, but only runs when     not applicable
                                            not armed; no transport (static)
  CE Booking Flow _send_flow_button :16863  Typed block: any non-ALLOWED raises      internal_error
                                            OutboundBlockedError before the Flow
                                            call and before _turn_flow_delivered
                                            is set; caller catches it (static,
                                            not probed under a held lock)
  Recovery Flow _send_flow :813             Retryable path: DEFINITE,                GATE_FAILED
                                            GATE_blocked_recipient_lock,
                                            0 transport, withdraw, HANDOFF
                                            FLOW_NOT_SENT
  Slot-conflict recovery text               DEFINITE, retried, then                  GATE_FAILED
    (via acknowledgement)                   TEXT_NOT_DELIVERED HANDOFF
  Acknowledgement :171                      Visible obligation: ledger COMMITTED,    PRE_SEND_FAILURE
                                            ack FAILED attempts 1, needs_human
                                            true, booking still SUCCESS. 4.23 s
                                            at the production bound
  Operator notification :460                Retryable, then FAILED (visible)         PRE_SEND_FAILURE
                                                                                     retryable
  Manual CRM whatsapp_ui :2512              Typed: 409                               500
                                            OUTBOUND_GATE_BLOCKED_RECIPIENT_LOCK
  API routes api/whatsapp :595 :648 :886    Typed: 503 (_store_outbound_and_send)    500
  Quote followup :90                        Retry next tick, no marker (static)      caught
  Buscando followup :85                     Retry next tick, no marker (static)      caught
  Unanswered-question alerts                Not a gate caller: internal email via
                                            Resend. Stays eligible through the CE
                                            ai_event, the visible backstop for
                                            CE turns

  Unhandled exception: none for the typed outcome. A raw non-lock DB error stays a 500 on manual
  and API routes, which do not send.

  Tripwire (scratch mutant: the gate always refuses; mark_sent and mark_failed from backend/app
  are logged). Full SQLite suite: 154 candidate-only failures, all from the forced refusal; 0
  hits. Positive control, same hooks with the gate open, three suites: 117 hits (102 mark_sent,
  15 mark_failed, across acknowledgement, recovery and operator notification). So the empty
  result means something. Limit: SQLite tests reach only paths those tests exercise; the 11
  transport sites were also read directly.

  Site conversation_engine:4739 reports _out("replied") but is corrected by
  out.reply_produced = bool(sent_id). Nothing claims delivery.

  Findings, non-blocking
    F-1  CE: about 20 inner `except OutboundBlockedError` sites commit and return kill-switch
         labelled detail, so the phone handoff reads phone_call_kill_switch under a lock refusal.
         Safe, misleading in the trail. Deferred.
    F-2  Flood escalation can downgrade. With the caller holding the state row at flood, the
         refusal is RECIPIENT_LOCK with needs_human false; the base hung instead. Default
         SessionLocal (autoflush False) has no wedge. Only autoflush=True, the direct-webhook
         path whose flag is false live, reaches it (4.3 s). The ai_event alert remains eligible.
         Review again before CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED is turned on.
    F-3  No test references BLOCKED_RECIPIENT_LOCK at caller level; only the P-1 suite and the
         gate do. My tripwire and caller probes offset this for this review, not for the future.
         Deferred, recommended.

================================================================================
7. TIMEOUT POLICY
================================================================================

  4000 ms lock and 10000 ms statement (4000 + 6000 margin) equal the booking-authority bounds.
  Acceptable for every caller, with no value change requested.

  Measured
    Stuck recipient lock, production bounds: refused in 4.01 to 4.04 s. Stacked with a stuck
    audit write: 8.02 s. Ack path end to end: 4.23 s.

  Against other bounds
    Recovery lease 60 s, ack lease 120 s, operator lease 120 s, transport 8 s and 15 s. All
    exceed the stacked 8 s. The statement bound is per statement, so the theoretical total is
    higher, but no measured path approached a lease.

  Single worker
    The base hang was unbounded on a single uvicorn worker. The candidate removes that for the
    lock. Followup loops still call _run_check() synchronously on the event loop, so a stuck
    lock stalls the loop 4.06 s (8 s stacked), additive per stuck recipient. Bounded and not a
    hang. Moving them to to_thread is recommended follow-up debt.

  Residual unbounded waits (outside the new bound)
    The pre-lock kill-switch and path audit writes, and mark_sent/mark_failed, are not bounded.
    Probe with the thread row held FOR UPDATE: the kill-switch audit did not finish within 12 s
    and completed only on release. No production FOR UPDATE on whatsapp_threads was found, and
    ordinary non-key UPDATEs do not conflict with the FK check, so it is not reachable today.
    Inherited, not introduced. Deferred.

================================================================================
8. CANCELLATION DURING AN IN-FLIGHT PROVIDER SEND
================================================================================

  Who can be affected
    Only the operator. C.c: customer outbound rows unchanged 1 to 1. Cancels come from CRM and
    API through BookingAuthority; no customer-initiated path was found.

  The window
    Larger than the closeout says. It opens at the summary read, not at "the send itself": a
    cancel inside the gate (C.b) was still sent to the operator. It closes when Meta accepts. A
    stuck lock can widen it by 4 to 8 s.

  State afterwards
    Truthful. The revision reads CANCELADO, the notice SENT, the gate row sent.

  Visible and reconcilable
    Partly. SENT rows are not on the attention list, so it is not flagged. It is reconcilable
    only by joining the notice to the revision. The operator sees the later cancel through the
    CRM, not through a correcting notice.

  Closing it
    Would need a database lock across the provider call, which the design forbids.

  Decision: acceptable documented debt, not a launch blocker. The worst case is one obsolete
  notice to staff.

================================================================================
9. FLAG ORDER
================================================================================

  Confirmed. Operator flag on with OUTBOUND_ENABLED false: PENDING, then FAILED with attempts 2,
  GATE_blocked_kill_switch, 2 blocked rows, 0 sends. Not delivered later. The attention surface
  shows it as required. Same on base.

  Also: flag off at commit gives HELD, attempts 0, NOT_CONFIGURED_DISABLED, and it is never
  revived on enable. A kill switch during a pending retry gives FAILED, not delivered later. Both
  on: SENT.

  Safe sequence from current code (C5)
    Precondition: the image contains P-1, and template, token, destination and floor are all in
    place. get_settings is lru_cached, so config needs a container recreate.
    Rule: never OPERATOR_WHATSAPP_NOTIFY_ENABLED=true while OUTBOUND_ENABLED is not true.

    Enable    Set both flags in the SAME container recreate. It is the only zero-gap option:
              bookings committed during a notify-off window are HELD for good. The alternative,
              OUTBOUND_ENABLED first and operator flag second, is safe, but bookings between the
              two steps need manual follow-up.
    Disable   Operator flag false first, then OUTBOUND_ENABLED false.
    Emergency The kill switch stays available. It burns pending notices to FAILED, visible, and
              each needs manual follow-up.

  docs/operations covers neither. The live floor is present (value 5), so enqueue is active once
  the flag is on.

================================================================================
10. TEST QUALITY
================================================================================

  30 M19 additions: exactly 30 path_id=_PATH fixture repairs, no assertion touched. Genuine.

  Alembic head assertion: valid, and stronger than the literal pin it replaces. It requires the
  database to equal the repository head AND that head to descend from the recipient-lock
  revision. Both legs are non-vacuous: database forced to 20261103_slot_recovery_job fails on
  equality; an impossible ancestor (scratch test edit) fails on the walk, whose list contains the
  real 20260629 revision. My first equality probe was invalid: the forced 20260629 id is 38
  characters, too long for varchar(32), so the UPDATE failed and the test passed at head. I
  caught that and reran it with a revision that fits. A database genuinely at 20260629 could not
  be represented in this column at all.

  Writer count 4 to 5: justified. AST writers are authorized_path, kill, flood, dedup and the new
  one. Each is still checked for path_id and deployment_id.

  Nothing weakened: no skip or xfail added. test_w1f_operator_signal gains n04b.

  Red on base: 13 failed, 19 passed. That is the closeout's 10 plus L13, L14 and N06b, which it
  said were added later. Each failed for its intended reason: unbounded wait, ALLOWED, 2 != 1,
  raw deadlock, redirected send.

  Green: P-1 plus M19 concurrency on 3 fresh databases, 43 passed each.

  Synchronization: L and N cases hold a real row and pause inside the gate; pg_stat_activity
  evidence shows the wait. Exceptions are N01 (barrier race, see below) and the M19 a/c cases,
  where which test fails varies by run.

  Mutants, final tree, one at a time, fresh database
    Closeout's 22, all run:
      G01-G15   all killed (G01 m19 8 failed, G02 m19 6; closeout 7 and 5; race-based counts)
      O01-O04, O06, O07   killed
      O05       INTERMITTENT. Survived in the first run, then 1 survived and 5 killed in 6
                repeats: killed 5 of 7, only ever by N01.
    Mine, 13:
      X01 X07 X08 X09 X10 X11 X12 X13   killed by the P-1 suite
      X04                               killed by the trace suite only
      X02   survives: any DB error becomes a refusal. Safe direction, no gap that matters.
      X03   survives: 40001 unpinned. Not reachable at READ COMMITTED, and dropping it gives a
            raw error, which does not send. Accepted.
      X05, X06   survive: production constants unpinned. C3.

  O05 detail. In production the status condition is the only guard against a stale candidate,
  and the deterministic probe proves it works: candidate second claim None, row attempts 1. With
  O05 the second claim succeeds, attempts 2 on a SENDING row. The code is right; the test only
  sometimes notices. C2.

  O07. The closeout describes the weak kill honestly: N17 only, by starving the candidate limit.
  Confirmed by X12 and X13: removing the further layers fails 2 and 16 cases. "Conditional UPDATE
  is a second layer" is true, and that layer is the one O05 removes.

================================================================================
11. REGRESSION COMPARISON
================================================================================

  Image ridecheck-crm-test:l4.7b2. Immutable base, final candidate tree, serial, --network none
  for SQLite. Identities are class::name, so subtests fold into one identity (raw counts below).
  Message comparison is normalised, first 600 characters.

  Full SQLite
    Side   failed  passed  skipped  errors  cases  failing identities
    base       62    7486      752       9   8308  71
    cand       62    7487      784       9   8341  71
    NEW 0   GONE 0   COMMON 71   COMMON changed message 0
    +33 cases = 32 P-1 (skip without a PostgreSQL URL) + n04b (passes). Removed 0.
    The 71 by file: m20_6d2 21, m19_r1_2_outbound_gate 12, l4_2_clean_slate 10, m12_calendar_ui
    8, Whatsapp Test 4, m12_3_calendar_nav 3, then ten files with 1 or 2. Reproduces the
    closeout's figures.

  Focused SQLite (within that run, candidate)
    m19_r1_outbound_safety_gate 15 pass; m19_f2_2_kill_switch 26 pass; m2_authorized_paths 28
    pass; l4_7w4_f3 19 pass; w1f_operator_signal 37 pass; minimum_launch 41 pass;
    m19_r1_2_outbound_gate 8 pass, 12 fail (same on base).

  Full PostgreSQL inventory, EXACT FINAL TREE, both sides, floor provisioned
    37 files on candidate, 36 on base (no P-1 file), fresh clones of a chain from the retained
    schema-only dump, head 20261104_vehicle_identity, floor provisioned rc 0 on both.
    Raw (tests / failures / errors / skipped): base 1814 / 78 / 0 / 9 for shared files; same
    1814 / 48 / 0 / 9 candidate, plus P-1 32 passed.
    Identities: base 1130 cases, 64 failing. Candidate 1162 cases, 34 failing.
    NEW 0   GONE 30   COMMON 34   COMMON changed message 0   +32 cases, removed 0.

    This is stronger than the closeout, which ran the non-notification files on cand2 and only
    15 files on the final tree. Its stated limit is now closed.

    GONE 30: exactly the 30 test_m19_r1_2_* identities (durability 10, concurrency 11,
    integration 9).

    COMMON 34, attributed by message; none passes through changed code:
      Stale head pins and their second-order effects, convergence p2 (3), p4 (1), p4_r1 (8),
      strict correction (7), strict schema (12): 31
      test_w1f_operator_notification_pg m01, stale head pin: 1
      test_l4_2_clean_slate l4s04: environment, allowlist value absent: 1
      test_l4_7w5_g37r_rv03 pg_13: reset coverage lists whatsapp_outstanding_questions and
      whatsapp_thread_evidence: 1
    The closeout's Run B had 25 because it covered 15 files. Same class; none is a new class.

  Notification suites are not inert
    test_w1f_operator_notification_pg 9 of 10, the 1 a pin. test_minimum_launch_pg 31,
    vehicle_identity_recovery_pg 83, w1f_o1 22, unanswered_alert 4, w01_r2 346, m19_r1_3e 34 of
    34, m19 durability 10, concurrency 11, integration 15, P-1 32. All pass.

  Run A / Run B floor distinction: explained, closeout accurate.
    enqueue_committed_bookings reads operator_notification_floor_ledger_id and, without a valid
    value, enqueues nothing by design. Migration 20261022 provisions it from data, so a
    schema-only restore has none. Run A's log shows it: 4 setup errors in the operator-notification
    suite, TypeError int(None) at test line 52. So Run A was inert for notifications on both
    sides, which the closeout says and does not rely on. My run provisions the floor. Live
    crm_test has the key (value 5), so production is not in the Run A state.

================================================================================
12. ROADMAP
================================================================================

  Hard prerequisite before OUTBOUND_ENABLED for customers    present, lines 1243-1245
  Commit, push and deployment preparation allowed before     present, line 1256
  Enablement forbidden until reviewed, committed, deployed   INCOMPLETE. Lines 1257-1258 say
                                                             "passes PostgreSQL verification";
                                                             lines 1263-1264 add review and
                                                             image. "Committed" appears nowhere.
  Current status NOT PASSED                                  present, line 1520

  Suggested wording for C1, lines 1257-1258: "Customer outbound enablement may not occur until
  P-1 is reviewed, committed and included in the deployed image."

  Not marked passed here.

================================================================================
13. REMAINING INHERITED ISSUES
================================================================================

  Issue                               Class                      Note
  ---------------------------------------------------------------------------------------------
  12 stale SQLite M19 tests with      Deferred debt              Same 12 on both sides. Handwritten
  handwritten schema                                             schema; not evidence either way.
  Stale migration-head pins           Deferred debt              31+1 PostgreSQL failures, and they
                                                                 hide upgrade assertions behind
                                                                 them. Repair after launch-critical
                                                                 work; do not read as green.
  claim_next not rechecking           Accepted debt              Needs concurrent senders; one loop
  next_attempt_at                                                today. Early retry stays guarded.
                                                                 BECOMES A BLOCKER if a second
                                                                 sender or worker is added.
  Shared-recipient flood limits       Accepted debt              Not run; code reading and the
                                                                 closeout. A burst above the limit
                                                                 fails some notices, visibly.
  In-flight cancellation              Accepted debt              Section 8. Operator only.
  Flag order                          LAUNCH BLOCKER for         Not a code blocker. Blocks
                                      enablement only            enablement until C5 exists.

  No other launch blocker. P-1 itself remains the blocker for customer outbound.

================================================================================
14. CLOSEOUT CORRECTIONS (C4)
================================================================================

  1  Section 7 "22 killed": O05 is an intermittent kill by a race. State it, or fix via C2.
  2  Section 9 "differ in one line": cand2 and final differ in 2 hunks (an import and set_floor).
  3  Line 128: _send_flow does not wrap attempt(), but its caller does (about line 949,
     GATE_FAILED_*). Raw exceptions are handled there.
  4  Line 286: CE takes "the usual non-kill-switch route" is incomplete. About 20 inner sites
     return kill-switch labelled detail (F-1).
  5  Line 284: "The window is the send itself" understates it. It opens at the summary read.
  6  Line 56: 94 events; I measured 86. Say it varies, or drop the number.
  7  Add that a terminated backend is a raw exception, not a typed refusal, and still no send.

================================================================================
15. MEMORY AND DEVIATIONS
================================================================================

  25 gated heavy runs, one at a time. Lowest MemAvailable during any run 925 MiB (sq-trip), above
  the 500 MiB abort. No abort. Lowest at any start 1966 MiB. Live containers never stopped.

  One gate stop: the first mutant chain stopped at O03 because MemAvailable read 1443 MiB, below
  1536, as a container tore down. Memory then read about 2030 MiB with no other heavy job, and
  consumers were only Claude sessions and n8n. I resumed with a bounded re-check (4 x 20 s) that
  would stop if memory stayed low. It did not. I did not ask for owner direction, because the dip
  had cleared on its own; the brief's stop applies when memory stays insufficient.

  Other notes
    - Live reads were read-only: role settings, schema head, floor key, container env.
    - Harness artifact fixed in my probes: duplicate fake WAMIDs from separate Template instances
      caused a UniqueViolation in mark_sent. Not a product fault.
    - One compound scratch command, a hard-link copy, was denied. I used read-only file overlay
      mounts instead, as the mutant runner already did. No candidate file was involved.
    - Shared-recipient flood was not run; classified from code.

================================================================================
16. EXIT VERIFICATION
================================================================================

  Exit fingerprint, taken after this audit was written, is byte-identical to the entry
  fingerprint across:
    refs, stash, all other worktrees (HEAD, status, diff), merge worktree full manifest
    live containers (ID, image, env hash), compose and env files, live flags
    TARGET_v7.7 4ce27122..., CANDIDATE_v7.8 2102a8ec...
    live schema heads (crm 20260624_group_default_viaticos, crm_test 20261103_slot_recovery_job)
    candidate head 163b37e, index 0, tree b0913d7f72c01b6f...

  Live flags at exit: OUTBOUND_ENABLED false, OPERATOR_WHATSAPP_NOTIFY_ENABLED false.
  Disposables: no p1rev container or network remains.

  Nothing staged, committed, pushed, merged, built, deployed or migrated. No flag changed. Meta
  not contacted. No message sent.

  VERDICT: CORRECTION REQUIRED
