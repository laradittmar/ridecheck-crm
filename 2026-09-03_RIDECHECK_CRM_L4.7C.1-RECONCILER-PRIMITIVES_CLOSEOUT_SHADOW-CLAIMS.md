PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7C.1-RECONCILER-PRIMITIVES

# L4.7C.1 — claims, states and records, all in shadow

Date: 2026-09-03
Phase C1 of the approved L4.7C design · **shadow only** · no canonical write · no authority
moved · no CE behaviour change · OUTBOUND OFF · crm_test only · production DB untouched.

---

## 1. Verdict

**PASS.** The vocabulary in which the two evidence producers can be compared now exists, the
four-valued information state is computed, and every decision is recorded in an append-only,
versioned record. **Nothing acts on any of it.** ConversationEngine remains fully
authoritative; the new layer produced 7 claims on a live probe and wrote nothing.

## 2. What was built

| Module | Role |
|---|---|
| `backend/app/schemas/claims.py` | `ClaimEvidence` + enums + information state + risk tiers. Pure schema: imports `json`, `hashlib`, `enum`, `typing`, `pydantic` and the two symbols it reuses from `turn_evidence`. |
| `backend/app/services/claim_projection.py` | `TurnEvidence → ClaimEvidence[]` and `FieldEvidence → ClaimEvidence[]`, organised by canonical claim type instead of producer shape. |
| `backend/app/services/shadow_reconciler.py` | `reconcile()` — group, compute state, choose outcome under a named/versioned rule, record. No I/O, no ORM, no service. |
| `turn_evidence.py` | `ReconciliationRecord` extended **additively** → `turn-evidence/1.2`. |
| `shadow_recorder.py` | `shadow-record/1.2` carries the reconciliation summary. |
| `conversation_engine.py` | The existing async shadow job now also projects and reconciles. |

### The claim

```python
ClaimEvidence(claim_type, value, polarity, status, evidence_class, producer,
              producer_version, source_message_ids, source_span, explicitness,
              temporality, modality, confidence, cycle_id, revision_id, created_at,
              supersedes, alternatives, reason, claim_id)
```

`claim_id` is a content hash, so the same claim from the same producer is the same id across
runs. Four fields exist nowhere else in the system and carry the safety weight:

* **`polarity`** — *"el auto no está en Tigre"* is evidence **against** Tigre;
* **`temporality`** and **`modality`** — *"si me cierra te hablo"* is FUTURE + CONDITIONAL,
  and `is_actionable_now` is False for it regardless of how confidently it was read;
* **`cycle_id`** — `in_cycle()` and `reconcile(cycle_id=…)` drop claims from a finished cycle,
  making the L4.6 stale-candidate defect class structurally impossible rather than merely
  discouraged.

`confidence` is carried and **never read**: no function in these three modules takes it as an
input to a decision, and CLAIM-08 asserts that two identical claims differing only in
confidence produce the same information state, and that a confident claim does not win a
disagreement.

### Information states

| State | Rule |
|---|---|
| `NEITHER` | no claims — **absence is never FALSE**; also the state of an AMBIGUOUS-only claim, since uncertainty is not support |
| `TRUE_ONLY` | asserted support, one distinct value |
| `FALSE_ONLY` | negated support only |
| `BOTH` | asserted + negated, **or** two asserted claims with incompatible values |

Ambiguity survives rather than resolving: `alternatives_for()` collects every reading, and the
record's `candidate_values` shows what was considered, not only what won.

### Projection rules that keep authority where the design put it

* A make the interpreter **added** to a model-only mention projects as `SEMANTIC_INFERRED`
  with `explicitness=IMPLIED`; the catalog's word arrives through the deterministic snapshot
  as `CATALOG_CONFIRMED`. CLAIM-02 and CLAIM-03 assert the two are distinct.
* `EXPLICIT_CUSTOMER` is granted only when the value can be found in the burst text.
* Tense and conditionality are read **once per turn** from grammatical markers — "si",
  "cuando", "voy a", "te aviso" — and applied uniformly to every claim of that turn. These are
  grammar, not business phrases: they mention no vehicle, price or zone, and add no
  sentence-specific behaviour (§6.1 no-phrase-patch rule).
