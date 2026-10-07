PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L4.7W5-GATE3-G3-7G-RESPONSE-PHASE-ORDERING-AUDIT

## §1 EXECUTIVE CONCLUSION

**CONCLUSION C — a prerequisite architectural correction is required before phase separation.**

The owner-approved order is reachable, and the reply-generation role is already separable:
the engine *already* counts the two model roles at distinct boundaries
(`_turn_semantic_calls`, `_reply_calls`/`_reply_role`), both reset per turn. No new
orchestration framework, no second canonical writer and no phrase rule is needed.

But the naive move is unsafe, and the audit found four bounded prerequisites that must land
first. Three were invisible until this audit instrumented the roles separately:

| # | Prerequisite | Evidence |
|---|---|---|
| **P1** | Reset the per-turn clarification memo | `_turn_clarification_decision_obj` is assigned twice and read twice but **never reset in `handle()`** — the only per-turn field in that block that is not. Measured: `rc14` turn 2 reuses turn 1's verdict |
| **P2** | Remove reply-generated prose's proposal authority **in the deployed posture** | the deployed image `840bce0` has **no** `reply_renderer_demotion_enabled` field, so `_reply_renderer_demotion_on()` reads `False` and `_proposals = decision` — today, in production, the reply model is the candidate-proposal source |
| **P3** | Move the MOTO/category floor with the decision | under the reproduced hoist, `g37f_26` failed `60207 not less than 55542 : the floor must run BEFORE the writer` — owner policy 19 (no false MOTO) was broken by the move itself |
| **P4** | Convert Layer D from answer-and-return to register-and-continue | `_handle_general_information_ai` is one of **41 early returns before reconciliation** and carries reply-generation call site #2 |

The twelve "regressions" that stopped G3-7F-R2 are **not twelve business regressions**. Measured
in a disposable tree: **5 are stale source anchors**, **5 are missing harness state**, **1 is the
genuine P3 safety regression**, and **1 (`seq_53`) was misattributed to the hoist in the
G3-7F-R2 closeout** — it reproduces from a separate suppressor edit, not from the move.

Two corrections to my own G3-7F-R2 closeout are recorded in §9 and §11: Layer D is **not**
involved in `rc14`, and seven of the eight open identities fail on **reply generation only**,
with **zero** semantic calls.

## §2 BASELINE AND NO-DELTA PROOF

| Item | Required | Found (before) | Found (after) |
|---|---|---|---|
| Branch | `fix/m21.1.1-primary-flow-regression` | same | same |
| HEAD | `5d9490e` | `5d9490e7af4c433e68e61b9cbed8efc4a310a486` | unchanged |
| Parent | `3214e2f` | `3214e2f` | unchanged |
| Previous implementation parent | `c797f73` | `c797f73` | unchanged |
| Remote | `161aa8d6573c8fb316922c51d9af3cf4616d0cc2` | same | same |
| Ahead / behind | 30 / 0 | 30 / 0 | 30 / 0 |
| Tracked modifications | clean | 0 | 0 |
| Stash | — | 0 | 0 |
| Runtime `GIT_SHA` | `840bce0` | `840bce0` | `840bce0` |
| `OUTBOUND_ENABLED` | false | false | false |
| Correction flags | unset | `G=[] P=[]` | `G=[] P=[]` |
| Deployed alembic | `20260918_hybrid_traces` | same | same |
| `pending_correction` / clarification table | absent | 0 / 0 | 0 / 0 |
| Business data | 47 / 34 / 1 / 35 / 734 / 2 | exact | exact |
| Containers | `2c06976a4034` / `43879bd4be63` / `7afa22b8a0a0` | restarts 0/0/0 | restarts 0/0/0 |

**AUDIT ONLY — nothing was changed.** No application code, test, prompt, schema, migration,
configuration, corpus file or commit. No OpenAI, Meta, WhatsApp or n8n call. No outbound
message, tester reset, build, deployment, container restart, `crm_test` migration or Wild.
All instrumentation was harness-local method wrapping inside sealed `--network none`
containers, plus one disposable `git archive` tree that carries no git state.

## §3 AUTHORITATIVE ARTIFACT VERIFICATION

| Artifact | Lines | Bytes | sha256 (16) | Header |
|---|---|---|---|---|
| Prelaunch canonical roadmap R2 | 320 | 18,430 | `f555b0111c9b4d23` | `PROJECT:` |
| G3-7F clarification-lifecycle architectural review | 709 | 41,620 | `b24428edaa837ccb` | `PROJECT:` |
| G3-7F hybrid-evidence sequencing audit | 716 | 45,759 | `c9f8cb83c921bc05` | `PROJECT:` |
| G3-7F sequencing implementation closeout | **492** | **28,082** | `d3d7144868b116e2` | `PROJECT:` |
| G3-7F-R2 owner-policy completion closeout | **351** | **20,982** | `b83816db40cab599` | `PROJECT:` |
| G3-5D response-composition-authority audit | 663 | — | `a89070db7745e246` | `PROJECT:` |

