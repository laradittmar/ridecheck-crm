PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R2-CARD-SCOPE-CORRECTION

Date: 2026-11-05. Local correction only. Nothing staged, committed, pushed, merged, built, deployed
or migrated live. No flag changed, no Flow published, Meta not contacted, no message sent, neither
live database touched. All values in tests are synthetic.

================================================================================
0. VERDICT
================================================================================
READY FOR FOCUSED INDEPENDENT REVIEW

  V-01, V-02, V-03, E-1 and E-2 are corrected and tested on SQLite and PostgreSQL 16.

    Full SQLite vs 198ed4d   71 vs 71 failing ids, NEW 0, GONE 0, 0 message differences
    Mutants                  72 of 72 killed on the final tree, plus the E-1 control on PostgreSQL
    Focused SQLite           251 passed, 44 subtests (vehicle + minimum-launch suites)
    PostgreSQL 16            vehicle 49 passed, 18 subtests; minimum launch 19 passed, 12 subtests
    Migration, Flow, worker, kanban_view.py    byte-identical to R1

  The owner should know before the review:
    1. An earlier design decision was REVERSED (N-1): test r09c pinned that a value given for car 2
       kills car 1's row. That contradicts this milestone, so the test was rewritten.
    2. Other details can still cross cars on the card (N-2): listing URL, seller and address, under
       accepted debt F-14. The correction covers plate and mileage, as this milestone scoped it.
    3. V-02 has a cut-over consequence (N-3): with the switch ON, a session still open on v7.7
       receives two keys v7.7 does not declare. Whether Meta tolerates that is unknown.

================================================================================
1. FILES CHANGED BY R2
================================================================================
  Against the R1 snapshot aa75cf1ff5d88702...; recursive diff shows exactly these five, plus docs.

  File                                            +    -   sha256
  backend/app/services/booking_flow_details.py    48   27  81efb3479e393b58
  backend/app/services/booking_flow_service.py     8    5  43eba1d9233fd436
  backend/app/services/lead_booking_details.py    36   17  ea4ec99b66098a31
  tests/test_vehicle_identity_recovery.py        506    4  953b4f15789529a3
  tests/test_vehicle_identity_recovery_pg.py      49    4  060b1554c49925cd
  This closeout, new.

  Tested snapshot digest fa2d42eaf087e9d9...; the worktree equals it, docs aside.

================================================================================
2. DISPOSITION
================================================================================
V-01  CLOSED. lead_booking_details.py
  Plate and mileage no longer come from the thread-wide read. They come only from the attempt the
  card displays: the focus candidate of that thread in the current cycle (`attempt_details`, the
  scope of `prefill_details`, batched for many leads).
    * Booked car on the card: no draft at all, even for a NULL. The revision lines are the source.
    * Later car after an earlier booking: that car's own scoped draft shows.
    * No focus, another cycle, or a focus candidate of another thread: nothing shown.
  Read only. Nothing copied to the lead; no evidence changed (q18).

V-02  CLOSED. booking_flow_service.py
  With the switch ON, SUMMARY always carries both keys, "" when absent or blank, never zero. With it
  OFF, the condition is as before. An absent key still clears nothing (e04).

  Checked because the two changes interact: since SUMMARY now always returns "", confirm_booking
  always carries both keys on v7.8. If a customer empties a prefilled value and Meta omits it,
  prepare_summary clears nothing, and the "" reaches confirm, which clears it (e05). That is the
  customer's decision arriving one step later, and it stays limited to values they could have seen.

