PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-ESCAPING-CONTROLLED-MERGE-INDEPENDENT-REVIEW

Date: 2026-11-06. Independent review. No product code changed. Nothing staged, committed, pushed,
built, deployed or migrated live. No flag changed, no Flow published, Meta not contacted, no message
sent, neither live database queried, no exec into a live container. All values used are synthetic.
One write beyond this audit reached the candidate worktree by my own mistake: see section 12.

================================================================================
0. VERDICT
================================================================================
READY FOR CONTROLLED COMMIT

  This is not READY FOR DEPLOYMENT, PUBLICATION or ENABLEMENT.

  Every condition of section 12 of the milestone is met. No blocking finding.

    Manifest           45 paths reconciled exactly; 29 + 12 byte-identical, 1 merged, 3 new; all 44
                       hashes in the closeout match
    Merge              5 conflicts reproduced; resolved from the rule, byte-identical e7a9e334...
    Tested tree        digest bbdd0fa1... reproduced: the retained runs are of this exact tree
    SQLite             71 vs 71, NEW 0, GONE 0, 0 message differences, +305, 0 removed
    PostgreSQL         62 vs 62, NEW 0, GONE 0; 34 files; +75; no candidate-only regression
    Behind stale pins  24 hidden test bodies executed on both trees: no regression was hiding
    Flags OFF          14-step v7.7 session identical on 198ed4d and merged, refusal included
    T-1                valid; 8 of 8 of my own mutants killed by the T-1 file alone
    C-1                accepted: 48 killed + 4 proven equivalent. Removal NOT required before commit
    C-2                accepted: Chromium is behavioural. The in-tree proof alone kills both sinks
    Migration          sound; independently rehearsed
    1400 MiB gate      NON-INVALIDATING PROCESS DEVIATION
    Independent mutants  34 of 34 killed, plus 3 of 3 client sinks in Chromium

  The owner should know:
    * My review left one git-ignored bytecode file in the candidate. No tested byte changed and it
      cannot reach the commit, but it breaks "only the audit". The permission layer refused its
      removal. Exact command in section 12.
    * The closeout's list of vacuous tests is incomplete: two more pass with no database at all
      (section 7).
    * The PostgreSQL proof of the outbound recipient lock remains absent. Inherited, not a merge
      effect, but it is a real gap in a security invariant (section 7).
    * The broad browser harness can silently lose a /control panel to its own SQLite race. It did
      so in both of my exact-tree runs (section 5).

================================================================================
1. MANIFEST RECONCILIATION
================================================================================
  Computed from git and the worktrees by my own script, not from the closeout.

  Worktree  Branch                                           HEAD     Index  Paths
  Target    integration/prelaunch-minimum-launch-merge       198ed4d  empty  45 (20 M, 25 ??)
  Source A  integration/prelaunch-vehicle-identity-recovery  198ed4d  empty  30
  Source B  fix/prelaunch-agenda-customer-data-escaping      198ed4d  empty  13

  Contribution
    A   30 paths: 29 byte-identical in the target, backend/app/ui/kanban_view.py merged
    B   13 paths: 12 byte-identical in the target, backend/app/ui/kanban_view.py merged
    A and B share kanban_view.py and nothing else. No path of either is missing from the target.
    Added only by the merge: tests/test_vehicle_identity_t1_regression.py (93da71e13f1332f4),
    tests/test_vehicle_identity_t1_regression_pg.py (e19a4a414d287ea3), the closeout
    (e8c39ca17c81a04c).
    29 + 12 + 1 + 3 = 45. All 44 hashes listed in closeout section 3 match. No unrelated path.

  Tree identity. The closeout's digest method is retained (bin/treehash.sh). Excluding the closeout:
    bbdd0fa1d28ac7eebbf55d1d0d8994f73dc2c8341cf4ddd380b40f5ba66d5849, the digest recorded before and
    after the merged full run. The retained results are therefore of this tree.

  Baseline. git archive of 198ed4d digests to 766aaef6d17ee38f..., equal to the retained pristine
    tree. pristine_h differs only by carrying tests/escaping_browser_proof.

  Secrets and live identifiers. All 45 paths, 570052 added characters. Token, key, DSN, JWT,
    private-key, e-mail and server-address shapes: 0 hits. 12 sensitive values from .env and the
    backend environment, compared in memory, names only: 0 occur. 2 phone-shaped hits, both the
    `__example__` value already in the committed v7.7 baseline, ending 000, synthetic.
  Scratch evidence in the tree: none (no XML, log, report, cache), other than section 12.
  git diff --check: clean. Stash list empty.

  Sources unchanged. All-file digests equal the merge's final fingerprint, ignored files included:
    A 5b25f5252bfbd9d1...   B 1ddaee2776e752ba...
    kanban_view.py: A 40084f99a196de18...e7b0, B e555a7f41f8960a0...be12, as expected.

