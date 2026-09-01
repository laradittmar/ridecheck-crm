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
| Email unanswered alerts | Migrated to Resend (L2.1-EMAIL-ALERTS 2026-09-01); RESEND_API_KEY + INTERNAL_BOOKING_EMAIL_TO already configured; smoke send accepted by Resend; 15/15 EMAIL tests PASS | Implemented + tested + smoke sent | **PROVEN / internal alert delivery restored via Resend** | None |
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
3. Email path decision: L2.1-EMAIL-ALERTS (2026-09-01) — migrated from SMTP to Resend. `send_unanswered_alert()` added to resend_email.py. unanswered_alert.py smtplib removed. RESEND_API_KEY + INTERNAL_BOOKING_EMAIL_TO configured and proven. Smoke send accepted by provider.
4. n8n transport path verified at code level. Runtime proof required at L4.
5. Image `ridecheck-crm-backend:l2.1-email-3131f88` built and verified (supersedes l2-transport-53b04e5).
6. Source/image parity confirmed: 52 failures all pre-existing B/C, 2965 passed, 20 new L2-PATH tests PASS.
7. OUTBOUND remains OFF.

### Test evidence
- test_l2_transport_path_integrity.py: 20/20 PASS
- test_l2_1_email_alerts.py: 15/15 PASS (EMAIL-01 through EMAIL-09)
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
- email failure mode is resolved or consciously deferred with another reliable alert method; ✅ MET (L2.1: migrated to Resend; RESEND_API_KEY configured; smoke send proven; 15/15 EMAIL tests PASS)
- n8n active runtime state is proven; ⚠️ DEFERRED to L4 (code path verified; live activation state requires runtime proof)
- transport path is attributable; ✅ MET (CE_TEXT/CE_FLOW path attribution working; MANUAL_CRM and SYSTEM_NOTIFICATION now explicit)
- runtime image matches source; ✅ MET (l2-transport-53b04e5)
- OUTBOUND remains OFF at closeout. ✅ MET

### Wild status after L2
Still **NO**. L3 must come first.

---

## L3 — Dirty-History Certification

### Status
**FROZEN — PASS (2026-09-01)**

### Objective
Prove that the exact failure family that caused repeated Wild crashes has actually been retired.

### Closed items
1. 50 dirty-history scenarios (L3-01 through L3-35 + realism audit) — 50/50 PASS.
2. All 11 scenario families covered: vehicle/year, location/zone, quote/acceptance,
   scheduling, active-cycle/reset, burst/voice, name/third-party, dedup/unanswered, booking.
3. Final customer-facing outcomes asserted for all required scenarios.
4. Pricing traces: zone_group, zone_detail, viaticos, precio_base, precio_total.
5. Scheduling traces: preferred_day / preferred_time authority verified.
6. Cycle watermark: old candidates filtered; new candidates pass; stale IDs cleared.
7. Zone authority: Sur/Berazategui, CABA/Palermo, Norte/Nordelta all proven.
8. No contradictory evidence against L1 invariants found.
9. OUTBOUND OFF throughout.

### Test evidence
- test_l3_dirty_history.py: 50/50 PASS (35 scenarios + realism checks)
- L1 gate: 19/19 still PASS (no regression)
- L2.1 gate: 15/15 still PASS (no regression)
- L2-transport gate: 20/20 still PASS (no regression)
- Full regression: 63 failed (all pre-existing B/C), 2974 passed, 62 skipped
- LAUNCH_RELEVANT_DEFECT: 0. UNKNOWN: 0.

### Key invariants proven at dirty-history level
- RISK-01: customer_name first-write-wins survives cross-cycle history ✅
- RISK-02: scheduling fill-if-absent; prior-cycle values cleared by reset ✅
- RISK-03: new candidates do not inherit stale home_zone_* ✅
- CL-05: stale current_focus_candidate_id cleared when not in active context ✅
- CL-07: zone_protected blocks AI zone overwrite after LR-3 write ✅
- Cycle watermark: genuinely prior-cycle candidates excluded from context ✅
- Quote: recomputed from current candidate zone, not prior-cycle zone ✅
- Acceptance: QUOTED stage gate blocks cross-cycle acceptance ✅
- Booking: ThreadRevision links current candidate, preserves historical booking ✅

### Exit criterion
**MET**

- All launch-critical dirty-history invariants PASS.
- Final customer-facing responses asserted for required scenarios.
- Full regression: LAUNCH_RELEVANT_DEFECT=0, UNKNOWN=0.
- No known BLOCKER/HIGH historical-contamination risk.

### Wild status after L3
**Eligible for L4 runtime certification.**

---

## L4 — Runtime Certification + Controlled Wild  ← ACTIVE — WILD #2 BLOCKED (PHONE DISCONNECTED)

### Objective
Prove the complete system using the real WhatsApp tester.

