PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L4.7W4-CONTROLLED-WILD

# The best conversation yet, ending on a Flow that cannot open

Wild stopped by owner · no code changed · no tester reset · no outbound after the stop
Runtime `ridecheck-crm-backend:w3f1-semauth-d5f89b3`, container unrestarted since 18:18:24Z

---

## 1. Conversation

| # | dir | content |
|---|---|---|
| 6083 | in (voice) | `Hola, ¿cómo están? Bueno, quería revisar un 2008 del 2014. ¿Ustedes hacen eso?` |
| 6084 | in (voice) | `¿Cómo es? ¿Mandan informe? ¿De qué se trata el informe? ¿De estar presente?` |
| 6085 | in (voice) | `Se fue a pagar con debito.` |
| 6086 | **out** `CE_TEXT` | service + report + presence answered, **payment appended once**, then asks for the zone |
| 6087 | in (voice) | `Ok, perfecto. El auto está en Berazategui, pero yo soy de Tigre.` |
| 6088 | **out** `CE_TEXT` | `…Peugeot 2008 2014 en Berazategui es de $240.000. Si te parece bien, podemos avanzar.` |
| 6089 | in | `Sí, dale. Bueno, avancemos. ¿Qué horarios tienen ustedes?` |
| 6090 | **out** `CE_TEXT` | asks day/time **+ the weekday table** — because the customer explicitly asked for it |
| 6091 | in | `oka, mañana podes? tengo que arreglar con el dueño` |
| 6092 | in | `o sino pasado` |
| 6093 | **out** `BOOKING_FLOW` | `Para mañana martes 08/09 tengo 5 horarios disponibles. Elegí el que te sirva…` |

## 2. All three W3-F1 fixes proved themselves live

**Mixed-intent acceptance — the W3 defect is closed.** `"Sí, dale. Bueno, avancemos. ¿Qué
horarios tienen ustedes?"` is acceptance beside an FAQ, exactly the shape that produced
`stance=None` in W3. This time:

```
L4.7C.3B AUTHORIZE result=ALLOW rule=authorize.quote_acceptance@v1 stance=ACCEPT
  satisfied=['stance_is_accept', 'acceptance_is_present_and_factual',
             'acceptance_read_not_derived', 'quote_exists', 'quote_delivered',
             'quote_in_current_cycle', 'quote_inputs_unchanged']
  reason=explicit present acceptance of a delivered, current quote
```

**The causal reason is acceptance authorization**, not incidental scheduling progression.

**FAQ context — no duplication.** The weekday table went out once, on the turn where the
customer literally asked for it; the explicit-ask rule kept it, correctly. The W3
`service_scope` overlap did **not** recur: 6086 appended only the payment answer, which the
AI had not covered. No `FAQ RECONCILE` suppression fired because there was nothing redundant
to suppress.

**Flow-first — dispatched at exactly the right moment.**

```
L4.7W3-F1 FLOW-FIRST thread_id=2041 day=2026-09-08 period=- slots=5 dispatched
```

No five-slot prose dump. `path_id=BOOKING_FLOW`, gate ALLOWED → SENT, Meta 200, delivered,
read. Timing, gating and attribution all correct.

**Also correct:** vehicle `Peugeot / 2008 / 2014 / SUV_4X4_DEPORTIVO`; location
`Sur / Berazategui` with `"yo soy de Tigre"` **not** leaking into the inspection location;
quote $240.000 on correct inputs; `WILD04R-F6 catalog authority: AI proposed 'AUTO' → using
'SUV_4X4_DEPORTIVO'` — a guard doing its job. **0 wrong canonical writes, 0 false
progression, 0 unauthorized outbound, 0 security events, 0 UNKNOWN paths, 1 model call per
burst.**

## 3. The defect: the Flow is dispatched in the wrong mode

`whatsapp_ui.py:366`

```python
"flow_action": "navigate",
"flow_action_payload": {"screen": initial_screen},
```

and `_send_booking_flow` calls it with `initial_screen="MAIN"`.

