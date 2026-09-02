PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: M21.3-LIVE-SAFETY-AUDIT2

# Real WhatsApp Delivery — Second Forensic Audit
**File:** `2026-08-27_RIDECHECK_CRM_M21.3-LIVE-SAFETY-AUDIT2_AUDIT_REAL-WHATSAPP-DELIVERY.md`
**Date:** 2026-08-27
**Auditor:** Claude (automated forensic)
**Trigger:** Owner produced WhatsApp screenshot showing delivery of "Perfecto, tenemos disponibilidad. ¡Ya te confirmo!" at ~18:32 Argentina local time, ~47 minutes after the "hello" at ~17:45 local. This text does not match the blocked greeting found in Audit 1.

---

## CONTAINMENT STATUS

```
docker exec ridecheck-crm-backend-1 printenv OUTBOUND_ENABLED → false
```

OUTBOUND_ENABLED is `false` in the running crm_test backend container. No containment action taken. All known automated paths through the crm_test stack are gated.

Evidence preserved: YES. No DB changes, no config changes, no patch applied.

---

## INCIDENT STATUS

**REAL WHATSAPP DELIVERY — SOURCE UNRESOLVED**

The text "Perfecto, tenemos disponibilidad. ¡Ya te confirmo!" is NOT present in the crm_test database, NOT present in n8n execution logs for the incident window, and NOT in the crm_test backend container logs. It was not sent by any auditable path through the crm_test stack. The production database (`crm` on `ridecheck-crm-postgres-1`) is the only remaining unaudited evidence source and is inaccessible under the standing "Production DB: UNTOUCHED" constraint.

---

## Part 1 — Exact Outbound Record

**Text searched:** `Perfecto, tenemos disponibilidad. ¡Ya te confirmo!`

Search across crm_test `whatsapp_messages`:
```sql
SELECT * FROM whatsapp_messages
WHERE text ILIKE '%Perfecto%disponibilidad%'
   OR text ILIKE '%tenemos disponibilidad%'
   OR text ILIKE '%Ya te confirmo%';
```

**Result: 0 rows matching exact target text.**

Partial matches returned (containing "tenemos disponibilidad" as part of rejection messages):
- ID 938, 941, 943 (Aug 22, rejection scheduling messages — different text)
- ID 5248, 5250 (Aug 27 prior session — rejection scheduling messages — different text)

**The delivered message has NO DB record in crm_test. This is a HIGH-severity forensic finding.**

---

## Part 2 — Meta Proof

No WAMID for this message exists in crm_test `whatsapp_messages`. No Meta API call for this text was logged in `ridecheck-crm-backend-1` container logs. The crm_test backend container started at 20:32:36 UTC and its full log (1913 lines) contains exactly ONE outbound gate event (blocked, msg 5255), no Meta API calls.

**Meta API proof is UNAVAILABLE from the crm_test stack.** The WAMID for the delivered message would be in the production database (`crm` on `ridecheck-crm-postgres-1`) if sent through the production backend.

---

## Part 3 — Time Correlation

All times stated explicitly in both UTC and Argentina local (UTC-3).

| Event | Argentina local (UTC-3) | UTC |
|---|---|---|
| Prior session: first outbound sent | ~16:20 | ~19:20 |
| Prior session: last outbound sent | ~16:28 | ~19:28 |
| Container restart (crm_test backend) | ~17:32 | ~20:32 |
| `hello` received | ~17:45 | ~20:45:49 |
| n8n debounce + CE call | ~17:46 | ~20:46:11 |
| CE blocked reply (msg 5255) | ~17:46 | ~20:46:15 |
| **Delivered message (owner screenshot)** | **~18:32** | **~21:32** |

**Gap analysis:**
- From "hello" inbound to delivered message: ~47 minutes
- Normal n8n debounce: 20 seconds
- A 47-minute delay is completely incompatible with any normal CE processing path

**N8N execution map for Aug 27 (full day, API-verified):**

| Execution ID | Status | UTC start | UTC stop |
|---|---|---|---|
| 1419 | success | 19:20:56 | ~19:21:20 |
| 1420 | success | 19:21:03 | ~19:21:26 |
| 1421–1429 | success | 19:21–19:28 | 19:21–19:28 |
| **1430** | **success** | **20:45:50** | **20:46:15** |
| **— gap —** | — | **20:46:15** | **no further executions** |

**No n8n execution occurred in the 20:46:15–22:00 UTC window** (17:46–19:00 local). The delivered message at ~21:32 UTC has zero n8n execution activity. This eliminates n8n as the sender in that window.

