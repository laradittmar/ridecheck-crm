# RideCheck CRM — Inbound Message Lifecycle

**Status:** CURRENT IMPLEMENTATION REFERENCE  
**Established:** 2026-08-25  
**Read alongside:**
- [`docs/architecture/DOMAIN_MODEL.md`](DOMAIN_MODEL.md) — entity model, field ownership, revision lifecycle
- [`docs/architecture/CONVERSATION_RUNTIME_CONTRACT.md`](CONVERSATION_RUNTIME_CONTRACT.md) — active-cycle context, cycle boundary, burst contract, telemetry thresholds

> **CRITICAL:** This document describes what the code does **now**, after WILD-04R, WILD-04R-F1, WILD-04R-F2, and WILD-04R-F3. If code conflicts with this document, STOP and report it as an architecture defect.

Sections use these labels:

- `[CONTRACT]` — Owner-authoritative requirement. Must be true.
- `[CURRENT]` — How the code actually behaves today.
- `[KNOWN GAP]` — Known delta between contract and current behavior.

---

## 1. High-Level Flow

```
CUSTOMER SENDS A WHATSAPP MESSAGE
          │
          ▼
META / WHATSAPP WEBHOOK
  POST /public/webhook/whatsapp
  n8n receives the raw webhook payload
          │
          ▼
PERSIST WhatsAppMessage (direction='in')
  Committed to DB before CE runs.
  Message survives any CE failure.
          │
          ▼
AUDIO TRANSCRIPTION (if message_type='audio')
  n8n Whisper node transcribes the audio.
  Transcript written back to WhatsAppMessage.text.
  Image description (GPT-4o) handled the same way.
          │
          ▼
N8N 20-SECOND DEBOUNCE
  Customer may send A, B, C as separate rapid messages.
  n8n coalesces them into one CE invocation.
  All messages already persisted; debounce is a timing gate only.
          │
          ▼
n8n → POST /api/conversation/handle
  ConversationHandleIn: thread_id, wa_message_id,
  unanswered_recent_user_messages, text, message_type
          │
          ▼
CE DISPATCH  handle() → _handle()
          │
          ▼
LOAD CONTEXT  _load_context(thread_id)
  Thread, Contact, Lead, State
  Candidates filtered by current_cycle_started_at watermark
  Messages filtered by current_cycle_start_message_db_id watermark
  (newest-20 active-cycle messages, in chronological order)
          │
          ▼
DEDUP CHECK
  state.last_processed_inbound_wa_message_id == event.wa_message_id?
  → yes: return skipped_dedup (idempotent)
  → no: continue
          │
          ▼
GET / CREATE STATE
  Capture previous_cursor BEFORE overwriting last_processed_inbound_wa_message_id.
  Advance cursor to event.wa_message_id.
          │
          ▼
LEAD CHECK
  ctx.lead is None?
  → yes: commit cursor marker, return no_lead
  → no: continue
          │
          ▼
CYCLE RESET CONSUMPTION  (if state.cycle_reset_pending)
  _execute_cycle_reset(): establish new-cycle watermarks,
  clear all ACTIVE_REVISION fields, commit.
  POST-RESET RELOAD (WILD-04R-F2):
    _reload_active_candidates() — re-query with new watermark
    _query_active_messages()    — re-query with new watermark
  Clears stale prior-cycle context that _load_context loaded
  before the reset ran.
          │
          ▼
HUMAN TAKEOVER CHECK  (evaluated AFTER reset clears needs_human)
  state.needs_human?
  → yes: return skipped_human (AI suppressed, human expected to reply)
  → no: continue
          │
          ▼
MESSAGE TYPE ROUTING
  message_type == 'flow_response'?
  → yes: _process_flow_response() — deterministic, no AI
  → no:  _process_text()
          │
          ▼
_process_text()
          │
          ▼
DB-AUTHORITATIVE BURST RECONSTRUCTION
  _fetch_burst_messages(thread_id, previous_cursor, current_wa_message_id)
  Queries WhatsAppMessage rows between cursor and current event.
  Prepends any DB messages absent from n8n's unanswered_recent_user_messages.
  current_turn_text = join of all burst texts.
  burst_message_count, burst_earliest_inbound_db_id recorded for telemetry.
          │
          ▼
EVIDENCE EXTRACTION LAYERS (all run before routing decides a reply)
  ┌─────────────────────────────────────────────────────────────┐
  │ Layer A: Motorcycle pre-gate                                │
  │   → motorcycle enquiry? human handoff immediately           │
  │                                                             │
  │ Layer B: Phone-call/human-request pre-gate                  │
  │   → explicit call request? human handoff                   │
  │                                                             │
  │ Layer C: Unsupported-service pre-gate                       │
  │   → F12/transfer/repair boundary? deterministic block       │
  │                                                             │
  │ Layer D: FAQ bypass (pure-FAQ turns only)                   │
  │   → detect_general_information AND no vehicle AND          │
  │     no prepurchase signal AND no location phrase           │
  │   → _handle_general_information_ai (AI renders FAQ)        │
  │   (zone/vehicle evidence in burst BLOCKS this path →        │
  │    keeps evidence extraction and full AI path intact)       │
  │                                                             │
  │ Layers E+: Stage-based routing                              │
  │   QUALIFYING / QUOTED / SCHEDULING / BOOKED handlers        │
  │   Vehicle detection, zone detection, candidate create/update│
  │   PricingService quote computation                          │
  └─────────────────────────────────────────────────────────────┘
          │
          ▼
STATE / CANDIDATE MUTATION
  Vehicle candidate created or updated in DB
  Zone written to state (home_zone_group / home_zone_detail)
  Stage advanced (QUALIFYING → QUOTED → SCHEDULING → BOOKED)
  Lead.flag advanced (PRESUPUESTANDO → PRESUPUESTO_ENVIADO → ACEPTADO)
          │
          ▼
RESPONSE COMPOSITION
  Deterministic rules assemble reply text.
  AI (OpenAI) invoked when deterministic path is insufficient.
  WILD-04R-F2: if burst contains FAQ signals alongside a pricing trigger,
  _build_faq_supplement() appends canonical FAQ answers to the quote reply.
  Neither FAQ supplement nor AI call may alter PricingService output.
          │
          ▼
OUTBOUND SAFETY GATE  _send_text_to_wa()
  OUTBOUND_ENABLED env var checked
  CLOSED_BETA_ALLOWED_WA_IDS allowlist checked
  Content fingerprint dedup checked
  If blocked: OutboundBlockedError → blocked_dispatch (state committed)
  If clear: message sent via Meta WhatsApp API
          │
          ▼
OUTBOUND MESSAGE PERSISTED  (direction='out', WhatsAppMessage)
          │
          ▼
AiEvent WRITTEN  (via api/conversation.py)
  latency_total_ms computed (webhook arrival → CE finish)
  latency_ce_ms from perf_counter() in handle()
  latency_debounce_ms = latency_total_ms - latency_ce_ms
  reply_required, reply_produced, alert_eligible, answer_source,
  contributing_sources, ai_invoked, burst_message_count,
  performance_status recorded
          │
          ▼
SLA / HUMAN ALERT  (unanswered_alert.py — 60-second polling loop)
  Queries ai_events WHERE reply_required AND alert_eligible
  AND NOT reply_produced AND created_at < NOW() - 120s
  → SMTP alert to ridecheckassistance@gmail.com (once per turn)
```

