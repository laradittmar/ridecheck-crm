PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R1-INDEPENDENT-REVIEW

Date: 2026-11-05. Independent review. This file is the only write to the candidate worktree.
Nothing staged, committed, merged, pushed, built, deployed or migrated live. No flag changed, no
Flow published, Meta not contacted, no message sent. The agenda worktree was read only. All values
used are synthetic.

================================================================================
0. VERDICT
================================================================================
CORRECTION REQUIRED

  One blocking product defect:

    V-01  A plate and mileage typed for one car appear on the CRM card against a different car on
          the same thread. It survives a committed booking: car 2's revision holds NULL for both
          fields, while "Datos de reserva" on its card shows car 1's plate beside car 2's make and
          model. The requirement that these values do not cross candidates fails.

  Two further defects must be closed before the switch is turned on. They are cheapest to fix in
  the same correction:

    V-02  If Meta omits blank optional inputs, SUMMARY is answered without two keys the candidate
          declares. That is the most common customer path: both new fields left blank.
    V-03  Details that repeat an earlier vehicle's values are not re-bound, so a recovered customer
          on a later vehicle gets those fields back empty.

  What held up: the migration is sound on the operational schema shape; mileage normalization is
  correct; winner and loser stay separate; recovery prefill does not leak; retries are idempotent;
  flag OFF changes nothing; the Flow regenerates byte for byte; the escaping merge reproduces
  exactly. No blocking migration defect. No customer-to-customer crossing was found.

  Live exposure of V-01 today: none. The published v7.7 collects neither field, so no such rows
  exist until v7.8 is published. The correction is narrow and does not reopen the migration, the
  Flow structure or the escaping merge.

================================================================================
1. SCOPE AND MANIFEST
================================================================================
  Worktree   /opt/ridecheck-crm-vehicle-identity-recovery
  Branch     integration/prelaunch-vehicle-identity-recovery
  HEAD       198ed4d6a7157230a616063094e2d50fb9623836, unchanged. Index empty.
  Paths      26 before this audit: 18 modified, 8 untracked.

  Reconciliation
    * The 25-path list in the 2026-11-04 closeout (§13) matches exactly. The only other path is
      the 2026-11-05 R1 closure closeout. No unexplained path. The proposed commit list needs both
      closeouts and this audit added.
    * Diff read in full: 18 tracked files, 349 insertions, 33 deletions, plus the new service,
      migration, generator, candidate Flow and two test files.

  Unchanged since R1 verification
    * Retained frozen tree digest recomputed: aa75cf1ff5d88702..., equal to the recorded value.
    * Recursive diff of worktree against that tree: the two closeouts only.
    * Newest candidate source file 2026-11-04 17:43, before the 2026-11-05 evidence.
    * 2026-11-04 closeout unchanged, d3546a67f107aa48.

  Secrets and identifiers in added material (closeouts excluded from the file scan)
    Meta token, API key, private key, SMTP or literal password, server IP, real email domain, wamid,
    private build path, scratch path: 0 each.
    Phone-shaped: 2, both the placeholder `__example__` inherited from the published Flow.
    Flow asset id: appears only in a line already in the tracked doc. Generated files: 0.
    backend/app/services/__pycache__ is git-ignored, as the closeout stated.

  Flow artifacts
    TARGET_v7.7 raw 4ce27122a55f3efd..., no diff against 198ed4d. r3 likewise.
    v7.8 exists only as an untracked CANDIDATE file. Compose and the live backend environment do
    not contain BOOKING_FLOW_DETAILS_PREFILL_ENABLED.

================================================================================
2. FINDINGS
================================================================================

