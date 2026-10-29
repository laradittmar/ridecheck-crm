PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-WILD1-BOOKING-FLOW-GATE-AND-REFUSAL-CONTRACT-CORRECTION
REVISION: R1-MINIMAL-PUBLISHED-DELTA, COMMITTED

# The booking-path cluster from the 2026-10-29 natural Wild — correction and audit

Date: 2026-10-29 · Branch `integration/prelaunch-booking-convergence` · HEAD `1008434ca76c`
**Nothing committed, staged, tagged or stashed. No live action. No Meta contact.**

This revision supersedes the first version of this document. Two of its material
conclusions were wrong, and both are corrected below with the evidence that refutes them
rather than quietly replaced:

* **the stated cause of F3 was wrong.** "The Flow runtime rejects a payload carrying
  undeclared keys" is contradicted by the Wild's own logs. §3 derives the real cause.
* **the proposed target was not minimal and would not have fixed the failure.** It was
  derived from an unpublished prefill candidate, changed twelve fields rather than four,
  altered the live DETAILS screen — and left the actual defect untouched. §4.

---

## 1. Starting safety posture, recorded before any edit

| | |
|---|---|
| HEAD | `1008434ca76c` — `fix(prelaunch): clarify R2 migration rollback boundary` |
| Branch | `integration/prelaunch-booking-convergence`, 0 ahead of origin |
| Staged / stash / tags | 0 / 0 / 0 |
| Dirty worktree at start | 12 modified, 68 untracked |
| `docs/operations/RECOVERY_MANIFEST.md` | **preserved**, sha256 `c70cdb3921018dae36a89242e2d6a9a01613272baa83e4f853e65a5e21294581`, unchanged. Its 122-line diff against HEAD is a pre-existing worktree edit and is **excluded from the proposed commit.** |
| Unrelated untracked audits/closeouts | 59 under `docs/Launch`, none modified |
| Wild evidence root | `/opt/ridecheck-builds/crm-w1f-wild1-20261029T111612Z`, 0700, preserved |
| Live backend | `ridecheck-crm-backend-1`, image `ridecheck-crm-backend:w1fintegrated-1008434c`, restart count **0**, started 2026-10-29T11:53:24Z |
| Live flags | `SMART_BOOKING_CE_ENABLED=false`, `OUTBOUND_ENABLED=false`, `CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED=false`, `RESEND_API_KEY` blank |
| Preserved Wild rows | untouched: offer 3 still `ISSUED`, thread 2074 / lead 168 untouched |

No container was recreated, no image was built, no flag was changed, no provider was
contacted, no Flow was published, no offer was cancelled, no `git add` was run.

---

## 2. What the Wild actually recorded

From `evidence/providers_delivery.txt` and `evidence/breach_forensics.txt`:

```
08:53:31  BOOKING_FLOW_REQUEST action=INIT               → 200, APPOINTMENT drawn
08:55:53  BOOKING_FLOW_DATE_SELECTED  date=2026-10-29    → APPOINTMENT redrawn with times
08:56:20  BOOKING_FLOW_SUMMARY_PREPARED date=2026-10-29 time=13:00   → SUMMARY drawn
08:56:23  BOOKING_HANDOFF  reason=SMART_BOOKING_DISABLED
08:56:23  BOOKING_REFUSED  reason=SMART_BOOKING_DISABLED  → "something went wrong"
08:56:40  BOOKING_FLOW_CONTEXT_CREATED                   → the owner's next attempt
08:56:45  BOOKING_FLOW_DATE_SELECTED                     → …and so on, ten times
```

Three screens rendered. The failure is at `confirm_booking`, on the SUMMARY footer, every
time.

---

## 3. F3, re-derived — the cause is the routing model, not the field set

### 3.1 Why the first explanation cannot be true

The deployed backend sends APPOINTMENT **twelve** keys. Published v7.3 declares seven.
These five are declared nowhere in the published artifact:

```
selected_date  selected_time  customer_name  contact_phone  contact_phone_display
```

