PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-CANONICAL-ROADMAP

# RideCheck CRM — Canonical Prelaunch Roadmap

**Truth date:** 2026-09-18  
**Last gate sync:** 2026-09-22 — `PRELAUNCH-ROADMAP-GATE1-GATE2-SYNC`. Gates 1 and 2 closed
on owner `INSPECTOR PASS`; Gate 3 reframed and left open as the next architectural
milestone. Documentation only: no code, no deployment, no Wild.  
**Truth correction:** 2026-09-23 — `L4.7W5-GATE3-ROADMAP-TRUTH-CORRECTION`. The
2026-09-22 sync asserted that semantic evidence reaches no production reconciler. The
executable-source audit
`2026-09-23_RIDECHECK_CRM_L4.7W5-GATE3-SEMANTIC-ROUTING-AUDIT_AUDIT_001.md` proved that
false. Gate 3 is redefined from *initial routing* to *completion, governance and
visibility* of a hybrid architecture that partially exists. Documentation only.  
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
| Semantic evidence routing | Semantic evidence ALREADY reaches five production consumers same-turn (scheduling, quote acceptance, human handoff, locality recovery, FAQ topics); it is ungoverned by one authority policy and four materially hybrid sites emit no reconciliation row | **GATE 3 — OPEN, NEXT** |
| Hybrid Inspector coverage | Truthful for the rows it receives; the three traced sites are CE-only, so no materially hybrid decision is currently visible | **INCOMPLETE COVERAGE — GATE 3** |
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

**Bounded completion — what Gate 1 DID prove.** Durable trace infrastructure; burst
identity; evidence capture; the `hybrid-decision-trace/1.2` contract; and the deployment
and persistence foundation beneath them.

**What Gate 1 did NOT prove**, corrected 2026-09-23 against executable source:

- **complete coverage of every production hybrid decision site.** Three reconciliation
  sites are instrumented; four materially hybrid sites are not (Gate 3 findings below);
- **universal semantic-to-reconciler governance.** Semantic evidence already reaches
  several production consumers, under no single authority policy;
- **complete claim-family authority policy.** No family-by-family policy exists yet.

Semantic evidence has **no direct canonical-mutation authority** and must not acquire any:
it may inform a reconciler or an authorizer, and those — not the producer — decide. That
separation is intact today and is Gate 3's to formalise.

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

**Bounded completion — what Gate 2 DID prove.** Truthful rendering of the rows the
Inspector receives; corrected row semantics; the captured-versus-effective distinction; an
owner-approved dashboard and detail page; privacy masking on rendered surfaces.

**What Gate 2 did NOT prove**, corrected 2026-09-23 against executable source:

- **that every production hybrid decision emits a reconciliation row.** Four do not;
- **that the dashboard currently represents all semantic influence.** Semantic evidence
  influences scheduling, acceptance, handoff, locality and FAQ selection, none of which
  appears as a reconciliation row;
- **that existing hybrid decision paths share one authority policy.** They do not.

The Inspector is **truthful for what it receives and incomplete in coverage**. It must not
be described as showing every production hybrid decision. Closing that gap is Gate 3, and
it does not reopen Gate 2: nothing the Inspector displays is wrong.

Exit:

- Lara can answer: “What did the customer say, what did the LLM infer, what did CE infer, what did reconciliation decide, and why?” from one screen.
- A failed turn is diagnosable without engineering access.
- The screen states which kind of turn it is describing, so a Flow transaction can never be
  read as a hybrid decision.

### Gate 3 — Semantic Evidence Routing and Reconciliation Authority — **OPEN / NEXT ARCHITECTURAL MILESTONE**

**Still open — and it is not what this roadmap said it was until 2026-09-23.**

The 2026-09-22 sync asserted that every production reconciliation builds its claims from CE
evidence alone and that no semantic claim had ever reached a reconciler. The
executable-source audit of 2026-09-23 proved that false. The correction is recorded here
because planning Gate 3 against the wrong premise would have produced the wrong milestone.

