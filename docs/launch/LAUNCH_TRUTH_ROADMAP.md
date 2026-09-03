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
| Booking Flow dispatch (authoritative UX) | OWNER DECISION 2026-09-01: BOOKING_FLOW authoritative. Sender wired: booking_flow_id + make_booking_token + path_id=BOOKING_FLOW; eligibility = established valid slot | Implemented + Tested (42/42 L4.3) | **NEEDS RUNTIME PROOF (WILD)** | Controlled Wild B |
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
| Vehicle/location evidence capture | Capture is evidence-driven; intent wording no longer authority; origin clause isolates itself; response cannot claim an unheld vehicle | Implemented + Tested (28/28 L4.6) | **NEEDS RUNTIME PROOF (WILD)** | Controlled Wild C |
| Scheduling temporal semantics (primary vs fallback day) | Primary "mñ/mañana" preference discarded when a weekday name is present; no primary+fallback representation | Wild-proven defect (Wild A) | **NEEDS FIX BEFORE WILD** | L4.3 Phase A |
| Business-hours authority | FAQ constant contradicts ScheduleService per-weekday hours | Wild-proven defect (Wild A) | **NEEDS FIX BEFORE WILD** | L4.3 Phase B |
| Booking Flow dispatch (28104222025943520) | Flow published but unreachable — no sender, no BOOKING_FLOW path_id, no token minting | Code-proven gap | **NEEDS FIX BEFORE PUBLIC LAUNCH** | L4.3 Phase C (owner decision first) |
| Outbound ledger deployment_id / correlation_id | Persisted as 'unknown' / NULL — GIT_SHA not injected by any compose | Runtime-proven | **NEEDS FIX BEFORE PUBLIC LAUNCH** | L4.3 Phase D |
| Host memory headroom / transport resilience | Global OOM killed n8n (sole inbound transport) on 4 GB zero-swap host; 4 GB swap now active and persistent | Runtime-proven incident (INFRA-OOM-01) | **NEEDS FIX BEFORE PUBLIC LAUNCH** | Container mem_limits, n8n restart policy, liveness alert, preflight memory gate |

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

### Phase B — Wild A (first TRUE clean-slate Wild)

**Wild A: PAUSED — SCHEDULING FAIL (2026-09-01)**

Forensic audit: `2026-09-01_RIDECHECK_CRM_L4-WILD-A-SCHEDULING-FORENSIC_AUDIT_TEMPORAL-FLOW.md`

Proven clean on a true zero-state thread (do not re-prove unless contradicted):
new-customer creation, single Contact/Thread/Lead, vehicle Peugeot 2008 + year 2014 +
`SUV_4X4_DEPORTIVO`, inspection-location authority (Berazategui kept, Tigre not applied),
pricing 150 000 + 90 000 = 240 000, FAQ service/report/presence/payment, acceptance,
burst assembly (7 inbound → 4 CE invocations, 0 duplicates), path attribution (4/4 `CE_TEXT`,
all `read`).

Open L4 defects:

| ID | Severity | Description |
|---|---|---|
| SCHED-A | HIGH | Primary relative-day preference (`mñ` = tomorrow) discarded whenever any weekday name appears in the burst (`_parse_scheduling_text` guard `and not day_name_found`). |
| SCHED-B | HIGH | Scheduling domain cannot represent PRIMARY + FALLBACK; the fallback day replaced the primary day and the primary clause's time was transplanted onto it (queried Thursday 15:00, never requested). |
| SCHED-D | HIGH | Reply never names the primary requested day and mis-states the rejection reason ("no disponibilidad" instead of "Thursday closes at 14:00"). |
| SCHED-E | HIGH | `_FAQ_HOURS_ANSWER` ("lunes a viernes de 9 a 18 hs") contradicts `ScheduleService._business_hours` (Mon 13–18, Tue 09:30–14, Thu 09–14). Two authorities for one business fact; directly caused the impossible Thursday 15:00 request. |
| FLOW-A | HIGH | Published Booking Flow `28104222025943520` has no sender: `settings.booking_flow_id` unreferenced, `make_booking_token()` never called, `OutboundPathId.BOOKING_FLOW` used at zero gate call sites. CE dispatches legacy Flow `1644218879979041`. |
| FLOW-B | HIGH | Two competing scheduling UXs (CE text negotiation vs in-Flow date/slot pickers). Runtime = TEXT, design intent = BOOKING_FLOW, `DOMAIN_MODEL §5` = text-then-Flow-for-data. **Owner decision required.** |
| FOR-01 | MEDIUM | All Wild A outbound rows carry `deployment_id='unknown'` and `correlation_id=NULL` (`GIT_SHA` injected by no compose file). Degrades the container-independent traceability invariant. |
| INFRA-OOM-01 | MEDIUM | Host global OOM (see §5.L4 Phase C below). |