2.1 BLOCKING PRODUCT DEFECTS

  V-01  HIGH. Vehicle identity crosses candidates on the CRM card. CONFIRMED by execution.
    Where   backend/app/services/lead_booking_details.py:138 and :147
    What    The vehicle shown is the current focus candidate. The plate and mileage shown are the
            thread's live draft rows, whatever candidate they are bound to. They are dropped only
            when bound to the latest booking's candidate.
    Measured on the unmodified candidate:
      Q03   car 1 draft, focus moves to car 2: card data is car 2 with car 1's plate and mileage.
      Q03b  the customer then submits car 2 with both fields blank: car 1's plate still shows. The
            blank correctly clears nothing (they never saw it), so nothing removes it either.
      Q10   car 1 loses a race, car 2 is booked with both fields blank:
              revision (kilometraje, dominio) = (NULL, NULL)
              card: "Fiat Uno (Auto)" with car 1's plate under "Datos de reserva"
    Why it blocks
      * The review requires these values not to cross candidates and to appear on the correct
        card. An operator can go to inspect a car holding the wrong plate.
      * The module docstring says the draft claims are "shown for the attempt they belong to".
        That is not what the code does.
      * It is not covered by accepted debt F-14. F-14 concerns a person's details persisting
        "until the customer submits new ones"; here a new submission does not remove the value,
        and both fields are new in this candidate. The closeout scoped the prefill read for exactly
        this reason (§7) but left the card read unscoped.
      * Not behind the switch, and not fixed by the escaping merge.
    Tests   i01-i05 use one vehicle per thread, so none can see it.
    Direction  Show these two claims only when bound to the candidate the card displays, using the
            scope `prefill_details` already applies. Add a test for Q03, Q03b and Q10.

2.2 BLOCKING MIGRATION DEFECTS

  None. See section 4.

2.3 BLOCKING FLOW / PUBLICATION PREREQUISITES

  V-02  MEDIUM, could become HIGH. SUMMARY can omit declared keys. CONFIRMED locally; whether it
        happens depends on Meta.
    Where   backend/app/services/booking_flow_service.py:866 and :756
    What    The two keys are returned only if the request contained at least one of them. If Meta
            omits a blank optional `${form.*}` value, a customer leaving both blank gets a SUMMARY
            without `vehicle_mileage` and `vehicle_plate`.
    Measured  Q01, switch on, both keys absent:
            "SUMMARY payload invalid — declared but not supplied: vehicle_mileage, vehicle_plate"
            That is the project's own `validate_screen_payload(..., candidate=True)`.
    Why it matters  `flow_contract.py` states that a declared key the backend omits is one the Flow
            renders as missing. SUMMARY is the screen that sends confirm_booking, so the possible
            failure sits on the most common path. The closeout's open question 6.3c considered
            only clearing a prefilled value, not this. Test g08 pre-fills "" for blanks, so it never
            exercises omission (E-2).
    Direction  With the switch on, always return both keys. The handset answer then no longer
            decides whether booking works.

  V-03  MEDIUM. Prefill loses values that did not change. CONFIRMED by execution.
    Where   backend/app/services/booking_flow_details.py:212, read at :365
    What    A value equal to the live row is skipped as "the same fact again", so that row stays
            bound to the earlier candidate or cycle. `prefill_details` then correctly refuses it.
    Measured  Q02: car 1 details, then car 2 with the same personal details and a new plate. The
            refreshed Flow offers only name, mileage and plate. Email, address and seller fields
            come back empty, although the customer typed them in this attempt.
    Severity  Fails safe: no wrong value is shown and no booking is affected. The customer retypes.
            A first vehicle on a thread is unaffected, and that is what the suites cover. It does
            not meet "need only choose a new slot" for a later vehicle on the same thread.
    Direction  Treat a value as the same fact only within the same candidate and cycle, keeping the
            replay protection (s02, g07c) intact.

  P-1  Meta Ejecutar has never seen this artifact. Form `init-values`, TextInput `pattern` and
       `helper-text`, and an object `details_prefill` carry no Meta precedent. The validator
       reports exactly 7 unproven properties.
  P-2  Handset proof with a real slot conflict, covering closeout §6.3 a-d, plus three cases that
       list omits:
         * `details_prefill` = {} and partial objects. Most first-time customers receive one of
           these, so a failure here would break ordinary bookings, not just recovery.
         * Whether a blank optional input arrives as "" or absent, as it decides V-02.
         * What the customer sees when `pattern` fails, for example `85,000`. No `error-message`
           is set, so the text is Meta's, in a language not yet known.
  P-3  Flow and switch must change together (D-1), and V-04 makes this concrete. Q04: with the
       switch on and a v7.7 form, a blank email cleared a draft value that form could never have
       displayed. That contradicts "cannot clear a value they never saw". Harm is limited to the
       draft, but sessions opened on v7.7 around cut-over need a decision.
  P-4  Fresh schema-only dump and rehearsal immediately before any live migration. See 4.4.
  P-5  Re-parent 20261102_professional_operational_origin, and set EXPECTED_HEAD to it, if the
       return-cutoff work lands. Stated by the closeout, not re-examined here.

