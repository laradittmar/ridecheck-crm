PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-OPERATOR-PAGE-DYNAMIC-HTML-ESCAPING-R2-INDEPENDENT-REVIEW

Date: 2026-11-05. Independent review. This file is the only write to the candidate worktree. Nothing
staged, committed, merged, built, deployed or migrated. No flag or DATABASE_URL change, no Meta
contact, no row modified. All payloads are synthetic. No customer value was printed or recorded.

================================================================================
0. VERDICT
================================================================================
READY FOR CONTROLLED MERGE

  No blocking finding. On a fixture far wider than the supplied one, 72 pages produced 0 executions,
  0 injected elements and 0 surviving handlers on the candidate. The same fixture on 198ed4d produced
  1474 executions and 2540 injected elements. Escaped-exactly-once holds in every context tested, and
  the full SQLite comparison is NEW 0 / GONE 0.

  The owner should know:
    * One real test gap, LOW, not a production defect (A-1): in the client-side escapers, an ordering
      or double-escape regression passes pytest. 3 of my 20 mutants survived. The code is correct
      today, proven in Chromium. The fault such a regression would cause is `&amp;lt;` shown to the
      operator, not execution.
    * The supplied browser proof does not include /calendar and fills only part of the schema (A-2).
      This review covered both. The harness itself is unchanged.
    * Still required on the merged tree: full SQLite and PostgreSQL-gated suites (section 11).
    * The fixes protect nothing until deployed. Deployed pages stay exposed until then.

================================================================================
1. SCOPE AND MANIFEST
================================================================================
  Candidate  /opt/ridecheck-crm-agenda-escaping, branch fix/prelaunch-agenda-customer-data-escaping
  HEAD       198ed4d6a7157230a616063094e2d50fb9623836 (unchanged), index empty

  A full tree diff of a snapshot against `git archive 198ed4d` found exactly the declared set:
    M  backend/app/ui/kanban_view.py    e555a7f41f8960a0...  +77/-767   matches closeout
    M  backend/app/ui/control_view.py   f57af9c56d4edacf...  +3/-2      matches closeout
    M  backend/app/ui/whatsapp_ui.py    5c31eb26f28c2146...  +6/-2      matches closeout
    A  3 test files, 3 files in tests/escaping_browser_proof, 3 closeouts: all sha256 match section 2
       of the R2 closeout. The R2 closeout, which cannot state its own hash, is 8ea15c9aab733b21...
  Nothing else differs.

  kanban_view.py, compared function by function from the AST: _filters_form_html, render_page,
  render_calendar_page, render_edit_latest_revision_form, render_revisions_table_page,
  render_profesionales_page, render_agencias_page changed; render_table_page removed. No other
  top-level object changed.

  Tests: no existing test file is modified, so no test, assertion or expected result was weakened.
  8003 cases on 198ed4d are all present on the candidate; 0 removed; +72 are exactly the three new
  suites. The new suites hold no skip, xfail or assert True.

  Secrets: no token, key, connection string or private-key shape in any added file. The only match is
  the fixture's own proof-signing-key. 23 sensitive values from the running backend environment were
  compared in memory against the added files, values not printed: 0 present. No live phone or email.
  Digit runs in the closeouts are hash fragments. No report, log or generated evidence is in the tree.

  Stored values: the diff adds no write call (commit, add, flush, execute, update, delete).