================================================================================
2. CONFLICT RESOLUTION, INDEPENDENTLY
================================================================================
  git merge-file on raw inputs (vehicle ours, 198ed4d base, escaping theirs): rc 5, 5 conflicts, all
  in render_calendar_page. I wrote my own line-oriented resolver from the accepted rule, without the
  merge's resolve.py. It asserts each conflict's shape.

    1  join + address   vehicle's 3-line join, the escaping side's address line
    2  calApptName      escaping side (dead, section 4)
    3  calApptName      escaping side (dead, section 4)
    4  calMonthApptName escaping side
    5  calWeekApptName  escaping side

  Result e7a9e334f92d14d4d28ca743aa49259d780658bcaa0593a94109ae4411bee654, byte-identical to the
  target on the first attempt. The file follows the rule; it does not merely match a hash.

  Required property                       Result
  parses, no conflict marker              yes, 0
  vehicle join once                       1, and one `def _aveh`
  dominio and kilometraje retained        yes
  address escaped                         2 `_esc_txt(r.direccion_texto)`, 0 raw
  live customer-name sinks                all escaped: day (3 sinks: name, data-rev-name, aria-label),
                                          month, week, data-search. 0 raw `_txt(_aname`
  vehicle escaped exactly once            `_aveh` returns plain text; every sink escapes once
  render_table_page deleted               0 definitions; render_revisions_table_page present

  Clean hunks were checked too, because a conflict marker shows only textual collision. Everything
  vehicle added to kanban_view.py escapes at its sink: `_vehicle_identity_line`, the details block,
  `_t`, `_esc_val`. `cobroVeh` and `cobroWho` read the dataset through textContent. No raw sink
  was added on either side of the other's work.

  Composition probe, new here: the accepted suites' agenda fixture has no plate or mileage at all,
  so I gave the real renderer hostile `dominio` AND hostile `kilometraje` together with hostile
  customer data, 14 payloads. Structure changed 0 of 14. The value read back exactly once at
  56 of 56 sinks (agendaApptVehicle, calWeekApptMeta, calMonthApptMeta, data-rev-veh).

================================================================================
3. T-1
================================================================================
  Both files read in full. They protect behaviour, not source formatting: they drive the real
  Flow service and real `render_lead_card`, and assert on `LeadBookingDetails`, the rendered block
  and database rows. Nothing inspects source text. The PostgreSQL file subclasses the same three
  classes onto the real server, so it runs the same assertions rather than a weaker copy.

  Required behaviour                                   Test
  Latest qualifying booking of the displayed candidate t1_01, and later cancelled in t1_02
  Cancelled excluded                                   t1_02, t1_03
  Completed included                                   t1_04
  Candidate-less fails closed with a focus             t1_05
  Candidate-less shown without a focus                 t1_06
  Own draft instead of an unrelated booking            t1_07, and another car's draft in t1_08
  Foreign-thread focus supplies nothing                t1_09, t1_10

  No production file changed for T-1: every production path in the manifest is byte-identical to A
  or B, kanban_view.py aside, and that is fully accounted for by section 2.

  Run      SQLite 10 passed (in the 419 of section 6). PostgreSQL 10 passed.

  My own mutants of lead_booking_details.py, written independently, T-1 file alone:
    T01 completed dropped   T02 cancelled admitted       T03 oldest booking wins
    T04 thread's latest used for the displayed car       T05 candidate-less hidden with no focus
    T06 foreign-thread guard removed   T07 thread-wide car draft   T08 first booking of the car
  8 of 8 killed. T-1 closes the R2A review's five survivors.

