# Booking Flow — publication and rollback runbook (PREPARED, NOT EXECUTED)

**No Meta contact has occurred. Nothing below has been run.** Supersedes the
`TARGET_v7.5` runbook of the same date, which proposed a candidate-derived artifact.

| | |
|---|---|
| Artifact to publish | `meta/flows/booking/TARGET_v7.6-minimal-refusal.json` |
| sha256 | `a25de8f43f5ada03650541cd84fa42cb4a2a5b2d772d89ac520afcd3219c43b4` |
| Flow asset ID | `28104222025943520` (`WHATSAPP_BOOKING_FLOW_ID`) — a new VERSION of the same asset |
| Baseline / rollback artifact | `meta/flows/booking/PUBLISHED_28104222025943520_v7.3.json` |
| baseline sha256 | `274038ba234a49fa4f99bae47c00f6cac640e248d56a373d40ae8fba6c1afb15` |
| Derived from | the **published baseline**, by `meta/flows/booking/make_minimal_target.py` |
| Excluded from deployment | `CANDIDATE_v7.4-prefill*.json` (unpublished prefill rollout, deferred) |
| Known-wrong artifact | the candidate-derived `TARGET_v7.5`, **not in this repository** — preserved for audit at `/opt/ridecheck-builds/crm-wild1-bfgate-20261029T121535Z/superseded/`, sha256 `6b9da137ddd1cf21…` |

The target is generated from the published file by a committed script. Run it again and the
output is byte-identical; the delta below is exactly what that script does.

## The published-to-target delta, in full

Measured by `meta/flows/booking/compare_flows.py`, not described from memory.

**APPOINTMENT — four declared fields added, nothing changed, nothing removed:**

```
+ slot_conflict_message : string    bound by a new TextBody, visible when has_refusal
+ has_refusal           : boolean
+ error_message         : string    bound by a new TextBody, visible when has_error
+ has_error             : boolean
```

**Two components added, both to APPOINTMENT, both new:**

```
+ {"type": "TextBody", "text": "${data.slot_conflict_message}", "visible": "${data.has_refusal}"}
+ {"type": "TextBody", "text": "${data.error_message}",         "visible": "${data.has_error}"}
```

**Two routing edges added:**

```
  DETAILS : ["SUMMARY"]  ->  ["SUMMARY", "APPOINTMENT"]
  SUMMARY : []           ->  ["APPOINTMENT"]
```

**Everything else is byte-identical to the published artifact.** DETAILS and SUMMARY declare
exactly what they declare today. No existing component, label, name, `required`, `enabled`,
`data-source`, navigate payload or data_exchange payload changes. `version` stays `"7.3"`
and `data_api_version` stays `"3.0"` — those are the Flow JSON schema and Data API
versions, not the asset revision.

Asserted by `CONTRACT-08c`, which fails if the target alters or removes any published
declaration or component and if the additions are anything other than those two TextBody
components.

### What is NOT in this target, and why

The superseded `TARGET_v7.5` was derived from `CANDIDATE_v7.4-prefill-r3.json` and so
carried an unpublished prefill rollout into a launch correction: **nine** new APPOINTMENT
fields rather than four, three new DETAILS fields, a rewritten APPOINTMENT navigate payload
and a rewritten DETAILS data_exchange payload — and it **deleted the `phone` TextInput from
DETAILS**, which would have stopped asking live customers for a phone number. None of that
is needed to render a refusal.

The five candidate keys (`selected_date`, `selected_time`, `customer_name`, `contact_phone`,
`contact_phone_display`) are also removed from the backend payload, because they are inert
against the published Flow and always were:

* v7.3's two Dropdowns declare no `init-value`, so no value the backend sent could
  preselect either one. The service's own comment said so while the payload kept sending
  them.
* v7.3's APPOINTMENT→DETAILS navigate payload carries none of the three identity keys, and
  v7.3's DETAILS declares none of them, so nothing downstream could read them.
* The phone an operator calls is `ctx.contact.wa_id`, re-asserted server-side in
  `_confirm_booking` whatever any screen shows. That guarantee is untouched.

They are named in `flow_contract.DEFERRED_PREFILL_KEYS` and asserted absent by
`CONTRACT-08b`, so their removal is a declaration rather than a silent omission. The
candidate artifacts and their Gate B certification tests stay in the repository for the
deferred prefill milestone.

## Why publication is required

Two independent reasons, both measured against the published artifact.

**1 — the routing model forbids the transition every refusal makes.** All four
`_refusal_screen` call sites and the `BookingTokenError` screen return
`screen: "APPOINTMENT"`, and all of them are raised from `_confirm_booking` or
`handle_prepare_summary` — reached while the customer is on SUMMARY or DETAILS. Published
v7.3 declares `{"APPOINTMENT": ["DETAILS"], "DETAILS": ["SUMMARY"], "SUMMARY": []}`.
Neither screen may reach APPOINTMENT, so the response names a transition the Flow forbids.
This is what the handset reported as "something went wrong", ten times.

