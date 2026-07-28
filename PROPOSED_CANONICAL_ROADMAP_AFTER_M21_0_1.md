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

## M21.0.2 — Green Regression Baseline ✓ COMPLETE

- Fixed import-order `Base.metadata.create_all` bug in 4 test files
- Fixed `sys.modules["app.db"]` stub teardown bug in isolation test (test_4, test_5)
- Fixed date-sensitive calendar test (uses `date.today()` instead of hardcoded May 30)
- Full suite: 424 passed, 0 failed, 62 skipped — deterministic in all collection orders
- No runtime code changed. No n8n workflow modified. No deployment.

---

## M21.1 — Semantic Conversation Engine

**Goal:** CE handles the full range of real customer language without failing silently.
**Dependency:** M21.0.1 and M21.0.2 complete; full regression suite green.

### M21.1.1 — Service Classification Gate

**Problem (Wild 2, Case A):** "Formulario 12 de una moto acá en Tigre, que está desarmada"
→ CE offered a quote instead of explaining the service boundary.

**Fix in CE (`conversation_engine.py`):**
- Pre-AI deterministic check: detect "Formulario 12", "formulario", "transferencia",
  "tramitación", "reparación", "desarmado/a" before the AI call
- If out-of-scope service detected: reply with service boundary message, do not enter quote flow
- Answer from documented policy; escalate when policy is absent

### M21.1.2 — Disassembled Vehicle Constraint

**Problem (Wild 2, Case A):** "una moto acá en Tigre, que está desarmada"
→ CE must not quote an inspection that cannot be performed.

**Fix in CE:**
- Detect "desarmado/a", "desmontado/a" before entering quote flow
- Reply that inspection requires the vehicle to be assembled and inspectable
- Do not quote an impossible inspection

### M21.1.3 — ASR Vehicle Name Normalization

**Problem (Wild 2, Case B):** Whisper transcribed "Ford Ka SEL" as "Ford KSL"
→ `lookup_vehicle()` returned None → no quote.

**Fix — two layers:**
1. **n8n (Transcribe Audio node):** Seed the Whisper `prompt` parameter with the
   top vehicle names from the catalog so ASR produces recognizable strings
2. **CE (`lookup_vehicle()`):** Post-transcription fuzzy match — if exact lookup fails,
   run Levenshtein distance or phonetic match against catalog aliases;
   if best match confidence ≥ 0.80, treat as catalog hit; below threshold, confirm with customer
   before using — no fabricated vehicle model

### M21.1.4 — Long Voice Structured Understanding

**Problem:** Customers send 20–30 second voice messages with multiple pieces of
information (vehicle, vehicle type, year, location, service intent, constraints).
CE currently misses fields already present and asks redundant clarification questions.

**Fix in CE:**
- When audio text is long (> 150 chars), extract all structured fields in one pass:
  vehicle, vehicle type, year, inspection location, service intent, logistical constraints
- Do not ask clarification for any field already extracted from the same message

### M21.1.5 — Location Semantic Roles

**Problem (Wild 2):** "Soy de La Plata, el auto está en Villa Urquiza"
→ CE used the customer's origin (La Plata) instead of the vehicle location (Villa Urquiza).

**Fix in CE:**
- Distinguish `customer_origin`, `vehicle_location`, `inspection_location`
- Always use `vehicle_location` / `inspection_location` for zone and pricing
- "Soy de La Plata, el auto está en Villa Urquiza" → Villa Urquiza selected

### M21.1.6 — Zone and Candidate Persistence

**Problem (Wild 2, Case B):** New vehicle location in same thread overwrote the
previous zone on the wrong candidate.

**Fix in CE:**
- Newly extracted vehicle location must update the correct (current_focus) candidate
- Stale location from an earlier enquiry must not override the current vehicle
- Pricing must use the current candidate's location

### M21.1.7 — Redundant-Question Elimination

**Problem:** CE asks for vehicle or zone information already present in the conversation.

**Fix in CE — before sending any clarification, check:**
1. Current message
2. Unanswered recent user messages
3. `recent_user_messages` (full burst)
4. Current candidate fields
5. Thread state (home_zone_group, home_zone_detail)
- Only ask when information is genuinely missing or contradictory

---

## M21.2 — Wild Conversation 2 Retry

**Goal:** Demonstrate M21.1 fixes on real-world audio inputs.
**Dependency:** M21.1.1 through M21.1.7 complete; full regression suite green.

**Acceptance scenarios:**
1. Formulario 12 + moto + desarmada → service boundary reply, no quote
2. Ford Ka SEL audio + Villa Urquiza → ASR normalized, quote sent
3. Customer from La Plata, vehicle in Villa Urquiza → Villa Urquiza selected for pricing
4. Long audio with all required quote information → no redundant clarification
5. No redundant vehicle/location question when already answered
6. Deterministic PricingService quote for correct zone
7. Clean CRM state after each scenario

---

## M21.3 — Scheduling UX

### M21.3.1 — WhatsApp List Message for Slot Selection

Replace the text slot list ("09:00, 09:30, 10:00...") with a WhatsApp interactive
List Message. The customer taps a slot; the selection arrives as structured data.

### M21.3.2 — Free-Text Scheduling Fallback

Keep `_parse_scheduling_text()` as a fallback for customers who type a time instead
of selecting from the list.

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

Limited to genuine commercial polish only. Semantic correctness (location roles,
zone persistence) has been moved to M21.1 where it belongs.

| ID | Deliverable | Notes |
|---|---|---|
| M21.5.1 | MOTO pricing | Add MOTO_PEQUEÑA ($80,000) to pricing_base.csv or remove MOTO cc-range from CE AI prompt |
| M21.5.2 | Response tone | Tightening of objection handling, concise transitions, FAQ wording |
| M21.5.3 | Quote follow-up | Automated reminder if quote not accepted within 24h |
| M21.5.4 | Follow-up experience | Video-quality copy for post-booking messages |

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
