PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L3-DIRTY-HISTORY
STATUS: FROZEN / PASS
DATE: 2026-09-01
COMMIT: 8b182d5
OUTBOUND: OFF
PRODUCTION DB TOUCHED: NO
L1 CONTRADICTED: NO
LAUNCH_RELEVANT_DEFECT: 0
UNKNOWN: 0

---

# L3 — Dirty-History Certification

## Executive Summary

**PASS.** 50/50 dirty-history scenarios pass. All BLOCKER/HIGH invariants confirmed.
No contradictory evidence against L1 found. Full regression: 63 failures all
pre-existing B/C, 2974 passed, 62 skipped. L3 is frozen. L4 is next.

---

## Safety Constraints

- OUTBOUND OFF throughout.
- Production DB not touched. crm_test only (SQLite in-memory for tests).
- No WhatsApp messages sent. No Meta sends. No n8n changes.
- No secrets printed. No API key values in any output.
- No production runtime mutation.

---

## Test Harness

**Method:** SQLite in-memory, ConversationEngine.__new__ (no I/O), service-level
method calls via genuine ORM context, PYTHONPATH pointed at container
site-packages for SQLAlchemy 2.x.

**Dirty history fixture (_seed_dirty_thread):**
- Prior-cycle candidate: `created_at = 2026-07-01T10:00:00Z` (before watermark)
- Cycle watermark: `current_cycle_started_at = 2026-08-15T10:00:00Z`
- New-cycle candidate: `created_at = 2026-08-31T10:00:00Z` (after watermark)
- Conflicting data: old candidate has wrong zone/year/vehicle; new candidate has correct values

**Realism verification:**
- 3 realism audit tests confirm: fixture seeds 2 candidates, old predates watermark,
  watermark correctly excludes old candidate from `_load_active_candidates`.

---

## Scenario Results — 50/50 PASS

### PART 3: Vehicle / Year (L3-01 to L3-05)

| Test | Scenario | Category | Result |
|------|----------|----------|--------|
| L3-01a | Prior 2008/2020 → new 2008/2015: _focus_candidate returns 2015 | BLOCKER | PASS |
| L3-01b | Old candidate (anio=2020) filtered by watermark | BLOCKER | PASS |
| L3-01c FINAL | _build_quote_reply contains "2015", not "2020" | BLOCKER | PASS |
| L3-02a | Old Corolla → new Taos: focus is Taos | BLOCKER | PASS |
| L3-02b | Taos does not inherit Corolla's CABA/Palermo zone | BLOCKER | PASS |
| L3-03a | Year correction 2022→2021 via _apply_candidate update | HIGH | PASS |
| L3-03b | _extract_year_from_text parses "2021" from correction text | HIGH | PASS |
| L3-04 | Same-cycle switch-back (Taos→Corolla): no duplicate created | HIGH | PASS |
| L3-05 | Ambiguous focus (2 mentioned, no current_focus): returns None | HIGH | PASS |

### PART 4: Location / Zone (L3-06 to L3-10)

| Test | Scenario | Category | Result |
|------|----------|----------|--------|
| L3-06a | Old CABA/Palermo history, new candidate zone=None: location returns (None, None) | BLOCKER | PASS |
| L3-06b FINAL | Quote absent (zone missing) | BLOCKER | PASS |
| L3-07a | "el auto está en Berazategui" → candidate zone_group=Sur, zone_detail=Berazategui | BLOCKER | PASS |
| L3-07b FINAL | Pricing uses Sur/Berazategui (viaticos=6000), not old CABA | BLOCKER | PASS |
| L3-08 FINAL | Correction Berazategui→Villa Urquiza: candidate zone=CABA; quote viaticos=0 | HIGH | PASS |
| L3-09 | LR-3 zone_protected blocks AI overwrite of Sur zone | BLOCKER | PASS |
| L3-10 | Prior Norte/Nordelta not leaked into new candidate with no zone | BLOCKER | PASS |

### PART 5: Quote / Acceptance (L3-11 to L3-14)

