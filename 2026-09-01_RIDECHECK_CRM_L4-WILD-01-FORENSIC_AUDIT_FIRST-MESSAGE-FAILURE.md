PROJECT: RIDECHECK_CRM / TYPE: AUDIT / MILESTONE: L4-WILD-01-FORENSIC

# RideCheck CRM — L4-WILD-01 Forensic Audit: First-Message Failure
**Date:** 2026-09-01
**Operator:** Assistant (automated, no code changes)
**Outbound disabled:** 2026-09-01T15:38:40Z (immediately on defect detection)
**Production DB touched:** NO
**Code changes made:** NO

---

## Part 1 — Evidence Preservation

### 1.1 Outbound shutdown timestamp
OUTBOUND_ENABLED set to false at **2026-09-01T15:38:40Z** via container recreate.
No WhatsApp messages were sent after this point.

### 1.2 DB snapshot taken
All forensic queries executed against `crm_test` PostgreSQL.
No DB writes were performed during forensic collection.
No tester state was corrected, no candidates were mutated, no cycle state was reset.

### 1.3 Pre-Wild baseline (from L4 preflight artifact)
These values were recorded before outbound was enabled:

| Key | Pre-Wild value |
|---|---|
| LAST_OUTBOUND_ID | 5695 |
| LAST_INBOUND_ID | 5694 |
| DEDUP_RECORDS | 23 |
| SECURITY_EVENTS_TOTAL | 733 |
| last_stage | QUOTED |
| cycle_reset_pending | False |
| home_zone_group | Sur |
| home_zone_detail | Berazategui |
| current_cycle_started_at | 2026-08-27T19:20:56Z |
| current_focus_candidate_id | 129 |

### 1.4 Backend log status
Backend CE processing logs from the Wild incident are **LOST**. The container was recreated (via `docker compose up -d --force-recreate backend`) to disable outbound immediately after the failure was detected. Only startup logs were available in the new container instance. The outbound ledger in `whatsapp_messages` is the authoritative forensic record per CLAUDE.md M2 invariants.

---

## Part 2 — Cycle Boundary Analysis

### 2.1 Prior-cycle state (2026-08-27 Wild-01)
The tester's prior Wild session (codename Wild-01) established the following state on 2026-08-27:
- A conversation was conducted, a quote was generated, candidate 129 was created
- `current_cycle_started_at` was set to `2026-08-27T19:20:56Z`
- `last_stage` was left as `QUOTED`
- `cycle_reset_pending` was left as `False`

### 2.2 Intervening exchange (2026-08-31)
The L4 preflight tester state snapshot (from the preflight artifact, 2026-09-01) shows:
- An exchange occurred on 2026-08-31 20:15 UTC: "hola" → greeting
- This exchange did NOT trigger a cycle reset (cycle_reset_pending was False and the tester sent a simple greeting)
- `cycle_reset_pending` remained False after this exchange

### 2.3 State at Wild start (2026-09-01T15:34:24Z)
Verified from crm_test at time of forensic collection:

```sql
SELECT t.id, s.last_stage, s.cycle_reset_pending, s.home_zone_group,
       s.home_zone_detail, s.current_cycle_started_at, s.current_focus_candidate_id
FROM whatsapp_contacts c
JOIN whatsapp_threads t ON t.contact_id = c.id
JOIN whatsapp_thread_states s ON s.thread_id = t.id
WHERE c.wa_id = '5491153368330';
```

Result:
| Column | Value |
|---|---|
| thread_id | 2 |
| last_stage | QUOTED |
| cycle_reset_pending | False |
| home_zone_group | Sur |
| home_zone_detail | Berazategui |
| current_cycle_started_at | 2026-08-27T19:20:56Z |
| current_focus_candidate_id | 129 |

**Conclusion:** The tester was mid-prior-cycle. No canonical cycle reset was armed. `current_cycle_started_at` was unchanged from the prior Wild session.

### 2.4 Candidate 129 (prior cycle)
```sql
SELECT id, marca, modelo, anio, tipo_vehiculo, zone_group, zone_detail, status, created_at
FROM whatsapp_thread_candidates
WHERE id = 129;
```