The Booking Flow is an **endpoint (Data Exchange)** Flow. Its handler implements exactly the
right contract — `ping`, **`INIT` → APPOINTMENT screen with available dates**,
`data_exchange(date_selected / prepare_summary / confirm_booking)`. But `flow_action:
"navigate"` tells Meta to render a screen **client-side** and never to call the endpoint.

Observed, and this is the proof: **64 data-exchange requests in the session log, every one
`action=ping`. Not a single `INIT` after the Flow was delivered and read.** The customer
opened it and Meta had nothing to render.

Two mismatches in one dispatch:

| | sent | Booking Flow contract |
|---|---|---|
| action | `navigate` (client-side) | `data_exchange` (server-driven) |
| initial screen | `MAIN` | `APPOINTMENT` (what INIT returns) |

The helper says so in its own first line: *"M17 — Send a WhatsApp Flow button message
(**data-collection mode, no back-end**)"*. It was written for the endpoint-less vehicle and
location fallback Flows. `_send_booking_flow` reuses it, so the one Flow that **does** have a
back end is dispatched as though it had none.

**The endpoint itself is healthy.** All 64 requests returned 200; RSA decryption works. The
single `BOOKING_FLOW_DECRYPT_FAIL` / 421 in the log is at 15:18:33 — **my own `curl {}` probe
during preparation**, not customer traffic. I am naming it so it is not misread later.

## 4. This is pre-existing, and flow-first is what revealed it

`git log -S'"flow_action": "navigate"'` → `7c901e9 M17: add WhatsApp interactive and Flow
outbound support`. `_send_booking_flow`'s use of the same helper dates to `3ee63ae`.
W3-F1 (`d5f89b3`) modified **neither file** — it only added a caller.

Before flow-first, the Booking Flow dispatched only after a customer spoke an exact time. No
Wild ever got there: W1 stopped on the vehicle, W2 on the location, W3 ended at the slot
list. **W4 is the first time a real customer has opened the Booking Flow**, and it failed the
first time it was asked to work.

That is the honest reading of "the booking flow was sent with correct timing but never
worked": the timing is new and correct, and it exposed a dispatch defect that has been
latent since M17.

## 5. Findings

| id | severity | finding |
|---|---|---|
| W4-1 | **BLOCKER** | Booking Flow dispatched with `flow_action: "navigate"` and `screen: "MAIN"`; the endpoint Flow needs `data_exchange` and its INIT returns `APPOINTMENT`. Meta never calls the endpoint, so the Flow cannot render or book. Booking is unreachable. |
| W4-2 | LOW | `_send_whatsapp_cloud_flow` is shared by endpoint-less fallback Flows and the endpoint Booking Flow, with no parameter distinguishing them — the structural reason W4-1 was possible. |
| W4-3 | LOW | Turn-1 latency 7.0 s; the flow-first turn 2 986 ms. |

**BLOCKER: W4-1. HIGH: NONE.** Nothing unsafe happened: `thread_revisions = 0`, no booking
was created, no availability was asserted as booked. The Flow simply could not open.

## 6. State and evidence

```
lead 127  ACEPTADO / CONSULTA_NUEVA          candidate 133  Peugeot 2008 2014 SUV Sur/Berazategui
stage SCHEDULING · requested 2026-09-08 · offered 11:00 11:30 12:00 12:30 13:00 · token minted
thread_revisions 0 · security_events new 0 · paths: CE_TEXT 3, BOOKING_FLOW 1, UNKNOWN 0
```

```
/opt/ridecheck-crm-forensics/L4.7W4_backend_stdout_2026-09-07T211654Z.log
   sha256 b1e406447166213a02b06b6670d5d14fe185087c9e7363519fd7379a2e7ef0b3
/opt/ridecheck-crm-forensics/L4.7W4_tester_export_pre_reset_2026-09-07T181752Z.txt
   sha256 870599ad5fd9e9ccc7f8398a61429679109cde2dc328befbfe0cc94e62d60fb9
shadow_turn_evidence 54 → 58 · reconciliation_records 10 → 12 · authorization_records 15 → 20
```

Thread 2041, candidate 133 and lead 127 left untouched. Outbound remains armed as the owner
set it. Wild clean count remains **0/3** — the session is not clean.
