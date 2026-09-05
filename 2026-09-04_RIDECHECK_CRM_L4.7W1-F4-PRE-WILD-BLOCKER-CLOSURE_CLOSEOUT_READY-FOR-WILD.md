PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W1-F4-PRE-WILD-BLOCKER-CLOSURE

# Three blockers, closed

crm_test only · OUTBOUND OFF · production business data untouched · live n8n workflow **not modified**
no credential rotated · C2/C3B/C4/C4A ON · F2 fuzzy invariants preserved · C5 not started

---

## 1. Verdict

**PASS. READY FOR NEXT OWNER WILD: YES.**

All three F3 blockers are closed and proven on the running system, not argued from source.
Two residual items are recorded in §7 — neither can affect a Wild, and neither is hidden.

## 2. A correction to F3 I have to make first

F3 reported that the legacy n8n AI pipeline was "still live on the false branch". **That
was wrong.** F3 inferred reachability from node presence and a walk *upstream* from the
senders. Doing it properly — forward reachability from the `Webhook` trigger — gives:

```
reachable from trigger : 59 of 164 nodes
UNREACHABLE            : AI Router · AI Reply Planner · OpenAI Chat Model ×4
                         Create Candidate · Patch Thread State · Mark Leads Needs Human
                         Request Backend Price · Check Schedule
                         Send Whatsapp Reply · Send Whatsapp Reply1 · Send Whatsapp Reply2
```

The legacy conversational AI pipeline is **dead code in the live workflow**. My earlier
statement overstated the risk, and the method that produced it was inadequate.

**But the audit found something worse that F3 had missed**, and only because I stopped
assuming:

```
IF - Engine Handled? (Flow M18)
   [true]  -> []                          ← CE owns it, correctly nothing
   [false] -> Get Thread State (Flow) -> Get Thread (Flow) -> Get Lead (Flow)
              -> IF - Flow Already Booked? -> Create Revision2 (Flow)
              -> Booking Confirmed Reply (Flow) -> Send Whatsapp Reply (Flow)
```

A **live second booking authority**, reachable whenever CE returns `handled=false`. And
`blocked_dispatch` — which is emitted on **every turn while `OUTBOUND_ENABLED != "true"`**
— returned exactly that. The standing kill-switch state, the state the system sits in
between Wilds, was routing Booking Flow submissions to a chain that creates a Revision and
sends a confirmation, bypassing `_process_flow_response`, C2 and C3B entirely.

## 3. Blocker 1 — closed at the source, without touching the live workflow

`blocked_dispatch` is now in `HANDLED_ACTIONS`. This is the same reasoning M21.2.8 already
applied to `error`: **CE retains ownership of the event; n8n must not fall back to another
engine.** A kill-switch block is a CE *decision*, not an abdication. `ok=False` still
reports honestly that nothing was sent.

`no_lead` was **deliberately left unchanged.** Its certified rationale — no lead is linked,
so CE genuinely did not own the event — stands, and I found no evidence contradicting it. A
certified decision is not overturned because it is convenient.

Answer to the Phase 1 question, proven not assumed: **the legacy AI branch is C (unreachable
dead code); the legacy Flow-booking branch was B (fallback-only) and is now unreachable from
every CE outcome except `no_lead`.**

## 4. Blocker 2 — a real machine/human boundary, proven live

Three classes now exist: **PUBLIC** (Meta webhook, Flow data-exchange), **MACHINE** (CE
invocation, sends, thread/candidate/revision/state mutation, AI toggle, excluded phones),
**HUMAN** (CRM session). A MACHINE route accepts a machine credential *or* a logged-in
human, so the CRM UI keeps working.

The obvious mechanism — a shared header — would have required adding `X-Internal-Auth` to
~30 live n8n HTTP nodes: an owner-authorised rollout I could not validate without live
traffic. So I measured what actually distinguishes the callers:

| caller | client address |
|---|---|
| n8n (peer container) | `172.18.0.3` |
| nginx / published port / internet | `172.18.0.1` — the bridge **gateway** (SNAT) |

Excluding the gateway separates the transport from the internet. Both channels are
configurable, both default to empty, and enforcement needs an explicit flag on top — an
unconfigured deployment behaves exactly as before.

**Proven on the running system with enforcement ON:**

```
n8n      -> /api/conversation/handle           422   (auth passed, empty body rejected)
n8n      -> /api/settings/ai-enabled           200
external -> /api/conversation/handle           401
external -> /api/whatsapp/thread/1/send-text   401
external -> /leads                             401
Meta webhook                                   still public
```