V-03  CLOSED. booking_flow_details.py
  Identity, repetition and supersession are now per ATTEMPT: one candidate in one cycle.
    * `_live_rows` reads one attempt's rows. "Same value" is idempotent only inside it.
    * The same value in another attempt is a new row bound to that attempt.
    * `_claim_id` now includes candidate and cycle (`flow-details:v2`). Without this, car 2's first
      row hashes the same as car 1's and is silently dropped as a replay.
    * A correction supersedes only inside its attempt. Car 1's rows are never written to.
  Rejected alternative: letting car 2's row supersede car 1's. It is two lines, but it marks car 1's
  evidence dead, which the milestone forbids.

  Consequence handled: a thread can now hold one live row per attempt, so "newest in the thread" is
  no longer always the right answer.
    * The card overlays the displayed attempt on the thread-wide read for every claim, so it shows
      what that attempt submitted (q20) and keeps what it emptied empty (q19b).
    * `live_details` now lets a newer emptied row hide an older attempt's value (q19). Its other
      reader, the customer name fallback, is a required field.
  This goes slightly past plate and mileage, because V-03 would otherwise have regressed the card.

E-1  CLOSED. tests/test_vehicle_identity_recovery_pg.py
  test_pgm_00 no longer asserts a width. It runs the full upgrade on varchar(128) and varchar(32),
  asserts the column holds the id, and that the complete 25-character id comes back.
  varchar(128) confirmed in the audit's operational schema dump. No production change.
  Correct reason, shown by control: restoring the old equality fails with `128 != 32`.

E-2  CLOSED. E2_Summary: both absent, only mileage, only plate, both present, both blank, explicit
  zero, each through validate_screen_payload(candidate=True) and the artifact walker; plus switch
  OFF with the legacy payload.

================================================================================
3. REGRESSION TESTS
================================================================================
  30 new SQLite tests. All 30 also run on PostgreSQL unchanged, through subclasses.

  Q_CardScope (14)
    q03 q03b q10   the review's three cases
    q11            positive: own draft on its own card
    q12            positive: committed values on the booked card
    q13            committed NULL stays NULL
    q14            later car after a booking: empty, then its own draft
    q15            returning to the first car
    q16            no focus, another cycle, foreign candidate
    q17            two threads
    q18            read changes nothing
    q19 q19b q20   emptied and resubmitted values
  E2_Summary (5)   e01 matrix; e02 omitted inputs book; e03 switch OFF; e04 omitted clears nothing;
                   e05 emptied value removed at confirm
  Q_Rebinding (11)
    q30 q31        evidenced for car 2; car 1's rows byte-identical
    q32            car 2 loses a REAL race, gets its complete prefill, then books
    q33            retries at prepare and confirm write nothing, no duplicate active row
    q34            going back steals nothing
    q35            later cycle on the same car
    q36            correction supersedes inside its attempt, naming its successor
    q37 q37b       blank clears only inside its attempt; never a row with no candidate
    q38 q39        two customers; attempts never share an identity

  Existing tests changed, with the reason:
    g06    switch ON with a legacy payload used to pin "no vehicle keys". V-02 requires them. The
           OFF case is unchanged.
    r09c   see N-1.
    pgm_00 see E-1.

================================================================================
4. MUTATION EVIDENCE
================================================================================
  72 of 72 KILLED, 0 survived, 0 not applied, on the final tree. Control: same suite passes.
  E-1 control on PostgreSQL: KILLED, `128 != 32` at test_pgm_00.

  Composition: 48 earlier mutants unchanged, 4 retargeted because R2 rewrote their text (P7, P8, R11,
  C9), 20 new.

  Required by the milestone
    candidate filter removed from the card read        V1a
    committed NULL falls back to another draft         V1c, plus V1d, V1e
    either SUMMARY key omitted                         V2a, V2b
    same value across candidates as idempotent        V3a, plus V3b, V3c
    duplicate evidence on a retry                      V3d
  Also: V1b cycle filter, V1f V1g displayed attempt, V2c switch, V2d omitted as zero, V2e absent key
  clears, V3e V3f claim id scope, V3g earlier attempt superseded, V3h correction not superseding.

  First pass was 70 of 72. Both survivors were test gaps, not product defects, examined before any
  change:
    V1d  nothing proved a later car still shows its own draft after an earlier booking. q14 extended.
    R8   with `previous` now in scope, it differs only for a row with no candidate. q37b added.
  Production code did not change for either. All 72 were then rerun on the final tree.

  Not mutated: the same-thread check on the focus candidate. Evidence rows bound to a foreign
  candidate cannot match, so removing it changes no output.