2.4 NON-BLOCKING LAUNCH DEBT

  M-1  LOW-MEDIUM. "Safe for the image below it" is only partly true. Measured: 198ed4d models
       read and insert correctly on the upgraded schema, but 198ed4d's preflight reports
       "Alembic version is ['20261104_vehicle_identity'], expected exactly [...]" and
       `require_declared_booking_schema` raises at startup. So once migrated, the old image keeps
       running but cannot restart. Once any plate or mileage exists, the downgrade refuses, so
       rolling back the image needs the archive procedure. This matches earlier migrations; it
       belongs in the D-2 window plan.
  L-1  LOW. `normalize_dominio` upper-cases before checking, so non-ASCII input can become ASCII:
       `ß1` is stored as `SS1`, `ﬁ1` as `FI1`. Measured. Reachable from API and CRM only; the Flow
       pattern refuses it.
  L-2  LOW. By reading, not run: a refused CRM value returns a bare 422 and the operator leaves the
       form. No data is changed (c04).
  L-3  Stated by the closeout, confirmed by reading: `_converged_flow_book` does not carry these
       fields; `backend/openapi_dump.json` is stale; draft plates sit in clear in evidence rows.

2.5 EVIDENCE AND TEST GAPS

  E-1  `test_pgm_00` fails on the operational shape: it asserts `alembic_version.version_num` is
       varchar(32), and the operational dump has varchar(128). The 25-character id fits both. No
       product fault, but it proves the closeout's PostgreSQL runs did not use that shape.
  E-2  g08 does not test omission. It should, for V-02.
  E-3  My mutant X07 (thread filter removed from `prefill_details`) SURVIVED, and the closeout's
       52 have no such mutant. Candidate ids are globally unique, so the candidate filter subsumes
       it: harmless defence in depth. But r12 does not prove the thread filter, as the closeout's
       list implies; isolation between customers rests on the candidate binding.
  E-4  My mutant X03 survived and is equivalent: the replace runs only after a regular expression
       that cannot match a comma. Not a gap.
  E-5  X17 (Flow mileage required) was first killed by the hash pin, because the run stops at the
       first failure. Behaviour was checked separately by my pattern probe.
  E-6  The closeout's 7256 and 7358 passed are pytest totals. The junit files show 7255 and 7357;
       one case on each side both passes and errors. Same on both sides, no effect.

2.6 UNRELATED PRE-EXISTING

  * 71 SQLite and 29 PostgreSQL failing identities on 198ed4d. Compared only, not assessed.
  * The candidate alone still has the raw agenda name and address sinks (F-1). It is not
    independently deployable.

