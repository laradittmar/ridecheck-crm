PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.4-CLEAN-WILD-PREP

# L4.4 — Clean Wild Preparation / Tester Zero State

Date: 2026-09-01
Purpose: return the authorized tester to a TRUE ZERO-STATE customer so the next controlled
Wild can repeat the entire journey (new customer → vehicle → FAQ → location → quote →
acceptance → scheduling → ordered primary/fallback → valid slot → Booking Flow → booking →
Revision/Agenda linkage).

Constraints honoured: crm_test only · production untouched · OUTBOUND OFF throughout ·
no WhatsApp sends · no fake inbound · no Meta changes · no n8n business-logic changes ·
no product code changes · global security evidence preserved · Wild A preserved as failed
historical evidence · tester thread NOT continued.

---

## 1. PART 1 — Wild A evidence preservation

Verified durably on the host filesystem (outside container storage) **before** any DELETE,
then a fresh canonical export was created and hashed.

| Artifact | Content |
|---|---|
| `L4.4_wildA_tester_export_pre_cleanup_2026-09-01T201508Z.txt` | full pre-cleanup dump: contact, thread, state, candidate 130, lead 122, 11 messages (with path_id/deployment_id/correlation_id/WAMIDs), 4 dedup rows, 1 recipient lock, 7 ai_events, 0 revisions / 0 thread_revisions / 0 feedback |
| `…_pre_cleanup_2026-09-01T201508Z.sha256` | `d38c1be30ba4fb90bcb9f4aaec913aff65df60a3b831d5c061d82b2aa4cd9e0f` |
| `L4-WILD-A_backend_logs_2026-09-01T181700Z.txt` | Wild window incl. all 4 CE turns |
| `L4-WILD-A_db_state_export_2026-09-01T190626Z.txt` | audit-time DB export |
| `WILD-A-preenable-backend-20260901_151440.log` | pre-enable runtime |
| `2026-09-01_RIDECHECK_CRM_L4-WILD-A-SCHEDULING-FORENSIC_AUDIT_TEMPORAL-FLOW.md` | committed in 103dd01 |

**WILD A FORENSIC PRESERVATION: PASS**

---

## 2. PART 2 — Tester FK graph audit (pre-cleanup)

Schema re-enumerated (21 tables); L4.3 introduced no migrations, and no new tester-scoped
table exists relative to L4.2.

| Table | Rows | Class |
|---|---|---|
| whatsapp_contacts | 1 | DELETE |
| whatsapp_threads | 1 | DELETE |
| whatsapp_thread_states | 1 | DELETE |
| whatsapp_thread_candidates | 1 | DELETE |
| whatsapp_messages | 11 | DELETE (exported first) |
| whatsapp_outbound_dedup | 4 | DELETE |
| whatsapp_recipient_locks | 1 | DELETE |
| ai_events | 7 | DELETE |
| leads | 1 | DELETE |
| revisions / thread_revisions / feedback_post_revision | 0 / 0 / 0 | DELETE (none existed) |
| flow token (`flow_booking_token`) | NULL | DELETE with state |
| security_events | 733 (0 tester-scoped) | **PRESERVE_GLOBAL** |
| demo contacts/threads/leads/revisions | 32 / 32 / 32 / 28 | **PRESERVE_GLOBAL** |
| viáticos zones, catalog, system settings, pricing | 211 / — / 1 / — | **PRESERVE_GLOBAL** |
| forensic exports + backend logs + audit doc | — | **PRESERVE_FORENSIC** |

FK map confirmed from `pg_constraint` (contacts→threads→{messages, candidates, states,
thread_revisions} cascade; leads→{revisions, feedback} cascade; no FK on
`current_focus_candidate_id`). Deletion was still performed explicitly in FK order rather
than relying on cascades.

---

## 3. PART 3 — Cleanup

One transaction, three guards, no product code involved:

1. **Database guard** — `RAISE EXCEPTION` unless `current_database() = 'crm_test'`.
2. **Leftover guard** — post-delete count of tester rows must be 0 or the transaction aborts.
3. **Preservation guard** — `security_events=733`, `leads=32`, `revisions=28`,
   `viaticos_zones=211` must hold or the transaction aborts.

