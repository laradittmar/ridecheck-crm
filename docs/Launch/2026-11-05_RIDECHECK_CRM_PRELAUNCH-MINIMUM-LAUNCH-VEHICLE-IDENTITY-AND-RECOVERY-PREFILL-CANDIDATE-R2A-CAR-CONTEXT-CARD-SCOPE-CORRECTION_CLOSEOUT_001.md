PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R2A-CAR-CONTEXT-CARD-SCOPE-CORRECTION

Date: 2026-11-05. Local correction only. Nothing staged, committed, pushed, merged, built, deployed
or migrated live. No flag changed, no Flow published, Meta not contacted, no message sent, neither
live database touched. All values in tests are synthetic.

================================================================================
0. VERDICT
================================================================================
READY FOR FOCUSED INDEPENDENT REVIEW

  N-2 is closed. Listing URL, seller, inspection address and requested slot no longer cross cars on
  the "Datos de reserva" block, joining plate and mileage. F-14 no longer applies to car facts.

    Full SQLite vs 198ed4d   71 vs 71 failing ids, NEW 0, GONE 0, 0 message differences
    Mutants                  85 of 85 killed on the final tree, first pass
    Focused SQLite           268 passed, 44 subtests
    PostgreSQL 16            vehicle 65 passed, 18 subtests; minimum launch 19 passed, 12 subtests
    Migration, models, preflight, Flow, contract, worker, kanban_view.py    unchanged

  The owner should know:
    1. An operator booking with no candidate (R-1): its car fields go blank in this block whenever
       the thread has a focus candidate. It follows the rule; it is a visible change.
    2. The card can show two cars (R-2): the pill and revision lines read the latest Revision, the
       block reads the focus candidate. Neither mixes values, but kanban_view.py was protected.

================================================================================
1. OWNER DECISIONS RECORDED
================================================================================
  N-2       CLOSED. Car-specific facts never fall back to another candidate's thread-wide value.
  N-1       ACCEPTED. The per-attempt evidence model stands. Not changed here.
  N-3, N-4  DEFERRED to the controlled Meta cutover milestone. Not touched. Switch stays OFF, candidate
            Flow stays unpublished.

================================================================================
2. FIELD CLASSIFICATION
================================================================================
  Every field of LeadBookingDetails, plus the rest of the card that concerns these facts.

  A. PERSON / THREAD LEVEL (behaviour unchanged)
    Field           Claim type             Source
    customer_name   FLOW_CUSTOMER_NAME     displayed attempt, else thread latest, else booking
    email           FLOW_CUSTOMER_EMAIL    the same
    Lead telefono, email, nombre           the lead's own columns, in the card header

  B. CAR / ATTEMPT LEVEL
    Field               Claim type               Committed source     Uncommitted source
    vehicle             none                     displayed candidate  displayed candidate
    locality            none                     displayed candidate  displayed candidate
    kilometraje         FLOW_VEHICLE_MILEAGE     revisions            attempt evidence
    dominio             FLOW_VEHICLE_PLATE       revisions            attempt evidence
    inspection_address  FLOW_INSPECTION_ADDRESS  thread_revisions     attempt evidence
    seller_name         FLOW_SELLER_NAME         thread_revisions     attempt evidence
    seller_phone        FLOW_SELLER_PHONE        thread_revisions     attempt evidence
    listing_url         FLOW_LISTING_URL         thread_revisions     attempt evidence
    requested_slot      FLOW_REQUESTED_SLOT      thread_revisions     attempt evidence

  Attempt evidence: bound to the displayed candidate and the current cycle. No scoped value: blank.
  Kilometraje and dominio on a committed card appear in the revision lines, not this block.

  Not in this block: seller type and agency. They have no Flow claim. They show only on a revision's
  own lines, so they stay with that revision.

  Two points of interpretation:
    * The milestone says "committed Revision". `revisions` has no seller or listing columns, so a
      booking's canonical copy of those is its `thread_revisions` row, written in the same
      transaction. x05 proves the pair and the card agree.
    * requested_slot is not on the owner's list. It describes one inspection, so it is car level.

  The code lists the two person claims explicitly (`PERSON_CLAIMS`). Everything else is car level by
  default, so a claim type added later is scoped unless someone asks otherwise.

