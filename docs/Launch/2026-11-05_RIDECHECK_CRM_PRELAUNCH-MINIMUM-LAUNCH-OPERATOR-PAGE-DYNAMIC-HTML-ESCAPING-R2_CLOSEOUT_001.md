PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-OPERATOR-PAGE-DYNAMIC-HTML-ESCAPING-R2

Date: 2026-11-05. Local correction only. Nothing staged, committed, pushed, merged, built or
deployed. No migration, Meta contact, flag change or live write. The live-data check was read-only
and printed no customer value. All test payloads are synthetic.

================================================================================
0. VERDICT
================================================================================
READY FOR INDEPENDENT REVIEW

  Proven: over a sealed local HTTP origin, Chromium recorded 173 distinct payload executions on
  198ed4d, 46 on the R1 tree and 0 on this tree, across 25 pages. N-4 is fixed, N-5's audit found
  and fixed further sinks, and the dead renderer is deleted. The live check found no markup
  indicators, so no cleanup decision is needed. Full suite on the final tree: NEW 0 / GONE 0.

  Most important finding: a customer's WhatsApp profile name executed as script in two operator
  pickers. The customer chooses that name, so it needed no operator mistake. Fixed here.

  The reviewer must know these limits:
    * The deployed backend reads the database crm_test, not crm. Owner confirmation needed (N-7).
    * The fixes are not live until deployed. Deployed pages stay exposed until then.
    * PostgreSQL-gated suites are deferred to merged-tree verification, as authorised.
    * Vehicle suites were kept paused, so the merge is proven by the escaping suites only.

================================================================================
1. BRANCH, WORKTREE, HEAD
================================================================================
  branch     fix/prelaunch-agenda-customer-data-escaping
  worktree   /opt/ridecheck-crm-agenda-escaping
  HEAD       198ed4d6a7157230a616063094e2d50fb9623836 (unchanged)
  index      empty

  The vehicle-identity worktree was read and copied to scratch only. It was not modified.

================================================================================
2. FULL CANDIDATE MANIFEST (agenda + R1 + R2)
================================================================================
  M  backend/app/ui/kanban_view.py
       sha256 e555a7f41f8960a09c31e6bdf4d4e00a38656f75adc4f382f6641e63caeabe12   +77/-767
  M  backend/app/ui/control_view.py
       sha256 f57af9c56d4edacf721d4bfd8b8c873948378d07c9a2703c3b025cee1ffe08cb   +3/-2
  M  backend/app/ui/whatsapp_ui.py
       sha256 5c31eb26f28c21468ad3746c6ea0ed19da9f260c97275a4ecd996aba978a860f   +6/-2
  A  tests/test_agenda_customer_data_escaping.py
       sha256 f38e1b0c238d42b0211c647e76afb6798f1682161e4587651a5c07bacb12f090 (unchanged)
  A  tests/test_operator_page_html_escaping.py
       sha256 e5c96c79ee80043b44d893d68bb75bd9e19b4d7856b52329c8f5dcc03fc10445 (unchanged)
  A  tests/test_operator_page_html_escaping_r2.py
       sha256 495340484319773c2cfafa802886ec8a942ca466b2642d42423ce311c4423c3c
  A  tests/escaping_browser_proof/run_proof.py
       sha256 005c5abd490d7c771f6aadbb9a42680e4fe49b0e02acc6c90735d17ef9d32da9
  A  tests/escaping_browser_proof/serve_hostile.py
       sha256 5e923a96a4ad1854a80193b380f11ea753e962d6696b13721fd5a2ea0f4b765c
  A  tests/escaping_browser_proof/driver.js
       sha256 22df3b57972d004a8f3dbe72e95c8274f57e53f60513cdd5a1e104b36c8b34a4
  A  docs/Launch/2026-11-04_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-AGENDA-CUSTOMER-DATA-ESCAPING_CLOSEOUT_001.md
       sha256 864a75e25628deeef92c536d2a83322771c354a1feed193ecb3b74b97bc48af2 (unchanged)
  A  docs/Launch/2026-11-04_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-OPERATOR-PAGE-DYNAMIC-HTML-ESCAPING-R1_CLOSEOUT_001.md
       sha256 a3316e6735cb487a40fa8b4e9b971c11652a18472d5e40c982eed77b4e9e8505 (unchanged)
  A  docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-OPERATOR-PAGE-DYNAMIC-HTML-ESCAPING-R2_CLOSEOUT_001.md
       this file

  Reconciliation of kanban_view.py, function by function:
    agenda    render_calendar_page
    R1        _filters_form_html, render_revisions_table_page, render_profesionales_page,
              render_agencias_page
    R2        render_page, render_edit_latest_revision_form; render_table_page deleted (-703 lines)
    Compared against both 198ed4d and R1. render_lead_card and render_revisions_block are identical
    to 198ed4d. Nothing else changed.

  The three test files depend on each other in that order: commit them together.
  The earlier closeouts are kept as written. Their open items N-1, N-4, N-5, N-6 are closed here.