---

## Part 4 — Root Cause Classification

Evidence assessed:

| Hypothesis | Evidence | Verdict |
|---|---|---|
| A. "hello" directly caused it | CE produced blocked greeting only; AiEvent 88 action=blocked_dispatch; no further n8n execution | **ELIMINATED** |
| B. "hello" triggered stale scheduling state | CE processed hello as greeting (DETERMINISTIC_RULE); no scheduling path reached | **ELIMINATED** |
| C. Message from previous scheduling request, arrived late | No WAMID in crm_test for this text; all prior-session messages accounted for with different text | **CANNOT CONFIRM — production DB needed** |
| D. n8n execution queued from earlier remained | n8n shows no execution in incident window; execution 1430 ended cleanly at 20:46:15 UTC | **ELIMINATED** |
| E. Background worker sent it | No background worker in crm_test backend; unanswered_alert only sends SMTP (not WhatsApp) | **ELIMINATED for crm_test** |
| F. Human/manual CRM send | Possible via UI or direct API; would require OUTBOUND_ENABLED=true or gate bypass — neither present in crm_test | **CANNOT CONFIRM — no record** |
| G. Production backend sent it at a different time, Meta delivered late | Production backend previously ran against production DB (`crm`); text exists in CE as a fallback; no production backend currently running | **MOST PROBABLE — unconfirmable without production DB** |

**Primary root cause: UNKNOWN — production database inaccessible.**

Most probable: the message was sent by the PRODUCTION backend at an earlier time (before the crm_test setup), was recorded in the production `crm` database, and was delivered to the owner's WhatsApp by Meta with a significant delay (phone offline, connectivity issue, or Meta retry).

---

## Part 5 — Text Source Locations

Search: `"Perfecto, tenemos disponibilidad"`, `"Ya te confirmo"`, `"tenemos disponibilidad"`

**Found in CE source:**
```
/opt/ridecheck-crm-release-candidate/backend/app/services/conversation_engine.py:4353
    fallback = ai_reply or "Perfecto, tenemos disponibilidad. ¡Ya te confirmo!"
```

**Context:** This fallback fires in the scheduling slot acceptance path when `WHATSAPP_FLOW_ID` is not set in the backend environment. The current container has `WHATSAPP_FLOW_ID=1644218879979041` set — this branch would NOT be reached in the current crm_test container. It would fire if a backend ran without `WHATSAPP_FLOW_ID` configured, or under a previous version of the workflow.

Status of this path: **ACTIVE in CE source** / **UNREACHABLE in current crm_test container** (FLOW_ID is set) / **POTENTIALLY REACHABLE in a prior production backend session** if FLOW_ID was unset at that time.

**Found in n8n workflow files:**
- `database.sqlite-wal` — contains `"tenemos disponibilidad"` inside an AI system prompt constraint: `"¿Avanzamos para ver si tenemos disponibilidad?"` — this is a system prompt instruction, NOT a send template. Status: **REFERENCE ONLY / NOT A SEND NODE**.

**Found in n8n nodes (wf_nodes.json):**
- `AI Reply Planner` node (n8n-nodes-langchain.chainLlm): this node is in the legacy fallback branch. It has NOT been reached in any Aug 27 execution (confirmed via full execution log scan). Last time it fired: execution 1293 on 2026-08-14. Status: **LEGACY / NOT REACHED AUG 27**.

**Found in forensics/docs:** `"tenemos disponibilidad"` appears in forensic analysis files — historical references only.

---

## Part 6 — OUTBOUND_ENABLED Contradiction

The delivered message was NOT sent through the current crm_test backend (`ridecheck-crm-backend-1`). Determination of which process sent it:

| Sender candidate | Running now? | Gate-controlled? | Could have produced message? |
|---|---|---|---|
| `ridecheck-crm-backend-1` (crm_test, port 8000) | YES | YES — OutboundSafetyGate on all paths (CE + /send-text) | **NO** — no record in crm_test DB; OUTBOUND_ENABLED=false; blocked record 5255 is the only outbound event |
| `ridecheck-crm-n8n-1` (n8n, port 5678) | YES | Partial — AI reply nodes call /send-text (gated); no direct Meta calls found | **NO** — no execution in incident window; all Aug 27 executions went through CE path |
| Production backend (DATABASE_URL→`crm`) | **NOT RUNNING** (no such container or process) | Unknown (production config differs) | **UNKNOWN — production DB inaccessible** |
| n8n legacy AI branch + Send Whatsapp Reply | EXISTS in workflow but not reached Aug 27 | Calls /send-text (gated) | **NOT REACHED** per event log |
| `ridecheck-crm-release-candidate-backend-1` | Created (never started) | N/A | **NO** |
| Any other process on host (port 8000/8001) | None found | N/A | **NO** — only one uvicorn at pid 1514662 |

