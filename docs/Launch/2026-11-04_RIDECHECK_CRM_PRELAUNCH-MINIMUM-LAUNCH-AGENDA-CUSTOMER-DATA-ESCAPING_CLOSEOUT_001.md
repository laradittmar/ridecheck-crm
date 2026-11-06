PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-AGENDA-CUSTOMER-DATA-ESCAPING

Date: 2026-11-04. Local correction only. Nothing staged, committed, pushed, merged, built or
deployed. No schema migration, Meta Flow, feature flag, allowlist or live data was touched. All
names, addresses and payloads in tests and in this file are synthetic.

================================================================================
0. VERDICT
================================================================================
READY FOR INDEPENDENT REVIEW

  Proven: F-1 is fixed in every reachable agenda surface. Hostile values execute on 198ed4d and
  do not execute on this tree in real Chromium. Text is escaped exactly once, Spanish text is
  unchanged, and the lead card is byte-identical. Full suite NEW 0 / GONE 0.

  The reviewer must know two limits:
    * The full suite ran on this production code, but NOT on the final test file. A last full pass
      was stopped by the host's low-memory protection at about 2% and was not restarted. See 7.2.
    * PostgreSQL-gated suites were not run. See 7.4.

  One new finding needs an owner decision: /table has the same defect, plus a reflected form of
  it (N-1, section 11). It is outside this milestone and is NOT fixed.

================================================================================
1. BRANCH, WORKTREE, HEAD
================================================================================
  branch     fix/prelaunch-agenda-customer-data-escaping (new; confirmed absent first)
  worktree   /opt/ridecheck-crm-agenda-escaping (new; confirmed absent first)
  HEAD       198ed4d6a7157230a616063094e2d50fb9623836 (unchanged; no commit made)
  upstream   none; the branch exists only on this host
  /opt/ridecheck-crm-vehicle-identity-recovery was read and never written.

================================================================================
2. THE DEFECT, AND WHAT F-1 UNDERSTATED
================================================================================
  F-1 named the customer name and address. The audit found more raw sinks in the same cards:

  Value              Week / month / slot cards on 198ed4d
  customer name      raw   `_txt(_aname(l))`, 4 sites
  address            raw   `_aaddr`, through `_ameta`, 4 sites
  vehicle            raw   `_txt(_aveh(r))`, 4 sites (F-1 fixed this only in its own candidate)
  revision status    raw   `_txt(r.estado_revision)`, 4 sites
  professional       raw   `_aprof`, through `_ameta`, 4 sites
  day start time     raw   `start_str`, 1 site (server-formatted today; escaped because the value
                           crosses the boundary as an untyped string)

  `_day_slots` holds 2 of each 4-site group. It has no caller. It was corrected, not removed.

  The day card already escaped its values; the fix does not change it, apart from the start time.

================================================================================
3. THE FIX
================================================================================
  backend/app/ui/kanban_view.py, inside `render_calendar_page` only (+25 / -18).

  * Name, vehicle and status sinks: `_txt(...)` -> `_esc_txt(...)`.
  * `_aaddr` escapes its fallback text. Its `_url_link` branch already escaped.
  * New `_ameta_row(r)` escapes the professional and joins the line, replacing four identical
    calls to `_ameta(_aaddr(r), _aprof(r))`.
  * `start_str` escaped.

  Exactly once. `_aname`, `_aveh` and `_aprof` deliberately stay plain text: `data-search` and the
  day card escape them at their own sinks, so escaping inside the helpers would show `&amp;` to the
  operator. `_aaddr` and `_ameta_row` return finished HTML and nothing escapes them again. A comment
  at the helpers records this. AE-16, AE-17 and mutants double-* and helper-escapes-name enforce it.

  Nothing stored changes. No write path was touched; AE-20 reads the raw columns after a page view.

================================================================================
4. AUDIT OF THE RENDERING BOUNDARY
================================================================================
  Every f-string interpolation in `render_calendar_page` was enumerated from the AST, not by reading.
  That list is now AE-15: a new unclassified interpolation fails until someone classifies it.

  Escaped at the sink    names, vehicle, zones, address, status, professional, phone (tel: href,
                         fixed scheme), amount, modal data-* and aria-label, data-search, day-start
                         values, date-header data-*
  Safe by construction   integer ids (lead, revision, thread), counters, dates and times, constant
                         labels, class names from fixed sets
  Percent-encoded        Maps and Waze URLs, `_urlquote(..., safe="")`
  Finished HTML          `_url_link`, `_ameta_row`, `pago_chip`, `stat_html`, `estado_raw`
  Constant only          `_render_revision_approval_ui` on this page (ids and fixed labels)
  Elsewhere on the page  `_sidebar_user_block` escapes the email

  Client side
    Cobro modal      `textContent` from dataset; no customer value reaches `innerHTML`
    paintPaid        `innerHTML` is a literal; server values go in by `textContent`
    showMonthDay,    `innerHTML = src.innerHTML` copies parsed markup from hidden holders. These are
    navGo            safe only because the holders are now escaped. On 198ed4d the holders themselves
                     executed payloads on page load, although hidden. Both copies were exercised in
                     Chromium (section 6).
    inline scripts   only ISO dates; `week` and `date` are parsed to `date` first (AE-07)

  Noted, not changed: a stored `link_maps` puts an `<a>` inside the card's `<a>`. Invalid nesting,
  inert, pre-existing.

