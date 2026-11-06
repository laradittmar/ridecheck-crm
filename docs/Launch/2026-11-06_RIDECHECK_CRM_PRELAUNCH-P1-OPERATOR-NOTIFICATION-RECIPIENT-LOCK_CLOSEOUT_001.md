PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-P1-OPERATOR-NOTIFICATION-RECIPIENT-LOCK

Revision: R1, after independent review. The review's verdict was CORRECTION REQUIRED, with no
production-code defect. Its corrections are applied here and logged in section 15, which keeps the
original wording of every statement that changed. Figures not marked R1 are the original closeout's.

Date: 2026-11-06. Nothing staged, committed, pushed, merged, built, deployed or migrated. No flag
changed, no Flow published, Meta not contacted, no message sent. All test values are synthetic.

================================================================================
0. VERDICT
================================================================================
READY FOR FOCUSED INDEPENDENT RE-REVIEW   (R1; the original verdict was READY FOR INDEPENDENT REVIEW)

  This is not READY FOR DEPLOYMENT or ENABLEMENT. P-1 stays NOT PASSED in the roadmap until this
  closeout is accepted by review AND the corrected code is committed AND it is in the image that
  enables outbound.

  Diagnosis      BOTH. Stale fixture, and it was masking real product defects in the lock.
  M19 suites     30 failed / 6 passed on base; 36 of 36 pass, and they now reach the lock.
  New suite      tests/test_p1_recipient_lock_pg.py, 37 cases after R1 (32 reviewed + 5 new), all
                 green. Of the 32, 13 fail on base production (independent review). The 5 were not
                 run on base.
  Repeats        Original: 5 fresh databases, 5 x 43 passed. R1: 3 fresh databases, 3 x 48 passed
                 (P-1 suite + M19 concurrency), and the three M19 suites 36 of 36.
  Mutants        22 valid, 22 killed, now that O05 is deterministic: 10 of 10 in R1 (section 7).
                 R1 reran O05 only. The other 21 kills are from the 32-case file. X05, X06 killed.
  SQLite full    failing 71 vs 71. NEW 0, GONE 0, COMMON changed 0. Before R1; NOT rerun in R1.
  PostgreSQL     NEW 0, GONE 30 (exactly the M19 tests), COMMON changed 0. See section 9 for how.
                 Before R1; NOT rerun in R1. Production code is byte-identical to that run.
  Migration      none. Head stays 20261104_vehicle_identity.

  The reviewer should look at these first:
    R-1  The gate is SHARED by every automated send. The fix adds one GateOutcome to all paths.
    R-2  "The recipient is selected under the PostgreSQL lock" is NOT the repository's contract.
         The recipient comes from configuration; the lock does not choose it. Section 3.
    R-3  Retry recipient: the repository was silent. Prompt item E.3 is taken as the owner's
         decision, and HELD is my choice of how to honour it. Owner should confirm. Section 3.
    R-4  A cancellation landing during an in-flight send is not closed. Section 12.
    R-5  12 more stale M19 tests exist, on SQLite, for a different reason. Left alone. Section 12.

================================================================================
1. TARGET AND ISOLATION OF THE WORK
================================================================================
  Worktree  /opt/ridecheck-crm-p1-notification-lock
  Branch    fix/prelaunch-p1-operator-notification-recipient-lock
  Base      163b37ecf3a70064f7b3e0b4715fd2ecf92843ca (HEAD unchanged; index empty)
  origin/integration/prelaunch-minimum-launch-merge resolved exactly to that commit, by rev-parse
  and by ls-remote. Neither the branch nor the directory existed before creation.
  Evidence  /opt/ridecheck-builds/crm-p1-notification-lock-20261106T151126Z (outside the repo)