================================================================================
2. ESCAPING CORRECTNESS, BY CONTEXT
================================================================================
  Every changed sink was read in the diff. Unchanged sinks the closeouts call safe were read as well:
  all innerHTML assignments, inline handlers, location and href assignments, and data-* read-backs in
  the three files.

  HTML text        _esc_txt, html.escape, waEsc, esc. Correct.
  HTML attributes  quote=True throughout. control esc() gained the single quote. Correct.
  URLs             _url_link allows only http(s) and escapes once. Maps and Waze are percent-encoded.
                   /table links are urlencoded, then escaped once for the href. Correct.
  JSON             both zones blocks replace `<` with backslash-u003c inside a type=application/json
                   block. Bytes confirmed at kanban_view.py:1798 and :5971. Non-executing, and
                   JSON.parse restores the value.
  JS and inline    the only stored string in an inline handler was path_id. It now travels in
  handlers         data-path and is read with this.dataset.path, the correct fix: HTML escaping cannot
                   protect a value the browser decodes before the JavaScript runs. Every remaining
                   inline handler holds an integer id, a constant, or this.dataset.detail, a generated
                   id. filterTraceByPath uses the value in a filter and encodeURIComponent.
  data-* read-back data-display-name and data-wa-id go back through waEsc before innerHTML (S-3).
                   Other read-backs reach textContent, parseInt, fetch paths or comparisons.
                   showMonthDay and navGo copy already-parsed holders; safe because the holders are
                   escaped, and exercised here.

  Confirmed safe, unchanged: toasts receive constants only. paintPaid uses a literal plus textContent.
  fmtTime and fmtDateTime cannot return input, because new Date never throws on a bad string.
  dirBadge and pathStatusBadge escape their fallback. renderFindResults uses textContent.

  Exactly once, in the browser: a marker row with an apostrophe, ampersand, double quote, tag and n
  with tilde was placed in every column and read back after rendering.
                              text good  text bad   attr good  attr bad
    candidate                     1125      2 (*)        406        0
    198ed4d                        691        671        292        0
    (*) Both are the raw trace JSON in a <pre>, where a backslash before the quote is correct for that
        context. Identical on 198ed4d. Not a defect.
  Pickers were captured while open: lead picker 6 good / 0 bad, forward picker 6 good / 0 bad.
  Detector control: with the amp-last mutants applied, the same check reported 0 good / 6 bad in both
  pickers and 40 bad on /control, so these zeros are not blind.

  Unchanged storage: AE-20, OP-25, OP-26 and R2-16 pass, the diff has no write call, and the render
  path is the only code touched.

================================================================================
3. HIGH-RISK PATHS
================================================================================
  Same fixture, sealed origin. Fields are those that executed or injected on 198ed4d.

  Path                                  198ed4d                                candidate
  /kanban picker display_name, wa_id    EXECUTED, both fields                  0
  WhatsApp lead picker name, phone      EXECUTED: nombre, apellido, telefono   0
  forward picker display_name, wa_id    EXECUTED, both fields                  0
  /control path_id                      EXECUTED through the jsq breakout      0
  agenda name, address, vehicle         EXECUTED: nombre, apellido, direccion, 0
                                        marca, modelo, tipo, also estado and
                                        professional name and cargo
  zone values                           EXECUTED: zone_group; edit form        0
  reflected q and marca on /table       EXECUTED: q, marca and 10 other params 0
  zone name <!--<script>                7 pages swallowed, no driver result    0 swallowed, script live

  All eight reproduced on 198ed4d and were disproved on the candidate. Reflected input on /kanban and
  /calendar never executed on either tree.

================================================================================
4. BROWSER PROOF
================================================================================
4.1 Supplied harness, unmodified
                 pages  no result  executions  img  script  handlers
    198ed4d         25          0         173  148      67       174
    candidate       25          0           0    0       0         0
  Matches R2 closeout section 7 exactly.

4.2 Independent proof (scratch only, not part of the candidate)
  Why: the supplied fixture leaves dozens of columns empty, /calendar is not a page in it, and its
  detectors match only img[src=x] and handlers holding one marker. A value that injected another tag
  without firing would read as zero.

  Sealing: the app ran in docker --network none on a Unix socket, SQLite in memory. Proxy and Chromium
  ran in an empty kernel network namespace (unshare -n, loopback only). An external connection from
  inside failed with `Network is unreachable` on every run. No provider contact was possible.

  Fixture: EVERY string and JSON column of every model, 202 columns, 825 rows, 11 attacks, 3 URL
  variants: img, svg, details/ontoggle, double- and single-quote attribute breakouts, closing-tag
  iframe srcdoc, two JS string breakouts, javascript: URL, pre-encoded entities, the once marker. The
  /control/turn trace is the real fixture with every string hostile.
  Pages: all seven in scope, including /calendar day, week and month, the two innerHTML copies, the
  Cobro modal, lead and revision edit forms, all pickers, /control/offers, offer and turn details,
  /integrations/whatsapp/debug, every string query parameter of each route, hostile path ids.
  Detectors: any element carrying the data-pz marker, live or transient (MutationObserver), on*
  attributes, javascript: URLs, iframes, scripts, plus the execution recorder.

                 pages  no result  exec   injected(live)  injected(transient)  handlers  iframes
    198ed4d         72          3  1474            2540                 1703      1929      570
    candidate       72          3     0               0                    0         0        0
    merged tree     72          3     0               0                    0         0        0

  Residual flags, each explained, none a finding:
    * 3 no-result pages on all trees: FastAPI 422 JSON for a non-integer offer id. Not HTML.
    * 12 "script" hits on all trees: the zones-data block. type=application/json, does not execute.
    * Server log errors: system_settings missing and parallel use of one SQLite connection. Harness
      only, unrelated to the payloads.

  Populated, so zero is not an empty page (candidate): agenda 55 week rows and 44 month cards, /kanban
  34 cards and 33 in the picker, lead picker 34, forward picker 33, /control 33 threads, 66 message
  rows, 16 paths, 25 turns, 19 booking rows, 33 critical events, 83 condition rows. No page raised a
  client error.

  Limits of this proof:
    * Columns held to fixed values by a CHECK constraint carry legal values. PostgreSQL enforces the
      same constraints, so hostile text cannot be stored there.
    * lead.estado and message direction and type were kept structural so rows render. Audio, image,
      reaction and quote bubbles were not driven in the browser; supplied mutants on those sinks are
      killed by pytest.
    * /login and other public pages were outside scope.