**2 — APPOINTMENT binds no component capable of showing a refusal.** Even a delivered
refusal would be silent.

No backend change can fix either one. There is no payload that both validates and tells the
customer why nothing was booked.

## Order of operations — conservative, backend first

Backend before Flow, and Smart Booking stays OFF until both halves are verified. Earlier
drafts of this runbook claimed Flow-before-backend was equally safe; that claim rested on
an assumption about how Meta treats a declared field the backend omits, which has not been
verified, so it is withdrawn.

| # | Step | Gate before proceeding |
|---|---|---|
| 1 | Commit and build the corrected backend | owner authorisation; 17 paths, named in the closeout |
| 2 | Deploy **only** the backend, seven flags remaining `false` | image id recorded; container restart count 0 |
| 3 | Prove Smart Booking OFF produces no offer and no Flow | zero new `whatsapp_booking_offers`; zero new `message_type='flow'`; the WhatsApp handoff text sent instead |
| 4 | Import `TARGET_v7.6` into Meta Flow Builder — **do not publish** | the file's sha256 matches the table above |
| 5 | Let Meta's validator run against the exact artifact | zero validation errors |
| 6 | Preview INIT, the refusal state and the error state | the Spanish strings render; the two TextBody components appear only with their flag set |
| 7 | Publish | steps 5 and 6 clean |
| 8 | Bounded owner-present handset verification | see below |
| 9 | Enable Smart Booking **only inside** that bounded window | step 8 passing |

Until both the backend and the Flow are verified, **Smart Booking remains OFF**. With it
off the Flow is never dispatched, so no customer can reach a screen that cannot render.

Step 5 is the proving step for the one construct that could not be verified here:
`visible` bound to a `${data.*}` boolean on a `TextBody` in Flow JSON 7.3. If Meta rejects
it, the fallback is a dedicated refusal screen reached by the same two routing edges, with
its own text field and no conditional visibility; re-derive the hash, re-run the contract
suite, and return to step 4. Do not assume an omitted declared field renders safely —
nothing here has demonstrated that, and step 6 is where it would be seen.

## Rollback

### Flow — the mechanism must be confirmed in the interface, not assumed

**Unverified:** whether Meta Flow Builder offers "revert to a previous published version"
for this asset, and whether a prior version remains selectable after a new one is
published. No Meta interface or API was contacted in this milestone, so this runbook does
not depend on that capability. **Check it at step 4, while you are already in the Builder,
and record what the interface actually offers.**

**The mechanism that depends on nothing new:** re-import
`PUBLISHED_28104222025943520_v7.3.json` (sha256 `274038ba234a49fa…`) and publish it as a
new version of asset `28104222025943520`. This uses only the import-and-publish capability
that steps 4–7 already exercise. The artifact is byte-identical in the repository and
asserted by `test_flow_07_the_published_baseline_is_byte_identical`.

If both fail — the asset cannot be published to at all — the remaining route is a new Flow
asset plus `WHATSAPP_BOOKING_FLOW_ID` repointed in `.env`, which is a configuration change
and a redeploy. Record it as the last resort; it has not been rehearsed.

### Backend

Redeploy the previous image. The corrections are code-only: no migration, no schema change,
no data change, `alembic` head untouched. Rollback is a container replacement.

### The fastest kill, and the one fully in our control

`SMART_BOOKING_CE_ENABLED=false` removes the Booking Flow from the customer journey
entirely — WILD1-F1's boundary refuses the dispatch before a token or an offer exists, so
no customer reaches any Flow screen and the WhatsApp handoff text is sent instead. This
needs no Meta action and is the first move if anything is wrong after step 9.

### Ordering

The backend rolls back independently of the Flow. Rolling the Flow back to v7.3 while the
corrected backend is live returns the system to exactly today's refusal behaviour — a
generic error on refusal — which is why step 2 before step 7 is acceptable and the reverse
is not proposed.

## After publication — bounded handset verification (step 8)

Contract validation proves the payload is *acceptable*. It does not prove the screen
*renders*. Required, owner-present, Smart Booking ON only inside the window:

1. A genuine slot conflict (two sessions racing one slot) shows the Spanish refusal text,
   not Meta's generic error.
2. An expired session shows the expiry text.
3. With Smart Booking OFF, a scheduling request produces a WhatsApp text and **no Flow** —
   zero `whatsapp_booking_offers` rows, zero `message_type='flow'` rows.

Neither an HTTP 200 from our own endpoint nor a screenshot of a success path is evidence
for (1) or (2). (3) is step 3's proof repeated after publication, to show the boundary did
not move.