All six begin literally with `PROJECT:`; zero contain `archive_project:`. The 492-line and
351-line artifacts match the sizes the brief named. The G3-5E and G3-5F response-plan
closeouts were read for model-call and response-source authority.

**Non-material path discrepancy:** the roadmap audit is at `docs/Launch/…`, not
`deliverables/…`; no `deliverables/` directory exists. Same filename and milestone.

Adopted roadmap constraints: no phrase catalogue as architecture; one canonical writer per
state family; real customer language is durable evidence; G3-8 owns the pending-correction
predicate wiring.

## §4 CURRENT EXECUTABLE PHASE GRAPH

`ConversationEngine.handle()` [3308..3454] → `_handle()` [3458..3525] (68 lines, delegates)
→ **`_process_text()` [6785..8274] — 1,490 lines**, which contains every phase below.

Order proven by source index at HEAD, not by name or comment:

| Line | Phase | Role |
|---|---|---|
| 6878 | `_canonical_burst(...)` | **burst identity** |
| 6901 | `_run_shadow_understand(...)` | **semantic dispatch** (sync method, submits `ShadowJob`) |
| 6950 | `_handle_general_information_ai(...)` | **Layer D — reply-gen #2, then RETURN** |
| 7303 | `_apply_inspection_zone` | mutation |
| 7342 | `_create_candidate_from_catalog` | mutation |
| 7349 | `_apply_zone_from_text` | mutation |
| 7406 | `lookup_vehicle(all_recent_text)` | **deterministic vehicle evidence** |
| 7419 / 7439 / 7572 | `_create_candidate_from_catalog` | mutation |
| 7578 | `_apply_vehicle_identity` | mutation |
| 7635 | `_apply_zone_from_text` | mutation |
| 7652 | `_routing_gate(...)` | non-clarification routing (Priority 1 + FAQ marker) |
| 7658 | `_check_fallback_flow_triggers(...)` | retired; returns `None` |
| **7688** | `decision, _admission = self._reply_decision(...)` | **REPLY GENERATION #1** |
| **7726** | `_reconciled_reply_proposals(ctx, state)` | **RECONCILIATION** |
| 7731 | `_apply_extracted` | mutation |
| **7789** | `_turn_clarification_decision(...)` | **clarification decision** |
| 7802 / 7886 | `_apply_candidate(...)` | **canonical writer** |
| 7807 | `_execute_clarification(...)` | reservation + Flow dispatch |
| 8010–8067 | `_apply_inspection_zone` ×4, `_apply_vehicle_identity` ×2 | mutation |

**Three ordering violations against the owner-approved phase order:**

1. **Reply generation precedes reconciliation by 38 lines** (7688 → 7726).
2. **Layer D returns 776 lines before reconciliation**, having already made a
   reply-generation call, and can therefore discard every other proposition in the burst.
3. **Eight canonical mutation sites run before reconciliation** (7303–7635), i.e. before the
   final authorized action is known.

### §4.1 Branch matrix

