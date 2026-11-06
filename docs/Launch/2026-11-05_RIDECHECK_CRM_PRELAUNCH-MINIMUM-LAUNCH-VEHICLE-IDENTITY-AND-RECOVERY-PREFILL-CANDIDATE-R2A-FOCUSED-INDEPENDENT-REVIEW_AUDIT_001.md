PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R2A-FOCUSED-INDEPENDENT-REVIEW

Date: 2026-11-05. Review only. No product code changed. Nothing staged, committed, merged, pushed,
built, deployed or migrated live. No flag changed, no Flow published, Meta not contacted, no message
sent, neither live database touched. This audit is the only write to the candidate worktree. All
values used are synthetic.

================================================================================
0. VERDICT
================================================================================
READY FOR CONTROLLED MERGE

  This is not READY FOR DEPLOYMENT and not READY FOR PUBLICATION.

  V-01, V-02, V-03 and N-2 are independently closed on SQLite and on PostgreSQL 16 with the
  operational schema shape. Nothing displayed a value of another car, cycle, thread or customer in
  any probe, and every criterion in section 9 of the milestone is met.

  Two things in the milestone's wording are NOT met to the letter. I judged both non-blocking. The
  owner may overrule that, and both should be decided before deployment, not after:

    F-1  Vehicle and Zona on a COMMITTED card come from the candidate, not the booking. Section 5
         asks for "booking/revision source only" for every car field. It is the same car, never
         another one, and the closeout's own table says so, but the requirement as written fails
         for these two of the eleven fields.

    F-2  R-3 can only be confirmed with one exception, which neither closeout reports. With flags
         OFF, the name default in the Flow payload changes in a narrow case (section 6). It is the
         customer's own earlier typed name. Dispatch and outbound are unaffected.

  Also: the candidate's suite does not cover five behaviours that are correct in the product
  (T-1, section 8). The "85 of 85 mutants" claim is true of its set, not of the whole selection logic.

================================================================================
1. R2/R2A SCOPE RECONCILIATION
================================================================================
  Compared by direct recursive diff, not from the closeouts.

  Worktree against my own R1 review tree: exactly these five paths differ, plus documents.

    File                                            +     -   sha256
    backend/app/services/booking_flow_details.py    48    27  81efb3479e393b58
    backend/app/services/booking_flow_service.py     8     5  43eba1d9233fd436
    backend/app/services/lead_booking_details.py    60    34  51e8f7a52f663e12
    tests/test_vehicle_identity_recovery.py        788     4  e6d2606e4a2e3f43
    tests/test_vehicle_identity_recovery_pg.py      54     4  f202855c82b356ef

  Worktree against the tested R2A snapshot: identical, except the R2A closeout itself.
  No other production path changed. tests/test_minimum_launch*.py are unchanged from R1.

  Every removed test line, read individually (8 lines in 5 places):
    import line        widened, not weakened
    g06                keeps its OFF case. ON now expects the candidate key set, required by V-02.
    r09c               see below
    pgm_00             the fixed width 32 replaced by a full-upgrade check on 128 and 32 (E-1)
  No assertion was weakened to make a suite pass.

  r09c reversal (N-1): explicit and correctly justified. The old test pinned that car 2's value
  kills car 1's row, which contradicts V-03. It now forces the pre-R2 shape by hand, a row with a
  successor on another candidate, and keeps its assertion: such a row stays history. Its behavioural
  opposite is pinned by q31. Accepted by the owner, with the known cost that an e-mail corrected for
  car 2 is not corrected in car 1's prefill.

  Earlier documents unchanged: 2026-11-04 closeout d3546a67f107aa48, R1 closure 2b56d1902af032dc, R1
  audit 176acd18bab7c52d, R2 closeout fff00a0509474964. R2A closeout is c839573a8d18d1e2.
  Index empty. HEAD 198ed4d. 29 paths before this audit. No secret, live identifier or generated
  runtime evidence among the changed paths; test values are synthetic.

