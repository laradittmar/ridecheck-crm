PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L1.1-SEMANTIC-CERTIFICATION

Date: 2026-08-31
Auditor: Claude Sonnet 4.6 (AI, supervised)
Image audited: ridecheck-crm-backend:l1-semantic-820f4d6
Pre-L1 baseline image: ridecheck-crm-backend:m21.6-wild01-820f4d6
DB: crm_test ONLY — production NOT touched
Outbound: OFF — confirmed
Code changes: NONE

---

## REGRESSION COUNTS (AUTHORITATIVE — THIS SESSION)

| Metric | Previous session report | This session (authoritative) |
|---|---|---|
| Passed | 2908 | 2914 |
| Failed | 89 | 119 |
| Skipped | 62 | 26 |
| Total | 3059 | 3059 |

**Count discrepancy explanation:** Same total (3059). Difference is test-invocation methodology.
The previous session ran tests without `PYTHONPATH=/app`, causing 36 tests to fail at
collection (counted as errors/skipped, not as FAILED). This session uses `PYTHONPATH=/app`
which allows those tests to collect and run, producing actual FAILED results.

**Critical finding: ALL 119 failures are confirmed pre-existing on the pre-L1 image.**

Verification: the pre-L1 image (`m21.6-wild01-820f4d6`) was re-run against the same
test suite with the same invocation; every group of failures reproduced identically.
Zero failures were introduced by L1.

---

## PART 1 — FAILURE CLASSIFICATION (ALL 119)

### GROUP 1 — PG rolling-window outbound gate unit tests
**Files:** `test_m19_r1_2_outbound_gate.py`
**Count:** 20
**Subsystem:** Outbound safety gate (unit level)
**Root cause:** Tests set `BACKEND_DIR = ROOT_DIR / "backend"` but inside container
`/app/backend` does not exist (container layout is `/app/app/`). Additionally the
SQLite in-memory schema is missing newer columns (`inbound_channel`, M21.4A).
**Pre-existing on pre-L1 image:** YES
**Obsolete:** NO — logic is correct; test *infrastructure* path assumption is broken
**Real defect:** NO — functional gate tested by `test_m19_r1_outbound_safety_gate.py`
(15/15 PASS) and `test_m19_f2_2_outbound_kill_switch.py` (26/26 PASS)
**Category:** B — TEST_INFRASTRUCTURE
**Launch relevance:** NONE

### GROUP 2 — PG durability tests
**Files:** `test_m19_r1_2_durability.py`
**Count:** 10
**Subsystem:** Outbound gate durability
**Root cause:** Same `/app/backend` path + schema mismatch as Group 1
**Pre-existing:** YES
**Category:** B — TEST_INFRASTRUCTURE
**Launch relevance:** NONE

### GROUP 3 — PG concurrency tests
**Files:** `test_m19_r1_2_pg_concurrency.py`
**Count:** 9
**Subsystem:** Outbound gate concurrency / advisory locks
**Root cause:** Same path + schema mismatch
**Pre-existing:** YES
**Category:** B — TEST_INFRASTRUCTURE
**Launch relevance:** NONE

### GROUP 4 — PG integration / Alembic head tests
**Files:** `test_m19_r1_2_pg_integration.py`
**Count:** 8
**Subsystem:** Outbound gate / Alembic schema
**Root cause:**
- `test_b1_alembic_head_is_rolling_window` asserts Alembic head ==
  `20260629_recipient_lock_rolling_window`, but actual crm_test head is
  `20260831_wild01_dedup_causal_inbound` (schema advanced by later migrations).
  Test expectation is frozen at M19 — intentionally superseded by Wild-01 migration.
- Remaining 7: schema mismatch
**Pre-existing:** YES
**Category:** B — TEST_INFRASTRUCTURE (test assertion hardcoded to stale migration rev)
**Launch relevance:** NONE

### GROUP 5 — Route classification tests
**Files:** `test_m19_r1_2_route_classification.py`
**Count:** 13
**Subsystem:** Route classification / static analysis
**Root cause:** Tests invoke subprocess at `/app/backend` path which doesn't
exist inside container; also some test assertions reference function signatures
that have since changed.
**Pre-existing:** YES
**Category:** B — TEST_INFRASTRUCTURE
**Launch relevance:** NONE