| Test | Scenario | Category | Result |
|------|----------|----------|--------|
| L3-11 FINAL | Old Revision quote does not satisfy new cycle (missing zone) | BLOCKER | PASS |
| L3-12 FINAL | Quote recomputed from Berazategui (Sur), not old CABA; reply contains "Berazategui", not "Palermo" | BLOCKER | PASS |
| L3-13 | Old SUV → new AUTO: pricing uses AUTO base (140000) not SUV (150000) | HIGH | PASS |
| L3-14a | _is_acceptance(["de acuerdo"]) = True | HIGH | PASS |
| L3-14b | After cycle reset, last_stage=None | BLOCKER | PASS |
| L3-14c | Acceptance gate requires QUOTED stage (source inspection) | BLOCKER | PASS |

### PART 6: Scheduling (L3-15 to L3-18)

| Test | Scenario | Category | Result |
|------|----------|----------|--------|
| L3-15 FINAL | Prior 13:00 cleared by reset; new AI says Thursday only: preferred_day=Thu, preferred_time=None | BLOCKER | PASS |
| L3-16 | AI says Thursday 11:00: preferred_day=Thu, preferred_time="11:00" | HIGH | PASS |
| L3-17 | Friday then correction→Saturday: Saturday wins | HIGH | PASS |
| L3-18 | Scheduling location comes from current Sur candidate, not old CABA state | BLOCKER | PASS |

### PART 7: Active-cycle / Reset (L3-19 to L3-22)

| Test | Scenario | Category | Result |
|------|----------|----------|--------|
| L3-19 | Completed inspection → cycle reset: all ACTIVE_REVISION fields cleared; old candidate archived; old Revision preserved | BLOCKER | PASS |
| L3-20 | Abandoned/quoted cycle reset: home_zone, preferred_day, preferred_time, last_stage, current_focus, revision_id all None | BLOCKER | PASS |
| L3-21 | Stale current_focus_candidate_id (prior-cycle): cleared by _focus_candidate; new candidate returned | BLOCKER | PASS |
| L3-22 | 3 prior + 1 active: watermark isolates exactly 1 active candidate | BLOCKER | PASS |

### PART 8: Burst / Voice (L3-23 to L3-26)

| Test | Scenario | Category | Result |
|------|----------|----------|--------|
| L3-23a | _extract_year_from_text finds year in voice transcript | HIGH | PASS |
| L3-23b | "el auto está en Berazategui" in voice burst → Sur/Berazategui written | BLOCKER | PASS |
| L3-23c FINAL | Quote uses Sur/Berazategui; reply contains "2015"+"Berazategui", not "2020"+"Palermo" | BLOCKER | PASS |
| L3-24 | Burst correction 2020→2019: year 2019 wins | HIGH | PASS |
| L3-25 FINAL | Two-burst Palermo→Quilmes: Quilmes/Sur wins | HIGH | PASS |
| L3-26 | Burst vehicle A→B: B is current_focus; A preserved as mentioned | HIGH | PASS |

### PART 9: Name / Third-party (L3-27 to L3-28)

| Test | Scenario | Category | Result |
|------|----------|----------|--------|
| L3-27a | "Fernando Lopez" established; AI proposes "Martín" (seller): customer_name unchanged | BLOCKER | PASS |
| L3-27b | ThreadRevision.seller_name exists as separate field | HIGH | PASS |
| L3-28 | AI update without ID; ambiguous focus: no silent mutation of arbitrary candidate | BLOCKER | PASS |

### PART 10: Dedup / Unanswered (L3-29 to L3-32)

| Test | Scenario | Category | Result |
|------|----------|----------|--------|
| L3-29 | WhatsAppOutboundDedup has causal_inbound_wa_message_id field | HIGH | PASS |
| L3-30 | content_fingerprint present in dedup model for retry blocking | HIGH | PASS |
| L3-31 | Unanswered SQL excludes "blocked" from direction check | HIGH | PASS |
| L3-32 | Unanswered SQL excludes "failed" from direction check | HIGH | PASS |

### PART 11: Booking (L3-33 to L3-35)

| Test | Scenario | Category | Result |
|------|----------|----------|--------|
| L3-33 | Booking location = current Sur/Quilmes, not old Norte/San Isidro | BLOCKER | PASS |
| L3-34 | New ThreadRevision links current candidate; old booking preserved in DB | BLOCKER | PASS |
| L3-35a | ThreadRevision.appointment_approval_token is unique | HIGH | PASS |
| L3-35b | Duplicate token → DB constraint violation | HIGH | PASS |

