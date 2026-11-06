PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE

Date: 2026-11-04. Local candidate only. Nothing staged, committed, pushed, merged, built,
deployed or migrated live. No flag, allowlist, Compose or environment file changed. No message
sent. Meta's API was not contacted. All plates, phones and addresses in tests and in this file
are synthetic.

================================================================================
0. VERDICT
================================================================================
R1 STATUS: NO VERDICT YET. The R1 evidence is incomplete (§17). The READY FOR INDEPENDENT REVIEW
below was the pre-R1 result and does not cover the R1 tree. The R1 full SQLite comparison and
mutation run have not completed.

Pre-R1 verdict: READY FOR INDEPENDENT REVIEW

  What was proven locally before R1: both fields end to end, recovery prefill through the candidate's real
  bindings, the migration on PostgreSQL 16, and NEW 0 / GONE 0 on SQLite and PostgreSQL.

  What is NOT proven, and blocks publication (not review):
    * Meta has never validated this artifact. It carries three properties no Meta-accepted
      artifact has: Form `init-values`, TextInput `pattern`, TextInput `helper-text`. Meta's
      public reference supports all three (§6). Only Ejecutar can confirm that.
    * No handset has run it (§6.3).
  If Ejecutar rejects `init-values`, the verdict for prefill becomes BLOCKED BY META COMPONENT
  CONTRACT. It is not that verdict today: the documented contract supports the design.

  Three items need an owner decision (§15): a pre-existing HTML-escaping gap on the agenda, the
  publication/flag cut-over order, and the PostgreSQL evidence limit in §10.

================================================================================
1. BRANCH, WORKTREE, HEAD, REMOTE
================================================================================
  branch     integration/prelaunch-vehicle-identity-recovery (new; both it and the directory
             were confirmed absent first)
  worktree   /opt/ridecheck-crm-vehicle-identity-recovery
  HEAD       198ed4d6a7157230a616063094e2d50fb9623836 (unchanged; no commit made)
  remote     no upstream configured; the branch exists nowhere but this host
  tree       R1 snapshot digest aa75cf1ff5d88702525297c2eca5098ddf1f2593b23b247bcf8e64a54835d599
             (every tracked and untracked file except this closeout; the worktree was compared
             to that snapshot file by file, no differences)

================================================================================
2. DATA MODEL TRACE (done before editing)
================================================================================
  Make, model, year, type    three copies, three purposes:
                               whatsapp_thread_candidates  conversation's working vehicle
                               thread_revisions            conversation-side booking projection
                               revisions                   the CRM's record of the inspection
  Details first received     `handle_prepare_summary` (Data Exchange). Nothing earlier: the
                             APPOINTMENT -> DETAILS step is client-side `navigate`.
  Persisted before confirm   `whatsapp_thread_evidence`, producer BOOKING_FLOW_DETAILS,
                             DRAFT/HOLD, bound to thread, cycle, offer-bound candidate; written
                             at prepare_summary and again at confirm_booking.
  Reach the CRM revision     `BookingCommand.revision_fields`, applied to `Revision` by
                             `BookingAuthority` inside the booking transaction.
  Winner and loser           winner: Revision + ThreadRevision + committed ledger row.
                             loser: no booking row; their draft rows plus one
                             `slot_recovery_jobs` row keyed by the losing offer.
  Recovery rebuilds the Flow `_issue_fresh_offer` issues a new offer with the SAME candidate
                             and cycle; the worker sends the same Flow in data_exchange mode.
                             The job carries no customer data, and must not start to.
  Long-term home             `revisions`: what the operator sees and edits, one row per
                             inspection.