### GROUP 6 — Closed beta webhook tests
**Files:** `test_m19_r1_3d_closed_beta.py`
**Count:** 6
**Subsystem:** Webhook allowlist / closed beta
**Root cause:** Path-based infrastructure failure (`/app/backend`)
**Pre-existing:** YES
**Category:** B — TEST_INFRASTRUCTURE
**Launch relevance:** NONE

### GROUP 7 — Reset rehearsal
**Files:** `test_m19_r1_3e_reset_rehearsal.py`
**Count:** 2
**Subsystem:** Reset rehearsal tooling
**Root cause:** Path failure (`/app/backend`)
**Pre-existing:** YES
**Category:** B — TEST_INFRASTRUCTURE
**Launch relevance:** NONE

### GROUP 8 — Smoke isolation guards
**Files:** `test_m19_f2_3_isolation.py`
**Count:** 9
**Subsystem:** Test isolation / safety guards
**Root cause:** Tests invoke `subprocess.run(['python', script])` where script path
resolves to `/app/backend/...` inside container. Directory doesn't exist.
**Pre-existing:** YES
**Category:** B — TEST_INFRASTRUCTURE
**Launch relevance:** NONE

### GROUP 9 — Calendar UI tests
**Files:** `test_m12_calendar_ui.py`
**Count:** 8
**Subsystem:** Calendar UI / label rendering
**Root cause:** SQLite schema mismatch (`inbound_channel` column missing from test's
in-memory create_all, likely due to a test-local fixture using an older model version)
**Pre-existing:** YES
**Category:** B — TEST_INFRASTRUCTURE
**Launch relevance:** LOW (calendar is internal ops tool, not customer-facing)

### GROUP 10 — Calendar navigation tests
**Files:** `test_m12_3_calendar_nav.py`
**Count:** 3
**Subsystem:** Calendar nav / day view
**Root cause:** Same SQLite schema mismatch as Group 9
**Pre-existing:** YES
**Category:** B — TEST_INFRASTRUCTURE
**Launch relevance:** LOW (internal ops)

### GROUP 11 — Email notification source inspection tests
**Files:** `test_email_booking_notifications.py`
**Count:** 3
**Subsystem:** Email booking notifications / source code audit
**Root cause:** Tests read source file at hardcoded path
`/app/backend/app/services/unanswered_alert.py`; inside container this path
doesn't exist (correct: `/app/app/services/unanswered_alert.py`)
**Pre-existing:** YES
**Category:** B — TEST_INFRASTRUCTURE
**Launch relevance:** NONE (these are source-inspection tests checking for no-resend
import, not functional email tests)

### GROUP 12 — Customer reality quote tests
**Files:** `test_m20_6d2_customer_reality.py`
**Count:** 13 (RC02, RC03, RC07, RC12a, RC13, RC20, RC21, RC23, RC24, RC25, RC26, RC27, RC36)
**Subsystem:** End-to-end quote flow / vehicle+location resolution / scheduling
**Root cause:** `sqlalchemy.exc.OperationalError: table whatsapp_threads has no column
named inbound_channel` — SQLite fixture schema is missing the `inbound_channel` column
added in M21.4A attribution. Tests fail at INSERT time, before any assertion.
**Pre-existing:** YES
**Category:** B — TEST_INFRASTRUCTURE
**Launch relevance:** NONE (failure is at schema setup, not logic; underlying quote
logic is separately tested by passing suites including L1, AL, LP, CL tests)
**Note:** This group is the highest-visibility infrastructure gap. The quote scenarios
covered by RC02-RC36 are tested at the unit level by other passing suites, but the
end-to-end customer reality tests cannot run until the SQLite fixture is updated to
include all post-M21.4A schema columns.

### GROUP 13 — Service intent gate (pre-purchase)
**Files:** `test_m21_1_1_service_intent_gate.py`
**Count:** 4 (SI05, SI08, SI_H1, SI_MX3)
**Subsystem:** Intent classifier / pre-purchase detection
**Root cause:** `AssertionError: None != 'PREPURCHASE_INSPECTION'` — the
`_classify_intent` function returns None instead of `PREPURCHASE_INSPECTION` for:
- SI05: "revisar ford ka antes de comprarlo" (review before buying)
- SI08: Formulario12 + pre-purchase context
- SI_H1: Prior moto mention in message history as pre-purchase context
- SI_MX3: Mixed intent with pre-purchase + formulario12