They came from `_identity_fields` and the GATE-A selection echo, written against
`CANDIDATE_v7.4-prefill-r3.json`, which has never been published. **That payload rendered
twice** — INIT at 08:53:31 and `date_selected` at 08:55:53 — and the owner reached SUMMARY.
Undeclared extra keys were tolerated by this asset, so a sixth undeclared key
(`slot_conflict_message`) cannot be what broke the refusal.

### 3.2 What distinguishes the failure

Every one of the four `_refusal_screen` call sites, and the `BookingTokenError` error
screen in `routes/flow_data_exchange.py`, returns `screen: "APPOINTMENT"`. All of them are
raised from `_confirm_booking` or `handle_prepare_summary` — reached while the customer is
on SUMMARY or DETAILS. Published v7.3 declares:

```json
{"APPOINTMENT": ["DETAILS"], "DETAILS": ["SUMMARY"], "SUMMARY": []}
```

| observed | routing edge | undeclared keys | result |
|---|---|---|---|
| APPOINTMENT → APPOINTMENT (`date_selected`) | none needed — self-refresh | 5 | **worked** |
| DETAILS → SUMMARY (`prepare_summary`) | declared | 0 | **worked** |
| SUMMARY → APPOINTMENT (every refusal) | **absent** | 6 | **failed, 10/10** |

The absent edge is the only variable that separates the failure from the two successes.
A screen name in a Data API response is a navigation instruction; `"SUMMARY": []` permits
none.

### 3.3 So there are two defects where the first version saw one

* **F3a** the refusal names a transition the published Flow forbids. The response is
  invalid and the customer sees Meta's generic error. **This is what the Wild hit.**
* **F3b** APPOINTMENT binds no component to a refusal message, so a delivered refusal
  would still have been silent.

The first version fixed only F3b. **Publishing its target would have left the owner's ten
failures exactly as they are** — its `routing_model` is byte-identical to v7.3's, including
`"SUMMARY": []`. That is the most serious finding in this document and it is mine.

---

## 4. Correction 1 — the true published delta, and a minimal target

### 4.1 The three-way comparison

Generated by `meta/flows/booking/compare_flows.py` into
`meta/flows/booking/PUBLISHED_TO_TARGET_DELTA.json` (63 428 bytes), baseline
`PUBLISHED_28104222025943520_v7.3.json` sha256 `274038ba234a49fa…`. Per screen it lists
declared fields added/removed/type-changed, components added/removed by content, visibility
rules, navigation and action payloads, and a RENDERED/CARRIED classification of every field.

| variant | sha256 | routing | APPOINTMENT fields | DETAILS fields | components |
|---|---|---|---|---|---|
| `TARGET_v7.6-minimal-refusal.json` | `a25de8f43f5ada03…` | **+2 edges** | **+4** | 0 | **+2** |
| the candidate-derived `TARGET_v7.5` † | `6b9da137ddd1cf21…` | unchanged | +9 | +3 | +2, **1 rewritten, 1 deleted** |
| `CANDIDATE_v7.4-prefill-r3.json` | `53a3d88f3dd57c9b…` | unchanged | +5 | +3 | +2, 1 rewritten, 1 deleted |

† **Not in this repository.** The owner ruled that a known-wrong, non-deployable artifact
must not sit beside the real Flow candidates. It is preserved for audit at
`/opt/ridecheck-builds/crm-wild1-bfgate-20261029T121535Z/superseded/SUPERSEDED_TARGET_v7.5-candidate-derived.json`,
sha256 `6b9da137ddd1cf21e925a897f086cd62b9e8c2b96d26872855c00b2922168c82`. Its row above is
a recorded measurement, taken while the file was still in the tree. The committed
`PUBLISHED_TO_TARGET_DELTA.json` covers only repo-resident artifacts, so it can always be
regenerated from the repository alone. Removing it also removed a dangling
`SUPERSEDED_FLOW_PATH` constant from `flow_contract.py`; no test referenced it.

