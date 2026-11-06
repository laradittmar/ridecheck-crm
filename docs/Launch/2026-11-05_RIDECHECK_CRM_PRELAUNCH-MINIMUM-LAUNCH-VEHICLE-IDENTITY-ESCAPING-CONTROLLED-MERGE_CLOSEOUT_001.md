PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-ESCAPING-CONTROLLED-MERGE

Date: 2026-11-05 (work ended 2026-11-06 00:35 local). Nothing staged, committed, pushed, tagged,
stashed, built, deployed or migrated live. No flag changed, no Flow published, Meta not contacted,
no message sent, no live database queried. All test values are synthetic.

================================================================================
0. VERDICT
================================================================================
READY FOR INDEPENDENT REVIEW

  This is not READY FOR DEPLOYMENT, PUBLICATION or ENABLEMENT.

    Resolved kanban_view.py   e7a9e334f92d14d4d28ca743aa49259d780658bcaa0593a94109ae4411bee654, exact
    Full SQLite vs 198ed4d    failing 71 vs 71, NEW 0, GONE 0, 0 message differences
    PostgreSQL, 34 files      failing 62 vs 62, NEW 0, GONE 0, no candidate-only regression
    Browser, 72 pages         0 executions, 0 live, 0 transient, 0 handlers (198ed4d: 1474 executions)
    Migration rehearsal       every expected result reproduced on the operational schema shape
    T-1                       10 tests, pass on SQLite and PostgreSQL; production unchanged for them
    Mutants                   52 valid: 48 killed behaviourally, 4 equivalent (dead code), 0 survived

  The reviewer should look at these first:
    C-1  Two of the five merge conflicts sit in DEAD CODE. `_day_slots` is never called, on 198ed4d
         or here. Four of the mutants the milestone asked for are therefore equivalent: only a
         source inventory sees them. The live day card renders elsewhere and IS proven (section 10).
         "Every applied mutant killed behaviourally" holds only with those four set aside.
    C-2  Two client-side sinks are killed by Chromium only. pytest can merely pin their source text.
         The broad browser proof that kills them lives in scratch, not in the tree.
    C-3  This is the first run ever of five gated files the earlier "28-file" inventory omitted.
         33 of the 62 PostgreSQL failures are theirs. All are inherited, identical on 198ed4d.
         They are stale tests, not regressions, but they were not known before today.

================================================================================
1. TARGET
================================================================================
  Worktree  /opt/ridecheck-crm-minimum-launch-merge
  Branch    integration/prelaunch-minimum-launch-merge
  Base      198ed4d6a7157230a616063094e2d50fb9623836 (HEAD unchanged)
  State     unstaged working tree, index empty, 45 paths with this closeout
  Neither the branch nor the directory existed before creation.

================================================================================
2. PRECONDITIONS AND SOURCE RECONCILIATION
================================================================================
  Both final audits and their closeouts were read. Both sources, recomputed before creating the
  target and again at the end:

  Source  Branch                                           HEAD     Index  Paths  kanban_view.py
  A       integration/prelaunch-vehicle-identity-recovery  198ed4d  empty  30     40084f99a196de18...
  B       fix/prelaunch-agenda-customer-data-escaping      198ed4d  empty  13     e555a7f41f8960a0...

  A's 16-digit hashes match every hash in its audit sections 1 and 10 and its R2A closeout section 6.
  B's 11 full hashes match its R2 closeout section 2; the R2 closeout itself matches its audit.
  Unchanged since audit: in each source the newest file is the final audit (A 18:31, B 13:19) and
  every other path predates it. A = the audit's 29 paths + the audit.
  lsof: no file in either source open for writing. One idle Claude session holds a cwd in A, with
  no write handle. Stash list empty.
  A holds one ignored file, backend/app/services/__pycache__/flow_contract.cpython-312.pyc. It was
  not carried and was left in place.

  Reconciliation of the final path set (computed, section 3):
    A's 30 paths: 29 byte-identical, kanban_view.py merged
    B's 13 paths: 12 byte-identical, kanban_view.py merged
    Shared by both candidates: backend/app/ui/kanban_view.py, and nothing else
    Added by this milestone: the two T-1 files and this closeout
  Excluded: scratch evidence, caches, logs, return-cutoff files. No migration other than
  20261104_vehicle_identity was added. No unrelated Launch document differs from 198ed4d.