================================================================================
4. C-1: DEAD CODE MUTANTS
================================================================================
  DECISION: 48 behaviourally killed + 4 proven equivalent IS SUFFICIENT. The dead function need not
  be removed before commit. Equivalent mutants are NOT counted as killed.

  Unreachable, on both trees (AST):
    198ed4d  def at 4467; merged  def at 3779. Nested in render_calendar_page, so it cannot be
    imported. Name references 0, attribute references 0, string references 0. The only dynamic
    calls in the enclosing function are getattr on data objects. No locals, vars, eval, exec,
    globals. Not returned.
  Unreachable at runtime: a profiler over day, week and month rendering saw 0 entries.
  The four mutations cannot change behaviour. E1.1, E1.2, E2.1 and E2.2 applied TOGETHER, 14 hostile
    pages: every line identical to the unmutated tree except `buildStamp`, the render timestamp.
    Control: two runs of the unmutated tree differ in that same line and no other. In Chromium, my
    run of the two name sinks together, and the retained run of each of the four alone, give 0
    executions over the broad proof.
  Live sinks exist elsewhere and are tested: agendaApptName, agendaApptVehicle, data-rev-name,
    data-rev-veh, aria-label and the address link, in `_operational_day_view`. My independent E01-E06
    are killed by HostileStructure::test_ae01, source inventories deselected. Retained D1-D6 agree,
    with 36 to 108 Chromium executions each.
  The hash correctly preserves the accepted resolution in the dead function: all four dead sinks use
    `_esc_txt`, as both accepted audits require.

  Mutant ledger reconciled: 46 first-pass entries + D1-D6 = 52 valid, after E1.5 was rewritten and
  E2.8 replaced by E2.8b. 46 pytest + 2 Chromium = 48, 4 equivalent, 0 survived. Exact.

  Why not remove it now: removal protects nothing at runtime and changes a resolved file two reviews
  accepted. Remove it after launch. test_ae15 keeps a source pin on it until then.

  The closeout's reading "four escaped name sinks means two live and two dead" is correct. The
  escaping audit's "slot card" wording is the one that missed this.

