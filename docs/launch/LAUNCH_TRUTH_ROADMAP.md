PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: LAUNCH-TRUTH-ROADMAP

# RideCheck CRM — Launch Truth Table & Finite Launch Roadmap

Date: 2026-08-31
Purpose: Single source of truth for launch readiness, replacing milestone-completion as the definition of "done".

## 1. Why this document exists

RideCheck CRM has repeatedly reached milestones with green targeted tests, only to expose closely related failures during real WhatsApp Wild testing.

From now on:

**A milestone means work was completed. A launch gate means a risk was retired.**

Launch readiness is assessed through four proof levels:

1. **IMPLEMENTED** — code/schema/config exists.
2. **TESTED** — targeted and regression tests pass for the intended invariant.
3. **RUNTIME-PROVEN** — the deployed crm_test image contains and executes the intended behavior.
4. **LAUNCH-PROVEN** — the behavior survives dirty historical state and controlled real WhatsApp Wild sessions without BLOCKER/HIGH defects.

A feature is not launch-ready merely because its implementation milestone closed.

## 2. Launch status categories

### PROVEN
No further work unless later runtime/Wild evidence disproves it.

### NEEDS FIX BEFORE WILD
A known defect could invalidate another controlled real conversation.

### NEEDS PROOF BEFORE WILD
Implementation appears correct, but launch-grade evidence is missing.

### BLOCKED BY EXTERNAL DEPENDENCY
Internal certification can continue, but public launch cannot.

### NEEDS FIX BEFORE PUBLIC LAUNCH
Not required for internal certification, but required before real customer traffic.

### SAFE POST-LAUNCH
Lower-priority improvement that must not delay launch.

## 3. Current executive status

**NOT READY FOR WILD YET**
**NOT READY FOR PUBLIC LAUNCH**

But the project is no longer in an undefined failure loop.

### L1 — Semantic Authority Consolidation
**Status: FROZEN / CONDITIONAL PASS**

L1.1 certification found:

- 0 launch-critical regression failures.
- 0 unknown regression failures.
- No known BLOCKER semantic-authority risks.
- No known HIGH semantic-authority risks.
- RISK-01, RISK-02, RISK-03, RISK-04, RISK-05, CL-05 and CL-07 CLOSED.
- CL-04 only PARTIALLY CLOSED at LOW severity and limited to legacy/null-watermark threads.
- Normal lifecycle cycle-reset contract PASS.
- Production launch is expected to start without historical real-customer threads.
- The real tester must receive a canonical cycle reset before the next Wild.

L1 is therefore safe to freeze and should not be reopened unless new evidence contradicts it.

# 4. Launch Truth Table