================================================================================
5. DEAD RENDERER
================================================================================
  On 198ed4d, AST over all 443 Python files (names, attributes, imports, string constants): one hit,
  the definition at kanban_view.py:2890. whatsapp_ui.py has a UTF-8 BOM; it was parsed with that
  handled, so it is included.
  Dynamic lookup: no getattr, import_module, eval or star import reaches kanban_view. The only
  globals() calls, at lines 2947-2948, read ICON_* inside render_table_page itself and went with it.
  Non-Python files outside docs: no reference.
  Runtime walk of the 125 registered routes on both trees: no endpoint names it, and no app module
  holds it apart from kanban_view on 198ed4d. app.ui.kanban never imported it.
  /table resolves to table_view, which calls render_revisions_table_page, on both trees.
  Deletion is safe.

================================================================================
6. REGRESSION VERIFICATION
================================================================================
  Runner: ridecheck-crm-test:l4.7b2, --network none, DATABASE_URL=sqlite://, read-only snapshots.

  Focused suites     72 passed, 786 subtests
  On 198ed4d code    275 failed, 60 passed (pytest counts failed subtests)

  Full SQLite, same day, serial. Gate: 1790 MiB available at start, no pytest, no vehicle test.
    198ed4d     62 failed, 7256 passed, 677 skipped, 9 errors
    candidate   62 failed, 7328 passed, 677 skipped, 9 errors
    failing ids 71 vs 71, NEW 0, GONE 0, messages differ 0
    skipped ids NEW 0, GONE 0
    cases       +72, removed 0
    vs supplied final run: NEW 0, GONE 0, same 8075 identities

  Relevant UI suites, taken from those two runs: 47 modules (calendar, kanban, control, WhatsApp UI,
  ux, cobro, minimum launch, R1, R2, e2e, flow endpoint), 1353 cases. Outcome identical case by case:
  1164 passed, 162 skipped, 27 failed. These suites were not rerun in isolation.

  PostgreSQL-gated suites deferred, as authorised.

  Supplied mutation evidence: 45 killed + 2 split follow-ups = 47. It reconciles with the closeout, and
  its one early survivor became R2-12c.

  Independent mutants, chosen to differ from the supplied set: 20, unmutated control passing.
    KILLED 17   table link, back link, /table zones block, kanban waEsc amp-last, address, professional
                raw and double, any-scheme URL, agency vendor and file, start time, week status, table
                email and open href, estado chip, edit-zone double, control data-path double.
    SURVIVED 3  whatsapp_ui waEsc amp-last, control esc amp-last, lead picker waEsc(waEsc()). See A-1.

================================================================================
7. VEHICLE MERGE PLAN
================================================================================
  Vehicle worktree read only; its kanban_view.py sha256 40084f99a196de18... matches the closeout and
  was unchanged at the end of this review. It is uncommitted, so repeat at merge time.

  git merge-file reproduces 5 conflicts, all in render_calendar_page:
    1  _aveh / _aaddr   BOTH: vehicle join (dominio, kilometraje) AND `_esc_txt(r.direccion_texto)`
    2  slot card        ours
    3  slot card 2nd    ours
    4  month card       ours
    5  week card        ours
  In 2 to 5 the conflict is the NAME line only. The vehicle line is the same on both sides and merges
  by itself, so "ours" keeps both.

  Independently resolved, the result hash starts e7a9e334f92d14d4, the same as the closeout.
  render_table_page is gone from it, and it parses.

  Both requirements hold:
    a. Mileage and plate: test_vehicle_identity_recovery 102 passed on the merged tree, 102 passed on
       the vehicle tree alone, identities identical. Plate and mileage reach the agenda through _aveh,
       escaped at each sink.
    b. Escaping: 72 passed, 786 subtests. Browser proof on the merged tree: 0 of everything, with
       revisions.dominio hostile. The _txt/_esc_txt sink list differs from the candidate by two added
       lines only, both escaped. The vehicle side's other sinks (render_booking_details_block, _t,
       _esc_val) escape too.
  Control: taking the vehicle side in all five conflicts fails the agenda suite, 68 failed. A wrong
  resolution would be caught.

  control_view.py and whatsapp_ui.py are byte-identical to 198ed4d in the vehicle tree and untouched by
  its changes. No overlap.

