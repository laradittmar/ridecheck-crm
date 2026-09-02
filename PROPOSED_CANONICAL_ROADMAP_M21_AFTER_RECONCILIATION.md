# Proposed Canonical Roadmap — M21 After Architecture Reconciliation

**As of:** 2026-07-28 ART  
**Status:** PROPOSAL — not committed  
**Baseline image:** `ridecheck-crm-backend:m20.6d5.2-093074a`  
**Audit reference:** `/opt/ridecheck-crm/forensics/M21_0_0_live_conversation_architecture_reconciliation_20260728.md`

> **Supersedes:** `CANONICAL_ROADMAP_M20_M21.md` (that file contained the wrong architecture)

---

## Architecture Fact (Correction)

The live message processor is **conversation_engine.py (CE)**, called by n8n via POST `http://backend:8000/api/conversation/handle`. The previous roadmap stated "the live message processor is n8n's AI pipeline" — this was wrong.

**Canonical architecture (Option D):**

```
n8n transport tier          CE engine tier            n8n AI fallback
─────────────────────       ──────────────────        ───────────────────
Webhook                  →  conversation_engine.py →  (dead code — never fires)
  audio → Whisper             all business logic
  image → GPT-4o vision       all CRM writes
  debounce (20s)              all outbound (Meta API)
  context aggregation
  lead find/create
  → POST /api/conversation/handle
```

`CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED` MUST remain `false` in production. If enabled, audio transcription, debounce, context aggregation, and lead creation are all lost.

---

## M20 Status: CLOSED-BETA VALIDATED

### Delivered

- Deterministic pricing + zone normalization (M20.0–M20.4)
- Kill switch (OUTBOUND_ENABLED) + beta allowlist
- Vehicle catalog (311 entries: AUTO + SUV_4X4_DEPORTIVO)
- Location Flow + Vehicle Flow integration
- Quote acceptance guard (ACEPTADO state machine)
- Booking Flow dispatch + revision creation
- Copy polish (approved quote/booking copy)
- M20.6D.5.2: scheduling state machine fixes (single-digit minutes, re-quote suppression, AI time capture) — 86/86 tests pass, **confirmed live in CE**

### Wild Conversations Completed

| Session | Result | Notes |
|---|---|---|
| Wild 1 / Retry 1 | FAIL | "11:3" not parsed (pre-M20.6D.5.2); re-quote bug |
| Wild 1 / Retry 2 | PASS | All scheduling fixes confirmed live in CE; booking created (Baic BJ30, La Plata, 11:30) |
| Wild 2 | PARTIAL | Audio received, transcribed; Case A (Formulario 12 misclassification in CE); Case B (ASR "Ka SEL"→"KSL" → vehicle catalog miss in CE) |

### M20 Architecture Decision (corrected)

The live message processor is CE. The M20.6D.5.2 scheduling fixes (single-digit minutes, re-quote suppression, AI time capture) are confirmed live — they executed in Wild Conversation 1 Retry #2. "11:3" → 11:30 was handled by CE's `_parse_scheduling_text()`, not by n8n AI.

---

## M21 — Architecture Formalization + Product Fixes

### M21.0 — Architecture Formalization (before next wild session)

Priority: **Required before any further product work**

| ID | Deliverable | Type | Owner |
|---|---|---|---|
| M21.0.1 | Processor observability: pass n8n_execution_id to CE; log CE action/handled in n8n output | n8n code node + CE logging | n8n + backend |
| M21.0.2 | Remove n8n AI fallback dead code (AI Router, Updater, Planner + false-branch CRM nodes) | n8n workflow edit | n8n |
| M21.0.3 | End-to-end integration test for n8n→CE path; write kill switch test (test_m20_2_kill_switch_proof.py is 0 bytes) | test | backend |
| M21.0.4 | Verify unanswered_recent_user_messages populated correctly in burst scenario | observability + test | backend |
| M21.0.5 | Update CLAUDE.md to state canonical architecture (Option D) and why CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED stays false | docs | — |