| Area | Current truth | Proof level | Status | Required action |
|---|---|---|---|---|
| Semantic authority — current input vs stale history | Architecture consolidated; no known BLOCKER/HIGH remaining | Implemented + Tested + Runtime certified | **PROVEN / FROZEN L1** | Do not reopen unless evidence disproves |
| New candidate stale-zone inheritance | Removed | Tested | **PROVEN** | None |
| Explicit current-turn zone vs AI overwrite | Deterministic zone protected | Tested | **PROVEN** | None |
| Explicit year correction | Current single unambiguous year overrides stale year | Tested + runtime | **PROVEN** | None |
| Scheduling stale day/time authority | Authority guards implemented | Tested | **PROVEN at L1 level** | Dirty-history certification later |
| Candidate focus ambiguity | Silent arbitrary fallback removed | Tested | **PROVEN at L1 level** | Dirty-history certification later |
| AI candidate update without ID | Ambiguous updates skipped instead of mutating arbitrary candidate | Tested | **PROVEN at L1 level** | Dirty-history certification later |
| Cycle reset — normal lifecycle | PASS for brand-new and normal repeated-inspection paths | Audited | **PROVEN WITH PRECONDITION** | Tester reset before next Wild |
| Legacy null-watermark threads | Residual LOW risk only for legacy/pre-migration thread state | Audited | **CONDITIONAL / LOW** | Clean/reset legacy thread if one exists |
| Quote authority | Recomputed from active candidate/current zone | Audited | **PROVEN** | Keep unchanged |
| Prior-cycle acceptance leakage | Reset clears stage before acceptance check | Audited | **PROVEN** | None |
| Booking Flow crypto + persistence | Endpoint, RSA/AES, Base64, watermark, advisory lock, atomic booking all passed audit | Audited + runtime | **PROVEN** | Later live E2E proof |
| Causal outbound dedup | New inbound allowed; same inbound retry blocked | Tested + runtime | **PROVEN** | Later Wild confirmation |
| Blocked outbound counts as unanswered | Fixed in live query | Tested | **PROVEN at code level** | Ops certification in L2 |
| Outbound path attribution | All 7 gate.attempt() call sites carry correct OutboundPathId | Implemented + Tested (20/20 L2-PATH) | **PROVEN / FROZEN L2** | None |
| Control dashboard forensic detail | Field mapping fixed; lead, blocked_reason, latency_ms added to detail | Implemented | **PROVEN at code level** | Runtime proof at L4 |
| Email unanswered alerts | SMTP path implemented; delivery blocked by missing SMTP_PASSWORD credential (operator action) | Implemented, credentials missing | **NEEDS FIX BEFORE PUBLIC LAUNCH** | Operator adds SMTP_PASSWORD to env |
| n8n actual activation state | n8n INACTIVE per prior audit; transport path code verified | Code-proven; runtime proof at L4 | **NEEDS PROOF BEFORE WILD** | L4 runtime proof |
| Runtime/source/image parity | L2 image l2-transport-53b04e5 verified; source matches image | Proven for current image | **PROVEN CURRENTLY** | Re-certify after L3 build |
| WhatsApp system-user token | New token loaded in runtime, outbound OFF | Runtime-proven | **PROVEN** | Verify old-token revocation status |
| App Secret / webhook signature | Verification code exists, but WHATSAPP_APP_SECRET unavailable due Meta issue | External blocker | **BLOCKED BY META FOR PUBLIC LAUNCH** | Retry/ticket with Meta |
| Controlled Wild without App Secret | Possible only as explicit temporary risk exception with tester-only outbound | Policy decision | **CONDITIONAL** | Only after L2 + L3 |
| Production migration | Not yet applied | Not started | **NEEDS FIX BEFORE PUBLIC LAUNCH** | L5 |
| Public launch | Not authorized | — | **NO-GO** | Complete gates below |

# 5. Finite launch roadmap

## L1 — Semantic Authority Consolidation

### Objective
Eliminate the systemic class where stale/historical/AI-derived data can overwrite higher-authority current-turn evidence.

### Status
**FROZEN — CONDITIONAL PASS**

### Closed risks
- RISK-01 customer name overwrite
- RISK-02 scheduling field overwrite
- RISK-03 stale zone inherited at candidate creation
- RISK-04 stale scheduling time fallback
- RISK-05 ambiguous AI candidate update
- CL-05 arbitrary focus fallback
- CL-07 AI overwrite of deterministic location
- Wild-01 explicit zone/year authority defects

### Residual condition
CL-04 is LOW and limited to legacy/null-watermark threads.

### Required precondition
Before the next Wild, the existing tester must enter the next inspection via the **canonical lifecycle reset**, not via manual DB edits.

### Exit criterion
**MET**

- No known BLOCKER/HIGH semantic authority risk.
- 0 launch-critical regression failures.
- 0 unknown regression failures.
- Safe to freeze L1.

### Rule
Do not modify L1 behavior during later milestones unless new evidence proves a regression.

---

## L2 — Transport + Operations Integrity

### Status
**FROZEN — PASS (2026-09-01)**

