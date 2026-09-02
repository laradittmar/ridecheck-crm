PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: M21.3-TRACE-BLOCKER

# M21.3 Trace Blocker — Forensic Audit: Unattributed Outbound
**File:** `2026-08-27_RIDECHECK_CRM_M21.3-TRACE-BLOCKER_AUDIT_TODAY-47MIN-DELAY.md`
**Date:** 2026-08-27
**Auditor:** Claude (automated forensic)
**Scope:** Identify the component, trigger, and transmission path for the WhatsApp message
`"Perfecto, tenemos disponibilidad. ¡Ya te confirmo!"` received at ~18:32 ART today.

---

## VERDICT: TRACEABILITY FAILURE / UNATTRIBUTABLE OUTBOUND

The target message cannot be attributed to any known execution path of the Ridecheck CRM system.
No send record, no execution trace, and no DB entry for this exact text exist across all three
databases (`crm`, `crm_test`, `crm_smoke_test`). The backend is physically incapable of generating
it under current configuration.

**This is a RELEASE BLOCKER.** The traceability gap must be resolved before M21.3 ships.

---

## Part 1 — Incident Timeline

| Time (ART) | Time (UTC) | Event |
|---|---|---|
| ~13:54–13:58 | 16:54–16:58 | First testing sub-session: n8n executions 1413–1418, many Meta webhook POSTs |
| ~16:20–16:28 | 19:20–19:28 | Second testing sub-session: n8n executions 1419–1424, outbound messages 5238–5253 sent (OUTBOUND=true) |
| ~17:32:36 | 20:32:36 | Backend container REPLACED (UX1 scroll fix deployment). New container starts. Logs from previous container are LOST. OUTBOUND_ENABLED=false (compose default) |
| 17:45:49 | 20:45:49 | Owner sends `hello` (inbound msg 5254, thread 2) |
| 17:45:50 | 20:45:50 | nginx: `POST /integrations/whatsapp/webhook` 200 OK — backend receives "hello" |
| 17:46:11 | 20:46:11 | n8n debounce fires, POSTs to `/api/conversation/handle` |
| 17:46:15 | 20:46:15 | CE produces greeting, OutboundSafetyGate BLOCKS it. Blocked record 5255 written. |
| **18:32:01** | **21:32:01** | **Two Meta webhook POSTs arrive from 173.252.107.9 and 173.252.107.36** |
| ~18:32 | ~21:32 | Owner receives `"Perfecto, tenemos disponibilidad. ¡Ya te confirmo!"` on phone |
| 18:39:04 | 21:39:04 | Third Meta webhook POST from 173.252.95.58 |
| 18:32+ | 21:32+ | unanswered_alert loops on AiEvent 88, SMTP unreachable |

---

## Part 2 — nginx Access Log Analysis (PIVOTAL FINDING)

The previous audit search used UTC timestamps — nginx logs in local ART time (`-0300`). The
earlier "EMPTY" result was a timezone error. Re-searching in ART correctly reveals:

**18:32:01 ART** (= 21:32:01 UTC):
```
173.252.107.9  - [27/Aug/2026:18:32:01 -0300] "POST /integrations/whatsapp/webhook HTTP/1.1" 200 2 "-" "facebookexternalua"
173.252.107.36 - [27/Aug/2026:18:32:01 -0300] "POST /integrations/whatsapp/webhook HTTP/1.1" 200 2 "-" "facebookexternalua"
```

**18:39:04 ART** (= 21:39:04 UTC):
```
173.252.95.58  - [27/Aug/2026:18:39:04 -0300] "POST /integrations/whatsapp/webhook HTTP/1.1" 200 2 "-" "facebookexternalua"
```

**Interpretation:**
- Source IPs `173.252.107.x`, `173.252.95.x` are confirmed Meta infrastructure (facebookexternalua).
- `/integrations/whatsapp/webhook` routes to `crm.ridecheck.ar` → nginx → `localhost:8000` → backend container.
- The 2-byte response is `"ok"` — the backend's standard webhook acknowledgment.
- The double POST at exactly the same second (18:32:01) is consistent with Meta sending STATUS UPDATES for two separate outbound messages simultaneously.

**Full-day Meta webhook pattern:**
| Time (ART) | Count | Session |
|---|---|---|
| 13:54–13:58 | 15 | First testing sub-session (many status updates for test messages) |
| 16:20–16:28 | 30+ | Second testing sub-session (delivered/read updates for msgs 5238–5253) |
| 17:45:50 | 1 | `hello` inbound message |
| **18:32:01** | **2** | **Status updates for earlier test messages (read receipts delayed ~2h)** |
| 18:39:04 | 1 | Additional status update |

---

