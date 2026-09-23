PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-CANONICAL-ROADMAP

# RideCheck CRM — Canonical Prelaunch Roadmap

**Truth date:** 2026-09-18  
**Last gate sync:** 2026-09-22 — `PRELAUNCH-ROADMAP-GATE1-GATE2-SYNC`. Gates 1 and 2 closed
on owner `INSPECTOR PASS`; Gate 3 reframed and left open as the next architectural
milestone. Documentation only: no code, no deployment, no Wild.  
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
| F7G-R2 safety floor | Source `ce978a2` pushed; image `ridecheck-crm-backend:w5f7g-ce978a2` built, pinned (`5177667`) and deployed; certified by `2026-09-18_RIDECHECK_CRM_L4.7W5-F7G-R2-CONTROLLED-DEPLOYMENT_CLOSEOUT_002.md` | **DEPLOYED / GATE 0 COMPLETE** |
| Hybrid interpretation for offered-slot rejection | Semantic result had no rejection; forensic record cannot prove which complete burst the model saw; no dedicated claim/reconciliation policy exists | **NOT PROVEN** |
| Alternative-time request | “¿No tenés algo más temprano?” correctly must not be treated as a rejection, but useful alternative response remains open F-03 | **OPEN MEDIUM** |
| Live hybrid observability | Hybrid Decision Inspector deployed as `ridecheck-crm-backend:w5tracerows-355c0ae` (`GIT_SHA 355c0ae`, config `b2e6d43`); owner returned `INSPECTOR PASS` 2026-09-22 | **DEPLOYED / GATES 1+2 COMPLETE** |
| Semantic evidence routing | Every production reconciliation still builds its claims from CE evidence alone; no semantic claim reaches a reconciler | **GATE 3 — OPEN, NEXT** |
| Raw WAMID on the forensic API | Rendered CRM surfaces mask WAMIDs; anonymous API access denied; the authenticated JSON API still returns the raw WAMID as a join key | **PRELAUNCH PRIVACY DECISION — NON-BLOCKING** |
| Real-client replay | Not yet executed against the completed hybrid architecture | **PENDING** |
| Smart Booking | Premises supplied; audit/design, implementation and certification remain | **PENDING / SCOPE CONTAINED** |
| Public launch | Not authorized | **NO-GO** |

## 4. Finite critical path

### Gate 0 — Deploy the proven safety floor — **COMPLETE (2026-09-18)**

**Goal:** Remove the immediate customer loop without pretending the hybrid gap is solved.

**Completed.** Certified by `2026-09-18_RIDECHECK_CRM_L4.7W5-F7G-R2-CONTROLLED-DEPLOYMENT_CLOSEOUT_002.md`.

| item | value |
|---|---|
| source commit | `ce978a2` |
| pin commit | `5177667` |
| image | `ridecheck-crm-backend:w5f7g-ce978a2` (digest `sha256:67a9054e…81d2`) |
| backend container | `6c9db45bf53d`, restarts 0, `GIT_SHA=ce978a2` |
| outbound | **OFF** throughout; 0 attempts, ledger delta 0 |
| Wild | **none performed** |
| hybrid semantic/reconciliation gap | **REMAINS OPEN** — this is a deterministic floor, not hybrid capability |

Only the backend was recreated; n8n, Postgres and nginx were untouched. Meta remained PUBLISHED and
valid, read-only. The seeded Agenda (ids 62–73) and thread 2053's 12 evidence rows are preserved.

Actions:

- Push/build/deploy F7G-R2 commit `ce978a2` through the controlled procedure.
- Recreate only the backend.
- Keep outbound OFF.
- Certify runtime attribution, webhook signatures, Meta status, Agenda integrity and unchanged business data.

Exit:

- Deployed runtime executes the deterministic rescue floor.
- No new regression or operational mutation.
- Closeout explicitly labels it a deterministic floor.

### Gate 1 — Hybrid Evidence Contract and live trace capture — **CLOSED / COMPLETE (2026-09-22)**

**Closed on owner `INSPECTOR PASS`, 2026-09-22.** Certified by
`2026-09-22_RIDECHECK_CRM_L4.7W5-TRACE-ROW-SEMANTICS-CONTROLLED-DEPLOYMENT_CLOSEOUT_001.md`.

The production system captures a durable, auditable hybrid decision trace per customer
turn, contract `hybrid-decision-trace/1.2`, carrying:

- the exact burst boundary and input identity — ordered message ids, timestamps and the
  hash of the normalized burst, so what each engine received is provable rather than
  asserted;
- semantic-engine execution evidence — status (including `PENDING`, `ABSENT`, `ERROR`),
  model, prompt and schema versions, latency, token count, error category and the
  validated structured evidence;
