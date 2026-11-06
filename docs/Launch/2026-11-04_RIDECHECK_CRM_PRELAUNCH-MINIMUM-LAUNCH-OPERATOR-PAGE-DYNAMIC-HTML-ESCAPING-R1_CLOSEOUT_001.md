PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-OPERATOR-PAGE-DYNAMIC-HTML-ESCAPING-R1

Date: milestone dated 2026-11-04, final runs completed 2026-11-05. Local correction only. Nothing
staged, committed, pushed, merged, built or deployed. No schema migration, Meta contact, feature
flag or live data was touched. All names, addresses and payloads are synthetic.

================================================================================
0. VERDICT
================================================================================
READY FOR INDEPENDENT REVIEW

  Proven: stored and reflected markup no longer executes on /table, /profesionales or /agencias.
  Chromium recorded 106 distinct payload executions before the correction and 0 after. Values are
  escaped exactly once, accented Spanish is unchanged, the agenda correction is intact, and the
  lead card is byte-identical. Full suite on the final tree: NEW 0 / GONE 0.

  The reviewer must know these limits:
    * PostgreSQL-gated suites were not run. See 8.
    * The vehicle-identity merge was tested with the two escaping suites only. See 7.
    * /kanban keeps the old zones-block escaping and was not audited here (N-4, section 9).

================================================================================
1. BRANCH, WORKTREE, HEAD
================================================================================
  branch     fix/prelaunch-agenda-customer-data-escaping
  worktree   /opt/ridecheck-crm-agenda-escaping
  HEAD       198ed4d6a7157230a616063094e2d50fb9623836 (unchanged; base of this work)
  index      empty

  No other worktree was modified. The vehicle-identity candidate was read and copied to scratch.

================================================================================
2. COMBINED CANDIDATE MANIFEST (exact, agenda + operator pages)
================================================================================
  M  backend/app/ui/kanban_view.py
       sha256 b1baec0109462970263a6a814df632b35f9d6cb78f3a12ab45dde12a751ccfbf
       +68/-60 against 198ed4d
  A  tests/test_agenda_customer_data_escaping.py
       sha256 f38e1b0c238d42b0211c647e76afb6798f1682161e4587651a5c07bacb12f090 (unchanged)
  A  tests/test_operator_page_html_escaping.py
       sha256 e5c96c79ee80043b44d893d68bb75bd9e19b4d7856b52329c8f5dcc03fc10445
  A  docs/Launch/2026-11-04_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-AGENDA-CUSTOMER-DATA-ESCAPING_CLOSEOUT_001.md
       sha256 864a75e25628deeef92c536d2a83322771c354a1feed193ecb3b74b97bc48af2 (unchanged)
  A  docs/Launch/2026-11-04_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-OPERATOR-PAGE-DYNAMIC-HTML-ESCAPING-R1_CLOSEOUT_001.md
       this file

  Reconciliation by function in kanban_view.py:
    agenda milestone     render_calendar_page
    this milestone       _filters_form_html, render_revisions_table_page,
                         render_profesionales_page, render_agencias_page
    Nothing else changed. The AST comparison was made function by function against 198ed4d and
    against the agenda candidate. render_calendar_page is identical to the agenda candidate.

  The operator test file imports its parse tree and client fixture from the agenda test file, so
  the two must be committed together.

  The agenda closeout is kept as written. Its N-1 (/table, NOT FIXED) is superseded by this file.

================================================================================
3. SINK AUDIT
================================================================================
  Routes: GET /table, /profesionales, /agencias in backend/app/ui/kanban.py.

  Corrected (47 sites)
    _filters_form_html (5)
      zone group and zone detail options, each value attribute and text; back link href
    render_revisions_table_page (29)
      chips: estado, flag, profesional, canal, marca, modelo, tipo vehiculo, anio, zona grupo,
        zona detalle, estado revision, date label, q (13)
      chip href, through _make_table_link (1)
      row cells: nombre, apellido, telefono, email, estado, flag, tipo, marca, modelo, anio,
        zona grupo, zona detalle, estado revision (13)
      row "Abrir" href (1)
      zones JSON block (1)
    render_profesionales_page (5)
      nombre, apellido, email, telefono, cargo
    render_agencias_page (8)
      nombre, direccion, mail, telefono, vendedor cell, file name, both vendor option lists

  Already correct, confirmed and now covered by mutants
    filter inputs (q, marca, modelo, anio, dates), text zone inputs, professional options,
    table search value, data-search, agency modal inputs, _url_link, user email

  By output context
    HTML text and attributes   _esc_txt, _esc_val, html.escape(quote=True)
    URLs and query parameters  urlencode for the query, then html.escape for the href
    JavaScript, inline         handlers hold integer primary keys only (OP-21)
    JavaScript, JSON block     < written as <; JSON.parse restores the same map (OP-09)
    Client script              zone options use textContent; the one innerHTML is a constant

  Safe by construction, deliberately not escaped: integer ids, strftime/isoformat output, numeric
  formatting, constant lists and labels, booleans picking a fixed word. OP-18 fails on any
  interpolation in these four functions that is neither escaped nor classified.

  Plain values stay plain: _txt, _val, _profesional_label, _build_query_string, vend_name and
  kanban_href. Each feeds a second sink or a URL, so escaping them early would escape twice.

  Rendered bytes change, decoded content does not: & in href is now &amp;, and < in the zones block
  is <. Browsers and JSON.parse read the same values as before.

  Zones block: the old replace("</", "<\/") stopped </script> but not <!--<script>. On the old page
  that zone name consumed the end of the document, including the page's own script.

  Download route: /ui/agencia_file/{id} is not an HTML sink on these pages. It returns FileResponse
  with a filename. Its response headers were read from source, not tested.

