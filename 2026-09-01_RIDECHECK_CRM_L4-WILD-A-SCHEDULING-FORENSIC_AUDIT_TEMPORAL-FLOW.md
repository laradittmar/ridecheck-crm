PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L4-WILD-A-SCHEDULING-FORENSIC

# L4 Wild A — Scheduling + Booking Flow Forensic Audit

Date: 2026-09-01
Author: Claude Opus 5 (1M context) under owner direction
Scope: first TRUE clean-slate Wild (post L4.2 tester zero-state)
Governance: LAUNCH_TRUTH_ROADMAP.md · PROJECT_CONTEXT.md · CLAUDE.md · L4 / L4.1 / L4.1A / L4.1B / L4.2 closeouts
Standing constraints honoured: no code changes, no DB correction, no manual Flow send, no continuation of the WhatsApp conversation, production untouched.

---

## 0. Evidence preservation

| Artifact | Path | Covers |
|---|---|---|
| Pre-Wild backend log | `/opt/ridecheck-crm-forensics/WILD-A-preenable-backend-20260901_151440.log` | pre-enable state |
| Wild A backend log (owner snapshot) | `/opt/ridecheck-crm-forensics/L4-WILD-A_backend_logs_2026-09-01T181700Z.txt` | 18:14:55Z → 18:28Z — **contains all 4 CE turns of Wild A** |
| Post-incident backend log | `/opt/ridecheck-crm-forensics/L4-WILD-A-SCHED_backend_logs_2026-09-01T190106Z.txt` | 18:55Z → 19:01Z (post-restart container) |
| DB state export (this audit) | `/opt/ridecheck-crm-forensics/L4-WILD-A_db_state_export_2026-09-01T190626Z.txt` | Contact/Thread/Lead/State/Candidate/messages/dedup/locks/ai_events/revisions/security_events |
| Tester zero-state export | `/opt/ridecheck-crm-forensics/L4.2_tester_forensic_export_2026-09-01.txt` | pre-deletion baseline |
| n8n event log | container `ridecheck-crm-n8n-1:/home/node/.n8n/n8nEventLog-1.log` | executions 1444–1450 |

Runtime at time of audit: OUTBOUND **OFF**, n8n **running**, image `ridecheck-crm-backend:l4.1-meta-error-01025b7`, DB `crm_test`.
Source/image parity verified byte-for-byte for `conversation_engine.py`, `schedule.py`, `booking_flow_service.py` (running image == worktree HEAD 76815a8).

Note: the container was recreated by the owner at 18:55Z to return OUTBOUND to OFF. The Wild A window log had already been persisted to disk at 18:28Z, so no forensic data was lost. Conversation state was not modified by this audit.

---

## 1. PART 1 — Clean-slate Wild invariants already proven

All values read from `crm_test`, not from the bot text.

| Invariant | Evidence | Result |
|---|---|---|
| Contact created exactly once | `whatsapp_contacts` count for `5491153368330` = 1 (id 2043, 18:19:34Z) | **PASS** |
| Thread created exactly once | `whatsapp_threads` for contact 2043 = 1 (id 2036) | **PASS** |
| Lead created exactly once | `leads` where telefono=tester = 1 (id 122, `inbound_channel=WHATSAPP`) | **PASS** |
| No prior Revision leaked | `revisions where lead_id=122` = 0; `thread_revisions where thread_id=2036` = 0 | **PASS** |
| No prior candidate leaked | `whatsapp_thread_candidates where thread_id=2036` = 1 (id 130) | **PASS** |
| Vehicle = Peugeot 2008 | candidate 130 `marca=Peugeot modelo=2008` | **PASS** |
| Year = 2014 | candidate 130 `anio=2014` | **PASS** |
| Inspection location = Berazategui | candidate 130 `zone_group=Sur zone_detail=Berazategui` | **PASS** |
| Customer origin (Tigre) did NOT overwrite inspection location | `state.home_zone_group=Sur`, `home_zone_detail=NULL`; candidate remains Berazategui/Sur; no Norte/Tigre anywhere in thread state | **PASS** |
| Vehicle category | candidate 130 `tipo_vehiculo=SUV_4X4_DEPORTIVO`; catalog lookup confirms `SUV_4X4_DEPORTIVO`, confidence high | **PASS** |
| Pricing 150000 + 90000 = 240000 | `PricingService.quote(SUV_4X4_DEPORTIVO, Sur, Berazategui)` re-executed against crm_test → base=150000, viaticos=90000, total=240000; matches message 6049 | **PASS** |
| FAQ — service / report / presence | message 6047 answers all three correctly | **PASS** |
| FAQ — payment, debit rejected | message 6047: "aceptamos transferencia bancaria, Mercado Pago y efectivo, pero no se puede pagar con débito" | **PASS** |
| FAQ — business hours | message 6052: "lunes a viernes de 9 a 18 hs" — **contradicts** `ScheduleService._business_hours` (Mon 13:00–18:00, Tue 09:30–14:00, Thu 09:00–14:00) | **FAIL — SCHED-E** |
| Quote acceptance recognised | `lead.flag=ACEPTADO`; `state.last_stage=SCHEDULING` | **PASS** |
| Scheduling stage entered correctly | `state.last_stage=SCHEDULING`, `last_intent=PREPURCHASE_INSPECTION` | **PASS** |