================================================================================
2. EXACT DIAGNOSIS
================================================================================
  Terminology first. Finding P-1 of the controlled-merge review concerns `whatsapp_recipient_locks`,
  the lock inside OutboundSafetyGate.attempt(), keyed by wa_id, taken for EVERY automated send. No
  lock exists specifically for operator notices. The "operator-notification recipient lock" is that
  lock as crossed on the OPERATOR_NOTIFICATION path, together with the notice's own recipient
  decision. Both were examined.

  First failure, reproduced on base, disposable PostgreSQL 16.13, real Alembic chain:
    test_m19_r1_2_pg_concurrency::test_a1_fresh_lock_audit_rows
    AssertionError: 0 != 1 : Expected 1 pending row, got 0
  Three suites: 30 failed, 6 passed. In the database afterwards: OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE
  security events (BLOCKER), and 0 rows in whatsapp_recipient_locks. The event count varies with how
  many attempts run before refusal, so no fixed number is claimed (R1 item 6). The gate
  refused at step -1 (correctly: path_id is mandatory since M21.3) and the lock was never touched.

  Classification against the four options in the prompt:
    1 stale fixture only              NO, though the fixture was stale
    2 real production admission defect NO. The path_id refusal is correct and unchanged
    3 masking a second product defect  YES, three
    4 contract ambiguous               PARTLY, for the retry only (section 3)

  With path_id alone, 35 of 36 passed. That looked like "fixture only", but those suites never
  contend past a bound. Deterministic probes on BASE production code found:

  D-1  UNBOUNDED LOCK WAIT. ACTIVE ON LIVE CONFIGURATION.
       The gate sets no lock_timeout, unlike every other lock in this repository. Live crm_test has
       lock_timeout=0, statement_timeout=0 (read-only query, live.lock_settings.txt). Observed: one
       gate blocked 7m35s on FOR UPDATE, wait_event=Lock, until I killed the run
       (red.unbounded_wait.pg_stat_activity.txt). The wait is also invisible to the deadlock
       detector when a caller holds the thread-state row and waits for the gate (L07), with the
       recipient lock held throughout.

  D-2  LOCK FAILURE SWALLOWED: THE GATE PROCEEDED UNLOCKED. LATENT ON LIVE.
       `except Exception: nested.rollback()` meant for SQLite's missing FOR UPDATE also swallowed
       PostgreSQL's failures. Observed on base: gate returned ALLOWED with the lock held by someone
       else (L05), and TWO ALLOWED for one identical message (L06). It needs a non-zero timeout or a
       cancelled backend, so it cannot fire on today's live settings by itself. But D-1 and D-2 are
       coupled: bounding the wait without fixing this would have ACTIVATED the fail-open.
       Also: FOR UPDATE matching zero rows succeeds and locks nothing (L09, a reset deleting the
       row). And a deadlock victim escaped as a raw exception (L08), which some callers do not catch.

  D-3  A RETRY COULD BE REDIRECTED.
       run_once re-reads operator_config(); nothing compared it with the first attempt. Observed on
       base: attempt 1 rejected, destination changed, attempt 2 sent to the NEW number and
       destination_hash overwritten, erasing the original decision (N06). Needs a configuration
       change plus restart while a retry is pending.

================================================================================
3. INVARIANT PROVEN, AND WHERE EACH POINT COMES FROM
================================================================================
  One committed booking, at most one eligible notice
    Determined: UNIQUE(booking_command_id), UNIQUE(identity), module docstring, 20261022 migration.
  Recipient/path "selected under the required lock"
    NOT THE CONTRACT. Determined the other way: the destination is read from protected runtime
    configuration only; no caller passes one (CLAUDE.md, operator_notification docstring). The gate
    validates it at step -1, BEFORE the lock. The lock serialises the flood and dedup decision for
    that recipient. Proven as stated here, not as phrased in the prompt.
  Concurrent attempts cannot redirect, duplicate or lose
    Determined. Conditional claim + per-attempt delivery identity + recipient lock. A crash loses
    no obligation: the ledger row remains, a lost sender becomes visible HELD.
  Retries preserve the original recipient decision
    Repository SILENT. Taken from prompt E.3 as the owner's decision. Implementation is my choice:
    the repository's rule that anything it cannot safely send is HELD and visible. Consequence the
    owner should accept knowingly: correcting a wrong operator number does NOT auto-deliver a
    pending retry; it surfaces as OPERATOR_NOTIFICATION_REQUIRED for manual follow-up.
  Cancelled or superseded bookings remain non-sendable
    Determined (PRELAUNCH-MINIMUM-LAUNCH C). Proven up to the send boundary; limit in section 12.
  No notice while controlling flags are off
    Determined (OUTBOUND_ENABLED at gate and transport; OPERATOR_WHATSAPP_NOTIFY_ENABLED fail-closed).

  Invariant added on PostgreSQL: the gate never decides for a recipient whose lock it does not hold.