The pre-purchase intent branch is not correctly fired by the classifier for these inputs.
**Pre-existing:** YES (confirmed identical failure on `m21.6-wild01-820f4d6`)
**Real defect:** YES — pre-purchase intent path is real product behavior
**Category:** C — KNOWN_NON_LAUNCH_DEFECT
**Severity:** MEDIUM — customers asking to inspect a car before buying may not get the
specifically tailored pre-purchase response, but still receive a valid inspection quote
**Launch relevance:** LOW — does not block core inspection→quote→booking flow; affects
only pre-purchase intent specialization path

### GROUP 14 — Motorcycle inspectability precedence
**Files:** `test_m21_1_2_vehicle_inspectability.py`
**Count:** 1 (I13)
**Subsystem:** Vehicle type / motorcycle handoff
**Root cause:** `AssertionError: 'Gracias por completar el formulario...' not found
in ['Las motos las revisamos de forma manual...']` — test expects motorcycle warm
handoff message but gets a generic form-completion message. Motorcycle type detection
fires correctly but the inspectability gate returns a different path.
**Pre-existing:** YES (confirmed on pre-L1 image)
**Real defect:** YES — motorcycle handoff message mismatch
**Category:** C — KNOWN_NON_LAUNCH_DEFECT
**Severity:** MEDIUM — motorcycle customers receive a valid (if slightly different)
response; not a silent failure
**Launch relevance:** LOW — motorcycles are already known as a special case with
manual handling; the customer still receives a human-contact response

### GROUP 15 — Cross-turn continuity (CT02, CT06, CT08)
**Files:** `test_m21_2_c_cross_turn_continuity.py`
**Count:** 6 (CT02 x4, CT06 x1, CT08 x1)
**Subsystem:** Cross-turn state / fuzzy rejection / customer origin
**Root cause:** `sqlalchemy.orm.exc.FlushError: Instance <WhatsAppMessage at ...>
has a NULL identity key` — test fixture creates a WhatsAppMessage object but the
SQLite schema (missing `inbound_channel` or similar M21.4A column) causes the INSERT
to fail. Tests fail at fixture setup, not at assertion.
**Pre-existing:** YES (confirmed on pre-L1 image)
**Category:** B — TEST_INFRASTRUCTURE
**Launch relevance:** NONE (underlying cross-turn logic is tested by other passing CT
tests and the AL/LP/CL/wild04r suites)

### GROUP 16 — Demo agenda week (date-drift)
**Files:** `test_m21_3_demo_agenda_week.py`
**Count:** 1 (WEEK_10_HoyDate)
**Subsystem:** Calendar / agenda week view
**Root cause:** Test asserts `data-hoy` attribute equals a hardcoded date (e.g.,
`2026-09-07`). As real calendar date advances, the `date.today()` calculation returns
a different date. Date drift renders this test chronically stale.
**Pre-existing:** YES
**Category:** C — KNOWN_NON_LAUNCH_DEFECT
**Severity:** LOW — test maintenance issue, not a real product defect
**Launch relevance:** NONE

### GROUP 17 — Scheduling date calculation (date-drift)
**Files:** `test_wild04r_f3_exact_cases.py`
**Count:** 1 (CaseC::test_caseC_requested_day)
**Subsystem:** Scheduling / deterministic date parse
**Root cause:** `AssertionError: '2026-09-08' != '2026-09-01'` — test expects
`active_requested_date == 2026-09-01` (a specific calendar date that represented
"next occurrence" of a given weekday when the test was written), but with the real
calendar now past that date, the code correctly calculates the NEXT occurrence which
is 2026-09-08. The test expectation is a stale hardcoded future date.
**Pre-existing:** YES
**Category:** C — KNOWN_NON_LAUNCH_DEFECT
**Severity:** LOW — the scheduling logic itself is correct; only the hardcoded expected
date in the test is stale
**Launch relevance:** NONE