**Wild A is not wholly discarded.** Everything up to and including quote acceptance is proven correct on a true zero-state thread. The failure is confined to the scheduling turn and to Booking-Flow orchestration.

---

## 2. PART 2 — Exact scheduling turn, field by field

Inbound message id **6053**, 2026-09-01 18:23:35.639Z.

| Field | Value |
|---|---|
| Raw WhatsApp content | `Mñ 15hs? O nose jueves que tenes` |
| WAMID | `wamid.HBgNNTQ5MTE1MzM2ODMzMBUCABIYFDNBRkVFODEyODdERDY5MEYwQzA0AA==` |
| Message type / transcription | `text` — no audio, no transcription involved |
| Normalized text (CE `combined`) | `mñ 15hs? o nose jueves que tenes` |
| Burst assembly | single-message burst (n8n execution 1450; no other inbound within debounce) |
| CE input | `POST /api/conversation/handle` at 18:23:35.775 local-log / reply 18:23:56 |
| AI interpretation | **not reached** — `_is_pure_scheduling_rafaga(...)` returned `True`, so the deterministic scheduling branch consumed the turn before any AI call |
| Deterministic temporal extraction | `_parse_scheduling_text(["Mñ 15hs? O nose jueves que tenes"], date(2026,9,1))` → **`('2026-09-03', '15:00')`** (re-executed in the running image; exact runtime reproduction) |
| `preferred_day` before | `NULL` |
| `preferred_day` after | `NULL` (cleared by the rejection branch) |
| `preferred_time` before | `NULL` |
| `preferred_time` after | `NULL` (cleared by the rejection branch) |
| Scheduling request passed to SchedulingService | `ScheduleCheckIn(address="Berazategui, Sur, Buenos Aires, Argentina", preferred_day=2026-09-03, preferred_time=15:00, zone_group="Sur", zone_detail="Berazategui")` |
| Date/time searched | **Thursday 2026-09-03 15:00** |
| Availability returned | `valid=False`, reasons=`["El turno no entra en el horario operativo del dia considerando revision y traslado"]`, conflicts=`[]`, full-day Sur slots = `["13:00"]` |
| State written by rejection branch | `active_requested_date=2026-09-03`, `last_requested_time=15:00`, `last_offered_slots=["13:00"]`, `last_visible_slots=["13:00"]` |
| Final composed reply (msg 6054, 18:23:56Z) | `Para jueves 03/09 a las 15:00 no tenemos disponibilidad. Horarios disponibles: 13:00. ¿Alguno te viene bien?` |
| Outbound path / status | `CE_TEXT`, status `read` (delivered to handset) |

Persisted ThreadState after the turn matches this trace exactly (`active_requested_date=2026-09-03`, `last_requested_time=15:00`, `last_offered_slots=["13:00"]`).

---

## 3. PART 3 — Temporal semantics

Reference instant: **2026-09-01, Tuesday, ART**. Therefore `mañana` = **Wednesday 2026-09-02**.

Actual parser behaviour, measured in the running image (`_parse_scheduling_text`, `today=2026-09-01`):

| Input | Parsed |
|---|---|
| `mñ 15hs` | `('2026-09-02', '15:00')` ✅ |
| `mañana 15hs` | `('2026-09-02', '15:00')` ✅ |
| `manana 15hs` | `('2026-09-02', '15:00')` ✅ |
| `mañ 15hs` | `(None, '15:00')` ❌ not recognised |
| `mn 15hs` | `(None, '15:00')` ❌ not recognised |
| `mñna 15hs` | `(None, '15:00')` ❌ not recognised |
| `jueves 15hs` | `('2026-09-03', '15:00')` ✅ |
| **`mñ 15hs o jueves`** | **`('2026-09-03', '15:00')`** ❌ tomorrow lost |
| `mañana 15hs o el jueves` | `('2026-09-03', '15:00')` ❌ tomorrow lost |

`mñ` **is** supported (`conversation_engine.py:1343`, `re.search(r"\bmñ\b", combined)`). It was not an unknown token.

**RAW TEMPORAL INTENT**
- PRIMARY REQUEST: Wednesday 2026-09-02 15:00
- FALLBACK REQUEST: Thursday 2026-09-03, flexible time