================================================================================
3. CANONICAL OWNERSHIP
================================================================================
  Field               Before commit                       After commit
  Kilometraje         evidence FLOW_VEHICLE_MILEAGE       revisions.kilometraje  integer
  Dominio / patente   evidence FLOW_VEHICLE_PLATE         revisions.dominio      varchar(16)

  Naming follows the vehicle columns beside them (`marca`, `anio`: Spanish). Flow keys
  (`vehicle_mileage`, `vehicle_plate`) follow the Flow's own English keys (`listing_url`).

  NOT added, with the reason:
    thread_revisions    `seller_phone` went there because `revisions` has no seller column at
                        all. These two have a home written by the same transaction; a copy
                        would be edited nowhere and would drift.
    candidates          the offer's candidate is frozen for pricing; a typed form value is not
                        a conversation claim.
    slot_recovery_jobs  deliberately free of customer data.

  Drift prevented: once a booking of the same vehicle commits, the lead card stops showing the
  draft values and the revision is the single source, so an operator's correction or clearing
  cannot be contradicted by an old draft (test i04).

  One rule, one file: `services/vehicle_identity.py`. Flow, API schema and CRM form all call it.
    kilometraje  plain digits or valid Argentine grouping (D-3, §17): `85.000` is 85000,
                 `1.234.567` is 1234567. Blank is NULL; 0 is kept. Refused: `85.00`, `85..000`,
                 `85,000`, `85,5`, `0.000`, `085.000`, signs, decimals, exponents, letters, mixed
                 separators. Stored as an integer; ceiling is the column, 2,147,483,647.
    dominio      trim, upper-case, remove spaces / hyphens / dots; any ASCII alphanumeric up
                 to 16 characters. ABC123 and AB123CD are not assumed. Not unique, no index
                 (no query needs one yet).
  Grouping is input only: persistence, SUMMARY and prefill always carry canonical digits.

================================================================================
4. MIGRATION DESIGN
================================================================================
  backend/migrations/versions/20261104_vehicle_identity.py
  revision id 25 characters (limit 32); down_revision 20261103_slot_recovery_job.

  Upgrade: strict, locked, in Alembic's transaction, using the project's column fingerprint.
    both absent                                  CREATED
    both canonical and empty                     ADOPTED_EMPTY_CANONICAL
    both canonical, holding values               VERIFIED_CANONICAL
    one canonical and empty, other absent        CREATED (adopting)
    any differing type/default/constraint/index  REFUSED, nothing changed
    partial and holding values                   REFUSED (provenance unknown)
  Backfills nothing. No default, so adding the columns rewrites no row.
  Downgrade: REFUSES while any revision holds either value; otherwise drops exactly what is
  present after checking each column is its own.
  Order: runs BEFORE the image. `schema_preflight.EXPECTED_HEAD` moves to this revision, so the
  new image will not start below it. The deployed image is unaffected by the two extra columns.

================================================================================
5. BACKEND BEHAVIOUR
================================================================================
  * Omitted fields survive unrelated edits. API PATCH uses `exclude_unset`, so omitted means
    untouched and null clears. The CRM form needed a marker: FastAPI turns a blank `Form` field
    into its default, so "cleared" and "never on this form" look identical. A hidden
    `vehicle_identity_form` input separates them; a stale tab erases nothing (c03).
  * No duplicate can arise from either field: the booking's idempotency key is the token alone.
    A later delivery with a different plate returns the existing booking unchanged (s05).
  * Request fingerprints are stable: both keys are left out when not given (a08, s06), so an
    idempotent retry spanning a deployment still matches.
  * Old clients: a v7.7 session sends neither key and is served exactly as before (g01, g06).
  * Logs: refusal messages are fixed text and never quote the value (k07, g10, s08).
  * Operator notification is untouched (s07).
  * CE transport not extended: `_converged_flow_book` reads the legacy Flow's keys, which have
    never carried these fields. The Booking Flow books through Data Exchange. A booking through
    that legacy path would have both revision columns NULL. Stated, not changed.

================================================================================
6. FLOW CANDIDATE
================================================================================
  meta/flows/booking/CANDIDATE_v7.8-vehicle-identity-prefill.json
    raw        2102a8ec5f01be41c6671a1d9a3c59cf1f79fbad55354bf623f1db1cd3348354
    canonical  3b8dc55eef53e260680c0efa4c894fc37bf22cbe0532dceee40a56d5d0370292
  generator  meta/flows/booking/make_v78_vehicle_prefill_candidate.py
    pins v7.7 by canonical fingerprint; exits 2 and writes nothing for any other source.
    Two consecutive runs byte-identical (f03).

  Adds: `Kilometraje` and `Dominio / patente` after the listing link; both values on the
  prepare_summary and confirm_booking payloads; `details_prefill` declared on APPOINTMENT and
  DETAILS, carried on the navigate payload, bound whole to the DETAILS Form's `init-values`.
  Removes, renames and reorders nothing (f08 strips the additions and compares equal to v7.7).
  routing_model identical. Canonical forward-only validator passes; backward-route rejections
  stay active (f05, f06). scripts/validate_flow_json.py: 0 routing, 0 error, 0 render, 7 unproven.