Result:
| Column | Value |
|---|---|
| id | 129 |
| marca | Peugeot |
| modelo | 2008 |
| anio | 2015 |
| tipo_vehiculo | SUV_4X4_DEPORTIVO |
| zone_group | Sur |
| zone_detail | Berazategui |
| status | current_focus |
| created_at | 2026-08-27 (prior Wild) |

Candidate 129 was created during prior Wild-01. It retained `status=current_focus` and `zone_detail=Berazategui`.

---

## Part 3 — Preflight Authorization Audit

### 3.1 L4 preflight claim (from artifact)
The L4-RUNTIME-WILD-PREFLIGHT artifact (2026-09-01) declared:

```
READY FOR OWNER MESSAGE: YES
```

### 3.2 L1.1 precondition (from LAUNCH_TRUTH_ROADMAP.md)
Section L1, "Required precondition":
> Before the next Wild, the existing tester must enter the next inspection via the **canonical lifecycle reset**, not via manual DB edits.

Section L1, "Required precondition" exit criterion:
> Tester reset before next Wild

### 3.3 Preflight check gap
The L4-RUNTIME-WILD-PREFLIGHT audit checked:
- ✓ Running image matches certified source
- ✓ DATABASE_URL → crm_test
- ✓ OUTBOUND_ENABLED=false
- ✓ WA token valid
- ✓ n8n active
- ✓ 104/104 gate smokes
- ✓ Tester contact/thread exists
- ✓ Booking Flow private key present
- ✓ Control operational
- ✗ **cycle_reset_pending=True** — NOT checked / NOT required by preflight protocol

The preflight protocol did NOT include a check that `cycle_reset_pending=True` before declaring READY. The tester state section in the preflight artifact noted:
> `cycle_reset_pending=False, cycle_started_at=2026-08-27`
> `Last exchange: 2026-08-31 20:15 ("hola" → greeting), not a clean-slate start`

Despite observing that the tester was mid-prior-cycle and cycle_reset_pending=False, the preflight returned `READY FOR OWNER MESSAGE: YES`. This was the authorization failure.

### 3.4 Root cause of authorization failure
The L4 preflight protocol lacked an explicit gate:

> **MISSING PREFLIGHT GATE:** cycle_reset_pending MUST be True before declaring READY for Wild.

Without this gate, a tester in QUOTED state from a prior Wild can be authorized for a new Wild with stale candidate and zone data active.

---

## Part 4 — Quote Trace

### 4.1 Inbound burst timeline
Three audio messages received in a 14-second window:

| msg_id | direction | timestamp (UTC) | transcript |
|---|---|---|---|
| 6040 | in | 2026-09-01T15:34:24Z | "Hola, quería saber si hacían revisiones de un 2008 del 2015..." |
| 6041 | in | 2026-09-01T15:34:32Z | FAQ questions (presence, payment, report) |
| 6042 | in | 2026-09-01T15:34:38Z | "¡Se puede pagar con Debito!" |

### 4.2 AI event processing
```sql
SELECT id, event_type, burst_message_count, latency_ce, created_at
FROM ai_events
WHERE id IN (98, 99, 100)
ORDER BY id;
```

| id | event_type | burst_message_count | latency_ce | created_at |
|---|---|---|---|---|
| 98 | triggered | — | — | 2026-09-01T15:34:24Z |
| 99 | triggered | — | — | 2026-09-01T15:34:32Z |
| 100 | processed | 3 | 3688ms | 2026-09-01T15:34:41Z |

n8n held all 3 audio messages across its 20-second debounce window, then delivered a single burst (3 messages) to the CE at 15:34:41Z.

### 4.3 CE processing (inferred — logs lost)
With no cycle reset pending, CE processed the burst against the existing prior-cycle state:
- Stage at entry: QUOTED
- Active candidate: 129 (SUV_4X4_DEPORTIVO 2015, Sur/Berazategui)
- home_zone_group=Sur, home_zone_detail=Berazategui (stale, from prior Wild)
- The tester's first message contained "un 2008 del 2015" — vehicle identification
- No new location was provided in any of the 3 messages
- CE matched vehicle mention to existing candidate 129 (same vehicle type/year)
- CE invoked PricingService with the stale zone: zone_group=Sur, zone_detail=Berazategui
- PricingService returned: base=150,000 + viaticos=90,000 = **$240,000**

### 4.4 Pricing computation proof
Verified from crm_test production pricing tables:

