PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W5-GATE3-G3-7F-R2-OWNER-POLICY-COMPLETION

## §0 RESULT

**CONDITIONAL — STOPPED on §13.1 and §13.6.**

Objective 1 (§5, the approved wording) is **IMPLEMENTED and committed**.
Objective 2 (§6–§8 and the §10 fifteen-scenario suite) is **NOT DELIVERED**, and no
implementation commit was made for it.

The nine wording identities did **not** close as the brief anticipated, and the reason is
the substantive finding of this milestone: **their failures are not wording failures.**
Eight of the nine assert `AI must not be called` or `Location Flow expected` — behavioural
contracts that cannot be satisfied without moving the reply-model call after reconciliation,
which is a conversation-engine phase change outside this milestone's bounded scope and which
broke twelve identities when attempted. One (the GBA coverage identity) was genuinely a
wording identity and is closed.

Nothing was pushed, built, deployed, migrated, enabled or run against Meta.

## §1 AUTHORITATIVE INPUTS — VERIFIED

| Input | Verified |
|---|---|
| Prior G3-7F implementation closeout | **492 lines, 28,082 bytes, sha256 `d3d7144868b116e263477e6171ee18a8a6114f2f8b21fa5d3761317e44a5fce4`** — exact match; begins literally with `PROJECT:`; `archive_project:` occurrences = 0 |
| Architecture audit `5c3894c` | present |
| Sequencing audit `5e1dd20` | present |
| Prelaunch canonical roadmap R2 | present, 320 lines, sha256 `f555b0111c9b4d234823e75e…` |

**Path discrepancy (non-material, reported as required):** the roadmap audit is at
`docs/Launch/2026-10-05_RIDECHECK_CRM_PRELAUNCH-CANONICAL-ROADMAP-R2_AUDIT_001.md`, not
`deliverables/…`. Same filename, same milestone; no `deliverables/` directory exists. Its
binding constraints were adopted: no phrase catalogue as architecture, one canonical writer
per state family, real customer language is durable evidence, G3-8 owns the pending-correction
predicate wiring.

The committed copy of the prior closeout was hash-verified **after** commit and is byte-identical.

## §2 BASELINE GATE — ALL MATCHED

| Item | Expected | Found |
|---|---|---|
| Branch | `fix/m21.1.1-primary-flow-regression` | same |
| HEAD | `c797f73` | `c797f731f07ddcc59396c065b62273db688fd1bd` |
| Parent | `5e1dd20` | same |
| Remote | `161aa8d6573c8fb316922c51d9af3cf4616d0cc2` | same |
| Ahead / behind | 28 / 0 | 28 / 0 |
| Tracked modifications | 0 | 0 |
| Runtime `GIT_SHA` | `840bce0` | `840bce0` |
| `OUTBOUND_ENABLED` | false | false |
| Correction flags | unset | `G=[] P=[]` |
| Deployed alembic | `20260918_hybrid_traces` | same |
| `pending_correction` / clarification table | absent | absent (0 / 0) |
| Business data | unchanged | 47 / 34 / 1 / 35 / 734 / 2 |
| Untracked probe + closeout files | untouched | untouched |

## §3 COMMIT GRAPH AND FILE LIST

```
5d9490e  fix(gate3): complete clarification and correction policy   ← implementation (§5 only)
3214e2f  docs(gate3): record G3-7F sequencing implementation        ← documentation adoption
c797f73  feat(gate3): reconcile evidence before clarification routing   ← prior HEAD
```

Two new local commits, neither amended, nothing pushed (ahead/behind **30/0**).
This closeout is uncommitted.

Commit `3214e2f`: 1 file, +492 — the prior closeout, unchanged.
Commit `5d9490e`: 4 files, +156 / −139.

| File | Δ | What |
|---|---|---|
| `backend/app/services/conversation_engine.py` | +84 / −83 | the two approved bodies; four competing bodies deleted; `_mark_service_question` |
| `tests/test_m20_6d2_customer_reality.py` | +34 / −8 | bodies repointed; attempt-history reset; two contradictory assertions strengthened |
| `tests/test_m18_business_logic.py` | +40 / −31 | GBA coverage identity migrated to the canonical body |
| `tests/test_l4_7w5_g3_7f_sequencing.py` | +12 / −3 | `seq_53` follows the detector into its helper |

## §4 THE TWO CANONICAL FLOW BODIES