6.1 Published Flow unchanged
  TARGET_v7.7-r3-prefill-refusal.json  raw 4ce27122a55f3efd4fbae117d620abf34024c5a86343bb23ce878182ba2bbd9d
                                       canonical dccd3bc973b1b51526c5064027b5d8f61ee885293dfa9815a272609092aca17d
  Byte-identical to the contract's pinned hash and to the copy in the minimum-launch tree. No
  diff against HEAD. r3 also unchanged.

6.2 The component contract, read from Meta's public reference (a web read; no API, no account)
  The September rejection stands and is explained: `init-value` is "Only available when
  component is outside Form component". All inputs here are inside a Form. Inside a Form the
  documented property is the Form's `init-values`: "<key, value> object where key - Field Name
  in Component; value - Field Initial Value", dynamic as "${data.init_values}", String for a
  Text Entry. The repository's earlier "the Flow cannot be prefilled" conclusion considered only
  `init-value`; `docs/operations/BOOKING_FLOW_PREFILL_CONSTRAINT.md` now carries a correction.
  Caveat: the fetched page text did not include its example code blocks, and the Form section
  was read as table text only.

6.3 Open questions only a handset or Ejecutar can answer
  a. Does Ejecutar accept `"init-values": "${data.details_prefill}"` with an object declaration?
  b. Does an object survive the navigate payload? (Arrays and strings are proven; objects not.)
  c. Is a blank optional input sent as "" or omitted? Clearing a prefilled value relies on "".
     If it is omitted, an emptied value stays in the draft (never a wrong booking: the booking
     takes the submission) and is offered again on a later Flow.
  d. Does the helper text render acceptably, and does `pattern` behave as the only client rule
     on a plain text input, including the escaped dot in the regular expression?
  e. F3a remains: the in-Flow refusal screen after a terminal SUMMARY is still unproven, which
     is why recovery happens by a fresh Flow.

================================================================================
7. RECOVERY PREFILL DESIGN
================================================================================
  The recovery worker was not changed at all: no state, guard, send or identity differs from
  198ed4d, so its exactly-once and fail-closed rules stand as they were.

  When the refreshed Flow opens, INIT reads the customer's live draft rows and returns them as
  `details_prefill`. The value depends on nothing held by a job, worker, request or process, so
  restart, lease expiry and retry need no special handling.

  Sequence: one recovery text, one refreshed instance of the SAME Flow at APPOINTMENT, the
  customer picks a slot, DETAILS opens filled, they review or edit, and confirm.
  Prefilled: name, email, address, seller name, seller phone, listing link, mileage, plate.
  Vehicle make/model/year has no input: it is shown from the offer's candidate on every screen.
  The refused slot is deliberately never prefilled.

  Scope is the safety property. Live rows are per THREAD (accepted debt F-14), so a naive read
  would offer one car's plate to a second car on the same thread. `prefill_details` reads only
  this thread + this offer's candidate + this cycle (r10, r11, r12). The customer's own name
  still follows them on their thread, as `customer_name` always has.

  Clearing: prefill makes a blank meaningful, because the customer saw a value and removed it.
  Behind the switch only, a blank optional field over a value they could have seen is recorded
  as an empty superseding row (r07). It cannot clear a value they never saw (r09b), and an absent
  key is never a clearing (r09). Switch off, the existing rule stands (r08).

  The switch: BOOKING_FLOW_DETAILS_PREFILL_ENABLED, default false, exact `true` only. Off, every
  payload is byte-for-byte the published v7.7 contract (g01). It is needed because v7.7 does not
  declare `details_prefill`. It is not set anywhere and is not in any Compose file.

  Failure windows (SQLite unless stated):
    process restart                       r20; pgv_02 reads it from a separate OS process
    lease expiry                          r21 (Flow resumed, text not re-sent)
    duplicate Meta submission             r22 (one job, one text, one Flow)
    retry of the refused request          r23 (same answer, nothing sent)
    text sent, Flow fails                 r24 (HANDOFF, flags kept, details visible, no re-send)
    Flow sent, worker interrupted         r25 (opens prefilled; no second Flow)
    Smart Booking OFF                     r26 (nothing sent, HANDOFF, details kept)
    K1 held through prepare_summary       pgv_04 (confirm-time write still keeps both)

================================================================================
8. CRM IDENTITY AFTER REFUSAL
================================================================================
  Before rebooking (i01): one contact, one thread, one lead, no booking; vehicle, personal and
  seller details, mileage and plate visible; none of the winner's values; no orphan lead.
  After fallback (i02): the same identity owns one booking and all details.
  Winner (i03): its own values only.