================================================================================
5. SQLITE RESULT
================================================================================
  Runner ridecheck-crm-test:l4.7b2, --network none, read-only trees, one side at a time, both today.

  Side       tree                failed  passed  skipped  errors  cases
  198ed4d    exact                  62    7256     677      9     8003
  candidate  R2 fa2d42eaf087...     62    7388     726      9     8184
  (pytest totals; junit counts one more error-and-pass case on each side, audit E-6.)

  Failing identities  71 vs 71, NEW 0, GONE 0
  Skipped identities  NEW 49, GONE 0
  Failure messages    0 differ
  Skip messages       0 differ
  Cases               added 181, removed 0

  Every difference, attributed:
    * +181 cases: test_vehicle_identity_recovery (132) and ..._pg (49), nothing else.
    * +132 passed: the first of those. 7256 + 132 = 7388.
    * +49 skipped: the PostgreSQL suite without its URLs. 677 + 49 = 726.
    * 11 tracebacks differ in body only: pre-existing calendar failures, kanban_view.py line numbers
      shifted by 3. Same 11 as the R1 closure.

  Focused: 251 passed, 44 subtests = vehicle 132 + test_minimum_launch, _r1, _r2 119.
  The 71 failures are pre-existing and were compared, not assessed.

================================================================================
6. POSTGRESQL RESULT
================================================================================
  PostgreSQL 16.13, disposable, internal network, tmpfs.
    test_vehicle_identity_recovery_pg   49 passed, 18 subtests. Includes pgm_00, and Q03, Q03b, Q10,
                                        the real-race rebinding and retry idempotency, as
                                        PG_CardScope, PG_Summary and PG_Rebinding.
    test_minimum_launch_pg              19 passed, 12 subtests.
  vid-pg and vid-pg-net removed by exact name. 0 vid-* containers or networks remain.

  Migration rehearsal: not repeated, condition not met. Migration 2667ae8c682c595e, models.py
  5b167b3238c8b332 and schema_preflight.py 0d26b939fea7e5b0 are byte-identical to R1. The accepted
  evidence is the independent review, section 4.

================================================================================
7. ARTIFACT AND PROTECTED FILES
================================================================================
  No migration or Flow artifact hash changed. Byte-identical to R1:
    20261104_vehicle_identity.py          2667ae8c682c595e
    CANDIDATE_v7.8 raw                    2102a8ec5f01be41c6671a1d9a3c59cf1f79fbad55354bf623f1db1cd3348354
    CANDIDATE_v7.8 canonical              3b8dc55eef53e260680c0efa4c894fc37bf22cbe0532dceee40a56d5d0370292
    make_v78_vehicle_prefill_candidate    dfa84cd267607655
    TARGET_v7.7                           4ce27122a55f3efd
    flow_contract.py                      a5fba45c644c1dee
    booking_slot_recovery.py (worker)     187d57d13c55b5e3
    kanban_view.py                        40084f99a196de18

  Scratch merge check not needed: kanban_view.py is unchanged, so the 5 conflicts and resolved hash
  e7a9e334f92d14d4... still apply. The escaping candidate does not change any of the three R2
  production files.

