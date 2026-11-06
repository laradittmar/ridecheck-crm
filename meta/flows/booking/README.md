# Meta Booking Flow — version control

The authoritative Flow definition lives on Meta and is **not** otherwise under version
control. That gap is why a defect in it can only be discussed from memory. These files close
it.

## CORRECTION — 2026-10-30. Which file is live.

This table said two things that cannot both be true: that
`PUBLISHED_28104222025943520_v7.3.json` was "a byte-exact copy of what is PUBLISHED right
now", and, two rows later, that r1 was "PUBLISHED live on 2026-09-16". Both rows were
written on 2026-09-11 and 2026-09-16 and neither was corrected when the other was added.

The contradiction was not harmless. On 2026-10-29 the WILD1 booking-flow correction derived
its target from the v7.3 row, concluded that five APPOINTMENT fields the backend was sending
were an unpublished candidate's init-values, and removed them. That target
(`TARGET_v7.6-minimal-refusal.json`) would have withdrawn a published, handset-certified
prefill feature and re-added a customer telephone input that live customers stopped seeing
on 2026-09-16.

**What is live is r3.** Two independent pieces of evidence, both stronger than this file:

* the 2026-09-16 Gate B Final Certification (R2) downloaded the live `flow.json` from the
  Flows API `assets` edge, canonicalised it to
  `5c4c68d10d0471eec8d439ee71324d20515ef1128397604719360feaa971c316`, and proved
  `live == r3` by deep structural equality — not by hash alone. Its own residual MED-01
  records that "after the R3 publish, R1's live definition is no longer recoverable";
* on 2026-10-30 the owner opened asset `28104222025943520` in Meta, copied the complete
  published JSON, and confirmed r3's structure field by field: twelve APPOINTMENT
  declarations, eight values on the APPOINTMENT → DETAILS navigate payload, DETAILS
  rendering `${data.contact_phone_display}` under "Te contactamos a este WhatsApp:", no
  telephone input of the customer's own, and `prepare_summary` sending
  `"phone": "${data.contact_phone}"`.

Nothing below is deleted or rewritten, and the superseded artifacts stay where they are.
What changes is the `meaning` column.