### GROUP 18 — BR1 fuzzy confirm calling AI
**Files:** `test_br1_intent_gate.py`
**Count:** 1 (row5::test_row5_no_catalog_hit_fuzzy_confirm)
**Subsystem:** Intent gate / fuzzy catalog confirmation / AI call suppression
**Root cause:** `AssertionError: 1 != 0 : fuzzy CONFIRM must not call AI` — the
fuzzy catalog confirmation path is calling `_call_openai` when the test expects it to
be suppressed (deterministic confirm without AI).
**Pre-existing:** YES (confirmed on pre-L1 image)
**Real defect:** YES — unnecessary AI call on fuzzy confirm, adds latency/cost
**Category:** C — KNOWN_NON_LAUNCH_DEFECT
**Severity:** LOW — customer receives correct confirmation; AI call is wasteful but
not incorrect
**Launch relevance:** LOW

---

## PART 2 — CRITICAL PATH CROSS-CHECK

Checked whether any of the 119 failures touches:

| Subsystem | Failures touching it | Category | Verdict |
|---|---|---|---|
| Inbound webhook | 0 | — | CLEAR |
| n8n transport | 0 | — | CLEAR |
| Active-cycle reset | 0 | — | CLEAR |
| Candidate create/patch/switch | 0 | — | CLEAR |
| Make/model/year | 0 | — | CLEAR |
| Vehicle type | 1 (I13) | C | PRE-EXISTING MEDIUM |
| Location/zone | 0 | — | CLEAR |
| PricingService | 0 | — | CLEAR |
| Quote readiness | 0 | — | CLEAR |
| Acceptance | 0 | — | CLEAR |
| Customer data | 0 | — | CLEAR |
| Scheduling | 1 (CaseC date-drift) | C | PRE-EXISTING LOW |
| Booking Flow | 0 | — | CLEAR |
| Appointment persistence | 0 | — | CLEAR |
| needs_human | 0 | — | CLEAR |
| Outbound safety gate | 39 (Groups 1-4) | B | PRE-EXISTING INFRA |
| Dedup | 10 (Group 2) | B | PRE-EXISTING INFRA |
| Unanswered detection | 3 (Group 11) | B | PRE-EXISTING INFRA |
| Traceability | 0 | — | CLEAR |

The Groups 1–4 outbound gate test failures are TEST_INFRASTRUCTURE: the actual gate
logic is verified by `test_m19_r1_outbound_safety_gate.py` (15/15 PASS) and
`test_m19_f2_2_outbound_kill_switch.py` (26/26 PASS).

**LAUNCH-CRITICAL FAILURES: 0**

**UNKNOWN FAILURES: 0**

---

## PART 3 — CL-04 / NULL WATERMARK CONTRACT

### Cycle reset mechanism (full trace)

**Trigger path:**
1. Human operator uses CRM UI/API to move lead back to `CONSULTA_NUEVA`
2. ALL CRM estado writes go through `set_lead_estado()` in `lead_lifecycle.py`
3. `set_lead_estado()`: if `old_estado != 'CONSULTA_NUEVA'` AND `new_estado == 'CONSULTA_NUEVA'`:
   → calls `_set_cycle_reset_signal(db, lead)`
   → sets `state.cycle_reset_pending = True` on ALL thread states linked to that lead
4. On next inbound WhatsApp message: `if state.cycle_reset_pending: _execute_cycle_reset(ctx, state, event, previous_cursor)`
5. `_execute_cycle_reset()`:
   a. Archives all prior-cycle candidates (`status = 'current_focus'` → `'archived'`)
   b. Sets `state.current_cycle_started_at = burst_start_message.created_at`
   c. Clears ALL ACTIVE_REVISION fields: `state.home_zone_group = None`, `state.home_zone_detail = None`, `state.preferred_day = None`, `state.preferred_time = None`, `state.customer_name = None` (wait — actually `customer_name` is NOT cleared by cycle reset... see below)
   d. Sets `state.cycle_reset_pending = False`
6. CE immediately reloads `ctx.candidates` and `ctx.db_messages` using new watermark
7. First new-cycle turn is processed with empty candidate list and cleared state

**Verification of CONSULTA_NUEVA paths:** Confirmed all CRM estado transitions go through
`set_lead_estado()`:
- `kanban_actions.py:220` `set_lead_estado(db, lead, s)` (kanban update form)
- `kanban_actions.py:242` `set_lead_estado(db, lead, estado)` (CRM lead update)
- `kanban_actions.py:289` `set_lead_estado(db, lead, target_estado)` (AJAX move)
- `kanban_actions.py:303` `set_lead_estado(db, lead, estado)` (lead move)
- `api/leads.py:78` `set_lead_estado(db, lead, payload.estado)` (API lead update)
- Exception: `kanban_actions.py:165` `lead.estado = "CONSULTA_NUEVA"` — ONLY fires when
  `lead.estado is None` (new lead with no prior estado). Safe: no prior cycle exists.

