# Canonical Roadmap — M21
**As of:** 2026-07-29 ART  
**Status:** IN PROGRESS  
**Approved by:** Lara Dittmar  
**Baseline image:** `ridecheck-crm-backend:email.1-afec998`  
**Baseline commit:** `afec998e81f5b727fd7b80b0903a74d7f4271ee8`  
**Regression baseline:** 424 passed, 0 failed, 62 skipped  
**Supersedes:** `CANONICAL_ROADMAP_M21_APPROVAL_CANDIDATE.md`, `PROPOSED_CANONICAL_ROADMAP_M21_AFTER_RECONCILIATION.md`, `CANONICAL_ROADMAP_M20_M21.md`

---

## Architecture (Authoritative)

```
WhatsApp → n8n transport tier → POST /api/conversation/handle → conversation_engine.py
```

n8n owns: audio transcription (Whisper), image description, 20-second debounce, context aggregation, lead find/create/link.  
CE owns: all conversation routing, candidate management, pricing, scheduling, CRM mutations, outbound.  
n8n AI fallback (AI Router → Updater → Planner): dead code, never fires, pending removal (M21.0.2).

`CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED` MUST remain `false` — n8n transport services are essential.

Authoritative reference: `/opt/ridecheck-crm/forensics/M21_0_0_live_conversation_architecture_reconciliation_20260728.md`

---

## M21.0 — Architecture Formalization ✓ COMPLETE

### M21.0.1 — Live Path Contract & Kill Switch Proof ✓ COMPLETE
- Commit: `1e6acd2`
- 44 tests (RC41–RC48): n8n→CE contract, kill switch, state rollback
- `CLAUDE.md`: canonical Option D architecture documented
- Report: `/opt/ridecheck-crm/forensics/M21_0_1_live_path_contract_kill_switch_20260728.md`

### M21.0.2 — Green Regression Baseline ✓ COMPLETE
- Commit: `bd22cd1`
- **424 passed, 0 failed, 62 skipped** — deterministic in all collection orders
- Fixed: import-order `Base.metadata.create_all` bug in 4 test files
- Fixed: `sys.modules["app.db"]` stub teardown in `test_m19_f2_3_isolation.py`
- Fixed: date-sensitive appointment test in `test_m12_3_calendar_nav.py`
- Report: `/opt/ridecheck-crm/forensics/M21_0_2_green_regression_baseline_20260728.md`

### EMAIL.1 — Booking Notification Delivery Fix ✓ COMPLETE
- Commit: `afec998`
- Image: `ridecheck-crm-backend:email.1-afec998`
- Fixed: `INTERNAL_BOOKING_EMAIL_TO` default corrected from `julian@ridecheck.ar` to `ridecheckassistance@gmail.com`
- Fixed: `reply_to` added to all three Resend functions
- 27 EMAIL-RC tests: 27/27 passed
- Report: `/opt/ridecheck-crm/forensics/EMAIL_1_booking_notification_delivery_fix_20260728.md`

### EMAIL.2 — Controlled Deployment & Delivery Verification ✓ COMPLETE
- Backend container: `email.1-afec998` deployed
- Controlled test email: sent, Resend accepted
- Gmail delivery: pending Lara confirmation
- Report: `/opt/ridecheck-crm/forensics/EMAIL_2_controlled_deployment_20260728.md`

---

## M21.1 — Semantic Conversation Engine ← IN PROGRESS (APPROVED)

**Dependency:** M21.0.1 + M21.0.2 complete ✓  
**Audit:** `/opt/ridecheck-crm/forensics/M21_1_0_semantic_engine_implementation_audit_20260729.md`  
**Audit corrections:** Section S of the above audit (added 2026-07-29)  
**Implementation prompt:** `/opt/ridecheck-crm/forensics/M21_1_1_IMPLEMENTATION_PROMPT.md`

### Approved business rules

All BD-1 through BD-8 open decisions resolved as BR-1 through BR-7 (approved by Lara, 2026-07-29):

| Rule | Scope | Behavior |
|---|---|---|
| BR-1 | Pre-purchase intent | Positive evidence required; UNCERTAIN → clarify first |
| BR-2 | Formulario 12 | Deterministic boundary: "Nosotros realizamos revisiones precompra; no gestionamos el Formulario 12." |
| BR-3 | Transfer/paperwork | Deterministic boundary response |
| BR-4 | Repairs/mechanical | Deterministic boundary; ambiguous → one clarification question |
| BR-5 | Motorcycle/quad/UTV | **Highest priority** → MOTORCYCLE_HUMAN_REVIEW (needs_human=True + Resend + WA reply). Triggers: moto, motocicleta, scooter, ciclomotor, cuatriciclo, quad, ATV, UTV |
| BR-6 | Disassembled car/SUV | Inspectability explanation; no quote; not applicable to assembled non-running |
| BR-7 | Assembled non-running | Clarification or escalation — NOT automatic decline |