================================================================================
3. FINAL MERGED PATH MANIFEST
================================================================================
  status, sha256 (16), origin, path. This closeout cannot state its own hash.

  M  5d48489fe20b0167  A    backend/app/api/revisions.py
  M  5b167b3238c8b332  A    backend/app/models.py
  M  8eb9756d16019053  A    backend/app/routes/flow_data_exchange.py
  M  3989054fd7331e95  A    backend/app/schemas/revisions.py
  M  81efb3479e393b58  A    backend/app/services/booking_flow_details.py
  M  43eba1d9233fd436  A    backend/app/services/booking_flow_service.py
  M  a5fba45c644c1dee  A    backend/app/services/flow_contract.py
  M  51e8f7a52f663e12  A    backend/app/services/lead_booking_details.py
  M  0d26b939fea7e5b0  A    backend/app/services/schema_preflight.py
  M  aa77483b49ed4475  A    backend/app/settings.py
  M  f57af9c56d4edacf  B    backend/app/ui/control_view.py
  M  0df48cf5d8da4f2a  A    backend/app/ui/kanban_actions.py
  M  e7a9e334f92d14d4  A+B  backend/app/ui/kanban_view.py
  M  5c31eb26f28c2146  B    backend/app/ui/whatsapp_ui.py
  M  24eb711e9e105584  A    docs/operations/BOOKING_FLOW_PREFILL_CONSTRAINT.md
  M  0a1888c8eea830fb  A    meta/flows/booking/README.md
  M  f7be592b05fe33a6  A    tests/test_convergence_p4_r1.py
  M  3c66a7ca17185720  A    tests/test_minimum_launch.py
  M  e4b51410bc0d7584  A    tests/test_minimum_launch_pg.py
  M  ea67a706acb72a9b  A    tests/test_w1f_w01_r2_outstanding_question.py
  ?? 267636fcb02c1d50  A    backend/app/services/vehicle_identity.py
  ?? 2667ae8c682c595e  A    backend/migrations/versions/20261104_vehicle_identity.py
  ?? 864a75e25628deee  B    docs/Launch/2026-11-04_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-AGENDA-CUSTOMER-DATA-ESCAPING_CLOSEOUT_001.md
  ?? a3316e6735cb487a  B    docs/Launch/2026-11-04_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-OPERATOR-PAGE-DYNAMIC-HTML-ESCAPING-R1_CLOSEOUT_001.md
  ?? d3546a67f107aa48  A    docs/Launch/2026-11-04_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE_CLOSEOUT_001.md
  ?? 32a95640c1108628  B    docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-OPERATOR-PAGE-DYNAMIC-HTML-ESCAPING-R2-INDEPENDENT-REVIEW_AUDIT_001.md
  ?? 8ea15c9aab733b21  B    docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-OPERATOR-PAGE-DYNAMIC-HTML-ESCAPING-R2_CLOSEOUT_001.md
  ?? 2b56d1902af032dc  A    docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R1-CLOSURE_CLOSEOUT_001.md
  ?? 176acd18bab7c52d  A    docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R1-INDEPENDENT-REVIEW_AUDIT_001.md
  ?? fff00a0509474964  A    docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R2-CARD-SCOPE-CORRECTION_CLOSEOUT_001.md
  ?? c839573a8d18d1e2  A    docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R2A-CAR-CONTEXT-CARD-SCOPE-CORRECTION_CLOSEOUT_001.md
  ?? 5ce52c768298d5d5  A    docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R2A-FOCUSED-INDEPENDENT-REVIEW_AUDIT_001.md
  ?? 2102a8ec5f01be41  A    meta/flows/booking/CANDIDATE_v7.8-vehicle-identity-prefill.json
  ?? dfa84cd267607655  A    meta/flows/booking/make_v78_vehicle_prefill_candidate.py
  ?? 22df3b57972d004a  B    tests/escaping_browser_proof/driver.js
  ?? 005c5abd490d7c77  B    tests/escaping_browser_proof/run_proof.py
  ?? 5e923a96a4ad1854  B    tests/escaping_browser_proof/serve_hostile.py
  ?? f38e1b0c238d42b0  B    tests/test_agenda_customer_data_escaping.py
  ?? e5c96c79ee80043b  B    tests/test_operator_page_html_escaping.py
  ?? 495340484319773c  B    tests/test_operator_page_html_escaping_r2.py
  ?? e6d2606e4a2e3f43  A    tests/test_vehicle_identity_recovery.py
  ?? f202855c82b356ef  A    tests/test_vehicle_identity_recovery_pg.py
  ?? 93da71e13f1332f4  T-1  tests/test_vehicle_identity_t1_regression.py
  ?? e19a4a414d287ea3  T-1  tests/test_vehicle_identity_t1_regression_pg.py
  ?? (this file)          this  docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-ESCAPING-CONTROLLED-MERGE_CLOSEOUT_001.md

  Tree digest bbdd0fa1d28ac7ee, identical before and after the merged full run. No file but this
  closeout was written after it.

================================================================================
4. CONFLICT RESOLUTION
================================================================================
  git merge-file, vehicle as ours, 198ed4d as base, escaping as theirs: exactly 5 conflicts, all in
  render_calendar_page. Resolved by a script that asserts the shape of each conflict.

    1  _aveh / _aaddr   BOTH: the vehicle join with dominio and kilometraje, and `_aaddr` with
                        `_esc_txt(r.direccion_texto)`
    2  calApptName      escaping side (in `_day_slots`, dead: C-1)
    3  calApptName      escaping side (in `_day_slots`, dead: C-1)
    4  calMonthApptName escaping side
    5  calWeekApptName  escaping side

  Resolved independently from the rule, not copied from a retained file. First attempt matched:
    sha256  e7a9e334f92d14d4d28ca743aa49259d780658bcaa0593a94109ae4411bee654

  Required property                         Result
  parses                                    yes
  vehicle join exactly once                 yes, 1 and 1
  dominio and kilometraje retained          yes
  four escaped name sinks, none raw         4 and 0
  escaped address, none raw                 2 and 0
  accepted escaping corrections retained    72 escaping tests, browser proof
  render_table_page deleted                 yes; render_revisions_table_page present
  conflict markers                          0

