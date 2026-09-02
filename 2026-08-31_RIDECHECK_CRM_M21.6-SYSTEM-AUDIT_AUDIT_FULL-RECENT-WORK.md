PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: M21.6-SYSTEM-AUDIT

Date: 2026-08-31
Author: Claude Sonnet 4.6 (AI assistant, supervised by four parallel read-only research agents)
Scope: All recent milestones (M18 through M21.6-WILD-01-REMEDIATION); CE execution order; test quality; go/no-go for Wild-02

---

## PART 1 — EXECUTIVE SUMMARY

### Central finding

The owner's concern is correct. The system has a **systemic structural pattern**, not isolated bugs: lower-authority data sources (stale thread state, AI extraction from prior turns, historical context) write unconditionally to fields that have already been set by higher-authority sources (LR-3 vehicle-location detection, explicit customer correction, current-turn deterministic parsing). FINDING-01, FINDING-02, and FINDING-03 from Wild-01 are each a specific manifestation of this pattern. The WILD-01 fixes correctly address those three specific paths. **However, the audit has identified five additional instances of the same structural pattern (RISK-01 through RISK-05) and seven candidate-lifecycle integrity gaps that were not addressed by the WILD-01 fixes.**

The test suite has a structural gap that allowed Wild-01 failures to go undetected: approximately 80% of tests seed a clean `WhatsAppThreadState` (no stale zone, no stale year), mock `_call_openai` to short-circuit the post-AI sync block under dirty-state conditions, and assert candidate field values rather than customer-facing reply text. A bug that corrupts a field after the AI sync block but before the quote-build step will pass most existing tests.

### Key findings summary

| Finding | Severity | Status | Category |
|---------|----------|--------|----------|
| FINDING-01 zone overwrite (Wild-01) | CRITICAL | FIXED | Closed |
| FINDING-02 year guard (Wild-01) | CRITICAL | FIXED | Closed |
| FINDING-03 dedup causal (Wild-01) | HIGH | FIXED | Closed |
| Unanswered alert blocked-outbound | MEDIUM | FIXED | Closed |
| RISK-01: `state.customer_name` unconditional AI overwrite | MEDIUM | OPEN | Pattern recurrence |
| RISK-02: `state.preferred_day/time` unconditional AI writes | MEDIUM | OPEN | Pattern recurrence |
| RISK-03: New candidate inherits stale `state.home_zone_*` at creation | HIGH | OPEN | Pattern recurrence |
| RISK-04: Stale `state.preferred_time` used as scheduling fallback | MEDIUM | OPEN | Pattern recurrence |
| RISK-05: `_apply_candidate` without AI `id` silently falls to current_focus | MEDIUM | OPEN | Pattern recurrence |
| `_normalize_zone_from_db` destroys zone_detail when it matches a group name | MEDIUM | OPEN | Candidate lifecycle |
| `_compute_price_quote` write side-effect on `home_zone_group` | LOW | OPEN | Candidate lifecycle |
| No DB uniqueness constraint on `status="current_focus"` | MEDIUM | OPEN | DB integrity |
| Null cycle watermark → prior-cycle candidates visible | MEDIUM | OPEN | Candidate lifecycle |
| 5 `gate.attempt()` call sites missing `path_id` | HIGH | OPEN | Gate integrity |
| `WHATSAPP_APP_SECRET` not set | HIGH (production) | OPEN | Security |
| `SMTP_PASSWORD` not set — unanswered emails silently dropped | MEDIUM | OPEN | Observability |
| `blocked_reason` / `latency_ms` not rendered in dashboard UI | LOW | OPEN | Observability |
| Closeout doc erroneously states email path is Resend | NOTE | OPEN | Documentation |

### Go/No-Go for Wild-02

**CONDITIONAL NO-GO.** Three items must be resolved first (see PART 26 for detail). Two more are strongly recommended. The core CE behavioral fixes from Wild-01 are correct and confirmed in the runtime image. The blocking items are RISK-03 (new candidate inherits stale zone — can produce wrong quote in the first turn of a new session), missing `path_id` at 5 gate call sites (BLOCKER SecurityEvents on every manual send and follow-up while outbound is enabled), and App Secret not set (must be remediated before any outbound-enabled session).

---

## PART 2 — AUDIT SCOPE AND SAFETY COMPLIANCE

### Safety constraints during audit

| Constraint | Status |
|-----------|--------|
| OUTBOUND OFF | CONFIRMED — all research was read-only |
| NO WhatsApp messages sent | CONFIRMED |
| NO Meta changes | CONFIRMED |
| NO n8n activation/deactivation | CONFIRMED |
| NO production DB mutation | CONFIRMED — crm_test read-only |
| NO code changes | CONFIRMED — audit only |
| Evidence preserved | CONFIRMED |

### Audit method

Four parallel read-only research agents conducted independent code reads:
- **Agent A**: Semantic authority map, duplicate sources of truth, candidate lifecycle
- **Agent B**: Architecture docs, CE execution order (phase-by-phase), analogous risks
- **Agent C**: Test quality, regression gap matrix, quote/dedup/unanswered alert audit
- **Agent D**: Booking flow, meta security, n8n transport, email provider, dashboard, runtime parity

All agents read code from `/opt/ridecheck-crm-release-candidate/`. No tools other than Read and Bash (read-only) were used.

---

## PART 3 — SEMANTIC AUTHORITY HIERARCHY

### 3.1 Zone (vehicle location)

Authority rank (highest → lowest):

| Rank | Source | Representation | When authoritative |
|------|--------|---------------|--------------------|
| 1 | LR-3 (`_apply_zone_from_text`) — current turn | `candidate.zone_group / zone_detail` | Explicit vehicle-location phrase in current turn text AND current-focus candidate exists |
| 2 | LR-3 buffer path | `state.home_zone_group / home_zone_detail` | Explicit vehicle-location phrase AND no current-focus candidate yet |
| 3 | Customer-confirmed zone from flow UI | `candidate.zone_group / zone_detail` | Customer selected zone in WhatsApp Flow |
| 4 | AI extraction (`_apply_extracted`) | `state.home_zone_detail` | AI extracted zone detail; guarded: only writes if `not state.home_zone_group` |
| 5 | Historical state from prior session | `state.home_zone_group / home_zone_detail` | Fallback when no LR-3 signal — also contaminates new candidates (RISK-03) |

Authoritative for pricing: `candidate.zone_group / zone_detail` (LR-1 in `_compute_price_quote`). Falls back to `state.home_zone_*` only when candidate zone fields are null/empty.

### 3.2 Year (vehicle year)

| Rank | Source | When authoritative |
|------|--------|-------------------|
| 1 | Exactly 1 unambiguous year token in current turn (`_ct_effective`) | Override any stale candidate year — FINDING-02 fix |
| 2 | `extract_model_del_year()` deterministic parse | New candidate or candidate with null anio |
| 3 | Historical context from prior AI messages | Only when `focus_after.anio is None` and no year in current turn |

Post-FINDING-02 fix: the outer guard `if focus_after and focus_after.anio is None` was changed to `if focus_after:`, and the historical fallback path now carries the `and focus_after.anio is None` guard. Explicit current-turn correction always wins.

### 3.3 Vehicle tipo

| Rank | Source | When authoritative |
|------|--------|-------------------|
| 1 | `VehicleCatalog._catalog_tipo_for()` (F6 guard) | Whenever catalog has a definitive tipo for the model — overrides AI |
| 2 | AI extraction | When no catalog match |