* `UNKNOWN_LOCATION_ROLE` produces **no** claim: an unassigned role is not a canonical fact.
* `HESITATE` and `QUESTION_ONLY` produce no claim either — doubt is evidence for nothing.

### Reconciliation

Rules are named and versioned (`reconcile.location_role` at `v1`, `reconcile.quote_accepted`
at `v1`, …) so a policy change is a version bump rather than an edited conditional. Outcome
comes from the information state and the **consequence**, never from confidence:

| State | LOW/MEDIUM | HIGH |
|---|---|---|
| `BOTH` | CLARIFY | **NEEDS_HUMAN** |
| `NEITHER` | HOLD (CLARIFY if ambiguous) | HOLD |
| `FALSE_ONLY` | ACCEPT (the negation is recorded) | ACCEPT |
| `TRUE_ONLY` | ACCEPT | ACCEPT **only if** some claim is present-tense and factual; otherwise **HOLD** |

One correction was made during implementation and is worth recording: the first version
projected a canonical value whenever the state was `TRUE_ONLY`, including on a HOLD. Recording
a value beside a decision *not* to take it is precisely the ambiguity that later becomes a
wrong action, so the projection is now gated on `ACCEPT`. A HOLD carries `canonical_value =
None`.

## 3. Append-only record — `turn-evidence/1.2`

`ReconciliationRecord` gained `claim_type`, `evidence_ids`, `candidate_values`, `rule_id`,
`rule_version`, `information_state`, `outcome`, `risk_tier`, `cycle_id`, `revision_id`,
`depends_on`, `supersedes`, `shadow`. All optional; every 1.0/1.1 record still validates under
the major-version guard. `ReconciliationLog.append()` is unchanged and still returns a new log
— CLAIM-13 asserts that history grows and that an existing record cannot be rewritten (the
models are frozen).

`depends_on` is populated now, before anything uses it: `quote_accepted` depends on
`vehicle.category` and `inspection_location`; `scheduling_preference` on
`inspection_location`; `vehicle.category` on make/model/year. C5 will read exactly this.

## 4. Corpus observation (Part 14) — 162 cases, no model calls

Projected from the stored `understand/1.18` outputs; the second producer here is the corpus
expectation standing in for deterministic evidence, **not** a live `FieldEvidence` snapshot —
stated plainly because the distinction matters.

| | |
|---|---|
| claims projected | **275** across 162 cases |
| information states | TRUE_ONLY 256 · **BOTH 7** · FALSE_ONLY 2 |
| outcomes | ACCEPT 257 · **CLARIFY 7** · HOLD 1 |
| most frequent claim types | service_intent 43 · vehicle.model 32 · vehicle.make 31 · inspection_location 31 · vehicle.year 29 · quote_accepted 22 · scheduling_preference 22 |
| producer comparison | AGREE 249 · DETERMINISTIC_ONLY 43 · SEMANTIC_ONLY 7 · **CONFLICT 9** |

The seven `BOTH` states and nine producer conflicts are the interesting output: disagreement
is now **visible and counted** instead of being resolved silently by whichever code path ran
first. That is the whole point of C1.

## 5. Critical scenarios (Part 15)

| Scenario | Result |
|---|---|
| *"Está en Berazategui, pero yo soy de Tigre."* | `inspection_location=Berazategui` TRUE_ONLY/ACCEPT · `customer_origin=Tigre` TRUE_ONLY/ACCEPT — two claims, two roles, no competition |
| *"si me cierra te hablo"* | projects FUTURE + CONDITIONAL; `quote_accepted.is_actionable_now = False`; reconciles to **HOLD** with **no canonical value** |
| *"Es del 2015, no del 2014"* | `correction{from:2014,to:2015}` and `vehicle.year=2015`, both recorded; the old value survives inside the relation |
| *"Mñ 15hs? O nose jueves que tenes"* | one scheduling claim carrying both branches in order, the time attached to the first only |
| *"Quiero comprar un Fox"* | `vehicle.model=Fox` EXPLICIT_CUSTOMER · `vehicle.make=Volkswagen` **SEMANTIC_INFERRED**; `CATALOG_CONFIRMED` appears only when the deterministic snapshot supplies it |

