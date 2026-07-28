# Proposed Canonical Roadmap — After M21.0.1

**As of:** 2026-07-28 ART  
**Status:** PROPOSAL — not committed, not approved  
**Baseline:** `ridecheck-crm-backend:m20.6d5.2-093074a`  
**Architecture basis:** M21.0.0 audit (CE is the live processor, Option D canonical)

---

## M21.0.1 — Live Path Contract & Kill Switch Proof ✓ COMPLETE

- Sanitized n8n payload fixtures (`tests/fixtures/m21/`)
- RC41–RC44: n8n → CE contract tests (`test_m21_0_1_live_path_contract.py`)
- RC45–RC48: kill switch proof (`test_m20_2_kill_switch_proof.py`)
- `CLAUDE.md`: canonical architecture documented
- No runtime code changed. No n8n workflow modified. No deployment.

---

## M21.1 — Semantic Conversation Engine

**Goal:** CE handles the full range of real customer language without failing silently.

### M21.1.1 — Service Classification Gate

**Problem (Wild 2, Case A):** "Formulario 12 de una moto acá en Tigre, que está desarmada"
→ CE offered a quote instead of explaining the service boundary.

**Fix in CE (`conversation_engine.py`):**
- Pre-AI deterministic check: detect "Formulario 12", "formulario", "transferencia",
  "tramitación", "reparación", "desarmado/a" before the AI call
- If out-of-scope service detected: reply with service boundary message, do not enter quote flow
- If vehicle is disassembled ("desarmada/o", "desmontada/o"): reply that inspection
  requires an assembled, driveable vehicle

### M21.1.2 — ASR Vehicle Name Normalization

**Problem (Wild 2, Case B):** Whisper transcribed "Ford Ka SEL" as "Ford KSL"
→ `lookup_vehicle()` returned None → no quote.

**Fix — two layers:**
1. **n8n (Transcribe Audio node):** Seed the Whisper `prompt` parameter with the
   top vehicle names from the catalog so ASR produces recognizable strings
2. **CE (`lookup_vehicle()`):** Post-transcription fuzzy match — if exact lookup fails,
   run Levenshtein distance or phonetic match against catalog aliases;
   if best match confidence ≥ 0.80, treat as catalog hit

### M21.1.3 — Long Voice Message Understanding

**Problem:** Customers send 20–30 second voice messages with multiple pieces of
information (vehicle, zone, service description, availability constraints).
CE currently uses only `unanswered_recent_user_messages` from n8n context.

**Fix in CE:**
- When audio text is long (> 150 chars), extract all structured fields in one pass:
  vehicle, zone, service type, urgency, constraints
- Avoid asking clarification questions that are already answered in the audio

### M21.1.4 — Redundant-Question Elimination

**Problem:** CE sometimes asks for zone or vehicle information that was already
mentioned in the current or a recent message.

**Fix in CE:**
- Before sending any clarification, check `recent_user_messages` for the requested field
- If already present: extract and use it rather than asking again

---

## M21.2 — Wild Conversation 2 Retry

**Goal:** Demonstrate the M21.1 fixes working on real-world audio inputs.

- Reset crm_test to clean state
- Replay Case A scenario (Formulario 12 + moto + desarmada): verify service boundary reply
- Replay Case B scenario (Ford Ka SEL audio): verify quote sent after ASR normalization
- New fresh scenario: two-location disambiguation (customer origin vs vehicle location)
- Full shutdown and evidence pack

---

## M21.3 — Scheduling UX

### M21.3.1 — WhatsApp List Message for Slot Selection

Replace the text slot list ("09:00, 09:30, 10:00...") with a WhatsApp interactive
List Message. The customer taps a slot; the selection arrives as structured data.

**Implementation:** CE `_handle_day_only_request()` sends a List Message instead of text.
n8n routes List Message responses to CE same as any structured response.

### M21.3.2 — Free-Text Scheduling Fallback

Keep `_parse_scheduling_text()` as a fallback for customers who type a time instead
of selecting from the list. The single-digit minute normalization ("11:3" → 11:30)
from M20.6D.5.2 remains active.

### M21.3.3 — Calendar Improvements

- Configurable scheduling horizon (currently hardcoded)
- Slot availability exposed per mechanic, not just per day
- Explicit "no availability this week" message when all slots full

---

## M21.4 — CRM Enrichment

| ID | Deliverable | Notes |
|---|---|---|
| M21.4.1 | Channel tracking | Record how the lead arrived (WhatsApp direct, QR, wa.me link, website) |
| M21.4.2 | Source attribution | UTM-style source/medium on lead creation |
| M21.4.3 | Campaign code mapping | Map lead source to campaign codes for reporting |
| M21.4.4 | Lead quality signals | Track conversational signals (urgency, budget mentioned, repeat contact) |

---

## M21.5 — Commercial Polish

| ID | Deliverable | Notes |
|---|---|---|
| M21.5.1 | MOTO pricing | Add MOTO_PEQUEÑA ($80,000) to pricing_base.csv or remove MOTO cc-range from CE AI prompt |
| M21.5.2 | Zone role assignment | "Yo soy de La Plata, el auto está en Villa Urquiza" → correct zone extraction |
| M21.5.3 | Zone persistence | Case B Wild 2: new zone in same thread must persist even when prior zone exists |
| M21.5.4 | Quote follow-up | Automated reminder if quote not accepted within 24h |

---

## M21.6 — Full Wild Conversation Validation

- Fresh tester session with real buyer intent
- Cover: audio, text burst, zone ambiguity, vehicle from audio, full booking flow
- Full shutdown + evidence pack
- Pass/fail criteria defined before activation

---

## M20 Closeout (after M21.6)

- Beta evidence pack (Wild 1 Retry #2 PASS + Wild 2 PARTIAL + Wild 3 target PASS)
- Production/test isolation audit
- `crm` (production) DB migration plan
- Release plan and go-live checklist

---

## What this roadmap does NOT include

- Removal of n8n AI fallback dead code: deferred — not a prerequisite for M21.1
- `CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED=true`: not recommended; n8n transport is essential
- Observability fields (n8n_execution_id in CE payload): deferred to a future milestone

---

*Written 2026-07-28 ART — proposal only — not committed — requires Lara's approval before implementation*