================================================================================
5. T-1 TESTS ADDED
================================================================================
  New files, so the accepted vehicle test bytes are untouched. No production file changed for them.
    tests/test_vehicle_identity_t1_regression.py      10 tests
    tests/test_vehicle_identity_t1_regression_pg.py   the same 10 on PostgreSQL, VEHICLE_PG_URL

  Required behaviour                                Test           Probe     First kill observed
  Latest qualifying booking for the displayed car   t1_01          b03       M05b
  (added) later cancelled does not displace it      t1_02          none      M05d
  Cancelled does not count as committed             t1_03          b01
  Completed counts as committed                     t1_04          b02       M05e
  Candidate-less fails closed with a focus          t1_05          r1a       M05, M05c
  Candidate-less shown with no focus                t1_06          r1c       M12
  Own draft used, not the unrelated booking         t1_07          r1a2
  Another car's draft shows that car only           t1_08          r1b
  Foreign-thread focus supplies nothing             t1_09          c07 tail  M11
  (added) foreign BOOKED focus supplies nothing     t1_10          none

  First run: 10 passed. All 7 mutants above were killed by the T-1 file ALONE. The column shows the
  first failure only: runs stop at it, so a blank does not mean that test would pass under a mutant.
  M05b, M05c, M05d, M05e and M11 are the five suite survivors of the R2A review, so T-1 is closed.

================================================================================
6. LIGHTWEIGHT MERGE CHECKS
================================================================================
  Compilation, 30 changed Python files      pass
  Import, app.main through the harness      pass, 126 routes, same as 198ed4d
  git diff --check, tracked and untracked   clean
  Conflict-marker scan                      0
  Secret and live-identifier scan           no token, key, DSN or private-key shape; 24 sensitive
                                            backend values compared in memory, not printed: 0 in
                                            added content
  Vehicle focused SQLite                    148 passed
  test_minimum_launch                       41 passed
  Three escaping suites                     72 passed (26 + 27 + 19), 797 subtests with the above
  T-1                                       10 passed
  All of the above in one run               271 passed, 797 subtests
  Generator from v7.7                       byte-identical to the candidate
  Generator from a wrong baseline           refused, rc 2, nothing written
  Validator: plain, pinned graph            rc 0
  Validator: --precedent v7.7               0 routing, 0 error, 0 render, 7 unproven
  Controls                                  v7.7 clean; known-bad v7.6 rc 1; compare_flows rc 0

  A bare `import app.main` outside the test harness fails on a JSONB compile under SQLite. That is
  the import order, not the merge: through the harness both trees pass.

  Confirmed
    Optional mileage and plate      present; g-, k- and p-suites; E1.7 and E1.8 killed
    Recovery prefill                r-suite, Q_Rebinding, PostgreSQL PG_Recovery, PG_Rebinding
    SUMMARY contract                E2_Summary on both engines; M08, M08b killed
    Dynamic HTML sinks              section 10, section 11
    No stored value pre-escaped     AE-20, OP-25, OP-26, R2-16 pass; E1.6 killed
    TARGET_v7.7                     4ce27122a55f3efd4fbae117d620abf34024c5a86343bb23ce878182ba2bbd9d
    CANDIDATE_v7.8                  2102a8ec5f01be41c6671a1d9a3c59cf1f79fbad55354bf623f1db1cd3348354
    Live flags, by name only        BOOKING_FLOW_DETAILS_PREFILL_ENABLED unset (default false);
                                    SMART_BOOKING_CE, OPERATOR_WHATSAPP_NOTIFY, EVIDENCE_ADMISSION,
                                    OUTSTANDING_QUESTION, LOCALITY_RESOLUTION_SAFETY, OUTBOUND,
                                    CONVERSATION_ENGINE_DIRECT_WEBHOOK all false

================================================================================
7. FULL SQLITE COMPARISON
================================================================================
  ridecheck-crm-test:l4.7b2, TZ America/Argentina/Buenos_Aires, DATABASE_URL=sqlite://, read-only
  trees, --network none, serial, same evening, no concurrent heavy job. The tree was hashed around
  the merged run: identical.

  Side     failed  passed  skipped  errors  cases   Duration
  198ed4d      62    7256      677       9   8003   6m54
  merged       62    7486      752       9   8308   7m53

  Failing identities   71 vs 71, NEW 0, GONE 0
  Failure messages     0 differ
  Skip messages        0 differ
  Skipped identities   NEW 75, GONE 0
  Cases                added 305, removed 0

  Every difference, attributed:
    +305 cases    vehicle 148 + vehicle PG 65 + escaping 72 + T-1 10 + T-1 PG 10
    +230 passed   148 + 72 + 10 = 230; 7256 + 230 = 7486
    +75 skipped   the two PostgreSQL files without their URLs; 677 + 75 = 752
    11 tracebacks differ in body only: 22 + 22 lines, kanban_view.py line numbers in
                  render_calendar_page and _operational_day_view. Nothing else.

  The 198ed4d failing set equals the retained R2A baseline: 71 vs 71, symmetric difference 0.