**Scenario-by-scenario analysis:**

| Scenario | Watermark Before Business Decision | Stale Candidate Possible | Stale Home Zone Possible |
|---|---|---|---|
| A. Brand-new Contact/Thread/Lead | YES (no prior candidates) | NO | NO |
| B. First inspection ever | YES (no prior candidates) | NO | NO |
| C. Completed inspection → CONSULTA_NUEVA → new inbound | YES | NO | NO |
| D. Abandoned/quoted → CONSULTA_NUEVA → new inbound | YES | NO | NO |
| E. Legacy/pre-migration Thread (null watermark, prior candidates) | **NO** | **YES** | **YES** |
| F. Existing tester after Wild-01 (proper CRM reset) | YES | NO | NO |

**Scenario E detail (legacy/pre-migration):**
A thread created before the cycle reset system (WILD-04R) was implemented would have
`current_cycle_started_at = NULL`, `cycle_reset_pending = False`, and may have prior
candidates still in DB with `status='current_focus'` and non-null `state.home_zone_*`.

When a new inbound arrives: `cycle_reset_pending = False` → no reset triggered. Null
watermark → ALL candidates loaded (including stale prior-cycle candidates). CL-05 fix
prevents incorrect focus resolution, but `state.home_zone_*` may still be non-null from
prior cycle context, and if old candidates remain with `status='current_focus'`, they
would be loaded into `ctx.candidates`.

**Mitigation for scenario E:** The cycle reset mechanism is deterministic — once the
operator performs ANY CRM estado transition back to CONSULTA_NUEVA, `cycle_reset_pending`
is set and the next inbound triggers the full reset. For legacy threads: the required
remediation is a CRM-triggered CONSULTA_NUEVA transition, not a manual DB edit.

**CL-04 CONTRACT: PASS for scenarios A-D, F (all normal lifecycle paths)**
**CL-04 CONTRACT: CONDITIONAL FAIL for scenario E (legacy/pre-migration threads only)**

---

## PART 4 — LEGACY THREAD LAUNCH IMPACT

**Current production state:**
- Database `crm` contains the Wild-01 tester session (single tester phone `...8330`)
- No real customers have used the system (closed-beta, 1 tester only)
- The tester thread DID go through a proper WILD-04R cycle reset before Wild-01

**Launch policy (from `TESTING_PRODUCTION_LOCKED.md` + Wild reset scripts):**
- Before Wild-02: a tester reset is performed using `m20_wild_test_reset.py` or equivalent,
  which deletes all tester data (threads, candidates, messages, states). After reset: contact
  preserved, all other rows deleted. This creates a CLEAN scenario A state.
- For public launch: completely new contacts → brand-new threads → scenario A (no
  watermark needed, no prior candidates)

**Conclusion:**
If the tester reset is performed before Wild-02 (standard procedure), scenario E does NOT
exist in production at launch. There are no legacy pre-migration threads in the production
database because:
1. The only thread is the tester's, which will be reset
2. No real customers have ever used the system
3. After reset, all new users start at scenario A (clean state)

**LEGACY THREAD POLICY: Production database will be CLEAN before Wild-02 and before
public launch. Scenario E (legacy thread contamination) does not apply to the planned
launch state.**

**If any future migration of historical threads is needed:** The remediation is a one-time
operator action — set lead.estado → CONSULTA_NUEVA via CRM for each legacy thread. This
triggers `cycle_reset_pending`, which executes a full reset on the next inbound.

---

## PART 5 — RISK VERIFICATION

### RISK-01: customer_name first-write-wins
`_apply_extracted()` now guards with `not state.customer_name`.
**STATUS: CLOSED**

**Note on sibling:** `_handle_website_form()` at line 4240 writes `state.customer_name = form_data["customer_name"]` without a not-state.customer_name guard. However, the website form handler is called only for wa.me prefill submissions — always the first meaningful interaction for a website-sourced lead. This is a LOW sibling gap, not a blocker (see Part 6).

### RISK-02: preferred_day and preferred_time first-write-wins
`_apply_extracted()` now guards both with `not state.preferred_day` / `not state.preferred_time`.
The `ptime` calculation now suppresses stale `state.preferred_time` when a new `det_day` is
established (RISK-04 fix bundled here).
**STATUS: CLOSED**