| file | meaning |
|---|---|
| `PUBLISHED_28104222025943520_v7.3.json` | **The preserved PRE-16-SEPTEMBER baseline.** What was published until 2026-09-16; **not live**. Never edit. Fetched read-only from the Flows API on 2026-09-11. It asks the customer for their own telephone number and declares none of the five prefill fields — the two structural facts that distinguish it from what is live. Publishing it would withdraw the prefill feature. |
| `CANDIDATE_v7.4-prefill.json` | **REJECTED by Meta on 2026-09-16.** Kept as forensic evidence. Never upload. |
| `CANDIDATE_v7.4-prefill-r1.json` | v7.4 minus the three `init-value` properties. **Ejecutar PASS, zero errors**, and **published live on 2026-09-16**, then superseded by r3 the same day. Rendered correctly on the handset except the DETAILS phone caption. Not live. |
| `CANDIDATE_v7.4-prefill-r2.json` | r1 with `is_time_enabled.__example__` set to `true` so the static preview can be walked. Runtime behaviour identical to r1. Superseded by r3; never uploaded. |
| `CANDIDATE_v7.4-prefill-r3.json` | **The owner-confirmed CURRENTLY PUBLISHED structure.** r1 with the DETAILS phone caption split into a static label and a whole-property `${data.contact_phone_display}` on a `TextBody`. Published on 2026-09-16 and live since. The filename still says CANDIDATE because the certification record cites it by that name; the canonical fingerprint above is what settles its identity. **This is the rollback artifact and the baseline every new target is derived from.** |
| `TARGET_v7.6-minimal-refusal.json` | **SUPERSEDED AND META-INVALID. Never import.** Two independent reasons: derived from the wrong baseline (v7.3, not the live r3), **and** its `routing_model` is cyclic — `DETAILS: [SUMMARY, APPOINTMENT]`, `SUMMARY: [APPOINTMENT]`, canonical `501d4aab43502730…` — the exact shape Meta rejected on 2026-10-30. `scripts/validate_flow_json.py` exits 1 on it. Preserved as historical evidence only; it is not a publication target and not a rollback point. It was produced by `make_minimal_target.py`, which is now neutralised. |
| `TARGET_v7.7-r3-prefill-refusal.json` | **PUBLISHED 2026-10-30**, owner-present, Meta validation clean (zero errors, zero warnings). r3 plus exactly four APPOINTMENT fields and two conditional `TextBody` components — **`routing_model` identical to r3's**, nothing removed, renamed, reordered or retyped. Raw sha256 `4ce27122a55f3efd…`, canonical `dccd3bc973b1b515…`. Generated deterministically by `make_r3_refusal_target.py`, which pins r3 by canonical fingerprint and refuses any other source. **This file used to add two backward routing edges and Meta rejected that artifact** (`98981193109ce426…` / `53dfe8fef7ca66c4…`, never to be republished) — see the correction note at the foot of this file. |
| `CANDIDATE_v7.8-vehicle-identity-prefill.json` | **CANDIDATE. NOT PUBLISHED, never validated by Meta.** The published v7.7 plus `Kilometraje` and `Dominio / patente` on DETAILS, both carried to `confirm_booking`, and `details_prefill` bound to the DETAILS Form's `init-values`, so a customer who lost a slot does not retype anything. `routing_model` identical. Raw sha256 `2102a8ec5f01be41…`, canonical `3b8dc55eef53e260…`. Carries three properties no Meta-accepted artifact has: Form `init-values`, TextInput `pattern`, TextInput `helper-text`. Meta's documentation supports all three, but only Ejecutar and a handset can prove them. |
| `make_v78_vehicle_prefill_candidate.py` | The v7.8 generator. Reads v7.7, verifies its canonical fingerprint, and exits non-zero without writing if it is given anything else. |
| `make_minimal_target.py` | **NEUTRALISED 2026-11-01 (WILD1-R3-C1).** It was the v7.6 generator and it exited 0 while writing a Meta-invalid cyclic `routing_model`. It now writes nothing, emits no JSON and **exits 2 for every invocation**, with a message naming the authoritative artifact and generator. The original is preserved in Git history (blob `8b93343d835d0998…`, commit `c26f980`); the working tree must not reproduce it. |
| `make_r3_refusal_target.py` | The v7.7 generator. Reads r3, verifies its canonical fingerprint, and exits non-zero without writing if it is given anything else. |
| `compare_flows.py` | Machine-readable n-way delta: every published-to-target claim in a closeout is generated by it. |
| `CANDIDATE_DIFF.md` | What v7.4 changed against the then-published asset, and why. |

Flow id `28104222025943520` ("RideCheck Booking"), json_version 7.3, data_api_version 3.0.
All five artifacts declare `version: "7.3"` internally — "v7.4"/"v7.6"/"v7.7" is a filename,
not an asset revision, and `version`/`data_api_version` are schema versions.

`CANDIDATE_v7.4-prefill-r3.json` is the rollback artifact: if publication goes wrong, this
is the definition to restore, **by hand** — Meta retains no prior Flow definition (MED-02),
so there is no version rollback to fall back on. Tests assert both it and the v7.3 baseline
stay byte-identical, so neither can drift silently.

**What 2026-09-16 actually established.** A Flow that parses and whose `${data.*}`
references all resolve can still be invalid — Meta checks which properties are allowed on
each component type, and nothing local had been checking that. The real defect in v7.4 was
three `init-value` properties: unsupported on `Dropdown` and on `TextInput`.

Owner Ejecutar results that day, which are the authority:

| asset | result |
|---|---|
| published v7.3 | PASS, zero errors |
| candidate v7.4 | FAIL — three `init-value` properties |
| candidate r1 | PASS, zero errors |

Meta also reported an error on the date Dropdown's `on-select-action` while those invalid
properties were present. A local rule was written from it, which then condemned the
published asset — a Flow working in production — and prompted a proposed backend-contract
milestone. The clean r1 run disproved it: r1 carries that exact action. **The action was
never the defect and no backend change is needed.** `scripts/validate_flow_json.py` no
longer contains that rule, and a test now asserts the published asset passes.

