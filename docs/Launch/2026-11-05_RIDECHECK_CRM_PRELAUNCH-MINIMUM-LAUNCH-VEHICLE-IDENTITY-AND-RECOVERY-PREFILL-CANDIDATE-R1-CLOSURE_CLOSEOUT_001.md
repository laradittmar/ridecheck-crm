PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R1-CLOSURE

Date: 2026-11-05. Verification only. This file is the only write to the vehicle worktree. No
candidate file changed. Nothing staged, committed, merged, built, deployed or migrated live. No
flag changed, the Flow was not published, Meta was not contacted, no live data was written.
The agenda worktree was read only.

================================================================================
0. VERDICT
================================================================================
READY FOR INDEPENDENT REVIEW

  This closes the R1 evidence gap in §17 of the 2026-11-04 closeout, and it is the verdict for the
  R1 tree. That closeout is preserved unchanged, so its §0 still reads "no verdict yet"; this file
  supersedes that line.

    Full SQLite          71 vs 71 failing ids, NEW 0, GONE 0, 0 message differences
    Mutants              52 of 52 killed, 0 not applied
    Focused SQLite       102 passed
    Vehicle PostgreSQL   19 passed; test_minimum_launch_pg 19 passed
    Full PostgreSQL      29 vs 29, NEW 0, GONE 0 (earlier run, same tree, reconciled in §4)
    Flow                 generator byte-identical; validators as recorded
    Merge simulation     102 vehicle + 72 escaping passed on the resolved tree

  This verdict does not cover publication. The candidate Flow has never been through Meta or a
  handset (§8). The evidence limits are in §7.

================================================================================
1. PRECONDITIONS
================================================================================
  Worktree     branch integration/prelaunch-vehicle-identity-recovery, HEAD 198ed4d6a715...,
               25 changed paths, 0 staged
  R1 content   snapshot digest recomputed: aa75cf1ff5d88702... equals the recorded R1 value.
               Worktree differs from the snapshot only by the 2026-11-04 closeout.
  kanban_view  40084f99a196de18e3ad3904632fa56bbd4e0c13251d22a4f642cb4c37a8e7b0
  Other edits  no other session had its working directory or an open file in this worktree. Newest
               candidate file 2026-11-04 17:43, closeout 18:20.
  Memory       the first reading passed (1628 MiB), then it fell to 1485 MiB. The chain held. It
               started at 1539 MiB at 13:37:14; the 1536 MiB gate was not lowered.
  Competing    none at each step: only the three live containers, no pytest, browser or harness.

  Memory during the run: lowest sample 769 MiB, 500 MiB stop never reached. Heavy checks ran one at
  a time, each behind the same gate, with a 3-second watchdog.

================================================================================
2. FULL SQLITE COMPARISON
================================================================================
  Runner: ridecheck-crm-test:l4.7b2 (sha256:c50f470951d4...), --network none, read-only trees,
  DATABASE_URL=sqlite://, TZ=America/Argentina/Buenos_Aires.

  The pristine result was NOT reused. The tree, inventory and image matched, but the environment
  could not be proven identical: the date had changed, and at least one test reads today's date
  (below). Both sides ran today, pristine first, then candidate.

  Side       tree                failed  passed  skipped  errors  cases  time
  pristine   198ed4d exact          62    7256     677      9     8003   8m12s
  candidate  R1 aa75cf1ff5d8...     62    7358     696      9     8124   7m11s

  Pristine tree proven identical to `git archive 198ed4d` by full recursive diff.

  Failing identities  71 vs 71, NEW 0, GONE 0
  Skipped identities  NEW 19, GONE 0
  Cases               added 121, removed 0
  Failure messages    0 differ
  Skip messages       0 differ

  Every difference, explained:
    * +121 cases: exactly test_vehicle_identity_recovery (102) and ..._pg (19).
    * +102 passed: the first of those suites. 7256 + 102 = 7358.
    * +19 skipped: the PG suite, which needs VEHICLE_PG_URL / VEHICLE_PG_MIG_URL. It ran in §4.
    * 11 failure tracebacks differ in the body, not the message: all are pre-existing calendar
      failures (test_m12_3_calendar_nav, test_m12_calendar_ui) and the only change is kanban_view.py
      line numbers, shifted by 3 by the candidate's additions.
    * Pristine today vs pristine 2026-11-04: same cases, same failing ids, 1 message differs:
      test_10a_today_data_attr_correct prints today's date. Not a candidate effect.

  The 71 failing ids are pre-existing on 198ed4d. They were not investigated here.

  Run limit: the container cap was 1050 MiB on both sides, not the earlier 1400 MiB, so the cap
  alone could not cross the 500 MiB stop. Peaks were 819 and 803 MiB, so it was not reached.