The superseded target did more than add nine fields. It **deleted the `phone` TextInput
from DETAILS** and rewrote the `prepare_summary` payload to read `${data.contact_phone}`
instead of `${form.phone}`. Publishing it would have stopped asking live customers for a
phone number, in a milestone whose purpose was to render a refusal.

### 4.2 The minimal target

`meta/flows/booking/TARGET_v7.6-minimal-refusal.json`, sha256
`a25de8f43f5ada03650541cd84fa42cb4a2a5b2d772d89ac520afcd3219c43b4`.

Generated **from the published baseline** by `meta/flows/booking/make_minimal_target.py`, a
committed script. Re-running it produces a byte-identical file, so the delta is exactly
what that script does:

```
APPOINTMENT data  + slot_conflict_message : string
                  + has_refusal           : boolean
                  + error_message         : string
                  + has_error             : boolean

APPOINTMENT layout
  + {"type":"TextBody","text":"${data.slot_conflict_message}","visible":"${data.has_refusal}"}
  + {"type":"TextBody","text":"${data.error_message}",        "visible":"${data.has_error}"}

routing_model     DETAILS : ["SUMMARY"] → ["SUMMARY","APPOINTMENT"]
                  SUMMARY : []          → ["APPOINTMENT"]
```

Nothing else. `version` stays `"7.3"`, `data_api_version` stays `"3.0"`. DETAILS and
SUMMARY declare exactly what they declare today; no existing component, label, name,
`required`, `enabled`, `data-source`, navigate payload or data_exchange payload changes.
`CONTRACT-08c` asserts this: every published declaration and component must survive
unchanged and the additions must be exactly those two TextBody components.

**It is genuinely minimal.** Neither addition can be dropped: without the routing edges the
response is invalid (F3a), without the fields and components the refusal is silent (F3b).

### 4.3 The five candidate keys are removed from the backend, with proof

Each was inert against the published Flow:

* **`selected_date` / `selected_time`** — v7.3's two Dropdowns declare no `init-value`, so
  no value the backend sent could preselect either. The service's own comment said exactly
  this while the payload kept sending them. What does work is the narrowing of the option
  *list*, which is kept and still asserted (DATE-TIME-01..06).
* **`customer_name`** — v7.3's DETAILS `name` TextInput declares no `init-value`. Only the
  unpublished candidate binds it.
* **`contact_phone` / `contact_phone_display`** — v7.3's APPOINTMENT→DETAILS navigate
  payload carries neither, and v7.3's DETAILS declares neither, so nothing downstream
  could read them. The phone an operator calls is `ctx.contact.wa_id`, re-asserted
  server-side in `_confirm_booking` whatever any screen shows — PHONE-02 and PHONE-03,
  unchanged and passing.

They are named in `flow_contract.DEFERRED_PREFILL_KEYS` and asserted absent from every
screen contract, from `appointment_payload()` and from the service's payload builders by
`CONTRACT-08b`. `_identity_fields` is removed; the candidate artifacts and their Gate B
certification tests stay in the repository for the deferred prefill milestone, and the
helper is recoverable from `1008434ca76c`.

**PHONE-05 is now strictly stronger than before:** the full number appears nowhere in the
payload at all, rather than merely being absent from its own masked copy.

---

## 5. Correction 2 — truthful change description

`flow_contract.py`, the runbook, this closeout and the tests now describe the delta
relative to **the published artifact**. The pins:

| | |
|---|---|
| published baseline | `meta/flows/booking/PUBLISHED_28104222025943520_v7.3.json` |
| | sha256 `274038ba234a49fa4f99bae47c00f6cac640e248d56a373d40ae8fba6c1afb15` |
| corrected target | `meta/flows/booking/TARGET_v7.6-minimal-refusal.json` |
| | sha256 `a25de8f43f5ada03650541cd84fa42cb4a2a5b2d772d89ac520afcd3219c43b4` |
| semantic delta | +4 APPOINTMENT fields, +2 APPOINTMENT components, +2 routing edges, nothing else |
| excluded from deployment | `SUPERSEDED_TARGET_v7.5-candidate-derived.json`, `CANDIDATE_v7.4-prefill*.json` |