This is correctly enforced by `_enforce_catalog_vehicle()` running after `_apply_candidate()`.

### 3.4 Customer name

| Rank | Source | When authoritative |
|------|--------|-------------------|
| 1 | AI extraction from current turn | Unconditional write — NO established-value guard (RISK-01) |
| 2 | Prior turns (via state persistence) | Only preserved if AI doesn't extract a name in this turn |

`lead.nombre` has a guard (`if not ctx.lead.nombre`). `state.customer_name` does not. These fields are out of sync structurally.

### 3.5 Scheduling fields (preferred_day, preferred_time)

| Rank | Source | When authoritative |
|------|--------|-------------------|
| 1 | Deterministic parse (`_parse_scheduling_text`) — current turn | `det_day`, `det_time` |
| 2 | AI extraction | `extracted.get("preferred_day_iso")`, `extracted.get("preferred_time_str")` — unconditional overwrite (RISK-02) |
| 3 | State persistence | `state.preferred_day`, `state.preferred_time` — used as fallback in scheduling line 3348 |

No "if already set" guard protects `state.preferred_day` or `state.preferred_time` from AI extraction overwrites.

### 3.6 Stage / lead_flag

| Rank | Source | When authoritative |
|------|--------|-------------------|
| 1 | Deterministic validation guards (lines 3220–3240) | AI cannot advance to PRESUPUESTO_ENVIADO without a real price; cannot regress from SCHEDULING |
| 2 | Pricing service output | Forces PRESUPUESTO_ENVIADO when price is available |
| 3 | AI `lead_flag` | Subject to guards above |

Correctly implemented. Not a risk area.

### 3.7 `needs_human`

| Source | Write direction |
|--------|----------------|
| CE `decision["needs_human"]` | Sets `state.needs_human = True`, `lead.necesita_humano = True` |
| Human-toggle endpoint (`/thread/{id}/toggle-human`) | Per CONVERSATION_RUNTIME_CONTRACT.md: known open gap — does not sync both fields. Owner decision pending. |

Not blocking Wild-02 (operator can observe directly in /control).

---

## PART 4 — DUPLICATE SOURCES OF TRUTH

| Field | Representations | Primary authority | Stale-risk |
|-------|----------------|-------------------|-----------|
| Vehicle zone | `candidate.zone_group/zone_detail` (5 representations: candidate, `state.home_zone_group/detail`, `state.pending_zone_*`, AI extracted, `_normalize_zone_from_db` lookup) | LR-3 → candidate | HIGH: state can overwrite candidate at creation (RISK-03) and was overwriting post-AI (FINDING-01, fixed) |
| Vehicle year | `candidate.anio`, current-turn extraction, historical inference | Current-turn (if 1 unambiguous token) | MEDIUM: was FINDING-02, now fixed. Historical fallback protected by `anio is None` guard. |
| Quote | `real_price_quote` (pre-AI), `real_price_quote` (post-AI), AI reply text | Post-AI computation (line 3214) | LOW: recomputed fresh each turn from current candidate/state |
| Customer name | `state.customer_name`, `lead.nombre` | `lead.nombre` has guard; `state.customer_name` does not | MEDIUM: RISK-01, AI overwrite not guarded |
| Scheduling time | `state.preferred_time`, `det_time` (deterministic), `extracted["preferred_time_str"]` | Deterministic parse | MEDIUM: RISK-02/04, AI extraction writes unconditionally |
| Current focus candidate | `ctx.current_focus_candidate_id` (state field), runtime resolved `_focus_candidate()` return | `_focus_candidate()` return | MEDIUM: unresolvable ID falls to candidates[0] silently |
| `home_zone_group` | State field, but also written by `_compute_price_quote` as a side effect (lines 5947-5948) | Should be state — but pricing writes here | LOW: side-effect is normalization only, not a correction |

---

## PART 5 — CE EXECUTION ORDER MAP (complete)

### PHASE 0 — DISPATCH (`_handle`)

1. `_load_context(thread_id)` — reads Thread, Contact, Lead, State; loads `ctx.candidates` (watermark-filtered by `current_cycle_started_at`); loads `ctx.db_messages` (newest-20, watermark-filtered)
2. Dedup check — reads `state.last_processed_inbound_wa_message_id`; early-return if match
3. `_get_or_create_state(ctx)`
4. Captures `previous_cursor = state.last_processed_inbound_wa_message_id`; sets `ctx.inbound_wa_message_id = event.wa_message_id`
5. Writes `state.last_processed_inbound_wa_message_id = event.wa_message_id`
6. Lead None check — commits cursor if no lead; early-return `no_lead`
7. Cycle reset — if `state.cycle_reset_pending`: `_execute_cycle_reset()` — writes new cycle watermarks; clears all ACTIVE_REVISION state fields; clears `cycle_reset_pending`; F2 reload of candidates + messages
8. `_maybe_set_attribution(ctx, state)` — first-write-only for `acq_source`, `inbound_channel`
9. `state.needs_human` guard — early-return `skipped_human` if true
10. Routes to `_process_flow_response` or `_process_text`

### PHASE 1 — UNDERSTAND: Burst assembly (`_process_text`)

- `_fetch_burst_messages()` — fetches any messages between `previous_cursor` and current
- Constructs `current_turn_text` (joined burst texts), `ai_input_messages`
- Sets `self._burst_message_count`, `self._burst_earliest_inbound_db_id`

### PHASE 2 — PRE-AI SERVICE GATES (in order, all early-return capable)

A. Motorcycle gate → `_motorcycle_human_handoff()`
B. Phone-call gate → `_handle_phone_call_escalation()`
C. Unsupported-service gate → `_handle_explicit_service_gate()`
D. FAQ bypass → `_handle_general_information_ai()` (pure-informational turns)
G. Inspectability gate → `_handle_vehicle_inspectability_gate()`
Website form → `_handle_website_form()`

### PHASE 3 — PRE-AI RECONCILE (LR-N evidence extraction)

- Price requery shortcut (QUOTED/SCHEDULING + price question)
- Acceptance shortcut (`_is_acceptance`)
- Date shortcut (`_parse_scheduling_text`)
- Fuzzy confirmation handler
- Vehicle catalog pre-detection: `lookup_vehicle()` → creates candidate if none → `pre_detected_vehicle`
- Fuzzy vehicle lookup (if no exact hit)
- Numeric model disambiguation
- `extract_model_del_year()` → patches `anio` if blank
- Layer F qualifying intent gate
- Zone snapshot captured: `_zone_at_turn_start`, `_cand_zone_at_turn_start`
- **LR-3 ZONE WRITE**: `_apply_zone_from_text(current_turn_text)` — if vehicle-location clause detected AND current focus exists → writes `focus.zone_group`, `focus.zone_detail` directly to candidate; sets `_vehicle_location_written=True`. If no focus → writes to `state.home_zone_*` (buffer path).
- Zone normalization: `_normalize_zone_from_db()` — fills `state.home_zone_group` from DB if detail-only
- Pre-AI price: `real_price_quote = _compute_price_quote(ctx, state)` — first computation
- Routing gate, fallback triggers, safety-net phone gate
- `self._answer_source = None`

### PHASE 4 — DECIDE: AI call

- `_snap_pre = resolve_field_evidence(ctx, state)`
- `_call_openai(messages_for_ai)` → sets `self._ai_invoked = True`
- Deferred-interest intercept (NU-6/7)

### PHASE 5 — POST-AI RECONCILE (sync extracted facts)