Verified by importing the module, byte-for-byte against §5.1 and §5.2:

```
Location: "Para calcular correctamente los viáticos de la revisión, te dejo un formulario
           breve para indicarnos dónde se encuentra el vehículo. Realizamos revisiones en
           CABA y GBA."
Vehicle:  "Para identificar correctamente el vehículo y cotizar la revisión, te dejo un
           formulario breve para completar la marca, el modelo y el año."
```

`_LOC_FLOW_BODY_STANDARD`, `_LOC_FLOW_BODY_CONCERN`, `_VEH_FLOW_BODY_GENERIC` and
`_VEH_FLOW_BODY_GARBLED` are **deleted** from the engine — confirmed absent by attribute
check — rather than left unreachable, so no caller, flag or later edit can reach a second
wording. No third wording was introduced.

**Exactly-one-response:** the Flow body *is* the customer message. `test_seq_42` asserts
neither `_dispatch_vehicle_clarification_flow` nor `_dispatch_location_clarification_flow`
contains `_send_text_to_wa`, so no separate introductory text can accompany the Flow. The
concern-aware variant is gone: `_dispatch_location_flow_direct` retains the `concern`
parameter because the certified dispatch contract includes it, but it no longer selects
wording.

**The CABA/GBA sentence is a product statement, not a routing trigger.** Coverage routing
remains `_is_outside_coverage` (23-city frozenset, Priority 1), a separate authority.
Nothing matches on the body text.

## §5 THE NINE FORMER WORDING FAILURES — CLOSURE TABLE

| # | Identity | Status | Actual failing assertion now |
|---|---|---|---|
| 1 | `test_location_clarification_wording_covers_gba` | **CLOSED** | — (migrated to assert the canonical body states CABA **and** GBA) |
| 2 | `rc04_concern_dispatches_location_flow_directly` | OPEN | `AI must not be called` |
| 3 | `rc06_generic_auto_price_question_dispatches_vehicle_flow_directly` | OPEN | `AI must not be called` |
| 4 | `rc07_ranger_known_no_zone_dispatches_location_flow` | OPEN | `AI must not be called` |
| 5 | `rc08_garbled_vehicle_dispatches_vehicle_flow_directly` | OPEN | `AI must not be called` |
| 6 | `rc12a_benavides_typo_dispatches_location_flow_directly` | OPEN | `AI must not be called` |
| 7 | `rc14_price_question_after_faq_dispatches_location_flow` | OPEN | `Location Flow expected for price question after FAQ` |
| 8 | `rc16_bare_vehicle_message_dispatches_location_flow` | OPEN | `AI must not be called` |
| 9 | `rc16_known_vehicle_price_question_dispatches_standard_location_flow` | OPEN | `AI must not be called` |