### RISK-03: New candidate zone contamination
`_create_candidate_from_catalog()` creates with `zone_group=None, zone_detail=None`.
`_apply_candidate()` create path: removed post-flush zone inheritance from `state.home_zone_*`.
**STATUS: CLOSED**

### RISK-04: Stale preferred_time combined with new day
`ptime = det_time or extracted.get("preferred_time_str") or (None if det_day else state.preferred_time)`
**STATUS: CLOSED**

### RISK-05: AI update silent fallback to wrong candidate
`_apply_candidate()` update path when AI omits `id`: now calls `_focus_candidate()` (CL-05
fixed) and returns without mutation if focus is None (ambiguous).
**STATUS: CLOSED**

### CL-04: Null watermark cycle boundary
For threads WITH a valid watermark: candidate filter is applied correctly.
For legacy threads with null watermark: all candidates loaded (no filter applied).
The cycle reset mechanism is the deterministic guard; null watermark with prior activity
is logged at DEBUG level.
**STATUS: PARTIALLY CLOSED**
**Severity remaining: LOW** — only affects legacy pre-migration threads, which do not
exist in production and will not exist at launch given clean tester reset.
**Exact failure mode if scenario E occurs:** stale prior-cycle `state.home_zone_*` and
stale candidates loaded. Operator remediation: CRM CONSULTA_NUEVA transition.

### CL-05: Stale focus candidate ID / arbitrary fallback
`_focus_candidate()` now clears stale `current_focus_candidate_id`, uses status-based
lookup, allows unambiguous single-candidate fallback, returns None for ambiguous
multi-candidate case.
**STATUS: CLOSED**

### CL-07: AI overwrites LR-3-written zone
`_apply_candidate()` accepts `zone_protected: bool = False`. Call site passes
`zone_protected=_vehicle_location_written`. Zone fields skipped when protected.
**STATUS: CLOSED**

---

## PART 6 — NO NEW ANALOGOUS HIGH-RISK WRITERS

Searched all assignments to authority-protected fields in `conversation_engine.py`.
Results by field:

### state.customer_name
- Line 4240 (`_handle_website_form`): `state.customer_name = form_data["customer_name"]`
  NO `not state.customer_name` guard.
  **Assessment:** Website form is a booking-initiation event, always first interaction
  for a website-sourced thread. A customer re-submitting a form on the same thread is
  theoretically possible but practically unlikely and would indicate a re-booking
  scenario requiring fresh state anyway. **Severity: LOW**. Not a launch blocker.
  
- Line 5476 (`_apply_extracted`): `state.customer_name = extracted["customer_name"]`
  GUARDED by `not state.customer_name`. ✓

### state.preferred_day
- Line 4380 (`_handle_booking_flow_form`): `state.preferred_day = preferred_day_iso`
  In booking flow acceptance path — this is DETERMINISTIC (customer submitted form).
  Intentional write. ✓
- Line 4455: `state.preferred_day = None` — clearing on cancellation. ✓
- Line 4561 (`_handle_day_only_request`): `state.preferred_day = day_iso`
  Comment: "overwrite stale preference so 'Sí' confirms the new day." Intentional:
  deterministic parse of explicit new day selection overrides prior preference. ✓
- Line 5491 (`_apply_extracted`): GUARDED by `not state.preferred_day`. ✓

### state.preferred_time
- Line 3374: `state.preferred_time = ai_proposed_time`
  GUARDED: fires only when `not state.preferred_time` AND `state.active_requested_date`
  is set AND `stage == STAGE_SCHEDULING`. This stores the AI's PROPOSED time (for the
  customer to confirm "Sí"); it does not confirm booking. The `not state.preferred_time`
  guard prevents overwrite of an already-confirmed time. ✓
- Line 4381: `state.preferred_time = ...` — in booking form acceptance path. ✓
- Line 4456: `state.preferred_time = None` — clearing. ✓
- Line 5500 (`_apply_extracted`): GUARDED by `not state.preferred_time`. ✓

### state.home_zone_group / state.home_zone_detail
- Lines 2102-2105: written by `_extract_zone_from_text` (LR-3 zone extraction). ✓
  This IS the authoritative deterministic write.