### Realism Audit

| Test | Scenario | Result |
|------|----------|--------|
| Realism-01 | _seed_dirty_thread creates exactly 2 candidates | PASS |
| Realism-02 | Old candidate created_at < cycle watermark | PASS |
| Realism-03 | _load_active_candidates excludes old candidate | PASS |

---

## Pricing Traces

Representative traces asserted in test results:

**L3-07b: AUTO in Berazategui (Sur)**
- tipo_vehiculo: AUTO
- zone_group: Sur
- zone_detail: Berazategui
- precio_base: 140000
- viaticos: 6000
- precio_total: 146000

**L3-12: AUTO in Berazategui (new cycle, prior cycle had CABA)**
- tipo_vehiculo: AUTO
- zone_group: Sur
- zone_detail: Berazategui
- precio_base: 140000
- viaticos: 6000
- precio_total: 146000 (not old CABA 140000)

**L3-13: AUTO vs old SUV**
- tipo_vehiculo: AUTO
- precio_base: 140000 (not SUV 150000)

---

## Scheduling Traces

**L3-15: Thursday only (prior 13:00 cleared)**
- preferred_day: "2026-09-10"
- preferred_time: None (not "13:00" from prior cycle)

**L3-16: Thursday + 11:00**
- preferred_day: "2026-09-10"
- preferred_time: "11:00"

---

## Invariant Verification Summary

| Invariant | Source | Status |
|-----------|--------|--------|
| RISK-01: customer_name first-write-wins | L3-27a | CONFIRMED |
| RISK-02: scheduling fill-if-absent; clear after reset | L3-15, L3-19 | CONFIRMED |
| RISK-03: no stale zone at candidate creation | L3-02b, L3-06a | CONFIRMED |
| RISK-04: stale scheduling time not inherited | L3-15 | CONFIRMED |
| RISK-05: ambiguous AI update skipped | L3-28 | CONFIRMED |
| CL-05: stale focus ID cleared | L3-21 | CONFIRMED |
| CL-07: zone_protected blocks AI zone | L3-09 | CONFIRMED |
| Cycle watermark exclusion | L3-01b, L3-22 | CONFIRMED |
| Acceptance gate requires QUOTED stage | L3-14b/c | CONFIRMED |
| Booking links current candidate | L3-34 | CONFIRMED |

---

## L1 Contradictory Evidence Rule

**No contradiction found.**

All L3 scenarios confirm L1 invariants. L3-21 (stale focus cleared) and L3-09
(zone_protected) directly retest L1 assertions in a dirty-history context and
produce the same deterministic result. L1 remains frozen.

---

## Full Regression

```
L3 test suite:        50/50 PASS  (test_l3_dirty_history.py)
L1 gate:              19/19 PASS  (test_l1_semantic_authority.py)
L2.1 gate:            15/15 PASS  (test_l2_1_email_alerts.py)
L2-transport gate:    20/20 PASS  (test_l2_transport_path_integrity.py)
Full suite:           2974 passed, 63 failed (pre-existing B/C), 62 skipped
LAUNCH_RELEVANT_DEFECT: 0
UNKNOWN: 0
```

Known collection errors (excluded from run, pre-existing):
- test_b5_intent_detection.py (DATABASE_URL guard fires at module load)
- test_m21_3_flow_endpoint_303.py (app/static directory not present on host)

---

## What Was NOT Changed

- Conversation Engine behavior
- Pricing / scheduling rules
- Booking Flow
- Outbound safety gate
- Semantic authority hierarchy
- WhatsApp send logic
- n8n workflow activation
- Production DB or runtime
- OUTBOUND_ENABLED (remains false)
- Any previously frozen gate (L1, L2, L2.1)

---

## Runtime Image

No new runtime image built for L3 (zero production code changes).
Current image: `ridecheck-crm-backend:l2.1-email-3131f88` (unchanged).
docker-compose.beta.yml: unchanged.

---

## Next Gate

**L4 — Runtime Certification + Controlled Wild**

L3 FROZEN. L4 requires:
- correct image confirmed in crm_test
- n8n runtime path operational (live activation state proven)
- Booking Flow endpoint operational
- Control dashboard operational
- Owner authorizes outbound (BETA_OUTBOUND_ENABLED=true)
- At least one canonical Wild conversation end-to-end