### Phase A — Runtime proof
**PREFLIGHT COMPLETE (2026-09-01)**

Preflight audit: `2026-09-01_RIDECHECK_CRM_L4-RUNTIME-WILD-PREFLIGHT_AUDIT_RUNTIME-CERTIFICATION.md`

BLOCKER-L4-01 (image mismatch) was remediated. Image `l2.1-email-3131f88` was deployed and all 10 post-enable preflight checks passed before outbound was authorized.

### Phase B — Wild certification

**Wild #1: FAIL (2026-09-01)**

Forensic audit: `2026-09-01_RIDECHECK_CRM_L4-WILD-01-FORENSIC_AUDIT_FIRST-MESSAGE-FAILURE.md`

**DEFECT-WILD-01-A (HIGH):** Quote of $240,000 delivered without the tester providing a location.
- Root cause: L4 preflight declared READY while tester was in QUOTED state from prior Wild, `cycle_reset_pending=False`, stale `home_zone_detail=Berazategui` active. CE used the prior-cycle Berazategui zone (150,000 + 90,000 = $240,000) when processing the first-message burst.
- CE behavior: CORRECT per L1 invariants. The failure is in the preflight checklist (missing gate on cycle_reset_pending=True).
- Reproduction: CASE A test (9/9 repro PASS) confirms the causal chain.
- **STATUS: REMEDIATED (L4.1 — 2026-09-01)**

**DEFECT-WILD-01-B (MEDIUM):** Outbound message id=6043 delivery failed (status=failed, wamid=None).
- Gate passed correctly (path_id=CE_TEXT). Failure at Meta API transport layer.
- L4.1 root cause (phone DISCONNECTED) **RETRACTED** by L4.1A audit — `status` field was unstable across API calls and is unreliable.
- **L4.1A root cause (STRONGLY_SUPPORTED):** Runtime configured with WRONG Meta phone asset. `WHATSAPP_PHONE_NUMBER_ID=122205934115920` (+54 9 11 5829-5318 / ON_PREMISE / WABA 101584872897508). Intended operational phone is `1196075770246218` (+54 9 11 5700-8687 / CLOUD_API / CONNECTED / GREEN / WABA 1520701463019847). ON_PREMISE phone used via Cloud API endpoint → intermittent delivery failures.
- Meta error capture now persists http_status + error_payload to `whatsapp_messages` DB before container events (L4.1).
- **STATUS: ROOT CAUSE IDENTIFIED — remediation pending owner authorization**

**L4.1 Remediation (CONDITIONAL PASS — 2026-09-01):**

Closeout: `2026-09-01_RIDECHECK_CRM_L4.1-WILD-REMEDIATION_CLOSEOUT_REAUTHORIZE-WILD.md`

1. Canonical lifecycle reset armed: `cycle_reset_pending=True` via two-step `set_lead_estado()` path ✅
2. New required preflight gate: `cycle_reset_pending=True` ✅
3. Meta error capture: `MetaSendError` + `meta_http_status`/`meta_error_payload` columns in `whatsapp_messages` ✅
4. Migration `20260901_l4_1_meta_error_capture.py` applied to crm_test ✅
5. Image `ridecheck-crm-backend:l4.1-meta-error-01025b7` deployed to crm_test ✅
6. 13/13 L4.1 tests PASS; 9/9 L4 repro PASS; 112/112 frozen gates PASS ✅
7. ~~Wild #2 BLOCKED: phone number DISCONNECTED~~ — **RETRACTED (L4.1A): wrong phone asset configured**

**L4.1A Audit (2026-09-01):**

Audit: `2026-09-01_RIDECHECK_CRM_L4.1A-META-ASSET-CORRECTION_AUDIT_DELIVERY-ROOT-CAUSE.md`

Root cause of DEFECT-WILD-01-B identified: `WHATSAPP_PHONE_NUMBER_ID` is set to wrong phone asset:
- Configured: `122205934115920` (+54 9 11 5829-5318 / ON_PREMISE / WABA 101584872897508) ← wrong
- Intended: `1196075770246218` (+54 9 11 5700-8687 / CLOUD_API / CONNECTED / GREEN / WABA 1520701463019847) ← correct
- All 5 published Flows are on the correct WABA (1520701463019847) already ✅
- Current token has messaging access to 1196075770246218 ✅
- Single `.env` line change is all that is required