================================================================================
2. V-01, CARD IDENTITY: CLOSED
================================================================================
  Source selection order (lead_booking_details.py:141-165), read directly:
    1. Thread-wide details supply person claims only (:141).
    2. The displayed attempt overrides the person claims, including an emptied value (:142-146).
    3. The latest thread booking fills a missing person claim (:147-152).
    4. committed = the booking of the DISPLAYED candidate, or the thread's latest only when there is
       no candidate to display (:153).
    5. Committed: car fields come from that booking and nothing else. The draft branch is an `else`,
       so a draft cannot reach a committed card at all (:154-163).
    6. Uncommitted: car fields come from the displayed attempt (:165).
  kilometraje and dominio are never set on the committed branch, so the block leaves them blank and
  the revision lines own them.

  Required                                                Probe          SQLite  PostgreSQL
  Car 2 never shows car 1's plate or mileage              c01            pass    pass
  Committed NULL stays NULL, own emptied draft            c02            pass    pass
  Committed NULL with a NEWER draft after booking         c03            pass    pass
  Uncommitted shows only its own attempt                  c04            pass    pass
  Return to car 1 restores it, rows untouched             c05            pass    pass
  Rendering issues no write                               c06            pass    pass
  No thread or customer crossing, identical values        c07            pass    pass
  Cycle isolation, in both directions                     c08            pass    pass

  c06 is stronger than comparing rows: a statement listener on the engine records any INSERT, UPDATE,
  DELETE or DDL during two renders. None was issued.
  c07 includes a focus pointing at another thread's candidate: no vehicle, no values.

================================================================================
3. V-02, SUMMARY CONTRACT: CLOSED
================================================================================
  Switch ON. Each case on a fresh agenda, through validate_screen_payload(candidate=True), the
  artifact walker, then a real booking. Both keys present in every SUMMARY.

  Case                         vehicle_mileage  vehicle_plate  Revision (km, dominio)
  both absent                  ""               ""             NULL, NULL
  only mileage                 85000            ""             85000, NULL
  only plate                   ""               AB123CD        NULL, AB123CD
  both present                 85000            AB123CD        85000, AB123CD
  both blank                   ""               ""             NULL, NULL
  whitespace only              ""               ""             NULL, NULL
  explicit zero                0                ""             0, NULL

  Customer clears a visible prefilled value: tested with the blank sent and with the key omitted.
  Both end cleared at confirm, Revision NULL (s02).
  Nothing is invented: absent is "", never 0. Neither field is required in the artifact (s04).
  Malformed values are refused (s05).

  Switch OFF: SUMMARY equals the published key set and passes the published validator. The two keys
  are absent. Omitted fields and blank fields clear nothing (s03).

  There is only one caller of `_summary_screen_data` (booking_flow_service.py:866), so no other
  SUMMARY path can escape this.

================================================================================
4. V-03, PER-ATTEMPT EVIDENCE: CLOSED
================================================================================
  Required                                                Probe          SQLite  PostgreSQL
  Same value, other candidate: evidenced; car 1 intact    a01            pass    pass
  Same value, other cycle: evidenced; earlier intact      a02            pass    pass
  Retries at prepare, confirm, replayed delivery: 0 rows  a03            pass    pass
  Correction supersedes inside its attempt only           a04            pass    pass
  Flip-flop inside one attempt leaves one live row        a05            pass    pass
  Real race lost on a later car: complete prefill         a06            pass    pass
  No customer crossing with identical values              a07            pass    pass
  Row with an R1-style claim id: repeat 0, change supersedes  a08        pass    pass
  8 concurrent identical submissions: one active row each a10            n/a     pass

  Claim identity now includes candidate and cycle (booking_flow_details.py:164). Mutants M09c and M09d
  show this is necessary, not decorative.

  Every reader affected by "one live row per attempt", by search of backend/app:
    attempt_details      card. Scoped.
    prefill_details      Flow prefill. Scoped, behind the switch.
    live_details         card person claims, and the Flow name default. Thread-wide: see F-2.
    thread_evidence      reads role INSPECTION_LOCATION; these rows have role NULL. for_thread is
                         read-only for tests and tracing. Not affected.
    tester_reset         deletes by thread. Not affected.
  No other reader exists.

  a06 limit: its check through a refreshed INIT is conditional on the worker minting a new token and I
  did not confirm it ran. Counted evidence is the read of `prefill_details` after a real refusal.
  The candidate's x06 and q32 cover the same path.