**Was that semantic structure recovered? — NO.**

---

## 4. PART 4 — Alternative / fallback intent

`_parse_scheduling_text` returns a single `tuple[str|None, str|None]` — one day, one time. `WhatsAppThreadState` likewise stores single-valued `preferred_day`, `preferred_time`, `active_requested_date`, `last_requested_time`. **There is no representation anywhere in the scheduling domain for "primary preference + fallback preference".**

Exact mechanism by which Thursday overwrote Wednesday (`conversation_engine.py:1332–1356`):

```python
day_name_found = any(name in combined for name in _SPANISH_DAY_TO_WEEKDAY)   # "jueves" → True
...
elif ("mañana" in combined or "manana" in combined
      or re.search(r"\bmñ\b", combined)) and not day_name_found:             # suppressed
    day_iso = tomorrow
...
if day_iso is None:                                                          # falls through
    for day_name, weekday in _SPANISH_DAY_TO_WEEKDAY.items():
        if day_name in combined:                                             # "jueves" → 2026-09-03
```

The guard `and not day_name_found` exists for a legitimate reason documented in the code: in Spanish, *"viernes por la mañana"* means *Friday morning*, not *tomorrow*. The guard therefore lets any weekday name anywhere in the burst suppress the relative-day branch. In this utterance the relative token was `mñ` — which is **unambiguously "tomorrow"** and can never mean "morning" — and it appeared **first**, in the primary clause. The weekday name from the **second, subordinate** clause won.

A second, independent corruption occurred in the same call: time extraction is "rightmost match wins" over the whole burst, and the only time token (`15hs`) belongs to the **Wednesday** clause. It was transplanted onto the **Thursday** day. The system therefore queried a request the customer never made — *Thursday at 15:00* — and reported unavailability for it.

---

## 5. PART 5 — Wednesday 2026-09-02 15:00, real deterministic availability

Computed independently with the certified `ScheduleService` against the live `crm_test` agenda (read-only re-execution, not inferred from the bot text).

Parameters: `zone_group=Sur`, `zone_detail=Berazategui`, `SERVICE_MINUTES=45`, `VIA_REVISION=45`, Wednesday business hours **09:00–18:00**, zero-zone **Norte / Melo y Panamericana**, travel matrix Norte↔Sur = 90, CABA↔Sur = 60, Sur↔Sur = 30.

Occupied that day:

| Source | Time | Zone |
|---|---|---|
| revision #26 | 09:00–09:45 | CABA |
| revision #14 | 10:00–10:45 | CABA |
| revision #27 | 12:30–13:15 | Norte |
| revision #28 | **15:00–15:45** | Norte |

**WED 02/09 15:00: UNAVAILABLE**

**EXACT CONFLICT:** direct service-window overlap with revision #28 (Norte, 15:00–15:45) — `ScheduleCheckOut.conflicts=[revision 28, 2026-09-02T15:00→15:45]`; additionally the travel constraint fails (previous appointment #27 Norte 12:30 + 45 min service + 90 min Norte→Sur travel ⇒ earliest reachable 14:45, and the next-appointment constraint bars the window regardless).

Full-day scan for a Sur inspection on Wednesday 02/09 — **every** 30-minute start is blocked:

| Start | Verdict |
|---|---|
| 09:00 / 09:30 | overlap #26 |
| 10:00 / 10:30 | overlap #14 |
| 11:00 / 11:30 | prev #14 (CABA 10:00) + 45 + 60 min travel |
| 12:00 / 12:30 / 13:00 | overlap #27 |
| 13:30 / 14:00 | prev #27 (Norte 12:30) + 45 + 90 min travel |
| 14:30 / 15:00 / 15:30 | overlap #28 |
| 16:00 / 16:30 / 17:00 | prev #28 (Norte 15:00) + 45 + 90 min travel |

`list_slots(Wed 02/09, Sur)` → **`[]`**. There is no Berazategui-compatible slot on Wednesday at all.

---

## 6. PART 6 — Thursday 2026-09-03 availability

Thursday business hours are **09:00–14:00** (`_business_hours`, weekday 3). Occupied: #29 CABA 09:00–09:45, #16 Sur 10:00–10:45, #30 Sur 11:30–12:15.

**THURSDAY VALID SLOTS (zone Sur): `13:00`**

**13:00 ONLY: YES.** 13:30 would end at 14:15, past the 14:00 hard end; 12:30 overlaps #30; earlier starts are blocked by overlap or travel. The bot's slot list was **correct**.

The `check()` for 15:00 returned the reason *"El turno no entra en el horario operativo del dia"* — i.e. **15:00 does not exist on a Thursday**, which is a categorically different fact from "that slot is taken". The customer was never told this.

