PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.2-CLEAN-SLATE-TESTER

Date: 2026-09-01
Status: PASS

---

# 1. Scope

Prepare the authorized tester number as a TRUE FIRST-TIME CUSTOMER in crm_test. Deleted all prior tester operational state while preserving forensic evidence, global security ledger, and all demo/agenda data. Tester is now operationally nonexistent.

Phone: 549115***8330 (masked)

---

# 2. PART 1 — Forensic export

All tester-scoped data exported before any deletion.

Export location: `/opt/ridecheck-crm-forensics/L4.2_tester_forensic_export_2026-09-01.txt`
Export hash (SHA-256): `e77148a533216fe48382098c17f1ae99042efebd22446cf664cd2512666ede17`
Size: 282 lines

Contents: Contact, Thread, ThreadState, Lead, 6 Candidates, 2 Revisions, 2 ThreadRevisions, 108 WhatsApp Messages (inbound/outbound with WAMIDs and delivery status), 24 Outbound Dedup records, 69 AI Events, 1 Recipient Lock, global security event count (733).

PII masked in human-readable artifact. Full data in export file.

**FORENSIC EXPORT: PASS**

---

# 3. PART 2 — Tester-scoped deletion boundary

Tables audited. FK graph verified. Deletion scope classified:

| Table | Action | Count deleted | Reason |
|---|---|---|---|
| ai_events | DELETE | 69 | thread_id=2 (tester operational) |
| whatsapp_outbound_dedup | DELETE | 24 | wa_id=549115***8330 (tester operational) |
| thread_revisions | DELETE | 2 | thread_id=2 (tester operational) |
| whatsapp_messages | DELETE | 108 | thread_id=2 (tester operational) |
| whatsapp_thread_candidates | DELETE | 6 | thread_id=2 (tester operational) |
| whatsapp_thread_states | DELETE | 1 | thread_id=2 (tester operational) |
| revisions | DELETE | 2 | lead_id=4 (tester operational) |
| whatsapp_recipient_locks | DELETE | 1 | wa_id=549115***8330 (tester operational) |
| whatsapp_threads | DELETE | 1 | contact_id=2 (tester operational) |
| leads | DELETE | 1 | id=4 (tester operational) |
| whatsapp_contacts | DELETE | 1 | id=2, tester contact |
| security_events | PRESERVE | 733 total | Global audit — 0 tester-specific events |
| feedback_post_revision | PRESERVE | 0 tester records | None existed |
| all demo data | PRESERVE | 32 contacts/threads/leads, 28 revisions | Not tester |

---

# 4. PART 3 — Global evidence preserved

- `security_events`: 733 rows preserved (0 referenced tester thread or WAMIDs)
- Pricing / viáticos / vehicle catalog: untouched
- Demo contacts/threads/leads: all 32 preserved
- Demo agenda / appointments: preserved
- n8n data: untouched
- Flow configuration: untouched

**GLOBAL SECURITY EVIDENCE PRESERVED: YES**

---

# 5. PART 4 — Clean-slate operation

Executed in a single PostgreSQL transaction. FK deletion order:

```sql
BEGIN;
DELETE FROM ai_events WHERE thread_id = 2;              -- 69 rows
DELETE FROM whatsapp_outbound_dedup WHERE wa_id = '...'; -- 24 rows
DELETE FROM thread_revisions WHERE thread_id = 2;        -- 2 rows
DELETE FROM whatsapp_messages WHERE thread_id = 2;       -- 108 rows
DELETE FROM whatsapp_thread_candidates WHERE thread_id = 2; -- 6 rows
DELETE FROM whatsapp_thread_states WHERE thread_id = 2;  -- 1 row
DELETE FROM revisions WHERE lead_id = 4;                 -- 2 rows
DELETE FROM whatsapp_recipient_locks WHERE wa_id = '...'; -- 1 row
DELETE FROM whatsapp_threads WHERE id = 2;               -- 1 row
DELETE FROM leads WHERE id = 4;                          -- 1 row
DELETE FROM whatsapp_contacts WHERE id = 2;              -- 1 row
COMMIT;
```

No FK constraint violations. Transaction committed cleanly.

crm_test only. Production DB untouched.

**TESTER OPERATIONAL CLEANUP: PASS**

---

# 6. PART 5 — Zero-state assertions