================================================================================
3. FILES CHANGED BY R2A
================================================================================
  Against the R2 snapshot fa2d42eaf087e9d9...; recursive diff shows exactly these three, plus docs.

  File                                            +    -   sha256
  backend/app/services/lead_booking_details.py    47   40  51e8f7a52f663e12
  tests/test_vehicle_identity_recovery.py        282    0  e6d2606e4a2e3f43
  tests/test_vehicle_identity_recovery_pg.py       5    0  f202855c82b356ef
  This closeout, new.

  Tested snapshot digest 2a4812d7111f9ec7...; the worktree equals it, docs aside.
  No existing test was changed or weakened: 0 lines removed from either test file.

  What changed in the reader:
    * Thread-wide details now supply person claims only.
    * A committed card takes car fields from the booking alone. It was evidence first, booking
      second, so a newer draft could beat a committed value.
    * The booking is looked up for the DISPLAYED candidate. It was the thread's latest, which would
      be the wrong car once a thread holds two bookings.
    * With no candidate to display, the thread's latest booking is what the card shows (x15).

================================================================================
4. MULTI-CAR TEST MATRIX
================================================================================
  Q_CarContext, 16 tests. All 16 also run on PostgreSQL through PG_CarContext.

  Owner's case                                                Test
   1  Car 1 has values, focus moves to car 2 with none        x01, also after a blank submission
   2  Car 2 supplies its own                                  x02
   3  Return to car 1, values intact                          x03, rows byte-identical
   4  Car 2 commits blank, no fallback                        x04
   5  Car 2 commits its own, booking and card agree           x05
   6  Car 1 loses a REAL race, car 2 books                    x06, card and prefill at each step
   7  Same name and e-mail on both cars                       x07
   8  Two customers                                           x08
   9  Later cycle, same candidate                             x09, in both directions
  10  Rendering writes nothing                                x10: evidence, revisions, thread
                                                              revisions and leads, rendered twice

  Added
  x11  a newer draft does not beat the committed value, including the slot
  x12  a committed blank is not filled, even by the same car's own draft
  x13  two booked cars on one thread each show their own booking
  x14  a later car does not borrow an earlier booking; the person still shows
  x15  no displayed car, but a booking: that booking is shown
  x16  exactly two claims follow the person

  Recovery prefill unchanged: its code is untouched. x06 and q32 prove the losing attempt gets its
  complete information. Same-scope idempotency: q33. Earlier evidence preserved: q31, x03.

================================================================================
5. RESULTS
================================================================================
5.1 Full SQLite. Runner ridecheck-crm-test:l4.7b2, --network none, read-only trees, one at a time.

  Side       tree                failed  passed  skipped  errors  cases
  198ed4d    exact                  62    7256     677      9     8003
  candidate  R2A 2a4812d7111f...    62    7404     742      9     8216
  (pytest totals; audit E-6.)

  Failing identities  71 vs 71, NEW 0, GONE 0
  Skipped identities  NEW 65, GONE 0
  Failure messages    0 differ
  Skip messages       0 differ
  Cases               added 213, removed 0

  Every difference, attributed:
    * +213 cases: test_vehicle_identity_recovery (148) and ..._pg (65), nothing else.
    * +148 passed: 7256 + 148 = 7404.
    * +65 skipped: the PostgreSQL suite without its URLs. 677 + 65 = 742.
    * 11 tracebacks differ in body only: pre-existing calendar failures, kanban_view.py line numbers
      shifted by 3. No other line differs.

5.2 Focused. 268 passed, 44 subtests: vehicle 148, test_minimum_launch, _r1, _r2, _e2e 120.