- `_apply_extracted(ctx, state, extracted)`:
  - `state.customer_name` **unconditional** if AI provided one (RISK-01)
  - `state.home_zone_detail` only if `not state.home_zone_group` (guarded — correct)
  - `state.preferred_day` unconditional (RISK-02)
  - `state.preferred_time` unconditional (RISK-02)
- `_normalize_zone_from_db()` — second normalization pass
- `_apply_candidate(ctx, cand_data)` — AI candidate update/create with F6 catalog guard
- `_enforce_catalog_vehicle()` — catalog authority for tipo_vehiculo (post-AI override)
- `_apply_narrative_interpretation()`
- `focus_after = _focus_candidate(ctx)` — re-resolved after all mutations
- Vehicle-change re-quote guard
- F3-T2 zone-correction re-quote guard
- **POST-AI ZONE SYNC (FINDING-01 FIXED)**: gap-fill only — `state.home_zone_group` → `focus_after.zone_group` only when `not focus_after.zone_group`
- **YEAR SYNC (FINDING-02 FIXED)**: if exactly 1 year in current turn → writes `focus_after.anio` unconditionally; if 0 years AND `anio is None` → history fallback
- Second price recompute: `real_price_quote = _compute_price_quote(ctx, state)` (line 3214)

### PHASE 6 — CALCULATE: Post-AI flag and reply assembly

- AI `lead_flag` validation guards
- Writes `lead.flag`, advances `state.last_stage`
- Human escalation → `state.needs_human = True`
- Deterministic quote override (pricing service forces PRESUPUESTO_ENVIADO)

### PHASE 7 — COMPOSE: Scheduling + reply

- `pday = det_day or state.preferred_day or state.active_requested_date`
- `ptime = det_time or extracted.get(...) or state.preferred_time` (RISK-04: stale fallback)
- If both: `_try_schedule_and_flow()`; if day only: `_handle_day_only_request()`
- Reply scrubbing: `_scrub_invented_price()`, `_scrub_scheduling_confirmation()`

### PHASE 8 — SEND

- `_send_text_to_wa(ctx, reply)`:
  - F3 FAQ supplement: `_compose_secondary_answers()`
  - F5.1 required-next-question append
  - `gate.attempt(causal_inbound_wa_message_id=ctx.inbound_wa_message_id, path_id=CE_TEXT)`
  - Atomic commit after successful send

---

## PART 6 — FINDING-01 VERIFICATION (zone overwrite)

### Root cause

`_apply_zone_from_text` (LR-3) correctly wrote `zone_group='Sur'`, `zone_detail='Berazategui'` directly to the current-focus candidate. The post-AI sync block then read `state.home_zone_group='CABA'` / `state.home_zone_detail='Palermo'` (stale from prior session) and overwrote the candidate unconditionally.

### Fix verification

**Before (bug):**
```python
if focus_after and _vehicle_location_written:
    if state.home_zone_group:
        focus_after.zone_group = state.home_zone_group   # unconditional overwrite
    if state.home_zone_detail:
        focus_after.zone_detail = state.home_zone_detail  # unconditional overwrite
```

**After (fixed):**
```python
if focus_after and _vehicle_location_written:
    if state.home_zone_group and not focus_after.zone_group:    # gap-fill only
        focus_after.zone_group = state.home_zone_group
    if state.home_zone_detail and not focus_after.zone_detail:  # gap-fill only
        focus_after.zone_detail = state.home_zone_detail
```

**Confirmed present in image** (`m21.6-wild01-820f4d6`): `not focus_after.zone_group` confirmed via runtime parity check.

**Regression tests**: `test_wild01_remediation.py::TestR1ZoneAuthorityFinding01::test_lr3_zone_survives_stale_state` — PASS. `test_wild04r_f4_location_authority.py` Cases A-B — PASS.

---

## PART 7 — FINDING-02 VERIFICATION (year guard)

### Root cause

Candidate 129 had `anio=2020` from prior session. Old outer guard: `if focus_after and focus_after.anio is None:` — because `anio=2020` (not None), the entire year extraction block was skipped. The customer's explicit correction "2008 del 2015" was silenced.

### Fix verification

**Before (bug):**
```python
if focus_after and focus_after.anio is None:
    # block skipped when anio=2020
```

**After (fixed):**
```python
if focus_after:                          # always run when focus exists
    if len(_ct_effective) == 1:
        year_hit = _extract_year_from_text(...)
        if year_hit:
            focus_after.anio = year_hit  # unconditionally overrides stale
    elif len(_ct_effective) == 0 and focus_after.anio is None:  # guard added
        # history fallback — only when candidate lacks a year
        ...
```

**Authority matrix post-fix:**
- 1 unambiguous year in current turn → always apply (overrides stale) ✓
- 0 years in current turn AND candidate has a year → preserve existing ✓
- 0 years in current turn AND candidate has no year → history fallback ✓
- 2+ years → ambiguous, no sync ✓

**Confirmed present in image**: `focus_after.anio is None` and `if focus_after:` both confirmed via runtime parity check.

**Regression tests**: `test_wild01_remediation.py::TestR2YearAuthorityFinding02` (R2a, R2b) — PASS. `test_wild03_cross_turn_year.py` — PASS.

---

## PART 8 — FINDING-03 VERIFICATION (dedup causal identity)

### Root cause

`WhatsAppOutboundDedup` dedup key was `(wa_id, message_kind, content_fingerprint)` within a 10-minute rolling window. When CE tried to send "¡Hola!" in response to a new inbound greeting, the dedup found the same text from a prior send (same fingerprint, same window) and blocked it — even though it was caused by a different inbound event.

### Fix verification

**`models.py`**: `WhatsAppOutboundDedup.causal_inbound_wa_message_id: Mapped[Optional[str]]` — confirmed present.

**`outbound_safety_gate.py`**: `_check_dedup()` adds WHERE clause when causal ID present:
```python
if causal_inbound_wa_message_id is not None:
    q = q.where(WhatsAppOutboundDedup.causal_inbound_wa_message_id == causal_inbound_wa_message_id)
```
Different inbound event → different causal ID → dedup does not match → ALLOWED.

**`conversation_engine.py`**: `_Context.inbound_wa_message_id` field added; set in `_handle()`; passed at both `gate.attempt()` call sites.

**Alembic migration** `20260831_wild01_dedup_causal_inbound`: Applied to crm_test. Column confirmed in DB.

**Regression tests**: R3 (new inbound ALLOWED), R4a (same inbound BLOCKED), R4b (legacy no-causal still blocks), R6a/R6b — all PASS.

---

## PART 9 — SECONDARY FIX: UNANSWERED ALERT

### Root cause

Both SQL paths (per-event and thread-level) computed "last message direction" without filtering on `status`. A `direction='out', status='blocked'` row made the thread appear answered.

### Fix verification

The thread-level inline query in `_run_check()` (lines 192-204) carries:
```sql
AND wm.status NOT IN ('blocked', 'failed')
```

**Dead constant**: `_FIND_THREAD_UNANSWERED_SQL` (defined at lines 59-80) is never referenced in `_run_check()`. It is dead code. The live path is the inline query. The fix is correctly applied to the live path. However, the constant and the inline query are now diverged — a maintainability hazard (see PART 18 for detail).

**Regression tests**: R5a, R5b — PASS.

---

## PART 10 — SECONDARY FIX: OPS DASHBOARD API

### Fix verification

