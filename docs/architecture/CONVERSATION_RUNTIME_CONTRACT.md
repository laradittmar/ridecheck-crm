# RideCheck CRM — Conversation Runtime Contract

**Status:** AUTHORITATIVE BUSINESS CONTRACT  
**Established by:** WILD-04R Architecture Correction audit (2026-08-24)  
**Read alongside:** `docs/architecture/DOMAIN_MODEL.md`

> **CRITICAL:** If CE code conflicts with this contract, **STOP**. Do not silently update this document to match the code. Report the conflict as an architecture defect requiring owner decision.

Sections use these labels:

- `[CONTRACT]` — Owner-authoritative requirement. Must be true.
- `[CURRENT]` — How the code actually behaves today (2026-08-24).
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

`[PLANNED — WILD-04R]`

Two new columns on `whatsapp_thread_states`:
- `current_cycle_start_message_db_id INTEGER NULL` — `WhatsAppMessage.id` of the first inbound message of the current cycle
- `current_cycle_started_at TIMESTAMPTZ NULL` — `WhatsAppMessage.created_at` (DB server clock) of the same message

`_load_context()` will filter:
- `db_messages`: `WHERE id >= current_cycle_start_message_db_id` (when set)
- `candidates`: `WHERE created_at >= current_cycle_started_at` (when set)
- Message ordering corrected to: `ORDER BY id DESC LIMIT 20` then reversed (most-recent-20 in chronological order)

---

## 2. Cycle Boundary

### Owner-approved lifecycle trigger

`[CONTRACT]`

A new inspection cycle begins when ALL of the following are true:

1. A human operator has set `lead.estado = CONSULTA_NUEVA` via the CRM UI
2. A prior inspection cycle was completed (evidenced by `state.current_revision_id IS NOT NULL`)
3. A new inbound message arrives from the customer

The human CONSULTA_NUEVA reset is an **intentional business action**. CE must not auto-detect or auto-trigger a cycle boundary. Only a human can start a new cycle.

The reset is **edge-triggered**: it fires exactly once. After the reset, `state.current_revision_id` is cleared (set to NULL), which prevents the same reset from firing again on subsequent messages in the same new cycle.

**Why `current_revision_id` is the correct edge signal:**

- `state.current_revision_id` is set at exactly one code site: `conversation_engine.py:1539`, inside the booking flow handler, when a `ThreadRevision(status='booked')` is created
- It is not set for provisional revisions (scheduling escalation path)
- After reset, it is cleared to NULL — the predicate becomes false for all subsequent turns in the new cycle
- It rearms only when the next booking is completed

**Safe cycle detection predicate:**

```python
def _is_new_cycle(lead, state) -> bool:
    return (
        lead.estado == "CONSULTA_NUEVA"
        and state.current_revision_id is not None
    )
```

**Fields reset at cycle boundary (all ACTIVE_REVISION fields on WhatsAppThreadState):**

```
last_intent, last_stage, needs_human, current_focus_candidate_id,
current_revision_id, home_zone_group, home_zone_detail,
preferred_day, preferred_time, active_requested_date, last_requested_time,
last_offered_slots, last_visible_slots, flow_booking_token,
vehicle_clarification_sent, location_clarification_sent,
vehicle_fallback_flow_sent, location_fallback_flow_sent,
inspectability_clarification_sent, pending_fuzzy_catalog_key,
pending_turn_evidence_text
```

**Fields reset at cycle boundary on Lead:**

```
flag, necesita_humano
```

`lead.estado` is NOT reset by CE — it is already CONSULTA_NUEVA (the human set it; CE must not overwrite it again).

`[CURRENT]`

CE does not read `lead.estado` for routing decisions anywhere. The `needs_human` guard at `conversation_engine.py:1377–1381` is unconditional — it suppresses CE regardless of `lead.estado`. There is no cycle detection, no ACTIVE_REVISION field reset, no cycle watermark.

`[GAP]`

The human CONSULTA_NUEVA reset has no effect on CE behavior today. A returning customer whose prior cycle ended with `needs_human=True` (post-booking) will be permanently suppressed by the `needs_human` guard even after the human resets the lead.

`[PLANNED — WILD-04R]`

Add `_is_new_cycle()` detection in `_handle()`, positioned **before** the `needs_human` guard and **before** semantic context is used. When the predicate fires: execute `_reset_revision_cycle()` which clears ACTIVE_REVISION fields, sets cycle watermarks, and commits. Then re-query candidates and messages with the new watermarks before proceeding. After reset, `needs_human = False`, so CE proceeds with the new cycle normally.

Required call order in `_handle()`:
1. `_load_context()` (loads identity + existing-watermark-filtered context)
2. Dedup check
3. Get/create state, set `last_processed_inbound_wa_message_id`
4. Lead None check
5. **Cycle detection + reset** (if fired: re-query candidates/messages with new watermarks)
6. `needs_human` guard
7. Route to `_process_flow_response` or `_process_text`

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

`[PLANNED — WILD-04R]`

