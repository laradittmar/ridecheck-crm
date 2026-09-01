# RideCheck CRM — Developer Reference

---

## Launch Certification Governance

Before any Wild, launch, pre-launch, runtime-certification, or launch-remediation work:

**READ [`docs/launch/LAUNCH_TRUTH_ROADMAP.md`](docs/launch/LAUNCH_TRUTH_ROADMAP.md) FIRST.**

Rules:

1. A FROZEN launch gate must not be reopened unless new contradictory runtime or Wild evidence is produced.
2. When a new defect is found, assign it to the launch gate it invalidates and remediate only that gate. Do not restart the project.
3. No unrelated feature work is permitted during active launch certification.
4. Milestone completion does not equal launch readiness. Every gate has an explicit exit criterion.

---

## M2 Outbound Security Invariants (M21.3-TRACE-HARDENING-FINAL)

### OUTBOUND FORENSIC AUTHORITY
`OutboundSafetyGate` is the sole authority for creating outbound `WhatsAppMessage`
records. Every automated outbound attempt MUST pass through `gate.attempt()`.
No code outside `outbound_safety_gate.py` may write `direction='out'` records directly.

### AUTHORIZED PATH INVARIANT
Every `gate.attempt()` call MUST pass `path_id=OutboundPathId.<X>.value`.
Calls with `path_id=None` are blocked at step -1 (before kill switch) and emit a
`OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE` BLOCKER SecurityEvent.
Authorized paths: CE_TEXT, CE_FLOW, CE_INTERACTIVE, CE_LIST, MANUAL_CRM,
BOOKING_FLOW, SYSTEM_NOTIFICATION. Legacy: LEGACY_N8N_AI_PIPELINE (blocked).

### STATUS WEBHOOK CORRELATION
Incoming Meta status webhooks (sent/delivered/read/failed) are matched to
`WhatsAppMessage` records via `wa_message_id`. Status updates follow precedence
(pending < sent < delivered < read < failed). Unknown WAMIDs emit a
`META_STATUS_FOR_UNKNOWN_WAMID` HIGH SecurityEvent.

### UNKNOWN WAMID ALERTING
When a status webhook arrives for a WAMID not in the ledger (table
`whatsapp_messages`) AND OUTBOUND is off, a `SUCCESSFUL_META_SEND_WHILE_OUTBOUND_OFF`
BLOCKER SecurityEvent is created. This catches unauthorized external sends.

### CONTAINER-INDEPENDENT TRACEABILITY
All forensic data needed to reconstruct the outbound timeline is persisted in
the PostgreSQL database BEFORE the Meta API call:
- `whatsapp_messages` (direction=out): pending record with path_id, deployment_id,
  correlation_id, content_fingerprint, text, thread_id, timestamp
- `whatsapp_outbound_dedup`: wa_id + fingerprint + created_at
- `security_events`: unauthorized path detections with details JSON
No container log access is required to determine WHAT was sent, WHEN, by WHOM,
via WHICH path, under WHICH deployment.

### SECRET HANDLING
`WHATSAPP_TOKEN` and `SMTP_PASSWORD` MUST NOT appear as literals in git-tracked
files. Use `${WHATSAPP_TOKEN}` / `${SMTP_PASSWORD}` in compose files and supply
actual values via `.env` (which is gitignored).
Do NOT rotate the Meta token without explicit owner authorization.
The `WHATSAPP_APP_SECRET` must be set for webhook signature verification in
production; empty value enables dev-mode skip (logged, not silent).

### OPERATOR FORENSIC QUERIES
- `GET /security/unauthorized-path-events` — SecurityEvent log with filters:
  since, until, wamid, thread_id, deployment_id, severity, fingerprint
- `GET /security/outbound-ledger` — All automated outbound WhatsAppMessage records:
  wamid (exact WAMID lookup), thread_id, path_id, fingerprint, status, window

---

## Canonical Live Conversation Path

The live message processor is **`conversation_engine.py`** (CE), called by n8n via:

```
POST http://backend:8000/api/conversation/handle
```

### Three-tier architecture

```
n8n transport tier              CE engine tier
──────────────────────          ────────────────────────────────
WhatsApp webhook            →   conversation_engine.py
  audio → Whisper (transcribe)    all conversation routing
  image → GPT-4o (describe)       all state transitions
  20-second debounce               vehicle & location logic
  context aggregation              pricing eligibility
  lead find / create / link        scheduling
  → POST /api/conversation/handle  Flow dispatch
                                   CRM mutations
                                   outbound safety
                                   customer replies via Meta API
```

### Critical flags

**`CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED` must be `false` in production.**

When false (correct): `webhook → n8n → CE` — n8n provides required transport services.  
When true (wrong for production): `webhook → CE directly` — audio transcription, 20-second debounce, context aggregation, and lead creation are all lost.

### n8n AI fallback

The n8n workflow contains an AI pipeline (AI Router → Candidate/State Updater → AI Reply Planner) on the false branch of "IF - Engine Handled? (M18)". This is legacy code. CE returns `handled=true` for all real conversations. The fallback never fires in production.

### Where product fixes belong

Conversation behavior fixes belong in **`backend/app/services/conversation_engine.py`**, not in the n8n workflow, unless the fix specifically concerns:
- Audio transcription → n8n Transcribe Audio node
- Image description → n8n Describe Image node
- Debounce timing → n8n Wait node
- Lead creation/linking → n8n Find Lead / Create Lead nodes

### Reference

Authoritative architecture audit:  
`/opt/ridecheck-crm/forensics/M21_0_0_live_conversation_architecture_reconciliation_20260728.md`

---

## Supersession Notice

`CANONICAL_ROADMAP_M20_M21.md` in this repository contained an incorrect claim:
"the live message processor is n8n's AI pipeline." **This is wrong.** The correct
processor is `conversation_engine.py`, confirmed by M21.0.0 audit (2026-07-28).

---

## Test Environment

Tests use SQLite in-memory. Production uses PostgreSQL via `DATABASE_URL`.

**The routing branch at `backend/app/routes/whatsapp.py:423` is not covered by any
automated test.** All existing tests either stub `n8n_webhook_url=""` or call CE directly.
The live n8n → CE path has no automated test yet (M21.0.3 milestone).

Kill switch (OUTBOUND_ENABLED) is tested by:
- `tests/test_m20_2_kill_switch_proof.py` (RC45–RC48)
- `tests/test_m19_f2_2_outbound_kill_switch.py` (full gate suite)
- `tests/test_m20_4_3_blocked_dispatch.py` (transaction boundaries)
