# RideCheck CRM — Conversation Runtime Contract

**Status:** AUTHORITATIVE BUSINESS CONTRACT  
**Established by:** WILD-04R Architecture Correction audit (2026-08-24)  
**Read alongside:** `docs/architecture/DOMAIN_MODEL.md`

> **CRITICAL:** If CE code conflicts with this contract, **STOP**. Do not silently update this document to match the code. Report the conflict as an architecture defect requiring owner decision.

Sections use these labels:

- `[CONTRACT]` — Owner-authoritative requirement. Must be true.
- `[CURRENT]` — How the code actually behaves today (2026-08-24).
- `[IMPLEMENTED — WILD-04R]` — Built and tested in crm_test as of 2026-08-24. Not yet run live.
- `[GAP]` — Known delta between contract and current behavior.
- `[PLANNED]` — Agreed implementation approach for the gap. Not yet built.

---

## 1. Active Revision Context

### Persistent thread history vs active semantic context

`[CONTRACT]`

The Thread accumulates all messages, candidates, and revisions from the customer's lifetime with RideCheck. This is **persistent thread history** — it must be preserved and accessible for CRM review, but it must not enter the CE's active semantic context for a new revision cycle.

**Active semantic context** is the subset of thread data that CE reads and reasons over for the current inspection cycle. It consists of:

- Current-cycle inbound and outbound messages (since the cycle boundary)
- Current-cycle vehicle candidates (created after the cycle boundary)
- Persistent customer identity facts: `customer_name`, `nombre`, `apellido`, `email`, `telefono`
- Persistent thread facts: `is_website_lead`, `canal`, `last_processed_inbound_wa_message_id`

**Not in active semantic context:**

- Messages from prior inspection cycles
- Vehicle candidates from prior inspection cycles
- `home_zone_group` / `home_zone_detail` from prior cycles (location is revision-scoped)
- `current_revision_id` pointing to a completed prior booking
- Any `WhatsAppThreadState` ACTIVE_REVISION field that was set during a prior cycle

`[CURRENT]`

CE's `_load_context()` loads **all** candidates unconditionally and the **oldest 20** messages unconditionally. For a thread with prior-cycle history, this means:

- `ctx.candidates` includes vehicles from all cycles (no filter)
- `ctx.db_messages` contains the 20 oldest messages on the thread (e.g., messages from a cycle 2 months ago) — not the 20 most recent

`[GAP]`

No active-cycle boundary exists. CE reads prior-cycle candidates and messages as if they were current context. This was the confirmed root cause of the WILD-04 failure: the AI received the Peugeot 2008 booking sequence as its "conversation history" when the customer asked about a Ford Focus.

`[IMPLEMENTED — WILD-04R Phase 1]`

Two new columns on `whatsapp_thread_states`:
- `current_cycle_start_message_db_id INTEGER NULL` — `WhatsAppMessage.id` of the first inbound message of the current cycle
- `current_cycle_started_at TIMESTAMPTZ NULL` — `WhatsAppMessage.created_at` (DB server clock) of the same message

`_load_context()` filters:
- `db_messages`: `WHERE id >= current_cycle_start_message_db_id` (when set)
- `candidates`: `WHERE created_at >= current_cycle_started_at` (when set)
- Message ordering: `ORDER BY id DESC LIMIT 20` then reversed (most-recent-20 in chronological order)

---

## 2. Cycle Boundary

### Owner-approved lifecycle trigger

`[CONTRACT]`

A new inspection cycle begins when a human operator transitions `Lead.estado` **from any non-CONSULTA_NUEVA state TO CONSULTA_NUEVA** via the CRM UI. This explicit human action sets a one-shot reset signal (`state.cycle_reset_pending = True`) on the linked `WhatsAppThreadState`. CE consumes the signal on the next real customer inbound.

The cycle boundary is the **human action itself** — not an inference from state fields. CE must not auto-detect or auto-trigger a cycle boundary.

**Why explicit signal, not state inference:**