================================================================================
5. C-2: CHROMIUM-ONLY KILLS
================================================================================
  DECISION: Chromium execution IS a behaviourally relevant test. Both kills are accepted. The broad
  proof staying in scratch is a LOW TEST-MAINTENANCE GAP, acceptable for this commit, with a
  post-launch follow-up. It does not block.

  Why it is behavioural: these sinks are client-side JavaScript, so a real engine running the real
  page against hostile stored data is the most direct test available. A Python test could only pin
  text. The detector records the payload's own token, which names the column, so a kill identifies
  the sink rather than merely a change.

  The two mutations, reproduced here:
    B27   picker `waEsc(lead.nombre ...)` removed. 153 executions, 36 live, tokens *~leads.nombre
          on /whatsapp/thread pages. Same counts as retained E2.7.
    B27a  `waEsc(lead.apellido ...)` removed, not previously tested. 153 executions, ~leads.apellido
    B28b  /control path back into inline onclick. 1 execution, token js1~whatsapp_messa.path_id:
          the same single token 198ed4d itself produces. Same as retained E2.8b.

  B28b also proves the `data-path` change is necessary: the mutant keeps the new `esc()` that encodes
  `'`, and the attribute still decodes before the handler compiles.

  Could the zero be an empty page or disabled JavaScript? No.
    * The harness result exists only if JavaScript ran: the driver itself writes it.
    * Merged: 0 client errors on every page. Panels equal 198ed4d count for count.
    * /control afterFilter is 2 on merged and 66 on 198ed4d and on the mutant: the fixed link really
      filters on a hostile path, while the unfixed one throws. That is positive evidence, not a zero.
    * Detector controls: 198ed4d 1474 executions, 2540 live. Mutants fire on the same fixture.
    * Sealed: `Network is unreachable` on an external fetch in the namespace.

  In-tree proof, run by me, unmodified:
                pages  no result  executions  img  script  handlers
    198ed4d        25          0         173  148      67       174
    merged         25          0           0    0       0         0

  NEW, and it corrects the closeout. C-2 says the broad proof is what kills these sinks. The IN-TREE
  proof kills both on its own: B27 10 executions, B28b 1. So the committed harness already protects
  them, though it must be run by hand, is not collected by pytest, and still omits /calendar (A-2).

  Broad proof, exact tree, my two runs: 75 URLs, 72 with a result, 3 FastAPI 422 JSON. 0 executions,
  0 live, 0 transient, 0 handlers, 0 iframes, 0 javascript URLs, 0 client errors. Script count 12, the
  application/json zones block. 12 is the same figure on 198ed4d. Exactly once: attributes 409 good, 0
  bad; pickers 6 good, 0 bad. The 2 text-bad are the known raw trace JSON in a <pre>. Vehicle block:
  33 blocks, 23 plate and 23 mileage lines, plate 2, seller 3, address 3, listing 2 exactly once.
  Retained evidence intact: harness files present, same fixture, reports parse, totals match.

  B-1 LOW, HARNESS. The broad proof shares one SQLite connection across threads. In BOTH my exact-tree
  runs /api/ops/critical-events failed under that race (ops_dashboard.py:785, InterfaceError and
  friends), leaving `critical: 0`, text-good 1144 instead of 1149. My first reading, a one-off flake,
  was too casual: it happened 2 times of 2. I do not count those runs for that panel. It IS covered:
  the retained exact-tree run has critical 33 and 0 executions, and my B27 and B27a runs, whose
  control_view.py is byte-identical, have critical 33, 0 executions, 0 live, 0 handlers.
  If this harness is carried into the tree it must assert panel counts, or a race reads as a pass.

  Page count wording: "72 pages, 3 no result" means 75 URLs, of which 72 returned a result.

================================================================================
6. SQLITE EVIDENCE
================================================================================
  Parsed by my own script, not cmp.py.

                        198ed4d   merged
  cases                    8003     8308
  failures + errors      62 + 9   62 + 9
  skipped                   677      752

  Failing identities  71 vs 71, NEW 0, GONE 0
  Failure messages    0 differ
  Status changes      0 of the 8003 common cases
  Cases               added 305, removed 0, no duplicate id
  Added, exactly      vehicle 148, vehicle PG 65, escaping 26 + 27 + 19, T-1 10, T-1 PG 10
  Skipped             NEW 75 (vehicle PG 65, T-1 PG 10), GONE 0, 0 messages differ
  Fresh               merged run 23:01 to 23:09, last tested file written 22:48, digest same before and after

  No unexplained skip: 43 distinct reasons, 38 modules. 34 are PostgreSQL files run in section 7, 3
  need git in the image (4 cases), 1 needs the live database. Full suite not repeated: the evidence is
  complete, fresh and tied to the exact tree.

  Focused, run by me, read-only tree, --network none: vehicle, T-1, minimum launch, the three escaping
  suites, p4_r1, outstanding question: 419 passed, 12 skipped (the R2 rehearsal, on PostgreSQL), 978
  subtests.

  Not in the closeout. 11 inherited failures have tracebacks through render_calendar_page, where every
  conflict sits, so I checked whether they mask the merge. They all stop at `" · ".join(zone_parts)`
  on a MagicMock zone in a stale test double. That is after `_aveh(r)` has run, so they do not hide a
  vehicle-join crash, but they are blind to everything later in the day card, on both trees. The
  agenda suites and Chromium cover that. INFO.

  Flags OFF, new here. The vehicle suite's E03 exists only in the candidate, so I scripted one v7.7
  session and ran it on 198ed4d and on merged, scrubbing tokens and ids: init, prepare, repeat,
  confirm, retry, loser prepare, loser refusal (the whole payload), provider calls, blank optionals,
  named lead, evidence rows, bookings, revisions, offers. 14 of 14 identical.

  Independent mutants, control passing, source inventories deselected for the escaping group:
    S01-S10  SUMMARY keys, switch leaks, attempt identity, prefill scope, attempt scope, grouping,
             plate case, plate persistence                   10 of 10
    E01-E16  live sinks, double escape, revision and details lines, plate lost from join  16 of 16
    T01-T08  section 3                                       8 of 8
  34 of 34. S02 (`details_prefill` sent with the switch OFF) survived my narrow selection and was then
  killed by test_refusal_07 in the Wild1 contract suite. The survivor was my selection, not a gap.

  Section 2 of the milestone, confirmed
    Optional mileage and plate, normalization        K and P suites; S08, S09
    No car, customer, thread, cycle crossing         Q suites, PG_CarContext, T-1; S05, S06, S07
    Losing customer's full details persist           PG_Recovery test_pgv_01
    Recovery prefill complete                        R suite, test_pgv_02 from a new process
    Repeated facts attempt-scoped                    Q_Rebinding, PG_Rebinding; S04
    Retry idempotency                                confirm-retry above; retained M10, M10b
    SUMMARY keys with the switch ON                  E2_Summary, PG_Summary; S01
    v7.7 unchanged when OFF                          differential above; S02, S03
    Car fields stay with their car                   PG_CardScope, PG_CarContext, T-1
    /kanban /table /profesionales /agencias /control /whatsapp/thread, pickers, modals, innerHTML
                                                     section 5, populated
    Escaped once at output, stored unescaped         AE-20, OP-25, OP-26, R2-16 pass; E12 killed

