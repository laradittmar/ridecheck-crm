PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W1-F3-AUTHORITY-SURFACE-CLOSURE

# Fewer ways to act

crm_test only · OUTBOUND OFF · production business behaviour untouched · live n8n read-only
C2 / C3B / C4 / C4A ON · F2 fuzzy invariants preserved · C5 not started

---

## 1. Verdict

**CONDITIONAL_PASS.** The two BLOCKER duplicate-authority paths are closed and held by
repo-wide invariants. The condition is not a defect I introduced — it is a **STOP
condition the milestone itself defines** (Phase 11): the live n8n workflow diverges from
the tracked export and still carries the legacy AI pipeline. I did not modify it.

**READY FOR NEXT OWNER WILD: NO.** Three finite blockers, listed in §10.

## 2. Evidence provenance — read this first

The milestone names two independent source audits as input. **Neither report is on this
host.** `/opt/ridecheck-independent-audit/` contains only the sanitized snapshot I built
*for* them. I therefore could not reconcile two audits; I built the inventory from
executable source, which this milestone makes authoritative anyway ("do not treat docs as
proof when source differs").

Where my measurements differ from the counts in the brief, **my counts are from source and
are reproducible**; the brief's are from reports I cannot see. I have not restated the
brief's numbers as if I had verified them.

## 3. Unified path inventory (Phase 1)

### Outbound senders — customer-facing

| PATH_ID | ENTRY | GATE | AUTH | DISPOSITION |
|---|---|---|---|---|
| OUT-01 | `CE._send_text_to_wa` (CE_TEXT/FLOW/INTERACTIVE/LIST) | yes | n8n internal | KEEP_CANONICAL |
| OUT-02 | `POST /api/whatsapp/thread/{id}/send-text` | yes, MANUAL_CRM | **none** | KEEP_AUTHORIZED_EXCEPTION (auth gap, §10) |
| OUT-03 | `POST /api/whatsapp/thread/{id}/send-interactive` | yes | none | KEEP_AUTHORIZED_EXCEPTION |
| OUT-04 | `POST /api/whatsapp/thread/{id}/send-list` | yes | none | KEEP_AUTHORIZED_EXCEPTION |
| OUT-05 | `POST /api/whatsapp/thread/{id}/send-flow` | yes | none | KEEP_AUTHORIZED_EXCEPTION |
| **OUT-06** | **`POST /whatsapp/thread/{id}/send` (CRM UI)** | **was NO** | session | **ROUTE_THROUGH_GATE — closed** |
| OUT-07 | system follow-ups (unanswered / quote / buscando) | yes | job | KEEP_AUTHORIZED_EXCEPTION |
| OUT-08 | Booking Flow confirmation reply | yes | token | KEEP_AUTHORIZED_EXCEPTION |

**Measured: exactly one ungated sender existed (OUT-06). It is closed.**
`OutboundSafetyGate` is now the only constructor of a `direction="out"` record repo-wide —
asserted by AST over docstring-stripped executable code, so a comment cannot satisfy it.

### Canonical identity writers

| | measured |
|---|---|
| direct `marca/modelo/tipo_vehiculo/anio/zone_*` assignment outside the C2 chokepoints | **1 site**: `ui_revision_latest_update` — authenticated human CRM edit of a `Revision`, allowed exception D |
| automated vehicle write bypasses | **0** |
| automated inspection-location write bypasses | **0** |

`_create_candidate_from_catalog` constructs a candidate from a **catalog-confirmed**
`VehicleMatch`; since F2 the fuzzy route into it must first pass
`reconcile_vehicle_identity`.

### Acceptance writers

| site | governed by |
|---|---|
| `_process_flow_response` | transactional Booking Flow (exception B) |
| `_handle_quoted_acceptance` | `_authorize_acceptance` (C3B) |
| `_handle_scheduling_escalation` | `_progression_allowed` (C3B) |
| `_process_text` scheduling branches ×3 | `_progression_allowed` (C3B) |
| **`_process_text` AI `lead_flag`** | **was NOTHING — closed** |

## 4. The two BLOCKERs, closed

**Phase 9 — the parallel outbound authority.** `POST /whatsapp/thread/{id}/send` built its
own outbound record and called Meta directly: no `path_id`, no 10-minute dedup, no
3-per-60s flood gate, no `deployment_id`/`correlation_id`. An operator's message was
invisible to the forensic ledger that every M2 invariant depends on. It is now a gate
client attributed `MANUAL_CRM`. It kept working through the kill switch before only
because the low-level sender raises `OutboundBlockedError` — one guard out of five.

**Phase 5 — the AI could grant acceptance.** `lead.flag = new_flag` applied whatever the
model returned, checked only against `_ALLOWED_FLAGS`. `PRESUPUESTO_ENVIADO` had a
deterministic-price guard; **`ACEPTADO` had none.** A model emitting
`lead_flag="ACEPTADO"` advanced commercial state with no quote, no delivery proof and no
customer acceptance. It now passes the same C3B authorizer as every other acceptance.

**A correction I made mid-milestone, worth recording.** My first version also blocked
AI-proposed acceptance when the authority flag is OFF, using the strict `_is_acceptance`
predicate. That broke **10 certified tests** — genuine acceptance-plus-FAQ turns like
"Sí, dale. ¿Qué horarios tienen?" are not acceptance *throughout*, so the strict predicate
rejected them. That is a product regression, not a safety gain. The guard now follows the
same flag discipline as every other cutover: flag OFF, legacy unchanged; flag ON, governed.

## 5. Phase 11 — live n8n parity **FAILS**, and this is the STOP

Exported read-only from the running container:

```
WORKFLOW ID   DaFqDIzVi1f92Hvz
NAME          CRM - Ridecheck (Mar 5 at 08:59:04)
ACTIVE        True
NODE COUNT    164          ← tracked export "(6).json" has 112
NODES SHA256  0f37b16b0fd5cc8bec05b2180ccc850221260d1dc060e732044749321df8ae6b
CALLS CE      True  (/api/conversation/handle)
```

**The live workflow is not transport-only.** It contains 11 AI-pipeline nodes
(`AI Router`, `AI Reply Planner`, three `OpenAI Chat Model`) and 35 state-writing HTTP
nodes. Exact runtime path of the legacy senders:

```
IF - Engine Handled? (M18) --[false]--> … --> Parse Final Answer
    --> IF - Has Reply Text? --> Debug Final Reply --> Reply Gate --> Send Whatsapp Reply
```

They fire only when CE returns `handled=false`. `CLAUDE.md` states this "never fires in
production" — **that is a claim about frequency, not about existence**, and the path is
live. Per Phase 11 I stopped rather than deleting anything.

One mitigation worth stating: every live sender posts to
`/api/whatsapp/thread/{id}/send-text`, which **is** gated. So the legacy pipeline cannot
send ungated — but its automated sends are attributed **`MANUAL_CRM`**, mislabelling
machine traffic as human in the forensic ledger. HIGH, not BLOCKER.

The live workflow is now committed as `N8N workflows/RUNTIME_LIVE_EXPORT_2026-09-04.json`
for provenance, with the Maps key redacted before commit (SEC-01 discipline).

## 6. Phase 10 — REST exposure, classified and reported

`_is_protected_path` covers `/kanban /table /calendar /profesionales /agencias /whatsapp
/integrations/whatsapp /ui/ /control`. It does **not** cover `/api/`. Since `8000` is
published on `0.0.0.0` and nginx proxies `crm.ridecheck.ar` to it, every `/api/*`
endpoint is reachable unauthenticated, including gated senders, candidate and thread-state
mutation, `/api/conversation/handle` and `/api/settings/ai-enabled`.

I have **not** changed this. Requiring auth on `/api/*` would break the live n8n transport
in the same breath, and getting that wrong silently drops customer messages. It is a
finite, named blocker for the owner (§10) rather than something to guess at.

## 7. Phase 17 — path count

**CURRENT AUTHORITY SURFACES: 8 outbound senders + 5 candidate constructors + 7 acceptance
sites + 1 booking writer = 21 measured surfaces.**

**FINAL LEGITIMATE ARCHITECTURAL PATHS: 6.**

1. **CE automated conversation** — evidence → reconciliation → authorization → canonical
   write → composition → validator → gate. The only path that may interpret a customer.
2. **CE Flow dispatch** — same pipeline, interactive payload, `CE_FLOW`.
3. **Manual CRM human send** — authenticated operator, `MANUAL_CRM`, gated, ledgered.
4. **Booking Flow confirmation** — token-validated transactional service; the only writer
   of `ThreadRevision(status="booked")`.
5. **System follow-up** — deterministic job, gated, `SYSTEM_NOTIFICATION`.
6. **Human CRM state change** — authenticated operator editing a Revision.

Everything else is evidence, a validator, or retired.

## 8. Phase 7 — FAQ coexistence, honestly incomplete

F2 removed the early return, so composition is now reached — the structural precondition.
The **general invariant is not yet implemented**: semantic `faq_intents` (which correctly
detected `service_scope` and `payment` on the real burst) are still not wired to the
deterministic answer constants, and the phrase detectors still miss all four questions.

I did not phrase-patch it, and I did not implement the wiring either — it needs the same
producer/authority split as C4A (semantic identifies *what* is asked; deterministic
business truth supplies the answer), which is a cutover of its own. **FAQ + BUSINESS
COEXISTENCE: FAIL**, carried as a named blocker rather than reported as done.

## 9. Tests and regression

`tests/test_l4_7w1_f3_authority_surface.py` — **9/9**: AUTH-01 no outbound record outside
the gate (repo-wide AST), AUTH-02 UI sender gated and attributed, AUTH-03 every
`gate.attempt` declares a `path_id`, AUTH-04/05 canonical identity writers, AUTH-06 the AI
cannot grant acceptance, AUTH-07 every ACEPTADO site classified, AUTH-08 F2 invariants
preserved, AUTH-09 one booking writer, AUTH-10 live n8n provenance recorded.

Real-path coverage is carried by the F2 suite (21/21, entering through `_process_text`,
including the exact F1 Wild burst). The remaining Phase-14 scenarios are not all covered;
see §10.

Full regression: **3 600 passed / 57 failed / 9 errors**, failure set identical to the F2
baseline, **0 new**.

Runtime: `ridecheck-crm-backend:l4.7w1f3-authority-eeec87d`, restarts 0, parity MATCH,
`outbound=False` in the boot line, crm_test rows unchanged (4 / 0).

**One operational note.** The first deploy of this image crash-looped: SEC hardening made
`docker-compose.beta.yml` interpolate `${POSTGRES_PASSWORD}`, and I redeployed without
supplying it, so the backend started with an empty password. Exactly the coupling the SEC
closeout predicted, hit by me. Fixed by supplying the existing value — **nothing was
rotated**. It is a real preview of what will happen on the next production deploy if
`.env` is not populated first.

## 10. Pre-Wild gate (Phase 15)

| gate line | result |
|---|---|
| PRE-RECONCILIATION FUZZY SENDS | **0** |
| AUTOMATED VEHICLE WRITE BYPASSES | **0** |
| AUTOMATED INSPECTION-LOCATION WRITE BYPASSES | **0** |
| AUTOMATED ACCEPTANCE AUTHORITY BYPASSES | **0** |
| AUTOMATED SCHEDULING-PREFERENCE BYPASSES | **0** |
| CUSTOMER-FACING OUTBOUND WITHOUT GATE | **0** |
| AI DIRECT ACEPTADO BYPASS | **0** |
| WEBSITE REGEX COMMERCIAL AUTHORITY BYPASS | `_handle_website_form` sets `PRESUPUESTO_ENVIADO`; **not closed** |
| FAQ semantic-topic → deterministic-answer composition | **FAIL** |
| LIVE N8N PATH KNOWN | **YES** |
| LIVE N8N → CE PATH PROVEN | **YES** |
| UNKNOWN BLOCKER/HIGH CUSTOMER PATHS | **0** |

**Three finite blockers before another Wild:**

1. **Live n8n legacy AI pipeline** — 164 live nodes vs 112 tracked; AI Router / Reply
   Planner / three senders live on the `handled=false` branch. Owner decision: prune to
   transport-only, or accept and re-export. *(Phase 11 STOP; not modified.)*
2. **`/api/*` unauthenticated** — gated senders and canonical mutators publicly reachable.
   Needs a machine-auth scheme for n8n→backend before locking, or the transport dies.
3. **FAQ coexistence** — semantic topic → deterministic answer wiring not implemented.

Website-form disposition is **ROUTE_THROUGH_AUTHORIZER**, scoped but not executed.

L1/L2/L3 FROZEN · L4 ACTIVE · Wild clean count **0/3** · C5 not started.