5.3 PostgreSQL 16.13, disposable, internal network, tmpfs.
    test_vehicle_identity_recovery_pg   65 passed, 18 subtests
    test_minimum_launch_pg              19 passed, 12 subtests
    E-1 control repeated: restoring the old equality fails with `128 != 32`.
    vid-pg and vid-pg-net removed by exact name. 0 vid-* containers or networks remain.

5.4 Mutants. 85 of 85 KILLED on the final tree, first pass. 0 survived, 0 not applied.
    Control: 268 passed.
    Composition: 67 carried over from R2, 5 retargeted because R2A rewrote their text, 13 new.

    Restoring thread-wide fallback
      V1c  thread-wide car fallback restored
      V1e  no scope falls back to the thread
      C9   draft outlives the booking
      "A4" committed blank filled by a draft (new)
    Removing scoping
      V1a  candidate filter        V1b  cycle filter
      V1d  any booking counts as the displayed car
      "A2" latest booking per candidate lost (new)
    Misclassification: B1-B7, each car claim moved to the person in turn: address, seller name,
      seller phone, listing URL, mileage, plate, slot. All killed.
      "A6" "A7" e-mail or name moved to the car.
    Others: "A1" booking with no focus hidden; "A5" uncommitted car shows nothing; V1f V1g person
      overlay.
    Quoted names are the new R2A set; earlier mutants also use A1-A3 for the API schema.

    One planned mutant was dropped before the run: it wrote draft values that the committed update
    overwrote at once, so it changed nothing. A4 is the real form of that fault.

5.5 Migration rehearsal not repeated, as instructed.

================================================================================
6. PROTECTED FILES
================================================================================
  Byte-identical to R1 and to R2:
    20261104_vehicle_identity.py          2667ae8c682c595e
    models.py                             5b167b3238c8b332
    schema_preflight.py                   0d26b939fea7e5b0
    CANDIDATE_v7.8 raw                    2102a8ec5f01be41c6671a1d9a3c59cf1f79fbad55354bf623f1db1cd3348354
    CANDIDATE_v7.8 canonical              3b8dc55eef53e260680c0efa4c894fc37bf22cbe0532dceee40a56d5d0370292
    make_v78_vehicle_prefill_candidate    dfa84cd267607655
    TARGET_v7.7                           4ce27122a55f3efd
    flow_contract.py                      a5fba45c644c1dee
    booking_slot_recovery.py (worker)     187d57d13c55b5e3
    kanban_view.py                        40084f99a196de18
  Byte-identical to R2: booking_flow_details.py 81efb3479e393b58,
  booking_flow_service.py 43eba1d9233fd436.

  kanban_view.py unchanged, so the 5 conflicts and resolved hash e7a9e334f92d14d4... still apply. The
  escaping candidate does not change lead_booking_details.py.

================================================================================
7. FINDINGS AND LIMITS
================================================================================
  R-1  VISIBLE CHANGE. `thread_revisions.candidate_id` is optional, so a booking can have none. If
       the thread also has a focus candidate, that booking cannot be tied to the displayed car, so
       its address, seller and URL leave this block. Previously they filled it. The revision lines
       still show that booking's Revision. Fail safe, per "if no correctly scoped value exists,
       display blank". How often this occurs on live rows was not measured: no live read was
       authorised. No test covers this case.

  R-2  CARD COHERENCE. The vehicle pill and revision lines read the latest Revision. The block reads
       the focus candidate. With car 1 booked and focus on car 2, the card shows car 1 up top and
       car 2 in the block. Each set is internally consistent and x14 proves no value crosses, but an
       operator sees two cars, as before this work. Changing it means changing kanban_view.py.

  R-3  UNGATED. Like V-01 and V-03, this reader takes effect when the code deploys, with the switch
       off. It does not affect a booking or a Flow payload. The effects on live cards:
         * Rows written before R2 that repeated a value stayed bound to the earlier candidate, so a
           second car shows those fields blank until the customer submits again.
         * A card after an operator cycle reset shows no car draft from the earlier cycle.
         * A booked card shows the booking's values even when a newer draft differs.
       All three show less or more authoritative data, never another car's.

  Limits
    * The second car is created by re-pointing the issued offer and focus; a later cycle by setting
      the state column. Not real Conversation Engine turns. x13's second booking is inserted directly.
    * Only this block is in scope. Other pages, such as the agenda, read revisions directly.
    * The 28-file PostgreSQL comparison was not rerun; it belongs to the merged tree.
    * Open audit items not in this scope stand: M-1, L-1 to L-3, E-3 to E-6.

