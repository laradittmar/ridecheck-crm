PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.3-SCHEDULING-SEMANTICS

# L4.3 — Scheduling Semantics + Booking Flow Wiring

Date: 2026-09-01
Scope: finite remediation of the defects proven by `L4-WILD-A-SCHEDULING-FORENSIC`.
Owner decision implemented: **BOOKING_FLOW is the authoritative scheduling / booking UX.**
Constraints honoured: OUTBOUND OFF, crm_test only, production untouched, Wild A evidence
preserved, no live WhatsApp send, L1/L2/L3 not reopened.

---

## 1. Owner decision as implemented

RideCheck Booking Flow `28104222025943520` (PUBLISHED) is the structured booking and
confirmation UX. Text conversation keeps exactly four responsibilities: interpret
scheduling intent, check deterministic availability, explain unavailable requests, and
walk the customer to one valid option. It never collects booking data and never books.

Contract: [`docs/architecture/BOOKING_UX_CONTRACT.md`](docs/architecture/BOOKING_UX_CONTRACT.md)
(+ `DOMAIN_MODEL §5` note, `PROJECT_CONTEXT` pointer, `CLAUDE.md` invariant).

---

## 2. Phase A — temporal semantics

**Root cause removed.** `_parse_scheduling_text` collapsed a two-branch utterance into a
single `(day, time)` tuple, and its guard `and not day_name_found` let any weekday name
anywhere in the burst suppress an earlier relative-day request.

New deterministic layer in `conversation_engine.py`:

| Function | Role |
|---|---|
| `_scan_day_tokens(combined, today)` | every day mention as `(position, iso)`, in utterance order, deduplicated |
| `_scan_time_tokens(combined)` | every time mention as `(position, "HH:MM")` |
| `_parse_scheduling_requests(texts, today)` | ordered `SchedulingRequest` branches; each time bound to the day it follows |

Rules:

- `mñ` is unambiguous (it can never mean "morning") → always resolves to tomorrow, even
  when a weekday name appears later in the same burst.
- `por/a/de la mañana` is masked as a PERIOD before any day scan, so
  *"viernes por la mañana"* stays Friday-morning.
- bare `mañana`/`manana` keeps the legacy ambiguity rule (weekday name present → weekday wins).
- a time token belongs to the day token that most recently precedes it; the first day also
  owns times spoken before it. **No time is ever transplanted across branches.**
- fewer than two distinct days → the legacy single-tuple parse is returned verbatim, so
  every previously certified single-branch behaviour is unchanged (proved by test).

Ordered evaluation (`_handle_ordered_scheduling_requests`), wired into both the QUOTED and
SCHEDULING deterministic branches:

```
PRIMARY.check() → valid  → Booking Flow (fallback never evaluated)
               → invalid → explain PRIMARY (real reason) → FALLBACK evaluated
                         → fallback exact time valid → Flow, primary explained in the same message
                         → otherwise → one text: primary rejection + fallback slots
```

Real Wild input, Tuesday 2026-09-01:

| | before L4.3 | after L4.3 |
|---|---|---|
| `Mñ 15hs? O nose jueves que tenes` | `('2026-09-03', '15:00')` — Wednesday lost, 15:00 transplanted | `[(2026-09-02, 15:00), (2026-09-03, None)]` |
| reply | "Para jueves 03/09 a las 15:00 no tenemos disponibilidad. Horarios disponibles: 13:00." | "Para mañana miércoles 02/09 a las 15:00 no tengo disponibilidad (ese horario ya está reservado) y no me queda ningún horario libre ese día. Para jueves 03/09 tengo 13:00. ¿Te sirve?" |

---

## 3. Phase B — business-hours single authority

`schedule.py` now owns `_WEEKDAY_HOURS` plus `business_hours_for_weekday()`,
`format_business_hours_es()` and `business_hours_summary_es()`.
`ScheduleService._business_hours()` delegates to it, and the CE FAQ answer is **generated**
by `_faq_hours_answer()`.

- Retired: `"Trabajamos de lunes a viernes de 9 a 18 hs y los sábados de 9 a 15 hs."`
- Generated: `"Trabajamos lunes de 13 a 18 hs, martes de 9.30 a 14 hs, miércoles de 9 a 18 hs, jueves de 9 a 14 hs, viernes de 9 a 18 hs y sábados de 9 a 15 hs. Los domingos no trabajamos."`

HOURS-01 asserts every weekday phrase in the answer equals the scheduler's table, and a
divergence test mutates the table and proves the answer follows it. Two existing suites
(`test_wild04r_f3_faq_preservation.py`, `test_messy_turn_reconciliation.py`) were updated
to derive the constant instead of hard-coding it.

---

## 4. Phase C — Booking Flow wiring

`ConversationEngine._send_booking_flow()`:

1. checks prerequisites CE legitimately owns — active candidate + resolvable zone;
2. mints `make_booking_token(thread_id)` **once**, stores it on
   `WhatsAppThreadState.flow_booking_token`, commits;