## Part 3 — Backend Webhook Handler Analysis

Examined `backend/app/routes/whatsapp.py:137` (`inbound_webhook`). The handler processes TWO
webhook payload types:

### 3.1 — Inbound Message Path (`value.messages`)
```
Store to DB → Create AiEvent → Trigger n8n → n8n debounces → CE processes → reply (or blocked)
```
Result: New `WhatsAppMessage` row in DB, new `AiEvent` row, `POST /api/conversation/handle` in logs.

### 3.2 — Status Update Path (`value.statuses`)
```
Look up wa_message_id in DB → If found, update status field → Commit → Return "ok"
```
Result: Existing message row updated. No new DB rows. No n8n trigger. No CE call.

**Evidence the 18:32 webhooks followed the STATUS UPDATE path:**

| Evidence | Value |
|---|---|
| New inbound message in DB after msg 5254? | **NO** — DB shows 5254 (`hello`) then 5255 (blocked reply), nothing after |
| `POST /api/conversation/handle` in backend logs at 18:32? | **NO** — backend logs show ONLY ONE CE call (at 17:46 for the `hello`) |
| New AiEvent created at 18:32? | **NO** — AiEvent 88 remains the last one |
| n8n execution for 18:32 event? | **NO** — last n8n execution 1424 at 19:24 UTC; 1425 is status `new` (stuck from 19:25 UTC) |

**These webhooks were read receipts for one or more of test messages 5238–5253**, which were sent at
~16:21–16:28 ART (OUTBOUND=true) and which the owner read approximately 2 hours later.

---

## Part 4 — Database Exhaustive Search

### 4.1 — Exact text search across ALL databases

```sql
SELECT id, direction, status, LEFT(text,80), created_at
FROM whatsapp_messages WHERE text ILIKE '%Perfecto, tenemos disponibilidad%';
```

| Database | Result |
|---|---|
| `crm_test` | **0 rows** |
| `crm` (production) | **0 rows** |
| `crm_smoke_test` | **0 rows** |

**The exact text "Perfecto, tenemos disponibilidad. ¡Ya te confirmo!" has NEVER been stored
in any database on this server. It was never sent (blocked or unblocked) through this backend.**

### 4.2 — All outbound messages today in thread 2

```
5238 | out | read  | ¡Hola! Sí, hacemos revisiones de vehículos...      | 19:21 UTC
5240 | out | read  | Genial! La cotización para la revisión del Peugeot  | 19:22 UTC
5243 | out | read  | ¡Genial! Vamos a coordinar la revisión del Peugeot | 19:24 UTC
5244 | out | read  | dije que horarios hacen, no radios                  | 19:25 UTC
5246 | out | read  | ¡Entendido! ¿Qué día y horario te viene mejor...   | 19:25 UTC
5248 | out | read  | Para mañana viernes 28/08 a las 18:00 no tenemos   | 19:27 UTC
5250 | out | read  | Para sábado 29/08 a las 18:00 no tenemos...        | 19:28 UTC
5253 | out | read  | Genial! La cotización para la revisión del Peugeot | 19:28 UTC
5255 | out | blocked | ¡Hola! ¿En qué puedo ayudarte hoy?              | 20:46 UTC
```

All 9 outbound messages accounted for. None contain the target text. The scheduling responses
(5248, 5250) both confirm unavailability (`no tenemos disponibilidad`) — the slot acceptance path
was NEVER reached during the testing session.

---

## Part 5 — Capability Analysis: Can the Current Container Generate the Target Text?

The text `"Perfecto, tenemos disponibilidad. ¡Ya te confirmo!"` has a single source in the codebase:

```python
# conversation_engine.py:4353
flow_id = (self.settings.whatsapp_flow_id or "").strip()
if not flow_id:
    logger.error("M18 WHATSAPP_FLOW_ID not set — sending text fallback")
    fallback = ai_reply or "Perfecto, tenemos disponibilidad. ¡Ya te confirmo!"
    sent_id = self._send_text_to_wa(ctx, fallback)
    return _out("replied", wa_message_id=sent_id)
```

This branch fires ONLY when all THREE conditions are simultaneously true:
1. Scheduling slot IS available (acceptance path, not rejection)
2. `WHATSAPP_FLOW_ID` is empty/unset
3. `OUTBOUND_ENABLED=true` (otherwise gate blocks the send)

**Current container state:**
```
WHATSAPP_FLOW_ID=1644218879979041   ← CONDITION 2 FAILS
OUTBOUND_ENABLED=false              ← CONDITION 3 FAILS
```

**The current backend container is PHYSICALLY INCAPABLE of generating this text.**