================================================================================
8. ISOLATION
================================================================================
  Candidate   HEAD 198ed4d, index empty, 29 paths with this closeout.
              R1 audit 176acd18bab7c52d, R1 closure 2b56d1902af032dc, R2 closeout fff00a0509474964,
              2026-11-04 closeout d3546a67f107aa48: unchanged.
  Escaping    /opt/ridecheck-crm-agenda-escaping: HEAD 198ed4d, index empty, kanban_view.py
              e555a7f41f8960a0, no file changed during this work. Read only.
  Live        fingerprint identical before and after, and to the end of R2: backend, postgres, n8n
              container id, image, start time, restart count; backend environment hash; Compose and
              .env hashes; HEAD, status and diff of minimum-launch, integration and main; v7.7 and r3
              hashes. Switch absent from Compose and the live backend environment.
              No live database queried. No docker exec into a live container.
  Memory      every heavy check gated at 1536 MiB, none needed to wait. Lowest sample in the gated
              chain 1575 MiB. 500 MiB stop never reached. One heavy job at a time.
  Evidence    /tmp/claude-0/-opt/96d2af32-9a81-475f-917d-d028419cd80f/scratchpad/out/r2a
              Not part of the candidate.

================================================================================
9. REMAINING PREREQUISITES
================================================================================
  Before controlled merge
    1. Focused independent review of R2 and R2A together.

  On the merged tree
    2. Full SQLite, vehicle suites, all PostgreSQL-gated suites, browser escaping proof, migration
       rehearsal.

  Before migration
    3. Fresh schema-only dump and rehearsal (P-4); window plan (M-1, D-2).

  Controlled Meta cutover milestone
    4. N-3: with the switch ON, a session still open on v7.7 receives two keys v7.7 does not declare.
       Whether Meta accepts that is unknown. Settle by Ejecutar or handset, or avoid by plan.
    5. N-4: with the switch ON, a v7.7 form submitting a blank e-mail can clear a value that form
       never displayed (audit Q04). An omitted key clears nothing.
    6. Owner Ejecutar: Form init-values, TextInput pattern and helper-text, object details_prefill;
       7 unproven properties (P-1).
    7. Handset proof with a real slot conflict, including {} and partial prefill, blank as "" or
       absent, and what the customer sees when pattern fails (P-2).
    8. Order of Flow and switch, including open v7.7 sessions (P-3, D-1).

================================================================================
10. PROPOSED COMMIT (NOT EXECUTED)
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
    docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R2A-CAR-CONTEXT-CARD-SCOPE-CORRECTION_CLOSEOUT_001.md \
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

    Details are scoped per attempt, one candidate in one cycle. On the CRM
    card, person facts follow the thread and car facts stay with their car:
    plate, mileage, listing, seller, address and slot come from the
    displayed booking or attempt and never fall back to the thread. Repeated
    facts are evidenced for the current attempt, and SUMMARY always supplies
    its declared keys with the switch on.

    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

================================================================================
11. RECOMMENDED NEXT MILESTONE
================================================================================
  PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R2A-FOCUSED-INDEPENDENT-REVIEW

  Scope: R2 and R2A together. booking_flow_details.py, booking_flow_service.py and
  lead_booking_details.py; the two test files; the per-attempt model and its readers; the field
  classification in section 2; R-1 to R-3. Confirm the protected hashes in section 6. If it passes,
  that review may issue READY FOR CONTROLLED MERGE.

STOP.
