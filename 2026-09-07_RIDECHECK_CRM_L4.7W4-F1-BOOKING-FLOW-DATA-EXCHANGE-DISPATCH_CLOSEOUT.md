PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W4-F1-BOOKING-FLOW-DATA-EXCHANGE-DISPATCH

STATUS: CONDITIONAL_PASS
DATE: 2026-09-07
CODE COMMIT: 9f94d40
IMAGE: ridecheck-crm-backend:w4f1-flowdx-9f94d40
SCOPE: crm_test only. No production migration. No schema change. No secrets printed.
OUTBOUND: OFF at close (see FINDING-02 — it was ON when this milestone began).

---

## 1. The defect

Wild W4 dispatched the Booking Flow correctly in every respect but one. Right moment,
right contact, right Flow id, right initial screen, correct `path_id` attribution,
delivered, read, and opened by the customer — and it did not work.

The evidence that named the cause was the absence of evidence: 64 data-exchange requests
reached the endpoint during that session, and every one was a Meta health-check `ping`.
Not a single `INIT`. The APPOINTMENT screen could not render because nothing had ever
asked the back end for its slots.

The message carried `flow_action: "navigate"` with a client-side
`flow_action_payload: {"screen": "APPOINTMENT"}`. That is the launch contract for an
*endpoint-less* Flow: it tells Meta to render the named screen on the client and never
call anyone. RideCheck Booking Flow `28104222025943520` is the one endpoint-backed Flow
of the seven. It must be launched as `data_exchange`.

**The defect was the launch action, and only the launch action.**

### Correction to the W4 audit

The W4 audit stated the Booking Flow was dispatched with `screen="MAIN"`. **That was
wrong.** `_send_booking_flow` already passed `initial_screen="APPOINTMENT"`. Line 6093,
which the audit read, belongs to `_try_schedule_and_flow` — a different, legacy Flow
dispatch. The screen was never the problem, and a fix aimed at it would have changed
nothing.

## 2. Flow inventory (read from the live environment)

| Flow | id | initial screen | endpoint | launch mode |
|---|---|---|---|---|
| Booking | 28104222025943520 | APPOINTMENT | **backed** | **data_exchange** |
| Main | 1644218879979041 | MAIN | none | navigate |
| Website | 1535038801697863 | WEBSITE_FINAL_DATA | none | navigate |
| Vehicle fallback | 27205677485784073 | VEHICLE_DETAILS | none | navigate |
| Location fallback | 2550767958730294 | LOCATION_DETAILS | none | navigate |

One endpoint-backed Flow; six endpoint-less. Only the first row changed.

## 3. The fix

`backend/app/ui/whatsapp_ui.py` — `_flow_launch_fields(mode, initial_screen)` is now the
single source of the launch action:

- `data_exchange` → `{"flow_action": "data_exchange"}` and **no** `flow_action_payload`.
  The endpoint's `INIT` response names the first screen; a client-side screen alongside it
  would contradict the endpoint.
- `navigate` → `{"flow_action": "navigate", "flow_action_payload": {"screen": ...}}`.
- Anything else, including `None` and `""`, raises `ValueError`. The mode is never
  inferred and never defaulted at the transport layer.

`backend/app/services/conversation_engine.py` — `_send_booking_flow` declares
`mode=FLOW_MODE_DATA_EXCHANGE` explicitly. The other six call sites are untouched and
keep `navigate`.

This is a general transport invariant — *a Flow is launched according to whether it has an
endpoint* — not a patch shaped around one Wild sentence (§6.1 satisfied).

## 4. Evidence under the deployed image

Payload actually built by `w4f1-flowdx-9f94d40` with live settings, Meta call intercepted,
nothing sent:

```
BOOKING FLOW (endpoint-backed)
  flow_id              : 28104222025943520
  flow_action          : data_exchange
  flow_action_payload  : <absent — correct>
  flow_message_version : 3

VEHICLE FLOW (endpoint-less, control)
  flow_id              : 27205677485784073
  flow_action          : navigate
  flow_action_payload  : {'screen': 'VEHICLE_DETAILS'}
```

Endpoint routed and rejecting unauthenticated payloads:
`POST /integrations/whatsapp/flows/booking/data-exchange` → **421** (decryption refused).
A 404 would have meant unrouted.

## 5. Tests

`tests/test_l4_7w4_f1_booking_flow_data_exchange.py` — 16 tests, BF-DX-01…15:

| id | assertion |
|---|---|
| BF-DX-01 | booking dispatch declares `FLOW_MODE_DATA_EXCHANGE` |
| BF-DX-02 | booking initial screen is APPOINTMENT |
| BF-DX-03/04 | vehicle and location Flows remain `navigate` |
| BF-DX-05 | mode is explicit; unknown/absent mode raises |
| BF-DX-06/07 | W3-F1 flow-first dispatch and no-prose-dump intact |
| BF-DX-08/09 | `INIT` implemented; `handle_init` returns APPOINTMENT |
| BF-DX-10 | INIT / date_selected / prepare_summary write no booking |
| BF-DX-11 | `confirm_booking` (service) and `_process_flow_response` (engine) are the sole booking writers |
| BF-DX-12 | `path_id=BOOKING_FLOW` set at dispatch |
| BF-DX-13/14/15 | acceptance, FAQ, vehicle/location governance unregressed |

