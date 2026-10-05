PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-CANONICAL-ROADMAP-R2

# RideCheck CRM — Canonical Prelaunch Roadmap R2

**Truth date:** 2026-10-05  
**Supersedes:** `2026-09-18_RIDECHECK_CRM_PRELAUNCH-CANONICAL-ROADMAP_AUDIT_001.md`  
**Purpose:** Replace the September status with one finite source of truth from the present Gate 3 position through controlled launch.

## 1. Executive truth

RideCheck CRM remains in **PRELAUNCH — NO-GO**.

The project is no longer blocked because the team does not understand the desired architecture. The hybrid architecture is now explicit, observable and partly implemented:

`Customer burst → RawEvidence → Semantic TurnEvidence + deterministic CE evidence → Reconciler → CanonicalState/action → response plan → audited result`

Gates 0, 1 and 2 are complete. The deterministic safety floor is deployed, hybrid traces are captured, and the Hybrid Decision Inspector is live and has passed its owner inspection after its misleading classifications were corrected.

The active bottleneck is **Gate 3: hybrid authority**. The project has proven that the semantic engine can provide fuzzy-language evidence and that deterministic CE can provide bounded safeguards. It has also proven that neither confidence nor a single engine may choose business authority. The remaining work is to finish safe correction confirmation, enforce pending decisions at every early-return gate, certify the complete reconciler and only then deploy the accumulated Gate 3 range.

The most recent independent review gives a **CONDITIONAL PASS** to the G3-7F foundation:

- deterministic target grounding is sound and should be retained;
- the semantic correction representation is sound and should be retained;
- the provisional-correction foundation is credible but incomplete;
- Commit 4, feature enablement and deployment are not authorized;
- one bounded G3-7F-R2 implementation is required before G3-8.

Smart Booking remains frozen behind the real-client replay. It is not the next milestone.

## 2. Non-negotiable architecture and product rules

1. **No phrase catalogue as architecture.** Real customer wording belongs in a versioned corpus with provenance, not in an expanding production list.
2. **Semantic evidence gets room to interpret fuzzy language.** It may produce structured meaning that CE could not detect.
3. **Semantic evidence is not canonical authority.** It cannot select a database row, write state, invent availability, accept a quote or book an appointment on its own.
4. **CE is a safety floor, not a veto on semantic understanding.** It provides deterministic facts, parsers and guards but may not silently erase valid semantic evidence.
5. **The reconciler owns admission and authority.** It compares typed propositions against canonical state and business services.
6. **Business services remain authoritative** for price, availability, booking, candidate identity and persisted state.
7. **One canonical writer per state family.** Candidate corrections continue through `_apply_candidate`; proposed state is not canonical truth.
8. **No silent fall-through.** Unproven actions HOLD, clarify once or hand off.
9. **Real customer language is durable evidence.** Failed Wilds, noisy text, corrections, negations and multi-message bursts are preserved, not rewritten into production rules.
10. **Live auditability is launch-critical.** Operators must see semantic output, CE evidence, reconciler admission, canonical before/after state, response plan and transport result.
11. **Scope freeze remains in force.** Smart Booking and unrelated functionality may not interrupt the hybrid critical path.
12. **No deployment authority is implied by a local PASS.** Migration, image, feature flags, outbound and Wilds each require their own checkpoint.

## 3. Current operational snapshot

| Item | Current truth | Status |
|---|---|---|
| Public launch | Not authorized | **NO-GO** |
| Outbound | `OUTBOUND_ENABLED=false` | **SAFE / OFF** |
| Deployed backend | Older certified runtime, SHA `840bce0` | **STABLE** |
| Local Gate 3 work | HEAD `72e978f`; 22 commits ahead of remote at the latest audit | **LOCAL ONLY** |
| G3-7F migration | Not applied to deployed `crm_test`; column absent | **NOT DEPLOYED** |
| Semantic model | `gpt-4o-mini`, prompt `understand/1.20`, schema `turn-evidence/1.4` in local source | **MODEL FROZEN** |
| Trace contract | Local contract `hybrid-decision-trace/1.7`; deployed runtime is older | **LOCAL ADVANCE** |
| Test/business data | Synthetic test environment; no real launch dataset | **TEST ONLY** |
| Meta Booking Flow | Published and previously handset-proven | **PASS** |
| Webhook security | Signature enforcement deployed and fail-closed | **PASS** |
| Test Agenda | Synthetic test revisions preserved | **PASS / TEST ONLY** |
| Smart Booking | Not started as the next critical-path step | **FROZEN** |