================================================================================
5. R2A FIELD CLASSIFICATION
================================================================================
  CONFIRMED, with one deviation (F-1).

  Field               Level    Committed source        Uncommitted source    Result
  customer_name       person   follows thread          follows thread        correct
  email               person   follows thread          follows thread        correct
  lead's own columns  person   lead                    lead                  correct
  kilometraje         car      revisions (lines)       attempt               correct
  dominio             car      revisions (lines)       attempt               correct
  inspection_address  car      thread_revisions        attempt               correct
  seller_name         car      thread_revisions        attempt               correct
  seller_phone        car      thread_revisions        attempt               correct
  listing_url         car      thread_revisions        attempt               correct
  requested_slot      car      thread_revisions        attempt               correct
  vehicle             car      CANDIDATE, not booking  candidate             F-1
  locality            car      CANDIDATE, not booking  candidate             F-1

  PERSON_CLAIMS is exactly name and e-mail, and every other claim is car level by default (c12).
  Seller type and agency: no Flow claim exists. Set on their rows, neither appears in the block, for
  either car (c13). They show only on their revision's own lines.

  Required                                                Probe          SQLite  PostgreSQL
  No correctly scoped value: blank                        c01 c08        pass    pass
  Person stays available, no car fallback                 c11            pass    pass
  Two REAL bookings on one thread each use their own      c09            pass    pass
  Cancelled or pending booking of car 1, focus on car 2   c10            pass    pass
  Cancelled booking of the displayed car is not committed b01            pass    pass
  Completed booking of the displayed car is committed     b02            pass    pass
  Two bookings of one car: the latest wins                b03            pass    pass
  Booked car in a later cycle keeps one source            k03            pass    pass
  Zero mileage draft displays                             c14            pass    pass

  c09 closes a limit of R2A: x13 inserted the second booking directly. Here both come from
  offer, prepare and confirm through the booking authority, with SUCCESS on each.

  N-2: CLOSED. Listing URL, seller, address and slot no longer cross cars.

================================================================================
6. R-1, R-2, R-3
================================================================================
  R-1  CLASSIFIED: FAIL-CLOSED. Acceptable for minimum launch.
    It cannot display another car's data: a booking with no candidate is never adopted for a
    displayed candidate. Only data whose ownership cannot be proven is hidden (r1a), and the person
    survives. The revision lines still show it, and the row itself is untouched. A candidate-less
    booking beside car 2's draft shows car 2 only (r1b). With no focus, it is shown whole (r1c).
    One refinement of the closeout: "its fields go blank" holds only when the displayed car has no
    draft. With a draft, that draft shows, not the booking (r1a2). It is the displayed car's own.
    Writers: the Flow path and the Conversation Engine's confirm path pass a real candidate; the
    other two engine paths pass a variable I did not trace to its source. The column stays nullable.
    Prevalence on live rows: UNKNOWN, not queried.

  R-2  CLASSIFIED: NON-BLOCKING UI-COHERENCE DEBT. Not a data-integrity defect.
    With car 1 booked and focus on car 2, the block holds only car 2's values and the rest of the
    card only car 1's, in both directions (r2a). No individual section mixes cars. Each section
    carries its own "Vehículo" line.
    Residual operator risk, which is why this should not be forgotten: the block is titled "Datos de
    reserva" even when it is another car's unbooked draft, beside a booked appointment. The card's
    edit form edits the latest revision, not the car in the block. An operator reading quickly could
    take car 2's address for car 1's inspection. By source, a candidate with no make, model or type
    would have no "Vehículo" line in the block at all. This predates R2. kanban_view.py not changed.
    Recommended after launch: title the block by state, such as "Intento sin confirmar".

  R-3  CONFIRMED WITH ONE EXCEPTION (F-2).
    With flags OFF the change does not alter Flow dispatch or outbound, and booking logic is
    untouched. The full-SQLite comparison and the reader search above support this.
    The exception is the name default. See F-2.