---

## 2. Persistence First

`[CONTRACT]`

Every inbound `WhatsAppMessage` is committed to the database **before** CE is invoked.

`[CURRENT]`

`routes/whatsapp.py` commits the `WhatsAppMessage` row (`direction='in'`) before dispatching the event to CE. This is unconditional — it does not depend on CE success.

**Fields persisted at this stage:**

| Field | Source |
|---|---|
| `thread_id` | Resolved from `wa_id` by n8n lead-find/create step |
| `wa_message_id` | Meta's message ID (unique per message from Meta) |
| `direction` | `'in'` |
| `message_type` | `'text'`, `'audio'`, `'image'`, `'flow_response'`, etc. |
| `text` | Raw message text (NULL for audio until transcription) |
| `timestamp` | Meta-provided customer send time (customer clock) |
| `created_at` | DB server clock at INSERT time |
| `status` | `'received'` |

**Why this matters:**

1. If CE crashes or raises an unhandled exception, the message row already exists. n8n's retry, or a human operator's re-trigger, can re-deliver the event without creating a duplicate row (the `wa_message_id` dedup at the CE level prevents double-processing).

2. `WhatsAppMessage.id` (auto-increment, DB-assigned) and `WhatsAppMessage.created_at` (DB server clock) are used as cycle watermarks. They are set at INSERT time, before CE reasoning, making them reliable and independent of CE behavior.

3. The CE burst reconstruction uses `WhatsAppMessage.id` as the cursor boundary. Because messages are pre-committed, all messages in a burst are available for DB-authoritative reconstruction even if n8n's event payload is incomplete.

**Why DB message IDs are preferred over `wa_message_id` for cursor ordering:**

`wa_message_id` is a Meta-assigned string opaque identifier. It carries no guaranteed ordering. `WhatsAppMessage.id` is an auto-increment integer set by the DB at commit time — it provides reliable monotonic ordering within a thread.

---

## 3. Audio / Image Transcription

`[CURRENT]`

Audio and image messages arrive as binary attachments. n8n handles transcription before CE runs:

1. n8n webhook receives the message. The `WhatsAppMessage` row is created with `text=NULL` and `message_type='audio'` (or `'image'`).
2. **Audio:** n8n's Whisper node calls the Whisper API. The transcript is written back to `WhatsAppMessage.text` in the same DB row.
3. **Image:** n8n's GPT-4o node generates a textual description. Written back to `WhatsAppMessage.text`.
4. n8n proceeds with the text-populated message to the debounce stage.

**Multiple audio messages in one burst:**

A customer may send several audio messages before n8n's debounce fires. Each is transcribed independently. All transcripts are present in `whatsapp_messages` before CE runs. CE's DB-authoritative burst reconstruction reads all of them from the DB and presents a joined `current_turn_text` to evidence extraction.

**CE receives audio content only as text.** CE is text-only. By the time CE is called, all audio/image content has been converted to `WhatsAppMessage.text`. CE does not know whether content was originally audio.

---

## 4. Debounce and Burst Assembly

`[CONTRACT]`

The 20-second debounce determines **when** CE runs. The DB determines **what** messages belong to the turn.

`[CURRENT]`

**The debounce:**