`CONTRACT-01` re-reads the target by path and hash; `CONTRACT-09` and `ROUTING-03` keep the
published-vs-target gap as standing regressions, so pointing the contract at v7.3 to obtain
green results fails and says why.

---

## 6. Correction 3 — conservative deployment order

`docs/operations/2026-10-29_BOOKING_FLOW_PUBLICATION_RUNBOOK.md`. The claim that
Flow-before-backend was equally safe rested on an unverified assumption about how Meta
treats a declared field the backend omits, and is **withdrawn**. Authorised order:

1. commit and build the corrected backend;
2. deploy **only** the backend, seven flags remaining `false`;
3. prove Smart Booking OFF produces no offer and no Flow;
4. import the target into Meta Flow Builder **without publishing**;
5. let Meta's validator run against the exact artifact;
6. preview INIT and the refusal/error states;
7. publish only after validation;
8. bounded owner-present handset verification;
9. enable Smart Booking **only inside** that window.

Until both halves are verified, Smart Booking stays OFF — and with it off the Flow is never
dispatched, so no customer can reach a screen that cannot render.

**Honest gap:** `visible` bound to a `${data.*}` boolean on a `TextBody` in Flow JSON 7.3
has **not** been validated by Meta. Step 5 is the proving step. The named fallback is a
dedicated refusal screen reached by the same two routing edges, with its own text field and
no conditional visibility. Nothing here demonstrates that an omitted declared field renders
safely, and this document does not claim it.

---

## 7. Correction 4 — rollback truth

**Unverified, and not relied upon:** whether Meta Flow Builder offers "revert to a previous
published version" for asset `28104222025943520`, and whether a prior version stays
selectable after a new one is published. No Meta interface or API was contacted. The
runbook instructs the owner to check this at step 4, while already in the Builder, and
record what the interface actually offers.

**The mechanism that depends on nothing new:** re-import
`PUBLISHED_28104222025943520_v7.3.json` (sha256 `274038ba234a49fa…`) and publish it as a new
version of the same asset — using only the import-and-publish capability that steps 4–7
already exercise. The artifact is byte-identical in the repository and asserted by
`test_flow_07_the_published_baseline_is_byte_identical`.

**Last resort, not rehearsed:** a new Flow asset plus `WHATSAPP_BOOKING_FLOW_ID` repointed
in `.env`, which is a configuration change and a redeploy.

**Fastest kill, fully in our control:** `SMART_BOOKING_CE_ENABLED=false` removes the Booking
Flow from the customer journey entirely — F1's boundary refuses the dispatch before a token
or an offer exists. No Meta action required.

**Backend:** redeploy the previous image. Code-only; no migration, no schema change, no data
change, `alembic` head untouched.

---

## 8. Correction 5 — A.5 residual, classified honestly

**A.5 is NOT closed.**

Measured, not asserted: `OfferLifecycle.issue()` has exactly **two** production callers,
both in `conversation_engine.py` — `_send_booking_flow` (CE:13251) and the legacy
data-collection Flow (CE:13082). **Both refuse above the call when dispatch is off**, before
a token is minted and before an offer row exists, asserted by AST dominance across every
function that mints a booking token (GATE-06, GATE-07).

* all currently identified customer/product dispatch paths are **prevented**;
* a future caller, or a direct internal call, **can still** create an offer while Smart
  Booking is OFF;
* the `issue()` detector makes that **visible** (`BOOKING_OFFER_ISSUED_WHILE_DISPATCH_OFF`
  at ERROR) but **does not prevent it**.

The detector is kept, not promoted to a raise. Making it block satisfies the words of A.5
and turns issuing an offer into a Smart-Booking-gated action for the whole test estate:
**114 tests across five suites fail**, including the offer authority's own contract suite
that guards the one-offer/one-booking index. Those suites seed offers through `issue()` with
the flag at its default `False`. **The 36 affected call sites across 7 files are all in
`tests/`** — a correction to the first version of this document, which implied production
breadth.

