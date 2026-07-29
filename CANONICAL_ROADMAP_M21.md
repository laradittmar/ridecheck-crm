# Canonical Roadmap — M21
**As of:** 2026-07-29 ART (updated by M21.1.0-B)
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

n8n owns: inbound webhook transport, lead creation and thread linking, 20-second debounce, audio transcription (Whisper), image description, context assembly.
CE owns: service interpretation, conversation routing, candidate management, zone state, pricing eligibility, scheduling, CRM mutations, human escalation, outbound decisions.

n8n AI fallback (AI Router → Updater → Planner): dead code, never fires, pending retirement (Architecture Closeout).

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
- Controlled test email: sent and delivered to `ridecheckassistance@gmail.com` (confirmed by Lara)
- Reply-To: confirmed correct by Lara (Reply in Gmail addresses `ridecheckassistance@gmail.com`)
- Report: `/opt/ridecheck-crm/forensics/EMAIL_2_controlled_deployment_20260728.md`

---

## M21.1 — Semantic Conversation Engine ← IN PROGRESS (APPROVED)

**Dependency:** M21.0.1 + M21.0.2 complete ✓
**Audit:** `/opt/ridecheck-crm/forensics/M21_1_0_semantic_engine_implementation_audit_20260729.md` (Sections S + T)
**Implementation prompt:** `/opt/ridecheck-crm/forensics/M21_1_1_IMPLEMENTATION_PROMPT.md` (M21.1.0-B revision)

### Approved business rules

| Rule | Scope | Behavior |
|---|---|---|
| BR-1 | Pre-purchase intent | Positive evidence required; UNCERTAIN → one clarification question; no commercial processing until intent confirmed |
| BR-2 | Formulario 12 | Deterministic boundary: "Nosotros realizamos revisiones precompra; no gestionamos el Formulario 12." |
| BR-3 | Transfer/paperwork | Deterministic boundary response |
| BR-4 | Repairs/mechanical | Deterministic boundary; genuinely ambiguous → one clarification question |
| BR-5 | Motorcycle/scooter/quad/ATV/UTV | **Highest priority** → route to RideCheck manual-handling team. Set needs_human=True. Send warm handoff. No automated commercial processing. |
| BR-6 | Disassembled car/SUV | Inspectability explanation; no quote; not applicable to assembled non-running |
| BR-7 | Assembled non-running | Clarification or escalation — NOT automatic decline |

**Note on BR-5 wording:** Motorcycle, scooter, quad, ATV, and UTV enquiries belong to a new, low-frequency business unit and are handled manually by the RideCheck team for now. The automated assistant must ensure the lead is linked, mark needs_human, trigger the internal review mechanism, send one warm handoff reply, and stop all automated commercial processing. CE does not state that RideCheck cannot serve these enquiries.

### Sub-milestones (implementation order)

| ID | Feature | Scope | Tests | Status |
|---|---|---|---|---|
| M21.1.1 | Service Intent, Motorcycle Handoff & Unsupported-Service Gate | BR-1–BR-5; gate before lookup_vehicle/zone/pricing; all motorcycle entry points; handled=true under kill switch | SI-01–SI-28 | NOT STARTED |
| M21.1.2 | Vehicle Inspectability Constraint | BR-6 (desarmado), BR-7 (non-running clarification) | SC05 + BR-7 variant | NOT STARTED |
| M21.1.3 | Location Semantic Roles & Candidate Persistence | Remove stale zone guard; zone → candidate; vehicle_location vs customer_origin | SC11–SC14, SC17 | NOT STARTED |
| M21.1.4 | ASR Vehicle Normalization | `fuzzy_lookup_vehicle()` difflib; gap_threshold=0.15; make constraint | SC07–SC09 (corrected), SC18 | NOT STARTED |
| M21.1.5 | Central Field-Evidence Resolver | `_resolve_field_evidence()` prevents redundant questions | SC15–SC17 updated | NOT STARTED |
| M21.1.6 | Long Voice & Narrative Understanding | AI prompt: exhaustive extraction in one pass; builds on M21.1.5 evidence model | SC10, SC15, SC16 | NOT STARTED |
| M21.1.7 | M21.1 Consolidated Regression Pack | SI-01–SI-28 + SC01–SC20; M21.0.2 baseline 424 still green | all | NOT STARTED |

### Key contract corrections (M21.1.0-B, 2026-07-29)

