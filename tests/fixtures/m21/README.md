# M21.0.1 — Sanitized Contract Fixtures

These fixtures represent the exact JSON payload shapes emitted by the live n8n workflow
to `POST /api/conversation/handle`. They are used by contract tests to prove that the
n8n → CE handoff is schema-compatible.

## Source

| File | Source node | Workflow |
|---|---|---|
| `n8n_ce_text_payload.json` | Call Backend Engine (M18) | DaFqDIzVi1f92Hvz |
| `n8n_ce_flow_payload.json` | Call Backend Engine (Flow M18) | DaFqDIzVi1f92Hvz |

**Extraction date:** 2026-07-28  
**Workflow SQLite source:** `/var/lib/docker/volumes/ridecheck-crm_n8n_data/_data/database.sqlite`  
**Audit reference:** `/opt/ridecheck-crm/forensics/M21_0_0_live_conversation_architecture_reconciliation_20260728.md`

## Sanitization

- `thread_id`: replaced with `1` (sequential test value)
- `wa_message_id`: replaced with test-prefixed string
- `wa_id`: replaced with a non-real test phone number (`5491100000099`)
- `flow_token`: replaced with a clearly labeled test string
- `flow_response`: populated with a realistic shape using the same field names and types as live data; vehicle/zone values are non-customer data
- Message text: non-customer example text representing the real field shape

## What is preserved

- Exact field names
- Exact data types (strings, integers, lists of strings, nested object)
- Exact optional/required field structure
- All seven fields from the normal-message contract
- All six fields from the flow-response contract

## The live workflow was not modified

These fixtures were extracted read-only from the n8n SQLite database. The n8n workflow
itself was not changed, activated, or modified in any way during M21.0.1.