================================================================================
8. POSTGRESQL-GATED INVENTORY
================================================================================
  Disposable postgres:16 (16.13), one server per side, tmpfs, --internal network with no other
  peer. Each file in its own process on freshly cloned databases. Chain built from this tree's own
  Alembic files, on the pre-SB1 base (models of commit c797f73).

  Inventory method: static scan of every gating variable, then the skip list of the full SQLite
  run as an independent check. Both agree: 38 modules skip. 34 are PostgreSQL files, all run. 3
  need git (below). 1 needs the live database.

  Gating variables provisioned:
    CONV_P2_PG_URL CONV_P2_PG_MIG_URL CONV_P3_PG_URL CONV_P4_MIG_URL CONV_P4R1_PARENT_URL
    CONV_P4R1_APPFIRST_URL CONV_P4R1_LEGACY_URL CONV_P4R1_EMPTY_URL CONV_SSA_PARENT_URL
    CONV_SSC_PGBIN G37R_PG_URL G37R_PG_ALEMBIC_URL RV03_PG_URL G7P_PG_URL CLARIFICATION_PG_URL
    MINLAUNCH_PG_URL MINLAUNCH_PG_MIG_URL MINLAUNCH_PG_R1_MIG_URL MINLAUNCH_PG_STAMP_URL
    R2_PG_URL R2_PG_MIG_URL R3_PG_URL R3_PG_MIG_URL R4_PG_URL R5_PG_URL R7_PG_URL SB1_PG_URL
    SB1_PG_BASE_URL SB1_PG_EXT_URL SB2A_PG_URL SB2A_PG_MIG_URL SB2A_PG_STAMP_URL SB2B_PG_URL
    SB2B_PG_CLEAN_URL SB2B_PG_MIG_URL W1F_PG_URL W1F_NOTIFY_PG_URL W1F_MIG_URL VEHICLE_PG_URL
    VEHICLE_PG_MIG_URL DATABASE_URL (postgresql) TEST_DATABASE_URL

  File, by junit case, merged                                  pass  fail  198ed4d
  test_convergence_p2_authority_pg                               34     3  same
  test_convergence_p3_r1_ack_pg                                  15     0  same
  test_convergence_p3_race_pg                                    20     0  same
  test_convergence_p4_pg                                         11     1  same
  test_convergence_p4_r1_pg                                      18     7  same
  test_convergence_p4_strict_schema_correction_pg                16     7  same
  test_convergence_p4_strict_schema_pg                           25     9  same
  test_l4_2_clean_slate             (no _pg, clone named crm_test) 17   2  same
  test_l4_7w5_g37r_rv03_tester_reset_pg                          26     1  same
  test_l4_7w5_g3_7f_clarification_lifecycle          (no _pg)    61     0  same
  test_l4_7w5_g3_7p_offer_authority_pg                          132     0  same
  test_l4_7w5_g3_7r_booking_projection_pg                         8     0  same
  test_l4_7w5_g3_7r_r2_refusal_bounds_pg                          9     0  same
  test_l4_7w5_g3_7r_r3_cycle_reset_bound_pg                      21     0  same
  test_m19_r1_2_durability                           (no _pg)     0    10  same
  test_m19_r1_2_pg_concurrency                       (no _pg)     0    11  same
  test_m19_r1_2_pg_integration                       (no _pg)     6     9  same
  test_m19_r1_3e_reset_rehearsal                     (no _pg)    33     1  same
  test_minimum_launch_pg                                         19     0  same
  test_prewild_pw1_bounded_runtime_pg                            17     0  same
  test_sb1_booking_representation_pg                             31     0  same
  test_sb2a_booking_authority_pg                                 25     0  same
  test_sb2a_r2_hardening_pg                                      18     0  same
  test_sb2a_r3_typed_plans_pg                                    12     0  same
  test_sb2a_r4_window_scope_pg                                   14     0  same
  test_sb2a_r5_lockwait_pg                                        5     0  same
  test_sb2a_r7_temporal_pg                                        8     0  same
  test_sb2b_option_b_pg                                          48     0  same
  test_vehicle_identity_recovery_pg                              65     0  new
  test_vehicle_identity_t1_regression_pg                         10     0  new
  test_w1f_o1_ce_sla_successor_pg                                17     0  same
  test_w1f_operator_notification_pg                               4     1  same
  test_w1f_unanswered_alert_pg                                    4     0  same
  test_w1f_w01_r2_outstanding_question               (no _pg)   150     0  same

  Files 32 vs 34. Cases 886 vs 961, added 75, removed 0. Skipped 0 on either side.
  Failing identities 62 vs 62, NEW 0, GONE 0. CANDIDATE-ONLY REGRESSIONS: 0.

  Message differences: 29, all read in full. 27 name the head revision (20261104 where 198ed4d
  says 20261103). 2 are test_m19 concurrency failures listing message ids in a racing order. After
  normalising head name, line number, id and time: 0 bodies differ.

  INHERITED FAILURES, identical on 198ed4d, none assessed as candidate behaviour:
    27  Test pins an old head; any later head fails them. 26 expect 20261022_operator_notifications:
        p2 3, p4 1, p4_r1 5, strict correction 7, strict 9, w1f 1. m19 integration 1 expects
        20260629_recipient_lock_rolling_window.
    29  test_m19_r1_2_*: 29 of the 30 call gate.attempt() without path_id, which the gate has refused
        since M21.3 (BLOCKED_UNAUTHORIZED_PATH). The tests are stale. The refusal is correct.
    2   p4_r1: a second-order failure of the same stale pin (`[] != [appfirst, upgraded]`, env rc).
    2   clean_slate: empty CLOSED_BETA_ALLOWED_WA_IDS, and a preserved-events count on a clone. They
        assert the live environment.
    1   rv03: `whatsapp_outstanding_questions`, `whatsapp_thread_evidence` not reset.
    1   r1_3e: `Read-only file system: /work/tests/__pycache__`. Harness artefact of the :ro mount.

  Not run, with the reason:
    test_l4_wild01_repro::test_a4 (1 case)          hardcoded localhost/crm_test and real pricing
                 rows. UNPROVISIONED: cannot run without the live database. Skips in the sealed run.
    test_l4_2_clean_slate zero-state tests          RAN, but VACUOUS: 'no rows for the tester' on an
                 empty clone proves nothing about live crm_test.
    4 cases needing git in the test image           not PostgreSQL. test_l4_7w5_g3_1_hybrid_site_trace,
                 test_l4_7w5_trace_row_semantics, _r2.
    tests/cleanup_smoketest_fixtures, reset_closed_beta_scenario, smoke_fallback_flows,
    smoke_website_flow                              scripts, not collected tests; never pointed at
                 anything. reset_closed_beta_scenario ran only as r1_3e's subprocess.

  Particular proofs, on disposable PostgreSQL 16, all pass
    Vehicle migration and preflight       PG_Migration 13, section 9
    Recovery job                          PG_Recovery 6, PG_A_Recovery 2, PG_R1_Recovery 4
    Race-loser persistence and prefill    PG_Rebinding 11, PG_Recovery
    Duplicate-lead identity               PG_D_ConcurrentLeads 4
    Operator notification                 Q_NoticePg 4, PG_R2_DispatchOff 2; its M_MigrationPg is the
                                          inherited head pin
    Per-attempt evidence isolation        PG_Rebinding 11
    T-1                                   PG_T1_* 10
    No cross thread, candidate or cycle   PG_CarContext 16, PG_CardScope 14, PG_T1_ForeignThreadFocus 2