## 6. Live runtime probe

On the deployed image, one two-message burst — *"Está en Berazategui pero yo soy de Tigre, es
un Focus 2017"* + *"si me cierra te hablo"*:

```
record        shadow-record/1.2 · turn-evidence/1.2 · understand/1.18 · dispatch async
claims        7
states        inspection_location TRUE_ONLY · customer_origin TRUE_ONLY · vehicle.model
              TRUE_ONLY · vehicle.make TRUE_ONLY · vehicle.year TRUE_ONLY ·
              future_intent TRUE_ONLY · inspectability TRUE_ONLY
outcomes      all ACCEPT        rules  reconcile.location_role, reconcile.vehicle_*,
                                       reconcile.stance, reconcile.inspectability
```

Two details worth naming. `inspectability` came from the **deterministic** `FieldEvidence`
snapshot, so both producers demonstrably projected into the same claim space. And
`quote_accepted` is **absent**: the interpreter read the conditional sentence as
`FUTURE_INTENT`, so no acceptance claim was ever created — the conditional guard never even
had to fire.

`crm_test.whatsapp_messages` 6 → 6 · `whatsapp_thread_candidates` 0 → 0 · no raw burst text in
the record.

## 7. No-authority proof (Part 16)

CLAIM-16 parses all three C1 modules and asserts that none imports `models`, `db`, `session`,
`conversation_engine`, `pricing`, `schedule`, `outbound_safety_gate`, `booking_flow_service`,
`thread_revisions`, `whatsapp_threads`, `lead_lifecycle` or `requests`, and that none contains
`.add(`, `.commit(`, `.flush(`, `.delete(` or `.execute(`. CLAIM-17 drives the real CE hook
end-to-end with a stubbed model and asserts: thread state unchanged, lead unchanged, no
candidate, `db.add`/`db.commit` never called, `_send_text_to_wa` / `_send_flow_button` never
called, `OutboundSafetyGate` never constructed — and that the reconciliation summary did reach
the shadow record. Every record carries `shadow=True`.

The summary stores **types and decisions, never values**: a test asserts that a locality
appearing in the evidence does not appear anywhere in the summary.

## 8. Tests and regression

`tests/test_l4_7c_1_reconciler_primitives.py` — **30/30 PASS** (CLAIM-01…18 plus
ambiguity-is-not-support, present-factual shape, dependency recording, high-risk escalation,
shadow marking and risk-tier defaults).

A cross-suite defect was found and fixed inside this milestone: `shadow_reconciler` built
`ReconciliationRecord` and `ReconciliationLog` from module-level imports, so after another
suite reloaded `app.schemas.turn_evidence` the two classes came from different module objects
and pydantic rejected the record. Both are now resolved from the module at call time. This is
the third instance of the same class of bug in this programme (`AcceptanceSignal`,
`CorrectionRelation`, now the record classes) — **module reloads in tests are a standing
hazard for identity-based comparisons and module-level class captures.**

Full regression: **3 422 passed / 60 failed / 9 errors** — failure set identical to the
baseline, **zero new failures**. Launch-gate suites (L1, L2, L2.1, L3, L4.3, L4.6, L4.7A,
L4.7B, L4.7B.2, L4.7B.2B, L4.7B.3, L4.7B.4, L4.7D, L4.7E, L4.7C.1): **443 passed, 0 failed**.
Unknown: 0.

One assertion was realigned deliberately: the L4.7B.2 suite pinned `turn-evidence/1.1`; the
additive bump to 1.2 moved it, with the reason recorded in the test.

## 9. What C1 is not

It is not authority. No canonical field is written by this layer, no business action is
authorised, no class-D parser was retired, and `CanonicalFacts` is still built the way L4.7D
built it. C2 (vehicle/location) is **not** started.

L1/L2/L3 FROZEN · L4 ACTIVE · OUTBOUND OFF · Wild clean count **0/3**.

Next: **L4.7C.2-VEHICLE-LOCATION-RECONCILIATION** — not started, and not to be started
automatically.