An inference-based predicate such as `lead.estado == CONSULTA_NUEVA AND last_stage IS NOT NULL` would incorrectly fire on every qualifying turn in a first cycle, because `lead.estado` remains `CONSULTA_NUEVA` throughout qualification and `last_stage` becomes `QUALIFYING` on the first turn. This was owner-rejected as WILD-04R design correction.

An inference-based predicate such as `lead.estado == CONSULTA_NUEVA AND current_revision_id IS NOT NULL` covers only booked cycles — it misses quoted-abandoned, scheduling-abandoned, provisional, and ATENCION_HUMANA lifecycle paths where `current_revision_id` is never set.

The explicit `cycle_reset_pending` flag covers all lifecycle paths and fires exactly once.

### The explicit reset signal: `cycle_reset_pending`

`[CONTRACT — IMPLEMENTED — WILD-04R Phase 1]`

New column on `whatsapp_thread_states`:

```sql
cycle_reset_pending  BOOLEAN  NOT NULL  DEFAULT FALSE
```

**Set by:** A centralized CRM helper (`set_lead_estado()` in `backend/app/services/lead_lifecycle.py`) called by every CRM endpoint that writes `Lead.estado`. The helper detects a real transition:

```python
previous_estado = lead.estado          # read BEFORE assignment
lead.estado = new_estado
if (
    new_estado == "CONSULTA_NUEVA"
    and previous_estado is not None    # not a brand-new row
    and previous_estado != "CONSULTA_NUEVA"  # real transition, not a no-op
):
    _set_cycle_reset_signal(db, lead)  # writes state.cycle_reset_pending = True
```

**Set on:** All real human transitions to `CONSULTA_NUEVA`. Covers all lifecycle end-states:
- Booked/completed cycle: `COORDINAR_DISPONIBILIDAD → CONSULTA_NUEVA`
- Quoted/abandoned cycle: human may set directly
- Scheduling/abandoned: same
- ATENCION_HUMANA resolved: `ATENCION_HUMANA → CONSULTA_NUEVA`

**NOT set on:**
- Brand-new Lead creation (estado starts as `CONSULTA_NUEVA` from default — previous is None)
- Setting CONSULTA_NUEVA on an already-CONSULTA_NUEVA lead (previous equals new — no-op)
- CE internal writes (CE only writes `COORDINAR_DISPONIBILIDAD` and `ATENCION_HUMANA`)
- Any elapsed-time or state-inference logic

**Consumed by:** CE `_handle()`, positioned before the `needs_human` guard. When `state.cycle_reset_pending is True`:
1. Capture `previous_cursor = state.last_processed_inbound_wa_message_id` (before overwrite)
2. Determine complete burst from DB: `WHERE id > prev_db_id AND id <= current_event_db_id AND direction='in' ORDER BY id ASC`
3. Set `state.current_cycle_start_message_db_id = first_burst_msg.id`
4. Set `state.current_cycle_started_at = first_burst_msg.created_at`
5. Clear all ACTIVE_REVISION fields on `WhatsAppThreadState` and `Lead`
6. Set `state.cycle_reset_pending = False` (signal consumed)
7. Commit
8. Re-query candidates and messages using new watermarks
9. Continue processing current burst normally

**After consumption:** `cycle_reset_pending = False`. Subsequent turns in the same new cycle never fire the reset again.

**CRM endpoints that must use `set_lead_estado()`:**

| Route | File | Current direct-write line |
|---|---|---|
| `PATCH /leads/{lead_id}` | `api/leads.py:77` | `lead.estado = payload.estado` |
| `POST /ui/lead_update` | `ui/kanban_actions.py:219` | `lead.estado = s` |
| `POST /ui/move` | `ui/kanban_actions.py:241` | `lead.estado = estado` |
| `POST /ui/move_lead` | `ui/kanban_actions.py:288` | `lead.estado = target_estado` |
| `POST /ui/lead/{lead_id}/move` | `ui/kanban_actions.py:302` | `lead.estado = estado` |

**Endpoints that write Lead.estado but do NOT need the helper** (they never write `CONSULTA_NUEVA`):