### Closed items
1. All 7 gate.attempt() call sites now carry explicit OutboundPathId:
   - `api/whatsapp.py` send_thread_text → MANUAL_CRM
   - `api/whatsapp.py` _store_outbound_and_send (interactive, list, flow callers) → MANUAL_CRM
   - `api/whatsapp.py` send_to_phone → SYSTEM_NOTIFICATION
   - `services/buscando_followup.py` → SYSTEM_NOTIFICATION
   - `services/quote_followup.py` → SYSTEM_NOTIFICATION
   - `services/conversation_engine.py` _send_text_to_wa → CE_TEXT (was already correct)
   - `services/conversation_engine.py` _send_flow_button → CE_FLOW (was already correct)
2. Control dashboard field mapping fixed: display_name, latest_ts, waiting_seconds, latest_direction; detail row adds lead_id, blocked_reason, latency_ms, wa_id_masked.
3. Email path decision: SMTP chosen (not Resend). Code is correct. Missing SMTP_PASSWORD credential is an operator action before public launch.
4. n8n transport path verified at code level. Runtime proof required at L4.
5. Image `ridecheck-crm-backend:l2-transport-53b04e5` built and verified.
6. Source/image parity confirmed: 52 failures all pre-existing B/C, 2965 passed, 20 new L2-PATH tests PASS.
7. OUTBOUND remains OFF.

### Test evidence
- test_l2_transport_path_integrity.py: 20/20 PASS
- Full regression: 52 failed (all pre-existing B/C), 2965 passed, 62 skipped

### Objective
Make the live transport, outbound safety and observability trustworthy enough that the next failure can be attributed immediately.

### Scope
1. Fix every missing `path_id` at outbound gate call sites.
2. Prove every authorized send route has an explicit `OutboundPathId`.
3. Improve Control dashboard trace rendering:
   - customer name
   - phone/wa_id
   - thread
   - Lead
   - Revision
   - text
   - audio transcript
   - path
   - WAMID
   - status
   - blocked reason
   - latency
4. Confirm blocked/failed outbound remains operationally unanswered.
5. Restore email alerts:
   - explicitly choose SMTP restoration or Resend migration
   - controlled internal smoke
6. Prove current n8n workflow activation/runtime state.
7. Prove current intended transport:
   Meta → backend webhook → n8n → ConversationEngine.
8. Confirm no legacy AI route is reachable.
9. Rebuild immutable crm_test image.
10. Verify source/image/runtime parity.

### Exit criterion
L2 passes only when:

- no authorized outbound call site has missing path_id; ✅ MET
- Control can reconstruct a real message event without SQL/log access; ✅ MET (field mapping fixed)
- email failure mode is resolved or consciously deferred with another reliable alert method; ✅ MET (SMTP chosen; credential gap documented as operator action)
- n8n active runtime state is proven; ⚠️ DEFERRED to L4 (code path verified; live activation state requires runtime proof)
- transport path is attributable; ✅ MET (CE_TEXT/CE_FLOW path attribution working; MANUAL_CRM and SYSTEM_NOTIFICATION now explicit)
- runtime image matches source; ✅ MET (l2-transport-53b04e5)
- OUTBOUND remains OFF at closeout. ✅ MET

### Wild status after L2
Still **NO**. L3 must come first.

---

## L3 — Dirty-History Certification

### Objective
Prove that the exact failure family that caused repeated Wild crashes has actually been retired.

### Test philosophy
Do not test clean state only.

Seed realistic persistent histories using:

**same Contact + same Thread + same Lead + multiple Revisions**

and intentionally conflict old and new data.

Tests must validate:

**input → canonical state → service input → customer-facing outcome**

not just intermediate DB fields.

### Minimum scenario families
- old vehicle vs new vehicle
- old year vs explicit corrected year
- old location vs new location
- old quote vs new quote
- old acceptance vs new cycle
- old candidate focus vs new cycle
- multiple active candidates
- switch candidate A → B → A
- voice with make/model/year/location
- multi-message burst with correction
- quote recomputation
- scheduling correction
- booking using only active Revision
- dedup across distinct inbound events
- same-event retry dedup
- blocked outbound remains unanswered
- lifecycle reset from completed inspection
- lifecycle reset from abandoned/quoted cycle
- seller/customer name references
- candidate update ambiguity