**The body is no longer the problem in any of the nine.** Two of them (#8, #9) had the one
assertion that the owner decision made unsatisfiable — "the standard body present and the
concern body absent" — and those were **strengthened**, not weakened: they now assert
`assertIn(_LOCATION_CLARIFICATION_INTRO, blocked)`, exact equality against the whole
approved body, replacing a two-way substring comparison between bodies that no longer differ.

No test was loosened to a substring, to `sent=True`, or to "some Flow was dispatched".

### STOP-03 — why the remaining eight cannot close here

Three contracts are individually certified and cannot all hold at once:

1. **M20.6D (certified):** a turn that dispatches a clarification Flow calls no model.
2. **G3-7F sequencing (owner-selected architecture):** clarification is decided from the
   turn's *reconciled* proposal.
3. **The engine as it stands:** the reply model is consulted at `_reply_decision`
   **before** reconciliation (`_reconciled_reply_proposals`).

Satisfying (1) and (2) together requires hoisting reconciliation above the reply-model call.
That was implemented and measured in this milestone, and then reverted:

| Attempt | Result |
|---|---|
| Decision hoisted above the reply model | closed 5 of the 8 — **and added 12 newly failing identities**, including this milestone's own `seq_01`, `seq_02`, `seq_03`, `cl_34`, `seq_53`, five `br1_intent_gate` identities, `g37_32` and `g37f_26` |
| Hoist reverted | 0 newly added, 1 closed |

Hoisting reconciliation is a conversation-engine phase change — Option C of the sequencing
audit, rejected for this gate — and §12 of this brief forbids broad redesign. Per §13.6 the
measured 12-identity regression is a stop, not something to force.

**#7 (`rc14`) has a second, independent cause.** Its burst is intercepted by Layer D
(`_handle_general_information_ai`, the pre-existing FAQ bypass at the top of `_process_text`),
which answers the FAQ and returns before any clarification decision exists. Layer D is a
phrase authority that predates this work and sits outside this milestone's file set.

**#3 and #5 (`rc06`, `rc08`) have a third cause, and it is §13.1 exactly.** Their turns carry
**no typed vehicle evidence at all**: `lookup_vehicle` misses ("un auto", a garbled model),
no semantic evidence is present in that harness, and no candidate exists yet. The inherited
behaviour came solely from `_wants_price_quote` + `_is_generic_vehicle_text`. Reproducing it
requires a phrase rule, which §12 forbids and which this whole gate exists to remove.

Each of the eight was confirmed to fail **in isolation**, so none is test-order contamination.

## §6 OBJECTIVE 2 (§6–§8, §10) — NOT DELIVERED

No code was written for the customer-requested-change policy, dependent-state invalidation,
or the existing-booking boundary, and the fifteen-scenario real-handler suite was not built.
Accordingly there is **no result to report for scenarios 1–15**, and this closeout does not
claim one.

What exists today, from the prior milestone and unchanged here, is the typed foundation those
scenarios would be built on — stated so the next milestone starts from fact, not from this
milestone's intentions:

| Capability | State |
|---|---|
| Fully resolved correction routes to typed confirmation, never a Flow | implemented; unit-proven (`test_seq_26`) |
| An ungrounded target never reaches confirmation | unit-proven (`test_seq_28`) |
| A correction competing with another live proposal goes to a person | unit-proven (`test_seq_27`) |
| "Focus 2008" / "Gol 2008" never become a Peugeot 2008 | real-handler proven (`wild04r`, both unweakened) |
| One canonical writer (`_apply_candidate`) | held; `test_seq_55` |
| `g3_8_enforced` | pinned `False` |
| Dependent-state invalidation (quote, viáticos, token, schedule) | **not implemented** |
| New/additional-candidate focus isolation | **not implemented** |
| Ambiguous-target HOLD end to end | **not implemented** |
| Existing-booking fail-safe handoff | **not implemented** |

**G3-8 remains open.** Nothing in this milestone touched `ThreadRevision` or CRM `Revision`
synchronization, and no automatic booked-revision mutation was added — but neither was the
fail-safe handoff of §8, so the boundary is **unproven**, not proven safe. That is the honest
statement, and it is why Objective 2 is reported as not delivered rather than partially met.

## §7 CLARIFICATION LIFECYCLE INVARIANTS (§9) — RE-VERIFIED

Re-run and passing after this commit:

| Invariant | Evidence |
|---|---|
| One clarification decision per turn | `test_seq_05`, `test_seq_07` |
| Reconciliation before the decision | `test_seq_03`, `cl_34` |
| One reservation authority | `test_seq_06` (AST: no other method calls `.reserve(`) |
| One attempt per family per cycle | real PostgreSQL `UNIQUE(thread_id, cycle_key, family)` |
| Cycle `0` until operator reset | `test_seq_45`, `test_seq_47` |
| Vehicle before Location | `test_seq_24` |
| Resolved corrections consume no attempt | `test_seq_26` |
| Flag set only after a proven `wa_message_id` | `test_seq_39` |
| Blocked/failed leave the flag false | `test_seq_40`, `test_seq_41` |
| Spent attempt routes to a human | `test_seq_19` |
| Store unavailability fails closed | `test_seq_32`, `test_seq_33`, `test_seq_35` |
| No ordinary car treated as MOTO | `test_seq_56` |
| FAQ does not trigger a Flow from stored state | `test_seq_23`, `rc14`/`rc15` FAQ identities pass |

**Real PostgreSQL and migration proof re-run in a disposable container (removed afterwards):**
15/15 lifecycle tests pass, including the 2-session and 10-session reservation races; the
upgrade → verify → downgrade → re-upgrade sequence reproduces 280 identical columns and all
7 constraints, with `pending_correction` surviving the clarification downgrade. SQLite was
not used as concurrency evidence. **`crm_test` was not migrated.**

## §8 REGRESSION — IDENTITIES, NOT TOTALS

```
full sealed suite: 60 failed, 5852 passed, 91 skipped, 9 errors, 3971 subtests passed
identities: 69      FlushError raised: 0
```

| Comparison | Added | Closed / fixed |
|---|---|---|
| vs parent `c797f73` | **0** | **1** — `test_location_clarification_wording_covers_gba` |
| vs baseline `5c3894c` / `66ec6c8` (67 identities) | 8 | 6 |

The 8 vs baseline are items #2–#9 of §5, inherited from the parent commit and unchanged in
count by this milestone. The 6 baseline failures fixed by the sequencing milestone **remain
fixed**. All five PRESERVE identities pass, unweakened:

| PRESERVE identity | Status |
|---|---|
| `wild04_resolver_focus_2008_not_numeric_path` | PASS |
| `wild04_resolver_gol_2008_not_numeric_path` | PASS |
| `returning_obs_2_issue7_focus_pilar_new_candidate` | PASS |
| `rc18_outbound_blocked_candidate_persists_flag_false` | PASS |
| `rc19_successful_delivery_sets_flag_and_dedup_blocks_replay` | PASS |

No subtest failure is hidden: `SUBFAIL` lines are counted in the identity set and there are 0.

**§11's required result is NOT met.** "All nine wording identities closed" and "zero added
vs baseline" are not satisfied, and this closeout does not claim otherwise.

The §11 mutation/adversarial battery was **not built**. The existing suite already catches
five of its ten listed mutations (second decision site, routing before reconciliation, store
failure read as already-attempted, same-family double dispatch, fallback to the retired
concern/standard body — the last now structurally impossible, the constants being deleted).
The remaining five concern Objective 2 and have nothing to test against.

## §9 EVERY CHANGED EXISTING TEST, AND WHY

| Test | Change | Reason |
|---|---|---|
| `test_m20_6d2_customer_reality.py` imports | `_LOC_FLOW_BODY_*` / `_VEH_FLOW_BODY_*` bound to the two canonical bodies | the engine constants are deleted; each assertion still checks the **complete, exact** approved text |
| `rc16_bare_vehicle_message_dispatches_location_flow` | `assertFalse(concern in …)` → `assertIn(_LOCATION_CLARIFICATION_INTRO, blocked)` | the standard/concern distinction no longer exists, so the original pair was self-contradictory; replaced by exact-body equality — strictly stronger |
| `rc16_known_vehicle_price_question_dispatches_standard_location_flow` | same | same |
| `_CLEAN_TABLES` | `whatsapp_clarification_attempts` added, first (FK order) | attempt history is per-thread durable state and this suite reuses thread ids; without the reset a later test inherited an earlier test's spent attempt and was escalated instead of asked |
| `test_location_clarification_wording_covers_gba` | asserts the coverage signal on the canonical Location body instead of on a sent text | the free-text step it drove is retired; §5.1 puts the coverage statement in the body. Still asserts GBA is named and that coverage is **not** restricted to the city |
| `test_seq_53` | follows `_is_general_faq_or_soft_close` into `_mark_service_question`, and asserts neither the gate nor the helper can reach any dispatcher | the detector moved behind the one-directional helper; the assertion is broadened, not relaxed |

No real-customer case was deleted, loosened, converted to a substring assertion, or
reclassified as obsolete.

## §10 SAFETY / NO-DELTA PROOF

| Item | Before | After |
|---|---|---|
| Branch | `fix/m21.1.1-primary-flow-regression` | same |
| HEAD | `c797f73` | `5d9490e` (two new local commits) |
| Ahead / behind | 28 / 0 | 30 / 0 |
| Uncommitted tracked | 0 | 0 |
| Stash | 0 | 0 |
| Runtime `GIT_SHA` | `840bce0` | `840bce0` |
| `OUTBOUND_ENABLED` | false | false |
| Correction flags | `G=[] P=[]` | `G=[] P=[]` |
| Containers | `2c06976a4034` / `43879bd4be63` / `7afa22b8a0a0`, restarts 0/0/0 | identical, restarts 0/0/0 |
| Deployed alembic | `20260918_hybrid_traces` | `20260918_hybrid_traces` |
| Clarification table / `pending_correction` | absent / absent | absent / absent |
| Business data | 47 / 34 / 1 / 35 / 734 / 2 | 47 / 34 / 1 / 35 / 734 / 2 |
| Outbound ledger | 35 | 35 |

No Meta call, WhatsApp send, model call, n8n mutation, tester reset, `crm_test` migration,
build, deployment, container recreation or Wild occurred. The disposable PostgreSQL container
was removed; 3 containers run, as before. Unrelated untracked files were not deleted.

**Secret scan of the complete diff** (452 lines): Meta tokens 0, OpenAI keys 0, DSNs with
credentials 0, raw `549…` phone numbers 0, full WAMIDs 0, inline passwords 0. Hashes in this
artifact are truncated except the one the brief required in full.

## §11 SEPARATED VERDICTS

| # | Subject | Verdict |
|---|---|---|
| 1 | Wording — the two canonical bodies, exact | **PASS** |
| 2 | Wording — competing authority removed | **PASS** (deleted, not merely unreachable) |
| 3 | Wording — exactly-one-response preserved | **PASS** |
| 4 | Wording — CABA/GBA coverage signal restored | **PASS** |
| 5 | Wording — nine identities closed | **FAIL** — 1 of 9; the other 8 are not wording failures (STOP-03) |
| 6 | Correction confirmation (§6.1) | **NOT DELIVERED** |
| 7 | New-candidate authority (§6.2) | **NOT DELIVERED** |
| 8 | Ambiguity safety (§6.3) | **NOT DELIVERED** |
| 9 | Dependent-state invalidation (§7) | **NOT DELIVERED** |
| 10 | Existing-booking boundary (§8) | **NOT DELIVERED** — no unsafe mutation was added, but the fail-safe handoff is unproven |
| 11 | Clarification lifecycle invariants (§9) | **PASS** — all re-verified |
| 12 | Real-PostgreSQL and migration proof | **PASS** — 15/15, 280 columns identical |
| 13 | Regression credibility | **PASS on method** (identities, both baselines, subtests counted) / **FAIL on required result** (§11 not met) |
| 14 | Changed-test credibility | **PASS** — six changes listed with reasons; two strengthened; none weakened |
| 15 | Accumulated G3-7F range | **CONDITIONAL** — SEQ-02/03/04/05/06 closed; STOP-03 and Objective 2 open |
| 16 | Gate 3 | **NOT CLOSED** |
| 17 | Deployment readiness | **NOT READY** — two migrations pending, no image built |
| 18 | Wild readiness | **NOT READY** — outbound off, nothing deployed |
| 19 | Artifact compliance | **PASS** — literal three-line header, exact filename |

## §12 LIMITATIONS AND DEFERRED FINDINGS

1. **STOP-03** — the M20.6D no-model contract, the owner-selected reconcile-before-routing
   architecture, and the engine's current reply-model-before-reconciliation order cannot all
   hold. Closing it means moving the reply-model call after reconciliation: a phase change,
   measured here at 12 added identities, and outside this milestone's scope. **Owner decision
   required**: authorize the phase change as its own milestone, or accept that a clarified
   turn consults the reply model.
2. **Objective 2 entirely** — §6, §7, §8 and the fifteen §10 scenarios. Not started in code.
3. **`rc06` / `rc08`** — §13.1 confirmed: a generic or garbled vehicle mention with no
   semantic evidence and no catalogue hit carries no typed vehicle evidence. Reproducing the
   inherited behaviour needs a phrase rule. Either the semantic layer must be the source, or
   the owner accepts that these turns reach the reply model.
4. **`rc14` / Layer D** — the pre-existing FAQ bypass answers a mixed FAQ-plus-price burst
   before clarification is considered. Untouched; outside the bounded file set.
5. **Asymmetric required-next** — `_required_next_information_authorized` covers the location
   requirement only. The vehicle-side equivalent would have to fire where no candidate exists,
   and the only typed signal then available (`last_intent`) persists across turns, so it also
   fired on the following FAQ and soft-close turns — measured at 15 added identities and
   reverted. Recorded in the method's own docstring.
6. **SEQ-09 debt** — 89 inherited phrases remain in Priority 1 and the FAQ detector. Neither
   can ask the customer anything; the detector is now one-directional by construction.
7. **SEQ-10** — every live thread is cycle `0`; one attempt per family per thread until an
   operator reset.
8. **R2R-07 / G3-8** — open and untouched.
9. **`pending_correction.route_clarification`** — the superseded pure router remains in the
   tree, uncalled by CE (`test_seq_54`).
10. **Two migrations pending**, neither applied to `crm_test`.
11. **The §11 mutation battery** was not built; five of its ten mutations are already covered.

NEXT: FINAL INDEPENDENT REVIEW OF G3-7F-R2 OWNER-POLICY COMPLETION

STOP.
