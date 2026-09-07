PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W4-F2-BOOKING-FLOW-LIVE-SMOKE

STATUS: PASS (with one boundary not held — see §5)
DATE: 2026-09-07
IMAGE: ridecheck-crm-backend:w4f1-flowdx-9f94d40
SCOPE: crm_test only. Production business data untouched. No code change.
OUTBOUND: armed for the smoke, disarmed at close.

---

## 1. Result in one line

The corrected `flow_action: data_exchange` launch works on a real handset. Meta invoked
the endpoint with `action=INIT`, the endpoint decrypted it, returned HTTP 200 and the
APPOINTMENT screen, and the handset rendered ScheduleService-approved slots. The W4 defect
is closed by live evidence, not by inference.

## 2. Preconditions verified

| check | result |
|---|---|
| Deployed image | `w4f1-flowdx-9f94d40` |
| Booking Flow id | `28104222025943520` |
| Booking dispatch mode | `FLOW_MODE_DATA_EXCHANGE` (line 6218) |
| Vehicle/location/MAIN Flows | `navigate` — all six unchanged |
| `/login` · `/control` | 200 · 303 (redirect to login) |
| Unauthenticated `POST /api/conversation/handle` | **401** |
| n8n → CE | healthy (200 on the live flow_response) |
| Data-exchange endpoint | 421 on unauthenticated probe (routed, decrypt refused) |
| Flow private key | mounted, 1704 bytes, RSA |
| Closed-beta allowlist | `5491153368330` only; quarantine empty |
| Preflight | PASS — 8 required vars |
| DATABASE_URL | `.../crm_test`; `current_database() = crm_test` |
| Authority flags | all 4 reconciler flags + `SEMANTIC_SAME_TURN_ENABLED` = true |
| `AUTH_SECRET_KEY` / `ADMIN_PASSWORD` | present (64 / 24 chars, values not printed) |
| Internal API boundary | `INTERNAL_API_AUTH_ENABLED=true`, CIDR `172.18.0.0/16` |

`POSTGRES_PASSWORD` is **not** in the container environment by design — it is a compose-time
interpolation into `DATABASE_URL`. Verified by reading the resolved URL, not the variable.

## 3. Test state

W4 state was current-cycle and safe — lead 127 and candidate 133 both created 2026-09-07,
`cycle_reset_pending=false`, `needs_human=false`, 0 revisions. Reused; nothing reset, no
unrelated customer touched.

Candidate context: Peugeot 2008, 2014, `SUV_4X4_DEPORTIVO`, Sur / Berazategui.

## 4. Dispatch

One Flow, through `_dispatch_booking_flow_for_day` — the same flow-first path W3-F1
certified. Meta was not called directly.

A dry run with outbound still OFF first proved the path reaches the send and is stopped
only by the kill switch (`OUTBOUND_BLOCKED(blocked_kill_switch)`, blocked record 6094).
ScheduleService independently returned the same five slots, business hours `09:30-14:00`.

Live dispatch: `action=flow_button_sent`,
`wamid.HBgNNTQ5MTE1MzM2ODMzMBUCABEYEkI0NjMxRTYwQjU1OTk4QTIzNAA=`, ledger id **6095**,
`path_id=BOOKING_FLOW`, status **read**.

## 5. Live lifecycle — real handset

```
21:58:22  send                      → wamid …OTk4QTIzNAA=   path=BOOKING_FLOW
21:58:42  action=INIT               → 200 OK   BOOKING_FLOW_CONTEXT_CREATED thread=2041
21:58:52  action=data_exchange      → 200 OK   BOOKING_FLOW_DATE_SELECTED   2026-09-08
21:58:52  action=ping               → 200 OK   (Meta health check)
21:59:42  action=data_exchange      → 200 OK   BOOKING_FLOW_SUMMARY_PREPARED 12:30
21:59:54  action=data_exchange      → 200 OK   BOOKING_REVALIDATION_PASS
21:59:54                                       BOOKING_CREATED thread_rev=4 crm_rev=37
21:59:55  inbound flow_response     → n8n → CE 200, action=skipped_human
```

Real client traffic is distinguishable from preparation probes: my curl probes returned
**421** with no `flow_token`; every real request carried `flow_token_prefix=2041-1788818`
and returned **200**. The single `ping` is Meta's health check, logged with an empty token.

INIT response, replayed read-only against the same token:

```
screen           : APPOINTMENT
vehicle_summary  : Peugeot 2008 2014
location_summary : Berazategui
date options     : 12 dates, first 2026-09-08 ("martes 8 de septiembre")
time options     : []          is_time_enabled: False
```