================================================================================
5. TESTS
================================================================================
  NEW tests/test_agenda_customer_data_escaping.py: 26 passed, 214 subtests, on the final tree.

  14 payloads: HTML tags, img/onerror, script, svg/onload, double- and single-quote attribute
  breakouts, plain quotes, apostrophe, ampersand, text that looks like an entity, card-closing
  markup, comment and textarea closers, javascript: URL. Each field carries its own prefix, so a
  value cannot pass on another field's behalf.

  The proofs parse the page:
    AE-01..09  a hostile page has the same elements and attribute names as a benign one, across
               payable, paid, legacy, no-quote, cancelled and no-address cards, link_maps, day start
    AE-10..13  decoded once, each sink equals the original, in day, pre-rendered day, week, search
               index, month, modal attributes and Maps/Waze URLs
    AE-14      accented Spanish written literally, no entities, nothing double-escaped
    AE-15..18  sink inventory, no double escape, helpers stay plain, modal uses textContent
    AE-19..22  real GET /calendar on real tables; database stays unescaped; rendering mutates
               nothing; lead card escaped once
    AE-23..26  address / professional line in all four combinations (see N-2)

  Negative control: the same suite against 198ed4d's kanban_view.py: 76 failed. (At that point the
  suite had 22 tests; the count includes subtests.)

  Mutation: 24 mutants, 24 killed, unmutated control passing. One per reverted sink (all 12 name,
  vehicle and status sites, including `_day_slots`), plus address, professional, start time, three
  double-escapes, escaping inside a helper, day and modal sinks, search index, modal innerHTML and
  `_url_link` scheme. A 25th, added with AE-23..26 (`_ameta` keeps "-"), was killed by AE-25/AE-26.
  The two `_day_slots` mutants for each field are killed only by AE-15, because that code cannot
  be rendered.

================================================================================
6. REAL-BROWSER PROOF
================================================================================
  Headless Chromium (Playwright build 1223), name resolution disabled, driven by a script injected
  into a copy of each page. It fires mouseover and focus on every element, walks day, week and
  month, triggers both innerHTML copies and opens the modal. Same fixture, both trees. This is
  evidence from a scratch harness and is not part of the repository's suite.

                                    198ed4d      this tree
  payload executions on load        72           0
  distinct payloads fired           27           0
  injected img elements             27           0
  injected script elements          27           0
  injected event handlers           54           0
  modal child elements              0            0   (the modal was already safe)

  On this tree the payload appears as literal text in the name, address and modal. The Spanish page
  reads identically on both trees: "José María Ñandú O'Higgins", "Av. Córdoba 1234, Peña & Cía",
  "Auto / Citroën / C3 / 2020", "Inés Ibáñez (Técnica)", "REVISIÓN".

  Fields fired on 198ed4d: name, surname, brand, model, address, status, professional (name, surname,
  cargo). Tipo did not fire there only because `.title()` mangles the payload; phone and zones were
  already escaped. The attribute-breakout payloads did not fire on either tree in this fixture; they
  are covered by AE-01..13.

================================================================================
7. BASELINES AGAINST 198ed4d
================================================================================
  Harness: ridecheck-crm-test:l4.7b2, `--network none`, DATABASE_URL=sqlite://, tree mounted
  read-only. Both trees were exports without .git. Identity = junit classname::name.

7.1 Full suite, SQLite
  198ed4d     62 failed, 7256 passed, 677 skipped, 9 errors     8003 cases
  this tree   62 failed, 7278 passed, 677 skipped, 9 errors     8025 cases
  failing identities 71 vs 71 · NEW 0 · GONE 0 · COMMON 71 · messages differ 0
  skipped NEW 0 · GONE 0 · cases removed 0 · cases added 22, all in the new suite
  198ed4d reproduces the counts in the vehicle-identity closeout, section 9.1.

7.2 Limit on 7.1
  That run used the suite at 22 tests. AE-23..26 were then added, with one fixture parameter. A final
  full pass started and the host's low-memory protection stopped it at about 2%. It was not restarted:
  the host was at about 110 MB free, with a parallel session's suites running beside the live
  stack. Its orphaned container, agesc-final, was stopped by exact name.
  Production code is byte-identical between the 7.1 run and this tree. The only difference is
  that one new test file, which passes on the final tree (26 passed, 214 subtests). A full pass on
  the final tree is still recommended for the reviewer; expected 7282 passed, same failures.

7.3 Relevant suites in isolation
  cobro action, m12 calendar UI, m12.3 nav, m21.3 demo week, ux2, ux2 runtime, ux3, minimum_launch,
  r1, r2, e2e
  198ed4d     14 failed, 277 passed
  this tree   14 failed, 277 passed
  failing identities 14 vs 14 · NEW 0 · GONE 0 · COMMON 14 · messages differ 0