================================================================================
4. CORRECTION
================================================================================
  outbound_safety_gate.py
    * SET LOCAL lock_timeout 4000 ms, statement_timeout 10000 ms for the gate's own transaction: the
      booking authority's values. Gate holds are milliseconds with no external call, so reaching the
      bound means a stuck holder, not ordinary contention.
    * On PostgreSQL any failure to hold the lock now raises. SQLite's path is unchanged.
    * Limit (R1 item 7): pg_terminate_backend produces a raw exception, not the typed refusal.
      Every caller still produces no send.
    * New GateOutcome.BLOCKED_RECIPIENT_LOCK: rollback (no dedup claim, no pending row), a blocked
      audit row, best effort under the same bound. Outcome, not exception, because every caller
      already treats != ALLOWED as nothing sent, while a raw exception is not handled uniformly.
      booking_slot_recovery._send_flow does not wrap attempt() itself, but its caller does, and
      records the raw failure as GATE_FAILED_*. The CE does not wrap it (R1 item 3). All 12 call
      sites read.
  operator_notification.py
    * Claim carries destination_hash. A retry to any other number, or with none recorded, is HELD
      RETRY_DESTINATION_CHANGED, and the recorded decision is kept.
    * For the notice, a contended lock is GATE_blocked_recipient_lock: retryable, nothing left the
      process, existing code path, no change needed.

  Not redesigned: claim, lease, enqueue, dedup, flood, kill switch, transport untouched.

  Test changes that are not new tests, for review of "no assertion weakened":
    * 30 gate calls in the 3 M19 PostgreSQL suites gain path_id=CE_TEXT. No assertion touched.
    * test_b1_alembic_head_is_rolling_window: a literal pin to 20260629 that every later head fails.
      Now asserts the database is at the repository's single head AND that head descends from
      20260629. Same guarantee, and stricter than "this one string". A judgement call: please check.
    * test_trace_02: count of blocked-row writers 4 -> 5. My new writer is held to the same
      path_id/deployment_id attribution by that test. It was the single NEW SQLite failure, caught
      by identity comparison, with raw counts nearly flat.

================================================================================
5. CHANGED-PATH MANIFEST (sha256, 16)
================================================================================
  55d6b9624b66eb04  +13  -3   backend/app/services/operator_notification.py
  a69e0f4866b5acae  +114 -4   backend/app/services/outbound_safety_gate.py
  700cf3afed428cc3  +25  -0   docs/launch/LAUNCH_TRUTH_ROADMAP.md
  e7543c6196e05515  +2   -1   tests/test_l4_7w4_f3_booking_price_completeness.py
  6ff561a2a33e1e52  +19  -0   tests/test_m19_r1_2_durability.py
  3296bde5fe30c8fa  +6   -0   tests/test_m19_r1_2_pg_concurrency.py
  70fa21be62f9e4d0  +30  -16  tests/test_m19_r1_2_pg_integration.py
  dc38dc89f39dfc15  +17  -0   tests/test_w1f_operator_signal.py
  17708a7d390f1632  new       tests/test_p1_recipient_lock_pg.py (717 lines)
  plus this closeout, which cannot state its own hash.
  Frozen final tree cand3, manifest c5cc4d58b265a150...; base snapshot 3e1a092ae894d29f...
  Working tree verified identical to cand3.

  R1. The list above is the reviewed state. Two hashes changed in R1, plus this closeout:
  073e448bd1ffe2e1  +55  -0   docs/launch/LAUNCH_TRUTH_ROADMAP.md
  c4bfe4ce2a9ad53a  new       tests/test_p1_recipient_lock_pg.py (815 lines, +98 -0 against reviewed)
  Unchanged, confirmed: 55d6b9624b66eb04, a69e0f4866b5acae and the other 5 test files above.
  Tree digest, the review's method (sha256 of the sorted sha256sum manifest, no .git):
    reviewed tree, audit excluded                 b0913d7f72c01b6fb589d2b34d21c1c598e6635fce3e676b5625f6f30ca6eb2d
                                                  (reproduced from the R1 entry manifest)
    audit and this closeout both excluded, before 4888607b2a2da40d9074234dad8f56f1ba40e0a77f964f6f86be1203b4c9898e
    audit and this closeout both excluded, final  0d17b5a592000c5a34d39e45f5a3bd629a6f7f62b1970c4bef675170638d55c4
  This closeout cannot state a digest that includes itself. cand3 is no longer the final tree.