No Gate 3 commit has been pushed or deployed. Runtime safety therefore remains unchanged while the local architecture is reviewed.

## 4. Gate status

| Gate | Objective | Status on 2026-10-05 |
|---|---|---|
| Gate 0 | Deploy deterministic safety floor | **COMPLETE** |
| Gate 1 | Persist complete hybrid evidence trace | **COMPLETE** |
| Gate 2 | Hybrid Decision Inspector | **COMPLETE / OWNER PASS** |
| Gate 3 | Hybrid interpretation, reconciliation and canonical authority | **IN PROGRESS / BLOCKING** |
| Gate 4 | Owner-controlled Human Rescue Wild | **PENDING GATE 3** |
| Gate 5 | Real-client replay corpus | **PENDING GATE 4** |
| Gate 6 | Useful alternative-time response F-03 | **PENDING REPLAY EVIDENCE** |
| Gate 7 | Smart Booking Engine MVP | **FROZEN UNTIL GATE 5** |
| Gate 8 | Real launch dataset/environment transition | **PENDING** |
| Gate 9 | Final combined certification and controlled launch | **PENDING** |

## 5. What Gates 0–2 achieved

### Gate 0 — deterministic safety floor

The immediate human-rescue loop was bounded and deployed without claiming that deterministic handling proved the hybrid architecture.

### Gate 1 — trace capture

The system now persists a correlated conversational trace containing the burst boundary, semantic execution, deterministic facts, reconciliation rows, canonical before/after state and outbound result.

### Gate 2 — Hybrid Decision Inspector

The Control dashboard now exposes live hybrid decisions. Earlier misleading labels such as false `AGREE` were corrected. The Inspector distinguishes which producers actually contributed evidence and shows when semantic evidence was not routed.

These gates are not reopened by the current Gate 3 defects.

## 6. Gate 3 — current truth

### 6.1 What is sound and retained

The G3-7F independent audit authorizes retention of:

- the Grounded-Target Invariant;
- excluded/alternate-reference protection;
- literal make+model creation guard;
- semantic `CorrectionTargetScope` and typed multi-field patches;
- fail-closed handling of older/malformed correction payloads;
- atomic Tier 2 patches;
- one proposed correction per thread;
- stable patch fingerprints and idempotency design;
- one canonical candidate writer;
- additive/reversible migration design;
- trace/Inspector representation of pending corrections;
- explicit disclosure that gate enforcement is not yet live.

The decisive safety result is that semantic confidence does not ground a target. Even a mislabelled `CONFIRMED` interpretation cannot select a customer vehicle unless canonical grounding succeeds.

### 6.2 What is not complete

`confirmation_admissible()` currently has no production caller. The system can design and arm a confirmation but does not yet consume the customer's acceptance. This makes the discovered confirmation defects latent rather than live, but they must be closed before G3-8 wires the path.

Commit 4 is not ready. The current local range must not be enabled or deployed as the launch candidate.

### 6.3 Bounded G3-7F-R2 scope

One final bounded correction is authorized in this symptom family. It must close only these implementation defects:

1. **Tier 1 echo:** an immediately applied year correction must reach the customer after the canonical write succeeds.
2. **Composer boundary:** the typed correction proposition must be consumed by the response composer; the engine must not remain the prose authority.
3. **Legacy mutual exclusion:** add the missing active proposals, including fallback Flow flags and `last_visible_slots`.
4. **Bounded stale-proposal recovery:** an abandoned Flow/token cannot trap the customer in an endless clarification loop.
5. **Flag dependency:** provisional correction must fail closed when deterministic grounding is disabled or the grounding basis is `NONE`.
6. **Delivery truth:** confirmation authority must be revoked when the referenced outbound row later becomes failed or blocked.
7. **Identity/category consistency:** a confirmed make/model correction must re-derive the catalogue category before price or routing can continue.
8. **Regression integrity:** full-suite subtest failures must be counted; the cross-module Pydantic reload defect must be repaired without weakening assertions.
9. **Coverage:** all four feature-flag combinations and each defect class must have executable production-path tests.