### Sub-milestones (implementation order)

| ID | Feature | Scope | Tests | Status |
|---|---|---|---|---|
| M21.1.1 | Service Intent & Unsupported-Service Gate | BR-1–BR-7 all intents; MOTORCYCLE_HUMAN_REVIEW highest priority | SC01–SC06, SC-M01–SC-M05 | NOT STARTED |
| M21.1.2 | Vehicle Inspectability Constraint | BR-6 (desarmado), BR-7 (non-running clarification) | SC05 + BR-7 variant | NOT STARTED |
| M21.1.3 | Location Semantic Roles & Candidate Persistence | Remove stale zone guard; zone → candidate; vehicle_location vs customer_origin | SC11–SC14, SC17 | NOT STARTED |
| M21.1.4 | ASR Vehicle Normalization | `fuzzy_lookup_vehicle()` difflib; gap_threshold=0.15; make constraint | SC07–SC09 (corrected), SC18 | NOT STARTED |
| M21.1.5 | Long Voice Structured Understanding | AI prompt: exhaustive extraction in one pass | SC10, SC15, SC16 | NOT STARTED |
| M21.1.6 | Central Field-Evidence Resolver | `_resolve_field_evidence()` prevents redundant questions | SC15–SC17 updated | NOT STARTED |
| M21.1.7 | M21.1 Consolidated Regression Pack | SC01–SC20 + SC-M01–SC-M05; M21.0.2 baseline 424 still green | all | NOT STARTED |

### Key corrections from M21.1.0-A (2026-07-29)

- **Service intent enum:** Added `MOTORCYCLE_HUMAN_REVIEW` at priority 0 (highest)
- **SC09 corrected:** Input `"ford ksl"` (not `"Ka"`); ratio vs Ford Ka = 0.800, vs Ford Kuga = 0.706; gap 0.094 < gap_threshold 0.15 → medium confidence confirm; `"Ka"` alone is an exact alias hit and would not test fuzzy logic
- **SC13 vs SC17 clarified:** SC13 = stale thread state from prior turn, fixed by guard removal; SC17 = genuine contradiction within single current message, fixed by role model contradiction detection
- **Motorcycle handoff ordering:** `_send_fallback_human_review_notification` fires before `_send_text_to_wa` so Resend email is always delivered even when WhatsApp kill switch blocks the customer message
- **New tests SC-M01–SC-M05:** Cover moto detection, motorcycle beats F12, kill-switch behavior, skipped_human on subsequent message, cuatriciclo/quad triggers

---

## M21.2 — Wild Conversation 2 Retry

**Dependency:** M21.1 complete

Retry Wild Conversation 2 with M21.1 fixes active. Must pass both Case A (Formulario 12) and Case B (ASR vehicle + location roles).

---

## M21.3 — Scheduling UX

**Dependency:** M21.2 (scheduling path unblocked)

| ID | Deliverable |
|---|---|
| M21.3.1 | WhatsApp List Message for slot selection |
| M21.3.2 | Time-parsing simplification (List Message reduces free-text need) |

---

## M21.4 — CRM Enrichment

| ID | Deliverable |
|---|---|
| M21.4.1 | `GET /api/zones/infer-group?zone_detail=X` endpoint (eliminate duplicate zone logic in n8n dead code) |
| M21.4.2 | Multi-vehicle thread: track and switch between candidates |

---

## M21.5 — Commercial Polish

Copy/UX polish based on real conversation replay findings.

---

## M21.6 — Full Wild Validation

Full live session with complete M21 feature set active. Go/no-go for M22.

---

## M20 Closeout

| Task | Status |
|---|---|
| Remove n8n AI fallback dead code (AI Router, Updater, Planner, false-branch CRM nodes) | PENDING (M21.0.2 was removed from scope pending M21.0.1 observability) |
| End-to-end integration test n8n→CE path | PENDING (M21.0.3) |
| Context array burst verification | PENDING (M21.0.4) |

---

*Written 2026-07-29 ART — M21.1 approved, implementation not started*  
*Previous roadmaps superseded — do not use `CANONICAL_ROADMAP_M20_M21.md` or `PROPOSED_*` files*