| Route | Writes to |
|---|---|
| `PATCH /api/revisions/.../appointment-approval` | `AGENDADO` only |
| `POST /public/revisions/.../approve` | `AGENDADO` only |
| CE booking handler (line 1526) | `COORDINAR_DISPONIBILIDAD` only |
| CE flow failure (line 4128) | `ATENCION_HUMANA` only |
| CE scheduling escalation (line 4164) | `ATENCION_HUMANA` only |

**Fields cleared at cycle reset — WhatsAppThreadState:**

```
last_intent, last_stage, needs_human, current_focus_candidate_id,
current_revision_id, home_zone_group, home_zone_detail,
preferred_day, preferred_time, active_requested_date, last_requested_time,
last_offered_slots, last_visible_slots, flow_booking_token,
vehicle_clarification_sent, location_clarification_sent,
vehicle_fallback_flow_sent, location_fallback_flow_sent,
inspectability_clarification_sent, pending_fuzzy_catalog_key,
pending_turn_evidence_text, cycle_reset_pending (→ False)
```

**Fields cleared at cycle reset — Lead:**

```
flag, necesita_humano, motivo_perdida (if set), buscando_auto_set_at
```

`lead.estado` is NOT reset — it is already `CONSULTA_NUEVA` and must remain so.

**Fields preserved through reset (identity and attribution):**

```
WhatsAppContact: all fields
WhatsAppThread: all fields
Lead: nombre, apellido, email, telefono, canal, ref_code, rc_code, compro_el_auto
WhatsAppThreadState: customer_name, last_processed_inbound_wa_message_id,
    is_website_lead, current_cycle_started_at (updated by reset),
    current_cycle_start_message_db_id (updated by reset)
WhatsAppMessage: all rows preserved (history unchanged)
WhatsAppThreadCandidate: all rows preserved (prior-cycle candidates remain in DB)
ThreadRevision: all rows preserved (historical bookings intact)
Revision: all rows preserved (CRM history intact)
```

### Known state-sync defect: `state.needs_human` vs `lead.necesita_humano`

`[GAP — owner decision required]`

CE reads `state.needs_human` for AI suppression. The human CRM UI writes `lead.necesita_humano` via `POST /ui/human` and `POST /ui/lead_toggle_humano`. These two endpoints do NOT write `state.needs_human`. If a human clears `lead.necesita_humano` without performing a full cycle reset (i.e., without transitioning lead to CONSULTA_NUEVA), CE remains permanently suppressed.

The new cycle reset **does** clear both fields together (both are in the ACTIVE_REVISION clear list above). The gap is for cases where the human wants to resume AI on the same cycle without starting a new one.

Owner decision required: should `POST /ui/human` and `POST /ui/lead_toggle_humano` also write `state.needs_human` when clearing `lead.necesita_humano`? This is a separate scope decision and must not be implemented without direction.

`[IMPLEMENTED — WILD-04R Phase 1]`

Call order in `_handle()` as built:
1. `_load_context()` (loads identity + existing-watermark-filtered context)
2. Dedup check
3. Get/create state, capture `previous_cursor` (before overwriting `last_processed_inbound_wa_message_id`)
4. Advance `last_processed_inbound_wa_message_id = event.wa_message_id`
5. Lead None check
6. **Cycle reset consumption** — if `state.cycle_reset_pending is True`: execute `_execute_cycle_reset()`, re-query candidates/messages with new watermarks, commit
7. `needs_human` guard (evaluated after reset — reset sets `needs_human = False`)
8. Route to `_process_flow_response` or `_process_text`

---

## 3. Message Burst Contract

`[CONTRACT]`

n8n debounces a 20-second window of inbound messages into a single CE call. When a customer sends multiple messages in rapid succession:

- All messages in the burst must be persisted in `whatsapp_messages` before CE runs
- All persisted unprocessed messages must be included in CE's `unanswered_recent_user_messages`
- A later message in the burst arriving after the debounce timer reset must not cause an earlier message to be silently dropped
- CE must produce one natural combined response addressing all messages in the burst

**Example (WILD-04 burst):**

