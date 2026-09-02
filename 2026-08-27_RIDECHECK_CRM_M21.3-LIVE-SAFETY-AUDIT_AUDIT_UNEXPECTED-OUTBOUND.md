PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: M21.3-LIVE-SAFETY-AUDIT

# Unexpected Outbound — Forensic Audit
**File:** `2026-08-27_RIDECHECK_CRM_M21.3-LIVE-SAFETY-AUDIT_AUDIT_UNEXPECTED-OUTBOUND.md`
**Date:** 2026-08-27
**Auditor:** Claude (automated forensic)
**Trigger:** Owner sent `hello` while testing scroll behavior, expected no bot reply, observed what appeared to be an outbound message in the CRM UI.

---

## SAFETY STATUS: CONTAINED — NO VIOLATION

The OutboundSafetyGate kill switch intercepted the reply before any HTTP call to the Meta WhatsApp Cloud API. The message was never delivered to the owner's phone. The audit record (status=`blocked`, wa_message_id=NULL) was visible in the CRM web UI and was mistaken for a sent message.

OUTBOUND_ENABLED was `false` at container start and remained `false` throughout the incident. No containment action required.

---

## Part 1 — Exact Inbound / Outbound Messages

### Inbound (trigger)
| Field | Value |
|---|---|
| WhatsApp message ID | `wamid.HBgNNTQ5MTE1MzM2ODMzMBUCABIYFjNFQjA2ODdERDQ5MDkwMUM1MjBBMzUA` |
| DB ID | 5254 |
| Thread ID | 2 |
| Lead ID | 4 / Contact ID 2 |
| Direction | in |
| Text | `hello` |
| Status | received |
| automated | False |
| Timestamp (UTC) | 2026-08-27 20:45:49 |
| DB created_at (UTC) | 2026-08-27 20:45:50.560 |

### Outbound attempt (blocked — never sent)
| Field | Value |
|---|---|
| DB ID | 5255 |
| Thread ID | 2 |
| wa_message_id | **NULL** — no WAMID assigned; Meta API was never called |
| Direction | out |
| Text | `¡Hola! ¿En qué puedo ayudarte hoy?` |
| message_type | text |
| Status | **blocked** |
| blocked_reason | `KILL_SWITCH: OUTBOUND_ENABLED is not 'true'` |
| automated | True |
| Timestamp (UTC) | 2026-08-27 20:46:15.388 |

### AiEvent (CE processing record)
| Field | Value |
|---|---|
| AiEvent ID | 88 |
| wa_message_id | `wamid.HBgNNTQ5MTE1MzM2ODMzMBUCABIYFjNFQjA2ODdERDQ5MDkwMUM1MjBBMzUA` |
| wa_id | `5491153368330` |
| event_type | inbound_message |
| text | `hello` |
| status | processed |
| reply_required | True |
| reply_produced | **False** |
| ai_invoked | True |
| action | **blocked_dispatch** |
| answer_source | DETERMINISTIC_RULE |
| latency_debounce_ms | 21070 (~21 s — n8n debounce fired) |
| latency_ce_ms | 3777 (~3.8 s) |
| burst_message_count | 1 |
| cycle_message_count | 12 |
| unanswered_alert_sent_at | NULL |

---

## Part 2 — Runtime Configuration

### OUTBOUND_ENABLED
- **Source:** `docker-compose.beta.yml` environment stanza:
  ```yaml
  OUTBOUND_ENABLED: "${BETA_OUTBOUND_ENABLED:-false}"
  ```
  Default is `false`. `BETA_OUTBOUND_ENABLED=true` is required to enable live outbound. It was not set on container restart.
- **Container value at audit time:** `false` (confirmed via `docker exec printenv`)
- **Container value at incident time:** `false` (container started at 20:32:36 UTC, 13 minutes before incident; state never changed)