================================================================================
9. TESTS
================================================================================
  Harness: ridecheck-crm-test:l4.7b2, sealed containers, --network none (SQLite) or a private
  --internal network (PostgreSQL), trees mounted read-only.

  NEW tests/test_vehicle_identity_recovery.py     102 passed (SQLite, R1 tree)
  NEW tests/test_vehicle_identity_recovery_pg.py  19 passed, 7 subtests (PostgreSQL 16, R1 tree)
  The Flow round-trip tests walk the candidate artifact's real ${data.*}/${form.*} bindings.

  Existing test files adapted (no assertion weakened):
    test_minimum_launch.py                     b01 now submits both new fields, keeping its
                                               "every claim type" equality
    test_minimum_launch_pg.py                  HEAD moved; R1_REV names the revision under test
    test_convergence_p4_r1.py, test_w1f_w01_r2_outstanding_question.py   head pins

  §9.1 and §9.3 are PRE-R1 measurements. §9.2 is superseded by §17. See §17 for what R1 reran.

9.1 SQLite, full suite, pre-R1 tree
  pristine 198ed4d   62 failed, 7256 passed, 677 skipped, 9 errors
  candidate          62 failed, 7347 passed, 694 skipped, 9 errors
  failing identities 71 vs 71 · NEW 0 · GONE 0 · COMMON 71 · messages differ 0
  (+91 passed = the new suite; +17 skipped = the new PG suite without PostgreSQL)

9.2 PostgreSQL 16, every gated file, one process each, fresh clones
  27 gated files on pristine, 28 on the candidate (the new suite); junit cases 797 and 814 (+17).
  failing identities 22 vs 22 · NEW 0 · GONE 0 · COMMON 22 · 19 messages differ, every one only
  by the head revision name inside the same inherited stale-literal assertion.
  test_convergence_p4_strict_schema_correction_pg first SKIPPED 23 on both sides for lack of
  PostgreSQL client tools. It was then run on both sides with tools copied from the disposable
  server: 9 failed, 18 passed each side, same failure list, all that same stale literal.
  test_minimum_launch_pg: 19 passed, 12 subtests.

  The 71 + 22 (+9) inherited failures were not investigated and are not dismissed: they fail
  identically on 198ed4d. The PG ones are mostly a stale literal naming 20261022 as head, which
  masks whatever those assertions were meant to prove. That is a real coverage gap for a future
  milestone.

9.3 Mutation
  42 mutants across normalization, persistence, recovery prefill, CRM and API. First run 38
  killed, 4 survived: R5 superseded row readable, R8 blank clears unseen value, C8 zero hidden on
  the revision card, A3 API fingerprint. All four were real test gaps, closed with behavioural
  tests, and re-run: 4/4 killed. Total 42/42. The 38 ran against the tree before those test-only
  additions; no production file changed between the two runs.

================================================================================
10. MIGRATION REHEARSAL
================================================================================
  Disposable PostgreSQL 16.13 (vid-pg, private network), upgraded from a real 20261103 state with
  50 revisions, in order:
    upgrade              CREATED, 1.7 s incl. process start; 50 rows; both columns nullable
    backfill check       0 rows hold a value
    rerun                no-op
    stamp back + upgrade ADOPTED_EMPTY_CANONICAL
    downgrade with data  REFUSED (revisions.dominio=1), nothing changed
    clean downgrade      DROPPED kilometraje, dominio; 50 rows kept
    re-upgrade           CREATED; 50 rows
  PG_Migration additionally covers VERIFIED_CANONICAL, partial adoption, five malformed shapes
  (bigint, default, NOT NULL, text, UNIQUE), a foreign index, partial-with-data refusal, and
  refusal to drop a foreign column.

  EVIDENCE LIMIT, stated plainly. Earlier milestones built PG templates from a schema-only dump
  of live crm_test. I attempted a `docker exec` into the live PostgreSQL container for this; the
  permission layer refused it and I did not retry or route around it. Three older dumps on disk
  contain data sections, so I did not read or load them. Templates here came from the repository
  alone: `create_all` from the pre-SB1 models in git history (parent of 4d2f0f9), stamped
  20261006, then the real Alembic chain. This is the method several suites' headers describe, and
  the result has the triggers and exclusion constraint, but it has NOT been compared to the live
  schema. A rehearsal against a schema-only dump is recommended before any live migration. The
  pristine and candidate sides used the same method, so NEW/GONE is like for like.

  An operational note: the first disposable filled its 600 MB tmpfs with templates and exited
  (WAL "No space left"). My sizing, no product meaning. It was recreated by exact name.