================================================================================
7. POSTGRESQL INVENTORY
================================================================================
  Static scan rebuilt independently: every env variable named by any test matching URL, DSN, PG or
  DATABASE. 42 found, 42 provisioned, 0 unaccounted either way.
  Skip-list cross-check: 38 modules skip; all 34 executed files are among them; the other 4 are 3 git
  modules and test_l4_wild01_repro.

  Retained comparison, own parser:
    Files 32 vs 34. Cases 886 vs 961, added 75: vehicle PG 65, T-1 PG 10. Removed 0. Skipped 0.
    Failing 62 vs 62, NEW 0, GONE 0. Candidate-only regression: 0.
    Message differences 29: 27 are the head name alone. 2 are m19 concurrency, outcomes in racing
    order. Nothing else.
    The five previously omitted files are present: m19 durability 10, concurrency 11, integration 9,
    r1_3e 1, clean_slate 2 = 33 failures, as C-3 states.

  Run by me, disposable PostgreSQL 16.13, one at a time:
    vehicle 65 passed; T-1 10 passed; minimum launch 19 passed; convergence race 20 passed;
    Smart Booking option B 48 passed; operator notification 4 passed, 1 failed (the known head pin);
    CE-SLA successor 17 passed; unanswered alert 4 passed.

  ARE THE INHERITED FAILURES GENUINE? Identical failures do not prove this alone. A test failing at a
  stale pin stops there on both trees, so anything after the pin could have regressed unseen, and no
  normalization can show it. So I tested it. On scratch copies I changed only the stale head literal
  to each tree's real head, then ran the five files on both trees:

    File                                 before  after   merged vs 198ed4d
    convergence_p2_authority_pg               3      1   same
    convergence_p4_pg                         1      0   same
    convergence_p4_r1_pg                      7      1   same
    strict_schema_correction_pg               7      0   same
    strict_schema_pg                          9      1   same
    Total, 131 cases                         27      3   NEW 0, GONE 0

  24 hidden test bodies ran and passed on merged, identically on 198ed4d, including 90 + 104 + 28
  subtests of schema proof. No regression was hiding. The 3 left, none a product defect:
    p2_mg_01       structural: asserts the head's parent is a fixed older revision
    p4r1_52, ssa_02  "both orders reach the same schema": they differ only as json vs jsonb on 3 columns
                   (expected_slots, source_message_ids, validated_as). tests/conftest.py aliases JSONB
                   to JSON before the app-first `create_all`; models.py and the migration both say
                   jsonb. Harness artefact. Full diff read: no vehicle column appears in it.
  m19 pin and the operator-notification pin stay masked: each is bound to its own revision's output.

  Other inherited failures
    r1_3e        on a WRITABLE copy: 34 passed. Confirmed artefact of the :ro mount.
    m19 path_id  all 67 `.attempt(` calls in test_m19_r1_2_* omit path_id; the gate's refusal is
                 correct. outbound_safety_gate.py is not in the manifest, so no merge effect.
    rv03         CORRECTS THE CLOSEOUT, which says the two tables are "not reset". They are:
                 tester_reset.py:355-368 deletes both. The test's hand-kept `handled` set is stale.
    clean_slate  2 environment assertions, as stated.

  UNPROVISIONED, VACUOUS OR LIVE-DEPENDENT
    test_l4_wild01_repro::test_a4       skips: hardcoded localhost crm_test and real pricing rows
    test_l4_wild01_repro::test_b4       NEW. PASSES WITH NO DATABASE. A nested `except: pass` swallows
                                        the connection failure, so its skip can never fire
    test_l4_1_wild_remediation::test_l4r09  NEW. Same defect. Both proved nothing in any sealed run
    test_l4_2_clean_slate zero-state    run, vacuous on an empty clone
    test_m19_r1_2_* (29)                run, cannot reach the recipient lock
    test_m19_r1_3d, test_m19_f2_3       hardcoded DSNs fed to guard scripts. Not PostgreSQL-gated. I did
                                        not assess whether a connection failure could satisfy them
    4 git cases                         not PostgreSQL

  P-1 MEDIUM, INHERITED TEST GAP, NOT BLOCKING THIS COMMIT. No suite proves the recipient lock on
  PostgreSQL: the m19 concurrency suite fails at the path_id gate before reaching it. That is an
  outbound safety guarantee in the M19 incident's own area, and it is unproven today exactly as on
  198ed4d. It should be repaired before OUTBOUND is enabled for customers. Owner's call.

