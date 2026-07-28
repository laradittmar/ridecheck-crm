# RideCheck CRM — Developer Reference

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