================================================================================
11. DEFERRED: PDF/DOC UPLOAD
================================================================================
  POST-LAUNCH BACKLOG. Upload for `Resultado técnico (link/doc)`: file upload, object storage,
  attachment APIs, malware scanning, document handling. Nothing was built. The field behaves as
  before (c10).

================================================================================
12. MERGE-CONFLICT ANALYSIS
================================================================================
  integration/prelaunch-minimum-launch
    Same commit as this branch's base, so a merge is a fast-forward today. If the enablement work
    commits first, overlap is possible only in paths it touches; it has been writing docs.

  Open return-cutoff work (uncommitted in /opt/ridecheck-crm-integration, read only)
    `patch --dry-run` of its 3,805-line diff: 4 failed hunks on pristine, the SAME 4 on the
    candidate (schema_preflight.py, test_convergence_p4_r1.py, test_w1f_w01_r2…×2). This candidate
    adds no new textual conflict. Those four are head-pin lines.
    Migration chain is the real work: its 20261102_professional_operational_origin has
    down_revision 20261027_w1f_conversation_memory, already a fork against minimum-launch. It must
    be re-parented onto 20261104_vehicle_identity, and EXPECTED_HEAD set to it.
    Both edit kanban_view.py, kanban_actions.py, models.py, settings.py and api/revisions.py; they
    merge cleanly today by hunk, but expect a review pass there.

================================================================================
13. PROPOSED COMMIT (NOT EXECUTED)
================================================================================
  subject:
    feat(launch): add vehicle mileage and plate, prefill recovered Booking Flow details

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

  Those 25 paths are every changed path of this milestone.

================================================================================
14. LIVE ISOLATION
================================================================================
  Fingerprint before and after (docker inspect, hashes of files, read-only git):
    backend, postgres, n8n    same container id, image, start time, restart count 0
    backend environment hash  identical
    /opt/ridecheck-crm compose and .env hashes identical
    /opt/ridecheck-crm and /opt/ridecheck-crm-integration  HEAD, status and diff identical
    /opt/ridecheck-crm-minimum-launch  HEAD and tracked diff identical
    v7.7 and r3 hashes identical

  One difference, not mine: minimum-launch's untracked-file list gained
  …CONTROLLED-ENABLEMENT-PREPARATION-R1_CLOSEOUT_001.md during this run, written by the parallel
  enablement session. No command here writes to that tree.

  Touched outside the worktree: the new branch and worktree metadata in the shared .git, which the
  milestone asked for, and this session's scratch directory. Reads only: public Meta documentation
  pages; `git ls-remote` on origin; read-only git on the two other trees; size and line counts of
  three old dumps, whose contents were not read.
  Disposables vid-pg, vid-pg-net, vid-probe, vid-mut, vid-sqlite-*, vid-pgtest-* are all gone,
  removed by exact name or --rm; none remain. No image was built.

================================================================================
15. FINDINGS AND DECISIONS FOR THE OWNER
================================================================================
  F-1 HIGH, PRE-EXISTING, NOT FIXED. The agenda renders the customer's name and the address without
      escaping: `_txt` trims but does not escape (`_txt(_aname(l))` at four sites, `_aaddr`).
      A name comes from the Flow, so stored HTML in a customer name reaches an operator's agenda.
      The minimum-launch escaping work covered the lead card, not the agenda. This code is in
      198ed4d and therefore deployed. I fixed only the vehicle line, because this milestone
      added the plate to it; the rest is outside this milestone's scope. Recommend its own small
      correction before Smart Booking is enabled for customers.

  D-1 Cut-over order. The candidate Flow and the switch should change together. Candidate live with
      the switch off: DETAILS opens empty and `details_prefill` is unresolved, which the docs allow
      but no handset has shown. Switch on with v7.7 live: an undeclared key is sent, which the asset
      has tolerated but is not guaranteed. If Meta requires a new Flow asset (Gate B Branch B),
      changing the Flow id and the switch in one restart makes this atomic. The variable also has
      to be added to the Compose environment at that point.

  D-2 Order against enablement. This candidate raises EXPECTED_HEAD, so it needs a migration window.
      It should not ride along inside the controlled enablement.

  D-3 RESOLVED by the owner: grouped input accepted (§17).

  Noted, unchanged: `backend/openapi_dump.json` is a static dump and does not show the new fields.
  The draft plate lives in `whatsapp_thread_evidence.value` in clear, as seller phones already do;
  that table has no API or UI reader other than the lead card, and any forensic export of it would
  include plates.