================================================================================
7. FINDINGS
================================================================================
  F-1  LOW, owner decision. Vehicle and locality on a committed card read the candidate.
       lead_booking_details.py:168. Probes k01, k02 FAIL against section 5's wording, on both
       engines. After a candidate is corrected or moved post-booking, the block shows the new
       identity or zone beside the booking's address and seller.
       Why not blocking: it is the same candidate, so no other car's value is shown. The revision
       lines keep the booked vehicle, and a difference there is arguably useful to an operator. The
       behaviour predates R2 and the closeout discloses it.
       Limits: the probes set the columns directly. The candidate writers show no stage guard, but a
       real post-booking turn was not run. thread_revisions holds marca, modelo, anio, tipo and
       zone_group but no zone_detail, so locality cannot be fully taken from the booking.

  F-2  LOW, undisclosed behaviour change. Thread-wide name is no longer the most recently typed.
       Lead with no name; name A on car 1, name B on car 2, A again on car 1. The third submission is
       now "the same fact" in its own attempt and writes nothing, so B stays the thread's newest row.
         R1 tree        live_details  -> A
         This tree      live_details  -> B
         Flow, flags OFF, customer_name  -> B   (booking_flow_service.py:697)
         Flow, switch ON, details_prefill name -> A
         Card                                  -> A
       Probe a09 FAILS on both engines. Same customer, their own typed name, shown on an editable
       required field. It reaches a booking only if they submit it unchanged, and any booking then
       gives the lead a name, which ends the fallback. Ungated, as is V-03.
       A possible correction: read the Flow name from the displayed attempt first.

  L-A  LIMIT. "Attempt = car" means "candidate = car". `_candidate_matches_evidence` matches on make
       and model only, so two physical cars of the same model on one thread are one candidate. By
       source reading, not run: the second car is offered the first's prefill and, once the first
       is booked, the card shows that booking. Existing rule, out of scope. Grounded corrections
       rewrite a candidate in place by design; that is the same car with a corrected label.

  N-5  Confirmed. Rows written before R2 keep v1 ids and read correctly (a08).