================================================================================
9. MIGRATION REHEARSAL
================================================================================
  Disposable postgres:16 on tmpfs. The retained operational crm_test schema dump (dated 2026-11-03,
  version_num varchar(128), sha256 2e06faf5ba07c480...), restored rc 0, stamped at
  20261027_w1f_conversation_memory, chain to parent, 5000 seeded revisions.

  Required                                 Result
  Upgrade to merged head                   CREATED, version 20261104_vehicle_identity
  Nullable kilometraje and dominio         integer, varchar(16), nullable, no default, no index
  No backfill                              0/0 of 5000 populated
  Data unchanged                           row hash identical; catalog diff is exactly two columns
  Repeat upgrade                           no-op
  Stamped back, empty / with values        ADOPTED_EMPTY_CANONICAL / VERIFIED_CANONICAL
  Limits                                   2147483647 and 16 chars accepted; one more refused
  Malformed, refused before any change     bigint, text, varchar(20), DEFAULT 0, NOT NULL, UNIQUE,
                                           CHECK, foreign index, partial holding data: 9 of 9
  Foreign column on downgrade              refused, not dropped
  Clean downgrade                          DROPPED; catalog identical to before; hash identical
  Re-upgrade                               identical to the first upgrade
  Downgrade with populated fields          REFUSED, both fields and mileage alone
  Preflight, merged code                   head 20261104_vehicle_identity: 0 problems; below head: 1
  Preflight, 198ed4d code on upgraded db   1 problem: exact-version refusal, so the old image cannot
                                           restart at the new head
  198ed4d models on upgraded schema        read and insert work
  ORM vs database                          types match; only the two generated columns differ

  After the deliberate partial-adoption step the final shape differs from the first upgrade in
  column ORDER only. Expected: dominio was created first in that step.

  On the same operational shape: vehicle PG 65 passed, T-1 PG 10 passed, minimum launch PG 19 passed.

  A fresh live schema-only dump is still required immediately before the real migration.