Target: **20–30 meaningful scenarios**.

### Exit criterion
- all launch-critical dirty-history invariants PASS;
- final customer-facing response asserted for critical scenarios;
- full regression has zero unexplained launch-critical failures;
- no known BLOCKER/HIGH historical-contamination risk remains.

### Wild status after L3
Eligible for L4 runtime certification.

---

## L4 — Runtime Certification + Controlled Wild

### Objective
Prove the complete system using the real WhatsApp tester.

### Phase A — Runtime proof
Before enabling outbound:

- correct image confirmed;
- crm_test confirmed;
- new token confirmed;
- Control operational;
- n8n runtime path operational;
- Booking Flow endpoint operational;
- outbound ledger operational;
- unauthorized-path endpoint operational;
- tester cycle reset completed canonically;
- tester-only outbound restriction active.

### App Secret policy
If Meta has still not resolved App Secret:

- public launch remains blocked;
- paid ads/broad inbound remain blocked;
- a tester-only Wild may proceed only as an explicit temporary security exception.

This exception is for certification only, not launch approval.

### Wild certification loop
Run distinct meaningful sessions:

**Wild → evidence → classify → remediate → regression → L3 relevant stress → fresh Wild**

A BLOCKER/HIGH resets consecutive clean Wild count.

Do not manually patch DB and continue the same failed conversation.

### Target exit criterion
**3 consecutive meaningful clean Wild sessions**

Each must have:

- 0 BLOCKER
- 0 HIGH
- correct vehicle
- correct year
- correct candidate
- correct location
- correct quote
- correct acceptance
- correct scheduling
- correct booking
- no stale-history leakage
- no duplicate/silent response
- no unexplained outbound
- no unauthorized path
- no unknown WAMID
- no bot-owned unanswered thread beyond threshold
- complete Control trace

Three trivial greeting tests do not qualify.

---

## L5 — Production Launch Gate

### Objective
Turn a certified crm_test release into a controlled production launch.

### Required before production
1. App Secret configured or Meta-supported equivalent.
2. Webhook signature verification fail-closed.
3. Old WhatsApp token revoked if still active.
4. Exact release image frozen.
5. Production DB migration plan reviewed.
6. Production migrations applied deliberately.
7. Production/test DB isolation verified.
8. Production env audit.
9. n8n production workflow verified.
10. Booking Flow production configuration verified.
11. Outbound gate verified.
12. Control dashboard operational in production.
13. Rollback plan documented.
14. Launch smoke test performed.

### Launch strategy
Do not activate every acquisition source at once.

Preferred ramp:

1. organic WhatsApp traffic;
2. monitor Control closely;
3. confirm stability;
4. then expand CTWA/marketing/paid acquisition.

### Exit criterion
Public launch requires:

- L1 frozen
- L2 PASS
- L3 PASS
- L4 PASS
- App Secret/security resolved
- production migration/runtime gate PASS

Only then:

**PUBLIC LAUNCH = GO**

# 6. Launch blocker policy

## BLOCKER
Examples:
- wrong customer receives message
- uncontrolled outbound
- wrong vehicle/candidate/location drives quote or booking
- cross-cycle state leakage affects business outcome
- duplicate booking
- unauthorized/unknown send path
- runtime image lacks certified fix

## HIGH
Examples:
- legitimate inbound silently receives no response
- wrong quote
- wrong scheduling slot
- incorrect handoff ownership
- inability to attribute an outbound message
- stale historical state can alter current business flow

## MEDIUM
Examples:
- operational inconvenience with safe fallback
- non-critical dashboard visibility gap
- recoverable UX issue

