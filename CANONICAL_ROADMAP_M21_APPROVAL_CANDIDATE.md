# Canonical Roadmap M21 — Approval Candidate

**As of:** 2026-07-28 ART  
**Status:** APPROVAL CANDIDATE — not approved — requires Lara's sign-off before implementation  
**Supersedes:** `PROPOSED_CANONICAL_ROADMAP_AFTER_M21_0_1.md` (detail file remains as reference)  
**Baseline:** `ridecheck-crm-backend:m20.6d5.2-093074a`

---

## M21.0 — Architecture Formalization ✓ COMPLETE

### M21.0.1 — Live Path Contract & Kill Switch Proof ✓
- Commit: `1e6acd2`
- 44 tests (RC41–RC48): n8n → CE contract, kill switch, state rollback
- `CLAUDE.md`: canonical three-tier architecture documented
- Report: `forensics/M21_0_1_live_path_contract_kill_switch_20260728.md`

### M21.0.2 — Green Regression Baseline ✓
- Commit: `{M21_0_2_SHA}` ← filled in at commit time
- 424 passed, 0 failed, 62 skipped — deterministic in all collection orders
- Fixed import-order `Base.metadata.create_all` bug in 4 test files
- Fixed `sys.modules["app.db"]` stub teardown in `test_m19_f2_3_isolation.py`
- Fixed date-sensitive appointment test in `test_m12_3_calendar_nav.py`
- Report: `forensics/M21_0_2_green_regression_baseline_20260728.md`

---

## M21.1 — Semantic Conversation Engine

**Dependency:** M21.0.1 + M21.0.2 complete and green.

| Sub-milestone | Feature | Wild 2 scenario it unblocks |
|---|---|---|
| M21.1.1 | Service Classification Gate | Case A: Formulario 12 |
| M21.1.2 | Disassembled Vehicle Constraint | Case A: moto desarmada |
| M21.1.3 | ASR Vehicle Normalization | Case B: Ford Ka SEL → Ford KSL |
| M21.1.4 | Long Voice Structured Understanding | Case B + long audio |
| M21.1.5 | Location Semantic Roles | Case C: La Plata vs Villa Urquiza |
| M21.1.6 | Zone and Candidate Persistence | Case B: zone override |
| M21.1.7 | Redundant-Question Elimination | All cases |

### M21.1.1 — Service Classification Gate
Pre-AI check: detect Formulario 12, paperwork/transfer, repair, unsupported services. Reply from documented policy; escalate when policy is absent.

### M21.1.2 — Disassembled Vehicle Constraint
Detect desarmado/a, desmontado/a. Reply that inspection requires assembled, inspectable vehicle. Do not quote an impossible inspection.

### M21.1.3 — ASR Vehicle Normalization
Two layers:
1. n8n: seed Whisper `prompt` with top catalog vehicle names
2. CE: fuzzy post-transcription match; auto-correct ≥ 0.80 confidence; confirm below threshold; no fabricated model

### M21.1.4 — Long Voice Structured Understanding
Single-pass extraction of: vehicle, vehicle type, year, inspection location, service intent, relevant logistical constraints. No redundant clarification for fields already present.

### M21.1.5 — Location Semantic Roles
Distinguish customer_origin / vehicle_location / inspection_location. "Soy de La Plata, el auto está en Villa Urquiza" → Villa Urquiza used for zone and pricing.

### M21.1.6 — Zone and Candidate Persistence
Newly extracted vehicle location updates the correct (current_focus) candidate. Stale location from a prior enquiry does not override the current vehicle. Pricing uses current candidate's location.

### M21.1.7 — Redundant-Question Elimination
Before sending any clarification, check: current message → unanswered burst → recent_user_messages → current candidate → thread state. Ask only when genuinely missing or contradictory.

---

## M21.2 — Wild Conversation 2 Retry

**Dependency:** M21.1.1–M21.1.7 complete; full regression suite green.

Acceptance scenarios (all must pass):
1. Formulario 12 + moto + desarmada → service boundary reply, no quote
2. Ford Ka SEL audio + Villa Urquiza → ASR normalized, deterministic quote
3. Customer from La Plata, vehicle in Villa Urquiza → Villa Urquiza zone selected
4. Long audio with all required info → no redundant clarification question
5. No redundant vehicle/location question when already answered
6. Deterministic PricingService quote for the correct zone
7. Clean CRM state (crm_test) after each scenario

---

## M21.3 — Scheduling UX

| Sub-milestone | Feature |
|---|---|
| M21.3.1 | WhatsApp List Message for slot selection (replaces text list) |
| M21.3.2 | Free-text scheduling fallback retained |
| M21.3.3 | Calendar improvements: configurable horizon, per-mechanic slots, "no availability" message |

---

## M21.4 — CRM Enrichment

| Sub-milestone | Feature |
|---|---|
| M21.4.1 | Channel tracking (WhatsApp direct, QR, wa.me, website) |
| M21.4.2 | Source attribution (UTM-style) |
| M21.4.3 | Campaign code mapping |
| M21.4.4 | Lead quality signals (urgency, budget, repeat contact) |

---

## M21.5 — Commercial Polish

Limited to genuine commercial polish. Semantic correctness (location roles, zone persistence) is in M21.1.

| Sub-milestone | Feature |
|---|---|
| M21.5.1 | MOTO pricing (MOTO_PEQUEÑA $80,000 or catalog cleanup) |
| M21.5.2 | Response tone, objection handling, concise transitions, FAQ wording |
| M21.5.3 | Quote follow-up reminder (24h) |
| M21.5.4 | Post-booking copy quality |

---

## M21.6 — Full Wild Conversation Validation

- Fresh tester session: audio, text burst, zone ambiguity, vehicle from audio, full booking
- Full shutdown + evidence pack
- Pass/fail criteria defined BEFORE activation

---

## M20 Closeout (after M21.6)

- Beta evidence pack (Wild 1 Retry #2 PASS + Wild 2 target PASS + Wild 3 target PASS)
- Production/test isolation audit
- `crm` (production) DB migration plan
- Release plan and go-live checklist

---

## Explicit exclusions

- n8n AI fallback removal: deferred (not a prerequisite)
- `CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED=true`: never recommended for production
- Observability fields in CE payload: deferred to a future milestone

---

*Approval candidate — 2026-07-28 ART — requires Lara's explicit approval before M21.1 begins*