================================================================================
6. POSTGRESQL CONCURRENCY EVIDENCE
================================================================================
  Disposable postgres:16 (16.13), tmpfs, --internal network. Synchronisation is deterministic: a
  gate is paused INSIDE the critical section and pg_stat_activity (wait_event_type = Lock) confirms
  the contender really waits. No sleep decides any outcome.

  Required proof                                  Tests                         Base
  1 M19 reaches the lock                          M19 36/36; mutants G01, G02    30 failed
  2 Concurrent claims, one sendable notice        L01 L02 N01 N02 N03            pass
  3 Retry cannot change recipient/path            N04 N05 N06 N06b               N06 FAILED
  4 Cancelled/superseded never sendable           N07 N08 N09 N10                pass
  5 Contention bounded, no indefinite deadlock    L03 L04 L07 L08 L13 L14 N13    FAILED
  6 Failure/rollback leaves no sendable row       L10 N11 N12                    pass
  7 Flags off, no delivery                        N14 N15 N16                    pass
  8 Unchanged outside P-1                         section 9                      -
  Fail-closed (D-2)                               L05 L06 L09 L12                FAILED
  Scope of the bound (SET LOCAL does not leak)    L11                            pass
  R1: rival claim between SELECT and UPDATE       N18                            not run
  R1: reviewed bounds, constants and on the server B01 B02 B03 L15               not run

  Base production ran the 29 cases that existed then: 10 failed, 19 passed. Each failure read and
  attributed. L13, L14 and N06b were added later and never ran on base; the Base column is by group. Earlier red runs had faults in MY tests, fixed and not counted: slot exhaustion,
  isolation between tests, a query that missed kill-switch audit rows, and L06 first pausing too
  early and passing on base, which meant it proved nothing until moved to the real window.

================================================================================
7. MUTATION
================================================================================
  R1 correction: as first written this said "22 killed", but O05's kill was a race in N01 and it
  survived 2 of the independent review's 7 runs. The statement was not true then. It is now, by
  test N18. The original text follows; the R1 results come after it.

  22 valid mutants, one at a time, fresh database each. 22 killed.
    G01 remove the lock call, G02 remove FOR UPDATE: also killed by M19 ALONE (7 and 5 failures),
      so the repaired suites genuinely reach and depend on the lock.
    G03-G15: fail-open restored, bounds removed, session-scoped bound, row guard, each SQLSTATE,
      audit bound, refusal reported as ALLOWED.
    O01-O07: retry guard, hash overwrite, guard on first attempt, null hash, claim condition,
      cancellation check, HELD reclaimable.
  O04 (null hash passes) SURVIVED first. Test N06b added; now killed.
  O07 is a weak kill: only N17, by starving the candidate limit. In production the claim's
  conditional UPDATE is a second layer, so it would not send. Not claimed as strong evidence.

  R1 results, final test file, one run at a time, fresh database each, file bound read-only:
    Control N18 alone          10 runs: 10 passed.
    O05, N18 alone             10 runs: 10 failed, each at "A claimed a row the rival already held".
    O05, whole P-1 file        failed N18 at that assertion; N01 failed too in this run.
    X05 lock 400000            killed: B01 B03 L15     X06 margin 600000      killed: B02 B03 L15
    X14 statement = lock only  killed: L14 L15         X15 statement = margin killed: L14 L15
    X16 constants swapped      killed: B01 B02 L15     X17 rowcount ignored   killed: N18
  X14 to X17 are extra, for the relationship between the bounds and for the claim's rowcount guard.
  Limit: of the 22, R1 reran O05 only. The other 21 were killed against the 32-case file, here and
  in the independent rerun. R1 added 98 test lines and removed 0, so those kills still stand, but
  they were not re-executed. The review's other X mutants were not rerun either.