================================================================================
3. TESTS AND EVIDENCE ACTUALLY EXECUTED
================================================================================
  All sealed test image ridecheck-crm-test:l4.7b2, trees mounted read only, --network none or a
  private --internal network. 9 heavy runs, strictly sequential, each behind the same gate:

  Run                        Gate MiB  Lowest MiB  Result
  focused SQLite                 2374        2189  102 passed
  my probes, SQLite              2333        2162  48 passed, 6 failed (findings below)
  card after booking             2290        2191  Q10 failed, confirms V-01
  PostgreSQL suites              2228        1755  see below
  rehearsal, first run           2049        1955  INVALID, see 7
  rehearsal, second run          2036        1922  section 4
  scratch merge, resolved        2025        1835  174 passed, 786 subtests
  scratch merge, vehicle side    2035        1927  agenda suite: 68 failed, 24 passed
  my 18 mutants                  2039        1843  16 killed, 2 survived, control 102 passed

  1536 MiB gate met every time; lowest sample 1755 MiB; 500 MiB stop never reached. Neither
  threshold was lowered. No competing pytest, build, browser or rehearsal.

  PostgreSQL 16.13, disposable, restored from the operational schema-only dump:
    test_vehicle_identity_recovery_pg   18 passed, 1 failed (E-1), 7 subtests; all 6 pgv tests pass
    test_minimum_launch_pg              19 passed, 12 subtests
    my PG run                           43 passed: the whole recovery, identity and persistence
                                        classes re-run on PostgreSQL, plus Q06-Q09.
    This closes the closeout's limit L-2: scope tests r10, r11, r12, r09b and r09c passed on real
    PostgreSQL. pgv_02 and pgv_02b read prefill from a separate OS process.

  Normalization, independent table, passed:
    Accepted  85000, 85.000, 1.234.567, surrounding spaces, 0, 2147483647, 2.147.483.647, integer.
    Blank     "", spaces, tab and newline, NBSP, None: all NULL, never 0.
    Refused   85,000  85,5  85.5  85.00  85.0000  85000.0  1e5  +85000  -85000  -1  85000km  km
              12a  85..000  .000  85.  085.000  0.000  000.000  00.000  1.23.456  1,234,567
              1.234,567  85 000  2147483648  2.147.483.648  99999999999  Arabic and full-width
              digits, booleans, floats, lists, dicts.
    CRM: the input is type="text", stored as an integer, reopens as digits (c01, c01b, c01c, c05
    passed; mutant G10 killed; form read directly).

  End to end, passed on SQLite and PostgreSQL:
    Q06  no winner value on any loser row and the reverse; winner revision 12000/ZZZ999; loser has
         no Revision and no ThreadRevision before rebooking.
    Q07  fallback books the loser's own values once; a repeat confirm gives the same screen and
         exactly one revision.
    Q08  API create stores 1.234.567 as 1234567 and normalizes the plate; omitted is NULL.
    Q09  flag OFF: no `details_prefill`, payload is the published contract, blank is not a clearing.
    Q05  (SQLite) a replay on the old offer token raises BookingTokenError and changes nothing.

  Mutants: 18 of my own, separate from the closeout's: 4 normalization, 6 scope and clearing, 1
  switch, 4 persistence and optionality, 2 CRM, 1 Flow, plus agenda. Survivors explained in E-3, E-4.

  Retained evidence validated rather than rerun (no staleness, tree digest equal):
    Full SQLite   recomputed from the XML: 8003 vs 8124 cases, failing 71 vs 71, NEW 0, GONE 0, 0
                  message differences, 121 added cases, exactly the two new suites, 0 removed.
    Focused       102 case ids identical to my run.
    Mutants       52 KILLED, 0 survived or not applied.
  Limit: those were judged against the suites as they stand, which is why they could not find V-01.