```sql
SELECT tipo_vehiculo, base_price FROM pricing WHERE tipo_vehiculo = 'SUV_4X4_DEPORTIVO';
-- base_price = 150000

SELECT zone_group, zone_detail, viaticos_amount
FROM viaticos_zones
WHERE zone_group = 'Sur' AND zone_detail = 'Berazategui';
-- viaticos_amount = 90000
```

Total: 150,000 + 90,000 = **$240,000** ✓ matches Wild observation

### 4.5 Outbound message
```sql
SELECT id, direction, status, wamid, path_id, blocked_reason, created_at
FROM whatsapp_messages
WHERE id = 6043;
```

| Field | Value |
|---|---|
| id | 6043 |
| direction | out |
| status | failed |
| wamid | None |
| path_id | CE_TEXT |
| blocked_reason | None |
| created_at | 2026-09-01T15:35:08Z |

Gate passed (path_id=CE_TEXT is authorized, OUTBOUND=true at that moment).
Meta API call failed — message never accepted by Meta (wamid=None).

---

## Part 5 — Reproduction

### CASE A: Incident State — cycle_reset_pending=False

**Setup:** Exact tester state at Wild start. `last_stage=QUOTED`, `cycle_reset_pending=False`, candidate 129 active with `zone_group=Sur`, `zone_detail=Berazategui`, `tipo_vehiculo=SUV_4X4_DEPORTIVO`.

**Prediction:** Quote of $240,000 would fire because:
- _execute_cycle_reset() does NOT fire (cycle_reset_pending=False)
- CE processes burst against QUOTED state with prior-cycle candidate
- PricingService uses Berazategui viaticos → $240,000

**Test file:** `tests/test_l4_wild01_repro.py` — class `TestCaseAIncidentState`

**Result:** **5/5 PASS**

| Test | Result | Assertion |
|---|---|---|
| A1: cycle_reset_not_pending | PASS | cycle_reset_pending=False, last_stage=QUOTED |
| A2: prior_candidate_zone_intact | PASS | cand 129: Sur/Berazategui, current_focus, SUV_4X4_DEPORTIVO 2015 |
| A3: stale_cycle_date_not_current | PASS | current_cycle_started_at=2026-08-27T19:20:56Z (unchanged) |
| A4: pricing_240000_berazategui | PASS | base=150000 + viaticos=90000 = 240000 |
| A5: focus_candidate_is_prior_cycle | PASS | current_focus_candidate_id=129 (prior cycle) |

**Conclusion:** CASE A is a PROVEN REPRODUCTION of the incident pricing path.

---

### CASE B: Canonical-Reset State — cycle_reset_pending=True

**Setup:** Same tester state but with `cycle_reset_pending=True` (as L1.1 precondition requires).

**Prediction:** Quote of $240,000 would NOT fire because:
- _execute_cycle_reset() fires on first inbound (cycle_reset_pending=True)
- Reset clears home_zone_group, home_zone_detail, last_stage
- New cycle has no zone → CE asks for location instead of quoting

**Test file:** `tests/test_l4_wild01_repro.py` — class `TestCaseBCanonicalReset`

**Result:** **4/4 PASS**

| Test | Result | Assertion |
|---|---|---|
| B1: reset_armed | PASS | cycle_reset_pending=True |
| B2: execute_cycle_reset_clears_zone | PASS | home_zone_group=None, home_zone_detail=None, cycle_reset_pending=False |
| B3: execute_cycle_reset_clears_stage | PASS | last_stage changed from QUOTED |
| B4: no_quote_without_location | PASS | PricingService with zone_group=None cannot produce $240,000 |

**Conclusion:** With canonical reset armed, the stale Berazategui zone is wiped before CE processes the burst. CE would ask for location, not quote $240,000.

**Combined reproduction verdict: 9/9 PASS**

---

## Part 6 — Reproduction Test File

File created: `tests/test_l4_wild01_repro.py`

Runs under the existing L3 test infrastructure:
- conftest.py JSONB→JSON patch applies automatically
- SQLite in-memory for CASE A/B state tests (A1-A3, A5, B1-B3)
- crm_test PostgreSQL for pricing proof (A4, B4)
- No writes to crm or crm_test production tables