**SCHED-C (incorrect availability calculation) is NOT a defect. The scheduling mathematics were correct in both directions.**

---

## 7. PART 7 — Expected customer response

Given the real deterministic availability (Wednesday: no Sur slot at all; Thursday: 13:00 only), the correct commercial response had to (a) name the customer's **primary** request and its outcome, and (b) then present the fallback:

> "Para mañana miércoles 02/09 no tengo disponibilidad en Berazategui. El jueves 03/09 trabajamos hasta las 14, así que las 15 no entra; sí tengo 13:00. ¿Te sirve?"

Core rule, currently unimplemented: **the PRIMARY explicit preference must be evaluated and reported before the FALLBACK preference. The customer must never have to infer that a day they explicitly asked for was silently discarded.**

Note that even a correct parse would not by itself have produced this reply: with Wednesday parsed as primary, the rejection branch would have found `all_slots=[]` and answered *"Para mañana miércoles 02/09 no hay horarios disponibles… ¿Tenés otro día preferido?"* — discarding the Thursday fallback the customer had already volunteered. **Parsing and orchestration are two separate defects.**

---

## 8. PART 8 — Booking Flow trigger audit

**Canonical runtime trigger (found, not inferred).** The only Booking-Flow dispatch contract in code is `ConversationEngine._try_schedule_and_flow` (`conversation_engine.py:4340–4432`):

> a concrete `(day, time)` request is parsed **and** `ScheduleService.check()` returns `valid=True` → store `preferred_day`/`preferred_time`, mint `flow_booking_token`, send the Flow button (`_send_flow_button`, path `CE_FLOW`).

`DOMAIN_MODEL.md §5` agrees: `QUOTED → (acceptance) → SCHEDULING (date/time coordination) → (booking Flow dispatched) → [flow_booking_token set] → (customer submits Flow) → BOOKED`.

So: acceptance alone does not make the Flow eligible; **an available, agreed concrete slot does.**

Trace for Wild A:

- **BOOKING FLOW ELIGIBLE: NO**
- **FIRST MOMENT IT WOULD HAVE BECOME ELIGIBLE:** the first `check(valid=True)` — i.e. the customer accepting `13:00` on Thursday 03/09 (or any other available slot). The conversation was stopped before that turn.
- **BOOKING FLOW SEND ATTEMPT: NO**
- **WHY NOT:** `sched_out.valid == False` for `2026-09-03 15:00` (outside Thursday operating hours) → control took the `else` branch (text alternatives). `state.flow_booking_token` is `NULL`; no `CE_FLOW` record exists in the ledger; zero `flow_button_sent` actions.

**Flow absence during Wild A was therefore correct behaviour under the implemented trigger contract — but it masks a separate, real defect:**

The published Flow **28104222025943520 ("RideCheck Booking")** is **not reachable by any code path**:

| Check | Result |
|---|---|
| `settings.booking_flow_id` (= `WHATSAPP_BOOKING_FLOW_ID`, default `28104222025943520`) | defined in `settings.py:30,96` — **zero references in any send path** |
| Flow ID actually sent by CE | `settings.whatsapp_flow_id` = **`1644218879979041`** (legacy data-collection Flow), at `conversation_engine.py:4413` |
| `make_booking_token()` (`booking_flow_service.py:174`) | **never called** by any sender |
| `OutboundPathId.BOOKING_FLOW` | registered in `outbound_path_registry.py:80` and listed by the ops dashboard — **used at zero `gate.attempt()` call sites** |
| CE token format | `f"{thread_id}-{int(time)}"` (2 parts) vs `make_booking_token` `f"{thread_id}-{ts}-{nonce}"` (3 parts) — `parse_booking_token` accepts both, so the data-exchange endpoint is not the blocker |

The M21.3-C-D closeout is consistent with this: it explicitly scoped *"the backend handles the Data Exchange… without sending"*, and recorded "Flow NOT published / Endpoint NOT connected / no live send". The owner has since **published** the Flow — but publication does not wire a sender. **No send trigger for the new Booking Flow has ever been implemented.**

---

## 9. PART 9 — Text scheduling vs Booking Flow

Two complete, competing scheduling experiences exist in the codebase:

**A. Conversational text scheduling (CE)** — `_parse_scheduling_text` → `_try_schedule_and_flow` / `_handle_day_only_request` / `_handle_period_request` / `_select_slot_from_offered`: day/time parsing, slot offering, period filtering, ordinal selection ("el último"), re-confirmation.

**B. Booking Flow (Meta Flow 28104222025943520)** — `BookingFlowService._available_dates()` (14-day horizon, days with slots > 0), `_slots_for_date()`, `handle_date_selected()`, APPOINTMENT screen with server-driven date and time lists, then SUMMARY → `handle_confirm_booking()` atomic booking.

