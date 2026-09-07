PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: OPS-CONTROL-MESSAGE-PATH-VISIBILITY

# The trace can be narrowed, and the CAMINO column stops lying by omission

Observability only · no conversation, routing, authority or outbound behaviour changed
crm_test only · production untouched · no schema change · read-only dashboard

---

## 1. Verdict

**PASS.** The message trace filters by customer, direction and path in any combination; the
path legend is read from the live registry; an inbound message and an unattributed outbound
one no longer look identical; and the TOTAL column shows a number.

## 2. What W3 made obvious

Auditing W3 was harder than it needed to be for two reasons, both fixed here.

The trace could not be narrowed to one conversation, so every question meant reading the
whole window. And the CAMINO column rendered **the same dash** for three different things:
an inbound message (which has no send path and never should), an outbound record with a
`NULL` path (which is a **forensic finding**), and an unregistered path. A dash for all
three is the one thing a Wild audit cannot afford.

## 3. Trace filters — they combine

`/api/ops/messages` now accepts `contact_id` and `path_id` alongside the existing
`direction` and `thread_id`. Verified live:

```
window=7d                        -> 12 msgs
window=7d&direction=out          ->  5
window=7d&path_id=CE_TEXT        ->  4
window=7d&path_id=MANUAL_CRM     ->  1
window=7d&path_id=UNKNOWN        ->  0     (no unattributed outbound exists — correct)
contact_id=2047                  -> 12      (out only: 5)
```

`path_id=UNKNOWN` is a deliberate selector for *outbound records with no path at all* — the
case that must be findable, not merely visible.

UI: two dropdowns above the trace (default **Todos**), a **Limpiar** button, and a
**Filtrar trazado** shortcut on every conversation row that applies that customer and scrolls
to the panel. The existing **Ver** action is untouched, and the existing Todos/IN/OUT buttons
still work and combine.

**Privacy:** the customer dropdown is built from `display_name` or the **masked** wa_id —
never a full phone number — and a test asserts the raw `wa_id` is not read in that code path.

## 4. Honest path display

| situation | before | now |
|---|---|---|
| inbound message | `—` (grey "unknown" badge) | **INBOUND**, neutral badge |
| outbound, `path_id` NULL | `—` (same badge) | **UNATTRIBUTED**, red |
| outbound, unregistered path | same badge | **UNKNOWN (\<value\>)**, red |
| outbound, legacy path | same badge | path name, critical badge |
| outbound, authorized | normal badge | unchanged |

The API now returns `path_display` and `path_class` per row, so the classification is made
server-side where the registry lives rather than re-derived in JavaScript. Live sample:

```
id=6082 out display=CE_TEXT     class=authorized
id=6081 in  display=INBOUND     class=inbound
id=6080 out display=MANUAL_CRM  class=authorized
```

## 5. Legend read from the registry, not copied from it

New `GET /api/ops/path-registry` enumerates `OutboundPathId` **at call time**, so a path
added to the registry appears in the legend automatically and one described but no longer
registered simply never renders. A test asserts the legend set equals the enum set exactly.

Live output — all 8 registered paths:

| path | kind | authority | initiator |
|---|---|---|---|
| `BOOKING_FLOW` | AUTOMATED | AUTORIZADO | Servicio de reservas |
| `CE_FLOW` | AUTOMATED | AUTORIZADO | ConversationEngine |
| `CE_INTERACTIVE` | AUTOMATED | AUTORIZADO | ConversationEngine |
| `CE_LIST` | AUTOMATED | AUTORIZADO | ConversationEngine |
| `CE_TEXT` | AUTOMATED | AUTORIZADO | ConversationEngine |
| `MANUAL_CRM` | **HUMAN** | AUTORIZADO | Operador con sesión iniciada en el CRM |
| `SYSTEM_NOTIFICATION` | AUTOMATED | AUTORIZADO | Tarea programada |
| `LEGACY_N8N_AI_PIPELINE` | AUTOMATED | **BLOQUEADO** | Pipeline de IA legacy en n8n |

The retired path is **shown and marked blocked**, not hidden — it is still in the registry,
so the dashboard says so. Descriptions were written against executable source: `MANUAL_CRM`
is the only HUMAN path, and `BOOKING_FLOW` is described as the only path that confirms a
reservation because `_process_flow_response` is still the sole writer of a booked revision.

Clicking a path row filters the trace to that path.

## 6. The TOTAL bug — audited and fixed

`Caminos de Envío` rendered `—` in the TOTAL column for every path. Cause: the JavaScript
read `r.total` and `/api/ops/paths` only ever emitted `count`. The field is now emitted under
both names and the UI falls back to `count`. No number was invented — live data reads
`CE_TEXT total=4 ok=4`, `MANUAL_CRM total=1 ok=1`, matching the five outbound rows in the
window exactly.

One thing I did **not** change: `/api/ops/paths` counts only `automated = true` outbound
records. Today that includes the operator's `MANUAL_CRM` send, because the gate marks
everything it writes automated — so nothing is currently hidden. It is a latent
inconsistency (a human send recorded as automated) and changing either the flag or the
filter would alter certified counting semantics, so it is recorded rather than adjusted.

## 7. A stale fixture from the previous milestone

The full regression surfaced a failure I had introduced in OPS-CRM-500 and not caught: after
that closeout I extended `preflight_deploy.sh` from six required variables to eight and never
re-ran the suite, so its own test fixture went stale. The fixture now **derives the list from
the script**, which is what it should have done originally. Reporting it because I caused it
and the previous closeout's "0 new failures" did not account for it.

## 8. Tests, regression, deployment

`tests/test_ops_control_message_path_visibility.py` — **20/20** (CONTROL-01…11 plus
unregistered-path naming, privacy in the dropdown, and legend/registry equality). The suite
builds a two-customer fixture covering authorized, human, blocked and **unattributed**
outbound.

`tests/test_m21_3_ops_dashboard.py` — **134 passed** together with the new suite; the
existing dashboard contract is intact. The new filters are type-checked rather than
truth-checked precisely so those certified tests, which call the endpoints directly with
unresolved `Query` defaults, keep working.

Full regression: **3 656 passed / 57 failed / 9 errors**, failure set identical to the
OPS-CRM-500 baseline, **0 new**.

Deployed via the preflight gate (8 variables + compose wiring). Runtime
`ridecheck-crm-backend:opsctrl-paths-5a59891`, restarts 0. Verified after deploy:

```
/login 200 · /control 200 (authenticated) · external -> CE 401 · external -> send 401
n8n -> CE 422 (auth passed) · OUTBOUND_ENABLED=true (tester scope, unchanged)
authority flags V/L/A/S/C4A all true · AUTH_SECRET_KEY and ADMIN_PASSWORD preserved
```

No Wild started.