```
Message 1: "Quiero revisar un 2008 del 2014"     ← inspection request
Message 2: "de qué consta la revisión?"          ← FAQ: what's included
Message 3: "cuánto tiempo tarda?"                ← FAQ: duration
Message 4: "qué medios de pago aceptan?"         ← FAQ: payment
```

Expected CE behavior: detect inspection intent from Message 1, qualify vehicle/zone, send combined reply addressing Messages 2–4 alongside the qualification question.

`[CURRENT]`

n8n's `unanswered_recent_user_messages` is assembled from the surviving debounce execution's trigger context. A burst fragmented across two sub-burst n8n executions can cause Message 1 (transcribed in the first sub-burst execution) to be absent from `unanswered_recent_user_messages` in the surviving second execution. CE receives only Messages 2–4 and routes to the FAQ path without detecting the inspection request.

This is the confirmed root cause of the WILD-04 failure.

`[GAP]`

n8n burst completeness is not guaranteed with the current `limit=10` message endpoint and sub-burst fragmentation behavior. CE has no guard against incomplete bursts.

`[IMPLEMENTED — WILD-04R Phase 1]`

CE-side burst completeness guard: during `_process_text()`, after assembling `_current_evidence` from `event.unanswered_recent_user_messages`, CE queries the DB for all unprocessed inbound messages between `state.last_processed_inbound_wa_message_id` and `event.wa_message_id`. Any messages present in the DB but absent from `_current_evidence` are prepended. This makes CE's burst context authoritative from the DB, eliminating the n8n sub-burst fragmentation dependency.

Deferred: n8n endpoint `limit` increase from 10 to 50 to cover pathological burst sizes.

`[IMPLEMENTED — WILD-04R-F3]`

Same-burst FAQ preservation extended to all commercial-progression paths.

**New routing invariant:** When a burst in QUOTED stage contains an acceptance signal (any single message satisfying `_is_acceptance([m])`) alongside FAQ signals, commercial progression takes priority over FAQ fast-path routing (Layer D). FAQ content is appended post-composition, not substituted for it.

**Mechanism:** `_compose_secondary_answers()` runs in `_send_text_to_wa()` for any turn where `_faq_reconciliation_burst` is armed. It appends canonical FAQ answers for any signals present in the burst but absent from the primary reply (probe-based duplicate detection). Covers AI path, deterministic pricing path, and all other commercial handlers.

**Contract implication:** A customer message containing both commercial intent (acceptance, zone, vehicle evidence) and FAQ signals must always receive both a commercial response (stage advance, quote, or scheduling) and the FAQ answer. Silent FAQ discard is a defect.

---

## 4. In-Flight Message Contract

`[CONTRACT]`

If Message B arrives while CE is processing Message A for the same thread:

- Message B must be persisted to `whatsapp_messages` (routes/whatsapp.py commits before CE dispatch)
- Message B must NOT be marked as processed until CE processes it
- Message B must NOT be lost
- When CE finishes Message A, Message B's n8n trigger eventually calls CE, which processes B normally
- Active state after both messages are processed must converge correctly

`[CURRENT]`

This contract is substantially met today:

- `routes/whatsapp.py:379` commits `WhatsAppMessage` before CE dispatch — Message B is safe
- `state.last_processed_inbound_wa_message_id` is the per-thread dedup cursor; each CE call uses the incoming `event.wa_message_id` and only marks it processed after successful handling
- Content fingerprint dedup on the outbound gate prevents duplicate replies

`[GAP]`

No row-level locking prevents two concurrent CE calls on the same thread. The outbound safety gate's `SELECT FOR UPDATE` prevents duplicate outbound sends, but two CE calls could both read and mutate `WhatsAppThreadState` concurrently. In practice, n8n's 20-second debounce serializes messages, making true concurrency rare.

---

## 5. Answer Performance Contract

`[CONTRACT]`

All times measured from customer message send to CE outbound reply dispatch.

