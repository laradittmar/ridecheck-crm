PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-WILD1-BOOKING-FLOW-R3-BASELINE-ALIGNMENT-CORRECTION

---

## CORRECTION — 2026-10-30, documentation only. The MF-07 mechanism as first written was wrong.

The independent clean-room review of this change set returned **CONCLUSION A — commit-ready as
scoped**, requiring no code correction and one documentation correction, which is this notice
and the rewritten §9.6.

**What this closeout got wrong.** §9.6 and §0 originally said the expired-token continuation is
refused because `handle_prepare_summary` *"requires a non-empty `phone` from the contact and
raises `BookingTokenError` first"*. That sentence conflates two different guards and is wrong
about both:

* `BookingTokenError` is raised by `resolve_context(booking_token)`, the **first** statement in
  `handle_prepare_summary` — not by the phone check.
* The `if not phone` check is the **last** of four validations, after the token, after the
  date/time shapes and after the name. It is therefore never the first reason for the refusal,
  and on the traced route it is never reached at all.

The empty phone is a *consequence* of the failed token, not the thing that refuses the
continuation. §9.6 below now records the route the review traced, re-verified here against
`booking_flow_service.py` and `flow_data_exchange.py` line by line. Nothing else in this
document is amended; no application, Flow, generator or test file was touched by this
correction.

---

## 0. Verdict

One aligned correction is prepared, against the Flow that is actually published.

* The new target is `meta/flows/booking/TARGET_v7.7-r3-prefill-refusal.json`, derived
  deterministically from `CANDIDATE_v7.4-prefill-r3.json`. Its generator pins r3 by canonical
  fingerprint and **refuses to run against any other artifact**, which is R1's mistake turned
  into an error rather than a silent success.
* The r3 → v7.7 delta is exactly **four APPOINTMENT fields, two conditional `TextBody`
  components and two routing edges**. Nothing is removed, renamed, reordered, retyped or
  re-ordered anywhere; DETAILS and SUMMARY are structurally identical to the published
  screens, byte for byte once sorted.
* The backend supplies **exactly 16 APPOINTMENT keys** — r3's published twelve plus the four
  this correction adds — and declared equals supplied in both directions with no exclusion
  set. The five values R1 removed are supplied again, from authoritative server state.
* 20 of 20 mutants killed, 0 survived.
* Full suite, four runs (candidate and the pristine `c26f9802` baseline, each on SQLite and on
  a disposable PostgreSQL 16.13): **failure identity sets identical, no NEW, no GONE**.

**Nothing was published, imported, validated through Meta, committed, pushed, tagged,
stashed, built, deployed or migrated. No flag changed. No provider was contacted. The live
`crm_test` database has had zero row changes since 2026-10-29.** The proposed commit is
written out in §12 and not executed.

Clean-Wild counter: **0/3**, unchanged.

**One finding to carry forward (MF-07, MEDIUM here, §9.6):** the expired-session screen
supplies the five prefill values as empty strings, because the token that names the contact is
the thing that just failed. On the published Flow that means a customer who taps *Continuar*
from the error screen reaches a DETAILS screen whose "Te contactamos a este WhatsApp:" caption
has nothing under it. This is the pre-existing published-era behaviour, not a regression, and
it is reachable mid-journey only through the new `DETAILS → APPOINTMENT` edge. It is named
rather than fixed: fixing it means changing what the error screen does, which is outside this
authorisation.

**Continuing from the error screen is not a recovery path**, and this closeout's first
description of *why* it is refused was wrong — see the correction notice above and the rewritten
§9.6. It is held at **MEDIUM** here, conservatively; the independent review classified its
**runtime impact as LOW** after tracing the whole route and finding no row written, no booking
created or changed, no wrong identity used, no outbound message and no loop without a further
explicit customer tap.

---

## 1. Why this milestone exists: the baseline was wrong, twice recorded

`meta/flows/booking/README.md` described `PUBLISHED_28104222025943520_v7.3.json` as "a
byte-exact copy of what is PUBLISHED right now" and, two rows later, recorded that r1 was
"PUBLISHED live on 2026-09-16". Both rows were written in September and neither was corrected
when the other was added.

On 2026-10-29 the WILD1 booking-flow correction (R1) read the first row, concluded that five
APPOINTMENT keys the backend was sending belonged to an unpublished candidate, removed them
from the payload, and derived `TARGET_v7.6-minimal-refusal.json` from v7.3.

The owner's 2026-10-30 export of the published JSON for asset `28104222025943520` settles it:
the live definition is r3's structure. That agrees with the 2026-09-16 Gate B Final
Certification, which downloaded the live `flow.json` from the Flows API `assets` edge,
canonicalised it to `5c4c68d1…` and proved `live == r3` by deep structural equality.

### 1.1 Owner export vs. the committed artifact, field by field

Transcribed from the owner's message and asserted against the artifact by
`TestBaseline::test_r2_b02_the_artifact_matches_the_owner_export_field_by_field` — the one
test in the suite whose expected values come from outside the repository.

| owner's export says | r3 artifact | verdict |
|---|---|---|
| APPOINTMENT declares 12 fields, in order `booking_token, vehicle_summary, location_summary, date, is_date_enabled, time, is_time_enabled, selected_date, selected_time, customer_name, contact_phone, contact_phone_display` | identical list, identical order | MATCH |
| APPOINTMENT → DETAILS carries 8 values (`booking_token, vehicle_summary, location_summary, date, time, customer_name, contact_phone, contact_phone_display`) | one `navigate` action, `next: DETAILS`, those 8 keys | MATCH |
| DETAILS shows "Te contactamos a este WhatsApp:" and `${data.contact_phone_display}` | `TextCaption` + `TextBody`, directly under `SingleColumnLayout` | MATCH |
| DETAILS asks the customer for no telephone number | `TextInput` names are `name, email, inspection_address, seller_name, seller_phone, listing_url` — no `phone` | MATCH |
| DETAILS `prepare_summary` sends `"phone": "${data.contact_phone}"` | exactly that | MATCH |
| routing `APPOINTMENT → DETAILS → SUMMARY`, SUMMARY terminal, no return edge | `{"APPOINTMENT":["DETAILS"],"DETAILS":["SUMMARY"],"SUMMARY":[]}`, `terminal: true` | MATCH |

No hash of the owner's export is claimed. No file containing that export exists in this
repository, and a fingerprint of text pasted into a chat message would be a fingerprint of my
own transcription. What is claimed is structural agreement with the artifact, item by item,
and that the artifact's canonical fingerprint is the one the Flows API download produced.

### 1.2 The diagnosis survives the baseline correction

v7.3 and r3 declare the **identical** routing model. The F3a root cause — a Data API response
naming a transition the Flow forbids — and the deployed `c26f9802` correction therefore stand
whichever of the two is live. Asserted by `CONTRACT-09b`.

What changes is the size of the published-to-target delta: **four keys, not nine**.

### 1.3 What publishing v7.6 over the live Flow would have done

Measured, not asserted (`P10_r3_to_v76_summary.txt`):

```
APPOINTMENT  fields_removed  contact_phone, contact_phone_display, customer_name,
                             selected_date, selected_time
APPOINTMENT  fields_added    error_message, has_error, has_refusal, slot_conflict_message
APPOINTMENT  components      −1 / +3,  actions CHANGED
DETAILS      fields_removed  contact_phone, contact_phone_display, customer_name
DETAILS      components      −3 / +2,  actions CHANGED
```

In customer terms: a required `Teléfono` input back on DETAILS, the masked-WhatsApp caption
deleted, and two action payloads reverted — the withdrawal of a published, handset-certified
feature. `CONTRACT-08d` now asserts that v7.6 is preserved on disk **and** is not the artifact
the backend points at, and measures the regression it would have shipped so the prohibition
cannot decay into a comment.