#### Current production truth

1. **One semantic interpretation is dispatched per burst**, near the beginning of the
   `ConversationEngine` turn (`conversation_engine.py:3417`), before every consumer below.
   At most one model call per burst; that invariant is enforced in code and must survive
   Gate 3.
2. **With the flags production actually runs** — `SHADOW_UNDERSTAND_ENABLED=true`,
   `SHADOW_UNDERSTAND_ASYNC=true`, `SEMANTIC_SAME_TURN_ENABLED=true`,
   `SEMANTIC_SAME_TURN_TIMEOUT_SECONDS=6.0` — semantic evidence **is available to same-turn
   production consumers**. All five flags default to `false`/off in `settings.py`; they are
   `true` by environment.
3. **Execution is concurrent, but consumption is a join.** The interpretation starts
   alongside CE work; a consumer that needs it waits through a bounded 6-second join before
   making the affected decision. A timeout is absent evidence, never a guess.
4. **Semantic evidence already participates in five production paths:**
   - **scheduling requests** — CE claims and semantic claims both enter
     `reconcile_scheduling`;
   - **quote acceptance** — CE and semantic evidence both enter
     `authorize_quote_acceptance`;
   - **human handoff** — semantic evidence can **independently** trigger scheduling
     escalation;
   - **locality recovery** — semantic evidence can produce a locality proposal and a
     customer-facing confirmation question;
   - **FAQ topics** — consumed in a lower-risk response path.
5. **Scheduling reconciliation can already compare deterministic and semantic claims**, and
   does so on resolved dates and times rather than wording. It uses its own current
   vocabulary (`source = semantic | deterministic | deterministic_conflict`) and is not
   represented through the Inspector's complete row semantics.
6. **The three currently traced reconciliation sites are CE-only:** vehicle-identity
   application; inspection-zone application; fuzzy vehicle-identity admissibility.
7. **The four materially hybrid decision sites emit no reconciliation row:** scheduling
   reconciliation; quote-acceptance authorization; semantic human-handoff request; semantic
   locality recovery.
8. **Therefore the Hybrid Decision Inspector is truthful for the rows it receives, and its
   production coverage is incomplete.**
9. **The deployed Inspector must not be described as showing every production hybrid
   decision.** It shows the CE-only decisions correctly and does not yet see the hybrid ones.
10. **Gate 3 is therefore not "route semantic evidence for the first time."**

Carried forward unchanged: there is still no evidence claim whose referent is the offered
appointment set, and `QUOTE_ACCEPTED` / `QUOTE_NEGATED` remain reserved for the quote and
must not be reused for it.

**Objective:** Gate 3 must **complete, govern and expose** the partially existing hybrid
architecture by bringing every production semantic/CE decision under explicit reconciliation
policy, consistent authority rules and complete Hybrid Decision Inspector coverage.

That objective includes, without exception:

- preserve the current **one-interpretation-per-burst** invariant;
- preserve **same-turn burst isolation** — a per-request engine, no cross-turn reuse;
- preserve **deterministic business validation** for availability, price, booking, zone and
  catalogue resolution;
- **trace every materially hybrid decision**;
- distinguish **semantic success, absence, timeout, provider error and validation failure**
  at the decision layer, not only in the log;
- **project semantic ambiguities and conflicts** instead of discarding them;
- establish **explicit claim-family authority policies**;
- prevent semantic interpretation from **directly authorizing protected business actions**;
- place canonical mutations behind **identified authoritative writers**;
- expose **evidence, reconciliation, authority and action** decisions in the Inspector;
- prevent a **late semantic result from changing a finalized turn**;
- keep **phrase cataloguing out of the architecture**.

#### Architectural principles this gate must preserve

1. **The semantic interpreter exists to understand fuzzy, misspelled, colloquial and
   non-textbook customer language.** That is the capability it is there to supply, and it
   is the capability a phrase list can never supply.