================================================================================
8. REQUIRED OUTCOME
================================================================================
  Car 2 never displays car 1's plate or mileage      q03 q03b q10 q11 q15; V1a V1c
  Committed NULL/NULL not supplemented               q10 q13; V1c V1d C9
  Car 2 displays its own scoped draft                q11 q14; V1d
  SUMMARY supplies its declared keys, switch ON      e01 e02; V2a V2b P8
  Legacy unchanged, switch OFF                       e03 g01 g06; P7 V2c
  Repeated facts evidenced for the current attempt   q30 q32 q35; V3a V3b V3e V3f
  Same-scope retries idempotent                      q33 q34 s02 g07c; V3d
  Recovery prefill complete for a later vehicle      q32
  No customer, thread, candidate or cycle crossing   q16 q17 q31 q34 q35 q38; V3c V3g
  Production dark with flags OFF                     see below

  Flag OFF: the switch is absent from Compose and the live backend environment, and its setting
  defaults to off (g02). The full suite ran at the default, NEW 0 / GONE 0.
  V-01 and V-03 are NOT behind the switch: they take effect when this code deploys. Neither affects
  a booking or a Flow payload, and the published v7.7 collects neither vehicle field. Two ungated
  effects, both limited to a second vehicle on a thread: V-03 writes that attempt its own rows for
  repeated person details, and the card prefers the displayed attempt's values. So "dark" holds for
  booking and Flow behaviour, not for those evidence rows and that card read.

================================================================================
9. FINDINGS FOR THE FOCUSED REVIEW AND THE OWNER
================================================================================
  N-1  DESIGN REVERSAL. r09c pinned "corrected under another vehicle: the old row is history, even
       for its own vehicle". R2 reverses it: another car's plate does not correct this car's plate.
       The test keeps its guarantee for what it can still protect: rows written before R2 can have a
       successor bound to another candidate, and those stay history. R5 is still killed by it.
       Cost: an e-mail corrected for car 2 is not corrected for car 1's prefill. The customer sees
       the old value there and can edit it.
       The 2026-11-04 closeout describes the old behaviour in places. It was not edited.

  N-2  DECISION. Listing URL, seller and inspection address can still appear beside the wrong car.
       If car 2 gives none, the card falls back to the thread's latest, which can be car 1's. This
       is accepted debt F-14, and this milestone's instructions name only plate and mileage, but its
       invariant says "every vehicle-related value". A listing URL or seller is arguably one. Not
       changed. The owner should decide whether F-14 still stands for those three.

  N-3  CUT-OVER INPUT (P-3, D-1). Switch ON plus an open v7.7 session: SUMMARY now carries two keys
       v7.7 does not declare. g06 proves booking works locally. Whether Meta accepts undeclared keys
       is not known and must be settled with Ejecutar or a handset, or avoided by the cut-over plan.

  N-4  NOT ADDRESSED, OUT OF SCOPE. Audit Q04: switch ON, a v7.7 form submitting a blank e-mail can
       clear a value that form never displayed. That concerns a blank PRESENT in the payload. An
       omitted key clears nothing (e04). It remains an open P-3 decision.

  N-5  Rows written before R2 keep v1 claim ids and thread-wide supersession. No backfill. New code
       reads them correctly: a repeat in scope is matched by value, not by hash.

  Limits
    * The second car is created by re-pointing the issued offer and focus, as r10 and the review
      did; a later cycle by setting the state column. Not real Conversation Engine turns.
    * q14 writes car 2's draft directly, because that thread has no issued offer after booking.
    * PostgreSQL ran on a chain-built schema, with varchar(128) applied by the test, not on the
      operational dump.
    * The 28-file PostgreSQL comparison was not rerun; the milestone named two suites. R2 changes
      evidence writes, so this needs the merged-tree PostgreSQL run below.
    * Audit items not in this scope stand: M-1, L-1 to L-3, E-3 to E-6.