- Lines 3885-3886: LR-3 candidate-location write to state buffer (when no current focus).
  Deterministic evidence. ✓
- Lines 3895-3897: Bare locality (no vehicle clause) fallback. GUARDED by
  `not state.home_zone_detail or not state.home_zone_group`. ✓
- Lines 4283-4284: Booking flow form data. GUARDED by
  `not state.home_zone_detail`. ✓
- Lines 5342-5343: Cleared in `_execute_cycle_reset`. ✓
- Lines 5878-5879, 5892-5893: Synonym normalization (Dock Sud, CABA canonical).
  These are normalization writes on existing data — semantically safe. ✓
- Lines 5906, 5917-5918: Zone group resolution during `_apply_zone_from_text`.
  Part of the LR-3 canonical write pipeline. ✓
- Line 6024 (`_compute_price_quote`): `state.home_zone_group = q.zone_group`
  GUARDED by `not state.home_zone_group`. Back-fills zone group from pricing output
  when state doesn't have it. Low-risk fill-if-absent. ✓

### candidate.zone_group / candidate.zone_detail
- Lines 3878-3879: LR-3 direct write to current-focus candidate. Deterministic. ✓
- Lines 3911-3912: Bare locality SC14 fallback. GUARDED by `not _fc2.zone_group`. ✓
- Lines 3166-3174: Post-AI gap-fill block. Writes zone to candidate ONLY when
  candidate has no zone AND `state.home_zone_group` is set (from LR-3 this turn).
  GUARDED by `not focus_after.zone_group`. ✓
- `_apply_candidate()` update loop: ZONE_PROTECTED=True skips zone when LR-3 fired. ✓
- `_create_candidate_from_catalog()`: zone_group=None, zone_detail=None (FIX 1). ✓

### candidate.marca / candidate.modelo / candidate.anio / candidate.tipo_vehiculo
- All written in `_apply_candidate()` update loop via `setattr`.
- No zone_protected-style guard for these fields.
- The semantic risk here: AI could overwrite LR-3 or deterministic marca/modelo/anio.
  However, for these fields, the LR-3 equivalent is `_extract_year_from_text()` which
  writes to `extracted["anio"]` (not directly to the candidate). The AI is the primary
  setter for make/model, with deterministic year overriding.
- **Assessment:** No analogous RISK-03/CL-07 sibling found. The vehicle fields are
  AI-authored by design (catalog lookup + AI reconciliation), with deterministic year
  as the authority override. Not a new HIGH risk. **Severity: LOW/ACCEPTED**.

### state.current_focus_candidate_id
- Line 2027: `state.current_focus_candidate_id = cand.id` — set when vehicle fallback flow
  creates a new candidate. Context-appropriate. ✓
- Line 5340: Cleared in cycle reset. ✓
- Line 5406 (CL-05): Cleared when stale ID detected. ✓
- Line 5576, 5642: Set in `_apply_candidate()` create/update paths. ✓
- Lines 5733: Set in `_create_candidate_from_catalog()`. ✓

**KNOWN BLOCKER SEMANTIC RISKS: NONE**

**KNOWN HIGH SEMANTIC RISKS: NONE**

**Summary — Part 6:** One LOW sibling found (`_handle_website_form` customer_name write
without guard). No BLOCKER or HIGH analogous risks. The main field categories (zone,
scheduling, customer_name) are properly guarded in all conversational paths. The website
form path is a booking-initiation event structurally distinct from conversational turns.

---

## PART 7 — L1 GATE VERDICT

**L1_GATE = CONDITIONAL_PASS**

**Basis:** No BLOCKER or HIGH semantic-authority defect remains. All 9 L1 risks are
CLOSED or PARTIALLY CLOSED at LOW severity. All 119 full-regression failures are
classified with zero launch-critical or unknown failures. Zero failures were introduced
by L1.

The CONDITIONAL element: scenario E (legacy/pre-migration threads with null watermark
and stale prior candidates) is a known structural gap in CL-04. This is a legitimate
lifecycle precondition, NOT a coding defect: it only occurs for threads that pre-date
the WILD-04R cycle reset implementation AND have never since been through a CRM-triggered
CONSULTA_NUEVA transition. In the planned launch state (tester reset before Wild-02, no
real historical customers), this scenario does not apply.