================================================================================
3. OWNER DECISIONS: OUTCOME
================================================================================
  N-4  Fixed. /kanban zones block. Execution was not the risk here: the name <!--<script> swallowed
       the rest of the page. R1 found this on /table; R2 applies the same fix.
  N-5  Audited. Five sink groups confirmed and fixed (section 4).
  N-6  render_table_page proven dead and deleted (section 5).
  Live Clean (section 6). No cleanup decision needed.

================================================================================
4. SAME-CLASS AUDIT
================================================================================
  Method: an AST listing of every f-string interpolation, then reading each unescaped value, every
  innerHTML assignment and each client path that carries a value into HTML.

  Confirmed and fixed
    S-1 HIGH  /kanban "Linkear WhatsApp" picker: thread.display_name and wa_id through innerHTML.
              display_name comes from the customer's WhatsApp profile via the webhook.
    S-2 HIGH  /whatsapp/thread lead picker: lead.nombre, apellido, telefono through innerHTML.
    S-3 HIGH  /whatsapp/thread forward picker: data-display-name and data-wa-id read back and
              written through innerHTML. Attribute escaping does not survive that read.
    S-4 HIGH  /control paths table: stored path_id inside onclick="filterTraceByPath('...')".
              The browser decodes the attribute before the JavaScript runs, so HTML-escaping cannot
              protect it. It now travels in data-path and is read with this.dataset.path. esc() also
              gained the single quote.
    S-5 MED   /kanban revision edit form: stored zone names in <option>, value and text.
    S-6 MED   /kanban zones block (N-4).

  Client fix: waEsc in kanban_view.py and whatsapp_ui.py escapes & first, then < > " '.

  Reachability: the lead and forward pickers open only on /whatsapp/thread, because the inbox page
  has no utility modal. The thread page is one click from the inbox and shares its script, so it
  was audited and fixed as part of N-5.

  Audited, no defect
    /kanban      No filter chips exist; R2-04 asserts it. q is the only reflected value and it is
                 escaped. Other filters are not written to the page. highlight_lead_id, open_lead,
                 open_rev and the hash reach getElementById or a digit match only. Toasts receive
                 constants. Lead card and revision block values are escaped.
    approval_tag _revision_approval_tag can return rev.approval_tag unescaped, but Revision has no
                 such column, so it is unreachable. Left unchanged to keep the lead card identical.
    /control     All other panels use esc(). contact_id, counts and thread ids are integers from
                 the API; thread_url is built by the server; turn_id uses encodeURIComponent.
    /control/offers, /control/offer/{id}, /control/turn/{id}
                 Escaped with html.escape through _e. Non-integer ids are refused or escaped.
    /whatsapp    List items, bubbles, quotes, reactions, flow summaries, audio, contact panel and
                 the path thread_id are escaped. Quick replies and search results use textContent.

  Not audited: /integrations/whatsapp/debug, and any page other than those named. The trace cards
  that the fixture does not populate were read in part, not fuzzed.

================================================================================
5. DEAD RENDERER
================================================================================
  ripgrep over all non-doc files: one hit, the definition. ripgrep is not installed system-wide;
  the copy bundled with the VS Code server was used. Docs mention it only as dead.
  AST over 445 Python files in backend, tests, scripts and meta: the definition only, covering
  names, attributes, imports and string constants.
  No __all__ in app/ui, no star import, no getattr or importlib access to kanban_view.
  Earlier reviews (2026-11-03, R2R-09) also recorded it as uncalled, and raising TypeError.

  Deleted. R2-14 fails if anything names it again; R2-15 fails if a registered route uses it and
  confirms /table still uses render_revisions_table_page. Full suite: NEW 0.