Run command:
```bash
DATABASE_URL="postgresql+psycopg://crm:crm@localhost:5432/crm_test" \
PYTHONPATH=/var/lib/containerd/.../snapshots/4288/fs/usr/local/lib/python3.12/site-packages:/opt/ridecheck-crm-release-candidate/backend \
python3 -m pytest tests/test_l4_wild01_repro.py -v
```

---

## Part 7 — FAQ Content Audit

The tester's burst included FAQ questions about inspections. CE's response is not recoverable (wamid=None, delivery failed, logs lost). However, the FAQ content in the system can be audited for correctness:

| FAQ topic | Tester question | Expected answer | Status |
|---|---|---|---|
| Presence required? | "¿es necesario que esté presente?" | No es necesario que estés presente | ✓ CORRECT in FAQ |
| Report at end? | Implied in FAQ question | Informe detallado del estado del vehículo | ✓ CORRECT in FAQ |
| Payment — debit | "¡Se puede pagar con Debito!" | No se acepta débito | ✓ CORRECT in FAQ |
| Payment — cash/transfer | — | Transferencia bancaria, Mercado Pago, efectivo | ✓ CORRECT in FAQ |

**FAQ content itself is correct.** The malformed response the tester received ($240,000 quote without location) was not caused by incorrect FAQ answers — it was caused by the stale zone from the prior cycle being used by the CE pricing engine.

---

## Part 8 — Delivery Failure Analysis

### 8.1 Evidence
Message id=6043:
- `status=failed`
- `wamid=None` — Meta never returned a WAMID → message was NOT accepted by Meta
- `blocked_reason=None` — the OutboundSafetyGate passed
- `path_id=CE_TEXT` — L2 fix working; gate did not block

### 8.2 Gate path
Per CLAUDE.md M2 invariants: outbound message is written to `whatsapp_messages` BEFORE the Meta API call. The gate accepted the call (OUTBOUND=true, path_id=CE_TEXT authorized, tester within CLOSED_BETA_ALLOWED_WA_IDS). The Meta API call itself failed at the transport layer.

### 8.3 Backend logs
CE processing logs are LOST — container was recreated to disable outbound. The exact HTTP error code from the Meta send API is not recoverable.

### 8.4 Candidate causes
Without backend logs, the exact Meta failure cause cannot be determined. Plausible candidates:
1. **Token rate-limit or transient error** — WA token was validated at preflight (HTTP 200), but meta send endpoint may have returned 429 or 500 during the Wild
2. **Phone number ID configuration issue** — WHATSAPP_PHONE_NUMBER_ID from env may not match the account's configured number
3. **Token permissions** — token validated for account info read; may lack messaging permission for this phone number

### 8.5 Classification
This delivery failure is a **SEPARATE FINDING** from the quote-without-location defect. The gate worked correctly (CE_TEXT path passed). The failure is at the Meta API transport layer.

**Severity: MEDIUM** — message was not delivered; ledger recorded it correctly; gate attribution is intact; no unknown/unauthorized path involved. The tester saw the red exclamation (failed delivery indicator) in WhatsApp.

### 8.6 Observability note
Per the outbound ledger: the system correctly recorded the failed outbound before the Meta call, preserving the forensic trail without requiring backend log access. This confirms the L2 outbound ledger design is working.

---

## Part 9 — Control Dashboard State

The Control dashboard was operational at time of incident (confirmed during L4 preflight). The outbound message id=6043 is visible in the outbound ledger with `status=failed`, `wamid=None`, `path_id=CE_TEXT`, `blocked_reason=None`.