**WhatsApp Cloud API credentials inventory:**
The WHATSAPP_TOKEN is present in `ridecheck-crm-backend-1` container env. The production compose file (`/opt/ridecheck-crm/docker-compose.yml`) builds a backend from `./backend` and connects to `postgres:5432/crm`. The production backend is not currently running. When it was running, it would have had access to the same WhatsApp credentials.

No process other than `ridecheck-crm-backend-1` currently has access to the WhatsApp Cloud API.

---

## Part 7 — Central Safety Invariant Audit

Desired invariant: when outbound is OFF, no automated component may transmit a WhatsApp customer message.

| Path | Gate present? | Status |
|---|---|---|
| CE → `_send_text_to_wa()` | YES — OutboundSafetyGate.attempt() inline | INTACT |
| CE → `_send_flow_button()` | YES — OutboundSafetyGate.attempt() inline | INTACT |
| `/api/whatsapp/thread/{id}/send-text` | YES — OutboundSafetyGate.attempt() at line 446 | INTACT |
| n8n "Send Whatsapp Reply" → `/send-text` | GATED via above endpoint | INTACT |
| n8n direct Meta API call | Not found in wf_nodes.json — no graph.facebook.com calls | INTACT |
| CRM manual send via UI | Routes through `/send-text` endpoint — gated | INTACT |
| Production backend (not running) | **NOT AUDITABLE** — not running, DB inaccessible | **UNKNOWN** |
| Background jobs / scheduled tasks | None in crm_test backend; unanswered_alert is SMTP-only | INTACT |

**Safety invariant for crm_test stack: INTACT.**

**Production backend safety invariant: UNKNOWN / NOT AUDITABLE.**

The production backend is not currently running. When it WAS running, it connected to the production `crm` database. Its OUTBOUND_ENABLED state at that time is unknown. This is where the unresolved question lives.

---

## Part 8 — Prior Audit Error

Audit 1 concluded "No message was delivered." That was wrong. Specific errors:

1. **Assumed the blocked greeting (msg 5255) was the owner's reported message.** The bodies are different. `¡Hola! ¿En qué puedo ayudarte hoy?` ≠ `Perfecto, tenemos disponibilidad. ¡Ya te confirmo!`. The prior audit should have cross-checked the exact text from the screenshot before concluding.

2. **Time window was too narrow (20:40–21:00 UTC).** The delivered message was at ~21:32 UTC. The audit window missed it.

3. **Did not query the full whatsapp_messages table for the exact text string.** An ILIKE search was not performed in Audit 1.

4. **Did not audit n8n execution history for the full day.** Prior audit checked n8n container logs (which were empty) but did not enumerate all n8n executions via API or event log.

5. **Did not verify that the production backend was NOT running.** The production postgres being up 3 months was noted but not acted on.

6. **Did not verify that the crm_test backend was the ONLY backend.** A process list check was not performed.

**Corrected forensic methodology:**
1. Always cross-check the reported message body against exact DB records before concluding
2. Use the full-day n8n execution list, not just log grep
3. Verify the exact text exists or does not exist in DB via ILIKE
4. Audit the production stack separately — its state affects the invariant
5. Expand time window to ±2 hours of reported event

---

## Part 9 — What Requires Owner Action

The following evidence is beyond the read scope of this automated audit:

**A. Production database access required to complete the investigation.**

The production postgres (`ridecheck-crm-postgres-1`, database `crm`) must be queried to:
```sql
SELECT id, direction, text, status, wa_message_id, blocked_reason, timestamp
FROM whatsapp_messages
WHERE text ILIKE '%Perfecto%disponibilidad%'
   OR text ILIKE '%Ya te confirmo%'
ORDER BY id DESC LIMIT 10;
```
This query is read-only and will not modify data. It requires authorization from the owner.

**B. WhatsApp Business Manager WAMID lookup.**

The delivered message in the owner's WhatsApp app has a WAMID assigned. Checking Meta's Business Manager or the WhatsApp Cloud API message history for that WAMID would prove exactly when and through which phone number ID it was sent.