================================================================================
8. MIGRATION
================================================================================
  Read in full, and rehearsed independently on disposable PostgreSQL 16 from the retained operational
  schema dump (2e06faf5ba07c480, schema only), 800 seeded rows, by my own script.

  Required                                 Result
  Only nullable integer and varchar(16)    int4 and varchar(16), nullable, no default
  No backfill                              0 populated; row hash unchanged
  No index, default, constraint            0, none, 0
  Repeat upgrade                           no-op
  Malformed refused, nothing changed       bigint, varchar(32), DEFAULT 0, index, partial with data:
                                           5 of 5, version stays at parent
  Clean downgrade, re-upgrade              DROPPED, row hash unchanged, CREATED
  Populated downgrade refused              both, and dominio alone (retained: mileage alone)
  Preflight head                           merged at head: 0 problems
  Old image cannot restart                 198ed4d preflight: 1 problem, exact-version refusal
  Models compatible while running          198ed4d models read and insert on upgraded schema
  New models                               merged ORM round trip of 85000 / AB123CD

  Retained rehearsal transcript read in full; it is tied to this tree and all its results hold (9 of
  9 malformed, foreign column on downgrade, limits, ADOPTED and VERIFIED). test_pgm_00..12 pass here.
  Assessment: sound. The dump is dated 2026-11-03; a fresh one is still required.

  Harness note, no effect: the retained pre-SB1 base differs from commit c797f73 in three non-schema
  files. models.py, the only one it uses, is identical, and my rebuild from true c797f73 reproduces
  the results.