================================================================================
10. BROWSER SECURITY PROOF
================================================================================
  Sealed: app in --network none on a Unix socket, SQLite in memory; proxy and Chromium in an empty
  network namespace. External connection attempted on each run: `Network is unreachable`.

  Supplied harness, unmodified, in tree
                 pages  no result  executions  img  script  handlers
    198ed4d         25          0         173  148      67       174
    merged          25          0           0    0       0         0

  Broad proof from the escaping review (every string and JSON column hostile, 203 columns, 825 rows,
  11 attacks), plus a new supplement: hostile FLOW_* claims through the real write_detail_rows, 297
  rows, so the "Datos de reserva" block renders. Without it that block's draft path was not reached.
                 pages  no result  exec  live  transient  handlers  iframes  js URLs
    198ed4d         72          3  1474  2540       1703      1929      570        0
    merged          72          3     0     0          0         0        0        0
    merged, comment mode  24    0     0     0          0         0        0        0

  Required: 0 executions, 0 live, 0 transient, 0 handlers, 0 executable script, iframe or
  javascript URL. MET. 198ed4d proves the detectors are not blind.
  Residual, all explained:
    3 no-result pages: FastAPI 422 JSON for a non-integer offer id, not HTML, on both trees.
    12 "script" hits: the zones-data block on /kanban and /table, type=application/json, on both.
    2 once-bad text nodes: raw trace JSON in a <pre> on /control/turn, where a backslash before a
    quote is correct. Same as the review.

  Covered, populated: agenda day, week, month (1171 day cards, 55 week rows, 44 month cards);
  /kanban (34 cards, 33 details blocks); /table; /profesionales; /agencias; /control (33 threads, 66
  messages, 25 turns); /whatsapp/thread; lead picker 34, forward picker 33; Cobro and edit modals.

  Exactly once: text 1149 good; attributes 409 good, 0 bad; lead and forward pickers 6 good, 0 bad.
  Vehicle in the block, exactly once: plate 2, seller 3, address 3, listing 2 (the marker rows);
  23 plate lines and 23 mileage lines rendered. revisions.dominio is hostile in the agenda and
  revision lines. Hostile mileage: `format_kilometraje` returns what it cannot parse, and it reached
  the DOM only as text. On PostgreSQL revisions.kilometraje is an integer and cannot hold text.

  Limits: CHECK-constrained columns carry legal values. Audio, image, reaction and quote bubbles
  were not driven in the browser. /login was out of scope. No provider contact was possible.

================================================================================
11. MUTATION RESULTS
================================================================================
  Copy of the merged tree, --network none. Controls before and after: 230 passed, 797 subtests.
  An import or collection error does not count. A kill by a source inventory did not count either:
  those mutants were rerun with test_ae15, test_r2_05b, test_r2_07 and test_r2_13 deselected, then
  through Chromium when pytest could not see them.

  Group (required by the milestone)               Mutants                   Result
  1 Vehicle escaping, live agenda sinks           D1 D2 E1.3 E1.4 E1.5      5 killed by pytest; D1 D2
                                                                            also in Chromium
    Vehicle sinks outside the agenda              E1.9-E1.12                4 killed
    Vehicle join, double escape                   E1.6 E1.7 E1.8            3 killed
  2 Raw customer/operator, live sinks             D3-D6, E2.3-E2.6, E2.9,   10 killed by pytest; D3-D6
                                                  E2.10                     also in Chromium
    Client-side, pytest cannot execute            E2.7 E2.8b                2 killed by Chromium
                                                                            (153 and 1 executions)
  3 Candidate and cycle scoping                   M02 M02b M03 M03b         4 killed
  4 Thread-wide car fallback                      M01 M06 M06b              3 killed
  5 Draft over committed                          M04 M04b                  2 killed
  6 SUMMARY keys                                  M08 M08b                  2 killed
  7 Repeated-fact attempt identity                M09 M09b M09c M09d        4 killed
  8 Duplicate evidence on retry                   M10 M10b                  2 killed
  9 T-1 status and recency, T-1 file alone        M05 M05b-e M11 M12        7 killed
  Dead code                                       E1.1 E1.2 E2.1 E2.2       4 EQUIVALENT, see C-1

  52 valid. 48 killed behaviourally: 46 by pytest, 2 by Chromium. 4 equivalent. 0 survived.
  D1-D6 are the live day card, Chromium 36 to 108 executions each. They were not in my first set.

  Corrections to my own work, disclosed:
    * E1.5 first left a dangling argument: a syntax error, reported INVALID by the harness, not
      counted. Rewritten; killed by test_ae01.
    * E2.8 first produced a JavaScript syntax error that disabled the page, so it "survived" for the
      wrong reason. Replaced by E2.8b, the exact 198ed4d line.
    * My first pass showed 45 of 46 killed. That figure overstated the result: six of those kills
      rested only on source pins. The rerun found C-1 and showed my set had missed the real day card.
    * My first PostgreSQL message comparer truncated to 400 characters and reported 28 false
      differences. Replaced by a full-text compare.
    * One command with an unnecessary cache `rm` was refused by the permission layer. Removed.

  Retained evidence validated for unchanged, non-overlapping code: vehicle 85 of 85 and review 26 of
  26 (their production files are byte-identical here, kanban_view.py aside); escaping 47 supplied,
  and 17 of 20 independent. Its 3 survivors are A-1, unchanged and not repeated here.

