PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-P1-OPERATOR-NOTIFICATION-RECIPIENT-LOCK-R1-FOCUSED-INDEPENDENT-REVIEW

Date: 2026-11-06
Base: 163b37ecf3a70064f7b3e0b4715fd2ecf92843ca
Candidate: /opt/ridecheck-crm-p1-notification-lock, branch
fix/prelaunch-p1-operator-notification-recipient-lock, uncommitted, index empty.
Tree digest, this audit and the original audit excluded:
594f5bac2f6aba2f537f77782bc4e6a4e6f74158f1e1f9f8847887d0d27297c7
Closeout reviewed: sha256 prefix 142f6fbc85407e1d

================================================================================
0. VERDICT
================================================================================

  READY FOR CONTROLLED COMMIT

  C1 to C5 are closed. Production code is byte-identical to the independently reviewed candidate.
  N18 is deterministic and behaves as required. No new unexplained finding.

  This verdict permits a controlled commit only. P-1 stays NOT PASSED: it also needs the commit
  and the corrected code in the deployed image. No flag may change on this verdict.

  Two non-blocking closeout notes are in section 5. Neither is a correction.

================================================================================
1. SCOPE
================================================================================

  Compared with my frozen snapshot of the reviewed candidate, exactly three files differ:
    docs/launch/LAUNCH_TRUTH_ROADMAP.md                   073e448bd1ffe2e1
    tests/test_p1_recipient_lock_pg.py                    c4bfe4ce2a9ad53a
    the closeout                                          142f6fbc85407e1d

  Production, unchanged:
    backend/app/services/operator_notification.py         55d6b9624b66eb04
    backend/app/services/outbound_safety_gate.py          a69e0f4866b5acae

  The other 5 changed test files keep their reviewed hashes. HEAD 163b37e, index empty.

  Original audit: byte-identical, sha256 1a03cfdaf5f1411f.... The R1 pre-change manifest, taken
  before R1 began, records that same hash, and the file's modification time predates R1.

================================================================================
2. C1 — ROADMAP: CLOSED
================================================================================

  Required sentence present, verbatim, in the "Not allowed before P-1" item.
  P-1 NOT PASSED: tracker row unchanged.
  Commit, push and deployment preparation allowed: unchanged.
  The pass condition also now names "committed".

  The roadmap diff since review is one hunk, inside the P-1 entry: that item, the pass condition
  and the new flag-order subsection. No other decision changed.