================================================================================
16. REQUIRED BEFORE PUBLICATION
================================================================================
  1. Independent review of this candidate.
  2. Migration rehearsal against a schema-only dump of the live schema.
  3. Owner Ejecutar of the candidate (validate; do not publish).
  4. Handset proof of §6.3 a-d with a real slot conflict.

================================================================================
17. R1 — OWNER DECISION D-3: GROUPED MILEAGE
================================================================================
Decision: accept plain digits or valid Argentine thousands grouping; reject anything needing a
guess; store an integer. Dominio is unchanged.

17.1 Changes
  backend/app/services/vehicle_identity.py   grouped form accepted by the one normalizer. A grouped
                                             value starts with a non-zero digit, every later group
                                             is exactly three digits. Same integer ceiling.
  Flow candidate                             pattern admits grouping; helper text now
                                             "Opcional. Ej.: 85000 o 85.000."; `input-type: number`
                                             REMOVED from Kilometraje.
  backend/app/ui/kanban_view.py              modal input is `type="text" inputmode="decimal"`.
  Generator, flow_contract hashes, README, tests.

  Two changes go beyond the normalizer, because the decision could not work without them:
    * Flow: Meta applies a base regular expression to a number input before `pattern`, and the
      reference does not say a dot passes. A text input leaves `pattern` as the only client rule.
      Cost: customers get the full keyboard, not the numeric keypad, for this field.
    * CRM: a browser `type="number"` input reports `1.234.567` as an empty value, which this form
      reads as a clearing. That would have silently erased the stored mileage.

  Judgement calls for review: `0.000` and `085.000` are refused (leading zero in a grouped value);
  the Flow pattern stops at 999.999.999 while API and CRM allow the column ceiling; the prefill
  shows digits (`85000`), not the customer's grouped spelling.

17.2 Evidence on the R1 tree
  COMPLETE
    Focused SQLite                 102 passed. New: k08-k11 boundaries, a05b, c01b, c01c, g07b, g07c,
                                   r04b (grouped through refusal, prefill and fallback), r04c.
    Flow generator                 regenerated; forward-only validator passes; 0 routing, 0 error,
                                   0 render, 7 unproven. Published v7.7 unchanged.
    PostgreSQL 16, all 28 files    failing identities 29 vs 29 · NEW 0 · GONE 0 · COMMON 29.
                                   26 messages differ, only by the head revision name.
                                   Includes the strict-schema correction suite, which §9.2 ran
                                   separately; it is why 22 became 29. The pristine side is the
                                   earlier run of an unchanged tree. 816 candidate cases.
    New PG suite                   19 passed, incl. pgv_02b (grouped, new OS process, fallback) and
                                   pgv_02c (2.147.483.647 fits the real column).
    test_minimum_launch_pg         19 passed, 12 subtests.
    Migration rehearsal            repeated, same result as §10 (file unchanged).

  NOT COMPLETE
    Full SQLite comparison         STOPPED by the system: host critically low on memory (swap
                                   4093/4095 MB, 135 MB available). No result. The pre-R1 NEW 0 /
                                   GONE 0 does not cover the R1 changes.
    Mutation run                   NOT STARTED. Prepared: the 42 earlier mutants plus 10 for
                                   grouping (G1-G10). Nothing to report.

17.3 Memory incident
  The killed shell left vid-sqlite-candidate_r1 and vid-pg running. Both were stopped by exact name,
  and vid-pg and vid-pg-net removed; available memory returned to 995 MB. A container that was not
  mine, agesc-final, was also running and was not touched. Live containers kept the same id, image,
  start time and restart count throughout. The host has about 4 GB of RAM shared by the live stack,
  this work and the parallel session, and swap filled during the run.

17.4 Live isolation, R1
  Same result as §14. minimum-launch's untracked docs changed again during this work, from the
  parallel session; its HEAD and tracked diff are unchanged. Nothing staged, committed, published,
  migrated or deployed. Meta not contacted.

17.5 To finish
  Run, when the host has headroom: full SQLite on the R1 tree, then the 52 mutants. Then update this
  section and issue the verdict.

STOP.