| Threshold | Classification | Action |
|---|---|---|
| ≤ 60 seconds | OK | None |
| > 60s and ≤ 120s | MEDIUM | Logged; no immediate alert |
| > 120 seconds | ALERT | Human-visible alert required |
| Reply required, no reply after 120s | ALERT | Human-visible alert required |
| Reply not required (dedup, no_lead, skipped_human) | NO_REPLY_REQUIRED | No alert |

Latency components:

- `latency_ce_ms` — CE processing time measured by `perf_counter()` inside `handle()`
- `latency_total_ms` — full customer wait: `AiEvent.created_at` (webhook arrival) to CE finish (`datetime.now(utc)`)
- `latency_debounce_ms` — pre-CE wait (`latency_total_ms - latency_ce_ms`); captures n8n debounce + dispatch overhead; labeled `pre_ce_wait_ms` in implementation comments

The 120-second ALERT threshold for no-reply is the owner-operative threshold. The existing `unanswered_alert.py` uses an approximately 5-minute wait.

`[CURRENT]`

No per-turn latency is measured or stored. `AiEvent` has no observability columns. The `unanswered_alert.py` service checks threads with no reply but uses a ~5-minute (300-second) threshold, not 120 seconds.

`[GAP]`

- No latency measurement
- No `performance_status` classification per turn
- Alert threshold is 300s, not 120s

`[IMPLEMENTED — WILD-04R Phase 2]`

13 nullable columns added to `ai_events` via migration `20260824_wild04r_ai_events_observability` + `20260824_wild04r_phase2_alert_ts`. `latency_ce_ms` written from `perf_counter()` in `handle()`. `latency_total_ms` computed from `AiEvent.created_at` → CE finish in `api/conversation.py`. `unanswered_alert.py` threshold updated to 120 seconds.

---

## 6. Answer Source Contract

`[CONTRACT]`

Every CE response must be tagged with the authoritative source of the business content it contains. "LLM answered" is NOT sufficient observability — the LLM may render natural language from content provided by a deterministic service.

**Required primary source taxonomy:**

| Source | Meaning |
|---|---|
| `DETERMINISTIC_RULE` | Response from a gate or rule requiring no AI (motorcycle gate, phone escalation, service boundary, inspectability gate, location contradiction) |
| `FAQ_RULE` | FAQ response via `_handle_general_information_ai` (invokes OpenAI to render FAQ content) |
| `PRICING_SERVICE` | Quote generated from PricingService; LLM renders delivery |
| `SCHEDULING_SERVICE` | Slot availability or provisional scheduling; LLM renders delivery |
| `VEHICLE_RESOLVER` | Vehicle catalog lookup / fuzzy match confirmation |
| `CE_AI` | OpenAI call is the primary source of the response |
| `FLOW_RESPONSE` | Booking Flow response processed deterministically |
| `ERROR_FALLBACK` | CE error path; reply may or may not have been sent |
| `HUMAN` | `needs_human` active; CE suppressed; human expected to reply |

**Combined responses:**

When multiple services contribute to one reply (e.g., vehicle qualifier + FAQ answer in same burst), record:
- `primary_answer_source` — dominant source
- `contributing_sources` — comma-separated secondary sources
- `ai_invoked` — `true` if `_call_openai()` was called on this turn

Example: AI renders a quote (PRICING_SERVICE) with an embedded FAQ answer (FAQ_RULE):
```
primary_answer_source = PRICING_SERVICE
contributing_sources  = FAQ_RULE
ai_invoked            = true
```

`[CURRENT]`

No source tracking exists. `AiEvent` has no `answer_source` field. `ConversationHandleOut` has no `answer_source` field. `_out()` carries only `action` (e.g., `"replied"`, `"booking_created"`) which is insufficient to distinguish deterministic from AI responses.

`[GAP]`

Operator cannot determine from telemetry whether a customer reply came from OpenAI, the pricing service, a static rule, or an error fallback.

`[IMPLEMENTED — WILD-04R Phase 2]`

`_out()` extended with `answer_source`, `contributing_sources`, and `ai_invoked` parameters. `ConversationHandleOut` carries all three fields. `handle()` applies `_ai_invoked` flag and infers `answer_source` at the exit point (CE_AI, FLOW_RESPONSE, ERROR_FALLBACK). Call-site tagging implemented for PRICING_SERVICE and FAQ_RULE. Full per-call-site tagging for all 40+ `_out()` sites is a second-pass refinement (deferred).