================================================================================
3. C2 — DETERMINISTIC CLAIM RACE: CLOSED
================================================================================

  Mechanism
    N18 calls the real ON.claim_next on PostgreSQL. A before_cursor_execute hook on A's engine
    fires on A's UPDATE and runs B's complete claim_next, synchronously, on a separate engine, so
    the hook cannot re-enter. The test has no sleep and no thread. The window cannot silently
    miss: if the rival never ran, assertIsNotNone fails.

  Sequence, proved from PostgreSQL's own statement log (log_statement=all), not only the test:
    Step  Control, backend pid                             O05 mutant, backend pid
    1     1047 SELECT id ... (A's candidates)              287 same
    2     1050 SELECT, UPDATE ... AND status = ..., COMMIT 289 UPDATE without status, COMMIT
    3     1048 UPDATE ... AND status = ...                 288 UPDATE without status
    A's first and second statements use different pids because the session returned its connection
    at ROLLBACK and checked out another. It is the same Session.

  Remaining steps, from assertions that passed:
    3  A's rowcount is 0.
    4  A's claim is None.
    5  Row is SENDING, attempts 1, B's token, nothing sent, no gate row.
    6  B completes: one send, SENT, attempts 1, gate row sent. A later pass adds nothing.

  An extra UPDATE after B's record in the control trace was checked: it is hold_lost_senders from
  the test's final run_pass, which matches expired leases only.

  Runs, one at a time, fresh database each, final test bytes:
    Unmodified N18    6 of 6 passed, plus 1 traced: 7 of 7.
    O05, N18 alone    6 of 6 failed, plus 1 traced: 7 of 7.
    O05, whole file   2 of 2 failed N18, and N01 also failed both times.
    Every O05 failure is at assertIsNone(loser, "A claimed a row the rival already held"), with a
    Claim at attempt 2.

  With the closeout's evidence (10 and 10, read and confirmed), no O05 run has survived N18.

================================================================================
4. C3 — TIMEOUT VALUES: CLOSED
================================================================================

  B01 pins 4000. B02 pins 6000. B03 pins the sum at 10000 and greater than the lock bound. These
  need no database. L15 patches nothing and reads pg_settings from inside the gate's own
  transaction, through _check_flood, while the lock is held: lock_timeout 4000, statement_timeout
  10000.

  Mutants on the final file, one at a time, fresh database:
    Mutant                        Killed by        Observed
    X05 lock 400000               B01 B03 L15      400000 != 4000; server read 400000/406000
    X06 margin 600000             B02 B03 L15      600000 != 6000; server read 4000/604000
    X14 statement = lock          L14 L15          server read 4000/4000
    X15 statement = margin        L14 L15          server read 4000/6000
    X16 constants swapped         B01 B02 L15      server read 6000/10000
    X17 rowcount ignored          N01 N18          second claim returned

  All match the closeout, except that N01 also failed under X17 here. N01 is race-based, so that
  extra failure is expected and N18 remains the deterministic kill. X14 to X17 were rebuilt by me,
  not taken from R1's evidence.

================================================================================
5. C4 — CLOSEOUT: CLOSED
================================================================================

  The seven points
    1  O05: section 7 and section 15 state the former intermittent kill and that "22 killed" was
       not accurate then. N18 replaces it. Confirmed.
    2  Two hunks, an import and set_floor. Confirmed.
    3  _send_flow does not wrap attempt(); booking_slot_recovery.py:949 does, recording GATE_FAILED_*.
       Confirmed in source.
    4  24 handlers, 5 literal *_kill_switch, 11 outbound_disabled. Recounted independently: same.
    5  Window opens at the summary read. Corrected in sections 12 and 15.
    6  Fixed count removed; 94 and 86 both recorded as variable.
    7  pg_terminate_backend raw exception, no send. Stated in sections 4 and 15, credited as
       coming from the review's probes and not re-probed.

  Figures
    Hashes        All R1 and unchanged hashes in section 5 reproduced.
    Tree digests  4888607b... reproduced from manifest.pre, 0d17b5a5... from manifest.post and
                  from the worktree now. b0913d7f... reproduced from manifest.pre. These exclude
                  every file matching *INDEPENDENT-REVIEW_AUDIT_001.md, the method of the review's
                  fingerprint. The worktree equals manifest.post file for file.
    Test counts   37 cases = 32 + 5. P-1 + M19 concurrency 48 x 3, M19 36 of 36, SQLite file
                  3 passed and 34 skipped: all reproduced.
    Lines         815 lines, +98 -0 against reviewed; roadmap +55 -0 against base. Confirmed.
    Mutation      Section 7 matches R1's results file and my reruns. Its limit is stated honestly:
                  21 of the 22 were not re-executed. Since R1 removed no test line, those kills
                  stand.
    R1 scope      Confirmed.
    Memory        Lowest 1957 MiB, lowest at start 2158 MiB, 0 aborts: matches memory.log.
    Commit paths  The 10 listed are exactly the 8 modified files, the new test and the closeout.

  Disclosed first R1 run: the discarded harness fault is real and retained. OwnWamid is the fix,
  the same artifact my original review met.

  Non-blocking notes
    N-1  Section 0, R-3 still says "Owner should confirm". The owner has since accepted the retry
         policy, in the review brief. A historic pointer, not false.
    N-2  Section 12 item 8 says 25 COMMON failures, the 15-file Run B figure. The full inventory
         figure is 34, which section 9 already gives.

================================================================================
6. C5 — FLAG ORDER: CLOSED
================================================================================

  All required points are recorded in the roadmap:
    Enablement   image holds the reviewed, committed P-1; template, token, destination and floor
                 valid; both flags in the same controlled container recreate; operator flag never
                 true while OUTBOUND_ENABLED is false; if two steps are unavoidable,
                 OUTBOUND_ENABLED first, with manual follow-up for gap bookings.
    Disablement  operator flag false, then OUTBOUND_ENABLED false.
    Emergency    authorised immediately; pending notices may become FAILED and visible; manual
                 follow-up; no automatic resend assumed.

  It agrees with the original audit's section 9. It adds that settings are cached, so a container
  recreate is needed. Documentation only: live OUTBOUND_ENABLED and
  OPERATOR_WHATSAPP_NOTIFY_ENABLED are both false, unchanged.

================================================================================
7. EXECUTION
================================================================================

  Disposable postgres:16, internal network, tmpfs, Alembic head 20261104_vehicle_identity, frozen
  copy of the final tree, read-only.

    P-1 file + M19 concurrency   3 fresh databases: 48 passed each
    M19, all three suites        36 of 36
    N18 and O05                  section 3
    X05, X06, X14 to X17         section 4
    B01 to B03                   pass, and on SQLite: 3 passed, 34 skipped
    L15                          pass; section 4

  Full SQLite and PostgreSQL inventories were not rerun, as authorised, because production is
  unchanged.

  Deviation: my first X05 to X17 runs and first O05 trace are INVALID and not counted. I named
  those databases with uppercase letters, which PostgreSQL folded to lowercase, so they failed to
  connect. They looked like kills but were not. Discarded, renamed, rerun; no "does not exist" in
  the valid outputs. The N18, O05, final and M19 runs used valid names and are unaffected.

  Memory: 2 gated heavy jobs, one at a time. Starts 2079 and 1989 MiB. Lowest 1690 MiB. No abort.
  Live containers never stopped.

================================================================================
8. EXIT VERIFICATION
================================================================================

  Exit fingerprint, taken after this audit was written, is identical to the R1 entry fingerprint
  across refs, stash, all other worktrees, the merge worktree manifest, live containers, compose
  and env files, live flags, TARGET_v7.7, candidate v7.8, live schema heads (crm_test
  20261103_slot_recovery_job) and the candidate tree.

  No p1rev container or network remains. The original audit was not edited.

  Nothing staged, committed, pushed, merged, built, deployed or migrated. No flag changed, no Flow
  published, no service restarted, Meta not contacted, no message sent.

  For the commit: the two audits are untracked and not in the closeout's path list. Adding them is
  the owner's choice.

  VERDICT: READY FOR CONTROLLED COMMIT