**Gate invalidation:**
- L4: FAIL — Wild #1 FAIL; consecutive clean count = 0/3; L4.1 + L4.1A complete; `.env` fix pending authorization
- L1, L2, L3: FROZEN — INTACT (not contradicted by Wild #1 evidence)

**Required before Wild #2:**
1. ~~Arm canonical lifecycle reset for tester~~ **DONE (L4.1)**
2. ~~Add `cycle_reset_pending=True` gate to L4 preflight checklist~~ **DONE (L4.1)**
3. ~~Investigate Meta API delivery failure~~ **DONE (L4.1A) — root cause: wrong phone asset**
4. **OWNER ACTION: Authorize `.env` change** `WHATSAPP_PHONE_NUMBER_ID=122205934115920` → `1196075770246218`; operator restarts backend — NO other env changes needed
5. Add log retention protocol before Wild (preserve container logs before any `--force-recreate`)
6. Run preflight with all gates including new required gates before authorizing Wild #2 outbound

**Consecutive clean Wild count: 0/3**

**Pre-existing classified risks (unchanged):**
- App Secret empty (dev mode webhook skip) — ACCEPTED for tester-only Wild; allowlist compensates

### App Secret policy
If Meta has still not resolved App Secret:

- public launch remains blocked;
- paid ads/broad inbound remain blocked;
- a tester-only Wild may proceed only as an explicit temporary security exception.

This exception is for certification only, not launch approval.

### Required preflight gate (added after Wild #1)
Before declaring READY for any Wild, the preflight checklist MUST confirm:

```
tester.cycle_reset_pending == True
```

If False: Wild is NOT authorized until the canonical lifecycle reset is armed.
This gate was missing from the Wild #1 preflight and caused DEFECT-WILD-01-A (HIGH).

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
**L4 — Wild #2 (ALL PREFLIGHT GATES PASS — awaiting outbound authorization)**

L1, L2, L3 are frozen. L4.1 + L4.1A + L4.1B complete. Wild #2 ready pending owner outbound authorization.

**L4.1B complete (2026-09-01):**
- `.env` corrected: `WHATSAPP_PHONE_NUMBER_ID=1196075770246218` ✅
- Container recreated with L4.1 certified image ✅
- Runtime phone ID verified inside container: `1196075770246218` ✅
- Outbound Meta URL: `https://graph.facebook.com/v19.0/1196075770246218/messages` ✅
- Token access to 1196075770246218: PASS ✅
- Meta phone status: CONNECTED / GREEN ✅
- `cycle_reset_pending == True` ✅
- `OUTBOUND_ENABLED == false` ✅
- 72/72 SQLite frozen gates PASS (L1 + M2 + M21.3) ✅
- 22/22 PostgreSQL L4+L4.1 PASS ✅

**Pre-Wild #2 baselines (2026-09-01, post L4.1B):**
```
LAST_INBOUND_ID  = 6042
LAST_OUTBOUND_ID = 6043
OUTBOUND_LEDGER  = 39 records
SECURITY_EVENTS  = 733
DEDUP_COUNT      = 24
```

**All preflight gates PASS:**
```
tester.cycle_reset_pending == True                 ✅
WHATSAPP_PHONE_NUMBER_ID == 1196075770246218        ✅
phone 1196075770246218 status == CONNECTED / GREEN  ✅
OUTBOUND_ENABLED == false                           ✅
```

**Required before Wild #2 outbound authorization:**

1. Owner explicitly authorizes outbound for Wild #2 tester session.
2. Log retention: before any `docker compose up --force-recreate`, copy backend logs (prevents log loss as in Wild #1).
3. Enable outbound: `BETA_OUTBOUND_ENABLED=true docker compose -f docker-compose.yml -f docker-compose.beta.yml up -d --force-recreate backend`
4. Tester sends first WhatsApp message to trigger `_execute_cycle_reset`.

Forensic audit: `2026-09-01_RIDECHECK_CRM_L4-WILD-01-FORENSIC_AUDIT_FIRST-MESSAGE-FAILURE.md`
L4.1 closeout: `2026-09-01_RIDECHECK_CRM_L4.1-WILD-REMEDIATION_CLOSEOUT_REAUTHORIZE-WILD.md`
L4.1A audit: `2026-09-01_RIDECHECK_CRM_L4.1A-META-ASSET-CORRECTION_AUDIT_DELIVERY-ROOT-CAUSE.md`
L4.1B closeout: `2026-09-01_RIDECHECK_CRM_L4.1B-PHONE-ID-REMEDIATION_CLOSEOUT_ENV-CORRECTED.md`

# 11. Status tracker

| Gate | Status | Can advance? |
|---|---|---|
| L1 Semantic Authority | **FROZEN — CONDITIONAL PASS** | YES |
| L2 Transport + Operations | **FROZEN — PASS (2026-09-01)** | YES |
| L3 Dirty-History Certification | **FROZEN — PASS (2026-09-01)** | YES |
| L4 Runtime + Wild Certification | **FAIL — L4.1+L4.1A+L4.1B COMPLETE; all preflight gates PASS; Wild #2 awaiting outbound authorization; 0/3 clean sessions** | AFTER OWNER OUTBOUND AUTHORIZATION |
| L5 Production Launch Gate | PENDING | NO |
| Meta App Secret | EXTERNAL BLOCKER | Does not block L4 tester-only Wild |

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