2. **The deterministic engine supplies independently derived evidence and enforces
   deterministic business floors.** It is a second producer and a safety floor, not a
   second opinion to be averaged with the first.
3. **`AGREE` requires distinct producers contributing to the same canonical business
   proposition, with compatible canonical values or equivalent polarity.** Agreement never
   depends on identical wording or identical extraction methods: two producers reaching
   `Peugeot 208` by different routes — one from colloquial speech, one from the catalogue —
   agree, because the canonical identity is the same. Compatibility must be established
   from canonical normalization or an existing resolver; where it cannot be established,
   the comparison is `COMPARISON_UNPROVEN`, never agreement.
4. **`CONFLICT` requires at least two distinct evidence producers contributing to the same
   canonical business proposition, with values or polarity that are provably incompatible
   after applicable canonical normalization or resolution.** Nothing weaker is a conflict:

   - missing semantic evidence is **not** conflict;
   - unrouted evidence is **not** conflict;
   - CE silence is **not** conflict;
   - a single producer is **not** cross-producer conflict;
   - claims about different propositions are **not** conflict;
   - an internally contradictory single producer is `AMBIGUOUS_EVIDENCE`, not cross-producer
     `CONFLICT`;
   - uncertainty, or an inability to prove equivalence, is `COMPARISON_UNPROVEN`, not
     `CONFLICT`.

   The first trace this system ever captured was headlined `CONFLICT` because a row with
   nothing to compare was read as contradiction. This principle exists so that cannot
   recur by policy, not only by code.
5. **`PARALLEL_EVIDENCE` means distinct producers contributed evidence about
   non-overlapping canonical propositions or claim families.** They spoke about different
   things, so:

   - parallel evidence is neither `AGREE` nor `CONFLICT`;
   - absence of contradiction does **not** convert parallel evidence into agreement;
   - each contribution remains available for its own reconciliation and authority policy;
   - no producer is considered to have confirmed a proposition it did not address.
6. **Semantic-only evidence is valid evidence and must remain visible as
   `SINGLE_PRODUCER`.** It must not be discarded merely because the CE could not understand
   the same language — that is precisely the language the interpreter exists for.
7. **Semantic evidence does not automatically gain mutation authority.** Routing it to a
   reconciler is not the same as letting it write canonical state.
8. **CE silence is not disagreement.**
9. **Absence of conflict is not agreement.**
10. **The reconciler — not either producer — decides** how evidence affects canonical state
    and which actions are permitted.
11. **No phrase catalogue or accumulating list of sentence variants is an acceptable
    architectural solution.** Real expressions belong in a versioned evaluation corpus.
12. **Every `HOLD`, acceptance, conflict, ambiguity, fallback and human escalation must be
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

- **Every** materially hybrid production decision emits a reconciliation row, and the
  Inspector shows a real two-producer comparison on a live turn.
- Live `AGREE`, `CONFLICT` and `PARALLEL_EVIDENCE` between the two producers are
  **observed in the Inspector**, not merely reachable in code and not merely proven by
  fixture.
- Every routed site shares one explicit authority policy, per claim family.
- New wording variants are handled by semantic equivalence plus bounded deterministic safeguards—not by adding each sentence to production logic.
- Corpus evaluation meets agreed precision/recall thresholds, especially false-handoff rate.
- Reconciler decisions — every `HOLD`, acceptance, conflict, ambiguity, fallback and human
  escalation — are visible in the Hybrid Decision Inspector.
- Model-disabled and model-failure paths remain safe, and semantic-only evidence is still
  visible rather than discarded.

#### Honest limitation until Gate 3 is implemented

What the deployed Inspector will and will not show today, so that a reader does not mistake
a truthful trace for a complete architecture:

- **the three rows it receives are CE-only**, so they correctly read `SINGLE_PRODUCER`,
  `NOT_ROUTED` or `NO_EVIDENCE`;