`/api/ops/messages` SELECT (`ops_dashboard.py` lines 308-389) confirmed to include:
- `WhatsAppMessage.text` (full, untruncated)
- `WhatsAppContact.wa_id` (unmasked, alongside `wa_id_masked`)
- `WhatsAppMessage.blocked_reason`
- `AiEvent.latency_total_ms` via `outerjoin(AiEvent, AiEvent.wa_message_id == WhatsAppMessage.wa_message_id)`, labeled `latency_ms`

All four fields confirmed present in API response.

**Gap remaining**: The dashboard UI (`control_view.py` JS detail panel) does NOT render `blocked_reason` or `latency_ms`. The API delivers them; the frontend does not display them. Operators cannot see blocked reason or CE latency per-message in the UI without querying the API directly. See PART 23.

---

## PART 11 — POST-FIX ANALOGOUS RISKS (new findings, same structural family)

These are five additional instances of the stale-overwrite pattern identified in the current post-WILD-01-fix codebase.

### RISK-01 — `state.customer_name` unconditional AI overwrite

**File:Line**: `conversation_engine.py`, `_apply_extracted()` ~line 5420
**Code**:
```python
if extracted.get("customer_name"):
    state.customer_name = extracted["customer_name"]   # no "not state.customer_name" guard
```
**vs `lead.nombre`** (line 5422): `if ctx.lead and not ctx.lead.nombre:` — has the guard.

**Risk**: AI extraction from any turn can overwrite an established customer name. If the customer mentions a third party's name, or the AI hallucinates a name, the previously correct `state.customer_name` is silently replaced. The guard that protects `lead.nombre` does not protect `state.customer_name`.

**Severity**: MEDIUM. Does not affect pricing or scheduling directly; affects greeting text and CRM display.

**Recommended fix**: Add `not state.customer_name` guard to match `lead.nombre` pattern.

---

### RISK-02 — `state.preferred_day` and `state.preferred_time` unconditional AI writes

**File:Lines**: `conversation_engine.py`, `_apply_extracted()` lines ~5433, ~5442
**Code**:
```python
if extracted.get("preferred_day_iso"):
    state.preferred_day = raw_day    # no "not state.preferred_day" guard

if extracted.get("preferred_time_str"):
    state.preferred_time = raw_time  # no "not state.preferred_time" guard
```

**Risk**: If the AI incorrectly extracts a scheduling preference from a non-scheduling turn (e.g., customer says "el martes pasado" referring to something else), the unconditional write silently replaces a previously confirmed `preferred_day`. Scheduling then uses this stale/incorrect value.

**Severity**: MEDIUM. Affects SCHEDULING stage. Could cause wrong date/time used for appointment booking.

**Recommended fix**: Add `not state.preferred_day` and `not state.preferred_time` guards, OR restrict the write to only run when `state.last_stage` is SCHEDULING/QUOTED.

---

### RISK-03 — New candidate inherits stale `state.home_zone_*` at creation

**File:Lines**: `conversation_engine.py`, `_create_candidate_from_catalog()` lines ~5650-5659
**Code**:
```python
_init_zone_group = state.home_zone_group if state else None
_init_zone_detail = state.home_zone_detail if state else None
candidate = WhatsAppThreadCandidate(
    zone_group=_init_zone_group,
    zone_detail=_init_zone_detail,
    ...
)
```

**Risk**: When a new candidate is created (e.g. vehicle detected in first turn of a new session), it inherits whatever `state.home_zone_group / home_zone_detail` currently contains. If state has stale zone data from a prior session (e.g. Wild-01 left `state.home_zone_group='CABA'`), the new candidate is born with the wrong zone. If no LR-3 zone phrase appears in the creation turn, the stale zone persists, and the customer is quoted for the wrong zone from the very first reply.

**When this becomes dangerous**: The F5.1 required-next-question guard asks for zone when `focus.zone_group` is None. If the candidate was born with a stale zone, F5.1 does NOT ask — it considers zone known. The customer gets an unchallenged quote for the wrong zone without CE ever asking to confirm.

**Severity**: HIGH. Can produce a wrong quote in the first CE turn of Wild-02 if `state.home_zone_*` is populated from Wild-01's stale state. REQUIRED FIX before Wild-02.

**Recommended fix**: At candidate creation, only inherit `state.home_zone_*` if the state was written in the CURRENT CYCLE (i.e., `state.current_cycle_started_at` is not None and the zone was written after it). A simpler guard: do not inherit at all and rely on LR-3 to write zone in the same turn, or on F5.1 to prompt if zone is missing.

---

### RISK-04 — Stale `state.preferred_time` used as scheduling fallback

**File:Line**: `conversation_engine.py` line 3348
**Code**:
```python
ptime = det_time or extracted.get("preferred_time_str") or state.preferred_time
```

**Risk**: If `det_time` (deterministic parse) is None and AI extraction didn't produce a time for this turn, the code uses `state.preferred_time` — which may have been set by a prior rejected scheduling slot. There is no clearing of `state.preferred_time` after a rejected slot (only `_handle_day_only_request` sets `active_requested_date` and clears the fields, but that runs AFTER this line). A stale prior-turn `state.preferred_time` can be silently combined with a new `preferred_day`, creating an unexpected scheduling attempt.

**Severity**: MEDIUM. Affects SCHEDULING stage only.

---

### RISK-05 — `_apply_candidate` without AI `id` falls to current_focus silently

**File:Lines**: `conversation_engine.py` `_apply_candidate()` lines ~5528-5531
**Code**:
```python
else:
    target = self._focus_candidate(ctx)   # AI omitted id — silently use current focus
    target_id = target.id if target else None
```

**Risk**: When the AI returns `action=update` without an `id` field, CE silently applies the update to the current focus candidate. If the AI is reasoning about a different vehicle or if the focus candidate is ambiguous (e.g. two unresolved candidates), the wrong candidate is mutated. Zone and year fields from the AI dict can be written to the wrong record.

**Severity**: MEDIUM. Not a regression — this behavior predates WILD-01. But it's the same structural coupling: AI omits context → CE guesses → wrong record mutated.

---

## PART 12 — CANDIDATE LIFECYCLE RISKS

Seven additional integrity gaps from the candidate lifecycle audit (Agent A findings):

### CL-01 — `_normalize_zone_from_db` destroys `zone_detail` when it matches a group name

**Risk**: `_normalize_zone_from_db` reads the DB to fill `home_zone_group` from a known `home_zone_detail`. If the detail value matches a zone group name (e.g., "Gran Buenos Aires"), the normalization sets `home_zone_group='Gran Buenos Aires'` and then blanks `home_zone_detail` (since "Gran Buenos Aires" is a group, not a detail). This is a silent data destruction that can change the zone used for pricing.

**Severity**: MEDIUM.

### CL-02 — `_compute_price_quote` has an undocumented write side-effect

**Location**: `conversation_engine.py` lines ~5947-5948

`_compute_price_quote` is named as a read/compute function but also writes `state.home_zone_group` as a normalization side-effect. This is an unexpected write in a function that callers expect to be pure. Callers who assume the function has no side-effects may reason incorrectly about state after calling it.

**Severity**: LOW. The write is a normalization (correct data), not corruption. But the implicit nature makes the code harder to audit.

### CL-03 — No DB uniqueness constraint on `status="current_focus"`