---

## 7. Human Alert Contract

`[CONTRACT]`

When a customer message requires a CE reply and no reply is produced within 120 seconds, a human-visible alert must fire.

- Alert mechanism: SMTP email to `ridecheckassistance@gmail.com` (existing mechanism in `unanswered_alert.py`)
- Polling interval: 60 seconds (alert fires at 120s–180s window — acceptable)
- A late reply (>120s but eventually produced) must still be classified as `performance_status = ALERT` in telemetry. The alert that already fired is not retracted.
- Alert deduplication: one alert per turn (`unanswered_alert_sent_at` on `ai_events`)

**Alert eligibility:**

Alert eligible when:
- `reply_required = true` (not dedup, not no_lead, not error)
- AND `action != "skipped_human"` (human takeover: CE is not the reply agent — a separate alert path exists for human-required threads)

**Not alert eligible:**
- `skipped_dedup` — not a real turn
- `no_lead` — no CRM record; n8n will have created one
- `error` with `detail="thread_not_found"` — infrastructure gap, not an SLA failure
- `skipped_human` — CE is suppressed intentionally; human reply is expected

**Alert eligible (despite being errors):**
- `error` with `detail="internal_error"` — CE attempted a reply but failed; customer is waiting

`[CURRENT]`

`unanswered_alert.py` runs every 60 seconds. It queries threads where the last message is inbound, `unanswered_alert_sent_at IS NULL`, `needs_human = false`, and `lead.estado = CONSULTA_NUEVA`. Wait before alert: approximately 5 minutes (300 seconds).

`[GAP]`

- Threshold is 300s, not 120s
- Alert is thread-level, not turn-level — cannot distinguish a no-reply on the current turn from the thread being appropriately idle
- No `alert_eligible` flag; alert eligibility logic is approximate

`[IMPLEMENTED — WILD-04R Phase 2]`

`unanswered_alert.py` updated to query `ai_events WHERE reply_required = true AND alert_eligible = true AND reply_produced IS NOT TRUE AND unanswered_alert_sent_at IS NULL AND created_at < NOW() - INTERVAL '120 seconds' AND needs_human IS NULL OR needs_human = false`. Per-turn alert tracking via `ai_events.unanswered_alert_sent_at`. Thread-level human-handoff alert (legacy `needs_human=true` path) preserved alongside per-turn check. Polling interval: 60 seconds.

---

## 8. Outbound Safety

`[CONTRACT]`

CE must never send an outbound WhatsApp message when:

1. `OUTBOUND_ENABLED = false` (kill switch; env var in `docker-compose.yml`)
2. The recipient is not on the `CLOSED_BETA_ALLOWED_WA_IDS` allowlist (when set)
3. The content fingerprint matches a recently-sent message (dedup guard)
4. The thread is in production and the deployment is closed-beta

`[CURRENT — fully implemented]`

The outbound safety gate (`outbound_safety_gate.py`) enforces:

- Kill switch: `OUTBOUND_ENABLED` env var checked per dispatch
- Allowlist: `CLOSED_BETA_ALLOWED_WA_IDS` restricts recipients in closed-beta deployments
- Content fingerprint dedup: prevents double-send on CE retry or concurrent calls
- Quarantine list: `QUARANTINED_TEST_WA_IDS` blocks specific numbers in test scenarios

Production: `docker-compose.yml` uses production DB (`crm`), no allowlist restriction, `OUTBOUND_ENABLED=true`.  
Closed-beta (`docker-compose.beta.yml`): DB = `crm_test`, allowlist = `5491153368330`, `OUTBOUND_ENABLED=false` by default.

---

## 9. Historical Context Rules

`[CONTRACT]`

Historical data must:

- **Persist in the database** — no historical message, candidate, or revision is ever deleted
- **Remain accessible in the CRM** — human operators can see full customer history in the kanban, timeline, and revision views
- **NOT become active semantic evidence** — CE must not use historical data as context for a new revision cycle

