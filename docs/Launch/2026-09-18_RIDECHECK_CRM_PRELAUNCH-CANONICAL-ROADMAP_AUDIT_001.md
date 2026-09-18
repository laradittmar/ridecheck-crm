PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-CANONICAL-ROADMAP

# RideCheck CRM — Canonical Prelaunch Roadmap

**Truth date:** 2026-09-18  
**Purpose:** Replace the outdated launch roadmap with one finite, deadline-driven source of truth.

## 1. Executive truth

RideCheck CRM is in **PRELAUNCH — NO-GO**.

The project has been delayed for almost three months. During that delay, Smart Booking was added to the expected launch scope. The response cannot be an endless sequence of phrase-specific fixes. Launch now depends on proving a trustworthy hybrid decision architecture and containing remaining scope.

The required architecture is:

`Customer burst → RawEvidence → Semantic TurnEvidence + deterministic CE evidence → Reconciler → CanonicalState/action → audited response`

The LLM and CE are independent evidence producers. Neither may silently become business authority. The reconciler must apply explicit, claim-specific rules against canonical state and business services. Every material decision must be inspectable from the CRM dashboard.

## 2. Non-negotiable product rules

1. **No phrase catalogue as architecture.** Real expressions belong in a regression/evaluation corpus, not in an indefinitely growing production exception list.
2. **Deterministic floors remain valid.** Narrow CE rules protect the system when the model is absent, slow or wrong, but they are safety floors—not proof that hybrid interpretation works.
3. **Semantic evidence is structured evidence, not authority.** The model may describe meaning; business rules and canonical state decide what is allowed.
4. **Reconciliation must be explicit.** For each claim, document agreement, disagreement, missing-evidence and uncertainty behavior.
5. **No silent fall-through.** If neither engine can support a safe action, the system must ask a useful clarification or hand off—not invent, loop or progress falsely.
6. **Live auditability is a launch requirement.** Operators must see what each layer received, produced and decided without reading SQL or container logs.
7. **Real customer language is durable evidence.** Failed Wild messages and real-client examples must remain in versioned corpora with provenance and expected semantic equivalence.
8. **Scope freeze.** Until launch, new functionality is accepted only if it closes a launch blocker or belongs to the already-agreed Smart Booking premises. Everything else is post-launch.

## 3. Current launch truth

| Area | Current truth | Status |
|---|---|---|
| Meta Booking Flow | R3 published, valid and handset-proven; correct prefill and read-only/masked phone | **PASS** |
| CRM Agenda “Cobrar” | Implemented and deployed | **PASS** |
| Test Agenda | 12 synthetic revisions, IDs 62–73, seeded for the current test week | **PASS / TEST ONLY** |
| Environment | Current CRM and `crm_test` contain synthetic data; this is not the real operational launch dataset | **LAUNCH GATE OPEN** |
| Webhook security | App Secret/signature enforcement deployed and proven fail-closed | **PASS** |
| Human rescue baseline | F7C–F7F deployed; multiple false-positive/negation defects closed | **PASS WITH NEW WILD FINDING** |
| Failed Wild | “Mmm, no me sirve” did not rescue; system re-asked rejected options | **HIGH DEFECT FOUND** |
| F7G-R2 safety floor | Correct local commit `ce978a2`; tests pass; not yet deployed | **READY FOR CONTROLLED DEPLOYMENT** |
| Hybrid interpretation for offered-slot rejection | Semantic result had no rejection; forensic record cannot prove which complete burst the model saw; no dedicated claim/reconciliation policy exists | **NOT PROVEN** |
| Alternative-time request | “¿No tenés algo más temprano?” correctly must not be treated as a rejection, but useful alternative response remains open F-03 | **OPEN MEDIUM** |
| Live hybrid observability | Current Control dashboard shows operational summaries but not Raw/LLM/CE/Reconciler/Canonical evidence | **LAUNCH GATE OPEN** |
| Real-client replay | Not yet executed against the completed hybrid architecture | **PENDING** |
| Smart Booking | Premises supplied; audit/design, implementation and certification remain | **PENDING / SCOPE CONTAINED** |
| Public launch | Not authorized | **NO-GO** |

## 4. Finite critical path

### Gate 0 — Deploy the proven safety floor

**Goal:** Remove the immediate customer loop without pretending the hybrid gap is solved.

Actions:

- Push/build/deploy F7G-R2 commit `ce978a2` through the controlled procedure.
- Recreate only the backend.
- Keep outbound OFF.
- Certify runtime attribution, webhook signatures, Meta status, Agenda integrity and unchanged business data.

Exit:

- Deployed runtime executes the deterministic rescue floor.
- No new regression or operational mutation.
- Closeout explicitly labels it a deterministic floor.

### Gate 1 — Hybrid Evidence Contract and live trace capture

**Goal:** Prove exactly what each engine saw and produced for one customer burst.

Required persisted trace, correlated by one `turn_id`/`burst_id`:

- ordered inbound message IDs and timestamps;
- normalized combined burst and privacy-safe input hash;
- semantic prompt/schema/model versions;
- semantic structured output, validation result, latency and errors;
- CE predicates/evidence with rule versions;
- canonical state before decision;
- reconciler inputs, rule selected, conflicts and resolution reason;
- allowed/blocked transition;
- canonical state after decision;
- response plan and outbound result;
- deployment SHA and correlation IDs.

Raw customer text may be shown to authorized CRM operators because it already exists in the WhatsApp conversation, but secrets and unnecessary model internals must not be stored.

Exit:

- A complete burst can be reconstructed across all five layers.
- Missing semantic calls, timeouts and malformed outputs are visible rather than indistinguishable from “no intent.”
- The exact model input boundary is provable.

### Gate 2 — Control dashboard: Hybrid Decision Inspector

**Goal:** Let Lara audit live decisions without SQL, SSH or container logs.

The existing dashboard remains the operational overview. Add a conversation/turn drill-down with:

#### A. Conversation timeline

- ordered IN/OUT messages and burst grouping;
- stage, canonical state and outstanding offer at each turn;
- Flow/token lifecycle, booking result and human ownership;
- deployment/version attribution.

#### B. Evidence comparison

| Panel | Must display |
|---|---|
| RawEvidence | message IDs, burst order, normalized text, attachments/transcripts, source and timestamps |
| Semantic engine | model/prompt/schema versions, structured claims, polarity, confidence/status, ambiguities, latency and errors |
| Deterministic CE | matched facts/predicates, parser outputs, guards/floors triggered, and rule identifiers |
| Reconciler | compared claims, agreements/conflicts/missing evidence, authority rule, accepted/rejected evidence and reason |
| Canonical result | state before/after, transition, allowed/blocked actions, clarification/handoff reason and response plan |

#### C. Operator signals

- obvious badges: `AGREE`, `CONFLICT`, `SEMANTIC MISSING`, `CE MISSING`, `BLOCKED`, `HANDOFF`, `FALLBACK`;
- filter by deployment, claim, conflict type, model error, human handoff and false progression;
- expandable raw JSON for expert audit, with a readable summary by default;
- links from the existing message trace to the exact decision inspector;
- no secret values and no unmasked phone exposure beyond existing authorized CRM policy.

Exit:

- Lara can answer: “What did the customer say, what did the LLM infer, what did CE infer, what did reconciliation decide, and why?” from one screen.
- A failed turn is diagnosable without engineering access.

### Gate 3 — Hybrid rejection capability and reconciliation authority

**Goal:** Make offered-appointment rejection a supported semantic concept without overloading quote acceptance.

Actions:

1. Measure the semantic engine offline against a split corpus of real and authored language, including the failed Wild burst.
2. Keep evaluation examples separate from prompt-development examples.
3. Determine whether the current schema can represent:
   - rejection of the offered appointment set;
   - request for a different/earlier option;
   - explicit human-help request;
   - uncertainty versus rejection.
4. Do **not** reuse `QUOTE_ACCEPTED/NEGATED` for appointment rejection unless a formal domain migration proves referential equivalence—which is not currently true.
5. If needed, introduce a dedicated evidence claim and one explicit live consumer.
6. Define reconciliation rules for:
   - semantic + CE agreement;
   - semantic-only evidence;
   - CE-only evidence;
   - disagreement;
   - absent/failed semantic call;
   - ambiguity and competing objects.
7. Business services remain authoritative for availability, price, booking and state mutation.

Exit:

- New wording variants are handled by semantic equivalence plus bounded deterministic safeguards—not by adding each sentence to production logic.
- Corpus evaluation meets agreed precision/recall thresholds, especially false-handoff rate.
- Reconciler decisions are visible in the dashboard.
- Model-disabled and model-failure paths remain safe.

### Gate 4 — Owner-controlled Human Rescue Wild completion

**Goal:** Complete the interrupted Wild with full live evidence.

Sequence:

1. Preserve the failed thread until replay evidence is secured.
2. Reset only the tester through the canonical lifecycle.
3. Arm tester-only outbound with Lara present.
4. Replay Branch A: reject the outstanding offered appointments.
5. Require exactly one acknowledgement, one operator alert, human ownership, token withdrawal, no booking and post-handoff silence.
6. On a fresh cycle, verify an alternative-time request alone does not falsely trigger rescue.
7. Run Branch B: direct request for a person to coordinate.
8. Disarm outbound immediately and certify counts.

Exit:

- Both rescue branches pass on real WhatsApp transport.
- Dashboard trace proves the complete hybrid decision path.
- Lara declares the Wild clean.

### Gate 5 — Real-client replay corpus

**Goal:** Find broad language failures before expanding scheduling complexity.

Inputs:

- real historical customer texts;
- approved audio transcripts and image-derived text;
- all prior failed Wild utterances;
- the preserved noisy/typo examples;
- multi-message bursts, corrections, negations and competing intents.

Method:

- privacy-controlled ingestion;
- semantic-equivalence grouping;
- development/evaluation split;
- replay through RawEvidence → both engines → reconciler → canonical outcome;
- dashboard-visible evidence;
- measure wrong progression, redundant question, unnecessary clarification, false handoff, missed handoff and duplicate response rates.

Exit:

- No BLOCKER/HIGH behavioral defects.
- No evidence of phrase-by-phrase production growth.
- Known corpus limitations documented honestly.

### Gate 6 — F-03 useful alternative scheduling response

**Goal:** Answer “¿No tenés algo más temprano?” safely and usefully.

This is not rejection handling. It is an alternative-search request. It should query authoritative availability and either offer valid earlier options, explain that none exist in the allowed horizon, or hand off when business constraints require it.

Exit:

- No invented slot.
- No false human handoff.
- No repeated rejected offer.
- Behavior proven against real availability and replay corpus.

### Gate 7 — Smart Booking Engine, contained scope

**Goal:** Implement the client-requested routing/booking logic without destabilizing the proven conversational core.

Order:

1. Audit and normalize the supplied booking premises into explicit business constraints.
2. Identify authoritative inputs, priorities, capacity, geography, travel time, inspector preferences, exceptions and override ownership.
3. Produce a deterministic scheduling proposal engine with explainable scores/reasons.
4. Keep the conversational semantic layer limited to customer preference evidence; it must not own operational allocation.
5. Simulate against the synthetic weekly Agenda before any live use.
6. Implement behind a feature flag and preserve the current booking path as rollback.
7. Certify Flow + Smart Booking + CRM Agenda together.

Exit:

- Every suggested appointment can explain why it was selected.
- Capacity, travel and collision rules are deterministic and tested.
- Existing booking path remains recoverable.
- No new feature beyond the agreed premises enters prelaunch scope.

### Gate 8 — Launch dataset and environment transition

**Goal:** Move from synthetic `crm_test` evidence to the real operational launch environment safely.

Required:

- identify production database and runtime ownership;
- migration plan and dry run;
- backup and rollback proof;
- real lead/revision import or clean-start decision;
- removal/quarantine of synthetic test records;
- access-control review;
- Meta/n8n/runtime secrets and webhook certification;
- Agenda readiness with real operational data;
- monitoring, alerts and incident runbook;
- owner sign-off.

Exit:

- No synthetic-data leakage.
- Production identity and rollback are unambiguous.
- Real Agenda and operational users are ready.

### Gate 9 — Final combined certification and controlled launch

Required:

- full regression and security suite;
- real-client replay rerun on release candidate;
- combined Flow + hybrid engine + Smart Booking certification;
- difficult-geography Wilds;
- outbound safety and observability certification;
- owner go/no-go review;
- controlled ramp with explicit stop conditions.

Public launch begins only after Lara declares **GO**.

## 5. Launch blockers versus post-launch work

### Launch blockers

- Deploy and replay F7G-R2.
- Complete hybrid evidence capture and dashboard inspector.
- Define and prove hybrid rejection/reconciliation capability.
- Complete Human Rescue Wild.
- Run real-client replay before Smart Booking.
- Implement and certify the agreed Smart Booking MVP.
- Complete production dataset/environment gate.
- Final combined certification.

### Post-launch unless evidence promotes them

- cosmetic dashboard refinements beyond the evidence inspector;
- new CRM convenience actions not required for operations;
- additional automation channels;
- speculative semantic claims without corpus evidence;
- Smart Booking enhancements beyond the signed premises;
- general cleanup of unrelated probe files and historical artifacts.

## 6. Governance and anti-loop rules

1. One milestone closes one named risk or capability.
2. Maximum one remediation round before an architectural review is required for the same symptom family.
3. A targeted test pass does not certify runtime behavior.
4. A Wild observation does not certify itself; Lara supplies the owner verdict.
5. “Partial pass” must identify the exact open gate and cannot silently generate an unlimited remediation chain.
6. Every fix must state whether it changes semantic production, deterministic evidence, reconciliation, canonical authority or only presentation.
7. Corpus additions require provenance, expected meaning and protection from deletion/relabeling.
8. No push, deployment, outbound activation, Meta mutation or production-data operation without the applicable owner checkpoint.
9. The roadmap is updated after every gate-changing closeout; completed low-level milestones are archived rather than expanded here.

## 7. Immediate next actions

1. Complete `L4.7W5-F7G-R2-CONTROLLED-DEPLOYMENT`.
2. Build Gate 1 evidence capture and Gate 2 Hybrid Decision Inspector **before the next Wild replay**, so the replay is fully auditable live.
3. Complete the hybrid rejection capability decision using measured corpus evidence.
4. Run the owner-controlled Human Rescue Wild replay.
5. Begin real-client replay.
6. Only then begin Smart Booking implementation.

## 8. Current owner decision

The project will not pursue an exhaustive vocabulary of customer phrases. RideCheck will launch on a bounded hybrid architecture whose evidence, reconciliation and canonical decisions are observable and testable. Schedule pressure does not justify hiding architectural uncertainty; it does justify freezing scope and using the shortest evidence-backed critical path above.