- **the four hybrid decisions produce no row at all**, so live `AGREE`, `CONFLICT` and
  `PARALLEL_EVIDENCE` are not observable — not because semantic evidence is unrouted, but
  because the routed sites are uninstrumented;
- **fixture proof is not production-coverage proof.** The `1.2` matrix demonstrates the
  classifier is correct; it does not demonstrate that every hybrid decision is captured.

#### Open findings — executable-source audit, 2026-09-23

**BLOCKER**

- The four materially hybrid production decision paths are not traced in the Hybrid
  Decision Inspector.

**HIGH**

- Thread-level locality fields have multiple direct writers and are not governed by one
  reconciliation authority.
- Semantic-only evidence can currently trigger a human handoff or a locality-confirmation
  response without an Inspector reconciliation row or an owner-ratified family policy.
- Semantic ambiguities and conflicts are produced by the interpreter but discarded before
  claim projection.

**MEDIUM**

- Scheduling producer identity is inferred from evidence class instead of producer
  namespace.
- Scheduling reconciliation hard-codes `TRUE_ONLY` and loses real polarity.
- Timeout, provider error, validation error and genuine semantic silence all collapse to
  `None` at the decision layer.
- Five post-2026-09-01 Wild sessions are absent from the durable semantic corpus.

**LOW**

- `ConversationEngine` contains many transaction commits, complicating a single
  authoritative turn-finalization boundary.

#### Owner-ratified Gate 3 authority policies — adopted 2026-09-23

The five questions raised by the 2026-09-23 audit were put to the owner and **ratified**.
They are binding constraints on G3-1 through G3-7.

**None of them is implemented.** Nothing in this subsection describes current behaviour;
each is a requirement the corresponding slice must satisfy and prove.

**The six layers these policies keep apart.** Every policy below is written against this
separation, and no policy may be read as collapsing any two of them:

- **semantic interpretation** — what the customer meant;
- **structured evidence** — that meaning expressed as typed claims;
- **reconciliation** — what two or more producers, taken together, prove;
- **canonical-state authority** — what may be written, and by which writer;
- **business validation** — availability, price, zone and catalogue, from the sources of
  truth;
- **permitted action** — what the system is then allowed to do.

**Two statements that govern every policy below.**

- **Semantic evidence does not require matching CE evidence to be valid.** Evidence the
  deterministic engine could not produce is still evidence. Discarding it because CE stayed
  silent would delete exactly the capability the interpreter exists to supply.
- **CE silence is not disagreement, and it is not a veto.** An absent deterministic reading
  neither contradicts a semantic one nor blocks it.

**None of these policies establishes phrase matching as the architecture.** They are
policies about meaning, evidence and authority. No policy below authorises a phrase list, a
keyword set, a regex or a sentence-specific branch, and P5 forbids turning corpus evidence
into one.

**P1 — Semantic-only human-handoff requests: ALLOWED.**

The semantic engine must be allowed to recognise an affirmative request for a person **even
when CE cannot understand the customer's wording**.

**"Explicit" means explicit in semantic meaning — not exact spelling, keywords or
phrasing.**

Requirements:

- the proposition must be **affirmative**;
- **negation must be respected**;
- "no me llame", "no quiero hablar con alguien" and equivalent negative meanings **must not
  trigger a handoff**;
- **ambiguity must not be converted into a positive request**;
- **CE silence is not a veto**;
- the decision requires an **explicit reconciliation policy**;
- the decision and the resulting state and action must be **fully traced**.

**P2 — Semantic-only locality evidence: MAY ASK A CONFIRMATION QUESTION.**

It may propose a candidate interpretation, of the form "¿Te referís a Palermo?".

Semantic-only locality evidence **must not**, by itself:

- write canonical locality or zone;
- determine **viáticos**;
- calculate or authorize a price;
- authorize a quote;
- promise availability;
- select a scheduling slot;
- dispatch a booking Flow;
- create a revision or a booking.