Both implement date selection **and** slot selection against the same `ScheduleService`.

- **AUTHORITATIVE SCHEDULING UX (intent, per M21.3-C-D + the published Flow's own screen contract): BOOKING_FLOW**
- **CURRENT RUNTIME BEHAVIOR: TEXT** — CE's deterministic scheduling branch intercepts every scheduling turn and negotiates day/time in chat; the legacy Flow `1644218879979041` is sent afterwards only to collect customer data
- **MATCH: NO**

`DOMAIN_MODEL.md §5` still describes the text-then-Flow-for-data sequence, so the architecture documents and the M21.3-C-D deliverable disagree. Per `PROJECT_CONTEXT.md` this is reported as an **architecture/orchestration defect requiring an owner decision** — the documents are not silently amended to match either implementation.

---

## 10. PART 10 — Booking Flow send prerequisites

| Prerequisite | State during Wild A | Verdict |
|---|---|---|
| Accepted quote | `lead.flag=ACEPTADO`, stage SCHEDULING | **PASS** |
| Active candidate | candidate 130 `current_focus` | **PASS** |
| Zone | `Sur` / `Berazategui` on candidate; `state.home_zone_group=Sur` | **PASS** |
| Revision / ThreadRevision | none exist — by design, created only at Flow submission (`DOMAIN_MODEL §2`) | **NOT_REQUIRED_YET** |
| Approval token | `appointment_approval_token` is a post-booking concern | **NOT_REQUIRED_YET** |
| Current cycle watermark | `cycle_reset_pending=false`, `last_processed_inbound_wa_message_id` = msg 6053 WAMID | **PASS** |
| Customer data (name/email/address) | not collected — collected *inside* the Flow | **NOT_REQUIRED_YET** |
| Seller data | not collected — Flow-internal | **NOT_REQUIRED_YET** |
| Address (exact) | zone-level only; exact address is a Flow field | **NOT_REQUIRED_YET** |
| Preferred day/time | **FAIL** — no valid slot was ever agreed; `preferred_day`/`preferred_time` are NULL | **FAIL (blocking, and correctly blocking)** |
| Meta Flow ID | `WHATSAPP_FLOW_ID=1644218879979041` set; `WHATSAPP_BOOKING_FLOW_ID` unset in runtime env (falls back to the `28104222025943520` default) but **never read by a sender** | **FAIL (wiring)** |
| Phone ID | `WHATSAPP_PHONE_NUMBER_ID=1196075770246218` (corrected in L4.1B) | **PASS** |
| Data-exchange health | endpoint live; `POST /integrations/whatsapp/flows/booking/data-exchange` → 200 in the Wild A log | **PASS** |
| `path_id` | `CE_FLOW` wired for the legacy Flow; `BOOKING_FLOW` wired at zero call sites | **FAIL (wiring)** |
| Outbound gate | ON during Wild A (tester-only allowlist); OFF now | **PASS (at the time)** |

**Conclusion:** Flow absence in this specific conversation is **correct** — the blocking prerequisite (an available agreed slot) was genuinely unmet. Independently of this conversation, the new Booking Flow has **no trigger, no sender and no path_id**, which is a genuine orchestration gap (FLOW-A).

---

## 11. PART 11 — Post-acceptance next action

Canonical sequence per `DOMAIN_MODEL.md §5` (owner-authoritative, WILD-04R confirmed):

```
QUOTED --(acceptance detected)--> SCHEDULING (date/time coordination) --(booking Flow dispatched)--> BOOKED
```

- **POST-ACCEPTANCE EXPECTED NEXT ACTION:** enter SCHEDULING and coordinate day/time; customer/seller/address data is collected by the booking Flow afterwards, not in chat.
- **ACTUAL NEXT ACTION:** message 6052 — "¿qué día y horario te viene mejor?" plus business hours; stage set to SCHEDULING.
- **MATCH: YES** against the current canonical document.

No third orchestration defect is confirmed here. The caveat is conditional on FLOW-B: if the published Booking Flow becomes the authoritative booking UX, the canonical post-acceptance action changes to *dispatch the Booking Flow immediately after acceptance* (the Flow itself owns date + slot + data), and `DOMAIN_MODEL §5` must be revised by owner decision — not silently.

---

## 12. PART 12 — n8n / burst check

n8n workflow executions in the Wild A window (from `n8nEventLog-1.log`, all `workflow.success`, none failed):

| Execution | Started (ART) | Inbound |
|---|---|---|
| 1444 | 15:19:34 | msg 6044 |
| 1445 | 15:19:44 | msg 6045 |
| 1446 | 15:19:50 | msg 6046 |
| 1447 | 15:20:50 | msg 6048 |
| 1448 | 15:21:34 | msg 6050 |
| 1449 | 15:21:47 | msg 6051 |
| 1450 | 15:23:35 | msg 6053 (scheduling turn) |

- **BURST COUNT: 4** (bursts 6044–6046, 6048, 6050–6051, 6053)
- **CE INVOCATIONS: 4** (`POST /api/conversation/handle` at 18:20:18, 18:21:16, 18:22:11, 18:23:56 — the 7 executions collapse correctly via the 20-second debounce)
- **DUPLICATE PROCESSING: NO** — 4 outbound records, 4 dedup rows with 4 distinct `causal_inbound_wa_message_id` values, 1 recipient lock, 0 SecurityEvents for thread 2036

The scheduling message was processed exactly once.

---

## 13. PART 13 — Gate classification (independent findings)

| ID | Severity | Gate | Description | Evidence |
|---|---|---|---|---|
| **SCHED-A** | HIGH | L4 | Primary relative-day preference (`mñ` = tomorrow) discarded whenever any weekday name appears anywhere in the burst. The guard `and not day_name_found` (`conversation_engine.py:1340–1344`) exists to protect *"viernes por la mañana"*, but also suppresses the unambiguous abbreviation `mñ`, which can never mean "morning". | Deterministic repro in running image: `mñ 15hs` → 2026-09-02; `mñ 15hs o jueves` → 2026-09-03 |
| **SCHED-B** | HIGH | L4 | The scheduling domain cannot represent PRIMARY + FALLBACK. `_parse_scheduling_text` returns one `(day, time)`; `WhatsAppThreadState` stores one day and one time. The fallback clause's day replaced the primary clause's day, **and** the primary clause's time (`15hs`) was transplanted onto it — producing a query the customer never made (Thursday 15:00). | `state.active_requested_date=2026-09-03`, `last_requested_time=15:00`; msg 6054 |
| **SCHED-C** | — | — | **NOT A DEFECT.** Availability mathematics correct for both days (independently recomputed). | §5, §6 |
| **SCHED-D** | HIGH | L4 | Response never mentions the primary requested day, and mis-explains the rejection: "no tenemos disponibilidad" where the true reason is "Thursday closes at 14:00". The rejection template (`conversation_engine.py:4455–4470`) surfaces `sched_out.reasons` only when there are **zero** slots; when any slot exists the real reason is dropped. | msg 6054 vs `check()` reason `"El turno no entra en el horario operativo del dia"` |
| **SCHED-E** | HIGH | L4 | Canonical FAQ hours constant `_FAQ_HOURS_ANSWER` (`conversation_engine.py:767–769`) says "lunes a viernes de 9 a 18 hs y los sábados de 9 a 15 hs". `ScheduleService._business_hours` says Mon 13:00–18:00, Tue 09:30–14:00, Wed 09:00–18:00, Thu **09:00–14:00**, Fri 09:00–18:00, Sat 09:00–15:00. Two authorities for the same business fact; the customer was told hours that do not exist and asked for Thursday 15:00 **as a direct consequence**. | msg 6052 vs `schedule.py:_business_hours` |
| **FLOW-A** | HIGH | L4 | The published Booking Flow `28104222025943520` has no sender: `settings.booking_flow_id` has zero send references, `make_booking_token()` is never called, `OutboundPathId.BOOKING_FLOW` is used at zero gate call sites. CE dispatches the legacy Flow `1644218879979041`. Not triggered in this Wild (correctly), but unreachable in every Wild. | grep of all send paths; `conversation_engine.py:4413` |
| **FLOW-B** | HIGH (architecture) | L4 | Two competing scheduling UXs: CE text negotiation (runtime authority) vs Booking Flow in-Flow date/slot pickers (design authority). Documents disagree (`DOMAIN_MODEL §5` vs M21.3-C-D). Owner decision required. | §9 |
| **POST-ACCEPT-A** | — | — | **NOT A DEFECT** against the current canonical document; conditional on the FLOW-B decision. | §11 |
| **FOR-01** | MEDIUM | L4 (runtime config) | All 4 Wild A outbound ledger rows carry `deployment_id='unknown'` and `correlation_id=NULL`. `GIT_SHA` is injected by no compose file, so `_compute_deployment_id()` falls back to `"unknown"`; CE's `gate.attempt()` calls pass no `correlation_id`. Degrades the CLAUDE.md "CONTAINER-INDEPENDENT TRACEABILITY" invariant: the ledger cannot say which build sent a message. | `whatsapp_messages` rows 6047/6049/6052/6054 |
| **INFRA-OOM-01** | MEDIUM | L4 (infrastructure) | Host global OOM at 18:30:37Z on a 4 GB, zero-swap host killed the Claude Code process and the n8n container (exit 137, `OOMKilled=true`, finished 18:37:03Z). No reboot. Backend survived. Occurred **13 minutes after** the last Wild A message and did **not** influence any scheduling behaviour. Mitigated for now by 4 GB `/swapfile`, persisted in `/etc/fstab`. | `dmesg -T`, `docker inspect`, §15 |

---

## 14. PART 14 — Contradictory evidence rule

| Gate | Reopened? | Reasoning |
|---|---|---|
| **L1 — Semantic Authority** | **NO** | L1 governs current-turn evidence vs **stale history**. Here both candidate days originate in the **same current turn**; no historical or AI-derived value overwrote current-turn evidence. Location authority (Berazategui vs Tigre), year, zone and quote authority all behaved exactly as L1 certified. L1 stays FROZEN. |
| **L2 — Transport + Ops** | **NO** | All 4 outbound records carry the correct `CE_TEXT` path_id; transport Meta → backend → n8n → CE worked; every message reached status `read`; no unauthorized-path SecurityEvent. FOR-01 is a **deployment configuration** gap (`GIT_SHA` never injected), not a defect in the L2 code that was certified; L2's exit criterion (path_id at every call site, dashboard reconstruction) is not contradicted. Recorded under L4 with a config remediation. |
| **L3 — Dirty History** | **NO** | Wild A ran on a true zero-state thread with no history at all. No certified dirty-history invariant is contradicted. |
| **L4 — Runtime / Wild** | **YES — owns every finding** | SCHED-A, SCHED-B, SCHED-D, SCHED-E, FLOW-A, FLOW-B, FOR-01, INFRA-OOM-01 are all runtime orchestration / runtime configuration / infrastructure findings surfaced by a controlled Wild. |

**CONTRADICTORY EVIDENCE AGAINST A FROZEN GATE: NO.**

---

## 15. INFRA-OOM-01 — host OOM incident (separate operational finding)

**Facts (owner-supplied, independently confirmed in this session):**

- Server did **not** reboot (postgres container uptime unbroken: "Up 3 months").
- `dmesg -T`: `Out of memory: Killed process 1012096 (MainThread) … global_oom` at Tue Sep 1 15:30:37 2026 ART (18:30:37Z).
- `ridecheck-crm-n8n-1`: `ExitCode=137`, `OOMKilled=true`, finished 18:37:03Z.
- Claude Code process was killed in the same event (tmux session loss).
- Host RAM ≈ 3.9 GB; **no swap** at the time.
- Backend and postgres survived.
- OOM occurred at 18:30:37Z; last Wild A message was 18:23:56Z — **6.7 minutes earlier**, and the entire scheduling turn completed and was persisted before it.
- Mitigation applied by owner: 4 GB `/swapfile` created, activated (`swapon --show` confirms 4 GB, prio -2), and persisted (`/etc/fstab` line 4).
- OUTBOUND returned to OFF (`OUTBOUND_ENABLED=false` in the running backend), n8n restarted **after** outbound was confirmed OFF (backend 18:55:00Z, n8n 18:55:26Z).

**Classification**

| Question | Answer |
|---|---|
| Severity | **MEDIUM** — availability/observability risk, not a data-integrity or safety-invariant risk. No outbound message, ledger row, dedup row or SecurityEvent was lost or duplicated; the OOM struck the transport tier while the conversation was already idle. |
| Launch relevance | **YES.** n8n is the sole inbound transport. If it is OOM-killed during real customer traffic, inbound messages stop being processed **silently** — Meta retries expire and customers are met with silence. This is a launch-blocking availability class, tracked at L4/L5. |
| Mitigated by 4 GB swap alone? | **NO — necessary but not sufficient.** Swap converts a hard kill into degradation under pressure, which is a real improvement, but the OOM was triggered by an unbounded workload (heavy agent/test process) on an unbounded container set. Nothing yet prevents recurrence, and nothing yet **alerts** if n8n dies. |
| Further runtime controls recommended before public launch? | **YES** — (a) `restart: unless-stopped` on the n8n service so an OOM kill self-heals; (b) an n8n liveness check surfaced on `/control` plus an alert (the Resend channel from L2.1 is already proven) when the inbound transport is down; (c) inbound-gap detection: alert if a webhook is received but no CE invocation follows within N seconds. |
| Memory limits on Claude/test workloads, n8n, containers? | **YES.** Set explicit `mem_limit` per container (n8n ≈ 1 GB, backend ≈ 1 GB, postgres ≈ 768 MB) so a runaway container is killed in isolation instead of triggering a **global** OOM that selects an unrelated victim. Cap heavy local workloads (full pytest runs, agent processes) with `systemd-run --scope -p MemoryMax=` or an equivalent cgroup limit. |
| Minimum free-memory / swap preflight before heavy suites? | **YES.** Add a preflight assertion to the Wild/test runbook: swap active ≥ 2 GB **and** available memory ≥ 1 GB before starting a full regression suite or a Wild session; abort with a clear message otherwise. Cheap, and it directly prevents the class of incident that cost this session. |

**The scheduling defect is NOT attributed to the OOM.** SCHED-A/B/D/E are deterministically reproducible in a healthy container with no memory pressure (§3, §4). No frozen gate is reopened on account of this infrastructure incident.

---

## 16. PART 16 — Wild A status

| Portion | Result |
|---|---|
| NEW CUSTOMER CREATION | **PASS** |
| PERSISTENT CONTACT / THREAD / LEAD | **PASS** |
| VEHICLE (Peugeot 2008) + YEAR (2014) + CATEGORY | **PASS** |
| FAQ (service, report, presence, payment/debit) | **PASS** |
| FAQ (business hours) | **FAIL — SCHED-E** |
| LOCATION AUTHORITY (Berazategui kept; Tigre not applied) | **PASS** |
| PRICING (150 000 + 90 000 = 240 000) | **PASS** |
| ACCEPTANCE | **PASS** |
| SCHEDULING | **FAIL** |
| BOOKING FLOW | **NOT_YET_ELIGIBLE** (correctly not sent) — separately **unreachable**, FLOW-A |
| BURST / TRANSPORT / DEDUP / PATH ATTRIBUTION | **PASS** |

**CLEAN-WILD COUNTER REMAINS 0/3.** Wild A is PAUSED, not discarded: the qualifying, FAQ, location-authority, pricing and acceptance portions are proven on a true clean slate and do not need to be re-proven unless later evidence contradicts them.

---

## 17. Recommended finite remediation — L4.3-SCHEDULING-SEMANTICS

Strictly scoped; no L1/L2/L3 reopening; owner decision required before any FLOW work.

**Phase A — scheduling semantics (SCHED-A, SCHED-B, SCHED-D)**
1. Narrow the relative-day guard: `day_name_found` may suppress only the ambiguous forms `mañana`/`manana` (which can mean "morning"). The unambiguous abbreviation `mñ` must always resolve to *tomorrow*. Optionally recognise `mñn`, `mñna`, `mnñ` as the same token — **not** bare `mn`.
2. Return an **ordered list of temporal requests** from `_parse_scheduling_text` (primary first, in utterance order), each carrying only the time token found **within its own clause**. Never transplant a time across clauses.
3. Evaluate PRIMARY first; on rejection evaluate FALLBACK in the same turn; compose one reply that names the primary day, its outcome and reason, then the fallback options.
4. Add an explicit "outside operating hours" branch to the rejection template so the real reason survives even when alternative slots exist.
5. Test suite `tests/test_l4_3_scheduling_semantics.py`: the exact live burst `"Mñ 15hs? O nose jueves que tenes"` against a frozen `today=2026-09-01`, plus primary/fallback matrices and the `mñ`/`mañana`/weekday interaction table from §3.

**Phase B — single business-hours authority (SCHED-E)**
6. Derive the FAQ hours answer from `ScheduleService._business_hours` (per-weekday, generated), retiring the hard-coded `_FAQ_HOURS_ANSWER` string. One authority for one business fact.

**Phase C — booking UX decision (FLOW-A, FLOW-B) — owner decision first**
7. Owner decides: BOOKING_FLOW authoritative, or TEXT authoritative with the Flow for data only.
8. If BOOKING_FLOW: wire a sender that uses `settings.booking_flow_id` + `make_booking_token()` + `gate.attempt(path_id=OutboundPathId.BOOKING_FLOW.value)`, define the dispatch point (acceptance vs agreed slot), and update `DOMAIN_MODEL §5` **as an owner-approved revision**. If TEXT: formally retire Flow 28104222025943520 and remove `booking_flow_id` from settings so no dead asset remains.

**Phase D — forensic + infrastructure (FOR-01, INFRA-OOM-01)**
9. Inject `GIT_SHA` at image build and into the compose environment so `deployment_id` is real; pass a `correlation_id` from CE through `gate.attempt()`.
10. Per-container `mem_limit`; `restart: unless-stopped` for n8n; n8n liveness + inbound-gap alert via the proven Resend channel; memory/swap preflight assertion in the Wild runbook.

**Wild B must not be authorized until Phase A and Phase B land and the Phase C decision is recorded.**

---

## 18. Constraints honoured

- No code changed.
- No DB row created, updated or deleted (all queries read-only; the state export is a copy).
- No manual Flow send; no WhatsApp message sent; conversation not continued.
- Conversational state left exactly as Wild A ended (`active_requested_date=2026-09-03`, `last_requested_time=15:00`, `last_offered_slots=["13:00"]`, `flow_booking_token=NULL`).
- Production database untouched — all work in `crm_test`.
- OUTBOUND OFF at close.