================================================================================
3. MUTATION RUN
================================================================================
  52 of 52 KILLED. 0 survived, 0 not applied. Sequential, one container, 900 MiB cap, 6m33s.
  Unmutated control: the same suite on the same tree, 102 passed.
  Scope: the 42 earlier mutants plus G1-G10. A mutant counts as killed only on pytest exit 1, a
  test failure, so a collection or import error would not count. Kills come from
  test_vehicle_identity_recovery only, with -x, so each records the first killing test.
  No survivor, so no product or test change was needed.

================================================================================
4. CANDIDATE INTEGRITY
================================================================================
  Focused SQLite          102 passed, 17s.
  Vehicle PostgreSQL      rerun today on PostgreSQL 16: 19 passed, 7 subtests.
  test_minimum_launch_pg  rerun today: 19 passed, 12 subtests.
                          vid-pg and vid-pg-net removed by exact name; no vid-* container remains.
  Full PostgreSQL         not rerun; reconciled. cmp_r1 recomputed from the retained XML: 28 files,
                          failing 29 vs 29, NEW 0, GONE 0, 26 message differences, 0 beyond the head
                          revision name, 797 vs 816 cases. No file in the snapshot is newer than
                          that run (2026-11-04 18:15), and the digest is unchanged, so no candidate
                          file affecting those results changed afterward.
  Migration rehearsal     not rerun, condition not met. The migration, models.py and
                          schema_preflight.py date from 15:17-15:18 on 2026-11-04, before the R1
                          rehearsal (17:45). No file has changed since then.
  Generator               rerun from v7.7: byte-identical to the candidate.
  Validators              scripts/validate_flow_json.py on the candidate:
                            plain, strict, pinned graph   0 routing, 0 error, 0 render, 0 unproven
                            --precedent v7.7              0 routing, 0 error, 0 render, 7 unproven
                          Controls: published v7.7 clean; known-bad v7.6 exits 1, so not blind.
                          compare_flows.py v7.7 vs candidate exits 0.
  Candidate Flow hashes   raw        2102a8ec5f01be41c6671a1d9a3c59cf1f79fbad55354bf623f1db1cd3348354
                          canonical  3b8dc55eef53e260680c0efa4c894fc37bf22cbe0532dceee40a56d5d0370292
  Published Flow          local artifact unchanged: TARGET_v7.7 raw 4ce27122a55f3efd... matches the
                          README pin and is byte-identical to the copy in minimum-launch. This
                          milestone published nothing. Meta was not contacted, so Meta's own copy
                          was not read: "unchanged" means no action here altered it.

================================================================================
5. FUNCTIONAL INVARIANTS
================================================================================
  Every test below passed today. Unmarked tests passed in both the full and the focused SQLite
  runs. (PG) means the PostgreSQL run.

  Fields optional                    a01 a03 g08 f09 s06 c03
  Blank persists as NULL             k01 p01 c02 a04
  85000 and 85.000 -> 85000          k02 k08 a05b c01b g07b pgv_02b (PG)
  Malformed or ambiguous refused     k03 k04 k09 k10 g09 g10 c04 a02 p04
  Dominio normalization unchanged    p02 p03 p05 p06 a07 (limit: §7)
  Winner and loser details persist   s04 r01 i01 i02 i03 pgv_01 (PG, real race)
  Prefill scoped                     thread r12, candidate r10, cycle r11, plus r09b r09c
  Rejected customer gets their data  r03 r04 r04b r06 r07 r20 pgv_02 (PG, new OS process)
  No identity crossing               r10 r11 r12
  Retries idempotent                 s02 s05 r22 r23 g07c
  Flag OFF: no change                g01 g02 g06 r08 r26 s06 r27

  Mutants R1-R10 and the G set confirm these tests can fail. Flag OFF is also supported by §2: the
  whole existing suite ran with the flag at its default and showed NEW 0 / GONE 0. The flag is not
  in Compose or in the live backend environment.

  "No change when OFF" covers booking and Flow behaviour. The new columns, CRM fields and migration
  are not behind the flag: this tree expects head 20261104_vehicle_identity whatever the flag says.