### Allowlist / Quarantine
- `CLOSED_BETA_ALLOWED_WA_IDS=5491153368330` — owner's WA ID is on the allowlist
- `QUARANTINED_TEST_WA_IDS=` — empty, no quarantine
- Allowlist status is irrelevant: kill switch fires before allowlist check when `OUTBOUND_ENABLED != 'true'`

### CE direct webhook
- `CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED` — **not set in container** (defaults to `false`)
- Live path: `WhatsApp webhook → n8n → POST /api/conversation/handle`
- n8n debounce confirmed active: `latency_debounce_ms=21070`

### Container image and start time
- Image: `ridecheck-crm-backend:wild04r-f6-fd73611`
- Container started: **2026-08-27T20:32:36Z** (13 minutes before incident)
- Restart count: 0 (no automatic restarts)

### Outbound counts (last 48 h at audit time)
| status | count |
|---|---|
| sent | 30 |
| read | 15 |
| blocked | 7 |

The 30 `sent` + 15 `read` records are from the earlier same-day testing session (19:21–19:28 UTC, messages 5238–5253) when `BETA_OUTBOUND_ENABLED=true` was in effect. Those are prior-session, not from the incident.

---

## Part 3 — Deployment History

### Event sequence (UTC)
| Time (UTC) | Event |
|---|---|
| 19:21–19:28 | Prior testing session, OUTBOUND ON, messages 5238–5253 sent/read |
| 20:32:36 | Container restarted (UX1 scroll fix deployment) — `OUTBOUND_ENABLED=false` (compose default) |
| 20:45:49 | `hello` received (inbound msg 5254) |
| 20:45:50 | n8n debounce started (21 s debounce window) |
| ~20:46:11 | n8n POSTed to `POST /api/conversation/handle` |
| 20:46:15 | CE produced greeting, gate blocked it (msg 5255 created, status=blocked) |
| 20:46:15+ | UI polls `/whatsapp/thread/2/latest` — blocked record becomes visible in chat view |

The container restart at 20:32:36 UTC was the deployment of the UX1 scroll-to-bottom fix. The `docker-compose.beta.yml` compose default (`${BETA_OUTBOUND_ENABLED:-false}`) set OUTBOUND to `false` because `BETA_OUTBOUND_ENABLED=true` was not passed in the restart command. This is the correct safe state.

---

## Part 4 — Outbound Safety Gate Trace

Gate execution for message 5255:

**Backend container log (direct trace):**
```
OUTBOUND_GATE_KILL_SWITCH wa_id=...8330 thread_id=2 fp=d015a911 blocked_id=5255
M19 OUTBOUND_BLOCKED thread_id=2 outcome=blocked_kill_switch wa=wamid.HBgNNTQ5MTE1MzM2ODMzMBUCABIYFjNFQjA2ODdERDQ5MDkwMUM1MjBBMzUA
INFO:     172.18.0.4:54300 - "POST /api/conversation/handle HTTP/1.1" 200 OK
```

**Gate decision path:**
1. `OutboundSafetyGate.attempt()` called by CE for outbound message
2. `_check_kill_switch()`: reads `OUTBOUND_ENABLED` env var → value is `false` (not `'true'`)
3. Gate writes audit record: WhatsApp message ID 5255, status=`blocked`, blocked_reason=`KILL_SWITCH: OUTBOUND_ENABLED is not 'true'`, wa_message_id=NULL
4. Returns `GateOutcome.BLOCKED` — CE does not call `_send_whatsapp_cloud_text()`
5. AiEvent updated: action=`blocked_dispatch`, reply_produced=False

No Meta WhatsApp Cloud API call was made. No WAMID was ever assigned.

---

## Part 5 — n8n Check

- n8n container: `ridecheck-crm-n8n-1`, **Up 6 days** (not restarted during incident)
- n8n log output in incident window (20:44–20:48 UTC): **empty** — no log lines emitted by n8n
- Evidence n8n DID execute: AiEvent 88 `latency_debounce_ms=21070` — the ~21 s delay is n8n's 20-second debounce; n8n then POSTed to CE `/api/conversation/handle`
- No pending delayed/debounced executions visible; single-burst message, burst_message_count=1
- n8n was not stopped or modified during the incident window