Deleted (in order): ai_events 7 · dedup 4 · recipient_locks 1 · messages 11 ·
thread_revisions 0 · thread_states 1 · candidates 1 · feedback 0 · revisions 0 ·
threads 1 · leads 1 · contacts 1. **COMMIT** reached with all guards satisfied.

**TESTER CLEANUP: PASS**

---

## 4. PART 4 — Zero-state assertions

All tester-scoped counts are 0: contact, thread, thread state, lead, candidate, revision,
thread revision, appointment/booking, message, dedup, recipient lock, flow token,
ai_events, feedback. A pattern sweep for any residual reference (`%53368330%`, tester
WAMID prefix, `excluded_phones`) returns 0 rows everywhere.

There is no `current_focus`, `last_stage`, `current_cycle_started_at`,
`cycle_reset_pending`, `home_zone`, `preferred_day/time`, `active_requested_date` or
`last_offered_slots` — because the tester does not exist in crm_test.

Independently confirmed by the certified L4.2 suite run against **live crm_test**:
**19/19 PASS** (L4S-01…L4S-10, including the pg-backed zero-state queries).

---

## 5. PART 5 — Test authorization retained

`CLOSED_BETA_ALLOWED_WA_IDS` unchanged in the running container (…8330), verified both in
the process environment and through `get_settings().closed_beta_allowed_wa_ids`.

**TESTER ALLOWLIST: PASS** · **TESTER CRM EXISTENCE: ZERO**

---

## 6. PART 6 — Runtime preflight

| Check | Result |
|---|---|
| Running image | `ridecheck-crm-backend:l4.3-sched-103dd01` |
| deployment_id | `103dd01ca7b5` |
| Database | `crm_test` |
| OUTBOUND | **off** |
| `WHATSAPP_PHONE_NUMBER_ID` | `1196075770246218` |
| Meta phone (read-only Graph GET) | **CONNECTED**, CLOUD_API, quality GREEN, verified_name "Ridecheck Assistance" (note: `code_verification_status=EXPIRED` — verification code only, not connectivity) |
| n8n | container up; inbound webhook **registered** → workflow ACTIVE (GET probe returns "not registered for GET requests", i.e. the POST webhook exists) |
| Booking Flow (Graph GET) | `28104222025943520` "RideCheck Booking" — **PUBLISHED**, category APPOINTMENT_BOOKING, `validation_errors: []` |
| BOOKING_FLOW path wired | YES |

No message was sent; all Meta calls were read-only GETs.

---

## 7. PART 7 — Memory / OOM preflight

`scripts/preflight_memory_check.sh` → **PASS** (swap 4095 MB ≥ 2048, available RAM
1610 MB ≥ 1024). `/swapfile` active (4 GB) and persistent (`/etc/fstab` line 4).
Restart policy `unless-stopped` on backend, n8n and postgres, with memory limits
1 GB / 1 GB / 768 MB. n8n auto-recovery previously proven in L4.3 by a host-side SIGKILL
of the container's main process (RestartCount now 1).

---

## 8. PART 8 — Clean first-inbound rehearsal

`tests/test_l4_4_clean_wild_prep.py` — **9/9 PASS**, no live traffic:

- L44-01 a brand-new ThreadState carries no inherited operational field
- L44-02 the CE cycle-reset guard cannot fire (`cycle_reset_pending=False`)
- L44-03 `_get_active_inspection_location` → `(None, None)` — no inherited location
- L44-04 pricing cannot produce a number without a zone — the Wild #1 stale-$240.000 class
- L44-05 a greeting yields zero scheduling branches — no inherited scheduling
- L44-06 the Booking Flow is not eligible at first inbound (no candidate, no zone) and mints no token
- L44-07 runtime constants: Flow id default + BOOKING_FLOW registered as an authorized path
- L44-08 no text-only booking completion path survives the reset
- L44-09 the L4.3 ordered-scheduling semantics are still in force

Plus L4.2 L4S-05…L4S-10 (new-customer rehearsal, no cycle reset, no inherited
candidate/stage/zone) green against live crm_test.

---

## 9. PART 9 — Booking Flow readiness (deployed container)