Sends are now attributed by **caller**: machine → `CE_FLOW`, operator → `MANUAL_CRM`. The
ledger stops recording automated traffic under a human's name.

## 5. Blocker 3 — FAQ: semantics identify, business truth answers

The composers already mapped *topic → canonical answer*. Only the **detector** was broken:
literal phrase sets that miss real speech. The interpreter had all four topics right.

`_faq_topics_for_burst()` now unions the phrase detectors with the semantic reading, and
`_FAQ_TOPIC_ANSWERS` maps each topic to a deterministic business constant. **The semantic
layer contributes the label and nothing else.**

Topics the interpreter can emit but for which RideCheck has no authoritative answer —
`coverage`, `duration` — are **deliberately absent from the table**. Inventing one is
exactly what this layer exists to prevent.

The payment answer was also wrong by omission: it listed what we take without answering
what was asked. Business truth is now stated in full, including the "no".

**Live, on the deployed image, the exact Wild burst:**

```
fuzzy on burst      : UNRESOLVED
vehicle             : Peugeot / 2008 / SUV_4X4_DEPORTIVO / 2014
semantic FAQ topics : payment, presence, report, service_scope
resolved topics     : payment, presence, report, service_scope

  Al finalizar la revisión te enviamos un informe detallado.
  No es necesario que estés presente durante la inspección.
  Aceptamos efectivo, transferencia bancaria y Mercado Pago. Con débito no estamos
    trabajando por el momento.
  Vamos hasta donde está el vehículo y hacemos la revisión pre-compra en el lugar. Al
    terminar te enviamos el informe con todo lo que encontramos.
```

Four questions asked, four answered, vehicle retained, no redundant request, no Fiat Uno.

## 6. n8n provenance

`RUNTIME_LIVE_EXPORT_2026-09-04.json` — id `DaFqDIzVi1f92Hvz`, active, **164 nodes**.
Re-verified against the live database after all changes: **node count identical,
structure identical, connections identical**. Parity **PASS**, with the Google Maps key
redacted by SEC-01 policy (the only difference, deliberate and recorded).

**The live workflow was not modified.** No change to it was required, because both
blockers were closable on the backend side.

## 7. Two residuals, recorded

1. **`no_lead` can still reach the legacy Flow chain.** Its first step is `Get Lead (Flow)`
   for a lead that by definition does not exist, so the chain cannot complete a booking.
   LOW. Closing it means extending `HANDLED_ACTIONS` again — the owner's call, since it
   overturns a certified decision.
2. **Enforcement is configuration, not code.** `INTERNAL_API_AUTH_ENABLED=true` and the
   trusted CIDR are set for crm_test. Production needs the same configuration, and the
   CIDR must match its compose network.

## 8. Tests and regression

`tests/test_l4_7w1_f4_pre_wild_closure.py` — **14/14** (PREWILD-01…10 plus the
peer-container channel, no-duplicate FAQ composition, and the scope answer).

**Three pins realigned deliberately**, each documented in place: `blocked_dispatch`
ownership (m20_2, m20_4_3) and caller-derived `path_id` (L2 transport integrity). The
`no_lead` assertion was restored after a blanket edit caught it — that was my error, found
by the suite.

Full regression: **3 614 passed / 57 failed / 9 errors**, failure set identical to the F3
baseline, **0 new**.

Runtime `ridecheck-crm-backend:l4.7w1f4-prewild-23fbe02`, restarts 0, parity MATCH ×4,
`outbound=False`, crm_test rows unchanged (4 / 0).

## 9. Pre-Wild gate

| line | result |
|---|---|
| LIVE N8N LEGACY CONVERSATIONAL AUTHORITY | **0** |
| LIVE N8N → CE | **PROVEN** |
| REPO/RUNTIME N8N PARITY | **PASS** |
| AUTOMATED N8N SENDS MISATTRIBUTED MANUAL_CRM | **0** |
| UNAUTHENTICATED CE INVOCATION | **0** |
| UNAUTHENTICATED CUSTOMER SEND | **0** |
| UNAUTHENTICATED CANONICAL STATE MUTATION | **0** |
| FAQ SEMANTIC TOPIC DETECTION | **PASS** |
| FAQ DETERMINISTIC ANSWERS | **PASS** |
| FAQ + COMMERCIAL PROGRESSION | **PASS** |
| REAL F1 WILD BURST | **PASS** |
| UNKNOWN BLOCKER/HIGH PATHS | **0** |
| NEW LAUNCH FAILURES | **0** |

L1/L2/L3 FROZEN · L4 ACTIVE · Wild clean count **0/3**.

Next: **CONTROLLED OWNER WILD** — on your authorisation, not automatically.