**What the handset then established, which Ejecutar could not.** r1 published clean and
reached the tester. Everything worked except one line: the DETAILS caption printed
`${data.contact_phone_display}` as literal characters. The backend was never at fault —
`_identity_fields()` supplies the masked value on every APPOINTMENT payload, the navigate
payload carries it, and DETAILS declares it. The one thing separating that line from the 39
references that did resolve is that it was the only property mixing a static sentence with a
dynamic reference:

```
"text": "Te contactamos a este WhatsApp: ${data.contact_phone_display}"   ← rendered literally
"text": "${data.vehicle_summary}"                                        ← resolved
```

Meta's Flow JSON reference says the static and dynamic variants of a property cannot be
combined, and its component reference lists `text` as dynamic on `TextCaption` as well as
`TextBody` — so the component type was never the problem, the concatenation was. Ejecutar
raised nothing. **A Meta-valid Flow can still be wrong on a handset**, so the validator now
reports this class as `RENDER` rather than `ERROR`: calling it an error would claim Ejecutar
rejects it, and it does not.

r3 — which is what is live — keeps the wording and splits it in two — a static `TextCaption` label, then the masked
value alone on a `TextBody` placed directly under `SingleColumnLayout`, which is exactly how
`vehicle_summary` and `location_summary` already render on APPOINTMENT. Dynamic text has
never been proven to render inside a `Form`, so r3 does not ask it to.

Run the validator before staging any candidate. Local PASS is still not Meta validation —
only Ejecutar decides.

Neither file contains a token, a signed URL or a key — a Flow definition carries none.

**What 2026-10-29/30 established.** A Flow target is only as good as the artifact it is
derived from, and "which file is published" is a claim that needs evidence, not a row in a
table. Three practices follow, and all three are now enforced by tests:

1. a generator pins its source baseline by canonical fingerprint and refuses to run against
   anything else (`make_r3_refusal_target.py`), so deriving from the wrong file is an error
   rather than a silent success;
2. the backend's screen contract (`backend/app/services/flow_contract.py`) names the live
   artifact, the preserved baseline and the superseded target separately, each with its own
   hash, so none of the three can stand in for another;
3. the structural facts that distinguish the baselines — a customer telephone input, and the
   masked-WhatsApp caption — are asserted directly, so identifying what is live never again
   depends on reading this file.

Byte comparison against anything Meta has served is meaningless: Meta reserialises Flow JSON
on download. Compare canonical forms —
`sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False))` — which
is reproducible on both sides, and which is what every fingerprint quoted above is.

---

## Owner kits — which one is safe (WILD1-R3-C1, 2026-11-01)

**Use:** `/opt/ridecheck-builds/crm-wild1-v77-ownerkit-corrected-20261101T203427Z` — start at its `OWNER_PROCEDURE.md`. Its verifier accepts the published
forward-only artifact by canonical fingerprint and rejects the cyclic one by name.

**Never use:** `/opt/ridecheck-builds/crm-wild1-v77-metapub-20261030T231602Z`. Every required hash in it describes the Meta-rejected cyclic Flow;
its verifier pinned `53dfe8fe…` as the publication target and would report the correct Flow
as a failure; and its `CHECKSUMS.sha256` certified the rejected copy, so it **passed its own
integrity check while being wrong**. It is preserved as evidence with its entry points
failing closed — see `00_SUPERSEDED_DO_NOT_USE.md` there. This was independent-review
finding **R-F01**.

**`TARGET_v7.6-minimal-refusal.json` is cyclic too** (`DETAILS -> [SUMMARY, APPOINTMENT]`,
`SUMMARY -> [APPOINTMENT]`, canonical `501d4aab…`), so it is inadmissible for the same
reason as the rejected v7.7 and not only because it came from the wrong baseline. Both are
now caught by `scripts/validate_flow_json.py`, which since 2026-11-01 calls the canonical
routing validator in `flow_contract.py`.

Repository validation, Meta validation and handset validation are three different things
and none substitutes for another. We have the first two. **F3a is OPEN.**

## Correction, 2026-10-30 — Meta forbids backward routing edges (WILD1-R3)

For a day and a half this directory held, and this file described, a publication target
that added two edges to `routing_model`:

```
DETAILS: ["SUMMARY", "APPOINTMENT"]
SUMMARY: ["APPOINTMENT"]
```