================================================================================
9. PROCESS DEVIATION: THE 1400 MiB GATE
================================================================================
  CLASSIFICATION: NON-INVALIDATING PROCESS DEVIATION.

  bin/heavy.sh did gate at 1400, not 1536. That is a real departure from the prompt, and the closeout
  disclosed it. It invalidates nothing, because the configured threshold never decided anything:

    Gated runs         11, every one logged
    Lowest START       2183 MiB; all 11 started at or above 1536, by at least 647 MiB
    Lowest DURING      1082 MiB (pgside_pristine), a runtime figure, as the closeout reports
    Watchdog           500 MiB, in the script as required; BELOW500 count 0
    Results            each with its expected outcome

  The 1082 is not a gate breach: 1536 governs starting, 500 governs running. A correct 1536 gate would
  have admitted the same 11 runs at the same moments.

  Small inaccuracy, also non-invalidating. "Every heavy step gated" does not cover the first T-1 run
  (t1.xml, 22:48), which precedes the first gate at 22:52. It was 10 tests, and the gated focused run
  superseded it.

  This review: all 27 runs gated at exactly 1536 and watched at 500. Lowest start 1894. Lowest during
  1143. Mutants re-checked the gate per mutant, lowest 1998. Serial. BELOW500 0, timeouts 0.

================================================================================
10. ACCEPTED OWNER DEBT
================================================================================
  Not accidentally changed. lead_booking_details.py, booking_flow_details.py and
  booking_flow_service.py are byte-identical to the audited source.

    F-1  committed card Vehicle/Zona from the current candidate    unchanged (`_vehicle(c)`)
    F-2  narrow repeated-name default, same customer               unchanged, cannot cross customers
    R-1  candidate-less ownership fails closed                     unchanged; now pinned, t1_05-08
    R-2  committed and unconfirmed sections may coexist            unchanged, each coherent
    Return-journey cutoff                                          excluded: no such migration or code
    Meta v7.8                                                      unpublished by this work; see below
    BOOKING_FLOW_DETAILS_PREFILL_ENABLED                           absent from .env and compose,
                                                                   default false: OFF

  The merge neither worsened these nor created cross-customer or cross-car behaviour.
  Limit L-A stands: "attempt = car" means candidate = car. N-8 stands.
  v7.8 regenerates byte-identical from v7.7 (2102a8ec...); a wrong baseline is refused, rc 2, nothing
  written; routing unchanged; validator rc 0.

================================================================================
11. FINDINGS OF THIS REVIEW (none blocking)
================================================================================
  P-1  MEDIUM  Recipient lock unproven on PostgreSQL. Inherited. Section 7.
  B-1  LOW     Broad harness can lose a /control panel silently. Section 5.
  V-1  LOW     test_b4 and test_l4r09 pass with no database. Inherited. Section 7.
  W-1  LOW     Closeout corrections: rv03 tables ARE reset; the in-tree proof DOES kill both C-2 sinks;
               the vacuous list omits two tests; "every heavy step gated" omits one small run.
  I-1  INFO    11 calendar tests are blind past `zone_parts`, on both trees. Section 6.
  I-2  INFO    24 test bodies sit behind stale pins. Updating the pins would restore that protection.
  I-3  INFO    Retained evidence lives under /tmp and is not durable.