================================================================================
8. SQLITE COMPARISON (immutable base vs exact final tree)
================================================================================
  ridecheck-crm-test:l4.7b2, DATABASE_URL=sqlite://, read-only trees, --network none, serial.
  Side   failed  passed  skipped  errors  (junit) cases  failing identities
  base       62    7486      752       9           8308  71
  final      62    7487      784       9           8341  71
  NEW 0   GONE 0   COMMON 71   COMMON with changed messages 0
  +33 cases = 32 P-1 cases (skip without a PostgreSQL URL) + n04b (passes). Removed 0.
  Focused SQLite: 120 passed, 43 skipped, 12 failed. The 12 are all in the base failing set: R-5.
  R1: this comparison was NOT rerun. Only the P-1 file was run on SQLite: 3 passed (B01 to B03),
  34 skipped. A full run would now show 5 more cases than the table above.

================================================================================
9. POSTGRESQL COMPARISON
================================================================================
  The gate is shared infrastructure, so the complete inventory was required and run.
  R1: NOT rerun; focused checks only (section 15). The review's own run on the final tree of the
  time, 37 files on both sides, closes the limit stated below: NEW 0, GONE 30, COMMON 34, changed 0.

  Run A, 37 files, each on fresh clones, chain from the retained schema-only dump (16c35a0c1ecccf1c,
  0 data rows), base vs cand2:
    Failing 84 vs 70.  NEW 16, GONE 30, COMMON 54, COMMON changed 0.
    The 16 NEW were all in my N-suite, and that exposed a harness fault: a schema-only restore has
    no operator_notification floor, so enqueue correctly fails closed and enqueues nothing. Therefore
    in Run A the notification suites were INERT ON BOTH SIDES. "Unchanged" meant nothing for them,
    and I do not rely on Run A for them.

  Run B, every notification-related file plus all M19, floor provisioned, writable trees, database
  named crm_test on the disposable for r1_3e, base vs exact final tree, 15 files:
    Failing 55 vs 25.  NEW 0, GONE 30, COMMON 25, COMMON changed 0.  +32 cases, removed 0.
    test_w1f_operator_notification_pg   9 of 10 (Run A: 4 setup errors, 1 pin); the 1 is a stale head pin
    test_m19_r1_3e_reset_rehearsal      34 of 34 (exercises reset of the lock table)
    test_minimum_launch_pg 31, vehicle_recovery_pg 83, w1f_o1 22, unanswered_alert 4,
    w01_r2 346, m19 durability 10, concurrency 11, integration 15, P-1 32: all pass.

  GONE 30 = exactly the 30 test_m19_r1_2_* identities. COMMON 25 attributed by message: stale head
  pins and second-order effects in convergence p2, p4, p4_r1, strict, plus the w1f pin. None passes
  through changed code; none changed message.

  Limit: the non-notification files of Run A ran on cand2, not the final tree. cand2 and final
  differ in two hunks of tests/test_p1_recipient_lock_pg.py, an import and the set_floor call
  (R1 item 2), which Run B ran. Run A's non-notification
  files are unaffected by the floor: 0 NEW there.

================================================================================
10. MIGRATION IMPACT
================================================================================
  None. No schema change, no new column, no CHECK touched. Head stays 20261104_vehicle_identity.
  status='blocked' already exists; blocked_reason is Text; both new codes fit last_error String(64).
  Rollback of this code is safe: new values are only text in existing columns.
  Live crm_test is at 20261103_slot_recovery_job, behind this tree. The merge's own migration
  ordering stands and is not changed by this work.