### M21.1 — Product Fixes from Wild Conversation 2 (CE changes)

> Note: these were labeled as "n8n prompt changes" in the old roadmap. They are **CE code changes** since CE is the live processor.

| ID | Deliverable | Type | Root cause addressed |
|---|---|---|---|
| M21.1.1 | Service classification gate in CE: detect "Formulario 12", "reparación", "transferencia", "desarmado/a" before AI call; reply with service boundary message | CE code | Case A (Wild 2) |
| M21.1.2 | "Disassembled vehicle" constraint: if "desarmada/o" detected, reply that inspection requires assembled vehicle | CE code | Case A (Wild 2) |
| M21.1.3 | Whisper prompt seeding with vehicle catalog names | n8n (Transcribe Audio node) | Case B (Wild 2) partial |
| M21.1.4 | Post-transcription fuzzy vehicle name normalization in CE: Levenshtein/phonetic match against catalog after transcription | CE code | Case B (Wild 2) |
| M21.1.5 | ASR confirmation step: if tipo_vehiculo extracted from audio with low confidence (<0.8), ask "¿Es un [model]?" before quoting | CE code | Case B (Wild 2) |

### M21.2 — Zone Infrastructure

| ID | Deliverable | Notes |
|---|---|---|
| M21.2.1 | `GET /api/zones/infer-group?zone_detail=X` endpoint | Eliminates duplicate zone logic in n8n Build Effective Quote Input JS (dead code today, but remove before it confuses future work) |
| M21.2.2 | Two-location semantic role assignment in CE | Distinguish `vehicle_location` (inspection zone) vs `customer_origin` in `_extract_zone_from_text` |
| M21.2.3 | Zone persistence across turns | Case B Wild 2: Villa Urquiza not persisted to thread state after Case A set Tigre |

### M21.3 — Interactive Scheduling

| ID | Deliverable | Notes |
|---|---|---|
| M21.3.1 | WhatsApp List Message for slot selection | Replace text slot list with interactive List Message; selection returns as structured data to CE |
| M21.3.2 | Time parsing simplification | With List Message, CE's `_parse_scheduling_text()` is only needed for free-text fallback |

---

## M22 — Advanced Features (Post-Validation)

| ID | Feature | Notes |
|---|---|---|
| M22.1 | Multi-vehicle thread handling | Track and switch between multiple vehicle candidates in one conversation |
| M22.2 | CRM attribution improvements | Higher-confidence vehicle + zone attribution from structured inputs |
| M22.3 | Quote follow-up automation | Automated reminder if quote not accepted within 24h |
| M22.4 | Post-service flow | NPS / review request after revision completed |

---

## Open Decisions (corrected)

| Decision | Options | Recommended | Reason |
|---|---|---|---|
| MOTO pricing | Add MOTO_PEQUEÑA ($80,000) to pricing_base.csv | Remove cc-range from CE AI prompt | Remove reference — simpler, no new pricing row |
| Slot selection UX | Keep text list (CE `_parse_scheduling_text`) | List Message API | Eliminates all time-parsing fragility |
| n8n AI fallback | Keep (safety net) | Remove (dead code + maintenance risk) | Never fires; creates confusion about live architecture |

---

## Constraints Going Forward

- **Do not enable `CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED=true` in production** — breaks audio, debounce, lead creation
- **All product fixes are CE code changes** — n8n is transport only (exception: Whisper prompt seeding is n8n)
- **Before next wild session: complete M21.0.1–M21.0.3** — observability and kill switch test must exist before more live traffic

---

*Written 2026-07-28 ART — proposal only — not committed*  
*Architecture basis: M21.0.0 audit, `/opt/ridecheck-crm/forensics/M21_0_0_live_conversation_architecture_reconciliation_20260728.md`*