================================================================================
12. SOURCE AND LIVE ISOLATION
================================================================================
  Fingerprint before and after, by my own script. Only ONE line differs: the target's all-file digest,
  explained below.
    Target           HEAD 198ed4d; index empty; no stash; all tracked and untracked paths unchanged
    Sources          both all-file digests identical; HEAD 198ed4d; index empty
    Live containers  backend, postgres, n8n: same id, image, start time, 0 restarts, env hash. Also
                     equal to the merge's final fingerprint
    Configuration    docker-compose.yml and .env hashes identical
    Live databases   no connection made. Test containers ran --network none or --internal
    Runtime flags    unchanged; none set by this work
    Published Flow   TARGET_v7.7 4ce27122a55f3efd... unchanged. Meta not contacted, so its own copy was
                     not read: "unchanged" means nothing here altered it
    Provider         none contacted
    All worktrees    all 11, target included: HEAD, status and diff hashes identical; refs identical
    Disposables      rvw-pg, rvw-net, rvw-drv, rvw-sq, rvw-mut, rvw-al, rvw-srv-*, escproof-*: 0 remain
  Reads of live state: docker inspect (ids, environment hashed) and .env, compared in memory for the
  secret scan. No value printed or recorded.

  DEVIATIONS BY THIS REVIEW, disclosed:
    D-1  ISOLATION, MY MISTAKE. At 10:25 I ran the v7.8 generator with --help on the host before
         setting PYTHONDONTWRITEBYTECODE. Its import left
           backend/app/services/__pycache__/flow_contract.cpython-312.pyc
         in the candidate. That breaks "the only permitted write is the audit". Effect: none on content.
         Excluding it the digest is exactly bbdd0fa1...; it is git-ignored, not on the explicit
         git add list. Source A holds the same file. The permission layer refused `rm`, and I did not
         work around that. To remove it:
           rm backend/app/services/__pycache__/flow_contract.cpython-312.pyc
           rmdir backend/app/services/__pycache__
    D-2  One symlink outside the scratchpad, /tmp/claude-0/rvw, pointing into it. My scratchpad path
         exceeds the 108-byte AF_UNIX limit, so the browser sockets needed a short root. It holds no
         data. Removal refused the same way; it remains.

  Corrections to my own work:
    * The first browser chain failed on that socket limit and produced no evidence. Discarded, rerun.
    * My first schema-diff drill died at collection (a line above `from __future__`). Discarded, rerun.
    * The first S02 run looked like a survivor; widening the suite killed it. Section 6.
    * Two G37R doc tests failed in that widened run only because my partial copy has no CLAUDE.md.
    * The dead-mutant page hash differed at first. I did not claim equivalence until a T-vs-T control
      showed the timestamp was the only difference.
    * I called the critical-panel loss a flake too early. Section 5.

  Evidence, not part of the candidate:
    /tmp/claude-0/-opt-ridecheck-crm-minimum-launch-merge/0821971f-9dd4-4f7e-ab5c-63fa0b0f0620/scratchpad

================================================================================
13. REMAINING PREREQUISITES (none proven here)
================================================================================
  Commit
    1. Stage the closeout's explicit list PLUS this audit: 46 paths. Do not use `git add -A`.
    2. Optionally remove the file in D-1 first.

  Migration
    3. Fresh schema-only dump and rehearsal immediately before the real migration.
    4. Migration before the new image. Merged code requires exactly 20261104_vehicle_identity.
    5. The old image cannot restart at the new head. Rollback means downgrading first, and downgrade
       is refused once any plate or mileage is stored. Plan the window accordingly.
    6. crm_test is the authoritative database (N-7). Repeat the read-only markup scan before launch.

  Meta and handset
    7. Owner Ejecutar: Form init-values, TextInput pattern and helper-text, object details_prefill, the
       7 unproven properties.
    8. Handset proof with a real slot conflict: empty and partial prefill, blank as "" or absent, what
       a customer sees when a pattern fails.
    9. N-3: switch ON with an open v7.7 session returns two undeclared keys.
    10. N-4: switch ON, a v7.7 form submitting a blank e-mail can clear a value it never displayed.
    11. Order of Flow and switch; the return-cutoff migration re-parent.
    12. BOOKING_FLOW_DETAILS_PREFILL_ENABLED stays OFF until v7.8 is published.

  Deployment
    13. Escaping fixes protect nothing until deployed. Card reader changes are ungated and take effect
        on deploy with the switch off (R-3, F-2).

  Owner decision
    14. P-1: repair the m19 PostgreSQL suites before enabling OUTBOUND for customers, or accept it.

  After launch: remove `_day_slots`; carry the broad proof into the tree with panel assertions and
  /calendar; update the stale pins; fix test_b4 and test_l4r09.

================================================================================
14. RECOMMENDED NEXT MILESTONE
================================================================================
  PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-ESCAPING-CONTROLLED-COMMIT

  Scope: verify tree digest bbdd0fa1... and the resolved hash, stage the 46 explicit paths, commit on
  integration/prelaunch-minimum-launch-merge. No push, build, deployment, migration, flag change or
  publication unless separately authorised.

STOP