**Risk**: `WhatsAppThreadCandidate.status = 'current_focus'` is enforced by CE logic (only one candidate per thread should hold this status at a time), but there is no database-level UNIQUE constraint on `(thread_id, status) WHERE status = 'current_focus'`. Under concurrent requests (e.g., if Meta sends two webhooks in rapid succession for the same thread before the dedup cursor is written), two candidates could become `current_focus` simultaneously. `_focus_candidate()` would then pick one nondeterministically.

**Severity**: MEDIUM. Mitigated in practice by the `state.last_processed_inbound_wa_message_id` dedup cursor at line 1571 and n8n's 20-second debounce, but not guaranteed at the DB level.

### CL-04 — Null cycle watermark allows prior-cycle candidates to appear

**Risk**: `WhatsAppThreadState.current_cycle_started_at` can be None (pre-cycle-reset sessions, or new threads). The watermark filter in `_load_context()` uses `created_at >= current_cycle_started_at` only when the watermark is not None. If watermark is None, all candidates for the thread are loaded, including candidates from prior test sessions.

**Severity**: MEDIUM. Directly related to FINDING-01 root cause (prior session data visible in new session).

### CL-05 — `current_focus_candidate_id` unresolvable → silent fallback

**Risk**: If `state.current_focus_candidate_id` points to a candidate ID that doesn't exist in `ctx.candidates` (e.g., it was created in a prior cycle and filtered by the watermark), `_focus_candidate()` silently falls back to `candidates[0]` (sorted by `created_at DESC`). This fallback picks the most recent candidate, which may be correct, but it introduces ambiguity and masks data inconsistency.

**Severity**: MEDIUM.

### CL-06 — Provisional revision orphan on double scheduling handoff

**Risk**: If the scheduling handoff path fires twice (e.g., a retry due to a transient error), `_try_schedule_and_flow()` can create a second `ThreadRevision` in PROVISIONAL status without advancing the first one to CONFIRMED or REJECTED. If the second handoff then fails, both revisions remain in PROVISIONAL, and CE's next turn may pick the wrong one as the current revision.

**Severity**: LOW-MEDIUM. Requires specific retry scenario to manifest.

### CL-07 — AI `_apply_candidate` update can overwrite LR-3-validated zone in same turn

**Risk**: `_apply_candidate()` runs in PHASE 5 after `_apply_extracted()`. The AI's `candidate` dict can include `zone_group` and `zone_detail`. The F6 guard protects `tipo_vehiculo` (from catalog) but there is no analogous guard protecting zone fields that LR-3 wrote in PHASE 3. If the AI extracts a different zone from the same text that LR-3 already resolved, `_apply_candidate()` can overwrite the LR-3-authoritative zone with the AI's version.

**Note**: This risk is partially mitigated by the post-AI zone sync guard added in FINDING-01 (which protects the `_vehicle_location_written` path), but the `_apply_candidate` overwrite runs BEFORE that sync. The FINDING-01 fix prevents stale state from overwriting LR-3; it does not prevent AI candidate data from overwriting LR-3.

**Severity**: MEDIUM. Requires AI to extract a zone in the candidate dict AND LR-3 to have written a different (correct) zone in the same turn.

---

## PART 13 — TEST QUALITY AUDIT

### Test inventory summary

80+ test files audited. Classification:

| Category | Count | Description |
|----------|-------|-------------|
| UNIT | ~15 | Tests of a single function/helper in isolation; no CE call |
| SERVICE | ~50 | Tests calling CE or a service with mocked AI + mocked/patched gate |
| POSTGRES | ~8 | Tests requiring a real PostgreSQL connection |
| RUNTIME_HTTP | ~2 | Tests hitting the live container HTTP API |
| UTIL | ~3 | Reset scripts and cleanup utilities |

### Key file classifications

| File | Invariant vs Impl | Dirty history? | AI mock? | Gate mock? |
|------|------------------|----------------|----------|------------|
| `test_wild01_remediation.py` | INVARIANT | YES | YES | PARTIAL |
| `test_wild04r_f4_location_authority.py` | INVARIANT | YES | YES | YES |
| `test_wild04r_cycle_boundary.py` | INVARIANT | YES | YES | YES |
| `test_m21_1_7_consolidated_semantic_regression.py` | INVARIANT | PARTIAL | YES | YES |
| `test_m18_business_logic.py` (410 tests) | MIXED | PARTIAL | YES | PARTIAL |
| `test_messy_turn_reconciliation.py` | INVARIANT | YES | YES | YES |
| `test_m21_2_d_commercial_flow_integrity.py` | INVARIANT | YES | YES | YES |
| `REALITY_TEST_RC_baseline.py` | INVARIANT | YES | YES (urlopen) | YES |
| `smoke_f4_postgres.py` | INVARIANT | YES | YES (urlopen) | PARTIAL |
| `test_m21_3_hardening_final.py` (T4-T25) | INVARIANT | NO | YES | NO (real gate) |
| `test_m19_r1_2_pg_integration.py` | INVARIANT | YES | YES | NO (real gate) |

---

## PART 14 — WHY TESTS PASS WHILE WILD SESSIONS FAIL

Three structural reasons explain the systematic gap:

### Reason 1: Clean fixture bias

Approximately 80% of SERVICE-level tests seed a `WhatsAppThreadState` with `home_zone_group=None`, `home_zone_detail=None`, no prior candidate year. The stale-overwrite bugs (FINDING-01, FINDING-02, RISK-03) require a dirty state — a thread that has been used in a prior session with different zone or year data. No test before `test_wild04r_f4_location_authority.py` combined:
- A prior-session stale zone in `state.home_zone_*`
- A current-turn LR-3 zone write to the candidate
- An assertion on the customer-facing quote text (not just the candidate field value)

### Reason 2: AI mock short-circuits the post-AI sync block under dirty conditions

Most service tests mock `_call_openai` to return a canned JSON dict and mock `_send_text_to_wa`. The mocked AI call still exercises the post-AI sync block (PHASE 5), but the fixture state is always clean, so the sync block finds nothing to conflict with. The real defect required:
1. `state.home_zone_*` to be populated with stale prior-session data (dirty fixture)
2. `_apply_zone_from_text` to write the correct zone to the candidate in PHASE 3 (LR-3 path)
3. The post-AI sync in PHASE 5 to then overwrite the candidate zone with the stale state value

Step 1 was missing in all pre-Wild-01 tests.

### Reason 3: Field-value assertions vs. customer-facing text assertions