================================================================================
8. LIVE-DATA EVIDENCE
================================================================================
  Supplied method reviewed: SELECT only, default_transaction_read_only=on, output limited to counts
  and ids, all text, varchar, json and jsonb columns. Sound.
  Two gaps (A-3): the claimed positive and negative controls are not in the retained script, so they
  could not be inspected; and quote-only JS breakouts, the S-4 class, were not an indicator.

  Repeat, crm_test only, per the owner's N-7 decision. Read-only confirmed by SHOW, and a deliberate
  CREATE TEMP TABLE was refused. Counts only.
    backend database name   crm_test
    tables / columns        30 / 243 (varchar, text, jsonb; no json or array columns exist)
    checks                  2187
    controls                9 positive, 6 negative, 0 failures
    closeout indicators     tag, comment, handler, script URL, entity: 0 hits
    added indicators        percent-encoded tag, literal backslash-u003c, template: 0 hits
    js_break heuristic      14 hits, hybrid_decision_traces.payload only
    rows                    leads 54, revisions 61, contacts 36, messages 69 (same as closeout)

  The 14 hits were reduced to shape, without values: quote, semicolon, space, key_name, equals. That is
  machine-generated key='value'; key= trace text, not a breakout, and that sink is escaped. Not a
  finding.

  Result: no markup in crm_test at this point in time. No row was cleaned or changed. Repeat before
  launch, because rows keep arriving.

================================================================================
9. OWNER DECISIONS RECORDED
================================================================================
  N-7  CLOSED BY OWNER DECISION. crm_test is the authoritative operational database for this launch.
       DATABASE_URL is not to be switched to crm. crm is stale. The database name is accepted launch
       debt, to be corrected separately after launch. Confirmed: the backend reads crm_test.
  N-8  ACCEPTED LOW LAUNCH DEBT. The link action on /whatsapp/inbox does nothing, because that page has
       no utility modal. Not fixed here.

================================================================================
10. FINDINGS (none blocking)
================================================================================
  A-1 LOW, TEST GAP. Pytest cannot run client JS, so waEsc and esc are pinned by source text. Moving &
      last, or wrapping waEsc twice, passes all 72 tests in whatsapp_ui.py and control_view.py. The
      kanban copy is killed. run_proof.py does not check exactly-once either. Effect of such a
      regression: visible entities in pickers and /control, never execution. Production code is
      correct today (section 2). Suggested later: pin the exact escaper bodies, or add the once check
      to the browser proof.
  A-2 LOW, PROOF COVERAGE. tests/escaping_browser_proof omits /calendar, and its fixture and detectors
      are narrower than its zero suggests. The R2 closeout's "closes R1's file:// limit" is accurate
      for the pages it names, but the agenda's own browser proof was still file-based. Closed by
      section 4.2 for this candidate. Do not read that harness alone as agenda coverage in future.
  A-3 LOW, EVIDENCE. See section 8. Closed by the repeat scan.
  A-4 INFO, DOC TEXT. R1 closeout section 3 reads "< written as <". The backslash-u003c sequence was
      lost when the document was written. The code is correct. The closeouts were not edited.

================================================================================
11. REQUIRED AT MERGE
================================================================================
  1. Reconfirm the vehicle kanban_view.py hash, then resolve per section 7. Do not take the vehicle
     side whole.
  2. Keep all three test files and tests/escaping_browser_proof together: they depend on each other.
  3. On the merged tree: full SQLite suite, vehicle suites, PostgreSQL-gated suites. Only the escaping
     suites, the non-PG vehicle suite and the browser proof were run on a merge in this review.
  4. Repeat the read-only scan before launch.

================================================================================
12. EVIDENCE AND ISOLATION
================================================================================
  Evidence is in this session's scratch directory and is not part of the candidate:
    /tmp/claude-0/-root/15ef24a3-6299-4182-960f-83cdfb89cf7d/scratchpad
  Before and after: candidate hashes, vehicle hash and HEAD unchanged. Index empty. Backend, postgres
  and n8n containers left running, none touched apart from the read-only scan and one read of the
  backend environment. No review container remains. The n8n connector was down and was not needed.
  One command containing `rm` was refused by the permission layer and was rewritten without it.

STOP.