================================================================================
4. MIGRATION RESULT: PASS
================================================================================
  4.1 Base. Schema-only dump of operational crm_test taken by the 2026-11-03 review session (30
  tables, 0 COPY or INSERT, PostgreSQL 16.13), restored to a disposable server on an internal
  network with tmpfs storage. Stamped at 20261027_w1f_conversation_memory, then the real chain to
  20261103_slot_recovery_job, then 500 leads and 5000 revisions, all synthetic. This is the
  rehearsal the closeout could not do.

  4.2 Result
    upgrade              CREATED. Catalog diff is exactly two lines:
                           kilometraje integer / dominio character varying(16)
                         Both nullable, no default, no index.
    existing rows        5000 rows, hash identical before and after; 0/0 of 5000 hold a value.
    repeat upgrade       no-op
    stamp back, empty    ADOPTED_EMPTY_CANONICAL
    limits               2147483647 and 16 characters accepted; 2147483648 and 17 refused by the
                         database. Parser ceiling equals the column.
    stamp back, values   VERIFIED_CANONICAL
    downgrade with data  REFUSED with both held, and with only one held; nothing dropped
    clean downgrade      DROPPED. Catalog identical to pre-upgrade, row hash identical.
    re-upgrade           CREATED, catalog identical to the first upgrade
    partial, empty       CREATED (adopting). Differs only by column order.
    foreign column       downgrade REFUSED

  4.3 Refused, version and shape unchanged: bigint, text, varchar(20), DEFAULT 0, NOT NULL,
  UNIQUE, CHECK, foreign index, partial holding data.
    Preflight: EXPECTED_HEAD 20261104_vehicle_identity. Head: 0 problems. Below head: refused.
    ORM and database agree on both columns. See M-1 for the old image.

  4.4 Limit. The dump is two days old and predates the deployment now running. The live database
  was neither read nor touched, so I do not know its current head; the running image's name
  suggests 20261103. The chain from the dump is proven; the live shape today is not. See P-4.

================================================================================
5. FLOW RESULT
================================================================================
  Proven locally
    Generator    two runs byte-identical to the candidate; raw 2102a8ec5f01be41..., canonical
                 3b8dc55eef53e260.... Refuses v7.6: exit 2, nothing written.
    Validators   plain and pinned graph: 0 routing, 0 error, 0 render, 0 unproven.
                 --precedent v7.7: 0 routing, 0 error, 0 render, 7 unproven.
    Controls     v7.7 clean; known-bad v7.6 exit 1. compare_flows exit 0.
    Structure    routing_model identical to v7.7; no `init-value`; Flow version 7.3.
    Placement    DETAILS, after Link de la publicación, before the Footer; both `required: false`.
    Carriage     APPOINTMENT navigate carries details_prefill. DETAILS prepare_summary sends both
                 from `${form.*}`. SUMMARY confirm_booking sends both from `${data.*}`.
    init-values  all 8 keys equal the 8 Form input names; all typed string, all TextInput.
    Pattern      accepts blank, 85000, 85.000, 1.234.567, 999.999.999. Refuses 85,000, 85.5,
                 85.00, 085.000, 0.000, signs, letters, 85..000, spaces and over 999.999.999.
                 Every accepted value is accepted by the server. The stricter client ceiling is
                 harmless. Tested with Python's regular expressions, not Meta's engine.
    Spanish      "Kilometraje", "Dominio / patente", "Opcional. Ej.: 85000 o 85.000." and
                 "Opcional. Ej.: ABC123 o AB123CD." are clear and correct.

  NOT locally provable: publication prerequisites, not results
    Whether Ejecutar accepts the artifact; whether an object survives navigate; {} and partial
    objects in init-values; blank "" or absent; pattern on device; helper-text rendering;
    pattern error text. Text input also costs the numeric keypad (owner-accepted trade-off).
    Meta's public documentation was not re-read: this review contacted nothing at Meta.