CE-side burst completeness guard: during `_process_text()`, after assembling `_current_evidence` from `event.unanswered_recent_user_messages`, CE queries the DB for all unprocessed inbound messages between `state.last_processed_inbound_wa_message_id` and `event.wa_message_id`. Any messages present in the DB but absent from `_current_evidence` are prepended. This makes CE's burst context authoritative from the DB, eliminating the n8n sub-burst fragmentation dependency.

Also planned: n8n endpoint `limit` increase from 10 to 50 to cover pathological burst sizes.

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

- `latency_debounce_ms` — time from first burst message to CE call start (n8n debounce contribution)
- `latency_ce_ms` — CE processing time (DB queries + OpenAI API if invoked)
- `latency_total_ms` — end-to-end: inbound `WhatsAppMessage.created_at` to outbound `WhatsAppMessage.created_at` (both DB server clock, comparable)

The 120-second ALERT threshold for no-reply is the owner-operative threshold. The existing `unanswered_alert.py` uses an approximately 5-minute wait.

`[CURRENT]`

No per-turn latency is measured or stored. `AiEvent` has no observability columns. The `unanswered_alert.py` service checks threads with no reply but uses a ~5-minute (300-second) threshold, not 120 seconds.

`[GAP]`

- No latency measurement
- No `performance_status` classification per turn
- Alert threshold is 300s, not 120s

`[PLANNED — WILD-04R observability milestone]`

Add 11 nullable columns to `ai_events`. Write `latency_ce_ms` from `perf_counter()` at `handle()` entry/exit. Compute `latency_total_ms` from inbound/outbound `WhatsAppMessage.created_at` delta in `routes/whatsapp.py`. Update `unanswered_alert.py` eligibility to 120 seconds.

---

## 6. Answer Source Contract

`[CONTRACT]`

Every CE response must be tagged with the authoritative source of the business content it contains. "LLM answered" is NOT sufficient observability — the LLM may render natural language from content provided by a deterministic service.

**Required primary source taxonomy:**

| Source | Meaning |
|---|---|
| `DETERMINISTIC_RULE` | Response from a gate or rule requiring no AI (motorcycle gate, phone escalation, service boundary, inspectability gate, location contradiction) |
| `FAQ_RULE` | Static FAQ copy (Layer D: `_handle_general_information_ai` without OpenAI) |
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

`[PLANNED — WILD-04R observability milestone]`

Extend `_out()` with `answer_source`, `contributing_sources`, and `ai_invoked` parameters. Add corresponding fields to `ConversationHandleOut`. Tag each handler at call sites. Write to `ai_events` via `routes/whatsapp.py` after CE returns. Full per-call-site tagging is a second-pass refinement; deterministic handler sources are tagged first.

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
- `error` — internal error, not an SLA failure
- `skipped_human` — CE is suppressed intentionally; human reply is expected

`[CURRENT]`

`unanswered_alert.py` runs every 60 seconds. It queries threads where the last message is inbound, `unanswered_alert_sent_at IS NULL`, `needs_human = false`, and `lead.estado = CONSULTA_NUEVA`. Wait before alert: approximately 5 minutes (300 seconds).

`[GAP]`

- Threshold is 300s, not 120s
- Alert is thread-level, not turn-level — cannot distinguish a no-reply on the current turn from the thread being appropriately idle
- No `alert_eligible` flag; alert eligibility logic is approximate

`[PLANNED — WILD-04R observability milestone]`

After observability columns are added to `ai_events`: update `unanswered_alert.py` to query `ai_events WHERE alert_eligible = true AND reply_produced IS NOT TRUE AND created_at < NOW() - INTERVAL '120 seconds' AND unanswered_alert_sent_at IS NULL`. Move per-turn alert tracking to `ai_events.unanswered_alert_sent_at`.

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

## Summary of Implementation Gaps (as of 2026-08-24)

| Contract requirement | Gap severity | Milestone |
|---|---|---|
| Active-cycle message filter | CRITICAL — confirmed WILD-04 cause | WILD-04R |
| Active-cycle candidate filter | CRITICAL — confirmed WILD-04 cause | WILD-04R |
| Message ordering bug (oldest-20 vs newest-20) | CRITICAL — wrong AI context | WILD-04R |
| Cycle boundary detection (lead.estado read by CE) | CRITICAL — human reset has no effect | WILD-04R |
| ACTIVE_REVISION field reset at cycle boundary | CRITICAL — state leaks forward | WILD-04R |
| n8n burst completeness (sub-burst fragmentation) | HIGH — confirmed WILD-04 cause | WILD-04R |
| AiEvent observability columns | MEDIUM — no performance visibility | WILD-04R obs milestone |
| latency_ce_ms / latency_total_ms measurement | MEDIUM — no performance data | WILD-04R obs milestone |
| Answer source tagging | MEDIUM — no provenance | WILD-04R obs milestone |
| Unanswered alert threshold (300s → 120s) | MEDIUM — misses SLA | WILD-04R obs milestone |