- deterministic-engine evidence — named business rules with versions, values and input
  digests;
- reconciliation rows — one per reconciliation call, each naming its decision site, the
  sources that participated, what each supplied, what was compared and on what basis, the
  reconciler's outcome, the rule and the reason;
- canonical state before and after, from a fixed allowlist;
- the final action and outcome, including the outbound path, status and WAMID tail;
- stable comparison identifiers — `logical_comparison_id` derived from decision site,
  claim family, rule and sorted claim content hashes, with repeats separated by a
  positional `occurrence_index` that is deliberately excluded from the identity;
- producer provenance — the source of every claim, taken from the producer's own
  namespaced literal (`semantic:*`, `ce:*`, `canonical:*`), never inferred from evidence
  strength;
- deployment and contract versions — the deployment SHA and the trace contract version on
  every record, with older contracts still readable and explicitly marked as legacy.

| item | value |
|---|---|
| application SHA | `355c0ae` |
| deployment-config commit | `b2e6d43` |
| deployed image | `ridecheck-crm-backend:w5tracerows-355c0ae` |
| digest | `sha256:5700073b238d08ff7b2c97dec538db4b6c7ce6bdf21e80276bfff6a2466c9fdc` |
| `HYBRID_TRACE_ENABLED` | `true` |
| `OUTBOUND_ENABLED` | `false` |

**What this gate does NOT claim.** Semantic evidence has **no production reconciliation
authority**. Capture proves what each producer did; it does not route semantic claims to a
reconciler and does not grant them the power to change canonical state. That is Gate 3.

**Scope — conversational hybrid decisions only.** A hybrid decision is a customer turn in
which the semantic engine and the deterministic CE both interpret the same evidence and a
reconciler may own the outcome. That happens in `ConversationEngine.handle()` and nowhere
else, so that is the only path this gate instruments.

**Meta Flow Data Exchange is explicitly out of scope** (owner decision, 2026-09-18).
`POST /integrations/whatsapp/flows/booking/data-exchange` is an operational transaction
path: it does not run the interpreter, produces no claim, and reconciles nothing. Forcing a
Flow callback into `hybrid-decision-trace/1.0` would fabricate semantic evidence, CE
evidence and agreement that never existed — the precise failure mode this whole programme
exists to prevent. A Flow-confirmed booking may be reached through existing booking and
message evidence and is labelled `FLOW TRANSACTION — NOT A HYBRID DECISION` wherever it is
shown, but it is never represented as a hybrid decision. **Operational observability for the
Flow transaction path is a separate, later concern** and is not a prerequisite for proving
the semantic / CE / reconciler architecture.

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
- **Certified on a live deployment with at least one owner-observed real trace.** Local test
  evidence, however complete, does not satisfy this line.

**Known scope limit carried forward.** Two pre-gates (motorcycle, phone-call) read
`_current_evidence`, which after WILD-04R burst completion can be wider than the burst the
two engines share. The trace hashes the shared burst and records the database burst ids;
the asymmetry is pinned by test rather than papered over, and is not claimed as agreement.

### Gate 2 — Control dashboard: Hybrid Decision Inspector — **CLOSED / COMPLETE (2026-09-22)**

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

- obvious badges: `HYBRID CONVERSATION TRACE`, `AGREE`, `CONFLICT`, `NO RULE`,
  `SEMANTIC PENDING`, `SEMANTIC MISSING`, `SEMANTIC ERROR`, `CE MISSING`, `BLOCKED`,
  `RECONCILED`, `DETERMINISTIC FLOOR`, `HANDOFF`, `FALLBACK`, `TRACE NOT CAPTURED`, and
  `FLOW TRANSACTION — NOT A HYBRID DECISION` wherever a Flow-originated action is shown;
  missing evidence is never labelled agreement and a deterministic floor is never labelled
  reconciled;
- filter by deployment, claim, conflict type, model error, human handoff and false progression;
- expandable raw JSON for expert audit, with a readable summary by default;
- links from the existing message trace to the exact decision inspector;
- no secret values and no unmasked phone exposure beyond existing authorized CRM policy.

**Status 2026-09-22 — CLOSED on owner `INSPECTOR PASS`.** Certified by
`2026-09-22_RIDECHECK_CRM_L4.7W5-TRACE-ROW-SEMANTICS-CONTROLLED-DEPLOYMENT_CLOSEOUT_001.md`.

- The `Decisiones híbridas` dashboard panel and the `/control/turn/{turn_id}` detail page
  are **deployed** and behind the CRM session; anonymous access is denied (`303` on the
  pages, `401` on the trace APIs, with a single refusal body that discloses nothing).