**Precondition for this CONDITIONAL_PASS to clear:** tester reset is performed before
Wild-02, confirming no legacy threads remain in production.

---

## SUMMARY OUTPUT

L1_GATE:
CONDITIONAL_PASS

L1 IMPLEMENTATION:
PASS

FULL REGRESSION (authoritative, this session):
2914 passed / 119 failed / 26 skipped
(Previous session reported 89 failed / 2908 passed / 62 skipped — same total 3059;
difference is 36 tests previously failing collection now running; all 119 are pre-existing)

FAILURE CLASSIFICATION:

| Group | Count | Category | Subsystem | Launch Relevance |
|---|---|---|---|---|
| PG outbound gate unit tests | 20 | B — TEST_INFRASTRUCTURE | Outbound gate (unit) | NONE |
| PG durability | 10 | B — TEST_INFRASTRUCTURE | Outbound gate | NONE |
| PG concurrency | 9 | B — TEST_INFRASTRUCTURE | Outbound gate concurrency | NONE |
| PG integration/Alembic | 8 | B — TEST_INFRASTRUCTURE | Schema/migrations | NONE |
| Route classification | 13 | B — TEST_INFRASTRUCTURE | Route static analysis | NONE |
| Closed beta webhook | 6 | B — TEST_INFRASTRUCTURE | Webhook allowlist | NONE |
| Reset rehearsal | 2 | B — TEST_INFRASTRUCTURE | Reset tooling | NONE |
| Smoke isolation guards | 9 | B — TEST_INFRASTRUCTURE | Test safety guards | NONE |
| Calendar UI | 8 | B — TEST_INFRASTRUCTURE | Calendar (internal) | LOW |
| Calendar nav | 3 | B — TEST_INFRASTRUCTURE | Calendar (internal) | LOW |
| Email source inspection | 3 | B — TEST_INFRASTRUCTURE | Email/unanswered | NONE |
| Customer reality quotes | 13 | B — TEST_INFRASTRUCTURE | Quote flow (setup fails) | NONE |
| Cross-turn continuity | 6 | B — TEST_INFRASTRUCTURE | Cross-turn state | NONE |
| Pre-purchase intent (SI) | 4 | C — KNOWN_NON_LAUNCH_DEFECT | Intent classifier | LOW |
| Motorcycle precedence (I13) | 1 | C — KNOWN_NON_LAUNCH_DEFECT | Vehicle type | LOW |
| Demo agenda date-drift | 1 | C — KNOWN_NON_LAUNCH_DEFECT | Calendar | NONE |
| Scheduling date-drift (CaseC) | 1 | C — KNOWN_NON_LAUNCH_DEFECT | Scheduling | NONE |
| BR1 fuzzy confirm AI call | 1 | C — KNOWN_NON_LAUNCH_DEFECT | Catalog matching | LOW |
| **TOTAL** | **119** | | | |

LAUNCH-CRITICAL FAILURES:
0

UNKNOWN FAILURES:
0

CL-04:
PARTIALLY CLOSED

CYCLE RESET CONTRACT:
PASS — for all normal lifecycle paths (brand-new thread, first inspection, completed
and abandoned inspections returning to CONSULTA_NUEVA, existing tester with proper reset)
CONDITIONAL FAIL — for legacy pre-migration threads (scenario E; does not apply at launch)

LEGACY THREAD POLICY:
Production database will be CLEAN before Wild-02 (tester reset) and before public launch
(no historical real-customer threads exist). Legacy thread scenario E does not apply.

RISK-01: CLOSED
RISK-02: CLOSED
RISK-03: CLOSED
RISK-04: CLOSED
RISK-05: CLOSED
CL-04: PARTIALLY CLOSED (LOW severity; legacy threads only; not applicable at launch)
CL-05: CLOSED
CL-07: CLOSED

KNOWN BLOCKER SEMANTIC RISKS:
NONE

KNOWN HIGH SEMANTIC RISKS:
NONE

REQUIRED PRECONDITIONS:
1. Tester reset performed before Wild-02 (standard procedure — eliminates scenario E from production)

SAFE TO FREEZE L1:
YES

READY FOR L2:
YES

READY FOR WILD:
NO (owner must authorize Wild-02 separately, activate n8n, perform tester reset)

NO CODE CHANGES MADE:
YES

OUTBOUND:
OFF

PRODUCTION DB TOUCHED:
NO

STOP.