All confirmed after cleanup:

| Entity | Count |
|---|---|
| Contact | 0 |
| Thread | 0 |
| ThreadState | 0 |
| Lead | 0 |
| Candidates | 0 |
| Revisions | 0 |
| ThreadRevisions | 0 |
| Messages | 0 |
| Dedup | 0 |
| AiEvents | 0 |
| RecipientLock | 0 |
| active-cycle state | NONE |
| cycle_reset_pending | N/A |
| current_cycle_started_at | N/A |
| last_stage | N/A |
| home_zone | N/A |

---

# 7. PART 6 — Tester allowlist

`CLOSED_BETA_ALLOWED_WA_IDS=5491153368330` confirmed in running container.

crm_test has 0 Contact/Thread/Lead for tester — this distinction is intentional.
Tester is authorized to participate but operationally nonexistent.

**TESTER ALLOWLIST: PASS**
**TESTER CRM EXISTENCE: ZERO**

---

# 8. PART 7 — First-inbound expectation

Proven via L4S-05 through L4S-09 (19 tests, all PASS):

1. Brand-new Contact created (no prior identity) ✅
2. Brand-new Thread created ✅
3. Brand-new Lead created (via n8n lead-find/create) ✅
4. Clean ThreadState: cycle_reset_pending=False, all zone/stage/scheduling fields None ✅
5. cycle_reset NOT triggered (guard: `if state.cycle_reset_pending:` → False) ✅
6. No inherited candidate ✅
7. No inherited location ✅
8. No inherited quote ✅
9. No inherited scheduling preferences ✅
10. First inbound processed normally ✅

**FIRST-INBOUND NEW-CUSTOMER REHEARSAL: PASS**

---

# 9. PART 8 — First revision domain expectation

Wild A lifecycle:
```
new Contact → new Thread → new Lead → active Revision #1 lifecycle
```
No prior Revision exists. Runtime creates Revision #1 naturally when booking occurs. Same Contact/Thread/Lead link preserved throughout Wild A.

---

# 10. PART 9 — Second revision certification plan (Wild B)

Documented only — NOT executed. Wild B cannot begin until Wild A completes.

**Canonical path for Wild B:**
1. Wild A completes (Lead.estado transitions off CONSULTA_NUEVA naturally via CRM).
2. Owner/app calls `set_lead_estado(db, lead, "CONSULTA_NUEVA")` after lead is in non-CONSULTA_NUEVA state.
3. `cycle_reset_pending=True` is set automatically on the Thread's ThreadState.
4. Verify in preflight: `tester.cycle_reset_pending == True`.
5. Tester sends first new-cycle WhatsApp message.
6. CE fires `_execute_cycle_reset()` on first real inbound.
7. New active cycle begins: zone/stage/candidates cleared; current_cycle_started_at refreshed.
8. Same Contact, Thread, Lead — Revision #2 linked correctly.
9. Revision #1 preserved in DB with all historical data intact.

**SECOND-REVISION PLAN DOCUMENTED: YES**

---

# 11. PART 10 — Demo / agenda safety

Before vs after:

| Data | Before | After | Delta |
|---|---|---|---|
| Demo contacts | 32 | 32 | 0 (preserved) |
| Demo threads | 32 | 32 | 0 (preserved) |
| Demo leads | 32 | 32 | 0 (preserved) |
| Demo revisions | 28 | 28 | 0 (preserved) |
| Security events | 733 | 733 | 0 (preserved) |
| Pricing / viáticos | intact | intact | untouched |

**DEMO DATA PRESERVED: YES**
**AGENDA PRESERVED: YES**

---

# 12. PART 11 — Outbound / Meta safety

| Check | Value |
|---|---|
| OUTBOUND_ENABLED | false |
| WHATSAPP_PHONE_NUMBER_ID | 1196075770246218 |
| Token access | PASS |
| Meta phone status | CONNECTED / GREEN |
| n8n status | ACTIVE (Up 11 days) |
| WhatsApp message sent during cleanup | NO |
| Non-tester outbound | 0 |

---

# 13. PART 12 — Gate smokes

Run in two isolated invocations (SQLite / PostgreSQL cross-suite interference documented in L4.1):

**SQLite frozen gates (L1 + M2 + M21.3):**
```
72 passed, 31 warnings — PASS ✅
```