================================================================================
6. MERGE COMPATIBILITY RESULT: REPRODUCED
================================================================================
  Read only: /opt/ridecheck-crm-agenda-escaping, base 198ed4d, its R2 closeout and its independent
  review (READY FOR CONTROLLED MERGE).
    agenda kanban_view.py    e555a7f41f8960a0..., matches, reconfirmed at the end
    vehicle kanban_view.py   40084f99a196de18..., matches, reconfirmed at the end
    git merge-file           exactly 5 conflicts, all in render_calendar_page
    resolution, derived independently from the conflict text:
      1  vehicle join (dominio, kilometraje) plus the agenda's _esc_txt address
      2-5 agenda name line
    resolved                 e7a9e334f92d14d4d28ca743aa49259d780658bcaa0593a94109ae4411bee654: match
    content                  parses; 4 escaped name sinks, 0 raw; 4 _aveh sinks escaped; join
                             present once; render_table_page absent
    scratch merged tree      174 passed, 786 subtests: 102 vehicle + 72 escaping
    vehicle side wholesale   68 failed. Taking it wholesale is wrong, as documented.

  The V-01 correction touches lead_booking_details.py, which the escaping candidate does not
  change, so it should not disturb this result. If kanban_view.py changes at all, the expected
  hashes above no longer apply and must be re-derived.

  Still owed on the real merged tree: full SQLite, PostgreSQL-gated suites, browser proof,
  migration rehearsal. Nothing here replaces them.

================================================================================
7. REVIEW CONDUCT AND LIMITS
================================================================================
  * First rehearsal invalid. My seed insert failed, so every data-dependent step ran on an empty
    table. I discarded it, fixed the seed and reran everything on a new database. Section 4 reports
    only the second run.
  * One command denied. My first rehearsal command was refused by the permission layer. It held a
    stray `rm` outside the scratchpad that I did not need; I removed that and put the script in a
    file. It targeted the disposable server only.
  * Q02, Q03 and Q10 create the second car by re-pointing the issued offer and focus, as the
    candidate's r10 does. They are not a real Conversation Engine turn. The card logic they exercise
    depends only on those rows.
  * Q04 models a v7.7 payload; it is not a real v7.7 session.
  * Not run: full SQLite, the 28-file PostgreSQL comparison, the browser proof, and PostgreSQL
    suites on the merged tree.

================================================================================
8. LIVE ISOLATION
================================================================================
  Fingerprint before and after this review is identical, using the closure's read-only method:
  backend, postgres and n8n container id, image, start time and restart count; backend environment
  hash; Compose and .env hashes; HEAD, status and diff of /opt/ridecheck-crm,
  /opt/ridecheck-crm-integration and /opt/ridecheck-crm-minimum-launch; v7.7 and r3 hashes.
  The live database was not queried. No `docker exec` into any live container.
  Disposables rvv-pg, rvv-net, rvv-sq, rvv-t, rvv-al and rvv-mut: all gone, removed by exact name
  or --rm; 0 remain. No image built.
  Candidate at end: HEAD 198ed4d, index empty, source identical to the frozen tree.
  Agenda at end: HEAD 198ed4d, kanban hash unchanged, no write.
  Evidence is in this session's scratchpad and is not part of the candidate.

================================================================================
9. REMAINING PREREQUISITES
================================================================================
  Before controlled merge
    1. Fix V-01, with tests for Q03, Q03b and Q10.
    2. Recommended in the same correction: V-02 and V-03, with an omission test (E-2).
    3. Correct `test_pgm_00` so it holds on the operational shape (E-1).
    4. Focused re-review of those changes.

  On the merged tree
    5. Full SQLite, PostgreSQL-gated suites, browser proof, migration rehearsal.

  Before migration
    6. Fresh schema-only dump and rehearsal (P-4).
    7. Migration window plan including restart and rollback behaviour (M-1, D-2).

  Before publication or switch ON
    8. V-02 and V-03 closed, if not already.
    9. Owner Ejecutar (P-1), then handset proof including {} and partial prefill (P-2).
    10. Owner decision on the cut-over, including open v7.7 sessions (P-3, D-1).

================================================================================
10. RECOMMENDED NEXT MILESTONE
================================================================================
  PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE-R2-CARD-SCOPE-CORRECTION

  Scope: V-01, V-02, V-03, E-1 and E-2 only. No change to the migration, the Flow artifact, the
  recovery worker or the agenda sinks. Then a focused independent review. If that passes, this
  candidate is ready for controlled merge. No frozen launch gate is reopened.

STOP.