---

## 2. File list and diff

Prepared in `/opt/ridecheck-crm-integration`, HEAD `c26f9802ca93da0263d031e8096b3f67938d1914`,
branch `integration/prelaunch-booking-convergence`. **Nothing staged, nothing stashed, no tag.**

### 2.1 New files (untracked)

| file | bytes | sha256 |
|---|---|---|
| `meta/flows/booking/TARGET_v7.7-r3-prefill-refusal.json` | 12 322 | `98981193109ce426af1298dbfca5dd2f24cae2aee0a7af08b20dde56001bc36f` |
| `meta/flows/booking/make_r3_refusal_target.py` | 6 499 | `0db0c989f432e837e652bab404470f89c5df8f8834f155caf5b5640f2fc824c6` |
| `tests/test_wild1_r3_aligned_flow_contract.py` | 45 667 | `14332b9edf22aa7be3a0b029c2150d7d9a46b9ea570bc1cadb882ad2592d9ee7` |
| `docs/Launch/2026-10-30_…R3-BASELINE-ALIGNMENT-CORRECTION_CLOSEOUT_001.md` | this file | see `closeout_001.sha256` |

### 2.2 Modified files (tracked) — seven

```
 backend/app/routes/flow_data_exchange.py           |  10 +-
 backend/app/services/booking_flow_service.py       | 107 +++++++--
 backend/app/services/flow_contract.py              | 187 +++++++++++-----
 meta/flows/booking/README.md                       |  73 +++++-
 scripts/validate_flow_json.py                      |   8 +-
 tests/test_l4_7w5_gate_a_flow_prefill_contract.py  | 245 +++++++++++++--------
 tests/test_wild1_booking_flow_gate_and_contract.py | 205 ++++++++++++++---
 7 files changed, 621 insertions(+), 214 deletions(-)
```

| file | sha256 after |
|---|---|
| `backend/app/services/flow_contract.py` | `05e9c2efbf6fb4dfbab2ad29413d55d194c63b8709bf1431ceeeb20bbb28cd30` |
| `backend/app/services/booking_flow_service.py` | `d5b845b18d6dfde1b55c8c0bf4e5a15a6ab2d84a93fff425e5ffb21d97c3ef55` |
| `backend/app/routes/flow_data_exchange.py` | `0234b5af1ac678a0a956f6c88a79a97eabf13d84c9f3f710b14777188fb2ffad` |
| `tests/test_wild1_booking_flow_gate_and_contract.py` | `f1d2025afa09017b4759d38718787606c7ccb1b409b084abed11628065a68308` |
| `tests/test_l4_7w5_gate_a_flow_prefill_contract.py` | `c3f32ee8569e6610e7128ce9e2744437f488aa1300676ca3e833e0eeb1fb185d` |
| `meta/flows/booking/README.md` | `93e43b5ce1be75be302fed1bf09f9b9b5e506b7a8803524b7eeb1eeed9641fdd` |
| `scripts/validate_flow_json.py` | `40b74ad273e57e6d13380c941a50580423424b681d44a40c78470747a72829cc` |

`scripts/validate_flow_json.py` is a **docstring-only** correction: its rule list said its
precedent came from "the live PUBLISHED asset, which is the only Flow JSON we hold that Meta
has demonstrably accepted", which named v7.3 by implication. Both v7.3 and r3 passed Ejecutar
clean on 2026-09-16, so every rule stands unchanged and the 2026-09-16 Ejecutar table in that
file is left exactly as written; only the sentence that implied v7.3 is live is corrected, with
a pointer to the README. The two suites that scan that file's source for its honesty strings
(`GBR2-20`, `PB-22`) still pass — 42 passed across both.

Full patches: `R11_diff_tracked.patch` and `R11_diff_untracked.patch` in the evidence root.

### 2.3 Preserved and NOT touched

| file | sha256 | git |
|---|---|---|
| `PUBLISHED_28104222025943520_v7.3.json` | `274038ba234a49fa…` | UNMODIFIED |
| `CANDIDATE_v7.4-prefill-r3.json` | `53a3d88f3dd57c9b…` | UNMODIFIED |
| `TARGET_v7.6-minimal-refusal.json` | `a25de8f43f5ada03…` | UNMODIFIED |
| `make_minimal_target.py` | `126f2f91866c7208…` | UNMODIFIED |

`docs/operations/RECOVERY_MANIFEST.md` carries a `M` in `git status`. It was last written on
**2026-10-23 19:02:11 ART**, seven days before this milestone, and this milestone did not open
it. No Launch document other than this closeout was added, and nothing was staged.

### 2.4 What is NOT in the change set

`docker-compose*.yml`, `.env`, `alembic/`, `docs/operations/`, `conversation_engine.py`,
`offer_lifecycle.py`, `booking_authority.py`, `booking_dispatch_gate.py`, and every rule in
`scripts/validate_flow_json.py` (only its docstring changed). The `c26f9802` dispatch gate and the HTTP refusal/error
correction are untouched, and §6.5 asserts that by test rather than by assertion here.

---

## 3. Proof 1 — the baseline

### 3.1 The required fingerprint is reproduced

```
CANDIDATE_v7.4-prefill-r3.json
  raw sha256        53a3d88f3dd57c9bf3089c97c9edfe83bc907afc428e7ef19e3c1538efb1bc04
  canonical sha256  5c4c68d10d0471eec8d439ee71324d20515ef1128397604719360feaa971c316   ← REQUIRED, MATCH
```

Canonicalisation is
`sha256(json.dumps(doc, sort_keys=True, separators=(",",":"), ensure_ascii=False))` — the
method the 2026-09-16 certification used. Byte comparison against anything Meta has served is
meaningless, because Meta reserialises Flow JSON on download; the canonical form is
reproducible on both sides. All five repository artifacts were canonicalised in the same run,
and three of the five fingerprints the certification recorded came back to the character:

```
PUBLISHED_…v7.3.json          22cbb58a2d693e1c9fb9603013474dc6c5269e5e2163885521549dd3d5327e28
CANDIDATE_v7.4-prefill-r1     fc7effccccba51612d30b0dcadbee252a8f7ba76485f1ad17d1fbf06e0053416
CANDIDATE_v7.4-prefill-r2     426a958cd0e94e386ef69ed89dcd86fa7092fe9342fee0c972fbdb25b15ca370
CANDIDATE_v7.4-prefill-r3     5c4c68d10d0471eec8d439ee71324d20515ef1128397604719360feaa971c316  (LIVE)
CANDIDATE_v7.4-prefill        339764a1a71c36e85ef37e5fba1a77ca8389fce45e9274c4b0df892870f48477
TARGET_v7.6-minimal-refusal   501d4aab43502730a0b176f3fb9cc37173f85e18ed2ecf265ace39795c6745dc
TARGET_v7.7-r3-prefill-refusal 53dfe8fef7ca66c478421462a15e8992bb8839712e1fb7c8790f90c410e7f965
```

### 3.2 The generator reads r3, and nothing else

`make_r3_refusal_target.py` canonicalises its source **before writing anything** and exits 2
with a `STOP:` line if it is not r3. Measured against every other artifact in the directory:

```
PUBLISHED_28104222025943520_v7.3.json   exit=2, no output file written
CANDIDATE_v7.4-prefill-r1.json          exit=2, no output file written
CANDIDATE_v7.4-prefill-r2.json          exit=2, no output file written
CANDIDATE_v7.4-prefill.json             exit=2, no output file written
TARGET_v7.6-minimal-refusal.json        exit=2, no output file written
```

It also asserts the twelve declarations in order, the three leading component texts, and the
exact published routing model before it edits — so a baseline that canonicalises correctly but
has been locally reshaped still stops it.