The reasoning was that a Data API response naming a screen is a navigation instruction, so
a refusal returning APPOINTMENT needed an edge to it. Four local certifications passed that
artifact. **Meta's validator rejected it on the first owner-present attempt**, with two
errors:

1. *"Backward route [DETAILS->APPOINTMENT] corresponding to forward route
   [APPOINTMENT->DETAILS] is not allowed in the routing model. Only forward routes can be
   specified."*
2. *"No entry screen found in the routing model. Expected a screen with no inbound edges as
   the entry screen."*

A backward edge is not something this platform has. `routing_model` is a forward-only DAG
with exactly one entry screen, and no artifact will ever declare one of those edges.

**What is published** is the same four fields and two components on r3's untouched routing
model. Meta accepted it with zero errors and zero warnings.

**Where the return to APPOINTMENT now lives:** in
`backend/app/services/flow_contract.py`, as `RECOVERY_RETURNS` — a narrow allowlist of
`(origin, trigger, typed outcome)` triples. The **screen** is always server-selected; the
`trigger` component of the key arrives in the request, so `origin` is a server-side function
of client input (R-F03) — which steers *which row* is consulted but cannot yield a different
screen, because every row resolves to APPOINTMENT. It is not an edge, it is not declared to Meta, it is not reachable by navigation,
and a client payload cannot request it.

**What is still unproven.** The 2026-10-29 Wild produced ten handset failures. The
explanation offered then — a missing edge — is now impossible, so the refusal screen is
UNPROVEN on a handset. Publishing the forward-only artifact closed the half of the defect
where the screen bound no message. It did not close the half where the response did not
arrive.

### Corrected 2026-11-01 (WILD1-R3-C1) — what those ten failures actually were

This section used to say the Wild "returned `SUMMARY → APPOINTMENT` ten times". The ledger
says otherwise, and the difference matters:

* **six** `booking_attempt_events` rows, all `PRE_EXECUTE / REFUSED /
  SMART_BOOKING_DISABLED` — the **OFF switch**, not a genuine availability conflict;
* **four** `data_exchange` requests with an **empty trigger**, re-raised as **HTTP 500**
  (WILD1-F4), returning no encrypted body and **naming no screen at all**.

So at most six observations bear on the question, **none of them a real slot conflict**.
Nor does Meta's clean validation help: during the publication session Meta sent this backend
**two `action=ping` requests and nothing else** (both `200 OK`), and `has_refusal` /
`has_error` carry `__example__: false`, so `Ejecutar` cannot draw the conditional components
even in principle. **Meta validation is not handset validation.**

Two hypotheses remain and they predict the same handset outcome:

| | hypothesis | status |
|---|---|---|
| **A** | Meta rejects a response naming a screen not forward-reachable from the current one | open |
| **E** | Meta rejects a response that returns from a **terminal** screen | open |

`SUMMARY` is the only `terminal: true` screen here. Both non-terminal origins worked
(APPOINTMENT self-refresh, DETAILS → SUMMARY); the terminal one failed 6/6. **E is the
runtime form of risk M2** in the 2026-10-29 publication-preparation closeout, which weighed
it only as a publication risk; M2 remains **open**.

If either holds, the smallest forward-only shape is a fourth screen —
`APPOINTMENT → DETAILS → SUMMARY → REFUSAL`, with `REFUSAL` terminal, since a route back to
APPOINTMENT is backward and forbidden. **That is a contingent option, not an authorised
change**, and nothing here proves it. The 2026-10-29 runbook's suggested fallback, a refusal
screen "reached by the same two routing edges", is **impossible**. Returning to APPOINTMENT
is **not proven**: Meta accepted it as a static artifact, and that is the whole of it.

**Two lessons for anyone deriving a Flow target here.**

* A local validator that passes is not evidence. `scripts/validate_flow_json.py` is a
  deny-list of errors Meta has already given us; it checked no routing property at all
  until this correction added `flow_contract.assert_forward_only`.
* A risk identified and not acted on is a risk taken. The 2026-10-29 publication-preparation
  closeout listed, as risk **M3**, *"a validator enforcing an acyclic routing graph rejects
  both edges"*. That is precisely what happened, a day later, after four more
  certifications of the same artifact.