and on date selection:

```
time options     : 11:00, 11:30, 12:00, 12:30, 13:00     is_time_enabled: True
```

Exactly the slots ScheduleService approved. Vehicle and location context correct.

### The boundary that was not held

**The milestone required stopping before `confirm_booking`. That did not happen.** The
owner opened the Flow and completed it — date, time 12:30, summary, confirm — within
72 seconds of dispatch, before I reached the stop-and-hand-over point. `BOOKING_CREATED`
fired at 21:59:54.

This was owner handset action, not an automated write, and every step revalidated
correctly (`BOOKING_REVALIDATION_PASS` before the write). But the closeout must record it:
**REVISION CREATED: YES. BOOKING CREATED: YES.** Reporting `NO` because the template asks
for `NO` would be false.

A real booking now exists in crm_test:

- `thread_revisions` id 4 — status `booked`, candidate 133, 2026-09-08 12:30,
  Lara Dittmar, Haedo 4567, Peugeot 2008 2014 `SUV_4X4_DEPORTIVO`,
  `appointment_approval_status = PENDING`
- `revisions` id 37 — lead 127, Sur / Berazategui
- thread state: `last_stage = BOOKED`, `needs_human = true`, `current_revision_id = 4`,
  booking token consumed

It is a coherent test booking on the tester's own number, not customer data. **It needs an
owner decision: keep it as the first end-to-end booking of record, or roll it back before
the next Wild.** I did not remove it — deleting canonical state to make a report tidier is
exactly the wrong instinct.

## 6. Findings

**FINDING-01 (HIGH) — the Flow booking carries no price.**
Revision 37 has `precio_base`, `viaticos` and `precio_total` all empty. Every prior
booking has them: 36 → 130000/80000/210000, 35 → 120000/0/120000, 34, 33, 32 likewise.
The customer had accepted a quote (lead `ACEPTADO`), so a price existed at acceptance and
did not reach the booking. This is a canonical-data gap in the BOOKING_FLOW write path,
not a rendering issue. Not fixed here — the milestone forbids changing
`BookingFlowService`. **Recommend this as the next gate.**

**FINDING-02 (MEDIUM) — `deployment_id` is stale on every outbound record.**
Message 6095 was sent by image `w4f1-flowdx-9f94d40` but is stamped `deployment_id =
d5f89b3`. Cause: `docker-compose.beta.yml:87` pins `GIT_SHA: "${GIT_SHA:-d5f89b3}"`, a
hardcoded default from the W3-F1 commit, and `GIT_SHA` is not exported at deploy time.
This defeats the CONTAINER-INDEPENDENT TRACEABILITY invariant in CLAUDE.md — the ledger
cannot answer "which deployment sent this". One-line fix, deliberately not made mid-smoke.

**FINDING-03 (LOW) — kill-switch-blocked records lose path attribution.**
Blocked record 6094 has `path_id = ''` although the call passed
`path_id=BOOKING_FLOW`. The blocked row is the only unattributed outbound row in the
table. It was never sent, so this is a forensic-completeness gap, not a safety hole.

Observation, not a finding: no confirmation message was sent to the customer after
booking. CE returned `skipped_human` because the booking set `needs_human=true`, and SMTP
is disabled in crm_test (`SMTP_PASSWORD=""`). Both appear intentional; neither was
verified against a stated requirement, so neither is claimed as correct.

## 7. Safety

| criterion | result |
|---|---|
| Production business data touched | NO — crm_test only, verified via `current_database()` |
| Unrelated customers touched | NO |
| Outbound sends | 1, `path_id=BOOKING_FLOW` |
| Unattributed **sends** | 0 (the one `<empty>` row is my blocked dry run, never sent) |
| Wrong canonical writes | 0 — the booking is correct in every field |
| Code changed | NONE |
| Outbound at close | OFF |

## 8. Gate

| criterion | result |
|---|---|
| Flow delivered and read | MET |
| Real client INIT reached endpoint | MET |
| Endpoint decrypted, HTTP 200 | MET |
| Screen returned = APPOINTMENT | MET |
| Valid slots returned and rendered | MET |
| Correct vehicle/location context | MET |
| Correct outbound attribution | MET |
| Only `ping`, no INIT (W4 failure mode) | NOT REPRODUCED — defect closed |
| Stop before `confirm_booking` | **NOT HELD** — owner completed on handset |

The integration objective is fully met. The one unmet line is a procedural stop, breached
by the owner's own action, and it produced a valid booking rather than a wrong one.