**Observation:** n8n was active and processed the message through its debounce and CE-call nodes. The CE handled it. No residual n8n execution queue issue.

---

## Part 6 — Greeting Behavior Under Live Operation

**Why CE replied to `hello`:**
- CE matched `hello` via deterministic greeting rule (AiEvent: `answer_source=DETERMINISTIC_RULE`)
- CE's standard greeting path fires for any message from an in-scope contact when the intent is unrecognized
- The response `¡Hola! ¿En qué puedo ayudarte hoy?` is CE's generic greeting

**Is this correct behavior?**
- Yes — CE is designed to respond to any inbound message. The kill switch is the safety layer, not CE itself.
- When `OUTBOUND_ENABLED=false`, CE can generate replies freely; the gate always intercepts them before transmission.
- This is the intended design: CE processes → Gate decides → nothing leaves the server.

**Greeting visible in UI — why:**
- WhatsApp message record 5255 (direction=out, status=blocked) was written to the DB as a kill-switch audit trail.
- The CRM web UI polls `GET /whatsapp/thread/2/latest` continuously (dozens of calls visible in backend logs).
- The UI renders all `direction='out'` messages in the chat view regardless of status.
- The blocked record appeared in the owner's UI as a bot reply, creating the perception of an unexpected outbound.
- **No message was delivered to WhatsApp.** The owner's phone did not receive it.

---

## Part 7 — Secondary Finding: Unanswered Alert Loop

AiEvent 88 has `reply_required=True`, `reply_produced=False`, `unanswered_alert_sent_at=NULL`.

The `unanswered_alert` service is firing repeatedly against AiEvent 88, attempting to email an SLA alert via SMTP. All attempts fail with:
```
OSError: [Errno 101] Network is unreachable
```
The unanswered alert SMTP client cannot reach `smtp.gmail.com:587` from within the container network. This is an infrastructure restriction in the current test environment.

This loop is benign with respect to safety:
- No outbound WhatsApp messages are involved
- The alert system is trying to notify a human that a message went unanswered — which is correct behavior given that the reply was blocked
- No data is being modified; the loop will continue until AiEvent 88's `unanswered_alert_sent_at` is set or the alert is cleared

This is a pre-existing SMTP network restriction, not caused by M21.3. No action required under this audit.

---

## Part 8 — Severity Classification

| Criterion | Finding |
|---|---|
| Safety contract violated? | **NO** |
| Message reached Meta API? | **NO** — wa_message_id=NULL |
| Message delivered to owner's phone? | **NO** |
| OUTBOUND_ENABLED was true at any point? | **NO** — false from container start |
| Gate bypassed or degraded? | **NO** — gate executed correctly |
| Root cause of perceived "reply"? | Blocked audit record (ID 5255) visible in CRM UI |
| n8n state abnormal? | NO — debounce correct, no residual queue |
| DB/production touched? | **NO** — crm_test only |

**Severity: INFORMATIONAL**

No safety contract violation. No outbound message was transmitted. The incident is fully explained by the CRM UI displaying the blocked audit record as a visual chat bubble, which the owner interpreted as a sent reply.

---

## Summary

The owner sent `hello` (inbound 5254) at 20:45:49 UTC during scroll-behavior testing. n8n debounced for 21 seconds then called CE. CE's deterministic greeting rule produced `¡Hola! ¿En qué puedo ayudarte hoy?`. The OutboundSafetyGate's kill switch intercepted it immediately — OUTBOUND_ENABLED was and remained `false` throughout — and wrote audit record 5255 (status=blocked, wa_message_id=NULL). The Meta WhatsApp Cloud API was never called. The blocked record appeared in the CRM web UI (which displays all outbound records regardless of status) and was mistaken for a delivered reply.

**Safety contract: INTACT.**
**Required actions: NONE.**

---

*Audit conducted 2026-08-27. No code changes, DB changes, or configuration changes were made during this audit.*