================================================================================
8. TESTS AND MUTATIONS ACTUALLY RUN
================================================================================
  Runner ridecheck-crm-test:l4.7b2, read-only tree, --network none for SQLite. Sequential.

  Run                                             Result
  Focused SQLite (vehicle + 4 minimum-launch)     268 passed, 44 subtests
  Vehicle PostgreSQL 16.13, operational shape     65 passed, 18 subtests
  Minimum launch PostgreSQL, same                 19 passed, 12 subtests
  Independent probes, SQLite                      37 passed, 3 failed, 1 skipped (a10), 11 subtests
  Independent probes, PostgreSQL                  38 passed, 3 failed, 11 subtests
  Scratch merged tree, R2A + escaping             220 passed, 797 subtests

  The 3 probe failures are k01, k02 and a09: F-1 and F-2, identical on both engines.
  PostgreSQL ran on the operational schema dump (version_num varchar(128)). That closes the R2 closeout's
  limit that it ran only on a chain-built schema. The dump itself is still dated 2026-11-03.

  Independent mutants: 26, written here, not taken from the implementer's set. 0 not applied. Control
  148 passed.

  Required by the milestone                        Mutant        Suite     Probes
  Thread-wide car fallback                         M01           killed    killed
  Lost candidate filter (card, prefill)            M02 M02b      killed    killed
  Lost cycle filter (card)                         M03           killed    killed
  Lost cycle filter (prefill)                      M03b          killed    survived
  Committed blank filled from a draft              M04           killed    killed
  Draft overwrites committed                       M04b          killed    killed
  Wrong booking for the displayed candidate        M05           killed    killed
  Oldest booking of the car                        M05b          SURVIVED  killed
  Candidate-less booking adopted (R-1 fail open)   M05c          SURVIVED  killed
  Cancelled booking counts                         M05d          SURVIVED  killed
  Completed booking stops counting                 M05e          SURVIVED  killed
  Car claim classed as person (URL, plate)         M06 M06b      killed    killed
  Person claim made car-only (e-mail, name)        M07 M07b      killed    killed
  Missing SUMMARY keys (3 forms)                   M08 M08b M08c killed    killed
  Cross-attempt idempotency, claim identity        M09 b c d     killed    killed
  Duplicate evidence on retry, two live rows       M10 M10b      killed    killed
  Foreign-thread focus shown                       M11           SURVIVED  killed

  Combined: 26 of 26 killed. Candidate suite alone: 21 of 26.
  M05b, M05d and M05e first survived my probes too. I added b01-b03, passing on the clean tree, and
  they kill all three.

  T-1  TEST GAP, no product defect. The five suite survivors mean the correct behaviour is unprotected
       against regression: booking choice by status and recency for the displayed car, R-1, and the
       foreign-thread check on the vehicle. The probes are in scratch and are not part of the candidate.
       Carry them into the suite. The R2A closeout already says R-1 has no test.

  Corrections to my own work, disclosed:
    * s01 first used slots outside the grid, and r1a expected blank where the car still had its
      own draft. Both were probe errors. Neither was reported as a finding; both were corrected and
      rerun, and only final runs appear above.
    * One read-only command was denied. It ended in an unnecessary scratch `rm -rf`. I removed that,
      split the command and reran it.

  Not run, and why:
    Full SQLite and the 85 mutants     validated instead, section 9
    Migration rehearsal                migration, models and preflight byte-identical to R1
    Flow validators                    candidate and generator byte-identical; s01 used the real
                                       payload validator and artifact walker
    28-file PostgreSQL, browser proof  owed on the merged tree

================================================================================
9. RETAINED EVIDENCE VALIDATED
================================================================================
  Location /tmp/claude-0/-opt/96d2af32-9a81-475f-917d-d028419cd80f/scratchpad/out/r2a
  Read by my own script, not the implementer's comparer.

  Tree           worktree = tested snapshot, by recursive byte diff, closeout aside. The closeout's
                 digest 2a4812d7... could not be reproduced because its method is not recorded, so
                 the byte diff is the evidence.
  Baseline       pristine equals 198ed4d: 680 tracked blobs compared by git object id, 0 differ.
  Freshness      last source change 17:16; full runs ended 17:48 and 17:56, mutants 17:38.
  Failing ids    71 vs 71, NEW 0, GONE 0, 0 message differences
  Cases          added 213, removed 0: vehicle 148 (all pass), vehicle PG 65 (all skip)
  Skips          NEW 65, GONE 0, all the PostgreSQL suite
  Totals         62 failed, 7404 passed, 742 skipped, 9 errors, as the closeout states
  Mutants        85 entries, 85 killed, 0 survived, 0 not applied

  Valid, so neither was repeated. The 71 failures are pre-existing and were compared, not assessed.

================================================================================
10. PROTECTED HASHES
================================================================================
  All byte-identical to my R1 tree.
    20261104_vehicle_identity.py          2667ae8c682c595e
    models.py                             5b167b3238c8b332
    schema_preflight.py                   0d26b939fea7e5b0
    CANDIDATE_v7.8 raw                    2102a8ec5f01be41c6671a1d9a3c59cf1f79fbad55354bf623f1db1cd3348354
    CANDIDATE_v7.8 canonical              3b8dc55eef53e260680c0efa4c894fc37bf22cbe0532dceee40a56d5d0370292
    make_v78_vehicle_prefill_candidate    dfa84cd267607655
    TARGET_v7.7                           4ce27122a55f3efd
    flow_contract.py                      a5fba45c644c1dee
    booking_slot_recovery.py (worker)     187d57d13c55b5e3
    kanban_view.py, vehicle               40084f99a196de18e3ad3904632fa56bbd4e0c13251d22a4f642cb4c37a8e7b0
    kanban_view.py, escaping              e555a7f41f8960a09c31e6bdf4d4e00a38656f75adc4f382f6641e63caeabe12