### 3.3 Determinism

Run twice into fresh temporary files:

```
regen1  98981193109ce426af1298dbfca5dd2f24cae2aee0a7af08b20dde56001bc36f
regen2  98981193109ce426af1298dbfca5dd2f24cae2aee0a7af08b20dde56001bc36f
committed TARGET_v7.7  98981193109ce426af1298dbfca5dd2f24cae2aee0a7af08b20dde56001bc36f
BYTE-IDENTICAL, both times
```

Re-asserted inside the test suite by `R2-B03`, which shells out to the generator twice and
compares with `TARGET_FLOW_SHA256`. A hand edit to the artifact fails there.

---

## 4. Proof 2 — the exact semantic delta, r3 → v7.7

Machine-readable: `P2_r3_to_v77_delta.json`, produced by `meta/flows/booking/compare_flows.py`.
Every claim below is generated by it or asserted by `TestExactDelta` (R2-D01…D10).

```
routing_model
  DETAILS   ["SUMMARY"]  ->  ["SUMMARY", "APPOINTMENT"]
  SUMMARY   []           ->  ["APPOINTMENT"]
  APPOINTMENT                 unchanged  (["DETAILS"])

APPOINTMENT
  fields_added        error_message (string), has_error (boolean),
                      has_refusal (boolean), slot_conflict_message (string)
  fields_removed      none
  fields_retyped      none
  components_added    TextBody  text ${data.slot_conflict_message}  visible ${data.has_refusal}
                      TextBody  text ${data.error_message}          visible ${data.has_error}
  components_removed  none
  actions_changed     none
  terminal_changed    none

DETAILS    no delta of any kind
SUMMARY    no delta of any kind  (its routing edge lives in the top-level routing_model)
```

### 4.1 Component order

```
children[0]  TextHeading  "Reservá tu revisión"
children[1]  TextBody     ${data.slot_conflict_message}   visible ${data.has_refusal}   ← added
children[2]  TextBody     ${data.error_message}           visible ${data.has_error}     ← added
children[3]  TextBody     ${data.vehicle_summary}
children[4]  TextBody     ${data.location_summary}
children[5]  Form         appointment_form
```

Six children, asserted by index and by text. The refusal is the reason the customer is back on
this screen, so it is the first thing under the title and not something to scroll for.

### 4.2 Nothing else moved

| claim | how it is asserted |
|---|---|
| no screen added, removed or renamed | `R2-D06`: screen id list identical |
| no screen un-terminalled | `R2-D06`: `SUMMARY.terminal is True` in both |
| no existing component altered or removed | `R2-D02`: every published component body (children stripped) survives; exactly 2 added, 0 removed |
| no action or payload changed, anywhere | `R2-D05`: every action in every screen compared whole, including its payload |
| DETAILS and SUMMARY structurally identical | `R2-D07`: whole screen objects, `sort_keys=True`, string-equal |
| no customer phone input | `R2-D08`: `TextInput` names on every screen; DETAILS' list identical to r3's |
| all 8 navigate values preserved | `R2-D09`: each key's binding checked (`${form.*}` for date/time, `${data.*}` otherwise) and confirmed declared by DETAILS |
| no `init-value` introduced | `R2-D10`: zero occurrences; `version` 7.3, `data_api_version` 3.0 unchanged |

---

## 5. Proof 3 — the backend contract

`TARGET_FLOW_PATH` → `meta/flows/booking/TARGET_v7.7-r3-prefill-refusal.json`,
`TARGET_FLOW_SHA256` → `98981193109ce426af1298dbfca5dd2f24cae2aee0a7af08b20dde56001bc36f`.

`PUBLISHED_FLOW_PATH` now names r3, with both its raw and its canonical fingerprint.
`PRESERVED_BASELINE_PATH` (v7.3) and `SUPERSEDED_TARGET_PATH` (v7.6) are separate named
constants with their own hashes, so none of the three can stand in for another.
`DEFERRED_PREFILL_KEYS` is gone; `RESTORED_PREFILL_KEYS` replaces it, and `CONTRACT-08b`
asserts the old name does not come back.

### 5.1 APPOINTMENT — declared 16, supplied 16, no exclusion set

| key | type | in live r3 | class | absent-value | authoritative source |
|---|---|---|---|---|---|
| `booking_token` | string | yes | RENDERED | `""` | `make_booking_token(thread.id)` |
| `vehicle_summary` | string | yes | RENDERED | `""` | `ctx.vehicle_summary` |
| `location_summary` | string | yes | RENDERED | `""` | `ctx.location_summary` |
| `date` | array | yes | RENDERED | `[]` | `ScheduleService.list_slots`, 14-day horizon |
| `is_date_enabled` | boolean | yes | RENDERED | `false` | `bool(date)` |
| `time` | array | yes | RENDERED | `[]` | `ScheduleService` slots for the chosen day |
| `is_time_enabled` | boolean | yes | RENDERED | `false` | `bool(time)` |
| `selected_date` | string | yes | CARRIED | `""` | the request's value, **only if present in this payload's own `date`** |
| `selected_time` | string | yes | CARRIED | `""` | the agreed slot, **only if the schedule was just re-read as still free** |
| `customer_name` | string | yes | RENDERED | `""` | `_lead_display_name(ctx.lead)` |
| `contact_phone` | string | yes | RENDERED | `""` | digits of `ctx.contact.wa_id` |
| `contact_phone_display` | string | yes | RENDERED | `""` | `_mask_phone(contact_phone)` |
| `slot_conflict_message` | string | **NEW** | RENDERED | `""` | the refusal's own reason |
| `has_refusal` | boolean | **NEW** | RENDERED | `false` | `True` only where a message is set |
| `error_message` | string | **NEW** | RENDERED | `""` | the token-error sentence |
| `has_error` | boolean | **NEW** | RENDERED | `false` | `True` only on the token-error screen |

```
declared minus supplied : EMPTY
supplied minus declared : EMPTY
```

RENDERED means the layout binds the key with `${data.<key>}` — visibly in a component, or as
the next screen's input when the binding sits in an action payload. `customer_name`,
`contact_phone` and `contact_phone_display` are RENDERED on APPOINTMENT in that second sense:
none of the three is drawn on APPOINTMENT itself; they are DETAILS' input, and DETAILS is where
the mask is read. CARRIED means declared by the published Flow and bound by nothing in it —
`selected_date`/`selected_time` were the `init-value` plan, and no artifact declares an
`init-value`, so they preselect nothing. They stay in the contract because **the published Flow
declares them**, and a declared key the backend omits is a key the Flow renders as missing.

DETAILS: declared 8, contract 8 (7 rendered, `customer_name` carried).
SUMMARY: declared 12, contract 12, all rendered — unchanged from `c26f9802`.

### 5.2 The five restored values, and why each is safe

`_identity_fields` is restored and reads **only** server state — asserted by AST in `R2-S07`,
which scans the function's statements (not its docstring) and fails if `data.get`, `data[`,
`payload` or `form.` appears:

```python
wa_id = "".join(ch for ch in (ctx.contact.wa_id or "") if ch.isdigit())
return {
    "customer_name": _lead_display_name(ctx.lead),
    "contact_phone": wa_id,
    "contact_phone_display": _mask_phone(wa_id),
}
```