================================================================================
12. FINDINGS (none blocking in my judgement; the reviewer may disagree)
================================================================================
  C-1  LOW, DEAD CODE. `_day_slots` (kanban_view.py:3779) has one reference, its definition. Same on
       198ed4d. Conflicts 2 and 3, and two of the four vehicle sinks, are in it. They are correctly
       escaped and pinned by test_ae15, and unreachable, so no behaviour can kill a mutant there.
       Consequence: "all four escaped name sinks" means two live and two dead. The live day card
       escapes at its own sinks with html_lib.escape, proven by D1-D6. Not changed, because any edit
       breaks the required hash. Delete after launch.
       The escaping audit calls conflicts 2 and 3 a "slot card" without noting that it is unreachable.
  C-2  LOW, TEST GAP, class of A-1 and A-2. The picker and /control path sinks are pinned only as text
       in pytest. The broad browser proof is what kills them, and it and its supplement are in scratch.
       tests/escaping_browser_proof still omits /calendar. Recommended later: carry the broad proof
       into the tree.
  C-3  INFO. The "28-file" inventory omitted test_m19_r1_2_durability, _pg_concurrency, _pg_integration,
       _r1_3e_reset_rehearsal and test_l4_2_clean_slate. They ran here for the first time. 33
       failures, all inherited. The m19 suites should be updated for path_id: as they stand they
       prove nothing about recipient locks on PostgreSQL. That guarantee is unproven by them, today
       as on 198ed4d.
  C-4  INFO. 27 failures pin 20261022 as head and keep failing at every later head. Together they hide
       real upgrade assertions behind the pin. Inherited.

================================================================================
13. ACCEPTED LAUNCH DEBT (owner decisions, not corrected here)
================================================================================
  F-1  LOW. On a committed card Vehicle and Zona may reflect the current candidate while revision
       lines keep the booked snapshot. Cannot cross candidates or customers.
  F-2  LOW. In the repeated-name case the Flow default may use another name typed by the same
       customer. Editable, required, cannot cross customers.
  R-1  Candidate-less booking data fails closed when ownership cannot be proven. Now pinned: t1_05-08.
  R-2  Post-launch UI coherence. A card may hold a committed section and a current unconfirmed
       section, each internally coherent, never mixing values.
  Deferred, not merged: return-journey cutoff and its migration. Meta publication of v7.8.

================================================================================
14. META AND HANDSET PREREQUISITES STILL OPEN
================================================================================
  None proven here. Deferred to the controlled cutover milestone.
    1. Owner Ejecutar: Form init-values, TextInput pattern and helper-text, object details_prefill,
       the 7 unproven properties.
    2. Handset proof with a real slot conflict: empty and partial prefill, blank as "" or absent,
       what the customer sees when a pattern fails.
    3. N-3: switch ON with an open v7.7 session returns two undeclared keys.
    4. N-4: switch ON, a v7.7 form submitting a blank e-mail can clear a value it never displayed.
    5. Order of Flow and switch, and the return-cutoff migration re-parent.
  Meta was not contacted, so its own copy was not read: "unchanged" means nothing here altered it.

================================================================================
15. DEPLOYMENT AND ROLLBACK CONSTRAINTS
================================================================================
  * Migration before the new image. Merged code requires exactly 20261104_vehicle_identity.
  * The old image CANNOT restart at the new head: its preflight refuses (reproduced, section 9). Its
    models can still read and insert, so a running old container survives, but any restart fails.
  * Rollback of code means downgrading the schema first. Downgrade is refused once any plate or
    mileage is stored; those rows must be archived under separate authority. Plan the window so.
  * Fresh schema-only dump and rehearsal immediately before the real migration. The dump used here
    is dated 2026-11-03. crm_test is the authoritative operational database (owner, N-7).
  * Repeat the read-only markup scan before launch.
  * Escaping fixes protect nothing until deployed. Card reader changes are ungated and take effect
    on deploy with the switch off (R-3, F-2).
  * BOOKING_FLOW_DETAILS_PREFILL_ENABLED stays OFF until v7.8 is published.

================================================================================
16. SOURCE AND LIVE ISOLATION
================================================================================
  Fingerprint before and after, by my own script: IDENTICAL.
    Both sources         every file including ignored, byte-identical; manifests identical; HEAD
                         198ed4d; index empty; nothing staged or committed
    Target               HEAD 198ed4d, index empty, no stash, no ignored file
    Live containers      backend, postgres, n8n: same id, image, start time, restart count, env hash
    Configuration        docker-compose.yml and .env hashes identical
    Live databases       no connection made. Test containers had no route to them.
    Runtime flags        unchanged; section 6
    Published Flow       TARGET_v7.7 unchanged
    Other worktrees      all 8: HEAD, status and diff hashes identical; git refs identical apart
                         from the new branch
    Provider             none contacted. Every run was --network none, --internal or an empty netns.
    Disposables          mlm-pg, mlm-net, mlm-drv, mlm-sq, mlm-mut, mlm-al, mlm-srv-*: removed by
                         exact name or --rm; 0 mlm-* containers or networks remain
  The one read of the live backend was docker inspect of its environment, values not printed.

  Memory: serial, every heavy step gated at 1400 MiB available with no foreign harness, watchdog at
  500 MiB. Lowest during any run 1082 MiB (PostgreSQL). The watchdog never fired.

  Evidence, not part of the candidate:
    /tmp/claude-0/-opt/1c8d00e0-af1d-4572-8b2b-b4c245778afc/scratchpad