================================================================================
11. MEMORY, ISOLATION AND ROADMAP
================================================================================
  Memory: gate MemAvailable >= 1536 MiB before each heavy job, one at a time, watchdog at 500 MiB.
  Lowest in any job 914 MiB (PostgreSQL inventory). The watchdog never fired. Entry about 2040 MiB.
  No session had to be exited. Stale `tail` watchers from earlier sessions exist; not touched.

  Exit fingerprint vs entry, by script (fingerprint.sh): IDENTICAL, b64a116b665f4b30. Rechecked after
  this closeout was written.
    Merge worktree      every file hash identical, HEAD 163b37e, clean. Not modified.
    Other worktrees     all 11: HEAD, status and diff hashes identical; refs identical bar new branch
    Live containers     backend, n8n, postgres: same id, image, start time, restart count 0, env hash
    Compose and .env    identical
    Runtime flags       unchanged; OUTBOUND_ENABLED and OPERATOR_WHATSAPP_NOTIFY_ENABLED false
    TARGET_v7.7         4ce27122a55f3efd...   CANDIDATE_v7.8  2102a8ec5f01be41...   both unchanged
    Live schema head    crm_test 20261103_slot_recovery_job, unchanged
    Live database       read-only queries only, in a read-only transaction: alembic_version and
                        timeout settings. No write.
    Disposables         p1lock-* containers and networks removed by exact name: 0 remain
  Deviations to note: `git fetch origin` was run once from the merge worktree before any change
  (FETCH_HEAD only; refs unchanged). One tool call was denied early (it bundled an unneeded rm -rf);
  it was reissued as smaller steps with no deletion.
  Not removed: the evidence directory, with frozen trees and pgin/pgbin, retained for review.

  Roadmap: docs/launch/LAUNCH_TRUTH_ROADMAP.md, additions only, in L5: the hard prerequisite with
  all six owner points, an exit-criterion line, and a status-tracker row (NOT PASSED). No other
  decision reworded.
  R1: within that P-1 entry only, the "Not allowed" line now reads "Customer outbound enablement may
  not occur until P-1 is reviewed, committed and included in the deployed image.", "committed" joins
  the pass condition, and the safe flag order follows it. P-1 stays NOT PASSED. No flag changed.

================================================================================
12. REMAINING RISKS
================================================================================
  1 NOT DEPLOYED. Live runs 198ed4d. Neither the live image nor the merge branch has this fix.
    Enabling outbound on an image without it would not satisfy P-1.
  2 CANCELLATION IN FLIGHT. A cancellation committing inside the window is not closed: holding a
    booking lock across a provider call is forbidden by design. The window opens at the notification
    summary read and closes when Meta accepts the send (R1 item 5). A stuck lock can widen it by 4 to
    8 s. Cancelled before the summary read, or between attempts: proven held.
  3 THE BOUND IS A NEW BEHAVIOUR ON ALL PATHS. A wait over 4 s now ends as a refusal, not a longer
    wait. CE: an OutboundBlockedError, but approximately 20 inner CE sites may return
    kill-switch-labelled detail under this lock refusal, so the trail can name the wrong cause while
    nothing is sent (R1 item 4; exact count in section 15). Manual CRM: 409 with
    OUTBOUND_GATE_BLOCKED_RECIPIENT_LOCK. API routes: 503. Nothing duplicates; a customer reply can be
    not sent. Only under a stuck holder, which previously meant a hang. The event loop can still be
    blocked for up to the bound, instead of indefinitely.
  4 R-5: test_m19_r1_2_outbound_gate.py, 12 failing on SQLite, inherited, in no prior closeout's
    list. It omits path_id AND uses a hand-written shadow schema missing whatsapp_threads.inbound_channel,
    which fails first. I tried the path_id repair, saw that, and reverted it: rebuilding that schema
    is unrelated to the PostgreSQL lock. Gate logic stays covered by test_m19_r1_outbound_safety_gate
    and the PostgreSQL suites. Recommended follow-up.
  5 Flag order. With the notice configured and OUTBOUND off, each notice is blocked twice and ends
    FAILED, visibly, sending nothing. It is not re-sent later. Existing behaviour. The required order
    is now in the roadmap beside P-1 (R1, C5).
  6 claim_next's conditional UPDATE does not re-check next_attempt_at: a second concurrent sender
    with a stale candidate could retry early. It cannot duplicate or redirect, and one loop runs
    today. Observed, not changed.
  7 Flood limit (3 per 60 s per recipient) applies to the single operator number: a burst of bookings
    can delay notices, and exhaust their two attempts to FAILED, visibly. Existing behaviour.
  8 The 25 COMMON PostgreSQL failures still hide real upgrade assertions behind stale pins (C-4).

================================================================================
13. PROPOSED COMMIT
================================================================================
  Not staged. For the owner, after review:

  Subject:
    fix(outbound): bound and fail closed the recipient lock; keep a retry's recipient (P-1)

  git add backend/app/services/operator_notification.py \
    backend/app/services/outbound_safety_gate.py \
    docs/launch/LAUNCH_TRUTH_ROADMAP.md \
    docs/Launch/2026-11-06_RIDECHECK_CRM_PRELAUNCH-P1-OPERATOR-NOTIFICATION-RECIPIENT-LOCK_CLOSEOUT_001.md \
    tests/test_l4_7w4_f3_booking_price_completeness.py \
    tests/test_m19_r1_2_durability.py \
    tests/test_m19_r1_2_pg_concurrency.py \
    tests/test_m19_r1_2_pg_integration.py \
    tests/test_p1_recipient_lock_pg.py \
    tests/test_w1f_operator_signal.py

  R1: this path list is still correct, since R1 changed only files already in it. The independent
  audit in docs/Launch/ is also untracked. It is not in the list; add it if the owner wants it in
  the same commit.