* **`contact_phone`** — digits of `ctx.contact.wa_id`, the identity WhatsApp proved. Digits-only
  is new (R1's parent sent `wa_id` verbatim): a formatted identity would otherwise reach a
  payload an operator's screen later reads. `R2-S01` sets `wa_id` to `"+54 9 11 0000-0001"` and
  asserts `"5491100000001"`.
* **`contact_phone_display`** — `_mask_phone` of that same value: last four digits only.
  `R2-S02` asserts the mask is a suffix of the full value and exactly four digits long.
  `R2-S03` proves `${data.contact_phone}` reaches **no visible component** in r3 or v7.7 — only
  action payloads bind it — so no complete number is drawn on a screen.
* **`customer_name`** — `_lead_display_name(ctx.lead)`: the operator-or-Flow-persisted name, or
  empty. A WhatsApp profile label is **not** elevated into a verified name. `R2-S05` asserts
  empty with no `nombre`, `"Juan Pérez"` with one, and that the profile label
  (`perfil_de_whatsapp`) appears nowhere in the payload.
* **`selected_date`** — the request's value only if the backend's own `date_items`, built from
  `ScheduleService` for this context's zone group, contains it. A day outside the horizon,
  a day with no slots, or a replayed day from another cycle collapses to `""`. `R2-S06` and
  `DATE-TIME-07` assert it with a day 400 days out.
* **`selected_time`** — never echoed from input at all. The only path that sets it is the
  agreed-slot narrowing branch, which re-reads the schedule and keeps the slot only if it is
  still free. A refusal additionally overrides it to `""`, because the time the customer picked
  is exactly what was not booked (`R2-R01b`).

`handle_prepare_summary`'s authority is untouched: `phone = (ctx.contact.wa_id or "").strip()`,
with the payload's value ignored. `R2-S04` submits `5491199999999` through both
`handle_prepare_summary` and `handle_confirm_booking` and asserts the SUMMARY screen and the
booking row both carry the real `wa_id`.

### 5.3 The route

`flow_data_exchange.py` needed no structural change: the token-error screen already goes
through `appointment_payload(...)`, which now supplies the five keys as empty strings — exactly
what the pre-`c26f9802` hand-rolled dict did deliberately, for the same reason. The only edit
is the comment, which claimed v7.3 "does not declare" five keys. See MF-07 in §9.6.

---

## 6. Proofs 4, 6 and 7 — behaviour

### 6.1 Smart Booking OFF creates nothing (R2-O)

`booking_dispatch_allowed()` requires a literal `True`; `None` and a bare `MagicMock` both read
OFF (`R2-O02`, which is what makes the OFF assertions assertions about the gate and not about a
patch). With the gate off, `_send_booking_flow`:

```
booking token minted      0   (make_booking_token patched to record every call)
customer replies sent     1   (the refusal is a turn outcome, not silence)
thread needs_human        True
ThreadRevision rows       unchanged
WhatsAppMessage rows      unchanged
refusal detail            SMART_BOOKING_DISABLED
```

`TestOffBehaviour` deliberately runs **without** the converged-posture patch, so the flag is in
its deployed state for those assertions.

### 6.2 ON, in disposable isolation (R2-N)

Smart Booking patched ON per class, against a disposable database, with `ScheduleService`
doubled and every provider module stubbed:

```
R2-N01  handle_init                       screen APPOINTMENT, exactly 16 keys, validator clean
R2-N02  init + date_selected + conflict   all three: APPOINTMENT, the same 16 keys
R2-N03  init→date_selected→prepare        ThreadRevision count 0  (nothing books early)
                                          offer count unchanged   (no second offer per session)
        then confirm_booking              ThreadRevision count 1
R2-N04  confirm, then confirm again       ThreadRevision count still 1, offer count unchanged
R2-N05  prepare_summary                   screen SUMMARY, exactly the 12 declared keys,
                                          data["phone"] == the real wa_id
```

### 6.3 Both refusal paths (R2-R)

```
slot conflict     screen APPOINTMENT   has_refusal True   slot_conflict_message set
                                       has_error  False   error_message ""
                                       selected_time ""   and the message is the GENUINE
                                       conflict text, not the OFF-switch handoff sentence
expired token     screen APPOINTMENT   has_error  True    error_message set
                                       has_refusal False  slot_conflict_message ""
                                       the five prefill keys present and EMPTY, not absent
```

`R2-R01b` covers the one line the ordinary conflict never reaches: a refusal called with an
empty submitted date takes the agreed-slot narrowing branch, which would echo the refused time
if the override were removed. The test first asserts the branch *does* echo `"11:00"` without
the override, so it cannot quietly become vacuous.

`R2-R04` proves both transitions are legal on v7.7 and **illegal** on the published Flow —
which is the proof that publication is required, not optional.

### 6.4 Typed 422 preserved

`R2-R05` re-asserts `c26f9802`'s F4 fix structurally: `status_code=422` is raised inside the
handler, and the `except HTTPException` clause is positioned **before** the generic handler in
the AST, so an intended 422 cannot become a 500 again. The existing `HTTP-01…06` group covers
the same ground behaviourally and is unchanged.

### 6.5 The `c26f9802` gate and corrections are intact

`GATE-01…08`, `HTTP-01…06`, `REFUSAL-01…08`, `ROUTING-01…06` and `WILD-01/02` are unmodified
and all pass. `CONTRACT-02…07` are parametrised on `flow_contract`'s constants, so repointing
them at r3 and v7.7 carried those checks across without an edit.

---

## 7. Test results

Runner: `ridecheck-crm-test:l4.7b2`, `-w /work/backend`, `-p no:cacheprovider`, tree mounted
read-only. SQLite runs use `--network none`. PostgreSQL runs use an `--internal` docker network
on which `graph.facebook.com`, `api.openai.com` and `api.resend.com` all fail to resolve
(`P5_disposable_pg.txt`).

### 7.1 Targeted set — 27 modules

```
candidate   21 failed, 1373 passed, 12 skipped, 1509 subtests passed
```

All 21 are in `test_m20_6d2_customer_reality.py` and all 21 are present in the pristine
`c26f9802` baseline's full-suite failure set — **inherited, none new**. The baseline's own
targeted run is not quoted as a comparison: it cannot collect
`test_wild1_r3_aligned_flow_contract.py`, which does not exist in that tree, so the run
aborts. The comparison that matters is the full-suite one below, where both trees collect
everything they have.

### 7.2 Focused suites

```
test_wild1_r3_aligned_flow_contract.py       39 passed      (new)
test_wild1_booking_flow_gate_and_contract.py 44 passed      (42 at c26f9802, +CONTRACT-08d, +09b)
test_l4_7w5_gate_a_flow_prefill_contract.py  26 passed      (24 at c26f9802; 13 of those 24
                                                             failed against this change set until
                                                             the R1 assertions were reversed)
```

### 7.3 Full suite — four runs, identity comparison

Four runs. Both candidate runs are against the **frozen** tree whose per-file hashes are in
`R12_frozen_tree.sha256`; both baseline runs are against
`git archive c26f9802 | tar -x`, a pristine tree with no working-tree state.

| run | tree | posture | result |
|---|---|---|---|
| `base_sqlite` | pristine `c26f9802` | SQLite, `--network none` | 62 failed, 6 992 passed, 658 skipped, 9 errors |
| `cand_sqlite_final` | candidate | SQLite, `--network none` | 62 failed, **7 035** passed, 658 skipped, 9 errors |
| `base_pg` | pristine `c26f9802` | PostgreSQL 16.13, `--internal` net | 92 failed, 6 998 passed, 622 skipped, 9 errors |
| `cand_pg_final` | candidate | PostgreSQL 16.13, `--internal` net | 92 failed, **7 041** passed, 622 skipped, 9 errors |

Failure identities, compared as sets:

```
base_sqlite  (70)  ->  cand_sqlite_final  (70)      NEW: none    GONE: none
base_pg     (100)  ->  cand_pg_final     (100)      NEW: none    GONE: none
```

**+43 passing in both postures, no identity moved in either.** The +43 accounts exactly:

```
test_wild1_r3_aligned_flow_contract.py        0 -> 39   (new module)
test_wild1_booking_flow_gate_and_contract.py 42 -> 44   (+CONTRACT-08d, +CONTRACT-09b)
test_l4_7w5_gate_a_flow_prefill_contract.py  24 -> 26   (+DATE-TIME-07, +PHONE-01b)
                                                  = +43
```

Two notes on how these numbers were produced, because both could otherwise mislead.

**The identity counts are 70/100, not the 66/96 the chain script printed.** The chain's own
filter required `^(FAILED|ERROR) [^ ]*::`, and
`tests/Whatsapp Test/Messages/test_conversation_flows.py` has spaces in its path — four
identities per set were silently dropped. They were dropped from every set equally, so the
comparison was never wrong, but the counts were. Recomputed with `^(FAILED|ERROR) .*::` in
`CHAIN3_identities.txt`; 70 is the known `c26f9802` baseline count.

**The PostgreSQL posture fails 30 more tests than SQLite, in BOTH trees.** All 30 are in
`test_m19_r1_2_durability.py`, `test_m19_r1_2_pg_concurrency.py` and
`test_m19_r1_2_pg_integration.py`, and they are an artifact of pointing one shared
`DATABASE_URL` at one database: those suites expect their own, and one of them
(`test_b1_alembic_head_is_rolling_window`) asserts a different alembic head outright. They are
identical in `base_pg` and `cand_pg_final`, so they are inherited by this posture and not
caused by this change set — but the PG run is therefore an *additional* comparison, not a
cleaner one, and the SQLite pair is the primary result. The suites that take their PostgreSQL
DSN from their own env vars (`SB2A_PG_URL`, `CONV_SSA_PARENT_URL`, …) remain skipped in both
trees and are not certified here; they are unaffected by this change set, which touches no
locking, no transaction boundary and no migration.

### 7.4 Mutation — 20 of 20 killed

Each mutant is one behaviour-changing edit, applied to a disposable copy of the candidate and
reverted afterwards. JSON mutants re-run the generator **and** re-point `TARGET_FLOW_SHA256`,
so they die on a semantic assertion rather than on the hash pin — without that, a JSON mutant
proves nothing. An unapplied mutant is reported BROKEN, never as a survivor.

| # | mutation | killed by |
|---|---|---|
| M01 | the two selection keys leave the contract again | R2-C01, C02, N01, N02 |
| M02 | the three identity keys leave the contract again | R2-C01, C02, N01, N02 |
| M03 | the phone is no longer digits-only | R2-S01 |
| M04 | the mask leaks the whole number | R2-S01, S02 |
| M05 | the requested day is echoed without checking it is offered | R2-S06 |
| M06 | a refusal preselects the time that was not booked | R2-R01b |
| M07 | the name is taken from the WhatsApp profile label | R2-S05, S07 |
| M08 | both server-side phone rebinds trust the Flow's payload | R2-S04 |
| M09 | the generator reads v7.3 and its pin is moved to match (R1's mistake) | R2-B03, B04, C01, C05 |
| M10 | the two messages are appended at the bottom | R2-D03 |
| M11 | the refusal message is permanently visible (`visible` dropped) | R2-D02 |
| M12 | the generator removes a published DETAILS field | R2-C05, D01, D07, D09 |
| M13 | `APPOINTMENT → DETAILS` is dropped while the two edges are added | R2-D04 |
| M14 | SUMMARY stops being terminal | R2-D06, D07 |
| M15 | a customer phone input comes back on DETAILS | R2-C05, D01, D02, D07 |
| M16 | the navigate payload stops carrying the phone | R2-D02, D05, D09 |
| M17 | an `init-value` is introduced on the date Dropdown | R2-D02, D10 |
| M18 | the builder stops refusing undeclared keys | R2-C04 |
| M19 | `RESTORED_PREFILL_KEYS` is emptied | R2-C02 |
| M20 | the identity helper is deleted again | R2-S01, S02, S05 |

**Two mutants survived the first pass and the tests were strengthened rather than the mutants
dropped.** M06 survived because the ordinary slot conflict never reaches the
`selected_time = ""` override — `R2-R01b` was written to exercise the branch that does. M19
survived because `R2-C02` asserted `RESTORED_PREFILL_KEYS <= published`, which an empty set
satisfies vacuously; the set is now **derived** from the two artifacts (live declarations minus
the v7.3 baseline's) and the constant must equal it. M08 was BROKEN on the first pass because
its anchor text appears at both rebind sites; it now mutates both, which is the stronger mutant.

### 7.5 Local validator

`scripts/validate_flow_json.py` — `0 error(s), 0 render, 0 unproven`, exit 0, for v7.3, r3,
v7.6 and v7.7. **This is not Meta validation.** The validator's own closing lines say so, and
§9 states why its silence on the new constructs is expected rather than reassuring.

---

## 8. Rollback and feature-flag behaviour

### 8.1 Code

Nothing is deployed, so there is nothing to roll back. The change set is six modified tracked
files and three new ones, all recoverable by `git checkout -- <path>` for the modified ones;
the three new files are untracked and deleting them returns the tree to `c26f9802`. The live
image `sha256:fa8fac16…` is unaffected and still pins `TARGET_v7.6` / 11 APPOINTMENT keys —
measured in-process in §10, which is the positive proof this candidate is **not** live.

### 8.2 Flow

`CANDIDATE_v7.4-prefill-r3.json` is the rollback artifact, and rollback is **re-importing it by
hand**: Meta retains no prior Flow definition (MED-02), and the 2026-09-16 certification
recorded that after the r3 publish, r1's live definition was no longer recoverable. There is no
version rollback to fall back on. v7.3 is **not** the rollback artifact — publishing it would
withdraw the prefill feature.

### 8.3 Flags

| flag | state | what it gates |
|---|---|---|
| `SMART_BOOKING_CE_ENABLED` | `false` | the booking writer, the DX confirm step, **and** (since `c26f9802`) the Flow dispatch |
| `OUTBOUND_ENABLED` | `"false"` | every send; `outbound_safety_gate` blocks unless the value is the exact string `"true"` |
| `OPERATOR_WHATSAPP_NOTIFY_ENABLED` | `false` | operator notifications |
| `CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED` | `false` | must stay `false` in production |
| `EVIDENCE_ADMISSION_ENABLED` | `false` | W1F-W-01 R1 |
| `OUTSTANDING_QUESTION_ENABLED` | `false` | W1F-W-01 R2 |
| `LOCALITY_RESOLUTION_SAFETY_ENABLED` | `false` | W1F-W-01 R3 |

With `SMART_BOOKING_CE_ENABLED` false the whole change set is inert on a live turn: no Flow is
dispatched, so no APPOINTMENT payload is built, so the restored keys reach nothing. The
correction's blast radius when deployed-but-off is zero, which is why the code can be deployed
before the Flow is published without ordering risk in that direction. **The reverse ordering is
not safe**: publishing v7.7 while the deployed backend still sends 11 keys would leave five
declared-but-unsupplied fields on the live APPOINTMENT screen. See §11.

---

## 9. Meta-only residual risks

**No local result is evidence of Meta acceptance.** Every item below is resolved by Meta's own
Ejecutar and by a bounded owner-present handset run, in that order, and by nothing else. The
repository validator is a deny-list of the three errors Meta actually produced in September; it
knows nothing about any construct below and prints that caveat itself.

### 9.1 MF-R01 (BLOCKER for publication) — `visible: "${data.*}"` on `TextBody`

Measured across all seven artifacts: dynamic booleans have precedent on this asset for exactly
two properties, both on `Dropdown`.

```
PUBLISHED v7.3   Dropdown.required 2,  Dropdown.enabled 2
r1 / r2 / r3     Dropdown.required 2,  Dropdown.enabled 2
v7.6 / v7.7      Dropdown.required 2,  Dropdown.enabled 2,  TextBody.visible 2   ← NO PRECEDENT
```

Meta rejected `init-value` on `Dropdown` and `TextInput` in September precisely because it
checks which properties are allowed per component type. `visible` on `TextBody` has never been
through Ejecutar on this asset. If it is rejected: **do not remove the messages, do not make
them permanently visible, do not invent another binding, and do not publish a modified Flow.**
Export the rejection text and stop.

### 9.2 MF-R02 (HIGH) — SUMMARY is terminal **and** declares an outgoing edge

`SUMMARY.terminal` is `true` in every artifact including v7.7, and v7.7 gives it
`["APPOINTMENT"]`. Whether Meta accepts that combination is unknown. `terminal` was
deliberately **not** removed: that is a Meta-side question and a local edit would be guessing,
which `R2-D06` and mutant M14 enforce.

### 9.3 MF-R03 (HIGH) — two backward edges

`DETAILS → APPOINTMENT` and `SUMMARY → APPOINTMENT` are the first backward transitions this
asset has ever declared; its routing model has been strictly forward since the Flow existed.
Meta may reject a cycle, or accept it and change the handset's back-navigation behaviour. If an
edge is rejected: **do not alter routing and do not publish.**

### 9.4 MF-R04 (HIGH) — the handset, which Ejecutar cannot answer

September already proved on this asset that a Meta-valid Flow can be wrong on a handset: r1
passed Ejecutar clean and then printed `${data.contact_phone_display}` as literal characters.
Ejecutar validates; only a handset renders. Whether the refusal `TextBody` appears, disappears,
or renders as literal `${data.has_refusal}` is a handset question.

### 9.5 MF-R05 (HIGH) — the static preview cannot demonstrate the refusal

Measured on v7.7:

```
has_refusal      __example__ false
has_error        __example__ false
is_time_enabled  __example__ false
```

With all three false, the preview draws neither message and the *Horario* Dropdown is disabled,
so the Horario step cannot be walked. **Do not edit an `__example__` to force the preview**: the
deployed backend will pin `TARGET_FLOW_SHA256`, and any edit changes the hash and breaks the
contract test. A preview that shows nothing is the expected result, not a failure.

### 9.6 MF-07 (MEDIUM, new) — the error screen's empty prefill, and the new edge that reaches it

**This section was rewritten on 2026-10-30 as a documentation-only correction required by the
independent review.** The superseded sentence is quoted in the correction notice at the top of
this document rather than deleted, because the mistake is instructive: it named the *last* of
four validations as the first reason for a refusal, and attributed to it an exception raised by
a different guard.

The expired/tampered-token screen supplies the five prefill values as empty strings. It has no
choice: the token names the contact, and the token is what failed, so there is no
`ctx.contact.wa_id` to read. That was the pre-`c26f9802` published-era behaviour too, so it is
not a regression.

What is new is that v7.7 gives DETAILS an edge **to** APPOINTMENT, so the error screen is now
reachable mid-journey rather than only at the start.

#### The route, as independently traced and re-verified here against the source

1. On an expired or tampered-token error screen, the five prefill values **and** `date` /
   `time` are empty — `appointment_payload()` supplies its declared defaults
   (`flow_data_exchange.py`, the `BookingTokenError` handler).
2. If the customer taps *Continuar*, the Flow navigates to DETAILS carrying **empty
   token/date/time values**, because the navigate payload copies what the screen holds.
3. `prepare_summary` then falls back to the envelope token:
   `booking_token = str(data.get("booking_token") or flow_token or "").strip()`
   (`flow_data_exchange.py:102`). The empty `data.booking_token` is replaced by the envelope
   `flow_token`.
4. **If that token is itself expired or tampered**, token validation fails again —
   `resolve_context()` is the first statement of `handle_prepare_summary` — and the handler
   returns **the same fail-closed error screen**.
5. **If the token is still valid** and the original error arose from another invalid condition,
   the empty `date` / `time` are rejected by the **existing typed HTTP 422 path**:
   `date.fromisoformat("")` raises `ValueError`, which the route converts to
   `HTTPException(422)`. This is the behaviour proof 6 preserves; it is not converted to a 500.
6. **Neither path** writes a row, creates or changes a booking, uses a wrong customer identity,
   sends an outbound message, or loops without another explicit customer tap. Both refusals
   occur before `_booking_event()`, which in any case *"Persists NOTHING"* and is a single log
   line; `handle_prepare_summary` creates no booking by construction, and no outbound path is
   reachable with `booking_dispatch_allowed()` false.
7. **The customer-facing limitation** is therefore not a data or identity fault: continuing from
   the error screen **is not a recovery path**, and it can expose a blank masked-phone caption
   ("Te contactamos a este WhatsApp:" with nothing under it) or Meta's own generic error.
8. **The error copy already tells the customer to restart** — *"Tu sesión de reserva expiró. Por
   favor, volvé a iniciar el proceso."* That is the correct instruction and it **must not** be
   changed to claim that retrying on that screen is supported, because it is not.

#### Severity, disposition and what the Wild must prove

9. MF-07 is recorded **conservatively as MEDIUM** in this implementation closeout. The
   independent review classified its **runtime impact as LOW** after tracing the route above.
   Both readings are kept: MEDIUM is the implementation's own conservative record, LOW is the
   review's traced-runtime assessment.
10. **The bounded handset Wild must prove recovery using a real slot conflict, not deliberate
    token expiry.** A real conflict is the recoverable case the refusal contract exists for —
    the customer is returned to APPOINTMENT with a populated screen and can book another slot.
    Deliberate token expiry exercises the fail-closed dead end described above, which is not a
    recovery path and would mis-certify the feature if used as one.

Named, not fixed: fixing it means changing what the error screen does, which this authorisation
does not cover. Candidate dispositions for a later milestone, in preference order: (a) make the
error screen's `Continuar` footer unavailable, (b) give the caption its own
`visible: ${data.has_error}`-inverse binding, (c) accept it. All three are Flow changes and all
three need their own owner authorisation.

### 9.7 MF-R06 (MEDIUM, carried) — a published Flow reverts to DRAFT when saved

MED-01 from the 2026-09-16 certification: saving a published Flow silently moves it to DRAFT,
and nothing in the backend guards dispatch on publication status. Any owner session that opens
the Flow Builder and saves without publishing leaves the asset in DRAFT. Relevant to the owner
walkthrough, not to this change set.

### 9.8 Unchanged facts worth restating

* All seven artifacts declare `version: "7.3"` and `data_api_version: "3.0"` internally. The
  `v7.4`/`v7.6`/`v7.7` in filenames are filenames, not asset revisions.
* `contact_phone.__example__` in r3 and v7.7 is `"5491100000000"` and
  `contact_phone_display.__example__` is `"…0000"`. Both are inherited from r3 unchanged; they
  are placeholder examples, not a real number, and a Flow definition carries no token, signed
  URL or key.

---

## 10. Live state and `crm_test`: unchanged

### 10.1 The database

Per-table content digests over every table in `public`, computed read-only
(`PGOPTIONS=-c default_transaction_read_only=on`), compared against the list the 2026-10-29
milestone recorded at its close:

```
tables 2026-10-29 END : 30      tables now : 30
sorted sha256 2026-10-29 END : 30d4ac4566f4b5968fc44ad69aaa3be75d0c292abbfb25292cf278f6ad8bb393
sorted sha256 now            : 30d4ac4566f4b5968fc44ad69aaa3be75d0c292abbfb25292cf278f6ad8bb393
CONTENT DIGESTS IDENTICAL — not one row changed in any of 30 tables since 2026-10-29
```

Both lists were re-sorted canonically before comparison: the six empty tables tie on the digest
key, and the original ordering was by digest alone. The legacy `crm` database (20 tables) was
digested too and is recorded for completeness.

### 10.2 The container and the deployed code

```
Started  2026-10-29T17:22:15.690662255Z
Restarts 0
Image    sha256:fa8fac166ba461fdc71d88023e1e5c95dd16e041b00a43ebfe78dca2914af6ac
Running  true
```

Read in-process from the running container:

```
deployment_id                                : c26f9802ca93
OUTBOUND_ENABLED (env)                       : 'false'   blocks_sends=True
smart_booking_ce_enabled                     : False     literal_false=True
operator_whatsapp_notify_enabled             : False     literal_false=True
conversation_engine_direct_webhook_enabled   : False     literal_false=True
evidence_admission_enabled                   : False     literal_false=True
outstanding_question_enabled                 : False     literal_false=True
locality_resolution_safety_enabled           : False     literal_false=True
booking_dispatch_allowed()                   : False
RESEND_API_KEY blank                         : True
SMTP_PASSWORD  blank                         : True
```

`OUTBOUND_ENABLED` is **not** a `Settings` field — `outbound_safety_gate` reads `os.environ`
directly and blocks unless the value is the exact string `"true"`. It is therefore read from the
running process's environment rather than from `get_settings()`. The other six are literal
`False` on the `@lru_cache`d settings object of the live process.

And the positive proof this candidate is not live:

```
the LIVE image's flow_contract:
  TARGET_FLOW_PATH    meta/flows/booking/TARGET_v7.6-minimal-refusal.json
  TARGET_FLOW_SHA256  a25de8f43f5ada03650541cd84fa42cb4a2a5b2d772d89ac520afcd3219c43b4
  APPOINTMENT keys    11
  has _identity_fields  False
```

### 10.3 Outbound and automatic-sender eligibility in `crm_test`

```
outbound rows by status   delivered 1, failed 1, read 47     pending 0
live booking offers       0   (unaccepted and unexpired)
threads with a Flow token 1   (the tester, from the 2026-10-29 Wild)
threads needs_human       1   (the same thread)
schema head               20261027_w1f_conversation_memory
```

Zero pending outbound and zero live offers: nothing is armed to send. The one tokened,
`needs_human` thread is the tester's state from 2026-10-29 and is consistent with the zero-delta
digest above.

### 10.4 Git

```
HEAD    c26f9802ca93da0263d031e8096b3f67938d1914
branch  integration/prelaunch-booking-convergence
staged  0
stash   0
tags at HEAD  0
```

### 10.5 What was created outside the repository

`/opt/ridecheck-builds/crm-wild1-r3align-20261030T130929Z` (mode 0700) — the evidence root.
`/opt/ridecheck-builds/pristine-c26f9802-132200` — `git archive c26f9802 | tar -x`, the
comparison baseline. `/opt/ridecheck-builds/mutate-r2-132611` — the disposable mutation tree,
restored to the unmutated candidate at the end of the run (the harness re-runs the suite after
restoring and reports the result). Disposable containers: `pgtest-r2align` (postgres:16.13) on
the `--internal` network `r2align-net`, both still present for inspection and holding no
project data. No `docker prune` of any scope was run; no file was deleted.

### 10.6 Re-measured again at the close, after every test run

The §10.1 digest was taken before the four full-suite runs; this one is after them, and after
the two re-runs, the mutation set and every validator execution:

```
tables 30
sorted sha256 CLOSE          30d4ac4566f4b5968fc44ad69aaa3be75d0c292abbfb25292cf278f6ad8bb393
sorted sha256 2026-10-29 END 30d4ac4566f4b5968fc44ad69aaa3be75d0c292abbfb25292cf278f6ad8bb393
STILL IDENTICAL — zero rows changed across this entire milestone

container      Started 2026-10-29T17:22:15Z   Restarts 0   Image sha256:fa8fac16…
deployment_id  c26f9802ca93   OUTBOUND_ENABLED 'false'   booking_dispatch_allowed() False
six Settings flags all literal False : True
live image still pins                : TARGET_v7.6-minimal-refusal.json, 11 APPOINTMENT keys
n8n container       Started 2026-10-21T23:31:12Z  restarts 0
postgres container  Started 2026-10-21T23:31:05Z  restarts 0
HEAD c26f9802…   staged 0   stash 0   tags-at-HEAD 0
disk /dev/vda1  116G total, 15G available, 88% used
```

Every test run was in a disposable container with the tree mounted **read-only**, against
either an in-memory SQLite database or a disposable PostgreSQL on an `--internal` network.
The live backend was only ever read from, in-process. The live PostgreSQL was only ever read
from, under `default_transaction_read_only=on`. `n8n` was not touched at all. No `docker
prune` of any scope was run and no file was deleted; the disposable PG container
(`pgtest-r2align`, holding `crm_r2`, `crm_r2b`, `crm_r2c`) and the network `r2align-net` are
left in place for inspection and can be disposed of by exact name.

---

## 11. Deployment and publication ordering

| step | safe to do alone? | why |
|---|---|---|
| deploy the backend, Smart Booking OFF | **yes** | no Flow is dispatched, so no payload is built; the change set is inert |
| publish v7.7 with the current backend live | **no** | the deployed backend supplies 11 keys; v7.7 declares 16. Five declared-but-unsupplied fields on the live APPOINTMENT screen, including the masked phone DETAILS reads |
| publish v7.7 **after** this backend is deployed | yes | declared equals supplied at 16 |
| leave the current backend live with r3 published | the state right now | the backend omits 5 keys r3 declares and 2 it binds. **Zero live exposure, because `booking_dispatch_allowed()` is `False`** — no Flow is dispatched at all. It becomes real at the first handset run |

So the order is: **commit → review → build → deploy (flags OFF) → owner publishes v7.7 →
bounded owner-present handset run.** Publishing first is the one ordering that has a cost.

---

## 12. Proposed commit — NOT EXECUTED

Exact paths, no wildcards, no `git add .`, no `git add -A`:

```
git add backend/app/services/flow_contract.py \
        backend/app/services/booking_flow_service.py \
        backend/app/routes/flow_data_exchange.py \
        meta/flows/booking/TARGET_v7.7-r3-prefill-refusal.json \
        meta/flows/booking/make_r3_refusal_target.py \
        meta/flows/booking/README.md \
        scripts/validate_flow_json.py \
        tests/test_wild1_r3_aligned_flow_contract.py \
        tests/test_wild1_booking_flow_gate_and_contract.py \
        tests/test_l4_7w5_gate_a_flow_prefill_contract.py \
        docs/Launch/2026-10-30_RIDECHECK_CRM_PRELAUNCH-WILD1-BOOKING-FLOW-R3-BASELINE-ALIGNMENT-CORRECTION_CLOSEOUT_001.md

git commit -F - <<'EOF'
fix(WILD1-R2): the refusal is added to the Flow that is actually published

R1 derived its target from PUBLISHED_28104222025943520_v7.3.json, on the strength of a
README row calling that file "a byte-exact copy of what is PUBLISHED right now" — two
rows above another saying r1 went live on 2026-09-16. The owner exported the published
definition for asset 28104222025943520 from Meta on 2026-10-30: it is r3's structure,
which the 2026-09-16 Gate B certification had already proved by downloading flow.json
from the Flows API and showing live == r3 by deep structural equality.

So R1 removed five APPOINTMENT values a shipped, handset-certified screen declares, and
TARGET_v7.6 would have completed the regression: a required Teléfono input back on
DETAILS, the masked-WhatsApp caption deleted, two action payloads reverted.

This revision derives the target from r3 instead. The delta is exactly four APPOINTMENT
fields, two conditional TextBody components and two routing edges; nothing is removed,
renamed, reordered or retyped, and DETAILS and SUMMARY are structurally identical to the
published screens. The backend supplies the published twelve plus the four — sixteen keys,
declared equals supplied in both directions — and the five restored values come from
server state only: digits of ctx.contact.wa_id, its mask, the persisted lead name, and
two selection ids that exist in their own data-source or are empty.

The F3a diagnosis is unaffected: v7.3 and r3 declare the identical routing model, so the
absent SUMMARY -> APPOINTMENT edge was the cause whichever was live.

Neither superseded artifact is deleted. TARGET_v7.6 and its generator stay for audit, and
flow_contract now names the live artifact, the preserved pre-16-September baseline and the
superseded target as three separate pinned constants so none can stand in for another. The
README's contradiction is corrected in place without rewriting its history.

make_r3_refusal_target.py pins r3 by canonical fingerprint and exits non-zero without
writing when given anything else, so deriving from the wrong baseline is now an error
rather than a silent success.

20/20 mutants killed. Full suite on SQLite and on a disposable PostgreSQL 16.13, candidate
and pristine c26f9802: failure identity sets identical, no new, no gone.

Not published, not imported, not validated through Meta, not built, not deployed, not
migrated. No flag changed. Zero rows changed in crm_test.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012ft9E2ABmtgvCUi5noCKft
EOF
```

No `git push`, no `git tag`, no `git stash`. `docs/operations/RECOVERY_MANIFEST.md` and
`docker-compose.beta.yml` are **deliberately excluded**: both were already modified before this
milestone and neither is part of this change set.

---

## 13. Proposed next milestone

```
PRELAUNCH-WILD1-BOOKING-FLOW-R3-ALIGNED-CANDIDATE-REVIEW-AND-COMMIT
  — OWNER AUTHORIZATION REQUIRED
```

An independent review of this change set, then the §12 commit, then — as a separate
owner-authorised milestone — a controlled build and deployment:

1. **Review** (no build): re-derive the r3 → v7.7 delta independently; re-run the mutation set;
   check that the five restored values cannot be influenced by Flow input on any path; confirm
   MF-07's containment claim by reading `handle_prepare_summary` rather than trusting §9.6.
   — **DONE 2026-10-30: CONCLUSION A, commit-ready as scoped, no code correction required.**
   Reading `handle_prepare_summary` is exactly what found the §9.6 mechanism error this
   document has now corrected, which is the case for asking a reviewer to read the source
   rather than the closeout.
2. **Commit** exactly §12.
3. **Controlled build**: `docker build --network none --pull=false` from
   `/opt/ridecheck-crm-integration`, **never** from the Compose `build:` context, which points
   at `/opt/ridecheck-crm/backend` (`main`, months old). Tag `wild1-r2align-<sha12>`. Certify
   offline on a disposable PostgreSQL 16.13 before any deploy.
4. **Controlled deployment**, flags OFF, `--no-build --pull never`, with the image pin edited in
   `/opt/ridecheck-crm-release-candidate/docker-compose.beta.yml`.
5. **Then** the owner publishes v7.7 — the Flow walkthrough and the fail-closed decision tree
   from the 2026-10-29 publication-preparation closeout still apply, with `TARGET_v7.7` and r3
   substituted for `TARGET_v7.6` and v7.3 throughout, and with §9's seven risks as the stop
   conditions.
6. **Then** the bounded owner-present handset run.

Clean-Wild counter stays **0/3** until step 6 produces a clean Wild.

---

## 14. Findings register

| id | severity | finding | state |
|---|---|---|---|
| MF-01 | BLOCKER | the published baseline was contested: README said v7.3, the Gate B certification said r3 | **CLOSED** by the owner's 2026-10-30 Meta export; README corrected; three artifacts now separately pinned |
| MF-02 | HIGH | the deployed backend omits 5 keys the live Flow declares and 2 it binds | **CLOSED in code** by this change set; still live until deployed, with zero exposure while `booking_dispatch_allowed()` is `False` |
| MF-03 | MEDIUM | `meta/flows/booking/README.md` contradicted itself about which file is live | **CLOSED**; the correction is at the top of the file and the distinguishing structural facts are now asserted by test (`CONTRACT-09b`) |
| MF-07 | MEDIUM here / LOW runtime per the independent review | the error screen's empty prefill is now reachable mid-journey via the new DETAILS edge; continuing from it is not a recovery path | **OPEN**, named in §9.6 (rewritten 2026-10-30: the mechanism first recorded was wrong), fail-closed on both traced paths, three dispositions proposed; the bounded Wild must prove recovery with a real slot conflict, never with deliberate token expiry |
| MF-R01 | BLOCKER for publication | `visible: "${data.*}"` on `TextBody` has no precedent on this asset | OPEN — Meta Ejecutar only |
| MF-R02 | HIGH | SUMMARY is terminal and declares an outgoing edge | OPEN — Meta Ejecutar only |
| MF-R03 | HIGH | two backward routing edges, the asset's first | OPEN — Meta Ejecutar only |
| MF-R04 | HIGH | handset rendering of a conditional `TextBody` | OPEN — handset only |
| MF-R05 | HIGH | the static preview cannot demonstrate the refusal | OPEN — expected, do not force it |
| MF-R06 | MEDIUM | a published Flow silently reverts to DRAFT when saved (MED-01) | OPEN — carried |

---

## 15. Evidence

`/opt/ridecheck-builds/crm-wild1-r3align-20261030T130929Z` (0700)

```
P1_baseline_and_determinism.txt   fingerprints, two regenerations, five refused baselines
P2_r3_to_v77_delta.json           the machine-readable r3 -> v7.7 delta
P5_disposable_pg.txt              the disposable PG bootstrap and the network-isolation proof
P5_disposable_pg_migration.txt    why `alembic upgrade head` cannot run from a bare database
P6_contract_table.txt             the declared/supplied table in §5.1, generated
P7_mutation.log                   the 20-mutant run, with the two strengthened tests
P8_validator.txt                  validate_flow_json.py on all four assets
P9_meta_constructs.txt            the §9 measurements
P10_r3_to_v76_delta.json          what publishing v7.6 over the live Flow would have done
P10_r3_to_v76_summary.txt         the same, summarised
R9_digest_list_crm_test_END.txt   30 per-table content digests, read-only
R9_digest_list_END.txt            the legacy `crm` database, 20 tables
R9_invariance.txt                 the zero-delta comparison and the container state
R10_live_state.txt                deployment_id, the seven flags, outbound, schema head
R11_change_set.txt                git state, the file list, the hashes
R11_diff_tracked.patch            the full diff of the six modified files
R11_diff_untracked.patch          the three new files
R9_*/R10_*/R11_* …                see §10 and §2
R12_frozen_tree.sha256            the per-file hashes the final two runs were made against
R13_suite_counts.txt              the per-suite baseline/candidate counts behind the +43
R14_closing_state.txt             §10.6, the closing re-measure
R14_digest_list_crm_test_CLOSE.txt  the 30 digests taken after every test run
CHAIN2.log                        the first four full-suite runs and their comparisons
CHAIN3.log                        the two candidate re-runs against the frozen tree
CHAIN3_identities.txt             the final identity comparison, space-tolerant filter
T_*.log                           every pytest run in full
ids2_*.txt / idsFINAL_*.txt       the failure identity sets
```

Also present, from the chain that was superseded when the tree changed mid-run:
`T_baseline_c26f9802_off.log`, `T_r2_candidate_off.log`, `FULLCHAIN.log`,
`ids_baseline_c26f9802_off.txt`, `T_targeted_*.log`, `ids_targeted_*.txt`. They are kept
rather than deleted: the earlier candidate run recorded 7 034 passing against a tree that was
one test short of final, and the difference between that and 7 035 is the audit trail for why
the two candidate runs were repeated.