| Turn shape | burst | semantic | Layer D | reply-gen | reconcile | clarification | writer | transport |
|---|---|---|---|---|---|---|---|---|
| Fixed Flow (vehicle/location) | ✔ | dispatched | — | **✔ (wasted)** | ✔ | ✔ | partial | Flow |
| FAQ only | ✔ | dispatched | **✔ RETURN** | ✔ (#2) | **✗** | **✗** | — | text |
| Mixed FAQ + service | ✔ | dispatched | **✔ RETURN** | ✔ (#2) | **✗** | **✗** | — | text (service lost) |
| Normal quote | ✔ | dispatched | — | ✔ | ✔ | ✔ | ✔ | text |
| Correction | ✔ | dispatched | — | ✔ | ✔ | ✔ | ✔ | text/confirmation |
| Human handoff | ✔ | dispatched | — | ✔ or — | ✔ or ✗ | ✔ | — | text |
| Semantic timeout | ✔ | `None` | — | ✔ | ✔ | ✔ (degraded) | ✔ | text |
| Outbound blocked | ✔ | dispatched | — | ✔ | ✔ | ✔ | per route | `blocked_dispatch` |

### §4.2 Early returns before reconciliation — **41**

Measured by AST over `_process_text` below line 7726. Representative, with the
proposition each can discard:

| Line | Return | Can discard |
|---|---|---|
| 6907 | `_motorcycle_human_handoff` | everything else in the burst |
| 6913 | `_handle_phone_call_escalation` | everything else |
| 6919 | explicit-service gate | vehicle/location propositions |
| **6950** | **Layer D** | **the whole service action** |
| 6961 | inspectability gate | vehicle/location |
| 6969 | website form | everything else |
| 7028/7031 | quoted acceptance | clarification need |
| 7064–7286 | 13 scheduling/availability returns | vehicle/location clarification |
| 7317/7319 | locality proposal | vehicle clarification |
| 7381–7552 | 6 fuzzy/numeric vehicle returns | location clarification |
| 7611/7620 | clarification/intent handlers | each other |
| 7639 | early zone | vehicle clarification |
| 7656/7662 | gate / retired trigger | — |
| 7668 | phone escalation | everything else |
| 7719 | deferred interest | everything else |

### §4.3 Mutations before the authorized action is known — **8 sites**

`_apply_inspection_zone` (7303), `_create_candidate_from_catalog` (7342, 7419, 7439, 7572),
`_apply_zone_from_text` (7349, 7635), `_apply_vehicle_identity` (7578). Each writes canonical
candidate or locality state before reconciliation (7726) and before the clarification
decision (7789). They pass the certified writers' own guards, so they are not unsafe
individually; they are, collectively, why "mutate only after an authorized action" is not
yet true.

## §5 MODEL-CALL SITES, SEPARATED BY ROLE

**Three call sites, two roles.** Confirmed at HEAD; G3-5D's line numbers have since moved.

| # | Role | Site | Prompt identity | Model resolution |
|---|---|---|---|---|
| 1 | **semantic interpretation** | `semantic_interpreter.py:682 _call_openai` → `urlopen:705`, entered via `interpret():714` | `understand/1.x` — **versioned** | `settings.openai_chat_model or "gpt-4o-mini"` |
| 2 | **reply generation** | `conversation_engine.py:7688` → `_reply_decision:11998` → `_call_openai:12262` → `_call_openai_inner:12291` → `urlopen:12317` | `_build_ai_messages`, **no version constant** | same setting |
| 3 | **reply generation** | `conversation_engine.py:10747` (`_handle_general_information_ai`) | same funnel, same unversioned prompt | same setting |

Sites 2 and 3 share **one** boundary (`_call_openai`), which is the seam the migration needs.
The two roles use **different** urlopen sites, in different modules.

### §5.1 The six required answers

1. **One semantic interpretation per burst?** Yes. `_turn_semantic_calls` is reset per turn
   and incremented once inside the single `interpreter.interpret`; the accessor
   `_semantic_turn_evidence()` is a bounded wait with no second call. G3-5D measured 1.
2. **Reply-generation calls per turn?** 0 or 1. Measured 1 on every one of the eight open
   identities; `rc14` showed 2 **across two turns**, not two in one turn.
3. **Can a fixed Flow incur a reply-generation call before the Flow is selected?**
   **YES — measured.** All five Flow-selecting identities made exactly 1 reply-generation
   call at line 7688 before the decision at 7789. The generated reply is discarded.
4. **Can reply-generated output still influence canonical state?** **YES, and it does in the
   deployed posture.** With demotion off, `_proposals = decision` and `_apply_extracted` /
   `_apply_candidate` consume the model's `extracted` and `candidate`. The deployed image
   `840bce0` has no `reply_renderer_demotion_enabled` field at all, so
   `_reply_renderer_demotion_on()` returns `False` there. At HEAD the field exists and the
   env default is `"true"`. **Deploying HEAD silently flips the proposal source.**
5. **Do "AI must not be called" tests distinguish the roles?** **NO.** They use
   `@patch("urllib.request.urlopen")`, and both modules reach `urlrequest.urlopen`, so one
   mock catches both roles.
6. **Minimal seam needed?** **None — it already exists.** `_call_openai` sets `_ai_invoked`,
   increments `_reply_calls` and records `_reply_role`, `_reply_status`, `_reply_latency_ms`
   and `_reply_burst`; `handle()` resets all of them per turn (lines 3341–3347), alongside
   `_turn_semantic_calls` (3339). The migration is to assert on these counters instead of on
   a shared urlopen mock.

### §5.2 Trace attribution truth

`semantic_model_calls` counts only the interpreter. `_reply_calls` exists on the engine but
**is not surfaced as a trace field**, so an operator reading a trace cannot see that a
clarification turn paid for a discarded reply. That is the one trace gap.

## §6 THE EIGHT OPEN IDENTITIES — MEASURED MATRIX

Role-separated counters, production posture (`_reply_renderer_demotion_on() = True`),
semantic interpreter disabled as the harness configures it, network off.

| Case | sem | reply | recon | clar | Layer D | Route selected | Body in ledger | Failing assertion |
|---|---|---|---|---|---|---|---|---|
| `rc04` | **0** | 1 | ✔ | ✔ | **0** | `LOCATION_FLOW_REQUIRED` / `LOCATION_UNRESOLVED` | **LOCATION (canonical)** | `1 != 0 : AI must not be called` |
| `rc06` | **0** | 1 | ✔ | ✔ | **0** | **none** | none | `1 != 0 : AI must not be called` |
| `rc07` | **0** | 1 | ✔ | ✔ | **0** | `LOCATION_FLOW_REQUIRED` / `LOCATION_UNRESOLVED` | **LOCATION (canonical)** | `1 != 0` (model count) |
| `rc08` | **0** | 1 | ✔ | ✔ | **0** | **none** | none | `1 != 0` (model count) |
| `rc12a` | **0** | 1 | ✔ | ✔ | **0** | `LOCATION_FLOW_REQUIRED` / `LOCATION_UNRESOLVED` | **LOCATION (canonical)** | `1 != 0` (model count) |
| `rc14` | **0** | 2 (two turns) | ✔✔ | ✔✔ | **0** | `NO_CLARIFICATION_REQUIRED` / `RESOLVED` ×2 | none | `Location Flow expected…` |
| `rc16a` | **0** | 1 | ✔ | ✔ | **0** | `LOCATION_FLOW_REQUIRED` / `LOCATION_UNRESOLVED` | **LOCATION (canonical)** | `1 != 0 : AI must not be called` |
| `rc16b` | **0** | 1 | ✔ | ✔ | **0** | `LOCATION_FLOW_REQUIRED` / `LOCATION_UNRESOLVED` | **LOCATION (canonical)** | `1 != 0 : AI must not be called` |

Flow selection was verified independently of the aborting count assertion, by recording
`_execute_clarification`'s decision and inspecting the blocked outbound ledger.

### Classification

| Class | Cases | Verdict |
|---|---|---|
| **Model-role coupling** — correct Flow, correct canonical body, only the shared model count fails | `rc04`, `rc07`, `rc12a`, `rc16a`, `rc16b` | the assertion is a **valid business invariant expressed against the wrong thing**: decompose to `_reply_calls == 0`, leaving `_turn_semantic_calls` free |
| **Degraded-mode fixture + representational question** — no typed vehicle evidence at all | `rc06`, `rc08` | see §7 |
| **Latent production defect (P1)** — per-turn memo never reset | `rc14` | see §8 |

**Correction to the G3-7F-R2 closeout:** it attributed `rc06`/`rc08` to "needing phrase
rules" and `rc14` to Layer D. The measurement shows Layer D is invoked **zero** times in all
eight, and `rc14`'s cause is the un-reset memo. No case was deleted, renamed, xfailed or
weakened; all eight customer inputs remain durable evidence.

## §7 GENERIC AND GARBLED VEHICLE TURNS — `rc06`, `rc08`

Inputs (durable corpus evidence, preserved verbatim):
`rc06` — "Quiero revisar un auto antes de comprarlo. ¿Cuánto sale y cómo hacen para ir hasta
donde está?"; `rc08` — a garbled/unrecognised model string.

### Normal hybrid operation

**Can the semantic schema represent "discussing a vehicle, requesting a quote, identity
unresolved"?** **Yes.** `turn-evidence/1.4` carries `ServiceIntentKind.QUOTE_REQUEST` and
`ServiceIntentKind.INSPECTION` independently of vehicle identity, and
`_required_next_information_authorized` already admits those two intents. The representation
exists; nothing new is needed.

**Does the current prompt actually produce it?** **NOT PROVEN HERE.** §14 forbids a live
model call, and the measured runs show `_turn_semantic_calls = 0` — the interpreter is
disabled in this harness, so these turns were decided with deterministic evidence only.
Proving coverage requires running the existing corpus against the frozen
`understand/1.x` + `gpt-4o-mini`, which is a corpus-evaluation task, not an audit action.
**This is the single missing proof and it is named in §18.**

### Semantic unavailable / timeout / malformed

Without semantic evidence, CE knows only: `lookup_vehicle` missed, no numeric-model match,
no candidate exists, `last_intent` may carry a *stored* `PREPURCHASE_INSPECTION` from an
earlier turn. **There is no typed current-turn evidence that this turn concerns a vehicle.**

The G3-7F-R2 attempt to derive it from stored `last_intent` was measured and reverted: it
fired on the following FAQ and soft-close turns too, adding 15 identities. Recorded in
`_required_next_information_authorized`'s own docstring.

**Required safe outcome:** no Vehicle Flow authority may be invented from phrases. With no
typed domain evidence the turn must take an already-authorized safe path — the reply model
for an ordinary conversational turn, or HOLD/generic human where progression would be
unsafe. `rc06`/`rc08` therefore **cannot be closed by the phase change alone**; they close
only if the semantic interpreter supplies the domain evidence, and they must be re-expressed
as semantic-enabled cases with an explicit degraded-mode twin.

## §8 MIXED FAQ AND SERVICE ACTION — `rc14` AND LAYER D

### `rc14` is not a Layer D case

Measured: Layer D invoked **0** times; both turns reached the clarification decision; both
returned `NO_CLARIFICATION_REQUIRED / RESOLVED`; `_required_next_information_authorized` ran
**once**, on turn 1, with `focus=('Ford','Focus','AUTO')`, `service_question=True` (correctly
suppressed for an FAQ turn) → `False`.

Turn 2 never re-evaluated. Root cause: **`_turn_clarification_decision_obj` is the only
per-turn field in `handle()`'s reset block that is never reset** (assigned at 4819 and 4862,
read at 4255 and 4726, reset nowhere). Turn 2 returned turn 1's memo.

**Production reachability:** both production construction sites build a fresh engine per
request — `backend/app/api/conversation.py:184` (the canonical `/api/conversation/handle`)
and `backend/app/routes/whatsapp.py:123` (the direct webhook, disabled in production). So
the memo does **not** leak in production today. It is a **latent** defect: any batching,
worker loop or long-lived engine would silently reuse the first turn's clarification verdict.
**P1, one line, and it must land before the phase change.**

### Layer D remains a real architectural problem

`_handle_general_information_ai` at 6950 answers and returns, 776 lines before
reconciliation, carrying reply-generation call site #2. A burst containing both an FAQ and a
service action loses the service action.

**Smallest seam:** Layer D already computes an answer; it must *register* a response-plan
item and continue instead of returning. The response-plan foundation needed for this already
exists — `LiveResponsePlan`, `Proposition`, `PropositionId`, `_ordered_included()` and
`plan.compose()` from G3-5E/G3-5F — so the change is a call-shape change, not new machinery:
replace `return self._handle_general_information_ai(...)` with a registration call plus
fall-through, and let the existing composer decide the final message set.

| Question | Answer |
|---|---|
| Factual truth source | unchanged — the canonical FAQ/hours authority; **DEF-04 hours-truth protection is untouched** because the answer text keeps its current producer and verification |
| Coexistence | an unrelated factual FAQ may accompany a Flow or a HOLD; it may never suppress a Vehicle/Location Flow, a correction confirmation, a quote action or a handoff |
| Message count | the Flow body is the whole message when a Flow is selected; a secondary FAQ item may be composed only through `plan.compose()`, which already deduplicates, with a bounded cap |
| Fixed-Flow wording | authoritative and unchanged when a clarification Flow is selected |
| Inspector | principal action and secondary FAQ answer shown as separate rows, with response source per item |

Not implemented here, as instructed.

## §9 THE TWELVE FAILED-HOIST IDENTITIES — REPRODUCED AND CLASSIFIED

Reproduced by re-applying the reverted hoist in a disposable `git archive` tree. **11 of 12
reproduce**; `seq_53` does not.

| Full node ID | Failure | Class |
|---|---|---|
| `tests/test_l4_7w5_g3_7f_sequencing.py::A_Sequencing::test_seq_01_semantic_is_dispatched_before_reconciliation` | `'_proposals = (self._reconciled_reply_proposals(' not found` | **stale source anchor** |
| `…::A_Sequencing::test_seq_02_typed_deterministic_evidence_exists_before_reconciliation` | same | stale source anchor |
| `…::A_Sequencing::test_seq_03_the_reconciled_proposal_exists_before_the_decision` | same | stale source anchor |
| `tests/test_l4_7w5_g3_7f_clarification_lifecycle.py::F_Placement::test_cl_34_the_decision_runs_after_reconciliation_and_before_mutation` | `ValueError: substring not found` | stale source anchor |
| `tests/test_l4_7w5_g3_7_reply_renderer_demotion.py::D_BoundaryContract::test_g37_32_the_consumers_read_the_proposals_not_the_decision` | `'_proposals = (self._reconciled_reply_proposals(ctx, state)' not found` | stale source anchor |
| `tests/test_br1_intent_gate.py::TestBR1Row5BareVehicle::test_row5_catalog_hit_sets_intent` | `0 != 1 : CONTINUE_SET must call AI` | **missing harness state** |
| `…::TestBR1Rows8to10StructuredCommercial::test_row8_vehicle_plus_location` | same | missing harness state |
| `…::TestBR1Rows8to10StructuredCommercial::test_row10_vehicle_plus_price` | same | missing harness state |
| `…::TestBR1Row16FAQWithVehicle::test_row16_faq_plus_vehicle_sets_intent` | `0 != 1 : Row 16 must call AI` | missing harness state |
| `…::TestBR1Row17FAQWithExplicitInspection::test_row17_quiero_revisar_plus_faq` | `0 != 1 : CONTINUE_SET must call AI` | missing harness state |
| `tests/test_l4_7w5_g3_7f_grounding_floor.py::F_CategoryFloor::test_g37f_26_the_floor_is_wired_before_the_writer` | `60207 not less than 55542 : the floor must run BEFORE the writer` | **TRUE SAFETY REGRESSION** |
| `tests/test_l4_7w5_g3_7f_sequencing.py::G_AuthorityRemoval::test_seq_53_the_surviving_phrase_sets_cannot_reach_a_clarification_flow` | **passes** under the hoist | **misattributed** in the G3-7F-R2 closeout; it reproduces from the separate suppressor edit |

### What each class needs

- **Stale anchors (5).** The tests assert phase order by searching for a literal *assignment
  statement*. The seam is to anchor on the *call site* (`self._reconciled_reply_proposals(`)
  or, better, on an explicit phase marker, so a legitimate move does not read as a regression.
  Eventual assertion: the reconciliation call precedes the decision call precedes the writer
  call, by call-site index, independent of how the result is assigned.
- **br1 (5).** These harnesses stub `_routing_gate`, `_apply_candidate` and
  `_create_candidate_from_catalog` as `MagicMock`, so after the hoist the reconciled proposal
  carries no identity and the decision selects a Vehicle Flow instead of reaching the reply
  model. Their real claim is "this turn is AI-handled and persists `last_intent`". Eventual
  assertion: `_reply_calls == 1` **and** `last_intent` persisted, with the harness supplying
  a resolved candidate so clarification is genuinely not required.
- **`g37f_26` (1).** Genuine. The MOTO/category floor (owner policy 19 — an unplaceable
  category is never announced as a motorcycle) ended up *after* the writer. **P3: the floor
  must move with the decision, as one block.** Eventual assertion unchanged — the floor
  precedes the writer — which is exactly the invariant that caught it.

### Required prerequisites, from this evidence

Not "a new orchestration method". The bounded set is: **P1** memo reset; **P2** strip
reply-generated prose's proposal authority in the deployed posture; **P3** move the category
floor with the decision; **P4** Layer D register-and-continue. Splitting evidence
reconciliation from response composition is **not** required — `_reconciled_reply_proposals`
is already a pure read, and the single reconciliation per turn is already the contract.

## §10 OPTIONS

### Option A — keep reply generation before reconciliation

Every clarification turn pays for a discarded generated reply (measured: 5 of 5 Flow turns).
The reply model keeps proposal authority in the deployed posture, so prose can still reach
canonical state. Mixed FAQ+service turns keep losing the service action at Layer D. Auditing
stays impossible from the trace, because `_reply_calls` is not a trace field. **Rejected:
it contradicts §2 of the owner decision.**

### Option B — evidence → reconciliation → conditional reply generation

The owner-approved target, and feasible: the role counters exist, reconciliation is already a
pure single-call read, the clarification decision is already memoized, the response-plan
foundation already exists, and the canonical writer is already single. **Blocked only by
P1–P4**, each independently bounded and independently testable.

### Option C — another architecture

**Not required.** No source evidence demands a second orchestration framework, a second
canonical writer, or a redesign of reconciliation. `_process_text`'s 1,490 lines and 41 early
returns are a decomposition problem, not an architecture problem, and decomposing them is not
a prerequisite for the phase move.

## §11 SELECTED TARGET ARCHITECTURE

```
 1 burst            _canonical_burst                      → CanonicalBurst (identity, fingerprint)
 2 semantic         _run_shadow_understand (dispatch once) → provider; bounded wait on demand
 3 deterministic    lookup_vehicle, zone/year/locality     → typed observations
 4 reconcile        _reconciled_reply_proposals (pure)     → ReconciledProposal (one per turn)
 5 decide           _turn_clarification_decision (memo)    → TurnClarificationDecision
                    + MOTO/category floor, same block     ← P3
 6 mutate           _apply_candidate / _apply_extracted    → canonical state, action-authorized
 7 plan             LiveResponsePlan.register(...)         ← Layer D registers here, P4
 8 reply-gen        _reply_decision — ONLY if the plan needs prose
 9 dispatch         OutboundSafetyGate                     → sole transport authority
```

**Reply-generation admission rule.** `_reply_decision` may be called only after phase 5, and
only when the authorized plan contains at least one item whose renderer is
`REPLY_MODEL`. A plan consisting solely of fixed items — Vehicle Flow, Location Flow, handoff
acknowledgement, deterministic refusal, coverage response, blocked-outbound audit — admits no
reply-generation call.

**Fixed-response bypass rule.** `decision.requires_clarification` or any
`RendererSource.DETERMINISTIC_TEMPLATE`-only plan ⇒ `_reply_calls` must remain 0.

**Mutation boundary.** Phase 6 only, through the existing writers. Reply-generated prose has
**no** mutation authority: `_REPLY_MUTATION_FIELDS` must be stripped at the boundary in both
postures (P2), not only when the demotion flag happens to be on.

**Multi-proposition rule.** Every actionable proposition is registered in phase 7; none is
discarded by an early return. `plan.compose()` deduplicates and bounds the message count; a
selected Flow's body remains the whole message.

**What moves, splits, narrows or dies**

| Method | Change |
|---|---|
| `_handle_general_information_ai` | **narrows** — registers a plan item, no longer returns |
| `_reply_decision` | **moves** to phase 8; gains an admission precondition |
| `_turn_clarification_decision` | unchanged, plus a per-turn reset (P1) |
| MOTO/category floor | **moves** with the decision (P3) |
| `_reconciled_reply_proposals` | unchanged — already pure, already once per turn |
| `_apply_candidate` | unchanged — remains the single canonical writer |
| `_check_fallback_flow_triggers` | already retired; **can be deleted** once the 5 anchor tests are re-anchored |
| 8 pre-reconciliation mutation sites | **narrow** — must become proposals or move behind phase 6 (staged, not in the first milestone) |

## §12 TYPED INTERFACES AND AUTHORITY BOUNDARIES

| Phase | Input | Output | May mutate | May dispatch |
|---|---|---|---|---|
| burst | inbound rows | `CanonicalBurst` | no | no |
| semantic | burst texts | `TurnEvidence \| None` | **no** | no |
| deterministic | burst texts + canonical state | typed observations | no | no |
| reconcile | admitted evidence + canonical state | `ReconciledProposal` | **no** | no |
| decide | `ReconciledProposal` + attempt history + flags | `TurnClarificationDecision` | no | no |
| mutate | authorized proposal | canonical state | **YES — only here** | no |
| plan | propositions | `LiveResponsePlan` | no | no |
| reply-gen | plan + context | prose | **no** | no |
| dispatch | composed message | transport result | status only | **YES — only here** |

## §13 TIMEOUT AND DEGRADED-MODE TABLE

| Condition | Semantic | Reply-gen | Classification | Outcome |
|---|---|---|---|---|
| semantic OK | 1 | per plan | `SEMANTIC_ADMITTED` | normal |
| semantic timeout | 1 (unconsumed) | per plan | `SEMANTIC_TIMEOUT` | deterministic evidence governs; no phrase fallback |
| semantic malformed | 1 | per plan | `SEMANTIC_UNPARSEABLE` | as timeout |
| semantic disabled | 0 | per plan | `SEMANTIC_DISABLED` | deterministic only |
| late semantic result | ignored | — | `SEMANTIC_LATE_DISCARDED` | must not mutate a completed turn |
| no typed domain evidence | any | per plan | `DOMAIN_NOT_PROVEN` | no Flow authority invented; safe existing path |
| attempt store unavailable | any | 0 | `CLARIFICATION_STORE_UNAVAILABLE` | no Flow, no claim, generic human |
| reply-gen error | — | 1 failed | `REPLY_ERROR` | existing deterministic fallback |
| outbound blocked | any | per plan | `OUTBOUND_BLOCKED` | `blocked_dispatch`, audit keeps the composed body |

## §14 RESPONSE-PLAN AND MESSAGE-COUNT POLICY

One plan execution per turn. Items registered, then composed once. A selected clarification
Flow contributes exactly one message whose body is the approved introduction, and admits no
companion text. A secondary factual FAQ item may be composed alongside a HOLD or a non-Flow
principal action, subject to a bounded cap and the existing deduplication in
`plan.compose()`. `included()` continues to report what actually reached the customer, so a
degraded composition cannot silently claim an item was delivered.

## §15 TRACE AND INSPECTOR REQUIREMENTS

Backward-compatible, additive:

- `reply_model_calls` — surface the existing `_reply_calls`; the one real trace gap.
- `reply_admission` — why reply generation was or was not admitted.
- `response_items[]` — per item: proposition id, renderer source, included yes/no.
- `phase_order` — the executed phase sequence, so an operator can see the order rather than
  infer it.
- keep `semantic_model_calls` meaning exactly what it means today.

Inspector must show, as distinct rows: semantic evidence, deterministic evidence,
reconciliation result, action authority, response source per item, transport result. Older
contracts must keep rendering what they have, by the payload-key gating already used for 1.9.

## §16 TEST-MIGRATION PLAN (DESIGN ONLY)

1. **Role counters.** Replace `@patch("urllib.request.urlopen")` + `call_count` with
   assertions on `eng._reply_calls` and `eng._turn_semantic_calls`. No new seam needed.
2. **Eight open identities.** Five become `_reply_calls == 0` with the Flow, route, reason,
   reservation and exact canonical body still asserted. `rc06`/`rc08` become semantic-enabled
   cases plus explicit degraded-mode twins. `rc14` becomes a two-turn memo-reset test.
3. **Twelve hoist identities.** Five re-anchored on call sites; five br1 given a resolved
   candidate and asserted on `_reply_calls == 1` plus `last_intent`; `g37f_26` unchanged —
   it is the guard that caught P3.
4. **Mixed FAQ+service.** The FAQ answer and the principal action both present; bounded
   message count; no duplicate.
5. **Fixed paths.** Vehicle Flow, Location Flow, correction confirmation, handoff: semantic
   permitted, `_reply_calls == 0`.
6. **No mutation from prose**, in both demotion postures.
7. **PRESERVE ×5**, PostgreSQL lifecycle/concurrency, migration upgrade/downgrade.
8. **Identity diff** against `5d9490e`, `c797f73` and the 67-identity baseline.

Classification of every assertion that changes:

| Class | Cases |
|---|---|
| business invariant preserved | the five Flow identities; `g37f_26`; PRESERVE ×5 |
| model-role coupling corrected | all `AI must not be called`; five br1 |
| degraded-mode expectation corrected | `rc06`, `rc08` |
| harness defect corrected | five br1 fixtures; five stale anchors |
| genuine behaviour change requiring owner approval | Layer D register-and-continue (message count on mixed turns) |

No assertion weakens to "something happened".

## §17 FILE-LEVEL IMPLEMENTATION SEQUENCE

1. `conversation_engine.py` — **P1**: reset `_turn_clarification_decision_obj` in `handle()`.
2. `conversation_engine.py` — **P2**: strip `_REPLY_MUTATION_FIELDS` at the boundary in both
   postures; prove no prose reaches canonical state.
3. `conversation_engine.py` — **P3**: move the MOTO/category floor into the decision block.
4. `tests/*` — re-anchor the five structural tests; repair the five br1 fixtures.
5. `conversation_engine.py` + `response_composition.py` — **P4**: Layer D registers instead of
   returning.
6. `conversation_engine.py` — move `_reply_decision` behind the plan's admission rule.
7. `schemas/hybrid_trace.py`, `ui/hybrid_trace_view.py` — additive trace/Inspector fields.
8. `tests/*` — the §16 suite.

## §18 RISKS, ROLLBACK BOUNDARY, STOP CONDITIONS FOR THE IMPLEMENTATION

Risks: the 41 early returns make phase 7 registration easy to miss on a branch; the 8
pre-reconciliation mutation sites are not addressed by steps 1–8 and remain a staged
follow-up; Layer D changes customer-visible message composition on mixed turns.

Rollback: steps 1–3 are independently revertible single-site changes. Step 5 is the first
customer-visible one and should be flag-gated.

Stop if: a second canonical writer becomes necessary; `rc06`/`rc08` need a phrase rule; Layer
D registration cannot bound the message count; or the corpus evaluation (below) shows the
frozen prompt does not produce the quote-request proposition.

**Missing proof, named:** whether `understand/1.x` on the frozen `gpt-4o-mini` actually emits
`QUOTE_REQUEST`/`INSPECTION` for the `rc06`/`rc08` inputs. §14 forbade the live call. This is
a corpus-evaluation task and it gates only `rc06`/`rc08`, not the phase change.

## §19 SEPARATED VERDICTS

| # | Subject | Verdict |
|---|---|---|
| 1 | Source reconstruction | **PASS** — phases, 41 early returns and 8 pre-reconciliation mutations proven by source index and AST |
| 2 | Semantic-call attribution | **PASS** — one interpretation per burst; counter exists and resets |
| 3 | Reply-call attribution | **PASS on mechanism, FAIL on visibility** — `_reply_calls` exists and resets, but is not a trace field |
| 4 | Current phase ordering | **FAIL** — reply generation precedes reconciliation; Layer D returns 776 lines earlier |
| 5 | Reconciliation authority | **CONDITIONAL** — pure and once per turn, but not the proposal source in the deployed posture (P2) |
| 6 | Fixed-response bypass feasibility | **PASS** — plan + renderer-source foundation already exists |
| 7 | Multi-proposition preservation | **FAIL** — 41 early returns can discard propositions; Layer D does |
| 8 | Layer D safety | **FAIL** — answer-and-return suppresses the service action |
| 9 | Generic/garbled vehicle behaviour | **UNPROVEN** — schema can represent it; prompt coverage not measurable without a live call |
| 10 | Degraded-mode safety | **PASS** — no phrase fallback was introduced; the reverted attempt is documented in source |
| 11 | State-mutation boundary | **FAIL** — 8 mutation sites precede the authorized action; prose retains proposal authority in the deployed posture |
| 12 | Response-plan readiness | **PASS** — reuse, no second framework |
| 13 | Trace/Inspector readiness | **CONDITIONAL** — additive changes only; one gap named |
| 14 | Test-migration credibility | **PASS** — every change classified; nothing weakened |
| 15 | Bounded implementation feasibility | **PASS, after P1–P4** |
| 16 | Accumulated G3-7F range | **CONDITIONAL** — SEQ-02/03/04/05/06 closed; STOP-01/03 explained and now actionable |
| 17 | Gate 3 | **NOT CLOSED** |
| 18 | Deployment readiness | **NOT READY** — and a new blocker: deploying HEAD flips the proposal source, because `840bce0` lacks the demotion field entirely |
| 19 | Wild readiness | **NOT READY** |

## §20 LIMITATIONS AND UNRESOLVED FINDINGS

1. **Prompt coverage for `rc06`/`rc08` is unproven** — live call forbidden (§18).
2. **The 8 pre-reconciliation mutation sites** are inventoried, not fixed; staged follow-up.
3. **`_process_text` is 1,490 lines with 41 early returns.** Not a prerequisite, but the
   largest standing risk to any phase work.
4. **Deployed/HEAD demotion divergence** — `840bce0` has no
   `reply_renderer_demotion_enabled` field; HEAD defaults it to true. A deployment changes
   the candidate-proposal source. Must be an explicit, evidenced decision.
5. **`_reply_calls` is not in the trace**, so today's traces cannot show a wasted reply call.
6. **G3-8 / R2R-07** untouched.
7. **Two migrations pending**, neither applied to `crm_test`.
8. **`seq_53`'s attribution in the G3-7F-R2 closeout was wrong**; corrected in §9.

## §21 RECOMMENDED NEXT MILESTONE

A bounded prerequisite-correction milestone covering **P1–P4 and nothing else**: the per-turn
memo reset, stripping prose proposal authority in both postures, moving the category floor
with the decision, and converting Layer D to register-and-continue — with the five stale
anchors re-anchored and the five br1 fixtures repaired, so that the phase move itself can
then be a mechanical, provable step.

NEXT: L4.7W5-GATE3-G3-7H-PHASE-SEPARATION-PREREQUISITES

STOP.