================================================================================
6. MERGE PREPARATION (NOT MERGED)
================================================================================
  Inputs: agenda kanban_view.py e555a7f41f8960a0..., base 198ed4d, vehicle 40084f99a196de18...
  git merge-file, in scratch, reproduces 5 conflicts, all in render_calendar_page:
    1  _aveh / _aaddr   BOTH: vehicle join (dominio, kilometraje) and _esc_txt(r.direccion_texto)
    2  slot card        agenda line
    3  slot card 2nd    agenda line
    4  month card       agenda line
    5  week card        agenda line
  Conflicts 2-5 are the name line only; the vehicle line merges by itself.

  Resolved hash e7a9e334f92d14d4d28ca743aa49259d780658bcaa0593a94109ae4411bee654, the same as the
  independent review. It parses, render_table_page is gone, and the dominio and kilometraje join is
  present once. 4 of 4 name sinks and the address are escaped, 0 raw, and all 4 _aveh sinks use
  _esc_txt.

  Scratch simulation of the merged tree (candidate, resolved file, control_view.py, whatsapp_ui.py,
  three escaping suites): 174 passed, 786 subtests. That is 102 vehicle + 72 escaping.

  Do not take the vehicle side whole: the review measured 68 agenda failures from that.
  Until merged, this tree still has the raw name and address sinks (F-1).

  The final merged tree must still run:
    * full SQLite;
    * vehicle suites;
    * PostgreSQL-gated suites;
    * browser escaping proof;
    * migration rehearsal.
  This simulation does not replace them. Reconfirm both kanban_view.py hashes at merge time, because
  both candidates are uncommitted.

================================================================================
7. EVIDENCE LIMITS
================================================================================
  L-1  Dominio: no pre-R1 copy of vehicle_identity.py survives, so "unchanged" is not a byte
       comparison. It rests on the P tests, and on mutants N1-N3: written against the pre-R1 code,
       they still apply verbatim to the R1 tree and are killed. R1's stated change was mileage only.
  L-2  Prefill scope tests r10-r12 run on SQLite. PostgreSQL proves the read in a new process
       (pgv_02, pgv_02b), not each scope boundary separately.
  L-3  Full PostgreSQL was reconciled, not rerun. Its pristine side is the earlier run of the
       unchanged tree. Only the two suites in §4 ran today.
  L-4  Mutants were judged against the focused suite only.
  L-5  Not assessed: the 71 SQLite and 29 PostgreSQL pre-existing failures, beyond identity
       comparison.
  L-6  Housekeeping, no effect: the scratch frozen.digest file is stale, from before R1, and was
       recomputed instead of trusted. backend/app/services/__pycache__ (git-ignored, dated
       2026-11-04 17:43) is in the worktree and predates this milestone.

================================================================================
8. STILL OPEN FROM THE 2026-11-04 CLOSEOUT
================================================================================
  Blocks publication: Meta has not validated the candidate (Form init-values, TextInput pattern,
  TextInput helper-text), and no handset has run it.
  Owner decisions: D-1 order of Flow and flag cut-over; D-2 migration window separate from enablement.
  Recommended: rehearse the migration against a live-schema dump.
  Merge work: the return-cutoff migration 20261102_professional_operational_origin needs re-parenting.
  F-1 is resolved only when the accepted escaping candidate is merged and deployed.

================================================================================
9. ISOLATION
================================================================================
  Before and after: HEAD 198ed4d, index empty, snapshot digest aa75cf1ff5d8..., kanban_view.py and
  Flow hashes unchanged. The 2026-11-04 closeout is unchanged, d3546a67f107aa48...
  Live fingerprint identical at start and end of this run: backend, postgres and n8n container id,
  image, start time, restart count; backend environment hash; Compose and .env hashes; integration
  and main HEAD and diff.
  Against the 2026-11-04 fingerprint, one change: minimum-launch's untracked-file hash, from another
  session, as §17.4 recorded. Its HEAD and tracked diff are unchanged.
  The live database was not queried. Agenda worktree was read only. Evidence is in
  /tmp/claude-0/-opt/96d2af32-9a81-475f-917d-d028419cd80f/scratchpad/out/closure and is not part of
  the candidate.

STOP.