- **Gate placement:** Service intent gate inserts before `lookup_vehicle`, `_extract_zone_from_text`, `_compute_price_quote`, and `_routing_gate` — not after them as the previous prompt incorrectly stated
- **Evidence source:** Gate uses current-turn `ai_input_messages` (current burst), not `all_recent_text` (which includes historical turns)
- **BR-1 in scope:** M21.1.1 implements UNCERTAIN → clarification; positive-intent requirement is part of M21.1.1, not deferred
- **Motorcycle wording:** New business unit, manual handling — not "RideCheck cannot inspect motorcycles"
- **Motorcycle entry points:** Gate covers text, audio-transcript, image-text, website-form, Vehicle Flow response, AI-extracted MOTO, and QUOTED/SCHEDULING stages via centralized helper
- **handled=true under kill switch:** New action `"human_handoff_blocked"` added to HANDLED_ACTIONS ensures n8n legacy fallback cannot run after motorcycle handoff under kill switch
- **Implementation order:** M21.1.5 (Field Evidence Resolver) precedes M21.1.6 (Long Voice) — evidence model must exist before AI prompt enhancement uses it
- **Contextual classification:** Simple substring matching is insufficient; implementation must handle false positives for repair/transfer/F12 context patterns

---

## M21.2 — Wild Conversation 2 Retry

**Dependency:** M21.1 complete

Retry Wild Conversation 2 with M21.1 fixes active. Must pass both Case A (Formulario 12 / moto boundary) and Case B (ASR vehicle normalization + location semantic roles).

---

## M21.3 — Scheduling UX

**Dependency:** M21.2 (scheduling path validated in live conversation)

| ID | Deliverable |
|---|---|
| M21.3.1 | WhatsApp List Messages for available slot selection |
| M21.3.2 | Interactive booking via List Message selection |
| M21.3.3 | Free-text scheduling fallback when List Message is unavailable |
| M21.3.4 | Slot revalidation before booking confirmation |
| M21.3.5 | Configurable scheduling horizon |
| M21.3.6 | Unavailable-day and unavailable-week messaging |
| M21.3.7 | Calendar improvements (holiday handling, capacity blocks) |

---

## M21.4 — CRM Enrichment & Attribution

| ID | Deliverable |
|---|---|
| M21.4.1 | Automatic channel detection (WhatsApp, web form, Instagram future) |
| M21.4.2 | Source and source-detail attribution |
| M21.4.3 | Campaign mapping |
| M21.4.4 | Client / partner / dealer / referral code mapping |
| M21.4.5 | First-touch attribution |
| M21.4.6 | Latest-touch attribution |
| M21.4.7 | Lead enrichment (vehicle history, coverage zone pre-check) |
| M21.4.8 | CRM visibility, filters, exports and reporting |

Instagram remains a future channel and is not a launch dependency.

---

## M21.5 — Commercial Polish

Conversation quality improvements based on real conversation replay findings:

- Natural Argentine WhatsApp tone throughout
- Objection handling (price concern, competitor comparison, scheduling friction)
- Natural transitions between stages (qualification → quote → scheduling)
- No unnecessary repetition or re-quoting of already-confirmed information
- FAQ and reassurance polish (what does the inspection cover, how long, where)
- Demo/video-quality conversations using real backend behavior (no scripted data)

---

## M21.6 — Full Wild Validation

Full live session with complete M21 feature set active. Must demonstrate end-to-end conversation quality on real, unscripted customer enquiries. Go/no-go gate for M22.

---

## Technical Backlog

Valid technical items not scheduled as standalone milestones. To be incorporated into the appropriate M21 sub-milestone or scheduled separately:

| Item | Candidate milestone |
|---|---|
| `GET /api/zones/infer-group?zone_detail=X` endpoint (eliminate zone-group duplication in n8n dead-code path) | M21.1.3 or M21.0.x closeout |
| Multi-vehicle thread: track and switch between candidates within a single thread | M21.1.3 (candidate persistence work) |

---

## Architecture Closeout

| ID | Task | Status |
|---|---|---|
| AC-1 | Retire n8n AI fallback (AI Router, Candidate/State Updater, AI Reply Planner, false-branch CRM nodes) | PENDING — pending observability confirmation from M21.0.1 |
| AC-2 | End-to-end integration test: n8n webhook → CE path (M21.0.3) | PENDING |
| AC-3 | Burst-context array verification: confirm n8n sends correct recent_user_messages under real debounce (M21.0.4) | PENDING |
| AC-4 | Processor observability: structured logging for CE decision points | PENDING |

---

## M20 Closeout

| Task | Status |
|---|---|
| Evidence pack: complete conversation trace for M20 closed-beta session | PENDING |
| Production/test isolation verification: confirm crm vs crm_test separation in all deployment modes | PENDING |
| Production release plan: go/no-go criteria, rollback procedure, monitoring checklist | PENDING |

---

*Updated 2026-07-29 ART by M21.1.0-B — corrected gate contract, motorcycle wording, implementation order, and full M21.3–M21.6 scope*
*Previous roadmaps superseded — do not use `CANONICAL_ROADMAP_M20_M21.md` or `PROPOSED_*` files*