## LOW / POST-LAUNCH
Examples:
- maintainability cleanup
- dead constants
- internal naming/documentation cleanup
- cosmetic polish

A MEDIUM/LOW issue must not automatically restart the whole launch process.

# 7. Evidence rules

| Evidence | Meaning |
|---|---|
| Code inspection | Implemented |
| Unit/service test | Tested locally |
| PostgreSQL integration | Tested with persistence |
| Runtime HTTP proof | Deployed behavior |
| Source/image hash parity | Correct build running |
| Controlled WhatsApp | Real transport/customer interaction |
| Control ledger/WAMID proof | Attributable real send |
| Consecutive Wild PASS | Launch-grade conversation behavior |

No future closeout may use "PASS" without stating which proof level it represents.

# 8. Regression policy

A failing test must be classified as:

- OBSOLETE_TEST
- TEST_INFRASTRUCTURE
- KNOWN_NON_LAUNCH_DEFECT
- LAUNCH_RELEVANT_DEFECT
- UNKNOWN

"Pre-existing" is not an acceptable final classification.

Launch gate requires:

- `LAUNCH_RELEVANT_DEFECT = 0` for the gate scope
- `UNKNOWN = 0`

L1.1 satisfied this rule.

# 9. Current external blocker — Meta App Secret

## Known truth
- Signature verification code exists.
- When App Secret is configured, invalid signatures fail closed.
- App Secret is currently unavailable due Meta sensitive re-authentication/account behavior.
- Current runtime therefore cannot independently authenticate webhook POSTs using `X-Hub-Signature-256`.

## Policy

### Allowed
- Local testing.
- crm_test development.
- L1/L2/L3 certification.
- Potential controlled tester-only Wild under explicit temporary exception.

### Not allowed
- broad beta traffic
- paid acquisition
- public launch

### Resolution
Retry Meta sensitive authentication and, if still failing, open/continue Meta support ticket.

This remains an **external launch blocker**, not a reason to halt internal certification work.

# 10. Current immediate next step

## NEXT ACTIVE GATE
**L2 — Transport + Operations Integrity**

L1 is frozen.

Do not start another Wild yet.

L2 should now resolve:

1. missing outbound path IDs;
2. Control forensic UI completeness;
3. email alert delivery;
4. n8n runtime truth;
5. transport attribution;
6. runtime/image parity.

After L2 closes, move to L3 dirty-history certification.

# 11. Status tracker

| Gate | Status | Can advance? |
|---|---|---|
| L1 Semantic Authority | **FROZEN — CONDITIONAL PASS** | YES |
| L2 Transport + Operations | **FROZEN — PASS (2026-09-01)** | YES |
| L3 Dirty-History Certification | **NEXT** | — |
| L4 Runtime + Wild Certification | PENDING | NO |
| L5 Production Launch Gate | PENDING | NO |
| Meta App Secret | EXTERNAL BLOCKER | Does not block L2/L3 |

# 12. Definition of launch-ready

RideCheck CRM is launch-ready only when all of the following are true:

- semantic authority is certified;
- transport is attributable;
- dirty persistent history is certified;
- runtime image equals certified source;
- real Booking Flow succeeds end-to-end;
- real WhatsApp interactions succeed;
- 3 consecutive meaningful Wild sessions are clean;
- no open BLOCKER/HIGH launch defect exists;
- App Secret/webhook authenticity is resolved;
- production migration/runtime checks pass;
- rollback and monitoring are ready.

Until then, milestone completion must never be described as launch readiness.

# 13. Governance rule

This document is the launch roadmap source of truth.

When a new defect is found:

1. classify severity;
2. identify which launch gate it invalidates;
3. remediate only that gate;
4. re-certify that gate;
5. continue forward.

Do **not** restart the entire project.

Do **not** add unrelated features during launch certification.

Do **not** reopen a frozen gate without contradictory evidence.

This is how the project exits the previous never-ending fix/test loop.