================================================================================
11. MERGE PLAN
================================================================================
  VALID. R2/R2A did not invalidate it.
    Inputs unchanged, and `git merge-file` recomputed on scratch copies: 5 conflicts.
    Retained resolved file: e7a9e334f92d14d4d28ca743aa49259d780658bcaa0593a94109ae4411bee654.
    Escaping changes none of the five R2/R2A paths.
    Tested, not inferred: R2A tree + resolved kanban + escaping files and tests = 220 passed, 797
    subtests. That is R1's 174 plus the 46 tests added since.
  The real merge was not performed.

================================================================================
12. ISOLATION
================================================================================
  Candidate   HEAD 198ed4d, index empty. Only this audit added.
  Escaping    HEAD 198ed4d, index empty, read only. Scratch copies were taken from it.
  Live        fingerprint identical before and after, and identical to the end of R2A: containers,
              images, start times, restart counts, environment hash, Compose and .env hashes, three
              worktrees, v7.7 and r3. No live database queried, no exec into a live container.
  Memory      9 gated runs, each at MemAvailable >= 1536 MiB with no competing check. Lowest at a
              gate 1948 MiB. Lowest during any run 1240 MiB, during the mutants. 500 MiB never
              reached. One heavy check at a time.
  Disposables rvv-pg and rvv-net removed by exact name, 0 rvv-* containers or networks remain.
  Evidence    /tmp/claude-0/-opt-ridecheck-crm-vehicle-identity-recovery/827c4419-eae3-4cfb-ae08-02ec30dc5051/scratchpad/r2a
              Not part of the candidate.

================================================================================
13. REMAINING PREREQUISITES
================================================================================
  None of these is proven by this audit.

  Owner, before deployment
    1. F-1: accept the candidate as the source of Vehículo and Zona on a committed card, or correct.
    2. F-2: accept the name default, or correct.

  On the merged tree
    3. Carry b01-b03, r1a, r1a2, r1b and the foreign-thread case into the suite (T-1).
    4. Full SQLite, vehicle suites, all PostgreSQL-gated suites, browser escaping proof, migration
       rehearsal.

  Migration
    5. Fresh schema-only dump and rehearsal (P-4).
    6. Window plan. The old image cannot restart at the new head, and rollback gets harder once any
       plate or mileage is stored (M-1, D-2).

  Meta and handset: all unproven, deferred to the controlled cutover milestone
    7. Owner Ejecutar: Form init-values, TextInput pattern and helper-text, object details_prefill,
       and the 7 unproven properties (P-1).
    8. Handset proof with a real slot conflict: {} and partial prefill, blank as "" or absent, what
       the customer sees when pattern fails (P-2). Whether Meta omits blank inputs remains unknown;
       V-02 makes booking independent of the answer locally.
    9. N-3: switch ON with an open v7.7 session returns two undeclared keys. Meta's response unknown.
   10. N-4: switch ON, a v7.7 form submitting a blank e-mail can clear a value it never displayed.
   11. Order of Flow and switch (P-3, D-1), and the return-cutoff migration re-parent (P-5).

  Standing: L-1 to L-3, E-3 to E-6 from the R1 audit, L-A and R-2 from this one.
  The second car in most scenarios is still created by re-pointing the offer and focus, not by real
  Conversation Engine turns. c09 uses a real second offer and booking, but the focus move is still set
  directly.

================================================================================
14. RECOMMENDED NEXT MILESTONE
================================================================================
  PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CONTROLLED-MERGE

  Scope: commit the candidate, apply the escaping candidate with the five documented conflict
  resolutions (resolved hash e7a9e334...), add the T-1 tests, and run prerequisite 4 on the merged
  tree. If the owner wants F-1 or F-2 corrected, do that first as a bounded correction: each touches
  one reader. No build, deployment, migration, flag change or publication.

STOP.