================================================================================
10. ISOLATION
================================================================================
  Candidate   HEAD 198ed4d, branch unchanged, index empty, 28 paths with this closeout.
              R1 independent-review audit 176acd18bab7c52d, unchanged and not edited.
              R1 closure 2b56d1902af032dc, 2026-11-04 closeout d3546a67f107aa48, unchanged.
  Escaping    /opt/ridecheck-crm-agenda-escaping: HEAD 198ed4d, index empty, kanban_view.py
              e555a7f41f8960a0, no file changed since this milestone began. Read only.
  Live        fingerprint identical before and after: backend, postgres, n8n container id, image,
              start time, restart count; backend environment hash; Compose and .env hashes; HEAD,
              status and diff of minimum-launch, integration and main; v7.7 and r3 hashes.
              No live database queried. No docker exec into a live container.
  Memory      every heavy check gated at 1536 MiB, none needed to wait. Lowest sample in the gated
              chain 1289 MiB. 500 MiB stop never reached. One heavy job at a time.
              Focused iterations during development ran after a manual check at 2032-2049 MiB.
  Evidence    /tmp/claude-0/-opt/96d2af32-9a81-475f-917d-d028419cd80f/scratchpad/out/r2
              Not part of the candidate.

================================================================================
11. REMAINING PREREQUISITES
================================================================================
  Before controlled merge
    1. Focused independent review of R2.
    2. Owner decision on N-2.

  On the merged tree
    3. Full SQLite, vehicle suites, all PostgreSQL-gated suites, browser escaping proof, migration
       rehearsal.

  Before migration
    4. Fresh schema-only dump and rehearsal (P-4); window plan (M-1, D-2).

  Before publication or switch ON (Meta and handset)
    5. Owner Ejecutar: Form init-values, TextInput pattern and helper-text, object details_prefill;
       7 unproven properties (P-1).
    6. Handset proof with a real slot conflict, including {} and partial prefill, blank as "" or
       absent, and what the customer sees when pattern fails (P-2). V-02 means the blank answer no
       longer decides whether booking works.
    7. Cut-over decision, including open v7.7 sessions: N-3, N-4, P-3, D-1.

================================================================================
12. PROPOSED COMMIT (NOT EXECUTED)
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
    backend/app/services/vehicle_identity.py \
    backend/app/settings.py \
    backend/app/ui/kanban_actions.py \
    backend/app/ui/kanban_view.py \
    backend/migrations/versions/20261104_vehicle_identity.py \
    docs/Launch/2026-11-04_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE_CLOSEOUT_001.md \
    docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R1-CLOSURE_CLOSEOUT_001.md \
    docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R1-INDEPENDENT-REVIEW_AUDIT_001.md \
    docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R2-CARD-SCOPE-CORRECTION_CLOSEOUT_001.md \
    docs/operations/BOOKING_FLOW_PREFILL_CONSTRAINT.md \
    meta/flows/booking/CANDIDATE_v7.8-vehicle-identity-prefill.json \
    meta/flows/booking/README.md \
    meta/flows/booking/make_v78_vehicle_prefill_candidate.py \
    tests/test_convergence_p4_r1.py \
    tests/test_minimum_launch.py \
    tests/test_minimum_launch_pg.py \
    tests/test_vehicle_identity_recovery.py \
    tests/test_vehicle_identity_recovery_pg.py \
    tests/test_w1f_w01_r2_outstanding_question.py

  Commit message:

    Add vehicle identity and durable recovery prefill candidate

    Optional kilometraje and dominio on revisions, the v7.8 Flow candidate
    (not published), and recovery prefill from durable evidence behind
    BOOKING_FLOW_DETAILS_PREFILL_ENABLED, default off.

    Details are scoped per attempt, one candidate in one cycle: the CRM card
    shows plate and mileage only beside their own car, repeated facts are
    evidenced for the current attempt, and SUMMARY always supplies its
    declared keys with the switch on.

    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

================================================================================
13. RECOMMENDED NEXT MILESTONE
================================================================================
  PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R2-FOCUSED-INDEPENDENT-REVIEW

  Scope: the three production files and two test files in section 1, the per-attempt evidence model
  and its effect on readers, the r09c reversal (N-1), and N-2 to N-4. Confirm that the migration,
  Flow and kanban_view.py hashes still match. If it passes, that review may issue READY FOR
  CONTROLLED MERGE.

STOP.