### Specific prohibitions

**Messages:**  
Historical messages must not appear in CE's `ctx.db_messages` for a new revision cycle. The AI prompt must not receive a prior booking confirmation, prior quote, or prior scheduling exchange as "recent conversation history" when starting a new cycle.

`[CURRENT / GAP]` `_load_context()` loads `ORDER BY timestamp ASC LIMIT 20` — the 20 oldest messages on the thread. For a thread with substantial history, this is exclusively prior-cycle messages. This is a confirmed bug. See Section 1.

**Candidates:**  
A vehicle candidate from a prior revision cycle must not be returned by `_focus_candidate()` for a new cycle. The fallback in `_focus_candidate()` to `ctx.candidates[0]` (the most-recently-updated candidate, which may be from a prior cycle) is a confirmed defect.

`[CURRENT / GAP]` `_load_context()` loads all candidates unconditionally. `_focus_candidate()` at `conversation_engine.py:4563–4572` falls back to `ctx.candidates[0]` when no `current_focus` status candidate is found. In WILD-04, this caused the Peugeot 2008 (prior-cycle, `status='archived'`) to become the active focus for a new cycle.

**ThreadRevision / Revision:**  
These are CRM records of completed bookings. CE must not read their fields to populate active-cycle context. The `state.current_revision_id` field is the CE's reference to the last booked revision — it is cleared at cycle reset and must not be used to look up historical booking details during a new cycle.

---

## 10. LLM Business Authority Invariant (WILD-04R-F6)

### What the LLM may NOT decide

`[CONTRACT — IMPLEMENTED — WILD-04R-F6]`

The LLM has **no authority** to mutate business-critical derived values. The following fields are owned by deterministic CE logic — they may not be set, overridden, or corrected by an LLM proposal:

| Field / Value | Deterministic Owner | LLM Role |
|---|---|---|
| `tipo_vehiculo` on a catalog-resolved candidate | `VehicleCatalog` → `_catalog_tipo_for()` | May propose vehicle identity (marca/modelo); tipo is derived from catalog |
| Inspection price (`precio_base`, `viaticos`, total) | `PricingService` | May render delivery language; must not invent or modify numbers |
| Zone viatico | `ViaticosZone` DB + `PricingRepository` | May extract location evidence from text; pricing arithmetic is CE's |
| Eligibility (inspectable vs non-inspectable vehicle) | `_check_inspectability_gate()` | May flag uncertainty; final eligibility decision is deterministic |
| Required next info (location question) | `_apply_required_next_question()` | May ask; CE appends the canonical question if AI omitted it |
| Scheduling availability | `ScheduleService` | May present slots; actual availability comes from the schedule service |
| Booking validity | CE booking Flow handler | May express intent; booking logic is deterministic |

### The authority chain for `tipo_vehiculo`

```
Customer language (natural text)
  ↓
Vehicle identity interpretation (marca + modelo extracted by AI or catalog lookup)
  ↓
VehicleCatalog._FULL_FORM_TO_ENTRY lookup (deterministic, _norm pipeline)
  ↓  (if catalog hit → catalog tipo is authoritative)
  ↓  (if no catalog hit → AI tipo is accepted as unknown-vehicle fallback)
canonical tipo_vehiculo (e.g. SUV_4X4_DEPORTIVO for Peugeot 2008)
  ↓
PricingService.quote(tipo_vehiculo, zone_group, zone_detail)
  ↓
precio_total (e.g. $200,000 for SUV_4X4_DEPORTIVO + San Miguel)
```

**Live failure this rule prevents:**

Turn 2 text: "El auto está en San Miguel."
- AI NLP guess: `AUTO` (from "auto" in the message text)
- Catalog fact: Peugeot 2008 → `SUV_4X4_DEPORTIVO`
- Before F6: tipo written as `AUTO`, price = 140000 + 50000 = **$190,000** (wrong)
- After F6: `_catalog_tipo_for("Peugeot", "2008")` → `SUV_4X4_DEPORTIVO`, price = **$200,000** (correct)