The R2 milestone may not:

- add phrases or aliases;
- add a second writer;
- enable flags;
- call the semantic model;
- implement G3-8 early-return enforcement;
- push, deploy or migrate the live database;
- expand into Smart Booking.

If the same confirmation-authority symptom family remains unsafe after R2, the next action is architectural review and owner decision—not G3-7F-R3.

### 6.4 G3-8 — routing precedence and commercial gate enforcement

After G3-7F-R2 passes independent review, G3-8 must wire the pending-correction predicate into every relevant early-return path.

It must prove that a blocking pending correction prevents:

- quote acceptance;
- scheduling progression;
- Meta Flow dispatch;
- stale-quote use;
- deterministic gates returning before corrected candidate authority runs.

Confirmation applies the atomic correction first, category/price are recomputed, and only then may scheduling continue.

`G37BR-02` remains the named blocker until this is proven through the real handler.

### 6.5 Gate 3 consolidated certification

Only after R2 and G3-8 pass independent reviews may the project run the consolidated Gate 3 certification:

1. Confirm the remaining historical Gate 3 findings are either closed by executable paths or explicitly still blocking; do not infer closure from later work.
2. Run the semantic correction matrix on the frozen `gpt-4o-mini`, prompt `understand/1.20`, schema `turn-evidence/1.4`.
3. Preserve evaluation inputs separately from prompt-development examples.
4. Report omissions, safe HOLDs, wrong values and wrong targets separately.
5. Require zero autonomous wrong-target mutations in the certification set.
6. Verify model timeout, malformed output and model-disabled paths.
7. Verify exact-one-response behaviour and truthful response/action attribution.
8. Verify the complete trace through the Inspector.
9. Decide feature-flag defaults only from the consolidated evidence.
10. Rehearse rollback and mandatory deployment order.

The model remains `gpt-4o-mini` through this certification. `gpt-5.4-mini` showed useful improvements and lower latency but failed the current contract in safety-critical ways and is not a launch substitution. A future model migration requires its own benchmark after the architecture is stable.

### 6.6 Gate 3 deployment order

The independent review proved this sequence is mandatory:

1. backup and preflight;
2. apply the additive database migration;
3. verify the new nullable column and Alembic head;
4. deploy/recreate the backend image;
5. keep outbound OFF;
6. verify runtime SHA, restart count, trace contract and Inspector;
7. run offline/controlled post-deployment certification;
8. arm only the designated tester at an owner checkpoint.

Deploying the new image before the migration breaks ordinary thread-state reads even with both feature flags OFF. Rolling the image back after the additive migration is safe because the older ORM ignores the extra nullable column.

## 7. Revised finite critical path

### Step 1 — G3-7F-R2 bounded correction

**Exit:** all nine bounded items in §6.3 pass; no phrase rules; both new flags remain OFF; full regression and subtest accounting reconcile.

### Step 2 — Independent review of G3-7F-R2

**Exit:** confirmation authority, composer routing, legacy mutual exclusion, category consistency, delivery truth and test credibility pass independently.

**Loop stop:** if the same family still fails architecturally, stop for owner review. No R3 by default.

### Step 3 — G3-8 routing precedence

**Exit:** no quote, schedule or Flow gate can bypass a blocking pending correction; the complete real-handler path is traceable.

### Step 4 — Gate 3 consolidated certification and cutover decision

**Exit:** semantic evaluation, deterministic degradation, reconciliation, response composition, trace truth and flag policy pass as one system. Commit 4 is created only here.

### Step 5 — Controlled migration and deployment

**Exit:** migration-first deployment succeeds with outbound OFF, rollback proven, data unchanged and Inspector truthful.

### Step 6 — Owner-controlled Human Rescue Wild

Run the preserved rejection and direct-human branches with Lara present. Require one acknowledgement, one alert, human ownership, token withdrawal, no booking and post-handoff silence.

**Exit:** both branches pass on real WhatsApp transport and Lara declares the Wild clean.