- **The owner returned `INSPECTOR PASS`** after inspecting both surfaces.
- The Inspector **separates the captured historical label from the effective
  interpretation**. A stored label is never rewritten: the preserved trace still holds
  `CONFLICT` and its rows still hold `SEMANTIC_MISSING` and `AGREE`, while the page reads
  `TRACE INCOMPLETE` with each row's withdrawn label shown beside it as `registrado:`.
- It **does not infer agreement from the absence of conflict.** `AGREE` requires two or
  more attributed producers, a shared canonical proposition, and canonical values proven
  compatible by a resolver the system already owns.
- It distinguishes, per row: `AGREE`, `CONFLICT`, `PARALLEL_EVIDENCE`,
  `COMPARISON_UNPROVEN`, `SINGLE_PRODUCER`, `AMBIGUOUS_EVIDENCE`, `NO_EVIDENCE`,
  `NOT_ROUTED`, `ERROR` and `LEGACY_PROVENANCE_UNAVAILABLE`; and at turn level
  `PARTIAL_RECONCILIATION`, `SINGLE_SOURCE_DECISION`, `NO_COMPARISON` and
  `TRACE_INCOMPLETE`.
- **Rendered WAMIDs are masked** to a stable, irreversible fingerprint across every panel,
  including the raw-JSON and semantic-evidence dumps. A WAMID base64-encodes the sender's
  phone number; it was printed in full before this deployment.
- **The six findings from the 2026-09-21 `INSPECTOR FAIL` are closed**: the false
  `CONFLICT` headline; row labels that implied agreement where a producer had not
  participated; the untruthful "families affected: 2 of 2" count; a Result panel reporting
  `action=replied` for a turn that produced no reply; the scope sentence claiming both
  engines interpreted the same evidence; and the unmasked inbound WAMID.

Exit:

- Lara can answer: “What did the customer say, what did the LLM infer, what did CE infer, what did reconciliation decide, and why?” from one screen.
- A failed turn is diagnosable without engineering access.
- The screen states which kind of turn it is describing, so a Flow transaction can never be
  read as a hybrid decision.

### Gate 3 — Semantic Evidence Routing and Reconciliation Authority — **OPEN / NEXT ARCHITECTURAL MILESTONE**

**Still open, and now the only thing between the architecture and its own premise.** Gates
1 and 2 made the gap visible and inspectable; visibility is not capability. Every
production reconciliation still builds its claims from CE evidence alone, so no semantic
claim has ever reached a reconciler. There is still no evidence claim whose referent is the
offered appointment set, and `QUOTE_ACCEPTED` / `QUOTE_NEGATED` remain reserved for the
quote and must not be reused for it.

**Objective:** Route real semantic `TurnEvidence` into the production reconciler alongside
deterministic CE evidence, then let explicit reconciliation policy determine canonical
state and permitted actions.

#### Architectural principles this gate must preserve

1. **The semantic interpreter exists to understand fuzzy, misspelled, colloquial and
   non-textbook customer language.** That is the capability it is there to supply, and it
   is the capability a phrase list can never supply.
2. **The deterministic engine supplies independently derived evidence and enforces
   deterministic business floors.** It is a second producer and a safety floor, not a
   second opinion to be averaged with the first.
3. **Agreement is based on shared canonical business propositions and compatible canonical
   values** — not identical wording, and not identical extraction methods. Two producers
   reaching `Peugeot 208` by different routes agree; two producers speaking about different
   fields have not agreed about anything.
4. **Semantic-only evidence is valid evidence and must remain visible as
   `SINGLE_PRODUCER`.** It must not be discarded merely because the CE could not understand
   the same language — that is precisely the language the interpreter exists for.
5. **Semantic evidence does not automatically gain mutation authority.** Routing it to a
   reconciler is not the same as letting it write canonical state.
6. **CE silence is not disagreement.**
7. **Absence of conflict is not agreement.**
8. **The reconciler — not either producer — decides** how evidence affects canonical state
   and which actions are permitted.
9. **No phrase catalogue or accumulating list of sentence variants is an acceptable
   architectural solution.** Real expressions belong in a versioned evaluation corpus.
10. **Every `HOLD`, acceptance, conflict, ambiguity, fallback and human escalation must be
    inspectable in the Hybrid Decision Inspector.** A decision that cannot be read is a
    decision that cannot be certified.

**Sub-goal carried forward:** make offered-appointment rejection a supported semantic
concept without overloading quote acceptance.

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

- Real semantic `TurnEvidence` reaches the production reconciler alongside CE evidence, and
  the Inspector shows it doing so on a live turn.
- Live `AGREE`, `CONFLICT` and `PARALLEL_EVIDENCE` between the two producers become
  reachable in production and are observed, not merely proven by fixture.