If FLOW_ID is set but `_send_flow_button()` throws an exception, CE returns `None` — it does NOT
fall back to the "Perfecto" text. There is no code path from the current container that produces
this output.

---

## Part 6 — n8n Execution Audit

n8n SQLite database copied from container and queried:

| Execution ID | Started (UTC) | Status | Notes |
|---|---|---|---|
| 1413–1418 | 16:54–16:58 | success | First testing sub-session (ART: 13:54–13:58) |
| 1419–1424 | 19:20–19:24 | success | Second testing sub-session (ART: 16:20–16:24) |
| **1425** | **None** | **new** | Created 2026-08-27 19:25:33 UTC — **NEVER STARTED** |

Execution 1425:
- Created during the testing session (msg 5245 trigger at 19:25:33 UTC)
- `startedAt=None`, `stoppedAt=None`, status=`new`
- Not related to the 18:32 incident — pre-dates it by ~2 hours

**NO n8n execution exists for any event at or around 21:32 UTC (18:32 ART).**

n8n saves only failed and certain successful executions. Execution 1425's `new` status is a
suspected SQLite-persistence race condition with an execution that DID process in-memory but
left a dangling `new` record.

---

## Part 7 — Component Clearance

| Component | Can send WhatsApp? | Involved at 18:32? | Cleared? |
|---|---|---|---|
| Backend CE (`_send_text_to_wa`) | YES (via Meta API) | NO (no CE call) | ✓ CLEARED |
| n8n `Send Whatsapp Reply` node | YES (calls `/api/whatsapp/.../send-text`) | NO (no n8n exec at 18:32) | ✓ CLEARED |
| n8n `Send Whatsapp Reply1` node | YES | NO | ✓ CLEARED |
| `unanswered_alert` service | NO (email only via SMTP) | N/A | ✓ CLEARED |
| BackgroundTasks in FastAPI | Via CE only | NO (no CE call) | ✓ CLEARED |
| Production backend (crm DB) | YES (if running) | Production DB: 0 rows today | ✓ CLEARED |
| Cron jobs | NONE that send WhatsApp | N/A | ✓ CLEARED |
| WhatsApp Flow completion screen | YES (flow UI text) | No flow_response in DB | UNVERIFIABLE |

---

## Part 8 — Remaining Unverifiable Hypothesis

**WhatsApp Flow completion screen:**