================================================================================
4. TESTS
================================================================================
  tests/test_operator_page_html_escaping.py, 27 tests:
    OP-01..09b /table · OP-10..11 /profesionales · OP-12..17 /agencias
    OP-18..21  sink inventory · OP-22..26 through the routes and real tables

  Payloads: tags, script, img/onerror, svg/onload, double and single quote breakouts, attribute
  then tag, quotes, ampersands, query separators, text that looks like an entity, closing cell,
  comment and select, URL-encoded, double-URL-encoded, javascript: URLs.

  Proof method: hostile and benign pages must have identical element and attribute structure; each
  value must read back equal to the original after one decode, so missing and double escaping both
  fail. Links are parsed back to the original filters. OP-07 checks raw source for bare ampersands.

  Storage: OP-25 writes through /ui/profesional_create and /ui/agencia_create, renders, then reads
  the raw columns: text is stored as typed. OP-26 shows rendering does not change its inputs.

  Negative control, operator suite on the agenda-only kanban_view.py:
    155 failed, 22 passed (pytest counts failed subtests)

================================================================================
5. RESULTS
================================================================================
  Runner: ridecheck-crm-test:l4.7b2, --network none, DATABASE_URL=sqlite://, tree mounted read-only.

  Focused, final tree
    operator + agenda suites      53 passed, 557 subtests. Agenda: 26 of 26 intact.

  Relevant existing suites (11 files)
    this tree                     14 failed / 277 passed
    vs 198ed4d                    NEW 0, GONE 0
    vs agenda candidate           NEW 0, GONE 0

  Full suite, final tree (tree digest 6a81456de712539e689202edf764f122958dc0c0022f0da5d7bfe5813f4f765a)
    198ed4d                       62 failed, 7256 passed, 677 skipped, 9 errors
    this tree                     62 failed, 7309 passed, 677 skipped, 9 errors
    failing identities            71 vs 71, NEW 0, GONE 0
    skipped identities            NEW 0, GONE 0
    cases added / removed         53 / 0 (exactly the two new suites)
    vs agenda candidate           NEW 0, GONE 0

  One failure message differs, in both comparisons:
    test_m21_3_demo_agenda_week::TestWEEK_10_HoyDate::test_10a_today_data_attr_correct
    It reports today's date. The baselines ran on 2026-11-04 and this run on 2026-11-05. Normalising
    the date removes the difference. It is a pre-existing failure.

  Memory gate: 1969 MiB available and no pytest process at start. Availability fell to about
  1457 MiB during the run, which completed normally. No vehicle-candidate test ran concurrently.

  Mutations: 76 of 76 killed, unmutated control passed. Scratch copy only.
    corrected boundaries          48 (47 sites; zones block has 2 mutants)
    already-correct boundaries    17
    double or early escaping      11
    killed by behavioural tests   75
    killed by inventory only      1

  The inventory-only mutant is the lead estado cell. _lead_operational_estado returns only known
  constants, so no payload can reach that sink. It is escaped for uniformity.