n8n holds a 20-second wait after each inbound message. If another message arrives within the window, the timer resets. When the window expires without a new message, n8n fires a single CE invocation carrying the most-recently-received `wa_message_id` and the aggregated `unanswered_recent_user_messages` list (up to n8n's internal message limit).

**DB-authoritative burst reconstruction (WILD-04R Phase 1):**

Inside `_process_text()`, CE performs:

```
_fetch_burst_messages(thread_id, previous_cursor, current_wa_message_id)
```

This queries `whatsapp_messages WHERE id > prev_db_id AND id <= current_db_id AND direction='in' ORDER BY id ASC`. Any messages present in the DB but absent from n8n's `unanswered_recent_user_messages` are prepended to `_current_evidence`.

**Why DB wins over n8n payload:**

n8n has an internal message-fetch limit (currently 10). A large burst, or a burst fragmented by sub-burst debounce executions, can result in earlier messages being absent from the n8n payload. Without the DB-authoritative guard, an inspection-intent message at the start of a burst could be dropped, causing CE to route to the FAQ path instead of the qualification path.

This was the confirmed root cause of WILD-04: Message 1 ("Quiero revisar un 2008 del 2014") was absent from the n8n payload. CE received only Messages 2–4 (FAQ questions) and routed to general information handling, creating no candidate and asking no qualifying questions.

**What belongs to a burst:**

All `WhatsAppMessage` rows with `direction='in'` and `id` strictly between the previous processed cursor and the current event's DB id, inclusive. There is no hard semantic limit on burst size — the watermark boundary is the cursor, not a message count.

**Key invariant:** Earlier messages cannot disappear merely because a later debounce execution wins. Every message committed to the DB before CE runs will be picked up by burst reconstruction.

---

## 5. Message Arrives While CE Is Processing

`[CONTRACT]`

If Message B arrives while CE is processing Message A for the same thread:
- Message B is persisted independently (pre-committed, before CE dispatch)
- Message B is not rolled back by any CE failure on Message A
- Message B receives its own n8n trigger after n8n's next debounce window
- CE processes Message B in a later call, by which point Message A's cursor has advanced

`[CURRENT]`

This contract is substantially met:

- `routes/whatsapp.py` commits the `WhatsAppMessage` row before CE dispatch (§2 above).
- The `last_processed_inbound_wa_message_id` cursor is advanced atomically with the CE response commit. If CE crashes processing Message A, the cursor is not advanced, and Message A's n8n retry will reprocess it.
- Content-fingerprint dedup on the outbound gate prevents a duplicate outbound send on retry.

`[KNOWN GAP]`

No row-level locking prevents two concurrent CE calls on the same thread. Two CE calls could read and mutate `WhatsAppThreadState` concurrently. The outbound safety gate (`SELECT FOR UPDATE` on the dedup row) prevents a duplicate outbound message but does not prevent two CE calls from producing conflicting state mutations.

In practice, n8n's 20-second debounce serializes most concurrent message scenarios. True concurrency requires two messages to arrive seconds apart and both debounce windows to expire simultaneously — rare in normal usage.

---

## 6. Returning Customer / Revision Cycle Reset

`[CONTRACT]`

A returning customer reuses the same `WhatsAppContact`, `WhatsAppThread`, and `Lead` that were established at first contact. See `DOMAIN_MODEL.md §6` for the full worked example.

A new inspection cycle begins when a human operator moves `Lead.estado` to `CONSULTA_NUEVA` via the CRM UI. CE must not auto-trigger a cycle boundary.

`[CURRENT]`

**Signal: `cycle_reset_pending`**

Every CRM endpoint that writes `Lead.estado` calls the centralized `set_lead_estado()` helper (`backend/app/services/lead_lifecycle.py`). The helper detects a real transition to `CONSULTA_NUEVA` and sets `state.cycle_reset_pending = True` on the linked `WhatsAppThreadState`.

The signal fires on **all** lifecycle end-states:
- Completed cycle: `REVISION_COMPLETA → CONSULTA_NUEVA`
- Abandoned cycle: any non-CONSULTA_NUEVA → CONSULTA_NUEVA
- Human-resolved escalation: `ATENCION_HUMANA → CONSULTA_NUEVA`

The signal does **not** fire on first-ever lead creation (no prior estado) or on a CONSULTA_NUEVA→CONSULTA_NUEVA no-op.

**CE consumption: `_execute_cycle_reset()`**

Called in `_handle()` immediately after the lead check, before the `needs_human` guard. Consumption steps:

1. Capture `previous_cursor` (last processed message before the new cycle)
2. Query the DB burst: all `WhatsAppMessage` rows `WHERE id > prev_db_id AND id <= current_db_id AND direction='in'`
3. Set `state.current_cycle_start_message_db_id = first_burst_msg.id`
4. Set `state.current_cycle_started_at = first_burst_msg.created_at` (DB server clock)
5. Clear all `ACTIVE_REVISION` fields on `WhatsAppThreadState` and `Lead` (see full list in `CONVERSATION_RUNTIME_CONTRACT.md §2`)
6. Set `state.cycle_reset_pending = False`
7. Commit

**Post-reset context reload (WILD-04R-F2):**

`_load_context()` runs at the top of `_handle()`, before the reset. It uses the **old** watermarks that existed before `_execute_cycle_reset()` wrote new ones. On the first turn after a reset, this means `_load_context()` may return prior-cycle candidates and messages.

F2 adds an explicit reload after `_execute_cycle_reset()`:

```python
ctx.candidates = self._reload_active_candidates(ctx.thread.id, state)
ctx.db_messages = self._query_active_messages(ctx.thread.id, state)
```

This re-queries both collections using the freshly written new-cycle watermarks. The result: the first new-cycle turn sees the same empty candidate list and new-cycle-only messages that every subsequent turn in the same cycle sees.

**After consumption:**

`cycle_reset_pending = False`. Subsequent turns in the same new cycle never fire the reset again. The new watermarks (`current_cycle_start_message_db_id`, `current_cycle_started_at`) remain set until the next human reset.

---

## 7. Historical vs Active Context

`[CONTRACT]`

Historical data is **preserved**, never deleted. Active CE context uses only current-cycle data. These are separate concerns.

`[CURRENT]`

**Messages:**

`_query_active_messages()` queries `WhatsAppMessage WHERE id >= current_cycle_start_message_db_id ORDER BY id DESC LIMIT 20`, then reverses the result to produce newest-20 in chronological order.

When `current_cycle_start_message_db_id` is `NULL` (first-ever cycle, no prior reset), all messages on the thread are eligible — there is no prior-cycle contamination because no prior cycle exists.

Prior-cycle messages remain in `whatsapp_messages` permanently and are visible to human operators in the CRM timeline. They are not in `ctx.db_messages` for active CE reasoning.

**Candidates:**

`_load_context()` filters `WhatsAppThreadCandidate WHERE created_at >= current_cycle_started_at`. `_reload_active_candidates()` applies the same filter after a reset.

Prior-cycle candidates remain in `whatsapp_thread_candidates` permanently. `_focus_candidate()` only selects from `ctx.candidates`, which is already filtered — it cannot return a prior-cycle candidate.

**Hard rule:** A historical candidate must never suppress creation of a new candidate. If `ctx.candidates` is empty at the start of a new cycle (because all prior-cycle candidates are excluded by the watermark), CE creates a new candidate from vehicle evidence in the burst. This is the correct behavior.

**What is NOT in active context:**

| Data | Status |
|---|---|
| Prior-cycle `WhatsAppMessage` rows | In DB, not in `ctx.db_messages` |
| Prior-cycle `WhatsAppThreadCandidate` rows | In DB, not in `ctx.candidates` |
| `ThreadRevision` booking records | In DB, not read by CE for new-cycle context |
| `Revision` CRM records | In DB, not read by CE for new-cycle context |
| ACTIVE_REVISION state fields (zone, stage, etc.) | Cleared at reset; start as NULL |

**What IS persistent across cycles (identity facts):**

- `WhatsAppContact` fields: permanent identity
- `Lead` identity fields: `nombre`, `apellido`, `email`, `telefono`, `canal`, `ref_code`, `rc_code`
- `WhatsAppThreadState.customer_name`
- `WhatsAppThreadState.last_processed_inbound_wa_message_id` (dedup cursor)
- `WhatsAppThreadState.is_website_lead`

---

## 8. Evidence Extraction vs Response Routing

`[CONTRACT]`

Evidence extraction happens before — and independently of — response routing. Facts extracted from a burst are persisted first. The response routing path chosen afterwards cannot erase evidence that has already been committed.

`[CURRENT]`

The `_process_text()` flow separates evidence extraction from routing:

1. **Burst assembled** (all messages, DB-authoritative)
2. **Current-turn text joined** from all burst messages
3. **Service gates run** (motorcycle, phone, unsupported-service) — these fire on the combined text and may return early with a human handoff
4. **Routing gates run** — if the turn is purely informational with no vehicle or location, the FAQ fast-path (Layer D) routes to AI-rendered FAQ; if the turn has vehicle/location evidence, the full stage-based routing path runs
5. **Within the full path**, vehicle detection, zone detection, and candidate create/update happen as part of the stage handlers — facts are written before the final reply is assembled

**Why this matters — WILD-04/F1 example:**

Burst: `["Quiero revisar un 2008 del 2014", "¿Mandan informes?", "¿Aceptan débito?"]`

All three messages are joined into `current_turn_text`. Vehicle evidence ("2008 del 2014") is detected from the combined text. The Layer D FAQ guard checks for location phrases and vehicle presence — finding a vehicle, it does NOT route to the FAQ fast-path. The full qualification path runs, creates the Peugeot 2008 / 2014 candidate, and the AI reply addresses both the qualification and the FAQ questions.

If evidence extraction were done only from the final message in the burst, the vehicle would be missed and no candidate would be created.

**Numeric model disambiguation:**

The vehicle catalog resolver (`lookup_vehicle()`) handles ambiguous numeric strings:

| Burst text | Resolved as |
|---|---|
| `"2008 del 2014"` | Peugeot 2008, year 2014 |
| `"Focus 2008"` | Ford Focus, year 2008 |
| `"Gol 2008"` | VW Gol, year 2008 |
| `"2008"` alone (ambiguous) | Triggers vehicle clarification flow |

The resolver distinguishes model numbers from years by catalog lookup: "2008" is a known Peugeot model; a second numeric token in a "model del year" pattern is parsed as the year. A known make token ("Focus", "Gol") before the number resolves the model unambiguously.

---

## 9. Routing vs Response Composition

`[CONTRACT]`

Routing determines which commercial stage to advance. Response composition assembles the reply text. These are distinct operations. A routing decision must not silently discard valid questions present in the same burst.

`[CURRENT — WILD-04R-F3]`

**The defect this rule addresses (pre-F2):**

Before WILD-04R-F2, when a burst contained zone evidence + an FAQ question, the deterministic pricing path discarded any FAQ signals. The customer received the price but got no answer to their hours/payment/presence question.

**F3 global same-burst FAQ reconciliation layer (WILD-04R-F3):**

F3 extends the rule to ALL commercial-progression paths, not just pricing.

**Mechanism:** `_faq_reconciliation_burst` is a per-turn instance attribute set in `_process_text()` after all early-return gates (motorcycle, phone, service, FAQ bypass, inspectability, website form) have been cleared. At `_send_text_to_wa()` time, `_compose_secondary_answers()` checks the stored burst text for unaddressed FAQ signals and appends canonical answers to the primary reply before outbound.

**Key properties:**
- Fires from `_send_text_to_wa()` — a single pre-outbound point covering ALL commercial handlers
- Probe-based duplicate detection: each canonical answer is only appended if its key phrase is absent from the primary reply (probe strings: "lunes a viernes", "informe", "presente", "efectivo")
- Does NOT alter state, lead.flag, candidate, zone, or scheduling
- `contributing_sources = ["FAQ_RULE"]` added to telemetry when supplement fires
- Arm cleared on first `_send_text_to_wa()` call (fires once per turn)

**Layer D companion guard (F3):**

In QUOTED stage, if the burst contains an acceptance word (`_is_acceptance([m])` is True for any single message), Layer D (FAQ bypass) is suppressed. Without this guard, "Dale ¿Aceptan débito?" would be intercepted as a pure FAQ turn, answering the payment question but never advancing `flag → ACEPTADO`.

**Example: Berazategui + horarios burst (F2 case, now via F3)**

Burst: `["El auto está en Berazategui.", "¿En qué horarios laburan?"]`  
Candidate: Peugeot 2008 / 2014, SUV_4X4_DEPORTIVO

- Zone "Berazategui" → `zone_group=Sur`, viaticos $90,000
- Vehicle base price $150,000 (SUV_4X4_DEPORTIVO)
- Quote: $240,000 (deterministic override path)
- `_faq_reconciliation_burst = "El auto está en Berazategui. ¿En qué horarios laburan?"`
- `_compose_secondary_answers` detects "horarios" → hours answer appended in `_send_text_to_wa`

**Exact current reply:**

```
Genial! La cotización para la revisión del Peugeot 2008 2014 es de $240.000.
Si te parece bien, podemos avanzar.

Trabajamos de lunes a viernes de 9 a 18 hs y los sábados de 9 a 15 hs.
```

Telemetry: `answer_source=PRICING_SERVICE`, `contributing_sources=["FAQ_RULE"]`, `burst_message_count=2`

**Example: acceptance + hours burst (exact F3 live failure)**

Burst: `["Okay !", "¿Qué horarios hacen?"]` while in QUOTED stage

- "horarios" not in Layer D patterns → Layer D does not intercept
- `_is_acceptance(["Okay !", "¿Qué horarios hacen?"])` → False (mixed words) → AI path
- AI detects acceptance → `lead_flag = "ACEPTADO"`, returns scheduling question
- `_faq_reconciliation_burst = "Okay ! ¿Qué horarios hacen?"`
- `_compose_secondary_answers` detects "horarios" → hours answer appended

**Exact F3 reply:**

```
¡Perfecto! ¿Qué día y horario te viene mejor para la revisión?

Trabajamos de lunes a viernes de 9 a 18 hs y los sábados de 9 a 15 hs.
```

Telemetry: `answer_source=CE_AI`, `contributing_sources=["FAQ_RULE"]`, `burst_message_count=2`

**Example: acceptance + payment burst (Layer D guard)**

Burst: `["Dale", "¿Aceptan débito?"]` while in QUOTED stage

- "aceptan debito" matches Layer D FAQ pattern (would normally bypass to FAQ handler)
- Layer D guard fires: QUOTED stage + "Dale" is an acceptance word → Layer D suppressed
- AI path: detects acceptance → `lead_flag = "ACEPTADO"`, returns scheduling question
- `_faq_reconciliation_burst = "Dale ¿Aceptan débito?"`
- `_compose_secondary_answers` detects "aceptan debito" → payment answer appended

**Exact reply:**

```
¡Perfecto! ¿Qué día y horario te viene mejor para la revisión?

Aceptamos efectivo, transferencia bancaria y Mercado Pago.
```

---

## 9.5 Turn Reconciliation

`[CONTRACT]`

Humans do not follow a deterministic conversational flow. Before booking or human handoff, a customer may at any time introduce, correct, or replace a vehicle; correct the inspection location; accept or reject a quote; provide or correct scheduling preferences; or ask FAQ questions. These signals may occur individually, in unexpected order, or simultaneously in the same burst.

**The CRM must reconcile all structured signals in a turn without silently discarding any of them.**

`[CURRENT — WILD-04R-F3]`

**Three mutable pre-booking domains:**

| Domain | Fields | Mutability |
|---|---|---|
| Vehicle | marca, modelo, anio, tipo_vehiculo | Correction (patch same candidate) or Replacement (new candidate) |
| Inspection location | zone_group, zone_detail | Correction (patch state + candidate) |
| Scheduling preference | preferred_day, preferred_time | Correction (overwrite prior preference) |

**LLM role:**

The LLM interprets natural language to extract structured signals: vehicle operations (correction vs. enrichment vs. replacement), location evidence/correction, scheduling preferences, acceptance/rejection, FAQ questions, and ambiguity. The LLM MUST NOT authoritatively decide price, scheduling availability, CRM persistence, or lifecycle transitions — these are deterministic engine responsibilities.

**Deterministic engine role:**

After LLM interpretation, deterministic code reconciles operations:

- `_apply_candidate()`: CREATE (new vehicle, switches focus, demotes old to "mentioned") | UPDATE (patch fields on existing candidate including year, zone, tipo) | NONE
- `_focus_candidate()`: returns current focus by `state.current_focus_candidate_id` → `status="current_focus"` → `candidates[0]`
- `_apply_zone_from_text()`: deterministic zone extraction for vehicle-location phrases and bare localities
- `_compute_price_quote()`: re-runs after every mutation — authoritative, never cached

**Correction semantics:**

| Scenario | Deterministic action | Guard |
|---|---|---|
| Year correction ("Perdón, es 2018") | AI → action=update anio=2018 on focus candidate | Year sync (lines 2957-2990): if focus.anio is None + single year token in turn |
| Vehicle replacement (new marca/modelo) | AI → action=create + status=current_focus → old focus demoted to "mentioned" | Vehicle-change guard: tipo changes → reset to QUALIFYING |
| Location correction ("Pilar, no Palermo") | Deterministic zone extraction → state+candidate zone updated | F3-T2 zone guard: zone changed + QUOTED/SCHEDULING → reset to QUALIFYING |
| Scheduling correction ("Mejor el viernes") | Deterministic parse: new day/time overwrites state.preferred_day/preferred_time | Current turn takes priority over stored preference |
| Return to prior candidate | AI → action=update id=<old_id> status=current_focus | F3-T3 dedup: AI action=create for same marca/modelo/tipo redirects to update existing |

**F3-T2: Zone correction re-quote guard**

When `state.home_zone_group` or `state.home_zone_detail` changes during a turn AND the stage is QUOTED, SCHEDULING, or FLOW_SENT: CE resets `lead.flag = "PRESUPUESTANDO"` and `state.last_stage = STAGE_QUALIFYING`. The deterministic quote override then re-prices with the new zone. This ensures the customer receives an updated quote after a location correction.

**F3-T3: Candidate dedup on create**

`_apply_candidate()` action=create checks whether a non-focus candidate with the same marca+modelo (case-insensitive) and compatible tipo already exists in ctx.candidates. If found, the create is redirected to an update on the existing candidate (with the target status), preventing duplicate entries when the AI mistakenly re-creates a previously-seen vehicle.

**F3-T4: Non-focus candidate list in AI prompt**

The AI system prompt now includes all non-focus candidates with their DB IDs under "Candidatos previos". The AI can reference these IDs to return action=update id=<N> status=current_focus to re-focus a prior vehicle without creating a duplicate. Rule 19 in the prompt instructs the AI to use this mechanism for returning customers.

**Questions are independent signals:**

A turn can contain a state mutation + commercial action + FAQ(s). None silently erases another. After the primary commercial handler runs, `_compose_secondary_answers()` (see §9) appends any unanswered FAQ signals from the burst. For vehicle/location mutations + FAQ in the same burst, the mutation is applied first (candidate created/updated, state reconciled, price recomputed), then the FAQ is appended to the final reply.

**Derived state invalidation:**

- Vehicle tipo changes → `_compute_price_quote()` re-runs, vehicle-change guard may reset stage
- Location changes → `_compute_price_quote()` re-runs, F3-T2 guard may reset stage
- Historical candidates (status="mentioned") are NEVER deleted — they remain as audit trail
- Scheduling preferences are overwritten by each new explicit mention

**Historical candidate preservation:**

When a candidate loses focus (demoted from "current_focus" to "mentioned"), it is NOT deleted from the DB. It remains as historical evidence and may be re-focused by a later turn using action=update. The `_reload_active_candidates()` query returns all candidates for the current cycle regardless of status.

---

## 10. FAQ Sources

`[CURRENT — WILD-04R-F3]`

FAQ answers used by `_compose_secondary_answers()` are defined as module-level constants in `backend/app/services/conversation_engine.py` (lines 736–762).

**Detection frozensets** (matched against normalized burst text):

| Signal | Constant | Sample triggers |
|---|---|---|
| Hours | `_HOURS_FAQ_DETECTION` | "horarios", "horario de atencion", "en que horarios" |
| Report | `_REPORT_FAQ_DETECTION` | "mandan informe", "informe de la revision" |
| Presence | `_PRESENCE_FAQ_DETECTION` | "tengo que estar presente", "hay que estar presente" |
| Payment | `_PAYMENT_FAQ_DETECTION` | "aceptan efectivo", "mercado pago", "como se paga" |

**Canonical answer constants:**

| Constant | Text |
|---|---|
| `_FAQ_HOURS_ANSWER` | "Trabajamos de lunes a viernes de 9 a 18 hs y los sábados de 9 a 15 hs." |
| `_FAQ_REPORT_ANSWER` | "Al finalizar la revisión te enviamos un informe detallado." |
| `_FAQ_PRESENCE_ANSWER` | "No es necesario que estés presente durante la inspección." |
| `_FAQ_PAYMENT_ANSWER` | "Aceptamos efectivo, transferencia bancaria y Mercado Pago." |

**Probe constants** (F3 — checked against normalized primary reply to prevent double-answers):

| Signal | Probe constant | Key phrase |
|---|---|---|
| Hours | `_FAQ_HOURS_PROBE` | `"lunes a viernes"` |
| Report | `_FAQ_REPORT_PROBE` | `"informe"` |
| Presence | `_FAQ_PRESENCE_PROBE` | `"presente"` |
| Payment | `_FAQ_PAYMENT_PROBE` | `"efectivo"` |

If the probe phrase is found in the normalized primary reply (whether AI-composed or deterministic), `_compose_secondary_answers()` skips that FAQ signal. This prevents double-answers when the AI already addressed the question in its natural reply.

**Important:** Debit and credit card are **not** accepted. `_FAQ_PAYMENT_ANSWER` lists only cash, bank transfer, and Mercado Pago. Debit references in a burst do not enable debit as a payment method; the canonical answer is returned regardless.

**Source-of-truth audit (WILD-04R-F2 pre-build verification, confirmed F3):**

- Hours and presence: appear **only** in these CE constants. Not in AI prompt.
- Report and payment: appear in these constants AND in AI system prompt rules 17 and 18, respectively. Wording differs; facts are consistent. No conflicting copies.

Do not add duplicate constants elsewhere. `_compose_secondary_answers()` called from `_send_text_to_wa()` is the single injection point for deterministic FAQ content across all commercial-progression replies (see Section 9 for the full F3 mechanism). `_build_faq_supplement()` has no call sites and is retained as dead code for reference only.

---

## 11. Pricing

`[CONTRACT]`

`PricingService` is the sole authority for inspection quotes. AI does not invent prices. No FAQ or AI path may modify a `PricingService`-computed price.

`[CURRENT]`

**Inputs to `_compute_price_quote()`:**

| Input | Source |
|---|---|
| `tipo_vehiculo` | Candidate field (e.g., `SUV_4X4_DEPORTIVO`, `AUTO`, `CAMIONETA`) |
| `zone_group` | `state.home_zone_group` (e.g., `CABA`, `Sur`, `Norte`) |
| `zone_detail` | `state.home_zone_detail` (e.g., `Berazategui`, `Pilar`, `Balvanera`) |

**Computation:**

```
base_price = PricingRepository.find_base_price(tipo_vehiculo)
viaticos   = PricingRepository.find_zone_by_group_and_detail(db, zone_group, zone_detail)
total      = base_price + (viaticos.viaticos if viaticos else 0)
```

**Worked example — Peugeot 2008 / 2014, Berazategui:**

```
tipo_vehiculo  = SUV_4X4_DEPORTIVO
base_price     = $150,000
zone_group     = Sur
zone_detail    = Berazategui
viaticos       = $90,000
────────────────────────
total          = $240,000
```

**Invariants:**
- `_build_faq_supplement()` appends text after the quote; it does not alter the numeric value.
- AI (OpenAI) is not invoked to generate or modify prices.
- If `PricingService` cannot compute a quote (missing zone, no base price record), CE does not send a price — it asks for the missing information.

---

## 12. Response Source / AI Provenance

`[CURRENT]`

Every CE response is tagged with the authoritative source of its business content.

**Primary `answer_source` values:**

| Value | Meaning |
|---|---|
| `PRICING_SERVICE` | Quote generated by `PricingService`; AI may render delivery language |
| `FAQ_RULE` | Response from `_handle_general_information_ai()` (Layer D FAQ path); AI renders canonical FAQ content |
| `SCHEDULING_SERVICE` | Slot availability or provisional scheduling response |
| `VEHICLE_RESOLVER` | Vehicle catalog lookup / fuzzy match confirmation step |
| `DETERMINISTIC_RULE` | Static gate: motorcycle handoff, phone escalation, service boundary, inspectability gate, location contradiction |
| `CE_AI` | OpenAI call is the primary source; not one of the above deterministic paths |
| `FLOW_RESPONSE` | WhatsApp booking Flow response processed deterministically |
| `ERROR_FALLBACK` | CE error path; reply may or may not have been sent |
| `HUMAN` | `needs_human` active; CE suppressed; human expected to reply |

**`contributing_sources`:**

When multiple services contribute to one reply, secondary sources are listed here.

Example — quote + hours:
```
answer_source        = PRICING_SERVICE
contributing_sources = ["FAQ_RULE"]
ai_invoked           = true
```

**`ai_invoked`:**

`true` if `_call_openai()` was called during this turn, regardless of whether the AI's output was the primary reply content. A pricing reply with AI-rendered delivery language sets `ai_invoked=true` and `answer_source=PRICING_SERVICE`.

**Where these are set:**

Tags are set at the call site in CE (e.g., `self._answer_source = "PRICING_SERVICE"` before sending). At the end of `handle()`, the priority resolution applies:
1. Inline `answer_source` on `_out()` call
2. Unambiguous action→source inference (e.g., `booking_created` → `FLOW_RESPONSE`)
3. `self._answer_source` (set during handler)
4. `CE_AI` (fallback when AI was invoked but no explicit tag was set)

`[KNOWN GAP]`

Not all `_out()` call sites have been tagged with explicit `answer_source`. Full per-call-site tagging is a deferred second pass. The inference rules at step 2 and 4 cover the common paths.

---

## 13. Outbound Safety

`[CONTRACT]`

CE generating a reply and a customer successfully receiving it are distinct events. The outbound safety gate sits between them and can block any send.

`[CURRENT]`

**Gate: `_send_text_to_wa()`** calls `outbound_safety_gate.py` before every WhatsApp API call.

**Kill switch:** `OUTBOUND_ENABLED` environment variable. If `!= "true"`, all outbound sends are blocked with `OutboundBlockedError`. CE still computes the full logical result and commits conversation state (so the cycle advances correctly). The blocked event is recorded. Action: `blocked_dispatch`.

**Allowlist:** `CLOSED_BETA_ALLOWED_WA_IDS` restricts recipients in closed-beta deployments. A number not on the allowlist is blocked.

**Content fingerprint dedup:** Prevents the same message from being sent twice to the same recipient in a short window. Guards against CE retries and concurrent CE calls.

**Current deployment state:**

| Environment | `OUTBOUND_ENABLED` | DB | Allowlist |
|---|---|---|---|
| Production | `true` | `crm` | None |
| Closed-beta | `false` (currently) | `crm_test` | `5491153368330` |

Outbound is currently `false` in closed-beta. No customer messages are being sent. Owner re-authorization is required before enabling.

---

## 14. Telemetry and Performance

`[CURRENT]`

Per-turn observability fields are stored in `ai_events` (written by `api/conversation.py` after CE returns).

**Reply classification fields:**

| Field | Type | Meaning |
|---|---|---|
| `reply_required` | bool | True unless `skipped_dedup`, `no_lead`, `skipped_human`, or `error(thread_not_found)` |
| `reply_produced` | bool | True for `replied`, `flow_button_sent`, `booking_created` |
| `alert_eligible` | bool | True when `reply_required` is true (SLA alert applies) |

**Answer provenance:**

| Field | Type | Meaning |
|---|---|---|
| `answer_source` | str | Primary source (see §12) |
| `contributing_sources` | str | Secondary sources (comma-separated, e.g., `FAQ_RULE`) |
| `ai_invoked` | bool | Whether `_call_openai()` was called |

**Burst fields:**

| Field | Type | Meaning |
|---|---|---|
| `burst_message_count` | int | Number of DB messages in the reconstructed burst |
| `burst_earliest_inbound_db_id` | int | `WhatsAppMessage.id` of the oldest burst message |

**Latency fields:**

| Field | Measurement |
|---|---|
| `latency_ce_ms` | CE processing time (`perf_counter()` inside `handle()`) |
| `latency_total_ms` | `AiEvent.created_at` (webhook arrival) → CE finish (`datetime.now(utc)`) |
| `latency_debounce_ms` | `latency_total_ms - latency_ce_ms` — n8n debounce + dispatch overhead |

**Performance classification (`performance_status`):**

| `latency_total_ms` | `performance_status` |
|---|---|
| ≤ 60,000 ms | `OK` |
| > 60,000 and ≤ 120,000 ms | `MEDIUM` |
| > 120,000 ms | `ALERT` |
| `reply_required=true`, no reply after 120s | `ALERT` |
| `reply_required=false` | `NO_REPLY_REQUIRED` |

**SLA alert:**

`unanswered_alert.py` runs on a 60-second polling loop. It queries `ai_events` for turns where:
- `reply_required = true`
- `alert_eligible = true`
- `reply_produced` is not true
- `unanswered_alert_sent_at IS NULL`
- `created_at < NOW() - 120 seconds`

On match: sends one SMTP alert to `ridecheckassistance@gmail.com` and sets `unanswered_alert_sent_at`. Alert fires at the 120–180 second window (120s threshold + up to 60s polling lag). A late reply does not retract an alert that has already fired, but the `performance_status` field records the classification permanently.

---

## 15. Failure Paths

`[CURRENT]`

**`skipped_dedup`**

- Trigger: `state.last_processed_inbound_wa_message_id == event.wa_message_id`
- `reply_required`: false
- `reply_produced`: false
- Who owns next action: nobody — this is idempotent silence
- Alert: none
- Also emitted when CE receives a message with no text content (`detail="no_text"`) or when a pure-FAQ AI turn produces no new reply (`detail="faq_no_reply"`)

**`no_lead`**

- Trigger: `ctx.lead is None` — thread has no linked Lead
- `reply_required`: false
- `reply_produced`: false
- Who owns next action: n8n's lead-find/create step should have linked a lead; this is an n8n/infrastructure gap
- Alert: none — not a CE SLA failure

**`skipped_human`**

- Trigger: `state.needs_human = True` (after cycle reset check)
- `reply_required`: false
- `reply_produced`: false
- `answer_source`: `HUMAN`
- Who owns next action: human operator
- Alert: separate human-handoff alert path (legacy thread-level check); no CE SLA alert for `skipped_human`

**`blocked_dispatch`**

- Trigger: `OutboundBlockedError` raised by outbound safety gate (kill switch)
- `reply_required`: true
- `reply_produced`: false
- `ok`: false
- Who owns next action: human operator (kill switch is intentional suppression)
- Alert: alert-eligible (CE attempted a reply but was blocked)
- State: CE state is committed (cycle advances, dedup cursor advances)

**`error` / `detail="thread_not_found"`**

- Trigger: `_load_context()` returns None (no matching thread in DB)
- `reply_required`: false (routing miss — customer was never expected a CE reply)
- `reply_produced`: false
- Alert: none

**`error` / `detail="internal_error"`**

- Trigger: unhandled exception in CE
- `reply_required`: true
- `reply_produced`: false
- `answer_source`: `ERROR_FALLBACK`
- Who owns next action: human operator or n8n retry
- Alert: alert-eligible (customer is waiting; CE failed)
- State: CE session rolled back — dedup cursor NOT advanced (allows retry)

**Flow failure / `ATENCION_HUMANA`**

- Trigger: booking Flow submission fails or is invalid
- CE sets `lead.estado = ATENCION_HUMANA`, `state.needs_human = True`
- `answer_source`: `DETERMINISTIC_RULE`
- Who owns next action: human operator must review and either resolve or reset to `CONSULTA_NUEVA`

---

## 16. Worked Examples

---

### Example A — Simple: Ford Focus 2019 in Pilar

**Messages (one burst):**
```
"Encontré un Focus 2019 en Pilar. ¿Cuánto sale la revisión?"
```

**Burst reconstruction:**
- 1 message. `burst_message_count = 1`

**Evidence extraction:**
- `lookup_vehicle("Focus 2019")` → Ford Focus, year 2019
- `_detect_vehicle_location_phrase("pilar")` → True (known zone)
- Vehicle type resolved → `AUTO` (Ford Focus is not SUV)
- Zone resolved → Norte / Pilar, viaticos fetched from `ViaticosZone`
- Candidate created: Ford Focus / 2019 / Norte / Pilar

**State changes:**
- `WhatsAppThreadCandidate` created with `status='current_focus'`
- `state.home_zone_group = Norte`, `state.home_zone_detail = Pilar`
- `state.last_stage = QUOTED`
- `lead.flag = PRESUPUESTO_ENVIADO`

**Route:** Deterministic pricing path. `PricingService` computes base + viaticos.

**Reply:**
```
Genial! La cotización para la revisión del Ford Focus 2019 es de $[X].
Si te parece bien, podemos avanzar.
```

**Telemetry:**
```
answer_source        = PRICING_SERVICE
contributing_sources = null
ai_invoked           = false (if deterministic path did not call AI)
burst_message_count  = 1
reply_required       = true
reply_produced       = true
```

---

### Example B — Initial Mixed Burst: Vehicle + Multi-FAQ

**Messages (one burst, three messages):**
```
M1: "Hola, ¿cómo andan? Quería revisar un 2008 del 2014. Ustedes hacen eso, ¿no?"
M2: "¿Mandan informes también? ¿Tengo que estar presente?"
M3: "¿Cómo se paga? ¿Aceptan débito?"
```

**Burst reconstruction:**
- 3 messages. `burst_message_count = 3`
- `current_turn_text` = M1 + M2 + M3 joined

**Evidence extraction:**
- `lookup_vehicle("2008 del 2014")` → Peugeot 2008, year 2014
- "Peugeot 2008" → `SUV_4X4_DEPORTIVO`
- No zone detected in burst
- Layer D FAQ guard: vehicle found → NOT routed to FAQ fast-path
- Full AI path: candidate created (`status='current_focus'`)
- AI asked to address FAQ questions + ask for zone

**State changes:**
- `WhatsAppThreadCandidate` created: Peugeot 2008 / 2014 / SUV_4X4_DEPORTIVO
- `state.last_stage = QUALIFYING`
- `lead.flag = PRESUPUESTANDO`

**Route:** CE_AI path. AI invoked.

**Reply (AI-generated, example):**
```
¡Hola! Sí, hacemos revisiones preventa de vehículos — es justo para eso.

Al finalizar la revisión te enviamos un informe detallado. No es necesario
que estés presente durante la inspección. Aceptamos efectivo, transferencia
bancaria y Mercado Pago (no débito ni crédito).

Para cotizarte la revisión del Peugeot 2008 2014, ¿en qué zona o barrio
está el auto?
```

**Telemetry:**
```
answer_source        = CE_AI
contributing_sources = null
ai_invoked           = true
burst_message_count  = 3
reply_required       = true
reply_produced       = true
```

`[NOTE]` When zone is unknown, `_build_faq_supplement()` does not fire (it is called only in the deterministic pricing path). The AI is expected to answer FAQ signals from the burst as part of its qualifying response.

---

### Example C — Pricing + FAQ Composition (Exact F2 Scenario)

**Messages (one burst, two messages):**
```
M1: "El auto está en Berazategui."
M2: "¿En qué horarios laburan?"
```

**Pre-existing state:** Peugeot 2008 / 2014 candidate (`status='current_focus'`). Zone not yet set.

**Burst reconstruction:**
- 2 messages. `burst_message_count = 2`

**Evidence extraction:**
- `_detect_vehicle_location_phrase("berazategui")` → True
- Zone resolved → Sur / Berazategui
- Layer D FAQ guard: location phrase present → NOT routed to FAQ fast-path
- Deterministic pricing path fires

**State changes:**
- `state.home_zone_group = Sur`, `state.home_zone_detail = Berazategui`
- `state.last_stage = QUOTED`
- `lead.flag = PRESUPUESTO_ENVIADO`

**Response composition:**
- `PricingService`: base $150,000 + viaticos $90,000 = **$240,000**
- `_build_faq_supplement("... en berazategui ... horarios ...")` detects "horarios" → `_FAQ_HOURS_ANSWER`
- Quote + hours appended

**Exact reply:**
```
Genial! La cotización para la revisión del Peugeot 2008 2014 es de $240.000.
Si te parece bien, podemos avanzar.

Trabajamos de lunes a viernes de 9 a 18 hs y los sábados de 9 a 15 hs.
```

**Telemetry:**
```
answer_source        = PRICING_SERVICE
contributing_sources = ["FAQ_RULE"]
ai_invoked           = false
burst_message_count  = 2
reply_required       = true
reply_produced       = true
```

---

### Example D — Returning Customer: New Cycle After Prior Bookings

**Prior history (preserved in DB, NOT in active context):**
- Cycle 1: Peugeot 2008 (archived), Balvanera, booked and completed
- Cycle 2: Peugeot 2008 2014 (archived), Berazategui, booked and completed

**Human action:** Operator moves Lead to `CONSULTA_NUEVA`.
- `set_lead_estado()` detects transition from `REVISION_COMPLETA → CONSULTA_NUEVA`
- `state.cycle_reset_pending = True`

**Customer message:**
```
"Encontré otro auto. Es un Focus 2019 en Pilar. ¿Cuánto sale?"
```

**CE receives event:**

1. `_load_context()` runs — loads old watermarks. Old candidates and messages may be in ctx.
2. Dedup check: passes (new message).
3. `state.cycle_reset_pending = True` → `_execute_cycle_reset()`:
   - New watermarks set to this message's `id` and `created_at`
   - All ACTIVE_REVISION fields cleared (zone, stage, focus candidate, etc.)
   - `cycle_reset_pending = False`
   - Committed
4. **F2 post-reset reload:**
   - `ctx.candidates = _reload_active_candidates(...)` → empty (no candidates created yet this cycle)
   - `ctx.db_messages = _query_active_messages(...)` → only current message (no prior-cycle messages)
5. `needs_human = False` (cleared at reset) → no human gate.
6. `_process_text()` runs with clean context.
7. Vehicle "Focus 2019" detected → new `WhatsAppThreadCandidate` created.
8. "Pilar" detected → Norte / Pilar zone set.
9. Pricing computed. Reply sent.

**What the AI sees:** Only the current message ("Encontré otro auto..."). No Peugeot, no Balvanera, no Berazategui, no prior quote, no prior booking confirmation.

**What the DB contains:** All prior messages, all prior candidates, all prior `ThreadRevision` and `Revision` rows — preserved and visible to human operators. Not deleted. Not in CE context.

**Telemetry:**
```
answer_source        = PRICING_SERVICE
contributing_sources = null
burst_message_count  = 1
reply_required       = true
reply_produced       = true
```

---

## 17. Hard Invariants

These are non-negotiable constraints. No feature, fix, or refactor may violate them.

1. **Inbound message persisted before CE reasoning.** A customer message is committed to `whatsapp_messages` before CE is invoked. CE failure does not destroy the message.

2. **Historical context is preserved, not deleted.** No `WhatsAppMessage`, `WhatsAppThreadCandidate`, `ThreadRevision`, or `Revision` row is ever deleted for semantic isolation purposes.

3. **Active-cycle boundaries isolate semantic context.** CE reads only current-cycle candidates and messages. Prior-cycle data stays in the DB but does not enter `ctx.candidates` or `ctx.db_messages`.

4. **Same Contact / Thread / Lead persist across inspection cycles.** One customer phone number = one `WhatsAppContact` = one `WhatsAppThread` = one `Lead`, permanently.

5. **Human CONSULTA_NUEVA transition is the cycle boundary signal.** CE does not auto-detect or auto-trigger cycle resets. Only a real human transition via `set_lead_estado()` sets `cycle_reset_pending`.

6. **Facts extracted from a burst cannot be erased by the response routing path.** Vehicle and location evidence is persisted before the reply is assembled.

7. **Questions in a burst cannot be silently erased by another route.** If a burst contains a pricing trigger and an FAQ question, the response must address both. (F3 invariant: `_compose_secondary_answers()` inside `_send_text_to_wa()` enforces this in the deterministic pricing path.)

8. **Known information must not be re-asked.** Once a vehicle or zone is persisted in the current cycle, CE must not ask the customer to repeat it.

9. **AI cannot invent prices.** All quotes come from `PricingService`. No AI response path may override or supplement a pricing figure.

14. **Active candidate zone is authoritative for all customer-facing location output.** `_get_active_inspection_location(ctx, state)` returns the candidate's zone when present; `state.home_zone_*` is a fallback only. Never read `state.home_zone_detail` directly for display, scheduling, AI prompt, or notifications — use the accessor so pricing and display always agree. (F4 invariant: pricing-zone == display-zone at all times.)

10. **Every reply-required turn is observable.** `ai_events` records `reply_required`, `reply_produced`, and `alert_eligible` for every CE turn. No reply-required turn is invisible to the SLA alert.

11. **Human alert threshold is 120 seconds.** A reply-required turn with no reply produced within 120 seconds triggers an SMTP alert to `ridecheckassistance@gmail.com`.

12. **A historical candidate must never become the active focus for a new cycle.** `_focus_candidate()` operates on `ctx.candidates`, which is filtered by the cycle watermark. A prior-cycle candidate cannot enter `ctx.candidates` and therefore cannot be returned as the focus.

13. **Post-reset context reload is mandatory.** After `_execute_cycle_reset()`, both `ctx.candidates` and `ctx.db_messages` must be re-queried with the new watermarks before any CE routing logic runs. (F2 invariant.)

---

## 18. Traceability

| Milestone | Lesson / Change |
|---|---|
| **WILD-04** | Live test failure. Three confirmed defects: (1) prior-cycle candidate (`status='archived'`) leaked into `_focus_candidate()` via `ctx.candidates[0]` fallback; (2) `_load_context()` used `ORDER BY timestamp ASC LIMIT 20`, returning exclusively prior-cycle messages for a returning customer; (3) n8n sub-burst fragmentation dropped the inspection-intent message from `unanswered_recent_user_messages`, causing CE to route to FAQ path and create no candidate. |
| **WILD-04R** | Owner-corrected architecture audit. Established: same Contact/Thread/Lead is canonical; `cycle_reset_pending` explicit signal (not state inference); `WhatsAppMessage.id` cursor for burst boundaries; candidate and message watermarks (`current_cycle_start_message_db_id`, `current_cycle_started_at`); newest-20 message ordering fix; ACTIVE_REVISION field clear list. |
| **WILD-04R-F1** | DB-authoritative burst completeness guard in `_process_text()`. CE now prepends any DB messages absent from n8n payload, making burst context independent of n8n message-fetch limit. Vehicle evidence from burst-leading messages is no longer silently dropped. |
| **WILD-04R-F2** | Two fixes: (A) Post-reset context reload — `_load_context()` runs before `_execute_cycle_reset()` and carried old-cycle data into the first new-cycle turn, blocking new candidate creation; fixed by explicit `_reload_active_candidates()` + `_query_active_messages()` after reset. (B) FAQ composition in pricing path — deterministic quote override was replacing the full reply, silently dropping FAQ signals from the same burst; fixed by `_build_faq_supplement()` appending canonical FAQ answers after the quote. |
| **WILD-04R-F3** | Turn Reconciliation. Extended from the F3 FAQ-preservation live failure (`["Okay !", "¿Qué horarios hacen?"]` dropped hours). Root cause family: any commercial handler could silently discard FAQ signals or fail to reconcile vehicle/location mutations with quote state. Fix set: (1) `_compose_secondary_answers()` unified pre-outbound FAQ reconciliation via `_faq_reconciliation_burst`; (2) Layer D guard: QUOTED+acceptance bursts bypass FAQ fast-path; (3) F3-T2 zone-correction re-quote guard: zone change in QUOTED/SCHEDULING → reset to QUALIFYING for re-price; (4) F3-T3 candidate dedup in `_apply_candidate()` action=create: same marca+modelo redirects to update existing; (5) F3-T4 non-focus candidates surfaced in AI prompt with IDs for re-focus; (6) AI prompt Rule 19: instruct AI to re-focus prior candidates by ID instead of creating duplicates. Test suite: M1–M10 messy conversation scenarios in `tests/test_messy_turn_reconciliation.py`. |
| **WILD-04R-F4** | Active Candidate Location Authority. Defect: `_build_quote_reply` and all display/scheduling/notification sites read `state.home_zone_detail` directly; `_compute_price_quote` (LR-1) reads candidate zone first. These disagreed when the active candidate had a different zone than state (e.g. Focus/Palermo shown as "San Miguel" because state was set from a prior Peugeot candidate). Fix: new `_get_active_inspection_location(ctx, state)` accessor returns `(zone_group, zone_detail)` with candidate-first precedence, mirroring LR-1. All customer-facing display sites (quote reply, scheduling, AI prompt, escalation/human-review notifications, CRM Revision, ThreadRevision) now use this accessor. `state.home_zone_*` is never proactively synced from candidate (Option A — fallback only). Test suite: Cases A–E + pricing/display consistency invariant in `tests/test_wild04r_f4_location_authority.py`. |

---

## 19. Required Reading

This document should be read before modifying any of the following:

| Area | Read before touching |
|---|---|
| WhatsApp webhook / n8n debounce | §3, §4, §5 (audio transcription, burst assembly, concurrency) |
| Message persistence | §2 (persistence-first contract, DB ID ordering) |
| Conversation Engine (CE) | §6–§15 (entire document applies) |
| Cycle reset / returning customer | §6, §7, §17 (invariants 3–5, 12–13) |
| Context loading (`_load_context`) | §7 (watermarks, active vs historical) |
| Burst assembly (`_process_text`) | §4, §8 (DB-authoritative burst, evidence extraction) |
| Routing layers | §8, §9 (evidence-first, composition-second) |
| Response composition | §9, §9.5, §10, §11 (FAQ supplement, turn reconciliation, pricing authority) |
| Turn reconciliation (vehicle/location/scheduling mutation) | §9.5 (mutable domains, correction semantics, dedup, re-quote guard) |
| Outbound | §13 (safety gate, kill switch, allowlist) |
| Telemetry / SLA | §14, §15 (fields, thresholds, failure paths) |

---

*Cross-doc consistency verified against `DOMAIN_MODEL.md` and `CONVERSATION_RUNTIME_CONTRACT.md` at time of writing. No contradictions found. If this document conflicts with either of those, treat the conflict as a documentation defect and report it — do not silently update any document to match code.*