7.4 PostgreSQL
  Not run. The change adds no SQL and no model or schema change, so the gated suites have nothing
  new to exercise. That is reasoning, not a measurement. In the SQLite run they skip identically.

7.5 Lead card
  Unchanged. No line of `render_lead_card` is in the diff. The rendered card with hostile values is
  byte-identical on both trees:
    with booking details  sha256 3a8a58d8b112097bf22c9d733657c070437981c0c40f4671d5de31b846e42877
    without               sha256 ddea87712b65d0fdd963a3c515c17211bfef47c24d2a9956658df45bfb640a8c
  test_e1..e4, test_x6 and test_b06 pass. None of the 14 failures in 7.3 is an escaping test.

================================================================================
8. MERGE WITH THE VEHICLE-IDENTITY CANDIDATE
================================================================================
  `git merge-file` of kanban_view.py against the candidate's working copy: 5 conflicts, all
  mechanical. 4 are the vehicle line, where both sides made the same change next to lines only this
  side changed; 1 is `_aveh` beside `_aaddr`. Take this side's lines and the candidate's `_aveh`.
  The candidate adds no interpolation that AE-15 would reject. Whichever lands second needs
  this resolution. That tree was being edited by another session during this work; the file
  compared was unchanged at the end of the run.

================================================================================
9. PROPOSED COMMIT (NOT EXECUTED)
================================================================================
  subject:
    fix(agenda): escape customer data at every agenda HTML sink

  body:
    The week, month and slot cards interpolated customer name, address, vehicle, status and
    professional without escaping, so stored markup ran on the operator's agenda. Escape once at
    each sink. Stored values and the plain-text helpers are unchanged.

    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

  git add \
    backend/app/ui/kanban_view.py \
    docs/Launch/2026-11-04_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-AGENDA-CUSTOMER-DATA-ESCAPING_CLOSEOUT_001.md \
    tests/test_agenda_customer_data_escaping.py

  Those 3 paths are every changed path of this milestone.
    backend/app/ui/kanban_view.py                  M  628efe1db2445288e26a65972e5b809941622379ba4cff94158b46f83b682447
    tests/test_agenda_customer_data_escaping.py    A  f38e1b0c238d42b0211c647e76afb6798f1682161e4587651a5c07bacb12f090

================================================================================
10. LIVE ISOLATION
================================================================================
  Same before and after:
    backend, postgres, n8n    same container and image, restart count 0
    /opt/ridecheck-crm compose and .env hashes
    /opt/ridecheck-crm, -integration, -minimum-launch: HEAD, status and diff

  Changed, not by this session:
    /opt/ridecheck-crm-vehicle-identity-recovery tracked diff. Another session was working there.
    Its status list hash and HEAD did not move.
    Containers vid-pg, vid-sqlite-candidate_r1, rcenp-*-pg and rcenp-*-backend appeared. They belong
    to parallel sessions; vid-sqlite-candidate_r1 mounts another session's scratch directory.

  No live database, Meta API or n8n contact. The n8n MCP connector was down and was not needed.
  No image built. Touched outside the worktree: branch and worktree metadata in the shared .git, and
  this session's temp directory. Two commands containing `rm` were refused by the permission layer, not
  worked around, so one stray one-line file remains there: browser_probe_placeholder.txt.

================================================================================
11. FINDINGS AND DECISIONS FOR THE OWNER
================================================================================
  N-1 HIGH, PRE-EXISTING, NOT FIXED, OUTSIDE THIS MILESTONE. /table has the same defect.
      `render_revisions_table_page` emits customer name, phone, email, vehicle and zones raw. Confirmed
      by rendering: stored name, phone and marca came out raw. Its filter chips are also REFLECTED:
      `q` and `marca` came out raw, so a crafted /table link can run script in a logged-in operator's
      session with nothing stored at all. It is in 198ed4d and therefore deployed. Recommend its own
      correction before Smart Booking is enabled for customers, with the same method.
      Same `_txt` pattern, source reading only: /profesionales and /agencias (operator-typed; the
      rendered probe of /profesionales failed on an incomplete fixture, so it is unconfirmed).
      `render_table_page` has it too but has no route.

  N-2 MEDIUM, PRE-EXISTING. 11 agenda tests give no evidence today. test_m12_calendar_ui (8) and
      test_m12_3_calendar_nav (3) die with `TypeError: expected str instance, MagicMock found` at the
      `zone_disp` join, before any assertion, on 198ed4d too. They include the separator tests for
      the line this milestone rerouted. AE-23..26 cover that line directly. Those fixtures were not
      changed. The other 3 of the 14 are a stale hardcoded date and two runtime asset checks.

  N-3 Live exposure not assessed. F-1 is deployed until this ships. Whether any stored live row holds
      markup was not checked: that needs a live read, which this milestone excludes. A read-only scan
      of `leads` and `revisions` for `<` before release would answer it.

  D-1 Order against the vehicle-identity candidate (section 8).

================================================================================
12. REQUIRED BEFORE MERGE
================================================================================
  1. Independent review.
  2. Full SQLite suite on the final tree, once the host has memory to spare (section 7.2).
  3. Owner decision on N-1.

STOP.