**C. Production backend history.**

When was the production backend (`/opt/ridecheck-crm/backend`) last running? What was its OUTBOUND_ENABLED state? The production compose file confirms it would connect to `postgres:5432/crm` — the long-running production postgres.

---

## Summary Return Format

---

INCIDENT STATUS:
CONFIRMED REAL WHATSAPP DELIVERY / SOURCE UNRESOLVED

SEVERITY:
HIGH — a WhatsApp message was delivered to the owner's phone that no auditable crm_test path can account for; the production stack is unaudited

EXACT DELIVERED MESSAGE

Text: `Perfecto, tenemos disponibilidad. ¡Ya te confirmo!`

DB message ID: NONE in crm_test

WAMID: UNKNOWN — not in crm_test DB; production DB inaccessible

Meta API called: YES (owner received it on real WhatsApp) / from what process: UNKNOWN

Delivery webhook confirmed: YES (owner screenshot)

Actual sending timestamp: UNKNOWN — not recoverable without production DB or WAMID trace

Actual sender path: UNKNOWN — most likely production backend connected to `crm` DB at a time when OUTBOUND was enabled

RELATED TO HELLO: NO — No n8n execution fired in the 47-minute window. The "hello" triggered execution 1430 which ended at 20:46:15 UTC. No execution ran again. The delivered message is NOT causally linked to the "hello" message.

47-MINUTE DELAY EXPLANATION: The delay is NOT compatible with any live processing path. The message was most likely sent at an EARLIER time (possibly a different session or date) by the production backend, and Meta delivered it to the owner's phone during the 18:32 local window (phone connectivity, Meta retry, or delayed delivery).

TEXT SOURCE LOCATIONS:
- `conversation_engine.py:4353` — ACTIVE code / UNREACHABLE in current crm_test container (FLOW_ID set) / could have fired in production backend if FLOW_ID was unset
- `n8n database.sqlite-wal` — system prompt constraint text only, not a send path
- n8n `AI Reply Planner` — legacy node, not reached Aug 27

RUNTIME SENDERS

Backend CE (crm_test): CANNOT SEND — OUTBOUND_ENABLED=false, gate intact, no record in crm_test DB

n8n: CANNOT SEND VIA n8n — no execution in incident window, all paths gated

Legacy n8n (AI Reply Planner): NOT REACHED on Aug 27 — last reached Aug 14 (exec 1293)

Production backend: NOT RUNNING — UNKNOWN historical state

Other sender-capable runtime: NONE found on host

OUTBOUND KILL SWITCH

Was OUTBOUND_ENABLED=false: YES — in crm_test container throughout

Did delivered message bypass central gate: UNKNOWN — the message was NOT processed by the crm_test gate. It bypassed the crm_test gate by not going through the crm_test stack at all.

If YES: The sender was NOT ridecheck-crm-backend-1. The gate was not bypassed — the message was sent from a different process (most likely the production backend, which has its own OUTBOUND_ENABLED state).

SAFETY INVARIANT VIOLATED:
UNKNOWN — crm_test invariant is INTACT; production stack invariant is unaudited and production backend's historical OUTBOUND state is unknown

PRIOR AUDIT WAS WRONG BECAUSE:
(1) Did not cross-check reported message body against DB — assumed blocked greeting was the reported message
(2) Time window too narrow (missed 21:32 UTC window)
(3) Did not search DB for exact text string
(4) Did not enumerate n8n executions via API
(5) Did not check whether production backend was/is a separate sender-capable process

CURRENT OUTBOUND STATE: OFF (crm_test)

Evidence preserved: YES

Production touched during audit: NO

Patch applied: NO

SAFE TO CONTINUE M21.3 DEVELOPMENT: NO

Blocker: A real WhatsApp message was delivered to the owner's real number from an unknown source. Until the production database is audited and the sending process identified, the safety invariant cannot be declared fully intact. The crm_test stack is safe. The production stack's state is unaudited.

Required to unblock:
1. Owner authorizes read-only query of `ridecheck-crm-postgres-1` (`crm` database) to find the message record and WAMID
2. OR owner checks WhatsApp Business Manager for the WAMID of "Perfecto, tenemos disponibilidad. ¡Ya te confirmo!" and traces it to the sending process
3. OR owner confirms they received this message on a DIFFERENT date (not today) and it is from a previous production session — in which case it is a historical delivery, not a current safety incident

---

*Audit conducted 2026-08-27. No code changes, DB changes, or configuration changes were made during this audit.*