3. calls `BookingFlowService.resolve_context(token)` — the published Flow's **own**
   contract validation, never re-implemented in CE;
4. sends via `_send_flow_button(..., flow_id=settings.booking_flow_id,
   initial_screen="APPOINTMENT", path_id=OutboundPathId.BOOKING_FLOW, cta_label="Reservar turno")`;
5. on contract rejection: reverts the token and falls back to the legacy Flow
   (never to a chat booking); on transport failure: reverts and stops — no second send.

`_send_flow_button` gained `path_id` and `cta_label` parameters; the default remains
`CE_FLOW`, so the six frozen L2 call sites keep their certified attribution.

Eligibility is the established valid slot, never bare quote acceptance (FLOW-06).

---

## 5. Phase E — rejection semantics

`_rejection_reason_es()` maps `ScheduleService` reasons to business language:

| Scheduler reason | Customer explanation |
|---|---|
| outside operating hours | "ese día trabajamos de 9 a 14 hs" (generated per weekday) |
| overlaps a booked slot | "ese horario ya está reservado" |
| travel constraint | "no llegamos a tiempo desde el turno anterior" |
| Sunday | "los domingos no trabajamos" |

Applied to both the single-request rejection message and the ordered primary/fallback
reply. The real reason is no longer dropped when alternative slots exist.

---

## 6. Phase F — forensic attribution

- `GIT_SHA` injected through `docker-compose.beta.yml` → `get_deployment_id()` returns the
  real build id instead of `"unknown"`.
- CE mints one `correlation_id` per turn (`_turn_correlation_id()`), passed to
  `gate.attempt()` from both `_send_text_to_wa` and `_send_flow_button`, so every outbound
  row of a turn is joinable.
- Frozen L2 path semantics untouched.

---

## 7. Phase G — INFRA-OOM-01 hardening

Measured steady state before setting any limit (2026-09-01): backend 116 MiB,
n8n 326 MiB, postgres 93 MiB on a 3.8 GiB host.

| Control | Value |
|---|---|
| swap | 4 GB active + persistent in `/etc/fstab` (owner, pre-existing) |
| n8n | `restart: unless-stopped`, `mem_limit: 1g`, `mem_reservation: 384m` |
| backend | `restart: unless-stopped`, `mem_limit: 1g`, `mem_reservation: 256m` |
| postgres | `restart: unless-stopped`, `mem_limit: 768m`, `mem_reservation: 192m` |
| preflight | `scripts/preflight_memory_check.sh` — swap ≥ 2 GB, available RAM ≥ 1 GB |
| test policy | heavy suites run sequentially in one container; agent/test workload is
  explicitly non-production and must never be allowed to starve CRM/n8n |

Limits are 3–8× steady state: they contain a runaway container instead of letting it
trigger a host-wide OOM that selects an arbitrary victim. Still open (tracked, not
implemented here): n8n liveness + inbound-gap alerting via the proven Resend channel.

The scheduling defect is **not** attributed to the OOM, and no frozen gate was reopened
because of it.

---

## 8. Tests

`tests/test_l4_3_scheduling_semantics.py` — **42/42 PASS**

TEMP-01…07 (+2 extra: weekday-never-suppresses-relative, legacy parity),
ORDER-01…03 (+3: primary never discarded, no bookable preference left, FLOW-05 in context),
HOURS-01/02 (+3: legacy wording gone, divergence guard, rejection taxonomy),
FLOW-01…08 (+5: settings default, gate receives BOOKING_FLOW, token parseable,
prerequisite blocking, token revert, real-session end-to-end contract),
FORENSIC-01/02 (+1), Wild A reproduction (3 tests covering all 10 asserted points).

Frozen gate suites: 206 passed in the combined L1/L2/L3/L4/kill-switch run;
M21.3 booking + scheduler suites 105/105 PASS.

Full regression (3 146 collected): **3 074 passed, 72 failed, 9 errors, 72 skipped**.
A differential run against the pre-change source tree (extracted from the previously
deployed image) produced the identical failure set: **0 regressions, 0 unknown failures**.
The 17 failures inside the L4/L4.1 suites are pre-existing SQLite-vs-PostgreSQL fixture
failures, unchanged by this work.

---

## 9. Runtime

| Item | Value |
|---|---|
| Image | `ridecheck-crm-backend:l4.3-sched-semantics` (rebuilt from this source) |
| Target | crm_test only |
| OUTBOUND | OFF (`OUTBOUND_ENABLED=false`) |
| Booking Flow ID | `28104222025943520` pinned in the beta compose |
| deployment_id | injected via `GIT_SHA` |
| Wild A tester state | untouched — thread 2036 / lead 122 / candidate 130 preserved |

---

## 10. Status

- Wild A: **PAUSED / FAILED AT SCHEDULING** — partial certification retained.
- Clean-Wild counter: **0/3**.
- L4: **NOT PASS**. L4.3 closes the code-level findings; runtime proof requires a
  controlled Wild B with owner outbound authorization.