================================================================================
14. VERDICT
================================================================================
Original: READY FOR INDEPENDENT REVIEW
Independent review: CORRECTION REQUIRED
R1: READY FOR FOCUSED INDEPENDENT RE-REVIEW

================================================================================
15. R1 CORRECTIONS AFTER INDEPENDENT REVIEW
================================================================================
  Source: 2026-11-06_..._INDEPENDENT-REVIEW_AUDIT_001.md, corrections C1 to C5. Audit not edited.
  Scope: the roadmap, tests/test_p1_recipient_lock_pg.py and this closeout. No production change.
  Evidence: /opt/ridecheck-builds/crm-p1-notification-lock-r1-20261106T221325Z

  C1  Roadmap wording: section 11.
  C2  N18 runs the real claim_next on PostgreSQL. A cursor hook runs claimant B to completion, on
      A's thread, immediately before A's UPDATE, so no timing decides it. It asserts B holds the
      claim, A gets None, A's UPDATE matched 0 rows, the row is SENDING with attempts 1 and B's
      token, nothing was sent, and that B's completion gives one send: SENT, attempts 1.
  C3  B01 to B03 pin 4000, 6000 and 10000 with no database, so they run everywhere. L15 reads the
      values PostgreSQL reports inside a real, unpatched gate transaction.
  C5  Flag order recorded in the roadmap beside P-1. Documented only.

  C4, the audit's section 14. Old wording kept here:
    1 "22 valid, 22 killed" was not accurate at the time: O05 was intermittent. Section 7.
    2 "cand2 and final differ in one line": two hunks, the import and set_floor. Verified by diff.
    3 "_send_flow and the CE do not wrap attempt()": _send_flow does not, but its caller does
      (booking_slot_recovery.py:949) and records GATE_FAILED_*. Verified in the source.
    4 "CE: an OutboundBlockedError on the usual non-kill-switch route": incomplete. The audit says
      about 20 sites. My count in conversation_engine.py: 24 `except OutboundBlockedError` handlers;
      5 return a literal *_kill_switch detail and 11 return outbound_disabled, so 16 name the switch
      or its effect. The other 8 are generic, re-raise or unrelated. Read statically; the handler
      count is exact, the grouping is mine.
    5 "The window is the send itself": understated. It opens at the notification summary read and
      closes when Meta accepts the send.
    6 "94 security events": removed. The count varies with how many attempts run before refusal
      (94 in my run, 86 in the review's).
    7 Added: pg_terminate_backend gives a raw exception, not the typed refusal, and every caller
      still produces no send. From the review's probes; not re-probed in R1.

  Focused verification, disposable postgres:16 (p1r1-*), final test bytes c4bfe4ce2a9ad53a:
    P-1 file + M19 concurrency    3 fresh databases, 48 passed each
    M19, all three suites         36 of 36
    N18 control / O05             section 7
    P-1 file on SQLite            3 passed, 34 skipped
    Full SQLite and PostgreSQL comparisons were NOT rerun, as instructed.

  A first R1 run is discarded. N18 passed alone but failed in all three full-suite controls, at its
  last line: gate row pending, not sent. Cause, shown by the log: the fake Template numbers wamids
  from 1 per instance and wa_message_id is unique, so mark_sent hit a UniqueViolation on the fake's
  number. A fault in my test, not in the product. N18 now uses a fake with unique wamids, and every
  result above is from the rerun. N18's failure also appeared in every mutant of that first run,
  which is why those kills were not used. Kept in run1_invalid_harness_fault/.

  Memory: gate 1536 MiB before each run, one heavy job, watchdog 500 MiB. Lowest 1957 MiB; lowest
  at a start 2158 MiB. No abort. Disposables removed by exact name: 0 p1r1-* remain.