```
booking_flow_id     : 28104222025943520
token roundtrip     : make_booking_token → parse_booking_token → thread id OK
_send_booking_flow  : present, uses OutboundPathId.BOOKING_FLOW.value, screen APPOINTMENT,
                      delegates to BookingFlowService.resolve_context()
BookingFlowService  : resolve_context, handle_init, handle_date_selected,
                      handle_prepare_summary, handle_confirm_booking
data-exchange route : /integrations/whatsapp/flows/booking/data-exchange (registered;
                      unencrypted probe → 421, the documented "re-fetch key" response)
booked creators     : ['_process_flow_response']  ← no text-only booking path
```

No live Flow send was performed.

---

## 10. PART 10 — Shared data safety

| Table | Before | After | Δ |
|---|---|---|---|
| whatsapp_contacts | 33 | 32 | −1 (tester) |
| whatsapp_threads | 33 | 32 | −1 (tester) |
| leads | 33 | 32 | −1 (tester) |
| revisions | 28 | 28 | 0 |
| revisions with turno (agenda) | 23 | 23 | 0 |
| thread_revisions | 0 | 0 | 0 |
| whatsapp_messages | 11 | 0 | −11 (all were tester) |
| ai_events | 7 | 0 | −7 (all were tester) |
| outbound dedup | 4 | 0 | −4 (all were tester) |
| recipient locks | 1 | 0 | −1 (tester) |
| candidates | 1 | 0 | −1 (tester) |
| security_events | 733 | 733 | 0 |
| unauthorized-path events | 733 | 733 | 0 |
| viáticos zones | 211 | 211 | 0 |
| system_settings | 1 | 1 | 0 |

Pricing, viáticos, catalog, Agenda demo data, Flow config and the n8n database were not
touched.

---

## 11. PART 11 — Fresh baseline

```
LAST_GLOBAL_INBOUND_ID    : 0        (no messages in crm_test)
LAST_GLOBAL_OUTBOUND_ID   : 0
OUTBOUND_LEDGER_COUNT     : 0
SECURITY_EVENTS_TOTAL     : 733      (max id 734)
UNAUTHORIZED_PATH_EVENTS  : 733      (all historical, none tester-scoped)
NON_TESTER_OUTBOUND_COUNT : 0
max contact id / thread id / lead id : 1914 / 1907 / 121

TESTER CONTACT COUNT  = 0
TESTER THREAD COUNT   = 0
TESTER LEAD COUNT     = 0
TESTER REVISION COUNT = 0
```

Any row appearing above these ids during Wild B is attributable to that Wild alone.

---

## 12. PART 12 — Relevant gate tests

`L1 · L2 · L3 · L4.1 Meta error capture · L4-WILD-01 repro · L4.3 scheduling semantics ·
L4.4 clean-wild prep · M21.3 Booking Flow · M19/M20 kill-switch + safety gate`
→ **270 passed, 1 skipped, 0 failed.**

L4.2 clean-slate against live crm_test → **19/19 PASS**.

Full regression (unchanged scope) → 3 100 passed / 55 failed / 9 errors / 72 skipped,
**zero new failures** against the pre-L4.3 differential baseline.

**Test-harness repair (test-only; no product code changed).** 17 tests in
`test_l4_1_wild_remediation.py` and `test_l4_wild01_repro.py` had been failing for two
harness reasons, both proven and fixed here:

1. `create_engine("sqlite:///:memory:")` without `StaticPool` — every new connection got
   an empty database, so `create_all()` was invisible to the session;
2. `from app.db import Base` — another suite in the same session stubs `app.db`, yielding
   empty metadata; `Base` is now taken from `app.models`.

These 17 tests (Meta error capture, canonical reset, Wild-01 reproduction) are now real
gate coverage again for Wild B instead of silent noise.

**LAUNCH-RELEVANT FAILURES: 0 · UNKNOWN: 0**

---

## 13. Status

- Tester: TRUE ZERO STATE in crm_test, still allowlisted for controlled Wild traffic.
- Wild A: preserved as failed historical evidence (exported + hashed + committed audit).
- Clean-Wild counter: **0/3** — not incremented.
- OUTBOUND: **OFF**. Owner authorization is the only remaining gate before Wild B.