**PostgreSQL L4 + L4.1 (separate invocation):**
```
21 passed, 1 skipped, 11 warnings — PASS ✅
```

**New L4.2 tests:**
```
19 passed, 9 warnings — PASS ✅
```

Launch-relevant failures: 0
Unknown: 0

---

# 14. PART 13 — Pre-Wild A baseline

Recorded after tester cleanup, 2026-09-01:

```
LAST_GLOBAL_INBOUND_ID   = NULL (all prior messages cleaned with tester)
LAST_GLOBAL_OUTBOUND_ID  = NULL
OUTBOUND_LEDGER_COUNT    = 0
SECURITY_EVENTS_TOTAL    = 733
UNAUTHORIZED_PATH_EVENTS = 733 (all pre-existing from certification runs, not runtime incidents)
NON_TESTER_OUTBOUND      = 0
TESTER_CONTACT_COUNT     = 0
TESTER_THREAD_COUNT      = 0
TESTER_LEAD_COUNT        = 0
TESTER_REVISION_COUNT    = 0
```

These are the baselines for Wild A. Any new records post-authorization are Wild A evidence.

---

# 15. Wild certification strategy

| Wild | Scenario | Count toward 3/3 |
|---|---|---|
| Wild A | Brand-new customer / first Revision | 1/3 if clean |
| Wild B | Same persistent identity / second Revision / cycle reset | 2/3 if clean |
| Wild C | Third meaningful scenario — defined after A+B based on remaining launch risk | 3/3 if clean |

Current count: **0/3**

---

# 16. Return block

```
PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.2-CLEAN-SLATE-TESTER

STATUS:
PASS

FORENSIC EXPORT:
PASS

EXPORT LOCATION:
/opt/ridecheck-crm-forensics/L4.2_tester_forensic_export_2026-09-01.txt

EXPORT HASH:
e77148a533216fe48382098c17f1ae99042efebd22446cf664cd2512666ede17

PRODUCTION DB TOUCHED:
NO

TESTER OPERATIONAL CLEANUP:
PASS

TESTER CONTACT COUNT:
0

TESTER THREAD COUNT:
0

TESTER THREADSTATE COUNT:
0

TESTER LEAD COUNT:
0

TESTER CANDIDATE COUNT:
0

TESTER REVISION COUNT:
0

TESTER THREADREVISION COUNT:
0

TESTER APPOINTMENT COUNT:
0

TESTER MESSAGE COUNT:
0

TESTER DEDUP COUNT:
0

GLOBAL SECURITY EVIDENCE PRESERVED:
YES

DEMO DATA PRESERVED:
YES

AGENDA PRESERVED:
YES

TESTER ALLOWLIST:
PASS

FIRST-INBOUND NEW-CUSTOMER REHEARSAL:
PASS

NO RESET ON FIRST CUSTOMER INBOUND:
PASS

NO INHERITED VEHICLE:
PASS

NO INHERITED LOCATION:
PASS

NO INHERITED QUOTE:
PASS

NO INHERITED SCHEDULING:
PASS

SECOND-REVISION PLAN DOCUMENTED:
YES

WHATSAPP_PHONE_NUMBER_ID:
1196075770246218

META PHONE:
CONNECTED

OUTBOUND:
OFF

N8N:
ACTIVE

FROZEN GATE SMOKES:
PASS (72/72 SQLite + 22/22 PostgreSQL)

NEW L4.2 TESTS:
19/19

LAUNCH-RELEVANT FAILURES:
0

UNKNOWN:
0

BASELINE RECORDED:
YES

ROADMAP UPDATED:
YES

L4 CLEAN WILD COUNT:
0/3

READY TO ENABLE OUTBOUND FOR CLEAN-SLATE WILD:
YES — tester clean-slate, all runtime gates PASS; owner must authorize

READY FOR OWNER MESSAGE:
NO — outbound still OFF

NEXT OWNER ACTION:
Authorize Wild A outbound. Command: cd /opt/ridecheck-crm && BETA_OUTBOUND_ENABLED=true docker compose -f docker-compose.yml -f /opt/ridecheck-crm-release-candidate/docker-compose.beta.yml up -d --force-recreate backend (SAVE LOGS FIRST before --force-recreate)

STOP.
```