================================================================================
17. PROPOSED git add AND COMMIT (NOT EXECUTED)
================================================================================
  git add \
  backend/app/api/revisions.py \
  backend/app/models.py \
  backend/app/routes/flow_data_exchange.py \
  backend/app/schemas/revisions.py \
  backend/app/services/booking_flow_details.py \
  backend/app/services/booking_flow_service.py \
  backend/app/services/flow_contract.py \
  backend/app/services/lead_booking_details.py \
  backend/app/services/schema_preflight.py \
  backend/app/settings.py \
  backend/app/ui/control_view.py \
  backend/app/ui/kanban_actions.py \
  backend/app/ui/kanban_view.py \
  backend/app/ui/whatsapp_ui.py \
  docs/operations/BOOKING_FLOW_PREFILL_CONSTRAINT.md \
  meta/flows/booking/README.md \
  tests/test_convergence_p4_r1.py \
  tests/test_minimum_launch.py \
  tests/test_minimum_launch_pg.py \
  tests/test_w1f_w01_r2_outstanding_question.py \
  backend/app/services/vehicle_identity.py \
  backend/migrations/versions/20261104_vehicle_identity.py \
  docs/Launch/2026-11-04_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-AGENDA-CUSTOMER-DATA-ESCAPING_CLOSEOUT_001.md \
  docs/Launch/2026-11-04_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-OPERATOR-PAGE-DYNAMIC-HTML-ESCAPING-R1_CLOSEOUT_001.md \
  docs/Launch/2026-11-04_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE_CLOSEOUT_001.md \
  docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-OPERATOR-PAGE-DYNAMIC-HTML-ESCAPING-R2-INDEPENDENT-REVIEW_AUDIT_001.md \
  docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-OPERATOR-PAGE-DYNAMIC-HTML-ESCAPING-R2_CLOSEOUT_001.md \
  docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R1-CLOSURE_CLOSEOUT_001.md \
  docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R1-INDEPENDENT-REVIEW_AUDIT_001.md \
  docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R2-CARD-SCOPE-CORRECTION_CLOSEOUT_001.md \
  docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R2A-CAR-CONTEXT-CARD-SCOPE-CORRECTION_CLOSEOUT_001.md \
  docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R2A-FOCUSED-INDEPENDENT-REVIEW_AUDIT_001.md \
  meta/flows/booking/CANDIDATE_v7.8-vehicle-identity-prefill.json \
  meta/flows/booking/make_v78_vehicle_prefill_candidate.py \
  tests/escaping_browser_proof/driver.js \
  tests/escaping_browser_proof/run_proof.py \
  tests/escaping_browser_proof/serve_hostile.py \
  tests/test_agenda_customer_data_escaping.py \
  tests/test_operator_page_html_escaping.py \
  tests/test_operator_page_html_escaping_r2.py \
  tests/test_vehicle_identity_recovery.py \
  tests/test_vehicle_identity_recovery_pg.py \
  tests/test_vehicle_identity_t1_regression.py \
  tests/test_vehicle_identity_t1_regression_pg.py \
  docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-ESCAPING-CONTROLLED-MERGE_CLOSEOUT_001.md

  Commit message:

    feat(launch): merge vehicle identity, recovery prefill and operator-page escaping

    Compose the independently reviewed vehicle/recovery and operator-page escaping
    candidates on 198ed4d. Resolve the five render_calendar_page conflicts: keep the
    vehicle join with dominio and kilometraje together with the escaped address and
    customer-name sinks.

    Add T-1 regression tests for booking selection by status and recency, candidate-less
    bookings and foreign-thread focus, on SQLite and PostgreSQL.

    Adds migration 20261104_vehicle_identity. The old image cannot restart at this head.
    BOOKING_FLOW_DETAILS_PREFILL_ENABLED stays off until the v7.8 Flow is published.

    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

================================================================================
18. RECOMMENDED NEXT MILESTONE
================================================================================
  PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-ESCAPING-CONTROLLED-MERGE-INDEPENDENT-REVIEW

  Scope: independently verify the manifest, the resolved hash, the T-1 tests and the comparisons on
  this unstaged tree. Decide specifically whether C-1's four equivalent mutants and C-2's
  Chromium-only kills satisfy "killed by behaviourally relevant tests". No commit, build,
  deployment, migration, flag change or publication until that verdict.

STOP.
