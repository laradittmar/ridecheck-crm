# Ridecheck CRM — Canonical Roadmap M20 → M21

**As of:** 2026-07-27  
**Baseline image:** `ridecheck-crm-backend:m20.6d5.2-093074a`  
**Audit reference:** `/opt/ridecheck-crm/forensics/M20_6D5_3A_conversation_architecture_audit_20260727.md`

---

## M20 Status: CLOSED-BETA VALIDATED

### Delivered
- Deterministic pricing + zone normalization (M20.0–M20.4)
- Kill switch (OUTBOUND_ENABLED) + beta allowlist
- Vehicle catalog (311 entries: AUTO + SUV_4X4_DEPORTIVO)
- Location Flow + Vehicle Flow integration
- Quote acceptance guard (ACEPTADO stage machine)
- Booking Flow dispatch + revision creation
- Copy polish (approved quote/booking copy)
- M20.6D.5.2: 3 scheduling state machine fixes in conversation_engine.py (single-digit minutes, re-quote suppression, AI time capture) — 86/86 tests pass

### Wild Conversations Completed
- **Wild 1 / Retry 1**: FAIL — "11:3" not parsed, re-quote text sent
- **Wild 1 / Retry 2**: PASS — booking Flow sent correctly, "11:3" → 11:30 ✓
- **Wild 2**: PARTIAL — audio messages received; Case A (Formulario 12) misclassified; Case B ("Ford KSL" ASR error) no quote sent

### M20 Architecture Decision (from audit)

The live message processor is **n8n's AI pipeline** (AI Router → Candidate/State Updater → AI Reply Planner), NOT `conversation_engine.py`. `CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED` is false.

`conversation_engine.py` is unit-tested (86/86 pass) and available via `/api/conversation/handle` if the flag is enabled. Current recommendation: freeze at M20.6D.5.2 commit until an M21 architecture decision is made.

---

## M21 — Conversation Intelligence Upgrade

### M21.0 — Pre-M21 Hotfixes (n8n prompt changes, no build required)

Priority: **Before next wild conversation session**

| ID | Fix | Type |
|---|---|---|
| M21.0.1 | Add Formulario 12 / trámite / transferencia service-boundary rule to AI Router prompt | n8n prompt |
| M21.0.2 | Add disassembled vehicle constraint ("desarmada/o" → cannot inspect) to AI Router + Updater prompts | n8n prompt |
| M21.0.3 | Resolve MOTO pricing inconsistency: add MOTO_PEQUEÑA ($80,000) to pricing_base.csv OR remove cc-range text from AI Router prompt | backend data |

### M21.1 — ASR Robustness

| ID | Deliverable | Notes |
|---|---|---|
| M21.1.1 | Whisper prompt seeding with vehicle catalog names | Pass top-N vehicle names as Whisper `prompt` parameter in Transcribe Audio node |
| M21.1.2 | Post-transcription fuzzy vehicle name normalization | After transcription, run Levenshtein/phonetic match against catalog; if confidence ≥ 0.8 auto-correct |
| M21.1.3 | Audio vehicle confirmation step | If tipo_vehiculo extracted from audio but confidence < 0.8, send "¿Es un [model]?" confirmation before quote |

### M21.2 — Service Classification Gate

| ID | Deliverable | Notes |
|---|---|---|
| M21.2.1 | Pre-classification keyword screen | Deterministic: detect Formulario 12, repair, paperwork, disassembled before AI Router |
| M21.2.2 | AI Router service_type output field | Add `"service_type": "INSPECCION_PRE_COMPRA | FORMULARIO_12 | REPARACION | INFO | OTRO"` to AI Router JSON output |
| M21.2.3 | Service boundary reply rules | If service_type ≠ INSPECCION_PRE_COMPRA: send appropriate redirect message, do not enter quote flow |

### M21.3 — Zone Infrastructure

| ID | Deliverable | Notes |
|---|---|---|
| M21.3.1 | `GET /api/zones/infer-group?zone_detail=X` endpoint | Eliminates duplicate zone_detail→zone_group logic in n8n JS |
| M21.3.2 | Replace hardcoded JS zone lookup in Build Effective Quote Input | Call new endpoint instead |
| M21.3.3 | Two-location semantic role assignment | Updater prompt: distinguish `vehicle_location` (inspection zone) vs `customer_origin` |

### M21.4 — Interactive Scheduling

| ID | Deliverable | Notes |
|---|---|---|
| M21.4.1 | WhatsApp List Message for slot selection | Replace text slot list with interactive List Message; selection comes back as structured data |
| M21.4.2 | Time parsing elimination | With List Message, no need to parse "11:3", "20ha", etc. |

### M21.5 — AI Pipeline Optimization

| ID | Deliverable | Notes |
|---|---|---|
| M21.5.1 | Architecture decision: n8n canonical vs. conversation_engine.py enabled | Pick one live path; document the other as archived or test-only |
| M21.5.2 | Combine Updater + Planner (optional) | Single structured-output AI call replacing 2 of 3 calls; reduces latency by ~33–50% |

---

## M22 — Advanced Features (Post-Validation)

| ID | Feature | Notes |
|---|---|---|
| M22.1 | Multi-vehicle thread handling | Correctly track and switch between multiple vehicle candidates in one conversation |
| M22.2 | CRM attribution improvements | Higher-confidence vehicle + zone attribution from structured inputs |
| M22.3 | Quote follow-up automation | Automated reminder if quote not accepted within 24h |
| M22.4 | Post-service flow | NPS / review request after revision completed |

---

## Open Decisions

| Decision | Options | Recommended |
|---|---|---|
| Live message processor | (A) Enable conversation_engine.py (`CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED=true`) | (B) Declare n8n AI pipeline canonical; freeze conversation_engine.py | B — n8n pipeline is live and working; conversation_engine.py adds maintenance overhead |
| MOTO pricing | Add MOTO_PEQUEÑA ($80,000) to pricing_base.csv | Remove cc-range from AI Router prompt | Remove reference — simpler, no new pricing row |
| Slot selection UX | Keep text list | List Message API | List Message — eliminates all time-parsing fragility |

---

*Written 2026-07-27 ART — not committed*