================================================================================
6. LIVE-DATA READ-ONLY CHECK
================================================================================
  Every query ran with default_transaction_read_only=on, confirmed by SHOW. Output was limited to
  table, column, indicator, count and internal ids. No row was modified.

  Indicators: tags, comments, event handlers, javascript:/vbscript:/data:text/html, and entities.
  Patterns passed positive controls for each indicator and a negative control (3<5 with accents).

    database   tables  columns  checks  columns with an indicator  rows reported
    crm_test       30      243    1215                          0              0
    crm            18      109     545                          0              0

  text, varchar, json and jsonb columns were all included, among them whatsapp_messages.raw_payload.
  Row volumes: crm_test 54 leads, 61 revisions, 36 contacts, 69 messages; crm 8 leads, 4 revisions,
  7 contacts, 31 messages.

  No suspicious rows, so the stop-and-request condition did not arise. This is a point-in-time
  result, taken 2026-11-05. New rows can arrive until the fix ships, so repeat it before launch.

================================================================================
7. BROWSER PROOF
================================================================================
  tests/escaping_browser_proof/run_proof.py, run on three backend trees.

  Isolation: the app ran in docker --network none on a Unix socket, with SQLite in memory. A
  loopback proxy forwarded to it. Chromium reached http://127.0.0.1 only; proxy and resolver rules
  refused every other host. No provider or external access. Every page reported that origin.
  The harness adds an operator session and injects its recorder and driver into HTML responses.

  The driver fired mouseover and focus on every element and opened the pickers, menus, filters and
  modals. Panels were confirmed populated, so zero is not an empty page: WhatsApp picker 6, lead
  picker 7, forward picker 6, /control paths 6, messages 36, threads 6.

                       pages  no result  executions  injected img  script  handlers
    198ed4d               25          0         173           148      67       174
    R1 tree               25          0          46            50      22        62
    this tree             25          0           0             0       0         0

  Executions by page         198ed4d   R1   final
    /kanban stored                11   11       0
    /table stored                 34    0       0
    /table reflected              60    0       0
    /profesionales stored         15    0       0
    /agencias stored              18    0       0
    /control stored                1    1       0
    /whatsapp/thread stored       10   10       0
    fallback thread page          24   24       0

  Reading those numbers:
    * Fallback thread page: requested with a hostile path id. The 24 executions are the STORED lead
      picker on that page. The reflected path id never executed on any tree.
    * Reflected /kanban and /control/turn: 0 on all trees; those sinks were already escaped.
    * /whatsapp/inbox: 0 on all trees; the pickers do not open there.
    * /control is 1 distinct execution: the jsq payload through S-4.
    * /control/offers and stored /control/turn were not in this run; R2-08 and R2-09 cover them.
    * Server logs show system_settings errors from the harness database. They affect only the AI
      toggle, which is outside this proof.

  This closes R1's file:// limit: /table, /profesionales and /agencias ran over HTTP here.
  Backend proven is byte-identical to the final tree; only test files changed afterwards.

================================================================================
8. TESTS AND RESULTS
================================================================================
  tests/test_operator_page_html_escaping_r2.py: 19 tests.
    R2-01..05b /kanban · R2-06..10 /control · R2-11..13 /whatsapp · R2-14..15 dead renderer ·
    R2-16 stored text unchanged after rendering
  Client sinks are pinned by source checks in pytest, because the runner has no browser. Their
  execution proof is section 7. R2-09 replaces every string of the real trace fixture with each
  payload.

  Negative control, R2 suite on R1 code: 44 failed, 13 passed (pytest counts failed subtests). That
  run predates the final two test edits.

  Runner: ridecheck-crm-test:l4.7b2, --network none, sqlite, tree mounted read-only.

  Focused, final tree         72 passed, 786 subtests (26 agenda + 27 R1 + 19 R2)
  Relevant suites (12 files)  14 failed / 303 passed; vs R1 NEW 0, GONE 0, messages differ 0
                              (one file added to the R1 list: test_m21_3_flow_endpoint_303)

  Full suite, final tree (digest 2d67511a434346da70cf84b6c68cb17ebcdcaeec05fd0b56f6602b149be06199)
    198ed4d           62 failed, 7256 passed, 677 skipped, 9 errors
    this tree         62 failed, 7328 passed, 677 skipped, 9 errors
    failing ids       71 vs 71, NEW 0, GONE 0
    skipped ids       NEW 0, GONE 0
    cases             +72, removed 0 (exactly the three new suites)
    vs R1 tree        NEW 0, GONE 0, messages differ 0

  One message differs from 198ed4d: test_10a_today_data_attr_correct, date only, pre-existing.
  Memory gate: 2040 MiB available, no pytest process at start. No vehicle test ran.

  Mutations, scratch copy: 47 of 47 killed, control passed.
    corrected R2 boundaries, helpers and the deleted renderer    25
    already-correct boundaries on the audited pages              22
  One mutant survived at first: the contact phone in the branch for a thread with no messages. That
  was a real test gap, closed by R2-12c, and it is now killed. Mutants ran before the last inventory
  refinement, which changed only R2-05b; none depended on it.

  Bytes
    lead card        45d0dcc7 on 198ed4d, R1 and this tree
    /calendar        identical to R1 rendered the same day
    /table, /profesionales, /agencias
                     identical to R1 rendered the same day, with row timestamps normalised
    /kanban          changed as intended: +211 bytes, the waEsc helper