### Implementation

`_catalog_tipo_for(self, marca, modelo)` — private CE method:
- Normalizes `"{marca} {modelo}"` via `_norm` (same pipeline as `_resolve_fuzzy_key`)
- Looks up `_FULL_FORM_TO_ENTRY`
- Returns `_normalize_tipo_vehiculo(entry["t"])` if found, else `None`

Guard location in `_apply_candidate()`:
- **Update path:** Before the field-write loop, if `tipo_vehiculo` is in the candidate dict, resolve effective marca+modelo (from update dict or from existing candidate), call `_catalog_tipo_for`, override if catalog hit
- **Create path:** After alias normalization (F5 D3), before `WhatsAppThreadCandidate()` construction, call `_catalog_tipo_for`, override if catalog hit

Unknown vehicles (no catalog hit) are not blocked — the AI tipo is accepted as-is. The guard only fires when the catalog has a definitive entry for the vehicle.

---

## Summary of Implementation Status (as of 2026-08-24)

| Contract requirement | Status | Milestone |
|---|---|---|
| `cycle_reset_pending` column on `whatsapp_thread_states` | IMPLEMENTED | WILD-04R Phase 1 |
| `set_lead_estado()` CRM helper (5 endpoints) | IMPLEMENTED | WILD-04R Phase 1 |
| CE `_execute_cycle_reset()` consumption in `_handle()` | IMPLEMENTED | WILD-04R Phase 1 |
| Active-cycle message filter (id >= watermark) | IMPLEMENTED | WILD-04R Phase 1 |
| Active-cycle candidate filter (created_at >= watermark) | IMPLEMENTED | WILD-04R Phase 1 |
| Message ordering bug (oldest-20 → newest-20) | IMPLEMENTED | WILD-04R Phase 1 |
| ACTIVE_REVISION field reset at cycle boundary | IMPLEMENTED | WILD-04R Phase 1 |
| CE burst completeness guard (DB-authoritative burst assembly) | IMPLEMENTED | WILD-04R Phase 1 |
| AiEvent observability columns (13 fields) | IMPLEMENTED | WILD-04R Phase 2 |
| latency_ce_ms / latency_total_ms / latency_debounce_ms measurement | IMPLEMENTED | WILD-04R Phase 2 |
| Answer source tagging (PRICING_SERVICE, FAQ_RULE, CE_AI, FLOW_RESPONSE, ERROR_FALLBACK) | IMPLEMENTED (partial — full per-call-site tagging deferred) | WILD-04R Phase 2 |
| Unanswered alert threshold (300s → 120s, per-turn via ai_events) | IMPLEMENTED | WILD-04R Phase 2 |
| `state.needs_human` / `lead.necesita_humano` sync in human-toggle endpoints | OPEN — owner decision pending | TBD |
| n8n endpoint limit increase (10 → 50) | OPEN — deferred | TBD |
| Same-burst FAQ preservation across all commercial-progression paths | IMPLEMENTED | WILD-04R-F3 |
| QUOTED+acceptance burst prioritized over FAQ fast-path (Layer D guard) | IMPLEMENTED | WILD-04R-F3 |
| Zone correction re-quote guard: location change in QUOTED/SCHEDULING → re-price | IMPLEMENTED | WILD-04R-F3 |
| Candidate dedup on create: same marca+modelo redirects to update existing | IMPLEMENTED | WILD-04R-F3 |
| Non-focus candidate IDs surfaced in AI prompt for re-focus | IMPLEMENTED | WILD-04R-F3 |
| Turn Reconciliation: vehicle correction/replacement/focus-switch — multi-domain | IMPLEMENTED (AI-guided, bounded seam) | WILD-04R-F3 |
| Catalog authority for `tipo_vehiculo` — LLM cannot overwrite catalog-validated tipo | IMPLEMENTED | WILD-04R-F6 |
| `_catalog_tipo_for(marca, modelo)` guard in `_apply_candidate()` update and create paths | IMPLEMENTED | WILD-04R-F6 |