No small source-aware fail-closed boundary is proven. Every design considered either adds a
parameter at all 36 test call sites — the mechanical re-fixturing the milestone forbids — or
relies on a caller declaring its own source, which a future caller can get wrong. The
alternative is therefore recorded as a named item rather than attempted:

> **POST-LAUNCH-HARDENING-01 — source-aware fail-closed offer issuance.** Make
> `OfferLifecycle.issue()` refuse when booking dispatch is off, with an explicit,
> type-checked source for legitimate non-dispatch issuance, without changing manual
> operator booking authority (`BookingAuthority.execute` via `ui/kanban_actions.py:868`
> and `api/revisions.py:96,173`). Cost measured at 114 tests / 5 suites / 36 call sites.

---

## 9. Retained without regression

| invariant | evidence |
|---|---|
| Smart Booking OFF blocks both customer Flow-dispatch paths before token or offer creation | GATE-06, GATE-07 (AST dominance), WILD-01 |
| The approved non-Flow handoff text is used, without Resend | GATE-08 (no e-mail helper reachable from the refusal), live `RESEND_API_KEY` blank |
| `HTTPException` remains outside the generic 500 handler | HTTP-01, HTTP-02 |
| Empty/unknown trigger and unknown action remain 422 | HTTP-04, HTTP-05 |
| Slot conflicts remain encrypted Flow responses | HTTP-03 |
| Contract checks remain bidirectional | CONTRACT-03, CONTRACT-04, REFUSAL-08 |
| The original defective exclusion set does not return | CONTRACT-08 (source scan), `test_l4_7w5_gate_a_flow_prefill_contract.py:336` fixed at source |
| `BookingAuthority.execute` is never dispatch-gated | GATE-08, and the boundary module's docstring records why |
| Operator booking by hand is untouched | no change to `ui/kanban_actions.py` or `api/revisions.py` |

---

## 10. Results

### 10.1 Regression identity, against a freshly built pristine HEAD

A clean tree was materialised with `git archive HEAD | tar -x` and the full suite run
against both trees in the same sealed container.

| | pristine `1008434ca76c` | R1 worktree |
|---|---|---|
| failed / errors | 62 / 9 | 62 / 9 |
| passed | 6 950 | **6 992** |
| skipped | 658 | 658 |
| unique failure identities (FAILED+ERROR) | **70** | **70** |
| NEW vs pristine | — | **none** |
| GONE vs pristine | — | **none** |

The two identity sets are byte-identical (`diff` clean). **+42 passing, zero new failures,
zero disappeared failures.**

*Correction to the first version of this document:* it reported the baseline as 67
identities. The measured figure is **70** (61 unique FAILED plus 9 ERROR). 67 matches
neither count and was a miscount. The conclusion — zero new, zero gone — is unchanged and
is now backed by two full runs and an identical set comparison.

`test_sb1_20_frozen_files_unchanged` is among the 70 and is **pre-existing**: three frozen
files already drifted at HEAD (`claim_projection.py`, `routes/whatsapp.py`,
`routes/flow_data_exchange.py`), measured by comparing each frozen git-blob hash against
HEAD and against the worktree.

### 10.2 Focused booking/flow/offer/sched set — 38 modules

8 failure identities before, 8 after, 1 001 passing (959 at the parent). All eight are the
known `OUTBOUND_ENABLED` environment tests, the two `unanswered_alert` Resend tests and the
frozen-files test above.

### 10.3 Mutation

13 mutants, **13 killed**, across the artifact, the contract module and the service.

A JSON mutant also re-points `TARGET_FLOW_SHA256` at the mutated file, so no artifact
mutant can die on the hash pin alone — otherwise the harness would prove nothing about the
invariant it was written to probe, which is the F5 mistake in a different costume.

