# RideCheck CRM — Canonical Domain Model

**Status:** AUTHORITATIVE BUSINESS CONTRACT  
**Established by:** WILD-04R Architecture Correction audit (2026-08-24)  
**Supersedes:** Any architectural inference made solely from database cardinality

> **CRITICAL:** If code or DB schema appears to conflict with this document, **STOP**. Do not silently update this document to match the code. Report the conflict as an architecture defect requiring owner decision. See anti-patterns section.

---

## 1. Purpose

This document defines the owner-authoritative business domain model for RideCheck CRM. It exists because:

1. Database foreign key cardinality can be misread as business requirements. The schema permits patterns that the business explicitly prohibits (e.g., multiple Leads per customer).
2. Future developers and AI agents have repeatedly inferred incorrect ownership models from DB schema alone.
3. The product's multi-inspection-per-customer lifecycle requires explicit documentation to prevent state-isolation defects (the root cause of WILD-04).

The domain model is a **business contract**, not a database schema description.

---

## 2. Core Entities

### WhatsAppContact

| Property | Value |
|---|---|
| Table | `whatsapp_contacts` |
| Identity key | `wa_id` (unique — the customer's WhatsApp phone number) |
| Persistence | **Permanent** — created once per person, never replaced |
| Cardinality | One per customer phone number |
| Owner scope | PERSON (survives all inspection cycles) |
| Mutable? | Display name only |

**Business meaning:** The permanent digital identity of the customer. One person = one `WhatsAppContact` for the lifetime of the product relationship.

---

### WhatsAppThread

| Property | Value |
|---|---|
| Table | `whatsapp_threads` |
| FK | `contact_id → whatsapp_contacts.id` (CASCADE) |
| Persistence | **Permanent** — created once per contact per channel, never replaced |
| Cardinality | One per `WhatsAppContact` (current implementation) |
| Owner scope | PERSON (survives all inspection cycles) |
| Mutable? | `last_message_at`, `unread_count`, `lead_id` linkage |

**Business meaning:** The permanent conversation record between RideCheck and this customer on this channel. A customer who buys and inspects 5 cars over 3 years uses the **same Thread** for all of them.

`WhatsAppThread.lead_id` links the thread to the CRM Lead. This link is established once by n8n's lead-find/create step and persists.

---

### Lead

| Property | Value |
|---|---|
| Table | `leads` |
| FK | Referenced by `whatsapp_threads.lead_id` (nullable) |
| Persistence | **Permanent** — created once per customer, never replaced |
| Cardinality | One per customer relationship |
| Owner scope | PERSON for identity fields; ACTIVE_REVISION for pipeline state fields |
| Mutable? | Pipeline state fields reset between cycles; identity fields persist |

**Business meaning:** The CRM commercial record for this customer. Accumulates commercial history across all inspection cycles. `Lead.estado` and `Lead.flag` track the current pipeline position within an active inspection cycle.

**DATABASE CARDINALITY DOES NOT OVERRIDE THIS BUSINESS CONTRACT.**

The schema permits multiple Leads per phone number. This is NOT the intended model. One customer = one Lead, always.

---

### WhatsAppThreadState

| Property | Value |
|---|---|
| Table | `whatsapp_thread_states` |
| FK | `thread_id → whatsapp_threads.id` (CASCADE, UNIQUE) |
| Persistence | **Permanent** — one-to-one with thread, never replaced |
| Cardinality | Exactly one per Thread |
| Owner scope | Mixed — see field ownership matrix |
| Mutable? | YES — the primary mutable CE state record |

**Business meaning:** The Conversation Engine's working memory for this thread. Contains the current state of the active inspection cycle. Fields are classified as PERSON_THREAD (survive cycles) or ACTIVE_REVISION (must be reset at each cycle boundary). See field ownership matrix.

---

### WhatsAppThreadCandidate

| Property | Value |
|---|---|
| Table | `whatsapp_thread_candidates` |
| FK | `thread_id → whatsapp_threads.id` (CASCADE) |
| Persistence | **Historical** — never deleted |
| Cardinality | Many per Thread (one per vehicle mentioned across all cycles) |
| Owner scope | ACTIVE_REVISION |
| Mutable? | `status`, `updated_at` |

**Business meaning:** A vehicle the customer has mentioned as a candidate for inspection. Each cycle produces one or more candidate records. Historical candidates from prior cycles remain in the DB but must not influence the active cycle's context.

`WhatsAppThreadCandidate.created_at` (DB server clock) is the temporal boundary key for cycle isolation (planned WILD-04R implementation).

---

### ThreadRevision

| Property | Value |
|---|---|
| Table | `thread_revisions` |
| FK | `thread_id → whatsapp_threads.id`, `candidate_id → whatsapp_thread_candidates.id` (SET NULL on delete) |
| Persistence | **Historical** — immutable after creation |
| Cardinality | Many per Thread (one per completed or provisional inspection booking) |
| Owner scope | HISTORICAL_REVISION |
| Mutable? | NO — snapshot at booking time |

**Business meaning:** The WhatsApp-channel booking record for one inspection appointment. Created when the customer submits the booking Flow. Captures: vehicle, location, buyer data, scheduled date/time, appointment token. Linked to the candidate that was in focus at booking time.

`ThreadRevision.created_at` (DB server clock, `server_default=func.now()`) is a reliable timestamp watermark for revision cycle boundaries.

Status values (CHECK constraint): `draft`, `collecting_data`, `booked`, `completed`, `provisional`

Current implementation creates `ThreadRevision` only at booking (`status='booked'`) or scheduling escalation (`status='provisional'`). The pre-booking inspection cycle (qualifying, quoting, scheduling) exists only in `WhatsAppThreadState` and `WhatsAppThreadCandidate` — no ThreadRevision row exists until booking.

---

### Revision

| Property | Value |
|---|---|
| Table | `revisions` |
| FK | `lead_id → leads.id` |
| Persistence | **Historical** — immutable CRM record after creation |
| Cardinality | Many per Lead (one per completed or provisional inspection booking) |
| Owner scope | HISTORICAL_REVISION |
| Mutable? | Pricing fields may be recalculated; appointment outcome fields set by human |

**Business meaning:** The CRM-side inspection record. Paired with `ThreadRevision` at booking (created in the same transaction), but with no direct FK between them. Contains pricing, professional assignment, payment, and outcome. Human operators manage this record post-booking.

**There is no FK between `ThreadRevision` and `Revision`.** They are paired by creation timing, not by a stored relationship.

---

### WhatsAppMessage

| Property | Value |
|---|---|
| Table | `whatsapp_messages` |
| FK | `thread_id → whatsapp_threads.id` (CASCADE) |
| Persistence | **Permanent** — messages are never deleted |
| Cardinality | Many per Thread (all messages across all inspection cycles) |
| Owner scope | HISTORICAL — messages belong to the cycle in which they occurred |
| Mutable? | `status` (delivery status); `automated` flag |

**Business meaning:** Every inbound and outbound message in this thread. `timestamp` is the Meta-provided clock (customer's send time). `created_at` is the DB server clock at insert time. No `revision_id` field exists — messages cannot be directly queried by cycle without a timestamp/cursor boundary.

---

## 3. Authoritative Relationship Model

```
WhatsAppContact   (permanent person identity — one per customer phone)
  │
  └── WhatsAppThread   (permanent conversation — one per contact per channel)
       │
       ├── WhatsAppThreadState   (1-to-1, mutable CE working memory)
       │
       ├── WhatsAppThreadCandidate[]   (all vehicles mentioned, all cycles)
       │    ├── Candidate A  (Peugeot 2008, created 2026-08, Cycle 1)
       │    ├── Candidate B  (Peugeot 2008 2014, created 2026-08, Cycle 2)
       │    └── Candidate C  (Ford Focus 2019, created 2026-08, Cycle 3)
       │
       ├── ThreadRevision[]   (one per completed booking per cycle)
       │    ├── ThreadRevision 1  → Candidate A  (booked, Cycle 1)
       │    ├── ThreadRevision 2  → Candidate B  (booked, Cycle 2)
       │    └── ThreadRevision 3  → (new cycle, not yet booked)
       │
       └── WhatsAppMessage[]   (all messages, all cycles, in timestamp order)
            ├── Messages 001–028   (Cycle 1, 2026-08)
            ├── Messages 029–055   (Cycle 2, 2026-08)
            └── Messages 056+      (Cycle 3, active)

Lead   (permanent CRM record — one per customer, linked from WhatsAppThread.lead_id)
  │
  └── Revision[]   (one per completed booking per cycle — CRM side)
       ├── Revision 1  (Cycle 1 booking, historical)
       ├── Revision 2  (Cycle 2 booking, historical)
       └── Revision 3  (Cycle 3 booking, when made)
```

**Key structural facts:**
- `WhatsAppThread.lead_id` is nullable — thread may exist before lead is linked
- `ThreadRevision.candidate_id` is SET NULL on delete — revision survives candidate deletion
- `Revision.lead_id` links to Lead — there is no FK between `Revision` and `ThreadRevision`
- `WhatsAppMessage` has no `revision_id` — cycle assignment is by timestamp/cursor boundary only

**DATABASE CARDINALITY DOES NOT OVERRIDE THIS BUSINESS CONTRACT.**

---

## 4. Field Ownership Matrix

Fields are classified by their lifecycle scope:

- **PERSON** — survives all inspection cycles; tied to the person's identity
- **THREAD** — tied to the conversation lifetime; de-facto permanent for this channel
- **ACTIVE_REVISION** — scoped to one inspection cycle; must be reset at cycle boundary
- **HISTORICAL_REVISION** — written once at booking; immutable; must not be read for new cycle context
- **ATTRIBUTION** — first-touch marketing; written once; never reset

### Lead fields

| Field | Classification | Notes |
|---|---|---|
| `nombre` | PERSON | Customer's first name; retained across cycles |
| `apellido` | PERSON | Customer's last name; retained across cycles |
| `email` | PERSON | Contact email; retained across cycles |
| `telefono` | PERSON | Contact phone; retained across cycles |
| `canal` | THREAD | Acquisition channel surface; set at first contact |
| `ref_code` | ATTRIBUTION | First-touch marketing ref (ga/ig/fb/org/dir/otro) |
| `rc_code` | ATTRIBUTION | Website session code RC-XXXX; first-touch only |
| `compro_el_auto` | PERSON | Whether customer bought the car; human-managed |
| `motivo_perdida` | ACTIVE_REVISION | Reason for lost deal; valid only when flag=PERDIDO |
| `estado` | ACTIVE_REVISION | Pipeline position; human resets to CONSULTA_NUEVA between cycles |
| `flag` | ACTIVE_REVISION | Commercial stage within cycle (PRESUPUESTANDO → ACEPTADO) |
| `necesita_humano` | ACTIVE_REVISION | Human takeover flag; mirrors `state.needs_human`; must be reset between cycles |
| `buscando_auto_set_at` | ACTIVE_REVISION | Deferred-interest timestamp; reset at cycle boundary |

### WhatsAppThreadState fields

| Field | Classification | Notes |
|---|---|---|
| `last_processed_inbound_wa_message_id` | THREAD | Dedup cursor; survives cycles; critical for re-processing prevention |
| `customer_name` | PERSON | Display name; retained across cycles |
| `is_website_lead` | THREAD | One-time website attribution flag |
| `unanswered_alert_sent_at` | THREAD | Cron-managed; not CE-routing-critical |
| `quote_followup_sent_at` | THREAD | Cron-managed followup state |
| `buscando_followup_sent_at` | THREAD | Cron-managed followup state |
| `last_intent` | ACTIVE_REVISION | Most recent detected intent; reset at cycle boundary |
| `last_stage` | ACTIVE_REVISION | CE conversation stage (QUALIFYING → QUOTED → SCHEDULING → BOOKED); reset at cycle boundary |
| `needs_human` | ACTIVE_REVISION | CE human-takeover suppressor; reset at cycle boundary |
| `current_focus_candidate_id` | ACTIVE_REVISION | ID of the active vehicle candidate; reset at cycle boundary |
| `current_revision_id` | ACTIVE_REVISION | ID of the booked ThreadRevision; set at booking, cleared at cycle reset |
| `home_zone_group` | ACTIVE_REVISION | Normalized inspection zone group; reset at cycle boundary (location is revision-scoped) |
| `home_zone_detail` | ACTIVE_REVISION | Inspection zone detail; reset at cycle boundary |
| `preferred_day` | ACTIVE_REVISION | Scheduling preference; reset at cycle boundary |
| `preferred_time` | ACTIVE_REVISION | Scheduling preference; reset at cycle boundary |
| `active_requested_date` | ACTIVE_REVISION | Last requested inspection date; reset at cycle boundary |
| `last_requested_time` | ACTIVE_REVISION | Last requested inspection time; reset at cycle boundary |
| `last_offered_slots` | ACTIVE_REVISION | Cached available slots; reset at cycle boundary |
| `last_visible_slots` | ACTIVE_REVISION | Slots presented to customer; reset at cycle boundary |
| `flow_booking_token` | ACTIVE_REVISION | One-time booking Flow security token; consumed at booking |
| `vehicle_clarification_sent` | ACTIVE_REVISION | One-per-cycle clarification guard; reset at cycle boundary |
| `location_clarification_sent` | ACTIVE_REVISION | One-per-cycle clarification guard; reset at cycle boundary |
| `vehicle_fallback_flow_sent` | ACTIVE_REVISION | One-per-cycle fallback guard; reset at cycle boundary |
| `location_fallback_flow_sent` | ACTIVE_REVISION | One-per-cycle fallback guard; reset at cycle boundary |
| `inspectability_clarification_sent` | ACTIVE_REVISION | One-per-cycle clarification guard; reset at cycle boundary |
| `pending_fuzzy_catalog_key` | ACTIVE_REVISION | Pending vehicle catalog confirmation key; reset at cycle boundary |
| `pending_turn_evidence_text` | ACTIVE_REVISION | Raw evidence from CONFIRM turn; paired with pending_fuzzy_catalog_key |

---

## 5. Revision Lifecycle

One inspection cycle flows through the following stages. The human operator controls the boundaries between cycles.

### CE internal stages (state.last_stage)

```
NULL / initial
  │
  ↓  (inspection signal detected)
QUALIFYING
  │
  ↓  (vehicle + zone confirmed, quote computed)
QUOTED
  │
  ↓  (acceptance detected)
SCHEDULING  (date/time coordination)
  │
  ↓  (booking Flow dispatched — WhatsApp Flow sent to customer)
[flow_booking_token set, boolean flags track Flow dispatch]
  │
  ↓  (customer submits Flow response)
BOOKED  ← ThreadRevision (status='booked') + Revision created here
         ← state.current_revision_id set here
         ← state.needs_human = True
         ← lead.estado = COORDINAR_DISPONIBILIDAD
         ← lead.flag = ACEPTADO
```

### Lead.estado lifecycle (CRM / human-managed)

```
CONSULTA_NUEVA         ← created state; also human reset state between cycles
  ↓  (CE sets after booking)
COORDINAR_DISPONIBILIDAD
  ↓  (human confirms appointment)
AGENDADO
  ↓  (inspection completed)
REVISION_COMPLETA
```

`ATENCION_HUMANA` can be set from any state (CE sets it for motorcycle/phone escalations; human can also set it).

### Lead.flag lifecycle (commercial progress within cycle)

```
NULL → PRESUPUESTANDO → PRESUPUESTO_ENVIADO → ACEPTADO → (PERDIDO | RECOMPRA)
```

### Human cycle boundary (returning customer)

```
REVISION_COMPLETA (or COORDINAR_DISPONIBILIDAD for cancelled cycles)
  │
  ↓  (human CRM action: set Lead.estado = CONSULTA_NUEVA)
CONSULTA_NUEVA   ← same Lead, same Thread, new inspection cycle begins
```

This human action is the **owner-defined cycle boundary signal**. See Returning Customer section for the full contract.

---

## 6. Returning Customer — Worked Example

**Scenario:** Lead 4 / Thread 2, customer has completed 2 prior inspection cycles.

**Prior history (preserved, immutable):**
- Revision Cycle 1: Peugeot 2008, Berazategui, booked August 2026
- Revision Cycle 2: Peugeot 2008 2014, Balvanera, booked August 2026

**Human action:**
- Human operator moves Lead 4 to `estado = CONSULTA_NUEVA` in the CRM kanban

**Customer sends (Cycle 3):**
> "Encontré otro auto. Es un Focus 2019 en Pilar. ¿Cuánto sale?"

**Required behavior:**

| Entity | Expected | Prohibited |
|---|---|---|
| WhatsAppContact | SAME (permanent) | New contact |
| WhatsAppThread | SAME (permanent) | New thread |
| Lead | SAME (permanent) | New lead |
| Vehicle in context | Ford Focus 2019 (new) | Peugeot 2008 (from prior cycles) |
| Location in context | Pilar (new) | Balvanera / Berazategui (from prior cycles) |
| Quote | New quote for Focus 2019 + Pilar zone | Prior quote |
| Active candidate | New candidate (created this cycle) | Prior candidates |
| Prior ThreadRevisions | Preserved in DB, not used in CE context | Deleted or overwritten |
| Prior Revisions | Preserved in DB, not used in CE context | Deleted or overwritten |
| CE state | All ACTIVE_REVISION fields reset | Retained from prior cycle |

The result is a new `ThreadRevision` (Cycle 3) and a new `Revision` (Cycle 3 CRM record) created when the customer books — all within the same Lead and Thread that have existed since the first inspection.

---

## 7. Invariants

These rules are hard constraints on the system. No feature or fix may violate them.

1. **Historical Revision data is immutable.** `ThreadRevision` and `Revision` rows for completed cycles are never modified after creation.

2. **Active revision data must never leak forward.** No vehicle, location, quote, appointment, or scheduling state from a prior cycle may appear in the CE context for a new cycle.

3. **Customer identity may persist.** `customer_name`, `nombre`, `apellido`, `email`, `telefono` survive all cycles. A returning customer is recognized.

4. **Historical messages remain stored.** `WhatsAppMessage` rows are never deleted. The full message history is available in the CRM for human review.

5. **Semantic context must be scoped to the active cycle.** CE must read only the messages and candidates that belong to the current inspection cycle. Historical messages/candidates must not enter the CE context.

6. **A historical candidate must not become the active focus.** `_focus_candidate()` must never return a candidate from a prior revision cycle as the active vehicle for a new cycle.

7. **Current PricingService always prices the current vehicle/location.** Quotes from prior cycles must not be reused. Every new cycle gets a fresh quote from `PricingService` using the current cycle's vehicle type and zone.

8. **Resetting Lead to CONSULTA_NUEVA is a human-owned lifecycle action.** CE must not auto-advance Lead.estado back to CONSULTA_NUEVA. Only a human operator, via the CRM UI, performs this reset.

9. **The same WhatsApp phone number always uses the same Contact, Thread, and Lead.** The `wa_id` uniqueness constraint on `WhatsAppContact` enforces this at the DB level.

---

## 8. Anti-Patterns

**DO NOT:**

- Create a new `Lead` for every inspection. One customer = one Lead.
- Create a new `WhatsAppThread` for every inspection. One customer = one Thread.
- Delete historical messages to achieve context isolation. Messages are permanent.
- Use the last N thread messages without an active-cycle boundary filter. A 3-year customer's last N messages may all be from a prior cycle.
- Infer the current inspection location from historical candidates. Location is revision-scoped.
- Infer the current vehicle from a prior `Revision` or prior `ThreadRevision`. Vehicle is revision-scoped.
- Reuse `_focus_candidate()` fallback to `ctx.candidates[0]` when candidates include prior-cycle vehicles.
- Interpret "the DB allows multiple Leads per customer" as "the business requires multiple Leads per customer." Database cardinality is not a business requirement.
- Silence an architecture conflict by updating this document to match the code.

---

## 9. Architecture Evidence / Traceability

| Milestone | Architectural lesson |
|---|---|
| **FLOW-001** | Established the WhatsApp Flow for customer data collection at booking. `ThreadRevision` is created at Flow submission. |
| **WILD-02** | First live closed-beta conversation. Validated the CE → booking pipeline end-to-end on crm_test. Established that CE, not n8n AI fallback, is the conversation processor. |
| **WILD-03** | Second live conversation. Validated scheduling and date coordination. Added evidence for the multi-turn conversation lifecycle. |
| **WILD-04** | Failed live test exposing three isolation defects: (1) prior-cycle candidate (Peugeot 2008, `status='archived'`) leaked into CE context via `_focus_candidate()` fallback; (2) oldest-20 message query returned prior-cycle messages, contaminating AI history; (3) n8n debounce sub-burst fragmentation caused the inspection intent message to be dropped from `unanswered_recent_user_messages`. Established that same Lead/Thread architecture requires explicit cycle boundary enforcement. |
| **WILD-04R** | Owner-corrected architecture audit. Confirmed: same Contact/Thread/Lead is the canonical model. Revision-scoped isolation is the required fix. `current_revision_id` IS NOT NULL is the reliable edge trigger for human cycle reset detection. `WhatsAppMessage.id` cursor and `WhatsAppMessage.created_at` are the reliable watermark fields. WILD-04R implementation is the milestone that addresses all isolation gaps. |