Plus a behavioural test in the certified L4.3 suite, **FLOW-02c**, asserting the mode on a
real dispatch rather than through static analysis.

**Regression: 3699 passed / 57 failed / 9 errors.** Failures and errors are identical to
the W3-F1 baseline — 0 new, 0 fixed, +17 passing.

VEHICLE FLOW REGRESSION = 0 · LOCATION FLOW REGRESSION = 0 · FLOW-FIRST REGRESSION = 0 ·
NEW LAUNCH FAILURES = 0.

## 6. Findings

**FINDING-01 (MEDIUM, fixed here) — a swallowed TypeError hid the failure mode.**
The L4.3 stub `fake_flow` had a fixed signature, so the new `mode` kwarg raised
`TypeError` *inside* the send, where `except Exception` logged it and returned `None`.
Seven certified tests then failed on `IndexError: list index out of range` — an empty
capture list — rather than on the real cause. The stub now takes `mode` and `**extra` and
records the mode, so a future kwarg surfaces as a contract failure at the assertion.

The same `except Exception` exists in production: if `_send_booking_flow` raises for any
reason, the token is restored, the error is logged, and the customer silently receives
nothing. That is safe but invisible from the customer's side. Not changed here —
out of scope, recorded for a future gate.

**FINDING-02 (MEDIUM, caused by this milestone) — the redeploy disarmed outbound.**
`OUTBOUND_ENABLED` is derived in `docker-compose.beta.yml` as
`"${BETA_OUTBOUND_ENABLED:-false}"`. The owner armed it for W4 by exporting
`BETA_OUTBOUND_ENABLED=true` at compose time; that lives in the shell, not in `.env`.
Recreating the backend without it fail-safed the runtime back to `OUTBOUND_ENABLED=false`.

The direction is the safe one and matches the standing constraint, but it contradicts the
"OUTBOUND unchanged" line in commit `9f94d40`, which was written before this was observed.
Recording it rather than leaving the commit message to stand as the record.

**I did not re-arm it.** Exporting `BETA_OUTBOUND_ENABLED=true` is the owner's
authorization gesture and is deliberately not a value I can flip in a tracked file.

## 7. Part 8 — live smoke proof NOT PERFORMED

The milestone asks for a controlled live proof: Meta accepts the send → `INIT` reaches the
backend → APPOINTMENT renders with slots. It was not performed, for two reasons, neither
of which I can resolve alone:

1. Outbound is OFF (FINDING-02). Re-arming is the owner's decision.
2. The proof requires a human to *open* the Flow on the tester handset. `INIT` is emitted
   by the client on open, not by the send. No amount of back-end work produces it.

What is proven without it: the bytes leaving the backend are now `data_exchange`, the
endpoint is routed and answering, and `handle_init` returns the APPOINTMENT screen. What
remains unproven: that Meta accepts this specific Flow in this mode and that the screen
renders slots on a real handset. **That is the conditional in CONDITIONAL_PASS.**

To run it, with the owner present: export `BETA_OUTBOUND_ENABLED=true`, recreate the
backend, send the tester one message advancing thread 2041 to a concrete slot, open the
Flow on the handset, and confirm an `INIT` (not a `ping`) in the endpoint log. **Stop
before final booking** unless completion is explicitly authorized.

## 8. Part 9 — Control dashboard

`GET /api/ops/path-registry` returns 8 paths; `BOOKING_FLOW` is present, labelled
"Flow de turno", `authorized: true`, `kind: AUTOMATED`, described as the only path that
confirms a reservation. `_path_display('out','BOOKING_FLOW')` → `BOOKING_FLOW`, badge
class `authorized`. Filtering the ledger by `path_id=BOOKING_FLOW` returns the W4
dispatch itself — message 6093, thread 2041 — the message this milestone repairs.

## 9. State

W4 state preserved untouched: thread 2041, lead 127 `ACEPTADO`, stage `SCHEDULING`,
`active_requested_date=2026-09-08`, booking token minted, `thread_revisions = 0`.
No booking was created. L1/L2/L3 remain FROZEN. Wild clean count remains **0/3**.

## 10. Gate

| criterion | result |
|---|---|
| Booking Flow launched as data_exchange | MET |
| Endpoint-less Flows unchanged | MET |
| No new regression vs W3-F1 baseline | MET (0 new) |
| BF-DX suite | MET (16/16) |
| Dashboard attribution | MET |
| Live Flow render proof | **NOT MET — owner-gated** |

CONDITIONAL_PASS. The condition is Part 8, and it is owner-gated, not engineering-gated.