| mutant | killed by |
|---|---|
| M-R1 the `SUMMARY → APPOINTMENT` edge dropped (**the live defect, restored**) | ROUTING-02, ROUTING-05 |
| M-R2 the `DETAILS → APPOINTMENT` edge dropped | ROUTING-02, ROUTING-05 |
| M-R3 `validate_transition` accepts everything | ROUTING-03, ROUTING-06 |
| M-R4 a deferred prefill key re-admitted to the contract | PHONE-01, PHONE-05, DATE-TIME-01, DATE-TIME-02 |
| M-R5 the service echoes the selection again | PHONE-01, PHONE-05, DATE-TIME-02, DATE-TIME-03 |
| M-R6 the refusal TextBody removed — transmitted, never read | CONTRACT-05, CONTRACT-08c |
| M-R7 `visible` dropped — the refusal shows on every draw | CONTRACT-05, CONTRACT-08c |
| M-R8 `TRIGGER_ORIGIN` edited to make the forbidden transition look legal | ROUTING-01 |
| M-R9a `REQUIRED_ROUTING_EDGES = frozenset()` | ROUTING-02 |
| M-R9b only the SUMMARY edge required | ROUTING-02 |
| M-R10 the `phone` TextInput deleted from DETAILS (what v7.5 did) | CONTRACT-08c |
| M-R11 the target re-derived from the candidate — five prefill keys back | CONTRACT-02, CONTRACT-04, CONTRACT-08b, FLOW-01 |
| M-R12 the refusal flag no longer paired with the message | REFUSAL-07, DATE-TIME-06 |

**One honest note on the harness.** The first run reported M-R9 as a survivor. It was not:
the operator I wrote was `frozenset({} or {…})`, and `{} or X` evaluates to `X`, so the
mutant was equivalent to the original. It was nevertheless a real hole: ROUTING-02 only
iterated `REQUIRED_ROUTING_EDGES`, so a genuinely emptied set would have made the test
vacuous and M-R9a would have survived. ROUTING-02 now **derives** the required edges from
`TRIGGER_ORIGIN` and compares them with the constant, and both correct mutants die.

### 10.4 Test inventory

`tests/test_wild1_booking_flow_gate_and_contract.py` — 42 tests:
CONTRACT-01..09 (+08b, 08c) · **ROUTING-01..06** · REFUSAL-01..08 · GATE-01..08 (+05b) ·
HTTP-01..06 · WILD-01..02.

---

## 11. The commit — 20 authorised paths

Authorised by `…-CORRECTION-COMMIT`. **`git add` was given these twenty paths explicitly;
`git add .` was not used.**

```
backend/app/routes/flow_data_exchange.py
backend/app/services/booking_dispatch_gate.py          (new)
backend/app/services/booking_flow_service.py
backend/app/services/conversation_engine.py
backend/app/services/flow_contract.py                  (new)
backend/app/services/offer_lifecycle.py
meta/flows/booking/TARGET_v7.6-minimal-refusal.json    (new)
meta/flows/booking/make_minimal_target.py              (new)
meta/flows/booking/compare_flows.py                    (new)
meta/flows/booking/PUBLISHED_TO_TARGET_DELTA.json      (new)
tests/test_wild1_booking_flow_gate_and_contract.py     (new)
tests/test_l4_7w5_gate_a_flow_prefill_contract.py
tests/test_l4_3_scheduling_semantics.py
tests/test_l4_4_clean_wild_prep.py
tests/test_m18_business_logic.py
tests/test_m20_6c1_copy_polish.py
tests/test_m20_6d2_customer_reality.py
tests/test_sched_flow_dispatch.py
docs/operations/2026-10-29_BOOKING_FLOW_PUBLICATION_RUNBOOK.md   (new)
docs/Launch/2026-10-29_..._CORRECTION_CLOSEOUT_001.md            (this file, new)
```

**Explicitly excluded:** `docs/operations/RECOVERY_MANIFEST.md` (pre-existing worktree edit,
preserved, sha256 `c70cdb3921018dae…` unchanged); the candidate-derived `TARGET_v7.5`
(preserved under the evidence root, §4.1 †); all unrelated untracked `docs/Launch`
documents; all preserved Wild evidence; every runtime, Compose, secret and environment file.

### Pre-commit verification