If one of the configured WhatsApp Flows (`WHATSAPP_FLOW_ID=1644218879979041`,
`WHATSAPP_VEHICLE_FALLBACK_FLOW_ID=27205677485784073`, `WHATSAPP_LOCATION_FALLBACK_FLOW_ID=2550767958730294`,
`WHATSAPP_WEBSITE_FLOW_ID=1535038801697863`) contains the text
`"Perfecto, tenemos disponibilidad. ¡Ya te confirmo!"` in its completion screen template (defined on
Meta's servers, not in our code), the user would see this text inside the flow UI — not as a chat
bubble message.

**Against this hypothesis:**
- Flow submission triggers an `nfm_reply` webhook → our backend would store a `flow_response`
  message in the DB
- No `message_type='flow_response'` entries in thread 2 today (zero rows)
- No flow button was sent during the testing session (all 5238–5253 are `message_type='text'`)
- Flow completion screens appear within the flow UI, not as standalone chat bubbles

**This hypothesis requires access to the Meta Business Manager Flow Editor to verify or rule out.**

---

## Part 9 — Missing Telemetry (Traceability Defect)

The following telemetry gaps PREVENT attribution:

| Gap | What's missing | Why it matters |
|---|---|---|
| **Previous container logs** | Container replaced at 20:32 UTC; old logs destroyed. Two testing sub-sessions (13:54–13:58 and 16:20–16:28 ART) fully handled by the old container. | Cannot inspect what the old container processed or sent. |
| **Webhook payload content** | The 18:32 webhook payloads were processed and acknowledged (200 OK) but the body is not persisted to DB when processing status updates. | Cannot confirm whether 18:32 webhooks were status updates or a new unrecognized message type. |
| **WAMID of received message** | Owner did not extract the WAMID from the screenshot. A WAMID would identify the sender phone number ID. | With WAMID, we could determine if the message was sent by our phone number (1196075770246218) or another. |
| **Meta-side send logs** | Meta's Business Manager → WhatsApp → Sent Messages log. Not accessible from our backend. | Would show all outbound messages from our number today, regardless of DB state. |
| **n8n execution data for 1413–1418, 1419–1424** | n8n saves only partial data. Full node I/O for all testing-session executions not available. | Cannot confirm what exact messages were planned for dispatch in n8n's reply nodes. |

---

## Part 10 — Attribution Chain Assessment

**Required chain (per mandate):**
```
trigger → component → generated text → send mechanism → Meta API request → WAMID → delivery at ~21:32 UTC
```

| Step | Status |
|---|---|
| Trigger | UNKNOWN — no inbound message or n8n exec at 21:32 UTC |
| Component | UNKNOWN — no CE call, no n8n exec |
| Generated text | NOT FOUND — zero DB rows for exact text across all 3 databases |
| Send mechanism | UNKNOWN — no `_send_text_to_wa` call at 21:32 |
| Meta API request | UNVERIFIABLE — no outgoing HTTP call logged from this server |
| WAMID | UNKNOWN — owner did not extract from WhatsApp |
| Delivery confirmation | 18:32 webhooks received, but content unknown |

**Attribution is IMPOSSIBLE with available telemetry.**

---

## Part 11 — Severity and Blocker Assessment

| Criterion | Finding |
|---|---|
| OUTBOUND_ENABLED at incident time | `false` — gate was active |
| Message delivered to owner's phone? | **YES — owner received it** |
| Message stored in crm_test DB? | **NO — zero rows** |
| Message stored in crm (prod) DB? | **NO — zero rows** |
| Message traceable to this system? | **NO — TRACEABILITY FAILURE** |
| n8n active at incident time? | YES, but no execution for 18:32 event |
| Alternative sender (other account)? | UNVERIFIABLE — requires WAMID |
| WhatsApp Flow completion? | UNVERIFIABLE — requires Meta Flow Editor access |
| Safety contract (no unintended outbound)? | **INDETERMINATE — cannot prove or disprove** |

**Severity: CRITICAL / RELEASE BLOCKER**

The safety contract requires that ALL outbound messages be:
1. Intentional (human-approved or CE-generated within scope)
2. Gated through OutboundSafetyGate
3. Recorded in the database

This message satisfies NONE of these criteria as verifiable by server-side evidence.
The gap between "owner received it" and "system has no record of it" is a fundamental
traceability defect regardless of which component actually generated it.

---

## Part 12 — Required Actions Before M21.3 Can Ship

1. **Owner to extract WAMID**: Long-press the received message in WhatsApp → Message Info → copy
   `wamid.*` identifier. With WAMID, we can check if `WHATSAPP_PHONE_NUMBER_ID=1196075770246218`
   was the sender on Meta's side.

2. **Check Meta Business Manager**: Logs → WhatsApp → Messages Sent for phone number 1196075770246218,
   date 2026-08-27, time ~21:32 UTC. If a message appears that is not in our DB, it originated from
   outside this backend (possible unauthorized API access or another client with the same token).

3. **Revoke and rotate WHATSAPP_TOKEN**: The current token is visible in printenv. Until the source
   is identified, the token must be treated as potentially compromised. It should be rotated after
   the Meta logs are inspected.

4. **Implement webhook payload logging**: Status update payloads should be logged (at least the
   `wa_message_id` and `status` fields) to enable post-hoc tracing.

5. **Implement container log persistence**: Replace ephemeral docker container logs with a persistent
   log aggregator (file-based `--log-driver local` with rotation, or a log ship to a durable store).
   The previous container's logs being destroyed is the primary traceability gap.

6. **Verify WhatsApp Flow templates**: In Meta Business Manager → Flows → check the completion screen
   text of all 4 configured flows for the string "Perfecto, tenemos disponibilidad. ¡Ya te confirmo!".

---

## Summary

At 18:32:01 ART (21:32:01 UTC), Meta delivered TWO webhook POSTs to this server. They were processed
by the backend as STATUS UPDATES (returning "ok", no DB rows written, no n8n triggered). Simultaneously,
the owner received `"Perfecto, tenemos disponibilidad. ¡Ya te confirmo!"` on their WhatsApp.

This text does not appear in any of the three databases (`crm`, `crm_test`, `crm_smoke_test`). The
current backend container is physically incapable of generating it (`WHATSAPP_FLOW_ID` is set,
disabling the only code path that produces this text). No n8n execution, no CE call, and no Meta API
outbound call is logged at the incident timestamp.

**VERDICT: TRACEABILITY FAILURE / UNATTRIBUTABLE OUTBOUND.**

The message was received by the owner. Its origin cannot be attributed to any known component of
this system. The traceability defects (destroyed container logs, no WAMID, no Meta-side send logs)
prevent a definitive root cause. The safety contract cannot be proven intact.

**M21.3 RELEASE IS BLOCKED pending resolution of items in Part 12.**

---

*Audit conducted 2026-08-27. No code changes, DB changes, or configuration changes were made during this audit.*
*Constraint compliance: OUTBOUND OFF, n8n UNTOUCHED, production UNTOUCHED (read-only forensic), development FROZEN.*