Scheduling mathematics were verified CORRECT in both directions (Wed 02/09 has no
Berazategui-compatible slot at all; Thursday's only valid Sur slot is 13:00).
No frozen gate is contradicted: L1 (both days came from the same current turn, not from
stale history), L2 (path attribution and transport correct) and L3 (no history existed)
remain FROZEN.

Remediation gate: **L4.3-SCHEDULING-SEMANTICS** (Phases A–D in the audit §17).
Wild B must not be authorized until Phase A + Phase B land and the Phase C owner decision
is recorded.

### Phase B.1 — L4.3-SCHEDULING-SEMANTICS remediation (2026-09-01)

**OWNER DECISION RECORDED: AUTHORITATIVE SCHEDULING UX = BOOKING_FLOW**
(RideCheck Booking Flow `28104222025943520`, PUBLISHED). Text conversation keeps
availability negotiation only. Contract: `docs/architecture/BOOKING_UX_CONTRACT.md`.

Closeout: `2026-09-01_RIDECHECK_CRM_L4.3-SCHEDULING-SEMANTICS_CLOSEOUT_TEMPORAL-BOOKING.md`

| Finding | Phase | Status |
|---|---|---|
| SCHED-A primary relative-day discarded | A | **CLOSED** — `mñ` is never suppressed by a later weekday; day mentions are kept in utterance order |
| SCHED-B no primary/fallback model | A | **CLOSED** — `_parse_scheduling_requests()` returns ordered branches; each time bound to its own clause |
| SCHED-D rejection omits primary + real reason | A/E | **CLOSED** — one reply names the primary day, its real reason, then the fallback offer |
| SCHED-E FAQ hours diverge from scheduler | B | **CLOSED** — hours generated from `business_hours_for_weekday()`; single authority |
| FLOW-A published Booking Flow unreachable | C | **CLOSED** — `booking_flow_id` + `make_booking_token()` + `path_id=BOOKING_FLOW` wired through `OutboundSafetyGate` |
| FLOW-B competing scheduling UX | C/D | **CLOSED BY OWNER DECISION** — documented sequence; no text-only booking path exists |
| FOR-01 deployment_id/correlation_id missing | F | **CLOSED** — `GIT_SHA` injected in the beta compose; CE passes a per-turn correlation id |
| INFRA-OOM-01 host OOM | G | **PARTIAL** — swap + container limits + n8n restart policy + preflight script done; alerting still open (Phase C below) |

Evidence: `tests/test_l4_3_scheduling_semantics.py` 42/42 PASS (TEMP-01…07, ORDER-01…03,
HOURS-01/02, FLOW-01…08, FORENSIC-01/02, Wild A reproduction). Full regression
3074 passed / 72 failed / 9 errors — **zero regressions** against the pre-change baseline
(same 81 pre-existing failures, verified by differential run).

**L4 remains FAIL. Wild A stays PAUSED. Clean-Wild counter stays 0/3.** A new controlled
Wild is required to prove the remediation in runtime.

### Phase B.2 — L4.4-CLEAN-WILD-PREP (2026-09-01) — **PASS**

Closeout: `2026-09-01_RIDECHECK_CRM_L4.4-CLEAN-WILD-PREP_CLOSEOUT_RESET-TESTER-ZERO.md`

Tester prepared as a TRUE ZERO-STATE customer for the next full clean Wild.

- Wild A evidence exported and hashed before any DELETE:
  `/opt/ridecheck-crm-forensics/L4.4_wildA_tester_export_pre_cleanup_2026-09-01T201508Z.txt`
  (sha256 `d38c1be3…4cd9e0f`), alongside the Wild-window backend log, the audit-time DB
  export and the committed forensic audit.
- Tester operational rows deleted in ONE guarded transaction (database assertion,
  post-delete leftover assertion, shared-data preservation assertion): 1 contact,
  1 thread, 1 state, 1 candidate, 1 lead, 11 messages, 4 dedup, 1 recipient lock,
  7 ai_events. 0 revisions / 0 thread_revisions / 0 feedback existed.
- Zero-state proven by the certified L4.2 suite against live crm_test: **19/19 PASS**.
- Preserved unchanged: 733 security_events, 32 demo contacts/threads/leads,
  28 demo revisions (23 with turno), 211 viáticos zones, system settings, catalog,
  Flow config, n8n DB.
- Tester remains authorized: `CLOSED_BETA_ALLOWED_WA_IDS` unchanged (…8330) while the
  tester has zero CRM identity.
- Runtime preflight: image `l4.3-sched-103dd01`, DB crm_test, OUTBOUND **off**,
  phone `1196075770246218` CONNECTED/CLOUD_API/GREEN, n8n webhook registered (ACTIVE),
  Booking Flow `28104222025943520` **PUBLISHED** with no validation errors, BOOKING_FLOW
  path wired and reachable in the deployed container.
- Memory preflight PASS (swap 4095 MB, available RAM 1610 MB); n8n auto-recovery proven.
- Test-harness repair (test-only, no product code): the L4/L4.1 SQLite fixtures used a
  non-shared in-memory pool and a possibly-stubbed `app.db.Base`, which had been masking
  17 tests. Fixed → relevant gate suites now **270 passed / 0 failed / 1 skipped**, and
  full regression improves to 3 100 passed / 55 failed / 9 errors with **zero new failures**.

**Clean-Wild counter stays 0/3.** Outbound stays OFF; a controlled Wild B requires
explicit owner authorization.

### Phase B.3 — Wild B (2026-09-01) — **FAIL: vehicle + location evidence capture**

Forensic audit: `2026-09-01_RIDECHECK_CRM_L4-WILD-B-VEHICLE-FORENSIC_AUDIT_CANDIDATE-PERSISTENCE.md`

Wild B ran from the L4.4 zero state and was stopped after two turns. Contact/Thread/Lead
were created correctly, but **no candidate and no location were ever persisted**: the bot
answered "hacemos el servicio de revisión para un 2008 del 2014" with zero candidate rows
and then asked the customer to confirm the same vehicle. Findings VEH-A, VEH-B, LOC-A,
LOC-B (HIGH) and OBS-A (MEDIUM). L1/L2/L3 not contradicted — nothing was overwritten;
evidence was never captured. Evidence preserved and hashed before outbound was disabled.

### Phase B.4 — L4.6-EVIDENCE-CAPTURE (2026-09-01) — **PASS**

Closeout: `2026-09-01_RIDECHECK_CRM_L4.6-EVIDENCE-CAPTURE_CLOSEOUT_CANONICAL-CAPTURE.md`

| Finding | Phase | Status |
|---|---|---|
| VEH-A intent whitelist blocked deterministic capture | A | **CLOSED** — capture is gated on evidence + qualification state, not on intent phrasing; Layer D no longer intercepts a FAQ-dominant burst that names a numeric model with its year |
| VEH-B reply asserted a vehicle canonical state lacked | B | **CLOSED** — `_enforce_canonical_vehicle_claim` finalizer on the single outbound text path |
| LOC-A origin clause suppressed the inspection location | C | **CLOSED** — origin clauses are isolated and stripped; the remainder is re-read and buffered |
| LOC-B confirmation could not replay evidence | D/E | **CLOSED** — clarification always arms pending state; a new candidate inherits the cycle-scoped location buffer |
| OBS-A CE decisions invisible at runtime | F | **CLOSED** — `CE_DECISION` structured records + app logger wired in `main.py` |

Owner-rule conflict surfaced and resolved conservatively: L4.6 asked that
"quiero revisar un 2008" persist a candidate, while the certified WILD-02-B owner rules
(W02-O08…O12) require a bare numeric model to be *confirmed*, never silently created.
A bare number is the genuinely ambiguous case (model vs year), so the certified rule was
kept: the vehicle is resolved deterministically and the confirmation is armed, and
"2008 **del 2014**" — unambiguous — is persisted immediately. **Owner decision required if
the bare-numeric rule should change.**

Evidence: `tests/test_l4_6_evidence_capture.py` 28/28 PASS (VEH-01…08, STATE-01/02,
LOC-01…06, CONF-01…04, full Wild B reproduction through the $240.000 quote, decision
logging). Relevant gates 558 passed / 1 skipped / 0 failed. Full regression
3 128 passed / 55 failed / 9 errors — **zero new failures** against the differential baseline.

**Clean-Wild counter stays 0/3.** Wild C requires owner outbound authorization.

### Phase B.5 — L4.7D-RESPONSE-VALIDATOR (2026-09-01) — **PASS**

Closeout: `2026-09-01_RIDECHECK_CRM_L4.7D-RESPONSE-VALIDATOR_CLOSEOUT_CANONICAL-CLAIMS.md`

A general deterministic validation layer now sits between composition and the outbound
gate on **every** CE text path: COMPOSE → VALIDATE → OutboundSafetyGate → SEND.

All six claim classes are state-checked (L4.7 found only one was):

| Claim | Canonical proof required |
|---|---|
| VEHICLE | current-focus candidate |
| LOCATION | candidate zone / cycle-scoped buffer; customer origin is never the inspection location |
| PRICE | PricingService quote (or an amount already sent this cycle); wrong amounts are rewritten to canonical |
| AVAILABILITY | ScheduleService evaluation or its slots; negative statements and confirmed bookings exempted |
| BOOKING | booked ThreadRevision — sending the Flow is not booking |
| ACCEPTANCE | lead flag ACEPTADO or stage ≥ SCHEDULING |

Design rules keep it general, per §6.1: only assertive sentences carry claims (questions
always survive), and every class names its canonical proof. Failure behaviour is
sentence-level surgery — FAQ answers and the required next question are preserved; a
deterministic fallback is used only when nothing survives. Decisions log as
`CE_RESPONSE_VALIDATION`. AI authority is unchanged.

Evidence: `tests/test_l4_7d_response_validator.py` 33/33; relevant gates 926 passed /
1 skipped / 0 failed; full regression 3 161 passed / 55 failed / 9 errors — zero new
failures. One certified test superseded and disclosed
(`TestRC05Requote::test_rc05_price_requote`: an AI-invented $140.000 previously reached the
customer; the canonical $150.000 is now delivered).

**Clean-Wild counter stays 0/3. Wild C not run.** Next: **L4.7E-SEMANTIC-EQUIVALENCE-CORPUS**.

### Phase B.6 — L4.7E-SEMANTIC-EQUIVALENCE-CORPUS (2026-09-01) — **PASS**

Closeout: `2026-09-01_RIDECHECK_CRM_L4.7E-SEMANTIC-EQUIVALENCE-CORPUS_CLOSEOUT_REAL-WORLD-CORPUS.md`

The durable semantic truth model and evaluation corpus that will govern the semantic
migration. **No runtime behaviour changed** — no CE reordering, no prompt/model change.

- `docs/semantic/SEMANTIC_TRUTH_MODEL.md` — RAW → TURN EVIDENCE → CANONICAL STATE, the
  CONFIRMED/PROPOSED/AMBIGUOUS/CONFLICT status model, the provenance contract, the
  non-mutating replay contract, privacy rules and the metric definitions.
- `tests/semantic_corpus/real_world_turns.jsonl` — **162 cases**: 4 owner-provided real
  customer messages stored verbatim, 8 imported failed/known Wild utterances (Wild A
  scheduling, Wild B vehicle and location, Wild #1) each naming the failure class it
  exposed, and 150 authored variants across 12 equivalence groups (A–L), with ≥20 variants
  for intent, vehicle identity, location role, acceptance and scheduling.
- `tests/semantic_corpus/evaluation.py` — inert harness reporting field precision, field
  recall, role accuracy, unsupported-inference rate, ambiguity/conflict handling and
  missing-field accuracy, sliceable by REAL/SYNTHETIC and by group. No single opaque score.
- `tests/semantic_corpus/replay_demo.py` — read-only replay proven against thread 2037:
  4 inbound messages reconstructed, scored against corpus truth, **0 rows mutated**.
- `tests/test_l4_7e_semantic_corpus.py` — 33/33 PASS.

Truth is business-defined: labels were authored, never generated by asking a model.
`REAL-002` and `REAL-004` carry `owner_review_required: true` — genuinely uncertain
readings are left uncertain rather than forced.

**Online self-learning remains DISALLOWED.** The permitted loop is: real conversation →
anonymised labelled corpus → offline evaluation → prompt/schema/model improvement →
regression certification → controlled deployment.

### Semantic migration sequence (agreed)

```
L4.7E  corpus + truth model + harness            ← DONE
  → L4.7A   TurnEvidence schema (shadow, parse-and-log only)
  → L4.7B   single UNDERSTAND pass, feature-flagged shadow mode
  → L4.7B.1 corpus replay + disagreement analysis
  → L4.7C   deterministic reconciler migration (field by field)
  → L4.7F   certification (dirty history + gates)
  → Clean Wild C
```

L1/L2/L3 remain FROZEN, L4 remains ACTIVE, **Wild clean count stays 0/3**, no new Wild.

### Phase B.7 — L4.7A-TURN-EVIDENCE-SCHEMA (2026-09-01) — **PASS**

Closeout: `2026-09-01_RIDECHECK_CRM_L4.7A-TURN-EVIDENCE-SCHEMA_CLOSEOUT_STRUCTURED-EVIDENCE.md`

`backend/app/schemas/turn_evidence.py` (`turn-evidence/1.0`) implements the structured
contract between raw language and deterministic reconciliation. **Schema only** — nothing
imports it yet, no CE reordering, no prompt/model change.

- Typed evidence for service intents, vehicles, locations (mandatory role), FAQ intents,
  acceptance/hesitation, ordered scheduling branches, corrections, identity, handoff,
  ambiguities and conflicts — all coexisting, none erasing another.
- Provenance on every item (source kind, interpreter, model + schema version, source
  message ids, spans) and on the turn (`TurnRef.reconstruction`, recording that historical
  burst grouping is only PARTIAL).
- Interpretation is frozen; reconciliation dispositions live in a separate append-only
  `ReconciliationLog` (ACCEPTED / REJECTED / DEFERRED / NEEDS_CLARIFICATION /
  CONFLICT_UNRESOLVED / SUPERSEDED).
- Deterministic canonical JSON with a major-version guard and `extra="forbid"`.
- **Corpus compatibility 162/162** — every case maps into the schema and round-trips back
  through the L4.7E harness with zero false positives, zero false negatives and zero
  unsupported inferences.
- No business authority: the module imports only `json`, `enum`, `typing`, `pydantic`
  (asserted), and exposes no apply/commit/save path.

Evidence: `tests/test_l4_7a_turn_evidence_schema.py` 47/47 PASS (SCHEMA-01…12).

Next: **L4.7B-SHADOW-UNDERSTAND**. Wild clean count stays **0/3**.

### Phase B.8 — L4.7B-SHADOW-UNDERSTAND (2026-09-01) — **PASS**

Closeout: `2026-09-01_RIDECHECK_CRM_L4.7B-SHADOW-UNDERSTAND_CLOSEOUT_SHADOW-INTERPRETER.md`

One controlled semantic UNDERSTAND pass now runs **in shadow** on every burst, before any
deterministic gate can early-return. It proposes `TurnEvidence` and changes nothing:
ConversationEngine remains the sole authority for routing, canonical state and every send.

- `SemanticTurnInterpreter` (`understand/1.0`, gpt-4o-mini, ≤ 1 call per burst, JSON
  structured output). The prompt forbids price, availability, booking, lead state and
  candidate persistence outright and requires ambiguity/conflict preservation, location-role
  separation, ordered scheduling branches and accept/hesitate/reject distinction.
- Append-only recorder: JSONL + `CE_SHADOW_UNDERSTAND` log line with schema/model version,
  latency and tokens. No raw text, no secrets, no migration.
- Failure isolation: a shadow error is logged and dropped; the customer turn is untouched.
  The flag must be exactly `True`, so no test mock can trigger a model call.

**Corpus evaluation (162/162 calls OK):** role accuracy **1.000**, ambiguity/conflict
handling **1.000**, missing-field accuracy **1.000**, precision 0.428, recall 0.669,
unsupported-inference rate 0.056. Wild B's location turn scores perfectly; Wild B's vehicle
turn resolves *Peugeot 2008* but loses the year; Wild A's scheduling keeps both ordered
branches but mis-resolves "mñ" to a weekday (no current-date context). Group I (corrections)
is weakest at P=0.077.

Cost/latency: mean 2 390 ms, p95 3 722 ms, 1 256 tokens per burst ≈ **$0.11 per 100
conversations**. The added latency is a promotion decision, not yet a problem — shadow runs
only in crm_test with OUTBOUND OFF.

Evidence: `tests/test_l4_7b_shadow_understand.py` 29/29 (SHADOW-01…15); full regression
3 270 passed / 55 failed / 9 errors, zero new failures. Image
`ridecheck-crm-backend:l4.7b-shadow-a7ddddb` deployed to crm_test with shadow ON.

**Authority did not move.** Next: **L4.7B.1-SHADOW-DISAGREEMENT-ANALYSIS** — do not proceed
to L4.7C before that review. Wild clean count stays **0/3**.

### Phase B.9 — L4.7B.1-SHADOW-DISAGREEMENT-ANALYSIS (2026-09-02) — **COMPLETE**

Audit: `2026-09-02_RIDECHECK_CRM_L4.7B.1-SHADOW-DISAGREEMENT-ANALYSIS_AUDIT_SEMANTIC-GAPS.md`

All 162 corpus cases classified against **both** producers (shadow interpreter and a fair
read-only re-execution of today's deterministic extractors with a seeded zone DB):

| | Precision | Recall | Role acc. | Unsupported | Clean |
|---|---|---|---|---|---|
| CE deterministic | 0.679 (REAL 0.933) | 0.477 | 1.000 | 0.012 | 44 |
| Shadow (measured) | 0.428 | 0.669 | 1.000 | 0.056 | 22 |
| Shadow (artifact removed) | 0.548 | 0.669 | 1.000 | 0.012 | 44 |
| **Union of both** | 0.567 | **0.707** | 1.000 | 0.012 | 44 |

Labels: BOTH_CORRECT 6 · SHADOW_CORRECT_CE_WRONG 16 · CE_CORRECT_SHADOW_WRONG 38 ·
BOTH_WRONG 100 · OWNER_REVIEW 2 (projected after the artifact fix: 18 / 26 / 26 / 90 / 2).

**Finding: the two producers are complementary, not competing** — precise-and-blind vs
broad-and-noisy — which is exactly the asymmetric architecture L4.7C is meant to build.
62 % of all shadow errors come from two contract gaps (an empty-item JSON template artifact
and unstated intent-emission scope); removing the artifact alone lifts precision 0.428 →
0.548 and cuts unsupported inference 0.056 → 0.012. **MODEL CHANGE: NO** — every dominant
class is a prompt, schema, context, mapper or corpus-label issue.

A quality gate for promotion is now defined (REAL precision/recall ≥ 0.85, overall
unsupported ≤ 0.01 and REAL exactly 0, role accuracy 1.000, every group recall ≥ 0.70, plus
case-level requirements on the owner examples and the Wild cases). **Today it passes only
role accuracy and ambiguity handling.**

**L4.7B.2-SHADOW-INTERPRETER-QUALITY is therefore inserted before L4.7C.** Revised sequence:

```
L4.7E → L4.7A → L4.7B → L4.7B.1 → L4.7B.2 → corpus quality gate → L4.7C → L4.7F → Clean Wild C
```

Authority did not move; no code changed in this audit. Wild clean count stays **0/3**.

### Phase B.10 — L4.7B.2-SHADOW-INTERPRETER-QUALITY (2026-09-02) — **COMPLETE; QUALITY GATE NOT PASSED**

Closeout: `2026-09-02_RIDECHECK_CRM_L4.7B.2-SHADOW-INTERPRETER-QUALITY_CLOSEOUT_QUALITY-GATE.md`

Every disagreement class named in L4.7B.1 was remediated as a general contract, not a phrase
patch: empty-item artifact removed at prompt, mapper and schema level; temporal context
supplied with date resolution kept deterministic; the vehicle number pair kept whole;
catalog identity capped at `PROPOSED`; `FUTURE_INTENT` added (`turn-evidence/1.1`); intent
scope and FAQ coexistence stated as rules; bounded current-cycle context with its provenance
recorded; the model call moved off the customer turn into a bounded worker; confidence made
advisory. **Model unchanged** (`gpt-4o-mini`). Three SYNTHETIC corpus labels were corrected
under the stated intent rule; **no REAL label was touched**.

Full-corpus rerun (162/162 calls OK, five draws across four prompt revisions) — shipped
version `understand/1.4`:

| Metric | L4.7B (1.0) | L4.7B.2 (1.4) | Gate |
|---|---|---|---|
| field precision, overall | 0.428 | **0.716** | ≥ 0.80 ❌ |
| field recall, overall | 0.669 | 0.616 | ≥ 0.85 ❌ |
| unsupported inference, overall | 0.056 | **0.012** | ≤ 0.01 ❌ (2/162) |
| unsupported inference, REAL | 0.083 | **0.000** | 0.000 ✅ |
| role accuracy | 1.000 | **1.000** | 1.000 ✅ |
| ambiguity/conflict handling | 1.000 | **1.000** | ≥ 0.98 ✅ |
| field precision, REAL | 0.486 | **0.720** | ≥ 0.85 ❌ |
| field recall, REAL | 0.567 | **0.600** | ≥ 0.85 ❌ |
| clean cases | 22 | **93** | — |

**The quality gate is NOT passed** (8 of 12 groups are still below 0.70 recall; group I
precision 0.333). Per L4.7B.1 §11 this means **L4.7C does not start**. Semantic authority did
not move; TurnEvidence still feeds nothing.

Against today's deterministic CE extractors on the same corpus, the shadow interpreter now
leads on recall (0.616 vs 0.483) and clean cases (93 vs 47) at comparable overall precision
(0.716 vs 0.696) — while CE keeps a decisive lead on REAL precision (0.933). SHADOW_CORRECT_CE_WRONG 62 · CE_CORRECT_SHADOW_WRONG 18 ·
BOTH_CORRECT 29 · BOTH_WRONG 51 · OWNER_REVIEW 2. The two producers remain complementary,
which is the premise L4.7C is built on.

Wild clean count stays **0/3**. OUTBOUND OFF. L1/L2/L3 remain FROZEN.

### Phase B.11 — L4.7B.2A-CORPUS-TRUTH-REVIEW (2026-09-02) — **COMPLETE; GATE STILL FAILS**

Closeout: `2026-09-02_RIDECHECK_CRM_L4.7B.2A-CORPUS-TRUTH-REVIEW_CLOSEOUT_OWNER-INTENT-RULE.md`

**Owner rule recorded:** a first inbound to RideCheck does not imply active
`PREPURCHASE_INSPECTION` intent merely because the customer wrote to an inspection business.
Service intent must come from the wording; contacting us, politeness, saving the contact,
promising to write again and searching for a car are not intent. Service intent and
commercial readiness stay separate. The rule is encoded once as `names_the_service()` in
`tests/semantic_corpus/build_corpus.py` and applied by the generators, not by case lists.

10 labels contradicted the rule and were corrected — **1 REAL** (REAL-001, the owner's own
example) and **9 SYNTHETIC** (SYN-FUT-01…07 too aggressive; SYN-QUOTE-01/04 too weak). 5
`SYN-MIX` cases were flagged OWNER_REVIEW_REQUIRED and left unchanged. No REAL raw text was
touched; the interpreter was not changed.

**Answer to the open premise: the corpus was not the problem.** Rescoring the *same*
interpreter outputs against corrected labels moved recall 0.6157 → 0.6314 and left precision
unchanged at 0.7163 — only 6 of 93 missing items were wrong expectations. **93.5 % of the gate
distance is interpreter behaviour, not corpus error.**

A separate instrument defect was found and deliberately not corrected here: the 8 `SYN-MIX`
fixtures omit the business evidence their own text contains and use a `"mixed"` FAQ sentinel,
producing **40.7 % of all false positives** in the corpus. Excluding them, the same unchanged
interpreter measures P 0.810 — illustrative of instrument error, not an achieved metric.

Gate re-applied unchanged: **FAIL** (REAL P 0.720 / R 0.621, overall P 0.716 / R 0.631,
8 of 12 group recalls below 0.70). L4.7C does not start.

Next: **L4.7B.2B-CORPUS-FIXTURE-REPAIR** (corpus only — repair the 8 J fixtures, adjudicate
the 5 flagged cases, resolve the `readiness` vs `FUTURE_INTENT` engagement overlap), then
**L4.7B.3** for the genuinely interpreter-side classes. MODEL CHANGE: still NO.

Housekeeping: the untracked `backend/MagicMock/` tree (811 files, 839 shadow records, 0
tracked, 0 production references) was audited and removed; its cause was fixed in L4.7B.2.

Regression 3 328 passed / 60 failed / 9 errors — unchanged. OUTBOUND OFF. Wild clean count 0/3.

### Phase B.12 — L4.7B.2B-CORPUS-FIXTURE-REPAIR (2026-09-02) — **COMPLETE; GATE STILL FAILS**

Closeout: `2026-09-02_RIDECHECK_CRM_L4.7B.2B-CORPUS-FIXTURE-REPAIR_CLOSEOUT_TRUTH-INSTRUMENT.md`

The measuring instrument was repaired before any further interpreter work. Three defects,
all punishing correct behaviour: a `"mixed"` FAQ sentinel no interpreter can emit; fixtures
(all 8 `SYN-MIX`, all 8 `SYN-CORR`) that omitted the vehicle, year, locality or corrected
value written in their own raw text; and a harness that flattened the six `turn-evidence/1.1`
acceptance signals to a boolean, so FUTURE_INTENT was indistinguishable from REJECT and a
**false ACCEPT could not be counted at all**.

**Engagement ontology decided:** stance is represented once, in `acceptance`
(ACCEPT/REJECT/HESITATE/FUTURE_INTENT/QUESTION_ONLY/UNKNOWN). `readiness` keeps only
`SEARCHING_NOT_READY`, a fact about the customer's purchase process. `FUTURE_CONTACT_INTENDED`
and `HESITANT_OR_DEFERRED` are retired as duplicate truth and canonicalised by the harness.
Business readiness stays deterministic and downstream, out of the semantic object.

Same interpreter — `semantic_interpreter.py` SHA-256 identical before and after, prompt
`understand/1.4`, model `gpt-4o-mini`:

| | Before | After | Cause |
|---|---|---|---|
| overall precision | 0.716 | **0.885** | instrument |
| overall recall | 0.631 | **0.728** | instrument |
| unsupported inference | 0.0123 | **0.0062** | instrument (a fixture forbade a locality its own text named) |
| clean cases | 92 | **99** | instrument |
| REAL precision / recall | 0.720 / 0.621 | **0.800 / 0.667** | instrument |
| group I precision | 0.333 | **0.909** | instrument |
| group J precision | 0.250 | **0.857** | instrument |

New stance metrics: exact accuracy **0.725**, **false ACCEPT rate 0.000**, FUTURE_INTENT
recall 0.727, HESITATE recall 0.167. Rescoring the *identical saved outputs* with the
repaired ruler accounts for +0.115 precision on its own — **none of the movement is model
quality**, and the closeout says so explicitly.

Gate re-applied unchanged: **FAIL** — REAL P 0.800, REAL R 0.667, overall R 0.728, and 7 of
12 group recalls below 0.70. Six of ten gate lines now pass (up from four). L4.7C does not
start.

Corpus integrity audit (`tests/semantic_corpus/integrity.py`, new): 7 findings → 4 objective
synthetic repairs, 3 detector corrections; final audit clean. 3 REAL labels changed, each a
mechanical ontology migration or the owner's explicit example; **no REAL raw text changed**,
owner REAL-001…004 byte-verbatim, all Wild raw examples untouched.

Tests `tests/test_l4_7b_2b_corpus_instrument.py` 19/19 (FIXTURE-01…12). Regression 3 347
passed / 60 failed / 9 errors — failure set byte-identical to baseline.

Next: **L4.7B.3-SHADOW-SEMANTIC-QUALITY**. OUTBOUND OFF. Wild clean count **0/3**.

### Phase B.13 — L4.7B.3-SHADOW-SEMANTIC-QUALITY (2026-09-03) — **COMPLETE; 9 OF 10 GATE LINES PASS**

Closeout: `2026-09-03_RIDECHECK_CRM_L4.7B.3-SHADOW-SEMANTIC-QUALITY_CLOSEOUT_FINAL-QUALITY-GATE.md`

One focused interpreter pass against the instrument repaired in L4.7B.2B. Eight prompt
revisions, each measured on the full 162-case corpus; two were rejected for re-introducing an
unsupported inference on a REAL case despite higher aggregate recall. Shipped
**`understand/1.12`**; **model unchanged** (`gpt-4o-mini`).

| | L4.7B.2B (1.4) | L4.7B.3 (1.12) |
|---|---|---|
| field precision / recall, overall | 0.885 / 0.728 | **0.938 / 0.890** |
| field precision / recall, REAL | 0.800 / 0.667 | **0.871 / 0.900** |
| unsupported inference | 0.0062 | **0.000** |
| role accuracy · ambiguity | 1.000 · 1.000 | **1.000 · 1.000** |
| clean cases | 99 | **131** |
| stance exact · false ACCEPT | 0.725 · 0.000 | **0.875 · 0.000** |

**Quality gate: FAIL on one line only** — every group A–L recall ≥ 0.70, missed by group I
(0.632) and group L (0.591). All nine other lines pass, including REAL precision and recall,
which failed in every previous milestone. Both group failures are the same residual class:
the companion item omitted next to the value it belongs to (`corrections[]` beside a corrected
value; `SEARCHING_NOT_READY` beside a stance).

Critical cases, reproducible across two draws: **WILD-A-04 clean** (PRIMARY TOMORROW 15:00 /
FALLBACK THURSDAY flexible — the Wild A scheduling class is closed), **WILD-B-02 clean** (both
location roles), **WILD-B-01 keeps Peugeot 2008 + 2014**, REAL-001 and REAL-004 clean, and
**zero unsupported inference on every owner example and every Wild case**. Group C reached
1.000 recall at 1.000 role accuracy.

Authority unchanged: no CE behaviour change, no canonical mutation, no outbound, shadow still
asynchronous. Image `ridecheck-crm-backend:l4.7b3-semantic-29538be` on crm_test only, parity
verified, live probe correct on all three Wild classes, `whatsapp_messages` 6 → 6.

Tests `tests/test_l4_7b_3_shadow_semantic_quality.py` 29/29. Regression 3 376 passed / 60
failed / 9 errors — failure set identical to baseline. Launch-gate suites 427 passed, with
only the pre-existing environment-dependent `test_l4_2_clean_slate` allowlist case.

Next: **L4.7B.4-COMPANION-EVIDENCE** (finite: lift groups I and L over the 0.70 recall floor
without disturbing the nine passing lines), then **L4.7C-SEMANTIC-RECONCILER-DESIGN** as an
audit/design pass. Wild clean count **0/3**. OUTBOUND OFF.

### Phase B.14 — L4.7B.4-COMPANION-EVIDENCE (2026-09-03) — **COMPLETE; GATE FAILS ONE LINE; PROMPT WORK EXHAUSTED**

Closeout: `2026-09-03_RIDECHECK_CRM_L4.7B.4-COMPANION-EVIDENCE_CLOSEOUT_FINAL-SHADOW-GATE.md`

Companion evidence — a value and the relation that explains it — is now guaranteed
**deterministically** where a guarantee is possible: a correction with a real relation can no
longer be pruned as empty; a named superseded vehicle yields the replacement relation; a
year-moving correction yields the corrected year. Neither derivation infers anything about
the customer, and a template echo derives nothing (a guard added after an intermediate
revision manufactured a false correction on two Wild cases).

Shipped `understand/1.18`, **model unchanged** (`gpt-4o-mini`). Two draws:

| | draw 1 | draw 2 |
|---|---|---|
| precision / recall, overall | **0.950 / 0.898** | 0.942 / 0.890 |
| precision / recall, REAL | **0.897 / 0.867** | 0.897 / 0.867 |
| unsupported · role · ambiguity | 0.000 · 1.000 · 1.000 | 0.000 · 1.000 · 1.000 |
| group I recall | 0.737 | 0.684 |
| group L recall | 0.636 | 0.636 |
| clean cases · false ACCEPT | 133 · 0.000 | 132 · 0.000 |

**Quality gate: FAIL on one line** — every group A–L recall ≥ 0.70. Group I rose from 0.632
and now straddles the floor; group L sits at 0.636. Nine lines pass on both draws, REAL
precision and recall among them. **L4.7C does not start.**

Critical cases in both draws: REAL-001/003/004 clean, WILD-A-04 clean, WILD-B-02 clean,
WILD-B-01 keeps Peugeot 2008 + 2014, zero unsupported inference everywhere, false ACCEPT
0.000. Live probe produced both companion pairs in one turn.

**Per the milestone contract no further broad semantic milestone is proposed.** The residual
is classified for an explicit owner decision: group L = MODEL_LIMIT (six prompt formulations
across two milestones never held ≥0.70) with a CORPUS_LIMIT alternative; group I remainder,
`quote_request` on a service question and HESITATE = PROMPT_LIMIT; REAL-002 = MODEL_LIMIT.
Options: accept the gate as met in substance and go to **L4.7C-SEMANTIC-RECONCILER-DESIGN**;
authorise one shadow model comparison for the companion-emission class; or revisit the two
group-L labels. Tests 16/16; regression 3 392 / 60 / 9 — identical to baseline.
Image `ridecheck-crm-backend:l4.7b4-companion-c33ab79`, crm_test only, OUTBOUND OFF.
Wild clean count **0/3**.

### Phase B.15 — L4.7C-SEMANTIC-RECONCILER-DESIGN (2026-09-03) — **DESIGN COMPLETE; NOTHING IMPLEMENTED**

Audit: `2026-09-03_RIDECHECK_CRM_L4.7C-SEMANTIC-RECONCILER-DESIGN_AUDIT_CLAIM-AUTHORITY.md`

**Owner quality exception, recorded:** L4.7B.4 passed 9 of 10 gate lines (group L recall
0.636, group I 0.737/0.684). Accepted **for reconciler design only**. It does not authorise
semantic evidence to mutate canonical state, and the recorded metrics stand unaltered —
**L4.7B is not marked complete or perfect**. The exception is defensible because the residual
is an *omission* class and the design makes omission structurally harmless: a missing
`SEARCHING_NOT_READY` yields NEITHER, which authorises nothing.

Design decisions of record:

* **Reconcile and authorize are separate steps.** "What is true enough to record" is not
  "what may now be done". A confirmed vehicle authorises no quote; a confirmed quote
  authorises no booking.
* **Authority is per claim type, never per producer.** No "CE beats LLM", no confidence
  ranking, no last-writer-wins. The semantic interpreter outranks CE on location *roles*; the
  catalog and zone resolver outrank it on vehicle identity and zone validity; price,
  availability and booking have **no** semantic producer at all.
* **Four information states** (NEITHER / TRUE_ONLY / FALSE_ONLY / BOTH) map onto the existing
  `turn-evidence/1.1` statuses plus a new `polarity`. **Absence is never false**, and no
  authorization precondition may be written as "not X".
* **Claims carry `temporality` and `modality`**, so *"si me cierra te hablo"* (FUTURE +
  CONDITIONAL) is structurally disqualified from acceptance regardless of confidence.
* `quote_accepted = TRUE` requires five conditions including a quote already delivered in this
  cycle and unchanged since.
* **Truth maintenance is minimal**: a `Justification` per canonical value (evidence ids +
  rule id + version) plus a ~20-row static dependency table for invalidation. No
  event-sourcing platform, no new canonical tables, at most one append-only log table.
* **`field_evidence.py` (M21.1.5) is the seed of the reconciler**, not a competitor: it
  already resolves eight fields with source labels, read-only, and is consumed by CE.
* ~40 CE deterministic helpers classified A (authoritative validator) / B (secondary evidence)
  / C (business rule) / D (redundant NLP gate). Class D is retired **last**, in C6, only after
  replay certification.
* `ReconciliationRecord`: **EXTEND** (claim_type, evidence_ids, candidate_values, rule
  id/version, information_state, outcome, cycle/revision, depends_on, supersedes, risk_tier) —
  additive, `turn-evidence/1.2`.
* Reproducibility stated honestly: the *decision* replays exactly; the *model's claims* do not
  — they are recorded rather than re-derived.

Plan C1…C7, each flag-guarded and reversible: primitives+log → vehicle/location →
intent/stance/acceptance → scheduling interface → derived-state invalidation → authority
cutover → replay certification. **Migration size MEDIUM; no big-bang rewrite.** New runtime
metrics defined for Wild certification, with three launch-blocking zero-targets: false
progression, quote error, booking error.

No code, no migration, no runtime change, no authority moved. Semantic migration is **not**
complete. OUTBOUND OFF · Wild clean count **0/3**.

Next: **L4.7C.1-RECONCILER-PRIMITIVES (phase C1)** — shadow only, changes nothing.

### Phase B.16 — L4.7C.1-RECONCILER-PRIMITIVES (2026-09-03) — **PASS (shadow only)**

Closeout: `2026-09-03_RIDECHECK_CRM_L4.7C.1-RECONCILER-PRIMITIVES_CLOSEOUT_SHADOW-CLAIMS.md`

Phase C1 of the L4.7C design is implemented **in shadow**: `ClaimEvidence` (polarity,
evidence class, explicitness, temporality, modality, cycle scope, supersedes, content-hash
id), the four-valued information state where **absence is never false**, projection from both
producers into one claim space, and a versioned reconciler that records a decision without
taking one. `turn-evidence/1.2` extends `ReconciliationRecord` additively;
`shadow-record/1.2` carries the summary.

**C1 PASS means the primitives exist in shadow. It does NOT mean semantic authority
migrated.** No canonical write, no business action, no class-D parser retired, CE fully
authoritative.

Corpus observation (162 cases, 275 claims): TRUE_ONLY 256 · BOTH 7 · FALSE_ONLY 2; outcomes
ACCEPT 257 · CLARIFY 7 · HOLD 1; producer comparison AGREE 249 · DETERMINISTIC_ONLY 43 ·
SEMANTIC_ONLY 7 · CONFLICT 9. Disagreement is now counted instead of being resolved by
whichever code path ran first.

Critical scenarios: Berazategui/Tigre split into two claims with two roles; *"si me cierra te
hablo"* projects FUTURE+CONDITIONAL and reconciles to **HOLD with no canonical value**; the
year correction carries both sides; ordered scheduling survives as one claim; a model-only
"Fox" yields EXPLICIT_CUSTOMER model and SEMANTIC_INFERRED make, with CATALOG_CONFIRMED only
from the deterministic producer.

Live probe on `ridecheck-crm-backend:l4.7c1-claims-8b91117` (crm_test, OUTBOUND OFF):
7 claims from **both** producers, all ACCEPT, no `quote_accepted` claim for the conditional
sentence, `whatsapp_messages` 6 → 6, candidates 0 → 0, no raw text stored.

Tests 30/30 (CLAIM-01…18) including a static no-authority proof over the three new modules.
Regression 3 422 passed / 60 failed / 9 errors — identical to baseline; launch-gate suites
443 passed. A third instance of the module-reload identity hazard was found and fixed.

Next: **L4.7C.2-VEHICLE-LOCATION-RECONCILIATION** — not started automatically.
Wild clean count **0/3**.

### Phase B.17 — L4.7C.2-VEHICLE-LOCATION-RECONCILIATION (2026-09-03) — **PASS (first authority cutover)**

Closeout: `2026-09-03_RIDECHECK_CRM_L4.7C.2-VEHICLE-LOCATION-RECONCILIATION_CLOSEOUT_FIELD-CUTOVER.md`

Vehicle identity and inspection location are now written by a named rule that records why,
instead of by whichever code path reached the field first. Flag-guarded
(`RECONCILER_VEHICLE_AUTHORITY_ENABLED`, `RECONCILER_LOCATION_AUTHORITY_ENABLED`, default OFF,
set only in crm_test) and reversible: with the flags off the write path is the legacy
assignment byte for byte.

Authority is per claim, never per producer: the customer's words say WHICH car and the
**catalog** says what it is called and what category it is; the semantic layer reads the
location ROLE and the **zone resolver** says whether the locality exists. An inferred make is
a catalog *probe*, never authority. An origin claim can never populate the inspection
location — enforced by claim type, not by ordering.

Single write path: 5 vehicle and 8 location assignment sites routed through two chokepoints,
with AST tests that fail if any candidate field is assigned outside them.

Shadow-vs-authority over the corpus: vehicle 32 AGREE / 1 CLARIFY, location 31 AGREE / 0
refusals, origin-only writes 0. The single disagreement is a bare numeric model the catalog
cannot place — the authority asks instead of guessing.

Two defects found by first real use and fixed: `information_state` treated any negation as
contradicting any assertion, so "es un Ka… no, un Kuga" read as BOTH (a replacement is not a
contradiction); and justifications lived only in the log stream, now appended to
`reconciliation_records.jsonl` — append-only, no new table, no migration.

Unchanged: pricing, scheduling, availability, booking, acceptance, handoff, lead lifecycle,
the `state.home_zone_*` pre-candidate buffer, and every class-D parser (retirement is C6).

Tests 26/26 (VL-01…18). Regression 3 448 / 60 / 9 with flags off **and** on — failure set
identical to baseline. Image `ridecheck-crm-backend:l4.7c2-fieldauth-7d61c40`, crm_test only,
OUTBOUND OFF, production untouched. Wild clean count **0/3**.

Next: **L4.7C.3-INTENT-STANCE-ACCEPTANCE-RECONCILIATION** — the high-risk family; not
automatic.

### Phase B.18 — L4.7C.3A-ACCEPTANCE-AUTHORIZATION-SHADOW (2026-09-03) — **PASS (shadow only)**

Closeout: `2026-09-03_RIDECHECK_CRM_L4.7C.3A-ACCEPTANCE-AUTHORIZATION-SHADOW_CLOSEOUT_AUTHORIZATION-PROOF.md`

The highest-risk transition now has a deterministic predicate, proven before it is trusted.
`authorize_quote_acceptance` ALLOWs only with an ACCEPT stance read from the customer (never
derived), ASSERTED + PRESENT + FACTUAL, a quote that exists **and was delivered** (its amount
present in the outbound ledger — computing a price is not telling the customer), acceptance
and quote in the current cycle, and the quote's inputs unchanged. `SEARCHING_NOT_READY`,
candidate conflict and location conflict are blockers: they block when present and prove
nothing when absent — readiness is never a prerequisite for ALLOW.

**Quote identity needs no migration**: it is derived from what the quote was computed from
(cycle · revision · candidate · category · zone · amount), so any input change is a staleness
signal.

Legacy vs new over the corpus, four quote scenarios: AGREE_DENY 119 · AGREE_ALLOW 14 ·
NEW_SAFER 54 · **LEGACY_SAFER 0** · **UNEXPLAINED 0**. Safety metrics all zero: false
progression, stale-quote progression, prior-cycle progression, computed-but-undelivered
acceptance, unsupported authorization. Valid acceptance coverage **19/21 = 90.5 %**.

Found by running the two side by side: the legacy `_has_acceptance_word` guard fires on
"**Bueno**, quería revisar una 2008…" — a false-progression risk in the system as it stands.
Two conservative misses recorded: an unaccented "Si avancemos" reads as conditional
(WILD-A-03; first item for C3B), and "Ok, cuándo pueden?" produced no stance at all.

Legacy acceptance paths mapped and classified (EVIDENCE_PRODUCER / BUSINESS_PRECONDITION /
WRITE_PATH / TEMPORARY_COMPATIBILITY / RETIRE_IN_C6). Nothing changed: CE remains
authoritative, `conversation_engine.py` does not reference the authorizer at all, and a test
asserts it.

Tests 27/27 (AUTH-01…20). Regression 3 475 / 60 / 9 — identical to baseline. Image
`ridecheck-crm-backend:l4.7c3a-authshadow-c3e82ec`, crm_test only, C2 vehicle/location
authority still ON, OUTBOUND OFF, production untouched. Wild clean count **0/3**.

Next: **L4.7C.3B-ACCEPTANCE-AUTHORITY-CUTOVER** — not automatic.

### Phase B.19 — L4.7C.3B-ACCEPTANCE-AUTHORITY-CUTOVER (2026-09-03) — **PASS (second authority cutover)**

Closeout: `2026-09-03_RIDECHECK_CRM_L4.7C.3B-ACCEPTANCE-AUTHORITY-CUTOVER_CLOSEOUT_ACCEPTANCE-CUTOVER.md`

Quote acceptance and commercial progression now require the deterministic predicate proven in
C3A. Flag-guarded (`RECONCILER_ACCEPTANCE_AUTHORITY_ENABLED`, default OFF, crm_test only) and
reversible: with the flag off the legacy branch is reached unchanged, and the regression is
identical to baseline in both positions.

**The unaccented "si" is fixed as a grammatical invariant**, not a phrase patch: a conditional
needs a consequence, so "si avancemos" / "si dale" / "si coordinemos" are affirmations while
"si me cierra te hablo" / "si puedo te aviso" stay FUTURE + CONDITIONAL. Coverage 19/21 →
**20/21 = 95.2 %**.

Four QUOTED progression sites gated: the explicit acceptance branch, the weaker
`_has_acceptance_word` guard, and the three day-proposal transitions (via
`authorize_scheduling_progression`, which shares the quote prerequisites without requiring a
stance). Scheduling **interpretation** untouched — that is C4. Booking untouched.

**A defect was caught by the live probe and fixed:** the first build assumed a stance because
the caller had matched acceptance language, which would have let *"Bueno, quería revisar una
2008…"* through the predicate — the very false positive being closed. The stance is now
evidence, not an assumption: a claim exists only when the turn is acceptance throughout.

Gate: false progression 0 · stale-quote 0 · prior-cycle 0 · computed-not-delivered 0 ·
unsupported authorization 0 · LEGACY_SAFER 0 · unexplained 0 · single acceptance write path
YES · WILD-A-03 PASS · "Bueno…" SAFE. The one coverage miss, "Ok, cuándo pueden?", is audited
and HOLD is the correct outcome: genuinely ambiguous between agreeing and asking.

Tests 28/28 (CUT-01…20). Regression 3 503 / 60 / 9 flag off and on. Image
`ridecheck-crm-backend:l4.7c3b-acceptauth-8aca1f4`, crm_test only, all three authority flags
ON there, OUTBOUND OFF, production untouched. Wild clean count **0/3**.

Next: **L4.7C.4-SCHEDULING-INTERFACE** — not automatic.

### Phase C — Infrastructure resilience (INFRA-OOM-01)

**Host OOM incident, 2026-09-01 18:30:37Z — CONFIRMED, MEDIUM, launch-relevant.**

Global OOM on a ~4 GB, zero-swap host killed the Claude Code process and the n8n container
(exit 137, `OOMKilled=true`). No server reboot; backend and postgres survived. Occurred
6.7 minutes AFTER the last Wild A message — **it did not cause the scheduling defect**, and
it does not reopen L1/L2/L3.

Mitigated (owner, same day): 4 GB `/swapfile` active and persisted in `/etc/fstab`;
OUTBOUND returned to OFF; n8n restarted only after outbound was confirmed OFF.

Swap alone is necessary but NOT sufficient. Required before public launch:

1. Per-container `mem_limit` (n8n ≈ 1 GB, backend ≈ 1 GB, postgres ≈ 768 MB) so a runaway
   container is killed in isolation instead of triggering a global OOM with an arbitrary victim.
2. `restart: unless-stopped` on n8n — it is the sole inbound transport; an OOM kill currently
   silences inbound traffic with no self-healing.
3. n8n liveness + inbound-gap alert via the proven Resend channel (webhook received but no CE
   invocation within N seconds).
4. Memory cap on heavy local workloads (full pytest runs, agent processes) via
   `systemd-run --scope -p MemoryMax=`.
5. Preflight assertion in the Wild/test runbook: swap ≥ 2 GB active AND ≥ 1 GB available memory
   before a full regression suite or a Wild session.

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

# 6.1 No phrase-specific patch rule (L4.7, 2026-09-01)

**No production fix may consist solely of adding a phrase, regex or alias so that one Wild
sentence passes.** Every natural-language fix must implement a documented general semantic
invariant, and must be accepted by a semantic-equivalence corpus (≥ 20 phrasings mapping to
the same structured evidence and the same canonical DB result) — not by the sentence that
triggered it.

Rationale: Wild A and Wild B both failed on phrasing boundaries
("quería revisar" vs "para revisar"; "el auto está en X" vs "está en X, pero yo soy de Y").
Enumerating phrasings deterministically does not converge. See
`2026-09-01_RIDECHECK_CRM_L4.7-SEMANTIC-ARCHITECTURE-AUDIT_AUDIT_UNDERSTAND-RECONCILE.md`.

Architecture verdict recorded by L4.7: **PARTIAL SEMANTIC ARCHITECTURE** — the semantic
layer holds no business authority (correct), but it runs after 22 deterministic layers and
19 early-return points, and 9 gates can still suppress evidence capture. Proposed finite
remediation: L4.7A schema → L4.7B single UNDERSTAND pass → L4.7C evidence reconciler →
L4.7D response validator → L4.7E semantic-equivalence corpus → L4.7F certification.
L4.7D and L4.7E are independently valuable and need not wait for the pipeline reorder.

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
**WILD C — runtime proof of the L4.3 + L4.6 remediations (awaiting owner outbound authorization)**

Wild B (2026-09-01) failed on vehicle/location evidence capture and was remediated by L4.6.
Before Wild C the tester must be returned to zero state again (the L4.4 procedure), then
outbound enabled with owner authorization.

Superseded entry:
**WILD B — runtime proof of the L4.3 remediation (awaiting owner outbound authorization)**

L4.4 (2026-09-01) has prepared the tester as a true zero-state customer and verified every
preflight except outbound itself. The single remaining action before Wild B is the owner's
explicit outbound authorization, followed by the standard enable procedure.

L4.3 landed 2026-09-01: ordered primary/fallback scheduling, single business-hours
authority, Booking Flow wired as the authoritative booking UX, forensic attribution and
OOM hardening. Code-level evidence is complete; runtime evidence is not. Next action is a
controlled Wild B on the clean-slate tester, with OUTBOUND enabled only after the standard
preflight (including `scripts/preflight_memory_check.sh`).

Superseded entry:
**L4.3 — SCHEDULING SEMANTICS REMEDIATION (Wild A defects)**

L1, L2, L3 remain frozen. L4.1 + L4.1A + L4.1B + L4.2 complete. Wild A was executed on
2026-09-01 against a true clean-slate tester and PAUSED at the scheduling turn; everything up
to and including quote acceptance passed. Next action is L4.3 Phase A + Phase B, plus the
Phase C owner decision on the authoritative booking UX (text vs Booking Flow).
Wild B is NOT authorized until those land. Forensic audit:
`2026-09-01_RIDECHECK_CRM_L4-WILD-A-SCHEDULING-FORENSIC_AUDIT_TEMPORAL-FLOW.md`.

Historical (superseded by the line above):
**L4 — Wild A: True First-Time Customer (awaiting owner outbound authorization)**

**L4.2 CLEAN-SLATE TESTER PREPARATION complete (2026-09-01):**
- All prior tester operational state deleted from crm_test (Contact, Thread, State, Lead, Candidates, Revisions, ThreadRevisions, Messages, Dedup, AiEvents, RecipientLock) ✅
- Forensic export preserved at `/opt/ridecheck-crm-forensics/` ✅
- 733 global security events preserved intact ✅
- 32 demo contacts / 32 demo threads / 32 demo leads / 28 demo revisions preserved ✅
- Tester allowlist `CLOSED_BETA_ALLOWED_WA_IDS=5491153368330` intact ✅
- 19/19 L4.2 new tests PASS ✅
- 72/72 SQLite frozen gates PASS (L1 + M2 + M21.3) ✅
- 22/22 PostgreSQL L4+L4.1 PASS ✅

**Certification strategy:**

| Wild | Scenario | Purpose |
|---|---|---|
| Wild A | Brand-new customer — first Contact, Thread, Lead, Revision #1 | Prove new-customer onboarding and full quote/booking lifecycle |
| Wild B | Same persistent identity — second Revision (cycle_reset_pending must fire) | Prove returning-customer cycle boundary; Revision #1 preserved |
| Wild C | Third meaningful scenario (defined after A+B based on remaining risk) | TBD |

**Post-cleanup pre-Wild A baselines (2026-09-01):**
```
LAST_GLOBAL_INBOUND_ID   = NULL (all prior tester messages cleaned)
LAST_GLOBAL_OUTBOUND_ID  = NULL
OUTBOUND_LEDGER_COUNT    = 0
SECURITY_EVENTS_TOTAL    = 733
UNAUTHORIZED_PATH_EVENTS = 733 (all pre-existing, audit trail from certification)
NON_TESTER_OUTBOUND      = 0
TESTER_CONTACT_COUNT     = 0
TESTER_THREAD_COUNT      = 0
TESTER_LEAD_COUNT        = 0
TESTER_REVISION_COUNT    = 0
```

**First-inbound expectation for Wild A:**
- Creates new Contact (wa_id=5491153368330)
- Creates new Thread linked to new Contact
- Creates new Lead (via n8n lead-find/create)
- Creates new ThreadState: cycle_reset_pending=False, all zone/stage/scheduling fields None
- CE processes normally — NO cycle reset triggered (no prior cycle)
- No inherited vehicle, location, quote, or scheduling preferences

**All runtime preflight gates PASS:**
```
WHATSAPP_PHONE_NUMBER_ID == 1196075770246218        ✅ (corrected in L4.1B)
phone 1196075770246218 status == CONNECTED / GREEN  ✅
OUTBOUND_ENABLED == false                           ✅
cycle_reset_pending: N/A (tester does not yet exist) ✅
```

**Required before Wild A outbound authorization:**

1. Owner explicitly authorizes Wild A outbound.
2. Log retention: copy backend logs BEFORE `docker compose up --force-recreate` (mandatory — prevents log loss as in Wild #1).
3. Enable outbound: `cd /opt/ridecheck-crm && BETA_OUTBOUND_ENABLED=true docker compose -f docker-compose.yml -f /opt/ridecheck-crm-release-candidate/docker-compose.beta.yml up -d --force-recreate backend`
4. Tester sends first WhatsApp message — CE creates fresh Contact/Thread/Lead and responds normally.

**Wild B (do NOT execute until Wild A is completed):**
After Wild A completes, the owner signals a new cycle via CRM:
1. Set lead.estado to `REVISION_COMPLETA` (or any non-CONSULTA_NUEVA state).
2. Then set lead.estado to `CONSULTA_NUEVA` via the CRM `set_lead_estado()` path.
3. `cycle_reset_pending=True` is set automatically.
4. Verify in preflight before Wild B.
5. Tester sends new message → CE executes cycle_reset → Revision #2 begins; Revision #1 preserved.

Forensic export: `/opt/ridecheck-crm-forensics/L4.2_tester_forensic_export_2026-09-01.txt`
L4.1 closeout: `2026-09-01_RIDECHECK_CRM_L4.1-WILD-REMEDIATION_CLOSEOUT_REAUTHORIZE-WILD.md`
L4.1A audit: `2026-09-01_RIDECHECK_CRM_L4.1A-META-ASSET-CORRECTION_AUDIT_DELIVERY-ROOT-CAUSE.md`
L4.1B closeout: `2026-09-01_RIDECHECK_CRM_L4.1B-PHONE-ID-REMEDIATION_CLOSEOUT_ENV-CORRECTED.md`
L4.2 closeout: `2026-09-01_RIDECHECK_CRM_L4.2-CLEAN-SLATE-TESTER_CLOSEOUT_PREPARE-ZERO-STATE.md`

# 11. Status tracker

| Gate | Status | Can advance? |
|---|---|---|
| L1 Semantic Authority | **FROZEN — CONDITIONAL PASS** | YES |
| L2 Transport + Operations | **FROZEN — PASS (2026-09-01)** | YES |
| L3 Dirty-History Certification | **FROZEN — PASS (2026-09-01)** | YES |
| L4 Runtime + Wild Certification | **FAIL — Wild A (scheduling) and Wild B (evidence capture) failed and were remediated (L4.3, L4.6); response validator added (L4.7D); tester zero state prepared (L4.4); runtime proof still pending; 0/3 clean sessions** | AFTER L4.7E AND A CONTROLLED WILD C |
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