| check | result |
|---|---|
| 1. staged set vs the 20 authorised paths, element-by-element | exact match, both directions |
| 2. `git diff --cached --check` | clean |
| 3. target sha256 vs the accepted value | `a25de8f43f5ada03…` — match |
| 4. regenerate the target from the published baseline | byte-identical (`cmp` clean) |
| 5. semantic delta: 4 fields, 2 TextBody components, the two edges, nothing else | 16/16 assertions pass |
| 6. secrets / credentials / WAMIDs / customer phone numbers / runtime config in the staged diff | none |
| 7. migration, model or schema change staged | none |
| 8. live isolation, seven flags | unchanged, all `false` |

**Check 5 found one defect in my own instrument, not in the artifact.** `compare_flows.py`
keyed each action by its component's layout *path*, so inserting the two TextBody
components shifted the Form from `children[3]` to `children[5]` and the Dropdown and Footer
actions underneath it were reported as changed. Their contents were byte-identical; the
comparator now keys actions by component identity (type + name + label), which is stable
across insertions. The delta was regenerated and check 5 then passed with no exemptions.

### Six pre-existing test fixtures, each justified

A bare `MagicMock()` settings object reads as OFF under `is True`, so six fixtures would
have begun asserting the refusal path by accident. Each gained
`settings.smart_booking_ce_enabled = True`: `test_sched_flow_dispatch.py`,
`test_l4_3_scheduling_semantics.py` (×2), `test_l4_4_clean_wild_prep.py`,
`test_m18_business_logic.py` (`_make_engine` plus 13 post-fixture `eng.settings = MagicMock()`
sites), `test_m20_6d2_customer_reality.py`, `test_m20_6c1_copy_polish.py`. That file already
used the idiom both ways at `:2109`/`:2173`. **No assertion was weakened** — the identity
comparison in §10.1 shows zero tests moved from failing to passing.

Eleven assertions in `test_l4_7w5_gate_a_flow_prefill_contract.py` were rewritten because
the owner has excluded the prefill rollout from this correction. Each carries a BEFORE /
AFTER / WHY / NOT WEAKENED note in its own docstring; §4.3 holds the proof that nothing
observable was lost, and PHONE-05 is strictly stronger.

---

## 12. Owner checkpoint — stopped before staging

1. **Exact published-to-target delta** — §4.1, §5: +4 APPOINTMENT fields, +2 APPOINTMENT
   components, +2 routing edges. Nothing else changes.
2. **Genuinely minimal?** Yes. Neither half can be dropped: the edges make the response
   valid (F3a), the fields and components make it readable (F3b). Zero candidate prefill.
3. **Corrected target sha256** —
   `a25de8f43f5ada03650541cd84fa42cb4a2a5b2d772d89ac520afcd3219c43b4`.
4. **Exact commit paths** — §11, **20** paths. `RECOVERY_MANIFEST.md` and the
   candidate-derived `TARGET_v7.5` both excluded.
5. **Proposed subject** — `fix(WILD1): the refusal names a screen the Flow can reach, and says why`
6. **Ordering** — §6, the conservative nine steps, backend first, Smart Booking OFF until
   both halves are verified.
7. **Meta rollback** — §7. **Version-revert is UNVERIFIED and not relied upon.** The
   mechanism of record is re-import and publish the preserved v7.3 artifact. The fastest
   kill is `SMART_BOOKING_CE_ENABLED=false`, which needs no Meta action.
8. **Remaining November 1 blockers** —
   * R-14 database privileges (unchanged, untouched here);
   * this commit, build, backend deploy and the step-3 OFF proof;
   * Meta validation + publication of `TARGET_v7.6` (step 5 also proves the `visible` binding);
   * bounded owner-present handset verification (steps 8–9);
   * operator destination/template configuration, still absent;
   * POST-LAUNCH-HARDENING-01 (A.5's blocking backstop) — **not** a launch blocker on the
     measured evidence: both production callers are prevented.

**W1F-W-01's conversation-memory acceptance remains PASSED.** November 1 remains fixed.

No live action occurred. No Meta contact. No preserved-row cleanup. No R-14 work. No
operator-template work. Nothing committed, staged, tagged or stashed.

STOP.