- New wording variants are handled by semantic equivalence plus bounded deterministic safeguards—not by adding each sentence to production logic.
- Corpus evaluation meets agreed precision/recall thresholds, especially false-handoff rate.
- Reconciler decisions — every `HOLD`, acceptance, conflict, ambiguity, fallback and human
  escalation — are visible in the Hybrid Decision Inspector.
- Model-disabled and model-failure paths remain safe, and semantic-only evidence is still
  visible rather than discarded.

#### Honest limitation until Gate 3 is implemented

This is what the deployed Inspector will and will not show today, stated so that a reader
does not mistake a truthful trace for a completed architecture:

- **live production reconciliation claims are still built from CE evidence**;
- **live rows may show `SINGLE_PRODUCER`, `NOT_ROUTED` or `NO_EVIDENCE`** — and will, by
  design, because only one producer ever participates;
- **genuine live `AGREE`, `CONFLICT` and `PARALLEL_EVIDENCE` between the semantic and
  deterministic producers are not yet reachable**;
- **fixture proof of those classifications is not equivalent to production routing proof.**
  The `1.2` matrix demonstrates the classifier is correct; it does not demonstrate that the
  architecture is wired.

### Gate 4 — Owner-controlled Human Rescue Wild completion

**Blocked on Gate 3.** A controlled replay or Wild runs only after Gate 3 is implemented,
reviewed, deployed and visible in the Inspector. Replaying before then would exercise a
single-producer path and certify it as hybrid.

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

- ~~Deploy and replay F7G-R2.~~ **DONE — Gate 0 complete 2026-09-18.**
- ~~Complete hybrid evidence capture and dashboard inspector.~~ **DONE — Gates 1 and 2
  complete 2026-09-22 on owner `INSPECTOR PASS`.**
- **Route semantic evidence into the reconciler and define reconciliation authority
  (Gate 3).** ← next architectural milestone.
- Complete Human Rescue Wild — only after Gate 3.
- Run real-client replay before Smart Booking.
- Implement and certify the agreed Smart Booking MVP.
- Complete production dataset/environment gate.
- Final combined certification.

### Prelaunch privacy-hardening decisions — non-blocking

**Raw WAMID on the authenticated forensic API.** Recorded 2026-09-22, deliberately left
open for the owner:

- rendered CRM surfaces **mask** WAMIDs to a stable, irreversible fingerprint;
- **anonymous API access is denied** (`401` on `/api/ops/turns` and `/api/ops/turn/{id}`);
- the **authenticated** forensic JSON API still returns the **raw WAMID**, because it is
  the join key into `whatsapp_messages` and `GET /security/outbound-ledger`;
- whether to mask or further privilege that API **must be decided before public launch** —
  an operator who can read the page can also call the API;
- **this does not block Gate 3** and is not an architectural dependency.

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

1. ~~Complete `L4.7W5-F7G-R2-CONTROLLED-DEPLOYMENT`.~~ **DONE — Gate 0 complete 2026-09-18.**
2. ~~Gate 1 hybrid trace capture: deploy and certify one owner-observed real trace.~~
   **DONE — Gate 1 complete 2026-09-22.** Its first identified defect is closed along the
   way: the shadow record stored only the burst's triggering WAMID and could not show which
   messages the model received; the trace now records the ordered ids and the hash of the
   normalized burst, satisfying *"the exact model input boundary is provable."*
3. ~~Gate 2 Hybrid Decision Inspector.~~ **DONE — Gate 2 complete 2026-09-22 on owner
   `INSPECTOR PASS`.**
4. **NEXT — Gate 3: Semantic Evidence Routing and Reconciliation Authority.** Route real
   semantic `TurnEvidence` into the production reconciler alongside deterministic CE
   evidence, then let explicit reconciliation policy determine canonical state and
   permitted actions. Not started.
5. Run the owner-controlled Human Rescue Wild replay (Gate 4) — **only after Gate 3 is
   implemented, reviewed, deployed and visible in the Inspector.**
6. Begin real-client replay (Gate 5) — **before any Smart Booking expansion.**
7. Only then begin Smart Booking implementation (Gate 7); its scope remains **frozen** to
   the signed premises.
8. Decide the raw-WAMID forensic-API privacy item before public launch — non-blocking for
   Gate 3.

**Unchanged by this sync:** public launch remains **NO-GO**, outbound remains **OFF**, and
Smart Booking scope remains **frozen**.

## 8. Current owner decision

The project will not pursue an exhaustive vocabulary of customer phrases. RideCheck will launch on a bounded hybrid architecture whose evidence, reconciliation and canonical decisions are observable and testable. Schedule pressure does not justify hiding architectural uncertainty; it does justify freezing scope and using the shortest evidence-backed critical path above.