Many tests assert `candidate.zone_group == 'Palermo'` (the field before the bug's overwrite) rather than checking that the customer's WhatsApp reply contains 'Palermo'. A bug that mutates the field *after* the test reads it but *before* the quote is built would pass a field assertion but fail a reply-text assertion. The Wild-01 R1 test was the first to use the `_send_text_to_wa` mock's `call_args` to verify the actual text sent to the customer under dirty-state conditions.

### Structural conclusion

The test gap is not random — it reflects a testing philosophy that evolved incrementally, adding tests for each new fix without always constructing the exact dirty-history conditions that would expose the next class of bugs. The WILD-01 remediation and `test_wild04r_*` suite represent the beginning of a shift toward dirty-history invariant testing, but RISK-01 through RISK-05 do not yet have corresponding dirty-history tests.

---

## PART 15 — REGRESSION GAP MATRIX

| # | Invariant | Tested? | Level | Dirty History? | Gap |
|---|-----------|---------|-------|----------------|-----|
| 1 | Explicit current-turn zone overrides stale historical zone | YES | SERVICE | YES | NONE — R1, F4-A/B, F3-exact |
| 2 | Explicit current-turn year overrides stale historical year | YES | SERVICE | YES | NONE — R2, wild03-cross-turn |
| 3 | New inbound event with same text gets a reply | YES | SERVICE | YES | NONE — R3 |
| 4 | Same inbound retry is deduped | YES | SERVICE | YES | NONE — R4a/b |
| 5 | Blocked outbound does not mask unanswered thread | PARTIAL | SERVICE | YES | PARTIAL — R5 tests SQL in isolation; no end-to-end `_run_check()` test with blocked outbound in full alerting path |
| 6 | Active candidate zone used for pricing (not state fallback) | YES | SERVICE | YES | NONE — F4 comprehensively covers this |
| 7 | New cycle does not inherit prior cycle's candidates | YES | SERVICE | YES | NONE — CYCLE-07/08, F5-cycle-safe |
| 8 | Acceptance of prior-cycle quote rejected in new cycle | YES | SERVICE | YES | THEORETICAL — cycle reset clears `last_stage` before acceptance check; no test for sending acceptance keyword as first message post-reset |
| 9 | Audio transcript year/location extracted and applied to candidate under dirty state | PARTIAL | UNIT | NO | GAP — ASR tests are unit-level (resolver only); no integration test combining audio transcript → CE turn → year+zone on candidate with stale prior state |
| 10 | Multi-turn correction: user corrects year mid-flow | YES | SERVICE | PARTIAL | NONE — wild03 X04, messy-turn M2 |
| 11 | Location correction mid-flow: verbal denial marker ("no, está en Berazategui") | PARTIAL | SERVICE | YES | PARTIAL GAP — M3 covers zone correction post-QUOTED; SEQ04 covers narrative correction via AI. No integration test for verbal correction + denial marker with stale candidate zone already set from prior turn |
| 12 | Booking uses only active-cycle data | YES | SERVICE | YES | NONE — BF-WM-02 covers this explicitly |
| 13 | `needs_human` cleared on new cycle | YES | SERVICE | YES | NONE — CYCLE-04 |
| 14 | Quote recomputed after explicit year correction | PARTIAL | SERVICE | NO | PARTIAL — year changes don't affect pricing (year is not a pricing input); test M2 checks year updated on candidate but doesn't assert re-priced quote. Invariant is vacuous for year-only corrections. |
| 15 | New candidate does NOT inherit stale state zone | NO | — | — | FULL GAP — RISK-03 has no test. No test creates a candidate against a thread with stale `state.home_zone_*` and then asserts that the candidate zone is empty (not inherited). |
| 16 | `state.customer_name` not overwritten by AI extraction when already established | NO | — | — | FULL GAP — RISK-01 has no test. |
| 17 | Manual CRM send does not generate BLOCKER SecurityEvent | NO | — | — | FULL GAP — no test for `path_id` in `whatsapp.py` send endpoints |

---

## PART 16 — QUOTE AUTHORITY AUDIT

### What `_compute_price_quote` uses

Inputs:
- `focus = _focus_candidate(ctx)` — requires `focus.tipo_vehiculo` (non-null)
- `zone_group = (focus.zone_group or "") or (state.home_zone_group or "")` — candidate zone authoritative; state fallback only when candidate zone is null/empty
- `zone_detail = (focus.zone_detail or "") or (state.home_zone_detail or "")` — same precedence

Does NOT use: candidate `anio`, revision fields, lead fields.

### Quote freshness

`_compute_price_quote` is called twice per turn:
1. Pre-AI (PHASE 3, line ~3011): uses current candidate/state before AI mutations
2. Post-AI (PHASE 5, line 3214): recomputed after ALL mutations (zone sync, year sync, candidate update, catalog enforcement)

The reply uses the post-AI computation. This is correct — the customer always sees a price based on the most current data for the turn.

### Can a stale quote satisfy the PRESUPUESTO_ENVIADO guard?

The guard checks `real_price_quote is None`. Since `_compute_price_quote` is always called fresh this turn, "stale" only means same-data-as-prior-turn. If zone/vehicle didn't change, the price is the same as last turn — not a staleness bug. If zone or vehicle changed, the F3-T2 or vehicle-change re-quote guard will reset the stage to QUALIFYING before the PRESUPUESTO_ENVIADO guard fires.

**Verdict: No stale-quote risk in the current implementation.**

### Can prior-cycle acceptance leak into current cycle?

No. Acceptance check (`_is_acceptance`) only fires when `state.last_stage == STAGE_QUOTED`. `_execute_cycle_reset` sets `state.last_stage = None` (line 5316) and runs **before** the acceptance check in `_handle()`. After reset, `last_stage=None` → acceptance gate never entered.

---

## PART 17 — DEDUP / GATE CALL SITE AUDIT

### All `gate.attempt()` call sites

| Location | Context | `causal_inbound_wa_message_id`? | `path_id`? | Assessment |
|----------|---------|----------------------------------|------------|------------|
| `conversation_engine.py:5974` | `_send_text_to_wa` | YES (`ctx.inbound_wa_message_id`) | YES (CE_TEXT) | CORRECT |
| `conversation_engine.py:6015` | `_send_flow_button` | YES | YES (CE_FLOW) | CORRECT |
| `conversation_engine.py:2261` | `_dispatch_vehicle_flow_direct` (blocked-dispatch audit sentinel) | NO | YES | LEGITIMATE OMISSION — only fires when OUTBOUND=false, creates audit record only |
| `conversation_engine.py:2298` | `_dispatch_location_flow_direct` | NO | YES | Same — audit sentinel |
| `conversation_engine.py:2324` | `_send_coverage_response` | NO | YES | Same |
| `conversation_engine.py:2386` | `_check_fallback_flow_triggers` vehicle flow | NO | YES | Same |
| `conversation_engine.py:2406` | `_check_fallback_flow_triggers` vehicle clarification | NO | YES | Same |
| `conversation_engine.py:2445` | `_check_fallback_flow_triggers` location flow | NO | YES | Same |
| `conversation_engine.py:2464` | `_check_fallback_flow_triggers` location clarification | NO | YES | Same |
| `whatsapp.py:446` | `send_thread_text` (manual CRM send) | NO | **NO** | **DEFECT** — missing path_id; generates BLOCKER SecurityEvent on every manual CRM send |
| `whatsapp.py:497` | `_store_outbound_and_send` (interactive, list) | NO | **NO** | **DEFECT** — same |
| `whatsapp.py:728` | `send_to_phone` (system notification) | NO | **NO** | **DEFECT** — missing path_id (SYSTEM_NOTIFICATION should be passed) |
| `buscando_followup.py:84` | Automated "still looking?" follow-up | NO | **NO** | **DEFECT** — missing path_id; generates BLOCKER SecurityEvent on every follow-up send |
| `quote_followup.py:89` | Automated "still interested?" follow-up | NO | **NO** | **DEFECT** — same |

**Impact**: Per CLAUDE.md: "Every `gate.attempt()` call MUST pass `path_id`. Calls with `path_id=None` are blocked at step -1 and emit a `OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE` BLOCKER SecurityEvent." This means:
- Every manual CRM send via `/thread/{id}/send-text` → BLOCKER SecurityEvent in production
- Every automated buscando/quote follow-up → BLOCKER SecurityEvent
- These are currently mitigated by `OUTBOUND_ENABLED=false` (all sends are blocked anyway), but MUST be fixed before any outbound-enabled Wild session

**REQUIRED FIX before Wild-02 with OUTBOUND=true.**

---

## PART 18 — UNANSWERED ALERT DEEP VERIFICATION

### Live execution path

`_run_check()` uses inline SQL queries (not the module-level constants). Both inline queries are correct:

**Per-event SLA path** (lines 132-178): Queries `ai_events` joined with `whatsapp_thread_states`. Correctly filters `needs_human IS NULL OR needs_human = false`. Correct by design (uses ai_events, not whatsapp_messages, so status filter not needed here).

**Thread-level path** (lines 181-206): Inline SQL carries `AND wm.status NOT IN ('blocked', 'failed')` — FINDING fix confirmed present.

### Dead constant hazard

`_FIND_THREAD_UNANSWERED_SQL` (lines 59-80) and `_FIND_UNANSWERED_EVENTS_SQL` (lines 27-46) are defined as module-level constants but neither is used in `_run_check()`. Both inline queries duplicate these constants. If either constant or either inline query is modified independently, they will silently diverge. This is a code maintainability hazard — not a current defect, but a future regression trap.

**Recommendation**: Either eliminate the constants (dead code) or deduplicate by having `_run_check()` reference them.

### Email delivery status

`_send_alert_email()` uses SMTP. `SMTP_PASSWORD` is NOT set in the container. The function checks: `if not s.smtp_host or not s.smtp_user or not s.smtp_password` → if any missing, logs WARNING and returns without sending. **Unanswered alert emails are currently silently suppressed.** See PART 22.

---

## PART 19 — BOOKING FLOW SECURITY AUDIT

All 10 booking flow items PASS:

| Item | Result |
|------|--------|
| 1. Public endpoint bypass is exact-path-only | PASS — exact string match, no prefix wildcard |
| 2. RSA/OAEP decryption present | PASS — `asym_padding.OAEP(mgf=MGF1(SHA256), algorithm=SHA256)` |
| 3. AES-GCM decryption present | PASS — `AESGCM(aes_key).decrypt(iv, encrypted_flow_data, None)` |
| 4. Base64 text response (not JSON) | PASS — `base64.b64encode(encrypted).decode("ascii")`, `media_type="text/plain"` |
| 5. Ping returns 200 | PASS — encrypted response inside the Data Exchange POST (per Meta spec) |
| 6. `date_selected`, `prepare_summary`, `confirm_booking` screens exist | PASS — all three handled |
| 7. Active-cycle candidate watermark used | PASS — `_load_focus_candidate()` filters by `current_cycle_started_at` |
| 8. Advisory lock or idempotency guard | PASS — `pg_try_advisory_xact_lock` keyed to SHA-256 of date |
| 9. Atomic persistence | PASS — ThreadRevision + Revision + Lead updates in one `db.commit()` |
| 10. Token consumption after booking | PASS — `state.flow_booking_token = None` before commit; token validated on every request |

The booking flow is secure and correctly implemented.

---

## PART 20 — META / WEBHOOK SECURITY AUDIT

| Item | Status | Detail |
|------|--------|--------|
| `X-Hub-Signature-256` validation code present | YES | `_verify_signature()` uses `hmac.compare_digest()` |
| Fail-closed when App Secret set and signature fails | YES | Returns HTTP 403 |
| Dev-mode bypass when `WHATSAPP_APP_SECRET=""` | YES — **bypass fires** | `if not secret: return True` — logged at INFO level |
| `WHATSAPP_APP_SECRET` set in running container | **NO** | `WHATSAPP_APP_SECRET present: False` |
| Bypass logged at WARNING/ERROR level | **NO** | Logged at INFO only — may not surface in alert dashboards |

**OPERATIONAL RISK: HIGH** if outbound is ever enabled before this is remediated.

With no App Secret set, any attacker who discovers the webhook URL can POST arbitrary payloads. The inbound handler will accept them, create contacts/threads/messages, and dispatch them to CE. Attack vectors include: fake conversation injection, rate limit exhaustion, data corruption. Currently mitigated only by `OUTBOUND_ENABLED=false` and the fact that the webhook URL isn't publicly advertised.

**REQUIRED: Set `WHATSAPP_APP_SECRET` before any outbound-enabled session.**

---

## PART 21 — N8N TRANSPORT AUDIT

| Item | Finding |
|------|---------|
| `CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED` | `false` in settings.py default; NOT set in container → defaults to `false` ✓ |
| Live path | Meta → `POST /integrations/whatsapp/webhook` → n8n (`http://n8n:5678/webhook/ridecheck-inbound`) → `POST /api/conversation/handle` → CE |
| n8n workflow `active` in JSON exports | Both workflow JSON files show `"active": True` |
| n8n DB activation state | Cannot be confirmed from code alone. Per MEMORY.md: "n8n INACTIVE; live rerun pending owner n8n activation." The JSON exports are backups; runtime DB state requires owner access. |
| n8n provides | Audio transcription, 20-second debounce, image description, lead find/create, context aggregation |
| CE direct dispatch | Correctly disabled (`CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED=false`). Never enable this without explicit CLAUDE.md update. |

The n8n transport path is correctly configured in code. Activation state requires owner verification via n8n admin UI.

---

## PART 22 — EMAIL PROVIDER AUDIT

### Unanswered alerts

| Item | Finding |
|------|---------|
| Email provider | SMTP (`smtplib.SMTP` in `unanswered_alert.py`) |
| `SMTP_HOST` | `smtp.gmail.com` (set) |
| `SMTP_USER` | `ridecheckassistance@gmail.com` (set) |
| `SMTP_PASSWORD` | **NOT SET** (`SMTP_PASSWORD present: False`) |
| Effect of missing password | `_send_alert_email()` logs WARNING and returns without sending. All unanswered alert emails are silently suppressed. |
| `RESEND_API_KEY` | **Present** in container — but not wired into `unanswered_alert.py`. The module uses SMTP exclusively. |

### Booking confirmations

`booking_flow_service.py` does not send a confirmation email. The booking flow returns a WhatsApp Flow SUCCESS screen. Any eventual WhatsApp confirmation would be emitted by CE through the outbound gate when outbound is enabled.

### Errata on prior closeout document

The WILD-01 closeout document (`2026-08-31_RIDECHECK_CRM_M21.6-WILD-01-REMEDIATION_CLOSEOUT_FORENSIC-FIX.md`) stated:

> "Email path is Resend API (`RESEND_API_KEY` present in container). The SMTP config in settings is dead code. No owner action required for email."

**This is incorrect.** The email path is SMTP, not Resend. `RESEND_API_KEY` is present but not wired into any email-sending code path. The SMTP config is the live path. `SMTP_PASSWORD` is not set, so emails are silently suppressed. Owner action IS required for email to function. See PART 25 for full errata.

### Required action

Either:
- Set `SMTP_PASSWORD` in the container environment, OR
- Migrate `unanswered_alert.py` to use the Resend API (using the present `RESEND_API_KEY`)

---

## PART 23 — DASHBOARD OBSERVABILITY AUDIT

### API layer (confirmed correct)

`GET /api/ops/messages` returns all 7 required fields:
- `text` (full, untruncated)
- `wa_id` (unmasked)
- `wa_id_masked`
- `blocked_reason`
- `latency_ms` (via outerjoin with AiEvent)
- `wa_message_id`
- `path_id`
- `status`

### UI layer (gaps)

The frontend `fetchMessages()` JS in `control_view.py` does NOT render `blocked_reason` or `latency_ms` in the message detail expand panel. The API delivers them correctly; the JavaScript detail grid has no `_detailItem('Bloqueado', r.blocked_reason)` or `_detailItem('Latencia', r.latency_ms)` entries.

**Operator impact**: During Wild-02, operators cannot see why a specific message was blocked or how long CE took to respond per-message without querying the API directly or reading logs.

### Runtime parity check caveat

The `dashboard_js_fix` parity check (from the runtime parity agent) was flagged as FAIL due to a `'' not in src[:5000]` predicate. This is a **false-negative in the audit script**: the `''` pattern matches legitimate JavaScript empty-string literals (`.split('?')[0]`, `|| ''` fallbacks, etc.) present throughout the minified JS at positions 4408, 29291, 30832. The actual UX5 fix (`data-detail` attribute on the click handler at `control_view.py:1204`) is confirmed present. The audit script predicate needs to be updated, not the code.

---

## PART 24 — RUNTIME PARITY VERIFICATION

Container: `ridecheck-crm-backend:m21.6-wild01-820f4d6`
Database: `crm_test`

| Feature | Runtime status |
|---------|---------------|
| OUTBOUND_ENABLED | `false` — confirmed via `/api/ops/summary` |
| Alembic version | `20260831_wild01_dedup_causal_inbound` — correct |
| `causal_inbound_wa_message_id` column | PRESENT in `whatsapp_outbound_dedup` |
| FINDING-01 fix: `not focus_after.zone_group` | PRESENT in `conversation_engine.py` |
| FINDING-02 fix: `if focus_after:` + `anio is None` on elif | PRESENT |
| FINDING-03 fix: causal ID in `outbound_safety_gate.py` | PRESENT |
| Unanswered alert fix: `status NOT IN ('blocked', 'failed')` | PRESENT in live inline query |
| Ops dashboard: `blocked_reason`, `latency_ms` | PRESENT in API endpoint |
| `WHATSAPP_APP_SECRET` | NOT SET |
| `SMTP_PASSWORD` | NOT SET |

---

## PART 25 — CLOSEOUT DOCUMENT ERRATA

File: `2026-08-31_RIDECHECK_CRM_M21.6-WILD-01-REMEDIATION_CLOSEOUT_FORENSIC-FIX.md`

| Section | Claim in closeout doc | Actual finding | Severity |
|---------|-----------------------|----------------|----------|
| EMAIL STATUS | "Email path is Resend API (RESEND_API_KEY present in container). The SMTP config in settings is dead code. No owner action required for email." | Email path is SMTP only. `RESEND_API_KEY` is present but NOT wired into any code. `SMTP_PASSWORD` is not set → emails silently suppressed. Owner action IS required. | FACTUAL ERROR |
| DASHBOARD JS FIX | Correctly states the `data-detail` fix was inherited from prior image | Dashboard UI does not render `blocked_reason` or `latency_ms` despite API returning them | OMISSION (not an error in the closeout, but an unresolved gap) |
| OUTBOUND: OFF | Correctly stated | Confirmed | OK |
| PRODUCTION DB TOUCHED: NO | Correctly stated | Confirmed | OK |

---

## PART 26 — GO / NO-GO DETERMINATION FOR WILD-02

### Verdict: CONDITIONAL NO-GO

All three WILD-01 bugs are correctly fixed and verified in the runtime image. The fixes are sound. However, the audit has identified three REQUIRED items and two RECOMMENDED items that must be addressed before Wild-02 can be authorized.

---

### REQUIRED BEFORE WILD-02

**REQUIRED-1: Fix RISK-03 — new candidate must not inherit stale `state.home_zone_*`**

Why: `state.home_zone_group='CABA' / home_zone_detail='Palermo'` was left in the database by Wild-01. If the owner starts Wild-02 with a vehicle message that doesn't also mention the zone in the same turn, CE will create a new candidate with `zone_group='CABA'`, `zone_detail='Palermo'` — the F5.1 zone gate will think zone is known and won't ask, and the customer will receive a Palermo quote in the first reply. This is the same structural failure as FINDING-01, just triggered at candidate creation instead of post-AI sync.

Fix: In `_create_candidate_from_catalog()`, do not inherit `state.home_zone_*`. Create with `zone_group=None, zone_detail=None` and rely on LR-3 (if zone is in the same turn) or F5.1 required-next-question (if zone is not) to fill it in the correct turn.

**REQUIRED-2: Fix `path_id` missing from 5 `gate.attempt()` call sites**

Why: `whatsapp.py:446`, `whatsapp.py:497`, `whatsapp.py:728`, `buscando_followup.py:84`, `quote_followup.py:89` all call `gate.attempt()` without `path_id`. Per CLAUDE.md, this generates a BLOCKER SecurityEvent on every call. These are currently suppressed by `OUTBOUND_ENABLED=false` (sends are blocked before path_id check fires), but once outbound is re-enabled for Wild-02, every manual CRM send and automated follow-up will generate BLOCKER events. This will obscure real security events and pollute the SecurityEvent audit trail.

Fix: Add the appropriate `OutboundPathId` value to each call site.

**REQUIRED-3: Set `WHATSAPP_APP_SECRET` before any outbound-enabled session**

Why: Without App Secret, any party who discovers the webhook URL can POST arbitrary inbound payloads. This is acceptable while OUTBOUND=false (worst case: fake messages processed by CE but no replies sent), but unacceptable once OUTBOUND=true. Wild-02 with outbound enabled requires App Secret to be set.

Fix: Set `WHATSAPP_APP_SECRET` in the container environment. Do NOT run any outbound-enabled Wild session without it.

---

### RECOMMENDED BEFORE WILD-02

**RECOMMENDED-A: Fix `state.customer_name` unconditional AI overwrite (RISK-01)**

Add `not state.customer_name` guard to `_apply_extracted()` to match the `lead.nombre` pattern.

**RECOMMENDED-B: Fix email — either set `SMTP_PASSWORD` or wire Resend**

Without this, unanswered alerts are silently dropped. During Wild-02, if CE fails to respond and the thread becomes unanswered, the operator will not be notified by email. Monitoring must be done manually via `/control` dashboard.

---

### NOT BLOCKING WILD-02

- RISK-02/04 (scheduling field overwrites): Only affects SCHEDULING stage, which typically doesn't arise until late in a successful Wild session
- RISK-05 (`_apply_candidate` without ID): Pre-existing behavior, not newly introduced
- CL-01 through CL-07: Candidate lifecycle risks, real but lower probability in a controlled Wild session
- Dashboard UI gaps (blocked_reason, latency_ms not displayed): Operators can query API directly
- Dead constant in `unanswered_alert.py`: Maintainability issue, not a behavioral defect

---

### Wild-02 authorization checklist

| Item | Required? | Status |
|------|-----------|--------|
| RISK-03 fix (candidate zone inheritance) | REQUIRED | OPEN |
| path_id at 5 gate call sites | REQUIRED | OPEN |
| `WHATSAPP_APP_SECRET` set | REQUIRED (before outbound) | OPEN |
| RISK-01 fix (customer_name) | RECOMMENDED | OPEN |
| Email (SMTP_PASSWORD or Resend) | RECOMMENDED | OPEN |
| Owner confirms readiness | REQUIRED | PENDING |
| n8n workflow re-activated | REQUIRED (for outbound path) | PENDING |
| Cycle reset performed before Wild-02 to clear Wild-01 state | REQUIRED | PENDING |

---

## SAFETY COMPLIANCE CONFIRMATION

NO CODE CHANGES MADE: YES
OUTBOUND: OFF
PRODUCTION DB TOUCHED: NO
STOP.