================================================================================
6. BROWSER, AGENDA AND LEAD CARD
================================================================================
  Pages were served through the real routes in the sealed runner, then loaded from file in headless
  Chromium with an injected driver. The driver fired mouseover and focus on every element, opened
  the filters, changed the zone select and opened every agency modal. 12 pages per tree.

                             executions  injected img  injected script  payload handlers
    198ed4d                     106           85            41                84
    agenda candidate            106           85            41                84
    this tree                     0            0             0                 0

  Before the correction, /table stored 37, /table reflected 36, /agencias 18, /profesionales 15.

  Limits of those numbers:
    * Quote-breakout payloads did not execute on the reflected pages, because those filter sinks are
      text or were already attribute-escaped. They are covered in the suite.
    * The canal chip was injected but did not execute: its title-casing broke the handler. It counts
      as an injected element, not an execution.
    * The <!--<script> page is outside the totals on the two old trees: it swallowed the driver, so
      no result was produced. On this tree the driver ran and the page script was live.

  Spanish in Chromium: chips, cells and the q input show José Ñandú Peñalver & Hijos — Córdoba
  "Año" O'Higgins exactly.

  Rendered bytes, build stamp normalised
                  198ed4d    agenda candidate    this tree
    lead card     45d0dcc7   45d0dcc7            45d0dcc7
    /kanban       a4a23c9d   a4a23c9d            a4a23c9d
    /calendar     26f12812   f9ecf4d4            f9ecf4d4

  The lead card is unchanged across all three and its fixture holds hostile values. AE-22 still
  proves it escaped once. /calendar differs from 198ed4d only through the agenda correction.

================================================================================
7. VEHICLE-IDENTITY CANDIDATE
================================================================================
  Compared with its working copy of kanban_view.py, sha256
  40084f99a196de18e3ad3904632fa56bbd4e0c13251d22a4f642cb4c37a8e7b0. That candidate is uncommitted
  and may change, so repeat this at merge time.

  Overlapping paths: only backend/app/ui/kanban_view.py.

  git merge-file: 5 conflicts, the same 5 reported by the agenda closeout, all in
  render_calendar_page. None comes from this milestone. The vehicle candidate does not change
  _filters_form_html or the three operator renderers, which merged cleanly.

    1  _aveh / _aaddr       take the vehicle join (dominio, kilometraje) AND our _esc_txt address
    2  slot card            take ours (name and vehicle both escaped)
    3  slot card, 2nd site  take ours
    4  month card           take ours
    5  week card            take ours

  Conflict 1 needs both sides. Taking theirs whole would revert the address escape. In 2 to 5 the
  vehicle side escapes only the vehicle line, so taking theirs would revert the name escape.

  Scratch merge with these resolutions: both escaping suites pass, 53 passed, 557 subtests. The
  vehicle candidate's own suites and the full suite were NOT run on that merge.

  render_lead_card is changed by the vehicle candidate, not by this work.

================================================================================
8. NOT DONE OR NOT PROVEN
================================================================================
  * PostgreSQL-gated suites were not run. All runs used SQLite.
  * No test ran against a deployed server. Nothing was deployed.
  * Pages were not exercised over a live HTTP origin; Chromium loaded them from file.
  * The 62 failures and 9 errors are pre-existing and unchanged, not fixed. This includes the 11
    agenda tests from N-2 of the agenda closeout.
  * Merged-tree proof is limited as stated in section 7.

================================================================================
9. FINDINGS
================================================================================
  N-4 MEDIUM, PRE-EXISTING, NOT FIXED, OUTSIDE THIS MILESTONE. render_page (/kanban) still uses the
      old zones-block replace at kanban_view.py:1797, so a zone named <!--<script> is expected to
      break that page as it did /table. Not run on /kanban. The fix is the same one-line change.

  N-5 PRE-EXISTING, NOT AUDITED. /kanban filter chips, /control and /whatsapp/inbox were outside
      this milestone. Do not read this result as covering every operator page.

  N-6 LOW, DEAD CODE. render_table_page has no callers and keeps its unescaped chips and rows. It
      shares the corrected filter form. Delete or correct it before anything routes to it.

  N-3 of the agenda closeout still stands, extended to these pages: whether live rows already hold
  markup was not assessed. Until this ships, the deployed pages render stored and reflected markup.
  /table links can carry a payload, so treat crafted links sent to operators as a live exposure.

================================================================================
10. PROPOSED COMMIT (NOT MADE)
================================================================================
  git add backend/app/ui/kanban_view.py \
    tests/test_agenda_customer_data_escaping.py \
    tests/test_operator_page_html_escaping.py \
    docs/Launch/2026-11-04_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-AGENDA-CUSTOMER-DATA-ESCAPING_CLOSEOUT_001.md \
    docs/Launch/2026-11-04_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-OPERATOR-PAGE-DYNAMIC-HTML-ESCAPING-R1_CLOSEOUT_001.md

  fix(launch): escape dynamic values on the agenda and operator pages

  Customer, operator and request values reached the agenda, /table,
  /profesionales and /agencias as markup, and /table reflected its
  filters. Escape each value once at its sink for its output context.
  Nothing is stored escaped.

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

================================================================================
11. NEXT STEPS
================================================================================
  1. Independent review of this combined candidate.
  2. Owner decision on N-4 and N-5, and on the live-data check in N-3.
  3. At merge: resolve section 7 as listed, then run the vehicle and full suites on the merged tree.
  4. Run the PostgreSQL-gated suites where that database is available.

STOP.