### Step 7 — Real-client replay

Replay de-identified historical turns, all failed Wilds, audio/image transcriptions, noisy spelling, multi-message bursts, corrections and competing intents.

Measure separately:

- wrong target;
- wrong value;
- unsafe progression;
- safe HOLD;
- omission;
- redundant question;
- false/missed handoff;
- duplicate response.

**Exit:** no BLOCKER/HIGH behavioural defect; corpus limitations and statistical intervals stated honestly.

### Step 8 — F-03 useful alternative scheduling

Implement the alternative-time request against authoritative availability. It must return valid earlier options, explain that none exist, or hand off under bounded business rules.

### Step 9 — Smart Booking MVP

Only now audit and implement geography, travel time, capacity, inspector preferences, collision handling and explainable scoring. The conversational layer supplies preferences; deterministic services own allocation.

### Step 10 — Launch dataset/environment transition

Back up, dry-run migration, identify real production ownership, quarantine synthetic data, prepare the real Agenda, certify secrets/access/monitoring and obtain owner sign-off.

### Step 11 — Final combined certification and controlled launch

Run the release-candidate regression, security suite, replay, Flow, hybrid engine, Smart Booking and difficult-geography tests. Launch begins only after Lara declares **GO**.

## 8. What is launch-critical versus deferred

### Launch-critical

- G3-7F-R2;
- G3-8 routing precedence;
- consolidated Gate 3 certification;
- migration-first controlled deployment;
- Human Rescue Wild;
- real-client replay;
- F-03 alternative-time response;
- agreed Smart Booking MVP;
- real launch dataset/environment;
- final combined certification.

### Deferred unless new evidence promotes it

- semantic-model upgrade;
- unification of every legacy pending-proposal representation beyond what safety requires;
- cosmetic Inspector work;
- additional CRM conveniences;
- additional automation channels;
- speculative semantic claims;
- Smart Booking features outside the agreed premises;
- historical cleanup unrelated to launch safety.

## 9. Anti-loop governance

1. One milestone closes one named capability or risk family.
2. G3-7F-R2 is the final bounded remediation for its symptom family. A repeated architectural failure returns to the owner.
3. A targeted suite never certifies runtime behaviour.
4. Regression reconciliation includes failures, errors and subtest failures; arithmetic must reconcile.
5. Tests must call production functions where behaviour matters; source/AST checks are governance support, not behavioural proof.
6. A model evaluation never grants canonical authority by itself.
7. A Wild never certifies itself; the owner supplies the verdict.
8. No phrase is promoted into production merely because it failed once.
9. Every corpus case retains provenance, expected meaning and must-not-infer constraints.
10. Every gate-changing closeout updates this roadmap or produces an explicit roadmap-sync artifact.
11. No push, migration, deployment, outbound activation, Meta mutation or real-data operation occurs without the applicable owner checkpoint.
12. Smart Booking cannot move ahead of the real-client replay.

## 10. Immediate owner-readable status

**Where are we?** The observability architecture works, and the core correction-target design is sound. The project is finishing the last unsafe edges of confirmation authority before connecting that authority to booking and scheduling gates.

**What happens next?** One bounded G3-7F-R2 correction, one independent review, then G3-8.

**What is not happening yet?** No model upgrade, no Wild, no Smart Booking, no push and no deployment.

**Why this is still the shortest path:** deploying now would connect incomplete confirmation authority to commercial actions. Restarting the architecture would discard several independently proven components. The bounded R2 → G3-8 → consolidated certification sequence preserves the good work while closing the exact remaining risks.

## 11. Current owner decision

RideCheck will launch with a bounded, auditable hybrid architecture—not an exhaustive catalogue of customer wording. The semantic layer is allowed to interpret fuzzy language; CE provides deterministic evidence and safety floors; the reconciler alone authorizes canonical changes and commercial progression.

The project remains **NO-GO** until Gate 3 is certified and deployed, the Human Rescue Wild passes, the real-client replay is clean, Smart Booking is proven in its contained scope, and the production environment is ready.

NEXT: L4.7W5-GATE3-G3-7F-R2-BOUNDED-CORRECTION
STOP.