The **proposal**, the **confirmation question** and the **eventual confirmed resolution**
must all be traced.

**P3 — Thread-level locality: ONE RECONCILED AUTHORITATIVE WRITER.**

The implementation must place `state.home_zone_group`, `state.home_zone_detail` and
equivalent thread-level locality mutations behind **one identified reconciliation and
mutation path**.

**Direct competing writers must be removed or routed through that authority.**

Implementation constraint carried from the owner's instruction: existing behaviour is
preserved until separately reviewed, with regression protection for location retention,
pricing and scheduling.

**P4 — Quote-acceptance disagreement: HOLD AND CLARIFY.**

When semantic and CE evidence address the **same** quote-acceptance proposition and are
**provably incompatible**:

- do **not** treat the quote as accepted;
- do **not** silently prefer CE;
- do **not** silently prefer semantic evidence;
- do **not** advance to scheduling;
- do **not** send the booking Flow;
- **HOLD and ask a concise clarification question**.

Neither producer wins by default. Silent victory for either side is what hides a real
disagreement from the operator, and it is precisely what this policy forbids.

**Scope limit, stated deliberately.** P4 applies to **genuine cross-producer conflict**. It
does **not** decide the separate policy for **semantic-only** quote acceptance; that family
case remains to be evaluated during Gate 3 authority-policy design (G3-6).

**P5 — Durable Wild corpus: ALL FIVE POST-2026-09-01 SESSIONS.**

All five preserved Wild sessions after 2026-09-01 must be added to the durable semantic
evaluation corpus **with provenance**. They are evaluation and certification evidence.

They **must not** become:

- phrase rules;
- regexes;
- keywords;
- deterministic exception lists;
- production matching shortcuts.

A corpus case proves whether the architecture understands. It is never a branch inside the
architecture.

#### Still unresolved after the 2026-09-23 ratification

Recorded so nothing here is mistaken for already answered:

- the **semantic-only quote-acceptance** family policy, outside a true semantic-versus-CE
  conflict — to be evaluated during Gate 3 authority-policy design (G3-6);
- **confidence thresholds** of any kind;
- the **exact customer-facing wording** of any clarification or confirmation question;
- **implementation details** of every slice;
- any change to the semantic **timeout**;
- **deployment timing** for any Gate 3 slice.

#### Bounded implementation sequence

- **G3-0** — roadmap truth correction (this entry).
- **G3-1** — instrument the four existing hybrid decision sites **without changing their
  decisions**.
- **G3-2** — preserve the semantic outcome reason: `OK`, `ABSENT`, `TIMEOUT`, `ERROR`,
  `VALIDATION_ERROR`.
- **G3-3** — project semantic ambiguities and conflicts, without granting them mutation
  authority.
- **G3-4** — establish reconciled single-writer authority for thread-level locality.
- **G3-5** — derive producer identity from the producer namespace and preserve real
  polarity.
- **G3-6** — implement owner-ratified family authority policies.
- **G3-7** — complete the durable Wild corpus, replay, certify, deploy and conduct an
  owner-present Wild, through separate controlled milestones.

**G3-1 is the next proposed implementation slice**, after this roadmap correction is
reviewed and pushed.

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
- **Complete, govern and expose the partially existing hybrid architecture (Gate 3).**
  ← next architectural milestone. Corrected 2026-09-23: semantic evidence already reaches
  five production consumers; what is missing is one authority policy and complete Inspector
  coverage, not initial routing.
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
4. **NEXT — Gate 3: Semantic Evidence Routing and Reconciliation Authority.** Complete,
   govern and expose the hybrid architecture that partially exists: bring every production
   semantic/CE decision under explicit reconciliation policy, consistent authority rules and
   complete Inspector coverage. The next implementation slice is **G3-1**, instrumenting the
   four existing hybrid decision sites without changing their decisions. Not started.
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