================================================================================
9. VEHICLE-IDENTITY MERGE RESOLUTION (UPDATED)
================================================================================
  Compared with its working copy, kanban_view.py sha256
  40084f99a196de18e3ad3904632fa56bbd4e0c13251d22a4f642cb4c37a8e7b0. It is uncommitted and may change,
  so repeat at merge time.

  Overlapping paths: only backend/app/ui/kanban_view.py. The vehicle candidate does not change
  control_view.py or whatsapp_ui.py.

  git merge-file: 5 conflicts, the same 5 as before, all in render_calendar_page. R2 added none. The
  render_table_page deletion and the render_page and edit-form changes merge cleanly.

    1  _aveh / _aaddr       BOTH: vehicle join (dominio, kilometraje) AND our _esc_txt address
    2  slot card            ours (name and vehicle escaped)
    3  slot card, 2nd site  ours
    4  month card           ours
    5  week card            ours

  Do not take the vehicle side whole: conflict 1 would lose the address escape, and 2 to 5 would
  lose the name escape.

  Scratch merge with those resolutions (kanban_view.py sha256 starting e7a9e334f92d14d4): all three
  escaping suites pass, 72 passed, 786 subtests.

  The first merged run failed R2-05b. It was not a defect: the vehicle candidate added an already
  escaped conditional to the edit form, and the guard did not recognise that form. The guard now
  accepts a conditional whose branches are constants or escapers, and it still fails on anything
  else. The vehicle candidate changes render_lead_card and the edit form; its new sinks in the edit
  form passed that guard.

  Still required on the merged tree: vehicle suites, full SQLite suite, PostgreSQL-gated suites.

================================================================================
10. FINDINGS
================================================================================
  N-7 NEEDS OWNER CONFIRMATION. The running backend's DATABASE_URL names crm_test, while the
      postgres container's default database is crm. crm_test has more and newer rows (latest message
      4 days old against 133 for crm). Both were scanned, so the result stands either way. Confirm
      which database is live before launch. Nothing was changed.

  N-8 LOW, PRE-EXISTING, NOT FIXED. On /whatsapp/inbox the row menu's link action does nothing: that
      page has no utility modal. This is a functional defect, outside this class.

  N-3 of the agenda closeout is closed for existing rows by section 6. Rows arriving after the check
  are not covered.

================================================================================
11. PROPOSED COMMIT (NOT MADE)
================================================================================
  git add backend/app/ui/kanban_view.py backend/app/ui/control_view.py backend/app/ui/whatsapp_ui.py \
    tests/test_agenda_customer_data_escaping.py \
    tests/test_operator_page_html_escaping.py \
    tests/test_operator_page_html_escaping_r2.py \
    tests/escaping_browser_proof \
    docs/Launch/2026-11-04_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-AGENDA-CUSTOMER-DATA-ESCAPING_CLOSEOUT_001.md \
    docs/Launch/2026-11-04_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-OPERATOR-PAGE-DYNAMIC-HTML-ESCAPING-R1_CLOSEOUT_001.md \
    docs/Launch/2026-11-05_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-OPERATOR-PAGE-DYNAMIC-HTML-ESCAPING-R2_CLOSEOUT_001.md

  fix(launch): escape dynamic values across the operator pages

  Customer, operator and request values reached the agenda, /kanban,
  /table, /profesionales, /agencias, /control and the WhatsApp thread
  as markup or script. Escape each value once at its sink for its
  output context, keep inline handlers free of stored strings, and
  delete the unreachable render_table_page. Nothing is stored escaped.

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

================================================================================
12. NEXT STEPS
================================================================================
  1. Independent review of this candidate.
  2. Owner confirmation of N-7.
  3. Merge per section 9, then run vehicle, full SQLite and PostgreSQL-gated suites.
  4. Repeat the read-only check of section 6 before launch.

STOP.