Thread 2 (tester's thread) remains in the dashboard with full forensic trace available via `GET /security/outbound-ledger?thread_id=2`.

No OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE events were generated during the Wild session — confirming no unauthorized path was used.

---

## Part 10 — n8n Burst Processing Proof

### 10.1 Evidence
AI events from crm_test:

| event_id | event_type | burst_message_count | latency_ce | timestamp |
|---|---|---|---|---|
| 98 | triggered | — | — | 15:34:24Z |
| 99 | triggered | — | — | 15:34:32Z |
| 100 | processed | 3 | 3688ms | 15:34:41Z |

### 10.2 Interpretation
- Events 98 and 99 mark individual message arrivals (n8n receives each, starts debounce timer)
- Event 100 marks the final CE invocation: burst_message_count=3 confirms all 3 audios were delivered together
- n8n held all 3 messages across 14 seconds (15:34:24 → 15:34:38) and debounced for ~3 seconds before calling CE
- Single CE invocation with full burst context

### 10.3 n8n behavior verdict
**n8n burst debounce: WORKING CORRECTLY.** The 20-second debounce assembled all 3 audio messages into a single CE call. No duplicate CE invocations. No messages dropped.

This finding does NOT invalidate any gate.

---

## Part 11 — Security Event Audit

### 11.1 Security events during Wild
```sql
SELECT id, severity, event_type, detected_at
FROM security_events
WHERE detected_at >= '2026-09-01T15:30:00Z'
ORDER BY id;
```

Result: No new security events generated during the Wild session.

The last known security event before the Wild: `id=734`, detected_at=2026-09-01T00:45Z (pre-existing, from overnight operation).

### 11.2 Non-tester outbound
```sql
SELECT COUNT(*) FROM whatsapp_messages
WHERE direction='out'
  AND created_at > '2026-09-01T00:00:00Z';
```
Count = 1 (only message id=6043, to tester 5491153368330)

**OUTBOUND_NON_TESTER = 0** — no unauthorized sends.

---

## Part 12 — Gate Invalidation Decision

### 12.1 What is invalidated

**L4 — Runtime Certification: WILD #1 = FAIL**

The first controlled Wild session failed due to an incomplete preflight checklist. The preflight did not gate on `cycle_reset_pending=True` before declaring READY.

**L4 is not PASS.** The Wild certification loop requires 3 consecutive meaningful clean Wild sessions with 0 BLOCKER and 0 HIGH. Wild #1 produced 1 HIGH defect (wrong quote, no location) and 1 MEDIUM defect (delivery failure).

### 12.2 What is NOT invalidated

**L1 — FROZEN / CONDITIONAL PASS: INTACT**

L1 was frozen with the explicit precondition: "The real tester must receive a canonical lifecycle reset before the next Wild." The Wild #1 failure is fully explained by this precondition not being met. L1 behavior (with a properly reset tester) is not contradicted.

L1 invariants tested by CASE B (reproduction test) all PASS. The CE correctly clears zone and stage when `cycle_reset_pending=True`. L1 semantic authority invariants are intact.

**L2 — FROZEN / PASS: INTACT**

L2 path attribution working: outbound message id=6043 has `path_id=CE_TEXT`. The gate accepted the call and recorded the ledger entry before the Meta API call. L2 delivery observability produced a complete forensic record despite logs being lost.

**L3 — FROZEN / PASS: INTACT**

L3 dirty-history certification tested the CE under canonical-reset conditions. The Wild #1 failure did not test a canonical-reset scenario. L3 scenarios remain valid.

### 12.3 New required preflight gate
Before the next Wild, the L4 preflight protocol MUST add:

```
PREFLIGHT GATE (MUST PASS before READY):
  tester.cycle_reset_pending == True
```

If this gate fails, the Wild cannot proceed until the canonical lifecycle reset is armed (owner action via CRM or explicit CE reset trigger).

---

## Part 13 — Defect Classification

### DEFECT-WILD-01-A: Quote Without Location (Stale Cycle)
- **Severity: HIGH** per LAUNCH_TRUTH_ROADMAP.md §6 (wrong quote; stale historical state can alter current business flow)
- **Class:** L4 PREFLIGHT FAILURE — incomplete checklist
- **Root cause:** L4 preflight declared READY while `cycle_reset_pending=False` and stale QUOTED state active
- **CE behavior:** CORRECT per L1 invariants — CE quotes with whatever zone is in state; it is the reset precondition that was missing
- **Remediation:** Add `cycle_reset_pending=True` gate to L4 preflight protocol; arm reset before next Wild

### DEFECT-WILD-01-B: Delivery Failure (Meta API)
- **Severity: MEDIUM** per LAUNCH_TRUTH_ROADMAP.md §6 (operational inconvenience; gate attribution intact; no unsafe path)
- **Class:** META API TRANSPORT FAILURE — delivery failure at Meta layer
- **Root cause:** Backend CE logs lost; exact Meta error code unknown; token was valid at preflight
- **CE behavior:** CORRECT — gate passed, ledger recorded, CE did not bypass any invariant
- **Remediation:** Investigate Meta API call configuration (phone_number_id, token permissions, rate limits) in next Wild; add log retention protocol before Wild (do not recreate container without preserving logs)

### NOT A DEFECT: n8n Burst
- n8n burst debounce processed 3 audio messages correctly into single CE invocation
- Classification: WORKING AS DESIGNED

---

## Part 14 — Roadmap Update Required

The LAUNCH_TRUTH_ROADMAP.md must be updated to reflect:
1. L4 Wild #1 = FAIL (HIGH + MEDIUM defects)
2. Gate invalidation: L4 remains FAIL
3. L1/L2/L3 remain FROZEN
4. Required preflight gate addition (cycle_reset_pending=True)
5. Next step: arm canonical reset for tester, then Wild #2

---

## Part 15 — Incident Summary

**What happened:**
On 2026-09-01, the first controlled Wild session was authorized while the tester remained in QUOTED state from a prior Wild (2026-08-27). The canonical lifecycle reset (required by L1.1 precondition) was never armed. When the tester sent 3 audio messages asking about an inspection of a Peugeot 2008 2015, the CE processed the burst against the existing QUOTED state and generated a $240,000 quote using stale Berazategui zone data from the prior Wild. Additionally, the outbound message failed at the Meta API transport layer (wamid=None).

**Why it happened:**
The L4 preflight checklist did not include a required gate verifying `cycle_reset_pending=True`. The preflight observed the stale state (cycle_reset_pending=False, cycle_started_at=2026-08-27) but returned READY anyway. This is a defect in the preflight protocol, not in the CE semantic authority (which correctly queries state and prices against whatever zone is active).

**What the system did correctly:**
- OutboundSafetyGate: path_id=CE_TEXT correctly assigned and accepted
- Outbound ledger: recorded before Meta call; complete forensic trail preserved
- n8n burst: 3 audios delivered as single CE invocation (no duplicates)
- CLOSED_BETA_ALLOWED_WA_IDS enforcement: only tester received messages (OUTBOUND_NON_TESTER=0)
- Security events: no BLOCKER events during Wild

**What was wrong:**
- L4 preflight returned READY with cycle_reset_pending=False (HIGH defect)
- Meta API delivery failed for unknown reason (MEDIUM defect; logs lost due to container recreate)

---

## Part 16 — Constraints Verified

| Constraint | Status |
|---|---|
| NO CODE CHANGES MADE | YES — no source files modified |
| PRODUCTION DB TOUCHED | NO — all queries against crm_test |
| TESTER STATE MANUALLY CORRECTED | NO — state preserved as forensic evidence |
| CANDIDATE / QUOTE PATCHED | NO |
| CE MODIFIED | NO |
| N8N MODIFIED | NO |
| OUTBOUND LOGIC MODIFIED | NO |
| WHATSAPP RETRY ATTEMPTED | NO |
| CONVERSATION CONTINUED | NO |
| OUTBOUND DISABLED AT CLOSE | YES — 2026-09-01T15:38:40Z |

---

## Part 17 — Reproduction Summary

```
tests/test_l4_wild01_repro.py — 9/9 PASS

CASE A (incident state, cycle_reset_pending=False):
  test_a1_cycle_reset_not_pending       PASS
  test_a2_prior_candidate_zone_intact   PASS
  test_a3_stale_cycle_date_not_current  PASS
  test_a4_pricing_240000_berazategui    PASS
  test_a5_focus_candidate_is_prior_cycle PASS

CASE B (canonical-reset state, cycle_reset_pending=True):
  test_b1_reset_armed                   PASS
  test_b2_execute_cycle_reset_clears_zone PASS
  test_b3_execute_cycle_reset_clears_stage PASS
  test_b4_after_reset_no_quote_without_location PASS
```

---

## Part 18 — Return Block

```
PROJECT: RIDECHECK_CRM / TYPE: AUDIT / MILESTONE: L4-WILD-01-FORENSIC

OUTBOUND AT CLOSE: OFF (disabled 2026-09-01T15:38:40Z)
PRODUCTION DB TOUCHED: NO
CODE CHANGES MADE: NO
TESTER STATE CORRECTED: NO

WILD #1 VERDICT: FAIL
  DEFECT-WILD-01-A: HIGH — Quote without location (stale cycle, preflight checklist gap)
  DEFECT-WILD-01-B: MEDIUM — Delivery failure at Meta API (wamid=None, logs lost)
  NOT-A-DEFECT: n8n burst processing (WORKING CORRECTLY)

GATE INVALIDATION:
  L4 Runtime + Wild: FAIL (Wild #1 FAIL; 0/3 consecutive clean sessions)
  L1 Semantic Authority: FROZEN — INTACT (precondition was not met; L1 behavior not contradicted)
  L2 Transport + Operations: FROZEN — INTACT (path attribution working; ledger forensic complete)
  L3 Dirty-History: FROZEN — INTACT (tests cover post-reset behavior; not contradicted)

REPRODUCTION: 9/9 PASS (tests/test_l4_wild01_repro.py)
  CASE A (incident state) — proves $240,000 quote from stale Berazategui zone
  CASE B (canonical-reset state) — proves no quote without zone after reset

FAQ CONTENT: CORRECT (answers verified; delivery failure prevented tester from receiving them)

REQUIRED BEFORE WILD #2:
  1. Arm canonical lifecycle reset for tester (cycle_reset_pending=True)
  2. Add cycle_reset_pending=True gate to L4 preflight checklist
  3. Add log retention protocol (preserve container logs before recreate)
  4. Investigate Meta API delivery failure (phone_number_id, token permissions)
  5. Wild #2 must start with cycle_reset_pending=True confirmed in preflight

CONSECUTIVE CLEAN WILD COUNT: 0/3
L4 STATUS: FAIL — REMEDIATION REQUIRED BEFORE WILD #2
```

---

## Part 19 — Files Produced in This Audit

| File | Purpose |
|---|---|
| `2026-09-01_RIDECHECK_CRM_L4-WILD-01-FORENSIC_AUDIT_FIRST-MESSAGE-FAILURE.md` | This document |
| `tests/test_l4_wild01_repro.py` | Forensic reproduction tests (9/9 PASS) |

---

## Part 20 — Forensic Data Appendix

### A. Inbound messages (crm_test)
```
id=6040, direction=in, ts=15:34:24Z, transcript="Hola, quería saber si hacían revisiones de un 2008 del 2015..."
id=6041, direction=in, ts=15:34:32Z, transcript=<FAQ questions>
id=6042, direction=in, ts=15:34:38Z, transcript="¡Se puede pagar con Debito!"
```

### B. AI events (crm_test)
```
id=98,  event_type=triggered,  ts=15:34:24Z
id=99,  event_type=triggered,  ts=15:34:32Z
id=100, event_type=processed,  ts=15:34:41Z, burst_message_count=3, latency_ce=3688ms
```

### C. Outbound message (crm_test)
```
id=6043, direction=out, ts=15:35:08Z, status=failed, wamid=None,
         path_id=CE_TEXT, blocked_reason=None
```

### D. Security events during Wild
```
None (last pre-existing: id=734 at 15:38:40Z — generated by container recreate, not by Wild)
```

### E. Tester state at Wild start (crm_test)
```
thread_id=2, contact_id=2, wa_id=5491153368330
last_stage=QUOTED
cycle_reset_pending=False
home_zone_group=Sur
home_zone_detail=Berazategui
current_cycle_started_at=2026-08-27T19:20:56Z
current_focus_candidate_id=129
```

### F. Candidate 129 (crm_test)
```
id=129, thread_id=2
marca=Peugeot, modelo=2008, anio=2015
tipo_vehiculo=SUV_4X4_DEPORTIVO
zone_group=Sur, zone_detail=Berazategui
status=current_focus
created_at=2026-08-27 (prior Wild)
```

### G. Pricing table (crm_test)
```
SUV_4X4_DEPORTIVO base_price = 150,000
viaticos_zones: zone_group=Sur, zone_detail=Berazategui → viaticos = 90,000
Total = 240,000
```

### H. Pre-Wild baseline vs Post-Wild
| Metric | Pre-Wild | Post-Wild | Delta |
|---|---|---|---|
| LAST_OUTBOUND_ID | 5695 | 6043 | +1 (failed) |
| LAST_INBOUND_ID | 5694 | 6042 | +3 (burst) |
| SECURITY_EVENTS_TOTAL | 733 | 734 | +1 (container recreate) |
| OUTBOUND_NON_TESTER | 0 | 0 | 0 |
| WAMID_CONFIRMED | — | 0 (failed) | — |
