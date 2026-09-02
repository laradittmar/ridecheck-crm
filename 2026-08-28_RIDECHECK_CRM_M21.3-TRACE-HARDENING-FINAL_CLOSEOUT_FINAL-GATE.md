# M21.3-TRACE-HARDENING-FINAL — Closeout (Final Gate)

**Date:** 2026-08-28  
**Branch:** main  
**Milestone:** M21.3-TRACE-HARDENING-FINAL  

---

## Regression Gate

```
platform linux -- Python 3.12.13, pytest-9.1.1
rootdir: /tmp/rctest3

tests/test_m19_r1_outbound_safety_gate.py    15 passed
tests/test_m19_f2_2_outbound_kill_switch.py  26 passed
tests/test_m20_4_3_blocked_dispatch.py        9 passed
tests/test_m2_authorized_paths.py            28 passed
tests/test_m21_3_hardening_final.py          25 passed

TOTAL: 103 passed, 0 failed, 0 skipped
```

---

## Deliverables Completed

### T1, T2, T3 — Pre-existing regression suite

All 78 pre-existing tests across `test_m19_r1`, `test_m19_f2_2`, and `test_m20_4_3` pass under the new architecture. Root-cause fixes:

- `test_m19_r1_outbound_safety_gate.py`: Replaced inline `_test_meta` table definitions with `app.models.Base.metadata.create_all(_engine)` + JSONB→JSON patch. Eliminated stale schema divergence.
- `test_m19_f2_2_outbound_kill_switch.py`: Changed `Base.metadata.create_all(_engine)` to `app.models.Base.metadata.create_all(_engine)` to survive shared pytest session import ordering.
- `test_m2_authorized_paths.py`: Replaced `from app.db import Base as _AppBase` + `_AppBase.metadata.create_all(_engine)` with `import app.models as _am_mod; _AppBase = _am_mod.Base` so the correct ORM metadata is used regardless of import order.

### Path-id caller coverage

All 9 `gate.attempt()` call sites in `conversation_engine.py` now carry `path_id=OutboundPathId.<X>.value`:

| Helper | path_id |
|--------|---------|
| `_send_text_to_wa` | `CE_TEXT` |
| `_send_flow_button` | `CE_FLOW` |
| `_dispatch_vehicle_flow_direct` | `CE_FLOW` |
| `_dispatch_location_flow_direct` | `CE_FLOW` |
| `_send_coverage_response` | `CE_TEXT` |
| `_check_fallback_flow_triggers` (vehicle flow) | `CE_FLOW` |
| `_check_fallback_flow_triggers` (vehicle text) | `CE_TEXT` |
| `_check_fallback_flow_triggers` (location flow) | `CE_FLOW` |
| `_check_fallback_flow_triggers` (location text) | `CE_TEXT` |

### deployment_id auto-population (gate)

`OutboundSafetyGate.attempt()` now auto-populates `deployment_id` from `get_deployment_id()` when the caller omits it. Every `ALLOWED` outbound record carries a non-null `deployment_id`. Tested by T4b, T5a, T11a, T25a, T25d.

### T4 — Crash before Meta call (3 tests, all PASS)

DB-before-Meta invariant verified: pending `WhatsAppMessage` record + dedup entry commit before Meta API is called. Fields: `path_id`, `deployment_id`, `content_fingerprint`, `status="pending"`.

### T5 — Crash after WAMID returned (2 tests, all PASS)

Pending record survives crash without a WAMID. `mark_sent()` idempotency verified on recovery.

### T6–T9 — Status webhook correlation (6 tests, all PASS)

`STATUS_PRECEDENCE` enforced: `pending < sent < delivered < read < failed`. Downgrades silently ignored. Unknown WAMID returns `"not_found"`. Verified WAMID linkage to correct thread.

### T11 — DB-only forensic reconstruction (4 tests, all PASS)

Full outbound lifecycle (pending → sent → delivered) reconstructible from DB alone. Thread-to-contact linkage via joins. Dedup entry provides timing evidence. Blocked records also durable.

### T24 — Webhook signature verification (6 tests, all PASS)

`_verify_signature()` in `app/routes/whatsapp.py` verified:
- Valid HMAC-SHA256 → accepted
- Wrong signature, missing header → rejected
- Empty `WHATSAPP_APP_SECRET` → dev-mode skip (returns True)
- Wrong algorithm prefix → rejected
- Body tampering detected via digest mismatch

### T25 — Deployment evidence (4 tests, all PASS)

`path_id` and `deployment_id` present on all ALLOWED outbound records. `get_deployment_id()` stable within a process.

### Secret hygiene

- `docker-compose.yml`: `WHATSAPP_TOKEN` and `SMTP_PASSWORD` no longer appear as literals — replaced with `${WHATSAPP_TOKEN}` and `${SMTP_PASSWORD}`.
- `.gitignore`: `.env` comment added.

### Operator forensic queries

Two new read-only endpoints:
- `GET /security/unauthorized-path-events` — SecurityEvent log with filters: `since`, `until`, `wamid`, `thread_id`, `deployment_id`, `severity`, `fingerprint`
- `GET /security/outbound-ledger` — Outbound WhatsAppMessage records with filters: `wamid`, `thread_id`, `path_id`, `fingerprint`, `status`, window

### Architecture documentation

`CLAUDE.md` updated with `M2 Outbound Security Invariants` section covering all 6 invariants: FORENSIC AUTHORITY, AUTHORIZED PATH, STATUS WEBHOOK CORRELATION, UNKNOWN WAMID ALERTING, CONTAINER-INDEPENDENT TRACEABILITY, SECRET HANDLING, OPERATOR FORENSIC QUERIES.

---

## Standing Constraints (Unchanged)

- OUTBOUND stays OFF (`OUTBOUND_ENABLED` not set in production)
- Production not touched
- n8n not modified
- No Meta token rotation
- No Booking Flow or scheduling work
- crm_test only for DB work

---

## New Files

| File | Purpose |
|------|---------|
| `backend/app/services/outbound_path_registry.py` | `OutboundPathId` enum, `AUTHORIZED_PATHS`, `get_deployment_id()` |
| `backend/app/services/security_events.py` | `SecurityEvent` persistence, SMTP alerting |
| `backend/app/routes/security.py` | Forensic query endpoints |
| `tests/test_m21_3_hardening_final.py` | T4, T5, T6–T9, T11, T24, T25 (25 tests) |
| `tests/test_m2_authorized_paths.py` | T16–T23 authorized path invariant suite (28 tests) |
